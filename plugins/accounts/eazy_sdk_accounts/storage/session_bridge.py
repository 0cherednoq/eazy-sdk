"""Generic session codec/store bridge over existing persistence repositories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from eazy_sdk.auth.session import SessionKey, SessionRevision, StoredSession


class SessionDataRepository(Protocol):
    async def load_session_data(self, key: str) -> tuple[object, int] | None: ...

    async def save_session_data(self, key: str, value: object, revision: int) -> None: ...

    async def invalidate_session_data(self, key: str, expected_revision: int | None) -> None: ...


class SessionCodec[T](Protocol):
    def encode(self, value: T) -> object: ...

    def decode(self, value: object) -> T: ...


@dataclass(slots=True)
class RepositorySessionStore[T]:
    repository: SessionDataRepository
    codec: SessionCodec[T]

    async def load(self, key: SessionKey) -> StoredSession[T] | None:
        stored = await self.repository.load_session_data(key.value)
        if stored is None:
            return None
        value, revision = stored
        return StoredSession(self.codec.decode(value), SessionRevision(revision))

    async def save(self, key: SessionKey, value: T, revision: SessionRevision) -> None:
        await self.repository.save_session_data(key.value, self.codec.encode(value), revision.value)

    async def invalidate(self, key: SessionKey, expected: SessionRevision | None = None) -> None:
        await self.repository.invalidate_session_data(
            key.value, expected.value if expected is not None else None
        )
