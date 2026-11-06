"""Bot abstraction: the ONLY contract between bots and the platform core.

To add a new bot (or a new browser-automation engine) you implement :class:`Bot`
and register it with :func:`app.registry.register`. Nothing else in the
platform needs to change: the runner, storage and web UI discover bots through
the registry at runtime.

Minimal example (also documented in the README):

    from app.bots.base import Bot, BotContext, BotResult
    from app.registry import register_bot

    @register_bot
    class HelloBot(Bot):
        name = "hello"
        title = "Hello Bot"
        description = "A minimal example bot."

        def run(self, ctx: BotContext) -> BotResult:
            ctx.log("hello from a custom bot")
            return BotResult(status="SUCCESS", details={"greeting": "hi"})
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar


# Final run statuses (persisted in the run history).
STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_BLOCKED = "BLOCKED"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_CANCELLED = "CANCELLED"

TERMINAL_STATUSES = {STATUS_SUCCESS, STATUS_FAILED, STATUS_BLOCKED, STATUS_TIMEOUT, STATUS_CANCELLED}
ACTIVE_STATUSES = {STATUS_PENDING, STATUS_RUNNING}


@dataclass(frozen=True)
class ConfigField:
    """A per-bot configuration input rendered by the web UI.

    ``secret=True`` fields are masked in the persisted history and never
    written to logs.
    """

    name: str
    label: str
    required: bool = False
    default: str = ""
    secret: bool = False
    help: str = ""


class BotError(Exception):
    """Raised by bots for run failures; the message is stored as run error."""


class MissingChallengeIdError(BotError):
    """Raised when the challenge_id could not be captured."""


class CaptchaBlockedError(BotError):
    """Raised when the CAPTCHA blocks the run (no reaction / not accepted)."""


@dataclass
class BotContext:
    """Handed to :meth:`Bot.run`; the bot reports progress through it.

    * ``report(challenge_id=...)`` persists data as soon as it is known, so a
      run that later fails or gets blocked still records its challenge_id.
    * ``log(...)`` appends a line to the run log visible in the web UI.
    * ``should_cancel()`` lets long bots abort quickly when a run is cancelled.
    """

    run_id: str
    headless: bool
    config: dict[str, str]
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _report_cb: Callable[..., None] = field(default=lambda **_: None, repr=False)
    _log_cb: Callable[[str], None] = field(default=lambda _line: None, repr=False)
    _cleanup_hooks: list[Callable[[], None]] = field(default_factory=list, repr=False)

    # -- reporting -----------------------------------------------------------
    def report(self, *, challenge_id: str | None = None, **details: Any) -> None:
        if challenge_id is not None:
            self._report_cb(challenge_id=challenge_id)
        if details:
            self._report_cb(details=details)

    def log(self, message: str) -> None:
        self._log_cb(str(message))

    # -- lifecycle -----------------------------------------------------------
    def should_cancel(self) -> bool:
        return self.cancel_event.is_set()

    def on_cleanup(self, fn: Callable[[], None]) -> None:
        """Register a function the runner must call even after timeouts."""
        self._cleanup_hooks.append(fn)


@dataclass
class BotResult:
    """What a bot reports when its run finishes."""

    status: str  # SUCCESS | FAILED | BLOCKED
    challenge_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError(f"invalid BotResult status: {self.status!r}")


class Bot(abc.ABC):
    """Base class every bot must extend.

    Class attributes describe the bot for listing/UI purposes; :meth:`run`
    performs the actual automation and MUST return a :class:`BotResult` whose
    ``status`` is SUCCESS only when the end-to-end goal truly succeeded
    (e.g. the form verification actually passed, not merely submitted).
    """

    name: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    engine: ClassVar[str] = "selenium"  # automation engine, shown in the UI
    config_fields: ClassVar[tuple[ConfigField, ...]] = ()

    @abc.abstractmethod
    def run(self, ctx: BotContext) -> BotResult:  # pragma: no cover - interface
        ...

    def validate_config(self, config: dict[str, str]) -> dict[str, str]:
        """Fill defaults and enforce required fields; raises ValueError."""
        resolved: dict[str, str] = {}
        for field_spec in self.config_fields:
            value = (config.get(field_spec.name) or "").strip()
            if not value and field_spec.default:
                value = field_spec.default
            if field_spec.required and not value:
                raise ValueError(f"missing required config field: {field_spec.name}")
            resolved[field_spec.name] = value
        # keep unknown keys too, so bots may accept free-form configuration
        for key, value in config.items():
            resolved.setdefault(key, value)
        return resolved
