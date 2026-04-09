# SAPPHIRE Flow — Data Flows

**Audience**: DHM technical staff — hydrologists, IT, and integration partners.
**Document version**: 0.1-draft (April 2026)
**Status**: DRAFT — subject to change. This document describes the intended design; implementation is ongoing.

---

## Overview

SAPPHIRE Flow processes data through 14 distinct flows, organised into three categories: **operational** (recurring, scheduled), **initialisation** (on-demand, typically once per station or model), and **maintenance** (periodic or event-driven).

```
                        ┌──────────────────────────────────────────────────┐
Sapphire Data Gateway   │                SAPPHIRE Flow                     │
  (ECMWF + SnowMapper) ──→  Weather Ingest ──→ Forecast Models ──┐       │
                        │                                         ├→ API ──→ DHM Dashboard
DHM Stations (WISKI)  ────→  Observation Ingest ──→ QC ──────────┘       │    External consumers
                        │                                                  │
                        │  Watchdog (independent) ──→ Pipeline Alerts      │
                        └──────────────────────────────────────────────────┘
```

---

## Operational Flows (recurring)

### Flow 1 — Forecast Cycle

**Trigger**: After each weather forecast delivery from the Sapphire Data Gateway (~4 times/day).
**Target duration**: All stations complete within 15 minutes per cycle.

| Step | What happens | Input | Output |
|------|-------------|-------|--------|
| 1 | Fetch weather + snow forecasts | Sapphire Data Gateway (ECMWF IFS ENS, SnowMapper) | Weather forecast archive |
| 2 | Extract spatial averages per basin | Gridded weather data, basin/elevation-band definitions | Per-station forcing time series |
| 3 | Archive extracted values | Per-station forcing | Permanent NWP archive |
| 4 | Post-process weather data | Raw extracted values | Bias-corrected forcing (pass-through initially) |
| 5 | Fetch recent observations | Quality-controlled observations from database | Model warm-up + initialisation data |
| 6 | Run forecast models | Forcing + observations + model state | Ensemble forecast (members or quantiles) |
| 7 | Post-process forecast output | Raw model output | Calibrated forecast (pass-through initially) |
| 8 | Quality-check forecast | Model output | QC flags (range, consistency, ensemble spread) |
| 9 | Store forecast | QC'd ensemble output | Forecast record in database |
| 10 | Check alert thresholds | Ensemble exceedance probabilities vs. station thresholds | Alert raised / updated / resolved |
| 11 | Dispatch notifications | Alert state changes | Webhook / email / SMS (if enabled) |

**Fallback behaviour**: When weather data is late, the system waits up to 3 hours, then falls back to the most recent available cycle. After 12 hours without any data, forecasting is paused with a warning. Every forecast records which weather cycle it used.

**Multi-model**: Multiple models can be assigned per station with priority ordering. If the primary model fails, the next model runs automatically.

### Flow 2 — Observation Ingest and QC

**Trigger**: Scheduled, approximately every 30 minutes.

| Step | What happens | Input | Output |
|------|-------------|-------|--------|
| 1 | Fetch latest observations | DHM station API (WISKI) | Raw observation values |
| 2 | Store raw values | Raw observations | Permanent raw record (never overwritten) |
| 3 | Run automated QC | Raw values, station-specific QC rules | QC status per observation (passed / suspect / failed / missing) |
| 4 | Check observation thresholds | QC-passed values vs. station thresholds | Observation-based alerts |
| 5 | Dispatch notifications | Alert state changes | Webhook / email / SMS (if enabled) |

**QC checks applied**: range check, rate-of-change, frozen sensor detection, spike detection, gross outlier detection. Each flagged value includes a reason explaining what triggered the flag.

### Flow 3 — Forecast Review and Publication

**Trigger**: On-demand (forecaster action via dashboard or API).

| Step | What happens | Input | Output |
|------|-------------|-------|--------|
| 1 | Display forecast for review | Raw model output, ensemble spread, alert status | Dashboard view |
| 2 | Forecaster reviews / adjusts | Human judgement | Adjusted values (if any), with audit trail |
| 3 | Publish forecast | Reviewed forecast | Status changed to "published" — now visible to external consumers |
| 4 | Generate bulletin | Published forecast | Formatted report for distribution |

**Note**: In the initial deployment, forecast value editing is not available. Forecasters can view and approve/reject forecasts but not modify values. Forecast editing is planned for a later phase.

### Flow 4 — Pipeline Watchdog

**Trigger**: Every 10 minutes, independent of other flows.

| Check | What it monitors | Alert recipient |
|-------|-----------------|-----------------|
| Weather data freshness | Did the latest forecast arrive on schedule? | IT / operations |
| Observation freshness | Is each station transmitting within expected intervals? | IT / operations |
| Forecast cycle timeliness | Did the last forecast cycle complete on time? | IT / operations |
| System health | Disk usage, backup age, worker status | IT / operations |

Pipeline alerts are distinct from flood alerts. They indicate system health issues, not hydrological events.

---

## Initialisation Flows (on-demand)

### Flow 0 — Deployment Onboarding

**When**: Once per deployment region (e.g., once for Nepal).

| Step | What happens |
|------|-------------|
| 1 | Define area of interest |
| 2 | Download area-wide static datasets (elevation, land cover, soil, geology) |
| 3 | Download area-wide historical forcing datasets |
| 4 | Verify completeness |
| 5 | Register datasets in system catalogue |

Prerequisite for station onboarding (Flows 5/5w).

### Flow 5 — River Station Onboarding

**When**: When new river stations are added to the system (batch operation).

| Step | What happens |
|------|-------------|
| 1 | Register station metadata (code, name, location, timezone, parameters) |
| 2 | Fetch or upload catchment boundaries and attributes |
| 3 | Import historical observations |
| 4 | Run QC on historical data |
| 5 | Convert water level to discharge via rating curves (where available) |
| 6 | Compute baseline statistics and flow regime thresholds |
| 7 | Configure model assignments and priorities |
| 8 | Train models or validate pre-trained models |
| 9 | Model admin confirms station is operational |

### Flow 5w — Weather Station Onboarding

**When**: When new weather stations are added (batch operation). Simplified variant of Flow 5.

| Step | What happens |
|------|-------------|
| 1 | Register station metadata |
| 2 | Import historical observations |
| 3 | Run QC on historical data |
| 4 | Model admin confirms station is operational |

### Flow 6/9 — Model Training (initial and retraining)

**When**: During station onboarding (initial) or when model performance degrades (retraining).

| Step | What happens |
|------|-------------|
| 1 | Assemble training dataset (historical forcing + observations) |
| 2 | Train model |
| 3 | Validate against held-out period |
| 4 | Store model artifact |
| 5 | Model admin reviews and approves or rejects |
| 6 | Promote to active (replaces previous version) |

### Flow 7 — Hindcast Generation

**When**: After model training, for verification and skill assessment.

| Step | What happens |
|------|-------------|
| 1 | Run trained model over archived historical periods |
| 2 | Store hindcast results |
| 3 | Compare hindcast output against observed values |

Used for: onboarding validation, model comparison, post-retraining verification, ongoing skill tracking.

### Flow 8/10 — Skill Computation (initial and recomputation)

**When**: After hindcast generation, or periodically as forecast archive grows.

| Step | What happens |
|------|-------------|
| 1 | Compute verification metrics (CRPS, Brier Skill Score, NSE, KGE, etc.) |
| 2 | Generate diagnostic diagrams (reliability, rank histogram, ROC) |
| 3 | Store scores, broken down by lead time, season, and flow regime |
| 4 | Flag stale scores for recomputation when underlying data changes |

### Flow 12 — Observation Reprocessing

**When**: After rating curve updates, manual CSV imports, or QC rule changes.

| Step | What happens |
|------|-------------|
| 1 | Re-derive discharge values using updated rating curves |
| 2 | Re-run QC on affected observations |
| 3 | Mark affected skill scores as stale (triggers recomputation) |

### Flow 13 — Model Onboarding

**When**: When a new model type is added to the system.

| Step | What happens |
|------|-------------|
| 1 | Register model definition (entry point, input requirements, output format) |
| 2 | Validate model interface against system contract |
| 3 | Assign to station(s) or station group |
| 4 | Train, hindcast, compute skill (triggers Flows 6, 7, 8) |
| 5 | Model admin confirms operational |

---

## Maintenance Flows (periodic or event-driven)

### Flow 11 — NWP Gap Recovery

**When**: When the pipeline watchdog (Flow 4) detects missing weather forecast data.

| Step | What happens |
|------|-------------|
| 1 | Identify gaps in the weather forecast archive |
| 2 | Re-fetch missing data from the Sapphire Data Gateway |
| 3 | Flag permanently unrecoverable gaps |

---

## Data Flow Dependencies

```
Flow 0 (deployment onboarding)
  └──→ Flow 5 / 5w (station onboarding)
         ├──→ Flow 6 (model training)
         │      └──→ Flow 7 (hindcast)
         │             └──→ Flow 8 (skill computation)
         └──→ Flow 2 (observation ingest — begins immediately)

Flow 1 (forecast cycle) ← requires: stations onboarded, models trained, weather data available
Flow 3 (forecast review) ← requires: forecasts produced by Flow 1
Flow 4 (watchdog) ← independent, always running

Flow 9 (retraining) → Flow 7 (hindcast) → Flow 10 (skill recomputation)
Flow 12 (observation reprocessing) → marks skill scores stale → Flow 10
Flow 13 (model onboarding) → Flow 6 → Flow 7 → Flow 8
```

---

## Integration Points

| System | Direction | Protocol | Data exchanged |
|--------|-----------|----------|----------------|
| Sapphire Data Gateway | SAPPHIRE pulls | HTTPS (REST API) | ECMWF weather forecasts, SnowMapper snow forecasts |
| DHM stations (WISKI) | SAPPHIRE pulls | HTTPS (REST API) | Real-time observations (water level, precipitation, temperature) |
| DHM dashboard | Dashboard pulls | HTTPS (REST API) | Forecasts, observations, alerts, station metadata |
| Other consumers (DRRMA, hydropower, etc.) | Consumer pulls | HTTPS (REST API, scoped API keys) | Forecasts, alerts (filtered by access scope) |
| Notification targets | SAPPHIRE pushes (if enabled) | Webhook / SMTP / SMS gateway | Alert state changes |

---

*This document is maintained by the SAPPHIRE team. For questions, contact hydrosolutions.*
