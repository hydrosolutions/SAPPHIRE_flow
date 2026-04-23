# 3. Uncertainty Paradigms for ML-Based Ensemble Streamflow Forecasting

Literature review for Section 3 of the Paper 0 outline.
Last updated: 2026-03-31.

## Key Findings

1. **Three paradigms exist for uncertainty in ML streamflow, but they have
   never been compared head-to-head.** Paradigm A (NWP ensemble pass-through)
   is standard for process-based models but has never been applied to a pure ML
   hydrological model. Paradigm B (learned distribution via CMAL/MDN) is
   operational at Google Flood Hub. Paradigm C (deep ensembles) has been tested
   for streamflow but poorly characterised relative to A and B.
2. **No paper propagates individual NWP ensemble members through a standalone
   ML streamflow model.** Dong et al. (HESS, 2025) propagate through a hybrid
   XAJ-LSTM; Modi et al. (JAMES, 2025) use resampled historical forcing (ESP),
   not NWP ensembles. The gap is confirmed.
3. **CMAL is the best-performing learned distribution for streamflow**
   (Klotz et al., HESS 2022), but was tested only with observed forcing at
   daily resolution. QRF is a viable non-neural alternative (Zhang et al.,
   HESS 2023) — comparable to CMAL, 50% faster, but with a basin-size
   dependence.
4. **CRPS as a direct training loss** is established for weather (AIFS-CRPS,
   Lang et al., 2024) and available in NeuralHydrology for streamflow.
   CRPS-trained models show better calibration than NLL-trained models,
   at slight cost to sharpness.
5. **Diffusion models represent an emerging Paradigm D** — generating ensemble
   traces via a learned stochastic process. DRUM (Ou et al., GRL 2025) and
   HydroDiffusion (Wang et al., arXiv 2025) show promising results but have
   no NWP forcing integration.
6. **The one study comparing deep ensemble LSTM with NWP forcing found poor
   spread-skill** (Sabzipour et al., J. Hydrol., 2023) — suggesting naive
   Paradigm C without NWP ensemble information may not capture forecast
   uncertainty adequately.
7. **Permutation-invariant neural networks** for processing ensemble NWP input
   exist (Hohlein et al., AIES 2024) but have only been applied to weather
   post-processing, not streamflow.

---

## 3.1 Paradigm A — NWP Ensemble Pass-Through

### The standard approach (process-based)

Every major operational ensemble flood forecasting system uses this paradigm:
each NWP ensemble member independently forces a deterministic hydrological
model, producing one streamflow trace per member. Exceedance probabilities are
computed from the resulting ensemble. All uncertainty comes from the NWP input;
the hydrological model has no stochastic component.

- EFAS: 51 ECMWF ENS + 20 COSMO-LEPS members → LISFLOOD → ensemble streamflow
- GloFAS: 51 ECMWF ENS members → os-LISFLOOD → ensemble streamflow
- NOAA NWM: 6 GFS time-lagged → WRF-Hydro → ensemble streamflow

The key assumption is that NWP forcing uncertainty dominates total forecast
uncertainty — an assumption that holds at medium range (day 3+) but is
questionable at short range where hydrological state uncertainty (soil moisture,
antecedent conditions) contributes significantly.

### Paradigm A with ML: an open gap

**No paper feeds individual NWP ensemble members through a standalone ML
streamflow model to produce ensemble traces.** The closest work:

**Dong et al. (HESS, 2025)**: Propagate ECMWF S2S ensemble members through a
CNN (for NWP downscaling) → XAJ-LSTM hybrid for streamflow. Each member
produces one streamflow trace. But the hydrological component is a conceptual-ML
hybrid, not a pure ML model, and operates at sub-seasonal daily resolution over
a single basin.

**Modi et al. (JAMES, 2025)**: Replace the process-based model in ESP (Ensemble
Streamflow Prediction) with an LSTM. The LSTM is forced with resampled
historical weather traces — the ensemble comes from historical forcing
variability, not NWP members. Tested on 76 western US basins with explicit
snowpack information, daily resolution. Conceptually close to Paradigm A but
uses historical resampling rather than NWP forecasts.

**Sharma et al. (J. Hydroinformatics, 2023)**: LSTM post-processes raw ensemble
streamflow forecasts from GEFSv2 (11 members) forced through a distributed
hydrological model (NOAA HLRDHM). The LSTM corrects per-member, per-lead-time
biases — preserving the ensemble structure — but acts as a post-processor on
top of a process-based model, not as the primary hydrological model.

**Hunt et al. (HESS, 2022)**: Used only the control member of ECMWF IFS to
drive an LSTM for western US streamflow, explicitly noting as a limitation that
"what is the best way to combine ensemble members as inputs to an LSTM" remains
an open question.

### Why this gap matters

If Paradigm A works with ML, it provides physically interpretable ensemble
spread — each member represents a physically consistent weather scenario
propagated through a learned rainfall-runoff relationship. This is what
forecasters understand and trust. If it doesn't work (because ML models are
insensitive to inter-member differences, or because the ensemble is
underdispersive through an ML model), that's equally important to know.

**Key references**:
- Dong, N., Hao, H., Yang, M., Wei, J., Xu, S., and Kunstmann, H.:
  Deep-learning-based sub-seasonal precipitation and streamflow ensemble
  forecasting over the source region of the Yangtze River, Hydrol. Earth Syst.
  Sci., 29, 2023–2042, doi:10.5194/hess-29-2023-2025, 2025.
- Modi, P., Jennings, K., Kasprzyk, J., Small, E., Wobus, C., and Livneh, B.:
  Using Deep Learning in Ensemble Streamflow Forecasting: Exploring the
  Predictive Value of Explicit Snowpack Information, J. Adv. Model. Earth
  Syst., 17(3), e2024MS004582, doi:10.1029/2024MS004582, 2025.
- Sharma, S., Ghimire, G. R., and Siddique, R.: Machine learning for
  postprocessing ensemble streamflow forecasts, J. Hydroinformatics, 25(1),
  126–139, doi:10.2166/hydro.2022.114, 2023.
- Hunt, K. M. R., Matthews, G. R., Pappenberger, F., and Prudhomme, C.: Using
  a long short-term memory (LSTM) neural network to boost river streamflow
  forecasts over the western United States, Hydrol. Earth Syst. Sci., 26,
  5449–5472, doi:10.5194/hess-26-5449-2022, 2022.

---

## 3.2 Paradigm B — Learned Distribution (MDN/CMAL)

### The Google approach

A single model takes deterministic NWP (or observed) forcing and outputs
parameters of a mixture distribution. Uncertainty is learned from the
training data distribution, not propagated from NWP ensemble spread. One
forward pass produces the full predictive distribution.

Google Flood Hub (Nevo et al., HESS 2022) uses CMAL — Countable Mixture of
Asymmetric Laplacians — a flexible mixture distribution trained with negative
log-likelihood loss. When forecast uncertainty exceeds ~50 cm, lead time is
shortened rather than issuing uncertain forecasts.

### Theoretical foundation: ALD and quantile regression

The asymmetric Laplace distribution (ALD) provides the probabilistic foundation
for quantile regression: minimising the quantile (pinball) loss is mathematically
equivalent to maximising the ALD likelihood (Yu & Moyeed, 2001; Kozumi &
Kobayashi, 2011). CMAL exploits this directly — each component in the mixture is
an asymmetric Laplacian, so the mixture density head is a multi-component
generalisation of quantile regression. This equivalence also means that any
single-quantile neural network trained with pinball loss is implicitly fitting a
single ALD; CMAL's advantage is that the mixture captures the full conditional
distribution rather than individual quantiles.

**Bayesian quantile regression neural networks** (Jantre et al., J. Stat. Theory
Pract., 2021) formalise this further: they assume an ALD likelihood for the
response variable and use MCMC (Gibbs + Metropolis-Hastings) via the
normal-exponential mixture representation of the ALD density for posterior
inference over network weights. They prove posterior consistency under a
misspecified ALD model. This is conceptually related to Paradigm B but uses
Bayesian weight inference rather than a mixture density head; the computational
cost of MCMC makes it impractical for operational use but theoretically
interesting for understanding the ALD–quantile–Bayesian triangle.

**Relaxed Quantile Regression** (Pouplin et al., ICML 2024) addresses a
practical limitation of standard quantile regression: the requirement that
prediction intervals be symmetric around the median. RQR removes this constraint
while maintaining coverage guarantees, producing tighter intervals for skewed
distributions — relevant for streamflow where right-skewed flood peaks are the
norm.

### Systematic comparison of learned distributions

**Klotz et al. (HESS, 2022)** is the only systematic comparison for LSTM
streamflow:

| Method | Approach | Ranking |
|--------|----------|---------|
| CMAL | Mixture of asymmetric Laplacians | **Best** |
| UMAL | Unconditional mixture of asymmetric Laplacians | 2nd |
| GMM | Gaussian mixture model | 3rd |
| MC Dropout | Dropout at inference | Worst |

Tested on 516 CAMELS basins, daily resolution, with **observed forcing only**.
The interaction with NWP forecast uncertainty was not examined. Note that this
study originates from the Google/NeuralHydrology group; independent replication
of these rankings at scale is lacking (see §1.5 CRAAB note).

### QRF as a non-neural alternative

**Zhang et al. (HESS, 2023)** compared Quantile Regression Forest (QRF) with
CMAL-LSTM for probabilistic post-processing of satellite precipitation-driven
streamflow simulations on 522 nested sub-basins (Yalong River, China):

- QRF and CMAL-LSTM are **comparable overall** for probabilistic prediction
- QRF outperforms in **smaller catchments** (flow accumulation area < 60,000 km²)
- CMAL-LSTM has a clear advantage in **larger basins** (> 60,000 km²)
- QRF is ~**50% faster** (~6 h vs ~12 h for 100-member ensemble experiments)
- CMAL-LSTM is superior for deterministic predictions when raw inputs are poor

This is significant because QRF provides a computationally cheaper, non-neural
baseline for probabilistic post-processing. The basin-size dependence suggests
that the representational advantage of LSTMs grows with catchment complexity.

Note: the outline attributed this paper to "Huo et al." — the correct authors
are Zhang, Ye, Analui, Nguyen, Sorooshian, Hsu, and Wang.

### Limitations of Paradigm B

1. **No physical interpretability of spread**: the model learns an average
   uncertainty pattern from training data; it cannot distinguish a forecast
   that is uncertain because of NWP disagreement (correctable) from one that
   is uncertain because of inherent process noise (irreducible).
2. **Distribution mismatch in non-stationary settings**: if NWP characteristics
   change (e.g., model upgrade, new ensemble design), the learned uncertainty
   may be miscalibrated.
3. **Only tested with observed or deterministic forcing**: Klotz et al. (2022)
   used observed forcing; Google Flood Hub uses deterministic ECMWF HRES +
   GraphCast. How CMAL performs when fed NWP forecast forcing (which has
   systematic biases and different error characteristics than observations)
   is unknown.

**Key references**:
- Klotz, D., Kratzert, F., Gauch, M., et al.: Uncertainty estimation with
  deep learning for rainfall-runoff modelling, Hydrol. Earth Syst. Sci., 26,
  1673–1693, doi:10.5194/hess-26-1673-2022, 2022.
- Nevo, S., Morin, E., Gerzi Rosenthal, A., et al.: Flood forecasting with
  machine learning models in an operational framework, Hydrol. Earth Syst.
  Sci., 26, 4013–4032, doi:10.5194/hess-26-4013-2022, 2022.
- Zhang, Y., Ye, A., Analui, B., Nguyen, P., Sorooshian, S., Hsu, K., and
  Wang, Y.: Comparing quantile regression forest and mixture density long
  short-term memory models for probabilistic post-processing of satellite
  precipitation-driven streamflow simulations, Hydrol. Earth Syst. Sci., 27,
  4529–4550, doi:10.5194/hess-27-4529-2023, 2023.
- Yu, K. and Moyeed, R. A.: Bayesian quantile regression, Stat. Probab. Lett.,
  54(4), 437–447, doi:10.1016/S0167-7152(01)00124-9, 2001.
- Jantre, S. R., Bhattacharya, S., and Maiti, T.: Quantile Regression Neural
  Networks: A Bayesian Approach, J. Stat. Theory Pract., 15(3),
  doi:10.1007/s42519-021-00189-w, 2021.
- Pouplin, T., Jeffares, A., Seedat, N., and van der Schaar, M.: Relaxed
  Quantile Regression: Prediction Intervals for Asymmetric Noise, in: Proc.
  ICML 2024, arXiv:2406.03258, 2024.

---

## 3.3 Paradigm C — Deep Ensembles

### The method

Deep ensembles (Lakshminarayanan et al., NeurIPS 2017) train M models with
different random seeds. Each model converges to a different local optimum in the
loss landscape, and the ensemble spread captures both aleatoric and epistemic
uncertainty. Fort et al. (2019) showed this diversity reflects exploration of
distinct loss landscape modes.

### Application to streamflow

Klotz et al. (HESS, 2022) included deep ensembles in their comparison, finding
them competitive with CMAL but less parameter-efficient (M separate models vs
one model with a mixture density head). The NeuralHydrology framework supports
multi-seed training as a configuration option.

**Sabzipour et al. (J. Hydrol., 2023)** compared LSTM deep ensembles against
the CEQUEAU process-based model, both driven by NWP forecasts, over a single
Canadian catchment. The LSTM ensemble showed **small spread and increasing bias
at longer lead times**, penalising CRPS compared to the process-based model.
This suggests that naive Paradigm C (deep ensemble with deterministic NWP) may
not capture forecast uncertainty adequately — the ensemble diversity from random
seed initialisation is insufficient to represent forcing uncertainty. (Caveat:
single-catchment study — the finding may not generalise across hydroclimatic
regimes. This is currently the only such comparison; replication across diverse
basins is needed.)

This is a critical finding: **seed diversity ≠ forcing uncertainty**. Deep
ensembles capture model uncertainty (epistemic), but the dominant source of
forecast uncertainty at medium range is NWP forcing uncertainty, which seed
diversity does not reflect. This motivates hybrid approaches (A+C) where each
ensemble member is also forced with a different NWP member.

### MC Dropout as inferior alternative

MC Dropout (Gal & Ghahramani, 2016) uses dropout at inference time to
approximate Bayesian uncertainty. All benchmarks in hydrology show it
underperforms CMAL, deep ensembles, and GMM (Klotz et al., 2022). It tends
to produce **underdispersive intervals** and is computationally expensive
(requires many forward passes for a single prediction). Not competitive for
operational use.

Recent work attempts to improve MC Dropout calibration: Son & Seok
(Neurocomputing, 2025) propose stable output layers to reduce variance in
uncertainty estimates; an enhanced MCD framework (arXiv:2505.15671, 2025)
integrates uncertainty-aware loss functions. These may narrow the gap with
CMAL but do not address the fundamental limitation that dropout-based
variational inference is a crude posterior approximation.

**Note on "MC-ALD"**: Combining MC Dropout with ALD/quantile loss (sometimes
informally called "MC-ALD") is a theoretically motivated composite —
ALD loss captures aleatoric uncertainty via quantile spread while MC Dropout
captures epistemic uncertainty via weight sampling. However, this is strictly
weaker than CMAL: a single ALD component per quantile versus a full mixture
of asymmetric Laplacians that already captures multi-modal conditional
distributions. Klotz et al. (2022) effectively tested this idea (MC Dropout
was evaluated alongside CMAL) and found it worst among all methods. The
composite does not appear as a named method in the literature.

**Key references**:
- Lakshminarayanan, B., Pritzel, A., and Blundell, C.: Simple and Scalable
  Predictive Uncertainty Estimation using Deep Ensembles, Advances in Neural
  Information Processing Systems 30 (NeurIPS), arXiv:1612.01474, 2017.
- Fort, S., Hu, H., and Lakshminarayanan, B.: Deep Ensembles: A Loss
  Landscape Perspective, arXiv:1912.02757, 2019.
- Sabzipour, B., Arsenault, R., Troin, M., Martel, J.-L., Brissette, F.,
  Brunet, F., and Mai, J.: Comparing a long short-term memory (LSTM) neural
  network with a physically-based hydrological model for streamflow forecasting
  over a Canadian catchment, J. Hydrol., 627, 130380,
  doi:10.1016/j.jhydrol.2023.130380, 2023.
- Son, S. and Seok, J.: Improving Monte Carlo dropout uncertainty estimation
  with stable output layers, Neurocomputing, 131927,
  doi:10.1016/j.neucom.2025.131927, 2025.
- Tyralis, H. and Papacharalampous, G.: A review of predictive uncertainty
  estimation with machine learning, Artif. Intell. Rev., 57, 94,
  doi:10.1007/s10462-023-10698-8, 2024.

---

## 3.4 CRPS as a Direct Training Loss

### The AIFS-CRPS breakthrough

Traditional AI weather models are trained with MSE/MAE loss on single
deterministic trajectories. To get ensembles, you perturb initial conditions
— borrowed from traditional NWP thinking and not optimised for ensemble
quality.

**AIFS-CRPS (Lang et al., 2024)** takes a fundamentally different approach:
train the model to minimise the **Continuous Ranked Probability Score** across
the ensemble members it produces. At each training step, the model generates
N members; the empirical CDF is scored against the verifying analysis via CRPS.
The gradient flows back through all members simultaneously.

Key innovations:
- **No initial-condition perturbations needed**: diversity comes from a
  stochastic latent input vector. Different noise samples → different weather
  states that collectively represent forecast uncertainty.
- **Noise-to-spread mapping is learned**, not hand-crafted — the model learns
  how much spread is appropriate for each weather regime.
- **50 ensemble members**, matching IFS-ENS. One single GNN produces all
  members — no ensemble of separate models.
- Matched or exceeded IFS-ENS on most probabilistic metrics at a fraction of
  the computational cost.

### CRPS loss for streamflow

CRPS as a training loss is already available in NeuralHydrology and has been
used for streamflow (Klotz et al., 2022). Compared to NLL (as used by CMAL):

| Aspect | CRPS Loss | NLL Loss (CMAL) |
|--------|-----------|-----------------|
| Distribution assumption | Distribution-free (works with samples) | Requires parametric family |
| Calibration | Better — directly optimises calibration + sharpness | Optimises likelihood |
| Sharpness | Slightly less sharp | Can be sharper if family is well-chosen |
| Outlier sensitivity | Robust — integrates over full CDF | Sensitive to tail observations |
| Quantile crossing | Not an issue with CDF parameterisation | N/A |

The practical difference is modest when the parametric family for NLL is
well-chosen (Klotz et al., 2022). However, CRPS loss becomes particularly
attractive when outputting ensemble members directly (as AIFS-CRPS does) rather
than mixture distribution parameters.

### Implication for SAPPHIRE Flow

The AIFS-CRPS approach is directly transferable to hydrological ensemble
forecasting: train a streamflow model with stochastic latent input and CRPS
loss, letting the model learn appropriate spread from historical
forecast-observation pairs. This would represent a **Paradigm B variant** where
the learned distribution is implicit (ensemble samples) rather than explicit
(mixture parameters), combining advantages of deep ensembles (multiple traces)
with learned uncertainty (no NWP propagation needed).

**Key references**:
- Lang, S., Alexe, M., Chantry, M., Dramsch, J., Pinault, F., Raoult, B.,
  Clare, M. C. A., Lessig, C., Maier-Gerber, M., Magnusson, L., et al.: AIFS
  — ECMWF's data-driven forecasting system, arXiv:2406.01465, 2024.
  [preprint]
- Rasp, S. and Lerch, S.: Neural networks for postprocessing ensemble weather
  forecasts, Mon. Wea. Rev., 146, 3885–3900, doi:10.1175/MWR-D-18-0187.1,
  2018.

---

## 3.5 Hybrid Paradigms

### A+B: CMAL per NWP member

Run each NWP ensemble member through a model with a CMAL head. Each member
produces a predictive distribution; the combined forecast is a mixture of
mixtures. This separates forcing uncertainty (inter-member spread) from model
uncertainty (intra-member spread). **Untested.**

### A+C: Deep ensemble per NWP member

Run each NWP member through M randomly-seeded models. Produces N×M streamflow
traces capturing both forcing and model uncertainty. Computationally expensive
but conceptually clean. **Untested.**

### B+latent noise (AIFS-CRPS for hydrology)

Train a single model with stochastic latent input and CRPS loss on
deterministic NWP forcing. The model learns to generate diverse ensemble
members that collectively reproduce observed forecast uncertainty. **Not yet
applied to streamflow.**

### A+B+latent noise

Feed NWP ensemble members to a CRPS-trained stochastic model. Each NWP member
generates K traces; the combined ensemble of N×K traces captures NWP forcing
uncertainty and learned residual uncertainty. **Untested, but this is the most
complete formulation.**

---

## 3.6 Emerging: Generative Models (Paradigm D)

Diffusion models generate ensemble traces via a learned stochastic process,
bypassing both NWP propagation and explicit distribution heads.

**DRUM** (Ou et al., GRL, doi:10.1029/2025GL115705, 2025): Diffusion-based
runoff model with LSTM encoder-decoder for probabilistic flood forecasting
across CONUS. Extends reliable lead times by nearly a full day for 20/50-year
floods compared to deterministic baselines. Originally arXiv:2412.11942;
published in Geophysical Research Letters.

**HydroDiffusion** (Wang et al., arXiv:2512.12183, 2025): Diffusion framework
with state-space model backbone. Outperforms LSTM-based diffusion variants on
CAMELS. [preprint]

Both are early-stage, daily resolution, no NWP forcing. But the architecture is
naturally suited to ensemble generation and could incorporate NWP conditioning.

---

## 3.7 Processing Ensemble NWP Input with ML

Regardless of the uncertainty paradigm, ML models must handle ensemble NWP
input. Three strategies exist, none systematically compared:

### 1. Member-by-member (shared weights)

Each NWP member passes through the same model independently, producing
per-member streamflow forecasts. Preserves full ensemble structure. N forward
passes.

Used by: Dong et al. (2025, hybrid), Sharma et al. (2023, post-processing).
Never used with a pure ML hydrological model.

### 2. Ensemble statistics as input

Collapse the ensemble into mean, spread, selected quantiles before input.
Reduces dimensionality but discards individual member structure and
inter-member correlations.

Used by: Hunt et al. (2022, control member only — the simplest case).
Google Flood Hub uses deterministic NWP, which is a degenerate case.

### 3. Permutation-invariant processing

Process all members through a shared encoder, then aggregate with a
permutation-invariant function (mean, attention, DeepSets-style). Preserves
member information while respecting exchangeability.

**Hohlein et al. (AIES, 2024)** demonstrated this for weather post-processing
using an encoder MLP per member followed by permutation-invariant pooling.
**Not yet applied to streamflow.** This architecture could directly ingest
ensemble NWP for a hybrid A+B approach.

**Key reference**:
- Hohlein, K., Schulz, B., Westermann, R., and Lerch, S.: Postprocessing of
  Ensemble Weather Forecasts Using Permutation-Invariant Neural Networks,
  Artif. Intell. Earth Syst., 3(1), doi:10.1175/AIES-D-23-0070.1, 2024.

---

## 3.8 Statistical Post-Processing of ML Ensemble Output

### BMA/EMOS applied to ML streamflow

Classical Bayesian Model Averaging (BMA) and Ensemble Model Output Statistics
(EMOS) correct ensemble bias and under-dispersion. Widely used for process-based
ensemble streamflow (Hemri et al., WRR 2015; Duan et al., 2007), but
**no paper applies BMA or EMOS specifically to ML streamflow ensemble output**.
This is a gap — as ML-generated ensembles may exhibit different bias/dispersion
characteristics than process-based ensembles.

### ML post-processing of process-based ensemble streamflow

An active area that sits between Paradigms A and B:

**Frame et al. (JAWRA, 2021)**: LSTM post-processes National Water Model output.
The "Errorcastnet" lineage — ML learns NWM systematic errors and corrects them.

**Sharma et al. (J. Hydroinformatics, 2023)**: Per-member, per-lead-time LSTM
correction of GEFS-driven ensemble streamflow. Preserves ensemble structure
while reducing bias.

**Solanki et al. (WRR, 2025)**: Combines multiple hydrological models with ML
and GEFS ensemble forcing. ML used for multi-model combination rather than as
the primary model.

**Key references**:
- Frame, J. M., Kratzert, F., Raney, A., Rahman, M., Salas, F. R., and
  Nearing, G. S.: Post-Processing the National Water Model with Long Short-Term
  Memory Networks for Streamflow Predictions and Model Diagnostics, JAWRA J.
  Am. Water Resour. Assoc., 57, 885–905, doi:10.1111/1752-1688.12964, 2021.
- Solanki, H., Vegad, U., Kushwaha, A., and Mishra, V.: Improving Streamflow
  Prediction Using Multiple Hydrological Models and Machine Learning Methods,
  Water Resour. Res., 61(1), doi:10.1029/2024WR038192, 2025.

---

## 3.9 Testable Predictions

Each paradigm makes distinct, testable predictions about ensemble behaviour:

| Paradigm | Spread source | Testable prediction |
|----------|--------------|---------------------|
| A (NWP pass-through) | NWP forcing variability | Ensemble spread correlates with NWP spread; skill tracks NWP skill |
| B (Learned distribution) | Training data patterns | Spread reflects learned climatological uncertainty; insensitive to NWP ensemble quality |
| C (Deep ensembles) | Model disagreement | Spread reflects epistemic uncertainty from training; independent of NWP quality |
| A+B (Hybrid) | Both | Decomposable into forcing and model components |
| D (Generative) | Learned stochastic process | Spread reflects conditional variability; may conflate forcing and model uncertainty |

**Diagnostic tools**: CRPS decomposition (reliability + resolution + uncertainty),
spread-skill ratio, rank histograms, and conditional coverage analysis can
distinguish these patterns. If Paradigm A spread tracks NWP spread but
Paradigm B spread does not, this reveals whether the ML model is sensitive to
inter-member forcing differences — a key question for operational deployment.

---

## 3.10 The Central Gap

**No systematic comparison of Paradigms A, B, and C exists for ML-based
streamflow forecasting at any temporal resolution.**

The literature provides fragments:
- Klotz et al. (2022): compared B variants (CMAL, UMAL, GMM, MC Dropout)
  but only with observed forcing — no NWP, no Paradigm A
- Sabzipour et al. (2023): compared Paradigm C (deep ensemble) vs
  process-based Paradigm A — found poor spread-skill for the ML ensemble
- Google Flood Hub: operationalised Paradigm B but never tested A or C
- AIFS-CRPS: demonstrated CRPS-as-loss for weather ensembles but not for
  streamflow

The interaction between uncertainty paradigm and:
- **NWP ensemble quality** (number of members, bias, dispersion)
- **Temporal resolution** (sub-daily amplifies timing uncertainty)
- **Lead time** (short-range: state uncertainty dominates; medium-range:
  forcing uncertainty dominates)

has **never been characterised**. This is the central open question and the
target of the ETH MSc thesis on uncertainty paradigms.

---

## 3.11 Conclusions for SAPPHIRE Flow

### Operational default: Paradigm B (CMAL)

For SAPPHIRE Flow's operational pipeline, CMAL is the pragmatic choice:
- One forward pass per forecast
- No ensemble bookkeeping (no N×M management)
- Operationally proven at Google scale
- NeuralHydrology implementation available

### Research mode: Paradigm comparison

The architecture should support all paradigms for research:
- **A**: member-by-member propagation (requires ensemble NWP ingestion)
- **B**: CMAL distribution head (default)
- **C**: multi-seed training (NeuralHydrology supports this)
- **A+B**: CMAL per member (composition of A and B)
- **B+noise**: AIFS-CRPS-style stochastic latent input

### What this means for the codebase

- `ForecastModel.predict()` should return `QuantileForecast` regardless of
  paradigm — all paradigms ultimately produce a probabilistic forecast
- Ensemble NWP input should be supported but optional — the model accepts
  either deterministic or ensemble forcing
- Training loss should be configurable: NLL (for CMAL), CRPS (for direct
  ensemble training), MSE (for deterministic baselines)
- Multi-seed training should be a configuration option, not a separate pipeline

### Evaluation metrics

- **Primary**: CRPS (integrates calibration and sharpness)
- **Calibration**: rank histograms, PIT histograms, reliability diagrams
- **Sharpness**: interval width, spread-skill ratio
- **Extreme events**: conditional CRPS above flood thresholds, Brier skill score
  for threshold exceedance
- **Paradigm diagnostics**: spread-NWP correlation (A vs B), conditional
  coverage by lead time, CRPS decomposition

---

## Reference Verification Status

Verified 2026-03-30.

### Verified
- [x] Zhang et al. (HESS, 2023) — DOI 10.5194/hess-27-4529-2023 confirmed.
  **Note**: Outline attributed this to "Huo et al." — corrected to Zhang et al.
- [x] Modi et al. (JAMES, 2025) — DOI 10.1029/2024MS004582 confirmed. Authors:
  Modi, Jennings, Kasprzyk, Small, Wobus, Livneh.
- [x] Hunt et al. (HESS, 2022) — DOI 10.5194/hess-26-5449-2022 confirmed.
- [x] Sabzipour et al. (J. Hydrol., 2023) — DOI 10.1016/j.jhydrol.2023.130380
  confirmed.
- [x] Hohlein et al. (AIES, 2024) — DOI 10.1175/AIES-D-23-0070.1 confirmed.
  **Note**: Research agent initially attributed this to "Mlakar et al." —
  corrected to Hohlein, Schulz, Westermann, and Lerch.
- [x] Lang et al. (2024) — arXiv:2406.01465 confirmed. AIFS foundation paper.
- [x] Solanki et al. (WRR, 2025) — DOI 10.1029/2024WR038192 confirmed.
- [x] Sharma et al. (J. Hydroinformatics, 2023) — **DOI corrected** from
  10.2166/hydro.2022.167 (which resolves to unrelated paper) to
  10.2166/hydro.2022.114.
- [x] Frame et al. (2021) — **CORRECTED**: Published in JAWRA, not HESS.
  DOI: 10.1111/1752-1688.12964. See CRAAB section above.
- [x] Dong et al. (HESS, 2025) — previously verified in Section 1.
- [x] Klotz et al. (HESS, 2022) — previously verified in Section 1.
- [x] Nevo et al. (HESS, 2022) — previously verified in Section 1.

### Updated during CRAAB review
- [x] DRUM — **Now published in GRL**: doi:10.1029/2025GL115705 (Ou et al.,
  2025). Originally arXiv:2412.11942. Updated from preprint to published.
- [x] HydroDiffusion — arXiv:2512.12183 (Wang et al., 2025) confirmed.
  Still preprint.
- [x] Frame et al. (2021) — **CORRECTED**: Journal is JAWRA (not HESS).
  DOI: 10.1111/1752-1688.12964. The previously cited DOI 10.5194/hess-25-4917-2021
  resolves to an unrelated paper (Gasset et al.).
- [ ] AIFS-CRPS ensemble-specific paper — not yet identified. The AIFS-CRPS
  approach is described in ECMWF technical memos and NeurIPS 2024 workshop
  presentations. A standalone peer-reviewed publication may not yet exist.
  **The CRPS-as-loss principle is well-established (Rasp & Lerch, 2018,
  doi:10.1175/MWR-D-18-0187.1); the AIFS-specific application needs a better
  source before publication.**

### Added 2026-03-31 (ALD/MC-ALD investigation)
- [ ] Yu & Moyeed (2001) — Bayesian quantile regression, doi:10.1016/S0167-7152(01)00124-9.
  Foundational ALD–quantile equivalence. Not yet DOI-verified.
- [ ] Jantre et al. (J. Stat. Theory Pract., 2021) — Bayesian QRNN via ALD + MCMC,
  arXiv:2009.13591. Not yet DOI-verified.
- [ ] Pouplin et al. (ICML 2024) — Relaxed Quantile Regression, arXiv:2406.03258.
  Confirmed accepted at ICML 2024.
- [ ] Son & Seok (Neurocomputing, 2025) — Stable output layers for MC Dropout,
  doi:10.1016/j.neucom.2025.131927. ScienceDirect listing confirmed.
- [ ] Tyralis & Papacharalampous (AI Review, 2024) — Predictive UQ review,
  doi:10.1007/s10462-023-10698-8. Springer listing confirmed; arXiv:2209.08307.

### Not yet searched (lower priority for Section 3)
- [ ] Gawlikowski et al. (2023, AI Review) — UQ survey, doi:10.1007/s10462-023-10562-9
- [ ] Wilson & Izmailov (NeurIPS 2020) — Bayesian perspective, arXiv:2002.08791
- [ ] Gasthaus et al. (AISTATS 2019) — spline quantile function RNNs
