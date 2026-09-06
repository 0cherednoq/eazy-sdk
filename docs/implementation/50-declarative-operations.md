# Фаза 50. Декларативные операции: операция как типизированное значение

Статус: план утверждён 2026-09-06, исполнение не начато. Gates — в `STATUS.md`. Зависит от фаз
42 (атрибуты сервиса на роутере), 44 (`Serialization` на корне), 48 (конвейер представления)
и 49 (конверты протоколов).

Тип документа: authoritative implementation plan для Фазы 50. Архитектурные решения приняты в
[`eazy-sdk-declarative-operations.md`](eazy-sdk-declarative-operations.md) (далее «дизайн»);
этот документ переводит их в задачи, сигнатуры, диагностики, тесты и gates так, чтобы фазу
можно было исполнить автономно, без обращения к автору дизайна. Там, где план уточняет или
отклоняется от дизайна, это записано в §10 с обоснованием; дизайн остаётся источником намерения,
план — источником механизма.

---

## 0. Как исполнять этот план автономно

Правила исполнения, дополняющие `AGENTS.md`:

1. **Порядок шагов фиксирован**: 50.1 → 50.2 → 50.3 → 50.4. Внутри шага подзадачи идут в
   порядке номеров; каждая подзадача — отдельный коммит с сообщением
   `Phase 50.N.M: <что сделано>`. Шаг закрывается коммитом `Implement phase 50.N: <название>` после
   зелёных gates шага (§9).
2. **Перед первым коммитом 50.1** записать в `STATUS.md` секцию `## Phase 50 — declarative
   operations (2026-09-06)` со `State: active` и таблицей gates, куда результаты дописываются по
   мере прохождения. Формат — как у фазы 49 (`STATUS.md:4049-4118`).
3. **Один путь исполнения.** Декоратор `@api.get` не имеет собственной ветки компилятора: он
   синтезирует класс операции и дальше идёт тем же кодом, что `op(...)`. Любая проверка вида
   `if descriptor.is_decorated:` в компиляторе или исполнителе — дефект плана, а не приём.
   Абсенс-тест 50.1.9 это закрепляет.
4. **Нет алиасов и shim'ов.** `Unpack[TypedDict]`, `responses=`/`response=`, `Wire.projection`,
   `Serialization.html` удаляются, а не помечаются deprecated. Alpha не держит алиасов
   (`AGENTS.md`, дизайн §3).
5. **Тесты вместе с кодом**, в файлах, названных в §6. Тест, названный в задаче, — часть её
   Definition of Done; задача без зелёного теста не закрыта.
6. **Диагностики дословно.** Тексты ошибок в таблицах §4 — контракт, тесты проверяют подстроки
   из них. Менять формулировку можно только вместе с тестом и с записью в §10.
7. **Что делать при противоречии.** Если код, дизайн и этот план расходятся: измерить (пробник в
   scratchpad, как `plan_probe.py` из §3.4), записать факт в §10 «Отклонения», выбрать вариант,
   который не создаёт второго пути и не добавляет публичное имя, продолжить. Останавливаться
   только если вариант меняет публичный контракт, описанный в дизайне §4–§5.
8. **Бюджет имён** (дизайн, решение 21): gate 50 — число различимых публичных имён после фазы не
   больше, чем до неё. Базовая линия снимается скриптом из 50.1.1 **до** любых правок и
   записывается в `STATUS.md`.
9. **Definition of Done шага**: все подзадачи закоммичены; все тесты §6 шага зелёные; все gates
   §9 зелёные с записанным выводом; exit criteria шага отмечены в §8; `STATUS.md` обновлён;
   отклонения записаны в §10 этого документа.

---

## 1. Что не так

Кратко, подробности в дизайне §2–§3.

- **Запрос не является значением.** Аргументы операции живут только в сигнатуре функции или в
  `TypedDict`. Их нельзя создать заранее, положить в очередь, изменить одно поле и отправить
  снова. `TypedDict` не умеет defaults и `default_factory`, поэтому форма `Unpack[TypedDict]`
  вынуждает повторять значения по умолчанию в каждом вызове.
- **Имена на проводе живут не там.** У `dataclass` нет alias'ов, поэтому имя пишется в маркере,
  а у Pydantic и msgspec alias'ы есть, но наш адаптер Pydantic читает их только при
  `serialize_by_alias=True` и молчит, если флага нет (измерено, дизайн §4.3).
- **Маркеры многословны.** `Annotated[int, Query()]` вдвое длиннее `Query[int]`, и на классе из
  десяти полей это видно сразу.
- **Ответ объявляется тремя способами** (`response=`, `responses=`, `errors=`), а чем читать тело
  — JSON или документ — автор объявляет руками, хотя это уже знает модель (`CSS`/`XPath` в
  метаданных).
- **`ApiError` не переживает `pickle`** (дизайн §5.5): `Exception.__reduce__` вызывает
  `cls(*args)` с одной строкой, а конструктор требует два аргумента.

---

## 2. Целевая модель: инварианты

Нормативный список. Каждый инвариант имеет тест в §6; номер инварианта указан в docstring теста.

- **I1. Операция — класс-значение.** `class GetOrder(HttpOperation[Order])` с полями-маркерами и
  атрибутом `__http__ = Http.get(...)`. Экземпляр класса — полный запрос до подготовки.
- **I2. Один компилируемый тип.** И `op(GetOrder)`, и `@api.get(...)` создают
  `_OperationDeclaration` с `operation_type`; компилятор и исполнитель не знают, кто написал класс.
- **I3. Маркер говорит «куда», модель — «как называется».** Имя на проводе берётся у модели
  (`rename`, `alias`); маркер даёт имя только у `dataclass` и для опций размещения. Два источника
  имени на одном поле — ошибка компиляции.
- **I4. Короткая форма маркера — обобщённый алиас над `Annotated`.** `Query[int]` и
  `Annotated[int, markers.Query()]` дают одну и ту же аннотацию; компилятор их не различает.
- **I5. `frozen` обязателен**; `default_factory` вызывается один раз при создании значения;
  `prepare()` отдаёт байты, лежащие в значении.
- **I6. Семья экстрактора выводится из модели**: модель с метаданными селектора читается
  документным бэкендом, без них — структурным. Деклараций `Decode`/`extract=` нет.
- **I7. Контент-тип выбирает только среди объявленных для статуса кейсов**, никогда — модель сам по
  себе.
- **I8. Специфичность**: точный статус > диапазон > `DEFAULT`; при равной специфичности —
  явная медиа > wildcard > без медиа; затем условие `when=` > без условия; затем слой: операция >
  сервис > клиент. Ничья на верхней ступени — `AmbiguousResponseOutcome`.
- **I9. Ошибки инфраструктуры принадлежат сервису или клиенту**, не операции: `errors` на роутере
  и `ClientConfig.errors` по хосту.
- **I10. Исключение не обязано наследовать `ApiError`**: форма `(Model, factory)` принимает любую
  фабрику.
- **I11. `ApiError` переживает `pickle`** как модель ошибки плюс редактированная сводка; тело и
  заголовки границу процесса не пересекают.
- **I12. Протокол — номинальный базовый класс операции**: `HttpOperation`, `RpcOperation`,
  `WsCall`, `WsSubscribe`, `WsSend`. Роутер принимает только свои.
- **I13. Бюджет имён не растёт.**

---

## 3. Архитектура

### 3.1. Модули

| Модуль | Статус | Содержимое |
|---|---|---|
| `eazy_sdk/operation.py` | новый | `HttpOperation[T]`, `Http`, `op()`, `_HttpSpec` |
| `eazy_sdk/protocols/operation.py` | новый (50.4) | `RpcOperation[T]`, `Rpc`, `_RpcSpec`; экспорт из `eazy_sdk.protocols` |
| `eazy_sdk/request/markers.py` | новый | реэкспорт дата-классов `Path`, `Query`, `QueryString`, `Header`, `Cookie`, `JsonField`, `Form`, `Part`, `JsonBody`, `FormBody`, `MultipartBody`, `BytesBody`, `ReplayableStreamBody`, `BodyProjection` одним пространством имён |
| `eazy_sdk/request/short.py` | новый, приватный | обобщённые алиасы `Path = Annotated[_T, markers.Path()]` и остальные; реэкспортируются из `eazy_sdk.request` и корня под короткими именами |
| `eazy_sdk/sentinels.py` | изменён | `type Omittable[T] = T \| Unset`; корень экспортирует `Omittable`, `UNSET` |
| `eazy_sdk/compile/input.py` | изменён | `inspect_operation_input(operation_type, ...)` заменяет `inspect_method_input`; путь `Unpack` удалён |
| `eazy_sdk/compile/http_operation.py` | изменён | `_OperationDeclaration.operation_type`, `.projection`, `.response_spec`; `MethodInputSchema.operation_type` |
| `eazy_sdk/api.py` | изменён | один дескриптор `_OperationDescriptor[P, T]` с `__get__`-overload по владельцу; декоратор синтезирует класс; `_Verb` принимает `success=`/`errors=`/`fallback=`/`projection=` |
| `eazy_sdk/response/_mapping.py` | новый, приватный | нормализация `success=`/`errors=`/`fallback=` в `Responses`; вывод семьи; форма `(Model, factory)` |
| `eazy_sdk/response/cases.py` | изменён | ранжирование в `inspect`, `precedence` у кейсов, `Json.unwrap`, `ApiError.__reduce__`, `ErrorSummary` |
| `eazy_sdk/models/adapters.py` | изменён | `ModelAdapter.evolve`, `ModelAdapter.frozen`; `ModelAdapterRegistry.evolve` |
| `eazy_sdk/models/documents.py` | новый | `is_document_model(model, registry)` — единственная реализация вывода семьи |
| `eazy_sdk/serialization.py` | изменён | `Serialization.documents` вместо `html`; `DocumentBackend.media_types` |
| `eazy_sdk/clients/config.py` | изменён | `ClientConfig.errors` |
| `eazy_sdk/compile/http_compiler.py` | изменён | проекция читается из декларации; указатели подписи проверяются против имён на проводе |
| `eazy_sdk/websocket/operation.py` | новый | `WsCall[T]`, `WsSubscribe[T]`, `WsSend`, `Ws` |
| `plugins/html`, `plugins/xml` | изменены | `media_types` у бэкендов, `documents` вместо `html` |
| `plugins/openapi` | изменён | генерация классов операций |
| `plugins/adaptix` | новый | `AdaptixModelAdapter` |
| `scripts/surface_count.py` | новый | метрика 4.5 как воспроизводимая команда |

### 3.2. Поток данных

```
класс операции (dataclass | pydantic | msgspec) + __http__
        │  op(GetOrder)               │  @api.get(...) def get_order(self, *, ...)
        │                             ▼
        │                    синтез dataclass(frozen, slots, kw_only) GetOrder.Operation
        ▼                             │
_OperationDescriptor[P, T] ◄──────────┘
        │  __set_name__(owner, name): owner, имя, kind (sync/async) по роутеру
        │  первый доступ через роутер: resolve_for(api)
        ▼
_OperationDeclaration(operation_type=..., input_schema=inspect_operation_input(...),
                      response_spec=..., projection=...)
        │  resolve(defaults, serialization): errors-цепочка, unwrap, вывод семьи → Responses
        ▼
compile_endpoint(...)  ── без изменений по существу; проекция берётся из декларации
        ▼
executor: prepare / execute  ── без изменений; ClientConfig.errors подмешивается по хосту
```

Вызов: `sdk.orders.get_order(order_id="42")` → `_bind_arguments` строит `GetOrder(order_id="42")`,
снимает значения полей, отбрасывает `UNSET`, отдаёт `values: dict[str, object]` — ровно тот
словарь, который исполнитель принимает сегодня. `prepare()`, `with_response()`, `Identity`,
`bind()`, state machine попыток, пять политик, конвейер фазы 48 — не меняются.

### 3.3. Типизация

`op()` типизирован через `Callable[P, HttpOperation[T]]`: у класса с `dataclass_transform`
(dataclass, Pydantic, msgspec) `P` — параметры `__init__`, `T` — generic-аргумент базы. Один
дескриптор, sync/async выбирается перегрузкой `__get__` по типу экземпляра-владельца — приём
`bind_method` из unihttp:

```python
class _OperationDescriptor[**P, T]:
    @overload
    def __get__(self, instance: None, owner: type[object]) -> _OperationDescriptor[P, T]: ...
    @overload
    def __get__(self, instance: AsyncApi, owner: type[object] | None = None) -> _BoundAsyncOperation[P, T]: ...
    @overload
    def __get__(self, instance: SyncApi, owner: type[object] | None = None) -> _BoundSyncOperation[P, T]: ...
```

Декоратор сохраняет `P` функции (как сегодня), поэтому у декорированной операции в сигнатуре
остаётся `options: CallOptions | None`; у `op()`-операции `options` передаётся через `send()` (§4.3).

### 3.4. Измеренные факты, на которые опирается план

Пробник `scratchpad/plan_probe.py`, Python 3.13, текущий master:

| Факт | Результат |
|---|---|
| Поле `field(init=False, default=...)` видно адаптеру dataclass | да: `('platform', required=False, default='android')`, в `__init__` отсутствует |
| Атрибут `__http__` переживает dataclass(slots), msgspec.Struct, pydantic.BaseModel | да, у всех трёх |
| Generic-аргумент читается из `__orig_bases__` | да: `get_origin(base) is HttpOperation → get_args(base)[0]` |
| Признак frozen | dataclass: `__dataclass_params__.frozen`; msgspec: `__struct_config__.frozen`; pydantic: `model_config["frozen"]` |
| Pydantic alias без `serialize_by_alias` | адаптер отдаёт `wire_name='per_page'`, `validation_name='perPage'` — это и есть сигнал для диагностики |
| Обновление значения | dataclass `dataclasses.replace`; msgspec `msgspec.structs.replace`; pydantic `model_copy(update=)` |
| Проверка «модель требует селекторов» | живёт в плагине: `plugins/html/eazy_sdk_html/schema.py:206`; в ядре её нет, поэтому вывод семьи нужен как отдельная функция ядра (§4.7) |
| `make_dataclass` с `slots=True`, `kw_only=True`, generic-базой и `namespace={"__http__": ...}` | работает: слоты, frozen, `__orig_bases__`, hints с маркерами — все на месте (`scratchpad/make_dc_probe.py`) |
| Кап корня | `tests/rewrite/test_phase14_public_api.py:914`: `len(eazy_sdk.__all__) <= 40`, сейчас 38 |
| Метрика 435 | нигде не автоматизирована; per-namespace капы есть в `test_phase33_public_surface.py`, `test_ext_surface.py`, `test_protection_surface.py` |

---
## 4. Спецификация компонентов

Каждый подраздел: публичная форма, правила, диагностики, что измерить тестом. Номера
диагностик `D-xx` используются в §6.

### 4.1. Маркеры: дата-классы в `markers`, короткие алиасы в корне

**`eazy_sdk/request/markers.py`** — только реэкспорт, ни одного нового класса:

```python
"""Placement descriptors, qualified: ``markers.Query("caseId")`` when a name or option is needed."""

from eazy_sdk.request.descriptors import (
    BodyProjection, BytesBody, Form, FormBody, JsonBody, JsonField, MultipartBody, Part,
    ReplayableStreamBody,
)
from eazy_sdk.request.params import Cookie, Header, Path, Query, QueryString

__all__ = [...]  # все перечисленные, отсортированы
```

**`eazy_sdk/request/short.py`** (приватный модуль; имя не публикуется):

```python
from typing import Annotated, TypeVar
from eazy_sdk.request import markers

_T = TypeVar("_T")

Path = Annotated[_T, markers.Path()]
Query = Annotated[_T, markers.Query()]
Header = Annotated[_T, markers.Header()]
Cookie = Annotated[_T, markers.Cookie()]
JsonField = Annotated[_T, markers.JsonField()]
Form = Annotated[_T, markers.Form()]
Part = Annotated[_T, markers.Part()]
JsonBody = Annotated[_T, markers.JsonBody()]
FormBody = Annotated[_T, markers.FormBody()]
MultipartBody = Annotated[_T, markers.MultipartBody()]
BytesBody = Annotated[_T, markers.BytesBody()]
```

Форма `TypeVar` + `Annotated`, **не** `type Path[T] = ...` (PEP 695): `get_type_hints(...,
include_extras=True)` подставляет `TypeVar`-алиас в `Annotated[int, Path()]` напрямую, а
`TypeAliasType` пришлось бы разворачивать вручную. Это измерено в дизайне §4.4 на обоих
проверяющих.

Правила:

- `eazy_sdk.request.__init__` и `eazy_sdk.__init__` экспортируют под короткими именами **алиасы**
  из `short.py`. Дата-классы доступны только как `markers.Path` и т. д. Внутренний код
  (`compile/input.py`, `dependencies`, `http_compiler.py`, плагины html/xml/openapi, `websocket`)
  импортирует дата-классы из `params`/`descriptors`/`markers`, никогда из корня. Gate: `grep -rn
  "from eazy_sdk import .*\b\(Path\|Query\|Header\|Cookie\)\b" eazy_sdk plugins` пуст, кроме
  тестов и примеров.
- `QueryString` и `BodyProjection` короткой формы не имеют (обязательные аргументы).
- `Inject.placement` принимает те же дата-классы, что сегодня; `Inject(markers.Header("X-Id"),
  DEVICE_ID)` — единственная форма, потому что алиас не несёт имени.
- Компилятор не различает `Query[int]` и `Annotated[int, markers.Query()]`: `_unwrap` в
  `compile/input.py` уже собирает метаданные вложенных `Annotated`.

Typing-фикстура (50.1.8): в `tests/unit/test_phase50_typing.py` по образцу
`test_phase25_typing.py` (inline-исходник → временный файл в `tests/` → `mypy --strict` и
`basedpyright`):

```python
@dataclass(frozen=True, slots=True)
class GetOrder(HttpOperation[Order]):
    __http__ = Http.get("/orders/{order_id}")
    order_id: Path[str]
    expand: Query[tuple[str, ...]] = ()
    locale: Annotated[str, markers.Header("Accept-Language")] = "en"

def takes_str(value: str) -> None: ...
takes_str(GetOrder(order_id="1").order_id)          # алиас прозрачен для проверяющего
assert_type(GetOrder(order_id="1").expand, tuple[str, ...])
```

### 4.2. `HttpOperation[T]`, `Http`, `_HttpSpec`

**`eazy_sdk/operation.py`**:

```python
class HttpOperation[T]:
    """Nominal base of an HTTP operation class. Carries no fields and no constructor.

    ``T`` is the default success value: ``HttpOperation[Order]`` reads as ``success={200: Order}``
    unless ``__http__`` declares ``success=`` explicitly.
    """

    __slots__ = ()
    __http__: ClassVar[_HttpSpec]


@dataclass(frozen=True, slots=True, kw_only=True)
class _HttpSpec:
    method: str
    path: str
    operation_id: str | None = None
    success: SuccessSpec | None = None
    errors: Mapping[Selector, ErrorSpec | Sequence[ErrorSpec]] = field(default_factory=dict)
    fallback: ErrorSpec | None = None
    inherit_errors: bool = True
    security: object = _INHERIT
    requires: tuple[object, ...] = ()
    inject: tuple[Inject, ...] = ()
    signing: object = _INHERIT
    crypto: PayloadCrypto | None | _Inherit = _INHERIT
    protections: tuple[SolverRequirement[Any, Any], ...] = ()
    wire: Wire = EMPTY_WIRE
    projection: BodyProjection[Any, Any] | None = None
    tags: tuple[str, ...] = ()
    idempotent: bool | None = None
    raw_response: bool = False
    discriminator: str | None = None        # заполняет только Rpc (50.4)


class Http:
    """The one public entry for HTTP verbs: ``Http.get(...)``, ``Http.post(...)``, ``Http.request(...)``."""

    __slots__ = ()

    @staticmethod
    def request(method: str, path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec: ...
    @staticmethod
    def get(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec: ...
    # delete, head, options, patch, post, put, trace — так же
```

`_HttpOptions` — `TypedDict(total=False)` со всеми полями `_HttpSpec`, кроме `method`, `path`,
`discriminator`. `_Verb` из `api.py` реализуется через `Http`: `api.get(path, **options)` строит
тот же `_HttpSpec` (§4.5), так что набор параметров у декоратора и у класса один и тот же по
построению, а не по соглашению. Типы `Selector`, `Spec`, `ErrorSpec`, `SuccessSpec` переезжают из
`experiments/response_case_sugar/_sugar.py` в `eazy_sdk/response/_mapping.py` (§4.7).

Правила класса операции (проверяются в `inspect_operation_input`, §4.4, при первом
`resolve_for`, то есть при первом обращении к операции через роутер; для декоратора — при создании
класса роутера, потому что синтез класса происходит там):

| # | Правило | Диагностика (`PlanError`) |
|---|---|---|
| D-01 | класс наследует `HttpOperation` и имеет `__http__` типа `_HttpSpec` | `operation class GetOrder has no __http__; assign Http.get(...) or another verb` |
| D-02 | класс frozen по правилам своей библиотеки (§3.4) | `operation class GetOrder must be frozen: use @dataclass(frozen=True) / msgspec.Struct(frozen=True) / ConfigDict(frozen=True)` |
| D-03 | для Pydantic `BaseModel` стоит в базах раньше `HttpOperation` | `operation class GetOrder must list BaseModel before HttpOperation: class GetOrder(BaseModel, HttpOperation[Order])` |
| D-04 | класс поддержан реестром адаптеров и не является TypedDict | `operation class GetOrder is not a model any configured adapter supports` |
| D-05 | generic-аргумент задан, либо `success=` задан явно | `operation class GetOrder declares neither HttpOperation[T] nor success=` |
| D-06 | `operation_id` уникален в роутере | существующая `duplicate operation_id: ...`, дополненная именами обоих классов |

`operation_id` по умолчанию — `cls.__qualname__` (дизайн, решение 16). Для класса, синтезированного
декоратором, — имя функции (поведение декоратора не меняется).

### 4.3. `op()` и дескриптор

```python
def op[**P, T](operation: Callable[P, HttpOperation[T]], /) -> _OperationDescriptor[P, T]:
    """Publish an operation class on a router with the constructor's own signature."""
```

Единственный аргумент. `operation_id` и всё остальное живёт в `__http__` (отклонение от дизайна
§4.6 `op(..., name=None)`, см. §10).

Рантайм-проверки в `op()` (при объявлении класса роутера, `TypeError`, как у других ошибок
объявления в `api.py`):

- аргумент — класс; иначе `op() expects an operation class, got <repr>`;
- класс — подкласс `HttpOperation` или `RpcOperation` (50.4) или WS-операции (50.4); иначе
  `op() expects a subclass of HttpOperation, RpcOperation, WsCall, WsSubscribe or WsSend`.

`_OperationDescriptor[P, T]` заменяет пару `_AsyncOperationDescriptor`/`_SyncOperationDescriptor`.
Вид (sync/async) определяется владельцем в `__set_name__`; для декоратора — как сегодня, по
`iscoroutinefunction`, и в `_validate_api_class` сверяется с роутером (существующая проверка
`wrong function kind` остаётся для декоратора; для `op()` вид всегда совпадает с роутером).

Связанная операция (`_BoundAsyncOperation[P, T]` / `_BoundSyncOperation[P, T]`) получает
методы; сигнатуры для async-варианта:

```python
async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T
async def with_response(self, *args: P.args, **kwargs: P.kwargs) -> ResponseEnvelope[T, Any]
async def prepare(self, *args: Any, options: PrepareOptions | None = None, **kwargs: Any) -> PreparedCall
def request(self, *args: P.args, **kwargs: P.kwargs) -> Operation          # 50.3, без I/O
async def send(self, request: Operation, /, *, options: CallOptions | None = None) -> T   # 50.3
async def send_with_response(self, request: Operation, /, *, options: CallOptions | None = None) -> ResponseEnvelope[T, Any]  # 50.3
def evolve(self, request: Operation, /, **changes: object) -> Operation    # 50.3
Operation: type[Operation]                                                  # класс операции
declaration: _OperationDeclaration[T]                                       # как сегодня
```

Здесь `Operation` в аннотациях — тип, выведенный из `Callable[P, HttpOperation[T]]` через
возвращаемый тип; в реализации дескриптор параметризован третьим TypeVar `Op`, но публичная
сигнатура `op()` от этого не меняется. Если вывести `Op` из `Callable[P, Op]` с ограничением
`Op: HttpOperation[Any]` невозможно на обоих проверяющих, `request()`/`send()`/`evolve()`
типизируются как `HttpOperation[T]`; это фиксируется typing-фикстурой 50.3.

`options` для `op()`-операции передаётся через `send(request, options=...)`; у декорированной
операции остаётся параметр `options` в сигнатуре функции (как сегодня). Обе формы проверяются
тестом 50.3.

`_bind_arguments` (общий для обоих путей):

```python
def _bind_arguments(self, api, *args, **kwargs) -> tuple[dict[str, object], CallOptions | None]:
    options = kwargs.pop("options", None) if self.accepts_options else None   # только декоратор
    request = self.operation_type(*args, **kwargs)     # ошибки конструктора модели не перехватываются
    return self._values_of(request), options

def _values_of(self, request) -> dict[str, object]:
    return {
        field.python_name: value
        for field in self.declaration.input_fields
        if not isinstance(value := getattr(request, field.python_name), Unset)
    }
```

`UNSET` отбрасывается здесь, поэтому исполнитель получает ровно тот словарь, что сегодня, когда
аргумент не передан. Тест: `prepare(page=UNSET)` и `prepare()` дают байт-в-байт одинаковый
запрос.

### 4.4. Чтение полей операции: `inspect_operation_input`

`eazy_sdk/compile/input.py`:

```python
def inspect_operation_input(
    operation_type: type[object],
    *,
    operation_id: str,
    path: str,
    models: ModelAdapterRegistry,
    projection: BodyProjection[Any, Any] | None,
    accepts_options: bool,        # True только для класса, синтезированного декоратором
) -> MethodInputSchema: ...
```

`inspect_method_input` и `_append_unpacked_fields` удаляются. `MethodInputSchema` становится
`(fields, operation_type)`; поле `unpacked` удаляется. `InputField` получает `omittable: bool`.

Алгоритм для каждого `ModelField` из `models.fields(operation_type)`:

1. `annotation, extra = unwrap_annotated(field.annotation)`; `metadata = (*field.metadata, *extra)`.
   Дубли по идентичности снимаются (Pydantic отдаёт метаданные и в hint, и в `FieldInfo`).
2. `Unset` вырезается из объединения: `int | Unset` → `int`, `omittable=True`. `required` тогда
   обязан быть `False` (D-12).
3. Размещение: ровно один маркер из `_PLACEMENT_TYPES`, как сегодня; при `projection is not None`
   поле без маркера — источник проекции (`placement=None`), как сегодня для TypedDict-источника.
4. Имя на проводе — таблица ниже.
5. Остальные проверки (`_validate_body`, `_validate_path`, кардинальность query) — как сегодня.

Разрешение имени на проводе (I3). Обозначения: `p` — Python-имя, `w` — `field.wire_name` от
адаптера, `v` — `field.validation_name`, `m` — `placement.name`:

| `m` | `w` | Результат | Диагностика |
|---|---|---|---|
| `None` | `== p` | `p` | — |
| `None` | `!= p` | `w` | — |
| задано | `== p` | `m` | — |
| задано | `!= p` | — | D-10: `input field 'per_page' in 'SearchOrders' names its wire field twice: model says 'PerPage', marker says 'perPage'; keep one` |
| любое | `== p`, но `v is not None and v != p` (Pydantic alias без `serialize_by_alias`) | — | D-11: `input field 'per_page' in 'SearchOrders' has alias 'perPage' that will not reach the wire; set model_config = ConfigDict(serialize_by_alias=True)` |

D-11 срабатывает до D-10. Для msgspec `w` и `v` совпадают всегда (адаптер читает `encode_name`),
для dataclass `w == p` всегда, поэтому таблица сводится к «dataclass именует маркером, остальные —
моделью».

Прочие диагностики:

| # | Условие | Текст |
|---|---|---|
| D-07 | поле без маркера и без проекции | существующий `input field 'x' in 'Op' has no placement` |
| D-08 | неизвестный объект в метаданных | существующий `... has unknown markers: ...`; метаданные Pydantic `FieldInfo` и msgspec игнорируются по типу, не считаются неизвестными |
| D-09 | поле `options` в классе операции | `operation class Op declares field 'options', which is reserved for CallOptions` |
| D-12 | `Omittable[T]` с обязательным полем | `input field 'page' in 'Op' is Omittable but has no default; give it UNSET` |
| D-13 | `Unpack`/`**kwargs` в декорированной функции | `operation 'get_user' uses **request: Unpack[...], which 0.3.0 removed; declare an operation class and publish it with op(...)` |

Поля `init=False` (константы, §4.5 дизайна) читаются как обычные поля с `required=False` и
попадают в `input_fields`; в сигнатуре `__init__` их нет, поэтому `P` их не показывает. Это
измерено (§3.4).

### 4.5. Декоратор синтезирует класс

`_OperationDecorator.__call__(declaration)`:

1. Как сегодня: сигнатура, `self`, `options`, hints, `result_type` (из return annotation; generic
   `HttpOperation[result_type]` строится из него).
2. Поля: каждый keyword-only параметр, кроме `options`, становится полем
   `(name, hint, field(default=...))`. Правила default:
   - нет default → обязательное поле;
   - default `None` → поле `Omittable[hint] = UNSET` — так сохраняется сегодняшняя семантика
     «не передан → не отправляется», при этом явно переданный `None` уходит на провод по
     `Wire.encoding`, как и сегодня (`_bind_arguments` учитывает «передан ли»);
   - любой другой default → как есть.
   - `default_factory` в сигнатуре функции невыразим; это и есть причина писать класс.
3. Класс: `dataclasses.make_dataclass(func.__name__, fields, bases=(HttpOperation[result_type],),
   frozen=True, slots=True, kw_only=True, namespace={"__http__": spec, "__module__":
   func.__module__, "__doc__": func.__doc__})`. `__qualname__` дописывается в `__set_name__`:
   `f"{owner.__qualname__}.{name}.Operation"` (дизайн, решение 1).
4. Далее — ровно `op(cls)`, с двумя отличиями, которые дескриптор хранит как флаги:
   `accepts_options=True` и `signature=<сигнатура функции>` для `__signature__` (IDE видит
   функцию, как сегодня). Абсенс-тест: в `eazy_sdk/compile/` и `eazy_sdk/clients/` нет ни одного
   чтения этих флагов.

`_Verb.__call__` принимает **только** новые параметры: `success=`, `errors=`, `fallback=`,
`projection=` и остальные из `_HttpOptions`. `responses=` и `response=` удаляются вместе с
`_SingularOperationDecorator` и `_singular_responses`. Поскольку `_Verb` строит `_HttpSpec`,
декоратор и класс не могут разойтись по набору параметров.

Пример эквивалентности (golden-тест 50.1.9):

```python
from dataclasses import dataclass

from eazy_sdk import Http, HttpOperation, Path, Query, SyncApi, api, op


class UsersApi(SyncApi):
    @api.get("/users/{user_id}", errors={404: UserNotFound})
    def get_user(self, *, user_id: Path[int], locale: Query[str] = "en") -> User:
        raise NotImplementedError


@dataclass(frozen=True, slots=True, kw_only=True)
class GetUser(HttpOperation[User]):
    __http__ = Http.get("/users/{user_id}", operation_id="get_user", errors={404: UserNotFound})
    user_id: Path[int]
    locale: Query[str] = "en"


class UsersApi2(SyncApi):
    get_user = op(GetUser)

# prepare() обоих даёт равные method, url, headers, body; UsersApi.get_user.Operation — публичный класс
```

### 4.6. `Omittable[T]` и `UNSET`

`eazy_sdk/sentinels.py`:

```python
type Omittable[T] = T | Unset
```

Корень экспортирует `Omittable` и `UNSET`. Компилятор (§4.4, шаг 2) вырезает `Unset` из
аннотации для схемы и codegen, `_bind_arguments` (§4.3) отбрасывает значение. Типизировать `UNSET`
как `Any` запрещено (дизайн, решение 12); typing-фикстура проверяет, что
`GetOrder(page="x")` отвергается при `page: Query[Omittable[int]] = UNSET`.

Проверить и закрепить тестом: `UNSET` в `JsonField`-поле не попадает в тело (сегодня путь
«не передан» это гарантирует, потому что значения нет в словаре; новый путь обязан вести себя
так же — обеспечено фильтром в `_values_of`).

### 4.7. Ответы: нормализация, вывод семьи, специфичность, слои

**`eazy_sdk/response/_mapping.py`** (перенос `_sugar.py` без `Decode`):

```python
type Selector = int | str | StatusRange | DefaultStatus
type Spec = type[object] | None | ResponseRepresentation[Any]
type ErrorSpec = type[BaseException] | Spec | Error[Any] | tuple[type[object], ApiErrorFactory[Any]]
type SuccessSpec = Spec | Mapping[Selector, Spec | Sequence[Spec]] | Sequence[Success[Any]]

def normalize_responses(
    *,
    result_type: object | None,                 # generic-аргумент класса, может быть None
    success: SuccessSpec | None,
    errors: Mapping[Selector, ErrorSpec | Sequence[ErrorSpec]] | Sequence[ErrorSpec],
    fallback: ErrorSpec | None,
    models: ModelAdapterRegistry,
    unwrap: str | None,
    operation_id: str,
) -> Responses[Any]: ...

def result_type_of(success: SuccessSpec | None, generic: object | None) -> object | None: ...
```

Правила `representation(spec)` для «голого» значения (явные `Json`, `Html`, `Extracted`,
`Parsed`, `Text`, `Bytes`, `Empty` проходят как есть):

| `spec` | Представление |
|---|---|
| `None` | `Empty()` |
| `bytes` | `Bytes()` |
| `str` | `Text()` |
| модель с селекторами (`is_document_model`) | `Html(model)` |
| любая другая модель | `Json(model, unwrap=unwrap)` — только для success-кейсов |

`is_document_model(model, models)` в **`eazy_sdk/models/documents.py`** — единственная реализация
вывода семьи (I6): обходит `models.fields(model)` и вложенные модели, возвращает `True`, если хоть
одно поле несёт в метаданных объект, удовлетворяющий `SelectorMarker` (`eazy_sdk.serialization`),
или объект с атрибутом `selector`, удовлетворяющим `SelectorMarker` (это `Scope` плагина, без
импорта плагина). Рекурсия по вложенным моделям с защитой от циклов. Компиляционная проверка
селекторов остаётся в плагине (`_check_selector_languages`) и вызывается, как сегодня, через
`HtmlExtractor.prepare` из `_validate_serialization` (`executor.py:2076`).

Ошибки: `_error_case(status, spec)`:

| `spec` | Кейс |
|---|---|
| `Error(...)` | как есть |
| подкласс `ApiError` | `Error(status, representation(problem_model(spec)), exception=spec)`; модель из `ApiError[Model]` по `__orig_bases__`/MRO, как в `_sugar.problem_model` |
| подкласс `ApiError` без модели | D-14: `OrderNotFound does not name a problem model; declare class OrderNotFound(ApiError[Model]) or use (Model, factory)` |
| `(Model, factory)` | `Error(status, representation(Model), exception=factory)` |
| голая модель / представление | `Error(status, representation(spec))`, исключение `ApiError` |
| ключ `DEFAULT` в `errors` | D-15: `errors= cannot use DEFAULT; declare fallback= instead` |
| `"4xx"`/`"5xx"` | `StatusRange(400, 499)` / `StatusRange(500, 599)`; иное строковое значение — D-16 `unsupported status selector 'weird'` |

**Специфичность (I8)** — в `Responses.inspect`, между разбором и `arbitrate_cases`
(`cases.py:566`): если `matches` больше одного, оставить только кандидатов с максимальным рангом
`(status_rank, media_rank, has_condition, -precedence)`, где `status_rank`: `int` → 2,
`StatusRange` → 1, `DefaultStatus` → 0; `media_rank`: явная медиа без `*` → 2, с `*` → 1, `None`
→ 0; `has_condition` — 1/0; `precedence` — 0 операция, 1 сервис, 2 клиент. `arbitrate_cases` не
меняется (общий с WS). `_narrow` из прототипа не переносится.

`Success`/`Error` получают поле `precedence: int = 0` (dataclass с `eq=False`, поведение
равенства не меняется). `_OperationDescriptor.resolve` при слиянии сервисных `errors` ставит
`replace(case, precedence=1)`; исполнитель при подмешивании `ClientConfig.errors` — `2`.

**`unwrap`**: `Json` получает поле `unwrap: str | None = None` — JSON-указатель RFC 6901,
применяемый `JsonExtractor` после декодирования и до `_apply_header_sources`. Отсутствие пути в
документе — `Malformed(KeyError)`. `SERVICE_ATTRIBUTES` пополняется именем `unwrap`
(`_ServiceDefaults.unwrap: str | None = None`, переопределение вниз по цепочке, не накопление).
Применяется только к success-кейсам, построенным из голой модели; явный `Json(...)` не трогается
(§10, отклонение 3). Сервис с `protocol` и `unwrap` одновременно — D-17 `service declares both
protocol and unwrap; the envelope already selects the payload`.

**Ошибка «забытые селекторы»** (дизайн, решение 10): `UnexpectedResponseError.__str__` при
ответе с документной медиа (`text/html`, `application/xhtml+xml`, `*/xml`) и только структурными
кандидатами дописывает: `the response is a document but none of the attempted models (Catalog)
carries CSS or XPath metadata`. Тест на подстроку.

**Документная модель без обязательных полей** (решение 11): `_validate_serialization` для
`Html`-кейса без `when=` вызывает плагинную проверку; плагин добавляет в `ExtractionSchema`
свойство `has_required_field`; если `False` — D-18 `Html model BlockPage matches any document;
add a required selector field or when=` (`BackendCapabilityError`, как остальные ошибки этого
места).

### 4.8. `ApiError`: `(Model, factory)`, `pickle`

```python
@dataclass(frozen=True, slots=True)
class ErrorSummary:
    operation_id: str
    status_code: int
    content_type: str | None
    attempt: int
    method: str
    target: str            # уже редактированный target из PreparedRequestSummary


class ApiError[T](EazySdkError):
    def __init__(self, error: T, context: ResponseContext[object] | ErrorSummary) -> None: ...
    @property
    def summary(self) -> ErrorSummary: ...          # всегда доступна
    def __reduce__(self): return (type(self)._restore, (self.error, self.summary))
    @classmethod
    def _restore(cls, error, summary): return cls(error, summary)
```

`context` после восстановления — `ErrorSummary` (дизайн, решение 9). Тест: `pickle.loads(pickle.dumps(exc))`
восстанавливает тип и `error`; строковое представление тела и заголовки в pickled-байтах
отсутствуют (`assert b"secret-body" not in payload`).

Подкласс приложения с собственным `__init__` не поддерживается по решению 17: документация
показывает форму `(Model, factory)`.

### 4.9. `ClientConfig.errors`

```python
@dataclass(frozen=True, slots=True)
class ClientConfig:
    ...
    errors: Mapping[str, Mapping[Selector, ErrorSpec | Sequence[ErrorSpec]]] = field(default_factory=dict)
    """Host-scoped error cases: {"books.example": {429: RateLimited, 503: AccessBlocked}}."""
```

Ключ — точный хост (без порта, регистронезависимо). Применение в `Executor._compile_call`
(`executor.py:679-726`): после `_resolve_http_crypto` вычислить хост целевого URL, нормализовать
мэппинг через `normalize_responses`-часть для ошибок (с `models=self.serialization.models`),
пометить `precedence=2` и дописать в `contract.responses.errors`. Кэшировать по
`(id(declaration), host)`. `fallback` клиентом не задаётся (D-15 для `DEFAULT` действует).

### 4.10. Проекция и указатели подписи

- `_OperationDeclaration.projection: BodyProjection | None` — новое поле; `Wire.projection`
  удаляется (`request/wire.py:71`, `Wire.over` перестаёт его сливать). Компилятор читает
  `contract.projection` вместо `contract.wire.projection` (`http_compiler.py:505-508`).
- `BodyProjection.source: type | None = None`; `None` означает «класс операции»; при компиляции
  подставляется `operation_type`. Валидация источника (`_validate_projection_source`) переписывается
  на `models.fields(source)`: TypedDict больше не требуется; когда `source` — сам класс операции,
  источниками проекции считаются все поля без маркера.
- `BodyProjection.using` принимает один или два позиционных аргумента; арность читается
  `inspect.signature` при компиляции; второй аргумент — `Injected`:
  `type Injected = Mapping[RequestDependency[Any], object]` в `eazy_sdk/dependencies/__init__.py`,
  собирается из `requires=` перед стадией `PROJECTION`. Один тест на двухаргументную проекцию,
  один — на однoаргументную (совместимость).
- Указатели подписи (`canonical_json(include=)`, `body_output(json_pointer=)`) проверяются в
  `_compile_body_signature_paths` (`http_compiler.py:1073`) не только для проекции, но и для
  плоского тела из `JsonField`-полей и корневого `JsonBody`-поля: набор допустимых имён —
  `wire_name` полей тела (для проекции — поля `target` через адаптер, рекурсивно). Неизвестный
  указатель — D-19: `signature pointer '/Data' is not a field of the request body of 'CreateOrder';
  available: /data, /meta, /token`.

### 4.11. `request()`, `send()`, `evolve()`

`ModelAdapter` получает два члена (структурный протокол, реализуются четырьмя встроенными
адаптерами и плагином adaptix):

```python
def evolve(self, value: object, changes: Mapping[str, object]) -> object: ...
def frozen(self, annotation: object) -> bool | None: ...   # None — библиотека не знает понятия
```

Реализации (измерено, §3.4):

| Адаптер | `evolve` | `frozen` |
|---|---|---|
| dataclass | `dataclasses.replace(value, **changes)` | `__dataclass_params__.frozen` |
| pydantic | `value.model_copy(update=changes)` — без валидации, как и конструктор dataclass/msgspec; это документируется | `model_config.get("frozen", False)` |
| msgspec | `msgspec.structs.replace(value, **changes)` | `__struct_config__.frozen` |
| TypedDict | `{**value, **changes}` | `None` |

`ModelAdapterRegistry.evolve(value, **changes)` диспетчеризует по `adapter_for_value` и **до**
вызова адаптера проверяет, что все ключи `changes` — имена полей из `fields(type(value))`; одна
проверка на все библиотеки, чтобы сообщение было одно: `ModelAdapterError: GetOrder has no field
'pgae'; fields: order_id, expand, locale`.

На связанной операции: `request(**kw)` → `self.operation_type(**kw)`; `send(request)` →
`_values_of(request)` и обычный вызов; `evolve(request, **changes)` →
`api._serialization.models.evolve(request, **changes)`. `send` отвергает чужой класс:
`TypeError: send() expects GetOrder, got SearchOrders`.

### 4.12. `Serialization.documents`

```python
@dataclass(frozen=True, slots=True)
class Serialization:
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters)
    json: JsonBackend = DEFAULT_JSON_BACKEND
    documents: tuple[DocumentBackend, ...] = ()
    """Empty leaves the choice to the extraction plugin; several are chosen by response media."""
```

`DocumentBackend` получает член `media_types: frozenset[str]` (parsel: `{"text/html",
"application/xhtml+xml"}`; ElementTree: `{"application/xml", "text/xml"}` плюс правило суффикса
`+xml`). Выбор: медиа кейса, если задана явно (`Html(Page, media_type=...)`), иначе медиа ответа;
первый бэкенд, чьё множество принимает медиа; при единственном бэкенде — он всегда. Ни один не
принял — `BackendCapabilityError: no document backend accepts 'application/pdf' for 'GetCase';
configured: parsel (text/html, application/xhtml+xml)`. Компиляционная проверка селекторов —
против каждого бэкенда; модель, которую не может прочитать ни один, — ошибка с именем поля.

`HtmlExtractor` (`cases.py:211`) читает `serialization.documents` вместо `serialization.html`;
`plugins/html`, `plugins/xml` и их тесты обновляются; `Serialization(html=...)` больше не
принимается (`TypeError` от dataclass, строка миграции).

### 4.13. `RpcOperation[T]` и WS-операции (50.4)

`eazy_sdk/protocols/operation.py` (экспорт из `eazy_sdk.protocols`, не из корня — бюджет §B):

```python
class RpcOperation[T]:
    __slots__ = ()
    __rpc__: ClassVar[_RpcSpec]

class Rpc:
    @staticmethod
    def method(discriminator: str, /, *, errors=..., fallback=..., **options) -> _RpcSpec: ...
```

Лоуринг: `Rpc.method("getBalance", errors={-32001: Fault}, fallback=Fault)` →
`_HttpSpec(method="POST", path="/", discriminator="getBalance", success={200: rpc_result(T)},
errors=(rpc_error(-32001, Fault),), fallback=rpc_error_default(Fault))`; путь и глагол берёт
`protocol` роутера, как сегодня у `@api.rpc`. Поля без маркера — параметры (`JsonField`
по умолчанию); `Header`/`Cookie` допустимы; `Path`/`Query` — D-20 `RPC operation GetBalance cannot
place 'x' in path; the envelope owns the URL`. `@api.rpc` остаётся как сахар и синтезирует
`RpcOperation`-класс.

`eazy_sdk/websocket/operation.py` (экспорт из `eazy_sdk.websocket`):

```python
class WsCall[T]:      __ws__: ClassVar[_WsSpec]   # Ws.call("event", replies=..., replay=...)
class WsSubscribe[T]: __ws__: ClassVar[_WsSpec]   # Ws.subscribe("event", messages=..., resubscribe=...)
class WsSend:         __ws__: ClassVar[_WsSpec]   # Ws.send("event", ...)
```

Все поля — payload, маркеры запрещены (D-21 `WebSocket operation Subscribe cannot place fields;
the whole class is the payload`); `payload=JsonPayload(cls)` по умолчанию. `op()` принимает их и
требует владельца `AsyncWsApi` (D-22 `WebSocket operation Subscribe is published on SyncApi
OrdersApi; WebSocket operations belong to AsyncWsApi`); HTTP/RPC-операция на `AsyncWsApi` —
симметричная D-23. `_WsOperationDeclaration` получает `operation_type`, `ws.call`/`ws.subscribe`/
`ws.send` синтезируют класс так же, как `api.get`.

### 4.14. Codegen (`plugins/openapi`)

`_method`/`_operation_decorator`/`_operation_request_model` заменяются на `_operation_class` +
`_router_member`:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class GetUser(HttpOperation[User]):
    __http__ = Http.get(
        "/users/{user_id}",
        operation_id="getUser",
        errors={404: GetUserNotFound},
        fallback=GetUserError,
    )

    user_id: Path[int]
    locale: Annotated[Omittable[str], markers.Query("locale")] = UNSET


class UsersApi(AsyncApi):
    get_user = op(GetUser)
```

Правила: всегда dataclass со `slots`; имя на проводе через `markers.X("name")`, когда оно
отличается от Python-имени, иначе короткая форма; необязательный параметр — `Omittable[T] = UNSET`;
`"default"`-ответ → `fallback=`; `Html` не генерируется (как сегодня); проекции фазы 21 —
`projection=` в `__http__`, `source` опускается. Снимки `tests/snapshots/real_world` перезаписать
`scripts/update_openapi_snapshots.py`; strict-mypy над сгенерированным пакетом остаётся частью
`test_generation_matches_snapshot_and_passes_strict_mypy`. `_GENERATED_RESERVED_NAMES`
пополняется `Http`, `HttpOperation`, `op`, `markers`, `Omittable`.

### 4.15. Плагин adaptix

`plugins/adaptix/eazy_sdk_adaptix/__init__.py`:

```python
@dataclass(frozen=True, slots=True)
class AdaptixModelAdapter:
    retort: Retort
    types: tuple[type[object], ...]                       # только эти типы; пустой кортеж — ValueError
    names: Mapping[type[object], Mapping[str, str]] = field(default_factory=dict)  # python → wire
    name: str = "adaptix"
```

- `supports_type`/`supports_value` — только `types` (дизайн §9.2: иначе адаптер объявит поддержку
  всех dataclass и столкнётся со штатным);
- `fields` — dataclass-поля типа; `wire_name` берётся из `names[type]`, иначе равен Python-имени.
  Реторт не отдаёт таблицу имён наружу, поэтому она объявляется рядом с ним один раз, и это тот же
  список, что автор и так пишет в `name_mapping`;
- `dump`/`load` — `retort.dump`/`retort.load`; `evolve` — `dataclasses.replace`; `frozen` — как у
  dataclass.

Плагин минимален: он показывает, что центральный `Retort` выражается адаптером (решение фазы 44
«нет отдельных RequestDumper и ResponseLoader» не нарушается). Тесты: `types=()` — `ValueError`;
дамп через реторт с `name_mapping` даёт имена из `names`; вместе со штатным реестром нет
`AmbiguousModelAdapterError`; тип не из `types` обслуживает штатный адаптер.

---

## 5. Задачи

Каждая подзадача — коммит. В скобках — файлы. «Тест» — имя функции в файле из §6.

### 50.1. Операция-класс, короткие маркеры, `op()`, синтез из декоратора, codegen

- **50.1.1 Базовая линия бюджета имён.** `scripts/surface_count.py`: обходит `eazy_sdk` и
  модули, из которых импортирует автор SDK (`eazy_sdk`, `.request`, `.request.markers`,
  `.response`, `.models`, `.codecs`, `.crypto`, `.protection`, `.protection.advanced`,
  `.websocket`, `.protocols`, `.dependencies`, `.serialization`, `.clients`, `.handlers`, `.auth`,
  `.identity`, `.root`, `.sentinels`, `.operation`, `.ext`), собирает `__all__` каждого, печатает
  число различимых имён и таблицу по модулям. Запустить на master **до** правок, записать число в
  `STATUS.md` как `surface baseline`. Gate фазы: число после 50.4 ≤ baseline. Тест
  `test_surface_count_script_runs`.
- **50.1.2 Маркеры** (`request/markers.py`, `request/short.py`, `request/__init__.py`,
  `__init__.py`). Переключить внутренние импорты дата-классов на `params`/`descriptors`/`markers`
  (`compile/input.py`, `compile/http_compiler.py`, `dependencies/__init__.py`, `websocket/*`,
  `plugins/html`, `plugins/xml`, `plugins/openapi`, `plugins/asyncapi`). Существующие тесты,
  использующие `Annotated[int, Path()]` через корень, переписать на `markers.Path()` или на
  короткую форму. Тесты: `test_short_marker_equals_annotated_form`,
  `test_markers_module_reexports_every_descriptor`.
- **50.1.3 `Omittable`** (`sentinels.py`, `__init__.py`). Тест `test_omittable_field_is_dropped`.
- **50.1.4 `HttpOperation`, `Http`, `_HttpSpec`** (`operation.py`). `_Verb` переписывается поверх
  `Http`. Тесты `test_http_verbs_build_one_spec`, `test_spec_rejects_unknown_option`.
- **50.1.5 `inspect_operation_input`** (`compile/input.py`, `compile/http_operation.py`,
  `models/adapters.py` — `frozen`). Удалить `Unpack`-путь. Все правила и диагностики §4.2, §4.4.
  Тесты `test_field_reading_dataclass_pydantic_msgspec_same_schema`,
  `test_wire_name_from_model_rename`, `test_wire_name_from_marker_for_dataclass`,
  `test_wire_name_twice_is_rejected` (D-10), `test_pydantic_alias_without_serialize_by_alias_rejected`
  (D-11), `test_not_frozen_rejected` (D-02), `test_pydantic_base_order_rejected` (D-03),
  `test_missing_http_rejected` (D-01), `test_unpack_is_rejected_with_hint` (D-13),
  `test_init_false_field_is_serialized_but_not_a_parameter`.
- **50.1.6 `op()` и единый дескриптор** (`api.py`). `_OperationDescriptor[P, T]`,
  `__set_name__`, `_bind_arguments` через конструктор класса, `_validate_api_class` для класс-форм.
  Тесты `test_op_on_sync_and_async_router`, `test_op_rejects_non_operation`,
  `test_unset_and_omitted_argument_prepare_same_bytes`, `test_operation_id_defaults_to_qualname`,
  `test_duplicate_operation_id_names_both_classes` (D-06).
- **50.1.7 Синтез класса декоратором** (`api.py`). `success=`/`errors=`/`fallback=`/`projection=`
  вместо `responses=`/`response=`; `make_dataclass`; `.Operation`. На этом шаге `success=` и
  `errors=` временно нормализуются тем же кодом, что в 50.2, но без ранжирования — поэтому 50.1.7
  включает перенос `_sugar.py` в `response/_mapping.py` в объёме `representation`/`selector`/
  `_success_cases`/`_error_case` (§4.7), без специфичности и слоёв. Тесты
  `test_decorator_synthesizes_public_operation_class`,
  `test_decorator_none_default_becomes_unset`, `test_decorator_and_class_prepare_identical_bytes`
  (golden), `test_responses_keyword_is_gone`.
- **50.1.8 Typing-фикстуры** (`tests/unit/test_phase50_typing.py`). Позитив: сигнатура `op()`
  видна с defaults и `default_factory` на трёх библиотеках; `assert_type` результата на sync и
  async роутере; короткая форма прозрачна. Негатив: неверный тип аргумента, неизвестный kwarg,
  `UNSET` в поле без `Omittable`, WS-операция на `SyncApi` (после 50.4 — дополнить).
- **50.1.9 Абсенс-тесты и миграция кода репозитория.** `test_no_second_execution_path`: в
  `eazy_sdk/compile/` и `eazy_sdk/clients/` нет обращений к `accepts_options`, `signature`,
  `is_decorated`, `Unpack`; `inspect_method_input` не существует. Переписать `examples/*.py`
  (10 файлов), `tests/unit/test_docs_examples.py`, `tests/unit/test_documentation_api_examples.py`,
  `tests/_support/body_projection_proof.py` и тесты фаз 17, 21, 23, 25, 26, 48 в местах, где
  используются `Unpack`, `responses=`, `response=`, `Wire(projection=)`. Правило: тест переписывается
  на новую форму с сохранением ожиданий; ожидания меняются только там, где это записано в §10.
- **50.1.10 Проекция на декларации** (`compile/http_operation.py`, `compile/http_compiler.py`,
  `request/wire.py`, `request/descriptors.py`). `projection=`, `source=None`, `Injected`,
  валидация источника через адаптер. Тесты `test_projection_source_defaults_to_operation`,
  `test_projection_receives_injected`, `test_wire_projection_field_is_gone`.
- **50.1.11 Codegen** (`plugins/openapi`). §4.14; перезапись снимков; `test_rewrite_generator.py`
  и `test_real_world_schemas.py` обновляются на новую форму; `test_phase21_body_projection_codegen.py`
  — на `projection=`. Приёмочный тест шага: сгенерированный пакет проходит strict mypy и
  исполняет вызов через mock transport (существующие тесты, обновлённые).
- **50.1.12 Закрытие шага**: gates §9, `STATUS.md`, коммит `Implement phase 50.1: operation classes`.

### 50.2. Мэппинг ответов, вывод семьи, специфичность, слои, `ApiError`

- **50.2.1 Вывод семьи** (`models/documents.py`, `response/_mapping.py`). `is_document_model`;
  таблица §4.7. Тесты `test_document_model_is_read_by_html_backend_without_declaration` (все
  четыре случая `j_html_service.py`), `test_structural_model_stays_json`,
  `test_nested_document_model_detected`.
- **50.2.2 Специфичность и `precedence`** (`response/cases.py`, `api.py`). Тесты
  `test_exact_status_beats_range`, `test_range_beats_default_fallback`,
  `test_explicit_media_beats_wildcard`, `test_operation_case_beats_service_case`,
  `test_true_tie_is_ambiguous`.
- **50.2.3 Форма `(Model, factory)` и `DEFAULT`-диагностика.** Тесты
  `test_tuple_factory_raises_application_exception_without_apierror`, `test_default_key_in_errors_rejected`
  (D-15), `test_apierror_without_model_rejected` (D-14).
- **50.2.4 `unwrap`** (`response/cases.py`, `api.py`). Тесты `test_service_unwrap_strips_envelope`,
  `test_explicit_json_is_not_unwrapped`, `test_unwrap_with_protocol_rejected` (D-17),
  `test_unwrap_missing_pointer_is_malformed`.
- **50.2.5 `ApiError.__reduce__`, `ErrorSummary`.** Тесты `test_apierror_pickle_roundtrip`,
  `test_pickled_apierror_carries_no_body_or_headers`.
- **50.2.6 `ClientConfig.errors`** (`clients/config.py`, `clients/executor.py`). Тесты
  `test_client_errors_apply_by_host`, `test_client_errors_lose_to_service_and_operation`,
  `test_client_errors_ignore_other_hosts`.
- **50.2.7 Диагностики ответа**: подсказка про селекторы в `UnexpectedResponseError`; D-18 через
  `has_required_field` в плагине html. Тесты `test_unexpected_document_response_hints_selectors`,
  `test_html_model_without_required_field_rejected`.
- **50.2.8 Указатели подписи** (`http_compiler.py`). D-19. Тесты
  `test_signature_pointer_validated_against_wire_names`, `test_signature_pointer_uses_model_rename`.
- **50.2.9 `Serialization.documents`** (`serialization.py`, `response/cases.py`, `plugins/html`,
  `plugins/xml`). Тесты `test_documents_backend_chosen_by_media`,
  `test_single_document_backend_accepts_any_media`, `test_no_backend_for_media_is_capability_error`,
  `test_serialization_html_keyword_is_gone`.
- **50.2.10 Закрытие шага.**

### 50.3. Запрос как значение: `request()`, `send()`, `evolve()`

- **50.3.1 `ModelAdapter.evolve`, `ModelAdapterRegistry.evolve`** (`models/adapters.py`). Тесты
  `test_evolve_dataclass_pydantic_msgspec_typeddict`, `test_evolve_unknown_field_names_fields`.
- **50.3.2 Методы связанной операции** (`api.py`). Тесты `test_request_builds_value_without_io`,
  `test_send_value_equals_direct_call_bytes`, `test_send_rejects_foreign_operation`,
  `test_evolve_then_send`, `test_send_options_for_op_operation`,
  `test_decorated_operation_keeps_options_parameter`.
- **50.3.3 Typing-фикстура**: `assert_type(sdk.orders.get_order.request(order_id="1"), GetOrder)`,
  `evolve` возвращает тот же тип, `send` принимает только его.
- **50.3.4 Пример** `examples/request_values.py` (очередь значений, `evolve(page=n)` в цикле
  без `Pages`) и строка в `examples/README.md`; тест в `test_docs_examples.py`.
- **50.3.5 Закрытие шага.**

### 50.4. RPC/WS-операции как классы, adaptix, документация, миграция

- **50.4.1 `RpcOperation`, `Rpc`** (`protocols/operation.py`, `api.py`). `@api.rpc` синтезирует
  класс. Тесты `test_rpc_operation_class_lowers_to_envelope_cases`,
  `test_rpc_operation_rejects_path_placement` (D-20), `test_api_rpc_decorator_synthesizes_rpc_class`;
  сюита фазы 49 зелёная без изменения ожиданий.
- **50.4.2 `WsCall`, `WsSubscribe`, `WsSend`, `Ws`** (`websocket/operation.py`, `websocket/api.py`).
  Тесты `test_ws_operation_classes_on_async_ws_api`, `test_ws_operation_rejects_placements` (D-21),
  `test_ws_operation_on_http_router_rejected` (D-22), `test_http_operation_on_ws_router_rejected`
  (D-23); `tests/websocket/` зелёная без изменения ожиданий.
- **50.4.3 Плагин adaptix** (`plugins/adaptix`, `pyproject.toml` workspace, `testpaths`). §4.15.
- **50.4.4 Документация** (§7) и `scripts/docs_freshness.py update`.
- **50.4.5 Миграция**: `docs-site/.../more/migration.mdx`, таблица Приложения A целиком.
- **50.4.6 Бюджет имён**: `scripts/surface_count.py` ≤ baseline; корень ≤ 40 (`test_phase14_public_api.py:914`).
  Если превышено — перенос имён по Приложению B, не ослабление теста.
- **50.4.7 Закрытие фазы**: gates §9, `STATUS.md` (State: Complete, Delivered 50.1–50.4,
  Decisions recorded, Commands run), строка в журнале
  `docs/eazy-sdk-architecture-refactor-plan.md` §6, статус дизайна → «реализовано в фазе 50».

---

## 6. Тесты

| Файл | Шаг | Что держит |
|---|---|---|
| `tests/unit/test_phase50_operations.py` | 50.1 | I1–I5, D-01…D-13, `op()`, синтез, `Omittable`, проекция на декларации |
| `tests/unit/test_phase50_typing.py` | 50.1, 50.3, 50.4 | видимость сигнатур на mypy и basedpyright; негативы |
| `tests/rewrite/test_phase50_golden.py` | 50.1 | байт-в-байт: декоратор ≡ класс; dataclass ≡ pydantic ≡ msgspec при одинаковых именах; `UNSET` ≡ «не передан» |
| `tests/unit/test_phase50_responses.py` | 50.2 | I6–I11, D-14…D-19, `unwrap`, `ClientConfig.errors`, pickle, `documents` |
| `tests/unit/test_phase50_request_values.py` | 50.3 | `request`/`send`/`evolve` на трёх библиотеках, `options` |
| `tests/unit/test_phase50_protocol_operations.py` | 50.4 | I12, D-20…D-23, RPC/WS-классы |
| `tests/unit/test_phase50_absence.py` | 50.1, 50.4 | нет второго пути; удалённые имена отсутствуют (`inspect_method_input`, `Wire.projection`, `Serialization.html`, `responses=`, `_SingularOperationDecorator`, `Decode`) |
| `plugins/openapi/tests/*` | 50.1 | генерация классов, снимки, strict mypy сгенерированного |
| `plugins/html/tests/*`, `plugins/xml/tests/*` | 50.2 | `media_types`, `documents`, `has_required_field` |
| `plugins/adaptix/tests/test_adapter.py` | 50.4 | адаптер поверх реторта, отсутствие конфликта |
| `tests/unit/test_surface_count.py` | 50.1 | скрипт метрики работает и печатает число |

Правила для тестов (дополняют `tests/README.md`): каждый тест инварианта начинается с docstring
`I<n>:`; диагностики проверяются подстрокой из таблиц §4; golden-тесты сравнивают `PreparedCall`
целиком (`method`, `url`, отсортированные заголовки, `body`), а не отдельные поля; трёхбиблиотечные
тесты параметризованы по фабрике класса, а не скопированы.

---

## 7. Документация и миграция

Страницы `docs-site/src/content/docs`, требующие переписывания (по `grep` на `Unpack[`,
`responses=`, `response=`, `Wire(projection`; 40 страниц):

- `getting-started/quickstart.mdx`
- `guides/requests/{index,path,query,headers,cookies,json,form,multipart,bytes,representation}.mdx`
  — `index` получает раздел «Операция как класс» и «Короткая форма маркеров»; `representation` —
  проекцию с `source` по умолчанию и `Injected`
- `guides/responses/{index,success,errors,html}.mdx` — мэппинг `success=`/`errors=`, вывод семьи,
  специфичность, `unwrap`, `(Model, factory)`, pickle
- `guides/{dependencies,multi-service,payload-crypto,protocols,websocket,xml}.mdx`
- `auth/{api-key,basic,bearer,combined,cookie,login,refresh,session}.mdx`
- `signing.mdx`, `api-reference/{api-methods,request,response,models-codecs,extensions,signing}.mdx`
- `more/examples/{json-auth,store-sdk}.mdx`, `more/migration.mdx`
- новая страница `guides/requests/values.mdx` (запрос как значение: `request`, `send`, `evolve`)

Стиль примеров — по `docs-example-style` (copy-paste runnable, встроенный тестовый HTML,
per-method разборы, multi-variant ответы). Каждая страница: обновить `sources:` в frontmatter,
затем `uv run python scripts/docs_freshness.py update <page>`.

`docs/implementation/sdk-authoring-reference.md` и `sdk-reference.md`: разделы про объявление
операций переписать на класс-форму; декоратор показать как короткую форму.

`examples/README.md`: уровни 2–9 обновить; новый уровень «запрос как значение».

Строки миграции (`more/migration.mdx`) — Приложение A.

---

## 8. Exit criteria

50.1:

- [ ] `Query[int]` и `Annotated[int, markers.Query()]` дают одну аннотацию; typing-фикстура зелёная
      на mypy и basedpyright.
- [ ] Операция на dataclass, Pydantic и msgspec с одинаковыми именами на проводе даёт байт-в-байт
      одинаковый `PreparedCall`.
- [ ] Декоратор и класс дают байт-в-байт одинаковый `PreparedCall`; `Api.op.Operation` публичен.
- [ ] `Unpack[TypedDict]`, `responses=`, `response=`, `Wire.projection` не существуют; D-13 даёт подсказку.
- [ ] D-01…D-13 воспроизводятся тестами дословно.
- [ ] Генератор OpenAPI выдаёт классы операций; снимки обновлены; сгенерированный пакет проходит
      strict mypy и исполняется.
- [ ] Абсенс-тест второго пути зелёный.

50.2:

- [ ] Все четыре случая `j_html_service.py` работают без единой декларации семьи.
- [ ] Специфичность I8 покрыта пятью тестами, включая настоящую ничью.
- [ ] `(Model, factory)` поднимает исключение приложения, не наследующее `ApiError`.
- [ ] `ApiError` переживает pickle без тела и заголовков.
- [ ] `ClientConfig.errors` применяется по хосту и проигрывает сервису и операции.
- [ ] `Serialization.documents` выбирает бэкенд по медиа; `html=` не принимается.
- [ ] Указатель подписи, не совпадающий с именем на проводе, — D-19.

50.3:

- [ ] `request()` не делает I/O; `send(request)` ≡ прямой вызов по байтам; `evolve` работает на трёх
      библиотеках и называет поля при опечатке.
- [ ] `options` доступен у `op()`-операции через `send`, у декорированной — параметром.

50.4:

- [ ] RPC- и WS-операции объявляются классами; сюиты фаз 49 и `tests/websocket/` зелёные без
      изменения ожиданий.
- [ ] Плагин adaptix дампит через реторт и не конфликтует со штатным адаптером.
- [ ] 40 страниц docs-site обновлены, `docs_freshness.py check` зелёный, миграция полная.
- [ ] `surface_count.py` ≤ baseline; `len(eazy_sdk.__all__) <= 40`.

---

## 9. Гейты

После каждого шага 50.N (и перед закрытием фазы — все):

```
uv run pytest -q
uv run mypy
uv run ruff check
uv run pytest -q tests/websocket tests/unit/test_phase49_envelopes.py tests/rewrite/test_phase04_signing.py tests/crypto
uv run python scripts/absence_audit.py
uv run python scripts/surface_count.py
uv run python scripts/docs_freshness.py check
uv run python docs-site/scripts/validate_docs.py
uv run --with-requirements docs-site/requirements.txt sphinx-build -W --keep-going -b dirhtml -c docs-site docs-site/src/content/docs .test-tmp/sphinx
uv build --all-packages --out-dir dist/ci && uv run --no-project python scripts/package_audit.py dist/ci && uv run --no-project python scripts/extras_smoke.py dist/ci
```

Docs-гейты обязательны с 50.4; на шагах 50.1–50.3 достаточно `docs_freshness.py check` после
`update` затронутых страниц (страницы, у которых `sources` указывают на удалённые имена, правятся
на том же шаге, иначе gate красный).

Результат каждого gate записывается в `STATUS.md` дословно (`PASS: N passed, M skipped in Ts`).

---

## 10. Отклонения от дизайна, принятые в плане

1. **`op()` без `name=`.** Дизайн §4.6 показывает `op(OperationType, *, name=None)`. `operation_id`
   уже живёт в `__http__` (решение 16); второй способ задать одно и то же — лишний. `op()`
   принимает один позиционный аргумент.
2. **`Inject` остаётся в `__http__`, а не полем класса.** Дизайн §4.1 упоминает «поля, объявленные
   `Inject`/`FromProtection`». Значение зависимости не является полем значения запроса (оно
   вычисляется на попытку), поэтому объявлять его полем значило бы нарушить I5. Форма
   `inject=(Inject(markers.JsonField("device_id"), DEVICE_ID),)` — единственная.
3. **`unwrap` применяется только к success-кейсам из голой модели.** Дизайн §5.1 п. 6 говорит
   «конверт снимается сервисным `unwrap`». Ошибочный конверт обычно лежит под другим ключом
   (`error`), и один указатель на оба исхода был бы неверен чаще, чем верен. Для ошибок — явный
   `Json(Model, unwrap="error")`.
4. **`options` у `op()`-операции — через `send()`.** `ParamSpec` конструктора не расширяется
   дополнительным параметром без потери точности сигнатуры; дизайн этот вопрос не поднимал.
5. **`RpcOperation` и `Rpc` экспортируются из `eazy_sdk.protocols`, WS-операции — из
   `eazy_sdk.websocket`**, а не из корня. Причина — тест `len(eazy_sdk.__all__) <= 40`
   (`tests/rewrite/test_phase14_public_api.py:914`) и Приложение B.
6. **`Responses`, `Success`, `Error` уходят из корня** в `eazy_sdk.response` (остаются
   публичными). С мэппингом они нужны редко; корень должен вместить `Http`, `HttpOperation`, `op`,
   `Omittable`, `UNSET`.
7. **`None`-default в декораторе становится `UNSET`** в синтезированном классе. Иначе декоратор
   начал бы отправлять `?locale=` там, где сегодня не отправляет ничего, и golden-тесты фаз 17/25
   потребовали бы смены ожиданий.
8. **`ClientConfig.errors` — мэппинг по точному хосту**, без путей и методов (у `CryptoRegistry`
   они есть). Дизайн говорит «host-scoped»; расширение до `RequestScope` — когда появится
   первый SDK, которому это нужно.
9. **Ранжирование по слоям через поле `precedence` на кейсе**, а не через порядок в кортеже:
   порядок уже несёт смысл (сервис перед операцией), и опираться на него молча было бы хрупко.
10. **`ModelField.default_factory` не добавляется.** `evolve` идёт через нативные функции
    библиотек, которым фабрика не нужна; codegen её не печатает. Отмечено как известное
    ограничение `fields()`.

Пункты, добавляемые исполнителем по правилу §0.7, дописываются сюда с датой.

---

## 11. Риски

- **Объём переписывания тестов и документации** (13 тестовых файлов с `Unpack`, 47 с
  `responses=`, 40 страниц). Смягчение: 50.1.9 и 50.4.4 — отдельные коммиты; правило «ожидания
  не меняются, кроме записанных в §10»; `docs_freshness.py update` страница за страницей.
- **`make_dataclass` и `slots=True` с generic-базой** — риск снят измерением
  (`scratchpad/make_dc_probe.py`): `make_dataclass(..., bases=(HttpOperation[dict],), frozen=True,
  slots=True, kw_only=True, namespace={"__http__": ...})` даёт класс со `__slots__`,
  `FrozenInstanceError` на присваивании, `__orig_bases__` с generic-аргументом и
  `get_type_hints(include_extras=True)` с маркерами; `__qualname__` переназначается после создания.
- **Pydantic и dunder-атрибут `__http__`.** Проверено, что Pydantic игнорирует его как поле
  (§3.4). Проверить также `model_json_schema()` сгенерированной операции в 50.1.5 — не должен
  падать.
- **Ранжирование ломает существующие ожидания `AmbiguousResponseOutcome`.** Существующие тесты,
  где ничья была между точным статусом и диапазоном, станут детерминированными. Каждый такой
  тест переписывается с явной записью в §10; ничьи между двумя точными статусами остаются
  неоднозначными.
- **Fingerprint плана.** Добавление членов адаптерам меняет `fingerprint_components` только если
  меняется `version`/`qualname`; менять их не нужно. Тест фазы 26 на стабильность fingerprint
  должен остаться зелёным.
- **Соблазн хранить состояние на дескрипторе.** `_resolved` остаётся на роутере, дескриптор —
  неизменяемая декларация; `__set_name__` пишет только `owner`, `name`, `kind`.

---

## 12. Открытые вопросы

Блокирующих нет. Значения по умолчанию, принятые планом (можно изменить до 50.1.2 без
последствий):

1. Имя приватного модуля алиасов — `eazy_sdk/request/short.py`.
2. `ClientConfig.errors` — точный хост, без `*.suffix`.
3. `ErrorSummary.target` — редактированный target из `PreparedRequestSummary`, без query.

---

## Приложение A. Миграция 0.2.0a5 → 0.3.0 (строки для `more/migration.mdx`)

| Было | Стало | Причина |
|---|---|---|
| `def op(self, **request: Unpack[Req]) -> T` | класс `Op(HttpOperation[T])` + `op(Op)` | defaults и `default_factory`, запрос как значение |
| `@api.get(path, responses=Responses(...))` | `@api.get(path, success={...}, errors={...}, fallback=...)` | один способ вместо трёх |
| `@api.get(path, response=Json())` | `@api.get(path)` с return annotation, либо `success={200: Model}` | семья выводится из модели |
| `Annotated[int, Query()]` из корня | `Query[int]` или `Annotated[int, markers.Query("name")]` | короткая форма |
| `from eazy_sdk import Path` (дата-класс) | `from eazy_sdk.request import markers; markers.Path(...)` | корневые имена стали алиасами |
| `Inject(JsonField("x"), dep)` | `Inject(markers.JsonField("x"), dep)` | то же |
| `Wire(projection=BodyProjection(...))` | `Http.post(..., projection=BodyProjection(target=..., using=..., encoding=...))` | проекция — представление, не байтовый контракт |
| `BodyProjection(source=TypedDict, ...)` | `source` опускается: источник — класс операции | одно объявление полей |
| `Serialization(html=ParselBackend())` | `Serialization(documents=(ParselBackend(),))` | несколько документных бэкендов |
| `from eazy_sdk import Responses, Success, Error` | `from eazy_sdk.response import ...` | бюджет корня |
| `Error(404, Json(Problem), exception=NotFound)` | `errors={404: NotFound}` при `class NotFound(ApiError[Problem])` | модель объявлена один раз |
| `Error(..., exception=factory)` | `errors={404: (Problem, factory)}` | без наследования от библиотеки |
| `@ws.call("event") async def f(self, *, ...)` | по-прежнему работает; альтернатива — `class F(WsCall[T])` + `op(F)` | классы для WS |
| `ApiError.context` после pickle | `ErrorSummary` | сводка вместо сырого контекста |
| `experiments/response_case_sugar` | удалён: реализовано | — |

## Приложение B. Бюджет имён

Корень (`eazy_sdk.__all__`, лимит 40, сейчас 38):

| Действие | Имена | Итог |
|---|---|---|
| добавить | `Http`, `HttpOperation`, `op`, `Omittable`, `UNSET` | 43 |
| убрать | `Responses`, `Success`, `Error` | 40 |

Вне корня: `+markers` (модуль), `+RpcOperation`, `+Rpc` (`protocols`), `+WsCall`, `+WsSubscribe`,
`+WsSend`, `+Ws` (`websocket`), `+ErrorSummary`, `+Injected`; `−` `JsonResponse` (`response`,
дублирует `Json`), `−` `MethodInputSchema.unpacked`, `−` `Wire.projection`; в пакете `websocket`
внутренние имена (`_messages`, `_outbound`, `_state` и т. д.), попавшие в `__all__` подпакета,
уводятся под подчёркивание по списку, который составляет 50.4.6 после запуска
`surface_count.py`. Gate: итог ≤ baseline. Если после 50.4.6 остаётся превышение, следующий
кандидат на вынос из корня — `Text` и `Bytes` (в мэппинге их заменяют `str` и `bytes`).

## Приложение C. Что записывать в `STATUS.md`

Секция `## Phase 50 — declarative operations (2026-09-06)`:

- `### State` — `Active (50.N)` во время работы, `Complete` в конце, одно-три предложения о том,
  какая проблема снята.
- `### Delivered` — по одному пункту на 50.N.M с именами модулей, тестов и количеством тестов.
- `### Decisions recorded` — копия §10 в английской формулировке.
- `### Surface baseline` — число из 50.1.1 и число после 50.4.6.
- `### Commands run` — таблица §9 с дословными результатами.
- `### Remaining work / blockers` — `None.` или список.

### Отклонения, записанные при исполнении 50.1 (2026-09-06)

11. **`op()` определён в `eazy_sdk/api.py`**, а не в `operation.py`: дескриптор живёт в `api.py`,
    и импорт из `operation.py` был бы циклическим. Публичное имя одно, экспортируется из корня.
12. **`inspect_operation_input` не принимает `accepts_options`.** Параметр из §4.4 противоречил
    бы абсенс-тесту 50.1.9 (компилятор не читает флаги декоратора); D-09 проверяется по имени
    поля независимо от формы.
13. **D-01 поднимается в `op()` при объявлении класса роутера**, не при первом `resolve_for`:
    `operation_id` нужен `_validate_api_class` уже при создании класса.
14. **Явный `BodyProjection.source=` сохраняет словарную семантику** (`using` получает mapping
    полей источника) и обе проверки фазы 21 («no placement» для лишнего неразмещённого поля,
    «also declares a placement» для размещённого поля источника). `source=None` — новая форма:
    `using` получает значение операции. Codegen оставляет явный `source=`, потому что
    сгенерированный и пользовательский (`ProjectionImport`) маппер написаны против TypedDict.
15. **Проверка requiredness источника проекции — по `omittable`**, а не по `required`: поле с
    default всегда присутствует в значении операции; пропустить можно только `Omittable`.
16. **Пропущенное обязательное поле и неизвестное поле — `TypeError` конструктора класса**, а не
    `OperationBindingError` (`missing_required`/`unknown_input`): запрос — значение, ошибка
    возникает до стадии bind. Тесты фазы 23 переписаны на это ожидание.
17. **`success=Json(status=201, when=...)` в «голой» форме** сохраняет свои `status`/`when`, а
    `Json()`/`Html()` без модели получает модель из типа результата — прежняя семантика
    `response=` без отдельного ключевого слова.
18. **Generic-алиасы (`list[Model]`, объединения) принимаются как JSON-модель** в `success=`:
    реестр моделей их загружает, как и раньше `Json(list[Model])`.
19. **Codegen эмитит явные кортежи `Success(...)`/`Error(...)`** в `success=`/`errors=`/
    `fallback=`, а не мэппинг `{404: Класс}`: только так сохраняются media types ответов
    (`application/problem+json`, `text/*`, `Empty(media_type=...)`) байт в байт.
20. **Корень: `JsonField` добавлен, `Text` и `Bytes` убраны** (Приложение B назвало их первыми
    кандидатами); итог — 39 имён при лимите 40.
21. **Сервисные ошибки попадают в операцию копией с `precedence=1`** (`replace(case,
    precedence=1)`), поэтому сравнение по идентичности в тесте фазы 25 заменено на сравнение
    полей.
22. **Имя класса, синтезированного codegen, — `<Pascal>Request`** (прежнее имя TypedDict): оно уже
    зарезервировано против имён моделей, а `GetUser` могло бы совпасть с моделью схемы.
23. **`ClientConfig` получает пятое поле `errors`** (2026-09-06). Тест фазы 44
    `test_the_config_is_three_groups_plus_the_crypto_registry` перечислял параметры
    конструктора; ожидание расширено до пяти имён с комментарием о причине. Само поле
    предписано §4.9 плана, поэтому это правка ожидания, а не отклонение от дизайна.
24. **`ErrorSummary` экспортируется из `eazy_sdk.response`** (2026-09-06), не из корня:
    Приложение B оставляет корень на 39 именах, а сводку читают там же, где ловят `ApiError`.
25. **`Json` подменяет свой `extractor` в `__post_init__`** (2026-09-06), когда задан
    `unwrap` и экстрактор оставлен по умолчанию: указатель применяет `JsonExtractor`
    (§4.7), а `extract(model)` не видит представление кейса.
26. **D-19 заменяет прежний текст «target field is not declared»** и для проекции
    (2026-09-06): §4.10 требует одну диагностику для всех форм тела. Ожидание теста фазы 21
    `test_body_signature_output_must_select_a_declared_target_field` переписано на новый текст
    с комментарием о причине.
27. **Корневое тело, не являющееся моделью** (`dict[str, str]`), не проверяется указателями
    подписи (2026-09-06): полей у него нет, а `body_output` дописывает в него новый ключ —
    ровно то, что делает тест фазы 48 `test_a_signature_base_and_a_body_output_use_the_operation_policy`.
28. **Подсказка про забытые селекторы считается и когда кейсов-кандидатов не было** (2026-09-06):
    JSON-модель на HTML-странице отсеивается по медиа ещё до разбора, поэтому подсказка читает
    модели кейсов, подходящих по статусу, а не только реально разобранные.
29. **`request()`, `evolve()` и `send()` типизированы через `HttpOperation[T]`**, а не через сам
    класс операции (2026-09-06). Измерено: третий параметр типа (`Desc[P, T, S]`) делает `T`
    невыводимым — mypy даёт `Need type annotation` и `Any` вместо результата. Класс доступен в
    рантайме (`Api.op.Operation`, `isinstance`), фикстура 50.3.3 проверяет
    `assert_type(..., HttpOperation[Order])` и `assert_type(send(...), Order)`. По той же причине
    `options=` у декорированной операции остаётся рантайм-параметром: синтезированный конструктор
    его не называет.
30. **`ModelAdapterRegistry.evolve` работает по значению**, поэтому `TypedDict` (обычный `dict`
    в рантайме) эволюционируется через адаптер, выбранный по типу (2026-09-06). Операции после
    фазы 50 — не `TypedDict`, так что на публичном пути это не встречается.
