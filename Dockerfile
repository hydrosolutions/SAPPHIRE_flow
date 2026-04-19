FROM python:3.11.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /usr/local/bin/uv

WORKDIR /app

# Build tooling for sdist-only deps on linux/arm64 (exactextract publishes no
# linux/aarch64 wheel; see Plan 056 D3). Builder stage only — the final image
# copies .venv and excludes these packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN mkdir -p src/sapphire_flow && touch src/sapphire_flow/__init__.py

RUN uv sync --frozen --no-dev

COPY src/ src/
COPY alembic.ini ./
COPY alembic/ alembic/


FROM python:3.11.12-slim

RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m app

# Add the PostgreSQL Global Development Group (PGDG) apt source + GPG key for versioned client packages.
# Debian's default repo ships postgresql-client-15, which can't dump a postgres 16 server.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates gnupg curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release; echo $VERSION_CODENAME)-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
      gosu curl postgresql-client-16 libexpat1 \
    && rm -rf /var/lib/apt/lists/*
# libexpat1: runtime dependency of rasterio's binary extensions (via rioxarray in the
# gridded-NWP extractor). Added 2026-04-19 as an A3 step-8 finding.

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
