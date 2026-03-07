---
status: DRAFT
---

> **DRAFT** — This design doc has not completed the review maturity gate. Do not treat as authoritative until `status: READY`.

# SAPPHIRE Flow — Design Overview

## What is SAPPHIRE Flow?

An operational hydrological forecasting system that produces probabilistic
forecasts at multiple temporal resolutions: sub-daily (3-6 day horizon),
daily (15-day horizon), with pentadal, dekadal, monthly, and seasonal aggregations planned for a later phase. It ingests weather data from APIs,
station observations in near real-time. v1 targets Nepal, with Central Asia
planned for v3. It serves forecasts via a REST API
with an optional review dashboard. Includes a full operational workflow:
data quality control, forecast review and adjustment, model skill assessment,
flood alerting, bulletin generation (Excel via ieasyreports), and scoped
data sharing with external institutions.

## Design goals

1. **Simple to deploy** — `docker compose up` on a bare Linux VM
2. **Simple to maintain** — by engineers of varying skill; hydrologists maintain models
3. **Resilient** — handles intermittent connectivity, late data, partial failures
4. **Transferable** — works for any hydromet with a new adapter module
5. **Low cost** — runs on modest hardware, minimal cloud spend
6. **Probabilistic by default** — all forecasts are ensembles or intervals
7. **API-first** — dashboard is optional; everything accessible via REST
8. **Hydrologist-centric** — designed around the operational forecaster's daily workflow
9. **Multi-resolution** — supports sub-daily through seasonal forecast horizons
10. **Auditable** — every data edit, forecast adjustment, and bulletin is logged
11. **Secure by default** — TLS everywhere, MFA, least-privilege access, encrypted backups

## Target scale

- Up to 500+ stations per deployment
- 25 years of daily historical data per gauge
- Up to 5 years of sub-daily data (10 min – 1 hr resolution)
- Growing data as the system runs operationally

## Timeline

| Date       | Milestone                                      |
|------------|-------------------------------------------------|
| 2026 H1    | v0: Swiss test data, model development, pipeline validation |
| Oct 2026   | v1.0: Testing begins with Nepal data            |
| Nov 2026   | First results demonstrated at hydromet visit    |
| Jan 2027   | Deploy on AWS, hydromet tests                   |
| Jan 2027   | Hydromet staff visit Switzerland for training    |
| Jan 2028   | Deploy on hydromet's own servers (if desired)   |

## Scope priorities

**v0 comprises three sub-phases** (v0a, v0b, v0c) that build incrementally.
The detailed design docs (01–08) use "v0" to refer to the full v0 scope.
The sub-phase breakdown below specifies which features are implemented in
each sub-phase:

- **v0a**: Core daily pipeline — CAMELS-CH + SMN + ICON-CH2-EPS + hydro_scraper adapters, daily models, full ingest→forecast→alert→API pipeline
- **v0b**: Sub-daily algorithm R&D — CAMELS generic adapter (DE/NZ/US), LSTM/transformer models, no operational deployment
- **v0c**: Swiss sub-daily operational validation — BAFU sub-daily sites, end-to-end sub-daily pipeline, staging on AWS

### v0a (2026 H1) — Swiss daily pipeline validation

- CAMELS-CH daily discharge + catchment weather for model training
- MeteoSwiss SMN hourly weather stations as supplemental forcing data
- ICON-CH2-EPS ensemble NWP (21 members: 20 perturbed + 1 control, 120h horizon) for operational weather forcing
- hydro_scraper for operational river gauge data (sub-daily)
- Daily forecast models (regression, persistence, possibly HBV)
- Full pipeline: ingest → forecast → alert → API
- NWP statistics archiving from day one
- No Nepal-specific features
- No security hardening

### v0b (2026 H1-H2) — Sub-daily algorithm testing

- CAMELS-DE, CAMELS-NZ, CAMELS-US sub-daily datasets
- Sub-daily forecast models (LSTM, transformer)
- Validates sub-daily code paths with real data
- No operational deployment — research/development phase

### v0c (2026 H2) — Swiss sub-daily validation

- 3 BAFU sites with requested sub-daily water level + discharge
- End-to-end sub-daily pipeline with Swiss operational data
- Validates the full sub-daily operational workflow
- Staging environment on AWS with Swiss data running continuously

### v1.0 (Oct 2026)

- Data ingest (adapters, QC, station management)
- Automated QC flags (range checks, rate-of-change)
- Historical data import tool
- Forecast pipeline (sub-daily and daily)
- Basic review dashboard
- Bulletin generation
- Flood alerts
- Observation-based alerting (not just forecast-based)
- Nepal time/date localization (Bikram Sambat calendar, NPT timezone)
- Station metadata managed in database (not TOML config files)
- Station model configuration managed in database (not TOML)
- Water level forecasting (discharge conversion deferred to v2.0)
- Security hardening (TLS, MFA, least-privilege, encrypted backups)
- Password policy + brute-force protection
- Docker secrets for production secrets management
- On-premise encryption at rest (LUKS)
- Integration test strategy (`docker-compose.test.yml`)

### v2.0 (post Jan 2027)

- Rating curve management UI
- Model skill views
- Scoped tokens for external data sharing
- Alert escalation (re-notify unacknowledged danger alerts after timeout)
- Basin-focused dashboard navigation (persistent basin selector)
- Host-level monitoring (disk space, Docker daemon, backup jobs)
- External uptime monitoring
- ON DELETE behavior for FKs documented
- Secret rotation policy (annual)
- Prefect exit strategy documented
- Virtual station support (ungauged sites with derived runoff — accessible via iEasyHydroHF API for current deployments; generic derivation Protocol for new hydromets)
- Discharge forecasting via rating curve conversion
- Rating curve uncertainty quantification
- Bulk data export endpoint for model training
- User management API endpoints (list users, deactivate users)

### v3.0 (post Jan 2028)

- Multi-resolution forecasting: pentadal (5-day), dekadal (10-day), monthly, seasonal models producing native output
- Seasonal forecasts with dedicated model interface
- Visual indication of edited observations on charts
- Ensemble spread adjustment in forecast review (widen/narrow uncertainty bands)
- Weather data API subscription cost tracking
- Gender/inclusion (i18n for Central Asia deployment — Russian, Kyrgyz, etc. — WCAG 2.1 AA, mobile responsiveness)
- Season definitions configurable per deployment (default: April–September for Central Asia)
- Open source governance (license, CONTRIBUTING.md, maintenance model)
- Measurable impact indicators (alert response time, forecast lead time, bulletin frequency)
- Data sharing agreements tracked alongside token issuance

## Open questions

- ~~**sapphire-sdk scope and governance**~~ — **Resolved**: For v1.0, all Protocols and domain types live inside SAPPHIRE_flow (in `src/sapphire_flow/protocols/` and `src/sapphire_flow/types/`). Extraction to a separate `sapphire-sdk` package is deferred until the model collaborator actively needs a shared dependency. This avoids multi-repo coordination overhead while there is only one consumer. The Protocol-based design means extraction is mechanical — move files, add a `pyproject.toml` — when the time comes.
- ~~**ON CONFLICT strategy for observation ingest**~~ — **Resolved**: uses `INSERT ... ON CONFLICT DO UPDATE` for value and quality_flag columns, handling both duplicate fetches and source corrections. See 02-data-model.md.
- ~~**forecast_values partitioning strategy**~~ — **Resolved**: `forecast_values` uses a denormalized `issued_at` column and is partitioned by monthly time range. UUIDv7-based range partitioning was rejected due to operational complexity. See 02-data-model.md.
- ~~**Flood threshold reference datums**~~ — **Resolved**: `FloodThreshold` now includes a `unit: str` field (e.g. `"m_gauge_zero"`, `"m_asl"`, `"m3s"`). Each adapter populates this from its source. The DB schema already had `unit_note TEXT`; the NamedTuple now matches. Which specific datum each external source uses is determined during adapter implementation (v0: BAFU; v1: Nepal DHM).
- ~~**Event-mode forecasting**~~ — **Deferred to v2.0**: NWP forecasts (ICON-CH2-EPS for v0, ECMWF IFS for v1) are available every 6 hours. Real-time rainfall data at higher frequency could refine forecasts between NWP cycles. Needs research into whether sub-cycle updates meaningfully improve lead time for the target catchments. Not blocking for v0/v1 — standard NWP-cycle-driven forecasting is sufficient for initial operational deployment.
- ~~**MeteoSwiss weather data format**~~ — **Resolved**: MeteoSwiss OGD provides ICON-CH2-EPS ensemble forecasts (21 members, 120h horizon, hourly, GRIB2) and ICON-CH1-EPS (11 members, 33h, hourly). Grid data only — adapter extracts nearest grid points via cfgrib + xarray. Files available for 24h after publication. No API key needed. See 03-adapters.md.
- ~~**v0 training data source**~~ — **Resolved**: Models train on SwissMetNet (SMN) station observations (hourly, 1981–present) co-located with river gauges. This avoids the daily-to-sub-daily temporal disaggregation problem with gridded climate data (RhiresD/TabsD). ICON-CH2-EPS forecasts are archived permanently from day one to build a hindcast archive for future bias correction. See 03-adapters.md.

## Pre-implementation refinement

Before entering detailed implementation planning, these design docs need a
refinement pass. The goal: **a junior programmer can read a section and know
exactly what to implement and how to verify that the implementation is correct.**

Areas that need refinement:

- ~~**Load testing strategy**~~ — **Resolved**: `make test-load` target defined
  in 08-testing-cicd.md. Simulates 500-station cycle (375K rows), 20 concurrent
  writers, measures P95 latency through PgBouncer. Acceptance criteria: <5 min
  write, <50ms P95 read, <150ms P95 read under write load.
- ~~**Chaos / failure mode testing**~~ — **Resolved**: 7 failure mode tests
  specified in 08-testing-cicd.md covering DB mid-write crash, worker crash
  recovery, adapter circuit breaker, stale cache fallback, partition missing
  with dead letter queue, stale alert flagging, and offline station alerts.
- ~~**JSONB schema validation**~~ — **Resolved**: Pydantic models defined for
  all 9 JSONB columns in `types-and-protocols.md` ("JSONB Boundary Schemas"
  section). Models: AccessTokenScope, StationMetadata, BasinMetadata,
  RatingCurveData, RatingCurveUncertainty, SkillMetrics, AuditDetail
  (discriminated union), EnsembleSnapshot. Implementation task 0a.14 added.
- **Upstream/downstream hydrological consistency** (v2.0): Post-forecast check
  that flags cases where a downstream station shows lower water levels than
  upstream during a rising limb. Even a dashboard warning would be valuable.
- ~~**MeteoSwiss adapter specifics**~~ — **Resolved**: Two adapters for v0:
  (1) `meteoswiss_nwp` fetches ICON-CH2-EPS ensemble forecasts (21 members,
  5-day horizon, hourly, GRIB2) via STAC API — grid-only, extracts nearest
  grid points, archives all data permanently for future bias correction.
  (2) `meteoswiss_smn` fetches SwissMetNet weather station observations
  (hourly, ~160 stations, CSV via OGD) for model training. See 03-adapters.md.

## Document index

- [01-architecture.md](01-architecture.md) — System architecture and tech stack
- [02-data-model.md](02-data-model.md) — Database schema and time series storage
- [03-adapters.md](03-adapters.md) — Data source adapter pattern
- [04-models.md](04-models.md) — Forecast model interface and collaboration strategy
- [05-flows.md](05-flows.md) — Prefect flows and scheduling
- [06-api.md](06-api.md) — REST API and optional dashboard
- [07-deployment.md](07-deployment.md) — Docker Compose and operations
- [08-testing-cicd.md](08-testing-cicd.md) — Testing strategy and CI/CD pipeline
- [design-fixes-plan.md](design-fixes-plan.md) — Design doc fixes plan from critical review
- [hydromet-qa-prep.md](hydromet-qa-prep.md) — Nepal DHM meeting preparation

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
| 1 | 2026-03-07 | design-reviewer, review-docs, review-domain | 9 | 14 | fixes-needed |
| 2 | 2026-03-07 | design-reviewer, review-docs, review-domain | 3 | 13 | fixes-needed |
| 3 | 2026-03-07 | design-reviewer, review-docs, review-domain | 1 | 5 | fixes-needed |
| 4 | 2026-03-07 | design-reviewer | 0 | 0 | fixes-needed |
