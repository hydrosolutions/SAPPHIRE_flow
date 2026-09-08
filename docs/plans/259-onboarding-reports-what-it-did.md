---
status: DRAFT
created: 2026-09-08
plan: 259
title: Onboarding reports what it actually did — a per-station outcome record and a delivered report
scope: Record one outcome per station an onboarding run was asked to handle, and deliver a human-readable report from both entrypoints. Explicitly NOT a change to promotion behaviour (Plan 256), NOT a change to what onboarding computes, NOT a permanent fleet-state collector, NOT a dashboard or API surface, NOT a forecast-time sufficiency check.
depends_on: [255]
blocks: []
source: 2026-09-08 — split out of the original Plan 255 after three independent Codex rounds, so its unresolved report-destination question stops blocking Plan 256. Carries the outcome record, the report, and a one-time fleet evidence snapshot.
---

# Plan 259 — onboarding reports what it actually did

## Status

**DRAFT.** Awaiting owner READY. Depends on **Plan 255** for the typed exclusion channel.

⚠️ **One decision is provisional and the owner should confirm it** — the report destination (T2
below). The recommendation needs no infrastructure change; the alternative does.

## Why this exists

`OnboardingResult` (`src/sapphire_flow/types/onboarding.py:7`) is fifteen integer counters plus a
flat `errors: list[str]`. The Prefect flow logs the counters as one `onboarding_flow_complete` line;
the CLI additionally prints each error (`scripts/onboard.py:374`). Neither says **which** stations
fell short, or whether that matters.

Every per-station shortfall is a log line in a run whose logs are discarded the moment the worker
container is recreated:

| condition | current handling | source |
|---|---|---|
| invalid / missing basin polygon | reason discarded — **Plan 255 T1 creates the channel** | `services/reanalysis_backfill.py:119` |
| held out for a zero-row backfill | `log.warning("onboarding.station_held_out_meteoswiss_backfill")` | `services/onboarding.py:768` |
| no forecast_targets → QC/baseline/regime skipped | `log.warning("station_no_forecast_targets")` ×3 | `services/onboarding.py:777, 840, 887` |
| baselines skipped, no water-level datum | `log.info("baselines_skipped_missing_water_level_datum")` | `services/onboarding.py:858` |
| no climatology floor → not promoted | `errors.append(…)` + `log.warning(…)` | `services/onboarding.py:1219–1225` |

⛔ **`operational_inputs.short_lookback` is NOT in scope.** `services/onboarding.py` does not import
`operational_inputs`; that warning comes from forecast input assembly. An onboarding-time data-depth
gate needs its own plan and its own criterion.

**This is not hypothetical.** On 2026-09-08, establishing the state of a 148-station fleet required
`flow_run_state` messages, microsecond clustering of `stations.updated_at`, an artifact-promotion
histogram against a cancellation timestamp, and extracting `stations` from two 25 GB nightly
`pg_dump` archives. That is forensics. It should have been a report.

⭐ **Precedent to match, not duplicate.** `flows/run_forecast_cycle.py:846` already emits a
per-station `FORECAST_STATION_DARK` record with a machine-readable reason — on staging it names
exactly the stations this plan exists for, every cycle. Reuse its vocabulary. And note that
**persisting is not surfacing**: `ops/watchdog.py` probes only three check types and that is not one,
so the system has been reporting those stations to nobody. The report must be handed to an operator,
not filed where no one looks.

## Reason vocabulary — one derivation rule

Shared with Plan 256 so the two never name different reasons for the same station. A station reports
exactly one **primary** reason, the most upstream unmet condition:

```
INVALID_BASIN_GEOMETRY → NO_OPERATIONAL_FORCING → NO_FORECAST_TARGETS
  → MISSING_WATER_LEVEL_DATUM → NO_CLIMATOLOGY_FLOOR → MODEL_TRAINING_INCOMPLETE
```

`HELD_MISSING_BACKFILL` is a promotion *disposition*, recorded alongside the reason. Execution
failures use a separate typed field (T1) and never a primary reason — a station that failed to be
written has no condition to report.

⚠️ There is deliberately **no** `INSUFFICIENT_OBSERVATION_HISTORY` member: onboarding computes no
history-depth threshold, so the reason would assert a check that does not exist. Under this rule
staging's 2392 and 2623 report `NO_CLIMATOLOGY_FLOOR`.

## Tasks

### T1 — one outcome per station the run was asked to handle

**Outcome.** A frozen dataclass records, for **every station the run was asked to onboard**, the
station id (where allocated), code, name, the facts onboarding established, the promotion
disposition, and — separately — any execution failure.

⚠️ **Keying on `resolved_station_ids` is not sufficient, and the reason is subtler than it looks.**
For a **new** station a failed write is caught before the station enters `station_map`
(`services/onboarding.py:462`). But for an **existing** station the map is populated at `:504`
*before* `_write_station_with_audit(..., is_update=True)` runs at `:510` — so a failed *update*
leaves the station in the map and looks resolved. Outcomes must therefore be tracked independently of
`station_map`, covering both a failed insert and a failed update.

⚠️ **Calculated stations enter separately** (`services/onboarding.py:547`, `if calculated_specs:`) and
can fail before an id is allocated (`services/calculated_station_onboarding.py:166`). The record must
admit an outcome with no station id, or this task's promise of "every requested station" is false for
them.

⚠️ **Name fields for what they witness.** Onboarding observes forcing via `fetch_available_sources`
*after* the backfill (`services/onboarding.py:736`), and `BackfillResult`
(`services/reanalysis_backfill.py:247`) carries only aggregate counts — no per-station breakdown. The
field is `forcing_sources_available_after_run`, not "landed".

`OnboardingResult` gains `station_outcomes: tuple[StationOnboardingOutcome, ...] = ()` — **with a
default**, because `tests/unit/flows/test_onboard_flow.py:538` constructs it directly. The fifteen
counters and `errors` are unchanged.

**Per-model fields are named explicitly**, with their temporal meaning, because the report promises an
assigned-vs-trained summary and today only aggregate counters exist
(`services/onboarding.py:1243`): `models_assigned_this_run: frozenset[ModelId]` and
`models_with_active_artifact_after_run: frozenset[ModelId]`. The first is a run effect; the second is
after-run state. They are not interchangeable and the report must not present them as one number.

**In.** `src/sapphire_flow/types/onboarding.py`, `src/sapphire_flow/services/onboarding.py`,
`docs/spec/types-and-protocols.md:2327`.
**Out.** No change to any store Protocol, no new DB table, no control-flow change, no change to
`BackfillResult`.

**Verification.** `uv run pytest tests/unit/services/test_onboarding.py::TestStationOutcomes` — a
fake-store run over five inputs returns five outcomes: a healthy station; an invalid-geometry station
naming `INVALID_BASIN_GEOMETRY`; a **new** station whose insert raises; an **existing** station whose
update raises; and a calculated spec that fails before id allocation. Pre-existing counter assertions
in that file pass unmodified, and `uv run pytest tests/unit/flows/test_onboard_flow.py` passes without
edit, proving the default.

**Pre-change.** RED, and it must be the **failed-update** case specifically: assert that a station
whose *update* raises appears in the result with an execution failure. Today it appears nowhere but as
a string in `errors`, *and* it is present in `station_map`, so a naive resolved-set implementation
would report it as a success. That is the discriminating failure. An `AttributeError` on
`station_outcomes` proves nothing and does not count.

### T2 — the report, and its delivery

**Outcome.** At the end of a run a plain-text report is written and its path logged as one structured
event. Four sections — **Complete**, **Degraded**, **Withheld**, **Failed** — every requested station
in exactly one, by code and name, with its primary reason or execution failure, plus a per-model
summary printing `models_assigned_this_run` and `models_with_active_artifact_after_run` as two
distinct figures. Counts appear alongside names, never instead of them.

**Destination — provisional, owner to confirm:**

🔴 **The obvious choice does not work.** The worker root is `read_only: true`
(`docker-compose.yml:122`); only `/data/artifacts`, `/data/nwp_grids`, `/data/bafu_forecasts` and
`/data/bafu_observations` are durable and writable, plus tmpfs `/tmp`, `/data/cache` and
`/tmp/sapphire_nwp`. A new `/data/reports` path would fail in production **while every `tmp_path`
test passed** — the failure mode this repository has been bitten by before.

| | |
|---|---|
| **recommended** | `/data/artifacts/onboarding-reports/` — already mounted `rw`, no Compose, security or CI/CD change |
| alternative | a new named volume `onboarding_reports:/data/reports:rw`, which additionally requires updates to `docker-compose.yml`, `docs/standards/cicd.md`, `docs/standards/security.md`, and a mount-contract test |
| filename | `onboarding-<clock() as %Y%m%dT%H%M%SZ>.txt` — the run's injected clock, so runs never collide and tests are deterministic |
| collision | suffix `-2`, `-3`, … rather than overwrite |
| retention | none; pruning is a deployment concern |
| test seam | the directory is a parameter defaulting to the resolved path, so tests write to `tmp_path` |

**Both entrypoints are wired**: `flows/onboard.py` and `scripts/onboard.py`.

**In.** `src/sapphire_flow/services/onboarding_report.py` (new), `src/sapphire_flow/flows/onboard.py`,
`scripts/onboard.py` (call sites only), `docs/v0-scope.md:70` (§A4 documents onboarding).
**Out.** No API endpoint, no dashboard, no HTML, no retention policy, no new volume unless the owner
picks the alternative.

**Verification.**
`uv run pytest tests/unit/services/test_onboarding_report.py` — rendering: each station code appears
exactly once under the expected heading, **and** a station with three assigned models but one trained
artifact renders both figures distinctly, so omitting or swapping them fails; plus
`uv run pytest tests/unit/flows/test_onboard_flow.py::TestOnboardingReportDelivery` and
`tests/unit/scripts/test_onboard_script.py::TestOnboardingReportDelivery` — delivery: with the
directory pointed at `tmp_path`, the file exists at the specified name and the logged event carries
its path. Rendering tests alone do not verify delivery.

**Pre-change.** N/A — new module. The behavioural claim is carried by T1.

### T3 — one-time fleet evidence

**Outcome.** The current state of the staging fleet is rendered once and committed here as
acceptance evidence that T1's vocabulary describes the conditions the fleet is actually in.

⚠️ **A one-time analysis, not a permanent collector.** An earlier draft proposed a
`scripts/fleet_state_report.py`, which a review round correctly called scope creep — a permanent,
separately-tested module for a single question. Per `CLAUDE.md` §Ad-hoc Analyses this is a
`uv run python3 << 'EOF'` heredoc against staging, read-only, whose **output** is the deliverable.

⚠️ **Not the T2 run report.** T2 reports what a run did; existing rows are indistinguishable from
ones a given run produced, so an `OnboardingResult` renderer cannot reconstruct fleet history after
the fact. These share the reason vocabulary and nothing else.

**In.** The rendered output, committed as `docs/plans/259-fleet-state-2026-09-08.txt`.
**Out.** No new module under `scripts/` or `src/`. No write of any kind to staging.

**Verification.** The committed output places 2116, 2392, 2615, 2623 and 2041 under **Degraded** with
`NO_CLIMATOLOGY_FLOOR`, 2024 under **Degraded** with `INVALID_BASIN_GEOMETRY`, and the 36 six-model
stations under **Complete** — matching the tiers measured on 2026-09-08 (36 / 41 / 66 / 1 / 4), code
for code.

⚠️ Those five read as *degraded*, not *withheld*, because on staging they are already `operational`.
That mismatch is the finding, not a renderer bug.

**Pre-change.** N/A — evidence task.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
