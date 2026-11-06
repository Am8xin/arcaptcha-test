"""Database layer: engine, session factory and the persistent run history.

Run records survive container restarts because the SQLite file lives on a
Docker volume (see docker-compose.yml). Any SQLAlchemy-compatible database can
be used by pointing `DATABASE_URL` elsewhere (e.g. PostgreSQL).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from sqlalchemy import (
    DateTime,
    Index,
    String,
    Text,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from .config import settings


class Base(DeclarativeBase):
    pass


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


class Run(Base):
    """One execution of one bot.

    This table *is* the persistent history required by the platform spec:
    run ID, bot identity, browser mode, start/end times, challenge_id, final
    status and error details. Failed and blocked runs are never deleted.
    """

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bot_name: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)  # headless|headful
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    started_at: Mapped[_dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[_dt.datetime | None] = mapped_column(DateTime, nullable=True)
    challenge_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    log_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_runs_created", "created_at"),
        Index("ix_runs_status", "status"),
    )

    # -- small helpers used by the runner / API ------------------------------

    def details(self) -> dict:
        try:
            return json.loads(self.details_json or "{}")
        except json.JSONDecodeError:
            return {}

    def config(self) -> dict:
        try:
            return json.loads(self.config_json or "{}")
        except json.JSONDecodeError:
            return {}

    def log_lines(self) -> list[str]:
        return (self.log_text or "").splitlines()

    def to_dict(self, *, include_log: bool = False) -> dict:
        payload = {
            "id": self.id,
            "bot": self.bot_name,
            "mode": self.mode,
            "status": self.status,
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "ended_at": self.ended_at.isoformat(timespec="seconds") if self.ended_at else None,
            "challenge_id": self.challenge_id,
            "error": self.error,
            "details": self.details(),
            "config": self.config(),
            "created_at": self.created_at.isoformat(timespec="seconds"),
        }
        if include_log:
            payload["log"] = self.log_lines()
        return payload


def _prepare_sqlite(url: str) -> str:
    """Make sure the parent directory of a SQLite URL exists."""
    if url.startswith("sqlite"):
        path_part = url.split("///")[-1]
        if path_part and path_part != ":memory:":
            Path(path_part).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return url


def make_engine(database_url: str | None = None):
    url = _prepare_sqlite(database_url or settings.database_url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        future=True,
    )
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def count_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Run.status, func.count()).group_by(Run.status)
    ).all()
    return {status: count for status, count in rows}
