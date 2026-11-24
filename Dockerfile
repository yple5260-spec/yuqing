FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 XIN_DATA_DIR=/data XIN_HOST=0.0.0.0 XIN_PORT=8765 XIN_REQUIRE_POSTGRES=1 \
    XIN_CRAWLER_PYTHON=/opt/crawler/bin/python PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
ARG DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG PLAYWRIGHT_DOWNLOAD_MIRROR=https://cdn.playwright.dev
WORKDIR /app
COPY requirements.txt requirements-crawler.txt ./
RUN set -eu; \
    install_python_packages() { \
        "$1" -m pip install --no-cache-dir --disable-pip-version-check --timeout 20 --retries 1 \
            --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r "$2" \
        || "$1" -m pip install --no-cache-dir --disable-pip-version-check --timeout 20 --retries 1 \
            --index-url https://mirrors.aliyun.com/pypi/simple -r "$2" \
        || "$1" -m pip install --no-cache-dir --disable-pip-version-check --timeout 20 --retries 1 \
            --index-url https://pypi.org/simple -r "$2"; \
    }; \
    install_python_packages python requirements.txt; \
    python -m venv /opt/crawler; \
    install_python_packages "$XIN_CRAWLER_PYTHON" requirements-crawler.txt
RUN set -eu; \
    sources=/etc/apt/sources.list.d/debian.sources; \
    cp "$sources" /tmp/debian.sources; \
    sed -i "s|^URIs: http://deb.debian.org/debian$|URIs: $DEBIAN_MIRROR|" "$sources"; \
    export DEBIAN_FRONTEND=noninteractive; \
    install_browser_libraries() { \
        apt-get update \
        && apt-get install -y --no-install-recommends libatomic1 fonts-noto-cjk \
        && "$XIN_CRAWLER_PYTHON" -m playwright install-deps chromium; \
    }; \
    install_browser_libraries || { cp /tmp/debian.sources "$sources"; install_browser_libraries; }; \
    rm -rf /var/lib/apt/lists/* /tmp/debian.sources
RUN set -eu; \
    export PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=90000; \
    PLAYWRIGHT_DOWNLOAD_HOST="$PLAYWRIGHT_DOWNLOAD_MIRROR" "$XIN_CRAWLER_PYTHON" -m playwright install --only-shell chromium \
        || "$XIN_CRAWLER_PYTHON" -m playwright install --only-shell chromium; \
    groupadd --gid 10001 app; \
    useradd --uid 10001 --gid app --create-home app; \
    install -d -o app -g app /data /app/runtime
COPY --chown=app:app server.py scoring.py database.py routing.py storage.py collection.py weibo.py social.py jobs.py portrait.py manage.py migrate_sqlite.py migrate_media.py ./
COPY --chown=app:app static/ ./static/
COPY --chown=app:app scripts/collect_page.py scripts/crawl4ai_smoke.py ./scripts/
ENV HOME=/home/app
USER 10001:10001
RUN "$XIN_CRAWLER_PYTHON" scripts/crawl4ai_smoke.py && rm -rf runtime/crawl4ai-smoke
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=3).read()"
CMD ["python", "server.py", "--no-seed"]
