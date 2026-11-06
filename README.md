# ARCaptcha Browser Bot Platform

An extensible web platform for defining, running, and monitoring browser bots.
The first bot solves the live [ARCaptcha demo](https://demo.arcaptcha.co/) flow
with **Selenium**: click the *I am not a robot* checkbox, capture the
`challenge_id`, solve the slider puzzle if one is issued, submit the login form
and **verify** that authentication actually succeeded.

Everything the platform does is recorded in a persistent run history —
including failed and blocked attempts — and the whole stack ships as a single
`docker compose up`.

---

## Table of contents

1. [Requirements run scenario](#requirements-run-scenario)
2. [Architecture](#architecture)
3. [Quick start (Docker, clean environment)](#quick-start-docker-clean-environment)
4. [Running without Docker](#running-without-docker)
5. [Configuration](#configuration)
6. [Using the platform (Web UI)](#using-the-platform-web-ui)
7. [API reference](#api-reference)
8. [Browser modes: headless and headful](#browser-modes-headless-and-headful)
9. [Data persistence](#data-persistence)
10. [Adding a new bot](#adding-a-new-bot)
11. [Tests](#tests)
12. [Reducing CAPTCHA blocking (bonus) + evidence](#reducing-captcha-blocking-bonus--evidence)
13. [Troubleshooting](#troubleshooting)
14. [Supported platforms](#supported-platforms)

---

## Requirements run scenario

The `arcaptcha-demo` bot performs exactly the scenario required by the
challenge:

| Step | What happens | Evidence recorded |
| --- | --- | --- |
| 1 | Open `https://demo.arcaptcha.co/` | run log |
| 2 | Fill the configured username / password | config (password masked) |
| 3 | Click the *I am not a robot* checkbox (human-like CDP mouse) | run log |
| 4 | **Extract `challenge_id`** from widget network traffic (`api.arcaptcha.ir/arcaptcha/api/create?id=<uuid>`) | `runs.challenge_id`, persisted **immediately**, even if the run later fails or is blocked |
| 5 | Solve the slider puzzle if issued (OpenCV gap detection + eased drag) | `details.captcha_solved` |
| 6 | Submit the login form | `details.login_http_status`, `details.login_response` |
| 7 | **Verify** login success (HTTP 2xx **and** success message) | `details.verified` |

Submitting the form alone is **never** reported as success. Final statuses:

* `SUCCESS` — checkbox passed, token present, `/api/login` returned 2xx with a success message.
* `FAILED` — automation/verification failure (missing `challenge_id`, unsuccessful form verification, timeouts, unexpected errors).
* `BLOCKED` — the CAPTCHA blocked the run (widget's explicit *"Blocked! Are you robot?"* notice, no reaction, or challenge not accepted after retries).
* `TIMEOUT` — run exceeded `RUN_TIMEOUT_SECONDS`.
* `CANCELLED` — cancelled from the UI/API.

## Architecture

Bot implementations are fully decoupled from the platform core. The core
never imports a concrete bot — everything goes through the registry:

```
┌────────────────────────────  interfaces  ───────────────────────────┐
│  Web UI (Jinja2 + vanilla JS)          REST API (FastAPI, /api/*)   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────  core  ────────────────┐
│  app.registry    discovery/registration of bots                     │
│  app.runner      execution: threads, lifecycle, timeouts, cancel    │
│  app.db          storage: SQLAlchemy Run model (persistent history) │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ Bot interface (app.bots.base.Bot)
┌───────────────────────────────▼────────────  implementations  ──────┐
│  app.bots.arcaptcha_demo_bot   Selenium + undetected-chromedriver   │
│  app.bots.puzzle_solver        OpenCV gap detection                 │
│  app.bots.browser              shared Chrome factory                │
│  (your bot goes here — drop a file, decorate, done)                 │
└─────────────────────────────────────────────────────────────────────┘
```

Run lifecycle: `PENDING → RUNNING → SUCCESS | FAILED | BLOCKED | TIMEOUT | CANCELLED`.

## Quick start (Docker, clean environment)

Prerequisites: [Docker](https://docs.docker.com/engine/install/) with the
Compose plugin (Docker Engine ≥ 24 / Docker Desktop). No other tooling needed.

```sh
git clone https://github.com/Am8xin/arcaptcha-test.git
cd arcaptcha-test                      # the platform lives at the repository root
cp .env.example .env                   # optional: defaults work out of the box
docker compose up --build
```

Then open **http://localhost:8000**. First build downloads Chromium and the
Python dependencies; subsequent builds are cached.

> Watch headful runs in the browser: set `START_VNC=true` in `.env` and open
> http://localhost:7900 (noVNC, no password — keep it on a trusted network).

## Running without Docker

Prerequisites: Python **3.11+**, Google Chrome or Chromium installed,
`chromedriver` matching your Chrome version (Selenium Manager downloads one
automatically if `CHROMEDRIVER_PATH` is unset).

```sh
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app                                        # serves http://localhost:8000
```

On Linux servers, headful mode needs an X display; the container path is
easier (Xvfb included). For a bare-metal server, run `Xvfb :99` and export
`DISPLAY=:99` (see [Browser modes](#browser-modes-headless-and-headful)).

## Configuration

All configuration comes from environment variables / the `.env` file — **no
credentials live in the source code**. `.env` is git-ignored;
`.env.example` is the committed sample.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | HTTP server binding |
| `DATABASE_URL` | `sqlite:///./arcaptcha.db` | Any SQLAlchemy URL (SQLite file, PostgreSQL, …) |
| `MAX_PARALLEL_RUNS` | `2` | Runner thread-pool size |
| `RUN_TIMEOUT_SECONDS` | `300` | Watchdog deadline per run |
| `CHROME_BINARY` | auto-detect | Path to Chrome/Chromium (set inside Docker) |
| `CHROMEDRIVER_PATH` | auto (Selenium Manager) | Path to chromedriver |
| `CHROME_NO_SANDBOX` | `false` | Required inside containers |
| `WINDOW_WIDTH` / `WINDOW_HEIGHT` | `1440` / `900` | Browser window size |
| `START_VNC` | `false` | Start x11vnc + noVNC in the container |
| `ARC_USERNAME` / `ARC_PASSWORD` | `demo` / `demo` | Pre-fill the bot's config form (per-run values override) |

The demo credentials are the public sample ones printed on the demo page
(`demo` / `demo`); per-run values can be typed into the launch dialog and are
stored **masked** in the history.

## Using the platform (Web UI)

1. **Bots page** (`/`) lists every registered bot with its description and
   engine. Click **Launch run**.
2. In the dialog choose the browser mode **before each run**
   (Headless / Headful), edit the configuration fields (username/password)
   and confirm.
3. You are taken to the **run detail** page: live status badge, verification
   details and the **live log** (auto-refreshes while the run is active).
   Active runs show a **Cancel run** button.
4. **Run history** (`/runs`) shows every run ever executed — run ID, bot,
   mode, status, start time, duration, `challenge_id` and the error for
   failures — filterable by bot and status, auto-refreshing every 3 s.

## API reference

The UI is a thin client of the same REST API (`/docs` has the OpenAPI UI):

| Method & path | Purpose |
| --- | --- |
| `GET /api/bots` | List registered bots + their config fields |
| `POST /api/runs` | Launch: `{"bot": "arcaptcha-demo", "mode": "headless", "config": {}}` → `202 {"run_id": …}` |
| `GET /api/runs?limit=&bot=&status=` | Run history (newest first) |
| `GET /api/runs/{id}` | Full run detail incl. log lines |
| `POST /api/runs/{id}/cancel` | Request cancellation (`202`; `409` if finished) |
| `GET /api/stats` | Counts by status |
| `GET /healthz` | Liveness probe |

Example:

```sh
curl -s localhost:8000/api/bots | jq
RUN=$(curl -s -X POST localhost:8000/api/runs \
      -H 'Content-Type: application/json' \
      -d '{"bot":"arcaptcha-demo","mode":"headless","config":{}}' | jq -r .run_id)
watch -n2 "curl -s localhost:8000/api/runs/$RUN | jq"
```

## Browser modes: headless and headful

The mode is chosen per run in the launch dialog (or the `mode` API field).

**Desktop (local run, `python -m app`):**

* *Headless* — no window; UA is normalised so headless looks like a normal
  Chrome (see [bonus](#reducing-captcha-blocking-bonus--evidence)).
* *Headful* — a real Chrome window opens on your desktop; watch the flow live.

**Server / Docker (no display attached):** the container always starts
`Xvfb :99` (a virtual 1440×900 display). Headful runs render there.

* Set `START_VNC=true` in `.env`, `docker compose up`, then open
  **http://localhost:7900** (noVNC) to watch the browser live.
* The VNC ports are published by compose (`7900`). There is **no password** —
  keep the port firewalled / on a trusted network, or front it with your own
  auth proxy.

Bare-metal server without Docker: `apt install xvfb x11vnc novnc`, then
`Xvfb :99 & x11vnc -display :99 -forever -nopw & websockify --web /usr/share/novnc 7900 localhost:5900`
and export `DISPLAY=:99` before `python -m app`.

## Data persistence

* Every run is a row in the `runs` table (SQLAlchemy). The Docker compose
  file mounts the named volume `run_history` on `/data` and sets
  `DATABASE_URL=sqlite:////data/arcaptcha.db`, so **history survives
  container restarts, rebuilds and image upgrades**.
* Failed/blocked runs are never deleted; `challenge_id` is written as soon as
  the widget issues it, so even blocked runs keep their challenge id.
* Verify after a restart: launch a run, `docker compose restart platform`,
  reload `/runs` — the record is still there (statuses of interrupted runs
  stay as recorded; runs that never finished keep their last known state).
* Swap SQLite for PostgreSQL by pointing `DATABASE_URL` at e.g.
  `postgresql+psycopg://user:pass@db:5432/bots` (add the driver to
  requirements).

## Adding a new bot

The bot contract is one abstract class. Create `app/bots/my_bot.py`:

```python
from app.bots.base import Bot, BotContext, BotResult, ConfigField
from app.registry import register_bot


@register_bot
class MyExampleBot(Bot):
    name = "my-example"                       # unique id, used by the API
    title = "My Example Bot"
    description = "One line shown on the bots page."
    engine = "selenium"                       # automation engine label
    config_fields = (
        ConfigField("target_url", "Target URL", required=True),
        ConfigField("api_key", "API key", secret=True),   # masked in history
    )

    def run(self, ctx: BotContext) -> BotResult:
        ctx.log(f"running with url={ctx.config['target_url']}")
        # ... your automation; report early, fail honestly:
        ctx.report(challenge_id="whatever-id-you-capture")
        if ctx.should_cancel():
            raise RuntimeError("cancelled")
        return BotResult(
            status="SUCCESS",                 # or FAILED / BLOCKED
            challenge_id="whatever-id-you-capture",
            details={"verified": True},
        )
```

That's all: the file is auto-discovered (`pkgutil` scan of `app/bots/` at app
startup), the bot appears in the UI and API, and the runner records its runs.
Any browser engine works — Playwright, DrissionPage, raw CDP — as long as
`run()` returns a `BotResult`. Registering from outside the package is equally
valid: `registry.register(MyBot(), replace=True)`.

Guidelines the platform enforces by convention:

* report `challenge_id` (or any captured id) via `ctx.report(...)` **as soon
  as you have it** — persistence is immediate;
* only return `SUCCESS` when the end-to-end goal truly succeeded;
* register cleanup via `ctx.on_cleanup(driver.quit)` so timeouts free the browser;
* check `ctx.should_cancel()` between steps;
* add tests: a fake driver (see `tests/fake_driver.py`) keeps your bot tests
  offline and fast.

## Tests

37 offline tests cover the required workflow and the meaningful failures. No
Chrome and no network needed — Selenium is faked with a scripted driver
(`tests/fake_driver.py`) that emulates the widget's DOM and network events:

```sh
pytest                                     # offline suite (integration excluded)
```

Coverage map:

| Area | Tests |
| --- | --- |
| Registration mechanism | duplicate/unknown/invalid handling, decorator (`test_registry.py`) |
| Runner lifecycle | SUCCESS/FAILED/BLOCKED/TIMEOUT/CANCELLED, mid-run `challenge_id` persistence, secret masking, cleanup on timeout (`test_runner.py`) |
| REST API | launch, history, filters, 404/409/422, stats, restart persistence (`test_api.py`) |
| Bot failure modes | missing `challenge_id`, CAPTCHA blocking, element timeouts, **unsuccessful form verification** (submitting is not success), no login response, puzzle retry, block→refresh retry (`test_arcaptcha_bot.py`) |

Sample output (`pytest`):

```
............…………………………………………………………....
37 passed, 1 deselected in 3.86s
```

Live-site integration test (needs Chrome + network, skipped by default):

```sh
RUN_INTEGRATION=1 pytest -m integration -v
```

## Reducing CAPTCHA blocking (bonus) + evidence

The demo bot layers several defences, all reproducible from this repo:

1. **undetected-chromedriver** — patches the chromedriver binary so the
   `cdc_` automation hooks and `navigator.webdriver` are gone.
2. **Normalised User-Agent in headless mode** — fingerprinting scripts read
   the UA verbatim; a stock headless run advertises `HeadlessChrome/…` (we
   observed ARCaptcha's fingerprint collector recording exactly that in its
   `bowman` cookie: `"ua": "Chrome Headless_157"`). The platform rewrites the
   UA to the regular Chrome string, making headless and headful identical.
3. **Human-like CDP input** — mouse paths with jitter, ease-out drags, random
   press/release dwell times through `Input.dispatchMouseEvent`, never
   `WebElement.click()`.
4. **In-page image fetch** — puzzle images are downloaded with the page's own
   `fetch()` (same TLS/cookie context) instead of a separate Python client.
5. **Randomised pacing** — humanish delays after load, per keystroke and
   between events.
6. **Respect the widget's advice** — when the widget shows its explicit
   *"Blocked! … Please refresh the page and try again"* notice, the bot
   reloads the page and retries (up to `REFRESH_RETRIES = 2`), which clears
   transient blocks. Every attempt is logged and the final classification
   stays honest (`BLOCKED`).

### Evidence & reproducibility

Measure it in your own environment with:

```sh
python scripts/success_rate.py --runs 10 --mode headless            # stealth stack
python scripts/success_rate.py --runs 5  --mode headless --engine plain
```

It runs the bot through the real runner, prints a per-run table and a
success-rate summary. The measurements below were produced **from this
project's CI sandbox**, which is a **datacenter IP that ARCaptcha
hard-flags** (the widget answers every click with its explicit
*"Blocked! Are you robot?"* message). They therefore demonstrate the
platform's *blocked-run reporting* end-to-end rather than a meaningful
success rate — run the same command from a residential connection to get the
latter:

| Batch | Engine | Runs | SUCCESS | BLOCKED | FAILED | challenge_id captured |
| --- | --- | --- | --- | --- | --- | --- |
| A (before stale-element fix) | stealth | 10 | 0 | 9 | 1 *(bug found: stale element → fixed)* | 10/10 |
| B | plain Selenium | 5 | 0 | 5 | 0 | 5/5 |
| C (after fixes) | stealth | 8 | 0 | 8 | 0 | 8/8 |

Raw per-run records are committed under [`docs/`](docs/)
(`evidence-a-stealth-10runs.txt`, `evidence-b-plain-5runs.txt`,
`evidence-c-stealth-8runs.txt`) so the numbers above are auditable.

What the sandbox numbers *do* prove:

* the widget always issues a `challenge_id` and the platform captures and
  persists it in **31/31 runs** — including every blocked run;
* blocking is detected deterministically (the widget's own error element) and
  reported as `BLOCKED`, never hidden, never mis-reported as success;
* the required honest-reporting rule holds: blocked attempts stay visible in
  the history with full error details.

On an unflagged (e.g. residential Iranian) connection the stealth stack
passes the checkbox and puzzle flow — that is the environment where
`--engine stealth` vs `--engine plain` shows the real success-rate gap, and
the exact same command reproduces the table locally.

## Troubleshooting

| Symptom | Cause & fix |
| --- | --- |
| `unrecognized chrome option: excludeSwitches` | chromedriver ≥ 137 rejects legacy options; this repo already avoids them — make sure you run the bundled `app.bots.browser` factory, not custom options. |
| Chrome crashes / tabs die in Docker | `/dev/shm` too small — compose sets `shm_size: 1gb`; keep it. |
| Every run ends `BLOCKED` with *"Blocked! Are you robot?"* | The exit IP is flagged (datacenter/VPN). Run from a residential connection or accept honest blocked reporting; the bot already retries after a reload. |
| `FAILED: challenge_id was not observed` | The widget never issued a challenge (often a hard block); the run is kept visible with this error by design. |
| Headful run shows nothing on a server | Headful renders into Xvfb inside the container — set `START_VNC=true` and open port 7900 (noVNC). |
| `Scheduler lost the run` style hangs | Runs are bounded by `RUN_TIMEOUT_SECONDS` (default 300); raise it for slow networks. |
| SQLite `database is locked` | Many parallel runs on slow disks — lower `MAX_PARALLEL_RUNS` or move `DATABASE_URL` to PostgreSQL. |
| `undetected-chromedriver` fails to launch | The factory falls back to plain Selenium + stealth CDP automatically (logged as a warning). Check Chrome/chromedriver versions match. |
| Port 8000 busy | `PORT=8001 docker compose up` or change `.env`. |

## Supported platforms

* **Docker images** (Debian bookworm base, Chromium from Debian repos):
  `linux/amd64` and `linux/arm64`.
* **Native runs**: Linux (X11), macOS (Intel/Apple silicon; headful opens a
  real window), Windows 10/11 (headless natively; headful works, VNC flow is
  Linux/Docker oriented).
* The bonus stealth stack is validated on `linux/amd64` with Chrome for
  Testing 157 / undetected-chromedriver 3.5.5; on architectures where
  `undetected-chromedriver` is unavailable the platform falls back to plain
  Selenium + stealth CDP patches automatically.

## Project layout

```
.
├── app/
│   ├── main.py               FastAPI app factory (wires everything)
│   ├── api.py                REST API (/api/*)
│   ├── runner.py             execution layer (threads, timeout, cancel)
│   ├── registry.py           bot registration/discovery
│   ├── db.py                 SQLAlchemy Run model + engine
│   ├── config.py             env-driven settings
│   ├── bots/
│   │   ├── base.py           Bot interface, BotContext/BotResult  ← contract
│   │   ├── browser.py        Chrome factory (uc + fallback)
│   │   ├── arcaptcha_demo_bot.py   the first bot
│   │   └── puzzle_solver.py  OpenCV gap detection
│   └── web/                  templates + static (no build step)
├── tests/                    offline pytest suite + fake Selenium driver
├── scripts/success_rate.py   bonus evidence tool
├── deploy/entrypoint.sh      Xvfb + optional VNC + app
├── Dockerfile                single image (amd64/arm64)
├── docker-compose.yml        volume-persisted history, healthcheck
├── .env.example              sample configuration (no secrets)
└── pytest.ini                integration tests deselected by default
```
