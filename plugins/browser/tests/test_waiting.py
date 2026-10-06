"""Ожидание: ждёт цикл, а не признак; сеть считается от действия; помехи — до него.

Фейк здесь с задержкой (`latency`): без неё поиск отсутствующего узла отвечает
сразу, и признак, который ждёт вместо цикла, ничего не стоит — регрессия невидима.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Annotated

import pytest
from eazy_sdk_browser import (
    AsyncBrowserClient,
    Browser,
    BrowserCallOptions,
    BrowserOperation,
    Element,
    Handle,
    any_of,
    click,
    css,
    execute,
    outcomes,
    visible,
    when,
)
from eazy_sdk_browser.network import ResponseView
from eazy_sdk_browser.testing import FakeDriver, NetworkFakeDriver

from examples.browser_portal import (
    COMPANIES_API,
    DIALOG_ROOT,
    CompaniesPortal,
    Created,
    PortalSilentError,
)

FIRST = "div.first"
SECOND = "div.second"
SUBMIT = f'{DIALOG_ROOT} button[type="submit"]'
FORM = {
    DIALOG_ROOT,
    f'{DIALOG_ROOT} input[name="name"]',
    f'{DIALOG_ROOT} input[name="inn"]',
    SUBMIT,
}


class Page:
    marker: Annotated[Element, css("div.ready")]


@dataclass(frozen=True, slots=True)
class Seen:
    which: str


async def _first(content: Page) -> Seen:
    _ = content
    return Seen(FIRST)


async def _second(content: Page) -> Seen:
    _ = content
    return Seen(SECOND)


async def _never(content: Page) -> Seen:
    _ = content
    raise AssertionError("исход должен был наступить до дедлайна")


async def _gave_up(content: Page) -> Seen:
    _ = content
    return Seen("gave up")


@dataclass(frozen=True, slots=True)
class Watch(BrowserOperation[Page, Seen]):
    """Два исхода: первый не наступает никогда, второй — чуть позже действия."""

    __browser__ = Browser.act(
        Page,
        outcomes=outcomes(
            when(visible(css(FIRST)), then=_first),
            when(visible(css(SECOND)), then=_second),
            otherwise=_never,
        ),
    )

    async def act(self, content: Page) -> None:
        _ = content


@dataclass(frozen=True, slots=True)
class Ready(BrowserOperation[Page, Seen]):
    __browser__ = Browser.act(
        Page, outcomes=outcomes(when(visible(css("div.ready")), then=_first), otherwise=_never)
    )

    async def act(self, content: Page) -> None:
        _ = content


@dataclass(frozen=True, slots=True)
class Hopeless(BrowserOperation[Page, Seen]):
    __browser__ = Browser.act(
        Page, outcomes=outcomes(when(visible(css(FIRST)), then=_first), otherwise=_gave_up)
    )

    async def act(self, content: Page) -> None:
        _ = content


def _reply(company_id: str) -> ResponseView:
    return ResponseView(
        url=f"https://portal.example.com{COMPANIES_API}",
        status=201,
        body=json.dumps({"id": company_id, "name": "ООО Ромашка"}).encode(),
        headers=(("content-type", "application/json"),),
    )


async def _appear(driver: FakeDriver, selector: str, after: float) -> None:
    await asyncio.sleep(after)
    driver.present.add(selector)


# --- B0.1: признаки не ждут -------------------------------------------------------------


@pytest.mark.unit
async def test_later_outcome_is_reached_while_the_first_sign_is_absent() -> None:
    """Первый признак отсутствует, второй появляется через 100 мс, дедлайн — секунда.

    Признак, который ждёт свой элемент полный таймаут локатора (5 с), съел бы дедлайн
    целиком, и второй исход стал бы недостижим.
    """
    driver = FakeDriver(latency=0.02)
    started = time.perf_counter()

    result, _ = await asyncio.gather(
        execute(Watch(), driver, options=BrowserCallOptions(timeout=1.0)),
        _appear(driver, SECOND, 0.1),
    )

    assert result == Seen(SECOND)
    assert time.perf_counter() - started < 1.0


@pytest.mark.unit
async def test_readiness_waits_for_any_candidate_with_one_timeout() -> None:
    """Условие готовности — одно ожидание на всех кандидатов, а не по очереди."""
    driver = FakeDriver(latency=0.01)

    result, _ = await asyncio.gather(execute(Ready(), driver), _appear(driver, "div.ready", 0.05))

    assert result == Seen(FIRST)


@pytest.mark.unit
async def test_call_options_set_the_settle_deadline() -> None:
    """Сколько ждать — свойство вызова, а не объявления."""
    driver = FakeDriver(latency=0.01)
    started = time.perf_counter()

    result = await execute(Hopeless(), driver, options=BrowserCallOptions(timeout=0.05))

    assert result == Seen("gave up")
    assert time.perf_counter() - started < 0.5


# --- B0.2: помехи до действия -------------------------------------------------------------


@pytest.mark.unit
async def test_banner_is_removed_before_the_action_and_only_once() -> None:
    """Баннер на странице до клика: сначала его закрывают, потом кликают.

    Тот же обработчик потом крутится в ожидании исхода, но `once=True` считается на
    всю операцию, а не на фазу — второго клика по «принять» нет.
    """
    banner, accept = css("div.cookie-banner"), css("button.accept")

    class Site:
        create: Annotated[Element, css("button.create")]

    async def done(content: Site) -> Seen:
        _ = content
        return Seen("done")

    async def never(content: Site) -> Seen:
        _ = content
        raise AssertionError("исход должен был наступить после уборки баннера")

    @dataclass(frozen=True, slots=True)
    class Create(BrowserOperation[Site, Seen]):
        __browser__ = Browser.act(
            Site,
            outcomes=outcomes(when(visible(css("div.ready")), then=done), otherwise=never),
            handlers=(Handle(when=visible(banner), do=click(accept)),),
        )

        async def act(self, content: Site) -> None:
            await content.create.click()

    driver = FakeDriver(
        present={"div.cookie-banner", "button.accept", "button.create", "div.ready"}
    )

    assert await execute(Create(), driver) == Seen("done")
    assert driver.log == ["click button.accept", "click button.create"]


# --- B0.3: водораздел буфера сети ---------------------------------------------------------


@pytest.mark.unit
async def test_second_submit_reads_the_second_reply_not_the_first() -> None:
    """Две отправки формы подряд: вторая получает второй ответ, а не прошлый."""
    driver = NetworkFakeDriver(present=set(FORM), answers={SUBMIT: [_reply("c-1"), _reply("c-2")]})
    portal = CompaniesPortal(AsyncBrowserClient(driver))

    first = await portal.submit_company(name="ООО Ромашка", inn="7701234567")
    second = await portal.submit_company(name="ООО Ромашка", inn="7701234567")

    assert (first, second) == (Created("c-1", "ООО Ромашка"), Created("c-2", "ООО Ромашка"))


@pytest.mark.unit
async def test_reply_that_arrived_before_the_action_is_not_its_answer() -> None:
    """Ответ лежал в буфере до действия — это чужой ответ, и исход его не видит."""
    driver = NetworkFakeDriver(present=set(FORM), replies=[_reply("c-old")])

    with pytest.raises(PortalSilentError):
        await CompaniesPortal(AsyncBrowserClient(driver)).submit_company(
            name="ООО Ромашка", inn="7701234567"
        )


@pytest.mark.unit
async def test_mark_hides_everything_before_it() -> None:
    driver = NetworkFakeDriver(replies=[_reply("c-old")])

    before = driver.mark()
    driver.replies.append(_reply("c-new"))

    seen = await driver.wait_response(COMPANIES_API, within=0, since=before)
    hidden = await driver.wait_response(COMPANIES_API, within=0, since=driver.mark())

    assert seen is not None
    assert json.loads(seen.body)["id"] == "c-new"
    assert hidden is None


# --- B0.4: any_of не ждёт кандидатов по очереди -------------------------------------------


@pytest.mark.unit
async def test_any_of_does_not_pay_a_timeout_per_missing_candidate() -> None:
    """Три кандидата, есть только третий: время — не больше одного таймаута."""
    driver = FakeDriver(present={"c"}, latency=0.01)
    locator = any_of("a", "b", "c", timeout=0.2)
    started = time.perf_counter()

    await locator.bind(driver).click()

    assert driver.log == ["click c"]
    assert time.perf_counter() - started < 0.2
