---
status: DRAFT
created: 2026-09-08
plan: 255
title: Onboarding reports what it actually did — typed eligibility exclusions, a per-station outcome record, and a human-readable report
scope: Turn the per-station facts onboarding discovers into typed data and a report an operator is handed at the end of a run. Three layers — make eligibility exclusions returnable instead of log-only (the shared prerequisite Plan 256 T1 also needs), record one outcome per REQUESTED station, render and deliver the report from both entrypoints. Additive and observational only. Explicitly NOT a change to promotion behaviour (Plan 256), NOT a dashboard or API surface, NOT a forecast-time sufficiency check, NOT a change to what onboarding computes.
depends_on: []
blocks: []
source: 2026-09-08 — a read-only diagnosis of the mac-mini staging host found five stations marked `operational` that the promotion gate could not have passed, and one silently excluded from operational forcing. Revised twice the same day after two independent Codex passes (5 blockers + 7 majors, then 4 further blockers).
---

# Plan 255 — onboarding reports what it actually did

## Status

**DRAFT — revised after two independent review rounds.** Awaiting owner READY.

| round | outcome |
|---|---|
| Codex #1 | 5 blockers, 7 majors — two promised conditions uncollectable in scope; `255 blocks 256` backwards; a factual error about Branson's forcing |
| Codex #2 | confirmed the call-site, audit-layer, CAMELS-CH and dependency fixes; raised 4 further blockers — deferred inputs, uncollectable run effects, an unpopulatable report section, a reason-vocabulary contradiction with 256 |

⚠️ **Plan 256 T1 depends on this plan's T1 specifically**, not the whole plan.

## Why this exists

`OnboardingResult` (`src/sapphire_flow/types/onboarding.py:7`) is fifteen integer counters plus a
flat `errors: list[str]`. The Prefect flow logs those counters as one `onboarding_flow_complete`
line; the CLI additionally prints each error (`scripts/onboard.py:374`). Neither says **which**
stations fell short, or whether that matters.

Every per-station shortfall is a log line in a run whose logs are discarded the moment the worker
container is recreated:

| condition | current handling | source | collectable? |
|---|---|---|---|
| invalid / missing basin polygon → no MeteoSwiss binding | reason logged then **discarded** | `services/reanalysis_backfill.py:119` | ❌ **no channel** — T1 creates one |
| held out for a zero-row backfill | `log.warning("onboarding.station_held_out_meteoswiss_backfill")` | `services/onboarding.py:768` | ✅ |
| no forecast_targets → QC/baseline/regime skipped | `log.warning("station_no_forecast_targets")` ×3 | `services/onboarding.py:777, 840, 887` | ✅ |
| baselines skipped, no water-level datum | `log.info("baselines_skipped_missing_water_level_datum")` | `services/onboarding.py:858` | ✅ |
| no climatology floor → not promoted | `errors.append(…)` + `log.warning(…)` | `services/onboarding.py:1219–1225` | ✅ |

⛔ **`operational_inputs.short_lookback` is NOT in scope.** `services/onboarding.py` does not import
`operational_inputs`; that warning comes from forecast input assembly. An onboarding-time data-depth
gate would need its own plan and its own criterion.

**This is not hypothetical.** On 2026-09-08, establishing the state of a 148-station fleet required
`flow_run_state` messages, microsecond clustering of `stations.updated_at`, an artifact-promotion
histogram against a cancellation timestamp, and extracting `stations` from two 25 GB nightly
`pg_dump` archives. That is forensics. It should have been a report.

⭐ **Precedent to match, not duplicate.** `flows/run_forecast_cycle.py:846` already emits a
per-station `FORECAST_STATION_DARK` record with a machine-readable reason — on staging it names
exactly the five stations this plan exists for, every cycle. Reuse its vocabulary. And note that
**persisting is not surfacing**: `ops/watchdog.py` probes only three check types and that is not one,
so the system has been reporting those stations to nobody.

## What is measured (staging, read-only, 2026-09-08)

148 stations, all `operational`, all with a basin, `forecast_targets`, a FORECAST binding and six
`model_assignments`. Substance in five tiers: **36** with all six models trained · **41** missing
`persistence_fallback` + `seasonal_precip_runoff_regression` · **66** also missing `nwp_regression`
(the 36/41/66 split is a deliberate mid-training cancellation on 2026-09-01) · **1** (2041) with a
single later-added artifact · **4** (2116, 2392, 2615, 2623) with **no artifact of any kind, ever**.

**2024 Branson** carries `Ring Self-intersection[7.20127964026713 46.1757792259881]`, is excluded by
`_has_valid_geometry`, and is the only station with **no MeteoSwiss binding and zero `meteoswiss_*`
rows**. 🔴 An earlier draft said it "runs on `camels-ch` forcing ending 2020-12-31" — **wrong**.
`adapters/hybrid_reanalysis_factories.py:9` retires the CAMELS-CH tier; those rows are never wired.
The true condition is **no wired operational reanalysis forcing at all**. Whether Branson's trained
artifacts were fitted from `camels-ch` rows read directly by the training path is **unverified and
not asserted here**.

## Reason vocabulary — one derivation rule

To keep this plan and Plan 256 from disagreeing about the same station, a reason is derived from the
**most upstream** unmet condition, in this fixed order:

```
INVALID_BASIN_GEOMETRY  →  NO_OPERATIONAL_FORCING  →  NO_FORECAST_TARGETS
   →  MISSING_WATER_LEVEL_DATUM  →  NO_CLIMATOLOGY_FLOOR  →  MODEL_TRAINING_INCOMPLETE
```

A station reports exactly one primary reason. `HELD_MISSING_BACKFILL` is a promotion *disposition*,
recorded alongside the reason rather than instead of it.

⚠️ Under this rule **2392 and 2623 report `NO_CLIMATOLOGY_FLOOR`**, not
`INSUFFICIENT_OBSERVATION_HISTORY` — a review round found the two plans naming different reasons for
the same stations. There is deliberately **no** `INSUFFICIENT_OBSERVATION_HISTORY` member: onboarding
computes no history-depth threshold, so a reason implying one would assert a check that does not
exist.

## Tasks

### T1 — eligibility exclusions become typed data (shared prerequisite)

**Outcome.** A caller can retrieve the eligible MeteoSwiss configs **and** a typed exclusion per
rejected station. This is the channel Plan 256 T1 needs and that T2 needs.

⚠️ **Preserve the existing signature; add a partitioning function beside it.** Four production call
sites and eight test sites depend on the current return shape:

| `services/onboarding.py:706` | `services/reanalysis_backfill.py:161` |
|---|---|
| `scripts/backfill_meteoswiss_history.py:187` | `scripts/validate_forcing_reference.py:223` |
| `tests/unit/services/test_reanalysis_backfill.py` (7 call sites) | `tests/unit/scripts/test_backfill_meteoswiss_history_script.py:205` (patches the symbol) |

Both operator scripts would silently pass the wrong object downstream. So add
`partition_meteoswiss_eligibility(...)` returning eligible configs plus exclusions, and express
`eligible_meteoswiss_configs` as a thin wrapper over it.

The reason `Enum` covers **every** branch the function already rejects on: `NO_BASIN_ID`,
`BASIN_NOT_FOUND`, `GEOMETRY_WRONG_TYPE`, `GEOMETRY_EMPTY`, `GEOMETRY_INVALID`.

**In.** `src/sapphire_flow/services/reanalysis_backfill.py`; `docs/spec/types-and-protocols.md:3842`
(adds the new function); `docs/touchpoint-maps.md:194` (documents both binding paths).
**Out.** No change to `_has_valid_geometry`, to any existing call site, or to
`eligible_meteoswiss_configs`' return type. No geometry repair.

**Verification.** `uv run pytest tests/unit/services/test_reanalysis_backfill.py` — the seven
existing call sites pass **unmodified** — plus a new
`tests/unit/services/test_reanalysis_backfill.py::TestPartitionMeteoswissEligibility` asserting a
station rejected for each of the five reasons appears in the partition with that reason.

**Pre-change.** N/A — additive interface. The function does not exist, so a test against it fails on
name resolution, which proves nothing about the defect. The discriminating evidence is instead that
`tests/unit/services/test_onboarding.py:1347`
(`test_ineligible_station_no_geometry_gets_no_binding`) passes **unchanged** afterwards: the
exclusion rule is untouched and only the reason became reachable.

### T2 — one outcome per requested station

**Outcome.** A frozen dataclass records, for **every station the run was asked to onboard** — not
only those successfully resolved — the station id, code and name, the facts onboarding established,
and the promotion disposition.

⚠️ **Per requested station, not per resolved station.** A station whose row write fails is caught
before it enters the station map (`services/onboarding.py:462`) and so never reaches
`resolved_station_ids`. Keying outcomes on the resolved set would leave the report's **Failed**
section permanently empty — it could not represent the one case an operator most needs to see.

`OnboardingResult` gains `station_outcomes: tuple[StationOnboardingOutcome, ...] = ()` — **with a
default**, because `tests/unit/flows/test_onboard_flow.py:538` constructs `OnboardingResult(...)`
directly. The fifteen counters and `errors` are unchanged.

⚠️ **Name fields for what they actually witness.** Onboarding observes forcing via
`fetch_available_sources` *after* the backfill (`services/onboarding.py:736`), and `BackfillResult`
(`services/reanalysis_backfill.py:247`) carries only aggregate `chunks_processed` /
`chunks_skipped` / `rows_written` / `stations` — no per-station breakdown. So the field is
`forcing_sources_available_after_run`, not "landed". Recording what *this run wrote* per station
would require extending `BackfillResult`, which is out of scope here.

**In.** `src/sapphire_flow/types/onboarding.py`, `src/sapphire_flow/services/onboarding.py`,
`docs/spec/types-and-protocols.md:2327`.
**Out.** No change to any store Protocol, no new DB table, no control-flow change, no change to
`reanalysis_backfill.py`'s result type.

**Verification.** `uv run pytest tests/unit/services/test_onboarding.py::TestStationOutcomes` — a
fake-store run over one valid station, one invalid-geometry station and one whose write raises
returns **three** outcomes with the expected reasons and dispositions; the pre-existing counter
assertions in that file pass unmodified; `uv run pytest tests/unit/flows/test_onboard_flow.py`
passes without edit, proving the default.

**Pre-change.** N/A for the field's existence — additive. The discriminating claim is the
**failed-station** case: assert that a station whose row write raises still appears in the result.
Today it appears nowhere except as a string in `errors`, so that assertion fails on substance, not on
a missing attribute.

### T3 — the report, and its delivery

**Outcome.** At the end of a run a plain-text report is written and its path logged as one structured
event. Four sections — **Complete**, **Degraded**, **Withheld**, **Failed** — every requested station
in exactly one, by code and name, with its primary reason, plus a per-model assigned-vs-trained
summary. Counts appear alongside names, never instead of them.

**Destination, decided here** (a review round flagged this as deferred):

| | |
|---|---|
| directory | `resolve_data_dir(config.paths_data_dir) / "reports" / "onboarding"`, created if absent |
| filename | `onboarding-<clock() as %Y%m%dT%H%M%SZ>.txt` — the run's injected clock, so two runs never collide and tests are deterministic |
| collision | if the path exists, suffix `-2`, `-3`, … rather than overwrite |
| retention | none — files are small and text; pruning is a deployment concern, not this plan's |
| test seam | the directory is a parameter defaulting to the resolved path, so tests write to `tmp_path` |

**Both entrypoints are wired**: `flows/onboard.py` and `scripts/onboard.py`. "Every run" that skips
the CLI is not every run.

**In.** `src/sapphire_flow/services/onboarding_report.py` (new), `src/sapphire_flow/flows/onboard.py`,
`scripts/onboard.py` (call sites only), `docs/v0-scope.md:70` (§A4 documents onboarding).
**Out.** No API endpoint, no dashboard, no HTML, no retention/pruning policy.

**Verification.**
`uv run pytest tests/unit/services/test_onboarding_report.py` — rendering: each station code appears
exactly once under the expected heading; and
`uv run pytest tests/unit/flows/test_onboard_flow.py::TestOnboardingReportDelivery` plus
`tests/unit/scripts/test_onboard_script.py::TestOnboardingReportDelivery` — delivery: with the
directory pointed at `tmp_path`, the file exists at the specified name and the logged event carries
its path. Rendering tests alone do not verify delivery.

**Pre-change.** N/A — new module; the behavioural claim is carried by T2.

### T4 — a fleet-state snapshot of the current staging fleet

**Outcome.** A read-only collector renders the fleet's *current state* from the database; its output
is committed here so the fleet's condition is recorded in the repo.

⚠️ **NOT the T3 run report.** T3 reports what a run did. Existing rows and artifacts are
indistinguishable from ones a given run produced, so an `OnboardingResult` renderer cannot
reconstruct history after the fact. They share the reason vocabulary and nothing else.

**In.** `scripts/fleet_state_report.py` (new, read-only); its output committed under this plan.
**Out.** No write of any kind to staging. No remediation — Plan 256 and Plan 258 own that.

**Verification.**
`uv run python scripts/fleet_state_report.py --out docs/plans/255-fleet-state-2026-09-08.txt`
run against staging, and the committed output places 2116, 2392, 2615, 2623 and 2041 under
**Degraded** with `NO_CLIMATOLOGY_FLOOR`, 2024 under **Degraded** with `INVALID_BASIN_GEOMETRY`, and
the 36 six-model stations under **Complete** — matching the measured tiers above, code for code.

⚠️ Those five read as *degraded*, not *withheld*, because on staging they are already `operational`.
That mismatch is the finding, not a renderer bug.

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
