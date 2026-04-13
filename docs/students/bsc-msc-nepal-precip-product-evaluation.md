# BSc/MSc Thesis: Sub-Daily Precipitation Product Evaluation for Operational Flood Forecasting in Nepal

**Institution**: Tribhuvan University, Kathmandu  
**Setting**: Nepali catchments across the HKH elevation gradient (supports SAPPHIRE Flow v1 + SnowMapper)  
**Duration**: 3-6 months (BSc or MSc)  
**Supervisors**: [TU supervisor], with scientific co-mentoring from the BARHKH network [SnowMapper developer & SAPPHIRE Flow developer contacts]  

## Research Question

Which publicly available gridded precipitation products most reliably
represent sub-daily precipitation across Nepal's extreme elevation gradient
(60 m to >5,000 m) — and where do they fail badly enough to require bias
correction before use as forcing for operational flood forecasting models?

Nepal et al. (JGR-Atmospheres, 2024) ranked 11 products against 159 DHM
stations at daily resolution. This thesis extends that work into two
dimensions that remain unexplored for Nepal: **systematic sub-daily
(hourly)** evaluation across the full elevation gradient, and **quantitative
elevation-band bias tables** for the products that matter most to
operational systems. Kumar et al. (J. Hydrology, 2021) performed exactly
this kind of analysis — 11 products at hourly-to-daily resolution, with
elevation-stratified bias and diurnal cycle characterization — but for
Sikkim (Eastern India, 27 gauges). Within Nepal, prior sub-daily work is
limited to a single-event case study (Talchabhadel et al., 2021; 9
stations, one basin) and a single high-altitude process study (Fujinami
et al., 2021). This thesis applies the Kumar et al. methodology to Nepal
proper, with newer products (IMERG V07B, MSWEP V3, CMORPH2) and an
operationally motivated product selection.

## Background

Within the BARHKH initiative, two operational systems consume sub-daily ECMWF IFS ensemble precipitation and temperature forecasts over Nepal:

- **SnowMapper** produces spatially distributed snow water equivalent (SWE), snow height (HS), 
  and snowmelt forecasts across Nepal's elevation gradient, using NWP forcing
  downscaled to high-resolution topography. Precipitation phase (rain vs snow) and amount at high elevations are
  critical inputs — biases here propagate directly into SWE and melt timing.
- **SAPPHIRE Flow** is an operational hydrological
  forecasting system. It
  trains ML streamflow models on ERA5-Land reanalysis and deploys them with
  ECMWF IFS ENS forecasts. In v1, SAPPHIRE Flow ingests SnowMapper's SWE
  and snowmelt output as additional forcing inputs for monsoon-season and
  spring runoff prediction in snow-influenced catchments.

Both systems share the same NWP forcing pipeline: ECMWF IFS ENS forecasts
arrive via the Sapphire Data Gateway, pre-extracted at basin level.
Precipitation biases in this shared forcing propagate into both snow
forecasts (SnowMapper) and streamflow forecasts (SAPPHIRE Flow). This
thesis characterizes those biases by elevation band, providing a common
correction baseline for both systems.

Known challenges:
- ERA5-Land shows substantial overestimation of monsoon precipitation at
  high elevations in the Dudh Koshi / Everest region (Khadka et al., 2022)
- All satellite products systematically underestimate orographic
  precipitation due to the warm-rain blind spot — shallow orographic clouds
  produce little ice for PMW scattering (Kumar et al., 2021; Nepal et al.,
  2021)
- Only ~12 DHM stations exist above 3,000 m, constraining all validation
  (WMO/SOFF, 2024)
- Strong diurnal cycles (afternoon convection + nocturnal mountain-valley
  circulation) are missed by daily products and poorly timed by NWP
  (Fujinami et al., 2021; Kumar et al., 2021)

## Method

Build on Nepal et al. (2024) rather than replicate it. Focus on sub-daily
resolution and products that are operationally relevant or newly available.

1. **Study domain**: Select ~20-40 DHM stations spanning four elevation
   bands (<500 m, 500-1500 m, 1500-3000 m, >3000 m), prioritizing stations
   with sub-daily records. Apply quality control following Kumar et al.
   (2021, Sec 2.2.1): flag physically implausible values, long dry spells
   during monsoon, and stuck-gauge readings. Report the fraction of data
   retained per elevation band.

2. **Product acquisition and harmonization**: Download products, regrid to
   common 0.1° grid, extract at gauge locations. Note the distinction
   between product types: reanalysis (ERA5-Land, HARv2) and satellite/merged
   products (IMERG, CMORPH2, MSWEP) are evaluated as quasi-observations at
   concurrent timesteps; IFS ENS is evaluated as a short-range forecast
   (Day 1-2 accumulations vs gauge), representing best-case NWP rather than
   an observation substitute. This follows Kumar et al. (2021), who included
   WRF model output alongside observational products.

3. **Daily verification** (baseline, comparable to Nepal et al.): Mean bias,
   RMSE, KGE, POD/FAR/CSI for threshold exceedance. Stratified by elevation
   band and season (monsoon JJAS, westerly DJF, transitions MAM/ON).

   **Monthly/annual water balance**: Aggregate daily precipitation to monthly
   and annual totals per station. Compare product totals against gauge totals
   to identify systematic volume biases. A product can score well on daily
   KGE but still get the annual water balance wrong — critical for both
   SnowMapper (total snowfall volume at high elevations) and SAPPHIRE Flow
   (runoff volume closure).

4. **Sub-daily verification**: Hourly bias, KGE, and event scores
   for products with sub-daily availability (IMERG 30-min, ERA5-Land hourly,
   IFS ENS hourly to 90 h, CMORPH2 30-min, MSWEP V3 hourly). Extreme
   indices: Rx1h, Rx3h, 99th-percentile exceedance.

5. **Diurnal timing analysis**: Characterize peak-hour precipitation
   timing error by elevation band. Compare observed diurnal cycle against
   each product's diurnal cycle using circular statistics.

6. **Temperature evaluation** (stretch goal — SnowMapper relevance):
   Evaluate 2 m temperature from ERA5-Land and IFS ENS against DHM stations,
   stratified by elevation band. Temperature lapse-rate bias directly affects
   SnowMapper's rain/snow partitioning and melt timing at high elevations.

7. **Elevation-band bias tables** (primary deliverable): Product × elevation
   band × season matrix of PBIAS, KGE, POD, timing error for precipitation;
   temperature bias and lapse-rate error for reanalysis/NWP products. These
   tables feed directly into bias correction design for both SnowMapper and
   SAPPHIRE Flow.

All analysis in Python (xarray, pandas, scipy), version-controlled on
GitHub.

## Products to Evaluate

**High priority — operational forcing for SAPPHIRE Flow v1:**

| Product | Type | Resolution | Temporal | Why |
|---------|------|-----------|----------|-----|
| **ERA5-Land** | Reanalysis | 0.1° | Hourly | v1 training forcing; known Alpine/Himalayan biases |
| **ECMWF IFS ENS** | NWP forecast | 9 km | Hourly-6h | v1 operational forcing; drives GloFAS Nepal |
| **GPM IMERG V07B Early** | Satellite NRT | 0.1° | 30 min | DHM's preferred nowcast product; best NRT in Nepal 2024 ranking |

**High priority — strong candidates or newly available:**

| Product | Type | Resolution | Temporal | Why |
|---------|------|-----------|----------|-----|
| **HARv2** | Regional reanalysis | 10 km | Hourly | Ranks 4th for Nepal (above ERA5-Land); designed for High Asia |
| **MSWEP V3 NRT** | Merged ML | 0.1° | Hourly | Globally best KGE; no independent Nepal V3 validation yet |
| **CMORPH2** | Satellite NRT | 8 km | 30 min | New generation; no HKH validation; pole-to-pole coverage |

**Reference (daily baseline):**

| Product | Type | Resolution | Temporal | Why |
|---------|------|-----------|----------|-----|
| **APHRODITE** | Gauge-interpolated | 0.25° | Daily | Best gauge-based product for Nepal; benchmark for 2001-2015 overlap |

Seven products total — manageable scope for 3-6 months while covering
reanalysis, NWP forecast, satellite NRT, merged, and gauge-interpolated
categories.

**Excluded** (already well-characterized or low priority): JRA-55 (retired),
GPCC (monthly), PERSIANN-CDR (ranked last for Nepal), GSMaP NRT (if scope
is tight — add as stretch goal given DHM also uses it).

## Data

- **Ground truth**: DHM Nepal gauge records — daily and sub-daily where
  available. ~20-40 stations across four elevation bands. Access via DHM
  (formal request required; coordinate with SAPPHIRE Flow / BARHKH contacts).
- **ERA5-Land**: Copernicus Climate Data Store (CDS), free, hourly, 1950-present
- **IFS ENS**: dynamical.org persistent Zarr archive (Apr 2024-present,
  free, cloud-optimized) — covers 2 full monsoon seasons. Access:
  `https://data.dynamical.org/ecmwf/ifs-ens/forecast-15-day-0-25-degree/latest.zarr`.
  TIGGE archive (2006-present, 0.5°, 6 h, free for research via ECMWF CDS)
  available as fallback for longer record at coarser resolution.
- **IMERG V07B Early**: NASA GES DISC, free
- **HARv2**: Published dataset (Wang et al., Int. J. Climatol., 2021),
  1980-2020 (check for updates); evaluated over the ERA5-Land/IMERG overlap
  period, not the IFS ENS period
- **MSWEP V3 NRT**: gloh2o.org (CC BY-NC 4.0 — non-commercial only)
- **CMORPH2**: NOAA CPC, free
- **APHRODITE**: RIHN/JMA, 1951-2015

## Evaluation

Core metrics (consistent with Nepal et al., 2024 for comparability):
- Mean bias, RMSE, Pearson and Spearman correlations, KGE
- Event-based: POD, FAR, CSI for threshold exceedance (10, 25, 50 mm/day)
- Extreme precipitation indices: Rx1h, Rx3h, Rx1day, 99th-percentile

Additional analyses:
- **Elevation-band stratification**: <500 m, 500-1500 m, 1500-3000 m, >3000 m
- **Seasonal stratification**: Monsoon (JJAS), westerly (DJF), transitions
  (MAM, ON)
- **Monthly/annual water balance**: Product-vs-gauge annual precipitation
  totals per elevation band. Identifies systematic volume biases that daily
  metrics can miss.
- **Diurnal cycle**: Peak-hour error using circular mean and circular
  dispersion per elevation band
- **APHRODITE overlap test**: Which NRT/reanalysis product best reproduces
  APHRODITE's station-dense climatology? Uses the 2001-2015 overlap period
  (where ERA5-Land, HARv2, and IMERG all coincide with APHRODITE), separate
  from the sub-daily / IFS ENS evaluation period. Informs post-2015
  training data choice.

## Expected Contributions

1. Systematic multi-station, multi-product sub-daily precipitation
   evaluation for Nepal — extending Nepal et al. (2024) from daily to
   hourly resolution and applying the Kumar et al. (2021) Sikkim
   methodology to Nepal proper with current-generation products
2. Quantitative elevation-band bias tables (product × elevation × season)
   for precipitation and temperature — directly usable for bias correction
   in both SnowMapper (rain/snow partitioning, melt timing) and SAPPHIRE
   Flow v1 (streamflow forcing)
3. First independent Nepal validation of MSWEP V3 NRT and CMORPH2
4. Diurnal timing error characterization by elevation band — extending
   Fujinami et al. (2021) from process understanding to operational product
   evaluation
5. Practical recommendation: which product to use as ERA5-Land substitute
   or bias-correction target for post-2015 training data (APHRODITE overlap
   analysis)

## Risks and Mitigations

- **DHM gauge data access**: Formal request required; not publicly available.
  Mitigate via BARHKH/SAPPHIRE Flow institutional contacts. Fallback: use
  publicly available stations from Caravan/GRDC (fewer, but sufficient for
  proof of concept).
- **Sparse sub-daily gauge records**: Many DHM stations transmit at 5-min
  intervals during monsoon but historical sub-daily archives may be
  incomplete. Report station count per elevation band transparently; use
  daily fallback where hourly is unavailable.
- **Few stations above 3,000 m**: Only ~12 DHM stations. Report high-altitude
  results with appropriate uncertainty; do not over-interpret small samples.
- **MSWEP V3 license**: CC BY-NC 4.0 restricts operational use. Evaluate
  scientifically; note license constraint for operational deployment.
- **Scope creep**: Seven products × four elevation bands × four seasons ×
  daily + sub-daily = many combinations. Prioritize the elevation-band bias
  table as the minimum viable deliverable; treat diurnal timing and APHRODITE
  overlap as stretch goals.

## Timeline

| Month | Activity |
|-------|----------|
| 1 | Literature review (start from Nepal et al., 2024), station selection, data download and harmonization |
| 2 | Daily verification (baseline), elevation-band and seasonal stratification |
| 3 | Sub-daily verification (IMERG, ERA5-Land, CMORPH2, MSWEP V3, IFS ENS) |
| 4 | Diurnal timing analysis, APHRODITE overlap test (if time permits) |
| 5 | Synthesis: compile elevation-band bias tables, draft results |
| 6 | Writing and defense preparation |

For a BSc thesis (3 months): focus on months 1-2 (daily) + month 3 (one
sub-daily product, e.g., ERA5-Land hourly), producing the elevation-band
bias table as the core deliverable.

## Key References

- Kumar, M. et al. (J. Hydrology, 599, 126252, 2021) — Hourly-to-daily 11-product evaluation in Sikkim (27 gauges, 290-4000 m); elevation-stratified bias, diurnal cycle, monthly water balance. **Primary methodological template.** [doi:10.1016/j.jhydrol.2021.126252](https://doi.org/10.1016/j.jhydrol.2021.126252)
- Nepal, B. et al. (JGR-Atmospheres, 2024) — 11-product Nepal ranking against 159 DHM stations; daily resolution baseline. [doi:10.1029/2024JD040759](https://doi.org/10.1029/2024JD040759)
- Nepal, B. et al. (Atmosphere, 2021) — IMERG and GSMaP extreme precipitation detection in Nepal; 279 stations. [doi:10.3390/atmos12020254](https://doi.org/10.3390/atmos12020254)
- Khadka, A. et al. (JAMC, 2022) — ERA5-Land vs HARv2 at high elevation in Dudh Koshi / Everest region. [doi:10.1175/JAMC-D-21-0091.1](https://doi.org/10.1175/JAMC-D-21-0091.1)
- Talchabhadel, R. et al. (Earth & Space Science, 2021) — Satellite-based extreme precipitation assessment during Aug 2014 flood, West Rapti Basin, Nepal; uses sub-daily IMERG against hourly gauges. [doi:10.1029/2020EA001518](https://doi.org/10.1029/2020EA001518)
- Fujinami, H. et al. (JGR-Atmospheres, 2021) — Twice-daily monsoon precipitation maxima in Nepal Himalayas; diurnal cycle process study. [doi:10.1029/2020JD034255](https://doi.org/10.1029/2020JD034255)
- Wang, X. et al. (Int. J. Climatol., 2021) — HARv2 regional reanalysis for High Mountain Asia. [doi:10.1002/joc.6686](https://doi.org/10.1002/joc.6686)
- Wang, X. et al. (arXiv, 2026) — MSWEP V3 technical description. [arxiv.org/abs/2602.01436](https://arxiv.org/abs/2602.01436)
- WMO/SOFF (2024) — Country Hydromet Diagnostics: Nepal (gauge network maturity score 2/5). [PDF](https://www.un-soff.org/wp-content/uploads/2025/08/Nepal-Country-Hydromet-Diagonistics.pdf)

## Relationship to Companion Projects

This thesis provides **upstream data quality assurance** for two operational
systems and two companion MSc theses:

- **SnowMapper**: Consumes the same IFS ENS forcing, downscaled to high-
  resolution topography. The elevation-band precipitation and temperature
  bias tables directly inform SnowMapper's bias correction for SWE and
  snowmelt.
- **SAPPHIRE Flow v1**: Uses ERA5-Land for training and IFS ENS for
  operational forecasts. The bias tables feed into the NWP post-processing
  module.
- **Companion MSc theses** (ETH Zurich, Tribhuvan University): Compare
  uncertainty paradigms for ML ensemble streamflow forecasting in
  Switzerland and Nepal. Both depend on the forcing products evaluated here.

Together, the projects cover the full pipeline: input data quality (this
thesis) → snow modelling (SnowMapper) → streamflow uncertainty (companion
theses).

## Publication Potential

Standalone short paper or technical note (e.g., EGU NHESS Brief
Communication): "Sub-daily precipitation product evaluation across Nepal's
elevation gradient: implications for operational snowmelt and flood forecasting."

