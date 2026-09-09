# syntax=docker/dockerfile:1
# One image, two processes: `gogo worker` writes forecasts; uvicorn serves the page.
# Secrets come from the environment at run time — nothing from .env is copied in.

FROM node:22-bookworm-slim AS ui
WORKDIR /repo
COPY web/package.json web/package-lock.json web/
RUN npm ci --prefix web
COPY web/ web/
RUN mkdir -p src/gogo/static/app && npm run build --prefix web

FROM python:3.12-slim-bookworm AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home gogo

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY --from=ui /repo/src/gogo/static/app ./src/gogo/static/app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh && chown -R gogo:gogo /app

USER gogo
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gogo", "worker", "--interval", "3600"]
