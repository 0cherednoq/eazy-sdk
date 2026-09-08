# Фаза 53. Кейсы ответа: `when=` везде, условие раньше статуса, конверт на модели

Статус: план утверждён 2026-09-08 (владелец: «Envelope в response, accept= оставляем, порядок
арбитража меняем»). Gates — в `STATUS.md`. Зависит от фазы 50 (кейсы ответа, `success=`/`errors=`,
`Responses.inspect`) и фазы 52 (`__pages__` читает результат операции, который здесь меняется).

Тип документа: authoritative implementation plan для Фазы 53. Источники: два issue в `docs/` —
`eazy-sdk-v0.2.0a7-response-cases-api-issue.md` (универсальный `when=`, composable-предикаты,
диагностика shadowing) и `eazy-sdk-v0.2.0a7-accept-post-parse-issue.md` (решение по разобранному
значению). Отклонения от них и решения владельца — в §10.

Мотивирующий SDK — `kad.arbitr.ru` (`C:/Users/user/Desktop/parsing/kad`): эндпоинт на 200 отдаёт
либо PDF, либо HTML-страницу капчи или блокировки, а JSON-ручки заворачивают полезную нагрузку в
конверт `{"Success": …, "Result": …, "Message": …}` тоже со статусом 200.

---

## 0. Как исполнять этот план автономно

1. **Возобновление.** Первое действие новой сессии: прочитать §9 (журнал) и раздел `## Phase 53`
   в `STATUS.md`. Первый шаг со статусом, отличным от `done`, — текущий.
2. **Порядок шагов фиксирован**: 53.1 → 53.2 → 53.3 → 53.4 → 53.5. Шаг 53.2 меняет поведение и
   не начинается, пока 53.1 не закрыт: без `when=` на всех представлениях у автора не будет
   способа выразить исключение из нового порядка.
3. **Каждый шаг — отдельный коммит** `Phase 53.N: <что сделано>` с тестами внутри.
4. **Один путь исполнения.** Ни одного второго механизма классификации: `accept=` и `Envelope`
   встраиваются в существующий `Responses.inspect`, а не рядом с ним.
5. **Ни одного изменения запроса.** Фаза трогает только разбор ответа.
6. **Проверки — при объявлении.** Всё, что видно по классу модели и по кейсам, проверяется при
   импорте (`op()` / `normalize_responses`), а не при первом ответе.
7. **Совместимость моделей.** Любой механизм обязан работать на всех четырёх бэкендах:
   dataclass, Pydantic, msgspec, TypedDict. Проверено (§3.4): значение `TypedDict` — обычный
   `dict`, у него нет методов, поэтому объявление на модели не может быть методом.
8. **При противоречии** между планом и кодом — записать в §9, выбрать вариант без второго пути,
   продолжить. Останавливаться только если меняется публичный контракт.

---

## 1. Что не так

### 1.1. `when=` есть не у всех представлений

Кейс концептуально — это `статус × медиа × разбор тела × предикат`. Предикат принадлежит кейсу
(`Success.condition`), но в короткой форме живёт на представлении (`Html(..., when=)`), и до
`Text`, `Bytes`, `Empty`, `Extracted`, `Parsed` его не донесли:

```python
success=Bytes(media_type=None, when=is_pdf)
# TypeError: Bytes.__init__() got an unexpected keyword argument 'when'
```

Единственный рабочий вариант — многословный объект-кейс `Success(200, Bytes(...), condition=…)`.
Словарная форма, которую документация называет основной, не покрывает всё.

### 1.2. Точный статус побеждает условие

`_specificity` (`response/cases.py:814`) сравнивает точность статуса **раньше** наличия условия:

```python
(_status_rank(case.status), _media_rank(...), 0 if case.condition is None else 1, -case.precedence)
```

Сервис объявляет защиту один раз:

```python
CHALLENGE_RESPONSE = Error(StatusRange(200, 599), Text(), exception=KadChallengeRequired,
                           condition=is_challenge)
```

Операция объявляет успех коротко: `success={200: Bytes(media_type=None)}`. Приходит страница капчи
со статусом 200, кандидатами становятся оба кейса, ранги — `(2, 0, 0, 0)` против `(1, 0, 1, -1)`,
сравнение заканчивается на первом элементе. Побеждает успех: HTML капчи молча возвращается как
байты PDF, ошибка не поднимается никогда.

Обойти это можно только навесив условие и на успех. Именно поэтому в `kad/api.py` шесть раз
написано `when=is_regular_html` и `condition=is_pdf` — это не описание контракта, а обход
арбитража.

### 1.3. Бизнес-неуспех в конверте не является ошибкой

`when=` работает до разбора, на байтах. Конверт `{"Success": false, "Result": null,
"Message": "Дело не найдено"}` со статусом 200 виден только после разбора, поэтому проверка
уезжает в код пользователя:

```python
def _document_items(response: DocumentPageResponse) -> list[KadDocument]:
    if not response.success:
        raise RuntimeError(response.message or "KAD document request was unsuccessful")
    return response.result.items
```

Следствия: `RuntimeError` не участвует в `errors=`, его не видит codegen, у него нет
`ErrorSummary` и он не проходит через guard/retry-слой; проверка дублируется в каждой точке
использования модели; транспортное поле `success` торчит в публичной модели SDK.

### 1.4. Предикаты — четыре функции ради трёх примитивов

`responses.py` в `kad` — это `startswith`, `in` и булева алгебра, написанные вручную над
`context.bytes`, с ручным `.encode()` для кириллицы.

## 2. Инварианты

- **I1.** Любое представление принимает `when=`; словарная форма выражает всё, кроме
  `precedence`, который автор никогда не пишет.
- **I2.** Условие — более сильное утверждение, чем точность статуса. Кейс с условием побеждает
  кейс без условия независимо от статуса; между двумя условными решает статус, затем медиа,
  затем слой.
- **I3.** «Условие» — это `when=` (до разбора), `accept=` (после разбора) и `Envelope.succeeds`
  на модели. Все три дают один и тот же ранг: у кейса либо есть критерий, либо нет.
- **I4.** Конверт объявляется на модели атрибутом класса `__envelope__`, а не методом: значение
  `TypedDict` — обычный `dict` (§3.4). Атрибут держит обычные вызываемые объекты, поэтому
  сложность условия ничем не ограничена.
- **I5.** `Envelope.succeeds` отвечает на вопрос «этот конверт успешен». Success-кейс совпадает,
  когда ответ истина, Error-кейс — когда ложь. Один предикат, инвертированный видом кейса,
  поэтому два зеркальных кейса на одном статусе никогда не бывают одновременно кандидатами.
- **I6.** `accept=` на кейсе отвечает на другой вопрос: «этот кейс подходит». Он используется как
  есть для обоих видов кейса и переопределяет `Envelope.succeeds`, когда задан.
- **I7.** Порядок применения к разобранному значению: `accept`/`succeeds`, затем `payload`.
  Исключение внутри любого из них — `Malformed`, а не падение вызова.
- **I8.** Публичный результат операции — то, что объявлено в `HttpOperation[T]`. `payload=`
  делает `T` полезной нагрузкой, а конверт остаётся деталью объявления.
- **I9.** Механизмы работают на всех четырёх бэкендах моделей; тест проходит по каждому.

## 3. Архитектура

### 3.1. Модули

| Модуль | Что меняется |
|---|---|
| `eazy_sdk/response/cases.py` | `when=` на `Text`/`Bytes`/`Empty`/`Extracted`/`Parsed`; `accept=` на `Json`/`Html`/`Extracted`/`Parsed`; `Envelope`; порядок в `_specificity`; применение критерия и проекции в `Responses.inspect` |
| `eazy_sdk/response/match.py` (новый) | `Predicate` и фабрики `body`, `content_type`, `status`, `header` |
| `eazy_sdk/response/_mapping.py` | чтение `accept` рядом с `when`; вывод типа результата через `Envelope.payload`; диагностики объявления |
| `eazy_sdk/response/__init__.py` | экспорт `Envelope`; `match` — отдельный модуль, не через `*` |

### 3.2. Порядок арбитража (53.2)

```python
def _specificity(case: ResponseCase[object]) -> tuple[int, int, int, int]:
    return (
        0 if _criterion_of(case) is None else 1,   # было третьим
        _status_rank(case.status),
        _media_rank(case.response.media_type),
        -case.precedence,
    )
```

`_criterion_of(case)` возвращает первое непустое из: `case.condition`, `accept` представления,
`__envelope__.succeeds` модели представления.

Что это меняет на практике:

| Ответ | До | После |
|---|---|---|
| PDF, 200, успех без условия | успех | успех |
| HTML капчи, 200, успех без условия, сервисная ошибка на диапазоне с условием | **успех, тихий баг** | ошибка `KadChallengeRequired` |
| два условных кейса, точный статус против диапазона | точный | точный |
| два безусловных кейса | точный | точный |

Цена изменения (§10, D2): операция, объявившая точный статус успехом, теперь проигрывает
сервисной ошибке с условием, если тело удовлетворяет условию. Выход — `when=` на успехе, который
после 53.1 доступен для любого представления.

### 3.3. Конверт на модели (53.3)

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Envelope[TEnvelope, TPayload]:
    """Как читать конверт сервиса: успешен ли он и где внутри полезная нагрузка."""

    succeeds: Callable[[TEnvelope], bool] | None = None
    payload: Callable[[TEnvelope], TPayload] | None = None
```

Объявляется атрибутом класса `__envelope__`. Проверено на всех бэкендах (§3.4):

```python
class DocumentPageResponse(msgspec.Struct, rename={"result": "Result", "success": "Success"}):
    result: DocumentPage
    success: bool
    message: str | None = None

    __envelope__ = Envelope(succeeds=lambda r: r.success, payload=lambda r: r.result)
```

Сложное условие — обычная функция с аннотацией, никакого языка выражений:

```python
def _kad_succeeds(envelope: KadEnvelope) -> bool:
    if not envelope.success or envelope.result is None:
        return False
    return not any(error.fatal for error in envelope.errors)
```

Объявление один раз на сервис — через общий объект или базовый класс, потому что чтение идёт
обычным поиском атрибута по MRO:

```python
KAD_ENVELOPE = Envelope(succeeds=_kad_succeeds, payload=lambda e: e.result)

class KadBase(msgspec.Struct):
    __envelope__ = KAD_ENVELOPE
```

Для `TypedDict` меняется только доступ, и это честно: значение действительно словарь.

```python
__envelope__ = Envelope(succeeds=lambda r: r["Success"], payload=lambda r: r["Result"])
```

Объявление операции не содержит дополнительных аргументов:

```python
class CaseDocumentsPage(HttpOperation[DocumentPage]):
    __http__ = Http.get(
        "/Kad/CaseDocumentsPage",
        success={200: Json(DocumentPageResponse)},
        errors={200: (Json(DocumentPageResponse), KadRequestFailed)},
    )
```

`accept=` на представлении — переопределение для модели, которую нельзя изменить (сгенерирована
из OpenAPI, пришла из чужого пакета). Отвечает на другой вопрос и потому не инвертируется:

```python
success={200: Json(ThirdPartyEnvelope, accept=lambda r: r.status == "ok")}
```

### 3.4. Измеренные факты, на которых стоит план

Проба (`scratchpad/probe_env.py`, 2026-09-08) по всем четырём бэкендам: дандер-атрибут класса
`__envelope__` объявляется в теле класса и читается через `getattr` у всех четырёх, включая
`TypedDict`; поля модели при этом не меняются, загрузка через `ModelAdapterRegistry` не ломается.

| Бэкенд | Атрибут класса | Метод на значении |
|---|---|---|
| dataclass | да | да |
| Pydantic | да | да |
| msgspec | да | да |
| TypedDict | да | **нет**: значение — `dict` |

Отсюда I4: объявление — атрибут, а не метод.

Второй факт: `_mapping.py` уже читает условие единообразно (`getattr(shape, "when", None)` в
`_success_cases` и в трёх ветках `error_case`), поэтому 53.1 не требует изменений в `_mapping`.

### 3.5. Где это применяется в `Responses.inspect`

Единственная точка — там, где модель уже загружена и значение кладётся в `matches`
(`cases.py`, ветка `isinstance(result, ParsedValue)`):

1. вычислить критерий: `accept` представления, иначе `__envelope__.succeeds` модели;
2. если критерий есть — применить: для `Success` подходит истина, для `Error` ложь, но для
   `accept=` — истина в обоих случаях (I6);
3. критерий не выполнен — кейс не кандидат: он не попадает ни в `matches`, ни в `malformed`;
4. критерий выполнен — применить `payload`, если объявлен, и положить результат в `matches`;
5. исключение внутри критерия или проекции — `Malformed` с этим исключением.

Никаких изменений в `arbitrate_cases`: если после фильтрации не осталось ничего, это обычный
`NoCaseMatch` → `UnexpectedOutcome`.

### 3.6. Тип результата

`_representation_result_type` возвращает тип полезной нагрузки, когда модель объявляет
`payload`: читается аннотация возврата через `get_type_hints`. Лямбда без аннотации ничего не
сообщает — тогда истиной остаётся `HttpOperation[T]`, который автор пишет всегда. Расхождение
между аннотацией `payload` и `T` ловится при импорте (D-53-05).

### 3.7. Предикаты `eazy_sdk.response.match` (53.4)

Один frozen-dataclass `Predicate(test, label)` с `__and__`/`__or__`/`__invert__`/`__repr__` и
несколько фабрик. Результат — обычный callable, удовлетворяющий `ResponseCondition`, поэтому
смешивается с лямбдами и существующими функциями.

```python
from eazy_sdk.response.match import body, content_type

is_pdf = body.startswith(b"%PDF-")
is_challenge = body.contains(b"pravocaptcha.execute") | body.contains(b'name="recaptchatoken"')
is_blocked = body.contains(b"support_kad@pravo.tech") | body.contains("Доступ заблокирован")
is_regular = ~(is_challenge | is_blocked) & content_type.startswith("text/html")
```

Набор: `body.startswith`, `body.contains(..., ignore_case=False)`, `body.matches(pattern)`,
`body.is_empty()`; `content_type.is_`, `content_type.startswith`; `status.is_`, `status.in_`;
`header(name).is_`, `header(name).contains`, `header(name).present()`.

Семантика типа аргумента: `bytes` сравнивается с `context.bytes`, `str` — с
`context.text.value`, и даёт `False`, если текст не декодируется. `label` попадает в `__repr__` и
в диагностику `AmbiguousResponseError`.

### 3.8. Диагностики

| Код | Текст (подстрока, которую проверяет тест) |
|---|---|
| D-53-01 | `X.__envelope__ must be an Envelope, got Y` |
| D-53-02 | `Envelope() declares neither succeeds= nor payload=` |
| D-53-03 | `accept= needs a model to read; Text/Bytes/Empty take when=` |
| D-53-04 | `operation 'op': Json(E) is declared for both success and error on status 200, and E declares no envelope rule; add Envelope(succeeds=...) or accept=` |
| D-53-05 | `operation 'op': Envelope.payload returns A, the operation returns B` |

## 4. Задачи

### 4.1. 53.1 — `when=` на всех представлениях

Поле `when: ResponseCondition | None = None` в `Text`, `Bytes`, `Empty`, `Extracted`, `Parsed`
(после существующих полей, чтобы позиционные вызовы не сломались). `_mapping.py` не меняется.
Документация: словарная форма покрывает всё, `Success`/`Error` переводятся в раздел advanced.

### 4.2. 53.2 — условие раньше статуса

`_criterion_of` и новый порядок в `_specificity`; переписать докстринг `_most_specific`, где
порядок задокументирован словами. CHANGELOG: breaking.

### 4.3. 53.3 — `Envelope` на модели и `accept=` на кейсе

`Envelope` в `cases.py` и в `__all__` пакета `response`; `accept=` на `Json`, `Html`,
`Extracted`, `Parsed`; чтение `__envelope__` с кешем по типу модели; применение по §3.5; тип
результата по §3.6; диагностики D-53-01..05.

### 4.4. 53.4 — `eazy_sdk.response.match`

Модуль по §3.7. Не экспортируется из `eazy_sdk.response` через `*`: точка входа —
`from eazy_sdk.response.match import body`.

### 4.5. 53.5 — документация и приёмка на `kad`

Страницы `guides/responses/*`: словарная форма как единственный рекомендованный стиль, новый
порядок арбитража с таблицей из §3.2, конверт на модели, предикаты. Переписать
`C:/Users/user/Desktop/parsing/kad` (`api.py`, `responses.py`, `models.py`) и проверить критерии
§7.1.

## 5. Тесты

`tests/unit/test_phase53_response_cases.py`.

**53.1**: `test_when_on_bytes_selects_pdf_over_html`, `test_when_on_text_empty_extracted_parsed`,
`test_dict_form_expresses_every_case_field`.

**53.2**: `test_conditional_range_beats_unconditional_exact_status` (сценарий §1.2 целиком: без
единого `when=` на успехе challenge даёт `KadChallengeRequired`, а PDF даёт байты),
`test_exact_status_still_wins_between_two_conditional_cases`,
`test_unconditional_cases_keep_status_order`, `test_operation_still_beats_service_on_a_full_tie`,
`test_specificity_order_is_condition_status_media_layer` (прямая проверка кортежа).

**53.3**: `test_envelope_splits_success_and_failure_on_one_status`,
`test_envelope_payload_becomes_the_operation_result`,
`test_envelope_works_on_dataclass_pydantic_msgspec_typeddict` (параметризован по бэкендам, I9),
`test_envelope_inherited_from_a_base_class`, `test_accept_overrides_the_model_rule`,
`test_accept_is_not_inverted_on_an_error_case`, `test_exception_in_succeeds_is_malformed`,
`test_case_with_envelope_ranks_as_conditional`, плюс по тесту на каждую диагностику §3.8.

**53.4**: `test_predicates_compose_and_read_bytes_or_text`, `test_predicate_label_in_repr`,
`test_predicate_mixes_with_a_plain_lambda`, `test_str_argument_on_undecodable_body_is_false`.

**Регресс**: существующие `test_exact_status_beats_range`, `test_explicit_media_beats_wildcard`,
`test_operation_case_beats_service_case`, `test_true_tie_is_ambiguous` в
`tests/unit/test_phase50_responses.py` остаются зелёными без правок; если какой-то из них
покраснеет, это отклонение и оно записывается в §9.

## 6. Документация

- `guides/responses/*`: словарная форма, новый порядок, конверт, предикаты; `Success`/`Error`
  помечены advanced.
- `sdk-authoring-reference.md`: раздел про кейсы ответа переписан под новый порядок.
- `CHANGELOG.md`: breaking про арбитраж, added про `when=`, `Envelope`, `accept=`, `match`.
- `scripts/docs_freshness.py update` для затронутых страниц.

## 7. Exit criteria

### 7.1. Приёмка на `kad`

1. В `kad/responses.py` не осталось `is_regular_html` и `is_pdf`; остаются только `is_challenge`
   и `is_blocked`, выраженные через `match`.
2. В `kad/api.py` нет ни одного `when=`, ни одного `Success(...)`/`condition=`; все успехи
   объявлены словарной формой.
3. `_document_items` удалён; неуспешный конверт поднимает `KadRequestFailed`, наследника
   `ApiError`.
4. Результат `CaseDocumentsPage` — `DocumentPage`; поля `success`/`message` не видны
   вызывающему коду.
5. Страница капчи со статусом 200 на каждой из ручек даёт `KadChallengeRequired`, а не молчаливый
   разбор; закреплено тестом в `kad`.

### 7.2. Библиотека

6. Все тесты §5 существуют и зелёные; порядок §3.2 закреплён прямой проверкой кортежа.
7. Диагностики §3.8 возникают при импорте.
8. `Envelope` работает на всех четырёх бэкендах (один параметризованный тест).
9. `len(eazy_sdk.__all__)` не изменился; `eazy_sdk.response.__all__` вырос ровно на `Envelope`.
10. Gates §8 зелёные, `STATUS.md` заполнен.

## 8. Гейты

- `uv run pytest -q tests/unit/test_phase53_response_cases.py`
- `uv run pytest -q --timeout=120` (полный набор; 10-секундный таймаут ложно срабатывает на
  Windows, см. STATUS фаз 50 и 52)
- `uv run mypy`
- `uv run ruff check`
- `uv run python scripts/docs_freshness.py check`
- `uv run python docs-site/scripts/validate_docs.py`
- `uv run python scripts/surface_count.py --total` (рост ровно на объявленные имена)

## 9. Журнал и отклонения

- 2026-09-08 — план написан по двум issue и трём решениям владельца (§10, D1–D3). Проба §3.4
  выполнена до написания плана.

## 10. Решения и отвергнутые варианты

- **D1. `Envelope` живёт в `eazy_sdk.response`** (решение владельца). Имя занято в
  `eazy_sdk.protocols` (конверт протокола: путь, метод, дискриминатор, JSON-RPC) и рядом с
  `ResponseEnvelope` (тип возврата `with_response`). Это три разные сущности с похожими именами;
  в одном файле сталкиваются редко, при необходимости импортируются с квалификацией. Риск принят
  осознанно, документация обязана развести их явно.
- **D2. Порядок арбитража меняется, диагностика shadowing из issue отклонена.** Предложенный там
  `PlanError` падал бы на самом обычном объявлении (сервисная ошибка на диапазоне с условием плюс
  любой успех без `when=`), то есть узаконивал бы boilerplate вместо его устранения. Причина бага
  — порядок рангов, а не отсутствие проверки. После перестановки перекрытия нет и проверять
  нечего. Узкая проверка остаётся только на реальную двусмысленность: одна модель в success и в
  error на одном статусе без единого критерия (D-53-04).
- **D3. `accept=` на кейсе остаётся** (решение владельца) как переопределение для моделей, которые
  нельзя изменить. Это не второй способ сказать одно и то же: `Envelope.succeeds` отвечает «конверт
  успешен» и инвертируется видом кейса, `accept=` отвечает «кейс подходит» и не инвертируется.
- **D4. Объявление на модели — атрибут, а не метод** (`__envelope__ = Envelope(...)`, не
  `def __accepts__`). Причина измерена (§3.4): значение `TypedDict` — обычный `dict`, метода у
  него нет. Атрибут держит обычные функции, поэтому сложные условия выражаются полноценным
  Python, а не языком выражений.
- **D5. Имя `succeeds`, а не `accepts`.** Рядом живёт `accept=` на кейсе, который отвечает на
  другой вопрос и не инвертируется. Два похожих имени для двух разных семантик — источник ошибок.
- **D6. `payload=` объявляется только на модели.** Где внутри конверта лежит нагрузка — свойство
  конверта, а не операции. Дублировать его на кейсе значит завести второй способ.
- **D7. `unwrap=` не трогаем.** Это JSON-pointer до разбора, он остаётся для сервисов без
  бизнес-статуса в конверте. С `Envelope` он несовместим по построению (после `unwrap` модель не
  видит `Success`), и это документируется, а не проверяется в рантайме: `unwrap` объявляется на
  представлении, `Envelope` — на модели, конфликт ловится при импорте как часть D-53-02.
- **D8. Композиция предикатов только для сырого тела.** Выражения над разобранной моделью
  (`field("success").is_(True)`) отклонены: обычная функция с аннотацией уже даёт подсказки IDE и
  проверку типов, а DSL был бы вторым способом написать то же самое и всегда беднее Python.
