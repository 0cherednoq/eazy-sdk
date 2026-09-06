# Reference: целевой SDK на Eazy SDK

Статус: consumer-facing reference. API для автора SDK описан в
[sdk-authoring-reference.md](sdk-authoring-reference.md).

## Обычный вызов

Generated SDK предоставляет typed facade над одним Eazy SDK client:

```python
from museum_sdk import AsyncAPI

async with AsyncAPI.httpx(base_url="https://museum.example") as sdk:
    hours = await sdk.operations.getMuseumHours()
```

`httpx()` — удобная фабрика generated package. Она создаёт обычный Zapros handler и делегирует
transport-neutral `from_handler(...)`; отдельного execution path у неё нет.

Для другого транспорта передайте любой совместимый Zapros handler в ту же generated factory:

```python
from zapros import AsyncStdNetworkHandler
from museum_sdk import AsyncAPI

sdk = AsyncAPI.from_handler(
    base_url="https://museum.example",
    handler=AsyncStdNetworkHandler(),
    credentials=credentials,
)
```

`from_handler(...)` также принимает готовую `session`, `ClientConfig` и `owns_handler`. Sync API
принимает синхронный `zapros.BaseHandler`.

## Сигнатуры операций

HTTP-поля видны как нормальные параметры Python. Placement metadata уже скомпилирована автором
SDK и не передаётся при вызове:

```python
ticket = await sdk.tickets.getTicketCode(ticket_id="ticket-42")

confirmation = await sdk.tickets.buyMuseumTickets(
    body=BuyMuseumTickets(ticket_type="general", event_id="event-1"),
)
```

`options: CallOptions | None` — единственный зарезервированный аргумент управления отдельным
вызовом. Auth, response cases, codecs, signing и protections являются частью SDK/client config, а
не kwargs каждой операции.

## Модели

Один client может работать с dataclass, Pydantic v2 и msgspec Struct. Эти библиотеки не задают
wire encoding сами: `ModelAdapter` сначала преобразует модель в primitive tree, после чего Zapros
или выбранный `BodyCodec` формирует body.

Плоский object-shaped request является `TypedDict` и раскрывается в именованные аргументы через
`Unpack`: caller пишет `create_post(user_id=7, title="Typed", body="No wrapper dict")`. Model
object используется для response, вложенного request document или model-owned
validation/serialization policy; оборачивать несколько scalar request fields в
Pydantic/dataclass/msgspec запрещено в документации и first-party examples.

Плоская public schema не обязана повторять wire JSON. SDK может скомпилировать отдельную
`BodyProjection`, поэтому тот же caller пишет:

```python
registered = await sdk.registration.register(
    login="john",
    email="john@example.com",
    first_name="John",
    last_name="Smith",
)
```

при этом private wire schema может содержать nested `account`, `profile` и `client` objects,
constants и per-attempt timestamp. Эти детали не появляются в signature и не требуют wrapper dict.
Projection выполняется заново на retry/redirect до единственного body encoding.

Aliases, declaration order, defaults и optional values обрабатываются единым
`ModelAdapterRegistry`. Пользовательская модель не мутируется.

## Ответы и typed errors

Обычный метод возвращает значение success case. Для status, headers и raw response используется
тот же method через `.with_response(...)`:

```python
result = await sdk.tickets.buyMuseumTickets.with_response(body=request)

confirmation = result.value
request_id = result.headers.get("x-request-id")
status = result.status_code
```

Документированная ошибка API содержит typed payload в `.error` и безопасный response context.
Unexpected, malformed и ambiguous responses остаются разными ошибками и не маскируются под API
error.

## Авторизация и повторные попытки

Auth настраивается в `ClientConfig`. Static token, procedural login, session reuse и refresh
используют тот же executor, что и основная операция. Retry, redirect, auth refresh и protection
replay каждый раз создают новый Zapros `Request`, повторно применяют зависимости и вычисляют новую
подпись по финальным bytes.

Zapros middleware, retry, redirects, cookie store и auth policies Eazy SDK не использует.

First-party anti-bot preset или custom `challenge_guard()` устанавливается одним immutable
`ClientConfig.with_protection()` expression. Detector, solver и declarative `solution_fields()` не
расширяют public operation signature. Managed state принадлежит lifecycle одного client/handler
session; смена proxy или impersonation создаёт новый handler и client.

Low-level `operation_protections`, policy, signal, requirement, binding и persistence contracts
доступны авторам SDK через `eazy_sdk.protection.advanced`. Обычный consumer их не собирает и не
использует auth registry для anti-bot state.

## HTML

HTML endpoint выглядит как любой другой typed endpoint:

```python
dashboard = await sdk.auth.login(email="user@example.com", password="secret")
```

`Html(Model)` извлекает primitive tree по `Annotated[..., CSS(...)]`, `XPath(...)` и `Scope(...)`,
после чего тот же model adapter создаёт dataclass/Pydantic/msgspec object. Offline-вариант доступен
напрямую:

```python
from eazy_sdk_html import parse_html

page = parse_html(saved_html, DashboardPage)
```

Парсинг не требует client или HTTP-запроса.

## Raw request

Для незадокументированной ручки доступен high-level API самого client:

```python
response = await client.get("/experimental", params={"mode": "compact"})
```

Один query name соответствует одному wire value. `tag=a&tag=b` и list, разворачиваемый в
повторяющиеся ключи, отклоняются до provider calls и network I/O. Коллекцию нужно закодировать в
одно значение (`tag=a,b`) на уровне SDK.

## Authoring and offline verification

`HttpOperation[T]` is the successful case, and the family is read from the model: selectors mean a
document, anything else means JSON. `success=`, `errors=` and `fallback=` take a mapping from a
status, a range or `DEFAULT` to a model, a representation or an exception class.
Common error cases live in the router's `errors` class attribute (inherited through its MRO);
operation errors extend them by default.

Declare the operation as a frozen class published with `op(...)`, or with the `@api.*` decorator,
which synthesizes the same class. `BodyProjection` takes the operation itself as its source and
observes the fields' own defaults.

`operation.prepare(..., options=PrepareOptions(...))` executes the ordinary request pipeline and
stops before network emission. `eazy_sdk.testing` records actual Zapros requests. Root SDKs can be
declared with `SyncRoot`/`AsyncRoot` and `api_group()`; a root closes only the clients it created
itself through `from_handler()`. Service addresses live on the routers; `bind(cls, client=…,
base_url=…)` overrides them at assembly.

`eazy_sdk` exports what an SDK author needs in the first hour — the declaration surface, the
composition surface, the request markers and the response contract. Model adapters live in
`eazy_sdk.models`, codecs in `eazy_sdk.codecs`, error bases in `eazy_sdk.core.errors`, transport
failures in `eazy_sdk.clients` and `eazy_sdk.handlers`. Nothing is hidden; nothing is spelled
twice.

## Lifecycle

Handler по умолчанию принадлежит client и закрывается вместе с ним. Для разделяемого handler
нужно явно указать `owns_handler=False`. Sync/async mismatch отклоняется при создании client.

## Гарантии границы

- один общий executor для hand-written и generated SDK;
- единственная transport extension boundary — Zapros `BaseHandler`/`AsyncBaseHandler`;
- порядок уникальных query/header/body fields следует compiled declaration order;
- custom exact body передаётся Zapros как окончательные `bytes` через `body=`;
- подпись вычисляется по сформированному Zapros request или тем же exact bytes;
- third-party handler получает conservative profile, verified profile можно передать явно;
- старых `wrap_*`, prepared adapters и compatibility client wrappers нет.

## Что пользователь SDK не настраивает

- compiler slots, layouts и request patches;
- отдельные transport adapters Eazy SDK;
- Zapros middleware/retry/auth;
- parser-specific validation модели;
- ручной replay после challenge/401/redirect;
- повторяющиеся query keys.
