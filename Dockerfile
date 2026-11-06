# Extensible browser-bot platform — single-image deployment.
#
# Contains: FastAPI app + bot runner + Chromium (Chromium via Debian repos,
# available for amd64 and arm64) + Xvfb for headful-in-container + VNC stack
# (x11vnc + noVNC) for watching headful runs on servers.
#
# Build:  docker build -t arcaptcha-platform .
# Run:    docker compose up          (recommended, see docker-compose.yml)

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    # Browser defaults inside the container (override in compose/.env if needed)
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_NO_SANDBOX=true \
    DATABASE_URL=sqlite:////data/arcaptcha.db \
    DATA_DIR=/data \
    DISPLAY=:99 \
    START_VNC=false

# Chromium + chromedriver (multi-arch: amd64, arm64) and the X11/VNC stack
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium chromium-driver \
        xvfb x11vnc novnc websockify \
        curl ca-certificates \
        fonts-liberation libnss3-tools xdotool \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY tests ./tests
COPY pytest.ini ./

# Persisted run history lives here (mount a volume on /data)
RUN mkdir -p /data

EXPOSE 8000 7900

COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# No healthcheck curl one-liner here: see docker-compose.yml (uses python httpx).

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "app"]
