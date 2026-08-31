from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast

import pytest

from eazy_sdk._internal import (
    CompiledContract,
    GraphError,
    InputField,
    OperationShape,
    OperationValues,
    PythonTypeValidator,
    RequestLocation,
    SlotCardinality,
    ValueSlot,
    apply_patch_atomic,
    compile_endpoint,
)
from eazy_sdk.auth import (
    ApiKeyScheme,
    AuthScheme,
    BearerScheme,
    ResolutionCycleError,
    all_of,
    any_of,
)
from eazy_sdk.auth.core import AttributeSessionSelector
from eazy_sdk.dependencies import (
    DependencyCachePolicy,
    DependencyRegistry,
    RequestDependency,
)
from eazy_sdk.ext import (
    AttributeSelector,
    AuthLocation,
    AuthPlacement,
    AuthProviderIdentity,
    AuthProviders,
    BindingOperation,
    DependencyCaches,
    LifecycleGraph,
    MemorySessionStore,
    RequestRequirement,
    ResultBinding,
    SessionAuth,
    SessionKey,
    SessionProvider,
    StaticAuthProvider,
    compile_dependency_order,
    resolve_requirements,
    resolve_security,
)
from eazy_sdk.request import Path
from eazy_sdk.storage.session_bridge import RepositorySessionStore


def request_slot[T](
    name: str,
    annotation: object,
    *,
    cardinality: SlotCardinality = SlotCardinality.ONE,
) -> ValueSlot[T]:
    return ValueSlot(
        name,
        PythonTypeValidator(annotation),
        cardinality=cardinality,
    )


@dataclass(frozen=True)
class DeviceContext:
    device_id: str
    tags: tuple[str, ...]


class DeviceProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, _context: object) -> DeviceContext:
        self.calls += 1
        return DeviceContext("dev_1", ("a", "b"))


@pytest.mark.asyncio
async def test_dependency_identity_dag_cache_and_multiple_atomic_bindings() -> None:
    header: ValueSlot[str] = request_slot("X-Device", str)
    tags: ValueSlot[str] = request_slot("tag", str, cardinality=SlotCardinality.MANY)
    shape = OperationShape((header, tags))
    values = OperationValues.empty(shape)
    dependency: RequestDependency[DeviceContext] = RequestDependency(
        "device", PythonTypeValidator(DeviceContext), DependencyCachePolicy.CALL
    )
    same_name: RequestDependency[DeviceContext] = RequestDependency(
        "device", PythonTypeValidator(DeviceContext)
    )
    assert dependency is not same_name
    requirement = RequestRequirement(
        dependency,
        True,
        (
            ResultBinding(AttributeSelector("device_id"), header),
            ResultBinding(
                AttributeSelector("tags"),
                tags,
                operation=BindingOperation.REPLACE_ALL,
            ),
        ),
    )
    provider = DeviceProvider()
    registry = DependencyRegistry()
    registry.register(dependency, provider)
    caches = DependencyCaches()
    first = await resolve_requirements(
        (requirement,), registry, operation_id="op", attempt=1, caches=caches
    )
    second = await resolve_requirements(
        (requirement,), registry, operation_id="op", attempt=2, caches=caches
    )
    changed = apply_patch_atomic(values, first)
    assert changed.require(header) == "dev_1"
    assert cast(tuple[str, ...], cast(object, changed.require(tags))) == ("a", "b")
    assert second.operations == first.operations
    assert provider.calls == 1


def test_dependency_cycles_are_diagnostic_before_provider_calls() -> None:
    first: RequestDependency[str] = RequestDependency("first", PythonTypeValidator(str))
    second: RequestDependency[str] = RequestDependency("second", PythonTypeValidator(str))
    first_requirement = RequestRequirement(first, True, (), requires=(second,))
    second_requirement = RequestRequirement(second, True, (), requires=(first,))
    with pytest.raises(GraphError, match=r"first.*second|second.*first"):
        compile_dependency_order((first_requirement, second_requirement))


@dataclass(frozen=True)
class Contract:
    operation_id: str = "getBalance"
    method: str = "GET"
    path: str = "/accounts/{account_id}"
    input_fields: tuple[InputField, ...] = (
        InputField(
            "account_id",
            "account_id",
            str,
            True,
            RequestLocation.PATH,
            Path("account_id"),
        ),
    )
    responses: object = "responses"
    security: object | None = None


@pytest.mark.asyncio
async def test_or_of_and_auth_applies_actual_selected_alternative_atomically() -> None:
    user = BearerScheme("user")
    device = ApiKeyScheme.header("X-Device-Key", name="device")
    service = BearerScheme("service")
    policy = any_of(all_of(user, device), service)
    contract = Contract(security=policy)
    compiled: CompiledContract[object] = compile_endpoint(contract)
    assert "Authorization" in compiled.header_slots
    assert "X-Device-Key" in compiled.header_slots
    providers = AuthProviders()
    providers.register(
        service,
        StaticAuthProvider(service, "service-token", AuthProviderIdentity("service-provider")),
    )
    executions, patch = await resolve_security(policy, providers, compiled)
    assert len(executions) == 1
    assert executions[0].scheme is service
    assert executions[0].alternative == 1
    values = OperationValues.from_bound(
        compiled.plan.shape, compiled.bind_input({"account_id": "a1"})
    )
    changed = apply_patch_atomic(values, patch)
    assert changed.require(compiled.header_slots["Authorization"]) == "Bearer service-token"
    assert not changed.contains(compiled.header_slots["X-Device-Key"])

    providers = AuthProviders()
    providers.register(user, StaticAuthProvider(user, "user-token", AuthProviderIdentity("user")))
    providers.register(
        device,
        StaticAuthProvider(device, "device-key", AuthProviderIdentity("device")),
    )
    executions, patch = await resolve_security(policy, providers, compiled)
    assert [execution.scheme for execution in executions] == [user, device]
    assert all(execution.alternative == 0 for execution in executions)
    changed = apply_patch_atomic(values, patch)
    assert changed.require(compiled.header_slots["Authorization"]) == "Bearer user-token"
    assert changed.require(compiled.header_slots["X-Device-Key"]) == "device-key"


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


@dataclass(frozen=True)
class UserSession:
    access_token: str
    valid: bool = True


class ScopedSdk:
    def __init__(
        self,
        graph: LifecycleGraph,
        provider: SessionProvider[Any, Any, Any] | None = None,
    ) -> None:
        self.graph = graph
        self.provider = provider

    async def recursive_login(self) -> None:
        assert self.provider is not None
        await self.provider.resolve(self.graph, "login")


class UserAuthService:
    def __init__(self, *, recursive: bool = False) -> None:
        self.calls = 0
        self.recursive = recursive

    async def acquire(self, credentials: Credentials, context: object) -> UserSession:
        from eazy_sdk.ext import AuthFlowContext

        flow = cast(AuthFlowContext[ScopedSdk], context)
        self.calls += 1
        assert credentials.username == "user"
        if self.recursive:
            await flow.sdk.recursive_login()
        return UserSession("token")


def session_scheme() -> AuthScheme[UserSession]:
    return AuthScheme(
        "user-session",
        PythonTypeValidator(UserSession),
        (
            AuthPlacement(
                AuthLocation.HEADER,
                "Authorization",
                AttributeSessionSelector("access_token"),
                "Bearer ",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_session_acquisition_uses_scoped_sdk_singleflight_and_store_revision() -> None:
    service = UserAuthService()
    store: MemorySessionStore[UserSession] = MemorySessionStore()
    config: SessionAuth[Credentials, UserSession, ScopedSdk] = SessionAuth(
        scheme=session_scheme(),
        key=SessionKey("account:user"),
        sdk_factory=lambda graph: ScopedSdk(graph),
        store=store,
        validate=lambda session: session.valid,
        credentials=Credentials("user", "secret"),
        acquire=service,
    )
    provider = SessionProvider(config)
    first, second = await asyncio.gather(provider.resolve(), provider.resolve())
    assert first.value == second.value == UserSession("token")
    assert first.execution.revision == second.execution.revision
    assert service.calls == 1
    stored = await store.load(SessionKey("account:user"))
    assert stored is not None and stored.value == UserSession("token")


@pytest.mark.asyncio
async def test_session_cycle_guard_fails_before_nested_network_work() -> None:
    service = UserAuthService(recursive=True)
    provider_box: list[SessionProvider[Credentials, UserSession, ScopedSdk]] = []

    def sdk_factory(graph: LifecycleGraph) -> ScopedSdk:
        return ScopedSdk(graph, provider_box[0])

    config: SessionAuth[Credentials, UserSession, ScopedSdk] = SessionAuth(
        scheme=session_scheme(),
        key=SessionKey("account:user"),
        sdk_factory=sdk_factory,
        store=MemorySessionStore(),
        validate=lambda session: session.valid,
        credentials=Credentials("user", "secret"),
        acquire=service,
    )
    provider = SessionProvider(config)
    provider_box.append(provider)
    with pytest.raises(ResolutionCycleError, match=r"acquire user-session.*acquire user-session"):
        await provider.resolve(operation_id="balance")


def test_credentials_and_initial_session_are_mutually_exclusive_and_keys_are_safe() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        SessionAuth(
            scheme=session_scheme(),
            key=SessionKey("account:user"),
            sdk_factory=ScopedSdk,
            store=MemorySessionStore(),
            validate=lambda session: session.valid,
            credentials=Credentials("user", "secret"),
            initial_session=UserSession("token"),
        )
    with pytest.raises(ValueError, match="non-secret"):
        SessionKey("password=secret")


class Codec:
    def encode(self, value: UserSession) -> object:
        return {"access_token": value.access_token, "valid": value.valid}

    def decode(self, value: object) -> UserSession:
        assert isinstance(value, dict)
        return UserSession(str(value["access_token"]), bool(value["valid"]))


class Repository:
    def __init__(self) -> None:
        self.values: dict[str, tuple[object, int]] = {}

    async def load_session_data(self, key: str) -> tuple[object, int] | None:
        return self.values.get(key)

    async def save_session_data(self, key: str, value: object, revision: int) -> None:
        self.values[key] = value, revision

    async def invalidate_session_data(self, key: str, expected_revision: int | None) -> None:
        current = self.values.get(key)
        if current is not None and (expected_revision is None or current[1] == expected_revision):
            self.values.pop(key)


@pytest.mark.asyncio
async def test_generic_storage_bridge_round_trip_and_revision_safe_invalidation() -> None:
    from eazy_sdk.ext import SessionRevision

    repository = Repository()
    store = RepositorySessionStore(repository, Codec())
    key = SessionKey("account:user")
    await store.save(key, UserSession("token"), SessionRevision(2))
    stored = await store.load(key)
    assert stored is not None and stored.value == UserSession("token")
    await store.invalidate(key, SessionRevision(1))
    assert await store.load(key) is not None
    await store.invalidate(key, SessionRevision(2))
    assert await store.load(key) is None
