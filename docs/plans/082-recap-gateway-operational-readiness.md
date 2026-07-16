---
status: DRAFT
created: 2026-06-25
plan: 082
title: recap Gateway operational and training readiness
scope: Nepal v1 live Gateway readiness
depends_on:
  - 081-recap-dg-client-integration
  - 115a-weather-source-identity-schema
---

# Plan 082 - recap Gateway operational and training readiness

## Revision Log

- **2026-06-25:** Split live/operational readiness out of Plan 081.
- **2026-07-15 re-grounding (081 + 115a MERGED):** Rebuilt every task against merged
  code — `_select_nwp_source` retired; two adapters with in-adapter pre-filter/isolation;
  dict-return path needs no role/source filtering; homogeneity binding-validator removed;
  Task 2C split; coverage check made executable.
- **2026-07-16 owner-decision pass:** Applied settled forks — 082 ships a thin store-backed
  `GatewayPolygonResolver` + §5a mapping table (fixture-tested); Recap config/resolution/auth
  errors HARD-ABORT (write record + fatal `None`); snow-forecast path added now; watchdog
  REUSES `NWP_DELIVERY` with a reason field; selector reuses the existing config `type` key.
  Fixed dependency ordering (git-pin split), extended watchdog to Flow-6, made gates
  test-selector style, tightened secret/leakage/name gates.

## Status

This plan is **DRAFT**. Do not begin implementation and do not dispatch subagents until the
user promotes it to READY. Both dependencies (081, 115a) are merged.

## Objective

Make the merged Plan 081 Recap adapters operationally usable for Nepal v1: ship the store-backed
polygon resolver, wire the Flow-1 forecast provider and Flow-6 reanalysis provider to the Recap
adapters, make the `NWP_DELIVERY` watchdog discriminate where Recap errors actually surface (both
flows), add the snow-forecast path, define the model-input temporal join, and implement an
executable training-readiness coverage gate.

## Non-goals

- Do not implement the offline adapter foundation here; that is Plan 081 (merged).
- Do not change Swiss deployment behavior (MeteoSwiss forecast + reanalysis paths).
- Do not implement the full basin/static-package **validation/import** — that is **Plan 120**
  (basin/static importer, the importer the `04` contract §5a calls for; Plan 117 is docs-only and
  builds none). Split: **082 owns the minimal §5a mapping-table SCHEMA + the resolver that reads it**
  (Task 2D); **Plan 120 owns POPULATING that table** (package import/validation) — one schema owner,
  one write owner, no double-migration.
- Do not treat non-empty historical DataFrames as coverage readiness.
- Do not expose Gateway access to external SAP3 API consumers.
- Do not build an extended-outage circuit-breaker / auto kill-switch. Per-cycle hard-abort (Task
  2G) repeats every cycle during a long Gateway outage by design; suppressing the repeat alert and
  auto-disabling the provider belong to **Flow 4 pipeline monitoring** (deferred, pointer only).
- Do not introduce a dependency mechanism other than the ForecastInterface pattern (git-pin +
  scoped wheel-guard exception now, private-index wheel later).

## Context Read

- `CLAUDE.md`, `docs/workflow.md`, `docs/conventions.md`
- Plans 081, 115a; `docs/plans/117-basin-static-artifact-architecture.md` (the `04` contract/§5a); `docs/plans/120-basin-static-importer.md` (the §5a importer 082's resolver reads)
- `docs/requirements/01-data-gateway-requirements.md`
- `docs/requirements/04-basin-static-artifact-contract.md` §5a
- `docs/standards/{orchestration,logging,security,cicd}.md`
- `src/sapphire_flow/adapters/recap_gateway.py` (merged, 081)
- `src/sapphire_flow/flows/run_forecast_cycle.py`, `src/sapphire_flow/flows/ingest_weather_history.py`
- `src/sapphire_flow/services/operational_inputs.py`
- `src/sapphire_flow/types/enums.py`, `src/sapphire_flow/exceptions.py`
- `config.toml`, `docker-compose.yml`
- client clone `../recap-dg-client` (main, `60e5d73`): `recap_client/{ecmwf,snow,http}.py`, `README.md`, `tests/test_http_errors.py`

## What Plan 081 Actually Delivered (merged ground truth)

- **Two adapters** in `adapters/recap_gateway.py`: `RecapGatewayForecastAdapter`
  (`NWP_SOURCE="ifs_ecmwf"`, `fetch_forecasts(...) -> dict[StationId, WeatherForecastResult]`,
  `:482,:490,:501`) and `RecapGatewayReanalysisAdapter` (`NWP_SOURCE="era5_land"`,
  `fetch_reanalysis(...) -> list[RawHistoricalForcing]`, `:605,:613,:624`).
- **In-adapter pre-filter + isolation.** `_prefilter` (`:327-345`) drops wrong-source / wrong-role
  / inactive / non-`BASIN_AVERAGE` bindings; `_resolve_all` (`:377-391`) skips-and-logs resolver
  misses; `_validate_resolved_ref` (`:348-374`) enforces basin-average. The flow must NOT
  re-implement this.
- **Reserved error taxonomy** (all `AdapterError` subclasses, `exceptions.py:34`):
  `GatewayResolutionError` (all-unmappable, `:160`), `RecapDataUnavailableError`
  (`code=="source_data_missing"`, retriable, `:168`), `RecapConfigurationError` (`:176`).
  `_map_recap_error` (`:199-218`) discriminates **structurally** via `getattr(exc, ...)` — never
  `isinstance` on client classes, never a message-string match. There is **no** `RecapAuthError`
  yet (a 401/403 with no structured body falls to the generic `AdapterError` at `:218`); Task 2G
  adds it.
- **`GatewayPolygonResolver` Protocol** (`:112-114`). 081 provides the Protocol only; 082 provides
  the concrete store-backed impl + the mapping table (Task 2D).

## How the merged flow consumes the adapters (verify before wiring)

- **Flow-1 forecast selection** is `StationStore.fetch_forecast_binding(station_id)`
  (`run_forecast_cycle.py:1242`). `_select_nwp_source` is retired (comment `:81-84`).
- **Dict-return path.** `fetch_forecasts` returns a `dict`, so `_fetch_nwp_task` takes the
  per-station branch at `:876` (loop `:881`) — **no** role/source filtering (081 already filtered).
  It does not go through the `GriddedForecast` branch (`:767`, filter `:804-809`).
- **Phase A→B round-trip.** Records store under `nwp_source="ifs_ecmwf"` (adapter `:490,:555`);
  Phase B reads back with `forecast_bindings[sid].nwp_source` (`:1525`) via `fetch_weather_forecasts`
  (`operational_inputs.py:348-350`). **Onboarding invariant (config, not code): the FORECAST
  binding's `nwp_source` must equal `"ifs_ecmwf"`,** else Phase B logs `operational_inputs.no_nwp`
  (`:358`) and returns `None`.
- **Flow-6 reanalysis selection** keys on `adapter.NWP_SOURCE` via the `_ReanalysisAdapter`
  Protocol (`ingest_weather_history.py:66-78`, `NWP_SOURCE:str` `:70`), used `:304,:309`
  (`_reanalysis_sources` `:243-252`). Both merged adapters already expose `NWP_SOURCE`, so no
  Protocol change is needed.

## Operational Decisions (settled)

### Config selector — reuse the existing `type` key

`config.toml` already carries `[adapters.weather_forecast].type = "meteoswiss_nwp"` (`:378`) and
`[adapters.weather_reanalysis].type = "meteoswiss_open_data_reanalysis"` (`:406`), but
`_load_weather_forecast_adapter_config` never reads `type` onto `_WeatherForecastAdapterConfig`
(`run_forecast_cycle.py:102-118` — no such field today). Task 2C adds a `type: str` field to that
dataclass, threaded from the existing TOML key, as the single source of truth the Flow-1 dispatch
reads. New Recap values: `type = "recap_gateway"` (Flow-1) and the same under
`[adapters.weather_reanalysis]` (Flow-6). No new `provider` field.

### Recap error behavior — HARD-ABORT (not degrade)

On a `RecapConfigurationError`, `GatewayResolutionError` (all-unmappable), or `RecapAuthError`,
the flow writes the distinct `NWP_DELIVERY` health record AND returns the flow-fatal `None`
(aborting the cycle). It does **not** degrade to runoff-only. Only `RecapDataUnavailableError`
(`source_data_missing`) follows the retriable `NoCycleAvailableError`-style path (runoff-only for
this cycle). Details in Task 2G.

### Watchdog mechanism — reuse `NWP_DELIVERY`

Discrimination uses the existing `PipelineCheckType.NWP_DELIVERY` (`enums.py:152`) with a
`detail.reason` category + distinct `subject` + WARNING/CRITICAL status. No new enum values.

### Ensemble contract

`member_id=0` (HRES `fc`, no `member`) + `member_id=1..50` (`pf`), inherited from 081 (adapter
`:194,:434`). `ecmwf.ifs_forecast` carries `run_hour`/`member` (clone `ecmwf.py:62-63`); the
adapter passes `run_hour=cycle_time.hour` (`:581`). `cf` is discontinued; do not depend on it.

### Coverage and training readiness

Gateway exposes no coverage metadata and does not flag gaps. Coverage is a fully SAP3-side gate:
a supervised manifest + two executable checks (Task 3B). Leakage guard uses the client's per-row
`source` column: observed = `era5_land` / `jsnow_reanalysis`; forecast-fill = `ifs` /
`jsnow_forecast` (clone `README.md:102-105`). Forecast-fill rows must be dropped from reanalysis
admission.

### Temporal reconciliation

Native valid times preserved (IFS 3-hourly→144 h then 6-hourly→~360 h; ERA5-Land hourly; snow
daily). Daily deterministic snow is broadcast across NWP member ids at model-input assembly, never
resampled inside the adapter; persisted snow records stay `member_id=None` (adapter `:707`).

## Implementation Phases

### Phase 1 - Live Marker and Gateway Smoke Tests

#### Task 1A - Register the Recap live marker

**Scope in:** Add `live_recap` to `pyproject.toml` markers. **Scope out:** No default-CI network
calls; no addopts change (default `not live` already covers `live_recap` since the tests carry both
markers).

**Verification** — a unit test proves marker registration AND discrimination:
- `live_recap` is registered in `pyproject.toml` markers (structural TOML parse, not a regex match).
- collecting the live suite under the default expression yields **zero** tests; under
  `'live and live_recap'` yields >0.
- negative controls proving the guard discriminates: an expression of bare `live` must still be
  excluded by default, and `not live_lindas` must NOT accidentally admit `live_recap` tests.

```bash
uv run pytest tests/unit/tooling/test_live_recap_marker.py::TestLiveRecapMarker
```

#### Task 1B - Add operational live smoke tests

**Scope in:** `tests/integration/live/test_recap_gateway_live.py`, marked `live` + `live_recap`,
skipping when `RECAP_API_KEY` is absent. Cover `fc`/`pf member=1` shape, member-bound rejections
(0/51), precip/temperature range after conversion, snow endpoint shape (`hs`/`rof`/`swe`), the
Task 1C shapefile's `g_<...>` column echo + one-column-per-band behavior, and the
`source`/`source_run` provenance columns. **Scope out:** Not part of default `uv run pytest`.
**Depends on 2H-dep** (real client installed).

**Verification:**

```bash
uv run pytest tests/integration/live/test_recap_gateway_live.py --collect-only -m 'live and live_recap'
RECAP_API_KEY=... uv run pytest tests/integration/live/test_recap_gateway_live.py -m 'live and live_recap' -v
```

#### Task 1C - Produce and PROVE a SAP3-compliant Gateway test GeoPackage

**Scope in:** Produce a small `.gpkg` with lowercase `g_<...>` feature names (no leading `0`),
≥1 banded basin, register it on the Gateway via manual upload, and record HRU + per-polygon names
as a JSON fixture. **Scope out:** Not the production export pipeline.

**Verification** — an offline test opens the `.gpkg` with `geopandas`/`fiona` and asserts: ≥1 layer;
polygon geometry; every feature `name` lowercase and not starting with `0`; ≥1 feature matching
`g_.*_band_\d+`; and the JSON fixture's names exactly equal the layer names read from the file
(fails on a missing/empty/invalid gpkg or fixture drift).

```bash
uv run pytest tests/integration/live/test_recap_compliant_gpkg.py::TestCompliantGeoPackage
```

### Phase 2 - Nepal Wiring, Resolver, Dispatch, Watchdog

#### Task 2H-dep - Git-pin recap-dg-client (dependency only) — SEQUENCED FIRST

**Scope in:** Add `recap-dg-client` as a rev-pinned git dependency in `pyproject.toml`
`[project.dependencies]` + `[tool.uv.sources]`, and update `uv.lock`. **Only** the dependency —
no CI wheel-guard, no Docker auth (Task 2H). This unblocks 2A/2D imports of `recap_client`.
**Scope out:** No CI/Docker changes here.

**Verification** — a test asserts the **exact normalized name** `recap-dg-client` appears as a key
in both `[project.dependencies]` (parsed requirement name) and `[tool.uv.sources]`, and that the
uv.lock records a git pin (rev). No `'recap' in text` substring check.

```bash
uv run pytest tests/unit/tooling/test_recap_dependency_pin.py::TestGitPin
```

#### Task 2A - Wire Nepal Recap configuration + API-key secret plumbing

**Scope in:** Nepal deployment config for Recap base URL, API-key secret, timeout/TLS, SAP3-side
retry policy, Gateway HRU-metadata source, staleness threshold. Add a `load_recap_api_key()` helper
that reads the secret and threads it into the client's `ApiClientConfig`. Add the Docker Compose
secret wiring: a top-level `secrets.sapphire_dg_api_key.file` entry and a `sapphire_dg_api_key`
entry under `services.prefect-worker.secrets` (and the ingest worker for Flow-6) — today
`docker-compose.yml` declares only `db_password` (`:300-302`, worker secrets `:97-98`).
**Scope out:** Do not enable Recap in Swiss profiles; never log/return the key. **Depends on 2H-dep.**

**Secret gate** — two tests, both discriminating:
- Config plumbing: with `RECAP_API_KEY` set, the built `ApiClientConfig.api_key` equals the exact
  secret; with it unset the helper raises/skip-guards.
- Compose artifact (YAML parse, not substring): `services.prefect-worker.secrets` includes
  `sapphire_dg_api_key` AND top-level `secrets.sapphire_dg_api_key.file` is declared. Fails against
  today's `db_password`-only compose.

```bash
uv run pytest tests/unit/config/test_recap_gateway_config.py tests/unit/deploy/test_compose_recap_secret.py
```

#### Task 2B - Resolve latest available Gateway cycle

**Scope in:** Probe candidate IFS `run_date`/`run_hour`, treating a `RecapDataUnavailableError`
(`source_data_missing`) as candidate-unavailable, stopping at configured max age.
**Scope out:** No Gateway health API.

**Verification** — a fake client returning `source_data_missing` for the newest N candidates then
data → resolver returns the first available older cycle; all-missing within max age → returns the
unavailable signal.

```bash
uv run pytest tests/unit/adapters/test_recap_gateway_cycle_resolution.py
```

#### Task 2C - Branch Flow-1 config validation on the `type` selector (BLOCKER)

**Scope in:** Add a `type: str` field to `_WeatherForecastAdapterConfig` (`run_forecast_cycle.py:102-118`),
read from `[adapters.weather_forecast].type` (existing key, `config.toml:378`). Make the
MeteoSwiss-field requirement in `_load_weather_forecast_adapter_config` (`:253-268`) fire **only**
when `type == "meteoswiss_nwp"`; for `type == "recap_gateway"` validate the Recap fields instead.
This single field is the source of truth Task 2D dispatches on. **Scope out:** No behavior change
for the MeteoSwiss `type`.

**Verification (fails before, passes after):** `type="recap_gateway"` + no MeteoSwiss fields → no
`ConfigurationError`; `type="meteoswiss_nwp"` + missing MeteoSwiss field → `ConfigurationError`
still raised.

```bash
uv run pytest tests/unit/flows/test_run_forecast_cycle.py::TestWeatherForecastConfigTypeBranch
```

#### Task 2D - Store-backed resolver + §5a mapping table + Flow-1 adapter dispatch (BLOCKER)

**Scope in, three coupled parts:**

1. **§5a additive mapping table** keyed by `station_id + gateway_hru_name + name`, columns
   `station_id, basin_id, gateway_hru_name, name, spatial_type, band_id` (contract
   `04-basin-static-artifact-contract.md:291-310`). Additive migration; does not touch the `basins`
   table.
2. **Store-backed `GatewayPolygonResolver`** that reads that table and returns a `GatewayPolygonRef`
   (satisfies the 081 Protocol, `recap_gateway.py:112-114`), unit-tested against a **fixture** (no
   real GeoPackage needed to build/verify).
3. **Flow-1 dispatch branch.** In `if adapter is None:` (`run_forecast_cycle.py:1063`, today only
   `MeteoSwissNwpAdapter` at `:1096`), add a `type == "recap_gateway"` branch constructing
   `RecapGatewayForecastAdapter(client=<from 2A>, resolver=<store-backed>)`.

**Prerequisite note (production readiness, not build):** the table is *populated* by an accepted
basin/static package via the **Plan 120** importer (the §5a persistence plan). 082 builds and
unit-tests the table + resolver against a fixture; a real Nepal production run additionally requires
Plan 120 landed and an accepted package populating the table.

**Scope out:** No package validation/import (Plan 120). **Depends on 2C, 2A, 2B, 2H-dep.**

**Verification:** resolver returns the right ref for a seeded fixture row and `None` for an unmapped
station; with `type="recap_gateway"` + injected fake client, the flow builds a
`RecapGatewayForecastAdapter` (not `MeteoSwissNwpAdapter`) and the dict path stores under
`nwp_source="ifs_ecmwf"`.

```bash
uv run pytest tests/unit/adapters/test_gateway_polygon_resolver.py tests/unit/flows/test_run_forecast_cycle.py::TestRecapForecastDispatch
```

#### Task 2E - Flow-6 reanalysis dispatch: `RecapGatewayReanalysisAdapter` (BLOCKER)

**Scope in:** In `build_production_reanalysis_adapter` (`ingest_weather_history.py:168-202`, call
site `:277-292`), add a branch keyed on `[adapters.weather_reanalysis].type == "recap_gateway"`
constructing `RecapGatewayReanalysisAdapter(client=..., resolver=<store-backed, 2D>)`. Selection
already works via `NWP_SOURCE="era5_land"` / `_reanalysis_sources` (`:243-252,:309`) — no Protocol
change. **Scope out:** Keep the MeteoSwiss reanalysis default unchanged. **Depends on 2C, 2A, 2D.**

**Verification:** `type="recap_gateway"` → factory returns a `RecapGatewayReanalysisAdapter`
(`NWP_SOURCE=="era5_land"`); default → `MeteoSwissOpenDataReanalysisAdapter`.

```bash
uv run pytest tests/unit/flows/test_ingest_weather_history.py::TestRecapReanalysisDispatch
```

#### Task 2G - `NWP_DELIVERY` watchdog discrimination — both flows (BLOCKER)

**Scope in, three parts:**

1. **Flow-1 recap error categorization + HARD-ABORT.** In `_fetch_nwp_task`, add named `except`
   clauses ahead of the catch-all `except Exception` (`run_forecast_cycle.py:762`), each writing a
   distinct `NWP_DELIVERY` record via `_append_pipeline_health_record`, then:
   - `RecapConfigurationError` → **CRITICAL**, `detail.reason="config_error"` (carry `field`) → return `None` (fatal abort).
   - `GatewayResolutionError` → **CRITICAL**, `detail.reason="all_unmappable"` → return `None` (fatal abort). *(Category previously OMITTED.)*
   - `RecapAuthError` → **CRITICAL**, `detail.reason="auth"` → return `None` (fatal abort). Add `RecapAuthError(AdapterError)` to `adapters/recap_gateway.py` and map `getattr(exc, "status_code", None) in (401, 403)` in `_map_recap_error` (`:199-218`); the client carries `ApiRequestError.status_code` (clone `http.py:28`).
   - `RecapDataUnavailableError` → **WARNING**, `detail.reason="source_data_missing"` → degrade to runoff-only (`return _NwpFetchOutcome(..., nwp_unavailable=True)`, matching the `NoCycleAvailableError` precedent at `:754-761`).
2. **Source-aware grid staleness.** `_check_nwp_grid_staleness` (`:544-582`) keys on
   `_ICON_NWP_SOURCE="icon_ch2_eps"` (`:84,:556`); on an IFS-only Nepal deploy
   `fetch_latest_cycle_time("icon_ch2_eps")` returns `None` every cycle → permanent CRITICAL false
   alarm (`:573-581`). Parameterize on the active forecast source string (`"ifs_ecmwf"` for Recap)
   so it detects a genuinely stale gateway cycle over `weather_forecasts`; keep `"icon_ch2_eps"` for
   MeteoSwiss. Do not point at the Zarr grid archive.
3. **Flow-6 reanalysis categorization.** Recap `AdapterError`s raised from `_fetch_reanalysis_task`
   (`ingest_weather_history.py:215-222`, called `:318`) currently propagate with no health record
   (the flow has no `pipeline_health_store`). Thread a `pipeline_health_store` into
   `ingest_weather_history_flow` and categorize the same way, writing the distinct `NWP_DELIVERY`
   record then re-raising (Flow-6 has no runoff-only fallback; a config/resolution/auth failure
   fails the ingest flow).

**Scope out:** Do not collapse all errors into stale delivery; do not disable the staleness check.

**Verification** — per category, exactly one `NWP_DELIVERY` record with the expected status +
`detail.reason`; config/resolution/auth assert the **fatal `None`** outcome (Flow-1) / re-raise
(Flow-6), `source_data_missing` asserts runoff-only. Staleness negative: Recap provider + fresh
`ifs_ecmwf` cycle + no `icon_ch2_eps` rows → no CRITICAL. Staleness positive control: MeteoSwiss
provider + old `icon_ch2_eps` cycle → still CRITICAL.

```bash
uv run pytest tests/unit/flows/test_run_forecast_cycle.py::TestRecapNwpDeliveryWatchdog tests/unit/flows/test_ingest_weather_history.py::TestReanalysisNwpDeliveryWatchdog tests/integration/store/test_pipeline_health_store.py
```

#### Task 2H-snow - Snow-forecast fetch path + temporal model-input join

**Scope in, two parts:**

1. **Snow-forecast fetch path.** Widen `SnowApiLike` (`recap_gateway.py:141-151`, today only
   `reanalysis`) with `forecast(*, hru_code, variable, run_date, run_hour: int, ...)` matching the
   client (`../recap-dg-client/recap_client/snow.py:63-86`, `run_hour:int` default 0, 0/6/12/18),
   and add a deterministic snow-forecast fetch to `RecapGatewayForecastAdapter` producing
   `member_id=None` snow rows. A fake-client test asserts `run_hour` is **SENT** (captured in the
   fake's recorded call kwargs) — the client returns no `run_hour` echo, so do not assert "echoed".
2. **Temporal join.** Implement/test the daily-snow → sub-daily 51-member IFS broadcast in the
   model-input service; no resample/broadcast inside the adapter.

**Scope out:** No aggregation inside the adapter. **Depends on 2D.**

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestSnowForecastFetch tests/unit/services/test_operational_inputs.py::TestRecapTemporalFeatureJoin
```

#### Task 2H - Recap dependency CI wheel-guard + Docker builder auth

**Scope in:** The scoped two-step CI wheel-guard exception (Plan 079-style) in
`.github/workflows/ci.yml`, private-repo clone auth for CI and the Docker builder stage, and the
removal-trigger doc. Update `docs/standards/{security,cicd}.md`. **Scope out:** No private-index
wheel migration (deferred); do not loosen the wheel-guard for any other package. **Depends on 2H-dep.**

**Removal trigger (concrete):** remove the git-pin + wheel-guard exception once `recap-dg-client` is
published to the hydrosolutions private package index and CI/Docker can install it as a wheel with
no source build. **Owner:** IT specialist; tracked as a Plan 080-style follow-up.

**Verification** — a test parses `ci.yml` and asserts the wheel-guard has the scoped `no-build`
exception naming exactly `recap-dg-client`, and the Docker builder stage declares the clone-auth
step. Prose assertions, single selector.

```bash
uv run pytest tests/unit/tooling/test_recap_wheel_guard.py::TestWheelGuardException
```

### Phase 3 - Coverage Gate and Training Readiness

#### Task 3A - Add Gateway coverage manifest model

**Scope in:** A SAP3-side coverage representation keyed by
`(gateway_hru_name, name, dataset, variable, band_id)` → covered span, from a supervised manifest.
**member_id is deliberately NOT part of the key** — coverage is member-agnostic (all ensemble
members of a cycle share its span). **Scope out:** No inference from non-empty DataFrames.

**Verification** — `TestGatewayCoverageManifest` MUST assert (discriminating, not a smoke test):
(a) a well-formed manifest row round-trips to the frozen model with the exact 5-tuple key and its
covered span; (b) a manifest that OMITS a required key field (e.g. `band_id` for a band row, or
`variable`) is REJECTED at construction (raises), not silently accepted; (c) the model exposes NO
constructor path that derives a span from row counts / non-empty data (member-agnostic + no
inference — a fixture with data but no declared span yields no coverage).

```bash
uv run pytest tests/unit/services/test_gateway_coverage_gate.py::TestGatewayCoverageManifest
```

#### Task 3B - Executable coverage gate + span check + leakage guard + backfill window (BLOCKER)

**Scope in:**

1. **`coverage_spans_window(manifest, requested_window, required_keys) -> bool`** — refuse Flow-6
   training unless the covered span contains the requested window for every required key
   `(gateway_hru_name, name, dataset, variable, band_id)`. A required key **absent** from the
   manifest is treated as no-coverage (refuse).
   This is a **training-readiness** gate, and its consequence is enforced by the *model lifecycle*,
   not by a second operational check: a model that cannot be trained (coverage refused) produces no
   artifact → gets no `ACTIVE` assignment → **never runs operationally** by construction. So there
   is no separate "operational training-coverage" gate. The gate produces a **signal**, not an
   irreversible block: a **head hydrologist retains the existing manual-promotion authority**
   (`promote_artifact`, `ModelArtifactStatus.PENDING_APPROVAL`,
   `services/model_onboarding.py:1445-1447`) to declare a model operational despite short
   auto-coverage.
2. **`assert_returned_span_covers_request(requested, returned)`** — HARD-BLOCK (raise) when the
   returned data span is shorter than requested. **Training hard-blocks. Operational forecast
   fetches log WARNING and continue** (a short horizon is still usable), consistent with the
   existing per-station graceful `operational_inputs.no_nwp` path (`:358`). *(Closed at this
   code-grounded default; see Residual forks.)*
3. **Leakage guard.** Drop client per-row `source ∈ {ifs, jsnow_forecast}` from reanalysis
   admission, admitting only `era5_land` / `jsnow_reanalysis` (clone `README.md:102-105`). The
   guard reads the client `source` column before the adapter strips provenance
   (`recap_gateway._split_provenance:228-242`).
4. **Parametric backfill window.** `ingest_weather_history` is hardcoded to 60 days
   (`_WINDOW_DAYS=60`, `:51`, used `:300`). Add explicit `start`/`end` (or `window_days`) params for
   multi-year Nepal history; keep the Swiss 60-day default unchanged.

**Scope out:** No automated chunked-backfill orchestration.

**Verification (discriminating):** window inside covered span → `True`; window one day past →
`False` (training refused); a required key missing from the manifest → refused. Span check: returned
short of requested → raises (training) / WARNS+continues (operational). Leakage: a snow frame with a
`jsnow_forecast` row → that row dropped, `jsnow_reanalysis` admitted. Backfill: `window_days=730` →
`start == now - 730d`; default unchanged at 60d.

```bash
uv run pytest tests/unit/services/test_gateway_coverage_gate.py::TestGatewayCoverageGate tests/unit/flows/test_train_models.py::TestGatewayCoverageTrainingGate tests/unit/adapters/test_recap_gateway.py::TestReanalysisLeakageGuard tests/unit/flows/test_ingest_weather_history.py::TestParametricBackfillWindow
```

### Phase 4 - Gateway Operations Runbook

#### Task 4A - Document Gateway operational procedures

**Scope in:** `docs/operations/recap-gateway-runbook.md` covering manual gpkg upload, historical
back-extraction tied to the coverage manifest, coverage-manifest recording, live smoke execution,
`NWP_DELIVERY` triage (the four `detail.reason` categories from 2G), API-key handling, and
snow-variable status. **Scope out:** Do not document upstream client fixes as complete.

**Verification** — a doc test asserts the runbook contains the required operator anchors
(`RECAP_API_KEY`, coverage manifest, historical back-extraction, the four `NWP_DELIVERY` reasons,
`live_recap`, snow) as headings/sections, not a bare substring scan.

```bash
uv run pytest tests/unit/docs/test_recap_runbook.py::TestRunbookSections
```

## Whole-Plan Exit Gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

Confirm live Recap tests are marker-gated and skipped from defaults:

```bash
uv run pytest tests/integration/live/test_recap_gateway_live.py --collect-only -m 'not live'   # zero collected
RECAP_API_KEY=... uv run pytest tests/integration/live/test_recap_gateway_live.py -m 'live and live_recap' -v   # credentialed only
```

## Resolved Gateway Questions (traceability)

1. String feature `name`s ARE echoed as columns; no leading `0` (`g_<...>` OK).
2. A banded gpkg returns one column per band polygon (validated by 1B/1C).
3. Coverage: no Gateway metadata; SAP3 supervised manifest (Phase 3).
4. Snow: `hs`=height, `rof`=snowmelt, `swe`=SWE.
5. No latest-cycle endpoint; SAP3 probes candidates (2B).
6. No concurrency limit.
7. `cf` discontinued; `fc` HRES is the control.

**Client caveats (verified against `../recap-dg-client` main `60e5d73`):**

- `unsupported_shapefile` is UNVERIFIED — the clone only demonstrates `unsupported_parameter`
  (`tests/test_http_errors.py:76`) and `source_data_missing` (`:118`). Discriminate off the
  structured error **type/attributes** (`ApiValidationError` → `RecapConfigurationError` via
  `getattr` code/field/supported_values), never off the string.
- **No client retry/backoff:** `get_parquet_df` issues a bare `self._session.get(...)` on a
  `requests.Session` (`recap_client/http.py:167,:192`). Retry is SAP3-side (Prefect task retries).
- **Back-extraction uses `reanalysis`, not gap-fill.** `ifs_gap_fill` (`ecmwf.py:120`) and
  `snow.gap_fill` (`snow.py:113`) exist but target operational gap-filling against a known window;
  historical training back-extraction needs the full leakage-free observed series, so the adapter
  uses `era5_land_reanalysis` (`ecmwf.py:29`) / `snow.reanalysis` (`snow.py:36`)
  (`recap_gateway.py:669,:681`).

## Risks and Recommendation

| Risk | Impact | Mitigation |
|---|---|---|
| No Gateway coverage metadata | Flow 6 could train on truncated history. | Executable manifest gate + span check + leakage guard (3B). |
| Banded HRU behavior unconfirmed | Banded models get incomplete features. | Credentialed banded live smoke (1B/1C). |
| 51 calls per variable/HRU/cycle | Slow/failure-prone cycles. | No Gateway concurrency limit; parallel fetch + SAP3-side Prefect retries (client has none). |
| Recap errors as fatal `None` with no record | Silent abort; permanent false CRITICAL on IFS-only cycles. | Task 2G: categorize before catch-all (both flows) + source-aware staleness. |
| Long Gateway outage repeats hard-abort every cycle | Alert noise. | Circuit-breaker/kill-switch deferred to Flow 4 pipeline monitoring (Non-goals). |
| Table populated only by future importer | Resolver returns `None` for every station until then. | Fixture-tested now; production readiness gated on Plan 120 + accepted package (2D note). |

Recommendation: **do not promote to READY until Plan 120 (the §5a importer) is sequenced** and the
the span-check design below is confirmed by the owner.

## Residual OWNER DECISIONs

- **Coverage = training-readiness gate + manual override — RESOLVED (owner, 2026-07-16).** Short
  training-history coverage refuses *automatic* training/promotion; an untrainable model never
  reaches operation *by construction* (no artifact → no assignment), so there is no separate
  "operational training-coverage" gate. A head hydrologist can manually promote despite short
  coverage (existing promotion authority — Task 3B item 1). The **distinct per-cycle returned-span
  check** (3B item 2) hard-blocks *training* but only WARNs+continues *operationally* — a short
  forecast **horizon** is still usable — matching the existing `operational_inputs.no_nwp` graceful
  path (`:358`). Confirmed at that default. *(No residual span-check fork remains.)*

All other forks are settled: resolver (082 ships store-backed + §5a table, importer = Plan 120);
error behavior (hard-abort); watchdog mechanism (reuse `NWP_DELIVERY` + reason); selector (existing
`type` key); snow-forecast (added now).

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-0",
      "name": "Dependency (git-pin recap-dg-client) — sequenced first",
      "tasks": ["2H-dep"],
      "parallel": false,
      "depends_on": ["plan-081", "plan-115a"]
    },
    {
      "id": "phase-1",
      "name": "Live marker and Gateway smoke tests",
      "tasks": ["1A", "1B", "1C"],
      "parallel": false,
      "depends_on": ["phase-0"]
    },
    {
      "id": "phase-2",
      "name": "Nepal wiring, resolver, dispatch, watchdog",
      "tasks": ["2A", "2B", "2C", "2D", "2E", "2G", "2H-snow", "2H"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "name": "Coverage gate and training readiness",
      "tasks": ["3A", "3B"],
      "parallel": false,
      "depends_on": ["phase-2"]
    },
    {
      "id": "phase-4",
      "name": "Gateway operations runbook",
      "tasks": ["4A"],
      "parallel": false,
      "depends_on": ["phase-3"]
    }
  ],
  "task_dependencies": {
    "1B": ["1A", "1C", "2H-dep"],
    "2A": ["2H-dep"],
    "2D": ["2C", "2A", "2B", "2H-dep"],
    "2E": ["2C", "2A", "2D"],
    "2G": ["2D", "2E"],
    "2H-snow": ["2D"],
    "2H": ["2H-dep"],
    "3B": ["3A"]
  }
}
```
