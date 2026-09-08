---
status: DRAFT
created: 2026-09-04
plan: 252
title: A time grid is a step AND a phase
scope: CONVENTIONS AND TYPES ONLY, and GRIDS ONLY. Define `TimeGrid(step, phase)`, declare the operational day boundary per deployment, audit the input adapters against it, and propose the supersession of Plan 228 D4. Explicitly NOT temporal support (point vs interval), CF `cell_methods`, or the period-ending convention — all Plan 258, split out 2026-09-08. Explicitly NOT the phase-aware execution across the resampler call sites (Plan 254 holds the inventory; re-measure it, never cite a count from prose), the Swiss retrain, artifact grid provenance or the Forecast Lab bounds — all Plan 254. NOT the daily-model anchoring (Plan 254 T8, absorbed from the superseded Plan 226), NOT Plan 234's aggregation threading.
depends_on: []
blocks: [254, 258]
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
  it does not retrain anything.

  🔴 **CORRECTED 2026-09-08 by the time-grid track owner — the retrain belongs to Plan 254 T6, NOT
  to Plan 226.** *(Plan 226 was subsequently SUPERSEDED and absorbed into Plan 254 T8, so the
  question is moot as well as answered; the reasoning is kept because the check that settled it is
  worth repeating.)* The original claim here (that 226 "already owns" the sequencing) was an assumption
  never checked against 226 itself, and it contradicted `254:182`, which says no plan owns the
  retrain until T6 creates the home. 254 is right. Verified against Plan 226's frontmatter and body,
  not its prose reputation: its `scope` reads *"ANCHORING ONLY"*, its `depends_on` is `[222, 228,
  235]` with no reference to 252 or 254, it excludes *"Backfill, recomputation, or migration of
  stored forecasts or hindcasts"* twice, and **the words "retrain" and "artifact" do not appear in
  it at all.** 226 cannot own a retrain it never mentions. What 226 does own, and keeps, is
  daily-model anchoring.
- **Skill scores computed against UTC-day observations become invalid for the retrained artifacts**,
  since the scored quantity changes. That intersects Plan 235's generation model — a recompute, not a
  silent overwrite.
- **Until the retrain lands, Switzerland stays at phase 0.** The declaration and the retrain must go
  live together; a config flip alone would feed 23:00Z days to artifacts trained on midnight days,
  which is exactly the substitution OD-7 says the model cannot detect.

✅ **The feared DOUBLE RETRAIN does not exist — refuted 2026-09-08, same check.** The concern raised
against this plan was: land 226 first, the models get anchored to midnight and retrained, then move
to 23:00Z and retrain *again*. It rests on 226 causing a retrain, and **226 causes none** — it
contains neither the word "retrain" nor "artifact", because it changes how daily output is *labelled*,
not what the models are trained on. There is therefore exactly one retrain in the whole family:
Plan 254 T6's, when the boundary actually moves.

That also means **226 needs no change and no re-scoping**, which matters because 226 carries a binding
Proportionality section — a finding that grows it is worse than one that shrinks it. 226 anchors to
UTC midnight, and per the bullet above midnight *is* Switzerland's boundary until T6 flips it, so 226
is anchoring to the grid actually in force and stays correct until then. Ordering 226 before or after
this plan does not create a second retrain. Sequence them on other grounds if you like; not on this
one.

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

**OD-4 — MOVED to Plan 258.** Forecast `valid_time` labelling depends on whether the value is a point
or an interval, which is Plan 258's subject. The previous text here labelled forecast `valid_time`
period-ending *without qualification*, which an independent review correctly flagged as contradicting
this plan's own later narrowing. Plan 258 states it once, by support.

**OD-5 — MOVED to Plan 258.** Interval bounds only exist for values that ARE intervals, so publishing
`period_start`/`period_end` is downstream of the point/interval decision. Plan 258 owns it.

**OD-6 — resampling: coarsen freely, interpolate with a per-parameter method, never upsample.**

| Case | Rule |
|---|---|
| Target coarser than source | Aggregate with the parameter's declared `AggregationMethod` (SUM for accumulations, MEAN for state variables) |
| Instantaneous variable onto off-phase marks | **Plan 258** — the method depends on temporal support. Linear interpolation between bracketing observations, subject to a maximum gap — beyond it emit nothing, never a straight line across a two-day hole |
| Accumulation onto a straddling boundary | **Plan 258** — support-dependent. Apportion by overlap fraction; flag as degraded when the split interval exceeds **15 minutes** |
| Target finer than the source's median spacing | **Refuse.** Three readings a day cannot become 24 hourly values; that is invention, not interpolation |

⛔ **The two support-dependent rows above are Plan 258's, not this plan's** — they are listed here only
so the table reads whole. What THIS plan owns is the grid part: coarsen freely, never upsample, and
refuse a target finer than the source's median spacing. The method is determined by the series, never
by the caller. Degradation is reported through the
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

**OD-1 — MOVED to Plan 258.** The period-ending convention binds interval-valued data only, and the
point/interval distinction is Plan 258's subject. The blanket form of this rule, and the narrowing
that later contradicted it, both live there now — stated once.

⛔ **Period convention and timezone phase are INDEPENDENT, and only the phase is this plan's.** Getting
the 45 minutes right and the labelling convention wrong yields a silent one-hour error stacked on the
offset. They must be recorded separately, never conflated into one "offset" field. This plan owns the
phase; Plan 258 owns the convention.

## Design

**A grid is `(step, phase)`.** Phase is the offset of the grid's marks from UTC midnight, as a
`timedelta` in `[0, step)`. Hourly UTC is `(3600s, 0)`. Hourly NPT is `(3600s, 900s)`. Daily UTC is
`(86400s, 0)`. Daily Nepali is `(86400s, 65700s)` — 18:15 UTC.

⚠️ **`65700 s` is the CIVIL reference, not the boundary we operate on. Four different numbers appear
in this plan and they are not alternatives — they are four distinct things.** Read this table before
quoting any of them; it is what a deployment gets configured from.

| # | Value | What it is | Status |
|---|---|---|---|
| 1 | `65700 s` (18:15Z) | Nepal's **exact civil** day boundary — NPT midnight, UTC+05:45 | Reference. Never operated on directly. |
| 2 | `64800 s` (18:00Z) | Nepal's **provisional operating** boundary — civil midnight rounded down to the whole hour, corroborated (not caused) by SnowMapper's UTC+6 day | **What T4 configures**, pending DHM's answer (T7). Whatever DHM names replaces it. |
| 3 | `0 s` (00:00Z) | Switzerland **today**, and what every deployment must keep declaring until the parity precondition in OD-9 holds | **Current.** OD-11 and T4 require it. |
| 4 | `82800 s` (23:00Z) | Switzerland **after the cutover** — fixed UTC+1 year-round, so every day is exactly 24 h | **Target.** Requires the retrain; Plan 254 T6 owns the move. |

⛔ **Rows 3 and 4 are the same deployment before and after a cutover, not a contradiction.** Where
this plan says Switzerland "is" `82800 s` it means the target; where it says Switzerland must declare
phase zero it means today. Both are true, in that order. Likewise rows 1 and 2: "anchor to Nepali
midnight" states the intent, `64800 s` is what we can actually operate on until DHM answers, and the
45-minute difference means a Nepali day so configured runs **00:45–23:45 local**, which is a known and
accepted approximation, not an oversight.

**Storage stays UTC**; `UtcDatetime` and `ensure_utc()` at boundaries are unchanged. Phase is
recorded *alongside*, not instead.

📐 **Phase is measured from UTC midnight, and that is a CHOICE this plan makes explicit.** The existing
code derives phase by modulo against the **Unix epoch** (`services/training_data.py:237`,
`services/skill/service.py`). For any step that divides 24 hours the two agree, because the epoch
began at a UTC midnight — and every step we run today (hourly, 3-hourly, 6-hourly, daily) does divide
it. For a step that does **not** divide 24 h (say 7 h, or 90 minutes) they diverge, and a value that
means one thing in config would mean another in the resampler.

**Rule:** the declared boundary is a time-of-day and is therefore **midnight-referenced**; the
implementation must convert to whatever reference the resampler uses rather than assume they match.
**Constraint:** a step that does not divide 24 h is **rejected at config load** until someone needs
one, at which point the reference must be settled deliberately rather than discovered. Examples that
are rejected: 7 h (1440 / 420 is not an integer) and 50 min. ⚠️ **90 min is NOT such an example** — it
divides 24 h exactly 16 times; an earlier revision of this paragraph used it wrongly.

**Bounds: `0 <= phase < step`.** Zero is a legal, required phase — Switzerland declares exactly that
(OD-11), so a rule excluding it would forbid the only deployment we run. *(An earlier revision here
wrote `0 < phase < step`, contradicting T2 and OD-11.)*

**Precision: phase is a whole number of MINUTES.** ⛔ The earlier formulation — "not an exact multiple
of the smallest unit the step is expressed in" — is not implementable: a `timedelta` does not preserve
how it was written, and `timedelta(days=1) == timedelta(hours=24) == timedelta(seconds=86400)`. Minute
precision is implementable, is what the config format expresses (`daily_grid_origin = "18:00"`), and
covers every case we have: NPT's 45-minute offset needs it, and nothing we ingest is anchored to a
sub-minute boundary. A phase with a non-zero seconds component is rejected at config load.

⛔ **Corollary — a stored step alone is HALF a grid, and a half grid reads as a false whole.** Any
store that records a step without its phase records something true and incomplete, and the incomplete
form is not inert: a bare `86400` invites every later reader to conclude "this sits on the daily grid
at midnight", which is a claim the value does not make. **Recording a step therefore obliges
recording a phase.** Where a store records only one, the omission is a defect to be named in that
store, not a convention to be worked around by inference at read time — inferring the phase back from
the timestamps is how a value that was never asserted becomes one that appears to have been.

📌 **This is an established pattern here, not a new design.** `skill_scores.phase_offset_seconds` and
`skill_diagrams.phase_offset_seconds` already exist (`db/metadata.py:1540`, `:1611`) and are written
(`store/skill_store.py:520`), gated to `computation_version >= 2`; the skill service refuses an
internally mixed-phase ensemble outright (`validate_homogeneous_time_step_and_phase`, Plan 228 D2).
The skill path records the whole grid. The forecast path does not.

🔴 **Measured on staging 2026-09-08, after `0.1.889` deployed Plan 241's write path:** of the 377
`forecasts` rows the system has populated `time_step_seconds` on so far, every one stores `86400`,
and **209 of them — 55% — sit on a non-zero, clock-derived phase** (139 `climatology_fallback` rows
at 26658 s = 07:24:18 UTC, among others). Only 168 are genuinely at phase 0. So the half-grid is
already the majority case in the live column, and it will stay that way until it is fixed: **the
store-side remedy — a phase column on `forecasts` mirroring the skill tables — is Plan 254's**, and
the disposition of the pre-existing NULL rows is Plan 248's (decided: quarantine). This plan owns
only the statement that a step without a phase is not a grid.

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

- **Plan 254 T8** anchors the daily models' `valid_time` to the calendar day they predict. That is the
  same defect family — a grid whose phase was never declared — and **254 T8 owns it.** *(It was
  absorbed from Plan 226 on 2026-09-08; 226 is `SUPERSEDED` and must not be cited as a live owner. An
  earlier revision of this bullet named 254 T8 and then said "226 owns the daily-model fix" two lines
  later.)*
- **Plan 258** owns temporal support — whether a value is a point or an interval — and therefore the
  period-ending convention, interval bounds, and the support-dependent resampling methods. This plan
  is buildable without it and must not re-absorb it.
- **Plan 234** threads each channel's declared aggregation method end to end. This plan supplies the
  rule that says *when* a resample is required; 234 supplies *how* it is performed.
- **Plan 253** found the fail-closed hole in QC rule lookup. The principle is identical and should be
  stated once, here, rather than rediscovered per subsystem.

## Non-goals

- The daily-model anchoring fix (**Plan 254 T8**, absorbed from the superseded Plan 226).
- Threading declared aggregation through the assembly paths (Plan 234).
- Building any DHM or Nepali adapter.
- Retrofitting phase onto historical stored rows. New writes declare it; a backfill is a separate
  decision with its own cost.

  ✅ **Discharged 2026-09-08.** Plan 248 T2 took that separate decision for the one population it
  affects — the 69 non-uniform `_pooled` rows in staging — and chose **discard**, once the owner
  established that the mac-mini is a test deployment and old forecasts may be deleted. With no NULL
  rows left, Plan 248 T3 uses a plain `SET NOT NULL`. *(An earlier note here recorded the
  quarantine + `NOT VALID` design that this superseded the same day; it was withdrawn because a CHECK
  is re-evaluated on UPDATE and would have frozen those rows.)* Nothing here waits on that and
  nothing there waits on this.
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

⚠️ **Tense correction (2026-09-08).** OD-9 below is written as though D4 *has been* superseded. It
has not. This plan is `DRAFT` — a proposal, not an instruction (`docs/workflow.md:64-89`) — and T8 is
the future task that would amend Plan 228. **Plan 228 is READY, its D4 is the authoritative rule, and
the code implements it** (`services/training_data.py:237-276`). Read OD-9 as *"D4 is proposed for
supersession by T8"*: phase zero stays authoritative until the owner advances this plan and that
amendment actually lands. The disposition is real; the past tense is not.

**OD-9 — Plan 228 D4 is PROPOSED FOR SUPERSESSION, not reinterpretation (owner allowed 2026-09-05).** D4 forbids
re-opening itself (`228:245`), so this is recorded as an explicit owner disposition. The supersession
must amend D4's **complete** rationale, invariant, implementation record, the decision document, the
touchpoint map and the locking tests — and must state that a fixed non-zero phase preserves D4 only
once training, operational assembly, scoring, NWP handling, fetch bounds **and artifacts** all use the
same declared grid. Until that parity holds, phase-zero remains correct. The execution half is
Plan 254.

**OD-10 — MOVED to Plan 258.** Adopting CF `cell_methods` as the temporal-support vocabulary, the
support table, and the fact that we currently discard all of it are Plan 258's, together with the
three blockers an independent review raised against them on 2026-09-08: the wrong cardinality (the
same canonical parameter has different support per product), an unsafe fail-closed migration, and an
unrepresentable "unknown" state.

**OD-1 (NARROWED) — MOVED to Plan 258**, with OD-1 and OD-4.

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

### Temporal support — moved out on 2026-09-08 (not a task here)

Temporal support — declaring it, and later verifying it against the source's CF `cell_methods` — is
**Plan 258 T1–T4**. It left this plan on 2026-09-08 because an independent review returned three
blockers against it and none of them were about grids: the value was assigned at the wrong
cardinality, the fail-closed migration violated the repo's additive-only rule, and "unknown" was not
representable. Those are now Plan 258's open decisions D1–D3.

⛔ **Do not re-absorb it.** This plan is buildable without it: a grid is a step and a phase whether or
not we have recorded what the values mean over that step.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:378-390`).

### T1 — declare the conventions where an adapter author will find them

**Outcome:** phase is defined as a grid property, the NPT worked example is written down, and the DST
limitation is recorded as a limitation rather than left to be rediscovered.

**In:** `docs/conventions.md` (primary home), cross-referenced from `docs/architecture-context.md`
and `docs/spec/types-and-protocols.md`. Must carry the NPT worked example, the statement that period
convention and grid phase are **independent** (with the convention itself pointed at Plan 258), and
that a DST-observing zone has no uniform civil-day grid at all.

⛔ **Not the period-ending convention or the CF table** — Plan 258. Name the split here so an adapter
author reading `conventions.md` finds both halves.

**Out:** any code change; the adapter audit (T6); anything in Plan 254.

**Pre-change:** N/A — documentation task. `grep -rn "grid phase\|TimeGrid" docs/conventions.md` returns nothing; the convention lives only in `docs/design/dhm-precipitation-milestones.md:119` and a code comment.

**Verification:** N/A — documentation task. Phase is defined, the NPT example appears, the DST limitation is stated, and Plan 258 is named as the home of temporal support.

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

### T4 — declare the operational grid boundary per deployment

**Outcome:** every deployment declares its boundary explicitly, including Switzerland's zero.

**In:** the deployment config model, plus **both** config layers — `config/overlays/` **and the
repository-root `config.toml`**, which is the active Swiss base and is loaded separately from the
overlays (`config/deployment.py:452`, `docs/v0-scope.md:490`). ⛔ **The root file was missing from
this task's scope, which would have made the requirement unsatisfiable for the one deployment we
actually run**: if the field has no default, the base config must declare it or Switzerland refuses to
start. Written as a readable time-of-day (`daily_grid_origin = "18:00"`) parsed once into a phase. **Also
`docs/spec/config-reference.toml`**, which states that it documents every config field — a new field
that does not appear there breaks that promise, and the workflow requires affected docs in every code
change. Depends on T2.

**Out:** deriving it from `stations.timezone`, which stays descriptive. Any per-station override.

**Pre-change:** `grep -rn "grid_origin\|grid_phase" config/ src/sapphire_flow/config/` returns nothing; no deployment declares a boundary.

**Verification:** `uv run pytest tests/unit/config/` — Nepal parses `"18:00"` to 64800 s; Switzerland's
root `config.toml` declares `"00:00"` → **0 s and is ACCEPTED** (phase zero is legal and required, not
an omission); a step that does not divide 24 h is **REJECTED at load**; a phase with a non-zero
seconds component is **REJECTED**; and a deployment with NO
declaration is **REJECTED** rather than defaulting to zero (OD-11); a value finer than the step's resolution is rejected.

### T6 — audit every input adapter against the conventions

**Outcome:** each adapter's **native grid phase** is recorded — confirmed, converted, flagged
unresolved, or **`NOT A GRID`**.

⛔ **The fourth outcome is required, and its absence contradicted OD-0.** OD-0 says a source is not
assumed to be a grid at all; a manually-read gauge or an event-triggered series has **no phase**, and
recording that as "unresolved" would misfile a known fact as an open question. `NOT A GRID` is a
terminal, correct answer. *(Temporal support gets its own audit in Plan 258 T4; keep the two tables adjacent in
`docs/conventions.md` but do not merge them — they are independent facts and merging them is how one
silently stands in for the other.)*

**In:** `src/sapphire_flow/adapters/`, recorded as a table in `docs/conventions.md`. Depends on T1.

**Out:** fixing a non-conforming adapter — each is its own change.

**Pre-change:** N/A — audit task. No such record exists, and both timebases are already in the building undocumented: the DHM workbook is UTC period-ending (`docs/design/dhm-precipitation-milestones.md:119`) while Pyramid AWS is NPT (`docs/design/dhm-precipitation-vision.md:354`).

**Verification:** N/A — audit task. Every adapter appears with a cited source, or is marked unresolved.

### T7 — make the DHM day-boundary questions EXACT

⚠️ **Corrected 2026-09-08: the questionnaire already asks this, and the earlier framing here was
wrong.** It claimed the questions were absent and cited a grep that returns nothing only because it is
case-sensitive and uses our vocabulary rather than the document's. `docs/requirements/dhm-data-formats-questions.md`
already asks **Q3.3** ("How is daily flow defined? Daily mean / instantaneous / max? … day boundary")
and **Q8.3** ("Time precision: exact timestamp format, the UTC offset (NPT = +05:45), seconds
precision, and the definition of a day"). The task is therefore to make existing questions precise
enough to settle OD-3 — not to add a missing one. The snow modeller is also **not** in scope here
despite the old title; the Gateway ask moved to Plan 258.

**Outcome:** Q3.3 and Q8.3 are sharpened so that DHM's answer resolves the day boundary to a specific
whole hour, so the provisional 18:00Z default can be replaced by their actual reference.

**In:** `docs/requirements/dhm-data-formats-questions.md`, amending Q3.3 and Q8.3 and adding what is
genuinely missing. Three things must be answerable afterwards: the conventional day
boundary in local time; a request for **15-minute data on `:00/:15/:30/:45`** (10-minute is worse for
Nepali targets, since 10 does not divide the 345-minute offset and 15 does); and confirmation of
period convention per parameter. Cite the India 08:30 IST precedent — it makes the question read as a
familiar convention rather than an unusual demand.

⛔ **The Gateway CF-metadata ask MOVED to Plan 258** together with the temporal-support work it
serves. It is not this plan's, and this plan is not blocked on it.

**Out:** re-rendering the `.docx`; assuming an answer. Unanswered leaves OD-3 on its provisional value.

**Pre-change:** N/A — requirements task. Q3.3 and Q8.3 exist but neither forces an answer naming a specific hour, so DHM could answer both fully and leave OD-3 unresolved. `grep -in "day boundary\|definition of a day" docs/requirements/dhm-data-formats-questions.md` returns nothing.

**Verification:** N/A — requirements task. Q3.3 and Q8.3 each demand a specific hour, the 15-minute grid request appears, and the India 08:30 IST precedent is cited.

### T8 — supersede Plan 228 D4

**Outcome:** D4 is superseded, not reinterpreted, with the owner's disposition recorded and the parity
precondition stated. ⛔ **This task is the ONLY thing that makes the supersession real** — until it
runs, D4 stands and phase zero is correct. Anything elsewhere in this plan written in the perfect
tense is describing this task's intended effect, not a completed one.

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

1. **One Nepal OPERATING boundary value appears throughout** — `64800 s` (18:00Z) until DHM answers.
   ⚠️ Not "one value": the plan deliberately distinguishes four (civil reference `65700`, operating
   `64800`, Switzerland today `0`, Switzerland after cutover `82800`) and that table is correct. The
   gate is that no *second operating* value for Nepal appears, which is the defect the draft had.
2. **No deployment defaults to phase zero by omission** (OD-11), including the repository-root
   `config.toml` that the Swiss deployment actually loads — not only `config/overlays/`.
3. **D4's supersession is PROPOSED with the parity precondition stated**, not asserted as done —
   Plan 228 stays authoritative until T8 lands.
4. ⛔ **Removed: "temporal support comes from CF `cell_methods`, never inferred".** That moved to
   Plan 258, and it could not have passed here anyway while the source metadata is stripped upstream.

## Dependency graph

```json
{
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 1, "depends_on": ["T1"]},
    {"id": "T4", "phase": 2, "depends_on": ["T2"]},
    {"id": "T6", "phase": 1, "depends_on": ["T1"]},
    {"id": "T7", "phase": 1, "depends_on": []},
    {"id": "T8", "phase": 2, "depends_on": ["T1"]}
  ]
}
```
