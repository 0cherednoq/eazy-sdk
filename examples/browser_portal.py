"""SDK портала компаний — сквозной пример всех решений библиотеки.

Показывает то, ради чего библиотека и делалась:

* **отказы объявлены, а не проверяются вручную** — и в двух разных местах, потому что
  у отказов две природы: страничные (`Failure`) и сетевые (`Responses` из `eazy-sdk`);
* **форма, которой ещё нет в DOM, — не поле карты страницы**, а отдельное состояние:
  в карте списка лежит только кнопка, а `submit` есть лишь у `CreateCompanyDialog`;
* **панель фильтров, которая в DOM есть всегда, — подкарта** (`region`), и её элементы
  ищутся внутри своего корня;
* **исход действия читается из ответа API**, разобранного объявлением по статусу;
* **тот же контракт ответа годится и для прямого запроса** — `CreateCompanyRequest`
  выполняется браузером через `BrowserHandler`, а случаи ответа берёт те же самые;
* **HTTP-роутер и браузерный роутер живут в одном `AsyncRoot`** — `PortalSdk` собирает
  `CompaniesApi` и `CompaniesPortal`, и вызывающий не видит, чем выполнена операция;
* **переход — тоже операция роутера** — `OpenCompanies` открывает адрес от `base_url`
  клиента и ждёт готовности, а увод на вход распознаёт то же правило портала.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, NoReturn

from eazy_sdk_browser import (
    AsyncBrowserApi,
    Browser,
    BrowserOperation,
    Capability,
    Element,
    Failure,
    Observation,
    PageError,
    css,
    outcomes,
    region,
    response,
    text,
    text_of,
    url,
    visible,
    when,
)
from eazy_sdk_browser.integrations.eazy_sdk import sdk_cases
from eazy_sdk_browser.network import ApiResponse, ApiValue
from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, JsonField, api_group, op
from eazy_sdk.response import Error, Json, Responses, StatusRange, Success

if TYPE_CHECKING:
    from eazy_sdk.response import ResponseContext

CREATE_BUTTON = 'button[data-test="create-company"]'
DIALOG_ROOT = 'div[role="dialog"]'
ERROR_BANNER = 'div[data-test="error"]'
FILTERS_ROOT = "form.filters"
COMPANIES_API = "/api/companies"


# --- отказы портала --------------------------------------------------------------------


class PortalError(PageError):
    """Портал отказал в операции."""


class SessionExpiredError(PortalError):
    """Сессия истекла: портал увёл на форму входа."""


class AccessDeniedError(PortalError):
    """У пользователя нет прав на это действие."""


class CompanyExistsError(PortalError):
    """Компания с таким ИНН уже заведена."""


class PortalBrokenError(PortalError):
    """Портал ответил ошибкой на своей стороне."""


class DialogNotOpenedError(PortalError):
    """Кнопка нажата, но форма не появилась: портал изменился или подвис."""


class PortalSilentError(PortalError):
    """Форма отправлена, но портал не ответил ни успехом, ни отказом."""


# --- сетевой контракт: статус, модель, исключение ---------------------------------------


@dataclass(frozen=True, slots=True)
class CreateReply:
    """Тело успешного ответа на создание компании."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ApiProblem:
    """Тело отказа: портал отвечает им на любой неуспешный статус."""

    code: str
    message: str


def _company_exists(problem: ApiProblem, context: ResponseContext[object]) -> Exception:
    """Исключения портала остаются своими: `eazy-sdk` принимает фабрику, а не только класс."""
    _ = context
    return CompanyExistsError(problem.message)


def _access_denied(problem: ApiProblem, context: ResponseContext[object]) -> Exception:
    _ = context
    return AccessDeniedError(problem.message)


def _portal_broken(problem: ApiProblem, context: ResponseContext[object]) -> Exception:
    _ = context
    return PortalBrokenError(problem.message)


COMPANY_SUCCESS = (Success(201, Json(CreateReply)),)
COMPANY_ERRORS = (
    Error(409, Json(ApiProblem), exception=_company_exists),
    Error(403, Json(ApiProblem), exception=_access_denied),
    Error(StatusRange(500, 599), Json(ApiProblem), exception=_portal_broken),
)
"""Что портал отвечает на создание компании. Отдельными кортежами, а не сразу
`Responses`, потому что одно и то же объявление нужно двум транспортам: перехвату
ответа в браузере и прямому запросу через `BrowserHandler`."""

COMPANY_CASES = Responses(success=COMPANY_SUCCESS, errors=COMPANY_ERRORS)
"""Объявлено один раз: 201 — модель успеха, остальные статусы — свои исключения.

Ответ, не подошедший ни под один случай (портал вернул 302 на страницу входа), поднимет
`UnexpectedResponseError`; подошедший, но не разобравшийся — `MalformedResponseError`.
Оба лучше, чем `KeyError` в середине сценария.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateCompanyRequest(HttpOperation[CreateReply]):
    """Та же операция портала, но запросом, а не через интерфейс.

    Случаи ответа — **те же кортежи**, что разбирают перехваченный ответ. Смысл всей
    затеи: контракт сервиса объявлен один раз, а чем его выполнить — браузером через
    `BrowserHandler` или обычным HTTP-обработчиком — решается при сборке клиента.
    """

    __http__ = Http.post(COMPANIES_API, success=COMPANY_SUCCESS, errors=COMPANY_ERRORS)

    name: JsonField[str]
    inn: JsonField[str]


class CompaniesApi(AsyncApi):
    """Ручки портала для прямых запросов."""

    create = op(CreateCompanyRequest)


# --- страничные отказы: объявлены один раз на портал ------------------------------------


async def _no_rights(page: Observation) -> Exception:
    """Подробности отказа берутся из баннера: класс исключения о причине не знает."""
    return AccessDeniedError(await text_of(css(ERROR_BANNER))(page))


PORTAL_ERRORS = (
    # Частные правила выше общих: «нет прав» не должно перехватываться «сессия истекла».
    Failure(when=text.contains("Недостаточно прав"), exception=_no_rights),
    Failure(
        when=url.contains("/login") | text.contains("Войдите заново"),
        exception=SessionExpiredError,
    ),
)
"""Страничные правила портала. Объявлены один раз — на роутере (`CompaniesPortal.errors`),
и каждая его операция получает их после своих собственных."""


# --- карты -----------------------------------------------------------------------------


class FilterPanel:
    """Панель фильтров. Всегда в DOM, поэтому подкарта, а не состояние."""

    query: Annotated[Element, css('input[name="q"]')]
    apply: Annotated[Element, css('button[type="submit"]')]


class CompaniesPage:
    """Список компаний. Формы создания здесь нет — её ещё нет на странице.

    Драйвера в карте нет намеренно: карта описывает вёрстку, а состояние-исход
    раннер строит сам — см. `to=` в `OpenCreateCompany.outcomes`.
    """

    create_button: Annotated[Element, css(CREATE_BUTTON)]
    filters: Annotated[FilterPanel, region(FilterPanel, root=css(FILTERS_ROOT))]


class CreateCompanyForm:
    """Карта диалога. Отдельный класс: у диалога своя готовность и своя жизнь.

    Селекторы намеренно без корня: область задаёт операция (`scope=`), поэтому та
    же карта подойдёт и диалогу, и выехавшей панели, если портал их поменяет местами.
    """

    name: Annotated[Element, css('input[name="name"]')]
    inn: Annotated[Element, css('input[name="inn"]')]
    submit: Annotated[Element, css('button[type="submit"]')]
    # Ответ портала — такая же часть карты, как поля формы, и разобран он объявлением:
    # обращение к нему либо даёт модель, либо поднимает объявленный отказ.
    reply: Annotated[ApiValue[CreateReply], ApiResponse(COMPANIES_API, sdk_cases(COMPANY_CASES))]


# --- состояния -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Created:
    """Компания заведена; портал вернул её идентификатор."""

    company_id: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateCompanyDialog:
    """Диалог открыт. Только отсюда форму можно заполнить и отправить.

    Состояние держит роутер, который его открыл: допустимые операции состояния — это
    операции этого роутера, и вызываются они через него, а не через драйвер.
    """

    api: CompaniesPortal

    async def submit(self, name: str, inn: str) -> Created:
        """Заполнить форму и отправить. Отказы портала поднимутся исключением."""
        return await self.api.submit_company(name=name, inn=inn)


# --- операции ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenCompanies(BrowserOperation[None, None]):
    """Открыть список компаний: переход по адресу, готовность — кнопка создания.

    Карты нет: переходу она не нужна. Увод на страницу входа распознаёт правило портала
    `url.contains("/login")` — это истёкшая сессия, а не «список не готов».
    """

    __browser__ = Browser.goto("/companies", at=css(CREATE_BUTTON))


async def _dialog_missing(content: CompaniesPage) -> NoReturn:
    """Не дождались формы — это отказ, а не исход.

    Возвращаемый `NoReturn` не добавляет члена в union исходов: типизатор видит, что
    выхода отсюда нет.
    """
    _ = content
    msg = "форма создания компании не открылась"
    raise DialogNotOpenedError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenCreateCompany(BrowserOperation[CompaniesPage, CreateCompanyDialog]):
    """Открыть форму создания компании: диалог появился — состояние; нет — отказ."""

    __browser__ = Browser.act(
        CompaniesPage,
        at=css(CREATE_BUTTON),
        outcomes=outcomes(
            when(visible(css(DIALOG_ROOT)), to=CreateCompanyDialog),
            otherwise=_dialog_missing,
        ),
    )

    async def act(self, content: CompaniesPage) -> None:
        """Нажать «Создать компанию» — дальше форму рисует JS."""
        await content.create_button.click()


async def _created(content: CreateCompanyForm) -> Created:
    """Разбор ответа объявлен; здесь остаётся только взять поля модели."""
    reply = await content.reply.value()
    return Created(company_id=reply.id, name=reply.name)


async def _no_answer(content: CreateCompanyForm) -> NoReturn:
    _ = content
    msg = "портал не ответил на создание компании"
    raise PortalSilentError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCompany(BrowserOperation[CreateCompanyForm, Created]):
    """Отправить форму. Исход виден в ответе API, а не в вёрстке."""

    __browser__ = Browser.act(
        CreateCompanyForm,
        at=css(DIALOG_ROOT),
        # Без `scope` `button[type="submit"]` нашёл бы кнопку формы фильтров: на
        # странице она стоит раньше диалога.
        scope=css(DIALOG_ROOT),
        # Единственный исход — ответ пришёл; что он означает, решает объявление.
        outcomes=outcomes(
            when(response.arrived(COMPANIES_API), then=_created),
            otherwise=_no_answer,
        ),
        # Карта читает ответ API — без сети операция бессмысленна, и драйвер без неё
        # отсекается до первого действия.
        requires=(Capability.network,),
    )

    name: str
    inn: str

    async def act(self, content: CreateCompanyForm) -> None:
        """Заполнить поля и отправить форму."""
        await content.name.fill(self.name)
        await content.inn.fill(self.inn)
        await content.submit.click()


# --- роутеры и корень -------------------------------------------------------------------


class CompaniesPortal(AsyncBrowserApi):
    """Портал через интерфейс: страничные правила объявлены здесь один раз."""

    errors = PORTAL_ERRORS

    open_companies = op(OpenCompanies)
    open_create_company = op(OpenCreateCompany)
    submit_company = op(SubmitCompany)


class PortalSdk(AsyncRoot):
    """Один SDK — два транспорта.

    `api` идёт запросами (через `BrowserHandler` — куками страницы), `portal` — кликами.
    Браузерный роутер получает свой клиент через `bind(CompaniesPortal, client=...)`.
    """

    api = api_group(CompaniesApi)
    portal = api_group(CompaniesPortal)
