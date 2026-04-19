# CI/CD and Deployment Standards

> This document extends `docs/architecture-context.md`. It adds deployment implementation detail. For foundational decisions, see: tech stack (architecture-context.md § Tech stack), DB connection patterns (conventions.md § Database connection patterns), cold storage layout (architecture-context.md § Data retention and cold storage), backup plan (architecture-context.md § Backup and disaster recovery). This document does not redefine the tech stack, schema designs, or data flow definitions.
>
> **v0 simplifications**: See [`docs/v0-scope.md`](../v0-scope.md) § A3 (no PgBouncer), § A1 (no partitioning/DLQ), § A2 (no cold storage), § A6 (single work pool), § A10 (simple backup), § F (simplified Docker topology).

## Docker Compose service topology

Single VM deployment. All services in one `docker-compose.yml`. Swiss v0 targets up to ~170 stations; architecture supports ~1000 stations across deployments.

### Services

| Service | Image | Depends on | Health check | Restart | Scope |
|---------|-------|-----------|-------------|---------|-------|
| `postgres` | `postgis/postgis:16-3.4` | — | `pg_isready -U sapphire` | `unless-stopped` | v0+v1 |
| `pgbouncer` | `pgbouncer/pgbouncer` | postgres (healthy) | `pg_isready -h localhost -p 6432` | `unless-stopped` | **v1** (§A3) |
| `prefect-server` | `prefecthq/prefect:3-python3.11` | postgres (healthy) | `curl -f http://localhost:4200/api/health` | `unless-stopped` | v0+v1 |
| `prefect-worker` | custom (sapphire-flow) | prefect-server, postgres | — | `unless-stopped` | **v0** (§A6) |
| `prefect-worker-ops` | custom (sapphire-flow) | prefect-server, pgbouncer | — | `unless-stopped` | **v1** (§A6) |
| `prefect-worker-hindcast` | custom (sapphire-flow) | prefect-server, pgbouncer | — | `unless-stopped` | **v1** (§A6) |
| `prefect-worker-training` | custom (sapphire-flow) | prefect-server, pgbouncer | — | `unless-stopped` | **v1** (§A6) |
| `api` | custom (sapphire-flow) | postgres (v0) / pgbouncer (v1), prefect-server | `curl -f http://localhost:8000/api/v1/health` | `unless-stopped` | v0+v1 |
| `caddy` | `caddy:2` | api | TCP check on 443 | `unless-stopped` | v0+v1 |
| `init` | custom (sapphire-flow) | postgres (healthy) | — | `no` (one-shot) | v0+v1 |

### Custom image

One Dockerfile for `prefect-worker-ops`, `prefect-worker-hindcast`, `prefect-worker-training`, `api`, and `init`. Different entrypoints select the role. Base: `python:3.11-slim`. Dependency constraints: `numcodecs>=0.16.1` is required for its linux/arm64 wheel (earlier versions fall back to sdist and fail). `exactextract` publishes no linux/arm64 wheel at any version, so the builder stage installs `build-essential`, `cmake`, and `libgeos-dev` to compile it from sdist; the runtime stage copies only `.venv` and remains slim (see Plan 056 D3).

### Named volumes

| Volume | Mount path | Used by | Purpose | Scope |
|--------|-----------|---------|---------|-------|
| `pgdata` | `/var/lib/postgresql/data` | postgres | PostgreSQL data directory | v0+v1 |
| `model_artifacts` | `/data/artifacts` | prefect-worker (rw) [v0], prefect-worker-ops (ro), prefect-worker-hindcast (ro), prefect-worker-training (rw), api (ro) | Trained model files | v0+v1 |
| `cold_storage` | `/data/cold` | prefect-worker-ops (rw), prefect-worker-hindcast (ro), api (ro) | Parquet archive | **v1** (§A2) |
| `nwp_grids` | `/data/nwp_grids` | prefect-worker (rw, v0) | NWP Zarr archive hot tier | v0+ |
| `backups` | `/data/backups` | prefect-worker (rw) | pg_dump backup files (§A10) | v0+v1 |
| `prefect_data` | `/data/prefect` | prefect-server | Prefect server state | v0+v1 |
| `caddy_data` | Caddy internal | caddy | TLS certificates, OCSP staples | v0+v1 |
| `caddy_config` | Caddy internal | caddy | Persisted Caddy configuration | v0+v1 |

tmpfs mount: `/tmp/sapphire_nwp` (size=4g) on prefect-worker — scratch space for NWP GRIB2-to-Zarr conversion.

Config bind mount: `./config.toml:/app/config.toml:ro` on api and all three workers.

### Dependency chain

```
postgres ──→ pgbouncer ──→ api ──→ caddy
    │                 ↗
    └──→ prefect-server ──→ prefect-worker-ops
                       ──→ prefect-worker-hindcast
                       ──→ prefect-worker-training

    init (one-shot, runs before workers/api)
```

> **v0 variant** (v0-scope.md §A3, §A6): No PgBouncer intermediary; single `prefect-worker` replaces the three specialized workers.
> ```
> postgres ──→ api ──→ caddy
>     │
>     └──→ prefect-server ──→ prefect-worker
>
>     init (one-shot, runs before worker/api)
> ```

All `depends_on` use `condition: service_healthy` where health checks are defined.

## Prefect work pool separation

> **v1-only** (v0-scope.md §A6): v0 uses a single `default` work pool. The three-pool topology below applies to v1.

Three work pools isolate workloads with different resource and concurrency profiles:

| Pool | Flows | Default concurrency | Container | Default resource limits |
|------|-------|---------------------|-----------|------------------------|
| `ops` | 1 (forecast cycle), 2 (obs ingest), 4 (watchdog), 11 (NWP recovery), backup (daily), DLQ drain (hourly) | 4 | `prefect-worker-ops` | `mem_limit: 4g, cpus: 2.0` |
| `hindcast` | 7 (hindcast), 8/10 (skill) | 4 | `prefect-worker-hindcast` | `mem_limit: 8g, cpus: 4.0` |
| `training` | 6/9 (training) | 1 | `prefect-worker-training` | `mem_limit: 8g, cpus: 4.0` |

All concurrency limits and container resource limits (`mem_limit`, `cpus` in `docker-compose.yml`) are deployment-configurable — the operator sizes them to the VM.

**Why three pools:**
- **`ops`**: Forecast pipelines for independent stations run in parallel within a cycle. Lightweight per task.
- **`hindcast`**: Hindcast steps (H.5) are parallelizable across station/model pairs (see architecture-context.md). Multiple hindcast runs can execute concurrently.
- **`training`**: Model training (T.3) is resource-intensive. Concurrency of 1 prevents parallel training from exhausting memory. After training completes, T.4–T.5 (hindcast + skill) are submitted to the `hindcast` pool.

Flow 1 (forecast cycle) has an additional per-flow concurrency limit of 1 — prevents two instances of the same cycle running simultaneously on Prefect restart.

## Database migration strategy

### Tool: Alembic

- Migration files in `alembic/versions/`
- Uses `DATABASE_URL_DIRECT` (bypasses PgBouncer — see conventions.md § Database connection patterns)
- Connection used only during migration, not at runtime

### First-boot sequence

Responsibilities are split across two stages:

**PostgreSQL container** (`docker-entrypoint-initdb.d/init-db.sh`, runs once on first `pgdata` volume creation):
- Creates the `sapphire` database and installs PostGIS (and v1-only pg_partman, pg_cron extensions)
- Creates DB service users (`sapphire_api`, `sapphire_worker`, `sapphire_prefect`) with permissions per conventions.md § Database connection patterns

> **v1-only** (v0-scope.md §A1): pg_partman and pg_cron extensions are not used in v0.

**`init` service** (runs before `api` and workers start, after PostgreSQL and Prefect Server are healthy):

1. Wait for PostgreSQL and Prefect Server health checks to pass (implicit via `depends_on`)
2. Run `alembic upgrade head` — creates all tables, indexes, constraints
3. > **v1-only** (v0-scope.md §A1)
   Run `SELECT partman.run_maintenance_proc()` — creates initial partitions
4. Register Prefect deployments (`python -m sapphire_flow.cli.register_deployments`) — idempotent, updates existing deployments

`init` steps are idempotent — safe to rerun on container restart. Re-running `init` on an existing database is the expected path during upgrades (step 3 of the upgrade procedure).

**Worker/API runtime** (happens at service startup, not during `init`):

- **Configuration loading** (`config.toml`):
  - **Deployment-level bootstrap** (danger levels, season definitions, skill interpretation schemes): only runs if `deployments` table is empty (first boot). Subsequent reruns skip this — deployment-level config is managed through the application after initial setup.
  - **Station and threshold config**: upsert semantics — new entries are added, existing entries are updated if the config has changed, entries present in the database but absent from `config.toml` are left untouched (never deleted).
- **Model entry-point scanning**: populates `models` table from `pyproject.toml` entry points at worker startup.

### Upgrade procedure

1. Pull new image tag: `docker compose pull`
2. Stop workers (graceful): `docker compose stop prefect-worker` (v0 single worker; v1: `prefect-worker-ops prefect-worker-training`)
3. Run init: `docker compose run --rm init` (applies migrations)
4. Restart all: `docker compose up -d`

### Rollback

No schema downgrade path — rollback = restore from backup + redeploy previous image tag. Migrations must be backwards-compatible for one version (additive only: new columns nullable, no destructive changes in a single release). This means the previous image tag can run against the new schema during the migration window.

## Log management

### Container log driver

All containers: `json-file` with `max-size: 50m`, `max-file: 5`. Set in `docker-compose.yml` logging config. **→ DECISION (plan 013)**: At ~1000 stations, log volume scales ~20× (see line 145). For deployments exceeding ~300 stations, increase to `max-file: 10` for the worker container or route structured logs to a persistent sink to preserve diagnostic history during incidents.

### Application logging

See [`docs/standards/logging.md`](logging.md) for the full logging strategy: framework configuration, mandatory context fields (including `model_id` and `group_id` for Flow 13), event naming taxonomy, log levels, and security constraints. Summary:

- Framework: `structlog` (JSON in prod, console in dev)
- Logger per module: `structlog.get_logger(__name__)`
- No `print()` — enforced by ruff rule `T201`
- Prefect log level: `PREFECT_LOGGING_LEVEL=WARNING` in production (see `logging.md` § Prefect-specific settings for rationale)

### Caddy access logs

JSON format, auto-rotated by Caddy. Include: timestamp, client IP, method, path, status, latency.

### Prefect flow logs

Retained in Prefect database. Retention: 30 days (configured in Prefect server settings). Older logs pruned automatically. **Plan 013 note**: At ~1000 stations, Prefect DB log volume grows ~20× — monitor Prefect DB disk usage alongside application data growth.

### Disk impact

With 4 forecast cycles/day and 48 obs ingest runs/day, estimated log volume is ~100 MB/day at ~50 stations before rotation, scaling roughly linearly with station count (~340 MB/day at ~170 stations, ~2 GB/day at ~1000 stations). The `max-file: 5` x `max-size: 50m` = 250 MB cap per container. 8 containers x 250 MB = ~2 GB maximum disk usage for container logs. v0 has 6 containers (no PgBouncer, one worker instead of three). At ~1000 stations, the 250 MB cap causes logs to rotate within hours — see plan 013 DECISION on line 125.

## Systemd integration

For production VMs, Docker Compose is managed by systemd to survive reboots:

```ini
# /etc/systemd/system/sapphire-flow.service
[Unit]
Description=SAPPHIRE Flow
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=true
WorkingDirectory=/opt/sapphire
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
```

Enable: `systemctl enable sapphire-flow`. The stack starts automatically on VM boot.

## Container health checks

Specified in `docker-compose.yml` per service:

```yaml
postgres:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U sapphire"]
    interval: 10s
    timeout: 5s
    retries: 5

pgbouncer:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -h localhost -p 6432"]
    interval: 10s
    timeout: 5s
    retries: 5

prefect-server:
  healthcheck:
    test: ["CMD-SHELL", "curl -f http://localhost:4200/api/health || exit 1"]
    interval: 15s
    timeout: 10s
    retries: 3

api:
  healthcheck:
    test: ["CMD-SHELL", "curl -f http://localhost:8000/api/v1/health || exit 1"]
    interval: 15s
    timeout: 10s
    retries: 3
```

Workers do not have Docker-level health checks — their liveness is monitored by Flow 4 (watchdog) via the `PipelineStatusSource` Protocol and by the host-level cron watchdog via the `/api/v1/health` endpoint.

## Image tagging and versioning

- Image tags match the Python package version (from `pyproject.toml`)
- `docker-compose.yml` pins the image tag — never uses `:latest` in production
- CI builds and tags images on every merge to `main`
- Deployment updates the tag in `docker-compose.yml` and runs the upgrade procedure above

## Host-level watchdog

Independent of Docker and Prefect. A cron job on the host VM:

```bash
# /etc/cron.d/sapphire-watchdog
*/5 * * * * root curl -sf http://localhost:8000/api/v1/health || /opt/sapphire/scripts/alert.sh "SAPPHIRE health check failed"
```

`alert.sh` sends a notification (email or SMS) directly using system tools (`sendmail`, `curl` to SMS API). This is the last-resort alerting mechanism — it works even when Docker, Prefect, and the application are all down (as long as the VM is up). See architecture-context.md § Backup and disaster recovery for the health endpoint specification.
