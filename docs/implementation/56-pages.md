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
| `api-reference/api-methods` | `reference/api/api-methods` | ok |  |  |  | n/a |
| `api-reference/auth` | `reference/api/auth` | ok |  |  |  | n/a |
| `api-reference/browser` | `reference/api/browser` | ok |  |  |  | n/a |
| `api-reference/clients` | `reference/api/clients` | ok |  |  |  | n/a |
| `api-reference/crypto` | `reference/api/crypto` | ok |  |  |  | n/a |
| `api-reference/dependencies` | `reference/api/dependencies` | ok |  |  |  | n/a |
| `api-reference/extensions` | `reference/api/extensions` | ok |  |  |  | n/a |
| `api-reference/index` | `reference/api/index` | ok |  |  |  | n/a |
| `api-reference/middleware` | `reference/api/middleware` | ok |  |  |  | n/a |
| `api-reference/models-codecs` | `reference/api/models-codecs` | ok |  |  |  | n/a |
| `api-reference/openapi` | `reference/api/openapi` | ok |  |  |  | n/a |
| `api-reference/protection` | `reference/api/protection` | ok |  |  |  | n/a |
| `api-reference/registration` | `reference/api/registration` | ok |  |  |  | n/a |
| `api-reference/request` | `reference/api/request` | ok |  |  |  | n/a |
| `api-reference/response` | `reference/api/response` | ok |  |  |  | n/a |
| `api-reference/session` | `reference/api/session` | ok |  |  |  | n/a |
| `api-reference/signing` | `reference/api/signing` | ok |  |  |  | n/a |
| `api-reference/sqlmodel` | `reference/api/sqlmodel` | ok |  |  |  | n/a |
| `api-reference/testing` | `reference/api/testing` | ok |  |  |  | n/a |
| `api-reference/verification` | `reference/api/verification` | ok |  |  |  | n/a |
| `api-reference/xml` | `reference/api/xml` | ok |  |  |  | n/a |
| `auth/api-key` | `guide/auth/api-key` | ok |  |  |  | n/a |
| `auth/basic` | `guide/auth/basic` | ok |  |  |  | n/a |
| `auth/bearer` | `guide/auth/bearer` | ok |  |  |  | n/a |
| `auth/combined` | `guide/auth/combined` | ok |  |  |  | n/a |
| `auth/cookie` | `guide/auth/cookie` | ok |  |  |  | n/a |
| `auth/index` | `guide/auth/index` | ok |  |  |  | n/a |
| `auth/jwt` | `guide/auth/jwt` | ok |  |  |  | n/a |
| `auth/login` | `guide/auth/login` | ok |  |  |  | n/a |
| `auth/refresh` | `guide/auth/refresh` | ok |  |  |  | n/a |
| `auth/registration` | `integrations/accounts/registration` | ok |  |  |  | n/a |
| `auth/session` | `guide/auth/session` | ok |  |  |  | n/a |
| `auth/verification` | `integrations/accounts/verification` | ok |  |  |  | n/a |
| `clients/curl-cffi` | `integrations/http/curl-cffi` | ok |  |  |  | n/a |
| `clients/httpx` | `integrations/http/httpx` | ok |  |  |  | n/a |
| `clients/index` | `integrations/http/index` | ok |  |  |  | n/a |
| `clients/requests` | `integrations/http/requests` | ok |  |  |  | n/a |
| `getting-started/index` | `start/index` | ok |  |  |  |  |
| `getting-started/installation` | `start/installation` | ok | n/a | ok | ok | ok |
| `getting-started/quickstart` | `start/quickstart` | ok | ok | ok | ok | ok |
| `guides/browser/browser-pool` | `integrations/browser/browser-pool` | ok |  |  |  | n/a |
| `guides/browser/index` | `integrations/browser/index` | ok |  |  |  | n/a |
| `guides/browser/login` | `integrations/browser/login` | ok |  |  |  | n/a |
| `guides/browser/outcomes` | `integrations/browser/outcomes` | ok |  |  |  | n/a |
| `guides/dependencies` | `guide/dependencies` | ok |  |  |  | n/a |
| `guides/index` | `guide/index` | ok |  |  |  | n/a |
| `guides/middleware` | `guide/middleware` | ok |  |  |  | n/a |
| `guides/multi-service` | `guide/multi-service` | ok |  |  |  | n/a |
| `guides/openapi` | `integrations/generators/openapi` | ok |  |  |  | n/a |
| `guides/pagination` | `guide/pagination` | ok |  |  |  | n/a |
| `guides/payload-crypto` | `guide/encryption` | ok |  |  |  | n/a |
| `guides/protection/cloudflare` | `integrations/protection/cloudflare` | ok |  |  |  | n/a |
| `guides/protection/index` | `guide/protection` | ok |  |  |  | n/a |
| `guides/protection/recaptcha` | `integrations/protection/recaptcha` | ok |  |  |  | n/a |
| `guides/protection/turnstile` | `integrations/protection/turnstile` | ok |  |  |  | n/a |
| `guides/protocols` | `guide/protocols` | ok |  |  |  | n/a |
| `guides/reliability/rate-limit` | `guide/reliability/rate-limit` | ok |  |  |  | n/a |
| `guides/reliability/redirect` | `guide/reliability/redirect` | ok |  |  |  | n/a |
| `guides/reliability/retry` | `guide/reliability/retry` | ok |  |  |  | n/a |
| `guides/requests/bytes` | `guide/requests/bytes` | ok |  |  |  | n/a |
| `guides/requests/cookies` | `guide/requests/cookies` | ok |  |  |  | n/a |
| `guides/requests/declarative` | `guide/requests/declarative` | ok |  |  |  | n/a |
| `guides/requests/form` | `guide/requests/form` | ok |  |  |  | n/a |
| `guides/requests/headers` | `guide/requests/headers` | ok |  |  |  | n/a |
| `guides/requests/index` | `guide/requests/index` | ok |  |  |  | n/a |
| `guides/requests/json` | `guide/requests/json` | ok |  |  |  | n/a |
| `guides/requests/multipart` | `guide/requests/multipart` | ok |  |  |  | n/a |
| `guides/requests/path` | `guide/requests/path` | ok |  |  |  | n/a |
| `guides/requests/query` | `guide/requests/query` | ok |  |  |  | n/a |
| `guides/requests/representation` | `guide/requests/representation` | ok |  |  |  | n/a |
| `guides/requests/values` | `guide/requests/values` | ok |  |  |  | n/a |
| `guides/responses/errors` | `guide/responses/errors` | ok |  |  |  | n/a |
| `guides/responses/html` | `guide/responses/html` | ok |  |  |  | n/a |
| `guides/responses/index` | `guide/responses/index` | ok |  |  |  | n/a |
| `guides/responses/success` | `guide/responses/success` | ok |  |  |  | n/a |
| `guides/serialization` | `integrations/models/index` | ok |  |  |  | n/a |
| `guides/sqlmodel` | `integrations/accounts/sqlmodel` | ok |  |  |  | n/a |
| `guides/websocket` | `guide/websocket` | ok |  |  |  | n/a |
| `guides/xml` | `integrations/documents/xml` | ok |  |  |  | n/a |
| `index` | `index` | ok |  |  |  | n/a |
| `more/examples/html-login` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/examples/index` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/examples/json-auth` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/examples/signed-api` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/examples/store-sdk` | удаляется в 56.4 | n/a | n/a | n/a | n/a | n/a |
| `more/migration` | `reference/migration` | ok |  |  |  | n/a |
| `signing` | `guide/signing` | ok |  |  |  | n/a |

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
