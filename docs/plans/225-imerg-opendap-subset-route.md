---
status: DRAFT
created: 2026-08-31
plan: 225
title: M-A5d — retrieve IMERG through the OPeNDAP subset route, 847 GB → 2.7 GB
scope: Add a second, separately-frozen read contract for GES DISC's OPeNDAP subset response and retrieve the full 2020-2025 IMERG Early window through it. NOT a replacement for the archive route, NOT a gauge comparison, NOT any skill claim.
depends_on: [211, 224]
blocks: [any IMERG comparison]
source: measured subset probe 2026-08-31; docs/plans/224-imerg-prerequisites-and-projection.md
---

# Plan 225 — M-A5d the IMERG OPeNDAP subset route

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING

**One new read contract and one new route through the existing pipeline.** Acquisition, extraction,
validation, publication, the gap reconciliation and the writer lock all exist and are reviewed.
⛔ No new framework, abstraction layer or file format beyond the contract the subset response requires.
**Adding length is a cost.**

## Why this plan exists — the measurement that changed the decision

Plan 224 measured the archive route: **8,047,136 B per granule**, full global field, so the 2020–2025
window is **846.7 GB against 939.6 GB free** — a 9.9 % margin that made the window a real decision.

**Probed 2026-08-31: GES DISC serves OPeNDAP for the same granule, and a server-side subset over the
frozen box is 25,524 B — 315× smaller.**

| | archive route | **OPeNDAP subset** |
|---|---|---|
| one granule | 8,047,136 B | **25,524 B** |
| one JJAS season (5,856) | 47.1 GB | **0.15 GB** |
| six JJAS seasons (35,136) | 282.7 GB | **0.90 GB** |
| **full window (105,216)** | **846.7 GB** | **2.69 GB** |

⇒ **The window decision disappears.** At 2.7 GB the whole archive is cheaper than one archive-route
season. ⛔ There is no longer a reason to retrieve a subset of the window.

**Verified on the probe response** (not assumed): exactly **90 × 50 = 4,500 cells** at 0.1° spacing,
cell edges **80.00–89.00 E / 26.00–31.00 N** — the frozen box exactly — all 4,500 finite, values
0–14.57 mm/hr.

## ⛔ The hazard this plan exists to handle

**The subset response is a DIFFERENT FILE ON A DIFFERENT GRID.** Plan 211's D1 contract pins the exact
1800/3600 coordinate vectors, and `exact_coordinate_vector` (`imerg_acquire.py:392`) warns in as many
words that rounding would let *"a subset service's own grid"* pass while mapping stations to different
cells. ⇒ **That warning is now load-bearing**: the subset route is the exact case it anticipated.

⛔ **Do NOT relax, parameterise or share the existing contract.** A second contract, frozen just as
strictly, is the whole point.

## Decisions

- **D1 — A SECOND read contract, frozen as strictly as the first.** `ImergReadContract`
  (`imerg_acquire.py:277`) and `contract_from_open_granule` (`:336`) stay untouched and keep serving the
  archive route. ⇒ Add a parallel subset contract pinning: the **90 lon and 50 lat values exactly**
  (no rounding, per `exact_coordinate_vector`'s own rule), the netCDF4 variable layout, dimension order,
  units string, fill value, and the longitude convention. ⛔ Two contracts, neither weakened.
  ⚠️ **"Variable layout" includes the dtype and any `scale_factor`/`add_offset`** — they decide what a
  decoded value *is*, and D5's tolerance is derived from them.

- **D2 — The route is recorded in the acquisition manifest and is part of the identity.** A bundle built
  from subset granules must be distinguishable from one built from full-field granules, because the two
  read different grids. ⇒ The route string enters the record and therefore the digest. ⛔ A bundle whose
  route disagrees with its contract must be refused.
  **Three concrete requirements, because the prose alone does not provide them:**
  1. **Contract validation must DISPATCH on the recorded route.** Provenance currently accepts only the
     archive `ROUTE` and always reconstructs an `ImergReadContract` (`imerg_acquire.py:708-766`).
     ⇒ Route → contract association, with tests for **both** mismatched combinations.
  2. 🔴 **Subset artifacts need a route-distinct filename or raw directory.** Raw storage is keyed only
     by the original granule filename and acquisition **reuses an existing path before downloading**
     (`imerg_acquire.py:483-492`, `:983-990`). ⇒ T2 holds the archive **and** subset form of the *same*
     granule at once, so manifest identity cannot prevent a **filesystem collision**.
  3. 🔴 **A route-aware granule READER is missing.** `imerg_extract.py:280-311` unconditionally parses an
     archive filename, invokes the `/Grid` contract parser and reads `/Grid/precipitation` — **it cannot
     consume the flattened response**. ⇒ Normalise the subset response into the existing
     `(valid_time, latitude, longitude)` dataset. ⛔ Route dispatch, not a reader abstraction.

- **D3 — Retrieve the FULL window: 2020-01-01 → 2025-12-31.** At 2.7 GB there is no reason to sub-select,
  and a partial archive would only invite a later top-up with all the identity questions that brings.
  ⚠️ **The real cost is requests, not bytes** — and ⛔ "politely" is not testable. **Four concrete
  behaviours, no scheduler and no new config surface:**
  1. A **fixed cadence between successful requests** (the loop currently has no delay).
  2. **Retry 429 honouring `Retry-After`, with backoff** — 429 is currently **not** retryable
     (`imerg_acquire.py:925-938`).
  3. **One authenticated, cookie-bearing session, reused.** The client makes independent `requests.get`
     calls, so ⛔ **D4's cookie-jar requirement is NOT yet implemented** (`:940-955`).
  4. **Resolve each day's filenames ONCE**, not per granule — filename resolution currently issues a
     directory listing before each local-file check (`:1076-1095`), so 105,216 counts *data* requests,
     not total HTTP traffic.
  ✅ **Resumability already exists** — validate-and-reuse an existing artifact rather than re-downloading
  (`:985-990`). ⇒ Preserve that behaviour for the subset filename; do not build a checkpoint store.
  Record gaps as gaps (Plan 220's rule: a gap is data, a wrong retrieval is not).

- **D4 — OPeNDAP access facts, measured 2026-08-31, not assumed.**
  - Endpoint: `https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGHHE.07/<yyyy>/<ddd>/<granule>.HDF5`
  - 🔴 **Earthdata auth needs `--location-trusted` and a cookie jar** — without them the redirect loops
    until curl gives up (measured: 50 redirects, HTTP 302, 0 bytes).
  - 🔴 **OPeNDAP FLATTENS the HDF5 `/Grid` group.** The constraint is `/precipitation`, **not**
    `/Grid/precipitation` — the latter returns HTTP 400 *"referenced a variable that was not found"*.
  - Constraint used: `dap4.ce=/precipitation[0][2600:2689][1160:1209];/lon[2600:2689];/lat[1160:1209]`
  - `.dap.nc4` returns netCDF4 that xarray opens directly (25,524 B; the raw `.dap` is 24,153 B).

- **D5 — The archive route stays, and stays the reference.** ⛔ Do not delete it. One granule already on
  disk was retrieved through it and validated against the original contract. ⇒ **Cross-check the two on
  that granule**, in this order:
  1. 🔴 **The lon and lat VECTORS first**, compared exactly and in order against the corresponding archive
     slices. ⛔ "All 4,500 values match" is only *indirect* evidence of alignment — the coordinate
     vectors are the actual station-mapping invariant D1 exists to protect.
  2. **Then the decoded PHYSICAL values** — not packed bytes — with a **tolerance frozen from the
     observed dtypes and packing BEFORE the comparison runs**. ⛔ "Within float tolerance" left unnamed
     is adjustable, which would defeat the stop rule below.
  ⛔ If either fails, the subset route is wrong and **this plan stops**.
  ⚠️ The probe response is **all finite**, so it cannot exercise fill-value decoding. ⇒ Keep the existing
  **synthetic** fill→`NaN` test (`test_imerg_extract.py:258-263`); ⛔ a second live granule would not
  test it either.

- **D6 — Nothing here compares against gauges.** ⛔ No skill claim, no diurnal analysis, no correction.
  This plan acquires and publishes; the comparison is separate work.

## Tasks

### T1 — the subset contract and route (depends: nothing)
**In:** D1's second contract; D2's route-in-identity; the OPeNDAP client per D4.
**Out:** any bulk retrieval; any change to the archive contract.
**Verify:** a full-field granule offered against the subset contract is **refused**, and vice versa;
a response whose lon/lat vectors differ by one cell is refused. ⛔ Prove each by reverting.

### T2 — cross-check the two routes on the granule we already hold (depends: T1)
**In:** D5 — fetch the subset for `2020-07-15T00:00Z` and compare against the full-field granule already
on disk, cell for cell over the box.
**Out:** any further retrieval until this passes.
**Verify:** the coordinate vectors match exactly first, then all 4,500 decoded values within the
pre-frozen tolerance. ⛔ **A mismatch stops the plan** — report it rather than adjusting a tolerance to
pass, which is precisely why the tolerance is frozen beforehand.

### T3 — retrieve the window (depends: T2)
**In:** D3 — all 105,216 granules through the subset route, resumable, gaps recorded as gaps.
**Out:** any extraction, comparison or publication beyond the bundle itself.
**Verify:** the acquisition record reports requested/retrieved/missing with the route named; the total
lands near the projected 2.7 GB; ⛔ nothing is written under `data/dhm_precip/era5_land*` and nothing
under any `points/` tree is deleted.

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

The full 2020–2025 IMERG Early record over the study box, acquired through a separately-frozen subset
contract, cross-checked cell-for-cell against the archive route on a shared granule, at ~2.7 GB.
⛔ No gauge comparison and no skill claim — that is the next milestone.

## Non-goals

Any gauge comparison, diurnal analysis or skill claim (D6) · relaxing or sharing the archive contract
(D1) · deleting the archive route (D5) · operational use — the ~4h20m latency rules it out · IMERG Final
or Late · the high basins, where retrieval degrades over snow and ice (M-A9 §7.3).
