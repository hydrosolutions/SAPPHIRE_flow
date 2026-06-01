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

> Supply-chain policy — digest pinning, CI action SHA pins, CVE scanning, SBOM generation, and dependency-update automation — is defined in [`docs/standards/security.md`](security.md) § Supply chain. This subsection describes the **operational workflow** around those rules. It does not duplicate the normative policy; consult `security.md` for the authoritative controls.

Three distinct concepts live under "image tagging" in this repo. They are not interchangeable, and only the first is an operator-managed tag-bump workflow.

### Locally built `sapphire-flow` app image — version tags

- Tag format: `sapphire-flow:${VERSION}`, where `${VERSION}` matches the Python package version in `pyproject.toml` / `src/sapphire_flow/__init__.py`.
- `${VERSION}` is bumped by `bump-my-version` on every commit per the `CLAUDE.md` version-bumping rule.
- Operators set the `${VERSION}` env var (via `.env` or compose overrides) when deploying a new build, then run the upgrade procedure above.
- This is the only image reference in the stack that moves on a normal deploy cadence.

### Third-party image references — digest pins, not version tags

Third-party images are pinned by **manifest-list digest** (`image:tag@sha256:...`) rather than by a floating tag. The full list and policy live in `security.md` § Supply chain → Image pinning; the operational shape is:

- `Dockerfile` builder + runtime stages — `python:3.11.12-slim@sha256:...` and `ghcr.io/astral-sh/uv:0.11.7@sha256:...`.
- `docker-compose.yml` — `postgis/postgis`, `prefecthq/prefect`, `caddy` all digest-pinned.
- `.github/workflows/ci.yml` integration-services block — `postgis/postgis` digest-pinned, kept in sync with the compose digest.

Digest pins are **reviewed immutable references**, not operational version tags. They move only through Dependabot PRs under review (Dependabot's `docker` and `docker-compose` ecosystems), never by operators at deploy time. The `bump-my-version` workflow that governs the local `sapphire-flow:${VERSION}` tag does **not** apply to these external images.

### `:latest` — forbidden, superseded by digest pinning

`:latest` (or any other floating tag) continues to be forbidden in any compose file or Dockerfile. The stronger rule since Plan 064 is **digest pinning** for all externally-pulled images, documented in [`security.md`](security.md) § Supply chain → Image pinning. Digest pinning supersedes the bare `:latest` prohibition: a digest reference is immutable by construction, whereas a non-`:latest` tag (e.g. `postgis:16-3.4`) is still a mutable pointer to whatever the upstream publisher currently serves.

### CI validation builds vs a publish / release workflow

CI builds the `sapphire-flow` image on every pull request under the `build-image-and-scan` job (see § CI workflow tiers below). The tag format there is `sapphire-flow:ci-${{ github.sha }}` — purely local to the CI runner, used to exercise the Dockerfile, run `trivy image`, and feed `syft` for SBOM generation. The image is discarded when the runner terminates; it is never pushed, tagged for release, or attached to a registry.

**No image publish / release workflow is shipped today.** CI does not build and push on merges to `main`; the `${VERSION}` tag on the compose `sapphire-flow` image is set by the operator at deploy time (from `pyproject.toml`), not produced by CI. A future plan may add a registry-publish workflow — at which point this section will gain a "published release images" subsection. Until then, treat any claim that CI produces deployable image tags as out of date.

(Plan 053's `## Future work` section deferred base-image digest pinning to a dedicated plan; [Plan 064](../plans/064-supply-chain-hardening.md) is the implementation record for the pins, CI build/scan tier, and SBOM artifact described here and in `security.md`.)

## Gate lifecycle: developer edit → CI → merge

<!-- Added by Plan 070 §D-Final-Pass — consolidates A3 (CLAUDE.md pre-commit section),
     B2 (uv run check subsection), and C1 (extended tiers table) into one narrative. -->

Two gates protect every change before it reaches `main`:

1. **Developer-tier gate (pre-commit)** — fast, local, fires automatically before `git commit` completes. Catches lint, format, and secret-pattern issues the moment they are written, before they reach a branch.
2. **CI gate (GitHub Actions)** — thorough, remote, fires on push and PR. Catches what pre-commit misses: integration tests (require postgres + system deps), image builds, Trivy CVE scans, SBOM generation, and the wheel-only guard.

**The parity invariant**: a developer should never push a commit that CI will reject for a reason their local environment could not have surfaced first.

### Full lifecycle flow

```
developer edits file
        │
        ▼
[pre-commit hooks — on git commit]
  • trailing-whitespace / end-of-file-fixer (mutating hygiene)
  • ruff format --check  (check-only, no auto-fix)
  • ruff check           (check-only, no auto-fix)
  • gitleaks             (secret-pattern scan)
        │ blocks commit on failure
        ▼
developer runs uv run check  (optional, pre-push confidence)
  • ruff format --check src/ tests/
  • ruff check src/ tests/
        │ mirrors the CI lint job's ruff steps
        ▼
git push / open PR
        │
        ▼
[CI gate — GitHub Actions ci.yml]
  Tier 1 lint     → ruff format --check, ruff check, trivy fs
  Tier 2 unit     → pytest tests/unit/ (system deps + postgres service)
  Tier 2 wheel    → wheel-only-guard (no-build uv sync)
  Tier 3 integration → pytest tests/integration/ (postgres service)
  Tier 4 build    → docker buildx build, trivy image, syft SBOM
  (Tier 5 e2e)    → not yet implemented
        │ all tiers green → PR is mergeable
        ▼
merge to main
```

Scheduled workflows run outside this push/PR path: `integration-nightly.yml` (03:00 UTC daily) covers `@pytest.mark.slow` and live-API tests; `live-lindas-weekly.yml` (Mondays 06:00 UTC) runs the BAFU LINDAS schema check. Both accept `workflow_dispatch` for out-of-cycle runs. First-fire run IDs are recorded in workflow header comments; see `.github/workflows/integration-nightly.yml` and `live-lindas-weekly.yml` headers.

### Known external-dependency caveats

The `live-lindas-weekly.yml` Monday 06:00 UTC schedule has exhibited intermittent failures (2 of 3 observed Monday-schedule runs failed as of 2026-05-11). Root cause is an upstream BAFU LINDAS publishing-pipeline issue, not a workflow defect — BAFU republishes the dataset later in the day, after which the same test passes. See [`docs/decisions/bafu-lindas-monday-window.md`](../decisions/bafu-lindas-monday-window.md) for the per-run evidence record. BAFU support contact: `abfragezentrale@bafu.admin.ch`.

### Cross-references

- `CLAUDE.md` §Pre-commit hooks — per-contributor install instructions, hook policy, and the check-only rationale.
- [`docs/plans/070-precommit-and-gate-parity.md`](../plans/070-precommit-and-gate-parity.md) — the plan that introduced the developer-tier gate and `uv run check`. Also defines the deferred A4 task (pyright ratchet as a pre-commit hook, triggers when Plan 069 Phase 1 lands).
- [`docs/plans/064-supply-chain-hardening.md`](../plans/064-supply-chain-hardening.md) — predecessor plan that surfaced the "wired but unrun" gate problem this plan fixes. Introduced Trivy image scan, SBOM generation, and CI action SHA pinning.
- [`docs/plans/069-pyright-backlog-cleanup.md`](../plans/069-pyright-backlog-cleanup.md) — DRAFT follow-on that re-enables pyright as a pre-commit ratchet (Plan 070 task A4, deferred until Phase 1 of Plan 069 lands).

See the CI workflow tiers table below for the full per-step breakdown of every `run:` command across all three workflow files, including local equivalents and CI-only reasons.

## CI workflow tiers

This subsection describes the operational topology of `.github/workflows/ci.yml`. Policy rationale (why each scan exists, what severity thresholds apply, what the `.trivyignore` discipline is) lives in [`security.md`](security.md) § Supply chain.

<!-- Extended by Plan 070 §C1 — two new columns + per-run-step rows. -->
<!-- "Local equivalent" = command a developer types locally to reproduce. -->
<!-- "CI only? Reason" = "No" when there is a real local equivalent; "Yes — <reason>" when not. -->

| Tier | Job | `run:` step | Depends on | Local equivalent | CI only? Reason |
|------|-----|-------------|------------|-----------------|-----------------|
| **ci.yml** | | | | | |
| 1 | `lint` | `uv sync --frozen` | — | `uv sync` (developers typically have a synced venv already) | No |
| 1 | `lint` | `uv run ruff check src/ tests/` | — | `uv run ruff check src/ tests/` (also via `uv run check` and pre-commit) | No |
| 1 | `lint` | `uv run ruff format --check src/ tests/` | — | `uv run ruff format --check src/ tests/` (also via `uv run check` and pre-commit) | No |
| 1 | `lint` | `shellcheck scripts/launchd/start-sapphire.sh scripts/launchd/watchdog.sh scripts/launchd/install-launchd.sh scripts/bootstrap-mac-mini.sh` | — | Same command (also via pre-commit `shellcheck`) | No |
| 1 | `lint` | `uv run pyright --outputjson src/ > /tmp/pyright.json \|\| true` | — | `uv run pyright src/` | No |
| 1 | `lint` | `uv run python tools/pyright_ratchet.py /tmp/pyright.json tools/pyright_baseline.json` | — | `uv run pyright src/` (then compare against `tools/pyright_baseline.json`) | No |
| 1 | `lint` | `aquasecurity/trivy-action` (fs scan, `uses:`) | — | `trivy fs --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed --scanners vuln --skip-dirs .venv .` | No (but requires trivy installed) |
| 2 | `unit` | Install system deps for cfgrib / rioxarray / exactextract | — | Brew/apt on the dev host (developer responsibility) | Yes — system-package install, not project-managed |
| 2 | `unit` | `uv sync --frozen` | — | `uv sync` | No |
| 2 | `unit` | `uv run pytest tests/unit/ --cov=src/sapphire_flow --cov-report=term-missing -v` | — | `uv run pytest tests/unit/` (requires system deps above) | No (but requires system deps) |
| 2 | `wheel-only-guard` | `uv sync --frozen --no-build --no-cache --no-install-project` | — | `uv sync --frozen --no-build --no-cache --no-install-project` | No |
| 3 | `integration` | Install system deps for cfgrib / rioxarray / exactextract | `unit` | Brew/apt on the dev host (developer responsibility) | Yes — system-package install, not project-managed |
| 3 | `integration` | `uv sync --frozen` | `unit` | `uv sync` | No |
| 3 | `integration` | `uv run pytest tests/integration/ -v -m "not slow"` | `unit` | `uv run pytest tests/integration/ -v -m "not slow"` (requires postgres service + system deps) | No (but requires postgres) |
| 4 | `build-image-and-scan` | `docker/build-push-action` (`uses:`) — build app image | `unit` | `docker buildx build -f Dockerfile -t sapphire-flow:local .` | No (but requires Docker daemon) |
| 4 | `build-image-and-scan` | `aquasecurity/trivy-action` (image scan, `uses:`) | `unit` | `trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed sapphire-flow:local` | No (but requires the image to be built + trivy installed) |
| 4 | `build-image-and-scan` | `anchore/sbom-action` (`uses:`) — generate SBOM with syft | `unit` | `syft sapphire-flow:local -o cyclonedx-json > sbom.cdx.json` | No (but requires syft installed) |
| 5 | `e2e` | _(not yet implemented — dangling comment at line 206 of ci.yml)_ | `unit`, `integration`, `build-image-and-scan` | n/a | n/a |
| **integration-nightly.yml** | | | | | |
| N | `integration-nightly` | Install system deps for cfgrib / rioxarray / exactextract | — | Brew/apt on the dev host (developer responsibility) | Yes — system-package install, not project-managed |
| N | `integration-nightly` | `uv sync --frozen` | — | `uv sync` | No |
| N | `integration-nightly` | `uv run pytest tests/integration/ -v -m "slow" --timeout=3600` | — | `uv run pytest tests/integration/ -v -m "slow"` (requires postgres + system deps) | No (but requires postgres + system deps) |
| N | `integration-nightly` | `uv run pytest tests/integration/live -v --timeout=3600 --override-ini "addopts="` | — | `uv run pytest tests/integration/live -v --override-ini "addopts="` (live external APIs) | No (but requires network + live external APIs) |
| **live-lindas-weekly.yml** | | | | | |
| W | `live-lindas-schema` | `uv sync --frozen` (Install dependencies) | — | `uv sync` | No |
| W | `live-lindas-schema` | `uv run pytest -m live_lindas -v` (Run live LINDAS schema check) | — | `uv run pytest -m live_lindas -v` (live external API) | No (but requires network + BAFU LINDAS up) |
| **live-lindas-weekly-autoretry.yml** | | | | | |
| Scheduled (event-driven) | `retry` | `gh run list ...` (Cap retries at 12 per day) | live-lindas-weekly.yml failure | n/a (event-triggered automation) | Yes — automation responding to a workflow event |
| Scheduled (event-driven) | `retry` | `sleep 300` (Wait 5 minutes for BAFU LINDAS to recover) | n/a | n/a (event-triggered automation) | Yes — bounded wait for upstream recovery |
| Scheduled (event-driven) | `retry` | `gh workflow run live-lindas-weekly.yml` (Re-dispatch live-lindas-weekly.yml) | sleep | n/a (event-triggered automation) | Yes — automation responding to a workflow event |

### Local gate helper — `uv run check`

The `[project.scripts]` entry `check = "sapphire_flow.cli.check:main"`
(declared in `pyproject.toml`) provides a one-command developer-side
mirror of the CI `lint` job's ruff steps:

~~~bash
uv run check       # runs `ruff format --check src/ tests/` then `ruff check src/ tests/`
~~~

It does NOT invoke `uv sync`: developers typically have a synced venv
when invoking it, and CI's lint job runs `uv sync --frozen` at the
workflow level before the ruff steps.

It does NOT invoke pytest: the `unit` and `integration` CI jobs are
CI-only because they require system deps (`libeccodes0`, `libgeos-c1v5`)
and a postgres service that a local-helper invocation should not assume.
For pre-merge confidence developers can run `uv run pytest tests/unit`
manually.

`uv run check` is the developer ergonomics counterpart to the
`pre-commit` developer-tier gate (see `CLAUDE.md` §Pre-commit hooks and
`docs/plans/070-precommit-and-gate-parity.md` for the full design).
The CI `lint` job keeps its own standalone ruff steps; this helper does
not modify CI behaviour.

### `build-image-and-scan`

The image-build-and-scan tier added by Plan 064 sits between `integration` and `e2e`. Operational shape:

- Runs `docker build` against the multi-stage `Dockerfile`, tagging the result `sapphire-flow:ci-${{ github.sha }}` local to the runner.
- Runs `trivy image` against the locally built image (`HIGH,CRITICAL`, `--ignore-unfixed`) to catch OS-level CVEs the `trivy fs` scan in the `lint` tier cannot see.
- Runs `syft` (via `anchore/sbom-action`) to produce a CycloneDX JSON SBOM, uploaded as the `sbom-cyclonedx` workflow artifact on every run.
- `e2e` gates on this job succeeding (`needs: [unit, integration, build-image-and-scan]`) — a failing CVE scan or SBOM step blocks the capstone suite.

All three steps share a runner context because images built in one GitHub Actions job are not visible to another job without an explicit image-tarball hand-off.

### Slow + live test tiers

Not every test runs in default CI. Two pytest markers partition the suite:

- **`@pytest.mark.slow`** — tests exceeding ~1 minute of wall time (full-pipeline e2e, large-fixture integration paths). Excluded from default `uv run pytest` via `pyproject.toml` `addopts`. Run by `.github/workflows/integration-nightly.yml` (nightly at 03:00 UTC, `--timeout=3600`).
- **`@pytest.mark.live` / `@pytest.mark.live_lindas` / `@pytest.mark.live_stac`** — tests that hit external APIs (MeteoSwiss STAC, BAFU LINDAS). Excluded from default CI. Run by `integration-nightly.yml` (nightly) or `live-lindas-weekly.yml` (Mondays at 06:00 UTC).

Both scheduled workflows also accept `workflow_dispatch`, so they can be fired manually via `gh workflow run` for out-of-cycle verification.

### Before major merges to main

The default `unit` + `integration` jobs catch fast-to-spot regressions. Large changes — new adapters, flow-wiring, model framework touches, or anything that could plausibly affect the slow/live paths — should run the nightly tier manually before merging:

```bash
# Fire the nightly suite against the branch currently being merged
gh workflow run integration-nightly.yml --ref <branch>
# Optionally also the weekly LINDAS schema check
gh workflow run live-lindas-weekly.yml --ref <branch>

# Watch the run
gh run watch
```

This is a convention, not a hard merge gate — branch protection does not require it. The discipline is: **if your change touches adapters, flows, or the e2e path, run the nightly manually before merging**. If it's cosmetic (docs, typo fixes, trivially-scoped refactors), the default CI is enough.

Rationale: slow/live tests take 10-30 minutes and hit rate-limited external APIs. Running them on every PR would burn runner time and external-API quota. Running them on merge to main is after-the-fact (a failure forces a revert). The manual-trigger-before-merge ritual catches regressions at the latest point where a fix is still cheap.

### live_stac test scope

`tests/integration/live/test_meteoswiss_nwp_live.py::test_fetch_and_parse_smoke`
is scoped to a 4-file smoke test (`max_files=4` on the adapter) to
stay within GitHub Actions runner limits. Its purpose is to detect
MeteoSwiss STAC endpoint drift (schema, URL format, paramId), NOT
to validate full-cycle correctness.

Full-cycle live validation happens in two places:
- **Unit tests** against committed ICON-CH2-EPS fixtures
  (`tests/unit/adapters/test_meteoswiss_nwp_real.py`).
- **Production dress rehearsals** (Plan 046 §A3 step 8).

If deeper live validation is needed (e.g., before a major adapter
change), temporarily raise `max_files` on a branch and run `gh
workflow run integration-nightly.yml --ref <branch>` per the ritual
above — revert before merging.

## Config overlays

A deployment can run from `main` with small config variants (staging subsets, per-region tweaks) without forking a branch. One base `config.toml` stays canonical; overlays patch only the keys they need; all loaders consume the merged result through a shared helper.

### Mechanism

- **Base file**: `config.toml` (pointed at by `SAPPHIRE_CONFIG`). Absolute path in Docker/production.
- **Overlay files**: zero or more TOML files listed in `SAPPHIRE_CONFIG_OVERLAY`, comma-separated. Paths applied left-to-right; rightmost wins on key collisions. Absolute paths in Docker/production.
- **Merge helper**: `load_merged_toml` in `src/sapphire_flow/config/_overlay.py`. All config loaders (`load_config`, `load_onboarding_config`, `load_qc_rules`, `load_forecast_qc_rules`, plus the adapter-endpoint read in `flows/ingest_observations.py`) route through this helper, so no loader sees an overlay-less view.
- **No overlay**: `SAPPHIRE_CONFIG_OVERLAY` unset or empty → identical behaviour to reading the base alone.

### Directory convention

- `config/overlays/*.toml` — version-controlled canonical overlays shared across operators (e.g. `staging-5-stations.toml`).
- `config/overlays/local/*.toml` — gitignored operator-only tweaks (ad-hoc debugging, per-host overrides). `.gitkeep` preserves the directory.

### Merge semantics

- **Dicts** deep-merge recursively — keys absent from the overlay inherit the base value.
- **Lists replace wholesale** — no append. An overlay that sets `basin_ids = ["2004", "2009"]` fully replaces the base's list; there is no way to extend the base list through an overlay. This applies to TOML array-of-tables (`[[danger_levels]]`, `[[seasons]]`) as well.
- Validation runs on the merged result via existing Pydantic models; overlays are not separately schema-checked.

### Failure mode

A missing overlay file raises `FileNotFoundError` at startup. There is no silent fallback to the base alone — a staging deployment with a typoed overlay path fails loud rather than silently running against the full operational config.

### Docker Compose pattern

Compose overlays select config overlays. `docker-compose.staging.yml` bind-mounts `config/overlays/staging-5-stations.toml` and sets `SAPPHIRE_CONFIG_OVERLAY` on the services that read config (`prefect-worker`, `api`, `init`). Operators run:

```
docker compose -f docker-compose.yml -f docker-compose.staging.yml up
```

### Minimal example

Overlay (`config/overlays/staging-5-stations.toml`):

```toml
# Trim onboarding to the 5-station A1 subset; base's data_source is preserved.
[onboarding]
basin_ids = ["2004", "2009", "2033", "2085", "2091"]
```

Selection (outside Docker):

```
SAPPHIRE_CONFIG=config.toml \
SAPPHIRE_CONFIG_OVERLAY=config/overlays/staging-5-stations.toml \
uv run python -m sapphire_flow.cli.register_deployments
```

## Host-level watchdog

Independent of Docker and Prefect. A cron job on the host VM:

```bash
# /etc/cron.d/sapphire-watchdog
*/5 * * * * root curl -sf http://localhost:8000/api/v1/health || /opt/sapphire/scripts/alert.sh "SAPPHIRE health check failed"
```

`alert.sh` sends a notification (email or SMS) directly using system tools (`sendmail`, `curl` to SMS API). This is the last-resort alerting mechanism — it works even when Docker, Prefect, and the application are all down (as long as the VM is up). See architecture-context.md § Backup and disaster recovery for the health endpoint specification.
