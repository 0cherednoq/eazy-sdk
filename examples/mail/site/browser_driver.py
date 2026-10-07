"""Small browser-driver adapter that executes login examples against ``MailSite``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit

from eazy_sdk_browser.testing import FakeDriver

from .app import MailSite, SiteRequest, SiteResponse

SUBMIT = 'button[type="submit"]'


@dataclass
class TeachingLoginDriver(FakeDriver):
    """Drive the public browser operations through the real teaching-site responses."""

    site: MailSite = field(default_factory=MailSite)
    step: str = "identify"
    login_id: str = ""
    cookie: str = ""

    def __post_init__(self) -> None:
        self.url = "https://mail.example/login/"
        self.present = {'input[name="email"]', SUBMIT}
        self.text = "Вход"

    def on_click(self, selector: str) -> None:
        if selector != SUBMIT:
            return
        if self.step == "identify":
            email = self.values.get('input[name="email"]', "")
            self.login_id = f"login:{email}"
            self._send("POST", "/login/identify", {"email": email})
        elif self.step == "password":
            self._send(
                "POST",
                "/login/password",
                {
                    "login_id": self.login_id,
                    "password": self.values.get('input[name="password"]', ""),
                },
            )
        elif self.step == "otp":
            self._send(
                "POST",
                "/login/otp",
                {
                    "login_id": self.login_id,
                    "code": self.values.get('input[name="code"]', ""),
                },
            )

    def _send(self, method: str, path: str, form: dict[str, str]) -> None:
        headers = [("content-type", "application/x-www-form-urlencoded")]
        if self.cookie:
            headers.append(("cookie", self.cookie))
        response = self.site(
            SiteRequest(
                method,
                path,
                headers=tuple(headers),
                body=urlencode(form).encode(),
            )
        )
        self._follow(path, response)

    def _follow(self, path: str, response: SiteResponse) -> None:
        headers = dict(response.headers)
        cookie = headers.get("set-cookie")
        if cookie is not None:
            self.cookie = cookie.split(";", maxsplit=1)[0]
        location = headers.get("location")
        if response.status == 303 and location is not None:
            target = urlsplit(location)
            follow_headers = (("cookie", self.cookie),) if self.cookie else ()
            followed = self.site(
                SiteRequest(
                    "GET",
                    target.path,
                    query=tuple(parse_qsl(target.query, keep_blank_values=True)),
                    headers=follow_headers,
                )
            )
            self.url = f"https://mail.example{location}"
            self._render(followed.body.decode())
            return
        self.url = f"https://mail.example{path}"
        self._render(response.body.decode())

    def _render(self, document: str) -> None:
        self.text = document
        if 'data-page="password"' in document:
            self.step = "password"
            self.present = {'input[name="password"]', SUBMIT}
            return
        if 'data-page="otp"' in document:
            self.step = "otp"
            self.present = {'input[name="code"]', SUBMIT}
            return
        if 'data-page="mailbox"' in document:
            self.step = "mailbox"
            self.present = {'[data-page="mailbox"]'}
            return
        if 'data-page="captcha"' in document:
            self.step = "captcha"
            self.present = {'[data-page="captcha"]'}
            return
        code = re.search(r'data-code="([^"]+)"', document)
        self.step = "failure"
        self.present = {f'[data-code="{code.group(1)}"]'} if code is not None else set()


__all__ = ["TeachingLoginDriver"]
