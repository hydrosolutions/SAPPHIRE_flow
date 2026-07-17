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
| `prefect-worker` | custom (sapphire-flow) | prefect-server (healthy), init (completed) | — | `unless-stopped` | **v0** (§A6) |
| `prefect-worker-ingest` | custom (sapphire-flow) | prefect-server (healthy), init (completed) | — | `unless-stopped` | **v0b** (§A6) — dedicated `ingest` pool worker isolating `*/5` obs ingest from the shared `default` pool (Plan 098) |
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

Config bind mount: `./config.toml:/app/config.toml:ro` on `api` and both v0 workers (`prefect-worker`, `prefect-worker-ingest`); the three-worker phrasing refers to the v1 topology.

### Dependency chain

```
postgres ──→ pgbouncer ──→ api ──→ caddy
    │                 ↗
    └──→ prefect-server ──→ prefect-worker-ops
                       ──→ prefect-worker-hindcast
                       ──→ prefect-worker-training

    init (one-shot, runs before workers/api)
```

> **v0 variant** (v0-scope.md §A3, §A6): No PgBouncer intermediary; `prefect-worker` (general `default` pool) plus `prefect-worker-ingest` (dedicated `ingest` pool, Plan 098) replace the three specialized workers. Both workers are init-gated one-shot dependents of `prefect-server`.
> ```
> postgres ──→ api ──→ caddy
>     │
>     └──→ prefect-server ──→ prefect-worker
>                        ──→ prefect-worker-ingest
>
>     init (one-shot, runs before workers/api)
> ```

All `depends_on` use `condition: service_healthy` where health checks are defined.

## Prefect work pool separation

> **v1-only** (v0-scope.md §A6): v0 now runs **two** work pools — the general `default` pool plus a dedicated `ingest` pool served by `prefect-worker-ingest` (a v0b obs-feed-isolation addition, Plan 098, that keeps the `*/5` observation ingest off the shared `default` pool). The three-pool ops/training/hindcast topology below still applies to v1.

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
2. Stop workers (graceful): `docker compose stop prefect-worker prefect-worker-ingest` (v0 both workers; v1: `prefect-worker-ops prefect-worker-training`)
3. Run init: `docker compose run --rm init` (applies migrations, creates both pools, reroutes deployments)
4. Restart all: `docker compose up -d`

> **Plan 098 note**: both v0 workers must be quiesced in step 2 before `init`/`alembic upgrade head` re-runs — leaving `prefect-worker-ingest` running during the upgrade breaks the sequence. Phase 1 (routing `ingest-observations` to the `ingest` pool) and Phase 2 (the `prefect-worker-ingest` container that serves it) ship together in a single image build + compose update; a partial deploy leaves the `ingest` pool workerless and the obs feed dead.

### Rollback

No schema downgrade path — rollback = restore from backup + redeploy previous image tag. Migrations must be backwards-compatible for one version (additive only: new columns nullable, no destructive changes in a single release). This means the previous image tag can run against the new schema during the migration window.

**Two-release column tightening (Plan 115a/115c)** — `station_weather_sources.role`
illustrates the additive-then-tighten pattern for a column that must eventually be
`NOT NULL`: migration `0030` (115a) adds `role` **nullable**, backfills it, and applies
a NULL-tolerant `CheckConstraint("role IS NULL OR role IN ('forecast','reanalysis')")`
so the previous image tag can still write an unroled row during the rollback window. A
later migration `0034` (115c — revision reallocated by 082's `0032` and 115b4's `0033`
landing first) tightens the column to `NOT NULL` only once that rollback window has
closed. Do not collapse the two into one release — the nullable step exists
specifically so `0030` stays backwards-compatible with the pre-115a image per the rule
above.

**Two-release reader flip + camels-ch retirement (Plan 115b4)** — the reanalysis-reader
default flip is deliberately isolated from the CAMELS-CH weather-binding retirement as
TWO SEQUENCED releases on this standard deploy path (no bespoke alembic targets), because
`init` runs `alembic upgrade head` **before** any worker/API confirms the new reader is
actually serving:

- **Before Release A** (§5C, distribution-shift gate): run
  `uv run python scripts/audit_distribution_shift.py` against the target deployment's
  `DATABASE_URL` — it enumerates every ACTIVE station/group model assignment and flags
  any whose declared `past_dynamic_features` overlap the parameters whose SOURCE changes
  under the flip (precipitation/temperature/temperature_min/temperature_max/
  relative_sunshine_duration). This is a LIVE-DB check, not a repo-review inference —
  disposition (retrain, or hold the flip for affected stations/groups) any flagged model
  BEFORE proceeding.
- **Release A** (§5A–5D + phase-6 loudness/guards): the hybrid parameter-drop fix, the
  MeteoSwiss-only priority chain (no CAMELS-CH tier), and flipping
  `DeploymentConfig.reanalysis_source` default to `"hybrid"`. **Ships with NO new
  migration** — it is a pure code/config deploy on the existing schema (the `camels-ch`
  weather binding, if present, is simply never read by the hybrid chain post-flip).
  Follow the standard upgrade procedure above, then confirm on staging: `ingest-weather-history`
  reports a non-zero effect (§6B health-by-effect, not `rows_stored`), a station serves
  past-dynamic features via the MeteoSwiss chain, and a forecast cycle completes on the
  new series. **A green flow is not evidence** — check the `weather_history_ingest`
  `PipelineHealthRecord`s and a direct dashboard forcing-endpoint read.
- **Release B** (§5E, migration `0033`): retires the `camels-ch` `station_weather_sources`
  binding — ships **only after** Release A is confirmed serving on staging. Deploy on the
  same standard upgrade procedure (`alembic upgrade head` now includes `0033`). The
  `historical_forcing` rows tagged `camels-ch` are **not** touched by this migration — they
  remain the Plan 115b3 validation reference + audit trail, readable by a direct
  source-keyed fetch. Confirm after deploying: the `camels-ch` weather binding is gone, its
  forcing rows are still readable directly, and a forecast cycle still completes.

**Rollback for both releases is the standard path** (restore from backup + redeploy
previous image tag, per the rule above) — `0033`'s `downgrade()` is a deliberate NO-OP
(logs a warning, does not raise, keeps the migration chain mechanically traversable)
rather than claiming a schema-level reversal it cannot honestly provide (the deleted
binding rows' station set cannot be reconstructed from what remains in the database).

**Ingest-worker rollback (Plan 098)** — if the combined deploy lands but `prefect-worker-ingest` then fails to start or crashes (e.g. missing `/data/artifacts` tmpfs, too-low `mem_limit`, missing `db_password`, or a wrong overlay path), `init` has already re-routed `ingest-observations` onto the `ingest` pool and the `default` worker no longer claims it, so the obs feed is **dead** until recovery. Restore the pre-098 state (LATE obs, not dead obs):

0. **Before opening the upgrade window**, tag the current image as a rollback anchor. The project ships no registry-publish workflow (see "No image publish / release workflow" below), so images are local-only — if the pre-upgrade image is pruned there is nothing to roll back to. Confirm the current tag with `docker images sapphire-flow --format "{{.Tag}}"`, then `docker tag sapphire-flow:${OLD_VERSION} sapphire-flow:rollback-backup`.
1. `docker compose stop prefect-worker prefect-worker-ingest` — quiesce both workers.
2. Revert `register_deployments.py` to the pre-098 routing (no `INGEST_POOL`; `ingest-observations` routes to `WORK_POOL = "default"`) and deploy the previous image — either rebuild the revert or point `VERSION` in `.env` back to the tagged `rollback-backup` / `${OLD_VERSION}` image from step 0, then `docker compose up -d`. If the revert also crosses a DB-migration boundary, follow the schema-rollback note above (restore from backup + redeploy). 098 itself ships no migration, so this applies only if 098 is bundled with a migration-carrying release.
3. `docker compose run --rm init` — re-registers `ingest-observations` back onto the `default` pool (`init` is idempotent).
4. `docker compose up -d prefect-worker` **without** the `prefect-worker-ingest` service — the `default` worker serves the obs feed again.

The symmetric partial-deploy failures (Phase 1 without Phase 2 → workerless `ingest` pool; Phase 2 before the pool is created → ingest worker polls an empty pool) reduce to the same recovery: revert routing to `WORK_POOL`, re-run `init`, run only the `default` worker.

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

With 4 forecast cycles/day and 48 obs ingest runs/day, estimated log volume is ~100 MB/day at ~50 stations before rotation, scaling roughly linearly with station count (~340 MB/day at ~170 stations, ~2 GB/day at ~1000 stations). The `max-file: 5` x `max-size: 50m` = 250 MB cap per container. 8 containers x 250 MB = ~2 GB maximum disk usage for container logs. v0 has 7 containers (no PgBouncer, two workers instead of three). At ~1000 stations, the 250 MB cap causes logs to rotate within hours — see plan 013 DECISION on line 125.

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

1. **Developer-tier gate (pre-commit + pre-push)** — local hooks catch lint, format, and secret-pattern issues before `git commit` completes. The slower pyright ratchet runs at `pre-push` so commits stay fast while type regressions are still blocked before they leave the machine.
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
[pre-push hook — on git push]
  • uv run pyright --outputjson src/
  • tools/pyright_ratchet.py compares live errors with tools/pyright_baseline.json
        │ blocks push if pyright errors exceed the ratchet baseline
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
- [`docs/plans/070-precommit-and-gate-parity.md`](../plans/070-precommit-and-gate-parity.md) — the plan that introduced the developer-tier gate and `uv run check`. A4 wires the pyright ratchet at `pre-push`, with CI as the backstop.
- [`docs/plans/064-supply-chain-hardening.md`](../plans/064-supply-chain-hardening.md) — predecessor plan that surfaced the "wired but unrun" gate problem this plan fixes. Introduced Trivy image scan, SBOM generation, and CI action SHA pinning.
- [`docs/plans/069-pyright-backlog-cleanup.md`](../plans/069-pyright-backlog-cleanup.md) — follow-on that supplied the pyright ratchet baseline and CI backstop consumed by Plan 070 A4.

See the CI workflow tiers table below for the full per-step breakdown of every `run:` command across all three workflow files, including local equivalents and CI-only reasons.

## CI workflow tiers

This subsection describes the operational topology of `.github/workflows/ci.yml`. Policy rationale (why each scan exists, what severity thresholds apply, what the `.trivyignore` discipline is) lives in [`security.md`](security.md) § Supply chain.

<!-- Extended by Plan 070 §C1 — two new columns + per-run-step rows. -->
<!-- "Local equivalent" = command a developer types locally to reproduce. -->
<!-- "CI only? Reason" = "No" when there is a real local equivalent; "Yes — <reason>" when not. -->

| Tier | Job | `run:` step | Depends on | Local equivalent | CI only? Reason |
|------|-----|-------------|------------|-----------------|-----------------|
| **ci.yml** | | | | | |
| 1 | `lint` | `Configure git auth for the private recap-dg-client clone` (Plan 082 Task 2H) | — | `git config --global url."https://<token>@github.com/hydrosolutions/recap-dg-client.git".insteadOf "https://github.com/hydrosolutions/recap-dg-client.git"` (developer needs a token with read access) | No — requires the `RECAP_DG_CLIENT_TOKEN` repo secret in CI |
| 1 | `lint` | `uv sync --frozen` | — | `uv sync` (developers typically have a synced venv already) | No |
| 1 | `lint` | `uv run ruff check src/ tests/` | — | `uv run ruff check src/ tests/` (also via `uv run check` and pre-commit) | No |
| 1 | `lint` | `uv run ruff format --check src/ tests/` | — | `uv run ruff format --check src/ tests/` (also via `uv run check` and pre-commit) | No |
| 1 | `lint` | `shellcheck scripts/launchd/start-sapphire.sh scripts/launchd/watchdog.sh scripts/launchd/install-launchd.sh scripts/bootstrap-mac-mini.sh` | — | Same command (also via pre-commit `shellcheck`) | No |
| 1 | `lint` | `uv run pyright --outputjson src/ > /tmp/pyright.json \|\| true` | — | `uv run pyright src/` | No |
| 1 | `lint` | `uv run python tools/pyright_ratchet.py /tmp/pyright.json tools/pyright_baseline.json` | — | `uv run pyright src/` (then compare against `tools/pyright_baseline.json`) | No |
| 1 | `lint` | `aquasecurity/trivy-action` (fs scan, `uses:`) | — | `trivy fs --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed --scanners vuln --skip-dirs .venv .` | No (but requires trivy installed) |
| 2 | `unit` | Install system deps for cfgrib / rioxarray / exactextract | — | Brew/apt on the dev host (developer responsibility) | Yes — system-package install, not project-managed |
| 2 | `unit` | `Configure git auth for the private recap-dg-client clone` (Plan 082 Task 2H) | — | Same as `lint` row above | No — requires `RECAP_DG_CLIENT_TOKEN` |
| 2 | `unit` | `uv sync --frozen` | — | `uv sync` | No |
| 2 | `unit` | `uv run pytest tests/unit/ --cov=src/sapphire_flow --cov-report=term-missing -v` | — | `uv run pytest tests/unit/` (requires system deps above) | No (but requires system deps) |
| 2 | `wheel-only-guard` | `Configure git auth for the private recap-dg-client clone` (Plan 082 Task 2H) | — | Same as `lint` row above | No — requires `RECAP_DG_CLIENT_TOKEN` |
| 2 | `wheel-only-guard` | Step 1 = "the wheel-only guard": `uv sync --frozen --no-build --no-cache --no-install-project --no-install-package forecastinterface --no-install-package recap-dg-client` | — | Same command | No |
| 2 | `wheel-only-guard` | Step 2 = "post-guard temporary exception install": `uv sync --frozen --no-cache --no-install-project --reinstall-package forecastinterface --reinstall-package recap-dg-client` | Step 1 guard | Same command | No |
| 3 | `integration` | Install system deps for cfgrib / rioxarray / exactextract | `unit` | Brew/apt on the dev host (developer responsibility) | Yes — system-package install, not project-managed |
| 3 | `integration` | `Configure git auth for the private recap-dg-client clone` (Plan 082 Task 2H) | `unit` | Same as `lint` row above | No — requires `RECAP_DG_CLIENT_TOKEN` |
| 3 | `integration` | `uv sync --frozen` | `unit` | `uv sync` | No |
| 3 | `integration` | `uv run pytest tests/integration/ -v -m "not slow"` | `unit` | `uv run pytest tests/integration/ -v -m "not slow"` (requires postgres service + system deps) | No (but requires postgres) |
| 4 | `build-image-and-scan` | `docker/build-push-action` (`uses:`) — build app image, passing `secrets: recap_dg_client_token=<RECAP_DG_CLIENT_TOKEN>` (Plan 082 Task 2H) | `unit` | `docker buildx build -f Dockerfile -t sapphire-flow:local --secret id=recap_dg_client_token,env=RECAP_DG_CLIENT_TOKEN .` | No (but requires Docker daemon + a local `RECAP_DG_CLIENT_TOKEN` env var) |
| 4 | `build-image-and-scan` | `aquasecurity/trivy-action` (image scan, `uses:`) | `unit` | `trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed sapphire-flow:local` | No (but requires the image to be built + trivy installed) |
| 4 | `build-image-and-scan` | `anchore/sbom-action` (`uses:`) — generate SBOM with syft | `unit` | `syft sapphire-flow:local -o cyclonedx-json > sbom.cdx.json` | No (but requires syft installed) |
| 5 | `e2e` | _(not yet implemented — dangling comment at line 206 of ci.yml)_ | `unit`, `integration`, `build-image-and-scan` | n/a | n/a |
| **dependency-safety.yml** (Plan 119) | | | | | |
| Unconditional (`pull_request`, every PR) | `dependency-safety` | `uv sync --frozen` | — | `uv sync` | No |
| Unconditional (`pull_request`, every PR) | `dependency-safety` | `uv run python tools/dependency_safety.py --base-ref "${{ github.event.pull_request.base.sha }}"` (Classify dependency-bump risk) | — | `uv run python tools/dependency_safety.py --base-ref <base-sha>` (any base commit) | Yes — requires the PR base SHA from the `pull_request` event context |
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

### `dependency-safety.yml` (Plan 119)

A sibling workflow to `ci.yml`, kept separate so its trigger stays
**unconditional** on `pull_request` (no top-level `paths:` filter). `ci.yml`'s
`on:` block also fires on `push` to `main`; this gate only makes sense in a
PR-diff-against-base context, so folding it into `ci.yml` would either widen
`ci.yml`'s triggers or require a job-level `if:` — a separate file is
cleaner.

Operational shape: checks out with `fetch-depth: 0` (needs the PR base
commit for `git show <base-sha>:<path>`), then runs
`tools/dependency_safety.py`, which diffs a fixed watched-file set
(`docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `uv.lock`,
`.github/workflows/ci.yml`, plus the gate's own self-policy files —
`tools/dependency_safety.py`, `.github/workflows/dependency-safety.yml`,
`.github/dependabot.yml`, `.dependency-safety-allowlist`) against the PR
base SHA and classifies the change BLOCK / REVIEW / ALLOW. If none of the watched files changed, the
script exits 0 immediately (skip-pass) — the job always reports a concrete
pass/fail, never GitHub's `Expected`/pending state, which is what makes it
safe to mark as a required check later. Policy rationale (why the gate
exists, the BLOCK/REVIEW/ALLOW criteria, the committed-allowlist override)
lives in [`security.md`](security.md) § Supply chain, per this doc's usual
split between operational topology and policy rationale.

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

Compose overlays select config overlays. `docker-compose.staging.yml` bind-mounts `config/overlays/staging-5-stations.toml` and sets `SAPPHIRE_CONFIG_OVERLAY` on the services that read config (`prefect-worker`, `prefect-worker-ingest`, `api`, `init`). The ingest worker must be included: without the overlay block its `SAPPHIRE_CONFIG_OVERLAY` is simply unset, `_resolve_overlay_paths()` returns `[]`, and the worker silently falls back to the base config and queries the wrong station set (all stations instead of the 5-station subset) — no crash. This is distinct from the `FileNotFoundError` failure mode above, which fires only when the env var IS set but the TOML bind mount is absent; both are prevented by adding the overlay block. Operators run:

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

## Host-level Docker image / build-cache prune (mac-mini, Plan 105 D3)

A weekly launchd job on the mac-mini prunes accumulated Docker images and build
cache that grow unbounded from version-bump rebuilds (~1.9 GB per bump). This is a
**host-level** job — it is NOT a Prefect flow, because running `docker image prune`
from a Prefect worker would require mounting the Docker socket into the container
(a container-escape surface, forbidden by `docs/standards/security.md`).

### Files

| File | Purpose |
|------|---------|
| `scripts/launchd/prune-docker.sh` | Main script: reads `docker system df --format '{{json .}}'`, parses Images and Build Cache reclaimable figures, runs `docker image prune -a -f` (≥ 1 GB reclaimable) and `docker builder prune -f` (≥ 1 GB reclaimable) independently |
| `scripts/launchd/ch.hydrosolutions.sapphire-docker-prune.plist` | launchd agent — label `ch.hydrosolutions.sapphire-docker-prune`, weekly `StartCalendarInterval` (Sunday 04:00 local) |
| `scripts/launchd/install-launchd.sh` | Updated to include the new plist in the `PLISTS=(...)` array (alongside `ch.hydrosolutions.sapphire.plist` and `ch.hydrosolutions.sapphire-watchdog.plist`) |

### Stack-up guard

`docker image prune -a -f` removes ALL images not referenced by a running container —
including the current `sapphire-flow:${VERSION}` tag if the stack is down. The script
therefore checks that the stack is running before pruning:

```bash
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -Eq '^sapphire_flow-'; then
    log 'stack not running or daemon unreachable — skipping prune'
    exit 0
fi
```

Plain `docker ps` (not `docker compose ps`) is used because the launchd job runs from
`scripts/launchd/` which has no `docker-compose.yml`; the compose form would always
find no project and skip every run. If the Docker daemon is unreachable, `docker ps`
exits non-zero, the `if !` condition is true, and the script exits cleanly (safe).

### Upgrade runbook note

After any operation that prunes images (weekly cron or a manual `docker system prune`),
always restart the stack with:

```bash
docker compose up -d --build
```

A bare `docker compose up -d` (no `--build`) attempts to reuse the cached image. If
the prune removed it, `up -d` will error or silently pull a different version. The
`--build` flag ensures the current version is rebuilt from the Dockerfile before
starting. This note applies equally to the upgrade procedure in § Upgrade procedure
above — step 4 (`docker compose up -d`) should be `docker compose up -d --build`
after any host-level image prune.

### Registration

Run `scripts/launchd/install-launchd.sh` once (or re-run after plist changes) to
register all three launchd agents — the installer is idempotent.

## Host-level watchdog

Independent of Docker and Prefect. A cron job on the host VM:

```bash
# /etc/cron.d/sapphire-watchdog
*/5 * * * * root curl -sf http://localhost:8000/api/v1/health || /opt/sapphire/scripts/alert.sh "SAPPHIRE health check failed"
```

`alert.sh` sends a notification (email or SMS) directly using system tools (`sendmail`, `curl` to SMS API). This is the last-resort alerting mechanism — it works even when Docker, Prefect, and the application are all down (as long as the VM is up). See architecture-context.md § Backup and disaster recovery for the health endpoint specification.
