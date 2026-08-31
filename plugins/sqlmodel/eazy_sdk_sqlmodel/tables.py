"""Ready-made account-storage v2 tables."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text

from eazy_sdk_sqlmodel.models import (
    BaseAccount,
    BaseAccountEvent,
    BaseAccountLink,
    BaseSession,
    BaseVerification,
)


class Account(BaseAccount, table=True):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("provider", "identifier", name="uq_accounts_provider_identifier"),
        CheckConstraint("revision >= 0", name="ck_accounts_revision_nonnegative"),
        Index("ix_accounts_provider_status_created", "provider", "status", "created_at"),
        Index(
            "uq_accounts_provider_remote_id",
            "provider",
            "remote_id",
            unique=True,
            sqlite_where=text("remote_id IS NOT NULL"),
            postgresql_where=text("remote_id IS NOT NULL"),
        ),
    )


class Session(BaseSession, table=True):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("account_id", "key", name="uq_sessions_account_key"),
        CheckConstraint("revision >= 0", name="ck_sessions_revision_nonnegative"),
        Index("ix_sessions_account_active_expiry", "account_id", "is_active", "expires_at"),
    )


class Verification(BaseVerification, table=True):
    __tablename__ = "verifications"
    __table_args__ = (
        UniqueConstraint("account_id", "challenge_id", name="uq_verifications_challenge"),
        CheckConstraint(
            "attempts_remaining IS NULL OR attempts_remaining >= 0",
            name="ck_verifications_attempts_nonnegative",
        ),
        Index("ix_verifications_account_status_created", "account_id", "status", "created_at"),
        Index("ix_verifications_via_created", "via_account_id", "created_at"),
    )


class AccountLink(BaseAccountLink, table=True):
    __tablename__ = "account_links"
    __table_args__ = (
        CheckConstraint(
            "owner_account_id != resource_account_id", name="ck_account_links_not_self"
        ),
        UniqueConstraint(
            "owner_account_id",
            "resource_account_id",
            "relation",
            name="uq_account_links_relation",
        ),
        UniqueConstraint(
            "resource_account_id", "exclusive_scope", name="uq_account_links_resource_scope"
        ),
    )


class AccountEvent(BaseAccountEvent, table=True):
    __tablename__ = "account_events"
    __table_args__ = (
        Index("ix_account_events_account_occurred", "account_id", "occurred_at"),
        Index("ix_account_events_account_type_occurred", "account_id", "type", "occurred_at"),
        Index("ix_account_events_correlation", "correlation_id"),
    )


TABLE_NAMES = frozenset(
    {"accounts", "sessions", "verifications", "account_links", "account_events"}
)


__all__ = ["TABLE_NAMES", "Account", "AccountEvent", "AccountLink", "Session", "Verification"]
