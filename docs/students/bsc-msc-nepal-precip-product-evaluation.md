# BSc/MSc Thesis: Sub-Daily Precipitation Product Evaluation for Operational Flood Forecasting in Nepal

**Institution**: Tribhuvan University, Kathmandu  
**Setting**: Nepali catchments across the HKH elevation gradient (supports SAPPHIRE Flow v1 + SnowMapper)  
**Duration**: 3-6 months (BSc or MSc)  
**Supervisors**: [TU supervisor], with scientific co-mentoring from Dr. Joel Fiddes (hydrosolutions / SLF) and Dr. Beatrice Marti (hydrosolutions / ETH Zurich) via the BARHKH network  

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

Within the BARHKH initiative, two operational systems — SnowMapper (snow
forecasts) and SAPPHIRE Flow (streamflow forecasts) — consume sub-daily
ECMWF IFS ensemble precipitation and temperature forecasts over Nepal.
Both share the same NWP forcing pipeline; precipitation biases propagate
into both snow and streamflow forecasts. This thesis characterizes those
biases by elevation band, providing a common correction baseline.

Known challenges:
- ERA5-Land overestimates monsoon precipitation at high elevations in the
  Dudh Koshi / Everest region (Khadka et al., 2022)
- Satellite products systematically underestimate orographic precipitation
  due to the warm-rain blind spot (Kumar et al., 2021; Nepal et al., 2021)
- Only ~12 DHM stations exist above 3,000 m, constraining all high-altitude
  validation (WMO/SOFF, 2024)
- Strong diurnal cycles (afternoon convection + nocturnal mountain-valley
  circulation) are poorly timed by NWP (Fujinami et al., 2021)

## Products to Evaluate

| Product | Type | Resolution | Temporal | Why |
|---------|------|-----------|----------|-----|
| **ERA5-Land** | Reanalysis | 0.1° | Hourly | v1 training forcing; known Himalayan biases |
| **ECMWF IFS ENS** | NWP forecast | ~18 km | 1h to 90h, then 3-6h | v1 operational forcing; drives GloFAS Nepal |
| **DWD ICON Global** | NWP forecast | 13 km | Hourly | Free open-data NWP; independent of ECMWF |
| **NCEP GFS** | NWP forecast | 0.25° | Hourly | Most widely used global NWP; free |
| **GPM IMERG V07B Early** | Satellite NRT | 0.1° | 30 min | DHM's preferred nowcast product; best NRT in Nepal 2024 ranking |
| **MSWEP V3 NRT** | Merged ML | 0.1° | Hourly | Globally best KGE; no independent Nepal V3 validation yet |
| **CMORPH2** | Satellite NRT | 8 km | 30 min | New generation; no HKH validation |
| **APHRODITE** | Gauge-interpolated | 0.25° | Daily | Best gauge-based product for Nepal; 2001-2015 reference baseline |

Eight products covering reanalysis, NWP forecasts (3), satellite NRT,
merged, and gauge-interpolated categories.

**Excluded** (already well-characterized or low priority): HARv2 (historical
WRF downscaling, not operational), JRA-55 (retired), GPCC (monthly),
PERSIANN-CDR (ranked last for Nepal), GSMaP NRT (stretch goal).

## Method

Build on Nepal et al. (2024) rather than replicate it. Focus on sub-daily
resolution and products that are operationally relevant or newly available.

1. **Study domain**: Use all available DHM stations with sufficient record
   length across Nepal, spanning four elevation bands (<500 m, 500-1500 m,
   1500-3000 m, >3000 m). Nepal et al. (2024) used 159 stations at daily
   resolution; this study should aim for comparable or greater coverage.
   The sub-daily subset will be smaller — report station counts per
   elevation band transparently. Apply quality control following Kumar
   et al. (2021, Sec 2.2.1): flag physically implausible values, long dry
   spells during monsoon, and stuck-gauge readings.

2. **Product acquisition and harmonization**: Download products, regrid to
   common 0.1° grid, extract at gauge locations. Reanalysis and
   satellite/merged products are evaluated as quasi-observations at
   concurrent timesteps; NWP forecasts (IFS ENS, ICON, GFS) are evaluated
   by lead time — sub-daily metrics for Day 1-3, daily metrics for
   Day 1-10 — to characterise how forecast skill degrades with lead time.

3. **Daily verification** (baseline, comparable to Nepal et al.): Mean bias,
   RMSE, KGE, POD/FAR/CSI for threshold exceedance (10, 25, 50 mm/day).
   Stratified by elevation band, season (monsoon JJAS, westerly DJF,
   transitions MAM/ON), and — for NWP products — forecast lead time
   (Day 1, 2, 3, 5, 7, 10). Monthly/annual water balance to identify
   systematic volume biases that daily metrics can miss.

4. **Sub-daily verification**: Hourly bias, KGE, and event scores for
   products with sub-daily availability. Extreme indices: Rx1h, Rx3h,
   99th-percentile exceedance. For NWP forecasts, evaluate at Day 1, 2,
   and 3 lead times to quantify sub-daily skill decay.

5. **Diurnal timing analysis** (stretch goal): Peak-hour precipitation
   timing error by elevation band using circular statistics.

6. **Temperature evaluation** (stretch goal): ERA5-Land and IFS ENS 2m
   temperature lapse-rate bias by elevation band — directly affects
   SnowMapper's rain/snow partitioning.

7. **Elevation-band bias tables** (primary deliverable): Product × elevation
   band × season matrix of PBIAS, KGE, POD, and (where available) timing
   error. For NWP products, include a lead-time dimension (Day 1-3
   sub-daily, Day 1-10 daily). These tables feed directly into bias
   correction design for both SnowMapper and SAPPHIRE Flow.

All analysis in Python (xarray, pandas, scipy), version-controlled on
GitHub.

## Data

- **Ground truth**: DHM Nepal gauge records — daily and sub-daily where
  available. All stations with sufficient record length across four
  elevation bands. Access via DHM (formal request required; coordinate with
  BARHKH contacts).
- **ERA5-Land**: Copernicus CDS, free, hourly, 1950-present
- **IFS ENS**: dynamical.org Zarr archive (Apr 2024-present, free); TIGGE
  (2006-present, 0.5°, 6h) as fallback for longer record
- **IMERG V07B Early**: NASA GES DISC, free
- **MSWEP V3 NRT**: gloh2o.org (CC BY-NC 4.0 — non-commercial only)
- **CMORPH2**: NOAA CPC, free
- **APHRODITE**: RIHN/JMA, 1951-2015

## Scope Tiers and Risks

**Minimum viable thesis (BSc or MSc):**
- Daily verification of 3+ products against all available DHM stations
  across four elevation bands
- Elevation-band x season bias table (PBIAS, KGE, POD) as the core
  deliverable
- Sub-daily verification for one product (ERA5-Land hourly)

**Full scope (MSc, 6 months):**
- All eight products at daily and sub-daily resolution
- Monthly/annual water balance per elevation band
- APHRODITE overlap test (2001-2015) for training data guidance
- NWP Day 1-2 forecast verification for IFS ENS, ICON, and GFS

**Stretch goals:** Diurnal timing analysis; temperature evaluation; GSMaP NRT

**Key risks:**
- **DHM gauge data access**: Formal request required; not publicly
  available. Mitigate via BARHKH institutional contacts. Fallback:
  Caravan/GRDC public stations.
- **Sparse sub-daily records**: Daily coverage is good (159+ stations) but
  the hourly subset may be substantially smaller. Use daily fallback where
  hourly is unavailable.
- **Few stations above 3,000 m**: Only ~12 DHM stations. Report
  high-altitude results with appropriate uncertainty.
- **MSWEP V3 license**: CC BY-NC 4.0 restricts operational use — note
  constraint for deployment.

## Timeline

| Month | Activity |
|-------|----------|
| 1 | Literature review, station selection, data download and harmonization |
| 2 | Daily verification, elevation-band and seasonal stratification |
| 3 | Sub-daily verification |
| 4 | Diurnal timing, APHRODITE overlap test (if time permits) |
| 5 | Synthesis: compile elevation-band bias tables, draft results |
| 6 | Writing and defense preparation |

BSc (3 months): months 1-2 (daily) + month 3 (one sub-daily product),
producing the bias table as core deliverable.

## Key References

- Kumar, M. et al. (J. Hydrology, 599, 126252, 2021) — Hourly-to-daily 11-product evaluation in Sikkim; elevation-stratified bias, diurnal cycle. **Primary methodological template.** [doi:10.1016/j.jhydrol.2021.126252](https://doi.org/10.1016/j.jhydrol.2021.126252)
- Nepal, B. et al. (JGR-Atmospheres, 2024) — 11-product Nepal ranking, 159 DHM stations, daily resolution. [doi:10.1029/2024JD040759](https://doi.org/10.1029/2024JD040759)
- Nepal, B. et al. (Atmosphere, 2021) — IMERG and GSMaP extreme precipitation in Nepal. [doi:10.3390/atmos12020254](https://doi.org/10.3390/atmos12020254)
- Khadka, A. et al. (JAMC, 2022) — ERA5-Land bias at high elevation, Dudh Koshi. [doi:10.1175/JAMC-D-21-0091.1](https://doi.org/10.1175/JAMC-D-21-0091.1)
- Talchabhadel, R. et al. (Earth & Space Science, 2021) — Sub-daily IMERG vs hourly gauges, West Rapti 2014 flood. [doi:10.1029/2020EA001518](https://doi.org/10.1029/2020EA001518)
- Fujinami, H. et al. (JGR-Atmospheres, 2021) — Diurnal precipitation cycle in Nepal Himalayas. [doi:10.1029/2020JD034255](https://doi.org/10.1029/2020JD034255)
- Wang, X. et al. (arXiv, 2026) — MSWEP V3 technical description. [arxiv.org/abs/2602.01436](https://arxiv.org/abs/2602.01436)
- WMO/SOFF (2024) — Country Hydromet Diagnostics: Nepal. [PDF](https://www.un-soff.org/wp-content/uploads/2025/08/Nepal-Country-Hydromet-Diagonistics.pdf)

## Publication Potential

Standalone short paper or technical note (e.g., EGU NHESS Brief
Communication): "Sub-daily precipitation product evaluation across Nepal's
elevation gradient: implications for operational snowmelt and flood forecasting."
