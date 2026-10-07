# Примеры Eazy SDK

Примеры используют только локальные обработчики и зарезервированные адреса `.example`. Внешняя
сеть для их запуска не нужна.

## Подготовка

Из корня репозитория установите зависимости:

```bash
uv sync --all-packages --all-extras
```

## Сквозной пример почтового SDK

Основной учебный пример обслуживает один объект `MailSite`:

```text
examples/mail/
  site/       общий учебный сайт и адаптеры транспортов
  http/       HTTP-линия SDK, по запускаемому модулю на главу
  browser/    браузерная линия SDK, по запускаемому модулю на главу
  __main__.py единая команда после сборки обеих линий
```

`MailSite` хранит учебные аккаунты, письма и сессии в памяти. Один объект отвечает HTTP-клиенту
через `handle_httpx()` и браузеру через `intercept_page()`. Обе линии видят те же формы входа,
токены, страницы писем и результаты отправки.

Запустите обе линии:

```bash
uv run python -m examples.mail http
uv run python -m examples.mail browser
```

Пошаговый разбор начинается на странице
[«Что строим»](../docs-site/src/content/docs/tutorial/index.mdx).

## Фрагменты документации

Публикуемый фрагмент ограничивается парными комментариями:

```python
# region docs: http-login-router
class LoginApi(SyncApi):
    ...
# endregion docs: http-login-router
```

Имя после `docs:` состоит из линии, темы и роли, разделённых дефисом. Оно уникально в
`examples/mail/`. Метки не вкладываются друг в друга и стоят непосредственно вокруг показываемого
кода. Страница подключает участок через `literalinclude` с `:start-after:` и `:end-before:`. Рядом
она даёт полный запускаемый файл и его проверенный вывод.

## Самостоятельные примеры

| Файл | Что показывает |
|---|---|
| [`quickstart.py`](quickstart.py) | Типизированный GET, параметр пути, Pydantic-ответ |
| [`blog_posts.py`](blog_posts.py) | JSON, query, тело запроса, aliases, `.with_response()` |
| [`flat_model_wire_body.py`](flat_model_wire_body.py) | Плоская операция и вложенное представление JSON |
| [`adaptix_nested_wire_body.py`](adaptix_nested_wire_body.py) | Adaptix, вложенный JSON, значения по умолчанию и время |
| [`catalog_html.py`](catalog_html.py) | HTML, CSS-селекторы, вложенные области и пагинация |
| [`response_cases.py`](response_cases.py) | Успешный вариант и типизированные ошибки 404/429 |
| [`request_values.py`](request_values.py) | `request()`, `evolve()` и `send()` для запроса как значения |
| [`bearer_auth.py`](bearer_auth.py) | Вход, `SecretStr` и статическая Bearer-схема |
| [`session_auth.py`](session_auth.py) | Автоматический вход, обновление сессии и повтор после 401 |

Каждый пример объявляет нейтральный адрес вроде `https://account.example`, а ответы получает от
локального обработчика. Так поведение воспроизводится без зависимости от стороннего сервиса.

## Проверка

```bash
uv run pytest -q tests/unit/test_docs_examples.py
uv run mypy examples
uv run ruff check examples
```

Запускаемые файлы, включённые в страницы документации, дополнительно проверяются тестом
`tests/unit/test_phase56_docs_examples.py`: вывод процесса должен совпасть с блоком
`example-output` на странице.
