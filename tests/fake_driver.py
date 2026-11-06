"""A scripted fake Selenium driver that emulates demo.arcaptcha.co offline.

``FakeSite`` implements a small state machine driven by the same CDP mouse
events the real bot sends (``Input.dispatchMouseEvent``) and by the same
performance-log entries the real widget produces
(``api.arcaptcha.ir/arcaptcha/api/create?id=<uuid>``, ``/api/login`` …).

This lets the failure-mode tests run the REAL bot code end-to-end (selectors,
challenge_id extraction, OpenCV solving, login verification) without Chrome
and without network.
"""

from __future__ import annotations

import base64
import json
import uuid

import cv2
import numpy as np

# ---------------------------------------------------------------- geometry ---
# Screen layout (matches the selector map of the demo page):
BG_RECT = {"x": 100, "y": 100, "width": 310, "height": 180}   # 620x360 @ 0.5 scale
PIECE_RECT = {"x": 120, "y": 150, "width": 45, "height": 45}  # piece_left=20px
SLIDER_RECT = {"x": 100, "y": 470, "width": 310, "height": 30}
CHECKBOX_RECT = {"x": 60, "y": 60, "width": 30, "height": 30}
LOGIN_RECT = {"x": 100, "y": 560, "width": 310, "height": 36}
NATURAL_WIDTH = 620
SCALE = 0.5
GAP_NATURAL = (300, 100)  # gap top-left in natural image coordinates
PIECE_LEFT_SCREEN = PIECE_RECT["x"] - BG_RECT["x"]  # 20


def _rect_center(rect):
    return rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2


def _in_rect(x, y, rect):
    return rect["x"] <= x <= rect["x"] + rect["width"] and rect["y"] <= y <= rect["y"] + rect["height"]


def _encode_png(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", image)
    assert ok
    return buf.tobytes()


def make_puzzle_images():
    """Synthetic background (with a dark gap) and matching RGBA piece.

    The piece keeps a transparent margin so its alpha outline is detectable
    (a fully-opaque canvas has no morphological gradient). The opaque 70x70
    region aligns with the gap at natural (300, 100).
    """
    rng = np.random.default_rng(7)
    bg = np.full((360, 620, 3), 205, np.uint8)
    bg += rng.integers(-6, 7, size=bg.shape, dtype=np.int16).astype(np.uint8)
    bg[100:170, 300:370] = 55  # dark gap -> strong Canny edges at its border
    piece = np.zeros((90, 90, 4), np.uint8)
    piece[0:70, 0:70, :3] = 55
    piece[0:70, 0:70, 3] = 255
    return bg, piece


class FakeElement:
    def __init__(self, env, selector, rect=None, attrs=None, natural_width=0):
        self.env = env
        self.selector = selector
        self.rect = rect or {"x": 10, "y": 10, "width": 100, "height": 30}
        self.attrs = attrs or {}
        self.natural_width = natural_width
        self.typed = []

    # selenium element API used by the bot
    def is_displayed(self):
        return True

    def get_attribute(self, name):
        return self.attrs.get(name)

    def click(self):
        pass

    def send_keys(self, *keys):
        self.typed.extend(k for k in keys if hasattr(k, "upper") or k is not None)


class FakeSite:
    """The state machine + fake network behind the fake driver."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.bg_img, self.piece_img = make_puzzle_images()
        self.bg_png = _encode_png(self.bg_img)
        self.piece_png = _encode_png(self.piece_img)
        self.bg_url = "https://api.arcaptcha.ir/puzzle/bg-1.png"
        self.piece_url = "https://api.arcaptcha.ir/puzzle/piece-1.png"

        self.page_loaded = False
        self.puzzle_visible = False
        self.checked_visible = False
        self.blocked_visible = False
        self.token = ""
        self.challenge_ids: list[str] = []
        self.pending_log: list[dict] = []
        self.response_bodies: dict[str, dict] = {}
        self.request_counter = 0
        self.closed = False
        self.cookies_cleared = 0

        # drag bookkeeping
        self.dragging = False
        self.drag_start_x = 0.0
        self.drag_last_x = 0.0
        self.solve_attempts = 0
        self.reject_first_solve = scenario == "puzzle_retry_then_pass"

        self.login_calls = 0
        self.load_count = 0

    # -- performance log helpers ---------------------------------------------
    def _next_rid(self) -> str:
        self.request_counter += 1
        return f"rid-{self.request_counter}"

    def push_request(self, url: str) -> None:
        rid = self._next_rid()
        self.pending_log.append(
            {"message": json.dumps({"message": {
                "method": "Network.requestWillBeSent",
                "params": {"requestId": rid, "request": {"url": url}},
            }})}
        )

    def push_response(self, url: str, status: int, body: str) -> None:
        rid = self._next_rid()
        self.pending_log.append(
            {"message": json.dumps({"message": {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": rid,
                    "response": {"url": url, "status": status},
                },
            }})}
        )
        self.response_bodies[rid] = {"body": body}

    def push_challenge_created(self) -> str:
        challenge_id = str(uuid.uuid4())
        self.challenge_ids.append(challenge_id)
        self.push_request(
            f"https://api.arcaptcha.ir/arcaptcha/api/create?id={challenge_id}"
            f"&site_key=d6ggpvy684"
        )
        return challenge_id

    # -- transitions -----------------------------------------------------------
    def on_checkbox_click(self) -> None:
        if self.scenario == "blocked":
            # widget issues the challenge server-side, then reports blocking
            self.push_challenge_created()
            self.blocked_visible = True
            return
        if self.scenario == "blocked_then_pass" and self.load_count <= 1:
            # first page load: blocked; the bot is expected to reload and retry
            self.push_challenge_created()
            self.blocked_visible = True
            return
        if self.scenario != "missing_challenge_id":
            self.push_challenge_created()
        if self.scenario in ("pass_no_puzzle", "missing_challenge_id"):
            if self.scenario == "pass_no_puzzle":
                self.checked_visible = True
                self.token = "tok-no-puzzle"
            else:
                # puzzle issued without any trackable challenge id
                self.puzzle_visible = True
            return
        # success / login_rejected / no_login_response /
        # puzzle_retry_then_pass: a puzzle is issued
        self.puzzle_visible = True

    def on_slider_release(self, moved: float) -> None:
        needed = GAP_NATURAL[0] * SCALE - PIECE_LEFT_SCREEN  # 130 px
        solved = moved >= needed * 0.9
        if solved and self.reject_first_solve:
            self.reject_first_solve = False
            # widget rejects and re-issues a fresh challenge
            self.push_challenge_created()
            self.solve_attempts += 1
            return
        if solved:
            self.solve_attempts += 1
            self.puzzle_visible = False
            self.checked_visible = True
            self.token = "tok-solved"

    def on_login_click(self) -> None:
        self.login_calls += 1
        if self.scenario == "no_login_response":
            return
        if self.scenario == "login_rejected":
            self.push_response(
                "https://demo.arcaptcha.co/api/login", 401,
                json.dumps({"msg": "Captcha is not valid"}),
            )
        else:
            self.push_response(
                "https://demo.arcaptcha.co/api/login", 200,
                json.dumps({"msg": "You are logged in successfully!"}),
            )


class FakeDriver:
    """Just enough of the Selenium WebDriver API for the demo bot."""

    def __init__(self, scenario: str = "success"):
        self.site = FakeSite(scenario)
        self.current_url = "https://demo.arcaptcha.co/"
        self._user_agent = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
        )

    # -- navigation -------------------------------------------------------------
    def get(self, url):
        self.site.page_loaded = True
        self.site.load_count += 1
        self.site.blocked_visible = False  # a fresh page load clears the notice

    def quit(self):
        self.site.closed = True

    def get_cookies(self):
        return []

    def delete_all_cookies(self):
        self.site.cookies_cleared = (self.site.cookies_cleared or 0) + 1

    # -- element lookup -----------------------------------------------------------
    def find_element(self, by, selector):
        el = self._resolve(selector)
        if el is None:
            from selenium.common.exceptions import NoSuchElementException

            raise NoSuchElementException(f"no element for {selector} (state={self._state()})")
        return el

    def find_elements(self, by, selector):
        el = self._resolve(selector)
        return [el] if el is not None else []

    def _state(self) -> str:
        s = self.site
        if not s.page_loaded:
            return "loading"
        if s.puzzle_visible:
            return "puzzle"
        if s.checked_visible:
            return "checked"
        return "form"

    def _resolve(self, selector) -> FakeElement | None:
        s = self.site
        state = self._state()
        if state == "loading":
            if selector in ("#arc-checkbox", "#input-checkbox") and s.page_loaded:
                return FakeElement(s, selector, rect=CHECKBOX_RECT)
            return None
        if selector in ("#arc-checkbox", "#input-checkbox"):
            return FakeElement(s, selector, rect=CHECKBOX_RECT)
        if selector == "#email":
            return FakeElement(s, selector, attrs={"placeholder": "email"})
        if selector == "#password":
            return FakeElement(s, selector)
        if selector == "form button":
            return FakeElement(s, selector, rect=LOGIN_RECT)
        if selector == "#arcaptcha-token":
            return FakeElement(s, selector, attrs={"value": s.token})
        if state == "puzzle":
            if selector == ".arc-puzzle":
                return FakeElement(s, selector, rect=BG_RECT)
            if selector == ".arc-puzzle img.tw-object-contain":
                return FakeElement(s, selector, rect=BG_RECT,
                                   attrs={"src": s.bg_url}, natural_width=NATURAL_WIDTH)
            if selector == ".arc-puzzle img.puzzle":
                return FakeElement(s, selector, rect=PIECE_RECT,
                                   attrs={"src": s.piece_url})
            if selector == ".arc-puzzle .draggable":
                return FakeElement(s, selector, rect=SLIDER_RECT)
        if state == "checked" and selector == "#checked-in-the-box":
            return FakeElement(s, selector, rect=CHECKBOX_RECT)
        if state != "loading" and selector == ".arc-widget-error":
            return FakeElement(s, selector) if s.blocked_visible else None
        return None

    # -- scripts -----------------------------------------------------------------
    def execute_script(self, script, *args):
        if "scrollIntoView" in script:
            return None
        if "document.querySelector" in script and "complete" in script:
            # puzzle-images completeness probe (mentions naturalWidth too,
            # so it must be checked first)
            return self.site.puzzle_visible
        if "getBoundingClientRect" in script:
            return dict(args[0].rect)
        if "naturalWidth" in script:
            return args[0].natural_width
        if "navigator.userAgent" in script:
            return self._user_agent
        raise AssertionError(f"unexpected script: {script[:80]!r}")

    def execute_async_script(self, script, *args):
        url = args[0]
        s = self.site
        if url == s.bg_url:
            png = s.bg_png
        elif url == s.piece_url:
            png = s.piece_png
        else:
            return None
        return "data:image/png;base64," + base64.b64encode(png).decode()

    # -- CDP -----------------------------------------------------------------------
    def execute_cdp_cmd(self, command: str, params: dict):
        if command == "Input.dispatchMouseEvent":
            return self._mouse(params)
        if command == "Network.getResponseBody":
            body = self.site.response_bodies.get(params["requestId"])
            if body is None:
                from selenium.common.exceptions import WebDriverException

                raise WebDriverException("no cached body")
            return dict(body)
        if command == "Page.addScriptToEvaluateOnNewDocument":
            return {}
        raise AssertionError(f"unexpected CDP command: {command}")

    def _mouse(self, params) -> None:
        event = params.get("type")
        x, y = params.get("x", 0), params.get("y", 0)
        buttons = params.get("buttons", 0)
        s = self.site

        if event == "mousePressed" and _in_rect(x, y, SLIDER_RECT):
            s.dragging = True
            s.drag_start_x = x
            s.drag_last_x = x
        elif event == "mouseMoved" and s.dragging and buttons == 1:
            s.drag_last_x = x
        elif event == "mouseReleased":
            if s.dragging:
                s.dragging = False
                s.on_slider_release(s.drag_last_x - s.drag_start_x)
            elif _in_rect(x, y, CHECKBOX_RECT):
                s.on_checkbox_click()
            elif _in_rect(x, y, LOGIN_RECT):
                s.on_login_click()

    # -- logs -----------------------------------------------------------------------
    def get_log(self, log_type):
        assert log_type == "performance"
        entries, self.site.pending_log = self.site.pending_log, []
        return entries
