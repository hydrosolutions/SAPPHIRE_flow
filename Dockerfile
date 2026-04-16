FROM python:3.11.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN mkdir -p src/sapphire_flow && touch src/sapphire_flow/__init__.py

RUN uv sync --frozen --no-dev

COPY src/ src/
COPY alembic.ini ./
COPY alembic/ alembic/


FROM python:3.11.12-slim

RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m app

RUN apt-get update && apt-get install -y --no-install-recommends gosu curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
COPY --from=builder --chown=app:app /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=app:app /app/alembic /app/alembic

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "sapphire_flow"]
