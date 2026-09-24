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

The package checks establish structural validity, not that each input gauge
coordinate was independently verified against CHWRR/DHM records. Complete the
coordinate confirmation below before generating replacement geometry or
onboarding additional stations; if a coordinate correction changes a basin,
issue a new geometry/package version and register the corrected geometry.

The SAP3 onboarding sequence is not yet complete. Plan 143 and its upstream
station/QC dependencies must be implemented and accepted before the steps in
“Run SAP3 onboarding” can be executed. In particular, do not trigger the
parameterless `onboard-stations` deployment: its defaults select CAMELS-CH
stations. Do not mark DHM stations operational or create forecast targets as
a shortcut around the readiness gates.

## 1. Confirm station identity and coordinates

Before delineating any basin, prepare one reviewed station inventory. Keep the
DHM gauge code distinct from BIPAD and River Watch identifiers. Confirm the
River Watch gauge ID and coordinates with CHWRR/DHM; public BIPAD station
records are a useful cross-check, but their station IDs may not be the same as
the River Watch IDs. Record the source and date for each coordinate and have a
second person verify the gauge identity, decimal-degree latitude/longitude,
and that latitude and longitude have not been swapped. Do not start the
appliance from an unverified coordinate or resolve a discrepancy by choosing
the point that produces the more convenient basin.

The static-attributes appliance expects latitude and longitude in WGS84
decimal degrees. Its delineator snaps the supplied pour point onto a river
cell, so the resulting outlet is not necessarily the input coordinate. For
each gauge, compare the candidate coordinate and resulting basin with the
station's river identity and a trusted drainage-area value when one is
available. The appliance documentation reports where `distance-first` and
`weight-first` snapping produce different catchments; use that audit as a
review aid, not as proof that either coordinate is correct.

For each new batch, the inventory should include:

| Field | Meaning |
|---|---|
| DHM gauge code | The authoritative station code used to match the DHM record (keep dots, e.g. `604.5`). |
| SAP3 network | `dhm`. |
| River Watch station ID | Confirm against CHWRR/DHM's River Watch interface; do not assume a BIPAD ID is the same. |
| Coordinate | Reviewed latitude and longitude in WGS84 decimal degrees. |
| Coordinate evidence | Authoritative source, cross-check source, reviewer, and review date. |
| Published drainage area | Area and source used to review the delineation, when available. |
| Basin/static package ID | A new immutable ID for this release of geometry and attributes. |
| Gateway HRU name | Exact registered shapefile/HRU name, recorded after Gateway registration. |
| Gateway polygon name | Exact returned feature `name`, recorded after Gateway registration. |
| Registration/data check | Date and evidence of polygon registration and requested source availability. |

The initial six-gauge batch's coordinates must be checked from the approved
station source before running the appliance. Do not treat coordinates embedded
in an old basin package or an example command as the authority for new runs.

## 2. Delineate basins and build the static-attributes package

Use the [static-attrs-nepal README](https://github.com/hydrosolutions/static-attrs-nepal#headless-batch-cli)
for the supported appliance route and full user instructions. The bundle route
requires a real amd64 Linux host; running from source is the documented route
for macOS. Its headless CLI accepts repeated
`--station CODE "DISPLAY NAME" LAT LON` arguments. From source, the documented
command shape is:

```bash
uv run python -m static_attrs_nepal.appliance \
  --station DHM_CODE "DISPLAY NAME" LATITUDE LONGITUDE \
  --snap-audit \
  --hfx-dataset-dir "$HFX_DATASET_DIR" \
  --hydroatlas-clip unused \
  --era5-land-cube unused
```

Run the snap audit with the reviewed coordinate set before building the
package. Review every disagreement and compare each selected basin with the
known river and drainage area. Resolve any uncertain coordinate or catchment
with CHWRR/DHM before proceeding. The appliance README documents
`distance-first` as the default based on its Nepal published-area sweep; do
not switch strategies without reviewing the expected catchment change.

Once coordinates and basins are accepted, run the same CLI with all gauges,
without `--snap-audit`, and with real local dataset paths, explicit network
and the intended Gateway HRU name, and a versioned output path. Use one
`--station` argument for each reviewed inventory row:

```bash
uv run python -m static_attrs_nepal.appliance \
  --station DHM_CODE "DISPLAY NAME" LATITUDE LONGITUDE \
  --network dhm \
  --gateway-hru-name INTENDED_HRU_NAME \
  --hfx-dataset-dir "$HFX_DATASET_DIR" \
  --hydroatlas-clip "$HYDROATLAS_CLIP" \
  --era5-land-cube "$ERA5_LAND_CUBE" \
  --output VERSIONED_PACKAGE_ZIP
```

Repeat `--station` for every gauge. Follow the appliance's current README for
the complete installation and package-validation procedure; do not copy
example coordinates into a live batch. Save the generated package and its
validation report as controlled deployment artifacts.

The appliance output is the source for the basin geometry and static
attributes; do not independently redraw a basin or substitute a different
polygon after validation. Validate the generated package using the contract
in
[`04-basin-static-artifact-contract.md`](../requirements/04-basin-static-artifact-contract.md)
and follow the [basin/static package importer runbook](basin-static-importer-runbook.md)
for SAP3 package validation. The package includes `manifest.json`, `basins.gpkg`,
`static_attributes.parquet`, `feature_catalog.json`, and
`validation_report.json`; include `README.md` and checksums as required by the
contract.

Before Gateway registration, verify the geometry, attributes, and package
structure. At this point the HRU and polygon names are proposed values; confirm
them with the Gateway operator before treating them as registered identifiers.
Verify all of the following:

- `network` is `dhm` and the package ID is unique and immutable.
- Every intended gauge occurs exactly once in both basin features and static
  attributes; `gauge_id` joins the two files without missing or extra rows.
- Each feature is a valid 2-D Polygon/MultiPolygon in EPSG:4326.
- The feature name follows the Gateway naming convention and maps one-to-one
  to its gauge; confirm the exact returned Gateway name after registration.
- The proposed Gateway HRU and feature names are recorded as provisional until
  confirmed by the Gateway operator.
- The package loader and semantic validation report pass with no unexplained
  errors or warnings.

Generate and validate the basin geometry first, then register that geometry
with the Gateway before finalizing the confirmed names in the SAP3 package
metadata.

## 3. Register the accepted geometry with the Data Gateway

After the basin geometries pass coordinate and delineation review, follow the
Gateway team's [new-geometry onboarding instructions](https://github.com/hydrosolutions/recap-dg-client/blob/main/docs/onboarding-a-new-geometry.md).

The procedure has four Gateway-side stages:

1. **Upload the GeoPackage.** In the Gateway Swagger page at
   `https://recap.ieasyhydro.org/sdk/docs`, authorize with the Gateway API key
   and use `POST /hru/vector-files/upload`. Upload one `.gpkg` in WGS84
   longitude/latitude degrees (EPSG:4326). Supply a fresh `hru_code`, an
   optional description, and the file. The `hru_code` becomes the permanent
   geometry name; it cannot be reused for another geometry. Do not put the API
   key in this runbook or onboarding inventory.
2. **Verify it is ready.** In the Configurator at
   `https://recap.ieasyhydro.org/admin`, open the Shapefiles list and confirm
   the new geometry appears with status `Ready`. If it is absent or not ready,
   stop and resolve the upload/CRS problem before enabling data.
3. **Enable subscriptions.** In the Configurator, add and save the required
   ERA5, IFS, and (if needed) Snow subscriptions for the new geometry. Select
   variables required by the intended model and leave the enable toggle on.
   For the current SAP3 forcing path, ERA5 precipitation and 2 m temperature
   are supported; select the corresponding IFS surface variables required by
   the deployment. Do not treat raw Gateway availability of radiation or snow
   as SAP3 model support. Snow has separate forecast and reanalysis toggles;
   enable the stream(s) actually required. If a needed variable is not listed,
   ask a Recap administrator to add it before continuing.
4. **Populate Gateway data.** In the Jobs dashboard at
   `https://recap.ieasyhydro.org`, trigger one manual run of
   `era5_land_backfill__targeted` using “Trigger DAG w/ config”. Enter
   `shapefile_names` as the new `hru_code`, and set `year_start` and
   `year_end` to the required calendar-year training window. `variables` may
   list the subscribed ERA5 variables one per line, or be left empty to fill
   every subscribed variable. At least one ERA5 subscription must already be
   enabled. This is one run for the selected geometries, variables, and years;
   it is not an Airflow date-range backfill.

   Then backfill the recent IFS forecasts: open `ifs__subdaily`, choose
   **Backfill**, set the date range to the last seven days, enable
   reprocessing of existing runs, and start it. The backfill includes all
   subscribed geometries and variables in that range, so do not start a
   separate run per variable. This is a shared, broad reprocessing operation,
   not a station-scoped action: before triggering it, review which geometries
   and variables are subscribed to `ifs__subdaily`, confirm with the Gateway
   operator which outputs in the seven-day window will be overwritten, and
   coordinate a run window with the owners of any other affected deployments.
   If the affected scope or overwrite is not confirmed and authorized, stop;
   do not trigger the backfill. The Gateway guide says repeated backfills
   overwrite the same outputs.

Retain the upload confirmation, readiness status, enabled subscriptions,
backfill run identifiers and requested years with the station inventory. If
the guide is not yet accessible to your team, request the current controlled
copy from the Gateway operator rather than improvising these steps.

Record the exact registered HRU name and every polygon `name` returned by the
Gateway. A batch may be one HRU with multiple polygon features; the HRU name
is not an individual gauge code. Do not infer the returned names from DHM
codes. For the initial batch, the expected HRU is `nepal6_20260923` and the
verified feature mapping is:

| DHM gauge | Gateway polygon |
|---:|---|
| 447 | `g_447` |
| 450 | `g_450` |
| 604.5 | `g_604_5` |
| 647 | `g_647` |
| 670 | `g_670` |
| 684 | `g_684` |

After backfills complete, confirm a read-only Gateway request succeeds for
the registered HRU and polygons. Record request date, variables tested,
returned coverage, unavailable values, and the Gateway job runs. Use the Recap
Gateway runbook for probe credentials and coverage recording. A successful
Gateway backfill is still separate from importing historical forcing into
SAP3.

## 4. Finalize and validate the SAP3 basin/static package

Once Gateway registration confirms the identifiers, write the final
`gateway_hru_name` and per-feature Gateway `name` values into the SAP3 package
metadata. Verify that `network` is `dhm`, that each basin feature's `name`
matches its Gateway polygon exactly, and that the `gauge_id` static-attribute
join is one-to-one and complete. Run the SAPPHIRE package loader and semantic
validation report. Do not edit the original handover files in place; create a
derived package with a new immutable package ID if metadata or contents need
correction. Store the package and report in the deployment's controlled
artifact location.

## 5. Confirm station rows exist before importing basins

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

## 6. Verify Gateway source coverage

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

## 7. Run SAP3 onboarding after its implementation is available

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

## 8. Handover for a new station batch

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
