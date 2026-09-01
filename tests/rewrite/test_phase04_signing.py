from __future__ import annotations

import dataclasses
import hashlib
import hmac
from dataclasses import dataclass

import pytest
from zapros import Client as ZaprosClient

from eazy_sdk._internal import (
    CompiledContract,
    GraphError,
    InputField,
    OperationIdentity,
    OperationValues,
    RequestLocation,
    RequestScope,
    ScopeContext,
    compile_endpoint,
)
from eazy_sdk.handlers import EmitOptions, ZaprosSyncEmitter
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import (
    DeclarativeSignature,
    Header,
    HmacSha256,
    JsonBody,
    Path,
    Query,
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    body_output,
    cookie_output,
    extend,
    header,
    header_output,
    hmac_sha256,
    join,
    literal,
    method,
    previous_signature,
    query,
    query_output,
    sign,
    target,
    use,
)
from eazy_sdk.request import (
    unsigned as unsigned_signing,
)
from eazy_sdk.request.prepared import (
    BufferedBody,
    RequestPreparer,
    UnsignedPreparedRequest,
)
from eazy_sdk.request.signatures import (
    SignatureIdentity,
    SignatureOutput,
    SignaturePlan,
    SignatureResult,
    SigningInput,
    compile_signatures,
    custom_signature,
    reserve_outputs,
    select_signatures,
    sign_prepared,
    whole_prepared_request,
)
from tests._support.raw_capture import RawCaptureServer

KEY = SigningKeyRequirement("payment-key")


@dataclass(frozen=True)
class Contract:
    operation_id: str = "createPayment"
    method: str = "POST"
    path: str = "/payments/{payment_id}"
    input_fields: tuple[InputField, ...] = (
        InputField("payment_id", "payment_id", str, True, RequestLocation.PATH, Path("payment_id")),
        InputField(
            "filter",
            "filter",
            list[str],
            False,
            RequestLocation.QUERY,
            Query("filter", explode=False),
        ),
        InputField(
            "timestamp", "X-Timestamp", str, True, RequestLocation.HEADER, Header("X-Timestamp")
        ),
        InputField("body", "body", object, True, RequestLocation.BODY, JsonBody()),
    )
    responses: object = "responses"


def unsigned(
    plan: SignaturePlan,
    *,
    base_url: str = "https://api.example",
    body: object = None,
    query_values: dict[str, object] | None = None,
) -> UnsignedPreparedRequest:
    compiled: CompiledContract[object] = compile_endpoint(Contract())
    arguments = compiled.bind_input(
        {
            "payment_id": "pay_42",
            "filter": (query_values or {"filter": ["z", "a"]})["filter"],
            "timestamp": "1700000000",
            "body": body if body is not None else {"z": 1, "a": 2},
        }
    )
    values = OperationValues.from_bound(compiled.plan.shape, arguments)
    return RequestPreparer(base_url).prepare(
        compiled, values, reserved_outputs=reserve_outputs(plan)
    )


def provider(_requirement: SigningKeyRequirement) -> SigningKey:
    return SigningKey(b"secret")


def test_hmac_golden_vector_reads_exact_prepared_target_header_and_body_digest() -> None:
    signature = hmac_sha256(
        key=KEY,
        base=join(
            method(),
            target(),
            header("X-Timestamp"),
            body_digest("sha256", encoding="hex"),
            separator=b"\n",
        ),
        output=header_output("X-Signature", encoding="hex", position=1),
    )
    plan = compile_signatures((signature,))
    prepared = sign_prepared(unsigned(plan), plan, provider)
    assert isinstance(prepared.body, BufferedBody)
    base = b"\n".join(
        (
            b"POST",
            b"/payments/pay_42?filter=z%2Ca",
            b"1700000000",
            hashlib.sha256(prepared.body.content).hexdigest().encode(),
        )
    )
    expected = hmac.new(b"secret", base, hashlib.sha256).hexdigest().encode()
    assert prepared.headers[1].name == b"X-Signature"
    assert prepared.headers[1].value == expected


def test_wire_query_order_and_canonical_projection_are_independent() -> None:
    signature = hmac_sha256(
        key=KEY,
        base=query(include=("filter",), order="canonical"),
        output=header_output("X-Signature"),
    )
    plan = compile_signatures((signature,))
    prepared = sign_prepared(unsigned(plan), plan, provider)
    assert prepared.target.endswith(b"filter=z%2Ca")
    expected = hmac.new(b"secret", b"filter=z%2Ca", hashlib.sha256).hexdigest().encode()
    assert next(item.value for item in prepared.headers if item.name == b"X-Signature") == expected


def test_query_cookie_and_json_outputs_fill_reserved_positions() -> None:
    query_signature = hmac_sha256(
        key=KEY,
        base=method(),
        output=query_output("signature", position=1),
        name="query",
    )
    cookie_signature = hmac_sha256(
        key=KEY,
        base=method(),
        output=cookie_output("sig", position=0),
        name="cookie",
    )
    body_signature = hmac_sha256(
        key=KEY,
        base=method(),
        output=body_output("signature", position=1),
        name="body",
    )
    plan = compile_signatures((query_signature, cookie_signature, body_signature))
    prepared = sign_prepared(unsigned(plan), plan, provider)
    digest = hmac.new(b"secret", b"POST", hashlib.sha256).hexdigest().encode()
    assert prepared.target == b"/payments/pay_42?filter=z%2Ca&signature=" + digest
    cookie = next(item.value for item in prepared.headers if item.name == b"Cookie")
    assert cookie == b"sig=" + digest
    assert isinstance(prepared.body, BufferedBody)
    assert prepared.body.content == b'{"z":1,"signature":"' + digest + b'","a":2}'


def test_dependent_signatures_are_topologically_sorted_and_cycles_are_rejected() -> None:
    first_id = SignatureIdentity("first")
    second_id = SignatureIdentity("second")
    first = DeclarativeSignature(
        first_id,
        HmacSha256(),
        literal(b"one"),
        (header_output("X-First"),),
        KEY,
    )
    second = DeclarativeSignature(
        second_id,
        HmacSha256(),
        join(previous_signature(first_id), literal(b"two"), separator=b":"),
        (header_output("X-Second"),),
        KEY,
    )
    plan = compile_signatures((second, first))
    assert [item.identity.name for item in plan.signatures] == ["first", "second"]
    prepared = sign_prepared(unsigned(plan), plan, provider)
    first_raw = hmac.new(b"secret", b"one", hashlib.sha256).digest()
    second_expected = hmac.new(b"secret", first_raw + b":two", hashlib.sha256).hexdigest()
    actual = next(item.value.decode() for item in prepared.headers if item.name == b"X-Second")
    assert actual == second_expected

    cycle_a = DeclarativeSignature(
        first_id,
        HmacSha256(),
        previous_signature(second_id),
        (header_output("X-A"),),
        KEY,
    )
    cycle_b = DeclarativeSignature(
        second_id,
        HmacSha256(),
        previous_signature(first_id),
        (header_output("X-B"),),
        KEY,
    )
    with pytest.raises(GraphError, match=r"first.*second.*first|second.*first.*second"):
        compile_signatures((cycle_a, cycle_b))


def test_self_signing_target_and_body_cycles_fail_before_key_provider() -> None:
    calls = 0
    signature = hmac_sha256(
        key=KEY,
        base=target(),
        output=query_output("signature"),
    )
    with pytest.raises(GraphError, match="self-signing cycle"):
        compile_signatures((signature,))
    assert calls == 0


class CustomSigner:
    def __init__(self, output: SignatureOutput) -> None:
        self.output = output

    def sign(self, signing: SigningInput, key: SigningKey | None) -> SignatureResult:
        assert key is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            signing.request.target = b"/mutated"  # type: ignore[misc]
        return SignatureResult({self.output: signing.request.method + b":" + key.reveal()})


def test_named_custom_signer_gets_frozen_view_and_only_declared_outputs() -> None:
    output = header_output("X-Custom", encoding="raw")
    signature = custom_signature(
        signer=CustomSigner(output),
        reads=whole_prepared_request(),
        outputs=(output,),
        key=KEY,
        name="vendor",
    )
    plan = compile_signatures((signature,))
    prepared = sign_prepared(unsigned(plan), plan, provider)
    value = next(item.value for item in prepared.headers if item.name == b"X-Custom")
    assert value == b"POST:secret"
    assert "secret" not in repr(SigningKey(b"secret"))
    assert "secret" not in repr(SigningInput(prepared.view, {}, 0))  # type: ignore[arg-type]


def test_capture_server_recomputes_signature_from_emitted_bytes() -> None:
    signature = hmac_sha256(
        key=KEY,
        base=join(method(), target(), body_digest(), separator=b"\n"),
        output=header_output("X-Signature"),
    )
    plan = compile_signatures((signature,))
    raw_client = ZaprosClient(handler=HttpxHandler())
    emitter = ZaprosSyncEmitter(raw_client)
    try:
        with RawCaptureServer() as server:
            prepared = sign_prepared(unsigned(plan, base_url=server.url), plan, provider)
            emitter(prepared, options=EmitOptions())
        capture = server.capture
        assert capture is not None
        target_bytes = capture.request_line.split(b" ", 2)[1]
        base = b"\n".join(
            (b"POST", target_bytes, hashlib.sha256(capture.body).hexdigest().encode())
        )
        expected = hmac.new(b"secret", base, hashlib.sha256).hexdigest().encode()
        assert b"X-Signature: " + expected in capture.header_lines
    finally:
        raw_client.close()


def test_api_group_scoped_and_endpoint_signing_precedence_is_explicit() -> None:
    api_signature = hmac_sha256(key=KEY, base=method(), output=header_output("X-Api"), name="api")
    group_signature = hmac_sha256(
        key=KEY, base=method(), output=header_output("X-Group"), name="group"
    )
    endpoint_signature = hmac_sha256(
        key=KEY, base=method(), output=header_output("X-Endpoint"), name="endpoint"
    )
    scoped_signature = hmac_sha256(
        key=KEY, base=method(), output=header_output("X-Scoped"), name="scoped"
    )
    context = ScopeContext(
        "https", "api.example", "/payments/1", "POST", OperationIdentity("createPayment")
    )
    rules = (
        sign(
            scoped_signature,
            scope=RequestScope(hosts=frozenset({"api.example"})),
        ),
    )
    assert select_signatures(context=context, api_default=(api_signature,), rules=rules) == (
        api_signature,
    )
    assert select_signatures(
        context=context,
        group_default=(group_signature,),
        api_default=(api_signature,),
        rules=rules,
    ) == (group_signature,)
    assert select_signatures(
        context=context,
        endpoint=use(endpoint_signature),
        group_default=(group_signature,),
    ) == (endpoint_signature,)
    assert select_signatures(
        context=context,
        endpoint=extend(endpoint_signature),
        group_default=(group_signature,),
    ) == (group_signature, endpoint_signature)
    assert (
        select_signatures(
            context=context,
            endpoint=unsigned_signing(),
            group_default=(group_signature,),
        )
        == ()
    )
    assert select_signatures(context=context, rules=rules) == (scoped_signature,)
