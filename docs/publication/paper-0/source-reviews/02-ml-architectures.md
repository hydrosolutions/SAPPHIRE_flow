# 2. ML Architectures for Sub-Daily Streamflow

Literature review for Section 2 of the Paper 0 outline.
Last updated: 2026-03-30.

## Key Findings

1. **LSTM remains the top performer** for standard regression/simulation and
   short-term forecasting (Liu et al., HESS 2025 benchmark: 13 architectures,
   LSTM wins on KGE for regression and 7-day forecasting). Transformers win
   only for long-horizon autoregression (>7 days) and extreme events.
2. **MTS-LSTM is the only architecture designed for multi-resolution
   input/output** in hydrology. A simplified variant (MF-LSTM, HESS 2025)
   uses a single LSTM cell with per-frequency embeddings, achieving 5x speedup
   and supporting three frequencies.
3. **Transformers offer architectural advantages for future forcing** (TFT's
   variable selection, FutureTST's cross-attention), but no paper has applied
   them to sub-hourly streamflow or to the NWP temporal mismatch problem.
4. **RiverMamba** (NeurIPS 2025) is the first SSM for hydrology, outperforming
   GloFAS globally, but operates at daily resolution on a global grid.
5. **Differentiable hydrology (delta-HBV)** is the state of the art for
   physics-ML integration. delta-HBV outperforms LSTM on extremes (Song et al.,
   WRR 2026), while hard constraints (MC-LSTM) hurt extreme event performance
   (Frame et al., HESS 2022).
6. **The temporal mismatch problem** (coarse NWP → fine streamflow) is
   under-discussed. MTS-LSTM handles it natively; TFT could via separate
   encoders; no paper explicitly addresses it with attention mechanisms.
7. **Temporal disaggregation is a viable alternative to multi-resolution
   architectures.** Classical methods (MMRC, BLRP) are operational-ready with
   exact conservation and no training data. ML methods (LSTM with conservation
   layer, GANs, diffusion models) improve extreme events and spatial coherence.
   However, no major operational flood forecasting system disaggregates NWP
   temporally — all match model timestep to NWP resolution.
8. **Sub-hourly resolution has diminishing returns** for meso-scale catchments.
   Studies show the marginal benefit of going finer than hourly is small and
   catchment-dependent for catchments >100 km². ICON-CH2-EPS at hourly/2 km is
   convection-permitting — sub-hourly disaggregation adds synthetic structure
   below the model's physical resolution.
9. **CNN-LSTM and ConvLSTM dramatically outperform standalone LSTM** when gridded
   NWP input is available — NSE 0.83 vs 0.51 in one study (Hu et al., 2024;
   comparison is spatially-aware hybrid vs non-spatial LSTM, not distributed vs
   lumped in the traditional hydrological sense).
   For steep mountain catchments, the spatial dimension (elevation-dependent
   precipitation, snow/rain partitioning) may matter more than temporal
   resolution. No CNN-LSTM has been applied to Himalayan streamflow with
   NWP grids and DEM as input — this is a gap.
10. **No architecture has been tested at sub-hourly resolution with ensemble
   NWP forcing** — this is the intersection gap. Paper 0 should compare
   multi-resolution architectures vs disaggregate-then-model as alternative
   strategies, and spatial (basin-average vs elevation-band vs gridded) as an
   orthogonal axis. Whether ensemble NWP propagation is necessary (vs learned
   uncertainty) is investigated separately in the Nepal generalization study
   (see `docs/students/msc-tribhuvan-nepal-generalization.md`, three-paradigm
   comparison).

---

## 2.1 LSTM and Variants

### Standard LSTM

Note: The LSTM-hydrology literature is dominated by the Google/JKU Linz group
(Kratzert, Gauch, Klotz, Hochreiter, Nearing), who developed most variants
below and the NeuralHydrology framework. This reflects the field's youth rather
than lack of independent work — Liu et al. (HESS, 2025) provide an independent
benchmark confirming LSTM's dominance (§2.2).

Kratzert et al. (2018) established that a single LSTM trained regionally across
multiple catchments outperforms SAC-SMA + Snow-17. On 531 CAMELS basins, the
out-of-sample LSTM achieved median NSE = 0.69 vs calibrated SAC-SMA (0.64) and
the National Water Model (0.58) (Kratzert et al., WRR 2019).

**Key references**:
- Kratzert, F., Klotz, D., Brenner, C., Schulz, K., and Herrnegger, M.:
  Rainfall-runoff modelling using Long Short-Term Memory (LSTM) networks,
  Hydrol. Earth Syst. Sci., 22, 6005–6022,
  doi:10.5194/hess-22-6005-2018, 2018.
- Kratzert, F., Klotz, D., Herrnegger, M., Sampson, A. K., Hochreiter, S.,
  and Nearing, G. S.: Toward improved predictions in ungauged basins:
  Exploiting the power of machine learning, Water Resour. Res., 55,
  11344–11354, doi:10.1029/2019WR026065, 2019.

### Entity-Aware LSTM (EA-LSTM)

The input gate is computed **solely from static catchment attributes** (area,
elevation, soil, climate indices) and remains constant across all time steps.
This allows the network to learn that different catchment groups activate
different memory cells. On 531 CAMELS basins:

| Model | Median NSE | Catastrophic failure (NSE ≤ 0) |
|-------|-----------|-------------------------------|
| EA-LSTM ensemble | **0.71** | 0.2% |
| Basin-calibrated HBV | 0.68 | — |
| Regionally calibrated mHM | 0.53 | 6.5% |
| Regionally calibrated VIC | 0.31 | 9.2% |

**Reference**: Kratzert, F., Klotz, D., Shalev, G., Klambauer, G.,
Hochreiter, S., and Nearing, G.: Towards learning universal, regional, and
local hydrological behaviors via machine learning applied to large-sample
datasets, Hydrol. Earth Syst. Sci., 23, 5089–5110,
doi:10.5194/hess-23-5089-2019, 2019.

### MTS-LSTM (Multi-Timescale LSTM) — critical for sub-daily

The only architecture designed for multi-resolution input/output in hydrology.
Uses a hierarchical **dual-branch** design:

- **Coarse branch**: processes full historical context at **daily** resolution
  (365 timesteps). Captures long-term memory (soil moisture, snowpack,
  baseflow).
- **Fine branch**: initialised from coarse branch's hidden/cell states via
  learned linear transformation, then processes recent data at **hourly**
  resolution (336 timesteps = 14 days).
- Branches have **separate weights** for their respective timescale inputs.

**Key property**: different input variables can feed different branches — daily
forcings (longer lead time) to the coarse branch; hourly met data to the fine
branch. Exploits the physical property that "watersheds are damped systems: the
impact of high-frequency variation becomes less important the farther we look
back."

**Benchmark (516 CAMELS basins)**:

| Model | Daily NSE | Hourly NSE |
|-------|----------|-----------|
| Multi-forcing MTS-LSTM (best) | **0.811** | **0.812** |
| sMTS-LSTM | 0.762 | 0.776 |
| Naive hourly LSTM | ~0.748 | ~0.766 |
| National Water Model | 0.597 | 0.585 |

**Computational advantage**: Training 6–8 h on V100 (vs >24 h naive hourly).
Inference for 10 years × 516 basins: 13 min vs ~9 h naive — **~40x faster**.

**Reference**: Gauch, M., Kratzert, F., Klotz, D., Nearing, G., Lin, J., and
Hochreiter, S.: Rainfall-runoff prediction at multiple timescales with a single
Long Short-Term Memory network, Hydrol. Earth Syst. Sci., 25, 2045–2062,
doi:10.5194/hess-25-2045-2021, 2021.

### MF-LSTM (Multi-Frequency LSTM) — simplified MTS-LSTM

Uses a **single LSTM cell** instead of separate per-branch cells. Per-frequency
**embedding layers** (FC networks) map different input variable counts at each
frequency to a shared dimension. Retains state-of-the-art performance (median
NSE = 0.81) with **5x reduction in processing time**. Also tested a
**weekly–daily–hourly** three-frequency scheme.

**Reference**: Acuna Espinoza, E., Kratzert, F., Klotz, D., Gauch, M.,
Alvarez Chaves, M., Loritz, R., and Ehret, U.: Technical note: An approach for
handling multiple temporal frequencies with different input dimensions using a
single LSTM cell, Hydrol. Earth Syst. Sci., 29, 1749–1758,
doi:10.5194/hess-29-1749-2025, 2025.

### MC-LSTM (Mass-Conserving LSTM)

Architecturally constrains LSTM to conserve mass via a "trash cell" for
evapotranspiration losses. Internal states correlate with real-world stores
(soil moisture, snow). Set state-of-the-art for peak flow prediction.

**However**: Frame et al. (HESS 2022) showed MC-LSTM performed **worse than
standard LSTM on extreme events** — the hard mass conservation constraint may
be too rigid for out-of-distribution behavior (see Sect. 2.5).

**Reference**: Hoedt, P.-J., Kratzert, F., Klotz, D., Halmich, C.,
Holzleitner, M., Nearing, G. S., Hochreiter, S., and Klambauer, G.: MC-LSTM:
Mass-Conserving LSTM, Proc. 38th Int. Conf. Machine Learning (ICML), PMLR 139,
4275–4286, 2021.

### xLSTM (Extended LSTM)

Introduces exponential gating with two variants: sLSTM (scalar memory, new
memory mixing) and mLSTM (matrix memory, fully parallelisable, covariance
update rule). Integrated into NeuralHydrology. Not yet benchmarked for
hydrology but represents the architectural frontier for LSTM-family models.

**Reference**: Beck, M., Pöppel, K., Spanring, M., Auer, A., Prudnikova, O.,
Kopp, M., Klambauer, G., Brandstetter, J., and Hochreiter, S.: xLSTM:
Extended Long Short-Term Memory, Advances in Neural Information Processing
Systems 37 (NeurIPS), 2024.
https://proceedings.neurips.cc/paper_files/paper/2024/hash/c2ce2f2701c10a2b2f2ea0bfa43cfaa3-Abstract-Conference.html

### Other RNN Variants

- **GRU**: Trains faster, comparable accuracy. Farfan-Duran & Cea (Earth Sci.
  Inform., 17, 5289–5315, 2024, doi:10.1007/s12145-024-01454-9) showed GRU
  achieving NSE ~0.96–0.98 at 1-hour lead times in NW Spain. The hydrology community has standardised on LSTM partly due to the
  NeuralHydrology ecosystem.
- **ODE-LSTM**: Continuous-time LSTM using neural ODE solvers for irregular
  time series. Available in NeuralHydrology. (Lechner and Hasani, NeurIPS,
  2020, arXiv:2006.04418.)

---

## 2.2 Transformers and Attention

### Temporal Fusion Transformer (TFT)

TFT natively distinguishes three input categories: (1) static covariates, (2)
known past inputs, (3) **known future inputs** (e.g., NWP forecasts). Uses
Variable Selection Networks per input type, LSTM encoder for history, and
multi-head attention decoder that attends to known future covariates. This is
architecturally significant: future forcing enters the decoder pathway directly.

Rasiya Koya & Roy (2024): TFT outperforms standalone LSTM and vanilla
Transformer for daily streamflow on 2,610 Caravan catchments. Provides
interpretable attention weights.

**Reference**: Rasiya Koya, S. and Roy, T.: Temporal Fusion Transformers for
streamflow prediction: Value of combining attention with recurrence, J. Hydrol.,
637, 131301, doi:10.1016/j.jhydrol.2024.131301, 2024.

**Original TFT**: Lim, B., Arik, S. O., Loeff, N., and Pfister, T.: Temporal
Fusion Transformers for interpretable multi-horizon time series forecasting, Int.
J. Forecasting, 37(4), 1748–1764, doi:10.1016/j.ijforecast.2021.03.012, 2021.

### FutureTST

Dual-attention design: encoder applies patch-wise self-attention to endogenous
variables (past streamflow); decoder uses **cross-attention** where endogenous
encodings are queries and exogenous (meteorological) encoding is keys/values.
Future weather forcing is injected through the key-value pathway, allowing
selective attention to relevant future conditions at each forecast step.

Mean NSE 0.82 (1-day) to 0.67 (30-day). Outperforms LSTM and LSTM-ED. In the
Delaware River Basin, maintained skill to 14 days while VIC deteriorated beyond
4 days.

**Architectural relevance for temporal mismatch**: cross-attention could
naturally handle different temporal granularities between queries (fine
streamflow) and keys/values (coarse NWP) without explicit interpolation. **This
has not been demonstrated in the literature.**

**Reference**: Ambika, A. K., Tayal, K., Mishra, V., and Lu, D.: Novel Deep
Learning Transformer Model for Short to Sub-Seasonal Streamflow Forecast,
Geophys. Res. Lett., 52(14), e2025GL116707, doi:10.1029/2025GL116707, 2025.

### HESS 2025 Benchmark: 13 Architectures

Liu et al. (2025) benchmarked 11 transformer variants + DLinear + LSTM across
5 multi-source datasets (CAMELS 531 basins, Global 3,434 basins, ISMN soil
moisture, SWE, dissolved oxygen). Also tested pre-trained LLMs (GPT-3.5,
GPT-4-turbo, Gemini, Llama3) and time-series foundation models (TimeGPT,
Lag-Llama, TTMs) in zero-shot mode.

| Task | Winner | Detail |
|------|--------|--------|
| Regression (simulation) | **LSTM** | CAMELS KGE = 0.80; Informer 2nd, trailing 0.11 on global |
| Short-term forecast (7-day) | **LSTM** | KGE = 0.89 vs ETSformer 0.81 |
| Extreme high flows | Transformers | Non-stationary Transformer better FHV |
| Extreme low flows | Transformers | Better FLV for soil moisture and oxygen |
| Long-term autoregressive (30+ day) | **Transformers** | At 30 d: LSTM KGE = −0.03; transformers stable |
| Zero-shot forecasting | Foundation models | TimeGPT KGE = 0.68 vs supervised LSTM 0.50 |

**Bottom line**: LSTM wins for the core operational task (regression +
short-term forecasting). Transformers win for long-horizon autoregression and
extremes. Foundation models show competitive zero-shot performance.

**Reference**: Liu, J., Shen, C., O'Donncha, F., Song, Y., Zhi, W., Beck,
H. E., Bindas, T., Kraabel, N., and Lawson, K.: From RNNs to Transformers:
benchmarking deep learning architectures for hydrologic prediction, Hydrol.
Earth Syst. Sci., 29, 6811–6828, doi:10.5194/hess-29-6811-2025, 2025.

### Other Transformer Papers

- **Liu et al. (J. Hydrol., 2024)**: First Transformer to match LSTM on CAMELS,
  but only with non-recurrent skip connection. Suggests CAMELS daily may be near
  predictability ceiling. doi:10.1016/j.jhydrol.2024.131389.
- **Orozco Lopez et al. (WRR, 2024)**: Vanilla Transformer + Informer-style
  decoder, 1–14 day daily forecasting, directly ingests spatially distributed
  NWP. doi:10.1029/2023WR036337.
- **Demiray et al. (Water Sci. Tech., 2024)**: Transformer for **24-hour hourly
  resolution** (one of few sub-daily transformer papers). Up to 20% NSE
  improvement over LSTM/GRU. doi:10.2166/wst.2024.110.

---

## 2.3 State-Space Models (SSMs)

### RiverMamba (NeurIPS 2025)

First SSM for hydrology. Uses Bidirectional Mamba blocks with space-filling
curves (Hilbert-like) to linearise the 2D spatial grid of river points into 1D
sequences. Hindcast module pre-trained on ERA5 reanalysis; forecast module
integrates ECMWF HRES NWP via concatenation.

**Resolution**: 0.05° global grid, **daily**, up to 7-day lead time. Evaluated
on 3,366 gauged stations (2021–2023) from GRDC.

**Results**: Outperforms both AI baselines and physics-based GloFAS on all
metrics. Less F1-score degradation with increasing lead time. Ablation:
Mamba backbone outperforms Flash-Attention Transformer in training efficiency
and slightly in prediction quality.

**Sequence length scaling**:

| Architecture | Training | Inference (per step) | Memory |
|-------------|---------|---------------------|--------|
| LSTM | O(n) | O(1) | O(1) hidden state |
| Transformer | O(n²) | O(n) growing KV cache | O(n²) attention |
| SSM (Mamba) | O(n) parallel scan | O(1) | O(1) hidden state |

At ~1K tokens: Mamba ~3x faster than Transformer (12 ms vs 38 ms per token).
At 16K: 15x faster. The crossover where SSMs clearly dominate is ~1K–4K steps.
For sub-hourly forecasting (5-day at 15-min + 7-day lookback ≈ 1,150 steps),
the efficiency difference is modest but compounds across ensemble members and
catchments.

**Reference**: Shams Eddin, M. H., Zhang, Y., Kollet, S., and Gall, J.:
RiverMamba: A State Space Model for Global River Discharge and Flood
Forecasting, Advances in Neural Information Processing Systems 38 (NeurIPS),
2025. arXiv:2505.22535.

---

## 2.4 Physics-Informed Hybrids

### Hybrid process-ML approaches

Three hybridisation strategies exist (Du and Pechlivanidis, Commun. Earth
Environ., 2025):
1. **Loose**: sequential — process model output feeds into LSTM as input
2. **Moderate**: shared feature space
3. **Tight**: feedback between LSTM and process model

Tight hybridisation performs best: NSE improved 12–26%, KGE improved 6–68%
over standalone models; also improved hydrological signatures and extreme flow
simulation.

**Key references**:
- Du, Y. and Pechlivanidis, I. G.: Hybrid approaches enhance hydrological
  model usability for local streamflow prediction, Commun. Earth Environ.,
  2025, doi:10.1038/s43247-025-02324-y.
- Konapala, G., et al.: Machine learning assisted hybrid models can improve
  streamflow simulation in diverse catchments across the conterminous US,
  Environ. Res. Lett., 15, 104022, doi:10.1088/1748-9326/aba927, 2020.
- Liu, J., et al.: A national-scale hybrid model for enhanced streamflow
  estimation, Hydrol. Earth Syst. Sci., 28, 2871–2893,
  doi:10.5194/hess-28-2871-2024, 2024.

### Differentiable hydrology (delta-HBV) — state of the art

Neural networks learn a global mapping from catchment attributes to
process-based model parameters, making the entire system end-to-end
differentiable. The delta-HBV family (Feng, Shen group, Penn State) dominates:

- **dPL framework**: NN learns parameter mapping; drastically outperforms
  evolutionary calibration; requires only ~12.5% of training data (Tsai et al.,
  Nat. Commun., 12, 5988, doi:10.1038/s41467-021-26107-z, 2021).
- **delta-HBV**: approaches LSTM accuracy while outputting physically meaningful
  variables (ET, soil moisture, SWE, baseflow) not in training loss (Feng et
  al., WRR, 58, e2022WR032404, doi:10.1029/2022WR032404, 2022).
- **Global-scale**: 3,753 basins, approaches LSTM for temporal generalisation,
  stronger spatial extrapolation (Feng et al., GMD, 17, 7181–7198,
  doi:10.5194/gmd-17-7181-2024, 2024).
- **Extremes**: delta-HBV1.1p **outperformed LSTM for ≥5-year return period
  events** — mass balance and first-order exchange terms constrain responses
  and reduce peak underestimation (Song et al., WRR, 2026,
  doi:10.1029/2025WR040414).
- **National-scale multiscale**: ~37 km² resolution, Muskingum-Cunge routing,
  trained on 2,807 basins, evaluated on 4,997. Median daily NSE 0.68 vs NWM3.0
  0.48 (Song et al., WRR, 61, e2024WR038928, doi:10.1029/2024WR038928, 2025).

**Review paper**: Shen, C., et al.: Differentiable modelling to unify machine
learning and physical models for geosciences, Nat. Rev. Earth Environ., 4,
552–567, doi:10.1038/s43017-023-00450-9, 2023.

### Hard constraints: a cautionary tale

Frame et al. (HESS, 2022): MC-LSTM performed **worse than standard LSTM on
extreme events**, even when extremes were excluded from training. The hard
architectural mass conservation constraint may be too rigid for
out-of-distribution behaviour.

**But**: Song et al. (WRR, 2026) showed delta-HBV **outperformed** LSTM on
extremes — suggesting **soft structural physics priors** (differentiable process
models) are more effective than **hard architectural constraints** (MC-LSTM).

**Reference**: Frame, J. M., et al.: Deep learning rainfall-runoff predictions
of extreme events, Hydrol. Earth Syst. Sci., 26, 3377–3392,
doi:10.5194/hess-26-3377-2022, 2022.

### NeuralHydrology hybrid support

Work is underway to incorporate process-based models directly into the
NeuralHydrology framework (CIROH developer conference presentation on
integrating CFE). Experimental/in-progress.

---

## 2.5 The Temporal Mismatch Problem

NWP outputs are typically 3–6-hourly; streamflow targets may be 15-min or
hourly. How does each architecture handle this?

### Architecture-specific approaches

| Architecture | Approach | Tested? |
|-------------|---------|---------|
| **MTS-LSTM** | Native dual-branch: coarse NWP → daily branch, fine obs → hourly branch. State transfer via learned linear layers. | Yes (daily+hourly, Gauch et al. 2021) |
| **MF-LSTM** | Single cell, per-frequency embeddings. Tested weekly+daily+hourly. | Yes (Acuna Espinoza et al. 2025) |
| **TFT** | Separate Variable Selection Networks per input type. Could route coarse NWP and fine obs through different encoders. | Not tested for temporal mismatch |
| **FutureTST** | Cross-attention: fine queries, coarse keys/values. Could naturally align different temporal granularities. | Not tested for temporal mismatch |
| **RiverMamba** | Global grid, single resolution. No multi-resolution support. | No |
| **Process models** | Temporal disaggregation of NWP precipitation (cascade-based stochastic, or uniform distribution). | Standard practice |

### Key gap

**No paper explicitly addresses the temporal mismatch between coarse NWP
forcing and fine-resolution streamflow targets using attention mechanisms or
any ML approach.** MTS-LSTM handles daily+hourly natively but has not been
tested with 3-hourly NWP + 15-min streamflow. The cross-attention mechanism
in FutureTST is architecturally the most natural fit (fine queries attending
to coarse keys/values) but this has not been demonstrated.

### Temporal disaggregation as a preprocessing alternative

Rather than building multi-resolution capabilities into the forecasting
architecture itself, an alternative strategy is to **disaggregate NWP
precipitation to fine resolution as a preprocessing step**, then feed a
standard single-resolution model. This "disaggregate-then-model" approach is
simpler to implement, leverages proven single-resolution architectures (LSTM),
and decouples the two problems (temporal downscaling vs rainfall-runoff).

#### Classical stochastic methods

**Microcanonical multiplicative random cascade (MMRC)**: Decomposes
precipitation from coarse to fine scales by multiplying by random weights at
each cascade level. Microcanonical variants conserve mass exactly at each
scale. Recent hybrids combining MMRC with clustering (k-means, DBSCAN) improve
extreme event representation (Chowdhury & Saha, Earth Sci. Inform., 17(4),
2024, doi:10.1007/s12145-024-01309-3). Limitation: tends to overstate extreme
precipitation due to predefined classification assumptions.

**Bartlett-Lewis Rectangular Pulse (BLRP)**: Poisson-cluster-process-based
stochastic rainfall generator. Simulates storms as clusters of rectangular
pulses in time. Recent advances: Onof and Wang (HESS, 24, 2791, 2020,
doi:10.5194/hess-24-2791-2020). Sub-hourly disaggregation scheme with
adjusting procedures: Kossieris et al. (J. Hydrol., 2016,
doi:10.1016/j.jhydrol.2016.07.036). Known limitation: underestimates hourly
extremes while overestimating daily extremes.

**RainFARM**: Nonlinear transformation of a Gaussian random field that
extrapolates the large-scale spatio-temporal power spectrum to unresolved
scales. Conserves information at larger scales. Can generate multiple
stochastic realisations. Implemented in pysteps (spatial component).
Evaluated for complex orography by Terzago et al. (NHESS, 18, 2825, 2018,
doi:10.5194/nhess-18-2825-2018).

**Key advantage of classical methods**: No training data or GPU required.
Well-understood statistical properties. Widely used in operational hydrology.
Inference in seconds on CPU.

#### ML-based methods

**LSTM for precipitation disaggregation**: Oates et al. (2025) disaggregate
daily precipitation to **half-hourly intervals** using an LSTM with auxiliary
hourly weather measurements. A novel normalisation layer integrated into the
network **guarantees conservation of daily totals** — the sum of disaggregated
half-hourly values exactly matches the observed daily total. Validated across
five Australian climate zones; outperforms other models at hourly aggregation.
(Stoch. Environ. Res. Risk Assess., 39, 2859–2872,
doi:10.1007/s00477-025-02996-0.)

**SpateGAN-ERA5**: Conditional GAN with 3D convolutional residual blocks for
simultaneous spatial + temporal downscaling of ERA5 from 24 km/1 h to
**2 km/10 min**. Trained on Germany, validated in US and Australia.
Inference ~0.04 s per patch on A100. Generates 100-member ensembles for
uncertainty quantification. **Does not guarantee mass conservation** —
stochastic generator that produces plausible realisations. (Glawion et al.,
npj Clim. Atmos. Sci., 2025, doi:10.1038/s41612-025-01103-y.) Original
spateGAN for temporal-only disaggregation of radar data: Scher &
Pessenteiner (HESS, 25, 3207–3225, 2021, doi:10.5194/hess-25-3207-2021).

**Spatiotemporal Video Diffusion (STVD)**: Two-module approach — deterministic
UNet-based downscaler with spatio-temporal factorised attention, plus a
conditional video diffusion model that captures residual stochastic details.
Diffusion models avoid GAN mode collapse and capture multimodal distributions
more reliably. Tested on FV3GFS output. (Srivastava, Yang, Kerrigan,
Dresdner, McGibbon, Bretherton, and Mandt, NeurIPS 2024,
arXiv:2312.06071.)

**Residual Corrective Diffusion (CorrDiff)**: UNet predicts the mean, then a
corrector diffusion model predicts the stochastic residual. Downscales 25 km
reanalysis to 2 km over Taiwan. **652x faster and 1310x more energy-efficient
than WRF**. (Mardani et al., Commun. Earth Environ., 2025,
doi:10.1038/s43247-025-02042-5.)

**Hard-constrained deep learning**: Architecture-agnostic constraint layers
(Scaled Additive Constraint Layer, Multiplicative Constraint Layer) that
**guarantee mass/energy conservation** between low-resolution and
high-resolution outputs. Can be applied to any CNN/UNet/GAN generator.
(Harder, Hernandez-Garcia, Ramesh, Yang, Sattegeri, Szwarcman, Watson, and
Rolnick, JMLR, 24(365), 2023; Geiss & Hardin, Artif. Intell. Earth Syst.,
2(1), doi:10.1175/AIES-D-21-0012.1, 2023.)

**GAN/VAE-GAN on NWP forecasts**: Harris et al. (2022) downscaled IFS
(ECMWF) hourly forecasts to high-resolution NIMROD radar over UK — one of the
few methods tested directly on operational NWP output rather than reanalysis.
Matches state-of-the-art pointwise post-processing in CRPS, power spectra, and
rank histograms. Produces spatially coherent precipitation maps.
(JAMES, 14, 2022, doi:10.1029/2022MS003120.)

#### Conservation constraints — critical for hydrology

| Method | Conservation mechanism |
|--------|----------------------|
| MMRC (microcanonical) | By construction (multiplicative weights sum to 1) |
| BLRP + adjusting procedures | Post-hoc adjustment to match coarser totals |
| Oates et al. LSTM | Normalisation layer in network; exact conservation |
| Hard-constrained DL (ScAddCL/MultCL) | Architecture-agnostic constraint layers; exact |
| SpateGAN / diffusion models | **Not guaranteed** — conservation only in expectation |
| RainFARM | Conserves at input resolution |

#### Comparison: ML vs classical vs multi-resolution architecture

| Criterion | Classical (Cascade/BLRP) | ML disaggregation | Multi-resolution architecture (MTS-LSTM) |
|-----------|-------------------------|-------------------|----------------------------------------|
| Conservation | Exact (microcanonical) | Only with constraint layer | N/A (end-to-end) |
| Extreme events | Cascade overestimates; BLRP underestimates | Generally better (GANs, diffusion) | Not evaluated at sub-hourly |
| Spatial coherence | Limited | Strong (GANs, diffusion) | N/A (point-based) |
| Computational cost | Seconds, CPU | Seconds–minutes, GPU | Single model training |
| Training data needed | Parameter fitting only | Large high-res datasets | Standard hydro training |
| Implementation complexity | Low | Moderate | High (architecture modification) |
| Error propagation | Adds a step → compounds uncertainty | Adds a step → compounds uncertainty | End-to-end optimisation |

### Operational practice: no system disaggregates NWP temporally

Notably, **no major operational flood forecasting system uses temporal
disaggregation of NWP precipitation**:

| System | NWP input resolution | Hydro model timestep | Disaggregation? |
|--------|---------------------|---------------------|-----------------|
| **EFAS** (Europe) | 6-hourly (ECMWF ENS) | 6-hourly (LISFLOOD) | No |
| **GloFAS** (Global) | Daily (ECMWF ENS runoff) | Daily routing | No |
| **FOEN/BAFU** (Switzerland) | Hourly (ICON/COSMO) | Hourly (WaSiM in FEWS) | No |
| **UK EA** (England/Wales) | Hourly (MOGREPS-UK, 2.2 km) | Varies | No — STEPS blends nowcast + NWP |
| **NOAA NWM** (USA) | Hourly (HRRR/RAP) | Hourly | No |

All systems match the hydrological model timestep to the NWP resolution.
FOEN/BAFU — the direct Swiss analogue — runs WaSiM at hourly timestep with
hourly ICON input and outputs hourly streamflow.

### Error propagation concerns

- Up to **50% of hydrological model error is attributable to precipitation
  input uncertainty** (Frontiers in Water, 2022,
  doi:10.3389/frwa.2022.836554). Adding a disaggregation step injects
  additional uncertainty on top of already-uncertain NWP precipitation.
- Disaggregation **preserves volume but distorts timing**: MMRC models conserve
  total rainfall but introduce artificial sub-hourly patterns. For flood peaks,
  timing of intense rainfall matters more than total volume.
- **Ensemble consistency**: Stochastic disaggregation of ensemble members
  either (a) adds identical sub-hourly patterns (destroying ensemble
  independence) or (b) adds different stochastic patterns (inflating spread
  with non-physical uncertainty). This makes threshold exceedance probabilities
  unreliable.
- Accumulating or disaggregating bias-corrected QPF introduces **frequency
  bias** at the finer resolution (WAF, 2022, doi:10.1175/WAF-D-21-0083.1).

### When sub-hourly matters (and when it doesn't)

- **Sub-hourly resolution is critical for**: urban hydrology with impervious
  surfaces, very small catchments (<10 km²), flash flood events.
- **Hourly is sufficient for**: meso-scale catchments (100–1000 km²) typical
  of BAFU stations, where the catchment itself acts as a temporal smoother.
- Ficchì, Perrin, and Andréassian (J. Hydrol., 538, 454–470, 2016,
  doi:10.1016/j.jhydrol.2016.04.016) analysed the impact of temporal resolution
  on 2400 flood events across 240 catchments: hourly outperforms daily for up
  to 4-day lead times, but **marginal benefit of going finer than hourly is
  small and catchment-dependent**. Their follow-up (J. Hydrol., 575, 1308–1327,
  2019, doi:10.1016/j.jhydrol.2019.05.084) showed that flux-matching
  disaggregation of daily models to sub-daily time steps produces performance
  approaching native sub-daily calibration — suggesting hydrological models are
  robust to precipitation timing errors.

### Swiss/Alpine-specific considerations

- **ICON-CH2-EPS is convection-permitting** at 2.1 km: hourly output already
  captures convective precipitation timing. Disaggregating to 15-min adds
  synthetic structure below the model's physical resolution.
- **MeteoSwiss CombiPrecip** provides radar-gauge merged precipitation at 5-min
  resolution. For 0–6 h lead times, blending radar nowcasts (genuine
  sub-hourly information) with NWP via STEPS-like approaches (pysteps) is
  preferable to fabricating sub-hourly NWP patterns.
- Wüest, Frei, Altenhoff, Hagen, Litschi, and Schär (Int. J. Climatol., 30,
  1764–1775, 2010, doi:10.1002/joc.2025) developed gridded hourly Swiss
  precipitation by disaggregating daily gauge analyses using hourly radar
  patterns — errors below 25% on the Swiss Plateau but larger in Alpine
  valleys due to radar shielding.
- The GWEX-MRC weather generator combines daily stochastic generation with
  MMRC disaggregation for sub-daily resolution in Switzerland, with parameters
  spatially interpolated using kriging with elevation — designed for climate
  impact studies, not operational forecasting.

### Nepal-specific: station-based disaggregation with 10-min data

Nepal's DHM operates ~168 tipping-bucket rain gauge stations (0.2 mm
resolution) with data transmitted every 5 minutes during monsoon, plus 22 full
automatic weather stations. This dense sub-hourly archive enables
observation-based disaggregation methods that are unavailable in many
operational contexts.

**Method of Fragments (MOF)**: The most promising approach. A non-parametric
technique that resamples vectors of "fragments" representing the observed
sub-hourly distribution within a coarser timestep. For each 3-hourly ECMWF IFS
total, an analog period is selected from the historical 10-min station archive
based on similarity (amount, season, time of day), and its temporal pattern is
applied as the disaggregation template. Conservation is exact by construction.

Three MOF variants exist (Li et al., Int. J. Climatol., 2018,
doi:10.1002/joc.5438): (1) single-site interval-based, (2) regionalised
(draws from neighbouring stations), (3) multi-site (preserves inter-station
correlation). The spatial variant S-MOF is available open-source
(github.com/KBreinl/S-MOF; Breinl and Di Baldassarre, J. Hydrol. Reg. Stud.,
21, 126–146, 2019).

**Conditioning on precipitation regime**: MOF quality improves significantly
when analog selection is conditioned on convective vs stratiform regime. Nepal's
monsoon has distinct regimes detectable from station covariates:
- **Afternoon-evening convective peaks** along the Lesser Himalayas
  (~2,000–2,200 m), driven by mountain-valley wind circulation
- **Early-morning stratiform events** along the southern margin (~500–700 m)
- ~60% of monsoon rainfall occurs during nighttime hours

Classification from 10-min intensity variance, temperature, and time of day
can select appropriate fragment templates (peaked/intermittent for convective;
smooth/continuous for stratiform). Tremblay (J. Atmos. Sci., 62, 1513–1528,
2005, doi:10.1175/JAS3411.1) successfully classified ~95% of 6-hour amounts
into convective/stratiform using weather state and cloud type.

**Real-time blending for short lead times**: The system already ingests
real-time 10-min station data. A simple operational blending scheme:
- **0–1 h**: direct station observations
- **1–6 h**: weighted blend of extrapolated station patterns + NWP-disaggregated
- **>6 h**: pure NWP disaggregated via MOF/analog

Nepal has no operational radar (first installed 2019, not yet used for
precipitation estimation; beam blockage is severe in mountain terrain), so
radar-based nowcasting/blending is not an option for v1.

**Data quality caveats**: Tipping buckets fail with solid precipitation above
~3,000 m. Few stations exist at high elevations despite significant
precipitation there. Raw 5-min transmission data may not be archived at
sub-hourly resolution — **verifying DHM's sub-hourly archival policy is a
critical prerequisite**.

### Nepal-specific: the spatial dimension (elevation bands vs gridded input)

For Nepal basins spanning 1,000–6,000 m elevation, basin-average precipitation
is **clearly inadequate**. The precipitation-elevation relationship is
non-linear with a mid-elevation maximum:

| Elevation band | Typical annual precip | Dominant mechanism |
|---------------|----------------------|-------------------|
| Lowlands (100–500 m) | 1,500–2,000 mm | Large-scale monsoon |
| Middle Hills (1,000–3,000 m) | Up to 5,000+ mm | Orographic maximum |
| High Himalaya (>3,500 m) | Rapidly decreasing | Moisture depleted |
| Trans-Himalaya | 200–500 mm | Rain shadow |

(Putkonen, Arct. Antarct. Alp. Res., 36(2), 244–248, 2004,
doi:10.1657/1523-0430(2004)036[0244:CSARDA]2.0.CO;2: Annapurna transect peaks
at 5,032 mm/yr at ~3,000 m, drops to ~1,100 mm/yr north of the crest.)

A single basin-average value destroys the elevation signal, which controls:

1. **Snow/rain partitioning** — the same storm produces rain below ~2,000–3,000 m
   and snow above. Getting this partition wrong dramatically affects the
   hydrograph (over/underestimated peak, wrong melt timing). The 0 °C isotherm
   varies from ~2,500 m in winter to ~5,000 m during monsoon peak.
2. **Orographic enhancement** — precipitation more than doubles between lowlands
   and mid-elevations. A linear lapse rate (standard HBV: 10%/100 m) is wrong;
   the real relationship has a breakpoint near 3,100 m (Dimri et al.,
   Atmos. Res., 2024).
3. **Runoff generation timing** — rain at 1,500 m contributes to runoff
   immediately; snow at 4,500 m is stored for weeks/months.

**ECMWF IFS at ~9 km covers a 500 km² tributary with only ~6–7 grid cells.**
Averaging these cells erases the elevation gradient entirely.

**Two approaches for ML models:**

1. **Elevation-band extraction**: Extract NWP values per elevation band
   (e.g., 500 m intervals, as in HBV). Each band receives adjusted
   precipitation and temperature via lapse rates. Feed the band-resolved
   values as a vector to the LSTM. This extends the current SAPPHIRE
   `GridExtractor` with a fourth method (`elevation_band`) alongside
   `basin_average`, `nearest_point`, and `external`.

2. **Gridded/raster input with CNN front-end**: Feed the raw NWP grid cells
   covering the catchment as a 2D spatial input. A CNN extracts spatial
   features (orographic gradients, snow line position); LSTM processes the
   temporal sequence. Wang & Karimi (HESS, 28, 2107, 2024) showed that
   **LSTM trained on spatially distributed rainfall significantly outperformed
   basin-averaged LSTM**. Li et al. (J. Hydrol., 2023) achieved NSE
   0.79–0.92 with CNN-LSTM on Tibetan Plateau mountain basins. Pokharel &
   Roy (J. Hydroinf., 2024) found CNN-LSTM improved in 66% of basins (21/32)
   over standalone LSTM.

For Paper 0's scope, elevation-band extraction is the pragmatic choice —
it requires no architecture changes (just a longer input vector) and directly
addresses the snow/rain partitioning problem. Gridded CNN-LSTM is a natural
extension for a follow-up study.

### Implication for Paper 0

The disaggregate-then-model approach is a **viable and simpler alternative to
multi-resolution architectures** for the temporal mismatch problem.
For SAPPHIRE's v0 (ICON-CH2-EPS, already hourly), disaggregation is
unnecessary — hourly NWP directly drives hourly or sub-hourly models. For v1
(Nepal, ECMWF IFS at 3–6-hourly), the options are:

1. **Multi-resolution architecture** (MTS-LSTM/MF-LSTM): end-to-end, no
   intermediate error, but requires architecture modification and is untested
   at sub-hourly.
2. **Station-based MOF + standard LSTM**: leverages Nepal's 10-min station
   archive, exact conservation, regime-conditioned, no GPU needed. Requires
   verified sub-hourly data archive.
3. **Classical cascade (MMRC) + standard LSTM**: fallback if station archive
   is insufficient. Exact conservation, no training data, but less realistic
   sub-hourly timing.
4. **ML disaggregation + standard LSTM**: better extreme events and spatial
   coherence than classical, but requires training data and may not conserve
   mass without constraint layers.

Additionally, the **spatial dimension** (elevation-band extraction or
gridded CNN-LSTM) may matter more than temporal resolution for Nepal's steep
catchments. A standard LSTM with elevation-band-resolved hourly NWP input
could outperform a sub-hourly model with basin-average precipitation.

Paper 0 should frame the problem as having two orthogonal axes:
- **Temporal**: native multi-resolution architecture vs disaggregate-then-model
- **Spatial**: basin-average vs elevation-band vs gridded input

The combination of these choices defines the experimental matrix. The
hypothesis that elevation-band resolution matters more than sub-hourly
temporal resolution for Nepal's steep catchments is testable and would be
a novel contribution.

---

## 2.6 CNN-LSTM and Spatially Distributed Architectures

All architectures in Sections 2.1–2.4 treat precipitation as a **scalar time
series** (basin-average or single station). For catchments with strong spatial
gradients — steep mountain basins, large catchments with heterogeneous rainfall
— this discards critical information. CNN-LSTM architectures process gridded
NWP rasters directly, extracting spatial features before temporal modelling.

### Architecture variants

**CNN+LSTM (sequential)**: A 2D CNN processes each timestep's spatial grid
independently, extracting features via convolutional and pooling layers. The
CNN output is flattened into a feature vector; the sequence of feature vectors
feeds an LSTM. Input is structured as a "weather video" — T frames, each a
multi-channel image (e.g., precipitation, T_max, T_min on the NWP grid).
Anderson & Radic (HESS, 26, 795–825, 2022, doi:10.5194/hess-26-795-2022)
established this approach for 226 catchments in southwestern Canada using ERA5,
achieving median NSE = 0.68. Sensitivity experiments showed the CNN learned
physically meaningful spatial patterns — attending to precipitation in upstream
areas and temperature in snow-dominated regions. Code:
github.com/andersonsam/cnn_lstm_era.

**ConvLSTM (convolutional LSTM)**: Replaces matrix multiplications in LSTM
gates with convolutions, so the hidden state is a **2D spatial grid** rather
than a 1D vector (Shi et al., NeurIPS, 2015, arXiv:1506.04214). Spatial and
temporal processing happen simultaneously at every timestep, preserving
spatial structure throughout. Dehghani et al. (Ecol. Inform., 75, 102119,
2023) found **ConvLSTM outperformed CNN+LSTM** for hourly streamflow
forecasting (NSE 0.98–0.99), with the most accurate peak flow timing and
magnitude. (Caveat: single-basin result — such high NSE values are unlikely
to generalise across diverse catchments.) Borgel et al. (GMD, 18, 2005–2019, 2025,
doi:10.5194/gmd-18-2005-2025) used ConvLSTM to predict runoff for **97 Baltic
rivers simultaneously** from atmospheric forcing grids alone.

**CNN-LSTM with attention**: Adds spatial attention to learn which grid cells
matter most for a given catchment. Tested on the Qinghai-Tibet Plateau with
NSE 0.79–0.92 (Zhang et al., Scientific Reports, 2025,
doi:10.1038/s41598-024-84810-5).
No paper yet applies spatial attention specifically to learn orographic
precipitation patterns from NWP grids.

### Key results: spatial vs lumped input

| Study | Spatial method | NSE (spatial) | NSE (lumped LSTM) | Domain |
|-------|---------------|--------------|-------------------|--------|
| Hu et al. (J. Hydrol. Reg. Stud., 51, 2024, doi:10.1016/j.ejrh.2023.101652) | CNN-LSTM (GPM+SM grids) | **0.834** | 0.510 (standalone LSTM, no spatial input) | Yellow River source |
| Pokharel & Roy (J. Hydroinf., 26, 2024, doi:10.2166/hydro.2024.114) | CNN-LSTM (ERA5-Land P+T) | improved 21/32 | baseline | Nebraska, 32 basins |
| Wang & Karimi (HESS, 28, 2107, 2024) | Spatially recursive LSTM | +0.113 KGE | baseline | Great Lakes, 141 basins |
| Li et al. (J. Hydrol., 620, 2023) | CNN-LSTM + multi-task | 0.79–0.92 | lower | Tibetan Plateau |
| Dehghani et al. (Ecol. Inform., 75, 2023) | ConvLSTM | 0.98–0.99 | lower | Hourly, single basin |

The improvement is **most pronounced for large basins** (>2,000 km²) and
basins with strong spatial precipitation gradients. Wang & Karimi (2024) found
improvement was negligible for small basins (<2,000 km²). For mountain
terrain, the improvement should be even larger due to elevation-dependent
precipitation and snow/rain partitioning.

### Handling variable-size catchments

Different catchments cover different numbers of NWP grid cells. Approaches:
- **Fixed bounding box with masking**: pad all catchments to the same grid
  size, mask out-of-catchment cells with zeros (most common).
- **Regional model with shared grid**: one large grid covering all catchments
  (Anderson & Radic, 2022).
- **Global average/max pooling**: CNN produces fixed-size output regardless
  of input grid dimensions.

### Static spatial information (DEM, topography)

DEM and topographic variables (slope, aspect) enter as **static channels**
concatenated with dynamic meteorological channels. The CNN can then learn
elevation-dependent patterns (orographic enhancement, snow line position)
directly from the data. Anderson & Radic (2022) confirmed the CNN learned to
attend to temperature in snow-dominated regimes, suggesting implicit
capture of snow processes. No paper has explicitly demonstrated CNN-learned
snow/rain partitioning from temperature + DEM grids — this would be a **novel
contribution** relevant to Himalayan catchments.

### Ensemble NWP handling

Three strategies exist for processing ensemble members through CNN-LSTM,
**none systematically compared** in the literature (a research gap):

1. **Ensemble statistics as channels**: mean, spread, and selected quantiles
   as input channels. Reduces dimensionality but discards individual member
   structure.
2. **Member-by-member (shared weights)**: each ensemble member passes through
   the same CNN-LSTM, producing per-member streamflow forecasts. Preserves
   full ensemble structure. Computationally expensive (N forward passes).
3. **All members as channels**: stack N members as separate channels (e.g., 21
   ICON-CH2-EPS members = 21 precip channels). CNN learns inter-member
   relationships. Memory-intensive for large ensembles.

### Graph Neural Networks as complementary approach

GNNs operate on graph-structured data (river networks) rather than regular
grids. They are **complementary, not competitive** with CNN-LSTM:
- **CNNs**: best for extracting spatial features from gridded NWP (orographic
  gradients, precipitation fields).
- **GNNs**: best for routing and multi-site prediction along river networks.

Kratzert et al. (EGU, 2021) proposed GNN for routing water along river
networks; Sun et al. (HESS, 26, 5163–5184, 2022,
doi:10.5194/hess-26-5163-2022) applied GNN to an alpine snow-dominated
watershed. A **hybrid CNN+GNN** (CNN for NWP spatial features, GNN for river
routing) would combine both advantages but has not been demonstrated.

### Practical considerations

- **Training data**: 20+ years per basin (Pokharel & Roy, 2024) or regional
  pretraining across hundreds of catchments with fine-tuning.
- **Computational cost**: ~2–3x slower training than standard LSTM, feasible
  on a single modern GPU. Borgel et al. (2025) trained ConvLSTM for 97 rivers
  in 400 epochs on standard hardware.
- **NeuralHydrology support**: the framework does **not natively support
  CNN-LSTM or ConvLSTM** — all models expect 1D time-series input. Would
  require a custom model class via the TemplateModel API, or a separate
  PyTorch implementation.
- **Transfer learning**: pretraining on diverse catchments, then fine-tuning on
  target domain, is effective and widely used.

### Relevance for SAPPHIRE / Paper 0

CNN-LSTM is the natural architecture for Nepal's steep catchments where
basin-average NWP is inadequate. The experimental design should compare:

1. **Standard LSTM** with basin-average forcing (baseline)
2. **Standard LSTM** with elevation-band-resolved forcing (simple spatial)
3. **CNN-LSTM** with gridded NWP + DEM as static channel (full spatial)
4. **ConvLSTM** variant if hourly prediction is the target (preserves spatial
   structure in hidden state)

The combination with the temporal axis (hourly vs disaggregated sub-hourly)
creates a 3×2 experimental matrix that would systematically quantify whether
spatial or temporal resolution matters more for steep catchments — a question
the literature has not answered.

---

## 2.7 The NeuralHydrology Framework

The de facto standard for ML-hydrology research. Built on PyTorch, maintained
by JKU Linz.

**Supported architectures** (v1.12–1.13):
- CudaLSTM, EA-LSTM, MTS-LSTM/sMTS-LSTM, MC-LSTM, xLSTM, ODE-LSTM,
  Transformer, MultiheadForecastLSTM, GRU (via extension)
- HybridModel / BaseConceptualModel for differentiable hybrids

**Probabilistic heads**: Regression, GMM, CMAL, UMAL — the latter three for
probabilistic prediction.

**Reference**: Kratzert, F., Gauch, M., Nearing, G., and Klotz, D.:
NeuralHydrology — A Python library for Deep Learning research in hydrology,
J. Open Source Softw., 7(71), 4050, doi:10.21105/joss.04050, 2022.

---

## Summary: Architecture Selection for Sub-Daily Ensemble Forecasting

| Architecture / Strategy | Strengths | Limitations | Sub-daily ready? |
|------------------------|----------|-------------|-----------------|
| MTS-LSTM | Native multi-resolution, proven hourly, 40x faster, NeuralHydrology support | No sub-hourly testing, no ensemble NWP testing | **Best candidate** |
| MF-LSTM | Simpler, 5x faster, 3-frequency support | Very new (2025), less validated | Strong candidate |
| **CNN-LSTM** | **Gridded NWP input, learns spatial patterns (orographic, snow line), NSE 0.83 vs 0.51 lumped** | **Not in NeuralHydrology, variable catchment sizes, ~2–3x slower** | **Best for steep catchments** |
| **ConvLSTM** | **Preserves spatial structure in hidden state, NSE 0.98–0.99 hourly, multi-river simultaneous** | **Memory-intensive, not in NeuralHydrology** | **Strong for hourly** |
| MMRC + standard LSTM | Simplest pipeline, exact conservation, no training data, CPU-only | Adds error source, distorts sub-hourly timing, overwrites extremes | **Quick baseline** |
| MOF + standard LSTM | Observation-based templates, regime-aware, exact conservation | Requires sub-hourly station archive | **Best for Nepal** |
| TFT | Future forcing handling, interpretability, attention | Daily only tested, no multi-resolution | Needs adaptation |
| FutureTST | Cross-attention for future forcing, strong 30-day skill | Daily only, new (2025) | Needs adaptation |
| RiverMamba | Global scale, outperforms GloFAS, O(n) scaling | Daily only, global grid (not catchment), very new | Future potential |
| delta-HBV | Physics priors, outperforms LSTM on extremes, interpretable | No ensemble NWP tested, sub-daily unexplored | Research interest |
| Standard LSTM | Proven, robust, NeuralHydrology, wins benchmarks | No native multi-resolution, no spatial input | Baseline |

**For Paper 2's experimental design**: The problem has two orthogonal axes —
temporal resolution and spatial resolution — creating a systematic matrix:

**Spatial axis** (rows):
1. Basin-average forcing (standard LSTM baseline)
2. Elevation-band-resolved forcing (standard LSTM, longer input vector)
3. Gridded NWP + DEM (CNN-LSTM / ConvLSTM)

**Temporal axis** (columns):
1. Direct hourly (null hypothesis — is sub-hourly even needed?)
2. Disaggregate-then-model (MOF or MMRC + standard architecture)
3. Multi-resolution architecture (MTS-LSTM/MF-LSTM)

Additionally: TFT as the attention-based comparison, delta-HBV as the
physics-informed hybrid, ridge regression as simple baseline. The hypothesis
that spatial resolution matters more than temporal resolution for Nepal's steep
catchments is testable and would be a novel contribution.

---

## References Not Yet Fully Verified

Updated 2026-03-30 via CRAAB review.

### Verified during CRAAB review (2026-03-30)
- [x] Hu et al. (J. Hydrol. Reg. Stud., 51, 2024) — **DOI confirmed**:
  10.1016/j.ejrh.2023.101652. NSE 0.834 vs 0.510 verified. Note: comparison
  is CNN-LSTM (spatial) vs standalone LSTM (no spatial input), not distributed
  vs lumped in traditional sense. Caveat added in text.
- [x] Beck et al. (2024) — xLSTM **confirmed in NeurIPS 2024 proceedings**
  (poster #96260). Citation updated from arXiv to proceedings.
- [x] Shams Eddin et al. (2025) — RiverMamba **confirmed accepted at NeurIPS
  2025** (camera-ready v3, Oct 2025). Not just arXiv preprint.
- [x] Song et al. (WRR, 2026) — **Published** in WRR vol 62, issue 2, Feb 2026.
  doi:10.1029/2025WR040414. Not early view.
- [x] Dehghani et al. (Ecol. Inform., 75, 2023) — single-basin caveat added
  for NSE 0.98–0.99 claim.

### Previously verified
- [x] Onof and Wang (HESS, 2020) — BLRP new developments, verified (was "Kaczmarska et al.")
- [x] Kossieris et al. (J. Hydrol., 2016) — BLRP sub-hourly scheme, DOI verified
- [x] Harris et al. (JAMES, 2022) — GAN/VAE-GAN on IFS forecasts, DOI verified
- [x] Mardani et al. (Commun. Earth Environ., 2025) — CorrDiff, DOI verified
- [x] Terzago et al. (NHESS, 2018) — RainFARM complex orography, verified (was "Rebora et al.")
- [x] Frontiers in Water (2022) — precipitation uncertainty 50% attribution, DOI verified
- [x] Li et al. (Int. J. Climatol., 2018) — MOF three resampling approaches, DOI verified
- [x] Breinl and Di Baldassarre (J. Hydrol. Reg. Stud., 21, 2019) — S-MOF, verified (was "2018")
- [x] Wang & Karimi (HESS, 28, 2107, 2024) — spatially distributed LSTM, DOI verified
- [x] Li et al. (J. Hydrol., 620, 2023) — CNN-LSTM Tibetan Plateau, verified (was "Xiang et al.")
- [x] Anderson & Radic (HESS, 26, 795, 2022) — CNN-LSTM regional hydrology, DOI verified
- [x] Shi et al. (NeurIPS, 2015) — ConvLSTM original paper, arXiv verified
- [x] Dehghani et al. (Ecol. Inform., 75, 2023) — ConvLSTM vs CNN-LSTM, verified (was "Hosseiny et al.")
- [x] Borgel et al. (GMD, 18, 2005, 2025) — ConvLSTM 97 rivers Baltic, DOI verified
- [x] Pokharel & Roy (J. Hydroinf., 26, 2024) — parsimonious CNN-LSTM, DOI verified
- [x] Sun et al. (HESS, 26, 5163–5184, 2022) — GNN river network learning, DOI verified (page range corrected)

### Verified during CRAAB review (2026-03-30, round 2)
- [x] GRU hourly study — **Identified**: Farfan-Duran & Cea, Earth Sci.
  Inform., 17, 5289–5315, 2024, doi:10.1007/s12145-024-01454-9. GRU NSE
  0.96–0.98 at 1-h lead in NW Spain. Citation updated in text.
- [x] Lechner and Hasani (2020) — **Confirmed** NeurIPS 2020 spotlight,
  arXiv:2006.04418. Citation updated in text.
- [x] MR-ACF-TE-LSTM — **Identified**: Apak, Kilinc, Yurtsever, Haznedar, and
  Ozkan, Scientific Reports, 16(1), 10149, 2026,
  doi:10.1038/s41598-026-40713-1. Published Feb 2026.
- [x] BWDformer — **Identified**: Xu, Zeng, Wang, Zhang, Wang, and Zang,
  Scientific Reports, 15, 2025, doi:10.1038/s41598-025-21007-4. Informer-based
  with wavelet decomposition. Published 2025, not 2026.
- [x] Xiang and Demir (2020) — **Two papers**: (a) Xiang, Yan, and Demir, WRR,
  56(1), e2019WR025326, 2020 (seq2seq foundation); (b) Xiang and Demir,
  Environ. Model. Softw., 131, 104761, 2020 (Neural Runoff Model, 125 Iowa
  stations). Paper (b) is the "NRM" reference.
- [x] Chandra & Saharia — **CORRECTED**: Authors are Chowdhury & Saha (not
  Chandra & Saharia). DOI 10.1007/s12145-024-01309-3 confirmed. Citation
  corrected in text.
- [x] Beucler et al. (JMLR, 2023) — **CORRECTED**: Paper is by Harder,
  Hernandez-Garcia, Ramesh et al., JMLR 24(365), 2023 (not Beucler). Beucler's
  constraint paper is Phys. Rev. Lett., 126, 098302, 2021. Citation corrected.
  Geiss & Hardin (AIES, 2(1), 2023) confirmed, doi:10.1175/AIES-D-21-0012.1.
- [x] Kerrigan et al. (NeurIPS 2024) — **CORRECTED**: First author is
  Srivastava (Kerrigan is 3rd author). Full: Srivastava, Yang, Kerrigan,
  Dresdner, McGibbon, Bretherton, and Mandt, NeurIPS 2024, arXiv:2312.06071.
  Title: "Precipitation Downscaling with Spatiotemporal Video Diffusion."
  Citation corrected in text.
- [x] Ficchi et al. — **CORRECTED**: Journal is J. Hydrol. (not Environ. Model.
  Softw.). Two papers: (a) Ficchì et al., J. Hydrol., 538, 454–470, 2016,
  doi:10.1016/j.jhydrol.2016.04.016 (temporal resolution impact); (b) Ficchì
  et al., J. Hydrol., 575, 1308–1327, 2019, doi:10.1016/j.jhydrol.2019.05.084
  (flux-matching disaggregation). Both now cited in text.
- [x] Wüest et al. — **Identified**: Wüest, Frei, Altenhoff, Hagen, Litschi,
  and Schär, Int. J. Climatol., 30, 1764–1775, 2010, doi:10.1002/joc.2025.
  Citation updated in text.
- [x] Tremblay (2005) — **DOI confirmed**: 10.1175/JAS3411.1. J. Atmos. Sci.,
  62(5), 1513–1528. Citation updated in text.
- [x] Putkonen (2004) — **DOI confirmed**:
  10.1657/1523-0430(2004)036[0244:CSARDA]2.0.CO;2. Arct. Antarct. Alp. Res.,
  36(2), 244–248. Citation updated in text.
- [x] Loritz et al. (HESS, 25, 147, 2021) — **DOI confirmed**:
  10.5194/hess-25-147-2021. Title: "The role and value of distributed
  precipitation data in hydrological models."
- [x] Oddo et al. (Front. Water, 6, 2024) — **DOI confirmed**:
  10.3389/frwa.2024.1346104. Authors: Oddo, Bolten, Kumar, and Cleary (NASA
  GSFC). Deep ConvLSTM for flash flood prediction.
- [x] Sadler et al. (WRR, 58, 2022) — **DOI confirmed**:
  10.1029/2021WR030138. Authors: Sadler, Appling, Read, Oliver, Jia, Zwart,
  and Kumar. Multi-task deep learning of streamflow + water temperature.

### Still unverified
- [ ] Dimri et al. (Atmos. Res., 2024) — elevation-dependent precip Upper Ganga.
  A.P. Dimri (JNU) is a known Himalayan meteorology researcher; paper is
  plausible but DOI could not be confirmed via web search. **Manual
  verification required before publication.**
- [ ] Li et al. (J. Hydrol. Reg. Stud., 2023) — CNN-LSTM Yellow River. **Likely
  does not exist as described.** Extensive search found no matching paper. The
  "66% of basins" finding may conflate Pokharel & Roy (2024, 21/32 Nebraska
  basins) with the Yellow River CNN-LSTM topic from Hu et al. (2024). **Remove
  or replace before publication.**
