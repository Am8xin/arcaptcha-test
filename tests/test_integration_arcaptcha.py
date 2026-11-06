"""Integration test against the LIVE demo site.

Deselected by default (pytest.ini: ``-m "not integration"``).
Run explicitly with::

    RUN_INTEGRATION=1 pytest -m integration -v

The run goes through the platform runner with the real Chrome factory, so it
requires Chrome/Chromium + network access. Because the demo site may block a
datacenter IP, the accepted outcomes are SUCCESS or BLOCKED; what the test
guarantees is that the run is recorded with a challenge_id whenever the
widget issued one, and that FAILED/TIMEOUT never occur from test-harness
errors.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

requires_integration = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION", "").lower() not in ("1", "true", "yes"),
    reason="set RUN_INTEGRATION=1 to run live-site tests",
)


@requires_integration
def test_live_run_reaches_success_or_blocked(engine, session_factory, runner):
    from tests.conftest import wait_for_status

    run_id = runner.start_run("arcaptcha-demo", "headless", {})
    run = wait_for_status(
        session_factory, run_id,
        ("SUCCESS", "FAILED", "BLOCKED", "TIMEOUT"),
        timeout=240,
    )

    assert run.status in ("SUCCESS", "BLOCKED")
    if run.status == "SUCCESS":
        assert run.challenge_id
        assert run.details().get("verified") is True
    if run.challenge_id:
        assert len(run.challenge_id) >= 16
