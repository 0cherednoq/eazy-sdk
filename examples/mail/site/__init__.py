"""Transport adapters for the shared teaching mail site."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

import httpx
import json

from .app import MailSite, SiteRequest, SiteResponse, SiteState

_SITE = MailSite()


def mail_site(request: SiteRequest) -> SiteResponse:
    """Serve one request through the default in-memory teaching site."""

    return _SITE(request)


def handle_httpx(request: httpx.Request, site: MailSite | None = None) -> httpx.Response:
    """Adapt an HTTP client request to the transport-neutral teaching site."""

    target = _SITE if site is None else site
    response = target(_site_request(request.method, str(request.url), request.headers, request.content))
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


async def intercept_page(route: PageRoute, site: MailSite | None = None) -> None:
    """Adapt an intercepted browser request to the same teaching site."""

    request = route.request
    target = _SITE if site is None else site
    response = target(
        _site_request(
            request.method,
            request.url,
            request.headers,
            request.post_data_buffer or b"",
        )
    )
    response = _follow_page_redirect(request, response, target)
    await route.fulfill(
        status=response.status,
        headers=dict(response.headers),
        body=response.body,
    )


def _follow_page_redirect(
    request: PageRequest,
    response: SiteResponse,
    site: MailSite,
) -> SiteResponse:
    """Let an intercepted form follow the teaching site's same-origin redirect."""

    headers = dict(response.headers)
    location = headers.get("location")
    if response.status != 303 or location is None:
        return response
    parsed = urlsplit(location)
    follow_headers = dict(request.headers)
    cookie = headers.get("set-cookie")
    if cookie is not None:
        follow_headers["cookie"] = cookie.split(";", maxsplit=1)[0]
    followed = site(
        SiteRequest(
            "GET",
            parsed.path,
            query=tuple(parse_qsl(parsed.query, keep_blank_values=True)),
            headers=tuple(follow_headers.items()),
        )
    )
    script = (
        "<script>history.replaceState(null, '', "
        f"{json.dumps(location)});</script>"
    ).encode()
    body = followed.body.replace(b"</body>", script + b"</body>")
    final_headers = dict(followed.headers)
    if cookie is not None:
        final_headers["set-cookie"] = cookie
    return SiteResponse(200, tuple(final_headers.items()), body)


def _site_request(
    method: str,
    url: str,
    headers: httpx.Headers | dict[str, str],
    body: bytes,
) -> SiteRequest:
    parsed = urlsplit(url)
    return SiteRequest(
        method=method,
        path=parsed.path,
        query=tuple(parse_qsl(parsed.query, keep_blank_values=True)),
        headers=tuple(headers.items()),
        body=body,
    )


__all__ = [
    "MailSite",
    "PageRequest",
    "PageRoute",
    "SiteRequest",
    "SiteResponse",
    "SiteState",
    "handle_httpx",
    "intercept_page",
    "mail_site",
]
