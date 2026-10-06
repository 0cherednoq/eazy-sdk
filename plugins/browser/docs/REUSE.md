# Разбор плагина: что переиспользовать из `eazy-sdk`, что чинить, как выровнять API

Дата разбора: 2026-09-12. Основание — четыре документа плагина (`OVERVIEW.md`, `DESIGN.md`,
`decisions.md`, `MERGE.md`), весь код `eazy_sdk_browser`, `examples/browser_portal.py`,
тесты плагина и сверка с ядром `eazy_sdk` (auth, protection, accounts, policies, middleware,
профиль обработчика, форма HTTP- и WebSocket-операций, роутеры, DI, пагинация).

План работ по итогам разбора — `PLAN.md`.

---

## 1. Что переиспользовать из `eazy_sdk`

### 1.1. Точка стыковки с роутерами уже есть

В `eazy_sdk/api.py` объявлен структурный протокол `_PublishesItself`: класс операции с
classmethod `__publish__()` публикуется обычным `op()`, а HTTP-сторона о его пакете не
знает. Так подключён WebSocket (`eazy_sdk/websocket/operation.py`, `ws_descriptor`).

```python
# eazy_sdk/api.py
@runtime_checkable
class _PublishesItself[TDescriptor](Protocol):
    @classmethod
    def __publish__(cls) -> TDescriptor: ...

def op(operation):
    if isinstance(operation, _PublishesItself):
        return operation.__publish__()
    ...
```

Браузерной операции достаточно реализовать `__publish__`, и она встанет на роутер и в общий
`AsyncRoot` через `api_group` рядом с HTTP-роутерами. Сейчас плагин вместо этого предлагает
`OpenCreateCompany().execute(driver)` и рукописные методы у состояний.

Граница проводится с двух сторон, как у WebSocket: HTTP не импортирует браузер (контракт
`http-core-does-not-know-the-browser` уже есть), а браузерный роутер сам проверяет в
`__init_subclass__`, что на него не публикуют `HttpOperation`, и браузерный дескриптор в
`__set_name__` — что его не вешают на `AsyncApi`.

### 1.2. Авторизация

| Модуль | Берётся | Почему |
|---|---|---|
| `eazy_sdk/auth/session.py` | `session_lifecycle(model, *, context_factory, service, store, ...)`, `SessionLifecycle`, `SessionStore`, `SessionAcquirer`/`Refresher`/`Adopter`/`Bridge`, `SessionKey`/`Revision`/`Record`, `MemorySessionStore`, маркеры `ExpiresAt`, `RefreshToken` | модуль сам объявляет нейтральность; контекст входа задаётся фабрикой, туда кладётся драйвер |
| `eazy_sdk/auth/lifecycle.py` | `LifecycleGraph`, `LifecycleNode`, `LifecycleCycleError` | защита от рекурсивного входа |
| `eazy_sdk/auth/cookies.py` | `HttpCookieSession` | готовая модель куки с `domain/path/secure/http_only/same_site/expires_at` |
| `eazy_sdk/auth/session.py` | `SessionBridge`, `BridgedSessionAdopter` | мост браузерной сессии в `Auth` HTTP-клиента |

Порядок `SessionLifecycle.resolve()`: store → validate → refresh → `initial_session` →
`acquire(credentials, context_factory(graph))`, всё под `asyncio.Lock`, ревизии монотонны.
Сервис входа — любой объект с `async acquire(credentials, context)` и опциональным
`async refresh(session, context)`.

**Не переиспользуется:** `session_auth` (модель обязана иметь поле `Bearer`),
`session_cookie` (читает `Set-Cookie` из `context.responses`), `AuthPlacement`,
`AuthLocation`, `resolve_security` — пишут в слоты скомпилированного HTTP-запроса.

### 1.3. Хранилище

Весь `eazy_sdk_accounts.storage` годится как есть, и плагин это уже подтвердил
(`integrations/accounts.py`): `AccountWorkspace.sessions.save(account, SessionData,
label="browser")`, `restrictions.freeze/ban`, `pool.pick/lease`, `history.record`.

Добавить стоит `RepositorySessionStore` из `storage/session_bridge.py` — это готовый
`SessionStore` для `SessionLifecycle`: логин (§1.2) и хранение сшиваются без своего кода.

Известные швы (не менять в плагине, чинить в ядре):

* `SessionData.cookies` — `dict[str, str]`; полное состояние живёт в
  `params["browser_state"]`, плоский словарь — проекция для HTTP;
* `ban`/`freeze` и `Order` не экспортированы из `storage/__init__`;
* `open_workspace` из `plugins/sqlmodel` возвращает `SqlAccountWorkspace`, у которого нет
  `sessions.save(...)`, `restrictions`, `history`, `pool` — докстринг
  `integrations/accounts.py` показывает его ошибочно;
* `SessionCodec` объявлен дважды одинаковой формой: `storage/session_bridge.py` и
  `auth/session_runtime.py`.

### 1.4. Декларативное описание и прочее нейтральное

| Что | Где | Как использовать |
|---|---|---|
| `Responses`, `Success`, `Error`, `StatusRange`, `Json`, `when=`, теги `Const`/`Payload` | `eazy_sdk/response/` | уже используется через `NormalizedResponse` + `ResponseContext` (`integrations/eazy_sdk.py`) |
| `Predicate` с `__and__/__or__/__invert__` и `label`, фабрики `body`, `status`, `header` | `eazy_sdk/response/match.py` | образец для языка признаков страницы |
| `RetryPolicy.wait()` | `eazy_sdk/policies.py` | экспонента с джиттером, инъекция `sleep`/`random` для тестов |
| `ScopedMiddleware[TScope, TImplementation]` | `eazy_sdk/middleware.py` | спроектирован под второй транспорт; браузеру нужен свой `BrowserScope` |
| `SolveContext`, `ChallengeSolver`, `SolverRequirement`/`Binding`/`Bindings`, `ReplayPolicy`, `TransportIdentity`, персистентности `per_call`/`until_expiry` | `eazy_sdk/protection/advanced.py` | форма «обнаружил → решил → повторил» как чистые значения; применитель свой |
| `HandlerProfile` + `CapabilityLevel` + `validate_profile` + `CapabilityMismatchError` | `eazy_sdk/handlers/profile.py` | паттерн профиля: поля-оси, рефлексивная проверка, все несовпадения разом |
| `TransportError(handler, phase, attempt, cause)` | `eazy_sdk/handlers/profile.py` | для закрытой страницы и упавшего браузера |
| `EazySdkError`, `PlanError`, `ConfigurationError` | `eazy_sdk/core/errors.py` | базы для иерархии плагина |
| `UNSET`/`Omittable`, `ModelAdapterRegistry.fields(owner)` | `eazy_sdk` | чтение полей операции как у WebSocket (`values_of`) |

**Не брать:** `clients/attempts.py` (приватный, `__all__ = []`), `AttemptMiddleware`
(контексты несут `PreparedRequest`), `ProtectionBundle`/`Guard.to_bundle()`/`solution_fields`
(лоуерятся в HTTP-слоты).

### 1.5. Что WebSocket оставил недотянутым

Список того, где браузерный плагин может быть сильнее второго транспорта: у WS нет
интеграции с `AsyncRoot`/`api_group`, нет `Identity`/`Serialization`, нет `.request()`/
`.evolve()`/`.send()`/`.prepare()` у связанной операции, только `__call__`.

---

## 2. Минусы текущей реализации

Отсортированы по весу. Первые четыре — дефекты поведения, остальное — форма и пробелы.

### 2.1. Дефекты поведения

1. **Цикл опроса исходов заблокирован своими же признаками.** `Visible.holds` вызывает
   `driver.find(timeout_ms=query.timeout_ms)`, а `PlaywrightDriver.find` делает
   `wait_for(state="visible", timeout=5000)`. Один круг `OutcomeSet.settle` ждёт до 5 с на
   первом кандидате. Ловушка, ради которой делали опрос с общим дедлайном (`decisions.md`,
   спайк 2), вернулась через адаптер. То же: `ApiArrived.holds` (`wait_response` с таймаутом
   5 с), `At.check`, `Failure.check`, `Handle.apply`. Фейковый драйвер отвечает мгновенно —
   тесты этого не видят. Признакам нужен неблокирующий опрос (таймаут 0), ожидание
   принадлежит только циклу.
2. **`OutcomeOp.execute` не убирает помехи до действия.** `BrowserOp.execute` и
   `NetworkOp.execute` вызывают `handle(__handlers__)` до `run`/`act`; `OutcomeOp` — только
   внутри `settle`, после клика. Баннер согласия перекроет кнопку на самом частом типе
   операции.
3. **Буфер сети без водораздела.** `wait_response` отдаёт последний подошедший ответ за всю
   жизнь страницы; второй запуск той же операции прочитает прошлый ответ (`MERGE.md` §6
   п. 10 записывает это как «осознанный выбор», а лечится кодом: `open_network` в `execute`
   запоминает позицию буфера и принимает только ответы после неё). Буфер растёт без
   ограничения и держит тела в памяти.
4. **`any_of` ждёт кандидатов последовательно.** `Lazy.resolve` перебирает селекторы по
   одному, каждый с полным таймаутом: три запасных селектора — 15 с до отказа.

### 2.2. Пробелы протокола и декларации

5. **`Driver` не позволяет написать SDK без playwright.** Нет навигации (`goto`), коллекций
   элементов (только `first`/`last`), атрибутов, `select`, `hover`, `upload`, ожидания
   перехода, вкладок. Единственный выход — `PlaywrightDriver.page`, то есть утечка
   транспорта. При этом в «минимуме, который умеют все» лежит `set_html` для contenteditable.
6. **Декларация проверяется поздно и типом не связана.** `__content__: ClassVar[type[Any]]`
   не сверяется с `TContent`; поле без маркера падает при первом `execute`, не при импорте.
   В ядре такое ловится диагностиками уровня импорта (D-54-03 для `Payload`).
7. **`first_of` типизирован до трёх исходов**: четвёртый молча уходит в перегрузку с `Any`.
8. **`template()` подставляет значения через `str.format` без экранирования** — кавычка в
   значении ломает селектор.
9. **`PlaywrightDriver.find` глотает любое исключение как «не найдено».** Закрытая страница
   и упавший браузер выглядят как отсутствующий элемент.
10. **Опции вызова смешаны с входами операции.** `timeout_ms` — поле dataclass операции
    (`examples/browser_portal.py`); в ядре единственный зарезервированный аргумент —
    `options: CallOptions`. Таймаутов пять констант в четырёх модулях, все в миллисекундах;
    ядро считает секунды `float`.

### 2.3. Форма и именование

11. **Исключения вне иерархии ядра и с коллизиями имён.** `PageError(Exception)`,
    `NotReadyError(RuntimeError)`, `ElementNotFoundError(LookupError)` не наследуют
    `EazySdkError`; `ContentDeclarationError` — `TypeError`, а не `PlanError`.
    `CapabilityMismatchError` объявлен дважды (`eazy_sdk/handlers/profile.py:95` и
    `eazy_sdk_browser/profile.py:88`) с разными базами. `Query` и `Cookie` дублируют имена
    из корня `eazy_sdk` — автор SDK, импортирующий оба пакета в один файл, получит конфликт.
12. **Три словаря для одной вещи.** `BrowserOp.run`, `OutcomeOp.act` + `outcomes`,
    `NetworkOp.act` + `on`; подклассы переопределяют `run` заглушкой. У ядра у операции нет
    исполняемого тела вообще.
13. **Состояния собираются руками.** `CreateCompanyDialog.submit` создаёт
    `SubmitCompany(...)` и вызывает `execute(self.driver)` — открытый вопрос №8 `DESIGN.md`
    и главный разрыв с ядром, где операции публикуются через `op()`.
14. **Документация расходится с кодом.** Докстринг `__init__.py` показывает
    `session: Annotated[Driver, CurrentDriver()]`, названный ошибкой в `DESIGN.md` §7.
    Докстринг `integrations/accounts.py` показывает `open_workspace(...)` (см. §1.3).
    `DESIGN.md` §7 цитирует контракты для модулей `eazy_sdk_browser.page` и `.sdk`, которых
    нет. Признанные ранее: `TextContains` читает весь `body` каждый круг; `region` скоупится
    склейкой CSS и не работает для shadow DOM.

---

## 3. Единый стиль API

Принцип, который ядро применило к WebSocket: **одинаковая форма там, где понятие то же,
свой словарь там, где понятие другое, и тот же способ его записать.**

| Понятие | HTTP в ядре | WebSocket | Браузер сейчас | Предложение |
|---|---|---|---|---|
| Спека операции | `__http__ = Http.post(...)` | `__ws__ = Ws.call(...)` | `__at__`, `__content__`, `__scope__`, `__frame__`, `__errors__`, `__handlers__` | один `__browser__ = Browser.act(CompaniesPage, at=..., scope=..., frames=..., outcomes=..., errors=..., handlers=..., requires=...)` |
| База операции | `HttpOperation[T]` | `WsCall[T]`, `WsSubscribe[T]`, `WsSend` | `BrowserOp`, `OutcomeOp`, `NetworkOp` | `BrowserOperation[TContent, TResult]`; сеть и состояние — `requires=(Capability.network,)`, как `requires=` в `_HttpSpec`, а не наследованием |
| Публикация | `op(GetOrder)` на `AsyncApi` | `op(Lookup)` на `AsyncWsApi` | нет | `op(OpenCreateCompany)` на `AsyncBrowserApi` через `__publish__`; `api_group` в общем `AsyncRoot` |
| Вызов | `api.get_order(order_id=...)` | `api.lookup(name=...)` | `Op(...).execute(driver)` | `portal.open_create_company()`; драйвер приходит клиентом `AsyncBrowserClient(driver)`, как `AsyncClient(handler)` |
| Опции вызова | `options: CallOptions` | `WsCallOptions` | поле `timeout_ms` в операции | `BrowserCallOptions(timeout: float)`; секунды |
| Случаи результата | `Responses(success=(...), errors=(...))` | `Replies(...)`, `Messages(...)` | метод `outcomes()` → `first_of(when(...), otherwise=...)` | `Outcomes(cases=(when(...), ...), otherwise=...)` как часть спеки; кортеж вместо перегрузок на три случая |
| Отказ | `Error(status, Json(M), exception=cls_or_factory, condition=)` | `ErrorReply(...)` | `Failure(when=, raises=cls, detail=reader)` | `Failure(when=, exception=cls_or_factory)`; фабрика получает драйвер и заменяет `detail` |
| Общие отказы сервиса | `errors = SERVICE_ERRORS` на роутере | то же | `__errors__` на базовом классе операций | `errors = PORTAL_ERRORS` на `AsyncBrowserApi`, precedence 1 |
| Условия | `body.contains(...)`, `status(...)`, `header(...)`, `Predicate` с `label` | те же | `Visible(...)`, `TextContains(...)`, `UrlContains(...)` | `visible(css(...))`, `text.contains(...)`, `url.contains(...)`; тот же `Predicate` с операторами и `label` |
| Профиль транспорта | `HandlerProfile` с полями `CapabilityLevel` + `validate_profile` | нет | `BrowserProfile(levels=frozenset)`, `Level`, `require` | `BrowserProfile` с полями-осями, `CapabilityLevel` из ядра, `validate_profile` собирает все несовпадения; одно `CapabilityMismatchError` из `eazy_sdk.handlers` |
| Маркеры карты | `Query[int]`, `Header[str]` через `Annotated` | нет | `Annotated[Element, css(...)]` | оставить; переименовать `Query` → `Locator`, `Cookie` → `BrowserCookie` |
| Ошибки декларации | `PlanError` при импорте | `PlanError` | `TypeError` при первом запуске | `PlanError` в `__init_subclass__`/`__publish__`, коды диагностик D-B-xx |
| Ошибки транспорта | `TransportError(handler, phase, attempt, cause)` | то же | `None` из `find` | `TransportError` из ядра |
| Тестовые двойники | `RecordingHandler` с `assert_request` | — | `FakeDriver` с `log: list[str]` | оставить; добавить `assert_*` в стиле рекордера |

Своим остаётся и это нормально: карта элементов отдельным классом (спайк доказал, что
вложенный класс типизатор не берёт), `region`, `template`, `Handle` для помех,
состояние-переход `to=`. Аналогов в HTTP нет, поэтому здесь важна не одинаковость, а та же
манера записи: frozen dataclass, PEP 695, lowercase-фабрики там, где выражение читается как
вызов, `__all__` в каждом модуле, диагностики с именем класса и подсказкой.

Целевая форма объявления:

```python
class CompaniesPage:
    create_button: Annotated[Element, css('button[data-test="create"]')]
    filters: Annotated[FilterPanel, region(FilterPanel, root=css("form.filters"))]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenCreateCompany(BrowserOperation[CompaniesPage, CreateCompanyDialog]):
    __browser__ = Browser.act(
        CompaniesPage,
        at=css('button[data-test="create"]'),
        outcomes=Outcomes(
            cases=(when(visible(css('div[role="dialog"]')), to=CreateCompanyDialog),),
            otherwise=_dialog_missing,
        ),
    )

    async def act(self, content: CompaniesPage) -> None:
        await content.create_button.click()


class CompaniesPortal(AsyncBrowserApi):
    errors = PORTAL_ERRORS
    handlers = (COOKIE_BANNER,)

    open_create_company = op(OpenCreateCompany)
    submit_company = op(SubmitCompany)


class PortalSdk(AsyncRoot):
    api = api_group(CompaniesApi)          # HTTP, через BrowserHandler или httpx
    portal = api_group(CompaniesPortal)    # браузер
```

Порядок работ: сначала четыре поведенческих дефекта (§2.1, независимы от формы), затем
`__browser__`-спека и роутер с `__publish__` (меняет всё публичное API, поэтому раньше
остального), потом переименования, коллизии и иерархия ошибок, и только после этого
расширение протокола `Driver` навигацией и коллекциями. Подробно — `PLAN.md`.
