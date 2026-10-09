"""Generic session codec/store bridge over existing persistence repositories."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from eazy_sdk.auth.session import SessionKey, SessionRevision, StoredSession
from eazy_sdk.cookies import CookieState

_FORMAT = "$eazy"
_WITH_COOKIES = "session+cookies"


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
        if isinstance(value, Mapping) and value.get(_FORMAT) == _WITH_COOKIES:
            return StoredSession(
                self.codec.decode(value["session"]),
                SessionRevision(revision),
                CookieState.from_primitive(value["cookies"]),
            )
        return StoredSession(self.codec.decode(value), SessionRevision(revision))

    async def save(
        self,
        key: SessionKey,
        value: T,
        revision: SessionRevision,
        cookies: CookieState | None = None,
    ) -> None:
        encoded = self.codec.encode(value)
        if cookies is not None:
            # A repository stores one value per key, so the snapshot rides beside the session.
            # A session saved without cookies keeps the plain form it always had.
            encoded = {
                _FORMAT: _WITH_COOKIES,
                "session": encoded,
                "cookies": cookies.to_primitive(),
            }
        await self.repository.save_session_data(key.value, encoded, revision.value)

    async def invalidate(self, key: SessionKey, expected: SessionRevision | None = None) -> None:
        await self.repository.invalidate_session_data(
            key.value, expected.value if expected is not None else None
        )
