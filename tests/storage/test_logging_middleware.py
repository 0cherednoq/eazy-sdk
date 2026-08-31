import logging

import pytest

from eazy_sdk.storage.middleware import LoggingMiddleware
from eazy_sdk.storage.storage import StorageMiddleware, StorageOp


def test_is_a_storage_middleware() -> None:
    assert isinstance(LoggingMiddleware(), StorageMiddleware)


async def test_logs_redacted_op_on_after(caplog: pytest.LogCaptureFixture) -> None:
    mw = LoggingMiddleware(level=logging.INFO)
    op = StorageOp(
        name="account.create",
        account_id="acc-1",
        data={"identifier": "bob", "secret": "topsecretvalue"},
    )
    with caplog.at_level(logging.INFO, logger="eazy_sdk.storage"):
        await mw.after(op)
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    payload = rec.__dict__["eazy_sdk"]
    assert payload["op"] == "account.create"
    assert payload["account_id"] == "acc-1"
    assert payload["data_keys"] == ["identifier", "secret"]
    # the secret VALUE never reaches the log (only the key NAME "secret" may appear)
    assert "topsecretvalue" not in str(payload)
    assert "topsecretvalue" not in rec.getMessage()


async def test_logs_warning_on_error(caplog: pytest.LogCaptureFixture) -> None:
    mw = LoggingMiddleware()
    op = StorageOp(name="session.save", account_id="acc-1")
    with caplog.at_level(logging.WARNING, logger="eazy_sdk.storage"):
        await mw.on_error(op, ValueError("boom"))
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert rec.__dict__["eazy_sdk"]["error"] == "ValueError"
