"""Phase 44: the client config is grouped by owner and declares no serialization."""

from __future__ import annotations

import dataclasses
import inspect
from dataclasses import FrozenInstanceError
from typing import Annotated, Any

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import (
    Client,
    ClientConfig,
    Hooks,
    Json,
    Path,
    Resilience,
    Responses,
    RetryPolicy,
    Security,
    Serialization,
    Success,
    SyncApi,
    SyncRoot,
    api,
    api_group,
)
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
from eazy_sdk.protection import Guard, GuardSolution, SolveContext, host
from eazy_sdk.protection.advanced import ProtectionBundle
from eazy_sdk.request import JsonBody
from eazy_sdk.response import ResponseContext

BASE = "https://config.test"


class Echo(BaseModel):
    ok: bool


ECHO: Responses[Echo] = Responses(success=(Success(200, Json(Echo)),))


class Payload(BaseModel):
    value: str


class EchoApi(SyncApi):
    @api.post("/echo", responses=ECHO)
    def send(self, *, body: Annotated[Payload, JsonBody()]) -> Echo:
        raise NotImplementedError

    @api.get("/echo/{item_id}", responses=ECHO)
    def get(self, *, item_id: Annotated[int, Path()]) -> Echo:
        raise NotImplementedError


class EchoSdk(SyncRoot):
    echo = api_group(EchoApi)


class CookieGuard(Guard[int]):
    scope = host("config.test")

    def detect(self, response: ResponseContext[object]) -> int | None:
        return None

    def solve(self, challenge: int, context: SolveContext) -> GuardSolution:
        return self.solution(cookies={"c": "v"})


def _client(bodies: list[bytes], config: ClientConfig | None = None) -> Client:
    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200, json={"ok": True})

    raw = httpx.Client(transport=httpx.MockTransport(handle))
    return Client(
        base_url=BASE,
        handler=HttpxHandler(raw, owns_client=True),
        config=config,
    )


def test_the_config_is_three_groups_plus_the_crypto_registry() -> None:
    parameters = set(inspect.signature(ClientConfig).parameters)
    assert parameters == {"resilience", "security", "hooks", "crypto"}
    for flat in (
        "retry",
        "auth_retries",
        "max_redirects",
        "timeout",
        "rate_limiter",
        "protection",
        "guards",
        "middleware",
        "models",
        "profile",
        "auth",
        "key_provider",
        "dependencies",
        "observer",
    ):
        assert flat not in parameters, flat


def test_each_group_owns_its_own_validation() -> None:
    with pytest.raises(ValueError, match="budgets cannot be negative"):
        Resilience(auth_retries=-1)
    with pytest.raises(ValueError, match="timeout must be positive"):
        Resilience(timeout=0)
    with pytest.raises(TypeError, match="ProtectionBundle"):
        Security(object())  # type: ignore[arg-type]
    assert isinstance(Security().protection, ProtectionBundle)
    assert Hooks().middleware == ()


def test_the_groups_are_immutable() -> None:
    config = ClientConfig(resilience=Resilience(timeout=5))
    for target, attribute, value in (
        (config, "resilience", Resilience()),
        (config.resilience, "timeout", 1.0),
        (config.security, "protection", ProtectionBundle()),
        (config.hooks, "middleware", ()),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, value)


def test_guards_are_lowered_by_the_factory_without_mutating_a_frozen_object() -> None:
    source = inspect.getsource(inspect.getmodule(ClientConfig))  # type: ignore[arg-type]
    assert "object.__setattr__" not in source

    guard = CookieGuard()
    lowered = Security.of(guard)
    fluent = Security().with_protection(guard)
    assert [policy.identity for policy in lowered.protection.challenge_policies] == ["CookieGuard"]
    assert [policy.identity for policy in fluent.protection.challenge_policies] == ["CookieGuard"]
    assert len(lowered.protection.solver_bindings) == 1
    # A copy of a lowered config must not lower the same guard a second time.
    assert len(ClientConfig(security=lowered).with_protection().bundle.challenge_policies) == 1


def test_call_options_come_from_the_resilience_group() -> None:
    resilience = Resilience(
        retry=RetryPolicy.safe(max_attempts=3),
        auth_retries=2,
        max_redirects=1,
        timeout=7.5,
    )
    options = ClientConfig(resilience=resilience).call_options()
    assert options == resilience.call_options()
    assert options.timeout == 7.5
    assert options.transport_retries == 2
    assert options.auth_retries == 2
    assert options.max_redirects == 1
    assert options.max_attempts == 1 + 2 + 2 + 1


def test_the_default_config_still_emits_the_default_call_options() -> None:
    options = ClientConfig().call_options()
    assert (options.timeout, options.auth_retries, options.max_redirects) == (None, 1, 0)
    assert options.max_attempts == 2


def test_serialization_is_declared_on_the_root_and_changes_the_body() -> None:
    from eazy_sdk.models.adapters import PydanticModelAdapter

    class UpperAdapter(PydanticModelAdapter):
        """A deliberate stand-in: the same model, a different representation."""

        def dump(
            self,
            value: object,
            *,
            mode: Any,
            registry: ModelAdapterRegistry,
        ) -> object:
            dumped = super().dump(value, mode=mode, registry=registry)
            if isinstance(dumped, dict):
                return {key: str(item).upper() for key, item in dumped.items()}
            return dumped

    registry = default_model_adapters().replace_adapter("pydantic", UpperAdapter())

    bodies: list[bytes] = []
    with _client(bodies) as client:
        EchoSdk(client).echo.send(body=Payload(value="quiet"))
        EchoSdk(client, serialization=Serialization(models=registry)).echo.send(
            body=Payload(value="quiet")
        )

    assert bodies == [b'{"value":"quiet"}', b'{"value":"QUIET"}']


def test_a_root_may_declare_serialization_on_the_class() -> None:
    declared = Serialization()

    class DeclaredSdk(EchoSdk):
        serialization = declared

    bodies: list[bytes] = []
    with _client(bodies) as client:
        sdk = DeclaredSdk(client)
        assert sdk._serialization is declared
        assert DeclaredSdk(client, serialization=Serialization())._serialization is not declared


def test_serialization_validates_what_it_accepts() -> None:
    with pytest.raises(TypeError, match="ModelAdapterRegistry"):
        Serialization(models=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="WireProfile"):
        Serialization(profile=object())  # type: ignore[arg-type]


def test_a_client_no_longer_declares_serialization_or_identity() -> None:
    config = ClientConfig()
    for absent in ("models", "profile", "auth", "key_provider", "dependencies", "observer"):
        assert not hasattr(config, absent), absent
    from eazy_sdk.clients.executor import ExecutionRuntime

    runtime_fields = {item.name for item in dataclasses.fields(ExecutionRuntime)}
    for absent in ("models", "profile", "auth", "key_provider", "dependencies", "observer"):
        assert absent not in runtime_fields, absent


def test_hooks_carry_middleware_into_the_runtime() -> None:
    from eazy_sdk.middleware import call_middleware

    seen: list[str] = []

    class Recorder:
        async def __call__(self, context: Any, call_next: Any) -> Any:
            seen.append(context.operation.operation_id)
            return await call_next(context)

    config = ClientConfig(hooks=Hooks(middleware=(call_middleware(Recorder()),)))
    bodies: list[bytes] = []
    with _client(bodies, config) as client:
        EchoApi(client).get(item_id=3)

    assert seen == ["get"]
