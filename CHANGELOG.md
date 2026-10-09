# Changelog

All notable changes to Eazy SDK are documented here. The project follows
[Semantic Versioning](https://semver.org/) and uses PEP 440 versions for Python packages.

## Unreleased

## 0.2.0a11 - 2026-10-10

Breaking:

- A cookie the server set is sent one way only: through the cookie jar of the caller's
  `Identity`, switched on by `Cookies(...)` on the SDK root. Everything that carried such a
  cookie by hand is removed, with no aliases:
  - `session_cookie(...)`, `eazy_sdk.auth.cookies` (`HttpCookieSession`,
    `parse_session_cookie`) — declare `Cookies(required=(...))` and an ordinary
    `session_scheme(Model)`;
  - `Placed.cookie(...)` and `Placed.cookies()`, together with the cookie-set auth placement
    (`AuthPlacement.many`) — the jar carries what the site set;
  - `eazy_sdk_browser.BrowserCookie` — use `eazy_sdk.cookies.StoredCookie`. Its `domain` has
    no leading dot; `host_only=False` is what sends a cookie to subdomains, and a dotted
    domain raises `ValueError`;
  - `browser_cookie_auth`, `BrowserCookieBridge`, `CookieAuthAdopter` — hand a browser session
    to an HTTP SDK with `Identity(cookies=CookieState(state.cookies))`.
- `SessionStore.save(key, value, revision, cookies=None)` and `StoredSession.cookies`. A store
  written for a token session keeps working; a store used on a cookie site must accept
  `cookies`. `eazy_sdk_sqlmodel` keeps the snapshot in a new nullable column: an existing
  database needs `ALTER TABLE sessions ADD COLUMN cookie_state JSON`.
- A browser session saved by an earlier version is not read: its cookies encoded their scope in
  a leading dot. It loads as an empty state and the owner signs in again.
- `session_scheme(Model)` no longer refuses a model that places nothing: that is the session of
  a site that keeps everything in cookies. On a service without `Cookies(...)` the first
  protected call raises `SessionConfigurationError` instead.

Added:

- `Cookies(required=(), leeway=..., public_suffixes=None)` in `eazy_sdk.auth`, declared on an
  SDK root or a router and inherited like `security`. Every cookie the site sets goes back
  where it belongs: to the next operation, to the next hop of a redirect, to another host of
  the same site. `required` names the cookies without which a session is not valid.
- `eazy_sdk.cookies`: `StoredCookie`, `CookieState` and `CookieJar`, following RFC 6265
  (host-only and domain scope, path and expiry rules, `Secure`, limits). A cookie without
  `Domain` belongs to the host that set it; a cookie without an expiry is kept and saved.
- One jar per `Identity`: two identities over one client share no cookie, one identity over two
  clients shares all of them. `Identity(cookies=CookieState(...))` starts from a saved set.
- The cookie snapshot is saved with the session under one revision, and read back even when the
  saved session is no longer valid. The library never clears a user's cookies by itself, so a
  site recognizes the device at the next login, as it does with a browser.
- `Http.get(..., cookies=False)` keeps one operation out of the jar, both ways.
- `Resilience(max_redirects=N, client_redirects=True)` and `CallOptions(client_redirects=True)`
  follow a redirect written into a page with `<meta http-equiv="refresh">`, as a `GET` on the
  same budget as a `3xx`. Only a page no declared case could read is treated as such a stub.
- A solver's own requests (`ProtectedFetch`) now really share the caller's cookies on a cookie
  site; before this the shared jar existed only in the docstring.

Changed:

- On a service that declares `Cookies(...)`: a security scheme that places a credential in a
  cookie (`CookieScheme`) is a declaration error, and so is an operation that writes the
  `Cookie` header by a field or `Inject`. A `Cookie[...]` field still works: it overrides the
  session's cookie of the same name for that request and is not stored.
- Without `Cookies(...)` nothing changes: a request is still fully determined by its operation.

## 0.2.0a10 - 2026-10-09

Added:

- `Location` in `eazy_sdk.response` compares the redirect target part by part: `host`, `path`,
  `query` and `contains`, every one optional, with `*` as the only wildcard in a string and a
  compiled pattern for the rest. A relative header is resolved against the request address
  first. On a model field, `Annotated[str, Location(path="/inbox*")]` is the criterion of the
  case and the source of the field at once; `Location.query("fail")` reads one query parameter,
  and `Const(...)` beside it states the value. The same object is a `when=` condition and
  combines with `eazy_sdk.response.match` predicates.
- `HeaderModel`: a model every field of which is read from around the body (`Location`,
  `FromHeader`, `FromCookie`) is read without the body, and is recognized without being told.
- `FromCookie("sid")` reads the value of a cookie the response sets into a model field.
- `Placed.query`, `Placed.header`, `Placed.cookie` and `Placed.cookies` in `eazy_sdk.auth` mark
  the fields of a session model that go into every protected request, so
  `session_scheme(Model).configure(...)` gives the whole acquire and refresh lifecycle to a
  session that is more than one Bearer header. `Bearer()` compiles through the same placements.
- `Regex` in `eazy_sdk_html` is a third selector language beside `CSS` and `XPath`. It searches
  the document text as received; a single-value field takes the first match, a list field every
  match. A document model may also carry `FromCookie`, `FromHeader` and `Location` fields.

Changed:

- A redirect whose status the operation declares a case on is read by the operation: the client
  no longer follows it and no longer raises `RedirectLimitError` for it, whatever
  `max_redirects` is. Before this, a response with a `Location` header on `301`, `302`, `303`,
  `307` or `308` never reached the declared cases. `fallback=` does not declare a status, and an
  operation with no case on a redirect status behaves as before.
- A case on a status inside `3xx` must state a criterion: `Location(...)` on a field of its
  model, `when=`, `accept=` or a `Const(...)` tag. A case that would claim every redirect is
  refused when the operation is first compiled; `when=Location()` says "any target" out loud.
- A session with an empty placed value is not valid: an empty Bearer token, an empty string or
  an empty cookie set is acquired again instead of being sent.
- The slot of an auth placement takes `secret` from `AuthPlacement.secret`. It was always
  secret before, so `secret=False` on a hand-built placement had no effect.
- A session model that places nothing is refused with a message naming `Placed` beside
  `Bearer`; two `Bearer` fields keep the previous message.

## 0.2.0a9 - 2026-10-07

Fixed:

- `Inject` values no longer land in another `Inject`'s slot. Every attempt created a fresh
  dependency descriptor per `Inject` and registered its provider in the identity's
  `DependencyRegistry` under `id(descriptor)`; once the descriptor was freed, the next one reused
  the address and received the stale provider, so a request silently carried a value of another
  parameter, possibly from another operation. The registry and the per-call caches are now keyed
  by the descriptor itself, an `Inject` provider stays on its own requirement instead of entering
  the shared registry, and `requires=`/`inject=` are lowered once per call, so
  `Inject(cache=DependencyCachePolicy.CALL)` keeps its value across retries.

## 0.2.0a8 - 2026-10-07

Breaking:

- One distribution instead of ten. The core and every integration ship as `eazy-sdk-core`; the
  separate `eazy-sdk-html`, `eazy-sdk-accounts`, `eazy-sdk-sqlmodel`, `eazy-sdk-browser`,
  `eazy-sdk-openapi`, `eazy-sdk-asyncapi`, `eazy-sdk-presets`, `eazy-sdk-xml` and
  `eazy-sdk-adaptix` distributions are gone. An extra now installs only the third-party
  libraries an integration needs: `eazy-sdk-openapi[yaml]` becomes
  `eazy-sdk-core[openapi,yaml]`, `eazy-sdk-browser[playwright]` becomes
  `eazy-sdk-core[browser,playwright]`. Import packages (`eazy_sdk`, `eazy_sdk_html`, ...), the
  `eazy-sdk-openapi`, `eazy-sdk-asyncapi` and `eazy-sdk-sqlmodel-migrate` commands and the
  `x-eazy-sdk` specification extension keep their names.
- The distribution is named `eazy-sdk-core`, not `eazy-sdk`: PyPI rejects `eazy-sdk` as too
  similar to the unrelated `eazysdk` project.
- Response-case arbitration ranks a criterion above status precision. The order is now: a case
  that states a criterion over one that states none, then an exact status over a range over
  `DEFAULT`, then an explicit media type over a wildcard, then the layer that declared the case.
  Before, an operation's plain `success={200: ...}` outranked a service's
  `Error(StatusRange(200, 599), ..., condition=...)` on status alone, so a protection page
  answered with 200 was parsed as a successful body and no error was ever raised. An operation
  that genuinely owns a status against a conditional case now says so with its own `when=`.

Added:

- Native `PydollDriver` for `pydoll-python>=3,<4` in `eazy_sdk_browser.handlers.pydoll`.
  It adapts an externally owned `Tab`, shares bounded response capture and browser-side fetch
  semantics with Playwright, and deliberately leaves browser/context/tab ownership, identity,
  proxy, concurrency and task retries to an orchestration layer such as `browser_pool`.
- `when=` on `Text`, `Bytes`, `Empty`, `Extracted` and `Parsed`, the five response
  representations that lacked it. The dict form of `success=`/`errors=` now expresses every
  field of a case except `precedence`, which authors never write.
- An `errors=` entry pairs a factory with a written-out representation, not only with a model
  class: `errors={200: (Text(media_type=None, when=is_challenge), ChallengeRequired)}`.
- `Const` and `Payload` in `eazy_sdk.response`: a response model states what its own body says
  about itself. `Annotated[bool, Const(True)]` is the JSON Schema `const` written in Python — the
  tag that tells a successful envelope from a failed one on the same 200, which is how Slack,
  Stripe and Cloudflare all describe that case in their own specifications. A tag states a fact
  and never a verdict, so it reads the same on a `Success` and on an `Error` case; which of the
  two a body means is said by the dictionary the model was declared in, because only the
  operation knows (Stripe answers 200 with `deleted_customer`, a success for a lookup and a
  failure for a wait-until-active). `Payload[T]` marks the field the operation returns, so the
  envelope stays a detail of the declaration and the result type is read from the annotation
  rather than inferred. Values are compared with their types, because `1 == True` in Python. The
  form is `Annotated` rather than `Literal` because only the annotation reaches every backend:
  `Literal[True]` is impossible on msgspec and the dataclass and TypedDict adapters reject
  `Literal` outright; a one-value `Literal` is read as the same statement where a backend does
  carry it. Five checks run before the request is sent: a constant the field cannot hold, two
  `Payload` fields on one model, `Payload` together with `unwrap=`, a projection that disagrees
  with `HttpOperation[T]`, and one model claimed by a success and an error with nothing to tell
  them apart.
- `accept=` on `Json`, `Html`, `Extracted` and `Parsed`: decides on the parsed value and overrides
  the tags the model declares, for a model you cannot edit and for a condition that does not
  reduce to equality. It answers "does this case match", so it is read as written on both success
  and error cases.
- `eazy_sdk.response.match`: composable predicates for `when=`, combined with `&`, `|` and `~`.
  `body.startswith`, `body.contains(..., ignore_case=)`, `body.matches`, `body.is_empty`,
  `content_type.is_`/`.startswith`, `status.is_`/`.in_`, and `header(name).present`/`.is_`/
  `.contains`. A `bytes` argument reads the raw body and a `str` one the decoded text, where a
  body that does not decode answers False instead of raising. Each predicate is an ordinary
  callable, so it mixes with existing functions and lambdas, and carries a label that reads in a
  diagnostic.

Fixed:

- `fallback=` answers a response every declared case declined. It was chosen before parsing, so a
  service reporting failure inside a 2xx never reached it: the success case stayed a candidate on
  the strength of its status, declined the body once its criterion saw it, and the call ended as
  `UnexpectedResponseError`. The dictionary form had no workaround, because `errors=` refuses
  `DEFAULT` precisely on the grounds that `fallback=` is where it belongs. A candidate that was
  malformed still keeps its outcome: there the case did claim the response.

Changed:

- A document model with nothing required no longer has to carry its own `when=` when another case
  states a criterion that could claim the same statuses. Since the arbitration order changed, the
  service's own protection case wins that page on its own, so requiring the negation of it on
  every success was boilerplate. With no such case anywhere, the model is still refused: it would
  swallow every page it is offered.

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
