
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_NO_SANDBOX=true \
    DATABASE_URL=sqlite:////data/arcaptcha.db \
    DATA_DIR=/data \
    DISPLAY=:99 \
    START_VNC=false


RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
          -e 's|http://deb.debian.org/debian-security|https://cloudfront.debian.net/debian-security|g' \
          -e 's|https://deb.debian.org/debian-security|https://cloudfront.debian.net/debian-security|g' \
          -e 's|http://deb.debian.org/debian|https://mirror.famaserver.com/debian|g' \
          -e 's|https://deb.debian.org/debian|https://mirror.famaserver.com/debian|g' \
          /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i \
          -e 's|http://deb.debian.org/debian-security|https://cloudfront.debian.net/debian-security|g' \
          -e 's|https://deb.debian.org/debian-security|https://cloudfront.debian.net/debian-security|g' \
          -e 's|http://deb.debian.org/debian|https://mirror.famaserver.com/debian|g' \
          -e 's|https://deb.debian.org/debian|https://mirror.famaserver.com/debian|g' \
          /etc/apt/sources.list; \
    fi; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' \
      > /etc/apt/apt.conf.d/80-retries; \
    apt-get update -o Acquire::Retries=5 -o Acquire::ForceIPv4=true


WORKDIR /srv/app

COPY requirements.txt ./

RUN python -m pip install \
    --index-url https://pypi.mirrors.ustc.edu.cn/simple/ \
    --trusted-host pypi.mirrors.ustc.edu.cn \
    --retries 10 \
    --timeout 120 \
    --no-cache-dir \
    -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY tests ./tests
COPY pytest.ini ./
COPY deploy/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh && \
    mkdir -p /data

EXPOSE 8000 7900

ENTRYPOINT ["/entrypoint.sh"]

CMD ["python", "-m", "app"]
