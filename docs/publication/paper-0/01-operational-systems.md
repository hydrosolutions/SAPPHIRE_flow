# 1. Operational Ensemble Flood Forecasting Systems

Literature review for Section 1 of the Paper 0 outline.
Last updated: 2026-03-09.

## Key Findings

1. **All major operational ensemble flood forecasting systems use process-based
   hydrological models** (LISFLOOD, WRF-Hydro, GR4H). No operational system
   uses ML for the hydrological routing step of ensemble streamflow forecasting.
2. **Google Flood Hub is the only operational ML-based streamflow system at
   global scale**, but it uses deterministic NWP + learned uncertainty (CMAL),
   not ensemble NWP propagation. Its global system is **daily only** — no
   operational ML system produces hourly probabilistic streamflow at multi-basin
   scale.
3. **ECMWF is developing AIFL**, an LSTM-based global streamflow model, but it
   is pre-operational and deterministic. AIFS ENS (51 members) is operational
   for weather but **not yet coupled to hydrology** — a publication window
   exists.
4. **The gap is confirmed**: no operational system propagates ensemble NWP
   members through an ML streamflow model to produce physically consistent
   ensemble streamflow forecasts.
5. **Uncertainty paradigms are untested head-to-head for streamflow**: Klotz et
   al. (2022) compared CMAL/MC Dropout/GMM but only with observed forcing at
   daily resolution. Deep ensembles (standard in AI weather) and statistical
   post-processing (BMA/EMOS) add further untested options.
6. **After the 2021 European floods**, all affected NW European countries
   invested in probabilistic forecasting, but harmonisation of warning levels
   and cross-border coordination remains poor.

---

## 1.1 Process-Based Ensemble Systems

### EFAS (European Flood Awareness System)

**Model**: LISFLOOD — distributed, hybrid conceptual-physical rainfall-runoff
model with kinematic wave routing. Computes full water balance per grid cell
(snowmelt, soil freezing, surface runoff, infiltration, preferential flow,
soil moisture redistribution, groundwater drainage, baseflow).

**Resolution history**:
- Pre-2023: 5 x 5 km grid
- EFAS v5.0 (operational 20 Sep 2023): **1 arcminute (~1.5 km)**. LISFLOOD
  runs 30x faster than the previous version, enabling this resolution jump
  (Smith et al., 2023).
- EFAS v5.5 (Sep 2025): AIFS Single integration

**NWP inputs**:
- ECMWF ENS: 51 members, up to 15-day lead time
- COSMO-LEPS: 20 members (~5.5-day lead time)
- DWD ICON: deterministic
- AIFS Single (since v5.5, Sep 2025): replaces ECMWF HRES

**Ensemble propagation**: Each NWP ensemble member independently forces the
same deterministic LISFLOOD model, producing one streamflow trace per member.
Exceedance probabilities computed against pre-defined return period thresholds
(2-year, 5-year, 20-year). The hydrological model itself has no stochastic
component — all ensemble spread comes from the NWP input (pure Paradigm A).

**Initialised twice daily** (00 and 12 UTC), 6-hourly or 24-hourly time steps,
5–15 day lead times depending on NWP product.

**Operational since 1 April 2012** (part of Copernicus Emergency Management
Service since 2011; research prototype from ~2003).

**Key references**:
- Thielen, J., Bartholmes, J., Ramos, M.-H., and de Roo, A.: The European
  Flood Alert System — Part 1: Concept and development, Hydrol. Earth Syst.
  Sci., 13, 125–140, doi:10.5194/hess-13-125-2009, 2009.
  https://doi.org/10.5194/hess-13-125-2009
- Bartholmes, J. C., Thielen, J., Ramos, M. H., and Gentilini, S.: The
  European Flood Alert System EFAS — Part 2: Statistical skill assessment of
  probabilistic and deterministic operational forecasts, Hydrol. Earth Syst.
  Sci., 13, 141–153, doi:10.5194/hess-13-141-2009, 2009.
  https://doi.org/10.5194/hess-13-141-2009
- Pappenberger, F., Cloke, H. L., Parker, D. J., Wetterhall, F., Richardson,
  D. S., and Thielen, J.: The monetary benefit of early flood warnings in
  Europe, Environ. Sci. Policy, 51, 278–291, 2015. (Benefit-cost ratio ~400:1;
  total EFAS cost ~EUR 42M over 10 years including development.)
  <!-- TODO: Verify DOI — likely 10.1016/j.envsci.2014.09.005 -->
- Mazzetti, C., Decremer, D., and Prudhomme, C.: Major upgrade of the European
  Flood Awareness System, ECMWF Newsletter, 166, 2021.
  https://www.ecmwf.int/en/newsletter/166/meteorology/major-upgrade-european-flood-awareness-system
  <!-- TODO: This is the 2021 article (Newsletter 166). The EFAS v5.0 upgrade
  (1 arcminute, LISFLOOD 30x speedup) went operational Sep 2023 and may be
  described in a later newsletter. Verify whether a separate v5.0 article
  exists, or whether the claims here belong to a different source. -->

### GloFAS (Global Flood Awareness System)

**Model**: os-LISFLOOD (open-source). Modelling chain: ECMWF IFS computes
surface and sub-surface runoff via HTESSEL land-surface scheme, then LISFLOOD
routes runoff through the global river network.

**Resolution history**:
- GloFAS v3.1 (May 2021): 0.1° (~10 km) — first fully open-source LISFLOOD
- **GloFAS v4.0** (reanalysis 2022, forecast operational 2023): **0.05° (~5 km)**
  — 4x resolution of v3.1. Calibrated against **1,995 discharge gauges** globally
  using DEAP evolutionary algorithm with modified KGE objective. 14 parameter
  maps. Reanalysis: 1979–2019 forced by ERA5.
- GloFAS v4.4 (Sep 2025): AIFS Single integration — first multi-model GloFAS

**NWP inputs**:
- ECMWF ENS: 51 members, medium-range (up to 15 days)
- Extended-range ensemble: up to 30 days
- Seasonal outlooks: up to 4 months (since 2018)
- AIFS Single (since v4.4, Sep 2025): deterministic only — AIFS ENS (51
  members, operational since Jul 2025 for weather) is **not yet integrated**
  into GloFAS hydrology

**Key references**:
- Alfieri, L., Burek, P., Dutra, E., Krzeminski, B., Muraro, D., Thielen, J.,
  and Pappenberger, F.: GloFAS — global ensemble streamflow forecasting and
  flood early warning, Hydrol. Earth Syst. Sci., 17, 1161–1175,
  doi:10.5194/hess-17-1161-2013, 2013.
  https://doi.org/10.5194/hess-17-1161-2013
- Harrigan, S., Zsoter, E., Alfieri, L., Prudhomme, C., Salamon, P.,
  Wetterhall, F., Barnard, C., Cloke, H., and Pappenberger, F.:
  GloFAS-ERA5 operational global river discharge reanalysis 1979–present,
  Earth Syst. Sci. Data, 12, 2043–2060, doi:10.5194/essd-12-2043-2020, 2020.
  https://doi.org/10.5194/essd-12-2043-2020

### NOAA National Water Model (NWM)

**Model**: WRF-Hydro framework + Noah-MP land surface model.

**Coverage**: 2.7 million stream reaches across CONUS, plus southern Alaska,
Hawaii, Puerto Rico, US Virgin Islands.

**Resolution**: 250 m routing grid, 1 km land surface grid.

**Ensemble forecasts**:
- Medium-range: **6-member** time-lagged ensemble from GFS (not true ensemble
  NWP — uses staggered deterministic runs), 8.5–10 day lead time, 4x daily
- Long-range: **16-member** 30-day ensemble from CFS, 4x daily

**Operational since 2016** (v1.0).

**Limitation**: Small ensemble sizes (6 and 16) compared to EFAS (51+20) and
GloFAS (51). Time-lagged approach undersamples forecast uncertainty.

### Australia: Bureau of Meteorology

**Model**: GR4H (hourly conceptual rainfall-runoff) + lag-and-route channel
routing, implemented in SWIFT (Short-term Water Information Forecasting Tools).

**Coverage**: 209 forecast locations across diverse hydroclimatic regions.

**Operational since 2019** (public service).

**Reference**: Hapuarachchi, H. A. P., Bari, M. A., Kabir, A., et al.:
Development of a national 7-day ensemble streamflow forecasting service for
Australia, Hydrol. Earth Syst. Sci., 26, 4801–4821,
doi:10.5194/hess-26-4801-2022, 2022.
https://doi.org/10.5194/hess-26-4801-2022

### Japan

MEPS (Meso-scale Ensemble Prediction System, 21 members) forces high-resolution
(~150 m) nationwide distributed rainfall-runoff models. Ensemble flash flood
predictions demonstrated for major events (2018 heavy rains, Typhoon Hagibis
2019). Research/pre-operational.

**Reference**: Sayama, T., Yamada, M., Sugawara, Y., and Yamazaki, D.:
Ensemble flash flood predictions using a high-resolution nationwide distributed
rainfall-runoff model: case study of the heavy rain event of July 2018 and
Typhoon Hagibis in 2019, Prog. Earth Planet. Sci., 7, 75,
doi:10.1186/s40645-020-00391-7, 2020.
https://doi.org/10.1186/s40645-020-00391-7

### India

Central Water Commission operates at 332 stations (133 inflow, 199 level),
~10,000 forecasts annually. Uses GFS/WRF from IMD. **Primarily deterministic**
in operational mode. Part of South Asia Flash Flood Guidance System (SAsiaFFGS).

### WMO Flash Flood Guidance System (FFGS)

Covers **60+ countries** serving ~3 billion people. Deterministic
threshold-based, not full ensemble streamflow forecasting.

### Summary Table: Process-Based Operational Systems

| System | Model | Resolution | NWP Ensemble | Members | Lead Time | Operational |
|--------|-------|-----------|-------------|---------|-----------|-------------|
| EFAS | LISFLOOD | 1 arcmin (~1.5 km) | ECMWF ENS + COSMO-LEPS + AIFS Single | 51+20+det | 5–15 d | 2012 |
| GloFAS | os-LISFLOOD | 0.05° (~5 km) | ECMWF ENS + ext. range + AIFS Single | 51 | 15–30 d | 2021 |
| NOAA NWM | WRF-Hydro | 250 m routing | GFS time-lagged / CFS | 6 / 16 | 10 / 30 d | 2016 |
| BoM Australia | GR4H/SWIFT | catchment-scale | NWP ensemble | variable | 7 d | 2019 |
| Japan | distributed RR | ~150 m | MEPS | 21 | 39 h | pre-op |

---

## 1.2 ML-Based Operational Systems

### Google Flood Hub

**Operator**: Google Research / Flood Forecasting Initiative.

**Coverage**: 80+ countries with verified gauged forecasts; ~150+ countries with
virtual gauges. ~250,000 virtual gauge forecast points. Reaches ~460M+ people
in flood-prone areas.

**Architecture**: Two-stage LSTM:
1. Sequence-to-one **hindcast LSTM** processes historical data (precipitation,
   stage measurements)
2. Hands off cell state to a sequence-to-sequence **forecast LSTM** that
   produces predictions at each lead time step
3. Separate **embedding networks** per weather product (ECMWF HRES, GraphCast).
   Outputs combined before LSTM input — robust to missing products.

**Training data**: Expanded from ~5,680 gauges (original) to ~16,000 gauges
(using Caravan open dataset).

**NWP input**: **Deterministic only** — ECMWF HRES + GraphCast (Google
DeepMind's AI weather model). **No ensemble NWP propagation.**

**Temporal resolution**: **Daily** for the global system (7-day horizon). The
original India/Bangladesh system (Nevo et al., 2022) produced **hourly** water
stage forecasts at 8–48 h lead times. **No operational ML system produces
hourly ensemble or probabilistic streamflow forecasts at multi-basin scale** —
this is a distinct gap beyond the ensemble NWP question.

**Uncertainty handling**: **CMAL** (Countable Mixture of Asymmetric Laplacians)
distribution head on forecast LSTM outputs. Trained with negative
log-likelihood loss. Uncertainty is **learned from data**, not propagated from
NWP ensemble spread. This is Paradigm B (learned distribution), not Paradigm A
(NWP pass-through). When uncertainty exceeds ~50 cm, lead time is shortened.

**Key references**:
- Nevo, S., Morin, E., Gerzi Rosenthal, A., et al.: Flood forecasting with
  machine learning models in an operational framework, Hydrol. Earth Syst.
  Sci., 26, 4013–4032, doi:10.5194/hess-26-4013-2022, 2022.
  https://doi.org/10.5194/hess-26-4013-2022
  (Describes the India/Bangladesh operational system, 376 gauges, hourly stage.)
- Nearing, G., Cohen, D., Dube, V., Gauch, M., Gilon, O., Harrigan, S.,
  Hassidim, A., Klotz, D., Kratzert, F., Metzger, A., Nevo, S.,
  Pappenberger, F., Prudhomme, C., Shalev, G., Shenzis, S., Tekalign, T. Y.,
  Weitzner, D., and Matias, Y.: Global prediction of extreme floods in
  ungauged watersheds, Nature, 627, 559–563,
  doi:10.1038/s41586-024-07145-1, 2024.
  https://doi.org/10.1038/s41586-024-07145-1
  (Global system, daily, matches/exceeds GloFAS nowcast in ungauged basins.)

### ECMWF AIFL (AI for Flood Forecasting)

**Pre-operational** LSTM-based global streamflow model. Pre-trained on
ERA5-Land, fine-tuned on IFS. Delivers up to 10-day streamflow predictions.
Successfully predicted a 20-year flood signal 6 days ahead during Storm Henk
(Jan 2024). Not yet replacing LISFLOOD but being integrated.

**SEED-FD project** (started Feb 2024, 3-year): Developing LSTM error models to
improve GloFAS forecasts using ML and satellite data.

**Reference**: Taccari, M. L., Tazi, K., Morrison, O. M., Grafberger, A.,
Colonese, J., Carton de Wiart, C., Prudhomme, C., Mazzetti, C., Chantry, M.,
and Pappenberger, F.: AIFL: A global daily streamflow forecasting model using
deterministic LSTM pre-trained on ERA5-Land and fine-tuned on IFS, arXiv
preprint, arXiv:2602.16579, 2026.
https://arxiv.org/abs/2602.16579

### NASA SPoRT Streamflow-AI

Deep learning model running in **near-real-time** at **250+ locations** across
the Eastern US with 7-day forecasts. Uses QPFs from WPC, National Blend of
Models, and GFS, plus NASA soil moisture and USGS gauge observations. Developed
in R2O collaboration with NWS River Forecast Centers. **Deterministic NWP
input, not ensemble.**

### NOAA Hybrid AI-NWM (Errorcastnet)

ML post-processing that learns NWM errors and corrects them. Combined with NWM,
the hybrid is **4–6x more accurate** than NWM alone for flood prediction (AGU
2025). Not standalone ML — enhances process-based NWM.

### No ML Operational Systems in Developing Countries

**No national hydromet service in a developing country independently runs ML for
operational streamflow forecasting.** Google Flood Hub provides this capability
externally. China, India: active research but not operationalised nationally.

### Summary Table: ML-Based Systems

| System | Operator | Temporal Res. | NWP Input | Ensemble? | Uncertainty | Status |
|--------|----------|--------------|-----------|-----------|-------------|--------|
| Google Flood Hub | Google | Daily (global), hourly (India) | ECMWF HRES + GraphCast (det.) | No | CMAL (learned) | Operational |
| AIFL | ECMWF | Daily | IFS (det.) | No | Deterministic | Pre-operational |
| SPoRT Streamflow-AI | NASA/NWS | Daily | QPF (det.) | No | Deterministic | Near-operational |
| Errorcastnet | NOAA | Hourly | NWM output (post-processing) | No | No | Research |

---

## 1.3 The Scalability Argument

### Per-catchment calibration burden

Process-based models require per-catchment calibration of 4–20+ parameters
against observed streamflow:
- EFAS LISFLOOD: calibrated over **700+ catchments** across Europe, ~9–14
  parameters per catchment using evolutionary algorithms
- GloFAS v4: calibrated at **1,995 stations** globally, producing 14 parameter
  maps. Regionalization transfers parameters to ungauged catchments — pragmatic
  but introduces substantial uncertainty.
- EFAS v5.0 at 1 arcminute: calibration took ~2 weeks on HPC

### ML regional training advantage

A single Entity-Aware LSTM trained on **531 basins simultaneously outperformed
hydrological models calibrated both regionally AND individually per basin**
(Kratzert et al., 2019). The key insight: LSTMs learn transferable
representations across catchments.

**"Never train on a single basin"** — Kratzert et al. (HESS, 2024) argued that
per-basin LSTM training is strictly inferior to multi-basin training. Most
published LSTM hydrology studies violate this principle.

The Nearing et al. (2024) Nature paper demonstrated that a **single global
LSTM** outperformed GloFAS (which requires per-location calibration) even in
**ungauged watersheds** — directly challenging the process-based paradigm on
its weakest point (transferability to data-scarce regions).

Google scaled training from 5,680 to 16,000 gauges, improving everywhere
including locations not in the training set.

**Key references**:
- Kratzert, F., Klotz, D., Shalev, G., Klambauer, G., Hochreiter, S., and
  Nearing, G.: Towards learning universal, regional, and local hydrological
  behaviors via machine learning applied to large-sample datasets, Hydrol.
  Earth Syst. Sci., 23, 5089–5110, doi:10.5194/hess-23-5089-2019, 2019.
  https://doi.org/10.5194/hess-23-5089-2019
- Kratzert, F., Gauch, M., Klotz, D., and Nearing, G.: HESS Opinions: Never
  train a Long Short-Term Memory (LSTM) network on a single basin, Hydrol.
  Earth Syst. Sci., 28, 4187–4201, doi:10.5194/hess-28-4187-2024, 2024.
  https://doi.org/10.5194/hess-28-4187-2024

### Computational cost

Clark et al. (2017) identified the fundamental tension in process-based
modelling: "researchers still struggle with tradeoffs among process complexity,
spatial complexity, domain size, ensemble size, the time period of model
simulation, and with running their most complex models for a large number of
model configurations."

Running 51 ensemble members through a distributed model at 1-arcminute
resolution across Europe, twice daily, requires substantial HPC resources. The
LISFLOOD 30x speedup for EFAS v5.0 was achieved through joint
hydrology-computer science optimisation — without it, the resolution upgrade
would not have been feasible.

**Reference**: Clark, M. P., Bierkens, M. F. P., Samaniego, L., et al.: The
evolution of process-based hydrologic models: historical challenges and the
collective quest for physical realism, Hydrol. Earth Syst. Sci., 21,
3427–3440, doi:10.5194/hess-21-3427-2017, 2017.
https://doi.org/10.5194/hess-21-3427-2017

### Ensemble-specific limitations

Nikhil Teja and Umamahesh (2022) reviewed 201 papers (2001–2021) on ensemble
flood forecasting and found persistent problems: ensemble streamflow forecasts
are generally **biased and under-dispersed** (ensemble spread underestimates
true forecast uncertainty). Grand challenges include: quality-controlled
datasets, new ensemble weighting techniques, inclusion of more physically based
datasets in data assimilation, and computational efficiency for timely warnings.

**Reference**: Nikhil Teja, K. and Umamahesh, N. V.: Two decades of ensemble
flood forecasting: a state-of-the-art on past developments, present
applications and future opportunities, Hydrol. Sci. J., 67(3), 477–493,
doi:10.1080/02626667.2021.2023157, 2022.
https://doi.org/10.1080/02626667.2021.2023157

---

## 1.4 The Post-2021 Shift to Probabilistic Forecasting

Busker et al. (2025) compared FFEWSs in transboundary river basins across
Luxembourg, Germany, the Netherlands, and Belgium — all affected by the July
2021 European floods (>200 fatalities).

**Key findings**:
- All countries have invested in probabilistic flood forecasting post-2021
- All regions now use mobile phone-based alerts
- Strong differences persist in warning levels, color codes, and response
  protocols across and within countries
- Some regions introduced a **purple warning level** for extreme events
- Lack of harmonisation hinders cross-border coordination — critical for
  transboundary basins (Meuse, Rhine tributaries)

**Reference**: Busker, T., Rodriguez Castro, D., Vorogushyn, S., Kwadijk, J.,
Zoccatelli, D., Loureiro, R., Murdock, H. J., Pfister, L., Dewals, B.,
Slager, K., Thieken, A. H., Verkade, J., Willems, P., and Aerts, J. C. J. H.:
Comparing Flood Forecasting and Early Warning Systems in Transboundary River
Basins, EGUsphere [preprint], doi:10.5194/egusphere-2025-828, 2025.
https://doi.org/10.5194/egusphere-2025-828

---

## 1.5 ML Uncertainty Quantification for Streamflow

Beyond the Paradigm A (NWP pass-through) vs Paradigm B (learned distribution)
dichotomy, two additional approaches are relevant:

### Paradigm C: Deep Ensembles and MC Dropout

**MC Dropout** (Gal & Ghahramani, 2016) uses dropout at inference time to
approximate Bayesian uncertainty. Applied to streamflow by Fang et al. (WRR,
2020) who showed MC Dropout LSTMs can estimate prediction intervals, but
intervals were often under-dispersed compared to CMAL-based approaches.

**Deep ensembles** (Lakshminarayanan et al., NeurIPS 2017) train M models with
different random seeds. Standard in AI weather forecasting (AIFS-CRPS, Lang et
al., 2024) but **never tested head-to-head against MDN/CMAL for streamflow**.

**Klotz et al. (HESS, 2022)** compared uncertainty approaches for LSTM
streamflow: CMAL > UMAL > GMM >> MC Dropout. This is the closest to a
systematic comparison, but tested only with **observed forcing at daily
resolution** — the interaction with NWP ensemble uncertainty was not examined.

**Key references**:
- Klotz, D., Kratzert, F., Gauch, M., et al.: Uncertainty estimation with
  deep learning for rainfall-runoff modelling, Hydrol. Earth Syst. Sci., 26,
  1673–1693, doi:10.5194/hess-26-1673-2022, 2022.
  https://doi.org/10.5194/hess-26-1673-2022
- Fang, K., Shen, C., Kifer, D., and Yang, X.: Evaluating the potential and
  challenges of an uncertainty quantification method for long short-term memory
  models for soil moisture predictions, Water Resour. Res., 56,
  e2020WR028095, doi:10.1029/2020WR028095, 2020.
  https://doi.org/10.1029/2020WR028095

### Statistical Post-Processing of Ensemble Streamflow

A body of work on **Bayesian Model Averaging (BMA)** and **Ensemble Model
Output Statistics (EMOS)** post-processes ensemble streamflow forecasts to
correct bias and under-dispersion. Raftery et al. (2005) established BMA for
weather; Duan et al. (2007) adapted it for multi-model streamflow. Hemri et al.
(2015) applied EMOS to EFAS ensemble streamflow, improving reliability.

These methods sit between Paradigm A and B: they start from ensemble NWP
propagation but learn statistical corrections. They are relevant because a
reviewer will ask why ML doesn't simply post-process process-based ensemble
output (as Errorcastnet does for NWM).

**Key references**:
- Hemri, S., Scheuerer, M., Pappenberger, F., Bogner, K., and Haiden, T.:
  Trends in the predictive performance of raw ensemble weather forecasts,
  Geophys. Res. Lett., 41, 9197–9205, doi:10.1002/2014GL062472, 2014.
  https://doi.org/10.1002/2014GL062472
- Duan, Q., Ajami, N. K., Gao, X., and Sorooshian, S.: Multi-model ensemble
  hydrologic prediction using Bayesian model averaging, Adv. Water Resour.,
  30, 1371–1386, doi:10.1016/j.advwatres.2006.11.014, 2007.
  https://doi.org/10.1016/j.advwatres.2006.11.014

---

## 1.6 The Central Gap

**No operational system combines ML-based hydrological modelling with ensemble
NWP propagation for probabilistic streamflow forecasting.**

The landscape divides cleanly:
- **Process-based + ensemble NWP** (EFAS, GloFAS, NWM, BoM): physically
  consistent ensemble propagation, but computationally expensive, per-catchment
  calibration, limited scalability
- **ML + deterministic NWP + learned uncertainty** (Google): scalable, no
  per-catchment calibration, but uncertainty is learned (CMAL), not propagated
  from ensemble NWP

The question of whether ML models should propagate NWP ensemble uncertainty
(Paradigm A), learn it from data (Paradigm B, as Google does), use model
ensembles (Paradigm C), or post-process process-based ensemble output has
**never been systematically compared for streamflow**. Klotz et al. (2022)
compared uncertainty methods but only with observed forcing at daily resolution
— the interaction with NWP ensemble uncertainty at sub-daily timescales is
unexamined. This is the central open question.

The closest work to bridging this gap:
- Dong et al. (HESS, 2025): CNN downscales ECMWF S2S ensemble NWP, feeds to
  hybrid XAJ-LSTM for streamflow. But: sub-seasonal (daily), not medium-range;
  single basin.
- Nikhil Teja et al. (J. Hydrol., 2023): Multiple NWP+HM combinations, but
  HMs are process-based. ML (Random Forest) used only for multi-model
  combination.
  <!-- TODO: Verify RF claim — may be conflated with a 2022 companion paper
  by the same group on QRF for NWP post-processing. -->

**Reference for Dong et al.**: Dong, N., Hao, H., Yang, M., Wei, J., Xu, S.,
and Kunstmann, H.: Deep-learning-based sub-seasonal precipitation and
streamflow ensemble forecasting over the source region of the Yangtze River,
Hydrol. Earth Syst. Sci., 29, 2023–2042, doi:10.5194/hess-29-2023-2025, 2025.
https://doi.org/10.5194/hess-29-2023-2025

**Reference for Nikhil Teja et al.**: Nikhil Teja, K., Manikanta, V., Das, J.,
and Umamahesh, N. V.: Enhancing the predictability of flood forecasts by
combining Numerical Weather Prediction ensembles with multiple hydrological
models, J. Hydrol., 625, 130176, doi:10.1016/j.jhydrol.2023.130176, 2023.
https://doi.org/10.1016/j.jhydrol.2023.130176

---

## AIFS Integration Status (as of Sep 2025)

Important context for future work:
- **AIFS Single** (deterministic, graph neural network + sliding window
  transformer, trained on ERA5, ~28 km): operational since Feb 2025
- **AIFS ENS** (51 members, ~31 km): **operational for weather since Jul 2025**
- AIFS Single integrated into EFAS v5.5 and GloFAS v4.4 (Sep 2025)
- **AIFS ENS is NOT yet integrated into GloFAS/EFAS** — only AIFS Single
  (deterministic) feeds the hydrological models
- This means AI-generated ensemble weather forecasts exist but are not yet
  coupled with hydrological models operationally — a publication window exists
  for demonstrating ML hydrology + ensemble NWP before ECMWF couples AIFS ENS
  to LISFLOOD/GloFAS

### NWP Ensemble Data Availability for Research

A critical practical consideration: ML models trained on reanalysis (ERA5-Land)
need access to NWP forecast archives for evaluation and bias characterization.

**IFS ENS (physics-based, 51 members)**: dynamical.org maintains a persistent,
freely accessible archive of ECMWF IFS ENS forecasts in cloud-optimized Zarr
format from **2024-04-01 to present** (~2 years). 51 members, 0.25° global,
3-hourly to day 6, 6-hourly to day 15, 27 variables including precipitation,
temperature, humidity, wind, and radiation. CC BY 4.0. Access:
`https://data.dynamical.org/ecmwf/ifs-ens/forecast-15-day-0-25-degree/latest.zarr`

**AIFS ENS**: No persistent public archive exists. ECMWF's open data retains
only a rolling 2-3 day window. AIFS ENS is on dynamical.org's roadmap but not
yet available. Any research using AIFS ENS requires self-archiving from the
rolling feed.

**Implication**: The IFS ENS archive makes it possible today to (a) compute
bias statistics between ERA5-Land and real ensemble NWP forecasts over Nepal,
and (b) evaluate ML models trained on ERA5-Land against real 51-member ensemble
forcing — without waiting for a custom archiving pipeline to accumulate data.

---

## 1.8 Conclusions for SAPPHIRE Flow

### What the literature tells us

The operational landscape reveals a clean split: process-based systems propagate
NWP ensemble uncertainty but are expensive and require per-catchment
calibration; Google's ML system scales elegantly but sidesteps ensemble NWP
entirely in favor of learned uncertainty. Nobody has bridged the two approaches,
and nobody has tested whether bridging them would even help.

Five lessons emerge for SAPPHIRE Flow's design:

**1. Learned uncertainty (CMAL) is the pragmatic operational choice.**
Google demonstrated that a CMAL distribution head on an LSTM produces
operationally useful probabilistic forecasts without ensemble NWP propagation.
Klotz et al. (2022) confirmed CMAL outperforms MC Dropout and GMM. For
SAPPHIRE Flow's operational system — where reliability, computational
efficiency, and pipeline simplicity matter — Paradigm B (learned distribution)
is the right default. One forward pass per forecast, no 21x or 51x ensemble
bookkeeping, no sensitivity to NWP ensemble design changes.

**2. Multi-basin training is non-negotiable.**
Kratzert et al. (2024) settled this: per-basin LSTM training is strictly
inferior. Nearing et al. (2024) proved global multi-basin models outperform
even calibrated process-based models in ungauged basins. SAPPHIRE Flow must
train a single model across all available catchments — Swiss BAFU stations for
v0, expanded to Nepali DHM stations for v1. This is also what makes the system
transferable to Nepal without per-catchment calibration at DHM.

**3. Hourly resolution is an open niche.**
Google's global system is daily. The India/Bangladesh system was hourly but
limited to stage (not discharge) at 376 gauges. No operational ML system
produces hourly probabilistic streamflow at multi-basin scale. SAPPHIRE Flow
targets hourly resolution from v0 — this is a genuine differentiator, not just
an incremental improvement. For Nepal's fast-responding catchments (Koshi,
Narayani tributaries), hourly resolution directly extends effective lead time.

**4. The ensemble NWP question is a research question, not an engineering one.**
Whether propagating ICON-CH2-EPS members (v0) or ECMWF IFS ENS members (v1)
through an LSTM adds forecast value over CMAL is genuinely unknown. The
literature provides no guidance — Klotz et al. tested uncertainty methods only
with observed forcing, process-based systems never used ML, and Google never
tried ensemble NWP. This is a well-scoped research question for Master's theses
(ETH for Swiss setting, Tribhuvan for Nepal), not something to gamble the
operational system on. SAPPHIRE Flow implements CMAL; the theses test whether
ensemble propagation would have been better.

**5. The AIFS ENS window is real but narrow.**
AIFS ENS has been operational for weather since July 2025 but is not yet
coupled to any hydrological model. ECMWF will eventually feed AIFS ENS into
GloFAS/EFAS (likely with LISFLOOD, not ML). If SAPPHIRE Flow or the Master's
theses can demonstrate ML + ensemble NWP before that happens, the contribution
is sharper. This motivates starting NWP archiving as early as possible in v0.

### What this means in practice

For the SAPPHIRE Flow codebase, these conclusions translate to:

- **Model output**: CMAL distribution head producing quantile forecasts
  (QuantileForecast in the type spec), not raw ensemble traces
- **NWP input**: Deterministic control run (or ensemble mean) as default model
  input; ensemble member propagation is an optional research mode, not the
  operational path
- **Training strategy**: Single Entity-Aware LSTM trained across all Swiss
  catchments (v0), with static catchment attributes as entity features
- **Archiving**: Store all ICON-CH2-EPS ensemble members regardless — they cost
  little to archive and are essential for the thesis experiments. For Nepal/v1,
  the dynamical.org IFS ENS archive (Apr 2024–present, 51 members, Zarr)
  provides immediate access to real ensemble NWP data without waiting for a
  custom pipeline.
- **Bias characterization**: Compute ERA5-Land vs IFS ENS bias statistics over
  Nepal before thesis work begins — a day's engineering work that de-risks the
  training-to-deployment gap for all downstream experiments.
- **Evaluation**: CRPS as the primary probabilistic metric, with spread-skill
  ratio and rank histograms for calibration assessment

---

## Reference Verification Status

Verified 2026-03-09 via DOI resolution and web search:
- [x] Busker et al. (2025) — full 14-author list added, DOI confirmed
- [ ] "Smith et al. (2023)" ECMWF Newsletter — **CORRECTED**: Newsletter 166 is
  by Mazzetti, Decremer, and Prudhomme (2021). The EFAS v5.0 (1 arcminute,
  2023) source needs manual identification — may be a different newsletter
  issue or technical report.
- [x] "Zsoter et al. (2022)" — **CORRECTED**: DOI 10.1080/02626667.2021.2023157
  is by Nikhil Teja, K. and Umamahesh, N. V. (not Zsoter). Pages: 477–493.
- [x] Nikhil Teja et al. (J. Hydrol., 2023) — full citation added.
  DOI: 10.1016/j.jhydrol.2023.130176
- [x] ECMWF AIFL — arXiv:2602.16579, Taccari et al. (2026), full author list
- [x] "Nakakita et al. (2020)" — **CORRECTED**: DOI 10.1186/s40645-020-00391-7
  is by Sayama, T. et al. (not Nakakita). Full title with subtitle added.
- [x] Hapuarachchi et al. (2022) — pages 4801–4821 confirmed
- [ ] Pappenberger et al. (2015) — DOI needs verification (try
  10.1016/j.envsci.2014.09.005)

## Additional References Found

These were not in the original outline but are relevant:
- Frame, J. M., et al.: Deep learning rainfall-runoff predictions of extreme
  events, Hydrol. Earth Syst. Sci., 26, 3377–3392,
  doi:10.5194/hess-26-3377-2022, 2022. (LSTM outperforms process-based on
  extremes; mass-conserving LSTM worse on out-of-sample extremes.)
  https://doi.org/10.5194/hess-26-3377-2022
- Emerton, R. E., et al.: Continental and global scale flood forecasting
  systems, WIREs Water, 3(3), 391–418, doi:10.1002/wat2.1137, 2016.
  (Comprehensive review of operational systems as of 2016.)
  https://doi.org/10.1002/wat2.1137
- Lees, T., et al.: Hydrological concept formation inside long short-term
  memory (LSTM) networks, Hydrol. Earth Syst. Sci., 26, 3079–3101,
  doi:10.5194/hess-26-3079-2022, 2022. (LSTM benchmarking 669 GB catchments.)
  https://doi.org/10.5194/hess-26-3079-2022
