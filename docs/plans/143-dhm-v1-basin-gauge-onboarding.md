---
status: DRAFT
created: 2026-07-23
revised: 2026-09-24
plan: 143
title: DHM v1 station, basin and Gateway onboarding
scope: Prepare the six DHM gauges for Nepal model onboarding by linking the station records from Plan 268 to the accepted basin/static package and already registered Gateway polygons, ingesting supported historical forcing, and creating explicitly marked water-level target history where the imported records and rating curves support it. NOT Gateway registration/subscription, Swiss CAMELS onboarding, an operational activation, alerting, or a claim that rating-derived stages are original DHM level observations.
depends_on: [120, 268, 304, 315]
blocks: []
related: [082, 117, 139, 264, 269, 272, 268, 301, 315, 316, 317, 318]
---

# Plan 143 — DHM v1 station, basin and Gateway onboarding

## Status

**DRAFT.** This plan must not be implemented until its dependencies have landed and
the orchestrator sets it READY. The sequence deliberately waits for the completed
QC ladder and Plan 268 station import before it assigns forecast targets or runs
onboarding QC.

## Problem and measured boundary

The deployed `onboard-stations` Prefect flow is the CAMELS-CH onboarding path. It
resolves its default data directory to `raw/CAMELS_CH` and, when no basin list is
passed, loads `config.toml`'s 169 Swiss onboarding basin IDs
(`src/sapphire_flow/flows/onboard.py:79,220-237`; `config.toml:178-203`). None of the six
DHM gauge codes is in that list. Triggering the deployed flow with defaults would
therefore process the Swiss configuration; passing the gauge codes does not turn
it into a Nepal importer.

The Nepal pieces now have a distinct, measured boundary:

- Plan 268 owns importing the six DHM station records and their daily discharge
  histories. Its current draft intentionally leaves `forecast_targets` unset and
  station status at `onboarding`.
- The Data Gateway registration is already complete upstream. The Recap API
  accepts the registered shapefile name `nepal6_20260923` as `hru_code` and
  returns six polygon columns: `g_447`, `g_450`, `g_604_5`, `g_647`, `g_670`,
  `g_684`. The `604.5` gauge code is represented by `g_604_5`. The registered
  shapefile is one HRU containing six polygon features; the API's `hru_code` is
  not an individual gauge code.
- Read-only live requests on 2026-09-24 returned non-null values for all six
  polygons for ERA5-Land `total_precipitation`, `2m_temperature`,
  `surface_net_solar_radiation`, `surface_net_thermal_radiation`; IFS `tp`, `2t`,
  `ssr`, `str`; and their stitched operational pairs. This proves Gateway
  retrieval only. SAP3's Recap adapter currently supports precipitation and
  temperature, while radiation remains explicitly excluded pending a safe
  temporal/unit contract (`src/sapphire_flow/adapters/recap_gateway.py:108-128`, Plan 243). JSNOW returned
  `source_data_missing` for the Nepal shapefile on the tested dates while the
  demo HRU returned snow data; snow readiness remains conditional on Gateway
  processing.
- Plan 120's basin package importer already persists each accepted basin's
  `gateway_hru_name` and feature `name` as a polygon binding. Do not recreate that
  store or implement a second binding mechanism (`src/sapphire_flow/adapters/recap_gateway.py:218-230`).
- The owner's v1 direction is water-level forecasts (`docs/v1-scope.md`). The
  DHM delivery in Plan 268 contains discharge and historical rating tables, not
  original level observations. No table is valid today, and the table datum must
  be established before any historical inversion is treated as a water-level
  target. Inversion yields rating-derived equivalent stages with uncertainty,
  not recovered original levels. Plan 268's data-use decision also governs any
  derived values and model parameters.

The missing work is therefore a Nepal-specific, repeatable station/basin import
and readiness path that consumes the registered Gateway shapefile and Plan 268
station rows. The existing `ingest-recap-reanalysis` flow is snow-only
(`src/sapphire_flow/flows/ingest_recap_reanalysis.py:1-10`), and
`ingest_weather_history` is MeteoSwiss-only
(`src/sapphire_flow/flows/ingest_weather_history.py:1-8`); neither persists
Recap ERA5-Land meteorological reanalysis for these stations. Plan 139's W3/W4 describes a
similar path for test HRU 12300; this plan delivers the DHM six-station use and
reusable ingestion needed here without changing Plan 139's 12300 scope. It must
never use the Swiss CAMELS flow as a shortcut.

## Decisions and boundaries

### D1 — Reuse the accepted package importer and Plan 268 station rows

Plan 268 creates station rows; this plan must resolve them by `(code, network)`
and must not create a second row for the same DHM gauge. Build and validate a
basin/static package using the existing contract and importer (Plans 117/120).
The package has one `gateway_hru_name`, `nepal6_20260923`, and six basin features
whose `name` values match the Gateway columns exactly: `g_447`, `g_450`,
`g_604_5`, `g_647`, `g_670`, and `g_684` for station codes 447, 450, 604.5,
647, 670, and 684. Importing it must produce one basin-average binding per
station.

The user owns all Gateway-side shapefile registration and subscription actions.
This plan consumes the existing registration and adds no Gateway admin API,
upload, or subscription automation.

### D2 — Keep data roles and station lifecycle explicit

The model target is `water_level`, consistent with the owner's v1 direction.
Plan 268 supplies no measured levels and no rating curve valid today. Historical
inversion is conditional: use it only for dates covered by a curve whose gauge
datum and unit can be reconciled to the model's canonical water-level datum, and
only after the owner and data owner authorize the derived values. Store a
distinct derived provenance and curve identity. Never label equivalent stages
as measured levels or use them outside a curve's validity dates or tabulated
discharge range. No conversion may extrapolate to manufacture a target.

Create and QC target history with an explicit `water_level` parameter before
setting `forecast_targets`. Do not rely on the standard onboarding service to
select the QC parameter while the target is still unset: that service derives
its QC scope from `forecast_targets`. Set the target only after the history has
passed the applicable DHM QC path and the owner-confirmed data-use decision
permits the derived values. Keep every station at `onboarding`; this plan does
not make it eligible for scheduled ingest or forecast production.

### D3 — Separate raw Gateway availability from supported model input

The initial supported forcing set is only what the Recap adapter and model
contracts actually support. Raw `ssr`/`str` retrieval does not authorize storing
them as canonical radiation, because the Gateway strips source unit and temporal
metadata and the current adapter intentionally excludes them. Snow is optional
for this onboarding gate until JSNOW data is available for the registered HRU.
If a selected model requires radiation or snow, onboarding must report that
requirement as unmet and hold that model/station combination; it must not invent
units, silently omit required inputs, or claim forecast readiness.

### D4 — Wait for the QC implementation chain

Plan 318's implementation is on `origin/main`; Plans 316 and 317 remain READY
and pending implementation. The owner directed that these gaps be completed
before Plans 264/269 and then Plan 268 proceed; Plan 272 is not considered
end-to-end until that work and its remaining closure are verified. Plan 315 owns
the onboarding QC fail-closed behavior and depends on the QC rule-shape work.
This plan remains blocked behind Plans 268, 304 and 315; it must not create
`forecast_targets` early and accidentally run onboarding QC with an incomplete
rule set.

## Non-goals

- Uploading the shapefile, assigning Gateway identifiers, or managing Gateway
  subscriptions; the owner completes those actions outside SAP3.
- Reusing or broadening the Swiss `onboard-stations` flow.
- Replacing the Plan 120 package loader, basin importer, or polygon-binding
  persistence.
- A live DHM observation adapter (Plan 300), rainfall observations (Plan 301),
  QC policy/thresholds (Plans 264/269/315), or edits to Plan 272 or Plans
  316–318.
- Forecast model training, model/group assignment, station promotion, forecast
  alerts, or an operational deployment.
- Treating Gateway raw radiation retrieval as proof of SAP3 radiation support.

## Tasks

### T1 — Validate the six-gauge onboarding manifest and package inputs

**Outcome:** one reviewed Nepal package maps the six owner-confirmed DHM gauge
codes to their station rows, basin geometries, static attributes, and exact
Gateway polygon names.

**In:** the accepted basin/static contract, Plan 120 importer, Plan 268 station
metadata, and the Gateway registration facts above.
**Out:** Gateway writes, station-row creation, model targets, rating-curve edits.

**Verification:** package validation succeeds; it declares exactly one Gateway
HRU name (`nepal6_20260923`), six unique station codes, six matching feature
names, and the correct `dhm` network. The manifest and package contain no
individual DHM discharge or rating-table values. Owner spot-checks the station
identity/geometry joins before any database write. Run
`uv run pytest tests/unit/services/test_basin_package_loader.py tests/unit/types/test_basin.py`.

**Pre-change:** N/A — input/package contract and review gate; no behavior change.

### T2 — Import the six basins and persist their Gateway polygon bindings

**Outcome:** all six Plan 268 station rows resolve to one imported basin each,
and the package importer stores the exact `nepal6_20260923` / `g_<code>` binding.

**In:** the reusable `import_loaded_basin_package` core and its tests; a
Nepal-specific operator command that validates the package then calls the core.
**Out:** edits to Plan 268, duplicate station creation, Gateway-side changes.

**Verification:** focused importer and resolver tests assert the six station
codes map one-to-one to the six polygons, including `604.5` → `g_604_5`; a
staging read-only audit confirms six basin rows and six bindings in the intended
tenant. A repeated import is idempotent. The generic CAMELS deployment is not
run. Run `uv run pytest tests/integration/services/test_basin_importer.py tests/integration/store/test_basin_importer_persistence.py tests/integration/store/test_basin_importer_idempotency.py`.

**Pre-change:** package import is not yet wired to the six Plan 268 station rows;
the test must fail on a missing station, wrong HRU name, wrong polygon name, or
duplicate mapping.

### T3 — Persist supported Nepal historical forcing

**Outcome:** a Recap ERA5-Land history path resolves the six persisted bindings
and stores supported historical variables in `historical_forcing` with
per-station provenance. The Recap adapter can fetch precipitation and
temperature reanalysis (variable mapping at
`src/sapphire_flow/adapters/recap_gateway.py:108-128`, fetch path at
`src/sapphire_flow/adapters/recap_gateway.py:1486-1544`); the existing
`ingest-recap-reanalysis` flow handles snow only.

**In:** a bounded Recap ERA5-Land ingestion flow using the existing adapter,
resolver, and historical-forcing store; Nepal deployment configuration and
focused tests. Include `precipitation` and `temperature` using their verified
ERA5-Land names. Record the model's declared requirements; do not add radiation
until its unit and temporal semantics are accepted and represented by the
adapter.
**Out:** another Gateway client, snow processing (handled by the existing
snow-only flow), IFS operational forecasting, raw-value dumps, forecasts.

**Verification:** unit tests cover the six bindings and canonical source/parameter
mapping. A bounded staging import reports per-station row counts, date coverage,
and source provenance only; no forcing values are logged or exported. The gate
fails when a model-required variable has no supported adapter mapping or no
usable records. JSNOW absence is reported as an unmet optional requirement, not
as a Gateway outage. Run
`uv run pytest tests/unit/flows/test_ingest_recap_era5_reanalysis.py tests/unit/adapters/test_recap_gateway.py`.

**Pre-change:** the live client can retrieve source data, but no flow persists
Recap ERA5-Land meteorological reanalysis to `historical_forcing`. Capture the
bounded baseline counts and assert the new path creates six-station records
without changing Swiss counts or assignments.

### T4 — Create qualified rating-derived water-level target history

**Outcome:** any approved, datum-compatible historical equivalent-stage targets
are distinctly marked; `forecast_targets` is set to `water_level` only for
stations with enough validated target history for the agreed model and forecast
period. Otherwise the station remains held with its target unset.

**In:** the existing rating conversion boundary (extend it with a tested inverse
only if needed), the `RATING_CURVE_DERIVED` provenance, an explicit
parameter-scoped QC invocation using the completed Plan 264/304/315 behavior,
and per-station target assignment. Obtain the required owner data-use decision
before storing or training on derived values.
**Out:** claiming reconstructed values are measured levels, curve extrapolation,
current rating-table creation, station promotion, model training.

**Verification:** tests cover curve validity boundaries, in-range inversion,
out-of-range discharge, missing curves, unresolved datum, unit conversion,
provenance, and idempotent writes. They prove that QC can evaluate the new RAW
`water_level` records while `forecast_targets` remains unset, and that the
target is assigned only after data-use approval, an accepted datum, and the
explicit DHM QC pass accepts usable history.
For staging, report eligible/held counts and date coverage without values. QC
must use the completed DHM rule selection and must not judge context rows; a
station with no acceptable target history keeps `forecast_targets` unset and is
reported as held. Run `uv run pytest tests/unit/services/test_rating_conversion.py tests/unit/services/test_qc.py tests/unit/services/test_onboarding.py`.

**Pre-change:** no inverse conversion path exists in
`services/rating_conversion.py`, and Plan 268 leaves all six targets unset.
Tests must fail against that state for a valid inverse and for marking an
unqualified station as a water-level target.

### T5 — Hand off station readiness without activating forecasts

**Outcome:** the operator has a per-station readiness report for model
onboarding, and the staging records remain in the `onboarding` lifecycle state.

**In:** `docs/operations/nepal-station-onboarding-runbook.md` and checks for basin/binding, supported
forcing, target-history, QC, tenant, and unmet model-input requirements.
**Out:** model assignment, training, hindcast, operational promotion, schedule
registration, alerts.

**Verification:** the report distinguishes ready-for-model-onboarding from
forecast-operational. All six gauge codes appear exactly once; each failed gate
names its reason. A database audit confirms no station was promoted and no
forecast/model assignment was created. Do not trigger the existing
`onboard-stations` deployment, whose defaults point at CAMELS-CH. Run
`uv run pytest tests/unit/services/test_nepal_onboarding_readiness.py`.

**Pre-change:** N/A — final bounded readiness and lifecycle gate.

## Exit condition

Plan 143 completes when each gauge has a correct station-to-basin-to-Gateway
binding, supported historical forcing, and either a QC-qualified water-level
target history or a specific hold reason. Completion does not mean that a model
is trained, that forecasts are being produced, or that a station is operational.

```json
{
  "phases": [
    {"id": "package", "tasks": ["T1"], "parallel": false},
    {"id": "basin-binding", "tasks": ["T2"], "depends_on": ["package"]},
    {"id": "forcing", "tasks": ["T3"], "depends_on": ["basin-binding"]},
    {"id": "targets", "tasks": ["T4"], "depends_on": ["forcing"]},
    {"id": "readiness", "tasks": ["T5"], "depends_on": ["targets"]}
  ]
}
```
