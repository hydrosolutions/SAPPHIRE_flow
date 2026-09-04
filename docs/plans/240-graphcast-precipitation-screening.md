---
status: DRAFT
created: 2026-09-04
plan: 240
title: M-A12 — screen GraphCast precipitation against the DHM gauges, JJAS 2022-2025
scope: Retrieve GraphCast 00Z 6-hourly accumulated precipitation over the study box at four leads, extract to the 26 gauge points, and run the ALREADY-TRACKED Plan 238 estimator. NOT a new statistic, NOT a new framework, NOT AIFS, NOT runoff.
depends_on: [238]
source: AIWP Model Reforecasts (AWS Open Data, noaa-oar-mlwp-data); Codex plan review 2026-09-04
---

# Plan 240 — M-A12 GraphCast precipitation screening

## Status

**DRAFT.** Not for implementation until the owner confirms.
⚠️ Renumbered 239 → 240: another session took 239 (`239-forcing-cadence-and-skill-sample-gates.md`).
⚠️ **Revised after an independent Codex review returned NEEDS-CHANGES**, which cut the acquisition to
**half** its original size and named four items that would otherwise make the number wrong.

## ⛔ PROPORTIONALITY IS BINDING

**Reuses `scripts/dhm_precip/ifs_event_timing.py` (Plan 238) unchanged.** ⛔ No new statistic, no new
framework, no config surface, no bootstrap, no second model. **Adding length is a cost.**

## ⛔ PER-RUN SCOPE (binding)

- ✅ **Network: the NOAA AWS bucket `noaa-oar-mlwp-data` ONLY.** ⛔ Not WeatherBench 2 — one route, not
  two. ⛔ **Never the Copernicus CDS.** ⛔ No MARS, no credentialed source.
- ⛔ Never write under `data/dhm_precip/era5_land*`, `imerg_early/`, `tigge/`, or any `points/` tree.
- **Hold at PR.** Patch bump folded into the code commit; docs separate and unbumped; stage by explicit
  path, never `git add -A`.

## Why this plan exists

Published work: AI models **under-predict precipitation above ~10 mm** from loss-function smoothing,
and GraphCast/Pangu/FuXi produce over-smooth precipitation fields. Our IFS control screening measured
event magnitudes at **0.45–0.92 of observed** with ~30–40 % of gauge events missed. ⇒ Is an AI product
better or worse than IFS **on the axis that decides a flood peak**, over Nepal, against the same gauges?

**What is available** (AWS Open Data registry, measured 2026-09-04): **only GraphCast carries
precipitation** — FourCastNet v2-small and Pangu-Weather carry none, so they are excluded by
availability, not by choice. GraphCast runs from **01/2022** ⇒ **four** JJAS seasons. 6-hourly steps,
240 h lead, 2 runs/day, NetCDF, licence "no restrictions".
⛔ **No public AIFS archive** — operational only from 2025, open data retains 4 days; self-generated
hindcasts would carry ERA5 **training leakage** and better-than-operational initial conditions.

## Decisions

- **D1 — Retrieve ONLY what the frozen convention consumes.** 🔴 The estimator selects
  `init_hour == 0` (`scripts/dhm_precip/ifs_event_timing.py:103`) and leads **6/12/18/24**
  (`:100`). ⇒ **488 00Z forecasts**, four leads each — ⛔ **not** 976 runs and ⛔ not 0–240 h.
  Every 12Z run and every lead beyond 24 h is waste.

- **D2 — Pin the exact archive variant BEFORE retrieving.** "GraphCast Operational" is no longer
  sufficient: the archive carries multiple initialisation variants. ⇒ Pin the **GFS-initialised** NOAA
  prefix and model version explicitly; D5's honesty depends on it.

- **D3 — The estimator is unchanged; the EXTRACTOR meets its contract.** `ifs_event_timing.py:302/329`
  requires seasonal Parquet with `station, init_time_utc, ending_lead_hours, valid_time_utc, tigge_mm`.
  ⇒ The GraphCast extractor emits **that** contract. ⚠️ That is ordinary boundary adaptation, **not** a
  new estimator. ⛔ Report-and-stop applies **only** if the sampled data cannot be represented as
  genuine 6-hour period-ending precipitation — ⛔ not merely because dimensions or column names differ.

- **D4 — Lock the physical semantics from T1's REAL sample**, not from documentation: the precipitation
  variable, the **units→mm conversion** (WB2's documented example is in **metres**), the six-hour period
  semantics, and `valid_time = init + ending_lead`. ⚠️ The estimator compares forecast magnitude against
  a **gauge-derived millimetre threshold** (`ifs_event_timing.py:489`) — a units error would silently
  corrupt every event and every magnitude ratio.

- **D5 — Name the point operator: nearest-cell great-circle**, matching the existing IFS extraction
  (`scripts/dhm_precip/tigge_ifs.py:467`). ⚠️ Leaving interpolation unspecified can materially change
  mountain precipitation, and an operator difference would masquerade as a model difference.

- **D6 — Same seasons AND same actual support.** The IFS comparison is **recomputed on the same four
  seasons** (passing `2022 2023 2024 2025` makes `build_cells` recompute each station's wet-window
  quantile from only those windows, `:416`). 🔴 **And** because gaps are tolerated, confirm GraphCast
  and IFS share the **same actual `(station, season)` cell set** before ranking. ⛔ Do not rank on
  different support.

- **D7 — Report the initialisation confound.** GraphCast here is **GFS-initialised**; IFS control is
  ECMWF-initialised. ⇒ This answers *"which archived operational product scores higher against these
  gauges?"* ⛔ It does **not** license any "AI versus physics" claim.

- **D8 — Nothing here touches runoff.** ⛔ No hydrological model, no discharge, no bias correction.

## Tasks

### T1 — measure the volume, then STOP
**Outcome:** measured bytes for one 00Z forecast's precipitation over the study box, by whole-file read
and by any partial-read the bucket supports, plus a **projection** for 488 forecasts; and the D4
semantics read off that real sample.
**In:** one forecast from the pinned GFS-initialised prefix.
**Out:** ⛔ any bulk retrieval; any extraction.
**Verification:** the report states bytes per forecast by each route, the projected total (⚠️ stated as
a projection, not a byte promise), free disk, the chosen route with its reason, and the confirmed
variable/units/period/valid-time semantics. ⛔ Exactly one forecast on disk afterwards.
**Pre-change:** N/A — measurement task.

### T2 — retrieve, extract, and compare (depends: T1)
**Outcome:** the Plan 238 estimator's report for **GraphCast and IFS on the same four seasons and the
same station-season support**, with increments and their counts.
**In:** the pinned prefix, 488 00Z forecasts, leads 6/12/18/24; nearest-cell great-circle extraction to
the 26 gauge points; `ifs_event_timing.py` **unchanged**.
**Out:** ⛔ any new statistic; runoff; bias correction; any other model.
**Verification:** a GraphCast seasonal Parquet loads through the **unmodified** estimator and produces a
report — ⚠️ rerunning the existing tests proves only no-regression, **not** GraphCast compatibility, so
the gate is a produced GraphCast report. Gaps are recorded as gaps, never filled. The station-season
support of both products is asserted identical before any ranking.
**Pre-change:** no GraphCast figures exist; the comparison cannot be produced today.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Exit gates

- GraphCast and IFS increments reported on the **same four seasons and identical station-season
  support**, through the **unchanged** Plan 238 estimator.
- The pinned archive variant, the units conversion, the point operator and the initialisation confound
  are all stated in the report.
- ⛔ No new statistic and no estimator change.

```bash
uv run pytest tests/unit/scripts/test_ifs_event_timing.py tests/unit/scripts/test_m_a11c_document_binding.py
uv run ruff check scripts/dhm_precip/
```

## Non-goals

AIFS (no public archive; self-generated carries training leakage) · FourCastNet and Pangu (no
precipitation variable) · WeatherBench 2 as a second route · 12Z runs and leads beyond 24 h (D1) ·
seasons 2020-2021, which GraphCast does not cover · runoff, discharge, bias correction · a new
estimator, statistic or framework.
