---
status: DRAFT
created: 2026-06-25
plan: 082
title: recap Gateway operational and training readiness
scope: Nepal v1 live Gateway readiness
depends_on:
  - 081-recap-dg-client-integration
  - 115-weather-source-identity-model
---

# Plan 082 - recap Gateway operational and training readiness

## Revision Log

- **2026-06-25 adversarial-review revision:** created this plan by splitting
  Gateway-dependent operational/training-readiness work out of the original Plan
  081. This clears review BLOCKERs 1-4, MAJORs 5-7, and the omissions by keeping
  Plan 081 offline-completable while moving live smoke, Nepal wiring, latest
  cycle/watchdog, coverage readiness, temporal model-input reconciliation, and
  runbooks here.
- **Ensemble control inherited from Plan 081:** `member_id=0` is Gateway `fc`
  HRES, which is the current ECMWF deterministic control after ECMWF discontinued
  the ENS control (`cf`); `member_id=1..50` are `pf` perturbed members. G7-compliant.
  Preserve this convention in live smoke and watchdog checks.
- **Coverage remains the highest-risk item:** missing Gateway coverage metadata
  is a hard training blocker in this plan, not a blocker for Plan 081 adapter
  readiness.
- **2026-06-25 Gateway-dev sync:** Gateway-dev agenda closed. Coverage is a **fully
  SAP3-side** gate — the Gateway returns only what's available and does not flag gaps
  or expose coverage metadata, so the supervised manifest is the mechanism (not a
  fallback). String column keys are supported (constraint: `name` must not start with
  `0`), so this plan adds a compliant **test-shapefile** task and validates echo/banded
  behavior via live smoke. The Gateway dev confirmed **no concurrency limit** to design
  around. Distribution follows the ForecastInterface git-pin pattern; this plan owns
  the committed git-pin + CI wheel-guard exception (private-repo clone auth).

## Status

This plan is **DRAFT**. Do not begin implementation and do not dispatch
subagents until the user promotes it to READY. This plan depends on Plan 081.

## Objective

Make the Plan 081 Recap adapter operationally usable for Nepal v1 by closing
Gateway-side live contract questions, wiring Nepal configuration, resolving the
latest available cycle and `NWP_DELIVERY` watchdog semantics, defining the
model-input temporal join policy, and implementing the training-readiness
coverage gate.

## Non-goals

- Do not implement the offline adapter foundation here; that is Plan 081.
- Do not change Swiss deployment behavior.
- Do not use `ecmwf.operational()`; endpoint provenance remains required.
- Do not treat non-empty historical DataFrames as coverage readiness.
- Do not expose Gateway access to external SAP3 API consumers.
- Do not introduce a dependency mechanism other than the ForecastInterface pattern
  (git-pin + scoped wheel-guard exception now, private-index wheel later). The
  committed git-pin lands in this plan (Task 2E).

## Context Read

- `CLAUDE.md`
- `docs/workflow.md`
- Plan 081: `docs/plans/081-recap-dg-client-integration.md`
- `docs/requirements/01-data-gateway-requirements.md`
- `docs/requirements/00-internal-gap-analysis.md`
- `docs/v0-scope.md` section I
- `docs/conventions.md`
- `docs/standards/orchestration.md`
- `docs/standards/logging.md`
- `docs/standards/security.md`
- `src/sapphire_flow/flows/run_forecast_cycle.py`
- `src/sapphire_flow/types/pipeline.py`
- `src/sapphire_flow/store/pipeline_health_store.py`
- `src/sapphire_flow/services/training_data.py`
- `src/sapphire_flow/flows/train_models.py`
- `tests/integration/live/test_meteoswiss_nwp_live.py`
- `pyproject.toml` pytest marker configuration

## Dependency on Plan 081

Plan 082 starts only after Plan 081 provides:

- `RecapGatewayAdapter`
- typed Gateway HRU/polygon metadata
- Recap variable catalog for confirmed variables
- elevation-band forecast storage support
- offline fake-client tests for DataFrame shape, unit conversion, member
  assembly, provenance, and error mapping

## Operational Decisions

### Live Test Markers

Add a dedicated `live_recap` pytest marker in `pyproject.toml` and keep live
Recap tests also marked `live`. Confirm the default `addopts` continues to
exclude network tests through `not live`; this keeps whole-plan `uv run pytest`
gates offline by default.

### Latest Cycle and Ensemble Contract

Operational Recap IFS fetches assemble a full SAP3 ensemble as:

- `member_id=0`: HRES `fc`, no `member` parameter. ECMWF discontinued the ENS
  control (`cf`) and replaced it with HRES `fc`, so `fc` is the current control —
  G7-compliant, not a deviation.
- `member_id=1..50`: `pf` members 1..50.

Live smoke must pin confirmed bounds:

- `pf member=1` valid.
- `pf member=0` rejected.
- `pf member=51` rejected.
- `fc` called without `member`.

`cf` is not exposed (discontinued by ECMWF); do not depend on it.

### Temporal Reconciliation for Model Inputs

The adapter preserves native valid times:

- IFS: 3-hourly through 144 h, then 6-hourly through about 360 h.
- ERA5-Land: hourly.
- Snow: daily.

The operational model-input path, not the adapter, must define how daily
deterministic snow joins 51-member sub-daily IFS features. First-cut policy:

1. Keep IFS member rows at native valid times and member ids.
2. Treat snow features as deterministic daily state/flux features keyed by
   date, station, spatial type, and band.
3. During model-input assembly, join each sub-daily NWP valid time to the snow
   value for that valid date and duplicate the deterministic snow value across
   all NWP member ids for models that consume ensemble NWP.
4. The duplication is an input-shaping step only; persisted Gateway snow
   forecasts remain deterministic with `member_id=None`.
5. If a model declares daily-only dynamic inputs, aggregate NWP in the model
   input service with explicit per-variable aggregation rules; do not aggregate
   inside the Recap adapter.

This policy must be tested before production enablement because it affects model
features, not just data ingestion.

### Coverage and Training Readiness

`recap-dg-client` exposes no coverage metadata, and the Gateway-dev confirmed
(2026-06-25) the Gateway returns only what is available and does **not** flag gaps.
Coverage is therefore a **fully SAP3-side** gate. Flow 6 training must remain blocked
until SAP3 has a coverage record proving the covered span contains the requested
training window for every required HRU/polygon/dataset/variable.

Mechanism (Nepal v1): a **supervised SAP3 coverage manifest**, recorded after manual
historical back-extraction, listing covered span per HRU/polygon/dataset/variable.
SAP3 also compares requested vs returned span on every fetch to detect **silent
truncation**. Do not infer readiness from first/last timestamps in a returned
DataFrame — the Gateway silently returns only what exists, so a non-empty frame is not
a coverage proof. Client v2's per-row `source` column adds a **leakage guard**: SAP3 can
assert historical rows are observed (`era5_land` / `jsnow_reanalysis`), not forecast-fill
(`ifs` / `jsnow_forecast`), before admitting them as training data.

### Watchdog Discrimination

`NWP_DELIVERY` must distinguish:

- stale latest available Gateway cycle
- unsupported HRU/shapefile metadata
- out-of-coverage basin/polygon
- API auth/key errors
- transient network/Gateway failures

Only stale latest available cycle is a true NWP delivery staleness alert.

## Implementation Phases

### Phase 1 - Live Marker and Gateway Smoke Tests

#### Task 1A - Define Recap live marker and collection safety

**Scope in:** Add `live_recap` to `pyproject.toml` markers and confirm default
pytest `addopts` excludes any test marked `live`, including Recap live smoke.

**Scope out:** Do not add network calls in default CI.

**Verification:**

```bash
uv run python -c "from pathlib import Path; text=Path('pyproject.toml').read_text(); assert 'live_recap:' in text and 'not live' in text"
```

#### Task 1B - Add operational live smoke tests

**Scope in:** Add `tests/integration/live/test_recap_gateway_live.py`, marked
`live` and `live_recap`, skipping when `RECAP_API_KEY` is absent. Cover
unsupported-shapefile discovery, `fc` shape, `pf` member 1 shape, confirmed
member-bound rejections, precipitation/temperature range checks after
conversion, snow endpoint shape (`hs`/`rof`/`swe`), and — using the Task 1C test
shapefile — that lowercase `g_<...>` feature names are echoed as columns and that a
banded HRU returns one column per band; and that the default `source`/`source_run`
provenance columns are present with expected values (`era5_land` / `ifs` / `jsnow_*`).

**Scope out:** Do not make live Recap tests part of default `uv run pytest`.

**Verification:**

```bash
uv run pytest tests/integration/live/test_recap_gateway_live.py --collect-only -m 'live and live_recap'
RECAP_API_KEY=... uv run pytest tests/integration/live/test_recap_gateway_live.py -m 'live and live_recap' -v
```

#### Task 1C - Produce a SAP3-compliant Gateway test shapefile

**Scope in:** Produce a small test GeoPackage with lowercase `g_<...>` feature names
(satisfying the no-leading-`0` constraint), including at least one **banded** basin
(`g_<...>_band_<id>` polygons), and register it on the Gateway via the manual web
upload. Record its HRU name + per-polygon names as a test fixture so Task 1B can
assert the column echo and band behavior.

**Scope out:** Do not build the production GeoPackage export/validation pipeline; this
is a minimal validation fixture.

**Verification:**

```bash
uv run python -c "from pathlib import Path; assert Path('tests/integration/live/fixtures/recap_compliant_hru.json').exists()"
```

### Phase 2 - Nepal Wiring, Latest Cycle, Temporal Inputs

#### Task 2A - Wire Nepal Recap configuration

**Scope in:** Add Nepal deployment config entries for Recap base URL, API key
environment variable, timeout/TLS policy, retry policy, Gateway HRU metadata
source, and operational staleness threshold.

**Scope out:** Do not enable Recap in Swiss profiles and do not expose the key
through API responses/logs.

**Verification:**

```bash
uv run pytest tests/unit/config/test_recap_gateway_config.py tests/unit/flows/test_run_forecast_cycle.py::TestNepalRecapConfigWiring
```

#### Task 2B - Resolve latest available Gateway cycle

**Scope in:** Implement latest-cycle probing over candidate IFS
`run_date`/`run_hour` values, treating `source_data_missing` as candidate
unavailable and stopping at configured max age.

**Scope out:** Do not require a Gateway health API.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway_cycle_resolution.py
```

#### Task 2C - Integrate NWP_DELIVERY watchdog semantics + NWP source dispatch generalization

**Scope in:**
1. Emit/store pipeline health information that distinguishes stale delivery from
   unsupported HRU, out-of-coverage, auth, and transient Gateway failures.
2. **NWP source dispatch generalization** (added per Plan 106 §4 — this is the
   implementation home for the gateway-adapter wiring; without it the `RecapGatewayAdapter`
   is dead code and every Nepal cycle emits a false-CRITICAL `NWP_DELIVERY` record):
   - **(a)** Add a `RecapGatewayAdapter` construction branch to the `if adapter is None:`
     block at `flows/run_forecast_cycle.py:964-997` (currently only builds
     `MeteoSwissNwpAdapter`).
   - **(b)** Parameterize `_check_nwp_grid_staleness` (`run_forecast_cycle.py:564-602`,
     was `:508-546`) on the **active NWP source string** instead of the module-level
     `_ICON_NWP_SOURCE`, wiring the call site (was `:1244-1250`; re-locate on current
     `main`). On an IFS-only Nepal deployment the current
     `fetch_latest_cycle_time("icon_ch2_eps")` over `weather_forecasts` (NOT the Zarr grid
     archive — do not send the fix toward grid storage) returns `None` every cycle and
     writes `PipelineHealthStatus.CRITICAL` / `PipelineCheckType.NWP_DELIVERY` (`:536-545`)
     — a permanent false alarm. Skip/redirect the ICON-grid staleness check for gateway
     (pre-extracted, non-gridded) sources.
   - **(c)** Add the analogous Flow-6 factory/dispatch branch at
     `ingest_weather_history.py:168-202` (`build_production_reanalysis_adapter`) + call site
     `:277-292`, and resolve the `NWP_SOURCE` Protocol gap for `_reanalysis_sources()`
     (`:243-252`) — recommended default (b): expose `NWP_SOURCE: str` on the gateway adapter
     to satisfy the local `_ReanalysisAdapter` Protocol (lowest blast radius; see Plan 106 §4).
   - **(d)** Update the now-stale `_select_nwp_source` docstring (`run_forecast_cycle.py:83-86`,
     "Phase A only stores ICON grid records…") to reflect multi-source support. `_select_nwp_source`
     itself needs **no** logic change — its BASIN_AVERAGE second pass (`:95-97`) already returns
     the gateway source.
   - **Phase A→B storage-key round-trip (corrected per Plan 115 [ex-114] + the 081 Codex review
     2026-07-13):** the forecast storage key is the **`role==FORECAST` binding's
     `nwp_source`** (e.g. `"ifs_ecmwf"`) as selected by `_select_nwp_source` — **not**
     `adapter.NWP_SOURCE`, which under the locked design is the adapter's *reanalysis*
     identity (`"era5_land"`, used only by Flow-6 `_reanalysis_sources`). Phase A must
     write forecast records under that forecast binding's source string so Phase B's
     `fetch_weather_forecasts(nwp_source=…)` (`services/operational_inputs.py`, re-locate
     on `main`) finds them — otherwise every Nepal station logs `operational_inputs.no_nwp`
     and returns None. This depends on **Plan 115** (the weather-source identity model, which owns the `WeatherSourceRole` field that makes
     `_select_nwp_source` pick the forecast binding deterministically); do not implement 2C
     dispatch before 115 lands.

3. **Generic gateway-binding validator (owned HERE, not deferred to D5-2).** To remove a
   sequencing contradiction (the completion-gate test below asserts the invariant, so its
   owner cannot be a later Wave-2 plan): Task 2C ships a **minimal, generic** validator —
   any `StationWeatherSource` binding for a gateway NWP source MUST carry
   `extraction_type = SpatialRepresentation.BASIN_AVERAGE` (`types/enums.py:73-77`), else
   `_select_nwp_source`'s fallback (`run_forecast_cycle.py:98`) silently routes the station
   through `_ICON_NWP_SOURCE` and defeats the fix. The **fuller, DHM-specific** onboarding
   validation (per-station units, datums, gauge metadata) remains owned by the D5-2 DHM-obs/
   onboarding plan, which **extends** this generic check — D5-2 depends on 2C, not vice-versa.

**Scope out:** Do not collapse all adapter errors into stale NWP delivery.

**Authoring dependency:** the dispatch regression test cannot compile until the
`RecapGatewayAdapter` class exists (Plan 081). Per the 081→082 dependency (frontmatter
`082:7-8`; graph `082:476,483`), sequence **081 WF2 merge → author the 2C dispatch test**;
do not stall a WF2 agent on a missing import.

**Verification:**

```bash
uv run pytest tests/unit/flows/test_run_forecast_cycle.py::TestRecapNwpDeliveryWatchdog tests/integration/store/test_pipeline_health_store.py
```

Plus a **completion-gate test that cannot pass by disabling the watchdog** (the loose form
would let "delete the staleness check entirely" pass): use a store with a callable
`fetch_latest_cycle_time`, seed **no** `icon_ch2_eps` cycles and a **fresh** `ifs_ecmwf`
cycle (or assert the gateway skip path), then route an IFS-bound station
(`nwp_source="ifs_ecmwf"`, `extraction_type=BASIN_AVERAGE`) through the full dispatch and
assert it (i) selects the gateway source, (ii) constructs the `RecapGatewayAdapter` (not
`MeteoSwissNwpAdapter`), and (iii) does **not** emit a `PipelineHealthStatus.CRITICAL`
`NWP_DELIVERY` record. **Plus a POSITIVE control:** a genuinely stale *active* source (an
ICON-bound Swiss station with an old cycle) **still** emits CRITICAL — proving the watchdog
was made source-aware, not switched off. **Plus** an onboarding test asserting a
`ConfigurationError` when a gateway binding uses a non-`BASIN_AVERAGE` `extraction_type`.

#### Task 2D - Define and test temporal model-input join policy

**Scope in:** Implement/test the model-input policy for joining deterministic
daily snow features to sub-daily 51-member IFS inputs without changing persisted
Gateway snow records.

**Scope out:** Do not resample/broadcast inside `RecapGatewayAdapter`.

**Verification:**

```bash
uv run pytest tests/unit/services/test_operational_inputs.py::TestRecapTemporalFeatureJoin
```

#### Task 2E - Add committed recap-dg-client dependency (ForecastInterface pattern)

**Scope in:** Add `recap-dg-client` as a rev-pinned git dependency in `pyproject.toml`
+ `[tool.uv.sources]`, implement the scoped two-step CI wheel-guard exception (Plan
079-style) in `.github/workflows/ci.yml`, add private-repo **clone auth** for CI and
the Docker builder stage, and document the removal trigger (migrate to a private-index
wheel — a future Plan 080-style follow-up). Update `docs/standards/security.md` and
`docs/standards/cicd.md`.

**Scope out:** Do not migrate to a private-index wheel here (deferred follow-up). Do
not loosen the wheel-guard for any package other than `recap-dg-client`.

**Verification:**

```bash
uv run python -c "from pathlib import Path; t=Path('pyproject.toml').read_text(); assert 'recap-dg-client' in t or 'recap_client' in t"
uv run python -c "from pathlib import Path; t=Path('.github/workflows/ci.yml').read_text(); assert 'recap' in t.lower() and 'no-build' in t"
```

### Phase 3 - Coverage Gate and Training Readiness

#### Task 3A - Add Gateway coverage record/manifest model

**Scope in:** Add a SAP3-side coverage representation keyed by
HRU/polygon/dataset/variable/span, populated from Gateway metadata if available
or from a supervised manifest if not.

**Scope out:** Do not infer coverage from non-empty DataFrames.

**Verification:**

```bash
uv run pytest tests/unit/services/test_gateway_coverage_gate.py::TestGatewayCoverageManifest
```

#### Task 3B - Gate Flow 6 training on coverage + parametric multi-year backfill window

**Scope in:**
1. Refuse training unless coverage contains the requested training window for every
   required Gateway-backed variable, spatial target, and band.
2. **Parametric historical-forcing backfill window** (added per Plan 106 — this is the
   home for the multi-year-ingest gap): `ingest_weather_history` is **hardcoded to a
   60-day window** (`flows/ingest_weather_history.py:50` `_WINDOW_DAYS = 60`, used at
   `:300` as `now - 60 days`) — the MeteoSwiss open-data archive limit. Add explicit
   `start`/`end` (or `window_days`) backfill parameters so Nepal ERA5-Land/Snowmapper
   training can accrue **multi-year** history from the gateway; **keep the Swiss 60-day
   default** unchanged (no regression to the MeteoSwiss path). Tie **Task 4A**'s manual
   Gateway back-extraction runbook to the coverage manifest so a supervised backfill is
   verifiable via coverage. (A separate plan is warranted ONLY if automated/chunked
   large-backfill orchestration is later needed — a plain parametric window suffices for v1.0.)

**Scope out:** Do not weaken existing Swiss/CAMELS training paths; do not build automated
chunked-backfill orchestration here.

**Verification:**

```bash
uv run pytest tests/unit/services/test_gateway_coverage_gate.py::TestGatewayCoverageGate tests/unit/flows/test_train_models.py::TestGatewayCoverageTrainingGate tests/unit/flows/test_ingest_weather_history.py::TestParametricBackfillWindow
```

### Phase 4 - Gateway Operations Runbooks

#### Task 4A - Document Gateway operational procedures

**Scope in:** Create `docs/operations/recap-gateway-runbook.md` covering manual
gpkg upload, historical back-extraction, coverage manifest/metadata recording,
live smoke execution, `NWP_DELIVERY` triage, API key handling, and snow-variable
confirmation status.

**Scope out:** Do not document upstream client fixes as already complete.

**Verification:**

```bash
uv run python -c "from pathlib import Path; p=Path('docs/operations/recap-gateway-runbook.md'); text=p.read_text(); required=['RECAP_API_KEY','coverage manifest','historical back-extraction','NWP_DELIVERY','live_recap','snow variable']; missing=[s for s in required if s not in text]; assert not missing, missing"
```

## Whole-Plan Exit Gates

Default gates must not hit the network:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

Confirm live Recap tests are marker-gated and skipped from defaults:

```bash
uv run pytest tests/integration/live/test_recap_gateway_live.py --collect-only -m 'live and live_recap'
uv run python -c "from pathlib import Path; text=Path('pyproject.toml').read_text(); assert 'live_recap:' in text and 'not live' in text"
```

Credentialed live smoke, when available:

```bash
RECAP_API_KEY=... uv run pytest tests/integration/live/test_recap_gateway_live.py -m 'live and live_recap' -v
```

## Open Gateway Questions Scoped to Plan 082

The Gateway-dev agenda was closed on 2026-06-25; the items below are **resolved** and
recorded for traceability rather than as open questions:

1. String feature `name`s ARE echoed as DataFrame columns; only constraint: a `name`
   must not start with `0` (satisfied by `g_<...>`). Validated by live smoke (Tasks 1B/1C).
2. A banded gpkg returns one column per band polygon — confirmed by the Tasks 1B/1C
   banded live smoke against the compliant test shapefile.
3. Coverage: the Gateway provides **no** coverage metadata and does not flag gaps;
   SAP3 uses a supervised coverage manifest (Phase 3).
4. Snow variables are stable: `hs`=snow height, `rof`=snowmelt (incl. direct runoff
   from snow-free areas), `swe`=SWE.
5. No latest-cycle endpoint; SAP3 probes candidate `run_date`/`run_hour` (Task 2B).
6. No concurrency limit to design around.
7. `cf` (ENS control) is discontinued by ECMWF; `fc` HRES is the control.

Genuinely still open (non-blocking, nice-to-have): header auth instead of the
query-param API key (an upstream client issue). **Source/run provenance is now solved:**
client v2 (PR #1, 2026-06-25) returns per-row `source`/`source_run` on every export, so
SAP3 tags `RawHistoricalForcing.version` and forecast cycles from `source_run`.

## Risks and Recommendation

| Risk | Impact | Mitigation in Plan 082 |
|---|---|---|
| No coverage metadata (Gateway won't provide) | Flow 6 could train on silently truncated history. | Hard training gate on a supervised SAP3 coverage manifest + a requested-vs-returned span check. |
| Banded HRU behavior unconfirmed | Nepal banded models may receive incomplete features. | Credentialed banded-HRU live smoke before production enablement. |
| 51 calls per variable/HRU/cycle | Slow cycles and higher failure probability. | Gateway dev confirmed no concurrency limit to design around (2026-06-25); use parallel fetch (raise the deploy `concurrency_limit`) with the retry/backoff policy from Plan 081. |
| Temporal mismatch across IFS/ERA5/snow | Model inputs can silently misalign. | Explicit model-input join policy and tests. |
| Watchdog over-alerting | Operators cannot distinguish stale Gateway delivery from config/coverage/auth problems. | Typed error discrimination and separate pipeline-health outcomes. |

Recommendation: **do not promote Plan 082 to READY until Plan 081 is accepted and
the team is ready to work through live Gateway credentials/answers**. Coverage
metadata or a supervised coverage manifest remains mandatory before training
readiness can be declared.

## References

- Plan 081: `docs/plans/081-recap-dg-client-integration.md`
- `docs/requirements/01-data-gateway-requirements.md` G1-G23
- `docs/requirements/00-internal-gap-analysis.md`
- `docs/standards/orchestration.md`
- `docs/standards/logging.md`
- `docs/standards/security.md`

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Live marker and Gateway smoke tests",
      "tasks": ["1A", "1B", "1C"],
      "parallel": false,
      "depends_on": ["plan-081"]
    },
    {
      "id": "phase-2",
      "name": "Nepal wiring, latest cycle, temporal inputs",
      "tasks": ["2A", "2B", "2C", "2D", "2E"],
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
      "name": "Gateway operations runbooks",
      "tasks": ["4A"],
      "parallel": false,
      "depends_on": ["phase-3"]
    }
  ]
}
```
