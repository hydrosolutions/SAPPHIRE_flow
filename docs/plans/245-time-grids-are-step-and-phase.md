---
status: DRAFT
created: 2026-09-04
plan: 245
title: A time grid is a step AND a phase — and every input is period-ending
scope: Make the phase of a time series a first-class, declared property rather than an assumption; declare period-ending as the repo-wide input convention and enforce it at the adapter boundary; forbid implicit alignment between grids that do not share a phase. Explicitly NOT the daily-model anchoring fix (Plan 226), NOT threading declared aggregation (Plan 234), NOT building any Nepali adapter.
depends_on: []
blocks: []
source: 2026-09-04 — owner raised NPT (UTC+5:45) while reviewing Plan 242; investigation showed the codebase treats a time step as a scalar throughout
---

# Plan 245 — a time grid is a step and a phase

## Status

**DRAFT — not reviewed.**

## The problem in one number

Nepal Time is **UTC+05:45**. An hourly NPT grid and an hourly UTC grid therefore **never share a
timestamp**: Nepali `HH:00` falls on UTC `:15` past the hour. A Nepali calendar day runs
**18:15 UTC → 18:15 UTC**.

Forcing arrives hourly in UTC. Some Nepali observations will arrive hourly in NPT. Both are "hourly".
Neither can be compared to the other without an explicit conversion, and nothing in the codebase
currently records enough to know that.

**Converting to UTC does not solve this**, which is the trap worth naming up front. Conversion
relabels instants; it does not move them onto a grid. After conversion one series still sits on `:00`
and the other on `:15` — and the offset is now *invisible*, because everything is nominally UTC.

## What the code actually does today

- **A time step is a scalar everywhere.** `QcRuleSet.rules_for` matches `time_step` by equality
  (`types/domain.py:160-166`); `store/forecast_store.py` derives `native_step_seconds` from the first
  pair on readback; `services/forecast_combination.py:458` derives one uniform step. Two series can
  both report `3600` and share no instant.
- **`stations.timezone` is inert.** It is declared as an IANA zone (`Asia/Kathmandu`,
  `Europe/Zurich`), carried faithfully from `config/onboarding.py:131` to `store/station_store.py:136`
  to `api/routes/api_stations.py:220` — and **never read to make a decision**. The metadata slot
  exists and does nothing.
- **The period convention is settled per-dataset, not repo-wide.** M-D3 established the DHM
  precipitation workbook as period-ending, aligning directly with ERA5-Land
  (`docs/design/dhm-precipitation-milestones.md:119`). But Pyramid AWS precipitation is **NPT**
  (`docs/design/dhm-precipitation-vision.md:354`). Both timebases are already in the building, and
  the convention is recorded in a research design doc rather than anywhere an adapter author would
  look.

## Owner decisions

**OD-1 — every input series is expected in the PERIOD-ENDING scheme.** A value stamped `16:00` is the
quantity for `15:00 → 16:00`. This is now the repo-wide convention for ingested data, not a
per-dataset finding. It matches what M-D3 already established for DHM and ERA5-Land, so it ratifies
existing practice rather than changing it.

Two consequences, and they are the point of writing it down:
- An adapter for a source that publishes **period-beginning** must convert at the boundary and
  record that it did. It must not pass the timestamps through and leave the discrepancy to be
  discovered downstream as a one-hour bias.
- A source whose convention is **unknown** is not ingested on an assumption. It is either resolved
  with the provider or the series is marked as carrying an unresolved ±1 step phase uncertainty.

⛔ **Period convention and timezone phase are INDEPENDENT.** Getting the 45 minutes right and the
labelling convention wrong yields a silent one-hour error stacked on the offset. They must be
recorded separately, never conflated into one "offset" field.

## Design

**A grid is `(step, phase)`.** Phase is the offset of the grid's marks from UTC midnight, as a
`timedelta` in `[0, step)`. Hourly UTC is `(3600s, 0)`. Hourly NPT is `(3600s, 900s)`. Daily UTC is
`(86400s, 0)`. Daily Nepali is `(86400s, 65700s)` — 18:15 UTC.

**Storage stays UTC**; `UtcDatetime` and `ensure_utc()` at boundaries are unchanged. Phase is
recorded *alongside*, not instead.

**Alignment is permitted only between identical grids.** Anything else requires an explicit,
declared resample using the aggregation method the parameter already carries
(`AggregationMethod`, `types/enums.py:178` — SUM for precipitation and reference ET, MEAN for state
variables). Shifting a series to make it fit is forbidden: a 15-minute nudge silently redistributes
accumulated precipitation.

**Fail closed.** An undeclared phase, or a grid mismatch with no declared conversion, refuses. It does
not guess, and it does not fall back to matching on step alone — which would be exactly the
nearest-match trap Plan 242's review identified as *dimensionally wrong*: a rate-of-change threshold
per 10 minutes is not that threshold per day.

**For Nepal specifically:** forcing stays hourly UTC as delivered. NPT-hourly observations keep phase
`900s` and are resampled onto whatever grid a consumer needs, never shifted. **Daily products and
skill evaluation anchor to Nepali midnight**, because a forecast DHM publishes for a given date means
the Nepali calendar day, not the UTC one.

## Relationship to plans already in flight

This is deliberately the *convention and the type*, not the consumers:

- **Plan 226** anchors the daily models' `valid_time` to the calendar day they predict. That is the
  same defect family — a grid whose phase was never declared — but 226 owns the daily-model fix.
- **Plan 234** threads each channel's declared aggregation method end to end. This plan supplies the
  rule that says *when* a resample is required; 234 supplies *how* it is performed.
- **Plan 242** found the fail-closed hole in QC rule lookup. The principle is identical and should be
  stated once, here, rather than rediscovered per subsystem.

## Non-goals

- The daily-model anchoring fix (Plan 226).
- Threading declared aggregation through the assembly paths (Plan 234).
- Building any DHM or Nepali adapter.
- Retrofitting phase onto historical stored rows. New writes declare it; a backfill is a separate
  decision with its own cost.
- Changing `UtcDatetime` or the storage timezone.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:378-390`).

### T1 — declare both conventions where an adapter author will find them

**Outcome:** period-ending is stated as the repo-wide input convention with its two consequences, and
phase is defined as a first-class grid property, in the documents an adapter author actually reads.

**In:** `docs/conventions.md` (the primary home), cross-referenced from
`docs/architecture-context.md` § data flows and `docs/spec/types-and-protocols.md`. Must state that
period convention and timezone phase are independent, and give the NPT worked example.

**Out:** any code change; any per-source audit of existing adapters (that is T4).

**Pre-change:** N/A — documentation task. `grep -rn "period-ending" docs/conventions.md docs/architecture-context.md` returns nothing today; the convention exists only in `docs/design/dhm-precipitation-milestones.md:119`, a research design doc.

**Verification:** N/A — documentation task. The convention must be stated with its NPT worked example and both consequences.

### T2 — a typed `TimeGrid`

**Outcome:** `TimeGrid(step: timedelta, phase: timedelta)` exists as a frozen dataclass with
`__post_init__` enforcing `0 <= phase < step`, plus a constructor deriving phase from an IANA
timezone and a step, and a predicate for whether two grids are alignable.

**In:** `src/sapphire_flow/types/` (a new module or `domain.py`), and
`docs/spec/types-and-protocols.md`. Depends on T1.

**Out:** changing any existing signature to take it. Adoption is T3.

**Pre-change:** `uv run python -c "from sapphire_flow.types.domain import TimeGrid"` fails with ImportError — no such type exists.

**Verification:** `uv run pytest tests/unit/types/test_time_grid.py` — hourly UTC and hourly NPT are both step 3600 and are NOT alignable; `Asia/Kathmandu` at daily yields phase 65700s (18:15 UTC); phase >= step and negative phase both raise; the Swiss `Europe/Zurich` daily case is exercised for a whole-hour zone.

### T3 — make the station timezone load-bearing

**Outcome:** `stations.timezone` is read to derive a station's grid phase, instead of being carried
and ignored.

**In:** the observation and forcing boundary where a series' grid is established; `types/station.py`.
Depends on T2.

**Out:** the daily-model anchoring fix (Plan 226) and the aggregation threading (Plan 234).

**Pre-change:** `grep -rn "\.timezone" --include=*.py src/sapphire_flow | grep -v "timezone.utc\|datetime.timezone"` shows the field only being copied between layers (`config/onboarding.py:131`, `store/station_store.py:136`, `api/routes/api_stations.py:220`, `services/calculated_station_onboarding.py:102`) and never read to make a decision.

**Verification:** `uv run pytest tests/unit/types/test_station.py tests/unit/services/` — a station in `Asia/Kathmandu` yields a different daily grid phase from one in `Europe/Zurich`, and the value is used rather than merely stored.

### T4 — audit every existing input adapter against the convention

**Outcome:** each adapter's period convention and grid phase are recorded — confirmed, converted, or
flagged unresolved. No adapter is left implicit.

**In:** the adapters under `src/sapphire_flow/adapters/`, recorded in `docs/touchpoint-maps.md` or a
table in `docs/conventions.md`. Depends on T1.

**Out:** fixing an adapter found to be non-conforming — each becomes its own change, so that a
conversion is never bundled with the audit that found it.

**Pre-change:** N/A — audit task. No such record exists; `docs/design/dhm-precipitation-milestones.md:119` covers the DHM workbook and ERA5-Land only.

**Verification:** N/A — audit task. Every adapter appears in the table with a cited source for its convention, or is explicitly marked unresolved.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py --inspect-json docs/plans/245-time-grids-are-step-and-phase.md
```

Three conditions hold in addition:

1. **No code path aligns two series on `step` alone.** The failure mode this plan exists to prevent
   is two "hourly" series being treated as comparable when they share no instant.
2. **No implicit shift.** A grid mismatch is resolved by a declared resample or refused. A test must
   lock the refusal, not only the success path.
3. **The convention is stated where an adapter author reads, not only in a research design doc.**

## Dependency graph

```json
{
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 1, "depends_on": ["T1"]},
    {"id": "T3", "phase": 2, "depends_on": ["T2"]},
    {"id": "T4", "phase": 2, "depends_on": ["T1"]}
  ]
}
```
