---
status: DRAFT
created: 2026-09-04
plan: 239
title: M-A12 — screen GraphCast precipitation against the DHM gauges, JJAS 2022-2025
scope: Retrieve GraphCast 6-hourly accumulated precipitation over the study box, extract to the 26 gauge points, and run the ALREADY-TRACKED event-timing estimator against it. NOT a new statistic, NOT a new framework, NOT AIFS, NOT a runoff experiment.
depends_on: [238]
source: AIWP Model Reforecasts (AWS Open Data, noaa-oar-mlwp-data); WeatherBench 2
---

# Plan 239 — M-A12 GraphCast precipitation screening

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING

**This reuses `scripts/dhm_precip/ifs_event_timing.py` (Plan 238) unchanged.** GraphCast is 6-hourly,
the same shape the estimator already consumes. ⛔ No new statistic, no new framework, no new
abstraction, no config surface. **Adding length is a cost.** If this plan needs a new estimator, the
premise is wrong and it should stop.

## ⛔ PER-RUN SCOPE (binding)

- ✅ **Network: the NOAA AWS bucket `noaa-oar-mlwp-data` and/or WeatherBench 2 on GCS only.**
  ⛔ **Never the Copernicus CDS.** ⛔ No ECMWF MARS, no credentialed source.
- ⛔ Never write under `data/dhm_precip/era5_land*`, `imerg_early/`, `tigge/`, or any `points/` tree.
  GraphCast output goes to its own root.
- **Hold at PR.** Patch bump folded into the code commit; docs separate and unbumped; stage by explicit
  path, never `git add -A`.
- ⚠️ Verify every code reference against source before relying on it.

## Why this plan exists

Published work shows AI models **under-predict precipitation above ~10 mm** from loss-function
smoothing, and that GraphCast, Pangu and FuXi produce over-smooth precipitation fields. Our own IFS
screening already measured event magnitudes at **0.45–0.92 of observed**. ⇒ The question is whether an
AI model is better or worse than IFS **on the axis that decides a flood peak**, over Nepal, against the
same gauges.

**What is actually available** (measured from the registry, 2026-09-04):
- **Only GraphCast carries precipitation** — FourCastNet v2-small and Pangu-Weather carry **none**.
- GraphCast Operational runs from **01/2022** ⇒ overlap with the gauge record is **JJAS 2022-2025, four
  seasons**, not six.
- **6-hourly steps**, 240 h lead, 2 runs/day (00Z/12Z), NetCDF, licence **"no restrictions"**.
- ⛔ **No public AIFS archive exists** — AIFS reached operational release in 2025 and ECMWF open data
  retains **four days**. AIFS weights are open on Hugging Face, but self-generated hindcasts would carry
  **ERA5 training leakage** and better-than-operational initial conditions. **Out of scope here.**

## Decisions

- **D1 — GraphCast only.** The other two archived models have no precipitation variable at all.
  ⛔ Do not retrieve them.

- **D2 — Reuse the Plan 238 estimator unchanged.** GraphCast's 6-hourly accumulation matches the
  window shape `ifs_event_timing.py` already consumes. ⇒ Same event definition, same null, same
  increments, same frozen convention. ⛔ **If the module needs a change to accept GraphCast, that is a
  finding to report, not a licence to extend it.**

- **D3 — Four seasons, and say so.** JJAS 2022-2025. ⚠️ The IFS comparison must be recomputed **on the
  same four seasons**, never quoted from the six-season run — that would compare different samples.

- **D4 — Prefer chunked partial reads over whole-file downloads.** The NetCDF files are global with 13
  pressure levels; we need one surface variable over a 9°×5° box. ⇒ If WeatherBench 2's Zarr on GCS
  supports reading only the box and only precipitation, use it. ⛔ **T1 measures the volume before
  anything bulk is retrieved** — the same gate that turned IMERG from 847 GB into 2.6 GB.

- **D5 — Report the initialisation difference.** GraphCast Operational is initialised from an
  operational analysis that is **not** ECMWF's; IFS control is. ⇒ This is not a controlled comparison of
  model physics, and the report must say so. ⛔ Do not attribute a difference to "AI vs physics".

- **D6 — Nothing here touches runoff.** ⛔ No hydrological model, no discharge, no bias correction.
  This screens precipitation against gauges and stops.

## Tasks

### T1 — measure the volume, then STOP
**Outcome:** a measured per-forecast byte cost for precipitation over the study box, by both routes
(S3 NetCDF whole-file, and Zarr partial read if available), and a projection for JJAS 2022-2025.
**In:** one forecast's data; the registry/WB2 documentation.
**Out:** ⛔ **any bulk retrieval.** ⛔ Any extraction.
**Verification:** the report states bytes per forecast by each route, the projected total for
4 × 122 × 2 forecasts, free disk, and which route is chosen and why. ⛔ Exactly one forecast is on disk
afterwards.
**Pre-change:** N/A — measurement task.

### T2 — retrieve JJAS 2022-2025 over the box (depends: T1)
**Outcome:** GraphCast 6-hourly accumulated precipitation over the study box for the four seasons, with
an acquisition record naming requested/retrieved/missing and the digests of what was consumed.
**In:** the route chosen in T1.
**Out:** any extraction or comparison.
**Verification:** the record reports the counts and gaps; ⛔ a gap is recorded as a gap, never filled or
scaled; nothing written outside the GraphCast root.
**Pre-change:** N/A — retrieval task.

### T3 — extract to the 26 points and run the tracked estimator (depends: T2)
**Outcome:** the Plan 238 estimator's report for GraphCast and for IFS **on the same four seasons**,
side by side, with the increments and their counts.
**In:** `scripts/dhm_precip/ifs_event_timing.py` **unchanged**; extraction to the 26 gauge points.
**Out:** ⛔ any new statistic; any runoff work; any bias correction.
**Verification:** `uv run pytest tests/unit/scripts/test_ifs_event_timing.py` still passes unchanged,
and the report carries both products' observed/null/increment with matched and searched counts.
**Pre-change:** no GraphCast figures exist today; the comparison cannot be produced.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "parallel": false, "depends_on": ["phase-2"]}
  ]
}
```

## Exit gates

- GraphCast and IFS event-timing increments reported **on the same four seasons**, against the same 26
  gauges, through the **unchanged** Plan 238 estimator.
- The initialisation difference (D5) and the four-season restriction (D3) are stated in the report.
- ⛔ No new statistic and no estimator change; if one was needed, the plan reports that instead.

```bash
uv run pytest tests/unit/scripts/test_ifs_event_timing.py tests/unit/scripts/test_m_a11c_document_binding.py
uv run ruff check scripts/dhm_precip/
```

## Non-goals

AIFS, self-generated or otherwise (no public archive; self-generated carries training leakage) ·
FourCastNet and Pangu (no precipitation variable) · any runoff or discharge work · bias correction ·
a new estimator or framework · the 2020-2021 seasons, which GraphCast does not cover.
