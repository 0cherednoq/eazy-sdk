"""Роутер и клиент: `portal.open_create_company()` в форме `AsyncApi` ядра.

Операция публикуется тем же `op()`, что и HTTP-операция; сервисные атрибуты
роутера достаются каждой его операции; состояния-исходы строятся из роутера; в
`AsyncRoot` браузерный роутер стоит рядом с HTTP-роутером.
"""

from dataclasses import dataclass
from typing import Annotated

import pytest
from eazy_sdk_browser import (
    AsyncBrowserApi,
    AsyncBrowserClient,
    Browser,
    BrowserCallOptions,
    BrowserClientConfig,
    BrowserDeclarationError,
    BrowserError,
    BrowserOperation,
    Element,
    Failure,
    Handle,
    PageError,
    click,
    css,
    execute,
    outcomes,
    text,
    visible,
    when,
)
from eazy_sdk_browser.api import _BoundBrowserOperation, _BrowserOperationDescriptor
from eazy_sdk_browser.integrations.handler import BrowserHandler
from eazy_sdk_browser.testing import FakeDriver, FetchFakeDriver, framed

from eazy_sdk import AsyncClient, api_group, bind, op
from eazy_sdk.root import AsyncRoot
from examples.browser_portal import (
    CREATE_BUTTON,
    DIALOG_ROOT,
    FILTERS_ROOT,
    CompaniesPortal,
    CreateCompanyDialog,
    CreateCompanyRequest,
    OpenCreateCompany,
    PortalSdk,
)

pytestmark = pytest.mark.unit

FAST = BrowserCallOptions(timeout=0.01)


class Page:
    button: Annotated[Element, css("button.go")]


class BrokenError(PageError):
    """Страница сообщила об ошибке."""


class DeniedError(PageError):
    """Нет прав."""


@dataclass(frozen=True, slots=True)
class Done:
    pass


async def _done(content: Page) -> Done:
    _ = content
    return Done()


async def _nothing(content: Page) -> Done:
    _ = content
    return Done()


@dataclass(frozen=True, slots=True, kw_only=True)
class Go(BrowserOperation[Page, Done]):
    __browser__ = Browser.act(
        Page,
        outcomes=outcomes(when(visible(css("div.ok")), then=_done), otherwise=_nothing),
        errors=(Failure(when=text.contains("сломалось"), exception=BrokenError),),
    )

    times: int = 1

    async def act(self, content: Page) -> None:
        for _ in range(self.times):
            await content.button.click()


@dataclass(frozen=True, slots=True, kw_only=True)
class GoAlone(BrowserOperation[Page, Done]):
    __browser__ = Browser.act(
        Page,
        outcomes=outcomes(when(visible(css("div.ok")), then=_done), otherwise=_nothing),
        inherit_errors=False,
    )

    async def act(self, content: Page) -> None:
        await content.button.click()


class Site(AsyncBrowserApi):
    errors = (Failure(when=text.contains("нет прав"), exception=DeniedError),)
    handlers = (Handle(when=visible(css("div.banner")), do=click(css("button.accept"))),)

    go = op(Go)
    go_alone = op(GoAlone)


class FramedSite(Site):
    frames = ('iframe[name="app"]',)


def site(driver: FakeDriver) -> Site:
    return Site(AsyncBrowserClient(driver))


# --- B2.2: дескриптор и привязанная операция ----------------------------------------------------


def test_op_publishes_a_browser_operation_through_the_core_op() -> None:
    descriptor = Site.__dict__["go"]

    assert isinstance(descriptor, _BrowserOperationDescriptor)
    assert descriptor.operation is Go
    assert isinstance(site(FakeDriver()).go, _BoundBrowserOperation)
    assert site(FakeDriver()).go.Operation is Go


async def test_bound_operation_calls_requests_and_sends() -> None:
    driver = FakeDriver(present={"button.go", "div.ok"})
    portal = site(driver)

    called = await portal.go(times=2)
    request = portal.go.request(times=3)
    sent = await portal.go.send(request, options=FAST)

    assert isinstance(called, Done)
    assert request == Go(times=3)
    assert isinstance(sent, Done)
    assert driver.log == ["click button.go"] * 5


async def test_send_refuses_a_foreign_request() -> None:
    portal = site(FakeDriver(present={"button.go"}))

    with pytest.raises(TypeError, match="GoAlone"):
        await portal.go.send(GoAlone())


def test_d_b_06_browser_operation_belongs_to_a_browser_router() -> None:
    with pytest.raises(BrowserDeclarationError, match="D-B-06"):

        class Elsewhere:
            go = op(Go)


def test_d_b_07_http_operation_is_refused_on_a_browser_router() -> None:
    with pytest.raises(BrowserDeclarationError, match="D-B-07"):

        class Mixed(AsyncBrowserApi):
            create = op(CreateCompanyRequest)


def test_d_b_01_and_05_are_checked_at_publication() -> None:
    @dataclass
    class Mutable(BrowserOperation[Page, None]):
        __browser__ = Browser.act(Page)

    with pytest.raises(BrowserDeclarationError, match="D-B-05"):
        op(Mutable)

    class Bare(BrowserOperation[Page, None]):
        pass

    with pytest.raises(BrowserDeclarationError, match="D-B-01"):
        op(Bare)


# --- сервисные атрибуты роутера ------------------------------------------------------------------


async def test_router_errors_come_after_the_operation_errors() -> None:
    """Частное правило операции стоит выше общего правила роутера."""
    driver = FakeDriver(present={"button.go"}, text="сломалось, и нет прав")

    with pytest.raises(BrokenError):
        await site(driver).go(options=FAST)


async def test_router_errors_apply_to_every_operation() -> None:
    driver = FakeDriver(present={"button.go"}, text="нет прав")

    with pytest.raises(DeniedError):
        await site(driver).go(options=FAST)


async def test_inherit_errors_false_keeps_the_router_rules_out() -> None:
    driver = FakeDriver(present={"button.go"}, text="нет прав")

    assert isinstance(await site(driver).go_alone(options=FAST), Done)


async def test_router_handlers_are_added_after_the_operation_handlers() -> None:
    driver = FakeDriver(present={"button.go", "div.banner", "button.accept", "div.ok"})

    await site(driver).go()

    assert driver.log == ["click button.accept", "click button.go"]


async def test_router_frames_wrap_the_operation_map() -> None:
    frame = 'iframe[name="app"]'
    driver = FakeDriver(present={framed("button.go", (frame,)), framed("div.ok", (frame,))})

    await FramedSite(AsyncBrowserClient(driver)).go()

    assert driver.log == [f"click {frame} >>> button.go"]


# --- B2.1: клиент и опции ------------------------------------------------------------------------


async def test_client_config_sets_the_default_timeouts_and_options_override() -> None:
    driver = FakeDriver(present={"button.go"}, latency=0.01)
    client = AsyncBrowserClient(driver, config=BrowserClientConfig(timeout=0.02))

    assert isinstance(await Site(client).go(), Done)
    assert isinstance(await Site(client).go(options=BrowserCallOptions(timeout=0.01)), Done)


async def test_client_closes_only_the_driver_it_owns() -> None:
    closed: list[str] = []

    class Closable(FakeDriver):
        async def aclose(self) -> None:
            closed.append("closed")

    async with AsyncBrowserClient(Closable(), owns_driver=False):
        pass
    async with AsyncBrowserClient(Closable(), owns_driver=True) as owner:
        pass
    await owner.aclose()

    assert closed == ["closed"]


# --- B2.4: состояния строятся из роутера ------------------------------------------------------


async def test_state_outcome_holds_the_router_that_opened_it() -> None:
    driver = FakeDriver(present={CREATE_BUTTON, DIALOG_ROOT, FILTERS_ROOT})
    portal = CompaniesPortal(AsyncBrowserClient(driver))

    dialog = await portal.open_create_company(options=FAST)

    assert isinstance(dialog, CreateCompanyDialog)
    assert dialog.api is portal


async def test_state_outcome_needs_a_router_not_a_bare_driver() -> None:
    driver = FakeDriver(present={CREATE_BUTTON, DIALOG_ROOT, FILTERS_ROOT})

    with pytest.raises(BrowserError, match="AsyncBrowserApi"):
        await execute(OpenCreateCompany(), driver, options=FAST)


# --- B2.3: браузерный роутер в AsyncRoot ------------------------------------------------------


async def test_root_composes_http_and_browser_routers_over_one_driver() -> None:
    driver = FetchFakeDriver(present={CREATE_BUTTON, DIALOG_ROOT, FILTERS_ROOT})
    browser = AsyncBrowserClient(driver)

    async with AsyncClient(
        base_url="https://portal.example.com", handler=BrowserHandler(driver)
    ) as http:
        sdk = PortalSdk(http, bindings=(bind(CompaniesPortal, client=browser),))
        dialog = await sdk.portal.open_create_company(options=FAST)

    assert isinstance(dialog, CreateCompanyDialog)
    assert sdk.portal.client is browser
    assert sdk.api.create.Operation is CreateCompanyRequest


async def test_root_refuses_a_browser_router_without_its_client() -> None:
    driver = FetchFakeDriver()

    async with AsyncClient(
        base_url="https://portal.example.com", handler=BrowserHandler(driver)
    ) as http:
        sdk = PortalSdk(http)
        with pytest.raises(TypeError, match="bind\\(CompaniesPortal, client="):
            _ = sdk.portal


def test_api_group_accepts_a_router_that_composes_itself() -> None:
    class Sdk(AsyncRoot):
        portal = api_group(CompaniesPortal)

    assert Sdk.portal.api_type is CompaniesPortal
