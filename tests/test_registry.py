"""Registration mechanism tests."""

import pytest

from app.bots.base import Bot, BotResult
from app.registry import (
    BotAlreadyRegistered,
    UnknownBot,
    register,
    register_bot,
    get,
    names,
)


def test_register_and_lookup(clean_registry):
    class Probe(Bot):
        name = "probe"
        title = "Probe"
        description = ""

        def run(self, ctx):
            return BotResult(status="SUCCESS")

    register(Probe())
    assert get("probe").name == "probe"
    assert "probe" in names()


def test_duplicate_registration_rejected(clean_registry):
    class Dup(Bot):
        name = "dup"
        title = "Dup"

        def run(self, ctx):
            return BotResult(status="SUCCESS")

    register(Dup())
    with pytest.raises(BotAlreadyRegistered):
        register(Dup())


def test_decorator_registers_instance(clean_registry):
    @register_bot
    class Decorated(Bot):
        name = "decorated"
        title = "Decorated Bot"

        def run(self, ctx):
            return BotResult(status="SUCCESS")

    assert isinstance(get("decorated"), Decorated)


def test_unknown_bot_raises(clean_registry):
    with pytest.raises(UnknownBot):
        get("does-not-exist")


def test_invalid_bot_rejected(clean_registry):
    class NoName(Bot):
        name = ""
        title = ""

        def run(self, ctx):
            return BotResult(status="SUCCESS")

    with pytest.raises(ValueError):
        register(NoName())
