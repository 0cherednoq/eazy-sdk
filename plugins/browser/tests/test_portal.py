"""Сквозной сценарий портала на фейковом драйвере: карты, исходы, отказы."""

import json

import pytest
from eazy_sdk_browser import AsyncBrowserClient, BrowserCallOptions, build_content
from eazy_sdk_browser.network import ResponseView
from eazy_sdk_browser.testing import FakeDriver, LyingDriver, NetworkFakeDriver

from eazy_sdk.handlers import CapabilityMismatchError
from eazy_sdk.response import MalformedResponseError, UnexpectedResponseError
from examples.browser_portal import (
    COMPANIES_API,
    CREATE_BUTTON,
    DIALOG_ROOT,
    ERROR_BANNER,
    FILTERS_ROOT,
    AccessDeniedError,
    CompaniesPage,
    CompaniesPortal,
    CompanyExistsError,
    CreateCompanyDialog,
    Created,
    DialogNotOpenedError,
    PortalBrokenError,
    SessionExpiredError,
)

FAST = BrowserCallOptions(timeout=0.01)
# Операция диалога объявляет область (`scope=`), поэтому её селекторы приходят
# драйверу с корнем диалога.
SUBMIT = f'{DIALOG_ROOT} button[type="submit"]'
FORM_FIELDS = {
    f'{DIALOG_ROOT} input[name="name"]',
    f'{DIALOG_ROOT} input[name="inn"]',
    SUBMIT,
}
JSON_HEADERS = (("content-type", "application/json"),)


def portal(
    status: int | None = None,
    payload: object = None,
    *,
    headers: tuple[tuple[str, str], ...] = JSON_HEADERS,
    body: bytes | None = None,
) -> NetworkFakeDriver:
    """Портал с открывающимся диалогом и заданным ответом API."""
    replies: list[ResponseView] = []
    if status is not None:
        replies.append(
            ResponseView(
                url=f"https://portal.example.com{COMPANIES_API}",
                status=status,
                body=json.dumps(payload).encode() if body is None else body,
                headers=headers,
            )
        )
    return NetworkFakeDriver(
        present={CREATE_BUTTON, DIALOG_ROOT, FILTERS_ROOT, *FORM_FIELDS},
        # Ответ приходит на клик по кнопке формы, как в браузере, — а не лежит в
        # буфере заранее.
        answers={SUBMIT: replies},
    )


def portal_of(driver: FakeDriver) -> CompaniesPortal:
    return CompaniesPortal(AsyncBrowserClient(driver))


async def open_dialog(driver: NetworkFakeDriver) -> CreateCompanyDialog:
    return await portal_of(driver).open_create_company(options=FAST)


@pytest.mark.unit
async def test_open_dialog_gives_state_with_submit() -> None:
    """Кнопка на странице, форма — в состоянии: карта списка про форму не знает."""
    driver = portal()

    dialog = await open_dialog(driver)

    assert isinstance(dialog, CreateCompanyDialog)
    assert isinstance(dialog.api, CompaniesPortal)
    assert f"click {CREATE_BUTTON}" in driver.log


@pytest.mark.unit
async def test_dialog_that_never_opens_is_a_failure_not_an_outcome() -> None:
    driver = NetworkFakeDriver(present={CREATE_BUTTON, FILTERS_ROOT})

    with pytest.raises(DialogNotOpenedError):
        await portal_of(driver).open_create_company(options=FAST)


@pytest.mark.unit
async def test_success_status_is_parsed_into_the_declared_model() -> None:
    """201 объявлен моделью успеха — исход берёт из неё поля, а не разбирает словарь."""
    driver = portal(201, {"id": "c-42", "name": "ООО Ромашка"})

    created = await (await open_dialog(driver)).submit(name="ООО Ромашка", inn="7701234567")

    assert created == Created(company_id="c-42", name="ООО Ромашка")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param(409, CompanyExistsError, id="409-conflict"),
        pytest.param(403, AccessDeniedError, id="403-forbidden"),
        pytest.param(503, PortalBrokenError, id="503-range"),
    ],
)
async def test_status_code_selects_the_declared_exception(
    status: int, expected: type[Exception]
) -> None:
    """Статус выбирает случай, а тело отказа приходит разобранной моделью."""
    driver = portal(status, {"code": "company_exists", "message": "ИНН уже заведён"})

    dialog = await open_dialog(driver)

    with pytest.raises(expected) as raised:
        await dialog.submit(name="ООО Ромашка", inn="7701234567")

    assert "ИНН уже заведён" in str(raised.value)


@pytest.mark.unit
async def test_undeclared_status_is_not_silently_accepted() -> None:
    """302 на страницу входа не подходит ни под один случай — это отдельная ошибка."""
    driver = portal(302, None, headers=(("content-type", "text/html"),), body=b"<html>login</html>")

    dialog = await open_dialog(driver)

    with pytest.raises(UnexpectedResponseError):
        await dialog.submit(name="ООО Ромашка", inn="7701234567")


@pytest.mark.unit
async def test_declared_status_with_broken_body_is_malformed() -> None:
    """201 объявлен, но тело не разобралось моделью: это не успех и не отказ портала."""
    driver = portal(201, None, body=b"{oops")

    dialog = await open_dialog(driver)

    with pytest.raises(MalformedResponseError):
        await dialog.submit(name="ООО Ромашка", inn="7701234567")


@pytest.mark.unit
async def test_page_level_rule_applies_to_every_operation() -> None:
    """Страничное правило объявлено один раз на портал — операция его не повторяет."""
    driver = portal()
    driver.text = "Недостаточно прав для создания компании"
    driver.present.add(ERROR_BANNER)

    with pytest.raises(AccessDeniedError) as raised:
        await portal_of(driver).open_create_company(options=FAST)

    assert raised.value.detail == f"текст {ERROR_BANNER}"


@pytest.mark.unit
async def test_session_expiry_is_recognised_by_url_or_text() -> None:
    """Признаки комбинируются оператором — портал выдаёт себя то одним, то другим."""
    by_url = portal()
    by_url.url = "https://portal.example.com/login?next=/companies"
    by_text = portal()
    by_text.text = "Войдите заново"

    for driver in (by_url, by_text):
        with pytest.raises(SessionExpiredError):
            await portal_of(driver).open_create_company(options=FAST)


@pytest.mark.unit
async def test_driver_without_network_fails_before_acting() -> None:
    """Драйвер, не умеющий читать сеть, отсекается объявленным требованием до действия."""
    driver = FakeDriver(present={CREATE_BUTTON, DIALOG_ROOT, FILTERS_ROOT, *FORM_FIELDS})

    with pytest.raises(CapabilityMismatchError) as raised:
        await portal_of(driver).submit_company(name="ООО Ромашка", inn="7701234567")

    assert raised.value.dimensions == ("network",)
    assert driver.log == []


@pytest.mark.unit
async def test_profile_that_lies_is_an_adapter_error() -> None:
    """Профиль обещал сеть, а метода нет — виноват адаптер, и сказать надо прямо."""
    driver = LyingDriver(present={CREATE_BUTTON, DIALOG_ROOT, FILTERS_ROOT, *FORM_FIELDS})

    with pytest.raises(TypeError, match="wait_response"):
        await portal_of(driver).submit_company(name="ООО Ромашка", inn="7701234567")


@pytest.mark.unit
async def test_list_is_opened_by_address_and_a_login_redirect_is_session_expiry() -> None:
    """Переход — операция портала: адрес от `base_url`, увод на вход — правило портала."""
    base = "https://portal.example.com"
    opened = portal()
    expired = FakeDriver(redirects={f"{base}/companies": f"{base}/login"})

    await CompaniesPortal(AsyncBrowserClient(opened, base_url=base)).open_companies(options=FAST)
    with pytest.raises(SessionExpiredError):
        await CompaniesPortal(AsyncBrowserClient(expired, base_url=base)).open_companies()

    assert opened.log == [f"goto {base}/companies"]


@pytest.mark.unit
async def test_region_scopes_lookups_to_its_root() -> None:
    """Подкарта ищет свои элементы внутри корня, а не по всей странице."""
    driver = portal()
    driver.present.add(FILTERS_ROOT + ' input[name="q"]')

    page = build_content(CompaniesPage, driver)
    await page.filters.query.fill("ромашка")

    assert FILTERS_ROOT + ' input[name="q"] = ромашка' in driver.log[0]
