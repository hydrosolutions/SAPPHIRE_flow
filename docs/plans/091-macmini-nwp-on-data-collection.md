# Plan 091 — Mac-mini NWP-on data-collection runbook

**Status**: DRAFT
**Phase**: 10c (staging infrastructure / operational data collection)
**Parent**: Plan 046 (Mac Mini Staging Deployment) — reuses the `docker-compose.macmini.yml` overlay, USB-backup mount, and LAN-only topology
**Reuses**: Plan 084 (dev-machine 2-station validation) — the hardened phase structure, exit gates, and the six operational footguns are adapted here from dev/runoff-only to mini/NWP-on
**Related**: Plan 077 (config-gated optional NWP / runoff-only mode, DONE), Plan 078 (runoff-only provenance, DONE via epic-088 M4), Plan 086/087 (NWP memory-bounded streaming + ICON mesh extraction, DONE — unblock NWP-on E2E), Plan 089 (model priority hierarchy, DONE — PR #48), Plan 090 (incomplete-cycle age-delay guard + coverage probe, **NOT merged** — see the accuracy note), Plan 062 (`PREFECT_HOME` persistence, open gap)
**Created**: 2026-07-02

---

> **This is an operator runbook, not an implementation plan.** It is DOCS-ONLY.
> It changes no code and deploys nothing by itself — it is the step-by-step
> procedure the operator (Bea) follows **on the Mac mini** to bring the merged
> SAPPHIRE Flow stack up in **NWP-on** mode, onboard a small set of BAFU river
> stations, run with **schedules active**, and collect **≥ 7 days** of data.

---

## Purpose

Deploy the stack on the Mac mini in NWP-on mode and let it run for a week or
more so that:

1. **LINDAS discharge history accrues.** BAFU LINDAS is real-time snapshot-only
   (no historical time series) — the deployment has to ingest over time to build
   an archive. The lag-based models need this: `linear_regression_daily`
   (`_LOOKBACK = 7`, `models/linear_regression_daily.py:30`) and `nwp_regression`
   (`_LOOKBACK = 7`, `models/nwp_regression.py:61`) both raise `Insufficient
   lookback` at **predict** time until 7 daily discharge lags exist in
   `observations`. Only after ≥ 7 days of accrued discharge can those models
   produce a live forecast.
2. **Repeated live ICON-driven forecasts + provenance are observed.** With
   schedules active, `forecast-cycle` (cron `0 */6`) fires four times a day and
   drives `nwp_rainfall_runoff` (weather-only, no lags — works from day one)
   against live ICON-CH2-EPS, so we accumulate a real record of NWP provenance
   (`primary` / `fallback` / `runoff_only`) and pipeline stability.

The **near-term value is data accrual + pipeline-stability observation.** The
two-model skill comparison (`nwp_rainfall_runoff` weather-only vs
`nwp_regression` weather+lags, and vs `linear_regression_daily`) is the eventual
payoff, unlocked once ≥ 7 days of discharge exist (Phase 5 — reference only).

---

## ⚠ Accuracy / prerequisite note — Plan 090 (PR #49) is NOT merged

This runbook was written against **`origin/main` at `18d6d64`** (Plan 089 / PR
#48 is the tip). Verified against the live tree on 2026-07-02:

- **Merged and present in `main`**: the operational ICON path (PR #44), NWP-on
  provenance with the `runoff_only` source + nullable cycle reference (PR #45,
  `3ee6f82`), the FI unit-gate fix (PR #46), the superset-input assembly fix
  (PR #47), and the config-driven model priority hierarchy (PR #48, Plan 089).
  Base `config.toml` ships `[adapters.weather_forecast] enabled = true`
  (`config.toml:367-368`) and the `[model_priorities]` table
  (`config.toml:51-56`).
- **NOT merged**: **Plan 090 / PR #49** (the incomplete-cycle age-delay guard).
  It lives on branch `docs/plan-090-incomplete-cycle` (`264e98c`). Consequently,
  on plain `main` **today**:
  - `nwp_cycle_min_age_minutes` **does not exist** in `config.toml` (it is added
    at `config.toml:17` only on the Plan 090 branch). There is no age-delay
    guard, no `nwp.cycle_too_recent` event, no `forecast_cycle.nwp_unavailable_runoff_only`
    event, and no `nwp.insufficient_coverage` event in `main`.
  - On NWP cycle exhaustion (a genuine ICON outage), `main` **aborts the whole
    cycle** with `forecast_cycle.nwp_fetch_failed_aborting`
    (`run_forecast_cycle.py:829`) — it does **not** gracefully degrade to
    runoff-only. The graceful runoff-only degradation-on-exhaustion behaviour is
    a Plan 090 deliverable.

**Operator decision (Phase 0 gate):** decide **before** deploying whether to

- **(Recommended) merge Plan 090 / PR #49 into `main` first**, which gives you
  (a) the age-delay guard `nwp_cycle_min_age_minutes = 105` that avoids fetching
  a still-publishing incomplete cycle (horizon truncation), and (b) graceful
  runoff-only degradation on cycle exhaustion. With #49 merged, the base config
  carries `nwp_cycle_min_age_minutes = 105`, so the NWP-on overlay below still
  needs **only** `enabled = true` (no need to repeat the age field), and the
  Phase 3/4 provenance events (`nwp.cycle_too_recent`,
  `forecast_cycle.nwp_unavailable_runoff_only`, `nwp.insufficient_coverage`) are
  emitted; **or**
- **run on plain `main` without #49** and accept: no age-delay guard (some
  cycles may be fetched while still publishing → truncated horizon on those
  cycles), and an ICON outage **aborts** the cycle rather than degrading to
  runoff-only.

Everywhere below, events and behaviours that exist **only with PR #49 merged**
are tagged **[Plan 090]**. The runbook is correct either way; the tags tell you
what to expect for the mode you chose.

---

## The NWP-on config problem this runbook solves

`docker-compose.macmini.yml` hard-codes the **runoff-only** overlay:

```yaml
# docker-compose.macmini.yml:22-28
services:
  prefect-worker:
    environment:
      SAPPHIRE_CONFIG_OVERLAY: /app/config/overlays/mac-mini.toml
    volumes:
      - /Volumes/sapphire-backup/pg_dumps:/data/backups:rw
      - /Users/sapphire/camels-ch:/data/raw:ro
      - ./config/overlays/mac-mini.toml:/app/config/overlays/mac-mini.toml:ro
```

`config/overlays/mac-mini.toml` sets `[adapters.weather_forecast] enabled =
false`, i.e. **runoff-only**. To run NWP-on we must override that env var and
mount a different overlay. **Do not** just drop `-f docker-compose.macmini.yml`:
that overlay also provides the USB-backup mount, the CAMELS-CH `/data/raw`
mount, and the host API port `8000:8000` — all of which the mini needs.

**The D3 footgun (Plan 077 D3, `config/_overlay.py:36-38`):** the overlay file
is **not baked into the image** (the Dockerfile copies only
`.venv/src/alembic`, not `config/`) and the worker is `read_only: true`. If
`SAPPHIRE_CONFIG_OVERLAY` points at a path that is **not bind-mounted**,
`config/_overlay.py` raises `FileNotFoundError` and the forecast-cycle crashes
**before** it ever evaluates NWP — it does **not** silently fall back to NWP.
So the NWP-on overlay MUST be both **selected** (env var) **and**
**bind-mounted** (like base `config.toml` at `docker-compose.yml:109`).

### Recommended approach — a tiny NWP-on overlay + a tiny compose override

Two small artifacts. **Full contents below** — create them on the mini exactly
as shown. (This PR is docs-only and does **not** commit them; committing them is
recommended as a small, reusable follow-up — see *Affected files*.)

**Artifact 1 — `config/overlays/mac-mini-nwp.toml`** (re-enables NWP; base
config's `nwp_cycle_min_age_minutes = 105` — present once PR #49 is merged —
applies without repeating it here):

```toml
# config/overlays/mac-mini-nwp.toml — Mac-mini NWP-ON overlay.
# Re-enables the ICON-CH2-EPS gridded NWP path that config/overlays/mac-mini.toml
# disables. Deep-merged over base config.toml (which already ships enabled=true);
# this overlay exists so we can point SAPPHIRE_CONFIG_OVERLAY at an explicitly
# NWP-on file instead of the runoff-only mac-mini.toml.
[adapters.weather_forecast]
enabled = true
```

**Artifact 2 — `docker-compose.macmini-nwp.yml`** (mirrors the macmini overlay's
env+mount pattern, but points at the NWP overlay; layered *last* so its env var
and mount win):

```yaml
# docker-compose.macmini-nwp.yml — NWP-ON selector for the Mac-mini stack.
# Layer AFTER docker-compose.macmini.yml so this env var + mount override the
# runoff-only selection:
#   docker compose -f docker-compose.yml \
#                  -f docker-compose.macmini.yml \
#                  -f docker-compose.macmini-nwp.yml up -d
services:
  prefect-worker:
    environment:
      SAPPHIRE_CONFIG_OVERLAY: /app/config/overlays/mac-mini-nwp.toml
    volumes:
      - ./config/overlays/mac-mini-nwp.toml:/app/config/overlays/mac-mini-nwp.toml:ro
```

Compose merges last-wins for scalar env values and **appends** volume mounts, so
the worker ends up with the USB-backup mount, the CAMELS-CH `/data/raw` mount,
**both** overlay files mounted (harmless), and `SAPPHIRE_CONFIG_OVERLAY` pointing
at the NWP-on file. The base `config.toml` mount from `docker-compose.yml:109`
still supplies the merge base.

> **Commit or transient?** Recommend committing both (they are small and
> reusable for every future mini NWP-on run), but **this PR keeps to docs-only**
> — the operator creates them on the mini from the blocks above, or a trivial
> follow-up PR adds them as tracked files.

---

## Environment (copy-paste preamble — run ON the mini)

All commands run from the repo root **on the Mac mini**. Set once per shell:

```bash
cd /path/to/SAPPHIRE_flow            # the repo checkout on the mini (confirm actual path)

uv sync                              # resolve the uv env so `uv run` below works

# Image tag — docker-compose.yml references sapphire-flow:${VERSION} with no
# default (docker-compose.yml:69); an unset VERSION aborts every compose call.
export VERSION="$(uv run bump-my-version show current_version)"
```

> **zsh word-splitting gotcha:** unlike bash, zsh does **not** word-split an
> unquoted variable, so a `DC="docker compose -f a -f b -f c"` alias expands as a
> single mis-parsed token. On the mini's default shell (zsh), either write the
> full three-file command each time, or use a shell **array**:
>
> ```bash
> DC=(docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml)
> "${DC[@]}" ps          # array expansion word-splits correctly in zsh AND bash
> ```
>
> The rest of this runbook writes the full command for clarity.

```bash
# The full NWP-on stack invocation used throughout (three overlays):
#   docker compose -f docker-compose.yml \
#                  -f docker-compose.macmini.yml \
#                  -f docker-compose.macmini-nwp.yml <cmd>

# Host API on the mini — the macmini overlay maps 8000:8000 (docker-compose.macmini.yml:30-32).
# This is :8000, NOT the dev host's :8010.
API=http://localhost:8000/api/v1
```

Targets (defaults — see Phase 2 for widening): `BASINS='["2009","2091"]'`.

> **Re-run this whole preamble in every new terminal** (e.g. one shell tailing
> logs, one issuing commands). `VERSION` is shell-local and hard-required.

---

## Phase 0 — Prerequisites (verify BEFORE deploy)

Do **not** proceed until every box is checked.

- [ ] **Merged `main` includes PRs #44–#48** (operational ICON path, NWP-on
      provenance, FI unit gate, superset inputs, model priority hierarchy).
      `git log --oneline -8` on the mini should show `18d6d64` (Plan 089 / #48)
      at or below `HEAD`.
- [ ] **Plan 090 / PR #49 decision made** (see the accuracy note above). If you
      want the age-delay guard + graceful runoff-only degradation, **merge #49
      first** and re-confirm `grep -n nwp_cycle_min_age_minutes config.toml`
      returns `nwp_cycle_min_age_minutes = 105`. If running without #49, note
      that the `[Plan 090]`-tagged gates/events below will not appear.
- [ ] `uv sync` succeeds.
- [ ] `secrets/db_password` present (`ls -l secrets/db_password`; `chmod 600`).
      This is the only secret v0 Swiss needs (all sources are public).
- [ ] **CAMELS-CH staged and non-empty at the mount source**:
      `ls /Users/sapphire/camels-ch/CAMELS_CH | head` is non-empty. The macmini
      overlay binds `/Users/sapphire/camels-ch → /data/raw:ro`
      (`docker-compose.macmini.yml:27`), so CAMELS-CH must live at
      `/Users/sapphire/camels-ch/CAMELS_CH` on the host.
- [ ] **USB backup SSD mounted**: `/Volumes/sapphire-backup/pg_dumps` exists,
      and the sentinel file is present:
      `ls /Volumes/sapphire-backup/pg_dumps/.sapphire-backup-volume` (Plan 046
      §C2). `backup_database_flow` refuses to run if the sentinel is absent.
- [ ] `.env` present if the deployment uses one (e.g. `CAMELS_CH_HOST_DIR` is
      **not** needed on the mini — the macmini overlay hard-codes the
      `/Users/sapphire/camels-ch` bind — but confirm no other `.env`-driven
      vars are required by your checkout).
- [ ] **Docker Desktop running** (`docker info` succeeds) with ≥ 16 GB RAM /
      ≥ 100 GB disk allocated (Plan 046 §A0). The worker is `mem_limit: 8g`
      (`docker-compose.yml:78`), sized above the 4 GiB `/tmp/sapphire_nwp`
      tmpfs working set — leave headroom in the Docker VM.
- [ ] **PREFECT_HOME persistence — settle or accept as risk (Plan 062).**
      `PREFECT_HOME` is **unset** in every compose file, so Prefect
      server/deployment/schedule state lives inside the `prefect_data` named
      volume + the server's working dir. For a multi-day run with schedules
      active, a worker/host restart must not lose the registered schedules.
      Mitigation: the `init` service re-runs `alembic upgrade head` +
      `register_deployments` on every `up` (`docker-compose.yml:205-210`), so
      deployments+crons are **re-registered idempotently** on restart, and
      `prefect_data` (`docker-compose.yml:250`) persists the server DB across a
      plain `docker compose restart` / host reboot (it is only wiped by
      `down -v`). **Accept-as-risk is reasonable for this run**, because a
      restart re-registers the schedules; but **do not `down -v`** mid-run
      (that wipes run history + schedules — see Rollback). Record the observed
      value: `docker compose ... exec -T prefect-server printenv PREFECT_HOME ||
      echo "unset (expected)"`.
- [ ] **The two NWP-on overlay files created** on the mini
      (`config/overlays/mac-mini-nwp.toml` + `docker-compose.macmini-nwp.yml`),
      exactly per the blocks above.

---

## Phase 1 — Clean start (schedules ACTIVE)

Goal: bring the NWP-on stack up from a known-clean DB, confirm every service is
healthy and every deployment registered, then **leave schedules active** (unlike
Plan 084, which pauses them — here we *want* the cron'd cycles firing to collect
data). The onboarding-scope guard in Phase 2 defends the one real full-set risk.

```bash
# WARNING: down -v WIPES pgdata, prefect_data, model_artifacts, nwp_grids, backups.
# Only run on a throwaway DB. If prior real observations must be kept, pg_dump first.
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml down -v

docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml build

docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml up -d
```

**Exit gates** (each a hard check):

1. **`init` exited 0**:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml logs init | tail -n 5   # shows "Init complete"
   ```
   (Use `ps -aq init` if inspecting exit code — `init` has `restart: "no"`,
   `docker-compose.yml:235`, so it has already exited by gate time and `ps -q`
   omits it.)
2. **Long-running services healthy within 5 min** (first boot):
   `docker compose ... ps` shows `postgres`, `prefect-server`, `prefect-worker`,
   `api` healthy/running. (`caddy` binds host `:80`/`:443` and is **not** on the
   validation path here — the watchdog and this runbook hit `:8000` directly;
   treat caddy state as non-blocking.)
3. **Health endpoint** (host `:8000`, per the macmini overlay):
   ```bash
   curl -s "$API/health" | jq -e '.status=="ok" and .prefect_status=="ok"'   # true
   ```
4. **9 deployments registered**:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
     exec -T prefect-worker prefect deployment ls
   ```
   Lists all 9 (`forecast-cycle`, `ingest-observations`, `backup-database`,
   `train-models`, `run-hindcast`, `compute-skills`, `compute-combined-skills`,
   `onboard-stations`, `onboard-model`).
5. **CAMELS-CH visible to the worker**:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
     exec -T prefect-worker ls /data/raw/CAMELS_CH | head
   ```
   Non-empty (confirms the `/Users/sapphire/camels-ch` read-only bind resolved).
6. **NWP-on overlay actually selected** (the D3 guard): confirm the worker sees
   the NWP overlay path and it is mounted:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
     exec -T prefect-worker sh -c 'echo $SAPPHIRE_CONFIG_OVERLAY; cat /app/config/overlays/mac-mini-nwp.toml'
   ```
   Prints `/app/config/overlays/mac-mini-nwp.toml` and `enabled = true`. If the
   `cat` fails, the mount is missing → fix Artifact 2 before continuing (else the
   first NWP forecast-cycle crashes with `FileNotFoundError`).
7. **DB is clean**: `curl -s "$API/stations?limit=200" | jq '.total'` → `0`.

**Schedules stay ACTIVE.** The cron'd deployments — `ingest-observations`,
`forecast-cycle` (`0 */6`), `backup-database` (`0 2`) — remain enabled so data
collection begins immediately. (`onboard-stations` carries **no** cron — Plan
084 D6 — so it cannot auto-fire; its only risk is the parameter-less config
fallback, handled in Phase 2.)

---

## Phase 2 — Onboard NWP-on (scoped, undisturbed)

Goal: register the model set (including the NWP models), then onboard exactly the
target stations, letting onboarding run to completion **without touching the
worker**.

### 2a — Register the model set (Flow 13, one `onboard-model` run per model)

`onboard_model_flow` takes a single `model_id` and discovers it via
`discover_models()` entry points (`flows/onboard_model.py:64,190`). The five
available model IDs (`pyproject.toml:132-137`) are: `linear_regression_daily`,
`climatology_fallback`, `persistence_fallback`, `nwp_regression`,
`nwp_rainfall_runoff`. For NWP-on you need the two NWP models **and** the
fallbacks (so the PRIMARY chain always has a floor):

```bash
for m in nwp_rainfall_runoff nwp_regression linear_regression_daily persistence_fallback climatology_fallback; do
  docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
    exec -T prefect-worker prefect deployment run onboard-model/onboard-model -p "model_id=\"$m\""
done
```

**Exit gate**: each run reaches `COMPLETED` (empty-scope register-only path is
~seconds). Verify the `models` table has all 5 rows (the `/models` API route is
HTML-only, so check the DB directly):
```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  exec -T postgres psql -U sapphire -d sapphire -c "SELECT id FROM models ORDER BY id;"
```
Expect 5 rows including both `nwp_rainfall_runoff` and `nwp_regression`.

### 2b — Onboard the target stations

Default targets: **`2009` + `2091`** (both discharge/river stations with
CAMELS-CH attributes and LINDAS coverage; Plan 084 D1). To **widen**, add more
river IDs from `config.toml [onboarding].basin_ids` to the JSON list — e.g.
`'["2009","2091","2033","2085"]'` (Plan 046's five-station set was
`{2091,2009,2033,2085}` rivers + `2004` lake; **do not** include lakes like
`2004` — water-level-only stations correctly stay in `onboarding`, no discharge
model). Keep the set small for a first NWP-on run.

**Footgun guards (adapted from Plan 084):**

- **Never restart / recreate / `--force-recreate` the worker while onboarding is
  in flight** (Plan 084 gotcha 1 / failure mode 1) — it tears down the flow
  before Step 8 marks stations operational → 0 operational.
- **Never trigger `onboard-stations` parameter-less** (Plan 084 D6 / failure
  mode 2). A parameter-less run falls back to `config.toml`'s full ~167-list
  (`flows/onboard.py:144-152`, logs `basin_ids_from_config`). **Always** pass
  `-p 'basin_ids=[...]'`. Before triggering, assert no `onboard-stations` run is
  already RUNNING/SCHEDULED/PENDING:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
    exec -T prefect-worker prefect flow-run ls --flow-name onboard-stations \
    --state RUNNING --state SCHEDULED --state PENDING
  # cancel any that appear:  ... prefect flow-run cancel <FLOW_RUN_ID>
  ```

**Trigger** (the `-p 'basin_ids=[...]'` argument is mandatory; JSON-string
quoting per Plan 046 §A3 finding F2):
```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  exec -T prefect-worker prefect deployment run onboard-stations/onboard-stations \
  -p 'basin_ids=["2009","2091"]'
```
Watch to completion:
```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  logs -f prefect-worker | grep -E 'onboarding_starting|onboarding_flow_complete|station_operational|station_no_active_artifact|model.onboarding_unit_completed|training_error'
```

**Exit gates:**

1. Exactly one `onboarding_starting` carries `basin_ids` of your target IDs.
   **If it shows `basin_ids=null` (or the full ~167-list), CANCEL immediately**
   (`prefect flow-run cancel <FLOW_RUN_ID>`) — that run fell back to config — and
   re-trigger with the explicit `-p` argument.
2. `onboarding_flow_complete` carries `stations_marked_operational == len(target)`
   (2 for the default). Record `observations_imported`, `observations_qc_passed`,
   `observations_qc_failed`, `observations_qc_suspect`. (Onboarding runs a
   day-by-day historical hindcast — expect ~20 min for 2 stations; Plan 068
   territory. Do not touch the worker while it runs.)
3. One `onboarding.station_operational` per target station; **zero**
   `onboarding.station_no_active_artifact`.
4. **Both NWP models trained + promoted.** The NWP models train on the CAMELS-CH
   historical discharge imported during onboarding, so their artifacts go ACTIVE
   even though live lags do not yet exist. Confirm ACTIVE artifacts:
   ```bash
   PW=$(cat secrets/db_password)
   docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
     exec -T postgres psql -U sapphire -d sapphire -c \
     "SELECT model_id, count(*) FROM model_artifacts WHERE status='active' GROUP BY model_id ORDER BY model_id;"
   ```
   Expect `nwp_rainfall_runoff` and `nwp_regression` (and
   `linear_regression_daily`) with ≥ 1 ACTIVE row each. (`persistence_fallback` /
   `climatology_fallback` are stateless fallbacks.)
5. **Priority hierarchy applied — no manual DB edit needed** (Plan 089 / PR #48).
   `config.toml:51-56` sets `nwp_regression=10`, `nwp_rainfall_runoff=20`,
   `linear_regression_daily=30`, `persistence_fallback=90`,
   `climatology_fallback=100`. Lower = tried first in the PRIMARY first-success
   chain, so a skill model wins over a fallback automatically. Spot-check the
   assignments:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
     exec -T postgres psql -U sapphire -d sapphire -c \
     "SELECT station_id, model_id, priority FROM model_assignments ORDER BY station_id, priority;"
   ```
   `nwp_rainfall_runoff` (priority 20) sits above `climatology_fallback` (100).
6. **ICON weather binding present.** Onboarding creates the
   `icon_ch2_eps / basin_average` binding for NWP models
   (`services/onboarding.py:348-356`):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
     exec -T postgres psql -U sapphire -d sapphire -c \
     "SELECT station_id, nwp_source, extraction_type FROM station_weather_sources;"
   ```
   Each target station has a `icon_ch2_eps / basin_average` row.

---

## Phase 3 — Confirm one NWP-on forecast

Goal: confirm the first live ICON-driven forecast lands with correct provenance.
With schedules active, `forecast-cycle` (cron `0 */6`) fires on its own at
00/06/12/18 UTC. To confirm without waiting, trigger one manually
(parameter-less — Plan 077 self-wires `MeteoSwissNwpAdapter` from `enabled=true`;
Plan 084 D4):

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  exec -T prefect-worker prefect deployment run forecast-cycle/forecast-cycle

docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  logs -f prefect-worker | grep -E 'forecast_cycle.starting|nwp.fetch_completed|nwp.archive_completed|forecast_cycle.nwp_fetch_failed_aborting|forecast.run_completed|forecast_cycle.complete'
```

**Exit gates:**

1. `forecast_cycle.starting` reports `stations = <len(target)>`; the run reaches
   `COMPLETED`.
2. **A forecast is stored for `nwp_rainfall_runoff`** (the weather-only model —
   it needs no discharge lags, so it produces a forecast on day one) with
   `representation = members` and ~21 members (ICON-CH2-EPS ensemble), and
   `nwp_cycle_source` = `primary` (or `fallback` if the freshest cycle was
   stale). Verify via the API (**wide window** — the forecasts endpoint defaults
   to `[now-7d, now]`, and a `cycle_time` rounding to a future slot would fall
   after `end=now` and read `.total==0`, `api_stations.py`):
   ```bash
   WIN='start=2000-01-01T00:00:00Z&end=2100-01-01T00:00:00Z'
   for sid in $(curl -s "$API/stations?status=operational&limit=200" | jq -r '.items[].id'); do
     curl -s "$API/stations/$sid/forecasts?limit=5&$WIN" \
       | jq -r '.items[] | "\(.model_id)  rep=\(.representation)  src=\(.nwp_cycle_source)"'
   done
   ```
   Expect a `nwp_rainfall_runoff  rep=members  src=primary` line per station.
   Cross-check in the DB (`forecasts.nwp_cycle_source ∈ {primary,fallback,
   runoff_only}`, `db/metadata.py:620-623`):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
     exec -T postgres psql -U sapphire -d sapphire -c \
     "SELECT model_id, representation, nwp_cycle_source, count(*) FROM forecasts GROUP BY 1,2,3 ORDER BY 1;"
   ```
3. **`src` should NOT be `runoff_only`** on a healthy run — NWP is enabled and
   the operational path ran. **Exception:** a genuine ICON outage. With **[Plan
   090]** merged, cycle exhaustion degrades gracefully to `runoff_only` (that is
   the *correct* degraded behaviour, Plan 090 D3) and you would see
   `forecast_cycle.nwp_unavailable_runoff_only` / `nwp.insufficient_coverage`.
   **Without #49**, cycle exhaustion instead **aborts** the cycle with
   `forecast_cycle.nwp_fetch_failed_aborting` (`run_forecast_cycle.py:829`) and
   no forecast is stored — retry on the next cron tick.
4. **`nwp_regression` failing `Insufficient lookback` is EXPECTED, not a
   failure.** `nwp_regression` (and `linear_regression_daily`) need 7 daily
   discharge lags at predict time (`models/nwp_regression.py:61`,
   `models/linear_regression_daily.py:47`); until ~7 days of live LINDAS
   discharge accrue they raise `Insufficient lookback` and the PRIMARY chain
   falls through to the next model. The stored `nwp_rainfall_runoff` forecast is
   the day-one result; the lag models join once discharge history exists
   (Phase 4 trainability query / Phase 5).

---

## Phase 4 — Multi-day monitoring (the point of the run)

Leave the stack running with schedules active for **≥ 7 days**. Run this
checklist **daily** (or at least every couple of cron ticks). All commands use
the host `:8000` API / the three-overlay compose invocation.

**A. Health + liveness**
```bash
curl -s "$API/health" | jq   # {"status":"ok","prefect_status":"ok",...}
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml ps
```
Confirm no worker crash-loop (`prefect-worker` not repeatedly restarting):
`docker compose ... ps` shows a stable uptime, and
`... logs --since 24h prefect-worker | grep -iE 'traceback|OOMKilled|exited'` is
quiet. (Watch RAM: the NWP path stages ICON GRIB under the 4 GiB
`/tmp/sapphire_nwp` tmpfs; the worker `mem_limit: 8g` cgroup-kills an over-budget
run rather than letting the host OOM killer fire — a SIGKILL'd cycle shows as a
`FAILED`/`CRASHED` run, retried next tick.)

**B. Ingest cadence (discharge accrual)**
```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  logs --since 6h prefect-worker | grep -E 'ingest|observations' | tail
```
`ingest-observations` runs every cron tick and appends LINDAS snapshots.
> **KNOWN: LINDAS Monday-morning publishing-window fragility.** The BAFU LINDAS
> weekly publish has failed on past Mondays. If ingest returns zero rows on a
> Monday morning, **check the VoID descriptor first** before treating it as
> schema drift — it is usually the publishing window, not a code break.

**C. Forecast provenance mix**
```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  exec -T postgres psql -U sapphire -d sapphire -c \
  "SELECT date_trunc('day', issued_at) d, model_id, nwp_cycle_source, count(*)
     FROM forecasts GROUP BY 1,2,3 ORDER BY 1 DESC, 2;"
```
Track the `primary` vs `fallback` vs `runoff_only` split over days. Watch the
event stream for provenance signals:
```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  logs --since 24h prefect-worker \
  | grep -E 'nwp.fetch_completed|forecast_cycle.nwp_fetch_failed_aborting|nwp.cycle_too_recent|forecast_cycle.nwp_unavailable_runoff_only|nwp.insufficient_coverage'
```
`nwp.cycle_too_recent`, `forecast_cycle.nwp_unavailable_runoff_only`, and
`nwp.insufficient_coverage` are **[Plan 090]** events — present only if PR #49 is
merged. On plain `main`, expect `nwp.fetch_completed` on success and
`forecast_cycle.nwp_fetch_failed_aborting` on cycle exhaustion.

**D. Disk usage (backup SSD + nwp_grids growth)**
```bash
df -h /Volumes/sapphire-backup
docker system df -v | grep -E 'nwp_grids|backups|pgdata'
ls -lh /Volumes/sapphire-backup/pg_dumps | tail   # nightly backup-database (0 2 UTC)
```
Each archived ICON cycle grows the `nwp_grids` volume — watch it does not fill
the Docker VM disk over a week (`archive = true`, `config.toml:371`). Confirm the
nightly `pg_dump` lands (newest `*.dump` < 26 h old — Plan 046 §C3 watchdog
threshold).

**E. Accrued discharge history + lag-model trainability**
```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  exec -T postgres psql -U sapphire -d sapphire -c \
  "SELECT station_id,
          max(timestamp) AS latest,
          count(*) FILTER (WHERE timestamp > now() - interval '8 days') AS rows_8d,
          count(DISTINCT date_trunc('day', timestamp))
            FILTER (WHERE timestamp > now() - interval '8 days') AS daily_buckets_8d
     FROM observations
    WHERE parameter = 'discharge' AND qc_status = 'qc_passed'
    GROUP BY station_id ORDER BY station_id;"
```
When `daily_buckets_8d >= 7` for a station, `linear_regression_daily` and
`nwp_regression` have enough live daily lags to satisfy the `_LOOKBACK = 7`
predict-time check — from that cycle on they should stop raising `Insufficient
lookback` and start contributing forecasts. That is the signal Phase 5 waits for.

---

## Phase 5 — Wrap / next step (reference — do NOT execute here)

Once **≥ 7 days** of QC-passed discharge have accrued (Phase 4E shows
`daily_buckets_8d >= 7`):

1. **Retrain the lag models on the accrued live history**, if desired
   (`train-models` with `-p 'model_ids=["nwp_regression","linear_regression_daily"]'`
   — note the plural **list** param, Plan 046 §A3 step 4). This picks up the
   operational discharge that has accumulated since onboarding. (Retrain policy
   is Plan 066 territory — configurable, still under research.)
2. **Two-model skill comparison** — compare `nwp_rainfall_runoff` (weather-only)
   vs `nwp_regression` (weather + lags) vs `linear_regression_daily` via
   `run-hindcast` → `compute-skills` (Plan 046 §A3 steps 5-6, JSON-string quoting
   for UUIDs). This is the eventual payoff of the data-collection run.

**Teardown / pause option.** To pause data collection without losing state
(preferred): pause the schedules but keep the stack up —
```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml \
  exec -T prefect-worker prefect deployment schedule pause --all
```
(resume with `... schedule resume --all`; a bare `pause <flow>/<dep>` is a no-op
on Prefect 3.6.x — Plan 084 D6/resolved-ground-truth 4). See Rollback for a full
stop.

---

## Known limitations / risks

- **Plan 090 (PR #49) not merged** — no incomplete-cycle age-delay guard on plain
  `main`. Some cycles may be fetched while ICON is still publishing → **horizon
  truncation** on those cycles; and cycle exhaustion **aborts** rather than
  degrading to runoff-only. Merging #49 mitigates both; even with #49, the
  **Plan 090 P2 exact-coverage probe is still deferred**, so member-set /
  horizon coverage is only approximately guarded (the age-delay reduces, but does
  not eliminate, truncation on some cycles).
- **`PREFECT_HOME` gap (Plan 062).** `PREFECT_HOME` is unset everywhere; Prefect
  state lives in the `prefect_data` volume + `init`-time re-registration. A plain
  restart / host reboot re-registers schedules and preserves the server DB
  volume, but **`down -v` wipes everything** (run history + schedules + data). Do
  not `down -v` mid-collection. This is accepted-as-risk for this run.
- **LINDAS Monday-morning publishing window** — periodic zero-row ingests around
  Monday mornings are usually the publish window, not schema drift; check the
  VoID descriptor before escalating.
- **NWP-on memory** — Plan 086/087 landed the memory-bounded streaming + mesh
  extraction that fixed the Plan 084 NWP-OOM; the worker `mem_limit: 8g` bounds
  blast radius but is **not** a graceful failure — an over-budget cycle is
  SIGKILL'd and shows as a FAILED/CRASHED run, retried next tick. Watch RAM
  (Phase 4A) over the multi-day run.
- **Lag models silent until ~7 days** — `nwp_regression` /
  `linear_regression_daily` raise `Insufficient lookback` until 7 daily discharge
  lags exist. Expected, not a failure; `nwp_rainfall_runoff` carries the PRIMARY
  forecast in the interim.

## Rollback / clean stop

- **Pause collection, keep data** (preferred): `prefect deployment schedule
  pause --all` (Phase 5), or `docker compose ... stop` to stop containers while
  preserving all volumes. `docker compose ... start` / `up -d` resumes;
  `init` re-registers deployments (schedules come back **active** on `up`, so
  re-pause if you want them off — Plan 084 gotcha 7).
- **Full stop, keep data**: `docker compose -f docker-compose.yml -f
  docker-compose.macmini.yml -f docker-compose.macmini-nwp.yml down` (no `-v`) —
  stops and removes containers but **keeps** the named volumes (`pgdata`,
  `prefect_data`, `nwp_grids`, `backups`, `model_artifacts`).
- **Full teardown, wipe data**: add `-v`. **Destroys** all volumes (observations,
  forecasts, discharge history, Prefect run history). Only for a clean re-start;
  `pg_dump` first if any accrued data matters.

---

## Affected files

- `docs/plans/091-macmini-nwp-on-data-collection.md` (this runbook — the only
  file this PR adds).
- **Described, not committed by this PR** (docs-only): `config/overlays/mac-mini-nwp.toml`
  and `docker-compose.macmini-nwp.yml` — full contents are in *The NWP-on config
  problem* section above. Recommended follow-up: commit both as small, reusable
  tracked files.
- `docs/plans/README.md` — add a Plan 091 index entry (deferred; the working
  tree already carries an uncommitted README change, kept out of this PR's
  scope).
- No production code or committed config changes (runbook only).
