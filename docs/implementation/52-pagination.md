# Фаза 52. Декларативная пагинация: `__pages__` на операции, `pages()`/`items()` на роутере

Статус: план утверждён 2026-09-08 (владелец: «оформляй как фазу 52 и начинай с `Pages.numbered`»).
Gates — в `STATUS.md`. Зависит от фазы 50 (операция-класс, `request()`/`evolve()`/`send()`) и
фазы 44 (`Serialization.models.evolve`).

Тип документа: authoritative implementation plan для Фазы 52. Обсуждение, из которого он вырос,
записано в §10 вместе с отвергнутыми вариантами.

---

## 0. Как исполнять этот план автономно

Правила, дополняющие `AGENTS.md`:

1. **Возобновление.** Первое действие новой сессии — прочитать §9 (журнал) и раздел
   `## Phase 52` в `STATUS.md`. Первый шаг со статусом, отличным от `done`, — текущий.
2. **Порядок шагов фиксирован**: 52.1 → 52.2 → 52.3 → 52.4. Шаг 52.4 условный (§4.4).
3. **Каждый шаг — отдельный коммит** `Phase 52.N: <что сделано>` с тестами внутри.
4. **Один путь исполнения.** Страница пагинации — это обычный `send()` связанной операции.
   Никакого второго исполнителя, никакого обхода retry/middleware/protections. Стратегия
   пагинации решает только одно: какой запрос следующий и когда остановиться.
5. **Ни одного изменения байтов.** Фаза не трогает конвейер запроса, кодеки, подпись.
6. **Проверки — при объявлении.** Всё, что можно проверить по классу операции (имена полей,
   тип результата), проверяется в `op()` при импорте, а не при первом вызове.
7. **При противоречии** между планом и кодом — записать в §9, выбрать вариант без второго
   пути исполнения, продолжить. Останавливаться только если меняется публичный контракт.

---

## 1. Что не так

SDK, написанный на фазе 50, объявляет ручки классами и не содержит кода, кроме объявлений.
Единственное, что заставляет автора писать метод-генератор на роутере, — пагинация:

```python
async def case_documents(self, case_id: str, *, page_size: int = 50, max_pages: int | None = None):
    page_number = 1
    seen: set[str] = set()
    while max_pages is None or page_number <= max_pages:
        response = await self.case_documents_page(case_id=case_id, page=page_number, per_page=page_size)
        documents = [item for item in response.result.items if item.document_id not in seen]
        if not documents:
            return
        for document in documents:
            seen.add(document.document_id)
            yield document
        if page_number >= response.result.pages_count:
            return
        page_number += 1
```

Двадцать строк, из которых декларативного знания — четыре факта: какое поле запроса — номер
страницы, какое — размер, где в ответе элементы и где число страниц. Остальное — один и тот же
цикл, повторяемый в каждом SDK с вариациями и ошибками (бесконечный цикл на сервере, который
после последней страницы отдаёт её снова; забытый `max_pages`; дедупликация «по месту»).

Ни `unihttp`, ни `descanso` пагинации не имеют. У генераторов SDK она есть и давно устоялась:
Speakeasy (`x-speakeasy-pagination`) и Stainless описывают её тремя стратегиями (offset/page,
cursor, next-url), ролями полей запроса (`page`, `offset`, `limit`, `cursor`) и путями в
ответе (`results`, `numPages`, `nextCursor`). Эта модель и берётся за основу.

## 2. Инварианты

- **I1.** Стратегия пагинации объявляется на классе операции атрибутом `__pages__`, рядом с
  `__http__`, и не меняет сигнатуру, ответ и поведение обычного вызова `api.op(...)`.
- **I2.** Итерация — это `request()` → `send()` → `evolve()` → `send()` … Каждая страница
  проходит полный путь исполнения операции. `options=` применяется к каждой странице.
- **I3.** Стратегия — чистые данные без ввода-вывода: функция «следующие изменения запроса или
  `None`» тестируется без клиента.
- **I4.** Условия остановки объявлены и конечны: пустая страница, страница короче размера,
  достигнут `total_pages`, достигнут `max_pages`, страница не дала ни одного нового элемента при
  дедупликации. Итератор без `max_pages` завершается на любом из первых четырёх.
- **I5.** Ошибки объявления — при импорте: неизвестное поле, тип результата, не совпадающий с
  `HttpOperation[T]`/`success=`, стратегия не того типа.
- **I6.** Подсказки IDE: тип результата передаётся первым аргументом `Pages.numbered(Model, ...)`,
  чтобы параметр лямбд `items=`/`total_pages=` выводился как `Model`. Строковые пути в ответ
  (`"result.items"`) не принимаются (§10, D2).
- **I7.** Бюджет имён корня `eazy_sdk` не растёт: `Pages` живёт в `eazy_sdk.pagination` (§10, D3).

## 3. Архитектура

### 3.1. Модули

| Модуль | Содержимое |
|---|---|
| `eazy_sdk/pagination.py` (новый) | `Pages` (фабрики), `NumberedPages[T]`, позже `OffsetPages[T]`, `CursorPages[T]`, `Pagination[T]` (объединение), `next_changes(...)`, диагностики. Без импортов из `api`. |
| `eazy_sdk/operation.py` | `HttpOperation.__pages__: ClassVar[Pagination[Any]]` — аннотация, как у `__http__`. |
| `eazy_sdk/api.py` | `op()` читает и валидирует `__pages__`; дескриптор хранит `pages`; `_BoundAsyncOperation.pages()/items()` и `_BoundSyncOperation.pages()/items()`. |

### 3.2. Объявление

```python
from eazy_sdk.pagination import Pages

@dataclass(frozen=True, slots=True, kw_only=True)
class CaseDocumentsPage(HttpOperation[DocumentPageResponse]):
    __http__ = Http.get("/Kad/CaseDocumentsPage")
    __pages__ = Pages.numbered(
        DocumentPageResponse,
        page="page",
        size="per_page",
        items=lambda r: r.result.items,
        total_pages=lambda r: r.result.pages_count,
    )

    case_id: Query[str]
    page: Query[int] = 1
    per_page: Query[int] = 25
```

`page=` и `size=` — **python-имена полей операции**, не wire-имена: стратегия работает через
`evolve()`, а тот принимает имена полей модели. `size=` необязателен; без него правило «страница
короче размера» не применяется.

### 3.3. Вызов

```python
async for page in api.case_documents_page.pages(case_id=cid, per_page=50):      # DocumentPageResponse
    ...
async for doc in api.case_documents_page.items(case_id=cid, key=lambda d: d.document_id):
    ...
```

Сигнатура обоих: позиционные и именованные аргументы конструктора операции плюс keyword-only
`max_pages: int | None = None`, `options: CallOptions | None = None`; у `items()` ещё
`key: Callable[[Any], Hashable] | None = None`. Первая страница — та, что задана значениями
полей (`page=3` начинает с третьей). Синхронный роутер отдаёт обычные генераторы.

### 3.4. Стратегия `numbered`: правила следующего запроса

Вход: значение запроса, результат страницы, число элементов, отданных с этой страницы
(`fresh`, после дедупликации). Выход: изменения для `evolve()` или `None`.

1. `fresh == 0` → `None`.
2. `total_pages` задан и `current_page >= total_pages(result)` → `None`.
3. `size` задан, значение поля — `int`, и `len(items(result)) < size` → `None`.
4. Иначе `{page: current_page + 1}`.

`current_page` читается с текущего значения запроса; не-`int` (например `UNSET`) —
`PlanError` D-52-05. Порядок правил нормативен: тест `test_numbered_rules_order`.

### 3.5. Диагностики

| Код | Текст (подстрока, которую проверяет тест) |
|---|---|
| D-52-01 | `operation class X.__pages__ must be a Pages strategy, got Y` |
| D-52-02 | `X.__pages__ names unknown field 'pgae'; fields: ...` |
| D-52-03 | `X.__pages__ declares result A, the operation returns B` |
| D-52-04 | `pages() requires __pages__ on X` |
| D-52-05 | `X.page must be an int to paginate, got Unset` |
| D-52-06 | `max_pages must be None or >= 1` |

## 4. Задачи

### 4.1. 52.1 — `Pages.numbered`, `pages()`/`items()`, валидация, документация

- `eazy_sdk/pagination.py`: `NumberedPages[T]`, `Pages.numbered(result, *, page, items, size=None, total_pages=None)`, `next_changes`.
- `api.py`: чтение `__pages__` в `op()` с D-52-01..03; `pages()`/`items()` на обеих связанных операциях; D-52-04..06.
- `operation.py`: аннотация `__pages__`.
- Тесты `tests/unit/test_phase52_pagination.py` (§5).
- Документация: `sdk-authoring-reference.md` (раздел «Pagination»), `docs-site/.../guides/pagination.mdx`, карточка в `guides/index.mdx`, `CHANGELOG.md` (Unreleased), `README.md` фаз.

### 4.2. 52.2 — `Pages.offset`

`offset=` и `limit=` — имена полей; следующий запрос `{offset: offset + len(items)}`; остановка:
пусто, короче `limit`, необязательный `total=` (`Callable[[T], int]`, остановка при
`offset + len(items) >= total`).

### 4.3. 52.3 — `Pages.cursor`

`cursor=` — имя поля; `next_cursor: Callable[[T], object | None]`; остановка: пусто или
`next_cursor(result) is None`. Первая страница — со значением поля как есть (обычно `UNSET`/`None`).

### 4.4. 52.4 — `Pages.next_url` (условный)

Нужен шов «отправить операцию по абсолютному URL вместо `path`». Исполняется только если
владелец подтвердит потребность; иначе записывается в §9 как отложенный.

## 5. Тесты (`tests/unit/test_phase52_pagination.py`)

Без ввода-вывода (`next_changes`): `test_numbered_stops_on_empty`, `test_numbered_stops_below_size`,
`test_numbered_stops_at_total_pages`, `test_numbered_advances`, `test_numbered_rules_order`,
`test_numbered_requires_int_page` (D-52-05).

Через `MockTransport` (sync и async): `test_pages_yields_every_page_and_stops`,
`test_items_flattens_pages`, `test_items_key_dedupes_and_stops_on_repeated_page` (сервер после
последней страницы отдаёт её снова — цикл конечен), `test_max_pages_bounds_iteration`,
`test_first_page_is_the_request_value` (`page=3` начинает с третьей), `test_options_reach_every_page`,
`test_sync_router_returns_generators`.

Объявление: `test_pages_rejects_unknown_field` (D-52-02), `test_pages_rejects_wrong_result_type`
(D-52-03), `test_pages_rejects_non_strategy` (D-52-01), `test_pages_without_declaration` (D-52-04),
`test_max_pages_validation` (D-52-06), `test_ordinary_call_is_unchanged` (I1).

## 6. Документация

- `sdk-authoring-reference.md`: раздел «Pagination» после «Request placements»; пример из §3.2.
- `docs-site/src/content/docs/guides/pagination.mdx` с `sources: [eazy_sdk.pagination]`;
  runnable-пример в стиле остальных страниц (встроенный `MockTransport`, страницы в списке).
- Карточка «Pagination» в разделе «Поведение» `guides/index.mdx`.
- `uv run python scripts/docs_freshness.py update` для новой страницы и страниц, чьи источники
  изменились.

## 7. Exit criteria

1. §3.2 объявляется без пользовательского кода; `case_documents` из §1 выражается как
   `items(..., key=...)` и даёт ту же последовательность документов на тех же ответах.
2. Все тесты §5 существуют и зелёные; правила §3.4 покрыты по одному.
3. Диагностики §3.5 — при `op()`, кроме D-52-04..06 (при вызове).
4. Обычный вызов операции с `__pages__` не изменился (I1): байты запроса и результат равны.
5. `len(eazy_sdk.__all__)` не изменился (I7).
6. Gates §8 зелёные, `STATUS.md` заполнен.

## 8. Гейты

- `uv run pytest -q tests/unit/test_phase52_pagination.py`
- `uv run pytest -q --timeout=120` (полный набор; 10-секундный таймаут ложно срабатывает на Windows, см. STATUS фазы 50)
- `uv run mypy`
- `uv run ruff check`
- `uv run python scripts/docs_freshness.py check`
- `uv run python docs-site/scripts/validate_docs.py`

## 9. Журнал и отклонения

- 2026-09-08 — план написан; 52.1 начат.
- 2026-09-08 — 52.1 done: `Pages.numbered`, `pages()`/`items()`, D-52-01..06, тесты (26), документация.
  Evidence — `STATUS.md`, раздел «Phase 52». Отклонений от §3–§4 нет. Замечено: `HttpOperation[Any]`
  не считается generic-аргументом (правило фазы 50), поэтому D-52-03 сверяет с `success=`; тест
  `test_pages_follows_success_when_no_generic` закрепляет это.
- 2026-09-08 — 52.2 done: `Pages.offset` (§4.2), `next_changes` диспетчеризует по типу стратегии,
  общие `_int_field`/`_declared_int`; 10 тестов (правила, порядок, роутер, сервер короче лимита,
  объявление). Уточнение к §4.2: следующий `offset` = текущий + длина полученной страницы,
  не `+ limit` — сервер, отдавший меньше, не пропускается.

## 10. Решения и отвергнутые варианты

- **D1. Составные операции (`Flow`/`Step`/`From`) отклонены владельцем** 2026-09-08: «оно того не
  стоит и такое трудно будет дебажить». Цепочка `PreparePdf → DownloadStampedPdf` остаётся обычным
  методом роутера. Пагинация — единственный принятый случай декларативного «нескольких вызовов».
- **D2. Строковые пути в ответ отклонены**: ни подсказок IDE, ни проверки типов, ошибка только в
  рантайме. Принята форма `Pages.numbered(Model, items=lambda r: ...)`: `T` выводится из первого
  аргумента, лямбды типизированы, mypy проверяет `r.result.pages_count`. Цена — тип пишется
  дважды (в `HttpOperation[T]` и в `__pages__`); расхождение ловит D-52-03 при импорте.
- **D3. `Pages` не экспортируется из корня**: тест `len(eazy_sdk.__all__) <= 40` и бюджет имён
  фазы 50 (Приложение B). Точка входа — `from eazy_sdk.pagination import Pages`, как у
  `eazy_sdk.request.markers`.
- **D4. `page=`/`size=` — python-имена полей**, потому что стратегия строит следующий запрос
  через `evolve()`, а не через wire. Wire-имя принадлежит модели (фаза 50) и здесь не нужно.
- **D5. Дедупликация — `key=` у `items()`, не у стратегии.** Это свойство вызова («мне нужны
  уникальные документы»), а не сервера. Правило «страница без новых элементов останавливает
  итерацию» покрывает и сервер, повторяющий последнюю страницу (`search_pages` из §1 делал это
  подписью страницы вручную).
- **D6. `pages()`/`items()` типизированы как `*args: Any, **kwargs: Any`** плюс явные
  keyword-only параметры, как уже сделано у `prepare()`: PEP 612 не позволяет добавить
  именованные параметры к `P.kwargs`. Элементы `items()` — `Any`: связанная операция знает `T`,
  но не тип элемента.
- **D7. Декоратор `@api.get` не получает `__pages__`**: класс синтезируется из функции, а
  стратегии нужен класс с полями. Декоратор остаётся сахаром для операций без пагинации.
