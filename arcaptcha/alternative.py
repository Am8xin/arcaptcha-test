"""Open the ARCcaptcha demo with DrissionPage and click the checkbox."""

import glob
import os
from pathlib import Path
from time import sleep

from DrissionPage import ChromiumOptions, ChromiumPage

from .log import logger, setup_logging

URL = "https://demo.arcaptcha.co/"
CHECKBOX = "#arc-checkbox"
WAIT_AFTER_CLICK = 10


def find_chromium_binary():
    """Find a locally installed Chrome/Chromium binary."""
    env_path = os.environ.get("CHROME_PATH")
    if env_path:
        return env_path

    patterns = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        str(
            Path.home()
            / "Library/Caches/ms-playwright/chromium-*/chrome-mac*/"
            "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        ),
        str(
            Path.home()
            / "Library/Caches/ms-playwright/chromium-*/chrome-mac*/"
            "Chromium.app/Contents/MacOS/Chromium"
        ),
    )
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None


def main():
    setup_logging()
    options = ChromiumOptions()
    binary = find_chromium_binary()
    if binary:
        logger.debug("Using Chromium binary: {}", binary)
        options.set_browser_path(binary)
    else:
        logger.warning("No Chromium binary found; using DrissionPage defaults")

    page = ChromiumPage(addr_or_opts=options)
    try:
        logger.info("Opening {}", URL)
        page.get(URL)
        page.wait.ele_displayed(CHECKBOX, timeout=20)

        checkbox = page.ele(CHECKBOX)
        if not checkbox:
            raise RuntimeError(f"Checkbox {CHECKBOX!r} was not found")

        logger.info("Clicking the ARCcaptcha checkbox")
        checkbox.click()
        logger.success(
            "Checkbox clicked; waiting {} seconds...", WAIT_AFTER_CLICK
        )
        sleep(WAIT_AFTER_CLICK)
    finally:
        logger.debug("Closing the browser")
        page.quit()


if __name__ == "__main__":
    main()
