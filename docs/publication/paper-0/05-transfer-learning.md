# 5. Transfer Learning at Sub-Daily Resolution

Literature review for Section 5 of the Paper 0 outline.
Last updated: 2026-03-31.

## Key Findings

1. **Daily-resolution transfer learning is well-established.** Multi-basin
   regional training always outperforms single-basin training (Kratzert et al.,
   HESS 2024). Global LSTM models trained on 5,000+ basins achieve ungauged
   flood prediction rivalling operational systems (Nearing et al., Nature 2024).
   Cross-continental transfer works with fine-tuning (Ma et al., WRR 2021).
2. **Sub-daily transfer learning is virtually untested.** No published study
   evaluates transfer at hourly or finer resolution. MTS-LSTM (Gauch et al.,
   HESS 2021) does hourly prediction but only on gauged basins. Lee et al.
   (J. Hydro-environ. Res., 2025) achieved 10-min PUB with CNN-LSTM (NSE 0.59)
   but only in South Korean basins — the only sub-daily PUB result found.
3. **Entity awareness is questioned.** Heudorfer et al. (GRL, 2025) showed
   static attributes may function primarily as basin identifiers rather than
   encoding physically meaningful generalisable features. Out-of-sample entity
   awareness is small. This challenges a core assumption of transfer learning
   for ungauged basins.
4. **The reanalysis-to-forecast domain shift is substantial.** AIFL (Taccari
   et al., arXiv 2026) showed NSE drops from 0.58 to 0.33 without fine-tuning
   on NWP. Two-stage training (pre-train on reanalysis, fine-tune on NWP)
   recovers most skill. Essential for any operational system.
5. **Climate dissimilarity degrades transfer, but process dissimilarity matters
   more than geographic distance.** Donor selection by physiographic similarity
   outperforms indiscriminate pooling (Ougahi & Rowan, WRR 2026). US-to-Tibet
   transfer showed limited success (Yao et al., J. Hydrol., 2023) — groundwater
   and glacier melt processes absent from training data were the main barrier.
6. **No foundation model operates at sub-daily resolution** as of March 2026.
   All three frontier global models (Google Flood Hub, AIFL, RiverMamba) are
   daily-only. CAMELSH (2025) provides hourly data for 5,188+ US basins but
   no model has been benchmarked on it for transfer.
7. **Probabilistic transfer is unexplored.** No study examines whether
   uncertainty estimates (CMAL, quantile regression) remain calibrated after
   transfer to new basins or climates.
8. **Minimum fine-tuning data at sub-daily resolution is unknown.** At daily
   scale, "a few years" suffice when starting from a pre-trained model (Ma
   et al., 2021). No equivalent estimate exists for hourly or sub-hourly.

---

## 5.1 Daily Transfer: The Established Baseline

### Multi-basin regional training

Kratzert et al. (HESS, 2024) — "Never Train on a Single Basin" — showed that
multi-basin LSTM training always equals or outperforms single-basin models, even
for the training basin itself. Of 150 surveyed papers (2021-2023), 122 still
trained on individual catchments. The Entity-Aware LSTM (EA-LSTM; Kratzert
et al., HESS 2019) conditions the input gate on 27 static catchment attributes,
enabling a single model to distinguish basin behaviours.

**CRAAB**:
- *Claim*: Regional training is always better. Well-supported for daily CAMELS.
- *Research gap*: Not tested at sub-daily resolution. The paper does not address
  whether the same holds for hourly, where basin response dynamics differ.
- *Assumption*: Sufficient public data exist for any practitioner to train
  regionally. May not hold for sub-daily in data-sparse regions.
- *Bias*: Authored by the NeuralHydrology group who developed the LSTM
  benchmark — natural advocacy for their framework. CAMELS-US dominance.

### Ungauged prediction at continental scale

Arsenault et al. (HESS, 2023) demonstrated LSTM outperforming traditional
regionalisation in leave-one-out cross-validation on 148 northeast North
American basins (median NSE 0.78 for PUB vs 0.58-0.63 for process-based
regionalisation). The LSTM outperformed even calibrated-in-place models in
75-78% of basins.

**CRAAB**:
- *Claim*: LSTM PUB outperforms traditional regionalisation. Well-supported.
- *Research gap*: Daily only. Single climatic region (humid continental).
  Restricted to basins > 500 km^2 — excludes the small/flashy catchments
  most relevant to sub-daily forecasting.
- *Bias*: Data-rich region with strong observational infrastructure.

**Key references**:
- Kratzert, F., Gauch, M., Klotz, D., and Nearing, G.: HESS Opinions: Never
  train a Long Short-Term Memory (LSTM) network on a single basin, Hydrol. Earth
  Syst. Sci., 28, 4187-4201, doi:10.5194/hess-28-4187-2024, 2024.
- Kratzert, F., Klotz, D., Shalev, G., Klambauer, G., Hochreiter, S., and
  Nearing, G.: Towards learning universal, regional, and local hydrological
  behaviors via machine learning applied to large-sample datasets, Hydrol. Earth
  Syst. Sci., 23, 5089-5110, doi:10.5194/hess-23-5089-2019, 2019.
- Arsenault, R., Martel, J.-L., Brunet, F., Brissette, F., and Mai, J.:
  Continuous streamflow prediction in ungauged basins: long short-term memory
  neural networks clearly outperform traditional hydrological models, Hydrol.
  Earth Syst. Sci., 27, 139-157, doi:10.5194/hess-27-139-2023, 2023.

---

## 5.2 Global Transfer: The Nearing et al. Landmark

Nearing et al. (Nature, 2024) trained an LSTM on 5,680 gauges globally,
achieving 5-day flood predictions in ungauged basins matching or exceeding
GloFAS nowcast reliability. Operational via Google Flood Hub in 80+ countries.
Later expanded to ~16,000 gauges via CARAVAN.

**CRAAB**:
- *Claim*: Global LSTM matches operational physics-based systems for ungauged
  flood prediction. Strong evidence, though the comparison (5-day AI vs 0-day
  GloFAS) is favourable framing.
- *Research gap*: Predicts threshold exceedance (classification), not continuous
  hydrographs. Daily resolution. No systematic analysis of which basin types
  transfer worst. No fine-tuning experiments reported. No ensemble output.
- *Assumption*: Global products (soil, land cover, topography) provide adequate
  static attributes everywhere. Missing forcing data imputed with means — may
  introduce systematic biases in data-poor regions.
- *Ambiguity*: Relative contribution of data volume vs architecture unclear.
  The role of Google's proprietary weather model vs public NWP is opaque.
- *Bias*: 5,680 gauges overwhelmingly in developed countries. "Ungauged"
  evaluation withholds existing data — different from truly data-sparse regions.

**Key reference**:
- Nearing, G., et al.: Global prediction of extreme floods in ungauged
  watersheds, Nature, 627, 559-563, doi:10.1038/s41586-024-07145-1, 2024.

---

## 5.3 Cross-Continental Transfer

### Data-rich to data-poor

Ma et al. (WRR, 2021) pre-trained on CONUS, fine-tuned on short local records
in Chile, GB, and China. Transfer improved predictions in all target regions,
with benefits increasing with source dataset diversity. However, the target
regions are still relatively data-rich by global standards.

Zhang et al. (The Innovation, 2024) showed cross-regional ED-DLSTM achieving
mean NSE 0.75 across 2,089 catchments (US/Canada/UK/Central Europe). Transfer
to Chile: US-trained 76.9% success, Canada-trained 66.2%, UK-trained only
42.5% — demonstrating that training diversity matters more than proximity.

Ougahi and Rowan (WRR, 2026) showed cluster-based donor selection (matching
physiographic similarity) outperforms indiscriminate pooling for transfer to
Central Asian basins. Adding dissimilar basins can degrade performance.

**CRAAB** (cross-cutting):
- *Claim*: Cross-continental transfer works with fine-tuning. Supported.
- *Research gap*: All studies daily. Target regions (Chile, GB, China, Central
  Asia) still have significant observational infrastructure — not representative
  of truly data-poor contexts (Nepal, sub-Saharan Africa).
- *Assumption*: CONUS training diversity is sufficient for global transfer. May
  not hold for monsoon, glaciated, or tropical catchments.
- *Ambiguity*: Whether improvement is from genuine hydrological knowledge
  transfer or from regularisation (better weight initialisation preventing
  overfitting on small datasets).

**Key references**:
- Ma, K., et al.: Transferring Hydrologic Data Across Continents — Leveraging
  Data-Rich Regions to Improve Hydrologic Prediction in Data-Sparse Regions,
  Water Resour. Res., 57, e2020WR028600, doi:10.1029/2020WR028600, 2021.
- Zhang, B., et al.: Deep learning for cross-region streamflow and flood
  forecasting at a global scale, The Innovation, 5(3), 100617,
  doi:10.1016/j.xinn.2024.100617, 2024.
- Ougahi, J. H. and Rowan, J. S.: Investigating Deep Learning Knowledge
  Transfer in Streamflow Prediction From Global to Local Catchment, Water
  Resour. Res., 62, e2025WR041194, doi:10.1029/2025WR041194, 2026.

---

## 5.4 Climate Dissimilarity and Process Mismatch

Yao et al. (J. Hydrol., 2023) showed limited effectiveness transferring from
671 US catchments to 4 Tibetan Plateau basins. Climate forcing quality was the
dominant factor; soil/geology attributes had less impact. The main barrier was
process dissimilarity: groundwater and glacier melt contributions are absent
from the US training set.

**Synthesis**: The evidence converges on three findings:
1. Transfer works best when source and target share dominant runoff generation
   mechanisms (rainfall-runoff vs snowmelt vs glacier melt).
2. Even with climate mismatch, pre-training provides better initialisation than
   random — the benefit is often regularisation, not direct knowledge transfer.
3. No study has quantified a "climate distance" threshold beyond which transfer
   becomes harmful. No formal framework exists for this.

**Implication for SAPPHIRE Flow**: The Switzerland-to-Nepal pathway involves
transferring from Alpine temperate to monsoon/glaciated regimes. The closest
published analog (US-to-Tibet) showed limited success, though with only 4 target
basins. Key concerns: (a) intense monsoon convection, (b) glacier-fed baseflows,
(c) extreme discharge seasonality, (d) sparse/uncertain forcing data.

**CRAAB**:
- *Research gap*: Alpine-to-monsoon transfer is essentially untested. No
  quantitative climate distance framework.
- *Bias*: Yao et al. used only 4 target basins — no statistical power. The
  Tibetan Plateau is a worst-case scenario that may not represent more moderate
  climate mismatches.

**Key reference**:
- Yao, Y., et al.: Can transfer learning improve hydrological predictions in
  the alpine regions?, J. Hydrol., 625, 130038,
  doi:10.1016/j.jhydrol.2023.130038, 2023.

---

## 5.5 Entity Awareness Under Scrutiny

Heudorfer et al. (GRL, 2025) challenged a core assumption of transfer learning.
Through ablation experiments, they showed that static attributes in EA-LSTM may
primarily serve as **in-sample unique identifiers** enabling the model to
differentiate basins, rather than encoding physically meaningful features that
generalise out of sample. Out-of-sample entity awareness exists but is small.

Yu et al. (WRR, 2024) found high consistency between two strategies —
incorporating static attributes vs classification-based training — suggesting
both capture overlapping information about streamflow generation mechanisms.

**Implication**: If static attributes are primarily identifiers rather than
generalisable features, entity-aware models may struggle for basins outside the
training distribution. This directly challenges the assumption underlying
SAPPHIRE Flow's transfer strategy from Switzerland to Nepal.

**CRAAB**:
- *Claim*: Entity awareness is weaker than assumed. Provocative and
  well-evidenced but tested only on CAMELS-US daily data.
- *Research gap*: Not tested at sub-daily resolution. At sub-daily timescales,
  different attributes matter (channel geometry, hillslope response, urban
  fraction) — entity awareness might behave differently.
- *Ambiguity*: Whether the finding generalises beyond CAMELS-US to more diverse
  global datasets.

**Key references**:
- Heudorfer, B., Kratzert, F., Klotz, D., and Nearing, G.: Are deep learning
  models in hydrology entity aware?, Geophys. Res. Lett., 52, e2024GL113036,
  doi:10.1029/2024GL113036, 2025.
- Yu, Z., et al.: Deciphering the mechanism of better predictions of regional
  LSTM models in ungauged basins, Water Resour. Res., 60, e2023WR035876,
  doi:10.1029/2023WR035876, 2024.

---

## 5.6 Foundation Models and Pre-Training

### The frontier: three global models (all daily)

| Model | Basins | Resolution | Architecture | Ensemble? | Operational? |
|---|---|---|---|---|---|
| Google Flood Hub (Nearing et al., 2024) | ~16,000 | Daily | LSTM | No | Yes (80+ countries) |
| AIFL (Taccari et al., arXiv 2026) | 18,588 | Daily | LSTM | No | Pre-operational (ECMWF) |
| RiverMamba (Shams et al., NeurIPS 2025) | Global grid | Daily | Mamba SSM | No | Research |

All are daily. None produce ensemble outputs natively. No foundation model
operates at sub-daily resolution.

### The reanalysis-to-forecast domain shift

AIFL quantified this directly: NSE drops from 0.58 to 0.33 when an
ERA5-Land-trained model is applied to IFS forecasts without fine-tuning. The
two-stage training (pre-train on ERA5-Land, fine-tune on IFS control, 4 years)
recovers most skill to KGE' 0.66.

This is the most important practical finding for operational systems: training
on reanalysis and deploying on NWP requires explicit domain adaptation.

**CRAAB**:
- *Claim*: Two-stage training bridges the domain shift. Well-supported for
  daily deterministic AIFL.
- *Research gap*: Daily only. Uses IFS control (single member), not ensemble.
  4-year fine-tuning window is short. No sub-daily evaluation.
- *Assumption*: ERA5-Land provides sufficient meteorological "truth" for
  pre-training. Known precipitation biases in tropical/mountainous regions.
- *Ambiguity*: Whether the two-stage approach helps more in some hydroclimates
  than others. Whether extending to ensemble NWP would require architectural
  changes.

### Architecture comparison

Liu et al. (HESS, 2025) benchmarked 13 architectures: LSTMs win standard
regression tasks (median KGE 0.75, 0.11 higher than best Transformer). But as
tasks become more complex (autoregression, zero-shot prediction),
attention-based models gradually surpass LSTMs.

**CRAAB**:
- *Claim*: LSTMs dominate for standard streamflow prediction. Well-supported.
- *Research gap*: Daily only. Does not include Mamba/SSM. Does not test
  MTS-LSTM. No pre-training/fine-tuning comparison.
- *Bias*: Hyperparameter conventions for hydrological LSTMs are well-established
  (Kratzert lineage); Transformers may be disadvantaged by less mature tuning.

**Key references**:
- Taccari, M. L., et al.: AIFL: A Global Daily Streamflow Forecasting Model
  Using Deterministic LSTM Pre-trained on ERA5-Land and Fine-tuned on IFS,
  arXiv:2602.16579, 2026.
- Shams, H., et al.: RiverMamba: A State Space Model for Global River Discharge
  and Flood Forecasting, NeurIPS 2025, arXiv:2505.22535, 2025.
- Liu, J., et al.: From RNNs to Transformers: benchmarking deep learning
  architectures for hydrologic prediction, Hydrol. Earth Syst. Sci., 29,
  6811-6842, doi:10.5194/hess-29-6811-2025, 2025.

### Fine-tuning strategies

Two recent studies validate fine-tuning of global models:
- **Fine-tuning LSTM for seamless transition** (Environ. Model. Softw., 2025):
  Local fine-tuning enhanced 73.5% of basins (median NSE 0.63). Regional
  fine-tuning helped only 55.1%. Adding recent discharge raised NSE to 0.71.
- **Fine Flood Forecasts** (arXiv/ICLR 2025): Fine-tuning a 6,375-basin global
  model to individual US basins yielded 8% NSE improvement, with largest gains
  in underperforming basins.

**CRAAB**:
- *Research gap*: Both daily, US-only. Minimum fine-tuning data length not
  systematically quantified. No probabilistic outputs.
- *Key finding*: Local fine-tuning beats regional fine-tuning, suggesting
  basin-specific adaptation is valuable even from a strong global initialisation.

### Related: HydroGEM (foundation model for QC)

HydroGEM (arXiv, 2025) is a 14.2M parameter TCN-Transformer self-supervised on
6.03M clean sequences from 3,724 USGS stations. Achieves F1 0.792 for anomaly
detection with zero-shot cross-national transfer (Tolerant F1 0.70). Not a
forecasting model, but demonstrates that foundation-model pre-training transfers
cross-nationally for hydrological tasks.

---

## 5.7 The Sub-Daily Transfer Gap

### The only sub-daily PUB result

Lee et al. (J. Hydro-environ. Res., 2025) achieved CNN-LSTM predictions at
**10-minute resolution** in an ungauged setting across 35 South Korean
watersheds: mean NSE 0.59 (+/-0.12), with 37.8% improvement for high flows.
This is the only published sub-daily PUB result found.

**CRAAB**:
- *Claim*: Sub-daily PUB is possible with CNN-LSTM at 10-min resolution.
  Supported but with important caveats.
- *Research gap*: NSE 0.59 is notably lower than daily PUB (~0.69-0.78),
  suggesting sub-daily transfer does degrade. No cross-climate testing. No
  probabilistic output. No transfer from pre-trained global model (trained from
  scratch on 35 South Korean basins).
- *Assumption*: Radar precipitation at matched resolution is available — not
  generalisable to NWP-forced operational contexts.
- *Ambiguity*: Whether the 0.59 NSE reflects fundamental sub-daily PUB
  difficulty or a data/model limitation of 35 monsoonal basins.
- *Bias*: South Korean monsoonal basins only. Small sample (35).

**Key reference**:
- Lee, J., Chung, E.-S., Kim, S., and Kim, D.: Streamflow forecasting in
  ungauged basins with CNN-LSTM and radar-based precipitation, J. Hydro-environ.
  Res., 60-61, 100666, doi:10.1016/j.jher.2025.100666, 2025.

### Why the gap exists

1. **Data scarcity**: Few countries have large-sample hourly datasets with
   matched forcings. CAMELSH (US, 2025) is the first; no global hourly
   equivalent exists.
2. **Computational cost**: Hourly sequences are 24x longer, making training
   expensive and convergence harder.
3. **Forcing quality**: Sub-daily NWP/reanalysis has higher uncertainty; transfer
   adds another layer of mismatch.
4. **Attribute relevance shifts**: At sub-daily timescales, fast-response
   attributes (channel geometry, hillslope connectivity, urban fraction) matter
   more than slow-response attributes (soil depth, geology, baseflow index) that
   dominate daily transfer. Standard CAMELS-type attributes may be less
   informative.

### What sub-daily transfer would require

1. Pre-training on a large-sample hourly dataset (CAMELSH is the current best
   candidate, US-only).
2. MTS-LSTM-style architecture adapted for PUB (add entity awareness to the
   multi-timescale framework).
3. Explicit domain adaptation from reanalysis to NWP at sub-daily resolution.
4. New catchment attributes relevant to fast-response processes.
5. Evaluation on cross-climate transfer (not just within CONUS).

---

## 5.8 Minimum Calibration Data

| Study | Resolution | Regime | Finding |
|---|---|---|---|
| Ma et al. (2021) | Daily | Cross-continental | "A few years" of fine-tuning suffices |
| Yang et al. (2023) | Monthly | Chinese basins | 20% of data (few-shot) |
| Kratzert (2018) | Daily | CAMELS-US | ~15 years minimum from scratch |
| Environ. Model. Softw. (2025) | Daily | Global→local | Local fine-tuning helps 73.5% of basins |
| Fine Flood Forecasts (2025) | Daily | Global→US | 8% NSE improvement from fine-tuning |

**Key observation**: Pre-training dramatically reduces fine-tuning data needs
compared to training from scratch. At daily resolution, a few years suffice when
starting from a strong global initialisation.

At sub-daily resolution: **no estimate exists**. The higher information content
per year at hourly resolution (8,760 vs 365 observations/year) might reduce the
years needed, but the higher complexity of sub-daily processes might offset this.

**CRAAB**:
- *Research gap*: No systematic study of minimum fine-tuning data at sub-daily
  resolution. This is directly relevant to SAPPHIRE Flow's Nepal deployment
  where sub-daily records may be short.
- *Assumption*: That the daily-scale finding ("a few years suffice") transfers
  to sub-daily. Untested and uncertain.

---

## 5.9 Probabilistic Transfer

No study combines uncertainty quantification with transfer learning or PUB.
Klotz et al. (HESS, 2022) established CMAL-based probabilistic LSTM baselines
but only for gauged basins at daily resolution. Chandra et al. (arXiv, 2024)
tried quantile-ensemble LSTM but explicitly did not test ungauged basins and
found regionalisation unsuccessful.

**CRAAB**:
- *Research gap*: Critical for SAPPHIRE Flow's ensemble-first design. If
  uncertainty estimates become miscalibrated after transfer, alert thresholds
  based on exceedance probabilities will be unreliable.
- *Assumption*: That training-period uncertainty characteristics transfer to new
  basins/climates. Highly questionable — different catchments have different
  intrinsic variability.

---

## 5.10 Cross-Cutting CRAAB Summary

### Claims to verify
- [ ] Regional training always outperforms single-basin at sub-daily — untested
- [ ] Entity awareness enables genuine physical generalisation — challenged by
  Heudorfer et al. (2025)
- [ ] Climate dissimilarity is the main barrier to transfer — process mismatch
  may matter more
- [ ] Pre-trained + fine-tuned always beats local training — established for
  daily, untested for sub-daily

### Confirmed research gaps
1. No transfer learning study at hourly or finer resolution (except Lee et al.
   2025, 35 South Korean basins at 10-min — the only data point)
2. No foundation model at sub-daily resolution
3. No probabilistic/ensemble transfer learning
4. No quantitative climate distance framework for transfer
5. No minimum fine-tuning data estimate at sub-daily
6. No Alpine-to-monsoon transfer study
7. No evaluation of which catchment attributes matter for sub-daily transfer

### Key assumptions to challenge in Paper 2
- Daily transfer results generalise to sub-daily
- Static catchment attributes enable genuine physical generalisation
- CONUS/European pre-training is sufficient for monsoon/glaciated targets
- Uncertainty estimates remain calibrated after transfer
- More training basins is always better (Ougahi 2026 shows this can hurt)

### Biases in the literature
- CAMELS-US dominance: most transfer studies use CAMELS basins
- Developed-country bias: target regions are still relatively data-rich
- Daily resolution bias: all foundation models and most transfer studies daily
- Architecture bias: LSTM dominance may reflect community expertise rather than
  genuine superiority
- Positive result bias: failed transfer experiments likely unpublished

---

## Verification TODOs

- [ ] Verify Lee et al. (2025) is the only sub-daily PUB study — search for
  recent preprints (ESSOAr, arXiv) post May 2025
- [ ] Confirm Heudorfer et al. (2025) findings hold — check for rebuttals or
  follow-up work
- [ ] Verify AIFL architecture details (hidden size, layers, parameter count)
- [ ] Check whether any CAMELSH-based transfer study has been published since
  dataset release (2025)
- [ ] Verify Nearing et al. (2024) gauge count (~5,680 in paper, ~16,000 via
  CARAVAN expansion)
- [ ] Confirm RiverMamba is NeurIPS 2025 — check proceedings
- [ ] Check whether MF-LSTM (2025) has been tested for transfer
- [ ] Search for any Nepal-specific ML streamflow papers
