# Фаза 62. Кука, которую ставит сервис входа

Зависит от фазы 58 (jar пользователя, I1) и фазы 59 (`cookies_of`, `ensure_session`).
Закрывает пробел (2) из приёмки фазы 58 (`58-identity-cookie-jar.md`, журнал, 2026-10-10):
«сервис входа не может отдать куки, добытые не операциями библиотеки».

## 1. Что не так

Сайт бывает устроен так, что сессионную куку ставит не сервер, а скрипт страницы. Рамблер/почта:
`POST /mail-auth-id/pub/api/v1/auth/login` отвечает `{token, expires_at, …}` и ставит только
`refresh_token`; каждый следующий запрос веб-клиента несёт куку `rsid` со значением `token`,
которую записал JS. Проверено вживую 2026-10-26: `POST /ajax/compose/` проходит с одной `rsid`
и без остальных кук.

Объявить это нечем. По I1 кука сервера едет через jar, кука вызывающего — `Cookie[str]`.
`rsid` — ни то ни другое: значение добывает сервис входа. `Placed.cookie` удалён в 58.5 и не
возвращается. `CookieScheme` держит статичное значение без лайфсайкла входа и не сочетается с
`Cookies()` (`_operation_cookies`). Обходной путь в SDK — собирать заголовок `Cookie` вручную
или держать две identity — запрещён I1 и `anti-patterns.md` скилла.

## 2. Инварианты

- **I1 фазы 58 остаётся.** Кука, которую ставит сайт — сервер или скрипт его страницы, —
  едет только через jar. Эта фаза добавляет второй источник записи в тот же jar, а не второй
  способ отправки.
- **I2.** Записать куку может только сервис сессии, внутри `acquire`/`refresh`: тот код,
  который в браузере выполнял бы скрипт страницы. У операций и у корня SDK записи нет.
- **I3.** Записанная кука неотличима от поставленной сервером: та же область действия, тот же
  выбор по адресу, тот же снимок рядом с сессией, та же проверка `Cookies(required=...)`.

## 3. API

```python
from eazy_sdk.auth import AuthContext
from eazy_sdk.cookies import StoredCookie


class LoginService:
    async def acquire(self, credentials: Login, context: AuthContext[Any]) -> Session:
        signed = await context.sdk.auth.sign_in(email=..., password=...)
        context.set_cookie(
            StoredCookie("rsid", signed.token, "mail.rambler.ru", secure=True,
                         expires_at=signed.expires_at)
        )
        return Session(email=..., expires_at=signed.expires_at)


class RamblerSdk(AsyncRoot):
    cookies = Cookies(required=("rsid",))
```

`AuthContext.set_cookie(cookie: StoredCookie) -> None` — положить куку в jar пользователя,
как это сделал бы скрипт страницы. Кука с тем же ключом (имя, домен, путь) заменяется;
просроченная не кладётся (поведение `CookieJar.load`).

С `required=("rsid",)` сессия без живой `rsid` недействительна и добывается заново: срок куки
— это срок токена.

## 4. Диагностики

| Код | Условие | Ошибка |
|---|---|---|
| D-62-01 | `set_cookie` на сервисе без `Cookies(...)` | `SessionConfigurationError`: куку некуда положить, объявите `Cookies(...)` |

## 5. Шаги

### 62.1. Запись куки из сервиса

- `eazy_sdk/auth/session_runtime.py`: `AuthContext.set_cookie`, реализация в
  `AuthFlowContext`; `SessionProvider._context` передаёт jar identity.
- `eazy_sdk/auth/session.py`: `SessionLifecycle.jar` — jar, привязанный `bind_cookies`.
- Тесты `tests/unit/test_phase62_service_cookies.py`: кука уходит в следующий запрос;
  сохраняется в снимке сессии; `required` на записанной куке — сессия с просроченной кукой
  добывается заново; замена по ключу; D-62-01.

### 62.2. Документация

- `guide/auth/cookies-session.mdx`: раздел «Куку ставит скрипт страницы».
- `CHANGELOG.md`.

## 6. Критерии выхода

1. Тесты 62.1 проходят; `uv run pytest -q`, `uv run mypy`, `uv run ruff check` чисты.
2. `uv run python scripts/docs_freshness.py check` чист.
3. Приёмка: `rambler-sdk` отправляет письмо операцией `compose.send` по HTTP на локальной
   сборке библиотеки, офлайн-replay по обмену #953 проходит.

## 7. Журнал

| Дата | Шаг | Запись |
|---|---|---|
| 2026-10-26 | план | Решение владельца: вариант «сервис пишет куку в jar» вместо возврата `Placed.cookie`; I1 не меняется. Работа в отдельном worktree, ветка `feat/phase62-service-cookies`, чтобы не задеть незакоммиченную фазу 61 в основной копии. |
| 2026-10-26 | 62.1 | done. `AuthContext.set_cookie`, `AuthFlowContext._jar`, `SessionLifecycle.jar`; D-62-01. `tests/unit/test_phase62_service_cookies.py` — 6 passed. |
| 2026-10-26 | 62.2 | done. Раздел «Куку ставит скрипт страницы» в `guide/auth/cookies-session.mdx` (`AuthContext` добавлен в `sources`, отпечаток пересчитан), `CHANGELOG.md` → Unreleased. |
| 2026-10-26 | гейты | `uv run pytest -q` — 2205 passed, 11 skipped, 584 с; `uv run mypy` — 553 файла чисто; `uv run ruff check` — чисто; `uv run lint-imports` — 5 контрактов; `docs_freshness.py check` — 85 страниц свежие. `npm run check`/`build` в `docs-site/` не запускались. |
| 2026-10-26 | приёмка | `rambler-sdk` на локальной сборке ветки: `ComposeApi.send` (`POST /ajax/compose/`) с сессией через `set_cookie("rsid")` и `Cookies(required=("rsid",))`. Скилл `eazy-sdk-from-capture`: G1–G10 PASS, 5 сценариев (вход ×2, неверный пароль, вход+отправка ×2 по #596→#953 и #82→#404). Живой прогон: отправка по HTTP `uid=9`, запасной браузерный путь на тех же куках `uid=10`. Срок у `rsid` в SDK не ставится: с `expires_at` токена replay записи зависит от текущего времени (токен из записи уже истёк). Критерии выхода 1 и 3 выполнены; 2 выполнен. Выпуск — по команде владельца. |
