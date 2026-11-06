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

import functools
import logging
import re
import subprocess
import threading

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from ..config import settings

logger = logging.getLogger(__name__)

# Serialise only the *startup* of browser sessions. undetected-chromedriver
# patches the chromedriver binary on first use; several concurrent creations
# used to race on that file, re-patch it over and over (adding ~80 s to a run)
# and sometimes crash, silently falling back to plain Selenium — which shows
# the "Chrome is being controlled by automated test software" bar and gets
# detected. The lock keeps runs parallel while startups queue briefly.
_CREATE_LOCK = threading.Lock()

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""


@functools.lru_cache(maxsize=8)
def detect_chrome_major(binary: str | None = None) -> int | None:
    """Return the major version of a Chrome binary, e.g. 157 (cached)."""
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
    options.page_load_strategy = "eager"  # DOM ready beats every resource
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--window-size={settings.window_width},{settings.window_height}")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-infobars")
    if settings.chrome_no_sandbox:
        options.add_argument("--no-sandbox")
    # Normalised UA in headless mode AND whenever the distro Chromium is
    # used (Docker): its real UA advertises "Chromium/…" instead of
    # "Chrome/…", an easy fingerprint flag. A real desktop Chrome keeps
    # its own UA in headful mode.
    distro_chromium = str(settings.chrome_binary or "").endswith("chromium")
    if headless:
        options.add_argument("--headless=new")
    if headless or distro_chromium:
        options.add_argument(f"--user-agent={_normalized_user_agent(headless)}")

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
                with _CREATE_LOCK:
                    driver = _create_uc(uc, options)
                _finish_driver(driver)
                return driver
            except Exception as exc:  # pragma: no cover - environment specific
                logger.warning("undetected-chromedriver failed (%s); falling back", exc)

    driver = webdriver.Chrome(
        service=_service(), options=options
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _STEALTH_JS})
    _finish_driver(driver)
    return driver


def _create_uc(uc_module, options) -> webdriver.Chrome:
    """Instantiate undetected-chromedriver, retrying once on transient races."""
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            return uc_module.Chrome(
                options=options,
                driver_executable_path=settings.chromedriver_path or None,
                browser_executable_path=settings.chrome_binary or None,
                version_main=detect_chrome_major(),
                use_subprocess=True,
                # Copy the patched driver per process so parallel runs never
                # re-patch (or lock) the shared binary.
                user_multi_procs=True,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning("uc.Chrome attempt %d failed: %s", attempt, exc)
    raise last_exc  # type: ignore[misc]


def _finish_driver(driver: webdriver.Chrome) -> None:
    """Apply timeouts and window size common to every driver flavour."""
    driver.set_page_load_timeout(settings.page_load_timeout)
    driver.set_window_size(settings.window_width, settings.window_height)


def _service() -> webdriver.Chrome.service.Service:
    from selenium.webdriver.chrome.service import Service

    return Service(settings.chromedriver_path) if settings.chromedriver_path else Service()
