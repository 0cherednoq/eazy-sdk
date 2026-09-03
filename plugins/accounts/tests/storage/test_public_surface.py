import subprocess
import sys

import eazy_sdk_accounts.storage as storage


def test_all_names_are_importable() -> None:
    for name in storage.__all__:
        assert hasattr(storage, name), f"{name} missing from eazy_sdk_accounts.storage"


def test_key_names_present() -> None:
    expected = {
        "Account",
        "Session",
        "Verification",
        "Restriction",
        "AccountLink",
        "AccountEvent",
        "AccountRecord",
        "SessionRecord",
        "VerificationRecord",
        "RestrictionRecord",
        "AccountLinkRecord",
        "AccountEventRecord",
        "Credentials",
        "AccountStatus",
        "AccountEventType",
        "Repository",
        "AccountRepository",
        "SessionRepository",
        "VerificationRepository",
        "RestrictionRepository",
        "LinkRepository",
        "EventStore",
        "InMemoryAccountRepository",
        "InMemorySessionRepository",
        "InMemoryVerificationRepository",
        "InMemoryRestrictionRepository",
        "InMemoryLinkRepository",
        "InMemoryEventStore",
        "MemoryStorage",
        "Storage",
        "StorageOp",
        "StorageObserver",
        "BaseStorageObserver",
        "StorageError",
        "AccountNotFoundError",
        "DuplicateAccountError",
        "SessionNotFoundError",
        "AccountUnavailableError",
        "AccountLeasedError",
        "StorageBackendError",
    }
    assert expected <= set(storage.__all__)


def test_importing_storage_does_not_require_optional_deps() -> None:
    code = (
        "import sys; "
        "import eazy_sdk_accounts.storage; "
        "blocked = {'httpx', 'requests', 'curl_cffi', 'sqlmodel', 'sqlalchemy', 'pydantic'}; "
        "leaked = blocked & set(sys.modules); "
        "assert not leaked, leaked"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
