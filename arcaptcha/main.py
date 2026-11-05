"""Open the ARCcaptcha demo with Selenium, solve the puzzle and log in.

The module fills the demo credentials, performs a human-like click on the
checkbox, waits for the puzzle, downloads its images inside the page, finds the
gap, drags the slider to it and submits the login form.

Mouse input goes through the CDP Input domain, image bytes are fetched with the
browser's own fetch() so no Python TLS fingerprint leaks, and the browser is
launched through undetected-chromedriver so chromedriver's cdc_ hooks are gone.
"""

import base64
import json
import random
from time import sleep, time

import cv2
import numpy as np
import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .log import logger, setup_logging
from .solver import find_gap

URL = "https://demo.arcaptcha.co/"
CHECKBOX = "#arc-checkbox"
CHECKBOX_INPUT = "#input-checkbox"
CHECKED_ICON = "#checked-in-the-box"
TOKEN = "#arcaptcha-token"
PUZZLE = ".arc-puzzle"
BACKGROUND_IMAGE = ".arc-puzzle img.tw-object-contain"
PIECE_IMAGE = ".arc-puzzle img.puzzle"
SLIDER = ".arc-puzzle .draggable"
EMAIL_INPUT = "#email"
PASSWORD_INPUT = "#password"
LOGIN_BUTTON = "form button"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo"
WAIT_AFTER_LOAD = (1.2, 2.5)
WAIT_AFTER_SOLVE = 3
WAIT_AFTER_LOGIN = 5

# Virtual cursor position, used to keep mouse movements continuous.
_cursor = [0.0, 0.0]


# --------------------------------------------------------------------------- #
# Browser
# --------------------------------------------------------------------------- #

def build_driver(headless: bool = False, undetected: bool = True):
    """Create a Chrome session with automation fingerprints patched."""
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--lang=en-US")
    if headless:
        options.add_argument("--headless=new")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    if undetected:
        try:
            import undetected_chromedriver as uc
        except ImportError:
            logger.warning("undetected-chromedriver missing; using plain Selenium")
            driver = webdriver.Chrome(options=options)
        else:
            driver = uc.Chrome(options=options, use_subprocess=True)
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_window_size(1440, 900)
    return driver


# --------------------------------------------------------------------------- #
# Mouse helpers (CDP)
# --------------------------------------------------------------------------- #

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
    """Glide the virtual cursor to (x, y) in a straight line."""
    start_x, start_y = _cursor
    for step in range(1, steps + 1):
        progress = step / steps
        dispatch_mouse(
            driver,
            start_x + (x - start_x) * progress + random.uniform(-jitter, jitter),
            start_y + (y - start_y) * progress + random.uniform(-jitter, jitter),
            "mouseMoved",
        )
        sleep(random.uniform(0.008, 0.018))
    _cursor[:] = [x, y]


def element_rect(driver, element):
    """Return the element box in viewport coordinates."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element
    )
    return driver.execute_script(
        """const r = arguments[0].getBoundingClientRect();
           return {x: r.x, y: r.y, width: r.width, height: r.height};""",
        element,
    )


def human_click(driver, element):
    """Click an element with a mouse move and a short press, like a person."""
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
    sleep(random.uniform(0.09, 0.16))
    dispatch_mouse(driver, x, y, "mouseReleased", button="left", buttons=0)


def drag_slider(driver, slider_box, distance):
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
    sleep(random.uniform(0.06, 0.12))

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
        sleep(random.uniform(0.008, 0.018))

    sleep(random.uniform(0.08, 0.16))
    dispatch_mouse(
        driver, start_x + distance, start_y, "mouseReleased", button="left", buttons=0
    )


# --------------------------------------------------------------------------- #
# Page helpers
# --------------------------------------------------------------------------- #

def wait_visible(driver, selector, timeout=20):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
    )


def is_visible(driver, selector):
    elements = driver.find_elements(By.CSS_SELECTOR, selector)
    return bool(elements) and elements[0].is_displayed()


def decode_image(data):
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RuntimeError("Could not decode an image from the page")
    return image


def fetch_image(driver, url):
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

    logger.debug("In-page fetch failed; falling back to requests for {}", url)
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
        raise RuntimeError(
            f"Image request failed for {url}: HTTP {response.status_code}"
        )
    return decode_image(response.content)


def wait_for_puzzle(driver):
    wait_visible(driver, PUZZLE, timeout=20)
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


def read_puzzle(driver):
    background = wait_visible(driver, BACKGROUND_IMAGE)
    piece = driver.find_element(By.CSS_SELECTOR, PIECE_IMAGE)
    slider = driver.find_element(By.CSS_SELECTOR, SLIDER)

    background_box = element_rect(driver, background)
    piece_box = element_rect(driver, piece)
    slider_box = element_rect(driver, slider)
    if not background_box or not piece_box or not slider_box:
        raise RuntimeError("Could not read the puzzle layout")

    natural_width = driver.execute_script(
        "return arguments[0].naturalWidth", background
    )
    scale = background_box["width"] / natural_width
    if scale <= 0:
        raise RuntimeError("Invalid background scale")

    background_url = background.get_attribute("src")
    piece_url = piece.get_attribute("src")
    if not background_url or not piece_url:
        raise RuntimeError("Could not read the puzzle image URLs")

    return {
        "background_url": background_url,
        "piece_url": piece_url,
        "piece_box": piece_box,
        "slider_box": slider_box,
        "scale": scale,
        "piece_top": round((piece_box["y"] - background_box["y"]) / scale),
        "piece_left": piece_box["x"] - background_box["x"],
    }


def solve_puzzle(driver):
    puzzle = read_puzzle(driver)
    logger.debug(
        "Puzzle layout: scale={:.3f}, piece_top={}px, piece_left={:.1f}px",
        puzzle["scale"],
        puzzle["piece_top"],
        puzzle["piece_left"],
    )
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
    logger.info(
        "Gap found at x={}, y={} (score={:.3f}); dragging {:.1f}px",
        gap.x,
        gap.y,
        gap.score,
        distance,
    )

    drag_slider(driver, puzzle["slider_box"], distance)


def human_type(element, text):
    """Type character by character with human-ish gaps."""
    element.click()
    element.send_keys(Keys.CONTROL, "a")
    element.send_keys(Keys.BACKSPACE)
    for char in text:
        element.send_keys(char)
        sleep(random.uniform(0.05, 0.14))


def fill_credentials(driver):
    logger.info("Filling demo credentials (username={!r})", DEMO_USERNAME)
    email = wait_visible(driver, EMAIL_INPUT, timeout=10)
    human_type(email, DEMO_USERNAME)
    human_type(driver.find_element(By.CSS_SELECTOR, PASSWORD_INPUT), DEMO_PASSWORD)


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #

def drain_performance_log(driver):
    try:
        driver.get_log("performance")
    except WebDriverException:
        pass


def wait_for_login_response(driver, timeout=15):
    """Poll the performance log for the /api/login response."""
    deadline = time() + timeout
    while time() < deadline:
        for entry in driver.get_log("performance"):
            try:
                message = json.loads(entry["message"])["message"]
            except (ValueError, KeyError):
                continue
            if message.get("method") != "Network.responseReceived":
                continue
            params = message["params"]
            if params["response"]["url"].endswith("/api/login"):
                return params
        sleep(0.2)
    return None


def read_response_body(driver, request_id):
    try:
        result = driver.execute_cdp_cmd(
            "Network.getResponseBody", {"requestId": request_id}
        )
    except WebDriverException:
        return "<body unavailable>"
    if result.get("base64Encoded"):
        return "<base64 body>"
    return (result.get("body") or "")[:200]


def submit_login(driver):
    logger.info("Submitting login form")
    drain_performance_log(driver)
    human_click(driver, driver.find_element(By.CSS_SELECTOR, LOGIN_BUTTON))

    response = wait_for_login_response(driver, timeout=15)
    if response is None:
        logger.warning("No /api/login response observed")
    else:
        status = response["response"]["status"]
        body = read_response_body(driver, response["requestId"])
        if 200 <= status < 300:
            logger.success("Login HTTP {}: {}", status, body)
        else:
            logger.error("Login HTTP {}: {}", status, body)

    sleep(WAIT_AFTER_LOGIN)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main(headless: bool = False, undetected: bool = True):
    setup_logging()
    logger.info("Launching Chromium")
    driver = build_driver(headless=headless, undetected=undetected)
    try:
        logger.info("Opening {}", URL)
        driver.get(URL)
        wait_visible(driver, CHECKBOX, timeout=20)
        sleep(random.uniform(*WAIT_AFTER_LOAD))

        fill_credentials(driver)

        logger.info("Clicking the ARCcaptcha checkbox")
        human_click(driver, driver.find_element(By.CSS_SELECTOR, CHECKBOX_INPUT))

        try:
            wait_for_puzzle(driver)
        except TimeoutException:
            if not is_visible(driver, CHECKED_ICON):
                raise
            logger.success("The checkbox passed without a puzzle")
            logger.info("Waiting {} seconds...", WAIT_AFTER_SOLVE)
            sleep(WAIT_AFTER_SOLVE)
            solved = True
        else:
            logger.info("Puzzle appeared; solving")
            solve_puzzle(driver)
            logger.info("Waiting {} seconds for verification...", WAIT_AFTER_SOLVE)
            sleep(WAIT_AFTER_SOLVE)
            solved = is_visible(driver, CHECKED_ICON)
            token = driver.find_element(By.CSS_SELECTOR, TOKEN).get_attribute("value")
            if solved:
                logger.success(
                    "Captcha solved (token: {})", "present" if token else "empty"
                )
            else:
                logger.error(
                    "Captcha was not solved (token: {})", "present" if token else "empty"
                )

        if solved:
            submit_login(driver)
        else:
            logger.warning("Login skipped because the captcha was not solved")
    finally:
        logger.debug("Closing the browser")
        driver.quit()


if __name__ == "__main__":
    main()