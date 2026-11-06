"""Execution layer: runs bots, records everything in the run history.

The runner is the only component that writes runs to the database; bots and
the web layer never touch run persistence directly. Every run goes through
the same lifecycle:

    PENDING -> RUNNING -> SUCCESS | FAILED | BLOCKED | TIMEOUT | CANCELLED

A watchdog enforces ``RUN_TIMEOUT_SECONDS``: if a bot exceeds the deadline the
run is marked TIMEOUT and the cleanup hooks registered by the bot (driver
quit) are invoked from the runner side.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import registry
from .bots.base import (
    ACTIVE_STATUSES,
    Bot,
    BotContext,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_TIMEOUT,
)
from .config import settings
from .db import Run, make_engine, make_session_factory

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 400


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def mask_config(bot: Bot, config: dict[str, str]) -> dict[str, str]:
    """Return a copy of *config* safe for persistence (secret fields masked)."""
    masked = dict(config)
    for field_spec in bot.config_fields:
        if field_spec.secret and masked.get(field_spec.name):
            masked[field_spec.name] = "********"
    return masked


class BotRunner:
    def __init__(
        self,
        session_factory=None,
        *,
        max_workers: int | None = None,
        run_timeout: int | None = None,
    ) -> None:
        if session_factory is None:
            session_factory = make_session_factory(make_engine())
        self._session_factory = session_factory
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers or settings.max_parallel_runs,
            thread_name_prefix="botrun",
        )
        self._timeout = run_timeout or settings.run_timeout_seconds
        self._cancel_events: dict[str, threading.Event] = {}

    # -- public API ----------------------------------------------------------

    def start_run(
        self, bot_name: str, mode: str, config: dict[str, str] | None = None
    ) -> str:
        bot = registry.get(bot_name)  # raises UnknownBot
        if mode not in ("headless", "headful"):
            raise ValueError(f"invalid mode: {mode!r}")
        resolved = bot.validate_config(config or {})

        run_id = str(uuid.uuid4())
        cancel_event = threading.Event()
        self._cancel_events[run_id] = cancel_event

        with self._session_factory() as session:
            session.add(
                Run(
                    id=run_id,
                    bot_name=bot.name,
                    mode=mode,
                    status=STATUS_PENDING,
                    config_json=json.dumps(mask_config(bot, resolved)),
                    created_at=_utcnow(),
                )
            )
            session.commit()

        self._pool.submit(self._execute, run_id, bot, mode, resolved, cancel_event)
        logger.info("run %s scheduled: bot=%s mode=%s", run_id, bot_name, mode)
        return run_id

    def cancel(self, run_id: str) -> bool:
        event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def has_active_slot(self, run_id: str) -> bool:
        return run_id in self._cancel_events

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)

    # -- worker ----------------------------------------------------------------

    def _execute(
        self,
        run_id: str,
        bot: Bot,
        mode: str,
        config: dict[str, str],
        cancel_event: threading.Event,
    ) -> None:
        log_lines: list[str] = []
        log_lock = threading.Lock()

        def persist(**changes) -> None:
            with self._session_factory() as session:
                run = session.get(Run, run_id)
                if run is None:
                    return
                if changes.get("challenge_id"):
                    run.challenge_id = changes["challenge_id"]
                if changes.get("details"):
                    merged = run.details()
                    merged.update(changes["details"])
                    run.details_json = json.dumps(merged, ensure_ascii=False)
                session.commit()

        def append_log(line: str) -> None:
            stamp = _dt.datetime.now().strftime("%H:%M:%S")
            with log_lock:
                log_lines.append(f"[{stamp}] {line}")
                payload = "\n".join(log_lines[-MAX_LOG_LINES:])
            with self._session_factory() as session:
                run = session.get(Run, run_id)
                if run is not None:
                    run.log_text = payload
                    session.commit()

        ctx = BotContext(
            run_id=run_id,
            headless=(mode == "headless"),
            config=config,
            cancel_event=cancel_event,
            _report_cb=persist,
            _log_cb=append_log,
        )

        def mark(
            status: str,
            *,
            error: str | None = None,
            details: dict | None = None,
        ) -> None:
            with self._session_factory() as session:
                run = session.get(Run, run_id)
                if run is None:
                    return
                run.status = status
                if run.started_at is None:
                    run.started_at = _utcnow()
                if status not in ACTIVE_STATUSES:
                    run.ended_at = _utcnow()
                if error:
                    run.error = error[:2000]
                if details:
                    merged = run.details()
                    merged.update(details)
                    run.details_json = json.dumps(merged, ensure_ascii=False)
                session.commit()
            append_log(f"--> {status}" + (f": {error}" if error else ""))

        append_log(f"Run starting: bot={bot.name} mode={mode}")
        mark(STATUS_RUNNING)

        worker = threading.Thread(
            target=self._safe_run,
            args=(bot, ctx, mark, persist),
            daemon=True,
        )
        worker.start()
        worker.join(self._timeout)

        timed_out = worker.is_alive()
        if timed_out:
            cancel_event.set()
            mark(STATUS_TIMEOUT, error=f"run exceeded the {self._timeout}s deadline")

        # Always run cleanup hooks (driver quit). Idempotent by contract.
        try:
            self._run_cleanup_hooks(ctx)
        except Exception:  # pragma: no cover
            logger.debug("cleanup failed", exc_info=True)

        self._cancel_events.pop(run_id, None)

    @staticmethod
    def _run_cleanup_hooks(ctx: BotContext) -> None:
        for hook in list(ctx._cleanup_hooks):
            try:
                hook()
            except Exception:  # pragma: no cover
                logger.debug("cleanup hook failed", exc_info=True)

    def _safe_run(self, bot: Bot, ctx: BotContext, mark, persist) -> None:
        try:
            result = bot.run(ctx)
            if ctx.should_cancel():
                mark(STATUS_CANCELLED, details=result.details)
                return
            persist(challenge_id=result.challenge_id, details=result.details)
            mark(result.status, error=result.error)
        except Exception as exc:  # bots should catch their own, but never leak
            logger.exception("bot %s crashed", bot.name)
            mark(STATUS_FAILED, error=f"{type(exc).__name__}: {exc}")
