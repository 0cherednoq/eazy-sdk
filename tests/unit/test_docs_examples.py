from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from eazy_sdk_html import parse_html
from pydantic import SecretStr

from eazy_sdk import Client, ClientConfig
from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler
from examples.adaptix_nested_wire_body import (
    AdaptixWireDefaults,
    make_register_converter,
)
from examples.books_to_scrape import CatalogPage
from examples.dummyjson_auth import (
    USER_BEARER,
    DummyJsonAuthApi,
    DummyJsonUsersApi,
)
from examples.dummyjson_session_auth import (
    DummyJsonSandbox,
    DummyJsonSdk,
    UserSession,
)
from examples.flat_model_wire_body import (
    RegisterUserProjection,
    RegisterWireSettings,
)
from examples.jsonplaceholder_posts import JsonPlaceholderApi

pytestmark = pytest.mark.unit

REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("examples/quickstart.py", "Mechanical keyboard 12900\n"),
        (
            "examples/response_cases.py",
            "200: order-42 is paid\n"
            "404: Unknown order missing\n"
            "429: rate_limited\n",
        ),
        (
            "examples/flat_model_wire_body.py",
            "registered: john as user-42\n",
        ),
        (
            "examples/adaptix_nested_wire_body.py",
            "adaptix registered: john as user-42\n",
        ),
        (
            "examples/docs/store_sdk.py",
            "pay-42 accepted receipt-pay-42\n",
        ),
        (
            "examples/dummyjson_session_auth.py",
            "authenticated: emilys (Emily Johnson)\n"
            "runtime:\n"
            "- POST /auth/login\n"
            "- GET /auth/me Bearer access-1\n"
            "- POST /auth/refresh\n"
            "- GET /auth/me Bearer access-2\n",
        ),
    ],
)
def test_local_examples_run_without_network(script: str, expected: str) -> None:
    completed = subprocess.run(
        [sys.executable, script],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.stdout == expected


def test_jsonplaceholder_example_serializes_and_parses_declared_models() -> None:
    def server(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/posts/1":
            return httpx.Response(
                200,
                json={"id": 1, "userId": 7, "title": "One", "body": "First"},
            )
        if request.method == "GET" and request.url.path == "/posts":
            assert dict(request.url.params) == {"userId": "7"}
            return httpx.Response(
                200,
                json=[{"id": 2, "userId": 7, "title": "Two", "body": "Second"}],
            )
        assert request.method == "POST" and request.url.path == "/posts"
        assert json.loads(request.content) == {
            "userId": 7,
            "title": "Created",
            "body": "Request body",
        }
        return httpx.Response(201, json={"id": 101, **json.loads(request.content)})

    raw = httpx.Client(
        transport=httpx.MockTransport(server),
        headers={},
        cookies={},
    )
    with Client(
        base_url="https://jsonplaceholder.test",
        handler=HttpxHandler(raw, owns_client=True),
    ) as client:
        posts = JsonPlaceholderApi(client)
        first = posts.get_post.with_response(post_id=1)
        selected = posts.list_posts(user_id=7)
        created = posts.create_post(
            user_id=7,
            title="Created",
            body="Request body",
        )

    assert (first.status_code, first.value.user_id) == (200, 7)
    assert [post.title for post in selected] == ["Two"]
    assert (created.id, created.user_id) == (101, 7)


def test_flat_public_model_example_projects_nested_wire_body_with_generated_values() -> None:
    projection = RegisterUserProjection(
        RegisterWireSettings(timestamp_factory=lambda: 1_787_740_000)
    )

    body = projection(
        {
            "login": "john",
            "email": "john@example.com",
            "first_name": "John",
            "last_name": "Smith",
        }
    )

    assert body == {
        "account": {
            "login": "john",
            "email": "john@example.com",
        },
        "profile": {
            "first_name": "John",
            "last_name": "Smith",
        },
        "client": {
            "encoding": "utf-8",
            "url": "https://example.com",
            "version": "1.0",
            "platform": "web",
            "timestamp": 1_787_740_000,
        },
    }


def test_adaptix_example_uses_wire_defaults_and_injected_time_factory() -> None:
    generated_at = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
    converter = make_register_converter(
        AdaptixWireDefaults(now_factory=lambda: generated_at)
    )

    body = converter(
        {
            "login": "john",
            "email": "john@example.com",
            "first_name": "John",
            "last_name": "Smith",
        }
    )

    assert body.payload.account.login == "john"
    assert body.payload.profile.name.first == "John"
    assert body.metadata.locale == "en-US"
    assert body.metadata.encoding == "utf-8"
    assert body.metadata.api_version == "2026-08"
    assert body.metadata.platform == "python"
    assert body.metadata.generated_at is generated_at


def test_books_example_extracts_nested_html_and_optional_pagination() -> None:
    document = b"""
    <html><body><div class="page_inner"><h1>Books</h1>
      <article class="product_pod">
        <p class="star-rating Four"></p>
        <h3><a href="book_1/index.html" title="A Practical Book">Book</a></h3>
        <p class="price_color">\xc2\xa312.50</p>
      </article>
      <li class="next"><a href="page-2.html">next</a></li>
    </div></body></html>
    """

    page = parse_html(document, CatalogPage)

    assert page.title == "Books"
    assert page.next_href == "page-2.html"
    assert page.books[0].title == "A Practical Book"
    assert page.books[0].price_gbp == Decimal("12.50")
    assert page.books[0].rating == "Four"
    assert page.books[0].absolute_url.endswith("/catalogue/book_1/index.html")


def test_dummyjson_example_keeps_login_public_and_adds_bearer_to_me() -> None:
    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            assert "Authorization" not in request.headers
            assert json.loads(request.content) == {
                "username": "emilys",
                "password": "emilyspass",
                "expiresInMins": 30,
            }
            return httpx.Response(
                200,
                json={
                    "username": "emilys",
                    "accessToken": "access-demo",
                    "refreshToken": "refresh-demo",
                },
            )

        assert request.url.path == "/auth/me"
        assert request.headers["Authorization"] == "Bearer access-demo"
        return httpx.Response(
            200,
            json={
                "id": 1,
                "username": "emilys",
                "email": "emily@example.test",
                "firstName": "Emily",
                "lastName": "Johnson",
            },
        )

    raw = httpx.Client(
        transport=httpx.MockTransport(server),
        headers={},
        cookies={},
    )
    with Client(
        base_url="https://dummyjson.test",
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(auth=USER_BEARER.static("access-demo")),
    ) as client:
        session = DummyJsonAuthApi(client).login(
            username="emilys",
            password=SecretStr("emilyspass").get_secret_value(),
            expires_in_mins=30,
        )
        user = DummyJsonUsersApi(client).me()

    assert session.access_token.get_secret_value() == "access-demo"
    assert (user.username, user.first_name) == ("emilys", "Emily")


@pytest.mark.asyncio
async def test_session_auth_uses_a_supplied_session_without_login() -> None:
    server = DummyJsonSandbox()
    saved = UserSession.model_validate(
        {"accessToken": "saved-access", "refreshToken": "saved-refresh"}
    )

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(server),
        headers={},
        cookies={},
    )
    async with DummyJsonSdk.from_handler(
        handler=AsyncHttpxHandler(raw, owns_client=True),
        session=saved,
    ) as sdk:
        user = await sdk.users.me()

    assert user.username == "emilys"
    assert server.calls == ["GET /auth/me Bearer saved-access"]
