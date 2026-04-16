# Plan 044 — Deployment Readiness (v0 Test Server)

**Status**: READY
**Phase**: 10b (infrastructure hardening)
**Depends on**: Phase 10 (Docker Compose, DONE), all flows (Phases 5-9, DONE)

## Context

Phases 1-10 are complete. All pipeline code works locally and in CI. Phase 11
(e2e test, Plan 043) is in progress separately. But `docker compose up` on a
fresh VM would fail for two hard blockers, and several gaps prevent operational
use. This plan closes the gap between "pipeline code is tested" and "you can
deploy on a VM and have a running forecast system."

---

## Stream A — Blockers (deploy would crash)

### Step 1 — Dockerfile: add alembic files

**File**: `Dockerfile`

The `init` container runs `alembic upgrade head` but the image has no `alembic/`
directory or `alembic.ini`. Only `src/` is copied.

**Fix**: In builder stage, after `COPY src/ src/`:
```dockerfile
COPY alembic.ini ./
COPY alembic/ alembic/
```
In runtime stage, after existing `COPY --from=builder` lines:
```dockerfile
COPY --from=builder --chown=app:app /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=app:app /app/alembic /app/alembic
```

### Step 2 — Init container: fix DB driver

**File**: `docker-compose.yml` line 176

`alembic/env.py` uses synchronous `engine_from_config()`. The init service has
`DATABASE_URL_TEMPLATE: postgresql+asyncpg://...` — incompatible. The API service
already correctly uses `postgresql+psycopg://`.

**Fix**: Change init's `DATABASE_URL_TEMPLATE` to `postgresql+psycopg://...`.

---

## Stream B — Prefect deployment registration + backup

### Step 3 — Deployment registration script

**Files**: `src/sapphire_flow/cli/__init__.py` (create, empty),
`src/sapphire_flow/cli/register_deployments.py` (create)

Per orchestration.md § Deployment registration, the init service registers all
v0 Prefect deployments after migrations. Currently it only runs alembic.

**API**: Prefect 3.6 `flow.deploy()` with `build=False, push=False` (code is
already installed in the image — no Docker build needed). Each call is idempotent
(creates or updates the deployment).

**v0 deployment specs** (from orchestration.md):

| Flow function | Deployment name | Schedule | Concurrency |
|---|---|---|---|
| `ingest_observations_flow` | `ingest-observations` | `*/10 * * * *` | — |
| `run_forecast_cycle_flow` | `forecast-cycle` | `0 */6 * * *` | 1 |
| `backup_database_flow` | `backup-database` | `0 2 * * *` | — |
| `train_models_flow` | `train-models` | (on-demand) | 1 |
| `run_hindcast_flow` | `run-hindcast` | (on-demand) | — |
| `compute_skills_flow` | `compute-skills` | (on-demand) | — |
| `compute_combined_skills_flow` | `compute-combined-skills` | (on-demand) | — |
| `onboard_stations_flow` | `onboard-stations` | (on-demand) | — |
| `onboard_model_flow` | `onboard-model` | (on-demand) | 1 |

Cron schedules read from env vars with defaults:
- `SCHEDULE_INGEST_OBSERVATIONS` (default `*/10 * * * *`)
- `SCHEDULE_FORECAST_CYCLE` (default `0 */6 * * *`)
- `SCHEDULE_BACKUP_DATABASE` (default `0 2 * * *`)

Script is invocable via `python -m sapphire_flow.cli.register_deployments`.

### Step 4 — Backup flow

**File**: `src/sapphire_flow/flows/backup.py` (create)

Per v0-scope § A10: `pg_dump` to local disk with rotation.

```python
@flow(name="backup-database", log_prints=False)
def backup_database_flow(backup_dir: str = "/data/backups", keep_count: int = 7) -> str:
    path = dump_database_task(backup_dir)       # pg_dump --format=custom
    cleanup_old_backups_task(backup_dir, keep_count)  # remove oldest beyond keep_count
    return path
```

- `dump_database_task`: `subprocess.run(["pg_dump", ...])`, reads `DATABASE_URL`
  from env. File: `sapphire_YYYYMMDD_HHMMSS.dump`.
- `cleanup_old_backups_task`: list `*.dump`, sort by mtime, unlink oldest.

### Step 5 — Docker Compose wiring

**File**: `docker-compose.yml`

1. Add `backups` named volume
2. Mount `backups:/data/backups:rw` on `prefect-worker`
3. Update `init` command:
   ```yaml
   command: >
     sh -c "
       alembic upgrade head &&
       python -m sapphire_flow.cli.register_deployments &&
       echo 'Init complete'
     "
   ```
4. Add schedule env vars to `init` service environment (operator-configurable)
5. Ensure `init` has `PREFECT_API_URL` (already present)

---

## Stream C — Operational polish

### Step 6 — Caddyfile: configurable domain

**File**: `Caddyfile`

Replace `:80` with `{$SAPPHIRE_DOMAIN::80}` (Caddy env var syntax with default).
When `SAPPHIRE_DOMAIN=sapphire.example.ch` is set, Caddy auto-provisions Let's
Encrypt TLS. Without it, falls back to plain HTTP on port 80.

### Step 7 — CORS env var

**File**: `docker-compose.yml` (API service environment)

CORS is already env-var-driven (`SAPPHIRE_CORS_ORIGINS` in
`src/sapphire_flow/api/__init__.py:23`). Just not wired in docker-compose.yml.

Add to API environment:
```yaml
SAPPHIRE_CORS_ORIGINS: ${SAPPHIRE_CORS_ORIGINS:-*}
```

Default `*` is acceptable for v0 (no auth, test server).

### Step 8 — Secrets bootstrap guide

**File**: `docs/secrets-bootstrap.md` (create)

Currently only `secrets/db_password` is consumed by docker-compose.yml. Document:
- What files to create in `secrets/`
- Generation command (`openssl rand -base64 32`)
- Permissions (`chmod 700 secrets; chmod 600 secrets/*`)

### Step 9 — v0 deployment quick-start

**File**: `docs/deployment-quickstart.md` (create)

Concise operational runbook for v0 Swiss test deployment:
1. Prerequisites (Docker, git clone)
2. Secrets setup (pointer to secrets-bootstrap.md)
3. Optional config (domain, CORS, schedules via `.env`)
4. Build + start (`docker compose build && docker compose up -d`)
5. Verify (health endpoint, Prefect UI via SSH tunnel)
6. First data load (trigger onboard-stations via Prefect UI)
7. Monitoring (logs, health endpoint)
8. Backup / restore procedure

Distinct from `docs/handover/it-operations.md` (Nepal/v1-focused).

---

## Dependency graph

```json
{
  "stream-a": {
    "tasks": ["1_dockerfile_alembic", "2_init_db_driver"],
    "parallel": true
  },
  "stream-b": {
    "tasks": ["3_register_deployments", "4_backup_flow"],
    "parallel": true,
    "then": ["5_compose_wiring"]
  },
  "stream-c": {
    "tasks": ["6_caddyfile", "7_cors", "8_secrets_guide"],
    "parallel": true,
    "then": ["9_quickstart"]
  }
}
```

Streams A, B, C are independent. Within each stream:
- A: steps 1 and 2 are independent (parallel)
- B: steps 3 and 4 are independent; step 5 depends on both
- C: steps 6, 7, 8 are independent; step 9 depends on all

---

## Verification

After all steps, on a clean machine with Docker:

1. `docker compose build` — no errors
2. `docker compose up -d` — all services healthy within 60s
3. `docker compose logs init` — shows alembic success + deployment registration
4. `curl http://localhost/api/v1/health` — returns `{"status":"ok"}`
5. Prefect UI (SSH tunnel to 4200) shows 9 deployments registered
6. Manual trigger of `backup-database` from Prefect UI produces a `.dump` file

---

## Files touched

| File | Action |
|------|--------|
| `Dockerfile` | Edit (add COPY alembic) |
| `docker-compose.yml` | Edit (fix init driver, add backup volume, update init command, add env vars) |
| `Caddyfile` | Edit (configurable domain) |
| `src/sapphire_flow/cli/__init__.py` | Create |
| `src/sapphire_flow/cli/register_deployments.py` | Create |
| `src/sapphire_flow/flows/backup.py` | Create |
| `docs/secrets-bootstrap.md` | Create |
| `docs/deployment-quickstart.md` | Create |
