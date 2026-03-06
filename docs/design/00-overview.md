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

### v0 (2026 H1) — Swiss development phase

Internal development and validation using Swiss public data. Not deployed
to any hydromet. Purpose: validate the full pipeline end-to-end, develop
and test model integration, and build confidence before Nepal deployment.

- Full pipeline: ingest → forecast → alert → API, running on Swiss reference stations
- Weather data from MeteoSwiss open data API (not sapphire-dg — no API key needed)
- Station data from BAFU/FOEN via hydro_scraper adapter (already planned)
- Model development by the lead developer using Swiss data
- Simple models only (linear regression, persistence, possibly HBV via pydrology)
- No Nepal-specific features (no Bikram Sambat, no bulletin generation)
- No security hardening (local development, no TLS/MFA)
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
- **Flood threshold reference datums** — Thresholds may be in different units/datums (m a.s.l. vs m above gauge zero vs m³/s). Need to clarify with hydromet. See 02-data-model.md and 06-api.md.
- **Event-mode forecasting** — ECMWF forecasts available every 6 hours. Real-time rainfall data at higher frequency could refine forecasts between ECMWF cycles. Needs research. See 05-flows.md.
- **MeteoSwiss weather data format** — The MeteoSwiss open data API is used for v0 (Swiss development phase). Unknown whether it provides ensemble forecasts or only deterministic output. If deterministic, the adapter wraps it as a single-member ensemble. Investigate during v0 implementation. See 03-adapters.md.

## Pre-implementation refinement

Before entering detailed implementation planning, these design docs need a
refinement pass. The goal: **a junior programmer can read a section and know
exactly what to implement and how to verify that the implementation is correct.**

Areas that need refinement:

- **Load testing strategy**: Define a `make test-load` target that simulates a
  500-station forecast cycle (382K rows written to `forecast_values` per run)
  and measures write throughput + query latency through PgBouncer. Acceptance
  criteria: full cycle completes within N minutes, P95 query latency under M ms.
  See 08-testing-cicd.md.
- **Chaos / failure mode testing**: The failure modes in 05-flows.md are described
  but have no corresponding tests. Add integration tests that simulate: DB killed
  mid-write, worker killed mid-forecast, adapter timeout, partition missing. One
  test per failure mode minimum.
- **JSONB schema validation**: Define Pydantic models for all JSONB fields
  (`access_tokens.scope`, `stations.metadata`, `rating_curves.data`,
  `rating_curves.uncertainty`, `model_skill.metrics`). Validate at the API
  boundary. Document the expected JSON structure for each field.
- **Upstream/downstream hydrological consistency** (v2.0): Post-forecast check
  that flags cases where a downstream station shows lower water levels than
  upstream during a rising limb. Even a dashboard warning would be valuable.
- **MeteoSwiss adapter specifics**: Determine API endpoints, data format,
  temporal resolution, and ensemble availability during v0 implementation.

## Document index

- [01-architecture.md](01-architecture.md) — System architecture and tech stack
- [02-data-model.md](02-data-model.md) — Database schema and time series storage
- [03-adapters.md](03-adapters.md) — Data source adapter pattern
- [04-models.md](04-models.md) — Forecast model interface and collaboration strategy
- [05-flows.md](05-flows.md) — Prefect flows and scheduling
- [06-api.md](06-api.md) — REST API and optional dashboard
- [07-deployment.md](07-deployment.md) — Docker Compose and operations
- [08-testing-cicd.md](08-testing-cicd.md) — Testing strategy and CI/CD pipeline
- [hydromet-qa-prep.md](hydromet-qa-prep.md) — Nepal DHM meeting preparation
