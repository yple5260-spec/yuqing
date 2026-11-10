FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 XIN_DATA_DIR=/data XIN_HOST=0.0.0.0 XIN_PORT=8765 XIN_REQUIRE_POSTGRES=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app && mkdir /data && chown app:app /data
COPY --chown=app:app server.py scoring.py database.py routing.py storage.py manage.py migrate_sqlite.py migrate_media.py ./
COPY --chown=app:app static/ ./static/
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=3).read()"
CMD ["python", "server.py", "--no-seed"]
