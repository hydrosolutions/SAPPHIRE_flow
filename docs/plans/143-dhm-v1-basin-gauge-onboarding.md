---
status: DRAFT
created: 2026-07-23
revised: 2026-09-29
plan: 143
title: DHM v1 station, basin and Gateway onboarding
scope: Prepare the six CHWRR gauges for discharge-model onboarding using Plan 268's imported daily discharge, the accepted basin/static package, registered Gateway polygons, and supported historical forcing. No rating inversion, new observation QC path, model training, station activation, alerting, or Gateway administration.
depends_on: [120, 268]
blocks: []
related: [082, 117, 139, 264, 269, 272, 301, 304, 315, 316, 317, 318]
---

# Plan 143 — DHM v1 station, basin and Gateway onboarding

## Status

**DRAFT.** Reconciled against remote `main` at `90aebc56` on 2026-09-29.
This is a planning update, not implementation or staging acceptance. Independent
review remains required; only the orchestrator sets READY.

| Prerequisite | State at this revision | Remaining gate for Plan 143 |
|---|---|---|
| 120 — basin/static importer | COMPLETE, archived; reusable core is present | Validate the actual six-basin package and station joins |
| 264 / 269 — network QC and station overrides | Merged (#315 / #324) | Use the existing selector and resolver; no reimplementation |
| 272 / 316 / 317 / 318 / 324 — cadence and unchecked-data handling | COMPLETE, archived | Scheduled-ingest closure does not close onboarding's separate gap |
| 268 — restricted DHM delivery | Implementation merged as `a7794028` (#332) | T8 real-delivery staging acceptance; deployment is owned by the `cmal_small` session |
| 304 / 315 — daily context / generic onboarding QC | DRAFT | Related follow-ons, no longer dependencies of the discharge-only path (D4) |

Plan 268's file still says READY; its merged code is the evidence of implementation,
not evidence that T8 passed. Do not rerun that deployment from this plan or assume
the six station rows exist until its operator reports acceptance. The owner's
2026-09-29 decision changes this plan to **discharge-first onboarding**: T4
qualifies Plan 268's already-QC'd history instead of creating and QC'ing levels.

**Restricted observations and Plan 402 D6.** The merged Plan 268 observation
route (`api/routes/api_stations.py::list_observations`) excludes its delivery ID
for every non-admin principal, including reviewer tokens. Those observations
and their QC details are therefore withheld today. This is a delivery filter,
not a blanket DHM-network policy. This plan preserves that protection (D5).
The owner decision about consumer-visible QC details on other DHM observations
and future forecasts remains open; neither Plan 402 nor the import authorizes
publishing restricted measurements or values recoverable from QC details.

## Problem and measured boundary

The deployed `onboard-stations` Prefect flow is the CAMELS-CH onboarding path. It
resolves its default data directory to `raw/CAMELS_CH` and, when no basin list is
passed, loads `config.toml`'s Swiss onboarding basin IDs
(`src/sapphire_flow/flows/onboard.py::onboard_stations_flow`; `config.toml`). None of the six
DHM gauge codes is in that list. Triggering the deployed flow with defaults would
therefore process the Swiss configuration; passing the gauge codes does not turn
it into a Nepal importer.

The Nepal pieces now have a distinct, measured boundary:

- Plan 268's merged `cli/import_dhm_delivery.py` registers the six stations in
  tenant `chwrr`, imports delivery-tagged curves and daily discharge, and runs
  delivery-only QC. It leaves `forecast_targets` unset and station status at
  `onboarding`. The existing shared Mac-mini database is the acceptance target;
  this plan does not create a separate Nepal database or tenant.
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
  temporal/unit contract (`src/sapphire_flow/adapters/recap_gateway.py::RECAP_VARIABLES`, Plan 243). JSNOW returned
  `source_data_missing` for the Nepal shapefile on the tested dates while the
  demo HRU returned snow data; snow readiness remains conditional on Gateway
  processing.
- Plan 120's importer assigns `stations.basin_id` and persists each accepted
  basin's `gateway_hru_name` and feature `name` in the polygon-binding store.
  It does **not** create `station_weather_sources` rows. T2 must also create the
  reanalysis source binding that `RecapGatewayReanalysisAdapter` filters on;
  having a polygon mapping alone cannot make a station eligible for retrieval.
- The owner selected discharge-first onboarding on 2026-09-29. The imported
  daily discharge is the target history; historical rating coverage does not
  gate use of a directly delivered discharge. The eventual water-level direction
  in `docs/v1-scope.md` remains a follow-on. No currently valid rating table is
  available, so readiness here says nothing about converting live levels or
  producing operational forecasts.

The missing work is therefore a Nepal-specific, repeatable station/basin import
and readiness path that consumes the registered Gateway shapefile and Plan 268
station rows. The existing `ingest-recap-reanalysis` flow is snow-only
(`src/sapphire_flow/flows/ingest_recap_reanalysis.py`), and
`ingest_weather_history` is MeteoSwiss-only
(`src/sapphire_flow/flows/ingest_weather_history.py`); neither persists
Recap ERA5-Land meteorological reanalysis for these stations. Plan 139's W3/W4 describes a
similar path for test HRU 12300; this plan delivers the DHM six-station use and
reusable ingestion needed here without changing Plan 139's 12300 scope. It must
never use the Swiss CAMELS flow as a shortcut.

## Decisions and boundaries

### D1 — Reuse the accepted package importer and Plan 268 station rows

Resolve Plan 268's existing station rows by `(code, network)` and verify every
row belongs to `chwrr`; do not create a second row for the same DHM gauge.
Use an explicit CHWRR-scoped run principal and enforce tenant isolation before
any station, basin, binding, forcing or target write. Reuse the deployment-only
`config/overlays/chwrr-import.toml`; neither the Swiss default nor a global-admin
identity is the routine writer. Build and validate a
basin/static package using the existing contract and importer (Plans 117/120).
The package has one `gateway_hru_name`, `nepal6_20260923`, and six basin features
whose `name` values match the Gateway columns exactly: `g_447`, `g_450`,
`g_604_5`, `g_647`, `g_670`, and `g_684` for station codes 447, 450, 604.5,
647, 670, and 684. Importing it must produce one basin-average binding per
station. Persist a matching `StationWeatherSource` with `nwp_source="era5_land"`,
role `REANALYSIS`, status `ACTIVE`, and basin-average spatial representation.
This activates a source binding for explicit history retrieval, not the station
lifecycle. Preserve any existing forecast binding; creating an IFS binding and
activating scheduled forecasts are outside this plan.

**Existing snow schedule:** `ingest-recap-reanalysis` also selects active
`era5_land` reanalysis bindings, including onboarding stations. Before T2 commits
the bindings, the staging orchestrator must set that deployment's existing
`station_ids` parameter to the explicit previously intended snow-station list,
excluding these six gauges (`[]` if none; never `None`, which selects all).
No queued or running unscoped snow run may overlap the binding import. Preserve
snow service for the previously intended stations and verify the effective
deployment parameters after any deployment re-registration. This is an operator
precondition using the existing flow parameter, not a new snow implementation.
If the scope cannot be established, hold T2 before writes. A past
`source_data_missing` response is not protection: data may become available.

The user owns all Gateway-side shapefile registration and subscription actions.
This plan consumes the existing registration and adds no Gateway admin API,
upload, or subscription automation.

### D2 — Keep data roles and station lifecycle explicit

**Owner decision, 2026-09-29: discharge first.** Qualify the existing
`MANUAL_IMPORT` / `discharge` rows carrying Plan 268's delivery ID. Do not create
another observation series, invert curves, change timestamps, or relabel the
measurements as derived. Use only persisted `QC_PASSED` targets, matching
`services/training_data.py::assemble_station_training_data`. Report RAW,
unchecked, suspect, failed and missing coverage separately; none counts as
usable target history for this gate.

Use an explicitly selected discharge model's declared inputs, cadence and
training window for the readiness check, without assigning or training it.
Keep the current resampling contract and Plan 268's provisional Nepal day
boundary; this plan does not resume the halted time-grid work. Verify actual
forcing/target overlap after the existing resampling path, not merely matching
date ranges. If it cannot satisfy the chosen model, report a hold.

Set `forecast_targets` to `frozenset({"discharge"})` only after usable history,
forcing and static requirements pass.
Every station remains `onboarding`. A missing live feed or current rating curve
is reported as a later operational limitation, not a reason to discard historical
discharge training data.

Scope authority is the recorded owner approval of a real-gauge pilot in
Plan 268 D9 and the 2026-09-29 discharge-first decision above. This plan performs
data preparation and qualification only; it neither trains models nor publishes
outputs. There is no runtime training-permission field or new approval flag to
implement or test. Plan 268 D5/D9's unresolved model-output publication question
remains a later release decision, recorded in the handoff rather than used as
a technical readiness predicate. Restricted observations remain withheld (D5).

### D3 — Separate raw Gateway availability from supported model input

The initial supported forcing set is only what the Recap adapter and model
contracts actually support. Raw `ssr`/`str` retrieval does not authorize storing
them as canonical radiation, because the Gateway strips source unit and temporal
metadata and the current adapter intentionally excludes them. Snow is optional
only when the selected model does not require it.
If a selected model requires radiation or snow, onboarding must report that
requirement as unmet and hold that model/station combination; it must not invent
units, silently omit required inputs, or claim forecast readiness.

### D4 — Reuse delivery QC; do not enter generic onboarding

Plans 264/269 and 272/316/317/318/324 have landed. Plan 268's full-history
discharge QC also landed and handles gaps by Nepal local date, including the
1986 timezone change. It neither needs Plan 304's short-window context nor
closes Plan 315's generic onboarding gap.

`services/onboarding.py` still selects QC by forecast target, passes no station
overrides, and aggregates empty flags without first establishing that a rule ran.
Plan 315 owns the latter behavior and still depends on DRAFT Plans 303 and 313;
Plan 303 itself waits for Plan 301's source cadence contract. Plan 304 requires
its context contract to be reconciled into Plan 315 before READY. Those follow-on
dependencies are not work completed by the DHM import.

These plans remain necessary for their own callers, but are removed from this
plan's dependency list because its revised T4 writes no observations or QC
verdicts and never calls generic onboarding. T8 acceptance must establish that
Plan 268's delivery QC ran with the intended rules and per-station ceilings.
T4 reads those persisted verdicts and holds unqualified stations. A required
QC rerun uses the existing CHWRR delivery command, not a new checker wrapper.

### D5 — Preserve restricted lineage and qualify the current delivery

Leave observation values, source, delivery IDs, curve references and QC verdicts
unchanged. Keep synthetic fixtures in the repository and report only counts,
coverage and hold reasons from real data; QC details can contain individual values.
Do not expose the delivery through a consumer/reviewer API or readiness report.

Serialize T4's database qualification and target assignment with Plan 268's
existing tenant-row lock so replacement/QC cannot change the cohort midway.
Readiness is evidence for that software/configuration and delivery state, not a
permanent certification: after any replacement or QC rerun, repeat T4/T5 before
model onboarding. Repeated qualification clears this plan's discharge target
when its gates no longer pass, keeps the station in onboarding, and never
overwrites an unexpected pre-existing target or active station/model assignment.

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
- Rating-table inversion, derived water-level history, datum reconciliation or
  level-to-discharge conversion of live observations.
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
names, and the correct `dhm` network; each resolved station belongs to `chwrr`.
Record the selected discharge model, declared input/static requirements,
cadence and historical training window before T3/T4. The manifest and package contain no
individual DHM discharge or rating-table values. Owner spot-checks the station
identity/geometry joins before any database write. Run
`uv run pytest tests/unit/services/test_basin_package_loader.py tests/unit/types/test_basin.py`.

**Pre-change:** N/A — input/package contract and review gate; no behavior change.

### T2 — Import the six basins and persist polygon and reanalysis bindings

**Outcome:** all six Plan 268 station rows resolve to one imported basin each,
with the exact `nepal6_20260923` / `g_<code>` polygon mapping and the D1
`era5_land` reanalysis source binding.

**In:** the reusable `import_loaded_basin_package` core and its tests; a
Nepal-specific operator command that validates the package then calls the core
and existing station-store weather-source writer in one caller-owned transaction.
Require explicit `chwrr` authority and the exact six-station scope. Offer a
dry run that exercises writes/read-back and rolls back. Treat a rejected or held
package report as a failed six-station import; do not commit a partial set.
Require the orchestrator's D1 snow-scope check before the writing invocation;
the rollback dry run alone cannot establish what a concurrent scheduled run sees.
**Out:** edits to Plan 268, duplicate station creation, Gateway-side changes.

**Verification:** focused importer and resolver tests assert the six station
codes map one-to-one to the six polygons, including `604.5` → `g_604_5`; a
staging read-only audit confirms six station basin links, six polygon mappings
and six active reanalysis bindings in `chwrr`. Tests reject another tenant's
station, missing scope and conflicting bindings before mutation; an injected
failure rolls back the package and source writes together. A repeated import is
idempotent and preserves other source bindings. The generic CAMELS deployment is not
run. Run `uv run pytest tests/integration/services/test_basin_importer.py tests/integration/store/test_basin_importer_persistence.py tests/integration/store/test_basin_importer_idempotency.py`.
Add a regression in `tests/unit/flows/test_ingest_recap_reanalysis.py` proving
the explicit prior station list excludes the six new bindings even when their
snow data is available, while preserving selection of existing snow stations;
also cover the empty list. The operator records the effective deployment
parameter and absence of overlapping unscoped runs before the import.

**Pre-change:** the existing package core writes a polygon mapping but no
weather-source binding. A synthetic package imported through that core leaves
`fetch_reanalysis_bindings` empty. The new operator-path test must turn that
case into six retrievable bindings and reject missing/wrong/duplicate mappings.

### T3 — Persist supported Nepal historical forcing

**Outcome:** a Recap ERA5-Land history path resolves the six persisted bindings
and stores supported historical variables in `historical_forcing` with
per-station provenance. The Recap adapter can fetch precipitation and
temperature reanalysis (`RECAP_VARIABLES` and
`RecapGatewayReanalysisAdapter.fetch_reanalysis`); the existing
`ingest-recap-reanalysis` flow handles snow only.

**In:** a bounded Recap ERA5-Land ingestion flow using the existing adapter,
resolver, and historical-forcing store; Nepal deployment configuration and
focused tests. Include `precipitation` and `temperature` using their verified
ERA5-Land names. Record the model's declared requirements; do not add radiation
until its unit and temporal semantics are accepted and represented by the
adapter. Use explicit start/end dates and bounded request windows; support
idempotent retries through the existing store. Select the six `chwrr` stations
in `onboarding` explicitly, not an operational-only or fleet-wide selector.

Preserve the adapter's `recap_era5_land_reanalysis` source, per-row version,
UTC timestamps and `[start, end)` window; exclude forecast-fill rows. The
binding's `era5_land` name is not the persisted source tag and must not be
substituted for it or for `era5_land_sloth_dynamic`. The existing
`StoreBackedReanalysisSource` queries by the binding name and therefore cannot
read these records unchanged. Provide an explicit source mapping for this
readiness path, preserving existing mappings, and verify persisted data can be
read through `WeatherReanalysisSource` without a fresh Gateway fetch. No global
Swiss reanalysis configuration change is allowed.
**Out:** another Gateway client, snow processing (handled by the existing
snow-only flow), IFS operational forecasting, raw-value dumps, forecasts.

**Verification:** unit tests cover the six bindings and canonical source/parameter
mapping, partial polygon responses, request boundaries, forecast-fill exclusion
and retry idempotency. PostgreSQL tests prove source/version read-back and the
store-backed read through the real `era5_land` bindings. A bounded staging import
reports per-station row counts, date coverage,
and source provenance only; no forcing values are logged or exported. The gate
fails when a model-required variable has no supported adapter mapping or no
usable records; one station's rows cannot satisfy another station's gate. JSNOW
absence is reported as optional only if the selected model does not require it.
Run `uv run pytest tests/unit/flows/test_ingest_recap_era5_reanalysis.py tests/unit/adapters/test_recap_gateway.py tests/unit/adapters/test_store_backed_reanalysis.py tests/integration/store/test_historical_forcing_store.py`.
The ERA5 ingestion test module is new; add PostgreSQL coverage for the source
mapping alongside the existing historical-forcing store integration tests.

**Pre-change:** the live client can retrieve source data, but no flow persists
Recap ERA5-Land meteorological reanalysis to `historical_forcing`. Capture the
bounded baseline counts and assert the new path creates six-station records
without changing Swiss counts or assignments.

### T4 — Qualify imported discharge history and assign the target

**Outcome:** each station has `forecast_targets=frozenset({"discharge"})` only
when its imported QC-passed discharge, static attributes and persisted forcing
satisfy the explicitly selected model/window. Every other station has an unset
target and a specific hold reason. All stay in `onboarding`.

**In:** a bounded Nepal readiness service/operator path consuming Plan 268 T8's
acceptance and the existing stores, resampling and model requirement types.
Check the exact restricted delivery cohort, persisted QC versions and usable
sample/overlap requirements. Perform the
database checks and target writes in one CHWRR-authorized transaction with the
tenant-row lock; provide a rollback dry run.
**Out:** observation or QC writes, curve conversion, generic CAMELS onboarding,
new QC rules, model training/assignment, station promotion or live-feed activation.

**Verification:** synthetic tests include adequate QC-passed daily discharge,
only RAW/unchecked/suspect/failed rows, inadequate overlap, missing required
static/forcing inputs, wrong tenant/delivery, and an unexpected
existing target or operational station. Excluding unqualified rows must leave
too-short histories held. Show that an expired/missing historical curve does
not disqualify directly delivered discharge. Verify target/forcing alignment
through existing resampling, including the Nepal day boundary, gaps and the
1986 timezone transition, without restamping the source observations.

PostgreSQL tests prove idempotent assignment, clearing a previously assigned
discharge target when requalification fails, rollback on error, and serialization
with delivery replacement/QC. Read back unchanged observation values, provenance
and QC statuses. In staging, report usable counts and overlap, never values.
Run the new `tests/unit/services/test_nepal_onboarding_readiness.py` and
`tests/integration/services/test_nepal_onboarding_readiness.py`, plus
`tests/unit/services/test_training_data.py` and
`tests/integration/cli/test_import_dhm_delivery.py` with `uv run pytest`.

**Pre-change:** Plan 268 deliberately leaves targets unset, and no Nepal
qualification path assigns them. A synthetic accepted delivery must yield a
discharge target through the new path; an all-unchecked history must stay held.

### T5 — Hand off station readiness without activating forecasts

**Outcome:** the operator has a per-station readiness report for model
onboarding, and the staging records remain in the `onboarding` lifecycle state.

**In:** `docs/operations/nepal-station-onboarding-runbook.md` and checks for basin/binding, supported
forcing, target-history, QC, tenant, and unmet model-input requirements.
**Out:** model assignment, training, hindcast, operational promotion, schedule
registration, alerts.

**Verification:** the report distinguishes ready-for-model-onboarding from
forecast-operational. All six gauge codes appear exactly once; each failed gate
names its reason. It identifies the selected model/window, persisted forcing
source and QC versions, and separates historical readiness from current live
input/rating limitations. The handoff cites the D2 scope decisions and records
the unresolved model-output publication question separately from computed
readiness; a reviewer token is not permission to publish restricted data.
A database audit confirms no station was promoted and no
forecast/model assignment was created. Do not trigger the existing
`onboard-stations` deployment, whose defaults point at CAMELS-CH. Run
`uv run pytest tests/unit/services/test_nepal_onboarding_readiness.py`.

**Pre-change:** documentation is N/A; the new readiness-report tests must fail
before implementation when a requested station is omitted or a held station is
reported ready. They exercise the public report, not private implementation state.

## Exit condition

Plan 143 completes when each gauge has a correct station-to-basin-to-Gateway
mapping and reanalysis binding, supported historical forcing, and either a
qualified discharge target or a specific hold reason. T1–T5 require synthetic
tests plus the stated aggregate staging read-back; a merged patch alone is not
acceptance. Completion does not mean that a model is trained, that forecasts
are being produced, or that a station is operational.

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
