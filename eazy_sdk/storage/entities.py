"""Storage entities: structural Protocols + default dataclass records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

type AuthScheme = str


@dataclass(frozen=True, slots=True, repr=False)
class SecretValue:
    value: str

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


@dataclass(frozen=True, slots=True)
class SessionData:
    scheme: str = "custom"
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    scopes: frozenset[str] = frozenset()
    audience: str | None = None
    subject: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AccountStatus:
    """Suggested (open) account lifecycle statuses. Any string is allowed."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class AccountEventType:
    """Suggested (open) canonical event types. Any string is allowed."""

    CREATED = "account.created"
    VERIFIED = "account.verified"
    AUTHORIZED = "account.authorized"
    BANNED = "account.banned"
    FROZEN = "account.frozen"
    LOGIN_SUCCESS = "login.success"
    LOGIN_FAILED = "login.failed"


@dataclass(repr=False)
class Credentials:
    """Secrets used to authenticate an account. Never reveals values in repr/logs."""

    scheme: AuthScheme = "custom"
    secret: SecretValue | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "has_secret": self.secret is not None,
            "data_keys": sorted(self.data),
        }

    def __repr__(self) -> str:
        return f"Credentials({self.redacted_summary()})"


# ---- structural Protocols (generic over ID) ----


class Account[ID](Protocol):
    id: ID
    identifier: str
    provider: str | None
    credentials: Credentials | None
    status: str
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class Session[ID](Protocol):
    id: ID
    account_id: ID
    scheme: AuthScheme
    headers: dict[str, str]
    cookies: dict[str, str]
    params: dict[str, Any]
    meta: dict[str, Any]
    expires_at: datetime | None
    scopes: frozenset[str]
    label: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    def to_session_data(self) -> SessionData: ...


class Verification[ID](Protocol):
    id: ID
    account_id: ID
    type: str
    status: str
    target: str | None
    meta: dict[str, Any]
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class Restriction[ID](Protocol):
    id: ID
    account_id: ID
    type: str
    status: str
    reason: str | None
    expires_at: datetime | None
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AccountLink[ID](Protocol):
    id: ID
    account_id: ID
    linked_account_id: ID
    type: str
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AccountEvent[ID](Protocol):
    id: ID
    account_id: ID
    type: str
    occurred_at: datetime
    data: dict[str, Any]
    session_id: ID | None
    meta: dict[str, Any]


# ---- default dataclass records (UUID-keyed) ----


@dataclass(kw_only=True)
class AccountRecord:
    identifier: str
    id: UUID = field(default_factory=uuid4)
    provider: str | None = None
    credentials: Credentials | None = None
    status: str = AccountStatus.ACTIVE
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(kw_only=True, repr=False)
class SessionRecord:
    account_id: UUID
    id: UUID = field(default_factory=uuid4)
    scheme: AuthScheme = "custom"
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    scopes: frozenset[str] = frozenset()
    audience: str | None = None
    subject: str | None = None
    label: str | None = None
    is_active: bool = True
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def to_session_data(self) -> SessionData:
        return SessionData(
            scheme=self.scheme,
            headers=dict(self.headers),
            cookies=dict(self.cookies),
            params=dict(self.params),
            meta=dict(self.meta),
            expires_at=self.expires_at,
            scopes=self.scopes,
            audience=self.audience,
            subject=self.subject,
        )

    @classmethod
    def from_session_data(
        cls, account_id: UUID, state: SessionData, *, label: str | None = None
    ) -> SessionRecord:
        return cls(
            account_id=account_id,
            scheme=state.scheme,
            headers=dict(state.headers),
            cookies=dict(state.cookies),
            params=dict(state.params),
            meta=dict(state.meta),
            expires_at=state.expires_at,
            scopes=state.scopes,
            audience=state.audience,
            subject=state.subject,
            label=label,
        )

    def redacted_summary(self) -> dict[str, Any]:
        """Log-safe summary: names and flags only — never secret values."""
        return {
            "id": str(self.id),
            "account_id": str(self.account_id),
            "scheme": self.scheme,
            "label": self.label,
            "is_active": self.is_active,
            "has_headers": bool(self.headers),
            "header_names": sorted(self.headers),
            "has_cookies": bool(self.cookies),
            "cookie_names": sorted(self.cookies),
            "has_params": bool(self.params),
            "param_names": sorted(self.params),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "scopes": sorted(self.scopes),
        }

    def __repr__(self) -> str:
        return f"SessionRecord({self.redacted_summary()})"


def session_record_values(
    account_id: Any, state: SessionData, *, label: str | None = None
) -> dict[str, Any]:
    """Build backend-neutral session row values without runtime auth coupling."""
    return {
        "account_id": account_id,
        "scheme": state.scheme,
        "headers": dict(state.headers),
        "cookies": dict(state.cookies),
        "params": dict(state.params),
        "meta": dict(state.meta),
        "expires_at": state.expires_at,
        "scopes": state.scopes,
        "audience": state.audience,
        "subject": state.subject,
        "label": label,
        "is_active": True,
    }


@dataclass(kw_only=True)
class VerificationRecord:
    account_id: UUID
    type: str
    id: UUID = field(default_factory=uuid4)
    status: str = "pending"
    target: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    verified_at: datetime | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(kw_only=True)
class RestrictionRecord:
    account_id: UUID
    type: str
    id: UUID = field(default_factory=uuid4)
    status: str = "active"
    reason: str | None = None
    expires_at: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(kw_only=True)
class AccountLinkRecord:
    account_id: UUID
    linked_account_id: UUID
    type: str
    id: UUID = field(default_factory=uuid4)
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(kw_only=True)
class AccountEventRecord:
    account_id: UUID
    type: str
    id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=_utcnow)
    data: dict[str, Any] = field(default_factory=dict)
    session_id: UUID | None = None
    meta: dict[str, Any] = field(default_factory=dict)
