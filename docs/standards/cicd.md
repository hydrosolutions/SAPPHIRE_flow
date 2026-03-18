# CI/CD and Deployment Standards

> This document extends `docs/architecture-context.md`. It adds deployment implementation detail. For foundational decisions, see: tech stack (architecture-context.md § Tech stack), DB connection patterns (conventions.md § Database connection patterns), cold storage layout (architecture-context.md § Data retention and cold storage), backup plan (architecture-context.md § Backup and disaster recovery). This document does not redefine the tech stack, schema designs, or data flow definitions.
>
> **v0 simplifications**: See [`docs/v0-scope.md`](../v0-scope.md) § A3 (no PgBouncer), § A1 (no partitioning/DLQ), § A2 (no cold storage), § A6 (single work pool), § A10 (simple backup), § F (simplified Docker topology).

## Docker Compose service topology

Single VM deployment. All services in one `docker-compose.yml`.

### Services

| Service | Image | Depends on | Health check | Restart |
|---------|-------|-----------|-------------|---------|
| `postgres` | `postgres:16` + PostGIS | — | `pg_isready -U sapphire` | `unless-stopped` |
| `pgbouncer` | `pgbouncer/pgbouncer` | postgres (healthy) | `pg_isready -h localhost -p 6432` | `unless-stopped` |
| `prefect-server` | `prefecthq/prefect:3-python3.11` | postgres (healthy) | `curl -f http://localhost:4200/api/health` | `unless-stopped` |
| `prefect-worker-ops` | custom (sapphire-flow) | prefect-server, pgbouncer | — | `unless-stopped` |
| `prefect-worker-hindcast` | custom (sapphire-flow) | prefect-server, pgbouncer | — | `unless-stopped` |
| `prefect-worker-training` | custom (sapphire-flow) | prefect-server, pgbouncer | — | `unless-stopped` |
| `api` | custom (sapphire-flow) | pgbouncer, prefect-server | `curl -f http://localhost:8000/api/v1/health` | `unless-stopped` |
| `caddy` | `caddy:2` | api | TCP check on 443 | `unless-stopped` |
| `init` | custom (sapphire-flow) | postgres (healthy) | — | `no` (one-shot) |

### Custom image

One Dockerfile for `prefect-worker-ops`, `prefect-worker-hindcast`, `prefect-worker-training`, `api`, and `init`. Different entrypoints select the role. Base: `python:3.11-slim`.

### Named volumes

| Volume | Mount path | Used by | Purpose |
|--------|-----------|---------|---------|
| `pg_data` | `/var/lib/postgresql/data` | postgres | PostgreSQL data directory |
| `model_artifacts` | `/data/artifacts` | prefect-worker-ops (ro), prefect-worker-hindcast (ro), prefect-worker-training (rw), api (ro) | Trained model files |
| `cold_storage` | `/data/cold` | prefect-worker-ops (rw), prefect-worker-hindcast (ro), api (ro) | Parquet archive |
| `prefect_data` | `/data/prefect` | prefect-server | Prefect server state |

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

All `depends_on` use `condition: service_healthy` where health checks are defined.

## Prefect work pool separation

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

- Migration files in `src/sapphire_flow/migrations/`
- Uses `DATABASE_URL_DIRECT` (bypasses PgBouncer — see conventions.md § Database connection patterns)
- Connection used only during migration, not at runtime

### First-boot sequence

The `init` service runs before `api` and workers start:

1. Wait for PostgreSQL health check to pass
2. Create database and extensions: `CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pg_partman; CREATE EXTENSION IF NOT EXISTS pg_cron;`
3. Create DB service users (`sapphire_api`, `sapphire_worker`, `sapphire_prefect`) with permissions per conventions.md § Database connection patterns
4. Run `alembic upgrade head` — creates all tables, indexes, constraints
5. Run `SELECT partman.run_maintenance_proc()` — creates initial partitions
6. If `deployments` table is empty: run bootstrap import from `config.toml` (danger levels, season definitions, skill interpretation schemes)
7. Scan model entry points and populate `models` table

Steps are idempotent — safe to rerun on container restart.

### Upgrade procedure

1. Pull new image tag: `docker compose pull`
2. Stop workers (graceful): `docker compose stop prefect-worker-ops prefect-worker-training`
3. Run init: `docker compose run --rm init` (applies migrations)
4. Restart all: `docker compose up -d`

### Rollback

No schema downgrade path — rollback = restore from backup + redeploy previous image tag. Migrations must be backwards-compatible for one version (additive only: new columns nullable, no destructive changes in a single release). This means the previous image tag can run against the new schema during the migration window.

## Log management

### Container log driver

All containers: `json-file` with `max-size: 50m`, `max-file: 5`. Set in `docker-compose.yml` logging config.

### Application logging

See [`docs/standards/logging.md`](logging.md) for the full logging strategy: framework configuration, mandatory context fields, event naming taxonomy, log levels, and security constraints. Summary:

- Framework: `structlog` (JSON in prod, console in dev)
- Logger per module: `structlog.get_logger(__name__)`
- No `print()` — enforced by ruff rule `T201`

### Caddy access logs

JSON format, auto-rotated by Caddy. Include: timestamp, client IP, method, path, status, latency.

### Prefect flow logs

Retained in Prefect database. Retention: 30 days (configured in Prefect server settings). Older logs pruned automatically.

### Disk impact

With 4 forecast cycles/day and 48 obs ingest runs/day, estimated log volume is ~100 MB/day before rotation. The `max-file: 5` x `max-size: 50m` = 250 MB cap per container. 8 containers x 250 MB = ~2 GB maximum disk usage for container logs.

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
