"""Deterministic teaching mail site used by every phase-56 example."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode

from eazy_sdk.crypto import CryptoContext, CryptoDirection, CryptoStage

from examples.mail.crypto import (
    BODY_CIPHER,
    FIELD_CIPHER,
    MAIL_ENCRYPTED_CONTENT_TYPE,
)

REJECTED_INPUT = "wrong"
VALID_LOGIN_INPUT = "correct"
VALID_OTP = "123456"
SESSION_START = datetime(2030, 1, 1, tzinfo=UTC)
MAIL_SIGNING_SECRET = b"mail-demo-secret"


@dataclass(frozen=True, slots=True)
class SiteRequest:
    method: str
    path: str
    query: tuple[tuple[str, str], ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""

    def header(self, name: str) -> str | None:
        wanted = name.casefold()
        return next((value for key, value in self.headers if key.casefold() == wanted), None)

    def json(self) -> dict[str, object]:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise ValueError("the teaching site accepts JSON objects")
        return value

    def form(self) -> dict[str, str]:
        return dict(parse_qsl(self.body.decode(), keep_blank_values=True))

    def query_value(self, name: str) -> str | None:
        return next((value for key, value in self.query if key == name), None)

    @property
    def is_json(self) -> bool:
        content_type = self.header("content-type") or ""
        return "application/json" in content_type or self.body.lstrip().startswith(b"{")


@dataclass(frozen=True, slots=True)
class SiteResponse:
    status: int
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""

    @classmethod
    def json(cls, status: int, document: object) -> SiteResponse:
        return cls(
            status,
            (("content-type", "application/json"),),
            json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(),
        )

    @classmethod
    def html(cls, status: int, document: str) -> SiteResponse:
        return cls(status, (("content-type", "text/html; charset=utf-8"),), document.encode())

    @classmethod
    def redirect(cls, location: str, *, cookie: str | None = None) -> SiteResponse:
        headers = [("location", location)]
        if cookie is not None:
            headers.append(("set-cookie", cookie))
        return cls(303, tuple(headers))


@dataclass(frozen=True, slots=True)
class Account:
    email: str
    password: str = VALID_LOGIN_INPUT
    otp: str = VALID_OTP
    blocked: bool = False
    captcha: bool = False


@dataclass(frozen=True, slots=True)
class MailMessage:
    id: int
    sender: str
    recipient: str
    subject: str

    def document(self) -> dict[str, object]:
        return {
            "id": self.id,
            "sender": self.sender,
            "recipient": self.recipient,
            "subject": self.subject,
        }


def _accounts() -> dict[str, Account]:
    return {
        "ada@mail.example": Account("ada@mail.example"),
        "blocked@mail.example": Account("blocked@mail.example", blocked=True),
        "captcha@mail.example": Account("captcha@mail.example", captcha=True),
    }


def _messages() -> list[MailMessage]:
    return [
        MailMessage(42, "ada@mail.example", "reader@mail.example", "Планы на пятницу"),
        MailMessage(43, "grace@mail.example", "reader@mail.example", "Сверка протокола"),
        MailMessage(44, "linus@mail.example", "reader@mail.example", "Встреча команды"),
        MailMessage(45, "margaret@mail.example", "reader@mail.example", "Отчёт за неделю"),
    ]


@dataclass(slots=True)
class SiteState:
    accounts: dict[str, Account] = field(default_factory=_accounts)
    messages: list[MailMessage] = field(default_factory=_messages)
    sent: list[MailMessage] = field(default_factory=list)
    sessions: dict[str, str] = field(default_factory=lambda: {"session-1": "ada@mail.example"})
    refresh_tokens: dict[str, str] = field(
        default_factory=lambda: {"refresh-1": "ada@mail.example"}
    )
    password_cookies: list[str | None] = field(default_factory=list)
    captcha_solves: list[str] = field(default_factory=list)
    refreshes: int = 0


@dataclass(slots=True)
class MailSite:
    """One in-memory site for HTTP handlers and intercepted browser pages."""

    state: SiteState = field(default_factory=SiteState)

    def __call__(self, request: SiteRequest) -> SiteResponse:
        handler = self._routes().get((request.method, request.path))
        if handler is None:
            return _failure(404, "not_found", "Route does not exist")
        return handler(request)

    def _routes(self) -> dict[tuple[str, str], Callable[[SiteRequest], SiteResponse]]:
        return {
            ("GET", "/messages/42"): self._quickstart_message,
            ("POST", "/login/identify"): self._identify,
            ("POST", "/login/password"): self._password,
            ("POST", "/login/captcha"): self._solve_captcha,
            ("GET", "/login/otp"): self._otp_page,
            ("POST", "/login/otp"): self._otp,
            ("GET", "/login/"): self._login_page,
            ("GET", "/inbox/"): self._inbox_page,
            ("GET", "/compose/"): self._compose_page,
            ("GET", "/api/v1/user/short"): self._short_user,
            ("GET", "/api/v1/client/fingerprint"): self._fingerprint,
            ("GET", "/api/v1/threads/status/smart"): self._threads,
            ("POST", "/api/v1/session/refresh"): self._refresh,
            ("POST", "/api/v1/messages/send"): self._send,
            ("GET", "/api/v1/messages/sent"): self._sent,
        }

    def _quickstart_message(self, _request: SiteRequest) -> SiteResponse:
        return SiteResponse.json(200, self.state.messages[0].document())

    def _identify(self, request: SiteRequest) -> SiteResponse:
        email = _field(request, "email")
        account = self.state.accounts.get(email)
        if account is None:
            return self._login_failure(request, "account_not_found", "Account does not exist")
        if account.blocked:
            return self._login_failure(request, "account_blocked", "Account is blocked")
        login_id = _login_id(email)
        if request.is_json:
            return _success({"login_id": login_id})
        return SiteResponse.html(200, _password_html(login_id))

    def _password(self, request: SiteRequest) -> SiteResponse:
        login_id = _field(request, "login_id")
        password = _field(request, "password")
        cookie = request.header("cookie")
        self.state.password_cookies.append(cookie)
        account = self.state.accounts.get(_email_from_login(login_id))
        if account is None:
            return self._login_failure(request, "account_not_found", "Account does not exist")
        if password != account.password:
            return self._login_failure(request, "wrong_password", "Password is incorrect")
        if account.captcha and "login_clearance=solved" not in (cookie or ""):
            if request.is_json:
                return SiteResponse.json(403, {"kind": "captcha", "site_key": "mail-login"})
            return SiteResponse.html(403, _captcha_html(login_id))
        if request.is_json:
            return _success({"login_id": login_id, "destination": "***-42"})
        return SiteResponse.redirect(f"/login/otp?{urlencode({'login_id': login_id})}")

    def _otp_page(self, request: SiteRequest) -> SiteResponse:
        login_id = request.query_value("login_id") or ""
        return SiteResponse.html(200, _otp_html(login_id))

    def _solve_captcha(self, request: SiteRequest) -> SiteResponse:
        login_id = _field(request, "login_id")
        if _email_from_login(login_id) not in self.state.accounts:
            return self._login_failure(request, "account_not_found", "Account does not exist")
        self.state.captcha_solves.append("mail-login")
        return SiteResponse.redirect(
            f"/login/otp?{urlencode({'login_id': login_id})}",
            cookie="login_clearance=solved; Path=/",
        )

    def _otp(self, request: SiteRequest) -> SiteResponse:
        login_id = _field(request, "login_id")
        code = _field(request, "code")
        account = self.state.accounts.get(_email_from_login(login_id))
        if account is None:
            return self._login_failure(request, "account_not_found", "Account does not exist")
        if code != account.otp:
            return self._login_failure(request, "wrong_code", "Code is incorrect")
        token = f"session-{account.email.split('@', maxsplit=1)[0]}"
        self.state.sessions[token] = account.email
        if request.is_json:
            return _success(
                {
                    "access_token": token,
                    "refresh_token": "refresh-1",
                    "expires_at": (SESSION_START + timedelta(minutes=5)).isoformat(),
                }
            )
        return SiteResponse.redirect("/inbox/", cookie=f"mail_session={token}; Path=/; HttpOnly")

    def _login_failure(self, request: SiteRequest, code: str, message: str) -> SiteResponse:
        if request.is_json:
            return _failure(200, code, message)
        return SiteResponse.html(200, _failure_html(code, message))

    def _login_page(self, _request: SiteRequest) -> SiteResponse:
        return SiteResponse.html(200, _identify_html())

    def _inbox_page(self, request: SiteRequest) -> SiteResponse:
        token = _cookie(request, "mail_session") or "session-1"
        return SiteResponse.html(
            200,
            _inbox_html(
                token,
                "refresh-1",
                SESSION_START + timedelta(minutes=5),
                self.state.messages,
            ),
        )

    def _compose_page(self, request: SiteRequest) -> SiteResponse:
        token = _cookie(request, "mail_session") or "session-1"
        return SiteResponse.html(200, _compose_html(token))

    def _short_user(self, request: SiteRequest) -> SiteResponse:
        email = self._authorized_email(request)
        if email is None:
            return _failure(401, "unauthorized", "Session is missing or expired")
        return _success({"email": email, "display_name": "Ada"})

    def _fingerprint(self, request: SiteRequest) -> SiteResponse:
        fingerprint = request.header("x-tls-fingerprint")
        if fingerprint != "browser-124":
            return _failure(403, "fingerprint_rejected", "Browser fingerprint is required")
        return _success({"fingerprint": fingerprint})

    def _threads(self, request: SiteRequest) -> SiteResponse:
        if self._authorized_email(request) is None:
            return _failure(401, "unauthorized", "Session is missing or expired")
        start = _page_start(request)
        limit = _positive_int(request.query_value("limit"), default=2)
        items = self.state.messages[start : start + limit]
        next_start = start + len(items)
        has_more = next_start < len(self.state.messages)
        body: dict[str, object] = {
            "items": [message.document() for message in items],
            "total": len(self.state.messages),
            "next_offset": next_start if has_more else None,
            "next_cursor": f"cursor-{next_start}" if has_more else None,
            "next_url": (
                f"/api/v1/threads/status/smart?offset={next_start}&limit={limit}"
                if has_more
                else None
            ),
        }
        return _success(body)

    def _refresh(self, request: SiteRequest) -> SiteResponse:
        refresh_token = str(request.json().get("refresh_token", ""))
        email = self.state.refresh_tokens.pop(refresh_token, None)
        if email is None:
            return _failure(401, "unauthorized", "Refresh token is missing or expired")
        self.state.refreshes += 1
        token = f"session-refresh-{self.state.refreshes}"
        next_refresh = f"refresh-{self.state.refreshes + 1}"
        self.state.sessions[token] = email
        self.state.refresh_tokens[next_refresh] = email
        return _success(
            {
                "access_token": token,
                "refresh_token": next_refresh,
                "expires_at": (
                    SESSION_START + timedelta(hours=self.state.refreshes, minutes=5)
                ).isoformat(),
            }
        )

    def _send(self, request: SiteRequest) -> SiteResponse:
        email = self._authorized_email(request)
        if email is None:
            return _failure(401, "unauthorized", "Session is missing or expired")
        signature = request.header("x-mail-signature") or ""
        if not hmac.compare_digest(signature, _mail_signature(request.body)):
            return _failure(403, "signature_rejected", "Signature is missing")
        encrypted = request.header("content-type") == MAIL_ENCRYPTED_CONTENT_TYPE
        body = _encrypted_mail_document(request) if encrypted else request.json()
        recipient = str(body.get("recipient", ""))
        subject = str(body.get("subject", ""))
        if recipient == "rejected@mail.example":
            response = _failure(200, "recipient_rejected", "Recipient was rejected")
            return _encrypt_mail_response(response) if encrypted else response
        if recipient == "quiet@mail.example":
            message = MailMessage(100 + len(self.state.sent), email, recipient, subject)
            self.state.sent.append(message)
            response = _success({"outcome": "silent"})
            return _encrypt_mail_response(response) if encrypted else response
        message = MailMessage(100 + len(self.state.sent), email, recipient, subject)
        self.state.sent.append(message)
        response = _success({"outcome": "sent", "message_id": message.id})
        return _encrypt_mail_response(response) if encrypted else response

    def _sent(self, request: SiteRequest) -> SiteResponse:
        recipient = request.query_value("recipient")
        subject = request.query_value("subject")
        matches = [
            message.document()
            for message in self.state.sent
            if (recipient is None or message.recipient == recipient)
            and (subject is None or message.subject == subject)
        ]
        return _success({"items": matches})

    def _authorized_email(self, request: SiteRequest) -> str | None:
        authorization = request.header("authorization") or ""
        token = authorization.removeprefix("Bearer ")
        return self.state.sessions.get(token)


def _field(request: SiteRequest, name: str) -> str:
    document = request.json() if request.is_json else request.form()
    return str(document.get(name, ""))


def _login_id(email: str) -> str:
    return f"login:{email}"


def _email_from_login(login_id: str) -> str:
    return login_id.removeprefix("login:")


def _cookie(request: SiteRequest, name: str) -> str | None:
    cookie = request.header("cookie") or ""
    for item in cookie.split(";"):
        key, _, value = item.strip().partition("=")
        if key == name:
            return value
    return None


def _positive_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _page_start(request: SiteRequest) -> int:
    cursor = request.query_value("cursor")
    if cursor is not None:
        return _positive_int(cursor.removeprefix("cursor-"), default=0)
    return _positive_int(request.query_value("offset"), default=0)


def _success(body: Mapping[str, object]) -> SiteResponse:
    return SiteResponse.json(200, {"status": "ok", "body": dict(body)})


def _mail_signature(body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest().encode()
    return hmac.new(MAIL_SIGNING_SECRET, digest, hashlib.sha256).hexdigest()


def _crypto_context(
    direction: CryptoDirection,
    *,
    algorithm: str = BODY_CIPHER.name,
    stage: CryptoStage = CryptoStage.ENCODED,
) -> CryptoContext:
    return CryptoContext(
        operation_id="teaching-mail-site",
        profile="mail-send-v1",
        algorithm=algorithm,
        direction=direction,
        stage=stage,
        attempt=1,
    )


def _encrypted_mail_document(request: SiteRequest) -> dict[str, object]:
    body_context = _crypto_context(CryptoDirection.INBOUND)
    document = json.loads(BODY_CIPHER.decrypt(request.body, context=body_context))
    if not isinstance(document, dict):
        raise ValueError("the teaching site accepts encrypted JSON objects")
    field_context = _crypto_context(
        CryptoDirection.INBOUND,
        algorithm=FIELD_CIPHER.name,
        stage=CryptoStage.DOCUMENT,
    )
    for field_name in ("subject", "body"):
        document[field_name] = FIELD_CIPHER.decrypt(
            document[field_name],
            context=field_context,
        )
    return document


def _encrypt_mail_response(response: SiteResponse) -> SiteResponse:
    context = _crypto_context(CryptoDirection.OUTBOUND)
    return SiteResponse(
        response.status,
        (("content-type", MAIL_ENCRYPTED_CONTENT_TYPE),),
        BODY_CIPHER.encrypt(response.body, context=context),
    )


def _failure(status: int, code: str, message: str) -> SiteResponse:
    return SiteResponse.json(
        status,
        {"status": code, "body": {"code": code, "message": message}},
    )


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>"
    )


_IDENTIFY_HTML = """
<!-- region docs: identify-html -->
<!-- examples/mail/site/app.py -->
<main data-page="identify">
  <form method="post" action="/login/identify">
    <label>Почта <input name="email" type="email"></label>
    <button type="submit">Продолжить</button>
  </form>
</main>
<!-- endregion docs: identify-html -->
"""


def _identify_html() -> str:
    return _page("Вход", _IDENTIFY_HTML)


def _password_html(login_id: str) -> str:
    return _page(
        "Пароль",
        '<main data-page="password"><form method="post" action="/login/password">'
        f'<input name="login_id" type="hidden" value="{html.escape(login_id)}">'
        '<label>Пароль <input name="password" type="password"></label>'
        '<button type="submit">Войти</button></form></main>',
    )


def _captcha_html(login_id: str) -> str:
    return _page(
        "Проверка",
        f'<main data-page="captcha" data-login-id="{html.escape(login_id)}">'
        '<div class="captcha" data-site-key="mail-login">Подтвердите вход</div>'
        '<form method="post" action="/login/captcha">'
        f'<input name="login_id" type="hidden" value="{html.escape(login_id)}">'
        '<button data-action="solve" type="submit">Я не робот</button>'
        "</form></main>",
    )


def _otp_html(login_id: str) -> str:
    return _page(
        "Код подтверждения",
        '<main data-page="otp"><form method="post" action="/login/otp">'
        f'<input name="login_id" type="hidden" value="{html.escape(login_id)}">'
        '<label>Код <input name="code" inputmode="numeric"></label>'
        '<button type="submit">Подтвердить</button></form></main>',
    )


def _failure_html(code: str, message: str) -> str:
    return _page(
        "Вход не выполнен",
        f'<main data-page="failure" data-code="{html.escape(code)}">'
        f'<p role="alert">{html.escape(message)}</p></main>',
    )


_INBOX_HTML_OPEN = """
<!-- region docs: inbox-html-open -->
<!-- examples/mail/site/app.py -->
<main data-page="mailbox">
  <ul data-collection="messages">
<!-- endregion docs: inbox-html-open -->
"""


def _inbox_html(
    token: str,
    refresh_token: str,
    expires_at: datetime,
    messages: list[MailMessage],
) -> str:
    rows = "".join(
        f'<li data-message-id="{message.id}"><b>{html.escape(message.sender)}</b> '
        f"{html.escape(message.subject)}</li>"
        for message in messages[:2]
    )
    return _page(
        "Входящие",
        f'<meta name="mail-token" content="{html.escape(token)}">'
        f'<meta name="mail-refresh" content="{html.escape(refresh_token)}">'
        f'<meta name="mail-expires" content="{expires_at.isoformat()}">'
        + _INBOX_HTML_OPEN
        + rows
        + '</ul><button data-action="more">Ещё</button></main>'
        """<script>
const token = document.querySelector('meta[name="mail-token"]').content;
const more = document.querySelector('[data-action="more"]');
more.addEventListener('click', async () => {
  const response = await fetch('/api/v1/threads/status/smart?offset=2&limit=2', {
    headers: {Authorization: `Bearer ${token}`},
  });
  const documentBody = await response.json();
  const collection = document.querySelector('[data-collection="messages"]');
  for (const message of documentBody.body.items) {
    const item = document.createElement('li');
    item.dataset.messageId = message.id;
    const sender = document.createElement('b');
    sender.textContent = message.sender;
    item.append(sender, ` ${message.subject}`);
    collection.append(item);
  }
  more.hidden = true;
});
</script>""",
    )


_COMPOSER_HTML = """
<!-- region docs: composer-html -->
<main data-page="compose">
  <form data-composer>
    <input name="recipient" type="email"><input name="subject">
    <textarea name="body"></textarea><button type="submit">Отправить</button>
  </form>
  <div role="status" hidden>Отправлено</div>
  <dialog data-outcome="rejected">Адрес отклонён</dialog>
</main>
<!-- endregion docs: composer-html -->
"""


def _compose_html(token: str) -> str:
    return _page(
        "Новое письмо",
        f'<meta name="mail-token" content="{html.escape(token)}">'
        + _COMPOSER_HTML
        + """<script>
const token = document.querySelector('meta[name="mail-token"]').content;
const composer = document.querySelector('[data-composer]');
const statusNode = document.querySelector('[role="status"]');
const rejected = document.querySelector('[data-outcome="rejected"]');
composer.addEventListener('submit', async (event) => {
  event.preventDefault();
  statusNode.hidden = true;
  if (rejected.open) rejected.close();
  const fields = new FormData(composer);
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const encode64 = (value) => {
    const bytes = encoder.encode(value);
    return btoa(String.fromCharCode(...bytes));
  };
  const decode64 = (value) => {
    const bytes = Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
    return decoder.decode(bytes);
  };
  const documentBody = Object.fromEntries(fields);
  documentBody.subject = `mail-field:${encode64(documentBody.subject)}`;
  documentBody.body = `mail-field:${encode64(documentBody.body)}`;
  const body = `mail-body:${encode64(JSON.stringify(documentBody))}`;
  const digestBytes = await crypto.subtle.digest('SHA-256', encoder.encode(body));
  const digest = Array.from(new Uint8Array(digestBytes))
    .map((value) => value.toString(16).padStart(2, '0')).join('');
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode('mail-demo-secret'), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']
  );
  const signatureBytes = await crypto.subtle.sign('HMAC', key, encoder.encode(digest));
  const signature = Array.from(new Uint8Array(signatureBytes))
    .map((value) => value.toString(16).padStart(2, '0')).join('');
  const response = await fetch('/api/v1/messages/send', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/vnd.mail.encrypted+json',
      Authorization: `Bearer ${token}`,
      'X-Mail-Signature': signature,
    },
    body,
  });
  const encryptedResponse = await response.text();
  const responseBody = JSON.parse(decode64(encryptedResponse.replace('mail-body:', '')));
  if (responseBody.status === 'recipient_rejected') {
    rejected.showModal();
  } else if (responseBody.body.outcome === 'sent') {
    statusNode.hidden = false;
  }
});
</script>""",
    )


__all__ = [
    "Account",
    "MailMessage",
    "MailSite",
    "SiteRequest",
    "SiteResponse",
    "SiteState",
]
