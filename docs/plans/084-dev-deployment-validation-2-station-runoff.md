# Plan 084 — Dev-machine deployment validation: 2-station runoff-only end-to-end

**Status**: DRAFT
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
| D3 | **Runoff-only is selected by the Plan 077 overlay** `config/overlays/mac-mini.toml` (`[adapters.weather_forecast].enabled = false`), wired via `SAPPHIRE_CONFIG_OVERLAY`. Base `config.toml` ships `enabled = true` (NWP-on), so without the overlay a parameter-less `forecast-cycle` self-wires `MeteoSwissNwpAdapter` and contacts MeteoSwiss live. | Plan 077 D1, T6; `run_forecast_cycle.py` self-wiring branch. |
| D4 | **`forecast-cycle` is a parameter-less deployment** in both modes (Plan 077). Direct Python invocation (the Plan 046 §A3-step-8 template) is only needed when explicitly injecting a `MeteoSwissNwpAdapter` for debugging; runoff-only and self-wired NWP both run via the normal Prefect deployment trigger. | `register_deployments.py:46-52` (no params); Plan 077 D3 + non-goals. |
| D5 | **Step 8 mark-operational is independent of Step 7 training.** Step 8 (`services/onboarding.py:608-654`) iterates `resolved_station_ids` and marks a non-weather station operational iff `fetch_artifacts_by_status(... ACTIVE, station_id)` returns ≥1 ACTIVE artifact for any `discovered` model — regardless of whether training ran or was skipped this run. This is the property failure mode 3 stress-tests. | `services/onboarding.py:608-654`. |
| D6 | **Only `ingest-observations`, `forecast-cycle`, `backup-database` carry crons.** In the current code `onboard-stations` is registered with `cron=None` — it does **not** auto-fire. This contradicts the Mac mini observation ("scheduled `onboard-stations` fires with `basin_ids=None`"). See Open Question 1. We still pause all three cron'd deployments for the test to keep the worker idle. | `register_deployments.py:33-91`. |
| D7 | **`onboarding.training_skipped` is logged at DEBUG** (`services/onboarding.py:520`, `log.debug`), and the worker runs `PREFECT_LOGGING_LEVEL=WARNING`. Asserting on it may require raising sapphire log verbosity; the idempotency gate (Phase 4) therefore relies primarily on the API (stations stay operational) and treats the `training_skipped` grep as best-effort. | `services/onboarding.py:520`; `docker-compose.yml:76`. |

---

## Environment (copy-paste preamble)

All commands run from the repo root on this host. Set once per shell:

```bash
cd /Users/bea/Documents/GitHub/SAPPHIRE_flow

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
     `docker inspect -f '{{.State.ExitCode}}' "$($DC ps -q init)"` is `0`.
  2. Long-running services healthy within 5 min (first boot):
     `$DC ps` shows `postgres`, `prefect-server`, `prefect-worker`, `api`,
     `caddy` healthy/running.
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
- **Steps** (Prefect 3 CLI; confirm the exact subcommand at runtime with
  `$DC exec -T prefect-worker prefect deployment schedule --help` — in Prefect 3
  it is `prefect deployment schedule pause <deployment-name> [schedule_id]`,
  with `prefect deployment schedule ls <deployment-name>` to inspect):
  ```bash
  for d in forecast-cycle ingest-observations backup-database; do
    $DC exec -T prefect-worker prefect deployment schedule ls "$d/$d"
    $DC exec -T prefect-worker prefect deployment schedule pause "$d/$d"
  done
  ```
  Note (D6): `onboard-stations` has **no** schedule in the current code, so there
  is nothing to pause for it; it can only run when manually triggered.
- **Exit gate**: `prefect deployment schedule ls <name>` shows each of the three
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
  Any NWP-enable (Phase 5) happens strictly after onboarding completes. Confirm
  no other flow run is RUNNING before triggering.
- **Trigger** (JSON-string quoting for the list param, per Plan 046 §A3 F2):
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
     two IDs (no `basin_ids=null`, no full-list run).
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
  overlay so the parameter-less `forecast-cycle` skips NWP. Provide
  `SAPPHIRE_CONFIG_OVERLAY=/app/config/overlays/mac-mini.toml` to the
  `prefect-worker` for this validation. Use a small **transient** dev override
  saved by the operator (do not commit) — e.g. `docker-compose.runoff.yml`:
  ```yaml
  services:
    prefect-worker:
      environment:
        SAPPHIRE_CONFIG_OVERLAY: /app/config/overlays/mac-mini.toml
  ```
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
  4. Forecasts visible via the API for **both** stations:
     ```bash
     for sid in $(curl -s "$API/stations?status=operational&limit=200" | jq -r '.items[].id'); do
       echo "$sid -> $(curl -s "$API/stations/$sid/forecasts?limit=1" | jq '.total')"
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
  3. Best-effort (D7): `onboarding.training_skipped`
     `reason=all_stations_have_active_artifact` is emitted (DEBUG — may require
     temporarily raising sapphire log verbosity; absence is not a failure if
     gates 1-2 hold and no new artifacts were created).
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
  2. Writability check (Plan 077 §T7):
     ```bash
     $DC exec -u app -T prefect-worker sh -c \
       'touch /data/nwp_grids/.w /tmp/sapphire_nwp/.w && echo ok && rm /data/nwp_grids/.w /tmp/sapphire_nwp/.w'
     ```
     Expect `ok`. (Also relevant to Plan 062 / failure mode 6 — note whether
     `PREFECT_HOME` / `.prefect` paths are writable and whether interrupted-run
     recovery works on this host.)
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
3. **Pause schedules** — pause `forecast-cycle`, `ingest-observations`,
   `backup-database`; verify each `active: False` and no upcoming runs.
4. **Onboard 2** — `prefect deployment run onboard-stations/onboard-stations -p 'basin_ids=["2009","2091"]'`;
   gate on `onboarding_flow_complete` `stations_marked_operational=2` + 2×
   `onboarding.station_operational`; never touch the worker mid-run.
5. **Verify operational** — `/stations?status=operational&limit=200 .total==2`.
6. **Runoff forecast** — worker with `SAPPHIRE_CONFIG_OVERLAY=.../mac-mini.toml`;
   `prefect deployment run forecast-cycle/forecast-cycle`; gate on
   `forecast_cycle.nwp_disabled mode=runoff_only`, no
   `no_operational_stations`, forecasts `.total>=1` per station.
7. **Idempotency** — re-run step 4; gate on `.total==2` operational still holds.
8. **(Optional) NWP** — drop the overlay, `up -d --force-recreate prefect-worker`,
   writability `ok`, re-trigger forecast-cycle.

**Six gotchas (defend against each):**

1. **Never restart/recreate the worker mid-onboarding** — it tears down the flow
   before Step 8 → 0 operational. Sequence any worker recreate (Phase 5) strictly
   after `onboarding_flow_complete`.
2. **Pause cron'd deployments** before the test (`forecast-cycle`,
   `ingest-observations`, `backup-database`). Repeated `onboarding_starting` =
   an auto-firing schedule starving the targeted run. NB: in current code
   `onboard-stations` has no cron (D6 / Open Question 1).
3. **Re-run must keep stations operational** — Step 8 is independent of training
   (D5). If a re-run drops them, that is a BUG to capture (Phase 4), not expected.
4. **Always query with `?status=operational&limit=200`** — the default
   `limit=50` masks status in histograms (failure mode 4).
5. **Pick discharge/river stations** — lake stations (water_level only) correctly
   stay in `onboarding` (no discharge model). `2009`/`2091` are rivers; `2004`
   (Murten) is a lake — do not use it here.
6. **`PREFECT_HOME` may not be persistent** (Plan 062) — verify `.prefect` is
   writable on this host; non-persistence means warnings + non-resumable runs.
   Note the observed behaviour for the mini.

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

## Open questions (resolve before promoting to READY)

1. **Failure mode 2 vs code (D6).** The current `register_deployments.py`
   registers `onboard-stations` with `cron=None` — it cannot auto-fire on a
   schedule. Yet the Mac mini showed repeated `onboarding_starting` with the full
   ~160-station set. Was the mini running older code, was onboarding triggered
   manually more than once, or did a different flow log that event? Confirm
   before trusting the "pause schedules" mitigation as complete.
2. **Runoff-only selection on dev.** Is the transient `docker-compose.runoff.yml`
   override (Phase 3b) acceptable, or should the dev overlay grow a documented
   `SAPPHIRE_CONFIG_OVERLAY` hook? Confirm the `config/overlays/mac-mini.toml`
   overlay is baked into the `sapphire-flow` image (so `/app/config/overlays/...`
   resolves inside the worker without an extra mount).
3. **`training_skipped` observability (D7).** Should Phase 4 temporarily set a
   DEBUG sapphire log level to make the idempotency assertion direct, or is the
   API-based gate sufficient?
4. **Exact Prefect 3 pause command.** Confirm `prefect deployment schedule pause`
   (vs `prefect deployment pause-schedule`) on the pinned Prefect 3 version in
   the worker image, and that pausing survives the test window.
5. **`PREFECT_HOME` persistence (Plan 062, failure mode 6).** Decide whether to
   block this plan on Plan 062, or just record dev behaviour and proceed.

---

## Affected files

- `docs/plans/084-dev-deployment-validation-2-station-runoff.md` (this plan).
- `docs/plans/README.md` (index entry).
- No production code or committed config changes (validation only). The
  `docker-compose.runoff.yml` override in Phase 3b is a transient, uncommitted
  operator file.
