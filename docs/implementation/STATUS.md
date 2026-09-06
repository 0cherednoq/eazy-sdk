# Eazy SDK rewrite status

Updated: 2026-09-06.

This file is evidence-driven: a phase is complete only when its documented exit criteria have
implementation and verification evidence.

| Phase | State | Evidence | Remaining work / blockers |
|---|---|---|---|
| 00 | complete | Clean initial worktree; legacy baseline 625 passed, 1 skipped; mypy/ruff clean; import freeze, local raw capture, defect characterization and strict target xfails recorded. | None. |
| 01 | complete | Identity slots/values/bindings, atomic patches, graph compiler and scope lowering; 7 focused tests and typing/lint gates. | None. |
| 02 | complete | Deterministic target/header/cookie and JSON/form/multipart/raw/compressed codecs with immutable prepared artifacts; 9 focused tests. | None. |
| 03 | complete | Prepared-only HTTPX, Requests, curl_cffi and wreq adapters; granular preflight and local first-hop capture; 11 focused tests. Capture claims are tied to the tested dependency versions and downgrade on unknown versions. | None. |
| 04 | complete | Prepared signing, projections, reserved outputs, multi-signature DAG, custom signer and captured-byte recomputation; 8 focused tests. | None. |
| 05 | complete | Unified non-throwing JSON/HTML/text/bytes/empty cases, arbitrary parser protocol, cached parser sessions and typed outcomes; 9 focused tests. | None. |
| 06 | complete | External signals, reactions, before-call flows, solver identities, atomic solution patches, replay proofs/budgets and cycle validation; 4 focused tests. | None. |
| 07 | complete | Typed dependency DAG/cache/bindings, OR-of-AND auth, session singleflight/cycle guard and generic storage bridge; focused rewrite and storage/SQLModel regression evidence. | None. |
| 08 | complete | One task-safe executor for sync/async, generic/generated calls, auth, dependencies, limiter, signing, redirects, retries, middleware, reactions and nested before-call flows; 11 focused tests. | None. |
| 09 | complete | OpenAPI 3.0/3.1/3.2 IR, interned references, canonical extension validation/lowering, typed dependency/provider/signature facades, stable `eazy_sdk.codegen`, deterministic atomic generation, import/execution and strict-mypy generated SDK gates; 8 tests. | None. |
| 10 | complete | Assigned legacy modules/symbols/tests/examples removed; docs/README migration and fingerprints complete; source and built-artifact absence audits, full gates, docs build, wheel/sdist and isolated install gates pass. | None. |
| 11 | complete | Separate typed `eazy-sdk-presets` package; immutable Cloudflare Challenge Pages/Turnstile and reCAPTCHA v2/v3/Enterprise factories; redaction, malformed/unknown/oversized fixtures, parser/application overrides, replay and network-scoped expiry singleflight; 17 focused tests and isolated extra installs. | None. |
| 12 | complete | One operation `TypedDict`, short placement/body markers, identity-based flat body slots, immutable `EndpointCall`, bound-only execution, migrated core/plugins, generated `inputs.py`/`Unpack` routers, deterministic name normalization, flat-vs-root OpenAPI body classification, Museum/Petstore snapshots and all release gates. | None. |
| 13 | complete | `pytest-httpserver` localhost auth suite, seven-adapter DI matrix, revision-safe `401 -> refresh -> replay`, optional real `pytest-iam` OAuth/OIDC suite, generated security smoke, redacted telemetry and all core gates pass. | None. |
| 14 | complete | Exact `FromHeader`, high-level auth/session and generated session factories, static auth, immutable `ClientConfig`, safe retry and minimal namespace allowlists are implemented and release-gated. | None. |
| 15 | complete | Transport-neutral account/session lifecycle, explicit domain/wire separation, bounded resumable verification, atomic memory/SQLModel persistence, HTTP/session handoff and fake browser boundary are implemented and release-gated. | None; the optional browser package and optional OpenAPI registration extension were deliberately not released. |
| 16 | complete | Five-table SQLModel v2, codecs, migration, registration store, events and reservations are implemented. `SqlSessionStore` now implements the neutral new-revision save contract and is verified through the real `session_auth` login/401-refresh persistence path. | None. |
| 17 | complete | Decorated sync/async API methods are the only typed operation API; private operation lowering, `.with_response`, bounded mandatory protection flows, canonical protection IR, generated Pydantic/wire models, first-party migration, documentation and release gates pass. | None. |
| 18 | complete | Zapros 0.16 is the only HTTP/handler boundary; model adapters, codecs, extractor-based JSON/XML/HTML, compact Inject, fresh-attempt signing, generated SDK migration, legacy absence and all available release gates pass. | None. The pre-generated consumer fixture remains unreadable on this Windows workspace, so full pytest/mypy use the documented exclusion while generated Museum import/strict-mypy runs normally. |
| 19 | complete | All WS-00–WS-07 exit criteria and Definition of Done items pass: the separate Zapros runtime, GraphQL-WS plugin, AsyncAPI 3.0 generator/CLI, docs, packages, isolation and full release gates have recorded evidence. | None. |
| 20 | complete | Common declarations/compiler, HTTP sync/async and WebSocket lowering, old WS protector removal, named OpenAPI/AsyncAPI bindings, the frozen PC-06 typed AAD/reserved metadata-output contract and all configured core gates pass. | None. |
| 21 | complete | All BP-00 through BP-06 exit criteria pass: typed public projection, private writers, crypto/signing ordering, OpenAPI codegen, examples/docs, absence, packages and full release gates have evidence. | None. |
| 22 | complete | Core plus five first-party plugins, three CLIs, protocol extensions, environment variables, generated output, tests and docs use only the target identity; full runtime, docs, package and absence gates pass. | None. |
| 23 | complete | PC-00–PC-04: optional projection omission, explicit `None`, required binding, per-attempt copies, structured diagnostics, strict docs and all release gates have evidence. | None. |
| 24 | complete | PP-00–PP-05: typed lifecycle config, compiler-reserved atomic private bindings, scoped persistent state, preset migration, SPI docs and all release gates pass. | None. |
| 25 | complete | AE-00–AE-06: two typed request styles, response/parser shorthand, shared-path preparation, recording handlers, root lifecycle, method coverage and release gates pass. | None. |
| 26 | complete | RH-00–RH-05: Python 3.13/3.14 policy, typed HTTP/compiler/WebSocket ownership and all final runtime/docs/package/isolation gates have evidence. | None. |
| 27 | complete | EX-00–EX-03: 23-name extension SPI, author reference, alpha metadata correction and full release evidence. | None. |
| 28 | complete | AP-00–AP-07: tracked workspace metadata, per-attempt identity, capability/identity preflight, immutable bundles, replay/public cleanup, bounded locks, docs and all release gates pass. | None. |
| 29 | complete | AS-00–AS-06: high-level guard builder, session-owned affinity, identity/capability removal, 19-name public API, 49-name advanced SPI, migrated presets/docs and all release gates. | None. |

## Production authoring audit remediation planning (2026-09-01)

### State

Planning complete. Runtime implementation started with PC-00. Phase 23 is the earliest incomplete
phase and PC-01 is the next executable increment. Phases 00–22 remain historically complete; the
new work reopens only the beta/stable production-hardening gate.

## Phase 23 PC-00 red baseline (2026-09-01)

### Delivered

- Added `tests/unit/test_phase23_projection_omission.py` with public async execution coverage for
  omitted `TypedDict(total=False)` projection keys, explicit `None`, missing required keys and an
  omitted-key transport retry.
- The mapper and handler counters prove required missing input is rejected before either effect.
- The red result isolates the defect to `_project_body()` unconditionally calling
  `OperationValues.require()` for an absent optional projection slot.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| PC-00 regression plus phase-21 baseline | `uv run pytest -q tests/unit/test_phase23_projection_omission.py tests/unit/test_phase21_body_projection.py tests/unit/test_phase21_body_projection_runtime.py` | EXPECTED RED: 2 failed, 26 passed. Both omitted-key cases fail with `BindingError: slot has no value: body-projection.source.page`; explicit `None`, required missing-before-effects and all prior phase-21 cases pass. |

### Remaining work

PC-02 replaces caller-visible internal-slot diagnostics with the structured operation error
contract. No phase-23 completion gate is claimed yet.

## Phase 23 PC-01 projection omission fix (2026-09-01)

### Delivered

- `_project_body()` now includes a projection source field only when its logical slot is present.
- Explicit `None` remains present, while an omitted optional key remains absent for the mapper on
  the initial attempt and transport retry.
- Every present source value is still deep-copied per attempt. A target-family matrix covers
  `TypedDict`, dataclass, Pydantic and msgspec targets with a mapper that mutates its input; each
  retry sees the original caller list and the caller object remains unchanged.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Projection/runtime/capture focus | `uv run pytest -q tests/unit/test_phase23_projection_omission.py tests/unit/test_phase21_body_projection.py tests/unit/test_phase21_body_projection_runtime.py tests/integration/test_phase21_projection_wire_capture.py` | PASS: 33 passed. |
| Focused static typing | `uv run mypy tests/unit/test_phase23_projection_omission.py eazy_sdk/clients/executor.py` | PASS: no issues in 2 source files. |
| Focused lint | `uv run ruff check tests/unit/test_phase23_projection_omission.py eazy_sdk/clients/executor.py` | PASS. |

## Phase 23 PC-02 structured diagnostics (2026-09-01)

### Delivered

- Added public `OperationBindingError` with keyword-only construction and stable `code`,
  `operation_id`, `field`, `phase` plus secret-free `as_dict()` serialization.
- Public method binding lowers unknown, missing-required and invalid values without exposing caller
  values or compiler slot names. Nested model validation reports the caller path when available.
- Projection mapper and target-validation failures use the same error contract. Target validation
  retains paths such as `account.login`; mapper exceptions retain no unsafe cause or value.
- Request preparation catches residual internal `BindingError` at the executor boundary and
  lowers it to the same operation error. The old separate `BodyProjectionError` surface was
  removed instead of retained as an alias or second error path.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Binding/projection regression focus | `uv run pytest -q tests/unit/test_phase23_projection_omission.py tests/unit/test_phase21_body_projection.py tests/unit/test_phase21_body_projection_runtime.py tests/integration/test_phase21_projection_wire_capture.py tests/rewrite/test_phase01_core.py tests/unit/test_flat_request_bodies.py` | PASS: 49 passed. |
| Focused static typing | `uv run mypy eazy_sdk/_internal/errors.py eazy_sdk/_internal/kernel.py eazy_sdk/_internal/http_compiler.py eazy_sdk/clients/executor.py tests/unit/test_phase23_projection_omission.py tests/unit/test_phase21_body_projection_runtime.py tests/unit/test_flat_request_bodies.py` | PASS: no issues in 7 source files. |
| Focused lint | matching `uv run ruff check` command over the same implementation/tests | PASS. |

## Phase 23 PC-03 strict authoring documentation (2026-09-01)

### Delivered

- Hand-written API examples now use explicit return annotations and
  `raise NotImplementedError`; the quickstart no longer disables `no-untyped-def`.
- API-method documentation explicitly rejects ordinary `...` method bodies under strict mypy.
- Request reference and JSON guide document omitted optional keys, explicit `None`, required
  missing values, per-attempt deep copies and `OperationBindingError` fields/redaction.
- Updated the local authoritative SDK authoring reference and public quickstart/API/reference/
  tutorial pages; refreshed only the affected public API fingerprints after content changes.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Runnable strict example | `uv run python examples/quickstart.py`; `uv run mypy --strict examples/quickstart.py` | PASS: expected output; no issues in 1 source file. |
| Documentation examples | `uv run pytest -q tests/unit/test_docs_examples.py` | PASS: 12 passed. |
| Documentation metadata | `uv run python docs-site/scripts/validate_docs.py` | PASS: 72 pages valid. |
| Documentation freshness | update affected request/quickstart fingerprints, then `uv run python scripts/docs_freshness.py check` | PASS: 58 pages fresh. |
| Strict documentation render | `uv run --frozen --group docs sphinx-build -W --keep-going -b dirhtml -c docs-site docs-site/src/content/docs docs-site/_build/html` | PASS: 72 sources; build succeeded without warnings. |

## Phase 23 PC-04 release closure (2026-09-01)

### Delivered

- Finalized the public diagnostics surface and refreshed affected API fingerprints after the
  documentation described the new contract.
- Marked future phase-25 candidate snippets as `python target`, so current-API documentation tests
  do not validate an intentionally not-yet-implemented signature style before AE-01.
- Built and audited all six workspace distributions, then installed all wheels together into a
  clean Python 3.14 environment and imported every package plus the new error contract under
  isolated mode.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Initial full suite | `uv run pytest -q` | EXPECTED GATE FAILURE: 2 documentation-example checks treated phase-25 target snippets as current API; 812 passed, 11 skipped. No runtime test failed. |
| Target-fence correction | `uv run pytest -q tests/unit/test_documentation_api_examples.py` | PASS: 4 passed. |
| Final full suite | `uv run pytest -q` | PASS: 814 passed, 11 skipped in 156.73s. Skips remain opt-in PostgreSQL (`EAZY_SDK_POSTGRES_DSN` unset), public WebSocket (`EAZY_SDK_RUN_LIVE_WS` unset) and absent local real-HTML fixtures; the configured suite excludes auth-conformance tests by marker. |
| Skip reason audit | `uv run pytest -q -rs plugins/sqlmodel/tests/test_postgresql_v2.py tests/test_real_html.py tests/websocket/test_live_exchange_soak.py` | PASS: environment-gated cases report their explicit PostgreSQL, real-HTML and live-WebSocket reasons. |
| Static typing | `uv run mypy` | PASS: no issues in 259 source files. |
| Lint | `uv run ruff check` | Initial sort-only `__all__` finding corrected; final rerun PASS. |
| Generated consumers | `uv run pytest -q plugins/openapi/tests/test_phase21_body_projection_codegen.py plugins/openapi/tests/test_rewrite_generator.py plugins/asyncapi/tests/test_asyncapi_generator.py` | PASS: 57 passed, including generated import/execution/strict typing gates. |
| Docs/freshness/absence/lock | `docs_freshness.py check`; docs metadata validator; `absence_audit.py`; `uv lock --check` | PASS: 58 pages fresh, 72 pages valid, absence audit clean and 156 packages resolved. |
| Package build | `uv build --all-packages --out-dir .test-tmp/phase23-dist-20260901` | PASS: six wheel/sdist pairs built. |
| Package audit | `uv run python scripts/package_audit.py .test-tmp/phase23-dist-20260901` | PASS: metadata, licenses, typing markers, Zapros boundary and legacy absence. |
| Isolated install | create Python 3.14 venv, install all six built wheels, run `python -I` imports and `OperationBindingError.as_dict()` assertion | PASS: six imports and diagnostics contract. |
| Patch integrity | `git diff --check` | PASS; only the existing fingerprint-lock CRLF conversion warning. |

### Phase decision

Every phase-23 exit criterion has direct evidence. Phase 23 is complete; phase 24 PP-00 is the
earliest incomplete checkpoint.

## Phase 24 PP-00–PP-05 completion (2026-09-01)

### Delivered

- PP-00 preserved the source characterization from the production audit: the former
  `ClientConfig.protections/signals/solvers/protection_solvers` path used `tuple[object, ...]`,
  executor `getattr` discovery, a single reaction target and no reaction persistence. Added a
  strict-mypy negative fixture for malformed config and misspelled preset capabilities.
- PP-01 replaced the ambiguous fields with typed `operation_protections`,
  `before_call_policies`, `challenge_policies`, `operation_protection_solvers` and
  `challenge_solvers`. Policies lower to closed immutable executor records before provider I/O;
  no compatibility reader or alias exists.
- PP-02 added compiler-reserved secret `PrivateBindings` for header/query/cookie/body plus dynamic
  cookie sets. Source selection and every destination are validated before one atomic patch;
  compiler/runtime conflicts reject the whole batch and private targets remain absent from public
  operation signatures.
- PP-03 added per-match/per-call/per-attempt/until-expiry/until-rejected lifetimes, policy and
  solver identity, client/session/network/challenge/revision cache partitioning, expiry bounds,
  repeated-challenge invalidation, compatible-call reuse, single-flight and cancellation-safe
  publication independent of auth state.
- PP-04 migrated Cloudflare, Turnstile and reCAPTCHA presets to distinct conditional or before-call
  policy types. Cloudflare now applies every `CloudflareClearance.cookies` item. The KAD-shaped
  integration covers two cookies, private header/query, network/session/revision isolation, auth
  coexistence, repeated rejection and concurrent matches.
- PP-05 published the lifecycle/SPI, persistence, concurrency, exception, redaction and replay
  contract plus a custom compound challenge example. OpenAPI generated config uses the new
  mandatory-flow field; fingerprints, package artifacts and isolated imports were refreshed.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Focused phase-24/runtime/preset/mandatory suite | `uv run pytest -q tests/unit/test_phase24_typed_protection_policies.py plugins/presets/tests/test_presets.py tests/rewrite/test_phase08_execution.py tests/rewrite/test_phase17_declarative_api.py` | PASS: 53 passed. |
| Final full suite | `uv run pytest -q` | PASS: 826 passed, 11 skipped in 159.59s. Skips remain the documented opt-in PostgreSQL/live-WebSocket/real-HTML environment cases and configured marker exclusion. |
| Static typing | `uv run mypy` | PASS: no issues in 260 source files. The focused negative fixture separately proves malformed policy/capability rejection. |
| Lint and patch integrity | `uv run ruff check`; `git diff --check` | PASS; only the fingerprint-lock CRLF conversion warning remains. |
| Generated consumers | `uv run pytest -q plugins/openapi/tests/test_phase21_body_projection_codegen.py plugins/openapi/tests/test_rewrite_generator.py plugins/asyncapi/tests/test_asyncapi_generator.py` | PASS: 57 passed, including generated import/execution/strict-mypy gates. |
| Docs examples and metadata | `uv run pytest -q tests/unit/test_docs_examples.py tests/test_docs_freshness.py`; `uv run --group docs python docs-site/scripts/validate_docs.py` | PASS: 21 tests and 72 validated pages. |
| Docs freshness/build | `uv run python scripts/docs_freshness.py check`; `uv run --group docs sphinx-build -W --keep-going -b dirhtml -c docs-site docs-site/src/content/docs docs-site/_build/html` | PASS: 58 pages fresh; strict 72-source build succeeded. `npm run check/build` are not runnable because the migrated docs-site has no `package.json`; Sphinx is its repository-defined gate. |
| Absence and lock | `uv run python scripts/absence_audit.py`; `uv lock --check` | PASS: identity/legacy/runtime absence audit clean; 156 packages resolved. |
| Package build/audit | `uv build --all-packages --out-dir .test-tmp/phase24-dist-20260901`; `uv run python scripts/package_audit.py .test-tmp/phase24-dist-20260901` | PASS: six wheel/sdist pairs; metadata, licenses, typing markers, Zapros boundary and legacy absence valid. |
| Isolated install | create `.test-tmp/phase24-venv-20260901` with Python 3.14, install all six wheels, run `python -I` imports/new-config assertion | PASS: all six packages and typed protection surface import from built artifacts. |

### Phase decision

Every phase-24 exit criterion has direct evidence. Phase 24 is complete; phase 25 AE-00 is the
earliest incomplete checkpoint.

## Phase 25 AE-00–AE-06 completion (2026-09-01)

### Delivered

- AE-00 added isolated mypy and basedpyright positive/negative proofs for direct defaults,
  `Unpack[TypedDict]`, descriptor results, parser typing, decorator keyword rejection, root groups
  and `.prepare()`. Runtime signature and collision tests cover the same public declarations.
- AE-01 made direct keyword-only parameters a first-class request style while preserving
  `Unpack[TypedDict]`. Both lower through `MethodInputSchema`; `BodyProjection` receives
  materialized direct defaults, keeps omitted `NotRequired` keys absent and rejects mixed styles.
  A KAD-shaped operation executes without a forwarding wrapper.
- AE-02 added `response=`, inherited/local `errors=`, `inherit_errors`, model inference from the
  explicit return annotation and immediate normalization to `Responses`. Added typed
  `callable_parser(model, callback)` and removed the former untyped `CallableParser` without an
  alias after migrating first-party presets and tests.
- AE-03 added immutable redacted `PreparedCall`, pure/full `PrepareOptions`, typed
  `PreparationIncomplete` and one stop-before-send boundary in the existing executor. Full
  preparation uses isolated protection state; neither mode sends a request or commits runtime
  protection state. Zapros-native sync/async recording handlers observe the actual send boundary.
- AE-04 added typed `SyncSdk`/`AsyncSdk`, lazy `api_group`, `from_client`/`from_handler` and exact
  owned/borrowed/scoped lifecycle. OpenAPI roots now inherit these bases and retain generated
  session/auth behavior without duplicate close/context-manager code.
- AE-05 added HEAD/OPTIONS/TRACE and validated arbitrary method tokens through the existing verb
  compiler and raw-client request path. Public docs now include request-style/default decisions,
  response choices, ownership, concurrency, error/redaction and extension-stability contracts.
- AE-06 refreshed deterministic OpenAPI snapshots/hashes, package artifacts and isolated imports;
  absence audit confirms there is no compatibility parser, binder, preparer or execution path.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Focused authoring/runtime/codegen | aggregate phase-25, request/projection, response/executor, presets and OpenAPI generator/real-world suite | PASS: 176 passed. |
| Initial full suite | `uv run pytest -q` | EXPECTED GATE FAILURE: 5 failed, 840 passed, 11 skipped. Two new docs blocks violated the repository's self-contained/signature policy and three phase-21 deterministic hashes still described the pre-root-template output; no runtime implementation test failed. |
| Corrected focused failures | `uv run pytest -q tests/unit/test_documentation_api_examples.py plugins/openapi/tests/test_phase21_body_projection_codegen.py` | PASS: 22 passed; a subsequent documentation-only rerun passed 4 tests. |
| Final full suite | `uv run pytest -q` | PASS: 845 passed, 11 skipped in 163.55s. Skips remain the documented opt-in PostgreSQL/live-WebSocket/real-HTML environment cases and configured marker exclusion. |
| Static typing | `uv run mypy`; phase-25 mypy/basedpyright fixtures | PASS: no issues in 265 source files; positive fixtures pass and negative calls/parser/decorator typos are rejected by both checkers. |
| Lint and patch integrity | `uv run ruff check`; `git diff --check` | PASS; only the existing fingerprint-lock CRLF conversion warning remains. |
| Documentation examples/metadata | `uv run pytest -q tests/unit/test_docs_examples.py tests/unit/test_documentation_api_examples.py tests/test_docs_freshness.py`; `uv run --group docs python docs-site/scripts/validate_docs.py` | PASS: 25 tests and 73 validated pages. |
| Docs freshness/build | `uv run python scripts/docs_freshness.py check`; `uv run --group docs sphinx-build -W --keep-going -b dirhtml -c docs-site docs-site/src/content/docs docs-site/_build/html` | PASS: 59 pages fresh; strict 73-source build succeeded. `docs-site/package.json` is absent, so the obsolete npm gates are not runnable; Sphinx is the repository-defined renderer. |
| Absence and lock | `uv run python scripts/absence_audit.py`; `uv lock --check` | PASS: identity/legacy/runtime absence audit clean; 156 packages resolved. |
| Package build/audit | `uv build --all-packages --out-dir .test-tmp/phase25-dist-20260901-final`; `uv run python scripts/package_audit.py .test-tmp/phase25-dist-20260901-final` | PASS: six wheel/sdist pairs; metadata, licenses, typing markers, Zapros boundary and legacy absence valid. |
| Isolated install | create `.test-tmp/phase25-venv-20260901-final` with Python 3.14, install all six wheels, run `python -I` imports of all packages and phase-25 public symbols | PASS: all distributions and new root/preparation/testing/parser surfaces import from built artifacts. |

### Phase decision

Every phase-25 exit criterion has direct evidence. Phase 25 is complete; phase 26 RH-00 is the
earliest incomplete checkpoint.

## Phase 26 RH-00 compatibility and behavior baseline (2026-09-01)

### Delivered

- Confirmed the pre-change policy: all six distributions, `uv.lock` and mypy target Python 3.14.
  Managed CPython 3.12.13, 3.13.12 and 3.14.3 are locally available.
- Built all six distributions with 3.12 and 3.13 build environments without changing metadata;
  installation is correctly rejected by the existing `Requires-Python >=3.14`. This proves build
  feasibility only and makes no support claim.
- Added an exact sync/async HTTP transition trace, fresh-attempt identity assertion and frozen
  compiler fingerprint in `tests/unit/test_phase26_runtime_baseline.py`.
- Recorded the executor/compiler/WebSocket ownership map, preserved transition suites and
  coverage-guided high-risk branches in `26-runtime-ownership-baseline.md`.
- No runtime or public API implementation changed in RH-00.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Python availability | `uv python list` | PASS: managed CPython 3.12.13, 3.13.12 and 3.14.3 available. |
| 3.12/3.13 build feasibility | `uv build --python 3.12/3.13 --all-packages` to separate RH-00 directories | PASS: six wheel/sdist pairs on each interpreter. |
| Pre-change install policy | install the respective built wheels into fresh 3.12/3.13 venvs | EXPECTED REJECTION: all six distributions consistently declare `Requires-Python >=3.14`. |
| Frozen HTTP/WS behavior | `uv run pytest -q tests/unit/test_phase26_runtime_baseline.py tests/rewrite/test_phase08_execution.py tests/websocket/test_ws03_runtime.py tests/websocket/test_ws04_subscriptions.py` | PASS: 34 passed. |
| Focused typing/lint | `uv run mypy tests/unit/test_phase26_runtime_baseline.py`; `uv run ruff check tests/unit/test_phase26_runtime_baseline.py` | PASS after import ordering; no typing issues. |
| Hotspot branch characterization | focused phase-08/21/24 plus all WebSocket tests with branch coverage for executor/compiler/WS runtime | EXPECTED COVERAGE GATE FAILURE: 124 passed, 8 skipped; 62.67% combined versus 80% threshold. Per-module baseline: compiler 60%, executor 53%, WebSocket runtime 73%; exact uncovered lines are recorded in command output and risk categories in the ownership baseline. |

### Remaining work

RH-01 must lower and prove the metadata/runtime matrix on 3.13 and publish the independent 3.12
feasibility result. Internal extraction remains blocked until RH-01 completes.

## Phase 26 RH-01 Python 3.13 support (2026-09-01)

### Delivered

- Lowered `Requires-Python` from 3.14 to 3.13 consistently for core, five plugins and the generated
  consumer fixture; added both 3.13 and 3.14 classifiers. Aligned `.python-version`, Ruff, mypy,
  Read the Docs, installation docs and `uv.lock`.
- Replaced six Python-3.14-only unparenthesized multi-exception handlers with the equivalent syntax
  supported by 3.13. No runtime version branch or compatibility API was added.
- Added `from __future__ import annotations` to every OpenAPI generated module so recursive
  self-return annotations import correctly on 3.13; refreshed snapshots and deterministic hash.
- Added missing reproducibility dependencies `basedpyright` and `eazy-sdk-xml` to the dev group.
  A clean 3.13 environment no longer depends on packages left in an older venv.
- Added `test_phase26_python_policy.py` to freeze distribution/toolchain policy and generated
  deferred annotations.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Initial 3.13 source lane | focused runtime/mypy/Ruff on CPython 3.13.12 | EXPECTED RED: six 3.14-only `except A, B` syntax sites identified; mypy and Ruff reported the same source locations. |
| Second 3.13 generated lane | focused phase-25/executor/OpenAPI/AsyncAPI tests plus mypy/Ruff | Initial: six failures — two exposed missing `basedpyright`; four generated imports exposed eager annotations. After fixes, only three intentionally stale deterministic hashes remained; their refreshed value passed. |
| Clean full 3.13 suite | `uv run --python 3.13 pytest -q` | Initial collection exposed missing `eazy-sdk-xml` dev dependency; after declaring it, PASS: 846 passed, 11 skipped in 159.97s. |
| 3.13 static gates | `uv run --python 3.13 mypy`; `uv run --python 3.13 ruff check` | PASS: no issues in 266 source files; all lint checks passed. |
| 3.13 docs | strict Sphinx build and freshness under `uv run --python 3.13 --group docs` | PASS: 73-source build without warnings; 59 pages fresh. |
| 3.13 package/isolation | build/audit all six distributions into `phase26-rh01-py313-dist`; install together into fresh CPython 3.13.12 venv | PASS: all imports; every installed distribution reports `Requires-Python >=3.13`. |
| 3.14 generated regression | phase-26 baseline plus OpenAPI generator/real-world/projection and AsyncAPI suites under CPython 3.14.3 | PASS: 65 passed. |
| 3.14 package/isolation | build/audit all six distributions into `phase26-rh01-py314-dist`; install together into fresh CPython 3.14.3 venv | PASS: all imports and the same `Requires-Python >=3.13` metadata. |
| Python policy regression | phase-26 policy/baseline tests plus focused mypy/Ruff on 3.13 | PASS: 3 tests, two typed files and lint. |
| 3.12 feasibility | build all distributions on 3.12, then force-install wheels with pip `--ignore-requires-python` into CPython 3.12.13 | BUILD PASS; IMPORT UNSUPPORTED: Python 3.12 rejects the 3.13 type-parameter default syntax in `AuthContext[TSdk = Any]`. No support claim or runtime fork was added. |

### Remaining work

RH-02 extracts typed HTTP stages while retaining the frozen transition trace, fresh attempts and
single coordinator loop. Python 3.13 and 3.14 are the supported release matrix.

## Phase 26 RH-02 HTTP executor stages (2026-09-01)

### Delivered

- Added private typed `RequestDocumentStageInput/Output`; projection, mandatory private writers
  and managed body assembly now produce one fresh semantic document through this stage on every
  attempt. Removed the former executor-local implementations.
- Added effect-free typed response decisions for middleware retry/redirect, response retry,
  reaction, auth refresh, HTTP redirect, success/raw terminal and rejected outcomes. The stage
  returns a transition to `ExecutionCore`; it never retries, refreshes, sleeps or emits itself.
- Kept one `ExecutionCore._attempts` coordinator, one logical values state and fresh request/signing
  construction per attempt. No stage type is exported from a public namespace.
- Added direct precedence, replay-safety, redirect-budget and terminal stage tests while retaining
  the frozen sync/async transition/fingerprint characterization.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Replay/auth/protection/projection/capture focus | phase-26 baseline plus phase-08/14/17/21/23/24, projection wire capture and session-auth suites | PASS: 130 passed. |
| Stage behavior | `uv run --python 3.13 pytest -q tests/unit/test_phase26_http_stages.py tests/unit/test_phase26_runtime_baseline.py` | PASS: 4 passed. |
| Focused typing/lint | mypy/Ruff over `_http_stages.py`, executor and stage tests | PASS: no issues in 3 typed files; lint clean. |
| Full previous-phase runtime gate | `uv run --python 3.13 pytest -q` | PASS: 851 passed, 11 skipped in 189.97s. |
| Full static gates | `uv run --python 3.13 mypy`; `uv run --python 3.13 ruff check` | PASS: no issues in 269 source files; all lint checks passed. |

### Remaining work

RH-03 extracts compiler passes while preserving the exact `CompiledContract` result and frozen
fingerprints. HTTP stage records remain private and the coordinator remains singular.

## Phase 26 RH-03 compiler passes (2026-09-01)

### Delivered

- Split `compile_endpoint` into explicit typed input/layout, projection/private-writer,
  crypto/signing-graph, response/capability and fingerprint passes.
- Each pass returns one frozen record plus a safe `CompilerPassDiagnostic` containing only names,
  counts and structural details. Diagnostics are stored on the private `CompiledContract`; values,
  credentials and wire bodies are never included.
- The orchestrator still invokes `compile_plan` once and returns one immutable `CompiledContract`.
  Input layout is copied before private writer reservation, so later passes do not mutate an
  earlier pass result.
- Added pass-order/count/graph tests for simple and projected declarations. The exact phase-26
  fingerprint remains `3e954f…fb46d`; generated OpenAPI/AsyncAPI output stays deterministic.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Pass/fingerprint/projection focus | phase-26 compiler/baseline plus phase-01/21/24 suites | PASS: 46 tests; focused pass suite separately PASS: 16 tests. |
| Generated/replay/compiler aggregate | phase-01/08/17/21/24 plus OpenAPI projection/generator/real-world and AsyncAPI generator suites | PASS: 131 passed. |
| Focused typing/lint | mypy/Ruff over compiler and pass tests | PASS: no issues; lint clean. |
| Full previous-phase runtime gate | `uv run --python 3.13 pytest -q` | PASS: 853 passed, 11 skipped in 161.41s. |
| Full static gates | `uv run --python 3.13 mypy`; `uv run --python 3.13 ruff check` | PASS: no issues in 270 source files; all lint checks passed. |

### Remaining work

RH-04 separates WebSocket connection, writer preparation, reader routing and subscription recovery
ownership while preserving its independent state machine, ordered queue and generation semantics.

## Phase 26 RH-04 WebSocket stages (2026-09-01)

### Delivered

- Added private typed lifecycle decisions for connect/reconnect admission, failure transitions and
  writer-queue admission. `AsyncWsClient` remains the only owner of state mutation, task creation,
  reconnect timing and the ordered queue.
- Extracted immutable outbound preparation into one stage result containing the Zapros frame and
  redacted protection snapshot. Normal sends, protocol messages and restored subscriptions now
  share that preparation path; the sole writer loop is unchanged.
- Extracted reader-routing decisions with the existing priority for terminal errors, pending
  replies, correlation/channel subscriptions, protocol controls and user messages. Only the
  coordinator mutates pending/subscription registries or performs protocol I/O.
- Extracted disconnect and recovery decisions without importing HTTP execution concepts or
  changing protocol plugin contracts. Reconnect delays and subscription recovery tokens remain
  owned by the WebSocket runtime/subscription objects.
- Added focused transition, route-priority, recovery and immutable-write tests.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| GraphQL-WS/AsyncAPI/crypto/recovery focus | `uv run --python 3.13 pytest -q tests/unit/test_phase26_websocket_stages.py tests/websocket plugins/asyncapi/tests/test_asyncapi_generator.py tests/crypto/test_websocket_crypto.py tests/crypto/test_pc06_aad_metadata.py` | PASS: 102 passed, 8 skipped in 3.97s. |
| Focused typing/lint | mypy/Ruff over `_runtime_stages.py`, `runtime.py` and stage tests | PASS: no issues in 3 source files; lint clean. |
| Full previous-phase runtime gate | `uv run --python 3.13 pytest -q` | PASS: 857 passed, 11 skipped in 159.19s. |
| Full static gates | `uv run --python 3.13 mypy`; `uv run --python 3.13 ruff check` | PASS: no issues in 272 source files; all lint checks passed. |

### Remaining work

RH-05 runs the supported-Python, coverage, mutation, docs, build, package, isolation and
single-path absence gates and records the stable-release readiness decision.

## Phase 26 RH-05 final release gate (2026-09-01)

### Delivered

- Verified the complete supported runtime matrix on clean CPython 3.13.12 and 3.14.3
  environments. A matrix-only typing-test defect was fixed by passing the active
  `sys.executable` to basedpyright; it can no longer inspect an unrelated root `.venv`.
- Extended the absence audit to require exactly one `ExecutionCore` definition and exactly one
  `compile_endpoint` entry, in addition to the existing sole-emit and removed-architecture gates.
- Built and audited the final core plus five first-party plugin wheel/sdist pairs, then installed
  the exact wheels together on both supported Python versions and imported them under `-I`.
- Re-ran public API, generated OpenAPI/AsyncAPI strict typing/import, transition, capture and
  fingerprint coverage as part of the full matrix. No intended wire or public API change was
  introduced by RH-02–RH-04.
- Stable-release readiness decision: implementation and release-quality gates are complete. The
  repository metadata deliberately remains the current `0.1.0a1` Alpha version; publication or a
  version bump was not part of the implementation authorization.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Python 3.13 exact core | clean `UV_PROJECT_ENVIRONMENT`; `uv run pytest -q` | PASS: 857 passed, 11 skipped in 160.61s. Skips are opt-in live WebSocket, external PostgreSQL and local real-HTML fixture lanes. |
| Python 3.14 full matrix | clean `UV_PROJECT_ENVIRONMENT`; `uv run --python 3.14 pytest -q` | PASS: 857 passed, 11 skipped in 161.89s. Initial run exposed and then verified the basedpyright interpreter-selection fix. |
| Full strict typing/lint | `uv run --python 3.13 mypy`; `uv run --python 3.13 ruff check` | PASS: no issues in 272 source files; all lint checks passed. |
| Branch coverage | `uv run --python 3.13 pytest -q --cov --cov-branch --cov-report=term-missing:skip-covered` | PASS: 857 passed, 11 skipped; total 84.46%, above the configured 80% gate. |
| Mutation | `uv run --python 3.13 mutmut run --help` | BLOCKED BY ENVIRONMENT: mutmut refuses native Windows execution and requires WSL; no mutation result is reported as passing. |
| Lock and source hygiene | `uv lock --check`; `git diff --check` | PASS: 159-package lock resolves; no whitespace errors. The existing API-fingerprint line-ending warning is non-fatal. |
| Single-path absence | `uv run --python 3.13 python scripts/absence_audit.py`; phase-10 absence test | PASS: one executor core, one compiler entry, one emit owner and no legacy/public duplicate path; 1 test passed. |
| Documentation | freshness check; strict `sphinx-build -W --keep-going -b dirhtml` | PASS: 59 pages fresh; 73 sources rendered without warnings. `docs-site/package.json` is absent, so obsolete npm gates are not runnable. |
| Package build/audit | six `uv build` runs into `.test-tmp/phase26-final-build`; `scripts/package_audit.py` | PASS: six wheel/sdist pairs have correct metadata, licenses, typing markers, Zapros boundary and no legacy paths. |
| Supported-version isolation | install all six final wheels into fresh CPython 3.13.12 and 3.14.3 venvs; `python -I` imports and metadata assertions | PASS: all six distributions import on both versions and report `Requires-Python >=3.13`. |

### Remaining work and blockers

None for the implementation plan. Native Windows cannot execute mutmut; the phase explicitly
requires mutation only where the environment supports the runner. All other phase-26 exit criteria
and the master Definition of Done have evidence.

### Planning evidence

- Reproduced the omitted `NotRequired` `BodyProjection` failure on commit `036c785`:
  `BindingError: slot has no value: body-projection.source.page` before handler execution.
- Verified that the existing phase-21 focused suite does not cover the defect: 24 tests pass while
  the standalone reproduction fails.
- Confirmed that current protection config uses `tuple[object, ...]` and runtime `getattr`
  discovery, reaction application writes one target, reaction freshness is not persisted, and the
  Cloudflare preset selects only `primary_cookie` from a multi-cookie result.
- Confirmed that direct parameters/default binding already exists below the canonical
  `Unpack[TypedDict]` policy, while projection compilation explicitly rejects a non-`Unpack`
  operation.
- Confirmed that replacing declaration bodies with `...` is not an accepted fix: strict mypy
  reports `Missing return statement [empty-body]` for an ordinary typed method.
- Split remediation into four ordered phases so a correctness fix, security lifecycle redesign,
  public authoring changes and internal refactor never share one unverified increment.

### Progress preservation contract

After every increment:

1. update the phase row above to `active`, `blocked` or `complete` only from recorded evidence;
2. append a dated section containing delivered behavior, exact commands/results and remaining
   work;
3. run the increment's focused tests before starting its dependent increment;
4. preserve all prior evidence, including initial red results and documented environment blockers;
5. do not mark a phase complete until every checkbox in its phase document has evidence;
6. resume from the earliest incomplete phase and earliest unchecked increment.

### Planned phase order

| Phase | First checkpoint | Completion boundary |
|---|---|---|
| 23 | PC-00 red projection regression | optional/`None`/required semantics, diagnostics, strict docs and release gates |
| 24 | PP-00 typed-policy characterization | typed config, compound state, persistence/single-flight, presets and SPI docs |
| 25 | AE-00 typing/API proofs | two request styles, response/parser/testing/root/method ergonomics and release gates |
| 26 | RH-00 compatibility/behavior baseline | verified Python floor, staged internals and full production release gate |

### Commands run for planning

| Command | Result |
|---|---|
| `git status --short` | PASS: clean before documentation edits. |
| `uv run pytest -q tests/unit/test_phase21_body_projection.py tests/unit/test_phase21_body_projection_runtime.py` | PASS: 24 passed; demonstrates the missing regression coverage. |
| inline `uv run python -` KAD-shaped optional-projection reproduction | EXPECTED FAIL: caller-visible `BindingError` before network. |
| `uv run mypy --config-file NUL --strict -c "def f() -> int: ..."` | EXPECTED FAIL: `empty-body`; `NUL` also reports the expected missing-config-section warning. |

Runtime pytest, full mypy, Ruff, docs build and package gates are not claimed for this planning
increment. Documentation-only validation is recorded after the phase files and master links are
checked.

### Planning-document verification

| Gate | Command | Result |
|---|---|---|
| API documentation freshness | `uv run python scripts/docs_freshness.py check` | PASS: 58 public pages fresh. |
| Public documentation metadata | `uv run python docs-site/scripts/validate_docs.py` | PASS: 72 pages valid. |
| Implementation links | resolve relative Markdown links in `README.md` and phases 23–26 | PASS: all referenced `.md` files exist. |
| Markdown structure | code-fence parity across `README.md` and phases 23–26 | PASS. |
| New phase text integrity | trailing whitespace, final newline and style scan for phases 23–26 | PASS. |
| Master/status linkage | count phase 23–26 links, status rows and named checkpoints | PASS: both master tables link all four phases; 24 checkpoints present. |

The repository intentionally ignores `/docs/` through `.gitignore`, so these authoritative local
implementation files do not appear in `git status` or `git diff --check`. The files themselves were
validated directly. Public `docs-site/` remains tracked and unchanged. Full Sphinx render was not
rerun because this planning increment changes no public documentation source.

## Phase 22 completion: local rename to eazy-sdk (2026-08-27)

The unpublished workspace now has one identity: distribution `eazy-sdk`, import `eazy_sdk` and
display name `Eazy SDK`. OpenAPI, AsyncAPI, presets, SQLModel and XML use the corresponding
`eazy-sdk-*` distributions and `eazy_sdk_*` imports. The OpenAPI/AsyncAPI extension prefix is
`x-eazy-sdk-`, environment variables use `EAZY_SDK_`, and no compatibility package or CLI alias
was added. The outer repository directory was intentionally left in place because it is not part
of package identity.

| Gate | Command | Result |
|---|---|---|
| Workspace imports and old-import rejection | `uv --cache-dir .uv-cache run python -c ...` | PASS: core and all five plugin imports succeed; the former import has no module spec. |
| Focused plugin suites | aggregate plugin run plus projection-codegen rerun | Initial aggregate: 3 expected renamed-source hash failures, 115 passed, 1 skipped. After updating the deterministic hash, the affected 18 tests passed; the final full suite below covers the complete plugin set. |
| Full test suite | `uv --cache-dir .uv-cache run pytest -q --basetemp=.test-tmp/eazy-sdk-full-20260827` | PASS: 805 passed, 11 skipped in 177.73s. Skips are the opt-in PostgreSQL matrix (`EAZY_SDK_POSTGRES_DSN` unset), public-exchange WebSocket tests (`EAZY_SDK_RUN_LIVE_WS` is not `1`) and unavailable real-HTML fixtures under `tests/test_html/`. |
| Static typing | `uv --cache-dir .uv-cache run mypy` | PASS: no issues in 258 source files. |
| Lint | `uv --cache-dir .uv-cache run ruff check` | PASS. |
| Former-identity and legacy absence | `uv --cache-dir .uv-cache run python scripts/absence_audit.py`; case-insensitive content/path scan | PASS: no former identity in repository source paths/content and no compatibility or legacy execution surface. |
| Documentation | freshness check; metadata validator; strict `sphinx-build -W --keep-going -b dirhtml` | PASS: 58 API-backed pages fresh, 72 pages valid and 72 pages built without warnings. |
| Package build and audit | `uv --cache-dir .uv-cache build --all-packages --out-dir .test-tmp/eazy-sdk-dist-20260827`; package audit | PASS: six wheel/sdist pairs have target metadata, licenses, typing markers and no forbidden paths. |
| Isolated install | fresh Python 3.14 venv; install all six built wheels; `python -I` imports and three CLI help smokes | PASS: six imports and three renamed CLI entry points work; former import is absent. |
| Active local environment | remove six stale editable metadata records, install renamed XML workspace package, inspect `uv pip list --format json` | PASS: six `eazy-sdk*` distributions and zero former-name distributions. |
| Dependency lock | `uv --cache-dir .uv-cache lock --check` | PASS: 156 packages resolved. |
| Patch integrity | `git diff --check` | PASS; only existing line-ending conversion warnings. |

## Documentation platform migration: Sphinx and Shibuya (2026-08-27)

Phase 10 remains complete. The public site now uses Sphinx with the Shibuya theme instead of
Astro/Starlight. The existing 72-page Russian documentation corpus stays in MyST-compatible MDX,
so API source metadata and fingerprints remain the release contract while Sphinx owns navigation,
cross-reference validation, search and production rendering.

Delivered:

- replaced the Astro/Starlight configuration, Node dependency tree and MDX UI components with
  Sphinx 9, Shibuya, MyST Parser, sphinx-design and sphinx-copybutton;
- converted cards, tabs, admonitions and request/response panels to native MyST directives while
  preserving every page route and all documented examples;
- added the complete captioned toctree, Pygments language highlighting, copy buttons, Russian
  search, responsive HTTP panels and the Shibuya light/dark color-mode control;
- added `.readthedocs.yaml`, a bounded documentation requirements file and a strict `dirhtml`
  Read the Docs build with warnings treated as errors;
- replaced the Node frontmatter gate with a Python metadata/content validator and changed CI,
  updating instructions and implementation-plan commands to the Sphinx toolchain;
- retained `llms.txt` and `llms-full.txt` generation through `sphinx-llms-txt`.

| Gate | Command | Result |
|---|---|---|
| Documentation metadata | `python docs-site/scripts/validate_docs.py` | PASS: 72 pages have valid frontmatter and substantive published content. |
| Documentation freshness | `uv run python scripts/docs_freshness.py check` | PASS: 58 API-backed pages fresh. |
| Strict production render | `uv run --frozen --group docs sphinx-build -W --keep-going -b dirhtml -c docs-site docs-site/src/content/docs docs-site/_build/html` | PASS: 72 pages built without warnings; Russian search plus `llms.txt` and 6,170-line `llms-full.txt` generated. |
| Browser QA | local `dirhtml` site opened with Playwright/Chrome at `/`, `/guides/requests/query/` and `/auth/login/` | PASS: Shibuya navigation, cards, admonitions, Python/JSON Pygments highlighting and paired HTTP panels render correctly. Temporary screenshots and browser/server state were removed after inspection. |
| Configured static typing | `uv run mypy` | PASS: no issues in 258 source files. |
| Configured lint | `uv run ruff check` | PASS. The first run requested import-block normalization in the two new Python docs files; Ruff fixed them and the full rerun passed. |
| Full test suite | `uv run pytest -q --basetemp=.test-tmp/docs-sphinx-final-20260827` | PASS: 805 passed, 11 skipped in 164.90s. The configured `.test-tmp/pytest` path is unreadable in this Windows sandbox and caused 36 setup errors during two attempted configured runs; the isolated basetemp rerun changed no test selection. |
| Dependency lock | `uv lock --check` | PASS: 156 packages resolved. |

## Phase 21 follow-up: runnable Adaptix projection example (2026-08-26)

Phase 21 remains complete. A separate runnable authoring example uses one generated Adaptix
converter in a `BodyProjection` that emits a multi-level dataclass JSON document. Private wire
metadata supplies locale/encoding/version/platform defaults and a replaceable timezone-aware
`datetime` factory; the transport callback verifies the exact nested payload, default metadata and
ISO 8601 time value actually serialized by `JsonBody`. The examples index and regression suite cover
the scenario without adding Adaptix to the core package dependencies.

| Gate | Command | Result |
|---|---|---|
| Runnable example | `uv run python examples/adaptix_nested_wire_body.py` | PASS: `adaptix registered: john as user-42`. |
| Example regression | `uv run pytest -q tests/unit/test_docs_examples.py` | PASS: 12 passed, including deterministic injected-time and subprocess wire serialization checks. |
| Focused typing/lint | focused `uv run mypy` and `uv run ruff check` over the example and regression test | PASS: no typing or lint issues. |
| Configured full pytest | `uv run pytest -q` | PASS: 805 passed, 11 skipped in 150.49s. Skips remain the opt-in PostgreSQL matrix (`EAZY_SDK_POSTGRES_DSN` unset), public exchange WebSocket tests (`EAZY_SDK_RUN_LIVE_WS` not `1`) and absent real-HTML fixtures (`tests/test_html/*.html`). |
| Configured static typing | `uv run mypy` | PASS: no issues in 258 source files. |
| Configured lint | `uv run ruff check` | PASS. |
| Documentation/absence/hygiene | `uv run python scripts/docs_freshness.py check`; `uv run python scripts/absence_audit.py`; `git diff --check` | PASS: 58 pages fresh, removed-path audit clean and no whitespace errors; only the two existing CRLF normalization warnings remain. |

The first focused subprocess run exposed that an optional `TypedDict` field is still an acquired
projection slot. The example was corrected to keep locale in private wire defaults and to use
Adaptix `link_constant(..., factory=...)`, then every focused and configured gate above was rerun
successfully. No blocker or runtime compatibility path was introduced.

On 2026-08-27, `make_register_converter` was simplified from four cascading generated converters
to one generated root converter with explicit payload and metadata factories. The emitted wire
model, injected-time behavior and public `BodyProjection` contract remain unchanged.

| Simplification gate | Command | Result |
|---|---|---|
| Runnable example | `uv run python examples/adaptix_nested_wire_body.py` | PASS: `adaptix registered: john as user-42`. |
| Example regression | `uv run pytest -q tests/unit/test_docs_examples.py` | PASS: 12 passed in 5.95s. |
| Focused typing/lint | focused `uv run mypy` and `uv run ruff check` over the example and regression test | PASS: no typing or lint issues. |
| Configured full pytest | `uv run pytest -q` | PASS: 805 passed, 11 skipped in 146.63s. |
| Configured static typing | `uv run mypy` | PASS: no issues in 258 source files. |
| Configured lint | `uv run ruff check` | PASS. |
| Documentation freshness | `uv run python scripts/docs_freshness.py check` | PASS: 58 pages fresh. |

## Phase 21 BP-06: examples, documentation and final release gates (2026-08-26)

Phase 21 is complete. Every exit criterion in the phase plan has implementation and verification
evidence.

Delivered:

- replaced the root-`BodyCodec` workaround example with one decorated `register` method using
  four expanded `Unpack[RegisterUser]` kwargs and a named `BodyProjection` into the private nested
  wire schema;
- changed the example from a mock transport callback to an actual temporary `127.0.0.1` HTTP
  server capture, including constants and a fresh timestamp factory;
- documented plain-function and optional Adaptix mapper variants, the OpenAPI projection extension,
  typed generation config/CLI binding and the public/private ownership boundary across README,
  SDK references, docs-site, package README and the historical serialization note;
- extended the absence audit to reject the removed example workaround and any `wire_body=` in
  generated OpenAPI snapshots, while the built-artifact audit keeps rejecting the removed runtime
  path;
- updated API fingerprints and master/phase status, with Adaptix and `ty` remaining development-only
  and the core wheel retaining Zapros as its sole mandatory dependency.

| Gate | Command | Result |
|---|---|---|
| Phase-21 final focus | cache-disabled pytest over projection compiler/runtime/proof, crypto/signing, localhost capture, runnable docs example, OpenAPI codegen and API fingerprint suites | PASS: 104 passed in 48.95s, including the real Pyright fixture and OpenAPI 3.0/3.1/3.2 generation/type/execution matrix. |
| Runnable localhost example | `uv run python examples/flat_model_wire_body.py`; docs example suite | PASS: `registered: john as user-42`; 10 tests passed in 4.30s. |
| Configured full pytest | `uv run pytest -q` | PASS: 803 passed, 11 skipped in 155.06s. Skips are the opt-in PostgreSQL matrix (`EAZY_SDK_POSTGRES_DSN` unset), public exchange WebSocket tests (`EAZY_SDK_RUN_LIVE_WS` not `1`) and uncommitted real-HTML fixtures (`tests/test_html/*.html` absent). The preceding run found one new README block without its local `api` import (802 passed); the block was corrected, its 4-test contract passed, and the configured suite was rerun to completion. |
| Configured static typing | `uv run mypy` | PASS: no issues in 257 source files. |
| Configured lint | `uv run ruff check` | PASS. |
| Documentation freshness/site | `uv run python scripts/docs_freshness.py check`; from `docs-site/`, `npm run check`; `npm run build` | PASS: 58 pages fresh, content/frontmatter valid and 73 pages built/indexed. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Source/snapshot absence | `uv run python scripts/absence_audit.py` | PASS: removed modules/APIs, `wire_body=`, generated snapshot occurrences and the root-codec example workaround are absent. |
| Packages and artifact audit | `uv build --all-packages --out-dir .phase21-final-build-20260826`; `uv run python scripts/package_audit.py .phase21-final-build-20260826` | PASS: six wheel/sdist pairs have valid metadata/licenses/typing markers, the Zapros-only mandatory core boundary and no removed phase-21 runtime path. |
| Isolated built-wheel import | fresh `.phase21-final-venv-20260826`, reinstall current core/OpenAPI wheels, then `python -I` parse/render smoke | PASS: built `BodyProjection`, `BodyProjectionIR`, `GenerationConfig`, static import rendering and OpenAPI 3.2 lowering import and execute outside the source tree. |
| Lock and diff hygiene | `uv lock --check`; `git diff --check` | PASS: 137 packages resolved and no whitespace errors; only existing CRLF normalization warnings remain. |

## Phase 21 BP-05: OpenAPI projection IR and generated SDK integration (2026-08-26)

BP-05 is complete. BP-06 is the next dependency.

Delivered:

- added the canonical operation-level `x-eazy-sdk.projection` contract with required public
  source schema/reference, exact request-body target schema/reference, named application
  requirement and encoding plus an optional stable name;
- added frozen `BodyProjectionIR` lowering with source/target fields and strict JSON Pointer
  diagnostics for missing, unknown, mismatched and unsupported extension values;
- added typed `GenerationConfig`/`ProjectionImport` resolution and a repeatable CLI
  `--projection REQUIREMENT=MODULE:ATTRIBUTE` boundary; generated execution uses only validated
  static imports, never lambdas, `eval` or dynamic import lookup;
- generated a caller-facing source `TypedDict`, private target Pydantic wire schema, inherited
  operation request `TypedDict` and named `BodyProjection` constant while preserving the normal
  wire-shaped root-body generator path when the extension is absent;
- added a deterministic generated-source digest, strict positive and negative consumer typing,
  static-import absence checks and exact HTTP execution fixtures for OpenAPI 3.0, 3.1 and 3.2.

| Gate | Command | Result |
|---|---|---|
| Dialect generation/type/execution matrix | cache-disabled pytest over `plugins/openapi/tests/test_phase21_body_projection_codegen.py` | PASS: 18 passed in 48.15s; OpenAPI 3.0.3, 3.1.1 and 3.2.0 each generate, pass strict mypy/import and emit the exact projected localhost JSON body. |
| Complete OpenAPI plugin suite | cache-disabled pytest over `plugins/openapi/tests` | PASS: 54 passed in 58.47s before the final CLI parser and deterministic-digest assertions; the final dedicated BP-05 suite above passes those additions. |
| Focused static typing | `uv run mypy plugins/openapi/eazy_sdk_openapi plugins/openapi/tests/test_phase21_body_projection_codegen.py` | PASS: no issues in 6 source files. |
| Focused lint | `uv run ruff check plugins/openapi/eazy_sdk_openapi plugins/openapi/tests/test_phase21_body_projection_codegen.py` | PASS. |

## Phase 21 BP-04: crypto, signing and exact codec integration (2026-08-26)

BP-04 is complete. BP-05 is the next dependency.

Delivered:

- bound outbound document-crypto selector compatibility to `BodyProjection.target`, not the
  caller-visible source or a missing root-body annotation, while preserving target alias
  resolution through the phase-20 typed path compiler;
- compiled the signature plan and `SIGN` stage before attempt-side providers, added target-aware
  nested body signature paths and included private/signature writer identities in the operation
  fingerprint;
- validated body-reserved output paths against the target schema and reject protection/signature,
  signature/signature, signature/field-crypto and signature/encoded-crypto conflicts before key
  resolution or network I/O;
- implemented proper escaped nested JSON Pointer insertion for body signature outputs and reject
  projection output that pre-populates a reserved signature path;
- kept one target semantic document through standard/custom encoding, verified custom gzip JSON
  bytes precede encoded crypto, and verified exact HMAC signing reads the final emitted ciphertext;
- proved fresh projection, document crypto and signatures together on response retry, managed
  redirect and 401 auth-refresh replay;
- added a real localhost first-hop capture whose exact projected/encrypted JSON bytes and signature
  match the server expectation.

| Gate | Command | Result |
|---|---|---|
| BP-04 crypto/signing matrix | cache-disabled pytest over new BP-04 unit/capture tests, all crypto tests, phase 04/08/17 and phase-21 runtime | PASS: 95 passed in 2.95s before the final target-binding/path diagnostics additions. |
| New projection crypto/signing suite | `uv run pytest -q tests/unit/test_phase21_projection_crypto_signing.py` | PASS: 11 passed; target selectors, custom compression, encoded crypto, exact/body outputs, collisions and retry/redirect/auth freshness are covered. |
| Localhost first-hop capture | `uv run pytest -q tests/integration/test_phase21_projection_wire_capture.py` | PASS: 1 passed; emitted bytes and exact signature match the socket server expectation. |
| Configured full pytest | `uv run pytest -q` | PASS: 785 passed, 11 skipped in 107.10s. |
| Static typing and lint | `uv run mypy`; `uv run ruff check` | PASS: no issues in 256 source files; lint clean. |
| Documentation/lock/diff | `uv run python scripts/docs_freshness.py check`; `uv lock --check`; `git diff --check` | PASS: 58 pages fresh, 137 packages resolved and no whitespace errors; only existing CRLF normalization warnings remain. |

## Phase 21 BP-03: private wire writers and legacy removal (2026-08-26)

BP-03 is complete. BP-04 is the next dependency.

Delivered:

- compiled `FromProtection` recursively from the projection target into exact serialized and
  validation paths, including nested Pydantic aliases, and added the ordered `PRIVATE_WIRE` plan
  stage between projection and document crypto;
- moved mandatory protection body injection out of root-body replacement: acquire/solve/verify is
  resolved once per logical call, while atomic reserved-path injection and final target validation
  run freshly on every initial/retry attempt;
- reject overlapping private writers before providers/network and reject mapper output that
  pre-populates a reserved target path before the main request is sent;
- removed the decorator argument, operation declaration field and executor branch for
  `wire_body=` with no alias, shim or second runtime path;
- migrated the phase-17 mandatory-protection APIs and the OpenAPI protection generator to named
  `BodyProjection` declarations with flattened source `TypedDict` kwargs;
- extended source and built-package absence audits for the removed path and moved the `ty` checker
  from mandatory core dependencies into the dev group, preserving the Zapros-only package
  boundary required by the release contract;
- updated protection/request/API-method documentation for the projection and recursive writer
  pipeline.

| Gate | Command | Result |
|---|---|---|
| BP-03 regression matrix | cache-disabled pytest over phase 17, phase 08, phase-21 declaration/proof/runtime, request serialization, phase-10 absence and all OpenAPI tests | PASS: 121 passed in 15.78s. |
| Mandatory protection focus | `uv run pytest -q tests/rewrite/test_phase17_declarative_api.py` | PASS: 11 passed; nested aliases, response retry, overlap and reserved-path collision are covered. |
| Configured full pytest | `uv run pytest -q` | PASS: 773 passed, 11 skipped in 106.06s. |
| Static typing and lint | `uv run mypy`; `uv run ruff check` | PASS: no issues in 254 source files; lint clean. |
| Source absence | `uv run python scripts/absence_audit.py` | PASS: decorator/declaration/executor/generator legacy paths and public docs are absent. |
| Packages and wheel absence | `uv build --all-packages --out-dir .phase21-bp03-build`; `uv run python scripts/package_audit.py .phase21-bp03-build` | PASS: six wheel/sdist pairs pass metadata, dependency, typing and legacy-source checks. The first audit correctly rejected `ty` as a mandatory core dependency; after moving it to dev and relocking, the final rebuild/audit passed. |
| Documentation | `uv run python scripts/docs_freshness.py check`; from `docs-site/`, `npm run check`; `npm run build` | PASS: 58 pages fresh; content/frontmatter valid; 73 pages built/indexed. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Lock and diff hygiene | `uv lock --check`; `git diff --check` | PASS: 137 packages resolved; no whitespace errors (only existing CRLF normalization warnings). |

## Phase 21 BP-02: per-attempt projection and target validation (2026-08-26)

BP-02 is complete. BP-03 is the next dependency.

Delivered:

- added the typed `BODY_PROJECTION` plan stage between attempt contributions and document crypto;
- the executor assembles a fresh source `TypedDict` after dependency/auth/middleware patches and
  invokes the structural mapper exactly once on every initial, retry and managed-redirect attempt;
- caller-owned nested collections are deep-copied before mapper invocation, so even a misbehaving
  mapper cannot mutate the bound caller object;
- mapper output is loaded through the configured target adapter and dumped once in JSON or Python
  mode before body encoding; TypedDict, dataclass, Pydantic and msgspec targets preserve nested
  structure, declaration order, aliases and defaults;
- standard JSON/form/multipart and custom `BodyCodec` consume the validated semantic document;
  `EncodeContext.models` was removed and first-party XML/example codecs were migrated to the
  primitives-only boundary;
- outbound document crypto now accepts the already-projected semantic document without a second
  model dump;
- `BodyProjectionError` reports operation/projection identity and safe nested validation paths,
  suppresses mapper/validator exception chains and does not expose input values.

| Gate | Command | Result |
|---|---|---|
| BP-02 runtime matrix | cache-disabled pytest over phase-21 runtime/declaration/proof, request/model/codec/docs, HTTP crypto, phase 08/14 and XML suites | PASS: 180 passed before the final same-document/dump-once additions; the final dedicated runtime matrix passes 11 tests. |
| Configured full pytest | `uv run pytest -q` | PASS: 770 passed, 11 skipped in 105.97s. |
| Configured static typing | `uv run mypy` | PASS: no issues in 254 source files. |
| Configured lint | `uv run ruff check` | PASS. |
| Documentation freshness | update the two genuinely revised request/quickstart pages, then `uv run python scripts/docs_freshness.py check` | PASS: 58 pages fresh. |
| Documentation site | from `docs-site/`, `npm run check`; `npm run build` | PASS: content/frontmatter valid and 73 pages built/indexed. Existing Astro markdown deprecation and missing `docs -> 404` notices remain non-fatal. |

BP-02 intentionally excluded work owned by later increments. Mandatory-protection/private target
writers are now complete in BP-03; crypto/signing writer integration is BP-04, generated projection
IR is BP-05 and the public example remains the root-codec characterization until BP-06.

## Phase 21 BP-01: declarations and input compilation (2026-08-26)

At the BP-01 checkpoint, phase 20 and BP-01 were complete and BP-02 was the next dependency.

Delivered:

- added the only public constructor, frozen generic
  `BodyProjection(source, target, using, encoding, name=None)`, under `eazy_sdk.request`;
- added immutable `MethodInputSchema` retaining the exact unpacked `TypedDict` identity alongside
  flattened fields;
- extended HTTP decorators with `body=BodyProjection(...)` and compiled exact source keys as
  identity-based logical slots with no flat wire name and no `BodyLayout` membership;
- validated TypedDict source identity, structural subset, annotation/requiredness equivalence,
  unplaced fields outside the source, source placement conflicts, projection/root/flat body
  conflicts, old `wire_body` coexistence and supported first-party target adapters;
- included projection name, source, target, mapper identity, encoding and adapter fingerprints in
  the HTTP plan fingerprint;
- moved the strict mypy/basedpyright proof from the isolated candidate to the production export.

| Gate | Command | Result |
|---|---|---|
| BP-01 compiler/typing focus | cache-disabled pytest with repo-local basetemp over phase-21 declaration/proof, typed-input, request-serialization and model-adapter suites | PASS: 100 passed, including production mypy and basedpyright subprocess fixtures. |
| Focused static typing | `uv run mypy eazy_sdk tests/unit/test_phase21_body_projection.py tests/unit/test_phase21_body_projection_proof.py tests/unit/test_typed_request_inputs.py tests/unit/test_request_serialization.py` | PASS: no issues in 110 source files. |
| Focused lint | matching `uv run ruff check` paths | PASS. |
| Dependency lock | `uv lock --check` | PASS: 137 packages resolved. Adaptix remains development-only. |
| Diff hygiene | scoped `git diff --check` over the phase-20 gate cleanup and BP-01 tracked files | PASS. |

Remaining BP-01 phase-level work is intentionally deferred to its owning increments: no mapper is
invoked yet, no target document is encoded, and the root-codec example remains the characterized
workaround until BP-02/BP-06.

## Phase 20 final configured-gate closure (2026-08-26)

- Restored the root-codec example to its documented, executable pre-phase-21 form instead of
  excluding it from collection.
- Made test-only access to private operation descriptor compilation explicit with `cast(Any, ...)`;
  no runtime descriptor or compatibility surface was added.
- Disabled pytest's optional cache provider in checked-in addopts because the existing
  `.pytest_cache` directory is ACL-blocked on this workspace. Test discovery, execution and the
  repo-local basetemp remain configured normally.

| Gate | Command | Result |
|---|---|---|
| Focused blocker regression | cache-disabled pytest over docs examples, typed inputs, phase 08/18 and XML codec | PASS: 61 passed. |
| Configured full pytest | `uv run pytest -q` | PASS: 746 passed, 11 skipped in 105.43s. |
| Configured static typing | `uv run mypy` | PASS: no issues in 252 source files. |
| Configured lint | `uv run ruff check` | PASS. |

The first configured pytest attempt failed during collection because pytest could not write
`.pytest_cache/v/cache/nodeids` (`PermissionError [Errno 13]`). After disabling only the optional
cache provider, the exact configured command above passed. Phase 20 is complete.

## Phase 21 BP-00: characterization and typing proof (2026-08-26)

At BP-00 the phase remained `pending`: the proof was intentionally isolated until phase 20's full
configured gates closed. That prerequisite is now complete and BP-01 is implemented above.

| Increment | State | Evidence | Remaining work / blocker |
|---|---|---|---|
| BP-00 | complete | Current unplaced-`Unpack` failure and one-wrapper root-body workaround are permanent characterizations. A production-free generic dataclass candidate preserves exact source/wire callable typing for plain functions and a real Adaptix converter in strict mypy and basedpyright. Source-subset, sync/async expanded kwargs, negative callable typing, stable names and the TypedDict/dataclass/Pydantic/msgspec target matrix are covered. | None. |
| BP-01 | complete | The production export, decorator input, `MethodInputSchema`, projection-only logical slots, compile diagnostics, plan fingerprints and strict production typing fixtures pass. | None. |
| BP-02 | complete | Per-attempt projection, target adapter validation, one model dump/encoding, primitives-only `BodyCodec`, retry/redirect freshness, safe errors and the first-party target matrix pass. | None. |
| BP-03 | complete | Recursive target-path writers, per-attempt mandatory injection, atomic collision checks, OpenAPI protection migration and source/wheel absence gates pass. | None. |
| BP-04 | complete | Projection targets drive document crypto; private/signature/crypto writers share checked ordering, exact/body signing works, and retry/redirect/auth plus localhost captures pass. | None. |
| BP-05 | complete | Canonical extension validation, `BodyProjectionIR`, typed static generation imports, private target/public source emission and OpenAPI 3.0/3.1/3.2 generation/type/execution gates pass. | None. |
| BP-06 | complete | Localhost projection example, plain/Adaptix/OpenAPI documentation, fingerprints, absence, full core/docs/package and isolated-wheel gates pass. | None. |

Delivered BP-00 artifacts and decisions:

- selected one public construction style: the generic frozen dataclass constructor
  `BodyProjection(source, target, using, encoding, name=None)`; no parallel factory is proposed;
- added production-free proof support under `tests/_support` and a focused suite that invokes both
  type checkers on positive and negative generated fixtures;
- verified Adaptix `get_converter(Source, Target)` retains
  `Callable[[Source], Target]`; its recipe provider types are partially unknown to basedpyright, so
  the fixture disables only `reportUnknownVariableType` while retaining strict assertions on the
  converter and projection types;
- added Adaptix only to the development dependency group. Core project dependencies and runtime
  imports remain unchanged;
- did not edit `eazy_sdk`, `examples/flat_model_wire_body.py`, `wire_body=` or the executor.

| Gate | Command | Result |
|---|---|---|
| BP-00 proof | cache-disabled pytest over `tests/unit/test_phase21_body_projection_proof.py` with a repo-local basetemp | PASS: 11 passed, including real mypy and basedpyright subprocess checks. |
| Request/model regression focus | same pytest invocation plus `tests/unit/test_typed_request_inputs.py` and `tests/unit/test_model_adapters.py` | PASS: 63 passed. |
| Changed-source typing | `UV_CACHE_DIR=.uv-cache uv run mypy tests/_support/body_projection_proof.py tests/unit/test_phase21_body_projection_proof.py` | PASS: no issues in 2 source files. |
| Changed-source lint | `UV_CACHE_DIR=.uv-cache uv run ruff check tests/_support/body_projection_proof.py tests/unit/test_phase21_body_projection_proof.py` | PASS. |
| Dependency lock | `UV_CACHE_DIR=.uv-cache uv lock --check` | PASS: 137 packages resolve and the dev-only Adaptix declaration matches `uv.lock`. |
| Documentation freshness | `UV_CACHE_DIR=.uv-cache uv run python scripts/docs_freshness.py check` | PASS: 58 pages fresh. |
| Diff hygiene | scoped `git diff --check` over BP-00 tests, dependency files and implementation status/plan | PASS. |
| Full static typing diagnostic | `UV_CACHE_DIR=.uv-cache uv run mypy` | NOT PASSING: the 9 already recorded private descriptor `.resolve` errors remain, and the user-owned `examples/flat_model_wire_body.py` currently adds 3 errors from rebinding an `AsyncClient` variable to `RegistrationApi` and then calling operation attributes through the old type. No BP-00 file contributes an error. |
| Full lint diagnostic | `UV_CACHE_DIR=.uv-cache uv run ruff check` | NOT PASSING: the user-owned `examples/flat_model_wire_body.py` has one unused local `user` at line 158. No BP-00 file contributes an error. |

## Public request to wire body projection plan (2026-08-26)

- Added phase 21 as the authoritative breaking implementation plan for preserving flat
  `Unpack[TypedDict]` operation signatures while projecting their semantic body inputs into a
  separate private wire schema.
- The target pipeline performs projection once per attempt after public patches and before target
  validation, document crypto, body encoding and signing. Adaptix is an optional structural mapper,
  not a core dependency.
- The plan replaces the mandatory-protection-only `wire_body=` branch and the root-`BodyCodec`
  example workaround instead of adding a third serialization path.
- Phase 21 remains pending behind active phase 20 so both features share one typed path/writer and
  stage-order graph.
- Narrowed the repository docs ignore rule so authoritative `docs/implementation/**` phase files
  remain trackable while other local `/docs/*` content stays ignored.

No phase-21 runtime code or compatibility API has been added.

| Gate | Command | Result |
|---|---|---|
| Documentation API shape/import audit | cache-disabled pytest over `tests/unit/test_documentation_api_examples.py` with a repo-local basetemp | PASS: 4 passed. |
| Documentation freshness | `UV_CACHE_DIR=.uv-cache uv run python scripts/docs_freshness.py check` | PASS: 58 pages fresh. The first invocation without the repo-local uv cache was blocked by the host uv cache ACL and is not reported as passing. |
| Diff hygiene | scoped `git diff --check` over the phase-21 plan, master plan, status and SDK references | PASS. |
| Runnable documentation examples diagnostic | cache-disabled pytest over documentation API and runnable example suites | NOT PASSING: collection fails because the already edited `examples/flat_model_wire_body.py` declares plain unplaced `Unpack[RegisterUser]` fields before Phase 21 exists. BP-06 owns its migration after the runtime contract is implemented. |

## Flat public model to nested wire JSON example (2026-08-26)

- Added a complete local async SDK example whose caller supplies a flat `RegisterUser` `TypedDict`
  while a root `BodyCodec` emits private nested `account`, `profile` and `client` wire objects.
- `RegisterWireSettings` owns hidden constants and a configurable `timestamp_factory`; the default
  factory reads Unix time during each body encode rather than during SDK declaration.
- The mock server verifies the actual content type and nested JSON received at the handler boundary.
  A focused codec test substitutes a deterministic clock and asserts the complete wire document.

| Gate | Command | Result |
|---|---|---|
| Executable example | `uv --cache-dir .uv-cache run python examples/flat_model_wire_body.py` | PASS: local mock registration returned `registered: john as user-42`. |
| Documentation examples | cache-disabled pytest over `tests/unit/test_docs_examples.py` with a repo-local basetemp | PASS: 10 passed. |
| Public example shape audit | cache-disabled pytest over `tests/unit/test_documentation_api_examples.py` with a repo-local basetemp | PASS: 4 passed. |
| Focused typing | `uv --cache-dir .uv-cache run mypy examples/flat_model_wire_body.py tests/unit/test_docs_examples.py` | PASS: no issues in 2 source files. |
| Lint and whitespace | `uv --cache-dir .uv-cache run ruff check`; scoped `git diff --check` | PASS. |

At this increment phase 20 remained active because PC-06 was unresolved; the blocker was closed by
the 2026-08-26 follow-up recorded in the phase-20 section below.

## Canonical operation request shape correction (2026-08-26)

- Restored the phase-12 authoring invariant after phase 17, the OpenAPI generator and public
  documentation had drifted back to direct path/query/header/body parameters. Every first-party
  operation with HTTP input now owns exactly one named `TypedDict`; all wire fields live in that
  type and the decorated method exposes them only through `**request: Unpack[RequestType]`.
- `CallOptions` remains the only direct operation argument because it controls execution rather
  than the HTTP request. An operation with no HTTP input remains a zero-argument declaration and
  does not need an empty ceremonial `TypedDict`. Optional wire fields use `NotRequired[...]`.
- The OpenAPI generator now emits one request `TypedDict` per input-bearing operation, including
  path/query/header/cookie and root-body fields, and reserves request type names against generated
  model collisions. A session-service collision between the generated operation request and its
  Pydantic wire model was found and corrected.
- All repository-owned runnable examples, public guides, API references and relevant plugin
  documentation use the same shape. A permanent AST audit parses their decorated Python examples
  and rejects direct HTTP request parameters or a non-`Unpack[TypedDict]` variadic request.

| Gate | Command | Result |
|---|---|---|
| Request/docs/OpenAPI focus | cache-disabled pytest over typed request inputs, runnable examples, documentation API blocks and OpenAPI generator/real-world schemas | PASS: 71 passed. |
| Full available workspace suite | cache-disabled repo-local-basetemp pytest excluding the unreadable generated consumer fixture | PASS: 726 passed, 11 skipped in 104.49s. |
| Changed-source typing | `uv --cache-dir .uv-cache run mypy examples tests/unit/test_docs_examples.py tests/unit/test_documentation_api_examples.py plugins/openapi/eazy_sdk_openapi/generator.py plugins/openapi/tests/test_rewrite_generator.py plugins/openapi/tests/test_real_world_schemas.py` | PASS: no issues in 14 source files; generated SDK strict-mypy checks also pass in the OpenAPI suites. |
| Full static typing diagnostic | `uv --cache-dir .uv-cache run mypy` | NOT PASSING: 9 pre-existing dirty-worktree test errors call private descriptor `.resolve` through a public bound-operation type in `plugins/xml/tests/test_xml_codec.py`, `tests/unit/test_typed_request_inputs.py`, `tests/rewrite/test_phase18_contracts.py` and `tests/rewrite/test_phase08_execution.py`. These files were not changed by this correction. |
| Lint | `uv --cache-dir .uv-cache run ruff check` | PASS. |
| Documentation | freshness check; from `docs-site/`, `npm run check` and `npm run build` | PASS: 58 pages fresh, content/frontmatter valid and 73 pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Absence and diff hygiene | `uv --cache-dir .uv-cache run python scripts/absence_audit.py`; scoped `git diff --check` | PASS: removed architecture remains absent and edited files have no whitespace errors. |

At this increment phase 20 remained active because PC-06 was unresolved; the blocker was closed by
the 2026-08-26 follow-up recorded in the phase-20 section below.

## Scoped auth SDK and transport-neutral generated factory correction (2026-08-25)

- Removed the example-only root SDK capture from `DummyJsonLoginService`. Acquisition and refresh
  now call ordinary decorated endpoints through `context.sdk`.
- Public `client.bind_sdk(factory)` creates the root and stores its factory instead of a root
  instance. Each lifecycle transition
  builds a non-owning SDK facade over the shared runtime and carries its parent `LifecycleGraph`
  into nested auth resolution and refresh.
- A recursively protected login now raises `ResolutionCycleError` before handler I/O instead of
  re-entering the same session lock.
- The handwritten DummyJSON SDK and generated OpenAPI SDKs expose transport-neutral
  `from_handler(...)`. Generated `httpx(...)` is a convenience factory that delegates to it.

| Gate | Command | Result |
|---|---|---|
| Auth/example/OpenAPI/SQLModel focus | pytest with cache disabled over docs examples, phase 14, OpenAPI generator and SQLModel storage tests | PASS: 84 passed. The preceding run executed 83 passing tests but pytest exited 1 while writing the pre-existing malformed `.pytest_cache`; the cache-disabled rerun is authoritative. |
| Generated factory and real-world snapshots | cache-disabled pytest over OpenAPI generator and real-world schema suites | PASS: 37 passed, including strict generated mypy and `from_handler(...)` execution. |
| Full available workspace suite | cache-disabled repo-local-basetemp pytest excluding the unreadable generated consumer fixture | PASS: 711 passed, 11 skipped in 102.78s after the final public `bind_sdk(...)` refactor. |
| Static typing and lint | `uv --cache-dir .uv-cache run mypy`; `uv --cache-dir .uv-cache run ruff check` | PASS: no issues in 246 source files; lint clean. |
| Executable example | `uv --cache-dir .uv-cache run python examples/dummyjson_session_auth.py` | PASS: lazy login, protected call, 401 refresh and replay produced the documented four-request trace. The first invocation without the repo-local uv cache was blocked by the host uv cache ACL. |
| Documentation | freshness check; from `docs-site/`, `npm run check` and `npm run build` | PASS: 58 pages fresh, content/frontmatter valid and 73 pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |

## TypedDict request examples and reusable session scheme correction (2026-08-25)

- Flat object-shaped request documents are now represented by `TypedDict` in runnable examples,
  public guides and the SDK authoring reference. Pydantic/dataclass/msgspec request models remain
  only for nested documents or genuine model-owned validation/serialization policy.
- Added a structural `TypedDict` model adapter and strict request binding validation for required,
  optional and unknown keys plus value annotations. Mappings remain mappings; runtime does not
  manufacture ceremonial request objects.
- Added public `session_scheme(Model).configure(...)` for hand-written SDK roots. The same typed
  scheme is used by `ApiDefaults` and lifecycle auth, so the root uses public
  `client.bind_sdk(SdkClass)` without `_bind_sdk`, `Any`, `AuthScheme[object]`, `type(self)` or an
  auth-capturing lambda. The frozen `session_auth(...)` signature remains unchanged.

| Gate | Command | Result |
|---|---|---|
| TypedDict/auth/docs focus | cache-disabled pytest over model adapters, flat bodies, runnable docs examples and phases 14, 15 and 17 | PASS: 99 passed. |
| Configured full suite diagnostic | `uv --cache-dir .uv-cache run pytest -q` | The first run exposed the prohibited `session_auth` signature extension and also hit 31 setup errors from the pre-existing ACL-broken `.test-tmp/pytest`; the API extension was removed. This run is not reported as passing. |
| Full available workspace suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/full-typed-dict-20260825` | PASS: 713 passed, 11 skipped in 103.14s. |
| Static typing and lint | `uv --cache-dir .uv-cache run mypy`; `uv --cache-dir .uv-cache run ruff check` | PASS: no issues in 246 source files; lint clean. |
| Documentation | freshness check; from `docs-site/`, `npm run check` and `npm run build` | PASS: 58 pages fresh, content/frontmatter valid and 73 pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Absence and packages | `scripts/absence_audit.py`; core and five plugin wheel/sdist builds; `scripts/package_audit.py` | PASS: removed paths remain absent; release metadata, licenses, typing markers and Zapros-only boundary are valid. |

## Declarative `Unpack[TypedDict]` request arguments (2026-08-25)

- Declarative sync/async methods now accept `**request: Unpack[TypedDict]`. Class creation expands
  required/optional typed fields and their `Annotated` placements into the existing identity-based
  `InputField`/slot shape; calls flatten the bound keyword mapping before the unchanged
  compiler/executor path.
- Plain variadic kwargs, non-`TypedDict` unpacking, missing/multiple placements, duplicate names and
  the reserved `options` field fail during operation declaration. A strict-mypy regression proves
  that required and unknown keyword checks remain visible to IDE/type checkers.
- Flat JSON examples and public guides use `JsonField` metadata plus `Unpack`, so callers pass named
  arguments instead of constructing a wrapper dict. Root `JsonBody` remains for scalar, array,
  nested/model-owned documents and mandatory-protection wire-model flows.
- The phase-17 absence audit no longer rejects `Unpack` itself; it continues to reject the removed
  `EndpointContract`/`EndpointCall` and public `bind()`/`execute*()` architecture.

| Gate | Command | Result |
|---|---|---|
| Unpack/compiler/examples/auth focus | cache-disabled pytest over typed inputs, flat bodies, runnable docs examples, phase 17 and session auth | PASS: 65 passed. |
| Executable session example | `uv --cache-dir .uv-cache run python examples/dummyjson_session_auth.py` | PASS: login, protected call, 401 refresh and replay produced the documented four-request trace. |
| Initial full suite diagnostic | cache-disabled full pytest with repo-local basetemp | 718 passed, 11 skipped, 1 failed because the old phase-17 absence text gate still banned every `Unpack[` occurrence. This run is not reported as passing. |
| Absence correction focus | absence audit plus phase-10 absence and typed-input tests | PASS: audit clean; 23 passed. |
| Final full suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/full-unpack-final-20260825` | PASS: 719 passed, 11 skipped in 104.25s. |
| Static typing and lint | `uv --cache-dir .uv-cache run mypy`; `uv --cache-dir .uv-cache run ruff check` | PASS: no issues in 246 source files; lint clean. |
| Documentation | freshness check; from `docs-site/`, `npm run check` and `npm run build` | PASS: 58 pages fresh, content/frontmatter valid and 73 pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |

## Narrow HTTP decorator namespace (2026-08-25)

- HTTP operation declarations now use one canonical import, `from eazy_sdk import api`, and
  `@api.get/post/put/patch/delete`. `api` is a slotted namespace object rather than the broad
  `eazy_sdk.api` implementation module; its complete public attribute set is exactly those five
  decorators, with no `SyncApi`, `AsyncApi`, raw runtime type or other accidental export.
- Direct verb exports were removed from root `eazy_sdk`, `eazy_sdk.api` and `eazy_sdk.codegen`
  without aliases. Examples, tests, public/implementation docs and OpenAPI generated source use the
  namespace syntax; Museum snapshots were regenerated.
- Public-surface and absence gates assert the exact namespace and reject direct verb imports in
  first-party examples/docs as well as reintroduced root/codegen/module exports. A nested test
  helper's local `api` instance was renamed after the full suite exposed Python lexical shadowing.
- The package audit's old blanket `Unpack[` ban was removed: PEP 692 declarative method arguments
  are canonical phase-17 API and are still checked by source/runtime/type tests. The audit continues
  to reject the removed contract/router architecture.

| Gate | Command | Result |
|---|---|---|
| Namespace/OpenAPI/absence focus | cache-disabled pytest over phases 10, 14 and 17 plus OpenAPI generator/real-world suites | PASS: 73 passed, including exact `dir(api)`, missing direct exports, generated strict mypy and snapshots. |
| Collision/example regression focus | cache-disabled pytest over auth, runnable docs examples, absence and public namespace | PASS: 49 passed. DummyJSON refresh again emits `POST /auth/refresh`. |
| Initial full-suite diagnostic | cache-disabled full pytest with repo-local basetemp | 706 passed, 11 skipped, 13 failed: 12 failures exposed the local `api` shadow in one nested auth helper and one exposed the incorrect GET refresh example. This run is not reported as passing. |
| Final full suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/api-namespace-full-final-20260825` | PASS: 719 passed, 11 skipped in 103.62s. |
| Static typing and lint | `uv --cache-dir .uv-cache run mypy`; `uv --cache-dir .uv-cache run mypy examples`; `uv --cache-dir .uv-cache run ruff check` | PASS: no issues in 246 source files, no issues in 9 examples, lint clean. |
| Documentation | freshness check; from `docs-site/`, `npm run check` and `npm run build` | PASS: 58 pages fresh, content/frontmatter valid and 73 pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Packages | parallel workspace build followed by sequential core recovery; `scripts/package_audit.py .test-tmp/api-namespace-build` | PASS: all six wheel/sdist pairs built and audited. The parallel core build first lost a temporary Hatchling path on Windows while all five plugins succeeded; sequential core build succeeded. The first audit then exposed the stale `Unpack[` ban; after correcting that gate, the final audit passed. |
| Diff hygiene | scoped `git diff --check` over the namespace, generator, tests, examples and docs changes | PASS. |

## Public documentation aligned with the real API (2026-08-25)

- Rebuilt the API-method reference around the actual `_Verb.__call__` signature and public
  namespace. It now documents all decorator arguments, all four `ApiDefaults` fields, the exact
  five supported HTTP verbs, unsupported HEAD/OPTIONS/TRACE declarations and the real
  `ResponseEnvelope[T, Any]` type instead of an undefined `Raw` placeholder.
- Updated request, dependency, codec, store SDK, quickstart, README and authoritative authoring
  snippets. Every block that declares `@api.<verb>` now imports `api` in that same block. Flat
  JSON/form/multipart structures consistently use `Unpack[TypedDict]` plus field placements;
  root body markers are reserved for scalar, array, nested or model-owned documents.
- Corrected an invalid phase-18 Python signature where a required body parameter followed a query
  parameter with a default. Implementation examples for `Inject` and custom codecs are now
  syntactically valid classes with imports matching the real public modules.
- Added a permanent documentation accuracy test. It scans 308 Python fences, parses all 40 blocks
  containing HTTP decorators, verifies their local `api` import, resolves every parseable
  `eazy_sdk.*` import against the installed modules and rejects direct verb imports.

| Gate | Command | Result |
|---|---|---|
| Documentation/API focus | cache-disabled pytest over the new docs audit, runnable docs examples, phase-10 absence and phase-14 public surface | PASS: 39 passed. |
| Full suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/docs-real-api-full-20260825` | PASS: 722 passed, 11 skipped in 102.55s. |
| Static typing and lint | `uv --cache-dir .uv-cache run mypy`; `uv --cache-dir .uv-cache run ruff check` | PASS: no issues in 247 source files; lint clean. |
| Documentation | freshness check; from `docs-site/`, `npm run check` and `npm run build` | PASS: 58 pages fresh, content/frontmatter valid and 73 pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |

### Complete hand-written login composition example (2026-08-26)

- The Login guide now declares one reusable `SessionScheme`, a protected account operation and a
  typed root SDK instead of stopping after the standalone auth service.
- The root factory passes `LoginService()` to `USER_SESSION.configure(...)`, installs the returned
  `Auth` through `ClientConfig(auth=auth)` and calls `client.bind_sdk(cls)` so lifecycle calls receive
  a scoped SDK through `AuthContext.sdk`.
- The generated-SDK path is a separate final step and states that its generated root factory embeds
  the auth service. The repository's executable DummyJSON session example remains the full
  login/401-refresh/replay reference.

| Gate | Command | Result |
|---|---|---|
| Documentation API audit | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/docs-login-page tests/unit/test_documentation_api_examples.py` | PASS: 3 passed. |
| Documentation freshness | `uv --cache-dir .uv-cache run python scripts/docs_freshness.py check` | PASS: 58 pages fresh. |
| Documentation validation | from `docs-site/`: `npm run check` | PASS: content and frontmatter valid. |
| Documentation build | from `docs-site/`: `npm run build` | PASS: 73 pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Live dev page | HTTP GET `http://127.0.0.1:4321/auth/login/` plus rendered-content markers | PASS: HTTP 200; the hand-written step, `ClientConfig` and `bind_sdk` are present. |

### Direct SDK construction experiment (2026-08-26)

- Added an isolated prototype whose public root is constructed directly as
  `DirectSdk(credentials=..., base_url=...)`. Endpoint groups are immediately available; the class
  has no public `httpx`, `from_handler` or `bind_sdk` method.
- The constructor configures session auth and the default HTTPX handler internally. Optional
  `handler`, saved `session`, `ClientConfig` and handler ownership remain constructor-injected
  dependencies.
- The existing `client.bind_sdk(...)` call is private implementation detail. Its factory returns the
  already-created root for the initial binding and creates non-owning facades for lifecycle-scoped
  clients without recursively executing the public constructor.
- The prototype proves lazy login, typed public handles, Bearer placement, `401` refresh/replay,
  supplied-session login bypass, default transport ownership and duplicate auth-owner rejection.
  Production `eazy_sdk` was not modified.

| Gate | Command | Result |
|---|---|---|
| Experiment behavior | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/direct-sdk-construction-final experiments/direct_sdk_construction` | PASS: 5 passed. |
| Experiment typing | `uv --cache-dir .uv-cache run mypy experiments/direct_sdk_construction` | PASS: no issues in 3 source files. |
| Experiment lint | `uv --cache-dir .uv-cache run ruff check experiments/direct_sdk_construction` | PASS. |

### Fail-fast auth service contract validation (2026-08-26)

- `session_auth(...)` and `SessionScheme.configure(...)` now reject an invalid auth service while
  building auth, before SDK binding, session lookup or network I/O.
- Required `acquire` must exist, be callable, be declared with `async def` and accept
  `(credentials, context)` positionally. Optional `refresh` may be absent; when present it must be
  callable, async and accept `(session, context)` positionally.
- Every failure names the concrete service class and method through `SessionConfigurationError`.
  A service that intentionally supports only acquisition remains valid.

| Gate | Command | Result |
|---|---|---|
| Auth/session focus | cache-disabled pytest over phase 14, session integration/unit and runnable auth examples | PASS: 64 passed. |
| Initial configured full suite | `uv --cache-dir .uv-cache run pytest -q` | Environment failure: 692 passed, 11 skipped and 32 setup errors because pytest could not clean the pre-existing ACL-blocked `.test-tmp/pytest`. This run is not reported as passing. |
| Final full suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/auth-service-validation-final-full-20260826` | PASS: 724 passed, 11 skipped in 104.08s. |
| Static typing | `uv --cache-dir .uv-cache run mypy` | PASS: no issues in 247 source files. |
| Lint | `uv --cache-dir .uv-cache run ruff check` | PASS. |

### Response-owned declarative method result typing (2026-08-26)

- `Responses[T]` is now the single static source for a decorated operation's successful result.
  The verb decorator carries `T` into one typed descriptor surface; binding that descriptor to
  `SyncApi` or `AsyncApi` exposes `T` or `Awaitable[T]` without reading the declaration's return
  annotation.
- Empty declaration methods may omit `-> T`. Runtime derives the result from successful
  `Json`/`Html`/`Extracted`/`Parsed`, `Text`, `Bytes` and `Empty` representations and publishes that
  derived type through the bound `inspect.signature`. An empty or otherwise uninferable success
  set still requires a return annotation as a runtime fallback and raises a focused configuration
  error when neither source exists.
- The runtime inference helper remains private; no extra public method was added to `Responses` or
  the `api` namespace. Existing explicit return annotations remain valid.
- OpenAPI generation now emits `Responses[ResultType](...)`, so generated descriptor typing also
  comes from the response declaration. Hand-written JSONPlaceholder and quickstart examples omit
  duplicate result annotations. Authoring/API docs explain the `mypy --strict` syntactic
  `no-untyped-def` exception for declaration-only modules; the bound method still has exact type
  `T`.

| Gate | Command | Result |
|---|---|---|
| Core typing/response focus | cache-disabled pytest over phases 05/17, endpoint calls, typed inputs and documentation examples | PASS: 50 passed. |
| OpenAPI generation | cache-disabled pytest over rewrite generator and real-world schemas | PASS: 37 passed, including snapshot and strict generated-mypy gates. |
| Runtime signature smoke | import the return-free JSONPlaceholder API and inspect its bound `get_post` | PASS: signature reports `BlogPost`; identity check against the model is true. |
| Configured full suite | `uv --cache-dir .uv-cache run pytest -q` | Environment failure after 693 passed and 11 skipped: 32 setup errors because pytest cannot clean the pre-existing ACL-blocked `.test-tmp/pytest`. This run is not reported as passing. |
| Final full suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/response-return-inference-full-20260826` | PASS: 725 passed, 11 skipped in 103.36s. |
| Static typing | `uv --cache-dir .uv-cache run mypy` | PASS: no issues in 247 source files. |
| Lint | `uv --cache-dir .uv-cache run ruff check` | PASS. |
| Documentation code audit | cache-disabled pytest over documentation API fences and runnable docs examples | PASS: 11 passed. |
| Documentation freshness | `uv --cache-dir .uv-cache run python scripts/docs_freshness.py check` | PASS: 58 pages fresh. |
| Documentation site | from `docs-site/`: `npm run check`; `npm run build` | PASS: content/frontmatter valid and 73 pages built. The first build exposed and prompted removal of an unsupported `Note` MDX component; the final build is green. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |

### PyCharm 2025.3 declarative typing follow-up (2026-08-26)

- PyCharm 2025.3.3 was verified against the hand-written JSONPlaceholder SDK. For an unannotated
  declaration whose body only raises `NotImplementedError`, the IDE ignores the generic
  callable-object decorator result, displays `NoReturn` on the method and `Never` on the bound
  call. A reduced probe confirms that this PyCharm version applies the transformation only when
  the decorator factory and its result are both plain `Callable` types; that shape cannot expose
  the typed `.with_response` descriptor API.
- The JSONPlaceholder declarations now repeat their successful `Responses[T]` type as explicit
  return annotations for PyCharm completion. The `.with_response` result has an explicit local
  `ResponseEnvelope[BlogPost]` annotation. Runtime lowering and the mypy-inferred descriptor path
  are unchanged.

| Gate | Command | Result |
|---|---|---|
| Installed IDE verification | PyCharm 2025.3.3 Quick Documentation over `by_user` before and after the annotations | PASS: changed from `Never` to `list[BlogPost]`. |
| Focused static typing | `uv run mypy examples/jsonplaceholder_posts.py` | PASS: no issues in 1 source file. |
| Focused lint | `uv run ruff check examples/jsonplaceholder_posts.py` | PASS. |
| Declarative API regression | cache-disabled pytest over `tests/rewrite/test_phase17_declarative_api.py` with a repo-local basetemp | PASS: 8 passed. |
| Documentation freshness | `uv run python scripts/docs_freshness.py check` | PASS: 58 pages fresh. |

At this increment phase 20 remained active because PC-06 was unresolved; the blocker was closed by
the 2026-08-26 follow-up recorded in the phase-20 section below.

## Phase 20: unified payload crypto for HTTP and WebSocket (2026-08-25)

Authoritative plan: [20-unified-payload-crypto.md](20-unified-payload-crypto.md).

| Increment | State | Completed exit criteria | Commands and results | Remaining / blockers |
|---|---|---|---|---|
| PC-00 | complete | Typed lambda path recording, mypy typo fixture, Pydantic/dataclass/msgspec aliases, negative selector/model/overlap cases and isolated experiment are verified. | Experiment: 7 passed; production matrix is included in the 156-test focus run. | None. |
| PC-01 | complete | Minimal `eazy_sdk.crypto`, structural sync/async protocols, immutable contexts/frozen JSON, typed failures, path compiler, limits, redacted/hashable declarations and runtime validation exist. | Focused pytest/mypy/ruff: PASS. | None. |
| PC-02 | complete | HTTP/WS scoped registries, deterministic priority ambiguity, operation/default/explicit-none precedence, direction-limited rules, HTTP/WS plan stages and reserved metadata writer collisions are implemented. | Scope/compiler tests and PC-06 signing/auth writer collision tests pass before transport/provider work. | None. |
| PC-03 | complete | HTTP field transforms run after model dump and before codec; content coding precedes encoded encryption; exact signing sees captured ciphertext; final media/length ownership, retry and redirect rebuilding pass. | HTTP crypto tests in the 156-test focus run: PASS. | None. |
| PC-04 | complete | Inbound decrypt precedes codec/model validation, raw/effective media are separate, plaintext statuses are explicit, mismatch/malformed/limit/redaction and sync/async parity pass. | HTTP crypto tests in the 156-test focus run: PASS. | None. |
| PC-05 | complete | Common stages cover WS send/call/subscription, binary default, text-safe override, exact-frame ordering, reconnect replay and captured frames. Old protector protocols/chains/exports were removed without aliases. | `tests/crypto tests/websocket`: 110 passed, 8 skipped; source/package absence gates pass. | None. |
| PC-06 | complete | Identity-based typed dependency inputs/AAD, `CryptoResult` output validation, HTTP header and WS document-envelope bindings, writer collision checks and explicit WS connection-vs-operation stage semantics are implemented alongside the earlier limits/cancellation/redaction/replay hardening. | New PC-06 conformance: 7 passed; crypto/WS focus: 117 passed, 8 skipped; expanded focus: 170 passed, 8 skipped; focused mypy/ruff pass. | None. The configured workspace gates remain phase-level work, not a PC-06 API blocker. |
| PC-07 | complete | Canonical `x-eazy-sdk-crypto` validation/lowering for OpenAPI and AsyncAPI, generated typed registries, deterministic/import/strict-mypy checks, public guide/fingerprints, package and absence audits exist. | Codegen focus, docs and package gates below pass. | None. |

### Phase 20 implementation evidence

- `eazy_sdk.crypto` owns only typed orchestration contracts; application algorithms are structural
  objects and the core dependency set remains only `zapros==0.16.0`.
- HTTP and WebSocket retain separate state machines. Both use the common profile/compiler stages,
  while HTTP lowers to the existing attempt coordinator and WebSocket lowers to its own
  generation/replay/subscription runtime.
- An inbound encoded WebSocket profile is connection-unique: operation identity is inside the
  encrypted envelope and cannot be selected before decrypt. Conflicting profiles fail closed.
- OpenAPI and AsyncAPI generated source accepts named handwritten profiles through
  `crypto_registry(profiles)` and contains no selector lambda, dynamic crypto import, key material
  or algorithm implementation.
- The public reader path now starts with a minimal store GET and continues in one SDK tutorial
  through JSON models, typed API errors, Bearer auth, exact HMAC signing, field/encoded crypto and
  WebSocket reuse. A separate crypto reference records selectors, algorithm protocols, wire
  ownership, scopes and failures. The local payment example executes auth, signing, outbound
  crypto and inbound crypto without public network access.
- PC-06 uses `CryptoInput[T]` identities backed by the existing `RequestDependency[T]` and
  `DependencyRegistry`; HTTP resolves operation inputs per attempt, while WebSocket caches only
  connection-scoped inputs for one generation. `CryptoContext.input(...)`, redacted `aad` and
  `CryptoContext.metadata(...)` are the only algorithm read surfaces.
- Algorithms may return `CryptoResult[T]` with typed `CryptoOutput[T]` values. HTTP owns explicit
  header bindings; WebSocket owns explicit document-envelope paths. Missing, duplicate,
  undeclared, mistyped and colliding outputs fail closed. Whole-frame WS detached metadata remains
  inside the application algorithm's byte envelope because routing is unavailable before decrypt.

| Gate | Command | Result |
|---|---|---|
| Crypto/WS/codegen focus | repo-local-basetemp pytest over `tests/crypto`, `tests/websocket`, OpenAPI and AsyncAPI tests | PASS: 156 passed, 8 skipped. |
| Full available workspace suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/docs-store-sdk-run --ignore=tests/fixtures/openapi_consumer/consumer_sdk -o "pythonpath=plugins/xml" tests plugins/openapi/tests plugins/asyncapi/tests plugins/presets/tests plugins/sqlmodel/tests plugins/xml/tests` | PASS: 703 passed, 11 skipped in 114.51s. The first follow-up run used a missing nested basetemp parent and ended with 672 passed, 11 skipped and 31 `tmp_path` setup errors; rerunning with the direct repo-local basetemp above removed those environment errors. |
| Configured pytest command | `uv --cache-dir .uv-cache run pytest -q` | PASS: 703 passed, 11 skipped in 103.79s. Pytest now uses the repository-local `.test-tmp/pytest`, ignores only the separately gated unreadable generated consumer, and discovers core plus all five plugin suites. |
| Static typing | `uv --cache-dir .uv-cache run mypy` | PASS: no issues in 237 source files. The generated consumer exclusion is now part of the checked-in mypy configuration. |
| Lint and absence | `uv run ruff check`; `uv run python scripts/absence_audit.py` | PASS. |
| Documentation | `uv --cache-dir .uv-cache run python scripts/docs_freshness.py check`; from `docs-site/`, `npm run check` and `npm run build` | PASS: 58 pages fresh; frontmatter valid; 73 HTML pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Executable documentation | `uv --cache-dir .uv-cache run python examples/quickstart.py`; same command for `examples/docs/store_sdk.py`; `ruff check` and `mypy` over both files | PASS: both examples produced their documented output; lint and typing are clean. |
| Visual documentation QA | local Astro preview, `playwright-cli`, then in-app Browser fallback | BLOCKED: the Playwright daemon closed its session under the installed Node 24 runtime, and the in-app runtime reported `No browser is available`. Static build, content validation and executable examples pass; no visual browser result is claimed. |
| Packages | sequential core, AsyncAPI, OpenAPI, presets, SQLModel and XML wheel/sdist builds; `scripts/package_audit.py` | PASS: all six distributions are `0.1.0a1`, carry MIT metadata and license text, include typing markers, contain no removed WS protector API/cipher implementation and add no mandatory crypto dependency. |
| Repository publication | release metadata, ignore/line-ending policy, public repository files, GitHub CI, artifact cleanup and common private-key/token signature scan | PASS: alpha metadata is synchronized across the workspace and docs package; MIT `LICENSE`, changelog, contribution/security policies, `.editorconfig`, `.gitattributes`, comprehensive `.gitignore` and two-job CI are present; tracked `.idea` files were removed from the index and confirmed local build/cache/demo database artifacts were deleted; no common private-key/token signatures were found in publishable source/docs. No Git remote exists, so repository/homepage URLs remain intentionally unset. |
| Dependency lock | `uv --cache-dir .uv-cache lock --check` | PASS: 135 packages resolved, including `eazy-sdk-xml==0.1.0a1` as a workspace member. |

### PC-06 blocker closure follow-up (2026-08-26)

| Gate | Command | Result |
|---|---|---|
| PC-06 contract conformance | `uv run pytest -q -p no:cacheprovider tests/crypto/test_pc06_aad_metadata.py` | PASS: 7 passed; covers HTTP sync/async typed AAD and header round trip, attempt/generation input lifetime, declared output validation/redaction, signing collision before transport, WS envelope round trip, reserved-writer collision and whole-frame stage rejection. |
| Crypto + WebSocket regression | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/pc06-final-run tests/crypto tests/websocket` | PASS: 117 passed, 8 skipped. A first rerun hit the known locked default basetemp; the unique repo-local basetemp removed that environment-only error. |
| Expanded phase focus | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/pc06-expanded-final tests/crypto tests/websocket tests/rewrite/test_phase08_execution.py tests/rewrite/test_phase14_public_api.py tests/unit/test_phase21_body_projection_proof.py` | PASS: 170 passed, 8 skipped. |
| Focused typing/lint | `uv run mypy eazy_sdk/crypto eazy_sdk/clients/executor.py eazy_sdk/websocket tests/crypto/test_pc06_aad_metadata.py`; matching `uv run ruff check` paths | PASS: no issues; lint clean. |
| Documentation | `python scripts/docs_freshness.py check`; from `docs-site/`, `npm run check`, `npm run build` | PASS: 58 pages fresh, frontmatter valid and 73 pages built. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Configured full pytest | `uv run pytest -q -p no:cacheprovider` | BLOCKED during collection by pending phase-21 `examples/flat_model_wire_body.py`: unplaced `RegisterUser` fields raise `PlanError`. No full-suite pass is claimed. |
| Configured full typing/lint | `uv run mypy`; `uv run ruff check` | BLOCKED by 12 existing descriptor/example typing errors and the pending phase-21 example's unused `user`. The PC-06 focused paths pass both gates. |

### Example gallery follow-up (2026-08-25)

- Replaced the deleted legacy example set with a progressive runnable gallery. It starts with a
  deterministic typed GET, then uses JSONPlaceholder for JSON request/response/query handling,
  Books to Scrape for nested HTML extraction and pagination, a local response-case server for
  typed 404/429 errors, and DummyJSON for login plus a Bearer-protected call.
- Restored the documentation's advanced local store SDK as one executable payment flow combining
  static Bearer auth, exact-body HMAC, typed field selectors, whole-body encryption, encrypted
  media type ownership and inbound decryption. Its Base64 wrappers are explicitly marked as
  interface demonstrations, not production cryptography.
- Added a Russian `examples/README.md` that orders examples by difficulty, records expected output,
  explains Python/wire aliases and `.with_response()`, distinguishes static Bearer from
  `session_auth`, and warns about public-site terms, robots and rate policies.
- Live smoke exposed an HTTPX boundary defect: HTTPX returned decoded response content together
  with the original `Content-Encoding`, so Zapros attempted gzip decoding twice. The handler now
  drops stale encoding/length headers and lets Zapros materialize the decoded length; a focused
  compressed-response regression covers the behavior.
- Examples are part of the checked-in mypy scope. Their regression tests use `MockTransport` and
  HTML fixtures, so the default suite never depends on public network availability.
- Added `dummyjson_session_auth.py` as the hand-written SDK counterpart to the generated session
  factory. The public root accepts exactly one of credentials or an existing session. A consumer
  calls only `sdk.users.me()`; first-use acquire, Bearer placement, 401 refresh and replay remain
  inside `session_auth`. The deterministic DummyJSON-shaped server proves that a supplied session
  skips login entirely. The root forwards both optional values directly to `session_auth`; it does
  not duplicate the runtime's exactly-one validation with a manual credentials/session branch.

| Gate | Command | Result |
|---|---|---|
| Example/HTTPX regressions | `uv --cache-dir .uv-cache run pytest -q tests/rewrite/test_phase03_transports.py tests/unit/test_docs_examples.py` | PASS: 14 passed, including automatic login/refresh/replay, supplied-session login bypass, JSON aliases/query/body, HTML scopes, auth isolation and single gzip decoding. |
| Public sandbox smoke | `uv --cache-dir .uv-cache run python` for `examples/jsonplaceholder_posts.py`, `examples/books_to_scrape.py` and `examples/dummyjson_auth.py` | PASS: JSONPlaceholder GET/filter/fake POST, 20 Books to Scrape cards and DummyJSON login plus `/auth/me` completed against the live services. No token was printed. |
| Full available workspace suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/example-gallery-full/run-3 --ignore=tests/fixtures/openapi_consumer/consumer_sdk -o "pythonpath=plugins/xml" tests plugins/openapi/tests plugins/asyncapi/tests plugins/presets/tests plugins/sqlmodel/tests plugins/xml/tests` | PASS: 710 passed, 11 skipped in 101.33s. |
| Configured pytest command | `uv run pytest -q` | BLOCKED after reaching 100% by the pre-existing malformed `.pytest_cache/v/cache` path (`FileExistsError [WinError 183]`) while saving pytest node IDs. The no-cache full suite above is the verification result. |
| Static typing | `uv --cache-dir .uv-cache run mypy` | PASS: no issues in 246 source files, including `examples`. |
| Lint | `uv --cache-dir .uv-cache run ruff check` | PASS. |
| Documentation | `uv --cache-dir .uv-cache run python scripts/docs_freshness.py check`; from `docs-site/`, `npm run check` and `npm run build` | PASS: 58 pages fresh, frontmatter valid and 73 pages built. Existing Astro markdown deprecation and missing `docs -> 404` notices remain non-fatal. |

Phase 20 remains active after PC-06 closure only because the configured full gates now encounter
pending phase-21 example/descriptor work recorded in the follow-up table above.

### Phase 20 planning evidence

- The plan was derived from the completed phase-19 HTTP/WS boundaries and preserves separate
  protocol state machines.
- The isolated experiment demonstrates a typed lambda path recorder and separate field/document
  and encoded/body stages without importing production `eazy_sdk`.
- The experiment API uses `request`/`response` and `body`; the production plan deliberately changes
  these to protocol-neutral `outbound`/`inbound` and `encoded` before API freeze.
- No production implementation, migration, full-suite result or phase exit criterion is claimed by
  this planning increment.

| Gate | Command | Result |
|---|---|---|
| Typed-selector experiment | `uv run pytest -q -p no:cacheprovider experiments/payload_crypto_api` | PASS: 7 passed in 0.30s. |
| Experiment typing | `uv run mypy experiments/payload_crypto_api` | PASS: no issues in 3 source files. |
| Experiment lint | `uv run ruff check experiments/payload_crypto_api` | PASS. |
| Phase links and structure | targeted `rg` checks over master plan, phase plan and status | PASS: phase link and PC-00–PC-07 rows are present. |
| Markdown fence parity | PowerShell fence count over `20-unified-payload-crypto.md` | PASS: 26 fence markers. |
| Changed documentation whitespace | trailing-whitespace scan over all three planning files and `git diff --check` over tracked files | PASS; only existing Windows LF-to-CRLF notices were reported. |
| Documentation freshness | `uv run python scripts/docs_freshness.py check` | PASS: 55 pages fresh. |

## Phase 19: WebSocket runtime, GraphQL-WS and AsyncAPI 3.0 (2026-08-23)

Authoritative plan: [19-websocket-runtime.md](19-websocket-runtime.md).

| Increment | State | Completed exit criteria | Commands and results | Remaining / blockers |
|---|---|---|---|---|
| WS-00 | complete | Phase contract adopted; `websocket` extra is pinned and included in `all`/dev; scripted fake connection/connector, deterministic clock/backoff and task-leak guard exist; HTTP fingerprints, import isolation and separate-runtime architecture are characterized. | 6 focused WS tests, 17 HTTP characterization tests, 566-test full suite, mypy, ruff, docs freshness, lock and isolated wheel installs: PASS. Details below. | None. |
| WS-01 | complete | `ValueSlot`, `OperationShape`, `OperationValues`, `ValuePatch`, `OperationCallState`, identity/metadata, scope/graph primitives and case arbitration are protocol-neutral; HTTP compiler/plan/declaration ownership is explicit; old internal names/modules are absent; nominal registries are type-safe. | 7 focused WS-01 tests, 90 HTTP rewrite tests, 33 OpenAPI tests, 573-test full suite, strict mypy, ruff and docs freshness: PASS. Details below. | None. |
| WS-02 | complete | Immutable logical/prepared/encoded artifacts; lossless frozen JSON tree; deterministic JSON text codec; Zapros text/binary/ping/pong/close normalization; pre-decode limits; pure `WsProtocol`; explicit `JsonEventProtocol`; close and recovery classification. | 13 focused WS-02 tests, 26-test WebSocket aggregate, 586-test full suite, strict mypy and ruff: PASS. Details below. | None. |
| WS-03 | complete | Async session state machine; one reader and serialized bounded writer; generation/namespace/correlation pending keys; one-shot `SEND`/`CALL` decorators; fail-closed uncertain delivery; explicit unsent/deduplicated replay; atomic timeout/cancellation/disconnect/close cleanup and task ownership. | 10 focused WS-03 tests, 36-test WebSocket aggregate, 147 HTTP regression tests, 596-test full suite, strict mypy, ruff, docs freshness and lock consistency: PASS. Details below. | None. |
| WS-04 | complete | Bounded async `Subscription`/`Event`; independent subscription state; distinct reconnect/replay/resubscribe/recovery policies; fatal/transient close classification; bounded reconnect budget and heartbeat supervisor; sequence/token recovery, gap metadata/errors and non-blocking overflow policies. | 10 focused WS-04 tests, 46-test WebSocket aggregate, 147 HTTP regression tests, 606-test full suite, strict mypy and ruff: PASS. Details below. | None. |
| WS-05 | complete | `JsonPayload`/`Replies`/`Messages` over common model/case kernels; typed success/error/malformed outcomes; declared semantic outputs; timestamp/nonce/per-message auth; HMAC/custom immutable signing; ordered frame/message protector chains; sealed stages and redacted artifacts/snapshots. | 10 focused WS-05 tests, 56-test WebSocket aggregate, 147 HTTP regression tests, 616-test full suite, strict mypy and ruff: PASS. Details below. | None. |
| WS-06 | complete | Static upgrade, refreshed protocol and dynamic per-message auth are distinct; connection/message/subscription middleware return typed decisions/patches under six-dimensional `WsScope`; HTTP/WS runtimes remain separate with shared model registry bootstrap; fake, Zapros ASGI and default localhost network paths share behavior; unsupported handoff is typed. | 12 focused WS-06 tests, 68-test WebSocket aggregate, 147 HTTP regression tests, 628-test full suite, strict mypy, ruff, docs freshness and lock consistency: PASS. Details below. | None. |
| WS-07 | complete | First-party `graphql-transport-ws` covers ACK handshake, ping/pong, query/mutation/subscription routing, terminal error/complete, cancellation and fatal duplicate-ID close. `eazy-sdk-asyncapi` parses JSON/YAML AsyncAPI 3.0 with identity-preserving refs, canonical extensions and diagnostics, then generates deterministic typed thin SDKs that import, strict-mypy and execute on the common runtime. Public docs/fingerprints, absence/package/isolation and all release gates pass. | 14 focused GraphQL/AsyncAPI tests, 82-test WS/generator aggregate, 147 HTTP regression tests, 642-test full suite, mypy, ruff, docs, absence, wheel/sdist audit and isolated installs: PASS. Details below. | None. |

### Phase 19 planning evidence

- The root WebSocket architecture proposal was reviewed against the completed phase-18 runtime,
  public SDK references, current tests and the installed Zapros 0.16 WebSocket implementation.
- The initial proposal's separate Eazy SDK `AsyncWsTransport` boundary was rejected. Eazy SDK will
  consume Zapros `aconnect_ws`/`AsyncBaseWebSocket` directly and will not add transport adapters.
- Static upgrade headers remain available through a configured Zapros client. Dynamic upgrade
  headers are explicitly out of scope; reconnect auth uses protocol or per-message application.
- No runtime implementation or release gate is claimed by this planning increment.

| Gate | Command | Result |
|---|---|---|
| Installed Zapros WebSocket API | `uv run python` inspection of `zapros.websocket`, `aconnect_ws`, `AsyncBaseWebSocket` and message types | PASS: Zapros 0.16 exposes async/sync connection APIs, text/binary/ping/pong/close messages, stdlib network and ASGI WebSocket paths. |
| Phase links and progress rows | `rg -n "phase 19|19-websocket|WS-0[0-7]|zapros\\.websocket" docs/implementation/...` | PASS: master-plan, phase document and status cross-links are present. |
| Markdown fence parity | PowerShell regex count over `19-websocket-runtime.md` | PASS: 22 fence markers. |
| Changed documentation whitespace | `git diff --check -- docs/implementation/README.md docs/implementation/STATUS.md docs/implementation/19-websocket-runtime.md` | PASS; only existing Windows LF-to-CRLF notices were reported. |
| Documentation freshness | `uv run python scripts/docs_freshness.py check` | PASS: 54 pages fresh. |

### WS-00 verification evidence

- `tests/websocket/_support` implements the Zapros `AsyncBaseWebSocket` and `aconnect_ws` call
  shapes entirely in memory. The connector scripts successful connections and handshake failures;
  the connection scripts frames and receive failures.
- The clock, backoff and task-leak helpers make time and concurrency checks deterministic.
- Architecture tests reject WebSocket imports from the HTTP runtime and reject a universal HTTP/WS
  request or executor declaration.
- The first combined baseline command timed out after 124 seconds because its pytest process exceeded
  the 120-second command limit. No PASS was claimed. The full suite was rerun separately with a
  300-second limit and passed.

| Gate | Command | Result |
|---|---|---|
| Dependency lock | `uv lock` and `uv lock --check` | PASS: lock resolves 133 packages and adds `wsproto==1.3.2` through the optional Zapros WebSocket extra. |
| WS-00 focused behavior/architecture | `uv run pytest -q tests/websocket/test_ws00_baseline.py` | PASS: 6 passed in 1.45s. |
| WS-00 focused typing | `uv run mypy tests/websocket` | PASS: no issues in 4 source files. |
| WS-00 focused lint | `uv run ruff check tests/websocket` | PASS. |
| HTTP public fingerprints and phase-18 behavior | selected phase-14 public namespace/client tests, phase-15 session/account fingerprints and all of `tests/rewrite/test_phase18_contracts.py` | PASS: 17 passed in 0.49s. |
| Core configured suite | `uv run pytest -q` | PASS: 566 passed, 3 skipped in 123.92s. |
| Static typing | `uv run mypy` | PASS: no issues in 196 source files. |
| Lint | `uv run ruff check` | PASS. |

| Documentation fingerprints | `uv run python scripts/docs_freshness.py check` | PASS: 54 pages fresh. |
| Built core without WebSocket extra | build core wheel, install into a clean venv, run isolated import and inspect available modules | PASS: `eazy_sdk` imports, `zapros.websocket` is not loaded and `wsproto` is absent. |
| Built core with WebSocket extra | install the same wheel with `[websocket]` into a second clean venv and import `wsproto` plus `zapros.websocket` | PASS. |

### WS-01 verification evidence

- `eazy_sdk._internal.kernel` owns protocol-neutral values, patches, operation identity/metadata,
  call state, scopes, graph compilation, parse outcomes and case arbitration. Its architecture test
  rejects imports from the HTTP and WebSocket runtimes.
- HTTP-only location/wire metadata lives in `http.py` and `CompiledContract`. HTTP declarations,
  plans and compiler code live in `http_operation.py`, `http_plan.py` and `http_compiler.py`.
- The old internal module and symbol names were removed. No aliases or forwarding modules remain.
- The nominal `CompilerKind` parameter makes `CompilerRegistry[HttpDeclaration, ...]` and
  `CompilerRegistry[WsDeclaration, ...]` incompatible under strict mypy. Runtime compilation also
  rejects a registry with the wrong kind.
- The first full post-migration pytest run failed with 8 OpenAPI failures because a mechanical
  source rewrite changed the generated import string from `.models` to `.kernels`. The generator
  string was corrected, the 33-test OpenAPI suite passed, and the full suite then passed.

| Gate | Command | Result |
|---|---|---|
| WS-01 focused kernel/architecture/typing | `uv run pytest -q tests/websocket/test_ws01_common_kernel.py` | PASS: 7 passed in 1.01s, including an isolated strict-mypy negative fixture. |
| HTTP migration characterization | phase 01–08 plus phase 17–18 rewrite suites | PASS: 90 passed in 2.13s. |
| OpenAPI regression rerun | `uv run pytest -q plugins/openapi/tests/test_rewrite_generator.py plugins/openapi/tests/test_real_world_schemas.py` | PASS: 33 passed in 13.23s. |
| Core configured suite | `uv run pytest -q` | PASS: 573 passed, 3 skipped in 130.04s. |
| Static typing | `uv run mypy` | PASS: no issues in 198 source files. |
| Lint | `uv run ruff check` | PASS. |
| Old internal API/module absence | `rg` for old request-kernel symbols, old `_internal` module paths and accidental `.kernels` generated imports | PASS: no forbidden source matches. |
| Documentation fingerprints | update 7 affected API fingerprints after middleware terminology review, then `uv run python scripts/docs_freshness.py check` | PASS: 54 pages fresh. |
| Lock consistency | `uv lock --check` | PASS: 133 packages resolved. |
| Changed-file whitespace | `git diff --check -- ...` over phase implementation/docs paths | PASS; only Windows LF-to-CRLF notices. |

### WS-02 verification evidence

- `eazy_sdk.websocket` now exports immutable message/frame artifacts, the `WsCodec` and
  `WsProtocol` contracts, deterministic `JsonTextCodec`, frame limits and explicit
  `JsonEventProtocol` configuration. Future runtime names are not stubbed.
- `FrozenArray` and `FrozenObject` preserve the empty-array/empty-object distinction. Object keys
  are canonicalized before deterministic JSON encoding.
- Zapros text, binary, ping, pong and close messages normalize without access to SDK operation
  declarations. Encoded application frames convert back to Zapros text/binary messages.
- Frame and codec limits measure UTF-8 bytes before JSON decoding or model validation.
- Protocol inspection returns distinct control, application message, reply and event values;
  binary/unknown frames return `NoMatch`, while recognized malformed envelopes return `Malformed`.
- Static architecture assertions confirm protocol code does not call `send`/`recv`, codecs contain
  no pending/reconnect state, and the fake Zapros boundary imports no SDK protocol schema.

| Gate | Command | Result |
|---|---|---|
| WS-02 frame/codec/protocol behavior | `uv run pytest -q tests/websocket/test_ws02_protocols.py` | PASS: 13 passed in 0.38s. |
| WebSocket aggregate | `uv run pytest -q tests/websocket` | PASS: 26 passed in 2.13s. |
| Core configured suite | `uv run pytest -q` | PASS: 586 passed, 3 skipped in 121.23s. |
| Static typing | `uv run mypy` | PASS: no issues in 205 source files. |
| Lint | `uv run ruff check` | PASS. |

### WS-03 verification evidence

- `AsyncWsClient` owns the explicit `IDLE -> CONNECTING -> HANDSHAKING -> READY ->
  RECONNECTING/CLOSING/CLOSED/FAILED` session states without adding WebSocket branches to the HTTP
  executor. `AsyncWsApi` and `@ws.send`/`@ws.call` lower into this same runtime.
- Every connection generation owns one reader task and one serialized writer task. The writer
  queue is bounded and fails closed on overflow; the pending registry key combines connection
  generation, protocol namespace and correlation identity.
- A pending call is registered before enqueue. Terminal reply, timeout, cancellation, disconnect
  and graceful close remove it exactly once. Active and queued writes receive distinct
  may-have-been-sent evidence, and closing the client resolves all waiters before task teardown.
- `NeverReplay`, `ReplayIfUnsent` and `ReplayWithDeduplication` keep uncertain delivery separate
  from reconnect. A Zapros exception after `send()` begins is `DeliveryUnknownError`; only an
  explicit deduplication proof can replay it. Each permitted replay rebuilds the semantic envelope,
  encoding and correlation on a fresh attempt/generation where reconnect is required.
- Out-of-order parallel replies route only to their own exchange. Old-generation frames cannot
  resolve current pending calls. Exceptions from the user message handler are isolated from the
  sole reader task.
- The first focused run was intentionally red: collection failed because `AsyncWsApi` did not yet
  exist. No PASS was claimed until the session runtime and facade were implemented.

| Gate | Command | Result |
|---|---|---|
| WS-03 initial regression run | `uv run pytest -q tests/websocket/test_ws03_runtime.py` | EXPECTED FAIL: collection stopped with `ImportError: cannot import name 'AsyncWsApi'`; this established the pre-implementation red test. |
| WS-03 one-shot/session behavior | `uv run pytest -q tests/websocket/test_ws03_runtime.py` | PASS: 10 passed in 0.46s. |
| WebSocket aggregate | `uv run pytest -q tests/websocket` | PASS: 36 passed in 2.26s. |
| HTTP isolation regression | `uv run pytest -q tests/rewrite tests/integration/test_http_client.py` | PASS: 147 passed in 7.92s. |
| Core configured suite | `uv run pytest -q` | PASS: 596 passed, 3 skipped in 120.46s. |
| Static typing | `uv run mypy` | PASS: no issues in 209 source files. |
| Lint | `uv run ruff check` | PASS. |
| Documentation fingerprints | `uv run python scripts/docs_freshness.py check` | PASS: 54 pages fresh. |
| Lock consistency | `uv lock --check` | PASS: 133 packages resolved. |
| Changed-file whitespace | `git diff --check -- eazy_sdk/websocket tests/websocket docs/implementation/STATUS.md docs/implementation/19-websocket-runtime.md` | PASS; only the existing Windows LF-to-CRLF notice for `STATUS.md` was reported. |

### WS-04 verification evidence

- `Subscription[T]` is a bounded async iterator and idempotent async context manager. It owns an
  independent state machine and publishes `Event[T]` with connection generation, recovered flag,
  last recovery position and overflow-gap metadata; it never reads from or writes to the socket.
- Reconnect, one-shot replay, resubscribe and recovery are separate policies. A transient close
  fails pending calls immediately, while only retained subscriptions enter the bounded reconnect
  supervisor. Fatal protocol/auth close codes never start reconnect.
- `ResubscribeFromStart` emits the original semantic subscription on the new generation and does
  not claim recovery. `RecoverBySequence` and `RecoverByToken` use the protocol recovery builder;
  sequence discontinuity terminates only the affected subscription with structured
  `RecoveryGapError` evidence.
- Per-subscription overflow is non-blocking. `FAIL`, `DROP_OLDEST` and `DROP_NEWEST` never await a
  slow consumer in the reader task; overflow or a user-handler exception does not block unrelated
  replies, subscriptions or heartbeat processing.
- The heartbeat supervisor sends through the serialized writer and requires a bounded ACK. Missing
  ACK follows the same transient-close/reconnect budget, while graceful client close owns and joins
  reader, writer, heartbeat, reconnect and user-handler tasks.
- `@ws.subscribe` lowers to the same session registry as direct `client.subscribe()` and preserves
  its typed `Subscription[T]` return without a second reader or reconnect loop.
- The first focused run was intentionally red: collection failed because `Event` and the WS-04
  subscription surface did not exist. No PASS was claimed until the runtime was implemented.

| Gate | Command | Result |
|---|---|---|
| WS-04 initial regression run | `uv run pytest -q tests/websocket/test_ws04_subscriptions.py` | EXPECTED FAIL: collection stopped with `ImportError: cannot import name 'Event'`; this established the pre-implementation red test. |
| WS-04 subscription/reconnect behavior | `uv run pytest -q tests/websocket/test_ws04_subscriptions.py` | PASS: 10 passed in 0.49s. |
| WebSocket aggregate | `uv run pytest -q tests/websocket` | PASS: 46 passed in 2.42s. |
| HTTP isolation regression | `uv run pytest -q tests/rewrite tests/integration/test_http_client.py` | PASS: 147 passed in 8.15s. |
| Core configured suite | `uv run pytest -q` | PASS: 606 passed, 3 skipped in 122.50s. |
| Static typing | `uv run mypy` | PASS: no issues in 211 source files. |
| Lint | `uv run ruff check` | PASS. |

### WS-05 verification evidence

- `JsonPayload` validates/dumps outbound dataclass, Pydantic, msgspec and primitive values through
  the common `ModelAdapterRegistry`. `Replies` and `Messages` route by discriminator and reuse
  common case arbitration; recognized invalid payloads remain `Malformed`, documented error
  replies retain their typed payload and subscriptions publish typed models.
- Semantic protection operates on immutable `PreparedMessage`; exact-frame protection operates on
  immutable `EncodedFrame`. Ordered inbound frame unprotection runs before protocol decode, and
  inbound semantic unprotection runs before reply/message model validation.
- Timestamp, nonce, per-message auth, HMAC-SHA256 projection and custom signer outputs occupy
  unique declared `MessageReservedOutput` paths. Duplicate writers and semantic mutation after a
  signature are rejected before emit. Custom signers receive only the immutable prepared artifact
  and return one declared output value.
- Every permitted one-shot replay rebuilds payload validation, semantic protectors, encoding and
  exact-frame protectors; the regression suite observes fresh nonce, correlation and signature.
- `SecretBytes`, `SecretText`, prepared/encoded artifacts and `ProtectionSnapshot` use redacted or
  hash/length-only representations. Runtime errors identify operation/protector state without
  including payload, auth value, key or signature bytes.
- Core exposes only protector protocols for arbitrary reversible/encryption integrations; no
  built-in encryption algorithm was added.
- The first focused run was intentionally red: collection failed because
  `CustomMessageSignature` and the WS-05 surface did not exist. No PASS was claimed until schemas
  and protection stages were implemented.

| Gate | Command | Result |
|---|---|---|
| WS-05 initial regression run | `uv run pytest -q tests/websocket/test_ws05_schemas_protection.py` | EXPECTED FAIL: collection stopped with `ImportError: cannot import name 'CustomMessageSignature'`; this established the pre-implementation red test. |
| WS-05 schema/protection behavior | `uv run pytest -q tests/websocket/test_ws05_schemas_protection.py` | PASS: 10 passed in 0.54s. |
| WebSocket aggregate | `uv run pytest -q tests/websocket` | PASS: 56 passed in 2.76s. |
| HTTP isolation regression | `uv run pytest -q tests/rewrite tests/integration/test_http_client.py` | PASS: 147 passed in 8.12s. |
| Core configured suite | `uv run pytest -q` | PASS: 616 passed, 3 skipped in 123.77s. |
| Static typing | `uv run mypy` | PASS: no issues in 214 source files. |
| Lint | `uv run ruff check` | PASS. |

### WS-06 verification evidence

- Static upgrade credentials are frozen into a Zapros client header configuration and have a
  redacted representation. Protocol authentication is a serialized handshake message sent through
  the sole bounded writer before the runtime becomes ready; its provider is resolved again for
  every connection generation and precedes subscription restoration. Dynamic per-message auth is
  resolved again for every outbound attempt, including resubscribe/recovery attempts.
- Connection, message and subscription middleware have distinct registrations and receive only an
  immutable `WsMiddlewareContext`. `WsScope` matches operation, endpoint, protocol, channel, event
  and direction. Middleware can only return typed continue/reject/message-patch values; its module
  contains no connect/send/recv/reconnect call and its context exposes no client, socket, scheduler,
  queue or reconnect capability.
- Outbound middleware patches are applied before the compiled semantic protection chain and still
  pass through the same bounded writer. Reserved auth/protection output collisions are rejected.
  Inbound message middleware runs after exact-frame and semantic unprotection; subscription
  middleware observes subscribe, resubscribe/recover and inbound event lifetimes.
- `RuntimeComposition` keeps the HTTP runtime and lazy WebSocket runtime as separate objects while
  passing one common `ModelAdapterRegistry` to the WebSocket factory. HTTP bootstrap calls a public
  async operation first and passes only its non-empty WSS endpoint into the WS runtime; merely using
  the HTTP runtime does not construct or connect WebSocket state.
- One parameterized call behavior runs unchanged over the scripted Zapros `AsyncBaseWebSocket`, an
  in-process Zapros `AsgiHandler`, and the default `AsyncStdNetworkHandler` against a localhost
  wsproto server. Eazy SDK adds no third-party transport adapter. A valid `101` response without a
  supported Zapros stream handoff is wrapped as `WsConnectError`.
- The first focused run was intentionally red: collection failed because
  `ConnectionMiddlewareApplication` and the WS-06 surface did not exist. No PASS was claimed until
  auth, middleware, composition and the production conformance paths were implemented.

| Gate | Command | Result |
|---|---|---|
| WS-06 initial regression run | `uv run pytest -q tests/websocket/test_ws06_auth_middleware_composition.py` | EXPECTED FAIL: collection stopped with `ImportError: cannot import name 'ConnectionMiddlewareApplication'`; this established the pre-implementation red test. |
| Auth/middleware/composition and Zapros conformance | `uv run pytest -q tests/websocket/test_ws06_auth_middleware_composition.py tests/websocket/test_ws06_zapros_paths.py` | PASS: 12 passed in 0.56s. |
| WebSocket aggregate | `uv run pytest -q tests/websocket` | PASS: 68 passed in 2.68s. |
| Invalid HTTP regression invocation | `uv run pytest -q tests/test_api.py tests/test_client.py tests/test_runtime.py tests/test_auth.py tests/test_dependencies.py tests/test_middleware.py tests/test_streaming.py` | INVALID: the listed root test paths do not exist; pytest reported `no tests ran` and no PASS was claimed. Replaced by the authoritative HTTP isolation command below. |
| HTTP isolation regression | `uv run pytest -q tests/rewrite tests/integration/test_http_client.py` | PASS: 147 passed in 8.09s. |
| Core configured suite | `uv run pytest -q` | PASS: 628 passed, 3 skipped in 123.80s. |
| Static typing | `uv run mypy` | PASS: no issues in 219 source files. |
| Lint | `uv run ruff check` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS: 54 pages fresh. |
| Lock consistency | `uv lock --check` | PASS: 133 packages resolved. |
| Changed-file whitespace | `git diff --check -- eazy_sdk/websocket tests/websocket docs/implementation/STATUS.md docs/implementation/19-websocket-runtime.md` | PASS; Git emitted only the existing LF-to-CRLF warning for `STATUS.md`. |

### WS-07 verification evidence

- `GraphqlTransportWsProtocol` is a pure first-party `WsProtocol` implementation for
  `graphql-transport-ws`. Dynamic `connection_init` can require `connection_ack` before READY;
  application ping/pong uses protocol envelopes through the same protected writer. Query,
  mutation and subscription `next` messages route by generation-safe operation ID. `error` and
  `complete` are terminal, local cancellation emits `complete`, and GraphQL fatal/duplicate-ID
  close codes do not reconnect.
- The new typed workspace package `eazy-sdk-asyncapi` and `eazy-sdk-asyncapi` CLI accept AsyncAPI
  3.0 JSON and optional YAML. The IR supports `ws`/`wss` servers, channels/address parameters,
  messages, send/receive operations, Operation Reply, JSON Schema payloads, correlation locations
  and identity-preserving local refs. Unsupported bindings, formats and refs produce
  `AsyncApiDiagnosticError` with operation ID, JSON Pointer and reference chain.
- Lowering is explicit: send without reply becomes `@ws.send`, send with reply becomes `@ws.call`,
  receive creates `Event[T]`, and only `x-eazy-sdk.websocket.kind: subscribe` creates
  `@ws.subscribe`. Canonical extensions lower typed error cases, completion events,
  resubscribe/recovery, replay, signing requirements and protocol imports.
- JSON and YAML fixtures generate byte-identical Python files. Generated packages contain only
  dataclass models, protocol/constants and thin `AsyncWsApi` declarations; import, execution
  through `AsyncWsClient` and isolated strict mypy pass. Regression assertions exclude reader,
  reconnect and callback logic from generated source.
- README, installation, example, site navigation and the task-oriented WebSocket/AsyncAPI guide
  were updated from verified code/tests. The guide targets SDK implementers, leads with generation
  and execution, then documents GraphQL, auth lifetimes, lowering and failure diagnostics. Its API
  fingerprint was generated by `docs_freshness.py`, and the 70-page site build passes.
- Absence audit now rejects WebSocket core imports of wsproto/aiohttp/websockets transport
  implementations. Package audit requires the typed `eazy-sdk-asyncapi` wheel/sdist. Final core
  and generator wheels install together with websocket/yaml extras in an isolated venv and import
  without workspace source paths.
- The initial GraphQL focused run was intentionally red on missing `GraphqlOperationError`; the
  initial AsyncAPI run was intentionally red on missing `eazy_sdk_asyncapi`. No completion was
  claimed before both implementations and every final gate passed.

| Gate | Command | Result |
|---|---|---|
| GraphQL-WS initial regression run | `uv run pytest -q tests/websocket/test_ws07_graphql_ws.py` | EXPECTED FAIL: collection stopped with `ImportError: cannot import name 'GraphqlOperationError'`; this established the pre-implementation red test. |
| AsyncAPI initial regression run | `uv run pytest -q plugins/asyncapi/tests/test_asyncapi_generator.py` | EXPECTED FAIL: collection stopped with `ModuleNotFoundError: No module named 'eazy_sdk_asyncapi'`; this established the package red test. |
| GraphQL-WS protocol behavior | `uv run pytest -q tests/websocket/test_ws07_graphql_ws.py` | PASS: 7 passed in 0.45s. |
| AsyncAPI IR/generation/CLI/typing/runtime | `uv run pytest -q plugins/asyncapi/tests` | PASS: 7 passed in 1.89s. |
| WebSocket and generator aggregate | `uv run pytest -q tests/websocket plugins/asyncapi/tests` | PASS: 82 passed in 3.65s. |
| HTTP isolation regression | `uv run pytest -q tests/rewrite tests/integration/test_http_client.py` | PASS: 147 passed in 8.32s. |
| Core configured suite | `uv run pytest -q` | PASS: 642 passed, 3 skipped in 122.85s. Skips are the absent local `tests/test_html/*.html` captures, unset `EAZY_SDK_POSTGRES_DSN`, and unavailable optional `pytest_iam` auth-conformance dependency (the configured suite also excludes that marker). |
| Static typing | `uv run mypy` | PASS: no issues in 226 source files. |
| Lint | `uv run ruff check` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS: 55 pages fresh. |
| Source absence | `uv run python scripts/absence_audit.py` | PASS: removed architecture and non-Zapros WebSocket transport imports absent. |
| Docs frontmatter | from `docs-site/`: `npm run check` | PASS: documentation content and frontmatter OK. |
| Docs production build | from `docs-site/`: `npm run build` | PASS: 70 pages built; Astro emitted only its upstream markdown deprecation and missing-entry warning. |
| Workspace distributions | `uv build --all-packages --out-dir dist/release` | PASS: core, AsyncAPI, OpenAPI, presets and SQLModel wheel/sdist pairs built. |
| Distribution audit | `uv run python scripts/package_audit.py dist/release` | PASS: typed markers, Zapros boundary and legacy absence verified, including `eazy-sdk-asyncapi`. |
| Isolated wheel imports | standard-library venv at `.test-tmp/ws07-wheel`; `uv pip install --reinstall --python ... "dist/release/eazy_sdk-0.1.0-py3-none-any.whl[websocket]" "dist/release/eazy_sdk_asyncapi-0.1.0-py3-none-any.whl[yaml]"`; import smoke | PASS: final wheels installed with Zapros/wsproto/PyYAML and imported `eazy_sdk.websocket`, `eazy_sdk_asyncapi`, YAML and the CLI entry point. |
| Lock consistency | `uv lock --check` | PASS: 134 packages resolved. |
| Changed-file whitespace | scoped `git diff --check` over phase-19 code, tests, packages, docs, scripts and status files | PASS; Git emitted only LF-to-CRLF warnings for existing tracked files. |

### Post-completion public exchange WebSocket validation (2026-08-23)

The opt-in `tests/websocket/test_live_exchange_soak.py` suite exercises only unauthenticated
public market-data channels. It never loads credentials or sends trading operations. Ordinary CI
remains network-independent unless `EAZY_SDK_RUN_LIVE_WS=1` is set.

- Coinbase Advanced Trade, OKX V5 public, and Kraken Futures public connections were exercised
  independently and concurrently through the production Zapros connector.
- Each connection used two concurrent subscription writes. Explicit unsubscribe acknowledgements,
  cancellation of all three stream-owner coroutines, and three repeated connect/close cycles per
  exchange completed without leaked asyncio tasks.
- A simultaneous five-minute soak kept all clients in `READY`: Coinbase received 1,817 messages
  (1,815 data), OKX 3,775 (3,773 data), and Kraken Futures 1,009 (1,006 data). First data arrived
  in 0.460-1.348 seconds and final close handshakes completed in 0.065-0.292 seconds.
- No runtime defect, unexpected disconnect, protocol error, pending-call residue, close timeout, or
  task leak was observed. No production runtime change was required.

| Gate | Command | Result |
|---|---|---|
| Individual public streams | `$env:EAZY_SDK_RUN_LIVE_WS='1'; uv run pytest -s -vv tests/websocket/test_live_exchange_soak.py -k "stream_receives"` | PASS: 3 passed against Coinbase, OKX, and Kraken Futures. |
| Concurrent clients and writes | `$env:EAZY_SDK_RUN_LIVE_WS='1'; uv run pytest -s -vv tests/websocket/test_live_exchange_soak.py -k "clients_and_writes"` | PASS: 1 passed; three clients and six subscription writes ran concurrently. |
| Five-minute live soak | `$env:EAZY_SDK_RUN_LIVE_WS='1'; $env:EAZY_SDK_LIVE_WS_SOAK_SECONDS='300'; uv run pytest -s -vv tests/websocket/test_live_exchange_soak.py -k "survive_soak"` | PASS: 1 passed in 302.18s; 6,601 total messages and all state/close/leak checks passed. |
| Unsubscribe, owner cancellation, and churn | `$env:EAZY_SDK_RUN_LIVE_WS='1'; $env:EAZY_SDK_LIVE_WS_CHURN_ROUNDS='3'; uv run pytest -s -vv tests/websocket/test_live_exchange_soak.py -k "unsubscribe or cancelling or churn"` | PASS: 3 passed; explicit unsubscribe, cancellation teardown, and nine repeated sessions passed. |
| Consolidated non-soak live suite | `$env:EAZY_SDK_RUN_LIVE_WS='1'; uv run pytest -s -q -m "live_network and not slow" tests/websocket/test_live_exchange_soak.py` | PASS: 7 passed in 15.26s. |
| Network-independent WebSocket regression | `uv run pytest -q tests/websocket` | PASS: 75 passed, 8 opt-in live tests skipped in 5.64s. |
| Core configured suite | `uv run pytest -q` | PASS: 642 passed, 11 skipped in 125.26s; the eight new skips are the intentionally disabled live cases. |
| Static typing | `uv run mypy` | PASS: no issues in 227 source files. |
| Lint | `uv run ruff check` | PASS. |

## Baseline environment

- Python 3.14.0 on Windows 11 (`10.0.26200`).
- Baseline capture versions: HTTPX 0.28.1, Requests 2.34.2, curl_cffi 0.15.0 and
  wreq 0.12.1.
- Initial `git status --short` and `git diff --name-only` were empty; all pre-existing user work was
  therefore preserved by starting from a clean tree.

## Final verification evidence

| Gate | Command | Result |
|---|---|---|
| Core configured suite | `uv run pytest -q` | PASS — 189 passed, 1 skipped in 4.29s. The skip is `tests/test_real_html.py`: optional uncommitted `tests/test_html/*.html` real-page fixtures are absent. |
| Whole workspace aggregate | `$env:PYTHONPATH='plugins/xml'; uv run pytest -q tests plugins/sqlmodel/tests plugins/openapi/tests plugins/presets/tests plugins/xml/tests` | PASS — 215 passed, 1 skipped in 6.82s. |
| Static typing | `uv run mypy` | PASS — no issues in 140 source files. |
| Lint | `uv run ruff check` | PASS. |

| Golden/capture | `uv run pytest -q tests/rewrite/test_phase02_preparation.py tests/rewrite/test_phase03_transports.py tests/rewrite/test_phase04_signing.py` | PASS — 25 passed. curl_cffi emits a Windows Proactor compatibility warning, but both sync/async capture tests execute and pass. |
| Storage/SQLModel | `uv run pytest -q tests/storage plugins/sqlmodel/tests` | PASS — 84 passed. |
| OpenAPI/codegen | `uv run pytest -q plugins/openapi/tests` | PASS — 8 passed, including the 3.0/3.1/3.2 matrix, deterministic generation, import, execution and strict mypy. |
| Protection presets | `uv run pytest -q plugins/presets/tests` | PASS — 17 passed. |
| XML optional plugin | `$env:PYTHONPATH='plugins/xml'; uv run pytest -q plugins/xml/tests` | PASS — 1 passed. |
| Source absence | `uv run python scripts/absence_audit.py` | PASS — removed paths/symbols/imports and duplicate execution loops absent. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 6 pages fresh. |

| Docs frontmatter | from `docs-site/`: `npm run check` | PASS — frontmatter OK. |
| Docs production build | from `docs-site/`: `npm run build` | PASS — 8 pages plus regenerated `llms.txt`, `llms-small.txt` and `llms-full.txt`. Generated LLM files contain no removed API/draft markers. |
| Workspace packages | `uv build --all-packages --out-dir dist/release` | PASS — core, OpenAPI, presets and SQLModel wheel/sdist pairs built. |
| XML package | `uv build plugins/xml --out-dir dist/release` | PASS — XML wheel/sdist built. |
| Built artifacts | `uv run python scripts/package_audit.py dist/release` | PASS — all wheel/sdist pairs contain `py.typed`; core has no mandatory optional dependency and no legacy paths. |
| Core isolation | install built core wheel with `--no-deps`, then `python -I` import smoke | PASS — core imports without presets or Pydantic. |
| Core extras | isolated wheel installs for `httpx`, `requests`, `curl-cffi`, `pydantic`, `bs4`, `sqlmodel` and `all` | PASS — every declared extra and its target module imports. |
| Plugin isolation | isolated built-wheel imports for OpenAPI, SQLModel and XML | PASS. |
| Preset isolation | isolated base, `browser` and `selectolax` wheel installs | PASS — base imports without either optional backend; both extras import independently. |
| Generated consumer | generate and build `tests/fixtures/openapi_consumer`, install it from its wheel against the built core wheel, then `python -I` import | PASS — generated `PING` contract imports from the installed consumer. |

Phases 00–14 have passing evidence. The Definition of Done is complete.

## Test architecture hardening (2026-08-14)

The completed rewrite received a behaviour-focused test infrastructure audit without changing
phase state or reintroducing a second execution path.

### Added infrastructure and evidence

- Added structured `tests/unit`, `tests/integration`, `tests/properties` and `tests/regression`
  suites while preserving the phase acceptance and storage suites.
- Added a deterministic stdlib localhost HTTP server covering all supported methods, exact body
  delivery, query/header/cookie round trips, repeated response headers, redirects, timeouts,
  disconnects and stream delivery without public Internet access.
- Added Hypothesis invariants for query round trips/order/duplicates and header normalization.
- Added branch coverage (`fail_under = 80`), strict markers, warnings-as-errors, a 10-second
  timeout and a narrow documented `curl_cffi` Windows warning exception.
- Added `coverage`, `pytest-cov`, `pytest-timeout`, `hypothesis` and `mutmut` to the uv development
  group. Mutation testing is configured as an optional audit.
- Added `tests/README.md` with placement guidance, commands, bugfix workflow and AI test rules.

### Defects reproduced before their fixes

| Reproduction | Before fix | Resolution |
|---|---|---|
| Public request serialization matrix | `18 failed, 6 passed` | Preparation now uses the declared OpenAPI path/query/header/cookie serializers and rejects request-header control injection. |
| Localhost redirect/error suite | `5 failed, 23 passed` | 301/302/303 POST redirects rebuild as bodyless GET attempts; 307/308 preserve method/body. Transport error assertions use the public typed fields. |
| Expired stored session replacement | `1 failed, 6 passed` | New sessions advance from the stored revision instead of attempting revision 1. |

Additional auth tests now reject empty bearer/API-key/cookie credentials and malformed Basic
credential tuples before transport. `wrap_httpx` overloads preserve sync/async client typing.

### Verification

| Gate | Command | Result |
|---|---|---|
| Configured suite | `uv run pytest -q` | PASS — 271 passed, 1 skipped in 18.70s. The existing skip is the optional uncommitted `tests/test_html/*.html` real-page fixture suite. |
| Workspace aggregate | `$env:PYTHONPATH='plugins/xml'; uv run pytest -q tests plugins/sqlmodel/tests plugins/openapi/tests plugins/presets/tests plugins/xml/tests` | PASS — 297 passed, 1 skipped in 19.37s. |
| Coverage | `uv run pytest -q --cov=eazy_sdk --cov-branch --cov-report=term-missing` | PASS — 87.87% lines, 67.70% branches, 83.86% combined; threshold 80%. |
| Static typing | `uv run mypy` | PASS — no issues in 151 source files. |
| Lint | `uv run ruff check` | PASS. |
| Source absence | `uv run python scripts/absence_audit.py` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 6 pages fresh. |
| Docs frontmatter | from `docs-site/`: `npm run check` | PASS. |
| Docs build | from `docs-site/`: `npm run build` | PASS — 8 pages. Astro reports its existing `markdown.gfm`/`markdown.smartypants` deprecation notice. |
| Mutation runner | `uv run mutmut --help` | BLOCKED on native Windows by mutmut 3.7.0's explicit WSL-only restriction; configuration and WSL/Linux commands are documented, but a full mutation campaign was not run because it is an optional, long-running audit. |

### Remaining high-value gaps

- Branch coverage is materially lower than line coverage; the largest behavioural concentration is
  executor arbitration/error branches (transport decisions, middleware failures, budget edges and
  nested protection actions).
- The less common OpenAPI parameter style/error combinations and full-querystring model path still
  have useful uncovered branches despite the representative matrix and properties.
- Requests and curl_cffi public factory lifecycle/type-error paths have less coverage than HTTPX;
  first-hop fidelity remains covered by the phase-03 suite.
- Managed persistence of response cookies is not exposed as a core client contract; tests protect
  manual request cookies and lossless multiple `Set-Cookie` response lines only.

## Real-world OpenAPI generation audit (2026-08-14)

Phase 09 remains complete for its documented supported subset. The audit makes the boundary of that
subset explicit; Eazy SDK does not claim complete OpenAPI 3.x support.

### Corpus and generated gates

- Vendored byte-pinned, licensed copies of Redocly Museum OpenAPI 3.1.0 at
  `2770b2b2e59832d245c7b0eb0badf6568d7efb53` and Swagger Petstore OpenAPI 3.0.4 at
  `8f0dd286987880b4af7bce552aca3813166f3049`.
- Every outbound Path Item operation is lowered: Museum 8/8 and Petstore 19/19.
- Committed full generated-package snapshots cover models, contracts, auth, sync/async clients and
  a machine-readable `openapi-compatibility.json`.
- Snapshot tests regenerate both packages, compare every emitted text file, import and execute the
  SDKs through HTTPX mock transport, and run strict mypy over each generated package.
- `scripts/update_openapi_snapshots.py` verifies fixture checksums before an intentional snapshot
  update. Two consecutive generations were byte-identical.

### Explicit compatibility boundary

- Museum reports two non-full-support items: PNG is returned as raw bytes and its inbound webhook
  is outside the outbound client generator.
- Petstore reports 18 items: OAuth2 token acquisition/refresh/scopes remain application concerns,
  one deterministic JSON request representation is generated where XML/form alternatives exist,
  XML responses are raw bytes, and typed response-header extraction is not emitted.
- OAuth2/OpenID Connect bearer injection, arbitrary binary response media and deterministic
  multi-content selection are now supported without silently pretending that omitted semantics were
  generated.
- External `$ref`, inbound callbacks/webhooks, multiple server selection, full XML model decoding
  and complete JSON Schema/OpenAPI vocabulary remain outside the supported executable subset.

### Defects exposed by the real schemas

| Defect | Resolution |
|---|---|
| A schema named `Error` shadowed the runtime response `Error` descriptor and broke imports. | All generated model references use a collision-safe `_models` namespace. |
| `dict[str, Any]` was tokenized as a fictitious model named `str,`, producing invalid source. | Model token collection now normalizes commas. |
| Optional request bodies emitted a dynamic `**dict[str, object]` call rejected by strict mypy. | Generated methods use explicit omitted/present body branches. |
| Pydantic constrained-string annotations failed strict mypy and email models failed at import. | Model generation uses `Annotated`; the plugin declares `pydantic[email]`. |
| Generated files used platform newline translation. | All generator-owned text is normalized to LF before snapshotting. |

### Verification

| Gate | Command | Result |
|---|---|---|
| Real-world focused suite | `uv run pytest -q plugins/openapi/tests/test_real_world_schemas.py` | PASS — 6 passed. |
| Complete OpenAPI suite | `uv run pytest -q plugins/openapi/tests` | PASS — 14 passed. |
| Snapshot regeneration | `uv run python scripts/update_openapi_snapshots.py` plus SHA-256 before/after comparison | PASS — byte-identical. |
| Configured suite | `uv run pytest -q` | PASS — 285 passed, 1 skipped. |
| Branch coverage | `uv run pytest -q --cov --cov-branch --cov-report=term` | PASS — 87.67% lines, 68.65% branches, 83.54% combined; threshold 80%. |
| Static typing | `uv run mypy` | PASS — no issues in 154 source files. |
| Lint | `uv run ruff check` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 6 pages fresh. |
| OpenAPI package build | `uv build plugins/openapi --out-dir <temporary>` | PASS — wheel and sdist built. |

### Next highest-value OpenAPI tests

- external and recursive multi-document `$ref` resolution with identity-preserving diagnostics;
- explicit media-selection API so XML/form request alternatives are callable rather than reported
  as omitted;
- typed response headers/cookies, including repeated values and links;
- OAuth2 scopes plus generated acquisition/refresh integration with the session runtime;
- callbacks/webhooks as a separate inbound-server target, if that becomes product scope.

## Generated SDK readability and router refinement (2026-08-14)

The real-schema review exposed consumer-facing generated source that was type-correct but difficult
to read. The generator now:

- emits each success/error response case on its own line inside `Responses`;
- imports models directly and uses readable aliases only for real collisions, for example
  `Error as ErrorModel`, instead of a blanket private `_models` namespace;
- groups methods by the first OpenAPI tag into typed sync/async routers
  (`api.tickets`, `api.events`, `api.pet`, and so on); untagged operations use
  `api.default`;
- formats generated method signatures vertically;
- preserves the direct model-returning method and the metadata form
  `ResponseEnvelope[Model, Raw]` without duplicating execution paths.

A strict generated-consumer sample now proves both
`await api.tickets.buyMuseumTickets(...)` and
`(await api.tickets.buyMuseumTickets_with_response(...)).value` have the static type
`MuseumTicketsConfirmation`.

| Gate | Command | Result |
|---|---|---|
| OpenAPI suite | `uv run pytest -q plugins/openapi/tests` | PASS — 15 passed. |
| Snapshot regeneration | `uv run python scripts/update_openapi_snapshots.py` plus SHA-256 before/after comparison | PASS — byte-identical. |
| Configured suite | `uv run pytest -q` | PASS — 286 passed, 1 skipped. |
| Branch coverage | `uv run pytest -q --cov --cov-branch --cov-report=term` | PASS — 87.73% lines, 68.86% branches, 83.63% combined; threshold 80%. |
| Static typing | `uv run mypy` | PASS — no issues in 154 source files. |
| Lint | `uv run ruff check` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 6 pages fresh. |

## Phase 12: typed request inputs (2026-08-14)

State: complete.

### Delivered contract

- Every operation declares one `TypedDict` request input. `Required`, `NotRequired` and the field
  annotation are the only source of type/requiredness facts.
- Public placement markers are `Path`, `Query`, `QueryString`, `Header` and `Cookie`. Flat bodies
  use `JsonField`, `Form` and `Part`; root documents use the body codecs. Removed long names are
  not aliases and are protected by the absence gate.
- `EndpointContract[Input, Output].bind(input)` validates before providers/network and produces an
  immutable `EndpointCall[Output]`. Sync/async clients accept only the bound call plus options.
- Flat JSON/form/multipart inputs compile to independent identity slots and are assembled only at
  preparation, preserving omission versus `None`, ordering, repeated values, multipart metadata,
  patches, dependency targets, signing and replay preparation.
- OpenAPI packages emit `inputs.py`; every endpoint contract is a private stable router class
  attribute immediately above its methods, so it stays out of consumer IDE completion and generated
  packages no longer need `contracts.py`. Tag routers expose `**request: Unpack[Input]` and call
  `self._CONTRACT.bind(request)`. Shared/nested or
  constrained documents stay Pydantic root bodies; safe inline leaf-only objects become kwargs.
- Generated contracts omit values equal to defaults, preserve tags as tuples, and keep contract
  generic declarations and thin executor returns on one line while response variants stay split.
- Deterministic normalization handles keywords, digits, punctuation, Unicode, reserved router
  names and cross-location collisions while retaining original wire names in metadata.

### Defects exposed during implementation

| Defect | Resolution |
|---|---|
| Unicode regex cleanup accepted `¼`, although it is not a valid Python identifier character. | Normalization now checks Python identifier start/continuation semantics; Hypothesis protects uniqueness and validity. |
| Enum literals became generated model imports such as `'available'`. | Literal expressions and model-name collection are separated. |
| Descriptor constructors could repeat and contradict input type/requiredness. | Type/required flags were removed; serialization metadata is keyword-only. |

### Verification

| Gate | Command | Result |
|---|---|---|
| Focused typed inputs/body | `uv run pytest -q tests/unit/test_typed_request_inputs.py tests/unit/test_request_serialization.py tests/unit/test_flat_request_bodies.py tests/rewrite/test_phase02_preparation.py plugins/xml/tests/test_xml_codec.py` | PASS — 59 passed. |
| OpenAPI + normalization property | `$env:PYTHONPATH='plugins/openapi'; uv run pytest -q plugins/openapi/tests tests/properties/test_openapi_input_names.py` | PASS — real-schema generation, import, runtime, strict-mypy and private colocated contract checks pass. |
| Configured suite | `uv run pytest -q` | PASS — 326 passed, 1 skipped in 17.19s. |
| Branch coverage | `uv run pytest -q --cov=eazy_sdk --cov-branch --cov-report=term-missing` | PASS — 88.78% statements, 69.60% branches, 84.94% combined; threshold 80%. |
| Static typing | `uv run mypy` | PASS — no issues in 160 source files; generated packages and positive/negative consumers run strict mypy. |
| Lint | `uv run ruff check` | PASS. |
| Source absence | `uv run python scripts/absence_audit.py` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 6 pages fresh. |
| Docs site | from `docs-site/`: `npm run check`; `npm run build` | PASS — frontmatter valid and 8 pages built; Astro emits the existing markdown deprecation notice. |
| Real schemas/snapshots | snapshot updater plus `uv run pytest -q plugins/openapi/tests` | PASS — 25 passed; pinned Museum 8/8 and Petstore 19/19 regenerate without `contracts.py`, import, execute, type-check and match committed snapshots including `inputs.py`. |
| Package build/audit | workspace and XML builds plus `scripts/package_audit.py` in a fresh temp directory | PASS — every wheel/sdist pair built and audited. |
| Isolated wheel consumer | fresh venv, installed core/OpenAPI wheels, Museum generation and import | PASS — `AsyncOperations._GET_MUSEUM_HOURS.input is GetMuseumHoursInput`. |

## Phase 13: localhost auth and OAuth/OIDC conformance (2026-08-14)

State: complete.

### Delivered contract

- `pytest-httpserver` owns deterministic real-socket auth scenarios; no test uses a public host,
  Docker, wall-clock sleeps, or production credentials.
- The injected `ClientHarness` runs identical contracts and assertions through HTTPX sync/async,
  Requests, curl_cffi sync/async and wreq sync/async. Basic, Bearer/JWT placement, API-key
  header/query, cookie, OR-of-AND security, Bearer refresh, and cookie rotation have adapter-wide
  coverage.
- `SessionProvider.refresh_execution()` refreshes the selected execution under the existing
  singleflight lock, compares revisions, reuses a valid newer concurrent revision, validates the
  refreshed value, and commits only after success.
- Concurrent localhost coverage now distinguishes same-operation and different-operation login
  races (12 callers, one login), and exercises 12 simultaneous logical calls through
  `401 -> refresh -> replay` (one refresh, all callers succeed on revision 2). Both scenarios run
  through the HTTPX and curl_cffi async adapters.
- Credentials-versus-initial-session resolution is parameterized over all seven adapters: credentials
  acquire once, while an already valid passed session performs no login/acquire request.
- `CallOptions.auth_retries` is a separate non-negative budget. A refresh consumes both it and the
  hard attempt budget, creates a fresh attempt through the shared coordinator, and refuses unsafe
  non-idempotent replay. Static auth, `403`, `500`, exhausted budgets, and refresh failures remain
  terminal without hidden loops.
- Auth/session values are excluded from provider/store/config `repr`. The observer receives a
  method/path/body-length summary rather than credentials, query secrets, headers, or body bytes.
- The optional `auth-conformance` dependency group runs a real Canaille issuer supplied by
  `pytest-iam`: OIDC discovery, client credentials, `invalid_client`, authorization code, signed
  JWT, userinfo, refresh, scopes and expiry metadata are exercised on all seven adapters.
- The optional stack pins `pytest-iam==0.2.5`, `canaille[oidc]==0.0.87`, and `joserfc==1.4.0` as a
  compatible set. Conformance is excluded from default pytest because Canaille owns process-global
  logging; `-m auth_conformance` is its explicit isolated gate.

### Defects reproduced before their fixes

| Reproduction | Before fix | Resolution |
|---|---|---|
| Selected Bearer session receives `401 invalid_token` | `CallOptions.__init__()` rejected `auth_retries`; no response-driven refresh path existed. | Executor retains selected `AuthExecution`, invokes revision-safe refresh, and re-enters its single attempt loop. |
| Concurrent refresh of one selected revision | Refresh was outside the provider lock and could overwrite a newer session. | Revision-aware singleflight returns the already-refreshed valid session to the loser. |
| Auth observer diagnostics | The observer received the complete prepared request, including secret headers/query/body. | Prepared telemetry is a redacted summary and secret-bearing dataclass fields are hidden from `repr`. |
| IAM conformance inside default suite | Canaille reconfigured global logging and four later storage logging tests failed. | The heavyweight marker is excluded by default and runs as a dedicated dependency-group gate. |

### Verification

| Gate | Command | Result |
|---|---|---|
| Focused concurrent auth | `uv run pytest -q tests/integration/auth/test_session_auth.py -k "concurrent_credentials or concurrent_401"` | PASS — 6 passed across HTTPX async and curl_cffi async. |
| Adapter auth matrix | Covered by the configured suite. | PASS — every credentials/session and Bearer/cookie refresh case passed across HTTPX sync/async, Requests, curl_cffi sync/async and wreq sync/async. |
| OAuth/OIDC conformance | `uv run --group auth-conformance pytest -p no:cacheprovider --basetemp .auth-tmp -q -m auth_conformance tests/conformance/auth` | PASS — 21 passed, the same three protocol scenarios on all seven adapters. |
| Generated real schemas | `uv run pytest -q plugins/openapi/tests/test_real_world_schemas.py` | PASS — 8 passed, including generated Museum Basic and Petstore OAuth2/Bearer execution. |
| Configured suite | `uv run pytest -p no:cacheprovider --basetemp .test-tmp -q` | PASS — 419 passed, 1 skipped and 21 optional conformance cases deselected in 103.69s. |
| Branch coverage | `uv run pytest -q --cov --cov-branch --cov-report=term` | PASS — 88.66% statements, 70.75% branches, 84.68% combined; threshold 80%. |
| Static typing | `uv run mypy` | PASS — no issues in 165 source files after the concurrent session additions. |
| Lint | `uv run ruff check` | PASS. |
| Lockfile | `uv lock --check` | PASS — 124 packages resolved. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 6 pages fresh. |

### Remaining high-value auth tests

- map a `WWW-Authenticate` challenge when one AND-alternative selects multiple refreshable session
  schemes instead of rejecting the ambiguous refresh;
- exercise explicit idempotency proof with a replayable POST body and reject a non-replayable body;
- add OAuth token revocation/introspection and `insufficient_scope` resource responses;
- add cancellation during procedural acquire/refresh and assert lock/store cleanup;
- execute a generated OAuth session acquirer when OpenAPI generation gains lifecycle ownership,
  rather than only generated Bearer placement.

## User-facing documentation expansion (2026-08-14)

State: complete.

### Delivered

- Replaced the architecture-first navigation with a usage path: quickstart, client adapters,
  static auth, session login/refresh, response variants, HTML parsing and request signing.
- Added a reusable responsive request/response component. Every worked protocol example shows
  method, URL, status, relevant headers and body in compact JSON; desktop uses side-by-side panels.
- Added HTTPX sync/async, Requests and curl_cffi sync/async client examples.
- Added Basic, Bearer/JWT, API-key header/query, cookie, OR-of-AND and session auth examples.
- Added login success, invalid-password and user-not-found variants, including the same-status HTML
  form with one cached BeautifulSoup document and explicit `NoMatch`/`Malformed` behavior.
- Added a byte-reproducible HMAC-SHA256 example. An HTTPX MockTransport check confirmed the shown
  35-byte JSON body and `X-Signature` value match the emitted request.
- Preserved the existing architecture page but removed it from the primary learning navigation;
  the new documentation does not teach internal execution architecture.

### Verification

| Gate | Command | Result |
|---|---|---|
| Runtime example smoke | inline `uv run python` HTTPX MockTransport scripts | PASS — session login applies `Bearer access-v1`; HMAC body, length and signature match the wire request. |
| Configured suite | `uv run pytest -q` | PASS — 401 passed, 1 skipped, 15 deselected in 101.14s. |
| Static typing | `uv run mypy` | PASS — no issues in 165 source files. |
| Lint | `uv run ruff check` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 12 pages fresh. |
| Docs frontmatter | from `docs-site/`: `npm run check` | PASS — frontmatter OK. |
| Docs production build | from `docs-site/`: `npm run build` | PASS — 14 pages built; the existing Astro markdown deprecation notice and `Entry docs → 404 was not found` notice remain non-fatal. |

### Remaining work / blockers

None for the requested top-level usage documentation. Internal architecture documentation was
intentionally left outside the new sidebar path.

## Documentation maps, guides and API reference (2026-08-14)

State: complete.

### Delivered

- Added Taskito-style card maps at `/guides/` and `/api-reference/`, plus grouped sidebar
  navigation that keeps architecture internals outside the primary learning path.
- Added a complete installation page for `pip` and `uv`, core extras, OpenAPI, presets, SQLModel
  and XML packages.
- Added practical guides for typed request inputs/bodies, dependencies, middleware, reliability,
  every Cloudflare/reCAPTCHA preset, OpenAPI generation and optional plugins.
- Added a compact public API reference for contracts/clients, request, response, auth/session,
  dependencies/middleware, protection/signing and plugins/OpenAPI. Compiler-only types are
  intentionally excluded.
- Added `examples/docs/local_showcase.py`: a network-free MockTransport example that captures a
  typed GET, Bearer request and login responses for success, wrong password and missing user.
- Added a regression test that executes the docs showcase and asserts statuses and wire auth.
- Added installation, selection guidance, sync/async examples and public API references for the
  completed `wreq` adapter.

### Verification

| Gate | Command | Result |
|---|---|---|
| Docs example | `uv run python examples/docs/local_showcase.py` | PASS — captured five exchanges with statuses `200, 200, 200, 401, 404`; Bearer header present. |
| Docs example test | `uv run pytest -q tests/unit/test_docs_examples.py` | PASS — 1 passed. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 26 pages fresh. |
| Docs frontmatter | from `docs-site/`: `npm run check` | PASS. |
| Docs production build | from `docs-site/`: `npm run build` | PASS — 31 pages built. Existing Astro deprecation and `Entry docs → 404 was not found` notices remain non-fatal. |
| Internal route audit | inspect all built `href="/..."` targets | PASS — 24 internal routes resolve. |
| Static typing | `uv run mypy` | PASS — no issues in 167 source files. |
| Changed-file lint | `uv run ruff check examples/docs/local_showcase.py tests/unit/test_docs_examples.py` | PASS. |
| Full lint | `uv run ruff check` | PASS. |
| Configured suite | `uv run pytest -p no:cacheprovider --basetemp .test-tmp -q` | PASS — 419 passed, 1 skipped and 21 optional conformance cases deselected. |
| Browser visual QA | local in-app browser discovery | BLOCKED — no browser instance was available; Astro build and generated HTML structure were verified instead. |

The docs dev server was started at `http://127.0.0.1:4321/` for user review and intentionally left
running.

## wreq client adapter increment (2026-08-14)

State: complete.

### Delivered

- Added prepared-only sync and async adapters for `wreq==0.12.1`, exposed through lazy adapter
  imports and the public `wrap_wreq(...)` factory.
- Preserved request-target bytes, header order/casing/duplicates, cookie placement and exact body
  bytes. Disabled redirects, implicit default headers and automatic compression negotiation; forced
  HTTP/1.1 because the current prepared-request protocol does not model HTTP/2 pseudo-headers.
- Added response normalization for status, URL, repeated headers, body, history and raw response.
  Unsupported per-request TLS verification is rejected at preflight because wreq configures it on
  the raw client.
- Added the `wreq` optional extra and included it in `all` and development dependencies. Capture
  guarantees are version-tied to 0.12.1 and downgrade to best-effort for unverified versions.
- Extended focused transport tests, the public factory/executor/auth matrix, OAuth/OIDC conformance,
  user documentation and API fingerprints for both sync and async clients.
- Hardened async client shutdown so awaitable `close()` methods are awaited when `aclose()` is not
  exposed, preserving curl_cffi async lifecycle behavior while supporting wreq.

### Verification

| Gate | Command | Result |
|---|---|---|
| Focused transports | `.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests/rewrite/test_phase03_transports.py` | PASS — 11 passed. |
| wreq auth matrix | `.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests/integration/auth/test_adapter_matrix.py -k wreq` | PASS — 14 passed, 35 deselected. |
| Configured suite | `.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .test-tmp -q` | PASS — 419 passed, 1 skipped, 21 deselected in 103.69s. Skip reason: local uncommitted `tests/test_html/*.html` real-page fixtures are absent. |
| OAuth/OIDC conformance | `uv run --group auth-conformance pytest -p no:cacheprovider --basetemp .auth-tmp -q -m auth_conformance tests/conformance/auth` | PASS — 21 passed in 46.18s. |
| Static typing | `uv run mypy` | PASS — no issues in 167 source files. |
| Lint | `uv run ruff check` | PASS. |
| Dependency lock | `uv lock --check` | PASS — 125 packages resolved. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 26 pages fresh. |
| Docs frontmatter | from `docs-site/`: `npm run check` | PASS. |
| Docs production build | from `docs-site/`: `npm run build` | PASS — 31 pages built; existing non-fatal Astro notices remain. |
| Package build | `uv build --out-dir .package-tmp` | PASS — wheel and sdist built; wheel metadata provides `wreq` and includes it in `all`. |
| Isolated wreq extra | standard-library `venv`, then `uv pip install --python .isolated-wreq\Scripts\python.exe ".package-tmp\eazy_sdk-0.1.0-py3-none-any.whl[wreq]"` and import smoke | PASS — installed Eazy SDK and wreq 0.12.1; imported `wrap_wreq`, `WreqSyncAdapter` and `WreqAsyncAdapter`. The first `uv venv` attempt was blocked by denied access to uv's global Python `.lock`; using the workspace Python's `venv` avoided that external path. |

### Remaining work / blockers

None for the wreq adapter increment.

## Session authorization guide rewrite (2026-08-15)

State: complete.

### Delivered

- Rewrote `/auth/session/` as a linear tutorial: Pydantic JSON models, login contract, a thin SDK
  method, Bearer registration, protected request, and then the optional managed-session extension.
- Kept `TypedDict` only as the endpoint placement declaration and `bind()` inside SDK methods;
  consumer examples now call `auth_api.login(...)` and `account_api.get(...)` exclusively. Router
  signatures match generated SDKs: `**request: Unpack[OperationInput]` is passed directly to
  `bind(request)`, without constructing a second mapping literal.
- Documented `200`, invalid-credentials `401`, and missing-user `404` exchanges with compact request
  and response JSON, status codes, and relevant headers, followed by typed exception handling.
- Corrected secret modeling so outbound passwords and refresh tokens serialize as actual JSON
  strings while stored access/refresh tokens remain protected by Pydantic `SecretStr`.

### Verification

| Gate | Command | Result |
|---|---|---|
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 26 pages fresh. |
| OpenAPI router generation | `uv run pytest -q plugins/openapi/tests/test_rewrite_generator.py` | PASS — 17 passed; generated routers retain `Unpack[Input]` and `bind(request)`. |
| Docs frontmatter | from `docs-site/`: `npm run check` | PASS — frontmatter OK. |
| Docs production build | from `docs-site/`: `npm run build` | PASS — 31 pages built. The first run exposed an MDX list-indentation error in the rewritten request/response component; indentation was fixed and the complete rerun passed. Existing Astro deprecation and missing `docs -> 404` notices remain non-fatal. |
| Local page smoke | `Invoke-WebRequest http://127.0.0.1:4321/auth/session/` | PASS — HTTP 200 and rendered `account_api` example present. |

The docs dev server is running at `http://127.0.0.1:4321/` and was intentionally left running for
user review.

## Annotated session API experiment (2026-08-15)

State: complete as an isolated prototype; no production API changed.

### Delivered

- Added `experiments/session_api/` with a runnable mock lifecycle and design comparison.
- The recommended candidate keeps JSON parsing in Pydantic and uses field metadata for the three
  runtime-only roles: `Bearer()`, `RefreshToken()` and `ExpiresAt(leeway=...)`.
- The common setup needs only credentials and an auth service; a generated client factory can
  provide the service automatically. Public `SessionKey`, `sdk_factory`, selector classes and
  `validate` callback are absent.
- `LoginCredentials` is a lifecycle input and is never reused as `LoginRequest`. The service owns
  construction of login/refresh endpoint request models through a scoped auth context; generated
  SDKs may generate and hide this mapping service.
- The mock covers lazy login, reuse, proactive expiry refresh, selected-session `401` refresh,
  refresh-request construction from matching annotations and concurrent first-use singleflight.
- Cookie auth is fixed as transport-owned state through `session_cookie(name)` and automatic
  `Set-Cookie` capture; it does not use `FromSetCookie`/`SessionCookie` model annotations.
- Header extraction is intentionally limited to exact `FromHeader(name)` lookup. Prefix stripping,
  value selection and other parsing remain responsibilities of a procedural auth service.
- The README separates the simple declarative flow from the procedural escape hatch required by
  multi-step CSRF/device/custom authentication and records persistence/multi-account boundaries.

### Verification

| Gate | Command | Result |
|---|---|---|
| Prototype lifecycle | `uv run pytest -q experiments/session_api` | PASS — 5 passed, including exact response-header extraction and annotation-free cookie capture/reuse/rotation. |
| Prototype typing | `uv run mypy experiments/session_api` | PASS — no issues in 3 source files. |
| Prototype lint | `uv run ruff check experiments/session_api` | PASS. |
| Configured suite | `uv run pytest -q` | PASS — 419 passed, 1 skipped and 21 optional conformance cases deselected. |
| Static typing | `uv run mypy` | PASS — no issues in 167 source files. |
| Lint | `uv run ruff check` | PASS. |

### Open decisions before production integration

- choose whether placement metadata belongs directly to a generated session model or a separate
  `session_bearer(UserSession)` declaration;
- define query-token placement and `expires_in` normalization; cookie auth deliberately has no
  equivalent model annotation;
- expose session identity only for shared/persistent stores and multi-account clients;
- compile simple endpoint flows into the existing scoped executor without adding a second execution
  path; keep procedural services for multi-step flows.

## Phase 14: public API simplification (2026-08-15)

State: complete.

### Accepted scope

- Promote exact, parser-free `FromHeader` to a general response field source.
- Integrate the session prototype into the existing runtime: Pydantic session roles, credentials
  separate from endpoint request models, annotation-free cookie sessions and procedural services
  for non-trivial flows.
- Add short static auth binding, immutable typed `ClientConfig` and a bounded user-facing
  `RetryPolicy` without adding another execution path.
- Replace broad public re-exports with explicit consumer/authoring/codegen/extension allowlists.
- Keep dependency output annotations and annotation-based control flow outside this phase.

### Planning verification

| Gate | Command | Result |
|---|---|---|
| Master-plan linkage | inspect phase graph, phase table and Definition of Done in `docs/implementation/README.md` | PASS — phase 14 is ordered after phase 13 and reopens the release gate. |
| Markdown whitespace | `git diff --check` | PASS. |

### Delivered

- Added exact case-insensitive `FromHeader` extraction to the existing JSON/Pydantic response path,
  including alias/validator support and distinct missing/repeated/malformed failures.
- Promoted the annotated session prototype to production: `Bearer`, `RefreshToken`, `ExpiresAt`,
  `session_auth`, `session_cookie`, procedural `AuthContext.call`, lazy acquire, proactive refresh,
  `401` replay and singleflight. Credentials remain separate from login request models.
- Added atomic cookie capture with attributes, expiry, rotation, deletion and rollback, without
  cookie field annotations. Added `.static(...)` for Bearer, Basic, API-key and cookie schemes.
- Added immutable `ClientConfig` and removed public `**options: Any` from all client factories.
  Added `RetryPolicy.none()` / `.safe(...)` over the existing coordinator with independent auth,
  response retry, reaction and redirect budgets and fresh preparation/signing.
- Added canonical OpenAPI `x-eazy-sdk.sessionAuth`. Generated sync/async factories own their HTTPX
  client, accept exactly one of `credentials=` or `session=`, generate the request mapping service,
  and execute login/refresh through the shared executor.
- Reduced root/subject exports to positive allowlists; advanced supported hooks live in
  `eazy_sdk.ext`. First-party plugins no longer import `eazy_sdk._internal`.
- Rewrote auth, client, retry and OpenAPI docs around the new public API and removed wreq from the
  user-facing docs as requested.

### Verification

| Gate | Command | Result |
|---|---|---|
| Focused phase/codegen | `uv run pytest -q tests/rewrite/test_phase14_public_api.py plugins/openapi/tests/test_rewrite_generator.py plugins/openapi/tests/test_real_world_schemas.py` | PASS — 52 passed. |
| Full suite | `uv run pytest -q` | PASS — 446 passed, 1 skipped, 21 optional conformance cases deselected in 103.93s. |
| Static typing | `uv run mypy` | PASS — no issues in 169 source files. |
| Lint | `uv run ruff check` | PASS. |
| Dependency lock | `uv lock --check` | PASS — 125 packages resolved. |
| Generated snapshots | `uv run python scripts/update_openapi_snapshots.py` then `uv run pytest -q plugins/openapi/tests/test_real_world_schemas.py` | PASS — snapshots regenerated; 8 passed. |
| Absence/import boundary | `uv run python scripts/absence_audit.py` | PASS — legacy paths/options, low-level exports, plugin `_internal` imports and extra execution loops absent. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 26 pages fresh. |
| Docs frontmatter | from `docs-site/`: `npm run check` | PASS. |
| Docs production build | from `docs-site/`: `npm run build` | PASS — 31 pages built; existing Astro deprecation and missing `docs -> 404` notices are non-fatal. |
| Package build | `uv build --out-dir .phase14-build` | PASS — wheel and sdist built; wheel was built from the sdist. |
| Isolated wheel/public parity | install wheel into `.phase14-venv`, import root and seven subject namespaces, compare `__all__`, assert negative low-level symbols, inspect archives | PASS — source and wheel allowlists match; phase 14 runtime files are present in both artifacts. |
| Whitespace | `git diff --check` | PASS; only configured LF/CRLF conversion warnings were emitted. |

### Remaining work / blockers

None.

## Phase 15: account registration and verification (2026-08-15)

State: complete. The user approved the specification on 2026-08-15; production implementation and
all unconditional phase exit criteria passed on the same date.

### Experiment decisions

- Registration input is split into typed `credentials`, durable `profile` and registration-only
  `details`; none of them is reused automatically as an endpoint request.
- The procedural registration service explicitly maps domain input to generated/hand-written create
  and verify request models through a scoped execution context.
- `AccountIdentifier()` is the only proposed common input annotation. It replaces a string selector
  and is checked for exactly one field before I/O. A flat per-field credential/persistence annotation
  DSL was rejected.
- A remote `AccountCreated` event commits a local account immediately. Verification-required
  accounts are stored as pending and returned as resumable `PendingRegistration` values.
- Manual proof submission and optional email/SMS providers share one bounded verification path.
  Invalid proof leaves the stored account pending; multi-step verification follows only a challenge
  returned by the server.
- Persistence uses event-sized `commit_created`/`commit_verification` operations so account,
  verification and resulting session can be atomic. Typed credentials require an explicit codec;
  automatic plaintext Pydantic serialization is forbidden.
- Remote creation followed by local storage failure is a typed reconciliation outcome and does not
  automatically repeat an unsafe create request.
- A token/cookie session produced by create or verify is handed to the existing auth/session
  lifecycle; registration does not implement placement or refresh.
- Transport independence is now an explicit phase-15 prerequisite: account/session lifecycle core
  cannot import HTTP request/response/client/adapters or browser runtimes. Existing
  `session_auth(...)`, generated client factories and annotation syntax remain the canonical HTTP
  facade over that one lifecycle state machine.
- The production gate includes a fake browser-like create/verify/resume/session-persistence suite
  with no HTTP objects, plus import/AST and isolated-install checks. Browser support will be an
  optional downstream adapter; the core remains extraction-ready without prematurely creating a
  separate distribution.

### Verification

| Gate | Command | Result |
|---|---|---|
| Prototype behavior | `uv run pytest -q experiments/account_registration` | PASS — 10 passed: separation, persistence, session handoff, manual/automatic multi-step verification, step budget, failure safety, compile-time markers and redaction. |
| Prototype typing | `uv run mypy --strict experiments/account_registration` | PASS — no issues in 3 source files. |
| Prototype lint | `uv run ruff check experiments/account_registration` | PASS. |
| Configured suite | `uv run pytest -q` | PASS — 446 passed, 1 skipped and 21 optional conformance cases deselected in 103.26s. |
| Static typing | `uv run mypy` | PASS — no issues in 169 source files. |
| Lint | `uv run ruff check` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 26 pages fresh. |
| Whitespace | `git diff --check -- docs/implementation/README.md docs/implementation/STATUS.md docs/implementation/15-account-registration-and-verification.md experiments/account_registration` | PASS; only configured LF/CRLF conversion warnings were emitted. |

### Delivered

- Added the minimal `eazy_sdk_accounts` public surface. `AccountDraft` keeps credentials, durable
  profile and registration-only details separate; exactly one `AccountIdentifier()` is compiled
  from a Pydantic credentials model before I/O.
- Extracted one stdlib-only `SessionLifecycle` for acquire/validate/refresh/store/revisions and
  singleflight. Existing `session_auth(...)` delegates to it without changing its signature,
  generated `credentials=`/`session=` factories or `Bearer`/`RefreshToken`/`ExpiresAt` syntax.
- Implemented create, complete/pending outcomes, manual and provider-driven verification, resume,
  resend, bounded multi-step/cycle/deadline/cancellation handling and same-identifier singleflight.
- Added explicit credential codecs, owned in-memory snapshots, event-sized persistence commits and
  typed reconciliation for remote success/local commit or session-adoption failure. Resume retries
  local session adoption without repeating remote create.
- Added `HttpRegistrationContext`/`SyncHttpRegistrationContext` downstream from the core and proved
  delegation through the existing executor. JSON registration uses existing signing; localhost
  create/verify proves non-idempotent retry refusal and session handoff.
- Added procedural `Set-Cookie` parsing without `SessionCookie`/`FromSetCookie` annotations. Token
  and cookie registration sessions are adopted by the existing auth runtime; opaque browser state
  converts only through an explicit `SessionBridge`.
- Added `SqlRegistrationStore` over the existing SQLModel account/verification/session tables with
  nested transaction/savepoint support, optimistic revisions and rollback evidence.
- Added a fake browser-like create/verify/resume/persist reference with no HTTP/browser objects and a
  runnable documentation example. No Playwright/Selenium dependency or optional browser package was
  added to core.
- Added junior-facing registration and account API pages, navigation cards, real JSON exchanges,
  manual/automatic verification, browser and JWT/cookie handoff examples. The recommended generated
  SDK path does not expose `bind({...})`; that syntax appears only in the advanced hand-written
  cookie example.
- The optional `x-eazy-sdk.accountRegistration` extension was not released. Its conditional
  snapshot/import/mypy/execution gate is therefore not applicable; ordinary generated endpoint
  methods remain the recommended mapping surface.

### Production verification

| Gate | Command | Result |
|---|---|---|
| Focused account/runtime/HTTP/SQL | `uv run pytest -q tests/rewrite/test_phase15_accounts.py tests/properties/test_account_registration.py tests/integration/accounts/test_http_registration.py plugins/sqlmodel/tests/test_registration_store.py` | PASS — 26 passed, including fake browser, localhost, property, concurrency, cookie/token handoff, reconciliation and SQL rollback. |
| Executable docs | `uv run pytest -q tests/unit/test_docs_examples.py` | PASS — 2 passed; the account lifecycle example runs without network. |
| Full suite | `uv run pytest -q` | PASS — 473 passed, 1 skipped and 21 optional conformance cases deselected in 106.32s. |
| Static typing | `uv run mypy` | PASS — no issues in 179 source files. |
| Lint | `uv run ruff check` | PASS. |
| Dependency lock | `uv lock --check` | PASS — 125 packages resolved. |
| Import/absence boundary | `uv run python scripts/absence_audit.py` | PASS — transport-neutral account core has no HTTP/browser imports and removed paths/loops remain absent. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 28 pages fresh. |
| Docs frontmatter/build | from `docs-site/`: `npm run check` and `npm run build` | PASS — 33 pages built; existing Astro deprecation and missing `docs -> 404` notices are non-fatal. |
| Core package | `uv build --out-dir .phase15-build` | PASS — sdist and wheel built from sdist. |
| Isolated core import | install the core wheel with `--no-deps` into `.phase15-venv`; import `eazy_sdk_accounts`; inspect loaded modules and wheel members | PASS — no Pydantic/HTTPX/Requests/curl_cffi/wreq/Playwright/Selenium module loaded; account core and HTTP downstream files packaged. |
| SQLModel package | from `plugins/sqlmodel/`: `uv build --out-dir .phase15-build`; isolated install/import of `SqlRegistrationStore` | PASS. |
| Whitespace | `git diff --check` | PASS; only configured LF/CRLF conversion warnings were emitted. |

### Remaining work / blockers

None. A concrete Playwright/Selenium package or canonical OpenAPI registration extension is future
optional downstream work and is not part of the public API released by this phase.

## Phase 16: minimal SQLModel account storage v2 (2026-08-15)

State: complete. Production implementation, migration, documentation and all unconditional exit
criteria passed on 2026-08-15.

### Accepted decisions

- Default backend contains exactly five tables: accounts, sessions, verifications, account links
  and account events. A dedicated restrictions table is not part of v2.
- Account is a universal managed identity/resource: mailbox, phone, social/API account or another
  provider-specific login entity.
- Arbitrary credentials and profile remain on the account as plaintext versioned JSON. A default
  Pydantic codec will handle common models and actual SecretStr values; encryption is optional
  future/custom behavior, while repr/log redaction remains mandatory.
- Sessions use one separate key/revision/opaque JSON persistence path shared by registration and
  auth. Verification rows preserve challenge/resend history and may reference the email/phone
  account used to receive proof.
- Directed account links distinguish owner/consumer from resource. Nullable `exclusive_scope`
  provides portable database-enforced reservation without an extra allocation table.
- Exclusive resource reservation happens before remote HTTP/browser I/O. The target local account
  is persisted as provisioning; failure is recorded, reservations are released and lifecycle
  history remains available.
- Account events are append-only audit/lifecycle records committed atomically with state changes;
  current state is not reconstructed by event sourcing.
- Phase 16 is a breaking schema replacement with an explicit v1 -> v2 migration and no dual
  read/write compatibility path.

### Planning verification

| Gate | Command | Result |
|---|---|---|
| Current-schema evidence | ephemeral SQLite metadata/insert probe | PASS — duplicate `(identifier, provider=NULL)` rows are accepted; accounts/sessions/verifications have no foreign keys, validating the redesign drivers. |
| Source-of-truth linkage | inspect phase graph/table and phase 16 document | PASS — phase 16 follows completed phase 15 and is marked pending. |

### Delivered

- Added the standalone `experiments/sqlmodel_account_v2` proof with no production imports.
- Fixed the canonical default provider key as `__default__` and the concrete default table names as
  `accounts`, `sessions`, `verifications`, `account_links` and `account_events`.
- Replaced the default SQLModel schema and repositories in one breaking path. Legacy credential
  columns, session columns, restriction model/table/repository and `meta.registration` decoding are
  absent from runtime; custom table bundles are validated before first write.
- Added `PlainPydanticCodec`, which roundtrips nested models, lists/mappings, UUID, timezone-aware
  datetime, `SecretStr` and `SecretBytes` without persisting the Pydantic redaction mask. Unsupported
  values fail before a partial write, and stored values are absent from repr/errors.
- Added database account/session CAS, non-null provider normalization, partial remote-id uniqueness
  and one opaque session path. `SqlSessionStore.save` accepts the neutral lifecycle's new revision,
  derives the previous CAS revision internally and keeps stale invalidation as a safe no-op.
- Added `AccountResource`/optional `using=`, provisioning before remote effect, atomic multi-resource
  reservation, database conflict before I/O, release/reactivation, deterministic failure/cancellation/
  deadline compensation and retry of the same failed aggregate.
- Added explicit verification columns/history (`via_account_id`, `replaces_id`, expiry/attempts),
  append-only correlated events and foreign-key deletion policies. Ambiguous remote outcomes become
  reconciliation-required state, retain reservations and cannot be retried automatically.
- Lifecycle SQL stores now own each event-sized transaction and reject explicit outer transactions;
  a second SQL session observes the reservation before the remote service starts.
- Added one-shot `inspect_v1`/`migrate_v1_to_v2` plus
  `eazy-sdk-sqlmodel-migrate URL [--apply]`. Dry-run reports normalized duplicates, active session
  conflicts, orphans and restrictions requiring application policy; a successful migration removes
  v1 tables and cannot run twice.
- Added deterministic SQLite/PostgreSQL DDL snapshots, live PostgreSQL concurrency coverage,
  migration fixtures, isolated wheel/import/CLI asset gates and user documentation for plaintext
  setup, resources, events, deletion policy and migration.

| Gate | Command | Result |
|---|---|---|
| Prototype behavior | `uv run pytest -q experiments/sqlmodel_account_v2` | PASS — 6 passed. |
| Prototype typing | `uv run mypy --strict experiments/sqlmodel_account_v2` | PASS — no issues in 3 source files. |
| Prototype lint | `uv run ruff check experiments/sqlmodel_account_v2` | PASS. |
| Focused SQL/account lifecycle | `uv run pytest -q plugins/sqlmodel/tests tests/rewrite/test_phase15_accounts.py tests/properties/test_account_registration.py tests/integration/accounts/test_http_registration.py` | PASS — 51 passed before the final additional matrix cases; final full suite includes all additions. |
| Live PostgreSQL 17 | set `EAZY_SDK_POSTGRES_DSN` to the one-shot container and run `uv run pytest -q plugins/sqlmodel/tests/test_postgresql_v2.py` | PASS — 1 passed; container removed after the gate. |
| Full suite | `uv run pytest -q` | PASS — 480 passed, 2 skipped and 21 optional conformance cases deselected in 104.03s. The PostgreSQL test is one default skip and passed separately above. |
| Static typing | `uv run mypy` | PASS — no issues in 178 source files. |
| Lint | `uv run ruff check` | PASS. |
| Dependency lock | `uv lock --check` | PASS — 126 packages resolved. |
| Import/absence boundary | `uv run python scripts/absence_audit.py` and `uv run pytest -q tests/rewrite/test_phase10_absence.py` | PASS — generated build/venv trees are excluded, removed runtime paths remain absent and account core is transport-neutral. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 28 pages fresh. |
| Docs frontmatter/build | from `docs-site/`: `npm run check` and `npm run build` | PASS — 33 pages built; existing Astro deprecation and missing `docs -> 404` notices are non-fatal. |
| Core package | `uv build --out-dir .phase16-final-build` | PASS — final sdist and wheel rebuilt from the current source tree. |
| SQLModel package | from `plugins/sqlmodel/`: `uv build --out-dir .phase16-final-build` | PASS — final sdist and wheel rebuilt from the current source tree. |
| Isolated imports/assets | install the final wheels into `.phase16-final-venv*`, import account/SQL APIs outside the repository, inspect metadata and CLI entry point | PASS — core loads no optional HTTP/Pydantic/SQL/browser module; SQL wheel exposes exactly five default tables and includes working migration/CLI assets. |
| Documentation examples | `uv run pytest -q tests/unit/test_docs_examples.py` | PASS — 2 passed. |
| Patch integrity | `git diff --check` | PASS — only configured LF/CRLF conversion warnings were emitted. |
| PostgreSQL DDL snapshot | `test_postgresql_schema_snapshot` in the configured/full suite | PASS — deterministic FK/index/constraint snapshot. |
| Neutral SQL session contract | `uv run pytest -q plugins/sqlmodel/tests/test_storage_v2.py plugins/sqlmodel/tests/test_registration_store.py` | PASS — 32 passed, including direct `session_auth(..., store=SqlSessionStore(...))` login, selected-session `401` refresh, replay and revision-2 persistence. |
| Focused SQL session typing | `uv run mypy plugins/sqlmodel/eazy_sdk_sqlmodel/session_store.py plugins/sqlmodel/tests/test_storage_v2.py` | PASS — no issues in 2 source files. |
| Focused SQL session lint | `uv run ruff check plugins/sqlmodel/eazy_sdk_sqlmodel/session_store.py plugins/sqlmodel/tests/test_storage_v2.py` | PASS. |

### Remaining work / blockers

None. Encryption/KMS and provider-specific restriction migration policy remain explicit non-goals;
they are not hidden compatibility paths in the v2 runtime.

## Documentation information architecture refresh (2026-08-15)

### Delivered

- Restructured the public navigation around Getting Started, Guides, Examples and API Reference.
- Added a capabilities map and dedicated installation/quickstart routes under `getting-started`.
- Added an examples map plus JSON auth, HTML login and signed API scenarios. Each scenario starts
  with the HTTP request and response and links to the complete focused guide.
- Clarified why hand-written contracts use `bind`, kept credentials separate from wire request
  models, documented automatic memory/custom-store login and refresh persistence, and documented
  the current SQL session-store limitation.
- Kept wreq out of the public documentation.

| Gate | Command | Result |
|---|---|---|
| Full suite | `uv run pytest -q` | PASS: 480 passed, 2 skipped and 21 optional conformance cases deselected in 105.63s. |
| Static typing | `uv run mypy` | PASS: no issues in 178 source files. |
| Lint | `uv run ruff check` | PASS. |
| Documentation freshness | `uv run python scripts/docs_freshness.py check` | PASS: 30 pages fresh. |
| Runnable documentation examples | `uv run pytest -q tests/unit/test_docs_examples.py` | PASS: 2 passed. |
| Frontmatter | from `docs-site/`: `npm run check` | PASS: frontmatter OK. |
| Static site build | from `docs-site/`: `npm run build` | PASS: 38 pages built. Existing Astro markdown deprecation and `docs -> 404` notices remain non-fatal. |
| Internal links | scan generated `dist` routes, excluding the pre-existing `/favicon.svg` reference | PASS: all documentation routes resolve. |
| Patch integrity | `git diff --check -- docs-site docs/implementation/STATUS.md` | PASS: only configured LF/CRLF conversion warnings. |

## Documentation topic split (2026-08-15)

Phase state is unchanged. Phase 16 remains active because the SQL session-store contract mismatch
is implementation work, not a documentation issue.

### Delivered

- Split authorization into Basic, Bearer, JWT, API key, cookie, combined security, login, session,
  refresh, registration and verification pages.
- Split request placement, response handling, client adapters, retry, redirect, rate limiting,
  protection presets and optional tools into focused pages.
- Replaced combined API Reference pages with one page per public module or concept.
- Removed the searchable architecture, compiler, storage-internals and legacy combined pages from
  the documentation site.
- Rebuilt the landing page, guide map, examples map and API map around the new routes.
- Kept wreq out of the public documentation.

| Gate | Command | Result |
|---|---|---|
| Heading structure | scan `title:` and Markdown headings for `и`, `или` and comma enumerations | PASS: no combined headings remain. |
| Documentation freshness | `uv run python scripts/docs_freshness.py check` | PASS: 53 pages fresh. |
| Runnable documentation examples | `uv run pytest -q tests/unit/test_docs_examples.py` | PASS: 2 passed. |
| Frontmatter | from `docs-site/`: `npm run check` | PASS: frontmatter OK. |
| Static site build | from `docs-site/`: `npm run build` | PASS: 68 pages built. Existing Astro markdown deprecation and `docs -> 404` notices remain non-fatal. |
| Internal links | scan 63 absolute documentation routes against `docs-site/dist` | PASS: 0 missing routes. |
| Live server | HTTP GET against eight representative routes on `127.0.0.1:4321` | PASS: every route returned 200 after restarting Astro. |
| Visual browser review | in-app browser discovery | SKIPPED: no browser instance is connected to this session; static build and live HTTP checks passed. |
| Full suite | `uv run pytest -q` | PASS: 480 passed, 2 skipped and 21 deselected in 109.71s. |
| Static typing | `uv run mypy` | PASS: no issues in 178 source files. |
| Lint | `uv run ruff check` | PASS. |
| Patch integrity | `git diff --check -- docs-site docs/implementation/STATUS.md` | PASS: only configured LF/CRLF conversion warnings were emitted. |

## Endpoint invocation API experiment (2026-08-15)

Phase state is unchanged. Phase 16 remains active; this isolated experiment does not modify the
production endpoint, client or auth APIs.

### Delivered

- Added four runnable alternatives to public `EndpointContract.bind({"field": value})` calls:
  explicit Pydantic requests, callable endpoints, direct typed endpoint arguments and typed SDK
  facades.
- Used one mock executor for every alternative so none of the candidates introduces a second
  execution path.
- Demonstrated matching auth refresh calls for each low-level form and a scoped
  `context.sdk.auth.refresh(...)` service that does not construct endpoint calls.
- Added a maintainer-facing comparison with costs, intended use and a provisional recommendation.

| Gate | Command | Result |
|---|---|---|
| Prototype behavior | `uv run pytest -q experiments/endpoint_invocation_api` | PASS: 5 passed. |
| Prototype typing | `uv run mypy --config-file experiments/endpoint_invocation_api/mypy.ini experiments/endpoint_invocation_api` | PASS: no issues in 3 source files. |
| Prototype lint | `uv run ruff check experiments/endpoint_invocation_api` | PASS. |
| Full suite | `uv run pytest -q` | PASS: 480 passed, 2 skipped and 21 deselected in 105.22s. |
| Static typing | `uv run mypy` | PASS: no issues in 178 source files. |
| Lint | `uv run ruff check` | PASS. |

## Declarative operation API experiment (2026-08-15)

Phase state is unchanged. Phase 16 remains active; this is an isolated breaking-API prototype and
does not change the production compiler, clients, generated SDKs or endpoint declarations.

### Delivered

- Added a decorator-based API where one typed `AsyncApi` method is both the operation declaration
  and the consumer call; there is no separate public contract, invocation object, `bind()` or
  string-keyed request mapping.
- Preserved operation ID, method/path, typed inputs, response cases, security, requirements,
  signing, response reactions, before-call flows, wire options, scope, tags, idempotency and raw
  response behavior in an internal immutable `CompiledOperation`.
- Added `@get`, `@post`, `@put`, `@patch` and `@delete` verb decorators with a statically typed
  keyword surface; request inputs come from `Annotated` method parameters and the result type comes
  from the return annotation.
- Replaced captcha-specific/guard authoring names with a general protection model. An operation's
  `protections=` contains only requirements that must complete before its first transport attempt;
  conditional response reactions and external protections are configured outside the decorator.
- Added a generated-model proof where public `LoginCredentials` remains separate from the exact
  private `_LoginWireBody`; `FromProtection` metadata fills two required JSON fields from a typed
  solver result before final Pydantic validation.
- Added identity-based `ProtectionRequirement` DI bindings, interchangeable fake implementations
  and evidence that a missing mandatory solver fails before the transport handler.
- Demonstrated inherited API security/signing/domain protection, explicit anonymous auth, normal
  no-input/raw calls and auth refresh through `context.sdk.auth.refresh(...)`.

| Gate | Command | Result |
|---|---|---|
| Prototype behavior | `uv run pytest -q experiments/declarative_operation_api` | PASS: 10 passed. |
| Prototype typing | `uv run mypy --config-file experiments/declarative_operation_api/mypy.ini experiments/declarative_operation_api` | PASS: no issues in 3 source files. |
| Prototype lint | `uv run ruff check experiments/declarative_operation_api` | PASS. |
| Full suite | `uv run pytest -q` | PASS: 480 passed, 2 skipped and 21 deselected in 104.50s. |
| Static typing | `uv run mypy` | PASS: no issues in 178 source files. |
| Lint | `uv run ruff check` | PASS. |

## Phase 17 completion (2026-08-15)

### Delivered

- Removed the public contract/call objects, binding helpers and typed client execution methods.
  Decorated methods on separate `AsyncApi` and `SyncApi` classes now lower to private operation
  declarations and use the existing shared executor. `.with_response(...)` is exposed by the same
  descriptor.
- Added class-creation validation for method signatures and automatic operation scopes. Preserved
  response cases, explicit HTTP placements, root/field bodies, auth defaults and explicit disable,
  dependencies, signing, wire options, tags, idempotency and raw responses.
- Added typed mandatory protection requirements and bounded acquire, solve and verify flows.
  Preflight validation rejects missing flows/solvers and invalid mappings before side effects;
  one result atomically fills multiple exact wire-model fields.
- Migrated auth login/refresh, registration/verification, presets, adapters, examples and test
  harnesses to scoped SDK methods. `SqlSessionStore` login/refresh persistence uses the neutral
  new-revision `SessionStore` contract.
- Changed OpenAPI generation to separate sync/async API classes, explicit method signatures,
  Pydantic request models and descriptor-provided response envelopes. Canonical
  `protectionFlows` plus operation `protections` generate public request models, private exact wire
  models and client flow configuration. Obsolete protection extension names are rejected.
- Rewrote the public README and documentation around direct SDK methods. Removed the old authoring
  and execution path from source, generated snapshots, documentation and built wheels.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Focused declarative API | `uv run pytest -q tests/rewrite/test_phase17_declarative_api.py` | PASS: 8 passed, including sync/async calls, response envelopes, solve/verify, acquire-only CSRF, multi-field injection and preflight failures. |
| OpenAPI unit/generation | `uv run pytest -q plugins/openapi/tests/test_rewrite_generator.py` | PASS: 25 passed; canonical protection package also passes strict mypy and obsolete extension aliases are rejected. |
| Museum/Petstore | `uv run python scripts/update_openapi_snapshots.py`; `uv run pytest -q plugins/openapi/tests/test_real_world_schemas.py` | PASS: snapshots regenerated; 8 import, typing and localhost execution tests passed. |
| Full suite and coverage | `uv run pytest -q --cov=eazy_sdk --cov-branch --cov-report=term` | PASS: 492 passed, 2 skipped, 21 deselected; combined coverage 85.37% (threshold 80%). |
| Auth conformance | `uv run pytest -q -m auth_conformance -rs` | PASS: 21 passed; 1 real-HTML fixture test skipped because optional local fixture files are absent. |
| Static typing | `uv run mypy` | PASS: no issues in 179 source files. |
| Lint | `uv run ruff check` | PASS. |
| Source absence | `uv run python scripts/absence_audit.py`; phase 10 absence test | PASS: removed paths, public exports, docs references and duplicate execution paths absent. |
| Documentation | freshness check; from `docs-site/`, `npm run check` and `npm run build`; generated-route scan | PASS: 53 pages fresh, frontmatter valid, 68 pages built and all internal routes resolve. Astro retains its existing markdown deprecation and missing `docs -> 404` notices. |
| Package artifacts | workspace plus XML builds in `.phase17-build`; `scripts/package_audit.py` | PASS: all five wheel/sdist pairs are typed; removed phase-17 source and paths are absent. |
| Isolated consumers | install built core/generated-consumer wheels and all plugin wheels into fresh venvs; `python -I` imports | PASS: direct SDK consumer and OpenAPI, presets, SQLModel and XML plugins import from built artifacts. |
| Patch integrity | `git diff --check` | PASS; only configured LF/CRLF conversion warnings. |

### Remaining work / blockers

None.

## Public documentation completeness follow-up (2026-08-15)

### Delivered

- Audited every public docs source and all 67 generated documentation routes after the phase-17
  breaking API migration. Replaced shallow operation pages with task-oriented guides and expanded
  compact reference pages with exact signatures, options, constraints and failure behavior.
- Rebuilt `/auth/login/` as a complete login/session how-to with HTTP request/response, separate
  credentials/request/session models, a decorated scoped API method, auth service mapping and the
  generated SDK usage path. Expanded refresh, session persistence and mandatory protection flows.
- Corrected stale public statements: OpenAPI uses `method.with_response(...)`; `SqlSessionStore`
  implements the neutral `SessionStore`; examples and navigation use decorated API methods.
- Expanded request placements and bodies, response handling, auth schemes, clients, retries,
  redirects, rate limiting, signing, dependencies, OpenAPI, SQLModel, XML and full scenario pages.
- Extended `docs-site/scripts/validate-frontmatter.mjs` so a published page with fewer than 180
  body characters fails `npm run check`; metadata-only shells can no longer pass the docs gate.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Content/frontmatter | from `docs-site/`, `npm run check` | PASS: all published pages satisfy the content floor and frontmatter contract. |
| Production render | from `docs-site/`, `npm run build` | PASS: 68 HTML pages built; Pagefind indexed all 68. Existing Astro markdown deprecation and `docs -> 404` notices remain non-fatal. |
| API freshness | `uv run python scripts/docs_freshness.py check` | PASS: 53 API-backed pages fresh. |
| Route content audit | inspect every generated `dist/**/index.html` `<main>` | PASS: all 67 documentation routes have a substantive main block; shortest rendered main is 201 characters. |
| Internal links | resolve every built documentation `href` against `dist` | PASS: all documentation routes resolve. |
| Live login page | request `http://127.0.0.1:4321/auth/login/` and inspect UTF-8 `<main>` | PASS: status 200, 4,375 main-text characters and all four workflow headings present. |
| Runnable examples | `uv run python examples/docs/local_showcase.py`; `uv run python examples/docs/account_lifecycle.py`; execute the published quickstart snippet | PASS: expected request captures, account lifecycle result and `200 req-42` output. |
| Behavior backing docs | `uv run pytest -q tests/rewrite/test_phase17_declarative_api.py`; SQLModel session-store selection | PASS: 8 phase-17 tests; 3 selected SQLModel session tests. |
| Static typing and lint | `uv run mypy`; `uv run ruff check` | PASS: no issues in 179 source files; all lint checks passed. |
| Public absence | `uv run python scripts/absence_audit.py`; public docs removed-term scan | PASS: removed APIs, internal execution terms and duplicate paths are absent. |
| Patch integrity | `git diff --check` | PASS; only configured LF/CRLF conversion warnings. |

### Skipped visual check

Pixel-level browser inspection was not available because the browser runtime reported no connected
browser (`agent.browsers.list()` returned an empty list). Production Astro rendering, live HTTP
content, route completeness and internal links were verified instead.

## Zapros transport-boundary architecture spike (2026-08-20)

### State

Complete. This spike does not change the completion state of phases 00-17 and does not migrate the
existing execution path.

### Delivered

- Pinned Zapros 0.16.0 for the evaluation, with pyreqwest 0.12.4 in the development matrix.
- Added sync and async curl_cffi Zapros handler prototypes.
- Added localhost wire tests for input immutability, unique query/header/body order, automatic
  header override/removal, every Zapros body representation, redirects, cookies, retries, sync and
  async streams, and two signature strategies.
- Verified Zapros stdlib, configured pyreqwest and custom curl_cffi handlers in sync and async modes.
- Recorded the decision, exact captures, unsupported duplicate-query contract and production gaps
  in `docs/implementation/zapros-evaluation.md`.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Focused behavior and wire capture | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider tests/integration/test_zapros_evaluation.py` | PASS: 29 passed. |
| Related transport/signing regression set | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider tests/integration/test_zapros_evaluation.py tests/rewrite/test_phase02_preparation.py tests/rewrite/test_phase03_transports.py tests/rewrite/test_phase04_signing.py` | PASS: 57 passed. |
| Available full suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider -o "pythonpath=plugins/xml" --basetemp=.test-tmp/zapros-full-20260820-3 --ignore=tests/fixtures/openapi_consumer/consumer_sdk tests plugins/sqlmodel/tests plugins/openapi/tests plugins/presets/tests plugins/xml/tests` | PASS: 539 passed, 2 skipped, 21 deselected. The ignored pre-generated consumer SDK is unreadable in this workspace (`WinError 5`). |
| Focused typing | `uv --cache-dir .uv-cache run mypy eazy_sdk/adapters/zapros_curl_cffi.py tests/integration/test_zapros_evaluation.py` | PASS: no issues in 2 source files. |
| Available full typing | `uv --cache-dir .uv-cache run mypy --exclude "tests[\\/]fixtures[\\/]openapi_consumer[\\/]consumer_sdk"` | PASS: no issues in 181 source files. Plain `uv run mypy` is blocked by `WinError 5` while traversing that fixture. |
| Focused lint | `uv --cache-dir .uv-cache run ruff check eazy_sdk/adapters/zapros_curl_cffi.py tests/integration/test_zapros_evaluation.py` | PASS. |
| Full lint | `uv --cache-dir .uv-cache run ruff check` | PASS; Ruff reports but tolerates the same inaccessible fixture. |
| Documentation freshness | `uv --cache-dir .uv-cache run python scripts/docs_freshness.py check` | PASS: 53 pages fresh. |

### Findings and remaining production work

- Both signing strategies match captured wire bytes on all six sync/async transport variants.
- Zapros preserves tested unique-name query, JSON-field and custom-header order without mutating
  caller inputs. Duplicate query names are unsupported because Zapros 0.16.0 retains only the
  first value.
- Zapros optional headers can be deleted before stdlib and custom curl emission. pyreqwest restores
  its own `Accept` and `User-Agent` defaults.
- Unconfigured `PyreqwestHandler()` follows redirects without Zapros redirect middleware. Eazy SDK
  must provide a builder with redirects and cookie storage explicitly disabled.
- The curl_cffi prototype buffers iterator bodies. True streaming and the remaining transport
  capability/capture gates are required before production migration.

## Phase 18 planning record (2026-08-20)

### State

Active. Runtime execution started after approval on 2026-08-20. The architecture document remains
the phase contract; implementation evidence is appended increment by increment below.

### Planning evidence

- Mapped the current API signature compiler, request preparer, signing graph, executor, adapter
  factories, response cases and extraction modules to the proposed architecture.
- Used the completed Zapros localhost spike as the handler/body/header/signing evidence baseline.
- Defined increments 18.1-18.8 with focused exit criteria and a final absence/release gate.
- Updated the master plan so duplicate query names are rejected and Zapros handlers replace the
  old prepared send protocol in the target architecture.

### Documentation verification

| Gate | Command | Result |
|---|---|---|
| Documentation freshness | `uv --cache-dir .uv-cache run python scripts/docs_freshness.py check` | PASS: 53 pages fresh. |
| Changed tracked docs whitespace | `git diff --check -- docs/implementation/README.md docs/implementation/STATUS.md` | PASS; only the existing Windows LF-to-CRLF notices were reported. |
| New plan structure | PowerShell code-fence parity and master-plan link count | PASS: 28 fence markers and 3 phase-18 links. |

Runtime pytest, mypy and ruff were not rerun for this docs-only planning increment. The most recent
runtime evidence remains in the Zapros spike section above.

## Phase 18 completion (2026-08-20)

### Delivered

- Replaced the prepared-adapter/factory boundary with public `Client`/`AsyncClient` constructors
  accepting Zapros `BaseHandler`/`AsyncBaseHandler`. Handler ownership, borrowed lifecycle,
  closed-handler reuse diagnostics and sync/async mismatch are explicit. `zapros==0.16.0` is the
  only mandatory runtime transport dependency.
- Removed `eazy_sdk.adapters`, client factories, `wrap_*`, validated client aliases, wreq and the
  parallel send protocol. HTTPX, Requests and curl_cffi are Zapros handlers with read-only
  profiles; third-party handlers receive a conservative profile. Requests/curl fail closed for
  unsupported streams, and HTTPX streams sync/async request bodies without buffering.
- Added logical Zapros inputs for JSON, form, multipart, exact bytes and replayable streams.
  Standard bodies use Zapros high-level inputs; exact custom/signature bodies use final `body=`
  bytes. Duplicate query names and repeated collection expansion fail before providers/network.
- Added immutable `ModelAdapterRegistry` support for dataclass, Pydantic v2, msgspec and custom
  adapters. Request, JSON/XML response, response headers, protection models and HTML extraction use
  the registry; adapter identity/version contributes to the execution-plan fingerprint. Direct
  model-library duck typing is isolated to `eazy_sdk.models`.
- Added `BodyCodec` and location-aware `ScalarCodec`, including exact byte preservation and
  single-value collection encoding. Added custom extractor composition where extractors return
  primitives and the registry constructs the public model.
- Added compact `Inject` authoring over the existing dependency graph. Injected slots preserve
  compiled order, cache/redaction behavior and remain absent from public method signatures.
- Added offline Parsel-backed `CSS`, `XPath`, `Scope`, `parse_html` and `Html(Model)` with nested
  dataclass/Pydantic/msgspec models and path/index-rich errors. Importing offline extraction does
  not load Zapros, HTTPX, Requests or curl_cffi.
- Moved signing to the final attempt representation and verified fresh request/signature creation
  for response retry, redirect, protection reaction and auth refresh. Standard Zapros JSON and
  exact spaced JSON signatures are recomputed from first-hop bytes across the transport matrix.
- Migrated OpenAPI generation to the public clients. Museum remains the positive snapshot/import/
  strict-mypy fixture; Petstore is the intentional negative fixture for an `explode=true` query
  array and reports its encoded JSON Pointer.
- Rewrote the SDK references and public client/HTML/model-codec/XML documentation. Package extras
  are `pydantic`, `msgspec`, `html`, `httpx`, `requests`, `curl-cffi`, `sqlmodel` and `all`.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Zapros handler/capture | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/phase18-handlers-3/run tests/integration/test_zapros_evaluation.py tests/rewrite/test_phase03_transports.py tests/integration/test_http_client.py tests/integration/test_zapros_public_client.py tests/unit/test_client_lifecycle.py` | PASS: 57 passed; Zapros version, headers, body kinds, configured stdlib/pyreqwest/curl sync/async and both signing strategies covered. |
| OpenAPI/plugins focus | focused OpenAPI real-world/generator, presets, SQLModel storage and XML command with repo-local `--basetemp` and XML `pythonpath` | PASS: 69 passed; Museum snapshot/import/strict-mypy and Petstore repeated-query rejection included. |
| Model/codec/HTML matrix | focused model adapter, wire codec, HTML, XML and fake-Zapros SDK tests | PASS: dataclass, Pydantic and msgspec request/response/XML/HTML paths plus custom adapter/codec/extractor. |
| Replay signing | focused phase 08/14 and public Zapros signing tests | PASS: retry, redirect, protection reaction and auth refresh rebuild and re-sign attempts. |
| Available full suite | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/phase18-final-full/run --ignore=tests/fixtures/openapi_consumer/consumer_sdk -o "pythonpath=plugins/xml" tests plugins/openapi/tests plugins/presets/tests plugins/sqlmodel/tests plugins/xml/tests` | PASS: 569 passed, 2 skipped, 15 deselected in 113.13s. Plain `uv run pytest -q` is blocked by `WinError 5` on the pre-generated consumer fixture and the host default pytest temp ACL; the repo-local temp/fixture exclusion is the available equivalent. |
| Auth conformance | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/phase18-auth-conformance/run --ignore=tests/fixtures/openapi_consumer/consumer_sdk -m auth_conformance -rs tests/conformance` | PASS: 15 passed in 45.27s. |
| Static typing | `uv --cache-dir .uv-cache run mypy --exclude "tests[\\/]fixtures[\\/]openapi_consumer[\\/]consumer_sdk"` | PASS: no issues in 192 source files. Plain mypy cannot traverse the same unreadable fixture. |
| Lint | `uv --cache-dir .uv-cache run ruff check` | PASS. `.test-tmp` and the unreadable generated consumer directory are explicit non-source exclusions. |
| Source absence | `uv --cache-dir .uv-cache run python scripts/absence_audit.py`; removed-term and direct-duck-typing scans | PASS: old adapters/factories/wrappers, phase-17 input API, duplicate execution loops and model duck typing outside `eazy_sdk.models` are absent. |
| Dependency lock | `uv --cache-dir .uv-cache lock --check` | PASS: 132 packages resolved; Zapros is pinned to 0.16.0. |
| Documentation | `scripts/docs_freshness.py check`; from `docs-site/`, `npm run check` and `npm run build` | PASS: 54 pages fresh, frontmatter valid and 69 HTML pages built/indexed. Existing Astro markdown deprecation and missing `docs -> 404` notices remain non-fatal. |
| Package artifacts | workspace plus XML builds in `.phase18-build`; `scripts/package_audit.py .phase18-build` | PASS: five wheel/sdist pairs are typed, require the exact Zapros core boundary and contain no legacy path/symbol/direct model duck typing. |
| Isolated core/extras | fresh venv installs of the built core wheel for core, Pydantic, msgspec, HTML, HTTPX, Requests and curl-cffi; `python -I` imports | PASS: all seven combinations import independently; offline HTML loads no Zapros/network module until a client is requested. |
| Isolated plugins | fresh venv installs of built OpenAPI, presets, SQLModel and XML wheels with the built core; `python -I` imports | PASS: all four plugin packages import, including extractor-based XML. |

### Remaining work / blockers

None. The unreadable pre-generated consumer directory is an environment ACL limitation, not a
phase implementation gap; live-generated Museum packages still pass import and strict mypy gates.

## Native model-owned serialization correction (2026-08-21)

Phase 18 remains complete after correcting ownership of request-model serialization policy.

### Contract and implementation

- Removed `exclude_unset`, `exclude_defaults` and `exclude_none` from `JsonBody`; the marker now
  describes only JSON placement/media type.
- Removed public `DumpPolicy`. `ModelAdapter.dump` follows the native model policy and receives only
  the target representation mode (`json` or `python`) plus the registry.
- Pydantic uses `model_dump(mode=...)` without Eazy SDK alias/exclusion/default overrides; msgspec
  uses `msgspec.to_builtins`, including native `rename` and `omit_defaults`; plain dataclasses emit
  all declared fields. `replace_adapter(...)` supports an intentional project-wide replacement of
  a built-in model family.
- JSON/form use JSON representation; multipart uses Python representation so arbitrary binary
  fields remain exact bytes. This mode selection does not alter model-owned exclusion policy.
- Generated OpenAPI models inherit their own `OpenAPIModel`. Its default excludes fields the caller
  did not set while retaining an explicitly supplied `None` as JSON `null`, preserving PATCH wire
  semantics without putting Pydantic flags into `JsonBody`.
- Source/built absence audits reject reintroduction of `DumpPolicy`. Authoritative implementation
  docs and public Pydantic/msgspec/dataclass/custom-adapter examples describe the corrected contract.

The initial red model-adapter run produced `8 failed, 23 passed`: native Pydantic/msgspec policy was
not yet honoured and the old marker parameters were still accepted. Those failures passed after the
implementation.

### Verification

| Gate | Command | Result |
|---|---|---|
| Model/body regressions | focused model-adapter and phase-02 runs | PASS — 38 passed, including non-UTF-8 Pydantic multipart bytes. |
| Model/OpenAPI regression aggregate | unit, phase-02/14, OpenAPI and XML focused suites | PASS — 192 passed. |
| Full available workspace suite | `uv run pytest -q -p no:cacheprovider --basetemp=.test-tmp/native-policy-full/run --ignore=tests/fixtures/openapi_consumer/consumer_sdk -o "pythonpath=plugins/xml" tests plugins/openapi/tests plugins/presets/tests plugins/sqlmodel/tests plugins/xml/tests` | PASS — 580 passed, 2 skipped, 15 deselected. |
| Configured pytest command | `uv run pytest -q` | BLOCKED before test execution by the pre-existing inaccessible/malformed `.pytest_cache/v/cache` path (`FileExistsError [WinError 183]`). The no-cache full suite above is the verification result. |
| Static typing | `uv run mypy --exclude "tests[\\/]fixtures[\\/]openapi_consumer[\\/]consumer_sdk" eazy_sdk tests plugins scripts` | PASS — no issues in 192 source files. Plain `uv run mypy` remains blocked by `PermissionError [WinError 5]` on that pre-generated consumer directory. |
| Lint | `uv run ruff check` | PASS. |
| Source absence | `uv run python scripts/absence_audit.py` | PASS. |
| Docs freshness | `uv run python scripts/docs_freshness.py check` | PASS — 54 pages fresh. |
| Docs site | from `docs-site/`: `npm run check`; `npm run build` | PASS — content/frontmatter valid and 69 pages built. Astro emits the existing Markdown deprecation notice and missing `docs -> 404` entry notice. |
| OpenAPI snapshots | `scripts/update_openapi_snapshots.py` followed by OpenAPI regression suites | PASS — generated `_model_base.py`, client/models snapshots, runtime unset/null behavior and strict generated mypy are current. |
| Package build/audit | workspace plus XML builds in `.test-tmp/native-policy-build`; `scripts/package_audit.py` | PASS — all five wheel/sdist pairs are typed and contain no removed policy API. |

## Phase 18 documentation follow-up (2026-08-20)

- Expanded the public model serialization page into a standalone how-to/reference for dataclass,
  Pydantic v2, msgspec and custom `ModelAdapter` implementations.
- Documented model-library installation, request/response use, dump-policy differences, aliases,
  adapter registration/fingerprinting, exact `BodyCodec` output and extractor boundaries.
- Linked the guide from installation, JSON request and successful-response pages, and made model
  serialization directly discoverable in the Requests navigation.

| Gate | Command | Result |
|---|---|---|
| Model and codec behavior | `uv --cache-dir .uv-cache run pytest -q -p no:cacheprovider --basetemp=.test-tmp/docs-serialization-matrix tests/unit/test_model_adapters.py tests/unit/test_wire_codecs.py tests/unit/test_request_serialization.py` | PASS: 45 passed. |
| Exact documentation examples | isolated runtime and strict-mypy smoke of the dataclass, Pydantic, msgspec, custom adapter and canonical JSON snippets | PASS: runtime assertions and strict mypy; corrected Pydantic alias construction and optional custom-field loading found by the first typing run. |
| Documentation freshness | `uv --cache-dir .uv-cache run python scripts/docs_freshness.py check` | PASS: 54 pages fresh. |
| Documentation validation | from `docs-site/`, `npm run check` | PASS: content and frontmatter valid. |
| Documentation build | from `docs-site/`, `npm run build` | PASS: 69 pages built and indexed; existing non-fatal Astro deprecation and missing `docs -> 404` notices remain. |

## Release 0.1.0 preparation and publication (2026-09-01)

### State

Complete for the maintainer-selected GitHub-only scope. Version selection, release metadata,
validation, build, artifact audit, supported-Python isolated installs, Git source/tag publication,
GitHub Release assets and public installation are complete. On 2026-09-01 the maintainer explicitly
deferred PyPI publication. No PyPI artifact is reported as published.

### Delivered

- Selected stable version `0.1.0` for the core package and all five first-party plugins.
- Updated inter-package requirements, the public runtime version, the generated-consumer fixture,
  the lockfile, README installation commands, stable classifiers and changelog.
- Built one wheel and one sdist for each of `eazy-sdk`, `eazy-sdk-openapi`,
  `eazy-sdk-asyncapi`, `eazy-sdk-presets`, `eazy-sdk-sqlmodel` and `eazy-sdk-xml` in
  `.test-tmp/release-0.1.0-20260901`.
- Kept the release artifacts local. The six PyPI project names returned no public JSON project
  metadata before publication.

### Verification evidence

| Gate | Command | Result |
|---|---|---|
| Lock | `uv lock`; `uv lock --check` | PASS: all six workspace distributions updated from `0.1.0a1` to `0.1.0`; 159 packages resolved. |
| Full runtime | clean `UV_PROJECT_ENVIRONMENT=.venv-release-313`; `uv run pytest -q` | PASS: 857 passed, 11 skipped in 161.75s. Skips remain the opt-in live WebSocket, external PostgreSQL and local real-HTML fixture lanes. |
| Static checks | `uv run mypy`; `uv run ruff check` | PASS: no issues in 272 source files; all lint checks passed. |
| Documentation and absence | `scripts/docs_freshness.py check`; `scripts/absence_audit.py`; strict Sphinx `dirhtml` build with `-W --keep-going` | PASS: 59 pages fresh; absence audit clean; 73 sources built without warnings. |
| Package artifacts | six `uv build` runs; `scripts/package_audit.py .test-tmp/release-0.1.0-20260901` | PASS: six wheel/sdist pairs have matching `0.1.0` metadata, licenses, typing markers, the Zapros boundary and no legacy paths. |
| Supported-version isolation | install the six exact wheels into fresh CPython 3.13.12 and 3.14.3 venvs; isolated import/version/`Requires-Python` assertions | PASS: all distributions import as `0.1.0` and declare `>=3.13` on both Python versions. |
| Upload validation | `uv publish --dry-run --trusted-publishing never` over the 12 release files | PASS: all 12 files validated; exit code 0. |
| PyPI publication | maintainer release decision | DEFERRED: the maintainer selected GitHub-only publication for this release. No PyPI upload was attempted. |
| Git source and tag | commit `7565191`; annotated tag `v0.1.0`; push `master` and tag to `origin` | PASS: release source and tag are public. Follow-up commit `b08643b` points README installation commands at GitHub assets. |
| GitHub Release | create public stable release `v0.1.0`; attach the exact 12 audited files | PASS: release ID `380801036`, 12 assets, `draft=false`, `prerelease=false`; every public size and SHA-256 digest matches the local audited artifact. |
| Public installation | fresh CPython 3.13.12 venv; install all six wheel URLs from the public GitHub Release with cache disabled; isolated imports/version assertions | PASS: all six distributions installed and imported as `0.1.0`. |

### Remaining work

None for the maintainer-selected GitHub-only release scope. PyPI publication remains intentionally
deferred until the maintainer re-enables it.

## Phase 27 — extension surface (2026-09-02)

### State

Complete. The maintainer confirmed that the repository is still alpha and that the previous stable
classification was erroneous. Breaking removal from `eazy_sdk.ext` was explicitly allowed; no
compatibility aliases or shims were introduced.

### Baseline

- `eazy_sdk.ext.__all__` exported 96 names.
- Only 14 names were mentioned in public documentation; 82 were absent.
- The namespace mixed extension protocols with dependency-lowering aliases, execution/compiler
  stages, prepared-request records, auth/session internals and objects renamed from private names.
- First-party runtime consumers are the XML codec/extractor and protection presets. Most other
  imports are repository tests using the namespace as an internal test barrel.

### Current increment

- [x] EX-00 baseline classified and authoritative phase plan added.
- [x] EX-01 exact SPI surface test, breaking cleanup and consumer migration.
- [x] EX-02 complete extension-author reference and freshness coverage.
- [x] EX-03 alpha metadata correction and complete verification gates.

### Commands run

| Command | Result |
|---|---|
| `git status --short` | PASS: clean baseline before phase edits. |
| repository import/export and documentation inventory (`rg`, Python AST) | PASS: 96 exports and first-party consumers classified. |
| `uv run pytest -q tests/unit/test_ext_surface.py tests/unit/test_phase25_typing.py plugins/xml/tests/test_xml_codec.py plugins/presets/tests/test_presets.py` | PASS: 26 passed. |
| complete affected HTTP/auth/signing/response/plugin regression set | PASS: 317 passed. |
| `uv run pytest -q` | PASS on exact tagged commit `e9808e6`: 859 passed, 11 skipped in 164.58s. |
| `uv run mypy` | PASS: no issues in 273 source files. |
| `uv run ruff check` | PASS: all checks passed. |
| `uv run python scripts/absence_audit.py` | PASS: removed symbols/imports and execution loops are clean. |
| `uv run python scripts/docs_freshness.py check` | PASS: 60 pages fresh; every one of 23 extension exports is named in the reference. |
| `npm run check`; `npm run build` from `docs-site/` | NOT APPLICABLE: current `docs-site` has no `package.json`; the repository uses Sphinx. |
| strict Sphinx `dirhtml` build with `-W --keep-going` | PASS: 74 sources built without warnings. |
| six workspace package builds; `scripts/package_audit.py` | PASS: six wheel/sdist pairs for `0.2.0a1`, matching metadata and no legacy paths. |
| isolated wheel installs on CPython 3.13.12 and 3.14.3 | PASS: all six distributions import as `0.2.0a1`. |
| Git source/tag publication | PASS: commit `e9808e6`, `master` and annotated tag `v0.2.0a1` pushed. |
| GitHub prerelease | PASS: release ID `380831159`, 12 assets, every public size and SHA-256 matches local audited artifacts. |
| public GitHub wheel install | PASS: all six public wheel URLs installed and imported on fresh CPython 3.13.12. |
| PyPI publication | DEFERRED: no PyPI upload was attempted, per maintainer direction. |

### Remaining work / blockers

None. The former `v0.1.0` GitHub release is preserved as a prerelease with an explicit correction
notice; it is not presented as a compatibility guarantee. The supported alpha release is
`v0.2.0a1` on GitHub. PyPI remains intentionally deferred.

## Phase 28 — anti-bot API correctness and ergonomics planning (2026-09-02)

### State

Pending. Planning and review triage are complete; runtime implementation has not started. AP-00 is
the next executable increment. Phases 00–27 remain historically complete, while phase 28 is the
release gate for the newly accepted findings.

### Accepted findings

- Public clients cannot provide the `ExecutionRuntime.network_identity` required by
  network-scoped Cloudflare and Turnstile policies.
- Fresh tracked checkout omits every plugin `README.md`; editable workspace sync fails even though
  ordinary wheel/sdist builds happen to succeed.
- Preset capabilities are metadata only and are not part of client/compiler preflight.
- `CloudflareClearance.user_agent` is neither applied nor checked against transport identity.
- First-party presets require manual lifecycle collection and solver-binding wiring.
- A dead phase-06 response-reaction/before-call API remains visible beside the phase-24 policy API.
- Replay constructors accept a negative budget and an empty idempotency proof.
- Per-key protection locks are never removed.
- Root README presents `TypedDict + Unpack` before the simpler phase-25 hand-written signature.

The review recommendation to add `eazy_sdk.compat.protection_v0` and deprecation wrappers is not
accepted: the authoritative alpha contract requires direct removal without aliases or a second
import path. The packaging finding is narrowed to clean editable workspace sync/source completeness;
the clean-snapshot `uv build --all-packages` command itself passed.

### Commands run

| Command | Result |
|---|---|
| `git status --short`; `git diff --stat` before edits | PASS: tracked worktree clean; no user changes to preserve. |
| read `docs/implementation/README.md`, phases 00–11, both SDK references and relevant phases 23–27 | PASS: target invariants and phase dependencies reviewed in order. |
| `git rev-parse HEAD` | PASS: current source is `e9808e6566a96c4828a8e484fa58e44911e8894a`, later than review snapshot `b08643b`. |
| source/test/docs inventory with `rg` and targeted file reads | CONFIRMED: all nine findings remain present on current source. |
| create a temporary `git archive HEAD` snapshot and inspect `plugins/*/README.md` | CONFIRMED: all five plugin README files are absent from tracked snapshot and ignored by root `*.md`. |
| `uv build --all-packages` in that clean snapshot | PASS: six wheel/sdist pairs built; this narrows the packaging claim. |
| `uv sync --all-packages --locked` in that clean snapshot | FAIL as expected: Hatch editable metadata raises `OSError: Readme file does not exist: README.md` for `eazy-sdk-openapi` (the same defect applies to the other plugin manifests). |
| inline public API probe for `ClientConfig`, `safe_method(-1)`, `idempotency_key("")` and `ReplayPolicy` signature | CONFIRMED: no network identity field; both invalid declarations construct successfully; ignored `freshness` remains public. |

### Plan and remaining work

- Added authoritative [phase 28](28-antibot-api-correctness-and-ergonomics.md) with triage,
  target contracts, AP-00–AP-07 increments, mandatory tests and exit criteria.
- Added phase 28 to the master dependency graph and feature-phase table.
- No implementation test is reported as passing in this planning increment.
- Full pytest, mypy, Ruff, docs, package and isolated-install gates were not rerun because only
  authoritative planning/status Markdown changed; they are mandatory in AP-07.

## Phase 28 — anti-bot API correctness and ergonomics implementation (2026-09-02)

### State

Complete. AP-00–AP-07 and every phase-28 exit criterion have implementation and verification
evidence. Phases 00–27 remain historically complete. No compatibility alias, deprecated wrapper,
second config reader or second executor path was added.

### Delivered

- All five plugin README files are prospective tracked inputs through an explicit `.gitignore`
  negation. Workspace metadata has an automated existence gate.
- `ClientConfig` and first-party handlers expose one typed `NetworkIdentity` or
  `NetworkIdentityProvider`. Runtime resolves it per attempt before managed-state lookup and uses
  it for cache/single-flight keys, solver context and solution validation. Missing or conflicting
  sources fail before providers and transport I/O.
- Vendor-neutral `ProtectionCapabilities` and solution identity expectations are compiled into
  policy metadata and plan fingerprints. Typed redacted mismatch errors occur before state commit,
  replay or emit.
- First-party Cloudflare, Turnstile and reCAPTCHA factories accept an optional direct solver and
  lower through immutable `ProtectionBundle` plus `ClientConfig.with_protection()`. Unbound DI and
  the low-level typed collections remain the advanced authoring path.
- Replay declarations validate nonnegative budgets and HTTP field-name proofs at construction.
  `ReplayPolicy.freshness` and the unreachable phase-06 reaction/before-call API were removed
  directly, with an exact `eazy_sdk.protection.__all__` and no shim.
- Counted single-flight locks remove their entry after the last holder/waiter on success,
  exception or cancellation. A 1,000-key stress regression returns the registry to zero.
- Root and site quickstarts lead with explicit keyword-only hand-written operations. Protection
  guides lead with bundle installation and document identity ownership, rotation, capabilities,
  mismatch, redaction and concurrency.

### Commands run

| Command | Result |
|---|---|
| focused protection/public-client suites during AP-02–AP-06 | PASS: final affected aggregate 59 passed; dedicated lock suite 17 passed including 1,000 unique keys, waiter cancellation and solver failure. |
| `uv run pytest -q` in the pre-existing `.venv` | ENVIRONMENT FAILURE: 859 passed, 11 skipped, 13 failed and 5 errored because the local Black mypyc extension and BasedPyright `dist/pyright` files were missing. No product assertion failed; this run is not counted as a passing gate. |
| `UV_PROJECT_ENVIRONMENT=.venv-phase28`, `UV_LINK_MODE=copy`, `uv sync --all-packages --locked` | PASS: clean CPython 3.13 environment created and all six editable workspace distributions installed. |
| clean-environment `uv run pytest -q` | PASS: final rerun 881 passed, 11 skipped in 166.57s. Skips are the existing opt-in live WebSocket, external PostgreSQL and local real-HTML lanes. |
| strict phase-28 consumer typing fixture under mypy and BasedPyright | PASS: valid identity provider, capable solver and bundle merge preserve exact types; invalid provider, solver result and bundle are rejected. |
| clean-environment `uv run mypy`; `uv run ruff check` | PASS: no issues in 275 source files; all lint checks passed. |
| `uv run python scripts/absence_audit.py` | PASS: removed identities/modules/symbols/imports and execution loops are clean. |
| docs example/freshness/workspace metadata test aggregate | PASS: 31 passed. |
| `uv run python scripts/docs_freshness.py check`; docs metadata validator | PASS: 60 API-backed pages fresh; 74 pages have valid frontmatter/content. |
| strict `sphinx-build -W --keep-going -b dirhtml` | PASS: 74 sources built without warnings; generated `llms-full.txt` contains 6,626 lines. |
| `npm run check`; `npm run build` from `docs-site/` | NOT APPLICABLE: `docs-site/package.json` is absent; Sphinx is the repository-defined renderer. |
| `uv build --all-packages`; `scripts/package_audit.py` | PASS: six wheel/sdist pairs for `0.2.0a1` have matching metadata, licenses, typing markers, the Zapros boundary and no legacy paths. |
| core-wheel scan for removed protection names | PASS: old phase-06 API names are absent from packaged Python/Markdown/text members. |
| prospective tracked snapshot from `git ls-files --cached --others --exclude-standard`; `uv sync --all-packages --locked`; `uv build --all-packages`; package audit | PASS: all five plugin README files exist, all six editable packages sync, and all twelve artifacts build/audit without manual snapshot preparation. Snapshot: `C:\Users\user\AppData\Local\Temp\eazy-phase28-snapshot-2491d940c2d34dcf8ac2c8d9a105ddc6`. |
| install all six exact wheels into fresh CPython 3.13.12 and 3.14.3 venvs; import all distributions | PASS: both environments import version `0.2.0a1`. |

### Remaining work / blockers

None for phase 28. The pre-existing `.venv` remains in use by an active BasedPyright language
server and was not destructively replaced; all authoritative release gates ran in the clean
`.venv-phase28` environment.

## Phase 28 release closure: Eazy SDK 0.2.0a2 (2026-09-02)

### State

Complete and published. Commit `185021c3439da8a2dfdd1b95c6736a7008acd019` is the exact source of
annotated tag `v0.2.0a2` and GitHub prerelease `380995351`. The release contains six wheels and
six source distributions. Follow-up commit `c204aaf3bb60d1e6a679fc19a2eb1e33c1b39b60` corrects the
top-level README status from `0.2.0a1` to `0.2.0a2` on `master`; the immutable release tag and
verified artifacts remain on the audited release commit. PyPI publication remains intentionally
deferred.

### Release changes

- Versioned the core distribution and all five workspace plugins as `0.2.0a2`.
- Raised first-party plugin requirements to `eazy-sdk>=0.2.0a2,<0.3`; the presets require the new
  phase-28 protection surface.
- Updated the root GitHub wheel installation URLs and the exact OpenAPI consumer fixture.
- Generated `uv.lock` from the updated workspace metadata.

### Commands run

| Command | Result |
|---|---|
| `uv lock`; `uv lock --check` | PASS: all six workspace distributions resolved as `0.2.0a2`; no tracked `0.2.0a1` reference remains. |
| clean-environment `uv run pytest -q` | PASS: 881 passed, 11 skipped in 171.05s. |
| clean-environment `uv run mypy`; `uv run ruff check` | PASS: no issues in 276 source files; all lint checks passed. |
| `uv run python scripts/absence_audit.py`; `uv run python scripts/docs_freshness.py check` | PASS: removed architecture is absent; 60 API-backed pages are fresh. |
| `uv run python docs-site/scripts/validate_docs.py` | PASS: metadata and content are valid for 74 pages. |
| first `sphinx-build` invocation without `-c docs-site` | COMMAND ERROR: Sphinx could not locate `conf.py` under the source directory. No project assertion ran. |
| `uv run sphinx-build -W --keep-going -b dirhtml -c docs-site docs-site/src/content/docs <temp>` | PASS: 74 sources built without warnings; the plugin generated `llms-full.txt` from all 74 sources with 6,626 reported lines. |
| `npm run check`; `npm run build` from `docs-site/` | NOT APPLICABLE: `docs-site/package.json` is absent; Sphinx is the repository-defined renderer. |
| `git archive HEAD`; clean-snapshot `uv sync --all-packages --locked`; `uv lock --check` | PASS: all five plugin README files are present and all six `0.2.0a2` workspace packages install from the tracked snapshot. |
| clean-snapshot `uv build --all-packages`; `scripts/package_audit.py` | PASS: six wheel/sdist pairs have matching alpha metadata, licenses, typing markers, the Zapros boundary and no legacy paths. |
| exact core-wheel removed-symbol scan | PASS: the deleted reaction/before-call API is absent at Python-identifier boundaries. |
| install all six exact commit-derived wheels on CPython 3.13.12 and 3.14.3 | PASS: every distribution and import reports `0.2.0a2`. |
| `git diff --cached --check`; release commit and annotated tag creation | PASS: commit `185021c`; tag `v0.2.0a2` points to that commit. |
| `git push --atomic origin master v0.2.0a2` | PASS: `master` and the annotated tag were published together. |
| GitHub draft creation, 12 asset uploads, local/public size and SHA-256 comparison, prerelease publication | PASS: release ID `380995351`, 12 assets, draft false, prerelease true, exact target commit. |
| anonymous GitHub API check and fresh CPython 3.13.12 install from all six public wheel URLs | PASS: public metadata/digests match and every installed distribution imports as `0.2.0a2`. |
| tracked-version sweep; focused README example tests; freshness check; README correction push | PASS: no `0.2.0a1` reference remains on `master`; 12 tests passed; 60 pages fresh; follow-up commit `c204aaf` is published. |
| PyPI upload | DEFERRED: this release follows the established GitHub-only alpha scope. |

### Published release

- Source commit: `185021c3439da8a2dfdd1b95c6736a7008acd019`
- Tag: `v0.2.0a2`
- GitHub prerelease: <https://github.com/0cherednoq/eazy-sdk/releases/tag/v0.2.0a2>
- Assets: 12 verified wheels/source distributions

### Remaining work / blockers

None for the GitHub alpha release. PyPI remains outside the approved release scope.

## Phase 29 — anti-bot simplification and session affinity planning (2026-09-02)

### State at this planning checkpoint

Historical snapshot, superseded by the completed implementation record below. At this point the
critique review and authoritative plan were complete, but AS-00 had not started. Phase 29 was the
earliest incomplete phase. Phases 00–28 remained historically complete; their evidence is not
rewritten by the later implementation.

### Accepted findings

- The ordinary custom-guard path exposes policy, signal, solver requirement, registry, private
  binding and bundle machinery instead of one lowering builder.
- `eazy_sdk.protection.__all__` contains 74 names and mixes user, plugin, compiler, cache and
  runtime responsibilities.
- `NetworkIdentity` duplicates handler/session configuration without applying or verifying the
  actual proxy, User-Agent, impersonation or connection.
- Per-attempt `NetworkIdentityProvider` is unsafe in the current executor. A solution acquired
  under identity A can be stored in policy-local call state and applied to replay attempt B.
- `sticky_network_identity` is inferred from the presence of metadata and does not prove session
  affinity.
- `ProtectionCapabilities` mixes runtime facts, handler/session properties and solver
  implementation choices. Browser/JavaScript flags can reject a behaviorally correct solver.
- First-party presets already prove the value of `with_protection()`; the same one-expression path
  should exist for custom response guards.

### Rejected or narrowed proposals

- Detector shorthand may return `challenge | None`, but internal match, `NoMatch` and `Malformed`
  outcomes remain distinct.
- Application callbacks may not receive or replace `PreparedRequest`; solution application stays
  a compiler-reserved atomic patch before fresh preparation and signing.
- Runtime preflight is narrowed to facts it controls. Missing solver, binding conflicts,
  replayability, semantic proof, budgets and `HandlerProfile` fidelity are not removed.
- Identity metadata is not deleted without a state-ownership replacement. Phase 29 binds managed
  state to one client/handler session lifecycle and makes proxy rotation create a new client.

### Planning commands and evidence

| Command / inspection | Result |
|---|---|
| initial `git status --short`; `git diff --stat`; proposal diff | PASS: worktree was clean before documentation edits. |
| read `docs/implementation/README.md`, phases 00–11, SDK references, phases 24/27/28 and current protection code/docs/tests | PASS: critique was compared with the authoritative plan and actual `v0.2.0a2` implementation. |
| AST evaluation of `eazy_sdk/protection.py::__all__` | PASS: baseline is exactly 74 public names. |
| public inline `AsyncClient` reproduction with provider A on attempt 1 and B on attempt 2 | DEFECT REPRODUCED: 2 provider calls, 2 handler calls, and replay B emitted `cf_clearance=from-a`. |
| existing phase-28 test inspection | GAP CONFIRMED: static identity and mismatch tests exist; no provider-rotation challenge/replay regression covers local call state. |
| `uv run pytest -q plugins/presets/tests/test_presets.py tests/unit/test_phase28_typing.py` | ENVIRONMENT FAILURE: 25 passed, 2 failed because the pre-existing `.venv` BasedPyright package is missing `dist/pyright`; no runtime/preset assertion failed. This is not a passing gate. |
| same focused command with `-k 'not basedpyright'` | PASS: 25 passed, 2 BasedPyright cases explicitly deselected after the environment failure was recorded. |
| `uv run python scripts/docs_freshness.py check` | PASS: 60 public API-backed pages are fresh; this planning change does not claim the future phase-29 public docs migration. |

The inline reproduction is planning evidence, not a passing release test. AS-00 must add a tracked
red regression before runtime changes.

### Progress

| Increment | State | Remaining work / blockers |
|---|---|---|
| AS-00 red baseline and API lock | pending | Add committed regressions, typing targets and exact removal allowlists. |
| AS-01 high-level custom guard | pending | Implement builder, detector adapter, solution mapping and stage errors. |
| AS-02 session-owned affinity | pending | Remove per-attempt identity path and bind state to handler session lifecycle. |
| AS-03 capability cleanup | pending | Remove implementation flags and retain proof-based preflight. |
| AS-04 public surface split | pending | Complete reachability audit, high-level allowlist and advanced SPI migration. |
| AS-05 presets and composition roots | pending | Migrate first-party factories and shared-session examples. |
| AS-06 documentation and release closure | pending | Update references/docs and run all release gates. |

### Remaining work / blockers

No external blocker existed at this planning checkpoint. The next executable increment was AS-00;
its completed result is recorded below.

## Phase 29 — anti-bot simplification implementation (2026-09-02)

### State

Complete. AS-00–AS-06 and every phase-29 exit criterion have implementation and verification
evidence. Phases 00–28 remain historically complete; no compatibility alias, deprecated wrapper
or second protection execution path was added.

### Delivered

- Reproduced the unsafe `identity A -> challenge -> identity B -> replay` transition and the
  false negative capability preflight as two red-baseline failures before changing the runtime,
  then converted them into permanent passing target regressions.
- Added typed `challenge_guard()` and `solution_fields()` high-level builders. They lower into the
  existing `ChallengePolicy`, compiler-reserved private bindings and single coordinator, including
  preserved match/`NoMatch`/`Malformed` outcomes and atomic multi-cookie application.
- Added redacted configuration, detection, solve and application stage errors with chained causes;
  the existing replay-stage error remains distinct.
- Removed public/per-attempt network identity metadata from `ClientConfig`, solver context and all
  first-party handler constructors. Protection state is keyed by client or actual handler session
  owner and is cleared with the client runtime lifecycle.
- Removed `BodyAccess`, `ProtectionCapabilities`, `CapableChallengeSolver`, implementation flags,
  identity expectations and the corresponding preflight/fingerprint fields. Missing solver,
  compiled destination, replay proof, response and verified `HandlerProfile` checks remain.
- Replaced `eazy_sdk/protection.py` with the package split: the sorted high-level namespace has 19
  names (SHA-256 `bd83b2c7f991d97bd044ba52534f911565ebae31cdbbc82d281f9c4ec624e810`)
  and the documented `eazy_sdk.protection.advanced` SPI has 49 names (SHA-256
  `e9432af539baf6657b9fd6398c075863431ab2db63d07fb02fab8af296f04b1b`).
- Migrated Cloudflare Challenge Pages, Turnstile, reCAPTCHA, internal/compiler imports, typing
  fixtures and public documentation. Ordinary examples use the short installable path; the
  lowering SPI is isolated in its reference section.
- Extended absence/package audits to reject the removed identity/capability symbols and old module
  path in source, public docs, wheel and sdist, while requiring both new protection package files.

### Increment evidence

| Increment | State | Evidence | Remaining work / blockers |
|---|---|---|---|
| AS-00 | complete | The pre-change red command produced exactly 2 expected failures: replay attempt B received `cf_clearance=from-a`, and a correct Cloudflare solver was rejected only for missing technology flags. The pre-change phase-28 focus remained green outside those target failures. | None. |
| AS-01 | complete | Custom KAD-shaped guard, detector outcome matrix, two-cookie atomic application, error redaction/cause and common-executor traces pass. | None. |
| AS-02 | complete | Native-session initial/replay, new-client isolation, close cleanup and constructor/signature absence regressions pass for sync/async paths. | None. |
| AS-03 | complete | Parameterized remote/API/WASM/browser solver behavior passes without technology declarations; retained preflight regressions pass in the full suite. | None. |
| AS-04 | complete | Exact 19/49-name fingerprints, high-level negative reachability, advanced typing and built wheel/sdist checks pass. | None. |
| AS-05 | complete | First-party presets and custom composition examples use one `with_protection()` installable expression and session-owned state. | None. |
| AS-06 | complete | Public/reference docs, fingerprints and all focused/full/docs/package/fresh/isolation release gates below pass. | None. |

### Commands run

| Command / gate | Result |
|---|---|
| `$env:UV_PROJECT_ENVIRONMENT='.venv-phase28'; uv run pytest -q tests/unit/test_phase29_red_baseline.py` before runtime changes | EXPECTED RED: exactly 2 failures, one for A-to-B clearance reuse and one for false capability rejection. The temporary red-only file was then replaced by passing target regressions. |
| first post-migration `uv run pytest -q` | DIAGNOSTIC FAILURE: 880 passed, 11 skipped and 1 stale documentation import failed in phase 17. The import was moved to the documented advanced SPI and all later focused/full reruns passed. |
| final phase-29/typing/surface focus: `uv run pytest -q tests/unit/test_phase29_antibot_simplification.py tests/unit/test_phase28_typing.py tests/unit/test_protection_surface.py` | PASS: 18 passed, including strict mypy/BasedPyright fixtures and remote/API/WASM/browser variants. |
| final clean-environment `uv run pytest -q` | PASS: 886 passed, 11 skipped in 152.08s. Skips are the existing opt-in live WebSocket (`EAZY_SDK_RUN_LIVE_WS` not enabled), external PostgreSQL (`EAZY_SDK_POSTGRES_DSN` unset) and absent local real-HTML fixture lanes. |
| `uv run mypy`; `uv run ruff check` | PASS: no issues in 278 source files; all lint checks passed. |
| `uv run python scripts/absence_audit.py` | PASS: removed identity/capability API, public low-level reachability, legacy paths, generated output and public documentation are clean. |
| `uv run python scripts/docs_freshness.py check`; `uv run python docs-site/scripts/validate_docs.py` | PASS: 60 API-backed pages are fresh; metadata/frontmatter/content are valid for 74 pages. |
| final `uv run pytest -q tests/unit/test_docs_examples.py tests/test_docs_freshness.py` | PASS: 21 passed after adding the shared-session SDK factory; documentation snippets and freshness metadata remain current. A preceding run including `tests/rewrite/test_phase10_absence.py` passed 22 tests. |
| `uv run sphinx-build -W --keep-going -b dirhtml -c docs-site docs-site/src/content/docs <temp>` | PASS: final build compiled all 74 sources without warnings; `llms-full.txt` was generated from 74 sources with 6,632 lines. |
| `npm run check`; `npm run build` from `docs-site/` | NOT APPLICABLE: `Test-Path docs-site/package.json` is false; Sphinx is the repository-defined renderer. |
| `uv lock --check`; `uv build --all-packages`; `uv run python scripts/package_audit.py dist` | PASS: lock is current; all six wheel/sdist pairs have matching metadata, licenses, typing markers, the Zapros boundary, required phase-29 package files and no removed API. |
| prospective snapshot from `git ls-files --cached --others --exclude-standard`; `uv sync --all-packages --locked`; `uv lock --check`; build and package audit | PASS in `C:\Users\user\AppData\Local\Temp\eazy-phase29-snapshot-971ca9991487481ebcf72a7aa44501f5`: all six editable packages sync and all twelve artifacts build/audit without workspace-only state. |
| install all six exact wheels into fresh CPython 3.13.12 and 3.14.3 environments; import high-level/advanced/plugins and assert removed names are unreachable | PASS on both supported Python versions; every distribution imports as `0.2.0a2`. The first probe invocation was a PowerShell `python -c` quoting error before Python could execute assertions; piping the unchanged probe to stdin produced the recorded passing runs. |
| final `git diff --check` | PASS: no whitespace errors. |

### Remaining work / blockers

None for phase 29. Publication, browser automation and an in-client rotating affinity lease remain
outside this phase's approved scope.

## Phase 29 release closure: Eazy SDK 0.2.0a3 (2026-09-02)

### State

Complete and published. The phase-29 breaking protection simplification is released from commit
`138561435fc06aaf0fc74a79f2f61af971586bb2` through annotated tag `v0.2.0a3` and GitHub
prerelease `381127280`. The release contains the six workspace wheels and six source
distributions. PyPI remains outside the established GitHub-only alpha scope.

### Release changes

- Raised the core distribution, all five plugins, runtime `__version__` and generated consumer
  fixture to `0.2.0a3`.
- Raised every first-party core/plugin dependency floor to `0.2.0a3` and regenerated `uv.lock`.
- Updated root installation examples to the immutable `v0.2.0a3` GitHub wheel URLs.
- Published release notes covering the high-level guard API, session ownership, advanced SPI split
  and removal of identity/capability declarations.

### Commands run

| Command / gate | Result |
|---|---|
| initial `git fetch --tags origin`; branch/tag/release inspection | PASS: local and remote `master` both started at `c204aaf`; latest release was `v0.2.0a2`; `v0.2.0a3` was free. |
| version sweep; `uv lock`; `uv lock --check` | PASS: all six workspace distributions resolve as `0.2.0a3`; no non-historical `0.2.0a2` reference remains. |
| clean-environment `uv run pytest -q` | PASS: 886 passed, 11 skipped in 151.20s. Skips remain the opt-in live WebSocket, external PostgreSQL and absent local real-HTML fixture lanes. |
| `uv run mypy`; `uv run ruff check` | PASS: no issues in 278 source files; all lint checks passed. |
| `scripts/absence_audit.py`; docs freshness; docs validation; strict Sphinx build | PASS: removed API remains absent, 60 pages are fresh, 74 pages are valid and all 74 sources build without warnings. |
| `uv build --all-packages --out-dir dist/release-0.2.0a3`; `scripts/package_audit.py` | PASS: exactly six `0.2.0a3` wheel/sdist pairs have valid alpha metadata, licenses, typing markers, the Zapros boundary, phase-29 package split and removed-API absence. |
| prospective tracked snapshot; locked all-package sync; lock check; build and package audit | PASS in `C:\Users\user\AppData\Local\Temp\eazy-release-a3-snapshot-36faaf38b50b4e1bb04dfd0ffe8efc0f`. |
| install all six exact local wheels into clean CPython 3.13.12 and 3.14.3 environments | PASS: every distribution and runtime import reports `0.2.0a3`; high-level and advanced protection surfaces import and removed names remain unreachable. |
| staged whitespace and secret-pattern scans; release commit and annotated tag | PASS: commit `1385614`; tag `v0.2.0a3` peels to the exact full commit. |
| `git push --atomic origin master v0.2.0a3` | PASS: branch and annotated tag were published atomically. |
| authenticated draft creation, 12 asset uploads, size/SHA-256 comparison, prerelease publication | PASS: release ID `381127280`, draft false, prerelease true, target commit exact and all server digests equal the local artifacts. |
| anonymous GitHub API/tag check; public download and SHA-256 comparison of all 12 assets | PASS: public metadata, branch/tag target, names, sizes and every downloaded digest match. |
| clean CPython 3.13.12 install from all six public GitHub wheel URLs | PASS: all distributions import and report `0.2.0a3`. |
| npm gates from `docs-site/` | NOT APPLICABLE: `docs-site/package.json` is absent; strict Sphinx is the repository-defined production build. |

### Published release

- Source commit: `138561435fc06aaf0fc74a79f2f61af971586bb2`
- Tag: `v0.2.0a3`
- GitHub prerelease: <https://github.com/0cherednoq/eazy-sdk/releases/tag/v0.2.0a3>
- Assets: 12 publicly downloaded and SHA-256 verified wheels/source distributions

### Remaining work / blockers

None for the GitHub alpha release. PyPI publication remains deferred under the established release
scope.

## Phase 30 — `SolveContext` transport ports and identity-bound managed state (2026-09-02)

### State

Complete in the working tree (not yet committed). Implements Phase 1 (items 1.1–1.7) of
`docs/eazy-sdk-remediation-plan.md`; plan document `30-solve-context-transport-ports.md`.
No compatibility alias, wrapper or second execution path was added; `SolveContext` was extended
additively with defaults.

### Delivered

- `ProtectedFetch` protocol and frozen `TransportIdentity` (`user_agent`/`proxy`/`impersonation`,
  credential-redacted `repr`, `fingerprint()`) in `eazy_sdk.protection.advanced`, re-exported from
  `eazy_sdk.protection` (exact allowlists now 21/51 names; fingerprints updated).
- `SolveContext.fetch`, `.identity`, `.request_headers`; `.deadline` is derived from the call
  timeout. The executor's `_RuntimeFetch` emits a raw `PreparedRequest` through `runtime.send`
  (same handler/proxy/cookie jar) and bypasses guards, reactions, replay, middleware and limiter.
- `_ManagedProtectionState.identity` stores the acquiring fingerprint; shared and local state that
  was acquired under another identity is dropped instead of applied. Managed state is now applied
  after dependency/auth/middleware patches so the checked identity includes injected headers.
- `HandlerProfile.impersonation`; curl_cffi handlers declare it from `impersonate=`.
- `eazy_sdk.redaction.redact_url_credentials()`.
- Docs: `api-reference/protection.mdx` (solve-context section) and `guides/protection/index.mdx`
  (solve through the same session); CHANGELOG `Unreleased` entry.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q tests/unit/test_phase30_solve_context_ports.py` | PASS: 10 passed (fetch via same handler, no recursion, sync parity, deadline, identity change invalidates session clearance, impersonation, redaction, offline context). |
| `uv run pytest -q` (full) | PASS: 896 passed, 11 skipped in 155.45s. Skips are the existing opt-in live WebSocket, external PostgreSQL and real-HTML fixture lanes. |
| `uv run mypy`; `uv run ruff check` | PASS: no issues in 279 source files; all lint checks passed. |
| `uv run python scripts/docs_freshness.py check` after `update` of 4 pages; `docs-site/scripts/validate_docs.py` | PASS: 60 pages fresh; 74 pages valid. |
| `uv run python scripts/absence_audit.py` | PASS. |
| `uv run --with-requirements docs-site/requirements.txt sphinx-build -W --keep-going -b dirhtml ...` | PASS: build succeeded, 74 sources, no warnings. |
| `git diff --check` | PASS. |
| basedpyright typing lanes (`test_phase21/25/28_typing`) | Initially FAILED for an environment reason (`Cannot find module './dist/pyright'`, also on the untouched tree); fixed with `uv pip install --reinstall --no-deps basedpyright`, then PASS (19 passed) and included in the full run above. |
| `ruff format --check` | NOT A GATE: pre-existing drift in 19 files (incl. `executor.py`, `advanced.py`) left untouched. |

### Remaining work / blockers

Acceptance against the `kad/` consumer (removal of its solver adapter and manual UA check) could
not be verified: `kad/` is not present in this checkout. Next: remediation-plan Phase 2.

## Phase 31 — simple guard layer (2026-09-02)

### State

Complete in the working tree (not yet committed, together with phase 30). Implements Phase 2
(items 2.1–2.6) of `docs/eazy-sdk-remediation-plan.md`; plan document
`31-simple-guard-layer.md`. No compatibility alias or second execution path: `Guard` lowers
through `challenge_guard()` into the existing typed policies and coordinator.

### Delivered

- `eazy_sdk/protection/guard.py`: `Guard[TChallenge]` (class-level `scope`/`cache`/`replay`/
  `revision`/`name`, declared `headers`/`query`/`body`, sync or async `solve()`,
  `self.solution(cookies=, headers=, query=, body=, expires_in=|expires_at=)`), `GuardSolution`
  (redacted repr, synthetic `header:<name>` selectors), `host()`, `operation()`.
- `challenge_guard()`: `cache: GuardCache` (`none`/`call`/`session` → per-match/per-call/
  session-scoped until-rejected), default whole-client scope, name inferred from the detector,
  solver as object (sync/async `solve()`) or function via `_CallableSolver`.
- `ClientConfig(guards=...)` lowered in `__post_init__` (field reset to `()`, `with_protection()`
  equivalent and idempotent); `client.invalidate_protection(*guards_or_names) -> int` on both
  client cores backed by `ExecutionRuntime.invalidate_protection()`.
- `eazy_sdk_presets` re-exports `host`/`operation` from core (`presets.host is protection.host`).
- Docs: protection guide rewritten around `Guard` + `guards=`; API reference for protection and
  clients; CHANGELOG `Unreleased`. Public allowlist fingerprints: `eazy_sdk.protection` 26
  names, `advanced` 53 names.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q tests/unit/test_phase31_guard_layer.py` | PASS: 12 passed (acceptance one-class scenario solved once per session, `guards=` idempotent lowering, declared headers + sync solve + undeclared rejection, cache none/call/session, inferred names, `expires_in`, invalidation by guard/name/all in async and sync clients, concurrent single-flight, redacted solve errors, core scope helpers). |
| `uv run pytest -q` (full) | PASS: 908 passed, 11 skipped in 150.53s (same opt-in skips as phase 30). Docs/surface/phase-31 focus re-run after the final docs edits: PASS. |
| `uv run mypy`; `uv run ruff check` | PASS: no issues in 281 source files; all lint checks passed. |
| `scripts/docs_freshness.py update` (12 pages) then `check`; `docs-site/scripts/validate_docs.py` | PASS: 60 pages fresh; 74 pages valid. |
| `uv run python scripts/absence_audit.py`; strict Sphinx build; `git diff --check` | PASS. |

### Remaining work / blockers

`kad/` consumer acceptance (collapsing `kad/security.py` + `kad/protection.py` into one `Guard`)
cannot be verified: the consumer is not in this checkout. Next: remediation-plan Phase 3 (advanced
cleanup; per user decision duplicates are removed without aliases).

## Phase 32 — advanced protection layer cleanup (2026-09-02)

### State

Complete in the working tree (uncommitted, together with phases 30–31). Implements Phase 3
(items 3.1–3.8) of `docs/eazy-sdk-remediation-plan.md`; plan document
`32-advanced-layer-cleanup.md`. Per the user's decision the duplicate names were removed without
deprecated aliases, in line with `AGENTS.md`.

### Delivered

- One solver protocol, requirement, binding, factory and registry; `solver_bindings` replaces the
  two registries on `ClientConfig`/`ExecutionRuntime`/`ProtectionBundle`. Removed names are
  listed in `32-advanced-layer-cleanup.md` and asserted absent by the phase-32 test.
- `ChallengePolicy`/`BeforeCallPolicy` frozen keyword-only self-validating dataclasses; presets
  subclass them; executor compile helpers no longer re-validate.
- `SolutionFields.bindings` public; detector signals typed from the detector annotation.
- `ChallengeMalformedError` and `AmbiguousChallengeError` raised from the signal outcome.
- Per-policy replay budgets; `OperationReference` typing plus `.declaration` on declarations and
  bound API methods; loop-agnostic, thread-safe `_ProtectionLockRegistry`.
- OpenAPI generator emits `SolverRequirement[Any, R]`; `museum_sdk` snapshot and the phase-21
  codegen hash re-pinned. Docs (api-reference protection/clients, protection guide, historical
  phase 17/24 snippets) and CHANGELOG updated.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q tests/unit/test_phase32_advanced_cleanup.py` | PASS: 7 passed (absence of duplicates, validating dataclass policies incl. presets, detector model inference, malformed/ambiguous errors, per-policy budget, cross-loop/thread single-flight). |
| `uv run pytest -q` (full) | 914 passed, 1 failed, 11 skipped in 157.93s; the failure was `test_documented_eazy_sdk_imports_resolve_to_real_symbols` on a historical import snippet in `docs/implementation/17-…md`. Snippets in phase 17/24 docs were renamed; focused re-run of docs/import/surface/phase-32 tests: PASS (35 passed). |
| `uv run mypy`; `uv run ruff check` | PASS: no issues in 282 source files; all lint checks passed. |
| docs freshness `update` (7 pages) + `check`; `validate_docs.py`; `absence_audit.py`; strict Sphinx; `git diff --check` | PASS: 60 pages fresh, 74 valid, audit clean, build without warnings, no whitespace errors. |

### Remaining work / blockers

None for phase 32. Next: remediation-plan Phase 4 (public surface and entry point); the facade
decision (4.4) needs the user's choice before anything is deleted.

## Phase 33 — public surface and entry point (2026-09-02)

### State

Complete in the working tree (uncommitted; 4.5 partially). Implements Phase 4 of
`docs/eazy-sdk-remediation-plan.md`; plan document `33-public-surface-and-entry.md`.

### Delivered

- Transport factories `Client.httpx/requests/curl_cffi`, `AsyncClient.httpx/curl_cffi`; bare
  `Client(base_url=...)` owns a Zapros `StdNetworkHandler`.
- Root re-exports of request placements and response cases; README/quickstart use one import
  block, `response=Json()` and `Client.httpx()`.
- One facade: `SyncSdk`/`AsyncSdk` merged into `SyncApi`/`AsyncApi` (`api_group`, `from_client`,
  `from_handler`, ownership, context managers); `eazy_sdk/sdk.py` deleted; raw verb methods removed
  from both clients (`request()` remains); generator emits `class X(SyncApi|AsyncApi)`.
- `ClientConfig(protection=ProtectionBundle | None)`, `config.bundle`, `ProtectionBundle.merge()`;
  `_internal` has no `__all__`; `codegen` exports `session_auth`/`session_scheme`/`ProtectionBundle`.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q tests/unit/test_phase33_public_surface.py` | PASS: 7 passed. |
| `uv run pytest -q` (full) | 920 passed, 2 failed, 11 skipped in 155.95s. Failures: two import-isolation tests, because `api.py` imported Zapros handler types at module level after the facade merge; moved under `TYPE_CHECKING`; focused re-run of both isolation tests plus phase-25/33 suites: PASS. |
| `uv run mypy`; `uv run ruff check` | PASS: 282 source files, no issues. |
| docs freshness `update` (11 pages) + `check`; `validate_docs.py`; `absence_audit.py`; strict Sphinx | PASS. |

### Remaining work / blockers

Surface metric 4.5 (≤300 names) not met: 889 names across public `__all__` (root 61). Phase 6
removes accounts/storage/extraction from the core; further pruning of websocket/crypto
re-exports is not scheduled. Next: Phase 5 (layering).

## Phase 34 — layering (2026-09-02)

### State

Complete in the working tree (uncommitted). Implements Phase 5 of
`docs/eazy-sdk-remediation-plan.md`; plan document `34-layering.md`.

### Delivered

- `eazy_sdk._internal` removed; `eazy_sdk.core` (kernel, errors, http, http_plan, ports) and
  `eazy_sdk.compile` (http_compiler, http_operation, input) with explicit re-exports and no
  `__all__`; every package/plugin/test/script/doc import rewritten.
- `eazy_sdk.policies` (`CallOptions`, `RetryPolicy`), `eazy_sdk.auth.lifecycle`
  (`LifecycleGraph`), lazy Pydantic import in `extraction`, `core.ports.CryptoProfile` used by
  the compiler; `CompiledContract` is typing-only in `request.prepared`, `protection.advanced`,
  `auth.core`.
- Layer contracts enforced by `tests/unit/test_phase34_layering.py` (AST import scan) instead
  of an external import-linter dependency.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` (full) | 927 passed, 1 failed, 11 skipped in 157.97s. The failure was the new crypto-port assertion (instantiating `PayloadCrypto` without a transform); rewritten as a structural check; `test_phase34_layering.py` + `test_ws01_common_kernel.py` re-run: 13 passed. |
| `uv run mypy`; `uv run ruff check` | PASS: 287 source files, no issues. |
| docs freshness `check` (no stale pages); `validate_docs.py`; `absence_audit.py`; strict Sphinx; `git diff --check` | PASS. |

### Remaining work / blockers

None for phase 34. Next: Phase 6 (scope extraction into plugins).

## Phase 35 — scope: plugins and websocket split (2026-09-02)

### State

Complete in the working tree (uncommitted). Implements Phase 6 of
`docs/eazy-sdk-remediation-plan.md`; plan document `35-scope-plugins.md`.

### Delivered

- `eazy-sdk-accounts` (`plugins/accounts`): registration, HTTP registration transport and the
  storage layer left the core; session lifecycle stayed as `eazy_sdk.auth.session`.
- `eazy-sdk-html` (`plugins/html`): extraction and its exceptions left the core;
  `eazy_sdk.response.Html` loads the plugin on demand with an install hint.
- Crypto stays in the core behind `core.ports.CryptoProfile`.
- `websocket/runtime.py` split into state + four subsystem mixins; `runtime.py` is 248 lines.
- Packaging: two new workspace members, extras `accounts`/`html`, `uv.lock` regenerated,
  `package_audit.py` covers 8 distributions; docs sources and snippets migrated.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` (full, after 6.1–6.3) | PASS: 928 passed, 11 skipped in 156.23s. |
| `uv run pytest -q` (full, after 6.4) | PASS: 928 passed, 11 skipped in 154.77s. |
| `uv run mypy`; `uv run ruff check` | PASS (291 source files). |
| `uv lock`; `uv sync --all-packages`; `uv build --all-packages`; `scripts/package_audit.py` | PASS: 8 wheel/sdist pairs; audit clean. |
| `python -I -c "import eazy_sdk, eazy_sdk.auth, eazy_sdk.clients"` module scan | PASS: `eazy_sdk_accounts`, `eazy_sdk_html`, `parsel`, `sqlmodel` are not imported; core wheel has no `accounts/storage/extraction/_internal` (87 modules). |
| docs freshness/validation; `absence_audit.py`; strict Sphinx; `git diff --check` | PASS. |

### Remaining work / blockers

Public-name metric: 743 names across `__all__` of public modules after phase 35 (from 889);
the ≤300 target of plan item 4.5 is still not met. Next: Phase 7.

## Phase 36 — hygiene (2026-09-02)

### State

Complete in the working tree (uncommitted). Implements Phase 7 of
`docs/eazy-sdk-remediation-plan.md`; plan document `36-hygiene.md`. With this phase every item
of the remediation plan is implemented except the numeric target of 4.5 (public names ≤300;
current 743), which is recorded as an open metric.

### Delivered

- `EazySdkError`/`ConfigurationError` base classes in `eazy_sdk.core.errors`; every public
  exception derives from the base and ends with `Error` (renames without aliases; the
  duplicated `SessionConfigurationError` merged into `eazy_sdk.auth.session`;
  `EazySDKError` → `EazySdkContextError`).
- Sync client: per-thread reusable `asyncio.Runner` (`clients/_core.py::_SyncRunner`),
  `EventLoopConflictError` inside a running loop, loop-safe close during GC.
- `ScopedMiddleware` generic contract; WebSocket applications expose `implementation`; storage
  hooks renamed to observers (`eazy_sdk_accounts.storage.observers`, `observers=`).
- `_ClientCore` shared by sync/async clients; stability matrix in the extensions reference;
  sync event-loop note in the clients reference.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q tests/unit/test_phase36_hygiene.py` | PASS: 6 passed. |
| `uv run pytest -q` (full) | PASS: 934 passed, 11 skipped in 156.51s. An earlier pass exposed order-dependent unraisable warnings (`shutdown_asyncgens` never awaited) from runners closed while another loop ran; fixed in `_close_runner`, re-run green. |
| `uv run mypy`; `uv run ruff check` | PASS: 293 source files, no issues. |
| docs freshness (`update` 7 pages, `check`); `validate_docs.py`; strict Sphinx; `absence_audit.py`; `uv build --all-packages` + `package_audit.py`; `git diff --check` | PASS. |

### Remaining work / blockers

- Plan item 4.5 metric (≤300 public names) is not met: 743 names across public `__all__`
  (`websocket` 113, `crypto` 67 + `crypto.core` 68, `codegen` 59, `request` 50,
  `protection.advanced` 49). Pruning these re-exports is a product decision.
- Consumer acceptance against `kad/` (phases 30–31) is unverified: the consumer is not in this
  checkout.
- Nothing is committed; the working tree holds phases 30–36 together with the regenerated
  `uv.lock`, two new workspace members and moved test trees.

## Release closure: Eazy SDK 0.2.0a4 (2026-09-03)

### State

Published. Phases 30–36 were committed as `16bd588` on branch `remediation-plan-phases-1-7`,
the version bump as `f2485c7`; `master` was fast-forwarded and annotated tag `v0.2.0a4` points at
`f2485c736941415477de1963a773bd40b4da5bb7`. GitHub prerelease `381757027` carries 8 wheels and
8 source distributions. PyPI remains outside the GitHub-only alpha scope.

### Commands run

| Command / gate | Result |
|---|---|
| version sweep (core, 7 plugins, `__version__`, consumer fixture, README URLs); `uv lock`; `uv lock --check`; `uv sync --all-packages` | PASS: every workspace distribution resolves as `0.2.0a4`. |
| `uv run pytest -q` | PASS: 934 passed, 11 skipped in 177.74s. |
| `uv run mypy`; `uv run ruff check`; `scripts/absence_audit.py` | PASS. |
| `uv build --all-packages --out-dir dist/release-0.2.0a4`; `scripts/package_audit.py` | PASS: 16 artifacts (8 wheel/sdist pairs). |
| `git push --atomic origin master v0.2.0a4` | PASS. |
| REST API draft release + 16 uploads + publish (`gh` is not installed; stored git credential used) | PASS: release `381757027`, `draft=false`, `prerelease=true`. |
| anonymous download of all 16 assets, size + SHA-256 comparison | PASS: 16/16, no mismatches. |
| clean CPython 3.13 venv install of the public core and presets wheels; import | PASS: `0.2.0a4`. |

## Phases 37–41 — 0.2.0a5 remediation (2026-09-03)

### State

Complete in the working tree on branch `a5-plan` (uncommitted). Implements every in-scope item of
`docs/eazy-sdk-v0.2.0a5-plan.md`: phase 2 (packaging + CI, `37-packaging-and-ci.md`), phase 1
(transport identity, `38-transport-identity-proxy.md`), phase 3 (protection contracts and names,
`39-protection-contracts-and-names.md`), phase 4 (migration page, `40-migration-doc.md`), phase 5
(runtime hygiene, `41-runtime-hygiene.md`). Deferred by the plan: C2 (`ClientConfig` grouping);
rejected as false: C4-5 (`LifecycleGraph` is not used by the accounts plugin).

### Delivered

- `HandlerProfile.proxy` declared by handlers; `proxy=` on `Client.httpx/requests/curl_cffi` and
  `AsyncClient.httpx/curl_cffi`; `EmitOptions` reduced to `timeout`; one transport identity per
  attempt computed before managed state (solve and apply agree); redacted `HandlerProfile.__repr__`.
- `eazy-sdk-html` requires `parsel`; `eazy-sdk-sqlmodel` declares `sqlalchemy`/`pydantic`;
  `package_audit.py` checks unconditional imports against mandatory dependencies (with
  `OPTIONAL_MODULES` for install-hinted handler modules); `extras_smoke.py`; GitHub Actions
  `ci.yml` (3.13/3.14) and `release.yml`.
- `ChallengeMalformedError` → `ChallengeParseError`; `ProtectedFetch` contract (no redirects,
  full timeout, shared jar, no pipeline); `SolveContext.remaining()`; single-flight semantics
  corrected (`session` only) in docstrings and docs.
- `more/migration` page (a3→a4, a4→a5) with a table-driven accuracy test; README/CHANGELOG links.
- `_SyncRunner` with `asyncio.Runner(loop_factory=new_event_loop)` (thread loop untouched),
  closed-check under the lock; `_MAX_POLL_DELAY = 0.01`; `generated_session_auth/scheme` public
  and distinct from `eazy_sdk.auth.session_auth/scheme`; OpenAPI generator emits the new names.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` (full, after every phase) | PASS: 976 passed, 11 skipped in 158.62s (a4 base: 934). New: `test_phase37_identity_proxy.py` (7), `test_phase37_protection_contracts.py` (6), `test_phase37_migration_doc.py` (24), `test_phase37_runtime_hygiene.py` (5). |
| `UV_PROJECT_ENVIRONMENT=.test-tmp/venv314 uv run --python 3.14 pytest -q -x` (a4 base, CI matrix feasibility) | PASS: 934 passed, 11 skipped in 164.59s. |
| `uv run mypy`; `uv run ruff check` | PASS: 298 source files, no issues. |
| `uv run pytest -q tests/unit/test_phase37_runtime_hygiene.py tests/unit/test_phase36_hygiene.py -W error::pytest.PytestUnraisableExceptionWarning` | PASS: 11 passed. |
| `scripts/docs_freshness.py update` (7 pages) + `check`; `docs-site/scripts/validate_docs.py`; strict Sphinx (`-W --keep-going -b dirhtml`) | PASS: 60 pages fresh; 75 pages valid; build succeeded. |
| `scripts/absence_audit.py`; `git diff --check` | PASS. |
| `uv lock`; `uv build --all-packages --out-dir .test-tmp/a5-dist`; `scripts/package_audit.py .test-tmp/a5-dist` | PASS: 16 artifacts; audit OK including the new unconditional-import rule (it flagged `eazy-sdk-sqlmodel` transitive imports and the three handler modules before `OPTIONAL_MODULES`/explicit deps were added). |
| `scripts/extras_smoke.py .test-tmp/a5-dist` | PASS: 14/14 (core + 9 extras + 4 standalone plugins) install into fresh venvs and run their smoke program. |
| YAML parse of `.github/workflows/{ci,release}.yml` | PASS. Workflows are not executed locally; first real run happens on push. |

### Remaining work / blockers

- Version is still `0.2.0a4` everywhere; the bump, `CHANGELOG` date, tag and release are a
  separate step (release workflow now does the GitHub part on tag push).
- CI has never run on GitHub yet; the first push to `master` validates the workflows.
- Plan item C2 (`ClientConfig` grouping) and the a3-plan metric 4.5 (≤300 public names) stay open.

## Release closure: Eazy SDK 0.2.0a5 (2026-09-05)

### State

Phases 37–41 committed as `40bfcc1` on branch `a5-plan`; version bumped to `0.2.0a5` in the
core, all seven plugins, `__version__`, the consumer fixture, README install URLs and the
`CHANGELOG` section date. The GitHub release is produced by `.github/workflows/release.yml` on the
`v0.2.0a5` tag push (first real run of the new workflows); the result is verified anonymously
after the push (see the journal of `docs/eazy-sdk-v0.2.0a5-plan.md`).

### Commands run

| Command / gate | Result |
|---|---|
| version sweep; `uv lock`; `uv lock --check`; `uv sync --all-packages` | PASS: every distribution resolves as `0.2.0a5`. |
| `uv run pytest -q` | PASS: 976 passed, 11 skipped in 174.60s. |
| `uv run mypy`; `uv run ruff check`; `scripts/absence_audit.py`; `git diff --check` | PASS. |
| `scripts/docs_freshness.py check`; `validate_docs.py`; strict Sphinx | PASS: 60 fresh, 75 valid, build succeeded. |
| `uv build --all-packages --out-dir dist/release-0.2.0a5`; `package_audit.py`; `extras_smoke.py` | PASS: 16 artifacts; audit OK; 14/14 extras. |
| `git push --atomic origin master v0.2.0a5` (`b4a81a2`) | PASS. Triggered CI (tag + master) and Release. |
| Release workflow run 33972421246 | PASS: release `383273055`, `prerelease=true`. Two defects of the first run fixed in `cfeb02b`: `actions/checkout` peels the annotated tag, so the body was the commit trailers (now re-fetched and asserted annotated); `files: dist/release/*` also uploaded uv's `.gitignore` (now `*.whl` + `*.tar.gz`). The published release was corrected in place via the REST API (asset deleted, body replaced with the CHANGELOG section). |
| anonymous download of the 16 published assets; `package_audit.py`; `extras_smoke.py` on them | PASS: audit OK; 14/14 extras install and run from the published wheels. Local Windows-built wheels differ by hash from the Linux-built ones (CRLF sources from `core.autocrlf=true`); the published Linux build is canonical. |
| CI workflow runs 33972421243/33972421287 (first run) | FAIL: 37 errors `FileNotFoundError: .test-tmp/pytest` (basetemp parent missing on a fresh clone) and 3 basedpyright proofs (`0 errors, N warnings`, exit code 1 on Linux for `reportUnusedCallResult`). Packaging job PASS on both. Fixed in `cfeb02b`: `tests/conftest.py::pytest_configure` creates `.test-tmp`; typing proofs assert "no type errors" rather than the checker's exit code. |
| CI run 33972822479 (`cfeb02b`) | py3.13 PASS (all steps), packaging PASS; py3.14 FAIL: `test_sync_runner_never_installs_a_current_loop_in_a_fresh_thread` used `asyncio.get_event_loop_policy()` (deprecated on 3.14, raised in the worker thread). Reproduced locally with `--python 3.14`; fixed in `375373b` (probe with `asyncio.get_event_loop()`), green on 3.13 and 3.14 locally. |
| CI run 33973099904 (`375373b`) | PASS: checks (py3.13), checks (py3.14), packaging all green. First fully green CI run of the repository. |

## Architecture refactor 0.3.0 planning (2026-09-05)

### State

Planning only; no implementation started. Base is `375373b` (tag `v0.2.0a5`). The plan comes from
a source-level review of `unihttp` and `descanso` and an audit of the debt accumulated during
generation; findings F1–F12 and the phase split are recorded in
`docs/eazy-sdk-architecture-refactor-plan.md`. The declaration form of phase 42 was revised on
2026-09-05 and again on 2026-09-06: transports as public root attributes were rejected first, then
the separate `Domain` type as well — service attributes now live on the router class and are shared
through an ordinary base class.
The remaining declaration decisions were closed by the owner the same day — `SyncRoot`/`AsyncRoot`,
a composition-only root (no operations, not a `SyncApi` subclass) and `bind(...)` for
assembly-time binding — so nothing blocks phase 42 from starting. Phase 48 (wire pipeline) was
added on 2026-09-06 after the serialization question, and phase 49 (protocol envelopes) the same
day after the owner asked whether descanso's JSON-RPC builder and per-position dumpers are worth
taking: the task was accepted, the second-builder solution was not.

| Phase | State | Evidence | Remaining work / blockers |
|---|---|---|---|
| 42 | complete | Service attributes (`base_url`, `protocol`, `errors`, `security`, `signing`, `crypto`, `allow`) read from the router class and its MRO, shared through an ordinary base class; root composition with `bind(cls, client=…, base_url=…)` keyed by class; one defaults chain (root → router MRO → operation); only routers in the SDK's public surface. F1 confirmed at `api.py:483`. | Done 2026-09-06 (`eazy_sdk/root.py`, `api.py`, `executor.py:_service_base_url`). Breaking for every existing SDK root declaration. Two forms rejected by the owner: transports as root attributes with `on=` (2026-09-05) and a separate `Domain` type (2026-09-06 — overhead, an ordinary base class does the same). |
| 43 | complete | Identity/session scope owns auth, `SessionStore`, `key_provider`, dependencies; `ClientConfig.auth` removed; SSO through the existing `SessionBridge`. F2 confirmed at `clients/config.py:31`. | Done 2026-09-06 (`eazy_sdk/identity.py`, `ExecutionCore.identity`). |
| 44 | complete | `ClientConfig` grouped by owner (`resilience`/`security`/`wire`/`hooks`); frozen-dataclass mutation removed; explicit dumper/loader roles. Closes the C2 item deferred in the a5 plan. | Done 2026-09-06 (`Resilience`/`Security`/`Hooks`, `eazy_sdk/serialization.py`). |
| 45 | complete | `AttemptState` plus four `AttemptPolicy` implementations replace the 500-line `_attempts` loop (`executor.py:745-1245`). | Done 2026-09-06 (`eazy_sdk/clients/attempts.py`, `_AttemptRun`). No test expectation was rewritten. |
| 46 | complete | Sans-io core and two thin sync/async drivers; `_SyncRunner` and `EventLoopConflictError` removed. | Done 2026-09-06 (`eazy_sdk/driver.py`). Sync latency 3248 -> 3124 us per call. |
| 47 | complete | `kernel` reduced by measurement, ghost modules resolved, private modules recomposed by responsibility, `eazy_sdk.__init__` down to ≤40 names (currently 55). | Not started. Depends on 45–46. |
| 48 | pending | One meaning for "wire" (F10: six today across three packages), a single `Wire` declaration, the projection → crypto → encode → sign pipeline expressed as data, the four-transform serialization split (contract vs implementation), the half-dead `WireProfile` (F11: only `protocol` is ever read; JSON parameters are hardcoded in five places), the removal of the unused scoped-signing API (F9, decided 2026-09-06 — signing is chosen by declaration site, never by request address), and pluggable HTML/XML parser backends (`Serialization(html=…)`; parsel is hardcoded at `plugins/html/eazy_sdk_html/schema.py:9,102`) with a compile-time selector-capability check. | Not started. Depends on 42–44; independent of 45–47. One open question: connect scoped signing to domains or delete it. |
| 49 | pending | Protocol envelopes: `ProtocolMessage`/`Envelope`/`CorrelationKey` lifted out of `eazy_sdk/websocket/` (F12), an envelope stage in the phase-48 pipeline, `JsonRpc`, `@api.rpc` next to `ws.call`, RPC errors through the existing `Responses` + `condition` (`response/cases.py:316`). | Not started. Depends on 48. Batch and GraphQL-over-HTTP are declared non-goals with reasons; two open questions (GraphQL partial success, JSON-RPC notifications). |

### Commands run

| Command / gate | Result |
|---|---|
| — | None yet; planning documents only. |


## Phase 42 — service mixins and root composition (2026-09-06)

### State

Complete. Base is `375373b`. The service address moved from the client to the router: `SyncApi`/
`AsyncApi` read `base_url`, `errors`, `security`, `signing`, `crypto`, `crypto_wire` and `allow`
from their own class and MRO, and `_OperationDeclaration.base_url` carries the resolved address to
`executor.py:_service_base_url`, where a router address beats the client's. `SyncRoot`/`AsyncRoot`
(`eazy_sdk/root.py`) are composition only — no operations, not subclasses of `SyncApi`/`AsyncApi`.

### Delivered

- **42.1** `_service_defaults_of()` collects service attributes over the MRO; two unrelated bases
  declaring different values is a declaration error, not a silent MRO win. `base_url` must be an
  absolute URL or empty.
- **42.2** `SyncRoot`/`AsyncRoot`: default client, `bindings=`, lazy group registry, lifecycle.
  `Root(client)` borrows; `Root.from_handler(...)` creates and owns one client; `close()`/`aclose()`
  is idempotent and touches only what the root created. `from_client`/`from_handler`/`close`/
  `aclose` are gone from `SyncApi`/`AsyncApi`. The root registers the scoped SDK factory through the
  new `_ClientCore._register_sdk_factory`, so `bind_sdk` no longer builds a throwaway root.
- **42.3** `bind(cls, client=…, base_url=…)` matched over the router MRO, most specific wins, per
  field; duplicate targets, ambiguous siblings and targets matching no router are assembly errors.
- **42.4** One chain: root → router MRO → operation. `resolve_for(api)` merges once per router
  instance and caches the declaration; `resolve()` is no longer called per request.
- **42.5** Declaration/assembly checks: attribute conflict, relative `base_url`, wrong api kind,
  scheme or signature outside `allow` (checked eagerly for every operation when the root is built),
  `api_group` on a router, operation on a root class.
- **42.6** Absence test: a root exposes only its groups plus `close`/`from_handler`.
- **42.7** `tests/unit/test_phase42_composition.py` (23 tests); examples `docs/store_sdk.py` and
  `dummyjson_session_auth.py` ported; OpenAPI generator emits `SyncRoot`/`AsyncRoot` roots with a
  `bindings=` passthrough and the museum snapshot was regenerated; docs updated and the new page
  `guides/multi-service.mdx` added to the toctree.

Three decisions beyond the original wording, recorded in `42-service-mixins-and-root-composition.md`:
`ApiDefaults` removed from the public surface (it was the third defaults mechanism), `api_group`
allowed only on a root, and `Serialization(models=…, json=…, html=…)` deferred to phase 48 (holding
it next to `ClientConfig.models` would be a second path for one setting).

Two pre-existing gate failures on `375373b` were fixed here: the python blocks of
`42-service-mixins-and-root-composition.md`, `48-wire-pipeline.md` and `49-protocol-envelopes.md`
did not import `api` / did not parse, so `test_documented_http_decorator_blocks_are_self_contained`
was red before this phase started.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` | PASS: 1007 passed, 11 skipped in 159.94s. |
| `uv run mypy` | PASS: no issues in 300 source files. |
| `uv run ruff check` | PASS. |
| `uv run python scripts/absence_audit.py` | PASS. |
| `uv run python scripts/docs_freshness.py update` + `check` | PASS: 61 pages fresh. |
| `uv run python docs-site/scripts/validate_docs.py` | PASS: 76 pages. |
| `uv run --group docs sphinx-build -W --keep-going -b dirhtml -c docs-site …` | PASS: 76 sources, build succeeded. |

### Remaining work / blockers

None for this phase. `Serialization` is carried into phase 48 together with the removal of
`ClientConfig.models`/`profile`.

## Phase 43 — identity and session scope (2026-09-06)

### State

Complete. Authentication left the transport. `eazy_sdk/identity.py` holds the public `Identity`
(auth bindings, `key_provider`, `dependencies`, observer) and the private `_IdentityScope` its
runtime view; `ClientConfig` and `ExecutionRuntime` lost all four fields and `ExecutionCore` gained
`identity=`. One `ExecutionRuntime` — with its pool, cookie jar and guard session — still belongs
to the client, so two identities over one client do not duplicate the transport:
`_ClientCore._core_for(scope)` builds only a new coordinator.

### Delivered

- **43.1/43.2** `Identity` is declared where `ClientConfig(auth=…)` used to be: on the root
  (`ShopSdk(client, identity=…)` or the class attribute `identity = Identity(...)`, constructor
  wins) and on a directly bound router (`UsersApi(client, identity=…)`). The root hands its groups
  the already-resolved `_IdentityScope` through the private `scope=` parameter, so exactly one
  owner registers the scoped-SDK factory.
- **43.3** `ClientConfig.auth`, `.key_provider`, `.dependencies` and `.observer` are removed with
  no alias; `client.bind_sdk` and `_register_sdk_factory` are gone with them, because the auth
  providers now live on the identity. `bind_session_lifecycle` registers the factory there.
- **43.4** One identity serves several services over one client or several — proved by
  `test_one_session_serves_two_services_on_two_clients`. A second identity over the same client
  gets its own session (`test_a_second_user_reuses_the_client_and_gets_its_own_session`).
- **43.5** The service allowlist (`allow`, phase 42.1) is enforced eagerly when the root is
  assembled; cookies stay in the client's jar, host-scoped per RFC 6265.
- **43.6** `tests/unit/test_phase43_identity.py` (12 tests); the whole existing auth suite is green
  with only its client assembly changed; docs updated across `auth/*`, `signing`,
  `guides/dependencies`, `api-reference/clients`, `guides/multi-service`, the migration page and
  the two implementation references.

Two form decisions beyond the plan's wording, recorded in `43-identity-and-session-scope.md`: the
`session()` root descriptor is not introduced (`session_auth(...)` / `SessionScheme.configure(...)`
already build the one `Auth`, a third spelling would be a second declaration path), and `identity=`
is accepted by routers as well as roots, so the direct binding phase 42 preserved does not lose
authentication. `SessionProvider.bind_sdk_factory` no longer overwrites an explicitly declared
`SessionAuth.sdk_factory`; only the `unbound_sdk` placeholder is replaced.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` | PASS: 1024 passed, 11 skipped in 158.55s (includes the localhost auth conformance suite). |
| `uv run mypy` | PASS: no issues in 302 source files. |
| `uv run ruff check` | PASS. |
| `uv run python scripts/absence_audit.py` | PASS. |
| `scripts/docs_freshness.py update` + `check` | PASS: 61 pages fresh. |
| `docs-site/scripts/validate_docs.py` | PASS: 76 pages. |
| strict Sphinx (`-W --keep-going -b dirhtml`) | PASS: build succeeded. |

### Remaining work / blockers

None. `ClientConfig` now carries transport policy only, which is what phase 44 regroups.

## Phase 44 — client config by owner, serialization on the root (2026-09-06)

### State

Complete. `ClientConfig` is `resilience` / `security` / `hooks` plus the host-scoped `crypto`
registry that phase 48.4 still has to rule on. `Resilience` validates the budgets and builds
`call_options()`; `Security` checks the bundle type and lowers installable guards in
`Security.of(...)`; `Hooks` carries middleware. `object.__setattr__` is gone from the module, and
`guards` is no longer a public field that is always empty.

`eazy_sdk/serialization.py` adds `Serialization` (model adapters, plus the current `WireProfile`
until phase 48 replaces it). `ExecutionRuntime` lost `models` and `profile`; `ExecutionCore` gained
`serialization=`. It is declared on the SDK root — constructor argument or class attribute — and on
a directly bound router, exactly like `identity=` and for the same reason.

### Delivered

- **44.1** Three frozen groups, each with its own invariants; `ClientConfig.call_options()` comes
  from `resilience`. `Hooks` holds middleware only, because phase 43 moved the observer to
  `Identity`. `crypto` stayed a top-level field: putting host-scoped payload-crypto rules next to
  the guard would mix owners inside one group, which this phase's acceptance forbids.
- **44.2** `Security.of(*guards)` lowers at construction; `with_protection()` still adds guards to a
  finished configuration and still rejects a duplicate policy identity.
- **44.3** `models` and `profile` left `ClientConfig` for `Serialization`. No separate
  `RequestDumper`/`ResponseLoader` protocols were introduced: `ModelAdapterRegistry` already is that
  role, and a second interface over it would be a second path; substitutability is what the task
  wanted, and `Serialization.models` provides it.
- **44.4** The default-vs-DI rule is invariant 27 in `docs/implementation/README.md` and a table in
  `sdk-authoring-reference.md`.
- **44.5** `tests/unit/test_phase44_config_groups.py` (11 tests), including a root that swaps the
  model adapter and changes the request body without touching a declaration. Every construction
  site in tests, examples, plugins and docs was regrouped; the OpenAPI generator emits
  `Security(...)` and threads `serialization=`.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` | PASS: 1035 passed, 11 skipped in 160.79s. |
| `uv run mypy` | PASS: no issues in 304 source files. |
| `uv run ruff check` | PASS. |
| `uv run python scripts/absence_audit.py` | PASS. |
| `scripts/docs_freshness.py update` + `check` | PASS: 61 pages fresh. |
| `docs-site/scripts/validate_docs.py` | PASS: 76 pages. |
| strict Sphinx (`-W --keep-going -b dirhtml`) | PASS: build succeeded. |

### Remaining work / blockers

`ClientConfig.crypto` waits for phase 48.4: host/path-scoped payload-crypto rules are a second way
to say what the operation and the router already say by place.

## Phase 45 — attempt state machine (2026-09-06)

### State

Complete. The 500-line `_attempts` loop is a delegate to `_AttemptRun`, the object of one
logical call, and the flat locals it used to carry are `AttemptBudgets` and `AttemptState` in the
new pure module `eazy_sdk/clients/attempts.py`. No method in `eazy_sdk/clients/executor.py` is
longer than 80 lines, and a test asserts it.

### Delivered

- **45.1** `AttemptState` (number, kind, reason, url, method override, body rule, retries,
  budgets) is immutable; a transition is a new instance. `TransportFailure` describes a handler
  error as data instead of an exception used as control flow.
- **45.2** Five policies, not four: `TransportRetryPolicy` and `ResponseRetryPolicy` are separate
  because a handler error and a retryable response are different inputs, though they share
  `budgets.transport`. `RedirectPolicy`, `AuthRefreshPolicy` and `ProtectionPolicy` each spend
  only their own budget, and the hard limit is fixed when the call starts.
- **45.3** `_AttemptRun` splits the attempt into `_prepare` → `_attempt` → `_route` → `_commit`;
  `_commit` performs the one effect the deciding policy asked for (backoff, session refresh,
  challenge solve). `ExecutionCore.execute`, `_compile_http_crypto` and `_before_call_state` were
  split as well, since the criterion covers the file.
- **45.4** `tests/unit/test_phase45_attempt_policies.py` decides eight policy cases with no
  client, handler or event loop, and asserts the module imports none of them.
- **45.5** The existing sync/async behavioural suite is green with no expectation rewritten;
  `test_every_attempt_prepares_and_signs_again` counts key-provider calls and distinct signatures.

One defect the refactor introduced and the suite caught: deriving `hard_limit` from the remaining
budgets made the limit shrink as they were spent, so a call that refreshed its session ran out of
attempts. It is a fixed field of `AttemptBudgets` now, and `base` reports the slack it grants
beyond every policy budget.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` | PASS: 1056 passed, 11 skipped in 159.99s. |
| `uv run mypy` | PASS: no issues in 306 source files. |
| `uv run ruff check` | PASS. |
| `uv run python scripts/absence_audit.py` | PASS. |
| `scripts/docs_freshness.py check` | PASS: 61 pages fresh. |

### Remaining work / blockers

None. The policies decide and the coordinator performs the effects, which is the split phase 46
needs to lift the runner out of coroutines.

## Phase 46 — two drivers, no private event loop (2026-09-06)

### State

Complete. `eazy_sdk/driver.py` holds both drivers over one execution specification. The
asynchronous driver is the event loop. The synchronous driver is `run_sync`, which steps the same
coroutine to completion **without creating a loop at all**: on a synchronous transport nothing in
the pipeline suspends, so every `await` resolves in place. What genuinely waits — retry backoff,
rate-limit delay, a contended protection lock — goes through `driver.sleep`, which blocks the
thread under the synchronous driver and yields to the loop under the asynchronous one.

`_SyncRunner`, `_close_runner` and `EventLoopConflictError` are gone, and with them the read of
stdlib's private `runner._loop`. `scripts/absence_audit.py` keeps them gone.

### Delivered

- **46.1/46.2** One specification, two drivers. The core was **not** turned into an effect
  generator: `build_request`/`on_response` without `await` would require synchronous extension
  points, and `AuthService.acquire`, `ChallengeSolver.solve` and middleware are declared
  `async def`, with auth services calling SDK operations through `context.sdk`. Making them
  synchronous for a synchronous client is a public contract change well outside this phase, and it
  is not needed for its goal: those coroutines do not suspend on a synchronous transport.
- **46.3** The synchronous client is callable from inside a running loop, and installs no loop of
  its own anywhere. The narrowing it buys is explicit: a callback that awaits real asynchronous
  I/O raises `SynchronousSuspensionError` instead of quietly spinning a private loop.
- **46.4** WebSocket stays asynchronous on purpose — the connection is the state, and
  `zapros.websocket.aconnect_ws` is the asynchronous boundary. A test records that, rather than
  silence.
- **46.5** `tests/unit/test_phase46_drivers.py` (11 tests): a synchronous call inside a live loop,
  no loop in a fresh thread, the runner's absence, the driver completing and refusing coroutines,
  the wait behaving differently per driver, and the drivers holding no decision logic.

Latency of the synchronous path (2000 calls through `httpx.MockTransport`, median of seven runs):
3248 us per call before, 3124 us after. No regression; the gain is the removed `asyncio.Runner`
machinery.

One defect the change surfaced: `RetryPolicy.safe`'s `sleep=` default was evaluated in the class
body, where the name now collided with the `_sleep` field and bound to the `Field` object. Renamed
to `_default_sleep`.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` (3.13) | PASS: 1066 passed, 11 skipped in 160.43s. |
| `uv run --python 3.14 pytest -q` | PASS: 1067 passed, 11 skipped in 164.57s. |
| `uv run mypy` | PASS: no issues in 308 source files. |
| `uv run ruff check` | PASS. |
| `uv run python scripts/absence_audit.py` | PASS (now also asserts `_SyncRunner`, `_close_runner`, `EventLoopConflictError`). |
| `scripts/docs_freshness.py update` + `check` | PASS: 61 pages fresh. |
| `docs-site/scripts/validate_docs.py`; strict Sphinx | PASS: 76 pages; build succeeded. |
| sync latency benchmark, before and after | 3248 us -> 3124 us per call. |

### Remaining work / blockers

None.

## Phase 47 — core, module and surface hygiene (2026-09-06)

### State

Complete. The kernel was cut by measurement, the layer that existed only to break an import is
gone, ten modules named by position are named by responsibility, and the package root exports 38
names instead of 55.

### Delivered

- **47.1** Usage of every `core/kernel.py` name was counted across the repository before any
  edit; the table is in the phase document. `Remove`, `StagedEffect`, `CustomScope` and
  `OperationMetadata` had no producer outside their own tests and are gone, together with
  `apply_patch_atomic`'s unused `staged_effects` parameter; `validate_annotation` became private.
  Invariant 2 (identity-based slots) is untouched. 575 -> 519 lines.
- **47.2** `core/ports.py` is deleted. `CryptoProfile` lives in `compile/http_compiler.py`, its
  only reader, with a docstring naming the cycle it breaks. `test_phase34_layering` asserts both
  the absence of the module and the protocol's new home.
- **47.3** Renamed by responsibility: `clients/_core.py` -> `_shared.py`,
  `clients/_http_stages.py` -> `_decisions.py`, `crypto/_runtime.py` -> `_compiler.py`,
  `websocket/_runtime_stages.py` -> `_decisions.py`, `websocket/_artifacts.py` -> `_messages.py`,
  and the five `websocket/_client_*.py` to `_connection.py`, `_outbound.py`, `_clearance.py`,
  `_reconnect.py`, `_state.py`. `crypto/_http.py`, `crypto/_inputs.py` and `websocket/_crypto.py`
  kept their names: they already say what lives there.
- **47.4** The root exports 38 names — declaration, composition, request markers, response
  contract. Model adapters, codecs, error bases and transport failures keep their own modules;
  a test asserts both the cap and that each moved name resolves where it now lives.
- **47.5** Both references describe the final surface.

### The a3 plan's metric 4.5

Recomputed over the consumer surface (`eazy_sdk` plus the modules an SDK author imports from):
**435 distinct public names**. The a3 target of <=300 stays open. The largest contributors are
`eazy_sdk.websocket` (113), `eazy_sdk.crypto` (67), `eazy_sdk.request` (50) and
`eazy_sdk.protection.advanced` (49) — per-package work that 47.4 did not cover, since its target
was the root.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` | PASS: 1076 passed, 11 skipped in 160.61s. |
| `uv run mypy` | PASS: no issues in 307 source files. |
| `uv run ruff check` | PASS. |
| `uv run python scripts/absence_audit.py` | PASS. |
| `scripts/docs_freshness.py update` + `check` | PASS: 61 pages fresh. |
| `docs-site/scripts/validate_docs.py`; strict Sphinx | PASS: 76 pages; build succeeded. |

Note: the phase-46 attempt to run the suite under Python 3.14 in the project venv left it
partially uninstalled (`uv` could not replace files held by a running process). It was repaired
with `uv sync --all-extras --all-packages` plus targeted `uv pip install --reinstall` of
`requests`, `black`, `basedpyright` and `nodejs-wheel-binaries`; a stale `node.exe` from
basedpyright had to be stopped first. Nothing in the repository was affected.

### Remaining work / blockers

The a3 metric 4.5 (<=300 public names) needs a per-package pass over `websocket`, `crypto`,
`request` and `protection.advanced`. It is not part of phases 48-49.

## Phase 48 — one request-representation pipeline (2026-09-06)

### State

Complete. "Wire" means one thing again: how a request looks on the wire. The stage order is one
tuple, the byte-level policy is one `Wire` inherited like any other service attribute, and the
libraries that do the work are declared once on the root — where a backend that cannot do the job
is a compile error rather than different bytes.

### Delivered

- **48.1** Six meanings became five names with one owner each: `JsonPolicy` and `QueryCodec` (how
  bytes are produced), `FieldOrder` and `Wire.exact` (in what order, how precisely),
  `TransportRequirements` (what the transport must speak), `Wire.encrypted` and `Wire.projection`.
  `CryptoWire` -> `Encrypted`, `CryptoRule.wire` -> `.encrypted`, `ws.call(crypto_wire=)` ->
  `encrypted=`. No public name spells "wire" in any other sense.
- **48.2** `Wire` is declared on the operation and inherited operation -> router -> service mixin
  field by field: `None` means inherit, so an operation declaring only an encoding keeps its
  service's encryption.
- **48.3** `eazy_sdk/request/pipeline.py` holds `RequestStage` and `REQUEST_PIPELINE`;
  `_RequestBuild` in the executor knows each stage and nothing about their order. A stage the
  operation did not declare does not run — an operation without an envelope has no idle pass.
  `RequestStage.ENVELOPE` and `_OperationDeclaration.envelope` are the declared slot phase 49
  fills.
- **48.4** Scoped signing is gone from the public surface entirely (`SigningRule`,
  `SigningOverride`, `SigningOverrideMode`, `use`, `extend`, `unsigned`, `sign`,
  `select_signatures`). A router declares `signed = True` and an unsigned operation is refused at
  compile time.
- **48.6** `JsonPolicy` reaches all five former hardcode sites: the JSON body, the signature base
  (`canonical_json`), the body rewritten by a signature output, the crypto response decoder
  (`CompiledPayloadCrypto.json`) and the WebSocket codec (plus the per-message signer, a sixth).
  `PreparedBodyView.json` records the policy its bytes were produced with, so the signature and
  the body cannot be encoded by different rules. `automatic_fields` was deleted, not revived.
- **48.7** `Serialization(models=, json=, html=)` is declared once on the root. `JsonBackend` and
  `DocumentBackend` are protocols; `StdlibJson` and the plugin's `ParselBackend` are the defaults,
  and `plugins/xml` offers `ElementTreeBackend` through the same protocol with
  `selector_languages={"xpath"}`. `_validate_serialization` runs in the executor preflight: a
  backend that cannot produce the operation's `JsonPolicy`, or a parser that cannot read its
  selector language, raises `BackendCapabilityError` naming the operation, the policy/selector and
  the backend.
- **48.5** `tests/unit/test_phase48_wire.py` (12 tests) covers stage order, signature over the
  final bytes including an encrypted body, `Wire` inheritance and override, every `Wire` field
  proven live by walking `dataclasses.fields`, the policy at each former hardcode site, both
  compile-time backend refusals, and the absence of ten removed names.

### Decisions recorded

- `Wire.body` was not added: the body codec is already declared by place, in the parameter
  annotation. `Wire.query` stayed because query has no marker of its own.
- A declared `encoding` changes how the body is delivered: prepared bytes travel as
  `ExactBodyInput` instead of the logical `JsonInput`. Without a declaration the logical input
  still travels, which is how a handler keeps a browser's exact JSON shape.
- The trace gained one diagnostic event (`stages`). The two frozen trace tests (phases 08, 26)
  were updated; the plan fingerprint they also assert is unchanged.
- The open question is closed: host-scoped payload crypto stays as a recorded exception. A missed
  crypto rule sends plaintext where the server expects ciphertext and fails on the first call —
  unlike a missed signature rule, it cannot pass unnoticed.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` | PASS: 1093 passed, 11 skipped in 158.45s. |
| `uv run mypy` | PASS: no issues in 310 source files. |
| `uv run ruff check` | PASS. |
| `uv run python scripts/absence_audit.py` | PASS (six phase-48 names added to `REMOVED_SYMBOLS`). |
| `scripts/docs_freshness.py update` + `check` | PASS: 62 pages fresh. |
| `docs-site/scripts/validate_docs.py` | PASS: 77 pages. |
| strict Sphinx (`-W --keep-going -b dirhtml -c docs-site`) | PASS: build succeeded. |

`use`, `extend`, `unsigned` and `sign` are covered by the phase-48 absence test rather than by
`absence_audit.py`: as bare words they occur in unrelated prose and code all over the repository.

### Remaining work / blockers

None for phase 48. Phase 49 fills the declared envelope slot.

## Phase 49 — protocol envelopes (2026-09-06)

### State

Complete. The framing a service puts around a payload left the transport it never belonged to,
and an HTTP service whose method name travels in the body is now declared the way a REST service
is — one line, no URL repeated, no envelope assembled by hand inside a business projection.

### Delivered

- **49.1** `eazy_sdk/protocols/` holds `Envelope`, `ProtocolMessage`, `CorrelationKey`,
  `ChannelKey`, `ControlKind` and `InboundMessageKind`. `WsProtocol` subclasses `Envelope` and
  adds what only a connection has: `codec`, `inspect(frame)`, `classify_close`, `build_recovery`,
  `build_cancel`, `build_control`. `JsonEventProtocol`, `GraphqlTransportWsProtocol` and the soak
  test's exchange protocol split `inspect(frame)` into frame handling plus a shared
  `read(envelope)`; `tests/websocket/` passes with no changed expectations (79 passed, 8 skipped).
  `eazy_sdk.websocket` no longer re-exports the moved names — migration rows added.
- **49.2** `RequestStage.ENVELOPE` runs between the projection and payload crypto. An operation
  without an envelope does not run the stage at all, which a test asserts by comparing the
  executed stages of an RPC call and of a REST health check on the same router.
- **49.3** `JsonRpc(path, version, method, id_on_retry)` implements `Envelope` as a pure function
  in both directions. `read` refuses an envelope carrying neither `result` nor `error`.
- **49.4** `api.rpc(discriminator, ...)` is a member of the one decorator namespace; every other
  argument is `api.post`'s. The path and verb come from the router's `protocol` attribute
  (`protocol` joined `SERVICE_ATTRIBUTES`), resolved over the MRO. `@api.rpc` on a router with no
  envelope raises at class creation, not on the first call.
- **49.5** `AttemptState.correlation` holds the id for the life of the attempt. It is reused on a
  repeat by default — a repeat should be recognisable to the server as a duplicate, the way an
  idempotency key is — and `id_on_retry="regenerate"` gets a fresh one per attempt. A reply
  carrying somebody else's id is a `MalformedResponseError`.
- **49.6** `rpc_result`, `rpc_error(code, model)` and `rpc_error_default(model)` build ordinary
  `Success`/`Error` cases with a `condition`; the typed error model keeps working exactly as it
  does over REST. No second raiser, no second error model — an absence test holds it.
- **49.7/49.8** `guides/protocols.mdx`, updates to `guides/websocket.mdx`, the migration page and
  the authoring reference; `tests/unit/test_phase49_envelopes.py` (13 tests).

### Decisions recorded

- `RpcIdGenerator` as a DI dependency was not introduced. The id format is a property of the
  service's protocol, not of the calling environment, so it is a method of the envelope; a
  separate dependency would be a second place deciding one question.
- `notification` (a JSON-RPC request with no id and no reply) is not implemented: mechanically
  expressible already, and no target API asks for it. Standard completeness is not a reason to
  add surface.
- `GraphqlHttp` is not included. The envelope is trivial; the cost is a third outcome beside
  `SuccessOutcome`/`ErrorOutcome` for GraphQL's simultaneous `data` and `errors`. Without that
  decision it would become a third way to return an error.
- `rpc_error_default` belongs in `Responses(fallback=…)`, not among `errors`: as a case it
  matches the same body a coded case matches, and two matching cases are an ambiguity.
- `json_body_document` was factored out of `request/prepared.py` so the envelope stage wraps the
  same structure the preparer would have encoded, built once rather than twice.

### Commands run

| Command / gate | Result |
|---|---|
| `uv run pytest -q` | PASS: 1108 passed, 11 skipped in 160.89s. |
| `uv run mypy` | PASS: no issues in 314 source files. |
| `uv run ruff check` | PASS. |
| `tests/websocket/` after the move | PASS: 79 passed, 8 skipped, no changed expectations. |
| `uv run python scripts/absence_audit.py` | PASS. |
| `scripts/docs_freshness.py update` + `check` | PASS: 63 pages fresh. |
| `docs-site/scripts/validate_docs.py` | PASS: 78 pages. |
| strict Sphinx (`-W --keep-going -b dirhtml -c docs-site`) | PASS: build succeeded. |

### Remaining work / blockers

None. Phases 42-49 of the 0.3.0 architecture refactor are complete. Two items stay open and are
recorded rather than pending: the a3 plan's metric 4.5 (<=300 public names, currently 435, needs a
per-package pass over `websocket`, `crypto`, `request` and `protection.advanced`), and
`PartialOutcome` for GraphQL-over-HTTP.

## Phase 50 — declarative operations (2026-09-06)

### State

Active (50.1). An operation becomes a frozen model class published with `op(...)`; the
decorator synthesizes the same class. Plan: `50-declarative-operations.md`; design:
`eazy-sdk-declarative-operations.md`.

### Delivered

- **50.1.1** `scripts/surface_count.py` walks the modules an SDK author imports from and counts
  the distinct objects behind their `__all__` names; `tests/unit/test_surface_count.py` (1 test).

### Surface baseline

`uv run python scripts/surface_count.py --total` on master before any phase-50 edit: **436**.
Gate for the phase: the number after 50.4.6 is ≤ 436.

### Decisions recorded

(§10 of the plan, English; appended as the phase proceeds.)

### Commands run

| Command / gate | Result |
|---|---|

### Remaining work / blockers

50.1.2 onwards.
