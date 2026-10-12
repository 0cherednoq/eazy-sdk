"""Play a capture back through the real SDK, without the network.

    python sdk_replay.py WORKDIR --scenarios tests/replay/scenarios.toml

Run from the root of the SDK project, in its environment. The transport of the SDK is replaced
by a handler that answers each request with the recorded response, matched by method, host and
path in the recorded order. The report tells, for every scenario, whether the call ended the
expected way, which requests the SDK made that the capture does not have, which recorded
exchanges it never asked for and how each request differs from the recorded one.

``scenarios.toml``::

    [sdk]
    factory = "tests.replay.factory:open_sdk"   # open_sdk(handler, **options) -> SDK
    mode = "async"                              # or "sync"

    [[scenario]]
    name = "wrong password"
    exchanges = "1-2"
    call = "signed_in"
    [scenario.options]                          # passed to the factory
    password = "__SECRET_password_1__"
    [scenario.args]                             # passed to the call
    [scenario.expect]
    raises = "InvalidCredentials"               # or: returns = "ThreadsPage"

    [[exclude]]
    exchanges = "10-12"
    reason = "background polling"

Placeholders of ``secrets.json`` in options and args are replaced by the real values, so the
SDK is driven by the same credentials the capture was recorded with.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import re
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_model import Capture, Exchange, load, media_type

REPORT_FORMAT = "eazy-replay/1"

_PLACEHOLDER = re.compile(r"__SECRET_[a-z0-9]+_\d+__")
_TIMESTAMP = re.compile(r"^1\d{9}(\d{3})?$")
_MUST_MATCH = {"origin", "referer", "content-type", "x-requested-with", "authorization"}
_TRANSPORT = {
    "host", "connection", "content-length", "accept-encoding", "user-agent", "accept",
    "accept-language", "cache-control", "pragma", "upgrade-insecure-requests", "cookie", "te",
    "priority", "dnt", "if-none-match", "if-modified-since",
}  # fmt: skip
_DROPPED_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}
_UNMATCHED_STATUS = 599


class ScenarioError(ValueError):
    """The scenarios file cannot be run as written."""


# --- scenarios file -----------------------------------------------------------------------


def parse_range(value: object) -> list[int]:
    """Read ``"1-3,7"`` or ``[1, 2, 3, 7]`` into exchange numbers."""
    if isinstance(value, list):
        return [int(item) for item in value]
    numbers: list[int] = []
    for part in str(value).split(","):
        low, separator, high = part.strip().partition("-")
        if not low:
            continue
        numbers += list(range(int(low), int(high) + 1)) if separator else [int(low)]
    return numbers


@dataclass(frozen=True, slots=True)
class Scenario:
    """One call of the SDK and the exchanges it is expected to replay."""

    name: str
    exchanges: tuple[int, ...]
    call: str
    options: Mapping[str, Any]
    args: Mapping[str, Any]
    expect: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Plan:
    """The parsed scenarios file."""

    factory: str
    mode: str
    scenarios: tuple[Scenario, ...]
    excluded: Mapping[int, str]


def load_plan(path: Path) -> Plan:
    """Read ``scenarios.toml``."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    sdk = document.get("sdk") or {}
    if ":" not in str(sdk.get("factory", "")):
        raise ScenarioError('[sdk] factory must be "module:callable"')
    mode = str(sdk.get("mode", "async"))
    if mode not in {"async", "sync"}:
        raise ScenarioError('[sdk] mode must be "async" or "sync"')
    scenarios = []
    for raw in document.get("scenario", []):
        expect = raw.get("expect") or {}
        if ("raises" in expect) == ("returns" in expect):
            raise ScenarioError(
                f"scenario {raw.get('name')!r}: expect exactly one of raises/returns"
            )
        scenarios.append(
            Scenario(
                name=str(raw["name"]),
                exchanges=tuple(parse_range(raw["exchanges"])),
                call=str(raw["call"]),
                options=raw.get("options") or {},
                args=raw.get("args") or {},
                expect=expect,
            )
        )
    if not scenarios:
        raise ScenarioError("no [[scenario]] is declared")
    excluded: dict[int, str] = {}
    for raw in document.get("exclude", []):
        if not str(raw.get("reason", "")).strip():
            raise ScenarioError("[[exclude]] needs a reason")
        for number in parse_range(raw["exchanges"]):
            excluded[number] = str(raw["reason"])
    return Plan(str(sdk["factory"]), mode, tuple(scenarios), excluded)


def resolve(value: Any, secrets: Mapping[str, str]) -> Any:
    """Replace placeholders inside a TOML value with the real secrets.

    TOML has no null: the string ``@none`` stands for ``None``.
    """
    if value == "@none":
        return None
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda match: secrets.get(match.group(0), match.group(0)), value)
    if isinstance(value, list):
        return [resolve(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, secrets) for key, item in value.items()}
    return value


def load_secrets(workdir: Path) -> dict[str, str]:
    """Read the placeholder table of a work directory; empty when it was never redacted."""
    path = workdir / "secrets.json"
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    return {name: str(entry["value"]) for name, entry in document["secrets"].items()}


# --- conformance --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sent:
    """What the SDK put on the wire, read off a transport request."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes

    @classmethod
    def of(cls, request: Any) -> Sent:
        headers = request.headers
        pairs = headers.list() if hasattr(headers, "list") else list(dict(headers).items())
        body = request.body if isinstance(request.body, bytes) else b""
        return cls(str(request.method).upper(), str(request.url), tuple(pairs), body)

    def header(self, name: str) -> str | None:
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)


class Shield:
    """Keeps secret values out of a report."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._values = sorted((v for v in secrets.values() if v), key=len, reverse=True)

    def show(self, value: str, limit: int = 80) -> str:
        for secret in self._values:
            if secret in value:
                value = value.replace(secret, "<secret>")
        return value if len(value) <= limit else value[: limit - 1] + "…"


def _volatile(value: str) -> bool:
    return bool(_TIMESTAMP.match(value))


def _compare_pairs(
    place: str,
    sent: Sequence[tuple[str, str]],
    recorded: Sequence[tuple[str, str]],
    shield: Shield,
    *,
    ordered: bool,
) -> list[str]:
    findings: list[str] = []
    sent_names, recorded_names = [k for k, _ in sent], [k for k, _ in recorded]
    missing = [name for name in dict.fromkeys(recorded_names) if name not in sent_names]
    extra = [name for name in dict.fromkeys(sent_names) if name not in recorded_names]
    if missing:
        findings.append(f"{place}: the SDK does not send {missing}")
    if extra:
        findings.append(f"{place}: the SDK sends {extra}, the capture has none")
    if ordered and not missing and not extra and sent_names != recorded_names:
        findings.append(f"{place}: field order is {sent_names}, recorded {recorded_names}")
    for name in dict.fromkeys(sent_names):
        mine = [value for key, value in sent if key == name]
        theirs = [value for key, value in recorded if key == name]
        if not theirs or mine == theirs:
            continue
        if len(mine) != len(theirs):
            # A name may repeat: one value where the capture has three is a difference.
            findings.append(
                f"{place} {name!r}: the SDK sends it {len(mine)} times, "
                f"recorded {len(theirs)} times"
            )
            continue
        for value, expected in zip(mine, theirs, strict=True):
            if expected == value or _volatile(expected):
                continue
            shown, wanted = _around_difference(value, expected)
            findings.append(
                f"{place} {name!r}: the SDK sends {shield.show(shown)!r}, "
                f"recorded {shield.show(wanted)!r}"
            )
    return findings


def _around_difference(left: str, right: str, width: int = 60) -> tuple[str, str]:
    """Cut two long values to the place where they start to differ."""
    if max(len(left), len(right)) <= width:
        return left, right
    same = next(
        (i for i, (a, b) in enumerate(zip(left, right, strict=False)) if a != b),
        min(len(left), len(right)),
    )
    start = max(0, same - 20)
    lead = "…" if start else ""
    return lead + left[start : start + width], lead + right[start : start + width]


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        out: list[tuple[str, str]] = []
        for key, item in value.items():
            out += _flatten(item, f"{prefix}.{key}" if prefix else str(key))
        return out
    return [(prefix, json.dumps(value, ensure_ascii=False, sort_keys=True))]


def _compare_bodies(sent: Sent, recorded: Exchange, shield: Shield) -> list[str]:
    kind = media_type(recorded.request_headers)
    if not recorded.request_body and not sent.body:
        return []
    if kind == "application/x-www-form-urlencoded":
        return _compare_pairs(
            "form",
            parse_qsl(sent.body.decode("utf-8", "replace"), keep_blank_values=True),
            parse_qsl(recorded.request_body.decode("utf-8", "replace"), keep_blank_values=True),
            shield,
            ordered=True,
        )
    if "json" in kind:
        try:
            mine, theirs = json.loads(sent.body or b"null"), json.loads(recorded.request_body)
        except ValueError:
            return ["body: one of the JSON bodies does not parse"]
        return _compare_pairs("json", _flatten(mine), _flatten(theirs), shield, ordered=False)
    if kind.startswith("multipart/"):
        return []  # the boundary differs on every run: parts are compared by the SDK's tests
    if sent.body != recorded.request_body:
        return [f"body: {len(sent.body)} bytes sent, {len(recorded.request_body)} recorded"]
    return []


def _cookie_names(value: str | None) -> set[str]:
    return {part.split("=", 1)[0].strip() for part in (value or "").split(";") if "=" in part}


def _compare_headers(
    sent: Sent, recorded: Exchange, known_cookies: frozenset[str]
) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    notes: list[str] = []
    sent_names = {name.lower() for name, _ in sent.headers}
    for name, value in recorded.request_headers:
        low = name.lower()
        if low in _TRANSPORT or low.startswith("sec-") or low.startswith(":"):
            continue
        own = low in _MUST_MATCH or low.startswith("x-")
        if low == "referer" and recorded.method.upper() == "GET":
            # On a GET the browser's Referer is where the person happened to be: the page
            # a script ran on, or the page before a navigation. A form names its origin.
            own = False
        if low not in sent_names:
            (findings if own else notes).append(f"header {name}: recorded, not sent by the SDK")
        elif low in {"origin", "referer"} and sent.header(name) != value:
            findings.append(f"header {name}: sent {sent.header(name)!r}, recorded {value!r}")
        elif low == "content-type":
            mine = (sent.header(name) or "").split(";", 1)[0].strip().lower()
            if mine != media_type(recorded.request_headers):
                findings.append(f"header {name}: sent {mine!r}, recorded {value!r}")
    missing = _cookie_names(recorded.request_header("cookie")) - _cookie_names(
        sent.header("cookie")
    )
    # A cookie the scenario's own responses never set was in the browser before the scenario
    # began: the SDK cannot have it, and that is not a difference in the request.
    earlier = sorted(missing - known_cookies)
    if earlier:
        notes.append(f"cookies {earlier} were set before this scenario began")
    if missing & known_cookies:
        findings.append(f"cookies: recorded {sorted(missing & known_cookies)}, not sent by the SDK")
    return findings, notes


def compare(
    sent: Sent,
    recorded: Exchange,
    shield: Shield,
    known_cookies: frozenset[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Return what makes a request differ from the recorded one, and what is merely noted.

    ``known_cookies`` are the cookies the responses served so far have set; ``None`` means
    every recorded cookie is expected.
    """
    if known_cookies is None:
        known_cookies = frozenset(_cookie_names(recorded.request_header("cookie")))
    findings = _compare_pairs(
        "query",
        parse_qsl(urlsplit(sent.url).query, keep_blank_values=True),
        recorded.query,
        shield,
        ordered=False,
    )
    findings += _compare_bodies(sent, recorded, shield)
    header_findings, notes = _compare_headers(sent, recorded, known_cookies)
    return findings + header_findings, notes


# --- playing ------------------------------------------------------------------------------


@dataclass(slots=True)
class Play:
    """The state of one scenario being replayed."""

    queue: list[Exchange]
    shield: Shield
    requests: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    cookies: set[str] = field(default_factory=set)

    def answer(self, request: Any, response_type: Callable[..., Any]) -> Any:
        """Find the recorded exchange for a request and build its response."""
        sent = Sent.of(request)
        parts = urlsplit(sent.url)
        for index, exchange in enumerate(self.queue):
            if (exchange.method.upper(), exchange.host, exchange.path) != (
                sent.method,
                parts.netloc,
                parts.path or "/",
            ):
                continue
            del self.queue[index]
            findings, notes = compare(sent, exchange, self.shield, frozenset(self.cookies))
            for value in exchange.response_headers:
                if value[0].lower() == "set-cookie":
                    self.cookies.add(value[1].split("=", 1)[0].strip())
            self.requests.append(
                {
                    "exchange": exchange.seq,
                    "request": f"{sent.method} {parts.netloc}{parts.path}",
                    "status": exchange.status,
                    "findings": findings,
                    "notes": notes,
                }
            )
            headers = [
                pair
                for pair in exchange.response_headers
                if pair[0].lower() not in _DROPPED_RESPONSE_HEADERS
            ]
            return response_type(
                exchange.status, headers, content=exchange.response_body, request=request
            )
        keys = sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)})
        self.unmatched.append(f"{sent.method} {parts.netloc}{parts.path} ?{','.join(keys)}")
        return response_type(
            _UNMATCHED_STATUS,
            [("content-type", "text/plain")],
            content=b"this request is not in the capture",
            request=request,
        )


def _walk(root: Any, path: str) -> Any:
    target = root
    for name in path.split("."):
        if isinstance(target, Mapping):
            target = target[name]
        elif (
            isinstance(target, Sequence)
            and not isinstance(target, str)
            and name.lstrip("-").isdigit()
        ):
            target = target[int(name)]  # "quotes.0.author": an element by its index
        else:
            target = getattr(target, name)
    return target


async def _settle(value: Any) -> Any:
    if inspect.isawaitable(value):
        value = await value
    if hasattr(value, "__aiter__"):
        return [item async for item in value]
    if inspect.isgenerator(value):
        return list(value)
    return value


def _judge(expect: Mapping[str, Any], result: Any, error: BaseException | None) -> list[str]:
    """Compare the end of a call with what the scenario expects; empty means as expected."""
    if "raises" in expect:
        if error is None:
            return [f"expected {expect['raises']} to be raised, the call returned"]
        names = {cls.__name__ for cls in type(error).__mro__}
        return [] if expect["raises"] in names else [f"expected {expect['raises']}"]
    if error is not None:
        return [f"expected a {expect['returns']} result, the call raised"]
    problems: list[str] = []
    names = {cls.__name__ for cls in type(result).__mro__}
    if expect["returns"] not in names:
        problems.append(f"expected {expect['returns']}, got {type(result).__name__}")
    for path, wanted in (expect.get("fields") or {}).items():
        actual = _walk(result, path)
        wanted = None if wanted == "@none" else wanted
        if actual != wanted:
            problems.append(f"{path}: expected {wanted!r}, got {actual!r}")
    for path, wanted in (expect.get("lengths") or {}).items():
        actual = len(result if path == "." else _walk(result, path))
        if actual != wanted:
            problems.append(f"len({path}): expected {wanted}, got {actual}")
    return problems


def _describe_error(error: BaseException, shield: Shield) -> str:
    chain: list[str] = []
    current: BaseException | None = error
    while current is not None and len(chain) < 4:
        chain.append(f"{type(current).__name__}: {shield.show(str(current), 240)}")
        current = current.__cause__ or current.__context__
    return " <- ".join(chain)


async def _run_async(
    scenario: Scenario, factory: Callable[..., Any], play: Play, options: Any, args: Any
) -> Any:
    from zapros import Response

    from eazy_sdk.testing import AsyncRecordingHandler

    handler = AsyncRecordingHandler(lambda request: play.answer(request, Response))
    opened = factory(handler, **options)
    if inspect.isawaitable(opened):
        opened = await opened
    if hasattr(opened, "__aenter__"):
        async with opened as sdk:
            return await _settle(_walk(sdk, scenario.call)(**args))
    return await _settle(_walk(opened, scenario.call)(**args))


def _run_sync(
    scenario: Scenario, factory: Callable[..., Any], play: Play, options: Any, args: Any
) -> Any:
    from zapros import Response

    from eazy_sdk.testing import RecordingHandler

    opened = factory(RecordingHandler(lambda request: play.answer(request, Response)), **options)
    if hasattr(opened, "__enter__"):
        with opened as sdk:
            value = _walk(sdk, scenario.call)(**args)
            return list(value) if inspect.isgenerator(value) else value
    value = _walk(opened, scenario.call)(**args)
    return list(value) if inspect.isgenerator(value) else value


def run_scenario(
    scenario: Scenario,
    capture: Capture,
    factory: Callable[..., Any],
    mode: str,
    secrets: Mapping[str, str],
) -> dict[str, Any]:
    """Replay one scenario and describe how it went."""
    by_seq = {exchange.seq: exchange for exchange in capture.exchanges}
    unknown = [number for number in scenario.exchanges if number not in by_seq]
    if unknown:
        raise ScenarioError(f"scenario {scenario.name!r}: no exchanges {unknown} in the capture")
    shield = Shield(secrets)
    play = Play([by_seq[number] for number in scenario.exchanges], shield)
    options, args = resolve(dict(scenario.options), secrets), resolve(dict(scenario.args), secrets)
    result: Any = None
    error: BaseException | None = None
    try:
        if mode == "async":
            result = asyncio.run(_run_async(scenario, factory, play, options, args))
        else:
            result = _run_sync(scenario, factory, play, options, args)
    except Exception as caught:  # noqa: BLE001 - any end of the call is a result to report
        error = caught
    problems = _judge(scenario.expect, result, error)
    outcome = _describe_error(error, shield) if error else f"returned {type(result).__name__}"
    findings = [
        f"#{item['exchange']} {text}" for item in play.requests for text in item["findings"]
    ]
    return {
        "name": scenario.name,
        "call": scenario.call,
        "outcome": outcome,
        "as_expected": not problems,
        "problems": problems,
        "requests": play.requests,
        "not_in_capture": play.unmatched,
        "unused_exchanges": [exchange.seq for exchange in play.queue],
        "findings": findings,
    }


def load_factory(spec: str) -> Callable[..., Any]:
    """Import ``module:callable`` from the SDK project."""
    module_name, _, attribute = spec.partition(":")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise ScenarioError(f"{spec} is not callable")
    return factory  # type: ignore[no-any-return]


def run(
    plan: Plan, capture: Capture, secrets: Mapping[str, str], noise: Mapping[int, str]
) -> dict[str, Any]:
    """Replay every scenario and decide the four replay gates."""
    factory = load_factory(plan.factory)
    scenarios = [
        run_scenario(scenario, capture, factory, plan.mode, secrets) for scenario in plan.scenarios
    ]
    claimed = {number for scenario in plan.scenarios for number in scenario.exchanges}
    excluded = {**noise, **plan.excluded}
    uncovered = [e.seq for e in capture.exchanges if e.seq not in claimed and e.seq not in excluded]
    unused = sorted({number for item in scenarios for number in item["unused_exchanges"]})
    gates = {
        "G4 every scenario ends as expected": all(item["as_expected"] for item in scenarios),
        "G5 no request outside the capture": not any(item["not_in_capture"] for item in scenarios),
        "G6 every exchange is used or excluded": not uncovered and not unused,
        "G7 requests match the recorded ones": not any(item["findings"] for item in scenarios),
    }
    return {
        "format": REPORT_FORMAT,
        "gates": gates,
        "passed": all(gates.values()),
        "scenarios": scenarios,
        "uncovered_exchanges": uncovered,
        "unused_exchanges": unused,
        "excluded": {str(number): reason for number, reason in sorted(excluded.items())},
        "capture_warnings": capture.warnings,
    }


def load_noise(workdir: Path) -> dict[int, str]:
    """Read the exchanges the inventory left out as noise."""
    path = workdir / "inventory.json"
    if not path.exists():
        return {}
    noise = json.loads(path.read_text(encoding="utf-8")).get("noise", {})
    return {int(number): str(reason) for reason, numbers in noise.items() for number in numbers}


def render(report: Mapping[str, Any]) -> str:
    """Render a replay report for the console."""
    lines: list[str] = []
    for scenario in report["scenarios"]:
        mark = "ok  " if scenario["as_expected"] else "FAIL"
        lines.append(f"[{mark}] {scenario['name']}: {scenario['call']} -> {scenario['outcome']}")
        lines += [f"       problem: {problem}" for problem in scenario["problems"]]
        for request in scenario["requests"]:
            lines.append(
                f"       #{request['exchange']} {request['request']} -> {request['status']}"
            )
            lines += [f"         differs: {text}" for text in request["findings"]]
            lines += [f"         note: {text}" for text in request["notes"]]
        lines += [f"       NOT IN THE CAPTURE: {text}" for text in scenario["not_in_capture"]]
        if scenario["unused_exchanges"]:
            lines.append(f"       never requested: exchanges {scenario['unused_exchanges']}")
    if report["uncovered_exchanges"]:
        lines.append(f"in no scenario and not excluded: exchanges {report['uncovered_exchanges']}")
    lines += [f"capture warning: {warning}" for warning in report["capture_warnings"]]
    lines += [f"{'PASS' if passed else 'FAIL'}  {gate}" for gate, passed in report["gates"].items()]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Replay the scenarios of an SDK project against a work directory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path.cwd()))
    try:
        plan = load_plan(args.scenarios)
        capture = load(args.workdir / "capture.json")
        report = run(plan, capture, load_secrets(args.workdir), load_noise(args.workdir))
    except ScenarioError as error:
        print(f"scenarios: {error}", file=sys.stderr)
        return 2
    target = args.report or args.workdir / "replay-report.json"
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(render(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
