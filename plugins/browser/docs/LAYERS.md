# Граница слоёв: плагин знает сайт, а не среду

2026-09-22 · основание — внешнее ревью `browser_pool/docs/EAZY_SDK_REVIEW.md` (2026-09-21),
каждое утверждение перепроверено по коду этой ветки (`feat/browser-plugin`). Документ —
план этапа **B7** в продолжение `PLAN.md` и ведётся по тем же правилам: раздел
«Состояние» обновляется после каждого шага, шаг закрывается только с доказательством.

---

## 0. Принцип

`eazy-sdk-browser` — это **site SDK**: он отвечает на вопрос «что сделать на этой странице
и как понять, что получилось». Всё, что отвечает на вопрос «**где** и **когда** эта
страница живёт», принадлежит слою выше (`browser-pool` или код пользователя).

Шов между ними — один объект: готовая нативная страница (`playwright.async_api.Page`),
обёрнутая в `Driver`. Плагин получает её снаружи и не спрашивает, откуда она.

| Вопрос | Владелец | Форма в плагине |
|---|---|---|
| Селекторы, карты, операции, исходы, переходы | плагин | `Locator`, `BrowserOperation`, `Outcomes`, роутер |
| Как распознать отказ сайта, какой отказ значит «сессия кончилась» | плагин | `Failure`, `BrowserLogin.expired` |
| Как войти, как понять, что сессия жива, как её обновить | плагин | `BrowserLogin` — объявление + чистые методы |
| Что такое сессия и как её прочитать со страницы | плагин | `BrowserState`, `export_state()` |
| Как перевести сессию в формат драйвера / хранилища | плагин (адаптеры) | конвертеры `handlers/playwright`, `integrations/accounts` |
| Жизненный цикл сессии **одного контекста**: взять из хранилища, войти один раз на все его вкладки, положить куки, войти заново после отказа, сохранить | плагин | `BrowserSession` — один объект на контекст (§2.1.1) |
| Сколько раз повторить операцию после повторного входа | плагин, по настройке вызывающего | `BrowserClientConfig.auth_retries` |
| Сколько контекстов у аккаунта, кто из них входит первым, сколько вкладок у контекста, какой прокси, антидетект | **слой выше** | — |
| Когда создать контекст и с каким `storage_state` (localStorage попадает в контекст только так) | **слой выше** | — |
| Какие учётные данные, чей аккаунт, какое хранилище — **выбор** | **слой выше** | передаются в `BrowserLogin.session(...)` готовыми |
| Очереди, семафоры, лимиты открытий, аренда | **слой выше** | — |

Проверка принципа — правило одной фразы: **объект плагина, созданный на одной странице,
не должен менять состояние, общее с другими страницами; объект плагина, созданный на
одном контексте, не должен менять состояние, общее с другими контекстами** (кроме
хранилища сессий, которое согласует их ревизиями), **и ни один из них не решает, что
делать, когда страниц или контекстов несколько.**

---

## 1. Разбор критики

Вердикт: *подтверждено* — дефект есть в коде и нарушает границу; *вне плагина* — дефект
реален, но чинится не здесь.

### 1.1. Вход живёт в клиенте, а сессия — в контексте · **подтверждено, главное**

- `AsyncBrowserClient(driver, login=…)` создаёт `_SignIn` со своим `SessionLifecycle` и своим
  `asyncio.Lock` (`eazy_sdk_browser/client.py:118-146`, `:176-180`). Клиент — это одна
  `Page`, значит single-flight входа ограничен **вкладкой**.
- Сессия при этом кладётся в **контекст**: `import_state` → `page.context.add_cookies` и
  `add_init_script` (`handlers/playwright.py:294-306`); выгружается тоже из контекста
  (`:286-292`).
- `SessionLifecycle._lock` — поле экземпляра (`eazy_sdk/auth/session.py:168`), экземпляр —
  свой у каждого клиента (`client.py:180`).

Следствия при N вкладках одного контекста: N независимых входов, параллельный повторный
вход в общий контекст, подмена кук под соседними вкладками посреди их операций, повторный
`import_state` каждым клиентом. Защита ревизиями (`refresh_revision`) закрывает только
последовательный случай.

**Почему это нарушение слоёв, а не просто баг:** объект с временем жизни вкладки меняет
состояние, общее для всех вкладок контекста, и делает это, не зная, сколько их. Сам
single-flight входа границу не нарушает — он нужен на уровне контекста (§2.1.1); нарушение
в том, что он поставлен на уровень страницы.

Сопутствующие нарушения, найденные при проверке (в ревью не названы):

- `_run_signed_in` сам решает политику повторов после повторного входа
  (`client.py:231-248`, `BrowserLogin.retries` — `login.py:119`). Сколько раз повторять
  операцию — решение вызывающего.
- `BrowserLogin` держит `credentials`, `store`, `identity` (`login.py:109-112`) — это данные
  **аккаунта и хранилища**, не знание сайта. Из-за них объявление нельзя переиспользовать
  между аккаунтами: одна декларация «как входить в почту» размножается на каждый аккаунт.
- `BrowserLogin.lifecycle(...)` (`login.py:151-167`) собирает жизненный цикл ядра — то есть
  плагин владеет машиной состояний «хранилище → проверка → вход → сохранение».
- `BrowserLoginContext.graph` (`login.py:77-90`) — деталь `SessionLifecycle`, протёкшая в
  публичный контекст входа.

### 1.2. `import_state` перезаписывает localStorage на каждой навигации · **подтверждено, баг**

`_seed_storage` (`handlers/playwright.py:444-455`) безусловно пишет сохранённые значения,
а `add_init_script` выполняется на каждом новом документе до конца жизни контекста.
SPA, обновившая токен, после следующего перехода получает старый. Каждый `renew()`
добавляет ещё один скрипт — они копятся.

Корень — тоже граница: положить состояние в контекст можно корректно только **при
создании контекста** (`new_context(storage_state=…)`), а создаёт контекст слой выше.
Плагин пытался сделать это задним числом со страницы и получил обходной путь.
Поэтому исправление — не «писать ключ, только если его нет», а убрать запись localStorage
из драйвера и дать слою выше конвертер в формат `storage_state`. Куки — другое дело:
`add_cookies` пишется один раз, а не на каждой навигации, поэтому сама по себе запись
кук безопасна и остаётся. Когда писать — отдельный вопрос: затереть ротированную сайтом
куку она может, и это решает правило записи из §2.1.1 (`LOGIN_SCOPE.md` §5 п. 1).

Тот же приём повторён в `BrowserSessions.restore(account, driver)`
(`integrations/accounts.py:142-148`): интеграция хранилища сама решает, когда положить
сессию в браузер.

### 1.3. Оси оркестрации в профиле страницы · **подтверждено**

`Capability.proxy_per_context`, `isolated_contexts`, `stealth` (`profile.py:37-50`,
`:67-71`; объявлены в `PLAYWRIGHT_PROFILE`, `handlers/playwright.py:54-66`). В
`DESIGN.md:92-95` в колонке «кто пользуется» — прочерк: ни одна операция их не требует и не
может, потому что `Driver` — это страница, а прокси, число контекстов и отпечаток —
свойства браузера и контекста. Их место — в возможностях слоя оркестрации.
Если операции нужен антидетект, это требование к **аренде страницы**, а не к драйверу.

### 1.4. Подписки драйвера и время его жизни · **подтверждено частично**

`PlaywrightDriver.__init__` подписывается на `response` и `framenavigated`
(`handlers/playwright.py:150-157`), отписка — только в `aclose()` (`:350-359`), а клиент
зовёт её лишь при `owns_driver=True` (`client.py:255`). Сам по себе это не нарушение
слоёв: драйвер вправе слушать свою страницу. Дефект — в неявном контракте времени жизни:
ничего не говорит, что драйвер **один на страницу и живёт вместе с ней**, и ничего не
отписывает его, когда страница закрыта. Слой выше, переиспользующий страницы, получит
утечку слушателей и N-кратное чтение тел.

Плагин чинит свою половину: делает время жизни явным и привязывает отписку к закрытию
страницы. «Одно вложение на страницу через все аренды» — забота слоя выше (в ревью это
`lease.attachment`), здесь не делается.

### 1.5. Буфер ответов читает всё · **подтверждено, не про слои, но в плагине**

`_remember`/`_store` (`handlers/playwright.py:377-397`) читают тело **каждого** ответа —
картинки, шрифты, бандлы; лимит в штуках (`CAPTURE_LIMIT = 500`, `:71-73`), не в байтах.
Память процесса Python растёт незаметно для лимитов браузера. Чинится политикой захвата
в драйвере: тип ресурса и бюджет в байтах.

### 1.6. `AccountPool`: гонка между `pick` и `lease` · **вне плагина**

`pick()` и `lease()` — отдельные вызовы (`plugins/accounts/eazy_sdk_accounts/storage/services/pool.py:84`,
`:158`), `_leased` — множество в памяти процесса (`:57`). Это оркестрация в плагине
аккаунтов, а не в браузерном. Здесь только фиксируется в `CORE_DEBT.md`; браузерный плагин
`AccountPool` не использует и после B7 использовать не должен.

### 1.7. Мелочи ревью

- `_SignIn.lock` — уходит вместе с `_SignIn` (1.1).
- Коллизия имён `Driver`/`BrowserProfile` с `browser-pool` — не дефект этой библиотеки;
  интегратор импортирует с алиасами. Не делается.
- Две модели куки — уже `CORE_DEBT.md` п. 3.

### 1.8. Что соблюдено и должно остаться

- Нет `launch()`, `new_context()`, `new_page()`, семафоров, очередей и пулов вкладок
  (проверено `grep` по `eazy_sdk_browser/`: единственные `asyncio.Lock` и `create_task` —
  `client.py:124` и чтение тел в `handlers/playwright.py:379`).
- Операции — frozen dataclass без драйвера: одна операция идёт в нескольких вкладках.
- Ядро плагина не импортирует драйверы (`browser-core-does-not-know-drivers`).

B7 закрепляет это проверкой (B7.6), чтобы граница не зависела от внимательности ревьюера.

---

## 2. Целевая форма

### 2.1. Вход — объявление и чистые шаги

```python
MAIL_LOGIN = BrowserLogin(                 # одно на сайт, общее для всех аккаунтов
    service=MailLogin(),                   # async acquire(credentials, context) -> BrowserState
    cookies=("sid",),                      # без живой sid сессия не сессия
    expired=(SessionExpiredError,),        # какие отказы значат «сессия кончилась»
)

MAIL_LOGIN.validate(state) -> bool                                  # жива ли сессия
MAIL_LOGIN.is_expired(error) -> bool                                # этот отказ — «кончилась»?
await MAIL_LOGIN.sign_in(client, credentials) -> BrowserState       # войти на этой странице
await MAIL_LOGIN.refresh(client, credentials, state) -> BrowserState  # свой refresh сервиса или вход заново
```

- Уходят из `BrowserLogin`: `credentials`, `store`, `identity`, `retries`, `key`,
  `lifecycle()`. Остаются: `service`, `cookies`, `expired`, `leeway`, `clock`.
- `sign_in`/`refresh` проверяют результат `validate` и бросают `BrowserSessionError`, если
  вход завершился, а сессия не годится. Ни блокировок, ни хранилища, ни повторов.
- `BrowserLoginContext(client)` — без `graph`. Сервис входа получает клиент без сессии
  (`session=None`), как и сейчас.
- Сервис остаётся совместимым с `SessionLifecycle` ядра по форме `acquire(credentials,
  context)`: им пользуется `BrowserSession` (§2.1.1) и может пользоваться слой выше.
- `AsyncBrowserClient(driver, base_url=, config=, session=)` — вместо `login=`. Клиент
  теряет `sign_in()`, `_SignIn`, `_run_signed_in`, `_login_context`. Клиент = драйвер +
  `base_url` + конфигурация + источник сессии.
- `BrowserCookieBridge`, `CookieAuthAdopter`, `browser_cookie_auth` не меняются: это чистое
  преобразование готового `BrowserState`; состояние им отдаёт `await session.state(client)`.

#### 2.1.1. Сессия: протокол для клиента и один объект на контекст

Полное обоснование — `LOGIN_SCOPE.md` §3–5. Кратко: сессия лежит в контексте, поэтому
её жизненный цикл принадлежит объекту с временем жизни контекста — не вкладки (как
`_SignIn` сейчас) и не аккаунта (пришлось бы помнить карту контекстов, а это решение
«когда страниц несколько»).

```python
class SessionSource[TRevision](Protocol):          # всё, что клиент знает о сессии; живёт в client.py
    async def ensure(self, client: AsyncBrowserClient) -> TRevision: ...
    async def renew(self, client: AsyncBrowserClient, rejected: TRevision) -> TRevision | None: ...

mail = MAIL_LOGIN.session(credentials=creds, store=sessions.store(account), identity="user@example.com")
async with (
    AsyncBrowserClient(PlaywrightDriver(page1), base_url=BASE, session=mail) as a,   # все вкладки
    AsyncBrowserClient(PlaywrightDriver(page2), base_url=BASE, session=mail) as b,   # одного контекста
):
    await MailPortal(a).send(...)                   # автологин: один вход на контекст
await mail.state(a)                                 # сессия без операции — для HTTP-моста
```

- Цикл клиента: `ensure(self)` → операция → отказ, для которого `is_expired` истинно →
  `renew(self, rejected)` → ревизия → повтор. Повторов — `BrowserClientConfig.auth_retries`
  (по умолчанию 1, как у ядра). `renew` вернул `None` — повторов нет, клиент заново
  бросает **исходный отказ из `expired`**: наружу уходит отказ сайта, а не исключение
  источника. Так «не умею входить заново» передаётся значением, а не исключением.
- **Ревизия для клиента непрозрачна.** Клиент её только держит и отдаёт обратно в
  `renew`, поэтому протокол параметризован `TRevision`, а клиент хранит `object`.
  `BrowserSession` работает с `SessionRevision` ядра, источник пула — с тем, что есть у
  пула (поколение контекста), ничего выдумывать ему не нужно. Из `client.py` уходит
  последний импорт `eazy_sdk.auth.session`, даже под `TYPE_CHECKING`.
- Источник получает **клиент, а не драйвер**: для входа нужен клиент без сессии на той же
  странице, а ему нужны `base_url` и `config` вызывающего. `AsyncBrowserClient.without_session()`
  отдаёт такой клиент (тот же драйвер, `base_url`, `config`, `session=None`); из него
  `BrowserSession` строит `BrowserLoginContext`.
- `BrowserSession` (`session.py`) — реализация для работы без пула, **один объект на
  контекст**. Внутри: `SessionLifecycle` ядра, чьи `acquire` и `refresh` — тонкие
  адаптеры над `login.sign_in(entrance, credentials)` и `login.refresh(entrance,
  credentials, state)`, так что путь входа один и проверка результата живёт только в
  `BrowserLogin`; **свой `asyncio.Lock` на весь шаг** «посмотреть в контекст → разрешить
  → положить куки → `applied`», как `_SignIn.lock` сегодня (без него параллельные вкладки
  сделают несколько записей); два поля: `applied` (ревизия, уже лежащая в контексте) и
  `applied_by_login_here` (её добыл вход на странице этого контекста, а не хранилище).
  **Порядок первого `ensure` — сначала контекст, потом хранилище:**
  1. `export_state()`; если `login.validate(...)` истинно — в контексте уже годная
     сессия. Хранилище с годной сессией → взять её ревизию, ничего не писать. Хранилище
     пустое или сессия там негодная → `lifecycle.adopt(состояние из контекста)`: ядро
     проверит, сохранит и выдаст новую ревизию под тем же локом (`eazy_sdk/auth/session.py`,
     `adopt`), без входа и без записи. Иначе второй контекст, созданный со
     `storage_state` из другого хранилища или с `store=None`, вошёл бы заново поверх
     годной сессии.
  2. иначе — `resolve()` (хранилище или вход) и запись кук по правилу ниже.
  **Быстрые пути, как у `_SignIn` сегодня:** `ensure` при непустом `applied` возвращает
  его, не трогая хранилище; `renew(rejected)` при `applied > rejected` возвращает
  `applied`, не трогая ни хранилище, ни контекст — сосед, получивший отказ со старой
  ревизией, пока первый уже вошёл, повторяет операцию сразу. Хранилище читается только
  при первом `ensure` и в `renew` с `rejected == applied`. Там `renew` →
  `refresh_revision`: входит держатель самой свежей ревизии, остальные ждут лок и
  выходят по быстрому пути. Вход идёт на странице первого клиента, упёршегося в
  отсутствие сессии; фабрика страниц для входа не даётся.
- `BrowserLogin.session(...)` **требует непустой `cookies`** и бросает
  `BrowserDeclarationError` при сборке: без объявленной куки `validate` проверяет лишь
  непустоту состояния, а в живом контексте почти всегда есть какая-нибудь кука
  аналитики, поэтому первый `ensure` счёл бы сессию годной и никогда не положил бы
  сохранённую. Для `sign_in`/`refresh` без `BrowserSession` пустой `cookies` по-прежнему
  допустим.
- **Страница входа — через `ContextVar`.** `SessionLifecycle` получает `context_factory`
  один раз при создании, а объект общий на все вкладки, поэтому клиент для входа
  передаётся на каждый вызов: `ensure`/`renew` ставят `ContextVar[AsyncBrowserClient]`
  на время `resolve`/`refresh_revision`, а `context_factory=lambda graph:
  BrowserLoginContext(entrance.get().without_session())` читает его. Под локом
  жизненного цикла в один момент времени входит один вызов, поэтому значение
  переменной однозначно.
- `ensure` и `renew` пишут в контекст **только куки** (`StateAware.add_cookies`). Когда
  писать: при первом `ensure` — только на шаге 2, то есть когда в контексте нет годной
  сессии. Контекст со `storage_state` от слоя выше записи не получает; контекст, где
  сайт сам ротировал куку, — тоже, иначе свежую куку затёрла бы старая из хранилища.
  После `renew`, когда хранилище действительно читалось, — только если новая ревизия
  **пришла из хранилища, а не из входа в этот контекст**: вход здесь уже положил куки
  сам, а ревизию из хранилища добыл соседний объект того же аккаунта, и в этом
  контексте её ещё нет. Признак считается по объекту, не по вызову, и ставится **только
  после успешного входа**: фабрика контекста входа вызывается до `acquire`/`refresh`, и
  упавший вход оставил бы признак при старом `applied`. Поэтому адаптеры `acquire`/`refresh`
  отмечают «вход был здесь» после того, как `login.sign_in`/`login.refresh` вернул
  результат, а в объект признак копируется только вместе с новым `applied`; `renew`,
  взявший ревизию из хранилища, его сбрасывает. Сравнение значений кук не делается: по
  значениям ротацию от протухания не отличить. Если в контексте лежала годная на вид, но
  мёртвая сессия, её отсеет сервер: отказ → `renew` → вход.
- Проверка драйвера — в `BrowserSession`, при первом `ensure`, до входа:
  `require_state(client.driver)`. Не в клиенте: клиент знает только протокол, а
  источнику пула `session_state` может быть не нужен.
- Защита инварианта, не оркестрация: `BrowserSession` запоминает
  `StateAware.context_key()` первого клиента (непрозрачный ключ контекста, для playwright
  — `page.context`) и бросает `BrowserSessionError`, если тот же объект принёс клиент с
  другим ключом. Два контекста одного аккаунта — два объекта с общим `store`; их
  согласует только хранилище ревизий, как два `Auth` в HTTP. Кеш объектов по аккаунту не
  делается.
- С пулом источник сессии — сам пул: `ensure` отдаёт ревизию контекста, `renew`
  возвращает `None`, клиент бросает отказ сайта, пул закрывает контекст и входит заново в
  новом. Адаптер живёт вне плагина (`integrations/pool`, вне B7); форма выше его
  допускает без правок плагина.
- `state(client)` требует страницу: без неё хранилище не разрешить и входить негде.
  `storage_state` для контекста «до первой вкладки» берётся из хранилища напрямую
  (`BrowserSessions.load(account)` → `to_storage_state`); если сессии там нет, первый
  контекст открывается без `storage_state`, вход кладёт куки, а `await mail.state(client)`
  отдаёт состояние для следующего контекста и для HTTP-моста. Второй контекст с этим
  `storage_state` войдёт без входа: первый `ensure` увидит годную сессию и примет её
  через `adopt`. Но **`store` у двух объектов одного аккаунта должен быть общим**, иначе
  после повторного входа они не согласуют ревизии; `store=None` даёт каждому объекту своё
  хранилище в памяти и годится только для одного контекста. `state()` без клиента не
  делается: это второй путь разрешения сессии.
- Единственное место в плагине, где разрешены `SessionLifecycle` и `asyncio.Lock`, —
  `session.py`; `add_cookies` реализует только драйвер (`StateAware.add_cookies`,
  `handlers/playwright.py`), а зовёт только `session.py`. Это корректность одной сессии,
  а не оркестрация: объект не создаёт контексты и вкладки, не выбирает аккаунт и не
  решает, что делать, когда контекстов несколько.

### 2.2. Состояние — чтение со страницы, запись только кук

- `StateAware` — три метода: `export_state()`, `add_cookies(cookies)` и
  `context_key() -> object`; `import_state` удаляется из протокола, `PlaywrightDriver`,
  фейков `testing.py`. `add_cookies` пишет только куки, идемпотентно, и зовёт его только
  `BrowserSession` (§2.1.1). `context_key` — непрозрачный ключ контекста, равный у всех
  страниц одного контекста (у playwright — `page.context`, у фейков — общий объект,
  который тест передаёт нескольким драйверам); сравнивается только на равенство.
  `Capability.session_state` означает «умеет выгрузить состояние, положить куки и назвать
  контекст» — докстринг правится.
- В `handlers/playwright.py` — две чистые функции формата (единственное место, которое
  знает формат playwright):
  `to_storage_state(state: BrowserState) -> StorageState` — для
  `browser.new_context(storage_state=…)`;
  `from_storage_state(raw) -> BrowserState` — ею же пользуется `export_state`.
  `_seed_storage` и `add_init_script` удаляются без замены: localStorage в живой контекст
  не дозаливается, для него — `storage_state` при создании контекста (`LOGIN_SCOPE.md`
  §5 п. 1). Единственная запись в контекст — куки из `BrowserSession.ensure` (§2.1.1).
- `integrations/accounts.BrowserSessions`: удаляются `restore(account, driver)` и
  `remember(account, driver)`; появляется `save(account, state, *, expires_at=None)`.
  `load`, `forget`, `store`, `BrowserStateCodec`, `to_session_data`/`from_session_data`
  остаются. Интеграция хранилища больше не видит `Driver`.

### 2.3. Профиль — только оси страницы

`Capability` и `BrowserProfile`: `network`, `session_state`, `page_requests`,
`navigation_events`, `shadow_dom`, `rich_text`. Оси `proxy_per_context`,
`isolated_contexts`, `stealth` удаляются отовсюду.

### 2.4. Драйвер живёт со страницей

- `PlaywrightDriver` — async context manager (`async with PlaywrightDriver(page) as driver`).
- Подписывается на `page.on("close")` и при закрытии страницы отписывается сам; `aclose()`
  по-прежнему идемпотентен и дожидается чтения тел.
- Докстринг класса и `DESIGN.md` §4 фиксируют контракт: **один драйвер на страницу, живёт
  столько же, сколько страница**; повторно оборачивать ту же страницу, не закрыв прежний
  драйвер, — ошибка вызывающего.

### 2.5. Политика захвата сети

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CapturePolicy:
    resource_types: frozenset[str] = frozenset({"xhr", "fetch", "document"})
    max_body_bytes: int = 2 * 1024 * 1024       # тело больше — не читается
    max_total_bytes: int = 32 * 1024 * 1024     # буфер вытесняет старые по байтам

PlaywrightDriver(page, capture=CapturePolicy())   # capture=None — сеть не слушается
```

- Заменяет `capture_network: bool` и `capture_limit`/`CAPTURE_LIMIT` (без псевдонимов).
- Тип ресурса проверяется **до** чтения тела (`response.request.resource_type`), размер —
  по `content-length`, а при его отсутствии — после чтения (тогда тело отбрасывается).
- Ответ, чьё тело не прочитано из-за лимита, попадает в буфер с пустым телом и признаком
  `ResponseView.body_dropped=True`; разбор такого ответа картой бросает
  `ResponseMissingError` с причиной «тело больше лимита захвата», а не парсит пустоту.
- Позиции (`mark()`/`since`) остаются монотонными при вытеснении по байтам.

---

## 3. Шаги

Порядок выбран так, чтобы после каждого шага все ворота были зелёными. Каждый шаг удаляет
старую форму в том же коммите, где появляется новая, — вместе с тестами, примером и
докстрингами (правило `PLAN.md`). Код и комментарии — по-русски, в стиле соседнего кода.

### B7.1. Оси оркестрации — вон из профиля

Файлы: `profile.py`, `handlers/playwright.py` (`PLAYWRIGHT_PROFILE`), `operations.py:260`
(докстринг упоминает `stealth`), `tests/test_unification.py:92-95` (взять `rich_text` вместо
`stealth`), `docs/DESIGN.md` §5 (строки таблицы и абзац про антидетект → «это требование к
аренде страницы, слой выше»), `docs-site/src/content/docs/api-reference/browser.mdx:99-100`.

Критерий: `grep -rn "proxy_per_context\|isolated_contexts\|stealth" plugins/browser docs-site/src`
ничего не находит, кроме этого документа и `decisions.md` (исторический).

### B7.2. Вход — объявление; сессия — один объект на контекст

Файлы: `login.py`, новый `session.py`, `client.py`, `__init__.py` (экспорты),
`tests/test_login.py`, новый `tests/test_session.py`,
`tests/test_browser_sessions.py::test_login_persists_its_session_into_the_account_workspace`.

1. `BrowserLogin` по §2.1; `_Relogin` превращается в `BrowserLogin.refresh`;
   `BrowserLogin.session(credentials, *, store=None, identity=...) -> BrowserSession`,
   при пустом `cookies` — `BrowserDeclarationError` (§2.1.1).
   Метод импортирует `session.py` **лениво, внутри тела**: `session` импортирует `login`
   на уровне модуля (`BrowserLoginContext`, тип `BrowserLogin`), обратный импорт на уровне
   модуля дал бы цикл.
2. `BrowserLoginContext(client)` без `graph`.
3. `session.py`: `BrowserSession` по §2.1.1 — туда переезжает `_SignIn` из клиента вместе
   с `SessionLifecycle` и локом; `acquire`/`refresh` жизненного цикла — адаптеры над
   `login.sign_in`/`login.refresh` и ставят признак «вход был здесь» только после
   успешного результата; добавляются `ContextVar` страницы входа и поле
   `applied_by_login_here`, проверка `context_key`, `require_state` при первом `ensure`,
   порядок первого `ensure` «контекст → `adopt` или `resolve`» из §2.1.1, быстрые пути по
   `applied` в `ensure` и `renew`, правило записи кук из §2.1.1, `state(client)`.
   `AsyncBrowserClient` импортируется только под `TYPE_CHECKING`.
   Метод драйвера для записи кук появляется в B7.3, поэтому **B7.3 идёт раньше B7.2**:
   порядок в «Состоянии» — B7.1, B7.3, B7.2, дальше по номерам.
4. `AsyncBrowserClient`: `login=` → `session=`; `SessionSource` объявляется здесь же, в
   `client.py` — это протокол того, что клиент принимает, и он не должен тянуть
   `session.py`; протокол параметризован `TRevision`, клиент держит ревизию как `object`,
   импорт `SessionRevision` из `client.py` удаляется вместе с `TYPE_CHECKING`-блоком
   `eazy_sdk.auth.session`); удалить
   `sign_in`, `_SignIn`, `_run_signed_in`, `_login_context`, runtime-импорт
   `BrowserLoginContext` (контекст входа строит `session.py`), импорт `asyncio`; добавить
   `without_session()`; цикл `ensure(self) → операция → renew(self, rejected) → повтор`,
   `None` из `renew` → исходный отказ бросается заново; `BrowserClientConfig.auth_retries`
   (новое поле, по умолчанию 1); поправить докстринг класса.
5. Тесты входа переписать на новую форму (имена — по смыслу):
   - `test_sign_in_runs_the_service_on_the_given_client_and_returns_a_valid_state`;
   - `test_sign_in_rejects_a_state_without_the_declared_cookie`
     (замена `…session_whose_cookie_is_about_to_expire…` — проверка `validate` с `leeway`);
   - `test_refresh_prefers_the_service_refresh_and_falls_back_to_sign_in`;
   - `test_is_expired_recognises_only_declared_failures`;
   - `test_one_login_declaration_serves_two_accounts` — одно объявление, два набора
     учётных данных, два разных состояния;
   - `test_client_without_session_never_signs_in` — `session=None`: операция идёт без
     `ensure`, отказ из `expired` не повторяется;
   - мост в HTTP (`test_http_router_sends_the_cookie_…`, `test_bridge_picks_…`,
     `test_expired_browser_cookie_…`) — замена `browser.sign_in()` на
     `await mail.state(client)`.
6. Тесты сессии (`test_session.py`) — прежние тесты клиента переезжают на
   `BrowserSession`, а не удаляются:
   - `test_first_operations_sign_in_once_…` → `test_one_session_object_serves_every_tab_of_its_context`
     — N клиентов одного контекста с одним объектом, параллельные первые операции, один
     вызов сервиса и **ни одного** `add_cookies`: вход прошёл на странице этого же
     контекста, куки уже там;
   - `test_stored_live_session_is_reused_…` — сохранённая годная сессия, контекст пустой:
     ноль вызовов сервиса и **ровно один** `add_cookies` на N параллельных первых
     операций (проверка лока на шаг записи);
   - новый `test_ensure_skips_the_write_when_the_context_already_holds_a_valid_session` —
     в хранилище годная сессия, контекст со `storage_state` или с кукой, которую сайт
     ротировал (значение отличается от хранилища): `validate` истинно, `add_cookies` не
     вызывается, `applied` — ревизия хранилища;
     парный `test_ensure_writes_when_the_context_session_is_not_valid` — куки нет или она
     просрочена → запись идёт;
   - новый `test_context_with_a_valid_session_is_adopted_without_signing_in` — хранилище
     пустое, контекст создан со `storage_state`: ноль входов, ноль записей, в хранилище
     появилась ревизия 1 (через `lifecycle.adopt`);
   - новый `test_failed_sign_in_leaves_no_login_here_mark` — сервис входа упал: `applied`
     не изменился, признак «вход был здесь» не выставлен, следующий `ensure` входит снова;
   - новый `test_ensure_fast_path_does_not_touch_the_store` — после первого `ensure`
     хранилище не читается перед каждой операцией;
   - новый `test_sign_in_page_is_the_client_that_hit_the_missing_session` — при
     параллельных первых операциях сервис входа получил клиент того, кто взял лок, с его
     `base_url` и `config`, без сессии;
   - новый `test_lifecycle_enters_only_through_login_sign_in_and_refresh` — сервис вернул
     состояние без объявленной куки: `BrowserSessionError` приходит из `BrowserLogin.sign_in`,
     жизненный цикл своей проверки не делает (один путь);
   - новый `test_session_requires_a_declared_cookie` — `BrowserLogin(cookies=()).session(...)`
     → `BrowserDeclarationError`; `sign_in` без `BrowserSession` с пустым `cookies` работает;
   - `test_declared_expiry_signs_in_again_…` — отказ из `expired` → `renew` → одна новая
     ревизия входом на этой странице, `add_cookies` не вызывается; соседний клиент с тем
     же отказом и старой ревизией получает `applied` по быстрому пути: хранилище не
     читается, `add_cookies` не вызывается, второго входа нет;
   - новый `test_renew_writes_a_revision_obtained_elsewhere` — второй объект с тем же
     хранилищем вошёл заново; первый после отказа берёт его ревизию из хранилища без
     входа и **пишет** куки;
   - `test_retry_is_spent_once_…` — `auth_retries` тратится один раз;
   - новый `test_renew_returning_none_re_raises_the_original_failure` — источник, чей
     `renew` отдаёт `None` (форма источника пула): повторов нет, наружу уходит отказ
     сайта, а не исключение источника;
   - новый `test_two_contexts_of_one_account_share_the_store_but_not_the_lock` — два
     объекта с общим хранилищем: второй берёт ревизию первого без входа;
   - новый `test_session_object_rejects_a_client_from_another_context` —
     `BrowserSessionError` при клиенте с другим `context_key`;
   - `test_login_needs_a_driver_that_accepts_session_state` — переезжает в `BrowserSession`:
     драйвер без `session_state` отсекается при первом `ensure`, до вызова сервиса;
     сборка клиента с `session=` драйвер не проверяет;
   - `test_sign_in_hands_the_session_to_the_caller` → `test_state_resolves_the_session_without_an_operation`.
7. Оркестрация сверх этого (сколько контекстов и вкладок, кто входит первым между
   контекстами, выбор аккаунта, аренда) **не переносится** в плагин — она показывается в
   документации как код слоя выше или пула (B7.7).

Критерий: `grep -rn "SessionLifecycle\|asyncio.Lock" plugins/browser/eazy_sdk_browser`
находит только `session.py`; `grep -rn "retries" plugins/browser/eazy_sdk_browser` — только
`auth_retries` в `client.py`; `grep -rn "login=" plugins/browser docs-site/src/content/docs/guides/browser examples`
пусто.

### B7.3. Состояние — только выгрузка; конвертер для контекста

Файлы: `state.py`, `handlers/playwright.py`, `testing.py:298-302`,
`integrations/accounts.py`, `tests/test_playwright_handler.py`, `tests/test_browser_sessions.py`.

1. `StateAware` — `export_state()`, `add_cookies(cookies)`, `context_key() -> object`
   (§2.2); `import_state` удалить; поправить сообщение в `require_state` и докстринг
   модуля `state.py` (абзац «загружают перед следующим запуском» → «localStorage кладёт
   в контекст тот, кто его создаёт; куки — `BrowserSession`»).
2. `to_storage_state`/`from_storage_state` в `handlers/playwright.py`; `export_state`
   переходит на `from_storage_state`; `import_state`, `_seed_storage` удалить.
   Куки в контекст пишет только `session.py` через драйвер: в `StateAware` вместо
   `import_state` — `add_cookies(cookies)` (только куки, без localStorage), у
   `PlaywrightDriver` — `page.context.add_cookies`, у фейков `testing.py` — запись в
   список. `context_key()`: у `PlaywrightDriver` — `page.context`, у фейков — параметр
   конструктора `context_key=` (по умолчанию новый `object()` на драйвер), чтобы тест мог
   дать нескольким фейкам один контекст. Это не «дозаливка состояния»: запись кук
   безопасна при навигации, в отличие от init-скрипта (регрессия 1.2 касалась только
   localStorage); когда писать, решает правило из §2.1.1, драйвер этого не знает.
3. `BrowserSessions`: `restore`/`remember` → `save(account, state, *, expires_at=None)`;
   докстринг модуля (пример в начале) — без драйвера.
4. Тесты:
   - `test_session_state_moves_between_contexts` → второй контекст создаётся
     `browser.new_context(storage_state=to_storage_state(state))`, проверки те же;
   - новый интеграционный `test_restored_local_storage_is_not_rewritten_on_navigation`:
     контекст из `storage_state`, страница меняет `localStorage['token']`, `goto` на тот же
     источник — значение осталось новым (регрессия ревью 1.2);
   - новый `test_storage_state_round_trip_keeps_every_cookie_attribute` (чистая функция,
     без браузера);
   - `test_remember_and_restore_through_storage`, `test_restore_without_saved_session` →
     `test_save_and_load_through_storage`, `test_load_without_saved_session`.

Критерий: `grep -rn "import_state\|add_init_script\|_seed_storage" plugins/browser`
пусто (кроме этого документа); `grep -rn "add_cookies" plugins/browser/eazy_sdk_browser`
находит только протокол в `state.py`, реализацию в `handlers/playwright.py` и `testing.py`
и вызов в `session.py`.

### B7.4. Драйвер живёт со страницей

Файлы: `handlers/playwright.py`, `tests/test_playwright_handler.py`, `docs/DESIGN.md` §4.

1. `__aenter__`/`__aexit__` → `aclose()`; подписка на `close` страницы, в обработчике —
   снять `response`/`framenavigated` (синхронно) и не создавать новых задач чтения тел;
   ожидание уже начатых — в `aclose()`.
2. Докстринг класса — контракт из §2.4.
3. Тесты (Chromium):
   - `test_driver_detaches_itself_when_the_page_closes` — после `page.close()` новые ответы
     не читаются, `aclose()` завершается без ошибок;
   - `test_drivers_wrapped_one_after_another_do_not_accumulate_listeners` — пять драйверов
     подряд с `async with` на одной странице, затем один ответ: он прочитан ровно одним
     живым драйвером (счётчик вызовов `_store` или число записей в буферах).

### B7.5. Политика захвата сети

Файлы: `handlers/playwright.py`, `network.py` (`ResponseView.body_dropped`), место разбора
карты ответа (`ResponseMissingError` с причиной), `tests/test_playwright_handler.py`,
`tests/test_waiting.py` (фейк не меняется, если не читает тела).

1. `CapturePolicy` по §2.5, экспорт из `handlers`; `capture_network`, `capture_limit`,
   `CAPTURE_LIMIT` удалить; `profile` смотрит на `capture is None`.
2. Вытеснение по сумме байтов; `_seen` и водораздел — без изменений.
3. Тесты:
   - `test_capture_limit_evicts_old_replies_but_keeps_positions` → переписать под байты;
   - `test_images_and_fonts_are_not_read` — страница грузит картинку, буфер её не содержит;
   - `test_oversized_body_is_marked_dropped_and_explained` — ответ больше `max_body_bytes`,
     операция, ждущая его, падает с понятной причиной;
   - `test_profile_matches_what_the_adapter_really_does` — `capture=None` вместо
     `capture_network=False`.

### B7.6. Граница — проверка, а не договорённость

1. `tests/test_layers.py` — разбор AST всех модулей `eazy_sdk_browser` (включая
   `handlers`, `integrations`, `testing`); запрещены вызовы и имена:
   `launch`, `launch_persistent_context`, `new_context`, `new_page`, `add_init_script`,
   `clear_cookies`, `set_extra_http_headers` на контексте, `asyncio.Semaphore`,
   `asyncio.Queue`, `asyncio.Condition`. Тест печатает файл и строку каждой находки.
   Исключения — по списку в тесте, а не по умолчанию: `asyncio.Lock` и `SessionLifecycle`
   разрешены только в `session.py`; `context.add_cookies` — только в
   `handlers/playwright.py` (реализация `StateAware.add_cookies`); тестовые файлы в
   `tests/` не проверяются. Всё вне списка — находка.
2. Контракт `import-linter` `browser-core-does-not-orchestrate` (`forbidden`): модули
   ядра плагина (список как у `browser-core-does-not-know-drivers`, **без `login` и без
   самого `session`**) не импортируют `eazy_sdk_browser.session`. Запрещать `eazy_sdk.auth.session` нельзя:
   `eazy_sdk/auth/__init__.py` сам импортирует `.session`, а ядро законно берёт `Auth`
   из `eazy_sdk.auth`, и косвенный импорт уронил бы контракт (`import-linter` 2.15
   считает косвенные импорты по умолчанию). Поэтому проверяются две вещи разными
   инструментами: **модуль** `session` недостижим из ядра — контрактом; **имена**
   `SessionLifecycle`/`asyncio.Lock` есть только в `session.py` — тестом из п. 1.
   Граф импортов, который должен получиться после B7.2:
   - `client` → `content`, `errors`, `operations` (runtime); `state` клиенту больше не
     нужен — `require_state` переехал в `BrowserSession`, импорт удаляется;
     `eazy_sdk.auth.session`, `login` и `session` клиент не импортирует вовсе, даже под
     `TYPE_CHECKING`;
   - `session` → `login` (runtime: `BrowserLoginContext`, `BrowserSessionError`),
     `eazy_sdk.auth.session` (runtime: жизненный цикл), `state` (runtime:
     `require_state`); `client` — только под `TYPE_CHECKING`;
   - `login` → `session` лениво, внутри `BrowserLogin.session()`. `import-linter` видит и
     такие импорты, поэтому `login` в `source_modules` контракта не входит; что `login`
     не собирает жизненный цикл сам, проверяет тест из п. 1;
   - `session` добавляется в `source_modules` контрактов `browser-core-does-not-know-drivers`
     и `browser-core-does-not-know-integrations`: драйверов и интеграций он знать не должен.
     В `source_modules` нового контракта его нет: список для него — не копия, а копия без
     `login` и `session`.
   Опция `exclude_type_checking_imports` не нужна: ни один запрещённый путь не идёт через
   `TYPE_CHECKING`.
3. `Определение готовности` в `PLAN.md` п. 3 — дописать шестой контракт.

### B7.7. Документы

1. `README.md`, `docs/OVERVIEW.md` (раздел входа, `:253-271`), `docs/DESIGN.md` (§4 —
   новый абзац «Граница оркестрации» со ссылкой сюда; §5; §9 — `BrowserSessions` без
   драйвера).
2. `docs-site/src/content/docs/guides/browser/login.mdx` — переписать в три части:
   объявление входа (`BrowserLogin`, одно на сайт); **без пула** — `LOGIN.session(creds,
   store=...)`, один объект на контекст, клиенты всех вкладок контекста с `session=mail`,
   автологин и повторный вход показываются на исходах (~15 строк, копируемых и
   проверяемых); если сайту нужен localStorage — `storage_state` из хранилища
   (`to_storage_state(await sessions.load(account))`) до первой вкладки, а когда там
   пусто — первый контекст без `storage_state`, после входа `await mail.state(client)`
   даёт состояние для следующего. Явно сказать: сколько
   контекстов и вкладок, второй контекст того же аккаунта — второй объект **с тем же
   `store`**, обязательно общим: с `store=None` у каждого объекта своё хранилище в
   памяти, и после повторного входа они не согласуют ревизии; повторный вход меняет
   куки контекста под соседними вкладками, они
   получат тот же отказ и повторят. **С пулом** — абзац: входом владеет пул, клиент
   получает источник пула, повторный вход идёт в новом контексте; ссылка на
   `integrations/pool` (вне B7). Прогнать пример на Chromium, вывод сверить с текстом
   страницы (стиль — как в `docs-example-style`: разбор по шагам, варианты исходов).
3. `docs-site/src/content/docs/api-reference/browser.mdx` — `AsyncBrowserClient` с
   `session=`, `auth_retries` и `without_session()`, `BrowserLogin`, `SessionSource`,
   `BrowserSession`, `StateAware` (`export_state`, `add_cookies`, `context_key`),
   `to_storage_state`, `CapturePolicy`, `PlaywrightDriver` как context manager.
4. `docs/CORE_DEBT.md` — пункт 10: гонка `AccountPool.pick`/`lease`, `_leased` в памяти
   процесса (`pool.py:57,84,158`); как чинить — атомарный `acquire` с ожиданием или отказ
   от аренды в пользу слоя оркестрации.
5. `docs/PLAN.md` — в «Состояние» строки B7.1…B7.8 со ссылкой на этот документ; в §1 —
   этап B7 после B6.
6. `docs-site/api-fingerprints.lock.json` и `scripts/docs_freshness.py` — обновить отпечатки
   по штатной процедуре, если ворота их требуют.

### B7.8. Закрытие этапа

Полные ворота (ниже) плюс ручная сверка: пройти таблицу §0 и для каждой строки «слой выше»
указать, что в `eazy_sdk_browser` для неё нет кода (ссылка на пустой `grep` или
`test_layers.py`). Результат — в строку B7.8.

---

## 4. Ворота

На каждый шаг (из корня репозитория):

```bash
uv run pytest -q plugins/browser/tests
uv run mypy
uv run ruff check
uv run ruff format --check
uv run lint-imports
uv run complexipy --plain --failed
uv run basedpyright plugins/browser/probe/typing_probe.py   # сверить названные строки с ожидаемыми
```

Браузерные интеграционные тесты должны **проходить на Chromium**, а не пропускаться:
в доказательстве указывать число passed и что `integration` не skipped.

Перед закрытием B7 дополнительно:

```bash
uv run pytest -q                                   # весь workspace
uv run python scripts/docs_freshness.py check
cd docs-site && npm run check && npm run build
```

Известная помеха: полный прогон workspace уже зависал на этой машине в
`tests/integration/auth` (`socket.socketpair()`, см. `CORE_DEBT.md`, конец). Если зависнет
снова — записать в «Состояние» точную команду, тест и симптом, статус шага `blocked`, и не
выдавать частичный прогон за полный.

---

## 5. Правила автономной работы

1. Перед началом: `git status`, `git diff` — в рабочем дереве есть чужие незакоммиченные
   правки (ветка `feat/browser-plugin`); их не трогать, не откатывать, не форматировать.
2. Первый шаг со статусом не `complete` — текущий. Если строка в «Состоянии» говорит
   `active`, сначала проверить по коду и тестам, что из шага уже сделано.
3. Шаг закрывается только когда все его критерии и ворота пройдены; в «Доказательство» —
   дата, команды, числа (`N passed`, `mypy N files ok`) и имена новых тестов.
4. Никаких псевдонимов, обёрток, `DeprecationWarning` и второго пути: старая форма
   удаляется в том же шаге.
5. Не добавлять в плагин ничего из строк «слой выше» в §0, даже «для удобства»
   (жизненный цикл одного контекста — `BrowserSession` — в плагине и в §0 есть): если без этого
   не работает пример — пример становится кодом слоя выше в документации.
6. Правки в `eazy_sdk/` и `plugins/accounts/` в рамках B7 запрещены; найденное —
   в `CORE_DEBT.md`.
7. Если шаг упирается в решение, которого здесь нет (например, форма `ResponseView` ломает
   чужой код), — статус `blocked`, причина в строке, остановиться и спросить.

---

## 6. Состояние

| Шаг | Что | Статус | Доказательство |
|---|---|---|---|
| B7.1 | оси `proxy_per_context`, `isolated_contexts`, `stealth` удалены | complete | 2026-09-23: `profile.py`, `PLAYWRIGHT_PROFILE`, докстринги `operations.py`/`driver.py`, `test_unification.py` (`rich_text` вместо `stealth`), `DESIGN.md` §5 (абзац «требование к аренде»), `PLAN.md` B3.3, `browser.mdx`. `grep` критерия — пусто вне `LAYERS.md`/`LOGIN_SCOPE.md`/`decisions.md`. Ворота: pytest плагина + `test_foreign_routers` 187 passed, 0 skipped; mypy 394 files ok; ruff check/format ok; lint-imports 4 kept; complexipy без находок; пробник — ошибки на строках 39, 51, 73, как ожидается |
| B7.3 | `export_state` + `add_cookies` + `context_key` в `StateAware`; `to_storage_state`; `BrowserSessions.save` | complete | 2026-09-23: `StateAware` — `export_state`/`add_cookies`/`context_key`; `to_storage_state`/`from_storage_state` в `handlers/playwright.py`, `import_state`, `_seed_storage`, `add_init_script` удалены; `BrowserSessions.save` вместо `restore`/`remember`, интеграция не видит `Driver`. Отклонение: у фейка вместо параметра `context_key=` — общий `FakeContext` (`testing.py`): вкладки одного контекста делят и ключ, и состояние, и журнал `cookie_writes`, как в браузере. Временный мост: `_SignIn` в `client.py` звал `add_cookies` до B7.2 (удалён там же). Тесты: `test_session_state_moves_between_contexts` (через `new_context(storage_state=…)`), новые `test_restored_local_storage_is_not_rewritten_on_navigation`, `test_cookies_go_into_the_context_shared_by_its_tabs`, `test_storage_state_round_trip_keeps_every_cookie_attribute`, `test_save_and_load_through_storage`, `test_load_without_saved_session`. `grep` критерия: `import_state` только в исторических записях `PLAN.md` (строка B5 и текст B5.1). Ворота: 190 passed, 0 skipped (Chromium); mypy 394 ok; ruff/format ok; lint-imports 4 kept; complexipy ok; пробник 39/51/73 |
| B7.2 | `BrowserLogin` — объявление; клиент с `session=`; `BrowserSession` — один объект на контекст | complete | 2026-09-23: `login.py` — `BrowserLogin(service, cookies, expired, leeway, clock)` + `validate`/`is_expired`/`sign_in`/`refresh`/`session()` (ленивый импорт `session.py`, пустой `cookies` → `BrowserDeclarationError`), `BrowserLoginContext(client)`; `session.py` — `BrowserSession` по §2.1.1; `client.py` — `SessionSource`, `session=`, `without_session()`, `auth_retries`, `_SignIn`/`sign_in()`/`_run_signed_in`/`_login_context` удалены, импортов `asyncio`, `login`, `state`, `eazy_sdk.auth.session` нет; `session` добавлен в оба контракта `browser-core-*`. Отклонения: (1) в `SessionSource` третий метод `is_expired(error)` — клиент иначе не отличит отказ-истечение, а `BrowserLogin` он не знает; (2) контекст жизненного цикла — сам клиент без сессии (`TContext = AsyncBrowserClient`): `sign_in`/`refresh` принимают клиента и строят `BrowserLoginContext` сами; (3) `renew`, когда хранилище сессию забыло (`SessionCredentialsRequiredError` из `refresh_revision`), входит через `resolve`; (4) `state(client)` отдаёт `export_state()` контекста после `ensure` — там и ротированная кука, и localStorage. Тесты: `test_session.py` — 20 (весь список п. 6, `test_ensure_writes_…` на два случая, `test_retry_is_spent_once_…` для `auth_retries` 1 и 0), мутации трёх ветвей правила записи ловятся; `test_login.py` — п. 5 + `test_auth_retries_cannot_be_negative`; `test_login_persists_…` через `mail.state(client)`. `grep`: `SessionLifecycle`/`asyncio.Lock` — только `session.py`; `retries` — только `auth_retries` в `client.py`; `login=` и `sign_in()` в коде и примерах нет, остались в `README.md`, `OVERVIEW.md`, `login.mdx`, `browser.mdx` — их переписывает B7.7, и в исторических записях `PLAN.md`. Ворота: 210 passed, 0 skipped; mypy 396 ok; ruff/format ok; lint-imports 4 kept; complexipy ok; пробник 39/51/73 |
| B7.4 | драйвер живёт со страницей | complete | 2026-09-23: `PlaywrightDriver` — `__aenter__`/`__aexit__` → `aclose()`; подписка на `close` страницы, `_detach()` снимает `response`/`framenavigated`/`close` синхронно и один раз, `_remember` после отписки задач не создаёт; `aclose()` идемпотентен и ждёт начатые чтения; профиль после закрытия не меняется (раньше `aclose` снимал `network`). Контракт — докстринг класса и абзац «Время жизни драйвера» в `DESIGN.md` §4. Тесты (Chromium): `test_driver_detaches_itself_when_the_page_closes`, `test_drivers_wrapped_one_after_another_do_not_accumulate_listeners` (счёт подписок у `page._impl_obj` + `mark()` пяти закрытых драйверов = 0); без подписки на `close` оба падают (проверено мутацией). Ворота: 212 passed, 0 skipped; mypy 396 ok; ruff/format ok; lint-imports 4 kept; complexipy ok; пробник 39/51/73 |
| B7.5 | `CapturePolicy` вместо счётчика ответов | complete | 2026-09-23: `handlers/capture.py` — `CapturePolicy(resource_types, max_body_bytes, max_total_bytes)` и `DEFAULT_CAPTURE`, экспорт из `eazy_sdk_browser.handlers` (модуль без playwright); `PlaywrightDriver(page, capture=…)`, `capture=None` — сеть не слушается и профиль без `network`; `capture_network`, `capture_limit`, `CAPTURE_LIMIT` удалены. Тип ресурса — в `_remember` до чтения; размер — по `content-length` до чтения, иначе после; `ResponseView.body_dropped`, `ResponseMissingError(url, reason)` — `ApiValue.value()` бросает «тело больше лимита захвата», не разбирая пустоту; вытеснение по сумме байт, последний ответ остаётся всегда, `_seen` монотонен. `MERGE.md` §6 п. 10 — бюджет вместо `capture_limit`. Тесты (Chromium): `test_capture_limit_evicts_old_replies_but_keeps_positions` (байты, вытесненный `n=1` не найден), `test_images_and_fonts_are_not_read`, `test_oversized_body_is_marked_dropped_and_explained`, `test_profile_matches_…` с `capture=None`; мутации фильтра типа и признака `body_dropped` ловятся. Ворота: 214 passed, 0 skipped; mypy 397 ok; ruff/format ok; lint-imports 4 kept; complexipy ok; пробник 39/51/73 |
| B7.6 | `test_layers.py` и контракт `browser-core-does-not-orchestrate` | complete | 2026-09-23: `tests/test_layers.py` — разбор AST всех 30 модулей пакета (ядро, `handlers`, `integrations`, `testing`); запрещены вызовы `launch`, `launch_persistent_context`, `new_context`, `new_page`, `add_init_script`, `clear_cookies`, `set_extra_http_headers` и `asyncio.Semaphore`/`Queue`/`Condition` (атрибутом и импортом); исключения списком `ALLOWED`: `asyncio.Lock` и `SessionLifecycle` — `session.py`, `context.add_cookies` — `handlers/playwright.py`, вызов `add_cookies()` — `session.py` и драйвер; находка печатается как `модуль:строка: что`; `test_the_checker_finds_what_it_forbids` проверяет сам проверяющий на синтетическом модуле. Контракт `browser-core-does-not-orchestrate` (ядро без `login` и `session` не импортирует `eazy_sdk_browser.session`), `session` — в двух прежних контрактах (с B7.2). `PLAN.md` «Определение готовности» п. 3 и `DESIGN.md` §7 дописаны. Ворота: 217 passed, 0 skipped; mypy 398 ok; ruff/format ok; lint-imports **5 kept** (пятый контракт плана был признан лишним в B6, поэтому «пять + один» = пять); complexipy ok; пробник 39/51/73 |
| B7.7 | документы, docs-site, `CORE_DEBT.md` п. 10, `PLAN.md` | complete | 2026-09-23: `README.md` (вход через `MAIL_LOGIN.session`, `async with PlaywrightDriver`, раскладка с `session.py`, пять контрактов, `test_layers.py`), `OVERVIEW.md` (раздел входа, раскладка, пять правил + граница слоёв), `DESIGN.md` (§4 — «Время жизни драйвера» и «Граница оркестрации», §5 из B7.1, §7 из B7.6, §9 — `BrowserSessions` без драйвера), `MERGE.md` §6 п. 10; `guides/browser/login.mdx` переписан в три части (объявление; без пула — две вкладки одного контекста, повторный вход после отказа, второй контекст со `storage_state`, правила про общий `store`, соседние вкладки и localStorage; с пулом — абзац про `SessionSource` с `renew → None`) плюс хранилище аккаунтов и мост; пример прогнан на Chromium, вывод совпал со страницей: `две вкладки, входов: 1` / `после отказа, входов: 2` / `второй контекст, входов: 2` / `Cookie: sid=s2`; примеры `index.mdx` и `outcomes.mdx` перепрогнаны — вывод прежний; `api-reference/browser.mdx` — `session=`, `auth_retries`, `without_session()`, `BrowserLogin`, `SessionSource`, `BrowserSession`, `StateAware`, `to_storage_state`/`from_storage_state`, `BrowserSessions.save`, `PlaywrightDriver` как context manager, `CapturePolicy`, `FakeContext`; `CORE_DEBT.md` п. 10 (`AccountPool.pick`/`lease`); `PLAN.md` — строки B7 в «Состоянии» и B7 в §1. Отличие от плана: пример входа ждёт исход `visible(div.inbox)` у `SignIn` — вход на странице асинхронный, и без исхода `export_state` читал контекст до куки. Ворота документации: `docs_freshness update` для 4 страниц → `check` 71 fresh; `validate_docs.py` 86 pages OK; `sphinx-build -W` — одно предупреждение, чужое и известное с B6 (`guides/pagination.mdx` вне оглавления). `npm run check`/`build` не запускались: в `docs-site/` нет `package.json`, сайт собирается Sphinx по `docs-site/UPDATING.md` |
| B7.8 | полные ворота и сверка §0 | blocked | 2026-09-23. **Ворота плагина (§4):** 217 passed, 0 skipped (Chromium); mypy 398 ok; ruff check/format ok; lint-imports 5 kept; complexipy ok; пробник 39/51/73. **Документация:** `docs_freshness check` 71 fresh; `validate_docs.py` 86 pages OK; `sphinx-build -W` — только известное с B6 чужое предупреждение (`guides/pagination.mdx`). **Полный прогон `uv run pytest -q`** на этот раз завершился без зависания (3 мин 48 с): 1632 passed, 11 skipped, **1 failed** — `tests/rewrite/test_phase10_absence.py::test_removed_execution_architecture_is_absent`, `absence_audit` находит «former identity remains in content: docs/implementation/STATUS.md». К B7 не относится: `STATUS.md` не менялся с коммита 2026-09-09, в `scripts/absence_audit.py` в рабочем дереве только переформатирование, B7 не трогал ни то, ни другое; править основной план ядра в рамках B7 нельзя (§5 п. 6) — нужно решение пользователя. **Сверка §0, строки «слой выше»:** (1) контексты, вкладки, кто входит первым, прокси, антидетект — `test_layers.py` запрещает `launch`/`new_context`/`new_page`, `grep proxy\|stealth\|isolated` по `eazy_sdk_browser` — только фильтр HTTP-заголовков `proxy-*` в `integrations/handler.py`, `BrowserSession` карты контекстов не держит и чужой контекст отвергает; (2) когда и с каким `storage_state` создать контекст — `new_context` встречается только в докстрингах-примерах (`handlers/playwright.py`, `integrations/accounts.py`), `to_storage_state` — чистый перевод формата; (3) учётные данные, аккаунт, хранилище — выбор: у `BrowserLogin` нет полей `credentials`/`store`/`identity`, они приходят в `session(...)` готовыми, `grep AccountPool\|.pick(\|.lease(` — пусто; (4) очереди, семафоры, лимиты, аренда — `test_layers.py` (`Semaphore`/`Queue`/`Condition`), `grep` — пусто |

---

## 7. Не делается и почему

- **Жизненный цикл сессии на уровне аккаунта или страницы.** На уровне страницы — это
  `_SignIn` сегодня (N вкладок = N входов). На уровне аккаунта объект вынужден вести
  карту «какой контекст какую ревизию получил» и лок на каждый контекст — решение
  «когда контекстов несколько», которое принадлежит слою выше. Делается только
  жизненный цикл **одного контекста** (`BrowserSession`, §2.1.1): single-flight входа —
  это корректность сессии, а не оркестрация. Обоснование — `LOGIN_SCOPE.md` §3.
- **Кеш `BrowserSession` по аккаунту**, чтобы два объекта на один контекст стали одним.
  Это неявное глобальное состояние; правило «один объект на контекст» документируется,
  как «один `Auth` на аккаунт» в HTTP.
- **Фабрика страниц для входа** и режим «вход в отдельной вкладке». Это создание
  страниц — слой выше; вход идёт на странице первого упёршегося клиента.
- **Адаптер `BrowserLogin` → `SessionFlow` пула и источник сессии пула.** Форма §2.1.1
  их допускает, живут они в `integrations/pool` за пределами B7.
- **Дозаливка localStorage в живой контекст**, включая защиту «ключ пишется, только если
  его нет» для init-скрипта. Лечит симптом 1.2 и оставляет плагину запись, которую нельзя
  отменить; localStorage задаётся `storage_state` при создании контекста.
- **Page-scoped вложения, аренда, лимиты открытий, `DriverCapabilities`** — `browser-pool`.
- **Правка `AccountPool`** — плагин аккаунтов, через `CORE_DEBT.md`.
- **Переименование `Driver`/`BrowserProfile`** из-за совпадения с `browser-pool` — не
  дефект этой библиотеки.

## Определение готовности B7

1. Все строки «Состояния» — `complete` с доказательством.
2. В `eazy_sdk_browser` нет кода, отвечающего на вопросы строк «слой выше» в §0; это проверяет
   `tests/test_layers.py` и пять + один контракт `lint-imports`.
3. Одна операция и одно объявление входа работают на двух страницах двух разных
   контекстов без общего объекта плагина между ними, кроме хранилища сессий (тесты из
   B7.2 `test_one_login_declaration_serves_two_accounts`,
   `test_two_contexts_of_one_account_share_the_store_but_not_the_lock` + Chromium-тест
   из B7.3); N вкладок одного контекста с одним `BrowserSession` дают один вход
   (`test_one_session_object_serves_every_tab_of_its_context`).
4. Полные ворота §4 зелёные, интеграционные тесты не пропущены.
