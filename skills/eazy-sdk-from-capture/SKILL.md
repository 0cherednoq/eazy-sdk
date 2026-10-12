---
name: eazy-sdk-from-capture
description: Write a declarative Python SDK on eazy-sdk for a private or undocumented web API from recorded traffic, and prove it by replaying the recording offline. Use when the user wants an SDK or client "from a HAR", "from a Burp export", "from recorded traffic", asks to record a browser session and turn it into an SDK, or to describe a site's private API with eazy-sdk.
---

# SDK from recorded traffic

You write the SDK. Scripts give you the facts and check the result: you never read a raw
recording and you never judge your own result by eye.

Paths below are relative to this skill's directory. Work files go to `.sdk-work/` in the SDK
project; that directory ignores itself for git. Talk to the user in the language they use.

## Rules that hold at every step

- **Never open** `capture.json`, `secrets.json`, `typed.json` or a recording directory. They
  hold live passwords, cookies and tokens. Read `inventory.md`, then `inventory.json`, and
  `capture.redacted.json` only when the inventory is not enough.
- **Write only what the recording shows.** No operation, error class or branch for something
  that was not observed. Name it in the report as not observed instead.
- **Do not work around the library.** If a thing cannot be declared, write it into
  `LIBRARY-GAPS.md` with a minimal example and mark the operation blocked.
- **A gate that did not run is not a pass.** Say exactly which gate was skipped and why.

## Steps

Each step has an exit condition. Do not start the next step before it holds.

### 0. Intake

Ask only what the recording cannot tell: package name and target directory, which scenarios
matter (default: everything recorded), and whether a recording already exists. Defaults you do
not ask about: pydantic models, async SDK, curl_cffi transport with `impersonate="chrome"`.

### 0a. Record (skip when the user already has a recording)

Read `references/capture-howto.md`, tell the user what to do in the browser, then run in the
background and wait for it to exit:

```
uv run --with pydoll-python python scripts/capture_record.py --url <start url> --out captures/
```

The user signs in and does what the SDK must repeat, names each scenario in the overlay
("Mark"), then presses "Finish" or closes the browser. Exit: the script printed the recording
directory.

### 1. Normalise

```
python -I scripts/capture_normalize.py <recording dir | file.har | burp export> --out .sdk-work
```

Exit: `capture.json` is written. Read every WARNING. A lossy source (Burp export without
base64) is fine for the inventory but cannot prove response cases that match on text: tell the
user now and ask for a re-export or a new recording before promising a green replay.

### 2. Redact

```
python -I scripts/capture_redact.py .sdk-work
```

Exit: exit code 0. A non-zero code means a secret survived; stop and report it.

### 3. Inventory

```
python -I scripts/capture_inventory.py .sdk-work
```

Exit: you have read `.sdk-work/inventory.md` in full. It lists hosts, scenarios, operations
with every parameter and where its value came from, response variants, redirect chains,
cookies, envelope candidates, pagination hints, noise and out-of-scope signals.

If "Out of scope signals" names a signature, an encrypted body, anti-bot markers or WebSocket
traffic, tell the user before going on: this skill names those, it does not solve them.

### 4. Plan — the one mandatory stop

Read `references/layout.md` and `references/operations.md`. Write `.sdk-work/sdk-plan.md`:

- hosts and the router classes over them;
- every operation: name, method and path, public parameters, what goes to `inject`, the
  result model, each response case with the exchange numbers that show it;
- how the session is obtained and what it places into requests (`references/auth-session.md`);
- the exception hierarchy, one class per **observed** refusal;
- scenarios for the replay, with the exchanges each one covers;
- exchanges left out, each with a reason;
- what was not observed and what you are assuming.

Show the plan and wait for the user's approval. This is where the user decides what the
recording cannot: names, which parameters are public, which scenarios to keep.

### 5. Write

```
python scripts/sdk_scaffold.py --package <name> --root <project dir>
```

Then write the package following the plan and the references: `references/responses.md` for
response cases and HTML extraction, `references/auth-session.md` for sign-in,
`references/pagination.md`, and `references/anti-patterns.md` before you finish. Fill in
`tests/replay/factory.py` and `tests/replay/scenarios.toml` (`references/replay.md`).

Exit: the package imports.

### 6. Check

From the project root, in its environment:

```
python <skill>/scripts/sdk_check.py .sdk-work --package <name> --scenarios tests/replay/scenarios.toml
```

Add `--ignore <dir>` for a directory that legitimately holds raw recordings. Fix the SDK and
rerun until every gate passes. Read `references/replay.md` for what each finding means. Never
make a gate pass by excluding an exchange you cannot explain or by loosening a scenario.

### 7. Report

Write `REPORT.md` in the project from `.sdk-work/check-report.json`:

- the gates, each with its status; skipped ones with the reason;
- what the replay proved, scenario by scenario;
- what you assumed and what was not observed;
- how to check against the live site (`examples/live_check.py`), which needs the user's
  credentials and is not part of the gates.

## Scripts

| Script | What it does |
|---|---|
| `capture_record.py` | records a browser session: traffic, actions, marks |
| `capture_normalize.py` | recording, HAR or Burp XML → `capture.json` |
| `capture_redact.py` | secrets → placeholders, `capture.redacted.json` + `secrets.json` |
| `capture_inventory.py` | facts → `inventory.md`, `inventory.json` |
| `sdk_scaffold.py` | project skeleton with the replay files |
| `sdk_replay.py` | replays scenarios through the SDK, gates G4–G7 |
| `sdk_check.py` | all gates G1–G10, one report |
| `refs_freshness.py` | the references still match the installed library |
