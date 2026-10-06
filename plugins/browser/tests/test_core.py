"""Ядро: локаторы, карта, условие готовности, параллельная работа операций."""

import asyncio
from dataclasses import dataclass
from typing import Annotated

import pytest
from eazy_sdk_browser import (
    Browser,
    BrowserDeclarationError,
    BrowserOperation,
    Capability,
    CurrentDriver,
    Driver,
    Element,
    ElementNotFoundError,
    Handle,
    NotReadyError,
    RichText,
    any_of,
    build_content,
    click,
    css,
    execute,
    last,
    outcomes,
    region,
    rich,
    visible,
    when,
)
from eazy_sdk_browser.locators import Pick
from eazy_sdk_browser.testing import FakeDriver, RichTextFakeDriver, framed

SUBJECT = 'input[name="Subject"]'
BODY = 'div[contenteditable="true"]'
RECIPIENT_CANDIDATES = (
    'input[type="text"]:not([name])',
    'input[aria-label*="Кому"]',
)


class ComposeContent:
    """Карта формы письма."""

    recipient: Annotated[Element, any_of(*RECIPIENT_CANDIDATES)]
    subject: Annotated[Element, css(SUBJECT)]
    body: Annotated[RichText, rich(last(BODY))]
    session: Annotated[Driver, CurrentDriver()]


@dataclass(frozen=True, slots=True, kw_only=True)
class Compose(BrowserOperation[ComposeContent, None]):
    """Заполняет форму письма. Исходов нет: сделала — и всё."""

    __browser__ = Browser.act(ComposeContent, at=css(SUBJECT), requires=(Capability.rich_text,))

    to_email: str

    async def act(self, content: ComposeContent) -> None:
        await content.recipient.fill(self.to_email)
        await content.subject.fill("Тема")
        await content.body.set_html("<p>Текст</p>")


def mailbox() -> RichTextFakeDriver:
    return RichTextFakeDriver(present={SUBJECT, BODY, RECIPIENT_CANDIDATES[0]})


@pytest.mark.unit
async def test_operation_runs_its_steps_in_order() -> None:
    driver = mailbox()

    await execute(Compose(to_email="user@example.com"), driver)

    assert driver.log == [
        f"fill {RECIPIENT_CANDIDATES[0]} = user@example.com",
        f"fill {SUBJECT} = Тема",
        f"html {BODY} = <p>Текст</p>",
    ]


@pytest.mark.unit
async def test_fallback_selector_is_used_when_the_first_is_gone() -> None:
    """Запасной селектор — часть языка локаторов, а не хелпер в каждом проекте."""
    driver = RichTextFakeDriver(present={SUBJECT, BODY, RECIPIENT_CANDIDATES[1]})

    await execute(Compose(to_email="user@example.com"), driver)

    assert f"fill {RECIPIENT_CANDIDATES[1]} = user@example.com" in driver.log


@pytest.mark.unit
async def test_missing_element_names_every_candidate() -> None:
    driver = RichTextFakeDriver(present={SUBJECT, BODY})

    with pytest.raises(ElementNotFoundError) as raised:
        await execute(Compose(to_email="user@example.com"), driver)

    assert raised.value.locator.selectors == RECIPIENT_CANDIDATES


@pytest.mark.unit
async def test_readiness_is_checked_before_any_step() -> None:
    """Условие готовности проверяет раннер: автор операции про него не забывает."""
    driver = RichTextFakeDriver(present=set())

    with pytest.raises(NotReadyError):
        await execute(Compose(to_email="user@example.com"), driver)

    assert driver.log == []


@pytest.mark.unit
async def test_same_operation_serves_two_tabs_at_once() -> None:
    """Операция неизменяема и без состояния — её можно гонять параллельно."""
    first, second = mailbox(), mailbox()
    operation = Compose(to_email="user@example.com")

    await asyncio.gather(execute(operation, first), execute(operation, second))

    assert first.log == second.log
    assert len(first.log) == 3


@pytest.mark.unit
async def test_current_driver_marker_puts_the_driver_in_the_map() -> None:
    driver = mailbox()

    content = build_content(ComposeContent, driver)

    assert content.session is driver


@pytest.mark.unit
def test_field_without_marker_is_refused_at_build_time() -> None:
    """Аннотация без маркера — опечатка, а не повод молча пропустить поле."""

    class Broken:
        button: Element

    with pytest.raises(BrowserDeclarationError, match=r"Broken\.button"):
        build_content(Broken, FakeDriver())


@pytest.mark.unit
async def test_region_prefixes_selectors_of_its_card() -> None:
    class Panel:
        query: Annotated[Element, css('input[name="q"]')]

    class Page:
        filters: Annotated[Panel, region(Panel, root=css("form.filters"))]

    driver = FakeDriver(present={'form.filters input[name="q"]'})

    page = build_content(Page, driver)
    await page.filters.query.fill("ромашка")

    assert driver.log == ['fill form.filters input[name="q"] = ромашка']


@pytest.mark.unit
async def test_last_picks_the_newest_node() -> None:
    """Композер открывается поверх прежних — берём последний, а не первый."""
    driver = mailbox()

    content = build_content(ComposeContent, driver)
    await content.body.click()

    assert driver.log == [f"click {BODY}"]
    assert last(BODY).pick is Pick.last


@pytest.mark.unit
async def test_operation_frames_reach_every_element_of_its_map() -> None:
    """Почта живёт во фрейме: объявлять его у каждого поля — переписывать одно и то же."""

    class Composer:
        to: Annotated[Element, css('textbox[name="to"]')]
        body: Annotated[Element, css("div.editor", frames=("iframe.editor",))]

    @dataclass(frozen=True, slots=True)
    class Send(BrowserOperation[Composer, None]):
        __browser__ = Browser.act(Composer, frames=('iframe[name="mail"]',))

        async def act(self, content: Composer) -> None:
            await content.to.fill("user@example.com")
            await content.body.fill("Текст")

    driver = FakeDriver(
        present={
            framed('textbox[name="to"]', ('iframe[name="mail"]',)),
            # Фрейм операции снаружи, собственный фрейм поля — глубже.
            framed("div.editor", ('iframe[name="mail"]', "iframe.editor")),
        }
    )

    await execute(Send(), driver)

    assert driver.log == [
        'fill iframe[name="mail"] >>> textbox[name="to"] = user@example.com',
        'fill iframe[name="mail"] >>> iframe.editor >>> div.editor = Текст',
    ]


@pytest.mark.unit
async def test_interceptor_clears_the_banner_before_the_outcome_is_read() -> None:
    """Баннер перекрывает страницу: сначала убираем помеху, потом смотрим исход."""
    banner = css("div.cookie-banner")
    accept = css("button.accept")

    class Page:
        marker: Annotated[Element, css("div.ready")]

    @dataclass(frozen=True, slots=True)
    class Done:
        pass

    async def done(content: Page) -> Done:
        _ = content
        return Done()

    async def never(content: Page) -> Done:
        _ = content
        raise AssertionError("исход должен был наступить после уборки баннера")

    @dataclass(frozen=True, slots=True)
    class Work(BrowserOperation[Page, Done]):
        __browser__ = Browser.act(
            Page,
            outcomes=outcomes(when(visible(css("div.ready")), then=done), otherwise=never),
            handlers=(Handle(when=visible(banner), do=click(accept)),),
        )

        async def act(self, content: Page) -> None:
            _ = content

    driver = FakeDriver(present={"div.cookie-banner", "button.accept", "div.ready"})

    assert isinstance(await execute(Work(), driver), Done)
    assert driver.log == ["click button.accept"], "баннер закрывают один раз, а не по кругу"


@pytest.mark.unit
async def test_template_element_takes_its_value_from_the_call() -> None:
    """Селектор карты статичен, а искать нужно по значению — тема письма, имя компании."""
    from eazy_sdk_browser import Template, template_text

    class Inbox:
        row: Annotated[Template, template_text('text="{subject}"')]

    driver = FakeDriver(present={'text="Отчёт за март"'})

    inbox = build_content(Inbox, driver)
    await inbox.row(subject="Отчёт за март").click()

    assert driver.log == ['click text="Отчёт за март"']
    assert not await inbox.row(subject="Письма нет").visible()
