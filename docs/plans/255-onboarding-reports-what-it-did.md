---
status: DRAFT
created: 2026-09-08
plan: 255
title: Onboarding reports what it actually did — typed eligibility exclusions, a per-station outcome record, and a human-readable report
scope: Turn the per-station facts onboarding already discovers into typed data and a report an operator is handed at the end of a run. Three layers, in order — make eligibility exclusions returnable instead of log-only (the shared prerequisite Plan 256 T1 also needs), record a per-station outcome, render and deliver the report. Additive and observational only. Explicitly NOT a change to promotion behaviour (Plan 256), NOT a dashboard or API surface, NOT a forecast-time sufficiency check.
depends_on: []
blocks: []
source: 2026-09-08 — a read-only diagnosis of the mac-mini staging host found five stations marked `operational` that the promotion gate could not have passed, and one silently excluded from operational forcing. Neither condition was visible anywhere except by reconstructing the run from nightly pg_dumps. Revised the same day after an independent Codex review returned 5 blockers and 7 majors against the first draft.
---

# Plan 255 — onboarding reports what it actually did

## Status

**DRAFT — revised after independent review, not yet re-reviewed.** Awaiting owner READY.

The first draft was reviewed by Codex on 2026-09-08 and by the authoring model against source.
Findings folded in below. Three were structural, not cosmetic:

1. Two of the conditions the plan promised to report **could not be collected within its declared
   scope** — the invalid-geometry reason is discarded inside `eligible_meteoswiss_configs`, and
   `short_lookback` is not emitted during onboarding at all.
2. The `255 blocks 256` dependency ran **backwards**: 255's invalid-geometry outcome needs precisely
   the plumbing that had been assigned to Plan 256 T1.
3. A load-bearing factual claim about Branson's forcing was **wrong** (below).

⚠️ **Plan 256 T1 depends on this plan's T1 specifically**, not on the whole plan. T2–T4 here are
independent of 256 and can land in any order relative to it.

## Why this exists

Station onboarding returns `OnboardingResult` (`src/sapphire_flow/types/onboarding.py:7`) — fifteen
integer counters plus a flat `errors: list[str]`. `flows/onboard.py` logs those counters as one
`onboarding_flow_complete` line.

That is enough to know *how many* stations were marked operational. It is not enough to know
**which, and whether the ones that were not are a problem**. Every per-station shortfall is a log
line in a run whose logs are discarded the moment the worker container is recreated:

| condition | current handling | source | collectable today? |
|---|---|---|---|
| invalid / missing basin polygon → no MeteoSwiss binding | `log.warning(reason=…)`, reason then **discarded** | `services/reanalysis_backfill.py:119` | ❌ **no channel** — T1 creates one |
| held out for a zero-row backfill | `log.warning("onboarding.station_held_out_meteoswiss_backfill")` | `services/onboarding.py:768` | ✅ set is in scope |
| no forecast_targets → QC/baseline/regime skipped | `log.warning("station_no_forecast_targets")` ×3 | `services/onboarding.py:777, 840, 887` | ✅ |
| baselines skipped, no water-level datum | `log.info("baselines_skipped_missing_water_level_datum")` | `services/onboarding.py:858` | ✅ |
| no climatology floor → not promoted | `errors.append(…)` + `log.warning("onboarding.station_missing_climatology_floor")` | `services/onboarding.py:1219–1225` | ✅ |

⛔ **`operational_inputs.short_lookback` was in the first draft and is REMOVED.**
`services/onboarding.py` does not import `operational_inputs` — that warning is emitted during
forecast input assembly, from the forecast-cycle and hindcast paths only. There is no channel through
which it can become an onboarding outcome, and inventing one would be a forecast-time sufficiency
check wearing an onboarding plan's clothes. If an onboarding-time data-depth gate is wanted, it needs
its own plan with its own criterion.

An operator finishing an onboarding run has no artefact that answers "is this fleet ready?".

**This is not hypothetical.** On 2026-09-08 establishing the state of a 148-station fleet required
reading `flow_run_state` messages, clustering `stations.updated_at` to the microsecond, correlating
`model_artifacts.promoted_at` against a cancellation timestamp, and extracting the `stations` table
out of two 25 GB nightly `pg_dump` archives. That is forensics. It should have been a report.

⭐ **One precedent to build on rather than duplicate.** The forecast cycle already emits a
per-station, per-cycle `FORECAST_STATION_DARK` record (`flows/run_forecast_cycle.py:846`) carrying a
machine-readable reason — on staging it names exactly the five stations this plan exists for,
`critical`, `all_models_failed`, every cycle. Two lessons: **match its vocabulary**, and note that
**persisting is not surfacing** — `ops/watchdog.py` probes only three check types and that is not one
of them, so the system has been correctly reporting those stations to nobody. T3's report must be an
artefact an operator is handed, not another row to go looking for.

## What is measured (mac-mini staging, read-only, 2026-09-08 06:30–07:20 UTC)

148 stations, all `river`/`gauged`/`operational`, all with a basin carrying geometry, 516 attributes,
`area_km2` and `band_geometries`, all with `forecast_targets = ["discharge"]`, all with an
`icon_ch2_eps`/`basin_average`/FORECAST binding, and all six `model_assignments`. On the label, a
uniform fleet. The substance is five tiers:

| tier | stations | evidence |
|---|---|---|
| all 6 models trained | 36 | `model_artifacts` status=active, 6 distinct `model_id` |
| missing `persistence_fallback`, `seasonal_precip_runoff_regression` | 41 | 4 distinct active `model_id` |
| also missing `nwp_regression` | 66 | 3 distinct active `model_id` |
| one artifact, added later by a *model* onboarding run | 1 (**2041**) | sole artifact `trained_at` 2026-09-04 |
| **no model artifact of any kind, ever** | 4 (**2116, 2392, 2615, 2623**) | zero rows in `model_artifacts` |

The 36/41/66 split is the understood consequence of a deliberate mid-training cancellation on
2026-09-01. The last two rows are not: those five have no climatology floor, no `clim_baselines`, no
`flow_regime_configs`, and four have never produced a forecast. Their shared upstream cause — QC never
processed their pre-2026 observations — is Plan 256's territory.

### Branson, stated correctly

**2024 Branson** carries an invalid basin polygon
(`ST_IsValidReason` → `Ring Self-intersection[7.20127964026713 46.1757792259881]`), is excluded by
`_has_valid_geometry`, and is the only station of 148 with **no MeteoSwiss reanalysis binding and
zero `meteoswiss_*` forcing rows**. The other 147 carry six products through 2026-09-06.

🔴 **The first draft said Branson "runs on `camels-ch` forcing, which ends 2020-12-31". That is
wrong and is corrected here.** `adapters/hybrid_reanalysis_factories.py` retires the CAMELS-CH tier
(Plan 115b4 §5B): those rows "are simply never wired into this hybrid chain". Branson holds 29 220
`camels-ch` rows, but the operational resolver never reads them. The true condition is **no wired
operational reanalysis forcing at all**, not stale forcing — a different mechanism, and worse.

⚠️ **Unverified and deliberately not asserted:** whether Branson's three trained models were fitted
from `camels-ch` rows read directly by the training path, which does not go through the hybrid
resolver. Training-time versus inference-time forcing provenance is a separate question. This plan
reports the *absence of a binding and of rows*, which is measured, and claims nothing about what its
artifacts were fitted on.

## What this plan does NOT do

It does not change which stations get promoted and does not touch `update_station_status`. A run that
promotes a station today promotes it after this plan too — it just also says so. Behaviour change is
Plan 256.

## Tasks

### T1 — eligibility exclusions become typed data (shared prerequisite)

**Outcome.** `eligible_meteoswiss_configs` stops discarding why it rejected a station. A caller can
retrieve the eligible configs **and** a typed exclusion per rejected station. This is the channel
Plan 256 T1 needs to hold an invalid-geometry station and that T2 below needs to report one.

⚠️ **Preserve the existing signature; add a partitioning function beside it.** Changing the return
shape in place would touch four production call sites, eight test sites and the spec:

| call site | |
|---|---|
| `services/onboarding.py:706` | onboarding Step 4c |
| `services/reanalysis_backfill.py:161` | `bind_meteoswiss_reanalysis_fleet` |
| `scripts/backfill_meteoswiss_history.py:187` | operator script |
| `scripts/validate_forcing_reference.py:223` | operator script |
| `tests/unit/services/test_reanalysis_backfill.py` | 7 direct call sites |
| `tests/unit/scripts/test_backfill_meteoswiss_history_script.py:205` | patches the symbol |
| `docs/spec/types-and-protocols.md:3842` | authoritative signature |

The two operator scripts would silently pass the wrong object downstream. So: add
`partition_meteoswiss_eligibility(...)` returning eligible configs plus exclusions, express
`eligible_meteoswiss_configs` as a thin wrapper over it, and leave every existing caller untouched.

The exclusion reason is an `Enum` covering **every** branch the function already rejects on — not
only self-intersection: `NO_BASIN_ID`, `BASIN_NOT_FOUND`, `GEOMETRY_WRONG_TYPE`, `GEOMETRY_EMPTY`,
`GEOMETRY_INVALID`.

**In.** `src/sapphire_flow/services/reanalysis_backfill.py`, a new exclusion type,
`docs/spec/types-and-protocols.md:3842` (adds the new function; the existing signature is unchanged).
**Out.** No change to `_has_valid_geometry`'s predicate, to any existing call site, or to
`eligible_meteoswiss_configs`' return type. No geometry repair.

**Verification.** `uv run pytest tests/unit/services/test_reanalysis_backfill.py` — the seven existing
call sites pass **unmodified**, plus a new test asserting a station rejected for each of the five
reasons appears in the partition with that reason.

**Pre-change.** RED: a test asserting a self-intersecting-polygon station's exclusion reason is
retrievable fails, because the reason today exists only inside a `log.warning` call and is
unreachable by any caller. `tests/unit/services/test_onboarding.py:1347`
(`test_ineligible_station_no_geometry_gets_no_binding`) already locks the *binding* behaviour and must
continue to pass untouched — that is the discriminator between "the reason is now returnable" and
"the exclusion rule changed".

### T2 — a per-station outcome record

**Outcome.** A frozen dataclass records, per station resolved in a run, the station **id, code and
name**, the facts onboarding established (geometry validity and exclusion reason from T1, forcing
sources landed, whether QC/baselines/regime produced rows, models assigned, models with an ACTIVE
artifact, climatology floor present, held-out and why) and the promotion decision reached.

`OnboardingResult` gains `station_outcomes: tuple[StationOnboardingOutcome, ...] = ()` — **with a
default**, because `tests/unit/flows/test_onboard_flow.py:538` constructs `OnboardingResult(...)`
directly and a field without one breaks it. The fifteen counters and `errors` are unchanged.

The reason vocabulary is an `Enum` sharing `FORECAST_STATION_DARK`'s terms where they overlap. At
minimum: `INVALID_BASIN_GEOMETRY`, `NO_OPERATIONAL_FORCING`, `NO_FORECAST_TARGETS`,
`MISSING_WATER_LEVEL_DATUM`, `NO_CLIMATOLOGY_FLOOR`, `HELD_MISSING_BACKFILL`,
`MODEL_TRAINING_INCOMPLETE`.

⚠️ **Record run effects, not final state.** `onboard_model`'s `ModelOnboardingResult` is discarded
after its promoted count is taken (`services/onboarding.py:1130–1157`), and `BackfillResult` carries
only an aggregate `rows_written`. Per-station model and backfill outcomes must be *retained* here, or
the record can only describe what the database happens to hold afterwards — a different question, and
T4's job.

**In.** `src/sapphire_flow/types/onboarding.py`, `src/sapphire_flow/services/onboarding.py` (the
sites tabulated above become record-writes *in addition to* their log lines),
`docs/spec/types-and-protocols.md:2327`.
**Out.** No change to any store Protocol. No new DB table. No control-flow change.

**Verification.** `uv run pytest tests/unit/services/test_onboarding.py -k outcome` — a fake-store run
over one valid station and one invalid-geometry station returns two outcomes with the expected
reasons; the pre-existing counter assertions in that file pass unmodified; and
`tests/unit/flows/test_onboard_flow.py` passes without edit, proving the default.

**Pre-change.** RED: the same test asserting the invalid-geometry station's outcome names
`INVALID_BASIN_GEOMETRY`. An `AttributeError` on `station_outcomes` alone is a signature failure and
does **not** prove the defect — the reason assertion is what fails for the reason the gap exists.

### T3 — the report, and its delivery

**Outcome.** At the end of a run a plain-text report is written and its path logged as one structured
event. Four named sections — **Complete**, **Degraded**, **Withheld**, **Failed** — every station in
exactly one, by code and name, with its reason, plus a per-model assigned-vs-trained summary so a
mid-training cancellation is legible at a glance. Counts appear alongside names, never instead of
them.

**Both entrypoints are wired**: `flows/onboard.py` and `scripts/onboard.py`. "Every run" that skips
the CLI is not every run.

⚠️ **The destination must be decided, not assumed.** There is no run-scoped artifact directory today;
only the shared `/data/artifacts` model volume exists. This task specifies path, filename (including
collision behaviour across runs) and retention before it is implementable.

**In.** `src/sapphire_flow/services/onboarding_report.py` (new), `src/sapphire_flow/flows/onboard.py`,
`scripts/onboard.py` (call sites only), and the operational runbook covering onboarding.
**Out.** No API endpoint, no dashboard, no HTML.

**Verification.** `uv run pytest tests/unit/services/test_onboarding_report.py` for rendering — each
station code appears exactly once under the expected heading — **plus** flow- and CLI-level tests
asserting the file is created at the specified path and the path is logged. Rendering tests alone do
not verify delivery.

**Pre-change.** N/A — new module. The behavioural claim is carried by T2.

### T4 — a fleet-state snapshot for the current staging fleet

**Outcome.** A read-only collector renders the *current state* of the deployed fleet from the
database, and its output is committed under this plan so the fleet's real condition is recorded in
the repo. This is acceptance evidence that T2's vocabulary describes the conditions the fleet is in.

⚠️ **Explicitly NOT the T3 run report.** T3 reports what a run did; existing rows and artifacts are
indistinguishable from ones a given run produced, so a renderer over an `OnboardingResult` cannot
reconstruct the fleet's history after the fact. Conflating the two was a first-draft error. They
share the reason vocabulary and nothing else.

**In.** A read-only invocation against staging; the rendered snapshot committed here.
**Out.** No write of any kind to staging. No remediation — Plan 256 owns that.

**Verification.** The snapshot places 2116, 2392, 2615, 2623 and 2041 under **Degraded** with
`NO_CLIMATOLOGY_FLOOR`, places 2024 under **Degraded** with `INVALID_BASIN_GEOMETRY` and
`NO_OPERATIONAL_FORCING`, and the 36 six-model stations under **Complete** — matching the measured
table above, station code for station code.

⚠️ Those five read as *degraded*, not *withheld*, because on staging they are already `operational`.
That mismatch is the finding, not a renderer bug; Plan 256 resolves it.

**Pre-change.** N/A — verification task.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T4"], "depends_on": ["phase-3"] }
  ]
}
```
