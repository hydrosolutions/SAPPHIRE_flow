# v1 Nepal Modelling — Design Notes

> Forward-looking design notes for v1 model architecture and NWP handling
> decisions. Sourced from Paper 0 literature review findings
> (`docs/publication/paper-0/02-ml-architectures.md`, `03-uncertainty-paradigms.md`).
> Updated as decisions are made.
>
> **Audience**: anyone planning v1 model work, ML architecture choices, or
> Nepal deployment. Not a literature review — links back to paper-0 for evidence.

Last updated: 2026-03-31.

---

## 1. Spatial input strategy

### The problem

Nepal basins span 1,000–6,000 m elevation. Basin-average NWP is clearly
inadequate — a single value destroys the elevation signal that controls snow/rain
partitioning, orographic enhancement, and runoff timing. ECMWF IFS at ~9 km
covers a 500 km² tributary with only ~6–7 grid cells. (Paper 0 §2.6.)

### Options

| Strategy | Input shape | Architecture | Evidence |
|----------|-------------|-------------|----------|
| **Basin-average** (baseline) | 1 value per parameter per timestep | Standard LSTM | Operational norm, clearly insufficient for steep catchments |
| **Elevation-band** | B bands × P parameters per timestep | Standard LSTM (longer input vector) | Extends existing `GridExtractor`. No architecture change. HBV-style. |
| **Gridded + CNN front-end** | lat × lon × P channels per timestep | CNN-LSTM or ConvLSTM | NSE 0.83 vs 0.51 lumped (Hu et al., 2024). Best for large/steep basins. |

### Architecture readiness

- **Elevation-band**: Fully supported. `SpatialRepresentation.ELEVATION_BAND`,
  `basins.band_geometries`, `GridExtractor` Protocol, `band_id` columns in
  `weather_forecasts` and `historical_forcing` tables — all exist.
- **Gridded CNN-LSTM**: Partially supported. `SpatialRepresentation.GRIDDED` and
  `GriddedForecast` type exist, but `ModelInputs.future_dynamic` assumes a 2D
  `pl.DataFrame`. CNN-LSTM needs `xr.Dataset` (lat × lon × channels × time).
  The design doc `v0-flow13-model-onboarding.md` §3a already notes: "The
  `xr.Dataset` path in `ModelInputs.forcing` is reserved for v1 gridded
  reanalysis." Requires: `ModelDataRequirements.spatial_type` field to declare
  tabular vs gridded input; input preparation dispatches accordingly.

### Decision needed (v1 planning)

Which strategy to prioritise for Nepal. The hypothesis (paper-0 §2.6) is that
spatial resolution matters more than temporal resolution for steep catchments.
The ETH + Tribhuvan thesis results (uncertainty paradigm comparison) will inform
this, but the spatial dimension is orthogonal to their research question.

**Recommendation**: Start with elevation-band (no architecture change, quick
win). Add CNN-LSTM as a second model class if elevation-band proves insufficient.
The experimental matrix (spatial × temporal × uncertainty paradigm) is the paper-2
contribution.

---

## 2. Temporal mismatch strategy

### The problem

ECMWF IFS ENS delivers 3-hourly fields (to day 6) and 6-hourly fields (to
day 15). Nepal streamflow targets are hourly or sub-hourly. How to bridge the
gap? (Paper 0 §2.5.)

### Options

| Strategy | Conservation | Extreme events | Complexity | Training data |
|----------|-------------|---------------|------------|---------------|
| **MTS-LSTM / MF-LSTM** | End-to-end | Unknown at sub-hourly | High (architecture mod) | Standard hydro |
| **MOF** (Method of Fragments) | Exact | Good if regime-conditioned | Low | 10-min station archive |
| **MMRC** (cascade) | Exact | Overstates | Low | Parameter fitting only |
| **ML disaggregation** | Only with constraint layer | Generally better | Moderate | Large high-res dataset |
| **Hourly LSTM on 3h NWP** (interpolate) | N/A | Unknown | Lowest | Standard |

### Nepal-specific advantage

Nepal's DHM operates ~168 tipping-bucket stations with 5-min transmission.
This dense sub-hourly archive enables MOF (Method of Fragments) — a
non-parametric technique that resamples observed sub-hourly patterns as
disaggregation templates. Conservation is exact by construction. Quality
improves when conditioned on convective vs stratiform regime (detectable from
10-min intensity variance + time of day). (Paper 0 §2.5, Nepal-specific
section.)

### Architecture readiness

All options are compatible with the current architecture:
- MTS-LSTM/MF-LSTM: model-internal concern. `ModelInputs` carries the data;
  the model decides how to split branches.
- MOF/MMRC/ML disaggregation: preprocessing step before `prepare_model_inputs()`.
  Fits as a new step between 1.5 (NWP post-processing) and 1.7, or as part of
  1.5 itself.

### Critical prerequisite

**Verify DHM's sub-hourly archival policy.** Tipping-bucket data is transmitted
every 5 minutes during monsoon, but it's unclear whether the historical archive
is available at sub-hourly resolution. Without the archive, MOF is not an
option.

### Decision needed (v1 planning)

Whether to invest in temporal disaggregation at all, or just run hourly. For
Swiss v0 this is a non-issue (ICON-CH2-EPS is hourly). The lit review finding
that hourly is sufficient for meso-scale catchments (100–1000 km²) applies to
most Nepal basins too — sub-hourly matters only for very small catchments
(<10 km²) and urban flash floods.

**Recommendation**: Start with hourly (simple interpolation of 3-hourly IFS to
hourly). Add MOF disaggregation as a configurable preprocessing option if
evaluation shows timing errors at short lead times. MTS-LSTM/MF-LSTM only if
the hourly + disaggregation approach proves inadequate.

---

## 3. Uncertainty paradigm

### The decision

Which paradigm(s) for Nepal v1 operational forecasts? (Paper 0 §3.)

| Paradigm | Members | One forward pass? | Interpretability | Tested for ML streamflow? |
|----------|---------|-------------------|-----------------|--------------------------|
| **A** — NWP pass-through | 51 (IFS ENS) | No (51 passes) | High (physically consistent scenarios) | No (gap) |
| **B** — CMAL head | Configurable quantiles | Yes | Low (learned uncertainty) | Yes (Klotz et al., 2022 — observed forcing only) |
| **C** — Deep ensembles | M × seeds | No (M passes) | Moderate | Poor spread-skill (Sabzipour et al., 2023) |
| **A+B** — CMAL per member | 51 × quantiles | No | High + residual | Untested |
| **B+noise** — AIFS-CRPS style | K stochastic samples | Yes | Low | Not for streamflow |

### What the theses will answer

The ETH thesis (Swiss, 21 ICON-CH2-EPS members) and Tribhuvan thesis (Nepal,
51 IFS ENS members) will directly compare A, B, and C. Results expected
~mid-2027. Until then, **CMAL (Paradigm B)** is the operational default —
proven at Google scale, one forward pass, no ensemble bookkeeping.

### Architecture readiness

Fully ready. The `ForecastModel` Protocol returns `ForecastEnsemble` (members
or quantiles) regardless of paradigm. `ModelCombinationStrategy` handles multi-model
combination. The architecture is paradigm-agnostic by design.

### Related: MC-ALD is not worth pursuing

Investigation confirmed (2026-03-31): "MC-ALD" (MC Dropout + asymmetric
Laplace distribution) is not an established method. The concept is subsumed by
CMAL (which uses a mixture of ALDs). MC Dropout ranked worst in Klotz et al.
(2022). See `03-uncertainty-paradigms.md` §3.3 for details.

---

## 4. NeuralHydrology framework constraints

NeuralHydrology is the de facto standard for ML hydrology. It natively supports
LSTM, EA-LSTM, MTS-LSTM, MF-LSTM, MC-LSTM, xLSTM, ODE-LSTM, Transformer, and
probabilistic heads (CMAL, UMAL, GMM). (Paper 0 §2.7.)

### What it does NOT support

- **CNN-LSTM / ConvLSTM**: expects 1D time-series input. Gridded spatial input
  requires a custom model class via `TemplateModel` API or a separate PyTorch
  implementation.
- **Permutation-invariant ensemble processing**: would need a custom encoder
  layer.

### Implication

If Nepal v1 needs CNN-LSTM (spatial strategy decision), it either:
1. Uses NeuralHydrology's `TemplateModel` API (possible but constrained), or
2. Is implemented as a standalone PyTorch model class satisfying SAPPHIRE Flow's
   `ForecastModel` Protocol directly.

Option 2 is more flexible but loses NeuralHydrology's training infrastructure
(data loaders, logging, config system). The `ForecastModel` Protocol is
framework-agnostic — it doesn't care what's inside.

---

## 5. CRPS as a direct training loss

### The opportunity

AIFS-CRPS (Lang et al., 2024) trains weather models to minimise CRPS across
ensemble members generated from stochastic latent noise. One model produces all
members — no ensemble of separate models. Already available in NeuralHydrology
for streamflow (Klotz et al., 2022 used it). (Paper 0 §3.4.)

### Architecture readiness

Model-internal concern. A CRPS-trained model with latent noise generates
members internally and returns them as a `ForecastEnsemble` with
`representation = MEMBERS`. No Protocol change. The only v0 guard needed is
§I4 (don't lock `future_dynamic` to 2D-only) — already added.

### When to pursue

Research mode for v0b+ (once ICON-CH2-EPS archiving produces enough
forecast-observation pairs for evaluation). A CRPS-as-loss model is a natural
candidate for Flow 13 model onboarding alongside CMAL.

---

## 6. Permutation-invariant ensemble NWP input

### The idea

Instead of running N forward passes (one per NWP member), feed all N members
simultaneously through a shared encoder with permutation-invariant aggregation
(mean pooling, attention, DeepSets-style). Demonstrated for weather
post-processing (Hohlein et al., AIES 2024), not yet for streamflow.
(Paper 0 §3.7.)

### Architecture readiness

Requires `future_dynamic` to carry a member dimension (members × timesteps ×
features). Currently 2D `pl.DataFrame`. Guard in place: `v0-scope.md §I4`
prevents accidentally closing this door.

### When to pursue

v0b+ at earliest, when ensemble NWP (ICON-CH2-EPS) is flowing. A natural
research extension once the thesis work establishes baseline paradigm
comparisons.

---

## Decision log

| # | Decision | Date | Rationale |
|---|----------|------|-----------|
| — | *No decisions made yet — document is forward-looking* | | |
