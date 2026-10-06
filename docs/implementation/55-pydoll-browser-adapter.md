# Фаза 55. Нативный адаптер Pydoll для `eazy_sdk_browser`

Статус: `complete`.

Дата плана: 2026-10-06.

Основной читатель: исполнитель, который реализует и проверяет адаптер без повторного
архитектурного исследования.

## 0. Результат фазы

`eazy_sdk_browser` получает второй реально работающий адаптер страницы:

```python
from eazy_sdk_browser import AsyncBrowserClient
from eazy_sdk_browser.handlers.pydoll import PydollDriver

driver = PydollDriver(tab, context_key=context)
portal = PortalSdk(AsyncBrowserClient(driver, base_url="https://portal.example"))
result = await portal.orders.list()
```

Адаптер принимает готовый нативный async `pydoll.browser.tab.Tab`. Он не запускает браузер,
не создаёт контекст или вкладку, не выбирает identity и прокси и не управляет конкурентностью.
Этими вопросами владеет вызывающий слой, например `browser_pool`.

Фаза считается завершённой, когда одна и та же декларативная операция Eazy проходит общий
контрактный набор на Playwright и Pydoll, а отдельная приёмка через `browser_pool` доказывает
следующую цепочку:

```text
BrowserPool[Pydoll] -> PageLease[Tab] -> lease.attachment(PydollDriver)
                    -> AsyncBrowserClient -> declarative browser operation
```

## 1. Зафиксированные решения

### 1.1. Поддерживается Pydoll 3

Экстра пакета:

```toml
pydoll = ["pydoll-python>=3,<4"]
```

Pydoll 3 является текущим major и содержит breaking changes относительно 2.x. Адаптер не
поддерживает одновременно API 2.x и 3.x и не добавляет ветвление по версии. Совместимость
`browser_pool` с Pydoll 2.27 остаётся свойством `browser_pool`, но Eazy-операции поверх
арендованного `Tab` требуют установленный Pydoll 3.

### 1.2. Используется нативный `Tab`, не Playwright compatibility layer

Pydoll 3 предоставляет `pydoll.playwright.async_api`, но `browser_pool.PydollDriver` выдаёт
нативный `pydoll.browser.tab.Tab`. Оборачивание его через Playwright-совместимый API потребовало
бы другого владельца вкладки или незафиксированного внутреннего преобразования.

Новый модуль `eazy_sdk_browser.handlers.pydoll` реализует протоколы Eazy напрямую поверх
публичного API `Tab` и `WebElement`. Существующий `PlaywrightDriver` не расширяется проверками
типа транспорта и не получает второй путь поведения.

### 1.3. Зависимость остаётся односторонней

- `eazy_sdk_browser` не импортирует `browser_pool`;
- `browser_pool` не требуется для обычного использования адаптера;
- интеграционный слой импортирует обе библиотеки и связывает их публичные API;
- core-модули `eazy_sdk_browser` не импортируют `pydoll`;
- Pydoll импортируется только модулем `handlers/pydoll.py` и его тестами.

Реализация `browser_pool.drivers.pydoll.PydollDriver` служит проверенным источником решений по
кукам, ошибкам закрытой вкладки и различиям API, но код адаптера Eazy не импортируется оттуда и
не копирует управление процессами, контекстами, прокси или эмуляцией identity.

### 1.4. Оркестрацией владеет внешний слой

Адаптер не содержит:

- `Chrome()` / `Edge()` и запуск браузера;
- `create_browser_context`, `new_tab` и закрытие чужого `Tab`;
- очереди, семафоры, пулы и повтор задачи;
- выбор аккаунта, прокси, fingerprint или профиля браузера;
- хранилище сессий и account lease.

Его время жизни равно времени жизни одной страницы. `aclose()` снимает только собственные
callback-и и дожидается начатого чтения ответов. Закрывать `Tab` адаптер не имеет права.

## 2. Целевой профиль возможностей

Первая версия объявляет только доказанные возможности:

| Ось | Уровень | Контракт |
|---|---|---|
| `network` | `CAPTURE_VERIFIED` | ответы после `mark()`, ограниченный буфер, тело читается после завершения запроса |
| `session_state` | `BEST_EFFORT` | все cookies контекста и localStorage текущего origin; запись только cookies |
| `page_requests` | `CAPTURE_VERIFIED` | `fetch()` исполняется внутри документа или выбранного iframe |
| `navigation_events` | `CAPTURE_VERIFIED` | переход главного документа и same-document navigation считаются событиями |
| `shadow_dom` | `UNSUPPORTED` | не обещается, пока обычный `Locator` не проходит закрытый shadow root |
| `rich_text` | `CAPTURE_VERIFIED` | HTML и события `input`/`change` проверены на настоящем Chromium |

`session_state` не объявляется полным: публичный API нативного `Tab` читает cookies контекста,
но не даёт одним вызовом полный storage state всех origins. Завышать уровень ради симметрии с
Playwright запрещено.

Если `capture=None`, профиль понижает только `network` до `UNSUPPORTED`, как Playwright-адаптер.

## 3. Контракт конструктора и времени жизни

```python
class PydollDriver:
    def __init__(
        self,
        tab: Tab,
        *,
        context_key: object | None = None,
        capture: CapturePolicy | None = DEFAULT_CAPTURE,
    ) -> None: ...
```

- `tab` принадлежит вызывающему слою;
- `context_key` должен быть одинаковым у вкладок одного контекста. В интеграции с пулом это
  `lease.context`;
- без `context_key` адаптер использует безопасный page-local ключ. Такой режим годится для одной
  вкладки, но не заявляет общий `BrowserSession` между вкладками;
- `capture` имеет тот же смысл и те же бюджеты, что у `PlaywrightDriver`;
- повторно оборачивать один `Tab` без закрытия первого адаптера запрещено документацией;
- `async with PydollDriver(...)` вызывает только `aclose()`, не `tab.close()`.

В `browser_pool` адаптер создаётся как page-scoped attachment:

```python
async with pool.page(identity) as lease:
    driver = await lease.attachment(
        "eazy-sdk-browser:pydoll",
        lambda tab: PydollDriver(tab, context_key=lease.context),
    )
    sdk = PortalSdk(AsyncBrowserClient(driver, base_url=BASE_URL))
    await sdk.orders.list()
```

`lease.attachment()` гарантирует один адаптер на тёплую вкладку и вызывает его `aclose()` при
закрытии вкладки.

## 4. Реализация

### 55.0. Зафиксировать красный baseline и API Pydoll

Добавить `plugins/browser/tests/test_pydoll_handler.py` с импортом через `pytest.importorskip`
только для обычного локального прогона. В release/CI job экстра Pydoll обязательна, поэтому
весь модуль должен пройти без skip.

Baseline фиксирует отсутствие `eazy_sdk_browser.handlers.pydoll` и содержит таблицу публичных
API Pydoll 3, реально используемых адаптером:

- `Tab.query`, `find`, `go_to`, `current_url`, `page_source`, `execute_script`;
- `Tab.on`, `remove_callback`, enable-функции Page/Network domains;
- `Tab.get_network_response_body`, `get_cookies`, `set_cookies`;
- `WebElement.query`, `find`, `iframe_context`, `click`, `hover`, `clear`, `type_text`,
  `get_attribute`, `text`, `value`, `is_visible`, `execute_script`.

Приватные `_execute_command`, connection handler и внутренние object id не входят в реализацию.

### 55.1. Модуль, упаковка и честный профиль

Создать:

```text
plugins/browser/eazy_sdk_browser/handlers/pydoll.py
plugins/browser/tests/test_pydoll_handler.py
```

Сделать:

1. `PYDOLL_PROFILE` по таблице §2.
2. `PydollDriver(Tab, ...)` и `PydollElement(Tab, WebElement)`.
3. Экстру `pydoll-python>=3,<4`.
4. Изолированный импорт: `import eazy_sdk_browser` без Pydoll продолжает работать.
5. Явный импорт пользователя:

   ```python
   from eazy_sdk_browser.handlers.pydoll import PydollDriver
   ```

Не реэкспортировать адаптер из `eazy_sdk_browser` или `eazy_sdk_browser.handlers`: импорт
optional backend не должен становиться частью загрузки core.

### 55.2. Элементы, кандидаты и iframe

`PydollElement` реализует весь минимальный `Element`:

- `fill`: `clear()` и `type_text(value)`;
- `click`, `hover`;
- `check` / `uncheck`: проверить тип и состояние, изменить его через пользовательское действие
  либо DOM с событиями `input` и `change`; повторная установка идемпотентна;
- `select`: выбрать `option` по value и послать `input`/`change`;
- `press`: `focus()` элемента и `tab.keyboard.press(key)`;
- `text`, `value`, `attribute`, `visible`.

Поиск соблюдает семантику Eazy, а не удобство конкретного SDK:

1. `find()` ждёт все кандидаты под одним общим deadline. Запрещён последовательный полный
   timeout на каждый selector.
2. Если несколько кандидатов стали видимыми в одном цикле, побеждает порядок объявления.
3. `peek()` не ждёт.
4. `peek_all()` возвращает все видимые элементы первого непустого кандидата в DOM-порядке.
5. `Pick.first` и `Pick.last` применяются после фильтрации видимых элементов.
6. Цепочка `Locator.frames` разрешается от `Tab` внутрь через найденный iframe `WebElement`,
   затем поиск продолжается на этом элементе. Объекты iframe между навигациями не кешируются.

Для ожидания использовать короткие неблокирующие `query(..., timeout=0, raise_exc=False)` всех
кандидатов с единым monotonic deadline. `asyncio.gather` допустим; N последовательных Pydoll wait
запрещены.

#### Диалект selector-ов

Переносимая основа `css(...)`, `last(...)` и `any_of(...)` остаётся CSS/XPath, которые принимает
`Tab.query`.

Два уже публичных текстовых шаблона поддерживаются явно:

- `text="literal"` переводится в поиск Pydoll по точному тексту;
- `selector:has-text("literal")` выбирает `selector`, затем фильтрует по нормализованному тексту.

Разбор ограничен этими формами и использует существующий `text_escape`. Неизвестный
Playwright-only selector получает детерминированный `BrowserDeclarationError` с selector и именем
драйвера, а не молчаливый `None`.

### 55.3. Навигация и базовое чтение страницы

Реализовать:

- `location()` через `await tab.current_url()`;
- `page_text()` через скрипт, читающий видимый текст `document.body`;
- `goto(url, wait, within)` с одним hard deadline;
- `navigations()` через Page events главного документа.

Семантика ожидания:

- `load`: `Tab.go_to()`;
- `networkidle`: `go_to()` и `wait_for_network_idle()` с оставшимся бюджетом;
- `domcontentloaded`: подписка ставится до `go_to`; если Pydoll всё равно ждёт более сильный
  `load`, это допустимое усиление, но timeout остаётся общим.

Считать `Page.frameNavigated` только для главного frame и `Page.navigatedWithinDocument` для
same-document переходов. Переход iframe не увеличивает счётчик страницы.

Любая ошибка Pydoll, кроме документированного «элемент не найден», материализуется как
`TransportError("pydoll", phase, 1, cause)`. Закрытая вкладка, оборванный CDP и timeout навигации
не превращаются в `None`.

### 55.4. Захват сети с ограниченным бюджетом

Подписаться на Network events только при `capture is not None`:

1. `RESPONSE_RECEIVED` сохраняет request id, URL, статус, headers, resource type и объявленную
   длину.
2. `LOADING_FINISHED` разрешает чтение тела.
3. До чтения применяются `CapturePolicy.resource_types` и `max_body_bytes` по `content-length`.
4. После чтения фактический размер проверяется повторно.
5. Base64 CDP body декодируется в bytes; текст не перекодируется через locale ОС.
6. `max_total_bytes` вытесняет старые тела, но монотонная позиция `mark()` не меняется.
7. Ответ с отброшенным телом остаётся в буфере с `body_dropped=True`.

`mark()` и `wait_response()` повторяют один контракт Playwright-адаптера. Общую логику буфера
вынести в transport-neutral private helper, если это удаляет дублирование без изменения
публичного API. Два расходящихся алгоритма budget/mark запрещены.

Не выключать Network domain в `aclose()`: его мог включить `browser_pool`, автор site SDK или
другой attachment. Адаптер снимает только callback id, которые зарегистрировал сам.

### 55.5. Состояние контекста и browser-pool

Реализовать `StateAware`:

- cookies через `tab.get_cookies()` / `tab.set_cookies()` с сохранением domain, path, expiry,
  secure, httpOnly и sameSite;
- localStorage только текущего origin через `execute_script`;
- `context_key()` возвращает переданный `context_key`; без него page-local ключ.

Не добавлять запись localStorage в живой контекст. Как и у Playwright-адаптера, localStorage
восстанавливает владелец контекста до выдачи страницы. Для `browser_pool` это его `StateStore` и
Pydoll driver, для standalone-кода это вызывающий слой.

В документации показать один режим владения сессией:

- `browser_pool.SessionFlow` исполняет `BrowserLogin.sign_in()` и сохраняет контекст;
- `AsyncBrowserClient` внутри page lease создаётся без `BrowserSession`;
- classifier пула переводит `login.is_expired(error)` в `ErrorKind.session`;
- следующая попытка получает новый контекст и повторно проходит `SessionFlow.open()`.

Запрещено создавать `BrowserSession` на каждую вкладку пула: сессия принадлежит контексту, а
page attachment принадлежит вкладке.

### 55.6. Page fetch и rich text

`FetchAware.fetch()` исполняет тот же browser-side fetch, что Playwright:

- метод, headers, body и credentials передаются без потерь;
- request/response body ходит через base64;
- redirect следует браузерной политике `follow`;
- timeout ограничивается внутри `AbortController` и снаружи общим deadline;
- выбранный iframe исполняет скрипт в своём execution context.

Вынести построение request spec и разбор результата в общий private helper. Транспортные вызовы
`evaluate`/`execute_script` остаются в адаптерах.

`RichTextAware.set_html()` находит элемент обычным путём, задаёт `innerHTML` и посылает bubbling
`input` и `change`. Тест проверяет, что обработчик страницы увидел оба события.

`shadow_dom` остаётся `UNSUPPORTED`. Поддержку закрытых shadow roots не следует объявлять только
потому, что Pydoll предоставляет `find_shadow_roots()`: Eazy `Locator` пока не задаёт переход в
конкретный shadow root и не может гарантировать обычную selector-семантику.

### 55.7. Закрытие callback-ов и ошибок

`PydollDriver.aclose()`:

1. идемпотентен;
2. снимает все собственные callback id через `remove_callback`;
3. не очищает чужие callback-и и не вызывает `clear_callbacks()`;
4. дожидается начатых задач чтения тел с `return_exceptions=True`;
5. не закрывает `Tab`, browser или context;
6. после закрытия не принимает новые события.

Если Pydoll не предоставляет событие закрытия `Tab`, автоматическая отписка не симулируется
polling-задачей. В standalone-примере обязателен `async with`; в `browser_pool` закрытие делает
`lease.attachment()`.

## 5. Тестовая матрица

### 5.1. Общий contract suite

Вынести поведение, которое обязано совпадать у адаптеров, в параметризуемый contract suite:

- общий deadline для `any_of`;
- порядок кандидатов, `first`/`last`;
- `peek` без ожидания и коллекции только видимых узлов;
- вложенные iframe;
- все действия и чтения `Element`;
- навигация и same-document event;
- network `mark`/`since`, двоичное тело, oversized body и общий бюджет;
- cookies, current-origin localStorage, context key;
- page fetch и rich text;
- закрытие callback-ов и отсутствие двойной подписки.

Playwright и Pydoll проходят один набор. Backend-specific тесты остаются только для различий API.

### 5.2. Живой Chromium

`test_pydoll_handler.py` поднимает настоящий Chrome/Edge через официальный Pydoll 3 и локальный
HTTPS/HTTP test site. Обязательные случаи:

1. CSS, XPath, `text="..."`, `:has-text(...)`, first/last и коллекция.
2. Вложенный iframe, включая cross-origin iframe локального test site.
3. `fill`, press, checkbox, select, hover, rich text.
4. `goto` для трёх load states и same-document navigation.
5. Два API-ответа с водоразделом, JSON и binary body.
6. Resource filtering и оба byte budget.
7. Cookie round-trip и localStorage текущего origin.
8. Browser-side fetch верхнего документа и iframe.
9. Закрытая вкладка даёт `TransportError`, а не `ElementNotFoundError`.
10. `aclose()` оставляет чужой Pydoll callback работающим.

Интеграционные тесты не считаются пройденными, если весь модуль skipped.

### 5.3. Приёмка через `browser_pool`

В `C:/Users/user/Desktop/browser_pool` добавить consumer-тест или отдельный acceptance fixture,
который устанавливает текущий wheel `eazy-sdk-browser[pydoll]` и проверяет:

1. `BrowserPool(PydollDriver())` выдаёт `PageLease` с нативным `Tab`.
2. `lease.attachment()` создаёт Eazy `PydollDriver` один раз на тёплую вкладку.
3. Две аренды выполняют одну декларативную Eazy-операцию без повторной подписки callback-ов.
4. Закрытие/выбрасывание вкладки вызывает `aclose()` attachment ровно один раз.
5. `BrowserLogin` исполняется из pool `SessionFlow`, не из page-local `BrowserSession`.
6. Объявленная session-expired ошибка классифицируется как `ErrorKind.session`, контекст
   выводится, повтор получает новый контекст и входит заново.
7. Операция после `lease.commit()` не повторяется пулом.

Eazy SDK не получает runtime-зависимость от `browser_pool`; это downstream acceptance gate.

## 6. Документация и поставка

Обновить:

- `plugins/browser/README.md`: выбор Playwright/Pydoll и короткий пример;
- `docs-site/.../guides/browser/index.mdx`: standalone Pydoll;
- новый `guides/browser/browser-pool.mdx`: владение ресурсами и сессией;
- `docs-site/.../api-reference/browser.mdx`: профиль Pydoll и ограничения;
- `CHANGELOG.md`: новый backend и точная поддерживаемая версия;
- package/extras audits и CI установку Pydoll 3.

Документация обязана явно сказать:

- Pydoll управляет только Chromium-based browsers;
- адаптер принимает готовый `Tab`;
- `session_state` имеет уровень `BEST_EFFORT`;
- shadow DOM не обещан первой версией;
- `browser_pool` владеет retry задачи и сессией контекста;
- Eazy владеет декларацией и исполнением одной site-операции.

## 7. Порядок исполнения

| Инкремент | Зависит от | Результат |
|---|---|---|
| 55.0 | фаза 54 | красный baseline, frozen API Pydoll 3, capability table |
| 55.1 | 55.0 | модуль, упаковка, import isolation, профиль |
| 55.2 | 55.1 | элементы, кандидаты, selector dialect, iframe |
| 55.3 | 55.2 | навигация, location, page text, события |
| 55.4 | 55.3 | bounded network capture |
| 55.5 | 55.2 | cookies, current-origin state, context ownership |
| 55.6 | 55.2, 55.3 | page fetch и rich text |
| 55.7 | 55.4 | lifecycle callback-ов |
| 55.8 | 55.4–55.7 | общий suite, browser-pool acceptance, docs и release gates |

После каждого инкремента обновлять `STATUS.md`: state, доставленное поведение, точные команды и
результаты, оставшаяся работа. Следующий инкремент не интегрируется до зелёных focused gates
предыдущего.

## 8. Exit criteria

Фаза завершена, только если:

1. `PydollDriver` структурно реализует `Driver`, `NavigationAware`, `NetworkAware`, `StateAware`,
   `FetchAware` и `RichTextAware`.
2. Профиль совпадает с реально проверенным поведением; `shadow_dom` не завышен.
3. Один contract suite зелёный на Playwright и Pydoll.
4. Pydoll Chromium tests прошли без полного skip на поддерживаемой CI-платформе.
5. `any_of` использует один deadline и не умножает timeout на число кандидатов.
6. Binary body, oversized body, resource filter и total budget имеют capture evidence.
7. Закрытие адаптера не закрывает чужой `Tab`, не снимает чужие callback-и и не оставляет задач.
8. Core импортируется без установленного Pydoll.
9. Wheel с экстрой Pydoll устанавливается изолированно и импортирует адаптер.
10. Приёмка через `browser_pool` проходит сценарий lease, attachment, login, session expiry,
    context replacement и retry.
11. Документация и `STATUS.md` описывают фактические ограничения без обещаний Playwright parity,
    которых тесты не подтверждают.
12. Все гейты §9 зелёные.

## 9. Гейты

Focused:

```bash
uv run pytest -q plugins/browser/tests/test_pydoll_handler.py
uv run pytest -q plugins/browser/tests/test_driver_contract.py
uv run pytest -q plugins/browser/tests tests/unit/test_foreign_routers.py
uv run basedpyright plugins/browser/probe/typing_probe.py
uv run lint-imports
```

Core:

```bash
uv run pytest -q
uv run mypy
uv run ruff check
uv run ruff format --check
uv run complexipy --plain --failed
```

Documentation and release:

```bash
uv run python scripts/docs_freshness.py check
uv run python docs-site/scripts/validate_docs.py
uv build --all-packages
uv run python scripts/package_audit.py
uv run python scripts/extras_smoke.py
```

Downstream:

```bash
# Из C:/Users/user/Desktop/browser_pool с wheel текущего checkout.
uv run pytest -q <consumer test for eazy_sdk_browser + PydollDriver>
```

Фактические имена новых contract/consumer tests фиксируются в `STATUS.md`; placeholder-команда
не считается выполненным гейтом.

## 10. Источники и проверенные факты

- Pydoll repository: <https://github.com/autoscrape-labs/pydoll>
- Pydoll 3 Playwright compatibility: <https://pydoll.tech/docs/guides/playwright-api/>
- Pydoll network monitoring: <https://pydoll.tech/docs/guides/network-monitoring/>
- Pydoll iframe traversal: <https://pydoll.tech/docs/guides/iframes/>
- Pydoll cookies and sessions: <https://pydoll.tech/docs/guides/cookies-and-sessions/>
- Reference implementation for lifecycle/cookies/errors:
  `C:/Users/user/Desktop/browser_pool/src/browser_pool/drivers/pydoll.py`

Публичный API Pydoll перепроверяется в 55.0 по установленной минимальной версии и текущей
верхней версии `<4`. Документация или source `main` без versioned install не являются достаточным
доказательством совместимости.

## 11. Риски

| Риск | Как удерживается |
|---|---|
| Pydoll callbacks продолжают жить после аренды | callback ids принадлежат адаптеру; `aclose()` снимает только их; pool attachment test |
| Тело читается до `loadingFinished` | отдельные response/loading callbacks и correlation по request id |
| Буфер сети съедает память | общий `CapturePolicy`, проверка размера до и после чтения, total budget |
| Несколько selector-ов умножают timeout | единый deadline и параллельный non-waiting probe |
| Raw selector оказывается Playwright-only | две поддерживаемые текстовые формы; остальное падает явно |
| Сессия принадлежит вкладке вместо контекста | `context_key` от владельца; pool `SessionFlow`; запрет page-local `BrowserSession` |
| Две библиотеки обе повторяют задачу | auth retry Eazy выключен в pool mode; task retry принадлежит `browser_pool` |
| Capability обещает больше Pydoll API | первая версия оставляет `shadow_dom` unsupported и state best-effort |
| Pydoll меняет API | диапазон major `>=3,<4`, минимальная и актуальная матрицы, package lock |
