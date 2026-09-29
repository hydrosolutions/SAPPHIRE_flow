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

Plan 268's historical delivery importer merged in PR #332. Its T8 staging
run on 2026-09-29 is orchestrator-reported in Plans 510/513 as an owner-approved
one-off, not independently verified here. Confirm against its retained Mac-mini
aggregate acceptance/QC evidence and confirm the six stations and persisted discharge
QC in tenant `chwrr` before proceeding. This is the existing shared Mac-mini
database, not a new Nepal database. The deployment session owns that acceptance.

Plan 143 is READY and its preparation commands are implemented. Real-package
joins, Plan 268 T8 and aggregate staging read-back remain acceptance gates.
For the Mac-mini, section 7 is not runnable until the runtime wrapper and
owner-approved execution role are recorded (section 9, steps 1 and 3), as well
as its data and snow-scope prerequisites. Do not trigger the
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
   longitude/latitude degrees (EPSG:4326). Extract the generated artifact and
   upload its `basins.gpkg` file; do not upload the artifact ZIP or the static
   attributes table. Supply a fresh `hru_code`, an optional description, and
   the file. The `hru_code` becomes the permanent geometry name; it cannot be
   reused for another geometry. Do not put the API key in this runbook or
   onboarding inventory.
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

Use `sapphire_flow.cli.onboard_nepal basins` below after Plan 268 T8 acceptance.
It enforces CHWRR write authority and atomically imports all six basin/polygon
mappings and reanalysis source bindings. Use the generic importer's runbook
for package validation guidance.

Before committing the bindings, have the staging orchestrator scope the existing
`ingest-recap-reanalysis` snow deployment's `station_ids` to its explicit prior
snow-station list, excluding the six new gauges (`[]` for none, not `None`).
Ensure no queued or running unscoped snow run overlaps the import, retain service
for existing snow stations, and verify the effective parameters again after
deployment re-registration. Active `era5_land` bindings otherwise enroll these
onboarding stations in scheduled snow ingestion too. A previous missing-data
response does not guarantee that future runs cannot write data. Hold the import
if this scope check is incomplete; a rollback dry run does not replace it.

Run the Nepal command's rollback dry run and inspect its full report before the
writing invocation. Confirm all six basins and both kinds of bindings are
imported or already current; any rejection or held basin must leave the whole
batch uncommitted. Retain the package ID, checksums, command context, report,
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

## 7. Run the six-station discharge preparation command

**Mac-mini hold:** do not run these examples until the runtime wrapper and
owner-approved execution role are recorded in the operator handoff (section 9,
steps 1 and 3). The approved wrapper replaces each example's `uv run` prefix
as appropriate for that runtime; keep the CLI arguments and process-local
configuration intact and verify all referenced paths inside that runtime.

Run these commands in the approved deployment environment with its existing
database and Recap secret configuration. `--config` points to the base config;
the command applies `SAPPHIRE_CONFIG_OVERLAY` in order. Use the existing
CHWRR-only identity overlay last; an admin identity or the Swiss default is
refused. `DATABASE_URL` selects the database. For this operator process, set:

```bash
export CHWRR_CONFIG=config.toml
export SAPPHIRE_CONFIG_OVERLAY=config/overlays/nepal-history.toml,config/overlays/chwrr-import.toml
```

Set `SAPPHIRE_RECAP_BASE_URL` to the deployment's accepted Gateway endpoint and supply
the existing API-key secret (`/run/secrets/sapphire_dg_api_key`, or the existing
`RECAP_API_KEY` environment fallback). The history overlay supplies adapter
settings only and does not change the forecast adapter or register schedules.
Keep these environment settings local to the operator process.
The command uses only the six codes listed above. The accepted basin package
comes from the existing Mac-mini handoff; synthetic test fixtures are not
staging inputs.

After the owner has checked the geometry joins, the deployment session has
recorded Plan 268 T8 acceptance, and the snow precondition in section 5 holds:

```bash
uv run python -m sapphire_flow.cli.onboard_nepal basins \
  --config "$CHWRR_CONFIG" --tenant chwrr --confirm-t8 \
  --package-dir "$ACCEPTED_PACKAGE_DIR" \
  --confirm-package-joins --confirm-snow-scope --dry-run
```

Review the result, then repeat without `--dry-run` to commit. The confirmation
flags acknowledge recorded operator checks; the CLI cannot inspect deployment
queues or verify geometry review. It refuses held or partially accepted packages.

Choose the installed discharge model, years, supported cadence and minimum
number of complete training windows when running the tool. Record its declared
weather and static inputs before retrieving history. Start and end are explicit
timezone-aware ISO timestamps; the end is exclusive.

```bash
uv run python -m sapphire_flow.cli.onboard_nepal history \
  --config "$CHWRR_CONFIG" --tenant chwrr --confirm-t8 \
  --start "$HISTORY_START" --end "$HISTORY_END"

uv run python -m sapphire_flow.cli.onboard_nepal qualify \
  --config "$CHWRR_CONFIG" --tenant chwrr --confirm-t8 \
  --start "$HISTORY_START" --end "$HISTORY_END" \
  --model "$DISCHARGE_MODEL" --time-step-hours "$STEP_HOURS" \
  --minimum-samples "$MINIMUM_SAMPLES" --dry-run
```

Repeat `qualify` without `--dry-run` after inspecting the aggregate report.
All three commands support rollback dry runs. History requests cover at most
31 days each and preserve the adapter's source and per-row version. Each batch
checks station state and bindings under the tenant lock, then commits separately
and releases the lock. A later failure preserves earlier committed batches;
repeating the same retrieval is safe. Dry runs roll back each batch. This command
calls the Prefect flow body in each transaction; it does not register or schedule
a deployment.

Qualification requires basin-average inputs, supported precipitation/temperature
features, required statics, and current-version QC-passed discharge from the
restricted delivery. It uses the existing resampling rules and counts contiguous
complete rows covering the model's lookback plus forecast horizon. The minimum
count is an explicit model-specific operator choice; it is not a skill score.
No historical rating curve is needed for directly delivered discharge.

Exit code 0 means the requested operation passed. Exit code 1 means a failure
or held coverage/readiness. A successful qualification with held stations
**commits cleared discharge targets** unless `--dry-run` is present; a raised
error rolls back the current transaction. For `history`, empty station/parameter
coverage in any batch returns exit code 1, including during a dry run.
Successfully stored data is preserved unless `--dry-run` is present. Rerun after filling the missing Gateway
coverage. History coverage counts alone do not establish
model readiness. The qualification report lists every gauge, hold reasons,
QC counts/versions, usable observations and complete windows, and overlap.
It contains no measurement values. Save the package ID/checksums, software and
configuration versions, model requirements, command options and aggregate
reports in the controlled handoff. Repeat qualification after delivery/QC changes.

The owner chose discharge-first onboarding on 2026-09-29. Stations remain in
onboarding with no assigned model. Live inputs, current rating availability,
and model-output publication remain separate release questions.

For each batch:

1. Confirm the selected database is the shared Mac-mini staging database, the
   write identity is scoped to `chwrr`, and all six reviewed DHM stations belong
   to that tenant. Record Plan 268 T8 acceptance and software/configuration
   versions. Select the discharge model and training window whose requirements
   the readiness check will assess; do not assign or train the model here.
2. Confirm station-to-basin and basin-to-Gateway bindings resolve one-to-one
   for every station. Also verify each `era5_land` / `REANALYSIS` source binding
   is active and basin-average; a polygon mapping alone is insufficient. Stop
   on a missing, duplicate, conflicting or cross-tenant mapping.
3. Back-extract supported ERA5-Land history for the model's declared training
   window. Record the actual persisted time span, variables, gaps, and source
   provenance. Verify the stored `recap_era5_land_reanalysis` rows can be read
   through the selected history reader and overlap the target history after
   resampling. Do not infer coverage from the requested window or confuse the
   source tag with the binding name or the separate Sloth ERA5-Land product.
4. Keep radiation or snow requirements unmet unless their SAP3 support and
   actual coverage have both been established. Hold any model requiring an
   unmet variable.
5. Qualify Plan 268's delivery-tagged, manual-import discharge using persisted
   `QC_PASSED` rows only. Set the target to `discharge` only when model input,
   overlap and static-input gates pass. RAW, unchecked, suspect and
   failed rows do not count as usable targets. Preserve the measurements,
   delivery tags and QC verdicts; do not invert curves or rerun generic
   onboarding QC. Keep unqualified targets unset and report the hold reason.
   Repeat qualification after delivery replacement or QC changes. The readiness
   report contains aggregates only; consumer and reviewer access to restricted
   observations remains withheld.
   Plan 268 D9's pilot approval and Plan 143 D2's discharge-first decision are
   the recorded scope authority; no runtime training-permission flag exists or
   is required here. Record the unresolved model-output publication question
   from Plan 268 D5/D9 in the handoff; this procedure does not authorize publication.
6. Review the readiness report and database audit. Every requested station
   must appear exactly once with its gate outcomes. Stations remain in the
   `onboarding` lifecycle state; this procedure does not assign models,
   schedule forecast production, or activate alerts. Record live-feed and
   current-rating limitations separately: historical training readiness is not
   proof that the operational input path is ready.

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

## 9. Mac-mini handoff — next visit

**Owner:** the staging orchestrator / `cmal_small` deployment session. Code is
in [PR #344](https://github.com/hydrosolutions/SAPPHIRE_flow/pull/344); this handoff
does not merge or deploy it. Plan 143 remains READY until its staging checks
are recorded in the plan. Use the procedure in section 7; this section identifies
what must be resolved on the host before running it.

**Current hold:** the execution runtime and database role are not yet established
for Plan 143. The repository's worker role cannot take the required tenant lock;
the known owner role can, but using it requires a fresh owner decision. Resolve
steps 1 and 3 before treating the section 7 command examples as runnable on the
Mac-mini. The prior delivery run is not permission to reuse its credential.

1. **Use the approved build.** Wait for PR checks and owner approval/merge, then
   have the staging session deploy the merged revision under the existing
   [Mac-mini deployment procedure](mac-mini-deploy-runbook.md). Record the
   actual deployed SHA. Run host/Compose operations from the persistent checkout
   `/Users/sapphire/SAPPHIRE_flow`, never this development worktree under `/tmp`.
   **Runtime is an unresolved check:** section 7 uses a checkout `uv` environment,
   but the deployed database is reached on the Compose network; its `postgres`
   hostname is not a host-side connection recipe. Do not expose a database port
   to make the example work. Have the staging session choose an approved runtime
   and record the exact execution wrapper before writes. In that runtime, verify
   the selected model and CLI are installed, the accepted package and base config
   are readable, both `chwrr-import.toml` and `nepal-history.toml` are available,
   the process has the authorized `DATABASE_URL`, and the Gateway secret is
   available by the supported mechanism. `/run/secrets/...` is a container path,
   not a host path. Check `python -m sapphire_flow.cli.onboard_nepal --help` there.

2. **Reuse the existing inputs.** Plans
   [510](../plans/510-operator-database-role-for-imports.md) and
   [513](../plans/513-declared-tenants-created-at-deploy.md) record an orchestrator
   report that Plan 268 T8 ran on 2026-09-29; this is not host verification.
   Obtain its retained aggregate acceptance and QC record
   from the deployment session. Use the six-catchment package already handed to
   that session; confirm its immutable package ID, checksums and owner-reviewed
   station/geometry joins. Do not replace the restricted delivery again simply
   to execute Plan 143. Verify six `chwrr` / `dhm` stations remain in onboarding,
   with no active model assignment and targets unset or discharge-only.

3. **Resolve the database execution role before writes.** The CHWRR config
   identity is an application check; it does not grant database privileges.
   Every command takes `SELECT ... FOR UPDATE` on the tenant row. The execution
   role also needs the importer’s basin/package/polygon/source-binding writes,
   historical-forcing inserts and station updates for targets and basin links.
   The worker role lacks the tenant UPDATE privilege needed for that lock.
   Plan 510 is still READY; its proposed delivery-operator role is not implemented
   by this PR and is not proof of authority for basin imports or target updates.
   The exit path is a fresh owner decision on an authorized execution role, or
   the planned advisory-lock change plus verification of a role's required writes.
   Plan 510 landing alone is insufficient: verify all delivery and onboarding
   callers use the same lock mechanism and deployed version before overlapping
   jobs. The orchestrator must supply an authorized execution role
   for these commands and verify its effective permissions and selected database.
   If none is available, hold here and resolve it through the owner. The earlier
   T8 one-off owner-credential approval does not authorize a new owner-credential
   run. Do not change grants or copy credentials into this handoff.

4. **Confirm the existing snow deployment's scope.** Preserve its prior station
   list, explicitly excluding these six gauges (`[]` if none, never `None`).
   Confirm no queued/running unscoped run can overlap the basin/source import.
   Recheck the effective parameter after deployment registration. Record this
   before using `--confirm-snow-scope`.

5. **Choose this run's model and history.** Select the installed discharge model,
   timezone-aware start/end, supported daily-or-coarser cadence, and minimum
   complete training-window count. Inspect its weather/static requirements before
   retrieval: this path supports basin-average precipitation and temperature;
   models requiring snow or radiation remain held. Set the process-local config
   overlays and existing Gateway secret as in section 7. Record configuration
   versions and endpoint identity without recording credentials.

6. **Execute section 7 in order.** Basin rollback dry run, inspect, then commit;
   retrieve history; qualification rollback dry run, inspect, then commit.
   History commits at most 31 days per transaction and can retain earlier batches
   after a later error. Exit 1 means failure or held coverage/readiness; inspect
   the aggregate report before repeating. Dry runs never retain writes and can
   still exit 1. Hold reasons are valid outcomes, not a reason to force targets.

7. **Read back and record acceptance in Plan 143.** Confirm six station-to-basin
   links, six exact polygon mappings and six active reanalysis bindings; retained
   forcing source/version and coverage; and exactly one readiness outcome per
   gauge for the selected model/window. Qualified targets are discharge-only;
   held targets are unset. All six stations must remain in onboarding, with no
   new model assignment, forecast production or alert activation. Preserve
   observation/QC lineage and report counts/coverage only. Repeat qualification
   after a delivery replacement or QC rerun. Record live-input/current-rating
   limitations and the unresolved model-output publication decision separately.

## Related procedures

- [Basin/static package importer runbook](basin-static-importer-runbook.md)
- [Recap Data Gateway operations runbook](recap-gateway-runbook.md)
- [Plan 143 — DHM v1 station, basin and Gateway onboarding](../plans/143-dhm-v1-basin-gauge-onboarding.md)
