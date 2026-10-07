"""Executable probes that de-risk the phase-56 documentation examples."""

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import pytest
from eazy_sdk_browser import BrowserCallOptions, Failure, Observation, enforce
from eazy_sdk_browser.testing import FakeDriver

from examples.mail.browser.captcha import main as browser_captcha_main
from examples.mail.browser.login_failures import main as browser_login_main
from examples.mail.browser.login_probe import (
    ACCOUNT_FAILURES,
    OTP_FAILURES,
    PASSWORD_FAILURES,
    VALID_LOGIN_INPUT,
    AccountBlockedError,
    AccountNotFoundError,
    CaptchaRequiredError,
    Identify,
    LoginPortal,
    Mailbox,
    OtpStep,
    PasswordStep,
    WrongCodeError,
    WrongPasswordError,
)
from examples.mail.browser.messages import main as browser_messages_main
from examples.mail.browser.send import main as browser_send_main
from examples.mail.browser.session import main as browser_session_main
from examples.mail.http.captcha import main as captcha_main
from examples.mail.http.login_failures import main as http_login_main
from examples.mail.http.messages import main as http_messages_main
from examples.mail.http.send import main as http_send_main
from examples.mail.http.session import main as http_session_main
from examples.mail.site import MailSite, handle_httpx, intercept_page
from examples.mail.site._playwright import playwright_mail


def test_http_login_probe_prints_all_six_outcomes(capsys: pytest.CaptureFixture[str]) -> None:
    http_login_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "wrong password: WrongPassword",
        "account not found: AccountNotFound",
        "account blocked: AccountBlocked",
        "second factor: OtpStep",
        "wrong code: WrongCode",
        "captcha: CaptchaRequired",
    ]


def test_browser_login_probe_prints_all_six_executed_outcomes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_login_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "wrong password: WrongPasswordError",
        "account not found: AccountNotFoundError",
        "account blocked: AccountBlockedError",
        "second factor: OtpStep",
        "wrong code: WrongCodeError",
        "captcha: CaptchaRequiredError",
    ]


def test_http_captcha_probe_replays_the_password_step(capsys: pytest.CaptureFixture[str]) -> None:
    captcha_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "challenge solved: mail-login",
        "password requests: 2",
        "replay cookie: login_clearance=solved",
        "next step: OtpStep",
    ]


def test_browser_captcha_probe_continues_the_password_operation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_captcha_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "challenge solved: mail-login",
        "password submits: 1",
        "same operation: OtpStep",
    ]


def test_http_session_probe_refreshes_by_expiry_and_rejection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    http_session_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "HTML session: ada@mail.example via session-1",
        "expiry refresh: session-refresh-1",
        "401 refresh: session-refresh-2",
        "refresh requests: 2",
    ]


def test_browser_session_probe_logs_in_once_for_two_tabs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_session_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "browser logins: 1",
        "tabs in context: 2",
        "session cookie: session-ada",
    ]


def test_http_messages_probe_runs_all_three_pagination_strategies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    http_messages_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "offset: [42, 43, 44, 45]",
        "cursor: [42, 43, 44, 45]",
        "next URL: [42, 43, 44, 45]",
    ]


def test_browser_messages_probe_loads_the_second_batch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_messages_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "first batch: [42, 43]",
        "after more: [42, 43, 44, 45]",
    ]


def test_http_send_probe_signs_and_handles_all_three_outcomes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    http_send_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "sent: 100",
        "rejected: RecipientRejected",
        "silent response: silent",
        "found in Sent: 101",
    ]


def test_browser_send_probe_distinguishes_all_three_outcomes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_send_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "sent: Sent",
        "rejected: Rejected",
        "silent: Silent",
    ]


def test_browser_login_probe_declares_three_typed_steps() -> None:
    assert LoginPortal.identify.operation is Identify
    identify_outcomes = Identify.__browser__.outcomes
    password_outcomes = LoginPortal.password.operation.__browser__.outcomes
    otp_outcomes = LoginPortal.otp.operation.__browser__.outcomes
    assert identify_outcomes is not None
    assert password_outcomes is not None
    assert otp_outcomes is not None
    assert identify_outcomes.cases[0].to is PasswordStep
    assert password_outcomes.cases[0].to is OtpStep
    assert otp_outcomes.cases[0].to is Mailbox


@pytest.mark.asyncio
async def test_browser_login_probe_executes_the_three_typed_steps() -> None:
    options = BrowserCallOptions(timeout=5.0)
    async with playwright_mail() as runtime:
        portal = LoginPortal(runtime.client)
        password = await portal.identify(email="ada@mail.example", options=options)
        otp = await password.submit(password=VALID_LOGIN_INPUT)
        mailbox = await otp.submit(code="123456")

    assert isinstance(password, PasswordStep)
    assert isinstance(otp, OtpStep)
    assert isinstance(mailbox, Mailbox)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selector", "rules", "error"),
    [
        ('[data-code="wrong_password"]', PASSWORD_FAILURES, WrongPasswordError),
        ('[data-code="account_not_found"]', ACCOUNT_FAILURES, AccountNotFoundError),
        ('[data-code="account_blocked"]', ACCOUNT_FAILURES, AccountBlockedError),
        ('[data-code="wrong_code"]', OTP_FAILURES, WrongCodeError),
        ('[data-page="captcha"]', PASSWORD_FAILURES, CaptchaRequiredError),
    ],
)
async def test_browser_login_failures_match_their_page_markers(
    selector: str,
    rules: tuple[Failure, ...],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        await enforce(rules, Observation(FakeDriver(present={selector})))


@dataclass(slots=True)
class _PageRequest:
    method: str
    url: str
    headers: dict[str, str]
    post_data_buffer: bytes | None


@dataclass(slots=True)
class _PageRoute:
    request: _PageRequest
    response: tuple[int, dict[str, str], bytes] | None = field(default=None, init=False)

    async def fulfill(self, *, status: int, headers: dict[str, str], body: bytes) -> None:
        self.response = (status, headers, body)


@pytest.mark.asyncio
async def test_one_teaching_site_serves_http_and_page_interception() -> None:
    body = json.dumps({"email": "ada@mail.example"}).encode()
    http_response = handle_httpx(
        httpx.Request("POST", "https://mail.example/login/identify", content=body)
    )
    route = _PageRoute(
        _PageRequest(
            method="POST",
            url="https://mail.example/login/identify",
            headers={"content-type": "application/json"},
            post_data_buffer=body,
        )
    )

    await intercept_page(route)

    assert route.response is not None
    status, headers, intercepted_body = route.response
    assert status == http_response.status_code == 200
    assert headers["content-type"] == http_response.headers["content-type"]
    assert intercepted_body == http_response.content


def test_complete_teaching_site_serves_the_http_tutorial() -> None:
    site = MailSite()
    transport = httpx.MockTransport(lambda request: handle_httpx(request, site))
    with httpx.Client(base_url="https://mail.example", transport=transport) as client:
        identified = client.post("/login/identify", json={"email": "ada@mail.example"}).json()
        login_id = identified["body"]["login_id"]
        assert identified["status"] == "ok"

        password = client.post(
            "/login/password",
            json={"login_id": login_id, "password": "correct"},
        ).json()
        assert password["body"]["destination"] == "***-42"

        session = client.post(
            "/login/otp",
            json={"login_id": login_id, "code": "123456"},
        ).json()
        token = session["body"]["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        inbox = client.get("/inbox/", headers={"Cookie": f"mail_session={token}"})
        assert f'content="{token}"' in inbox.text
        assert 'data-page="mailbox"' in inbox.text

        user = client.get("/api/v1/user/short", headers=auth).json()
        assert user["body"]["email"] == "ada@mail.example"

        rejected_fingerprint = client.get("/api/v1/client/fingerprint").json()
        accepted_fingerprint = client.get(
            "/api/v1/client/fingerprint",
            headers={"X-TLS-Fingerprint": "browser-124"},
        ).json()
        assert rejected_fingerprint["status"] == "fingerprint_rejected"
        assert accepted_fingerprint["body"]["fingerprint"] == "browser-124"

        offset_page = client.get(
            "/api/v1/threads/status/smart?offset=0&limit=2",
            headers=auth,
        ).json()["body"]
        cursor_page = client.get(
            f"/api/v1/threads/status/smart?cursor={offset_page['next_cursor']}&limit=2",
            headers=auth,
        ).json()["body"]
        next_url_page = client.get(offset_page["next_url"], headers=auth).json()["body"]
        assert [item["id"] for item in offset_page["items"]] == [42, 43]
        assert offset_page["total"] == 4
        assert [item["id"] for item in cursor_page["items"]] == [44, 45]
        assert next_url_page["items"] == cursor_page["items"]

        refreshed = client.post(
            "/api/v1/session/refresh",
            json={"refresh_token": session["body"]["refresh_token"]},
        ).json()
        assert refreshed["body"]["access_token"] == "session-refresh-1"
        assert refreshed["body"]["refresh_token"] == "refresh-2"

        def signed_post(message: dict[str, str]) -> dict[str, Any]:
            request = client.build_request(
                "POST",
                "/api/v1/messages/send",
                headers=auth,
                json=message,
            )
            digest = hashlib.sha256(request.content).hexdigest().encode()
            request.headers["X-Mail-Signature"] = hmac.new(
                b"mail-demo-secret",
                digest,
                hashlib.sha256,
            ).hexdigest()
            return cast("dict[str, Any]", client.send(request).json())

        sent = signed_post({"recipient": "grace@mail.example", "subject": "Проверка"})
        rejected = signed_post(
            {"recipient": "rejected@mail.example", "subject": "Проверка"}
        )
        silent = signed_post({"recipient": "quiet@mail.example", "subject": "Проверка"})
        found = client.get(
            "/api/v1/messages/sent?recipient=grace%40mail.example&subject=%D0%9F%D1%80%D0%BE%D0%B2%D0%B5%D1%80%D0%BA%D0%B0"
        ).json()
        assert sent["body"]["outcome"] == "sent"
        assert rejected["status"] == "recipient_rejected"
        assert silent["body"]["outcome"] == "silent"
        assert [item["id"] for item in found["body"]["items"]] == [100]


@pytest.mark.asyncio
async def test_complete_teaching_site_serves_the_browser_tutorial() -> None:
    site = MailSite()

    async def visit(
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        route = _PageRoute(
            _PageRequest(method, url, headers or {}, body),
        )
        await intercept_page(route, site)
        assert route.response is not None
        return route.response

    status, _, identify = await visit("GET", "https://mail.example/login/")
    assert status == 200
    assert b'name="email"' in identify

    form_headers = {"content-type": "application/x-www-form-urlencoded"}
    _, _, missing = await visit(
        "POST",
        "https://mail.example/login/identify",
        body=b"email=missing%40mail.example",
        headers=form_headers,
    )
    _, _, blocked = await visit(
        "POST",
        "https://mail.example/login/identify",
        body=b"email=blocked%40mail.example",
        headers=form_headers,
    )
    _, _, wrong_password = await visit(
        "POST",
        "https://mail.example/login/password",
        body=b"login_id=login%3Aada%40mail.example&password=wrong",
        headers=form_headers,
    )
    _, _, wrong_code = await visit(
        "POST",
        "https://mail.example/login/otp",
        body=b"login_id=login%3Aada%40mail.example&code=000000",
        headers=form_headers,
    )
    _, _, captcha = await visit(
        "POST",
        "https://mail.example/login/password",
        body=b"login_id=login%3Acaptcha%40mail.example&password=correct",
        headers=form_headers,
    )
    assert b'data-code="account_not_found"' in missing
    assert b'data-code="account_blocked"' in blocked
    assert b'data-code="wrong_password"' in wrong_password
    assert b'data-code="wrong_code"' in wrong_code
    assert b'data-page="captcha"' in captcha

    _, _, password = await visit(
        "POST",
        "https://mail.example/login/identify",
        body=b"email=ada%40mail.example",
        headers=form_headers,
    )
    assert b'data-page="password"' in password
    assert b"login%3Aada" not in password

    status, _, otp = await visit(
        "POST",
        "https://mail.example/login/password",
        body=b"login_id=login%3Aada%40mail.example&password=correct",
        headers=form_headers,
    )
    assert status == 200
    assert b'data-page="otp"' in otp
    assert b"history.replaceState" in otp

    status, headers, inbox = await visit(
        "POST",
        "https://mail.example/login/otp",
        body=b"login_id=login%3Aada%40mail.example&code=123456",
        headers=form_headers,
    )
    assert status == 200
    session_cookie = headers["set-cookie"].split(";", maxsplit=1)[0]
    _, _, composer = await visit(
        "GET",
        "https://mail.example/compose/",
        headers={"cookie": session_cookie},
    )
    assert b'data-page="mailbox"' in inbox
    assert b'data-collection="messages"' in inbox
    assert b"/api/v1/threads/status/smart?offset=2&amp;limit=2" not in inbox
    assert b"/api/v1/threads/status/smart?offset=2&limit=2" in inbox
    assert b"data-composer" in composer
    assert b'data-outcome="rejected"' in composer
    assert session_cookie.removeprefix("mail_session=").encode() in composer
    assert b"/api/v1/messages/send" in composer
