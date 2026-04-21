# python:3.12.13-slim (manifest-list digest pinned 2026-04-21 per Plan 064 B1)
FROM python:3.12.13-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286 AS builder

# ghcr.io/astral-sh/uv:0.11.7 (manifest-list digest pinned 2026-04-21 per Plan 064 B5)
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /usr/local/bin/uv

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


# python:3.12.13-slim (manifest-list digest pinned 2026-04-21 per Plan 064 B1)
FROM python:3.12.13-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286

RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m app

# Add the PostgreSQL Global Development Group (PGDG) apt source + GPG key for versioned client packages.
# Debian's default repo ships postgresql-client-15, which can't dump a postgres 16 server.
# PGDG apt signing key, vendored 2026-04-21. Fingerprint: B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8.
# Source key id ACCC4CF8 (PostgreSQL Debian Repository). Rotate deliberately per Plan 064 B6 / D11.
COPY docker/keys/apt.postgresql.org.asc /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates gnupg curl \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release; echo $VERSION_CODENAME)-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
      gosu curl postgresql-client-16 libexpat1 libgeos-c1v5 \
    && rm -rf /var/lib/apt/lists/*
# libexpat1: runtime dependency of rasterio's binary extensions (via rioxarray in the
# gridded-NWP extractor). Added 2026-04-19 as an A3 step-8 finding.
# libgeos-c1v5: provides libgeos_c.so.1 required by exactextract (used by
# ExactExtractGridExtractor for basin-average extraction from NWP grids).

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
