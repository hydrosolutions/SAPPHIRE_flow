---
status: DRAFT
created: 2026-07-21
plan: 136
title: BAFU LINDAS observation archive collector (all gauges, quarantined, sub-daily)
scope: A standalone quarantined collector that archives ALL BAFU gauges' real-time observations from LINDAS, decoupled from station onboarding. Swiss.
depends_on: []
blocks: []
---

# Plan 136 — BAFU LINDAS observation archive collector

## Status

**DRAFT.** Owner decision locked (2026-07-21, below). For `/plan` adversarial review before READY.

## Context — collect now, because LINDAS has no history

LINDAS (`lindas.admin.ch/foen/hydro`) serves BAFU river-gauge **observations** in **real time only** —
it carries **no historical time series** (Plan 111 Phase 0, confirmed 2026-07-08; memory
`project_bafu_lindas_realtime_only`). Today we ingest only the **2 onboarded operational stations**
(Porte_du_Scex, Rheinfelden-Messstation) via `flows/ingest_observations.py` → `hydro_scraper`
(`stations_polled=2`, verified live 2026-07-21). The full BAFU network (~170 gauges) publishes on the
same graph but we don't collect it.

Because there is no back-catalogue, **the archive can only be built forward** — so starting a full-network
collector **now** maximizes the eventual sub-daily series length. This is the same rationale, and the
same shape, as the Plan 111 **forecast** collector (`collect-bafu-forecasts`), and it is that plan's
**natural complement**: BAFU forecasts + BAFU observations together are exactly what the Plan 111
benchmark scores (forecast-vs-own-observation). This plan collects the observation half; it does **not**
build the scorer (Plan 111 G3).

## Decision (owner, 2026-07-21): quarantined collector, onboard later

**Quarantined "collect-now, import-later" archive** — a standalone collector that writes all gauges'
observations to a quarantined store keyed by **BAFU gauge code** (not `station_id`), **decoupled from
station onboarding**. **Rejected: onboarding all ~170 BAFU gauges** as real stations to route them through
`ingest-observations` into the `observations` table — heavier, entangles ~170 archive-only gauges with the
operational forecast set, and unnecessary to start the clock. Specific gauges are onboarded/imported from
the archive **when a real use** (training, benchmarking) needs them.

## Objective

Start a forward-only sub-daily observation archive for the full BAFU gauge network from LINDAS, with the
smallest safe machinery, mirroring the deployed-and-proven `collect-bafu-forecasts` collector — so that a
long sub-daily series accrues from today, importable later without re-collection.

## Non-goals

- **Not** onboarding BAFU gauges as stations, and **not** writing to the `observations` table (quarantine).
- **Not** touching the operational `ingest-observations` path (the 2 operational stations stay as-is).
- **Not** the benchmark/scorer — that is Plan 111 (G3); this is the observation-collection half only.
- **Not** backfill — LINDAS has no history; the archive is forward-only by nature.
- **Not** alerting/QC on these gauges — archive-only, evaluation-tier.

## Scope (to harden in `/plan`)

### 1. Fetch-all LINDAS query (`adapters/hydro_scraper.py` or a collector-local query)

`hydro_scraper._build_sparql_query` is **per-station** (`SELECT ?predicate ?object` over one station
node, keyed by `site_code`, `:173-194`), and `fetch_observations` loops per `StationConfig` (`:58-64`).
The collector needs a **single SPARQL query over the whole `foen/hydro` graph** that returns **every**
gauge node's current measurements (`discharge`, `waterLevel`, `waterTemperature`, `measurementTime` —
the dimensions the adapter already maps, `:37-49`) in one request — no per-station fan-out, no onboarded
station list. **`/plan` to specify** the exact query and confirm against the live graph what gauges +
parameters are exposed.

### 2. LINDAS returns a *latest snapshot*, not a window → cadence must match its update frequency

Verified: the query returns the **current** value per gauge (one `measurementTime`), not a time series —
so a low poll cadence would **drop** sub-daily points (gaps). The collector cadence must be **≥ the
LINDAS gauge update frequency** to capture the full sub-daily series. The operational ingest polls every
5 min (`*/5`) and captures its 2 stations cleanly, which bounds the answer. **`/plan` to probe the actual
BAFU/LINDAS update frequency** (typically ~10 min for level) and set the cadence to capture it without
gaps; dedup makes over-polling harmless. Record the chosen cadence + the measured update frequency.

### 3. The collector flow (`flows/collect_bafu_observations.py`) — mirror `collect-bafu-forecasts`

A `@flow(name="collect-bafu-observations")` mirroring the forecast collector's safeguards
(`flows/collect_bafu_forecasts.py`): dedup on `(gauge_code, parameter, timestamp)`; atomic temp+rename
writes; polite client (identifying `User-Agent`, request cap/retry); **quarantine** — writes **only**
under the configured archive path, **never** the DB / a `station_id`; **blank archive path ⇒ no-op**
(same guard as `DeploymentConfig.bafu_forecast_archive_path`).

### 4. Quarantined archive store

A parquet archive (mirror `bafu_forecast_archive` / `/data/bafu_forecasts`) — a new
`bafu_observation_archive` volume at `/data/bafu_observations`, keyed by gauge code + parameter +
timestamp, forward-only permanent retention (like the forecast archive). Not the `observations` table.
**`/plan` to decide** parquet layout (per-cycle vs per-gauge) and the dedup index.

### 5. Heartbeat (Flow 4 staleness hook)

One best-effort `pipeline_health` heartbeat per successful run (a new
`PipelineCheckType.BAFU_OBSERVATION_FRESHNESS`, mirroring `BAFU_FORECAST_FRESHNESS`), so the watchdog can
alert if this feed goes stale — same shape as the forecast collector's Flow 4 hook.

### 6. Deploy wiring (mirror Plan 111b)

`register_deployments.py` deployment `collect-bafu-observations` (cron per §2), `concurrency_limit=1`,
default pool; `docker-compose.yml` `bafu_observation_archive` volume; `config/overlays/mac-mini.toml`
`[adapters.bafu_observation].archive_base_path` as the enable switch (unset ⇒ no-op). A companion mac-mini
runbook (like 111b).

## Relationship to Plan 111

Plan 111 (BAFU forecast benchmarking) collects **forecasts** (`collect-bafu-forecasts`, merged) and is
gated on a licence for **publishing** the comparison. This plan collects the **observations** the
benchmark scores against — the two collectors together make the benchmark *possible*; publishing stays
under Plan 111 Gate G1. No licence is needed to *collect* public LINDAS data (same basis as the forecast
collector).

## Tests (to harden in `/plan`)

- Fetch-all query returns multiple distinct gauges (not one) from a faked/recorded LINDAS response.
- Dedup: a second run over the same snapshot archives zero new rows.
- Quarantine: blank `archive_base_path` ⇒ no-op (no writes, no DB touch); a set path writes only under it.
- Heartbeat: a successful run writes the `BAFU_OBSERVATION_FRESHNESS` pipeline_health record.
- No operational-path change: `ingest-observations` / the 2 operational stations are untouched.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

## Verification

- Unit tests above.
- Post-deploy (mac-mini, Swiss staging, when convenient): one run archives many gauges; a second run
  dedups; the heartbeat lands; the operational feeds (forecast collector + `ingest-observations`) are
  unaffected.

## References

- Plan 111 / 111b (the BAFU **forecast** collector — the pattern this mirrors; the benchmark this feeds).
- `adapters/hydro_scraper.py` (`_build_sparql_query` per-station `:173`, dimensions `:37-49`, graph `:24`).
- `flows/collect_bafu_forecasts.py` (quarantine/dedup/heartbeat pattern to mirror).
- `flows/ingest_observations.py` (the operational path this must NOT touch).
- memory `project_bafu_lindas_realtime_only` (no history → forward-only), `project_plan111_bafu_collector`.
