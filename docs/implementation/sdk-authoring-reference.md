# Reference: разработка private SDK на Eazy SDK

Статус: целевой authoring API, включая approved projection contract фазы 21.

## Основная форма API

Автор объявляет операцию классом, а роутер публикует её через `op(...)`. Публичной сигнатурой
SDK становится конструктор класса:

```python
from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import (
    UNSET,
    AsyncApi,
    Http,
    HttpOperation,
    JsonField,
    Omittable,
    Path,
    Query,
    op,
)
from eazy_sdk.request import markers
from eazy_sdk.response import ApiError


class CreateUserError(ApiError[Problem]):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateUser(HttpOperation[User]):
    __http__ = Http.post(
        "/orgs/{org_id}/users",
        success={201: User},
        errors={400: CreateUserError},
    )

    org_id: Path[str]
    name: JsonField[str]
    email: JsonField[str]
    locale: Query[Omittable[str]] = UNSET
    trace: Annotated[Omittable[str], markers.Header("X-Trace")] = UNSET


class AsyncUsers(AsyncApi):
    create = op(CreateUser)
```

`HttpOperation[T]` задаёт и успешный случай, и тип публичного вызова. Класс может быть dataclass,
Pydantic-моделью или msgspec-структурой; требование одно — он frozen, потому что запрос является
значением: его можно построить (`create.request(...)`), изменить (`evolve`) и отправить (`send`).
Значения по умолчанию — обычные Python defaults вместе с `default_factory`; поле, которое иногда
не нужно отправлять, объявляется `Omittable[T] = UNSET`.

Короткая форма — декоратор `@api.post(...)` над методом; он синтезирует такой же frozen-класс,
доступный как `AsyncUsers.create.Operation`. Оставляйте `raise NotImplementedError` как
недостижимое тело: strict mypy отклоняет `...` как `empty-body`. Результату `.with_response(...)`
при необходимости задайте локальную аннотацию `ResponseEnvelope[User]`.

`Http` и `api` — два написания одних и тех же глаголов: `Http.get`/`api.get`, `Http.post`/
`api.post`, ..., `Http.request(method, path)`/`api.request(method, path)`.

`Annotated` placement разбирается при создании класса. Одно поле имеет ровно одно placement; body
encoding нельзя смешивать с другим root/flat body encoding. Короткая форма `Query[int]` — это
`Annotated[int, markers.Query()]`; полная нужна там, где у маркера есть аргументы.

Вся HTTP-форма операции объявляется полями одного класса: path, query, headers, cookies и body
fields лежат рядом с placement metadata. Dataclass/Pydantic/msgspec под `markers.JsonBody()`
используются для вложенных документов или model-owned validation, а не как ceremonial wrapper
вокруг нескольких scalar request fields.

## Request placements

Поддерживаются `Path`, `Query`, `Header`, `Cookie`, flat `JsonField`/`Form`/`Part` и root
`JsonBody`/`FormBody`/`MultipartBody`/`BytesBody`/`ReplayableStreamBody`. Default wire order —
порядок параметров и полей модели; sparse override задаётся через `WireOptions`.

Repeated query keys не поддерживаются. Для collection нужен single-value codec:

```python
tags: Annotated[list[str], Query("tag", codec=DelimitedScalarCodec(","))]
```

## Pagination

Стратегия страниц объявляется на классе операции атрибутом `__pages__` рядом с `__http__`;
цикл живёт в библиотеке. `page=`/`size=` — python-имена полей операции (следующий запрос
строится через `evolve()`), класс результата передаётся первым аргументом, чтобы лямбды были
типизированы, а `op()` сверяет его с `HttpOperation[T]` при импорте.

```python
from eazy_sdk.pagination import Pages

@dataclass(frozen=True, slots=True, kw_only=True)
class ListDocuments(HttpOperation[DocumentsPage]):
    __http__ = Http.get("/documents")
    __pages__ = Pages.numbered(
        DocumentsPage,
        page="page",
        size="per_page",
        items=lambda r: r.items,
        total_pages=lambda r: r.pages_count,
    )

    case_id: Query[str]
    page: Query[int] = 1
    per_page: Query[int] = 25

for page in api.documents.pages(case_id=cid):                      # DocumentsPage
    ...
for doc in api.documents.items(case_id=cid, key=lambda d: d.id):  # elements, deduplicated
    ...
```

Остановка, по порядку: пустая страница (или ни одного нового элемента при `key=`), достигнут
`total_pages`, страница короче `size`, достигнут `max_pages=`. Первая страница — та, что в полях
запроса. `options=` уходит в каждый `send()`. Обычный вызов операции не меняется. `Pages` не
экспортируется из корня `eazy_sdk` (бюджет имён). `Pages.offset(Model, offset=, limit=, items=,
total=)` адресует страницы смещением и двигает его на длину реально полученной страницы. План и
остальные стратегии — `52-pagination.md`.

## Public request и private wire body

Если caller-facing schema должна оставаться плоской, а protocol требует другой nested document,
используется отдельная `BodyProjection`. Public `TypedDict` остаётся источником `Unpack` signature;
private wire model владеет реальной структурой body:

```python target
from typing import TypedDict, Unpack

from eazy_sdk import AsyncApi, api
from eazy_sdk.request import Body, BodyProjection


class RegisterUser(TypedDict):
    login: str
    email: str
    first_name: str
    last_name: str


class RegisterUserWire(TypedDict):
    account: AccountWire
    profile: ProfileWire
    client: ClientWire


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUser(HttpOperation[RegisteredUser]):
    __http__ = Http.post(
        "/register",
        projection=BodyProjection(
            target=RegisterUserWire,
            using=register_to_wire,
            encoding=Body.json(),
        ),
    )

    login: str
    email: str


class RegistrationApi(AsyncApi):
    register = op(RegisterUser)
```

`register_to_wire` — structural callable `(RegisterUser) -> RegisterUserWire`; источник проекции —
сам класс операции, поэтому поля объявлены один раз. Это может быть
обычная функция или generated Adaptix converter; core не зависит от mapper library. Constants и
pure factories допустимы и вычисляются на каждой attempt. Auth, CSRF, CAPTCHA, session и другие
client-state values применяются отдельными typed wire writers после projection.

Минимальный plain-function mapper не требует дополнительной зависимости:

```python target
def register_to_wire(source: RegisterUser) -> RegisterUserWire:
    return {
        "account": {"login": source["login"], "email": source["email"]},
        "profile": {
            "first_name": source["first_name"],
            "last_name": source["last_name"],
        },
        "client": make_client_wire(),
    }
```

Если SDK уже использует Adaptix, тот же callable contract можно получить без adapter-а Eazy SDK:

```python target
from adaptix import P
from adaptix.conversion import get_converter, link_constant, link_function


def account_wire(source: RegisterUser) -> AccountWire:
    return {"login": source["login"], "email": source["email"]}


def profile_wire(source: RegisterUser) -> ProfileWire:
    return {
        "first_name": source["first_name"],
        "last_name": source["last_name"],
    }


register_to_wire = get_converter(
    RegisterUser,
    RegisterUserWire,
    recipe=[
        link_function(account_wire, P[RegisterUserWire].account),
        link_function(profile_wire, P[RegisterUserWire].profile),
        link_constant(P[RegisterUserWire].client, factory=make_client_wire),
    ],
)
```

Adaptix остаётся зависимостью приложения/SDK authoring environment; core package его не импортирует.

Поля projection source могут не иметь placement metadata: их body ownership задаёт сама
`BodyProjection`. Любое поле operation вне source по-прежнему требует `Path`, `Query`, `Header`,
`Cookie` или другой обычный placement. Projected body нельзя смешивать с flat/root body в той же
operation.

Projection возвращает semantic wire document, а не bytes. `Body.json()`/form/multipart либо custom
`BodyCodec` кодирует уже validated target один раз. Retry, redirect, auth replay и protection
reaction повторяют projection, encoding, crypto и signing.

## ModelAdapter

Model conversion отделена от HTTP serialization. Registry по умолчанию содержит adapters для
dataclass, Pydantic v2 и msgspec Struct, сохраняет declaration order и используется request,
response, response headers и HTML extraction.

Serialization policy принадлежит самой модели. `Body.json()`, `Body.form()` и `Body.multipart()`
объявляют placement/media type и не управляют aliases, defaults, unset или `None`. Pydantic adapter
вызывает `model_dump(mode="json")`; msgspec adapter соблюдает `rename`, `omit_defaults`, tags и
другие Struct options; plain dataclass включает все объявленные поля. Для другой dataclass policy
нужен custom adapter.

Adapter получает representation mode: `json` для JSON/form и `python` для multipart, где нужно
сохранить binary values. Этот mode не управляет aliases/defaults/exclusion.

Pydantic не имеет `ConfigDict`-эквивалента `exclude_unset=True`: это параметр `model_dump`.
Отдельная PATCH-модель может владеть этим default через собственный `model_dump`/serializer.
OpenAPI generator выпускает для этого `OpenAPIModel`, который исключает только незаданные поля и
сохраняет явно переданный `None` как JSON `null`.

Custom adapter реализует `name`, `supports_type`, `supports_value`, `fields`, `dump` и `load`:

```python
models = default_model_adapters().with_adapter(VendorModelAdapter())
config = ClientConfig(models=models)
```

`with_adapter(...)` добавляет новую model family. Для намеренной замены встроенного adapter-а
используйте `replace_adapter("dataclass", ProjectDataclassAdapter())`; replacement сохраняет имя.

Имена adapters уникальны. Ноль совпадений даёт `UnsupportedModelTypeError`, несколько совпадений —
`AmbiguousModelAdapterError`. Adapter возвращает semantic primitives и не должен кодировать JSON
bytes или мутировать input object.

## BodyCodec и ScalarCodec

`BodyCodec` получает уже адаптированный semantic document и `EncodeContext(operation_id=...)`, а
возвращает окончательные bytes:

```python
import json
from typing import Annotated, TypedDict, Unpack

from eazy_sdk import AsyncApi, api
from eazy_sdk.codecs import EncodeContext


class CanonicalJson:
    name = "vendor-canonical-json"
    media_type = "application/vnd.vendor+json"

    def encode(self, document: object, context: EncodeContext) -> bytes:
        return json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True, slots=True, kw_only=True)
class CreatePayment(HttpOperation[Payment]):
    __http__ = Http.post("/payments", signing=PAYMENT_SIGNATURE)

    body: Annotated[PaymentInput, CanonicalJson()]


class AsyncPayments(AsyncApi):
    create = op(CreatePayment)
```

К phase-21 target `BodyCodec` получает уже адаптированный semantic wire document: model selection и
dump завершены до codec, `EncodeContext` не предоставляет model registry. Полученные bytes
передаются Zapros через `body=` и подписываются без повторной сериализации.
`ScalarCodec` возвращает одну строку для одного path/query/header/cookie/form slot; создавать два
query pairs он не может.

Standard `JsonBody`, `FormBody` и `MultipartBody` используют high-level inputs Zapros. Exact mode
выбирается для custom bytes, body-embedded signature и требований, которые нельзя безопасно
выразить standard encoding.

## ResponseExtractor

Extractor извлекает primitive tree или raw value. Model construction всегда выполняет registry:

```python
class VendorExtractor:
    name = "vendor"

    def bind(self, response: ResponseContext[object]) -> BoundVendorExtractor:
        return BoundVendorExtractor(response)


RESPONSES = Responses(
    success=(Success(200, Extracted(VendorReply, using=VendorExtractor())),),
)
```

Bound extractor возвращает `ParsedValue`, `NoMatch` или `Malformed`. Runtime кеширует parsing по
identity extractor-а внутри response attempt. `Json(Model)` и `Html(Model)` являются готовыми
extractor compositions.

## HTML schema

HTML model использует только field metadata:

```python
@dataclass
class Product:
    name: Annotated[str, CSS(".name::text")]
    price: Annotated[float, XPath(".//*[contains(@class, 'price')]/text()")]


@dataclass
class Catalog:
    title: Annotated[str, CSS("h1::text")]
    products: Annotated[list[Product], Scope(CSS(".product"))]
```

`parse_html(data, Catalog)` работает offline. Для response используется `Html(Catalog)`. Selector
engine извлекает primitives, а не вызывает Pydantic/msgspec/dataclass напрямую. Ошибка содержит
полный model field path и selector.

## Inject

Request-owned значения можно объявить без параметра публичного method:

```python
from typing import Annotated, TypedDict, Unpack

from dataclasses import dataclass

from eazy_sdk import AsyncApi, Http, HttpOperation, Inject, JsonField, op
from eazy_sdk.request import markers


@dataclass(frozen=True, slots=True, kw_only=True)
class Login(HttpOperation[UserSession]):
    __http__ = Http.post(
        "/login",
        inject=(
            Inject(markers.Header("X-Device-ID"), device_id),
            Inject(markers.Query("timestamp"), unix_timestamp),
        ),
    )

    email: JsonField[str]
    password: JsonField[str]


class LoginApi(AsyncApi):
    login = op(Login)
```

Source может быть constant, sync/async callable или provider с одним `DependencyContext`.
Compiler создаёт обычный dependency slot; отдельного DI runtime для `Inject` нет. Коллизии с
публичными wire names отклоняются при compile.

## Protection policies

Обычный custom response guard описывается одним installable builder-ом:

```python target
guard = challenge_guard(
    name="kad.wasm",
    scope=host("kad.arbitr.ru"),
    detect=detect_kad_challenge,
    solver=KadSolver(session=session, runtime=wasm_runtime),
    apply=solution_fields(
        cookies={"pr_fp": "pr_fp", "wasm": "wasm"},
    ),
    replay=rejected_before_origin(max_replays=1),
)

config = ClientConfig().with_protection(guard)
```

Detector получает immutable `ResponseContext` и возвращает typed challenge или `None`.
`solution_fields()` объявляет fixed headers, query fields, cookies, body paths или dynamic cookie
set. Destinations резервируются compiler-ом и отсутствуют в public signature. Весь batch
валидируется до единственного commit перед новой preparation и signing. Detector и solver не
получают `PreparedRequest`.

First-party preset использует тот же `with_protection()` path. Solver реализует только typed
`Challenge -> Solution`; browser, JavaScript, WASM или remote API являются implementation details,
а не capability flags.

Managed state принадлежит lifecycle одного client/handler session. Proxy, User-Agent и
impersonation фиксируются при construction handler-а. Для rotation внешний pool создаёт новый
handler и client; новый client не получает старый clearance. Если solver и HTTP должны разделять
session/lease, composition root создаёт их вместе. Не используйте auth/session registry как
storage anti-bot cookies.

Framework и generated-policy authors импортируют low-level contracts только из
`eazy_sdk.protection.advanced`: `ResponseSignal`, `SolverRequirement`, `PrivateBindings`,
`ProtectionPersistence`, binding registries и `ProtectionBundle`. Runtime/compiler/cache helpers
остаются private. Mandatory operation protection и proactive before-call policies также остаются
в advanced SPI.

## Client и handler

```python
client = AsyncClient(
    base_url=BASE_URL,
    handler=custom_zapros_handler,
    config=ClientConfig(resilience=Resilience(retry=RetryPolicy.safe(max_attempts=3))),
)
identity = Identity(auth=(auth,), dependencies=providers)
sdk = VendorSdk(client, identity=identity, serialization=Serialization(models=models))
```

Клиент несёт транспортную политику тремя группами: `resilience=` (retry, auth-replay, redirects,
timeout, rate limiter), `security=` (guard/anti-bot) и `hooks=` (middleware). Сессия, ключи подписи
и DI живут в `Identity` — их владелец вызывающая сторона, а не транспорт. Один `Identity`
обслуживает все роутеры одного корня, в том числе когда сервисам выданы разные клиенты.

### Дефолт против DI

| Что | Где объявляется | Почему |
|---|---|---|
| retry, timeout, redirects, rate limiter, guard, middleware | `ClientConfig` | есть разумный дефолт, меняется редко, свойство исполнителя |
| credentials и сессия, ключи подписи, DI, observer | `Identity` | зависит от вызывающей стороны и окружения, часть — секреты |
| адрес, `errors`, `security`, `signing`, профили crypto, `allow` | роутер или миксин сервиса | это требует сервер: контракт API |
| представление тела (`Wire`, фаза 48) | операция | влияет на байты, от которых считается подпись |
| библиотека моделей и бэкенд кодирования (`Serialization`) | корень SDK | на байты не влияет: это реализация |

Отсюда правило про подпись: алгоритм — поле контракта (сервер требует именно такую подпись), ключ —
DI (секрет пользователя). Клиент не объявляет ни signer, ни cipher: его дело — доставить байты.

Если procedural auth service вызывает `context.sdk`, фабрику scoped SDK регистрирует тот объект,
которому передан `identity=`: корень (`VendorSdk(client, identity=identity)`) или отдельный роутер.

```python
sdk = VendorSdk(client, identity=identity)
```

Во время login/refresh runtime вызывает ту же фабрику с non-owning scoped client, который разделяет
transport и несёт parent lifecycle graph. Service не хранит root SDK. Сессия, объявившая
собственный `sdk_factory`, свой остаётся.

Custom transport реализует Zapros `BaseHandler` или `AsyncBaseHandler`. Eazy SDK transport adapter
не пишется. First-party HTTPX, Requests и curl_cffi handlers находятся в `eazy_sdk.handlers.*`.
Handler может публиковать read-only `profile`; иначе используется conservative profile.

Native redirect, retry, cookie persistence и auth должны быть выключены. Все повторные попытки
координирует Eazy SDK, чтобы заново выполнить auth/dependencies/protection и signing.

## Phase 25 authoring surface

- Hand-written operations use explicit keyword-only parameters with real Python defaults.
- Reusable/generated operations declare the same operation classes; the generator emits them.
- Direct request parameters and `Unpack` do not mix, except for the separate `options` control.
- `HttpOperation[T]` is the success case; `success=` is the multi-case form, and the family
  (JSON or document) is read from the model rather than declared.
- The router's `errors` attribute is inherited unless `inherit_errors=False`.
- Use `callable_parser(Model, callback)` for a typed single-model parser. `CallableParser` is
  removed without an alias.
- Bound operations expose `.prepare(...)`, which stops the ordinary executor before handler send.
- `SyncRoot`/`AsyncRoot` with `api_group()` compose routers; `from_handler()` gives the root an
  owned client. Routers declare `base_url`, `errors`, `security`, `signing`, `crypto`,
  `wire`, `protocol`, `signed` and `allow`; clients declare transport policy only.
- `api.head`, `api.options`, `api.trace` and `api.request(method, ...)` share the same compiler.
- `api.rpc(discriminator, ...)` is a member of that same namespace, for a service whose method
  name travels in the body. The URL and verb come from the router's `protocol` envelope
  (`eazy_sdk.protocols.JsonRpc`); declaring `@api.rpc` without one fails at class creation.
  JSON-RPC errors use the ordinary `Responses` path: `rpc_result`, `rpc_error(code, model)` and
  `rpc_error_default(model)` (the last belongs in `fallback=`).
- The `eazy_sdk` root exports 38 names: declaration, composition, request markers, response
  contract. Model adapters, codecs, error bases and transport failures keep their own modules.

`PreparedCall` is a redacted public view, not a second mutable request model. For wire-boundary
tests use `eazy_sdk.testing.RecordingHandler` or `AsyncRecordingHandler`.

## Signing

Declarative signing graph, projections и reserved outputs сохраняются. Подпись объявляется
только по месту — на операции или на роутере/миксине сервиса; адресных (host/path) правил нет,
а `signed = True` на роутере делает операцию без подписи ошибкой компиляции. Signer читает
immutable snapshot финального request. Custom exact codec подписывается по уже полученным bytes;
standard body — по body сформированного Zapros `Request`.

Retry, redirect, reaction и auth refresh создают новую attempt и новую подпись. Нельзя подписать
`b'{"a": 1}'`, а отправить Zapros JSON `b'{"a":1}'`.

## Минимальный набор tests SDK

- class creation/compile для каждой public signature;
- отсутствие duplicate query names и repeated expansion;
- round-trip request/response для выбранных model libraries;
- golden standard/exact body bytes и first-hop signature;
- новый request/signature на каждом replay path;
- success/error/malformed/ambiguous response cases;
- fixture-only HTML extraction и handler-free parsing;
- arbitrary fake Zapros handler для SDK integration;
- strict mypy для generated package и consumer.

## Anti-patterns

| Anti-pattern | Целевой механизм |
|---|---|
| transport-specific client wrapper | `Client(handler=...)` |
| model library duck typing в parser/request code | `ModelAdapterRegistry` |
| custom adapter кодирует JSON bytes | `BodyCodec` |
| extractor строит Pydantic/msgspec object | extractor primitives + model adapter |
| middleware/retry Zapros | Eazy SDK coordinator |
| signer сериализует body повторно | Zapros request snapshot или exact codec bytes |
| provider добавляет новый неизвестный slot | compiled `Inject`/dependency patch |
| `tag=a&tag=b` | single-value `ScalarCodec` |
