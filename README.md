# Eazy SDK

[![CI](https://github.com/0cherednoq/eazy-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/0cherednoq/eazy-sdk/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue.svg)](https://0cherednoq.github.io/eazy-sdk/)
[![PyPI](https://img.shields.io/pypi/v/eazy-sdk-core.svg?include_prereleases&cacheSeconds=300)](https://pypi.org/project/eazy-sdk-core/)
[![Python](https://img.shields.io/pypi/pyversions/eazy-sdk-core.svg?cacheSeconds=300)](https://pypi.org/project/eazy-sdk-core/)

Библиотека для Python, на которой пишут типизированные SDK к чужим HTTP API, WebSocket и сайтам.
Запрос объявляется классом, ответ разбирается в модель, а вход, обновление сессии, подпись,
повторы и пагинация описываются рядом с операцией и не попадают в код вызова.

> Статус: alpha, версия `0.2.0a7`. Альфа-версии переименовывают публичные имена без устаревших
> псевдонимов, список замен ведёт страница
> [«Миграция»](https://0cherednoq.github.io/eazy-sdk/reference/migration/). Документация собирается в
> сайт из [docs-site/](https://github.com/0cherednoq/eazy-sdk/tree/master/docs-site) и публикуется на
> [GitHub Pages](https://0cherednoq.github.io/eazy-sdk/).

## Что это даёт

* Операция описана один раз: поля класса задают путь, query, заголовки и тело, а тип результата
  виден редактору и тайпчекеру.
* Один и тот же роутер работает через HTTPX, Requests, curl_cffi или свой обработчик. Ядро не
  ставит ни HTTP-клиент, ни библиотеку моделей.
* Ответы разбираются в Pydantic, msgspec, dataclass или Adaptix. Ошибка API внутри ответа с
  кодом 200 становится типизированным исключением.
* Вход, обновление сессии по сроку и по отказу, подпись HMAC, шифрование тела, капча и
  пагинация объявляются декларативно и срабатывают на каждой попытке.
* Тот же SDK может работать через браузер: операции страницы на Playwright или Pydoll и сессия
  браузера у HTTP-клиента.
* Генераторы собирают SDK из OpenAPI 3.0-3.2 и AsyncAPI 3.0.

HTTP-клиент, браузер и хранилище аккаунтов остаются за приложением: SDK получает их готовыми.

## Установка

Нужен Python 3.13 или новее.

```bash
pip install --pre "eazy-sdk-core[httpx,pydantic]"
```

На PyPI ядро называется `eazy-sdk-core`, а импортируется как `eazy_sdk`. Проект `eazysdk` на PyPI
к этой библиотеке отношения не имеет. Флаг `--pre` нужен, пока выходят только альфа-версии. Те же файлы приложены к каждому
[релизу GitHub](https://github.com/0cherednoq/eazy-sdk/releases).

Дополнения выбираются по задаче: `httpx`, `requests`, `curl-cffi`, `websocket`, `pydantic`,
`msgspec`, `html`, `accounts`, `sqlmodel`, `browser`. Генераторы и готовые описания защит
поставляются отдельными пакетами: `eazy-sdk-openapi`, `eazy-sdk-asyncapi`, `eazy-sdk-presets`.
Полная таблица есть на странице
[«Установка»](https://0cherednoq.github.io/eazy-sdk/start/installation/).

## Быстрый старт

Скрипт ниже получает одно письмо с учебного сайта `mail.example`. Сайт отвечает в том же
процессе, поэтому для знакомства хватит одного файла и сети не нужно.

```python
# quickstart.py
from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from eazy_sdk import Client, Http, HttpOperation, Path, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler


class Message(BaseModel):
    id: int
    sender: str
    subject: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/{message_id}")  # метод и путь

    message_id: Path[int]  # поле операции уходит в путь


class MailApi(SyncApi):
    message = op(GetMessage)  # операция становится методом роутера


def mail_site(request: httpx.Request) -> httpx.Response:
    # учебный сайт в этом же процессе; в приложении запрос уходит в сеть
    return httpx.Response(
        200,
        json={"id": 42, "sender": "ada@mail.example", "subject": "Планы на пятницу"},
    )


raw = httpx.Client(transport=httpx.MockTransport(mail_site))

with Client(
    base_url="https://mail.example",
    handler=HttpxHandler(raw, owns_client=True),
) as client:
    message = MailApi(client).message(message_id=42)
    response = MailApi(client).message.with_response(message_id=42)

print(f"{message.sender}: {message.subject}")
print(response.status_code)

# ada@mail.example: Планы на пятницу
# 200
```

Что здесь произошло:

1. `GetMessage` описал запрос как значение. `__http__` задал метод и путь, поле `message_id`
   подставилось в путь, а `HttpOperation[Message]` назвал тип успешного ответа.
2. `op(GetMessage)` опубликовал операцию на роутере. Сигнатура метода `message` совпадает с
   конструктором класса, поэтому редактор подсказывает `message_id`.
3. Клиент доставил байты и разобрал JSON в `Message`. Роутер о транспорте не знает: в
   приложении вместо двух строк с `raw` достаточно `Client.httpx(base_url=...)`.
4. `.with_response()` вернул тот же результат вместе со статусом и заголовками.

## Дальше

| Задача | Страница |
|---|---|
| Разобраться в ролях: операция, роутер, клиент, корень, `Identity` | [Основные понятия](https://0cherednoq.github.io/eazy-sdk/start/concepts/) |
| Пройти один SDK почты от входа до отправки письма, по HTTP и через браузер | [Разбор на примере](https://0cherednoq.github.io/eazy-sdk/tutorial/) |
| Объявить параметры, заголовки и тело запроса | [Запросы](https://0cherednoq.github.io/eazy-sdk/guide/requests/) |
| Разобрать успешный ответ и ошибку API | [Ответы](https://0cherednoq.github.io/eazy-sdk/guide/responses/) |
| Добавить ключ, токен, cookie, вход и обновление сессии | [Авторизация](https://0cherednoq.github.io/eazy-sdk/guide/auth/) |
| Обойти список страницами, подписать и зашифровать запрос | [Пагинация](https://0cherednoq.github.io/eazy-sdk/guide/pagination/), [подпись](https://0cherednoq.github.io/eazy-sdk/guide/signing/), [шифрование](https://0cherednoq.github.io/eazy-sdk/guide/encryption/) |
| Выбрать HTTP-клиент, библиотеку моделей, браузер или генератор | [Интеграции](https://0cherednoq.github.io/eazy-sdk/integrations/) |
| Проверить SDK без сети | [Тестирование SDK](https://0cherednoq.github.io/eazy-sdk/guide/testing/) |
| Понять, как устроен путь запроса внутри | [Архитектура](https://0cherednoq.github.io/eazy-sdk/architecture/) |
| Найти точную сигнатуру | [Справочник API](https://0cherednoq.github.io/eazy-sdk/reference/api/) |

Запускаемые примеры ко всем страницам лежат в [examples/](https://github.com/0cherednoq/eazy-sdk/blob/master/examples/README.md).

## Для ИИ-ассистентов

Сайт документации отдаёт два текстовых файла, собранных из тех же страниц:

* [llms.txt](https://0cherednoq.github.io/eazy-sdk/llms.txt) содержит краткое описание проекта
  и ссылки на страницы. Он подходит помощнику, который умеет открывать ссылки: в контекст
  попадают только нужные страницы.
* [llms-full.txt](https://0cherednoq.github.io/eazy-sdk/llms-full.txt) содержит полный текст
  документации одним файлом. Он подходит для локального поиска и для инструмента, который по
  ссылкам не ходит.

Оба файла обновляются при каждой публикации сайта, отдельную копию документации поддерживать не
нужно.

## Разработка

Нужен [uv](https://docs.astral.sh/uv/). Браузерным тестам нужен Chromium.

```bash
uv sync --all-packages --all-extras            # окружение и все инструменты
uv run playwright install chromium             # браузер для тестов плагина
uv run pytest -q                               # тесты
uv run mypy                                    # типы
uv run ruff check                              # линтер
uv run python scripts/docs_freshness.py check  # страницы не отстали от кода
```

Порядок работы описан в [CONTRIBUTING.md](https://github.com/0cherednoq/eazy-sdk/blob/master/CONTRIBUTING.md), об уязвимостях сообщают по
[SECURITY.md](https://github.com/0cherednoq/eazy-sdk/blob/master/SECURITY.md). План архитектуры лежит в
[docs/implementation/](https://github.com/0cherednoq/eazy-sdk/blob/master/docs/implementation/README.md).

## Лицензия

[MIT](https://github.com/0cherednoq/eazy-sdk/blob/master/LICENSE)
