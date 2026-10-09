"""Phase 58.6: a redirect written into a page is followed like a ``3xx``, cookies and all.

The scenario is the last leg of a webmail login. The mailbox answers ``200`` with a stub that
sends the browser to the auth host, the auth host sets the cookie that activates the session and
redirects back, and only then does the mailbox open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

import httpx
import pytest
from eazy_sdk_html import CSS

from eazy_sdk import Client, ClientConfig, Http, HttpOperation, Resilience, SyncApi, op
from eazy_sdk.clients import CallOptions
from eazy_sdk.clients.base import RedirectLimitError
from eazy_sdk.cookies import Cookies
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import Const, NormalizedResponse, Text, UnexpectedResponseError
from eazy_sdk.response.refresh import MAX_REFRESH_DELAY, meta_refresh_target

MAIL = "https://e.mail.example"
AUTH = "https://auth.mail.example"

STUB = (
    '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Redirecting</title>'
    '<meta http-equiv="refresh" content="0;URL=\''
    + AUTH
    + "/sdc?from=https%3A%2F%2Fe.mail.example%2Finbox'\"/>"
    "</head></html>"
)
MAIL_PAGE = "<html><head><title>Mail</title></head><body>Inbox</body></html>"


def _page(
    body: str, *, url: str = f"{MAIL}/inbox", media: str = "text/html"
) -> NormalizedResponse[object]:
    return NormalizedResponse(200, url, "GET", (("Content-Type", media),), body.encode())


def _meta(content: str) -> str:
    return f'<html><head><meta http-equiv="refresh" content="{content}"></head></html>'


# --- reading where a page points --------------------------------------------------------------


def test_the_stub_of_the_motivating_site_points_at_the_auth_host() -> None:
    assert meta_refresh_target(_page(STUB)) == (
        f"{AUTH}/sdc?from=https%3A%2F%2Fe.mail.example%2Finbox"
    )


@pytest.mark.parametrize(
    ("content", "target"),
    [
        ("0;url=/next", f"{MAIL}/next"),
        ("0; URL=/next", f"{MAIL}/next"),
        ("0;URL='/next'", f"{MAIL}/next"),
        ("0, url=next", f"{MAIL}/next"),
        ("3;url=https://other.example/a?b=1&amp;c=2", "https://other.example/a?b=1&c=2"),
        ("0.5;url=/next", f"{MAIL}/next"),
    ],
)
def test_the_address_is_read_in_every_spelling_browsers_accept(content: str, target: str) -> None:
    assert meta_refresh_target(_page(_meta(content))) == target


def test_the_attributes_come_in_any_order_and_any_case() -> None:
    page = "<META CONTENT='0;url=/next' HTTP-EQUIV='Refresh'>"
    assert meta_refresh_target(_page(page)) == f"{MAIL}/next"
    assert meta_refresh_target(_page("<meta http-equiv=refresh content=0;url=/next>")) == (
        f"{MAIL}/next"
    )


@pytest.mark.parametrize(
    "page",
    [
        MAIL_PAGE,
        _meta("0"),
        _meta("30;url=/later"),
        _meta(f"{MAX_REFRESH_DELAY + 1};url=/later"),
        _meta("0;url=/inbox"),
        '<meta http-equiv="content-type" content="0;url=/next">',
        f"<noscript>{_meta('0;url=/no-script')}</noscript>",
        f"<!-- {_meta('0;url=/commented')} -->",
        '<meta http-equiv="refresh">',
    ],
    ids=[
        "no-meta",
        "reload-without-address",
        "long-delay",
        "just-over-the-limit",
        "points-at-itself",
        "another-http-equiv",
        "inside-noscript",
        "inside-a-comment",
        "no-content",
    ],
)
def test_a_page_that_sends_the_client_nowhere(page: str) -> None:
    assert meta_refresh_target(_page(page)) is None


def test_only_an_html_page_is_read() -> None:
    assert meta_refresh_target(_page(_meta("0;url=/next"), media="application/json")) is None
    assert meta_refresh_target(_page(_meta("0;url=/next"), media="application/xhtml+xml")) == (
        f"{MAIL}/next"
    )


# --- followed by the client -------------------------------------------------------------------


@dataclass(slots=True)
class MailSite:
    """A stub, a cookie set on the way, and a mailbox that opens only with that cookie."""

    seen: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        cookie = request.headers.get("cookie", "")
        self.seen.append((f"{request.method} {host}{path}", cookie))
        html = {"content-type": "text/html; charset=utf-8"}
        if path == "/sdc":
            return httpx.Response(
                302,
                headers=[
                    ("location", f"{MAIL}/inbox"),
                    ("set-cookie", "sdcs=active; Domain=mail.example; Path=/"),
                ],
            )
        if path == "/loop":
            step = int(request.url.params.get("n", "0")) + 1
            return httpx.Response(
                200, headers=html, content=_meta(f"0;url=/loop?n={step}").encode()
            )
        if "sdcs=active" in cookie:
            return httpx.Response(200, headers=html, content=MAIL_PAGE.encode())
        return httpx.Response(200, headers=html, content=STUB.encode())


@dataclass(frozen=True, slots=True)
class Mailbox:
    title: Annotated[str, CSS("title::text"), Const("Mail")]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenInbox(HttpOperation[Mailbox]):
    __http__ = Http.get("/inbox")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenAnyPage(HttpOperation[str]):
    """Accepts whatever comes back, the stub included."""

    __http__ = Http.get("/inbox", success={200: Text(media_type=None)})


@dataclass(frozen=True, slots=True, kw_only=True)
class Loop(HttpOperation[Mailbox]):
    __http__ = Http.get("/loop")


class MailApi(SyncApi):
    cookies = Cookies()

    inbox = op(OpenInbox)
    any_page = op(OpenAnyPage)
    loop = op(Loop)


def _client(site: MailSite, resilience: Resilience) -> Client:
    raw = httpx.Client(transport=httpx.MockTransport(site), headers={}, cookies={})
    return Client(
        base_url=MAIL,
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(resilience=resilience),
    )


def test_a_stub_is_followed_and_the_cookie_set_on_the_way_opens_the_mailbox() -> None:
    site = MailSite()
    with _client(site, Resilience(max_redirects=3, client_redirects=True)) as client:
        assert MailApi(client).inbox() == Mailbox("Mail")

    assert [call for call, _ in site.seen] == [
        "GET e.mail.example/inbox",
        "GET auth.mail.example/sdc",
        "GET e.mail.example/inbox",
    ]
    assert site.seen[-1][1] == "sdcs=active"


def test_without_the_option_the_stub_is_just_a_page_nobody_described() -> None:
    site = MailSite()
    with (
        _client(site, Resilience(max_redirects=3)) as client,
        pytest.raises(UnexpectedResponseError),
    ):
        MailApi(client).inbox()
    assert len(site.seen) == 1


def test_a_page_the_operation_accepts_is_its_result_and_is_not_followed() -> None:
    site = MailSite()
    with _client(site, Resilience(max_redirects=3, client_redirects=True)) as client:
        assert "Redirecting" in MailApi(client).any_page()
    assert len(site.seen) == 1


def test_a_page_redirect_spends_the_same_budget_as_a_status_redirect() -> None:
    site = MailSite()
    with (
        _client(site, Resilience(max_redirects=1, client_redirects=True)) as client,
        pytest.raises(RedirectLimitError),
    ):
        MailApi(client).inbox()
    assert [call for call, _ in site.seen] == [
        "GET e.mail.example/inbox",
        "GET auth.mail.example/sdc",
    ]


def test_a_page_that_keeps_redirecting_runs_out_of_budget() -> None:
    site = MailSite()
    with (
        _client(site, Resilience(max_redirects=2, client_redirects=True)) as client,
        pytest.raises(RedirectLimitError),
    ):
        MailApi(client).loop()
    assert len(site.seen) == 3


def test_the_option_is_set_for_one_call_too() -> None:
    site = MailSite()
    with _client(site, Resilience()) as client:
        operation = MailApi(client).inbox
        result = operation.send(
            operation.request(),
            options=CallOptions(max_attempts=4, max_redirects=3, client_redirects=True),
        )
    assert result == Mailbox("Mail")


# --- a wrong setting is refused where it is written -------------------------------------------


def test_following_page_redirects_needs_a_redirect_budget() -> None:
    with pytest.raises(ValueError, match="follows nothing with max_redirects=0"):
        Resilience(client_redirects=True)
    with pytest.raises(ValueError, match="follows nothing with max_redirects=0"):
        CallOptions(client_redirects=True)
