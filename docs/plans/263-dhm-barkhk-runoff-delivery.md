---
status: DRAFT
created: 2026-09-10
revised: 2026-09-10
plan: 263
reviews: ["codex 2026-09-10 — NOT READY, 5 blockers, all verified and folded except D7 which returns to the owner"]
title: DHM Barkhk delivery — parse, verify and import six Koshi/Narayani gauges
scope: Parse the September 2026 DHM runoff delivery (6 daily-discharge series, 112 rating tables, 1 scanned station list) into SAP3 domain types, and land it as onboarded stations + rating curves + observations. NOT a live DHM API adapter, NOT a level→discharge operational path (no level data exists in this delivery), NOT a change to the halted time-grid/phase work, NOT a change to Plan 139's scope.
depends_on: [035]
source: 2026-09-10 — measured directly against the delivered files (README.md: "Received without further comments from Subash via Vishnu on September 8, 2026 via email to Beatrice"). Every number in this plan was measured, not inferred.
---

# Plan 263 — DHM Barkhk delivery: parse, verify and import

## Status

**DRAFT — NOT READY.** One independent Codex review has run (2026-09-10) and its findings
are folded below; every one was verified against the cited source before folding. An
independent **Claude** pass is still owed before READY.

The review changed the plan materially — it was right about five things the author was
wrong about:

1. **T3 is not unblocked after all.** Curve validity is stored as *instants*, so turning
   DHM's inclusive end dates into stored intervals requires the same Nepali-day boundary
   T4 is waiting on. The author declared T3 unblocked; it is not.
2. **No task created the station rows** every other task's foreign keys require. Now T2b.
3. **T4's acceptance criterion contradicted the plan's own measurement** — it demanded
   every value be in range while the plan reports one that is not.
4. **The draft itself leaked three individual rating-table values** past the constraint
   the author had just written. Generalised; see § Data handling.
5. **The in-memory test double diverges** from the store the plan proposes to fix, so
   fixing only the database reader would split unit and integration behaviour.

**One finding is not folded and is back with the owner: D7.** See § Decisions.

**All nine owner decisions were closed on 2026-09-10** (§ Decisions). Two of the answers
changed the shape of the work rather than merely selecting an option:

- **The measurements may not be published** (D5). This is a handling constraint on every
  task, not a footnote — it rules out the checked-in data excerpts the first revision's
  T1 and T5 assumed. See § Data handling.
- **DHM has no current rating tables to give us** (D8). The expired-curve finding is
  therefore not a gap in the delivery that a follow-up email closes; there is nothing to
  send. A live level→discharge path for these six stations is blocked at source.

## What arrived

`.../2025-01-BARHKH/data/runoff/BARKHK PROJECT DATA/` (Dropbox, outside the working
tree, git-ignored by virtue of living outside the repo):

| File(s) | Content |
|---|---|
| `DFL_{447,450,604.5,647,670,684}.txt` | Daily **discharge** in m³/s, 96,521 values total |
| `RT_{same six}.txt` | **112 rating tables**, stage→Q at 0.1 m steps, each with a validity window |
| `Updated_Published_Station_List_2023.pdf` | DHM's published station list — **a 3-page scan, no text layer** |
| `koshi basin.jpg`, `NARAYANI BASIN.jpg` | Basin overview maps |
| `README.md` | Provenance note (added locally, not by DHM) |

**There is no water-level data in this delivery.** See § The shape of the gap.

### The six stations

Identity resolved by reading the scanned station list (pages 2–3). Every station's
published record range in the PDF matches its `DFL_` file's first and last date
**exactly** — a six-for-six independent confirmation that the file/station binding
is correct.

| Code | River | Location | Lat (N) | Lon (E) | Elev m | Area km² | Record |
|---|---|---|---|---|---|---|---|
| 447 | Trisuli | Betrawati, Nuwakot | 27°58'12.31" | 85°10'58.60" | 567 | 4,619.85 | 1977–2019 |
| 450 | Narayani | Devghat, Chitawan | 27°43'31.74" | 84°25'39.43" | 180 | 31,649.96 | 1963–2023 |
| 604.5 | Arun | Turkeghat | 27°18'42.98" | 87°11'21.49" | 281 | 27,124.20 | 1975–2019 |
| 647 | Tamakoshi | Busti | 27°38'10.46" | 86°5'5.84" | 835 | 2,934.55 | 1971–2019 |
| 670 | Dudhkoshi | Rabuwa Bazar | 27°16'6.72" | 86°39'33.91" | 444 | 3,716.26 | 1964–2019 |
| 684 | Tamor | Majhitar | 27°9'33.39" | 87°42'42.79" | 418 | 4,017.61 | 1996–2019 |

These values are **transcribed by eye from a scan** and must be owner-confirmed
before they become station rows (D4).

## Data handling (owner constraint, D5)

**These measurements may not be published.** Station codes may be; the discharge values
and the rating tables may not, except in aggregated form from which individual values
cannot be recovered.

Consequences, binding on every task below:

- **No excerpt of the delivered files may be checked into this repository** — not as a
  test fixture, not as a doc example, not as an expected-output file. Test fixtures are
  **synthetic**, hand-written to exhibit each structural feature (multi-block tables,
  repeated type labels, a negative stage, a missing year, an overlapping day).
- The delivered files stay where they are, outside the working tree. Nothing copies them in.
- Aggregates are fine: counts, coverage percentages, date ranges, validity windows.
  What this plan records — record spans, counts, coverage percentages, curve validity
  windows, and the station metadata (which comes from DHM's own *published* station list)
  — is aggregate or public and stays. **Individual stage and discharge values do not**, and
  the independent review found three that had survived the first draft (a table's lowest
  stage, and two tables' point counts and reach). They have been generalised. The test to
  apply is whether a reader could recover a value from the table, not whether the value
  feels structural — that reasoning is what let the three through.
- **Open question for the pilot (D9):** if a model is trained on these measurements, it is
  unresolved whether its forecasts inherit the restriction. Worth settling before the pilot
  is placed, not after.

## The shape of the gap

The briefing's hypothesis was that the gap is parsing and station identity, not the
curve model. **That is two-thirds right, and the remaining third is the load-bearing part.**

- **Parsing is genuinely easy.** Both formats are rigidly regular. Across 96,521 data
  lines and 112 rating tables there are **zero** unparsable lines, zero duplicate dates,
  zero non-numeric values, zero stray lines inside a rating block. Every rating table is
  strictly increasing in both stage and discharge — which clears the two rules most likely
  to reject real-world tables, but is *not* the converter's whole contract (it also requires
  well-formed finite points, and strictly positive discharge under log-linear
  interpolation). T3 proves the real thing by running the converter, not by monotonicity.
- **Station identity is easy but manual.** `stations.code` is `Text`, so `"604.5"` needs
  no special handling. The metadata to onboard with is only available as a scan.
- **The curve *model* fits; the curve *identity and lifecycle* model does not.** Three
  concrete mismatches, all measured, all in § Where DHM does not fit the existing model.

## Divergence from the July 2026 dummy samples

Every point on which the real files differ from `tmp_dhm_formats/`:

| Dummy said | Real files |
|---|---|
| `DFL_*`: 3 header lines | **1 title line + one line per year present**, e.g. 44 header lines for station 447. The year list is a real index: station 647 declares 48 years for a 49-year span, and 2010 is genuinely absent from the data |
| `DFL_*`: INTEGER m³/s | **Mostly decimal.** Station 450 is 100% integer; 447 is 46% integer / 54% one-decimal; 647 is 37% integer. Precision is a per-station property, not a format rule |
| `DFL_*`: one year per file | **Whole record per file**, 24–61 years |
| `GHT_*` gauge height, 3×/day | **Not delivered at all** |
| `RT_*`: `From Date`/`To Date` in `DD-Mon-YYYY` (dash) | Confirmed — dash in `RT_`, slash in `DFL_`. The dummy was right |
| `RT_*`: 0.1 m steps | Confirmed for all 112 tables |
| CRLF | Confirmed, all files |
| Silent gaps, no flag marker | **Confirmed and material** — see below |

Two structural facts the dummies did not show at all:

- `RT_` files are **multi-block**: a 5-line file header, then N blocks of
  `Rating Type No` / `From Date` / `To Date` / `From Stage` / points / a dashed separator.
  17–29 blocks per station (6 for station 684).
- **Stage can be negative.** At least one table's lowest tabulated stage is below zero,
  so any importer or validator that assumes a non-negative stage will reject real data.

### Silent gaps, quantified

DHM does not transmit flagged data, so absence is unmarked. Measured:

| Station | Missing days | Longest gap |
|---|---|---|
| 447 | 0 (0.0%) | — |
| 450 | 155 (0.7%) | 122 d from 1976-09-01 |
| 604.5 | 288 (1.8%) | 106 d from 2019-02-15 |
| 647 | 1,173 (6.6%) | **471 d from 2009-09-17** (all of 2010) |
| 670 | 433 (2.1%) | 245 d from 2009-05-01 |
| 684 | 32 (0.4%) | 32 d from 2008-03-12 |

The importer must write no row for a missing day. It must **not** write
`qc_status=MISSING` rows either, which would assert we know the day was observed and
rejected — we do not.

## Where DHM does not fit the existing model

All three are measured, and all three are decided by the owner below.

**1. "Rating Type No" is not a version, and is not even a stable curve identity.**
`rating_curves` has `UNIQUE (station_id, version)` and migration 0034 calls it
"monotonic versioning". DHM's type numbers **repeat within a station** — station 447
uses type 3 in four separate periods, type 4/5/7/9 twice each; station 670 reuses eight
different type numbers. Worse, station 604.5's **type 4 carries two genuinely different
tables**: the one covering `[1983-07-16..1986-07-31]` is materially shorter and reaches a
lower stage than the one shared by `[1995-08-31..1998-08-17]` and `[2001-08-20..2003-08-19]`. So
the type number cannot be the `version`, cannot be a natural key, and there is nowhere
in the current schema to record it. That is squarely Plan 035's provenance scope.

**2. Three one-day overlaps will make `fetch_curve_at` raise.**
DHM's `To Date` is inclusive. Mapping it to the repo's half-open `[valid_from, valid_to)`
convention gives `valid_to = To Date + 1 day`, and three consecutive pairs then overlap
by exactly one day:

| Station | Overlapping day | Curves |
|---|---|---|
| 447 | 2011-08-06 | type 9 → type 10 |
| 450 | 2001-08-23 | type 2 → type 4 |
| 604.5 | 1995-08-31 | type 5 → type 4 |

`PgRatingCurveStore.fetch_curve_at` uses `.one_or_none()` and will raise
`MultipleResultsFound` on those three days. `fetch_active_curves_batch_at` uses `.all()`
with a documented last-wins ordering and will not. That inconsistency is pre-existing;
this delivery is the first thing that would actually trigger it.

**3. Every DHM curve is closed, and every one has already expired.**
DHM always supplies a `To Date`, so no DHM curve is ever `valid_to IS NULL` — meaning
`fetch_active_curve()` and `fetch_active_curves_batch()` return nothing for all six
stations. And the newest curve per station is already in the past as of 2026-09-10:

| Station | Newest curve ends | Expired |
|---|---|---|
| 447 | 2025-12-31 | 253 d ago |
| 450 | 2024-09-28 | 712 d ago |
| 604.5, 647 | 2020-07-10 | 2,253 d ago |
| 670, 684 | 2020-07-20 | 2,243 d ago |

**No station in this delivery has a rating curve valid today** — and per D8, DHM has no
current table to supply. This is therefore a standing operational limitation, not an
outstanding request: a live level→discharge path for these six stations is blocked at
source until DHM produces new tables.

## What the data says about itself

Internal consistency, measured by inverting each daily discharge back through whichever
curve was valid that day:

- **96,520 of 96,521 values fall inside the tabulated Q range** of their valid curve. The
  daily series is *consistent with* having been produced from these exact tables — which is
  weaker than saying it was; see § What is not established.
- **One value at station 670 falls outside** its valid curve's range. One exception in
  96,521 is not noise to round away: it is either a curve/date mismatch or a value DHM
  produced by some other route, and the import needs a stated policy for it (D10).
- **4,629 values (4.8%) have no curve at all**: station 604.5's record starts 1975-05-23
  but its first curve starts 1983-07-16 (2,976 values), and station 670's record starts
  1964-03-10 against a first curve from 1968-10-06 (1,653 values). Their provenance
  cannot be reconstructed from this delivery.
- **The daily aggregation rule is NOT determinable from these files.** Recovered stages
  cluster well above chance on a 0.01 m grid (17–43% vs a 4% chance rate), which is
  consistent with a quantised stage series, but the fit improves monotonically on finer
  grids — which any finer grid does — so this does not discriminate "mean of stage,
  then convert" from "convert each reading, then mean". Do not let a plausible story
  here harden into a claim. This is a question for DHM (D8).

## What is not established

Stated plainly, because two of the findings above came from the author treating a
consistent measurement as a confirmed fact:

- **We do not know that DHM produced these discharge values from these tables.** Every
  value but one lands inside its contemporaneous table's range, which is *consistent with*
  that and would be odd if it were false — but it is not proof, and one value contradicts it.
- **We do not know how a daily value is aggregated** from sub-daily readings. Measured and
  explicitly inconclusive: recovered stages cluster above chance on a 0.01 m grid, but the
  fit improves on any finer grid, so the test cannot separate "average the stage, then
  convert" from "convert each reading, then average".
- **We do not know when a Nepali day starts or ends** (D6, with DHM, unanswered).
- **Nothing in this plan's measurements is independently verifiable** by a reviewer, since
  the files cannot be copied into the repository. Treat them as the author's claims.

Design consequence: **no task may assert provenance that rests on any of the above.**

## Two traps, recorded not solved

**Trap 1 — timezone, and what a daily value means.** Partly resolved, still blocking.

The owner has settled the *interpretation*: **each value is a Nepali day**, not a UTC day
and not an arbitrary 24 hours. That is a real constraint and it rules out the tempting
shortcut of treating the date as a UTC calendar date.

What remains open is the **boundary**: from when to when DHM considers a Nepali day to run.
The owner has put that question to DHM and **has not yet had an answer**. Until it comes
back, the exact instant a value should carry is unknown — and choosing one anyway would be
inventing a fact about someone else's data.

The downstream consequence is unchanged and is why this blocks rather than merely waits:
Nepal is UTC+05:45, so a Nepali day cannot coincide with a UTC calendar day, and the skill
machinery buckets observations on UTC calendar days. Reconciling those two is the
time-grid/phase work the owner has **halted**. This plan does not design a grid or phase
answer, and does not choose a timestamp convention.

**Trap 2 — Plan 139's `rof` proxy target.** Asked plainly: **this delivery does not
supersede W1a.** Plan 139 is scoped to gateway HRU `12300`, which resolves to
`station_code 123` / `g_123` / `testin_123` in `tests/fixtures/basin_static/nepal-dhm-basins/`
— a 99.7 km² test basin at 28.244 N, 82.923 E in western Nepal. None of the six delivered
stations is 12300 or near it; they are 2,935–31,650 km² Koshi and Narayani basins 1.5–5°
of longitude to the east. Plan 139's W1b is **not** partly satisfied by this: it asks
for a target for 12300 specifically, and unrelated gauges elsewhere in Nepal do not
partially supply that. W1b remains wholly unresolved for 12300. What has changed is only
that real DHM discharge now exists in our hands at all — for other stations, ending in 2019
for four of the six. What this delivery makes possible is a
**different** pilot: a real gauge with a real target, trained against a reanalysis forcing
that spans the same decades (ERA5-Land / Caravan), not against the operational gateway
feed. Whether to open that is an owner scope call (D9), not a change this plan makes to 139.

## Decisions

All closed by the owner, 2026-09-10.

**D1 — Where DHM's rating-table label goes. CLOSED: we number the curves ourselves.**
`version` is the 1-based chronological ordinal of the curve within its station; DHM's type
label is recorded alongside as provenance. Needs a nullable provenance column and a
migration. Carried here rather than deferred, since nothing else is waiting on Plan 035.

**D2 — The three one-day overlaps. CLOSED: the newer table wins.**
Import verbatim — no edit to DHM's dates — and fix `fetch_curve_at` to resolve an overlap
to the curve with the later `valid_from`, matching the last-wins rule
`fetch_active_curves_batch_at` already documents. This fixes a real pre-existing reader
defect, not just this import.

**D3 — Expired curves. CLOSED: store as delivered.**
Every curve keeps the `valid_to` DHM gave it. `fetch_active_curve()` consequently returns
`None` for all six stations, and that is the correct answer: no station has a currently
valid table.

**D4 — Transcribed station metadata. CLOSED: owner will confirm, and transcription is
permitted.** DHM has confirmed we may transcribe the scanned station list (D8). The
coordinates, elevations and drainage areas in § The six stations still need the owner's
spot-check before they become station rows.

**D5 — Import target. CLOSED: side dataset first, and the data may not be published.**
See § Data handling — the publication constraint is the load-bearing half of this answer
and binds every task.

**D6 — What a daily value means. CLOSED as far as it can be: it is a Nepali day.**
The exact day boundary is with DHM, unanswered. See Trap 1. T4 stays blocked.

**D7 — Source label for the discharge. REOPENED by the independent review; owner to
re-decide.** The owner closed this on the author's recommendation — bind each value to the
curve in force that day — but that recommendation rested on a claim the author had itself
measured as only *consistent with* the data, not confirmed (§ What is not established).

The review found the label is not merely descriptive. `RATING_CURVE_DERIVED` plus a curve
id is the precondition for the rating-curve **reprocessing** path: `archive_observation_values`
(`store/observation_version_store.py:46`) *rejects* anything else, and that path exists to
recompute discharge from **water level** when a corrected curve arrives. We have no water
level for these stations, and never will for this historical record. So the binding would
not just overstate provenance — it would enrol 92,000 values in a machine that cannot
process them.

Recommendation, reversing the author's earlier one: **`MANUAL_IMPORT` throughout, with no
curve binding**, until DHM confirms how the values were produced. It is the one label that
claims only what we know — these numbers came from DHM by hand. The curves are still
imported and still queryable by date; nothing is lost but an assertion we cannot support.

**D10 — the one out-of-range value at station 670. NEW, open.** One value in 96,521 falls
outside its contemporaneous curve's range. Options: import it unflagged, import it with a
QC flag, quarantine it, or ask DHM. Recommendation: import it and flag it — it is real
delivered data, and silently dropping a value because it contradicts our model of how the
data was made is exactly the inversion of trust to avoid.

**D8 — What to ask DHM. CLOSED, and the answer reshapes the finding.**
**There are no current rating tables** — not withheld, not in this delivery by oversight;
they do not exist. So the expired-curve finding is not a follow-up email away from being
closed, and an operational level→discharge path for these six stations is blocked at
source. Transcription of the scanned station list is permitted. Still worth asking, but
not gating this plan: the water-level readings, and tables covering the two stations whose
records begin years before their earliest table.

**D9 — A real-gauge pilot. CLOSED: yes.**
Where it lives — alongside the Swiss study, or as a separate deployment — is still being
weighed and is **not** this plan's to settle. Note the unresolved publication question in
§ Data handling: whether forecasts trained on restricted measurements inherit the
restriction should be settled before the pilot is placed.

## Tasks

Every task inherits § Data handling: no excerpt of the delivered files is checked in, and
fixtures are synthetic. Verification names the command or test node, per `docs/workflow.md`
§ Plan Structure.

### T1 — DHM file parsers

**Outcome**: pure functions parsing a daily-flow file and a rating-table file into frozen
dataclasses, with no I/O and no DB dependency. Malformed input raises a typed error; a line
that is neither header nor data is never silently skipped.
**In**: new module under `src/sapphire_flow/adapters/`; unit tests; synthetic fixtures.
**Out**: no store writes, no station resolution, no timestamp policy — T1 yields dates and
values as delivered; the UTC boundary belongs to T3/T4 and is gated on D6.
**Verification**: `uv run pytest tests/unit/adapters/test_dhm_files.py`, covering at least:
a truncated block, a non-numeric value, an unknown line inside a rating block, and a
declared year absent from the data — each asserting the typed error and its message
fragment, not just that something raised. Plus the aggregate re-measure below (T5), which
is the only check that touches the real files.
**Pre-change**: N/A — new module.

### T2a — Station metadata artifact

**Outcome**: the six stations' transcribed metadata as a reviewable checked-in artifact
(`timezone: Asia/Kathmandu`, `network: dhm`, `station_kind: river`). Station codes and the
station-list metadata are publishable (D5); the measurements are not.
**In**: fixture/config only.
**Out**: no DB writes.
**Verification**: `uv run pytest tests/unit/config/test_dhm_station_metadata.py` — asserts
every required field is present and well-formed for all six (coordinates in range, timezone
resolvable, code non-empty), which is what this task actually produces. The record-span
cross-check against the delivered files belongs to T5, which has the parser and the files.
**Pre-change**: N/A — new data. **Gated on the owner's spot-check (D4).**

### T2b — Station onboarding into the side dataset

**Outcome**: six `stations` rows exist in the side dataset, so the foreign keys T3 and T4
depend on can resolve. Added after the independent review found nothing created them.
**In**: an onboarding entry point writing the T2a artifact to the side dataset.
**Out**: **no `station_status` promotion** — these stay `onboarding`. Promotion switches on
ingest and forecasting for a station and is not this plan's to trigger.
**Verification**: after running it, six rows exist with the expected codes and `network=dhm`,
and each `station_status` is `onboarding`.
**Pre-change**: N/A — new path. **Depends on T2a.**

### T3 — Rating curve import

**Outcome**: 112 curves stored with a chronological `version`, DHM's type label kept as
provenance, half-open validity derived from DHM's inclusive end date, and no station left
with an open-ended curve.
**In**: migration adding a nullable provenance column; `RatingCurve`; the store; the
importer; the `fetch_curve_at` fix **and the matching fix to the in-memory double**
(`tests/fakes/fake_stores.py:1587`, which returns the first match in insertion order while
its batch sibling at :1625 already resolves last-wins — fixing only the database reader
would split unit from integration behaviour).
**Out**: no conversion of any observation.
**Verification**: `uv run pytest tests/unit/store/test_rating_curve_store.py
tests/unit/adapters/test_dhm_rating_import.py`, asserting: all 112 pass
`RatingConverter.from_curve` with the interpolation actually selected; versions are
1..N in date order per station with no gaps; DHM's type label round-trips through store and
read-back; every imported curve has a non-null `valid_to`; and `fetch_curve_at` returns the
later-starting curve on an overlap — **proven identically against the real store and the
fake**, so the two cannot drift.
**Pre-change**: a RED test proving `fetch_curve_at` raises `MultipleResultsFound` on two
simultaneously-valid curves, built from a synthetic two-curve fixture — the actual defect
and its actual cause, not a signature error.
**BLOCKED on D6.** Curve validity is stored as `UtcDatetime` instants, so converting DHM's
inclusive end dates into half-open intervals requires the Nepali-day boundary DHM has not
yet given us. The independent review caught this; the previous revision wrongly called T3
unblocked. Everything else in T3 — the migration, the version scheme, the provenance
column, both reader fixes — can be built and tested against synthetic fixtures first; only
the import of the real dates waits.

### T4 — Daily discharge import

**Outcome**: 96,521 observations, no row for any missing day, no `MISSING`-status rows,
each row labelled per D7 (**reopened — see § Decisions**).
**In**: importer; observation store.
**Out**: no QC policy change, no skill or training wiring.
**Verification**: stored row count per station equals the T5-measured count; no row exists
on any measured gap day; **96,520 of 96,521 values lie inside their curve's tabulated range
and exactly one — the known station-670 exception — does not**, handled per D10. The
earlier "every value in range" criterion contradicted the plan's own measurement and could
not have passed.
**Pre-change**: N/A — new import path.
**BLOCKED on D6 and D7.** Do not start by picking a day boundary.

### T5 — Delivery re-measure

**Outcome**: a re-runnable script reproducing the aggregates in this plan, so a later
delivery can be diffed against this one, and so the plan's unverifiable claims become
reproducible on the owner's machine.
**In**: a script under `scripts/`, reading the delivered files in place.
**Out**: not a test gate; not a flow.
**Permitted output — the complete allowed set**: row and curve counts; counts and
percentages of missing days, gap runs, curve-less days and out-of-range values; first and
last dates; gap start dates and lengths; curve validity windows and point counts; per-file
value-precision percentages. **No stage value, no discharge value, in output or in failure
diagnostics.**
**Verification**: the script's output matches the aggregate tables in this plan exactly.
**Pre-change**: N/A. **Depends on T1** (it needs the parser and nothing else).

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1", "T2a"], "parallel": true },
    { "id": "phase-2", "tasks": ["T2b", "T5"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T4"], "depends_on": ["phase-3"] }
  ]
}
```

T1 and T2a are genuinely independent. T5 needs only the parser, so it no longer waits on the
curve import; T2b needs only the metadata artifact. T3 and T4 both remain blocked on D6.

## Explicitly out of scope

- A live DHM API adapter (the questionnaire track, `project_dhm_data_interface`).
- Any operational level→discharge path — **there is no level data in this delivery**, no
  station has a currently-valid curve, and DHM has none to give (D8). Blocked at source.
- The halted time-grid / phase work (Plans 252/254/258, 239).
- Plans 261/262 — another session owns those.
- Any change to Plan 139's scope. The owner has approved a real-gauge pilot (D9), but
  where it lives — alongside the Swiss study or as a separate deployment — is undecided and
  is not settled here.
- Publishing these measurements in any form (§ Data handling).
