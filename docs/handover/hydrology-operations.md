# SAPPHIRE Flow — Hydrologist Guide (Nepal Deployment)

**Audience**: DHM hydrologists and flood forecasters who will operate SAPPHIRE Flow.
**Document version**: April 2026

---

## 1. What SAPPHIRE Flow Does

SAPPHIRE Flow is an operational hydrological forecasting system. It ingests processed weather and snow forecast data from the Sapphire Data Gateway (sourcing ECMWF ensemble weather forecasts and SnowMapper snow forecasts) and real-time observations from DHM stations, runs hydrological models, produces probabilistic water level or discharge forecasts, checks flood thresholds, and makes results available via API.

Key characteristics:

- **Probabilistic, not single-valued.** Every forecast is an ensemble — 51 or more traces — allowing exceedance probabilities to be computed for each danger level. Forecasters and downstream systems see the full uncertainty range, not a single best-guess line.
- **Multi-model with automatic fallback.** Multiple hydrological models can be assigned to each station. If the primary model fails, the system falls back to the next in priority order automatically.
- **Water level first, discharge later.** — water level is the initial forecast variable, with discharge conversion via rating curves planned for a later phase. This allows us to deliver operational forecasts sooner while building the rating curve database and conversion process in parallel. Forecast quality depends on station data and weather forecast quality.
- **Audit trail throughout.** Every forecast adjustment, model choice, and alert state change is recorded with user identity, timestamp, and reason.

---

## 2. Operational Data Flows

The section below gives an overview over the most important operational data flows. A more detailed document is currently in preparation.

```
Sapphire Data Gateway ──→ Weather + Snow Ingest ──────────→ ┐
  (ECMWF + SnowMapper)                                      ├──→ Forecast Models ──→ Alert Check ──→ API
DHM Stations (sub-hourly) ──→ Observation Ingest ──→ QC ────┘                                      ↓
                                                                                      DHM Dashboard
```

### Forecast cycle

Triggered after each data delivery from the Sapphire Data Gateway, approximately four times per day (00, 06, 12, 18 UTC). The cycle:

1. Fetches the latest ECMWF IFS ensemble weather forecasts and SnowMapper snow forecasts from the Sapphire Data Gateway.
2. Extracts precipitation, temperature, snow, and other variables for each station's basin, applying elevation-band splitting for steep Himalayan terrain.
3. Fetches the most recent quality-controlled observations to initialise and warm up model state.
4. Runs all assigned models for every operational station.
5. Checks each ensemble against configured flood thresholds, computing exceedance probabilities.
6. Raises, updates, or resolves alerts as thresholds are crossed.
7. Stores alerts in the database, queryable via the REST API. DHM's alerting system polls the API to pick up new and changed alerts.

Target: all stations complete within 15 minutes per cycle.

When data from the Sapphire Data Gateway is late (a normal occurrence with ECMWF deliveries), the system waits up to three hours, then falls back to the most recent available cycle, then skips with a warning if no cycle is available within 12 hours. Every forecast record stores which ECMWF cycle it used.

### Observation ingest and quality control

Runs on a sub-hourly schedule (approximately every 30 minutes). For each DHM station:

1. Fetches latest transmitted observations (water level, precipitation, temperature as applicable).
2. Stores the raw value — raw values are never overwritten.
3. Runs Stage 1 QC — sensor validation:
   - Range check (within sensor limits and historical flood-of-record bounds)
   - Rate-of-change check (physically possible rise/fall rates per station)
   - Frozen sensor detection (identical value repeated over N intervals)
   - Spike detection (single-interval outlier that reverts)
   - Gross outlier detection (beyond historical climatological envelope)
4. Flags each observation: `qc_passed`, `qc_suspect`, or `qc_failed`. A `missing` marker is inserted if an expected observation was not received.
5. Derives discharge from water level using the active rating curve (once rating curves are loaded).
6. Checks observed values against flood thresholds in real time and raises observation-based alerts. Alerts can be queried via API.

QC-passed observations feed into the forecast cycle (step 3 above). Raw, suspect, and failed values are stored for operator review but excluded from forecast model inputs.

### Pipeline watchdog

Runs every 10 minutes, independently of the forecast cycle and observation ingest. Checks:

- Whether the weather and snow forecasts arrived from the Sapphire Data Gateway on schedule.
- Whether each DHM station's most recent observation is within the expected interval.
- Whether the last forecast cycle completed on time.
- Disk usage and backup age.

Pipeline alerts are intended to go to the IT and operations team, not to flood forecasters. They use the same alert infrastructure but are labelled separately.

---

## 3. What DHM Provides

| Data | Format | Frequency | Notes |
|------|--------|-----------|-------|
| Station metadata | API (agreed) | Once, then updates | Location (lat/lon), parameters measured, gauge zero datum, IANA timezone |
| Real-time observations | API (agreed) | Sub-hourly (5-min tipping bucket preferred) | Water level (primary), precipitation, temperature where available |
| Flood thresholds | API, CSV, or spreadsheet (TBD) | Once, then annual review | Per station, per danger level. Units and datum must match the forecast variable. |
| Historical observations | CSV or database export | Once (for training) | 20 years daily data (agreed), plus whatever sub-daily data is available. |
| Rating curves (hQ tables) | CSV | Annual updates after surveys | Water level vs. discharge pairs per station. Required for future discharge conversion. |

**Snow data**: In addition to DHM-provided data, SAPPHIRE Flow ingests snow water equivalent (SWE) and snowmelt forecasts from SnowMapper via the Sapphire Data Gateway. This data is available as model input for snow-influenced catchments — snow observations will be ingested into the SnowMapper model directly, not into the forecast models.

**Station metadata minimum fields required for onboarding**: station code, name, geographic coordinates, station type (river/weather/lake), IANA timezone identifier (e.g. `Asia/Kathmandu`), list of parameters measured.

**Basins and catchments**: Each gauging station is associated with its own catchment area — the specific drainage area upstream of that gauge. This is the unit used for weather forcing extraction and hydrological modelling. Multiple stations on the same river (e.g. Karnali at Benighat and Karnali at Chisapani) each have their own catchment and are modelled independently. A regional basin name (e.g. "Karnali") can optionally be stored for grouping and display purposes, but it does not affect the modelling — each station's forecast is based on its own catchment forcing.

**Data transfer**: SAPPHIRE Flow polls DHM's existing data system (WISKI) via an agreed API to fetch real-time observations. DHM does not need to push data to a new endpoint — the existing telemetry infrastructure remains unchanged.

---

## 4. Modelling Approach

### Weather forcing

ECMWF IFS ENS provides 51 ensemble members at approximately 9 km resolution over Nepal, with 3-hourly fields to day 6 and 6-hourly fields to day 5, at a lower temporal resolution up to day 15. Each ensemble member represents a physically consistent scenario for the next five days. In addition, SnowMapper provides spatially distributed snow water equivalent (SWE) and snowmelt forecasts, which are critical for monsoon-season and spring runoff prediction in Nepal's snow-influenced catchments. Both data sources are delivered through the Sapphire Data Gateway which requires an internet connection.

### Elevation-band extraction

Nepal's basins span 1,000–6,000 m elevation. A single basin-average temperature or precipitation value is inadequate — it destroys the elevation signal that controls snowmelt, rain-snow partitioning, and runoff timing. SAPPHIRE Flow divides each basin into elevation bands and extracts separate NWP values for each band. Temperature lapse-rate correction is applied across bands. This is the recommended starting approach; a gridded spatial input with a convolutional neural network front-end is a research extension if elevation bands prove insufficient.

Elevation bands are defined in one of two ways:

- **From shapefiles**: HSOL will perform the initial station onboarding together with DHM during deployment. After deployment, DHM uploads basin shapefiles for new stations to be added to the forecasting system following detailed instructions provided by HSOL.
- **Standard band widths**: If shapefiles are not available, bands can be specified using standard widths of 200, 500, 1000, or 2000 metres, derived automatically from a DEM.

Models may declare which elevation band resolution they expect. The system validates that the configured bands for a station satisfy the model's requirements before training or forecasting.

### Model types

| Model | Description | When used |
|-------|-------------|-----------|
| Linear regression | Fast, low-data-requirement baseline | Always assigned; primary fallback when other models fail |
| LSTM neural network or Deep Learning models | Long short-term memory or deep learning models trained on 1–5 years of history | Assigned after successful training; higher accuracy during monsoon and flood events |
| Conceptual (HBV/airGR) | Process-based model with snow, soil moisture, groundwater stores | Supported by the architecture but not planned for the initial Nepal deployment. Possible future contribution from university students (e.g. TU bachelor/master thesis work) — quality cannot be guaranteed in that case. |

Some models — particularly the LSTM neural network — require a warm-up period of historical weather data (typically 365 days) concatenated with the forecast to fill their memory window. The system supports model-declared warm-up requirements: each model specifies the length of historical context it needs, and the pipeline assembles the warm-up data automatically before each forecast cycle. For Nepal, warm-up forcing comes from ERA5-Land reanalysis (a gap-free gridded archive from the European Centre for Medium-Range Weather Forecasts).

Short gaps in the warm-up data (e.g. from delayed ERA5-Land updates) are interpolated automatically. If a gap exceeds the configurable maximum interpolation window, the forecast cycle for that station is skipped with a warning rather than producing a forecast from incomplete state and a fallback model is applied.

**Downscaling**: Both ERA5-Land reanalysis and ECMWF weather forecasts are downscaled using the same methods, ensuring consistency between training data and operational forcing. The downscaling approach is an active research area — several methods are being evaluated:

- **Elevation-dependent downscaling** using DHM station observations (temperature, precipitation) to correct for local biases in coarse-resolution gridded products.
- **Pressure level data** from ERA5/ECMWF may be used as additional forcing variables where surface-level fields are insufficient in complex terrain.
- **Topographic downscaling** for temperature, building on SnowMapper's existing approach (Joel Fiddes' methodology).
- **Precipitation**: downscaling follows established methods from the literature; no proprietary approach is applied.
- **Bias-correction** with trusted Radar products, e.g. GPM.

The key design constraint is that whatever downscaling is applied to ERA5-Land for training must also be applied identically to ECMWF forecasts for operational use. This prevents a systematic mismatch between the data the model learned from and the data it receives in production.

**Future NWP sources**: The architecture supports adding new weather forecast sources. Once WARF (Weather and Research Forecasting) forecasts are mature enough for operational use in hydrological forecasting, SAPPHIRE can be configured by DHM to ingest them. Note that switching or adding a new weather forcing source requires retraining all ML models on the new data — models trained on ECMWF cannot be used directly with WARF forcing. Instructions will be provided to DHM to perform the onboarding of a new weather forecast product.

### Uncertainty representation

The operational default is a **probabilistic output head (CMAL)**: the model is trained to predict a probability distribution directly from input data in a single forward pass. This is a proven approach at scale that avoids the computational cost of running each ensemble member separately through the hydrological model.

An alternative approach — running each of the 51 ECMWF ensemble members separately through the hydrological model to propagate meteorological uncertainty directly — may be explored in joint research between hydrosolutions and Tribhuvan University, depending on student interest.

### Model verification

Forecast accuracy is tracked continuously using standard WMO verification metrics (WMO-1364):

- **CRPS** (Continuous Ranked Probability Score) — overall ensemble accuracy
- **Brier Skill Score** — threshold exceedance probability accuracy
- **Reliability diagrams** — whether stated probabilities are trustworthy
- **Rank histograms** — whether ensemble spread is appropriate (under- or over-dispersive)
- **NSE, KGE, PBIAS, MAE** — deterministic accuracy of ensemble median
- **Peak timing error** — how accurately the model times flood peaks

Skill scores are computed by running the trained model over archived historical periods (hindcasting). The weather forecast archive needed for hindcasting must be built up over time — SAPPHIRE archives every ECMWF forecast it receives, but this archive starts from deployment. Until sufficient forecast archive is available (typically 6–12 months), skill scores are computed using reanalysis weather data (ERA5-Land) as a substitute. Reanalysis-based skill scores are a useful approximation but tend to be optimistic compared to scores based on actual forecasts. Models are retrained and re-evaluated when performance degrades.

---

## 5. Forecast Workflow

### Forecast status

```
Model output (raw) ──→ Forecaster review & editing ──→ Published ──→ Bulletin
```

| Status | Meaning | Visible to external consumers? |
|--------|---------|-------------------------------|
| Raw | Automated model output; no human interaction | No |
| Reviewed | Forecaster has examined the forecast and optionally adjusted values | No |
| Published | Forecaster has approved for release | Yes — via API |

### From forecast to bulletin

In a typical operational workflow, the forecaster needs to:

1. **View** the raw model output (ensemble spread, exceedance probabilities, multi-model comparison)
2. **Approve** the forecast for publication (change status from raw → published)
3. **Produce a bulletin** from the published forecast for distribution

### Dashboard

SAPPHIRE Flow includes a minimal forecast review dashboard intended for development. In the initial deployment, this dashboard supports:

- **Viewing** model inputs and outputs, ensemble spread, forecast skill metrics, and alert status (all tables contents)
- **Link** to the Prefect flow dashboard for monitoring of the individual data flows.

This dashboard is not optimized for operational forecasting use but for visual validation of code and model development. Forecast value editing (shift, scale, cap, floor adjustments with audit trail).

DHM has its own forecast dashboard under development or recently completed. DHM's dashboard can call the SAPPHIRE API to change a forecast's status (raw → published) after review of the forecasts. Only reviewed, i.e. published forecasts can be made available to other government institutions or downstream forecast users.

**Open question — forecast review workflow**: Can DHM's existing dashboard support the forecast approval/publication step (i.e. call the SAPPHIRE API to publish a forecast)? If no, what would it entail in terms of costs to support this?

### Alert timing

Threshold checks run on raw forecasts immediately after each model cycle. Alerts are stored and queryable via the REST API (`GET /api/v1/alerts`). DHM's alerting system polls this endpoint to pick up new and changed alerts and handles distribution to forecasters and downstream agencies.

---

## 6. Alerting

### Danger levels

Danger levels are defined once per deployment, in consultation with DHM. Each level has a name, a configured trigger probability, and a hysteresis band to prevent repeated fire-and-clear cycles during oscillating ensembles.

Switzerland uses five levels (low, moderate, considerable, high, very high). Nepal's level set will be agreed with DHM — typically three to four levels. Each level definition includes:

| Field | Meaning | Example |
|-------|---------|---------|
| Trigger probability | Minimum fraction of ensemble members that must exceed the threshold to raise an alert. E.g. 50% means at least 26 out of 51 ensemble members must show exceedance. | 50% |
| Resolve probability | Fraction must fall below this to clear the alert — set lower than trigger to prevent repeated fire-and-clear cycles when the ensemble oscillates around the threshold. | 30% |
| Minimum trigger duration | How long the exceedance probability must remain above the trigger threshold before an alert is raised. Prevents alerts from brief ensemble spikes. | 12 hours |
| Minimum resolve duration | How long the probability must remain below the resolve threshold before the alert is cleared. | 6 hours |

### Per-station thresholds

Each station has threshold values for a subset of danger levels. Not all levels need to be defined for every station — undefined levels are simply skipped (no check, no display).

Thresholds are loaded from DHM's official definitions at station onboarding. In future, where official thresholds are unavailable, they can be estimated from flood frequency analysis on the historical record (20+ years recommended), clearly marked as inferred rather than authoritative.

### Alert types

| Type | Trigger | Source |
|------|---------|--------|
| Forecast alert | Ensemble exceedance probability exceeds configured threshold | Model output |
| Observation alert | Real-time measured value exceeds threshold directly | Station observation |
| Pipeline alert | Data freshness, flow run failure, disk usage | System watchdog |

Forecast and observation alerts share the same danger levels and threshold values. The difference is in the check: forecast alerts use exceedance probability across ensemble members; observation alerts use a direct value comparison.

### Alert lifecycle

```
raised ──→ acknowledged ──→ resolved
```

- **Raised**: threshold first exceeded; notification dispatched.
- **Acknowledged**: an operator has noted the alert; suppresses repeat notifications.
- **Resolved**: probability or value has fallen back below the resolve threshold for the required duration; auto-resolved by the system.

### Notifications

DHM's alerting system polls the SAPPHIRE REST API (`GET /api/v1/alerts`) to pick up new and changed alerts. DHM is responsible for downstream distribution (SMS, email, etc.) to forecasters, field staff, and partner agencies.

If DHM's existing systems cannot poll the API, SAPPHIRE can implement webhook push notifications as an alternative — this would need to be agreed as additional scope. See section 14, question 6.

---

## 7. Data Access (REST API)

All data is available through a REST API — a web interface that allows dashboards, portals, and other systems to retrieve data programmatically. Consumers include (examples):

| Consumer | Scope |
|----------|-------|
| DHM | Full access — all stations, all parameters |
| Other government agencies (e.g. DRRMA) | Flood alerts and forecast data (scoped access) |
| Other government authorities | Scoped access per agency |
| Hydropower operators | Stations relevant to their operations |
| Neighbouring countries | Border-relevant stations |

Each consumer receives an API key scoped to the stations and parameters they are permitted to access. The API returns JSON by default; CSV export is supported for all time-series endpoints.

**Key endpoints** (illustrative):

| Purpose | Example |
|---------|---------|
| List stations | `GET /api/v1/stations` |
| Observations for a station | `GET /api/v1/stations/{id}/observations` |
| Forecasts for a station | `GET /api/v1/stations/{id}/forecasts` |
| Active alerts | `GET /api/v1/alerts` |
| System health | `GET /api/v1/health` |

Temporal aggregation is supported — for example, daily, monthly, or custom periods. The system also supports pentadal (5-day) and dekadal (10-day) aggregation for deployments that use these intervals (e.g. Central Asian hydromets).

### Delft-FEWS compatibility

We are aware that DHM has interest in other state-of-the-art hydrological modeling platforms, including Delft-FEWS. While SAPPHIRE Flow is not built on the FEWS platform, the architecture has been designed with future FEWS coupling in mind. The key data structures — ensemble forecasts with individual members, station metadata, observation timeseries with QC flags — map directly to the Delft-FEWS Published Interface (PI) data model:

| SAPPHIRE Flow | Delft-FEWS PI equivalent |
|---------------|-------------------------|
| Station code | `locationId` |
| Parameter name (`water_level`, `discharge`) | `parameterId` (`H.sim`, `Q.sim`) |
| Ensemble members (per-member timeseries) | One PI `<series>` per `ensembleMemberIndex` |
| Forecast issue time | `<forecastDate>` |
| QC flags | PI `flag` attribute |

If DHM decides to deploy Delft-FEWS in the future, SAPPHIRE Flow's forecasts and observations can be made available to FEWS through a lightweight adapter — a thin script that reads from SAPPHIRE's REST API and writes PI-XML files to a directory that FEWS monitors. This is the standard FEWS integration pattern (General Adapter) and does not require changes to SAPPHIRE's architecture. Alternatively, we can add a PI-XML or PI-JSON export format directly to the SAPPHIRE API.

This coupling would allow DHM to use SAPPHIRE Flow for automated forecasting while using Delft-FEWS for visualization, manual intervention, or integration with other models — the two systems complement rather than compete.

---

## 8. Timezone and Calendar

All observations, forecasts, and alert timestamps are stored internally in UTC. Display conversion happens at the API and dashboard boundary.

| Setting | Nepal |
|---------|----------|
| Storage timezone | UTC |
| Display timezone | Nepal Standard Time (NPT, UTC+05:45) |
| Calendar system | Gregorian by default (see open question below) |
| Hydrological day boundary | 00:00–00:00 NPT |

**Open question — Bikram Sambat calendar**: SAPPHIRE can be configured to display dates in Bikram Sambat for the API, dashboard, and reports. If DHM's operational workflow uses Gregorian dates, we will use Gregorian throughout — this is simpler and avoids additional development effort. Does DHM require Bikram Sambat date display, or is Gregorian acceptable?

---

## 9. Rating Curves

Rating curves (hQ tables) describe the relationship between water level and discharge at a gauging station. They are used to derive discharge from the real-time water level observations that DHM transmits, once discharge conversion is enabled.

Key points for DHM operations:

- Each station holds a versioned history of rating curves with valid-from dates.
- When a curve is updated (e.g. after a major flood that shifts channel geometry), the new curve is uploaded and becomes active from its valid date. Historical observations are not retroactively recalculated with the new curve — the version used at the time is recorded permanently on each derived value.
- If water level exceeds the maximum calibrated point of the curve (extrapolation), the derived discharge value is flagged as extrapolated rather than rejected. The flag includes the extrapolation magnitude — how far beyond the maximum calibrated point the value lies (as a percentage). This distinguishes minor extrapolation (5% beyond the curve) from extreme extrapolation (100%+ beyond), which are very different operational situations. Flood-peak values are retained with their flag — they are operationally important even when uncertain.
- Updated curves should be uploaded at the start of each season or after major channel surveys. DHM hydromet operations staff will have access to the upload interface (maybe a folder location on the server).

---

## 10. Observation QC — What the Flags Mean

We understand that DHM hydrologists already perform quality checks on their raw observation data, but that these checks are currently not persistent or documented — results are not stored and the criteria are not formally recorded. SAPPHIRE Flow aims to implement the same quality checks DHM currently applies, so that the automated QC is consistent with DHM's existing practices. The difference is that SAPPHIRE retains all QC results permanently: which checks ran, what was flagged, and why. This gives DHM a documented QC history for the first time, without changing the checks themselves.

We would like to work with DHM hydrologists during the AWS testing phase to document their current QC criteria (thresholds, rules, tolerances) so we can configure SAPPHIRE's automated checks to match.

Quality control runs automatically for every incoming observation. No observation value is ever silently discarded — raw values are always preserved and QC status is stored as metadata.

| QC Status | Meaning | Used in forecasting? |
|-----------|---------|----------------------|
| `raw` | Just ingested; QC has not yet run | No — awaiting QC |
| `qc_passed` | All automated checks passed | Yes |
| `qc_suspect` | At least one check raised a concern; value may be wrong | No (by default) |
| `qc_failed` | At least one check found the value invalid | No |
| `missing` | Expected observation was not received | No — gap marker |

When a value is flagged `qc_suspect` or `qc_failed`, the flag record explains which check triggered it and what the specific problem was (e.g. "value 8.4 m exceeds maximum sensor range of 7.0 m" or "rise rate 0.8 m/min exceeds physical limit of 0.3 m/min"). Operators can review flagged values in the dashboard.

Manual override (API): an authorised operator can override the automated status on individual observations — marking them valid or invalid — with a recorded identity and rationale.

---

## 11. Canonical Parameter Names

All data in SAPPHIRE Flow uses standard parameter names regardless of the source-specific terminology.

| Parameter | Unit | Description |
|-----------|------|-------------|
| `water_level` | m | Above gauge zero datum (documented per station) |
| `discharge` | m³/s | Volumetric flow rate |
| `precipitation` | mm | Accumulated rainfall/snowfall |
| `temperature` | °C | Air temperature |
| `humidity` | % | Relative humidity |
| `wind_speed` | m/s | Wind speed |
| `snow_depth` | cm | Snow depth at station |

The Nepal deployment expects water level as the primary river station parameter. Discharge is derived via rating curves when available.

---

## 12. WMO Standards Applied

SAPPHIRE Flow is designed against international WMO standards throughout.

| Standard | Relevance to Nepal deployment |
|----------|-----------------------|
| WMO-1072 — Manual on Flood Forecasting and Warning | End-to-end system design reference |
| WMO-1364 — Verification of Hydrological Forecasts | Skill metrics (CRPS, Brier Score, reliability, rank histograms) |
| WMO-1091 — Ensemble Prediction Systems | Ensemble interpretation; multi-model combination |
| WMO-1254 — EPS Post-processing | Bias correction strategy; Tier 1 pass-through initially, Tier 2 (MOS) after 6–12 months of archive |
| WMO-168 Vol I — Guide to Hydrological Practices | Observation QC flag vocabulary; automated checks |
| WMO-1150 — Impact-Based Forecast and Warning | Danger level design; severity/urgency/certainty framework |
| WMO-1109 / CAP — Common Alerting Protocol | Machine-readable alert format for integration with national warning systems |
| WMO-1044 — Manual on Stream Gauging | Rating curve methodology |
| WIGOS (WMO-1192) | Station metadata interoperability — not used internally by DHM, but supported for international data sharing if needed |

---

## 13. Deployment Timeline

| Phase | Target | Scope |
|-------|--------|-------|
| Swiss validation | Mid-2026 | End-to-end pipeline on Swiss public data. Proves architecture, models, QC, alerting, and API. |
| Nepal testing (AWS) | ~6–12 months | System runs on AWS infrastructure managed by the SAPPHIRE team. Model training, pipeline validation, and skill evaluation using DHM data. DHM accesses the system remotely for review and feedback. |
| Nepal production (DHM) | After successful AWS validation | Full operational deployment on DHM's own infrastructure. DHM IT takes over hosting and operations with SAPPHIRE team support. |
| Discharge conversion | TBD | Discharge conversion via rating curves. Expanded parameter support. Forecast review dashboard. |
| Ongoing | — | Model retraining, skill monitoring, rating curve updates, system maintenance. |

The Swiss validation phase is not a prototype to be discarded — it is the same production codebase. The AWS testing phase gives DHM time to evaluate the system, provide feedback, and prepare their infrastructure before taking over operations.

---

## 14. Questions for DHM

Questions are grouped by urgency. Numbered for point-by-point response.

### Must know — answers affect system design

These questions need confirmed answers early, as the answers influence architectural decisions that are costly to change later.

**1. Flood threshold system — which is authoritative?**

We understand that Nepal uses two separate threshold systems: the official MoHA warning levels (normal, alert, danger, extreme — four levels) and internal DHM operational monitoring thresholds. SAPPHIRE needs to implement one authoritative set. Questions:

- Which threshold system should SAPPHIRE use — the MoHA official levels, the internal DHM operational thresholds, or both (with clear labelling)?
- When an external consumer (DRRMA, Bipad, hydropower operators) sees a "danger" alert from the SAPPHIRE API, should that correspond to the official MoHA level or to DHM's internal operational level?

*Why we are asking*: The threshold values, the number of levels, and the semantic meaning of each level affect the alert schema design. Mixing two systems without clear labelling creates confusion during flood events.

**2. Seasonal thresholds**

Our initial plan is to use thresholds that are valid year-round (same values for monsoon and dry season). However, we are aware that during monsoon season, baseline flows are elevated at many stations, which could cause persistent low-level alerts that do not represent actual flood risk.

Does DHM require seasonally varying thresholds (e.g. different values for June–September vs. October–May)? If yes, how many seasonal periods, and are the seasonal threshold values already defined?

*Why we are asking*: Year-round thresholds are simpler and our preferred starting point. If seasonal variation is required, the threshold schema needs a validity period — a design decision that should be made before implementation. We will verify during the AWS testing phase whether year-round thresholds produce acceptable alert behaviour for Nepal's stations.

**3. QC-suppressed observation alerts**

During a real flood event, station sensors are often submerged, fouled, or reading erratically — exactly when real-time data matters most. If the observation QC flags a value as `qc_failed`, the observation-based alert check is suppressed (the system will not raise an alert based on a value it considers invalid).

Should SAPPHIRE notify the forecaster when an observation-based alert check was suppressed due to QC failure? This would be a distinct notification: "Station X observation exceeded the danger threshold but was QC-failed — review the raw value manually." Without this, the forecaster might assume a station is safe when in fact the sensor was simply malfunctioning.

*Why we are asking*: This requires a "suppressed alert" state or notification type in the alert pipeline. If DHM wants this, it needs to be designed into the alert system.

**4. Water level first — confirmation**

Our plan for the initial deployment is to forecast water level directly and add discharge conversion as a follow-up. Water level is more directly observable and avoids compounding uncertainty from rating curve errors.

Is water-level-only forecasting operationally acceptable for DHM's flood warning workflow in the first phase? Or does DHM's internal process require discharge values from day one?

Do we have any information about which cross-sections are immutable (e.g. major river stations) versus which are more likely to have rating curve updates after major floods?

**5. Forecast frequency during flood events**

The Sapphire Data Gateway delivers weather and snow forecasts every six hours (following the ECMWF cycle). Our standard schedule runs one forecast cycle after each delivery.

During active flood events, does DHM need more frequent forecast updates — for example, every one to two hours? If yes: would DHM use real-time rainfall observations to update forecasts between delivery cycles?

*Why we are asking*: More frequent updates are possible in principle but require additional engineering work. If this is a firm requirement for the initial deployment, we need to design for it now.

**6. Alerting integration**

SAPPHIRE stores alerts in the database and serves them via the REST API (`GET /api/v1/alerts`). Our base assumption is that DHM's existing alerting system polls this endpoint to pick up new and changed alerts, and handles downstream distribution (SMS, email, field staff notifications).

- What alerting system does DHM currently use, and can it poll a REST API on a schedule?
- If polling is not feasible, SAPPHIRE can implement webhook push notifications as additional scope — what would integration on DHM's side require?

### Can be resolved during the AWS testing phase

These questions are important but do not block system design. They can be addressed while the system is running on AWS for validation.

**7. Flood threshold definitions**

How are flood thresholds defined at DHM?

- Units: water level (m) or discharge (m³/s)?
- Reference datum: metres above sea level, metres above gauge zero, or other?
- How many stations currently have defined thresholds?
- Are thresholds available via API or only as a spreadsheet?

*Context*: Thresholds must be in the same units and datum as the forecast variable. The system is designed to accept thresholds in any format — this question determines configuration, not architecture.

**8. Retroactive observation corrections**

Does DHM retroactively correct observation values in their source database after initial transmission? If so, how frequently (daily, weekly, rarely)?

*Context*: The system currently treats each transmission as final. If DHM does apply retroactive corrections, we can add correction detection — this is an adapter-level change, not an architectural one.

**9. Flood threshold format**

For flood thresholds: will these be available via the same API as station metadata, or provided as CSV/spreadsheet?

*Context*: SAPPHIRE can ingest thresholds from either source. This determines configuration, not architecture.

**10. Input quality flagging and forecaster notification**

Should SAPPHIRE flag each forecast with an input quality indicator (FULL / PARTIAL / DEGRADED) based on observation staleness, NWP cycle age, and warm-up state? Should forecasters be notified when a forecast is produced under degraded input conditions?

Yes to both. SAPPHIRE flags every operational forecast with an `InputQualityLevel` (FULL, PARTIAL, or DEGRADED) and a list of `InputQualityFlag` entries explaining what is degraded and why. The quality level is exposed in the API and displayed in the dashboard. In v0, there is no push notification — quality is visible in the API response and logged. In v1, a forecaster notification will be added when a station's forecast is DEGRADED, using the notification infrastructure (step 1.14).
