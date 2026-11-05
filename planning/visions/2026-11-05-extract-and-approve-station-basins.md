# Extract and approve station basins

Program: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/450
Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/451

## Accepted outcome

A facilitator uploads station information, invokes the existing basin/static
extractor, obtains hydrologist approval on an interactive map, and exports only
approved stations for subsequent onboarding and model training. Deliver this
operator tool inside SAPPHIRE Flow, separate from the scheduled forecast runtime.
This is the first contained outcome of the [overarching onboarding vision](2026-11-05-staged-station-onboarding-toward-a-production-pipeline.md),
not a Gateway, forecast-deployment or full administrator-dashboard implementation.

The owner confirmed this scope after inspecting the existing 21-station handover.
The supplied extraction package has already been tested and independently evaluated
three times. Treat it as an accepted external dependency: do not repeat its
scientific validation or revisit the selected snapping strategy.

## Reuse and ownership

Use [static-attrs-nepal](https://github.com/hydrosolutions/static-attrs-nepal)
and the owner's tested extraction handover without editing either. Preserve the
existing default snapping method and the existing basin/static artifact contract.
Do not replace the delineator, static calculators or their scientific behavior.

The operator tool belongs in this repository because it coordinates SAPPHIRE
onboarding and reuses its package contracts and validation. Keep extraction in its
own supported environment or existing container and invoke its supported command
interface. A direct Python dependency is not required; inspect the tested bundle's
actual interface before selecting the smallest execution wrapper. Do not mix its
geospatial dependencies into the normal forecast installation or implicitly start
extraction/review services in the forecast worker or public API. Provide an explicit
operator entrypoint. A future administrator dashboard can reuse these operations.

No edits to externally owned extraction, Gateway, dashboard or model tools are
authorized. Document a missing capability and escalate to the project owner rather
than patching, forking around or silently replacing another developer's behavior.
The existing SAPPHIRE-flow-map dashboard is for forecast review, not this operator
interface; no changes there are included.

## Station upload and extraction

The small local interface accepts CSV or Excel using a defined template. At minimum,
identify each station unambiguously with its code, display name and latitude/longitude
in WGS84 decimal degrees. Preserve string codes, including decimal-looking codes;
do not infer station identity from a floating-point number. Support optional published
drainage area and source information for review. Show the interpreted inventory for
confirmation before extraction. Invalid or ambiguous input must be corrected, not
silently coerced into a different station or location.

The facilitator can then start extraction and see its outcome or failure. Reuse the
accepted package and current default snapping method. Do not ask the operator to
choose snapping algorithms and do not repeat alternative-strategy audits merely to
revalidate the accepted extractor. Existing source evidence can be retained without
becoming a new mandatory validation exercise.

Select the lowest-overhead supported execution route after inspecting the actual
bundle and available resources. The facilitator brings a laptop; coordinated use of
the Infomaniak Nepal server or separately authorized temporary EC2 is also possible.
There is no fixed hosting choice or new infrastructure mandate. Do not provision paid
resources or add extraction load to the Nepal server without owner authorization and
coordination with its deployment session. Keep the review interface on the facilitator's
computer; extraction may run on the selected approved host. Internet is available at
the NHS office. Dataset availability and supported architecture must be verified rather
than assuming a container will run efficiently on the laptop.

## Review map and approval

Reuse the supplied interactive HTML map's design and useful controls rather than
building a new GIS tool. Show:

- the original station coordinate and selected snapped outlet;
- the selected basin boundary and nearby river context where available;
- station selection, outlet zoom and basin-fit controls;
- available published/extracted drainage-area comparisons and snap information;
- two selectable backgrounds: visible-band true-color satellite imagery and terrain.

**Do not display alternative snapping results.** The owner accepts the current default.
The old map's street background is not a substitute for the required terrain layer.
Use online backgrounds with appropriate attribution and usage terms. If imagery
cannot load, retain the geometry overlay and show a clear warning rather than
pretending the full visual review is available. Offline background packaging is not
required.

A hydrologist approves through a simple local action. Default selection is the whole
batch, but individual stations can remain unapproved or be marked as needing correction.
Only approved stations proceed. Show existing warnings and comparisons without inventing
new scientific pass/fail thresholds. Approval with warnings requires explicit
acknowledgment. Malformed or incomplete packages cannot be approved; a scientific
warning is not the same as structural invalidity.

Record the self-declared reviewer name and the reviewed basin/package version without
claiming authenticated identity. Preserve approval across an interrupted review, but
do not reuse it for changed geometry. Keep the interface local to one facilitator;
no new login, remote approval service or multi-user system is included. NHS authentication
requirements will be clarified in later work.

## Corrections

When a catchment looks wrong, recommend correcting the extraction pour point first.
Preserve the original station coordinate and its source; record any extraction-point
adjustment and reason rather than overwriting the station's location. Mark the station
as needing correction and allow the approved subset to continue. Corrected inputs
require re-extraction and fresh basin approval. Do not move a point simply to force
agreement with a published area. Suspected DEM defects are escalated to the owner of
the extraction data/tool. No polygon editing, interactive outlet dragging or DEM editing
is part of this delivery.

## Approved exports and modeller handover

Export **approved stations only** in the onboarding package and training-ready modeller
handover. Preserve the original extraction outputs unchanged. Provide a separate summary
of excluded stations and their reasons; awaiting review is not implicit approval.

Reuse the existing handover conventions for static attributes, feature definitions
and units, coordinate/snap evidence, provenance, validation results and checksums.
Keep geometry and attribute joins exact. The handover includes the accepted basin
geometry/package as well as modeller-friendly attribute tables and the review map.
Do not include restricted runoff, water-level or rating-table data merely because the
handovers are in the same local project area.

Partial approval must yield a valid approved subset, not a ZIP whose rows were removed
without updating its manifest, metadata and checksums. Follow the existing immutable
package contract; do not mutate a sealed original or reuse its identity for changed
contents. Confirm whether the existing supported interfaces can produce the required
subset. If this needs an external-tool change, escalate to the owner before proceeding.
Subset output must correspond to the actual reviewed basins, not unreviewed new geometry.

Retain a shareable, read-only HTML export for NHS feedback. Identify the reviewed
basin version and approval/pending state clearly. It can show the review batch, but
must not disguise excluded basins as approved training inputs. Colleagues may return
feedback; the facilitator records the hydrologist's resulting decision locally. The
export is not a second source of approval authority and has no remote submission
or authentication mechanism. Satellite and terrain backgrounds still require internet.

## Focused acceptance, not extractor revalidation

Minimize new testing to the integration this repository owns:

- uploaded station identities and coordinates are interpreted and passed correctly;
- outputs satisfy the existing package contract and match the requested/approved set;
- upload, station selection, map layers and approval controls work;
- subset approval, warning acknowledgment, invalid-output rejection and changed-basin
  reapproval behave correctly;
- approved-only handovers remain internally consistent, traceable and usable;
- one focused end-to-end rehearsal demonstrates upload through approved handover.

Do not repeat the three independent scientific extraction evaluations, run comparative
snapping studies, or expand this into a GIS benchmark. Reuse existing checks rather
than duplicate them. Repository-mandated code review and pre-merge test gates still
apply; minimizing task-specific validation does not waive them.

Use new trial stations for live extraction acceptance. The previous six- and
21-station handovers are read-only reference examples, not permission to reset or
re-register existing evaluation-server stations. Never label automated map/control
checks as hydrologist approval.

## Evidence and implementation discovery

The owner's supplied extraction location is:

```text
/Users/bea/Library/CloudStorage/OneDrive-hydrosolutionsltd-TobiasSiegfried-01_PROJECTS_ACTIVE/2025_01 ReCAP Nepal (SDC)/09_handover_prep/static_attributes
```

Filesystem access to that location was denied during discovery; its current contents
and version have not been inspected. Do not bypass permissions. Obtain an accessible
copy or authorized access before depending on that handover.

The inspected modeller handover is:

```text
/Users/bea/hydrosolutions Dropbox/Bea martibeatrice@gmail.com/2025-01-BARHKH/data/handover_basin_static_attributes_modeller_21
```

Its `nepal-21-gauge-snap-review.html` is a self-contained Leaflet map with embedded
geometry and online backgrounds. It has useful station/outlet/basin controls, but
currently has street/satellite rather than terrain, displays alternative snapping,
and does not record approvals. Reuse its useful presentation, not those superseded
choices. The inspected README reports a v2 bundle/extractor 0.2.0, distance-first
selection, and 21-station extraction on temporary native amd64 EC2. It also explicitly
records human review as pending; existing handover validation is not approval.
The handover contains attribute CSV/Parquet, GeoPackage, feature catalog, coordinate
and snap evidence, package manifest, provenance, validation and checksums.

The upstream README documents the `static_attrs_nepal.appliance` headless command,
a local entry UI and local HFX/HydroATLAS/ERA5-Land datasets. Its currently inspected
main interface and the tested v2 handover must not be assumed identical. Resolve the
supported bundle entrypoint and versions without rewriting the extractor. Find the
existing map-generation source where available; the HTML is a presentation reference,
not evidence that a reusable generator has already been located.

SAPPHIRE reuse points include `docs/requirements/04-basin-static-artifact-contract.md`,
`docs/operations/basin-static-importer-runbook.md`,
`src/sapphire_flow/services/basin_package_loader.py`, and the existing Nepal onboarding
runbook. Use package validation without importing into a live forecast database during
this Effort. The next Efforts own Gateway registration and SAPPHIRE station admission.

## Boundaries and coordination

No Gateway registration or backfill, forecast activation, production deployment, full
administrator dashboard, authentication, permanent data deletion or externally owned
tool changes are included. Coordinate any shared package/configuration changes or live
server use with the Nepal-cloud and BAFU-Mac-mini sessions through the owner. Use a
separate worktree and feature branch and preserve their work and running systems.
This vision grants no infrastructure spending or deployment authority. Its publication
does not deliver or close the Effort and does not start implementation.
