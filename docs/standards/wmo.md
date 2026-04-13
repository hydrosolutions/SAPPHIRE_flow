# WMO Standards Reference

> This document catalogues WMO publications relevant to SAPPHIRE Flow's design and implementation. It does not redefine system architecture or flow logic — for those, see: data flows (architecture-context.md § Data flows), forecast verification (architecture-context.md § Skill assessment), alert thresholds (architecture-context.md § Alert checking), observation QC (architecture-context.md § Quality control). **Read before** any work on forecast verification metrics, alert level definitions, ensemble post-processing, observation QC flags, or international data exchange formats.

## 1. Overview

WMO sets international standards for operational hydrology — covering data collection, quality control, forecast verification, ensemble post-processing, warning dissemination, and data exchange. This document maps WMO publications to SAPPHIRE Flow subsystems so implementers know which standards apply to their work area.

## 2. Document inventory

| WMO No. | Title | Year | Relevance | URL |
|---------|-------|------|-----------|-----|
| 1072 | Manual on Flood Forecasting and Warning | 2011 | End-to-end system design | [link](https://library.wmo.int/records/item/35881-manual-on-flood-forecasting-and-warning) |
| 1364 | Guidelines on the Verification of Hydrological Forecasts | 2025 | Forecast verification metrics | [link](https://library.wmo.int/records/item/69478-guidelines-on-the-verification-of-hydrological-forecasts) |
| 1091 | Guidelines on Ensemble Prediction Systems and Forecasting | — | Ensemble interpretation | [link](https://library.wmo.int/viewer/48473/download?file=wmo_1091_en.pdf&type=pdf&navigator=1) |
| 1254 | Guidelines on Ensemble Prediction System Postprocessing | 2021 | Bias correction, calibration | [link](https://library.wmo.int/viewer/57510?medianame=1254_Guidelines_on_EPSPP_en_) |
| 168 Vol I & II | Guide to Hydrological Practices (2 vols) | 6th ed. | Data collection, QC, forecasting | [Vol I](https://unstats.un.org/unsd/envaccounting/waterGuidelines/Material/WMO_Guide_168_Vol_I_en_hydrological_practices.pdf) / [Vol II](https://www.hydrology.nl/images/docs/hwrp/WMO_Guide_168_Vol_II_en.pdf) |
| 1150 | Guidelines on Multi-Hazard Impact-Based Forecast and Warning Services | 2015/2021 | Alert system design | [link](https://library.wmo.int/records/item/54669-wmo-guidelines-on-multi-hazard-impact-based-forecast-and-warning-services) |
| 1109 | Guidelines for Implementation of Common Alerting Protocol | — | Alert format standard (CAP) | [link](https://etrp.wmo.int/pluginfile.php/17980/mod_resource/content/1/wmo_1109_en.pdf) |
| — | Quality Management Framework — Hydrology (QMF-H) | — | Pipeline quality assurance | [link](https://community.wmo.int/en/activity-areas/hydrology-and-water-resources/quality-management-framework-hydrology) |
| 49 Vol. III | Technical Regulations — Hydrology | 2006/2022 | Station classification | [link](https://library.wmo.int/records/item/35631-technical-regulations-volume-iii-hydrology) |
| 1192 | WIGOS Metadata Standard | — | Station metadata interoperability | [link](https://repository.oceanbestpractices.org/bitstream/handle/11329/1379/1192_en.pdf) |
| — | WHOS / WaterML 2.0 | — | Data exchange format | [link](https://wmo.int/activities/wmo-hydrological-observing-system-whos) |
| — | WIS 2.0 | — | MQTT pub-sub, OGC EDR API | [link](https://community.wmo.int/site/knowledge-hub/programmes-and-initiatives/wmo-information-system-wis/wis2-overview) |
| 1044 | Manual on Stream Gauging (2 vols) | 2010 | Rating curves, discharge measurement | [link](https://library.wmo.int/viewer/35841?medianame=wmo_1044-v2_en_) |
| Res. 1 Cg-Ext(2021) | Unified Data Policy | 2021 | Data sharing governance | [link](https://wmo.int/wmo-unified-data-policy-resolution-res1) |
| — | HydroSOS | — | Global hydrological status context | [link](https://wmo.int/activities/hydrosos) |
| — | WWRP/WGNE Forecast Verification Research | — | Verification method reference | [link](https://www.cawcr.gov.au/projects/verification/) |

**Priority column** (for quick orientation):

| Priority | WMO No.(s) |
|----------|------------|
| Critical — implement against these now | 1072, 1364, 1091, 1254 |
| High — design decisions depend on these | 168, 1150, 1109, QMF-H |
| Medium — interoperability and metadata | 49 Vol. III, 1192, WHOS, WIS 2.0 |
| Lower — reference and governance | 1044, Res. 1, HydroSOS, CAWCR |

## 3. Mapping to SAPPHIRE Flow subsystems

### Forecast verification (Flows 8/10 — skill computation)

- **WMO-1364** (primary): Defines five verification dimensions — accuracy, bias, reliability, resolution, sharpness. Recommends CRPS for full-distribution ensemble evaluation, Brier Score for threshold exceedance, reliability diagrams for calibration, rank histograms for ensemble spread. This is the normative reference for `compute_skills` flow output.
- **CAWCR verification site**: Practical implementation reference for metric formulas. Use alongside WMO-1364.

Maps to: `compute_skills` flow, skill interpretation schemes in DB, `SkillScore` types.

### Ensemble forecasting and post-processing (Flow 1 — forecast cycle)

- **WMO-1091**: How to interpret ensemble spread as uncertainty; how to derive probability forecasts from raw members. Relevant to the `WeatherPostProcessor` pass-through in v0 and full calibration in v1.
- **WMO-1254**: Three-tier approach to EPS post-processing:
  - Tier 1 — simple bias correction (mean offset removal). Aligns with v0 pass-through strategy.
  - Tier 2 — MOS (Model Output Statistics). Target for v1 after sufficient archive accumulates.
  - Tier 3 — advanced calibration (EMOS, BMA). Post-v1.

Maps to: NWP post-processing step in Flow 1, `WeatherPostProcessor` Protocol.

### Observation QC (Flow 2 — observation ingest)

- **WMO-168 Vol I** (primary): Chapters on data quality control — automated range checks, temporal consistency, spatial consistency, suspect/missing flags. Defines the standard QC flag vocabulary (good, suspect, erroneous, missing).
- **WMO-49 Vol III**: Station classification and observing programme definitions — relevant for station tiering and expected data frequency.

Maps to: `QualityChecker` Protocol, QC flag enum, observation ingest pipeline.

### Alert and warning system (Flow 1 — alert checking step)

- **WMO-1150**: Impact-based warnings — moves beyond pure threshold exceedance toward impact severity. Defines three-tier severity (yellow/orange/red) with recommended language and dissemination protocols. Directly relevant to Nepal v1 where DHM handles alerting; informs danger level design.
- **CAP / WMO-1109**: Common Alerting Protocol — machine-readable XML alert format. Defines `severity`, `urgency`, `certainty`, geographic `area`, and recommended actions. Enables integration with national and international warning dissemination systems.
- **WMO-1091 §10**: Multiple forecasting systems provide additional probability information for extreme events. When several independent hydrological models are available per station, their ensembles can be combined rather than selecting a single model, improving tail-event probability estimates. SAPPHIRE's multi-model alert strategy (see `architecture-context.md` Flow 1 Phase C) implements this principle via four combination strategies (primary, pooled, bma, consensus).
- **WMO-1091 §9.1.1 (by analogy)**: Per-model bias correction should be applied before combination. The original section addresses NWP ensemble post-processing; the principle extends to hydrological model output — each model's forecast ensemble should pass through its own post-processing (step 1.9) before entering the pooled or BMA combination in Phase C.

**Distinguishing SAPPHIRE BMA from WMO-1254 BMA**: WMO-1254 Tier 3 defines BMA as a method for post-processing atmospheric EPS members into calibrated probabilistic forecasts (operating on raw NWP member output). SAPPHIRE's BMA (`bma` alert strategy, plan 010) operates at a different point in the forecast chain — it combines outputs from multiple hydrological models (each already producing an ensemble over NWP members) using skill-based weights. Both use Bayesian Model Averaging as the mathematical framework, but they address distinct combination problems and are applied at different stages. The WMO-1254 Tier 3 approach would apply to step 1.5 (NWP post-processing); SAPPHIRE BMA applies at step 1.11 (alert threshold checking).

Maps to: `AlertChecker` Protocol, danger level definitions, notification system, Nepal v1 DHM integration, multi-model alert strategy (plan 010).

### Station metadata (Flow 5 — station onboarding)

- **WIGOS / WMO-1192** (primary): Metadata fields for station discovery and interoperability. Defines the WIGOS Station Identifier (WSI) format (`0-{country}-{network}-{local_id}`). Reference for which fields to capture in the `stations` table.
- **WMO-49 Vol III**: Station classification, identification schemes, observing programme definitions. Complements WIGOS for network-level metadata.

Maps to: `stations` table schema, station onboarding workflow.

### Data exchange and API (API layer)

- **WHOS / WaterML 2.0**: OGC standard for exchanging hydrological observations, forecasts, and alerts. Consider as an optional output format for international interoperability (v1+). Not required for v0.
- **WIS 2.0**: MQTT publish-subscribe for data notification; OGC EDR API for interactive data retrieval. Future consideration for publishing SAPPHIRE outputs to the WMO information system.
- **Resolution 1 (Unified Data Policy)**: Distinguishes core data (free, unrestricted exchange) from recommended data. Governs international data-sharing obligations — relevant when SAPPHIRE outputs are made available to external agencies.

Maps to: API response formats, future interoperability layer.

### System design (cross-cutting)

- **WMO-1072**: End-to-end reference for flood forecasting systems — data collection, model selection, warning dissemination chain. Use to validate architectural decisions against international practice.
- **QMF-H**: Quality assurance for the full pipeline — data validation, operational procedures, service delivery, documentation. Frames pipeline monitoring (Flow 4) as a QA activity.
- **WMO-1044**: Rating curve methodology — relevant to Nepal v1 rating curve correction parameter (open design item in `memory/project_rating_curve_correction.md`).

Maps to: overall architecture validation, pipeline monitoring (Flow 4), Nepal v1 Flow 5 design.

## 4. v0 vs v1 applicability

### v0 (Swiss data — immediate)

| Standard | What to apply |
|----------|---------------|
| WMO-1364 | Verification metrics for skill computation (`compute_skills` flow). Implement CRPS, Brier Score, rank histograms. |
| WMO-1091 | Ensemble interpretation: derive probability forecasts from ICON-CH2-EPS members in the forecast cycle. |
| WMO-1254 | Tier 1 bias correction when NWP archive is sufficient. Tier 1 pass-through is acceptable for v0. |
| WMO-168 Vol I | QC flag vocabulary and automated checks for SMN and BAFU observation ingest. |
| WMO-1072 | Reference for validating overall system design — read at architecture review points. |

### v1 (Nepal — deferred)

| Standard | When it applies |
|----------|-----------------|
| WMO-1150 + CAP (WMO-1109) | Impact-based warning design and CAP format for Nepal DHM integration. Design in Flow 3 / notification layer. |
| WIGOS (WMO-1192) | Station metadata interoperability when integrating DHM stations. |
| WHOS / WaterML 2.0 | Data exchange if international sharing is required (DHM, ICIMOD). |
| WMO-1044 | Rating curve methodology for Nepal rating curve correction parameter. |
| WIS 2.0 | Data publication if WMO integration is requested. |
| WMO-1254 Tier 2/3 | Advanced calibration once 6–12 months of NWP archive are available. |

## 5. Gap analysis and resolution

### Addressed in v0

| Gap | WMO reference | Resolution |
|-----|--------------|------------|
| **QC "missing" status** | WMO-168 Vol I | Added `MISSING` to `QcStatus` enum. Expected-but-not-received observations are represented as explicit gap markers (`value = NULL`, `qc_status = 'missing'`). Aligns with WMO standard QC flag vocabulary (good, suspect, erroneous, missing). |
| **Sharpness metric** | WMO-1364 (sharpness dimension) | Added explicit sharpness metrics to skill computation spec: mean prediction interval width (P10–P90, P25–P75), mean ensemble range. Computed per lead time alongside reliability diagnostics. |
| **Forecasters informed when forecast produced under degraded input conditions** | WMO-1072, QMF-H | `InputQualityLevel` + `InputQualityFlag` on `OperationalForecast`; API exposes quality level and flags; dashboard displays at-a-glance indicator. (Plan 023) |

### Deferred to v1+

| Gap | WMO reference | Plan | Earliest |
|-----|--------------|------|----------|
| **Impact-based warnings** | WMO-1150 | Add impact layer (exposure, vulnerability) on top of existing danger levels. Nepal DHM may handle alerting in-house. | v1 |
| **CAP alert format** | WMO-1109 | CAP XML serializer for alert records. Optional API endpoint or push feed for DHM integration. | v1 |
| **WIGOS Station Identifiers** | WMO-1192 | Column added in v0 (present in `stations` table). Population: Swiss stations have WIGOS IDs in v0; Nepal stations populated during v1 onboarding. | v0 (column), v1 (Nepal population) |
| **WaterML 2.0 / WHOS** | WHOS | Optional WaterML 2.0 serializer for observation and forecast time series. Only if international data sharing is required. | v1+ |
| **Advanced EPS calibration** | WMO-1254 Tier 2/3 | MOS (Tier 2) after 6–12 months NWP archive. EMOS/BMA (Tier 3) post-v1. | v1 |
| **Neighboring station visualization** | WMO-168 Vol I (spatial consistency) | Dashboard map view showing neighboring stations for manual spatial consistency assessment. Not automated QC — SAPPHIRE is a forecast tool, not a QC platform. | v1+ (dashboard) |
