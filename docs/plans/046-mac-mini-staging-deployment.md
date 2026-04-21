# Plan 046 — Mac Mini Staging Deployment + Edge-Case Test Suite

**Status**: IN_PROGRESS
**Revision**: 11 — A3 dress-rehearsal on 2026-04-21 (5-station, on main with `docker-compose.staging.yml` per Plan 065) completed steps 1, 2, 3, 5, 6, 7, 9 and surfaced issues on steps 4 and 8. See `docs/deployment/dress-rehearsal-2026-04-21.md` for the full report. Rev 11 folds the rehearsal findings back into the plan text — no code or procedure change in Rev 11 itself; substantive fixes are spawned as detour plans: (a) §A1 "validates multi-parameter pipeline (discharge + water_level)" softened — Murten (lake) is correctly ingestion-only under the current discharge-target model lineup, multi-parameter forecast coverage deferred to v0b per finding F7. (b) §A2 gets explicit `docker compose down -v` as first-run-from-main prerequisite (F1) plus a `VERSION` env-var note for operators (Mac-specific gotcha). (c) §A3 steps 5 and 6 add JSON-string quoting examples for UUID deployment params (`-p 'station_id="..."'` not `-p station_id=...`) per finding F2 — raw UUIDs fail Prefect CLI's JSON-value parsing. (d) §A3 step 8 direct-invoke template adds `DATABASE_URL` construction boilerplate (`PW=$(cat /run/secrets/db_password); export DATABASE_URL=...`) — `docker compose exec` does not inherit the entrypoint-built env. (e) §A3 step 4 gets a pointer to **Plan 066** (train-models retrain strategy — under research; should be configurable, not a single hard-coded data-window policy) per finding F3. (f) §A3 step 8 gets a pointer to **Plan 067** (MeteoSwiss STAC adapter investigation — the "cycle late" signal is almost certainly our query implementation, not MeteoSwiss's publication — plus `_MAX_FALLBACK_STEPS` configurability and pagination-cap removal) per findings F4 + F5. (g) §A4 gets a pointer to **Plan 068** (`onboard-stations` parallelization + decoupling historical-hindcast phase) — 38 min at 5 stations projects to ~26 h at 169, blocking v0 scale-up. (h) Mac-mini watch note: F6 compute-skills transient OOM under parallel load — monitor during A4. (2026-04-21)

**Revision**: 10 — Plan 065 DONE (2026-04-21, commit `4cf6d32`, tag `v0.1.376`, archived `b0ce875`) introduces the config-overlay mechanism and supersedes the `staging-5-stations` branch workflow: (a) §A1 "Transient config change" paragraph is marked SUPERSEDED (strikethrough retained for historical context) — A3 runs from `main` with `-f docker-compose.staging.yml` selecting `config/overlays/staging-5-stations.toml`. (b) §A1 "Cross-reference (Plan 065)" placeholder commit hash filled in. (c) §A2 adds a preliminary `docker compose build` step because the cached `sapphire-flow:latest` image predates Plan 065 and does not contain the overlay loader; the up command adds `-f docker-compose.staging.yml` as the third overlay. (d) §A3 step-8 direct-invoke `docker compose exec` command similarly adds `-f docker-compose.staging.yml` for consistency. (e) §A4 scale-up no longer restores a "full 169-station [onboarding] list" — instead, drop `-f docker-compose.staging.yml` from the compose command and re-up to revert to 169. (f) Files-to-modify table row for `config.toml` (A1 transient, branch-only) marked SUPERSEDED. (g) Open question #4 about `staging-5-stations` branch management collapsed — branch deprecated, operator may `git branch -D staging-5-stations` after A3 completes. No code changes in Rev 10 — pure doc revision. (2026-04-21)

**Revision**: 9 — Plan 060 DONE (2026-04-19) resolves A3 step-4 through step-8 compat gaps: (a) blanket `cache_policy=NO_CACHE` on all 25 lifecycle-flow @tasks (fixes the Prefect 3 HashError on store-typed inputs that silently zeroed `train-models`); (b) `CHOWN` + `FOWNER` cap_add landed (via commit `289c5f8`) and documented in `security.md § Capabilities` (fixes `/data/artifacts` + `/data/backups` permission-denied); (c) `/data/raw` migrated from `sapphire_data` named volume to a `CAMELS_CH_HOST_DIR`-driven bind-mount in `docker-compose.dev.yml` (fixes the empty-volume bootstrap); (d) §A3 step-4 gains a trigger-command example with `model_ids` list form; (e) §A3 step-8 redirected to a direct-invoke of `run_forecast_cycle_flow.fn(...)` with an explicitly-constructed `MeteoSwissNwpAdapter` (deferred the adapter-registry design to a future plan per Plan 060 D4); (f) §A1 commit-ordering step added for a second rebase of `staging-5-stations` onto main to pick up Plan 060's 25 @task edits + cap_add + dev-overlay changes before resuming A3 step 5; (g) Stream C4 runbook requires a "Flows that require direct-invoke rather than Prefect UI trigger" section; (h) Stream C2 mac-mini overlay must now spec a `/data/raw` mount (bind-mount or named volume) since Plan 060 removed the base-compose `sapphire_data:/data/raw:rw` entry.

**Revision**: 8 — A3 step 1 surfaced two more deployment-infra findings plus one Plan-044 completeness gap that warranted a detour plan: (a) Prefect 3 dispatches `Flow.from_source` to `afrom_source` in async context, returning a coroutine — needed `await flow_fn.afrom_source(...)` explicitly. Fixed in `c0d7fd8`; 13 CLI tests updated to `AsyncMock`. (b) `docker-compose.yml:74` (prefect-worker) had `DATABASE_URL_TEMPLATE: postgresql+asyncpg://...` (copy-paste from prefect-server's legit async URL), but flow code uses sync SQLAlchemy → all 8 deployment-triggerable flows crashed with `MissingGreenlet` at `_db.py:68`. One-line fix: `+asyncpg` → `+psycopg`. (c) Plan 044 wired the production bootstrap into only 4 of 9 flows; the 4 model-lifecycle flows (onboard-model, train-models, compute-skills, compute-combined-skills) crashed with `'NoneType'.register_model` — **Plan 059 DONE** (`d484f0d` feat + `0630e30` archive + `566c5db` chore, tag `v0.1.323`) replicates the `setup_production_stores` bootstrap with correct `deployment_config` load and defensive None-checks. Post-fix: `onboard-model` deployment trigger reaches `COMPLETED` in ~8s (empty-scope register-only path). Ready to resume A3 step 2 onwards (2026-04-18)

**Revision**: 7 — A2.5 landed (`4f42244`, `v0.1.309`). Renamed `forecast.station_completed` → `forecast.run_completed` and added `ensemble_size` + `lead_time_hours` fields. Implementation surfaced that the plan's §A2.5 pseudocode referenced non-existent attributes `.ensemble.members` / `.ensemble.timesteps`; corrected to the real `ForecastEnsemble` surface (`member_count`, `forecast_horizon_steps`, `time_step`). Implementation also added `model_id` context binding so combination-mode produces one event per `(station, model)` pair. 1 new unit test added; 17/17 green. Ready for A3 dress rehearsal (2026-04-18)

**Revision**: 6 — fifth A2 finding: Prefect 3's `.adeploy()` hard-requires either an `image` kwarg (for docker pools) or a `.from_source(...)` chain (for process pools); `src/sapphire_flow/cli/register_deployments.py` did neither and failed with `ValueError: Either an image or remote storage location must be provided`. Fixed by threading `flow_fn.from_source(source="/app", entrypoint="src/<module_path>.py:<flow_attr>")` ahead of the existing `adeploy(...)` call; 13 CLI unit tests updated to mock the new pattern. With this fix, **A2 passes all exit criteria**: 5 services healthy, init exit 0, 9 Prefect deployments registered with correct crons, `/api/v1/health` returns `{"status":"ok","prefect_status":"ok"}` on port 8010. Ready for A2.5 (2026-04-18)

**Revision**: 5 — A2 first-boot surfaced four deployment-infra findings, one requiring a detour plan: (a) `prefecthq/prefect:3-python3.11` healthcheck used `curl` which the image does not ship → fixed in commit `a668959` by switching to a python `urllib.request.urlopen` probe (both `prefect-server` and `api`); (b) stale `sapphire_flow_pgdata` named volume from prior runs retained the old postgres password → runbook must include a `down -v` / password-drift guard; (c) `ForecastInterfaceAdapter` editable sibling dep `../ForecastInterface` broke the Docker build context → dormant adapter removed in commit `2173052` as a net-positive simplification (zero production callers); (d) `numcodecs 0.15.1` has no linux/arm64 wheel and the paired `zarr<3` pin blocked the bump → **Plan 056 DONE** (commits `3fd5348` feat + `c9b37a2` archive, tag `v0.1.306`) bringing `zarr>=3.0`, `numcodecs>=0.16.1`, `xarray>=2026.04.0` while retaining zarr-v2 on-disk format; A2 Docker build now succeeds clean on linux/arm64 without `gcc`. All four findings will also be recorded in the A5 dress-rehearsal report (2026-04-18)
**Revision**: 4 — reconcile with Plan 050 (Prefect run naming) which landed between A1 and A2.5: update `run_forecast_cycle.py` line refs (`:722` → `:744`, `:721` → `:743`, `:499` → `:521`); A1 station 2004 recorded as lake (not river) after live LINDAS probe; add explicit `staging-5-stations` rebase-onto-main step before A3 (2026-04-17)
**Phase**: 10c (staging infrastructure + deployment validation)
**Depends on**: Plan 043 (e2e test, DONE, archived), Plan 044 (deployment readiness, DONE, archived), Plan 045 (gridded NWP forecast cycle, DONE, archived), Plan 056 (zarr-python 3 migration, DONE, archived — unblocked A2 Docker build on arm64), Plan 059 (model-lifecycle flow bootstrap, DONE, archived — unblocked A3 deployment triggers).
**Scope**: Validate the gridded NWP forecast cycle on real Swiss data on a Mac mini staging host. This is the first real-data run of the gridded path; v0a point-weather has never been wired and is not planned. Access is LAN-only via SSH tunnel; public HTTPS access via Cloudflare Tunnel is Plan 049.

---

## Context

### Why now

Plan 044 closed the Linux-VM deployment gap. Plan 043 landed a testcontainers-based e2e test. Plan 045 wires the gridded NWP path into the forecast cycle. The next step is a real, running deployment the team can operate and observe — and the chosen host is a Mac mini in the office, used as a permanent staging server for v0, v1, and v2. Production forecasts remain on AWS or hydromet servers; the Mac mini is never the operational target.

Before touching the Mac mini we run a dress rehearsal on the developer MacBook Pro. The two machines are both macOS, so a problem found on the MacBook Pro is a problem avoided on the Mac mini. We also use this plan to fill the biggest remaining test gap: no test in the repo actually drives the real `docker-compose.yml` stack or exercises deployment failure modes.

**Plan 046 delivers LAN-only access**: team members reach the API via SSH tunnel from a laptop on the office LAN. No public URL, no Cloudflare, no DNS delegation, no Entra ID SSO in this plan. Public HTTPS via Cloudflare Tunnel + Cloudflare Access (Entra ID SSO + external-viewer OTP) is Plan 049, which depends on Plan 046.

### Inputs (confirmed with user)

- Developer machine: MacBook Pro (macOS). Assume Apple Silicon; C0 verifies.
- Staging host: Mac mini in office LAN. Assume Apple Silicon; C0 verifies.
- Mac mini is never production — AWS / hydromet take over for operational deployments.
- Gridded NWP only (v0a point-weather was never wired). Plan 045 is DONE — gridded path is live.
- All Swiss v0 data sources are public (BAFU LINDAS SPARQL, MeteoSwiss STAC). **The only secret required in Plan 046 is `db_password`.**
- Access to the staging API: SSH tunnel from team laptops on the office LAN. No inbound ports open on the office network, no router config needed.
- Station scale ramp: 5 stations → 169 stations, on both machines. Five for A1/A3: **2091, 2004, 2009, 2033, 2085**. 2033 (Reuss) and 2085 (Ticino) are added to the full basin list (going from 167 to 169). The five-station set must include at least one lake station (see A1).
- IT specialist (you / this orchestrator) owns the plan end-to-end.

### Problem statement

1. `docker compose up` has never been run against real Swiss data outside CI-style testcontainer fixtures. Real-data-path gaps (STAC pagination, LINDAS SPARQL quirks, Zarr write patterns, Prefect deployment registration under real scheduling) are unknown.
2. No test in the repo exercises the compose stack as a whole, nor does any test cover deployment-time failure modes (container crash mid-cycle, disk full, stale data source, corrupt config, missing secret, failed migration).
3. No macOS-specific deployment glue exists: `systemd` is Linux-only, Docker Desktop on macOS behaves differently from Docker Engine, Apple Silicon requires arm64 images, launchd replaces systemd.
4. `forecast.run_completed` (the canonical event name from logging.md §Canonical forecast cycle events) is never emitted. The per-station hook in `run_forecast_cycle.py:744` currently emits a non-canonical `forecast.station_completed` with only `duration_ms`. (Originally `:722` in earlier plan revisions; shifted by Plan 050's run-name templates which added `prefect_runtime` wiring in the same module.) The A3 exit gate — which looks for `forecast.run_completed` with `duration_ms` and `station_id` — is currently unpassable until A2.5 renames the event and adds the missing fields.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **LAN-only access in Plan 046.** Team members reach the Mac mini API and Prefect UI via SSH tunnel from a laptop on the office LAN. No inbound ports open; no router config. `caddy` runs in the stack for future use but Caddyfile is set to `tls internal` (self-signed) on the Mac mini overlay — it is not reachable from outside the LAN anyway. Pre-flight: assert ≥ 50 GB free on Docker VM disk **and** ≥ 50 GB free on `/Volumes/sapphire-backup` before proceeding. | Zero networking risk. Simple to operate. Plan 049 adds the Cloudflare Tunnel + public URL on top. |
| D2 | **launchd LaunchAgent** replaces the systemd unit on macOS. Both ship in the repo; `docs/standards/cicd.md` documents both. The LaunchAgent wrapper waits for Docker Desktop before calling `docker compose up -d`. | macOS has no systemd; launchd is the supported init path. A LaunchAgent (not LaunchDaemon) runs in the `sapphire` user session — required because Docker Desktop on macOS binds to a per-user socket. Linux VMs keep using the existing systemd unit. |
| D3 | **Docker Desktop** on both Macs (not Colima / Podman / OrbStack). API latency probed over SSH tunnel from a team laptop on the office LAN. | Homogeneity with the team's existing setup. |
| D4 | **New test tier: `tests/deployment/`** that drives real `docker compose up` via `testcontainers DockerCompose`. | Testcontainers test Postgres; they don't test the whole compose topology. Deployment bugs need a deployment-level harness. |
| D5 | **Edge cases are fault-injected**, not observed-in-the-wild. | Deterministic, CI-reproducible. |
| D6 | **Station scale ramp: 5 → 169 on both machines** (167 original + 2033 + 2085). | User-confirmed. 5 first catches structural issues cheaply; full list catches scaling ones. |
| D7 | **No automated output comparison between MacBook Pro and Mac mini.** Comparison is ad-hoc, manual, on demand. Cycle completion definition: Prefect `forecast-cycle` flow run reaches `COMPLETED` state. Obs ingest and backup tracked separately. Transient-error retries that ultimately succeed are not failures. Go / no-go includes peak disk usage check (≥ 50 GB free on Docker VM disk, ≥ 50 GB free on `/Volumes/sapphire-backup`). | Building a rigorous cross-machine diff harness is a separate, larger effort. |
| D8 | **Backup target on Mac mini: single external USB disk via `pg_dump`** (`backup_database_flow` from Plan 044). `backup_database_flow` writes to `/data/backups` (the `backups` named Docker volume, internal disk). The Mac mini overlay bind-mounts `/Volumes/sapphire-backup/pg_dumps` over `/data/backups` in `prefect-worker` so dumps land on the USB disk. **Sentinel file canonical path: `/data/backups/.sapphire-backup-volume` (inside the container) ≡ `/Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume` (host, via the bind-mount).** The backup pre-flight reads the container-side path and refuses to run if absent. | No restic, no encrypted chain, no restore rehearsal — matches v0-scope.md §A10. Plan 048 for restic. |
| D9 | **Plan 046 contains a small set of in-scope production-code additions**: (1) rename `forecast.station_completed` → `forecast.run_completed` and add `ensemble_size` + `lead_time_hours` in `run_forecast_cycle.py:722` (required for A3 exit gate), (2) free-space pre-flight + container-side sentinel check + `BackupRefusedError` in `backup_database_flow` (required for B8 + backup mount check), (3) `BackupRefusedError` in `src/sapphire_flow/exceptions.py`, (4) `HydroScraperAdapter.verify_gauge_reachable(site_code, station_kind)` public method in `src/sapphire_flow/adapters/hydro_scraper.py`. All other application findings become follow-up plans. Stream A findings after A5 commit are read-only; any finding requiring code changes spawns a numbered follow-up plan. | Keeps scope contained. |
| D10 | **`prefect-server` network membership.** Currently on `[backend, frontend]`. In Plan 046 (LAN-only) this is not critical, but it is noted. Plan 049 WILL remove `prefect-server` from `frontend` when `cloudflared` is introduced. No change in Plan 046. | Deferred to Plan 049. |
| D11 | **Caddy in Mac mini overlay: `tls internal`.** The Mac mini Caddyfile override uses `tls internal` (self-signed cert) or plain HTTP on `:80`. Not reachable from outside the LAN in Plan 046; Plan 049 activates the public path. | Avoids ACME certificate provisioning failures on an offline box. |
| D12 | **Prefect flow history pruning: 30 days.** Add `PREFECT_RESULTS_PERSIST_BY_DEFAULT=false` and `PREFECT_LOG_LEVEL=WARNING` already set; add `PREFECT_API_DATABASE_PRUNE_OLDER_THAN=30` (days) env var to `prefect-server` service in `docker-compose.yml`. No existing cicd.md or orchestration.md convention found — 30-day default adopted. | Prevents unbounded Prefect DB growth. |

---

## Stream A — Dress rehearsal on MacBook Pro

Goal: every gap between "CI green" and "runs with real Swiss data on macOS" surfaces here, not on the Mac mini. All steps happen on the developer laptop. Plan 045 is DONE (archived).

### A0 — Pre-flight (15 min)

- Confirm Docker Desktop installed, version ≥ 4.30.
- Allocate ≥ 16 GB RAM, ≥ 8 CPUs, ≥ 100 GB disk in Docker Desktop → Resources.
- Assert ≥ 50 GB free on Docker VM disk.
- `uv sync` succeeds.
- `uv run pytest tests/` green (baseline confirmation).

### A1 — Secrets + config for 5-station run

- Create `./secrets/db_password` (`openssl rand -hex 32`, `chmod 600`).
- **5-station set: four rivers + one lake.** Live LINDAS + CAMELS-CH probing during A1 showed that **2004 is Lake Murten, not a river** (CAMELS-CH `water_body_type == "lake"`; LINDAS returns 2 bindings under the `LAKE` query path and 0 under `RIVER`). The four rivers are therefore `{2091, 2009, 2033, 2085}` and the lake is `2004`. All five are LINDAS-verified and already carry CAMELS-CH attributes (2033 and 2085 become part of the permanent 169-station list; 2004/2009/2091 were already in the 167-station list). Hydrologist sign-off originally required for the lake pick is no longer applicable because no substitution was needed. Rationale: exercises four rivers + one lake for station-onboarding coverage. **Multi-parameter forecast coverage (discharge + water_level) is NOT validated by this subset in v0** — all three v0 models target discharge, and Murten (lake) has only `water_level` observations, so M.2 compatibility correctly skips artifact creation and Murten remains in `onboarding` state (ingestion-only path blocked by operational-gating). Water-level-target forecast coverage is deferred to v0b per dress-rehearsal finding F7 (2026-04-21).
- **Permanent config change** — add basin_ids `2033` (Reuss at Andermatt) and `2085` (Ticino at Bellinzona) to `config.toml` `[onboarding].basin_ids`. The full list grows from 167 to 169.
  - Verify all five gauges are reachable on LINDAS before committing: call the new public method `HydroScraperAdapter.verify_gauge_reachable(site_code: str, station_kind: StationKind) -> bool` (see Files to modify). The method issues a live HTTP SPARQL probe against LINDAS (built via `_build_sparql_query`) and returns `True` on HTTP 2xx with at least one binding, `False` on 4xx/5xx or empty response, and raises `AdapterError` on network failure. Document the invocation in the runbook.
  - Verify all five have CAMELS-CH attributes via the `camelsch` package.
  - If any fails these checks, replace with a substitute from the same river/lake system and flag the hydrologist (**hydrologist sign-off required**).
- **Plan 065 DONE (2026-04-21, commit `4cf6d32`, tag `v0.1.376`, archived `b0ce875`).** The config-overlay mechanism has landed. A3 runs from `main` with the three-file compose stack `docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.staging.yml up -d` — the `docker-compose.staging.yml` overlay mounts `config/overlays/staging-5-stations.toml` into the prefect-worker, api, and init services and sets `SAPPHIRE_CONFIG_OVERLAY=/app/config/overlays/staging-5-stations.toml`, which the loader deep-merges into the base `[onboarding]` section to trim from 169 to 5 stations. The "Transient config change (branch only)" procedure below is **SUPERSEDED by this mechanism** and kept in strikethrough only for historical context. See `docs/plans/archive/065-config-overlay-environment-variants.md`.
- ~~**Transient config change (branch only, never pushed, never opens a PR)** — create a `staging-5-stations` branch off the commit that adds 2033 and 2085 to `config.toml`. On that branch, trim `[onboarding].basin_ids` to the 5-station A1 set. Commit ordering: (1) commit the permanent 2033/2085 addition to main; (2) branch `staging-5-stations` off that commit; (3) **before running A3, rebase `staging-5-stations` onto the current `main`** so it picks up any intervening landed work (e.g. Plan 050's Prefect run-name templates, A2.5's `forecast.run_completed` rename, any post-A1 hotfixes). Resolve any `config.toml` conflicts by keeping the 5-station subset from the branch; (4) run A3 on the rebased branch; (5) merge or discard; (6) before A4 runs on main: `pg_dump` checkpoint (see V below), then `docker compose down -v && docker compose up -d` — wipes stations/hindcasts/skills/model artifacts from the 5-station A3 run so A4 starts from a clean DB; (7) A4 runs on main; (8) **after Plan 060 archives, re-rebase `staging-5-stations` onto main to pick up the cache_policy / cap_add / dev-overlay changes before resuming A3 step 5.** Plan 060 landed during A3 execution (train-models crashed silently on the Prefect 3 HashError at step 4, triggering the detour plan); the rebase is required before the per-station `run-hindcast` loop can succeed.~~
  **SUPERSEDED (Rev 10) — see the Plan 065 cross-reference bullet above.** Historical procedure retained for traceability only.
- Four river systems (Aare headwater → mid → lower, Reuss high-alpine snowmelt, Ticino southern-alpine) plus one lake. Both discharge-only and discharge+water_level parameter sets covered.

### A2 — First compose up (all services healthy)

- **First run after Rev 10**: rebuild the `sapphire-flow` image. The cached `sapphire-flow:latest` predates Plan 065 and does not contain the overlay loader code. Run `docker compose build` once before the first up. For iterative runs (restarting after a container exits), skip this step unless source changed. Alternatively pass `--build` inline to the up command below — at the cost of rebuilding every time.
- **First run from main (Rev 11 note, per F1):** `docker compose down -v` is mandatory if the DB contains state from prior A3 attempts (including from the now-deprecated `staging-5-stations` branch). Pre-existing stations route `onboard-model` into the full training + hindcast + skill-gate path instead of Rev 8's documented "empty-scope register-only" path, which can fail on partial data. A completely clean slate is the canonical starting point — if you want to preserve observations, `pg_dump` first.
- **`VERSION` env-var gotcha (Rev 11 note):** the compose file references `sapphire-flow:${VERSION}` with no default, so every `docker compose ...` invocation must set `VERSION=<tag>` or fail with "required variable VERSION is missing a value". The least-friction fix is a repo-root `.env` with `VERSION=<current-tag>` or `${VERSION:-latest}` default in `docker-compose.yml`. Until that hygiene patch lands, every invocation in this runbook must be prefixed `VERSION=<tag>`.
- `VERSION=<tag> docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.staging.yml up -d`. The `docker-compose.staging.yml` overlay (Plan 065) selects the 5-station A1 subset via `SAPPHIRE_CONFIG_OVERLAY`; the base `config.toml` on main stays at 169 stations and is re-used in A4.
- Wait for healthchecks: postgres, prefect-server, prefect-worker, api, caddy (max **5 min** on first boot, 3 min on warm restart). Verify `init` container exited 0: `docker compose logs init | tail`.
- Verify **9 deployments** registered (all by name): `forecast-cycle`, `ingest-observations`, `backup-database`, `train-models`, `run-hindcast`, `compute-skills`, `compute-combined-skills`, `onboard-stations`, `onboard-model`. Check via Prefect UI at `http://localhost:4200`.
- Record image pull time, first-boot time, resource usage at idle.
- **Exit**: `curl -s http://localhost:8010/api/v1/health | jq -e '.status == "ok" and .prefect_status == "ok"'` returns `true`. (Response shape: `{"status": "ok", "prefect_status": "ok", "checked_at": "<iso>"}` — three fields, per `health.py`.)

### A2.5 — `forecast.run_completed` instrumentation (rename + fields)

**This is a rename, not an addition.** `run_forecast_cycle.py:744` currently emits `log.info("forecast.station_completed", duration_ms=duration_ms)`. (Line reference updated after Plan 050 added run-name templates earlier in the file; if the line shifts again, anchor on the event name `forecast.station_completed` inside the per-station loop.) Rename to the canonical `forecast.run_completed` (per `logging.md` §Canonical forecast cycle events) and add the fields the A3 exit gate asserts on. This is a required in-scope production-code change.

Per-station emission inside the per-station forecast loop (replacing the existing `forecast.station_completed` line):
```python
log.info(
    "forecast.run_completed",
    duration_ms=duration_ms,
    ensemble_size=ensemble_size,
    lead_time_hours=lead_time_hours,
)
```

Field derivation (both fields must be computed inside the per-station loop before the log call):
- `ensemble_size`: `primary_ensemble.member_count` for the primary `ForecastEnsemble` on the per-model result — the same ensemble that was just stored. The loop produces one event per `(station, model)` pair: it iterates `all_ensembles[sid]: dict[ModelId, dict[str, ForecastEnsemble]]` and, for each model, picks the first `ForecastEnsemble` (all parameters share member count, horizon, and time_step within a single model run). `model_id` is bound via `bind_contextvars(model_id=str(mid))` around each emission so combination-mode runs stay distinguishable.
- `lead_time_hours`: `primary_ensemble.forecast_horizon_steps * primary_ensemble.time_step.total_seconds() / 3600` — computed off the same ensemble. `ForecastEnsemble` exposes `member_count`, `forecast_horizon_steps`, and `time_step` (`timedelta`) as properties; see `src/sapphire_flow/types/ensemble.py`. For v0a daily 5-step × 24h this resolves to `120`; for v0b+ hourly 120-step × 1h this also resolves to `120`.

(Earlier revisions of this section used `len(fc_result.ensemble.members)` / `len(fc_result.ensemble.timesteps)`; those attribute names do not exist on `ForecastEnsemble`. A2.5 implementation corrected to the real type surface.)

`duration_ms` is mandatory per logging.md §D6 and is already computed at `run_forecast_cycle.py:743` (was `:721` pre-Plan-050). `station_id` is **not** passed as a kwarg — it is already bound via `bind_contextvars(station_id=...)` at `run_forecast_cycle.py:521` (was `:499` pre-Plan-050) and will appear on the event automatically (logging.md §Context binding protocol rule 3).

This is the only production-code change in Stream A besides the pre-flight code in A1.

### A3 — 5-station dress rehearsal

Trigger flows in sequence through the Prefect UI. Each must complete without manual intervention. Record wall-clock time and peak RSS for each.

1. `onboard-model` — register `LinearRegressionDaily`, `ClimatologyFallbackModel`, `PersistenceFallbackModel` via Flow 13 (auto-promote path). Required before `onboard-stations` can assign models (see v0-scope.md §A4 step 6).
2. `onboard-stations` — 5 stations (four rivers + one lake) from `config.toml`
3. `ingest-observations` (manually triggered, 1 cycle). Assertion: at least 1 observation per station with `timestamp > T0` (where `T0` is the wall-clock start of this step) appears in the DB — verifies the real-time LINDAS poll path rather than silently no-oping. Observation fetch must be sequential (rate-limited) per BAFU LINDAS conventions; a minimum-rows-per-station floor must be respected — if any station returns zero rows for a non-first-run ingest, raise (do not silently continue).
4. `train-models` — linear regression, 5 stations. Trigger params: `{"model_ids": ["linear_regression_daily"]}` (note: `model_ids` is a plural **list** per the flow signature at `src/sapphire_flow/flows/train_models.py`). Plan 060 landed `cache_policy=NO_CACHE` across all lifecycle @tasks — prior to Plan 060 this step silently trained zero models because Prefect 3 crashed on store-typed hash inputs. **Rev 11 finding F3:** the retrain flow currently assembles training data only from observations ingested *after* initial onboarding; at A3 time that is typically 1 row per parameter per station, which fails the lookback≥8 check. Initial training already produced active artifacts inside `onboard-stations` (step 2), so step 4 can be **skipped in A3 pending Plan 066** (train-models retrain strategy — configurable, still under research). Do **not** block A3 on step 4 until Plan 066 lands; record in A5 as "skipped per F3".
5. `run-hindcast` — **per-station loop**. `run_hindcast_flow` raises `ValueError("Either station_id or group_id must be provided")` when both are None, so the trigger is issued once per onboarded station. Pull `(station_id, artifact_id)` tuples from `model_artifacts WHERE model_id = 'linear_regression_daily' AND status = 'active'` (lowercase enum value). **Rev 11 finding F2:** pass UUIDs in JSON-string form, not as bare tokens — Prefect CLI tries to JSON-parse the value of `-p key=value` and a raw UUID is not a valid JSON token, producing parameter-validation errors (empty string / trailing-space). Use `-p 'station_id="..."'` with double quotes **inside** the single quotes. Canonical example:

    ```bash
    VERSION=<tag> docker compose exec -T prefect-worker prefect deployment run run-hindcast/run-hindcast \
      -p 'station_id="a7ac3be7-ed74-4106-a3aa-5c676c1dc769"' \
      -p 'artifact_id="b95f0fd8-1046-4ade-b797-d989439d56a6"' \
      -p 'model_id="linear_regression_daily"'
    ```

    The `model_id` param is required by the deployment schema (was not called out in Rev 9).
6. `compute-skills` — pulls `(station_id, model_id, artifact_id, parameter)` from the DB; trigger with those four values. Same JSON-string quoting as step 5 applies. Canonical example:

    ```bash
    VERSION=<tag> docker compose exec -T prefect-worker prefect deployment run compute-skills/compute-skills \
      -p 'station_id="..."' -p 'artifact_id="..."' \
      -p 'model_id="linear_regression_daily"' -p 'parameter="discharge"'
    ```
7. `compute-combined-skills` — trigger once after `compute-skills` to validate registration and execution. In v0a with no pooled combinations this is a no-op but must run cleanly (Prefect run reaches `COMPLETED`).
8. `forecast-cycle` — one full cycle (gridded NWP path via `MeteoSwissNwpAdapter`). **Per Plan 060 D4**, `forecast-cycle` is triggered via **direct Python invocation** (not the Prefect UI deployment trigger) because `MeteoSwissNwpAdapter` is not JSON-serialisable and cannot be passed as a deployment parameter. The adapter-registry design is deferred to a future plan. Canonical template:

    ```bash
    VERSION=<tag> docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.staging.yml exec -T prefect-worker sh -c '
      PW=$(cat /run/secrets/db_password)
      export DATABASE_URL="postgresql+psycopg://sapphire:${PW}@postgres:5432/sapphire"
      cd /app && python -c "
    from pathlib import Path
    import httpx
    from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter
    from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow

    adapter = MeteoSwissNwpAdapter(
        stac_base_url=\"https://data.geo.admin.ch/api/stac/v1\",
        stac_collection=\"ch.meteoschweiz.ogd-forecasting-icon-ch2\",
        scratch_path=Path(\"/tmp/sapphire_nwp\"),
        http_client=httpx.Client(timeout=60),
    )
    result = run_forecast_cycle_flow(adapter=adapter)
    print(\"forecast-cycle result:\", result)
    "'
    ```

    **Rev 11 notes on step 8 (findings F4 + F5):**
    - **`DATABASE_URL` construction boilerplate is required** — `docker compose exec` does not inherit the entrypoint-built env, so `DATABASE_URL` must be constructed inline from `/run/secrets/db_password` + the `DATABASE_URL_TEMPLATE`. Earlier Rev-9 template omitted this and failed with `KeyError: 'DATABASE_URL'`.
    - **Call as `run_forecast_cycle_flow(adapter=adapter)`** (real Prefect flow invocation), NOT `.fn(adapter=adapter)`. The flow internally calls `_fetch_nwp_task.submit(...)` which requires a task-runner context — `.fn` bypasses Prefect runtime and the `.submit` call raises `RuntimeError: Unable to determine task runner`. Rev 9's reference to `.fn(...)` is superseded.
    - **MeteoSwiss cycle "late" signal needs adapter investigation (Plan 067).** During the 2026-04-21 rehearsal the adapter aborted with "No cycle available within 3 fallback steps". MeteoSwiss is reliable; the signal is almost certainly a query-implementation issue in `MeteoSwissNwpAdapter` (wrong datetime-filter semantics, wrong sort order, or pagination bug). Plan 067 investigates before assuming anything about MeteoSwiss publication lag.
    - **Adapter has a hardcoded `_MAX_FALLBACK_STEPS=3` and a 100-page STAC pagination cap** — both need to become configurable (or the cap removed via server-side filtering). Plan 067 covers the configurability work once the root cause is understood.

    The flow body uses Plan 059's production-bootstrap to resolve every store from `DATABASE_URL`; only `adapter` needs explicit injection. `run_forecast_cycle_flow` is sync — do **not** wrap in `asyncio.run(...)`.
9. API spot checks: `GET /api/v1/stations`, `GET /api/v1/stations/{station_id}/forecasts?limit=1`, `GET /api/v1/alerts`

**Exit**: all 9 steps green, forecast appears in DB, API returns it. structlog contains `forecast.run_completed` events with `duration_ms` and `station_id` for each station — confirms per-step instrumentation from v0-scope.md §D6 is active.

### A4 — 169-station scale-up

**⚠ Rev 11 blocker (Plan 068):** the 2026-04-21 A3 rehearsal took **38 minutes** for `onboard-stations` at 5 stations — sequential per-station per-model historical-hindcast phase. Linear extrapolation to 169 stations is ~26 hours, which is operationally prohibitive for a Mac-mini staging cutover and a Nepal production cutover both. **Do not attempt A4 until Plan 068 lands** (decouple initial historical-hindcast from `onboard-stations` into an asynchronous `backfill-hindcasts` flow, and/or parallelise the per-station loop via Prefect `task.map`). Instrument `docker stats --no-stream` polling during the A4 run to capture peak RSS (finding F6 — compute-skills transient OOM under parallel load on a 16 GiB Docker Desktop allocation).

**Pre-`down -v` checkpoint**: before wiping the 5-station run, run:
```bash
docker compose exec -T postgres pg_dump -U sapphire sapphire > pre-down-$(date +%F).dump
```
**Runbook human-confirmation step**: "Only proceed with `docker compose down -v` if the current DB is throwaway. If real observations were ingested during A3 that you may need, keep this dump."

Drop `-f docker-compose.staging.yml` from the compose command to deselect the 5-station overlay; the base `config.toml` on `main` already carries all 169 stations. Verify before re-upping: `grep -c '^\s*"[0-9]*"' config.toml` should return 169 (or equivalent grep that counts basin_ids entries). Then:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```
Repeat A3 end-to-end (including `onboard-model` as step 1).

Record: onboarding wall-clock, forecast-cycle wall-clock, peak RSS per container, DB size after one cycle, NWP Zarr volume size.

**Exit gates**:
- Forecast cycle completes in **< 60 s** — hard target per v0-scope.md §D (full cycle at ~170 stations). If this measurement exceeds 60 s, the plan is **blocked** — escalate as a performance blocker before continuing to Stream C.
- No container OOMs.
- No Prefect task retries due to flakiness (retries due to real STAC/LINDAS transient errors are acceptable and noted).

### A5 — Dress-rehearsal report

Commit a short markdown doc: `docs/deployment/dress-rehearsal-YYYY-MM-DD.md`.

Required sections:
- What broke, what we fixed, what we deferred (→ numbered follow-up plans).
- Resource baseline (RAM, CPU, disk, network).
- Timing baselines (per-flow wall-clock for 5 and 169 stations).
- Mac-specific gotchas we hit (Docker Desktop quirks, file-permission issues, arm64 image availability).

**Exit**: report committed. Any blockers → spawn a numbered fix plan. Plan 046 pauses on Stream A until the blocker resolves. After A5 is committed it is read-only — any finding requiring code changes spawns a numbered follow-up plan.

---

## Stream B — Compose-stack smoke + edge-case test suite

Runs in parallel with Stream A. Produces a reusable test tier that protects every future deployment.

### B1 — Deployment test harness

- Use `testcontainers.compose.DockerCompose` for parity with the existing integration tier's use of `testcontainers.postgres`. **Dependency gap**: `pyproject.toml` today declares only `testcontainers[postgres]` — the `compose` module lives in the `testcontainers-compose` extras set and is not installed. Add `testcontainers[compose]` (or `testcontainers-compose` directly, depending on the installed `testcontainers` version) to the `dev` dependency group as part of B1, and record the chosen distribution in the B1 PR.
- `tests/deployment/__init__.py`, `tests/deployment/conftest.py`.
- Two fixtures:
  - `compose_stack` (session-scoped): starts stack, waits for health endpoints, yields, tears down with `docker compose down -v`. Used for non-destructive tests (B2, B3, B4).
  - `fresh_compose_stack` (function-scoped): calls `docker compose down -v` and a fresh `up -d` per test. Used for destructive tests (B7, B8, B9, B10, B11, B12).
- B10 and B11 drive `docker compose` directly (compose-up must fail) and therefore use neither fixture — they invoke the compose CLI inline.
- Mark all deployment tests with `@pytest.mark.deployment`; default `pytest` excludes them, CI opts in explicitly. Mark destructive tests additionally with `@pytest.mark.deployment_destructive`; default local `pytest` also excludes them, nightly CI opts in separately.
- Add to `pyproject.toml` `[tool.pytest.ini_options]`:
  ```toml
  markers = [
      "deployment: requires a full compose stack; excluded by default",
      "deployment_destructive: destructive compose tests; excluded by default",
  ]
  addopts = "-m 'not deployment'"
  ```

### B2 — Happy-path smoke test

`test_stack_boots_cleanly`: fresh compose up → 5 long-running services (postgres, prefect-server, prefect-worker, api, caddy) become healthy within **5 min** (first boot) → `init` exited 0 → 9 Prefect deployments registered → `curl -s http://localhost:8010/api/v1/health | jq -e '.status == "ok" and .prefect_status == "ok"'` returns `true` → teardown.

### B3 — Idempotency test

`test_init_reruns_safely`: boot stack → run `docker compose run --rm init` a second time → verify:
- alembic idempotent (no new migrations applied on re-run),
- Prefect deployment count unchanged at 9 (`forecast-cycle`, `ingest-observations`, `backup-database`, `train-models`, `run-hindcast`, `compute-skills`, `compute-combined-skills`, `onboard-stations`, `onboard-model`),
- no ERROR or CRITICAL entries in init container logs on re-run.

(Drop assertion "station config in DB unchanged" — init does not manage stations.)

### B4 — Volume persistence test

`test_volumes_survive_restart`: boot stack → onboard 1 station via CLI → `docker compose down` (no -v) → `docker compose up -d` → verify station still in DB and Prefect deployments still registered. Assert surviving volumes by name: `pgdata`, `prefect_data`, `model_artifacts`.

### B5 — Edge: NWP source unavailable

`test_forecast_cycle_handles_nwp_outage`: start stack with `METEOSWISS_STAC_URL=https://localhost:9999` (unreachable HTTPS port). Trigger forecast cycle. Assert: Prefect run reaches `FAILED` state with a clear error message, no partial forecast written to DB.

**HTTPS note**: `MeteoSwissNwpAdapter._download_asset` (line 184) guards `if not href.startswith("https://"): raise AdapterError(...)`. The env-var override must therefore use an `https://` URL. For the "unreachable" variant this is fine — connection refuses at TCP. For richer fake-server scenarios (see B13 `fake_stac_server`), serve real TLS via a self-signed cert and inject a trust root, **or** inject a patched `httpx.Client` on the adapter and do not exercise the URL guard.

(Drop `degraded` assertion — the health endpoint never returns `degraded`.)

### B6 — Edge: LINDAS timeout

`test_obs_ingest_handles_lindas_slowness`: inject a slow HTTP proxy that adds 60s latency between adapter and real LINDAS. Assert: adapter respects configured timeout, obs ingest fails fast, alert logged.

### B7 — Edge: DB crash mid-cycle

`test_db_crash_during_forecast`: poll flow-run state to synchronise the kill. Use two `PrefectClient.read_flow_run(run_id)` calls — one for the onboarding flow run ID, one for the forecast flow run ID — to confirm `onboarding` run has reached `COMPLETED` and `forecast-cycle` run has reached `RUNNING` before issuing the kill. If either state is not reached within 60s the test fails loudly. Sketch:

```python
async with get_client() as client:
    onboard_run = await client.read_flow_run(onboard_run_id)
    forecast_run = await client.read_flow_run(forecast_run_id)
    # assert expected states before killing postgres
```

After killing postgres, restart it → verify: Prefect marks run `FAILED`, next manual trigger succeeds cleanly, no orphaned rows in `forecasts` table.

### B8 — Edge: disk near-full

Pre-requisite in-scope production-code change: add a free-space pre-flight check to `backup_database_flow` (see Files to modify). If the target volume has < 5 % free, raise `BackupRefusedError` (defined in `src/sapphire_flow/exceptions.py`, inheriting `SapphireError`) before invoking `pg_dump`. Also refuse if the sentinel file at `<backup_dir>/.sapphire-backup-volume` is absent (USB disk not mounted). The pre-flight reads the path **inside the container** — i.e. `/data/backups/.sapphire-backup-volume` under the default `backup_dir`. The sentinel must be created on the host at the bind-mount root, `/Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume`, which the overlay maps 1:1 to the container-side path. These are small, necessary production additions motivated by this test and the backup volume mount check from D8 — acknowledged as production code, not hidden behind "test precondition."

`test_backup_refuses_on_low_disk`: mount a size-limited tmpfs Docker volume (see B13) as backup target, fill to 95 %, trigger backup flow. Assert: backup flow logs error, returns non-zero, no new `.dump` file written, previous backup preserved.

(Drop `degraded` assertion — health endpoint never returns `degraded`. Drop: "health endpoint reports degraded.")

### B9 — Edge: invalid config.toml

`test_init_fails_loudly_on_invalid_config`: ship a malformed `config.toml` (unknown station type). Run init. Assert: non-zero exit, error message names the offending field, no partial DB state left behind.

### B10 — Edge: missing `db_password` secret

`test_compose_fails_without_db_password`: omit `./secrets/db_password`. Assert: `docker compose up` exits non-zero with an error message naming `./secrets/db_password` — postgres never starts.

### B11 — Edge: alembic migration fails

`test_init_fails_on_migration_error`: inject a bad migration into a **copy** of `alembic/versions/` under `tmp_path` (so the real tree is never polluted on failure). Point `ALEMBIC_CONFIG` env var at the copy. Assert: init exits non-zero, API and workers do not start, migration tip in DB unchanged from pre-boot.

### B12 — Edge: container restart mid-cycle

`test_worker_restart_during_cycle`: trigger forecast cycle → kill prefect-worker mid-run → worker restarts → verify: failed run stays `FAILED` (Prefect does not auto-retry at infrastructure level), next cycle runs normally.

### B13 — Fault-injection utilities

Pytest fixtures / helpers centralised in `tests/deployment/fault_injection.py`:
- `fake_stac_server(status_code, delay_ms)` — FastAPI instance that impersonates the MeteoSwiss STAC API. **TLS requirement**: `MeteoSwissNwpAdapter._download_asset` at line 184 rejects any asset `href` that does not start with `https://`, so the fake server must either (a) listen on TLS with a self-signed cert the test trusts via `SSL_CERT_FILE` / `httpx.Client(verify=...)` on an injected client, or (b) the test bypasses the URL guard entirely by constructing the adapter with an injected `httpx.Client` whose transport returns canned responses. Option (b) is simpler — prefer it unless the test specifically needs to exercise the URL-validation code path.
- `slow_http_proxy(delay_ms)` — preferred implementation for B6: an `httpx.MockTransport` (or `httpx.HTTPTransport` wrapped in a `DelayingTransport` that sleeps `delay_ms` before delegating) injected into the adapter's `httpx.Client`. Avoids mitmproxy and the HTTPS certificate-injection problem entirely. The original "mitmproxy-style proxy" framing is retired.
- `fill_disk_to(path, percent)` — context manager using a size-limited tmpfs Docker volume. **Linux (and macOS via Docker)**: create with `docker volume create --driver local --opt type=tmpfs --opt o=size=100m --opt device=tmpfs <name>` then bind-mount as `backup_dir` in a compose override. Write `os.urandom` bytes (not zeros) to defeat APFS transparent compression. Verify `shutil.disk_usage()` / `os.statvfs()` operate correctly against tmpfs before use. Cleanup on exit guaranteed. Do NOT use `mount -o loop` (requires root on Linux; not cross-platform).
- `kill_container(service_name)` / `pause_container(service_name)` — wrappers around the Docker CLI. **Name resolution**: `testcontainers.compose.DockerCompose` creates a randomised project name (e.g. `tc_<uuid>`), so raw container names like `sapphire-postgres-1` are not predictable. The helpers accept a compose **service** name (`postgres`, `prefect-worker`, …) and resolve the live container ID at call time via `docker compose -p <project> ps --format json --filter "service=<name>"`, extracting `.Name` or `.ID`. The compose project name must be captured from the `DockerCompose` fixture and passed through (either via a helper module-level variable set by the fixture, or as an explicit argument to each call).

**Exit gates (Stream B)**:
- All deployment tests pass on the MacBook Pro.
- Session-scoped non-destructive subset (B2–B4) wall-clock < 10 min.
- Full destructive-inclusive tier wall-clock < 60 min.
- CI job added (opt-in `-m deployment`, not on every PR) that runs the deployment tier nightly; separate opt-in `-m deployment_destructive` for the destructive subset.

---

## Stream C — Mac-mini deployment glue

Depends on **A5 complete** (dress-rehearsal report). May run in parallel with Stream B.

Streams renamed from the original draft. Current stream mapping:
- C0: Apple Silicon verification
- C1: launchd LaunchAgent (main stack)
- C2: backup volume (external USB + overlay mount)
- C3: watchdog LaunchAgent
- C4: Mac-mini runbook
- C5: LAN access procedure (SSH tunnel; optional dev overlay for office LAN)

### C0 — Apple Silicon + Docker Desktop verification

- Confirm both machines are Apple Silicon: `uname -m` → `arm64`.
- Confirm all images have arm64 variants: `docker manifest inspect` for `postgis/postgis:16-3.4`, `prefecthq/prefect:3-python3.11`. (All do as of 2026-04, but verify at plan execution time.)
- Confirm the custom `sapphire-flow` image builds natively on both machines.
- If any image is amd64-only, document the workaround and add a remediation task to the plan.

### C1 — launchd LaunchAgent (main stack)

- `scripts/launchd/ch.hydrosolutions.sapphire.plist` — LaunchAgent (user-context) that runs the wrapper script at login, logs to `~/Library/Logs/sapphire-flow.log`.
- `scripts/launchd/install-launchd.sh` — copies plist to `~/Library/LaunchAgents/`, runs `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ch.hydrosolutions.sapphire.plist`.
- `scripts/launchd/start-sapphire.sh` — wrapper script that waits for Docker Desktop before calling compose. `WAIT_MAX` is set to **240** (4 min) — Docker Desktop on Apple Silicon can take 90–120 s to expose the socket on a cold boot after OS restart (VirtioFS initialisation + Linux VM kernel boot), and 60 s is insufficient in practice. The LaunchAgent plist sets `KeepAlive = { SuccessfulExit = false }` and `ThrottleInterval = 60` so that if Docker Desktop is still not ready after 4 min the agent exits non-zero and launchd retries after a 60 s throttle — avoiding a permanently stranded stack. Exact wrapper:
  ```bash
  #!/bin/bash
  set -e
  WAIT_MAX=240
  WAITED=0
  until docker info >/dev/null 2>&1; do
      if [ "$WAITED" -ge "$WAIT_MAX" ]; then
          echo "Docker Desktop did not start within ${WAIT_MAX}s — aborting" >&2
          exit 1
      fi
      sleep 3
      WAITED=$((WAITED + 3))
  done
  exec docker compose \
      -f /path/to/sapphire/docker-compose.yml \
      -f /path/to/sapphire/docker-compose.macmini.yml \
      up -d
  ```
  Replace `/path/to/sapphire` with the actual repo path on each machine.

- Main-stack plist must include `RunAtLoad = true`, `KeepAlive = { SuccessfulExit = false }`, and `ThrottleInterval = 60` (in seconds). Do **not** use unconditional `KeepAlive = true` — that would relaunch the wrapper immediately on normal exit (once compose is `up -d`, the wrapper exits 0 and its job is done). The `SuccessfulExit = false` form relaunches only on non-zero exit, which is the intended behaviour for the Docker-Desktop-not-ready case.
- Test: reboot MacBook Pro with plist installed, confirm stack auto-starts.

### C2 — Backup volume: external USB + overlay mount

- External USB SSD ≥ 500 GB, APFS, mounted at `/Volumes/sapphire-backup`.
- Create the `pg_dumps` subdirectory and sentinel file at setup time. The sentinel's host path must correspond 1:1 to the container-side path the pre-flight reads (`/data/backups/.sapphire-backup-volume`):
  ```bash
  mkdir -p /Volumes/sapphire-backup/pg_dumps
  touch /Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume
  ```
- `docker-compose.macmini.yml` overlay must include:
  ```yaml
  services:
    prefect-worker:
      volumes:
        - /Volumes/sapphire-backup/pg_dumps:/data/backups:rw
  ```
  This shadows the `backups` named volume with the USB host path. `backup_database_flow` writes to `/data/backups` inside the container — dumps land on the USB disk.
- Pre-flight in `backup_database_flow`: if `<backup_dir>/.sapphire-backup-volume` (default `/data/backups/.sapphire-backup-volume`, container-side) is absent, raise `BackupRefusedError("USB backup disk not mounted")` before invoking `pg_dump`.
- Nightly `backup-database` at 02:00 UTC (as wired by Plan 044). No additional configuration.
- No restore rehearsal in v0; defer to Plan 048.
- **BLOCKING for D2 — add `/data/raw` mount to `docker-compose.macmini.yml`** (Plan 060 prerequisite): Plan 060 removed the base-compose `sapphire_data:/data/raw:rw` mount from `prefect-worker`, so the mac-mini overlay now has no `/data/raw` mount at all unless C2 adds one. Without this, D2's `onboard-stations` will fail with "CAMELS-CH not found" (the same class of error dev hit before A3 step 2). Options: (a) declare a new named volume `sapphire_data_macmini` and stage CAMELS-CH via `docker cp` at provisioning time, or (b) bind-mount a host path on the Mac mini where the operator pre-stages the dataset (mirrors the dev-overlay pattern, read-only). Choice is C2 implementer's call; either is compatible with Plan 060.

### C3 — Watchdog LaunchAgent

- `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist` — LaunchAgent (user-context) that runs every 5 min (`StartInterval: 300`).
- Plist skeleton (required inline — do not omit):
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0">
  <dict>
      <key>Label</key>
      <string>ch.hydrosolutions.sapphire-watchdog</string>
      <key>ProgramArguments</key>
      <array>
          <string>/usr/local/bin/uv</string>
          <string>run</string>
          <string>python</string>
          <string>-m</string>
          <string>sapphire_flow.ops.watchdog</string>
      </array>
      <key>StartInterval</key>
      <integer>300</integer>
      <key>StandardOutPath</key>
      <string>/Users/sapphire/Library/Logs/sapphire-watchdog.log</string>
      <key>StandardErrorPath</key>
      <string>/Users/sapphire/Library/Logs/sapphire-watchdog.log</string>
      <key>RunAtLoad</key>
      <false/>
  </dict>
  </plist>
  ```
- Watchdog log rotation: use `newsyslog` (macOS native). Add `/etc/newsyslog.d/sapphire-watchdog.conf`:
  ```
  /Users/sapphire/Library/Logs/sapphire-watchdog.log  sapphire:staff  640  7  1024  *  J
  ```
  (7 rotations, 1 MB size threshold, bzip2 compression.)
- `src/sapphire_flow/ops/watchdog.py` — Python wrapper that:
  1. Probes `http://localhost:8000/api/v1/health` (not `/api/v1/health/detail` — that endpoint is deferred to v0b per v0-scope.md §J).
  2. Emits `pipeline.health_check_completed` structlog event before posting to Slack.
  3. Posts Slack alert on health check failure. Slack webhook URL stored in `./secrets/slack_webhook_url` (`chmod 600`). Read directly by the Python process (host-process secret, not via `/run/secrets`). See security.md update in Files to modify.
  4. Checks backup staleness: if the newest `*.dump` in `/Volumes/sapphire-backup/pg_dumps/` is older than 26 h, post a Slack alert.
  5. **Hysteresis**: alert on first failure, then on recovery and every 6th subsequent consecutive failure (= approximately every 30 min). Track consecutive failure count in a state file (e.g. `~/.sapphire-watchdog-state.json`).
  6. Slack message format:
     ```
     [SAPPHIRE staging] health check FAILED — host: <hostname>, time: <ISO>, http_status: <N or "unreachable">
     ```
     Backup staleness alert:
     ```
     [SAPPHIRE staging] backup STALE — newest dump: <ISO timestamp or "none found">, threshold: 26h
     ```
- `scripts/launchd/watchdog.sh` — one-liner: `uv run python -m sapphire_flow.ops.watchdog`.
- `src/sapphire_flow/ops/__init__.py` — package marker.

### C4 — Mac-mini runbook

`docs/deployment/mac-mini-staging.md`. Sections:
- **Hardware prerequisites**:
  - Mac mini, Apple Silicon, ≥ 16 GB RAM, ≥ 1 TB internal SSD.
  - External USB SSD ≥ 500 GB (APFS, for backups). Create the backup subdirectory and sentinel: `mkdir -p /Volumes/sapphire-backup/pg_dumps && touch /Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume` (host path maps to container `/data/backups/.sapphire-backup-volume` via the overlay bind-mount).
  - **UPS recommendation**: connect the Mac mini to an uninterruptible power supply (at minimum, a ≥ 600 VA model). macOS supports UPS shutdown signalling — configure via System Settings → Battery → UPS. This prevents filesystem corruption on power loss and reduces WAL recovery events. Not mandatory but strongly recommended before any multi-week unattended run.
  - **Disable Docker Desktop auto-update**: Docker Desktop → Settings → Software Updates → uncheck "Automatically check for updates." Unattended auto-updates can disrupt the running stack mid-cycle.
  - **Disable macOS software auto-update**: System Settings → General → Software Update → uncheck "Install macOS updates" and "Install application updates from the App Store." Enable only security response updates if desired. Re-evaluate before planned maintenance windows.
- macOS prerequisites: Homebrew, Docker Desktop (≥ 4.30), `uv`.
- Docker Desktop resource config: ≥ 16 GB RAM, ≥ 8 CPUs, ≥ 100 GB disk.
- `git clone`, secrets bootstrap (`db_password` only for Plan 046).
- First boot + init verification.
- Station scale ramp (5 → 169).
- Configure macOS auto-login: System Settings → Users & Groups → Automatic Login → `sapphire`.
- Auto-start via launchd LaunchAgent (C1).
- Backup setup (C2): mount USB disk, create sentinel, wire overlay, verify nightly job.
- Watchdog setup (C3): install plist, provision Slack webhook, verify alert.
- **LAN access (SSH tunnel)**: from any team laptop on the office LAN:
  ```bash
  ssh -N -L 8010:localhost:8000 -L 4200:localhost:4200 sapphire@<mac-mini-lan-ip>
  # Then in another terminal:
  curl -s http://localhost:8010/api/v1/health | jq .
  # Prefect UI: http://localhost:4200
  ```
  Add a `~/.ssh/config` stanza on each team laptop for convenience:
  ```
  Host sapphire-staging
      HostName <mac-mini-lan-ip>
      User sapphire
      LocalForward 8010 localhost:8000
      LocalForward 4200 localhost:4200
  ```
- Date & Time: confirm "Set automatically" is enabled; verify with `sntp -sS time.apple.com`.
- Mac mini is dedicated to SAPPHIRE staging; no other tenants permitted.
- Troubleshooting: container won't start, Docker Desktop resource exhaustion, disk full, SSH tunnel won't connect, backup disk unmounted.
- Upgrade procedure: `docker compose pull && docker compose stop prefect-worker && docker compose run --rm init && docker compose up -d`.
- **Flows that require direct-invoke rather than Prefect UI trigger** (Plan 060 D4): `forecast-cycle`, non-empty `onboard-model`, and non-empty `train-models` cannot be triggered through the Prefect deployment UI because their inputs (`MeteoSwissNwpAdapter`, `forcing_source`, model Python objects) are not JSON-serialisable deployment parameters. The adapter-registry design is deferred to a future plan. Until it lands, operators invoke these flows via:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.macmini.yml exec -T prefect-worker python -c "<flow_invocation>"
  ```
  See Plan 060 §T4 for the canonical `forecast-cycle` template. Document each flow's exact invocation in the runbook so operators are not reverse-engineering adapter constructors at 2 a.m.

### C5 — LAN access verification

From a fresh team laptop (hydrologist or ML expert, not the IT specialist/implementer):
1. Follow the SSH-tunnel section of `docs/deployment/mac-mini-staging.md` top-to-bottom using only the runbook — no guidance from the IT specialist.
2. Verify: `curl -s http://localhost:8010/api/v1/health | jq -e '.status == "ok" and .prefect_status == "ok"'` returns `true`.
3. Verify Prefect UI is accessible at `http://localhost:4200` through the tunnel.
4. Record any runbook gap found → fix runbook immediately.

**Exit gates (Stream C)**:
- All glue committed, Stream B passes on the MacBook Pro with launchd plist installed.
- A team member (hydrologist or ML expert, not IT specialist) can reach the API via the documented SSH-tunnel procedure from a fresh laptop using only the runbook — verified in C5.

---

## Stream D — Mac-mini operational validation

Depends on **Stream B DONE** and **Stream C DONE**.

### D1 — Clean-state install

Wipe any prior state on the Mac mini. Assert pre-flight: ≥ 50 GB free on Docker VM disk **and** ≥ 50 GB free on `/Volumes/sapphire-backup`. Follow `docs/deployment/mac-mini-staging.md` top-to-bottom. Time each step. Any undocumented step → fix runbook.

### D2 — 5-station operation

Same as A3 (including `onboard-model` as step 1) but on Mac mini. Leave running unattended for 24 h. Confirm:
- One scheduled `forecast-cycle` runs on its configured schedule (see `register_deployments.py`) without manual trigger.
- One scheduled `ingest-observations` runs on its configured schedule.
- Nightly `backup-database` at 02:00 UTC completes, a new `.dump` (pg_dump custom format) appears on the external USB disk at `/Volumes/sapphire-backup/pg_dumps/`.
- Health endpoint stays `ok` the whole time.

### D3 — 169-station operation

Restore full station list (now 169 after adding 2033 and 2085). Leave running 48 h.
- Two scheduled forecast cycles complete.
- Obs ingest runs ~96 times.
- Disk usage monitored via `df -h`; peak captured.
- API latency probed every 5 min over the SSH tunnel from a team laptop on the office LAN.

### D4 — Multi-day unattended run

Continue D3 for another 5 days (total ~7 days since stack start). No manual intervention. Daily: glance at Prefect UI + health endpoint via SSH tunnel.

### D5 — Power-loss simulation

Physically pull power. Wait 30 s. Plug back in. Confirm:
- macOS boots.
- Auto-login brings up the `sapphire` user session.
- Docker Desktop auto-starts (System Settings → General → Login Items).
- LaunchAgent fires and brings up the stack.
- PostgreSQL WAL recovers cleanly.
- Health endpoint reaches `ok` within 5 min of power-on.
- Next scheduled cycle runs normally.

Repeat once to confirm determinism.

### D6 — Docker Desktop restart

Quit Docker Desktop from the menu bar. Relaunch. Confirm stack comes back up and all healthchecks clear within 3 min (warm restart).

### D7 — Operational validation report

`docs/deployment/mac-mini-validation-YYYY-MM-DD.md`.
- 7-day run summary: forecast cycles attempted vs completed, obs ingest runs attempted vs completed, any manual interventions. Cycle completion defined as Prefect `forecast-cycle` flow run reaching `COMPLETED` state; transient-error retries that ultimately succeed are not failures.
- Resource high-water marks (RAM, CPU, disk including Docker VM + USB backup volume).
- Peak disk usage vs 50 GB pre-flight thresholds.
- Outstanding issues → numbered follow-up plans.
- **Go / no-go recommendation**: Mac mini is / is not ready to be the v0 staging host the team relies on.

**Exit gates (Stream D)**:
- 7-day unattended run with ≥ 95 % cycle completion rate (soft target).
- Power-loss recovery demonstrated twice.
- Validation report committed; go / no-go called.

---

## Dependency graph

```json
{
  "stream-a": {
    "tasks": ["A0", "A1", "A2", "A2.5", "A3", "A4", "A5"],
    "parallel": false,
    "depends_on": ["Plan 045 DONE"]
  },
  "stream-b": {
    "tasks": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B13"],
    "parallel": {"sequential": ["B1", "B2", "B3", "B4"], "parallel_after_B4": ["B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12"], "B13_interleaved": true},
    "depends_on": ["A0"]
  },
  "stream-c": {
    "tasks": ["C0", "C1", "C2", "C3", "C4", "C5"],
    "parallel": "C0 first, then C1-C5 in parallel",
    "depends_on": ["A5"]
  },
  "stream-d": {
    "tasks": ["D1", "D2", "D3", "D4", "D5", "D6", "D7"],
    "parallel": false,
    "depends_on": ["stream-b", "stream-c"]
  }
}
```

Critical path: `A0 → max(A5→C-tail, B-tail) → D1 → D7`. Stream B is likely the longer arm of the fork. Streams A and B run in parallel from day 1 (B depends on A0, not A5).

---

## Files to create

| Path | Stream | Purpose |
|---|---|---|
| `docs/plans/046-mac-mini-staging-deployment.md` | — | This plan |
| `docs/deployment/dress-rehearsal-YYYY-MM-DD.md` | A5 | Dress-rehearsal report |
| `docs/deployment/mac-mini-staging.md` | C4 | Mac-mini runbook |
| `docs/deployment/mac-mini-validation-YYYY-MM-DD.md` | D7 | Operational validation report |
| `tests/deployment/__init__.py` | B1 | Test tier marker |
| `tests/deployment/conftest.py` | B1 | `compose_stack` and `fresh_compose_stack` fixtures |
| `tests/deployment/test_stack_smoke.py` | B2–B4 | Happy-path + idempotency + persistence |
| `tests/deployment/test_stack_edge_cases.py` | B5–B12 | Fault-injection scenarios |
| `tests/deployment/fault_injection.py` | B13 | Shared fault-injection utilities |
| `scripts/launchd/ch.hydrosolutions.sapphire.plist` | C1 | Main stack LaunchAgent (user-context) |
| `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist` | C3 | Watchdog LaunchAgent (user-context) |
| `scripts/launchd/install-launchd.sh` | C1 | Bootstrap script |
| `scripts/launchd/start-sapphire.sh` | C1 | Docker Desktop wait + compose up wrapper |
| `scripts/launchd/watchdog.sh` | C3 | Slack-posting watchdog one-liner |
| `docker-compose.macmini.yml` | C1, C2 | Mac-mini compose overlay: USB bind-mount, Caddy `tls internal` |
| `src/sapphire_flow/ops/watchdog.py` | C3 | Python wrapper emitting `pipeline.health_check_completed` + Slack post + backup staleness check |
| `src/sapphire_flow/ops/__init__.py` | C3 | Package marker |

## Files to modify

| Path | Stream | Change |
|---|---|---|
| `docs/standards/cicd.md` | C1, C4 | Add launchd LaunchAgent alongside systemd; document Docker Desktop wait wrapper; document Mac-mini compose overlay; add note on Prefect history pruning env var (D12); note `prefect-server` `[backend, frontend]` will lose `frontend` in Plan 049. |
| `docs/standards/security.md` | C3 | Add `slack_webhook_url` to §Secrets management as a "host-process secret" exception: file under `./secrets/`, `chmod 600`, read directly by the watchdog Python process (not via `/run/secrets`). Explicitly document this exception class with rationale. |
| `docs/v0-scope.md` | B1 | §E5: add deployment test tier as opt-in nightly CI job (`-m deployment`); add separate `-m deployment_destructive` opt-in for destructive subset. |
| `pyproject.toml` | B1 | Add `[tool.pytest.ini_options].markers` for `deployment` and `deployment_destructive`; add `addopts = "-m 'not deployment'"`. Add `testcontainers[compose]` (or `testcontainers-compose`) to the `dev` dependency group — only `testcontainers[postgres]` is declared today. |
| `config.toml` | A1 (permanent) | Add basin_ids `2033` and `2085`; full list grows from 167 to 169. |
| ~~`config.toml`~~ | ~~A1 (transient, branch only)~~ | **SUPERSEDED (Rev 10):** the 5-station subset is now provided by `config/overlays/staging-5-stations.toml` per Plan 065; no transient edit to `config.toml` required. The `staging-5-stations` branch is deprecated. |
| `docker-compose.yml` | D12 | Add `PREFECT_API_DATABASE_PRUNE_OLDER_THAN=30` env var to `prefect-server` service (30-day Prefect history pruning). |
| `src/sapphire_flow/flows/backup.py` | B8, C2 | Add free-space pre-flight: if target volume < 5 % free OR sentinel file absent, raise `BackupRefusedError` before `pg_dump`. |
| `src/sapphire_flow/exceptions.py` | B8, C2 | Add `BackupRefusedError(SapphireError)`. |
| `src/sapphire_flow/flows/run_forecast_cycle.py` | A2.5 | **Rename** the existing `log.info("forecast.station_completed", duration_ms=...)` at line 744 (was `:722` pre-Plan-050) to `forecast.run_completed` and add `ensemble_size` + `lead_time_hours` fields (derived per A2.5). `station_id` is already bound via `bind_contextvars` at line 521 (was `:499` pre-Plan-050) — do **not** re-pass it as a kwarg. Anchor on event-name search if line references have shifted again. |
| `src/sapphire_flow/adapters/hydro_scraper.py` | A1 | Add public method `verify_gauge_reachable(site_code: str, station_kind: StationKind) -> bool` (issues a live HTTP SPARQL probe against LINDAS using `_build_sparql_query(site_code, station_kind)`; returns `True` on HTTP 2xx with ≥ 1 binding, `False` on 4xx/5xx or empty response, raises `AdapterError` on network failure). `station_kind` is required because `_build_sparql_query` dispatches on river vs lake. |

---

## Exit gates for Plan 046

1. All of Stream A passes (dress-rehearsal report committed, no unresolved blockers; forecast cycle < 60 s hard target met).
2. All of Stream B passes locally and on nightly CI.
3. All of Stream C glue shipped, runbook complete.
4. Stream D validation report committed with a **go** recommendation.
5. **LAN access exit gate**: a team member (hydrologist or ML expert, not the IT specialist/implementer) can reach the API via the documented SSH-tunnel procedure from a fresh laptop using only `docs/deployment/mac-mini-staging.md` — no guidance from the IT specialist. Verified by running `curl -s http://localhost:8010/api/v1/health | jq -e '.status == "ok" and .prefect_status == "ok"'` successfully.
6. Orchestrator asks the user to save memory entries for: "v0 staging runs on Mac mini (LAN-only access via SSH tunnel)", "deployment test tier exists in `tests/deployment/`", "macOS deployment uses launchd LaunchAgent", "public HTTPS via Cloudflare Tunnel is Plan 049".

## Deferred to follow-up plans

- **Public URL via Cloudflare Tunnel + Cloudflare Access (Entra ID SSO + external-viewer OTP) — Plan 049.** Plan 049 depends on Plan 046.
- restic + encrypted backup + monthly restore rehearsal (Plan 048).
- Off-site backup target (needs AWS or hydromet availability).
- Automated cross-machine output comparison (MacBook Pro vs Mac mini).
- Mac-mini → AWS / hydromet migration playbook.
- Nepal v1 data sources + secrets (Plan 047+).
- `/api/v1/health/detail` endpoint (deferred to v0b — `pipeline_health` table not populated until Flow 4).

## Risks

| Risk | Mitigation |
|---|---|
| Forecast cycle > 60 s at 169 stations — A4 perf blocker | Measure in A3 (5 stations) first to establish per-station baseline. If A4 blocks, spawn a dedicated perf plan before continuing to Stream C. The < 60 s target is a hard gate. |
| Apple Silicon image availability gap (one image amd64-only) | C0 verifies up front; workaround documented if needed. |
| `testcontainers DockerCompose` adds flakiness to the test suite | Deployment tier is opt-in, not on every PR. Nightly CI only. |
| Mac mini office LAN is unreliable → stale NWP, stale LINDAS, intermittent API | D7 decides go / no-go based on observed reliability. If bad, switch Mac mini to Ethernet. |
| Dress rehearsal surfaces an application bug that blocks progress | Such a bug gets its own plan. Plan 046 pauses on Stream A until the blocker resolves. |
| Secrets management on macOS: uniformity with Linux | File-based secrets with `chmod 600` on both platforms. `slack_webhook_url` is a host-process secret (watchdog reads directly); `db_password` is a Docker secret (mounted via compose). No Keychain integration — avoids platform-specific divergence. |
| 2033, 2085, or lake substitute unavailable on LINDAS or missing CAMELS-CH attributes | A1 includes explicit `verify_gauge_reachable()` checks before committing. Substitution requires hydrologist sign-off. |
| macOS auto-login disables screen-lock on the `sapphire` account | Physical access gated by locked office; `sapphire` is a dedicated service account. Auto-login requires FileVault key storage — accepted given physical access controls. Unacceptable for production. |
| Docker Desktop commercial license applicability | hydrosolutions is under the 250-employee / $10M threshold → Docker Desktop Personal tier. Re-evaluate if company grows. |
| NTP/time drift on macOS causes stale LINDAS/STAC queries | C4 runbook adds `sntp -sS time.apple.com` verification; System Settings → Date & Time → Set automatically. |
| Mac mini used for other workloads | Dedicated to SAPPHIRE staging; no other tenants permitted. |
| USB backup disk unmounted after power cycle | Sentinel file at `/data/backups/.sapphire-backup-volume` (container path; host mount `/Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume`) pre-flight catches this and raises `BackupRefusedError`. C3 watchdog staleness check (26 h) provides secondary alerting. |
| Docker Desktop auto-update disrupts unattended run | C4 runbook: disable auto-update in Docker Desktop settings. |
| macOS auto-update disrupts Docker / compose | C4 runbook: disable automatic macOS updates; apply manually before maintenance windows. |

## Open questions

Not blocking DRAFT → READY, but worth flagging before READY:

1. **Lake station for A1**: which CAMELS-CH lake station? Hydrologist must confirm (or confirm the fallback: longest continuous LINDAS record). IT specialist cannot substitute alone.
2. **Target cycle-completion SLA for the 7-day D4 run?** Default proposed: ≥ 95 %. User to confirm or adjust.
3. **Slack workspace**: incoming webhook must be provisioned before C3 can exit. Who provisions it?
4. ~~**`staging-5-stations` branch management**: confirm local-only, never pushed, never merged — only the permanent 2033/2085 addition lands on main.~~ **RESOLVED (Rev 10):** the branch is deprecated per Plan 065. The operator may delete it with `git branch -D staging-5-stations` after A3 completes; no further branch management is required.

Blockers for Stream C execution (not for DRAFT → READY):
- External USB SSD for C2 must be procured and mounted before C2/D2 can run.
- Slack workspace must have an incoming webhook provisioned before C3 can exit.

Resolved (decisions baked into the plan):
- Station set baseline: four rivers (2091, 2004, 2009, 2033 or 2085 as appropriate) + one lake (hydrologist picks).
- Webhook destination: Slack.
- SSO / public URL: deferred to Plan 049.
- DNS provider: Hostpoint for `hydrosolutions.ch` — not touched in Plan 046.
- Access model for Plan 046: LAN-only via SSH tunnel.
