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

**Question**: Where should forecast uncertainty come from when coupling ML with
ensemble NWP?

**To cover**:
- **Paradigm A — NWP pass-through**: each ensemble member → deterministic model
  → N streamflow members. Standard in process-based systems (EFAS). Assumes
  uncertainty = forcing uncertainty. Never systematically tested with ML.
- **Paradigm B — Learned distribution (MDN/CMAL)**: single model, mixture
  density output head. Klotz et al. (HESS 2022): CMAL > UMAL > GMM >> MC
  Dropout. But tested only with observed forcing, daily. Huo et al. (HESS 2023):
  QRF comparable to CMAL, 50% faster. Modi et al. (JAMES 2025): DL in ensemble
  streamflow, but daily.
- **Paradigm C — Deep ensembles**: M models (different seeds).
  Lakshminarayanan et al. (NeurIPS 2017) established the method. Standard in
  weather (AIFS-CRPS, Lang et al. 2024). Never tested head-to-head with MDN
  in hydrology.
- **Hybrid paradigms**: A+B (CMAL per NWP member), A+C (deep ensemble per
  NWP member). Untested.
- **CRPS-as-loss frontier**: AIFS-CRPS showed direct CRPS optimisation for
  ensemble weather models. Unexplored for streamflow.
- **Testable predictions**: A → spread correlates with NWP spread; B → spread
  reflects learned patterns; C → spread reflects model disagreement. CRPS
  decomposition and spread-skill ratio can distinguish these.

**Key gap**: No head-to-head comparison of A vs B vs C for streamflow. This is
the central open question.

### 4. Sub-Hourly Resolution: Value and Limits

**Question**: When does sub-hourly resolution add value over hourly/daily?

**To cover**:
- Flash flood context: catchments <100 km², concentration time <3 h.
  15-min resolution could double effective lead time.
- Available data: USGS NWIS 15-min (~5,000+ gauges), CAMELSH hourly (3,166),
  LamaH-CE hourly (859), CAMELS-GB v2 hourly (671), CAMELS-SPAT (HESS 2025)
- NWP bottleneck: can ML learn sub-NWP-timestep dynamics from discharge
  autoregression? MTS-LSTM suggests yes. Empirically untested.
- Diminishing returns: large catchments (>1,000 km², response >12 h) — daily
  may suffice. Value is catchment-size-dependent.
- Water level vs discharge: sub-hourly more natural for stage. Rating curve
  uncertainty at high flows (20–30%+). No ML comparison of stage vs discharge.

**Key gap**: Sub-hourly ML streamflow forecasting is unexplored. The
interaction between temporal resolution and uncertainty paradigm is unknown.

### 5. Transfer Learning at Sub-Daily Resolution

**Question**: Can sub-daily ML models transfer across regions?

**To cover**:
- Google/Nevo et al. (Nature 2024): daily transfer works globally
- Sub-hourly transfer: completely untested
- Climate dissimilarity effects: does sub-hourly transfer degrade faster?
- Minimum calibration data at fine resolution

**Key gap**: Sub-hourly transfer learning is unexplored.

### 6. Datasets and NWP Ensemble Products

**Question**: What data exists to run these experiments?

**To cover**:
- Streamflow: USGS NWIS 15-min, CAMELSH, LamaH-CE, CAMELS-GB v2
- NWP ensembles: GEFS v12 reforecast (5–11 members, 3-hourly, 2000–2019),
  GEFS v12 operational (31 members), TIGGE (51 members, 6-hourly),
  ECMWF IFS ENS open data (51 members, 3-hourly, 2024+)
- Reanalysis: NLDAS-2, ERA5-Land, CHESS-met
- Training vs evaluation split strategies for different member counts

**Deliverable**: Table of data sources with resolution, coverage, access.

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
