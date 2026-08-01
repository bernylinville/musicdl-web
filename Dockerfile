FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN python -m pip install --no-cache-dir "uv==0.8.14"

WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY backend/src ./backend/src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 musicdl \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /app musicdl

WORKDIR /app
COPY --from=builder --chown=1000:1000 /opt/venv /opt/venv
COPY --chown=1000:1000 frontend/dist /app/frontend

USER 1000:1000
EXPOSE 4534

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4534/healthz', timeout=3).read()"]

CMD ["uvicorn", "musicdl_web.app:app", "--host", "0.0.0.0", "--port", "4534", "--no-access-log"]
