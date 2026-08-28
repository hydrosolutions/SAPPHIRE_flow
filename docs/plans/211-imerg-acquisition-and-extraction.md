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
layer, config surface, plugin seam or file format. The bundle, manifest and discovery machinery
**already exist** (`era5_extract_manifest.py`, `extract_era5_t2m.py`, P2/P6 discovery); this reuses
them. If a decision fits in a sentence it must not become a module. **Adding length is a cost.**

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

- **D1 — IMERG Early V07, `GPM_3IMERGHHE_07`, field `precipitation`.** Record the exact collection
  short name and the granule version string (`V07A`/`V07B` as encountered) in the manifest. ⛔ **Verify
  the field name against a downloaded granule before building on it** — V06 called it
  `precipitationCal` and V07 renamed it; a plan-stated field name is not a verified one. *(A
  plausible-looking collection identifier was invented once already during this track's planning and
  caught only by review.)*

- **D2 — The extraction operator is NEAREST CELL CENTRE, and the reason is M-A5's, unchanged.**
  Precipitation is not a smooth field; bilinear is a convex combination of four cells and cannot
  preserve a cell-scale maximum, biasing exactly the tail this track cares about. Locked here **before
  any numbers are seen**, exactly as Plan 174 D1 locked it for ERA5-Land. Run **one bilinear
  sensitivity** and report it as a **named operator-sensitivity envelope** — ⛔ never an uncertainty
  band, never a decision gate (Plan 174 D1a).

- **D3 — Aggregation is the MEAN OF THE TWO HALF-HOURLY RATES, output in mm/h.** IMERG is a **rate**
  (mm/hr) over each half-hour, so an hourly rate is `(r₁ + r₂) / 2` and the hourly accumulation in mm is
  numerically the same number. **Output mm/h**, matching ERA5-Land's product so a later comparison
  needs no unit reconciliation. ⛔ **None of ERA5-Land's deaccumulation logic transfers** — there is no
  accumulator and no 01 UTC reset here, and reusing that code path would be a category error.

- **D4 — An hour with fewer than two granules is NOT synthesised.** Record it as absent with its
  granule count. ⛔ Averaging a single granule into an hourly value invents coverage, and this track has
  already established that a value whose conditions are hidden is worse than a gap.

- **D5 — The hourly timestamp convention is PERIOD-ENDING, stated explicitly**, matching ERA5-Land's
  extracted series (`precipitation at t` = total over `t−1 → t`). IMERG granules carry `S`/`E` labels;
  the hour ending at `t` is built from the two granules whose `E` falls in `(t−1, t]`. ⛔ State the
  convention in the manifest — an unstated timestamp convention cost this track a ±1.75 h uncertainty
  on Pyramid.

- **D6 — There is NO model orography, and the elevation table says so.** ERA5-Land has its own
  orography; IMERG is a satellite retrieval on a 0.1° grid with no model surface. ⇒ `orography_source`
  is **`DEM_PROXY`**, never `MODEL_OROGRAPHY`, and the station-to-grid "elevation mismatch" is a
  **grid-cell-versus-station** comparison against a public DEM, not a model-versus-station one. Carry
  both vertical-datum enums, station side **`UNKNOWN`** (Plan 174 D3b). ⛔ Do not present this table as
  the equivalent of ERA5-Land's — the quantity differs, and M-A6's D7 caveat about interpolated
  precipitation not seeing orography does not even apply here, because there is no orography to see.

- **D7 — All 26 stations are extracted; the SCOPING is on conclusions, not coverage.** M-A9 recommends
  IMERG be pursued at low and mid elevation, where its published skill is good and where Group B's 20
  stations can validate it. ⛔ That is not a reason to acquire less: the high stations are exactly what
  will **demonstrate** the limitation, and subsetting would leave it asserted rather than shown.

- **D8 — This acquisition is RETROSPECTIVE, and the manifest records that word.** Retrospectively
  downloaded Early can contain inputs unavailable in live operation, so any later skill number derived
  from this bundle may **overstate live Early performance** (Plan 209 D7). ⛔ A future evaluation must
  not quote a figure from this bundle as if it were live-capture.

- **D9 — Determinism, as everywhere on this track.** Any aggregation rounds to a fixed precision before
  comparison or rendering (`ma6_estimands._BUCKET_TOTAL_ROUNDING_DECIMALS`,
  `ma7_profiles._HOURLY_MEAN_ROUNDING_DECIMALS` are the precedents). ⛔ **A two-run diff is not
  sufficient evidence** — it passed twice on genuinely broken code in M-A6 and M-A7.

- **D10 — Publish by the existing convention; build no second one.** Identity-addressed bundle under
  its own data root, manifest, discovery by **the highest `NNNN` whose manifest validates** (P2/P6).
  ⛔ An identity is a **label, not a lookup key** — never glob `*-<identity>`, never a run-numbered path.

## Tasks

Three tasks, three phases. `$ENV` abbreviates the Earthdata-authenticated environment.

### T1 — acquisition (depends: nothing new)
**In:** retrieve IMERG Early V07 half-hourly granules over the DHM station bounding box for the DHM
window (2020-01-01 → 2025-12-31), into a raw archive under its own data root; record collection short
name, granule version, retrieval timestamps and per-granule checksums. **Verify the `precipitation`
field name against a real granule (D1) before anything depends on it.**
**Out:** any aggregation, any extraction, any comparison.
**Verify:** `uv run pytest tests/unit/scripts/test_imerg_acquire.py -q` for the pure parts (granule-name
construction, window/box arithmetic, missing-granule accounting) **with no network**; then one gated
real retrieval reporting granules requested, retrieved, and missing.
⚠️ **Respect the archive.** Retrieve once; do not re-download what is already on disk. ⛔ Never write
into `data/dhm_precip/era5_land*` — IMERG gets its own root.

### T2 — hourly aggregation and point extraction (depends: T1)
**In:** aggregate half-hourly rates to hourly per D3/D4/D5, extract at the 26 station locations with
the **nearest** operator (D2), run the bilinear sensitivity, and publish an identity-addressed bundle
(D10) carrying the series, the operator-sensitivity envelope, and the elevation table per D6.
**Out:** any gauge comparison, any skill statistic, any correction.
**Verify:** `uv run pytest tests/unit/scripts/test_imerg_extract.py -q`, including **a test that an hour
with one granule is recorded absent rather than synthesised** (D4) and **a test asserting the
period-ending convention** (D5). Then the gated real extraction, twice, **both outputs proven non-empty
before comparison** and byte-identical (D9).

### T3 — the extraction record (depends: T2)
**In:** the manifest and a short record of what was acquired: collection, version, window, box, granule
counts and gaps, the named operator, the sensitivity envelope, the elevation table, and **the words
`RETROSPECTIVE` and the measured latency** (D8, and the T2 figures above).
**Out:** any interpretation of the numbers; any statement about IMERG's skill or fitness.
**Verify:** every figure in the record traces to the manifest or to a named command.

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
operator recorded in the manifest and never implied by code; the per-station elevation table with
`orography_source = DEM_PROXY` and both vertical-datum enums; the operator-sensitivity envelope against
bilinear; and the acquisition record marked **RETROSPECTIVE** — all regenerable from the committed
pipeline into an identity-addressed bundle. **The same shape as M-A5's exit, for IMERG Early.**

## Non-goals

Any comparison against the DHM gauges (a later milestone, the IMERG analogue of M-A6) · any skill,
verification or fitness claim · any correction, adjustment or downscaling · IMERG Final or Late ·
operational or live-capture ingest · a second bundle/manifest/discovery mechanism (D10).
