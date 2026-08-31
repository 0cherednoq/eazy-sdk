import pytest

from eazy_sdk.exceptions import EazySDKError
from eazy_sdk.storage.exceptions import (
    AccountLeasedError,
    AccountNotFoundError,
    AccountUnavailableError,
    DuplicateAccountError,
    SessionNotFoundError,
    StorageBackendError,
    StorageError,
)


def test_hierarchy() -> None:
    assert issubclass(StorageError, EazySDKError)
    for cls in (
        AccountNotFoundError,
        DuplicateAccountError,
        SessionNotFoundError,
        AccountUnavailableError,
        AccountLeasedError,
        StorageBackendError,
    ):
        assert issubclass(cls, StorageError)


def test_context_and_log_dict_redacts() -> None:
    err = DuplicateAccountError(
        "already exists", identifier="bob", provider="example.com", op="account.create"
    )
    assert err.identifier == "bob"
    assert err.provider == "example.com"
    assert err.op == "account.create"
    log = err.to_log_dict()
    assert log["error_type"] == "DuplicateAccountError"
    assert log["identifier"] == "bob"
    assert log["provider"] == "example.com"
    assert log["op"] == "account.create"
    with pytest.raises(StorageError):
        raise err
