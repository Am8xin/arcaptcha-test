
#!/bin/sh

set -eu

echo "[entrypoint] Initializing application..."

mkdir -p /data

# ------------------------------------------------------------
# 1. Verify Xvfb installation
# ------------------------------------------------------------

if ! command -v Xvfb >/dev/null 2>&1; then
    echo "[entrypoint] ERROR: Xvfb is not installed." >&2
    echo "[entrypoint] Expected binary: /usr/bin/Xvfb" >&2
    exit 1
fi

# ------------------------------------------------------------
# 2. Start virtual display
# ------------------------------------------------------------

echo "[entrypoint] Starting Xvfb on ${DISPLAY:-:99}"

Xvfb "${DISPLAY:-:99}" \
    -screen 0 1440x900x24 \
    -nolisten tcp &

XVFB_PID=$!

sleep 1

if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "[entrypoint] ERROR: Xvfb failed to start." >&2
    exit 1
fi

echo "[entrypoint] Xvfb started successfully (PID: $XVFB_PID)"

# ------------------------------------------------------------
# 3. Optional VNC / noVNC
# ------------------------------------------------------------

if [ "${START_VNC:-false}" = "true" ] || \
   [ "${START_VNC:-false}" = "1" ]; then

    echo "[entrypoint] Starting x11vnc..."

    x11vnc \
        -display "${DISPLAY:-:99}" \
        -forever \
        -shared \
        -nopw \
        -quiet &

    VNC_PID=$!

    echo "[entrypoint] Starting noVNC on port 7900..."

    websockify \
        --web /usr/share/novnc \
        7900 \
        localhost:5900 &

    NOVNC_PID=$!

    sleep 1

    if ! kill -0 "$VNC_PID" 2>/dev/null; then
        echo "[entrypoint] ERROR: x11vnc failed." >&2
        exit 1
    fi

    if ! kill -0 "$NOVNC_PID" 2>/dev/null; then
        echo "[entrypoint] ERROR: noVNC failed." >&2
        exit 1
    fi

    echo "[entrypoint] noVNC available on port 7900"
fi

# ------------------------------------------------------------
# 4. Start application
# ------------------------------------------------------------

echo "[entrypoint] Starting ARCaptcha Browser Bot Platform..."

exec "$@"
