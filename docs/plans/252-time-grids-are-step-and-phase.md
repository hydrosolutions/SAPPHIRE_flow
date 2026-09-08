---
status: DRAFT
created: 2026-09-04
plan: 252
title: A time grid is a step AND a phase — and interval data is period-ending
scope: CONVENTIONS AND TYPES ONLY. Adopt CF `cell_methods` as the temporal-support type, narrow period-ending to interval-valued data, define `TimeGrid(step, phase)`, declare the operational boundary per deployment, read CF attributes at ingest, and supersede Plan 228 D4. Explicitly NOT the phase-aware execution across the seven resampler call sites, the Swiss retrain, artifact grid provenance or the Forecast Lab bounds — all Plan 254. NOT Plan 226's anchoring, NOT Plan 234's aggregation threading.
depends_on: []
blocks: [254]
source: 2026-09-04 — owner raised NPT (UTC+5:45) while reviewing Plan 253; investigation showed the codebase treats a time step as a scalar throughout
---

# Plan 252 — a time grid is a step and a phase

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

## ⭐ It is not a Nepal problem in waiting — phased grids are already in the Swiss STAGING data

Measured read-only against the **staging** database (the mac-mini deployment) on 2026-09-07, while
implementing Plan 241. ⚠️ Staging, not a production system — but it runs the real Swiss pipeline on
real feeds, so the grids it produces are the ones the code produces. The argument above is made from
NPT arithmetic; this is the same defect already present, with no Nepal involvement.

| | |
|---|---|
| forecasts stored | 6457 |
| carrying a sub-second `valid_time` component | 4182 (65%) |
| — of those, **uniformly** spaced (one constant phase) | **4113** |
| — of those, **non-uniform** (multiple interleaved phases) | **69 — every `_pooled` row ever stored** |

The offsets come from four models — `climatology_fallback` (2637), `persistence_fallback` (961),
`linear_regression_daily` (515) and `_pooled` (69). They *look* clock-derived rather than
grid-aligned; stated as an inference, not a measurement, though the mechanism is visible in the
code — the cycle resolves an omitted reference time from an unrounded wall clock
(`flows/run_forecast_cycle.py`).

**The 4113 uniform ones are this plan's thesis with evidence attached.** A constant offset preserves
the gap between consecutive timestamps, so each reports a clean 86400-second step and passes any
check that inspects only the step. The phase is invisible — until two differently-phased series
meet.

**The 69 are what happens when they do.** A sampled pooled forecast is not one grid with a blemish
but two interleaved daily series:

```
2026-09-05 00:00:00+00
2026-09-05 06:00:02.858976+00
2026-09-06 00:00:00+00
2026-09-06 06:00:02.858976+00          <- 21602 s, then 64798 s, repeating
```

⛔ These 69 do **not** have "a phase" in this plan's sense — phase is a property of ONE uniform grid,
and these carry two. They are the failure mode, not an instance of the pattern. Because their stored
cadence is NULL (every measured row predates alembic `0053`) the legacy reader takes the FIRST gap,
so they read back at roughly six hours rather than daily.

Sanity checks run alongside the census: no forecast headers without values, no NULL `valid_time`s,
no duplicate `(forecast, valid_time, member)` rows, no multi-parameter forecasts.

## What the code actually does today

- **A time step is a scalar everywhere.** `QcRuleSet.rules_for` matches `time_step` by equality
  (`types/domain.py:160-166`); `services/forecast_combination.py:458` derives one uniform step. Two
  series can both report `3600` and share no instant.
  📌 **Updated 2026-09-07 by Plan 241 (#258):** `store/forecast_store.py` no longer derives
  `native_step_seconds` from the first pair **for a row with a persisted, non-NULL cadence** — new
  writes store `ensemble.time_step` directly. Note `0053` is nullable-first with no backfill, so a
  row written AFTER the migration by an older image is still NULL; the criterion is the stored
  value, not the migration date. That fixes the step, **not the phase**, and the derivation survives
  for every NULL row, which is every row measured above. The scalar-step critique stands in full.
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
| Nepal daily | `(86400 s, phase from OD-3)` | see OD-3 — pending DHM's answer |
| Nepal sub-daily | `(3600 s, 0)` | UTC hours, matching forcing exactly |
| Swiss daily | `(86400 s, 82800 s)` | 23:00Z → 23:00Z — a fixed UTC+1 day, NOT UTC midnight |

**Every deployment declares a fixed offset; there is no "UTC default" special case.** Switzerland's
is UTC+1 year-round (23:00Z), not DST-following and not UTC midnight — every day is exactly 24 h, the
boundary never moves, and it sits within an hour of civil midnight all year. This is common practice
among European hydrological services precisely to avoid the 23/25-hour problem. The honest cost: in
summer the Swiss "day" ends at 01:00 local rather than midnight, a documented one-hour displacement
that introduces no assumption and fabricates no data.

⚠️ **Changing Switzerland's boundary from UTC midnight to 23:00Z requires retraining, and the owner
has accepted that (2026-09-05).** The existing Swiss artifacts were trained on UTC calendar days;
under OD-7 an artifact is only valid for the cut it was trained on, so every Swiss daily artifact
must be retrained against 23:00Z days before the new boundary goes live. Three consequences follow,
and none of them belong to this plan:

- **Retraining is a deployment activity, not a task here.** This plan makes the boundary declarable;
  it does not retrain anything. Sequencing the retrain belongs with Plan 226, which already owns
  daily-model anchoring.
- **Skill scores computed against UTC-day observations become invalid for the retrained artifacts**,
  since the scored quantity changes. That intersects Plan 235's generation model — a recompute, not a
  silent overwrite.
- **Until the retrain lands, Switzerland stays at phase 0.** The declaration and the retrain must go
  live together; a config flip alone would feed 23:00Z days to artifacts trained on midnight days,
  which is exactly the substitution OD-7 says the model cannot detect.

⛔ **A DST-observing zone has no uniform civil-day grid at all.** Measured: Zurich civil days run 23,
24 or 25 hours (29 Mar 2026 is 23:00Z→22:00Z = 23 h; 25 Oct is 22:00Z→23:00Z = 25 h). A "day" that is
not 86400 s is not a step, so no amount of phase bookkeeping rescues it. Nepal has **no DST** —
`Asia/Kathmandu` is UTC+05:45 for all twelve months — so its civil day *is* a uniform grid. This is
why the phase is declared per deployment and never derived from a timezone: derivation would give
18:15 for Nepal (ignoring OD-3) and something broken for Switzerland.

**OD-3 — a daily bucket is whole UTC hours. WHICH whole hour is an open question for DHM, and the
answer may remove the compromise entirely.**

The original reasoning assumed the boundary is local midnight, which lands at 18:15Z and forces a
15-minute rounding. **That assumption may be wrong.** In Nepal, any local clock time at `:45` past
the hour maps to an *exact* whole UTC hour:

| Local | UTC |
|---|---|
| 00:45 NPT | 19:00Z |
| 06:45 NPT | 01:00Z |
| **08:45 NPT** | **03:00Z** |
| 09:45 NPT | 04:00Z |

So if DHM's conventional observation or rainfall day begins at a `:45` local time, we get an exact
whole-hour bucket with **no rounding, no displacement and no compromise**. Precedent makes this
likely: **India's Met Department defines its rainfall day as 08:30–08:30 IST**, and IST is UTC+5:30 —
also a non-whole-hour zone — which maps to exactly 03:00Z. A national service with the same class of
problem already solved it by anchoring to a local clock time that lands cleanly. South Asian services
commonly use an 08:30 or 08:45 observation day.

**Therefore: ask DHM what their conventional observation/rainfall day boundary is (T7), and adopt it
if it maps to a whole UTC hour.** The rounding below is the FALLBACK, used only if their boundary is
local midnight or otherwise lands mid-hour.

**Fallback if the boundary is local midnight: 18:00Z→18:00Z, not the exact civil 18:15Z.** The forcing
is hourly, so an exact civil day would require apportioning one hourly precipitation accumulation
across the boundary **every single day**, under an assumption of uniform rainfall within that hour —
the least safe assumption available for convective rain. The whole-hour bucket introduces **no
assumption at all**: every value is used as delivered. The cost is a documented 15-minute
displacement (a Nepali "day" runs 00:45–23:45 local), which is 0.6% of the day and is stated on the
label rather than discovered. The shift is uniform, so consecutive days partition the timeline
exactly — no gaps, no overlaps, no drift, no value counted twice.

**OD-7 — models are timezone-agnostic, which is precisely why the CUT must match.** Models consume
time steps and know nothing about zones; the modeller supplies steps. That is not a reason the
boundary is unimportant — it is the reason it is dangerous. Training data cut at midnight UTC and
operational data cut at 18:00Z are both "daily totals" to the model, but they are **different
physical quantities**, and nothing in the model can detect the substitution. The responsibility sits
entirely with us.

This is also how Plan 228 D4 reconciles. D4 says *"every path aggregates onto UTC calendar buckets;
nothing aligns to a forecast's own timestamp"*, and its reasoning is entirely about a **moving**
anchor — the first implementation lined buckets up with `issue_time`, so a 06:00 hindcast consumed
rolling 06:00–06:00 means while training used calendar days. A fixed, declared, deployment-wide
boundary does not reintroduce that: it is the same instant every day, forever. **D4's invariant
survives; only the word "UTC" narrows.** T8 amends it to "one fixed declared boundary per deployment,
never the forecast's own clock", which preserves every reason D4 gives.

⛔ **The binding constraint is that training and operation must use the SAME cut.** Changing a
deployment's boundary after its artifacts are trained invalidates them. For Nepal this is free today
(no artifacts trained). For Switzerland it is not — see the warning in OD-2.

**OD-8 — Delft-FEWS reaches the same split, which is mild evidence we are on a trodden path.** FEWS
stores internally in GMT, attaches a time-zone attribute per series, and — for *equidistant* series —
configures **fixed offsets** (`GMT+1`) rather than DST-observing zone names, reserving DST-aware
zones for display. That is the same fixed-offset-for-data, DST-for-display split arrived at here
independently. *Confidence: from knowledge of FEWS, not verified against its documentation; treat as
a starting point, not a citation.*

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
existing `InputQualityFlag` channel, which Plan 253 made persistent and API-visible — no second
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
nearest-match trap Plan 253's review identified as *dimensionally wrong*: a rate-of-change threshold
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
- **Plan 253** found the fail-closed hole in QC rule lookup. The principle is identical and should be
  stated once, here, rather than rediscovered per subsystem.

## Non-goals

- The daily-model anchoring fix (Plan 226).
- Threading declared aggregation through the assembly paths (Plan 234).
- Building any DHM or Nepali adapter.
- Retrofitting phase onto historical stored rows. New writes declare it; a backfill is a separate
  decision with its own cost.
- Changing `UtcDatetime` or the storage timezone.

## Corrections forced by review (2026-09-05, 26 findings: 20 blockers)

This plan was rewritten after an independent review. Four errors are recorded rather than quietly
fixed, because three of them were errors of *reasoning*, not typos:

1. **The D4 argument was wrong.** The first draft claimed Plan 228 D4's reasoning is "entirely about
   a moving anchor". It is not: D4 also rests on NWP, training and existing artifacts all
   representing UTC calendar days (`228:132`), and its invariant is implemented in shipped code —
   `aligned_lookback_bounds` and `floor_to_time_step`, both phase-zero (`228:294`). This is a
   **supersession**, not a one-word narrowing. See OD-9.
2. **A test contradicted this plan's own table.** The draft's `TimeGrid` test asserted a 10-minute
   grid nests into "neither" hourly grid. The OD-6 table in the same document shows 10-minute on
   `:00` straddling **0 of 144** intervals against UTC hours — it nests exactly. Corrected in T2.
3. **"Phase is absent everywhere" is false.** Skill scoring already validates, partitions and
   persists `(time_step, phase)` (`services/skill/service.py:110`). T2 must reuse or replace that
   representation, not pretend it is new.
4. **"Every input is period-ending" is invalid for instantaneous data** — see OD-1, now narrowed.

## Owner decisions taken 2026-09-05, after the review

**OD-9 — Plan 228 D4 is SUPERSEDED, not reinterpreted (owner allowed 2026-09-05).** D4 forbids
re-opening itself (`228:245`), so this is recorded as an explicit owner disposition. The supersession
must amend D4's **complete** rationale, invariant, implementation record, the decision document, the
touchpoint map and the locking tests — and must state that a fixed non-zero phase preserves D4 only
once training, operational assembly, scoring, NWP handling, fetch bounds **and artifacts** all use the
same declared grid. Until that parity holds, phase-zero remains correct. The execution half is
Plan 254.

**OD-10 — CF `cell_methods` is the temporal-support type (2026-09-05).** The review's blocker was that
nothing distinguishes instantaneous from interval data, so the interpolation rules were not
representable. CF already supplies the vocabulary, and **our upstream data already carries it** —
confirmed by the SnowMapper modeller: `time: point` for SWE and snow depth, `time: sum` for runoff,
alongside `units` and `long_name`, int16-packed with CF `scale_factor`.

| `cell_methods` | Temporal support | Period-ending? | Cross-grid method |
|---|---|---|---|
| `time: point` | instantaneous | **No — a point is not an interval** | linear interpolation, bounded by a maximum gap |
| `time: sum` | interval accumulation | Yes | overlap apportionment |
| `time: mean` / `max` | interval statistic | Yes | the declared `AggregationMethod` |

⛔ **We currently discard this.** The recap adapter reads no CF attributes; `cell_methods` appears in
this repo only as a comment (`adapters/era5_land_reanalysis.py:20`) and in an archived plan.
`ParameterDefinition` (`types/domain.py:36`) carries `unit` and `aggregation_method` and nothing about
temporal support. T3 reads it at ingest — the cheapest route to the typed distinction the review
demanded, using a standard rather than an invention.

**OD-1 (NARROWED) — interval-valued data is period-ending; instantaneous data is not.** A river stage
reading at 08:00 is a *point*, not an interval ending at 08:00. The original blanket rule was wrong
for every `time: point` channel. The convention now binds only `time: sum` and `time: mean`/`max`
data, and the distinction is carried by OD-10 rather than assumed.

**OD-3 (REVISED) — the Nepal daily boundary is PROVISIONALLY 18:00Z, pending DHM (T7).** The draft
held three different values at once; this is the single value used throughout until DHM answers.

Two independent routes reach it, which is why it is a defensible provisional rather than a guess:
rounding civil midnight (18:15Z) down to the whole hour, **and** SnowMapper's own daily aggregation,
which uses a solar offset of `round(centroid_lon/15)` = **UTC+6 for Nepal — whose midnight is exactly
18:00Z**.

**No cross-system conflict — corrected 2026-09-07.** An earlier draft warned that adopting a DHM
boundary other than 18:00Z would put us at odds with SnowMapper. That was wrong. **We receive
SnowMapper data as hourly UTC on the top of the hour and aggregate it ourselves**; the UTC+6 solar
shift is their *dashboard's* presentation layer, not our input. So there is no boundary to reconcile
with them — the UTC+6 coincidence remains an interesting corroboration of 18:00Z and nothing more.
The same holds for any UTC-delivered source.

**The requirement is therefore configurability, not a particular value (owner, 2026-09-07).**
Whatever DHM names as their reference is what we adopt. 18:00Z is the default until they answer; the
design must not hard-code it anywhere, and T4's rejection of an undeclared deployment is what keeps
that honest.

**OD-11 — Switzerland must DECLARE phase zero; absence is not a default.** The draft verified that an
undeclared deployment silently defaults to 0, which contradicts this plan's own fail-closed rule.
Every deployment declares, including the one whose answer is zero.

**Supply-side confirmations (SnowMapper modeller, 2026-09-05):** forcing is hourly on the top of the
UTC hour with no `:45` timestamps — ECMWF native 3/6-hourly UTC interpolated to 1-hourly UTC — so
OD-2's sub-daily-on-UTC decision matches what is actually delivered. NPT is presentation-layer only.
A known gap on their side: point meteograms are published in UTC and should be NPT — display-layer,
not ours.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:378-390`).

### T1 — declare the conventions where an adapter author will find them

**Outcome:** period-ending is stated for interval-valued data only; CF `cell_methods` is named as the
temporal-support type; phase is defined as a grid property; and the DST limitation is recorded as a
limitation.

**In:** `docs/conventions.md` (primary home), cross-referenced from `docs/architecture-context.md`
and `docs/spec/types-and-protocols.md`. Must carry the OD-10 table, the NPT worked example, the
statement that period convention and phase are independent, and that a DST-observing zone has no
uniform civil-day grid.

**Out:** any code change; the adapter audit (T6); anything in Plan 254.

**Pre-change:** N/A — documentation task. `grep -rn "period-ending\|cell_methods" docs/conventions.md` returns nothing; the convention lives only in `docs/design/dhm-precipitation-milestones.md:119` and a code comment.

**Verification:** N/A — documentation task. The OD-10 table and the instantaneous exception both appear.

### T2 — a typed `TimeGrid`, reusing what already exists

**Outcome:** `TimeGrid(step, phase)` exists with `0 <= phase < step`, an alignable predicate and a
nesting predicate — and it either reuses or explicitly replaces skill's existing `(time_step, phase)`
representation rather than duplicating it.

**In:** `src/sapphire_flow/types/`, `docs/spec/types-and-protocols.md`, and
`services/skill/service.py:110` where phase is already validated and persisted. Depends on T1.

**Out:** deriving phase from an IANA timezone — never offered, since derivation reintroduces DST.
Threading it through call sites (Plan 254).

**Pre-change:** `uv run python -c "from sapphire_flow.types.domain import TimeGrid"` fails with ImportError, while `grep -n "phase" services/skill/service.py` shows a second, incompatible representation already in use.

**Verification:** `uv run pytest tests/unit/types/test_time_grid.py` — hourly UTC and hourly Nepali are both step 3600 and NOT alignable; a 15-minute grid on quarter-hour marks nests into BOTH; **a 10-minute phase-zero grid nests into hourly UTC and NOT into hourly Nepali** (the draft asserted neither, contradicting OD-6); phase >= step raises.

### T3 — read CF attributes at ingest

**Outcome:** `cell_methods` and `units` survive from the source NetCDF into stored parameter
metadata, so temporal support is a fact we hold rather than one we assume.

**In:** the recap extraction path and `ParameterDefinition` (`types/domain.py:36`). Depends on T2.

**Out:** acting on the value — that is Plan 254. Changing `AggregationMethod`.

**Pre-change:** `grep -rn "cell_methods\|\.attrs" src/sapphire_flow/adapters/` returns nothing; the attribute is present in every upstream file and discarded at our boundary.

**Verification:** `uv run pytest tests/unit/adapters/` — a fixture carrying `cell_methods: time: sum` and `units: mm` round-trips into parameter metadata, and a source missing `cell_methods` is recorded as unknown rather than defaulted.

### T4 — declare the operational grid boundary per deployment

**Outcome:** every deployment declares its boundary explicitly, including Switzerland's zero.

**In:** `config/overlays/` and the deployment config model, written as a readable time-of-day
(`daily_grid_origin = "18:00"`) parsed once into a phase. Depends on T2.

**Out:** deriving it from `stations.timezone`, which stays descriptive. Any per-station override.

**Pre-change:** `grep -rn "grid_origin\|grid_phase" config/ src/sapphire_flow/config/` returns nothing; no deployment declares a boundary.

**Verification:** `uv run pytest tests/unit/config/` — Nepal parses `"18:00"` to 64800 s; a deployment with NO declaration is **REJECTED** rather than defaulting to zero (OD-11); a value finer than the step's resolution is rejected.

### T6 — audit every input adapter against the conventions

**Outcome:** each adapter's period convention, temporal support and native phase are recorded —
confirmed, converted, or flagged unresolved.

**In:** `src/sapphire_flow/adapters/`, recorded as a table in `docs/conventions.md`. Depends on T1.

**Out:** fixing a non-conforming adapter — each is its own change.

**Pre-change:** N/A — audit task. No such record exists, and both timebases are already in the building undocumented: the DHM workbook is UTC period-ending (`docs/design/dhm-precipitation-milestones.md:119`) while Pyramid AWS is NPT (`docs/design/dhm-precipitation-vision.md:354`).

**Verification:** N/A — audit task. Every adapter appears with a cited source, or is marked unresolved.

### T7 — put the boundary question to DHM and to the snow modeller

**Outcome:** the conventional observation-day boundary is asked of DHM, so the provisional 18:00Z
default can be replaced by their actual reference.

**In:** `docs/requirements/dhm-data-formats-questions.md`. Three questions: the conventional day
boundary in local time; a request for **15-minute data on `:00/:15/:30/:45`** (10-minute is worse for
Nepali targets, since 10 does not divide the 345-minute offset and 15 does); and confirmation of
period convention per parameter. Cite the India 08:30 IST precedent — it makes the question read as a
familiar convention rather than an unusual demand.

**Out:** re-rendering the `.docx`; assuming an answer. Unanswered leaves OD-3 on its provisional value.

**Pre-change:** N/A — requirements task. `grep -n "rainfall day\|observation day\|day boundary" docs/requirements/dhm-data-formats-questions.md` returns nothing.

**Verification:** N/A — requirements task. All three questions appear with the precedent cited.

### T8 — supersede Plan 228 D4

**Outcome:** D4 is superseded, not reinterpreted, with the owner's disposition recorded and the parity
precondition stated.

**In:** `docs/plans/228-hindcast-and-skill-on-wrong-data.md` § D4 (`:121-147`), its implementation
record (`:294`), the decision document and `docs/touchpoint-maps.md`. Must preserve the
moving-anchor prohibition, exactly-N-complete-buckets, and align-and-extend; must state that phase
zero remains correct until training, assembly, scoring, NWP, fetch bounds and artifacts all share a
declared grid. Depends on T1.

**Out:** re-opening D1-D3, the shipped P1/P2 fix, or anything 228 assigns to Plan 234/235. Changing
any phase-zero behaviour — that is Plan 254.

**Pre-change:** N/A — documentation task superseding a settled decision. `228:123` reads "Every path aggregates onto UTC calendar buckets", which forbids daily forecasts for any region not on UTC.

**Verification:** N/A — documentation task. D4 still forbids a moving anchor; the owner disposition and the parity precondition are both recorded.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/252-time-grids-are-step-and-phase.md
```

Four conditions hold in addition:

1. **One Nepal boundary value appears throughout** — the draft held three at once.
2. **No deployment defaults to phase zero by omission** (OD-11).
3. **Temporal support comes from CF `cell_methods`, never inferred** from a parameter name.
4. **D4 is superseded with the parity precondition stated**, not reinterpreted.

## Dependency graph

```json
{
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 1, "depends_on": ["T1"]},
    {"id": "T3", "phase": 2, "depends_on": ["T2"]},
    {"id": "T4", "phase": 2, "depends_on": ["T2"]},
    {"id": "T6", "phase": 1, "depends_on": ["T1"]},
    {"id": "T7", "phase": 1, "depends_on": []},
    {"id": "T8", "phase": 2, "depends_on": ["T1"]}
  ]
}
```
