# MSc Thesis: Generalizing ML Ensemble Streamflow Uncertainty to Monsoon Catchments

**Institution**: Tribhuvan University, Kathmandu
**Setting**: Nepali river basins (SAPPHIRE Flow v1)
**Duration**: 6-9 months (flexible for Nepali academic calendar)
**Supervisors**: [TU colleague], [lead developer as co-supervisor]

## Research Question

Do the findings from the Swiss uncertainty paradigm comparison (ETH thesis)
generalize to monsoon-dominated, data-sparse catchments in Nepal? Does ensemble
NWP propagation matter more or less when gauge density is low and flood
generation mechanisms are fundamentally different?

## Background

The companion ETH thesis compares three uncertainty paradigms for ML streamflow
forecasting (NWP ensemble propagation, learned distributions, deep ensembles)
in data-rich Swiss Alpine catchments. Nepal presents a contrasting setting:
monsoon-driven hydrology, sparse gauge networks, larger NWP ensemble (ECMWF IFS
ENS, 51 members), and operational stakes — Nepal DHM is the target user for
SAPPHIRE Flow v1.

If the Swiss findings hold across both settings, the result is robust. If they
diverge — e.g., ensemble propagation matters more in data-sparse Nepal — that
is an even more valuable finding for the global flood forecasting community.

## Method

Replicate the three-paradigm comparison from the ETH thesis using the SAPPHIRE
Flow codebase, adapted for Nepal:

- **Paradigm A**: Each of 51 ECMWF IFS ENS members forced through an LSTM
  trained on Nepali catchments
- **Paradigm B**: Deterministic NWP (IFS HRES) with CMAL distribution head
- **Paradigm C**: Deep ensemble of 5-10 LSTMs with deterministic NWP

Same base architecture and evaluation framework as the ETH thesis, enabling
direct cross-regime comparison.

## Data

- **Streamflow**: DHM Nepal hourly discharge at ~20-40 stations (subset with
  reliable records). Supplemented by iEasyHydroHF historical data if available.
- **Training weather**: ERA5-Land hourly reanalysis (0.1°) for historical
  period. DHM station observations where available.
- **Forecast NWP**: ECMWF IFS ENS (51 members, 15-day lead, 3-hourly to day 6,
  6-hourly to day 15) from the **dynamical.org persistent archive** — freely
  available in cloud-optimized Zarr from 2024-04-01 to present (~2 years). This
  eliminates the dependency on a custom archiving pipeline.
  Access: `https://data.dynamical.org/ecmwf/ifs-ens/forecast-15-day-0-25-degree/latest.zarr`
- **Catchment attributes**: From Nepal DHM GIS data, supplemented by global
  datasets (HydroATLAS, MERIT DEM)

## Evaluation

Same metrics as ETH thesis for comparability:
- CRPS and CRPS decomposition
- Spread-skill ratio
- Rank histograms
- Event-based analysis (monsoon flood events)

Additional Nepal-specific analyses:
- **Gauge density sensitivity**: How does paradigm ranking change when training
  gauges are artificially thinned (simulating sparser networks)?
- **Monsoon vs dry season**: Does the optimal paradigm differ by season?
- **Cross-regime transfer**: Can the Swiss-trained model transfer to Nepal with
  fine-tuning? Which paradigm transfers best?

## Expected Contributions

1. First test of ML uncertainty paradigms in a monsoon, data-sparse setting
2. Cross-regime robustness assessment (Alpine vs monsoon) when combined with
   ETH results — strengthens both theses
3. Practical guidance for Nepal DHM on uncertainty representation in SAPPHIRE
   Flow operational deployment
4. Capacity building: a Nepali researcher with hands-on ML hydrology experience

## Risks and Mitigations

- **Data quality**: Nepali gauge records have gaps and rating curve issues.
  Apply quality control filters; report results for clean vs full datasets.
- **Limited stations**: ~20-40 stations vs ~80 in Switzerland. Use
  multi-basin training (Kratzert et al., 2024 "never train on single basin")
  to maximize data value. Report as limitation.
- **NWP archive timing**: Largely mitigated by the dynamical.org IFS ENS
  archive (Apr 2024–present). No dependency on SAPPHIRE Flow v1 deployment
  for NWP data.
- **Compute access**: Provide access to project compute resources. LSTM
  training is modest (~hours on single GPU).

## Prerequisites (provided by supervisors or student, depending on level)

**ERA5-Land ↔ IFS ENS bias characterization**: Before thesis experiments begin,
compute systematic bias statistics between ERA5-Land (training data) and IFS
ENS forecasts (deployment data) over Nepal. This is a ~1 day engineering task
using the dynamical.org Zarr archive and ERA5-Land from CDS:

1. Open IFS ENS Zarr, subset to Nepal bounding box + key variables (precip,
   2m temp, humidity)
2. Regrid ERA5-Land (0.1°) to IFS ENS grid (0.25°) or vice versa
3. Compute per-variable: bias, RMSE, correlation stratified by lead time,
   season (monsoon/dry), and elevation band
4. Determine whether systematic bias correction (e.g., quantile mapping) is
   needed before training, or whether the LSTM can absorb the distribution
   shift

If the student has strong data engineering skills, this is a good onboarding
task (month 1). Otherwise, supervisors provide the bias stats as input.

## Timeline

Start ~6 months after ETH thesis begins (benefits from ETH codebase/lessons).

| Month | Activity |
|-------|----------|
| 1-2 | Literature review, Nepal data collection and QC, learn SAPPHIRE codebase. Bias characterization (ERA5-Land vs IFS ENS) if student-led. |
| 3 | Adapt ETH pipeline for Nepal data and ECMWF IFS ENS |
| 4 | Run three-paradigm comparison on Nepal catchments |
| 5-6 | Evaluation, cross-regime comparison with ETH results |
| 7 | Gauge density sensitivity and transfer experiments |
| 8-9 | Writing and defense preparation |

## Key References

- Same as ETH thesis, plus:
- Kratzert et al. (HESS, 2024) — "Never train on a single basin"
- Nearing et al. (Nature, 2024) — Global ML transfer to ungauged basins
- Hapuarachchi et al. (HESS, 2022) — Operational ensemble forecasting (BoM,
  methodological parallel)

## Joint Publication Potential

The two theses together form a strong journal paper: "Uncertainty paradigms for
ML ensemble streamflow forecasting: a cross-regime comparison from Alpine
Switzerland to monsoon Nepal." Target: HESS or WRR.
