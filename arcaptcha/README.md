# ARCcaptcha Demo

End-to-end browser automation for the live [ARCcaptcha demo](https://demo.arcaptcha.co/).
This folder shows how the checkbox and slider challenge works, how the gap is
detected with OpenCV, and how to automate the whole flow with two different
browser libraries.

The demo is for educational purposes only. The site and its selectors can change.

## What is ARCcaptcha?

ARCcaptcha is a CAPTCHA service that protects forms with a checkbox and, when
additional verification is needed, a slider puzzle. On the demo page:

1. A checkbox verifies the visitor.
2. A puzzle overlay appears with a background image and a small puzzle piece.
3. The visitor drags the slider so the piece lands in the gap.
4. After the challenge is solved, the page shows a success indicator.
5. The demo login form can be submitted with the credentials `demo` / `demo`.

If the checkbox passes without a puzzle, the flow simply continues to login.

## What is in this folder?

| File | Purpose |
| --- | --- |
| `main.py` | Full Patchright flow: fill credentials, click checkbox, solve puzzle, submit login. |
| `alternative.py` | DrissionPage example that opens the page and clicks the checkbox. It does not solve the puzzle or login. |
| `solver.py` | OpenCV gap detector used by `main.py`. |
| `log.py` | Colored loguru logging configuration shared by both scripts. |

## How it works

### 1. Fill the demo credentials

The email and password fields are filled before the CAPTCHA is touched:

- `#email` = `demo`
- `#password` = `demo`

### 2. Click the checkbox

The visible clickable square is `#input-checkbox`, not the inner checkbox input.
A regular locator click does not trigger ARCcaptcha in this demo, so the script
uses a human-like mouse sequence:

- move to the checkbox,
- press the mouse button,
- hold it for a short random time,
- release it.

### 3. Read the puzzle layout

When the puzzle appears, `main.py` reads:

| Selector | Meaning |
| --- | --- |
| `.arc-puzzle` | Puzzle overlay. |
| `.arc-puzzle img.tw-object-contain` | Background image. |
| `.arc-puzzle img.puzzle` | Puzzle piece with an alpha channel. |
| `.arc-puzzle .draggable` | Slider handle. |
| `#checked-in-the-box` | Visible after a successful checkbox challenge. |
| `#arcaptcha-token` | Hidden token produced by the widget. |

The background image and the piece are downloaded with the browser session, so
the solver works with the exact images the page displayed. The displayed scale
is calculated from the rendered width and `naturalWidth`.

### 4. Detect the gap with OpenCV

`solver.py` implements `find_gap(background, piece, y_hint, y_tolerance)`:

1. Build a binary mask from the piece alpha channel (`alpha > 128`).
2. Extract the piece outline with a morphological gradient.
3. Run Canny edge detection on the background.
4. Build a distance transform where background edges have distance 0.
5. Slide the outline over the background and minimize the mean distance of the
   outline points to the nearest edge.

The vertical search is restricted to the piece's current row (`y_hint +/- 2`),
which makes the search fast and stable. The result contains `x`, `y`, and a
match score.

### 5. Drag the slider

The drag distance is:

```text
distance = gap.x * scale - piece_left
```

`piece_left` is the piece position relative to the background, and `scale`
converts the natural image coordinates to screen pixels. The slider is dragged
with cubic easing and small pauses, then the script waits for verification and
checks `#checked-in-the-box`.

### 6. Submit login

After the CAPTCHA is solved, the script clicks the login button and captures the
`/api/login` response. A successful run reports:

```text
Login HTTP 200: {'msg': 'You are logged in successfully!'}
```

## What are Patchright and DrissionPage?

### Patchright

[Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) is a
stealth-oriented, drop-in replacement for Playwright. It keeps the Playwright
API but patches common browser automation fingerprints. `main.py` uses
Patchright's synchronous Chromium API to control a visible browser, read the
page, fetch the puzzle images, perform mouse movement and submit the form.

Patchright uses its own Chromium build. Install it with:

```sh
patchright install chromium
```

### DrissionPage

[DrissionPage](https://github.com/g1879/DrissionPage) is a Python browser
automation library that controls Chromium directly and can combine browser
interaction with HTTP requests. `alternative.py` uses it to open the demo page
and click the checkbox. It is an alternative to Patchright, not a replacement
for the puzzle solver: this example stops after the click.

On macOS, DrissionPage looks for a local Chrome/Chromium, then for a
Playwright/Patchright Chromium build. You can also point it to a specific
binary with the `CHROME_PATH` environment variable.

## Requirements

- Python 3.10+
- The dependencies from the repository root:

```sh
pip install -r requirements.txt
```

- Patchright Chromium:

```sh
patchright install chromium
```

- Network access to `https://demo.arcaptcha.co/`

## Running

Run the commands from the repository root so the relative imports resolve.

### Patchright: solve the CAPTCHA and log in

```sh
python -m demo.arcaptcha.main
```

The script opens a visible Chromium window, fills `demo` / `demo`, clicks the
checkbox, solves the puzzle, waits for verification, and submits the login form.
Colored loguru output shows each step.

### DrissionPage: click the checkbox only

```sh
python -m demo.arcaptcha.alternative
```

This variant opens the page and clicks the checkbox, then waits briefly. It is
useful for comparing the two automation libraries.

## Configuration

At the top of `main.py`:

- `DEMO_USERNAME` / `DEMO_PASSWORD` — demo login credentials.
- `WAIT_AFTER_SOLVE` — verification wait in seconds.
- `WAIT_AFTER_LOGIN` — wait after the login response in seconds.
- `URL` — the ARCcaptcha demo address.

Logging is configured in `log.py` with loguru. Change the `setup_logging(level)`
argument to control verbosity; the default is `DEBUG`.
