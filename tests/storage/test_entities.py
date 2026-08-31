from datetime import UTC, datetime
from uuid import UUID, uuid4

from eazy_sdk.storage.entities import (
    AccountEventRecord,
    AccountEventType,
    AccountLinkRecord,
    AccountRecord,
    AccountStatus,
    Credentials,
    RestrictionRecord,
    SecretValue,
    SessionData,
    SessionRecord,
    VerificationRecord,
)


def test_credentials_redacted_summary_hides_secret() -> None:
    creds = Credentials(
        scheme="bearer", secret=SecretValue("hunter2"), data={"refresh_token": "super_secret_rt"}
    )
    summary = creds.redacted_summary()
    assert summary == {"scheme": "bearer", "has_secret": True, "data_keys": ["refresh_token"]}
    assert "hunter2" not in repr(creds)
    assert "super_secret_rt" not in repr(creds)
    assert "super_secret_rt" not in repr(summary)


def test_account_status_constants() -> None:
    assert AccountStatus.ACTIVE == "active"
    assert AccountStatus.INACTIVE == "inactive"
    assert AccountStatus.DELETED == "deleted"


def test_account_event_type_constants() -> None:
    assert AccountEventType.CREATED == "account.created"
    assert AccountEventType.AUTHORIZED == "account.authorized"
    assert AccountEventType.BANNED == "account.banned"
    assert AccountEventType.LOGIN_SUCCESS == "login.success"
    assert AccountEventType.LOGIN_FAILED == "login.failed"


def test_account_record_defaults() -> None:
    acc = AccountRecord(identifier="bob")
    assert isinstance(acc.id, UUID)
    assert acc.identifier == "bob"
    assert acc.provider is None
    assert acc.credentials is None
    assert acc.status == "active"
    assert acc.meta == {}
    assert acc.created_at.tzinfo is UTC


def test_session_data_roundtrip() -> None:
    account_id = uuid4()
    state = SessionData(
        scheme="bearer",
        headers={"Authorization": "Bearer x"},
        cookies={"sid": "abc"},
        params={"k": "v"},
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        scopes=frozenset({"read"}),
        audience="https://api.example.com",
        subject="user:42",
    )
    session = SessionRecord.from_session_data(account_id, state, label="proxy-7")
    assert session.account_id == account_id
    assert session.label == "proxy-7"
    assert session.is_active is True

    back = session.to_session_data()
    assert back.scheme == "bearer"
    assert back.headers == {"Authorization": "Bearer x"}
    assert back.cookies == {"sid": "abc"}
    assert back.params == {"k": "v"}
    assert back.expires_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert back.scopes == frozenset({"read"})
    assert back.audience == "https://api.example.com"
    assert back.subject == "user:42"
    session.headers["X"] = "1"
    assert "X" not in back.headers

    # Construction must copy, not alias, the source state's dicts.
    state.headers["Y"] = "injected"
    assert "Y" not in session.headers


def test_session_record_repr_redacts_secrets() -> None:
    session = SessionRecord(
        account_id=uuid4(),
        headers={"Authorization": "Bearer super_secret_token"},
        cookies={"sid": "secret_cookie_value"},
    )
    text = repr(session)
    assert "super_secret_token" not in text
    assert "secret_cookie_value" not in text
    assert "Authorization" in text  # header NAMES are safe to show


def test_child_records_carry_account_id() -> None:
    account_id = uuid4()
    assert VerificationRecord(account_id=account_id, type="email").status == "pending"
    assert RestrictionRecord(account_id=account_id, type="ban").status == "active"
    link = AccountLinkRecord(
        account_id=account_id, linked_account_id=uuid4(), type="registered_via"
    )
    assert link.type == "registered_via"
    event = AccountEventRecord(account_id=account_id, type="login.success")
    assert event.session_id is None
    assert event.occurred_at.tzinfo is UTC
