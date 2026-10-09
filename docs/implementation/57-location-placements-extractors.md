# Фаза 57. `Location`, размещения на модели сессии, экстракторы не-JSON

Статус: план утверждён 2026-10-09 (владелец: «обязательность на 3xx оставляем, начинай с
57.1»); 57.1–57.4 сделаны, см. §9. Gates — в
`STATUS.md`. Зависит от фазы 53 (кейсы ответа, `when=`, арбитраж), фазы 54 (`Const`/`Payload`,
проверки при импорте) и фазы 50 (маркеры-`Annotated`). Один релиз: `0.2.0a10`.

Тип документа: authoritative implementation plan для Фазы 57. Источник — боевое испытание
`armgs-sdk` (`C:\Users\user\Desktop\armgs-sdk`, разбор в его `docs/eazy-auth-proposals.md` и
`docs/eazy-usage-audit.md`) и обзор Hurl, Arazzo, Airbyte low-code, Scrapy, Step Functions,
URLPattern. Решения владельца и отвергнутые варианты — в §10.

Мотивирующий сервис: вход ARMGS (движок Mail.ru octavius). Исход входа сообщается редиректом:
`302` с `Location` на почту, на `cgi-bin/secstep`, с `fail=`, с `errno=25`. Сессия после входа
раскладывается в три места запроса сразу: `token` и `email` в query, набор cookies в `Cookie`.
Токен лежит в JS-блобе HTML-страницы.

---

## 0. Как исполнять этот план автономно

1. **Возобновление.** Первое действие новой сессии: прочитать §9 (журнал) и раздел `## Phase 57`
   в `STATUS.md`. Первый шаг со статусом, отличным от `done`, — текущий.
2. **Порядок шагов фиксирован**: 57.1 → 57.2 → 57.3 → 57.4 → 57.5. 57.3 от 57.1–57.2 не зависит
   кодом, но идёт после них, чтобы приёмочный пример 57.5 собирался из готовых частей.
3. **Каждый шаг — отдельный коммит** `Phase 57.N: <что сделано>` с тестами внутри.
4. **Один путь исполнения.** `Location` не новый вид кейса и не новый параметр `Http.*`: это
   маркер поля и предикат для уже существующих `success=`/`errors=`/`when=`. `Placed` не вторая
   схема рядом с `Bearer`: `Bearer` становится частным случаем того же механизма.
5. **Проверки — при объявлении.** Всё, что видно по классу модели и по кейсам, проверяется при
   импорте (`normalize_responses` / `validate_responses`, `session_scheme`), а не при первом
   ответе.
6. **Совместимость моделей.** Маркеры ответа обязаны работать на четырёх бэкендах: dataclass,
   Pydantic, msgspec, TypedDict; тесты параметризованы по каждому (как I7 фазы 54).
7. **Транспорт не трогаем.** Хэндлеры остаются `RedirectControl.FORCED_OFF` и без cookie-jar.
   Редиректы ведёт ядро (`decide_response`, бюджет `Resilience(max_redirects=...)`); фаза меняет в
   нём одно правило — §3.4a.
8. **При противоречии** между планом и кодом — записать в §9, выбрать вариант без второго пути,
   продолжить. Останавливаться только если меняется публичный контракт из §3.

---

## 1. Что не так

### 1.1. Исход в `Location` нечем объявить

`eazy_sdk.response.match.header("location")` умеет `present / is_ / contains` по сырой строке.
Нет разбора на хост, путь и query, нет разрешения относительного адреса, значение параметра
достать нечем. `FromHeader` читается только поверх JSON-объекта (`headers.py`: «requires a JSON
object body»), а у `302` тело — HTML-заглушка или пусто. В `armgs-sdk` из-за этого появился
`auth/flow.py`: enum исходов и каскад `if marker in url.lower()`.

До 57.1 объявить кейс на `302` было нельзя вовсе: `decide_response` уводил ответ с `Location` в
переход раньше, чем на него смотрели кейсы, а при бюджете редиректов 0 (значение по умолчанию)
вызов заканчивался `RedirectLimitError`. Исходная редакция плана этого не учитывала (§9).

### 1.2. Жизненный цикл сессии есть только у схемы с одним `Bearer`

`_SessionModel.compile` требует ровно одно поле `Bearer` (`session_runtime.py`), а `configure()`
есть только у `SessionScheme`. Для сессии, которая кладётся в несколько мест, остаётся сырой
`AuthScheme(... AuthPlacement(...), ...)`, и лайфсайкл к нему публично не привязать. В
`armgs-sdk` `auth/binding.py` повторяет `_build_session_auth` и зовёт приватный `Auth._bind`.

### 1.3. Набор cookies нельзя объявить размещением

`AuthLocation.COOKIE` кладёт одну cookie с известным именем. Набор с именами из рантайма
приходится склеивать в строку и ставить заголовком `Cookie` руками, без проверки имён и значений.
При этом в ядре уже есть слот под набор: `ManagedCookieSetDescriptor` (`request/prepared.py`,
используется защитами).

### 1.4. Документ читается только CSS и XPath

Значение из JS-блоба страницы селектором не достать, а `ParselBackend` объявляет только `css` и
`xpath`. Поле документа без селектора — ошибка компиляции схемы, поэтому смешать в одной модели
селекторы и `FromHeader` тоже нельзя.

---

## 2. Инварианты

- **I1.** `Location` утверждает факт об ответе, а не исход вызова: читается одинаково в
  `success=` и в `errors=` и не инвертируется (как I1 фазы 54).
- **I2.** Все части `Location` необязательны. `Location()` без аргументов значит «заголовок есть».
  Хост не нужен нигде: шаблон из одного пути или одного параметра полноценен.
- **I3.** Несовпадение `Location` — `NoMatch`, а не `Malformed`: ответ не сломан, он про другой
  случай, арбитраж продолжается. Это отличает его от `FromHeader`, чей пропуск остаётся ошибкой.
- **I4.** `Location` на поле — четвёртое написание критерия рядом с `when=`, `accept=` и тегами:
  ранг в `_specificity` тот же, отдельной ступени нет (I3 фазы 53).
- **I5.** Кейс на статусе из 3xx обязан иметь критерий. Объявление, которое забрало бы любой
  редирект, — ошибка при импорте. Явное «любой» пишется `Location()`.
- **I6.** Относительный `Location` разрешается от `NormalizedResponse.url` до сравнения и до
  записи в поле: модель всегда получает абсолютный адрес.
- **I7.** Сессия пригодна, только если каждое размещаемое значение непусто. Отдельный
  `validate=` для этого не нужен.
- **I8.** `Bearer()` и `Placed.header("Authorization", prefix="Bearer ")` дают одну и ту же
  схему. Второго компилятора модели сессии нет.
- **I9.** Чтение аннотаций кешируется по типу в том же `declaration_of` (I6 фазы 54).

---

## 3. Архитектура

### 3.1. Модули

| Модуль | Что меняется |
|---|---|
| `eazy_sdk/response/location.py` (новый) | `Location` (шаблон, предикат, маркер поля), `Location.query(name)`, разбор и разрешение адреса, глоб |
| `eazy_sdk/response/sources.py` (новый) | общее семейство «источник поля из ответа»: `+FromCookie`, `apply_response_sources` вместо `_apply_header_sources`, `is_header_model` |
| `eazy_sdk/response/headers.py` | `Headers`, `ResponseHeader`, `FromHeader` остаются на месте; `_apply_header_sources` удалён |
| `eazy_sdk/response/cases.py` | представление `HeaderModel[T]`; ветка в `_CaseReading.read`; `_criterion_of` видит `Location` |
| `eazy_sdk/response/markers.py` | `ModelDeclaration` дополняется источниками и критериями `Location` |
| `eazy_sdk/response/_mapping.py` | вывод `HeaderModel` в `representation()`; D-57-01…04 в `validate_responses`; `has_criterion` |
| `eazy_sdk/response/match.py` | `_label_of` читает `.label` у любого объекта, чтобы `Location` сочетался с `&`, `\|`, `~` |
| `eazy_sdk/auth/session.py`, `session_runtime.py` | `Placed`; `_SessionModel` обобщён до списка размещений; правило I7 |
| `eazy_sdk/auth/core.py` | размещение-набор cookies через `ManagedCookieSetDescriptor` |
| `plugins/html/eazy_sdk_html/schema.py` | селектор `Regex`; поле с источником ответа не требует селектора |
| `eazy_sdk/clients/_decisions.py`, `executor.py` | редирект на статусе, на который операция объявила кейс, не ведётся ядром (§3.4a) |
| `response/__init__.py`, `auth/__init__.py` | `+Location`, `+HeaderModel`, `+FromCookie`, `+Placed`; корень `eazy_sdk` — см. §10, открытый вопрос |

### 3.2. `Location`: форма объявления

```python
from typing import Annotated
from eazy_sdk import Http, HttpOperation
from eazy_sdk.response import ApiError, Const, Location


@dataclass(frozen=True, slots=True)
class SignedIn:
    url: Annotated[str, Location(path="/inbox*")]

@dataclass(frozen=True, slots=True)
class NeedsCode:
    url: Annotated[str, Location(path="/cgi-bin/secstep*")]

@dataclass(frozen=True, slots=True)
class Banned:
    errno: Annotated[str, Location.query("errno"), Const("25")]

@dataclass(frozen=True, slots=True)
class Rejected:
    fail: Annotated[str, Location.query("fail")]


class AccountBanned(ApiError[Banned]): ...
class InvalidCredentials(ApiError[Rejected]): ...


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCredentials(HttpOperation[SignedIn | NeedsCode]):
    __http__ = Http.post(
        "/cgi-bin/auth",
        security=None,
        success={302: [SignedIn, NeedsCode]},
        errors={302: [AccountBanned, InvalidCredentials]},
    )
```

Передавать никуда нового не нужно: формы `{статус: [Модель, ...]}` и
`{статус: [ApiError[Модель], ...]}` уже разбирает `_mapping.py`.

Сигнатура:

```python
Location(
    *,
    host: str | re.Pattern[str] | None = None,
    path: str | re.Pattern[str] | None = None,
    query: Mapping[str, str | re.Pattern[str] | EllipsisType] | None = None,
    contains: str | None = None,
)
Location.query(name: str)      # источник: значение одного параметра
```

`Location` — обычный класс со `__slots__`, а не dataclass: имя `query` занято и аргументом
конструктора, и методом-источником, поэтому аргументы хранятся в приватных слотах. Равенство,
хеш и `repr` пишутся руками; `label` — как у `Predicate`.

### 3.3. `Location`: сравнение

| Часть | С чем сравнивается | Правила |
|---|---|---|
| `host` | `urlsplit(...).hostname` | без порта, без учёта регистра |
| `path` | `urlsplit(...).path`, пустой читается как `/` | как пришёл, без декодирования; регистр важен |
| `query` | `parse_qs(..., keep_blank_values=True)` | `...` — параметр есть; строка — равна одному из значений; шаблон — `fullmatch` по одному из значений |
| `contains` | весь разрешённый адрес | подстрока, регистр важен (как `header(...).contains`) |

Строка в `host` и `path` — глоб, в котором особенный только `*` (любая последовательность,
включая `/`); остальное буквально. Глоб пишется своим переводом в регулярное выражение через
`re.escape`, не `fnmatch`: там особенные ещё `?` и `[`, которые в путях встречаются буквально.
`re.Pattern` сравнивается через `fullmatch`.

Заголовок читается через `Headers.getall("location")`: нет строки или строк больше одной —
несовпадение. Указанные части соединяются по «и». «Или» пишется двумя кейсами либо
`Location(...) | Location(...)` в `when=`.

### 3.4. `Location`: три места применения

1. **Маркер поля.** `Annotated[str, Location(...)]` — поле получает абсолютный адрес, шаблон
   служит критерием. `Annotated[str, Location.query("x")]` — поле получает первое значение
   параметра; `list[str]` — все значения. Поле, тип которого не допускает `None`, — критерий:
   без значения это `NoMatch`. Поле с типом `... | None` критерием не является: оно заполняется,
   когда цель подходит, и остаётся пустым, когда нет. Умолчание поля на это не влияет.
2. **Предикат.** `Location(...)` вызывается как `ResponseCondition`, годится в `when=` любого
   представления и сочетается с `Predicate` через `&`, `|`, `~`.
3. **Проверка значения.** Равенство параметра константе пишется существующим `Const`, как в
   `Banned` выше; своего сравнения `Location.query` не заводит.

Замечание для документации: `Empty(when=Location(...))` на `302` с непустым телом даст
`Malformed` («expected empty body») — это существующее поведение `Empty`. Когда значение не
нужно, пишут `Bytes(when=Location(...))` или модель с полем.

### 3.4a. Кто читает редирект

Ядро ведёт редиректы само: `decide_response` на `301/302/303/307/308` с `Location` возвращает
переход, пока есть бюджет `Resilience(max_redirects=...)`, а без бюджета поднимает
`RedirectLimitError`. Правило фазы: **если операция объявила кейс на этот статус — точным кодом
или диапазоном — ответ читает операция, ядро его не ведёт**, каким бы ни был бюджет.
`fallback=` (`DEFAULT`) статус не объявляет: он описывает то, что не забрал никто, а редирект,
который ядро ещё может провести, до этого не дошёл.

Следствия:

- редирект, на статус которого кейс объявлен, но цель никто не описал, — `UnexpectedResponseError`,
  а не переход;
- операция без кейсов на 3xx ведёт себя как раньше;
- кейс уровня сервиса на `"3xx"` выключает ведение редиректов для всего сервиса — это и значит
  «объявить».

Реализация: `Responses.declares(status)`, поле `ResponseDecisionInput.redirect_declared`.

### 3.5. `HeaderModel`: модель, читаемая без тела

Модель, у которой **каждое** поле несёт источник из ответа (`Location`, `Location.query`,
`FromHeader`, `FromCookie`) и ни одно не несёт селектор, читается представлением
`HeaderModel[T]`: тело игнорируется, `media_type=None`. Вывод делает `representation()` в
`_mapping.py`, порядок такой: документ (есть селекторы) → `HeaderModel` (все поля из ответа) →
JSON. Явная форма `HeaderModel(Model, when=..., accept=...)` существует по той же причине, что
`Json(...)` и `Html(...)`.

Модель, где источники из ответа стоят рядом с обычными полями, остаётся JSON-моделью (как
сейчас с `FromHeader`): `201` с телом и `Location` на созданный ресурс объявляется одной моделью.

В `_CaseReading.read` ветка `HeaderModel` начинает с пустого отображения, дальше идёт общий путь:
`apply_response_sources` → `models.load` → `_decide_parsed` (теги, `accept`, `Payload`).
`apply_response_sources` возвращает `NoMatch`, если не выполнен критерий `Location`, и по-прежнему
поднимает `HeaderValidationError` на пропущенном обязательном `FromHeader`.

### 3.6. `FromCookie`

`FromCookie("act")` — значение cookie из `Set-Cookie` **этого** ответа
(`ResponseContext.cookies`). Несколько строк с одним именем — берётся последняя: так поступает
браузер. Пропуск обязательного поля — ошибка разбора, как у `FromHeader`; критерием `FromCookie`
не является. Атрибуты cookie (срок, домен) остаются делом `parse_session_cookie`.

### 3.7. `Placed`: размещения на модели сессии

```python
from eazy_sdk.auth import Placed, session_scheme

class ArmgsSession(BaseModel):
    token: Annotated[str, Placed.query("token")]
    email: Annotated[str, Placed.query("email", secret=False)]
    cookies: Annotated[dict[str, str], Placed.cookies()]
    login: str = ""

ARMGS_SESSION = session_scheme(ArmgsSession, name="armgs-session")
auth = ARMGS_SESSION.configure(credentials=login, service=ArmgsLoginService())
```

| Маркер | Куда | Тип поля |
|---|---|---|
| `Placed.query(name, *, secret=True)` | query-параметр | скаляр, `SecretStr` |
| `Placed.header(name, *, prefix="", secret=True)` | заголовок | скаляр, `SecretStr` |
| `Placed.cookie(name)` | одна cookie | скаляр, `SecretStr` |
| `Placed.cookies()` | набор cookies | `Mapping[str, str]` |

`_SessionModel` вместо `bearer_field` и `bearer` хранит кортеж размещений; `Bearer(header,
prefix)` переводится в то же размещение, что `Placed.header(header, prefix=prefix)`.
`_SessionModel.scheme()` строит `AuthScheme` из всего кортежа. `configure()`, проверка «ровно одно
из `credentials` и `session`», хранилище и `refresh` уже есть в `_build_session_auth` и не
меняются. `session_auth(...)` и `generated_session_*` продолжают работать через тот же
`_SessionModel`.

`_SessionModel.is_valid` дополняется I7: пустая строка, пустое отображение или `None` в любом
размещаемом поле делают сессию непригодной; `ExpiresAt` проверяется как раньше.

`Placed.cookies()` требует **пробы до реализации** (57.3, первый пункт): слот под набор сейчас
заводит только компилятор защит (`http_compiler.py`, `PrivateCookieSetBinding`), а размещения
схемы ищут слот по имени в `compiled.cookie_slots` (`auth/core.py`, `_binding_operations`). Нужно
выяснить, где компилятор заводит слоты под размещения схемы, и завести там слот с
`ManagedCookieSetDescriptor`. Если проба покажет, что без второго пути не обойтись, — записать в
§9 и остановиться на этом пункте: меняется контракт §3.7.

### 3.8. `Regex` и источники ответа в документе

```python
from eazy_sdk_html import CSS, Regex

class MailPage(BaseModel):
    title: Annotated[str, CSS("title::text")]
    token: Annotated[str, Regex(r'/api/v1/user/short.*?"token":"([^"]+)"', flags=re.S)]
    csrf: Annotated[str | None, Regex(r'"authCSRFToken":"([^"]+)"')] = None
    act: Annotated[str | None, FromCookie("act")] = None
```

`Regex(pattern, flags=0)` — третий язык селекторов, `language = "regex"`. Шаблон компилируется
в `__post_init__`; ноль групп — берётся всё совпадение, одна — группа, больше одной — ошибка
объявления. Скалярное поле получает **первое** совпадение, поле-список — все: текст, в отличие от
разметки, повторяется, и требование «ровно одно совпадение» сделало бы селектор бесполезным.

`ParselBackend.selector_languages` получает `"regex"`. Поиск идёт по тексту документа **как он
пришёл**, а не по разметке, пересобранной парсером: корневой `ParselNode` хранит исходный текст.
Сущности не раскрываются, скрипты не разбираются. Под `Scope` поиск идёт по разметке своего
узла. Сам `Scope` регулярным выражением не задаётся. Бэкенд, не знающий `regex`, отклоняет
модель уже существующей проверкой `_check_selector_languages`.

`_compile_model` перестаёт требовать селектор у поля, несущего источник из ответа: такое поле в
схему извлечения не попадает, его заполняет общий `apply_response_sources` после извлечения
(тот же шаг, что уже применяется к `Html` в `_CaseReading.read`). `has_required_field` такие поля
не учитывает: отличить одну страницу от другой может только то, что читается из тела.

### 3.9. Диагностики

Все — `PlanError` при импорте, если не сказано иное.

| Код | Когда | Текст по смыслу |
|---|---|---|
| D-57-01 | кейс на статусе или диапазоне внутри 300–399 без критерия (`has_criterion` ложно) | «кейс заберёт любой редирект; добавьте `Location(...)` на поле модели или `when=Location()`» |
| D-57-02 | в модели больше одного поля с шаблоном `Location(...)` | «шаблон один на модель; остальные части берите `Location.query(...)`» |
| D-57-03 | тип поля под `Location` не `str`, `list[str]` (только для `query`) или их `\| None` | назвать поле и допустимые типы |
| D-57-04 | пустое имя в `Location.query("")`; ключ или значение `query=` не того типа | назвать аргумент |
| D-57-05 | модель сессии без единого размещения; больше одного `Bearer`; два размещения в одно место с одним именем; два `Placed.cookies()` | `SessionConfigurationError`, назвать поля |
| D-57-06 | `Placed.cookies()` на поле не `Mapping[str, str]`; `Placed.*` на несериализуемом типе | `SessionConfigurationError` |
| D-57-07 | `Regex` с более чем одной группой; `Regex` под `Scope` | `ExtractionCompileError` |

В рантайме нового нет: ответ, не подошедший ни под один кейс, — существующий
`UnexpectedResponseError`, два подошедших с равным рангом — `AmbiguousResponseError`. В
`attempted` попадают имена моделей `HeaderModel`, в подсказке — `label` шаблонов.

---

## 4. Задачи

### 4.1. 57.1 — `Location` как предикат

- `response/location.py`: класс, глоб, разбор адреса, разрешение относительного, `label`, `&`,
  `|`, `~`.
- `match.py`: `_label_of` читает `.label` у любого объекта.
- `_decisions.py`, `executor.py`, `cases.py`: правило §3.4a.
- Экспорт из `eazy_sdk.response`.
- Тесты §5.1.

### 4.2. 57.2 — источники ответа, `HeaderModel`, критерий на поле

- `response/sources.py`: перенос `FromHeader`, `FromCookie`, `apply_response_sources`.
- `markers.py`: `ModelDeclaration` читает источники и критерии `Location`, кеш тот же.
- `cases.py`: `HeaderModel`, ветка чтения, `NoMatch` из источников, `_criterion_of`.
- `_mapping.py`: вывод представления, `has_criterion`, D-57-01…04.
- Проверить `tests/integration/test_redirects.py` и остальные тесты с кейсами на 3xx: объявления
  без критерия переводятся на `when=Location()`; сколько их оказалось — записать в §9.
- Тесты §5.2.

### 4.3. 57.3 — `Placed`

- Проба `Placed.cookies()` (§3.7), результат — в §9.
- `Placed`, обобщение `_SessionModel`, I7, D-57-05…06.
- `Bearer` поверх того же механизма; существующие тесты сессий проходят без правок.
- Тесты §5.3.

### 4.4. 57.4 — `Regex` и смешанные документы

- `Regex` в `eazy_sdk_html`, `ParselBackend`, D-57-07.
- `_compile_model` пропускает поля с источником ответа.
- Тесты §5.4.

### 4.5. 57.5 — пример, документация, выпуск

- `examples/redirect_login.py`: учебный сервис на `httpx.MockTransport` с четырьмя исходами входа
  в `Location`, сессией в двух query-параметрах и наборе cookies, страницей с токеном в JS-блобе.
  Оркестрация шагов — обычный `AuthService.acquire`, cookies между шагами передаются явно.
- Страницы документации (§6), `CHANGELOG.md`, снапшот публичного API
  (`tests/rewrite/test_phase14_public_api.py`).
- Выпуск `0.2.0a10` по процедуре выпуска.

---

## 5. Тесты

### 5.1. `Location`-предикат (`tests/unit/response/test_location.py`)

- каждая часть отдельно и в сочетании; `Location()` — только наличие заголовка;
- глоб: `*` в начале, в середине, в конце; `?` и `[` буквально; `re.Pattern` через `fullmatch`;
- хост: регистр, порт, IDN как пришёл;
- относительный `Location` (`/inbox`, `../x`, `?fail=1`) разрешается от адреса запроса;
- query: `...`, строка, шаблон, пустое значение, повторяющийся параметр;
- нет заголовка, две строки заголовка — ложь, без исключения;
- `&`, `|`, `~` с `Predicate` и с лямбдой; `label` читается в диагностике;
- §3.4a: объявленный редирект читается и не ведётся даже при бюджете; необъявленный ведётся;
  `fallback=` редирект не забирает.

### 5.2. `HeaderModel` и критерий (`tests/unit/response/test_header_model.py`, параметризация по четырём бэкендам)

- вывод представления: все поля из ответа → `HeaderModel`; смесь с обычными → JSON; с
  селекторами → документ;
- четыре исхода на одном `302`: два успеха и две ошибки, каждый выбирается своим `Location`;
- `Location.query` + `Const`: совпало, не совпало, параметра нет;
- непустое тело у `302` не мешает;
- ни один кейс не подошёл → `UnexpectedResponseError`; два подошли → `AmbiguousResponseError`;
- `fallback=` получает редирект, который отвергли все кейсы (порядок фазы 54.1);
- критерий `Location` ранжируется выше кейса без критерия на том же статусе;
- `FromCookie`: значение, последняя из одноимённых, пропуск обязательного — `Malformed`;
- `201` с JSON-телом и `Location` в одной модели;
- D-57-01…04 срабатывают при импорте, запрос не уходит.

### 5.3. `Placed` (`tests/unit/auth/test_placed.py`, `tests/integration/auth/`)

- два query и набор cookies уходят в запрос; секретные значения скрыты в логах и `repr`;
- `configure(credentials=...)` запускает `acquire` на первом защищённом вызове; `refresh` на 401;
- `Bearer()` и `Placed.header("Authorization", prefix="Bearer ")` дают равные размещения;
- I7: пустой токен, пустой набор cookies → сессия непригодна, идёт повторный вход;
- `SecretStr` раскрывается при размещении;
- D-57-05…06; существующие тесты `session_scheme`/`session_auth`/`session_cookie` зелёные без правок.

### 5.4. `Regex` (`plugins/html/tests/`)

- ноль групп, одна группа, флаги; сущности не раскрываются;
- обязательное поле без совпадения — модель не читается, кейс уступает другому;
- поле с `FromCookie`/`FromHeader` в документе заполняется и не считается в `has_required_field`;
- бэкенд без `regex` отклоняет модель при объявлении; D-57-07.

### 5.5. Приёмка

`examples/redirect_login.py` исполняется тестом примеров документации; в журнале вызовов
учебного сервиса — ровно ожидаемая последовательность запросов.

---

## 6. Документация

- Справочник ответов: раздел «Редиректы и `Location`» с разбором по частям шаблона и таблицей
  сравнения из §3.3; заметка про `Empty` из §3.4.
- Справочник авторизации: «Сессия в нескольких местах запроса» — `Placed`, I7, связь с `Bearer`.
- Справочник HTML: `Regex`, источники ответа в документе.
- Рецепт «Вход через редиректы» на `examples/redirect_login.py`: все исходы по причинам, по
  правилам примеров (copy-paste runnable, встроенный учебный сервис).
- `sdk-reference.md` и `sdk-authoring-reference.md`: новые имена.

---

## 7. Exit criteria

### 7.1. Приёмка на `armgs-sdk`

Проверяется на ветке `armgs-sdk`, вне этого репозитория; в `STATUS.md` записывается результат.

1. `auth/flow.py` удалён: исходы входа — модели с `Location` в `success=`/`errors=` операции.
2. `auth/binding.py` удалён, приватных импортов `eazy_sdk.auth.core` / `session_runtime` и вызова
   `Auth._bind` в SDK нет.
3. `security.py` — одна строка `session_scheme(ArmgsSession, ...)`; `_validate_session` и
   `ArmgsSession.cookie_header` удалены.
4. `auth/extract.py` — модель страницы почты; регулярные выражения остались только в `Regex(...)`.
5. Живой вход с 2FA и чтение ящика проходят (`examples/login.py`, `examples/read_inbox.py`).

### 7.2. Библиотека

1. Тесты §5 существуют и зелёные.
2. Каждая диагностика §3.9 срабатывает до отправки запроса.
3. Ни одного изменения в `eazy_sdk/handlers/`.
4. `Bearer`-сессии работают без правок в тестах и примерах.
5. Публичные имена добавлены в снапшот API и в справочники.

---

## 8. Гейты

- `uv run pytest -q`
- `uv run mypy`
- `uv run ruff check`
- `uv run python scripts/docs_freshness.py check`
- сборка сайта документации (Sphinx, с фазы 56)
- сборка пакета и проверка изолированной установки колеса с extras `html`
- import-linter: ядро не узнаёт о плагинах (`Regex` живёт в `eazy_sdk_html`)

---

## 9. Журнал и отклонения

| Дата | Шаг | Запись |
|---|---|---|
| 2026-10-09 | — | План написан. Код не менялся, проверки не запускались. Шаг 57.1 не начат. |
| 2026-10-09 | 57.1 | **Отклонение от исходной редакции.** План утверждал, что `302` доходит до операции как обычный ответ. Это неверно: редиректы ведёт ядро (`decide_response`), и при бюджете 0 ответ с `Location` заканчивался `RedirectLimitError` до просмотра кейсов. Добавлено правило §3.4a; §0.7, §1.1, §3.1, §11 исправлены. |
| 2026-10-09 | 57.1 | **Отклонение.** `Location` экспортирован только из `eazy_sdk.response`: в корне `eazy_sdk` ровно 40 имён, а `test_phase14_public_api` и `test_phase52_pagination` держат бюджет `<= 40`. Поднимать бюджет — решение владельца (§10). |
| 2026-10-09 | 57.1 | В блоках §3.7 и §3.8 убраны строки импорта ещё не существующих `Placed` и `Regex`: `test_documented_eazy_sdk_imports_resolve_to_real_symbols` проверяет python-блоки планов. Вернуть в 57.3 и 57.4. |
| 2026-10-09 | 57.4 | **Отклонение.** `Regex` ищет своим `re.finditer` по исходному тексту, а не через `selector.re(...)` parsel: тот работает по разметке, пересобранной lxml, и раскрывает сущности. Скалярное поле берёт первое совпадение (§3.8 переписан). |
| 2026-10-09 | 57.4 | done. `Regex`, источники ответа в документе, D-57-07; `plugins/html/tests/test_phase57_regex.py` (19 тестов). `uv run mypy` — 509 файлов без замечаний; `uv run ruff check` — чисто. `uv run pytest -q`: первый прогон — 1866 passed, 11 skipped, 3 failed (`test_appending_query_preserves_existing_and_new_pair_order`, `test_live_driver_contract[pydoll]`, `test_driver_detaches_itself_when_the_page_closes`); все три проходят отдельно, пример, найденный hypothesis, при прямом воспроизведении проходит за 0,7 мс; повторный полный прогон — 1869 passed, 11 skipped. Код этих тестов фаза не трогает. Импорт `Regex` в блок §3.8 возвращён. |
| 2026-10-09 | 57.3 | Проба `Placed.cookies()` прошла без второго пути: слоты размещений схемы заводит `http_compiler` (цикл по `scheme.placements`), для набора там же заводится cookie-слот с `ManagedCookieSetDescriptor`, дальше работает существующий `_cookies`. Имя слота — `auth_cookie_set_name(<имя схемы>)` в `core/http.py`; у `AuthPlacement` появилось поле `many`. |
| 2026-10-09 | 57.3 | **Попутное изменение.** Слот размещения теперь берёт `secret` из `AuthPlacement.secret`; раньше компилятор ставил `secret=True` всегда, и поле `secret=False` у сырого `AuthPlacement` ни на что не влияло. Полный прогон это не задело. |
| 2026-10-09 | 57.3 | Сообщение об отсутствии размещений изменено: «session model places nothing into the request: declare exactly one Bearer field, or Placed...». Для двух `Bearer` текст прежний. |
| 2026-10-09 | 57.3 | done. `Placed`, обобщённый `_SessionModel`, I7, D-57-05…06; `tests/unit/test_phase57_placed.py` (19 тестов). `uv run pytest -q` — 1850 passed, 11 skipped; `uv run mypy` — 508 файлов без замечаний; `uv run ruff check` — чисто. Импорт `Placed` в блок §3.7 возвращён. |
| 2026-10-09 | 57.2 | **Отклонение.** Критерием считается поле, тип которого не допускает `None`; умолчание не учитывается (§3.4 исправлен). Причина: `declaration_of` читает модель по аннотациям без реестра адаптеров и умолчаний не видит, а правило должно быть одним и для ранга, и для чтения. |
| 2026-10-09 | 57.2 | **Отклонение.** `FromHeader` не переезжал: остался в `headers.py`, `sources.py` его импортирует. Перенос дал бы только лишний диф. |
| 2026-10-09 | 57.2 | Кейсов на 3xx без критерия в существующих тестах не нашлось: до 57.1 объявить такой кейс было нельзя, так что D-57-01 ничего не ломает. |
| 2026-10-09 | 57.2 | done. `sources.py`, `HeaderModel`, `LocationQuery`, `criteria_of`, D-57-01…04; `tests/unit/test_phase57_header_model.py` (31 тест, четыре бэкенда). `uv run pytest -q` — 1831 passed, 11 skipped; `uv run mypy` — 507 файлов без замечаний; `uv run ruff check` — чисто. |
| 2026-10-09 | 57.1 | done. `eazy_sdk/response/location.py`, `Responses.declares`, `redirect_declared`; `tests/unit/test_phase57_location.py` (47 тестов). Гейты — в `STATUS.md`. `Location.query(...)` как источник поля отложен в 57.2, где он начинает читаться. |

---

## 10. Решения и отвергнутые варианты

**Решения владельца (2026-10-09):**

- имя — `Location`;
- хост и остальные части шаблона необязательны;
- три пункта (`Location`, размещения сессии, экстракторы) выходят одним релизом.

**Отвергнуто:**

- **Новый вид кейса `Redirect(to=..., as_=...)` в списках `success=[...]`.** Вторая форма
  объявления рядом со словарём по статусу и параметр `as_`, которого больше нигде нет. Маркер на
  поле модели повторяет `Const`, `CSS` и `FromHeader`.
- **Отдельный строчный `location` в `eazy_sdk.response.match` рядом с классом.** Два имени для
  одного: `Location(...)` сам вызывается как предикат.
- **Fluent-билдер `SessionScheme.of(Model).query(...).header(...)`.** Модель сессии уже несёт
  `Bearer`, `RefreshToken`, `ExpiresAt` на полях; билдер стал бы вторым местом, где описана та же
  модель.
- **`all_of(...)` с общим credential.** `all_of` соединяет независимые схемы; общий источник
  потребовал бы режима проекции и второго смысла у существующей функции.
- **Ограничить `Location` статусами 3xx.** Заголовок законен на `201` и `202`.
- **`fnmatch` для глоба.** `?` и `[` в путях встречаются буквально.
- **Упорядоченное «первое совпадение побеждает».** В библиотеке порядок объявления не значит
  ничего, спор решает ранг, равный ранг — ошибка. Исключение для редиректов стало бы вторым
  правилом арбитража.
- **Язык выражений для ветвления шагов** (как Jinja у Airbyte, Filtrex у Step CI). Теряется
  типизация; оркестрация шагов входа остаётся кодом в `AuthService.acquire`.

- обязательность критерия на 3xx (I5, D-57-01) остаётся (2026-10-09).

**Открыто, решает владелец до 57.5:** экспорт `Location` из корня `eazy_sdk`. Бюджет корня — 40
имён, занят полностью. Либо бюджет поднимается до 41 в двух тестах, либо `Location` остаётся в
`eazy_sdk.response` рядом с `Const`, `ApiError` и представлениями.

---

## 11. Что идёт следом (не в этой фазе)

- **Cookie-jar между операциями и хопами и meta-refresh.** HTTP-редиректы ядро уже ведёт
  (`Resilience(max_redirects=...)`), но cookies, поставленные промежуточным ответом, в следующий
  хоп не попадают, а клиентский переход (meta-refresh) не распознаётся. До этого cookies между
  шагами входа передаются явно, а переход через `/sdc` в `armgs-sdk` остаётся на собственной
  сессии.
- **Цепочка источников с запасным вариантом** (`FromCookie("act") | Regex(...)`): требует
  порядка вычисления между семействами «источник ответа» и «селектор документа».
- **`url.host / url.path / url.query` в `eazy_sdk_browser.conditions`** тем же словарём, что
  `Location`.
- **Кодоген**: вывод `Location` из OpenAPI `headers` и расширений.
