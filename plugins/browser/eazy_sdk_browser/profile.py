"""Профиль возможностей драйвера и проверка требований до запуска сценария.

Та же форма, что у `HandlerProfile` в `eazy-sdk`: адаптер объявляет по оси, что
умеет, операция — что ей нужно, а несовпадение падает **до** того, как браузер
что-то сделал, одним `CapabilityMismatchError` ядра со всеми осями разом. Шкала —
тоже общая, `CapabilityLevel`: у HTTP это порядок заголовков, у браузера — сеть,
переходы, редактор, но «умеет / умеет с оговорками / не умеет» — одно и то же.

Оси здесь — только свойства **страницы**, потому что `Driver` — это страница.
Прокси, число контекстов и антидетект — свойства браузера и контекста; их создаёт
слой выше, и требование к ним — требование к аренде страницы, а не к драйверу.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from eazy_sdk.handlers import CapabilityLevel, CapabilityMismatchError


class Capability(StrEnum):
    """Оси, по которым драйверы расходятся настолько, что это меняет сценарий.

    Значение — имя поля профиля: `getattr(profile, Capability.network)`.
    """

    network = "network"
    """Видеть ответы, которые получила страница (CDP или эквивалент)."""

    session_state = "session_state"
    """Выгрузить состояние сессии (cookies и localStorage), положить куки и назвать контекст."""

    page_requests = "page_requests"
    """Выполнять HTTP-запрос изнутри страницы: её куками, её TLS, её источником."""

    navigation_events = "navigation_events"
    """Ждать переход как событие, а не опросом адреса."""

    shadow_dom = "shadow_dom"
    """Прокол shadow DOM обычным селектором."""

    rich_text = "rich_text"
    """Положить разметку в редактор (`contenteditable`) так, чтобы редактор это заметил."""


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    """Что умеет конкретный адаптер. Объявляется один раз на класс драйвера.

    Умолчание каждой оси — «не умеет»: драйвер, который ничего о себе не сказал,
    не получает возможностей авансом. Частичный адаптер (playwright без подписки
    на сеть) берёт `dataclasses.replace`.
    """

    name: str
    network: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    session_state: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    page_requests: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    navigation_events: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    shadow_dom: CapabilityLevel = CapabilityLevel.UNSUPPORTED
    rich_text: CapabilityLevel = CapabilityLevel.UNSUPPORTED

    def level(self, capability: Capability) -> CapabilityLevel:
        level: CapabilityLevel = getattr(self, capability.value)
        return level

    def supports(self, capability: Capability) -> bool:
        """Хотя бы `BEST_EFFORT`: сценарий отработает, пусть и не самым прямым путём."""
        return self.level(capability) is not CapabilityLevel.UNSUPPORTED


def validate_profile(requires: tuple[Capability, ...], profile: BrowserProfile) -> None:
    """Сверить требования с профилем; несовпадения — все разом, одним исключением.

    Как `validate_profile` ядра: автор SDK видит полный список того, чего драйверу
    не хватает, а не первую ось, после которой всё равно пришлось бы менять драйвер.
    """
    missing = tuple(str(capability) for capability in requires if not profile.supports(capability))
    if missing:
        raise CapabilityMismatchError(missing)


UNKNOWN_PROFILE = BrowserProfile(name="unknown")
"""Профиль по умолчанию: драйвер ничего о себе не объявил, значит не умеет ничего сверх минимума."""

__all__ = ["UNKNOWN_PROFILE", "BrowserProfile", "Capability", "validate_profile"]
