# 6. Datasets and NWP Ensemble Products

Literature review for Section 6 of the Paper 0 outline.
Last updated: 2026-03-31.

## Key Findings

1. **Four curated large-sample datasets provide matched hourly streamflow +
   forcing**: CAMELSH (5,188+ US basins), LamaH-CE (859 Central European),
   CAMELS-GB v2 (671 UK), and CAMELS-SPAT (1,426 US/Canada). All stop at
   hourly — no sub-hourly benchmark exists anywhere.
2. **Only CAMELS-GB v2 provides hourly water level alongside discharge** — a
   critical variable for operational flood warning. CAMELS-CH and CAMELS-DE
   include daily water level. USGS NWIS has raw 15-min stage but uncurated.
3. **CAMELS-CH is daily only** (331 Swiss catchments). No formal hourly
   extension published. HydroCH (Kauzlaric et al., 2023) provides hourly
   discharge for 291 Swiss catchments but is standalone and unmatched with
   forcing data. SAPPHIRE v0 must assemble its own sub-daily Swiss dataset.
4. **Two substantial NWP ensemble reforecast archives exist**: GEFSv12 (5/11
   members, 3-hourly, 2000-2019, free on AWS) and ECMWF ENS (11 members,
   6-hourly, rolling 20 years, restricted access). No AI weather model
   reforecast archive is publicly available despite the potential to generate
   them cheaply.
5. **Ensemble member count mismatch is a recognised problem** — reforecasts
   have 5-11 members vs 31-51 operational. Strategies include: ensemble
   summary statistics, per-member processing, fair CRPS scoring. The AIFS-CRPS
   finding that 4 training members suffice (with larger ensembles at inference)
   is promising but unvalidated for hydrology.
6. **ERA5-Land is the only viable global hourly reanalysis** for sub-daily ML
   training, but has systematic precipitation biases in complex topography:
   ~40 mm/month overestimation in Alps, "spectacular" monsoon overestimation
   in Himalayas. Bias correction is mandatory.
7. **The reanalysis-to-NWP distribution shift is substantial** — AIFL showed
   performance degrades substantially without fine-tuning on NWP (exact NSE
   drop TBC). Two-stage training (pre-train reanalysis, fine-tune NWP)
   recovers skill (median KGE' 0.66). Essential for operational systems.
8. **No dataset exists for Nepal** — CAMELS-IND covers peninsular India only,
   excluding Himalayan catchments. SAPPHIRE v1 must construct its own dataset
   from DHM stations + ERA5-Land/IFS forcing.
9. **Temporal split strategies must avoid data leakage** — sequential holdout
   with post-split sequence construction is mandatory. Standard k-fold CV is
   invalid for time series. Buffer zones between train/val/test should match
   LSTM lookback window (180-365 days).

See also: [precipitation_products.md](precipitation_products.md) for a detailed
survey of operational precipitation products for Nepal.

---

## 6.1 Streamflow Benchmark Datasets

### Summary table

| Dataset | Region | N basins | Temporal | Q | Stage | Forcing | Record | Access |
|---|---|---|---|---|---|---|---|---|
| **CAMELSH** | CONUS | 5,188+ | Hourly | Yes | No | NLDAS-2 (11 vars) | 1980-2024 | Zenodo, open |
| **LamaH-CE** | Central Europe | 859 | Hourly | Yes | No | ERA5-Land | ~1981-2017 | Zenodo, open |
| **CAMELS-GB v2** | Great Britain | 671 | Hourly | Yes | **Yes** | Radar-gauge 1 km | 2006-2022 | EIDC, OGL |
| **CAMELS-SPAT** | US/Canada | 1,426 | Hourly | Yes | No | 4 products | Varies | FRDR, open |
| **CAMELS-CH** | Switzerland | 331 | **Daily** | Yes | Yes (daily) | Daily met | 1981-2020 | Zenodo, open |
| **CAMELS-DE** | Germany | 1,582 | **Daily** | Yes | Yes (daily) | DWD | 1951-2020 | Open |
| **HydroCH** | Switzerland | 291 | Hourly | Yes | No | **None** | Varies | Zenodo, open |
| **CAMELS-IND** | Peninsular India | 228 | **Daily** | Yes | No | IMD/IMDAA | 1980-2020 | Zenodo, open |
| **Caravan** | Global | ~6,830 (original); ~22,000+ (community extensions) | **Daily** | Yes | No | ERA5-Land | 1950-2023 | Zenodo, open |
| **USGS NWIS** | US | ~10,000+ | **15-min** | Yes | Yes | **None** | ~1990s- | Free API |

### CAMELSH (Tran et al., Sci. Data, 2025)

The first large-sample hourly hydrometeorological dataset: 9,008 catchments with
NLDAS-2 forcing, 5,188+ with hourly USGS streamflow, 45 years (1980-2024),
439+ catchment attributes. ERA5-Land extension also available.

**CRAAB**:
- *Claim*: First large-sample hourly dataset for CONUS. Valid.
- *Research gap*: No matched NWP ensemble forcing. No water level. No sub-hourly
  despite USGS recording at 15-min.
- *Bias*: NLDAS-2 underestimates convective precipitation extremes. Gauge
  network favours eastern US. Regulated catchments included without filtering.

### LamaH-CE (Klingler et al., ESSD, 2021)

859 basins across the upper Danube and Austria. Unique nested basin structure
enabling network-aware modelling. ERA5-Land hourly forcing. 60+ attributes.

**CRAAB**:
- *Claim*: Comprehensive hourly dataset for Central Europe with intermediate
  catchment delineation. Valid.
- *Research gap*: No NWP ensemble forcing. No water level. Alpine precipitation
  uncertainty high and unquantified.
- *Bias*: Geographic bias toward Austria/upper Danube. ERA5-Land smooths
  orographic extremes.

### CAMELS-GB v2 (Coxon et al., ESSD, 2025 — in review)

671 catchments with daily (1970-2022) and **hourly** (2006-2022) data.
Critically includes **hourly water level** alongside discharge — unique among
CAMELS datasets. Radar-gauge precipitation blend at 1 km. Groundwater levels
for 55 wells.

**CRAAB**:
- *Claim*: First CAMELS dataset with hourly water level. Significant.
- *Research gap*: Short hourly record (16 years) limits ML training without
  transfer. No NWP ensemble forcing. Still in review.
- *Bias*: UK maritime climate only. Radar coverage weaker in Scottish Highlands.

### CAMELS-CH (Hoege et al., ESSD, 2023)

331 Swiss catchments, daily only, 1981-2020. Includes daily water level.
References HydroCH (Kauzlaric et al., 2023) for hourly discharge (291
catchments) but no formal integration.

**CRAAB**:
- *Research gap*: Daily only — critical gap for sub-daily forecasting. No
  hourly extension timeline. HydroCH provides hourly Q but no matched forcing.
- *Implication for SAPPHIRE v0*: Must assemble Swiss sub-daily data directly
  from BAFU/MeteoSwiss sources, which is already the planned approach.

### USGS NWIS (raw 15-minute data)

The only sub-hourly streamflow source at scale. ~10,000+ active gauges with
15-min (some 5-min) stage and derived discharge. Free API access.

**CRAAB**:
- *Research gap*: No curated benchmark at 15-min resolution. No matched 15-min
  forcing. This is raw material, not a ready-to-use ML dataset.
- *Assumption*: Rating curves are current and accurate — often not true during
  flood events. Some "15-min" data is interpolated.
- *Bias*: Network density favours populated areas. Systematic peak flow
  underestimation during extremes due to rating curve extrapolation.

### South/Southeast Asia: the gap

- **CAMELS-IND** (228 catchments, daily, peninsular India only) — explicitly
  excludes Himalayan catchments.
- No CAMELS-Nepal, CAMELS-Bangladesh, or CAMELS-SE Asia exists.
- Caravan/GRDC-Caravan includes sparse South Asian stations via GRDC.
- **For Nepal v1, SAPPHIRE must construct its own dataset from DHM stations +
  ERA5-Land/IFS forcing.**

**Key references**:
- Tran, V. N., et al.: CAMELSH: A Large-Sample Hourly Hydrometeorological
  Dataset and Attributes at Watershed-Scale for CONUS, Sci. Data, 12, 1307,
  doi:10.1038/s41597-025-05612-6, 2025.
- Klingler, C., Schulz, K., and Herrnegger, M.: LamaH-CE: LArge-SaMple DAta
  for Hydrology and Environmental Sciences for Central Europe, Earth Syst. Sci.
  Data, 13, 4529-4565, doi:10.5194/essd-13-4529-2021, 2021.
- Coxon, G., et al.: CAMELS-GB v2: hydrometeorological time series and
  landscape attributes for 671 catchments in Great Britain, Earth Syst. Sci.
  Data (preprint), doi:10.5194/essd-2025-608, 2025.
- Höge, M., et al.: CAMELS-CH: hydro-meteorological time series and landscape
  attributes for 331 catchments in hydrologic Switzerland, Earth Syst. Sci.
  Data, 15, 5755-5784, doi:10.5194/essd-15-5755-2023, 2023.
- Kauzlaric, M., et al.: Hourly discharge database HydroCH, Zenodo,
  doi:10.5281/zenodo.7691294, 2023.
- Knoben, W. J. M., et al.: CAMELS-SPAT: streamflow observations, forcing data
  and geospatial data for hydrologic studies across North America, Hydrol. Earth
  Syst. Sci., 29, 5791-5833, doi:10.5194/hess-29-5791-2025, 2025.
- Mangukiya, N. K., et al.: CAMELS-IND: hydrometeorological time series and
  catchment attributes for 228 catchments in Peninsular India, Earth Syst. Sci.
  Data, 17, 461-491, doi:10.5194/essd-17-461-2025, 2025.
- Loritz, R., et al.: CAMELS-DE: hydro-meteorological time series and attributes
  for 1582 catchments in Germany, Earth Syst. Sci. Data, 16, 5625-5642,
  doi:10.5194/essd-16-5625-2024, 2024.
- Kratzert, F., et al.: Caravan — A global community dataset for large-sample
  hydrology, Sci. Data, 10, 61, doi:10.1038/s41597-023-01975-w, 2023.

---

## 6.2 NWP Ensemble Products

### Operational ensemble products

| Model | Agency | Spatial | Temporal | Members | Lead | Access |
|---|---|---|---|---|---|---|
| **IFS ENS** | ECMWF | 9 km | 1h→3h→6h | 51 | 15 d | Open (CC-BY-4.0) |
| **AIFS ENS** | ECMWF | 31 km | ~6h | 50 | 15 d | Open |
| **GEFS v12** | NOAA | 25 km | 3h→6h | 31 | 16 d | Free (AWS) |
| **ICON-CH2-EPS** | MeteoSwiss | 2.2 km | Hourly | 21 | 5 d | STAC API |
| **MOGREPS-G** | UKMO | 20 km | 1h→3h | 45 | ~8 d | AWS (30-day rolling) |
| **ICON-EPS** | DWD | 40 km | ~6h | 40 | 7.5 d | Free |

For detailed product descriptions, including Nepal-specific NWP products
(HIWAT, NCMRWF, TIGGE), AI weather models (AIFS, GenCast, GraphCast), and
near-real-time precipitation estimates (IMERG, GSMaP, CMORPH2), see
[precipitation_products.md](precipitation_products.md).

### Reforecast archives for ML training

| Product | Members | Spatial | Temporal | Archive | Access |
|---|---|---|---|---|---|
| **GEFSv12 reforecast** | 5 daily / 11 weekly | 0.25 deg | 3h (0-72h), 6h (72-384h) | 2000-2019 | Free (AWS S3) |
| **ECMWF ENS reforecast** | 11 | ~18 km | 6h | Rolling 20 yr | MARS (restricted) |
| **S2S reforecast** | Centre-dependent | 1-1.5 deg | Daily | 20+ yr | Free (research) |
| **TIGGE** | 15-51/centre | 0.5 deg | 6h | 2006-present | Free (research) |
| **AIFS ENS hindcast** | 51 | 0.25 deg | ~6h | 2003-2022 | Unclear (internal?) |

**GEFSv12 reforecast** is the dominant choice for ML streamflow experiments:
free, easy access on AWS, 20-year archive with 3-hourly resolution for the
first 72 hours. Main limitation: only 5 members daily (11 weekly) vs 31
operational. The 3h→6h temporal transition at 72 hours creates interpolation
questions for hydrological models expecting uniform timesteps.

**ECMWF ENS reforecast** is higher quality but access is restricted to ECMWF
member/co-operating states and licensed researchers. The October 2025 open-data
policy covers real-time products; whether it extends to the full reforecast
archive is ambiguous.

**ICON-CH2-EPS** (SAPPHIRE v0's primary NWP): 21 members, 2.2 km
convection-permitting, hourly, 5-day lead. **No public reforecast archive
exists.** This is a significant constraint for ML training — the model must be
trained on observations/reanalysis and adapted to NWP at deployment.

**No AI weather model reforecast archive is publicly available** despite the
potential to generate them cheaply (minutes per forecast vs hours for
physics-based). AIFS hindcast (2003-2022) is referenced in ECMWF papers but
access for external researchers is unclear.

**CRAAB** (cross-cutting):
- *Research gap*: No public reforecast for ICON-CH2-EPS or any convection-
  permitting ensemble. ML models cannot be trained on NWP directly and must
  rely on the reanalysis→NWP domain adaptation strategy.
- *Assumption*: GEFSv12 and ECMWF ENS reforecasts are representative of
  operational forecast quality. They use frozen model versions, which avoids
  discontinuities but does not reflect operational evolution.
- *Ambiguity*: Whether ECMWF's open-data policy extends to reforecasts.

**Key references**:
- Guan, H., et al.: GEFSv12 Reforecast for Subseasonal and
  Hydrometeorological Applications, Mon. Weather Rev., 150(3),
  doi:10.1175/MWR-D-21-0245.1, 2022.
- ECMWF: Reforecast documentation,
  https://www.ecmwf.int/en/forecasts/documentation-and-support/extended-range/
  re-forecast-medium-and-extended-forecast-range

---

## 6.3 Reanalysis Products for Training

| Product | Spatial | Temporal | Coverage | Record | Key bias |
|---|---|---|---|---|---|
| **ERA5-Land** | ~9 km | Hourly | Global | 1950-present | +40 mm/mo precip in Alps; monsoon overestimation in Himalayas |
| **ERA5** | ~31 km | Hourly | Global | 1940-present | Coarser; precipitation biases similar |
| **NLDAS-2** | ~12 km | Hourly | CONUS | 1979-2018 | US only; radar-disaggregated precip |
| **CHESS-met** | 1 km | **Daily** | Great Britain | 1961-2019 | Daily only; interpolation artefacts |
| **HARv2** | 10 km | Hourly | High Asia | 1979-present | Better than ERA5-Land for wind in Nepal |

### ERA5-Land: the default for global ML hydrology

ERA5-Land is the de facto standard forcing for global-scale ML streamflow
training. It is the backbone of Caravan, AIFL, and most global LSTM studies.
However, systematic biases in complex topography are well-documented:

- **Alps**: ~40 mm/month precipitation overestimation, ~2.1 deg C temperature
  underestimation (Dalla Torre and Di Marco, J. Hydrol. Reg. Stud., 2024;
  South Tyrol). ERA5 (not ERA5-Land) precipitation RMAE 37% in Austrian Alpine
  headwaters (Water, 2025).
- **Nepal/Himalayas**: "Spectacular overestimation" during monsoon at high
  elevations in Everest/Dudh Koshi basin (Khadka et al., JAMC, 2022). Negative
  bias at high altitudes, positive at lower altitudes.
- **Drizzle problem**: Overestimates light precipitation, underestimates
  high-intensity events — exactly the conditions that matter for floods.

Bias correction is mandatory for mountainous applications. Regional
climatologies (not global WorldClim) are recommended. ML-based bias correction
(Random Forest) shows promise (R^2 > 0.99 for temperature, ~0.91 for precip).

**Implication**: SAPPHIRE v0 uses SwissMetNet station observations as training
forcing (bypassing ERA5-Land bias). v1 Nepal will require ERA5-Land with bias
correction.

**CRAAB**:
- *Claim*: ERA5-Land is suitable for global ML hydrology training. Partially
  true — works well in flat/temperate terrain, requires bias correction in
  mountains.
- *Research gap*: No large-scale study compares ERA5-Land hourly against station
  observations specifically for sub-daily ML training in Alps or Himalayas.
- *Ambiguity*: ERA5-Land precipitation is a model product (not assimilated from
  gauges directly) — its "observation-like" status is misleading.

### NLDAS-2 vs ERA5 performance comparison

Predicting streamflow with global datasets (Front. Water, 2023) showed NLDAS-2
local forcing achieved median daily NSE 0.71 vs ERA5's 0.54 on CAMELS-US — a
~0.17 NSE advantage primarily from higher spatial resolution (12 km vs 31 km).
However, ERA5 outperformed NLDAS-2 in western/NW US (median NSE 0.83 vs 0.78),
suggesting the advantage is region-dependent. *Note: these figures are from a
2023 Frontiers in Water paper, not from Gauch et al. (2021) MTS-LSTM — verify
exact source and add full citation.*

**Key references**:
- Khadka, A., et al.: Evaluation of ERA5-Land and HARv2 Reanalysis Data at
  High Elevation in the Upper Dudh Koshi Basin (Everest Region, Nepal), J. Appl.
  Meteor. Climatol., 61(8), 931-954, doi:10.1175/JAMC-D-21-0091.1, 2022.
- Dalla Torre, D. and Di Marco, N.: Suitability of ERA5-Land reanalysis dataset
  for hydrological modelling in the Alpine region, J. Hydrol. Reg. Stud., 52,
  101718, doi:10.1016/j.ejrh.2024.101718, 2024.
- Wang, X., et al.: WRF-based dynamical downscaling of ERA5 reanalysis data for
  High Mountain Asia: Towards a new version of the High Asia Refined analysis,
  Int. J. Climatol., 41(S1), 743-762, doi:10.1002/joc.6686, 2021.
- Gauch, M., et al.: Rainfall-runoff prediction at multiple timescales with a
  single Long Short-Term Memory network, Hydrol. Earth Syst. Sci., 25,
  2045-2062, doi:10.5194/hess-25-2045-2021, 2021.

---

## 6.4 Train/Eval Split Strategies

### Temporal splitting (mandatory for time series)

Standard k-fold cross-validation is invalid for time series due to temporal
autocorrelation. Sequential temporal splits are mandatory. Recent work quantified
the leakage: 10-fold CV with pre-split sequence construction inflates
performance by up to 20.5% RMSE (Albelali and Ahmed, arXiv:2512.06932, 2025 —
general time-series methodology, not hydrology-specific, but directly
applicable).

**Recommended structure**:
- **Sequential holdout**: Train on earlier period, validate, test on latest.
  Example: Gauch et al. (2021): Train 1990-2003, Val 2003-2008, Test 2008-2018.
- **Post-split sequence construction**: Partition data first, then create LSTM
  input sequences. Never construct sequences before splitting.
- **Buffer zone**: Include a gap between train and validation periods sized to
  at least the LSTM lookback window (180-365 days).
- **Water-year boundaries**: Split on hydrological year boundaries, not calendar
  years. Example: Kratzert et al.: Train WY1981-1995, Test WY1996-2010.

### Handling ensemble member count mismatch

The core problem: reforecasts have fewer members (5-11) than operational
forecasts (21-51). Strategies from the literature:

| Strategy | Description | Source |
|---|---|---|
| **Per-member processing** | Train deterministic model on reanalysis; at deployment, process each ensemble member independently | AIFL (Taccari et al., 2026) |
| **Summary statistics** | Train on ensemble mean/spread, invariant to member count | Common in hydrology |
| **Fair CRPS** | Remove finite-ensemble-size bias from evaluation scores | Ferro (QJ RMS, 2014) |
| **Few members suffice** | 4 training members sufficient; larger ensembles at inference only | AIFS-CRPS (Lang et al., 2026) |
| **Member-as-sample** | Treat each member as a separate training example | Sharma et al. (J. Hydroinformatics, 2023) |
| **PoET** | Ensemble-size-agnostic transformer post-processing | Ben Bouallègue et al. (AIES, 2024) |

The AIFL strategy (train on reanalysis, fine-tune on NWP control, deploy
per-member) sidesteps the member count problem entirely and is the most
practical approach for systems like SAPPHIRE where no NWP reforecast exists.

**CRAAB**:
- *Research gap*: No study directly addresses the ICON-CH2-EPS situation (21
  operational members, no reforecast). The AIFL per-member strategy is validated
  for deterministic IFS only, not convection-permitting ensembles.
- *Assumption*: Ensemble members are exchangeable. Holds for perturbed members
  but not for control vs perturbed.
- *Key finding*: AIFS-CRPS showed 4 training members suffice — promising but
  unvalidated for hydrological applications.

### The reanalysis-to-NWP domain shift

AIFL (Taccari et al., 2026) quantified this directly:
- Performance degrades substantially when ERA5-Land-trained model is applied to
  IFS without fine-tuning (exact NSE drop TBC — verify against paper tables).
- Normalised Wasserstein distance between ERA5-Land and IFS precipitation is
  substantial (median 0.045, extreme cases 0.638).
- Two-stage training (pre-train ERA5-Land 40 years, fine-tune IFS 2016–2019)
  recovers skill to median KGE' 0.66 (median NSE 0.53 on 2021–2024 test set).
- Fine-tuning primarily rescues poorly-performing basins (44.7% improved, 22.7%
  minor degradation).

**Implication for SAPPHIRE**: The two-stage strategy (pre-train on
ERA5-Land/SwissMetNet observations, fine-tune on ICON-CH2-EPS once sufficient
operational data accumulates) is directly supported by the AIFL precedent. The
fine-tuning period can be short (~4 years) relative to pre-training (40 years).

**Key references**:
- Ferro, C. A. T.: Fair scores for ensemble forecasts, Q. J. Roy. Meteor. Soc.,
  140, 1917-1923, doi:10.1002/qj.2270, 2014.
- Taccari, M. L., et al.: AIFL: A Global Daily Streamflow Forecasting Model
  Using Deterministic LSTM Pre-trained on ERA5-Land and Fine-tuned on IFS,
  arXiv:2602.16579, 2026.
- Sharma, S., Ghimire, G. R., and Siddique, R.: Machine learning for
  postprocessing ensemble streamflow forecasts, J. Hydroinformatics, 25(1),
  126-139, doi:10.2166/hydro.2022.114, 2023.
- Lang, S., et al.: AIFS-CRPS: ensemble forecasting using a model trained with
  a loss function based on the continuous ranked probability score, npj Artif.
  Intell., 2, 18, doi:10.1038/s44387-026-00073-7, 2026.
- Ben Bouallègue, Z., et al.: Improving Medium-Range Ensemble Weather Forecasts
  with Hierarchical Ensemble Transformers, Artif. Intell. Earth Syst., 3(1),
  e230027, doi:10.1175/AIES-D-23-0027.1, 2024.
- Albelali, S. and Ahmed, M.: Hidden Leaks in Time Series Forecasting: How Data
  Leakage Affects LSTM Evaluation, arXiv:2512.06932, 2025.

---

## 6.5 Cross-Cutting CRAAB Summary

### Systemic biases across all datasets
1. **No ensemble NWP forcing in any dataset.** Every dataset uses reanalysis or
   observations. ML models cannot be directly trained on NWP ensemble input
   without adding your own NWP data.
2. **Northern Hemisphere, temperate bias.** Most data from US, Europe, Australia.
   Tropical, monsoon, and high-mountain catchments severely underrepresented.
3. **Reanalysis smoothing.** ERA5-Land and NLDAS-2 systematically underestimate
   precipitation extremes — the conditions that matter most for flood
   forecasting.
4. **Rating curve uncertainty unquantified.** All datasets report discharge from
   rating curves but none quantify conversion uncertainty, which is largest
   during floods.
5. **Water level as primary observable is neglected.** Only CAMELS-GB v2 has
   hourly stage. For operational flood warning (threshold-on-level), this is a
   critical gap.
6. **No sub-hourly benchmark.** USGS NWIS provides raw 15-min data for ~10,000+
   stations, but no curated benchmark with matched forcing exists.

### Confirmed research gaps
1. No curated sub-hourly benchmark dataset
2. No NWP ensemble forcing included in any benchmark dataset
3. No publicly available convection-permitting NWP reforecast (ICON-CH2-EPS)
4. No AI weather model reforecast archive publicly shared
5. No standardised train/eval protocol for ensemble streamflow ML experiments
6. No dataset for Nepal/Himalayan catchments at any resolution
7. No validation of member count mismatch strategies for hydrological ML

### Verification TODOs
- [ ] Confirm CAMELSH v2 includes water level (agent reports no; outline claims
  yes — check the actual dataset)
- [x] Check CAMELS-GB v2 review status — still preprint as of March 2026
- [x] Verify HydroCH catchment count (291) and temporal coverage — confirmed
- [ ] Check whether ECMWF open-data policy extends to reforecast archive
- [ ] Confirm GEFS reforecast member count details (5 daily / 11 weekly flagged
  as potentially incorrect — may be 5 throughout; verify against paper Table 1)
- [ ] Verify AIFS-CRPS "4 members suffice" finding — check exact claim in Lang
  et al. (npj AI, 2026)
- [ ] Search for any post-May-2025 sub-hourly curated dataset publications
- [x] Check CAMELS-SPAT hourly forcing product identities — 4 products: RDRS
  v2.1, ERA5, EM-Earth (all hourly), Daymet v4 R1 (daily)
- [ ] Find exact source for NLDAS-2 vs ERA5 NSE comparison (0.71 vs 0.54) —
  likely from Front. Water, 2023 (doi:10.3389/frwa.2023.1166124), not Gauch
  et al. 2021. Add full citation.
- [ ] Verify AIFL exact NSE values for domain shift (0.58/0.33 unconfirmed —
  aligned with §5 TBC flag)
- [ ] Verify Khadka et al. (2022) "spectacular overestimation" phrasing — needs
  full-text check
- [ ] Confirm ERA5-Land RMAE 37% source — Austrian Alpine headwaters study
  (Water, 2025) uses ERA5 not ERA5-Land; verify distinction
