"""Phase 33: public surface and entry point (factories, root re-exports, one facade, config)."""

from __future__ import annotations

import inspect
from typing import Annotated, Any, cast

import httpx
import pytest
from pydantic import BaseModel
from zapros import (
    AsyncBaseHandler,
    AsyncStdNetworkHandler,
    BaseHandler,
    Request,
    Response,
    StdNetworkHandler,
)

import eazy_sdk
from eazy_sdk import (
    AsyncApi,
    AsyncClient,
    Bytes,
    Client,
    ClientConfig,
    Json,
    Path,
    Query,
    Responses,
    Success,
    SyncApi,
    api,
    api_group,
)
from eazy_sdk.protection import Guard, GuardSolution, SolveContext, host
from eazy_sdk.protection.advanced import ProtectionBundle
from eazy_sdk.response import ResponseContext


class Product(BaseModel):
    id: int
    title: str


class ProductsApi(SyncApi):
    @api.get("/v1/products/{product_id}", response=Json())
    def product(self, *, product_id: Annotated[int, Path()]) -> Product:
        raise NotImplementedError

    @api.get("/v1/products/{product_id}/image", response=Bytes())
    def image(self, *, product_id: Annotated[int, Path()]) -> bytes:
        raise NotImplementedError


class AsyncProductsApi(AsyncApi):
    @api.get(
        "/v1/products",
        responses=Responses(success=(Success(200, Json(list[Product])),)),
    )
    async def products(self, *, q: Annotated[str, Query()] = "") -> list[Product]:
        raise NotImplementedError


class StoreSdk(SyncApi):
    products = api_group(ProductsApi)


class AsyncStoreSdk(AsyncApi):
    products = api_group(AsyncProductsApi)


def _respond(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/image"):
        return httpx.Response(200, content=b"PNG", headers={"Content-Type": "image/png"})
    if request.url.path == "/v1/products":
        return httpx.Response(200, json=[{"id": 1, "title": "one"}])
    return httpx.Response(200, json={"id": 42, "title": "Keyboard"})


def test_quickstart_needs_one_import_block_and_no_handler_assembly() -> None:
    transport = httpx.MockTransport(_respond)
    with Client.httpx(base_url="https://store.test", transport=transport) as client:
        store = StoreSdk(client)
        assert store.products.product(product_id=42) == Product(id=42, title="Keyboard")
        assert store.products.image(product_id=42) == b"PNG"
        assert store.products is store.products
    assert client._closed
    for name in ("Path", "Query", "Header", "JsonBody", "Json", "Responses", "Success",
                 "Html", "Bytes", "Text", "Error", "Cookie", "FormBody", "BodyProjection"):
        assert name in eazy_sdk.__all__, name
    assert "SyncSdk" not in eazy_sdk.__all__ and "AsyncSdk" not in eazy_sdk.__all__
    assert not hasattr(eazy_sdk, "SyncSdk")
    with pytest.raises(ModuleNotFoundError):
        __import__("eazy_sdk.sdk")


@pytest.mark.asyncio
async def test_async_factory_and_owning_root_close_exactly_once() -> None:
    transport = httpx.MockTransport(_respond)
    raw = httpx.AsyncClient(transport=transport)
    async with AsyncStoreSdk.from_client(
        AsyncClient.httpx(base_url="https://store.test", client=raw), owns_client=True
    ) as store:
        assert await store.products.products(q="x") == [Product(id=1, title="one")]
    # The handler borrowed the caller's httpx client, so it stays open.
    assert not raw.is_closed
    await raw.aclose()


def test_bare_client_uses_the_standard_zapros_handler_and_owns_it() -> None:
    client = Client(base_url="https://store.test")
    assert isinstance(client.handler, StdNetworkHandler)
    assert client.owns_handler
    client.close()
    async_client = AsyncClient(base_url="https://store.test")
    assert isinstance(async_client.handler, AsyncStdNetworkHandler)
    assert async_client.owns_handler
    for cls, factories in (
        (Client, ("httpx", "requests", "curl_cffi")),
        (AsyncClient, ("httpx", "curl_cffi")),
    ):
        for factory in factories:
            assert inspect.ismethod(getattr(cls, factory)), (cls, factory)
        for verb in ("get", "post", "put", "patch", "delete", "head", "options", "trace"):
            assert not hasattr(cls, verb), (cls, verb)
        assert callable(cls.request)


def test_requests_and_curl_cffi_factories_build_first_party_handlers() -> None:
    requests = pytest.importorskip("requests")
    with Client.requests(base_url="https://store.test") as client:
        from eazy_sdk.handlers.requests import RequestsHandler

        assert isinstance(client.handler, RequestsHandler)
    session = requests.Session()
    with Client.requests(base_url="https://store.test", session=session) as client:
        assert cast(Any, client.handler).session is session
    pytest.importorskip("curl_cffi")
    from eazy_sdk.handlers.curl_cffi import AsyncCurlCffiZaprosHandler, CurlCffiZaprosHandler

    with Client.curl_cffi(base_url="https://store.test", impersonate="chrome") as client:
        assert isinstance(client.handler, CurlCffiZaprosHandler)
        assert client.profile.impersonation == "chrome"
    async_client = AsyncClient.curl_cffi(base_url="https://store.test")
    assert isinstance(async_client.handler, AsyncCurlCffiZaprosHandler)


def test_api_group_kind_is_validated_and_bare_api_owns_nothing() -> None:
    with pytest.raises(TypeError, match="wrong API kind"):

        class Invalid(SyncApi):
            products = api_group(AsyncProductsApi)

    class Handler(BaseHandler):
        def __init__(self) -> None:
            self.closed = 0

        def handle(self, request: Request) -> Response:
            raise AssertionError("not called")

        def close(self) -> None:
            self.closed += 1

    handler = Handler()
    client = Client(base_url="https://store.test", handler=handler)
    with StoreSdk(client) as store:
        assert isinstance(store.products, ProductsApi)
    assert handler.closed == 0
    with StoreSdk.from_client(client, owns_client=True):
        pass
    assert handler.closed == 1
    assert isinstance(AsyncBaseHandler, type)


def test_client_config_groups_protection_into_one_bundle() -> None:
    class CookieGuard(Guard[int]):
        scope = host("store.test")

        def detect(self, response: ResponseContext[object]) -> int | None:
            return None

        def solve(self, challenge: int, context: SolveContext) -> GuardSolution:
            return self.solution(cookies={"c": "v"})

    config = ClientConfig(guards=[CookieGuard()])
    assert isinstance(config.protection, ProtectionBundle)
    assert [p.identity for p in config.protection.challenge_policies] == ["CookieGuard"]
    assert len(config.protection.solver_bindings) == 1
    assert ClientConfig().protection is None
    assert ClientConfig().bundle == ProtectionBundle()
    assert not ClientConfig().bundle
    parameters = inspect.signature(ClientConfig).parameters
    assert {"protection", "guards", "retry", "auth", "timeout"} <= set(parameters)
    for removed in ("operation_protections", "before_call_policies", "challenge_policies",
                    "solver_bindings", "challenge_solvers", "operation_protection_solvers"):
        assert removed not in parameters
    with pytest.raises(TypeError, match="ProtectionBundle"):
        ClientConfig(protection=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="duplicate"):
        ClientConfig(guards=[CookieGuard()]).with_protection(CookieGuard())
    merged = ClientConfig(guards=[CookieGuard()]).with_protection()
    assert merged.protection is not None
    assert [p.identity for p in merged.protection.challenge_policies] == ["CookieGuard"]


def test_internal_and_codegen_do_not_advertise_private_surface() -> None:
    import eazy_sdk.codegen as codegen
    import eazy_sdk.compile as compile_layer
    import eazy_sdk.core as core

    assert not hasattr(compile_layer, "__all__") and not hasattr(core, "__all__")
    assert all(not name.startswith("_") for name in codegen.__all__)
    assert {"session_auth", "session_scheme", "ProtectionBundle"} <= set(codegen.__all__)
    assert len(eazy_sdk.__all__) <= 70
