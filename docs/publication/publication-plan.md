---
status: DRAFT
---

> **DRAFT** — This document has not completed the review maturity gate.

# SAPPHIRE Flow — Publication Plan

Living document, refined as work progresses.
Last revised: 2026-03-09 (major revision based on systematic literature research).

## Motivation

SAPPHIRE Flow sits at an intersection that is underserved in the literature:
**sub-hourly ensemble streamflow forecasting + ML/DL models + operational
deployment in resource-constrained settings**. The v0->v1->v2->v3 trajectory
(Swiss->Nepal->rating curves + tooling->Central Asia) provides a natural
experimental arc.

### Gaps confirmed by literature research (2026-03-09)

| Gap | Evidence | Competition risk |
|-----|----------|-----------------|
| **Sub-hourly + ensemble NWP + ML** (all three together) | No published work combines these. Most ML papers use observed forcing or deterministic NWP; most sub-daily papers don't use ensembles. | Low -- Google is daily, EFAS uses process models |
| **15-min resolution ML streamflow forecasting** | Entire ML hydrology field is at daily (dominant) or hourly (emerging). Sub-hourly is unexplored. Value likely strongest for small/flashy catchments (<100 km2, response time <3h); may not add value for large catchments. | Very low |
| **Three paradigms of uncertainty generation compared** | NWP pass-through (EFAS standard), mixture density networks (best single-model method per Klotz et al. 2022), deep ensembles (standard DL, untested head-to-head in hydrology) -- never compared for streamflow | Low |
| **Water level vs. discharge as ML target** | No systematic comparison exists for flood forecasting. Rating curve uncertainty at high flows (20-30%+) means stage-based alerting may be more reliable. | Very low |
| **Sub-hourly transfer learning** | Google (Nature 2024) showed daily transfer works globally. Sub-hourly is completely untested. | Medium -- Google could extend |
| **Operational ML system run BY a national hydromet** | Google/ECMWF run it for you. No published transferable package for self-hosted deployment. | Low |

### What's already well-covered (avoid as primary contribution)

- Daily LSTM streamflow forecasting (saturated -- hundreds of papers)
- Daily transfer learning to ungauged basins (Nevo et al., Nature 2024)
- Ensemble NWP -> process-based model -> streamflow (EFAS/GloFAS since 2012)
- ML post-processing of ensemble forecasts (active but crowded)
- Probabilistic DL uncertainty methods individually (deep ensembles, quantile
  regression, MDN -- mature, though no unified hydrology benchmark exists)

---

## Paper Portfolio

Papers are numbered by their original planning order. Paper 4 (operational
decision support) was dropped as too speculative -- it can be revisited if
Nepal deployment produces interesting operational data post-2027.

### Paper 0 -- Literature Review (working document → publication TBD)

**Working title**: "What is missing for operational sub-daily ensemble flood
forecasting with machine learning?"

**Type**: Working literature review. Publication decision deferred until the
review is complete. Options:

| Option | Format | Cost | Notes |
|--------|--------|------|-------|
| Paper 2 introduction | ~3,000 words integrated | $0 | Natural home for this material |
| WRR Commentary | ~2,000 words, 1–2 figs | $0 | Free for commentaries, peer-reviewed, high-impact |
| Both | Commentary for framing + Paper 2 intro for depth | $0 | Best reach |

**Core contribution**:
- Synthesizes the current landscape: operational systems (Google Flood Hub,
  EFAS/GloFAS, Israel), ML architectures (LSTM, TFT, Mamba), uncertainty
  methods (MDN/CMAL, deep ensembles, quantile regression), and sub-daily
  datasets (CAMELSH, LamaH-CE)
- Identifies the three-paradigm gap as the central open question (NWP
  pass-through vs. learned distribution vs. deep ensemble for streamflow)
- Points to sub-hourly + ensemble NWP + ML as the underserved intersection
- Validates (or invalidates) Paper 2's experimental design against the
  literature

**Framing note**: Describe SAPPHIRE Flow as a research platform under
development; do not make performance claims. Frame around the literature gap,
not the system.

**Timing**: Write immediately (Q2 2026). No results needed -- pure literature
synthesis. Feeds directly into Paper 2 regardless of standalone publication
decision.

**Key literature to position against**:
- Klotz et al. (HESS 2022) -- uncertainty estimation with DL for rainfall-runoff
  (GMM vs CMAL vs UMAL vs MC Dropout; no deep ensembles, no ensemble NWP)
- Nevo et al. (2022) ML in operational flood forecasting (HESS)
- Nearing et al. (2024) Global prediction of extreme floods (Nature) [NOT Nevo]
- ECMWF AIFS-CRPS (Lang et al., 2024/2026) -- CRPS-trained ensemble for weather
- MTS-LSTM (Gauch et al., HESS 2021) -- multi-timescale architecture
- TFT for streamflow (Rasiya Koya & Roy, JoH 2024)
- RiverMamba (NeurIPS 2025) -- state-space models emerging
- Busker et al. (2025) -- cross-country FEWS comparison
- Modi et al. (2025) -- DL in ensemble streamflow forecasting (JAMES)
- FutureTST (Ambika, GRL 2025) -- transformer with future forcing
- CAMELSH (Scientific Data 2025) -- 3,166 US catchments hourly

---

### Paper 2 -- Flagship Methods Paper

**Working title**: "Sub-hourly probabilistic streamflow forecasting from ensemble
NWP: comparing uncertainty paradigms across architectures and resolutions"

**Type**: Research article

**Target venue**: HESS or WRR

**Core contribution**:
- First systematic comparison of three fundamentally different approaches to
  probabilistic ensemble streamflow forecasting with ML at sub-hourly resolution
- Multi-factor experimental design: 5 architectures x 3 uncertainty paradigms x
  4 temporal resolutions x 2 datasets, with bias correction as post-processing
  sub-analysis
- Answers: where does model complexity stop paying off? Which uncertainty
  paradigm produces the best-calibrated probabilistic forecasts? At what
  resolution does NWP forcing become the bottleneck?

**Timing**: After v0b (sub-daily algorithm R&D) produces results. Core research
paper -- this is where the science lives.

#### Experimental Design

##### Factor 1: Model Architecture (5 + 1 stretch)

| Model | Type | Multi-resolution? | Notes |
|-------|------|-------------------|-------|
| Ridge regression | Simple linear baseline | No (3-hourly aggregated I/O) | "How much does DL actually add?" |
| HBV/GR4J | Process-based baseline | No (hourly timestep) | Traditional hydrology reference |
| MTS-LSTM | DL baseline | Yes (native dual-branch) | NeuralHydrology, operational standard |
| TFT | DL + attention | Yes (separate coarse/fine encoder branches) | Explainability, recurrence + attention |
| HBV->LSTM | Physics hybrid | Yes (HBV hourly, LSTM fine) | Process states as additional LSTM inputs |
| *Differentiable HBV* | *Stretch goal* | *Yes* | *HBV with learnable parameters, end-to-end gradient descent* |

**Post-processing sub-analysis** (applied to the best-performing
architecture x paradigm combination, not a factorial dimension):
- **Bias correction layer**: secondary model (linear, quantile mapping, or
  lightweight NN) trained on residuals. Mirrors NWP post-processing (EMOS)
  and tests whether cheap correction closes residual gaps. Evaluated as a
  supplement to the main results.

The hybrid variants test two distinct approaches to physics-informed ML:
1. **HBV->LSTM**: Run HBV at hourly timestep to produce intermediate states
   (soil moisture, snowpack, routing state). Feed these as additional features
   to the fine-resolution LSTM branch alongside raw meteorological forcing.
   HBV states are interpolated to 15-min via nearest-neighbor for the LSTM
   branch. Tests whether process knowledge improves ensemble calibration.
2. **Differentiable HBV** (stretch): HBV with learnable parameters optimized
   end-to-end via gradient descent. More ambitious; NeuralHydrology has
   experimental support.

##### Factor 2: Uncertainty Paradigm (3)

| Paradigm | Mechanism | Uncertainty source | NeuralHydrology implementation |
|----------|-----------|-------------------|-------------------------------|
| **A: NWP pass-through** | Each NWP ensemble member -> single deterministic model (MSE loss) -> N streamflow members | Forcing uncertainty (from NWP) | Train 1 model with MSE, run N times with different forcing members |
| **B: CMAL (Mixture Density)** | Single model with CMAL output head, trained with CMALLoss on ensemble-mean NWP | Learned predictive distribution (aleatoric) | Native CMAL head in NeuralHydrology (Klotz et al. 2022) |
| **C: Deep ensemble** | M=5-10 independently trained models (different random seeds), ensemble-mean NWP input, each with Gaussian output head | Model epistemic uncertainty | Train M NeuralHydrology models, aggregate predictions |

**Why these three**: Each paradigm makes a fundamentally different assumption
about where forecast uncertainty originates:
- **A** says: "uncertainty comes from the weather forecast"
- **B** says: "uncertainty comes from the predictive distribution the model
  learned from data" (best single-model method: CMAL > UMAL > GMM >> MC
  Dropout, per Klotz et al. HESS 2022)
- **C** says: "uncertainty comes from what the model doesn't know" (best
  general-ML method per Lakshminarayanan et al. 2017, but untested
  head-to-head against MDN in hydrology)

**Hybrid paradigms** (secondary analysis): A+B (run CMAL on each NWP member ->
N distributions) and A+C (run deep ensemble on each NWP member -> NxM members).
Tests whether combining uncertainty sources improves calibration.

**Research risk for Paradigm B**: CMAL has been validated for rainfall-runoff
(Klotz et al. 2022) but not with ensemble NWP input or at 15-min resolution.
CMAL can be "more brittle than GMM" (NeuralHydrology docs). Mitigation:
preliminary study on a small catchment subset before full matrix.

Not all architecture x paradigm combinations are meaningful:

| Architecture | A (pass-through) | B (CMAL) | C (deep ensemble) |
|-------------|:---:|:---:|:---:|
| Ridge regression | yes | -- | yes (M regressions) |
| HBV/GR4J | yes | -- | yes (param perturbation) |
| MTS-LSTM | yes | yes | yes |
| TFT | yes | yes | yes |
| HBV->LSTM | yes | yes | yes |
| *Differentiable HBV* | *yes* | *yes* | *--* |

~15 meaningful architecture x paradigm combinations.

##### Factor 3: Temporal Resolution (4)

| Resolution | Source | Notes |
|-----------|--------|-------|
| **15-min** | USGS NWIS raw instantaneous values (not CAMELSH hourly aggregation -- CAMELSH provides hourly only; 15-min requires direct NWIS pull) | Novel -- nobody in ML hydrology works at this resolution. Value likely strongest for catchments <100 km2 with response time <3h. |
| Hourly | LamaH-CE, CAMELS-GB v2 | Current emerging frontier in ML hydrology |
| 6-hourly | Aggregated from above | Matches many NWP output intervals |
| Daily | Aggregated from above | Current ML standard, baseline comparison |

##### Factor 4: Dataset (2)

| Dataset | Resolution | Catchments | Region | NWP ensemble (training) | NWP ensemble (evaluation) | Role |
|---------|-----------|------------|--------|------------------------|--------------------------|------|
| **USGS 15-min** | 15-min | See screening criteria below | US (CONUS) | GEFS v12 reforecast (5-11 mbr, 3-hourly, 2000-2019) | GEFS v12 operational (31 mbr, 3-hourly, 2017-present) | **Primary** |
| **LamaH-CE** | Hourly | 859 | Central Europe | TIGGE (51 mbr, 6-hourly, 2006-2017) | Same (TIGGE has sufficient members) | **European validation** |

**USGS data screening criteria** (applied before experiments):
- Minimum 15-min record length: 10+ years
- Maximum gap fraction: <10% of total record
- Ice-affected periods: masked (excluded from training and evaluation)
- Report how many stations survive screening (expect significant reduction
  from 3,166)

**NWP forcing strategy — training vs. evaluation split**: The GEFS v12
reforecast (2000-2019) has only 5 members (daily) / 11 members (weekly) --
too few for Paradigm A evaluation. Strategy:
- **Training period**: Use GEFS reforecast (5-11 members, 2000-2019) for all
  paradigms. For Paradigm A, the model learns from the available members; for
  Paradigms B and C, use ensemble-mean forcing.
- **Evaluation period**: Use GEFS v12 operational (31 members, 2017-present)
  for Paradigm A evaluation. This gives ~7 years of 31-member ensemble for
  proper probabilistic evaluation (CRPS, reliability, spread-skill). For
  Paradigms B and C, use ensemble-mean from the same period.
- **LamaH-CE**: TIGGE (51 members) is sufficient for both training and
  evaluation of all paradigms.

**NWP temporal mismatch strategy**: Architecture-specific handling:
- **MTS-LSTM**: Native multi-resolution input. Coarse NWP (3-hourly) enters
  the coarse branch; fine-resolution observations (15-min) enter the fine
  branch. Model learns to integrate across timescales internally. No
  interpolation needed.
- **TFT**: Separate coarse/fine encoder branches — 3-hourly NWP enters a
  coarse-resolution encoder, 15-min observations enter a fine-resolution
  encoder. Outputs at fine resolution via decoder.
- **HBV->LSTM**: HBV runs at hourly timestep (its typical operational
  resolution). HBV states (soil moisture, snowpack, routing) are interpolated
  to 15-min via nearest-neighbor for the fine-resolution LSTM branch.
- **HBV/GR4J standalone**: Runs at hourly timestep. For 3-hourly NWP
  precipitation: uniformly distribute the 3-hourly accumulation across
  sub-intervals (divide total by N). For rate variables (temperature, wind):
  hold 3-hourly value constant (step interpolation). This preserves mass
  conservation for precipitation.
- **Ridge regression**: Uses 3-hourly aggregated inputs and outputs.
  Sub-hourly resolution not applicable (inherent limitation, reported as such).

For ensemble handling across all architectures: apply the same deterministic
temporal processing to all ensemble members. Do not use stochastic
disaggregation per member (confounds ensemble spread with disaggregation
noise). Address ensemble calibration at the output stage.

##### Spatial Cross-Validation Design

**USGS (primary)**:
- **Spatial folds**: Leave-one-region-out using USGS HUC-2 regions (18 major
  hydrologic basins in CONUS). Each fold trains on 17 regions and evaluates
  on the held-out region. This tests geographic generalization.
- **Temporal split within each fold**: Last 20% of the training record
  reserved as temporal test set. 30-day warm-up period (= 2,880 timesteps at
  15-min) precedes each test period and is excluded from metric computation.
  Warm-up observations are drawn from the training partition to avoid leakage.
- **Alternative (sensitivity)**: Climate-based groupings (6 macro-regions
  following Knoben et al.) as a second spatial partitioning scheme.

**LamaH-CE (European validation)**:
- Leave-one-region-out by Austrian federal state or major sub-basin (Danube
  tributaries). Same temporal split and warm-up protocol.

##### Two-Stage Screening Design

Given the large experimental matrix (~15 paradigm x architecture combos x 4
resolutions x 2 datasets = ~120 configurations):

**Stage 1 (screening)**: Run all configurations on a representative subset of
~100 catchments (stratified by area, climate, and data completeness). Use a
single spatial fold. Evaluate CRPS and spread-skill ratio. This identifies
clearly uncompetitive configurations.

**Stage 2 (full matrix)**: Run promising configurations (those within 10% of
the best Stage 1 CRPS per architecture class) on the full catchment set with
full cross-validation. Report Stage 1 screening results transparently.

If computational capacity is not limiting, run the full matrix directly and
present the screening as a sensitivity analysis.

**Estimated compute**: ~15 configs x 4 resolutions x 2 datasets x 18 CV folds
x ~10 GPU-hours per training = ~21,600 GPU-hours for the full matrix.
Stage 1 screening reduces this to ~15 x 4 x 2 x 1 fold x 10h = ~1,200
GPU-hours. Priority ordering: MTS-LSTM and TFT first (highest expected value),
then HBV->LSTM, then ridge regression and HBV baselines.

##### Evaluation Metrics

| Metric | Purpose | Primary? |
|--------|---------|----------|
| NSE | Deterministic skill | Standard |
| KGE | Bias + variability + correlation | Preferred over NSE |
| **CRPS** | Probabilistic skill | **Primary** for paradigm comparison |
| **CRPS decomposition** (reliability + resolution + uncertainty) | Attributes CRPS differences to calibration vs. signal capture (Murphy decomposition, Gneiting & Raftery 2007) | **Primary** diagnostic |
| **Sharpness** | Mean ensemble spread, stratified by flow regime | Essential companion to reliability |
| Reliability diagrams | Ensemble calibration (visual) | Essential |
| **Spread-skill ratio** | Does ensemble spread match actual error? | **Key diagnostic** for paradigm A/B/C |
| Brier score (exceedance) | Alert-relevant probabilistic skill | Connects to operational alerting |
| **Economic value score** | Decision value at alert thresholds (watch 20%, warning 50%, danger 80%) | Operational relevance (Richardson 2000) |
| Lead-time degradation curves | Skill decay over forecast horizon, **stratified by catchment area** (<100 km2, 100-1000 km2, >1000 km2) and concentration time | Sub-daily value proposition |

#### Research Questions (sharpened)

1. **Which uncertainty paradigm produces the best-calibrated probabilistic
   sub-hourly streamflow forecasts?** Evaluate via CRPS, CRPS decomposition,
   reliability diagrams, spread-skill ratio. Paradigm A (NWP pass-through) vs.
   B (CMAL mixture density) vs. C (deep ensemble) — three fundamentally
   different assumptions about where uncertainty originates.

2. **How much does model complexity add?** Ridge regression -> HBV -> MTS-LSTM
   -> TFT -> HBV->LSTM hybrid. Where does the complexity-skill curve flatten?
   Can post-hoc bias correction close residual gaps?

3. **Does physics-informed ML outperform pure ML for ensemble spread
   calibration?** HBV->LSTM (and optionally differentiable HBV) vs. pure
   MTS-LSTM/TFT. Evaluated via CRPS decomposition reliability component.

4. **At what lead times and catchment sizes does sub-hourly resolution add
   value over hourly, 6-hourly, and daily for flood threshold exceedance
   detection?** Stratified by catchment area (<100 km2, 100-1000 km2,
   >1000 km2) and concentration time. Evaluated via Brier skill score. A null
   result (no value for most catchments) is equally publishable.

5. **Can ML models learn to produce 15-minute streamflow forecasts from
   3-hourly ensemble NWP forcing?** Tests whether the multi-resolution
   architecture can learn useful sub-NWP-timestep dynamics from the discharge
   autoregression. If yes: sub-hourly flood warning is possible with coarse
   NWP. If no: identifies the resolution boundary.

6. **What fraction of forecast uncertainty originates from NWP forcing
   (Paradigm A) vs. learned distribution (Paradigm B) vs. model epistemic
   uncertainty (Paradigm C)?** Compare spread magnitude, spread-skill
   correlation, and reliability across paradigms. Also test hybrid paradigms
   (A+B, A+C) to assess whether combining sources improves calibration.

#### Key Comparisons / Baselines

- Persistence forecast (naive baseline)
- Climatological ensemble (reference for CRPS skill score)
- Google Flood Hub / GloFAS at equivalent lead times (if comparable catchments)
- Published MTS-LSTM benchmarks from NeuralHydrology
- Klotz et al. (HESS 2022) CMAL/MDN benchmarks (same NeuralHydrology framework)

#### What We Need

| Item | Source | Status |
|------|--------|--------|
| USGS 15-min discharge + gage height | NWIS Instantaneous Values API via `dataretrieval` Python (batched 50-100 stations, API key) | Needs download pipeline |
| LamaH-CE discharge + forcing | Zenodo (free) | Ready |
| GEFS v12 reforecast (training) | AWS `s3://noaa-gefs-pds` (free) | Needs extraction pipeline |
| GEFS v12 operational (evaluation) | AWS (free) | Needs extraction pipeline |
| TIGGE for LamaH-CE region | ECMWF MARS API (free for research). **Start retrieval early** -- tape-based storage, ~30 GB/day rate limit. Check if LamaH-CE authors already archived this. | Needs retrieval (allow weeks) |
| NeuralHydrology framework | Open source, pip install | Ready |
| GPU compute (~1,200h screening, ~21,600h full) | Institutional cluster or cloud | Needs scoping |

#### Related Work to Position Against

- Klotz et al. (HESS 2022) -- MDN uncertainty for rainfall-runoff (CMAL best,
  MC Dropout worst); we add ensemble NWP input and deep ensemble comparison
- AIFS-CRPS (Lang et al., 2024) -- CRPS-trained ensemble for weather
- MTS-LSTM (Gauch et al., HESS 2021) -- we extend to ensemble NWP
- Huo et al. (HESS 2023) -- QRF vs CMAL-LSTM (comparable performance)
- Dong et al. (HESS 2025) -- CNN-LSTM ensemble for Yangtze, but daily
- Modi et al. (JAMES 2025) -- DL in ensemble streamflow, but daily
- TFT for streamflow (Rasiya Koya & Roy, JoH 2024) -- but daily, no ensemble
- Nevo et al. (HESS 2022) -- ML in operational flood forecasting (Google)

---

### Paper 5 -- Water Level vs. Discharge as Forecast Target

**Working title**: "Water level vs. discharge as ML forecast target: implications
for flood alerting accuracy"

**Type**: Research article

**Target venue**: WRR or HESS

**Core contribution**:
- First systematic comparison of water level (stage) vs. discharge as ML
  prediction target for flood threshold exceedance detection
- Uses USGS NWIS 15-min data (up to ~5,188 gauges with both gage height and
  discharge; superset of CAMELSH's 3,166 discharge-only catchments)
- Quantifies how rating curve uncertainty at high flows degrades
  discharge-based flood alerting compared to direct stage prediction
- Tests multi-task learning (predict both simultaneously) as a third approach
- Isolates training-data quality from prediction uncertainty via a three-
  condition experimental design

**Timing**: Can run alongside Paper 2 -- same dataset, same models, different
analysis angle. Uses a subset of Paper 2's experimental runs. May be published
as a companion paper or folded into Paper 2 if results are thin.

**Experimental conditions** (three-way comparison on the same stations):

| Condition | Train on | Predict | Evaluate against | What it tests |
|-----------|---------|---------|-----------------|--------------|
| **D-D** | Discharge | Discharge | Observed discharge | Standard approach (rating curve noise in training labels) |
| **S-S** | Stage | Stage | Observed stage | Clean training signal, direct threshold comparison |
| **S-D** | Stage | Stage -> convert to discharge via rating curve at evaluation | Observed discharge | Isolates training quality effect from prediction uncertainty |
| **Multi** | Both (multi-task) | Both | Both | Does joint learning improve either target? |

Comparing D-D vs. S-S isolates the combined effect. Comparing S-S vs. S-D
isolates how much the rating curve conversion degrades discharge estimates
at evaluation time. Comparing D-D vs. S-D isolates the training label quality
effect.

**Research questions**:
1. Does predicting water level directly improve flood threshold exceedance
   detection compared to predicting discharge? (Evaluated via Brier score,
   hit rate, false alarm rate at multiple threshold levels.)
2. How much does rating curve uncertainty at high flows degrade
   discharge-based alerting? (Decomposed via the three-condition design.)
3. Can multi-task learning (predict both stage and discharge simultaneously)
   improve skill on both targets compared to single-target models?
4. How do the three uncertainty paradigms (A/B/C) interact with the target
   variable choice?

**What we need**:

| Item | Source | Status |
|------|--------|--------|
| USGS 15-min gage height (param 00065) | NWIS API (same pipeline as Paper 2) | Needs download |
| USGS 15-min discharge (param 00060) | NWIS API | Same pipeline as Paper 2 |
| USGS published rating curve uncertainties | NWIS rating curve data | Needs investigation |
| Paper 2 model training infrastructure | Shared | Dependency on Paper 2 |

**Key literature**:
- Rating curve uncertainty at high flows: 20-30%+ at 95% confidence (Australian
  study, Tandfonline 2019); UK 500-station study (PMC 2016)
- Klotz et al. (HESS 2022) -- all benchmarks use discharge; stage never tested
- Nevo et al. (HESS 2022) -- Google predicts stage, then converts
- NWS uses stage thresholds for operational flood warnings
- No published head-to-head ML comparison of stage vs. discharge as target

**Relevance to Nepal/CA**: In Nepal and Central Asia, rating curves are poorly
constrained (few high-flow measurements), some stations may only have stage
data. If stage prediction is shown to be equal or better for alerting, this
simplifies operational deployment.

---

### Paper 3 -- Transfer Learning (de-risked)

**Working title**: "Sub-hourly transfer learning for flood forecasting: from
multi-regional CAMELS benchmarks to operational deployment"

**Type**: Research article

**Target venue**: WRR or Nature Water (if results are strong)

**Core contribution**:
- First study of transfer learning at **sub-hourly** resolution (Google showed
  daily transfer works globally in Nature 2024)
- De-risked design: core experiments use leave-region-out cross-validation
  within USGS + LamaH-CE (no external dependency). Nepal becomes a validation
  case study, not the core contribution.
- Quantifies: how much local data is needed to achieve useful skill at 15-min
  and hourly resolution?
- Compares: train-from-scratch vs. fine-tune vs. zero-shot transfer
- Tests whether sub-hourly transfer degrades faster than daily/hourly with
  increasing climate dissimilarity

**Timing**: Core experiments (cross-regional within USGS + LamaH-CE) can start
after Paper 2 model training is complete. Nepal validation when v1 data is
available (2027+). Lowest priority paper.

**Research questions**:
1. Can models trained on USGS 15-min data generalize to LamaH-CE hourly
   catchments (and vice versa) at sub-daily resolution?
2. Does sub-hourly transfer degrade faster than daily transfer with climate
   dissimilarity?
3. What is the minimum local calibration data needed for useful skill at
   15-min resolution? (Test: 0, 10, 50, 200, 1000 days of local data.)
4. Which catchment attributes best predict transferability at sub-hourly
   resolution?
5. Does the uncertainty paradigm (A/B/C) affect transferability?

**Disentangling resolution vs. region**: To avoid conflating temporal resolution
and geographic transfer effects, include an intermediate experiment:
USGS-to-USGS transfer at different resolutions (15-min model tested on
hourly-aggregated data from held-out regions). This isolates the resolution
effect from the geographic effect.

**Key comparisons**:
- Nevo et al. (2024) Google global flood prediction in ungauged basins (Nature)
  -- daily, we extend to sub-hourly
- Kratzert et al. LSTM regionalization studies
- Similarity-guided source selection (MDPI Water 2025)

**What we need**:

| Item | Source | Status |
|------|--------|--------|
| Paper 2's trained models | Shared infrastructure | Dependency on Paper 2 |
| USGS + LamaH-CE spatial CV | Paper 2's CV setup | Ready after Paper 2 |
| Catchment attributes (634 CAMELSH, 60+ LamaH-CE) | Included in datasets | Ready |
| Nepal DHM station data (validation only) | DHM agreement needed | Deferred to v1 |

---

### Paper 1 -- Software Paper (minimal effort)

**Working title**: "SAPPHIRE Flow: An open-source, protocol-driven operational
forecasting system for probabilistic streamflow prediction"

**Type**: Software description

**Target venue**: Journal of Open Source Software (JOSS)

**Core contribution**:
- Protocol/adapter architecture enabling transferability across hydromets
- Full operational pipeline: NWP ingest -> QC -> ensemble forecast ->
  probabilistic alerting -> bulletin generation -> REST API
- Designed for resource-constrained deployments (`docker compose up` on a VM)

**Note**: The research experiments (Papers 2, 3, 5) use NeuralHydrology for
model training independently of the SAPPHIRE Flow operational codebase. The
JOSS paper describes the operational system, not the research benchmarking.
Its value proposition is operational deployment for hydromets, not research
results. Can be submitted early (after v0c) without waiting for research
papers.

**Timing**: Whenever v0c is stable. Minimal writing effort -- JOSS requires a
short paper (typically 1-2 pages) plus good documentation and tests.

**What we need**:

| Item | Source | Status |
|------|--------|--------|
| Working v0 system, 2+ adapters | Development | In progress |
| README, API docs, test coverage | Documentation | Planned |
| Statement of need | Writing | Planned |

---

## Timeline

| Paper | Priority | Earliest draft | Data dependency | Key milestone |
|-------|----------|---------------|-----------------|---------------|
| 0 (Lit review) | **1st** | 2026 Q2 | None (literature only) | Working review → Paper 2 intro + optional WRR Commentary ($0) |
| 2 (Methods) | **2nd** | 2026 Q4 | USGS 15-min + GEFS + LamaH-CE + TIGGE | Sub-hourly algorithm R&D complete |
| 5 (Stage vs Q) | **3rd** | 2027 Q1 | Subset of Paper 2 experiments | Runs alongside Paper 2 |
| 3 (Transfer) | **4th** | 2027 Q2-Q3 | Paper 2 models + cross-regional validation | Leave-region-out complete |
| 1 (JOSS) | **5th** (opportunistic) | Anytime after v0c | v0c working | Stable operational system |

Papers 0 and 2 are the priority. Paper 0 can be submitted before any results
exist. Paper 5 reuses Paper 2 infrastructure with a different analysis lens
and may be folded into Paper 2 if results are thin. Paper 3 is de-risked by
not depending on Nepal data for the core contribution.

---

## Data Sourcing Strategy

### Streamflow / Water Level

| Source | Resolution | Variables | Catchments | Access | Status |
|--------|-----------|-----------|------------|--------|--------|
| USGS NWIS (CAMELSH catchments) | 15-min | Discharge (00060) + gage height (00065) | ~3,166 (Q via CAMELSH), ~5,188 (Q+stage via NWIS superset) | `dataretrieval` Python, API key needed | Needs download pipeline |
| LamaH-CE | Hourly | Discharge only (no stage) | 859 | Zenodo (free) | Ready |
| CAMELS-GB v2 | Hourly | Discharge + water level | 671 | EIDC (free) | Ready |
| BAFU (Switzerland) | 10-min (TBC) | Discharge + water level (TBC) | TBD | Public API (TBC) | Needs verification |

**Note**: CAMELSH provides hourly aggregations of USGS data. For 15-min
resolution, we pull directly from USGS NWIS instantaneous values — CAMELSH
catchment definitions (boundaries, attributes) are reused, but the streamflow
data comes from NWIS at native resolution.

### NWP Ensemble Forcing

| Product | Members | Resolution | Temporal | Archive | Region | Access | Role |
|---------|---------|-----------|----------|---------|--------|--------|------|
| **GEFS v12 reforecast** | 5 daily / 11 weekly | 25 km | 3-hourly | 2000-2019 (20 yr) | Global | AWS `s3://noaa-gefs-pds` (free) | **Training** (long record, few members) |
| **GEFS v12 operational** | 31 | 25 km | 3-hourly | 2017-present | Global | AWS (free) | **Evaluation** of Paradigm A (sufficient members for probabilistic assessment) |
| **TIGGE** | 51 (ECMWF) | ~50 km | 6-hourly | 2006-present | Global | ECMWF MARS (free for research) | LamaH-CE training + evaluation |
| **ECMWF IFS ENS open data** | 51 | 25 km | 3-hourly | 2024-04 onward | Global | dynamical.org Zarr on AWS (free) | Future use / supplementary |
| **ICON-EU-EPS** (DWD) | 40 | 7 km | Hourly | Deletes after 24h (must self-archive) | Europe | DWD Open Data (free) | Future use |
| **ICON-CH2-EPS** (MeteoSwiss) | 21 | 2.1 km | Hourly | Self-archived in SAPPHIRE | Switzerland | MeteoSwiss STAC API | SAPPHIRE v0 operational |

**Primary pairing**: USGS 15-min + GEFS reforecast (training) / GEFS
operational (evaluation). Largest sample, both sub-hourly discharge and stage.

**European pairing**: LamaH-CE + TIGGE (51 members, 6-hourly). Limits
sub-hourly analysis to hourly resolution but provides European validation with
sufficient ensemble members.

**Operational pairing**: Swiss stations + ICON-CH2-EPS (SAPPHIRE's v0
operational system, highest NWP resolution). Not used for research papers
unless BAFU sub-daily data becomes available.

### Reanalysis Forcing (for training)

| Product | Resolution | Temporal | Source |
|---------|-----------|----------|--------|
| NLDAS-2 | 12 km | Hourly | Included in CAMELSH |
| ERA5-Land | 9 km | Hourly | Included in LamaH-CE |
| CHESS-met | 1 km | Daily (hourly precip in v2) | Included in CAMELS-GB v2 |

---

## Cross-Cutting Considerations

**Data sharing**: All papers release code and data where possible. CAMELSH,
LamaH-CE, CAMELS-GB, GEFS, TIGGE are all publicly available. Model weights
and experiment configs shared via Zenodo. Nepal DHM data sharing requires
agreement.

**Reproducibility**: NeuralHydrology framework for model training.
Docker-based deployment for SAPPHIRE Flow operational system. Experiment
configs and random seeds tracked for full reproducibility.

**Open science**: Paper 0 submitted to HESS (open access, open review). All
papers target open-access venues where possible.

**Co-authorship**: Consider including Nepal DHM staff (Paper 3 validation),
SAPPHIRE Central Asia collaborators, NeuralHydrology community contributors,
and domain experts.

---

## Literature to Track

Key groups, systems, and trends to monitor:

**Operational systems**:
- **Google Flood Hub** -- expanding to sub-daily? Sub-hourly? More ungauged
  basin work? (Currently daily, 250k virtual gauges, 80+ countries)
- **EFAS/GloFAS + AIFS** -- AIFS Single integrated Sep 2025; AIFS ENS
  operational Jul 2025. Watch for streamflow-specific AI integration.
- **NeuralHydrology** (Kratzert, Gauch et al.) -- MTS-LSTM updates, new
  architectures, operational deployment papers?

**Architectures**:
- **Mamba / state-space models** -- RiverMamba (NeurIPS 2025) showed competitive
  results. Watch for hydrology-specific Mamba papers. Sequence lengths in
  SAPPHIRE (120h at 15-min = 480 steps) may not be long enough for Mamba's
  efficiency advantages.
- **Temporal Fusion Transformer** -- gaining traction (Rasiya Koya & Roy 2024).
  Explainability features are a selling point.
- **Foundation models for hydrology** -- early stage (arXiv 2024). Could change
  the transfer learning story entirely.

**Datasets**:
- **CAMELSH** (2025) -- hourly CAMELS-US, 3,166+ catchments. The first
  large-sample hourly dataset. Note: provides hourly aggregation; 15-min
  requires direct USGS NWIS pull. Also has ERA5-Land variant (Zenodo v2).
- **LamaH-CE** updates -- end date currently 2017, watch for extensions.
- **CAMELS-GB v2** (2025) -- hourly discharge + water level + groundwater.
- **CAMELS-SPAT** (HESS 2025) -- spatially distributed forcing, sub-daily.

**Methods**:
- **MDN/CMAL uncertainty** -- Klotz et al. (HESS 2022) established CMAL as
  best single-model method. Watch for deep ensemble comparison in hydrology
  (could be us). Also watch for CRPS-as-loss applied to streamflow.
- **Quantile ensembles** -- Ensemble Quantile-LSTM, QDeepGR4J. QRF comparable
  to CMAL but 50% faster (Huo et al. HESS 2023).
- **Physics-informed ML** -- differentiable process models, hybrid
  architectures. Active frontier.

**Policy / operations**:
- **Busker et al. (2025)** -- cross-country FEWS comparison, all NW European
  countries moved to probabilistic after 2021 floods.
- **Nepal DHM modernization** -- currently no operational ML forecasting
  (review paper, 2025). Data scarcity + limited compute are main barriers.

---

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
| 1 | 2026-03-09 | review-domain, plan-reviewer, review-docs | 7 | 14 | fixes-needed |
