---
status: DRAFT
created: 2026-09-08
plan: 255
title: Onboarding reports what it actually did — a per-station outcome record and a human-readable report
scope: Give station onboarding a per-station outcome vocabulary and emit a human-readable report at the end of every run, naming each station and the reason it is complete, degraded or withheld. Additive and observational only. Explicitly NOT a change to promotion behaviour (Plan 256 owns that), NOT a change to what onboarding computes, NOT a dashboard or API surface.
depends_on: []
blocks: [256]
source: 2026-09-08 — a read-only forensic diagnosis of the mac-mini staging host found five stations marked `operational` that the promotion gate could not have passed, and one station silently downgraded to stale forcing. Neither condition was visible anywhere except by reconstructing the run from nightly pg_dumps.
---

# Plan 255 — onboarding reports what it actually did

## Status

**DRAFT.** Awaiting owner READY.

## Why this exists

Station onboarding currently returns `OnboardingResult` — sixteen integer counters and a flat
`errors: list[str]`. `flows/onboard.py` logs those counters as one `onboarding_flow_complete` line.

That is enough to know *how many* stations were marked operational. It is not enough to know
**which stations, and whether the ones that were not are a problem**. Every per-station shortfall in
`services/onboarding.py` is a `log.warning` in a run whose logs are gone as soon as the worker
container is recreated:

| condition | current handling | source |
|---|---|---|
| invalid basin polygon → no MeteoSwiss binding | `log.warning("reanalysis_backfill.station_excluded", reason="no_valid_basin_geometry")` | `services/reanalysis_backfill.py` |
| held out for zero-row backfill | `log.warning("onboarding.station_held_out_meteoswiss_backfill")` | `services/onboarding.py:765` |
| no forecast_targets → QC/baseline/regime skipped | `log.warning("station_no_forecast_targets")` ×3 | `services/onboarding.py:777, 840, 887` |
| baselines skipped, missing water-level datum | `log.info("baselines_skipped_missing_water_level_datum")` | `services/onboarding.py:851` |
| no climatology floor → not promoted | `errors.append(...)` + `log.warning("onboarding.station_missing_climatology_floor")` | `services/onboarding.py:1219` |
| lookback shorter than the model requires | `log.warning("operational_inputs.short_lookback")` — **and continues** | `services/operational_inputs.py:667` |

An operator finishing an onboarding run has no artefact that answers "is this fleet ready?".

⭐ **There is one precedent worth building on rather than duplicating.** The forecast cycle already
emits a per-station, per-cycle `FORECAST_STATION_DARK` record
(`flows/run_forecast_cycle.py:846`) carrying a machine-readable `reason` and the station's assigned
models — on staging it names exactly the five stations this plan exists for, `critical`,
`all_models_failed`, every cycle. That is the right shape: per-station, reasoned, persisted, not a
log line. Two lessons for this plan:

1. **Match its vocabulary** — the outcome reasons in T1 should sit alongside
   `FORECAST_STATION_DARK`'s, not invent a parallel scheme for the same conditions.
2. **Persisting is not surfacing.** `ops/watchdog.py` probes only three check types and that is not
   one of them, so the system has been correctly reporting these five stations every six hours to
   nobody. A report that is written and unread fails in the same way — hence T2's requirement that
   it be a run artefact an operator is handed, not another row to go looking for.

**This is not hypothetical.** On 2026-09-08 establishing the state of a 148-station fleet required
reading `flow_run_state` messages, clustering `stations.updated_at` to the microsecond, correlating
`model_artifacts.promoted_at` against a cancellation timestamp, and extracting the `stations` table
out of two 25 GB nightly `pg_dump` archives to bracket a status change. That is forensics. It should
have been a report.

## What is measured (mac-mini staging, read-only, 2026-09-08 06:30–06:45 UTC)

148 stations, all `river`/`gauged`/`operational`, all with a basin carrying geometry, 516 attributes,
`area_km2` and `band_geometries`, all with `forecast_targets = ["discharge"]`, all with an
`icon_ch2_eps`/`basin_average`/FORECAST binding, and all six `model_assignments`. On the label, a
uniform fleet.

The substance is five distinct tiers:

| tier | stations | evidence |
|---|---|---|
| all 6 models trained | 36 | `model_artifacts` status=active, 6 distinct `model_id` |
| missing `persistence_fallback`, `seasonal_precip_runoff_regression` | 41 | 4 distinct active `model_id` |
| also missing `nwp_regression` | 66 | 3 distinct active `model_id` |
| one artifact, added later by a *model* onboarding run | 1 (**2041**) | sole artifact `trained_at` 2026-09-04 |
| **no model artifact of any kind, ever** | 4 (**2116, 2392, 2615, 2623**) | zero rows in `model_artifacts` |

The 36/41/66 split is the legitimate, understood consequence of a deliberate mid-training
cancellation on 2026-09-01. The last two rows are not: those five stations have no
`climatology_fallback` floor, no `clim_baselines` rows and no `flow_regime_configs` row, and four of
them have never produced a forecast. `2392` holds 784 observations in total and `2623` holds 131.

Separately, **2024 Branson** carries an invalid basin polygon
(`ST_IsValidReason` → `Ring Self-intersection[7.20127964 46.1757792]`). It is the only station of 148
with no MeteoSwiss reanalysis binding and **no MeteoSwiss forcing rows** — it runs on `camels-ch`
forcing alone, which ends `2020-12-31`. The other 147 carry six MeteoSwiss products through
`2026-09-06`. Nothing in the database records that this happened.

## What this plan does NOT do

It does not change which stations get promoted, does not add a gate, and does not touch
`update_station_status`. A run that promotes a station today promotes it after this plan too — it
just also says so, per station, in a form a person can read. Behaviour change is Plan 256.

This split is deliberate: the report is the vocabulary the gate will be expressed in, and it is worth
landing on its own so the next onboarding run is legible even if the gate work is still in review.

## Tasks

### T1 — a per-station outcome record

**Outcome.** A frozen dataclass records, for each station resolved in a run, the station code and id
and the observable facts onboarding established about it: basin geometry validity, forcing sources
that landed, whether QC/baselines/regime produced rows, which models were assigned, which models
produced an ACTIVE artifact, whether the climatology floor exists, whether the station was held out
and why, and the resulting promotion decision. `OnboardingResult` gains a
`station_outcomes: tuple[StationOnboardingOutcome, ...]` field; the existing counters stay, byte for
byte, so every current caller and test is unaffected.

The degradation vocabulary is an `Enum`, not a set of booleans or free strings — per
`CLAUDE.md` §Enums over booleans. At minimum: `INVALID_BASIN_GEOMETRY`,
`NO_OPERATIONAL_FORCING`, `NO_FORECAST_TARGETS`, `MISSING_WATER_LEVEL_DATUM`,
`INSUFFICIENT_OBSERVATION_HISTORY`, `NO_CLIMATOLOGY_FLOOR`, `HELD_MISSING_BACKFILL`,
`MODEL_TRAINING_INCOMPLETE`.

**In.** `src/sapphire_flow/types/onboarding.py` (new or existing home for `OnboardingResult`),
`src/sapphire_flow/services/onboarding.py` — the six sites tabulated above become record-writes
*in addition to* their existing log lines, never instead of them.
**Out.** No change to any store Protocol. No new DB table. No change to control flow: a station that
is processed today is still processed.

**Verification.** `uv run pytest tests/unit/services/test_onboarding.py -k outcome` — a fake-store run
over a fixture with one valid station and one invalid-geometry station returns two outcomes with the
expected reasons, and the pre-existing counter assertions in that file still pass unmodified.

**Pre-change.** RED: the same test asserting `result.station_outcomes` fails with `AttributeError`,
because the field does not exist. That is a signature failure, not proof of the defect — so the test
must *also* assert that the invalid-geometry station's outcome names `INVALID_BASIN_GEOMETRY`, which
is the fact no current code path records anywhere. See
`feedback_red_first_must_prove_the_fault`.

### T2 — the human-readable report

**Outcome.** At the end of `onboard_stations_flow`, a plain-text report is written to the run's
artifact directory and logged as a single structured event carrying its path. It has four named
sections — **Complete**, **Degraded**, **Withheld**, **Failed** — and every station appears in
exactly one, by code and name, with its reason. It ends with a per-model training summary (assigned
vs. active artifact) so a mid-training cancellation is legible at a glance rather than requiring a
`promoted_at` histogram.

Counts appear *alongside* names, never instead of them. A report that says "5 stations degraded"
without naming them reproduces the problem this plan exists to solve.

**In.** `src/sapphire_flow/services/onboarding_report.py` (new), `src/sapphire_flow/flows/onboard.py`
(call site only).
**Out.** No API endpoint, no dashboard, no HTML. Plain text. The report is a deployment artefact, not
a product surface.

**Verification.** `uv run pytest tests/unit/services/test_onboarding_report.py` — rendering a
hand-built `OnboardingResult` containing one station per tier produces a report in which each station
code appears exactly once, under the expected heading, with its reason string present.

**Pre-change.** N/A — new module, no prior behaviour. The behavioural claim is carried by T1.

### T3 — reconstruct the report for the current staging fleet

**Outcome.** The report renderer is run read-only against the mac-mini database and the output is
attached to this plan, so the fleet's real state is recorded in the repo rather than living in one
session's scratch. This is the acceptance evidence that the vocabulary in T1 actually describes the
conditions the fleet is in.

**In.** A read-only invocation against staging; the rendered report committed under this plan.
**Out.** No write of any kind to the staging database. No remediation — Plan 256 owns that.

**Verification.** The rendered report places 2116, 2392, 2615, 2623 and 2041 under **Withheld** or
**Degraded** with `NO_CLIMATOLOGY_FLOOR`, places 2024 under **Degraded** with
`INVALID_BASIN_GEOMETRY` *and* `NO_OPERATIONAL_FORCING`, and places the 36 six-model stations under
**Complete** — matching the measured table above, station code for station code.

⚠️ The report will read them as *degraded*, not *withheld*, because on staging they are already
`operational`. That mismatch is the finding, not a bug in the renderer, and Plan 256 resolves it.

**Pre-change.** N/A — verification task.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
