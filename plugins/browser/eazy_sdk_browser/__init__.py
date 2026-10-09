"""eazy-browser — декларативные браузерные SDK поверх любого драйвера.

Библиотека делает для браузера то же, что `eazy-sdk` делает для HTTP: операция
описывается классом-контрактом, а не россыпью императивных шагов, а драйвер
(playwright, selenium, camoufox, pydoll) приходит адаптером и в ядро не протекает.

    class CompaniesPage:
        create_button: Annotated[Element, css('button[data-test="create"]')]


    @dataclass(frozen=True, slots=True, kw_only=True)
    class OpenCreateCompany(BrowserOperation[CompaniesPage, CreateCompanyDialog]):
        __browser__ = Browser.act(
            CompaniesPage,
            at=css('button[data-test="create"]'),
            outcomes=outcomes(
                when(visible(css('div[role="dialog"]')), to=CreateCompanyDialog),
                otherwise=_dialog_missing,
            ),
        )

        async def act(self, content: CompaniesPage) -> None:
            await content.create_button.click()

    class CompaniesPortal(AsyncBrowserApi):
        errors = PORTAL_ERRORS
        open_create_company = op(OpenCreateCompany)

    portal = CompaniesPortal(AsyncBrowserClient(driver))
    dialog = await portal.open_create_company()

Имена не пересекаются с `eazy_sdk`: локатор — `Locator`, а не `Query`; кука —
`BrowserCookie`, а не `Cookie`. Ошибки, профиль возможностей и его шкала берутся из
ядра, своих у плагина нет.

Почему именно так — `docs/decisions.md`; открытые вопросы — `docs/DESIGN.md`.
Адаптеры лежат в `eazy_sdk_browser.handlers`, необязательные интеграции — в
`eazy_sdk_browser.integrations`; ни те, ни другие не импортируются ядром.
"""

from __future__ import annotations

from eazy_sdk_browser.conditions import Observation, Sign, text, url, visible
from eazy_sdk_browser.content import CurrentDriver, Marker, Region, build_content, region
from eazy_sdk_browser.driver import Driver, Element, LoadState
from eazy_sdk_browser.errors import BrowserDeclarationError, BrowserError
from eazy_sdk_browser.failures import (
    ExceptionFactory,
    Failure,
    FailureRules,
    PageError,
    enforce,
    text_of,
)
from eazy_sdk_browser.interceptors import Action, Handle, Handlers, click, handle
from eazy_sdk_browser.locators import (
    EachLocator,
    ElementNotFoundError,
    Elements,
    Lazy,
    Locator,
    Pick,
    Template,
    TemplateLocator,
    any_of,
    css,
    css_escape,
    each,
    last,
    template,
    template_text,
    text_escape,
)

from eazy_sdk_browser.profile import (  # isort: skip
    BrowserProfile,
    Capability,
    validate_profile,
)

from eazy_sdk_browser.fetch import (  # isort: skip
    FETCH_TIMEOUT,
    FetchAware,
    PageFetchError,
    PageReply,
    PageRequest,
    require_fetch,
)

from eazy_sdk_browser.state import (  # isort: skip
    BrowserCookie,
    BrowserState,
    Origin,
    StateAware,
    require_state,
)

from eazy_sdk_browser.network import response  # isort: skip

from eazy_sdk_browser.login import (  # isort: skip
    BrowserCookieBridge,
    BrowserLogin,
    BrowserLoginContext,
    BrowserLoginService,
    BrowserSessionError,
    CookieAuthAdopter,
    browser_cookie_auth,
)

from eazy_sdk_browser.navigation import NavigationAware, navigated  # isort: skip

from eazy_sdk_browser.rich_text import RichLocator, RichText, RichTextAware, rich  # isort: skip

from eazy_sdk_browser.outcomes import (  # isort: skip
    FromApi,
    Outcomes,
    When,
    outcomes,
    when,
)

from eazy_sdk_browser.spec import Browser  # isort: skip

from eazy_sdk_browser.operations import (  # isort: skip
    BrowserOperation,
    NotReadyError,
)

from eazy_sdk_browser.client import (  # isort: skip
    AsyncBrowserClient,
    BrowserCallOptions,
    BrowserClientConfig,
    SessionSource,
    execute,
)

from eazy_sdk_browser.session import BrowserSession  # isort: skip

from eazy_sdk_browser.api import AsyncBrowserApi, BrowserServiceDefaults  # isort: skip

__version__ = "0.2.0a10"

__all__ = [
    "FETCH_TIMEOUT",
    "Action",
    "AsyncBrowserApi",
    "AsyncBrowserClient",
    "Browser",
    "BrowserCallOptions",
    "BrowserClientConfig",
    "BrowserCookie",
    "BrowserCookieBridge",
    "BrowserDeclarationError",
    "BrowserError",
    "BrowserLogin",
    "BrowserLoginContext",
    "BrowserLoginService",
    "BrowserOperation",
    "BrowserProfile",
    "BrowserServiceDefaults",
    "BrowserSession",
    "BrowserSessionError",
    "BrowserState",
    "Capability",
    "CookieAuthAdopter",
    "CurrentDriver",
    "Driver",
    "EachLocator",
    "Element",
    "ElementNotFoundError",
    "Elements",
    "ExceptionFactory",
    "Failure",
    "FailureRules",
    "FetchAware",
    "FromApi",
    "Handle",
    "Handlers",
    "Lazy",
    "LoadState",
    "Locator",
    "Marker",
    "NavigationAware",
    "NotReadyError",
    "Observation",
    "Origin",
    "Outcomes",
    "PageError",
    "PageFetchError",
    "PageReply",
    "PageRequest",
    "Pick",
    "Region",
    "RichLocator",
    "RichText",
    "RichTextAware",
    "SessionSource",
    "Sign",
    "StateAware",
    "Template",
    "TemplateLocator",
    "When",
    "__version__",
    "any_of",
    "browser_cookie_auth",
    "build_content",
    "click",
    "css",
    "css_escape",
    "each",
    "enforce",
    "execute",
    "handle",
    "last",
    "navigated",
    "outcomes",
    "region",
    "require_fetch",
    "require_state",
    "response",
    "rich",
    "template",
    "template_text",
    "text",
    "text_escape",
    "text_of",
    "url",
    "validate_profile",
    "visible",
    "when",
]
