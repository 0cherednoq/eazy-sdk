"""Local deterministic service used by the request-guide examples."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

import httpx

from eazy_sdk import Client
from eazy_sdk.handlers.httpx import HttpxHandler


def _json_response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _auth_get(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/auth/api-key":
        return _json_response(
            200,
            {"credential": request.headers.get("X-API-Key", "")},
        )
    if path == "/auth/basic":
        encoded = request.headers.get("Authorization", "").removeprefix("Basic ")
        username, _, _password = base64.b64decode(encoded).decode("utf-8").partition(":")
        return _json_response(200, {"credential": username})
    if path == "/auth/bearer":
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return _json_response(200, {"credential": token})
    if path == "/auth/jwt":
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return _json_response(200, {"credential": "jwt" if token.count(".") == 2 else "invalid"})
    if path == "/auth/cookie":
        cookies = SimpleCookie(request.headers.get("Cookie", ""))
        session = cookies.get("mail_session")
        return _json_response(
            200,
            {"credential": session.value if session is not None else ""},
        )
    if path == "/auth/combined":
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return _json_response(
            200,
            {"credential": f"{request.headers.get('X-Device-Key', '')}+{token}"},
        )
    return _json_response(404, {"error": "route_not_found"})


def _response_get(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/orders/order-42":
        return httpx.Response(
            200,
            headers={"X-Request-ID": "request-42"},
            json={"id": "order-42", "status": "paid", "total": 12_900},
        )
    if path == "/orders/busy":
        return _json_response(
            429,
            {"code": "rate_limited", "message": "Try again later"},
        )
    if path.startswith("/orders/"):
        order_id = path.rsplit("/", maxsplit=1)[-1]
        return _json_response(
            404,
            {"code": "order_not_found", "message": f"Unknown order {order_id}"},
        )
    return httpx.Response(
        200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content=b"""<!doctype html><html><body><main>
<h1>Mailbox library</h1>
<article class="book"><a href="sdk-patterns.html">SDK Patterns</a><span>12.50</span></article>
<article class="book"><a href="typed-http.html">Typed HTTP</a><span>18.00</span></article>
<a class="next" href="page-2.html">next</a>
</main></body></html>""",
    )


def _get(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/auth/"):
        return _auth_get(request)
    if path.startswith("/orders/") or path == "/catalogue/page-1.html":
        return _response_get(request)
    if path == "/users/42":
        return _json_response(200, {"id": 42, "name": "Ada"})
    if path == "/users":
        return _json_response(
            200,
            [{"id": 42, "name": "Ada", "query": str(request.url.params.get("q", ""))}],
        )
    if path == "/reports":
        return _json_response(
            200,
            {
                "locale": request.headers.get("Accept-Language", ""),
                "request_id": request.headers.get("X-Request-ID"),
            },
        )
    if path == "/dashboard":
        cookies = SimpleCookie(request.headers.get("Cookie", ""))
        experiment = cookies.get("experiment")
        return _json_response(
            200,
            {
                "experiment": experiment.value if experiment is not None else None,
                "widgets": 3,
            },
        )
    if path == "/items":
        page = int(request.url.params.get("page", "1"))
        pages = {
            1: {"items": ["keyboard", "mouse"], "next_page": 2},
            2: {"items": ["monitor", "cable"], "next_page": 3},
            3: {"items": ["lamp"], "next_page": None},
        }
        return _json_response(200, pages[page])
    return _json_response(404, {"error": "route_not_found"})


def _post(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/users":
        document = json.loads(request.content)
        return _json_response(201, {"id": 42, "name": document["name"]})
    if path == "/login":
        fields = parse_qs(request.content.decode("ascii"))
        return _json_response(200, {"account": fields["email"][0], "access_token": "session-1"})
    if path == "/files":
        body = request.content
        filename = "avatar.txt" if b'filename="avatar.txt"' in body else "unknown"
        return _json_response(201, {"file_id": "file-1", "filename": filename})
    if path == "/register":
        document = json.loads(request.content)
        account = document["account"]
        return _json_response(201, {"id": "account-42", "login": account["login"]})
    if path == "/orders":
        document = json.loads(request.content)
        return _json_response(201, {"id": "order-42", "item": document["item"]})
    return _json_response(404, {"error": "route_not_found"})


def request_guide_site(request: httpx.Request) -> httpx.Response:
    """Answer the small contracts demonstrated in ``guide/requests``."""

    if request.method == "GET":
        return _get(request)
    if request.method == "POST":
        return _post(request)
    if request.method == "PUT" and request.url.path == "/objects/report.bin":
        return _json_response(201, {"stored": True, "bytes": len(request.content)})
    return _json_response(404, {"error": "route_not_found"})


@contextmanager
def request_client() -> Iterator[Client]:
    """Yield a real SDK client backed by the deterministic local service."""

    raw = httpx.Client(
        transport=httpx.MockTransport(request_guide_site),
        headers={},
        cookies={},
    )
    with Client(
        base_url="https://api.mail.example",
        handler=HttpxHandler(raw, owns_client=True),
    ) as client:
        yield client
