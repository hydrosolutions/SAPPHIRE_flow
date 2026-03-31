# 4. Sub-Hourly Resolution: Value and Limits

Literature review for Section 4 of the Paper 0 outline.
Last updated: 2026-03-31.

## Key Findings

1. **Sub-hourly ML streamflow forecasting is virtually unexplored.** No
   large-sample study tests ML models at 15-min or finer resolution. The vast
   majority of ML hydrology operates at daily resolution (CAMELS benchmark
   legacy); a handful of papers reach hourly (Gauch et al., HESS 2021; Lees
   et al., HESS 2021). The hourly-to-sub-hourly transition is a genuine
   research gap.
2. **Catchment size determines where sub-hourly adds value.** Below ~25 km^2,
   concentration times fall under 1 hour and sub-hourly data is essential.
   Between 25-100 km^2, the benefit is catchment-specific (slope, urbanisation,
   storm type). Above ~100 km^2, hourly is generally sufficient; above ~500 km^2,
   daily often suffices (Ficchi et al., J. Hydrol., 2016; Gaal et al., WRR,
   2012).
3. **No rigorous evidence supports the claim that 15-min resolution "doubles
   effective lead time."** The argument is plausible from first principles but
   no published study quantifies it. Radar nowcasting systems (ERICHA, AIGA)
   operate at 15-min update cycles but the improvement is not characterised as
   a lead-time doubling.
4. **Rating curve uncertainty at high flows (15-40%, up to 43% in mountains)
   may mask the benefit of fine-resolution discharge forecasting.** If training
   and evaluation data have 20-40% uncertainty at peaks, a 10% improvement in
   peak timing from sub-hourly resolution is invisible within the error
   envelope. This argues for predicting stage directly.
5. **Water level (stage) prediction via ML is a growing field but lacks a
   large-sample comparison with discharge prediction.** Stage avoids rating
   curve uncertainty, is directly observable, and operationally relevant
   (warning thresholds are often in stage). CAMELSH (2025) now provides both
   stage and discharge at hourly resolution for 5,188+ US basins.
6. **No sub-hourly benchmark dataset exists.** All large-sample hourly datasets
   (CAMELSH, LamaH-CE) stop at 1-hour resolution. Researchers must construct
   their own datasets from raw gauge archives. CAMELS-CH is daily only.
7. **ML-based temporal disaggregation is emerging** (SpateGAN for
   ERA5 -> 2 km/10 min, LSTM for daily -> half-hourly) **but none has been
   tested on NWP forecast fields or coupled to hydrological models.**
8. **The NWP temporal mismatch problem is under-discussed.** MTS-LSTM handles
   daily+hourly natively but was only tested in simulation mode with observed
   forcing. No study tests whether discharge autoregression can interpolate
   sub-NWP-timestep dynamics. No study couples ensemble NWP with sub-hourly
   ML streamflow.

---

## 4.1 When Does Sub-Hourly Resolution Add Value?

### Catchment size and concentration time

The relationship between catchment area and flood response time is the primary
determinant of required temporal resolution.

| Catchment area | Typical T_c | Required resolution | Evidence |
|---|---|---|---|
| < 10 km^2 | < 1 h | Sub-hourly (5-15 min) | Strong (urban hydrology, flash flood lit) |
| 10-25 km^2 | 1-3 h | Hourly or finer | Moderate (UK FSR, flash flood studies) |
| 25-100 km^2 | 2-10+ h | Hourly likely sufficient | Moderate (Ficchi 2016, Gaal 2012) |
| 100-500 km^2 | 7-150 h | Hourly; daily often adequate | Strong (Ficchi 2016, 240 catchments) |
| > 500 km^2 | > 12 h | Daily often sufficient except for peak timing | Strong |

There is no single clean threshold. Gaal et al. (2012) showed that for a
100 km^2 catchment, flood timescales range from 7 to 150 hours depending on
climate type, soil properties, and land use. The variability within a given
catchment size class is as large as the variability between classes.

**CRAAB**:
- *Claim*: Sub-hourly matters below ~25 km^2. Plausible and consistent across
  multiple sources.
- *Research gap*: No large-sample study rigorously tests the hourly-to-sub-hourly
  transition for ML models. Ficchi et al. (2016) — the most rigorous temporal
  resolution study (2,400 events, 240 French catchments) — only goes down to
  1-hour resolution and uses conceptual models, not ML.
- *Assumption*: Concentration time is the right proxy for required resolution.
  This ignores other factors: NWP forcing resolution, data quality, model
  architecture capacity.
- *Ambiguity*: "Sub-hourly" conflates 30-min, 15-min, 10-min, 5-min. The
  optimal resolution within the sub-hourly band is unknown.
- *Bias*: European and US focus. Tropical/monsoon catchments (relevant for Nepal
  v1) are underrepresented.

**Key references**:
- Ficchi, A., Perrin, C., and Andreassian, V.: Impact of temporal resolution
  of inputs on hydrological model performance: An analysis based on 2400 flood
  events, J. Hydrol., 538, 454-470, doi:10.1016/j.jhydrol.2016.04.016, 2016.
- Gaal, L., et al.: Flood timescales: Understanding the interplay of climate
  and catchment processes through comparative hydrology, Water Resour. Res., 48,
  doi:10.1029/2011WR011509, 2012.

### Diminishing returns at finer resolution

Ficchi et al. (2016) provide the most systematic evidence on diminishing returns:
- Daily -> 6-hourly: **large improvement** in flood simulation accuracy.
- 6-hourly -> hourly: **moderate improvement**, primarily for peak flow precision.
- Hourly -> sub-hourly: **no large-sample evidence** for or against.

The gap is precisely in the hourly-to-sub-hourly transition. The only evidence
comes from process-based models (GR family); ML models may behave differently
because they can potentially exploit higher-frequency information through learned
temporal patterns.

**CRAAB**:
- *Claim*: Diminishing returns set in below 6-hourly for average metrics.
  Well-supported for conceptual models on meso-scale catchments.
- *Research gap*: Does the same diminishing-returns pattern hold for ML models?
  ML models may extract information from high-frequency patterns that conceptual
  models cannot represent.
- *Assumption*: Average performance metrics capture the relevant signal. Peak
  flow metrics continue to improve at finer resolution even when average metrics
  plateau — for flood forecasting, peaks are what matter.
- *Bias*: Rainfall temporal resolution was found to matter more than spatial
  resolution for flood peaks (HESS 2019 sensitivity study), suggesting the
  temporal dimension deserves more attention.

---

## 4.2 The NWP Temporal Mismatch Problem

### The core challenge

Operational NWP ensemble products deliver forcing at 1-6 hourly resolution
(ICON-CH2-EPS: hourly; GEFS: 3-hourly; ECMWF ENS: 3-hourly; TIGGE: 6-hourly),
but flash-flood-relevant streamflow dynamics occur at 15-min timescales. The
question: **can ML models learn sub-NWP-timestep dynamics, or must the forcing
be disaggregated first?**

### MTS-LSTM: the closest architecture

Gauch et al. (HESS, 2021) developed the Multi-Timescale LSTM, which processes
long-past inputs at daily resolution and recent inputs at hourly resolution in
a branched architecture with shared cell state transfer. This is the closest
existing architecture to the SAPPHIRE Flow problem.

**Limitations**:
1. Information flows coarse-to-fine only (not bidirectional).
2. Tested only in **simulation mode** with observed forcing — not with NWP.
3. No uncertainty quantification or ensemble prediction.
4. Not tested with forcing that degrades in temporal resolution with lead time
   (as real NWP does — e.g., GEFS switches from hourly to 3-hourly at day 5).
5. Not tested outside CAMELS-US basins.
6. Does not extend below hourly resolution.

The architecture is conceptually well-suited for the 3-hourly NWP + 15-min
streamflow problem but would require significant adaptation and validation.

Jahangir and Quilty (WRR, 2025) extended the multi-timescale idea with
hierarchical deep learning (HDL) that enforces consistency between timescales
via temporal hierarchical reconciliation. However, this addresses daily/weekly
consistency, not sub-daily scales.

**CRAAB**:
- *Claim*: MTS-LSTM handles multi-resolution input effectively. Supported for
  daily+hourly with observed forcing.
- *Research gap*: Extension to NWP forcing, ensemble input, and sub-hourly
  output is entirely untested.
- *Assumption*: Coarse-to-fine information transfer via cell state is sufficient.
  Fine-to-coarse feedback might also be valuable.
- *Ambiguity*: Would a third timescale branch (e.g., 15-min for recent 6 hours)
  help? No evidence either way.
- *Bias*: CAMELS-US catchments (natural, mid-latitude, > 50 km^2) — exactly the
  catchments where sub-hourly is least needed.

**Key references**:
- Gauch, M., Kratzert, F., Klotz, D., Nearing, G., Lin, J., and Hochreiter, S.:
  Rainfall-runoff prediction at multiple timescales with a single Long Short-Term
  Memory network, Hydrol. Earth Syst. Sci., 25, 2045-2062,
  doi:10.5194/hess-25-2045-2021, 2021.
- Jahangir, M. S. and Quilty, J.: Hierarchical deep learning for consistent
  multi-timescale hydrological forecasting, Water Resour. Res., 61,
  e2024WR038105, doi:10.1029/2024WR038105, 2025.

### Autoregressive discharge feedback

Nearing et al. (HESS, 2022) showed that feeding observed discharge back as an
LSTM input feature outperforms variational data assimilation for daily models —
simpler, faster, and more accurate. However:
- Only tested at daily scale with observed forcing.
- In forecast mode beyond the current time, the model must use its own
  predictions (no observed discharge), and performance degrades.
- The key question — **can sub-daily discharge autoregression compensate for
  temporally coarse NWP forcing?** — remains unanswered.

Lees et al. (HESS, 2022) showed LSTMs learn internal representations correlated
with unmeasured hydrological stores (soil moisture, SWE), suggesting the network
develops an implicit catchment state model. Whether this learned state is robust
enough to interpolate between NWP timesteps is unknown.

**CRAAB**:
- *Claim*: Autoregression is effective for data assimilation in daily LSTM
  models. Well-supported.
- *Research gap*: Whether autoregressive discharge can interpolate sub-NWP-timestep
  dynamics is a genuine open question — plausible but empirically untested.
- *Assumption*: If the LSTM has an internal catchment state representation,
  recent discharge observations could anchor the trajectory between NWP
  timesteps. Theoretical only.
- *Ambiguity*: The interaction between autoregressive feedback and ensemble NWP
  spread is unknown — does autoregression collapse ensemble diversity?

**Key references**:
- Nearing, G. S., et al.: Technical note: Data assimilation and autoregression
  for using near-real-time streamflow observations in long short-term memory
  networks, Hydrol. Earth Syst. Sci., 26, 5493-5513,
  doi:10.5194/hess-26-5493-2022, 2022.
- Lees, T., et al.: Hydrological concept formation inside long short-term memory
  (LSTM) networks, Hydrol. Earth Syst. Sci., 26, 3079-3101,
  doi:10.5194/hess-26-3079-2022, 2022.

### Temporal disaggregation of forcing

Three emerging approaches, none tested on NWP forecast fields:

1. **SpateGAN** (Glawion et al., Earth Space Sci., 2023; npj Clim. Atmos. Sci.,
   2025): Conditional GAN for spatio-temporal downscaling of ERA5 from
   24 km/1 h to 2 km/10 min. First global-scale deep learning precipitation
   downscaling. Stochastic — each realisation gives a different sub-hourly
   pattern, which is conceptually attractive for ensemble forecasting.
2. **LSTM disaggregation** (Oates et al., Stoch. Environ. Res. Risk Assess.,
   2025): Daily-to-half-hourly precipitation disaggregation with guaranteed
   conservation of daily totals. First ML method for this granularity.
3. **Schaake shuffle / BJP** (Robertson et al., J. Hydrometeorol., 2020):
   Statistical approach producing calibrated hourly ensemble precipitation from
   daily observations using NWP temporal templates.

**CRAAB** (cross-cutting):
- *Claim*: ML can disaggregate precipitation to sub-hourly resolution. Supported
  for observed/reanalysis data.
- *Research gap*: None tested on NWP forecast fields, which have different error
  structures (systematic biases, smoothed extremes) than coarsened observations.
  None coupled to a hydrological model to demonstrate downstream benefit.
- *Assumption*: Patterns learned from observations/reanalysis transfer to NWP
  forecast mode. Non-trivial — NWP errors differ fundamentally from coarsening
  artefacts.
- *Ambiguity*: How to integrate stochastic disaggregation (SpateGAN) with
  ensemble NWP — does it add useful diversity or noise?
- *Bias*: SpateGAN trained on German radar (exceptionally dense network);
  performance in radar-sparse regions unknown.

**Key references**:
- Glawion, L., Polz, J., Kunstmann, H., and Schmid, A.: spateGAN:
  Spatio-temporal downscaling of rainfall fields using a cGAN approach, Earth
  Space Sci., 10, e2023EA002906, doi:10.1029/2023EA002906, 2023.
- Glawion, L., et al.: Global spatio-temporal ERA5 precipitation downscaling
  to km and sub-hourly scale using generative AI, npj Clim. Atmos. Sci., 8,
  103, doi:10.1038/s41612-025-01103-y, 2025.
- Oates, H., et al.: A long short-term memory model for sub-hourly temporal
  disaggregation of precipitation, Stoch. Environ. Res. Risk Assess., 39,
  doi:10.1007/s00477-025-02996-0, 2025.
- Robertson, D. E., Shrestha, D. L., and Wang, Q. J.: Calibrating hourly
  precipitation forecasts with daily observations, J. Hydrometeorol., 21(7),
  1655-1673, doi:10.1175/JHM-D-19-0246.1, 2020.

---

## 4.3 Water Level vs Discharge Prediction

### The rating curve problem

Rating curve uncertainty at high flows undermines fine-resolution discharge
forecasting. Summary of evidence:

| Source | Context | High-flow uncertainty |
|---|---|---|
| McMillan et al. (2012) | Meta-review, global | +/-15-40% |
| Coxon et al. (2015) | 500 UK stations | +/-13-25% (95% CI) |
| Frontiers in Water (2023) | 3 mountainous sites | 11.9-43% |

At high flows, extrapolation beyond the gauged range is needed in approximately
2 out of 3 years of record (Pappenberger et al., HSJ, 2010). Extrapolation
introduces even larger errors than interpolation.

**Implication**: If observations have 15-40% uncertainty at peaks, the benefit
of resolving discharge dynamics at 15-min resolution may be unverifiable. A
forecast that captures a 10% improvement in peak timing is invisible within
the observation error envelope.

### ML for water level prediction

A growing body of work predicts stage directly:
- **Google HydroNets** (2024): Implicitly separates generalizable rainfall-runoff
  from site-specific rating curve learning.
- **Bui et al.** (Environ. Sci. Europe, 2023): LSTM outperforms MLP for water
  level prediction in lowland rivers.
- **Wang et al.** (J. Hydrol., 2020): In high-sediment rivers, direct water
  level prediction is the only viable approach (no stable stage-discharge
  relationship).
- **Nkiaka et al.** (Sci. Total Environ., 2024): ML water level prediction
  advantageous in tidal/estuarine contexts with non-unique stage-discharge.

**Advantages of stage prediction**:
1. Avoids rating curve uncertainty (stage measurement: typically +/-1-3 cm).
2. Directly observable — stage is measured; discharge is always derived.
3. Operationally relevant — flood warning thresholds often defined in stage.
4. Simpler target distribution — bounded, smoother than discharge.

**Disadvantages**:
1. Cross-section specific — predictions valid only at the measurement location.
2. Not transferable across stations (unlike discharge).
3. Most hydrological modelling tradition and process understanding built around
   discharge.

**CRAAB**:
- *Research gap*: No large-sample systematic comparison of stage-first vs
  discharge-first ML prediction. CAMELSH (2025) now provides both variables
  at hourly resolution for 5,188+ US basins, enabling such a comparison.
- *Assumption*: That rating curve uncertainty is the dominant error source
  undermining fine-resolution discharge forecasting. May not hold where rating
  curves are stable and well-constrained.
- *Ambiguity*: How to evaluate stage forecasts across diverse stations — no
  universal stage-based performance metric exists (unlike NSE/KGE for discharge).

**Key references**:
- McMillan, H., et al.: Benchmarking observational uncertainties for hydrology,
  Hydrol. Process., 26, 4078-4111, doi:10.1002/hyp.9524, 2012.
- Coxon, G., et al.: A novel framework for discharge uncertainty quantification
  applied to 500 UK gauging stations, Water Resour. Res., 51, 5531-5546,
  doi:10.1002/2014WR016532, 2015.
- Pappenberger, F., et al.: Deriving rating curves using remotely sensed
  inundation data, Hydrol. Sci. J., 55(4), doi:10.1080/02626667.2010.504186,
  2010.

---

## 4.4 Available Sub-Daily Datasets

| Dataset | Region | Resolution | N catchments | Key limitation |
|---|---|---|---|---|
| CAMELSH (Tran et al., Sci. Data, 2025) | CONUS | Hourly | 5,188+ | US only; no sub-hourly; missing values |
| LamaH-CE (Klingler et al., ESSD, 2021) | Central Europe | Hourly | 859 | PET issues; Alpine complexity; no sub-hourly |
| CAMELS-CH (Hoege et al., ESSD, 2023) | Switzerland | Daily | 331 | Daily only; hourly extension planned |
| CAMELS-GB v2 | Great Britain | Hourly | 671 | UK only |
| Caravan (Kratzert et al., Sci. Data, 2023) | Global | Daily | 6,830 | Daily only |
| USGS NWIS (raw) | US | 15-min | ~5,000+ | Raw; not packaged for ML benchmarking |
| BAFU (raw) | Switzerland | 10-min | ~200+ | Raw; not packaged for ML; SMN stations co-located |

**Critical gap**: No large-sample sub-hourly benchmark dataset exists anywhere.
All hourly datasets stop at 1-hour resolution. Any claim about the value of
15-min resolution in ML streamflow forecasting cannot be validated against
standardised benchmarks. Researchers must construct their own datasets from raw
gauge archives — exactly what SAPPHIRE v0 will do with BAFU/SMN data.

**CRAAB**:
- *Bias*: The CAMELS daily benchmark created a path dependency — researchers
  use what benchmarks exist, so daily resolution dominates.
- *Research gap*: No sub-hourly ML benchmark equivalent to CAMELS.
- *Assumption*: Using daily mean discharge implicitly assumes peak timing and
  magnitude (sub-daily phenomena) are not the forecasting target — problematic
  for flood warning.

**Key references**:
- Tran, T., et al.: CAMELSH: A Large-Sample Hourly Hydrometeorological Dataset
  and Attributes at Watershed-Scale for CONUS, Sci. Data,
  doi:10.1038/s41597-025-05612-6, 2025.
- Klingler, C., Schulz, K., and Herrnegger, M.: LamaH-CE: LArge-SaMple DAta
  for Hydrology and Environmental Sciences for Central Europe, Earth Syst. Sci.
  Data, 13, 4529-4565, doi:10.5194/essd-13-4529-2021, 2021.
- Hoege, M., et al.: CAMELS-CH: hydro-meteorological time series and landscape
  attributes for 331 catchments in hydrologic Switzerland, Earth Syst. Sci. Data,
  15, 5755-5784, doi:10.5194/essd-15-5755-2023, 2023.
- Kratzert, F., et al.: Caravan — A global community dataset for large-sample
  hydrology, Sci. Data, doi:10.1038/s41597-023-01975-w, 2023.

---

## 4.5 Flash Flood Forecasting with ML

ML for flash floods is a growing subfield, but temporal resolution remains
coarse relative to the phenomenon:

- **Lees et al. (HESS, 2021)**: Benchmarked LSTM against four lumped conceptual
  models for 669 GB catchments at hourly resolution. One of the few non-daily ML
  benchmarks. Did not test sub-hourly.
- **Hu et al. (various, 2020-2023)**: Multiple papers on LSTM for flash flood
  prediction in Chinese catchments, sometimes at hourly resolution. Basin sizes
  and definitions of "flash flood" vary widely.
- **Frame et al. (2022-2023)**: Operational LSTM forecasting at US NWM scale.
  Check whether any experiments used sub-hourly data — likely not, as NWM
  operates at hourly output.

**CRAAB**:
- *Claim*: "LSTM outperforms conceptual models for flash floods" — a common
  claim but comparison baselines vary widely across papers.
- *Assumption*: Many flash flood ML papers assume radar rainfall at matching
  temporal resolution, which is not universally available operationally.
- *Ambiguity*: "Flash flood" is inconsistently defined — sometimes by catchment
  size, sometimes by response time, sometimes by impact severity.
- *Bias*: Publication bias toward positive results; failed ML applications for
  flash floods are rarely published.

---

## 4.6 Cross-Cutting CRAAB Summary

### Claims to verify
- [ ] Sub-hourly resolution adds value below 25 km^2 — well-supported but
  untested with ML
- [ ] 15-min resolution doubles effective lead time — no quantitative evidence
  found; treat as informal/aspirational
- [ ] Rating curve uncertainty masks sub-hourly benefit — logically sound,
  not yet empirically demonstrated for ML forecasts

### Confirmed research gaps
1. No ML model tested at sub-hourly resolution with ensemble NWP forcing
2. No large-sample sub-hourly benchmark dataset
3. No systematic comparison of stage vs discharge prediction with ML
4. No study on whether discharge autoregression compensates for coarse NWP
5. No temporal disaggregation tested on NWP forecast fields (only reanalysis)
6. No evidence on the hourly-to-sub-hourly transition for ML models

### Key assumptions to challenge in Paper 2
- Daily/hourly resolution is sufficient for flood forecasting
- Rating curve uncertainty is negligible at evaluation time
- NWP temporal resolution is a hard constraint (vs. learnable interpolation)
- Concentration time alone determines required resolution

### Biases in the literature
- CAMELS-daily path dependency: most ML hydrology benchmarks are daily
- Geographic bias: US and Europe dominate; tropical/monsoon catchments
  underrepresented
- Publication bias: failed sub-hourly experiments likely unpublished
- Catchment size bias: ML literature skews toward > 50 km^2

---

## Verification TODOs

- [ ] Verify Ficchi et al. (2016) exact findings on hourly-to-sub-hourly
  transition (or confirm they stop at hourly)
- [ ] Check whether Frame et al. (2022-2023) or NWM-related LSTM papers tested
  sub-hourly resolution
- [ ] Verify CAMELSH v2 (2025) includes both water level and discharge at hourly
- [ ] Check CAMELS-CH hourly extension status — is it published or still planned?
- [ ] Confirm USGS NWIS 15-min gauge count (~5,000+ is an approximation)
- [ ] Search for any sub-hourly ML streamflow papers published after May 2025
- [ ] Verify Gaal et al. (2012) catchment size vs flood timescale findings
- [ ] Check if Bennett et al. (2016) disaggregation findings have been
  replicated with ML models
- [ ] Verify Google HydroNets architecture details re stage vs discharge
