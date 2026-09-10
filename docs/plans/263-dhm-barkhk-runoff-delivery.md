---
status: DRAFT
created: 2026-09-10
revised: 2026-09-10
plan: 263
title: DHM Barkhk delivery — parse, verify and import six Koshi/Narayani gauges
scope: Parse the September 2026 DHM runoff delivery (6 daily-discharge series, 112 rating tables, 1 scanned station list) into SAP3 domain types, and land it as onboarded stations + rating curves + observations. NOT a live DHM API adapter, NOT a level→discharge operational path (no level data exists in this delivery), NOT a change to the halted time-grid/phase work, NOT a change to Plan 139's scope.
depends_on: [035]
source: 2026-09-10 — measured directly against the delivered files (README.md: "Received without further comments from Subash via Vishnu on September 8, 2026 via email to Beatrice"). Every number in this plan was measured, not inferred.
---

# Plan 263 — DHM Barkhk delivery: parse, verify and import

## Status

**DRAFT.** Owner sets READY. Non-trivial: one independent Claude and one independent
Codex review before READY. The task breakdown has not been reviewed since the decisions
below were folded in.

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
  What this plan already records — record spans, missing-day counts, curve validity
  windows, and the station metadata (which comes from DHM's own *published* station list)
  — is aggregate or public and stays. One borderline line is retained deliberately: a
  single table's lowest stage, quoted as evidence that stage can be negative. It is a
  structural fact about table shape, not a measurement. Flag if you read it otherwise.
- **Open question for the pilot (D9):** if a model is trained on these measurements, it is
  unresolved whether its forecasts inherit the restriction. Worth settling before the pilot
  is placed, not after.

## The shape of the gap

The briefing's hypothesis was that the gap is parsing and station identity, not the
curve model. **That is two-thirds right, and the remaining third is the load-bearing part.**

- **Parsing is genuinely easy.** Both formats are rigidly regular. Across 96,521 data
  lines and 112 rating tables there are **zero** unparsable lines, zero duplicate dates,
  zero non-numeric values, zero stray lines inside a rating block. Every rating table is
  strictly increasing in both stage and discharge, so all 112 satisfy
  `RatingConverter.from_curve`'s validation as written.
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
- Stage can be **negative**: station 670's type-5 table starts at −0.1 m.

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
tables**: `[1983-07-16..1986-07-31]` has 52 points to 6.0 m, while
`[1995-08-31..1998-08-17]` and `[2001-08-20..2003-08-19]` have 66 points to 7.4 m. So
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

**No station in this delivery has a rating curve valid today.** A live level→discharge
path needs a current curve from DHM that is not in this delivery. This is the single
most actionable thing to put back to Subash.

## What the data says about itself

Internal consistency, measured by inverting each daily discharge back through whichever
curve was valid that day:

- **96,520 of 96,521 values fall inside the tabulated Q range** of their valid curve
  (one exception at station 670). The daily series is consistent with having been produced
  from these exact tables.
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
of longitude to the east. Plan 139's W1b ("real DHM discharge … not available on this
timeline") is now *partly* false — real DHM discharge has arrived — but not for 12300,
and it ends in 2019 for four of the six. What this delivery makes possible is a
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

**D7 — Source label for the discharge. CLOSED: reflect how each value was produced.**
`RATING_CURVE_DERIVED`, bound to the curve, on the ~95% of days a curve covers;
`MANUAL_IMPORT` on the 4,629 days with no curve. The source column is therefore
two-valued across one series by design — that is the honest shape, and the curve binding
stays queryable.

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
fixtures are synthetic.

### T1 — DHM file parsers

**Outcome**: pure functions parsing a daily-flow file and a rating-table file into frozen
dataclasses, with no I/O and no DB dependency. Rejects malformed input with a typed error;
never silently skips a line.
**In**: new module under `src/sapphire_flow/adapters/`; unit tests; synthetic fixtures.
**Out**: no store writes, no station resolution, no timestamp policy — T1 yields dates and
values as delivered; the UTC boundary belongs to T4 and is gated on D6.
**Verification**: unit tests over **hand-written synthetic fixtures** exhibiting each
structural feature (multi-block tables, a repeated type label, a negative stage, a declared
year absent from the data, an inclusive-end overlap). Plus one bounded inspection run
against the real delivery *in place*, asserting only the aggregate counts already recorded
in this plan — 96,521 daily values, 112 curves, 0 unparsable lines — and printing no values.
**Pre-change**: N/A — new module.

### T2 — Station identity and onboarding fixture

**Outcome**: the six stations' metadata as a reviewable, checked-in artifact with
`timezone: Asia/Kathmandu`, `network: dhm`, `station_kind: river`. Station codes and the
station-list metadata are publishable (D5); the measurements are not.
**In**: fixture/config only.
**Out**: no DB writes; no `station_status` promotion.
**Verification**: each station's fixture record range equals its file's measured first and
last date — the six-for-six check, re-run as a test over the aggregate dates only.
**Pre-change**: N/A — new data. **Gated on the owner's spot-check (D4).**

### T3 — Rating curve import

**Outcome**: 112 curves stored with a chronological `version`, DHM's type label kept as
provenance, half-open validity derived from DHM's inclusive end date, and no station left
with an open-ended curve.
**In**: migration adding a nullable provenance column; `RatingCurve`; the store; the
importer; the `fetch_curve_at` fix.
**Out**: no conversion of any observation.
**Verification**: all 112 round-trip through `RatingConverter.from_curve` without raising;
`fetch_curve_at` returns exactly one curve for every day of each station's record where a
curve exists, **including the three overlap days**, and returns the later-starting curve on
each of those three.
**Pre-change**: a RED test proving `fetch_curve_at` raises `MultipleResultsFound` for
station 447 on 2011-08-06 — the actual defect and its actual cause, not a signature error.
Reproduce it with a synthetic two-curve fixture, not a delivered excerpt.
**Unblocked** — D1, D2 and D3 are closed.

### T4 — Daily discharge import

**Outcome**: 96,521 observations, no row for any missing day, no `MISSING`-status rows,
each row labelled per D7.
**In**: importer; observation store.
**Out**: no QC policy change, no skill or training wiring.
**Verification**: stored row count per station equals the measured count; no row exists on
any measured gap day; every stored value lies inside its curve's tabulated range where a
curve exists; the ~4,629 curve-less days carry the manual-import label and no curve binding.
**Pre-change**: N/A — new import path.
**BLOCKED on D6** — DHM has not yet said when a Nepali day begins and ends, and this task
cannot assign a timestamp without that. Do not start it by picking a boundary.

### T5 — Delivery reconciliation report

**Outcome**: a re-runnable check reproducing every **aggregate** in this plan, so a later
delivery can be diffed against this one.
**In**: a script under `scripts/`.
**Out**: not a test gate; not a flow. **Its output contains counts, coverage and date
ranges only — no discharge values, no rating-table rows.** If the committed artifact would
carry a value, it is not committed.
**Verification**: the script's output matches the aggregate tables in this plan exactly.
**Pre-change**: N/A.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1", "T2"], "parallel": true },
    { "id": "phase-2", "tasks": ["T3"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5"], "depends_on": ["phase-2"] }
  ]
}
```

T5 now depends on T3 rather than T4, so the reconciliation report is not held up by the
blocked import.

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
