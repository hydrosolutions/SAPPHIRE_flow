# Nepal DHM station onboarding runbook

Repeatable procedure for preparing new DHM stations for a Nepal test deployment.
It records the current six-station example and separates Gateway registration,
which DHM/hydrosolutions operators perform, from SAP3 ingestion and readiness
checks. It does not activate forecasts or grant operational status.

## Current status and safety boundary

The first batch is the Gateway HRU `nepal6_20260923`, containing gauges
`447`, `450`, `604.5`, `647`, `670`, and `684`. The Gateway polygon names are
`g_447`, `g_450`, `g_604_5`, `g_647`, `g_670`, and `g_684`, respectively.
The code `604.5` is normalized to `g_604_5` for the polygon feature name.

The six-basin GeoPackage and static-attributes table have been inspected
against the package loader: there is one valid EPSG:4326 basin per gauge, the
`gauge_id` join is one-to-one and complete, and the six geometries and static
rows are present. Gateway requests have also returned ERA5-Land historical,
IFS forecast, and operational stitched values for `tp`, `2t`, `ssr`, and
`str`. Those checks establish source availability; they do not establish that
SAP3 persists every variable or can use it in a model.

The SAP3 onboarding sequence is not yet complete. Plan 143 and its upstream
station/QC dependencies must be implemented and accepted before the steps in
“Run SAP3 onboarding” can be executed. In particular, do not trigger the
parameterless `onboard-stations` deployment: its defaults select CAMELS-CH
stations. Do not mark DHM stations operational or create forecast targets as
a shortcut around the readiness gates.

## 1. Prepare the station and Gateway inventory

For each new batch, maintain one reviewed inventory with these fields:

| Field | Meaning |
|---|---|
| DHM gauge code | The authoritative station code used to match the DHM record (keep dots, e.g. `604.5`). |
| SAP3 network | `dhm`. |
| River Watch station ID | Confirm against CHWRR/DHM's River Watch interface; do not assume a BIPAD ID is the same. |
| Gateway HRU name | Exact registered shapefile/HRU name returned by the Gateway operator. |
| Gateway polygon name | Exact `name` value returned by the Gateway for this gauge. |
| Basin/static package ID | A new immutable ID for this release of geometry and attributes. |
| Registration/data check | Date and evidence of polygon registration and requested source availability. |

Ask the Gateway operator to register the batch GeoPackage and confirm the
returned HRU and polygon names. A batch may be one HRU with multiple polygon
features; the HRU name is not an individual gauge code. Record the exact
returned identifiers in the inventory. Do not infer names from the station
code when the Gateway can confirm them.

For the initial batch, the verified mapping is:

| DHM gauge | Gateway polygon |
|---:|---|
| 447 | `g_447` |
| 450 | `g_450` |
| 604.5 | `g_604_5` |
| 647 | `g_647` |
| 670 | `g_670` |
| 684 | `g_684` |

## 2. Assemble and validate the basin/static package

Obtain a basin GeoPackage and static-attribute Parquet from the basin
modeller. Build the package using the contract in
[`04-basin-static-artifact-contract.md`](../requirements/04-basin-static-artifact-contract.md)
and the existing [basin/static package importer runbook](basin-static-importer-runbook.md).
The package includes `manifest.json`, `basins.gpkg`,
`static_attributes.parquet`, `feature_catalog.json`, and
`validation_report.json`; include `README.md` and checksums as required by the
contract.

Before import, verify all of the following:

- `network` is `dhm` and the package ID is unique and immutable.
- Every intended gauge occurs exactly once in both basin features and static
  attributes; `gauge_id` joins the two files without missing or extra rows.
- Each feature is a valid 2-D Polygon/MultiPolygon in EPSG:4326.
- The feature name is exactly the Gateway polygon name, including normalized
  names such as `g_604_5`.
- The package declares the correct Gateway HRU name and the six-station
  inventory agrees with its contents.
- The package loader and semantic validation report pass with no unexplained
  errors or warnings.

For the initial package, the Gateway HRU name must be `nepal6_20260923`.
The delivered source package previously declared a placeholder HRU name, so
check the current package metadata and correct it in a derived package copy
before import; do not edit the original handover files in place. Store the
resulting package and validation report in the deployment's controlled
artifact location, not in a personal temporary directory.

## 3. Confirm station rows exist before importing basins

The basin importer resolves each package gauge to an existing SAP3 station by
`(code, network)`. Confirm the six station records have been created by the
DHM station-import process and use `network=dhm`. If a station is missing or
has a different code/network, stop and correct the station import or package
through its owner. A held basin is a visible readiness outcome; do not work
around it by creating a duplicate station.

Once Plan 143 and its dependencies are implemented and approved, run the
existing importer against the reviewed package and the intended Nepal
database, following the command and report guidance in the basin/static
importer runbook. Review the full report. Confirm each intended basin is
`imported` or already current, and resolve every `onboarding_held` reason
before continuing. Retain the package ID, checksums, command context, report,
and database environment in the onboarding record. Do not rerun a modified
package with an already-used package ID; issue a new package ID for corrected
content.

## 4. Verify Gateway source coverage

After polygon registration, probe the exact HRU and polygon names for each
required variable and record the request window, returned coverage, and data
gaps. For the six-station batch, the measured Gateway availability includes:

- ERA5-Land historical meteorology: precipitation (`tp`) and 2 m temperature
  (`2t`) are the initial SAP3-supported forcing variables.
- IFS and operational stitched forcing: availability was observed for `tp`,
  `2t`, surface net solar radiation (`ssr`), and surface net thermal radiation
  (`str`). Availability alone does not make `ssr`/`str` supported SAP3 model
  inputs; the adapter's unit/time interpretation must be explicitly supported
  first.
- Snow variables: treat these as unavailable for this HRU until a fresh probe
  confirms that Gateway processing is complete and values are returned for
  these polygons.

Use the [Recap Gateway runbook](recap-gateway-runbook.md) for API-key handling,
historical back-extraction, and coverage recording. Never place API keys in
the inventory, command history, or onboarding report. A successful read-only
probe does not mean historical values have been persisted in SAP3; the
Nepal-specific historical forcing persistence step is delivered by Plan 143.

## 5. Run SAP3 onboarding after its implementation is available

Do this only when the orchestrator has made the onboarding implementation
available and its documented invocation explicitly scopes the intended DHM
stations and Nepal database. The implementation must perform the basin/binding
checks, persist supported historical forcing, assess eligible water-level
targets and QC, and produce a per-station readiness report. Until then, this
section is a gate, not an executable command sequence.

For each batch:

1. Confirm the selected database is the Nepal test deployment and the station
   scope is the reviewed list of DHM gauge codes. Record the deployment and
   software version.
2. Confirm station-to-basin and basin-to-Gateway bindings resolve one-to-one
   for every station. Stop on a missing, duplicate, or unexpected mapping.
3. Back-extract supported ERA5-Land history for the model's declared training
   window. Record the actual persisted time span, variables, gaps, and source
   provenance. Do not infer coverage from the requested window.
4. Keep radiation or snow requirements unmet unless their SAP3 support and
   actual coverage have both been established. Hold any model requiring an
   unmet variable.
5. Keep the forecast target unset unless authorized, QC-qualified
   water-level history is available. Rating-derived stages must have an
   approved, datum-compatible rating curve covering the discharge and dates;
   label them as derived equivalents, never as original DHM level
   observations. Otherwise record the hold reason and proceed only with tasks
   that do not require a target.
6. Review the readiness report and database audit. Every requested station
   must appear exactly once with its gate outcomes. Stations remain in the
   `onboarding` lifecycle state; this procedure does not assign models,
   schedule forecast production, or activate alerts.

## 6. Handover for a new station batch

Repeat this runbook for each new batch. Treat Gateway registration and basin
packages as versioned inputs. A geometry or static-attribute correction gets
a new package ID; retain the prior package and import report for traceability.
Recheck Gateway polygon names, station identifiers, variables, actual
coverage, and the model's required training window for every batch. Share the
inventory, accepted package, validation/import reports, coverage record, and
readiness report with the designated CHWRR/DHM counterparts through the
approved project location. Do not share credentials or data beyond the
project's agreed data-use permissions.

## Related procedures

- [Basin/static package importer runbook](basin-static-importer-runbook.md)
- [Recap Data Gateway operations runbook](recap-gateway-runbook.md)
- [Plan 143 — DHM v1 station, basin and Gateway onboarding](../plans/143-dhm-v1-basin-gauge-onboarding.md)
