"""Phase 51.7: a response's body is parsed through the configured backend, when it is safe to.

``ResponseContext.json`` used to hardcode ``json.loads`` regardless of what backend the SDK
configured for JSON (plan §1, F4). That misses whatever a configured backend would have been
faster at, but the fix is not "always use the configured backend": a backend can encode correctly
and still decode lossily -- ``orjson`` represents an integer wider than 64 bits as a Python
``float``, silently, for every response it reads. ``JsonBackend.readable`` is where a backend says
whether it is safe to parse a response with; a backend that does not declare itself readable is
never used for that, no matter what it is configured for writing.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from eazy_sdk.request.wire import JsonPolicy
from eazy_sdk.response import ResponseContext
from eazy_sdk.response.normalized import NormalizedResponse
from eazy_sdk.serialization import Serialization

BASE = "https://read-backend.test"

BIG_INT = 12345678901234567890123  # wider than 64 bits; a float would round it


def _response(body: bytes, *, serialization: Serialization) -> ResponseContext[object]:
    raw = httpx.Response(200, content=body, headers={"content-type": "application/json"})
    return ResponseContext(
        NormalizedResponse(
            status_code=raw.status_code,
            url=f"{BASE}/thing",
            method="GET",
            headers=tuple(raw.headers.items()),
            body=raw.content,
            raw_response=raw,
        ),
        serialization=serialization,
    )


@dataclass(frozen=True, slots=True)
class LossyJson:
    """A stand-in for orjson: correct to write, but represents a huge int as a float."""

    name: str = "lossy"
    readable: bool = False

    def supports(self, policy: JsonPolicy) -> bool:
        return True

    def dumps(
        self,
        value: object,
        policy: JsonPolicy,
        *,
        default: object | None = None,
        sort_keys: bool | None = None,
    ) -> bytes:
        import json

        return json.dumps(value).encode()

    def loads(self, data: bytes) -> object:
        return {"value": float(BIG_INT)}  # what a naive big-int-as-float reader would return


@dataclass(frozen=True, slots=True)
class TrackedStdlibJson:
    """The stdlib backend, wearing a name that proves it -- not a hardcoded module -- read."""

    name: str = "tracked-stdlib"
    readable: bool = True

    def supports(self, policy: JsonPolicy) -> bool:
        return True

    def dumps(
        self,
        value: object,
        policy: JsonPolicy,
        *,
        default: object | None = None,
        sort_keys: bool | None = None,
    ) -> bytes:
        import json

        return json.dumps(value).encode()

    def loads(self, data: bytes) -> object:
        import json

        return {"tracked": True, **json.loads(data)}


def test_the_default_backend_reads_an_integer_wider_than_64_bits_exactly() -> None:
    body = f'{{"value": {BIG_INT}}}'.encode()

    context = _response(body, serialization=Serialization())

    assert context.json.value == {"value": BIG_INT}


def test_a_backend_that_declares_itself_unreadable_is_not_used_to_parse_a_response() -> None:
    """The backend can still be configured for writing; parsing falls back to the stdlib."""

    body = f'{{"value": {BIG_INT}}}'.encode()

    context = _response(body, serialization=Serialization(json=LossyJson()))

    assert context.json.value == {"value": BIG_INT}


def test_a_readable_backend_is_used_to_parse_the_response() -> None:
    """The configured backend does run, proven by a marker only it adds."""

    context = _response(b'{"value": 1}', serialization=Serialization(json=TrackedStdlibJson()))

    assert context.json.value == {"tracked": True, "value": 1}
