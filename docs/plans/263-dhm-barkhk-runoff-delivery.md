---
status: DRAFT
created: 2026-09-10
revised: 2026-09-10
plan: 263
reviews:
  - "codex 2026-09-10 — NOT READY, 5 blockers; all verified, folded"
  - "claude 2026-09-10 — NOT READY, 6 blockers + 7 majors; found most of what codex missed; all verified, folded"
  - "codex 2026-09-10 r2 — NOT READY, 4 blockers; killed the re-import claim and the QC isolation"
  - "claude 2026-09-10 r2 — NOT READY, 3 blockers + 7 majors; same two core failures, independently"
  - "codex 2026-09-10 set — NOT READY; reviewed with 264; 3 seam blockers neither plan owned"
open_decisions: [D14]
depends_on: [264]
title: DHM Barkhk delivery — parse, verify and import six Koshi/Narayani gauges
scope: Parse the September 2026 DHM runoff delivery (6 daily-discharge series, 112 rating tables, 1 scanned station list) into SAP3 domain types, and land it as onboarded stations + rating curves + observations. NOT a live DHM API adapter, NOT a level→discharge operational path (no level data exists in this delivery), NOT a change to the halted time-grid/phase work, NOT a change to Plan 139's scope.
related: [035]
source: 2026-09-10 — measured directly against the delivered files (README.md: "Received without further comments from Subash via Vishnu on September 8, 2026 via email to Beatrice"). Every number in this plan was measured, not inferred.
---

# Plan 263 — DHM Barkhk delivery: parse, verify and import

## Status

**DRAFT — NOT READY.** Both mandated independent passes have now run and are folded
(Codex 2026-09-10; Claude 2026-09-10). Every finding was verified against the cited source
before folding. **Round 2 of review has been folded.** Both passes returned NOT READY and, working
independently, killed the same two designs the previous round introduced:

- **The re-import story was false on both legs** (D6). Observations orphan rather than
  update when the day boundary moves, because `timestamp` is part of the natural key; and
  the curve import is a plain insert that cannot be re-run even once. D6 now carries an
  explicit replacement procedure instead of a claim about upserts.
- **The QC task could certify data without checking it** (T7). An empty flag list
  aggregates to *passed*, so a rule set that fails to match leaves every row marked
  quality-passed with zero rules run — and the acceptance test as written would have passed
  in that state.

Both were the author's own additions in the previous fold. The pattern from round 1 repeated
in a sharper form: **the parts of this plan that assert something about the repository's
behaviour have been wrong at a much higher rate than the parts that describe the data.**
Round 2 also found that a calibration the author recommended would have committed restricted
rating-table values — the same leak class already stripped once, reintroduced through a
different door.

**Fifteen of sixteen closed; D14 is reopened** — the owner found it aimed at a *plausible*
maximum where the project's own standard calls for a *physical impossibility* gate, which would
have reproduced the Swiss failure one level down. T1–T6 are buildable. T7 waits on D14 and on
**Plan 264**, which the owner's answer to D15 split out: teaching QC rules which network they
apply to changes shared code the live Swiss deployment depends on, and that risk gets its own
plan and its own review rather than a paragraph in this one.

**Round 3 reviewed this plan together with Plan 264, as a set** — and found three blockers
that neither plan owned alone, which is what reviewing them separately would have missed:

- **Plan 264 does not make six station-specific limits expressible**, because it adds a
  *network* dimension and D14 asks for *per-station* ceilings. The capability exists
  (`StationQcOverride`, merged per station by the checker) but neither plan claimed it, so an
  implementation could have satisfied both with one shared maximum. T7 now owns the six
  overrides and asserts each station's effective ceiling.
- **The general fail-open path was owned by nobody.** This plan had bolted a local assertion
  onto its own import while the live ingest and onboarding paths stayed fail-open. That policy
  moved to Plan 264, once, for every caller.
- **Fixing the flag-version defect changes Swiss output**, because the Swiss rules declare
  `"1.0.0"` while flags emit a hard-coded `"1.0"`. That compatibility decision is now Plan
  264's D5; this plan consumes it.

Also folded: the replacement procedure's delete predicates were dangerously broad (they would
have taken later, unrelated manual imports and any newer curve), and T4's rehearsal invoked a
procedure ending in T7 — a hidden dependency on Plan 264 through the back door.

The two passes between them found eleven blockers, and the pattern is worth stating because
it should shape how this plan is finished: **the author's measurements of the delivered data
held up; the author's claims about this repository did not.** Nothing in the file analysis
was overturned. What was wrong was a verification command naming a test tier that cannot
exercise the defect, a migration that breaks a pinned Alembic head, an onboarding task that
cannot satisfy a NOT NULL tenant, an import that never states the QC status of 99,246 rows,
and an acceptance criterion whose number depends on the very decision the task is blocked on.

Corrections to the author's own earlier reasoning, recorded so they are not re-derived:

1. **T3 is not unblocked** (Codex). Curve validity is stored as instants, so it needs the
   same Nepali-day boundary T4 waits on.
2. **The D7 argument was overstated** (Claude). The reprocessing path it invoked is
   **unbuilt**: `fetch_derived_observations_by_curve` raises `NotImplementedError`
   (`store/observation_store.py:233`) and `archive_observation_values` has no production
   caller. The conclusion survives, on two stronger legs; see D7.
3. **This Status section claimed all nine decisions were closed** while two sections below
   it reopened one and opened another. Fixed here.
4. **The delivery's headline count was wrong in every earlier revision.** It said 96,521
   daily values; the true figure is **99,246**, and the in-range result is 94,616 of the
   94,617 values that have a curve. The author found this while measuring for D12; neither
   review could have caught it, since the files are unverifiable from the repository. It is
   the exact failure the second review named — *flag anywhere the design would fail badly if
   one of those numbers were wrong* — and the fold that answered it did its job: T4's
   acceptance criterion had already been rewritten as a delta against T5's re-measure rather
   than a literal, so the wrong number would have surfaced at implementation instead of being
   ratified. Read every count here as the author's claim until T5 reproduces it.

An independent pass also confirmed by reading source that the three code mismatches this
plan is built on are accurate, that the data-handling constraint is not violated anywhere in
the current text, and that the plan's exclusions hold — no timezone/grid/phase design, no
change to Plan 139's scope, no contact with Plans 261/262.

## What arrived

`.../2025-01-BARHKH/data/runoff/BARKHK PROJECT DATA/` (Dropbox, outside the working tree —
*out of tree*, which is not the same as ignored; see § Data handling for the guard):

| File(s) | Content |
|---|---|
| `DFL_{447,450,604.5,647,670,684}.txt` | Daily **discharge** in m³/s, 99,246 values total |
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
- **The constraint currently rests on prose alone.** The second review flagged this: the
  files are out of tree, which is not the same as ignored, so a stray `git add` of a copied
  file would succeed. T6 adds a mechanical guard — an ignore pattern plus a pre-commit path
  check — so the constraint does not depend on anyone remembering it.
- **Open question for the pilot (D9):** if a model is trained on these measurements, it is
  unresolved whether its forecasts inherit the restriction. Worth settling before the pilot
  is placed, not after.

## The shape of the gap

The briefing's hypothesis was that the gap is parsing and station identity, not the
curve model. **That is two-thirds right, and the remaining third is the load-bearing part.**

- **Parsing is genuinely easy.** Both formats are rigidly regular. Across 99,246 data
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
  17–29 blocks per station for five of the six; 6 for station 684.
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

- **94,616 of the 94,617 values that have a curve fall inside its tabulated Q range.** The
  daily series is *consistent with* having been produced from these exact tables — which is
  weaker than saying it was; see § What is not established.
- **One value at station 670 falls outside** its valid curve's range. One exception in
  94,617 is not noise to round away: it is either a curve/date mismatch or a value DHM
  produced by some other route, and the import needs a stated policy for it (D10).
- **4,629 values (4.7%) have no curve at all**: station 604.5's record starts 1975-05-23
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

**Trap 1 — timezone, and what a daily value means.** Resolved by a working assumption; the
underlying uncertainty is unchanged.

The owner settled the *interpretation*: **each value is a Nepali day**, not a UTC day and not
an arbitrary 24 hours. That rules out the tempting shortcut of treating the date as a UTC
calendar date.

The **boundary** — from when to when DHM considers a Nepali day to run — is still unknown.
The owner has asked several counterparts and expects no rapid answer, and has decided not to
wait: D6 adopts 00:00–23:59 Asia/Kathmandu as a **stated working assumption**, with a
replacement procedure for correcting it later. That is a deliberate, revisable choice made in
the open, not a discovered fact — and the honesty of it depends entirely on the replacement
procedure actually working, which is why D6 spends more words on that than on the boundary.

The downstream consequence is unchanged: Nepal is UTC+05:45, so a Nepali day cannot coincide
with a UTC calendar day, and the skill machinery buckets observations on UTC calendar days.
Reconciling those two is the time-grid/phase work the owner has **halted**, and this plan
still does not design a grid or phase answer. Adopting a boundary for *importing* a date-only
value is not the same act as deciding how forecast and observation grids align — if a later
task starts reasoning about the second, it has left this plan's scope.

**Trap 2 — Plan 139's `rof` proxy target.** Asked plainly: **this delivery does not
supersede W1a.** Plan 139 is scoped to gateway HRU `12300`, which resolves to
`station_code "123"` / `g_123` in
`tests/fixtures/basin_static/nepal-dhm-basins/validation_report.json` — a 99.7 km² test
basin at 28.244 N, 82.923 E in western Nepal (the fixture family is described as
test-data placeholder in `tests/fixtures/basin_static/README.md:26`). None of the six delivered
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

All closed by the owner on 2026-09-10; D16 was raised by the cross-plan review and closed the same day.

**D1 — Where DHM's rating-table label goes. CLOSED: we number the curves ourselves.**
`version` is the 1-based chronological ordinal of the curve within its station; DHM's type
label is recorded alongside as provenance. Needs a nullable provenance column and a
migration. Carried here rather than deferred, since nothing else is waiting on Plan 035.

**D2 — The three one-day overlaps. CLOSED: the newer table wins.**
Import verbatim — no edit to DHM's dates — and fix `fetch_curve_at` to resolve an overlap
to the curve with the later `valid_from`, matching the last-wins rule
`fetch_active_curves_batch_at` already documents. This is a real pre-existing reader defect,
but do not over-weight it: `fetch_curve_at` has **no production caller** (only integration
tests), while the batch sibling is the one wired into the forecast cycle. The fix is cheap
and correct; it is not urgent on its own.

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

**D6 — What a daily value means. CLOSED by a stated working assumption.**
It is a Nepali day (owner). The owner has asked several counterparts for DHM's exact day
definition, expects no rapid answer, and has decided not to wait: **assume a Nepali day runs
00:00–23:59 Asia/Kathmandu, and re-import if we learn otherwise.**

That is a sound call, and it is not free — it makes **re-importability a design requirement**.

**The author's first attempt at that requirement was wrong, and both round-2 passes caught it
independently.** The plan claimed that because `source` is part of the observations natural
key, a re-import "upserts in place". The natural key is
`(station_id, timestamp, parameter, source)` (`db/metadata.py:570-576`) — **`timestamp` is in
it**. A boundary correction changes every timestamp, so a re-import inserts a second ~99,246
rows under new keys and leaves the originals as orphans. The claimed property holds only for
a re-run that changes nothing, which is precisely *not* the scenario it was written for.

The curve side is worse: `store_rating_curve` is a plain `sa.insert`
(`store/rating_curve_store.py:22-36`) against `UNIQUE (station_id, version)`, so T3 cannot be
re-run even once — the second attempt raises. And the curves cannot simply be deleted first,
because `observations` carries a composite FK to `rating_curves` with **no `ondelete`**
(`db/metadata.py:547-552`): once T4 has landed, curve deletion is blocked until the
observations go.

**The requirement is therefore an explicit replacement procedure, not an upsert.** In this
order, in one transaction:

1. delete the observations **this delivery imported** — not every `manual_import` row for
   these stations, which would also destroy any later manual import that has nothing to do
   with the September 2026 delivery;
2. delete the rating curves **this delivery imported** — likewise not "the tenant's curves",
   which would take a newer curve DHM sends later;
3. re-run T3, then T4, then T7, against the corrected boundary constant.

**The delivery needs an identity for steps 1 and 2 to be safe**, and round 4 found the
author's proposed identity does not exist. It said to use "the curve binding plus the import's
own recorded id". Neither half works: `observations` has **no column able to hold an import
id** (`db/metadata.py:497-555`), `store_raw_observations` mints `id` itself
(`observation_store.py:73`) so the CLI cannot pre-assign one, and the curve-binding half fails
on precisely the **4,629 rows D7 leaves with a NULL curve id** — which would be
indistinguishable from any other manual import, reinstating the over-broad predicate this was
written to remove.

**T3's migration must therefore add a delivery column to `observations` as well as the
provenance column on `rating_curves`**, and T4's In list can no longer call the observation
store "read-only". The rehearsal must assert that the curve-less rows are deleted too, and that
an unrelated manual observation and an unrelated curve, both created before the replacement,
**survive it**.

Two supporting requirements:

- **One shared boundary constant** across T3 and T4. They must not drift, or `fetch_curve_at`
  selects the wrong curve at a seam.
- **T7 always follows T4, never the reverse.** Both observation writers reset QC state on
  conflict — `store_raw_observations` forces `RAW` (`store/observation_store.py:105`, **conditionally** —
  the `where` at `:111-116` means an unchanged re-run does not reset it) and `store_observations`
  rewrites it from the object unconditionally (`:48`) —
  so re-running the import after a QC pass silently discards the QC result.

The rehearsal that proves this is a *boundary-change* rehearsal — import under boundary A,
re-import under boundary B, assert the A rows are **gone** — not a same-boundary re-run,
which proves nothing about the case D6 exists for.

**T3 and T4 are unblocked by this.** Trap 1 stands as the record of *why* the assumption is
an assumption; nothing here designs a grid or phase answer for the halted track.

**D7 — Source label for the discharge. CLOSED: record the link, claim nothing more.**
The owner closed this on the author's recommendation — bind each value to the curve in force
that day — but that rested on a claim the author had measured as only *consistent with* the
data, not confirmed (§ What is not established).

**The author's first argument for reversing was overstated, and the second review said so.**
It claimed the binding would "enrol 92,000 values in a machine that cannot process them"
(quoted as written; that count was also wrong — see the Status note on the corrected totals).
The guard is real — `archive_observation_values` rejects anything that is not
`RATING_CURVE_DERIVED` with a curve id (`store/observation_version_store.py:45-54`) — but it
is a filter that *rejects*, not a mechanism that *enrols*, and the path is **unbuilt**:
`fetch_derived_observations_by_curve` raises `NotImplementedError`
(`store/observation_store.py:233`), and nothing in `src/` calls the archive at all. The
hazard is prospective. Stated as it was, it invited rejection on the grounds that the harm
is hypothetical.

The conclusion nevertheless stands, on one verified leg:

- **The label is an identity, not an annotation.** `source` is part of
  `uq_observations_natural_key` (`db/metadata.py:570-577`), which is exactly what
  `store_observations` upserts on. Choosing wrong is therefore not a later `UPDATE` — it is
  a delete plus a re-insert of the full ~99,246-row import. The owner should price the
  decision at that, not at the cost of an edit.

**This is a three-way choice, not a binary** — the second review found the middle option,
which the author had missed:

| Option | Claims | Archive-eligible |
|---|---|---|
| `RATING_CURVE_DERIVED` + curve id | that this curve produced this value | yes |
| `MANUAL_IMPORT` + curve id | that this curve was in force that day | no |
| `MANUAL_IMPORT`, no curve id | only that DHM sent us the number | no |

The middle option is representable — the domain type allows it (`types/observation.py:39`)
and the composite FK is skipped only when the curve id is NULL, so a populated one is still
integrity-checked. **CLOSED on the middle option** (owner, 2026-09-10): `MANUAL_IMPORT`
throughout, with `rating_curve_id` populated as pure provenance wherever a curve covers the
day and NULL for the 4,629 days where none does. It records everything we know — including
the curve link — and claims nothing we do not.

**Documentation consequence, found in round 2 and missed by every pass before it:** three
places state that this column is set *only* when the source is rating-curve-derived
(`docs/spec/types-and-protocols.md:810` and `:821`, `docs/architecture-context.md:300`, which is weaker — it says derived observations *reference*
a curve, never that only they may). This import lands ~94,617 rows that contradict the spec
lines as written. The contract is worth widening
rather than the decision reversing — provenance and derivation are genuinely different
claims, and the schema already permits the distinction — but it must be **written down**,
not left as an undocumented exception. T6 carries it.

**D8 — What to ask DHM. CLOSED, and the answer reshapes the finding.**
**There are no current rating tables** — not withheld, not in this delivery by oversight;
they do not exist. So the expired-curve finding is not a follow-up email away from being
closed, and an operational level→discharge path for these six stations is blocked at
source. Transcription of the scanned station list is permitted. Still worth asking, but
not gating this plan: the water-level readings, how a daily value is aggregated from
sub-daily readings, whether the tables are read with linear interpolation (D13), and tables
covering the two stations whose records begin years before their earliest table.

**D9 — A real-gauge pilot. CLOSED: yes.**
Where it lives — alongside the Swiss study, or as a separate deployment — is still being
weighed and is **not** this plan's to settle. Note the unresolved publication question in
§ Data handling: whether forecasts trained on restricted measurements inherit the
restriction should be settled before the pilot is placed. Note also D12: with these rows
imported as `RAW`, a pilot cannot read them until a QC pass is decided and run.

**D10 — the one out-of-range value at station 670. CLOSED: import as delivered.**
One value in 94,617 — the subset that has a curve at all — falls outside its range. Note the second
review's catch: "import it and flag it" is not free — a QC flag requires a rule id, a rule
version, and a status that is neither raw nor missing (`types/domain.py:88-101`), so
flagging one row invents a QC rule identity and leaves exactly one QC'd row among 99,245
that are not. That is a policy act, and it cannot sit under T4's "no QC policy change".
**CLOSED: import it as delivered** (owner, 2026-09-10), with T5's re-measure as the standing
record that it exists. No bespoke QC rule is invented for one row. Note this is now partly
overtaken by D12: our own QC runs over the whole series anyway, so if the value is genuinely
anomalous the range rule will say so on its own terms — which is a better outcome than a
hand-made flag, because it is the same judgement applied to all 99,246 values.

**D11 — what "side dataset" means. CLOSED: a new tenant in the existing database.**
D5 said "side dataset first" and the author never defined it. The second review found the
phrase appears nowhere in the repository outside this plan, and that the gap is not
cosmetic: `stations.tenant_id` is NOT NULL with no server default (`db/metadata.py:292-297`)
and `services/tenant_boundary.py:12` hard-errors on an unknown tenant code, so T2b cannot
insert a station without an answer. **CLOSED: a new tenant in the existing database** (owner, 2026-09-10) — real isolation for
restricted data without standing up separate infrastructure, matching how the schema already
partitions. T2b provisions it; the six stations stay at `onboarding` status regardless.

**Round 2 addition — the tenant needs an identity, not just a topology.** T2b cannot say
"the tenant D11 names" if D11 names none. Fix the code and display name in T2a's artifact
alongside the stations, and provision **fetch-before-create**: `store_tenant` is a plain
insert, so re-running T2b would otherwise fail on the second attempt — the same class of
defect D6 hit on curves.

**D12 — what QC status the imported rows carry. CLOSED, and the answer is better than the
recommendation it replaced: run our own daily QC over the series.**

The author recommended importing everything as `RAW` — unchecked — to avoid asserting a
quality review that never happened. The owner supplied the missing context and a better
answer: **DHM states this is quality-checked regime data**, and we should nonetheless **pass
it through our own daily QC** rather than inherit their claim or fake one of our own. The
status each row carries is then whatever our QC decides, which is the only status that is
true by construction.

**The machinery already exists and already covers this cadence.** `Stage1QualityChecker`
(`services/qc.py:225`) selects rules by inferred time step. Nothing new has to be built to run
QC on a daily series.

**🔴 But the earlier revisions of this plan described the wrong rule set, and so did their
evidence.** They read `config/qc_rules.py`'s `_default_swiss_qc_rules()` and assumed it was
what runs. It is not: `load_qc_rules` returns the built-ins **only when no `[qc_rules]` section
exists**, and this repo's `config.toml:207` supplies one — 26 rules, not the 28 in code. The
defaults function is dead at runtime. Measured against the config actually in force, the daily
discharge tier is **four rules, not five**:

| Daily discharge rule | In force? | Threshold |
|---|---|---|
| range check | yes | max 5,000 m³/s |
| rate of change | yes | max 500 m³/s/day |
| spike | yes | tolerance **0.5** (the code default is 0.1) |
| gross outlier | yes, but **inert** — no baselines (D16) | k-sigma 5 |
| frozen sensor | **absent — no daily discharge rule exists** | — |

So **three rules can actually fire**. Every "five rules" statement in earlier revisions was
wrong, and the frozen-sensor row in the table below described a rule that is not deployed.

**But the thresholds are Swiss, and applying them unchanged would be worse than not running
QC at all.** Measured against the delivered data:

Re-measured against the **deployed** thresholds, and simulating the service's own behaviour
(no elapsed-time guard) rather than an idealised one:

| Daily rule in force | Would flag | of which from gap-bridging |
|---|---|---|
| range check | 1,126 (1.13%) | — |
| rate of change | 2,642 (2.66%) | 4 |
| spike (tolerance 0.5) | 665 (0.67%) | 1 |
| frozen sensor | rule not deployed — cannot fire | — |
| gross outlier | inert, no baselines (D16) | — |

**The gap-bridging defect is real but small here** — 5 rows across 99,246. Earlier revisions
implied it was a major source of false flags; measured, it is not. Fix it for correctness, not
because it distorts this import.

The range-check number is the dangerous one: **1,125 of those 1,126 are at station 450**,
a 31,650 km² basin whose genuine monsoon peaks exceed the Swiss ceiling by nearly threefold.
Running the Swiss rule would mark the single most important part of the record — the floods —
as out of range. That is not QC; it is a calibration error wearing QC's clothes, and it would
be recorded as a quality judgement on data DHM has already checked. **D14 covers the
calibration.**

**Sequencing consequence:** QC is a distinct step after import, not a property of it. T4
imports at `RAW` — honestly unchecked, because at that instant it is — and **T7** runs the
QC pass that assigns the real status. A pilot reads the data only after T7.

**D13 — interpolation method. CLOSED: linear.**
T3 requires all 112 curves to pass the converter "with the interpolation actually selected",
and the method was never selected. Measured for this decision: **no tabulated discharge in
any of the 7,869 points is zero or negative**, so log-linear is representable and the data
does not force the choice. **CLOSED: linear** (owner, 2026-09-10) — it matches the column default
(`db/metadata.py:601-606`), and the author's inversion of the daily series through these
tables under linear interpolation put all but one value in range, which is weak but
directional evidence that linear is what DHM uses. Worth confirming with DHM; not worth
blocking on.

**D14 — how the DHM daily QC thresholds are calibrated. REOPENED 2026-09-10: the closed answer aimed at the wrong target.**
Opened by D12's answer. Swiss thresholds cannot be reused (see the table above), so a DHM
daily rule set is needed. Three constraints on any answer, two of them found in round 2:

- **Circularity.** Deriving a threshold from the record it will then judge guarantees the
  record passes and makes the QC decorative.
- **The author's proposed escape does not fully work.** It recommended per-station limits
  from each station's rating-table ceiling, on the grounds that the tables are independent of
  the daily series. Both round-2 passes rejected that: this plan elsewhere reports the daily
  values are *consistent with having been produced from those same tables*, so their ceilings
  are not independent evidence. At most this is a rating-envelope consistency check, and it
  must be described as one.
- **🔴 A table-derived limit would publish a restricted value.** A per-station `value_max`
  taken from the highest tabulated discharge **is** an individual tabulated value, recoverable
  by anyone reading the config — the exact test § Data handling sets, and the same leak class
  already stripped from this document once. Any table-derived limit must pass through a
  deliberately lossy transform (round up to the next 1,000 m³/s, or scale by basin area) and
  D14's answer must say which.

**Also note the seam is not what the author assumed.** `StationQcOverride` is a dataclass
only (`types/domain.py:170-176`): there is **no** `station_qc_overrides` table, no store, no
loader, and both production callers hard-code `overrides=[]`
(`services/onboarding.py:799`, `flows/ingest_observations.py:308`). Per-station overrides can
therefore be constructed in-process by the import CLI and passed to the checker, but they
cannot be *persisted* without new schema. Prefer the in-process route; say so explicitly
rather than writing "override rows".

**Reopened because the author framed the target wrongly, and the owner caught it.** The closed
answer asked for each station's **plausible maximum**. The project's own standard says a range
check is something else entirely: *"a physical-impossibility gate, not an outlier filter …
deliberately unreachable rather than discriminating"* (`docs/standards/wmo.md:89-91`). A
plausible maximum **is** a discriminating filter, and it would reproduce the Swiss failure one
notch lower — 5,000 m³/s flags 1,125 genuine monsoon peaks at station 450 precisely because it
is a *plausible Swiss* maximum rather than an impossible one. A "plausible Nepali maximum" would
condemn the largest real floods in the record, which are the rows that matter most.

**Owner decision 2026-09-10 — WHERE the thresholds live: station onboarding configuration,
updatable later.** That settles the shape and is the right call: a QC ceiling is station
metadata, belongs with the station, and must be correctable without a code change.

**🔴 It also adds scope this plan did not have, because none of that exists today.** Verified:
there is **no** `station_qc_overrides` table, no store, no Protocol, no config key — the search
returns nothing anywhere in `src/`, `alembic/`, `config.toml` or `docs/spec/`.
`StationQcOverride` is a bare dataclass (`types/domain.py:170-176`) that both production callers
hard-code to an empty list. So per-station QC thresholds cannot be onboarded, cannot be
persisted, and **cannot be updated later** — the plan's earlier answer (build six in memory
inside the import) quietly accepted that and would have produced thresholds nobody could change
without editing code.

Delivering the owner's decision therefore requires: a migration and table, a store and Protocol
and fake, a config surface on station onboarding, and a loader wired into the QC call path —
none of it currently in any task. `station_thresholds` (`db/metadata.py`) is a useful precedent
for the shape (per-station, per-parameter, with a `source` discriminator) but is a **different**
concern: those are alert danger levels, not QC bounds. Do not overload it.

**Open question for the next session:** does that persistence work belong in this plan, in Plan
264, or in its own? It is squarely the gap 264 declared out of scope, and it is now load-bearing.

**The target is an impossibility ceiling: a value water cannot produce, only a fault can.**
Exceeding it means the number is wrong, not that something remarkable happened.

*Recommendation:* derive it from **catchment area against a published flood-envelope curve**
(Creager / Francou–Rodier or equivalent) — the standard instrument for "the largest discharge
ever observed anywhere from a basin of this size" — then round up coarsely. That basis is
**not circular** (external published hydrology, no dependence on this delivery or DHM's tables),
**not a leak** (the input is catchment area, already in DHM's own published station list; the
output is a round number far above any real value), **the right shape** (unreachable by
construction, so it catches faults and never floods), and **cheap** (one number per station from
an area T2a already transcribes). **The team's hydrologist sets the coefficient** — the author is
not qualified to and must not guess it here.

The same reframing applies to `max_rate` and the spike tolerance: both are currently written as
plausibility filters and will flag real monsoon rises unless restated as impossibility gates.

The rating-table envelope may still serve as a **cross-check computed at re-measure time and
never committed**; it is not the basis.

**How six different limits are actually expressed — the gap the set review found.** Plan 264
adds a *network* dimension, so one DHM rule serves all six stations with one ceiling. Six
station-specific maxima therefore cannot come from the rule alone. They come from
`StationQcOverride`, which already expresses exactly this and which
`Stage1QualityChecker.check` already merges per station and rule — it simply has no
persistence, so the import constructs the six in memory and passes them in. **Plan 263 T7 owns
building those six and proving each station's effective merged ceiling; Plan 264 owns only
which rule they merge into.** Without that split written down, an implementation could satisfy
both plans with a single shared DHM maximum and pass every stated gate.

**D16 — the gross-outlier rule needs baselines that cannot exist yet. CLOSED: drop the rule for this import.**
The set review found this is a chicken-and-egg, not an implementation detail. `_apply_gross_outlier`
needs a climatological baseline per station and day-of-year (`services/qc.py:206-208`), and the
existing lifecycle computes baselines **only from observations that are already `QC_PASSED`**
(`services/onboarding.py:836-870`) — which these will not be until the QC pass runs, and which
these stations skip anyway because they carry no forecast targets (T2b). Leaving the choice to
the implementer, as T7 did, means a silent zero flag count that reads like a clean pass.
**CLOSED** (owner, 2026-09-10): **drop gross-outlier from the DHM set for this import**,
explicitly via `skipped_rule_ids` — never by letting it resolve and report zero. Bootstrapping
baselines from unchecked data in order to judge that same data is the circularity D14 exists to
avoid. **Three** rules then carry the pass — range, rate-of-change and spike. Frozen-sensor is
not deployed at this cadence at all (D12), so the DHM set has no fourth rule to fall back on;
that thinness is a reason to look again at the rule set later, not a reason to bootstrap
baselines circularly now.

A 52-year daily record is ample to compute proper baselines **after** it has been QC'd by those
four, which is the honest order. That is deliberately left for later work and is not scoped
here; T7's only obligation is that the exclusion is explicit in the run's output, so a reader
can never mistake "this rule was not run" for "this rule found nothing".

**D15 — how the DHM rules are isolated from the Swiss ones. CLOSED: the rules learn their network.**
T7 claimed it could add a DHM daily rule set "beside" the Swiss one without changing Swiss
behaviour. Both round-2 passes found that is not achievable as stated, for two independent
reasons:

- `rules_for` matches on **parameter and time step only** (`types/domain.py:160-167`) — no
  network, tenant or station dimension. Put DHM daily discharge rules in the same rule set
  and *both* fire on any daily discharge series, with `aggregate_qc_status` taking the worst.
  The Swiss ceiling would still flag the monsoon peaks, so the calibration would achieve
  nothing.
- The config route is worse. `load_qc_rules` returns the Swiss defaults **only when no
  `[qc_rules]` section exists** (`config/qc_rules.py:263-268`), and the overlay's `_deep_merge`
  replaces lists wholesale (`config/_overlay.py:44-56`). A DHM rule set delivered through TOML
  would therefore **delete** the Swiss rules for that deployment — while T7's proposed gate
  ("the Swiss defaults are byte-identical") still passes, because the default *function* is
  untouched.

**CLOSED: teach the rules which network they apply to** (owner, 2026-09-10) — the selector
dimension, not the in-process workaround the author recommended. It is the better answer: the
collision is a real limitation for any deployment serving two networks, not an artefact of
this import, and the workaround would have left it in place for the next caller to rediscover.

**It changes shared code the running Swiss deployment depends on, so it is its own plan:
Plan 264, which now blocks this one's QC task.** That is a deliberate split — the regression
risk to a live QC path deserves its own review and rollout rather than a paragraph inside a
data-import plan. T7 consumes the result; it does not build it.

## Tasks

Every task inherits § Data handling: no excerpt of the delivered files is checked in, and
fixtures are synthetic.

### T1 — DHM file parsers

**Outcome**: pure functions parsing a daily-flow file and a rating-table file into frozen
dataclasses, with no I/O and no DB dependency. Malformed input raises a typed error; a line
that is neither header nor data is never silently skipped.
**In**: `src/sapphire_flow/adapters/dhm_files.py`;
`tests/unit/adapters/test_dhm_files.py`; synthetic fixtures under
`tests/fixtures/dhm/` (hand-written, never delivered content).
**Out**: no store writes, no station resolution, no timestamp policy — T1 yields dates and
values as delivered; the UTC boundary belongs to T3/T4 and follows D6's working assumption.
**Verification**: `uv run pytest tests/unit/adapters/test_dhm_files.py` — covering a
truncated block, a non-numeric value, an unknown line inside a rating block, a declared year
absent from the data, and a negative stage (accepted, not rejected); each asserting the
typed error and its message fragment, not merely that something raised. **Self-contained:
the earlier revision made T1's gate depend on T5, which runs after it — that could never
have been satisfied at T1's completion.**
**Pre-change**: N/A — new module.

### T2a — Station metadata artifact

**Outcome**: the six stations' transcribed metadata as a reviewable checked-in artifact.
Station codes and station-list metadata are publishable (D5); the measurements are not.
**In**: `tests/fixtures/dhm/stations.toml`;
`tests/unit/config/test_dhm_station_metadata.py`.
**Out**: no DB writes.
**Verification**: `uv run pytest tests/unit/config/test_dhm_station_metadata.py` — asserts
all six carry, and are well-formed in, every column `stations` requires NOT NULL: `code`,
`name`, `location` (lon/lat in range, to become POINT srid 4326), `station_kind = river`,
`network = dhm`, `timezone = Asia/Kathmandu`, `measured_parameters = ["discharge"]` (the
canonical parameter name, `docs/conventions.md:87-89`), `station_status = onboarding`,
`ownership`, `gauging_status`, and **`tenant_id`** (NOT NULL, no server default — omitted
from the previous revision's "complete" enumeration, which is the same fault it was written
to fix). `altitude_masl` is nullable but carried. **Drainage area has no column on
`stations`** — it belongs to `basins` (`db/metadata.py:100`); keep it in the artifact as
transcribed reference, and do not imply a station field for it.
**`forecast_targets` stays unset** — see T2b.
**Pre-change**: N/A — new data. **Gated on the owner's spot-check (D4).**

### T2b — Station onboarding into the side dataset

**Outcome**: six `stations` rows exist where T3 and T4 can reference them. Added after the
first review found nothing created them; bounded after the second found it could not be
implemented as first written.
**In**: `src/sapphire_flow/cli/import_dhm_delivery.py` (station branch), reusing
`services/onboarding.py`; whatever tenant provisioning D11 selects.
**Out**: **no `station_status` promotion** — these stay `onboarding`. Promotion switches on
ingest and forecasting for a station and is not this plan's to trigger.
**No QC.** Round 2 found that the onboarding service is itself a QC pass: its Step 5 fetches
RAW observations and runs `Stage1QualityChecker` over them with the deployment rule set
(`services/onboarding.py:772-830`). Re-run after T4 — which T2b's own idempotency check
requires — that would apply the **Swiss** 5,000 m³/s ceiling to the DHM series, producing
exactly the outcome D12 calls a calibration error wearing QC's clothes. It is guarded today
only by `station_target` being empty, i.e. by `forecast_targets` being unset — which T2a never
required until now.
**Verification**: `uv run python -m sapphire_flow.cli.import_dhm_delivery stations --dry-run`
then without it; after running against the D11 tenant, six rows exist with the expected
codes, `network = dhm`, `station_status = onboarding`; re-running it produces no duplicate
rows **and no tenant duplicate** (`store_tenant` is a plain insert — idempotency does not come
free, so provision fetch-before-create); and **`forecast_targets` is NULL on all six**, with
an assertion that onboarding's QC step took its `continue` branch for every station rather
than running.
**Pre-change**: N/A — new path. **Depends on T2a. Unblocked** — D11 selects a new tenant in
the existing database; T2b provisions it and inserts against it.

### T3 — Rating curve import

**Outcome**: 112 curves stored with a chronological `version`, DHM's type label kept as
provenance, half-open validity derived from DHM's inclusive end date, and no station left
with an open-ended curve.
**In**: `alembic/versions/0056_dhm_delivery_provenance.py` (next free revision; no other plan
claims 0056) — adding **two** columns: the rating-type provenance column on `rating_curves`
(D1) **and a delivery-identity column on `observations`** (D6). The second was stated in D6 but
never reached this task until round 4 caught it; without it the replacement procedure cannot
identify the 4,629 curve-less rows and falls back to the over-broad predicate D6 exists to
remove. **`src/sapphire_flow/db/metadata.py`** (both tables are hand-maintained there);
`src/sapphire_flow/types/observation.py` and `src/sapphire_flow/store/observation_store.py`
(the new column must reach `RawObservation` and its writer, which mints ids itself at `:73`);
`src/sapphire_flow/protocols/stores.py` and `tests/fakes/fake_stores.py` to match;
**`tests/unit/db/test_alembic_head_release_b.py`** (`_RELEASE_B_HEAD` is pinned at `"0055"`
and asserts a single head — a new migration fails the suite until it is bumped);
`src/sapphire_flow/types/rating_curve.py`; `src/sapphire_flow/store/rating_curve_store.py`;
**`tests/fakes/fake_stores.py`** (its `fetch_curve_at` at :1587 returns the first
insertion-order match while its batch sibling at :1625 already resolves last-wins — fixing
only the database reader would split unit from integration behaviour);
`src/sapphire_flow/cli/import_dhm_delivery.py` (curve branch);
**`tests/unit/store/test_fake_rating_curve_store.py`** (new).
**Out**: no conversion of any observation.
**Verification**:
- `uv run pytest tests/integration/store/test_rating_curve_store.py` for the database
  reader. **This is the integration tier deliberately**: `fetch_curve_at`'s
  `.one_or_none()` behaviour is a property of a live Postgres result and is not observable
  from a unit test. The previous revision named `tests/unit/store/test_rating_curve_store.py`,
  which does not exist — that gate would have exited on a missing file.
- `uv run pytest tests/unit/store/test_fake_rating_curve_store.py` for the fake, asserting
  the **same** overlap outcome, so the two cannot drift. **This file does not exist yet and is
  listed under In above** — round 4 caught the previous revision naming a second nonexistent
  path in the very bullet that fixed the first one.
- `uv run pytest tests/unit/db/test_alembic_head_release_b.py` after bumping the head pin.
- Import assertions: all 112 pass `RatingConverter.from_curve` under the D13 interpolation;
  versions are 1..N in date order per station with no gaps; DHM's type label round-trips
  through store and read-back; every imported curve has a non-null `valid_to`.
**Where deletion lives.** Neither store exposes a delete today
(`store/rating_curve_store.py:18-36`, `store/observation_store.py:33-64`), so the replacement
needs one. Add bounded, delivery-scoped delete operations to both stores and their Protocols
and fakes — not ad-hoc SQL in the CLI, which would put a destructive operation outside the
store boundary every other write in this repo respects. One transaction spans both deletes.

**Also in scope after round 2: T3 must be re-runnable.** `store_rating_curve` is a plain
insert against `UNIQUE (station_id, version)`, so today a second run raises. D6's replacement
procedure requires curve deletion to be possible, which the composite FK from `observations`
blocks unless observations are deleted first. Implement the delete-then-reimport path and
order it correctly; do not add `ondelete` cascade to a shared FK to make this easier.
**Pre-change**: two RED tests — (a) `fetch_curve_at` raises `MultipleResultsFound` on two
simultaneously-valid curves, from a synthetic two-curve fixture; (b) a second
`store_rating_curve` of the same station+version raises — **at the integration tier**, since
the fake stores by `curve.id` in a dict (`tests/fakes/fake_stores.py:1573-1575`) and enforces
no uniqueness, so this can never go RED against it. Both the actual defect and
its actual cause, not signature errors.
**Unblocked.** D13 selects linear interpolation; D6 supplies the working day boundary
(00:00–23:59 Asia/Kathmandu). Both are read from **one shared constant** with T4 — the two
imports must not drift, or `fetch_curve_at` selects the wrong curve at a seam. An inclusive
DHM end date becomes `valid_to` = 00:00 Asia/Kathmandu on the following day, expressed in
UTC; assert that explicitly on the three known overlap days.

### T4 — Daily discharge import

**Outcome**: 99,246 observations, no row for any missing day, no `MISSING`-status rows, each
labelled `MANUAL_IMPORT` with the covering curve recorded as provenance (D7) and imported at
`RAW` — genuinely unchecked at that instant. T7 assigns the real quality status.
**In**: `src/sapphire_flow/cli/import_dhm_delivery.py` (observation branch);
`src/sapphire_flow/store/observation_store.py` — **not read-only**: T3 adds the delivery
column and this task writes it on every imported row. The previous revision's "no change
expected" was left standing after D6 said it could not be.
**State which writer is used and why.** `store_raw_observations` forces `qc_status = RAW` on
conflict (`observation_store.py:104`); `store_observations` rewrites the status from the
object. Either way a T4 re-run after T7 discards the QC result, which is why D6's procedure
re-runs T7 last. Choose `store_raw_observations` — it is the honest writer for unchecked
imported data and its conflict predicate already refreshes curve provenance.
**Out**: no QC rule authored and no QC run — that is T7. No skill or training wiring.
**Verification**: per-station stored row count equals **T5's re-measured count for that
station** — expressed as a delta against T5, not as a literal, because which curve is in
force on a day depends on the D6 boundary and an absolute number would silently ratify
whatever the implementer produced; no row exists on any gap day T5 reports; the count of
values outside their curve's range equals T5's count (currently 1, imported as delivered per
D10); every row is `MANUAL_IMPORT` at `RAW`, carrying a curve id wherever T5 says a curve
covers the day and NULL on the 4,629 days it says none does. Plus the **boundary-change
rehearsal** D6 requires — **stopping at T4**: import under boundary A, run the replacement's
delete steps, re-run T3 and T4 under boundary B, and assert **no row from boundary A survives**
and that an unrelated manual observation and curve do survive. The rehearsal deliberately
excludes the T7 re-run: D6's full procedure ends with QC, but requiring that here would make
T4 depend on Plan 264 through the back door. T7 owns re-running itself. A same-boundary re-run
assertion is not a substitute — it passes in the one case the requirement does not care about.
**Pre-change**: N/A — new import path.
**Unblocked**, on D6's working assumption. **Re-importability is a requirement** (D6) — and
it is delivered by the explicit replacement procedure there, *not* by upsert semantics. The
previous revision's claim that a re-import "upserts in place" was false: `timestamp` is part
of the natural key, so a boundary change creates new rows and orphans the old ones.

### T5 — Delivery re-measure

**Outcome**: a re-runnable script reproducing the aggregates in this plan, so a later
delivery can be diffed against this one and the plan's unverifiable claims become
reproducible on the owner's machine.
**In**: `scripts/dhm_delivery/remeasure.py`. Note `scripts/` carries no pyright gate in this
repo — this script is not type-checked by CI.
**Out**: not a test gate; not a flow.
**Permitted output — the complete allowed set**: row and curve counts; counts and
percentages of missing days, gap runs, curve-less days and out-of-range values; first and
last dates; gap start dates and lengths; curve validity windows and point counts; per-file
value-precision percentages; **and per-rule QC flag counts and rates** (added in round 2 —
without them the plan's own tool cannot reproduce the figures D12's table and T7's gate rest
on). **No stage value, no discharge value, in output or in failure diagnostics** — including
no threshold that is itself a tabulated value (D14).
**Verification**: `uv run python scripts/dhm_delivery/remeasure.py --check` exits 0 when its
output matches the aggregate tables in this plan, and prints a diff and exits non-zero when it
does not. **The QC flag counts are compared as exact values, not as a floor** — the earlier
"floor, not a prediction" hedge made this gate unsatisfiable by construction, and it is no
longer needed now the counts are measured against the deployed thresholds and the service's
actual gap behaviour (D12).
**Pre-change**: N/A. **Depends on T1** — it needs the parser and nothing else.

### T6 — Documentation and the publication guard

**Outcome**: the docs this plan invalidates are corrected, and the no-publish constraint
stops resting on prose. Added after the second review found no task did either.
**In**: `docs/spec/types-and-protocols.md` — the `RatingCurve` type, the `RatingCurveStore`
Protocol's `fetch_curve_at` contract, **and lines 810 and 821, which say `rating_curve_id` is
set only when the source is rating-curve-derived** (D7 widens that; see below);
`docs/architecture-context.md` — the `rating_curves` table entry (**at :2278, not the §2206
this plan inherited from Plan 035 — that line is the `QcStatus` section**) and **:300**, which
carries the same `rating_curve_id` claim; `docs/touchpoint-maps.md` — the Persistence / API
write-path map still states that `RatingCurveStore` has **no `Pg*` implementation**, which
`src/sapphire_flow/store/rating_curve_store.py:18` contradicts; `.gitignore` and
`.pre-commit-config.yaml` for the guard.
**Out**: no code change.
**Verification**: `uv run pre-commit run --all-files` passes; and the guard is proven by
**invoking the named hook directly** on a force-staged synthetic prohibited path, asserting
non-zero, then asserting an allowed path still passes. A plain `git add` test is not sufficient
evidence: `.gitignore:21` already ignores `data/`, so the refusal could come from the
pre-existing ignore rule and the hook could be inert. Note the honest limits — every hook in
`.pre-commit-config.yaml` is scoped to `^(src|tests|docs|\.github|scripts|tools)/`, so a file at
the repo root or under a new top-level directory is seen by no hook, and `--no-verify` bypasses
all of them. The guard narrows the failure; it does not remove the need to not copy the files in.
**Pre-change**: N/A for the documentation half. For the guard, the discriminating evidence runs
*after* the hook lands and *before* it is trusted: stage a file at a delivered-data path and
confirm the hook refuses it, then confirm an ordinary file still commits. The previous revision
asked the new hook to reject something before the hook existed, which is not a test anyone can
run.

### T7 — DHM daily QC rule set, and the QC pass

**Outcome**: a DHM daily rule set exists, isolated from the Swiss one, and every imported
observation carries a quality status our own QC actually assigned. Added on the owner's
instruction (D12): DHM calls this quality-checked regime data, and we check it ourselves
rather than inherit or fake that claim.

**Round 2 rewrote this task.** As first written it could have marked all 99,246 rows
quality-passed with **zero rules run**, and its acceptance test would have passed in that
state. The three faults, all verified in source:

1. **It fails open.** `aggregate_qc_status([])` returns `QC_PASSED`
   (`types/domain.py:104-106`), and `rules_for` returns an empty tuple when nothing matches
   (`types/domain.py:160-167`). The time step is *inferred* from the median inter-row gap
   (`services/qc.py:40-47`), so any series whose median gap is not 86,400 s matches no rule,
   collects no flags, and is written `qc_passed`. "No row left at RAW" is *satisfied* by that
   state, and the per-rule counts read zero — indistinguishable from "ran and found nothing".
2. **The rules bridge gaps.** Rate-of-change and spike take the previous and next element of
   the *list* with no elapsed-time check (`services/qc.py:71-90`, `:150-196`), and
   frozen-sensor ignores timestamp continuity. Across station 647's 471-day gap they would
   compare a 2009 value to a 2011 one against a per-day threshold. (The author's own
   measurements in D12 guarded on consecutive days, so the real service would flag **more**
   than an elapsed-time-guarded measurement would suggest. D12's table now reports the
   service's own behaviour, measured both ways: the difference is 5 rows in 99,246.)
3. **One of the five rules is inert.** `_apply_gross_outlier` returns `None` when no
   climatological baseline exists (`services/qc.py:205-209`). **Correction:** an earlier revision
   said "every existing caller passes `baselines=[]`" — that is false. `flows/ingest_observations.py:309`
   passes real baselines fetched at `:297`; only `services/onboarding.py:800` passes an empty
   list. The DHM import has no baselines either way, so the conclusion stands, but the stated
   reason did not.

**In**: a DHM rule set declaring `network = "dhm"`, selected by the network-aware lookup
**Plan 264** delivers (D15). **The route is `config.toml`'s own `[[qc_rules.rules]]` array** —
the rule list already in force, to which DHM-network rules are added alongside the existing
ones. Round 4 found the previous revision excluded every route it named (not the code defaults,
not a TOML overlay) while never naming one that works, leaving only the in-process workaround
the owner rejected at D15 — which would have needed nothing from Plan 264 at all, making the
dependency vacuous. Adding to the base rule list is the route that genuinely requires 264's
network dimension, because that is where DHM and Swiss rules coexist and would otherwise
collide. **Not** an edit to `config/qc_rules.py`'s dead defaults, and **not** a separate TOML
overlay (which replaces the list wholesale — 264's D4); **six in-process `StationQcOverride` objects**,
one per station, carrying D14's physical ceilings (the network rule alone cannot express six
different maxima — see D14);
contiguous-segment splitting before the checker is called, or elapsed-time awareness in the
affected rules; `src/sapphire_flow/cli/import_dhm_delivery.py` (QC branch).
**Out**: no new QC *rule kind* — the daily discharge rules already exist (four configured,
three able to fire — D12) and this task calibrates and applies them. No change to any **Swiss**
rule's values, and no change to an operational QC path for Swiss stations.
**This task does change the deployment configuration**, by adding DHM-network rows to
`config.toml`'s `[qc_rules]` array — that is the route named in **In**, and the previous
revision's blanket "no change to the deployment configuration" contradicted it. What must not
change is any existing row; Plan 264's network selection is what makes adding rows safe.
**Verification**:
- **Fail closed.** Assert that `rules_for('discharge', 86400s)` returned a non-empty set for
  every group processed, and that the rows evaluated per rule equal the group's row count. A run
  where no rule resolved must **fail**, not certify.
  **Segment splitting and fail-closed fight each other, and the plan must resolve it here.**
  `_infer_time_step` returns one hour for any group of fewer than two rows
  (`services/qc.py:41-42`), so a single day isolated between two gaps — entirely plausible in a
  record with 1,173 missing days — resolves no daily rule: silently fail-open today, a hard
  raise once Plan 264 T3 lands. **Segments shorter than two rows are excluded from QC and
  reported as excluded**, by count and by date, rather than being passed to a checker that
  cannot classify them. They are not `qc_passed`.
- **Per-station ceilings actually apply.** Assert each station's **effective merged**
  `value_max` equals that station's D14 figure — six distinct assertions — by calling
  `services/_qc_helpers.py::merge_thresholds` directly. That is the only route that proves the
  merge: asserting on the override objects proves nothing, and reading `QcFlag.detail` embeds a
  discharge value and so collides with the no-values rule. The override must match on all four
  of `station_id`, `rule_id`, `parameter` and `time_step` — **a wrong `time_step` silently
  falls back to the 5,000 ceiling**, which is exactly the failure this gate exists to catch.
  A single shared DHM maximum must fail this.
- **Isolation.** Exactly one daily-discharge rule of each id resolves for the DHM series, and
  a Swiss daily series resolves the Swiss rules unchanged — asserted on **both** paths, since
  the previous gate passed identically for the working and the broken design. This is
  guaranteed structurally by Plan 264's most-specific-wins selection, not by DHM and Swiss
  thresholds happening not to collide.
- **Gaps.** No rate-of-change or spike flag is raised across any gap T5 reports. (Frozen-sensor
  is deliberately absent from the list: D12 established it is not deployed at this cadence, so
  asserting it would pass vacuously — the clause would look like a gate and test nothing.)
- **Provenance.** Flags carry the DHM rule set's version. The hard-coded
  `_RULE_VERSION = "1.0"` at five of six flag sites (`services/qc.py:22`) is **Plan 264's D5**,
  not this plan's to fix — correcting it changes *Swiss* flags' recorded version too, because
  the Swiss rules declare `"1.0.0"`. (Not **every** flag: `_apply_frozen_sensor`
  (`services/qc.py:140`) already uses the configured version. And 264's D5 covers a second half
  this plan does not touch — `services/qc_datum.py:23-26` writes the row-level
  `observations.qc_rule_version` independently and hard-returns `"1.0"`, so this gate could
  otherwise pass while the persisted row still disagrees.) T7 consumes the fix and
  asserts DHM flags carry the DHM version; it does not make the compatibility decision.
- **Gross-outlier is excluded** via `skipped_rule_ids` (D16) — verified to work as assumed
  (`services/qc.py:232,256`, already used in production via `obs_skipped_rules`) — and the
  exclusion is **named in the run's output**, alongside the fact that frozen-sensor is not
  deployed at this cadence. Assert it is skipped, not merely that it produced nothing — a zero flag
  count from a rule that could not fire must never be reportable as a clean pass.
- **The statuses are actually persisted.** `Stage1QualityChecker.check` returns a dict and
  writes nothing (`services/qc.py:225-286`) — persistence is the caller's `update_qc`. Assert
  against the **database** that no imported row remains `RAW` and that the per-status counts sum
  to the imported total. Without this, a QC branch that computes flags and never stores them
  passes every other gate here.
- **The run reports what it did**, per rule: rules resolved, rows evaluated, flags raised, plus
  the excluded short segments and the skipped rule ids. `check()` exposes none of this today, so
  the import CLI must record it as it goes — a gate that cannot be observed is not a gate.
- `uv run pytest tests/unit/config/test_dhm_qc_rules.py`.
**Pre-change**: a **synthetic** RED test demonstrating the mechanism — a series containing a
value above the Swiss ceiling is flagged by the Swiss rule and not by the DHM rule. It proves
the mechanism, not the delivery; **T5's run is the evidence that the fault is real in the
delivered data**, and it cannot be a checked-in test without either reading restricted files
or embedding restricted values.
**Depends on T4, and on Plan 264 shipping (its D5 included).** All decisions closed.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1", "T2a"], "parallel": true },
    { "id": "phase-2", "tasks": ["T2b", "T5", "T6"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T4"], "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T7"], "depends_on": ["phase-4"] }
  ]
}
```

T1 and T2a are genuinely independent. T5 needs only the parser; T2b needs only the metadata
artifact; T6 needs neither. Only T7 is gated — on Plan 264, not on an open decision — and everything through
T4 can be built. T7 additionally waits on **Plan 264**, which is a separate plan with its own
review — not a blocker inside this one. The phase order is also the **re-import order** (D6):
curves before observations before QC, and the replacement runs in reverse.

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
- Any change to an existing Swiss QC rule's values, or to the QC behaviour of Swiss stations.
  T7 **does** add DHM-network rows to the deployment's rule array (its In); it edits no existing
  row, and Plan 264's network selection is what keeps the two apart.
