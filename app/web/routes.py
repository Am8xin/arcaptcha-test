"""Server-rendered pages (Jinja2) + static assets.

The pages are thin clients of the REST API: all dynamic behaviour (launching
runs, refreshing history, live logs) is plain JavaScript talking to /api/*.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"page": "bots"}
    )


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request):
    return templates.TemplateResponse(
        request, "runs.html", {"page": "runs"}
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(request: Request, run_id: str):
    return templates.TemplateResponse(
        request, "run_detail.html", {"page": "run", "run_id": run_id}
    )
