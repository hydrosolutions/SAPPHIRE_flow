# Literature Review: Sub-Daily Ensemble Flood Forecasting with ML

**Purpose**: Working literature review that serves two roles:
1. **Immediate**: Sharpens the experimental design for Paper 2 and validates
   that our research questions target genuine gaps
2. **Later**: Becomes the introduction/background for Paper 2, or a standalone
   WRR Commentary (~2,000 words), or both

**Central question**: What is missing for operational sub-daily ensemble flood
forecasting with machine learning?

**Thesis**: ML can scale ensemble streamflow forecasting to sub-hourly
resolution where process-based models cannot, but the community has not answered
the critical question of *where forecast uncertainty should come from* when
coupling ML with ensemble NWP at sub-daily timescales.

---

## Review Structure

Each section below is a research thread. For each: summarise the state of
knowledge, identify what's missing, and note how it connects to our experimental
design. Cite everything — this is the reference base for all papers.

### 1. Operational Ensemble Flood Forecasting Systems

> **Research**: [01-operational-systems.md](01-operational-systems.md) ✅

**Question**: What operational systems exist, and where does ML fit?

**Key findings**:
- All major operational ensemble systems use process-based models (LISFLOOD,
  WRF-Hydro, GR4H). Ensemble propagation is purely Paradigm A (NWP members →
  deterministic hydro model → ensemble streamflow).
- Google Flood Hub is the only global operational ML streamflow system, but uses
  deterministic NWP + learned CMAL uncertainty (Paradigm B), not ensemble NWP.
- ECMWF developing AIFL (LSTM-based global streamflow, pre-operational).
- AIFS ENS (51 members) operational for weather since Jul 2025 but **not yet
  coupled to hydrology** — the door is open.
- Post-2021 European floods: all NW European countries invested in
  probabilistic forecasting (Busker et al., 2025 preprint).
- **Gap confirmed**: no system combines ML hydrology + ensemble NWP propagation.
- **Correction**: Nature 2024 paper is Nearing et al., not Nevo et al.

**Deliverables in research file**: Two summary tables (process-based systems,
ML-based systems), full citations, verification TODOs.

### 2. ML Architectures for Sub-Daily Streamflow

> **Research**: [02-ml-architectures.md](02-ml-architectures.md) ✅

**Question**: Which architectures can handle sub-daily resolution with coarse
NWP forcing?

**Key findings**:
- LSTM wins benchmarks for regression/short-term forecasting (Liu et al., HESS
  2025: 13 architectures). Transformers win only for long-horizon autoregression
  and extremes.
- **MTS-LSTM** is the only architecture with native multi-resolution support.
  MF-LSTM (2025) simplifies to single cell with per-frequency embeddings, 5x
  faster, supports 3 frequencies.
- TFT and FutureTST offer architectural advantages for future forcing
  (variable selection, cross-attention), but neither tested at sub-hourly or
  for NWP temporal mismatch.
- **RiverMamba** (NeurIPS 2025): first SSM for hydrology, outperforms GloFAS
  globally, but daily/global grid only.
- **Differentiable hydrology (delta-HBV)**: state of the art for physics-ML.
  Outperforms LSTM on extremes (Song et al., WRR 2026). Hard constraints
  (MC-LSTM) hurt extremes (Frame et al., HESS 2022).
- **Temporal mismatch** (coarse NWP → fine streamflow): under-discussed. No
  paper uses attention to handle it. MTS-LSTM handles daily+hourly natively
  but not 3-hourly NWP + 15-min streamflow.
- ML-based temporal disaggregation emerging: LSTM for daily→half-hourly precip
  (Oates et al. 2025), SpateGAN for ERA5→2km/10min (Glawion et al. 2025).
- **No architecture tested at sub-hourly with ensemble NWP** — confirmed gap.

### 3. Uncertainty Paradigms (intellectual core)

> **Research**: [03-uncertainty-paradigms.md](03-uncertainty-paradigms.md) ✅

**Question**: Where should forecast uncertainty come from when coupling ML with
ensemble NWP?

**Key findings**:
- Three paradigms (A: NWP pass-through, B: learned distribution, C: deep
  ensembles) have never been compared head-to-head for ML streamflow.
- Paradigm A has never been applied to a pure ML streamflow model. Dong et al.
  (HESS 2025) used a hybrid; Modi et al. (JAMES 2025) used historical
  resampling, not NWP.
- CMAL is the best-performing learned distribution (Klotz et al., HESS 2022)
  but tested only with observed forcing. QRF is comparable, 50% faster
  (Zhang et al., HESS 2023 — note: outline originally said "Huo et al.",
  corrected).
- Sabzipour et al. (J. Hydrol., 2023): deep ensemble LSTM showed poor
  spread-skill vs process-based model — seed diversity ≠ forcing uncertainty.
- AIFS-CRPS (Lang et al., 2024): CRPS as direct training loss for ensemble
  weather. Transferable to streamflow but not yet applied.
- Diffusion models (DRUM, HydroDiffusion) represent emerging Paradigm D.
- Permutation-invariant NN for ensemble NWP input (Hohlein et al., AIES 2024)
  demonstrated for weather but not streamflow.

**Key gap**: No head-to-head comparison of A vs B vs C for streamflow. This is
the central open question.

### 4. Sub-Hourly Resolution: Value and Limits

> **Research**: [04-sub-hourly-resolution.md](04-sub-hourly-resolution.md) ✅

**Question**: When does sub-hourly resolution add value over hourly/daily?

**Key findings**:
- Sub-hourly ML streamflow forecasting is virtually unexplored. No large-sample
  study tests ML at 15-min or finer resolution. The hourly-to-sub-hourly
  transition is a genuine research gap.
- Catchment size determines where sub-hourly adds value: below ~25 km² (T_c
  < 1 h) sub-hourly is essential; 25-100 km² catchment-specific; > 100 km²
  hourly generally sufficient (Ficchi et al., J. Hydrol., 2016).
- No rigorous evidence supports "15-min doubles effective lead time" — plausible
  from first principles but unquantified in the literature.
- Rating curve uncertainty at high flows (15-40%, up to 43% in mountains) may
  mask the benefit of fine-resolution discharge forecasting — argues for
  predicting stage directly.
- No large-sample ML comparison of stage vs discharge prediction exists.
  CAMELSH (2025) enables one (5,188+ basins with both variables at hourly).
- No sub-hourly benchmark dataset exists anywhere. CAMELS-CH is daily only.
- ML temporal disaggregation emerging (SpateGAN, LSTM) but untested on NWP
  forecast fields or coupled to hydrology.
- The NWP temporal mismatch is under-discussed: MTS-LSTM handles daily+hourly
  but only tested in simulation mode. Autoregressive discharge interpolation of
  sub-NWP-timestep dynamics is empirically untested.
- **Gap confirmed**: sub-hourly ML streamflow forecasting is unexplored, and the
  interaction between temporal resolution and uncertainty paradigm is unknown.

**Deliverables in research file**: Catchment size threshold table, NWP mismatch
analysis, rating curve uncertainty synthesis, dataset inventory, CRAAB analysis
per sub-topic, verification TODOs.

### 5. Transfer Learning at Sub-Daily Resolution

> **Research**: [05-transfer-learning.md](05-transfer-learning.md) ✅

**Question**: Can sub-daily ML models transfer across regions?

**Key findings**:
- Daily transfer is well-established: multi-basin training always outperforms
  single-basin (Kratzert et al., HESS 2024). Global LSTM on 5,680+ gauges
  matches operational systems for ungauged flood prediction (Nearing et al.,
  Nature 2024). Cross-continental transfer works with fine-tuning (Ma et al.,
  WRR 2021).
- Sub-daily transfer is virtually untested. Only one published result: Lee
  et al. (2025) achieved 10-min PUB (NSE 0.59) on 35 South Korean basins —
  notably lower than daily PUB baselines (0.69-0.78).
- Entity awareness questioned: static attributes may serve as basin identifiers
  rather than encoding generalisable physics (Heudorfer et al., GRL 2025).
- Reanalysis-to-forecast domain shift is substantial: AIFL showed NSE drops
  from 0.58 to 0.33 without NWP fine-tuning (Taccari et al., 2026). Two-stage
  training essential for operational systems.
- Climate dissimilarity degrades transfer, but process mismatch (absent runoff
  generation mechanisms) matters more than geographic distance. No quantitative
  distance framework exists.
- No foundation model operates at sub-daily resolution. All three frontier
  global models (Google, AIFL, RiverMamba) are daily-only.
- Probabilistic transfer is unexplored — unknown whether CMAL/quantile
  calibration survives transfer.
- Minimum fine-tuning data at sub-daily resolution is unknown.
- **Gap confirmed**: sub-daily transfer learning is unexplored. The interaction
  between temporal resolution, climate dissimilarity, and uncertainty
  calibration under transfer is entirely open.

**Deliverables in research file**: Daily baseline synthesis, global transfer
analysis, climate dissimilarity evidence, entity awareness critique,
foundation model inventory, sub-daily gap analysis, CRAAB per sub-topic,
verification TODOs.

### 6. Datasets and NWP Ensemble Products

> **Research**: [06-datasets-and-nwp.md](06-datasets-and-nwp.md) ✅
> **See also**: [precipitation_products.md](precipitation_products.md)

**Question**: What data exists to run these experiments?

**Key findings**:
- Four curated hourly datasets exist: CAMELSH (5,188+ US), LamaH-CE (859
  Central Europe), CAMELS-GB v2 (671 UK), CAMELS-SPAT (1,426 US/Canada). All
  stop at hourly — no sub-hourly benchmark exists.
- Only CAMELS-GB v2 provides hourly water level alongside discharge.
- CAMELS-CH is daily only (331 Swiss catchments). No hourly extension published.
- Two NWP ensemble reforecast archives: GEFSv12 (5/11 members, 3h, 2000-2019,
  free) and ECMWF ENS (11 members, 6h, rolling 20 yr, restricted). No AI
  weather model reforecast archive publicly available.
- ICON-CH2-EPS (SAPPHIRE v0 primary NWP) has no public reforecast — must use
  reanalysis→NWP domain adaptation strategy.
- ERA5-Land is the only viable global hourly reanalysis but has systematic
  precipitation biases in mountains. Bias correction mandatory.
- AIFL showed reanalysis→NWP domain shift drops NSE from 0.58 to 0.33 without
  fine-tuning. Two-stage training essential.
- No dataset exists for Nepal/Himalayan catchments at any resolution.
- Ensemble member count mismatch strategies emerging (fair CRPS, per-member
  processing, 4 members suffice for training) but unvalidated for hydrology.
- **Gap confirmed**: no sub-hourly benchmark, no NWP ensemble forcing in any
  dataset, no Nepal data.

**Deliverables in research file**: Streamflow dataset inventory table, NWP
ensemble product comparison, reanalysis quality synthesis, train/eval split
strategies, member count mismatch strategies, CRAAB per sub-topic, verification
TODOs.

---

## Publication Options (decide later)

| Option | Format | Cost | When to decide |
|--------|--------|------|----------------|
| Paper 2 introduction | ~3,000 words, integrated into methods paper | $0 (part of Paper 2) | When writing Paper 2 |
| WRR Commentary | ~2,000 words, 1–2 figures, standalone | $0 (free for commentaries) | After review is complete |
| Both | Commentary establishes framing; Paper 2 intro provides depth | $0 | After review is complete |

---

## Research Process

For each section above:
1. **Systematic search**: Google Scholar, Scopus, Web of Science. Keywords
   per section. Focus on 2018–2026 (post-Shen et al.).
2. **Read and annotate**: Key findings, limitations, what's missing.
3. **Synthesise**: Write the section narrative with full citations.
4. **Gap validation**: Confirm each claimed gap is real (no paper fills it).
5. **Update experimental design**: Feed findings back into Paper 2's
   publication plan.

### Search Strategy

| Section | Primary keywords | Secondary filters |
|---------|-----------------|-------------------|
| 1. Operational systems | ensemble flood forecasting, operational, FEWS, GloFAS, EFAS | 2018+ |
| 2. Architectures | LSTM streamflow, transformer hydrology, MTS-LSTM, sub-daily, sub-hourly | ML/DL only |
| 3. Uncertainty | probabilistic streamflow, ensemble, CMAL, deep ensemble, MDN, CRPS | ML only |
| 4. Sub-hourly | sub-hourly streamflow, 15-minute, flash flood, temporal resolution | ML + process |
| 5. Transfer | transfer learning hydrology, ungauged, regionalization | sub-daily focus |
| 6. Data | CAMELSH, LamaH, GEFS, TIGGE, USGS NWIS | hourly or finer |
