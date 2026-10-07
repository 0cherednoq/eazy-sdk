"""Local deterministic service used by the request-guide examples."""

from __future__ import annotations

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


def _get(request: httpx.Request) -> httpx.Response:
    path = request.url.path
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
