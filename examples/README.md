# Примеры Eazy SDK

Здесь не набор несвязанных сниппетов, а короткий маршрут от первой ручки до SDK с
авторизацией, подписью и шифрованием. Начните с первого файла и двигайтесь вниз.

## Подготовка

Из корня репозитория установите зависимости:

```bash
uv sync --all-packages --all-extras
```

## Сквозной пример почтового SDK

Новый учебный пример живёт отдельно от старых самостоятельных сценариев:

```text
examples/mail/
  site/       общий учебный сайт и адаптеры транспортов
  http/       HTTP-линия SDK, по запускаемому модулю на главу
  browser/    браузерная линия SDK, по запускаемому модулю на главу
  __main__.py единая команда после сборки обеих линий
```

`MailSite` в `site/` хранит учебные аккаунты, письма и сессии в памяти. Один объект отвечает
HTTP-клиенту через `handle_httpx()` и браузеру через `intercept_page()`: обе линии видят те же
формы входа, токены, страницы писем и результаты отправки. Внешняя сеть этому сайту не нужна.

Фрагменты для документации ограничиваются парными комментариями:

```python
# region docs: http-login-router
class LoginApi(SyncApi):
    ...
# endregion docs: http-login-router
```

Имя фрагмента после `docs:` состоит из линии, темы и роли, записанных через дефис. Оно уникально
в `examples/mail/`. Начало отмечается `# region docs: <имя>`, конец -
`# endregion docs: <имя>`. Метки не вкладываются друг в друга и стоят непосредственно вокруг
показываемого кода. Страница включает такой участок через `literalinclude` с `:start-after:` и
`:end-before:`, а рядом даёт полный запускаемый файл. Благодаря этому код страницы, тестов и
примера остаётся одним и тем же кодом, а сами метки проходят Ruff.

Три примера обращаются к публичным учебным сервисам. Остальные используют
`httpx.MockTransport` или временный localhost server, поэтому воспроизводятся без внешней сети.

| Уровень | Файл | Что показывает | Сеть |
| --- | --- | --- | --- |
| 1 | [`quickstart.py`](quickstart.py) | typed GET, path parameter, Pydantic response | нет |
| 2 | [`jsonplaceholder_posts.py`](jsonplaceholder_posts.py) | JSON, query, request body, aliases, `.with_response()` | JSONPlaceholder |
| 3 | [`flat_model_wire_body.py`](flat_model_wire_body.py) | плоская public-операция, вложенный wire JSON, константы, timestamp и device context | нет |
| 4 | [`adaptix_nested_wire_body.py`](adaptix_nested_wire_body.py) | Adaptix, многоуровневый JSON, defaults и генератор `datetime` | нет |
| 5 | [`books_to_scrape.py`](books_to_scrape.py) | HTML, CSS selectors, nested scopes, pagination | Books to Scrape |
| 6 | [`response_cases.py`](response_cases.py) | success case и типизированные 404/429 | нет |
| 7 | [`request_values.py`](request_values.py) | запрос как значение: `request()`, `evolve()`, `send()`, пагинация без абстракции | нет |
| 8 | [`dummyjson_auth.py`](dummyjson_auth.py) | login, `SecretStr`, Bearer token | DummyJSON |
| 9 | [`dummyjson_session_auth.py`](dummyjson_session_auth.py) | автоматические login, session reuse, refresh и replay | нет |
| 10 | [`docs/store_sdk.py`](docs/store_sdk.py) | Bearer + HMAC + field/body crypto | нет |

## 1. Первая JSON-ручка

```bash
uv run python examples/quickstart.py
```

```text
Mechanical keyboard 12900
```

Операция — это frozen-класс: его поля описывают запрос, `__http__` — метод и путь, а
`HttpOperation[Product]` называет тип успешного ответа. `op(GetProduct)` публикует операцию на
роутере, и её публичной сигнатурой становится конструктор класса.

Короткая форма того же самого — декоратор `@api.get(...)`; он синтезирует такой же класс. Обе
формы описаны на странице
[«Объявление операции»](../docs-site/src/content/docs/guides/requests/declarative.mdx).

## 2. JSON API: чтение, фильтр и создание

[JSONPlaceholder](https://jsonplaceholder.typicode.com/guide/) — публичный REST sandbox.
Пример читает один пост, передаёт `userId` как query parameter и отправляет плоский JSON.
Каждая операция хранит все HTTP-поля в своём `TypedDict` и раскрывает его через `Unpack`:

```bash
uv run python examples/jsonplaceholder_posts.py
```

Обратите внимание на разделение имён: поля в Python называются `user_id`, а на провод уходит
`userId`. Здесь операции объявлены Pydantic-моделями, поэтому имя на проводе задаёт
`serialization_alias` вместе с `serialize_by_alias=True`, а `validation_alias` остаётся у модели
ответа. Конструктор класса даёт IDE точные keyword arguments: caller пишет
`create_post(user_id=..., title=..., body=...)`. JSONPlaceholder имитирует запись и возвращает
результат, но не сохраняет его.

`.with_response()` нужен, когда кроме модели важны status, headers или другой transport context:

```python
response = posts.get_post.with_response(post_id=1)
print(response.status_code, response.value.title)
```

## 3. Плоская public-операция и вложенный wire JSON

Пользователь SDK передаёт четыре плоских поля операции `RegisterUser`. `BodyProjection` вызывает
typed `RegisterUserProjection`, который строит приватный `RegisterUserWire`, добавляет константы
из `RegisterWireSettings`, вызывает `timestamp_factory` при каждой подготовке запроса и берёт
`device_id`/`device_type` из `DeviceContext`, который приложение один раз зарегистрировало в
`DependencyRegistry`:

```bash
uv run python examples/flat_model_wire_body.py
```

```text
registered: john as user-42
```

Пример проверяет фактически отправленный JSON body, включая вложенные `account`, `profile`,
скрытые client defaults, целочисленный timestamp и блок `device`. Чтобы тестировать генерацию
времени детерминированно, создайте
`RegisterUserProjection(RegisterWireSettings(timestamp_factory=lambda: 1787740000))`.

Device context показывает разделение ролей: `device_id` и тип устройства знает приложение, а не
вызывающий метод, поэтому они объявлены зависимостью `DEVICE`, приходят из
`Identity(dependencies=...)` и попадают в тело через фабрику представления, а не через параметр
каждой ручки. Проекция подходит для констант и чистых фабрик, принадлежащих wire representation.
Custom `BodyCodec` нужен только для нестандартных bytes после проекции. Access token, CSRF,
CAPTCHA и session state добавляйте через auth, dependencies, `Inject` или protections.

## 4. Adaptix: многоуровневый JSON, defaults и время

[`adaptix_nested_wire_body.py`](adaptix_nested_wire_body.py) собирает projection одним generated
Adaptix converter прямо из класса операции. Плоские caller-поля превращаются в `payload.account` и
`payload.profile.name`; приватный `metadata` получает значения по умолчанию и свежий timezone-aware
`datetime` от заменяемой `now_factory`:

```bash
uv run python examples/adaptix_nested_wire_body.py
```

```text
adaptix registered: john as user-42
```

`JsonBody` рекурсивно сериализует dataclass-модели, а `datetime` переводит в ISO 8601. Для
детерминированного теста передайте
`AdaptixWireDefaults(now_factory=lambda: datetime(..., tzinfo=UTC))`. Сам Adaptix остаётся
dev/SDK-authoring зависимостью: пакет `eazy_sdk` от него не зависит.

## 5. HTML как типизированная модель

[Books to Scrape](https://books.toscrape.com/) специально создан для практики scraping.
Вместо ручного обхода DOM пример описывает страницу двумя dataclass-моделями:

```bash
uv run python examples/books_to_scrape.py
```

`CSS(...)` извлекает текст или атрибут. `Scope(...)` повторяет вложенную модель для каждой
карточки товара. Ссылка следующей страницы имеет тип `str | None`, потому что на последней
странице её нет.

Для реального сайта сначала проверьте условия использования, `robots.txt` и ограничения
частоты запросов. Публичный sandbox не задаёт правила для чужих production-сайтов.

## 6. Response cases

Одинаковый endpoint может вернуть полезную модель или разные ошибки. Не разбирайте их через
`if response.status_code` в каждом вызове — объявите cases рядом с ручкой:

```bash
uv run python examples/response_cases.py
```

```text
200: order-42 is paid
404: Unknown order missing
429: rate_limited
```

Eazy SDK валидирует error body и поднимает `OrderNotFound` или `RateLimited`. В обработчике
доступны и модель ошибки, и исходный response context.

## 7. Запрос как значение

Одна ручка, отличающаяся одним числом, — это пагинация. Отдельная абстракция для неё не нужна:
`request()` строит значение, `evolve()` копирует его со следующей страницей, `send()` отправляет.

```bash
uv run python examples/request_values.py
```

```text
collected: keyboard, mouse, monitor, cable, lamp
queued page: lamp
queued page: keyboard
```

Значение — это сам класс операции, поэтому очередь запросов — обычный список, а `evolve`
проверяет имена полей через ту же библиотеку моделей, что и конструктор.

## 8. Авторизация

[DummyJSON Auth](https://dummyjson.com/docs/auth) предоставляет публичную demo-учётку.
Пример сначала вызывает `/auth/login`, держит пароль и tokens в `SecretStr`, затем создаёт
Bearer binding и вызывает `/auth/me`:

```bash
uv run python examples/dummyjson_auth.py
```

Demo credentials уже заданы в файле. Их можно заменить переменными окружения:

```powershell
$env:DUMMYJSON_USERNAME = "emilys"
$env:DUMMYJSON_PASSWORD = "emilyspass"
uv run python examples/dummyjson_auth.py
```

Правило для SDK простое:

- токен уже получен приложением — используйте `BearerScheme.static(token)`;
- SDK сам владеет login/refresh — объявите [`session_auth`](../docs-site/src/content/docs/auth/session.mdx);
- не передавайте реальные secrets аргументами каждой ручки и не печатайте tokens в лог.

## 9. Автоматический login внутри SDK

[`dummyjson_session_auth.py`](dummyjson_session_auth.py) повторяет документированный контракт
DummyJSON через локальный `MockTransport`. Пользователь SDK передаёт credentials в root factory,
но вызывает только нужную бизнес-ручку:

```python
import httpx
from eazy_sdk.handlers.httpx import AsyncHttpxHandler

credentials = LoginCredentials(
    username="emilys",
    password=SecretStr("emilyspass"),
)
handler = AsyncHttpxHandler(httpx.AsyncClient(), owns_client=True)

async with DummyJsonSdk.from_handler(handler=handler, credentials=credentials) as sdk:
    user = await sdk.users.me()
```

`from_handler(...)` принимает любой асинхронный handler Zapros. HTTPX здесь выбран только в
consumer setup; сам SDK не знает, какой транспорт стоит под Zapros.

На первом защищённом вызове runtime сам получает session. Если сервер отвечает `401`, runtime
вызывает `refresh`, подписывает запрос новым Bearer token и повторяет исходную ручку:

```bash
uv run python examples/dummyjson_session_auth.py
```

```text
authenticated: emilys (Emily Johnson)
runtime:
- POST /auth/login
- GET /auth/me Bearer access-1
- POST /auth/refresh
- GET /auth/me Bearer access-2
```

Если приложение загрузило сохранённую session, передайте `session=saved_session` вместо
`credentials=...`. Валидная session сразу используется для бизнес-запроса, поэтому login не
выполняется. `session_auth(...)` сам проверяет, что передан ровно один источник:
`credentials` или `session`.

Root SDK — `class DummyJsonSdk(AsyncRoot)` с группами `auth = api_group(DummyJsonAuthApi)`
и `users = api_group(DummyJsonUsersApi)`. `AsyncRoot` уже даёт конструктор `DummyJsonSdk(client)`,
`from_handler(...)`, `aclose()` и `async with`; автор hand-written SDK лишь переопределяет
`from_handler`, добавляя
параметры `credentials=`/`session=`, собирает auth через
`DUMMYJSON_SESSION.configure(credentials=..., session=..., service=DummyJsonLoginService())`,
кладёт его в `Identity(auth=(auth,))` — сессия принадлежит вызывающей стороне, а не транспорту, — и
вызывает `super().from_handler(...)`. Остальные параметры базовой фабрики (`base_url`, `config`,
`owns_handler`, `profile`, `bindings`) пробрасываются как есть. В generated SDK эквивалентный override создаётся
генератором. Потребитель SDK не создаёт service, transport, `ClientConfig` или auth binding и не
управляет lifecycle вручную.

`DUMMYJSON_SESSION = session_scheme(UserSession, ...)` связывает API defaults и lifecycle auth
одной типизированной scheme; `.configure(...)` принимает credentials/session/service. Scoped SDK для
`AuthContext[DummyJsonSdk]` регистрирует сам конструктор корня, поэтому рукописных
`__init__`, `aclose`, `__aenter__`, `Any`, `AuthScheme[object]` или closure с `security` в SDK нет.

| Кто | За что отвечает |
| --- | --- |
| Потребитель SDK | Передаёт `credentials=` или `session=` и вызывает бизнес-ручку |
| Автор SDK или генератор | Один раз связывает login/refresh operations с auth service |
| Runtime Eazy SDK | Выбирает готовую session или `acquire`, обновляет её после `401` и повторяет запрос |

Runtime не угадывает, какая произвольная ручка является login или refresh. Эту связь должен
объявить автор SDK либо сгенерировать OpenAPI plugin. После объявления решение о моменте вызова
полностью принадлежит runtime.

## 10. Полный локальный SDK

Финальный пример собирает несколько механизмов на одной платёжной ручке:

```bash
uv run python examples/docs/store_sdk.py
```

```text
pay-42 accepted receipt-pay-42
```

Порядок обработки имеет значение: поля карты шифруются до JSON serialization, всё готовое
тело шифруется после неё, а HMAC считается по ciphertext, который уйдёт в transport. Ответ
проходит обратный путь до валидации `PaymentResult`.

Шифры в примере — обратимые Base64-обёртки только для демонстрации интерфейса. Eazy SDK
управляет стадиями и wire metadata; настоящий алгоритм, ключи, nonce и их жизненный цикл
предоставляет приложение. Практическое описание находится в
[`payload-crypto.mdx`](../docs-site/src/content/docs/guides/payload-crypto.mdx).

## Проверка примеров

Автономные regression-тесты не обращаются к публичным сервисам:

```bash
uv run pytest -q tests/unit/test_docs_examples.py
uv run mypy examples
uv run ruff check examples
```
