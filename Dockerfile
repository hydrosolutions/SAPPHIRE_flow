# python:3.12.13-slim (manifest-list digest pinned 2026-04-21; re-pinned 2026-06-02
# to the debian 13.5 rebuild — clears CVE-2026-4878 libcap2 + prior OpenSSL/pip CVEs — per Plan 064 B1)
FROM python:3.14.6-slim@sha256:44dd04494ee8f3b538294360e7c4b3acb87c8268e4d0a4828a6500b1eff50061 AS builder

# ghcr.io/astral-sh/uv:0.11.7 (manifest-list digest pinned 2026-04-21 per Plan 064 B5)
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /usr/local/bin/uv

WORKDIR /app

# Build tooling for sdist-only deps on linux/arm64 (exactextract publishes no
# linux/aarch64 wheel; see Plan 056 D3). Builder stage only — the final image
# copies .venv and excludes these packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN mkdir -p src/sapphire_flow && touch src/sapphire_flow/__init__.py

RUN uv sync --frozen --no-dev

COPY src/ src/
COPY alembic.ini ./
COPY alembic/ alembic/


# python:3.12.13-slim (manifest-list digest pinned 2026-04-21; re-pinned 2026-06-02
# to the debian 13.5 rebuild — clears CVE-2026-4878 libcap2 + prior OpenSSL/pip CVEs — per Plan 064 B1)
FROM python:3.14.6-slim@sha256:44dd04494ee8f3b538294360e7c4b3acb87c8268e4d0a4828a6500b1eff50061

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
      gosu curl postgresql-client-16 libexpat1 libgeos-c1v5 libeccodes0 \
    && rm -rf /var/lib/apt/lists/*
# libexpat1: runtime dependency of rasterio's binary extensions (via rioxarray in the
# gridded-NWP extractor). Added 2026-04-19 as an A3 step-8 finding.
# libgeos-c1v5: provides libgeos_c.so.1 required by exactextract (used by
# ExactExtractGridExtractor for basin-average extraction from NWP grids).
# libeccodes0: ecCodes C library that cfgrib / xarray use to parse GRIB2
# files from MeteoSwiss ICON-CH2-EPS. The Python eccodes wheel is a thin
# ctypes wrapper; the system shared library must be present at runtime.
# Added 2026-04-23 after Sprint 1.3 forecast-cycle live run surfaced
# "Cannot find the ecCodes library" from cfgrib.

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
