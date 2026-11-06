"""Platform configuration.

Everything is read from environment variables so that no credentials or
environment-specific values ever live in the source code. A sample
configuration is provided in `.env.example`; copy it to `.env` and edit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # reads .env if present; real environment takes precedence


def _env(key: str, default: str) -> str:
    value = os.environ.get(key, "")
    return value if value.strip() else default


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the platform process."""

    # --- HTTP server -------------------------------------------------------
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))

    # --- Storage -----------------------------------------------------------
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", "sqlite:///./arcaptcha.db")
    )

    # --- Execution ---------------------------------------------------------
    max_parallel_runs: int = field(
        default_factory=lambda: int(_env("MAX_PARALLEL_RUNS", "2"))
    )
    run_timeout_seconds: int = field(
        default_factory=lambda: int(_env("RUN_TIMEOUT_SECONDS", "300")),
    )

    # --- Browser -----------------------------------------------------------
    chrome_binary: str = field(default_factory=lambda: _env("CHROME_BINARY", ""))
    chromedriver_path: str = field(default_factory=lambda: _env("CHROMEDRIVER_PATH", ""))
    chrome_no_sandbox: bool = field(
        default_factory=lambda: _env("CHROME_NO_SANDBOX", "").lower()
        in ("1", "true", "yes")
    )
    window_width: int = field(default_factory=lambda: int(_env("WINDOW_WIDTH", "1440")))
    window_height: int = field(
        default_factory=lambda: int(_env("WINDOW_HEIGHT", "900"))
    )

    @staticmethod
    def data_dir() -> Path:
        """Directory for SQLite databases and run artifacts."""
        return Path(_env("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


settings = Settings()
