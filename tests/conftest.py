"""Shared pytest fixtures.

Tests run fully offline: the database lives in a temporary SQLite file and
 Selenium/Chrome is never launched (bots receive fake drivers or scripted
behaviour). Real-network tests are marked ``integration`` and skipped by
default (see test_integration_arcaptcha.py).
"""

from __future__ import annotations

import threading
import time as _time

import pytest
from fastapi.testclient import TestClient

from app.bots.base import Bot, BotResult, ConfigField
from app.db import make_engine, make_session_factory, init_db
from app.main import create_app
from app.runner import BotRunner
from app import registry


class ScriptedBot(Bot):
    """Configurable bot used to exercise the runner lifecycle."""

    name = "scripted"
    title = "Scripted Test Bot"
    description = "A bot whose behaviour is scripted by the test."
    config_fields = (
        ConfigField("username", "Username", required=True, default="demo"),
        ConfigField("password", "Password", required=True, default="s3cret", secret=True),
    )

    def __init__(self, script=None):
        self.script = script or self._default_script
        self.cleanup_called = threading.Event()

    @staticmethod
    def _default_script(ctx) -> BotResult:
        ctx.log("scripted success")
        return BotResult(
            status="SUCCESS", challenge_id="ch-scripted-1", details={"verified": True}
        )

    def run(self, ctx) -> BotResult:
        ctx.on_cleanup(self.cleanup_called.set)
        return self.script(ctx)


@pytest.fixture()
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture()
def clean_registry():
    saved = registry.snapshot()
    registry.clear()
    yield registry
    registry.restore(saved)


@pytest.fixture()
def scripted_bot(clean_registry):
    bot = ScriptedBot()
    clean_registry.register(bot)
    return bot


@pytest.fixture()
def runner(session_factory):
    runner = BotRunner(session_factory, max_workers=2, run_timeout=4)
    yield runner
    runner.shutdown(wait=False)


@pytest.fixture()
def client(engine, session_factory, runner):
    app = create_app(engine=engine, session_factory=session_factory, runner=runner)
    with TestClient(app) as test_client:
        yield test_client


def wait_for_status(session_factory, run_id, statuses, timeout=10.0):
    """Poll the DB until the run reaches one of *statuses*; return the Run."""
    from app.db import Run

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        with session_factory() as session:
            run = session.get(Run, run_id)
            if run is not None and run.status in statuses:
                return run
        _time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {statuses} in time")
