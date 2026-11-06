"""Runner lifecycle tests: every status, persistence guarantees, masking."""

import threading
import time

import pytest

from app.bots.base import Bot, BotResult, ConfigField
from app.db import make_session_factory
from app.registry import UnknownBot
from app.runner import BotRunner
from tests.conftest import wait_for_status

TERMINAL = ("SUCCESS", "FAILED", "BLOCKED", "TIMEOUT", "CANCELLED")


def make_bot(clean_registry, name, script, fields=()):
    class Dynamic(Bot):
        this_name = name
        this_fields = fields

        def run(self, ctx):
            return script(ctx)

    Dynamic.name = name
    Dynamic.title = name.title()
    Dynamic.description = "dynamic test bot"
    Dynamic.config_fields = fields
    bot = Dynamic()
    clean_registry.register(bot)
    return bot


def test_success_run_persists_full_history(session_factory, runner, scripted_bot):
    run_id = runner.start_run("scripted", "headless", {"username": "demo", "password": "pw"})
    run = wait_for_status(session_factory, run_id, TERMINAL)

    assert run.status == "SUCCESS"
    assert run.bot_name == "scripted"
    assert run.mode == "headless"
    assert run.started_at is not None and run.ended_at is not None
    assert run.started_at <= run.ended_at
    assert run.challenge_id == "ch-scripted-1"
    assert run.error is None
    assert run.details()["verified"] is True
    log = run.log_text
    assert "Run starting" in log and "--> SUCCESS" in log


def test_secret_config_is_masked_in_history(session_factory, runner, scripted_bot):
    run_id = runner.start_run(
        "scripted", "headful", {"username": "demo", "password": "super-secret"}
    )
    run = wait_for_status(session_factory, run_id, TERMINAL)

    config = run.config()
    assert config["username"] == "demo"
    assert config["password"] == "********"
    assert "super-secret" not in run.config_json
    assert "super-secret" not in run.log_text


def test_headful_mode_recorded(session_factory, runner, scripted_bot):
    run_id = runner.start_run("scripted", "headful", {"username": "demo"})
    run = wait_for_status(session_factory, run_id, TERMINAL)
    assert run.mode == "headful"
    assert run.status == "SUCCESS"


def test_crashing_bot_becomes_failed_with_error(session_factory, runner, clean_registry):
    def boom(ctx):
        raise ValueError("boom")

    make_bot(clean_registry, "crasher", boom)
    run_id = runner.start_run("crasher", "headless", {})
    run = wait_for_status(session_factory, run_id, TERMINAL)

    assert run.status == "FAILED"
    assert "ValueError" in run.error and "boom" in run.error
    assert run.ended_at is not None


def test_blocked_result_remains_visible(session_factory, runner, clean_registry):
    def blocked(ctx):
        return BotResult(status="BLOCKED", challenge_id="ch-x", error="widget refused")

    make_bot(clean_registry, "blockedbot", blocked)
    run_id = runner.start_run("blockedbot", "headless", {})
    run = wait_for_status(session_factory, run_id, TERMINAL)

    assert run.status == "BLOCKED"
    assert run.challenge_id == "ch-x"
    assert run.error == "widget refused"


def test_challenge_id_reported_midrun_survives_later_failure(
    session_factory, runner, clean_registry
):
    """PDF requirement: record challenge_id whenever it is obtained."""
    def failing_after_report(ctx):
        ctx.report(challenge_id="ch-kept-42", detail="halfway")
        ctx.log("challenge captured, now failing")
        raise RuntimeError("network died after the challenge")

    make_bot(clean_registry, "midreport", failing_after_report)
    run_id = runner.start_run("midreport", "headless", {})
    run = wait_for_status(session_factory, run_id, TERMINAL)

    assert run.status == "FAILED"
    assert run.challenge_id == "ch-kept-42"
    assert run.details().get("detail") == "halfway"


def test_timeout_marks_run_and_runs_cleanup(session_factory, engine, clean_registry):
    runner = BotRunner(make_session_factory(engine), max_workers=1, run_timeout=1)

    started = threading.Event()
    released = threading.Event()
    cleanup_called = threading.Event()

    def slow(ctx):
        ctx.on_cleanup(cleanup_called.set)
        started.set()
        released.wait(timeout=10)
        return BotResult(status="SUCCESS")

    make_bot(clean_registry, "slowbot", slow)
    try:
        run_id = runner.start_run("slowbot", "headless", {})
        assert started.wait(timeout=5)
        run = wait_for_status(session_factory, run_id, ("TIMEOUT",), timeout=8)
        assert "deadline" in run.error
        assert cleanup_called.wait(timeout=5)
    finally:
        released.set()
        runner.shutdown(wait=False)


def test_cancel_marks_run_cancelled(session_factory, runner, clean_registry):
    def waiter(ctx):
        for _ in range(200):
            if ctx.should_cancel():
                break
            time.sleep(0.05)
        return BotResult(status="SUCCESS")

    make_bot(clean_registry, "waiter", waiter)
    run_id = runner.start_run("waiter", "headless", {})
    wait_for_status(session_factory, run_id, ("RUNNING",))
    assert runner.cancel(run_id) is True

    run = wait_for_status(session_factory, run_id, TERMINAL)
    assert run.status == "CANCELLED"


def test_invalid_mode_rejected(runner, scripted_bot):
    with pytest.raises(ValueError):
        runner.start_run("scripted", "invisible", {})


def test_unknown_bot_rejected(runner, scripted_bot):
    with pytest.raises(UnknownBot):
        runner.start_run("ghost", "headless", {})


def test_missing_required_config_rejected(runner, clean_registry):
    make_bot(
        clean_registry,
        "strict",
        lambda ctx: BotResult(status="SUCCESS"),
        fields=(ConfigField("token", "Token", required=True),),
    )
    with pytest.raises(ValueError):
        runner.start_run("strict", "headless", {})
