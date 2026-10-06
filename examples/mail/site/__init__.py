"""Transport-neutral teaching site shared by the HTTP and browser examples."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

REJECTED_INPUT = "wrong"


@dataclass(frozen=True, slots=True)
class SiteRequest:
    method: str
    path: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""

    def json(self) -> dict[str, object]:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise ValueError("the teaching site accepts JSON objects")
        return value


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
            json.dumps(document, separators=(",", ":")).encode(),
        )


def mail_site(request: SiteRequest) -> SiteResponse:
    """Return one deterministic teaching response without knowing the caller's transport."""

    if request.method != "POST":
        return SiteResponse.json(405, {"message": "Method not allowed"})
    body = request.json()
    if request.path == "/login/identify":
        email = body["email"]
        if email == "missing@mail.example":
            return _failure("account_not_found", "Account does not exist")
        if email == "blocked@mail.example":
            return _failure("account_blocked", "Account is blocked")
        if email == "captcha@mail.example":
            return _failure("captcha_required", "Solve the challenge")
        return _success({"login_id": "login-1"})
    if request.path == "/login/password":
        if body["password"] == REJECTED_INPUT:
            return _failure("wrong_password", "Password is incorrect")
        return _success({"login_id": body["login_id"], "destination": "***-42"})
    if request.path == "/login/otp":
        if body["code"] != "123456":
            return _failure("wrong_code", "Code is incorrect")
        return _success({"access_token": "session-1"})
    return SiteResponse.json(404, {"message": "Not found"})


def handle_httpx(request: httpx.Request) -> httpx.Response:
    """Adapt an HTTP client request to the same transport-neutral teaching site."""

    response = mail_site(
        SiteRequest(
            method=request.method,
            path=request.url.path,
            headers=tuple(request.headers.items()),
            body=request.content,
        )
    )
    return httpx.Response(
        response.status,
        headers=dict(response.headers),
        content=response.body,
    )


class PageRequest(Protocol):
    method: str
    url: str
    headers: dict[str, str]
    post_data_buffer: bytes | None


class PageRoute(Protocol):
    @property
    def request(self) -> PageRequest: ...

    async def fulfill(
        self,
        *,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> None: ...


async def intercept_page(route: PageRoute) -> None:
    """Adapt a browser route to the same transport-neutral teaching site."""

    request = route.request
    response = mail_site(
        SiteRequest(
            method=request.method,
            path=urlsplit(request.url).path,
            headers=tuple(request.headers.items()),
            body=request.post_data_buffer or b"",
        )
    )
    await route.fulfill(
        status=response.status,
        headers=dict(response.headers),
        body=response.body,
    )


def _success(result: dict[str, object]) -> SiteResponse:
    return SiteResponse.json(200, {"ok": True, "result": result})


def _failure(code: str, message: str) -> SiteResponse:
    return SiteResponse.json(200, {"ok": False, "code": code, "message": message})


__all__ = [
    "PageRequest",
    "PageRoute",
    "SiteRequest",
    "SiteResponse",
    "handle_httpx",
    "intercept_page",
    "mail_site",
]
