"""Единый стиль с `eazy_sdk`: ошибки, профиль возможностей, имена, язык признаков.

Плагин — часть той же библиотеки, а не соседняя: его ошибки ловятся корнем ядра, его
профиль — та же форма и шкала, что у HTTP-обработчиков, а имена не пересекаются с
публичными именами `eazy_sdk`, чтобы SDK, импортирующий оба пакета, не держал псевдонимов.
"""

import re

import eazy_sdk_browser
import pytest
from eazy_sdk_browser import (
    BrowserDeclarationError,
    BrowserError,
    BrowserProfile,
    Capability,
    ElementNotFoundError,
    Locator,
    NotReadyError,
    Observation,
    PageError,
    PageFetchError,
    css,
    response,
    text,
    url,
    validate_profile,
    visible,
)
from eazy_sdk_browser.network import ResponseMissingError
from eazy_sdk_browser.testing import FakeDriver

import eazy_sdk
from eazy_sdk.cookies import StoredCookie
from eazy_sdk.core.errors import EazySdkError, PlanError
from eazy_sdk.handlers import CapabilityLevel, CapabilityMismatchError

pytestmark = pytest.mark.unit


# --- B3.2: имена ------------------------------------------------------------------------


def test_public_names_do_not_collide_with_the_core() -> None:
    """`Query`, `Cookie`, `TransportError` и прочие имена ядра плагин не переопределяет."""
    assert set(eazy_sdk.__all__) & set(eazy_sdk_browser.__all__) == set()


def test_renamed_types_carry_the_browser_in_their_name() -> None:
    assert Locator(("button",)).selectors == ("button",)
    assert StoredCookie(name="sid", value="x", domain="mail.example").is_session()


# --- B3.1: иерархия ошибок ---------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ElementNotFoundError(css("button")),
        NotReadyError(css("button")),
        ResponseMissingError("/api"),
        PageFetchError("https://x/", "CORS"),
        PageError("отказ"),
    ],
)
def test_runtime_failures_descend_from_the_core_root(error: BrowserError) -> None:
    assert isinstance(error, BrowserError)
    assert isinstance(error, EazySdkError)


def test_declaration_failures_are_plan_errors() -> None:
    assert issubclass(BrowserDeclarationError, PlanError)
    assert not issubclass(BrowserDeclarationError, BrowserError)


def test_locator_errors_name_every_candidate_and_frame() -> None:
    locator = Locator(("a", "b"), frames=("iframe.mail",))

    assert str(ElementNotFoundError(locator)) == "iframe.mail >>> a | b"


# --- B3.3: профиль в форме HandlerProfile -------------------------------------------------


def test_profile_reports_every_missing_capability_at_once() -> None:
    """Автор SDK видит весь список, а не первую ось, после которой всё равно менять драйвер."""
    profile = BrowserProfile("bare", network=CapabilityLevel.BEST_EFFORT)

    with pytest.raises(CapabilityMismatchError) as raised:
        validate_profile(
            (Capability.network, Capability.rich_text, Capability.session_state), profile
        )

    assert raised.value.dimensions == ("rich_text", "session_state")
    validate_profile((Capability.network,), profile)


def test_profile_defaults_to_nothing_and_reads_by_capability() -> None:
    profile = BrowserProfile("bare")

    assert all(
        profile.level(capability) is CapabilityLevel.UNSUPPORTED for capability in Capability
    )
    assert not profile.supports(Capability.network)
    assert FakeDriver().profile.name == "fake"


# --- B3.4: язык признаков -----------------------------------------------------------------


def test_signs_read_as_their_labels() -> None:
    """Диагностика называет критерии, а не `<lambda>` — как `Predicate` в ядре."""
    sign = (visible(css("div.ok")) | text.contains("готово")) & ~url.contains("/login")

    assert (
        sign.label
        == "((visible div.ok or text contains 'готово' ignoring case) and not url contains '/login')"
    )
    assert repr(response.arrived("/api/companies")) == "<response arrived '/api/companies'>"
    assert url.matches(r"/companies/\d+").label == "url matches '/companies/\\\\d+'"


async def test_url_matches_reads_the_location_once_per_round() -> None:
    driver = FakeDriver(url="https://portal.example.com/companies/42")
    page = Observation(driver)

    assert await url.matches(re.compile(r"/companies/\d+$")).holds(page)
    assert not await url.matches("/login").holds(page)
    assert await text.contains("список", ignore_case=True).holds(page)
    assert not await text.contains("список", ignore_case=False).holds(page)
