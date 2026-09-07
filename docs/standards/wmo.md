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
| 8 | Guide to Instruments and Methods of Observation (CIMO Guide) — Vol. I | 2018 ed. | **Ch. 6 Measurement of precipitation** — gauge types, exposure, catch | [link](https://community.wmo.int/site/knowledge-hub/programmes-and-initiatives/instruments-and-methods-of-observation-programme-imop/guide-instruments-and-methods-of-observation-wmo-no-8-0) |
| IOM-131 | WMO Solid Precipitation Intercomparison Experiment (SPICE) — final report | 2018 | Gauge catch efficiency, wind-induced undercatch. **Note the series:** this is an *Instruments and Observing Methods* (IOM) report, **not** a WMO-No. publication | [record](https://library.wmo.int/records/item/56317-wmo-solid-precipitation-intercomparison-experiment-spice-2012-2015) · [SPICE](https://community.wmo.int/en/activity-areas/imop/intercomparisons/spice) |
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

#### Precipitation gauge catch efficiency

- **WMO-No. 8 (CIMO Guide) Vol I, Ch. 6 — "Measurement of precipitation"** is the instrument
  authority here, and **WMO-SPICE (IOM Report No. 131)** its intercomparison evidence; **WMO-168 Vol I,
  Ch. 3** covers the same subject from the hydrological-practice side.
  ⚠️ *Two easy confusions, both made and corrected during M-I3: SPICE is an **IOM report**, not a
  WMO-No.; and "Vol I Ch. 6" belongs to **WMO-No. 8**, not to WMO-168 — whose Ch. 6 is groundwater.*
  Together they establish that a
  precipitation gauge under-catches, that the deficit **grows with wind speed**, and that it is far
  larger for solid than liquid precipitation — unheated gauges in cold, exposed sites being the worst
  case.

**⛔ SAPPHIRE applies NO numeric catch correction, and this is a deliberate position, not an omission.**

- **Why not.** A defensible correction needs wind speed at gauge height, gauge and shield type, and
  precipitation phase, per station and per timestep. For the Nepali network we have **none of the
  three**: station metadata carries no instrument or shield type, wind is not co-located, and phase is
  inferred rather than observed. Applying a published transfer function without those inputs would
  substitute one unquantified error for another while *appearing* corrected.
- **What we do instead.** Undercatch is carried as a **signed, directional caveat on catch
  efficiency** — *for a correctly-functioning gauge, catch ≤ true precipitation* — attached to every
  magnitude we report, rather than as an adjustment to the number.
- **⛔ The caveat is directional about catch; it is NEVER a lower bound on a reported total.** Our QC is
  a physical-impossibility gate, not an outlier filter (`value_max = 200.0` mm/h, deliberately
  unreachable rather than discriminating), so a single spurious high reading passes QC and can push a
  station total **above** true precipitation — reversing the very sign the caveat would otherwise
  guarantee.

**Where this position does real work.** The DHM precipitation track's Pyramid transect
(`docs/design/dhm-precipitation-milestones.md` § M-A8) fits an apparent rain-phase precipitation
gradient of −52 %/km over 2,660–5,600 m. Because catch efficiency falls with wind and wind exposure
rises up a transect, the observed decline is **steeper than the true one** — so the figure is reported
as an **upper bound in magnitude**, and the true decline could be nil. **That bound, rather than a
corrected gradient, is the deliverable**, and it is a direct consequence of the no-correction position
recorded here.

Maps to: the DHM precipitation vision's D6, `docs/design/dhm-precipitation-vision.md`; Plan 184 D6;
and `docs/design/dhm-precipitation-phase2-recommendation.md` § 5.

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

**Standing rule (Plan 253 T4c, added 2026-09-04):** a row in either table below moves to
*Verified* only on evidence from the running system or a named, runnable test — **never** on a
plan's `status`, and **never** on a plan's own declared-but-unclaimed prerequisite. Two failure
modes produced the drift this section corrects, and a reader should be able to tell them apart:
Plan 023 was archived at `status: READY` by commit `33bdc640` — a pure file move containing no
code — while its own text deferred the DB columns and API exposure to later phases
(`023:733-740`, `023:472-477`); those deferred phases were then simply **never picked up** by any
later plan. The row went stale not because the plan lied about its own status, but because nothing
tracked the gap between "specified" and "implemented" once the plan was archived. No tooling
enforces this rule — the audit that found the drift was cheap; this is a convention, re-run
whenever this section is touched.

#### Verified against the running system

*"Verified" means a **named runnable command** — a `pytest` node or test file someone can execute as written — or a **recorded live query with its result and date**. A source citation alone is not evidence: it shows code exists, not that it behaves. A row with neither belongs in the section below. Keep each row's claim no stronger than what its command actually demonstrates.*

| Gap | WMO reference | Evidence | Verified |
|-----|--------------|----------|----------|
| **Sharpness metric** | WMO-1364 (sharpness dimension) | Mean prediction interval width (P10–P90, P25–P75), mean ensemble range, computed per lead time: `services/skill/metrics.py:107-117`, emitted at `services/skill/service.py:448-455`. Runnable: `uv run pytest tests/unit/services/skill/test_metrics.py tests/unit/services/skill/test_service.py`. | 2026-09-04 |
| **QC flag vocabulary** | WMO-168 Vol I | `QcStatus` (`types/enums.py`) maps cleanly onto WMO-168's good / suspect / erroneous / missing, plus `RAW` as a pre-check state; the aggregation is exercised by `aggregate_qc_status()`. Runnable: `uv run pytest tests/unit/types/test_domain.py`. | 2026-09-04 |
| **Automated range + temporal-consistency checks** | WMO-168 Vol I | `_apply_range_check` and `_apply_rate_of_change` (`services/qc.py:50`, `:71`), implemented by `Stage1QualityChecker` (`services/qc.py:225`). Runnable: `uv run pytest tests/unit/services/test_qc.py`. **Scope of this evidence:** checker behaviour. That the checker runs against every ingested observation is a separate claim, evidenced by the ingest flow's own tests, not by this row. | 2026-09-04 |
| **Forecasters informed when forecast produced under degraded input conditions** | WMO-1072, QMF-H | `InputQualityLevel`/`InputQualityFlag` persisted on `forecasts` (migration `0053`) and read back by value, not by dataclass default (`store/forecast_store.py`, `tests/integration/store/test_forecast_store.py`); exposed on both the list and detail API responses to every authenticated role (`api/schemas.py` `ForecastSummary`, `api/routes/api_forecasts.py`, `tests/unit/api/`) — Plan 253 Phase 1 (T1a–T1c), closing Plan 023's unfinished half. | 2026-09-04 (Plan 253 T4c) Runnable: `uv run pytest tests/integration/db/test_migration_input_quality.py tests/integration/store/test_forecast_store.py tests/unit/api/test_api_stations.py tests/unit/api/test_api_forecasts.py`. **Scope:** persistence and API exposure only — dashboard display and the `forecast.input_quality_assessed` event remain unimplemented. |

#### Specified, not verified

| Gap | WMO reference | What exists | What is missing |
|-----|--------------|-------------|------------------|
| **QC "missing" status — production coverage** | WMO-168 Vol I | `MISSING` exists in `QcStatus` and is enforced as an invariant both in the domain type (`types/observation.py:47-49`) and as a DB check constraint (`db/metadata.py:543`: `(qc_status = 'missing') = (value IS NULL)`). | No direct gauged-feed path in operational ingest synthesises a `MISSING` row for a timestamp that was expected and did not arrive. Measured 2026-09-02/03: `SELECT count(*) FROM observations WHERE qc_status='missing'` returns **0** on the mini, and all 148 deployed stations are `gauging_status = 'gauged'` (the one existing producer, `services/component_derivation.py:116`, only fires for `CALCULATED` stations, of which this deployment has none). The gauged-feed producer is **Plan 250** (`docs/plans/250-explicit-gap-markers-for-unmarked-feeds.md`, `status: DRAFT`), not yet built — stays here until it lands. |

### Deferred to v1+

| Gap | WMO reference | Plan | Earliest |
|-----|--------------|------|----------|
| **Impact-based warnings** | WMO-1150 | Add impact layer (exposure, vulnerability) on top of existing danger levels. Nepal DHM may handle alerting in-house. | v1 |
| **CAP alert format** | WMO-1109 | CAP XML serializer for alert records. Optional API endpoint or push feed for DHM integration. | v1 |
| **WIGOS Station Identifiers** | WMO-1192 | Column added in v0 (present in `stations` table, `wigos_id`). **Correction (Plan 253 T4a, measured 2026-09-02):** the previous row claimed "Swiss stations have WIGOS IDs in v0" -- false; `wigos_id` is populated on **0 of 148** stations. Population is unstarted work, not a v0 accomplishment, and remains explicitly out of this plan's scope (populating the column is separate work). | v0 (column, done), v1 (population, including Swiss -- not started) |
| **WaterML 2.0 / WHOS** | WHOS | Optional WaterML 2.0 serializer for observation and forecast time series. Only if international data sharing is required. | v1+ |
| **Advanced EPS calibration** | WMO-1254 Tier 2/3 | MOS (Tier 2) after 6–12 months NWP archive. EMOS/BMA (Tier 3) post-v1. | v1 |
| **Neighboring station visualization** | WMO-168 Vol I (spatial consistency) | Dashboard map view showing neighboring stations for manual spatial consistency assessment. Not automated QC — SAPPHIRE is a forecast tool, not a QC platform. | v1+ (dashboard) |
