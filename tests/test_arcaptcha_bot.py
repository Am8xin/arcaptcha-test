"""Failure-mode tests for the ARCaptcha demo bot.

Each test drives the REAL bot implementation against a scripted fake
Selenium driver (see fake_driver.py), covering the meaningful failures
required by the challenge spec:

* missing challenge_id
* CAPTCHA blocking
* timeouts
* unsuccessful form verification (submitting the form alone is NOT success)

plus the success path, the pass-without-puzzle path and the retry path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_driver import FakeDriver  # noqa: E402

from app.bots.arcaptcha_demo_bot import ArcaptchaDemoBot  # noqa: E402
from app.bots.base import BotContext  # noqa: E402


def make_ctx():
    return BotContext(
        run_id="unit-run",
        headless=True,
        config={"username": "demo", "password": "demo"},
    )


@pytest.fixture()
def fast_bot(monkeypatch):
    """Speed the bot up: no sleeps, short waits."""
    import app.bots.arcaptcha_demo_bot as bot_module

    monkeypatch.setattr(bot_module, "_sleep", lambda *_: None)
    monkeypatch.setattr(bot_module, "WAIT_AFTER_LOAD", (0, 0))
    monkeypatch.setattr(bot_module, "WAIT_AFTER_SOLVE", 0.01)
    monkeypatch.setattr(bot_module, "WAIT_AFTER_LOGIN", 0.01)
    monkeypatch.setattr(bot_module, "CHALLENGE_REACTION_TIMEOUT", 2.0)
    monkeypatch.setattr(bot_module, "CHALLENGE_ID_TIMEOUT", 0.5)
    monkeypatch.setattr(bot_module, "LOGIN_RESPONSE_TIMEOUT", 0.8)
    return bot_module


def run_bot(scenario: str, fast_bot) -> tuple:
    driver = FakeDriver(scenario)
    bot = ArcaptchaDemoBot(driver_factory=lambda headless: driver)
    result = bot.run(make_ctx())
    for hook in list(make_ctx()._cleanup_hooks):  # no-op; runner normally does this
        pass
    return result, driver


def test_success_path_with_puzzle(fast_bot):
    result, driver = run_bot("success", fast_bot)

    assert result.status == "SUCCESS", result.error
    assert result.challenge_id  # a uuid captured from widget traffic
    assert len(result.challenge_id) >= 16
    assert result.details["verified"] is True
    assert result.details["captcha_solved"] is True
    assert result.details["login_http_status"] == 200
    assert driver.site.solve_attempts == 1


def test_pass_without_puzzle_is_success(fast_bot):
    result, driver = run_bot("pass_no_puzzle", fast_bot)

    assert result.status == "SUCCESS"
    assert result.challenge_id
    assert result.details["verified"] is True


def test_missing_challenge_id_fails(fast_bot):
    """Puzzle appears but the widget never issues a challenge id -> FAILED."""
    result, driver = run_bot("missing_challenge_id", fast_bot)

    assert result.status == "FAILED"
    assert "challenge_id" in (result.error or "").lower()
    assert result.challenge_id is None


def test_captcha_blocking_is_blocked(fast_bot):
    """Widget shows its explicit 'Blocked!' message -> BLOCKED (not FAILED)."""
    result, driver = run_bot("blocked", fast_bot)

    assert result.status == "BLOCKED"
    assert "blocked" in (result.error or "").lower()
    assert result.details.get("verified") is False
    # the challenge was still issued and captured before the block
    assert result.challenge_id is not None


def test_unsuccessful_form_verification_is_not_success(fast_bot):
    """Captcha solved and form submitted, but login rejected -> FAILED."""
    result, driver = run_bot("login_rejected", fast_bot)

    assert result.status == "FAILED"
    assert result.status != "SUCCESS"
    assert "Form verification unsuccessful" in (result.error or "")
    assert result.details["login_http_status"] == 401
    assert result.details["verified"] is False
    assert result.challenge_id  # challenge was still captured and persisted


def test_missing_login_response_is_not_success(fast_bot):
    """Form submitted but /api/login never observed -> FAILED, never SUCCESS."""
    result, driver = run_bot("no_login_response", fast_bot)

    assert result.status == "FAILED"
    assert "no /api/login response" in (result.error or "")
    assert result.details["verified"] is False


def test_puzzle_retry_then_success(fast_bot):
    result, driver = run_bot("puzzle_retry_then_pass", fast_bot)

    assert result.status == "SUCCESS", result.error
    assert driver.site.solve_attempts == 2
    assert len(driver.site.challenge_ids) == 2  # widget re-issued a challenge


def test_block_is_followed_by_refresh_and_retry(fast_bot):
    """Bonus: an explicit block on the first load is retried after a reload."""
    result, driver = run_bot("blocked_then_pass", fast_bot)

    assert result.status == "SUCCESS", result.error
    assert driver.site.load_count == 2  # one reload after the block
    assert result.details.get("refresh_retries") == 1


def test_runner_level_flow_with_fake_driver(
    session_factory, runner, clean_registry, fast_bot, monkeypatch
):
    """The bot wired through the real runner: DB record + cleanup verified."""
    driver = FakeDriver("success")
    bot = ArcaptchaDemoBot(driver_factory=lambda headless: driver)
    clean_registry.register(bot)

    run_id = runner.start_run("arcaptcha-demo", "headless", {"username": "demo"})
    from tests.conftest import wait_for_status

    run = wait_for_status(session_factory, run_id, ("SUCCESS", "FAILED", "BLOCKED"))
    assert run.status == "SUCCESS", run.error
    assert run.challenge_id
    assert run.details()["verified"] is True
    assert driver.site.closed  # runner ran the cleanup hook (driver.quit)
