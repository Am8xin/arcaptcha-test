"""REST API of the platform.

The web UI is a client of the same API; anything the UI does can also be
scripted with curl (see README "API reference").
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import registry
from .bots.base import ConfigField, TERMINAL_STATUSES
from .db import count_by_status

router = APIRouter(prefix="/api")


class LaunchRequest(BaseModel):
    bot: str
    mode: str = Field(description="headless or headful")
    config: dict[str, str] = Field(default_factory=dict)
    repeat: int = Field(
        default=1,
        ge=1,
        le=50,
        description="Launch the bot this many times instead of once (each run gets its own record)",
    )


class CancelResponse(BaseModel):
    cancelled: bool


def _field_payload(field_spec: ConfigField) -> dict:
    return {
        "name": field_spec.name,
        "label": field_spec.label,
        "required": field_spec.required,
        "default": "" if field_spec.secret else field_spec.default,
        "secret": field_spec.secret,
        "help": field_spec.help,
    }


@router.get("/bots")
def list_bots() -> list[dict]:
    return [
        {
            "name": bot.name,
            "title": bot.title,
            "description": bot.description,
            "engine": bot.engine,
            "config_fields": [_field_payload(f) for f in bot.config_fields],
        }
        for bot in registry.all_bots()
    ]


@router.post("/runs", status_code=HTTPStatus.ACCEPTED)
def launch_run(payload: LaunchRequest, request: Request):
    runner = request.app.state.runner
    run_ids: list[str] = []
    try:
        for _ in range(payload.repeat):
            run_ids.append(runner.start_run(payload.bot, payload.mode, payload.config))
    except registry.UnknownBot as exc:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {
        "run_id": run_ids[0],  # backward compatible: first of the batch
        "run_ids": run_ids,
        "status": "PENDING",
    }


@router.get("/runs")
def list_runs(
    request: Request,
    limit: int = 50,
    bot: str | None = None,
    status: str | None = None,
) -> list[dict]:
    from sqlalchemy import select

    from .db import Run

    limit = max(1, min(limit, 200))
    with request.app.state.session_factory() as session:
        query = select(Run).order_by(Run.created_at.desc()).limit(limit)
        if bot:
            query = query.where(Run.bot_name == bot)
        if status:
            query = query.where(Run.status == status)
        runs = session.scalars(query).all()
        return [run.to_dict() for run in runs]


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    from .db import Run

    with request.app.state.session_factory() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, "no such run")
        return run.to_dict(include_log=True)


@router.post("/runs/{run_id}/cancel", response_model=CancelResponse)
def cancel_run(run_id: str, request: Request) -> CancelResponse:
    from .db import Run

    runner = request.app.state.runner
    with request.app.state.session_factory() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, "no such run")
        if run.status in TERMINAL_STATUSES and not runner.has_active_slot(run_id):
            raise HTTPException(HTTPStatus.CONFLICT, "run already finished")
    return CancelResponse(cancelled=runner.cancel(run_id))


@router.get("/stats")
def stats(request: Request) -> dict:
    with request.app.state.session_factory() as session:
        return count_by_status(session)
