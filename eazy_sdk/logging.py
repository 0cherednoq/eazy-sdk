"""Standard-logging helpers. Eazy SDK never uses ``print``."""

from __future__ import annotations

import logging

LOGGER_NAME = "eazy_sdk"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the ``eazy_sdk`` logger or a child of it."""
    if name is None:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
