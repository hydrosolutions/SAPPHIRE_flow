# Local basin extraction and approval

This is a facilitator-operated development tool for **new station batches**.
It does not import stations, call the Gateway, open a database, discover models,
or start scheduled forecasts. It is not part of the public API. Reviewer names
are self-declared, not authenticated. Scientific adoption still needs owner approval.

## Hydrologist quick-start — a prepared session

The facilitator prepares the tool and extraction; you judge the basin and outlet.
You do not need to install the software to review a prepared session. If the tool
or package is not ready, ask the facilitator to complete the setup below first.

1. **Check the station list.** Compare every interpreted code, name and WGS84
   coordinate with the source before confirming. Codes such as `00001` must retain
   their leading zeros.
2. **Wait for the basin to be ready.** An existing package shows **Existing
   extraction loaded**; do not start extraction again. In automatic mode, the
   facilitator starts extraction after confirmation and waits for a valid result.
3. **Inspect the evidence.** Review the original point, selected outlet and basin
   boundary with satellite/terrain backgrounds, area and snap distance. Missing
   map context is not evidence that the basin is correct.
4. **Check the selection and record your decision.** All stations are selected by
   default. Viewing one station on the map does **not** select only that station
   for approval. Select the intended rows, enter your name and decide. Acknowledge
   displayed warnings only after reviewing them; structural holds cannot be waived.
5. **Save the approved-only handover.** The success message gives a folder path on
   the facilitator's computer. Ask them to open it. For colleague feedback, share
   only `review.html`, subject to data-use restrictions; it still needs internet
   for map assets/backgrounds and is not the approved model-input package.

**State key:** Loaded/completed means ready for review, not approved. Pending
means no approval yet. Needs correction means the recorded concern remains to be
resolved before fresh review; changed inputs require a new extraction. Approved
means eligible for this basin handover only, not station admission, model-input
qualification or activation of forecasts.

See [Operate and resume](#operate-and-resume) for the detailed decision procedure.
The local review address works on the facilitator's machine; it is not a link
that colleagues can open on their own computers.

## Facilitator responsibilities

One person may fill both roles, but setup checks do not replace the explicit
scientific review and approval.

**Initial setup — with the project owner's support for the first run:** choose
[the workflow](#choose-your-workflow), prepare the SAPPHIRE environment and private
workspace, and follow [Prepare](#prepare) and [Explicit launch](#explicit-launch).
For attachment, obtain the matching station sheet and validated unpacked package,
retaining its original ZIP. For automatic extraction, also provision the pinned
extractor environment, datasets and output-path access. Verify the first run with
the owner: confirm the inputs, inspect the loaded result, check map connectivity,
and complete review/export without treating setup success as scientific approval.
Retain the working launch command, runtime/dataset references and workspace
locations securely so the session can be resumed; do not store credentials in argv.

**Before each session:** the full installation is not repeated on an unchanged
system. Start the local tool, check that the intended package and station sheet
match, and check that imagery/terrain and any supplied river context are usable.
Explain any missing context to the hydrologist. Use a new workspace for a new batch,
or resume the existing one for the same batch. Help the reviewer find the saved
handover and share the appropriate files; never record approval on their behalf
without their explicit decision.

**After a system change:** initial setup is not a permanent guarantee. If the
computer, operating system, Python/container environment, storage paths or access
permissions change, repeat the affected preparation and startup checks. For
extraction-related changes, run a known reference case in a separate test workspace
and compare it with retained results before new work; investigate differences
rather than assuming success means equivalence. Preserve original packages and
approval records. Ask the owner for help if the earlier setup no longer works.
This is rechecking the fixed setup, not an instruction to upgrade the extractor or
rerun previously approved stations in place. A rebuilt appliance handover remains
pending; this guide does not turn the current tool into a self-installing appliance.

## Choose your workflow

Both workflows end with the same steps: inspect the basin and selected outlet,
record a hydrologist decision, then export only approved stations. Neither
workflow registers a station or enables forecasts.

| Workflow | What it does | When to choose it |
| --- | --- | --- |
| **Attach an existing extraction** | Loads an already extracted package for validation, map review and approved-only export. It does not run the extractor or recompute geometry/static values. | Extraction has already finished elsewhere, such as on EC2. This is the route used for the Trishuli rehearsal. |
| **Run automatic extraction** | Starts your configured external extractor after you confirm the uploaded inputs and click **Start extraction**. It retains the resulting ZIP, validates/unpacks it, then opens the same review workflow. | You have provisioned the pinned `v0.2.0` runtime and datasets, and its command can write to the output path visible to the operator. |

For attachment, use `--package-dir` with the validated **unpacked directory**, not
the ZIP. Upload the same station inputs and confirm them. Once attached, the page
shows **Existing extraction loaded**; skip Start extraction. Keep the original ZIP.
You do not need an extractor command or dataset installation just to review a
completed package.

For automatic extraction, use `--extractor-command` and explicitly select
`--extractor-interface v0.2.0`. The operator does not install the extractor,
download datasets or provision an extraction host. Historical v2 is a separate
compatibility option, not the selected source release for this pipeline.

Choose one launch mode, not both. See [Explicit launch](#explicit-launch) for
commands and runtime requirements. The UI behavior described here belongs to
the operator update; a still-running older review server needs the updated code
and an authorized restart before it shows these fixes.

## Prepare

1. For new automatic runs, provision the fixed `static-attrs-nepal` release
   `v0.2.0`, source commit `25cc09c2aff192033abefaf6b052822ad7024d67`,
   and its local HFX, HydroATLAS and ERA5-Land datasets. Source/runtime pinning
   is the operator's responsibility; the CLI interface selector does not verify
   executable identity. The rebuilt appliance handover is still pending.
   The historical v2 bundle remains a separate compatibility path. Keep that
   environment separate from SAPPHIRE. Do not install its dependencies into the forecast environment.
   Historical tested v2 bundle SHA-256:
   `e438ca2c5bd6a88a9a65d5471c54ae097db6410fd4a8abb50d57661f868f0c83`.
   The owner-provided tar matched that historical hash in read-only verification.
   Its historical image ID is
   `sha256:0d05d352bd0431517aa62469e13efc0347448d3fa9d01f8fa0292445df41facd`.
   This is not a claim that the image has been loaded or run here. The bundle
   requires an authorized native Linux amd64 host; Apple Silicon emulation is
   not a supported fallback for its AVX/AVX2 requirements.
2. Verify source identity, architecture, dataset paths and supported command
   on the extraction host. Both historical v2 and the selected release can report
   version 0.2.0; that string does not prove source identity. The release fixes
   distance-first internally and rejects v2 strategy/audit options. Trishuli
   main/v2 geometry and 92 static values matched in the same runtime; the
   21-station comparison was **not** exactly equivalent. Neither comparison
   demonstrated automatic extraction through this operator or a rebuilt image.
   This delivery runs only synthetic subprocesses, not a new live extraction.
   The inaccessible supplied OneDrive folder was not bypassed.
3. Use a new private workspace outside the repository. Do not copy restricted
   discharge, stage or rating-table files into it. One running operator per
   workspace is enforced by a local process lock (macOS/Linux).
4. Prepare UTF-8 CSV or one-sheet `.xlsx`. See
   [synthetic template](../examples/basin-operator-stations.csv). Required columns
   are `station_code`, `display_name`, `latitude`, `longitude`. Optional columns
   are `published_area_km2` (positive km²) and `source`. Coordinates are WGS84
   decimal degrees. Codes are strings: preserve `001.0`, not `1`. In Excel,
   use a facilitator-prepared sheet, or set the code column to **Text before
   entering codes**. When importing CSV into a spreadsheet, import that column as
   text rather than accepting automatic number conversion. Formatting a converted
   number as text does not restore lost zeros: return to the original source.
   Numeric or mixed-type Excel code columns are rejected. Compare interpreted
   codes and coordinates with the source before confirming. Duplicate codes and normalized polygon-name collisions
   are rejected. Uploads are limited to 5 MiB and 1,000 stations.

## Explicit launch

From the normal installed SAPPHIRE environment:

```bash
uv run python -m sapphire_flow.cli.basin_operator \
  --workspace /absolute/private/new-basin-review \
  --extractor-command /absolute/private/extractor-command.json \
  --extractor-interface v0.2.0
```

Open `http://127.0.0.1:8765` (not `localhost`). The listener is loopback-only;
Host and CSRF checks protect local writes. Do not reverse-proxy it, tunnel it to
colleagues, or bind it publicly. Keep the facilitator's account and workspace
private. There is no multi-user authentication service. Close with Ctrl-C.
The launcher stops its direct extraction child; inspect any container/remote
wrapper separately before retrying interrupted work.

The command file is a JSON **argv prefix**, not a shell script. It is trusted
operator configuration, never uploaded from the page. Use absolute paths. For
the pinned release interface, an illustrative configuration is:

```json
[
  "/absolute/pinned-v0.2.0-extractor/.venv/bin/python",
  "-m", "static_attrs_nepal.appliance",
  "--hfx-dataset-dir", "/absolute/datasets/hfx",
  "--hydroatlas-clip", "/absolute/datasets/hydroatlas.gpkg",
  "--era5-land-cube", "/absolute/datasets/era5-land.zarr"
]
```

The operator appends repeated `--station CODE NAME LAT LON`, then a new
`--package-id` and `--output`. Do not include these arguments in the prefix.
It never interprets a shell, selects another snapping method, or adds scientific
thresholds. With `--extractor-interface v0.2.0`, no strategy/audit options are
added; `--snap-strategy` and `--snap-audit` in the prefix are rejected, including
`--option=value` forms. Station/output/package-ID overrides are also rejected.

For the historical v2 bundle only, choose `--extractor-interface historical-v2`.
This is the backward-compatible default when the selector is omitted, **not**
the recommended release for new pipelines. It adds `--snap-strategy distance-first`
when absent and rejects other strategies. It does not rewrite arbitrary prefixes
or detect a binary's version. Do not put credentials into argv: the local invocation
record preserves exact argv/input provenance, the selected interface and, for
v0.2.0, the expected release/source reference explicitly as configuration, not
verified provenance. Existing invocation records are unchanged. An existing
container command can be used only when its owner-supported interface accepts these arguments and exposes the
output directory at the same absolute host path. No infrastructure is provisioned.

Both supported interfaces must write a **ZIP file** at `--output`. Each attempt uses
a fresh `package.zip`; the original ZIP is retained unchanged and hashed. The
operator unpacks into a new sibling `package/` directory for the existing loader.
Only unique root-level contract files are accepted: the six required v2 members
(manifest, basins, attributes, catalog, validation and README), plus the contract's
optional bands/comparison/checksum files. Links, paths, unexpected files, encrypted
entries and more than 2 GiB expanded content are rejected. Do not guess another
release's archive layout or silently rewrite its command. Escalate incompatibility.
For separately completed extraction, launch in attachment mode instead:

```bash
uv run python -m sapphire_flow.cli.basin_operator \
  --workspace /absolute/private/new-basin-review \
  --package-dir /absolute/package
```

Do not combine `--package-dir` with `--extractor-command`; launch fails fast.
A valid attached package shows **Existing extraction loaded** with Start extraction
disabled. Direct extraction requests in attachment mode are rejected without
clearing approvals. With neither configuration, the page asks you to configure
an extractor or attach a package and disables Start extraction. The facilitator
must verify this package was made from the uploaded coordinates and current corrected inputs. Attaching verifies the exact station
set, each station's display name and the package contract, **not** scientific delineation or input provenance that
the producer does not expose. A corrected batch cannot reattach a superseded
package identity. Keep original extraction outputs unchanged.

Optional `--rivers /absolute/nearby-rivers.geojson` accepts a small GeoJSON
FeatureCollection of LineString/MultiLineString context. Obtain it through the
supported extractor handover; do not substitute alternative catchments or scrape
restricted data. If no context is supplied, the map explicitly says so.

## Operate and resume

1. Upload, inspect the interpreted inventory, then **Confirm**. No extraction
   starts before confirmation. In attachment mode, confirmation loads the package;
   no extraction is launched. Corrected inputs cannot display the old package as
   current and require a new package identity.
2. In configured automatic mode, **Start extraction**. It runs as a separate
   process/environment. Reload to see completion or failure. Each attempt has a new directory containing immutable
   inputs/argv and `extractor.log`; these remain local, not remotely shared.
   A failed or structurally invalid output cannot be approved. Restarting the
   operator preserves review state; an in-flight process is reported interrupted,
   not adopted by PID or assumed successful. Inspect output before retrying.
3. Inspect original points, selected snapped outlets and boundaries. Use station
   selection, outlet zoom and basin/batch fit. There are **no alternative snaps**.
   Published/extracted areas and snap distances are evidence, not new thresholds.
4. Check the selected rows: all stations are selected by default. Choosing one
   station in the map dropdown does not change the decision selection. Select the
   intended subset or whole batch, then enter the hydrologist's name.
   Blank/whitespace names show feedback beside the controls and focus the name
   field without sending a decision. Empty selection is reported first. Server
   validation still applies. Approve, leave pending or mark needing correction. Pending/correction actions
   require a reason. Approval with any displayed warning requires the explicit
   acknowledgment checkbox. Structural failures and existing coverage holds
   cannot be overridden. Approval here is not model-input qualification.
5. Export the approved subset before revising remaining inputs if needed. For a
   correction, prefer a surveyed extraction-point adjustment with a reason.
   Never change the original station coordinate or move a point merely to fit a
   published area. Escalate suspected DEM problems to the extractor owner.
   Adjustments preserve original inputs and input history, clear approvals and
   require confirmation, re-extraction and fresh review. No polygon editing.
6. **Create immutable approved-only handover**. The UI reports its local path
   in an announced message that receives focus and scrolls into view. Network and
   server errors use the same visible feedback. Review/correction errors can be
   fixed without losing the form values. The saved path is on the facilitator's
   computer; ask them to open it. Share only `review.html` for feedback, subject to
   data-use restrictions. That read-only copy still needs internet for map assets
   and backgrounds; it is not an approved training table.
   Do not overwrite an existing handover. Unreviewed stations are never approved
   implicitly. Any package file or inventory change invalidates prior approval.
   The workspace retains the first observed content identity for each package ID,
   including across restart and reattachment. Changed package bytes under that
   same ID are rejected repeatedly: obtain a new producer package ID, then review
   again. Inventory-only provenance changes do not alter package content identity,
   but still require fresh review. Local state is trusted facilitator data, not a
   signed identity or tamper-proof approval ledger. Restarting and attaching the
   same unchanged automatic package retains decisions, fingerprint and its recorded
   source ZIP hash. A different package does not inherit that hash, including
   replacement detected on page load before confirmation and fresh review.

## Troubleshooting the review controls

| What you see | Meaning and action |
| --- | --- |
| **Enter a reviewer name before recording a decision.** | A station is selected but the reviewer name is empty or only spaces. Enter the hydrologist's name and retry. No decision was recorded by that attempt. If nothing is selected, select a station first. |
| **Start extraction** is disabled | In attachment mode this is expected: confirm the matching inventory and review the loaded package instead. With no package or command configured, relaunch in one of the modes above. In automatic mode, confirm inputs first; do not start a second extraction while one is running. |
| The command rejects `--snap-strategy` or `--snap-audit` | The pinned `v0.2.0` release fixes distance-first internally and does not accept these historical v2 options. Remove them from its configured prefix. Only select `historical-v2` when intentionally running the old bundle; do not switch interfaces merely to hide a mismatch. |

After **Create immutable approved-only handover**, the success message shows
**Handover saved:** followed by the local directory path. It receives focus and
scrolls into view; this is a saved local folder, not a remote upload or automatic
station registration. If an error appears instead, resolve it before treating an
export as complete. See [Handover contract](#handover-contract) for its contents.

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

Repeatable project-native checks (Chrome installed locally; no runtime browser
dependency or packaged-browser download):

```bash
uv run pytest tests/unit/basin_operator tests/unit/services/test_basin_package_loader.py
uv run --with playwright==1.64.1 pytest tests/unit/basin_operator/browser_checks.py
```

`browser_checks.py` is outside pytest's default `test_*.py` / `*_test.py`
discovery patterns. Run the explicit separate browser command above. It starts
an isolated server on a free port with synthetic files, never the live workspace.
It uses headless installed Chrome and blocks external map assets. It verifies
actual button requests, focus/viewport, correction, network/server errors, export
success and read-only feedback. It is not a map-provider availability test.

Focused synthetic tests cover uploads, argv, strict release-shaped and legacy
external subprocesses (no scientific recomputation),
local host/CSRF checks, warning/structural gates, selection, resume, changed-package
reapproval and approved-only round-trip packages. They do not repeat upstream
scientific evaluations. Before real use, obtain the supported extractor runtime and
datasets, verify a **new** trial batch end to end, verify live imagery/terrain and
available river context, and obtain hydrologist review. Do not reset existing
six-/21-station evaluation-server records. Full repository tests and independent
reviews remain pre-merge gates.
