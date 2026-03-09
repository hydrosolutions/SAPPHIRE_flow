# MSc Thesis: Uncertainty Paradigms for ML Ensemble Streamflow Forecasting

**Institution**: ETH Zurich
**Setting**: Swiss Alpine catchments (SAPPHIRE Flow v0)
**Duration**: 6 months
**Supervisors**: [lead developer], [ETH professor TBD]

## Research Question

Does propagating ensemble NWP members through an ML streamflow model (Paradigm
A) produce better probabilistic forecasts than learning uncertainty from data
(Paradigm B) or using deep model ensembles (Paradigm C)?

This question has never been answered for streamflow at any temporal resolution.

## Background

Operational ensemble flood forecasting systems (EFAS, GloFAS, NWM) propagate
NWP ensemble members through process-based hydrological models. Google Flood
Hub, the only operational ML streamflow system, instead uses deterministic NWP
with a learned CMAL distribution head. Whether ensemble NWP propagation adds
value when the hydrological model is an LSTM is unknown.

## Method

Train a single Entity-Aware LSTM on Swiss catchments (BAFU gauges, MeteoSwiss
observations) with three uncertainty configurations:

- **Paradigm A**: Each of 21 ICON-CH2-EPS ensemble members forced through a
  deterministic LSTM, producing 21 streamflow traces
- **Paradigm B**: Deterministic NWP (ICON-CH2-EPS control run) with a CMAL
  distribution head on the LSTM output
- **Paradigm C**: 5-10 LSTMs trained with different random seeds, each forced
  with deterministic NWP, producing a deep ensemble

All three share the same base LSTM architecture and training data. Differences
are isolated to the uncertainty generation mechanism.

## Data

- **Streamflow**: BAFU hourly discharge, ~50-80 catchments with sufficient
  record length
- **Training weather**: MeteoSwiss SwissMetNet hourly station observations
- **Forecast NWP**: ICON-CH2-EPS (21 members, 5-day lead, hourly, GRIB2 via
  MeteoSwiss STAC API). Archived by SAPPHIRE Flow starting ~mid-2026.
- **Evaluation period**: Requires ~6-12 months of archived NWP-streamflow pairs

## Evaluation

- CRPS (continuous ranked probability score) — primary metric
- CRPS decomposition: reliability + resolution
- Spread-skill ratio: does ensemble spread track forecast error?
- Rank histograms: calibration of ensemble forecasts
- Event-based analysis: performance during flood events
- Computational cost comparison (inference time per forecast)

## Expected Contributions

1. First head-to-head comparison of uncertainty paradigms A, B, C for ML
   streamflow forecasting
2. Empirical answer to whether NWP ensemble information adds value beyond
   what an LSTM can learn from data
3. Practical guidance for operational system design (SAPPHIRE Flow)

## Risks and Mitigations

- **Insufficient NWP archive length**: Start thesis after 6+ months of
  ICON-CH2-EPS archiving. Use reforecast data if available.
- **Small ensemble size (21)**: ICON-CH2-EPS has 21 members vs ECMWF's 51.
  Sufficient for the comparison; note as limitation.
- **Limited flood events in evaluation period**: Supplement with leave-one-out
  cross-validation on historical events using reanalysis forcing.

## Timeline

| Month | Activity |
|-------|----------|
| 1 | Literature review, data preparation, familiarize with SAPPHIRE codebase |
| 2 | Implement Paradigm A (ensemble propagation) and Paradigm B (CMAL head) |
| 3 | Implement Paradigm C (deep ensemble), run all experiments |
| 4 | Evaluation, CRPS decomposition, event analysis |
| 5 | Additional experiments (sensitivity tests, ablations) |
| 6 | Writing and defense preparation |

## Key References

- Klotz et al. (HESS, 2022) — CMAL vs MC Dropout vs GMM for LSTM uncertainty
- Kratzert et al. (HESS, 2019) — Entity-Aware LSTM
- Lakshminarayanan et al. (NeurIPS, 2017) — Deep ensembles
- Nearing et al. (Nature, 2024) — Global ML streamflow
- Lang et al. (2024) — AIFS-CRPS for ensemble weather
