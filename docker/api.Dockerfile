FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /opt/stockai

RUN python -m pip install --no-cache-dir uv==0.11.27

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime

ENV HOME=/tmp \
    PATH=/opt/stockai/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

RUN groupadd --gid 10001 stockai \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /tmp --shell /usr/sbin/nologin stockai

WORKDIR /opt/stockai
COPY --from=builder --chown=10001:10001 /opt/stockai/.venv /opt/stockai/.venv

USER 10001:10001
EXPOSE 8000
VOLUME ["/tmp"]
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health/live', timeout=2).close()"]

ENTRYPOINT ["stockai-api"]
