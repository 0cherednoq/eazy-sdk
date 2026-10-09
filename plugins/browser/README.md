# eazy_sdk_browser

Декларативные браузерные SDK поверх любого драйвера. Готовые нативные адаптеры — Playwright и
Pydoll 3; Selenium и Camoufox могут подключаться следующими адаптерами.

То же, что `eazy-sdk` делает для HTTP, — для браузера. Операция описана классом-контрактом с
одной спекой `__browser__`, публикуется тем же `op()` на роутере, а драйвер приходит клиентом и
в контракт не протекает.

```python
class CompaniesPage:
    """Список компаний. Формы создания здесь нет — её ещё нет на странице."""

    create_button: Annotated[Element, css(CREATE_BUTTON)]
    filters: Annotated[FilterPanel, region(FilterPanel, root=css(FILTERS_ROOT))]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenCompanies(BrowserOperation[None, None]):
    """Переход: адрес от base_url клиента, готовность — кнопка создания."""

    __browser__ = Browser.goto("/companies", at=css(CREATE_BUTTON))


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenCreateCompany(BrowserOperation[CompaniesPage, CreateCompanyDialog]):
    """Действие: диалог появился — состояние; не появился — отказ."""

    __browser__ = Browser.act(
        CompaniesPage,
        at=css(CREATE_BUTTON),
        outcomes=outcomes(
            when(visible(css(DIALOG_ROOT)), to=CreateCompanyDialog),
            otherwise=_dialog_missing,
        ),
    )

    async def act(self, content: CompaniesPage) -> None:
        await content.create_button.click()


class CompaniesPortal(AsyncBrowserApi):
    errors = PORTAL_ERRORS  # страничные отказы — один раз на портал

    open_companies = op(OpenCompanies)
    open_create_company = op(OpenCreateCompany)
    submit_company = op(SubmitCompany)
```

Запуск на настоящем браузере:

```python
async with async_playwright() as playwright:
    browser = await playwright.chromium.launch()

    async with PlaywrightDriver(await browser.new_page()) as driver:  # один на страницу
        client = AsyncBrowserClient(driver, base_url="https://portal.example.com")
        portal = CompaniesPortal(client)
        await portal.open_companies()
        dialog = await portal.open_create_company()
        created = await dialog.submit(name="ООО Ромашка", inn="7701234567")
```

Полный рабочий пример — `examples/browser_portal.py`: его гоняют тесты и на фейковом
драйвере, и на живом Chromium. Там же `PortalSdk(AsyncRoot)` — HTTP-роутер и браузерный
роутер в одном корне.

## Что даёт

| | |
|---|---|
| **Одна спека** | `__browser__ = Browser.act(Content, ...)` для действия, `Browser.goto(url, ...)` для перехода; ошибка объявления — `BrowserDeclarationError` (`PlanError`) с кодом D-B-xx при импорте |
| **Роутер и клиент** | `AsyncBrowserApi` публикует операции `op()` ядра; `AsyncBrowserClient` держит драйвер, таймауты в секундах и базу адресов; браузерный роутер встаёт в `AsyncRoot` рядом с HTTP |
| **Карта элементов** | аннотациями: `css` / `any_of` / `last`, подкарты `region`, коллекции `each`, шаблоны `template` / `template_text` с экранированием, редактор `rich` |
| **Условие готовности** | `at=` проверяет раннер; не выполнено — сначала слово правилам отказа |
| **Исходы** | `outcomes(when(...), otherwise=...)`; union выводится из объявления и сверяется с типом операции |
| **Переходы** | `to=State`: состояние строится из роутера и вызывает его же операции |
| **Признаки** | `visible()`, `text.contains()`, `url.contains()`, `response.arrived()`, `navigated()` с `\|`, `&`, `~`; признак не ждёт — ждёт цикл |
| **Отказы страницы** | `Failure(when=<признак>, exception=<класс или фабрика>)` у роутера и у операции |
| **Отказы API** | ответ страницы в карте (`ApiResponse`) разбирает объявление `Responses` из `eazy-sdk` |
| **Возможности драйвера** | профиль по осям `CapabilityLevel`; нужное операции (`requires=`, признаки, маркеры карты) сверяется до первого действия |
| **Вход** | `BrowserLogin` на `SessionLifecycle` ядра: сессия из хранилища, повторный вход после объявленного отказа |
| **Хранилище** | `BrowserSessions.store(account)` — сессия за аккаунтом `eazy_sdk_accounts` |
| **Мост в HTTP** | `Identity(cookies=CookieState(state.cookies))` отдаёт куки браузерной сессии HTTP-клиенту |
| **Браузер как транспорт** | `BrowserHandler`: объявленная HTTP-операция уходит запросом из страницы |

## Установка

Ядро плагина не зависит ни от одного драйвера — транспорт ставится экстрой:

```bash
uv add "eazy-sdk-core[browser]"                 # плагин вместе с ядром SDK
uv add "eazy-sdk-core[browser,playwright]"      # с адаптером playwright
uv add "eazy-sdk-core[browser,pydoll]"          # с нативным адаптером Pydoll >=3,<4
uv add "eazy-sdk-core[browser,accounts]"        # + аккаунты и сессии в хранилище eazy-sdk
```

`eazy-sdk` — обязательная зависимость: ошибки, профиль возможностей и жизненный цикл сессии у
плагина общие с ядром. Драйвера в зависимостях нет намеренно, и это проверяется установкой из
колеса (`scripts/extras_smoke.py`), а не обещанием в этом абзаце.

## Pydoll 3

Адаптер принимает уже открытый нативный `pydoll.browser.tab.Tab`. Он не запускает и не
закрывает Chrome/Edge, не создаёт контекст, не выбирает proxy/identity и не повторяет задачу:

```python
from eazy_sdk_browser import AsyncBrowserClient
from eazy_sdk_browser.handlers.pydoll import PydollDriver

tab = await browser.start()  # Tab принадлежит вызывающему слою
async with PydollDriver(tab, context_key=context) as driver:
    portal = CompaniesPortal(AsyncBrowserClient(driver, base_url=BASE_URL))
    await portal.open_companies()
```

`aclose()` снимает только callback-и адаптера и ждёт начатое чтение ответов; `tab.close()` он
не вызывает. Pydoll управляет Chromium-based браузерами. Профиль первой версии честно ограничен:
сеть, запросы из страницы, навигационные события и rich text проверены захватом; session state —
`BEST_EFFORT` (cookies контекста и localStorage только текущего origin); shadow DOM не объявлен.
Не оборачивайте один `Tab` двумя живыми `PydollDriver`: сначала закройте первый адаптер; в пуле
это правило обеспечивает `lease.attachment()`, возвращающий один attachment на тёплую вкладку.
Для пула вкладок и context-scoped сессии см. руководство `guides/browser/browser-pool.mdx`.

## Вход и сессия

Вход стоит дорого: капча, антибот, второй фактор. Поэтому SDK сам знает, как войти: перед
операцией проверяет сессию, при необходимости входит, а после отказа «сессия кончилась»
входит заново и повторяет операцию. Объявление входа одно на сайт, а сессия — одна на
контекст браузера, на все его вкладки:

```python
MAIL_LOGIN = BrowserLogin(
    service=MailLogin(),                # async acquire(credentials, context) -> BrowserState
    cookies=("sid",),                   # без живой sid сессия не сессия
    expired=(SessionExpiredError,),     # после такого отказа входят заново и повторяют
)

mail = MAIL_LOGIN.session(credentials, store=sessions.store(account), identity="ada")
a = AsyncBrowserClient(PlaywrightDriver(page1), base_url=BASE, session=mail)
b = AsyncBrowserClient(PlaywrightDriver(page2), base_url=BASE, session=mail)  # тот же контекст
```

Сколько контекстов и вкладок открыть и с каким `storage_state`, решает код вызывающего или
пул браузеров, а не SDK (`docs/LAYERS.md`). HTTP-клиенту та же сессия отдаётся целиком, если его
SDK объявил `Cookies(...)`:

```python
state = await mail.state(a)
folders = await MailApi(http, identity=Identity(cookies=CookieState(state.cookies))).folders()
```

## Браузер как транспорт

Ответ, который страница получила, разбирает объявление `eazy-sdk`. Обратная сторона: то же
объявление может само **сделать** запрос — руками браузера.

```python
class MailList(HttpOperation[MailListReply]):
    __http__ = Http.post("/Mailbox/Mail", success=MAIL_LIST_SUCCESS, errors=MAIL_LIST_ERRORS)
    offset: Query[int] = 0


handler = BrowserHandler(driver, frames=('iframe[name="mail"]',))
async with AsyncClient(base_url="https://maillist.example", handler=handler) as client:
    page = await MailApi(client).page(offset=50)
```

Пятидесятое письмо — без сорока девяти нажатий «дальше» и без единого слова о том, кто мы: вход
уже пройден страницей. Чего браузер при этом не умеет — порядок и регистр заголовков, повторные
имена, ручной `Cookie`, остановку редиректов, — объявлено в `HandlerProfile`, а не выясняется
посреди сценария.

## Разработка

Проверки запускаются из корня репозитория и охватывают весь workspace:

```bash
uv sync --all-groups
uv run ruff check                      # линт, общий строгий набор
uv run mypy                            # типы, strict
uv run lint-imports                    # пять архитектурных контрактов
uv run complexipy --plain --failed     # когнитивная сложность
uv run pytest -q plugins/browser/tests # только этот плагин
```

Интеграционные тесты поднимают настоящий Chromium; если браузера нет, они пропускаются, а не
падают. Установить: `uv run playwright install chromium`. Граница слоёв — что плагин не
создаёт браузеры, контексты и вкладки — проверяется `tests/test_layers.py`.

Пробник типизатора содержит намеренные ошибки и потому исключён из `mypy` и `ruff`: важен не
факт падения, а какие строки типизатор назовёт, а какие пропустит.

```bash
uv run basedpyright plugins/browser/probe/typing_probe.py
```

## Устройство

```
eazy_sdk_browser/
  spec.py         Browser.act / Browser.goto — единственная спека операции
  operations.py   BrowserOperation, раннер, диагностики D-B-xx
  api.py          AsyncBrowserApi: роутер, дескриптор, сервисные атрибуты
  client.py       AsyncBrowserClient, BrowserClientConfig, BrowserCallOptions, execute
  outcomes.py     outcomes / when, состояния из роутера
  conditions.py   признаки страницы и снимок Observation
  locators.py     css / any_of / last, коллекции each, шаблоны с экранированием
  content.py      сборка карты из аннотаций, подкарты region
  driver.py       протокол Driver и Element — минимум, который умеют все
  navigation.py   адрес Browser.goto, переходы как события, признак navigated()
  network.py      ответы страницы: response.arrived, ApiResponse
  rich_text.py    редактор форматированного текста — возможность rich_text
  failures.py     правила отказа страницы
  interceptors.py помехи: увидел баннер — закрой и продолжай
  login.py        объявление входа BrowserLogin и мост в HTTP-Auth
  session.py      BrowserSession: сессия одного контекста на SessionLifecycle ядра
  state.py        состояние сессии: куки и localStorage, протокол StateAware
  fetch.py        запрос изнутри страницы
  profile.py      возможности драйвера и проверка до запуска
  errors.py       BrowserError и BrowserDeclarationError
  testing.py      фейковые драйверы: страница без браузера
  handlers/       адаптеры Playwright/Pydoll, общий bounded capture и CapturePolicy
  integrations/   хранилище аккаунтов, разбор ответов eazy-sdk, BrowserHandler
```

Ядро плагина не импортирует ни драйверы, ни интеграции — это проверяется контрактами
`import-linter`, а не договорённостью.

## Документация

**`docs/OVERVIEW.md` — начните отсюда:** зачем библиотека, почему у неё такая форма, что она
умеет. Руководство для пользователя — страница «Браузер» на сайте документации
(`docs-site/src/content/docs/guides/browser/`). Подробности рядом:

| Файл | О чём |
|---|---|
| `docs/DESIGN.md` | принятые решения и открытые вопросы |
| `docs/REUSE.md` | что плагин взял у ядра и какие дефекты нашёл |
| `docs/PLAN.md` | план приведения к форме ядра: этапы B0–B6 с доказательствами |
| `docs/CORE_DEBT.md` | долг ядра, найденный плагином: задачи не для плагина |
| `docs/decisions.md` | протокол трёх спайков — исторический: имена тех дней |
| `docs/MERGE.md` | слияние с `eazy-sdk`: совместимость, шаги, найденные дефекты |

## Лицензия

MIT.
