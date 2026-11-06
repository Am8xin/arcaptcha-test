
# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_NO_SANDBOX=true \
    DATABASE_URL=sqlite:////data/arcaptcha.db \
    DATA_DIR=/data \
    DISPLAY=:99 \
    START_VNC=false \
    PIP_INDEX_URL=https://pypi.org/simple/ \
    PIP_DEFAULT_TIMEOUT=60

# Install system dependencies from official Debian repositories
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium chromium-driver \
        xvfb x11vnc novnc websockify \
        curl ca-certificates \
        fonts-liberation libnss3-tools xdotool \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

# Install Python dependencies
COPY requirements.txt ./

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
        --retries 3 \
        --timeout 60 \
        -r requirements.txt

# Copy application files
COPY app ./app
COPY scripts ./scripts
COPY tests ./tests
COPY pytest.ini ./

RUN mkdir -p /data

EXPOSE 8000 7900

COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

CMD ["python", "-m", "app"]
