---
status: DRAFT
created: 2026-09-04
plan: 252
title: A time grid is a step AND a phase
scope: CONVENTIONS AND TYPES ONLY, and GRIDS ONLY. Define `TimeGrid(step, phase)`, declare the operational day boundary per deployment with an optional per-station override, audit the input adapters against it, and propose the supersession of BOTH Plan 228 D4 (T8) and the architecture's per-station-IANA day-boundary decision (T9). NOT temporal support (point vs interval), CF `cell_methods`, or the period-ending convention — Plan 258. NOT the phase-aware execution across the resampler call sites, the Swiss retrain, artifact grid provenance, the daily-model anchoring or the Forecast Lab bounds — Plan 254 (which holds the call-site inventory; ALWAYS re-measure it, never cite a count from prose). NOT Plan 234's aggregation threading.
depends_on: []
blocks: [254, 258]
source: 2026-09-04 — owner raised NPT (UTC+5:45) while reviewing Plan 253; investigation showed the codebase treats a time step as a scalar throughout
---

# Plan 252 — a time grid is a step and a phase

## Status

**DRAFT.** Consolidating rewrite 2026-09-09 — see the Changelog. Three independent review rounds
(8 → 7 → 6 blockers) were failed not on the reasoning but on the document: each round patched the
text and left the superseded sentence in place, so corrections accumulated as contradictions. This
revision states every decision once, in its settled form, and confines the history to one Changelog
section. What remains genuinely unsettled is in § Open decisions, named rather than buried.

📍 **Open-question state as of 2026-09-10 — ONE remains open:**

| | state |
|---|---|
| **OQ-3** provenance channel | **OPEN** — narrowed by OD-13; the decision is Plan 254 D2 |
| **OQ-6** differently-phased sources feeding one model | ✅ **ANSWERED by the owner 2026-09-10 — see OD-15.** Switzerland adopts the precipitation day; temperature is the recorded off-grid input. T10 records it; Plan 254 T6 is no longer blocked on a decision, only on T10 landing. |
| OQ-1 target-grid step field | ✅ ANSWERED — the step is per station-and-model pairing in the DB; rejection at onboarding |
| OQ-2 interval bounds | ✅ CLOSED — Plan 258 T5 owns them |
| OQ-4 closest-hour rule | ✅ CONFIRMED by owner 2026-09-09 |
| OQ-5 hindcast phase column | ✅ CLOSED — folded into Plan 254 T7 |

⛔ **Every reference elsewhere in this plan must agree with this table.** Three review rounds were
lost to a corrected decision leaving its superseded statement standing somewhere else.

## The problem in one number

Nepal Time is **UTC+05:45**. An hourly NPT grid and an hourly UTC grid therefore **never share a
timestamp**: Nepali `HH:00` falls on UTC `:15` past the hour. A Nepali calendar day runs
**18:15 UTC → 18:15 UTC**.

Forcing arrives hourly in UTC. Some Nepali observations will arrive hourly in NPT. Both are "hourly".
Neither can be compared to the other without an explicit conversion, and nothing in the codebase
records enough to know that.

**Converting to UTC does not solve this**, which is the trap worth naming up front. Conversion
relabels instants; it does not move them onto a grid. After conversion one series still sits on `:00`
and the other on `:15` — and the offset is now *invisible*, because everything is nominally UTC.

## Phased grids are already in the Swiss data — this is not a Nepal problem in waiting

Measured read-only against the **staging** database (the mac-mini deployment) on 2026-09-07, while
implementing Plan 241. ⚠️ Staging, not a production system — but it runs the real Swiss pipeline on
real feeds, so the grids it produces are the ones the code produces. The NPT argument above is
arithmetic; this is the same defect already present, with no Nepal involvement.

| | |
|---|---|
| forecasts stored | 6457 |
| carrying a sub-second `valid_time` component | 4182 (65%) |
| — of those, **uniformly** spaced (one constant phase) | **4113** |
| — of those, **non-uniform** (multiple interleaved phases) | **69 — every `_pooled` row then stored** |

The offsets come from four models — `climatology_fallback` (2637), `persistence_fallback` (961),
`linear_regression_daily` (515) and `_pooled` (69). They *look* clock-derived rather than
grid-aligned; stated as an inference, not a measurement, though the mechanism is visible in the code
— the cycle resolves an omitted reference time from an unrounded wall clock
(`flows/run_forecast_cycle.py`).

**The 4113 uniform rows are this plan's thesis with evidence attached.** A constant offset preserves
the gap between consecutive timestamps, so each reports a clean 86400-second step and passes any
check that inspects only the step. The phase is invisible — until two differently-phased series meet.

**The 69 are what happens when they do.** A sampled pooled forecast is not one grid with a blemish
but two interleaved daily series:

```
2026-09-05 00:00:00+00
2026-09-05 06:00:02.858976+00
2026-09-06 00:00:00+00
2026-09-06 06:00:02.858976+00          <- 21602 s, then 64798 s, repeating
```

⛔ These 69 do **not** have "a phase" in this plan's sense — phase is a property of ONE uniform grid
and these carry two. They are the failure mode, not an instance of the pattern. Because their stored
cadence is NULL (every measured row predates alembic `0053`) the legacy reader takes the FIRST gap,
so they read back at roughly six hours rather than daily.

Sanity checks run alongside the census: no forecast headers without values, no NULL `valid_time`s,
no duplicate `(forecast, valid_time, member)` rows, no multi-parameter forecasts.

🔴 **Re-measured on staging 2026-09-08, after `0.1.889` deployed Plan 241's write path.** Of the 377
`forecasts` rows the system has populated `time_step_seconds` on, every one stores `86400` and
**209 — 55% — sit on a non-zero, clock-derived phase** (139 `climatology_fallback` rows at 26658 s =
07:24:18 UTC, among others). Only 168 are genuinely at phase 0. The half-grid is the majority case in
the live column, not an anticipated one.

## 🔴 The strongest evidence for this plan is a live Swiss product — and OUR OWN INPUTS DISAGREE

Established 2026-09-09 by reading MeteoSwiss's own grid-product documentation, not by inference.
⭐ **This supersedes the clock-derived-timestamp argument below as this plan's primary evidence.**
That argument was about phases OUR code produces; this is a phased daily grid arriving from a national
weather service, in production, since 1961.

| Product we ingest | What "day D" covers, per the provider | As a grid |
|---|---|---|
| `meteoswiss_rhiresd` (definitive precipitation) | **06:00 UTC on D → 06:00 UTC on D+1** | `(86400 s, 21600 s)` |
| `meteoswiss_rprelimd` (preliminary precipitation) | **06:00 UTC on D → 06:00 UTC on D+1** | `(86400 s, 21600 s)` |
| `meteoswiss_tabsd` / `tmind` / `tmaxd` (temperature) | **midnight → midnight UTC** | `(86400 s, 0)` |
| `meteoswiss_sreld` (relative sunshine duration) | ⚠️ **UNRESOLVED** — product doc not yet read | — |
| `camels-ch` | ⚠️ **UNRESOLVED** — research dataset, needs its documentation | — |

**Seven forcing sources are stored** (measured on staging 2026-09-09); five are answered above, two
are open. ⛔ Do not quote a count of eight — an earlier revision did.

Verbatim, from *"Documentation of MeteoSwiss Grid-Data Products — Daily Precipitation (final
analysis): RhiresD"*: *"Daily precipitation on day D, corresponding to rainfall and snowfall water
equivalent accumulated from 06:00 UTC of day D to 06:00 UTC of day D+1."* The preliminary product's
documentation carries the identical sentence. TabsD's reads: *"representative of the average, the
minimum and the maximum from midnight to midnight UTC."*

⛔ **These are the GRID products, not the station data behind them.** The 06:00 window originates in
the manual rain-gauge network, which is read at 06:00 UTC — but the gridded analysis inherits it and
states it as its own `Variable` definition. Checked specifically, because "that is only the station
convention" is the obvious and wrong way to dismiss this.

**We store all of them stamped `00:00` and treat all of them as midnight-to-midnight**, verified on
staging 2026-09-09: one distinct time-of-day across all SEVEN stored forcing sources.

🔴 **Three consequences, in increasing order of importance:**

1. **Precipitation is displaced +6 h.** The value we label day D actually covers 06:00 on D to 06:00
   on D+1. Overnight rain falling between midnight and 06:00 is attributed to the PREVIOUS day in our
   data.
2. ⛔ **Our own inputs disagree with each other by six hours.** Every Swiss model is trained and served
   on a precipitation day and a temperature day that are not the same day. This is not a
   we-versus-provider mismatch; it is internal.
3. ⭐ **A source is a grid with a phase, and different sources feeding ONE model can carry DIFFERENT
   phases.** The plan's design assumed the difficulty was between a source and a target. It is also
   BETWEEN SOURCES, and nothing in the system can currently express that, let alone reconcile it.

📌 **Not urgent, but it must ride the retrain.** The misalignment has been consistent since 1961, so
models learned the same skew they are served — this is a wrong question modelled consistently, not a
live corruption. Correcting it invalidates every Swiss artifact, so it goes into **Plan 254 T6's
cutover** with the boundary move and Plan 262's stamping change. Owner decision 2026-09-09.

## What the code does today

- **A time step is a scalar on every path but one.** (The exception is skill scoring, below — an
  earlier revision said "everywhere", which its own next bullet refutes.) `QcRuleSet.rules_for`
  matches `time_step` by equality
  (`types/domain.py:160-166`); `services/forecast_combination.py:458` derives one uniform step. Two
  series can both report `3600` and share no instant.
  Plan 241 (#258) changed one half of this: `store/forecast_store.py` no longer derives
  `native_step_seconds` from the first pair **for a row with a persisted, non-NULL cadence** — new
  writes store `ensemble.time_step` directly. Migration `0053` is nullable-first with no backfill, so
  a row written after the migration by an older image is still NULL; the criterion is the stored
  value, not the migration date. That fixes the step, **not the phase**, and the first-pair
  derivation survives for every NULL row. The scalar-step critique stands in full.
- **`stations.timezone` is inert.** Declared as an IANA zone (`Asia/Kathmandu`, `Europe/Zurich`) and
  carried faithfully from `config/onboarding.py:131` through `store/station_store.py:136` to
  `api/routes/api_stations.py:220` — and **never read to make a decision**. Verified 2026-09-09:
  every read site is a field copy — into a row, a domain object (`store/station_store.py:355`), a
  response model, or a calculated-station construction. ⛔ The claim is about DECISIONS, not read
  count; an earlier revision said "only three read sites", which undercounts the copies. No site
  branches on it, and `zoneinfo`/`pytz` appear nowhere in `src/`. The metadata slot exists and does
  nothing.
- **Phase is not absent — it exists in exactly one subsystem.** Skill scoring already derives,
  validates and persists a phase: `_valid_time_phase_us` (`services/skill/service.py:76`) takes
  `vt.timestamp() % step`, `validate_homogeneous_time_step_and_phase` (`:112`) refuses an internally
  mixed-phase ensemble (Plan 228 D2), and `skill_scores.phase_offset_seconds` /
  `skill_diagrams.phase_offset_seconds` persist it (`db/metadata.py:1540`, `:1611`, written at
  `store/skill_store.py:520` for scores, `:545` for diagrams), gated to `computation_version >= 2`. **The skill path records the whole
  grid; the forecast path records half of one.** T2 must reuse or explicitly replace this
  representation, never duplicate it.
- **The period convention is settled per-dataset, not repo-wide.** M-D3 established the DHM
  precipitation workbook as period-ending, aligning with ERA5-Land
  (`docs/design/dhm-precipitation-milestones.md:119`), while Pyramid AWS precipitation is **NPT**
  (`docs/design/dhm-precipitation-vision.md:354`). Both timebases are already in the building, and
  the convention is recorded in a research design doc rather than anywhere an adapter author looks.

## ⛔ The architecture document says something different, in six places, and no code implements either

Measured 2026-09-09 against `origin/main` at `dc442d57`. This was not known to any previous revision
of this plan, and it is the largest single obstacle to consistent timezone behaviour: **three trusted
repo documents assert a per-station, IANA-derived day boundary — the mechanism this plan forbids.**

| Site | What it asserts |
|---|---|
| `docs/architecture-context.md:3287` | **A LOCKED DECISION:** "IANA timezone per station \| UTC storage, local display. Daily aggregation uses local day boundaries." |
| `docs/architecture-context.md:2933` | "Daily aggregation: uses the station's local timezone to define day boundaries." |
| `docs/architecture-context.md:2982` | "Daily aggregates … aggregated using local timezone day boundaries." |
| `docs/conventions.md:299-300` | "Per-station IANA timezones (from station metadata) are used for daily aggregation day boundaries." |
| `docs/design/v0-flow2-observation-pipeline.md:435`, `:446`, `:620` | Builds on the same premise, and `:620` cites the architecture as its authority. |
| `docs/plans/106-v1-critical-path-roadmap.md:148` | Records the gap as an undrafted v1.0 item: daily NWP aggregation is "UTC-bucketed … but arch requires station-local day boundaries". |

**Nothing implements it.** `zoneinfo`, `pytz` and any per-station timezone arithmetic appear **nowhere**
in `src/` — the only `astimezone` calls convert *to* UTC (`types/datetime.py:10`,
`api/forecast_lab_schemas.py:35`). `stations.timezone` is written and served and never read to make a
decision. So there are three positions, not two:

| | Boundary | Status |
|---|---|---|
| The architecture documents | per station, derived from its IANA zone | Documented as locked. Never built. |
| This plan | per deployment, declared as a fixed offset, overridable per station (OD-12) | Proposed. |
| The running code | UTC midnight, phase 0, everywhere | What actually happens. |

⛔ **A plan that contradicts a locked decision without superseding it is how the contradiction survives
review.** T9 supersedes it explicitly.

**What survives and what does not.** The architecture's *intent* is kept only in its weak form: a
daily aggregate should mean a day the local users recognise, and a Nepali day is not a UTC day
(`architecture-context.md:2933`). ⚠️ **It does NOT survive as "the local CIVIL day"** — OD-15 moved
Switzerland to a morning-to-morning observation day, which no member of the public would call a day.
Its *mechanism* — derive the boundary from each station's IANA zone — is refuted, on the measured ground that a DST-observing zone has no uniform
civil-day grid at all (OD-2: Zurich civil days run 23, 24 or 25 hours). For Nepal the two mechanisms
give an identical answer, because `Asia/Kathmandu` has no DST. **This is a supersession of mechanism,
not of intent.**

## Design

**A grid is `(step, phase)`.** Phase is the offset of the grid's marks from UTC midnight, as a
`timedelta` in `[0, step)`. Hourly UTC is `(3600 s, 0)`. Hourly NPT is `(3600 s, 900 s)`. Daily UTC is
`(86400 s, 0)`. Daily Nepali civil is `(86400 s, 65700 s)` — 18:15 UTC.

**Storage stays UTC.** `UtcDatetime` and `ensure_utc()` at boundaries are unchanged. Phase is recorded
*alongside*, never instead.

### The four boundary values, and why they are not alternatives

⚠️ Four distinct numbers appear in this plan. Read this table before quoting any of them; it is what a
deployment is configured from.

| # | Value | What it is | Status |
|---|---|---|---|
| 1 | `65700 s` (18:15Z) | Nepal's **exact civil** day boundary — NPT midnight, UTC+05:45 | Reference. Never operated on directly. |
| 2 | `64800 s` (18:00Z) | Nepal's **provisional operating** boundary — civil midnight rounded to the CLOSEST whole hour (OD-3), which for 18:15Z is 18:00Z — corroborated, not caused, by SnowMapper's UTC+6 day | **What T4 configures**, pending DHM's answer (T7). |
| 3 | `0 s` (00:00Z) | Switzerland **today**, and what SWITZERLAND keeps declaring until OD-9's parity precondition holds and Plan 254 T6 cuts over. Not a value other deployments inherit — Nepal declares row 2. | **Current for Switzerland.** Declared, never defaulted (OD-11). |
| 4 | `21600 s` (06:00Z) | Switzerland **after the cutover** — the MeteoSwiss precipitation day, fixed year-round, so every day is exactly 24 h | **Target (owner, 2026-09-10).** Requires the retrain; Plan 254 T6 owns the move. |

⛔ **`82800 s` (23:00Z) was the target until 2026-09-10 and is now WITHDRAWN.** It is kept out of this
table deliberately: quoting it is the single easiest way to reintroduce the contradiction. See OD-15.

Rows 3 and 4 are one deployment before and after a cutover. Rows 1 and 2 are the intent and what we
can operate on: a Nepali day configured at `64800 s` (18:00Z) starts at **23:45 NPT** and runs
**23:45 → 23:45 local**, a known and accepted 15-minute displacement from civil midnight.
⚠️ **Corrected 2026-09-10: an earlier revision said "00:45–23:45 local", which spans 23 hours and is
impossible on an 86400 s grid.** 18:00Z + 5:45 = 23:45 NPT, so the day begins a quarter-hour BEFORE
civil midnight, not three-quarters after it.

### Phase is measured from UTC midnight — and that is a choice

📐 The existing code derives phase by modulo against the **Unix epoch**
(`floor_to_time_step`, `services/training_data.py:244`; `services/skill/service.py:101`). For any step
that divides 24 hours the two references agree, because the epoch began at a UTC midnight — and every
step we run today (hourly, 3-hourly, 6-hourly, daily) divides it. For a step that does **not** divide
24 h they diverge, and a value meaning one thing in config would mean another in the resampler.

**Rule:** the declared boundary is a time-of-day and is therefore **midnight-referenced**; the
implementation converts to whatever reference the resampler uses rather than assuming they match.

**Constraint:** a step that does not divide 24 h is **rejected at config load**, until someone needs
one and the reference is settled deliberately rather than discovered. Rejected examples: 7 h
(1440 / 420 is not an integer) and 50 min. **90 minutes is not such an example** — it divides 24 h
exactly 16 times. ⚠️ Which config field carries a step, and therefore where this rejection acts, is
**OQ-1** below; T4 cannot be implemented until it is answered.

### Bounds and precision

**`0 <= phase < step`.** Zero is a legal and required phase — Switzerland declares exactly that
(OD-11), so a rule excluding it would forbid the only deployment we run.

**Phase is a whole number of MINUTES.** A phase with a non-zero seconds component is rejected at
config load. Minute precision is implementable, is what the config format expresses
(`daily_grid_origin = "18:00"`), and covers every case we have: NPT's 45-minute offset needs it, and
nothing we ingest is anchored to a sub-minute boundary. ⛔ Do not express this as "not an exact
multiple of the smallest unit the step is expressed in" — a `timedelta` does not preserve how it was
written, and `timedelta(days=1) == timedelta(hours=24) == timedelta(seconds=86400)`.

### A stored step alone is HALF a grid, and a half grid reads as a false whole

⛔ Any store that records a step without its phase records something true and incomplete, and the
incomplete form is not inert: a bare `86400` invites every later reader to conclude "this sits on the
daily grid at midnight", a claim the value does not make. **Recording a step therefore obliges
recording a phase.** Where a store records only one, the omission is a defect to be named in that
store, not a convention to be worked around by inference at read time — inferring the phase back from
the timestamps is how a value that was never asserted becomes one that appears to have been.

This is an established pattern here, not a new design: the skill tables already do it (see § What the
code does today). **TWO tables record a step and no phase**, verified 2026-09-09:

| table | step | phase | owner of the remedy |
|---|---|---|---|
| `forecasts` | `time_step_seconds` | — | **Plan 254 T7** |
| `hindcast_forecasts` (`db/metadata.py:1281-1294`) | `time_step_seconds`, `NOT NULL` with a positive check | — | **Plan 254 T7** (folded in 2026-09-09; formerly unowned) |

The disposition of the pre-existing `forecasts` rows is Plan 248's. This plan owns only the statement
that a step without a phase is not a grid.

### Alignment, resampling and failure

**Alignment is permitted only between identical grids.** Anything else requires an explicit, declared
resample using the aggregation method the parameter already carries (`AggregationMethod`,
`types/enums.py:178` — SUM for precipitation and reference ET, MEAN for state variables). Shifting a
series to make it fit is forbidden: a 15-minute nudge silently redistributes accumulated
precipitation.

⚠️ **OQ-7 (was "OD-16") — OFF-GRID PAIRING IS A PROPOSAL, NOT A SETTLED DECISION. Demoted
2026-09-10 after independent review; specification is Plan 263.**

⛔ **Do not implement from this section, and do not cite it as a decision.** It names a gap and
sketches a direction; the semantics an implementer needs are absent, and an independent review found
them absent in five specific ways (below). It is recorded here because **OD-15 depends on an operation
that does not yet exist**, and that dependency must be visible rather than implied.

There is a case neither rule covers, and OD-15 creates it: **the source and the target share a STEP
but not a PHASE, and the source cannot be sub-divided** — Swiss daily temperature at phase 0 feeding a
daily model at phase 21600. Resampling would need to split a daily value (OD-13 forbids it); shifting
would move a timestamp (forbidden above); refusing would discard temperature entirely. 🔴 **Under the
rules as previously written an implementer had no legal move at all.** Named here as a third
operation:

> **Off-grid pairing (SKETCH).** Each source value is paired with the target bucket it **most
> overlaps**. The value is used **unchanged**, its timestamp is **unchanged**, and the pairing records
> the **displacement** and the **overlap fraction**. The series is flagged **off-grid** for as long as
> the mismatch lasts.

🔴 **What that sketch does NOT define — the five gaps, from an independent review 2026-09-10.**
Plan 263 must settle every one before any task implements this.

1. **"Most overlaps" is meaningless for an instantaneous value.** A point has zero duration and
   therefore overlaps every bucket by nothing. The sketch was written for intervals and silently
   applied to both.
2. **No minimum overlap.** As written, a 50.1% overlap is as acceptable as 75%. Nothing rejects a
   barely-related pairing.
3. **No tie-break** when a value overlaps two buckets equally — which is exactly what happens when the
   phases differ by half a step.
4. ⭐ **The representation is self-contradictory as sketched.** It requires the timestamp to be
   unchanged AND the value to sit on the target grid. The frame carries ONE timestamp column: keep it
   and the series is not on the target grid; replace it and the "timestamp unchanged" rule is broken.
   **It needs two time fields — original support and target association — and the sketch never says
   so.**
5. ⭐ **The accumulation-versus-statistic line is directionally right but insufficient.** A daily
   maximum is a property of the interval it was measured over, and the extreme may fall entirely
   within the non-overlapping remainder. **A paired extreme is therefore NOT the extreme of the target
   bucket and must never be labelled as one.** A paired value may be a declared proxy feature; it may
   not impersonate the target bucket's statistic. This weakens the case for `tmind`/`tmaxd`
   specifically, while leaving the daily mean the strongest case.

For Switzerland: a temperature value covering `D 00:00 → D+1 00:00` overlaps the target bucket
`D 06:00 → D+1 06:00` by 18 of 24 hours, so it pairs with that bucket at an overlap of 0.75 and a
displacement of −6 h.

⛔ **Its limits, which make it honest rather than a loophole:**
- **Same step only.** It never substitutes for a resample where one is possible.
- **Never for an accumulation.** A rainfall total paired across a boundary would attribute rain to
  the wrong day with no arithmetic to justify it. Restricted to interval **statistics** (means,
  minima, maxima) and instantaneous values, where the quantity is not additive over the window.
- **Declared, never inferred.** A deployment declares that a source is paired off-grid; discovering a
  phase mismatch at runtime **refuses**, exactly as before.
- **The overlap fraction is recorded on every paired value**, so a consumer can see the approximation
  rather than having to reconstruct it.

⛔ **NOT implemented by any task yet.** Plan 254 T3/T4 previously claimed to implement it; that was
withdrawn on 2026-09-10. **Plan 263 specifies it; nothing builds it until 263 settles the five gaps.**

**Fail closed.** An undeclared phase, or a grid mismatch with no declared conversion, refuses. It does
not guess, and it does not fall back to matching on step alone — the nearest-match trap Plan 253's
review identified as *dimensionally wrong*: a rate-of-change threshold per 10 minutes is not that
threshold per day.

## Owner decisions

*Taken in a grill-me session 2026-09-04 and extended 2026-09-05 and 2026-09-07, and OD-12 on
2026-09-09. Every number below was computed, not asserted.*

⚠️ **The staging queries were NOT preserved, and that is a real gap.** An earlier revision claimed
"the commands are in the task verifications"; they are not. Every staging census in this plan — the
6457/4182/4113/69 breakdown, the per-model counts, the sanity checks, the 377/209/168 re-measurement,
the Swiss `:05`/`:55` observation marks — is unreproducible from this document and unverifiable
without a staging connection. **Re-measure before acting on any of them**; treat them as dated
observations, not as standing facts.

**OD-0 — the target grid is `(step, phase)`; the SOURCE is not assumed to be a grid at all.**
Observations arrive at whatever cadence and phase the provider sends — 10-minute on `:02` marks,
15-minute, hourly, or three manual readings a day at 08:00/12:00/16:00 with 4 h/4 h/16 h spacing.
Ingest stores native instants in UTC and grids nothing. The store already behaves this way (Swiss
observations carry `:05` and `:55` marks alongside the `:00/:10/:20` majority), so this ratifies
existing behaviour. All grid difficulty belongs to ONE component: the preparation step that maps an
arbitrary series onto a declared target grid.

**OD-1, OD-4, OD-5, OD-10 — MOVED to Plan 258.** The period-ending convention, forecast `valid_time`
labelling, interval bounds and the CF `cell_methods` vocabulary all depend on whether a value is a
point or an interval. Plan 258 states each once, by support — including **interval bounds, which are
Plan 258 T5** (owner decision 2026-09-09). ⛔ They are not an orphan; earlier revisions said so.

**OD-2 — sub-daily runs on UTC phase; daily runs on a declared local-anchored phase.** These are
different products, not an inconsistency: a daily forecast means **a named day in some local
convention**, an hourly one does not. ⚠️ **"Civil day" was withdrawn here on 2026-09-10 by OD-15.**
Switzerland's day is now an OBSERVATION day (06:00Z, the precipitation product's) and is deliberately
NOT a civil day; Nepal's is a civil day rounded to a whole hour (OD-3). The plan must not argue that a
daily forecast means a civil day — that was the reasoning behind the withdrawn 23:00Z target. Keeping
sub-daily on UTC means the hourly forcing is consumed exactly as delivered. ⚠️ **An earlier revision
added "and only instantaneous observations are interpolated" here. Deleted — OD-13 forbids
interpolation outright.** Off-grid instantaneous readings are handled by OD-14's bucket edges, which
invent nothing. ⚠️ **OQ-7's sketch also mentions instantaneous values; that overlap is unresolved and
is one of the five gaps Plan 263 must settle** — as written, a point has no overlap to be "most" of.

| Product | Grid | As UTC |
|---|---|---|
| Nepal daily | `(86400 s, OD-3)` | provisionally 18:00Z, pending DHM |
| Nepal sub-daily | `(3600 s, 0)` | UTC hours, matching forcing exactly |
| Swiss daily (target) | `(86400 s, 21600 s)` | 06:00Z → 06:00Z, matching the precipitation product (OD-15) |

**Every deployment declares a fixed offset; there is no "UTC default" special case.** A station may
override it (OD-12), but the deployment-level declaration is always required. **Switzerland's target
is 06:00Z year-round (OD-15)** — not DST-following and not UTC midnight. Every day is exactly 24 h and
the boundary never moves.

⚠️ **This is an OBSERVATION day, not an approximated civil day, and that is a deliberate change of
intent.** 06:00Z is 07:00 local in winter and 08:00 in summer, so it makes no attempt to sit near
civil midnight — the earlier 23:00Z target did, and was withdrawn on 2026-09-10. A morning-to-morning
hydrological day is long-standing practice; the honest cost is that a "day" in our Swiss output is not
the day a member of the public would mean.

⚠️ **Moving Switzerland from UTC midnight to 06:00Z requires a retrain, and the owner accepted that
(2026-09-05, target revised 2026-09-10).** Existing Swiss artifacts were trained on UTC calendar days; under OD-7 an artifact is
valid only for the cut it was trained on. **Plan 254 T6 owns the retrain and the cutover.** Until it
lands, **Switzerland stays at phase 0** — a config flip alone would feed 06:00Z days to
midnight-trained artifacts, exactly the substitution OD-7 says the model cannot detect. Skill scores
computed against UTC-day observations become invalid for the retrained artifacts, which is a
recompute through Plan 235's generation mechanism, not a silent overwrite.

⛔ **A DST-observing zone has no uniform civil-day grid at all.** Measured: Zurich civil days run 23,
24 or 25 hours (29 Mar 2026 is 23:00Z→22:00Z = 23 h; 25 Oct is 22:00Z→23:00Z = 25 h). A "day" that is
not 86400 s is not a step, so no amount of phase bookkeeping rescues it. Nepal has **no DST** —
`Asia/Kathmandu` is UTC+05:45 in all twelve months — so its civil day *is* a uniform grid. This is why
the phase is declared per deployment and never derived from a timezone: derivation would give 18:15
for Nepal and something broken for Switzerland.

**OD-3 — a daily bucket is a whole number of UTC hours; Nepal's is PROVISIONALLY 18:00Z, pending
DHM (T7).**

The operating rule, stated once (owner, 2026-09-09): **we adopt whatever boundary DHM names as
their conventional observation day, expressed as the CLOSEST whole UTC hour, and we record the
displacement that rounding introduces.**

⚙️ **The rounding RULE is itself configurable, not just the value.** Nearest is what we use now; if
DHM tells us they round down, we adopt that without a code change. A deployment declares the civil
boundary, the rounding rule, and the resulting operating boundary — the last DERIVED from the first
two, never hand-entered independently of them.

📌 **The owner has already asked DHM about the time shift; no answer as of 2026-09-09.** The whole-hour requirement is not a preference we might trade away against
DHM's answer — it is forced by the forcing. Hourly forcing cannot be cut mid-hour without apportioning
one hourly precipitation accumulation across the boundary every single day, under an assumption of
uniform rainfall within that hour, which is the least safe assumption available for convective rain.
A whole-hour bucket introduces **no assumption at all**: every value is used as delivered.

There is a good chance no rounding is needed. In Nepal any local clock time at `:45` past the hour
maps to an *exact* whole UTC hour:

| Local | UTC |
|---|---|
| 00:45 NPT | 19:00Z |
| 06:45 NPT | 01:00Z |
| **08:45 NPT** | **03:00Z** |
| 09:45 NPT | 04:00Z |

Precedent makes a `:45` boundary likely: **India's Met Department defines its rainfall day as
08:30–08:30 IST**, and IST is UTC+5:30 — also a non-whole-hour zone — which maps to exactly 03:00Z. A
national service with the same class of problem solved it by anchoring to a local clock time that
lands cleanly, and South Asian services commonly use an 08:30 or 08:45 observation day.

Until DHM answers, the provisional value is **64800 s (18:00Z)**, reached by two independent routes:
rounding civil midnight (18:15Z) to the CLOSEST whole hour, **and** SnowMapper's own daily aggregation,
which uses a solar offset of `round(centroid_lon / 15)` = UTC+6 for Nepal, whose midnight is exactly
18:00Z. The residual displacement is 15 minutes — 0.6% of the day, uniform, so consecutive days
partition the timeline exactly: no gaps, no overlaps, no drift, no value counted twice.

**No cross-system conflict.** We receive SnowMapper data as hourly UTC on the top of the hour and
aggregate it ourselves; their UTC+6 solar shift is a dashboard presentation layer, not our input. The
coincidence corroborates 18:00Z and nothing more. The same holds for any UTC-delivered source.

**The requirement is configurability, not a particular value (owner, 2026-09-07).** The design must
not hard-code 18:00Z anywhere; T4's rejection of an undeclared deployment is what keeps that honest.

**OD-6 (REPLACED by OD-13 on 2026-09-09) — resampling.** The original table permitted linear
interpolation for instantaneous values and proportional apportionment for accumulations straddling a
boundary. **Both are withdrawn.** See OD-13. What survives: coarsen freely with the parameter's
declared `AggregationMethod` (SUM for accumulations, MEAN for state variables); **refuse** a target
finer than the source's median spacing, because three readings a day cannot become 24 hourly values.
The method is determined by the series, never by the caller. Degradation is **intended** to be reported through the
existing `InputQualityFlag` channel, which Plan 253 made persistent and API-visible — no second
mechanism. ⚠️ **That is an intent, not a settled contract: see OQ-3.** The channel does not reach the
resampler, training or hindcast today, and closing the gap is Plan 254 D2.

✅ **RESOLVED 2026-09-09 (Plan 254 D3).** The repo's no-imputation rule
(`docs/touchpoint-maps.md:247-248`: missing operational-input values are gated via `max_nan`, "never
imputed / interpolated / filled") **WINS**; OD-6's interpolation and apportionment rows are withdrawn
by OD-13. ⛔ Do not describe this as an open contradiction.

**Why 15 minutes, and why phase matters more than length.** Measured straddling rates per day:

| Source | vs UTC hours | vs Nepali hours |
|---|---|---|
| 10-min on `:00` | 0 / 144 | **24 / 144** |
| 10-min on `:02` | 24 / 144 | 24 / 144 |
| **15-min on `:00`** | **0 / 96** | **0 / 96** |
| hourly on `:00` | 0 / 24 | all |

A 15-minute source on quarter-hour marks splits **nothing**, against either grid — because 15 divides
the 345-minute NPT offset exactly (23 × 15). A 10-minute source does not, even on perfect `:00` marks,
because 10 does not. **This is what to request from DHM: 15-minute data on `:00/:15/:30/:45`.** Not
"sub-hourly", and specifically not 10-minute, which is worse than 15 in a way no one would guess.

⛔ **Period convention and grid phase are INDEPENDENT.** Getting the 45 minutes right and the labelling
convention wrong yields a silent one-hour error stacked on the offset. They are recorded separately
and never conflated into one "offset" field. This plan owns the phase; Plan 258 owns the convention.

**OD-15 — SWITZERLAND ADOPTS THE PRECIPITATION DAY: 06:00Z → 06:00Z (owner, 2026-09-10). This
ANSWERS OQ-6 and WITHDRAWS the 23:00Z target.**

Two of our Swiss inputs cut the day differently — MeteoSwiss precipitation runs 06:00→06:00 UTC,
temperature runs midnight→midnight (both established from the provider's own product documentation).
They cannot be reconciled by shifting either one: re-cutting a daily total means splitting it, which
OD-13 forbids. One input must therefore be accepted as off-grid.

🔴 **THE MECHANISM FOR THAT DOES NOT YET EXIST.** The decision to align with precipitation stands —
it is the right choice and the reasoning below holds. But the operation that would carry the
off-grid temperature series is **OQ-7, an open question specified by Plan 263**, not a settled rule.
⛔ **Plan 254 T6 must not retrain Switzerland until Plan 263 lands**, because the retrain would
otherwise bake in an unspecified treatment of temperature.

⚖️ **We align with PRECIPITATION.** It is the input that drives runoff, which is what we forecast, so
the more consequential series is the exact one. **Temperature becomes the off-grid input, displaced by
6 h**, and that displacement is recorded rather than discovered — it is defensible because temperature
varies slowly and is used as a daily mean, where a six-hour window shift matters far less than it
would for a rainfall total. ⭐ **That asymmetry is exactly why pairing must never apply to an
accumulation: had we aligned the other way, precipitation would have needed it, and there is no honest
way to pair a total.** ⚠️ Review 2026-09-10 sharpened this: the daily MEAN is the strong case; a daily
minimum or maximum is weaker, because the extreme may lie in the part of the window that does not
overlap. Plan 263 decides whether `tmind`/`tmaxd` may be paired at all.

| | grid | status after cutover |
|---|---|---|
| `meteoswiss_rhiresd` / `rprelimd` (precipitation) | `(86400 s, 21600 s)` | **native — exact** |
| `meteoswiss_tabsd` / `tmind` / `tmaxd` (temperature) | `(86400 s, 0)` | **off-grid by 6 h, recorded** |
| Swiss operational day | `(86400 s, 21600 s)` | matches precipitation |

⛔ **This WITHDRAWS the 23:00Z target** taken on 2026-09-05. That target existed to sit near civil
midnight while avoiding DST; this one exists to match the data we actually receive. Both are fixed,
DST-free, exactly-24-hour days — but they are different days, and **only 06:00Z is now current.**
🔑 The two goals turned out to be incompatible, and matching the data beat approximating the calendar.

⚠️ **Consequences that must travel with this:** the Swiss "day" is a morning-to-morning observation
day, not a civil day, so any public-facing label must say so; `meteoswiss_sreld` and `camels-ch` are
still unread (Plan 252 T6) and may add a third boundary; and Nepal is untouched — its boundary comes
from DHM (OD-3).

**OD-13 — WE NEVER INVENT A NUMBER (owner, 2026-09-09).** This supersedes OD-6's interpolation and
apportionment rows and settles the standing contradiction with the repo's no-imputation rule
(`docs/touchpoint-maps.md:247-248`: missing operational-input values are gated via `max_nan`, "never
imputed / interpolated / filled"). The rule wins; the exception is withdrawn.

| Operation | Allowed? | Why |
|---|---|---|
| Estimating a value between two readings | ⛔ **NO** | Invents a measurement. On the path that raises flood alerts. |
| Splitting a total across a boundary | ⛔ **NO** | Invents a division, under an assumption of uniform rainfall — the least safe assumption available for convective rain, and the very thing OD-3's whole-hour rounding exists to avoid. |
| Combining onto a coarser grid | ✅ YES | Uses every value as delivered. |
| Refusing when neither is possible | ✅ YES | Fail closed. |

📌 **Verified 2026-09-09: nothing in `services/` interpolates or fills today** — no `interpolate`,
`fill_null` or `forward_fill`. The rule describes what the code already does; the withdrawn exception
would have been the change.

⭐ **This is why the 15-minute data request matters.** Quarter-hourly readings nest exactly into both
UTC and Nepali grids, because 15 divides the 345-minute offset (23 × 15). Ten-minute readings do not.
**Asking for the right data is what makes Nepal work without inventing anything** — it is a
requirement, not a nicety.

**OD-14 — TIMESTAMPS ARE AUTHORITATIVE; THE BUCKET EDGE MOVES, NOT THE READING (owner, 2026-09-09).**

The problem OD-13 leaves open: how do we build a 3-hourly average from readings that arrive at 00:03,
00:13, 00:23 …, on a grid the weather data defines? Not by relabelling them — a reading's timestamp is
what it is.

**Rule: a bucket runs from the reading closest to its nominal start boundary up to the reading closest
to its nominal end boundary.** The readings never move; the edges are chosen from what actually
exists. The same rule applies at daily scale, cut at the declared day boundary, and **the same
treatment is applied to the weather data**, so both sides of a comparison are cut the same way.

⚙️ **A configurable limit bounds it.** If the nearest reading to a boundary sits further away than the
declared limit, **no value is produced for that bucket** and the refusal says why. Without it, a
thinned gauge record silently yields a confident-looking average over the wrong span. Recommend the
limit default to half the source's reading interval; it is declared per deployment, and how far the
chosen edge actually sat is recorded on the value.

⛔ **Aggregate only when a model actually needs a coarser interval** (owner: *"if, and only if we have
to aggregate"*). Aggregation is never done speculatively.

**OD-7 — models are timezone-agnostic, which is precisely why the CUT must match.** Models consume
time steps and know nothing about zones; the modeller supplies steps. That is not a reason the
boundary is unimportant — it is the reason it is dangerous. Training data cut at midnight UTC and
operational data cut at 18:00Z are both "daily totals" to the model, but they are **different physical
quantities**, and nothing in the model can detect the substitution. **The binding constraint is that
training and operation use the SAME cut.** Changing a deployment's boundary after its artifacts are
trained invalidates them: free for Nepal today (no artifacts trained), not free for Switzerland.

**OD-8 — Delft-FEWS reaches the same split**, which is mild evidence we are on a trodden path. FEWS
stores internally in GMT, attaches a time-zone attribute per series, and — for *equidistant* series —
configures **fixed offsets** (`GMT+1`) rather than DST-observing zone names, reserving DST-aware zones
for display. That is the same fixed-offset-for-data, DST-for-display split arrived at here
independently. *Confidence: from knowledge of FEWS, not verified against its documentation; a
starting point, not a citation.*

**OD-9 — Plan 228 D4 is PROPOSED FOR SUPERSESSION, not reinterpretation (owner allowed 2026-09-05).**

⛔ **Read this in the future tense.** Plan 228 is `READY`, its D4 is the authoritative rule, and the
code implements it (`floor_to_time_step` / `aligned_lookback_bounds`, `services/training_data.py:244-283`). This plan is `DRAFT` — a proposal, not an
instruction (`docs/workflow.md:64-89`). **T8 is the task that would make the supersession real; until
it runs, phase zero is correct.**

D4 says *"every path aggregates onto UTC calendar buckets … nothing aligns to a forecast's own
timestamp"* (`228:124-125`). Its reasoning is **not** solely about a moving anchor — it also rests on
the repo already committing to calendar days across NWP, training and existing artifacts (`228:133`),
and its invariant is implemented in shipped code (`aligned_lookback_bounds`, `floor_to_time_step`,
both phase-zero; implementation record at `228:359-387`). That is why this is a supersession and not a
one-word narrowing. Plan 228 forbids re-opening D1–D4 (`228:321-323`), so this is recorded as an
explicit owner disposition.

What a fixed, declared, deployment-wide boundary preserves: it is the same instant every day, forever,
so the moving-anchor prohibition D4 exists to protect is untouched. What T8 must state: a fixed
non-zero phase preserves D4 **only once training, operational assembly, scoring, NWP handling, fetch
bounds and artifacts all use the same declared grid.** Until that parity holds, phase zero remains
correct. The execution half is Plan 254.

**OD-11 — Switzerland must DECLARE phase zero; absence is not a default.** An undeclared deployment
currently defaults to 0 silently, which contradicts this plan's fail-closed rule. Every deployment
declares, including the one whose answer is zero. A station override (OD-12) is optional; the
deployment declaration it falls back to is not.

**OD-12 — the boundary is DECLARED, never DERIVED; declared per deployment, overridable per station
(owner, 2026-09-09).** This supersedes the architecture's per-station-IANA mechanism — see the section
above — and it is what makes a country spanning several fixed offsets representable.

| | |
|---|---|
| **Where it is declared** | The deployment declares a required default. A station MAY carry an override. |
| **What is declared** | A fixed offset from UTC midnight, in whole minutes. Never an IANA zone name. |
| **Why not derived** | A DST-observing zone has no uniform civil-day grid at all — see OD-2. Derivation gives 18:15 for Nepal and something unrepresentable for Switzerland. |
| **Invariant** | Every station in a `StationGroup` must resolve to the SAME phase. |

⭐ **The override is nearly free, which is why it is in scope now rather than deferred.** Plan 254 T4
resolves a grid at call sites that are **already per-station**, so a per-station lookup is the same
threading work a deployment constant would need — and a deployment constant would hard-code a global
into a per-station path, which is a false simplification, not a real one.

⛔ **The group invariant is not optional and is one clause from existing.**
`services/run_group_forecast.py:95-107` (`_assert_consistent_station_inputs`) already asserts that
every station in a group shares `issue_time`, `forecast_horizon_steps` and **`time_step`** — and the
frames are then stacked row-wise onto one timestamp column (`:92`). It checks the step and not the
phase: the half-grid defect again. **Extending that assertion to the phase is Plan 254's**, listed in
its T4 scope; this plan owns only the rule. Two stations on different phases cannot share a
group artifact, because the stacked frame would carry two interleaved grids in one timestamp column —
the exact shape of the 69 rows in the census above.

**Supply-side confirmation (SnowMapper modeller, 2026-09-05):** forcing is hourly on the top of the
UTC hour with no `:45` timestamps — ECMWF native 3/6-hourly UTC interpolated to 1-hourly UTC — so
OD-2's sub-daily-on-UTC decision matches what is delivered. NPT is presentation-layer only on their
side. A known gap of theirs: point meteograms are published in UTC and should be NPT — display-layer,
not ours.

## Open decisions

These are unresolved. They are listed here rather than left implicit in a task.

**OQ-1 — ANSWERED 2026-09-09, by measurement rather than decision.** ⭐ The operational step is **not
in a config file at all**: it is declared per station-and-model pairing in the database
(`model_assignments.time_step`, `INTERVAL NOT NULL`). That is why no config field could be found — the
question presupposed the wrong home. So the rejection cannot happen "at config load".

⚖️ **Owner decision: reject at STATION ONBOARDING**, where the pairing is created and a human is
present to fix it. A non-dividing step never reaches the database and the error names the station and
the model. ⛔ Not at forecast time — that fails nightly and unattended.

*(The superseded question is kept below, because the search it prompted is what produced the answer.)*

**OQ-1 (superseded) — which config field carries the OPERATIONAL TARGET-GRID step?** T4 declares a *phase* (`daily_grid_origin = "18:00"`) and its
verification requires that a step not dividing 24 h is rejected at config load — but no field
declaring the *operational target grid's* step exists. ⚠️ **Narrowed 2026-09-09:** an earlier wording
said no step-bearing config field exists at all, which is REFUTED —
`skill_interpretation[].time_step_hours` (`config/deployment.py:59`) is one. It configures skill
interpretation bands, not the assembly target grid, so it is the wrong place for this rejection.
Either T4 gains a target-grid step field, or the rejection moves to wherever that step is actually
declared (per model, per parameter, or per channel). **T4 cannot be implemented as written until this
is answered.**

⚙️ **Where OD-13 and OD-14 are IMPLEMENTED: Plan 254 T3.** This plan states the rules; T3 is the only
task that touches the resampler, so it carries all of them — no interpolation, no apportionment,
bucket edges chosen from the nearest actual reading, the configurable refusal limit, tie-breaking, and
recording how far the chosen edge sat from nominal. ⛔ **An earlier revision stated OD-14 and named no
implementer at all.** If T3 does not carry these, nothing does.

**OQ-6 — ✅ ANSWERED 2026-09-10 by OD-15. How do two sources with DIFFERENT day boundaries feed ONE model?** Measured:
precipitation arrives on a 06:00 day, temperature on a midnight day. Same step, different phase, so
by this plan's own alignable rule they are NOT alignable — yet today we combine them silently.
⛔ **We cannot fix this by shifting either one:** re-cutting a daily total to a different boundary
means splitting it, which OD-13 forbids. The real options are to declare the model's target grid and
accept one source as off-grid with the mismatch recorded, to re-derive the 06:00 products from
sub-daily station data (expensive, and outside this system), or to adopt 06:00 as the Swiss
operational day so precipitation is native and temperature is the off-grid one. ⚠️ **This interacts
with the former 23:00Z target**, which is why answering it withdrew that target rather than sitting
beside it.

✅ **ANSWERED 2026-09-10 — see OD-15: Switzerland adopts the precipitation day (06:00Z), temperature
becomes the recorded off-grid input, and the 23:00Z target is withdrawn.** T10 propagates it.

**OQ-2 — CLOSED 2026-09-09. Interval bounds are owned by Plan 258 T5**, added as a real task by owner
decision. ⛔ **They are no longer an orphan; do not describe them as one.** *(The orphan state was
real until that task existed: this plan assigned them to 258, Plan 254 assigned them to Plan 251, and
neither carried a task. Recorded because the fix was to create an owner, not to re-point a
reference.)*

**OQ-3 — NARROWED 2026-09-09, re-widened 2026-09-10.** OD-13's no-invention rule removes
interpolation and apportionment entirely. Two things remain to report: a bucket edge that had to reach
further than the declared limit, **and — if OQ-7 is adopted — a paired value's overlap fraction,
displacement and off-grid provenance.** ⚠️ An earlier revision said the edge was the ONLY remaining
degradation; that predates OQ-7 — a far smaller signal than the original design assumed. Which channel carries it is
still Plan 254 D2, still open.

*(Original framing, still accurate about the channel:)* OD-6 said the existing `InputQualityFlag`
channel reports it. ⛔ **That is not implementable as stated, and this plan cannot settle it.**
Plan 254 measured the channel: `assess_input_quality` emits only observation staleness, NWP age and
warm-up; only `OperationalForecast` persists the pair; `HindcastForecast` has no such fields and Plan
253 excluded hindcasts; and the resampler returns a bare DataFrame with nowhere to put provenance. So
OD-6's "no second mechanism" is a *preference*, not a settled contract. **The decision is Plan 254 D2,
and it is open.** Until it closes, treat OD-6's reporting clause as an intent.

**OQ-5 — CLOSED 2026-09-09. Folded into Plan 254 T7**, which now covers `hindcast_forecasts`
alongside `forecasts` (owner decision). ⛔ **Do not describe the hindcast phase column as unowned.**
Why it mattered: hindcasts are what skill is computed from, and skill is the one path that already
partitions by phase, so a phase-blind hindcast store fed a phase-aware scorer.

**OQ-4 — ✅ CONFIRMED by the owner 2026-09-09.** OD-3's rule is authoritative: DHM's stated boundary,
rounded to the CLOSEST whole UTC hour, with the rounding rule itself configurable. ⛔ Do not describe
it as awaiting confirmation.

## Relationship to plans already in flight

This is deliberately the convention and the type, not the consumers.

- **Plan 254** owns all execution: threading the declared grid through the resampler call sites
  (⛔ **re-measure that inventory before using it; never cite a count from prose** — it moved from 6 to
  8 to 12 sites in a fortnight, and 254's own text still contradicts itself between seven and twelve),
  the fetch-bound helpers, artifact grid provenance, the `forecasts` phase column (T7), the Swiss retrain
  and cutover (T6), and the daily-model anchoring (T8, absorbed from Plan 226).
- **Plan 226 is `SUPERSEDED`** — absorbed into Plan 254 T8 on 2026-09-08. It must not be cited as a
  live owner of anything.
- **Plan 258** owns temporal support — point or interval — and therefore the period-ending
  convention, the CF `cell_methods` vocabulary and the support-dependent resampling methods. ⛔ This
  plan is buildable without it and must not re-absorb it: a grid is a step and a phase whether or not
  we have recorded what the values mean over that step.
- **Plan 234** threads each channel's declared aggregation method end to end. This plan supplies the
  rule that says *when* a resample is required; 234 supplies *how* it is performed.
- **Plan 248** owns the pre-existing `forecasts.time_step_seconds` rows. Its T2 decided **discard**
  for the 69 non-uniform `_pooled` rows, once the owner established that the mac-mini is a test
  deployment, which restored a plain `SET NOT NULL` in its T3. Nothing here waits on that and nothing
  there waits on this.
- **Plan 106** records a `to-draft` v1.0 gap — "timezone / local-day aggregation audit" (`106:148`) —
  whose evidence is exactly the mismatch T9 documents. T9 marks it owned by this plan and Plan 254;
  it must not be drafted a second time.
- **Plan 253** found the fail-closed hole in QC rule lookup. The principle is identical and is stated
  once, here, rather than rediscovered per subsystem.

## Non-goals

- All execution and rollout — Plan 254.
- Temporal support, period-ending, CF `cell_methods` — Plan 258.
- Threading declared aggregation through the assembly paths — Plan 234.
- Building any DHM or Nepali adapter.
- Retrofitting phase onto historical stored rows. ⚠️ **No write declares a phase today** — the
  `forecasts` table has no phase column at all, and adding one is Plan 254 T7, not this plan. The
  pre-existing stored population is Plan 248's, already decided.
- Changing `UtcDatetime` or the storage timezone.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:198`).

⚠️ Task IDs are non-contiguous (T1, T2, T4, T6, T7, T8, T9, T10). The gaps are from the 2026-09-05 and
2026-09-08 splits into Plans 254 and 258. **The IDs are stable and referenced by those plans — do not
renumber them.** T9 was added 2026-09-09 and takes the next free number rather than filling a gap.

### T1 — declare the conventions where an adapter author will find them

**Outcome:** phase is defined as a grid property, the NPT worked example is written down, and the DST
limitation is recorded as a limitation rather than left to be rediscovered.

**In:** `docs/conventions.md` (primary home), cross-referenced from `docs/architecture-context.md` and
`docs/spec/types-and-protocols.md`. Must carry the NPT worked example, the statement that period
convention and grid phase are **independent** (with the convention itself pointed at Plan 258), that a
DST-observing zone has no uniform civil-day grid at all, and **the three OD-6 rules this plan owns as
conventions**: coarsen with the parameter's declared `AggregationMethod`, never upsample, and refuse a
target finer than the source's median spacing. ⛔ Those three are conventions here and *behaviour* in
Plan 254 — recording them is this task's job; enforcing them is not.

⛔ **Not the period-ending convention or the CF table** — Plan 258. Name the split here so an adapter
author reading `conventions.md` finds both halves.

**Out:** any code change; the adapter audit (T6); anything in Plan 254.

**Pre-change:** N/A — documentation task. `grep -rn "grid phase\|TimeGrid" docs/conventions.md`
returns nothing; the convention lives only in `docs/design/dhm-precipitation-milestones.md:119` and a
code comment.

**Verification:** N/A — documentation task. Phase is defined, the NPT example appears, the DST
limitation is stated, the three OD-6 grid rules appear as conventions, and Plan 258 is named as the
home of temporal support.

### T2 — a typed `TimeGrid`, reusing what already exists

**Outcome:** `TimeGrid(step, phase)` exists with `0 <= phase < step`, an alignable predicate and a
nesting predicate, and it **REPLACES** skill's ad-hoc `(time_step, phase_us)` pair as the single
representation. ⛔ **"Reuse or replace" is settled here, not left to the implementer** — leaving it
open is what made the previous revision unimplementable.

**The two predicates, stated as formulas so the implementer makes no choice:**

- `a.alignable(b)` ⟺ `a.step == b.step and a.phase == b.phase`. Nothing weaker: equal steps with
  unequal phases is exactly the hourly-UTC-vs-hourly-NPT case that shares no instant.
- `fine.nests_into(coarse)` ⟺ `coarse.step % fine.step == 0` **and** `(coarse.phase - fine.phase) %
  fine.step == 0`. Both clauses are load-bearing: 10-minute-on-`:00` satisfies the first against
  hourly NPT and fails the second (`900 % 600 = 300`), which is the OD-6 table's 24/144 straddles.

⚠️ **Skill's stored phase is LOSSY, and the replacement must not inherit that.** Phase is derived in
microseconds (`services/skill/service.py:101`) and persisted floor-divided to whole seconds
(`:642`, `phase_us // 1_000_000`), so two cohorts whose phases differ sub-second collapse to one
stored value — and the census above contains exactly such phases (`06:00:02.858976`). Declared phases
are whole minutes (§ Bounds and precision) so this cannot bite a *declared* grid; it bites the
*observed* phase of existing data. Record the limitation; do not widen the declared type to carry
microseconds.

**In:** `src/sapphire_flow/types/`, `docs/spec/types-and-protocols.md`, and
`services/skill/service.py:76-112`, where phase is already derived, validated and persisted. Depends
on T1.

**Out:** deriving phase from an IANA timezone — never offered, since derivation reintroduces DST.
Threading it through call sites (Plan 254).

**Pre-change:** `uv run python -c "from sapphire_flow.types.domain import TimeGrid"` fails with
ImportError, while `grep -n "phase" services/skill/service.py` shows a second, incompatible
representation already in use — a nullable integer of microseconds, persisted as seconds.

**Verification:** `uv run pytest tests/unit/types/test_time_grid.py` — hourly UTC and hourly Nepali
are both step 3600 and NOT alignable; a 15-minute grid on quarter-hour marks nests into BOTH; **a
10-minute phase-zero grid nests into hourly UTC and NOT into hourly Nepali** (consistent with the OD-6
straddling table, which shows 0/144 against UTC and 24/144 against NPT); `phase >= step` raises;
`phase == 0` is accepted.

### T4 — declare the operational grid boundary per deployment

**Outcome:** every deployment declares its boundary explicitly, including Switzerland's zero.

**In:** the deployment config model, plus **both** config layers — `config/overlays/` **and the
repository-root `config.toml`**, which is the active Swiss base and is loaded separately from the
overlays (`load_merged_toml`, `config/deployment.py:478`; `docs/v0-scope.md:492-495`). ⛔ **The root file must be in scope**:
if the field has no default, the base config must declare it or Switzerland refuses to start. ⚙️ **THREE fields, not one** (OD-3, corrected 2026-09-09 — an earlier revision declared only the
last and could not express a configurable rounding rule):

| field | example | meaning |
|---|---|---|
| `daily_grid_origin` | `"18:00"` (Nepal) / `"06:00"` (Switzerland) | ⭐ **PRIMARY. The operating boundary in UTC, always DECLARED.** Never computed from anything else. |
| `daily_boundary_provenance` | `"civil-midnight, rounded nearest"` / `"MeteoSwiss precipitation day"` | why that value; **optional, and descriptive only** |
| `daily_civil_boundary` | `"00:00"` | OPTIONAL — the local civil boundary, only where one exists |
| `daily_boundary_rounding` | `"nearest"` \| `"down"` | OPTIONAL — used only to CHECK a declared origin against a declared civil boundary, never to produce it |

🔴 **Corrected 2026-09-10. An earlier revision made `daily_grid_origin` DERIVED from a civil boundary
plus a rounding rule. That was wrong twice over:** it reintroduced derivation, which OD-12 forbids
("declared, never derived"); and **it cannot express Switzerland at all** — 06:00Z is an observation
day inherited from the data provider, and there is no civil midnight anywhere that rounds to it. The
derived design fitted Nepal and silently excluded the only deployment we actually run.

⚙️ Where both a civil boundary and a rounding rule ARE declared (Nepal), the config **validates** that
the declared origin follows from them and refuses a mismatch. Where they are absent (Switzerland), the
origin stands on its own with its provenance recorded. Changing the rounding rule therefore changes
what is *accepted*, not what is *computed*.
| `bucket_edge_tolerance` | `"7m30s"` | OD-14's limit: how far the reading nearest a bucket boundary may sit from nominal before the bucket is REFUSED. Default: half the source's reading interval. **Consumed by Plan 254 T3.** |
| `daily_grid_origin_overrides` | per station | OD-12's optional per-station override, same validation as the deployment value |

Where a civil boundary is declared, the displacement between it and the operating boundary is computed
and **recorded on every value cut with it**, so it is stated rather than discovered. Where none is
declared, the provenance string carries that role. **Also
`docs/spec/config-reference.toml`**, which states that it documents every config field — a new field
absent from it breaks that promise. Depends on T2.

**In (also):** the optional per-station override of OD-12 — a nullable field alongside the existing
station metadata, validated to the same rules as the deployment value (whole minutes, `0 <= phase <
step`). Absent means "use the deployment declaration", which is the only defaulting this plan permits,
because the deployment value is itself required.

**In (also) — the station-onboarding step rejection (OQ-1).** The operational step lives on the
station-and-model pairing (`model_assignments.time_step`), so onboarding must refuse a step that does
not divide 24 h, naming the station and the model. ⛔ **This was a decision with no task until
2026-09-10** — T4 carries it because T4 is where grid validation lives, even though the field is not
in config.

**Out:** deriving it from `stations.timezone`, which stays descriptive metadata and is never read to
make a decision. Enforcing the group-uniformity invariant (Plan 254 T4).

**Pre-change:** `grep -rn "grid_origin\|grid_phase\|daily_grid" config/ config.toml src/sapphire_flow/config/`
returns nothing (verified 2026-09-09); no deployment declares a boundary.

**Verification:** `uv run pytest tests/unit/config/` — Nepal parses `"18:00"` to 64800 s;
Switzerland's root `config.toml` declares `"00:00"` → **0 s and is ACCEPTED** (phase zero is legal and
required, not an omission); a phase with a non-zero seconds component is **REJECTED**; a deployment
with **NO** declaration is **REJECTED** rather than defaulting to zero (OD-11); **onboarding refuses a
station-and-model pairing whose step does not divide 24 h**, naming both; `bucket_edge_tolerance`
parses and is readable by the resampler; **Switzerland declares `"06:00"` with a provenance string and
NO civil boundary, and is ACCEPTED**; a declared origin that contradicts a declared civil boundary and
rounding rule is **REJECTED** rather than silently believed; and switching the rounding rule from
`nearest` to `down` changes **which declared origins are ACCEPTED** — `18:00` passes under both, while
`19:00` passes only under `nearest` — **without a code change**.

⛔ **The rounding rule never COMPUTES the origin.** An earlier revision's verification said switching
the rule "changes the derived origin", contradicting the design above, where the origin is declared
and the rule only validates it. No correct implementation could satisfy both.

⚙️ **Where the step check lives.** T4 validates the PHASE in config; the non-dividing-step check runs
at **station onboarding**, because the step is on the station-and-model pairing, not in config (OQ-1).
Both are in T4's scope — the task spans the two places grid validity is decided. ⚠️ **An earlier
revision of this task said the rejection was NOT in T4 while its In-section added it, a flat
contradiction introduced on 2026-09-10.**

⛔ **Removed: "a value finer than the step's resolution is rejected."** It is not implementable — a
`timedelta` does not retain how it was written, which is the same reason the precision rule is stated
as whole minutes. The whole-minutes check is the whole of it.

⚠️ **No Nepal deployment profile exists** under `config/` or `config/overlays/` (verified 2026-09-09),
so "Nepal parses `18:00`" must be exercised against a test fixture. Creating a real Nepal profile is
deployment work and is **out of scope here**.

### T6 — audit every input adapter against the conventions

**Outcome:** each adapter's **native grid phase** is recorded — confirmed, converted, flagged
unresolved, or **`NOT A GRID`**.

⭐ **This task has already produced its most important finding, and the method is the point.** Reading
the provider's own product documentation established that MeteoSwiss precipitation runs 06:00→06:00
while its temperature runs midnight→midnight (see the evidence section above). **No automated check
would have found that** — the metadata is not in the files, and the gateway strips what little there
is. ⛔ **The audit is a DOCUMENTATION-READING task, not a data-inspection one.** Three of eight Swiss
sources are answered. ⚠️ **Count: SEVEN forcing sources are stored** (an earlier revision said "three
of eight"; both numbers were wrong)
(`camels-ch`, `meteoswiss_rhiresd`, `meteoswiss_rprelimd`, `meteoswiss_sreld`, `meteoswiss_tabsd`,
`meteoswiss_tmind`, `meteoswiss_tmaxd` — measured on staging). **Five are answered** (the three
temperature products share one document); `meteoswiss_sreld` and `camels-ch` are open.

⛔ **The fourth outcome is required by OD-0.** A source is not assumed to be a grid at all; a
manually-read gauge or an event-triggered series has **no phase**, and recording that as "unresolved"
would misfile a known fact as an open question. `NOT A GRID` is a terminal, correct answer.

*(Temporal support gets its own audit in Plan 258 T4. Keep the two tables adjacent in
`docs/conventions.md` but do not merge them — they are independent facts, and merging them is how one
silently stands in for the other.)*

**In:** `src/sapphire_flow/adapters/`, recorded as a table in `docs/conventions.md`. Depends on T1.

⛔ **Inclusion rule and row key, so "every adapter appears" is checkable.** `adapters/` also holds
ForecastInterface code, rate limiting, status adapters, factories, readers and helpers — those are
out. **In scope:** any adapter that yields timestamped observation, forcing or forecast values into
the system. **Row key: `(adapter, product/series)`, not `(adapter)`** — one adapter can expose several
products at different cadences, and one row per adapter would let one product's phase silently stand
for another's.

⚠️ **This key is the GRID audit's, and it does not settle Plan 258's cardinality question.** An
earlier revision of this task claimed it was the "same grain as Plan 258 T4", which pre-empts 258's
D1 — still open between `(source, parameter)` and the adapter channel. Grid phase and temporal
support are independent facts (OD-6) and may well be recorded at different grains; if 258 settles on
a different key, that is not a conflict with this table.

**Out:** fixing a non-conforming adapter — each is its own change.

**Pre-change:** N/A — audit task. No such record exists, and both timebases are already in the
building undocumented: the DHM workbook is UTC period-ending
(`docs/design/dhm-precipitation-milestones.md:119`) while Pyramid AWS is NPT
(`docs/design/dhm-precipitation-vision.md:354`).

**Verification:** N/A — audit task. Every adapter appears with a cited source, or is marked
unresolved.

### T7 — make the DHM day-boundary questions EXACT

**Outcome:** Q3.3 and Q8.3 are sharpened so that DHM states their conventional day boundary **as a
specific local clock time**, which we then round ourselves per OD-3.

⛔ **Do NOT ask them to give us a whole UTC hour.** An earlier revision did, which discards the very
fact we need: their civil boundary is what gets configured, and the rounding is ours to apply and
record. Asking for a pre-rounded answer would also hide whether their boundary lands cleanly — and a
`:45` local boundary needs no rounding at all.

⚠️ **The questionnaire already asks this.** `docs/requirements/dhm-data-formats-questions.md` asks
**Q3.3** ("How is daily flow defined? Daily mean / instantaneous / max? … day boundary") and **Q8.3**
("Time precision: exact timestamp format, the UTC offset (NPT = +05:45), seconds precision, and the
definition of a day"). The task is to make existing questions precise enough to settle OD-3, not to
add a missing one.

**In:** `docs/requirements/dhm-data-formats-questions.md`, amending Q3.3 and Q8.3. **Two** things must
be answerable afterwards: the conventional day boundary in local time, resolved to a specific hour;
and a request for **15-minute data on `:00/:15/:30/:45`** (10-minute is worse for Nepali targets, since
10 does not divide the 345-minute offset and 15 does). Cite the India 08:30 IST precedent — it makes
the question read as a familiar convention rather than an unusual demand.

⛔ **Period convention per parameter is NOT asked here.** An earlier revision listed it as a third
item, contradicting this plan's own frontmatter and the sentence below. It is Plan 258's, and it will
need its own question.

⛔ **The Gateway CF-metadata ask belongs to Plan 258**, with the temporal-support work it serves. It is
not this plan's, and this plan is not blocked on it. The snow modeller is likewise out of scope here.

**Out:** re-rendering the `.docx`; assuming an answer; the period-convention question (Plan 258).
Unanswered leaves OD-3 on its provisional value.

**Pre-change:** N/A — requirements task. Q3.3 and Q8.3 exist (`docs/requirements/dhm-data-formats-questions.md:158`
for Q8.3) but neither forces an answer naming a specific hour, so DHM could answer both fully and
leave OD-3 unresolved.

**Verification:** N/A — requirements task. Q3.3 and Q8.3 each demand a specific hour, the 15-minute
grid request appears, and the India 08:30 IST precedent is cited.

### T10 — PROPAGATE OD-15 (which answered OQ-6) through every affected document

**Outcome:** OD-15's decision is written through every affected document, so no superseded target
survives anywhere.

✅ **The DECISION is taken (OD-15, owner 2026-09-10): Switzerland adopts the precipitation day,
06:00Z.** This task is no longer about choosing — it is about propagating, which is exactly the step
this family has failed three times running.

**In:** this document; `docs/conventions.md`; `docs/architecture-context.md` (with T9); and any place
naming a Swiss target boundary. ⛔ **Sweep for `82800`, `23:00Z`, "UTC+1 year-round" AND "civil day"**
— the withdrawn target is the easiest contradiction to reintroduce, and its RATIONALE survived the
first sweep even though its value did not.

**Out:** any code; the retrain itself (Plan 254 T6); re-deriving any product.

⚙️ **Who implements the answer: Plan 254 T4.** ⛔ **T10 produces only a DECISION, and a decision with no
executor is how this family kept stalling.** OD-15's selection — Switzerland adopts the 06:00 day,
temperature recorded as the off-grid input — is threaded through the assembly paths by 254 T4, which is already the task that resolves a target grid at every call site. Plan 254 T6
consumes the result; it does not implement it.

**Pre-change:** N/A — decision task. The evidence section above measures the conflict; no plan
resolves it, and Plan 254 T6 is blocked on it.

**Verification:** N/A — propagation task. ⛔ **The gate is that every match is a withdrawal note, a
historical changelog entry, or the unrelated DST example — NOT that the count is small.** A
2026-09-10 sweep returned 13 matches across Plans 239, 248, 252 and 254, every one legitimate; an
earlier wording of this gate demanded "only OD-15" and was therefore unpassable. Also: one Swiss
operating boundary (`21600 s`) appears throughout; the temperature displacement is stated wherever the
Swiss grid is named; and **no surviving passage argues the Swiss day is a CIVIL day.**

### T8 — supersede Plan 228 D4

**Outcome:** D4 is superseded, not reinterpreted, with the owner's disposition recorded and the parity
precondition stated.

⛔ **This task is the ONLY thing that makes the supersession real.** Until it runs, D4 stands and phase
zero is correct.

**In:** `docs/plans/228-hindcast-and-skill-on-wrong-data.md` § D4 (`:122-149`), its implementation
record (`:359-387`), the decision document, `docs/touchpoint-maps.md` **and D4's locking tests** —
amending the rule without amending the tests that pin it leaves the old rule enforced in CI. Must preserve the moving-anchor
prohibition, exactly-N-complete-buckets, and align-and-extend; must state that phase zero remains
correct until training, assembly, scoring, NWP, fetch bounds and artifacts all share a declared grid.
Depends on T1.

**Out:** re-opening D1–D3, the shipped P1/P2 fix, or anything 228 assigns to Plan 234/235. Changing
any phase-zero behaviour — that is Plan 254.

**Pre-change:** N/A — documentation task superseding a settled decision. `228:124-125` reads "Every
path aggregates onto UTC calendar buckets … Nothing aligns to a forecast's own timestamp", which
forbids daily forecasts for any region not on UTC.

**Verification:** N/A — documentation task. D4 still forbids a moving anchor; the owner disposition
and the parity precondition are both recorded.

### T9 — supersede the per-station IANA day-boundary decision across the architecture docs

**Outcome:** the six sites listed above state OD-12's mechanism, the locked decision carries the
owner's disposition, and the undrafted roadmap gap is closed rather than left to be redrafted.

⛔ **This task is the only thing that makes OD-12 authoritative.** Until it runs, the architecture's
per-station-IANA mechanism is the documented rule and this plan contradicts it. It follows the same
pattern as T8 (which supersedes Plan 228 D4): an owner disposition recorded in the document it
amends, never a silent edit.

**In:** `docs/architecture-context.md:3287` (the locked-decision row), `:2933` and `:2982`;
`docs/conventions.md:299-300`; `docs/design/v0-flow2-observation-pipeline.md:435`, `:446`, `:620`;
and `docs/plans/106-v1-critical-path-roadmap.md:148`, whose "v1.0 timezone / local-day aggregation
audit" gap this plan and Plan 254 together close — mark it owned, do not leave it *to-draft*.

Each site must state: the boundary is **declared**, not derived; declared per deployment with an
optional per-station override; always a fixed offset, never an IANA zone name; and every station in a
station group resolves to one phase. **Keep the intent** — a daily aggregate still means the local
hydrological day — and record *why* the mechanism changed, so the DST reasoning is not rediscovered.

**Out:** changing any behaviour (Plan 254); `stations.timezone` itself, which stays descriptive;
re-opening any other locked decision.

**Pre-change:** the six sites above, verified 2026-09-09, assert a per-station IANA-derived boundary
while `grep -rn "zoneinfo\|pytz" src/` returns nothing — a documented architecture with no
implementation, now contradicted by this plan.

**Verification:** N/A — documentation task. No site still derives the boundary from a station's IANA
zone; the locked-decision row carries the owner disposition and the DST rationale; the roadmap gap
names its owning plans.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/252-time-grids-are-step-and-phase.md
```

Four conditions hold in addition:

1. **One Nepal OPERATING boundary value appears throughout** — `64800 s` (18:00Z) until DHM answers.
   The plan deliberately distinguishes four values (civil reference `65700`, Nepal operating `64800`,
   Switzerland today `0`, Switzerland after cutover `21600`); the gate is that no *second operating*
   value for Nepal appears, and that **every surviving `82800` / `23:00Z` match is a withdrawal note, a
   historical changelog entry, or the unrelated DST example — never a live target.** ⛔ The gate is the
   NATURE of each match, not the count; an earlier wording demanded "only OD-15" and was unpassable
   against 13 legitimate matches. **And sweep the RATIONALE too** — "civil day", "UTC+1 year-round" —
   because on 2026-09-10 the value was withdrawn everywhere while the argument for it survived.
2. **No deployment defaults to phase zero by omission** (OD-11), including the repository-root
   `config.toml` that the Swiss deployment actually loads — not only `config/overlays/`.
3. **D4's supersession is PROPOSED with the parity precondition stated**, not asserted as done —
   Plan 228 stays authoritative until T8 lands.
4. **OQ-1 is answered before T4 ships**, and OQ-2 is answered before Plan 254 or 258 proceeds past the
   tasks that need interval bounds.
5. **No document still derives the day boundary from a station's IANA timezone** (T9), and no
   document still presents the per-station-IANA mechanism as locked.

## Dependency graph

```json
{
  "plan": 252,
  "nodes": [
    {"id": "T1", "phase": 1, "depends_on": []},
    {"id": "T2", "phase": 1, "depends_on": ["T1"]},
    {"id": "T6", "phase": 1, "depends_on": ["T1"]},
    {"id": "T7", "phase": 1, "depends_on": []},
    {"id": "T4", "phase": 2, "depends_on": ["T2"]},
    {"id": "T8", "phase": 2, "depends_on": ["T1"]},
    {"id": "T9", "phase": 2, "depends_on": ["T1"]},
    {"id": "T10", "phase": 1, "depends_on": [], "note": "PROPAGATES OD-15 (which ANSWERED OQ-6); Plan 254 T6 waits on it landing, not on a decision"}
  ]
}
```

## Changelog

⛔ **This section replaces the inline correction notes.** A correction note that leaves the wrong text
in place does not fix a contradiction — it documents one. Every entry below is already applied above;
nothing here needs reconciling against the body.

**2026-09-09 — the architecture contradiction, folded (owner, same day).** A measured sweep found
that `docs/architecture-context.md` (three sites, one of them a **locked decision**),
`docs/conventions.md` and `docs/design/v0-flow2-observation-pipeline.md` all assert a per-station,
IANA-derived day boundary — the mechanism this plan forbids — while **no code implements it** and the
running system is UTC throughout. Previous revisions of this plan never noticed. Added: the measuring
section before § Design, **OD-12** (declared not derived; per deployment with an optional per-station
override; group-uniform), and **T9** to supersede the six sites and close Plan 106's undrafted gap.
⚖️ **Owner disposition:** support multi-timezone countries now if it is cheap, else keep one boundary
per deployment. Measured cheap — Plan 254 T4 resolves a grid at call sites that are already
per-station, and `_assert_consistent_station_inputs` already checks a group's shared `time_step` and
is one clause from checking its phase. The override is therefore IN scope; T4 gained it and its
enforcement is Plan 254's.

**2026-09-09 — independent Codex review of the rewrite, folded.** 9 blockers, 5 majors. ⭐ **Three
were losses the rewrite itself introduced**, which is the specific risk of rewriting and the reason
the rewrite was reviewed rather than shipped: T8 had dropped "and the locking tests" from what the
supersession must amend; the scope had dropped the prohibition on citing Plan 254's call-site count
from prose; and this Changelog had dropped the "26 findings: 20 blockers" measurement from the
2026-09-05 round. All three restored.

Also folded: T2 now SETTLES reuse-vs-replace and states both predicates as formulas (it was
unimplementable as an implementer's choice), and records that skill's stored phase is lossy —
derived in microseconds at `services/skill/service.py:101`, persisted floor-divided to seconds at
`:642`. T1 now carries the three OD-6 rules this plan owns. T6 gained an inclusion rule and the
`(adapter, product)` row key. T7 regained its mandatory `Pre-change` and dropped a period-convention
question that belongs to Plan 258. T4 dropped an unimplementable "finer than the step's resolution"
check and notes that no Nepal profile exists. OQ-3 and OQ-4 were added — OD-6's reporting channel is
an intent gated on Plan 254 D2, not a settled contract; OD-3 needs owner confirmation.

⚖️ **Citations corrected after verification.** All four Plan 228 references were wrong and had been
carried forward unchecked: D4 is `:122-149` not `:121-147`, the quoted rule `:124-125` not `:123`, the
rationale `:133` not `:132`, the implementation record `:359-387` not `:294`, and the
do-not-reopen prohibition `:321-323` not `:245`. Also corrected: `docs/v0-scope.md:492-495`,
`config/deployment.py:468`, `store/skill_store.py:545`. Two of this plan's own claims were narrowed
after being refuted — "a time step is a scalar everywhere" (skill is the exception, as the next bullet
already said) and "only three timezone read sites" (there are more; the claim that holds is that none
makes a decision). ⚠️ OQ-1's premise was REFUTED as written: `skill_interpretation[].time_step_hours`
IS a step-bearing config field, so the question narrowed to the *operational target-grid* step.

⚠️ **The staging censuses are unreproducible** — no queries were preserved. Recorded as a gap under
§ Owner decisions rather than left as an implied guarantee.

**2026-09-09 — consolidating rewrite.** Three review rounds (8 → 7 → 6 blockers) failed on layered
corrections rather than on reasoning. Each decision is now stated once, in its settled form. Also
applied in this pass:

- **OD-3 stated as one rule.** The plan previously said both "adopt DHM's boundary if it maps to a
  whole UTC hour" and "whatever DHM names is what we adopt", unconditionally. Reconciled: we adopt
  DHM's boundary, expressed as the CLOSEST whole UTC hour, recording the displacement. ⚠️ *(This entry
  originally read "at or before it" and "flagged for owner confirmation"; the owner confirmed the
  CLOSEST-hour rule on 2026-09-09 — see OD-3 and OQ-4. Corrected here rather than left to contradict
  them.)* The
  whole-hour constraint is forced by hourly forcing, not a preference. **Flagged for owner
  confirmation** — this is a reconciliation of two texts, not a new owner decision.
- **`docs/workflow.md:378-390` corrected to `:198`** (measured 2026-09-09). The same stale citation
  is still present in Plan 254.
- **Two open questions promoted out of the prose.** OQ-1 (T4 rejects a non-dividing step with no
  step-bearing config field) and OQ-2 (interval bounds assigned to 258 by this plan, to 251 by 254,
  and carried by neither) were previously invisible.
- **Citations re-verified 2026-09-09** against `origin/main` at `dc442d57`: `types/domain.py:160-166`,
  `types/enums.py:178`, `db/metadata.py:1540`/`:1611`, `store/skill_store.py:520`,
  `config/deployment.py:452`, `config/onboarding.py:131`, `store/station_store.py:136`,
  `api/routes/api_stations.py:220`, `services/forecast_combination.py:458`,
  `services/training_data.py` (`floor_to_time_step`) all CONFIRMED.
  ⚠️ **Re-measured 2026-09-09 after rebasing onto `071b62e3`** (PR #268 merged mid-session and shifted
  `training_data.py` by 7 lines): `floor_to_time_step` is `:244`, `aligned_lookback_bounds` `:261`,
  `resample_to_time_step` `:286`, `load_merged_toml` `deployment.py:478`,
  `_assert_consistent_station_inputs` `run_group_forecast.py:99`. 🔑 **Correcting a line citation and
  then rebasing re-stales it — cite the SYMBOL, and treat the line as a hint.** Skill's phase machinery is at
  `services/skill/service.py:76-112`, not `:110`. `forecasts` confirmed to carry no phase column;
  `stations.timezone` confirmed to have no decision-making read site.

**2026-09-08 — the three splits and the disposals.** Temporal support (OD-1, OD-4, OD-5, OD-10) moved
to Plan 258 after a review returned three blockers all belonging to it: wrong cardinality, an unsafe
fail-closed migration, and an unrepresentable "unknown". Plan 226 was SUPERSEDED into Plan 254 T8. The
Swiss retrain was confirmed to belong to Plan 254 T6, not to Plan 226 — verified against 226's own
frontmatter and body, in which the words "retrain" and "artifact" do not appear at all. The feared
**double retrain does not exist**: it rested on 226 causing a retrain, and 226 causes none. Plan 248
T2 chose **discard** for the 69 non-uniform rows, superseding an earlier quarantine design that would
have frozen those rows, since a CHECK constraint is re-evaluated on UPDATE.

**2026-09-05 — four errors of reasoning recorded rather than quietly fixed** (from an independent
review returning **26 findings, 20 of them blockers**).

1. **The D4 argument was wrong.** The first draft claimed D4's reasoning is "entirely about a moving
   anchor". It is not: D4 also rests on NWP, training and artifacts all representing UTC calendar days
   (`228:132`), and its invariant is implemented in shipped code (`228:294`). This is a supersession,
   not a narrowing — see OD-9.
2. **A test contradicted this plan's own table.** The draft's `TimeGrid` test asserted a 10-minute
   grid nests into "neither" hourly grid; the OD-6 table shows it straddles 0 of 144 intervals against
   UTC hours, so it nests exactly. Corrected in T2.
3. **"Phase is absent everywhere" is false.** Skill scoring already validates, partitions and persists
   `(time_step, phase)`. T2 must reuse or replace it, not pretend it is new.
4. **"Every input is period-ending" is invalid for instantaneous data** — the narrowing now lives in
   Plan 258.
