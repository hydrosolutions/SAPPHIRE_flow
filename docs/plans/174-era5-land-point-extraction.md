---
status: DRAFT
created: 2026-08-15
revised: 2026-08-15
plan: 174
title: M-A5 — ERA5-Land point extraction at the 26 station locations
scope: Extract the M-A4 hourly-mm ERA5-Land product at the 26 gauge locations with a NAMED, recorded extraction operator; record per-station grid coordinates and model-orography elevation; quantify station-to-grid elevation mismatch; run an operator-sensitivity comparison. Explicitly NOT the gauge-vs-ERA5 comparison (M-A6), NOT IMERG (split to its own plan — no acquisition path exists), NOT any QC-mask application (the mask is the gauge side; pairing is M-A6), NOT a correction design.
depends_on: [171]
blocks: [M-A6, M-A7]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 174 — M-A5 ERA5-Land point extraction

## Status
**DRAFT.** Not for implementation until the owner confirms.

## Scope correction this plan makes to the milestone

M-A5 as written says "**also extract IMERG at the same 26 points** … nearly free once the extraction
pipeline exists." That is **wrong, and the error is worth recording**: IMERG appears nowhere in this
repo except two design documents. It needs a NASA Earthdata account and credentials (an operator act,
exactly like the CDS licence acceptance that held Plan 171), a different service (GES DISC / OPeNDAP),
**half-hourly** native resolution requiring aggregation to hourly, and a **rate (mm/hr) convention
rather than an accumulation** — so none of M-A4's deaccumulation logic transfers. That is an
M-A4-shaped body of work, not a parameter.

**Owner decision 2026-08-15: SPLIT.** This plan is ERA5-Land only. IMERG acquisition + extraction
becomes its own plan, mirroring M-A4. **Consequence: M-A6 opens as a two-way comparison** (gauge vs
ERA5-Land) and becomes three-way when the IMERG plan lands. This is the right order anyway —
ERA5-Land is the milestone's stated primary and the one that de-risks Plan 152's OOD concern.

## Problem

M-A4 built the acquisition and left a per-year gridded product at
`data/dhm_precip/era5_land/hourly_mm/era5_land_tp_mm_{year}.nc` — hourly, mm, period-ending UTC, on a
0.1° grid over 26–31 N / 80–89 E. M-A6 needs those values **at the gauges**, not on the grid. Turning
a 0.1° field into 26 point series requires choosing an operator, and that choice is not neutral for
this dataset.

## Measured facts this plan rests on

Run against `data/dhm_precip/station_coordinates.csv` (26 stations, gitignored) on 2026-08-15:

| Fact | Value | Why it matters |
|---|---|---|
| Stations inside the acquired box | **26 of 26** | No silent-NaN class. A station outside would extract as NaN rather than error |
| Elevation range | **67 → 3,700 m** | The mismatch against a smoothed 0.1° orography will be large and elevation-dependent |
| Max offset to nearest grid node | **5.36 km lat / 4.81 km lon** | Up to ~7 km diagonal in a ~10 km cell — nearest and bilinear will differ materially. The operator choice is not cosmetic |
| Grid cells holding >1 station | **1** — Kirtipur (1,364 m) and Khumaltar (1,334 m) | See D6: a free natural experiment |

## Design decisions

- **D1 — Nearest cell centre is the primary operator; bilinear is the sensitivity arm.**
  Precipitation is not a smooth field, and **bilinear interpolation of a precipitation field
  systematically damps extremes** — it is a weighted average of four cells. Our track's central
  finding is that the distribution **tail** is what fails to transfer and what a flood system depends
  on, so an operator that smooths the tail biases exactly the quantity of interest. Nearest preserves
  the model's own distribution shape at the cost of a positional error bounded by half a cell
  diagonal (~7 km here, measured above). Both are computed; **nearest is what M-A6 consumes** unless
  the sensitivity result overturns it, and that decision is recorded in the manifest, not assumed.

- **D2 — "Containing cell" and "nearest cell centre" are NOT the same operator and must not be
  conflated.** ERA5-Land values are cell-centre-registered on whole-0.1° nodes. "Nearest node" and
  "the cell whose bounds contain the point" coincide only when the cell is defined as centred on the
  node. State the registration explicitly and assert it against the product's own coordinate vector
  rather than assuming — the Plan 171 lesson (**the first written statement of ERA5-Land's
  accumulation convention was wrong**) applies to registration too.

- **D3 — Model orography is NOT in the acquired product, and this is a real gap.**
  `era5_request.py:27` acquires `total_precipitation` and nothing else. M-A5's exit requires
  **model-orography elevation per station**, which ERA5-Land does not ship as an ordinary hourly
  variable. The route must be **verified against the service before it is coded** (D3a below); the
  plan must not hard-code an assumed dataset name. This is the single largest unknown in this plan.

- **D3a — Orography acquisition is a task, not an assumption.** Task 1 is a probe: establish how
  ERA5-Land's static orography/geopotential is actually obtained, capture the exact request or file
  reference the way Plan 171 captured the CDS payload, and record it in the plan before task 2 codes
  against it. **If the probe shows this needs an operator act** (a second licence acceptance, a
  different account), that surfaces as an owner ask immediately rather than mid-build.
  **Fallback if orography proves unobtainable in this window:** report the mismatch against a public
  DEM aggregated to the ERA5-Land grid, **labelled as a DEM proxy, not model orography** — they are
  not the same quantity, and conflating them would misstate the diagnostic.

- **D4 — Extraction never applies the QC mask.** The M-A3 mask is the **gauge** side. Applying it here
  would bake a pairing decision into the ERA5 artefact and make it unusable for anything else. Rule 1
  (MNAR) binds M-A6, which must apply the mask **identically to both sides at pairing time**. This
  plan produces a complete, unmasked ERA5 series per station.

- **D5 — Time axis: assert alignment, do not assume it.** M-D3 established the gauge convention is
  **period-ending**, and ERA5-Land is also period-ending, so they align with **no offset** — this is
  the fact that removed the ±1 h uncertainty blocking all diurnal work. Because so much rests on it,
  the extraction asserts the product's own stamp convention and hourly spacing rather than
  inheriting the claim from a document.

- **D6 — Kirtipur/Khumaltar: a free empirical bound on within-cell representativeness.**
  Two gauges, **3.5 km apart, in the same ERA5 cell, 30 m apart in elevation**. ERA5 cannot
  distinguish them: it returns one identical series for both. Their observed disagreement is
  therefore a **pure lower bound on within-cell representativeness error**, with the elevation
  confound almost entirely removed — no model error, no operator choice enters it.
  The milestones doc says representativeness can be "characterised, not decomposed"; this pair does
  not decompose it, but it **bounds one component empirically**, which is strictly more than the
  milestone assumed was available. Emit it as a named diagnostic.
  **Honest limits:** n = 1 pair, and it bounds within-cell variability only at ~3.5 km separation in
  one valley — it is not a network-wide estimate and must not be reported as one.

- **D7 — Deterministic and regenerable, with its own identity.** Mirror M-A4's two-identity pattern
  (`era5_manifest.py`): an **extraction identity** over (operator, station table checksum, source
  product checksums, code version) distinct from the source product identity, so re-running with a
  changed operator produces a new artefact rather than silently overwriting. Same atomic
  tmp → `os.replace` publish, same reopen-and-validate discipline.

- **D8 — Station identity is the coordinate table's `station` column,** which is the same key the
  gauge side uses (`loader.py` unpivots on it and `station_coordinates.csv` carries `excel_col`).
  Assert the extracted station set **equals** the 26-station gauge set exactly — a silent
  partial join here would corrupt every M-A6 pairing downstream.

## Constraints

1. **No real ERA5-Land data exists on disk yet** — the owner's task 2b (one October-2021 CDS call) has
   not run. This plan is therefore built and tested **against synthetic NetCDF fixtures** conforming
   to the M-A4 D9 schema, exactly as Plan 171 built acquisition ahead of the real call. A real-data
   gate is authored but **skips** until the product exists (the `DHM_PRECIP_XLSX` pattern).
2. **The repo is public.** Station coordinates and extracted series are gitignored research data
   (`data/dhm_precip/`). Test fixtures are **synthetic**; no real coordinates in committed tests.
3. **Verify against the service, not the documentation** (Plan 171's hardest-won rule) — binds D2, D3
   and D5.

## Tasks

**Phase 1 — ground the unknown**
- **1a** *(probe, no production code)* — Establish the ERA5-Land static-orography route. Capture the
  exact request/file reference. Record it in this plan under "Observed orography route". If it needs
  an operator act, stop and raise it.

**Phase 2 — extraction core** *(depends: 1a)*
- **2a** — Synthetic ERA5-Land fixture builder (D9-schema-conformant NetCDF, known analytic field so
  extraction results are hand-checkable).
- **2b** — Grid registration assertions (D2) + time-axis assertions (D5).
- **2c** — The two operators (nearest, bilinear) behind one named, recorded interface (D1).
- **2d** — Station-set equality assertion (D8) and the box-containment check.

**Phase 3 — diagnostics** *(depends: 2)*
- **3a** — Per-station table: grid coords, model-orography elevation (or DEM proxy per D3a),
  station elevation, mismatch.
- **3b** — Operator-sensitivity comparison (nearest vs bilinear), reported on the statistics the track
  actually cares about — **including tail quantiles**, not just means.
- **3c** — The Kirtipur/Khumaltar within-cell diagnostic (D6).

**Phase 4 — publish** *(depends: 3)*
- **4a** — Extraction identity + manifest + atomic publish (D7); reopen-and-validate.
- **4b** *(operator, gated on task 2b)* — Run against the real product once it exists.

## Exit gates

- Extracted per-station hourly series for all **26** stations, unmasked, regenerable from the
  committed pipeline
- Operator **named and recorded in the manifest**, not implied by code
- Per-station elevation-mismatch table (with the orography source explicitly labelled)
- Operator-sensitivity comparison including tail quantiles
- Kirtipur/Khumaltar within-cell bound emitted as a named diagnostic
- `ruff` + `pyright` clean; pyright ratchet honoured; full suite green
- Real-data gate authored and **skipping** with a clear reason until 2b lands

## Open questions for the owner

1. **Does the sensitivity result get to overturn D1?** My position: yes, but only on a pre-stated
   criterion, decided **before** seeing the numbers — otherwise we are choosing the operator that
   flatters the comparison. Proposed criterion: keep nearest unless bilinear changes a **tail**
   statistic by less than the Kirtipur/Khumaltar within-cell bound (i.e. the operator choice is
   demonstrably smaller than irreducible representativeness error), in which case either is
   defensible and we keep nearest for tail fidelity anyway.
2. **Is a DEM-proxy fallback (D3a) acceptable** if model orography proves to need another operator
   act, or should the milestone block on the real thing?
