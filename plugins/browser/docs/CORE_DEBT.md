# Долг ядра, найденный браузерным плагином

Задачи для `eazy_sdk` и соседних плагинов, а не для `eazy_sdk_browser`. Плагин их обходит,
но чинить их в плагине значило бы закрепить обход. Пункты 1–9 проверены по коду
2026-09-14 (`PLAN.md`, B6.2), пункт 10 — 2026-09-23 (`LAYERS.md` §1.6); закрытый пункт
помечается здесь со ссылкой на правку.

| # | Что | Где сейчас | Чем мешает | Как чинить |
|---|---|---|---|---|
| 1 | `SuccessOutcome` не экспортирован | `eazy_sdk/response/cases.py:518`; в `eazy_sdk/response/__init__.py` его нет | адаптер разбора ответа (`eazy_sdk_browser/integrations/eazy_sdk.py:36`) импортирует его мимо публичной поверхности, иначе успех не отличить от отказа | экспорт из `eazy_sdk.response` |
| 2 | `BAN`, `FREEZE` и `Order` не экспортированы | `eazy_sdk_accounts/storage/services/restrictions.py:27-28`, `storage/services/pool.py:37`; в `storage/__init__.py` их нет | SDK, замораживающий аккаунт по отказу антибота, импортирует строки ограничений из служб | экспорт из `eazy_sdk_accounts.storage` |
| 3 | Две модели куки, обе неполные | `SessionData.cookies: dict[str, str]` (`eazy_sdk_accounts/storage/entities.py:25`) и `HttpCookieSession` (`eazy_sdk/auth/cookies.py:15`) | браузерная сессия не помещается в `SessionData.cookies`: полное состояние лежит в `params["browser_state"]`, а плоский словарь — проекция (`integrations/accounts.py`) | одна модель куки с `domain`, `path`, сроком и флагами для `SessionData` и HTTP |
| 4 | `open_workspace` отдаёт не `AccountWorkspace` | `eazy_sdk_sqlmodel/factory.py:105` (`SqlAccountWorkspace`), `:334` | у `SqlAccountWorkspace` нет служб `sessions.save/active/invalidate`, `history`, `restrictions`, `pool` — `BrowserSessions` и пул аккаунтов на SQL-хранилище не работают | `open_workspace` над теми же службами, что `AccountWorkspace` |
| 5 | `SessionCodec` объявлен дважды | `eazy_sdk/auth/session_runtime.py:59`, `eazy_sdk_accounts/storage/session_bridge.py:19` | два одинаковых протокола одного имени; кодек плагина (`BrowserStateCodec`) удовлетворяет обоим случайно | один протокол в `eazy_sdk.auth.session`, второй — реэкспорт |
| 6 | Службы хранилища типизированы `account: Any` | `eazy_sdk_accounts/storage/services/sessions.py:39`, `:51`, `:54` | потребитель со строгим линтом (`ANN401`) делает свою обёртку generic по типу аккаунта, чтобы не протащить `Any` | параметр типа аккаунта у служб |
| 7 | Нет cookie-привязки, принимающей сессию со стороны | `session_cookie(...)` требует `credentials` и `service` (`eazy_sdk/auth/session_runtime.py:618`) и читает `Set-Cookie` из HTTP-ответов; `Auth()` закрыт, `Auth._bind` приватный | мост из браузера (`browser_cookie_auth`) отдаёт статический `Auth` через `CookieScheme.static`: после повторного входа в браузере его пересобирают руками, HTTP-клиент сам сессию не обновит | `session_cookie(..., session=)` или привязка с `adopt` без HTTP-входа, обновляемая через `SessionLifecycle` |
| 8 | `op()` теряет сигнатуру конструктора самопубликуемой операции | перегрузка `op[TDescriptor](operation: type[_PublishesItself[TDescriptor]]) -> TDescriptor` (`eazy_sdk/api.py:931-932`) | поля браузерной и WebSocket-операции типизатор видит как `...`: `portal.submit_company(name=1)` не ошибка (`probe/typing_probe.py`) | перегрузка `op` с `ParamSpec` конструктора для самопубликуемых операций |
| 9 | ~~`.gitignore` скрывает `plugins/*/docs/*.md`~~ | **закрыто:** `.gitignore:77` — `!plugins/*/docs/*.md` | — | — |
| 10 | Гонка между `AccountPool.pick` и `lease` | `plugins/accounts/eazy_sdk_accounts/storage/services/pool.py:84` (`pick`), `:158` (`lease`); занятые — множество `_leased` в памяти процесса (`:57`) | два исполнителя между `pick` и `lease` берут один аккаунт: `pick` не резервирует, а `lease` бросает уже после выбора; между процессами `_leased` не виден вовсе. Браузерный плагин `AccountPool` после B7 не использует (`LAYERS.md` §1.6) | атомарный `acquire` с ожиданием свободного аккаунта и резервом в хранилище — или отказ от аренды в пользу слоя оркестрации (`browser-pool`) |

Не долг ядра, но важно для закрытия этапов плагина: полный прогон workspace (`uv run pytest -q`)
в этапах B4 и B5 не завершился на машине разработки — HTTP-тесты ядра в
`tests/integration/auth` зависали на localhost (`socket.socketpair()`,
`GetQueuedCompletionStatus`). Прогон нужно повторить на исправной машине.
