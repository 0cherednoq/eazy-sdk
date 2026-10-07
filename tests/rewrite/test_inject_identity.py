"""``Inject`` values stay in their own slots across operations, calls and attempts."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx

from eazy_sdk import (
    AsyncApi,
    ClientConfig,
    Http,
    HttpOperation,
    Identity,
    Inject,
    Resilience,
    RetryPolicy,
    op,
)
from eazy_sdk.dependencies import (
    DependencyCachePolicy,
    DependencyRegistry,
    RequestDependency,
)
from eazy_sdk.request import SigningKey, SigningKeyRequirement, header_output, hmac_sha256, target
from eazy_sdk.request.markers import Query
from tests._support.zapros_clients import client_from_httpx


def _const(name: str, value: str) -> Inject:
    return Inject(Query(name), value)


@dataclass(frozen=True, slots=True, kw_only=True)
class First(HttpOperation[dict[str, str]]):
    __http__ = Http.get("/first", inject=(_const("a", "A1"), _const("b", "B1"), _const("c", "C1")))


@dataclass(frozen=True, slots=True, kw_only=True)
class Second(HttpOperation[dict[str, str]]):
    __http__ = Http.get("/second", inject=(_const("x", "X2"), _const("y", "Y2")))


class EchoApi(AsyncApi):
    first = op(First)
    second = op(Second)


def _echo(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=dict(request.url.params))


def _client(handler: Any, *, attempts: int = 1) -> Any:
    return client_from_httpx(
        httpx.AsyncClient(
            base_url="https://example.test",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            resilience=Resilience(retry=RetryPolicy.safe(max_attempts=attempts), auth_retries=0)
        ),
    )


async def test_inject_values_never_cross_operations() -> None:
    # Descriptors of a finished attempt are freed and their addresses reused at once, so a
    # registry keyed by ``id()`` handed the next operation a stale provider.
    registry = DependencyRegistry()
    client = _client(_echo)
    api = EchoApi(client, identity=Identity(dependencies=registry))

    for _ in range(200):
        assert await api.first() == {"a": "A1", "b": "B1", "c": "C1"}
        assert await api.second() == {"x": "X2", "y": "Y2"}
    await client.aclose()

    assert registry._providers == {}


def test_registry_keeps_a_registered_dependency_alive() -> None:
    registry = DependencyRegistry()
    registry.register(RequestDependency.typed("short-lived", str), lambda: "stale")

    # Whatever address the next descriptor lands on, it is not the registered one.
    for _ in range(100):
        assert registry.provider(RequestDependency.typed("fresh", str)) is None
    assert len(registry._providers) == 1


async def test_inject_cache_policy_holds_across_retries() -> None:
    counters = {"call": 0, "attempt": 0}

    def count(name: str) -> Any:
        def source() -> str:
            counters[name] += 1
            return f"{name}-{counters[name]}"

        return source

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Flaky(HttpOperation[dict[str, str]]):
        __http__ = Http.get(
            "/flaky",
            inject=(
                Inject(Query("call"), count("call"), cache=DependencyCachePolicy.CALL),
                Inject(Query("attempt"), count("attempt")),
                _const("fixed", "F"),
            ),
        )

    class FlakyApi(AsyncApi):
        flaky = op(Flaky)

    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(503 if len(seen) % 3 else 200, json=seen[-1])

    client = _client(handler, attempts=3)
    api = FlakyApi(client)
    first = await api.flaky()
    second = await api.flaky()
    await client.aclose()

    assert first == {"call": "call-1", "attempt": "attempt-3", "fixed": "F"}
    assert second == {"call": "call-2", "attempt": "attempt-6", "fixed": "F"}
    assert [item["call"] for item in seen] == ["call-1"] * 3 + ["call-2"] * 3
    assert [item["attempt"] for item in seen] == [f"attempt-{n}" for n in range(1, 7)]


async def test_every_attempt_signs_the_value_it_injects() -> None:
    ticks = 0

    def tick() -> str:
        nonlocal ticks
        ticks += 1
        return str(ticks)

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Signed(HttpOperation[dict[str, str]]):
        __http__ = Http.get(
            "/signed",
            inject=(Inject(Query("ts"), tick), _const("fixed", "F")),
            signing=(
                hmac_sha256(
                    key=SigningKeyRequirement("inject-key"),
                    base=target(),
                    output=header_output("X-Signature"),
                ),
            ),
        )

    class SignedApi(AsyncApi):
        signed = op(Signed)

    sent: list[tuple[str, str]] = []
    expected: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.params["ts"], request.headers["X-Signature"]))
        expected.append(hmac.new(b"secret", request.url.raw_path, hashlib.sha256).hexdigest())
        return httpx.Response(503 if len(sent) < 3 else 200, json=dict(request.url.params))

    client = _client(handler, attempts=3)
    api = SignedApi(client, identity=Identity(key_provider=lambda _: SigningKey("secret")))
    result = await api.signed()
    await client.aclose()

    assert result == {"ts": "3", "fixed": "F"}
    assert [ts for ts, _ in sent] == ["1", "2", "3"]
    assert [signature for _, signature in sent] == expected
    assert len(set(expected)) == 3
