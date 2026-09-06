"""Phase 42: service mixins on routers, root composition and assembly-time bindings."""

from __future__ import annotations

import inspect
from typing import Annotated, Any

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import (
    AsyncApi,
    AsyncClient,
    AsyncRoot,
    Client,
    Json,
    Responses,
    Success,
    SyncApi,
    SyncRoot,
    api,
    api_group,
    bind,
)
from eazy_sdk.auth import BearerScheme
from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler
from eazy_sdk.request import SigningKeyRequirement, body_digest, header_output, hmac_sha256
from eazy_sdk.request.markers import Path
from eazy_sdk.testing import RecordingHandler


class Echo(BaseModel):
    url: str


ECHO: Responses[Echo] = Responses(success=(Success(200, Json(Echo)),))

MAIN = "https://api.shop.com"
PAY = "https://pay.shop.com"
STAGE_PAY = "https://stage-pay.shop.com"

SHOP_SESSION = BearerScheme("shop-session")
OTHER_SESSION = BearerScheme("other-session")
PAYMENTS_HMAC = hmac_sha256(
    key=SigningKeyRequirement("payments"),
    base=body_digest("sha256"),
    output=header_output("X-Signature"),
)


def _echo(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"url": str(request.url)})


def _client(base_url: str = MAIN) -> Client:
    raw = httpx.Client(transport=httpx.MockTransport(_echo))
    return Client(base_url=base_url, handler=HttpxHandler(raw, owns_client=True))


class PaymentsService:
    """An ordinary base class: everything shared by the routers of one service."""

    base_url = PAY


class BooksApi(SyncApi):
    @api.get("/books/{book_id}", responses=ECHO)
    def get(self, *, book_id: Annotated[int, Path()]) -> Echo:
        raise NotImplementedError


class CardApi(PaymentsService, SyncApi):
    @api.get("/v1/cards", responses=ECHO)
    def get(self) -> Echo:
        raise NotImplementedError


class RefundApi(PaymentsService, SyncApi):
    @api.get("/v1/refunds", responses=ECHO)
    def get(self) -> Echo:
        raise NotImplementedError


class ShopSdk(SyncRoot):
    books = api_group(BooksApi)
    card = api_group(CardApi)
    refunds = api_group(RefundApi)


def test_two_services_reach_two_hosts_over_one_client() -> None:
    with _client() as client:
        sdk = ShopSdk(client)
        assert sdk.books.get(book_id=1).url == f"{MAIN}/books/1"
        assert sdk.card.get().url == f"{PAY}/v1/cards"
        assert sdk.refunds.get().url == f"{PAY}/v1/refunds"
        assert sdk.card is sdk.card


def test_bind_on_the_service_mixin_moves_every_router_of_that_service() -> None:
    with _client() as client:
        sdk = ShopSdk(client, bindings=(bind(PaymentsService, base_url=STAGE_PAY),))
        assert sdk.card.get().url == f"{STAGE_PAY}/v1/cards"
        assert sdk.refunds.get().url == f"{STAGE_PAY}/v1/refunds"
        assert sdk.books.get(book_id=1).url == f"{MAIN}/books/1"


def test_bind_on_one_router_wins_over_its_service_mixin() -> None:
    with _client() as client:
        sdk = ShopSdk(
            client,
            bindings=(
                bind(PaymentsService, base_url=STAGE_PAY),
                bind(CardApi, base_url="https://card.shop.com"),
            ),
        )
        assert sdk.card.get().url == "https://card.shop.com/v1/cards"
        assert sdk.refunds.get().url == f"{STAGE_PAY}/v1/refunds"


def test_bind_client_sends_one_router_through_another_transport() -> None:
    main = RecordingHandler(
        status=200,
        headers={"Content-Type": "application/json"},
        content=b'{"url":"main"}',
    )
    payments = RecordingHandler(
        status=200,
        headers={"Content-Type": "application/json"},
        content=b'{"url":"payments"}',
    )
    with Client(base_url=MAIN, handler=main) as main_client, Client(
        base_url=PAY, handler=payments
    ) as payments_client:
        sdk = ShopSdk(main_client, bindings=(bind(CardApi, client=payments_client),))
        sdk.books.get(book_id=1)
        sdk.card.get()

    main.assert_count(1)
    payments.assert_count(1)
    payments.assert_request(method="GET", url=f"{PAY}/v1/cards")


def test_a_router_without_service_attributes_uses_the_client_address() -> None:
    with _client("https://other.example") as client:
        sdk = ShopSdk(client)
        assert sdk.books.get(book_id=7).url == "https://other.example/books/7"


def test_service_attributes_are_inherited_through_the_mro() -> None:
    class Regional(PaymentsService):
        base_url = "https://eu.pay.shop.com"

    class RegionalCardApi(Regional, SyncApi):
        @api.get("/v1/cards", responses=ECHO)
        def get(self) -> Echo:
            raise NotImplementedError

    class RegionalSdk(SyncRoot):
        card = api_group(RegionalCardApi)

    with _client() as client:
        assert RegionalSdk(client).card.get().url == "https://eu.pay.shop.com/v1/cards"


def test_two_mixins_with_conflicting_base_url_are_a_declaration_error() -> None:
    class Other:
        base_url = "https://other.shop.com"

    with pytest.raises(TypeError, match="conflicting 'base_url'"):

        class Conflicted(PaymentsService, Other, SyncApi):
            pass


def test_a_relative_base_url_is_a_declaration_error() -> None:
    with pytest.raises(ValueError, match="absolute URL"):

        class Relative(SyncApi):
            base_url = "/v1"


def test_a_scheme_outside_allow_is_rejected_when_the_root_is_assembled() -> None:
    class GuardedService:
        base_url = PAY
        allow = (SHOP_SESSION,)

    class LeakyApi(GuardedService, SyncApi):
        @api.get("/leak", responses=ECHO, security=OTHER_SESSION)
        def get(self) -> Echo:
            raise NotImplementedError

    class LeakySdk(SyncRoot):
        leak = api_group(LeakyApi)

    with _client() as client, pytest.raises(TypeError, match="allowlist"):
        LeakySdk(client)


def test_a_signature_outside_allow_is_rejected_when_the_root_is_assembled() -> None:
    class GuardedService:
        base_url = PAY
        allow = (SHOP_SESSION,)

    class SignedApi(GuardedService, SyncApi):
        @api.post("/sign", responses=ECHO, signing=PAYMENTS_HMAC)
        def post(self) -> Echo:
            raise NotImplementedError

    class SignedSdk(SyncRoot):
        signed = api_group(SignedApi)

    with _client() as client, pytest.raises(TypeError, match="allowlist"):
        SignedSdk(client)


def test_an_allowed_scheme_and_signature_pass() -> None:
    class GuardedService:
        base_url = PAY
        allow = (SHOP_SESSION, PAYMENTS_HMAC)

    class AllowedApi(GuardedService, SyncApi):
        @api.post("/ok", responses=ECHO, security=SHOP_SESSION, signing=PAYMENTS_HMAC)
        def post(self) -> Echo:
            raise NotImplementedError

    class AllowedSdk(SyncRoot):
        ok = api_group(AllowedApi)

    with _client() as client:
        assert isinstance(AllowedSdk(client).ok, AllowedApi)


def test_bind_on_an_unused_class_is_an_assembly_error() -> None:
    class Unrelated:
        pass

    with _client() as client, pytest.raises(TypeError, match="targets no router"):
        ShopSdk(client, bindings=(bind(Unrelated, base_url=STAGE_PAY),))


def test_two_bindings_on_the_same_class_are_an_assembly_error() -> None:
    with _client() as client, pytest.raises(TypeError, match="declared twice"):
        ShopSdk(
            client,
            bindings=(bind(CardApi, base_url=STAGE_PAY), bind(CardApi, base_url=PAY)),
        )


def test_two_bindings_of_equal_specificity_are_an_assembly_error() -> None:
    class Marker:
        pass

    class MarkedApi(Marker, PaymentsService, SyncApi):
        @api.get("/marked", responses=ECHO)
        def get(self) -> Echo:
            raise NotImplementedError

    class MarkedSdk(SyncRoot):
        marked = api_group(MarkedApi)

    with _client() as client, pytest.raises(TypeError, match="conflicting bind"):
        MarkedSdk(
            client,
            bindings=(bind(Marker, base_url=STAGE_PAY), bind(PaymentsService, base_url=PAY)),
        )


def test_an_operation_on_the_root_class_is_a_declaration_error() -> None:
    with pytest.raises(TypeError, match="only composes routers"):

        class RootWithOperation(SyncRoot):
            @api.get("/oops", responses=ECHO)  # type: ignore[type-var]
            def get(self) -> Echo:
                raise NotImplementedError


def test_api_groups_are_declared_on_a_root_not_on_a_router() -> None:
    with pytest.raises(TypeError, match="api groups belong to"):

        class NestedRouter(SyncApi):
            books = api_group(BooksApi)


def test_a_root_is_not_a_router_and_exposes_no_transport() -> None:
    assert not issubclass(SyncRoot, SyncApi)
    assert not issubclass(AsyncRoot, AsyncApi)
    with _client() as client:
        sdk = ShopSdk(client)
        for name in ("request", "handler", "profile", "raw"):
            assert not hasattr(sdk, name), name
        public = {name for name in dir(sdk) if not name.startswith("_")}
        assert public == {
            "books",
            "card",
            "refunds",
            "close",
            "from_handler",
            "identity",
            "serialization",
        }


def test_from_handler_owns_only_the_client_it_created_and_close_is_idempotent() -> None:
    handler = RecordingHandler(
        status=200,
        headers={"Content-Type": "application/json"},
        content=b'{"url":"x"}',
    )
    sdk = ShopSdk.from_handler(handler=handler, base_url=MAIN)
    owned = sdk._owned[0]
    sdk.close()
    sdk.close()
    assert owned._closed

    borrowed = _client()
    root = ShopSdk(borrowed)
    root.close()
    assert not borrowed._closed
    borrowed.close()


def test_a_bound_client_is_never_closed_by_the_root() -> None:
    payments = _client(PAY)
    handler = RecordingHandler(
        status=200,
        headers={"Content-Type": "application/json"},
        content=b'{"url":"x"}',
    )
    sdk = ShopSdk.from_handler(
        handler=handler,
        base_url=MAIN,
        bindings=(bind(CardApi, client=payments),),
    )
    sdk.close()
    assert not payments._closed
    payments.close()


def test_routers_no_longer_carry_root_factories() -> None:
    for router in (SyncApi, AsyncApi, BooksApi, CardApi):
        for name in ("from_client", "from_handler", "close", "aclose"):
            assert not hasattr(router, name), (router, name)


def test_the_merged_declaration_is_computed_once_per_router() -> None:
    with _client() as client:
        sdk = ShopSdk(client)
        card = sdk.card
        first = inspect.getattr_static(CardApi, "get").resolve_for(card)
        second = inspect.getattr_static(CardApi, "get").resolve_for(card)
        assert first is second
        assert first.base_url == PAY


class AsyncBooksApi(AsyncApi):
    @api.get("/books/{book_id}", responses=ECHO)
    async def get(self, *, book_id: Annotated[int, Path()]) -> Echo:
        raise NotImplementedError


class AsyncCardApi(PaymentsService, AsyncApi):
    @api.get("/v1/cards", responses=ECHO)
    async def get(self) -> Echo:
        raise NotImplementedError


class AsyncShopSdk(AsyncRoot):
    books = api_group(AsyncBooksApi)
    card = api_group(AsyncCardApi)


@pytest.mark.asyncio
async def test_async_root_routes_two_services_over_one_client() -> None:
    raw = httpx.AsyncClient(transport=httpx.MockTransport(_echo))
    client: Any = AsyncClient(base_url=MAIN, handler=AsyncHttpxHandler(raw, owns_client=True))
    async with AsyncShopSdk(client) as sdk:
        assert (await sdk.books.get(book_id=3)).url == f"{MAIN}/books/3"
        assert (await sdk.card.get()).url == f"{PAY}/v1/cards"
    assert not client._closed
    await client.aclose()


def test_a_root_rejects_a_group_of_the_other_execution_kind() -> None:
    with pytest.raises(TypeError, match="wrong API kind"):

        class Mixed(SyncRoot):
            card = api_group(AsyncCardApi)
