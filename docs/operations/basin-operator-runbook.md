# Get approved basins: extract or attach, review, save the handover

> Part of the onboarding journey: see [onboarding-start-here.md](onboarding-start-here.md).

This is the first step of onboarding stations. The **admin** (the person who runs the
tool; the tool's own pages call this person the "facilitator") prepares the inputs and runs
a small local tool. The **hydrologist** looks at each basin and decides whether it is
suitable. One person can do both jobs, but a successful run is not scientific approval.

> This tool does **not** register stations or start forecasts. Reviewer names are
> self-declared, not authenticated. The review page opens only on the admin's own computer
> (`http://127.0.0.1:8765`), not on a colleague's computer.

## Get approved basins: admin checklist

**Goal:** one saved, approved handover folder with the basins and static attributes of the
stations the hydrologist approves. **Not covered here:** registering stations, the weather
data service (next step) and forecasts.

### Choose your route

| Route | Choose it when | You need |
|---|---|---|
| **1. Attach a package that already exists** | hydrosolutions (or anyone) gave you an extracted package, for example the pre-extracted Nepal basins | The unpacked package folder, its original ZIP, and the matching station sheet. No extractor and no datasets. |
| **2. Run the extractor** | You have new or corrected stations and no package yet | The extractor, set up with hydrosolutions (see below), a command file, and your station sheet. |

The extractor is a separate project. Today it covers **Nepal only** (it needs a Nepal basin
dataset, a Nepal attribute set and a Nepal weather store) and is delivered by hydrosolutions
as a Linux (amd64) Docker bundle or as source plus three large datasets. Its repository is
private. For another country, ask hydrosolutions before you plan: those datasets would have
to be built first.

### Inputs you need

| Input | What it is | Example |
|---|---|---|
| Station sheet | CSV or one-sheet Excel with `station_code`, `display_name`, `latitude`, `longitude` (optional `published_area_km2`). Codes are text: keep `00001`. | [template](../examples/basin-operator-stations.csv) |
| Package folder and its ZIP (route 1) | The unpacked, validated package and the original ZIP, kept unchanged. For the pre-extracted Nepal basins hydrosolutions sends a download link together with the matching station sheet. | `/absolute/package` |
| Extractor command file (route 2) | A JSON file that tells the tool how to start the extractor; set up with hydrosolutions. | `/absolute/private/extractor-command.json` |
| Workspace | A new, empty private folder **outside any Git checkout** for this batch. | `/absolute/private/new-basin-review` |
| Hydrologist | Reviews and records the decision; their name is a label, not a login. | |

### Steps

| # | Step | Who | Done when |
|---|---|---|---|
| 1 | Prepare the station sheet. Compare codes and coordinates with your source; keep leading zeros (import as text). | Admin | The sheet matches the source |
| 2 | Route 1: download the package and unpack it into a new folder; keep the original ZIP. Route 2: get the extractor ready with hydrosolutions. | Admin | Package folder and ZIP in hand, or the command file works |
| 3 | Start the tool with the command for your route (below). | Admin | The page opens at `http://127.0.0.1:8765` |
| 4 | Upload the station sheet, check the stations shown, and **Confirm**. | Admin, hydrologist | Route 1: the page shows **Existing extraction loaded** |
| 5 | Route 2 only: click **Start extraction** once, then reload to check progress. Never start a second run while one is busy. | Admin | A basin is shown |
| 6 | Review each basin and record a decision. Follow [Hydrologist: review the basin](#hydrologist-review-the-basin). | Hydrologist | Decisions recorded |
| 7 | Click **Create immutable approved-only handover**. | Hydrologist, admin | The page shows **Handover saved:** and a folder path |
| 8 | Keep the handover folder unchanged and go on with the [Gateway checklist](recap-gateway-runbook.md#prepare-an-approved-basin-handover--admin-checklist). | Admin | You have the folder path |

Commands (replace the absolute paths; never put credentials in a command):

```bash
# Route 1: attach an existing package (use the unpacked folder, not the ZIP)
uv run python -m sapphire_flow.cli.basin_operator \
  --workspace /absolute/private/new-basin-review \
  --package-dir /absolute/package

# Route 2: run the extractor
uv run python -m sapphire_flow.cli.basin_operator \
  --workspace /absolute/private/new-basin-review \
  --extractor-command /absolute/private/extractor-command.json \
  --extractor-interface v0.2.0
```

Do not use both routes in one command. Basin outlines and coordinates can carry data-use
restrictions: keep the package and workspace private, and share only `review.html` for
feedback (never an approval).

### If it stops

| What you see | What to do |
|---|---|
| **Start extraction** is disabled | Expected on route 1. Confirm the stations and review the loaded package. |
| The package is refused after Confirm | The sheet must list exactly the package's stations and display names. Compare them and correct the sheet; never edit the package. |
| Station codes lost their leading zeros | Go back to the original source and import the column as text. |
| An extraction is running | Reload to check. Do not start another run: it clears the current decisions. |
| A colleague cannot open the page | It is local to your computer. Send them `review.html` for feedback only. |
| Anything else, or you need the extractor | See the troubleshooting table and details below, or ask hydrosolutions with the step number and the exact message (no values, no credentials). |

### Words used here

**Package:** the extracted ZIP with basin outlines and static attributes. **Outlet:** the
point on the river where a basin ends. **Snap distance:** how far the tool moved your
coordinate to reach the river. **Handover:** the saved folder of approved basins. **Workspace:**
the private folder where the tool keeps its files.

## Hydrologist: review the basin

1. **Check the station details.** Compare the displayed codes, names and coordinates
   with your source. Keep leading zeros: `00001` must stay `00001`. Tell the
   admin about any mistake before confirming the inputs.
2. **Check that the basin is ready.** If the page says **Existing extraction
   loaded** or extraction is **completed** with a basin shown, skip Start
   extraction. Otherwise, ask the admin to finish loading a valid result.
3. **Inspect the basin and outlet.** Use the satellite and terrain backgrounds.
   Compare the original point, snapped outlet, boundary, area and snap distance.
   Tell the admin if the map context you need is missing.
4. **Select the stations you mean to review.** Check the boxes in the table.
   **All stations are selected by default.** Choosing a station on the map does
   not change those checkboxes.
5. **Record your decision.** Enter your name, then choose **Approve selected**,
   **Leave pending**, or **Needs correction**. Give a reason for pending/correction.
   Review any warnings before acknowledging them. Structural holds cannot be
   overridden.
6. **Save the handover.** Click **Create immutable approved-only handover**.
   Look for **Handover saved:** and ask the admin to open that folder.
   Only approved stations belong in the exported basin/static package.

> **Loaded is not approved.** Approval here covers this basin handover only—not
> model-input qualification or activation of forecasts. Do not approve a basin
> when the evidence needed for your review is missing.

## Admin: prepare each review session

1. **Choose the route.** Already have an extraction? Use **Attach an existing
   extraction**. Need to create one? Use **Run automatic extraction**, with the
   pinned runtime and datasets prepared first. The explanations and commands are
   in the expandable sections below.
2. **Prepare the station sheet and workspace.** Keep station codes as text. Use a
   new private workspace for a new batch, or resume the existing workspace for the
   same batch. Check that an existing package matches the supplied station inputs.
3. **Start the local tool and check the inputs.** For a new batch, upload the
   sheet. When resuming, use the saved inputs; do not upload again. Ask the
   hydrologist to check them and confirm if confirmation is still needed.
   Attachment mode loads the package at confirmation. In automatic mode, start
   extraction only when a new or corrected batch needs a result. If a valid
   completed basin is already loaded, go straight to review; do not rerun it.
   Reload to check a running extraction. Do not start another run while it is busy.
4. **Check the review is usable.** Verify that the intended basin is loaded and
   satellite/terrain backgrounds work. Explain any missing river or map context.
   Let the hydrologist make the decision; never infer approval from a successful run.
5. **Open and share the saved handover.** The displayed folder is on your computer.
   For colleague feedback, send only `review.html`, subject to data-use restrictions.
   Its map backgrounds still need internet. This read-only feedback copy is not
   an approved training table or a way to record approval remotely.

> **First run?** Set up the system with the project owner's support. Open
> **First setup** below. On an unchanged system, you normally reuse that setup.
> After a system change, open **Recheck after a system change** before new work.

## Help and technical details

Open only the section you need. These sections explain the steps above; they do
not authorize new infrastructure, a deployment, or a scientific approval.

<details>
<summary>First setup — admin, with the project owner</summary>

The project owner will support the first run. This is a development tool,
not a self-installing appliance. A rebuilt appliance handover is still pending.

1. With the owner, choose attachment or automatic extraction.
2. Prepare the installed SAPPHIRE environment and a private workspace. Use the
   **Preparation requirements** and **Launch commands** sections below together.
3. For attachment, obtain the matching station sheet, validated unpacked package
   and original ZIP. No extractor runtime or datasets are needed just for review.
4. For automatic extraction, also provision the fixed `v0.2.0` extractor runtime,
   its datasets and output-path access. Do not install these into the forecast
   environment. The operator does not provision them for you.
5. Check the first run with the owner: inputs, loaded result, map access, explicit
   hydrologist decision and export. A successful setup does not approve the basin.
6. Retain the working command, runtime/dataset references and workspace locations
   securely. Do not put credentials in command arguments. These records help you
   start the next session without repeating installation.

</details>

<details>
<summary>Recheck after a system change — admin</summary>

Repeat the affected setup checks if the computer, operating system,
Python/container environment, storage paths or access permissions change.
This does not mean reinstalling everything after every session.

1. Preserve original packages, workspaces and approval records.
2. Recheck the affected preparation requirements and launch command. Verify that
   the intended inputs and paths are still available.
3. If extraction is affected, run a known reference case in a **separate test
   workspace** and compare with retained results. Investigate differences; a
   successful exit alone does not prove equivalent output.
4. Check the map and review controls before starting new work. Ask the project
   owner for help if the previous setup no longer works.

Keep the selected extractor release fixed. Do not use this check as an excuse to
upgrade it or overwrite previously approved outputs.

</details>

<details>
<summary>What each workflow does</summary>

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

</details>

<details>
<summary>If a button does not respond, or a command fails</summary>

## Troubleshooting the review controls

| What you see | Meaning and action |
| --- | --- |
| Upload is rejected because the workspace already has an inventory | Use the saved inputs when resuming; do not upload the sheet again. Start a new workspace only for a new batch. |
| **Enter a reviewer name before recording a decision.** | A station is selected but the reviewer name is empty or only spaces. Enter the hydrologist's name and retry. No decision was recorded by that attempt. If nothing is selected, select a station first. |
| **Start extraction** is disabled | In attachment mode this is expected: confirm the matching inventory and review the loaded package instead. With no package or command configured, relaunch in one of the modes above. In automatic mode, confirm inputs first; do not start a second extraction while one is running. |
| The command rejects `--snap-strategy` or `--snap-audit` | The pinned `v0.2.0` release fixes distance-first internally and does not accept these historical v2 options. Remove them from its configured prefix. Only select `historical-v2` when intentionally running the old bundle; do not switch interfaces merely to hide a mismatch. |

After **Create immutable approved-only handover**, the success message shows
**Handover saved:** followed by the local directory path. It receives focus and
scrolls into view; this is a saved local folder, not a remote upload or automatic
station registration. If an error appears instead, resolve it before treating an
export as complete. See [Handover contract](#handover-contract) for its contents.

</details>

<details>
<summary>What the review states mean</summary>

| Page state | What it means |
| --- | --- |
| Existing extraction loaded / completed | A result is ready for review. It is not approved yet. |
| Pending | No approval has been recorded. |
| Needs correction | Resolve the recorded concern before fresh review. Changed inputs require a new extraction. |
| Approved | Eligible for this basin handover only. This is not station admission, model qualification or forecast activation. |

If you need to correct an extraction point, preserve the original coordinate and
record the reason. Do not move points merely to match a published area. See
**Detailed review, corrections and resume** below for the procedure.

</details>

<details>
<summary>Preparation requirements — admin</summary>

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
   use an admin-prepared sheet, or set the code column to **Text before
   entering codes**. When importing CSV into a spreadsheet, import that column as
   text rather than accepting automatic number conversion. Formatting a converted
   number as text does not restore lost zeros: return to the original source.
   Numeric or mixed-type Excel code columns are rejected. Compare interpreted
   codes and coordinates with the source before confirming. Duplicate codes and normalized polygon-name collisions
   are rejected. Uploads are limited to 5 MiB and 1,000 stations.

</details>

<details>
<summary>Launch commands — admin</summary>

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
colleagues, or bind it publicly. Keep the admin's account and workspace
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
an extractor or attach a package and disables Start extraction. The admin
must verify this package was made from the uploaded coordinates and current corrected inputs. Attaching verifies the exact station
set, each station's display name and the package contract, **not** scientific delineation or input provenance that
the producer does not expose. A corrected batch cannot reattach a superseded
package identity. Keep original extraction outputs unchanged.

Optional `--rivers /absolute/nearby-rivers.geojson` accepts a small GeoJSON
FeatureCollection of LineString/MultiLineString context. Obtain it through the
supported extractor handover; do not substitute alternative catchments or scrape
restricted data. If no context is supplied, the map explicitly says so.

</details>

<details>
<summary>Detailed review, corrections and resume</summary>

## Operate and resume

1. For a new workspace, upload and inspect the interpreted inventory, then
   **Confirm**. When resuming, inspect the saved inputs instead of uploading again;
   confirm only if needed. In attachment mode, confirmation loads the package
   without running extraction. Corrected inputs require confirmation and a newly
   extracted package identity; the old package is not current.
2. In configured automatic mode, click **Start extraction** only when a new or
   corrected batch needs a result. If a valid completed basin is already loaded,
   skip this step and retain its review decisions. Starting another extraction
   clears the current decisions; it is not the way to resume review.
   Extraction runs as a separate process/environment. Reload to see completion or
   failure. Each attempt has a new directory containing immutable
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
   fixed without losing the form values. The saved path is on the admin's
   computer; ask them to open it. Share only `review.html` for feedback, subject to
   data-use restrictions. That read-only copy still needs internet for map assets
   and backgrounds; it is not an approved training table.
   Do not overwrite an existing handover. Unreviewed stations are never approved
   implicitly. Any package file or inventory change invalidates prior approval.
   The workspace retains the first observed content identity for each package ID,
   including across restart and reattachment. Changed package bytes under that
   same ID are rejected repeatedly: obtain a new producer package ID, then review
   again. Inventory-only provenance changes do not alter package content identity,
   but still require fresh review. Local state is trusted admin data, not a
   signed identity or tamper-proof approval ledger. Restarting and attaching the
   same unchanged automatic package retains decisions, fingerprint and its recorded
   source ZIP hash. A different package does not inherit that hash, including
   replacement detected on page load before confirmation and fresh review.

</details>

<details>
<summary>Map backgrounds, connectivity and usage terms</summary>

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

</details>

<details>
<summary>What the handover contains</summary>

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
CSRF token, remote submission or approval authority. The admin records the
resulting decision locally. Review what you share; even geometry/coordinates may
have data-use restrictions. Gateway registration and SAPPHIRE admission are later,
separately authorized steps, not actions in this operator.

</details>

<details>
<summary>Validation evidence and remaining acceptance</summary>

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

</details>

## Next: prepare the approved handover in the Gateway

Use the companion [approved-handover procedure](recap-gateway-runbook.md#prepare-an-approved-basin-handover--admin-checklist).
Keep the exported package and original approval immutable. Gateway admission
checks identity/checksums/approval applicability and reads feature attributes only;
it does not repeat geometry review. Its external private workspace is separate
from this review server. Do not stop or restart the review tool to use it.
