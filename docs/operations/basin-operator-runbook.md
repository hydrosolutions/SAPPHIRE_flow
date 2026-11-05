# Local basin extraction and approval

This is a facilitator-operated development tool for **new station batches**.
It does not import stations, call the Gateway, open a database, discover models,
or start scheduled forecasts. It is not part of the public API. Reviewer names
are self-declared, not authenticated. Scientific adoption still needs owner approval.

## Prepare

1. Obtain the owner's supported, tested `static-attrs-nepal` bundle and its local
   HFX, HydroATLAS and ERA5-Land datasets. Keep that environment separate from
   SAPPHIRE. Do not install its dependencies into the forecast environment.
   Required tested v2 bundle SHA-256:
   `e438ca2c5bd6a88a9a65d5471c54ae097db6410fd4a8abb50d57661f868f0c83`.
   The owner-provided tar matched that historical hash in read-only verification.
   Its historical image ID is
   `sha256:0d05d352bd0431517aa62469e13efc0347448d3fa9d01f8fa0292445df41facd`.
   This is not a claim that the image has been loaded or run here. The bundle
   requires an authorized native Linux amd64 host; Apple Silicon emulation is
   not a supported fallback for its AVX/AVX2 requirements.
2. Verify the bundle version, architecture, dataset paths and supported command
   on the extraction host. The tested handover documents extractor 0.2.0/v2;
   upstream main is not assumed equivalent. This delivery has **not** run a new
   live extraction. The inaccessible supplied OneDrive folder was not bypassed.
3. Use a new private workspace outside the repository. Do not copy restricted
   discharge, stage or rating-table files into it. One running operator per
   workspace is enforced by a local process lock (macOS/Linux).
4. Prepare UTF-8 CSV or one-sheet `.xlsx`. See
   [synthetic template](../examples/basin-operator-stations.csv). Required columns
   are `station_code`, `display_name`, `latitude`, `longitude`. Optional columns
   are `published_area_km2` (positive km²) and `source`. Coordinates are WGS84
   decimal degrees. Codes are strings: preserve `001.0`, not `1`. In Excel,
   format **and enter** every code as text. Numeric or mixed-type Excel code
   columns are rejected. Duplicate codes and normalized polygon-name collisions
   are rejected. Uploads are limited to 5 MiB and 1,000 stations.

## Explicit launch

From the normal installed SAPPHIRE environment:

```bash
uv run python -m sapphire_flow.cli.basin_operator \
  --workspace /absolute/private/new-basin-review \
  --extractor-command /absolute/private/extractor-command.json
```

Open `http://127.0.0.1:8765` (not `localhost`). The listener is loopback-only;
Host and CSRF checks protect local writes. Do not reverse-proxy it, tunnel it to
colleagues, or bind it publicly. Keep the facilitator's account and workspace
private. There is no multi-user authentication service. Close with Ctrl-C.
The launcher stops its direct extraction child; inspect any container/remote
wrapper separately before retrying interrupted work.

The command file is a JSON **argv prefix**, not a shell script. It is trusted
operator configuration, never uploaded from the page. Use absolute paths. For
the tested appliance interface, an illustrative configuration is:

```json
[
  "/absolute/tested-extractor/.venv/bin/python",
  "-m", "static_attrs_nepal.appliance",
  "--hfx-dataset-dir", "/absolute/datasets/hfx",
  "--hydroatlas-clip", "/absolute/datasets/hydroatlas.gpkg",
  "--era5-land-cube", "/absolute/datasets/era5-land.zarr",
  "--snap-strategy", "distance-first"
]
```

The operator appends repeated `--station CODE NAME LAT LON`, then a new
`--package-id` and `--output`. Do not include these arguments in the prefix.
It never interprets a shell, selects another snapping method, or adds scientific
thresholds. The wrapper always passes the tested v2 `--snap-strategy distance-first`
setting (adds it if absent from the prefix). Other explicit strategies are
rejected. Do not substitute upstream main, which has a different CLI. Do not put credentials into argv: the local invocation
record preserves it for provenance. An existing container command can be used
only when its owner-supported interface accepts these arguments and exposes the
output directory at the same absolute host path. No infrastructure is provisioned.

The tested v2 invocation writes a **ZIP file** at `--output`. Each attempt uses
a fresh `package.zip`; the original ZIP is retained unchanged and hashed. The
operator unpacks into a new sibling `package/` directory for the existing loader.
Only unique root-level contract files are accepted: the six required v2 members
(manifest, basins, attributes, catalog, validation and README), plus the contract's
optional bands/comparison/checksum files. Links, paths, unexpected files, encrypted
entries and more than 2 GiB expanded content are rejected. Do not guess another
release's archive layout or silently rewrite its command. Escalate incompatibility.
For separately completed extraction, launch with `--package-dir /absolute/package`
instead. The facilitator must verify this package was made from the uploaded
coordinates and current corrected inputs. Attaching verifies the exact station
set and package contract, **not** scientific delineation or input provenance that
the producer does not expose. A corrected batch cannot reattach a superseded
package identity. Keep original extraction outputs unchanged.

Optional `--rivers /absolute/nearby-rivers.geojson` accepts a small GeoJSON
FeatureCollection of LineString/MultiLineString context. Obtain it through the
supported extractor handover; do not substitute alternative catchments or scrape
restricted data. If no context is supplied, the map explicitly says so.

## Operate and resume

1. Upload, inspect the interpreted inventory, then **Confirm**. No extraction
   starts before confirmation.
2. **Start extraction**. It runs as a separate process/environment. Reload to see
   completion or failure. Each attempt has a new directory containing immutable
   inputs/argv and `extractor.log`; these remain local, not remotely shared.
   A failed or structurally invalid output cannot be approved. Restarting the
   operator preserves review state; an in-flight process is reported interrupted,
   not adopted by PID or assumed successful. Inspect output before retrying.
3. Inspect original points, selected snapped outlets and boundaries. Use station
   selection, outlet zoom and basin/batch fit. There are **no alternative snaps**.
   Published/extracted areas and snap distances are evidence, not new thresholds.
4. Select all stations (the default) or a subset. Enter the hydrologist's name.
   Approve, leave pending or mark needing correction. Pending/correction actions
   require a reason. Approval with any displayed warning requires the explicit
   acknowledgment checkbox. Structural failures and existing coverage holds
   cannot be overridden. Approval here is not model-input qualification.
5. Export the approved subset before revising remaining inputs if needed. For a
   correction, prefer a surveyed extraction-point adjustment with a reason.
   Never change the original station coordinate or move a point merely to fit a
   published area. Escalate suspected DEM problems to the extractor owner.
   Adjustments preserve original inputs and input history, clear approvals and
   require confirmation, re-extraction and fresh review. No polygon editing.
6. **Create immutable approved-only handover**. The UI reports its local path.
   Do not overwrite an existing handover. Unreviewed stations are never approved
   implicitly. Any package file or inventory change invalidates prior approval;
   reload and review again. Local state is trusted facilitator data, not a signed
   identity or tamper-proof approval ledger.

## Maps and connectivity

The page and read-only export embed basin/point data. Leaflet 1.9.4 loads from
unpkg with a pinned script integrity hash. The backgrounds are:

- Esri World Imagery: visible-band true-color satellite/aerial imagery, not a
  false-color scientific raster. Attribution remains on the map. Review
  [Esri imagery information and terms](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9)
  and [Esri terms](https://www.esri.com/en-us/legal/terms/full-master-agreement).
- OpenTopoMap terrain/topography with relief. Attribution credits OpenStreetMap,
  SRTM and OpenTopoMap (CC-BY-SA). Follow the
  [OpenTopoMap usage information](https://opentopomap.org/about).

Use normal interactive viewing only: no tile prefetching, bulk download or offline
packaging. Confirm provider terms and NHS-office connectivity before the session.
Tile failures show a warning and leave geometry overlays in place. A missing
Leaflet library shows a separate warning and preserves the review table; reconnect
before map review. Automated UI tests are not hydrologist approval or proof of
provider availability. Exported HTML still needs internet for map assets/backgrounds.

## Handover contract

Each export contains:

- `package/`: a new `basin-static-artifact/v1` identity, approved-only geometry and
  exact joined attributes, catalog, subset validation report and recalculated
  checksums; selected geometry/static values are not recomputed;
- modeller CSV/Parquet static tables, feature definitions and units;
- `coordinates-and-snap.json`: original/source and adjusted extraction coordinates,
  adjustment reason, selected outlets, snap distance and drainage comparison;
- `approval.json`: self-declared reviewer, timestamp and exact source/derived
  fingerprints and the source ZIP hash for an app-run extraction;
  `package/derivation.json` traces the unchanged source package;
- `excluded-stations.csv`: separate pending/correction stations and reasons;
- `review.html`: a read-only feedback copy that may show the whole review batch,
  clearly labeled with review states; it is **not** an approved training table;
- `checksums.sha256`: hashes of all handover files, including the derived manifest.

Use CSV import-as-text for station/gauge identifiers in spreadsheets. The original
package is never modified. Both source and derived packages pass the existing
loader and exact join validation. Optional bands are filtered with the same station
set. An official-comparison Parquet without `station_code` is not guessed/subset:
export stops and the producer must clarify its schema. Unknown optional source
extras are not copied into approved training inputs.

Send colleagues only the read-only HTML for feedback. It has no write controls,
CSRF token, remote submission or approval authority. The facilitator records the
resulting decision locally. Review what you share; even geometry/coordinates may
have data-use restrictions. Gateway registration and SAPPHIRE admission are later,
separately authorized steps, not actions in this operator.

## Validation and remaining acceptance

Focused synthetic tests cover uploads, argv, external-process orchestration,
local host/CSRF checks, warning/structural gates, selection, resume, changed-package
reapproval and approved-only round-trip packages. They do not repeat upstream
scientific evaluations. Before real use, obtain the supported extractor runtime and
datasets, verify a **new** trial batch end to end, verify live imagery/terrain and
available river context, and obtain hydrologist review. Do not reset existing
six-/21-station evaluation-server records. Full repository tests and independent
reviews remain pre-merge gates.
