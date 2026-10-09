"""Адаптер playwright на настоящем Chromium.

Страница и её API поднимаются перехватом запросов, поэтому тесты не ходят в сеть и
не зависят от чужого сервера. Проверяется именно адаптер: находит ли он узлы, видит
ли ответы, совпадает ли объявленный профиль с тем, что он умеет на самом деле.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from eazy_sdk_browser import (
    AsyncBrowserClient,
    BrowserCallOptions,
    BrowserState,
    Elements,
    Origin,
    css_escape,
    rich,
    template,
    template_text,
)
from eazy_sdk_browser.fetch import PageRequest
from eazy_sdk_browser.handlers import CapturePolicy
from eazy_sdk_browser.handlers.playwright import (
    PLAYWRIGHT_PROFILE,
    PlaywrightDriver,
    from_storage_state,
    to_storage_state,
)
from eazy_sdk_browser.integrations.handler import BrowserHandler
from eazy_sdk_browser.locators import any_of, css
from eazy_sdk_browser.network import ResponseMissingError
from eazy_sdk_browser.profile import Capability
from playwright.async_api import BrowserContext, Page, Route, async_playwright

from eazy_sdk import AsyncClient, bind
from eazy_sdk.cookies import StoredCookie
from eazy_sdk.handlers import CapabilityMismatchError, TransportError
from examples.browser_portal import (
    CREATE_BUTTON,
    AccessDeniedError,
    CompaniesApi,
    CompaniesPortal,
    CompanyExistsError,
    CreateCompanyDialog,
    Created,
    PortalSdk,
)

# Настоящий браузер: холодный запуск Chromium дольше корневого `timeout = 10`.
pytestmark = pytest.mark.timeout(120)

PORTAL_HOST = "portal.example.test"
PORTAL_ORIGIN = f"https://{PORTAL_HOST}"
PORTAL_URL = f"{PORTAL_ORIGIN}/companies"
API_URL = "**/api/companies"
FAST = 2.0
OPTIONS = BrowserCallOptions(timeout=FAST)

PAGE_HTML = """
<!doctype html>
<html lang="ru">
  <body>
    <form class="filters"><input name="q"><button type="submit">Искать</button></form>
    <button data-test="create-company">Создать компанию</button>
    <div data-test="banner"></div>
    <script>
      document.querySelector('[data-test="create-company"]').addEventListener('click', () => {
        const dialog = document.createElement('div');
        dialog.setAttribute('role', 'dialog');
        dialog.innerHTML =
          '<input name="name"><input name="inn"><button type="submit">Сохранить</button>';
        document.body.appendChild(dialog);
        dialog.querySelector('button').addEventListener('click', async (event) => {
          event.preventDefault();
          await fetch('/api/companies', {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({name: dialog.querySelector('[name="name"]').value}),
          });
        });
      });
    </script>
  </body>
</html>
"""


LIST_HTML = """
<!doctype html>
<html lang="ru">
  <body>
    <table class="companies">
      <tbody>
        <tr data-id="c-1"><td>ООО Ромашка</td></tr>
        <tr data-id="template" hidden><td>шаблон строки</td></tr>
        <tr data-id="c-2"><td>АО Лютик</td></tr>
      </tbody>
    </table>
    <select name="role">
      <option value="user">Пользователь</option>
      <option value="admin">Администратор</option>
    </select>
    <label><input type="checkbox" name="agree"> Согласен</label>
    <a class="menu" href="/help" title="Справка">Меню</a>
    <div class="tip" hidden>Подсказка</div>
    <script>
      document.querySelector('a.menu').addEventListener('mouseenter', () => {
        document.querySelector('div.tip').hidden = false;
      });
    </script>
  </body>
</html>
"""


QUOTED_HTML = r"""
<!doctype html>
<html lang="ru">
  <body>
    <table class="companies">
      <tbody>
        <tr data-name='ООО "Ромашка"'><td>ООО "Ромашка"</td></tr>
        <tr data-name="АО Лютик"><td>АО Лютик</td></tr>
      </tbody>
    </table>
    <div id="42">Карточка 42</div>
    <div class="path">C:\temp</div>
    <div class="editor" contenteditable="true"></div>
    <script>
      window.inputs = 0;
      document.querySelector('div.editor').addEventListener('input', () => {
        window.inputs += 1;
      });
    </script>
  </body>
</html>
"""


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    """Chromium со страницей портала; без браузера тест пропускается, а не падает."""
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001 — нет браузера: это не провал адаптера
            pytest.skip(f"Chromium недоступен: {error}")
        opened = await browser.new_page()
        await opened.route(
            "**/companies",
            # Без charset браузер прочитает UTF-8 как latin-1 и покажет кракозябры.
            lambda route: route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=PAGE_HTML
            ),
        )
        try:
            yield opened
        finally:
            await browser.close()


def portal_of(driver: PlaywrightDriver) -> CompaniesPortal:
    return CompaniesPortal(AsyncBrowserClient(driver))


async def answer(page: Page, status: int, payload: object) -> None:
    """Ответ портала на создание компании."""

    async def handler(route: Route) -> None:
        await route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload),
        )

    await page.route(API_URL, handler)


@pytest.mark.integration
async def test_driver_finds_nodes_and_reads_the_page(page: Page) -> None:
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page)

    button = await driver.find(css(CREATE_BUTTON, timeout=FAST))
    missing = await driver.find(css("button.absent", timeout=0.2))

    assert button is not None
    assert await button.text() == "Создать компанию"
    assert missing is None, "ненайденный узел — обычный исход поиска, а не исключение"
    assert PORTAL_URL in await driver.location()
    assert "Создать компанию" in await driver.page_text()
    await driver.aclose()


@pytest.mark.integration
async def test_candidates_are_waited_together_but_chosen_by_declaration(page: Page) -> None:
    """`or_` отдаёт узлы в порядке DOM; предпочтение — в порядке объявления."""
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page, capture=None)

    # В DOM кнопка фильтров стоит раньше кнопки создания — объявлен обратный порядок.
    chosen = await driver.find(
        any_of(CREATE_BUTTON, "form.filters button", "button.absent", timeout=FAST)
    )
    peeked = await driver.peek(any_of("button.absent", "form.filters button"))

    assert chosen is not None
    assert await chosen.text() == "Создать компанию"
    assert peeked is not None
    assert await peeked.text() == "Искать"
    assert await driver.peek(css("button.absent")) is None


@pytest.mark.integration
async def test_collection_counts_visible_rows_in_document_order(page: Page) -> None:
    """Скрытая строка-шаблон не входит в коллекцию и не сдвигает номера строк."""
    await page.set_content(LIST_HTML)
    driver = PlaywrightDriver(page, capture=None)
    rows = Elements(driver, css("table.companies tr", timeout=FAST))

    assert await rows.count() == 2
    assert await rows.texts() == ["ООО Ромашка", "АО Лютик"]
    assert await rows.nth(-1).attribute("data-id") == "c-2"
    assert [await row.attribute("data-id") async for row in rows] == ["c-1", "c-2"]
    assert await Elements(driver, css("table.absent tr")).count() == 0
    await driver.aclose()


@pytest.mark.integration
async def test_element_selects_checks_hovers_and_reads_values(page: Page) -> None:
    await page.set_content(LIST_HTML)
    driver = PlaywrightDriver(page, capture=None)
    role = css('select[name="role"]', timeout=FAST).bind(driver)
    agree = css('input[name="agree"]', timeout=FAST).bind(driver)
    menu = css("a.menu", timeout=FAST).bind(driver)

    await role.select("Администратор")
    await agree.check()
    checked = await page.is_checked('input[name="agree"]')
    await agree.uncheck()
    await menu.hover()

    assert await role.value() == "admin"
    assert checked
    assert not await page.is_checked('input[name="agree"]')
    assert await css("div.tip", timeout=FAST).bind(driver).visible()
    assert await menu.attribute("title") == "Справка"
    assert await menu.attribute("data-absent") is None
    await driver.aclose()


@pytest.mark.contract
@pytest.mark.integration
async def test_css_escape_matches_the_browser_css_escape(page: Page) -> None:
    """Экранирование сверено с `CSS.escape` самого браузера, а не с нашим прочтением спецификации."""
    samples = ['ООО "Ромашка"', "a\\b", "42", "-1", "-", "line\nbreak", "\x00", "a_b-c", "#.:[]"]

    expected = await page.evaluate("samples => samples.map((value) => CSS.escape(value))", samples)

    assert [css_escape(sample) for sample in samples] == expected


@pytest.mark.integration
async def test_templates_find_values_with_quotes_on_real_dom(page: Page) -> None:
    await page.set_content(QUOTED_HTML)
    driver = PlaywrightDriver(page, capture=None)
    row = template('tr[data-name="{name}"]', timeout=FAST).bind(driver)
    card = template("#{company_id}", timeout=FAST).bind(driver)
    exact = template_text('text="{value}"', timeout=FAST).bind(driver)
    has = template_text('tr:has-text("{name}")', timeout=FAST).bind(driver)

    assert await row(name='ООО "Ромашка"').text() == 'ООО "Ромашка"'
    assert await card(company_id=42).text() == "Карточка 42"
    assert await exact(value='ООО "Ромашка"').text() == 'ООО "Ромашка"'
    assert await exact(value="C:\\temp").text() == "C:\\temp"
    assert await has(name='ООО "Ромашка"').attribute("data-name") == 'ООО "Ромашка"'
    await driver.aclose()


@pytest.mark.integration
async def test_rich_text_puts_markup_and_tells_the_editor(page: Page) -> None:
    await page.set_content(QUOTED_HTML)
    driver = PlaywrightDriver(page, capture=None)
    editor = rich(css("div.editor", timeout=FAST)).bind(driver)

    await editor.set_html("<p><b>Жирный</b> текст</p>")

    assert PLAYWRIGHT_PROFILE.supports(Capability.rich_text)
    assert await page.inner_html("div.editor") == "<p><b>Жирный</b> текст</p>"
    assert await page.evaluate("window.inputs") == 1
    await driver.aclose()


@pytest.mark.integration
async def test_closed_page_is_a_transport_failure_not_a_missing_node(page: Page) -> None:
    """Закрытая страница — отказ транспорта, а не «элемент не нашёлся»."""
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page, capture=None)
    await page.close()

    with pytest.raises(TransportError) as raised:
        await driver.find(css(CREATE_BUTTON, timeout=FAST))

    assert (raised.value.handler, raised.value.phase) == ("playwright", "find")
    with pytest.raises(TransportError):
        await driver.peek(css(CREATE_BUTTON))


@pytest.mark.integration
async def test_goto_operation_opens_the_portal_and_waits_for_readiness(page: Page) -> None:
    """Переход — операция роутера: адрес от `base_url`, готовность — уже на открытой странице."""
    driver = PlaywrightDriver(page)
    portal = CompaniesPortal(AsyncBrowserClient(driver, base_url=PORTAL_ORIGIN))

    await portal.open_companies(options=OPTIONS)
    dialog = await portal.open_create_company(options=OPTIONS)

    assert await driver.location() == PORTAL_URL
    assert isinstance(dialog, CreateCompanyDialog)
    await driver.aclose()


@pytest.mark.integration
async def test_unreachable_address_is_a_transport_failure(page: Page) -> None:
    await page.route("**/down", lambda route: route.abort())
    driver = PlaywrightDriver(page, capture=None)

    with pytest.raises(TransportError) as raised:
        await driver.goto(f"{PORTAL_ORIGIN}/down", within=FAST)

    assert (raised.value.handler, raised.value.phase) == ("playwright", "goto")
    await driver.aclose()


@pytest.mark.contract
@pytest.mark.integration
async def test_navigation_is_an_event_even_to_the_same_address(page: Page) -> None:
    """Перезагрузка того же адреса — переход, которого опрос `location()` не увидит."""
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page, capture=None)
    before = driver.navigations()

    await page.reload()

    assert PLAYWRIGHT_PROFILE.supports(Capability.navigation_events)
    assert driver.navigations() == before + 1
    assert await driver.location() == PORTAL_URL
    await driver.aclose()


@pytest.mark.integration
async def test_full_scenario_on_real_browser(page: Page) -> None:
    """Сценарий целиком: клик, диалог, форма, ответ API — на настоящем Chromium."""
    await answer(page, 201, {"id": "c-42", "name": "ООО Ромашка"})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page)

    dialog = await portal_of(driver).open_create_company(options=OPTIONS)
    created = await dialog.submit(name="ООО Ромашка", inn="7701234567")

    assert created == Created(company_id="c-42", name="ООО Ромашка")
    await driver.aclose()


@pytest.mark.integration
async def test_one_root_serves_requests_and_clicks(page: Page) -> None:
    """HTTP-роутер и браузерный роутер в одном `AsyncRoot`, на одном драйвере.

    `sdk.api.create` уходит запросом из страницы, `sdk.portal.open_create_company` —
    кликом; вызывающий не видит, чем выполнена операция.
    """
    await answer(page, 201, {"id": "c-42", "name": "ООО Ромашка"})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page)

    async with AsyncClient(base_url=PORTAL_ORIGIN, handler=BrowserHandler(driver)) as http:
        sdk = PortalSdk(http, bindings=(bind(CompaniesPortal, client=AsyncBrowserClient(driver)),))
        requested = await sdk.api.create(name="ООО Ромашка", inn="7701234567")
        dialog = await sdk.portal.open_create_company(options=OPTIONS)
        clicked = await dialog.submit(name="ООО Ромашка", inn="7701234567")

    assert requested.id == "c-42"
    assert clicked == Created(company_id="c-42", name="ООО Ромашка")
    await driver.aclose()


@pytest.mark.integration
async def test_api_status_becomes_the_declared_exception(page: Page) -> None:
    """409 от настоящего fetch поднимается тем же исключением, что и на фейке."""
    await answer(page, 409, {"code": "company_exists", "message": "ИНН уже заведён"})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page)

    dialog = await portal_of(driver).open_create_company(options=OPTIONS)

    with pytest.raises(CompanyExistsError, match="ИНН уже заведён"):
        await dialog.submit(name="ООО Ромашка", inn="7701234567")
    await driver.aclose()


@pytest.mark.integration
async def test_page_failure_rule_fires_on_real_dom(page: Page) -> None:
    """Страничное правило читает настоящий текст страницы, а не фейковую строку."""
    await page.goto(PORTAL_URL)
    await page.eval_on_selector(
        '[data-test="banner"]',
        "element => { element.textContent = 'Недостаточно прав'; }",
    )
    driver = PlaywrightDriver(page)

    with pytest.raises(AccessDeniedError):
        await portal_of(driver).open_create_company(options=OPTIONS)
    await driver.aclose()


@pytest.mark.contract
@pytest.mark.integration
async def test_profile_matches_what_the_adapter_really_does(page: Page) -> None:
    """Профиль без подписки на сеть не обещает сеть — и операция это замечает."""
    await answer(page, 201, {"id": "c-1", "name": "ООО Ромашка"})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page, capture=None)

    assert PLAYWRIGHT_PROFILE.supports(Capability.network)
    assert not driver.profile.supports(Capability.network)

    dialog = await portal_of(driver).open_create_company(options=OPTIONS)
    with pytest.raises(CapabilityMismatchError) as raised:
        await dialog.submit(name="ООО Ромашка", inn="7701234567")

    assert raised.value.dimensions == (Capability.network,)
    await driver.aclose()


@pytest.mark.integration
async def test_response_that_arrived_before_the_wait_is_not_lost(page: Page) -> None:
    """Ответ мог прийти до того, как его начали ждать: гонку решает буфер, а не удача."""
    await answer(page, 201, {"id": "c-7", "name": "ООО Ромашка"})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page)

    await page.evaluate("fetch('/api/companies', {method: 'POST', body: '{}'})")
    await page.wait_for_timeout(200)
    seen = await driver.wait_response("/api/companies", within=FAST)
    # А с водоразделом после прихода тот же ответ уже чужой.
    hidden = await driver.wait_response("/api/companies", within=0.1, since=driver.mark())

    assert seen is not None
    assert seen.status == 201
    assert json.loads(seen.body)["id"] == "c-7"
    assert hidden is None
    await driver.aclose()


@pytest.mark.integration
async def test_capture_limit_evicts_old_replies_but_keeps_positions(page: Page) -> None:
    """Буфер ограничен в байтах, а позиция считается монотонно: вытеснение её не сдвигает."""
    body = json.dumps({"id": "c-n", "name": "x" * 70})  # 100 байт на ответ
    await page.route(
        "**/api/companies*",
        lambda route: route.fulfill(status=201, content_type="application/json", body=body),
    )
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page, capture=CapturePolicy(max_total_bytes=2 * len(body)))

    before = driver.mark()
    for number in range(1, 4):
        await page.evaluate(f"fetch('/api/companies?n={number}', {{method: 'POST', body: '{{}}'}})")
        await page.wait_for_timeout(100)

    assert driver.mark() == before + 3
    assert await driver.wait_response("n=1", within=0, since=before) is None, "вытеснен"
    assert await driver.wait_response("n=2", within=0, since=before) is not None
    assert await driver.wait_response("/api/companies", within=0, since=before + 2) is not None
    assert await driver.wait_response("/api/companies", within=0, since=before + 3) is None
    await driver.aclose()


@pytest.mark.integration
async def test_images_and_fonts_are_not_read(page: Page) -> None:
    """Картинка грузится страницей, но в буфер не попадает и позиции не занимает."""
    await page.route(
        "**/logo.png",
        lambda route: route.fulfill(status=200, content_type="image/png", body=b"\x89PNG" * 64),
    )
    await answer(page, 201, {"id": "c-1", "name": "x"})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page)

    await page.evaluate(
        "new Promise((done) => { const image = new Image(); image.onload = image.onerror = done;"
        " image.src = '/logo.png'; document.body.append(image); })"
    )
    await post_company(page)

    assert await driver.wait_response("/logo.png", within=0) is None
    assert await driver.wait_response("/api/companies", within=0) is not None
    assert driver.mark() == 1
    await driver.aclose()


@pytest.mark.integration
async def test_oversized_body_is_marked_dropped_and_explained(page: Page) -> None:
    """Тело больше лимита не читается, а операция, ждущая ответ, объясняет почему."""
    await answer(page, 201, {"id": "c-1", "name": "ООО Ромашка" * 20})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page, capture=CapturePolicy(max_body_bytes=16))

    dialog = await portal_of(driver).open_create_company(options=OPTIONS)
    with pytest.raises(ResponseMissingError, match="лимита захвата"):
        await dialog.submit(name="ООО Ромашка", inn="7701234567")

    dropped = await driver.wait_response("/api/companies", within=0)
    assert dropped is not None
    assert (dropped.status, dropped.body, dropped.body_dropped) == (201, b"", True)
    await driver.aclose()


def listeners(page: Page) -> dict[str, int]:
    """Сколько обработчиков висит на событиях страницы, на которые подписан драйвер."""
    # Счёт подписок playwright наружу не выставляет — берём у внутреннего объекта.
    emitter = page._impl_obj
    return {
        event: len(emitter.listeners(event)) for event in ("response", "framenavigated", "close")
    }


async def post_company(page: Page) -> None:
    await page.evaluate("fetch('/api/companies', {method: 'POST', body: '{}'})")
    await page.wait_for_timeout(150)


@pytest.mark.integration
async def test_driver_detaches_itself_when_the_page_closes(page: Page) -> None:
    """Страница закрыта — драйвер отписался сам, и `aclose()` после этого проходит тихо."""
    await answer(page, 201, {"id": "c-1", "name": "x"})
    await page.goto(PORTAL_URL)
    baseline = listeners(page)
    driver = PlaywrightDriver(page)
    await post_company(page)
    assert driver.mark() == 1

    await page.close()

    assert listeners(page) == baseline
    await driver.aclose()
    await driver.aclose()


@pytest.mark.integration
async def test_drivers_wrapped_one_after_another_do_not_accumulate_listeners(page: Page) -> None:
    """Пять драйверов подряд на одной странице: ответ читает только живой."""
    await answer(page, 201, {"id": "c-1", "name": "x"})
    await page.goto(PORTAL_URL)
    baseline = listeners(page)
    closed: list[PlaywrightDriver] = []
    for _ in range(5):
        async with PlaywrightDriver(page) as driver:
            closed.append(driver)

    async with PlaywrightDriver(page) as live:
        assert listeners(page) == {event: count + 1 for event, count in baseline.items()}
        await post_company(page)

        assert live.mark() == 1
        assert [driver.mark() for driver in closed] == [0] * 5
    assert listeners(page) == baseline


@pytest.mark.contract
@pytest.mark.integration
async def test_session_state_moves_between_contexts() -> None:
    """Состояние сессии переживает смену контекста: куки и localStorage на месте.

    Проверка ровно того, ради чего состояние и выгружают: новый контекст — это новый
    «чистый» браузер, и вход в него пришлось бы проходить заново.
    """
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001 — нет браузера: это не провал адаптера
            pytest.skip(f"Chromium недоступен: {error}")

        first = await portal_in(await browser.new_context())
        await first.goto(PORTAL_URL)
        await first.evaluate(
            "document.cookie = 'sid=abc123; path=/'; localStorage.setItem('theme', 'dark');"
        )
        state = await PlaywrightDriver(first, capture=None).export_state()

        assert [cookie.name for cookie in state.cookies] == ["sid"]
        assert state.cookies[0].domain.endswith("portal.example.test")
        assert state.origins[0].mapping() == {"theme": "dark"}

        # Состояние кладёт в контекст тот, кто его создаёт, — при создании.
        second = await portal_in(await browser.new_context(storage_state=to_storage_state(state)))
        await second.goto(PORTAL_URL)

        assert "sid=abc123" in await second.evaluate("document.cookie")
        assert await second.evaluate("localStorage.getItem('theme')") == "dark"
        await browser.close()


async def portal_in(context: BrowserContext) -> Page:
    """Вкладка контекста, в которой портал отвечает страницей без сети."""
    await context.route(
        "**/companies",
        lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=PAGE_HTML
        ),
    )
    return await context.new_page()


@pytest.mark.contract
@pytest.mark.integration
async def test_restored_local_storage_is_not_rewritten_on_navigation() -> None:
    """Сайт обновил токен в localStorage — после перехода он не откатывается к сохранённому.

    Регрессия ревью (`LAYERS.md` §1.2): стартовый скрипт писал сохранённое значение на
    каждом новом документе. Теперь localStorage приходит только со `storage_state`.
    """
    saved = BrowserState(
        origins=(Origin(origin=PORTAL_ORIGIN, items=(("token", "old"),)),),
    )
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001 — нет браузера: это не провал адаптера
            pytest.skip(f"Chromium недоступен: {error}")
        context = await browser.new_context(storage_state=to_storage_state(saved))
        page = await portal_in(context)
        await page.goto(PORTAL_URL)
        assert await page.evaluate("localStorage.getItem('token')") == "old"

        await page.evaluate("localStorage.setItem('token', 'new')")
        await page.goto(PORTAL_URL)

        assert await page.evaluate("localStorage.getItem('token')") == "new"
        await browser.close()


@pytest.mark.integration
async def test_cookies_go_into_the_context_shared_by_its_tabs() -> None:
    """`add_cookies` пишет в контекст: соседняя вкладка видит куку, чужой контекст — нет."""
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001 — нет браузера: это не провал адаптера
            pytest.skip(f"Chromium недоступен: {error}")
        context = await browser.new_context()
        writer = PlaywrightDriver(await portal_in(context), capture=None)
        neighbour = PlaywrightDriver(await portal_in(context), capture=None)
        stranger = PlaywrightDriver(await portal_in(await browser.new_context()), capture=None)

        await writer.add_cookies((StoredCookie(name="sid", value="s1", domain=PORTAL_HOST),))

        assert writer.context_key() is neighbour.context_key()
        assert writer.context_key() is not stranger.context_key()
        assert [cookie.value for cookie in (await neighbour.export_state()).cookies] == ["s1"]
        assert (await stranger.export_state()).cookies == ()
        await browser.close()


def test_storage_state_round_trip_keeps_every_cookie_attribute() -> None:
    """Перевод в `storage_state` и обратно ничего не теряет — ни атрибутов, ни сессионности."""
    state = BrowserState(
        cookies=(
            StoredCookie(
                name="sid",
                value="abc",
                domain="portal.example.test",
                host_only=False,
                path="/app",
                expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                secure=True,
                http_only=True,
                same_site="Strict",
            ),
            StoredCookie(name="consent", value="1", domain=PORTAL_HOST, same_site="Lax"),
        ),
        origins=(Origin(origin=PORTAL_ORIGIN, items=(("theme", "dark"), ("token", "t"))),),
    )

    raw = to_storage_state(state)

    assert raw["cookies"][1]["expires"] == -1
    assert from_storage_state(raw) == state


@pytest.mark.integration
async def test_declared_operation_is_executed_by_the_browser(page: Page) -> None:
    """Объявленная операция уходит запросом из страницы и возвращает свою модель.

    Ни одного клика: DOM здесь не участвует вовсе. Тот же контракт ответа, которым
    разбирается перехваченный ответ, выполняет обычный вызов — только транспортом
    служит сам браузер.
    """
    await answer(page, 201, {"id": "c-9", "name": "ООО Ромашка"})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page, capture=None)

    async with AsyncClient(base_url=PORTAL_ORIGIN, handler=BrowserHandler(driver)) as client:
        created = await CompaniesApi(client).create(name="ООО Ромашка", inn="7701234567")

    assert created.id == "c-9"
    assert created.name == "ООО Ромашка"


@pytest.mark.integration
async def test_declared_failure_still_raises_through_the_browser(page: Page) -> None:
    """Статус отказа поднимает объявленное исключение и на этом транспорте."""
    await answer(page, 409, {"code": "company_exists", "message": "ООО Ромашка уже есть"})
    await page.goto(PORTAL_URL)
    driver = PlaywrightDriver(page, capture=None)

    async with AsyncClient(base_url=PORTAL_ORIGIN, handler=BrowserHandler(driver)) as client:
        with pytest.raises(CompanyExistsError, match="уже есть"):
            await CompaniesApi(client).create(name="ООО Ромашка", inn="7701234567")


@pytest.mark.contract
@pytest.mark.integration
async def test_browser_sends_the_page_cookies_without_being_told(page: Page) -> None:
    """Ради этого браузер и берётся транспортом: сессия уходит сама.

    Ручной `Cookie` handler отвергает — и правильно: он бы всё равно не дошёл, а
    запрос ушёл бы не от того, от кого просил вызывающий.
    """
    seen: list[str] = []

    async def remember(route: Route) -> None:
        seen.append(route.request.headers.get("cookie", ""))
        await route.fulfill(status=201, content_type="application/json", body=json.dumps({}))

    await page.route(API_URL, remember)
    await page.goto(PORTAL_URL)
    await page.evaluate("document.cookie = 'sid=abc123; path=/'")
    driver = PlaywrightDriver(page, capture=None)

    reply = await driver.fetch(PageRequest(method="GET", url=f"{PORTAL_ORIGIN}/api/companies"))

    assert reply.status == 201
    assert "sid=abc123" in seen[0]
