# Prepare approved basins in the Data Gateway

Program: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/450
Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/452

## Accepted outcome

Extend SAPPHIRE Flow's staged station-onboarding journey from an approved basin
handover to verified Gateway registrations, subscriptions and source-data
coverage. A facilitator can prepare a new batch, pause for manual historical
backfills, and resume without losing completed work or silently duplicating
registrations. Eligible stations can proceed to the later SAPPHIRE preparation
stage; other stations remain visibly held with reasons.

This is integration work in SAPPHIRE Flow, not development of the Recap Data
Gateway or its client. Reuse supported Gateway APIs and existing client functions.
The owner approved this integration and selected Trishuli as the first rehearsal
station. This document captures the outcome. Implementation follows the invoked
global skill and current `AGENTS.md` safeguards, including independent review and
owner-only PR approval and merge.

The [overarching vision](2026-11-05-staged-station-onboarding-toward-a-production-pipeline.md)
provides the full journey and shared boundaries. The delivered
[basin-review vision](2026-11-05-extract-and-approve-station-basins.md) supplies the
approved-only handover. This stage ends before SAPPHIRE station/database import,
model evaluation or forecast activation.

## Start from the approved extraction output

Accept only the approved subset from the extraction/review handover. Preserve
text station codes, original coordinates, station-to-feature associations,
source package identity and recorded approval. Preserve the immutable original
package and distinguish proposed Gateway names from confirmed registration names.
Never assume that a proposed name already denotes a registered geometry.

**Do not repeat geometry checking for approved extraction output.** The owner
explicitly considers geometries that passed extraction and review suitable for
upload. Do not introduce another delineation, validity audit, comparison, scientific
approval or Gateway geometry round-trip equality gate. Checking handover identity,
checksums and approval applicability is not another geometry review. Changed
geometry must return through the upstream extraction/review process, not be
repaired or substituted in this stage.

Upload the existing approved GeoPackage through the supported API. Do not send
the whole package ZIP or static-attribute table as the geometry upload. Check
whether the request succeeded, retain useful warnings and failures, and capture
confirmed registration identifiers and the station/feature mapping without
altering the original handover. Record subsequent Gateway evidence separately;
if a downstream package needs corrected metadata, derive a new immutable package
rather than editing the approved source in place.

## Propose and confirm the requested data

Propose subscriptions and history periods from the intended model requirements,
starting with the Program's preferred `cmal_small` workflow. Let the operator
change the proposal before confirmation. Clearly show the new geometry/batch,
variables, data streams and historical periods that will be requested. Do not
silently omit an unavailable required input or claim that Gateway availability
establishes SAPPHIRE model compatibility.

Use supported registration and subscription APIs. Offer an optional Gateway
admin-page review; do not automate that UI or make manual geometry rechecking a
normal prerequisite. Keep ERA5-Land, IFS and snow requirements distinct, including
snow forecast versus reanalysis subscriptions. Do not enable unnecessary streams,
alter unrelated subscriptions or create global parameter definitions as a shortcut
when a required parameter is unavailable. Route missing capabilities to the owner
and Vatro.

Do not invent a universal training-history window or claim that forecast input
lookback alone defines a retraining period. Proposals must explain their model
basis and any information the operator must supply. Reflect the Gateway's actual
request units, such as whole calendar years for the documented ERA5 history job,
and expected source publication lag rather than promising unavailable recent data.

## Manual history handoff and verified resume

ERA5-Land and snow history backfills remain manual through the Gateway DAGs
dashboard. Provide simple numbered instructions identifying the exact confirmed
geometries, variables, streams and periods, and retain operator-supplied job
references. Confirm the supported snow procedure with Vatro; do not invent a DAG
name or treat an ERA5 job as proof of snow completion.

On resume, fetch actual data for the requested identifiers, variables and periods.
Report returned usable coverage, missing intervals or values, and explicit
per-station holds. A submitted job, completed dashboard run, enabled subscription
or successful empty HTTP response is not coverage evidence. Distinguish source
availability from later persistence, units/time interpretation and model readiness
inside SAPPHIRE. Data checks must cover the agreed requested scope rather than
claiming completeness from a small successful sample. Keep eligible stations
separate from held stations without hiding either outcome.

Do not automatically trigger shared IFS forecast refreshes/backfills. They may
reprocess other geometries and overwrite shared outputs. Any such run requires
separate scope confirmation, coordination and owner authorization. Otherwise wait
for the needed data or report a hold; do not make a broad refresh an invisible
part of registration.

## Resumability and uncertainty

Retain enough evidence to reconcile completed external work when the facilitator
returns or a request fails. Gateway actions and local state are not one
transaction. Reuse verified completed work; do not blindly retry writes or assign
a fresh geometry name merely because a response was lost. The same registration
name must never be reused for changed geometry.

An uncertain upload outcome is an operational hold, not a reason to repeat
geometry validation. Use supported registration evidence and operator/Vatro
reconciliation where needed. Do not assume that a name collision proves that the
existing registration belongs to this handover. Keep the hold explicit if available
interfaces cannot resolve uncertainty safely. Subscription reconciliation must
also preserve unrelated work and distinguish already-completed requests from new
writes. Permanent deletion or shared Gateway cleanup is not a recovery mechanism.

Keep the workflow coherent with the existing facilitator-operated onboarding
journey, with clear confirmations, pause/resume guidance and per-station outcomes.
The detailed presentation is an implementation choice; a full administrator
dashboard, new authentication system or public endpoint is not part of this work.

## Ownership, authority and live rehearsal

Vatro owns required Gateway/client changes. Do not edit, fork around, or silently
replace externally owned behavior. Document an interface gap and communicate it
to Vatro through the owner. Obtain Vatro's review of the integration and operator
procedure before the full rehearsal. An agent code review is not that developer
review. Preserve the ForecastInterface contract and model ownership boundaries;
no Aquacast changes are authorized by a missing requirement or integration gap.

The owner permits data fetches and registration of new shapefiles. This does not
authorize changes to existing stations, shared-service configuration, broad
forecast backfills, deployment, new infrastructure spending or forecast activation.
Show and confirm the requested subscription scope before live subscription writes.
Coordinate any shared backend/runtime overlap through the owner with the Nepal-cloud
and BAFU-Mac-mini work. Use an isolated branch/checkout and preserve existing services.

Use the existing owner-approved Trishuli research handover, station code `00001`,
for the first Gateway rehearsal. It is not an operational DHM station. Preserve
its research identity and original approval; do not use the separate, still-pending
automatic extraction output or transfer approval to it. Register under a fresh,
confirmed Gateway name and leave existing six- and twenty-one-station registrations
untouched. Reuse retained confirmed evidence on reruns rather than creating
additional trial registrations for demonstration.

Keep credentials out of inventories, URLs shown to reviewers, reports and logs.
Use existing approved secret handling and data-use boundaries. Coverage evidence
must not become an unauthorized raw-data export.

## Repository and API grounding

Discovery inspected the existing onboarding runbook, approved handover and client,
and the public Gateway OpenAPI schema without making authenticated Gateway writes.
The published schema exposes geometry upload, parameter catalog reads, subscription
list/create/update operations and data exports. The inspected Python client facade
primarily exposes weather and snow data retrieval; it is not an onboarding CLI.
Using a supported Gateway endpoint from SAPPHIRE does not authorize adding methods
to Vatro's client.

Reuse and inspect during implementation:

- `docs/operations/nepal-station-onboarding-runbook.md` for registration, manual
  history handoff and the separation from database import. Its six-station examples
  are historical context, not permission to reuse those stations or DHM defaults.
- `docs/operations/recap-gateway-runbook.md` for data access and coverage evidence.
- `src/sapphire_flow/basin_operator/` for approved handover and facilitator workflow.
- `src/sapphire_flow/services/basin_package_loader.py` and the basin/static contract
  for identities and exact joins, not a new geometry-review gate.
- The [Gateway onboarding guide](https://github.com/hydrosolutions/recap-dg-client/blob/main/docs/onboarding-a-new-geometry.md)
  and [public API schema](https://recap.ieasyhydro.org/sdk/openapi.json).

The documented upload response contains message/detail text and optional warnings,
not a structured geometry read-back contract. Do not require a new geometry
verification endpoint. Confirm response interpretation and ambiguous-upload recovery
with Vatro. Schema visibility proves interface documentation, not current credential
permissions, successful registration, job execution or data availability.

## Evidence of success and exclusions

Demonstrate the approved Trishuli handover passing through real registration,
confirmed required subscriptions, the manual-history handoff and data coverage
read-back, after Vatro's review and the applicable operator confirmations. Report
actual results truthfully; incomplete history is a visible hold, not full live
acceptance. Demonstrate partial-batch holds and interrupted/repeated operations
without duplicate registrations or changes to existing work. Provide simple
operator instructions and retain evidence sufficient to resume.

Tests or fixtures can establish error/hold behavior but cannot substitute for the
live rehearsal. The final report distinguishes implemented capability, completed
live steps and outstanding external work. No source package or approval is mutated.

Exclude SAPPHIRE station import, historical database ingestion, model retraining,
performance approval, activation, public publication, alerts, NHS authentication,
a full admin dashboard, production deployment, external-tool development, permanent
deletion and shared Gateway cleanup. Those boundaries are not waived by completion
of this integration stage.
