#!/bin/sh
set -e
mkdir -p /data/logs
Xvfb :99 -screen 0 1440x900x24 &
sleep 1
exec uvicorn app.main:app --host 0.0.0.0 --port 8000