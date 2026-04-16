# Plan 044 — Deployment Readiness (v0 Test Server)

**Status**: DONE
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

The `init` container runs `alembic upgrade head` but the image had no `alembic/`
directory or `alembic.ini` — only `src/` was copied.

**Applied**: In builder stage (lines 13-14), after `COPY src/ src/`:
```dockerfile
COPY alembic.ini ./
COPY alembic/ alembic/
```
In runtime stage (lines 31-32), after existing `COPY --from=builder` lines:
```dockerfile
COPY --from=builder --chown=app:app /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=app:app /app/alembic /app/alembic
```

### Step 2 — Init container: fix DB driver

**File**: `docker-compose.yml`

`alembic/env.py` uses synchronous `engine_from_config()`. The init service had
`DATABASE_URL_TEMPLATE: postgresql+asyncpg://...` — incompatible. The API service
already correctly uses `postgresql+psycopg://`.

**Applied**: Changed init's `DATABASE_URL_TEMPLATE` to `postgresql+psycopg://...`.

> **Note**: `prefect-worker` correctly keeps `postgresql+asyncpg://` — it uses
> SQLAlchemy async sessions, not Alembic. Do not unify drivers across services.

---

## Stream B — Prefect deployment registration + backup

### Step 3 — Deployment registration script

**Files**: `src/sapphire_flow/cli/__init__.py` (created, empty),
`src/sapphire_flow/cli/register_deployments.py` (created)

Per orchestration.md § Deployment registration, the init service registers all
v0 Prefect deployments after migrations. Previously it only ran alembic.

**API**: Prefect 3.6 `flow.adeploy()` with `build=False, push=False` (code is
already installed in the image — no Docker build needed). Each call is idempotent
(creates or updates the deployment).

**v0 deployment specs** (from orchestration.md):

| Flow function | Deployment name | Schedule | Concurrency |
|---|---|---|---|
| `ingest_observations_flow` | `ingest-observations` | `*/30 * * * *` | — |
| `run_forecast_cycle_flow` | `forecast-cycle` | `0 */6 * * *` | 1 |
| `backup_database_flow` | `backup-database` | `0 2 * * *` | — |
| `train_models_flow` | `train-models` | (on-demand) | 1 |
| `run_hindcast_flow` | `run-hindcast` | (on-demand) | — |
| `compute_skills_flow` | `compute-skills` | (on-demand) | — |
| `compute_combined_skills_flow` | `compute-combined-skills` | (on-demand) | — |
| `onboard_stations_flow` | `onboard-stations` | (on-demand) | — |
| `onboard_model_flow` | `onboard-model` | (on-demand) | 1 |

Cron schedules read from env vars with defaults:
- `SCHEDULE_INGEST_OBSERVATIONS` (default `*/30 * * * *`) — matches orchestration.md
  + cicd.md "48 obs ingest runs/day." Operators wanting near-real-time can override
  to `*/10 * * * *` (the architecture-context.md aspiration).
- `SCHEDULE_FORECAST_CYCLE` (default `0 */6 * * *`)
- `SCHEDULE_BACKUP_DATABASE` (default `0 2 * * *`)

Script is invocable via `python -m sapphire_flow.cli.register_deployments`.

> **Note**: `onboard-weather-stations` and `reprocess-observations` are absent because
> the flow code doesn't exist yet. Added to the registration script when the flows land
> (per orchestration.md § Deployment registration).

### Step 4 — Backup flow

**File**: `src/sapphire_flow/flows/backup.py` (created)

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
- `postgresql-client` is installed in the Docker image (`Dockerfile` line 21) to
  provide `pg_dump` and `pg_restore`.

> **v0c TODO**: When Flow 4 (pipeline monitoring) is implemented, add a marker
> file write after successful dump (e.g. `/data/backups/.last_backup_ok`) so
> step 4.10 (`check_type = 'backup_freshness'`) can verify backup recency.

### Step 5 — Docker Compose wiring

**File**: `docker-compose.yml`

1. Added `backups` named volume
2. Mounted `backups:/data/backups:rw` on `prefect-worker`
3. Updated `init` command:
   ```yaml
   command: >
     sh -c "
       alembic upgrade head &&
       python -m sapphire_flow.cli.register_deployments &&
       echo 'Init complete'
     "
   ```
4. Added schedule env vars to `init` service environment (operator-configurable)
5. `init` has `PREFECT_API_URL` (already present)

---

## Stream C — Operational polish

### Step 6 — Caddyfile: configurable domain

**File**: `Caddyfile`

Replaced `:80` with `{$SAPPHIRE_DOMAIN::80}` (Caddy env var syntax with default).
When `SAPPHIRE_DOMAIN=sapphire.example.ch` is set, Caddy auto-provisions Let's
Encrypt TLS and applies HSTS automatically. Without it, falls back to plain HTTP
on port 80 (documented as v0 exception in security.md § Network policy).

Prefect UI is **not** routed through Caddy — it is internal-only per security.md
§ Network policy. Access via SSH tunnel only: `ssh -L 4200:localhost:4200 user@vm`.

### Step 7 — CORS env var

**File**: `docker-compose.yml` (API service environment)

CORS is already env-var-driven (`SAPPHIRE_CORS_ORIGINS` in
`src/sapphire_flow/api/__init__.py:23`). Wired in docker-compose.yml:

```yaml
SAPPHIRE_CORS_ORIGINS: ${SAPPHIRE_CORS_ORIGINS:-*}
```

Default `*` is acceptable for v0 (no auth, test server). Documented as v0
exception in docker-compose.yml and security.md § CORS policy.

### Step 8 — Secrets bootstrap guide

**File**: `docs/secrets-bootstrap.md` (created)

Currently only `secrets/db_password` is consumed by docker-compose.yml. Documents:
- What files to create in `secrets/`
- Generation command: `openssl rand -base64 32 | tr -d '\n'` — the `tr -d '\n'`
  is critical because a trailing newline corrupts the `DATABASE_URL` constructed by
  `entrypoint.sh` (which uses `cat /run/secrets/db_password` in URL interpolation).
- Permissions (`chmod 700 secrets; chmod 600 secrets/*`)
- v1 additional secrets table (JWT signing key, TOTP encryption key)

### Step 9 — v0 deployment quick-start

**File**: `docs/deployment-quickstart.md` (created)

Concise operational runbook for v0 Swiss test deployment:
1. Prerequisites (Docker, git clone)
2. Secrets setup (pointer to secrets-bootstrap.md)
3. Optional config (domain, CORS, schedules via `.env`)
4. Build + start (`docker compose build && docker compose up -d`)
5. Verify (health endpoint, Prefect UI via SSH tunnel)
6. First data load (trigger onboard-stations via Prefect UI)
7. Monitoring (logs, health endpoint)
8. Backup / restore procedure — restore runs from `prefect-worker` container
   (which has both `pg_restore` and the `backups` volume), not from `postgres`
9. Upgrade procedure — matches cicd.md: graceful worker stop → `run --rm init`
   → restart all

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
2. `docker compose up -d` — all services healthy within 90s (prefect-server has
   `start_period: 90s` to allow for first-boot DB migrations)
3. `docker compose logs init` — shows alembic success + deployment registration
4. `curl http://localhost/api/v1/health` — returns `{"status":"ok"}`
5. Prefect UI (SSH tunnel to 4200) shows 9 deployments registered
6. Manual trigger of `backup-database` from Prefect UI produces a `.dump` file
7. Restore procedure from quickstart §8 succeeds against the dump from step 6
8. Upgrade procedure from quickstart §9 re-runs migrations idempotently

---

## Files touched

| File | Action |
|------|--------|
| `Dockerfile` | Edit (add COPY alembic) |
| `docker-compose.yml` | Edit (fix init driver, add backup volume, update init command, add env vars, add prefect-server `start_period`, add worker `PREFECT_LOGGING_LEVEL`) |
| `Caddyfile` | Edit (configurable domain, v0 HTTP comment, remove Prefect UI route) |
| `src/sapphire_flow/cli/__init__.py` | Create |
| `src/sapphire_flow/cli/register_deployments.py` | Create |
| `src/sapphire_flow/flows/backup.py` | Create |
| `docs/secrets-bootstrap.md` | Create |
| `docs/deployment-quickstart.md` | Create |
| `docs/standards/security.md` | Edit (v0 port 80 exception in § Network policy) |
| `docs/standards/cicd.md` | Edit (v0 worker name in upgrade procedure) |

---

## Post-implementation review (2026-04-16)

Issues found by critical review against standards docs (`orchestration.md`,
`cicd.md`, `security.md`) and `v0-scope.md`. All fixed in-place — the step
descriptions above incorporate the fixes.

| # | Issue | Severity | Fix |
|---|-------|----------|-----|
| H1 | `prefect-server` healthcheck missing `start_period` — cold-start failure on real VM when Prefect runs its own DB migrations (30-60s) | High | Added `start_period: 90s`, bumped `retries: 5` |
| H2 | Prefect UI routed via Caddy (`/prefect/*`) — violates security.md § Network policy (internal-only, SSH tunnel) and broken SPA asset loading (root-relative paths) | High | Removed Caddy route; SSH tunnel documented in quickstart |
| H3 | Restore procedure in quickstart wrong — `postgres` container doesn't mount `backups` volume, missing `--clean` flag | High | Rewrote to use `prefect-worker` (has `pg_restore` + volume) |
| H4 | Ingest schedule default `*/10` (144 runs/day) contradicts orchestration.md + cicd.md (48 runs/day = `*/30`) | High | Changed to `*/30` in code, compose, plan, quickstart |
| M1 | Upgrade procedure uses `up -d init` instead of cicd.md `run --rm init`; missing graceful worker stop | Medium | Fixed quickstart to match cicd.md |
| M2 | `openssl rand -base64 32` trailing newline corrupts `DATABASE_URL` via `entrypoint.sh` | Medium | Added `tr -d '\n'` in `secrets-bootstrap.md` and quickstart |
| M3 | Port 80 HTTP exposure not flagged as v0 exception (security.md says 443-only) | Medium | Added v0 exception to security.md § Network policy and Caddyfile comment |
| L1 | `PREFECT_LOGGING_LEVEL=WARNING` missing from `prefect-worker` (cicd.md mandatory for production) | Low | Added to `docker-compose.yml` |
| L3 | cicd.md upgrade procedure references v1 worker names (`prefect-worker-ops/training`) | Low | Updated to show v0 name with v1 note |

### Second review pass (2026-04-16)

Issues found by critical review against `security.md`, `cicd.md`,
`orchestration.md`, and `logging.md`. Fixed in commit `edd2743`.

| # | Issue | Severity | Fix |
|---|-------|----------|-----|
| B1 | `pg_dump` not installed in Docker image (`Dockerfile` only had `gosu curl`) | Blocker | Added `postgresql-client` to `apt-get install` |
| S1 | `pg_dump` password exposed in CLI args (visible in `/proc/<pid>/cmdline`) | Medium | Parse URL, pass components as flags, inject password via `PGPASSWORD` env var |
| S2 | `backup.completed` missing `duration_ms` (logging standard D6 violation) | Low | Added `time.perf_counter()` timing to `dump_database_task` |
| S3 | Dump files created with default umask `0644` (world-readable) | Low | Added `dump_file.chmod(0o600)` after successful dump |
| D1 | cicd.md volume table missing `backups`, `sapphire_data`, `caddy_data`, `caddy_config` | Low | Added 4 volumes to table |
| D2 | cicd.md init sequence listed config loading and model scanning as init steps (they run at worker/API runtime) | Low | Restructured into separate "Worker/API runtime" section |
| D3 | orchestration.md flow function names missing `_flow` suffix | Low | Updated all 8 names to match codebase |
| D4 | orchestration.md missing `compute_combined_skills_flow` row | Low | Added row |
| D5 | `secrets-bootstrap.md` missing `chown root:root` (security.md requires root ownership) | Low | Added `sudo chown -R root:root secrets/` |
| D6 | `deployment-quickstart.md` missing CORS restriction warning for v1 | Low | Added blockquote warning |
| D7 | `security.md` CORS "never `*`" contradicts v0 default | Low | Softened to "never `*` in production", added v0 exception note |
