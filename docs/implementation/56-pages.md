# Фаза 56. Очередь страниц

Рабочий файл к [плану](56-documentation-overhaul.md). Строка страницы обновляется в том же
коммите, что и сама страница. Отметки: пусто - не начато, `ok` - сделано и проверено,
`n/a` - к странице не относится.

Столбцы: «перенос» - этап 56.1; «примеры» - код взят из `examples/`, обвязки нет;
«текст» - аудит и правка humanizer-ru; «линтер» - ноль ошибок линтера навыка;
«слепая» - проверка свежим агентом, нужна только там, где стоит не `n/a`.

## Существующие страницы

| Страница | Новый путь | перенос | примеры | текст | линтер | слепая |
|---|---|---|---|---|---|---|
| `api-reference/api-methods` | `reference/api/api-methods` |  |  |  |  | n/a |
| `api-reference/auth` | `reference/api/auth` |  |  |  |  | n/a |
| `api-reference/browser` | `reference/api/browser` |  |  |  |  | n/a |
| `api-reference/clients` | `reference/api/clients` |  |  |  |  | n/a |
| `api-reference/crypto` | `reference/api/crypto` |  |  |  |  | n/a |
| `api-reference/dependencies` | `reference/api/dependencies` |  |  |  |  | n/a |
| `api-reference/extensions` | `reference/api/extensions` |  |  |  |  | n/a |
| `api-reference/index` | `reference/api/index` |  |  |  |  | n/a |
| `api-reference/middleware` | `reference/api/middleware` |  |  |  |  | n/a |
| `api-reference/models-codecs` | `reference/api/models-codecs` |  |  |  |  | n/a |
| `api-reference/openapi` | `reference/api/openapi` |  |  |  |  | n/a |
| `api-reference/protection` | `reference/api/protection` |  |  |  |  | n/a |
| `api-reference/registration` | `reference/api/registration` |  |  |  |  | n/a |
| `api-reference/request` | `reference/api/request` |  |  |  |  | n/a |
| `api-reference/response` | `reference/api/response` |  |  |  |  | n/a |
| `api-reference/session` | `reference/api/session` |  |  |  |  | n/a |
| `api-reference/signing` | `reference/api/signing` |  |  |  |  | n/a |
| `api-reference/sqlmodel` | `reference/api/sqlmodel` |  |  |  |  | n/a |
| `api-reference/testing` | `reference/api/testing` |  |  |  |  | n/a |
| `api-reference/verification` | `reference/api/verification` |  |  |  |  | n/a |
| `api-reference/xml` | `reference/api/xml` |  |  |  |  | n/a |
| `auth/api-key` | `guide/auth/api-key` |  |  |  |  | n/a |
| `auth/basic` | `guide/auth/basic` |  |  |  |  | n/a |
| `auth/bearer` | `guide/auth/bearer` |  |  |  |  | n/a |
| `auth/combined` | `guide/auth/combined` |  |  |  |  | n/a |
| `auth/cookie` | `guide/auth/cookie` |  |  |  |  | n/a |
| `auth/index` | `guide/auth/index` |  |  |  |  | n/a |
| `auth/jwt` | `guide/auth/jwt` |  |  |  |  | n/a |
| `auth/login` | `guide/auth/login` |  |  |  |  | n/a |
| `auth/refresh` | `guide/auth/refresh` |  |  |  |  | n/a |
| `auth/registration` | `integrations/accounts/registration` |  |  |  |  | n/a |
| `auth/session` | `guide/auth/session` |  |  |  |  | n/a |
| `auth/verification` | `integrations/accounts/verification` |  |  |  |  | n/a |
| `clients/curl-cffi` | `integrations/http/curl-cffi` |  |  |  |  | n/a |
| `clients/httpx` | `integrations/http/httpx` |  |  |  |  | n/a |
| `clients/index` | `integrations/http/index` |  |  |  |  | n/a |
| `clients/requests` | `integrations/http/requests` |  |  |  |  | n/a |
| `getting-started/index` | `start/index` |  |  |  |  |  |
| `getting-started/installation` | `start/installation` |  |  |  |  |  |
| `getting-started/quickstart` | `start/quickstart` |  |  |  |  |  |
| `guides/browser/browser-pool` | `integrations/browser/browser-pool` |  |  |  |  | n/a |
| `guides/browser/index` | `integrations/browser/index` |  |  |  |  | n/a |
| `guides/browser/login` | `integrations/browser/login` |  |  |  |  | n/a |
| `guides/browser/outcomes` | `integrations/browser/outcomes` |  |  |  |  | n/a |
| `guides/dependencies` | `guide/dependencies` |  |  |  |  | n/a |
| `guides/index` | `guide/index` |  |  |  |  | n/a |
| `guides/middleware` | `guide/middleware` |  |  |  |  | n/a |
| `guides/multi-service` | `guide/multi-service` |  |  |  |  | n/a |
| `guides/openapi` | `integrations/generators/openapi` |  |  |  |  | n/a |
| `guides/pagination` | `guide/pagination` |  |  |  |  | n/a |
| `guides/payload-crypto` | `guide/encryption` |  |  |  |  | n/a |
| `guides/protection/cloudflare` | `integrations/protection/cloudflare` |  |  |  |  | n/a |
| `guides/protection/index` | `guide/protection` |  |  |  |  | n/a |
| `guides/protection/recaptcha` | `integrations/protection/recaptcha` |  |  |  |  | n/a |
| `guides/protection/turnstile` | `integrations/protection/turnstile` |  |  |  |  | n/a |
| `guides/protocols` | `guide/protocols` |  |  |  |  | n/a |
| `guides/reliability/rate-limit` | `guide/reliability/rate-limit` |  |  |  |  | n/a |
| `guides/reliability/redirect` | `guide/reliability/redirect` |  |  |  |  | n/a |
| `guides/reliability/retry` | `guide/reliability/retry` |  |  |  |  | n/a |
| `guides/requests/bytes` | `guide/requests/bytes` |  |  |  |  | n/a |
| `guides/requests/cookies` | `guide/requests/cookies` |  |  |  |  | n/a |
| `guides/requests/declarative` | `guide/requests/declarative` |  |  |  |  | n/a |
| `guides/requests/form` | `guide/requests/form` |  |  |  |  | n/a |
| `guides/requests/headers` | `guide/requests/headers` |  |  |  |  | n/a |
| `guides/requests/index` | `guide/requests/index` |  |  |  |  | n/a |
| `guides/requests/json` | `guide/requests/json` |  |  |  |  | n/a |
| `guides/requests/multipart` | `guide/requests/multipart` |  |  |  |  | n/a |
| `guides/requests/path` | `guide/requests/path` |  |  |  |  | n/a |
| `guides/requests/query` | `guide/requests/query` |  |  |  |  | n/a |
| `guides/requests/representation` | `guide/requests/representation` |  |  |  |  | n/a |
| `guides/requests/values` | `guide/requests/values` |  |  |  |  | n/a |
| `guides/responses/errors` | `guide/responses/errors` |  |  |  |  | n/a |
| `guides/responses/html` | `guide/responses/html` |  |  |  |  | n/a |
| `guides/responses/index` | `guide/responses/index` |  |  |  |  | n/a |
| `guides/responses/success` | `guide/responses/success` |  |  |  |  | n/a |
| `guides/serialization` | `integrations/models/index` |  |  |  |  | n/a |
| `guides/sqlmodel` | `integrations/accounts/sqlmodel` |  |  |  |  | n/a |
| `guides/websocket` | `guide/websocket` |  |  |  |  | n/a |
| `guides/xml` | `integrations/documents/xml` |  |  |  |  | n/a |
| `index` | `index` |  |  |  |  | n/a |
| `more/examples/html-login` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/examples/index` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/examples/json-auth` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/examples/signed-api` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/examples/store-sdk` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/migration` | `reference/migration` |  |  |  |  | n/a |
| `signing` | `guide/signing` |  |  |  |  | n/a |

## Новые страницы

| Страница | Этап | написана | примеры | текст | линтер | слепая |
|---|---|---|---|---|---|---|
| `start/concepts` | 56.3 |  |  |  |  |  |
| `start/next` | 56.3 |  |  |  |  |  |
| `tutorial/index` | 56.4 |  |  |  |  |  |
| `tutorial/login` | 56.4 |  |  |  |  |  |
| `tutorial/login-failures` | 56.4 |  |  |  |  |  |
| `tutorial/captcha` | 56.4 |  |  |  |  |  |
| `tutorial/session` | 56.4 |  |  |  |  |  |
| `tutorial/messages` | 56.4 |  |  |  |  |  |
| `tutorial/send` | 56.4 |  |  |  |  |  |
| `tutorial/encryption` | 56.4 |  |  |  |  |  |
| `tutorial/assembly` | 56.4 |  |  |  |  |  |
| `guide/testing` | 56.5 |  |  |  |  | n/a |
| `integrations/index` | 56.6 |  |  |  |  | n/a |
| `integrations/http/custom-handler` | 56.6 |  |  |  |  | n/a |
| `integrations/browser/playwright` | 56.6 |  |  |  |  | n/a |
| `integrations/browser/pydoll` | 56.6 |  |  |  |  | n/a |
| `integrations/models/pydantic` | 56.6 |  |  |  |  | n/a |
| `integrations/models/msgspec` | 56.6 |  |  |  |  | n/a |
| `integrations/models/dataclass` | 56.6 |  |  |  |  | n/a |
| `integrations/models/adaptix` | 56.6 |  |  |  |  | n/a |
| `integrations/documents/html` | 56.6 |  |  |  |  | n/a |
| `integrations/generators/asyncapi` | 56.6 |  |  |  |  | n/a |
| `integrations/accounts/index` | 56.6 |  |  |  |  | n/a |
| `architecture/index` | 56.7 |  |  |  |  | n/a |
| `architecture/request-path` | 56.7 |  |  |  |  | n/a |
| `architecture/attempts` | 56.7 |  |  |  |  | n/a |
| `architecture/response-cases` | 56.7 |  |  |  |  | n/a |
| `architecture/identity` | 56.7 |  |  |  |  | n/a |
| `architecture/extension-points` | 56.7 |  |  |  |  | n/a |
| `architecture/browser` | 56.7 |  |  |  |  | n/a |
| `reference/changelog` | 56.8 |  |  |  |  | n/a |
