# Декларативные операции Eazy SDK: что взять из unihttp и как это соединить с нашим рантаймом

Статус: архитектурное предложение, решения 1–21 из §12 приняты 2026-09-06. Основа для фазы 50 (после фаз 42–49, до 0.3.0).
План исполнения: [`50-declarative-operations.md`](50-declarative-operations.md); при расхождении дизайн отвечает за намерение, план — за механизм.

Дата: 2026-09-06.

Читатель: maintainer Eazy SDK.

Источники, на которых основан документ:

- `unihttp` 0.3.0 (`bind_method.py`, `method.py`, `markers.py`, `serializers/adaptix/*`,
  `clients/base.py`, `middlewares/*`) и пример SDK `kad_client` (`client.py`, `methods/*`,
  `serialization.py`, `middlewares/*`);
- `eazy_sdk/api.py`, `request/*`, `response/cases.py`, `compile/*`, `clients/executor.py`,
  `protocols/*`, `websocket/*`;
- `experiments/response_case_sugar/*` (выводы README, особенно `h_error_mapping.py`,
  `i_response_shape.py`, `j_html_service.py`), `experiments/api_authoring_alternatives/`,
  `experiments/declarative_operation_api/`;
- `private_sdk_endpoint_architecture.md`, `eazy-sdk-websocket-architecture.md`,
  `docs/implementation/STATUS.md` (решения фаз 42–49).

---

## 1. Решение в одном абзаце

Ввести **операцию как типизированное значение**: класс-модель запроса с маркерами размещения
полей (`Path`, `Query`, `Header`, `Cookie`, `JsonField`, `Form`, `Part`, `Body`) и контрактом ответа
на уровне класса (`success=`, `errors=`, `decode=`, `signing=`, `crypto=`, `wire=`). Публикуется на
роутере одним дескриптором `op(GetOrder)`, который отдаёт IDE сигнатуру `__init__` модели
(все kwargs, defaults и `default_factory`), а рантайму — тот же `HttpOperationDeclaration`, что
сегодня строит `@api.get`. Нынешний декоратор остаётся как короткая форма для плоских
endpoint'ов и **компилируется в ту же операцию-класс**, поэтому путь исполнения один. Ответы
описываются мэппингом `{status: Model | ExceptionClass}`, а чем читать тело, не объявляется
вовсе: семью экстрактора определяют метаданные самой модели, потому что модель с селекторами
может прочитать только документный бэкенд. Конверт ответа снимается сервисным `unwrap`.
Протоколы, то есть HTTP, конверт JSON-RPC, вызов и подписка WebSocket, позже GraphQL,
различаются **номинальным базовым классом операции**, а не строкой `transport=`, ровно как
записано в WS-документе.

---

## 2. Что именно хорошо в unihttp (и чего у нас нет)

| Свойство unihttp | Где оно живёт | Есть ли у нас | Комментарий |
|---|---|---|---|
| Запрос = dataclass с маркерами `Path[str]`, `Query[int]`, `Body[...]`, `Header[str]`, `Form`, `File`, `Raw` | `markers.py`, `method.py` | Частично: маркеры есть (`Annotated[int, Path()]`), но **только в сигнатуре функции или TypedDict**; запрос не является значением | Главный разрыв. |
| Короткая запись маркера `Query[int]` через обобщённый алиас над `Annotated` | `markers.py` | Нет: только `Annotated[int, Query()]` | Берём. Алиас забирает короткое имя, дата-класс уходит в `markers.` (§4.3). |
| `bind_method(Method)` → у клиента метод с сигнатурой `__init__` модели, sync/async выбирается по типу клиента через overload | `bind_method.py` | Похоже: `_SyncOperationDescriptor`/`_AsyncOperationDescriptor` с `ParamSpec` над функцией | Наш дескриптор уже умеет ParamSpec; надо лишь принимать `type[Operation]` как источник `P`. |
| Defaults и `field(default_factory=...)` прямо в объявлении запроса | dataclass | Нет для `Unpack[TypedDict]` (TypedDict не имеет defaults), есть для kwargs-формы | Именно поэтому нужна модель-значение, а не TypedDict; это же подтверждено выводом 1 README экспериментов. |
| Запрос переиспользуется: `replace(search, page=page)` в пагинации, `call_method(PrepareKadPdf(...))` внутри сервиса | `client.py:search_pages`, `download_pdf` | Нет: `prepare()` даёт `PreparedCall`, но нет переиспользуемого объекта запроса до подготовки | Второй важный разрыв; см. §7 (пагинация). |
| Тип ответа в generic-аргументе: `BaseMethod[SearchResult]` | `method.py:__init_subclass__` | Есть аналог: return annotation + fallback `api.py:750-756` | Берём обе формы: generic = default success. |
| Per-method хуки `validate_response`, `on_error`, `make_response` | `method.py` | Лучше: `Success/Error(..., when=)`, `Responses.fallback`, `ProtectionPolicy`, `AttemptMiddleware` | Не переносим императивные хуки; см. §6. |
| Middleware-цепочка `handle(request, next)` с per-call middleware | `clients/base.py` | Лучше: `CallMiddleware` + `AttemptMiddleware` с декларативными решениями, state machine попыток | Ничего не берём. |
| `Omitted` — «не отправлять поле» | `omitted.py` | Есть `UNSET` (`sentinels.py`), но он не документирован как значение поля запроса | Закрепить `UNSET` как default для optional query/header в операциях-классах. |
| Имя на проводе: у маркеров его нет вовсе, только `name_mapping` в централизованном `Retort` | `markers.py`, `serialization.py` kad_client | Лучше: имена принадлежат модели, `ModelAdapterRegistry` читает alias'ы pydantic и `rename` msgspec | Измерено: `PydanticDumper` и `MsgspecDumper` в unihttp берут ключи из `vars(obj)` и alias'ы игнорируют. См. §4.3. |
| Модель запроса: только `@dataclass` | `method.py`, README unihttp | Лучше: операцией может быть dataclass, Pydantic или msgspec Struct | Измерено: `vars()` на msgspec Struct падает, поэтому Struct не может быть методом unihttp. У нас `rename="pascal"` заменяет весь рецепт KAD. |
| `RetryMiddleware`, `ErrorMapperMiddleware` по статусу | `middlewares/*` | Лучше: `Resilience`, пять политик, `errors={...}` с типизированными моделями | Ничего не берём. |
| HTML-метод = `HtmlMethod.parse(selector)` руками | `kad_client/methods/base.py` | Лучше: `Html(Model)` + `CSS/XPath/Scope` на модели, compile-time проверка бэкенда | Ничего не берём. |
| curl_cffi backend через свой класс клиента | `kad_client/clients/curl_cffi.py` | Есть `Client.curl_cffi()` через Zapros handler | Ничего не берём. |

Вывод: из unihttp нужно взять три вещи. Запрос как значение с маркерами размещения. Биндинг
класса в метод с сохранением сигнатуры конструктора. Короткую запись маркеров `Query[int]`.
Всё остальное у нас шире и декларативнее.

---

## 3. Почему это не «второй путь исполнения» и не откат решений фаз 17/42

`experiments/api_authoring_alternatives/README.md` отверг `descriptor_with_input.py` («ceremonial
wrapper object for every flat operation … no direct keyword completion»). Отвергнута была именно
**потеря keyword-completion у вызывающего**: там дескриптор принимал `input=GetPostRequest` и
вызов выглядел как `posts.get_post(GetPostRequest(post_id=1))`.

`bind_method` в unihttp закрывает ровно это: `MethodBinder[P, T]` типизирован как
`Callable[P, BaseMethod[T]]`, где `P` — синтезированная `dataclass_transform` сигнатура
`__init__`. Pyright и mypy показывают `search_instances(page: int = 1, count: int = 25, courts:
list[str] = ..., ...)` без единого объекта в вызове. Причина отказа снята, а оба преимущества
(значение + плоский вызов) совмещаются.

Правило `AGENTS.md` «no second execution path» соблюдается, если:

1. существует **один** компилируемый тип — `HttpOperationDeclaration` (уже есть,
   `eazy-sdk-websocket-architecture.md`, «Common metadata»);
2. декоратор `@api.get` не имеет собственной ветки в компиляторе: он строит класс операции из
   сигнатуры при создании роутера и дальше идёт тем же путём, что `op(GetOrder)`;
3. `**request: Unpack[TypedDict]` **удаляется** как форма ввода (её единственная задача —
   «переиспользуемая схема» — полностью покрывается классом операции, а defaults она не умеет).
   Это breaking, но 0.3.0 и так breaking, а alpha не держит алиасов.

Правило `private_sdk_endpoint_architecture.md` §5 «маленький плоский запрос → аргументы функции;
сложная структура → модель» остаётся в силе и получает честную реализацию: обе формы дают
операцию-класс, различие только в том, кто его написал.

---

## 4. Целевой authoring API

### 4.1. Операция как класс

```python
from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import AsyncApi, Http, HttpOperation, JsonField, Path, Query, op
from eazy_sdk.request import markers
from eazy_sdk.response import ApiError


class OrderNotFound(ApiError[ApiProblem]): ...
class RateLimited(ApiError[ApiProblem]): ...


@dataclass(frozen=True, slots=True)
class GetOrder(HttpOperation[Order]):          # generic = success по умолчанию, {200: Order}
    __http__ = Http.get(
        "/orders/{order_id}",
        errors={404: OrderNotFound, 429: RateLimited},
    )

    order_id: Path[str]                                             # короткая форма, §4.4
    expand: Query[tuple[str, ...]] = ()
    locale: Annotated[str, markers.Header("Accept-Language")] = "en"


class OrdersApi(AsyncApi):
    get_order = op(GetOrder)
```

Вызов:

```python
order = await sdk.orders.get_order(order_id="42", expand=("items",))
envelope = await sdk.orders.get_order.with_response(order_id="42")
prepared = sdk.orders.get_order.prepare(order_id="42")     # offline, как сегодня
request = sdk.orders.get_order.request(order_id="42")      # NEW: значение GetOrder
same = await sdk.orders.get_order.send(request)            # NEW: отправить значение
```

**Метаданные операции живут в атрибуте `__http__`, а не в class keywords.** Это измерено, а не
выбрано по вкусу. Class keywords работают на dataclass и на Pydantic, но msgspec отвергает их
раньше, чем до них доберётся `__init_subclass__`:

```
class S(Meta, msgspec.Struct, method="GET"):
    -> TypeError StructMeta.__new__() got an unexpected keyword argument 'method'
```

Атрибут же переживает все три библиотеки, включая `@dataclass(slots=True)`, который пересоздаёт
класс. Отдельная ловушка на будущее: пересоздание из-за `slots=True` вызывает
`__init_subclass__` второй раз и уже без keywords, поэтому реализация, которая пишет
`cls.__method__ = method` безусловно, молча затирает метаданные. С атрибутом этой ловушки нет
вовсе.

`Http.get`, `Http.post` и остальные глаголы это одна публичная точка вместо восьми имён в корне.
`Http.request(method, path, ...)` покрывает нестандартные глаголы.

**Операцией может быть модель любой поддерживаемой библиотеки**, и это не украшение. Msgspec
превращает контракт в стиле KAD, где всё именуется в PascalCase, в одно ключевое слово:

```python
class SearchOrders(HttpOperation[SearchPage], msgspec.Struct, rename="pascal", frozen=True):
    __http__ = Http.post(
        "/orders/search",
        success={200: SearchPage, 202: SearchQueued},   # явный мэппинг перекрывает generic
        signing=(VENDOR_HMAC,),
    )

    page: JsonField[int] = 1
    count: JsonField[int] = 25
    sides: JsonField[list[SideFilter]] = msgspec.field(default_factory=list)
    with_vks: JsonField[bool] = msgspec.field(name="WithVKSInstances", default=False)
```

Что уходит на провод, проверено на нашем же реестре адаптеров:

```
{"Page": 1, "Count": 25, "Sides": [], "WithVKSInstances": false}
```

Двенадцать строк `name_mapping` в централизованном реторте, как в `kad_client/serialization.py`,
заменяются одним `rename="pascal"` плюс одним исключением на поле. Подробности про имена в §4.3.

Ключевые правила:

- Поля читает `ModelAdapterRegistry`, который уже понимает dataclass, Pydantic, msgspec и
  TypedDict, включая defaults, `default_factory` и alias'ы. Маркеры размещения читаются из
  `Annotated` так же, как сегодня из сигнатуры (`compile/input.py`).
- Содержимое `__http__` равно сегодняшним параметрам `_OperationOptions`: `operation_id`,
  `success`, `errors`, `fallback`, `inherit_errors`, `security`, `requires`, `inject`, `signing`,
  `crypto`, `protections`, `wire`, `projection`, `tags`, `idempotent`, `raw_response`.
- Поле без маркера это ошибка компиляции (принцип §3 `private_sdk_endpoint_architecture.md`),
  кроме полей, объявленных `Inject`/`FromProtection`: они не входят в публичную сигнатуру.
- `UNSET` как default означает «не отправлять», это наш аналог `Omitted` из unihttp. `None`
  сериализуется по правилам `Wire.encoding`.
- `frozen` обязателен. Не frozen-модель отвергается на компиляции с указанием класса.

### 4.2. Декоратор как короткая форма той же операции

```python
from eazy_sdk import Path, Query, SyncApi, api


class UsersApi(SyncApi):
    @api.get("/users/{user_id}", errors={404: UserNotFound})
    def get_user(self, *, user_id: Path[int], locale: Query[str] = "en") -> User:
        raise NotImplementedError
```

При создании класса `UsersApi` декоратор синтезирует
`dataclass(frozen=True, slots=True) UsersApi.get_user.Operation` из сигнатуры (return annotation →
generic, параметры → поля) и биндит его ровно так же, как `op(...)`. Побочный эффект: у
декорированной операции тоже появляются `.request(...)`/`.send(...)`, а `.prepare()` и codegen
работают с одним типом. `Unpack[TypedDict]` больше не принимается.

### 4.3. Имена на проводе: короткий маркер говорит «где», модель говорит «как называется»

Сначала измеренный факт про unihttp, потому что он объясняет, почему там маркеры такие короткие.

**В unihttp нет per-field имени вообще.** В `markers.py` у маркеров нет параметра имени: маркер
это чистый алиас `Path = Annotated[_MarkerValueT, PathMarker()]`. Единственный документированный
способ переименования это `name_mapping` в централизованном `Retort` на клиенте, что и видно в
`kad_client/serialization.py`, где 12 классов перечислены в одном рецепте. Более того, оба
остальных сериализатора unihttp alias'ы просто игнорируют: `PydanticDumper` и `MsgspecDumper`
собирают тело как `target_dict[field_name] = ...` из `vars(obj)`, то есть по имени Python-атрибута.
Проверено:

```
pydantic Field(alias='perPage') -> model_dump(by_alias=True) = {'perPage': 25}
                                -> vars(obj)                 = {'per_page': 25}   <- берётся это
msgspec Struct                  -> vars(obj) TypeError: vars() argument must have __dict__
```

Последняя строка означает, что msgspec Struct в unihttp вообще не может быть методом. Во всех
примерах его README метод это `@dataclass`, а Pydantic и msgspec используются только для
вложенных payload'ов.

То есть короткие маркеры в unihttp коротки ровно потому, что именование вынесено на клиент.
Нам не нужно платить эту цену: инвариант 5 уже говорит, что имена на проводе принадлежат модели.
Отсюда правило:

> Маркер размещения говорит, **куда** попадает значение. Имя на проводе говорит модель.

Три уровня, от самого общего к самому точному.

**Уровень 1, вся операция сразу.** Одно ключевое слово вместо рецепта на весь клиент:

```python
class SearchInstances(HttpOperation[SearchResult], msgspec.Struct, rename="pascal", frozen=True):
    page: JsonField[int] = 1
    count: JsonField[int] = 25              # -> {"Page": 1, "Count": 25}
```

У Pydantic то же самое делает `alias_generator`, у msgspec доступны `camel`, `pascal`, `kebab`,
`upper` и собственная функция.

**Уровень 2, одно поле-исключение.** Механизмом самой модели, не маркером:

```python
    with_vks: JsonField[bool] = msgspec.field(name="WithVKSInstances", default=False)
    per_page: Query[int] = Field(25, alias="perPage")        # Pydantic
```

**Уровень 3, dataclass и опции размещения.** У dataclass нет понятия alias, поэтому имя берёт
маркер. Он же нужен, когда заданы `style`, `explode`, `allow_reserved` или `codec`, которые к
имени отношения не имеют:

```python
    case_id: Annotated[str, markers.Query("caseId")]
    tags: Annotated[list[str], markers.Query(explode=False)]
```

Правило разрешения конфликта: если имя задано и моделью, и маркером, это ошибка компиляции с
обоими источниками, а не молчаливый приоритет. Инвариант 5 говорит, что размещение не
переопределяет alias'ы модели; две декларации одного имени это не приоритет, а опечатка.

**Что придётся починить.** Наш собственный адаптер Pydantic читает alias только при
`serialize_by_alias=True` в конфиге модели. Измерено на текущем коде:

```
Field(25, alias='perPage')                          -> ('per_page', 'per_page')
Field(25, serialization_alias='perPage')            -> ('per_page', 'per_page')
model_config = ConfigDict(serialize_by_alias=True)  -> ('per_page', 'perPage')
alias_generator + serialize_by_alias                -> ('per_page', 'perPage')
```

Поведение согласовано с самим Pydantic, у которого `model_dump()` по умолчанию тоже отдаёт имена
полей. Но для операции имя на проводе это весь смысл, поэтому alias, который не попадёт на
провод, должен становиться диагностикой при компиляции, а не тихо игнорироваться. Адаптер msgspec
уже читает и `rename`, и `field(name=...)` правильно, это тоже измерено.

### 4.4. Короткая форма маркера: `Query[int]` вместо `Annotated[int, Query()]`

`unihttp/markers.py` объявляет маркеры обобщёнными алиасами над `Annotated`:

```python
_MarkerValueT = TypeVar("_MarkerValueT")
Path = Annotated[_MarkerValueT, PathMarker()]
Query = Annotated[_MarkerValueT, QueryMarker()]
```

и поле пишется как `page: Query[int] = 1`. Это вдвое короче нашей сегодняшней формы, и на классе
из десяти полей разница видна сразу.

**Механизм ровно один, и он измерен.** Альтернатива с `__class_getitem__` на дата-классе
работает в рантайме, но отвергается обоими проверяющими:

```
mypy   : "QueryCls" expects no type arguments, but 1 given
         Argument 1 to "takes_int" has incompatible type "QueryCls"; expected "int"
pyright: Argument of type "QueryCls" cannot be assigned to parameter "x" of type "int"
```

Обобщённый алиас проходит оба без единой ошибки, а `get_type_hints(..., include_extras=True)`
в рантайме отдаёт `Annotated[int, QueryDesc(name=None)]`, то есть ровно то, что читает
`compile/input.py`.

**Следствие: имя алиаса и имя дата-класса обязаны различаться.** Алиас параметризуется типом, а
метаданные внутри него фиксируются в момент объявления алиаса, поэтому ни имя, ни `style` в него
не передать. Частый случай забирает короткое имя, редкий уходит в квалифицированный модуль:

```python
from eazy_sdk import Path, Query, Header, Cookie, JsonField, Form, Part   # алиасы
from eazy_sdk.request import markers                                      # дата-классы
```

```python
@dataclass(frozen=True, slots=True)
class GetCaseDocumentsPage(HttpOperation[DocumentPageResponse]):
    __http__ = Http.get("/Kad/CaseDocumentsPage")

    case_id: Annotated[str, markers.Query("caseId")]     # у dataclass нет alias'ов
    page: Query[int] = 1                                 # имя совпадает
    per_page: Annotated[int, markers.Query("perPage")] = 25
    cache_buster: Query[int] = UNSET                     # не отправляется, пока не задан
    accept: Header[str] = PAGE_ACCEPT
```

Та же операция на msgspec, где имена берёт модель:

```python
class GetCaseDocumentsPage(HttpOperation[DocumentPageResponse], msgspec.Struct, rename="camel"):
    __http__ = Http.get("/Kad/CaseDocumentsPage")

    case_id: Query[str]
    page: Query[int] = 1
    per_page: Query[int] = 25
    cache_buster: Query[int] = msgspec.field(name="_", default=UNSET)
    accept: Header[str] = PAGE_ACCEPT
```

Правила:

- Короткая форма есть у всех маркеров размещения: `Path[T]`, `Query[T]`, `Header[T]`, `Cookie[T]`,
  `JsonField[T]`, `Form[T]`, `Part[T]`, `JsonBody[T]`, `FormBody[T]`, `BytesBody[T]`.
  У маркеров с обязательными аргументами, таких как `QueryString` и `BodyProjection`, её нет.
- `Query[int]` и `Annotated[int, markers.Query()]` дают одно и то же значение аннотации.
  Компилятор их не различает; документация и codegen печатают короткую форму, когда нет ни имени,
  ни опций.
- Короткая форма работает и в сигнатуре декоратора: `def get(self, *, user_id: Path[int]) -> User`.
- Модуль `eazy_sdk.request.markers` переэкспортирует дата-классы из `params.py` и
  `descriptors.py` одним пространством имён. Плоских публичных имён не добавляется, а
  `Inject(...)` начинает принимать `markers.Header("X-Device-ID")`.
- Альтернатива, отвергнутая по бюджету имён: плоские `QueryNamed`, `HeaderNamed` и так далее.
  Это семь новых имён в корне при метрике, уже превышенной вдвое.
- Соответствие именам unihttp: `Body` это `JsonField` для поля документа или `JsonBody` для
  документа целиком, `File` это `Part`, `Raw` это `BytesBody`. Отдельного `Body` не вводим:
  размещение тела задаётся кодеком, а `Wire.body` отвергнут в фазе 48 по той же причине.
- Нужна typing-фикстура в `tests/typing/`, закрепляющая обе формы на mypy и pyright.

### 4.5. Представление модели: плоский вход для пользователя, сырой wire-документ для сервера

Типичный приватный API принимает не то, что вводит пользователь. Пример реального контракта
(`POST /api/v2/order/create`):

```json
{
  "data":  {"symbol": "BTCUSDT", "qty": "0.01", "side": "BUY"},
  "meta":  {"device_id": "a1f3…", "platform": "android", "app_version": "5.1.0",
            "ts": 1725600000123, "nonce": "9f0c…"},
  "token": "<session token>",
  "sign":  "<hmac-sha256 over canonical json of data+meta+token>"
}
```

Пользователь SDK должен писать `await sdk.orders.create(symbol="BTCUSDT", qty="0.01")`. Всё
остальное — константы, фабрики, зависимости рантайма, сессия и подпись — имеет **разных
владельцев**, и каждое значение объявляется у своего владельца ровно один раз:

| Значение | Владелец | Декларация | Когда вычисляется |
|---|---|---|---|
| `symbol`, `qty`, `side` | вызывающий | поля операции-класса | при вызове |
| `platform`, `app_version` | константы SDK | defaults `WireSettings` | один раз |
| `ts`, `nonce` | фабрики «на попытку» | `default_factory` в `WireSettings`, вызываются проекцией | каждая попытка (`PROJECTION` stage выполняется на каждой попытке) |
| `device_id` | зависимость рантайма, устойчивый идентификатор установки | `Inject(markers.JsonField("device_id"), DeviceIdProvider)` | по `DependencyCachePolicy` |
| `token` | сессия (`Identity`/auth) | session writer в `security=`; в аргументы не попадает никогда | после auth-resolve, до подписи |
| `sign` | производное от финальных байт | `signing=(hmac_sha256(...))` с `body_output("sign")` | последним, `SIGN` stage |

Три слоя кода. **Слой 1 — сырой wire-документ.** Обычная модель без маркеров размещения:
это тело целиком, её alias'ы и вложенность принадлежат ей (инвариант 5):

```python
@dataclass(frozen=True, slots=True)
class OrderData:
    symbol: str
    qty: str
    side: Literal["BUY", "SELL"]


@dataclass(frozen=True, slots=True)
class OrderMeta:
    device_id: str
    platform: str
    app_version: str
    ts: int
    nonce: str


@dataclass(frozen=True, slots=True)
class CreateOrderWire:
    data: OrderData
    meta: OrderMeta
    token: str | None = None      # заполняет session writer
    sign: str | None = None       # заполняет signing; в проекции всегда None
```

**Слой 2 — константы и фабрики.** Один frozen-объект настроек, тестируемый без сети:

```python
@dataclass(frozen=True, slots=True)
class OrderWireSettings:
    platform: str = "android"
    app_version: str = "5.1.0"
    timestamp_ms: Callable[[], int] = lambda: int(time.time() * 1000)
    nonce: Callable[[], str] = lambda: secrets.token_hex(16)


@dataclass(frozen=True, slots=True)
class CreateOrderProjection:
    settings: OrderWireSettings = field(default_factory=OrderWireSettings)

    def __call__(self, public: CreateOrder, injected: Injected) -> CreateOrderWire:
        return CreateOrderWire(
            data=OrderData(public.symbol, public.qty, public.side),
            meta=OrderMeta(
                device_id=injected[DEVICE_ID],
                platform=self.settings.platform,
                app_version=self.settings.app_version,
                ts=self.settings.timestamp_ms(),
                nonce=self.settings.nonce(),
            ),
        )
```

**Слой 3 — публичная операция.** Поля — только то, что вводит пользователь:

```python
DEVICE_ID = dependency(DeviceIdProvider, cache=DependencyCachePolicy.SESSION)
ORDER_SIGN = hmac_sha256(
    key=SigningKeyRequirement("orders-hmac"),
    base=canonical_json(include=("/data", "/meta", "/token"), sort_keys=True),
    output=body_output("sign"),
)


@dataclass(frozen=True, slots=True)
class CreateOrder(HttpOperation[OrderCreated]):
    __http__ = Http.post(
        "/api/v2/order/create",
        errors={400: OrderRejected, 401: SessionExpired},
        projection=BodyProjection(
            target=CreateOrderWire, using=CreateOrderProjection(), encoding=JsonBody()
        ),
        requires=(DEVICE_ID,),
        security=SESSION_TOKEN_IN_BODY,        # session writer пишет в /token
        signing=(ORDER_SIGN,),
    )

    symbol: JsonField[str]
    qty: JsonField[str]
    side: JsonField[Literal["BUY", "SELL"]] = "BUY"


class OrdersApi(AsyncApi):
    create = op(CreateOrder)
```

Вызов и то, что видит IDE:

```python
order = await sdk.orders.create(symbol="BTCUSDT", qty="0.01")       # side="BUY" по умолчанию
prepared = sdk.orders.create.prepare(symbol="BTCUSDT", qty="0.01")   # offline: token/sign отредактированы
```

Что здесь ново относительно сегодняшнего `examples/flat_model_wire_body.py`, а что уже есть:

- **Есть**: `BodyProjection(source, target, using, encoding)`, `Inject`, `dependency()`,
  `hmac_sha256` + `body_output`, session writers, pipeline `PROJECTION → … → SIGN`, редакция
  секретов в `prepare()`. Пример выше отличается от текущего только формой объявления.
- **Ново**: `source` у `BodyProjection` по умолчанию — сама операция-класс (в декораторе —
  синтезированный класс), поэтому параметр опускается; `projection=` становится class keyword и
  параметром декоратора вместо `wire=Wire(projection=...)` (проекция — не байтовый контракт,
  а представление, ей место рядом с `success`/`errors`, а не в `Wire`).
- **Ново**: проекция получает второй аргумент `Injected` — отображение
  `RequestDependency → значение` из `requires=`. Сегодня `Inject` умеет класть значение только в
  верхний уровень тела (`JsonField("device_id")`); вложенный `meta.device_id` без этого требует
  либо JSON-pointer в `Inject`, либо доступа к зависимости из проекции. Второе честнее:
  проекция и так единственное место, знающее форму wire-документа.
- **Не меняется**: `token` и `sign` никогда не являются полями операции и не проходят через
  проекцию как значения. Проекция оставляет `None`, session writer и подпись заполняют слоты
  по своим правилам; `prepare()` показывает их отредактированными.

Короткий вариант без проекции. Когда wire-документ плоский, а «сырых» полей два-три, слои 1 и
2 не нужны — константы и фабрики объявляются прямо на операции полями с `init=False`:

```python
@dataclass(frozen=True, slots=True)
class Ping(HttpOperation[Pong]):
    __http__ = Http.post("/ping", requires=(DEVICE_ID,),
                         inject=(Inject(markers.JsonField("device_id"), DEVICE_ID),))

    message: JsonField[str]
    platform: JsonField[str] = field(default="android", init=False)                 # константа
    ts: JsonField[int] = field(default_factory=unix_ms, init=False)                 # один раз, при создании
```

Поля с `init=False` не попадают в сигнатуру `op(Ping)`, но участвуют в сериализации и в
`evolve()`. **Фабрика вызывается один раз, при создании значения.** Значение запроса истинно:
`prepare()` отдаёт ровно те байты, которые в нём лежат, иначе «запрос это значение» не выполняется.
Свежий timestamp или nonce на каждую попытку это уже существующий `Inject` с
`DependencyCachePolicy.ATTEMPT`, а не фабрика поля. Правило выбора: **вложенность или больше
трёх сырых полей это проекция; значение, которое обязано меняться между попытками, это
`Inject`; всё остальное это `init=False`**.

Для ответа ничего из этого не требуется: `success={200: OrderCreated}` читает публичную модель, а
если сервер отвечает тем же конвертом, его снимает сервисный атрибут `unwrap = "data"`, см. §5.

### 4.6. Биндинг

`op(OperationType, *, name=None)` возвращает `_OperationDescriptorBase[TApi, P, T]`, где
`P` берётся из `Callable[P, HttpOperation[T]]` (конструктор класса), `T` — из generic-аргумента.
Overloads по типу владельца (`SyncApi` → `Callable[P, T]`, `AsyncApi` → `Callable[P, Awaitable[T]]`)
уже есть в `api.py:728-858`; добавляется только вход `type[Operation]`. Никакой интроспекции в
рантайме на вызов: `__init__` модели вызывается напрямую, компиляция выполняется один раз
при первом доступе, как сейчас.

`api_group`, `SERVICE_ATTRIBUTES`, `bind()` и `Identity` не меняются.

Одна деталь, которую нельзя пропустить: **обновление значения запроса не единообразно** между
библиотеками моделей. Измерено:

```
dataclass       -> dataclasses.replace(request, page=3)
pydantic        -> dataclasses.replace() TypeError; нужен request.model_copy(update={"page": 3})
msgspec Struct  -> msgspec.structs.replace(request, page=3)
```

Поэтому `.send(replace(request, page=3))` из примеров выше работает только для dataclass.
`ModelAdapterRegistry` должен получить метод `evolve(value, **changes)`, диспетчеризующий на
нужную реализацию, а дескриптор операции выставить его как `request.evolve(page=3)` или
`sdk.orders.search.evolve(request, page=3)`. Без этого запрос-значение теряет главное своё
применение, переиспользование с изменением одного поля.

---

## 5. Ответы: мэппинг статусов, а семья экстракторов выводится из модели

Принимаются как спецификация выводы 1, 2, 3, 5, 6, 8 README экспериментов. Вывод 9 про
`Decode(extract=...)` и вывод 10 про `Decode(errors=...)` **отклоняются**, вместе с самим типом
`Decode`. Причина в §5.2.

### 5.1. Форма объявления

1. `success={status: Model | Sequence[Representation]}`,
   `errors={status | "4xx" | "5xx": ExceptionClass | Model | (Model, factory)}`,
   `fallback=ExceptionClass`. Ключ `DEFAULT` внутри `errors` это ошибка компиляции с подсказкой
   про `fallback=`.
2. `ApiError[Model]` берёт модель из обобщённого аргумента. Он не обязателен: голая модель
   ловится как `ApiError`, а пара `(Model, factory)` принимает любую фабрику, возвращающую любое
   исключение, потому что `ApiErrorFactory` это уже
   `type[ApiError[Any]] | Callable[[T, ResponseContext], Exception]`. Наследование от библиотеки
   нужно ровно тогда, когда нужен отдельный ловимый тип и не хочется писать фабрику.
3. `Responses.inspect` ранжирует кандидатов: точный статус выше `StatusRange`, `StatusRange`
   выше `DEFAULT`. Условие `when=` и медиа разбивают ничью. `AmbiguousResponseOutcome` остаётся
   только для настоящей неоднозначности.
4. Проекция ответа `project(WireModel, to=Public, using=...)` это полноценный шаг `inspect()`,
   а не обёртка-парсер. Иначе compile-time проверка бэкенда не выполняется, что измерено в
   выводе 8.
5. `Json`, `Html`, `Success`, `Error` и кортежная форма остаются escape hatch и смешиваются
   с мэппингом в одном объявлении.
6. Конверт ответа снимается сервисным атрибутом `unwrap = "data"`. Только декларативный путь,
   не вызываемый объект, иначе генератор OpenAPI и AsyncAPI не может его показать.

### 5.2. Почему `Decode` не нужен: семья читается из метаданных модели

`Decode(extract=HTML, errors=JSON)` ключевал медиа по исходу, но медиа по исходу не варьируется.
Она варьируется по тому, какой документ прислал сервер, и по тому, какая модель его читает. Исход
это подменная переменная: она совпадает с реальностью на «сайт отдаёт HTML, ошибки JSON» и
ломается на первом же сервисе, где рейт-лимитер отвечает JSON, а WAF отдаёт HTML.

Настоящее правило проще и не требует деклараций вообще:

> Модель, несущая `CSS`, `XPath` или `Scope`, читается документным бэкендом. Модель без них
> читается структурным.

Это не эвристика. Ровно это уже вычисляет compile-time проверка в `executor.py:2096-2101`,
которая компилирует модель под экстрактор и отвергает несовпадение с указанием поля
(`ApiProblem.code requires CSS or XPath metadata`). Вывод и проверка становятся одним кодом,
поэтому неверный вывод невозможен, а не просто маловероятен.

Проверка на всех случаях `j_html_service.py`:

| Кейс | Модель | Семья | Что требовалось раньше |
|---|---|---|---|
| `{200: CatalogPage}` | селекторы CSS | документная | `Decode(extract=HTML)` |
| `{404: PageNotFound}` | селекторы CSS | документная | наследование `extract` |
| `{429: RateLimited}` над `ApiProblem` | селекторов нет | структурная | `Decode(errors=JSON)` |
| `{502: GatewayDown}` над `GatewayPage` на JSON-сервисе | селекторы CSS | документная | `response=Html(...)` на классе |

Три уровня локальности схлопываются в ноль деклараций.

### 5.3. Что тогда выбирает контент-тип

Выбор по контент-типу остаётся, но живёт в арбитраже кейсов, где различие истинно, а не
в правиле, размазанном по сервису. Два места.

Первое. Автор объявил на один статус несколько кейсов с разными медиа. Контент-тип ответа плюс
ранжирование по специфичности решают, какой из них разбирать. Это случай карточки дела в KAD:
один URL отдаёт HTML на навигацию и JSON на XHR-запрос.

```python
@dataclass(frozen=True, slots=True)
class GetCaseCard(HttpOperation[CaseCardPage | CaseCardEnvelope]):
    # селекторы слева, чистая модель справа; выбирает контент-тип ответа
    __http__ = Http.get("/Card/{case_id}", success={200: (CaseCardPage, CaseCardEnvelope)})

    case_id: Path[str]
    requested_with: Header[str] = UNSET
```

Второе. Внутри документной семьи бэкендов может быть больше одного, HTML и XML. Здесь контент-тип
выбирает бэкенд, см. §9.

Важная граница: контент-тип выбирает только среди кейсов, которые автор объявил для этого
статуса. Он никогда не выбирает модель сам по себе, иначе ответ прокси начинает управлять тем,
во что вы разбираете тело.

### 5.4. Инфраструктурные ответы принадлежат сервису, а не операции

Блок-страница WAF, страница гейтвея и ответ рейт-лимитера не являются свойством эндпоинта.
Механизм уже есть и его достаточно: `errors` это единственный сервисный атрибут, который
накапливается вниз по цепочке вместо перезаписи.

```python
class BooksSite:
    base_url = "https://books.example"
    errors = {429: RateLimited, 503: AccessBlocked}


@dataclass(frozen=True, slots=True)
class GetCatalogPage(HttpOperation[CatalogPage]):
    __http__ = Http.get("/catalogue/page-{page}.html", errors={404: PageNotFound})

    page: Path[int] = 1


class BooksApi(BooksSite, SyncApi):
    catalog_page = op(GetCatalogPage)
```

Ни `Decode`, ни `Json(...)`, ни `Html(...)` в этом файле нет. Статусы 200, 404 и 503 читает
документный бэкенд, статус 429 читает структурный. Решило наличие селекторов в моделях.

Challenge-страницы Cloudflare и Turnstile сюда не относятся вовсе: они уже `protections` с
политикой повтора, а ошибкой становится только терминальный блок.

Открытым остаётся один случай: один WAF перед несколькими сервисами на общем домене. Тогда
объявление нужно на уровне клиента. Прецедент есть, это host-scoped `ClientConfig.crypto`,
записанный в фазе 48 как осознанное исключение с обоснованием «пропущенное правило падает на
первом же вызове». Пропущенный инфраструктурный кейс даёт `UnexpectedResponseError` вместо
типизированного исключения, то есть падает так же громко. Решение в §12.

### 5.5. Дефект, который надо починить в 50.2

`ApiError` не переживает `pickle`. Воспроизведено на текущем коде:

```
created: ApiError('documented API error for getOrder') | args = ('documented API error for getOrder',)
pickle FAILS: TypeError ApiError.__init__() missing 1 required positional argument: 'context'
```

`Exception.__reduce__` восстанавливает объект как `cls(*self.args)`, а `args` содержит только
строку сообщения, потому что `__init__` передаёт наверх текст, а `error` и `context` кладёт в
атрибуты. Ломается любая передача исключения между процессами: multiprocessing, Celery,
`concurrent.futures`. Лечится собственным `__reduce__`, возвращающим `(cls, (self.error,
self.context))`, при условии что контекст сериализуем. Если контекст решено не сериализовать,
нужно явное решение о том, что именно переживает передачу.

Класс при этом остаётся простым: своего `__init_subclass__` у него нет, и добавлять его не надо.
Ключевое слово `response=`, которое я предлагал раньше, отменяется вместе с `Decode`, потому что
семья выводится. Простой класс свободно смешивается с иерархией приложения через множественное
наследование.

---

## 6. Ошибки «200 OK с телом-заглушкой» и другие императивные хуки unihttp

`kad_client` решает межстраничные ответы (`is_challenge`, `is_blocked`, `is_pdf`) в
`validate_response`/`make_response`. В нашей модели это три разных декларации, каждая уже есть:

| Ситуация в KAD | Где это у нас |
|---|---|
| 200 с HTML-страницей challenge вместо данных | `ProtectionPolicy` + `protections=(WasmChallenge(),)`; повтор попытки с обновлёнными cookies делает state machine попыток, не middleware с `while True` |
| 200 с блок-страницей (`451`-семантика) | `Error("2xx", Html(BlockPage), exception=AccessBlocked, when=looks_like_block)` в сервисных `errors` |
| Тело должно быть PDF, иначе ошибка | `success={200: Bytes(media_type="application/pdf")}` + `fallback=UnexpectedBody`; media check делает `inspect()` |
| Bootstrap-запрос перед первым вызовом | `requires=(BootstrapSession,)` через `dependencies` DAG, не переопределение `make_request` |

Вывод: `validate_response`/`on_error`/`make_response` **не** переносятся. Единственное, что стоит
добавить: условие `when=` должно уметь читать первые N байт тела без полного декодирования
(KAD сканирует 1 МБ, чтобы не lowercase-ить PDF) — это деталь `ResponseContext`, не API.

---

## 7. Пагинация: отложена, но запрос-значение делает её дешёвой

Решение от 2026-09-06: в фазу 50 пагинация не входит. Раздел сохранён как эскиз, потому что он
объясняет, зачем запрос вообще должен быть значением.

Сегодня в репозитории нет ни одного упоминания пагинации. Пример unihttp, методы `search_pages`
и `case_documents`, показывает суть: следующая страница это `replace(request, page=page + 1)`,
а условие остановки читается из ответа. Когда операция станет значением, декларация ляжет
поверх §4 без нового механизма:

```python
class SearchOrders(HttpOperation[SearchPage], msgspec.Struct, rename="pascal"):
    __http__ = Http.post(
        "/orders/search",
        pages=Pages(next=lambda req, res: replace(req, page=req.page + 1),
                    done=lambda req, res: len(res.items) < req.count),
    )

    page: JsonField[int] = 1
    count: JsonField[int] = 25
```

```python
async for page in sdk.orders.search.pages(count=25, max_pages=10): ...
```

Метод `.pages(...)` появлялся бы на дескрипторе только у операций с этим ключом. Дедупликация,
как `seen` в KAD, остаётся заботой вызывающего. Cursor-пагинация выражается той же парой
`next` и `done`.

---

## 8. Протоколы: один принцип объявления, разные базовые классы

Совпадает с уже записанным в `eazy-sdk-websocket-architecture.md` («Compiler registry выбирает
compiler по nominal declaration type»). Операция-класс делает это буквально:

```python
class HttpOperation[T]: ...                     # __http__ = Http.get(...): path, success, errors, signing, crypto, wire, projection
class RpcOperation[T]: ...                      # discriminator, success=rpc_result(...), errors={code: ...}; envelope берётся из router.protocol
class WsCall[T]: ...                            # event, replies=Replies(...), replay=
class WsSubscribe[T]: ...                       # event, messages=Messages(...), recovery=
class WsSend: ...                               # event
```

Поля любой из них — тот же набор моделей и маркеров (для WS маркеры размещения не нужны: вся
модель — payload, что сегодня и делает `JsonPayload(Model)`). `op(...)` выбирает дескриптор по
номинальному типу; роутер `AsyncWsApi` принимает только WS-операции, `SyncApi` — только HTTP/RPC.
Ошибка «WS-операция на sync-роутере» — при создании класса, с источником.

GraphQL-over-HTTP остаётся non-goal до появления `PartialOutcome` (решение фазы 49); когда он
появится, это будет `GraphqlOperation[T]` с `query=`/`variables` из полей — без изменения §4.

---

## 9. Сериализация, бэкенды и выбор реализации

### 9.1. Имена на модели, adaptix как адаптер

- Инвариант 5, размещение не переопределяет alias'ы модели, сохраняется и распространяется на
  операции-классы. `markers.JsonField("Page")` это alias поля операции, он не спорит с alias'ами
  вложенной модели.
- Глобальную политику именования не изобретаем. У Pydantic есть `alias_generator`, у msgspec
  `rename`, для dataclass остаётся `Wire(encoding=JsonPolicy(naming=...))` как единственная точка.
- Для тех, кому нужен центральный `Retort` в стиле unihttp, добавляется плагин `eazy-sdk-adaptix`
  с `AdaptixModelAdapter(retort)`. Это реализация `ModelAdapter`, то есть решение фазы 44 «нет
  отдельных RequestDumper и ResponseLoader» не нарушается.

### 9.2. Кто чем сериализует: два слоя, а не один

Query, form, JSON и multipart сериализуются **по-разному, и иначе быть не может**: у них разные
целевые форматы. В query нет типов, там всё становится строкой и потом percent-кодируется.
В JSON типы есть. Form это пары строк. Multipart это части со своими заголовками. Библиотека
модели не умеет и не должна уметь ни одного из этих кодирований.

Поэтому слоёв два, и это правило 19 из `private_sdk_endpoint_architecture.md`, «ModelAdapter и
WireCodec это разные уровни абстракции». Реализовано так:

| Слой | Кто | Что делает | От чего зависит |
|---|---|---|---|
| 1 | `ModelAdapter` | модель в словарь `{имя_на_проводе: значение}`, включая вложенность | от библиотеки модели |
| 2 | `ModelAdapterRegistry._normalize_dump` | скаляры в JSON-безопасные примитивы | ни от чего, единообразно |
| 3 | кодек места | примитивы в байты или строки конкретного места | от места, не от модели |

Третий слой у каждого места свой: `ScalarCodec` плюс `style`/`explode` и `QueryCodec` для query,
`ScalarCodec` со `style="simple"` для заголовков и cookie, `JsonBackend.dumps` с `JsonPolicy` для
JSON-тела, urlencode для form, части со своими заголовками для multipart, `BytesBody` для сырого
тела.

**Ответ на «если человек выбрал msgspec, всё сериализуется им»: нет, и не может.** Msgspec не
умеет собрать query string, а `JsonPolicy` с его `separators`, `ensure_ascii` и `sort_keys`
существует потому, что канонические байты читает подпись. Если бы тело собирал
`msgspec.json.encode`, политика была бы обойдена и подпись считалась бы не над теми байтами,
которые уходят. Библиотека модели даёт структуру и имена, кодирование остаётся за местом.

**Выбор адаптера идёт по модели, а не по клиенту.** Одно значение может смешивать три
библиотеки, и каждая вложенная модель обслуживается своим адаптером со своими именами.
Проверено на реестре по умолчанию:

```python
@dataclass(frozen=True, slots=True)
class SearchBody:
    sides: list[Side]        # msgspec Struct, rename="pascal"
    filter: Filter           # pydantic, alias "DateFrom"
    total: Decimal
```

```
{'sides': [{'Name': 'ООО Ромашка', 'ExactMatch': True}],
 'filter': {'DateFrom': '2026-01-01'},
 'total': '10.50'}
```

Скаляры при этом нормализует реестр, а не библиотека модели: `date` в ISO-строку, `Decimal` в
строку, `Enum` в значение. Проверено, что результат одинаков для всех трёх библиотек. Это важно
для подписи: представление скаляра на проводе не должно зависеть от того, чем автор описал
модель.

**Конфликты адаптеров громкие, а не молчаливые.** `_select` собирает все адаптеры, поддержавшие
тип, и падает, если их больше одного. Измерено на подсунутом адаптере, объявившем поддержку
dataclass:

```
AmbiguousModelAdapterError: multiple model adapters support SearchBody: adaptix, dataclass
```

Порядок регистрации на это не влияет: `first=True` меняет порядок в кортеже, но не разрешает
неоднозначность. Перехватить чужой тип молча нельзя.

Отсюда требование к плагину adaptix из §9.1: его `supports_type` должен признавать **только те
типы, которые явно зарегистрированы в его `Retort`**, иначе он объявит поддержку всех dataclass
и столкнётся со штатным адаптером. Второй поддерживаемый путь это `replace_adapter`, но он
требует сохранить имя заменяемого адаптера, то есть adaptix пришлось бы назвать `dataclass`, что
хуже. Рекомендуется первый путь.

### 9.3. Сменные бэкенды: orjson, selectolax, bs4

Точки подключения уже существуют и объявляются один раз на корне SDK:

```python
Serialization(
    models=default_model_adapters(),   # dataclass, pydantic, msgspec, TypedDict
    json=OrjsonBackend(),              # реализация JsonBackend
    html=SelectolaxBackend(),          # реализация DocumentBackend
)
```

`JsonBackend` это протокол из четырёх членов: `name`, `supports(policy)`, `dumps` и `loads`.
Ключевой здесь `supports`. Байты, которые обязан произвести бэкенд, задаёт `JsonPolicy` операции,
и бэкенд, который не умеет требуемое, говорит об этом заранее. Для orjson это не теория: он не
поддерживает произвольные разделители и не умеет сортировать ключи так, как требует часть
канонических подписей. Такая операция отвергается на компиляции, а не подписывается над байтами,
о которых сервер не договаривался.

`DocumentBackend` это протокол из трёх членов: `name`, `selector_languages` и `parse`. Множество
языков селекторов это не предпочтение, а проверяемое свойство. selectolax и BeautifulSoup дают
`frozenset({"css"})`, parsel даёт `{"css", "xpath"}`, ElementTree из плагина XML даёт
`{"xpath"}`. Модель, выбирающая по XPath, под CSS-only бэкендом становится ошибкой компиляции с
указанием поля. То есть выбор selectolax ради скорости честно стоит вам XPath, и вы узнаёте об
этом до первого запроса, а не в проде.

Что нужно написать, чтобы подключить свой бэкенд: класс с этими членами. Базовых классов нет,
регистрации нет, наследования от библиотеки нет. Это структурные протоколы, как ciphers в
`payload_crypto_api`.

### 9.4. Что придётся доделать: несколько документных бэкендов

Сегодня `Serialization.html` это одно поле типа `DocumentBackend | None`. Пока SDK работает с
одним видом документов, этого достаточно, и вывод семьи из §5.2 полностью закрыт: семью выбирает
модель, конкретный бэкенд стоит один.

Разрыв появляется у SDK, который читает и HTML, и XML. Модель с `XPath` подходит обоим
бэкендам, и выбрать между ними может только контент-тип ответа. Предлагается заменить поле
на упорядоченный набор с выбором по медиа:

```python
Serialization(json=OrjsonBackend(), documents=(ParselBackend(), ElementTreeBackend()))
```

Правило выбора: первый бэкенд, чьё объявленное множество медиа принимает контент-тип ответа;
при единственном бэкенде поведение прежнее. Compile-time проверка селекторов выполняется против
каждого объявленного бэкенда, и модель, которую не может прочитать ни один, отвергается.

---

### 9.5. Что выбрать для модели запроса и при чём тут скорость

Вопрос звучит как «pydantic или msgspec быстрее нативного dataclass». Измерено на этом
репозитории, Python 3.13, шесть полей, лучшее из семи прогонов:

| Форма операции | Конструирование, мкс на вызов | Относительно |
|---|---|---|
| msgspec Struct | 0.202 | x1.0 |
| dataclass со `slots` | 0.901 | x4.5 |
| pydantic BaseModel | 1.232 | x6.1 |

Кратность выглядит внушительно, но это неверная система отсчёта. Вот та же величина рядом с
остальной работой:

| Что | Микросекунд |
|---|---|
| Разброс между самой быстрой и самой медленной моделью | 1.03 |
| Полная подготовка запроса `prepare()`, без сети | 2761 |
| Один сетевой roundtrip | 20000–200000 |

Разброс составляет 0.037% от подготовки запроса и 0.002% от вызова с сетью. Чтобы выбор
библиотеки моделей стоил одной секунды, нужен миллион вызовов. **Скорость не является
основанием для выбора.**

Два уточнения к таблице. Чтение полей адаптером стоит 23–35 мкс, но это compile-time, один раз
на операцию, а не на вызов. Dump значения через наш реестр стоит 8.6–10.9 мкс и почти не зависит
от библиотеки, потому что работу делает адаптер, а не сама модель.

Выбирать надо по тому, что каждая библиотека даёт, а не по скорости:

- **dataclass** не тянет зависимость, но не имеет понятия alias вообще. Имена на проводе целиком
  уезжают в `markers.Query("caseId")`. Разумный default для API, где имена совпадают.
- **msgspec Struct** даёт `rename` на всю операцию и `field(name=...)` на поле. Для контракта в
  стиле KAD это разница между одним ключевым словом и двенадцатью строками рецепта. Валидацию
  ввода не делает.
- **pydantic BaseModel** единственный проверяет тип на границе вызова. Измерено: на
  `page="не число"` dataclass и msgspec молча пропускают строку в поле `int`, pydantic
  поднимает `ValidationError`. Это аргумент за корректность, а не за скорость, и он стоит около
  одной микросекунды.

Отдельное наблюдение, не относящееся к вопросу, но найденное при замере: `prepare()` стоит
2.7 мс на вызов после прогрева. Это на три порядка больше любых различий между библиотеками
моделей и заслуживает отдельного профилирования. Замер сделан на офлайновом пути подготовки,
который включает редакцию секретов, поэтому переносить его на горячий путь реального вызова
без проверки нельзя.

---

## 10. Что удаляется, что добавляется, что не меняется

Удаляется (breaking, 0.3.0):

- `**request: Unpack[TypedDict]` как форма ввода операции (`api.py`, `compile/input.py`, docs
  `guides/requests/index`, README).
- `responses=`/`response=` пара на декораторе в пользу `success=`/`errors=`/`fallback=`
  (решение открытого вопроса README: три способа — регрессия). `Responses(...)` остаётся типом
  результата нормализации и escape hatch через `success={200: (Json(A, when=...), Json(B))}`.

Добавляется:

- Обобщённые алиасы над `Annotated` для короткой формы `Query[int]` и модуль
  `eazy_sdk.request.markers`, переэкспортирующий дата-классы (§4.3). Корневые имена `Path`,
  `Query`, `Header`, `Cookie`, `JsonField`, `Form`, `Part` меняют смысл с дата-класса на алиас,
  это идёт в `more/migration`.
- `eazy_sdk/operation.py`: `HttpOperation[T]`, `RpcOperation[T]`, значение `Http` с глаголами
  `Http.get`/`Http.post`/`Http.request`, `op()` дескриптор-фабрика; `websocket/operation.py`:
  `WsCall`, `WsSubscribe`, `WsSend`.
- На дескрипторе: `.request(**kwargs) -> Operation`, `.send(operation)`, `.pages(...)`.
- `projection=` как class keyword/параметр декоратора вместо `Wire(projection=...)`; `source`
  у `BodyProjection` по умолчанию — операция; второй аргумент проекции `Injected` (§4.4).
- Поля операции с `init=False` как константы и per-attempt фабрики (§4.5).
- `ModelAdapterRegistry.evolve(value, **changes)`, единый способ обновить значение запроса (§4.6).
- Диагностика при компиляции, когда alias Pydantic не попадёт на провод из-за отсутствия
  `serialize_by_alias` (§4.3), и когда имя задано и моделью, и маркером сразу.
- Вывод семьи экстрактора из метаданных модели, одним кодом с compile-time проверкой (§5.2).
- Сервисный атрибут `unwrap` для конверта ответа, только декларативный путь.
- Ранжирование кандидатов по специфичности и по медиа в `Responses.inspect`.
- Форма `(Model, factory)` как значение в `errors=`, чтобы исключение не требовало `ApiError`.
- `ApiError.__reduce__`, чтобы исключение переживало `pickle` (§5.5).
- `Serialization.documents` вместо одиночного `html`, с выбором бэкенда по медиа (§9.4).
- Плагин `plugins/adaptix`.

Отложено за пределы фазы 50: `Pages` и метод `.pages()` (§7).

Не вводится вовсе: тип `Decode` и его поля `extract`, `errors`, `media`. Ключевое слово
`response=` на `ApiError`. Оба отменены выводом семьи из §5.2.

Не меняется: исполнитель, state machine попыток, пять политик, `Wire`, pipeline фазы 48,
подписи, payload crypto, `Identity`, `bind()`, протоколы `JsonBackend` и `DocumentBackend`,
WS runtime, Zapros handlers, драйверы фазы 46, протокольные конверты фазы 49.

Бюджет публичных имён, метрика a3 4.5, сейчас 435 при цели не выше 300. Добавляется
`HttpOperation`, `RpcOperation`, `op`, `Pages`, `markers`, три WS-операции. Удаляется путь
`Unpack`, параметры `response` и `responses` у декоратора, а `Decode` не появляется. Чистый
прирост около шести имён. Компенсировать за счёт пакета `websocket`, где их 113, отдельной
задачей.

---

## 11. Порядок работ (одна фаза 50 из четырёх шагов, каждый со всеми gates)

1. **50.1 Операция-класс + `op()`**: короткие маркеры `Query[int]` (§4.4), метаданные в
   `__http__` (§4.1), имена на проводе от модели (§4.3), `HttpOperation`, дескриптор, компиляция
   из полей модели через `ModelAdapterRegistry`; декоратор синтезирует класс; удаление `Unpack`.
   Приёмочный тест: операция на dataclass, на Pydantic и на msgspec Struct дают байт-в-байт
   одинаковый запрос при одинаковых именах на проводе.
   Typing-фикстуры pyright/mypy на видимость сигнатуры с `default_factory` (как
   `_pycharm_typing_probe.py`). Golden-тест: `prepare()` даёт байт-в-байт тот же запрос для
   декоратора и класса.
2. **50.2 Мэппинг ответов, вывод семьи, специфичность**: перенос `_sugar.py` в библиотеку,
   вывод экстрактора из метаданных модели одним кодом с compile-time проверкой, ранжирование
   кандидатов, форма `(Model, factory)`, `ApiError.__reduce__`, сервисный `unwrap`, удаление
   `response=` и `responses=`. Тип `Decode` не переносится.
3. **50.3 `.request()`, `.send()` и `evolve()`**: запрос как переиспользуемое значение,
   единый способ его обновить поверх трёх библиотек моделей.
4. **50.4 RPC/WS-операции как классы**, плагин adaptix, docs (`guides/requests/index`,
   `responses/*`, `multi-service`, `protocols`, `websocket`), `more/migration`, codegen
   (`plugins/openapi`, `plugins/asyncapi`) генерирует классы операций вместо функций с `...`.

---

## 12. Решения и оставшиеся вопросы

Решено 2026-09-06:

1. **Синтезированный из декоратора класс публичен** как `UsersApi.get_user.Operation`. Иначе
   переиспользование значения запроса не типизируется, а codegen не может на него сослаться.
2. **`frozen` обязателен** для операций-классов. Не frozen-модель отвергается на компиляции с
   указанием класса. Иммутабельность это инвариант ядра, а молча замораживать копию значило бы
   иметь два разных значения под одним именем.
3. **Имя маркера остаётся позиционным**: `markers.Header("Accept-Language")`, а не
   `markers.Header(name=...)`. Остальные опции остаются keyword-only, как сейчас.
4. **`Decode` не вводится.** Семья экстрактора выводится из метаданных модели (§5.2), выбор по
   контент-типу живёт в арбитраже кейсов (§5.3), конверт снимается сервисным `unwrap`.
5. **Метаданные операции живут в атрибуте `__http__`, а не в class keywords.** Измерено: msgspec
   отвергает неизвестные class keywords на уровне метакласса, а `@dataclass(slots=True)`
   пересоздаёт класс и вызывает `__init_subclass__` второй раз уже без keywords. Атрибут
   переживает все три библиотеки моделей (§4.1).
6. **Имя на проводе принадлежит модели**, маркер говорит только о размещении. Уровень операции
   это `rename` у msgspec и `alias_generator` у Pydantic, уровень поля это `field(name=)` и
   `Field(alias=)`, а `markers.Query("caseId")` остаётся для dataclass и для опций размещения
   (§4.3).
7. **Пагинация откладывается** за пределы фазы 50. Она чисто дополнительна, ничего не ломает и
   ничего не блокирует (§7). Прежняя формулировка этого вопроса была неясной, поэтому он
   переписан здесь вместе с ответом.

Решено 2026-09-06, вторая серия, по итогам разбора опасных мест:

8. **Значение запроса истинно.** `default_factory` выполняется один раз при конструировании,
   `prepare()` отдаёт байты, лежащие в значении. Обновление между попытками это `Inject` с
   `DependencyCachePolicy.ATTEMPT`, для него этот механизм и существует (§4.5).
9. **`ApiError` сериализует модель ошибки и редактированную сводку, никогда не сырой контекст.**
   `__reduce__` возвращает `(cls._restore, (error, summary))`, где сводка это frozen-значение
   с `operation_id`, статусом, медиа и номером попытки, собранное той же редакцией, что уже
   делает `ErrorContext.to_log_dict`. Тело и заголовки границу процесса не пересекают.
   После восстановления `context` отдаёт сводку; полный контекст доступен только в процессе,
   где ошибка возникла (§5.5).
10. **Забытые селекторы: runtime остаётся, сообщение становится точным.** Отказ на компиляции
    невозможен без эвристики, а эвристика в диагностике хуже поздней, но честной ошибки.
    Поэтому `UnexpectedResponseError` при несовпадении «ожидали структурную медиа, пришёл
    документ» получает подсказку: у модели нет метаданных CSS или XPath, и если ответ является
    документом, их надо добавить. Второй рубеж уже есть, это офлайновый `parse_html(html, Model)`
    во встроенных тестовых страницах, которые требует стиль примеров документации (§5.2).
11. **Документная модель без единого обязательного поля отвергается на компиляции**, если у
    кейса нет `when=`. Такая модель совпадает с любым документом, включая страницу WAF, и
    возвращает объект из `None` как успех. Обязательным считается поле, чьё отсутствие даёт
    `ExtractionError`; пустой список под `Scope` обязательным не является (§5.3).
12. **`Omittable[T]` для опциональных полей.** Экспортируется из корня как `T | Unset`, поле
    пишется `page: Query[Omittable[int]] = UNSET`. Компилятор трактует `Unset` в объединении
    как «не отправлять, пока не задано» и вырезает его из схемы OpenAPI. Типизировать `UNSET`
    как `Any` запрещено: это спрятало бы реальные ошибки (§4.1).
13. **Указатели подписи проверяются против скомпилированных имён на проводе.** Каждый указатель в
    `canonical_json(include=)` и `body_output(json_pointer=)` обязан разрешаться в схеме тела
    после того, как адаптер прочитал `wire_name` полей. Неизвестный указатель это `PlanError`
    с самим указателем и списком доступных имён. Конвенция одна: указатели пишутся в именах
    провода, потому что подпись читает байты, а не Python-атрибуты (§4.3).
14. **Сниффинга тела нет.** Если сервер отдаёт документ с неверным content-type, единственный
    обход это явный `media_type` на кейсе: `Html(Page, media_type="text/plain")`. Выбор
    документного бэкенда берёт медиа кейса, если она задана, иначе медиа ответа (§9.4).
15. **Неверный порядок баз для Pydantic отвергается на компиляции** с сообщением, называющим
    правильный: `class X(BaseModel, HttpOperation[Order])`. Предупреждение Pydantic при создании
    класса теряется в выводе, а ошибка компиляции детерминирована (§4.1).
16. **`operation_id` по умолчанию равен `__qualname__` класса операции**, переопределяется
    через `Http.get(..., operation_id=...)`. Одно значение `__http__` можно разделить между
    классами, потому что id выводится из класса, а не из значения. Два класса с одним явным id
    это ошибка компиляции, называющая оба (§4.1).
17. **Исключения с иерархией приложения: `ApiError` первым в базах, база приложения без своего
    `__init__`.** Кооперативные конструкторы у исключений это известная трясина, и библиотека в
    неё не лезет. Если база приложения определяет конструктор, используется форма
    `(Model, factory)` в `errors=`, для этого она и добавлена (§5.1).
18. **Codegen переезжает в 50.1 и становится его приёмочным тестом.** Генератор OpenAPI выдаёт
    форму класса с первого шага: dataclass со `slots`, имена на проводе через `markers.`,
    потому что в сгенерированном коде явность важнее краткости, а зависимость от msgspec
    навязывать нельзя. Окна с двумя формами не возникает (§11).
19. **Инфраструктурные ошибки на уровне клиента: `ClientConfig.errors`**, host-scoped мэппинг
    той же формы, что сервисный `errors`. Сливается в цепочку самым внешним слоем, операция
    переопределяет. Обоснование то же, что у host-scoped `ClientConfig.crypto` в фазе 48:
    пропущенное правило падает на первом вызове. Входит в 50.2, где слияние `errors` и так
    переписывается (§5.4).
20. **GraphQL остаётся вне фазы 50** до появления `PartialOutcome`, решение фазы 49 не
    пересматривается (§8).
21. **Бюджет имён: gate фазы 50 это «не больше 435 после фазы»**, то есть нулевой чистый рост.
    Добавляемые имена компенсируются удалением пути `Unpack`, параметров `response` и
    `responses`, и переносом внутренних имён пакета `websocket` под подчёркивание. Снижение
    к 300 это отдельная фаза с собственным планом, здесь не обещается.

Осталось подтвердить до 50.1, для каждого дана рекомендация по умолчанию:

1. Имя модуля для дата-классов маркеров. Рекомендация: `eazy_sdk.request.markers`, потому что
   `params` уже занят и содержит только параметры, а не дескрипторы тела.
2. Имя единого апдейта значения запроса. Рекомендация: `registry.evolve(value, **changes)` как
   единственная реализация и `sdk.orders.search.evolve(request, page=3)` на дескрипторе как
   единственная публичная точка. Метода на самом значении нет, чтобы не трогать пространство
   имён чужой модели (§4.6).
3. Pydantic без `serialize_by_alias=True`. Рекомендация: ошибка компиляции, если у поля есть
   alias, а конфиг его не включает на вывод. Молчаливая диагностика здесь недостаточна: имя на
   проводе это весь смысл alias'а у операции (§4.3).
4. `Serialization.documents`. Рекомендация: в 0.3.0, потому что поле `html` всё равно
   переименовывается при переходе на семьи, и делать это дважды дороже (§9.4).
