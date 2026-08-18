---
status: DRAFT
created: 2026-08-18
plan: 183
title: ERA5-Land forcing for Switzerland — our own ingestion, validated against Caravan
scope: aquacast's models were trained on catchment-averaged ERA5-Land; we feed MeteoSwiss. Close that substitution by acquiring ERA5-Land ourselves (the Recap gateway cannot serve a Swiss AOI — owner, proven) and basin-averaging it onto SAP3 polygons. Most of the machinery already exists from Plan 171. The plan's centre of gravity is NOT the pipeline but the VALIDATION: our extraction must be shown to reproduce Caravan's, or the model silently receives a distribution it never saw.
depends_on: [171]
blocks: [152]
supersedes: []
---

# Plan 183 — ERA5-Land forcing for Switzerland

## Why this and not retraining

Owner weighed retraining the models on Swiss weather against ingesting ERA5-Land ourselves, and chose
the latter. The deciding argument is that `docs/v0-scope.md` and `docs/architecture-context.md`
**already commit v1 to ERA5-Land** ("Switch to ERA5-Land via `WeatherReanalysisSource` Protocol for
Nepal"). Nepal has no MeteoSwiss. ERA5-Land is the only lineage that serves both deployments, so this
is v1 critical-path work rather than a Swiss detour — and retraining on MeteoSwiss would discard the
12,952-basin pooling that makes `cmal_pool_PT` transferable in the first place.

## What already exists — this is assembly, not greenfield

| piece | where | state |
|---|---|---|
| CDS acquisition (resumable, checksummed, manifest) | `scripts/dhm_precip/era5_acquire.py`, `era5_request.py` | **built** (Plan 171), parameterised by `area` + `variable` |
| Deaccumulation of ERA5 accumulations | `scripts/dhm_precip/era5_deaccumulate.py` | **built** |
| Transform / unit conversion | `scripts/dhm_precip/era5_transform.py` | **built** |
| Basin-average extraction | `preprocessing/exact_extract_grid_extractor.py` | **built**, already used for MeteoSwiss NWP |
| Storage | `historical_forcing` (source/parameter keyed) | **built** |
| Injectable forcing Protocol | `WeatherReanalysisSource` | **built** |

**Three real gaps:** the toolchain is **precipitation-only** (`DEFAULT_VARIABLE = "total_precipitation"`),
it is **point-extraction** at gauges rather than basin-average, and it is wired to a **Nepal** study
box (26–31 N, 80–89 E).

## The gate: our extraction must reproduce Caravan's

**This is the point of the plan.** ERA5-Land is a public dataset, so anyone can download it — the
risk is not acquisition but that *our* catchment-averaging differs from *Caravan's*, giving PT a
distribution shift it never trained on. `area` is not the only thing that can silently rescale a
model's inputs.

**We have per-basin ground truth without needing Caravan's raw timeseries.** The statics parquet
carries Caravan's **climate indices**, which are derived from Caravan's own ERA5-Land forcing by
`calculate_climate_indices` — verified present for **296 Swiss basins**: `p_mean` (mean 4.61 mm/day),
`frac_snow` (0.248), `high_prec_freq` (0.033), `low_prec_dur` (3.07), plus the `*_freq`/`*_dur` pair.

So the validation is a **closed loop**: recompute those indices from OUR extracted ERA5-Land over
Caravan's own window (`--start 1981-01-01 --end 2020-12-31`, per aquacast's docs) and compare to the
published values, per basin. Agreement means our extraction reproduces their lineage; disagreement
localises the error to a specific index and basin.

**T3 is a GATE, not a task.** No ERA5 forcing is wired into any model path until it passes.

## Tasks

### T1 — extend acquisition to Switzerland and temperature
Parameterise the study area (currently the Nepal box) and add `2m_temperature` alongside
`total_precipitation`. Temperature is **instantaneous, not accumulated** — it must NOT go through
deaccumulation, and conflating the two is the most likely silent error here.

Units: ERA5-Land ships precipitation in **m** and temperature in **K**; SAP3 canonical is **mm** and
**°C**.

### T2 — basin-average onto SAP3 polygons
Route the acquired grid through `ExactExtractGridExtractor` — the same extractor already used for
MeteoSwiss NWP, so the spatial representation matches what the rest of the system expects. Write to
`historical_forcing` under a distinct source, alongside (not replacing) the MeteoSwiss rows, so both
lineages remain queryable and comparable.

### T3 — ⛔ VALIDATION GATE: reproduce Caravan's climate indices
Recompute Caravan's indices from our extraction over 1981-2020 and compare per basin against the
published values.

**Acceptance is numeric and per-index**, not "looks close": agree on `p_mean` first — it is the
simplest and any systematic bias shows there. Then `frac_snow` (which exercises the temperature path
and therefore catches a K/°C or instantaneous/accumulated error), then the `high_/low_prec_freq/dur`
pair (which exercise the daily aggregation boundary).

**Tolerance must be decided before running the comparison, not after seeing the numbers.** Record it
in the plan when D1 is answered.

### T4 — expose as an injectable `WeatherReanalysisSource`
So training, hindcast and operational paths can select it per `DeploymentConfig.reanalysis_source`
without touching call sites — the injectability `v0-scope.md` §I2 already requires.

### T5 — retain hourly for the sub-daily model
`cmal_global_subdaily_PT` declares `daily` + `hourly`. ERA5-Land is hourly natively, so store the
hourly product and aggregate to daily rather than discarding it — re-acquiring later costs a full
CDS re-download.

## Open decisions

| # | Question | Recommendation |
|---|---|---|
| **D1** | What agreement tolerance counts as "reproduces Caravan"? | Decide **before** running T3. Suggest a relative tolerance on `p_mean` in the low single-digit percent, but the owner/modeller should set it — it is a scientific judgement about how much forcing drift the model tolerates, not an engineering one. |
| **D2** | Which basins for validation — all 296 in the parquet, or the 148 we onboard? | **All 296.** They cost nothing extra to compare and a systematic error may only appear in a subset (e.g. high-alpine, where `frac_snow` is sensitive). |
| **D3** | Full 1981-2020 acquisition, or a shorter validation window first? | **Short window first** for the pipeline, then full for validation — the indices are only comparable over Caravan's exact window, but the plumbing can be proven on one year. |

## Workload estimate

Deliberately rough, and the uncertainty is concentrated in one place.

- **T1** small — parameterising an existing, tested acquisition; the new work is the temperature path.
- **T2** small-to-moderate — the extractor exists; the work is wiring the grid to it and the store.
- **T3 unknown, and it dominates.** If our extraction agrees with Caravan first time, hours. If it
  does not, the debugging is open-ended: grid alignment, cell weighting, boundary handling,
  aggregation-window convention. **This is the honest risk in the plan** — everything else is
  assembly of proven parts.
- **T4/T5** small.

**The CDS download itself is the long pole in wall-clock**: 40 years of hourly ERA5-Land over a Swiss
box is a large, queued, rate-limited request. Start T3's acquisition early even while T1/T2 are being
written.

## Non-goals
- Retraining any model (the rejected option).
- Replacing MeteoSwiss as the v0 operational forcing — this lands **alongside** it.
- Nepal acquisition (Plan 171 owns it), and the DHM precipitation research track.

## References
- Plan 171 — the ERA5-Land acquisition this reuses.
- Plan 152 § four substitutions — this closes "NWP ≠ ERA5-Land".
- `docs/v0-scope.md` §I2, `docs/architecture-context.md` — the v1 ERA5-Land commitment.
