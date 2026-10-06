"""Навигация: `Browser.goto` — операция роутера, переход после действия — водораздел.

У перехода нет тела: адрес собирается из полей операции, готовность проверяется на
открытой странице, а увод на вход распознают правила роутера — раньше, чем истечёт
условие готовности. Переход после действия признак не ждёт, а сравнивает со счётом до
действия (`navigated()`), как ответы API сравниваются с водоразделом буфера.
"""

from dataclasses import dataclass, field
from typing import Annotated, NoReturn

import pytest
from eazy_sdk_browser import (
    AsyncBrowserApi,
    AsyncBrowserClient,
    Browser,
    BrowserCallOptions,
    BrowserClientConfig,
    BrowserDeclarationError,
    BrowserOperation,
    Capability,
    Element,
    Failure,
    LoadState,
    NotReadyError,
    PageError,
    css,
    execute,
    navigated,
    outcomes,
    response,
    url,
    visible,
    when,
)
from eazy_sdk_browser.testing import FakeDriver, NavigatingFakeDriver

from eazy_sdk import op
from eazy_sdk.handlers import CapabilityMismatchError

pytestmark = pytest.mark.unit

BASE = "https://portal.example.com"
LOGIN = f"{BASE}/login"
INBOX = f"{BASE}/inbox"
FAST = BrowserCallOptions(timeout=0.05)


class SessionExpiredError(PageError):
    """Портал увёл на вход."""


class LoginHangsError(PageError):
    """Вход не закончился ничем."""


# --- переход как операция ------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenCompany(BrowserOperation[None, None]):
    __browser__ = Browser.goto("/companies/{company_id}", at=css("h1.company"))

    company_id: str


@dataclass(frozen=True, slots=True)
class CompanyCard:
    api: AsyncBrowserApi


async def _card_missing(content: None) -> NoReturn:
    _ = content
    raise NotReadyError(css("h1.company"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ShowCompany(BrowserOperation[None, CompanyCard]):
    __browser__ = Browser.goto(
        "/companies/{company_id}",
        outcomes=outcomes(
            when(visible(css("h1.company")), to=CompanyCard), otherwise=_card_missing
        ),
    )

    company_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenHelp(BrowserOperation[None, None]):
    __browser__ = Browser.goto("https://help.example.com/{topic}")

    topic: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenQuick(BrowserOperation[None, None]):
    __browser__ = Browser.goto("/companies", wait="domcontentloaded")


class Page:
    go: Annotated[Element, css("button.go")]


@dataclass(frozen=True, slots=True, kw_only=True)
class Go(BrowserOperation[Page, None]):
    __browser__ = Browser.act(Page, at=css("button.go"))

    async def act(self, content: Page) -> None:
        await content.go.click()


class Portal(AsyncBrowserApi):
    errors = (Failure(when=url.contains("/login"), exception=SessionExpiredError),)

    open_company = op(OpenCompany)
    show_company = op(ShowCompany)
    go = op(Go)


def portal(driver: FakeDriver, base_url: str = BASE) -> Portal:
    return Portal(AsyncBrowserClient(driver, base_url=base_url))


async def test_goto_opens_the_address_from_its_fields_and_checks_readiness_there() -> None:
    """Значение поля кодируется целиком: `/` в нём — символ, а не разделитель пути."""
    target = f"{BASE}/companies/c%2042%2Fx"
    driver = FakeDriver(pages={target: {"h1.company"}})

    await portal(driver).open_company(company_id="c 42/x")

    assert driver.log == [f"goto {target}"]
    assert driver.url == target


async def test_redirect_to_login_is_the_declared_failure_not_an_unready_page() -> None:
    driver = FakeDriver(redirects={f"{BASE}/companies/42": f"{LOGIN}?next=/companies/42"})

    with pytest.raises(SessionExpiredError):
        await portal(driver).open_company(company_id="42")


async def test_page_that_no_rule_explains_is_not_ready() -> None:
    driver = FakeDriver()

    with pytest.raises(NotReadyError):
        await portal(driver).open_company(company_id="42")

    assert driver.log == [f"goto {BASE}/companies/42"]


async def test_action_readiness_miss_asks_the_rules_first() -> None:
    """Кнопки нет, потому что портал увёл на вход: это истёкшая сессия, а не «не готово»."""
    driver = FakeDriver(url=LOGIN)

    with pytest.raises(SessionExpiredError):
        await portal(driver).go()

    assert driver.log == []


async def test_goto_with_outcomes_returns_the_state_built_from_the_router() -> None:
    driver = FakeDriver(pages={f"{BASE}/companies/42": {"h1.company"}})
    router = portal(driver)

    card = await router.show_company(company_id="42", options=FAST)

    assert isinstance(card, CompanyCard)
    assert card.api is router


async def test_absolute_address_does_not_need_a_base_url() -> None:
    driver = FakeDriver()

    await execute(OpenHelp(topic="вход"), driver)

    assert driver.log == ["goto https://help.example.com/%D0%B2%D1%85%D0%BE%D0%B4"]


async def test_relative_address_needs_the_client_base_url() -> None:
    driver = FakeDriver()

    with pytest.raises(BrowserDeclarationError, match="base_url"):
        await portal(driver, base_url="").open_company(company_id="42")

    assert driver.log == []


@dataclass
class RecordingDriver(FakeDriver):
    seen: list[tuple[str, LoadState, float]] = field(default_factory=list)

    async def goto(self, url: str, *, wait: LoadState = "load", within: float) -> None:
        self.seen.append((url, wait, within))
        await super().goto(url, wait=wait, within=within)


async def test_navigation_timeout_comes_from_the_client_and_the_call() -> None:
    driver = RecordingDriver()
    client = AsyncBrowserClient(
        driver, base_url=BASE, config=BrowserClientConfig(navigation_timeout=12.0)
    )

    await client.execute(OpenQuick())
    await client.execute(OpenQuick(), options=BrowserCallOptions(navigation_timeout=3.0))

    assert driver.seen == [
        (f"{BASE}/companies", "domcontentloaded", 12.0),
        (f"{BASE}/companies", "domcontentloaded", 3.0),
    ]


def test_d_b_09_goto_operation_has_no_body() -> None:
    with pytest.raises(BrowserDeclarationError, match="D-B-09"):

        class Both(BrowserOperation[None, None]):
            __browser__ = Browser.goto("/companies")

            async def act(self, content: None) -> None:
                _ = content


def test_d_b_10_address_placeholders_are_plain_fields_of_the_operation() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Typo(BrowserOperation[None, None]):
        __browser__ = Browser.goto("/companies/{company}")

        company_id: str

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Formatted(BrowserOperation[None, None]):
        __browser__ = Browser.goto("/companies/{company_id!r}")

        company_id: str

    with pytest.raises(BrowserDeclarationError, match=r"D-B-10: Typo .* no field company$"):
        op(Typo)
    with pytest.raises(BrowserDeclarationError, match="D-B-10: Formatted"):
        op(Formatted)


# --- переход после действия ---------------------------------------------------------------


class LoginForm:
    submit: Annotated[Element, css("button.login")]


@dataclass(frozen=True, slots=True)
class Mailbox:
    pass


@dataclass(frozen=True, slots=True)
class BadCredentials:
    pass


async def _mailbox(content: LoginForm) -> Mailbox:
    _ = content
    return Mailbox()


async def _rejected(content: LoginForm) -> BadCredentials:
    _ = content
    return BadCredentials()


async def _hangs(content: LoginForm) -> NoReturn:
    _ = content
    raise LoginHangsError


@dataclass(frozen=True, slots=True, kw_only=True)
class NaiveLogin(BrowserOperation[LoginForm, Mailbox | BadCredentials]):
    """Исходы без `navigated()`: форма входа ещё видна сразу после клика."""

    __browser__ = Browser.act(
        LoginForm,
        outcomes=outcomes(
            when(url.contains("/inbox"), then=_mailbox),
            when(visible(css("form.login")), then=_rejected),
            otherwise=_hangs,
        ),
    )

    async def act(self, content: LoginForm) -> None:
        await content.submit.click()


@dataclass(frozen=True, slots=True, kw_only=True)
class Login(BrowserOperation[LoginForm, Mailbox | BadCredentials]):
    __browser__ = Browser.act(
        LoginForm,
        outcomes=outcomes(
            when(navigated() & url.contains("/inbox"), then=_mailbox),
            when(navigated() & visible(css("form.login")), then=_rejected),
            otherwise=_hangs,
        ),
        requires=(Capability.navigation_events,),
    )

    async def act(self, content: LoginForm) -> None:
        await content.submit.click()


def login_page(*, delay: float) -> NavigatingFakeDriver:
    """Форма входа; клик уводит в почту не сразу, а через `delay` секунд."""
    return NavigatingFakeDriver(
        url=LOGIN,
        present={"form.login", "button.login"},
        pages={INBOX: {"div.inbox"}},
        links={"button.login": INBOX},
        delay=delay,
    )


async def test_without_navigated_the_old_page_is_mistaken_for_the_outcome() -> None:
    """Так выглядит гонка, ради которой признак и нужен: отказ, хотя вход удался."""
    result = await execute(
        NaiveLogin(), login_page(delay=0.1), options=BrowserCallOptions(timeout=1.0)
    )

    assert isinstance(result, BadCredentials)


async def test_navigated_waits_out_the_old_page() -> None:
    driver = login_page(delay=0.1)

    result = await execute(Login(), driver, options=BrowserCallOptions(timeout=1.0))

    assert isinstance(result, Mailbox)
    assert driver.visits == 1


async def test_navigation_before_the_action_is_not_its_transition() -> None:
    driver = login_page(delay=0)
    driver.links = {}
    await driver.goto(LOGIN, within=1.0)

    with pytest.raises(LoginHangsError):
        await execute(Login(), driver, options=BrowserCallOptions(timeout=0.1))

    assert driver.visits == 1


async def test_navigation_sign_needs_its_capability_before_acting() -> None:
    driver = FakeDriver(url=LOGIN, present={"form.login", "button.login"})

    with pytest.raises(CapabilityMismatchError) as raised:
        await execute(Login(), driver)

    assert raised.value.dimensions == ("navigation_events",)
    assert driver.log == []


class WatchfulPortal(AsyncBrowserApi):
    errors = (Failure(when=response.arrived("/api/session"), exception=SessionExpiredError),)

    go = op(Go)


async def test_router_signs_add_their_requirements_before_acting() -> None:
    """Правило роутера читает сеть — драйвер без сети отсекается до клика, а не после."""
    driver = FakeDriver(present={"button.go"})

    with pytest.raises(CapabilityMismatchError) as raised:
        await WatchfulPortal(AsyncBrowserClient(driver)).go()

    assert raised.value.dimensions == ("network",)
    assert driver.log == []


def test_navigated_reads_as_its_label_and_names_its_capability() -> None:
    sign = navigated() & ~url.contains("/login")

    assert sign.label == "(navigated and not url contains '/login')"
    assert sign.requires == (Capability.navigation_events,)
    assert (visible(css("a")) | response.arrived("/api")).requires == (Capability.network,)


def test_d_b_04_navigation_sign_and_its_requirement_go_together() -> None:
    with pytest.raises(
        BrowserDeclarationError, match=r"D-B-04: Undeclared needs .*navigation_events"
    ):

        class Undeclared(BrowserOperation[LoginForm, Mailbox | BadCredentials]):
            __browser__ = Browser.act(
                LoginForm,
                outcomes=outcomes(
                    when(navigated(), then=_mailbox),
                    when(visible(css("form.login")), then=_rejected),
                    otherwise=_hangs,
                ),
            )

    with pytest.raises(
        BrowserDeclarationError, match=r"D-B-04: Stale declares .*navigation_events"
    ):

        class Stale(BrowserOperation[LoginForm, None]):
            __browser__ = Browser.act(LoginForm, requires=(Capability.navigation_events,))
