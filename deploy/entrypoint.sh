#!/bin/sh
# Container entrypoint:
#   1. start Xvfb on :99 (always — headless runs ignore it, headful runs need it)
#   2. optionally start x11vnc + noVNC (START_VNC=true) so you can watch the
#      browser in headful mode from a normal web browser on port 7900
#   3. exec the platform (uvicorn via python -m app)

set -e

mkdir -p /data

echo "[entrypoint] starting Xvfb on :99"
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
XVFB_PID=$!

# Give Xvfb a moment, then verify it is up
sleep 0.5
if ! kill -0 "$XVFB_PID" 2>/dev/null; then
  echo "[entrypoint] ERROR: Xvfb failed to start" >&2
  exit 1
fi

if [ "$START_VNC" = "true" ] || [ "$START_VNC" = "1" ]; then
  echo "[entrypoint] starting x11vnc (port 5900, internal) + noVNC (port 7900)"
  x11vnc -display :99 -forever -shared -nopw -quiet &
  websockify --web /usr/share/novnc 7900 localhost:5900 &
  echo "[entrypoint] noVNC ready: http://localhost:7900  (no password - keep it internal!)"
fi

exec "$@"
