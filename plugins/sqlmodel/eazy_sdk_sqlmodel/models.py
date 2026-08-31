"""SQLModel account-storage v2 base models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

DEFAULT_PROVIDER = "__default__"


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_provider(provider: str | None) -> str:
    """Map the public optional provider to one non-null persistence key."""

    value = provider.strip() if provider is not None else ""
    return value or DEFAULT_PROVIDER


def public_provider(provider: str) -> str | None:
    return None if provider == DEFAULT_PROVIDER else provider


def normalize_identifier(identifier: str) -> str:
    value = identifier.strip()
    if not value:
        raise ValueError("account identifier cannot be empty")
    return value


class UtcDateTime(TypeDecorator[datetime]):
    """Store naive UTC and always return timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class BaseAccount(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    provider: str = Field(default=DEFAULT_PROVIDER, nullable=False)
    identifier: str = Field(nullable=False)
    remote_id: str | None = Field(default=None)
    status: str = Field(default="provisioning", nullable=False)
    revision: int = Field(default=0, nullable=False)
    credentials: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    profile: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    meta: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)

    def redacted_summary(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "provider": self.provider,
            "identifier": self.identifier,
            "status": self.status,
            "revision": self.revision,
            "has_credentials": bool(self.credentials),
            "profile_keys": sorted(self.profile),
            "meta_keys": sorted(self.meta),
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.redacted_summary()})"

    __str__ = __repr__


class BaseSession(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    account_id: UUID = Field(foreign_key="accounts.id", ondelete="CASCADE", nullable=False)
    key: str = Field(nullable=False)
    revision: int = Field(default=1, nullable=False)
    kind: str = Field(default="custom", nullable=False)
    payload: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    expires_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)

    def redacted_summary(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "account_id": str(self.account_id),
            "key": self.key,
            "revision": self.revision,
            "kind": self.kind,
            "is_active": self.is_active,
            "has_payload": bool(self.payload),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.redacted_summary()})"

    __str__ = __repr__


class BaseVerification(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    account_id: UUID = Field(foreign_key="accounts.id", ondelete="CASCADE", nullable=False)
    via_account_id: UUID | None = Field(
        default=None, foreign_key="accounts.id", ondelete="SET NULL"
    )
    challenge_id: str = Field(nullable=False)
    kind: str = Field(nullable=False)
    status: str = Field(default="pending", nullable=False)
    target: str | None = Field(default=None)
    expires_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    attempts_remaining: int | None = Field(default=None)
    replaces_id: UUID | None = Field(
        default=None, foreign_key="verifications.id", ondelete="SET NULL"
    )
    meta: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)
    verified_at: datetime | None = Field(default=None, sa_type=UtcDateTime)


class BaseAccountLink(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_account_id: UUID = Field(foreign_key="accounts.id", ondelete="RESTRICT", nullable=False)
    resource_account_id: UUID = Field(
        foreign_key="accounts.id", ondelete="RESTRICT", nullable=False
    )
    relation: str = Field(nullable=False)
    status: str = Field(default="active", nullable=False)
    exclusive_scope: str | None = Field(default=None)
    meta: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)
    released_at: datetime | None = Field(default=None, sa_type=UtcDateTime)


class BaseAccountEvent(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    account_id: UUID = Field(foreign_key="accounts.id", ondelete="CASCADE", nullable=False)
    type: str = Field(nullable=False)
    occurred_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime, nullable=False)
    correlation_id: str | None = Field(default=None)
    session_id: UUID | None = Field(default=None, foreign_key="sessions.id", ondelete="SET NULL")
    verification_id: UUID | None = Field(
        default=None, foreign_key="verifications.id", ondelete="SET NULL"
    )
    link_id: UUID | None = Field(default=None, foreign_key="account_links.id", ondelete="SET NULL")
    data: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    meta: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)


__all__ = [
    "DEFAULT_PROVIDER",
    "BaseAccount",
    "BaseAccountEvent",
    "BaseAccountLink",
    "BaseSession",
    "BaseVerification",
    "UtcDateTime",
    "normalize_identifier",
    "normalize_provider",
    "public_provider",
    "utcnow",
]
