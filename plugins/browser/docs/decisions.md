# Как библиотека пришла к своей форме

> **Исторический документ.** Имена здесь — те, что были на момент спайков: `BrowserOp`,
> `OutcomeOp`, `first_of`, `__at__`, `__errors__`, `Failure(raises=...)`, `TextContains`.
> Сегодняшняя форма — `README.md` и `OVERVIEW.md`; как к ней пришли — `PLAN.md` (B0–B6).
> Выводы спайков — механика `Annotated`, операция без драйвера, union исходов из объявления —
> остались в силе.

Документ — протокол трёх спайков, на которых выбиралась форма API. Сам код спайков
удалён после переноса в `eazy_sdk_browser/`; здесь остались решения, замеры типизатора и
отвергнутые варианты, чтобы к ним не возвращаться по кругу.

Команды в тексте (`poe probe`, `poe probe-transitions`) относились к удалённым
пробникам; сегодняшний эквивалент — `uv run poe probe` по `probe/typing_probe.py`.

Одна и та же `Compose` — заполнить форму письма — записана тремя способами. Общее
(`_shared.py`): протокол драйвера, язык локаторов, условие `At`, доменные модели.
Различается **только форма объявления**.

Воспроизвести: `uv run poe check` (все три делают одно и то же) и `uv run poe probe`
(что из этого видит типизатор).

## Что сравнивали

| | A: дескрипторы | B: `Annotated` | C: `__init_subclass__` |
|---|---|---|---|
| Тип элемента виден | `Element` | `Element` | `Element` |
| **Опечатка в имени элемента** | **ошибка** | **ошибка** | **проходит молча** |
| Тип результата виден | `Composed` | `Composed` | `Composed` |
| Входы операции видны | `OutgoingMessage` | `OutgoingMessage` | `OutgoingMessage` |
| Операция неизменяема | нет | **да** | нет |
| **Одну операцию можно гонять в двух вкладках сразу** | **нет** | **да** | **нет** |
| Карта рядом с операцией | **да** | нет, отдельным классом | **да** |
| `Any`/`cast` в библиотеке | нет | 3 (заперты в базе) | нет |
| Объявление проверяется | при доступе | при сборке карты | **при импорте модуля** |

Вывод пробника (`uv run poe probe`), дословно:

```
typing_probe.py:22 - information: Type of "operation.subject" is "Element"
typing_probe.py:24 - error: Cannot access attribute "subjekt" for class "Compose"      ← A ловит
typing_probe.py:29 - information: Type of "content.subject" is "Element"
typing_probe.py:30 - error: Cannot access attribute "subjekt" for class "ComposeContent" ← B ловит
typing_probe.py:36 - information: Type of "operation.subject" is "Element"
typing_probe.py:37 - information: Type of "operation.subjekt" is "Element"             ← C пропускает
2 errors, 0 warnings, 9 notes
```

## Что выяснилось по дороге

**A требует изменяемой операции.** Дескриптор знает только `instance`, значит драйвер
кладётся в экземпляр — `frozen=True` невозможен. Альтернатива, `ContextVar`, прячет
привязку от сигнатур и от тестов.

**B не даёт объявить карту вложенным классом.** Естественная форма
`class Compose(BrowserOp["Compose.Content", Composed])` отвергается: pyright отвечает
`Class definition for "Compose" depends on itself`. Попытка обойти это базой с
`content: object` ломает LSP — `reportIncompatibleMethodOverride`, потому что
переопределение сужает параметр. Поэтому карта объявлена рядом, отдельным классом.

**A и C нельзя переиспользовать параллельно.** Обе кладут драйвер в экземпляр
операции, поэтому два одновременных `execute` перетирают его друг другу: тест
`test_same_operation_in_two_tabs_at_once` фиксирует это на фейковом драйвере,
который уступает управление, как настоящий. Для «одного аккаунта на много вкладок»
это значит, что объект операции придётся создавать на каждую вкладку заново.
B этим не страдает: карта приходит аргументом, состояния в операции нет.

**C проверяет объявление раньше всех** — `__init_subclass__` срабатывает при импорте
модуля, то есть кривой `__content__` роняет процесс на старте, а не на первом запуске.
Но за краткость платится тем, что `__getattr__` делает **любое** имя валидным `Element`:
опечатка доходит до рантайма, где `Lazy` ищет несуществующий селектор.

## Ограничение первого спайка

Проверялась форма объявления, а не полный контракт операции. Переходы вынесены во
второй спайк — `spikes/transitions/`.

---

# Спайк 2: переходы (на выбранной механике B)

Два вида перехода из живого сценария webmail: **исход действия** (клик «Отправить»
кончается подтверждением, отказом по адресу или тишиной) и **смена состояния** (вход
уводит в почту, на 2FA или обратно на форму).

Сравниваются два способа описать исход: `t1_union.py` — операция распознаёт его сама,
`t2_declarative.py` — операция объявляет таблицу «признак → исход», распознаёт раннер.
Карты элементов у обоих общие (`_forms.py`), чтобы различие было только в форме.

| | T1: распознаёт операция | T2: объявленная таблица |
|---|---|---|
| Тип исхода виден | union трёх | тот же, но **выведен из декларации** |
| Забытая ветка у вызывающего | ловится (`assert_never`) | ловится (`assert_never`) |
| **Декларация разошлась с типом операции** | не про что | **ошибка типизатора** |
| Ожидание нескольких признаков | автор пишет сам | раннер, опрос по кругу |
| `if` в операции | два | ни одного |
| Строк на операцию | 12 | 14 |

Вывод пробника (`uv run poe probe-transitions`), дословно:

```
:31 - information: Type of "t2_declarative.Submit().execute" is
      "(driver: Driver) -> CoroutineType[Any, Any, Sent | Rejected | Unconfirmed]"
:32 - information: Type of "t2_declarative.Login(...).execute" is
      "(driver: Driver) -> CoroutineType[Any, Any, Mailbox | TwoFactorRequired | BadCredentials]"
:43 - error: Argument of type "Unconfirmed" cannot be assigned to parameter "arg"
      of type "Never" in function "assert_never"           ← забытая ветка ловится
:53 - error: Cannot access attribute "compose" for class "TwoFactorRequired"
                                                            ← переход защищён типом
:61 - error: Cannot access attribute "compose" for class "TwoFactorRequired"
:61 - error: Cannot access attribute "compose" for class "BadCredentials"
                                                            ← до разбора исхода шага нет
:77 - error: Type "OutcomeSet[SubmitContent, Sent | Rejected | Unconfirmed]" is not
      assignable to return type "OutcomeSet[SubmitContent, Sent | Rejected]"
                                                            ← декларация и тип не разойдутся
```

## Что выяснилось

**Union исходов выводится из декларации.** `first_of(...)` перегружен по числу случаев,
поэтому типизатор собирает `Sent | Rejected | Unconfirmed` сам и сверяет с типом
операции. Объявить исход и забыть его в типе нельзя — это ошибка, а не расхождение,
которое обнаружится в проде.

**Типизированный переход получается без новых понятий.** Успех входа — не `True`, а
состояние `Mailbox`, у которого есть `compose`. У `TwoFactorRequired` такого метода нет,
и типизатор это видит. Пока исход не разобран через `match`, следующий шаг недоступен
вообще — ровно то, что Geb делает через `to: ResultsPage`.

**Неопределённый исход занял своё место в типах.** `Unconfirmed` — третий член union,
а не исключение и не `None`. Вызывающий обязан его разобрать, иначе `assert_never`
даёт ошибку.

**T1 честно выглядит проще, но прячет ловушку ожидания.** В нём `visible()` каждого
признака ждёт свой таймаут по очереди: первый кандидат съедает всё отведённое время,
и второй исход становится практически недостижим. В T2 опрос идёт по кругу с общим
дедлайном — это встроено в `OutcomeSet.settle`, а не оставлено на внимательность автора.
Ловушка пережила спайк в другом месте: сами признаки внутри круга ждали элемент через
`driver.find` с полным таймаутом. Закрыто в B0.1 (`docs/PLAN.md`): признак смотрит на
снимок страницы `Observation` через `peek` и не ждёт, ждёт только цикл.

**Признак исхода не обязан быть элементом.** Вход опознаётся по редиректу, поэтому
`UrlContains` — такой же маркер карты, как `css(...)`. Сборщик карты о видах признаков
не знает: он вызывает `marker.bind(driver)`. Это же место даёт `CurrentDriver()` —
маркер, кладущий в карту сам драйвер, чтобы состояние-исход унесло доступ к странице
с собой, не заводя состояния в самой операции.

## Ограничение второго спайка

`Mailbox.compose` знает про конкретную операцию `Compose` — состояние связано с тем,
что из него можно сделать. Для спайка это нормально, но в библиотеке набор разрешённых
операций должен объявляться, а не прописываться методами руками.

Не проверялись: контракт отказов (что считать сбоем инструмента, а что — ответом
страницы) и улики при отказе — `docs/DESIGN.md` §6, вопросы 5 и 7.

---

# Спайк 3: контракт отказов, вложенные карты и сетевые признаки

Пример целиком — `spikes/failures/company.py`: портал компаний, где форма создания
открывается по кнопке, а отказ приходит ответом API.

## Отказы объявляет создатель SDK

Один раз, как `SERVICE_ERRORS` в `eazy-sdk`:

```python
PORTAL_ERRORS = (
    # Частные правила выше общих: «нет прав» не должно перехватываться «сессия истекла».
    Failure(
        when=TextContains("Недостаточно прав"),
        raises=AccessDeniedError,
        detail=text_of(css(ERROR_BANNER)),
    ),
    Failure(
        when=UrlContains("/login") | TextContains("Войдите заново"), raises=SessionExpiredError
    ),
)


class PortalOp[TContent, TResult](OutcomeOp[TContent, TResult]):
    """База операций портала: правила отказа объявлены один раз для всех."""

    __errors__ = PORTAL_ERRORS
```

Операция добавляет своё правило к общим: `__errors__ = (COMPANY_EXISTS, *PORTAL_ERRORS)`.

Правила проверяются **первыми в том же цикле опроса**, что и исходы. Иначе страница,
показавшая «неверный пароль», сначала выждала бы общий таймаут и вернулась бы
«ничем не подтверждено»: отказ выглядел бы неопределённостью.

Признаки исходов и признаки отказов — один язык (`spikes/_conditions.py`),
комбинируемый операторами, как `body.contains(...) | body.contains(...)` в `eazy-sdk`.

## Где живёт форма, которой ещё нет на странице

Два разных случая, и путать их нельзя:

| | Что это | Как объявляется |
|---|---|---|
| Панель фильтров, строка таблицы | **есть в DOM всегда** | подкарта: `region(FilterPanel, root=css("form.filters"))` — ищет свои элементы внутри корня |
| Диалог создания компании | **появляется по клику** | отдельная карта + отдельное состояние; попасть можно только операцией открытия |

То есть форма создания компании **не поле карты списка**. В карте списка — только
кнопка. Связь даёт переход: `OpenCreateCompany` возвращает `CreateCompanyDialog`, и
только у него есть `submit`. Типизатор это подтверждает: `page.name` —
`Cannot access attribute "name" for class "CompaniesPage"`.

Так получается потому, что у диалога **своё условие готовности**. Будь форма полем
карты списка, её локаторы существовали бы и до клика — и первая же опечатка в
селекторе всплыла бы не при открытии диалога, а в момент использования.

## Сетевой отказ — не признак, а объявление ответов

Ответ браузера — обычный HTTP-ответ, поэтому разбирает его то же объявление, что и
в HTTP-SDK. Своего языка не заводим: `Responses` из `eazy-sdk` подключён адаптером.

```python
COMPANY_CASES = Responses(
    success=(Success(201, Json(CreateReply)),),
    errors=(
        Error(409, Json(ApiProblem), exception=_company_exists),
        Error(403, Json(ApiProblem), exception=_access_denied),
        Error(StatusRange(500, 599), Json(ApiProblem), exception=_portal_broken),
    ),
)


class CreateCompanyForm:
    name: Annotated[Element, css('input[name="name"]')]
    reply: Annotated[ApiValue[CreateReply], ApiResponse(COMPANIES_API, sdk_cases(COMPANY_CASES))]
```

Отсюда бесплатно приходят: выбор случая по статусу и медиа-типу, разбор модели,
условия по телу (`condition=`), объявленные исключения, `UnexpectedResponseError`
для ответа, не подошедшего ни под один случай (портал вернул `302` на страницу входа),
и `MalformedResponseError` для подошедшего, но не разобравшегося. Всё это проверено
в `tests/test_failures.py`, включая `StatusRange(500, 599)`.

Исключения остаются **своими**: `eazy-sdk` принимает не только класс `ApiError`, но и
фабрику `(модель, контекст) -> Exception`, поэтому `CompanyExistsError` наследуется от
`PageError` портала, а не от чужой иерархии.

Разделение вышло чистым:

| Природа отказа | Чем объявляется | Где |
|---|---|---|
| Страница показала баннер, увела на вход | `Failure(when=<признак>, raises=...)` | `__errors__` базового класса операций |
| API ответил `409`, `403`, `5xx` | `Responses(success=..., errors=...)` | объявление ответа в карте |

Ручной проверки `if reply.error == "company_exists"` не осталось нигде: исход читает
модель успеха, а отказ поднимается сам. В типе успеха нет ни `| None`, ни поля `error` —
`reply.id` это `str`.

**Адаптер** — `spikes/failures/_eazy_sdk.py`, полтора десятка строк: `ResponseView`
браузера превращается в `NormalizedResponse`, дальше `responses.inspect(context).unwrap()`.
Ядро знает только протокол `Decoder` (`ResponseView -> модель`) и зависимости на
`eazy-sdk` не имеет — она ставится экстрой `eazy-browser[eazy-sdk]`.

Вывод пробника (`uv run poe probe-failures`), дословно:

```
:22 - information: Type of "dialog" is "CreateCompanyDialog"
:23 - information: Type of "await dialog.submit(...)" is "Created"
:29 - information: Type of "page.filters.query" is "Element"     ← подкарта типизирована
:30 - error: Cannot access attribute "name" for class "CompaniesPage"
                                                                  ← формы в карте списка нет
:40 - information: Type of "reply" is "CreateReply"
:41 - information: Type of "reply.id" is "str"          ← отказы сюда не доходят
:42 - error: Cannot access attribute "error" for class "CreateReply"
2 errors, 0 warnings, 8 notes
```

Отдельно стоит отметить `NoReturn`: «диалог не открылся» — это отказ, а не исход,
поэтому `otherwise=_dialog_missing` объявлен как `-> NoReturn` и **не добавляет члена
в union**. Тип операции остаётся `CreateCompanyDialog`, без `| None`.

## Ограничения третьего спайка

- Подкарта скоупится склейкой CSS (`form.filters input[name="q"]`). Этого хватает для
  обычной вёрстки, но не для shadow DOM и iframe — там нужен настоящий scoping в
  протоколе драйвера (`element.find_element` / `locator.locator`).
- `TextContains` читает текст страницы на каждом круге опроса. В браузере это
  недёшево; в библиотеке понадобится кэш на круг или отдельный признак по селектору.
- Скриншоты и HTML при отказе сознательно не делались: они нужны включаемыми, и это
  отдельная задача.
- `SuccessOutcome` не экспортирован из публичного `eazy_sdk.response`, поэтому адаптер
  импортирует его из `eazy_sdk.response.cases`. Без него успешный исход не отличить от
  отказа, не полагаясь на `unwrap() -> None`; стоит вынести наверх.
