"""Typed, log-safe storage exceptions."""

from __future__ import annotations

from typing import Any

from eazy_sdk.exceptions import EazySDKError


class StorageError(EazySDKError):
    """Base class for all storage-layer errors. Context is log-safe (no secrets)."""

    def __init__(
        self,
        message: str,
        *,
        op: str | None = None,
        account_id: Any | None = None,
        identifier: str | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.op = op
        self.account_id = account_id
        self.identifier = identifier
        self.provider = provider

    def to_log_dict(self, **_: Any) -> dict[str, object]:
        data: dict[str, object] = {
            "error_type": type(self).__name__,
            "message": self.message,
        }
        for key in ("op", "account_id", "identifier", "provider"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value if isinstance(value, str | int) else str(value)
        return data


class AccountNotFoundError(StorageError):
    """No account matched the given id/identifier."""


class DuplicateAccountError(StorageError):
    """An account with the same (identifier, provider) already exists."""


class SessionNotFoundError(StorageError):
    """No session matched the given account/label."""


class AccountUnavailableError(StorageError):
    """No usable account was available to pick from the pool."""


class AccountLeasedError(StorageError):
    """The account is already leased by another worker."""


class StorageBackendError(StorageError):
    """The underlying storage backend (SQL, etc.) raised an error."""
