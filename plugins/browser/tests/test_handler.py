"""Браузер в роли транспорта `eazy-sdk` — без браузера.

Здесь проверяется перевод между `zapros.Request` и запросом из страницы: что уходит,
что отбрасывается и что отвергается вслух. Сам поход в Chromium — в
`test_playwright_handler.py`.
"""

from __future__ import annotations

import pytest
from eazy_sdk_browser.fetch import PageFetchError, PageReply, require_fetch
from eazy_sdk_browser.integrations.handler import BROWSER_HANDLER_PROFILE, BrowserHandler
from eazy_sdk_browser.testing import FakeDriver, FetchFakeDriver, FetchLiar
from zapros import URL, Request

from eazy_sdk.handlers.profile import CapabilityLevel, CapabilityMismatchError, RedirectControl

pytestmark = pytest.mark.unit

TARGET = "https://portal.example.test/api/companies"


def ask(headers: dict[str, str] | None = None, body: bytes | None = None) -> Request:
    return Request(URL(TARGET), "POST", headers or {}, body=body)


# --- доступ к возможности ---------------------------------------------------------------------


def test_fetch_requires_capability() -> None:
    """Драйверу, который так не умеет, отказывают до первого запроса."""
    with pytest.raises(CapabilityMismatchError):
        require_fetch(FakeDriver())


def test_fetch_liar_is_adapter_error() -> None:
    with pytest.raises(TypeError, match="fetch"):
        require_fetch(FetchLiar())


# --- что уходит в браузер ---------------------------------------------------------------------


async def test_request_reaches_the_page_as_declared() -> None:
    """Метод, адрес, тело и фрейм-источник доезжают без изменений."""
    driver = FetchFakeDriver(reply=PageReply(status=201, url=TARGET, body=b"{}"))
    handler = BrowserHandler(driver, frames=("iframe[name=app]",))

    await handler.ahandle(ask({"accept": "application/json"}, body=b'{"name": "x"}'))

    sent, frames = driver.sent[0]
    assert (sent.method, sent.url, sent.body) == ("POST", TARGET, b'{"name": "x"}')
    assert dict(sent.headers) == {"accept": "application/json"}
    assert frames == ("iframe[name=app]",)


async def test_browser_controlled_headers_are_dropped() -> None:
    """Заголовки, которыми распоряжается браузер, до `fetch` не доходят.

    Отдельно про `user-agent`: его `fetch` как раз **позволяет** переопределить, а
    `zapros` подставляет свой по умолчанию. Не отбрось его обработчик — запрос,
    взятый у браузера ради неотличимости, представился бы `python-zapros`.
    """
    driver = FetchFakeDriver()
    handler = BrowserHandler(driver)

    await handler.ahandle(
        ask(
            {
                "accept": "application/json",
                "host": "portal.example.test",
                "accept-encoding": "gzip",
                "sec-ch-ua": "подделка",
                "user-agent": "не-браузер/1.0",
            }
        )
    )

    sent, _ = driver.sent[0]
    assert dict(sent.headers) == {"accept": "application/json"}


async def test_manual_cookie_is_refused_not_ignored() -> None:
    """Тихая подмена кук хуже отказа: уйдут не те, о которых просил вызывающий."""
    handler = BrowserHandler(FetchFakeDriver())

    with pytest.raises(ValueError, match="Cookie"):
        await handler.ahandle(ask({"cookie": "sid=подделка"}))


async def test_streamed_body_is_buffered() -> None:
    """`fetch` не умеет тело потоком — обработчик собирает его целиком, а не роняет."""
    driver = FetchFakeDriver()

    await BrowserHandler(driver).ahandle(
        Request(URL(TARGET), "POST", {}, body=iter([b"aa", b"bb"]))
    )

    sent, _ = driver.sent[0]
    assert sent.body == b"aabb"


# --- что приходит обратно ----------------------------------------------------------------------


async def test_reply_drops_headers_about_packing() -> None:
    """Тело пришло распакованным — заголовки об упаковке стали бы враньём."""
    driver = FetchFakeDriver(
        reply=PageReply(
            status=200,
            url=TARGET,
            headers=(
                ("content-type", "application/json"),
                ("content-encoding", "gzip"),
                ("content-length", "17"),
            ),
            body=b'{"id": "c-1"}',
        )
    )

    response = await BrowserHandler(driver).ahandle(ask())

    assert response.status == 200
    names = {name.lower() for name in response.headers.keys()}  # noqa: SIM118 — Headers, не dict
    assert "content-type" in names
    assert "content-encoding" not in names


async def test_browser_refusal_is_not_an_http_answer() -> None:
    """Запрет CORS — не ответ со статусом, и притворяться ответом он не должен."""
    driver = FetchFakeDriver(refuse="TypeError: Failed to fetch")

    with pytest.raises(PageFetchError, match="Failed to fetch"):
        await BrowserHandler(driver).ahandle(ask())


# --- профиль говорит правду ---------------------------------------------------------------------


def test_profile_admits_what_the_browser_cannot_do() -> None:
    """Ограничения объявлены, а не выясняются посреди сценария."""
    assert BROWSER_HANDLER_PROFILE.header_order is CapabilityLevel.UNSUPPORTED
    assert BROWSER_HANDLER_PROFILE.header_casing is CapabilityLevel.UNSUPPORTED
    assert BROWSER_HANDLER_PROFILE.duplicate_headers is CapabilityLevel.UNSUPPORTED
    assert BROWSER_HANDLER_PROFILE.manual_cookie_field is CapabilityLevel.UNSUPPORTED
    assert BROWSER_HANDLER_PROFILE.redirects is RedirectControl.UNCONTROLLED


def test_profile_names_the_browser_it_speaks_through() -> None:
    """Личность транспорта — настоящий браузер, и обработчик называет какой."""
    handler = BrowserHandler(FetchFakeDriver())

    assert handler.profile.impersonation == "fake-fetch"
