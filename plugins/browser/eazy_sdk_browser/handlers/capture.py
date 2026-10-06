"""Что драйвер читает из сети страницы и сколько держит в памяти процесса.

Тело ответа читается сразу, потому что после ухода страницы его уже не достать. Но
читать **всё** нельзя: страница за долгий сценарий тянет картинки, шрифты и бандлы
мегабайтами, и память процесса Python растёт незаметно для лимитов браузера. Операциям
нужны ответы API и документы — остальное не читается вовсе, а буфер ограничен в байтах,
а не в штуках.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class CapturePolicy:
    """Политика захвата ответов страницы. `capture=None` у драйвера — сеть не слушается."""

    resource_types: frozenset[str] = frozenset({"xhr", "fetch", "document"})
    """Какие типы ресурсов читать. Проверяется до чтения тела."""
    max_body_bytes: int = 2 * 1024 * 1024
    """Тело больше — не читается: ответ попадает в буфер с пустым телом и `body_dropped`."""
    max_total_bytes: int = 32 * 1024 * 1024
    """Сколько байт тел держать; старые ответы вытесняются, позиции остаются монотонными."""

    def __post_init__(self) -> None:
        if self.max_body_bytes < 0 or self.max_total_bytes < 0:
            msg = "capture limits cannot be negative"
            raise ValueError(msg)


DEFAULT_CAPTURE = CapturePolicy()
"""Политика по умолчанию: XHR, fetch и документы; 2 МБ на тело, 32 МБ на буфер."""

__all__ = ["DEFAULT_CAPTURE", "CapturePolicy"]
