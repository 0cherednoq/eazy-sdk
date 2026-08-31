from eazy_sdk.logging import LOGGER_NAME, get_logger
from eazy_sdk.redaction import preview, redact_headers, redact_json


def test_redact_headers_is_case_insensitive() -> None:
    out = redact_headers({"Authorization": "Bearer x", "X-Other": "ok"})
    assert out["Authorization"] == "***REDACTED***"
    assert out["X-Other"] == "ok"


def test_redact_json_recurses_and_keeps_shape() -> None:
    payload = {"password": "p", "nested": {"token": "t", "keep": 1}, "list": [{"api_key": "k"}]}
    out = redact_json(payload)
    assert out == {
        "password": "***REDACTED***",
        "nested": {"token": "***REDACTED***", "keep": 1},
        "list": [{"api_key": "***REDACTED***"}],
    }


def test_redact_json_passes_through_non_dict() -> None:
    assert redact_json("plain") == "plain"


def test_preview_truncates() -> None:
    assert preview("abcdef", 3) == "abc…"
    assert preview("ab", 3) == "ab"
    assert preview(None, 3) is None


def test_get_logger_uses_namespace() -> None:
    assert get_logger().name == LOGGER_NAME
    assert get_logger("sub").name == "eazy_sdk.sub"


def test_preview_non_positive_limit_returns_empty() -> None:
    assert preview("supersecret", 0) == ""
    assert preview("supersecret", -1) == ""
    assert preview(None, -1) is None


def test_redact_proxy_authorization() -> None:
    out = redact_headers({"Proxy-Authorization": "Basic abc"})
    assert out["Proxy-Authorization"] == "***REDACTED***"


def test_redact_client_secret_and_private_key() -> None:
    out = redact_json({"client_secret": "s", "private_key": "k", "keep": 1})
    assert out == {"client_secret": "***REDACTED***", "private_key": "***REDACTED***", "keep": 1}
