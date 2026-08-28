---
status: DRAFT
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

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING

**This is an acquisition and extraction milestone — mostly mechanical.** No new framework, abstraction
layer, config surface, plugin seam or file format. The **discovery convention** already exists (P2/P6) and is reused
verbatim; the **manifest and publisher are ERA5-specific and are not** — see D10, and the t2m
precedent that hit the same wall. If a decision fits in a sentence it must not become a module. **Adding length is a cost.**

## ⛔ THE MILESTONE'S OWN TEXT IS SUPERSEDED ON ONE POINT

M-A5b says the extraction uses **IMERG Final**. **The owner decided on 2026-08-28 that the target is
IMERG Early** (Plan 209 D7). Final has ~3.5-month latency and is the one run that can never serve an
operational role; a characterisation built on it would answer a question we do not have.

⇒ **This plan supersedes that sentence.** Everything else in M-A5b — mirror M-A4→M-A5, half-hourly,
mm/hr rate convention, aggregate to hourly, none of ERA5-Land's deaccumulation logic transfers, same
grid/elevation diagnostics — stands.

## What is already known, measured rather than assumed

Measured 2026-08-28 (Plan 209 T2), against the **GES DISC HTTPS archive**:

- **Retrievable.** `GPM_3IMERGHHE_07` at
  `https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGHHE.07/<yyyy>/<ddd>/`, authenticated by
  Earthdata Login via `~/.netrc`. HTTP 200; **48 granules/day**, as half-hourly implies.
- **Latency is 4 h 20 m – 4 h 50 m**, not the documented "~4 h", and granules publish in **hourly
  batches of two** — so the freshest observation is never fresher than ~4 h 20 m.

⛔ **That latency figure belongs to this plan's record, not to its scope.** M-A5b acquires
retrospectively; the operational consequence is M-A9's and a later milestone's.

## Decisions

- **D1 — IMERG Early **V07B**, `GPM_3IMERGHHE_07`, field `/Grid/precipitation`.** ⛔ Pin the revision:
  "V07A/V07B as encountered" would let **mixed revisions** into one bundle. Current Early is **V07B**;
  if a granule carries another revision, stop and report rather than blending.
  **Freeze the whole read contract on first granule and assert it on every subsequent one** — HDF5
  path, dimension order, coordinate registration (cell-centre vs edge), units string, and **fill
  value**. Record all of it in the manifest. Verifying only the field name is not enough: a transposed
  grid or an unmasked fill sentinel would pass that check and corrupt every downstream number.
  *(V06 called the field `precipitationCal`; V07 renamed it — a plan-stated name is not a verified one.)*

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

- **D5 — The hourly timestamp convention is PERIOD-ENDING, stated explicitly**, matching ERA5-Land's
  extracted series (`precipitation at t` = total over `t−1 → t`). IMERG granules carry `S`/`E` labels;
  the hour ending at `t` is built from the two granules whose `E` falls in `(t−1, t]`. ⛔ State the
  convention in the manifest — an unstated timestamp convention cost this track a ±1.75 h uncertainty
  on Pyramid.

- **D6 — NO elevation-mismatch table. Record the grid cell and the station elevation, and stop.**
  ERA5-Land's table existed to quantify **model**-versus-station mismatch, which is what D14's lapse
  correction consumes. IMERG is a satellite retrieval with **no model surface**, and nothing in this
  track derives a lapse correction from it — so a "mismatch" here has **no consumer**.
  ⛔ **Do not substitute a public DEM.** Plan 174 D3a's DEM route (`orography_source = DEM_PROXY`) was
  **never evaluated** — `era5_orography_spec.py:47` records that Branch A satisfied D3a on the first
  probe, so the candidate list's acceptance criteria were never applied to anything, and
  `OBSERVED_OROGRAPHY_SPEC` (`:155`) implements the model route only. Requiring a DEM would mean
  choosing a product, datum, cell-aggregation and no-data rule **and building the branch**, inside an
  acquisition milestone.
  ⇒ Record per station: the **IMERG grid cell** (indices and centre lat/lon) and the **station
  elevation with its vertical-datum enum (`UNKNOWN`, Plan 174 D3b)**. That is what a later comparison
  actually needs.


- **D7 — This acquisition is RETROSPECTIVE, and the manifest records that word.** Retrospectively
  downloaded Early can contain inputs unavailable in live operation, so any later skill number derived
  from this bundle may **overstate live Early performance** (Plan 209 D7). ⛔ A future evaluation must
  not quote a figure from this bundle as if it were live-capture.

- **D8 — Determinism.** Round every aggregation to **9 decimal places** before comparison or
  rendering, matching `ma7_profiles._HOURLY_MEAN_ROUNDING_DECIMALS`. ⛔ A two-run diff alone is not
  sufficient evidence — it passed twice on genuinely broken code here; the mechanism must be argued.

- **D9 — Same discovery CONVENTION; IMERG gets its own root, reader and manifest type.**
  ⛔ **An earlier revision claimed the ERA5-Land publisher could simply be reused. It cannot.**
  `era5_extract_manifest.points_root()` hardcodes `data_root / "era5_land" / "points"` (`:157`), the
  manifest schema requires ERA5 **accumulation and orography** records (`:515`), and the primary series
  must be complete and finite (`:1278`) — which D4's NaN hours violate by design. **t2m already hit
  this and needed its own reader** (`extract_era5_t2m.py:430`); IMERG follows that precedent.
  ⇒ Reuse the **convention**, not the module: identity-addressed bundle under an **IMERG data root**,
  its own manifest type carrying IMERG's provenance (no fabricated ERA5 accumulation records), and
  discovery by **the highest `NNNN` whose manifest validates** (P2/P6). ⛔ An identity is a **label, not
  a lookup key** — never glob `*-<identity>`, never a run-numbered path. ⛔ And do not invent a second
  *discovery rule*; that is the thing this decision actually forbids.

## Tasks

Three tasks, three phases. `$ENV` abbreviates the Earthdata-authenticated environment.

### T1 — acquisition (depends: nothing new)
**In:** retrieve IMERG Early **V07B** half-hourly granules for the DHM window (2020-01-01 →
2025-12-31) into an **IMERG data root** (⛔ never under `data/dhm_precip/era5_land*`), recording
collection short name, granule revision, retrieval timestamps and per-granule checksums.

**PINNED — the retrieval shape, because the two options differ by orders of magnitude.**
- **Reuse the frozen bounding box** `[31, 80, 26, 89]` (`era5_request.py:104`) — the same box
  ERA5-Land used, so the two products cover identical ground. ⛔ Do not restate the numbers from
  memory; import the constant.
- **Prefer server-side spatial subsetting.** The full window is **105,216 global granules**;
  subsetting is the difference between a manageable archive and one that dominates the disk. **If the
  endpoint cannot subset, say so and stop** rather than silently downloading the globe — the choice
  changes raw volume, what the checksums mean, and what the identity is computed over.
- **Verify the read contract on the first granule (D1)** before retrieving the rest.

**Out:** any aggregation, any extraction, any comparison.
**Verify:** `uv run pytest tests/unit/scripts/test_imerg_acquire.py -q` — granule-name construction,
window/box arithmetic, and missing-granule accounting, **with no network**. Then one gated retrieval
reporting granules requested / retrieved / missing, and the frozen read contract as observed.
⚠️ Retrieve once; never re-download what is on disk.

### T2 — hourly aggregation and point extraction (depends: T1)
**In:** aggregate half-hourly rates to hourly per D3/D4/D5, extract at the 26 station locations with
the **nearest** operator (D2), run the bilinear sensitivity, and publish an identity-addressed bundle
(D10) carrying the series, the operator-sensitivity envelope, and the elevation table per D6.
**Out:** any gauge comparison, any skill statistic, any correction.
**Verify:** `uv run pytest tests/unit/scripts/test_imerg_extract.py -q`, including **a test that an
hour with one granule is `NaN` with `granule_count == 1`, never synthesised** (D4) and **a test
asserting the period-ending mapping** (D5). Then, gated:

```
$ENV uv run python scripts/dhm_precip/imerg_extract.py --out <dir_a>
$ENV uv run python scripts/dhm_precip/imerg_extract.py --out <dir_b>
```

Both outputs **proven non-empty before comparison**, then compared **excluding the manifest's
`generated_at` and `retrieved_at` fields** — every other byte identical (D8). ⛔ "Byte-identical"
without naming the time-bearing exclusions is not a pass/fail criterion.

### T3 — the extraction record (depends: T2)
**In:** the manifest and a short record of what was acquired: collection, version, window, box, granule
counts and gaps, the named operator, the sensitivity envelope, the elevation table, and **the words
`RETROSPECTIVE` and the measured latency** (D8, and the T2 figures above).
**Out:** any interpretation of the numbers; any statement about IMERG's skill or fitness.
**Verify:** `uv run python scripts/dhm_precip/imerg_extract.py --describe <bundle>` prints the record;
every figure in it traces to the manifest or to a named command, and a reviewer can regenerate any of
them. ⛔ A figure that cannot be traced is a defect.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Exit

Extracted IMERG **Early** series at the 26 stations, hourly, mm/h, period-ending; the named nearest
operator recorded in the manifest and never implied by code; the per-station **IMERG grid cell** (indices, centre lat/lon) and station elevation
with its vertical-datum enum (D6 — ⛔ no DEM mismatch table); the operator-sensitivity envelope against
bilinear; and the acquisition record marked **RETROSPECTIVE** — all regenerable from the committed
pipeline into an identity-addressed bundle. **The same shape as M-A5's exit, for IMERG Early.**

## Non-goals

Any comparison against the DHM gauges (a later milestone, the IMERG analogue of M-A6) · any skill,
verification or fitness claim · any correction, adjustment or downscaling · IMERG Final or Late ·
operational or live-capture ingest · a second bundle/manifest/discovery mechanism (D10).
