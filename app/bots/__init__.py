"""Bot package: auto-discovers every bot module in this directory.

Adding a bot = dropping a module here that defines a ``Bot`` subclass wrapped
in ``@register_bot``. See base.py for the minimal example.
"""

from __future__ import annotations

import importlib
import pkgutil

from .base import (  # noqa: F401  (re-exported for convenience)
    Bot,
    BotContext,
    BotError,
    BotResult,
    ConfigField,
    MissingChallengeIdError,
)


def autodiscover() -> None:
    """Import all modules in this package so their @register_bot runs."""
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name in {"base", "browser"}:
            continue
        importlib.import_module(f"{__name__}.{module_info.name}")


# NOTE: autodiscover() is *not* executed on import to avoid a circular
# import (bot modules import app.registry, which imports this package).
# The application entry point (app.main) calls it during startup; tests may
# call it explicitly or import bot modules directly.
