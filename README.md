# ARCaptcha Browser Bot Platform

A small web platform (FastAPI + Selenium) that runs the
[ARCaptcha demo](https://demo.arcaptcha.co/) flow end-to-end: opens the page,
solves the checkbox / slider CAPTCHA with undetected-chromedriver, submits the
login form and **verifies** the result. Every run — success, failure or blocked
— is stored in a persistent history with a live log.

## Quick start

> The folder name does **not** matter. Use whichever folder contains
> `app/` and `docker-compose.yml` — for example
> `C:\Users\amin\Desktop\selenium-demo\demo`.

### Option A — Docker (easiest)

Prereq: [Docker Desktop](https://docs.docker.com/get-docker/) installed and running.

```sh
docker compose up --build
```

Then open **http://localhost:8000**. (The first build downloads Chromium and
the Python dependencies; later builds are cached.)

### Option B — Plain Python (Windows / macOS / Linux)

Prereqs: Python **3.11+** and Google Chrome installed.

```powershell
# Windows PowerShell — run inside the project folder
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app
```

macOS / Linux: activate the venv with `source .venv/bin/activate` instead.

`.env` is **optional** — defaults work out of the box. Copy `.env.example` to
`.env` only when you want to change a setting.

## Use it

| Where | What |
| --- | --- |
| `http://localhost:8000` | Bots page → **Launch run** |
| Launch dialog | mode (**Headless / Headful**), a **credentials table** (any number of username/password pairs), **Number of runs** (1–50) |
| `/runs` | full run history, filterable, auto-refreshes |
| `/docs` | interactive REST API |

**Multiple credentials:** in the launch dialog, add as many username/password
pairs as you need. Run *i* uses pair *i* — when there are more runs than pairs,
the pairs repeat in order (2 pairs × 5 runs → a, b, a, b, a). Leave the table
empty to use the default `demo` / `demo` credentials. Passwords are masked in
the run history.

Launch via the API instead of the UI:

```sh
curl -X POST localhost:8000/api/runs -H "Content-Type: application/json" \
     -d '{"bot":"arcaptcha-demo","mode":"headless","repeat":5,
          "credentials":[{"username":"u1","password":"p1"},
                         {"username":"u2","password":"p2"}]}'
```

## Configuration

All settings are environment variables (put them in `.env`). None are required.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAX_PARALLEL_RUNS` | `4` | runs executing at the same time |
| `RUN_TIMEOUT_SECONDS` | `300` | deadline per run |
| `PAGE_LOAD_TIMEOUT` | `30` | Chrome page-load deadline |
| `START_VNC` | `false` | Docker only: watch headful runs at `http://localhost:7900` |
| `ARC_USERNAME` / `ARC_PASSWORD` | `demo` / `demo` | pre-fill the launch form |

## Run statuses

`SUCCESS` — CAPTCHA solved **and** login verified · `FAILED` — automation or
verification error · `BLOCKED` — the CAPTCHA blocked the run · `TIMEOUT` ·
`CANCELLED`. Submitting the form alone is never reported as success.

## Tests

46 offline tests, no Chrome and no network needed:

```sh
pytest
```

## Add your own bot

Drop a file into `app/bots/`, subclass `app.bots.base.Bot` and decorate it with
`@register_bot` — it is auto-discovered and appears in the UI and API. See
`app/bots/arcaptcha_demo_bot.py` for a full example.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Every run ends `BLOCKED` ("Are you robot?") | Your exit IP is flagged (VPN / datacenter). Run from a normal connection. |
| Port 8000 busy | Set `PORT=8001` in `.env` and restart. |
| Chrome tabs crash in Docker | Keep `shm_size: 1gb` in `docker-compose.yml`. |
| Runs queue instead of running together | Raise `MAX_PARALLEL_RUNS` in `.env`. |

---

**Note:** `.env` and `*.db` are git-ignored and must never be committed.
