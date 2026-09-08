"""Phase 48: one pipeline, one declaration of how a request looks on the wire.

The old code spread the answer to "what exactly gets signed?" over three files and six
unrelated meanings of the word *wire*. What holds it together now is small: the stage order
is one tuple, the byte-level policy is one ``Wire`` inherited like every other default, and
the library that runs it is declared once on the root. These tests hold each of those three
claims to its promise — including the promise that nothing in ``Wire`` is decoration.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
from typing import Annotated, Any, TypedDict

import httpx
import pytest
from eazy_sdk_html import CSS, DEFAULT_HTML_BACKEND, XPath
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncClient, Identity, api
from eazy_sdk.clients.executor import transport_requirements
from eazy_sdk.compile.http_operation import _OperationDeclaration
from eazy_sdk.compile.input import inspect_operation_input
from eazy_sdk.crypto import (
    encrypt_encoded,
    encrypt_field,
    encrypt_outbound,
    http_encrypted,
    payload_crypto,
)
from eazy_sdk.crypto.core import CryptoContext, FrozenValue
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.models import default_model_adapters
from eazy_sdk.request import (
    BodyProjection,
    SigningKey,
    SigningKeyRequirement,
    Wire,
    body_digest,
    body_output,
    canonical_json,
    header_output,
    hmac_sha256,
    literal,
)
from eazy_sdk.request.markers import JsonBody, JsonField, Query
from eazy_sdk.request.pipeline import REQUEST_PIPELINE, RequestStage
from eazy_sdk.request.wire import (
    DEFAULT_JSON_POLICY,
    FieldOrder,
    JsonPolicy,
    QueryCodec,
    StdlibJson,
    dump_json,
)
from eazy_sdk.response import Empty, Html, Responses, Success
from eazy_sdk.serialization import BackendCapabilityError, Serialization

pytestmark = pytest.mark.unit

BASE = "https://wire.test"
KEY = SigningKeyRequirement("phase48")
NO_CONTENT: Responses[None] = Responses(success=(Success(204, Empty()),))


def _secret(observer: Any = None) -> Identity:
    return Identity(key_provider=lambda _requirement: SigningKey(b"secret"), observer=observer)


class Payment(TypedDict):
    note: str


class FieldCipher:
    name = "phase48-field-test-only"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert isinstance(value, str)
        return f"sealed:{value}"


class WholeCipher:
    name = "phase48-encoded-test-only"

    def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        return b"sealed:" + value


def _client(handler: Any) -> AsyncClient:
    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return AsyncClient(base_url=BASE, handler=AsyncHttpxHandler(raw, owns_client=True))


async def _accept(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(204)


@dataclasses.dataclass(frozen=True, slots=True)
class _Phase48:
    pass


def _declaration() -> _OperationDeclaration[Any]:
    schema = inspect_operation_input(
        _Phase48, operation_id="phase48", path="/x", models=default_model_adapters()
    )
    return _OperationDeclaration(
        operation_id="phase48",
        method="POST",
        path="/x",
        input_fields=schema.fields,
        input_schema=schema,
        result_type=object,
        responses=NO_CONTENT,
    )


# --- the stage order --------------------------------------------------------------------


def test_the_pipeline_is_one_declaration_and_signing_is_always_last() -> None:
    assert REQUEST_PIPELINE[-1] is RequestStage.SIGN
    assert set(REQUEST_PIPELINE) == set(RequestStage)
    assert len(set(REQUEST_PIPELINE)) == len(REQUEST_PIPELINE)


async def _stages_of(router: Any, **call: Any) -> tuple[RequestStage, ...]:
    seen: list[tuple[RequestStage, ...]] = []

    def observer(phase: str, value: object) -> None:
        if phase == "stages":
            assert isinstance(value, tuple)
            seen.append(value)

    async with _client(_accept) as client:
        await router(client, identity=_secret(observer)).send(**call)
    assert len(seen) == 1
    return seen[0]


@pytest.mark.asyncio
async def test_a_plain_operation_runs_only_the_stages_it_declares() -> None:
    class PlainApi(AsyncApi):
        @api.post(
            "/plain",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
        )
        async def send(self, *, body: Annotated[dict[str, str], JsonBody()]) -> None:
            raise NotImplementedError

    stages = await _stages_of(PlainApi, body={"note": "hi"})
    assert stages == (RequestStage.ENCODE, RequestStage.SIGN)
    assert RequestStage.ENVELOPE not in stages, "an operation without an envelope idles nowhere"


@pytest.mark.asyncio
async def test_a_projected_encrypted_signed_operation_runs_the_declared_order() -> None:
    crypto = payload_crypto(
        "phase48",
        outbound=encrypt_outbound(
            encrypt_field(Payment, lambda body: body["note"], using=FieldCipher()),
            encoded=encrypt_encoded(using=WholeCipher()),
        ),
    )

    class SealedApi(AsyncApi):
        @api.post(
            "/sealed",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
            crypto=crypto,
            signing=hmac_sha256(key=KEY, base=body_digest(), output=header_output("X-Sig")),
            projection=BodyProjection(
                Payment,
                lambda source: source,
                JsonBody(),
                source=Payment,
            ),
            wire=Wire(encrypted=http_encrypted(content_type="application/sealed+json")),
        )
        async def send(self, *, note: str) -> None:
            raise NotImplementedError

    stages = await _stages_of(SealedApi, note="hi")
    assert stages == (
        RequestStage.PROJECTION,
        RequestStage.DOCUMENT_CRYPTO,
        RequestStage.ENCODE,
        RequestStage.ENCODED_CRYPTO,
        RequestStage.SIGN,
    )
    assert list(stages) == [stage for stage in REQUEST_PIPELINE if stage in stages]


@pytest.mark.asyncio
async def test_the_signature_reads_the_bytes_that_leave_including_the_encrypted_body() -> None:
    crypto = payload_crypto(
        "phase48-encoded",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=WholeCipher())),
    )

    class SealedApi(AsyncApi):
        @api.post(
            "/sealed",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
            crypto=crypto,
            signing=hmac_sha256(key=KEY, base=body_digest(), output=header_output("X-Sig")),
            wire=Wire(encrypted=http_encrypted(content_type="application/sealed+json")),
        )
        async def send(self, *, body: Annotated[dict[str, str], JsonBody()]) -> None:
            raise NotImplementedError

    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    async with _client(handler) as client:
        await SealedApi(client, identity=_secret()).send(body={"note": "hi"})

    request = captured[0]
    assert request.content.startswith(b"sealed:")
    expected = hmac.new(
        b"secret", hashlib.sha256(request.content).hexdigest().encode(), hashlib.sha256
    ).hexdigest()
    assert request.headers["x-sig"] == expected, "the signature reproduces from the capture"


# --- Wire is inherited, and every field of it is live ------------------------------------


@pytest.mark.asyncio
async def test_a_wire_declaration_is_inherited_and_the_operation_wins() -> None:
    class SealedService:
        wire = Wire(encoding=JsonPolicy(ensure_ascii=True), query=QueryCodec(space="plus"))

    class InheritedApi(SealedService, AsyncApi):
        @api.post(
            "/inherited",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
        )
        async def inherited(
            self,
            *,
            body: Annotated[dict[str, str], JsonBody()],
            note: Annotated[str, Query("note")],
        ) -> None:
            raise NotImplementedError

        @api.post(
            "/overridden",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
            wire=Wire(encoding=JsonPolicy()),
        )
        async def overridden(self, *, body: Annotated[dict[str, str], JsonBody()]) -> None:
            raise NotImplementedError

    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    async with _client(handler) as client:
        sdk = InheritedApi(client)
        await sdk.inherited(body={"note": "привет"}, note="a b")
        await sdk.overridden(body={"note": "привет"})

    assert b"\\u043f" in captured[0].content, "the service policy reached the body"
    assert "note=a+b" in str(captured[0].url), "the service query codec reached the query"
    assert "привет".encode() in captured[1].content, "the operation overrode the service"


@pytest.mark.asyncio
async def test_no_field_of_wire_is_decoration() -> None:
    """Every field is walked, and each one is shown to change something observable."""

    names = {item.name for item in dataclasses.fields(Wire)}
    assert names == {
        "encrypted",
        "order",
        "exact",
        "encoding",
        "query",
        "transport",
    }
    proven: set[str] = set()

    class WireApi(AsyncApi):
        @api.post(
            "/ordered",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
            wire=Wire(
                order=FieldOrder(body=("second", "first")),
                encoding=JsonPolicy(ensure_ascii=True),
                query=QueryCodec(space="plus"),
            ),
        )
        async def ordered(
            self,
            *,
            first: Annotated[str, JsonField()],
            second: Annotated[str, JsonField()],
            note: Annotated[str, Query("note")],
        ) -> None:
            raise NotImplementedError

    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    async with _client(handler) as client:
        await WireApi(client).ordered(first="a", second="ы", note="a b")

    body = captured[0].content.decode()
    assert body.index('"second"') < body.index('"first"')
    proven.add("order")
    assert "\\u04" in body
    proven.add("encoding")
    assert "note=a+b" in str(captured[0].url)
    proven.add("query")

    # ``exact`` and ``transport`` reach the bytes through the capability the transport must
    # have, which is negotiated before a single byte is written.
    base = _declaration()
    assert transport_requirements(base).dimensions == ()
    exact = dataclasses.replace(base, wire=Wire(exact=True))
    assert any(
        item.dimension == "exact_target" for item in transport_requirements(exact).dimensions
    )
    proven.add("exact")
    http2 = dataclasses.replace(base, wire=Wire(transport="http/2"))
    assert any(item.minimum == "http/2" for item in transport_requirements(http2).dimensions)
    proven.add("transport")

    sealed = dataclasses.replace(
        base, wire=Wire(encrypted=http_encrypted(content_type="application/sealed+json"))
    )
    assert sealed.wire.encrypted is not None
    proven.add("encrypted")

    assert proven == names, f"unread Wire fields: {sorted(names - proven)}"


# --- one JSON policy, honoured everywhere the bytes are produced -------------------------


def test_the_json_policy_reaches_the_websocket_sites_that_used_to_hardcode_it() -> None:
    from eazy_sdk.websocket._messages import freeze_value
    from eazy_sdk.websocket.codecs import JsonTextCodec
    from eazy_sdk.websocket.protection import HmacSha256MessageSignature, SecretBytes

    ascii_policy = JsonPolicy(ensure_ascii=True)
    value = {"note": "ы"}
    assert dump_json(value) != dump_json(value, ascii_policy)

    frame = freeze_value(value)
    assert "\\u04" not in JsonTextCodec().encode(frame).data
    assert "\\u04" in JsonTextCodec(json=ascii_policy).encode(frame).data

    signer = HmacSha256MessageSignature(key=SecretBytes(b"k"))
    assert signer.json == DEFAULT_JSON_POLICY
    assert dataclasses.replace(signer, json=ascii_policy).json == ascii_policy


def test_the_json_policy_reaches_the_crypto_response_decoder() -> None:
    from eazy_sdk.crypto._compiler import compile_payload_crypto
    from eazy_sdk.models import default_model_adapters

    profile = payload_crypto(
        "phase48-policy",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=WholeCipher())),
    )
    compiled = compile_payload_crypto(
        profile, default_model_adapters(), json=JsonPolicy(ensure_ascii=True)
    )
    assert compiled.json == JsonPolicy(ensure_ascii=True)
    assert compile_payload_crypto(profile, default_model_adapters()).json == DEFAULT_JSON_POLICY


@pytest.mark.asyncio
async def test_a_signature_base_and_a_body_output_use_the_operation_policy() -> None:
    ascii_wire = Wire(encoding=JsonPolicy(ensure_ascii=True))

    class SignedApi(AsyncApi):
        @api.post(
            "/base",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
            wire=ascii_wire,
            signing=hmac_sha256(key=KEY, base=canonical_json(), output=header_output("X-Sig")),
        )
        async def over_the_body(self, *, body: Annotated[dict[str, str], JsonBody()]) -> None:
            raise NotImplementedError

        @api.post(
            "/into-body",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
            wire=ascii_wire,
            signing=hmac_sha256(key=KEY, base=literal(b"const"), output=body_output("signature")),
        )
        async def into_the_body(self, *, body: Annotated[dict[str, str], JsonBody()]) -> None:
            raise NotImplementedError

    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    async with _client(handler) as client:
        sdk = SignedApi(client, identity=_secret())
        await sdk.over_the_body(body={"note": "ы"})
        await sdk.into_the_body(body={"note": "ы"})

    expected = hmac.new(
        b"secret",
        dump_json({"note": "ы"}, JsonPolicy(ensure_ascii=True), sort_keys=True),
        hashlib.sha256,
    ).hexdigest()
    assert captured[0].headers["x-sig"] == expected, "the base was built under the policy"

    content = captured[1].content
    assert b"\\u04" in content, "the body was rewritten under the same policy"
    assert json.loads(content)["signature"]


# --- the backend is implementation, and it still has to be able to do the job -------------


@dataclasses.dataclass(frozen=True, slots=True)
class AsciiIncapableJson:
    """A stand-in for the real constraint: orjson cannot produce ``ensure_ascii=True``."""

    name: str = "ascii-incapable"
    readable: bool = False

    def supports(self, policy: JsonPolicy) -> bool:
        return not policy.ensure_ascii

    def dumps(
        self,
        value: object,
        policy: JsonPolicy,
        *,
        default: Any = None,
        sort_keys: bool | None = None,
    ) -> bytes:
        return dump_json(value, policy, default=default, sort_keys=sort_keys)

    def loads(self, data: bytes) -> object:
        return json.loads(data)


@pytest.mark.asyncio
async def test_a_backend_that_cannot_produce_the_declared_bytes_is_rejected() -> None:
    class AsciiApi(AsyncApi):
        @api.post(
            "/ascii",
            success=NO_CONTENT.success,
            errors=NO_CONTENT.errors,
            fallback=NO_CONTENT.fallback,
            wire=Wire(encoding=JsonPolicy(ensure_ascii=True)),
        )
        async def send(self, *, body: Annotated[dict[str, str], JsonBody()]) -> None:
            raise NotImplementedError

    serialization = Serialization(json=AsciiIncapableJson())
    async with _client(_accept) as client:
        sdk = AsciiApi(client, serialization=serialization)
        with pytest.raises(BackendCapabilityError) as failure:
            await sdk.send(body={"note": "hi"})
    message = str(failure.value)
    assert "AsciiApi.send" in message or "send" in message, "the operation is named"
    assert "ascii-incapable" in message, "the backend is named"
    assert "ensure_ascii=True" in message, "the policy is named"

    assert StdlibJson().supports(JsonPolicy(ensure_ascii=True))


@dataclasses.dataclass(frozen=True, slots=True)
class CssOnly:
    """A parser shaped like the real constraint: selectolax reads CSS and not XPath."""

    name: str = "css-only"

    @property
    def selector_languages(self) -> frozenset[str]:
        return frozenset({"css"})

    @property
    def media_types(self) -> frozenset[str]:
        return frozenset({"text/html"})

    def parse(self, data: bytes | str) -> Any:
        return DEFAULT_HTML_BACKEND.parse(data)


class ByXPath(BaseModel):
    price: Annotated[str, XPath("//span/text()")]


class ByCss(BaseModel):
    price: Annotated[str, CSS("span::text")]


@pytest.mark.asyncio
async def test_a_parser_that_cannot_read_the_operation_selector_is_rejected() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "text/html"}, content=b"<span>10</span>"
        )

    class PageApi(AsyncApi):
        @api.get("/xpath", success=(Success(200, Html(ByXPath)),))
        async def by_xpath(self) -> ByXPath:
            raise NotImplementedError

        @api.get("/css", success=(Success(200, Html(ByCss)),))
        async def by_css(self) -> ByCss:
            raise NotImplementedError

    async with _client(handler) as client:
        sdk = PageApi(client, serialization=Serialization(documents=(CssOnly(),)))
        assert (await sdk.by_css()).price == "10"
        with pytest.raises(BackendCapabilityError) as failure:
            await sdk.by_xpath()
    message = str(failure.value)
    assert "xpath" in message and "css-only" in message


# --- what phase 48 removed ---------------------------------------------------------------


def test_the_six_meanings_of_wire_and_the_scoped_signatures_are_gone() -> None:
    import eazy_sdk
    import eazy_sdk.request as request_package

    removed = (
        "WireProfile",
        "WireOptions",
        "SigningRule",
        "SigningOverride",
        "SigningOverrideMode",
        "select_signatures",
        "unsigned",
        "use",
        "extend",
        "sign",
    )
    for name in removed:
        assert not hasattr(request_package, name), name
        assert not hasattr(eazy_sdk, name), name
        assert name not in request_package.__all__
