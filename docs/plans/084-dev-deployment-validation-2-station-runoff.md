# Plan 084 — Dev-machine deployment validation: 2-station runoff-only end-to-end

**Status**: READY
**Phase**: 10c (staging infrastructure / deployment validation)
**Parent**: Plan 046 (Mac Mini Staging Deployment) — builds directly on the A3
dress-rehearsal procedure
**Related**: Plan 077 (forecast-cycle optional NWP / runoff-only mode, DONE),
Plan 060 (dev CAMELS-CH bind-mount + `cache_policy=NO_CACHE`), Plan 062
(`PREFECT_HOME` persistence, open gap), Plan 065 (config overlays)
**Created**: 2026-06-26

---

## Problem

The most recent Mac mini bring-up attempt failed to produce operational
stations or forecasts. The failure was not a single bug but a cluster of
operational footguns (worker recreated mid-onboarding, a scheduled flow firing
the full ~160-station set and starving the targeted run, pagination masking
status, lake stations correctly staying in `onboarding`, suspected stuck
operational-marking on re-run, non-persistent `PREFECT_HOME`).

Before re-attempting on the Mac mini, we prove the **full local deployment
pipeline** works end-to-end on **this macOS developer host** for a deliberately
tiny, fully-controlled scope: **2 BAFU river (discharge) stations, dev overlay,
runoff-only first**. The A3 dress rehearsal (Plan 046) already validated 5
stations on this machine; this plan distils that into a clean, repeatable,
failure-mode-hardened 2-station runbook with an explicit verification gate at
every step. It is a **validation plan**: it must not change production behaviour.
If a real bug surfaces (notably failure mode 3 below), it is captured as a crisp
repro finding to seed a WF2 fix milestone — not fixed inline.

### Scope

- **In**: 2 discharge/river stations (`2009`, `2091`), dev overlay
  (`docker-compose.yml` + `docker-compose.dev.yml`), runoff-only forecast first,
  optional NWP-enabled pass last, repeatable clean-start procedure, hardened
  runbook.
- **Out**: the full ~160-station onboarding (explicitly forbidden — see C2), the
  Mac mini host, Cloudflare/public access, code changes (validation only),
  scale/performance targets (Plan 046 A4 owns those).

---

## Decisions / ground truth (verified against the codebase, 2026-06-26)

| # | Decision | Evidence |
|---|---|---|
| D1 | **Stations `2009` + `2091` are the target pair.** Both are discharge/river stations with CAMELS-CH attributes and LINDAS coverage. | Both present in `config.toml` `[onboarding].basin_ids` (lines 141-146). Plan 046 §A1: the four rivers were `{2091, 2009, 2033, 2085}`, the lake was `2004`; "all five are LINDAS-verified and already carry CAMELS-CH attributes"; "2004/2009/2091 were already in the 167-station list". Task 1a re-confirms live before onboarding. |
| D2 | **Runoff-only first; no NWP adapter required.** The forecast model set (`linear_regression_daily`, `persistence_fallback`, `climatology_fallback`) declares no future-dynamic NWP features, so a forecast can be produced without contacting MeteoSwiss. | Plan 077 problem statement + `operational_inputs.py:197-205`; `run_forecast_cycle.py` `runoff_only_mode` branch (`:514`, `:643`). |
| D3 | **Runoff-only is selected by the Plan 077 overlay** `config/overlays/mac-mini.toml` (`[adapters.weather_forecast].enabled = false`), wired via `SAPPHIRE_CONFIG_OVERLAY`. Base `config.toml` ships `enabled = true` (NWP-on), so without the overlay a parameter-less `forecast-cycle` self-wires `MeteoSwissNwpAdapter` and contacts MeteoSwiss live. The overlay file is **not baked into the image** — like base `config.toml` it must be **bind-mounted** into the read-only worker; if `SAPPHIRE_CONFIG_OVERLAY` points at a path that is not mounted, `config/_overlay.py:36-38` raises `FileNotFoundError` and the forecast-cycle crashes before any NWP decision (it does **not** silently fall back to NWP). | Plan 077 D1, T6; `run_forecast_cycle.py:93-100` (`_resolve_overlay_paths` → `load_merged_toml`); `config/_overlay.py:12,27-33,36-38`; base config mount `docker-compose.yml:108` (`./config.toml:/app/config.toml:ro`); overlay mount `docker-compose.macmini.yml:28`; Dockerfile `:62-65` copies only `.venv/src/alembic` (no `config/`). |
| D4 | **`forecast-cycle` is a parameter-less deployment** in both modes (Plan 077). Direct Python invocation (the Plan 046 §A3-step-8 template) is only needed when explicitly injecting a `MeteoSwissNwpAdapter` for debugging; runoff-only and self-wired NWP both run via the normal Prefect deployment trigger. | `register_deployments.py:46-52` (no params); Plan 077 D3 + non-goals. |
| D5 | **Step 8 mark-operational is independent of Step 7 training.** Step 8 (`services/onboarding.py:608-654`) iterates `resolved_station_ids` and marks a non-weather station operational iff `fetch_artifacts_by_status(... ACTIVE, station_id)` returns ≥1 ACTIVE artifact for any `discovered` model — regardless of whether training ran or was skipped this run. This is the property failure mode 3 stress-tests. | `services/onboarding.py:608-654`. |
| D6 | **Only `ingest-observations` (`*/30`), `forecast-cycle` (`0 */6`), `backup-database` (`0 2`) carry crons.** In the current code `onboard-stations` is registered with `cron=None` — it cannot auto-fire on a schedule. The **real** full-set risk is therefore not a cron but the `basin_ids=None` config fallback: a parameter-less `onboard-stations` run reads `config.toml [onboarding].basin_ids` (the full ~167 list) and logs `basin_ids_from_config`. Defence is preventive (Task 2a): always pass `-p 'basin_ids=[...]'`, guard that no other `onboard-stations` run is RUNNING/SCHEDULED before triggering, and CANCEL on sight of `onboarding_starting` with `basin_ids=null`. We still pause the three cron'd deployments to keep the worker idle. | `register_deployments.py:33-91` (cron defaults `:29`; crons `:35-37,44,50,57`); `flows/onboard.py:144-152` (config fallback + `basin_ids_from_config`). |
| D7 | **Sapphire exit-gate events are emitted at every level regardless of `PREFECT_LOGGING_LEVEL`.** `configure_prefect_logging()` is *defined* (`logging.py:82`) but **never called** — the worker runs a bare `prefect worker start` (`docker-compose.yml:70`), so sapphire's structlog stays unconfigured and emits all levels through the default `PrintLogger`. `PREFECT_LOGGING_LEVEL=WARNING` (`docker-compose.yml:76`) only governs Prefect's own stdlib loggers, not sapphire structlog. Therefore `onboarding.training_skipped` (DEBUG, `services/onboarding.py:520`) is observable on stdout alongside every INFO/WARNING gate event. Phase 4 asserts on it **directly** — no verbosity change needed. | `services/onboarding.py:520`; `logging.py:82` (no callsite); `docker-compose.yml:70,76`. |

---

## Environment (copy-paste preamble)

All commands run from the repo root on this host. Set once per shell:

**Preconditions** (one-time on a fresh checkout / reused host, per README
§"Quick start"): the `secrets/` symlink and `secrets/db_password` exist
(README §1 — `up` mounts `./secrets/db_password`, `docker-compose.yml:247`), and
`.env` defines `CAMELS_CH_HOST_DIR` (README §2; auto-loaded by docker compose).
Both already exist on this dev host; verify before `up` if reusing on the Mac
mini. Run `uv sync` first so `uv run` below resolves — an empty `VERSION` aborts
every `$DC` call via `${VERSION:?...}` (`docker-compose.yml:69`).

```bash
cd /Users/bea/Documents/GitHub/SAPPHIRE_flow

uv sync   # ensure the uv env is synced (fresh checkout) so the next line resolves

# Image tag — docker-compose.yml references sapphire-flow:${VERSION} with no default.
export VERSION="$(uv run bump-my-version show current_version)"

# CAMELS-CH host dir (already set in .env; the host path must contain a CAMELS_CH/ subdir).
#   .env -> CAMELS_CH_HOST_DIR=/Users/bea/Library/Application Support/sapphire-flow/raw
# docker compose auto-loads .env from the repo root.

# Convenience: the two-overlay dev compose invocation used throughout.
DC="docker compose -f docker-compose.yml -f docker-compose.dev.yml"

# Dev host ports (docker-compose.dev.yml): API 8010->8000, Prefect 4200, Postgres 5438.
API=http://localhost:8010/api/v1
```

Targets: `BASINS='["2009","2091"]'`.

> **Shell note**: `VERSION`, the `DC` alias, and `API` are shell-local. If you
> open a new terminal mid-run (e.g. one shell tailing logs, one issuing
> commands), **re-run this entire preamble** in that shell — `docker-compose.yml`
> hard-requires `${VERSION}` (no default), so a `$DC` call with an unset
> `VERSION` aborts.

---

## Phase 1 — Clean start

Goal: bring the dev stack up from a known-clean DB so no stale `onboarding`-state
stations or partial artifacts from prior runs pollute the test, then silence the
scheduled deployments so only intended manual runs execute.

### Task 1a — Confirm `2009` + `2091` are discharge stations with CAMELS-CH coverage

- **Scope**: Re-confirm, before any onboarding, that both gauges are river
  (discharge) stations, are reachable on LINDAS, and carry CAMELS-CH attributes.
  Out: substituting stations (if either fails, STOP and escalate to the
  hydrologist per Plan 046 §A1).
- **How**: confirm membership in the onboarding list and CAMELS-CH staging:
  ```bash
  grep -nE '"2009"|"2091"' config.toml          # both must appear in [onboarding].basin_ids
  ls "${CAMELS_CH_HOST_DIR}/CAMELS_CH" | head    # CAMELS-CH dataset present on host
  ```
  Optional live LINDAS re-probe (Plan 046 §A1 method) via
  `HydroScraperAdapter.verify_gauge_reachable("2009", StationKind.RIVER)` and the
  same for `2091`.
- **Exit gate**: both IDs present in `basin_ids`; `CAMELS_CH/` directory exists
  and is non-empty. Prior-art evidence (Plan 046 §A1) already classifies both as
  rivers with CAMELS-CH attrs — this task is a fast re-confirmation, not new
  research. If the CAMELS-CH dir is absent, stage it before proceeding.

### Task 1b — Clean DB, build, up, init green, 9 deployments

- **Scope**: Bring the dev stack up from an empty database on the current code.
  Out: any station/forecast work.
- **Steps**:
  ```bash
  $DC down -v                 # WARNING: wipes pgdata, prefect_data, artifacts, nwp_grids, backups
  $DC build                   # code changed since the last cached image
  $DC up -d
  ```
- **Exit gates** (each is a hard check):
  1. `init` exited 0: `$DC logs init | tail -n 5` shows `Init complete` and
     `docker inspect -f '{{.State.ExitCode}}' "$($DC ps -aq init)"` is `0`.
     (Use `ps -aq`, not `ps -q`: `init` has `restart: "no"`
     (`docker-compose.yml:226`) and has already exited by gate time, so plain
     `ps -q` omits it and `docker inspect ""` would error.)
  2. Long-running services healthy within 5 min (first boot):
     `$DC ps` shows `postgres`, `prefect-server`, `prefect-worker`, `api`
     healthy/running. (`caddy` is **not** on the validation path — it binds host
     `:80`/`:443` with a 30s start-period and nothing here goes through it
     (validation hits `localhost:8010` directly); treat its state as
     non-blocking.)
  3. Health endpoint:
     `curl -s "$API/health" | jq -e '.status=="ok" and .prefect_status=="ok"'`
     returns `true`.
  4. **9 deployments registered**: at the Prefect UI `http://localhost:4200`, or
     `$DC exec -T prefect-worker prefect deployment ls` lists all 9
     (`forecast-cycle`, `ingest-observations`, `backup-database`, `train-models`,
     `run-hindcast`, `compute-skills`, `compute-combined-skills`,
     `onboard-stations`, `onboard-model`).
  5. CAMELS-CH visible to the worker:
     `$DC exec -T prefect-worker ls /data/raw/CAMELS_CH | head` is non-empty
     (confirms the `CAMELS_CH_HOST_DIR` read-only bind-mount resolved).
  6. **DB is clean**: `curl -s "$API/stations?limit=200" | jq '.total'` returns
     `0`.

### Task 1c — Pause the scheduled deployments for the test duration

- **Scope**: Prevent any cron'd deployment from firing and thrashing the single
  process worker while the targeted runs execute (failure mode 2). Out: deleting
  schedules permanently.
- **Steps** (Prefect 3.6.23 CLI). The `pause` subcommand **cannot** take a bare
  `<flow>/<deployment>`: `_set_schedule_activation` (`prefect/cli/deployment.py:885-888`)
  requires *either* `--all` *or* both a deployment name **and** an explicit
  `schedule_id`; a deployment-name-only call exits non-zero and pauses nothing.
  Pause all schedules in one shot via the worker container (`-T` skips the TTY
  confirm prompt — `is_interactive()` is false without a TTY):
  ```bash
  $DC exec -T prefect-worker prefect deployment schedule pause --all
  ```
  `--all` only touches deployments that actually have a schedule, i.e. the three
  cron'd ones (`forecast-cycle`, `ingest-observations`, `backup-database`);
  `onboard-stations` has no schedule, so `--all` is harmless and safer than
  hand-listing schedule-ids. Then verify per deployment with the bare
  `<flow>/<deployment>` name (which `ls` **does** accept —
  `prefect/cli/deployment.py:1043-1060`; flow name == deployment name for all v0
  deployments):
  ```bash
  for d in forecast-cycle ingest-observations backup-database; do
    $DC exec -T prefect-worker prefect deployment schedule ls "$d/$d"
  done
  ```
  Note (D6): `onboard-stations` has **no** schedule in the current code, so there
  is nothing to pause for it; it can only run when manually triggered. The
  parameter-less full-set risk for `onboard-stations` is handled preventively in
  Task 2a, not by pausing.
- **Teardown (resume afterward)**: when validation is done, re-enable the
  schedules so the host returns to normal operation:
  ```bash
  $DC exec -T prefect-worker prefect deployment schedule resume --all
  ```
- **Exit gate**: `prefect deployment schedule ls "$d/$d"` shows each of the three
  schedules `active: False` (paused). For a belt-and-braces check, observe the
  Prefect UI "Upcoming runs" panel for ~2 min and confirm **no** auto-scheduled
  runs appear and **no** repeated `onboarding_starting` events surface in
  `$DC logs prefect-worker`.

---

## Phase 2 — Onboard 2 river stations

### Task 2a — Onboard `2009` + `2091`, undisturbed

- **Scope**: Run `onboard-stations` scoped to exactly the two river IDs and let
  it run to completion without touching the worker. Out: scale, lakes, NWP.
- **Ordering constraint (failure mode 1)**: do **not** restart, recreate, or
  `--force-recreate` the worker (or any service) while this run is in flight.
  Any NWP-enable (Phase 5) happens strictly after onboarding completes.
- **Pre-trigger guard (failure mode 2 — preventive, D6)**: the full-set danger is
  a parameter-less `onboard-stations` run falling back to `config.toml`'s full
  `[onboarding].basin_ids`. Before triggering, assert no `onboard-stations` run is
  already RUNNING or SCHEDULED, and cancel any that is:
  ```bash
  $DC exec -T prefect-worker prefect flow-run ls --flow-name onboard-stations \
    --state RUNNING --state SCHEDULED --state PENDING
  # If any appear, cancel each before proceeding:
  #   $DC exec -T prefect-worker prefect flow-run cancel <FLOW_RUN_ID>
  ```
- **Trigger** (the `-p 'basin_ids=[...]'` argument is **mandatory** — never trigger
  parameter-less; JSON-string quoting for the list param, per Plan 046 §A3 F2):
  ```bash
  $DC exec -T prefect-worker prefect deployment run onboard-stations/onboard-stations \
    -p 'basin_ids=["2009","2091"]'
  ```
  Then watch to completion:
  ```bash
  $DC logs -f prefect-worker | grep -E 'onboarding_starting|onboarding_flow_complete|station_operational|station_no_active_artifact|training_error|model.onboarding_unit_completed|hindcast.skip.no_observations|hindcast.step_failed'
  ```
- **Exit gates** (grep the canonical single-line events):
  1. Exactly one `onboarding_starting` for this run carries `basin_ids` of the
     two IDs. **If `onboarding_starting` shows `basin_ids=null` (or the full
     ~167-list), CANCEL the flow run immediately** — that run fell back to
     `config.toml` and is onboarding the full set:
     ```bash
     $DC exec -T prefect-worker prefect flow-run cancel <FLOW_RUN_ID>
     ```
     then stop and re-trigger with the explicit `-p 'basin_ids=[...]'` argument.
  2. The run reaches `onboarding_flow_complete` (proves an uninterrupted run got
     to Step 8 — defends failure mode 1) carrying
     `stations_marked_operational=2`. Record `observations_imported`,
     `observations_qc_passed`, `observations_qc_failed`,
     `observations_qc_suspect`.
  3. Exactly **two** `onboarding.station_operational` events (one per station);
     **zero** `onboarding.station_no_active_artifact`.
  4. The Prefect flow run reaches `COMPLETED`
     (`prefect flow-run ls` / Prefect UI).
  5. If any `hindcast.skip.no_observations` / `hindcast.step_failed` /
     `onboarding.training_error` appears, capture it but do not auto-fail unless
     gate 2/3 also fail — initial training already produces ACTIVE artifacts that
     satisfy Step 8.

---

## Phase 3 — Verify operational + forecast (runoff-only)

### Task 3a — API confirms 2 operational stations

- **Scope**: Verify the onboarding outcome through the API, using the
  pagination-safe query (failure mode 4). Out: forecasts.
- **Check**:
  ```bash
  curl -s "$API/stations?status=operational&limit=200" | jq '.total'        # expect 2
  curl -s "$API/stations?status=operational&limit=200" | jq -r '.items[].code'
  ```
  Sanity histogram (always `limit=200`, never the default 50):
  ```bash
  curl -s "$API/stations?limit=200" | jq -r '.items[].station_status' | sort | uniq -c
  ```
- **Exit gate**: `.total == 2` for `status=operational`; the two codes correspond
  to `2009`/`2091`.

### Task 3b — Trigger one runoff-only forecast-cycle; verify forecasts for both

- **Scope**: Produce a forecast for both operational stations without NWP. Out:
  NWP/gridded path (Phase 5), skill/combination.
- **Select runoff-only mode**: the worker must run with the Plan 077 runoff-only
  overlay so the parameter-less `forecast-cycle` skips NWP. This requires **both**
  the env var **and** a bind-mount of the overlay file — the overlay is *not* baked
  into the image (D3), and the worker is `read_only`, so an env var alone makes
  `config/_overlay.py` raise `FileNotFoundError` and crashes the cycle before it
  ever evaluates NWP. Use a small **transient** dev override saved by the operator
  (do not commit) — `docker-compose.runoff.yml`, mirroring
  `docker-compose.macmini.yml:24-28`:
  ```yaml
  services:
    prefect-worker:
      environment:
        SAPPHIRE_CONFIG_OVERLAY: /app/config/overlays/mac-mini.toml
      volumes:
        - ./config/overlays/mac-mini.toml:/app/config/overlays/mac-mini.toml:ro
  ```
  > Why a bespoke override and not `-f docker-compose.macmini.yml` directly:
  > `docker-compose.macmini.yml` additionally binds host paths that exist only on
  > the mini (`/Volumes/sapphire-backup/pg_dumps`, `/Users/sapphire/camels-ch`).
  > Layering it on this dev host would append those mounts and fail. The transient
  > override copies only the two lines we need (env var + overlay mount).

  Apply it and recreate **only the worker** — this is safe now because Phase 2
  onboarding is complete and nothing is running:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.runoff.yml up -d prefect-worker
  ```
  (Alternative without an override file: leave base config NWP-on and accept that
  Phase 3b becomes a self-wired live-NWP run — but the objective is runoff-only
  first, so prefer the overlay.)
- **Trigger** (parameter-less; D4):
  ```bash
  $DC exec -T prefect-worker prefect deployment run forecast-cycle/forecast-cycle
  $DC logs -f prefect-worker | grep -E 'forecast_cycle.nwp_disabled|forecast_cycle.starting|forecast_cycle.no_operational_stations|forecast.run_completed'
  ```
- **Exit gates**:
  1. `forecast_cycle.nwp_disabled` with `mode="runoff_only"` is logged (proves
     runoff-only path), and `forecast_cycle.starting` reports `stations=2`.
  2. **No** `forecast_cycle.no_operational_stations` event.
  3. The flow run reaches `COMPLETED`.
  4. Forecasts visible via the API for **both** stations. **Pass an explicit wide
     window** — `GET /stations/{id}/forecasts` defaults to `[now-7d, now]`
     (`api_stations.py:248-253`), so a forecast whose `cycle_time` rounds to a
     future slot would fall after `end=now` and read `.total==0`:
     ```bash
     WIN='start=2000-01-01T00:00:00Z&end=2100-01-01T00:00:00Z'
     for sid in $(curl -s "$API/stations?status=operational&limit=200" | jq -r '.items[].id'); do
       echo "$sid -> $(curl -s "$API/stations/$sid/forecasts?limit=1&$WIN" | jq '.total')"
     done
     ```
     Each station returns `.total >= 1`.

---

## Phase 4 — Idempotency / re-run (failure mode 3 stress test)

### Task 4a — Re-run onboarding for the same 2; assert still operational

- **Scope**: With ACTIVE artifacts already present from Phase 2, re-run
  `onboard-stations` for the same two IDs and prove Step 8 still keeps them
  operational even though training is skipped (D5). Out: fixing any bug found.
- **Steps**: keep the worker as-is (revert the runoff override is optional — it
  does not affect onboarding). Re-trigger exactly as Task 2a:
  ```bash
  $DC exec -T prefect-worker prefect deployment run onboard-stations/onboard-stations \
    -p 'basin_ids=["2009","2091"]'
  ```
- **Exit gates**:
  1. Run reaches `onboarding_flow_complete`.
  2. **Stations remain operational**:
     `curl -s "$API/stations?status=operational&limit=200" | jq '.total'` still
     returns `2`.
  3. **Idempotency — authoritative signal**: no new ACTIVE artifacts were created
     (artifact count unchanged from Phase 2) and the stations stay operational
     (gate 2). The artifact-count delta is the provable idempotency check (it
     reflects DB state, not logging config).
  4. (D7) **Corroborating only**: `onboarding.training_skipped`
     `reason=all_stations_have_active_artifact` *should* appear. It logs at DEBUG;
     the D7 claim is that sapphire structlog is unconfigured on the worker so it
     prints regardless of `PREFECT_LOGGING_LEVEL`:
     ```bash
     $DC logs prefect-worker | grep 'onboarding.training_skipped'
     ```
     This is the one gate that depends on logging-config behaviour rather than a
     provable code path. If the line is **missing** while gates 2-3 pass, do
     **not** call it a D5 bug — treat it as "verify the D7 logging assumption"
     (e.g. confirm via the stations API that the two stations are still
     operational and the artifact count is unchanged, which are the authoritative
     signals). Only a gate-2/gate-3 failure constitutes the Phase 4 bug below.
- **If gate 2 FAILS** (stations drop out of / never reach operational on the
  re-run): this is the suspected Mac mini bug. Do **not** fix inline. Produce a
  crisp **BUG FINDING** with:
  - Exact repro (clean DB → onboard 2 → re-onboard 2).
  - Expected: 2 operational after re-run (Step 8 independent of training, D5).
  - Actual: observed operational count + the relevant events.
  - Diagnosis pointers to capture: was Step 8 reached? Is `discovered`
    non-empty (Step 6/7 populated it)? Does
    `fetch_artifacts_by_status(model_id, ACTIVE, station_id)` return rows for
    each station? Were `onboarding.station_operational` /
    `onboarding.station_no_active_artifact` emitted?
  - File the finding so it can seed a WF2 (vision-build) `issue` milestone with a
    locked regression test. Reference `services/onboarding.py:608-654`.

---

## Phase 5 — (Optional) NWP-enabled pass

Run only after Phases 1-4 are green. This is the first point at which recreating
the worker is safe, because nothing is running.

### Task 5a — Enable NWP, recreate worker, writability check, forecast-cycle

- **Scope**: Switch to NWP-on, confirm scratch/archive writability, and produce
  one self-wired or direct-invoke NWP forecast. Out: scale, skill.
- **Steps**:
  1. Remove the runoff override (drop `-f docker-compose.runoff.yml`) so the base
     `config.toml` NWP-on default applies, and recreate the worker (safe now):
     ```bash
     $DC up -d --force-recreate prefect-worker
     ```
  2. Writability check (Plan 077 §T7) — confirms the NWP scratch/archive paths
     the gridded path writes to:
     ```bash
     $DC exec -u app -T prefect-worker sh -c \
       'touch /data/nwp_grids/.w /tmp/sapphire_nwp/.w && echo ok && rm /data/nwp_grids/.w /tmp/sapphire_nwp/.w'
     ```
     Expect `ok`.
  2b. `PREFECT_HOME` observation (failure mode 6 / Plan 062 — record only, do not
     block). `PREFECT_HOME` is unset in every compose file; Plan 062's concern is
     server-side state persistence across `down`/restart, which this from-`down -v`
     procedure never exercises (the server is not restarted mid-run). Just record
     the observed dev behaviour:
     ```bash
     $DC exec -T prefect-server printenv PREFECT_HOME || echo "PREFECT_HOME unset (expected)"
     ```
     Note: because `PREFECT_HOME` is unset, a `$DC down` loses Prefect run history;
     this is recorded behaviour, not a gate failure for this plan.
  3. Trigger NWP forecast-cycle. Preferred: parameter-less (Plan 077 self-wires
     `MeteoSwissNwpAdapter` from `enabled=true`):
     ```bash
     $DC exec -T prefect-worker prefect deployment run forecast-cycle/forecast-cycle
     ```
     Fallback / explicit injection (Plan 046 §A3-step-8 direct-invoke template,
     constructs `DATABASE_URL` inline from `/run/secrets/db_password` and injects
     a live `MeteoSwissNwpAdapter` with `max_fallback_steps` derived from
     `ceil(cfg.nwp_max_fallback_age_hours / 6.0)`).
- **Exit gates**:
  1. Writability check prints `ok`.
  2. Flow run reaches `COMPLETED`; **no** `forecast_cycle.no_operational_stations`;
     NWP fetch did not abort
     (`forecast_cycle.nwp_fetch_failed_aborting` absent).
  3. Forecasts present via the API for both stations (same check as Task 3b
     gate 4).

---

## Phase 6 — Hardened dev validation runbook

### Task 6a — Capture the validated procedure + gotchas

- **Scope**: Fold the validated commands and the six gotchas into the
  "Dev validation runbook" section below so the Mac mini attempt reuses it. Out:
  any code/config change.
- **Exit gate**: the runbook section is complete, copy-paste runnable, and every
  step references its verification (canonical event grep or API check).

### Dev validation runbook (the deliverable)

Ordered, copy-paste procedure (uses the Environment preamble above):

1. **Confirm targets** — `grep -nE '"2009"|"2091"' config.toml`; `ls "$CAMELS_CH_HOST_DIR/CAMELS_CH"`.
2. **Clean start** — `$DC down -v && $DC build && $DC up -d`; gate on `init`
   exit 0, health `ok/ok`, 9 deployments, `/stations?limit=200 .total==0`,
   `ls /data/raw/CAMELS_CH` non-empty.
3. **Pause schedules** — `prefect deployment schedule pause --all` (a bare
   `pause <flow>/<deployment>` does nothing on 3.6.23); verify each of
   `forecast-cycle`, `ingest-observations`, `backup-database` shows
   `active: False` via `schedule ls "$d/$d"` and no upcoming runs. Resume with
   `... schedule resume --all` when done.
4. **Onboard 2** — guard no `onboard-stations` run is RUNNING/SCHEDULED, then
   `prefect deployment run onboard-stations/onboard-stations -p 'basin_ids=["2009","2091"]'`
   (the `-p` arg is mandatory — parameter-less falls back to the full config list);
   gate on `onboarding_starting` carrying the 2 IDs (CANCEL if `basin_ids=null`),
   `onboarding_flow_complete` `stations_marked_operational=2` + 2×
   `onboarding.station_operational`; never touch the worker mid-run.
5. **Verify operational** — `/stations?status=operational&limit=200 .total==2`.
6. **Runoff forecast** — recreate the worker with `docker-compose.runoff.yml`
   (env var **and** overlay bind-mount — an env var alone crashes config load);
   `prefect deployment run forecast-cycle/forecast-cycle`; gate on
   `forecast_cycle.nwp_disabled mode=runoff_only`, no
   `no_operational_stations`, forecasts `.total>=1` per station (use an explicit
   wide `start/end` window on the forecasts query).
7. **Idempotency** — re-run step 4; gate on `.total==2` operational still holds.
8. **(Optional) NWP** — drop the overlay, `up -d --force-recreate prefect-worker`,
   writability `ok`, re-trigger forecast-cycle.

**Gotchas (defend against each):**

1. **Never restart/recreate the worker mid-onboarding** — it tears down the flow
   before Step 8 → 0 operational. Sequence any worker recreate (Phase 5) strictly
   after `onboarding_flow_complete`.
2. **Never trigger `onboard-stations` parameter-less; pause the cron'd
   deployments.** The real full-set mechanism is the `basin_ids=None` →
   `config.toml` full-list fallback (D6), not a cron — `onboard-stations` has no
   schedule. Defence is preventive: guard that no `onboard-stations` run is
   RUNNING/SCHEDULED before triggering, always pass `-p 'basin_ids=[...]'`, and
   CANCEL on sight of `onboarding_starting` with `basin_ids=null`. Separately,
   pause the three cron'd deployments (`forecast-cycle`, `ingest-observations`,
   `backup-database`) to keep the single-process worker idle.
3. **Re-run must keep stations operational** — Step 8 is independent of training
   (D5). If a re-run drops them, that is a BUG to capture (Phase 4), not expected.
4. **Always query with `?status=operational&limit=200`** — the default
   `limit=50` masks status in histograms (failure mode 4).
5. **Pick discharge/river stations** — lake stations (water_level only) correctly
   stay in `onboarding` (no discharge model). `2009`/`2091` are rivers; `2004`
   (Murten) is a lake — do not use it here.
6. **`PREFECT_HOME` is unset everywhere — record, do not block** (Plan 062). This
   from-`down -v` procedure never restarts the server mid-run, so it does not
   exercise Plan 062's resumability concern. Just observe
   `$DC exec prefect-server printenv PREFECT_HOME` (expect unset) and note that a
   `down` loses run history. Do not block this plan on Plan 062.
7. **Any worker/service recreate re-runs `init`, which re-activates the paused
   schedules** (discovered during the 2026-06-28 run). `prefect-worker`
   `depends_on` `init`, so `up -d [--force-recreate] prefect-worker` brings up
   `init` again → `register_deployments` re-creates the deployments with their
   crons **active**, silently undoing Task 1c. After *every* worker recreate
   (Phase 3b, Phase 5), immediately re-run
   `prefect deployment schedule pause --all` and re-verify `active: False`.

---

## Validation run — 2026-06-28 (results)

Executed on this dev host (macOS, Docker Desktop VM 15.84 GiB), image
`sapphire-flow:0.1.499`. Stations `2009` (`056108c7…`) + `2091` (`828385f6…`).

| Phase | Outcome | Evidence |
|---|---|---|
| 1a targets | ✅ PASS | both IDs in `[onboarding].basin_ids`; CAMELS-CH staged + visible to worker (`/data/raw/CAMELS_CH`). |
| 1b clean start | ✅ PASS | `init` exit 0 (`Init complete`, `count=9`); postgres/prefect-server/api healthy; health `ok/ok`; 9 deployments; DB `total=0`. |
| 1c pause schedules | ✅ PASS | `pause --all` → 3 cron'd deployments `active: False`. The round-2 blocker fix (`pause --all`, not bare `pause <flow>/<dep>`) validated live. |
| 2 onboard | ✅ PASS | `onboarding_starting basin_ids=['2009','2091']` (not null); `onboarding_flow_complete stations_marked_operational=2 models_trained=6 observations_imported=58440 qc_passed=28955 qc_failed=0 qc_suspect=265`; 2× `station_operational`; 0× `station_no_active_artifact`; flow `COMPLETED`. Onboarding took ~20 min (day-by-day historical hindcast — Plan 068 territory). Failure modes 1 & 2 defended (uninterrupted run reached Step 8; scoped run, not the full set). |
| 3a operational | ✅ PASS | API `status=operational&limit=200 .total=2`, codes `2009`/`2091`; histogram clean. |
| 3b runoff forecast | ✅ PASS | `forecast_cycle.nwp_disabled mode=runoff_only`, `starting stations=2`, `forecasts_stored=2 stations_succeeded=2`, no `no_operational_stations`; flow `COMPLETED`; forecasts `.total=1` for both stations. Required re-pausing schedules after the worker recreate (gotcha 7). |
| 4 idempotency | ✅ PASS — **failure mode 3 did NOT reproduce** | re-run `onboarding_flow_complete stations_marked_operational=2 models_trained=0`; stations still operational (`.total=2`); **ACTIVE artifact count unchanged 6→6** (authoritative); `training_skipped reason=all_stations_have_active_artifact` for all 3 models — observed at DEBUG on worker stdout, **empirically confirming D7**. Step 8 is independent of training, as designed (D5). |
| 5 NWP (optional) | ❌ **FAIL — OOM** | see Finding NWP-OOM below. Core pipeline (1–4) unaffected. |

### Finding: NWP forecast-cycle OOM-killed on dev (NWP-OOM)

- **Repro**: clean dev stack (Phases 1–4 green) → recreate worker NWP-on (base
  `config.toml enabled=true`) → `prefect deployment run forecast-cycle/forecast-cycle`.
- **Expected**: cycle `COMPLETED`, forecasts for both stations.
- **Actual**: flow run **CRASHED** after ~320 s — state message
  *"Flow run process exited with non-zero status code -9"* (SIGKILL). Worker
  container `"OOMKilled": true`. The crash is in the NWP fetch/extract step
  (`nwp.fetch_started` logged; no forecasts stored; no graceful
  `nwp_fetch_failed_aborting`).
- **Diagnosis**: the ICON-CH2-EPS pull for one cycle staged **484 GRIB2 files /
  2.7 GB** under `/tmp/sapphire_nwp/<cycle>/` (21 members × ~120 hourly steps ×
  {t_2m, tot_prec} × {ctrl, perturb}); the worker has **no `mem_limit`** and the
  Docker VM is 15.84 GiB. Processing the set exceeded available memory →
  cgroup/host OOM killer took the flow subprocess.
- **Not fixed inline** (validation only). Candidate mitigations for a follow-up
  (WF2 fix-mode milestone or a dedicated plan): stream/extract per member/step
  and release eagerly instead of holding the full set in memory; cap NWP
  concurrency; set a worker `mem_limit` + verify graceful degradation; or
  pre-archive to Zarr out-of-band. **This is the most material finding of the
  run** and gates any NWP-on deployment on the Mac mini (which is unlikely to
  have more RAM than this host).

### Net result

The **runoff-only end-to-end pipeline (Phases 1–4) is validated green** on dev
for 2 river stations, and the suspected failure mode 3 did not reproduce —
strongly implying the Mac mini's 0-operational outcome was operational (worker
recreated mid-onboarding / partial prior state / schedule reactivation), not a
Step 8 code bug. The **NWP-on path is blocked by an OOM** that must be addressed
before an NWP-enabled mini bring-up.

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-clean-start",
      "tasks": ["1a", "1b", "1c"],
      "parallel": false,
      "note": "1a confirms targets; 1b is the clean up; 1c pauses schedules after init registers them."
    },
    {
      "id": "phase-2-onboard",
      "tasks": ["2a"],
      "parallel": false,
      "depends_on": ["phase-1-clean-start"]
    },
    {
      "id": "phase-3-verify-forecast",
      "tasks": ["3a", "3b"],
      "parallel": false,
      "depends_on": ["phase-2-onboard"],
      "note": "3a confirms operational via API; 3b runs runoff-only forecast and verifies forecasts."
    },
    {
      "id": "phase-4-idempotency",
      "tasks": ["4a"],
      "parallel": false,
      "depends_on": ["phase-3-verify-forecast"]
    },
    {
      "id": "phase-5-nwp-optional",
      "tasks": ["5a"],
      "parallel": false,
      "depends_on": ["phase-4-idempotency"],
      "note": "Optional. Only safe to recreate the worker here because nothing is running."
    },
    {
      "id": "phase-6-runbook",
      "tasks": ["6a"],
      "parallel": false,
      "depends_on": ["phase-5-nwp-optional"]
    }
  ]
}
```

---

## Resolved ground truth (was: open questions; all settled against the codebase 2026-06-26)

1. **Failure mode 2 mechanism (D6).** `onboard-stations` is registered with
   `cron=None` (`register_deployments.py:29,80-84`) — it cannot auto-fire. The
   real full-set risk is the `basin_ids=None` → `config.toml [onboarding].basin_ids`
   fallback (`flows/onboard.py:144-152`, logs `basin_ids_from_config`). Mitigation
   is preventive in Task 2a (guard for running/scheduled runs; mandatory `-p`
   argument; cancel on `basin_ids=null`), plus pausing the three real crons.
2. **Runoff-only selection on dev.** The transient `docker-compose.runoff.yml`
   override is the chosen mechanism. The overlay is **not** baked into the image
   (Dockerfile `:62-65` copies only `.venv/src/alembic`); like base `config.toml`
   it must be **bind-mounted**. The override therefore sets **both**
   `SAPPHIRE_CONFIG_OVERLAY` and the `./config/overlays/mac-mini.toml` mount
   (mirroring `docker-compose.macmini.yml:24-28`). Selection wiring is real:
   `_resolve_overlay_paths()` (`config/_overlay.py:12,27-33`) →
   `load_merged_toml` (`run_forecast_cycle.py:93-100`) →
   `nwp_enabled = weather_forecast_config.enabled` →
   `runoff_only_mode = not nwp_enabled` (`run_forecast_cycle.py:476-479,517`);
   base `config.toml` ships `enabled = true`, overlay sets `enabled = false`.
3. **`training_skipped` observability (D7).** Direct gate. `configure_prefect_logging()`
   is never called, so sapphire structlog prints all levels regardless of
   `PREFECT_LOGGING_LEVEL`; `onboarding.training_skipped` (DEBUG) is grep-able on
   the worker stdout. No verbosity change needed (see Phase 4 gate 3).
4. **Prefect pause command.** On Prefect 3.6.23, `pause` requires `--all` or both
   a deployment name and an explicit `schedule_id` — a bare
   `pause <flow>/<deployment>` exits non-zero and pauses nothing
   (`prefect/cli/deployment.py:885-888`). The working command is
   `prefect deployment schedule pause --all` (resume with
   `... schedule resume --all`). Inspection, however, **does** accept the bare
   name: `prefect deployment schedule ls <flow>/<deployment>`
   (`prefect/cli/deployment.py:1043-1060`; flow name == deployment name for all v0
   deployments).
5. **`PREFECT_HOME` (Plan 062, failure mode 6).** Do **not** block on Plan 062.
   `PREFECT_HOME` is unset in every compose file; Plan 062's concern is
   server-side persistence across restart, which this from-`down -v` procedure
   never exercises. Record the observed value (Task 5a step 2b) and proceed.

---

## Affected files

- `docs/plans/084-dev-deployment-validation-2-station-runoff.md` (this plan).
- `docs/plans/README.md` (index entry).
- No production code or committed config changes (validation only). The
  `docker-compose.runoff.yml` override in Phase 3b is a transient, uncommitted
  operator file.
