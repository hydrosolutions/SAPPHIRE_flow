---
status: DRAFT
created: 2026-09-08
plan: 258
title: A value is a point or an interval, and nothing in the system records which
scope: The temporal-support half of the time-grid family, split out of Plan 252 on 2026-09-08 after an independent review returned three blockers all belonging to this one concern. Adopt CF `cell_methods` as the vocabulary, settle at WHAT CARDINALITY temporal support is recorded, narrow period-ending labelling to interval-valued data, and verify a declaration against the source once the Gateway passes CF attributes through. Explicitly NOT `TimeGrid(step, phase)` or the day boundary (Plan 252), NOT the phase-aware execution (Plan 254), NOT the aggregation METHOD declaration (Plan 234).
depends_on: [252]
blocks: [254]
source: 2026-09-08 — split out of Plan 252 by owner decision, after a Codex review returned 8 blockers of which 3 (wrong cardinality, unsafe migration, unrepresentable unknown state) were all about temporal support and none about grids
---

# Plan 258 — a value is a point or an interval

## Status

**DRAFT.** Split out of Plan 252 on 2026-09-08. Two of its four open decisions are genuine design
questions that were never asked, which is why bundling this with the grid convention kept failing
review: the grid half is *substantially* settled and this half is not.

⚠️ **Corrected 2026-09-09: "the grid half is settled" was too strong.** Plan 252 carries five open
questions of its own (OQ-1 … OQ-5), two of which — the operational target-grid step field, and the
operational target-grid step field — block this plan and Plan 254 rather than 252 itself.

## Why this is a separate plan

Plan 252 answers **where the day starts** — a grid is a step and a phase. This plan answers **what a
value means over its step** — is it sampled at the instant, or accumulated/averaged across the
interval that ends there.

They arrived together because CF supplies the vocabulary for both, and they were reviewed together
twice. The second review returned eight blockers; three were about temporal support alone
(cardinality, migration safety, an unrepresentable "unknown" state) and none of the remaining five
touched it. That is the split line.

⛔ **They are independent and must not be re-merged.** Getting the phase right and the support wrong
gives you a correctly-bucketed sum of instantaneous samples — silently wrong, and wrong in a way no
timestamp check catches.

## The vocabulary — CF `cell_methods` (moved from Plan 252 OD-10, unchanged)

CF already supplies this, and **our upstream data already carries it** — confirmed by the SnowMapper
modeller: `time: point` for SWE and snow depth, `time: sum` for runoff, alongside `units` and
`long_name`, int16-packed with CF `scale_factor`.

| `cell_methods` | Temporal support | Period-ending? | Which bucket it falls in |
|---|---|---|---|
| `time: point` | instantaneous | **No — a point is not an interval** | by its own instant |
| `time: sum` | interval accumulation | Yes | by the interval it closes |
| `time: mean` / `time: maximum` | interval statistic | Yes | by the interval it closes; combine with the declared `AggregationMethod` |

🔴 **Cross-grid METHODS removed 2026-09-09.** The last column previously read "linear interpolation"
and "overlap apportionment" — **both are forbidden by Plan 252 OD-13.** Temporal support no longer
selects a method; it says which bucket a value belongs to, and the bucket edges come from Plan 252
OD-14. Nothing here invents a value.

⚠️ **The CF token is `maximum`, not `max`.** `max` is our `AggregationMethod` member; `maximum` is the
CF cell method. They are not interchangeable and the wrong one would not round-trip.

⛔ **We currently discard all of it.** The recap adapter reads no CF attributes — that is the claim
that matters and it is CONFIRMED. ⚠️ The narrower claim that `cell_methods` "appears only as a comment
at `adapters/era5_land_reanalysis.py:20` and in an archived plan" is REFUTED (2026-09-09): further
active comments exist at `adapters/recap_gateway.py:140`, `:151`, and several active docs mention it.
**Nothing READS it at runtime**; the string is not rare.

## Period-ending, stated once and correctly

⛔ **This is the contradiction the review found in Plan 252, and it exists because the rule was
written before the point/interval distinction was.** Plan 252 said in one place that *every* input
series is period-ending, and in another that only interval-valued data is. Only the second is true.
The rule now lives here, in one place, and binds only interval data:

- **Interval-valued data is PERIOD-ENDING.** A precipitation value stamped `16:00` is the quantity for
  `15:00 → 16:00`. This ratifies existing practice (M-D3 established it for DHM and ERA5-Land).
- **Instantaneous data is NOT.** A river stage reading at `08:00` is a point at `08:00`, not an
  interval ending there. Applying period-ending to it is a category error.
- **Forecast `valid_time` follows the same rule**, by support and not blanket. An interval-valued
  daily forecast bucket is stamped on its closing boundary; an instantaneous forecast is stamped at
  the instant it describes. *(Plan 252 OD-4 previously said forecast `valid_time` is period-ending
  without qualification. That is narrowed here.)*
- **An adapter for a source that publishes period-BEGINNING must convert at the boundary and record
  that it did** — never pass the timestamps through and leave a one-hour bias to be found downstream.
- **A source whose convention is unknown is not ingested on an assumption.** Resolve it with the
  provider, or mark the series as carrying an **unresolved ±1 step LABELLING uncertainty** — the value
  may belong to the interval before or after its stamp. ⚠️ It is NOT a *phase* uncertainty: shifting a
  series by a whole step leaves its phase modulo the step unchanged, which is precisely why the two
  concepts are independent, as the next paragraph says.

⛔ **Period convention and timezone phase are INDEPENDENT.** Getting the 45 minutes right and the
labelling convention wrong yields a silent one-hour error stacked on the offset. Record them
separately; never conflate them into one "offset" field.

## 🔴 Open decisions — these are why the plan is DRAFT

### D1 — ANSWERED 2026-09-09: **per (source, parameter)**

⚖️ **Owner decision.** Support is recorded against the combination of source and measurement, because
that is where the fact lives — a provider computed it that way. `ForcingSource` already keys this
granularity and `historical_forcing` carries both in its natural key (`db/metadata.py:824-877`), so
the slot fits what exists. Rejected: one row per canonical parameter (refuted below); the adapter
channel (a runtime notion, while this is a property of the data).

⚠️ **Still open within D1:** whether a series WE transformed carries the support of its input or of
the transform. A daily mean we computed from hourly points is an interval, and nothing records that we
made it one.

### D1 (evidence) — why per-parameter was refuted

The Plan 252 draft put a single `POINT | INTERVAL` value on `ParameterDefinition` and the
`parameters` row. **That is wrong, and demonstrably so.** The same canonical parameter has different
support depending on which product it came from:

🔴 **The original worked example was WRONG and is replaced (owner correction, 2026-09-09).** It
claimed gateway temperature is instantaneous. It is not: the gateway delivers temperature **averaged
over a time span** and precipitation **summed over one**, then averages both over an **area**. The
code corroborates — `era5_land_reanalysis.py:10-16` documents `temperature_2m_mean` and a
`cell_methods: time: sum` accumulation, and `recap_gateway.py:71`, `:195` document basin-average-only
delivery. Both sides of that example are intervals, so it proved nothing.

**The example that does hold:**

| canonical parameter | gauge / weather station | any gridded product |
|---|---|---|
| `temperature` | a thermometer read at 09:00 — **a point** | `TabsD` daily mean (`adapters/meteoswiss_open_data_reanalysis.py:208-212`), ECMWF hourly mean — **an interval** |

One row per canonical parameter cannot hold both, which is what D1 answers.

⭐ **A THIRD kind of support was surfaced by the same correction and is NOT this plan's:** gateway
values are averaged over an **area**, not measured at a point in space. Nothing records spatial
support either. Noted here so it is not lost; it needs its own owner.

One row per canonical parameter cannot hold both. There are **11 canonical parameters** today
(`discharge`, `humidity`, `precipitation`, `radiation`, `reference_et`,
`relative_sunshine_duration`, `snow_depth`, `swe`, `temperature`, `water_level`, `wind_speed`) and at
least `temperature` is already ambiguous; the others must be audited rather than assumed clean.

**Candidate homes, and the recommendation:**

1. ⭐ **The source series — (source, parameter).** Recommended. It is where the fact actually lives:
   `TabsD` is a daily mean because MeteoSwiss computed it that way. `ForcingSource` already keys this
   granularity, and the adapters already know which product they fetched.
2. The adapter channel. Nearly the same thing in practice, but channels are a runtime concept and
   the fact is a property of the data, not of who read it.
3. The canonical parameter. **Rejected** — refuted by the table above.

**What the owner must settle:** option 1 or 2, and then whether a *transformed* series (one we
resampled) carries the support of its input or of the transform. A daily mean computed by us from
hourly points is an interval, and nothing currently records that we made it one.

### D2 — what happens to the 11 existing parameter rows?

The Plan 252 draft said "no default; an undeclared parameter refuses". Combined with a `NOT NULL`
column that would be **an unsafe migration**: `docs/standards/cicd.md` requires additive changes only
within a release — new columns nullable, tightened in a **later** release, so the previous image can
run against the new schema during the migration window (the `station_weather_sources.role` two-release
pattern is the worked precedent).

**Recommendation:** follow that precedent exactly — add the column nullable, backfill deliberately
with a recorded per-series justification, tighten in a later release. **Fail-closed at the read
boundary, not in the schema**: a consumer that needs the support and finds NULL refuses, which gives
the same safety without an unsafe migration. This depends on D1, since what gets backfilled depends
on what the column hangs off.

### D3 — is "unknown" representable?

The draft said missing `cell_methods` would be recorded as "unknown", while the type had only `POINT`
and `INTERVAL`. Unknown must either be a third state or be NULL with a documented meaning — and
"declared by us but not yet verified against the source" is a **fourth** state, distinct from both.

**Recommendation:** NULL means *not recorded*; a separate boolean or timestamp records *verified
against source CF metadata*. Two independent facts, two fields — do not encode them in one enum, which
is how a "declared" value silently acquires the authority of a measured one.

### D5 — does a model OUTPUT declare its temporal support? (blocking Plan 254 T8)

This plan covers **input** sources and adapters throughout. Nothing here declares the support of a
value the system *produces*. But the period-ending rule above binds forecast `valid_time` "by support
and not blanket" — so Plan 254 T8, which anchors a daily forecast's label, cannot tell whether that
bucket is stamped on its closing boundary without knowing whether the model emits a point or an
interval. **No plan owns this.** Added 2026-09-09.

### D6 — who asks DHM about period convention?

Plan 252 T7 explicitly REMOVED the period-convention question from the DHM questionnaire and assigned
it here (`252` T7). This plan has an audit task (T4) but **no task that asks or amends a provider
question**, so the evidence T4 would audit against has no route to being obtained. Either this plan
gains a questionnaire task or 252 T7 takes it back. Added 2026-09-09.

### D4 — who compares, and where does the comparison live?

Even once the Gateway passes `cell_methods` through, nothing is specified about where it travels or
who checks it. The Gateway result construction retains values, not source metadata
(`adapters/recap_gateway.py:806`), and the parameter store contract is **read-only**
(`protocols/stores.py:914`). A verification step needs a write path that does not exist.

## ✅ Interval bounds — assigned here, and now CARRIED here (T5)

Plan 252 assigns `period_start` / `period_end` to this plan (its OD-5). Plan 254 assigned them to
Plan 251. **Verified 2026-09-09: this plan's T0–T4 contain no bounds task, and Plan 251 contains no
`period_start`, `period_end` or interval-bound work at all.**

⚖️ **CLOSED 2026-09-09 by owner decision: this plan owns them, as T5.** They are this plan's shape of
problem — bounds only exist for values that ARE intervals. ⛔ **They are no longer an orphan; do not
describe them as one, here or in Plans 252 and 254.** The fix was to create an owner, not to re-point
a reference at a third plan.

## ⛔ Upstream dependency — real, and only half-asked

Plan 243 asked the Gateway to preserve the source `units` attribute; that work is in progress on their
side. **It asked for `units` only, not `cell_methods`.** Extending the same request is cheap — same
channel, same people, same pass-through work — and it should ride on 243's request rather than open a
second one.

⛔ **Until it lands, T3 below is unbuildable.** That is why it is a task in a DRAFT plan and not in a
READY one: a READY plan's task list is its completion ledger, and a ledger cannot contain an item
gated on someone else's release.

## Tasks

⚠️ **All six decisions above must be settled before T1 is written as a contract.** The Plan 252
review showed exactly what happens otherwise: tasks that cannot be implemented as specified.

### T0 — settle D1–D6
**Outcome:** six recorded owner decisions. **In:** this document. **Out:** any code.
**Verification:** each decision is recorded with its rationale and the option it rejected.

### T1 — declare temporal support at the settled cardinality
**Outcome:** `TemporalSupport` exists as a type and is carried at `(source, parameter)` per D1, added
**nullable** per D2, with a deliberate backfill and a recorded justification per series.

⚙️ **The concrete home, named 2026-09-09** — "wherever D1 decided" was not a contract an implementer
could build: a **`parameter_support` registry keyed `(source, parameter)`**, additive and nullable,
covering every source kind that carries timestamped values — observation feeds, forcing sources
(`historical_forcing` already keys `(source, parameter)` in its natural key, `db/metadata.py:824-877`)
and weather-forecast sources. ⛔ It is NOT a column on `parameters`; that is the per-parameter shape
D1 refuted.
**In:** `types/enums.py`, the type D1 selects, `db/metadata.py` plus an additive migration.
**Out:** reading anything from the source (T3); the aggregation METHOD (Plan 234). Depends on T0.
**Pre-change:** no temporal-support field exists anywhere; a consumer must infer support from
`aggregation_method`, which says how to COMBINE values, not what a value already IS.
**Verification:** a declared series round-trips by value; a NULL reads back as NULL and is never
defaulted; the migration is additive and the previous image runs against the new schema.

### T2 — fail closed at the read boundary
**Outcome:** a consumer that needs temporal support and finds none refuses, rather than assuming.
**In:** the read paths D1 implies. **Out:** the schema constraint (D2 keeps the column nullable).
Depends on T1. **Verification:** the refusal is locked by a test, not only the success path.

### T6 — ask the Gateway to pass `cell_methods` through

**Outcome:** the request is actually MADE and tracked. ⛔ **T3 declares itself blocked on this and no
task owned making the ask** — a dependency on someone else's work that nobody was assigned to
request. Plan 243 asked for `units` only.

**In:** the same channel and people as Plan 243's units request; extend it rather than opening a
second one. Record where the request lives and how we will know it landed.
**Out:** implementing the verification (T3). **Depends on T0.**
**Verification:** N/A — external-request task. The request exists, is linked from this plan, and T3
names it as its unblocking condition.

### T3 — verify the declaration against the source (⛔ BLOCKED — do not start)
**Outcome:** the source's CF `cell_methods` is read at ingest and **checked against** T1's
declaration, so a mismatch is caught rather than assumed away.
⛔ **Blocked on the Gateway CF pass-through above.** Depends on T1, D3, D4.
**Verification:** a fixture whose `cell_methods` contradicts the declaration fails; agreement records
the verification, and the recorded state distinguishes *declared* from *verified*.

### T5 — publish the window an interval value covers

⚖️ **Owner decision 2026-09-09: this plan owns it**, as a real task rather than a cross-reference.
Previously it was assigned here by Plan 252, to Plan 251 by Plan 254, and carried by neither.

**Outcome:** an interval-valued value is published with the start and end of the window it covers, so
a consumer never has to infer our convention from a single stamp. **⛔ Only for values that ARE
intervals** — giving a point reading a start and end would invent a span it does not have, which is
why this task sits behind T1.

**In:** the published API and export shapes. **Out:** internal storage (the stamp plus the declared
support is sufficient internally); points. Depends on T1.

📌 **Ride the next format version, do not open a second one.** Plan 251 already carries a v2→v3
transition of the Forecast Lab snapshot; if that lands first, these fields go with it.

**Verification:** an interval value carries bounds matching its declared support and stamp; a point
value carries none; and a consumer reading only the bounds gets the same window as one applying the
convention to the stamp.

### T4 — audit every input adapter's temporal support
**Outcome:** each adapter's series is declared, with the source document that settles it — the same
shape as Plan 243's unit register, which is the worked precedent for this exact problem.
Depends on T1. **Verification:** every ingesting adapter appears; none is left implicit.

## Exit gates

```bash
uv run pytest tests/unit && uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
```

1. **All decisions D1–D6 are recorded** before any task ships (D5 and D6 added 2026-09-09), and the
   interval-bounds task (T5) exists and is not described anywhere as an orphan.
2. **The migration is additive and reversible-by-redeploy** — nullable, no tightening in this release.
3. **A declared-but-unverified value is distinguishable from a verified one** — never conflated.
4. ⛔ **Not a gate: "temporal support comes from CF metadata, never inference".** It cannot be, while
   T3 is blocked. The honest interim gate is that support is *declared by us, recorded as declared,
   and refused when absent*.

## Dependency graph

```json
{
  "plan": 258,
  "nodes": [
    {"id": "T0", "phase": 1, "depends_on": []},
    {"id": "T1", "phase": 2, "depends_on": ["T0"]},
    {"id": "T2", "phase": 3, "depends_on": ["T1"]},
    {"id": "T4", "phase": 3, "depends_on": ["T1"]},
    {"id": "T5", "phase": 3, "depends_on": ["T1"]},
    {"id": "T6", "phase": 1, "depends_on": ["T0"], "note": "the external ask T3 is blocked on; nobody owned it before 2026-09-09"},
    {"id": "T3", "phase": 4, "depends_on": ["T1"], "blocked_on": "Gateway CF attribute pass-through — extend the Plan 243 units-only request to cell_methods"}
  ]
}
```
