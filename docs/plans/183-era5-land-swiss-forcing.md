---
status: DRAFT
created: 2026-08-18
plan: 183
title: ERA5-Land forcing from the sloth-dynamic store — the lineage aquacast was trained on
scope: aquacast's models were trained on catchment-averaged ERA5-Land; we feed MeteoSwiss. Close that substitution by reading the SAME store aquacast's data plane reads — `s3://sloth-dynamic/v1/era5/` (company infrastructure, owner-confirmed available) — and basin-averaging it onto SAP3 polygons with the mappings `aquaire` documents. Rewritten 2026-08-18: an earlier draft proposed building CDS acquisition ourselves, which is unnecessary now that the store and its reference implementation are available.
depends_on: []
blocks: [152]
supersedes: []
---

# Plan 183 — ERA5-Land forcing from the sloth-dynamic store

## What changed, and why this is now small

An earlier draft scoped this as *build our own CDS acquisition*, reusing Plan 171's Nepal
precipitation toolchain. **That is no longer the cheapest path.** The owner confirmed
`s3://sloth-dynamic/v1/era5/` — the exact store `aquacast`'s data plane (`aquaire`) reads — is
company infrastructure and available to us.

So we are no longer *reproducing* the training lineage. We are **reading it**. That removes the
largest risk the earlier draft carried: whether our acquisition and Caravan's agree.

**Decision (owner, 2026-08-18): read the store directly; `aquaire` is the authoritative SPEC, not a
dependency.** Taking `aquaire` as a runtime dependency would add ~10 constraints to the operational
lockfile — `pandas>=3`, `dask`, `geopandas`, `contextily`, `netcdf4`, `s3fs` — plus `rivretrieve`,
which is **not on PyPI** (a second private dependency needing its own token), and its `pyproject`
assumes an editable `../aquacast` sibling checkout rather than library consumption. We resolved one
dependency conflict from `aquacast` alone this week (`rich>=15` vs `prefect<15`, still carried as a
uv override). `aquaire` remains the reference implementation and stays usable out-of-band.

## The store, and the canonical mapping we must follow exactly

From `aquaire/src/aquaire/sources/era5.py` — this is the contract:

```
s3://sloth-dynamic/v1/era5/   ERA5-Land DAILY aggregates, ONE zarr per variable,
                              zarr v3, inline consolidated metadata,
                              0.1° global (1801×3600), LAND-ONLY so ocean is NaN, CF-1.11

canonical name         unit    store variable                     native  transform
precipitation          mm/day  total_precipitation_sum            m       × 1000
mean_temperature       degC    temperature_2m_mean                K       − 273.15
solar_net_radiation    W/m²    surface_net_solar_radiation_sum    J/m²    ÷ 86400
thermal_net_radiation  W/m²    surface_net_thermal_radiation_sum  J/m²    ÷ 86400
```

**Three traps `aquaire` documents, each of which produces plausible-looking wrong numbers:**

1. **Radiation is a daily ACCUMULATION** (`cell_methods: time: sum`, J/m² over the day), so the
   divisor is **86400, not 3600**. The hourly divisor inflates radiation **24×**.
2. **Daily aggregation is UTC 00–24**, verified from the store's own `history` provenance
   (`daily_reduce(..., time_shift={'hours': 0})`) rather than from its label.
3. **`thermal_net_radiation` is downward-positive and therefore negative.** Nothing clips the sign;
   "fixing" it would be wrong.

## ⚠️ This reopens radiation (G2)

`cmal_pool_PT` declares only precipitation and temperature, which is why Plan 152 scoped radiation
out. But the store and the data plane carry **four** canonical forcings, and the sub-daily artifact
may want them. **Do not silently narrow to two** — decide it (D2) rather than inherit it from what
the daily artifact happens to declare.

## Tasks

### T1 — read the store
Add `s3fs` (the only missing piece — we already have `zarr>=3.0`, `xarray`, `dask`). Open the
per-variable zarrs read-only and apply the mappings above verbatim.

**Acceptance:** a known basin's daily series comes back in canonical units with a plausible range —
and specifically that `thermal_net_radiation` is **negative**, which catches a sign "correction"
and a units error at once.

### T2 — basin-average onto SAP3 polygons
Route the grid through `ExactExtractGridExtractor` — the same extractor already used for MeteoSwiss
NWP, so the spatial representation matches what the rest of the system expects. Write to
`historical_forcing` under a distinct source, **alongside** the MeteoSwiss rows so both lineages
stay queryable and comparable.

**The land-only grid is the trap here:** ocean cells are NaN, so a coastal or partially-masked
catchment can silently average over fewer cells than it should. Assert the contributing-cell count,
not just that a number came back.

### T3 — validate against Caravan's published indices
Even reading the same store, **our catchment-averaging is still ours** — cell weighting, boundary
handling and the polygon itself all differ from Caravan's. So the check still matters, though it is
now a check on averaging rather than on acquisition.

The statics parquet carries Caravan's climate indices, computed from Caravan's own ERA5-Land, for
**296 Swiss basins**: `p_mean` (mean 4.61 mm/day), `frac_snow` (0.248), `high_prec_freq` (0.033),
`low_prec_dur` (3.07). Recompute them from our extraction over Caravan's window
(1981-01-01 → 2020-12-31) and compare per basin.

**Ordered so each failure localises:** `p_mean` first (systematic bias), then `frac_snow` (exercises
the temperature path), then the `freq`/`dur` pair (daily-boundary convention).

**Tolerance is set BEFORE the comparison (D1), not after seeing the numbers.**

### T4 — expose as an injectable `WeatherReanalysisSource`
So training, hindcast and operational paths select it via `DeploymentConfig.reanalysis_source`
without touching call sites — the injectability `v0-scope.md` §I2 already requires.

## Open decisions

| # | Question | Recommendation |
|---|---|---|
| **D1** | Agreement tolerance for T3? | Set before running. A scientific judgement about tolerable forcing drift — owner/modeller, not engineering. |
| **D2** | Two forcings or four? | **Ingest all four.** The store has them, the sub-daily artifact may need them, and re-running the extraction later costs more than storing two extra columns now. |
| **D3** | Daily store only, or sub-daily too? | Daily first. `aquaire` has a separate `sources/subdaily.py`; treat sub-daily as a follow-on once the daily path is validated. |

## Workload

Much smaller than the CDS draft. **T1/T2/T4 are assembly** — the extractor, the store schema and the
Protocol all exist. **T3 remains the uncertain one**, but its risk has dropped from "does our
acquisition match theirs" to "does our averaging match theirs", which is a narrower question with a
smaller failure surface.

**No CDS download**, so the multi-day acquisition long-pole of the earlier draft is gone entirely.

## Non-goals
- Retraining any model (the rejected option).
- Replacing MeteoSwiss as v0 operational forcing — this lands alongside it.
- Taking `aquaire` as a runtime dependency (D above); it stays the spec.
- Catchment delineation — we have basin polygons already.

## References
- `sandrohuni/aquaire` — `src/aquaire/sources/era5.py` (the mapping contract), `sources/grid.py`
  (lat/lon slicing conventions), `store.py` (cache layout). **Private; read as specification.**
- `hydrosolutions/static-attrs-nepal` — the DHM deployment appliance; same lineage, different packaging.
- Plan 152 § four substitutions — this closes "NWP ≠ ERA5-Land".
