# Changelog

All notable changes to Eazy SDK are documented here. The project follows
[Semantic Versioning](https://semver.org/) and uses PEP 440 versions for Python packages.

## Unreleased

Breaking:

- Response-case arbitration ranks a criterion above status precision. The order is now: a case
  that states a criterion over one that states none, then an exact status over a range over
  `DEFAULT`, then an explicit media type over a wildcard, then the layer that declared the case.
  Before, an operation's plain `success={200: ...}` outranked a service's
  `Error(StatusRange(200, 599), ..., condition=...)` on status alone, so a protection page
  answered with 200 was parsed as a successful body and no error was ever raised. An operation
  that genuinely owns a status against a conditional case now says so with its own `when=`.

Added:

- `when=` on `Text`, `Bytes`, `Empty`, `Extracted` and `Parsed`, the five response
  representations that lacked it. The dict form of `success=`/`errors=` now expresses every
  field of a case except `precedence`, which authors never write.
- An `errors=` entry pairs a factory with a written-out representation, not only with a model
  class: `errors={200: (Text(media_type=None, when=is_challenge), ChallengeRequired)}`.
- `Envelope` in `eazy_sdk.response`: a model declares how its service envelope reads, as the class
  attribute `__envelope__ = Envelope(succeeds=..., payload=...)`. `succeeds` answers whether the
  envelope is a success, so a `Success` case matches when it says yes and an `Error` case when it
  says no; one predicate declares both halves of a service that reports business failure inside a
  200, and the failure becomes an ordinary `ApiError` instead of a hand-written check downstream.
  `payload` projects the successful value, so the operation returns the payload while the envelope
  stays a detail of the declaration; an error case keeps the whole envelope, where its message
  lives. The declaration is a class attribute holding plain callables rather than a method,
  because a `TypedDict` value is a plain `dict` and carries no methods; all four model libraries
  are covered by one test.
- `accept=` on `Json`, `Html`, `Extracted` and `Parsed`: decides on the parsed value and overrides
  the model's own rule, for a model you cannot edit. It answers "does this case match", so unlike
  `succeeds` it is read as written on both success and error cases.

## 0.2.0a7 - 2026-09-08

Scope: phase 52, declarative pagination (`docs/implementation/52-pagination.md`). No breaking
change; the root export list is unchanged.

Added:

- Declarative pagination (phase 52.1). An operation class declares
  `__pages__ = Pages.numbered(Model, page=..., size=..., items=..., total_pages=...)` from
  `eazy_sdk.pagination`; the bound operation gains `pages(...)` and `items(..., key=...)`, sync
  and async, with `max_pages=` and `options=`. Every page is an ordinary `send()`; `op()` checks
  the field names and the result type at import. The root export list is unchanged.
- `Pages.offset(Model, offset=..., limit=..., items=..., total=...)` (phase 52.2): pages addressed
  by an element offset; the next offset advances by the page the server actually returned.
- `Pages.cursor(Model, cursor=..., items=..., next_cursor=...)` (phase 52.3): pages addressed by
  a token the previous page handed out; the first page sends the field as declared, `None` from
  `next_cursor` ends the iteration.
- `Pages.next_url(Model, items=..., next_url=...)` (phase 52.4): the next page is sent to the link
  the previous page returned, verbatim; the operation's path and query fields are not re-applied,
  headers, cookies and body still are; a relative link resolves against the page that gave it.

## 0.2.0a6 - 2026-09-08

Scope: the confirmed findings of the phase-50 code review
(`docs/eazy-sdk-phase50-review-plan.md`), phase 51 serialization performance
(`docs/implementation/51-serialization-performance.md`), and a `Body` encoding namespace.

Breaking:

- `success=` and `errors=` document several shapes for one status with a **list**:
  `success={200: [OrderV1, OrderV2]}`. A tuple is the `(Model, factory)` error form and was never
  unrolled; the type now says so instead of promising any `Sequence`.
- `Json(unwrap=..., extractor=...)` raises `ValueError`. The pointer is what the standard
  extractor reads; a custom extractor unwraps the envelope itself.
- `ClientConfig(errors=...)` refuses two spellings of one host (`"Books.Example"` and
  `"books.example"`); matching is case-insensitive, as documented.
- `security=`, `signing=` and `requires=` on an operation are typed. Values that were accepted as
  `object` and failed later at compile time are now rejected by the type checker.

Fixed:

- The parsed response document is cached once per response again: the cache keyed by `id()` of a
  tuple built at the call site, so it never hit, re-parsed the page for every candidate case, grew
  one entry per extraction, and could hand a stale entry to an unrelated object.
- Host-scoped error cases no longer leak between operations: the cache keyed by `id(contract)`
  without holding the contract, so a rebuilt router could inherit another operation's declaration.
- `Omittable[T] = UNSET` works on WebSocket operations: the field is omitted from the message
  instead of failing with `no model adapter supports GenericAlias`.
- A document model readable by any configured parser compiles; previously only the first
  `Serialization.documents` entry was consulted by the discriminator check.
- An unrelated mixin with empty `__slots__` is no longer reported as the operation base (D-03).
- An object carrying `spec` and `operation_type` is no longer reported as an HTTP operation
  published on an `AsyncWsApi` (D-23).
- `pyrefly` is a development dependency, not a runtime one.

Performance (phase 51; no change to bytes on the wire, see plan §3 invariants P1-P8):

- `ModelAdapterRegistry` caches a model's fields, keyed by class: `_apply_header_sources` was
  re-resolving every model's annotations through `get_type_hints()` on every response (71% of a
  sequential parse's budget). New `ModelAdapterRegistry.clear_field_cache()`.
- `DataclassModelAdapter`/`TypedDictModelAdapter` build a load plan once per class instead of
  reading fields and re-walking the value-loading dispatch on every object; the two adapters that
  paid this cost per object, not per response, are 4-9x faster on a 200-item list.
- Adapter selection (`ModelAdapterRegistry._select`) is cached by type instead of scanning every
  configured adapter on every value.
- A non-stdlib JSON backend paired with a signature that reads or writes the JSON body is refused
  at operation-compile time: the signature base and any body rewrite after inserting an output
  are always encoded by the stdlib backend, so a second backend could sign bytes different from
  the ones sent. Silent today (one backend ships); this closes the class of bug before a second
  backend can trigger it.
- `JsonBackend` gains a `readable` property. `ResponseContext.json` reads through the configured
  backend only when it declares itself safe to parse a response with; `orjson`, for one, silently
  represents an integer wider than 64 bits as a `float`. `StdlibJson.readable = True`.
- Net effect, measured end to end (`Responses.inspect()`, one machine, `experiments/perf/`):
  every response-parsing scenario is 3.9x-8.3x faster than before phase 51, with request-path
  scenarios unaffected (not this phase's target). Fast-path decoding straight from bytes (plan
  §6, 51.6) was evaluated and declined: the remaining gap to native decode speed did not justify
  a second parsing path once the above landed.

Added:

- `Body`: a namespace of body encodings (`Body.json()`, `Body.form()`, `Body.multipart()`,
  `Body.raw()`, `Body.stream()`) for `BodyProjection.encoding=`, the same pattern as `Http.post`
  for HTTP methods, instead of importing each encoding descriptor separately from
  `eazy_sdk.request.markers`.

## 0.2.0a5 - 2026-09-05

Scope: the confirmed findings of the 0.2.0a4 verification (`docs/eazy-sdk-v0.2.0a5-plan.md`).

- Transport identity now carries the proxy. `HandlerProfile.proxy` is declared by the handler;
  `Client.httpx/requests/curl_cffi(proxy=...)` and `AsyncClient.httpx/curl_cffi(proxy=...)`
  configure the session they create and declare it. `EmitOptions` keeps only `timeout`
  (`proxy`, `verify_tls`, `stream_response` were never read by any handler).
- One identity per attempt: the fingerprint a managed solution is bound to is computed from the
  public values of the attempt before managed state is applied, at solve time and at apply time.
  A guard that owns `User-Agent` no longer invalidates its own solution.
- `ChallengeMalformedError` (runtime) renamed to `ChallengeParseError`; the detector-side
  `MalformedChallengeError` is unchanged.
- `ProtectedFetch` contract documented (no redirects, full timeout per fetch, shared cookie jar,
  no pipeline); `SolveContext.remaining()` returns the solve budget left.
- `eazy-sdk-html` depends on `parsel` unconditionally; the `parsel` extra is gone.
  `eazy-sdk-sqlmodel` declares `sqlalchemy` and `pydantic` it imports directly.
- `scripts/package_audit.py` rejects unconditional imports not covered by a distribution's
  mandatory dependencies; `scripts/extras_smoke.py` installs every extra from wheels into fresh
  venvs; GitHub Actions `ci.yml` (3.13/3.14) and `release.yml` (tag -> GitHub release).
- Sync `Client` no longer touches the thread's current event loop (`asyncio.Runner` with an
  explicit loop factory); the closed check happens under the runner lock.
- `eazy_sdk.codegen.session_auth/session_scheme` renamed to
  `generated_session_auth/generated_session_scheme` (distinct from `eazy_sdk.auth.session_auth`).
- Docs: `more/migration` lists every rename of 0.2.0a3 -> 0.2.0a4 -> 0.2.0a5; single-flight
  semantics per cache mode corrected (`session` only).

## 0.2.0a4 - 2026-09-03

- `SolveContext` gained transport ports: `fetch` (a `ProtectedFetch` over the client's own
  handler, proxy, and cookie jar that bypasses guards, replay, middleware, and rate limiting),
  `identity` (`TransportIdentity` with a redacted `repr` and `fingerprint()`), and
  `request_headers`; `deadline` is now derived from the call timeout.
- Managed protection state remembers the transport identity it was acquired under. A later
  request prepared under a different User-Agent, proxy, or impersonation label invalidates the
  stored clearance instead of replaying it.
- `HandlerProfile.impersonation` declares the handler's impersonation label; curl_cffi handlers
  set it from `impersonate=`.
- Added `eazy_sdk.redaction.redact_url_credentials()`.
- Added the one-class guard layer: `Guard` (class-level `scope`/`cache`/`replay`/declared
  destinations, sync or async `solve()`, `self.solution(cookies=..., headers=..., expires_in=...)`),
  `GuardSolution`, and `ClientConfig(guards=[...])` as the primary installation path
  (`with_protection()` stays equivalent).
- `challenge_guard()` gained `cache="none" | "call" | "session"`, a whole-client default `scope`,
  a `name` inferred from the detector, and accepts a plain solver function or a sync `solve()`.
- `host()` and `operation()` moved into `eazy_sdk.protection`; `eazy_sdk_presets` re-exports them.
- Added `client.invalidate_protection(*guards_or_names)` to drop cached solutions without touching
  runtime internals.
- Advanced SPI cleanup (breaking, no aliases): `ProtectionSolver` → `ChallengeSolver`,
  `ProtectionRequirement[TResult]` → `SolverRequirement[TChallenge, TSolution]`,
  `ChallengeSolverBinding`/`bind_challenge_solver`/`ChallengeSolverBindings` →
  `SolverBinding`/`bind_solver`/`SolverBindings`, `SolutionCookieSet` → `PrivateCookieSetBinding`
  (still built by `solution_cookie_set()`), `ChallengePolicySpec`/`BeforeCallPolicySpec` →
  `ChallengePolicy`/`BeforeCallPolicy` (frozen keyword-only dataclasses replacing the
  runtime-checkable protocols). `ClientConfig`/`ExecutionRuntime` keep one `solver_bindings`
  registry instead of `operation_protection_solvers` + `challenge_solvers`;
  `ProtectionBundle.solver_bindings` replaces the two binding tuples.
- `SolutionFields.bindings` is public; detector-based signals carry the detector's annotated
  challenge type instead of an `object` sentinel.
- A malformed challenge now raises `ChallengeMalformedError` (policy, attempt, detector error as
  `__cause__`); two definitive policies matching one response raise `AmbiguousChallengeError`.
- Replay budgets are counted per policy; `ProtectionFlow.acquire`/`verify` and
  `BeforeCallPolicy.acquire` are typed as `OperationReference`.
- Protection single-flight locks are loop-agnostic and thread-safe, so a sync `Client` shared by
  threads no longer risks cross-loop `asyncio.Lock` errors.
- `HandlerProfile.impersonation` and bound API methods expose `.declaration`.
- Entry point: `Client(base_url=...)`/`AsyncClient(base_url=...)` default to Zapros' standard
  network handler; `Client.httpx()`, `Client.requests()`, `Client.curl_cffi()`,
  `AsyncClient.httpx()`, `AsyncClient.curl_cffi()` build first-party handlers. `Path`, `Query`,
  `Header`, `Cookie`, `JsonBody`, `FormBody`, `BodyProjection`, `Json`, `Html`, `Bytes`, `Text`,
  `Responses`, `Success`, `Error` are re-exported from `eazy_sdk`.
- One facade (breaking): `SyncSdk`/`AsyncSdk` and `eazy_sdk.sdk` are gone; `SyncApi`/`AsyncApi`
  own `api_group()`, `from_client()`, `from_handler()`, `close()`/`aclose()` and the context
  manager. Raw `client.get()/post()/...` verbs are removed; `client.request()` stays.
- `ClientConfig(protection=ProtectionBundle | None)` replaces `operation_protections`,
  `before_call_policies`, `challenge_policies` and `solver_bindings`; `config.bundle`,
  `ProtectionBundle.merge()`/`.solvers`.
- `eazy_sdk._internal` no longer declares `__all__`; `eazy_sdk.codegen` exports `session_auth`,
  `session_scheme` and `ProtectionBundle` instead of `_generated_*` helpers.
- Layering (breaking for private imports): `eazy_sdk._internal` is split into `eazy_sdk.core`
  (kernel, errors, HTTP locations, plans/scopes, `CryptoProfile` port) and `eazy_sdk.compile`
  (endpoint compiler, operation declarations, input schemas). `CallOptions`/`RetryPolicy` live in
  `eazy_sdk.policies`; `LifecycleGraph` lives in `eazy_sdk.auth.lifecycle`; the compiler depends
  on the crypto port instead of `eazy_sdk.crypto`; `extraction` imports the Pydantic integration
  lazily. Layer contracts are enforced by `tests/unit/test_phase34_layering.py`.
- Scope (breaking): account registration/verification and the multi-account storage layer moved
  to the `eazy-sdk-accounts` plugin (`eazy_sdk_accounts`, `eazy_sdk_accounts.storage`); the
  session lifecycle primitives moved to `eazy_sdk.auth.session`. Offline HTML inspection and
  extraction moved to the `eazy-sdk-html` plugin (`eazy_sdk_html`, extraction errors in
  `eazy_sdk_html.exceptions`); `eazy_sdk.response.Html` loads it on demand. Extras: `accounts`,
  `html`; `eazy-sdk-sqlmodel` depends on `eazy-sdk-accounts`.
- Exceptions (breaking): every public exception derives from `eazy_sdk.core.errors.EazySdkError`
  and ends with `Error` — `AttemptLimitError`, `RedirectLimitError`, `TransportError`,
  `PreparationIncompleteError`, `CapabilityMismatchError`, `CryptoLimitError`,
  `CryptoRuntimeMismatchError`, `EncryptedMediaTypeMismatchError`,
  `EncryptedFrameKindMismatchError` replace the old names; configuration errors share
  `ConfigurationError`; `EazySDKError` is now `EazySdkContextError`.
- Sync `Client` reuses one `asyncio.Runner` per thread instead of `asyncio.run()` per call and
  raises `EventLoopConflictError` inside a running event loop.
- `eazy_sdk.middleware.ScopedMiddleware` is the one registration contract for HTTP and WebSocket;
  WebSocket applications expose `implementation`; storage hooks are observers
  (`eazy_sdk_accounts.storage.observers`, `observers=`).
- Sync/async clients share `eazy_sdk.clients._core._ClientCore`; `api-reference/extensions`
  documents the stability matrix.

## 0.2.0a1 - 2026-09-02

- Reduced `eazy_sdk.ext` from an accidental 96-name internal barrel to 23 reviewed extension SPI
  contracts.
- Added the complete extension-author reference and migrated first-party plugins/tests to owning
  namespaces.
- Corrected project metadata back to alpha. Breaking changes remain allowed before a stable release.

## 0.1.0 - 2026-09-01

GitHub-only snapshot that was incorrectly labelled stable. It was not published to PyPI and does
not establish a compatibility guarantee.

- Added exact omission semantics and structured caller diagnostics for projected request bodies.
- Added typed protection policies, atomic multi-value updates, persistent reaction freshness, and
  managed Cloudflare clearance.
- Added hand-written SDK authoring helpers, offline request preparation, typed parser inference,
  and public API grouping.
- Added verified Python 3.13 support alongside Python 3.14.
- Split HTTP execution, compilation, and WebSocket lifecycle logic into typed internal stages
  while retaining one execution path and the existing wire behavior.
- Completed full runtime, typing, lint, documentation, package, isolation, and branch-coverage
  release gates for the core package and all five first-party plugins.

## 0.1.0a1 - 2026-08-25

First public alpha.

- Typed HTTP and async WebSocket SDK runtimes built on Zapros.
- Declarative API methods with sync and async clients.
- OpenAPI 3.0/3.1/3.2 and AsyncAPI 3.0 SDK generators.
- Pluggable model adapters, request/response codecs, auth, signing, middleware, and storage.
- Application-owned field and whole-payload encryption for HTTP and WebSocket.
- Optional SQLModel, XML, protection preset, HTTP handler, and HTML extraction integrations.
