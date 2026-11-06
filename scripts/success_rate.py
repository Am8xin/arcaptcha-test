"""Bonus evidence: measure the anti-blocking success rate.

Runs the ARCaptcha demo bot N times through the platform runner (isolated
database) and prints a table with status, challenge_id and duration, plus a
success-rate summary.

Usage:
    python scripts/success_rate.py --runs 10 --mode headless
    python scripts/success_rate.py --runs 5  --mode headless --engine plain

``--engine plain`` builds the driver WITHOUT undetected-chromedriver, UA
normalisation is kept, to demonstrate the contribution of the stealth stack
(the comparison table in the README was produced this way).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import registry  # noqa: E402
from app.bots import autodiscover  # noqa: E402
from app.bots import arcaptcha_demo_bot as bot_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import init_db, make_engine, make_session_factory  # noqa: E402
from app.runner import BotRunner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--mode", choices=["headless", "headful"], default="headless")
    parser.add_argument(
        "--engine",
        choices=["stealth", "plain"],
        default="stealth",
        help="stealth=undetected-chromedriver (default), plain=stock Selenium",
    )
    parser.add_argument("--db", default="sqlite:///./success_rate.db")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    autodiscover()
    registry.get("arcaptcha-demo")  # sanity check

    if args.engine == "plain":
        # Completely vanilla Selenium: no undetected-chromedriver, no UA
        # normalisation, no stealth patches — the "before" baseline.
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        def plain_factory(headless: bool):
            options = Options()
            if settings.chrome_binary:
                options.binary_location = settings.chrome_binary
            if headless:
                options.add_argument("--headless=new")
            options.add_argument("--window-size=1440,900")
            options.add_argument("--disable-dev-shm-usage")
            if settings.chrome_no_sandbox:
                options.add_argument("--no-sandbox")
            options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
            from selenium.webdriver.chrome.service import Service

            service = (
                Service(settings.chromedriver_path)
                if settings.chromedriver_path else Service()
            )
            return webdriver.Chrome(service=service, options=options)

        bot = bot_module.ArcaptchaDemoBot(driver_factory=plain_factory)
        registry.register(bot, replace=True)

    engine = make_engine(args.db)
    init_db(engine)
    session_factory = make_session_factory(engine)
    runner = BotRunner(session_factory, max_workers=1, run_timeout=args.timeout)

    from tests.conftest import wait_for_status  # reuse the polling helper

    results = []
    try:
        for index in range(1, args.runs + 1):
            run_id = runner.start_run("arcaptcha-demo", args.mode, {})
            try:
                run = wait_for_status(
                    session_factory, run_id,
                    ("SUCCESS", "FAILED", "BLOCKED", "TIMEOUT"),
                    timeout=args.timeout + 30,
                )
                duration = (
                    (run.ended_at - run.started_at).total_seconds()
                    if run.started_at and run.ended_at else float("nan")
                )
                results.append((index, run.status, run.challenge_id, duration, run.error))
            except AssertionError as exc:
                results.append((index, "HANG", None, float("nan"), str(exc)))
            status = results[-1][1]
            mark = "✓" if status == "SUCCESS" else "✗"
            print(
                f"{mark} run {index:>2}/{args.runs}: {status:<8} "
                f"challenge_id={results[-1][2] or '-'} "
                f"({results[-1][3]:.1f}s)"
                f"{'' if not results[-1][4] else '  error=' + results[-1][4][:80]}"
            )
    finally:
        runner.shutdown(wait=False)

    total = len(results)
    success = sum(1 for r in results if r[1] == "SUCCESS")
    blocked = sum(1 for r in results if r[1] == "BLOCKED")
    failed = sum(1 for r in results if r[1] in ("FAILED", "TIMEOUT", "HANG"))
    print("\n=== summary ===")
    print(f"engine={args.engine} mode={args.mode} runs={total}")
    print(f"success: {success}/{total} ({100 * success / total:.0f}%)")
    print(f"blocked: {blocked}/{total}")
    print(f"failed/timeout: {failed}/{total}")
    print(f"challenge_id captured in {sum(1 for r in results if r[2])}/{total} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
