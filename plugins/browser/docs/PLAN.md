# План: браузерный плагин в форме `eazy-sdk`

Основание — `REUSE.md` (разбор от 2026-09-12). Документ ведётся по ходу работы: раздел
«Состояние» обновляется после каждого шага, остальное меняется только решением.

Правила ведения (те же, что в `docs/implementation/STATUS.md` корня):

* шаг закрывается только с доказательством — команда и её результат в таблице;
* шаги внутри этапа идут по порядку, этапы — по зависимостям из §1;
* никаких совместимых псевдонимов и второго пути исполнения: старая форма удаляется на
  том же шаге, где появляется новая, вместе с тестами и примером;
* новый код — по-русски, `frozen=True, slots=True, kw_only=True`, PEP 695, `__all__`.

Ворота на каждый шаг (из корня репозитория):

```bash
uv run pytest -q plugins/browser/tests
uv run mypy
uv run ruff check
uv run lint-imports
uv run complexipy --plain --failed
uv run basedpyright plugins/browser/probe/typing_probe.py   # смотреть, какие строки названы
```

Полные ворота перед закрытием этапа — дополнительно `uv run pytest -q` по всему
workspace и `uv run python scripts/docs_freshness.py check`.

---

## Состояние

| Этап | Шаг | Статус | Доказательство |
|---|---|---|---|
| B0 | B0.1 признаки без ожидания | complete | 2026-09-12: `Driver.peek`, `Observation` в `conditions.py`, `FakeDriver.latency_ms`; `test_waiting.py::test_later_outcome_is_reached_while_the_first_sign_is_absent` (второй исход через 100 мс при дедлайне 1 с) — `pytest -q plugins/browser/tests` 68 passed, Chromium не пропущен |
| B0 | B0.2 помехи до действия | complete | 2026-09-12: `OutcomeOp.execute` вызывает `handle` до `act` с общим `handled`; `test_waiting.py::test_banner_is_removed_before_the_action_and_only_once` (лог `click button.accept` раньше `click button.create`) |
| B0 | B0.3 водораздел буфера сети | complete | 2026-09-12: `NetworkAware.mark()`/`since`, `deque(maxlen=capture_limit)` в playwright, `Network.since`, `ApiValue.since`; `test_waiting.py::test_second_submit_reads_the_second_reply_not_the_first`, `::test_reply_that_arrived_before_the_action_is_not_its_answer`, `test_playwright_handler.py::test_capture_limit_evicts_old_replies_but_keeps_positions`; MERGE.md §6 п.10 переписан |
| B0 | B0.4 параллельный `any_of` | complete | 2026-09-12: `Driver.find(selectors: tuple)`, playwright через `or_()` с восстановлением порядка объявления; `test_waiting.py::test_any_of_does_not_pay_a_timeout_per_missing_candidate` (три кандидата < одного таймаута), `test_playwright_handler.py::test_candidates_are_waited_together_but_chosen_by_declaration` |
| B0 | B0.5 отказ транспорта ≠ «не найдено» | complete | 2026-09-12: в адаптере ловится только `playwright.async_api.TimeoutError`, остальное → `TransportError("playwright", phase, 1, cause)`; `test_playwright_handler.py::test_closed_page_is_a_transport_failure_not_a_missing_node`; `lint-imports` 4 kept, `mypy` 377 files ok, `ruff` ok, `complexipy` ok, пробник — только ожидаемые строки 47 и 69 |
| B1 | B1.1 `_BrowserSpec` и `Browser.act` | complete | 2026-09-13: `spec.py` (`_BrowserSpec[TContent]` по группам полей, `Browser.act`), `__browser__` — единственный dunder; `__at__/__content__/__scope__/__frame__/__errors__/__handlers__` удалены; `examples/browser_portal.py` в новой форме; `pytest plugins/browser/tests` 91 passed |
| B1 | B1.2 `Outcomes` кортежем, `Failure(exception=)` | complete | 2026-09-13: `outcomes.py` — `Outcomes(cases, otherwise)`, фабрика `outcomes(*cases, otherwise=)` с перегрузками до 6 случаев, `first_of` удалён; `Failure(when=, exception=cls_or_factory)`, `raises=/detail=` удалены; пробник: `four` → `Outcomes[CompaniesPage, _A | _B | _C | _D]`, `five` → `… | _E` |
| B1 | B1.3 одна база `BrowserOperation` | complete | 2026-09-13: `BrowserOperation[TContent, TResult]` с единственным `act`; `BrowserOp/OutcomeOp/NetworkOp`, `Network`, `open_network` удалены; раннер — функция `execute(operation, driver, *, options: BrowserCallOptions)`; `test_waiting.py::test_call_options_set_the_settle_deadline` |
| B1 | B1.4 диагностики при импорте | complete | 2026-09-13: `tests/test_declarations.py` — D-B-01, 02, 03 (и внутри `region`), 04 (оба направления), 05, 08; mypy 382 files ok, ruff ok, complexipy ok, lint-imports 4 kept |
| B2 | B2.1 `AsyncBrowserClient` и `BrowserCallOptions` | complete | 2026-09-13: `client.py` — `BrowserClientConfig(timeout, element_timeout, response_timeout)`, `BrowserCallOptions`, `AsyncBrowserClient(driver, *, config, owns_driver)` с `aclose()`, `execute(operation, driver, *, config, options)`; константы `*_TIMEOUT` удалены, `Locator.timeout`/`ApiResponse.timeout` = `None` → из клиента (`Marker.timed`); `test_api.py::test_client_config_sets_the_default_timeouts_and_options_override`, `::test_client_closes_only_the_driver_it_owns` |
| B2 | B2.2 дескриптор, `__publish__`, `AsyncBrowserApi` | complete | 2026-09-13: `api.py` — `_BrowserOperationDescriptor` (D-B-06 в `__set_name__`), `_BoundBrowserOperation` (`__call__`, `.request`, `.send`, `.Operation`), `BrowserOperation.__publish__` → `op()` ядра, `AsyncBrowserApi(client, *, defaults)` с `errors/handlers/frames` по MRO, D-B-07; `test_api.py` (порядок правил операция→роутер, `inherit_errors=False`, обработчики, фреймы) |
| B2 | B2.3 `api_group` в `AsyncRoot` | complete | 2026-09-13: ядро — протокол `_ComposesItself.__compose__(client)` в `eazy_sdk/api.py`, `api_group` и `AsyncRoot` пропускают такой роутер мимо HTTP-резолва (`tests/unit/test_foreign_routers.py`, гайд `multi-service.mdx` обновлён); `PortalSdk` в примере; `test_api.py::test_root_composes_http_and_browser_routers_over_one_driver`, `test_playwright_handler.py::test_one_root_serves_requests_and_clicks` на Chromium |
| B2 | B2.4 состояния-переходы получают роутер | complete | 2026-09-13: `FromApi` вместо `FromDriver`, `When.build(content, api)`, `CreateCompanyDialog(api: CompaniesPortal).submit → api.submit_company`; `test_api.py::test_state_outcome_holds_the_router_that_opened_it`, `::test_state_outcome_needs_a_router_not_a_bare_driver`; ворота: plugin 137 passed (с тестами композиции ядра), mypy 386 files ok, ruff/complexipy/lint-imports ok, пробник — ожидаемые строки 38/50/72 |
| B3 | B3.1 иерархия ошибок от `EazySdkError` | complete | 2026-09-13: `errors.py` (`BrowserError(EazySdkError)`, `BrowserDeclarationError(PlanError)`), `PageError/NotReadyError/ElementNotFoundError/ResponseMissingError/PageFetchError` под `BrowserError`; `test_unification.py::test_runtime_failures_descend_from_the_core_root`, `::test_declaration_failures_are_plan_errors` |
| B3 | B3.2 переименования и коллизии | complete | 2026-09-13: `Locator`, `TemplateLocator`, `BrowserCookie`, `CapabilityMismatchError`/`CapabilityLevel` из ядра, `timeout: float` (секунды) везде; `test_unification.py::test_public_names_do_not_collide_with_the_core` (`eazy_sdk.__all__ ∩ eazy_sdk_browser.__all__ = ∅`) |
| B3 | B3.3 профиль в форме `HandlerProfile` | complete | 2026-09-13: `BrowserProfile(name, network=..., ...)` по осям, `validate_profile(requires, profile)` собирает все несовпадения; `profile()/require/with_level/Level` удалены; `test_unification.py::test_profile_reports_every_missing_capability_at_once`, `test_playwright_handler.py::test_profile_matches_what_the_adapter_really_does` зелёный |
| B3 | B3.4 язык признаков как `Predicate` | complete | 2026-09-13: `Sign.label`, фабрики `visible()`, `text.contains()`, `url.contains()/matches()`, `response.arrived()`; классы признаков приватные; `test_unification.py::test_signs_read_as_their_labels`; ворота: 81 passed, mypy 379 files ok, ruff ok, lint-imports 4 kept, complexipy ok, пробник — ожидаемые строки 35/47/69 |
| B4 | B4.1 навигация и ожидание перехода | complete | 2026-09-13: `Driver.goto(url, *, wait, within)` в минимуме протокола; `Browser.goto(url, [Content], at=, wait=, ...)` — второй глагол (`_BrowserSpec.navigation`), адрес из полей операции от `AsyncBrowserClient(base_url=)`, D-B-09 (у перехода нет `act`), D-B-10 (`{имя}` адреса — поле); переход после действия — `NavigationAware.navigations()` и признак `navigated()` за `Capability.navigation_events`; `Sign.requires` + `requirements_of` — возможности признаков, в том числе правил роутера, сверяются до действия, D-B-04 на сеть и переходы; `OpenCompanies` в примере; `tests/test_navigation.py` (17), `test_portal.py::test_list_is_opened_by_address_and_a_login_redirect_is_session_expiry`, на Chromium `test_playwright_handler.py::test_goto_operation_opens_the_portal_and_waits_for_readiness`, `::test_navigation_is_an_event_even_to_the_same_address`, `::test_unreachable_address_is_a_transport_failure`; ворота: 135 passed (плагин + композиция ядра, Chromium не пропущен), mypy 388 files ok, ruff/format/complexipy ok, lint-imports 4 kept, docs_freshness 67 fresh, пробник — ожидаемые строки 38/50/72, `Browser.goto("/companies")` → `_BrowserSpec[None]` |
| B4 | B4.2 коллекции элементов и атрибуты | complete | 2026-09-13: `Element` — `attribute`, `value`, `select`, `hover`, `check`, `uncheck`; `Driver.peek_all(locator)` — видимые узлы первого кандидата без ожидания (playwright — `filter(visible=True)` + `nth`); маркер `each(locator)` → `Elements` с `count()`, `nth(i)` (действие ждёт, `-1` — последний), `texts()`, `async for`; `ElementNotFoundError.index`; фейк — `counts`, `values`, `attributes`, `FakeElement(selector, page)`; `tests/test_collections.py` (9), на Chromium `test_playwright_handler.py::test_collection_counts_visible_rows_in_document_order`, `::test_element_selects_checks_hovers_and_reads_values`; ворота: 146 passed (плагин + композиция ядра, Chromium не пропущен), mypy 389 files ok, ruff/format/complexipy ok, lint-imports 4 kept, docs_freshness 67 fresh, пробник — ожидаемые строки 39/51/73, `texts()` → `list[str]`, узел обхода → `Element` |
| B4 | B4.3 экранирование `template` | complete | 2026-09-14: `css_escape` (алгоритм `CSS.escape`, сверен с браузером), `text_escape` и маркер `template_text` для текстовых движков playwright (`text="…"`, `:has-text("…")`); `Template` подставляет значения экранированными и требует ровно имена шаблона; `placeholders` — общий разбор `{имён}` для шаблонов и адресов `Browser.goto`, неверный шаблон — `BrowserDeclarationError` при объявлении; `tests/test_templates.py`, на Chromium `test_playwright_handler.py::test_css_escape_matches_the_browser_css_escape`, `::test_templates_find_values_with_quotes_on_real_dom`; ворота — в строке B4.4 |
| B4 | B4.4 `set_html` из минимума в возможность | complete | 2026-09-14: `set_html` удалён из `Element`, `Lazy` и фейкового элемента; `Capability.rich_text`, `RichTextAware.set_html(locator, html) -> bool`, маркер `rich(locator)` → `RichText` (элемент плюс `set_html`), отказ при сборке карты; маркеры называют свою возможность (`requires`), D-B-04 на `rich_text`; фейк `RichTextFakeDriver`/`RichTextLiar`; `tests/test_rich_text.py` (8), на Chromium `::test_rich_text_puts_markup_and_tells_the_editor`; ворота: 172 passed (плагин + композиция ядра, Chromium не пропущен), mypy 392 files ok, ruff/format/complexipy ok, lint-imports 4 kept, docs_freshness 67 fresh, пробник — ожидаемые строки 39/51/73 |
| B5 | B5.1 вход через `session_lifecycle` | complete | 2026-09-14: `login.py` — `BrowserLogin(credentials, service, store, identity, cookies, expired, retries, leeway, clock)` строит `SessionLifecycle` ядра, `BrowserLoginContext(client без входа, graph)`, `_Relogin` вместо обязательного `refresh`; `AsyncBrowserClient(login=)` — сессия до первой операции (`import_state`), повторный вход через `refresh_revision` и повтор `retries` раз, `sign_in()`; `tests/test_login.py` (8 тестов входа) |
| B5 | B5.2 `SessionStore` поверх `AccountWorkspace` | complete | 2026-09-14: `BrowserSessions.store(account)` → `RepositorySessionStore` из `eazy_sdk_accounts` (`_AccountSessionData` + `BrowserStateCodec`), ревизия и ключ в `SessionData.meta`; докстринг про `open_workspace` исправлен; `test_browser_sessions.py::test_store_keeps_the_revision_and_the_key_in_session_meta`, `::test_store_invalidates_only_the_revision_it_was_asked_about`, `::test_store_does_not_serve_a_session_saved_under_another_key`, `::test_login_persists_its_session_into_the_account_workspace` |
| B5 | B5.3 мост в HTTP-`Auth` | complete | 2026-09-14: `BrowserCookieBridge` (`SessionBridge[BrowserState, HttpCookieSession]`), `CookieAuthAdopter` (`CookieScheme(...).static`), `browser_cookie_auth` через `BridgedSessionAdopter`; `test_login.py::test_http_router_sends_the_cookie_of_the_browser_session` (`AsyncRecordingHandler` получил `Cookie: sid=s1`), `::test_bridge_picks_the_cookie_by_name_and_then_by_domain`, `::test_expired_browser_cookie_is_not_handed_to_http`; ворота: 187 passed (плагин + композиция ядра), mypy 394 files ok, ruff/format/complexipy ok, lint-imports 4 kept (списки модулей расширены), docs_freshness 67 fresh, пробник — ожидаемые строки 39/51/73 |
| B6 | B6.1 документы и пример | complete | 2026-09-14: `README.md`, `OVERVIEW.md`, `DESIGN.md` переписаны под текущую форму (§6 закрывает вопросы 8, 9, 11 и даёт таблицу D-B-01…10, §7 — настоящие контракты из `pyproject.toml`), `decisions.md` помечен историческим; `examples/browser_portal.py`, докстринг `__init__.py` без `CurrentDriver` и пробник с 4–5 исходами и переходом через роутер — уже в целевой форме после B1–B4; docs-site: `guides/browser/index.mdx`, `outcomes.mdx`, `login.mdx`, `api-reference/browser.mdx`, оглавление и хабы; примеры страниц прогнаны на Chromium, вывод сверен (`['ООО Ромашка', 'АО Лютик']`, четыре варианта входа, `Cookie: sid=s1`); `docs_freshness` — 71 fresh, `validate_docs.py` — 86 pages OK, `sphinx-build -W` — единственное предупреждение чужое и было до B6 (`guides/pagination.mdx` вне оглавления) |
| B6 | B6.2 долг документации ядра | complete | 2026-09-14: `docs/CORE_DEBT.md` — 8 открытых пунктов долга ядра с местом в коде, помехой и способом починки (`SuccessOutcome`, `BAN`/`FREEZE`/`Order`, две модели куки, `open_workspace` → `SqlAccountWorkspace`, два `SessionCodec`, `account: Any`, cookie-сессия без HTTP-входа, `op()` без `ParamSpec`) и закрытый пункт `.gitignore` (`!plugins/*/docs/*.md`) |
| B7 | B7.1 оси оркестрации вон из профиля | complete | 2026-09-23: план и доказательства — `LAYERS.md` §6 |
| B7 | B7.3 состояние: выгрузка, куки, `to_storage_state` | complete | 2026-09-23: `LAYERS.md` §6 |
| B7 | B7.2 `BrowserLogin` — объявление, `BrowserSession` — один объект на контекст | complete | 2026-09-23: `LAYERS.md` §6 |
| B7 | B7.4 драйвер живёт со страницей | complete | 2026-09-23: `LAYERS.md` §6 |
| B7 | B7.5 `CapturePolicy` | complete | 2026-09-23: `LAYERS.md` §6 |
| B7 | B7.6 `test_layers.py` и `browser-core-does-not-orchestrate` | complete | 2026-09-23: `LAYERS.md` §6 |
| B7 | B7.7 документы | complete | 2026-09-23: `LAYERS.md` §6 |
| B7 | B7.8 полные ворота и сверка §0 | blocked | 2026-09-23: одно падение workspace вне B7 (`test_phase10_absence`, `docs/implementation/STATUS.md`) — `LAYERS.md` §6 |

Статусы: `pending` / `active` / `blocked` / `complete`. Блокировка — с причиной в колонке
доказательства.

Отклонения от текста шагов, принятые по ходу (B0):

* `At.check` не опрашивает `peek` по кругу, а делает один `find` по кортежу кандидатов
  (B0.4): ожидание элемента — работа драйвера, и у playwright оно событийное, а не опросом.
  Дедлайн — `timeout_ms` самого запроса, а не `SETTLE_TIMEOUT_MS`.
* Снимок `Observation` (драйвер + водораздел `since` + кэш адреса и текста на круг) введён
  уже в B0, а не в B3.4: без него `ApiArrived` некуда передать водораздел. B3.4 остаётся
  за `label` и фабриками в нижнем регистре.
* `NetworkFakeDriver.reply` заменён на `replies` (буфер) и `answers` (ответы, которые
  выпускает клик по селектору): ответ, лежащий в буфере до действия, водоразделом
  отсекается — фейк обязан воспроизводить порядок «действие → ответ».

Отклонения, принятые в B3:

* Контракт `browser-core-does-not-know-integrations` больше не запрещает ядру плагина
  `eazy_sdk`: ошибки, профиль и шкала — общие, без импорта их не взять. Запрет на
  `eazy_sdk_accounts`, `integrations` и `handlers` остаётся. `eazy-sdk` стал обязательной
  зависимостью плагина (экстра `eazy-sdk` удалена, `extras_smoke.py` обновлён).
* Признак ответа — `response.arrived(...)`, а не `api.arrived(...)`: `api` уже есть в
  `eazy_sdk.__all__`, и модуль `eazy_sdk_browser.api` появится в B2.2.
* `Driver.find`/`Driver.peek` принимают `Locator` целиком, а не `selectors/pick/timeout/
  frames` по отдельности; параметр ожидания у `wait_response` — `within`, не `timeout`:
  ruff ASYNC109 запрещает `timeout` у async-функций, а ядро это правило только
  игнорирует по долгу. Поля объявлений (`Locator.timeout`, `OutcomeSet.timeout`,
  `PageRequest.timeout`) — по-прежнему `timeout`.
* `ContentDeclarationError` удалён без подкласса: карта без маркера поднимает
  `BrowserDeclarationError` с именем поля в сообщении (в B1.4 станет D-B-03).
* `Level.supported` отображён в `CapabilityLevel.CAPTURE_VERIFIED`, `best_effort` — в
  `BEST_EFFORT`.
* README, OVERVIEW и DESIGN плагина ещё называют старые имена (`Query`, `Visible`,
  `timeout_ms`); правятся в B6.1 вместе с формой операции.

Отклонения, принятые в B1:

* D-B-01 (нет `__browser__`) и D-B-05 (не frozen) проверяются не в `__init_subclass__`,
  а в `declaration_of()` — при `execute` и, с B2.2, при `__publish__`: декоратор
  `@dataclass` применяется **после** создания класса, и в `__init_subclass__` ещё не
  видно ни `frozen`, ни того, что база без спеки — абстрактная. D-B-02/03/04 при импорте.
* Добавлен D-B-08: `TResult` класса против union исходов из `outcomes=` (по аннотациям
  `to=`/`then=`/`otherwise=`, `NoReturn` не считается); без исходов `TResult` обязан
  быть `None`. Типизатор эту связь не видит — `ClassVar` не может нести параметр класса.
* Дедлайн ожидания исхода ушёл из объявления в `BrowserCallOptions.timeout` уже сейчас
  (`operations.py`); B2.1 переносит его в `client.py` и добавляет остальные поля.
* `handled` живёт в `Observation` (снимок операции), а не отдельным аргументом;
  `Observation.next_round()` даёт снимок следующего круга с тем же `since` и `handled`.
* Фасад `Network`/`open_network` удалён вместе с `NetworkOp`: ответ читается только
  маркером карты `ApiResponse`, потребность — `requires=(Capability.network,)`.

Отклонения, принятые в B2:

* Правка ядра ради B2.3 — не пропуск в `_validate_router`, а протокол `_ComposesItself`
  (`__compose__(client) -> Self`) рядом с `_PublishesItself`: `api_group` принимает такой
  класс, `_RootBase` не проверяет его вид, `_resolve_plan` не читает его сервисные
  атрибуты и не резолвит как HTTP, `_build_group` вызывает `__compose__`. Без него
  корень падал уже на `__init_subclass__` («wrong API kind»), а не в `_validate_router`.
  Тест ядра — `tests/unit/test_foreign_routers.py`, гайд — `guides/multi-service.mdx`.
* `op(Operation)` ядра теряет сигнатуру конструктора: `__publish__` — метод класса, и
  `ParamSpec` конструктора ему недоступен; тип результата доезжает (`reveal_type(dialog)`
  — `CreateCompanyDialog`), поля вызова — `...`. Точная сигнатура есть у
  `eazy_sdk_browser.api.publish`. То же у WebSocket ядра; записано в B6.2.
* Параметр ожидания `Outcomes.settle` и раннер получают контекст `Call(timeouts, within,
  api)` одним значением (ruff PLR0913); `BrowserCallOptions` переехал в `client.py`.
* `Locator.patience` — таймаут для адаптера (свой или умолчание `DEFAULT_TIMEOUTS`),
  потому что локатор может прийти к драйверу и вне клиента.
* Состояние с `to=` из голого `execute()` не построить: `BrowserError` с указанием
  вызывать через роутер. `FromApi` объявлен как `__init__(self, api: AsyncBrowserApi)`;
  состояние примера сужает тип до `CompaniesPortal` — typing-совместимость проверяется
  пробником (`reveal_type(dialog)`).

Отклонения, принятые в B4.1:

* Вместо `wait_for_location(predicate, *, timeout)` — счётчик `NavigationAware.navigations()`
  и признак `navigated()`. Признаки не ждут (B0.1), и ожидающему методу протокола не
  нашлось вызывающего: новый адрес и так видят `url.*` в цикле `settle`. Чего опрос не
  видит — старой страницы сразу после клика и перезагрузки того же адреса, — то закрывает
  водораздел, как у сети (B0.3): `navigated() & visible(css("form.login"))` не совпадёт на
  странице, с которой ещё не ушли. Playwright считает `framenavigated` главного фрейма;
  драйвер, который видит переход только опросом, ось не объявляет.
* `Driver.goto` получил `within` — таймаут перехода из клиента
  (`BrowserClientConfig.navigation_timeout`, 30 с, и `BrowserCallOptions.navigation_timeout`);
  отказ сети и истёкший срок — `TransportError("playwright", "goto", ...)`.
* `Browser.goto(url)` без карты объявляется `BrowserOperation[None, ...]`; карта вторым
  позиционным нужна, только если исходы читают страницу. База адресов — у клиента
  (`AsyncBrowserClient(driver, base_url=)`, как у `AsyncClient`); значение поля кодируется
  целиком (`quote(safe="-._~")`, как параметр пути в ядре); относительный адрес без базы —
  `BrowserDeclarationError` при вызове. Новые коды: D-B-09 (у перехода нет `act`) — при
  импорте, D-B-10 (`{имя}` адреса — простое имя поля операции) — при публикации.
* Невыполненное условие готовности сначала спрашивает правила отказа — у обоих глаголов:
  кнопки нет, потому что портал увёл на вход, — это `SessionExpiredError`, а не
  `NotReadyError`. У `goto` правила проверяются ещё и сразу после перехода, не дожидаясь
  таймаута `at`.
* `Sign.requires`: возможности признаков в правилах, помехах и исходах — в том числе в
  правилах роутера — раннер сверяет с профилем до действия (`requirements_of`). D-B-04
  обобщён на сеть и переходы: маркеры карты и признаки операции против `requires=`.
* Водоразделы у `act` берутся после условия готовности (ответы, пришедшие пока ждали
  кнопку, — не ответ на клик), у `goto` — до перехода (ответы загрузки — его следствие).

Отклонения, принятые в B4.2:

* Фейк: `present` остался множеством, число узлов — отдельный `counts: dict[str, int]`
  (путь из `present` без записи — один узел): перевод `present` в словарь переписал бы
  каждый тест ради одной оси. Добавлены `values` и `attributes`; `FakeElement(selector,
  page)` вместо `(selector, log, on_click)`, реакция на клик — публичный
  `FakeDriver.on_click`.
* Playwright — не `locator.all()`, а `filter(visible=True)` + `count()` + `nth(i)`: `all()`
  отдаёт и скрытые узлы, и скрытая строка-шаблон сдвинула бы номера. Коллекция — только
  видимые узлы, как у `find` и `peek`.
* Метод протокола — `peek_all`, и он не ждёт: пустая таблица — тоже ответ. Ждёт только
  действие над `nth(i)` — опросом `peek_all` до таймаута локатора (`NTH_POLL`), потому что
  номер среди видимых одним событием драйвер не выразит. Отрицательный номер — с конца.
* К `check()` добавлен `uncheck()`: снять галочку — такое же действие формы.
* Пробник: импорт коллекций отдельной строкой сдвинул ожидаемые ошибки на строки 39/51/73.

Отклонения, принятые в B4.3:

* `template("text={subject}")` из текста шага — неверная пара: CSS-экранирование ломает
  текстовый движок (пробел становится `\ `). Текстовые шаблоны — только `template_text`, и
  кавычки вокруг подстановки пишет автор (`text="{subject}"`); тест в `test_core.py`
  переведён. `text_escape` схлопывает пробельные символы: перевод строки в CSS-строке
  `:has-text()` и в JSON-строке `text="…"` экранируется по-разному, а текстовое сравнение
  playwright пробелы всё равно нормализует.
* Разбор `{имён}` (`placeholders`) один на шаблоны селекторов и адреса `Browser.goto` —
  переехал из `navigation.py` в `locators.py`. Шаблон проверяется при объявлении
  (`BrowserDeclarationError`), вызов — на точный набор имён (`TypeError`).

Отклонения, принятые в B4.4:

* `set_html` — метод драйвера (`RichTextAware.set_html(locator, html) -> bool`), а не
  элемента: у протокольного `Element` его больше нет, а `RichText` — это `Lazy` с
  `set_html`, так что редактор по-прежнему умеет `click` и `text`.
* Кроме отказа при сборке карты — D-B-04: маркер `rich(...)` в карте требует
  `requires=(Capability.rich_text,)`, и драйвер отсекается ещё до условия готовности. Для
  этого маркеры называют свою возможность свойством `requires` (у `ApiResponse` — сеть), и
  проверка требований больше не знает `ApiResponse` поимённо.

Полные ворота этапа B4 (2026-09-14) выполнены не целиком: `docs_freshness` — 67 fresh, а
`uv run pytest -q` по workspace не завершился ни разу из пяти попыток — на этой машине
`socket.socketpair()` виснет в `accept()` при создании цикла asyncio, до кода теста
(таймаут pytest; первыми попадают HTTP-тесты ядра в `tests/integration/auth`, без них —
любой async-тест). Вне pytest тот же вызов тоже виснет, по отдельности файлы то проходят,
то нет. Ядро в B4 не менялось; прогон повторить на исправной машине до начала B6.

Отклонения, принятые в B5:

* Вход — не `integrations/login.py`, а `eazy_sdk_browser/login.py`: `login=` принимает
  клиент, то есть ядро плагина, а контракт `browser-core-does-not-know-integrations`
  запрещает ядру импорт интеграций. Модуль зависит только от `eazy_sdk.auth`.
* Не `session_lifecycle(...)` ядра, а объявление `BrowserLogin(...)`, из которого клиент
  строит `SessionLifecycle` сам: контексту входа нужен клиент, клиенту — вход, и собрать
  одно раньше другого нельзя. К тому же `session_lifecycle` не принимает свою проверку,
  а браузерной сессии нужна проверка кук (`cookies=`, `leeway`).
* Контекст входа — клиент без входа на том же драйвере (`BrowserLoginContext.client`):
  операции входа не требуют входа, и рекурсии нет без обхода графа.
* Что значит «сессия умерла», объявляет вход кортежем `expired=` (исключения правил
  отказа), а не библиотека по имени `SessionExpiredError`. `refresh` сервиса не обязателен:
  без него `refresh_revision` входит заново (`_Relogin`). `sign_in()` у клиента — войти
  заранее и получить `BrowserState`, например для моста в HTTP.
* `BrowserSessions.store(account)` — не своя обёртка, а готовый `RepositorySessionStore`
  из `eazy_sdk_accounts` с репозиторием одного аккаунта и кодеком состояния; кроме
  ревизии, в `meta` лежит ключ сессии, и запись под чужим ключом не отдаётся.
* Цель моста в HTTP — `CookieAuthAdopter`, который строит `Auth` публичным
  `CookieScheme(...).static(value)`. У ядра нет публичной cookie-привязки, принимающей
  сессию без собственного HTTP-входа (`session_cookie` читает `Set-Cookie` из HTTP-ответов,
  `session_auth` требует поле `Bearer`), а `Auth._bind` приватный. Цена — статический
  `Auth`: после повторного входа в браузере мост проходят заново. Записано в B6.2.
  `browser_cookie_auth(state, scheme, name)` делает оба шага `BridgedSessionAdopter` и
  отдаёт `Auth` с типом (у `adopt` ядра тип результата — `object`).
* Контракты import-linter дополнены модулями ядра плагина из B1–B5 (`api`, `client`,
  `errors`, `interceptors`, `login`, `navigation`, `outcomes`, `rich_text`, `spec`): до
  этого они не были ни под одним контрактом.

Полные ворота этапа B5 (2026-09-14) выполнены не целиком, по той же причине, что в B4:
`docs_freshness` — 67 fresh, а `uv run pytest -q` по workspace прерван таймаутом на 43-м
тесте. Упал и завис HTTP-тест ядра
`tests/integration/auth/test_adapter_matrix.py::…[httpx-sync-basic]`: цикл asyncio ждёт
ответа localhost в `GetQueuedCompletionStatus`. Плагин эти тесты не импортируют, ядро в
B5 не менялось; прогон повторить на исправной машине до начала B6.

Отклонения, принятые в B6:

* Сайт документации — Sphinx (myst, тема `shibuya`), а не Astro: `npm run check` и
  `npm run build` из текста шага на деле — `docs-site/scripts/validate_docs.py` и
  `sphinx-build -W` по `docs-site/UPDATING.md`. Сборка с `-W` падала и до B6 на
  `guides/pagination.mdx` (страница не включена в оглавление); чужая страница не тронута.
* Раздел «Браузер» — не одна страница, а три руководства и страница API: большие страницы
  docs-site делятся на подстраницы. Примеры — полные скрипты на встроенном HTML, прогнанные
  на Chromium, с дословным выводом.
* `OVERVIEW.md` и `DESIGN.md` переписаны шире §2–§3 и §3–§7: устаревшие имена стояли и в
  §5, §7, §8, §10 (`raises=`, `__errors__`, `NetworkOp`, «стоимость признаков»). История —
  §1, §4, §9 OVERVIEW и §9–§10 DESIGN — сохранена с пометкой.
* Пример, докстринг `__init__.py` и пробник в B6 не менялись: целевую форму они получили по
  ходу B1–B4.2, и повторять правку ради строки в плане незачем.
* Долг ядра — отдельный документ `CORE_DEBT.md`, а не абзац плана: у каждого пункта место в
  коде, помеха и способ починки. Пункт про `.gitignore` оказался уже закрыт; добавлены два
  пункта, найденные в B2 и B5.
* Пятый контракт `browser-api-does-not-know-drivers` из определения готовности не заведён:
  `api` и `client` с B5 входят в оба контракта ядра плагина (`DESIGN.md` §7), отдельный
  контракт ничего бы к ним не добавил.

Сверка с определением готовности (2026-09-14):

1. Все шаги в «Состоянии» — `complete` с доказательством. **Выполнено.**
2. `uv run pytest -q` по workspace зелёный, браузерные интеграционные тесты проходят на
   Chromium. **Не подтверждено:** тесты плагина на Chromium проходят (187 passed), а полный
   прогон workspace на машине разработки не завершается — см. записи о полных воротах B4 и B5.
3. Контракты `lint-imports` держатся. **Выполнено**, четыре; пятый признан лишним (выше).
4. Пересечений `__all__` между `eazy_sdk` и `eazy_sdk_browser` нет. **Выполнено**
   (`test_unification.py::test_public_names_do_not_collide_with_the_core`).
5. `examples/browser_portal.py` работает на фейке и на Chromium и содержит общий `AsyncRoot`.
   **Выполнено** (`test_portal.py`, `test_playwright_handler.py::test_one_root_serves_requests_and_clicks`).
6. Правок в `eazy_sdk/` ради плагина нет, кроме пропуска чужого роутера (B2.3). **Выполнено:**
   в ядре изменены только `eazy_sdk/api.py` и `eazy_sdk/root.py` — протокол `_ComposesItself`.

---

## 1. Этапы и зависимости

```
B0 дефекты поведения ──┐
                       ├─→ B1 форма операции ─→ B2 роутер и клиент ─→ B4 протокол драйвера
                       │                                   │
                       └─→ B3 имена и ошибки ←─────────────┘
                                                            └─→ B5 вход и хранилище ─→ B6 документы
                                                                                        │
                                                   B7 граница слоёв (`LAYERS.md`) ←──────┘
```

B0 и B3 не зависят от формы и могут идти первыми; B1 раньше B2; B4 после B2, потому что
навигация становится операцией роутера; B5 после B2, потому что вход — это операция.
B7 — после B6, по внешнему ревью: плагин знает сайт, а не среду. Его план, шаги и
доказательства ведутся в `LAYERS.md` (обоснование входа — `LOGIN_SCOPE.md`); в таблице
выше — только строки со ссылкой туда.

---

## B0. Дефекты поведения

Цель: сценарий, который сегодня зависает или читает чужой ответ, начинает работать. Форма
API не меняется.

### B0.1. Признаки не ждут — ждёт цикл

`conditions.py`, `operations.py`, `driver.py`, `handlers/playwright.py`, `testing.py`.

1. В протокол `Driver` добавить `peek(selector, *, pick, frames) -> Element | None` —
   мгновенная проверка без ожидания. В playwright — `locator.is_visible()` без `wait_for`.
2. `Visible.holds`, `At.check`, `Failure.check`, `Handle.apply`, `text_of` переводятся на
   `peek`. `find` с таймаутом остаётся только у `Lazy.resolve` — то есть у действий.
3. `ApiArrived.holds` вызывает `wait_response(timeout_ms=0)`: ответ либо уже в буфере,
   либо цикл придёт снова.
4. `At.check` получает собственный дедлайн (`SETTLE_TIMEOUT_MS`) и опрашивает `peek` по
   кругу, а не делегирует ожидание драйверу.
5. `FakeDriver` получает задержку `latency_ms`, которую `find` честно выжидает, — иначе
   регрессия невидима. Тест: два исхода, первый не наступает, второй наступает через
   100 мс, дедлайн 1 с; до правки второй недостижим.

Выход: тест на фейке с задержкой зелёный; интеграционный `test_full_scenario_on_real_browser`
зелёный; время `settle` при отсутствующем первом признаке не превышает дедлайн.

### B0.2. Помехи убираются до действия

`operations.py`. `OutcomeOp.execute` вызывает `handle(__handlers__, driver, handled)` до
`act`, тем же `handled`, что потом уходит в `settle` (чтобы `once=True` считался на всю
операцию, а не на фазу). Тест: баннер в `present` до клика, лог драйвера показывает
`click accept` раньше `click create`.

### B0.3. Водораздел буфера сети

`network.py`, `handlers/playwright.py`, `testing.py`.

1. `NetworkAware` получает `mark() -> int` (текущая позиция буфера) и
   `wait_response(url_contains, *, timeout_ms, since: int = 0)`.
2. `open_network(driver)` вызывает `mark()` при создании; `Network.wait_response` передаёт
   `since`. `ApiResponse.bind` делает то же при сборке карты.
3. Буфер — `collections.deque(maxlen=...)` с параметром `capture_limit` у
   `PlaywrightDriver`; позиция считается монотонно, независимо от вытеснения.
4. Тест: два подряд `execute` одной операции с двумя ответами на один адрес; второй
   получает второй ответ. Тест: ответ, пришедший до `mark()`, не виден.

Убрать из `MERGE.md` §6 п. 10 формулировку «лечится выбором».

### B0.4. `any_of` не ждёт кандидатов по очереди

`locators.py`, `driver.py`, `handlers/playwright.py`. `Driver.find` принимает
`selectors: tuple[str, ...]` и возвращает первый появившийся; playwright — через
`locator.or_()` для кандидатов, порядок предпочтения сохраняется выбором среди видимых.
`Lazy.resolve` делает один вызов. Тест на фейке с задержкой: три кандидата, найден
третий, время ≤ одного таймаута.

### B0.5. Отказ транспорта — не «не найдено»

`handlers/playwright.py`. В `find`/`peek` ловится только `playwright.async_api.TimeoutError`;
остальное заворачивается в `TransportError(handler="playwright", phase=..., attempt=1,
cause=...)` из `eazy_sdk.handlers` (импорт в адаптере допустим — ядро не трогается,
контракт `browser-core-does-not-know-integrations` касается только ядра плагина).
Тест: закрытая страница → `TransportError`, не `ElementNotFoundError`.

---

## B1. Форма операции

Цель: одна спека, одна база, кортеж исходов, диагностики при импорте.

### B1.1. `_BrowserSpec` и фасад `Browser`

`operations.py` → разнести: `spec.py` (спека и фасад), `operations.py` (база и раннер).

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class _BrowserSpec[TContent]:
    content: type[TContent]
    # Где операция работает.
    at: Query | None = None
    scope: Query | None = None
    frames: tuple[str, ...] = ()
    # Что приходит назад.
    outcomes: Outcomes[Any, Any] | None = None
    errors: FailureRules = ()
    inherit_errors: bool = True
    handlers: Handlers = ()
    # Что операции нужно от драйвера.
    requires: tuple[Capability, ...] = ()
    # Имена для инструментов.
    operation_id: str | None = None
    tags: tuple[str, ...] = ()


class Browser:
    __slots__ = ()

    @staticmethod
    def act[TContent](content: type[TContent], /, **options) -> _BrowserSpec[TContent]: ...
```

Порядок полей — по группам, как в `_HttpSpec`. `__browser__: ClassVar[_BrowserSpec[Any]]`
— единственный dunder операции; `__at__`, `__content__`, `__scope__`, `__frame__`,
`__errors__`, `__handlers__` удаляются.

### B1.2. `Outcomes` кортежем, `Failure(exception=)`

* `Outcomes(cases: tuple[When, ...], otherwise: ..., )` без `timeout_ms` — таймаут уходит в
  опции вызова (B2.1). `first_of` удаляется; вывод union остаётся за счёт перегрузок
  `Outcomes.__new__`/фабрики `outcomes(*cases, otherwise=)` до 6 случаев плюс общая.
  Пробник типизатора фиксирует, что 4 и 5 случаев типизированы.
* `Failure(when=, exception=cls_or_factory)`; фабрика — `Callable[[Driver], Awaitable[Exception]]`
  или класс. `raises=`/`detail=` удаляются, `text_of` становится помощником для фабрики.

### B1.3. Одна база `BrowserOperation[TContent, TResult]`

Единственный переопределяемый метод — `async act(self, content) -> None`, для операций
без объявленных исходов результат читается `Outcomes` со случаем `then=`. Сетевые
операции объявляют `requires=(Capability.network,)` и получают `Network` через карту —
маркер `ApiResponse` уже есть; фасад `NetworkOp.on` удаляется. `BrowserOp.run`,
`OutcomeOp`, `NetworkOp` удаляются. Раннер — отдельная функция `execute(operation, driver,
*, options)` в `operations.py`, а не метод: операция остаётся значением.

### B1.4. Диагностики при импорте

`__init_subclass__` у `BrowserOperation` и проверка в `__publish__` (B2.2), коды `D-B-01…`:

| Код | Что |
|---|---|
| D-B-01 | нет `__browser__` — «assign Browser.act(...)» |
| D-B-02 | `content` спеки не совпадает с `TContent` параметра класса |
| D-B-03 | поле карты без маркера (перенос `ContentDeclarationError` на импорт) |
| D-B-04 | `requires` содержит возможность, которую ни один маркер карты не использует, или наоборот — карта содержит `ApiResponse`, а `network` не объявлен |
| D-B-05 | класс операции не frozen |

Все — `PlanError`. Тесты на каждый код.

---

## B2. Роутер и клиент

Цель: `portal.open_create_company()` вместо `Op().execute(driver)`; браузерный роутер в
общем `AsyncRoot`.

### B2.1. `AsyncBrowserClient` и `BrowserCallOptions`

`client.py`:

```python
@dataclass(frozen=True, slots=True)
class BrowserCallOptions:
    timeout: float | None = None        # секунды; общий дедлайн settle
    element_timeout: float | None = None
    response_timeout: float | None = None


class AsyncBrowserClient:
    def __init__(self, driver: Driver, *, config: BrowserClientConfig | None = None,
                 owns_driver: bool = False) -> None: ...
    async def aclose(self) -> None: ...
```

Все константы `*_TIMEOUT_MS` уходят в `BrowserClientConfig` (секунды, `float`);
`Query.timeout_ms` остаётся переопределением на элемент, но в секундах и с именем `timeout`.

### B2.2. Дескриптор, `__publish__`, `AsyncBrowserApi`

`api.py` по образцу `eazy_sdk/websocket/api.py`:

* `_BrowserOperationDescriptor` с `__set_name__` (отказ D-B-06, если владелец не
  `AsyncBrowserApi`), `__get__` → `_BoundBrowserOperation` с `__call__(**fields,
  options=None)`, `.request(**fields)` (операция как значение), `.send(request, *,
  options)`, `.Operation`;
* `BrowserOperation.__publish__()` classmethod возвращает дескриптор;
* `AsyncBrowserApi(client, *, defaults=None)` с сервисными атрибутами `errors`,
  `handlers`, `frames`, читаемыми по MRO как `SERVICE_ATTRIBUTES` ядра; `__init_subclass__`
  отказывает `HttpOperation` (D-B-07);
* `ws`-подобный неймспейс-декоратор не делаем: у браузерной операции есть тело `act`,
  синтезировать её из сигнатуры нечем.

Порядок правил отказа: операция (precedence 0) выше роутера (1), `inherit_errors=False`
отключает наследование — как в ядре.

### B2.3. `api_group` в `AsyncRoot`

`eazy_sdk/root.py` строит роутеры из `_api_kind` и HTTP-клиента; браузерному роутеру нужен
браузерный клиент. Вариант без правки ядра: `AsyncBrowserApi` принимает в `api_group`
через `bind(CompaniesPortal, client=AsyncBrowserClient(driver))` — `Binding` ядра уже
позволяет подать свой клиент роутеру. Проверить, что `_validate_router` ядра не требует
HTTP-резолва от чужого роутера; если требует — это дефект ядра, чинится в ядре
(`_PublishesItself`-роутер пропускается), не обходится в плагине. Тест: `PortalSdk` с
HTTP-роутером через `BrowserHandler` и браузерным роутером на одном драйвере.

### B2.4. Состояния-переходы получают роутер

`when(sign, to=CreateCompanyDialog)` строит состояние из драйвера; после B2 состояние
должно нести роутер: `to=` принимает класс с `__init__(self, api: AsyncBrowserApi)`, и
`CreateCompanyDialog.submit` становится `await self.api.submit_company(name=..., inn=...)`.
Открытый вопрос №8 `DESIGN.md` закрывается: допустимые операции состояния — это
операции роутера, который оно держит. `FromDriver` → `FromApi`.

---

## B3. Имена и ошибки

### B3.1. Иерархия от `EazySdkError`

`failures.py`, `locators.py`, `operations.py`, `profile.py`, `fetch.py`, `network.py`:

```
EazySdkError
├── PlanError ── BrowserDeclarationError (D-B-xx)
├── BrowserError
│   ├── PageError            объявленный отказ страницы (база для SDK)
│   ├── NotReadyError
│   ├── ElementNotFoundError
│   ├── ResponseMissingError
│   └── PageFetchError
└── TransportError           из ядра, без своего подкласса
```

`ContentDeclarationError` → `BrowserDeclarationError`.

### B3.2. Переименования и коллизии

| Было | Стало | Почему |
|---|---|---|
| `Query` | `Locator` | `eazy_sdk.Query` — маркер поля запроса |
| `Cookie` (state) | `BrowserCookie` | `eazy_sdk.Cookie` — маркер поля |
| `CapabilityMismatchError` (плагин) | удалить, брать `eazy_sdk.handlers.CapabilityMismatchError` | два класса одного имени |
| `Level` | `CapabilityLevel` из ядра | одна шкала |
| `TextContains`, `UrlContains`, `Visible` | см. B3.4 | |
| `BrowserOp`/`OutcomeOp`/`NetworkOp` | `BrowserOperation` | B1.3 |
| `timeout_ms: int` везде | `timeout: float` | единицы ядра |

Проверка: `python -c "import eazy_sdk, eazy_sdk_browser"` и сравнение `__all__` —
пересечений нет; тест `test_package.py` закрепляет это.

### B3.3. Профиль в форме `HandlerProfile`

`profile.py`:

```python
@dataclass(frozen=True, slots=True)
class BrowserProfile:
    name: str
    network: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    session_state: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    page_requests: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    navigation_events: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    shadow_dom: ...
    rich_text: ...
    # оси контекста и браузера сняты в B7.1 (LAYERS.md §1.3)


def validate_profile(requires: tuple[Capability, ...], profile: BrowserProfile) -> None
```

Собирает все несовпадения и бросает одно `CapabilityMismatchError(dimensions=...)` ядра.
`require`/`profile(name, **levels)` удаляются; `with_level` → `dataclasses.replace`.
Тест `test_profile_matches_what_the_adapter_really_does` остаётся.

### B3.4. Язык признаков как `Predicate`

`conditions.py`: база `Sign` получает `label: str` для диагностик (как `Predicate` в
`response/match.py`), фабрики в нижнем регистре: `visible(locator)`, `text.contains(...)`,
`url.contains(...)`, `url.matches(...)`, `api.arrived(...)`. Классы `Visible`,
`TextContains`, `UrlContains`, `ApiArrived` становятся приватными реализациями.
`TextContains` кэширует текст на круг: `settle` передаёт признакам `Observation`
(снимок `location` + ленивый `page_text`), а не драйвер — закрывает открытый вопрос №9.

---

## B4. Протокол драйвера

### B4.1. Навигация и ожидание перехода

`driver.py`: `goto(url, *, wait: Literal["load","domcontentloaded","networkidle"])`,
`wait_for_location(predicate, *, timeout)` за возможностью `navigation_events`.
Операция `Open(BrowserOperation[..., ...])` с `Browser.goto(url_template, at=...)` —
второй глагол фасада. Тест на playwright: открыть страницу, `at` выполнен.

### B4.2. Коллекции и атрибуты

`Element`: `attribute(name) -> str | None`, `value() -> str`, `select(option)`, `hover()`,
`check()`. Карта: маркер `each(locator)` → `Annotated[Elements, each(css("tr"))]` с
`count()`, `nth(i)`, `async for`, `texts()`. Playwright — `locator.all()`. Фейк —
`present` становится `dict[str, int]` (сколько узлов).

### B4.3. Экранирование `template`

`template("text={subject}")` подставляет значение через `css_escape` (по CSS.escape:
экранирование кавычек и управляющих символов); для текстовых движков playwright
(`text=`, `:has-text()`) — отдельный маркер `template_text`. Тест: значение с `"`.

### B4.4. `set_html` — из минимума в возможность

`Element.set_html` уходит из протокола в `Capability.rich_text`; в карте —
`Annotated[RichText, rich(css(...))]`. Драйвер без возможности отказывает при сборке карты.

---

## B5. Вход и хранилище

### B5.1. Вход через `session_lifecycle`

`integrations/login.py`:

```python
login = session_lifecycle(
    BrowserState,
    context_factory=lambda graph: BrowserLoginContext(client, graph),
    service=MailLogin(),          # async acquire(credentials, context) -> BrowserState
    store=...,                    # B5.2
    identity="mail:user@example.com",
)
```

`MailLogin.acquire` выполняет операции роутера и возвращает `export_state()`; `validate`
— куки не истекли; `refresh` — не обязателен. `AsyncBrowserClient` принимает `login=` и
вызывает `resolve()` перед первой операцией, `import_state` — из результата. Реакция на
`SessionExpiredError` в правилах отказа: `refresh_revision` и повтор один раз — по образцу
`auth_retries` ядра.

### B5.2. `SessionStore` поверх `AccountWorkspace`

`integrations/accounts.py`: `BrowserSessions` получает метод `store(account) ->
SessionStore[BrowserState]` — обёртка над `workspace.sessions` с `label`, ревизией в
`SessionData.meta["revision"]`. `remember`/`restore` остаются как ручной путь.
Исправить докстринг про `open_workspace`.

### B5.3. Мост в HTTP-`Auth`

`SessionBridge[BrowserState, HttpCookieSession]` — выбрать куку по имени; через
`BridgedSessionAdopter` браузерный вход отдаёт `Auth` HTTP-клиенту. Тест: HTTP-роутер с
`RecordingHandler` получает `Cookie` из браузерной сессии.

---

## B6. Документы

### B6.1. Документы и пример

* `examples/browser_portal.py` переписан в целевую форму (`REUSE.md` §3);
* `README.md`, `OVERVIEW.md` §2–§3, `DESIGN.md` §3–§7 обновлены; §6 закрывает вопросы 8, 9,
  11; §7 ссылается на реальные контракты из корневого `pyproject.toml`;
* докстринг `__init__.py` без `CurrentDriver` в карте;
* пробник `typing_probe.py` — целевая форма, включая 4–5 исходов и переход с роутером;
* `docs-site`: страница плагина, `npm run check`, `npm run build`.

### B6.2. Долг документации ядра

Список для отдельных задач в ядре, не в плагине: экспорт `SuccessOutcome` из
`eazy_sdk.response`; экспорт `BAN`/`FREEZE`/`Order` из `eazy_sdk_accounts.storage`;
одна модель куки в `SessionData`; `SqlAccountWorkspace` ≠ `AccountWorkspace`; два
`SessionCodec`; `.gitignore` скрывает `plugins/*/docs/*.md` — решить, коммитятся ли
документы плагина; cookie-сессия, принимающая сессию со стороны (`adopt`) без
собственного HTTP-входа, — без неё мост из браузера отдаёт только статический `Auth`
(B5.3); `op()` теряет сигнатуру конструктора самопубликуемых операций (B2).

---

## Определение готовности

1. Все шаги в «Состоянии» — `complete` с доказательством.
2. `uv run pytest -q` по workspace зелёный, браузерные интеграционные тесты проходят на
   Chromium, а не пропускаются.
3. `lint-imports`: четыре контракта держатся, добавлен пятый — `browser-api-does-not-know-
   drivers` для `eazy_sdk_browser.api` и `client`. С B7 — шестой по счёту плана,
   `browser-core-does-not-orchestrate`: ядро плагина не импортирует
   `eazy_sdk_browser.session` (`LAYERS.md` B7.6; пятый признан лишним, поэтому в
   `pyproject.toml` их пять).
4. Пересечений `__all__` между `eazy_sdk` и `eazy_sdk_browser` нет.
5. `examples/browser_portal.py` работает и на фейке, и на Chromium, и содержит общий
   `AsyncRoot` с HTTP- и браузерным роутером.
6. Ни одной правки в `eazy_sdk/` ради плагина, кроме починки дефектов из B6.2 и, если
   потребуется, пропуска чужого роутера в `_validate_router` (B2.3).
