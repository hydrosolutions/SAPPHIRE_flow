# Publicly Available Operational Precipitation Products for Nepal

**Purpose**: Survey of precipitation products relevant to operational flood
forecasting in Nepal. **Scope**: only products that are (a) publicly available without
restrictive licensing, (b) have short latency suitable for real-time or near-real-time
use, or (c) are NWP/AI weather forecast products providing precipitation forcing.
Historical-only and training-relevant products are covered briefly in Section 5.

**Last updated**: 2026-03-10

---

## Table of Contents

1. [Near-Real-Time Precipitation Estimates](#1-near-real-time-precipitation-estimates)
2. [NWP Precipitation Forecasts](#2-nwp-precipitation-forecasts)
3. [AI Weather Models with Precipitation](#3-ai-weather-models-with-precipitation)
4. [Operational Systems in Nepal](#4-operational-systems-in-nepal)
5. [Training and Reference Products](#5-training-and-reference-products)
6. [Challenges Specific to Nepal](#6-challenges-specific-to-nepal)
7. [Validation Summary](#7-validation-summary)
8. [References](#8-references)

---

## 1. Near-Real-Time Precipitation Estimates

Products with **latency <24 h** and **free public access**. These provide observed or
estimated precipitation for monitoring, data assimilation, or nowcasting.

### Quick-Reference Table

| Product | Agency | Res | Temporal | Latency | Public Access |
|---|---|---|---|---|---|
| **IMERG Early** | NASA | 0.1 deg | 30 min | 4 h | GES DISC, GEE |
| **IMERG Late** | NASA | 0.1 deg | 30 min | 14 h | GES DISC, GEE |
| **GSMaP NOW** | JAXA | 0.1 deg | Hourly | ~0 h | JAXA portal |
| **GSMaP NRT** | JAXA | 0.1 deg | Hourly | ~4 h | JAXA FTP, GEE |
| **PDIR-Now** | CHRS/UCI | 0.04 deg | Hourly | 15--60 min | CHRS portal |
| **CMORPH2** | NOAA/CPC | 8 km | 30 min | ~1 h | NOAA CPC |
| **MSWEP V3 NRT** | GloH2O | 0.1 deg | Hourly | ~2 h | gloh2o.org (CC BY-NC 4.0) |
| **CHIRPS v3 sat** | CHC/USGS | 0.05 deg | Daily | ~2 d | CHC FTP, GEE |

### 1.1 GPM IMERG Early/Late (V07B)

**Agency**: NASA Goddard Space Flight Center
**Method**: Multi-satellite passive microwave (PMW) constellation + geostationary IR.
GPM Core Observatory (GMI + DPR) anchors calibration. No gauge correction in
Early/Late runs.

- **Early** (4 h latency): Forward morphing only. Best operational compromise between
  timeliness and quality.
- **Late** (14 h): Forward+backward morphing. Better quality, still sub-daily.
- **Access**: NASA GES DISC (OpenDAP, HTTPS), Google Earth Engine
  (`NASA/GPM_L3/IMERG_V07`), AWS open data.

**Nepal validation** (279 stations, 2014--2019): correlation 0.52, bias -2.49 mm/day.
POD decreases with elevation; underestimation increases above ~3,000 m. V07 improved
over V06 for extreme and orographic precipitation. Spatial correlation ~0.75, PBIAS
<12% in Gandak River Basin.

**Limitations**: Underestimates orographic precipitation; warm-rain processes poorly
captured (shallow orographic clouds produce little ice for PMW scattering).

### 1.2 GSMaP NOW/NRT (V8)

**Agency**: JAXA
**Method**: Multi-satellite PMW + geostationary IR. No gauge correction in NOW/NRT.

- **NOW** (~0 h latency): Real-time, lowest accuracy.
- **NRT** (~4 h latency): Better quality, still fast enough for operational use.
- **Access**: JAXA Global Rainfall Watch portal (free registration), GEE
  (`JAXA/GPM_L3/GSMaP_v8_operational`).

**Nepal validation**: Overall correlation 0.79 but -55% bias. Flat terrain:
correlation >0.8, POD ~70%. High altitude: correlation ~0.4, POD ~40%.

**Limitations**: Severe underestimation at high elevations. NOW product has lowest
accuracy among all variants.

### 1.3 PDIR-Now

**Agency**: CHRS, UC Irvine
**Method**: IR-only (cloud-top temperature). Highest spatial resolution among NRT
products (0.04 deg ~4 km), lowest latency (15--60 min).

- **Access**: CHRS Data Portal (chrsdata.eng.uci.edu), free.

**Nepal validation**: Lower accuracy than IMERG and GSMaP. Cloud-top temperature
correlates poorly with surface rainfall in complex terrain.

**Limitations**: IR-only approach fundamentally limited for orographic precipitation.
Fastest product available but least accurate for Nepal.

### 1.4 CMORPH2

**Agency**: NOAA/CPC
**Method**: PMW retrievals + IR motion vectors + JPSS sensors + GFS model forecasts.
Second-generation product.

- **Specs**: 0.073 deg (~8 km), 30-min temporal, ~1 h latency. Refreshed every 30 min
  for first 3 hours.
- **Coverage**: Pole-to-pole (unlike v1 which was 60N--60S).
- **Access**: NOAA CPC, free. Reprocessing from 1991 underway.

**Nepal validation**: Relatively new, limited independent validation for Nepal.
Original CMORPH ranked below IMERG in Nepal comparisons.

**Limitations**: New product with limited HKH-specific validation. Original CMORPH
showed moderate performance in Nepal.

### 1.5 MSWEP V3 NRT

**Agency**: GloH2O (Hylke Beck et al.)
**Method**: ML-based fusion (XGBoost + Random Forest) of IMERG, GSMaP, PERSIANN,
PDIR-Now, ERA5, GDAS + static terrain predictors. Trained on >15,000 hourly + 57,000
daily + 86,000 monthly gauge stations.

- **Specs**: 0.1 deg, hourly, ~2 h latency.
- **Access**: gloh2o.org. **License: CC BY-NC 4.0** (free for non-commercial/academic
  use only).

**Nepal validation**: Mixed. Good in Arun River Basin. Globally best KGE (0.69) but
limited independent validation in Nepal for V3 (published Feb 2025).

**Limitations**: Non-commercial license restricts operational deployment. V3 very new.
Gauge sparsity at high altitude limits ML model performance.

### 1.6 CHIRPS v3 Preliminary (sat)

**Agency**: Climate Hazards Center, UC Santa Barbara / USGS FEWS NET
**Method**: IR cold-cloud-duration + station gauge data + CHPclim climatology.

- **Specs**: 0.05 deg (~5.5 km), daily, ~2 day latency.
- **Access**: CHC FTP, GEE (`UCSB-CHC/CHIRPS/V3/DAILY_SAT`), free.

**Nepal validation**: Spatial correlation 0.75, PBIAS <12% in Gandak Basin. Weaker for
daily extremes compared to IMERG.

**Limitations**: **Daily only** -- no sub-daily data, limiting utility for sub-daily
flood forecasting. 2-day latency is borderline for real-time use.

---

## 2. NWP Precipitation Forecasts

Publicly available ensemble and deterministic NWP products providing precipitation
forecasts suitable for driving hydrological models.

### Quick-Reference Table

| Model | Agency | Res | Members | Lead Time | Latency | Public Access |
|---|---|---|---|---|---|---|
| **IFS HRES** | ECMWF | 9 km | 1 | 10 d | ~7 h | Open Data (CC-BY-4.0) |
| **IFS ENS** | ECMWF | 9 km | 51 | 15 d | ~7 h | Open Data (CC-BY-4.0) |
| **AIFS ENS** | ECMWF | 31 km | 50 | 15 d | ~7 h | Open Data |
| **GFS** | NOAA | 28 km | 1 | 16 d | ~4 h | Free (NOMADS, AWS) |
| **GEFS v12** | NOAA | 25 km | 31 | 16 d | ~5 h | Free (NOMADS, AWS) |
| **MOGREPS-G** | UKMO | 20 km | 45 | ~8 d | ~6 h | AWS (30-day rolling) |
| **ICON** | DWD | 13 km | 1 | 7.5 d | ~5 h | Free (opendata.dwd.de) |
| **ICON-EPS** | DWD | 40 km | 40 | 7.5 d | ~6 h | Free (opendata.dwd.de) |

Products available via **TIGGE** (12-centre archive, 0.5 deg, 6 h, current phase
2024--2028): ECMWF, NCEP, UKMO, JMA (51 members, 27 km), CMA (15 members, 63 km),
DWD, NCMRWF, IMD, Meteo France, ECCC, KMA, CPTEC. Access via ECMWF CDS.

### 2.1 ECMWF IFS ENS

The primary NWP product for operational flood forecasting globally.

- **Resolution**: 9 km (Cycle 48r1, June 2023). 137 vertical levels.
- **Members**: 51 (1 control + 50 perturbed).
- **Lead time**: 15 days (00/12 UTC), 6 days (06/18 UTC).
- **Temporal steps**: Hourly to 90 h, 3-hourly to 144 h, 6-hourly to 360 h.
- **Precipitation variables**: Total precipitation (tp), convective (cp), large-scale
  (lsp), precipitation type.
- **Access**: Full Real-time Catalogue open under CC-BY-4.0 since Oct 2025. Python:
  `ecmwf-opendata` package. Cloud mirrors on AWS, Azure, GCP. Open-Meteo API for
  simplified access.

**Nepal relevance**: Precipitation skill improved 2--6% with 9 km upgrade. Substantial
reduction in unrealistic precipitation extremes over orography. Consistently
outperforms other centres in TIGGE South Asian monsoon evaluations. Drives GloFAS
flood forecasts for Nepal.

### 2.2 ECMWF AIFS ENS

AI-based ensemble forecast, operational since 1 Jul 2025.

- **Resolution**: 31 km (coarser than IFS ENS).
- **Members**: 50. Run at 6-hour intervals.
- **Precipitation**: Total precipitation. V1.1.0 (Aug 2025) fixed bias; up to 12%
  improvement in short-range precipitation skill.
- **Access**: Available alongside IFS in ECMWF open data, same pipeline.

**Limitations**: Known spurious trace precipitation in arid regions. Lower resolution
than IFS ENS. Relatively new.

### 2.3 NOAA GFS / GEFS v12

Freely available US global models.

- **GFS**: 0.25 deg (~28 km), deterministic, 16-day lead, 4 runs/day.
- **GEFS v12**: 31 members, ~25 km (C384L64), 16-day lead (35 days at 00 UTC), 4
  runs/day.
- **Access**: NOMADS, AWS Open Data, Azure. Fully free, no registration.

**Nepal relevance**: Lower skill than ECMWF for South Asian monsoon but freely
available with very simple access. Viable backup or multi-model ensemble component.

### 2.4 UKMO MOGREPS-G

- **Members**: 44 perturbed + 1 control.
- **Resolution**: 20 km.
- **Lead time**: ~8 days (hourly to 54 h, 3-hourly to 198 h).
- **Access**: AWS Open Data (30-day rolling archive, free).

### 2.5 DWD ICON / ICON-EPS

- **ICON (deterministic)**: 13 km, 90 vertical levels, 7.5-day lead.
- **ICON-EPS**: 40 members, ~40 km globally (refined to 20 km over Europe only --
  Nepal gets 40 km).
- **Access**: Free via opendata.dwd.de.

**Limitation**: No regional nest over South Asia. 40 km ensemble resolution is coarse
for Nepal terrain.

### 2.6 Regional Models Covering Nepal

**HIWAT (WRF at DHM)**: NASA SPoRT WRF configuration driven by GFS. Municipality-
level resolution for Nepal. 54-hour lead time, hourly output. Daily at 13:00 UTC
during March--September. SERVIR SOCRATES platform. **Access**: DHM/ICIMOD;
availability to external users unclear.

**NCMRWF NCUM-R**: 4.4 km convection-permitting model covering 62--106 E, up to 41 N
(includes Nepal). 3-day lead time, deterministic only. **Access**: Limited; primarily
for Indian met agencies.

### 2.7 Extended Range

- **ECMWF S2S**: 46-day lead, 51 members, twice weekly. Demonstrated skill for South
  Asian monsoon onset prediction. Access via Copernicus CDS.
- **ECMWF SEAS5**: 51 members, ~36 km, 7-month seasonal forecasts. Known
  overestimation of precipitation over Himalayan orography.

---

## 3. AI Weather Models with Precipitation

Emerging products. Most are operationally available via ECMWF or open-source, but
validation for South Asian monsoon precipitation is limited.

| Model | Agency | Members | Precip | Resolution | Public Access | Monsoon Validation |
|---|---|---|---|---|---|---|
| **AIFS** | ECMWF | 50 | Yes | 31 km | ECMWF open data | Best AI model (MAUSAM 2025) |
| **GenCast** | DeepMind | 50+ | Yes | 0.25 deg | Open-source (GitHub) | Strong skill |
| **GraphCast** | DeepMind | 1 | Auxiliary | 0.25 deg | Open-source | Reasonable 1--3 d; 20--35% underest. of 99th-pct |
| **FourCastNet** | NVIDIA | 1 | Yes | 0.25 deg | Open-source | Larger regional errors |
| **Pangu-Weather** | Huawei | 1 | No | 0.25 deg | Open-source | N/A (no precip output) |

### MAUSAM Study (2025) -- Key Finding

Comprehensive evaluation of 7 AI models during South Asian Monsoon:

- Forecast errors **15--45% larger** when verified against ground observations vs
  reanalysis -- reanalysis-centric benchmarks overstate skill.
- All models **underpredict extreme precipitation**.
- **AIFS, GraphCast, GenCast** show smallest systematic biases.
- None validated as standalone replacements for physics-based NWP over South Asia.

### Practical Availability

- **AIFS ENS** is the most accessible: operational at ECMWF, same data pipeline as IFS
  ENS, minimal additional engineering to ingest both.
- **GenCast** is open-source and runnable locally but not operationally hosted.
- All others require self-hosting inference.
- Open-Meteo and WeatherAPI services expose some AI model outputs via simple REST APIs.

---

## 4. Operational Systems in Nepal

### 4.1 Nepal DHM Flood Early Warning

- **Real-time telemetric data**: 286 met + 170 hydro stations, 5-min transmission
  during monsoon. Rainfall threshold-based warnings + water level monitoring.
- **HIWAT**: WRF-based 54-hour probabilistic precipitation forecasts (March--Sept).
  Ingests GFS + GPM satellite data.
- **ECMWF-SPT**: 15-day ensemble streamflow predictions using IFS ENS runoff.
- **GloFAS**: 30-day probabilistic flood forecasts via ECMWF IFS ENS driving
  HTESSEL+LISFLOOD. Freely accessible.
- **Toll-free 1155 hotline**; online at dhm.gov.np.

### 4.2 ICIMOD / SERVIR-HKH

- **Flash Flood Prediction Tool**: HIWAT precipitation into RAPID model, 54-hour
  forecasts for 12,428 river segments in Nepal.
- **Streamflow Prediction Tool**: ECMWF ENS runoff into RAPID, 10-day forecasts for
  519 river segments.
- SERVIR-HKH concluded Jan 2025; tools remain operational.

### 4.3 GloFAS

ECMWF Global Flood Awareness System. IFS ENS (51 members, 9 km) drives
HTESSEL+LISFLOOD at 0.1 deg. 15-day flood probability for all major Nepal rivers.
30-day seasonal outlooks via SEAS5. Freely accessible; operational since 2011.

---

## 5. Training and Reference Products

Products unsuitable for real-time operations due to high latency, discontinued
production, or restricted access, but valuable for model training, calibration, and
validation.

| Product | Agency | Res | Temporal | Record | Latency | Role |
|---|---|---|---|---|---|---|
| **IMERG Final** | NASA | 0.1 deg | 30 min | 2000-- | 3.5 mo | Best-quality satellite precip for training |
| **ERA5-Land** | ECMWF/C3S | 0.1 deg | Hourly | 1950-- | ~5 d | Reanalysis forcing for model training |
| **ERA5** | ECMWF/C3S | 0.25 deg | Hourly | 1940-- | ~5 d | Reanalysis; drives GloFAS reanalysis |
| **APHRODITE** | RIHN/JMA | 0.25 deg | Daily | 1951--2015 | N/A | Best gauge-based product for Nepal |
| **GSMaP Gauge** | JAXA | 0.1 deg | Hourly | 2000-- | ~3 d | Gauge-corrected satellite precip |
| **CHIRPS v3 rnl** | CHC/USGS | 0.05 deg | Daily | 1981-- | ~3 wk | Long-record gauge+satellite merge |
| **CPC Gauge** | NOAA | 0.5 deg | Daily | 1979-- | ~2 d | Gauge-only gridded analysis |
| **PERSIANN-CDR** | CHRS/UCI | 0.25 deg | Daily | 1983-- | ~2 mo | Long climate-quality record |
| **SM2RAIN-ASCAT** | IRPI-CNR | 0.1 deg | Daily | 2007--2022 | Annual | Soil-moisture-based; ended 2022 |
| **GEFS Reforecast** | NOAA | 25 km | 3-hourly | 2000--2019 | N/A | 5--11 members; NWP hindcast for training |
| **DHM gauges** | Nepal DHM | Point | Daily/5-min | ~1960s-- | RT (telemetric) | Ground truth; purchase required |

**Key notes**:
- **ERA5-Land** is the planned training data source for SAPPHIRE Flow v1.
- **APHRODITE** is the best-validated gauge product for Nepal but ends 2015 and is
  daily only.
- **IMERG Final** has gauge calibration and is the best satellite product, but 3.5-month
  latency makes it training-only.
- **DHM gauge data** is not publicly available (purchase + formal request required).

---

## 6. Challenges Specific to Nepal

### 6.1 Orographic Effects

Nepal spans ~60 m (Terai) to 8,849 m (Everest) over ~200 km horizontal distance.
Extreme precipitation gradients that 0.1 deg pixels (~10 km) cannot resolve. All
satellite products systematically underestimate orographic precipitation.

### 6.2 Warm/Shallow Orographic Rain

PMW algorithms rely on ice scattering signatures aloft. Warm orographic rain from
shallow clouds (common on windward slopes during monsoon) produces little ice --
severe under-detection by GPM, GSMaP, CMORPH.

### 6.3 Convective vs Stratiform

Monsoon: intense convective storms (well-detected) + widespread stratiform rain
(poorly detected). Winter western disturbances: primarily stratiform (worst satellite
performance).

### 6.4 Snowfall and Winter Precipitation

PMW retrievals over snow-covered surfaces have high uncertainty. Satellite products
consistently worst during winter. Snowfall estimation extremely poor.

### 6.5 Gauge Network Sparsity

Only ~12 DHM stations above 3,000 m. Limits gauge-calibrated products and all
validation studies. Wind undercatch adds uncertainty. WMO maturity score: 2/5.

### 6.6 Diurnal Cycle

Strong afternoon convection + nocturnal mountain-valley circulations. Daily products
miss this signal. NWP models struggle with nocturnal Himalayan precipitation timing.

### 6.7 NWP Resolution

Global NWP at >10 km cannot resolve valley-scale circulations. Even WRF at 4 km
struggles with nocturnal precipitation over Himalayan slopes. ECMWF's 9 km upgrade
reduced unrealistic extremes over orography but does not resolve individual valleys.

---

## 7. Validation Summary

### Comprehensive Ranking (Nepal 2024, JGR-Atmospheres)

11 products evaluated against 159 DHM gauge stations, 2001--2020. Ranking for
extremes:

**APHRODITE > MSWEP > TPMFD > HAR > ERA5-Land > IMERG V07 > CLDAS > IMERG V06 >
CHIRPS > CMORPH > PERSIANN**

Note: top-ranked products (APHRODITE, MSWEP) are not NRT-suitable. Among NRT
products, **IMERG V07** ranks best.

### Performance by Region

| Region | Elevation | Best NRT Product | Notes |
|---|---|---|---|
| **Terai** | <500 m | Most adequate | IMERG, GSMaP both reasonable |
| **Middle Hills** | 1,000--2,000 m | IMERG | Highest detectability, lowest errors |
| **High Himalaya** | >3,000 m | All struggle | Satellites underestimate; sparse validation |

### Seasonal Performance

| Season | Performance | Notes |
|---|---|---|
| **Monsoon (JJAS)** | Best | Highest detectability, lowest errors |
| **Pre-monsoon (MAM)** | Good | Convective, well-detected |
| **Post-monsoon** | Moderate | -- |
| **Winter (NDJF)** | Worst | Overestimation, high false alarm rates |

### NWP Precipitation Skill by Lead Time (South Asia)

| Lead Time | Useful For | Notes |
|---|---|---|
| **Day 1--3** | Deterministic + ensemble | IFS HRES, WRF show useful skill |
| **Day 3--7** | Ensemble essential | ECMWF ENS outperforms others; probability of exceedance useful |
| **Day 7--15** | Large-scale patterns | Ensemble mean useful; individual-event skill drops |
| **2--4 weeks** | Monsoon phase only | MJO-related active/break signals provide some predictability |

---

## 8. References

Dao, V., Arellano, C.J., Nguyen, P., Almutlaq, F., Hsu, K., and Sorooshian, S. (2025). Bias Correction of Satellite Precipitation Estimation Using Deep Neural Networks and Topographic Information Over the Western U.S. *JGR-Atmospheres*. [doi:10.1029/2024JD042181](https://doi.org/10.1029/2024JD042181)

Davids, J.C. et al. (2019). Soda Bottle Science -- Citizen Science Monsoon Precipitation Monitoring in Nepal. *Frontiers in Earth Science*, 7, 46. [doi:10.3389/feart.2019.00046](https://doi.org/10.3389/feart.2019.00046)

Girona-Mata, M., Orr, A., Widmann, M., Bannister, D., Dars, G.H., Hosking, S., Norris, J., Ocio, D., Phillips, T., Steiner, J., and Turner, R.E. (2025). Probabilistic Precipitation Downscaling for Ungauged Mountain Sites: A Pilot Study for the Hindu Kush Himalaya. *HESS*, 29, 3073--3100. [doi:10.5194/hess-29-3073-2025](https://doi.org/10.5194/hess-29-3073-2025)

Gupta, A., Sheshadri, A., and Suri, D. (2025). MAUSAM: An Observations-Focused Assessment of Global AI Weather Prediction Models During the South Asian Monsoon. *arXiv*, 2509.01879. [arxiv.org/abs/2509.01879](https://arxiv.org/abs/2509.01879)

Khadka, A., Wagnon, P., Brun, F., Shrestha, D., Lejeune, Y., and Arnaud, Y. (2022). Evaluation of ERA5-Land and HARv2 Reanalysis Data at High Elevation in the Upper Dudh Koshi Basin (Everest Region, Nepal). *J. Appl. Meteor. Climatol.*, 61(8), 931--954. [doi:10.1175/JAMC-D-21-0091.1](https://doi.org/10.1175/JAMC-D-21-0091.1)

Lang, S. and Magnusson, L. (2025). AIFS ENS Becomes Operational. *ECMWF Newsletter* 185. [ecmwf.int](https://www.ecmwf.int/en/newsletter/185/earth-system-science/aifs-ens-becomes-operational)

Mishra, B., Panthi, S., Ghimire, B.R., Poudel, S., Maharjan, B., and Mishra, Y. (2023). Gridded Precipitation Products on the Hindu Kush-Himalaya: Performance and Accuracy of Seven Precipitation Products. *PLOS Water*, 2(8), e0000145. [doi:10.1371/journal.pwat.0000145](https://doi.org/10.1371/journal.pwat.0000145)

Nepal, B. et al. (2024). Assessing Multi-Source Precipitation Estimates in Nepal. *JGR-Atmospheres*, 129, e2024JD040759. [doi:10.1029/2024JD040759](https://doi.org/10.1029/2024JD040759)

Nepal, B., Shrestha, D., Sharma, S., Shrestha, M.S., Aryal, D., and Shrestha, N. (2021). Assessment of GPM-Era Satellite Products' (IMERG and GSMaP) Ability to Detect Precipitation Extremes over Mountainous Country Nepal. *Atmosphere*, 12(2), 254. [doi:10.3390/atmos12020254](https://doi.org/10.3390/atmos12020254)

Price, I., Sanchez-Gonzalez, A., Alet, F., et al. (2025). Probabilistic Weather Forecasting with Machine Learning. *Nature*, 637, 84--90. [doi:10.1038/s41586-024-08252-9](https://doi.org/10.1038/s41586-024-08252-9)

Sharma, S., Chen, Y., Zhou, X., Yang, K., Li, X., Niu, X., Hu, X., and Khadka, N. (2020). Evaluation of GPM-Era Satellite Precipitation Products on the Southern Slopes of the Central Himalayas Against Rain Gauge Data. *Remote Sensing*, 12(11), 1836. [doi:10.3390/rs12111836](https://doi.org/10.3390/rs12111836)

Wang, X. et al. (2025). MSWEP V3: Machine Learning-Powered Global Precipitation Estimates at 0.1° Hourly Resolution (1979--Present). *arXiv*, 2602.01436. [arxiv.org/abs/2602.01436](https://arxiv.org/abs/2602.01436)

WMO/SOFF (2024). Country Hydromet Diagnostics: Nepal. [library.wmo.int](https://library.wmo.int/records/item/57493-country-hydromet-diagnostics) | [PDF](https://www.un-soff.org/wp-content/uploads/2025/08/Nepal-Country-Hydromet-Diagonistics.pdf)
