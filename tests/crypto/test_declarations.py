from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import msgspec
import pytest
from pydantic import BaseModel, ConfigDict, Field

from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoContext,
    CryptoDirection,
    CryptoLimitError,
    CryptoRegistry,
    CryptoRule,
    CryptoStage,
    EncryptField,
    FrozenValue,
    HttpCryptoContext,
    PayloadCrypto,
    PayloadDecryptionError,
    decrypt_field,
    decrypt_inbound,
    encrypt_encoded,
    encrypt_field,
    encrypt_outbound,
    freeze_value,
    http_crypto_scope,
    payload_crypto,
    thaw_value,
)
from eazy_sdk.crypto._runtime import (
    compile_payload_crypto,
    decrypt_document,
    encrypt_bytes,
    encrypt_document,
)
from eazy_sdk.models import default_model_adapters


class Card(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    number: str = Field(alias="pan")
    cvv: str


class Payment(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    card: Card = Field(alias="payment_card")


@dataclass
class DataclassCard:
    number: str


@dataclass
class DataclassPayment:
    card: DataclassCard


class MsgspecCard(msgspec.Struct, rename={"number": "pan"}):
    number: str


class MsgspecPayment(msgspec.Struct, rename={"card": "payment_card"}):
    card: MsgspecCard


@dataclass(frozen=True)
class PrefixCipher:
    name: str = "prefix-test-only"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert context.path is not None
        return "enc:" + str(value)

    def decrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert context.path is not None
        assert isinstance(value, str)
        return value.removeprefix("enc:")


def profile() -> PayloadCrypto:
    cipher = PrefixCipher()
    return payload_crypto(
        "payments-v1",
        outbound=encrypt_outbound(
            encrypt_field(Payment, lambda body: body.card.number, using=cipher),
            encrypt_field(Payment, lambda body: body.card.cvv, using=cipher),
        ),
        inbound=decrypt_inbound(
            decrypt_field(Payment, lambda body: body.card.number, using=cipher),
            decrypt_field(Payment, lambda body: body.card.cvv, using=cipher),
        ),
    )


def test_profile_is_hashable_and_repr_does_not_expose_algorithm_state() -> None:
    declared = profile()

    assert hash(declared) == hash(declared)
    assert "PrefixCipher" not in repr(declared)
    assert "prefix-test-only" not in repr(declared)


@pytest.mark.asyncio
async def test_encoded_limits_are_checked_before_and_after_algorithm() -> None:
    calls = 0

    class ExpandingCipher:
        name = "expanding-test-only"

        def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
            nonlocal calls
            calls += 1
            return value * 4

    context = HttpCryptoContext(
        "pay",
        "limits",
        "pending",
        CryptoDirection.OUTBOUND,
        CryptoStage.ENCODED,
        1,
    )
    with pytest.raises(CryptoLimitError, match="input"):
        await encrypt_bytes(
            b"1234",
            encrypt_encoded(using=ExpandingCipher(), max_input_bytes=3),
            context=context,
        )
    assert calls == 0
    with pytest.raises(CryptoLimitError, match="output"):
        await encrypt_bytes(
            b"1234",
            encrypt_encoded(using=ExpandingCipher(), max_output_bytes=8),
            context=context,
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_selector_resolves_model_aliases_and_transforms_before_model_load() -> None:
    declared = profile()
    compiled = compile_payload_crypto(
        declared,
        default_model_adapters(),
        outbound_model=Payment,
        inbound_models=(Payment,),
    )
    assert tuple(item.wire_path for item in compiled.outbound_fields) == (
        ("payment_card", "pan"),
        ("payment_card", "cvv"),
    )
    context = HttpCryptoContext(
        "pay",
        "payments-v1",
        "pending",
        CryptoDirection.OUTBOUND,
        CryptoStage.DOCUMENT,
        1,
        method="POST",
        authority="api.test",
    )
    source = freeze_value({"payment_card": {"pan": "4111", "cvv": "123"}})
    encrypted = await encrypt_document(source, compiled.outbound_fields, context=context)
    assert thaw_value(encrypted) == {
        "payment_card": {"pan": "enc:4111", "cvv": "enc:123"}
    }
    decrypted = await decrypt_document(
        encrypted,
        compiled.inbound_fields,
        context=HttpCryptoContext(
            "pay",
            "payments-v1",
            "pending",
            CryptoDirection.INBOUND,
            CryptoStage.DOCUMENT,
            1,
        ),
    )
    assert default_model_adapters().load(Payment, thaw_value(decrypted)).card.number == "4111"


@pytest.mark.parametrize(
    ("model", "declaration", "expected"),
    [
        (
            DataclassPayment,
            lambda cipher: encrypt_field(
                DataclassPayment, lambda body: body.card.number, using=cipher
            ),
            ("card", "number"),
        ),
        (
            MsgspecPayment,
            lambda cipher: encrypt_field(
                MsgspecPayment, lambda body: body.card.number, using=cipher
            ),
            ("payment_card", "pan"),
        ),
    ],
)
def test_selector_uses_every_first_party_model_adapter(
    model: type[object],
    declaration: Callable[[PrefixCipher], EncryptField],
    expected: tuple[str, ...],
) -> None:
    cipher = PrefixCipher()
    field = declaration(cipher)
    declared = payload_crypto("adapter", outbound=encrypt_outbound(field))

    compiled = compile_payload_crypto(
        declared,
        default_model_adapters(),
        outbound_model=model,
    )

    assert compiled.outbound_fields[0].wire_path == expected


def test_selector_rejects_calls_and_overlapping_paths() -> None:
    cipher = PrefixCipher()
    with pytest.raises(CryptoConfigurationError, match="method calls"):
        encrypt_field(Payment, lambda body: body.card.model_dump(), using=cipher)
    with pytest.raises(CryptoConfigurationError, match="overlap"):
        encrypt_outbound(
            encrypt_field(Payment, lambda body: body.card, using=cipher),
            encrypt_field(Payment, lambda body: body.card.number, using=cipher),
        )


def test_compiler_rejects_algorithm_without_required_method() -> None:
    class InvalidCipher:
        name = "invalid-test-only"

    declared = payload_crypto(
        "invalid",
        outbound=encrypt_outbound(
            encrypt_field(Payment, lambda body: body.card.number, using=InvalidCipher())  # type: ignore[arg-type]
        ),
    )

    with pytest.raises(CryptoConfigurationError, match="no callable encrypt"):
        compile_payload_crypto(
            declared,
            default_model_adapters(),
            outbound_model=Payment,
        )


def test_compiler_rejects_unknown_field_and_wrong_operation_model() -> None:
    cipher = PrefixCipher()
    unknown = payload_crypto(
        "unknown",
        outbound=encrypt_outbound(
            encrypt_field(Payment, lambda body: body.card.nubmer, using=cipher)  # type: ignore[attr-defined]
        ),
    )
    with pytest.raises(CryptoConfigurationError, match="unknown crypto selector field"):
        compile_payload_crypto(
            unknown,
            default_model_adapters(),
            outbound_model=Payment,
        )

    with pytest.raises(CryptoConfigurationError, match="does not match operation model"):
        compile_payload_crypto(
            profile(),
            default_model_adapters(),
            outbound_model=DataclassPayment,
        )


def test_registry_is_deterministic_and_ambiguous_rules_fail_closed() -> None:
    declared = profile()
    scope = http_crypto_scope(hosts=("api.test",), path_prefixes=("/private",))
    registry = CryptoRegistry((CryptoRule(declared, scope, 1),))
    resolved = registry.resolve_http(
        host="API.TEST",
        path="/private/payments",
        method="POST",
        operation_id="pay",
    )
    assert resolved is not None
    assert resolved.profile.name == "payments-v1"
    ambiguous = CryptoRegistry(
        (
            CryptoRule(declared, scope, 1),
            CryptoRule(declared, scope, 1),
        )
    )
    with pytest.raises(CryptoConfigurationError, match="ambiguous HTTP"):
        ambiguous.resolve_http(
            host="api.test",
            path="/private/payments",
            method="POST",
            operation_id="pay",
        )


def test_registry_rule_can_limit_profile_direction() -> None:
    declared = profile()
    registry = CryptoRegistry(
        (
            CryptoRule(
                declared,
                http_crypto_scope(operation_ids=("pay",)),
                directions=frozenset((CryptoDirection.OUTBOUND,)),
            ),
        )
    )

    resolved = registry.resolve_http(
        host="api.test",
        path="/payments",
        method="POST",
        operation_id="pay",
    )

    assert resolved is not None
    assert resolved.profile.outbound is declared.outbound
    assert resolved.profile.inbound is None


def test_registry_rejects_same_profile_name_with_different_algorithm_identity() -> None:
    first = profile()
    second_cipher = PrefixCipher(name="second-test-only")
    second = payload_crypto(
        first.name,
        outbound=encrypt_outbound(
            encrypt_field(Payment, lambda body: body.card.number, using=second_cipher)
        ),
    )

    with pytest.raises(CryptoConfigurationError, match="conflicting definitions"):
        CryptoRegistry(
            (
                CryptoRule(first, http_crypto_scope(hosts=("one.test",))),
                CryptoRule(second, http_crypto_scope(hosts=("two.test",))),
            )
        )


@pytest.mark.asyncio
async def test_algorithm_errors_are_sanitized_without_cause() -> None:
    class LeakingCipher:
        name = "leaking-test-only"

        def decrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
            raise RuntimeError("plaintext=secret key=secret")

    declared = payload_crypto(
        "safe-errors",
        inbound=decrypt_inbound(
            decrypt_field(Payment, lambda body: body.card.number, using=LeakingCipher())
        ),
    )
    compiled = compile_payload_crypto(
        declared,
        default_model_adapters(),
        inbound_models=(Payment,),
    )
    with pytest.raises(PayloadDecryptionError) as captured:
        await decrypt_document(
            freeze_value({"payment_card": {"pan": "ciphertext", "cvv": "123"}}),
            compiled.inbound_fields,
            context=HttpCryptoContext(
                "pay",
                "safe-errors",
                "pending",
                CryptoDirection.INBOUND,
                CryptoStage.DOCUMENT,
                1,
            ),
        )
    assert captured.value.__cause__ is None
    assert "secret" not in str(captured.value)
