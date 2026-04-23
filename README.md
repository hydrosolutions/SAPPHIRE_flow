# SAPPHIRE Flow

[![CI](https://github.com/hydrosolutions/SAPPHIRE_flow/actions/workflows/ci.yml/badge.svg)](https://github.com/hydrosolutions/SAPPHIRE_flow/actions/workflows/ci.yml)
[![Integration (nightly)](https://github.com/hydrosolutions/SAPPHIRE_flow/actions/workflows/integration-nightly.yml/badge.svg)](https://github.com/hydrosolutions/SAPPHIRE_flow/actions/workflows/integration-nightly.yml)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Managed with uv](https://img.shields.io/badge/managed%20with-uv-261230.svg)](https://docs.astral.sh/uv/)

<!-- Uncomment when the repository becomes public (these badges require shields.io to query the GitHub API, which fails on private repos):
[![GitHub release](https://img.shields.io/github/v/release/hydrosolutions/SAPPHIRE_flow)](https://github.com/hydrosolutions/SAPPHIRE_flow/releases)
[![Last commit](https://img.shields.io/github/last-commit/hydrosolutions/SAPPHIRE_flow)](https://github.com/hydrosolutions/SAPPHIRE_flow/commits/main)
[![Open issues](https://img.shields.io/github/issues/hydrosolutions/SAPPHIRE_flow)](https://github.com/hydrosolutions/SAPPHIRE_flow/issues)
-->

Operational hydrological forecasting system. Ingests weather and station data, runs ensemble forecast models, checks alert thresholds, and will serve results via a REST API (in progress).

## Maintenance Status

🟢 **Active** – Developed & maintained by [hydrosolutions](https://github.com/hydrosolutions)

## Requirements

- Docker >= 24, Docker Compose v2
- [uv](https://docs.astral.sh/uv/) >= 0.5
- Python 3.11+

## Quick start

The quick start deploys SAPPHIRE Flow as a **demo** using publicly available Swiss data (CAMELS-CH stations, MeteoSwiss ICON-CH2-EPS forecasts, BAFU observations). This is the default configuration in v0 and is intended to showcase the pipeline end-to-end. Instructions for configuring the system against other areas of interest (custom station networks, alternative NWP sources, regional data) will be published as part of v1.

### 1. Create secrets

Secrets live outside the repository at `~/.config/sapphire-flow/secrets/` (dev). A gitignored symlink in the repo lets Docker Compose find them. In production, Docker secrets mount files at `/run/secrets/<name>` — see [security standards](docs/standards/security.md) for the full model.

```bash
mkdir -p ~/.config/sapphire-flow/secrets
openssl rand -base64 24 > ~/.config/sapphire-flow/secrets/db_password
ln -s ~/.config/sapphire-flow/secrets secrets
```

### 2. Start the database

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres
```

This starts PostgreSQL (PostGIS) on **localhost:5438** with the `sapphire` database and user.

Dev overlay port mapping (to avoid conflicts with other local services):

| Service | Host port | Container port |
|---|---|---|
| PostgreSQL | 5438 | 5432 |
| Prefect UI | 4200 | 4200 |
| API | 8010 | 8000 |

### 3. Install Python dependencies

```bash
uv sync
```

### 4. Run database migrations

```bash
DB_PASS=$(cat secrets/db_password)
DATABASE_URL="postgresql+psycopg://sapphire:${DB_PASS}@localhost:5438/sapphire" \
  uv run alembic upgrade head
```

### 5. Onboard initial data (CAMELS-CH)

Downloads the CAMELS-CH dataset (~250 MB) and loads stations into the database:

```bash
DB_PASS=$(cat secrets/db_password)
DATABASE_URL="postgresql+psycopg://sapphire:${DB_PASS}@localhost:5438/sapphire" \
  SAPPHIRE_ENV=dev \
  uv run python scripts/onboard.py --download
```

To onboard a single station for quick testing:

```bash
DB_PASS=$(cat secrets/db_password)
DATABASE_URL="postgresql+psycopg://sapphire:${DB_PASS}@localhost:5438/sapphire" \
  SAPPHIRE_ENV=dev \
  uv run python scripts/onboard.py --download --basin-ids 2004
```

Data is stored outside the repository at the location resolved by `SAPPHIRE_DATA_DIR` (defaults to the platform data directory, e.g. `~/Library/Application Support/sapphire-flow` on macOS).

### 6. Run tests

```bash
uv run pytest
```

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes (scripts/tests) | -- | psycopg connection string |
| `SAPPHIRE_CONFIG` | No | built-in Swiss defaults | Path to deployment config TOML |
| `SAPPHIRE_DATA_DIR` | No | platform data dir | Root for raw data, artifacts, cache |
| `SAPPHIRE_ENV` | No | `prod` | Set to `dev` for console log output |

## Documentation

- [v0 scope](docs/v0-scope.md) -- what is built and in what order
- [Architecture](docs/architecture-context.md) -- system design and data flows
- [CI/CD standards](docs/standards/cicd.md) -- Docker topology, deployment, upgrades
- [Security standards](docs/standards/security.md) -- secrets, container hardening
- [Config reference](docs/spec/config-reference.toml) -- all configuration fields
