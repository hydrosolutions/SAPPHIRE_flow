# Staged station onboarding toward a production pipeline

## Accepted outcome

Provide a guided, resumable workflow from new Nepal stations to scheduled
forecasts. Deliver it incrementally, starting with a demonstrable evaluation
workflow and developing it into a production onboarding pipeline. The initial
delivery supports testing and hydrologist feedback; it is not production
certification. This vision records the owner's accepted discovery decisions.
It is not an implementation plan or permission to deploy.

The operator follows one coherent, staged journey, not one uninterrupted
command: basin extraction and review, Gateway preparation and manual backfill,
SAPPHIRE preparation, model evaluation, and human-approved activation.

## Reuse and ownership boundaries

Reuse existing software rather than rebuilding its capabilities:

| Capability | Existing component |
|---|---|
| Basin delineation and static attributes | [static-attrs-nepal](https://github.com/hydrosolutions/static-attrs-nepal) |
| Gateway integration and user documentation | [recap-dg-client](https://github.com/hydrosolutions/recap-dg-client/) |
| Station onboarding, ingestion, model evaluation and forecasting | This SAPPHIRE Flow repository |
| Operational forecast review | [SAPPHIRE-flow-map](https://github.com/hydrosolutions/SAPPHIRE-flow-map) |

The owner also supplied this local extraction handover location:

```text
/Users/bea/Library/CloudStorage/OneDrive-hydrosolutionsltd-TobiasSiegfried-01_PROJECTS_ACTIVE/2025_01 ReCAP Nepal (SDC)/09_handover_prep/static_attributes
```

Inspect the handover and repository interfaces before integrating; do not assume
that their versions are identical. Local dataset paths are deployment inputs,
not portable defaults to hard-code or data to copy into this repository.

**No edits to the other tools are authorized.** They belong to other developers.
If a supported interface lacks a required capability, document the gap and
escalate to the project owner. Do not patch, fork around, or silently replace
another developer's behavior. This boundary also applies to model dependencies.
The existing forecast dashboard serves operational forecast review; it is not
an administrator or system-management dashboard. Reuse its existing capabilities
where suitable and escalate any required changes.

## 1. Extract and approve basins

Use the existing extraction software to generate basin geometry and static
attributes from reviewed station identities and coordinates. Reuse the previous
interactive basin-map approach where available. The map must show the supplied
gauge locations, basin boundaries, snapped outlets, and available drainage-area
comparisons, with two selectable background layers:

- visible-band, true-color satellite imagery;
- a terrain background.

Provide a simple local review page with an **Approve** action. A hydrologist
reviews the extracted catchments before Gateway upload. Default to whole-batch
approval, while allowing individual stations to remain unapproved. Only approved
basins proceed. Keep rejected or uncertain stations pending correction; do not
add an interactive basin-editing tool in the initial delivery.

Keep this proportional: one facilitator-operated computer, a locally bound page,
and no new login system or publicly accessible approval endpoint. Retain enough
information to associate approval with the reviewed geometry. Changed geometry
requires review again. Confirm imagery availability, usage terms and workshop
connectivity during preparation.

## 2. Prepare the Data Gateway

Register accepted geometries and required parameters through supported APIs.
The Gateway provides APIs, not a verified onboarding CLI in the client repository.
Propose parameters and historical periods from the intended models' requirements;
let the operator change them. Report unmet model requirements rather than silently
omitting them. Offer an optional review of the Gateway admin page.

Pause for **manual ERA5-Land and snow history backfills through the DAGs dashboard**.
Provide a clear checklist of the requested geometries, variables and periods and
retain the operator's job references. Do not automate these manual backfills.
On resume, verify actual returned coverage, not merely submission or job completion.
Continue eligible stations and report explicit holds for the others.

A shared forecast refresh/backfill can affect other geometries and overwrite
outputs. It requires separate scope confirmation and authorization; it must not
be hidden inside station registration. Keep proposed and confirmed Gateway
identifiers distinct and preserve immutable geometry/package versions.

Ask the Data Gateway developer to review this integration and the operator
procedure before the full rehearsal. Route required upstream changes through
the project owner. Credentials must not appear in inventories, reports or URLs
shown to reviewers.

## 3. Onboard into SAPPHIRE Flow

Reuse existing station import, basin-package import, binding, ingestion and
qualification services. Resolve or create station records through the authorized
import path, then import accepted packages and exact Gateway bindings. Keep
station identity, tenant, geometry, static attributes and source bindings
consistent; do not reuse Swiss defaults for Nepal onboarding.

Persist supported historical forcing and check actual usable coverage against
the selected model's requirements and target observations. Preserve observation
QC and delivery lineage. Data availability in the Gateway does not itself prove
that SAPPHIRE supports a variable's units and temporal semantics.

Preserve package versions, provenance and completed work. Interrupted runs must
resume safely without duplicate registrations or records. External Gateway work
and database imports are not one transaction: retain completed work and reconcile
it before retrying. Distinguish data readiness, model readiness and activation.
Every requested station has a clear outcome: completed, awaiting review,
awaiting external work, or held with a reason.

## 4. Evaluate an existing model or retrain

Support both paths explicitly. Start with `cmal_small`, preferred in this
workflow's model-priority configuration without changing unrelated deployments.
Test an existing compatible model first, then exercise retraining. Do not retrain
automatically just because a station was added, and do not bypass compatibility
checks when an existing model cannot serve the new stations.

Present per-station skill metrics and hydrographs for forecast or hindcast
review, reusing existing evaluation and review capabilities. Observe the shared
ForecastInterface contract; a gap is not permission to modify another developer's
model or to invent a SAPPHIRE-side workaround.

## 5. Review and activate forecasts

A hydrologist must review skill metrics and hydrographs before making a model
active. Default approval to the whole batch while allowing a subset. Associate
approval with the evaluated model version and results. Retrained or replacement
models require fresh review before activation.

For the initial delivery, activation enables scheduled forecasts in the designated
evaluation environment. All existing authorized testing hydrologists should be
able to see trial forecasts within existing access boundaries. This does not
authorize widening tenant access or public data release. Public publication and
alerts are not enabled implicitly.

The ultimate destination is production: once the workflow is approved for that
use, activation also enables scheduled forecasts in the production environment.
Preserve a future option to publish a model's forecasts with explicit **evaluation
status within production**, while approved production models continue in parallel.
Evaluation forecasts must remain distinguishable and must not silently replace
production forecasts or acquire their alerting authority. This is a future
capability, not an initial-delivery requirement.

## 6. Checks, exceptions and reviewer identity

Begin with broad, permissive skill thresholds and refine them using testing and
hydrologist feedback. Required data integrity and compatibility checks are
separate from model-performance thresholds.

Include an explicit evaluation-only exception to a skill threshold: record a
reason and reviewer, and keep the exception visible alongside the results.
Required-input, geometry, model-compatibility and access checks cannot be
overridden. Missing data or an unevaluable model is not a weak skill score.

Initially record a self-declared reviewer name without claiming authenticated
identity. Authentication must be added later, after requirements are clarified
with NHS, the hydromet counterpart. Authenticated reviews will then record the
reviewer's username; preserve the distinction from earlier self-declared reviews.
Production acceptance criteria and exception authority must be agreed before
production rollout. Permissive evaluation settings do not automatically become
production policy.

## 7. Retire stations reversibly

Provide a station retirement operation that stops forecast production and
station-specific ingestion by default, with an explicit option to keep ingestion
running. Preserve observations, forecasts, model artifacts, packages and audit
history. Leave shared Gateway subscriptions untouched. Reactivation requires
fresh readiness checks and hydrologist approval. Permanent deletion and Gateway
cleanup are outside scope.

Reversible retirement must never use the permanent decommissioned transition.
The current station lifecycle gates both forecast execution and observation
ingestion, so a station-status change alone cannot implement the retain-ingestion
option. That option requires a separate control that reliably prevents forecast
participation while allowing ingestion; if that separation cannot be established,
keep forecasts stopped and report the hold rather than enabling both implicitly.
Choose the mechanism during implementation using existing controls where suitable.

## 8. Future administrator dashboard

Deliver the operator workflow and small basin-review page first, not a full
administrator dashboard. Keep configuration explicit and reusable so a later
dashboard guides authorized hydrologists through the same workflow instead of
creating a second onboarding pathway. Clarify authentication, authorization and
review responsibilities with NHS before implementing the final multi-user design.

## 9. Workshop, parallel work and delivery

Deliver stages in order while keeping the full forecast journey as the goal.
Use lessons from the earlier six- and twenty-one-station Nepal onboarding runs,
but use **new stations for live trials**. Do not reset, re-register or disturb
existing evaluation-server stations to demonstrate onboarding.

Rehearse the full workflow before the Nepal visit, including a Gateway-developer
review. The workshop may span five working days. If processing time or readiness
prevents completion, demonstrate prepared results and complete the full training
later when two counterpart staff visit Zurich. Do not claim that manual backfills
or model training will finish within one workshop session.

Two other sessions are working in parallel: Nepal forecast evaluation deployment
on a cloud server, and BAFU forecasts on the Mac mini. This work uses separate
worktrees and feature branches. Do not take over their deployment configuration,
station sets, running services or uncommitted changes. Coordinate through the
owner when shared service changes, model defaults, database migrations, deployment
registration or live acceptance would overlap. Branch isolation alone does not
isolate shared runtimes. This vision authorizes no deployment or live-data writes.

## Evidence of success

An operator can take a new Nepal station batch through extraction, map review,
Gateway registration, the manual-history handoff, SAPPHIRE preparation, model
evaluation and approved activation. Partial approval and incomplete coverage
produce per-station holds rather than silent omission. Pauses and retries preserve
completed work without duplicates. The two model paths and reversible retirement
are demonstrable. Existing evaluation stations and unrelated deployments remain
unaffected. Review outputs contain no credentials or unauthorized raw-data exports.

The initial workflow demonstrates this journey without claiming production
readiness, while retaining a path to authenticated production operation.

## Repository grounding and remaining investigation

Source baseline for authoring: SAPPHIRE Flow `f99fe581f837f3de1164f8422ff5c1ef79286250`.
These are reuse points, not claims that the new workflow already exists:

- `docs/operations/nepal-station-onboarding-runbook.md` documents the existing
  extraction/Gateway/SAPPHIRE handoffs, manual scope checks and named history route.
- `docs/requirements/04-basin-static-artifact-contract.md` and
  `docs/operations/basin-static-importer-runbook.md` define the accepted package,
  immutable identifiers, joins and importer behavior.
- `src/sapphire_flow/cli/onboard_nepal.py` and
  `src/sapphire_flow/services/nepal_onboarding.py` provide existing Nepal paths;
  some remain six-station/CHWRR-specific. Named weather scopes already support
  history submission. Submission success is not completed ingestion.
- `config.toml` has `[model_priorities]`: lower numbers mean preferred in the
  PRIMARY chain. This is selection priority, not an ensemble blending weight.
  Verify the effective deployment overlay before making `cmal_small` preferred.
- The extraction repository documents `static_attrs_nepal.appliance`, local
  HFX/HydroATLAS/ERA5-Land inputs and `basin-static-artifact/v1` output. Confirm
  the supplied handover version and locate the earlier basin-map implementation.
- The [Gateway geometry guide](https://github.com/hydrosolutions/recap-dg-client/blob/main/docs/onboarding-a-new-geometry.md)
  describes manual onboarding. Confirm the supported registration/subscription
  API capabilities with its developer rather than inferring them from export
  client methods or automating the admin UI.

Before implementation, inspect these current sources, `cmal_small` requirements,
existing metric/hydrograph review capabilities, and imagery usage terms. Record
external integration gaps for owner escalation. Do not create speculative
replacement components or interpret historical runbook status as a fresh runtime
verification. No numerical production thresholds or final NHS auth requirements
have been agreed by this vision.
