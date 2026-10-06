"""Executable probes that de-risk the phase-56 documentation examples."""

import json
from dataclasses import dataclass, field

import httpx
import pytest

from examples.mail.browser.login_probe import Identify, LoginPortal, Mailbox, OtpStep, PasswordStep
from examples.mail.http.captcha_probe import main as captcha_main
from examples.mail.http.login_probe import main
from examples.mail.site import MailSite, handle_httpx, intercept_page


def test_http_login_probe_prints_all_six_outcomes(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "wrong password: WrongPassword",
        "account not found: AccountNotFound",
        "account blocked: AccountBlocked",
        "second factor: OtpStep",
        "wrong code: WrongCode",
        "captcha: CaptchaRequired",
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
        assert [item["id"] for item in cursor_page["items"]] == [44, 45]
        assert next_url_page["items"] == cursor_page["items"]

        refreshed = client.post("/api/v1/session/refresh", headers=auth).json()
        assert refreshed["body"]["access_token"] == "session-refresh-1"

        send_headers = {**auth, "X-Mail-Signature": "mail-signature"}
        sent = client.post(
            "/api/v1/messages/send",
            headers=send_headers,
            json={"recipient": "grace@mail.example", "subject": "Проверка"},
        ).json()
        rejected = client.post(
            "/api/v1/messages/send",
            headers=send_headers,
            json={"recipient": "rejected@mail.example", "subject": "Проверка"},
        ).json()
        silent = client.post(
            "/api/v1/messages/send",
            headers=send_headers,
            json={"recipient": "quiet@mail.example", "subject": "Проверка"},
        ).json()
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

    _, _, password = await visit(
        "POST",
        "https://mail.example/login/identify",
        body=b"email=ada%40mail.example",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert b'data-page="password"' in password
    assert b"login%3Aada" not in password

    status, headers, _ = await visit(
        "POST",
        "https://mail.example/login/password",
        body=b"login_id=login%3Aada%40mail.example&password=correct",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert status == 303
    assert headers["location"] == "/login/otp?login_id=login%3Aada%40mail.example"

    _, _, otp = await visit(
        "GET",
        f"https://mail.example{headers['location']}",
    )
    assert b'data-page="otp"' in otp

    status, headers, _ = await visit(
        "POST",
        "https://mail.example/login/otp",
        body=b"login_id=login%3Aada%40mail.example&code=123456",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert status == 303
    assert headers["location"] == "/inbox/"
    session_cookie = headers["set-cookie"].split(";", maxsplit=1)[0]

    _, _, inbox = await visit(
        "GET",
        "https://mail.example/inbox/",
        headers={"cookie": session_cookie},
    )
    _, _, composer = await visit("GET", "https://mail.example/compose/")
    assert b'data-page="mailbox"' in inbox
    assert b'data-collection="messages"' in inbox
    assert b"data-composer" in composer
    assert b'data-outcome="rejected"' in composer
