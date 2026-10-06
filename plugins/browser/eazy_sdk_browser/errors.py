"""Корни ошибок плагина — под `EazySdkError`, как у HTTP-транспорта.

Вызывающий, который ловит `EazySdkError`, ловит и браузерные отказы: одна библиотека —
один корень. Два корня ниже:

* `BrowserDeclarationError` — объявление собрано неверно, и это видно до запуска
  (карта без маркера, операция без спеки). Наследует `PlanError` ядра: это та же
  категория, что D-54-xx у HTTP-операций, и тот же момент — импорт или сборка.
* `BrowserError` — отказ во время сценария: элемент не нашёлся, страница не готова,
  ответа нет, портал отказал названным исключением.

Отказ транспорта — закрытая страница, оборванное соединение — идёт `TransportError`
ядра без своего подкласса: адаптер — такой же обработчик, как `httpx` или `curl`.
"""

from __future__ import annotations

from eazy_sdk.core.errors import EazySdkError, PlanError


class BrowserDeclarationError(PlanError):
    """Объявление операции или карты собрано неверно; видно до первого действия."""


class BrowserError(EazySdkError):
    """Отказ во время браузерного сценария."""


__all__ = ["BrowserDeclarationError", "BrowserError"]
