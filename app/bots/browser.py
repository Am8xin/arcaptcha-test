"""Chrome driver factory shared by every Selenium-based bot.

Reads the standard platform env vars (CHROME_BINARY, CHROMEDRIVER_PATH,
CHROME_NO_SANDBOX, ...) so the same code runs on desktops and inside Docker.

Anti-detection measures applied here (see README "Reducing CAPTCHA blocking"):

* ``undetected-chromedriver`` removes chromedriver's automation hooks when
  available; if it is missing or fails to launch, we fall back to plain
  Selenium plus a small stealth script so the platform still works.
* In headless mode the User-Agent is normalised (the word ``Headless`` is
  removed) because fingerprinting scripts read it verbatim.
"""

from __future__ import annotations

import logging
import re
import subprocess

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from ..config import settings

logger = logging.getLogger(__name__)

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""


def detect_chrome_major(binary: str | None = None) -> int | None:
    """Return the major version of a Chrome binary, e.g. 157."""
    binary = binary or settings.chrome_binary or "google-chrome"
    for candidate in (binary, "google-chrome", "chromium"):
        try:
            out = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.search(r"(\d+)\.", out.stdout)
        if match:
            return int(match.group(1))
    return None


def _normalized_user_agent(headless: bool, override: str = "") -> str:
    if override:
        return override
    major = detect_chrome_major() or 130
    ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    ).format(major=major)
    return ua  # headless or not: never advertise "Headless" in the UA


def build_driver(headless: bool, *, undetected: bool = True) -> webdriver.Chrome:
    """Create a Chrome session with automation fingerprints patched."""
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--window-size={settings.window_width},{settings.window_height}")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-dev-shm-usage")
    if settings.chrome_no_sandbox:
        options.add_argument("--no-sandbox")
    if headless:
        options.add_argument("--headless=new")
        # Keep the UA identical between headless and headful runs.
        options.add_argument(f"--user-agent={_normalized_user_agent(headless=True)}")

    if settings.chrome_binary:
        options.binary_location = settings.chrome_binary
    # NOTE: legacy anti-detection options `excludeSwitches` and
    # `useAutomationExtension` are NOT set here: chromedriver >= 137 rejects
    # them ("unrecognized chrome option"). undetected-chromedriver removes the
    # automation switches itself; the plain fallback patches via CDP instead.
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    if undetected:
        try:
            import undetected_chromedriver as uc
        except ImportError:
            logger.warning("undetected-chromedriver not installed; using plain Selenium")
        else:
            try:
                driver = uc.Chrome(
                    options=options,
                    driver_executable_path=settings.chromedriver_path or None,
                    browser_executable_path=settings.chrome_binary or None,
                    version_main=detect_chrome_major(),
                    use_subprocess=True,
                )
                driver.set_window_size(settings.window_width, settings.window_height)
                return driver
            except Exception as exc:  # pragma: no cover - environment specific
                logger.warning("undetected-chromedriver failed (%s); falling back", exc)

    driver = webdriver.Chrome(
        service=_service(), options=options
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _STEALTH_JS})
    driver.set_window_size(settings.window_width, settings.window_height)
    return driver


def _service() -> webdriver.Chrome.service.Service:
    from selenium.webdriver.chrome.service import Service

    return Service(settings.chromedriver_path) if settings.chromedriver_path else Service()
