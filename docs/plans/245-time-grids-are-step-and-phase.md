---
status: DRAFT
created: 2026-09-04
plan: 245
title: A time grid is a step AND a phase — and every input is period-ending
scope: Make the phase of a time series a first-class declared property; declare period-ending as the repo-wide input convention; make the preparation step phase-aware with per-parameter interpolation, a refusal on upsampling and degradation flagged through the existing input-quality channel; and publish explicit interval bounds. Explicitly NOT the daily-model anchoring fix (Plan 226), NOT threading declared aggregation (Plan 234), NOT building any Nepali adapter, NOT API read-time local-day aggregation.
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

*Taken in a grill-me session on 2026-09-04. Every number below was computed, not asserted;
the commands are in the task verifications.*

**OD-0 — the target grid is `(step, phase)`; the SOURCE is not assumed to be a grid at all.**
Observations arrive at whatever cadence and phase the provider sends — 10-minute on `:02` marks,
15-minute, hourly, or three manual readings a day at 08:00/12:00/16:00 with 4 h/4 h/16 h spacing.
Ingest stores native instants in UTC and grids nothing. The store already behaves this way (Swiss
observations today carry `:05` and `:55` marks alongside the `:00/:10/:20` majority), so this
ratifies existing behaviour rather than changing it. All grid difficulty belongs to ONE component:
the preparation step that maps an arbitrary series onto a declared target grid.

**OD-2 — sub-daily runs on UTC phase; daily runs on a local-anchored phase.** These are different
products, not an inconsistency: a daily forecast *means* a civil day, an hourly one does not. Keeping
sub-daily on UTC means the hourly forcing is consumed exactly as delivered and only instantaneous
observations are interpolated — the benign direction. Concretely:

| Product | Grid | As UTC |
|---|---|---|
| Nepal daily | `(86400 s, 64800 s)` | 18:00Z → 18:00Z |
| Nepal sub-daily | `(3600 s, 0)` | UTC hours, matching forcing exactly |
| Swiss daily | `(86400 s, 0)` | 00:00Z → 00:00Z, unchanged |

⛔ **A DST-observing zone has no uniform civil-day grid at all.** Measured: Zurich civil days run 23,
24 or 25 hours (29 Mar 2026 is 23:00Z→22:00Z = 23 h; 25 Oct is 22:00Z→23:00Z = 25 h). A "day" that is
not 86400 s is not a step, so no amount of phase bookkeeping rescues it. Nepal has **no DST** —
`Asia/Kathmandu` is UTC+05:45 for all twelve months — so its civil day *is* a uniform grid. This is
why the phase is declared per deployment and never derived from a timezone: derivation would give
18:15 for Nepal (ignoring OD-3) and something broken for Switzerland.

**OD-3 — a daily bucket is whole UTC hours: 18:00Z→18:00Z, not the exact civil 18:15Z.** The forcing
is hourly, so an exact civil day would require apportioning one hourly precipitation accumulation
across the boundary **every single day**, under an assumption of uniform rainfall within that hour —
the least safe assumption available for convective rain. The whole-hour bucket introduces **no
assumption at all**: every value is used as delivered. The cost is a documented 15-minute
displacement (a Nepali "day" runs 00:45–23:45 local), which is 0.6% of the day and is stated on the
label rather than discovered. The shift is uniform, so consecutive days partition the timeline
exactly — no gaps, no overlaps, no drift, no value counted twice.

**OD-4 — forecast `valid_time` is period-ending**, consistent with OD-1. The daily bucket above is
stamped `18:00Z` on its closing day. The tempting alternative — stamping "the date it is about",
e.g. `2026-09-05 00:00Z` — is rejected: that instant is *inside* the window it labels and corresponds
to no boundary, which is precisely the defect Plan 226 exists to correct.

**OD-5 — published data carries explicit interval bounds, not just a stamp.** A consumer receiving
`2026-09-05 18:00Z` alone must know our rounding rule, our period convention and our timezone
reasoning to interpret it. The same value carrying `period_start` and `period_end` requires them to
know nothing. This follows CF conventions and netCDF, which attach `bounds` to every value for
exactly this reason, and it makes OD-3's 15-minute displacement self-describing. It also
future-proofs OD-3: moving to exact civil days later would need no consumer change.

**OD-6 — resampling: coarsen freely, interpolate with a per-parameter method, never upsample.**

| Case | Rule |
|---|---|
| Target coarser than source | Aggregate with the parameter's declared `AggregationMethod` (SUM for accumulations, MEAN for state variables) |
| Instantaneous variable onto off-phase marks | Linear interpolation between bracketing observations, subject to a maximum gap — beyond it emit nothing, never a straight line across a two-day hole |
| Accumulation onto a straddling boundary | Apportion by overlap fraction; flag as degraded when the split interval exceeds **15 minutes** |
| Target finer than the source's median spacing | **Refuse.** Three readings a day cannot become 24 hourly values; that is invention, not interpolation |

The method is determined by the parameter, never by the caller. Degradation is reported through the
existing `InputQualityFlag` channel, which Plan 242 made persistent and API-visible — no second
mechanism.

**Why 15 minutes is the threshold, and why phase matters more than length.** Measured straddling
rates per day:

| Source | vs UTC hours | vs Nepali hours |
|---|---|---|
| 10-min on `:00` | 0 / 144 | **24 / 144** |
| 10-min on `:02` | 24 / 144 | 24 / 144 |
| **15-min on `:00`** | **0 / 96** | **0 / 96** |
| hourly on `:00` | 0 / 24 | all |

A 15-minute source on quarter-hour marks splits **nothing**, against either grid — because 15 divides
the 345-minute NPT offset exactly (23 × 15). A 10-minute source does not, even on perfect `:00`
marks, because 10 does not. **This is what to request from DHM: 15-minute data on `:00/:15/:30/:45`.**
Not "sub-hourly", and specifically not 10-minute, which is worse than 15 in a way no one would guess.

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

**Outcome:** period-ending is stated as the repo-wide input convention with its two consequences;
phase is defined as a grid property; and the DST limitation is written down as a limitation.

**In:** `docs/conventions.md` (primary home), cross-referenced from `docs/architecture-context.md`
§ data flows and `docs/spec/types-and-protocols.md`. Must carry the NPT worked example (a Nepali day
is 18:00Z→18:00Z covering 00:45–23:45 local), state that period convention and timezone phase are
independent, and record that a DST-observing zone has no uniform civil-day grid.

**Out:** any code change; the per-adapter audit (T6).

**Pre-change:** N/A — documentation task. `grep -rn "period-ending" docs/conventions.md docs/architecture-context.md` returns nothing; the convention exists only in `docs/design/dhm-precipitation-milestones.md:119`, a research design doc no adapter author reads.

**Verification:** N/A — documentation task. The convention appears with its worked example and both consequences, and the DST limitation is stated.

### T2 — a typed `TimeGrid`

**Outcome:** `TimeGrid(step, phase)` exists as a frozen dataclass enforcing `0 <= phase < step`, with
a predicate for whether two grids are alignable and a helper for whether one nests into another.

**In:** `src/sapphire_flow/types/` and `docs/spec/types-and-protocols.md`. Depends on T1.

**Out:** deriving phase from an IANA timezone — explicitly not offered, since derivation is what
would reintroduce DST. Changing existing signatures to take it (T3).

**Pre-change:** `uv run python -c "from sapphire_flow.types.domain import TimeGrid"` fails with ImportError.

**Verification:** `uv run pytest tests/unit/types/test_time_grid.py` — hourly UTC and hourly Nepali are both step 3600 and NOT alignable; a 15-minute grid on quarter-hour marks nests into both, a 10-minute grid nests into neither; phase >= step and negative phase both raise.

### T3 — declare the operational grid phase in deployment config

**Outcome:** the daily grid origin is an explicit, readable declaration in deployment config
(`daily_grid_origin = "18:00"` in the Nepal overlay), parsed once into a phase.

**In:** `config/overlays/` and the deployment config model. Written as a time-of-day, not a duration,
so a reviewer can check OD-3's rounding judgement at a glance — `64800` hides exactly the decision
that should be visible. Parsed to a `timedelta` at the config boundary per CLAUDE.md's
parse-don't-validate rule. Depends on T2.

**Out:** deriving it from `stations.timezone`, which stays descriptive metadata for display. Any
per-station or per-model override — one deployment value covers both Nepal tenants.

**Pre-change:** `grep -rn "grid_origin\|grid_phase" config/ src/sapphire_flow/config/` returns nothing; no deployment declares a grid phase, and `stations.timezone` is carried through four layers without being read.

**Verification:** `uv run pytest tests/unit/config/` — the Nepal overlay parses `"18:00"` to a 64800 s phase; a Swiss overlay with no declaration defaults to phase 0; a value not a whole multiple of the step's resolution is rejected.

### T4 — make the preparation step phase-aware

**Outcome:** an arbitrary source series is mapped onto a declared target grid by the OD-6 rules —
coarsen, interpolate, or refuse — with degradation flagged.

**In:** `services/training_data.py:225` `resample_to_time_step`, which currently buckets with
`group_by_dynamic(every=...)` and no `offset`, so every bucket is epoch-aligned. polars supplies
`offset`, `closed` and `label`, so this is a parameter change plus the interpolation and refusal
logic, not a rewrite. Also its three callers: `services/hindcast.py:229`,
`services/operational_inputs.py`, `services/skill/service.py:278`. Degradation is reported via the
existing `InputQualityFlag`. Depends on T2, T3.

**Out:** the daily-model anchoring fix (Plan 226) and aggregation threading (Plan 234). Changing
`AggregationMethod` itself.

**Pre-change:** `grep -n "group_by_dynamic" services/training_data.py` shows `every=` with no `offset`, and the docstring states buckets are epoch-aligned by Plan 228 D4 — so a Nepali-phased series is silently re-bucketed onto UTC marks, which is a 15-minute shift presented as a resample.

**Verification:** `uv run pytest tests/unit/services/test_training_data.py` — a 15-minute source on quarter-hour marks maps onto BOTH a UTC-hourly and a Nepali-hourly target with zero apportionment; an accumulation straddling a boundary is apportioned and flagged only when the split interval exceeds 15 minutes; an instantaneous series is interpolated but not across a gap beyond the maximum; and a target finer than the source's median spacing is REFUSED, with the refusal locked by a test rather than only the success path.

### T5 — publish explicit interval bounds

**Outcome:** a published value carries `period_start` and `period_end`, so a consumer needs to know
none of our conventions to interpret it.

**In:** the API forecast schemas and the Forecast Lab snapshot. Follows CF/netCDF practice, which
attaches bounds to every value for this reason. Depends on T2.

**Out:** API read-time local-day aggregation — deferred by the owner; this task makes it possible
later without a consumer change, which is the point. Changing any stored `valid_time`.

**Pre-change:** an API forecast response carries `valid_time` alone, so the 15-minute displacement in OD-3 is only discoverable from documentation.

**Verification:** `uv run pytest tests/unit/api/` — a daily Nepal forecast response carries bounds of 18:00Z to 18:00Z spanning exactly 24 h, and the bounds are consistent with the period-ending stamp.

### T6 — audit every existing input adapter against the convention

**Outcome:** each adapter's period convention and native phase are recorded — confirmed, converted,
or flagged unresolved.

**In:** the adapters under `src/sapphire_flow/adapters/`, recorded as a table in
`docs/conventions.md`. Depends on T1.

**Out:** fixing a non-conforming adapter — each becomes its own change, so a conversion is never
bundled with the audit that found it.

**Pre-change:** N/A — audit task. No such record exists; `docs/design/dhm-precipitation-milestones.md:119` covers the DHM workbook and ERA5-Land only, and the Pyramid AWS source is NPT (`docs/design/dhm-precipitation-vision.md:354`), so both timebases are already in the building undocumented.

**Verification:** N/A — audit task. Every adapter appears with a cited source for its convention, or is explicitly marked unresolved.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py --inspect-json docs/plans/245-time-grids-are-step-and-phase.md
```

Four conditions hold in addition:

1. **No code path aligns two series on `step` alone.** Two "hourly" series that share no instant must
   not be treated as comparable.
2. **No implicit shift.** A grid mismatch is resolved by a declared resample or refused, and the
   REFUSAL is locked by a test, not only the success path.
3. **Upsampling is refused, not flagged.** Three readings a day must not become 24 hourly values.
4. **The conventions are stated where an adapter author reads**, not only in a research design doc.

## Dependency graph

```json
{
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 1, "depends_on": ["T1"]},
    {"id": "T3", "phase": 2, "depends_on": ["T2"]},
    {"id": "T4", "phase": 2, "depends_on": ["T2", "T3"]},
    {"id": "T5", "phase": 3, "depends_on": ["T2"]},
    {"id": "T6", "phase": 1, "depends_on": ["T1"]}
  ]
}
```
