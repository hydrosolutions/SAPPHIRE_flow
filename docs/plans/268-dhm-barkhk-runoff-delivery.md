---
status: DRAFT
created: 2026-09-10
revised: 2026-09-28
plan: 268
reviews:
  - "codex 2026-09-10 — NOT READY, 5 blockers; all verified, folded"
  - "claude 2026-09-10 — NOT READY, 6 blockers + 7 majors; found most of what codex missed; all verified, folded"
  - "codex 2026-09-10 r2 — NOT READY, 4 blockers; killed the re-import claim and the QC isolation"
  - "claude 2026-09-10 r2 — NOT READY, 3 blockers + 7 majors; same two core failures, independently"
  - "claude 2026-09-24 — NEEDS_CHANGES; timezone history, curve delivery identity, strict QC preflight, and minor consistency findings; folded"
  - "claude 2026-09-25 — NEEDS_CHANGES on a pre-current revision; five material findings and minor edge cases folded"
  - "claude 2026-09-25 — NEEDS_CHANGES on SHA aceede207c898e48608a8efbecfcd375146e14f29b2e2f40eb941d4d911f399f; four major and eight lower-severity findings; findings folded, exact-state re-review pending"
  - "claude 2026-09-25 — NEEDS_CHANGES on SHA 6e9250f06ff14628c4a80f6481403b0d98dea3b254099d7604ab25cb2d45f243; six findings folded, exact-state re-review pending"
  - "claude 2026-09-25 — NEEDS_CHANGES on SHA 26cf51848186a9266a1587cf063e9fcb6803501fce899ec775ed11df1f9a40d3; five findings folded, exact-state re-review pending"
  - "claude 2026-09-25 — NEEDS_CHANGES on SHA 0053df24bc102e56e2fdca1249b6881e7a4174c1b9849c253da14dfb62a28668; RawObservation delivery identity and three minor contract fixes folded"
  - "claude 2026-09-25 — NEEDS_CHANGES on SHA eafdb33dac8154cf79251b2a0a28ed6d590dfb83eb41aa2ff7e377906a9e2655; delivery-scoped QC, Plan 264/269 config seam, schema references, and store ownership findings folded"
  - "codex 2026-09-25 — NEEDS_CHANGES on SHA c0994d0b; atomic replacement transaction finding folded"
  - "codex 2026-09-25 — NEEDS_CHANGES on SHA f2ff783c; shared QC rule-set version bump finding folded"
  - "codex 2026-09-25 — NEEDS_CHANGES on SHA 5cb0fa23; stale migration rationale corrected and focused recheck passed; later D14 threshold revision needs full exact-state review"
  - "claude 2026-09-25 — NEEDS_CHANGES; cross-plan D4 gate, QC pending-state and T7 validator-contract findings; folded, exact-state re-review pending"
  - "codex 2026-09-28 — NO_FINDINGS on SHA cb769d7b; tenant authority, real-data acceptance, collision isolation and pending-state checked"
open_decisions: []
depends_on: [264, 269]
title: DHM Barkhk delivery — parse, verify and import six Koshi/Narayani gauges
scope: Parse the September 2026 DHM runoff delivery (6 daily-discharge series, 112 rating tables, 1 scanned station list) into SAP3 domain types, and land it as onboarding-status station rows + rating curves + observations. NOT a modification or activation of the existing DHM observation adapter, NOT a level→discharge operational path (no level data exists in this delivery), NOT a change to the halted time-grid/phase work, NOT a change to Plan 139's scope.
related: [035]
source: 2026-09-10 — measured directly against the delivered files (README.md: "Received without further comments from Subash via Vishnu on September 8, 2026 via email to Beatrice"). Data counts are measured; the D6 boundary and D14 thresholds are explicit provisional decisions, not observed facts.
---

# Plan 268 — DHM Barkhk delivery: parse, verify and import

## Status

**DRAFT — NOT READY.** The owner selected provisional loose-first thresholds under D14.
Plans 264 and 269 are implemented and merged (#315 and #324). T7 can use their
network-aware rule selection and tenant-aware threshold resolver; their merge is no longer
an implementation gate. An independent Codex review found no issues in this revision;
an independent Claude review of the exact text is still required before READY.

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
  file would succeed. T0 adds a mechanical guard — an ignore pattern plus a pre-commit path
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

Every point on which the real files differ from the synthetic dummy examples in
`docs/requirements/`:

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

| Station | Newest curve ends |
|---|---|
| 447 | 2025-12-31 |
| 450 | 2024-09-28 |
| 604.5, 647 | 2020-07-10 |
| 670, 684 | 2020-07-20 |

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
wait: D6 adopts local 00:00 to the following local 00:00 in `Asia/Kathmandu` as a **stated working
assumption**, with a replacement procedure for correcting it later. That is a deliberate, revisable choice made in
the open, not a discovered fact — and the honesty of it depends entirely on the replacement
procedure actually working, which is why D6 spends more words on that than on the boundary.

Kathmandu did not use one fixed UTC offset across this record: the zone history uses +05:30
before the 1986 transition and +05:45 after it (Nepal was not UTC+05:45 for the full record).
Convert each Nepali date at local midnight with
`ZoneInfo("Asia/Kathmandu")`; never apply a fixed offset. The skill machinery buckets observations
on UTC calendar days.
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

Each decision's closure date is recorded with that decision. D16 was raised by cross-plan review and closed on 2026-09-10.

**D1 — Where DHM's rating-table label goes. CLOSED: we number the curves ourselves.**
`version` is a station-scoped monotonic ordinal: this delivery's curves are ordered
chronologically and start one greater than the maximum version that remains for that station.
D1 does not renumber unrelated curves; if an unrelated higher-version curve is added between
imports, the replacement delivery receives higher versions than it did previously.
DHM's type label is recorded alongside as provenance. Needs a nullable provenance column and a
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

**D4 — Transcribed station metadata. CLOSED: owner-confirmed 2026-09-19.** DHM has
confirmed we may transcribe the scanned station list (D8).

- **Station codes: CONFIRMED by the owner, 2026-09-19.** 447, 450, 604.5, 647, 670, 684
  may become station rows.
- **Coordinates: CONFIRMED by the owner, 2026-09-18**, by inspecting all three sources on
  topographic and satellite imagery. The **published station list is the source of truth**.
  The river-watch/BIPAD portal is a water-level source, NOT a position source: station 670
  is plotted ~1.3 km away on a hillside, and 450 is ~1.7 km out. The reviewed GIS layer
  (`nepal_gauge_outlets.csv`) is not a fallback either — it places 647 1.9 km from a
  position DHM's own two channels agree on to 38 m, and contains no Turkeghat at all.
- **Elevations and drainage areas: still unconfirmed.** They were not part of either
  spot-check. The published drainage areas are, however, usable as a CHECK on basin
  delineation rather than as an input to it.

⚠️ A pour point that is wrong by ~1 km on a hillside still delineates a watershed — just
the wrong one, silently, with nothing raised. Basin extraction must snap to the drainage
network and compare the delineated area against the published area before accepting it.

**D5 — Import target. CLOSED: side dataset first, and the data may not be published.**
See § Data handling — the publication constraint is the load-bearing half of this answer
and binds every task.

**D6 — What a daily value means. CLOSED by a stated working assumption.**
It is a Nepali day (owner). The owner has asked several counterparts for DHM's exact day
definition, expects no rapid answer, and has decided not to wait: **assume a Nepali day runs from local 00:00 to the following local 00:00 in
`Asia/Kathmandu`, using the IANA timezone history, and re-import if we learn otherwise.**

If local midnight did not exist, define the day start as the first valid instant on that local
date. On 1986-01-01 clocks jumped from 23:59:59 to 00:15; do not manufacture a nonexistent
00:00 instant. The first valid instant is 00:15 at +05:45 (`1985-12-31T18:30:00Z`).

That is a sound call, and it is not free — it makes **re-importability a design requirement**.

**The author's first attempt at that requirement was wrong, and both round-2 passes caught it
independently.** The plan claimed that because `source` is part of the observations natural
key, a re-import "upserts in place". The natural key is
`(station_id, timestamp, parameter, source)` (`db/metadata.py::uq_observations_natural_key`) — **`timestamp` is in
it**. A boundary correction changes every timestamp, so a re-import inserts a second ~99,246
rows under new keys and leaves the originals as orphans. The claimed property holds only for
a re-run that changes nothing, which is precisely *not* the scenario it was written for.

The curve side is worse: `store_rating_curve` is a plain `sa.insert`
(`store/rating_curve_store.py:22-36`) against `UNIQUE (station_id, version)`, so T3 cannot be
re-run even once — the second attempt raises. And the curves cannot simply be deleted first,
because `observations` carries a composite FK to `rating_curves` with **no `ondelete`**
(`db/metadata.py`, observations-to-rating-curves FK): once T4 has landed, curve deletion is blocked until the
observations go.

**The requirement is therefore an explicit atomic replacement procedure, not an upsert.** First
parse, convert, and validate the complete replacement payload without changing the database.
Then in one transaction: (1) delete observations tagged
`delivery_id = "dhm-barkhk-2026-09-08"` for the six delivery stations; (2) delete rating curves
with that delivery ID and those stations; (3) insert all replacement curves and observations
under the corrected shared boundary. Commit only when every T3/T4 write succeeds. Preserve
every unrelated manual observation and curve, including records for these same stations. Run
T7 after the replacement transaction commits.

If another table still references one of the delivery's curves, the transaction must roll back
and preserve the complete prior delivery. References may come from `forecasts` or
`observation_versions`, not only observations. Likewise, any insert failure or interruption
before commit must roll back both deletes and all replacement writes. Do not continue to T3/T4
after a blocked delete. Return a non-zero result naming the station, delivery ID, dependent
table or FK constraint, and tell the operator to resolve that reference before retrying.

**The stable package identity for steps 1 and 2 is `delivery_id = "dhm-barkhk-2026-09-08"`**.
Every observation and curve imported from this package carries it; a later package gets a
different identity. Round 4 found the author's proposed identity did not exist. It said to use "the curve binding plus the import's
own recorded id". Neither half works: `observations` has **no column able to hold an import
id** (`db/metadata.py:497-555`), `store_raw_observations` mints `id` itself
(`observation_store.py:90`) so the CLI cannot pre-assign one, and the curve-binding half fails
on precisely the **4,629 rows D7 leaves with a NULL curve id** — which would be
indistinguishable from any other manual import, reinstating the over-broad predicate this was
written to remove.

**T3's migration must therefore add `delivery_id` to both `observations` and `rating_curves`,
in addition to the rating-type provenance column on `rating_curves`**, and T4's In list can no longer call the observation
store "read-only". The rehearsal must assert that the curve-less rows are deleted too, and that
an unrelated manual observation survives it, and an unrelated curve on one of the same six
stations survives it. This proves the delivery-id predicate is narrower than station ownership.

Two supporting requirements:

- T3, T4, T5 and T7 use one shared local-day conversion based on `ZoneInfo("Asia/Kathmandu")`;
  keep UTC persistence and do not hard-code an offset.
- A boundary correction changes the timestamps/validity instants, so the replacement procedure
  deletes only rows and curves carrying this delivery identity.
- **T7 always follows T4, never the reverse.** Both observation writers reset QC state on
  conflict — `store_raw_observations` forces `RAW` (`store/observation_store.py:122`, **conditionally** —
  the `where` at `:128-137` means an unchanged re-run does not reset it) and `store_observations`
  rewrites it from the object unconditionally (`:65`) —
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
(quoted as written; that count was also wrong — the corrected delivery total is 99,246).
The guard is real — `archive_observation_values` rejects anything that is not
`RATING_CURVE_DERIVED` with a curve id (`store/observation_version_store.py:45-54`) — but it
is a filter that *rejects*, not a mechanism that *enrols*, and the path is **unbuilt**:
`fetch_derived_observations_by_curve` raises `NotImplementedError`
(`store/observation_store.py:261`), and nothing in `src/` calls the archive at all. The
hazard is prospective. Stated as it was, it invited rejection on the grounds that the harm
is hypothetical.

The conclusion nevertheless stands, on one verified leg:

- **The label is an identity, not an annotation.** `source` is part of
  `uq_observations_natural_key` (`db/metadata.py`), which is exactly what
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

**Documentation consequence:** the `RawObservation` and `Observation` definitions in
`docs/spec/types-and-protocols.md` and the observations schema in
`docs/architecture-context.md` restrict `rating_curve_id` to rating-curve-derived
observations. The historical v0 scope note and diagram comment in
`docs/spec/database-schema.md` label that field absent from v0 even though the implemented
schema includes it; T6 makes their historical scope explicit. This import lands ~94,617 rows
that contradict the derived-only descriptions. The `observation_versions.rating_curve_id`
description remains correct and must not be changed. The contract is worth widening
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

**D11 — the Nepal tenant and station identity. CLOSED by owner, 2026-09-28.**
Use tenant code `chwrr`, display name `CHWRR Nepal`, in the existing Mac-mini staging
database. This separates restricted Nepal records from Swiss data without a second database.
The six `network = dhm` station rows are also the intended future operational CHWRR rows.
Plan 143 consumes these same records for basin, forcing and target preparation; station
promotion is outside both plans and must never register a second copy. The
global `(network, code)` uniqueness constraint reserves these six identities in this database.
T2b refuses a pre-existing code owned by another tenant and requires explicit reconciliation
before import; it never adopts or duplicates that row. Until promotion, all six remain
`onboarding`. T2a's artifact records the exact tenant code and display name. T2b provisions
it fetch-before-create because `store_tenant` is a plain insert.

The checked-in Swiss deployment permits writes only to `sapphire`. Tenant creation therefore
requires an explicitly authorized one-time global-admin bootstrap run. Later station, curve,
observation, and QC commands resolve a `chwrr`-scoped `WritePrincipal` from deployment
configuration and call `enforce_tenant_isolation` before mutation. Neither the station row
nor a read-only API token grants write authority.

**D12 — what QC status the imported rows carry. CLOSED, and the answer is better than the
recommendation it replaced: run our own daily QC over the series.**

The author recommended importing everything as `RAW` — unchecked — to avoid asserting a
quality review that never happened. The owner supplied the missing context and a better
answer: **DHM states this is quality-checked regime data**, and we should nonetheless **pass
it through our own daily QC** rather than inherit their claim or fake one of our own. The
status each row carries is then whatever our QC decides, which is the only status that is
true by construction.

**The machinery already exists and already covers this cadence.** `Stage1QualityChecker`
(`Stage1QualityChecker.check`) selects rules by inferred time step. Nothing new has to be built
to run QC on a daily series.

**🔴 Earlier revisions used the wrong rule set for their evidence.** They read
`config/qc_rules.py`'s `_default_swiss_qc_rules()` as though it were selected by the checked-in
configuration. It is not: `config.toml` supplies `[qc_rules]` and defines 26 rules (the
function defines 28). The built-ins remain runtime fallbacks when `SAPPHIRE_CONFIG` is unset.
Against the checked-in configuration as it stood before PR #298, the daily discharge tier was
**four rules, not five**. The thresholds and QC measurements below are a **pre-PR #298
snapshot**, not the current Swiss configuration: PR #298 widened the daily discharge range
ceiling from 5,000 to 100,000 m³/s (`config.toml:281-285`). Retain these counts only as
historical evidence for why Swiss limits must not calibrate DHM; they are not current behavior
or an acceptance baseline:

| Daily discharge rule | In force? | Threshold |
|---|---|---|
| range check | yes | max 5,000 m³/s |
| rate of change | yes | max 500 m³/s/day |
| spike | yes | tolerance **0.5** (the code default is 0.1) |
| gross outlier | yes, but **inert** — no baselines (D16) | k-sigma 5 |
| frozen sensor | absent from checked-in config; present in no-config defaults | — |

So **three rules can fire in the checked-in configuration**. The no-config defaults add a daily
frozen-sensor rule, but T7 must use the explicit configured rules. Every "five rules" statement
in earlier revisions was wrong.

**Historical result:** applying that pre-#298 Swiss ruleset unchanged to the delivered data
would have flagged the following rows. The counts below simulate the service's behavior at
that time (without an elapsed-time guard), not today's deployed thresholds:

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
a 31,650 km² basin whose genuine monsoon peaks exceed the pre-#298 Swiss ceiling.
Running the Swiss rule would mark the single most important part of the record — the floods —
as out of range. That is not QC; it is a calibration error wearing QC's clothes, and it would
be recorded as a quality judgement on data DHM has already checked. **D14 covers the
calibration.**

**Sequencing consequence:** QC is a distinct step after import, not a property of it. T4
imports at `RAW` — honestly unchecked, because at that instant it is — and **T7** runs the
QC pass that assigns the real status. A pilot reads the data only after T7.

**D13 — interpolation method. CLOSED: linear.**
T8 requires all 112 curves to pass the converter "with the interpolation actually selected",
and the method was never selected. Measured for this decision: **no tabulated discharge in
any of the 7,869 points is zero or negative**, so log-linear is representable and the data
does not force the choice. **CLOSED: linear** (owner, 2026-09-10) — it matches the column default
(`db/metadata.py:601-606`), and the author's inversion of the daily series through these
tables under linear interpolation put all but one value in range, which is weak but
directional evidence that linear is what DHM uses. Worth confirming with DHM; not worth
blocking on.

**D14 — first-pass DHM daily-discharge thresholds. CLOSED by owner, 2026-09-25.**

The owner's loose-first decision in `docs/v1-scope.md` § QC posture governs this test deployment:
start with permissive thresholds and refine them as the data and QC behavior become better
understood. A local audit parsed all six delivered daily series (99,246 records total); no
negative or zero values or duplicate dates were found. Missing calendar dates remain gaps and
must not be bridged by rate or spike checks. This audit informed the scale below; individual
measurements and observed maxima are intentionally omitted from this plan.

Use these provisional discharge settings:

| Station | Daily `range_check.value_min` | Daily `range_check.value_max` |
|---|---:|---:|
| 447 | 0 m³/s | 20,000 m³/s |
| 450 | 0 m³/s | 100,000 m³/s |
| 604.5 | 0 m³/s | 25,000 m³/s |
| 647 | 0 m³/s | 10,000 m³/s |
| 670 | 0 m³/s | 50,000 m³/s |
| 684 | 0 m³/s | 20,000 m³/s |

The shared DHM daily `range_check` row has `value_min = 0 m³/s` and `value_max = 100,000 m³/s`.
The six station ceilings change only the effective `value_max`; the zero lower bound remains on
the shared DHM rule. Put those ceilings in `[[onboarding.station_qc_thresholds]]` in the base
`config.toml`, mirrored in `docs/spec/config-reference.toml`. For the other DHM daily rules, the
shared network-specific rows set `rate_of_change.max_rate = 100,000 m³/s` per daily step and
`spike.tolerance = 25` (25 times the preceding day's absolute discharge). These settings are
deliberately broad first-pass guards, not physical-impossibility limits, approved operational
limits, or evidence that a value below them is correct. They may let gross errors through. They
are for controlled test and model-development use; do not rely on them for operational decisions.
Do not derive any threshold from rating-table points. Reassess and narrow thresholds with DHM
after reviewing additional records and observed QC outcomes. No hydrologist-approval gate remains
for these initial test values. The merged Plan 269 reports the six declarations as pending INFO
when tenant `chwrr` exists but has no DHM station, or when that tenant does not exist and the
database has no DHM station anywhere. Matching `chwrr` stations still in `onboarding` are also
pending. If `chwrr` is absent while another tenant already has a DHM station, the declarations
are **rejected**, produce WARNING health records on every scheduled run, and fail the edit-time
validator even with `--allow-unonboarded-network dhm`. T7 adds `dhm` to
`onboarding.qc_pending_networks`; before rolling that shared config onto such a database,
bootstrap the empty `chwrr` tenant or defer the config rollout. Do not normalize a permanent
warning. Once any `chwrr` DHM station exists, an unknown code is a warning-level rejection,
so a typo cannot hide as pending. T7's direct import must resolve all six ceilings as
applicable before any QC write. Plan 269's edit-time validator stays strict by default;
the opt-in pending mode applies only under the conditions above and still rejects invalid
rules, parameters, cadences and thresholds.

T7 declares station ceilings through the merged Plan 264/269 configuration interface and adds the
DHM network rules to the configured rule set; it constructs no hard-coded overrides. Plan 269 owns the
configuration schema and resolver, Plan 264 owns network-aware rule selection and provenance,
and T7 owns applying the validated values in the import CLI. These stations stay at
`station_status = onboarding`; scheduled operational ingest remains outside this delivery.

**D16 — the gross-outlier rule needs baselines that cannot exist yet. CLOSED: drop the rule for this import.**
The set review found this is a chicken-and-egg, not an implementation detail. `_apply_gross_outlier`
needs a climatological baseline per station and day-of-year (`_apply_gross_outlier` in
`services/qc.py`), and the
existing lifecycle computes baselines **only from observations that are already `QC_PASSED`**
(`services/onboarding.py:836-870`) — which these will not be until the QC pass runs, and which
these stations skip anyway because they carry no forecast targets (T2b). Leaving the choice to
the implementer, as T7 did, means a silent zero flag count that reads like a clean pass.
**CLOSED** (owner, 2026-09-10): **drop gross-outlier from the DHM set for this import**,
explicitly via `skipped_rule_ids` — never by letting it resolve and report zero. Bootstrapping
baselines from unchecked data to judge those same rows would introduce a separate circular
validation problem. Three rules remain selected, but only range and spike provide independent
first-pass checks at D14's settings: the `rate_of_change` limit equals the shared range ceiling,
so it cannot flag a pair whose values both satisfy range, and any such flag necessarily
accompanies a range violation. T7's run report must label that rule non-discriminating rather
than presenting zero flags as evidence of a clean pass. The configured spike rule can only catch
an extreme isolated upward jump: it requires the value to differ from both neighbors by more
than 25 times the preceding day's absolute discharge (so the central value must exceed 26 times
the previous value); with non-negative discharge, it cannot catch a downward spike, and the
checker skips this rule when the preceding value is zero. This delivery has no zero readings.
The checked-in rules omit
`frozen_sensor`; the no-config fallback contains it, but T7 must use the explicit configured
rule set. The selected checks are a reason to revisit QC later, not to bootstrap baselines
circularly now.

A 52-year daily record is ample to compute proper baselines **after** it has passed the initial
QC pass, which is the honest order. That is deliberately left for later work and is not scoped
here; T7's only obligation is that the exclusion is explicit in the run's output, so a reader
can never mistake "this rule was not run" for "this rule found nothing".

**D15 — how the DHM rules are isolated from the Swiss ones. CLOSED: the rules learn their network.**
T7 claimed it could add a DHM daily rule set "beside" the Swiss one without changing Swiss
behaviour. Both round-2 passes found that is not achievable as stated, for two independent
reasons:

- Before Plan 264, `rules_for` matched only parameter and cadence. DHM daily rules in the
  same list would therefore have run on Swiss stations. The merged selector now also takes
  `network` and replaces generic rows of the same rule, parameter and cadence where a DHM
  row exists (`types/domain.py::QcRuleSet.rules_for`).
- The TOML overlay merge replaces a rule array wholesale. Plan 264 now rejects any
  `[qc_rules]` section in an overlay, so T7 adds DHM rows only to the shared base list.

**CLOSED: teach the rules which network they apply to** (owner, 2026-09-10) — the selector
dimension, not the in-process workaround the author recommended. It is the better answer: the
collision is a real limitation for any deployment serving two networks, not an artefact of
this import, and the workaround would have left it in place for the next caller to rediscover.

**Plan 264 implemented this shared selector in PR #315.** Its Swiss regression risk received
separate review and rollout. T7 consumes the result; it does not build it.

## Tasks

Every task inherits § Data handling: no excerpt of the delivered files is checked in, and
fixtures are synthetic.

### T0 — Restricted-delivery publication guard

**Outcome**: delivered `DFL_*.txt` and `RT_*.txt` files cannot be staged anywhere in the
repository by accident. **In**: `.gitignore`, `.pre-commit-config.yaml`,
`scripts/guard_dhm_delivery_paths.py`, `tests/unit/scripts/test_dhm_delivery_path_guard.py`,
`docs/standards/cicd.md`, and `CLAUDE.md`. The unscoped pre-commit hook rejects those basenames
at the repository root or in any nested directory, except the two exact synthetic documentation
examples already tracked at `docs/requirements/DFL_Dummy Station A.txt` and
`docs/requirements/RT_Dummy Station A.txt`. The ignore rules and guard test must allow only
those exact paths; the test also verifies prohibited synthetic root/nested paths fail and an
ordinary path passes. Its own filename is outside the guarded patterns. **Verification**: run the
focused test and `uv run pre-commit run --all-files`; confirm the existing examples pass and
prohibited paths fail. `--no-verify` still bypasses hooks. **Out**: no runtime behavior.
**Pre-change**: N/A. No other matching-name fixture may be checked in.

### T1 — DHM file parsers

**Outcome**: pure functions parsing a daily-flow file and a rating-table file into frozen
dataclasses, with no I/O and no DB dependency. Malformed input raises a typed error; a line
that is neither header nor data is never silently skipped.
**In**: `src/sapphire_flow/adapters/dhm_files.py`;
`tests/unit/adapters/test_dhm_files.py`; synthetic fixtures under
`tests/fixtures/dhm/` (hand-written, never delivered content; names such as
`synthetic_daily_flow.txt` and `synthetic_rating_tables.txt`, outside T0's blocked
`DFL_*.txt` and `RT_*.txt` patterns).
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
the artifact carries tenant code `chwrr` and display name `CHWRR Nepal`, and all six stations carry, and are
well-formed in, every station column `stations` requires NOT NULL: `code`,
`name`, `location` (lon/lat in range, to become POINT srid 4326), `station_kind = river`,
`network = dhm`, `timezone = Asia/Kathmandu`, `measured_parameters = ["discharge"]` (the
canonical parameter name, `docs/conventions.md:90`), `station_status = onboarding`,
`ownership`, and `gauging_status`. The artifact does not contain a database-generated
`tenant_id`; T2b resolves the tenant code to that ID. Assert `altitude_masl` is NULL: gauge
elevations are not cross-checked, so do not put
them in this artifact or station records. **Drainage area has no column on
`stations`** — it belongs to `basins` (`db/metadata.py:100`); keep it in the artifact as
transcribed reference, and do not imply a station field for it.
**`forecast_targets` stays unset** — see T2b.
**Pre-change**: N/A — new data. **Unblocked**: the owner completed the D4 spot-check.

### T2b — Station registration in the side-dataset tenant

**Outcome**: a guarded, repeatable command can create the six `stations` rows that T3 and T4
will reference; T8 executes it on staging. Added after the
first review found nothing created them; bounded after the second found it could not be
implemented as first written.
**In**: `src/sapphire_flow/cli/import_dhm_delivery.py` (tenant bootstrap and station branches),
`config/overlays/chwrr-import.toml` (new, `[deployment]` only, setting
`writable_tenants = ["chwrr"]`), tenant and station stores, and the T2a metadata artifact.
The bootstrap command creates tenant `chwrr` only when the explicit deployment identity
has `global_admin = true`; it refuses the normal Swiss and CHWRR-scoped identities. It
checks that identity directly before tenant creation because resolving a principal for
`chwrr` requires the tenant to exist already. Because
`resolve_run_principal` validates declared writable tenant codes before resolving the run,
normal CHWRR authority cannot bootstrap an as-yet-unknown tenant. The operator supplies a
temporary, out-of-repo admin deployment overlay with `global_admin = true` and
`writable_tenants = []`, uses it only for this one command, then removes it. No global-admin
profile is checked in. The station command uses the checked-in CHWRR import overlay, resolves
a `chwrr`-bound principal, calls `enforce_tenant_isolation` on the resolved tenant ID before
any station write, and refuses the Swiss-only base identity. It fetches or creates no other
tenant and writes the six station records with the resolved ID. It does
not call the public CAMELS-CH onboarding entry point or the private general onboarding flow:
this delivery has no verified basin, forcing, or MeteoSwiss backfill inputs for that workflow.
It creates no substitute basin or forcing records.
**Out**: **no `station_status` promotion** — these stay `onboarding`.
Plan 269's scheduled-ingest path does not apply QC overrides to stations in `onboarding`; it
reports those declarations as pending INFO. A single newly ingested daily observation also has no
inferable daily cadence in the measured QC context window, so this path does not run the daily
D14 rules on that single-row group. T7 independently runs QC over the full delivery after import
and does not require forecast targets or operational promotion. Promotion remains out of scope.
T2b performs no QC; T7 applies the provisional D14 DHM rules after observations are imported.
T2b keeps `forecast_targets` unset so these stations remain onboarding.
**PR-time verification**: synthetic tenant/station store tests prove bootstrap requires
global-admin identity, `stations --tenant chwrr` refuses the Swiss-only base config and a
wrong target tenant before mutation, and the CHWRR deployment overlay grants only `chwrr`
write authority. The real command sequence and database assertions run in T8, not CI.
After the operator run, six rows exist with the expected
codes, `network = dhm`, `station_status = onboarding`, and the D11 tenant's resolved
`tenant_id`. If a station with the same network and code already exists, require that it belongs
to the D11 tenant; otherwise create it. Because
`uq_stations_network_code` is global across tenants, abort and identify the conflicting tenant
if that code exists elsewhere; never reassign or silently reuse another tenant's station.
Re-running it produces no duplicate rows **and no tenant duplicate** (`store_tenant` is a plain
insert — idempotency does not come free, so provision fetch-before-create); and
**`forecast_targets` is NULL on all six**. Include a cross-tenant same-code refusal test.
**Pre-change**: N/A — new path. **Depends on T2a.** T2b implements the registration path;
T8 executes it against Mac-mini staging after merge.

### T3 — Rating curve import

**Outcome**: an importer can store the 112 curves with unique per-station `version` values in delivery chronology,
DHM's type label kept as
provenance, half-open validity derived from DHM's inclusive end date, and no station left
with an open-ended curve.
**Migration identity**: choose the next free revision in the merged migration graph when
implementation starts; do not reserve or hard-code `0057`. Set `down_revision` to the actual
single head at that time; `0057` is already occupied on `origin/main`. Update the pinned
release-head test to the chosen revision and preserve its single-head assertion.
For each station, number this delivery's curves in chronological order starting at one greater
than the maximum version among curves that remain after delivery-scoped deletion (or 1 when
none remain). Do not renumber unrelated curves. Re-import therefore keeps a surviving
unrelated curve's version. It reproduces this delivery's prior version sequence only if no
unrelated higher-version curve was added since the preceding import; otherwise it allocates a
new sequence above that curve.
**In**: the selected Alembic migration — adding **three** nullable columns: `rating_type_label TEXT NULL` on `rating_curves` (D1), and `delivery_id`
on both `rating_curves` and `observations` (D6). New DHM rows set the stable package identity;
legacy rows remain NULL. Without the observation tag, the replacement procedure cannot identify
the 4,629 curve-less rows; without the curve tag, it cannot safely delete imported curves. **`src/sapphire_flow/db/metadata.py`** (both tables are hand-maintained there);
`src/sapphire_flow/types/observation.py` (add
`RawObservation.delivery_id: str | None = None` and `Observation.delivery_id: str | None = None`),
`src/sapphire_flow/types/rating_curve.py` (add
`RatingCurve.delivery_id: str | None = None` and `RatingCurve.rating_type_label: str | None = None`),
`src/sapphire_flow/store/observation_store.py` (persist `RawObservation.delivery_id` on writes
and return it from the observation row mapper), and
`src/sapphire_flow/store/rating_curve_store.py` (persist these optional values and return them
on reads, preserving existing constructors);
`src/sapphire_flow/protocols/stores.py` and fake stores to match;
**`tests/unit/db/test_alembic_head_release_b.py`** (update `_RELEASE_B_HEAD` to the selected
migration revision and retain the single-head assertion);
**`tests/fakes/fake_stores.py`** (its `fetch_curve_at` returns the first
insertion-order match while `fetch_active_curves_batch_at` resolves last-wins — fixing
only the database reader would split unit from integration behaviour);
`src/sapphire_flow/cli/import_dhm_delivery.py` (curve import function, which accepts the
caller-owned connection and never commits);
`src/sapphire_flow/adapters/nepal_local_day.py` (new shared local-day conversion helper used
by T3, T4, T5 and T7);
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
- Test that an FK-restricted curve delete is translated to the actionable D6 error, and that
  a curves-only delete transaction restores the delivery curves after an injected failure.
- PR-time synthetic assertions: `RatingConverter.from_curve` accepts valid points and rejects
  invalid ones under D13; imported versions are consecutive after any surviving same-station
  version; type labels round-trip through store/read-back; every imported curve has a non-null
  `valid_to`; overlap resolution and the 1986 local-day boundary match D2/D6. The assertion
  that **all 112 delivered curves** convert and round-trip runs in T8 after T4's coordinator
  exists. T3 has no standalone real-data commit command or real-data exit gate.
**Where deletion lives.** Neither store exposes a delete today
(`store/rating_curve_store.py:18-36`, `store/observation_store.py:33-64`), so the replacement
needs one. Add bounded, delivery-scoped delete operations to both stores and their Protocols
and fakes — not ad-hoc SQL in the CLI, which would put a destructive operation outside the
store boundary every other write in this repo respects. T3 implements the curve delete and
translates FK-restricted deletion into the actionable D6 error. Its exit gate tests that a
curve-only delete transaction rolls back and restores the delivery curves after an injected
failure. T4 owns the cross-store replacement coordinator and its integration tests: a forecast-
referenced curve must leave both delivery observations and curves untouched, and an injected
failure after replacement curves are inserted but before observations are written must restore
the prior delivery without a partial replacement.

**Also in scope after round 2: T3 must be re-runnable.** `store_rating_curve` is a plain
insert against `UNIQUE (station_id, version)`, so today a second run raises. D6's replacement
transaction requires curve deletion to be possible, which the composite FK from `observations`
blocks unless observations are deleted first. Implement the atomic delete-and-replace path and
order it correctly; do not add `ondelete` cascade to a shared FK to make this easier.
**Pre-change**: two RED tests — (a) `fetch_curve_at` raises `MultipleResultsFound` on two
simultaneously-valid curves, from a synthetic two-curve fixture; (b) a second
`store_rating_curve` of the same station+version raises — **at the integration tier**, since
the fake stores by `curve.id` in a dict (`tests/fakes/fake_stores.py:1590-1592`) and enforces
no uniqueness, so this can never go RED against it. Both the actual defect and
its actual cause, not signature errors.
**Unblocked.** D13 selects linear interpolation. Put the shared local-day conversion helper in
`src/sapphire_flow/adapters/nepal_local_day.py`. T3, T4, T5 and T7 use this single helper for
D6's working boundary: the half-open interval `[00:00, next 00:00)` in
`Asia/Kathmandu`, converted through `ZoneInfo`. The imports must not drift, or `fetch_curve_at`
selects the wrong curve at a seam. An inclusive
DHM end date becomes `valid_to` = 00:00 Asia/Kathmandu on the following day, expressed in
UTC; assert that explicitly on the three known overlap days.

### T4 — Daily discharge import

**Outcome**: the replacement command can import 99,246 observations, no row for any missing
day and no `MISSING`-status rows, each
labelled `MANUAL_IMPORT` with the covering curve recorded as provenance (D7) and imported at
`RAW` — genuinely unchecked at that instant. T7 assigns the real quality status.
**In**: `src/sapphire_flow/cli/import_dhm_delivery.py` (observation branch and atomic replacement
coordinator);
`src/sapphire_flow/store/observation_store.py`, `src/sapphire_flow/protocols/stores.py`, and
`tests/fakes/fake_stores.py` — **not read-only**: T3 adds the delivery column and this task
owns the observation-side delivery delete, batch collision preflight, fake behavior, and import
writes. `tests/integration/store/test_observation_store_upsert.py` covers the PostgreSQL writer;
`tests/integration/cli/test_import_dhm_delivery.py` (new) covers replacement atomicity and
is extended in T7 for the coordinated QC pass;
`src/sapphire_flow/exceptions.py` defines a typed delivery-collision error;
`tests/unit/flows/test_ingest_observations_dhm.py` and
`tests/unit/flows/test_ingest_observations.py` cover scheduled-ingest collision handling and
Swiss continuity; `src/sapphire_flow/services/onboarding.py` and
`tests/unit/services/test_onboarding.py` cover its existing per-station writer call.
The store rejects a whole batch with that typed error before writing any row. Scheduled ingest
keeps its normal bulk call; only on this error does it retry one station at a time, report the
colliding station as failed, and omit its rows from the later QC cohort while storing and
checking other stations, including Swiss ones. Other storage errors retain their existing
handling. Onboarding already writes per station; a collision fails only that station and its
later stations continue. No caller may treat a short returned ID list as a complete success.
T4 adds the tenant-row `FOR UPDATE` method to `src/sapphire_flow/store/tenant_store.py`,
`src/sapphire_flow/protocols/stores.py`, and `tests/fakes/fake_stores.py`; T7
reuses it. The replacement coordinator acquires it before any delivery-scoped read or write.
For initial import and replacement, stage and validate the complete curve and observation
payloads before mutation. The replacement coordinator then opens one `engine.begin()`
transaction, constructs both stores with that same SQLAlchemy connection, deletes the tagged
delivery rows, calls T3 to insert all curves and T4 to insert all observations, and commits once.
Before any mutation, resolve the `chwrr`-scoped principal from the explicit import config
and deployment-only overlay and enforce tenant isolation for the target tenant. A Swiss-only
or missing deployment identity fails before deletion. The CLI reads its DB URL from the
host's `DATABASE_URL`; it must never print credentials.
Before reading or changing delivery rows, it locks the D11 tenant row `FOR UPDATE` through a
shared tenant-store method. T7 takes that same lock for its full QC pass, so replacement
cannot delete or recreate rows while QC classifies them.
The stores and import functions must not open nested transactions or commit themselves. Thus a
failure at any T3/T4 step restores the old delivery. QC runs only after commit.
**State which writer is used and why.** Choose `store_raw_observations` — it is the honest
writer for unchecked imported data. It persists `RawObservation.delivery_id` on insert and
conflict update. A T4 re-run after T7 discards the QC result, which is why
D6's procedure re-runs T7 last. Both `store_raw_observations` and `store_observations` must
preflight the complete batch and refuse before writing if a natural-key collision has a
different `delivery_id`. Both conflict updates use NULL-safe equality
(`IS NOT DISTINCT FROM`) and set the incoming `delivery_id`: NULL matches NULL for ordinary
ingest corrections; this delivery matches itself; another delivery cannot overwrite it. Test
these behaviors through both writers, including that a normal NULL-tagged correction still
updates and that a tagged DHM row's delivery identity survives any same-delivery update.
Within the replacement transaction, after the writer returns and before commit, read back
the complete intended natural-key set through the observation store and require every key to
carry this delivery's ID. A conflicting insert between preflight and upsert may make the
conditional conflict update skip a row; a missing or foreign-tagged key aborts and rolls back
the entire replacement. Total station/day counts are insufficient because the foreign row
can occupy an intended key.
**Out**: no QC rule authored and no QC run — that is T7. No skill or training wiring.
**Verification**: before writing, refuse any observation natural-key collision
`(station_id, timestamp, parameter, source)` whose existing `delivery_id` is NULL or differs
from this delivery. For conflict updates, compare delivery IDs with NULL-safe equality
(`IS NOT DISTINCT FROM`): NULL matches NULL, so ordinary ingest corrections still update
ordinary rows; this delivery's ID matches itself; another delivery's ID never matches. Include
`delivery_id` in inserted/upserted values. Test that a normal NULL-tagged ingest correction
still updates, that a single tagged collision in a multi-station scheduled batch leaves
unaffected Swiss rows stored and QC-eligible, and that onboarding continues with its next
station after a collision. Convert each delivered date
to the first valid instant of that Nepali local date and then to UTC through `ZoneInfo`, not a
fixed offset, before storage. On 1986-01-01 local midnight was skipped; use the first valid
instant, 00:15 Asia/Kathmandu (`1985-12-31T18:30:00Z`).
Assert those four mappings for both observation timestamps and curve validity boundaries:
`1970-01-01` → `1969-12-31T18:30:00Z`, `1986-01-01` →
`1985-12-31T18:30:00Z` (first valid local instant 00:15), `1986-01-02` → `1986-01-01T18:15:00Z`, and
`2000-01-01` → `1999-12-31T18:15:00Z`. PR-time synthetic tests prove that missing dates
produce no row, `MANUAL_IMPORT` rows begin at `RAW`, a covering curve ID is stored when one
exists and NULL otherwise, and the D10 out-of-range policy preserves the supplied value.
The real per-station counts, gaps, one out-of-range value and 4,629 curve-less rows are
checked during T8's operator run against T5's fresh aggregates. In the **boundary-change
rehearsal** D6 requires — **stopping at T4** — an unrelated manual observation at an overlapping
natural key with a NULL/different delivery tag makes the replacement abort before any write.
For the successful path, import under boundary A, replace under boundary B, and assert no row
or curve from A survives while a separate unrelated manual observation at a timestamp outside
the delivery's imported date range and a same-station curve with a non-overlapping validity
window do survive. The rehearsal deliberately
excludes the T7 re-run: D6's full procedure ends with QC, but requiring that here would make
T4 depend on Plan 264 through the back door. T7 owns re-running itself. A same-boundary re-run
assertion is not a substitute — it passes in the one case the requirement does not care about.
Integration-test a natural-key collision inserted after preflight but before upsert: the
post-write delivery-ID check must roll back the entire replacement. Also test that a
forecast-referenced curve leaves both delivery observations and curves untouched, and an injected failure after replacement
curves are inserted but before observations are written must restore the prior delivery without
a partial replacement.
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
**Permitted output**: row and curve counts; counts and percentages of missing days, gap runs,
curve-less days and out-of-range values; first and last dates; gap start dates and lengths;
curve validity windows and point counts; per-file value-precision percentages. **No stage value
or discharge value** may appear in output
or failure diagnostics.
**PR-time verification**: synthetic fixtures prove `--check` accepts matching aggregates and
reports a mismatch without printing values. T8 runs
`uv run python scripts/dhm_delivery/remeasure.py --input-dir <restricted-dir> --check`
against the real delivery outside CI; it compares data-derived aggregates to this plan's
tables, excluding historical D12 QC flag counts. QC flag counts are excluded:
  the D12 counts use a pre-#298 rule set, and current QC acceptance belongs to T7 tests using the
  provisional D14 DHM rules, not a fixed count table.
**Pre-change**: N/A. **Depends on T1 and T3** — curve-bound counts use the parsed rating
curves and the shared Kathmandu local-day boundary helper.

### T6 — Documentation updates

**Outcome**: documentation matches the completed schema, stores, import workflow, and QC
behavior. Run after T3, T4, and T7 so it describes the implemented contract.
**In**: `docs/spec/types-and-protocols.md` — `RawObservation`, `Observation`, and `RatingCurve`
fields, delivery
scoped delete operations on both store Protocols, and NULL-safe observation conflict updates;
`docs/spec/database-schema.md` — the new observations and rating_curves columns and constraints,
the `observations.rating_curve_id` field description so it covers provenance as well as
derivation, and the historical v0 scope note and diagram comment so they do not read as
current-schema claims. Leave the `observation_versions.rating_curve_id` description unchanged;
`docs/spec/config-reference.toml` — mirror the DHM QC rule rows and version in `config.toml`;
`docs/architecture-context.md` — the observations and rating_curves schemas (including
`delivery_id` and rating-type provenance), the delivery-scoped delete behavior, the local-day
conversion contract, and DHM-network QC configuration/import behavior;
`docs/touchpoint-maps.md` — the persistence/API write-path map for both implemented stores.
**Out**: no forecast/runtime behavior change. T0 updates the pre-commit guard documentation.
**Verification**: each documented field, operation, and QC/configuration path matches the
implemented API and tests; no stale statement remains that limits `rating_curve_id` to derived
observations or omits either store's PostgreSQL implementation.
**Pre-change**: N/A. **Depends on T3, T4, and T7.** This is the final plan task; T0 installs the
restricted-file guard before implementation fixtures or code are added.

### T7 — DHM daily QC rules and import-time QC

**Outcome**: the QC command can assign honest states to every delivered observation. A row is
`QC_PASSED` only when applicable rules actually ran and passed; unclassifiable rows remain
`QC_UNCHECKED` and are reported, never counted as passed.

T7 adds a delivery-scoped observation-store QC update, with Protocol and fake counterparts.
The scoped update requires observation ID and delivery
ID, returns whether exactly one row was updated, and leaves the general `update_qc` API
unchanged. Verify locking and affected-row behavior against PostgreSQL.

**In**: add the delivery-scoped QC update to `src/sapphire_flow/store/observation_store.py`,
`src/sapphire_flow/protocols/stores.py`, and `tests/fakes/fake_stores.py`; add DHM-network
daily-discharge rules to the existing `config.toml` and
`docs/spec/config-reference.toml` rule lists without changing Swiss rows or their per-rule
versions. Keep both files' DHM rule definitions and shared `[qc_rules].version` identical.
Advance that version from
`1.0.0` to `1.1.0` because this changes the deployed rule set; if an approved intervening
configuration change has already advanced it when implementation begins, increment that current
version instead. Each new DHM rule carries its own version under Plan 264. Then use Plan 264's
network-aware selection in
`src/sapphire_flow/cli/import_dhm_delivery.py`. Declare the six D14 provisional station-specific
range ceilings in `[[onboarding.station_qc_thresholds]]` in base `config.toml`, mirrored in
`docs/spec/config-reference.toml`. Add the network-specific daily DHM rules to
`[qc_rules].rules` in both files beside the generic rules; Plan 264 D4 makes the base rule list
authoritative. Declare `dhm` in
`onboarding.qc_pending_networks` in both files so deployments without DHM stations report the
expected pending state as INFO. Resolve the ceilings through Plan 269's
`load_onboarding_config` / `resolve_station_qc_overrides` boundary. Plan 269 does not wire these
overrides into this import command; T7 owns loading the validated configuration, selecting the
six import stations, and passing the resolved overrides and station-to-network mapping to QC.
The six-station QC cohort is selected by the exact station codes in T2a's `stations.toml` and
the target tenant: require exactly one matching DB station per code and `network = dhm`. Read
`delivery_id` through the observation-store row mapper and select only rows tagged
`dhm-barkhk-2026-09-08`; filter before grouping or calling QC so unrelated observations from
the same stations are neither reclassified nor included in temporal comparisons. Do
not select through operational-ingest eligibility or require `forecast_targets` (these six
remain `onboarding`). Resolve each station's ceiling from the per-station config entry keyed to
that station, and require exactly one applicable ceiling for each code. Supply a T7-specific
`is_applicable` predicate for those six station IDs, the three selected rule IDs, and `discharge`; its Plan 269 signature includes station, rule ID and parameter. Separately validate that each configured rule is at the daily cadence before resolution; do not reuse scheduled-ingest operational eligibility. The command must not
construct override objects from hard-coded values. Require a non-empty
`SAPPHIRE_CONFIG` pointing to the explicit config and fail before loading if it is absent.
Load rules only through
`load_qc_rules(config_path=<explicit path>)`; never call `_default_swiss_qc_rules()` and never
accept its implicit fallback. Before the first `update_qc`, load and resolve the explicit
configuration through the final Plan 264/269 interfaces, then assert that the *effective* DHM
daily rules and six station ceilings produce exactly the D14 values for every station. The
shared daily rule values live on `[qc_rules].rules` entries with `network = "dhm"`; each station
ceiling is a local onboarding override. Each station
must resolve exactly one instance of each of the three DHM rules, with its own ceiling
applicable. Reject a nonempty `Resolution.rejected`, `not_applicable`, or `fan_out` before any
QC write; fan-out means one ceiling could apply to multiple rule versions. Reject a non-empty
`SAPPHIRE_CONFIG_OVERLAY` for this command unless it names the checked-in
`config/overlays/chwrr-import.toml` and that file contains only `[deployment]` with
`writable_tenants = ["chwrr"]`. This permits CHWRR write authority without letting an
overlay alter QC rules, ceilings, or the inherited onboarding tenant. Resolve the
`chwrr`-scoped principal and enforce tenant isolation before QC updates; reject the Swiss
base identity and any unscoped/global-admin identity for this routine import.
Plan 264 D4 is closed: shared base configuration carries the
network rules and Plan 269 supplies per-station ceilings. Both interfaces are now on `main`;
use them directly while keeping the effective rules exactly at the D14 values above.
`resolve_station_qc_overrides` accepts tenant-scoped specs, station configurations, the rule
set, a callable `(station, rule_id, parameter) -> bool`, and `tenant_id=`. Its `Resolution`
separates `overrides`, `rejected`, `not_applicable`, and `fan_out`.

The six D14 station ceilings and shared DHM daily `rate_of_change` / `spike` thresholds are
fixed first-pass inputs, not implementation guesses. Keep `gross_outlier` excluded using the D16
skipped-rule mechanism and report that
exclusion explicitly. Do not add a daily `frozen_sensor` rule.

Before QC, split observations at gaps in the local daily calendar so rate-of-change and spike
rules never compare values across a missing local date. Consecutive Kathmandu dates remain in
one segment even when their UTC timestamps are 23 h 45 min apart across the 1986 offset change;
T7 must not treat that timezone transition as a missing day. Use the current `infer_time_step` / `resolve_selection`
behavior from Plan 272: fewer than two distinct timestamps have no inferred cadence, and a
group with no runnable rules is persisted as `QC_UNCHECKED`. Report those groups and any short
segments by station, date range, and count. T7 is not complete while any delivered row is
unaccounted for or any row is described as checked without a rule execution.

Run the entire delivery QC pass in one caller-owned database transaction. Acquire the same
D11 tenant-row lock as T4 before fetching the delivery cohort, preflight rules and ceilings,
classify the cohort, and write every status through the delivery-scoped store update. Require
exactly one affected row for every intended observation ID; any missing row, changed delivery
ID, or exception rolls back all QC statuses and flags. Commit only after the persisted
delivery-tagged cohort and status totals match the intended IDs. A later replacement resets
its new rows to `RAW` and must rerun T7 under D6.

**Verification**:

- Exercise the actual config loader, resolver and checker path; assert each of the six stations
  receives its own D14 provisional effective `value_max`, including the correct daily `time_step` and
  DHM network. A direct `merge_thresholds` unit test alone is insufficient.
- Assert DHM daily rules resolve for DHM while Swiss daily rules and outcomes remain unchanged
  (`tests/unit/services/test_qc_swiss_equivalence.py`). Check the `range_check`,
  `rate_of_change`, and `spike` DHM replacements individually; `gross_outlier` is the only
  shared generic rule and is skipped for this import.
- Test contiguous segments and gaps using local calendar dates: no rate-of-change or spike
  comparison crosses a missing local date, while consecutive local dates across the 1986 offset
  transition are not split. If a short transition-spanning segment cannot resolve cadence, it
  remains `QC_UNCHECKED` and appears in the run report.
- Verify `load_qc_rules(config_path=...)` for both files returns the same shared version,
  `1.1.0` (or the correctly incremented current version if an approved intervening change
  exists), and the same DHM rules by network, rule ID, parameter, cadence, threshold, and
  per-rule version. The rule-set version is not stored on observation rows. Verify each flag uses
  the effective configured version of its producing rule, and for discharge verify the row-level
  `qc_rule_version` is the Plan 324 generation marker `"1.2"`; it is distinct from both the
  per-rule flag versions and `[qc_rules].version`.
- Verify persisted row statuses on synthetic integration data: totals account for every
  imported row and no unchecked row is counted as passed. T8 repeats the database check on
  the real delivery. The run report names each resolved/skipped rule,
  rows evaluated, flags raised, excluded short segments, and explains that D14 `rate_of_change`
  is non-discriminating at this setting: it cannot flag a pair both within the configured range.
  Do not publish delivered values or restricted rating-table data.
- Inject a failure after some QC updates and assert no status or flag from that pass survives.
  In a PostgreSQL integration test, overlap T7 with T4 replacement and assert their tenant
  lock serializes the operations; an update that affects zero rows must fail and roll back.
- Seed an unrelated observation on one of the six stations with a non-delivery ID and an
  existing QC status. Assert T7 neither changes its status nor includes it in checker groups;
  rate and spike comparisons use only rows from this delivery.
- Add focused tests for the import CLI configuration and QC path, including a synthetic RED test showing that network selection applies the DHM range ceiling
  only to DHM stations while preserving the current Swiss result.
- Add refusal tests for an unset explicit config, a config missing the DHM rows, a missing
  station ceiling, a rejected ceiling, a not-applicable ceiling, nonempty `fan_out`, a Swiss-only
  or unscoped principal, and any overlay other than the deployment-only CHWRR import overlay;
  each asserts no QC status write occurs. The permitted overlay must leave effective QC rules
  and threshold declarations identical to the base file.

**Depends on T4.** Plan 264's selector and Plan 269's resolver are merged prerequisites,
not remaining work in this plan.

### T8 — Restricted-delivery staging acceptance (operator run after merge)

**Outcome**: the six `chwrr` stations, 112 curves and 99,246 daily observations are present
in the existing Mac-mini staging database, and the delivery cohort has persisted QC statuses.
This is the real-data acceptance for T2b/T3/T4/T5/T7, not a PR or CI gate. The implementation
PR uses synthetic fixtures and the full test suite; no delivered file enters the repository.

**Target and inputs**: a checkout of the merged code on the Mac mini, the staging database
selected by its secret-backed `DATABASE_URL`, `SAPPHIRE_CONFIG` set to the deployed base
`config.toml`, and `DHM_DELIVERY_DIR` set to the restricted delivery directory mounted
outside the checkout. The CLI exposes
`bootstrap-tenant`, `stations`, `replace`, and `qc` subcommands with `--tenant chwrr`;
`replace` takes `--input-dir`, and every writing subcommand has `--dry-run`. Commands report
aggregates and IDs only, never stage/discharge values, rating points, or DB credentials.

**Operator sequence**:

1. Confirm the target is the shared Mac-mini staging database, the required migration is at
   its single head, and no other tenant owns any of the six `(dhm, code)` identities. If
   `chwrr` is absent, run
   `uv run python -m sapphire_flow.cli.import_dhm_delivery bootstrap-tenant --tenant chwrr --dry-run`
   and then without `--dry-run`, with a temporary, out-of-repo
   deployment overlay containing `[deployment] global_admin = true` and
   `writable_tenants = []`; verify the admin identity before creating the tenant, then remove
   that overlay. Do not use this identity for station or data writes.
2. Set `SAPPHIRE_CONFIG_OVERLAY` to the checked-in CHWRR deployment-only overlay. Run
   `uv run python -m sapphire_flow.cli.import_dhm_delivery stations --tenant chwrr --dry-run`,
   then the same command without `--dry-run`. Read back the six station rows, their tenant,
   onboarding status, network and unset forecast targets.
3. Run `uv run python scripts/dhm_delivery/remeasure.py --input-dir "$DHM_DELIVERY_DIR" --check`.
   Compare the aggregate report with this plan. Run
   `uv run python -m sapphire_flow.cli.import_dhm_delivery replace --tenant chwrr --input-dir "$DHM_DELIVERY_DIR" --dry-run`;
   this executes the full T4 transaction and rolls it back, including conversion and
   store/read-back checks for all 112 curves. Then run `replace` without `--dry-run`. Confirm
   exact per-station counts, gap dates, curve coverage, rating-type labels and delivery tags
   against the fresh re-measure, including the single out-of-range value and 4,629 curve-less
   observations. Check all imported observations are `RAW` before QC.
4. With the deployment overlay temporarily unset, run
   `uv run python scripts/onboard.py --validate-config config.toml` against
   the base config and staging database; its six onboarding declarations may read PENDING but
   none may be REJECTED or FAN_OUT. Restore the CHWRR overlay and run
   `uv run python -m sapphire_flow.cli.import_dhm_delivery qc --tenant chwrr --dry-run`,
   then without `--dry-run`. Read back the delivery-tagged cohort: every intended row has its
   persisted QC status/version, status totals equal the imported count, no unrelated row
   changed, and the aggregate run report names skipped/unclassifiable rules and segments.

Any failed comparison stops the sequence. The operator keeps the aggregate report and exact
software/config versions with the staging handover, without attaching restricted measurements.
No task or plan is COMPLETE merely because the synthetic PR tests pass.
**Depends on T0–T7 landing and staging deployment.** Production activation is out of scope.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T0"] },
    { "id": "phase-2", "tasks": ["T1", "T2a"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T2b"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T3"], "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T5"], "depends_on": ["phase-4"] },
    { "id": "phase-6", "tasks": ["T4"], "depends_on": ["phase-5"] },
    { "id": "phase-7", "tasks": ["T7"], "depends_on": ["phase-6"] },
    { "id": "phase-8", "tasks": ["T6"], "depends_on": ["phase-7"] },
    { "id": "phase-9", "tasks": ["T8"], "depends_on": ["phase-8"] }
  ]
}
```

T0 installs the publication guard before implementation files appear. T1 and T2a are independent;
T2b creates the stations required by T3. T5 runs after T3 so curve-bound counts use the shared
boundary helper. T6 closes the code/documentation PR; T8 is the post-merge Mac-mini operator
acceptance. T7 uses the
provisional D14 thresholds and the interfaces merged under Plans 264 and 269. The
Swiss-equivalence test is `tests/unit/services/test_qc_swiss_equivalence.py`.
The phase order is also the **re-import order** (D6):
curves before observations before QC, and the replacement runs in reverse.

## Explicitly out of scope

- Changes to the existing DHM observation adapter or its direct-DHM connectivity and activation
  (`adapters/dhm.py`; implemented under Plan 300). This plan only imports the historical delivery.
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
