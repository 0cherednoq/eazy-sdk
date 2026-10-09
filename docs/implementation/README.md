# Eazy SDK: master-план breaking rewrite

Статус: утверждённый план реализации.

Дата фиксации: 2026-08-13.

Совместимость со старым API: не поддерживается.

Этот каталог является единственным архитектурным планом rewrite. Он заменяет прежние design,
review, roadmap и superpowers-документы. Публичная документация в `docs-site/` описывает
выпущенный API и обновляется в фазе 10 после стабилизации нового runtime. First-party vendor
presets документируются и выпускаются отдельно в фазе 11, не меняя execution semantics core.

Конкретный candidate итогового API — generated clients, routers, auth, extension points,
API captcha и Cloudflare — показан в [SDK reference](sdk-reference.md).
Как построить такой private SDK вручную — descriptors, binders, routers, clients, dependencies,
credentials и полный session cycle — зафиксировано в
[SDK authoring reference](sdk-authoring-reference.md).

The post-rewrite Zapros transport-boundary spike, including captured bytes, handler defaults and
signature results, is recorded in [Zapros transport-boundary evaluation](zapros-evaluation.md).
The approved breaking refactor that makes Zapros the only HTTP/handler boundary and adds model
adapters, codecs and offline HTML extraction is tracked in
[phase 18](18-zapros-model-codecs-and-html-extraction.md).
The approved WebSocket extension keeps the HTTP and WebSocket state machines separate, uses
`zapros.websocket` as the connection/frame boundary, and is tracked in
[phase 19](19-websocket-runtime.md).
The approved application-owned payload crypto extension shares typed profiles and staged
transforms across those separate runtimes and is tracked in
[phase 20](20-unified-payload-crypto.md).
The approved public-to-wire body projection keeps `Unpack[TypedDict]` signatures flat while a
separate private schema owns the protocol document and is tracked in
[phase 21](21-public-wire-body-projection.md).
The unpublished workspace receives one clean package, import, plugin, CLI and documentation
identity without compatibility aliases in [phase 22](22-eazy-sdk-local-rename.md).
Production feedback from a hand-written KAD SDK is converted into authoritative correctness,
protection, authoring and runtime-hardening work in
[phases 23–26](23-production-correctness-and-diagnostics.md). The external audit remains evidence;
these phase documents define the target behavior and gates.
The post-release anti-bot API review is triaged into a clean-checkout, identity, capability,
installation, replay and concurrency correction in
[phase 28](28-antibot-api-correctness-and-ergonomics.md). The review remains evidence; phase 28
defines the accepted target and deliberately rejects compatibility shims.
The `v0.2.0a2` simplification audit then demonstrated that per-attempt identity metadata does not
bind the actual handler session and can leak a locally solved clearance across identity rotation.
The accepted breaking correction, high-level custom guard builder and public-surface reduction are
tracked in [phase 29](29-antibot-api-simplification-and-session-affinity.md).
The post-`v0.2.0a3` remediation plan (`docs/eazy-sdk-remediation-plan.md`) starts with the
`SolveContext` transport ports (`fetch`, `identity`, `request_headers`, filled `deadline`) and
identity-bound managed state in [phase 30](30-solve-context-transport-ports.md).
The `v0.2.0a4` verification findings (`docs/eazy-sdk-v0.2.0a5-plan.md`) are implemented as
[phase 37](37-packaging-and-ci.md) (packaging, CI), [phase 38](38-transport-identity-proxy.md)
(declared proxy, one identity per attempt), [phase 39](39-protection-contracts-and-names.md)
(`ChallengeParseError`, `ProtectedFetch` contract), [phase 40](40-migration-doc.md) (migration
page) and [phase 41](41-runtime-hygiene.md) (sync runner, generated session helpers).
The post-`0.2.0a5` architecture review — a source-level comparison against `unihttp` and
`descanso` plus an audit of the debt accumulated during generation — is planned in
[`docs/eazy-sdk-architecture-refactor-plan.md`](../eazy-sdk-architecture-refactor-plan.md) and
implemented as [phase 42](42-service-mixins-and-root-composition.md) (service attributes on the
router class, shared through an ordinary base class; root composition; clients supplied at
assembly), [phase 43](43-identity-and-session-scope.md) (auth
owned by an identity scope, not by a transport), [phase 44](44-client-config-grouping.md) (`ClientConfig` grouped by owner; closes the
deferred C2 item), [phase 45](45-attempt-state-machine.md) (attempt state machine instead of the
500-line loop), [phase 46](46-sans-io-core-and-drivers.md) (sans-io core, two thin drivers),
[phase 47](47-core-hygiene-and-surface.md) (kernel, module and public-surface hygiene) and
[phase 48](48-wire-pipeline.md) (one meaning for "wire", the projection/crypto/encode/sign pipeline
as data, and the fate of the unused scoped-signing API) and
[phase 49](49-protocol-envelopes.md) (protocol envelopes: JSON-RPC and other "method in the body"
APIs, with the envelope concept lifted out of the WebSocket package).
[Phase 52](52-pagination.md) adds declarative pagination: a `__pages__` strategy on the
operation class, `pages()`/`items()` on the bound operation, every page an ordinary `send()`.
[Phase 53](53-response-cases.md) fixes the response-case API: `when=` on every representation, a
condition ranked above status precision in arbitration, and a service envelope declared on the
model (`__envelope__ = Envelope(succeeds=..., payload=...)`) so a business failure inside a 200
becomes an ordinary `ApiError`.
[Phase 54](54-response-tags.md) replaces that temporary model-level verdict with response facts:
`Const(...)` tags select the case and `Payload[T]` names the value returned to the caller.
[Phase 55](55-pydoll-browser-adapter.md) adds a native Pydoll 3 page adapter to
`eazy_sdk_browser`; browser processes, contexts, leases, proxies and task retries remain owned by
an external orchestration layer such as `browser_pool`.
[Phase 56](56-documentation-overhaul.md) rebuilds the documentation site: four top tabs, one mail
SDK tutorial shown for both HTTP and browser on a shared teaching site, and every page snippet
included from a runnable file in `examples/`. It changes no core or plugin code.
[Phase 57](57-location-placements-extractors.md) (planned) lets a response state its outcome in
the `Location` header (`Location(...)` on a model field and in `when=`), places one session in
several request slots through `Placed` markers on the session model, and adds `Regex` and
`FromCookie` extractors for non-JSON responses.

## Цель

Eazy SDK должен иметь один execution path для hand-written и generated SDK-вызовов. Runtime
компилирует типизированный план, собирает логический запрос через identity-based slots,
адаптирует модели, строит Zapros request и подписывает ровно то представление, которое получает handler,
классифицирует ответ без exception-driven orchestration и выполняет только явно разрешённые
replay/reaction transitions.

Временное сосуществование старого и нового runtime допустимо в development branch для переноса
тестов. В релизной ветке и опубликованном wheel остаётся только новый путь: compatibility aliases,
deprecated wrappers и legacy client loop не создаются.

## Архитектурные инварианты

1. Обычный endpoint задаёт method/path/parameters/body/responses/optional security один раз; slots,
   shape, layout, wire requirements и binder выводятся compiler-ом.
2. Internal `ValueSlot[T]` сравнивается по identity; строковое имя используется только для schema,
   diagnostics и wire encoding.
3. Позиция значения принадлежит internal `RequestLayout`, а не порядку mutation. Replacement никогда не
   перемещает slot.
4. Query сохраняет порядок уникальных names; duplicate query names отклоняются до side effects.
   Headers сохраняют порядок, casing и повторы только в пределах verified handler profile.
5. Model conversion выполняет выбранный `ModelAdapter`, который соблюдает native serialization
   policy самой модели. Request placement не переопределяет aliases, defaults или field exclusion.
   Обычные JSON/form/multipart inputs кодирует Zapros, а exact/custom body кодируется Eazy SDK
   ровно один раз и передаётся через `body=`.
6. Zapros `BaseHandler`/`AsyncBaseHandler` является единственной HTTP transport extension boundary;
   `zapros.websocket.aconnect_ws`/`AsyncBaseWebSocket` является WebSocket boundary. Отдельные
   Eazy SDK send/transport protocols отсутствуют.
7. Signer читает только `SigningInput`, построенный из prepared representation. Wire order и
   canonical order являются независимыми декларациями.
8. Signature output заполняет заранее зарезервированный slot. После финальной подписи covered
   target, headers и body неизменяемы.
9. Capability mismatch обнаруживается до dependency/auth resolver calls, persistent commits и
   network side effects.
10. Каждый retry, reaction и redirect создаёт новый `AttemptState` и заново проходит preparation
   и signing.
11. Mechanical replayability тела и semantic idempotency операции проверяются раздельно.
12. Один `Responses` обслуживает JSON/HTML/XML/custom formats; author передаёт model и arbitrary
    protocol-compatible parser.
13. `NoMatch`, `Malformed` и ambiguous response cases представлены разными outcomes.
14. Документированный API response, preflight challenge и external protection signal имеют разные
    scope/lifecycle, но общий solver/replay coordinator.
15. Backend document одного parser-а строится не более одного раза на transport attempt.
16. Auth response handling использует фактически выбранную security alternative и её
    `AuthExecution`, а не default provider или строку в metadata.
17. Common call state живёт в protocol-neutral `OperationCallState`; HTTP budgets и attempt state
    принадлежат отдельным `HttpCallState` и `HttpAttemptState`;
    `RequestDraft.meta` не используется.
18. Sync и async runners исполняют одну state-machine specification и проходят одну
    параметризованную behavioral suite.
19. Secrets не попадают в `repr`, snapshots, exceptions или telemetry без явной redaction policy.
20. Procedural session service вызывает bound auth endpoints через `AuthContext.call(...)` того же
    executor-а; guards, solvers, dependencies, signing и replay не реализуются внутри service.
21. Signing имеет presets, lossless declarative projections и named procedural escape hatch;
    custom signer читает immutable prepared request и возвращает только declared outputs.
22. Payload crypto разделён на semantic document и encoded bytes stages. Общие declarations не
    объединяют HTTP/WebSocket state machines и не поставляют encryption algorithm или key storage.
23. Public request schema и wire body schema могут различаться только через compiled
    `BodyProjection`: projection выполняется на каждой attempt до единственного body encoding и не
    получает client/transport state.
24. Omitted optional projection value, explicit `None` и required missing value являются тремя
    разными states; diagnostics описывают operation/field/phase, а не только internal slot.
25. Mandatory operation protection, proactive before-call policy, conditional challenge и auth
    имеют разные typed lifecycle boundaries. Compound private clearance применяется атомарно и
    кешируется только внутри lifecycle одного client/handler session.
26. Solver объявляет только typed `Challenge -> Solution` contract. Browser, JavaScript, WASM и
    remote service не являются public capability flags; transport-affinity гарантируется
    session ownership, а не user-authored metadata.

## Границы rewrite

В scope входят request model, preparation, adapters, signing, response cases, signals/reactions,
dependencies, auth/session integration, public scoped middleware, shared executor, OpenAPI
generator и optional first-party protection presets.

Следующие границы зафиксированы:

- storage repositories, services, entities и SQLModel schema не передизайниваются;
- меняется только generic session codec/store bridge между auth и storage;
- extraction и rate limiting сохраняются как возможности и подключаются к новому core;
- OpenAPI 3.0, 3.1 и 3.2 остаются поддерживаемыми входными форматами;
- generic workflow engine, Arazzo и declarative auth workflow DSL не входят в rewrite; session
  acquisition остаётся procedural, bounded `before` protection flow входит в фазу 06;
- docs-site сохраняется и переписывается после стабилизации API;
- generated SDK не содержит пользовательских runtime-строк, lambdas, `attrgetter` или wiring
  через metadata.

## Фазы и dependency graph

Номер фазы определяет release-gate: следующая фаза не объединяется в release branch, пока не
выполнены exit criteria предыдущей. Разработка независимых частей может идти заранее, но их
интеграция подчиняется графу:

```text
00 baseline
   |
   v
01 core model/compiler
   |-----------------------> 05 response cases/extraction
   v                                  |
02 assembly/serialization             |
   v                                  |
03 prepared transports                |
   v                                  |
04 prepared signing ------------------+
                                      v
                         06 signals/reactions/replay
                                      |
                                      v
                         07 auth/session/dependencies
                                      |
          03 + 04 + 05 + 06 + 07 ----+
                                      v
                              08 shared executor
                                      |
                                      v
                              09 OpenAPI/codegen
                                      |
                                      v
                              10 removal/release
                                      |
                                      v
                         11 protection presets
                                      |
                                      v
                         12 typed request inputs
                                      |
                                      v
                         13 auth localhost/conformance
                                      |
                                      v
                         14 public API simplification
                                      |
                                      v
                         15 account lifecycle
                                      |
                                      v
                         16 SQLModel storage v2
                                      |
                                      v
                         17 declarative API methods
                                      |
                                      v
                         18 Zapros/model/extraction boundary
                                      |
                                      v
                         19 WebSocket runtime/AsyncAPI
                                      |
                                      v
                         20 unified payload crypto
                                      |
                                      v
                         21 public -> wire body projection
                                      |
                                      v
                         22 local package identity rename
                                      |
                                      v
                         23 production correctness/diagnostics
                                      |
                                      v
                         24 typed protection/managed clearance
                                      |
                                      v
                         25 authoring/testing ergonomics
                                      |
                                      v
                         26 compatibility/runtime hardening
                                      |
                                      v
                         27 extension surface
                                      |
                                      v
                         28 anti-bot API correctness
                                      |
                                      v
                         29 anti-bot API simplification
```

| Фаза | Результат |
|---|---|
| [00](00-baseline-and-legacy-map.md) | baseline, legacy inventory и characterization defects |
| [01](01-core-model-and-plan-compiler.md) | identity slots, atomic patches и typed execution plan |
| [02](02-request-assembly-and-serialization.md) | deterministic codecs и immutable prepared model |
| [03](03-prepared-transports.md) | `emit(PreparedRequest)`, capabilities и first-hop fidelity |
| [04](04-declarative-signing.md) | prepared signing, projections/scopes, custom hooks, DAG и golden vectors |
| [05](05-response-cases-and-extraction.md) | единые cases, arbitrary parser protocol и typed outcomes |
| [06](06-signals-reactions-and-replay.md) | preflight/API captcha/WAF, solvers и replay coordinator |
| [07](07-auth-session-and-dependencies.md) | typed dependencies, security execution и session integration |
| [08](08-shared-executor-and-clients.md) | одна sync/async state machine, public scoped middleware и минимальный client API |
| [09](09-openapi-ir-and-code-generation.md) | identity-preserving IR и generated typed facades |
| [10](10-legacy-removal-docs-and-release-gates.md) | удаление legacy, docs migration и release gates |
| [11](11-cloudflare-and-recaptcha-presets.md) | готовые Cloudflare/reCAPTCHA protection presets, overrides и vendor compatibility gates |
| [12](12-typed-request-inputs.md) | TypedDict/Unpack operation inputs, bound calls и короткий descriptor API |
| [13](13-auth-localhost-conformance.md) | localhost auth/session suite и OAuth2/OIDC conformance |
| [14](14-public-api-simplification.md) | response field sources, простой auth/session, typed client config/retry и минимальные exports |
| [17](17-declarative-api-methods.md) | decorated sync/async API methods, mandatory protection flows and removal of public contracts/calls |
| [18](18-zapros-model-codecs-and-html-extraction.md) | Zapros handler boundary, model adapters, custom codecs, compact Inject and offline HTML extraction |
| [19](19-websocket-runtime.md) | separate async WebSocket runtime over Zapros, subscriptions/recovery, GraphQL-WS and AsyncAPI 3.0 |
| [20](20-unified-payload-crypto.md) | typed application-owned field/encoded crypto profiles shared by separate HTTP and WebSocket runtimes |
| [21](21-public-wire-body-projection.md) | flat `Unpack` request signatures projected into private nested wire body schemas before encoding |
| [22](22-eazy-sdk-local-rename.md) | clean local rename of the core, plugins, CLIs, generated output and documentation |
| [23](23-production-correctness-and-diagnostics.md) | optional projection correctness, structured caller diagnostics and strict authoring docs |
| [24](24-typed-protection-policies-and-managed-clearance.md) | typed protection lifecycles, atomic private bindings and scoped persistent clearance |
| [25](25-authoring-and-testing-ergonomics.md) | explicit/defaulted signatures, response/parser conveniences, preparation testing and root lifecycle |
| [26](26-compatibility-and-runtime-hardening.md) | verified Python baseline and typed HTTP/compiler/WebSocket internal stages |
| [27](27-extension-surface.md) | minimal documented `eazy_sdk.ext` SPI and corrected alpha contract |
| [28](28-antibot-api-correctness-and-ergonomics.md) | public network identity, protection preflight/bundles, replay/lock correctness and fresh workspace gates |
| [29](29-antibot-api-simplification-and-session-affinity.md) | high-level custom guards, session-owned affinity, proof-based preflight and minimal protection surface |

Post-rewrite feature work is tracked separately and does not reopen the completed 00–14 rewrite
Definition of Done:

| Feature phase | State | Result |
|---|---|---|
| [15](15-account-registration-and-verification.md) | complete | transport-neutral account/session lifecycle, typed creation, atomic persistence, bounded verification and downstream HTTP/browser boundaries |
| [16](16-sqlmodel-account-storage-v2.md) | complete | five-table account storage, plaintext typed payloads, resource reservations, verification history and lifecycle events |
| [17](17-declarative-api-methods.md) | complete | replaced the public contract/bind/execute authoring surface from phases 08, 09, 12 and 14 |
| [18](18-zapros-model-codecs-and-html-extraction.md) | complete | Zapros handlers are the only HTTP transport boundary; model adapters, codecs, Inject and offline HTML extraction share one execution path |
| [19](19-websocket-runtime.md) | complete | async-only WebSocket operations, subscriptions, reconnect/recovery, protection, GraphQL-WS and AsyncAPI 3.0 without a universal HTTP/WS executor |
| [20](20-unified-payload-crypto.md) | complete | application-owned typed field/encoded encryption and decryption for HTTP and WebSocket with protocol-specific wire bindings |
| [21](21-public-wire-body-projection.md) | complete | typed public request to private wire body projection, per-attempt factories and removal of `wire_body=` |
| [22](22-eazy-sdk-local-rename.md) | complete | unpublished workspace identity rename without compatibility aliases |
| [23](23-production-correctness-and-diagnostics.md) | complete | projection omission correctness, caller diagnostics and strict documentation |
| [24](24-typed-protection-policies-and-managed-clearance.md) | complete | typed policy split, atomic private bindings and persistent anti-bot clearance |
| [25](25-authoring-and-testing-ergonomics.md) | complete | hand-written SDK authoring and offline request verification ergonomics |
| [26](26-compatibility-and-runtime-hardening.md) | complete | Python 3.13/3.14 support, typed runtime ownership and final release evidence complete |
| [27](27-extension-surface.md) | complete | 23-name implementation SPI, complete author reference and corrected alpha release |
| [28](28-antibot-api-correctness-and-ergonomics.md) | complete | public per-attempt identity, capability/identity preflight, installable bundles, replay/lock cleanup and fresh-checkout release evidence |
| [29](29-antibot-api-simplification-and-session-affinity.md) | complete | high-level custom guards, session-owned affinity, proof-based preflight, minimal protection surface and full release evidence |

Phases 00–29 are complete. Phase 27 corrected the accidental stable-release classification: the
project remains alpha and may make breaking public-surface changes without compatibility aliases.
Phase 28 closes the accepted anti-bot review findings without a compatibility layer. Evidence is
recorded in `STATUS.md`. Phase 29 corrects the false per-attempt identity/capability guarantees
discovered against `v0.2.0a2`; there is no incomplete implementation phase.

## Целевой public surface

Конкретные signatures фиксируются tests/API fingerprints в фазах 08–09. Namespace ownership
фиксируется сейчас:

| Namespace | Публичная ответственность |
|---|---|
| `eazy_sdk` | handler-based clients, API decorators, `ResponseEnvelope` и базовые errors |
| `eazy_sdk.request` | parameter/body descriptors, public-to-wire `BodyProjection` и sparse `WireOptions`; compiler request model internal |
| `eazy_sdk.response` | `Responses`, `Success`, `Error`, `Json`, `Html`, `Parsed`, envelopes и common field sources |
| `eazy_sdk.handlers` | first-party Zapros handlers; arbitrary transports implement Zapros handler protocols directly |
| `eazy_sdk.auth` | bearer/api-key/basic/cookie helpers, schemes, composite helpers и session facade |
| `eazy_sdk_accounts` | transport-neutral account/session lifecycle, registration outcomes, verification и persistence protocols |
| `eazy_sdk.middleware` | scoped call/attempt middleware, contexts, decisions и registration helpers |
| `eazy_sdk.protection` | high-level challenge solver, installable guard builders, replay proofs and caller-facing errors |
| `eazy_sdk.protection.advanced` | implementation SPI for typed policies, signals, private bindings and preset lowering; not the ordinary user path |
| `eazy_sdk_accounts.storage` | существующие repositories/services/facade; только новый session bridge |
| `eazy_sdk_html` | offline `parse_html`, selector metadata и optional HTML backends поверх extractor protocol |
| `eazy_sdk.codegen` | compact contracts/facades, которые импортирует generated source |
| `eazy_sdk.ext` | advanced hooks: custom predicates, codecs, signatures, signals/reactions и transport profiles |
| `eazy_sdk.websocket` | async WebSocket API, protocol/message contracts, subscriptions, recovery and WebSocket-specific middleware |

First-party vendor presets фазы 11 выпускаются отдельным optional distribution `eazy-sdk-presets`
с import surface `eazy_sdk_presets`. Core package не импортирует vendor modules и не получает
обязательной зависимости от DOM/browser/captcha backend.

Ожидаемая high-level поверхность клиента:

```python
await sdk.users.get(user_id=1)                       # -> User
await sdk.users.get.with_response(user_id=1)         # -> ResponseEnvelope[User]
await client.request("GET", "/health")               # -> NormalizedResponse[TRaw]
```

Все три метода компилируются в `ExecutionPlan` и вызывают один execution core. Verb helpers
являются тонкими typed wrappers над `request()` и не содержат собственного pipeline.

## Request state machine

Compile выполняется при создании/регистрации contract и transport configuration. До первого
resolver или сетевого вызова compiler валидирует slots, phases, graph cycles, writers,
signatures, replay policy и handler profile capabilities.

```text
COMPILE
  decorated API method + domain rules + middleware/other registries + handler profile
      -> ExecutionPlan

BIND (один раз на logical call)
  user arguments -> OperationValues -> HttpCallState(OperationCallState)

CALL MIDDLEWARE (один раз на logical call)
  scoped onion chain -> continue once OR typed short-circuit

ATTEMPT (для первой отправки и каждого replay/redirect)
  clone logical values
      -> select auth/dependencies
      -> resolve values
      -> run scoped attempt middleware contributors
      -> validate all patches
      -> atomic patch commit
      -> reserve rate-limit capacity / wait
      -> assemble logical RequestPlan
      -> adapt models and select standard Zapros input or exact codec bytes
      -> build Zapros Request
      -> derive digests/values and sign immutable request snapshot
      -> apply only reserved outputs
      -> run read-only before_emit middleware hooks
      -> Zapros handler
      -> ResponseContext
      -> run scoped attempt middleware response hooks
      -> inspect signals and endpoint cases
      -> arbitrate actions

TERMINAL
  return success/envelope
  OR materialize documented API error
  OR raise external-signal/configuration/transport error
```

`OperationCallState` владеет неизменяемыми bound values и call cache. `HttpCallState` добавляет
HTTP-specific независимые budgets:
transport failures, auth reactions, response reactions, redirects, backoff и общий hard limit.
`AttemptState` владеет только данными конкретной попытки, выбранными auth executions,
prepared artifacts и response context. Ни одна попытка не патчит уже подписанный artifact.

Новая middleware-система является частью целевого API, а не compatibility layer. `CallMiddleware`
оборачивает один logical call и получает single-use `call_next`; `AttemptMiddleware` исполняется
на каждой фактической попытке и возвращает typed patch/decision. Middleware не вызывает adapter и
не реализует скрытый retry loop: replay/redirect/backoff остаются решениями общего coordinator.

## Правила breaking rewrite

- Новый код не импортирует legacy types даже временно через adapter/shim.
- Старый и новый tests располагаются раздельно; target acceptance tests не утверждают legacy
  semantics.
- После переноса capability удаляются старые exports, tests и examples в той же фазе либо явно
  записываются как phase-10 debt.
- Нельзя добавлять новые возможности в `ResponseTrigger`, legacy retry, `Inject`, auth
  middleware и текущий `RequestDraft` signing path.
- Новый middleware API реализуется поверх typed execution plan; запрет legacy middleware не
  означает отказ от middleware как публичной возможности.
- Custom escape hatches живут в `eazy_sdk.ext`, имеют явную non-serializable маркировку и не
  используются generator-ом.
- Configuration errors детерминированы и по возможности содержат source pointer/slot identity,
  но не secrets.
- Любая capability с wire-fidelity обещанием подтверждается first-hop capture test; иначе
  profile объявляет более слабую capability и strict request отклоняется.
- Commit каждой фазы должен быть пригоден для bisect: target tests зелёные, незавершённый legacy
  path изолирован и не выбирается новым API.

## Общие acceptance criteria

- Обычный body передаётся Zapros ровно одним high-level input (`json=`, `form=`, `multipart=` или
  `body=`); exact/custom body передаётся только как окончательные bytes через `body=`.
- Если public request и wire body имеют разные schemas, compiler выполняет одну typed projection
  на каждой attempt; body codec получает только итоговый validated wire document.
- Missing optional projection fields остаются отсутствующими; explicit `None` остаётся значением;
  required missing fields падают до mapper, providers и network.
- Signer читает только immutable snapshot окончательного Zapros request или exact bytes того же
  request.
- Custom signer получает immutable full request view, объявляет reads и возвращает только reserved
  outputs; query/header/body projections сохраняют выбранные lossless values.
- Dependency replacement не меняет slot position.
- Duplicate query names отклоняются; supported header repeats и encoding metadata не теряются в
  пределах verified handler profile.
- Каждый replay и redirect заново проходит preparation и signing.
- API response и external protection signal представлены разными типами.
- Mandatory operation protection, before-call protection и conditional response challenge имеют
  разные typed configuration boundaries. Compound private outputs коммитятся атомарно и не
  используют auth registry как protection storage. Persistent response-challenge state не
  переживает lifecycle client/handler session.
- Auth использует реально выбранную security alternative.
- Procedural auth service вызывает generated routers scoped SDK; nested guards/signing исполняет
  общий pipeline, а не service.
- Scoped middleware работают одинаково для generic request, generated router, nested auth call и
  каждого replay attempt; второй вызов single-use `call_next` отклоняется.
- Attempt middleware могут атомарно патчить logical request до Zapros construction, читать
  immutable final request snapshot перед handler call и возвращать typed response/transport
  decisions; post-sign mutation запрещена.
- Sync и async клиенты проходят одну параметризованную behavioral suite.
- Generated SDK не содержит пользовательских runtime-строк, lambdas или hidden metadata wiring.
- Unsupported handler fidelity обнаруживается до resolver calls и network side effects.

## Definition of Done

Rewrite завершён, когда:

1. выполнены exit criteria всех фаз 00–14;
2. опубликованный package содержит один client/executor path;
3. legacy symbols отсутствуют в source, exports, generated snapshots, examples и docs;
4. full pytest, mypy и ruff gates зелёные для core и plugins;
5. docs freshness, metadata validation и Sphinx/Shibuya production build зелёные;
6. Zapros built-in и first-party handlers имеют честные capability declarations и capture
   evidence; arbitrary handler без evidence получает консервативный profile;
7. golden-byte fixtures доказывают равенство signed и emitted request representation;
8. OpenAPI 3.0/3.1/3.2 fixtures генерируются, импортируются, type-check-ятся и исполняются;
9. storage/SQLModel regression suite подтверждает отсутствие редизайна persistence слоя;
10. public call/attempt middleware проходят sync/async, replay, scope и nested-auth suites;
11. release audit не находит compatibility aliases, второй loop или ссылок на удалённый архив.

Для beta/stable дополнительно обязательны exit criteria фаз 23–26. Их завершение не переписывает
исторические evidence фаз 00–22: новые regression tests и gates добавляются отдельными записями в
`STATUS.md`.

Для следующего alpha release дополнительно обязательны exit criteria фазы 29. Она не меняет
исторический state фаз 00–28 и не разрешает compatibility layer для исправляемой public surface.
