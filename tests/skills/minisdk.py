"""A tiny SDK project for the synthetic site, written to disk for the replay and check tests."""

from __future__ import annotations

from pathlib import Path

SDK_SOURCE = '''\
"""A tiny SDK for the synthetic site of the skill tests."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated, Self

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Inject, Query, api_group, bind, op
from eazy_sdk.auth import Cookies
from eazy_sdk.request import Form, markers
from eazy_sdk.response import ApiError, Location
from eazy_sdk_html import Regex
from pydantic import BaseModel
from zapros import AsyncBaseHandler


def _now_ms() -> int:
    return int(time.time() * 1000)


class IdHost(AsyncApi):
    """The sign-in host."""


class MailHost(AsyncApi):
    """The mail host."""


class LoginPage(BaseModel):
    csrf: Annotated[str, Regex(r\'"csrf":"([^"]+)"\')]


class MailPage(BaseModel):
    token: Annotated[str, Regex(r\'"token":"([^"]+)"\')]


@dataclass(frozen=True, slots=True)
class SignedIn:
    url: Annotated[str, Location(path="/inbox*")]


@dataclass(frozen=True, slots=True)
class Rejected:
    url: Annotated[str | None, Location()] = None


class InvalidCredentials(ApiError[Rejected]):
    """The site sent the visitor back to the login page."""


class Thread(BaseModel):
    id: str
    subject: str = ""


class ThreadsPage(BaseModel):
    threads: list[Thread]
    threads_total: int


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenLogin(HttpOperation[LoginPage]):
    __http__ = Http.get(
        "/login", inject=(Inject(placement=markers.Query("from"), source="mail"),)
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCredentials(HttpOperation[SignedIn]):
    __http__ = Http.post(
        "/auth?project=mail",
        inject=(Inject(placement=markers.Header("Origin"), source=ORIGIN),),
        success={302: SignedIn},
        fallback=InvalidCredentials,
    )

    login: Form[str]
    password: Form[str]
    csrf: Form[str]
    keep: Form[str] = "1"


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenInbox(HttpOperation[MailPage]):
    __http__ = Http.get("/inbox/")


@dataclass(frozen=True, slots=True, kw_only=True)
class ListThreads(HttpOperation[ThreadsPage]):
    __http__ = Http.get(
        "/api/v1/threads", inject=(Inject(placement=markers.Query("_"), source=_now_ms),)
    )

    token: Query[str]
    offset: Query[int] = 0
    limit: Query[int] = LIMIT


class IdApi(IdHost):
    open = op(OpenLogin)
    submit = op(SubmitCredentials)


class MailPages(MailHost):
    inbox = op(OpenInbox)


class MailApi(MailHost):
    unwrap = "/body"

    threads = op(ListThreads)


class MiniSdk(AsyncRoot):
    cookies = Cookies()

    ident = api_group(IdApi)
    pages = api_group(MailPages)
    mail = api_group(MailApi)

    @classmethod
    def open(cls, handler: AsyncBaseHandler) -> Self:
        return cls.from_handler(
            handler=handler,
            base_url="https://mail.site.example",
            bindings=(bind(IdHost, base_url="https://id.site.example"),),
        )

    async def sign_in(self, *, login: str, password: str) -> MailPage:
        page = await self.ident.open()
        await self.ident.submit(login=login, password=password, csrf=page.csrf)
        return await self.pages.inbox()

    async def first_page(self, *, login: str, password: str) -> ThreadsPage:
        inbox = await self.sign_in(login=login, password=password)
        return await self.mail.threads(token=inbox.token)
'''

FACTORY_SOURCE = '''\
"""How the replay opens the SDK."""

from minisdk.api import MiniSdk


def open_sdk(handler):
    return MiniSdk.open(handler)
'''

SCENARIOS = """\
[sdk]
factory = "replay_factory:open_sdk"
mode = "async"

[[scenario]]
name = "wrong password"
exchanges = "1-2"
call = "sign_in"
[scenario.args]
login = "__SECRET_email_1__"
password = "__SECRET_password_1__"
[scenario.expect]
raises = "InvalidCredentials"

[[scenario]]
name = "sign in and read the first page"
exchanges = [1, 3, 4, 5]
call = "first_page"
[scenario.args]
login = "__SECRET_email_1__"
password = "__SECRET_password_2__"
[scenario.expect]
returns = "ThreadsPage"
fields = { threads_total = 5 }
lengths = { threads = 2 }

[[exclude]]
exchanges = "6"
reason = "the second page: pagination is not part of this SDK"
"""


def write_project(root: Path, *, origin: str = "https://id.site.example", limit: int = 2) -> Path:
    """Write the SDK project into ``root`` and return the path of its scenarios file."""
    package = root / "minisdk"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""The synthetic SDK."""\n', encoding="utf-8")
    source = SDK_SOURCE.replace("source=ORIGIN", f'source="{origin}"').replace(
        "= LIMIT", f"= {limit}"
    )
    (package / "api.py").write_text(source, encoding="utf-8")
    (root / "replay_factory.py").write_text(FACTORY_SOURCE, encoding="utf-8")
    scenarios = root / "scenarios.toml"
    scenarios.write_text(SCENARIOS, encoding="utf-8")
    return scenarios
