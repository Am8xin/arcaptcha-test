"""ARCcaptcha demo bot — the first implementation on the platform.

Required scenario (per the hiring challenge):

1. open https://demo.arcaptcha.co/
2. fill the configured username / password
3. click the "I am not a robot" checkbox
4. extract the ``challenge_id`` from widget network traffic and persist it
   *immediately* (even if the run later fails or gets blocked)
5. solve the slider puzzle if one is issued (OpenCV + human-like CDP mouse)
6. submit the login form and **verify** that login actually succeeded
   (HTTP 2xx *and* a success message). Submitting alone is never SUCCESS.

Failure semantics:
* checkbox never reacts            -> BLOCKED  (CAPTCHA blocking)
* challenge_id never observed      -> FAILED   (missing challenge_id)
* puzzle not solved after retries  -> BLOCKED
* login response not 2xx/success   -> FAILED   (form verification unsuccessful)
* no /api/login response at all    -> FAILED   (could not verify)

The browser driver is created through a ``driver_factory`` (default: the
platform Chrome factory), which keeps the bot testable: tests inject a
scripted fake driver instead of a real Chrome.
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
from collections import deque
from time import sleep as _real_sleep, time
from typing import Callable

import cv2
import numpy as np
import requests
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .base import (
    Bot,
    BotContext,
    BotError,
    BotResult,
    CaptchaBlockedError,
    ConfigField,
    MissingChallengeIdError,
)
from .browser import build_driver
from .puzzle_solver import find_gap
from ..registry import register_bot

URL = "https://demo.arcaptcha.co/"
CHECKBOX = "#arc-checkbox"
CHECKBOX_INPUT = "#input-checkbox"
CHECKED_ICON = "#checked-in-the-box"
BLOCKED_MESSAGE = ".arc-widget-error"  # widget's "Blocked! Are you robot?" notice
TOKEN = "#arcaptcha-token"
PUZZLE = ".arc-puzzle"
BACKGROUND_IMAGE = ".arc-puzzle img.tw-object-contain"
PIECE_IMAGE = ".arc-puzzle img.puzzle"
SLIDER = ".arc-puzzle .draggable"
EMAIL_INPUT = "#email"
PASSWORD_INPUT = "#password"
LOGIN_BUTTON = "form button"

CHALLENGE_ID_RE = re.compile(
    r"arcaptcha\.(?:ir|co)[^\"']*?(?:/arcaptcha/api/create|/challenge)[^\"']*?[?&]id=([0-9a-fA-F-]{16,})"
)
LOGIN_URL_SUFFIX = "/api/login"

WAIT_AFTER_LOAD = (1.2, 2.5)
WAIT_AFTER_SOLVE = 3.0
WAIT_AFTER_LOGIN = 2.0
CHALLENGE_REACTION_TIMEOUT = 25  # wait for puzzle OR success after the click
CHALLENGE_ID_TIMEOUT = 15  # wait for the create?id=... request
LOGIN_RESPONSE_TIMEOUT = 15
PUZZLE_ATTEMPTS = 2
REFRESH_RETRIES = 2  # reload-and-retry after an explicit block (bonus)

# Indirection so tests can make the bot run instantly.
_sleep = _real_sleep


# --------------------------------------------------------------------------- #
# Performance-log watcher
# --------------------------------------------------------------------------- #

class PerformanceWatcher:
    """Drains the CDP performance log and exposes challenge/login events.

    The ARCcaptcha widget requests ``api.arcaptcha.ir/arcaptcha/api/create
    ?id=<uuid>&site_key=...`` when the checkbox is clicked; that ``id`` is the
    challenge_id. The same value reappears in responses of the bowman
    endpoints, which we use as a fallback extraction path.
    """

    def __init__(self, driver) -> None:
        self._driver = driver
        self._buffer: deque[dict] = deque(maxlen=2000)
        self.challenge_id: str | None = None

    def drain(self) -> None:
        try:
            entries = self._driver.get_log("performance")
        except WebDriverException:
            return
        for entry in entries:
            try:
                message = json.loads(entry["message"])["message"]
            except (ValueError, KeyError, TypeError):
                continue
            self._buffer.append(message)

    def poll_challenge_id(self) -> str | None:
        """Scan buffered traffic for the challenge_id; returns it once found."""
        self.drain()
        if self.challenge_id:
            return self.challenge_id
        for message in self._buffer:
            if message.get("method") != "Network.requestWillBeSent":
                continue
            url = message.get("params", {}).get("request", {}).get("url", "")
            match = CHALLENGE_ID_RE.search(url)
            if match:
                self.challenge_id = match.group(1)
                return self.challenge_id
            if "bowman.arcaptcha" in url and "u=" in url:
                match = re.search(r"[?&]u=([0-9a-fA-F-]{16,})", url)
                if match:
                    self.challenge_id = match.group(1)
                    return self.challenge_id
        for message in self._buffer:
            if message.get("method") != "Network.responseReceived":
                continue
            url = message.get("params", {}).get("response", {}).get("url", "")
            if "bowman.arcaptcha" in url:
                match = re.search(r'"u"\s*:\s*"([0-9a-fA-F-]{16,})"', self._body_of(message))
                if match:
                    self.challenge_id = match.group(1)
                    return self.challenge_id
        return None

    def wait_for_login_response(self, timeout: float) -> dict | None:
        """Return the /api/login Network.responseReceived params, or None."""
        deadline = time() + timeout
        while time() < deadline:
            self.drain()
            for message in reversed(self._buffer):
                if message.get("method") != "Network.responseReceived":
                    continue
                params = message["params"]
                if params.get("response", {}).get("url", "").endswith(LOGIN_URL_SUFFIX):
                    return params
            _sleep(0.2)
        return None

    def _body_of(self, message: dict) -> str:
        request_id = message.get("params", {}).get("requestId")
        if not request_id:
            return ""
        try:
            result = self._driver.execute_cdp_cmd(
                "Network.getResponseBody", {"requestId": request_id}
            )
        except WebDriverException:
            return ""
        return (result.get("body") or "")[:4000]


# --------------------------------------------------------------------------- #
# Human-like mouse helpers (CDP Input domain)
# --------------------------------------------------------------------------- #

_cursor = [0.0, 0.0]


def dispatch_mouse(driver, x, y, event, button="none", buttons=0):
    driver.execute_cdp_cmd(
        "Input.dispatchMouseEvent",
        {
            "type": event,
            "x": round(float(x), 2),
            "y": round(float(y), 2),
            "button": button,
            "buttons": buttons,
            "clickCount": 1,
        },
    )


def move_mouse(driver, x, y, steps=10, jitter=0.0):
    start_x, start_y = _cursor
    for step in range(1, steps + 1):
        progress = step / steps
        dispatch_mouse(
            driver,
            start_x + (x - start_x) * progress + random.uniform(-jitter, jitter),
            start_y + (y - start_y) * progress + random.uniform(-jitter, jitter),
            "mouseMoved",
        )
        _sleep(random.uniform(0.008, 0.018))
    _cursor[:] = [x, y]


def element_rect(driver, element) -> dict | None:
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element
    )
    return driver.execute_script(
        """const r = arguments[0].getBoundingClientRect();
           return {x: r.x, y: r.y, width: r.width, height: r.height};""",
        element,
    )


def human_click(driver, element):
    rect = element_rect(driver, element)
    if not rect:
        raise RuntimeError("Element has no bounding box")

    x = rect["x"] + rect["width"] / 2
    y = rect["y"] + rect["height"] / 2
    move_mouse(
        driver,
        x - random.uniform(60, 120),
        y - random.uniform(40, 90),
        steps=random.randint(6, 10),
        jitter=1.5,
    )
    move_mouse(driver, x, y, steps=random.randint(8, 14))
    dispatch_mouse(driver, x, y, "mousePressed", button="left", buttons=1)
    _sleep(random.uniform(0.09, 0.16))
    dispatch_mouse(driver, x, y, "mouseReleased", button="left", buttons=0)


def drag_slider(driver, slider_box: dict, distance: float) -> None:
    start_x = slider_box["x"] + slider_box["width"] / 2
    start_y = slider_box["y"] + slider_box["height"] / 2

    move_mouse(
        driver,
        start_x - random.uniform(20, 60),
        start_y + random.uniform(-15, 15),
        steps=6,
        jitter=1.5,
    )
    move_mouse(driver, start_x, start_y, steps=random.randint(6, 12))
    dispatch_mouse(driver, start_x, start_y, "mousePressed", button="left", buttons=1)
    _sleep(random.uniform(0.06, 0.12))

    steps = max(20, int(abs(distance) / 6))
    for step in range(1, steps + 1):
        progress = 1 - (1 - step / steps) ** 3
        dispatch_mouse(
            driver,
            start_x + distance * progress,
            start_y + random.uniform(-1.0, 1.0),
            "mouseMoved",
            button="left",
            buttons=1,
        )
        _sleep(random.uniform(0.008, 0.018))

    _sleep(random.uniform(0.08, 0.16))
    dispatch_mouse(
        driver, start_x + distance, start_y, "mouseReleased", button="left", buttons=0
    )


# --------------------------------------------------------------------------- #
# Page helpers
# --------------------------------------------------------------------------- #

def wait_visible(driver, selector: str, timeout: float = 20):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
    )


def is_visible(driver, selector: str) -> bool:
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        return bool(elements) and elements[0].is_displayed()
    except StaleElementReferenceException:
        # The widget re-rendered mid-check; the caller's polling loop will
        # retry with a fresh element on its next iteration.
        return False


def decode_image(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise BotError("Could not decode an image from the page")
    return image


def fetch_image(driver, url: str) -> np.ndarray:
    """Download an image with the browser's own fetch() to keep its fingerprint."""
    data_url = driver.execute_async_script(
        """
        const url = arguments[0];
        const done = arguments[arguments.length - 1];
        fetch(url, {credentials: 'include'})
          .then(r => r.arrayBuffer())
          .then(buf => {
            const bytes = new Uint8Array(buf);
            let bin = '';
            for (let i = 0; i < bytes.length; i += 8192) {
              bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
            }
            done('data:image/png;base64,' + btoa(bin));
          })
          .catch(() => done(null));
        """,
        url,
    )
    if data_url:
        return decode_image(base64.b64decode(data_url.split(",", 1)[1]))

    cookies = {cookie["name"]: cookie["value"] for cookie in driver.get_cookies()}
    response = requests.get(
        url,
        cookies=cookies,
        headers={
            "Referer": driver.current_url,
            "User-Agent": driver.execute_script("return navigator.userAgent"),
        },
        timeout=30,
    )
    if not response.ok:
        raise BotError(f"Image request failed for {url}: HTTP {response.status_code}")
    return decode_image(response.content)


def wait_for_puzzle(driver):
    wait_visible(driver, PUZZLE, timeout=CHALLENGE_REACTION_TIMEOUT)
    WebDriverWait(driver, 20).until(
        lambda d: d.execute_script(
            """const bg = document.querySelector(arguments[0]);
               const img = document.querySelector(arguments[1]);
               return Boolean(bg && img && bg.complete && img.complete
                   && bg.naturalWidth > 0 && img.naturalWidth > 0);""",
            BACKGROUND_IMAGE,
            PIECE_IMAGE,
        )
    )


def read_puzzle(driver) -> dict:
    background = wait_visible(driver, BACKGROUND_IMAGE)
    piece = driver.find_element(By.CSS_SELECTOR, PIECE_IMAGE)
    slider = driver.find_element(By.CSS_SELECTOR, SLIDER)

    background_box = element_rect(driver, background)
    piece_box = element_rect(driver, piece)
    slider_box = element_rect(driver, slider)
    if not background_box or not piece_box or not slider_box:
        raise BotError("Could not read the puzzle layout")

    natural_width = driver.execute_script("return arguments[0].naturalWidth", background)
    scale = background_box["width"] / natural_width
    if scale <= 0:
        raise BotError("Invalid background scale")

    background_url = background.get_attribute("src")
    piece_url = piece.get_attribute("src")
    if not background_url or not piece_url:
        raise BotError("Could not read the puzzle image URLs")

    return {
        "background_url": background_url,
        "piece_url": piece_url,
        "piece_box": piece_box,
        "slider_box": slider_box,
        "scale": scale,
        "piece_top": round((piece_box["y"] - background_box["y"]) / scale),
        "piece_left": piece_box["x"] - background_box["x"],
    }


def solve_puzzle(driver) -> None:
    puzzle = read_puzzle(driver)
    background = fetch_image(driver, puzzle["background_url"])
    piece = fetch_image(driver, puzzle["piece_url"])

    expected_width = round(puzzle["piece_box"]["width"] / puzzle["scale"])
    expected_height = round(puzzle["piece_box"]["height"] / puzzle["scale"])
    if (piece.shape[1], piece.shape[0]) != (expected_width, expected_height):
        piece = cv2.resize(
            piece,
            (expected_width, expected_height),
            interpolation=cv2.INTER_AREA,
        )

    gap = find_gap(background, piece, y_hint=puzzle["piece_top"])
    distance = gap.x * puzzle["scale"] - puzzle["piece_left"]
    drag_slider(driver, puzzle["slider_box"], distance)


def human_type(element, text: str) -> None:
    element.click()
    element.send_keys(Keys.CONTROL, "a")
    element.send_keys(Keys.BACKSPACE)
    for char in text:
        element.send_keys(char)
        _sleep(random.uniform(0.05, 0.14))


def read_login_response_body(driver, request_id: str | None) -> str:
    if not request_id:
        return "<no request id>"
    try:
        result = driver.execute_cdp_cmd(
            "Network.getResponseBody", {"requestId": request_id}
        )
    except WebDriverException:
        return "<body unavailable>"
    if result.get("base64Encoded"):
        return "<base64 body>"
    return (result.get("body") or "")[:300]


# --------------------------------------------------------------------------- #
# The bot
# --------------------------------------------------------------------------- #

@register_bot
class ArcaptchaDemoBot(Bot):
    name = "arcaptcha-demo"
    title = "ARCaptcha Demo Login"
    description = (
        "Opens demo.arcaptcha.co, clicks the 'I am not a robot' checkbox, captures "
        "the challenge_id, solves the slider puzzle if issued, then submits the "
        "login form and verifies that authentication actually succeeded."
    )
    engine = "selenium + undetected-chromedriver"
    config_fields = (
        ConfigField(
            name="username",
            label="Demo username",
            required=True,
            default=os.environ.get("ARC_USERNAME", "demo"),
            help="Username shown on the demo login form.",
        ),
        ConfigField(
            name="password",
            label="Demo password",
            required=True,
            default=os.environ.get("ARC_PASSWORD", "demo"),
            secret=True,
            help="Password for the demo login form.",
        ),
    )

    def __init__(self, driver_factory: Callable[[bool], object] | None = None) -> None:
        self._driver_factory = driver_factory or (
            lambda headless: build_driver(headless)
        )

    # -- orchestration --------------------------------------------------------

    def run(self, ctx: BotContext) -> BotResult:
        details: dict = {"verified": False, "captcha_solved": False}
        driver = self._driver_factory(ctx.headless)
        ctx.on_cleanup(self._make_cleanup(driver))
        watcher = PerformanceWatcher(driver)
        username = ctx.config.get("username", "demo")
        password = ctx.config.get("password", "demo")

        try:
            ctx.log(f"Opening {URL}")
            driver.get(URL)
            wait_visible(driver, CHECKBOX, timeout=25)
            _sleep(random.uniform(*WAIT_AFTER_LOAD))
            self._guard(ctx)

            puzzle_appeared = self._click_until_reaction(
                driver, watcher, ctx, details, username, password
            )

            challenge_id = watcher.poll_challenge_id()
            if puzzle_appeared and not challenge_id:
                ctx.log("Waiting for the challenge_id in widget traffic")
                self._wait_for_challenge_id(watcher, ctx)
                challenge_id = watcher.challenge_id
            if not challenge_id:
                # Required by the platform spec: a run without a challenge_id
                # can never be reported as a full success.
                raise MissingChallengeIdError(
                    "challenge_id was not observed in widget network traffic"
                )
            ctx.report(challenge_id=challenge_id)
            ctx.log(f"challenge_id captured: {challenge_id}")

            if puzzle_appeared:
                solved = self._solve_with_retries(driver, watcher, ctx)
                if not solved:
                    return BotResult(
                        status="BLOCKED",
                        challenge_id=challenge_id,
                        details=details,
                        error=(
                            f"CAPTCHA challenge was not accepted after "
                            f"{PUZZLE_ATTEMPTS} attempts"
                        ),
                    )
                details["captcha_solved"] = True
            else:
                ctx.log("Checkbox passed without a puzzle")
                details["captcha_solved"] = True

            token = self._read_token(driver)
            details["captcha_token_present"] = bool(token)
            if not token:
                raise BotError("CAPTCHA token is empty after solving")

            _sleep(WAIT_AFTER_SOLVE)

            ctx.log("Submitting the login form")
            verification = self._submit_and_verify(
                driver, watcher, ctx, username, password
            )
            details.update(verification["details"])
            if not verification["ok"]:
                return BotResult(
                    status="FAILED",
                    challenge_id=challenge_id,
                    details=details,
                    error=verification["error"],
                )

            details["verified"] = True
            ctx.log("Login verified successfully")
            return BotResult(
                status="SUCCESS",
                challenge_id=challenge_id,
                details=details,
            )
        except MissingChallengeIdError as exc:
            return BotResult(status="FAILED", details=details, error=str(exc))
        except CaptchaBlockedError as exc:
            return BotResult(
                status="BLOCKED",
                challenge_id=watcher.challenge_id,
                details=details,
                error=str(exc),
            )
        except BotError as exc:
            return BotResult(status="FAILED", details=details, error=str(exc))
        except TimeoutException as exc:
            return BotResult(
                status="FAILED",
                details=details,
                error=f"Timeout while waiting for a page element: {exc}",
            )
        except Exception as exc:  # unexpected automation errors
            return BotResult(
                status="FAILED",
                details=details,
                error=f"{type(exc).__name__}: {exc}",
            )

    # -- steps -----------------------------------------------------------------

    @staticmethod
    def _make_cleanup(driver):
        def _cleanup() -> None:
            try:
                driver.quit()
            except Exception:
                pass
        return _cleanup

    @staticmethod
    def _guard(ctx: BotContext) -> None:
        if ctx.should_cancel():
            raise BotError("Run cancelled by user")

    @staticmethod
    def _fill_credentials(driver, username: str, password: str) -> None:
        email = wait_visible(driver, EMAIL_INPUT, timeout=10)
        human_type(email, username)
        human_type(driver.find_element(By.CSS_SELECTOR, PASSWORD_INPUT), password)

    def _click_until_reaction(
        self,
        driver,
        watcher,
        ctx: BotContext,
        details: dict,
        username: str,
        password: str,
    ) -> bool:
        """Fill the form, click the checkbox, wait for the reaction.

        Two transient-failure recovery paths:
        * the DOM re-renders mid-interaction (stale element) -> one in-place
          reload+retry of the fill+click sequence;
        * the widget reports an explicit block -> follow its own advice
          ('Please refresh the page and try again') and reload+retry, up to
          ``REFRESH_RETRIES`` times (see README 'Reducing CAPTCHA blocking').
        """
        attempts_allowed = REFRESH_RETRIES + 1
        for attempt in range(1, attempts_allowed + 1):
            if attempt > 1:
                ctx.log(
                    f"Blocked; reloading the page and retrying "
                    f"(attempt {attempt}/{attempts_allowed})"
                )
                details["refresh_retries"] = attempt - 1
                # Start the retry as a *fresh* visitor: drop the session the
                # widget just flagged, then pause like a human who read the
                # 'please refresh' message. Retrying instantly with the same
                # cookies reproduced the same block (same challenge_id) every
                # time.
                try:
                    driver.delete_all_cookies()
                except Exception:  # driver-specific quirks must not break the retry
                    pass
                _sleep(random.uniform(2.5, 5.0))
                driver.get(URL)
            wait_visible(driver, CHECKBOX, timeout=25)
            if attempt > 1:
                _sleep(random.uniform(*WAIT_AFTER_LOAD))
            self._guard(ctx)
            try:
                ctx.log(f"Filling credentials (username={username!r})")
                self._fill_credentials(driver, username, password)
                ctx.log("Clicking the ARCcaptcha checkbox")
                human_click(
                    driver, driver.find_element(By.CSS_SELECTOR, CHECKBOX_INPUT)
                )
            except StaleElementReferenceException:
                if attempt == attempts_allowed:
                    raise
                ctx.log("Page re-rendered mid-interaction; reloading and retrying")
                driver.get(URL)
                wait_visible(driver, CHECKBOX, timeout=25)
                _sleep(random.uniform(*WAIT_AFTER_LOAD))
                self._fill_credentials(driver, username, password)
                human_click(
                    driver, driver.find_element(By.CSS_SELECTOR, CHECKBOX_INPUT)
                )
            ctx.log("Waiting for the challenge reaction")
            try:
                return self._wait_for_reaction(driver, watcher, ctx)
            except CaptchaBlockedError:
                if attempt == attempts_allowed:
                    raise
                watcher.drain()  # keep earlier traffic for id extraction
        raise CaptchaBlockedError("unreachable")

    def _wait_for_reaction(self, driver, watcher, ctx: BotContext) -> bool:
        """Wait until the widget reacts: puzzle, success, error, or nothing."""
        deadline = time() + CHALLENGE_REACTION_TIMEOUT
        reported = False
        while time() < deadline:
            self._guard(ctx)
            watcher.drain()
            if not reported:
                found = watcher.poll_challenge_id()
                if found:
                    ctx.report(challenge_id=found)
                    ctx.log(f"challenge_id captured: {found}")
                    reported = True
            if is_visible(driver, BLOCKED_MESSAGE):
                raise CaptchaBlockedError(
                    "CAPTCHA widget explicitly blocked the run "
                    "('Blocked! Are you robot?' message visible)"
                )
            if is_visible(driver, CHECKED_ICON):
                return False  # passed without a puzzle
            if is_visible(driver, PUZZLE):
                return True  # puzzle issued
            _sleep(0.25)
        raise CaptchaBlockedError(
            "CAPTCHA did not react to the checkbox click (no puzzle, no pass) "
            "- run classified as BLOCKED"
        )

    def _wait_for_challenge_id(self, watcher, ctx: BotContext) -> None:
        deadline = time() + CHALLENGE_ID_TIMEOUT
        while time() < deadline and not ctx.should_cancel():
            if watcher.poll_challenge_id():
                ctx.report(challenge_id=watcher.challenge_id)
                ctx.log(f"challenge_id captured: {watcher.challenge_id}")
                return
            _sleep(0.25)

    def _solve_with_retries(self, driver, watcher, ctx: BotContext) -> bool:
        for attempt in range(1, PUZZLE_ATTEMPTS + 1):
            self._guard(ctx)
            ctx.log(f"Solving puzzle (attempt {attempt}/{PUZZLE_ATTEMPTS})")
            try:
                solve_puzzle(driver)
            except (BotError, StaleElementReferenceException) as exc:
                ctx.log(f"Puzzle solve error: {exc}")
            _sleep(WAIT_AFTER_SOLVE)
            if is_visible(driver, CHECKED_ICON):
                return True
            if is_visible(driver, BLOCKED_MESSAGE):
                ctx.log("Widget reported blocking after the solve attempt")
                return False
            ctx.log("Puzzle attempt rejected; checking for a fresh challenge")
            try:
                wait_for_puzzle(driver)
            except TimeoutException:
                # No new puzzle appeared: either passed or blocked.
                return is_visible(driver, CHECKED_ICON)
        return False

    @staticmethod
    def _read_token(driver) -> str:
        try:
            tokens = driver.find_elements(By.CSS_SELECTOR, TOKEN)
            return (tokens[0].get_attribute("value") or "") if tokens else ""
        except StaleElementReferenceException:
            return ""

    def _submit_and_verify(self, driver, watcher, ctx, username: str, password: str) -> dict:
        params = watcher.wait_for_login_response(0.01)  # drain leftovers first
        self._ensure_credentials(driver, username, password, ctx)
        human_click(driver, driver.find_element(By.CSS_SELECTOR, LOGIN_BUTTON))
        params = watcher.wait_for_login_response(LOGIN_RESPONSE_TIMEOUT)

        details: dict = {"login_response_observed": params is not None}
        if params is None:
            return {
                "ok": False,
                "details": details,
                "error": (
                    "Form verification unsuccessful: no /api/login response was "
                    "observed within "
                    f"{LOGIN_RESPONSE_TIMEOUT}s"
                ),
            }

        status = params.get("response", {}).get("status")
        body = read_login_response_body(driver, params.get("requestId"))
        details["login_http_status"] = status
        details["login_response"] = body[:200]

        if self._login_accepted(status, body):
            return {"ok": True, "details": details, "error": None}

        # Observed live: the widget re-renders the login form when the token
        # lands, wiping the typed credentials -> HTTP 400 "Please fill
        # username and password". Re-type once and submit again.
        body_l = (body or "").lower()
        if status == 400 and "fill username" in body_l:
            ctx.log("Login rejected with empty credentials; re-typing and retrying once")
            self._ensure_credentials(driver, username, password, ctx)
            human_click(driver, driver.find_element(By.CSS_SELECTOR, LOGIN_BUTTON))
            params = watcher.wait_for_login_response(LOGIN_RESPONSE_TIMEOUT)
            if params is not None:
                status = params.get("response", {}).get("status")
                body = read_login_response_body(driver, params.get("requestId"))
                details["login_http_status"] = status
                details["login_response"] = body[:200]
                if self._login_accepted(status, body):
                    return {"ok": True, "details": details, "error": None}

        return {
            "ok": False,
            "details": details,
            "error": (
                f"Form verification unsuccessful: /api/login returned HTTP "
                f"{status}: {body[:150]}"
            ),
        }

    @staticmethod
    def _login_accepted(status, body: str) -> bool:
        """True only when the HTTP status is 2xx AND the body says success."""
        body_l = (body or "").lower()
        success_msg = (
            "logged in successfully" in body_l
            or '"success": true' in body_l.replace(" ", " ")
            or "'success': true" in body_l
        )
        return bool(status and 200 <= status < 300 and success_msg)

    @staticmethod
    def _ensure_credentials(driver, username: str, password: str, ctx=None) -> None:
        """Re-type any credential the form lost.

        The widget re-renders the page when the challenge token lands; typed
        values can disappear silently, and submitting an empty form answers
        HTTP 400 'Please fill username and password'. Filling only what is
        actually missing keeps the common path untouched.
        """
        try:
            email = driver.find_element(By.CSS_SELECTOR, EMAIL_INPUT)
            pwd = driver.find_element(By.CSS_SELECTOR, PASSWORD_INPUT)
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            return
        retyped: list[str] = []
        try:
            if (email.get_attribute("value") or "") != username:
                retyped.append("username")
                human_type(email, username)
            if (pwd.get_attribute("value") or "") != password:
                retyped.append("password")
                human_type(pwd, password)
        except (StaleElementReferenceException, WebDriverException):
            return
        if retyped and ctx is not None:
            ctx.log(
                "Re-typed " + "+".join(retyped)
                + " (the form was re-rendered while the token landed)"
            )
