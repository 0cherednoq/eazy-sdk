"""Normalisation, redaction and inventory of a capture, on a synthetic site."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import capture_inventory
import capture_normalize
import capture_redact
import pytest
from capture_model import Capture, dump, load
from inventory_render import render

from tests.skills.conftest import API_TOKEN, CSRF, EMAIL, PASSWORD, SESSION, SITE


def _normalized(path: Path) -> Capture:
    return capture_normalize.merge([capture_normalize.read_any(path)])


def _inventory(path: Path) -> dict[str, Any]:
    capture = _normalized(path)
    return capture_inventory.build(capture_redact.redact(capture, capture_redact.collect(capture)))


def _operation(inventory: dict[str, Any], identifier: str) -> dict[str, Any]:
    return next(item for item in inventory["operations"] if item["id"] == identifier)


# --- normalisation ------------------------------------------------------------------------


def test_kind_is_read_from_content_not_from_the_name(burp_plain: Path, har_file: Path) -> None:
    assert burp_plain.suffix == ""
    assert _normalized(burp_plain).source == "burp-xml"
    assert _normalized(har_file).source == "har"


def test_every_source_gives_the_same_exchanges(burp_base64: Path, har_file: Path) -> None:
    for capture in (_normalized(burp_base64), _normalized(har_file)):
        assert [(e.method, e.url, e.status) for e in capture.exchanges] == [
            (item.method, item.url, item.status) for item in SITE
        ]
        assert [e.response_body for e in capture.exchanges] == [i.response_body for i in SITE]
        assert capture.exchanges[2].request_body == SITE[2].request_body
        assert capture.warnings == []


def test_plain_burp_export_splits_on_bare_line_feeds(burp_plain: Path) -> None:
    capture = _normalized(burp_plain)
    # The XML parser folded CRLF into LF; the body must still be found.
    assert capture.exchanges[1].request_body.startswith(b"login=alice.reader%40mailbox.example")
    assert capture.exchanges[4].response_body.startswith(b'{"status": 200')
    assert capture.exchanges[0].response_header("set-cookie") is not None


def test_plain_burp_export_is_reported_as_lossy(burp_plain: Path) -> None:
    capture = _normalized(burp_plain)
    assert capture.exchanges[0].meta.get("lossy") is True
    assert not any(byte > 127 for byte in capture.exchanges[0].response_body)
    assert any("without base64" in warning for warning in capture.warnings)
    assert any("lost their non-ASCII text" in warning for warning in capture.warnings)


def test_base64_burp_export_keeps_text_and_is_not_lossy(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    assert "Вход" in capture.exchanges[0].response_body.decode()
    assert not any(exchange.meta.get("lossy") for exchange in capture.exchanges)


def test_content_encoding_is_removed_and_remembered() -> None:
    notes: list[str] = []
    headers, body = capture_normalize.decode_body(
        [("Content-Encoding", "gzip"), ("Content-Type", "text/plain")],
        gzip.compress(b"hello"),
        notes,
    )
    assert body == b"hello"
    assert ("Content-Encoding", "gzip") not in headers
    assert notes == ["content-encoding removed: gzip"]


def test_unknown_content_encoding_is_named_not_guessed() -> None:
    notes: list[str] = []
    headers, body = capture_normalize.decode_body([("Content-Encoding", "br")], b"\x1b\x00", notes)
    assert body == b"\x1b\x00"
    assert ("Content-Encoding", "br") in headers
    assert "not decoded" in notes[0]


def test_chunked_body_is_joined() -> None:
    notes: list[str] = []
    _, body = capture_normalize.decode_body(
        [("Transfer-Encoding", "chunked")], b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n", notes
    )
    assert body == b"hello world"


def test_unsupported_input_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not a capture", encoding="utf-8")
    with pytest.raises(capture_normalize.UnknownCaptureError):
        capture_normalize.read_any(path)


def test_capture_document_round_trips(burp_base64: Path, tmp_path: Path) -> None:
    capture = _normalized(burp_base64)
    dump(capture, tmp_path / "capture.json")
    again = load(tmp_path / "capture.json")
    assert [e.response_body for e in again.exchanges] == [
        e.response_body for e in capture.exchanges
    ]
    assert again.exchanges[2].request_headers == capture.exchanges[2].request_headers


def test_two_inputs_are_merged_in_time_order(har_file: Path, tmp_path: Path) -> None:
    document = json.loads(har_file.read_text(encoding="utf-8"))
    first = {"log": {"entries": document["log"]["entries"][3:]}}
    second = {"log": {"entries": document["log"]["entries"][:3]}}
    (tmp_path / "late.har").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "early.har").write_text(json.dumps(second), encoding="utf-8")
    merged = capture_normalize.merge(
        [
            capture_normalize.read_any(tmp_path / "late.har"),
            capture_normalize.read_any(tmp_path / "early.har"),
        ]
    )
    assert [e.url for e in merged.exchanges] == [item.url for item in SITE]
    assert [e.seq for e in merged.exchanges] == list(range(1, len(SITE) + 1))


# --- redaction ----------------------------------------------------------------------------


def test_no_secret_survives_in_any_spelling(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    vault = capture_redact.collect(capture)
    redacted = capture_redact.redact(capture, vault)
    text = json.dumps(
        [
            [
                e.url,
                e.request_headers,
                e.request_body.decode(),
                e.response_headers,
                e.response_body.decode("utf-8", "replace"),
            ]
            for e in redacted.exchanges
        ],
        ensure_ascii=False,
    )
    for secret in (CSRF, SESSION, API_TOKEN, PASSWORD, "Tr0ub4dor%263x", EMAIL, "alice.reader"):
        assert secret not in text
    assert capture_redact.leaks(redacted, vault) == []
    assert redacted.redacted is True
    # The original is left as it was: replay needs the real values.
    assert CSRF in capture.exchanges[0].response_body.decode()


def test_one_value_gets_one_placeholder_everywhere(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    vault = capture_redact.collect(capture)
    redacted = capture_redact.redact(capture, vault)
    placeholder = vault.by_value[CSRF].placeholder
    assert placeholder in redacted.exchanges[0].response_body.decode()  # the page script
    assert placeholder in redacted.exchanges[0].response_header("set-cookie")  # type: ignore[operator]
    assert f"csrf={placeholder}" in redacted.exchanges[1].request_body.decode()  # the form
    assert redacted.exchanges[1].request_header("cookie") == f"act={placeholder}"


def test_page_config_words_are_not_secrets(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    capture.exchanges[0].response_body += b'<script>cfg={"authType":"password_first"}</script>'
    vault = capture_redact.collect(capture)
    assert "password_first" not in vault.by_value
    assert "2026.10" not in vault.by_value


def test_a_secret_spelled_like_a_placeholder_slug_is_not_a_leak(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    capture.exchanges[1].request_body += b"&auth_mode=password"
    vault = capture_redact.collect(capture)
    assert "password" in vault.by_value
    assert capture_redact.leaks(capture_redact.redact(capture, vault), vault) == []


def test_mailbox_domain_equal_to_the_site_is_kept(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    body = capture.exchanges[1].request_body.replace(b"mailbox.example", b"site.example")
    capture.exchanges[1].request_body = body
    vault = capture_redact.collect(capture)
    redacted = capture_redact.redact(capture, vault)
    assert "site.example" not in vault.by_value
    assert redacted.exchanges[0].host == "id.site.example"


def test_typed_values_are_secrets_with_the_field_as_origin(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    typed = [{"value": "4821", "field": {"type": "text", "name": "otp"}}]
    vault = capture_redact.collect(capture, typed)
    assert vault.by_value["4821"].kind == "typed"
    assert vault.by_value["4821"].origins == ["typed into otp"]


def test_redact_command_writes_files_and_ignores_the_work_directory(
    burp_base64: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    assert capture_normalize.main([str(burp_base64), "--out", str(work)]) == 0
    assert capture_redact.main([str(work)]) == 0
    assert (work / ".gitignore").read_text(encoding="utf-8") == "*\n"
    secrets = json.loads((work / "secrets.json").read_text(encoding="utf-8"))["secrets"]
    assert {entry["value"] for entry in secrets.values()} >= {CSRF, SESSION, API_TOKEN, PASSWORD}
    assert CSRF not in (work / "capture.redacted.json").read_text(encoding="utf-8")
    assert capture_inventory.main([str(work)]) == 0
    assert CSRF not in (work / "inventory.md").read_text(encoding="utf-8")
    capsys.readouterr()


def test_inventory_refuses_a_capture_that_was_not_redacted(
    burp_base64: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    capture_normalize.main([str(burp_base64), "--out", str(work)])
    (work / "capture.redacted.json").write_bytes((work / "capture.json").read_bytes())
    assert capture_inventory.main([str(work)]) == 1
    assert "not redacted" in capsys.readouterr().err


# --- inventory ----------------------------------------------------------------------------


def test_operations_are_grouped_and_noise_is_left_out(burp_base64: Path) -> None:
    inventory = _inventory(burp_base64)
    assert [item["id"] for item in inventory["operations"]] == [
        "GET id.site.example/login",
        "POST id.site.example/auth",
        "GET mail.site.example/inbox/",
        "GET mail.site.example/api/v1/threads",
    ]
    assert inventory["noise"] == {"static resource": [7]}
    assert [host["host"] for host in inventory["hosts"]] == ["id.site.example", "mail.site.example"]


def test_two_outcomes_on_one_status_are_two_variants(burp_base64: Path) -> None:
    auth = _operation(_inventory(burp_base64), "POST id.site.example/auth")
    assert [(v["status"], v["location"], v["exchanges"]) for v in auth["responses"]] == [
        (302, "id.site.example/login ?fail", [2]),
        (302, "mail.site.example/inbox/", [3]),
    ]
    assert auth["responses"][1]["sets_cookies"] == ["sid"]


def test_form_fields_keep_wire_order_and_behaviour(burp_base64: Path) -> None:
    auth = _operation(_inventory(burp_base64), "POST id.site.example/auth")
    assert auth["body_order"] == ["login", "password", "csrf", "keep"]
    body = {item["name"]: item for item in auth["body"]}
    assert body["keep"]["behaviour"] == "constant"
    assert body["password"]["behaviour"] == "secret:password"
    assert len(body["password"]["values"]) == 2
    assert body["login"]["behaviour"] == "secret:email"


def test_value_origins(burp_base64: Path) -> None:
    inventory = _inventory(burp_base64)
    auth = _operation(inventory, "POST id.site.example/auth")["value_origins"]
    assert auth["body:csrf"] == {"from": "set-cookie", "exchange": 1, "cookie": "act"}
    assert auth["body:login"] == {"from": "the caller"}
    assert auth["body:password"] == {"from": "typed by the user"}
    token = _operation(inventory, "GET mail.site.example/api/v1/threads")["value_origins"][
        "query:token"
    ]
    assert token["from"] == "response body"
    assert token["exchange"] == 4
    assert token["regex"] == '"token":"([^"]+)"'


def test_a_token_found_only_in_a_page_script_is_traced_to_it(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    # Without the cookie the same value is only in the page script.
    capture.exchanges[0].response_headers = [
        pair for pair in capture.exchanges[0].response_headers if pair[0] != "Set-Cookie"
    ]
    inventory = capture_inventory.build(
        capture_redact.redact(capture, capture_redact.collect(capture))
    )
    origin = _operation(inventory, "POST id.site.example/auth")["value_origins"]["body:csrf"]
    assert origin["from"] == "response body"
    assert origin["regex"] == '"csrf":"([^"]+)"'


def test_query_values_are_classified(burp_base64: Path) -> None:
    threads = _operation(_inventory(burp_base64), "GET mail.site.example/api/v1/threads")
    query = {item["name"]: item for item in threads["query"]}
    assert query["_"]["behaviour"] == "timestamp"
    assert query["offset"]["behaviour"] == "varies"
    assert query["offset"]["hint"] == "pagination"
    assert query["limit"]["behaviour"] == "constant"
    assert threads["pagination_hint"] == {
        "request": ["offset", "limit"],
        "response": ["body.threads_total"],
    }


def test_schema_merges_samples_and_carries_no_values(burp_base64: Path) -> None:
    threads = _operation(_inventory(burp_base64), "GET mail.site.example/api/v1/threads")
    (variant,) = threads["responses"]
    assert variant["body_marks"] == {"status": 200}
    items = variant["json_schema"]["properties"]["body"]["properties"]["threads"]["items"]
    assert items["properties"]["id"] == {"type": ["string"], "format": ["numeric-string"]}
    assert items["properties"]["flag"]["optional"] is True
    assert "optional" not in items["properties"]["subject"]
    assert "Тема" not in json.dumps(variant, ensure_ascii=False)


def test_envelope_candidate_is_found(burp_base64: Path) -> None:
    (envelope,) = _inventory(burp_base64)["envelopes"]
    assert envelope["host"] == "mail.site.example"
    assert envelope["keys"] == ["body", "status"]
    assert envelope["marks"] == {"status": [200]}


def test_cookie_table_tells_who_set_and_who_received(burp_base64: Path) -> None:
    cookies = {item["name"]: item for item in _inventory(burp_base64)["cookies"]}
    assert cookies["sid"]["set_by"] == [3]
    assert cookies["sid"]["domains"] == [".site.example"]
    assert cookies["sid"]["sent_to"] == ["mail.site.example"]
    assert cookies["act"]["sent_to"] == ["id.site.example", "mail.site.example"]


def test_redirect_chain_follows_location_and_meta_refresh() -> None:
    def page(
        seq: int, url: str, status: int, headers: list[tuple[str, str]], body: bytes = b""
    ) -> Any:
        from capture_model import Exchange

        return Exchange(
            seq, "GET", url, status=status, response_headers=headers, response_body=body
        )

    html = [("Content-Type", "text/html")]
    stub = b"""<meta http-equiv="refresh" content="0;URL='https://a.example/sync?x=1'"/>"""
    chain = capture_inventory.redirect_chains(
        [
            page(1, "https://m.example/inbox/", 200, html, stub),
            page(2, "https://a.example/sync?x=1", 302, [("Location", "https://m.example/inbox/")]),
            page(3, "https://m.example/inbox/", 200, html, b"<title>ok</title>"),
            page(4, "https://m.example/other", 200, html, b"<title>other</title>"),
        ]
    )
    assert [[(step["exchange"], step.get("then")) for step in steps] for steps in chain] == [
        [(1, "meta-refresh"), (2, "location"), (3, None)]
    ]


def test_one_address_serving_a_stub_and_a_page_gives_two_variants() -> None:
    from capture_model import Exchange

    html = [("Content-Type", "text/html")]
    stub = b"""<meta http-equiv="refresh" content="0;URL='https://a.example/sync'"/>"""
    samples = [
        Exchange(
            1,
            "GET",
            "https://m.example/inbox/",
            status=200,
            response_headers=html,
            response_body=stub,
        ),
        Exchange(
            2,
            "GET",
            "https://m.example/inbox/",
            status=200,
            response_headers=html,
            response_body=b"<title>Mail</title>",
        ),
    ]
    variants = capture_inventory.response_variants(samples)
    assert [variant["exchanges"] for variant in variants] == [[1], [2]]
    assert variants[0]["html"]["meta_refresh"] == "https://a.example/sync"
    assert variants[1]["html"]["title"] == "Mail"


def test_identifier_segments_become_path_parameters() -> None:
    assert capture_inventory.path_template("/api/users/12345/files") == "/api/users/{id}/files"
    assert (
        capture_inventory.path_template("/doc/3f2b8c1e-0a4d-4e6f-9b7a-1c2d3e4f5a6b")
        == "/doc/{uuid}"
    )
    merged = capture_inventory.merge_templates(
        {("GET", "h", f"/folders/{name}/items") for name in ("f10", "f22", "f35")}
    )
    assert set(merged.values()) == {"/folders/{param}/items"}
    kept = capture_inventory.merge_templates({("GET", "h", "/a/one"), ("GET", "h", "/a/two")})
    assert set(kept.values()) == {"/a/one", "/a/two"}


def test_out_of_scope_signals_are_named(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    capture.exchanges[4].url += "&sign=9f8e7d6c5b4a39281706f5e4d3c2b1a0"
    capture.exchanges[3].response_body += b'<div class="g-recaptcha"></div>'
    inventory = capture_inventory.build(
        capture_redact.redact(capture, capture_redact.collect(capture))
    )
    signals = "\n".join(inventory["out_of_scope"])
    assert "'sign' looks like a signature" in signals
    assert "#4: the response carries anti-bot or captcha markers" in signals


def test_marks_cut_the_timeline_into_scenarios(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    capture.timeline = [
        {"kind": "mark", "label": "login"},
        {"kind": "action", "action": "input", "target": {"name": "password"}},
        {"kind": "action", "action": "click", "target": {"tag": "button", "label": "Sign in"}},
        {"kind": "request", "exchange": 3},
        {"kind": "mark", "label": "inbox"},
        {"kind": "request", "exchange": 5},
        {"kind": "request", "exchange": 7},
    ]
    found = capture_inventory.scenarios(capture, kept={1, 2, 3, 4, 5, 6})
    assert found == [
        {
            "name": "login",
            "actions": ["input password", "click Sign in"],
            "exchanges": [3],
            "steps": ["input password", "click Sign in", "-> #3"],
        },
        {"name": "inbox", "actions": [], "exchanges": [5], "steps": ["-> #5"]},
    ]


def test_markdown_page_names_every_operation(burp_base64: Path) -> None:
    inventory = _inventory(burp_base64)
    page = render(inventory)
    for operation in inventory["operations"]:
        assert f"### {operation['id']}" in page
    assert "comes from cookie `act` set by #1" in page
    assert "## Out of scope signals" in page


def test_a_short_secret_never_eats_part_of_a_longer_word(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    # The account's local part is a plain word that also starts an API parameter name.
    body = capture.exchanges[2].request_body.replace(b"alice.reader", b"support")
    capture.exchanges[2].request_body = body
    capture.exchanges[4].url += "&supported_fields=1&owner=support"
    vault = capture_redact.collect(capture)
    assert vault.by_value["support"].kind == "emaillocal"
    redacted = capture_redact.redact(capture, vault)
    assert "supported_fields=1" in redacted.exchanges[4].url
    assert "owner=support" not in redacted.exchanges[4].url
    assert capture_redact.leaks(redacted, vault) == []


def test_only_the_address_sent_with_a_password_is_split_into_halves(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    capture.exchanges[4].url += "&to=contact%40partner.example"
    vault = capture_redact.collect(capture)
    assert "contact@partner.example" in vault.by_value
    assert "contact" not in vault.by_value
    assert "partner.example" not in vault.by_value
    assert vault.identities == {"alice.reader@mailbox.example"}


def test_an_envelope_survives_one_endpoint_that_does_not_use_it() -> None:
    def variant(keys: list[str]) -> dict[str, Any]:
        schema = {"type": ["object"], "properties": {key: {"type": ["integer"]} for key in keys}}
        return {"json_schema": schema, "body_marks": {"status": 200} if "status" in keys else {}}

    operations = [
        {"id": f"GET h/api/{n}", "host": "h", "responses": [variant(["status", "body"])]}
        for n in range(5)
    ]
    operations.append({"id": "GET h/srv.json", "host": "h", "responses": [variant(["up"])]})
    (found,) = capture_inventory.envelopes(operations)
    assert found["keys"] == ["body", "status"]


def test_what_the_page_showed_names_the_outcome_of_an_exchange(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    capture.timeline = [
        {
            "kind": "action",
            "tab": "t",
            "action": "click",
            "target": {"tag": "button", "label": "Sign in"},
        },
        {"kind": "request", "tab": "t", "exchange": 2},
        {
            "kind": "page",
            "tab": "t",
            "title": "Sign in",
            "appeared": ["This account is blocked."],
            "appeared_total": 1,
        },
        {
            "kind": "action",
            "tab": "t",
            "action": "click",
            "target": {"tag": "button", "label": "Sign in"},
        },
        {"kind": "request", "tab": "t", "exchange": 3},
        {"kind": "request", "tab": "t", "exchange": 4},
        {
            "kind": "page",
            "tab": "t",
            "title": "Inbox",
            "appeared": [f"Subject {n}" for n in range(12)],
            "appeared_total": 40,
        },
    ]
    inventory = capture_inventory.build(
        capture_redact.redact(capture, capture_redact.collect(capture))
    )
    auth = _operation(inventory, "POST id.site.example/auth")
    refused, signed_in = auth["responses"]
    assert refused["page_showed"] == [
        {"title": "Sign in", "appeared": ["This account is blocked."]}
    ]
    # A rendered list is content of the account: it names nothing and is not quoted.
    assert "page_showed" not in signed_in
    assert "Subject" not in json.dumps(inventory, ensure_ascii=False)
    assert inventory["scenarios"][0]["steps"] == [
        "click Sign in",
        "-> #2",
        "the page shows: This account is blocked.",
        "click Sign in",
        "-> #3",
        "-> #4",
    ]
    assert "then the page showed (title `Sign in`): «This account is blocked.»" in render(inventory)


def test_a_notice_element_is_quoted_and_a_long_render_is_only_counted() -> None:
    facts = capture_inventory.page_facts(
        {"title": "T", "notices": ["Wrong code"], "appeared": ["a", "b"], "appeared_total": 30}
    )
    assert facts == {"title": "T", "notices": ["Wrong code"], "appeared_lines": 30}


def test_pages_of_one_level_are_not_folded_into_one_template() -> None:
    pages = {("GET", "h", f"/{name}") for name in ("login", "scroll", "logout", "about")}
    assert set(capture_inventory.merge_templates(pages).values()) == {
        "/login",
        "/scroll",
        "/logout",
        "/about",
    }


def test_a_page_belongs_to_the_navigation_not_to_background_requests(burp_base64: Path) -> None:
    capture = _normalized(burp_base64)
    said = {"kind": "page", "tab": "t", "appeared": ["Wrong password"], "appeared_total": 1}
    capture.timeline = [
        {
            "kind": "action",
            "tab": "t",
            "action": "click",
            "target": {"tag": "button", "label": "Go"},
        },
        {"kind": "request", "tab": "t", "exchange": 5, "resource": "Fetch"},
        {"kind": "request", "tab": "t", "exchange": 2, "resource": "Document"},
        {"kind": "request", "tab": "t", "exchange": 6, "resource": "Fetch"},
        # A look that saw nothing new must not end the wait for the one that does.
        {"kind": "page", "tab": "t", "title": "Sign in", "appeared": [], "appeared_total": 0},
        said,
        {
            "kind": "action",
            "tab": "t",
            "action": "click",
            "target": {"tag": "button", "label": "More"},
        },
        {"kind": "request", "tab": "t", "exchange": 5, "resource": "Fetch"},
        {"kind": "request", "tab": "t", "exchange": 6, "resource": "Fetch"},
        {"kind": "page", "tab": "t", "appeared": ["Moved to Trash"], "appeared_total": 1},
    ]
    shown = capture_inventory.pages_after(capture, kept={2, 5, 6})
    assert shown == {
        2: {"appeared": ["Wrong password"]},
        5: {"appeared": ["Moved to Trash"]},
    }


def test_a_spinner_is_not_a_message() -> None:
    facts = capture_inventory.page_facts({"appeared": ["Loading..."], "appeared_total": 1})
    assert facts == {}
    assert capture_inventory.page_facts({"notices": ["Загружается..."]}) == {}
    facts = capture_inventory.page_facts(
        {"appeared": ["Загрузка…", "Неверный пароль, попробуйте ещё раз"], "appeared_total": 2}
    )
    assert facts == {"appeared": ["Неверный пароль, попробуйте ещё раз"]}
