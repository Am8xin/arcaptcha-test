"""Bot registry: how implementations become visible to the platform.

Two equivalent ways to register a bot:

1. Decorate the class with :func:`register_bot` and drop the module into
   ``app/bots/`` (auto-discovered at import time, see ``app.bots.__init__``).
2. Call :func:`register` manually with an instance::

       from app.registry import register
       register(MyBot(), replace=True)

The web UI, the REST API and the runner read the registry; they never import
bot modules directly, which keeps implementations and platform core decoupled.
"""

from __future__ import annotations

import threading
from typing import TypeVar, Type

from .bots.base import Bot

_TBot = TypeVar("_TBot", bound=Bot)

_lock = threading.RLock()
_registry: dict[str, Bot] = {}


class BotAlreadyRegistered(Exception):
    pass


class UnknownBot(Exception):
    pass


def register(bot: Bot, *, replace: bool = False) -> Bot:
    """Register a bot *instance*. Called by :func:`register_bot`."""
    with _lock:
        if bot.name in _registry and not replace:
            raise BotAlreadyRegistered(
                f"a bot named {bot.name!r} is already registered"
            )
        if not bot.name or not bot.title:
            raise ValueError("bots must define non-empty 'name' and 'title'")
        _registry[bot.name] = bot
    return bot


def register_bot(cls: Type[_TBot]) -> Type[_TBot]:
    """Class decorator: instantiate and register immediately."""

    register(cls())
    return cls


def get(name: str) -> Bot:
    with _lock:
        try:
            return _registry[name]
        except KeyError:
            raise UnknownBot(f"no bot registered as {name!r}") from None


def all_bots() -> list[Bot]:
    with _lock:
        return list(_registry.values())


def names() -> list[str]:
    with _lock:
        return sorted(_registry)


def clear() -> None:
    """Test helper: empty the registry."""
    with _lock:
        _registry.clear()


def snapshot() -> dict[str, Bot]:
    """Test helper: capture the current registry contents."""
    with _lock:
        return dict(_registry)


def restore(saved: dict[str, Bot]) -> None:
    """Test helper: replace the registry contents with *saved*."""
    with _lock:
        _registry.clear()
        _registry.update(saved)
