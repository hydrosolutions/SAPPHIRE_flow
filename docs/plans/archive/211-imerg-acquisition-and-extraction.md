---
status: READY
created: 2026-08-28
plan: 211
title: M-A5b — IMERG Early acquisition and point extraction
scope: Acquire IMERG Early V07 half-hourly precipitation over the DHM station box, aggregate to hourly, and extract at the 26 station locations into an identity-addressed bundle with the same diagnostics M-A5 produced for ERA5-Land. NOT a comparison against the gauges, NOT a skill claim, NOT a correction, NOT IMERG Final.
depends_on: [174]
blocks: [M-A9 follow-on comparison]
source: docs/design/dhm-precipitation-milestones.md § M-A5b
---

# Plan 211 — M-A5b IMERG Early acquisition and extraction

## Status

**READY.** Owner confirmed 2026-08-28, after two independent review rounds. A confirming review
(2026-08-29) closed the last five items — the publish/discovery predicate now RESOLVES the
permanent acquisition record and recomputes the bundle's two digests from it, D1's read contract
is validated semantically, `sensitivity_params` and D6's datum/cell agreement are pinned again,
a malformed `station_accounting` value is skipped rather than raised, and recorded gaps are
canonicalised chronologically. See `docs/design/dhm-precipitation-milestones.md` § M-A5b.

## ⛔ PROPORTIONALITY IS BINDING

**This is an acquisition and extraction milestone — mostly mechanical.** No new framework, abstraction
layer, config surface, plugin seam or file format. The **discovery convention** already exists (P2/P6) and is reused
verbatim; the **manifest and publisher are ERA5-specific and are not** — see D9, and the t2m
precedent that hit the same wall. If a decision fits in a sentence it must not become a module. **Adding length is a cost.**

## ⛔ THE MILESTONE'S OWN TEXT IS SUPERSEDED ON TWO POINTS

1. **Final → Early.** M-A5b says the extraction uses **IMERG Final**; the owner decided on 2026-08-28
   that the target is **Early** (Plan 209 D7). Final has ~3.5-month latency and is the one run that can
   never serve an operational role; a characterisation built on it would answer a question we do not have.
2. **The elevation-mismatch table is dropped** (D6). The milestone still requires one
   (`docs/design/dhm-precipitation-milestones.md:347`), inherited from M-A5's ERA5-Land shape.

⛔ **Point 2 puts a task in scope, not just a note:** as written, this plan's Exit and the milestone's
Exit cannot both be satisfied. **T2 must update the milestone text** — otherwise the next reader meets
two authoritative documents demanding opposite deliverables. Everything else in M-A5b stands: mirror
M-A4→M-A5, half-hourly, mm/hr rate convention, aggregate to hourly, none of ERA5-Land's deaccumulation
logic transfers.

## What is already known, measured rather than assumed

Measured 2026-08-28 (Plan 209 T2), against the **GES DISC HTTPS archive**:

- **Retrievable.** `GPM_3IMERGHHE_07` at
  `https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGHHE.07/<yyyy>/<ddd>/`, authenticated by
  Earthdata Login via `~/.netrc`. HTTP 200; **48 granules/day**, as half-hourly implies.
- **Latency is 4 h 20 m – 4 h 50 m**, not the documented "~4 h", and granules publish in **hourly
  batches of two** — so the freshest observation is never fresher than ~4 h 20 m.

## Decisions

- **D1 — IMERG Early **V07B**, `GPM_3IMERGHHE_07`, field `/Grid/precipitation`.** ⛔ Pin the revision:
  "V07A/V07B as encountered" would let **mixed revisions** into one bundle. Current Early is **V07B**;
  if a granule carries another revision, stop and report rather than blending.
  **Freeze the whole read contract on first granule and assert it on every subsequent one** — HDF5
  path, dimension order, coordinate registration (cell-centre vs edge), units string, **fill value**,
  **exact grid shape, the latitude/longitude vectors, their axis direction, and the longitude
  convention** (−180…180 vs 0…360). Record all of it in the manifest. ⛔ Without the vectors and the
  longitude convention, two conforming granules — or a subset service versus the full archive — can map
  the same station to **different cells** while passing every other stated check. Verifying only the field name is not enough: a transposed
  grid or an unmasked fill sentinel would pass that check and corrupt every downstream number.

- **D2 — The extraction operator is NEAREST CELL CENTRE, and the reason is M-A5's, unchanged.**
  Precipitation is not a smooth field; bilinear is a convex combination of four cells and cannot
  preserve a cell-scale maximum, biasing exactly the tail this track cares about. Locked here **before
  any numbers are seen**, exactly as Plan 174 D1 locked it for ERA5-Land. Run the bilinear sensitivity **exactly as Plan 174 D1a
  specifies it** — its pinned population, seasons, wet-hour policy, quantiles and delta statistics, not
  a fresh choice of any of them — and reuse the existing implementation
  (`era5_extract.py:518`). ⛔ Without that reference, multiple incompatible envelopes would satisfy this
  task and the promised M-A5-shaped diagnostic would not be locked. Report it as a **named
  operator-sensitivity envelope** — never an uncertainty band, never a decision gate.

- **D3 — Aggregation is the MEAN OF THE TWO HALF-HOURLY RATES, output in mm/h.** IMERG is a **rate**
  (mm/hr) over each half-hour, so an hourly rate is `(r₁ + r₂) / 2` and the hourly accumulation in mm is
  numerically the same number. **Output mm/h**, matching ERA5-Land's product so a later comparison
  needs no unit reconciliation. ⛔ **None of ERA5-Land's deaccumulation logic transfers** — there is no
  accumulator and no 01 UTC reset here, and reusing that code path would be a category error.

- **D4 — Absent hours are carried on a COMPLETE axis as NaN, never omitted, never synthesised.**
  The output has one row per hour of the window. An hour with **fewer than two granules** is `NaN`, and
  every hour carries its **granule count** (0, 1 or 2) as a companion column; a granule that exists but
  whose station cell is non-finite is likewise `NaN`, counted separately. The manifest records both
  totals. ⛔ Averaging a single granule into an hourly value invents coverage, and omitting the row
  hides that it was ever expected.

  ⚠️ **This collides with the ERA5-Land publisher, which requires a complete FINITE primary series**
  (`era5_extract_manifest.py:1278`). IMERG's series legitimately contains NaN, so it **cannot** publish
  through that path unchanged — see D9.

- **D5 — PERIOD-ENDING, with the axis endpoints and the boundary granules pinned.** `precipitation at
  t` = total over `t−1 → t`, matching ERA5-Land. The hour ending at `t` is built from the two granules
  whose `E` falls in `(t−1, t]`.
  ⛔ **The axis runs `2020-01-01 00:00` … `2025-12-31 23:00` inclusive, which means the FIRST hour needs
  the two granules of 2019-12-31 23:00–24:00.** Retrieval must therefore start one hour before the
  window. Without them the first hour is falsely NaN, or — worse — the whole series is shifted an hour
  against ERA5-Land and the gauges, which would look like a real phase difference. State the convention
  and both endpoints in the manifest; an unstated timestamp convention already cost this track a
  ±1.75 h uncertainty on Pyramid.

- **D6 — NO elevation-mismatch table. Record the grid cell and the station elevation, and stop.**
  ERA5-Land's table existed to quantify **model**-versus-station mismatch, which is what D14's lapse
  correction consumes. IMERG is a satellite retrieval with **no model surface**, and nothing in this
  track derives a lapse correction from it — so a "mismatch" here has **no consumer**.
  ⛔ **Adding a public DEM is out of scope**: Plan 174 D3a's `DEM_PROXY` branch was never evaluated
  (`era5_orography_spec.py:47`), so requiring one would mean choosing a product, datum, aggregation and
  no-data rule *and building the branch*, inside an acquisition milestone.
  ⇒ Record per station: the selected cell's **centre latitude/longitude** and the **station elevation
  with its vertical-datum enum** (`UNKNOWN`, Plan 174 D3b). ⛔ **Not** grid indices — they would need a
  global-versus-subset-local convention that no downstream consumer has asked for.


- **D7 — This acquisition is RETROSPECTIVE, and the manifest records that word.** Retrospectively
  downloaded Early can contain inputs unavailable in live operation, so any later skill number derived
  from this bundle may **overstate live Early performance** (Plan 209 D7). ⛔ A future evaluation must
  not quote a figure from this bundle as if it were live-capture.

- **D8 — Determinism.** Round every aggregation to **9 decimal places** before comparison or
  rendering, matching `ma7_profiles._HOURLY_MEAN_ROUNDING_DECIMALS`. ⛔ A two-run diff alone is not
  sufficient evidence — it passed twice on genuinely broken code here; the mechanism must be argued.

- **D9 — Same discovery CONVENTION; IMERG gets its own root, reader and manifest type.**
  The ERA5-Land publisher cannot be reused: `era5_extract_manifest.points_root()` hardcodes
  `data_root / "era5_land" / "points"` (`:157`), the manifest schema requires ERA5 **accumulation and
  orography** records (`:515`), and the primary series must be complete and finite (`:1278`) — which
  D4's NaN hours violate by design. **t2m already hit this and needed its own reader**
  (`extract_era5_t2m.py:430`); IMERG follows that precedent.
  ⇒ Reuse the **convention**, not the module: identity-addressed bundle under an **IMERG data root**,
  its own manifest type carrying IMERG's provenance (no fabricated ERA5 accumulation records), and
  discovery by **the highest `NNNN` whose manifest validates** (P2/P6). ⛔ An identity is a **label, not
  a lookup key** — never glob `*-<identity>`, never a run-numbered path. ⛔ And do not invent a second
  *discovery rule*; that is the thing this decision actually forbids.

  **Because the module is new, five things it would otherwise have inherited must be stated:**
  - **Output schema**, one row per station-hour: `station_id`, `timestamp_utc` (period-ending, D5),
    `precip_mm_per_h` (nullable), `granule_count` (0/1/2), plus the per-station cell centre lat/lon and
    elevation of D6. Nullability is part of the schema, not an accident of the data.
  - **T1→T2 handoff is an acquisition manifest**, written by T1 and *read* by T2: route used, collection
    short name, granule revision, window, box, per-granule checksums, and the observed read contract.
    ⛔ T2 must not re-derive any of it by re-listing the directory — that is how the two tasks drift.
  - **Identity is hashed over exactly what was READ** (P7a): the acquisition manifest's granule
    checksums, the station table, the window, the box, the named operator, and the frozen read
    contract. ⛔ **Not** wall-clock time, output paths, or hostname — those go in the manifest as
    provenance and stay out of the hash.
  - **Allocator: the next free `NNNN` above the highest existing**, whether or not that bundle
    validates. ⛔ Discovery skips an invalid bundle; allocation must not *reuse* its number, or a broken
    bundle and its replacement collide.
  - **Publication and discovery must call ONE validation predicate**, not two implementations of the
    same rules. ⛔ Otherwise a bundle passes publication and is then invisible to discovery — the
    failure is silent, and it looks like data loss rather than a validator disagreement.

- **D10 — The RAW GRANULES ARE DISPOSABLE; the acquisition manifest is PERMANENT.** Raised by the owner
  at READY: this archive may have to be discarded sooner than originally intended. That is safe, but
  only in one direction. The extracted bundle is **26 stations × hourly ≈ 1.4 M rows** — tens of MB —
  while the raw granules are **105,216 global files**, projected in T1 at hundreds of GB. ⛔ Do not
  restate that projection as a fact: T1 measures one granule and multiplies, and the ratio is whatever
  that measurement says. The raw data is the only part whose disk cost is ever in question.
  ⇒ **What must survive discard is the acquisition manifest** (D9): route, collection, revision, window,
  box, the observed read contract, and **every per-granule checksum**. It is kilobytes. ⛔ Discarding
  raw granules *without* it makes the bundle unfalsifiable — nothing could ever confirm which bytes
  produced it.
  ⚠️ **After discard, bit-for-bit regeneration depends on GES DISC not having revised the granules**
  (V07A→V07B is precedent). The retained checksums do not prevent that; they make it **detectable** on
  re-download, which is the honest guarantee and the one worth keeping. State it in the manifest rather
  than implying reproducibility the archive cannot promise.
  ⇒ **This raises the value of server-side subsetting (T1)**: a subset over the frozen box is ~4,500
  cells per granule, which would make retention a non-question rather than a decision.

## ⛔ PER-RUN SCOPE FOR THIS `/implement` (binding)

**Build the pipeline; stop at T1's own projection gate. Do NOT perform the bulk retrieval.**

- ✅ **In scope:** `imerg_acquire.py`, `imerg_extract.py`, their **no-network** unit tests under
  `tests/unit/scripts/`, the D9 publisher + shared validation predicate, and T2's milestone-text update.
- ✅ **Exactly ONE granule may be downloaded** — the D1 contract probe. It is load-bearing: without a
  real granule the read contract is *assumed*, which is the exact failure D1 exists to prevent. Record
  its observed structure and its size. ⛔ **One. Not a day, not a month.**
- ⛔ **STOP after reporting the projection** (granule size × 105,216 against free disk). The bulk
  retrieval is the owner's call — 896 GB free as of 2026-08-28, so a full-globe pull would consume most
  of the disk. Report and halt; do not retrieve, do not "start with a small subset to be safe".
- ⛔ **Tests must never touch the network.** Build fixtures as small synthetic files carrying the
  structure observed on the probe granule — never by downloading inside a test.
- ⛔ Nothing is written under `data/dhm_precip/era5_land*`, and nothing under any `points/` tree is
  deleted. The ERA5 archive is untouched by this work.
- **Iterate against `tests/unit/scripts/` (~75 s)**, not the full unit suite (~8 min) — running the full
  suite as an iteration loop has stalled eight subagents on this track. Run the full suite **once** at
  the end.
- **Hold at PR.** Every code commit bumps patch (`uv run bump-my-version bump patch`) folded into the
  real commit. Never merge, never tag on a branch.

## Tasks

Two tasks, two phases. `$ENV` abbreviates the Earthdata-authenticated environment.

### T1 — acquisition (depends: nothing new)
**In:** retrieve IMERG Early **V07B** half-hourly granules for the DHM window (2020-01-01 →
2025-12-31) into an **IMERG data root** (⛔ never under `data/dhm_precip/era5_land*`), recording
collection short name, granule revision, retrieval timestamps and per-granule checksums.

**PINNED — the retrieval shape, because the two options differ by orders of magnitude.**
- **Reuse the frozen bounding box** `[31, 80, 26, 89]` (`era5_request.py:104`) — the same box
  ERA5-Land used, so the two products cover identical ground. ⛔ Do not restate the numbers from
  memory; import the constant.
- **The route is the GES DISC HTTPS archive** — the only route measured to work (Plan 209 T2), and the
  one D1's HDF5 read contract describes. ⛔ **Do not swap in a subset service without re-freezing the
  contract**: a subsetter may return NetCDF4 rather than HDF5, renamed or reordered dimensions, its own
  fill value, and a subset-local grid — which changes the format, the per-granule checksums, and
  therefore the bundle identity. If a subset route is used it must satisfy D1 **as observed, recorded
  in the manifest as the route actually used**.
- **Decide full-granule versus subsetting by MEASUREMENT, in this order:** retrieve one granule, verify
  D1's read contract on it, record its size, and multiply by **105,216** (the window's granule count).
  Report that projection **before** retrieving the rest. If it does not fit the available disk, stop and
  report — ⛔ never start downloading the globe and discover the volume at 60 %.

**Out:** any aggregation, any extraction, any comparison.
**Verify:** `uv run pytest tests/unit/scripts/test_imerg_acquire.py -q` — granule-name construction,
window/box arithmetic, and missing-granule accounting, **with no network**. Then one gated retrieval
reporting granules requested / retrieved / missing, and the frozen read contract as observed.
⚠️ Retrieve once; never re-download what is on disk.

### T2 — hourly aggregation, point extraction, and the extraction record (depends: T1)
**In:** aggregate half-hourly rates to hourly per D3/D4/D5, extract at the 26 station locations with
the **nearest** operator (D2), run the bilinear sensitivity, and publish an identity-addressed bundle
(D9) carrying the series, the operator-sensitivity envelope, and the per-station cell/elevation record
of D6. Then **update `docs/design/dhm-precipitation-milestones.md` § M-A5b** for both superseded points
(Early not Final; no elevation-mismatch table), so the milestone and this plan no longer demand
opposite deliverables.
**Out:** any gauge comparison, any skill statistic, any correction.
**Verify:** `uv run pytest tests/unit/scripts/test_imerg_extract.py -q`, including **a test that an
hour with one granule is `NaN` with `granule_count == 1`, never synthesised** (D4) and **a test
asserting the period-ending mapping, including the first hour of the axis** (D5). Then, gated:

```
$ENV uv run python scripts/dhm_precip/imerg_extract.py --out <dir_a>
$ENV uv run python scripts/dhm_precip/imerg_extract.py --out <dir_b>
```

Both outputs **proven non-empty before comparison**, then compared **excluding the manifest's
`generated_at` and `retrieved_at` fields** — every other byte identical (D8). ⛔ "Byte-identical"
without naming the time-bearing exclusions is not a pass/fail criterion.

⇒ **The bundle manifest IS the extraction record** — collection, version, route, window, box, granule
counts and gaps, the named operator, the sensitivity envelope, the cell/elevation record, the word
**`RETROSPECTIVE`** (D7) and the measured acquisition latency. ⛔ No separate report artefact and no
`--describe` renderer: every figure already lives in the manifest, and a second surface rendering the
same numbers is a second thing to keep in sync. Every figure must trace to the manifest or to a named
command; a figure that cannot be traced is a defect.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Exit

Extracted IMERG **Early** series at the 26 stations, hourly, mm/h, period-ending; the named nearest
operator recorded in the manifest and never implied by code; the per-station **cell centre lat/lon**
and station elevation with its vertical-datum enum (D6 — ⛔ no DEM mismatch table, and the milestone
text updated to match); the operator-sensitivity envelope against bilinear; and the manifest marked
**RETROSPECTIVE** — all regenerable from the committed pipeline into an identity-addressed bundle,
with the acquisition manifest retained independently of the raw granules (D10). **The same shape as M-A5's exit, for IMERG Early.**

## Non-goals

Any comparison against the DHM gauges (a later milestone, the IMERG analogue of M-A6) · any skill,
verification or fitness claim · any correction, adjustment or downscaling · IMERG Final or Late ·
operational or live-capture ingest · a second bundle/manifest/discovery mechanism (D9).
