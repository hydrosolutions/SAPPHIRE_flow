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

## 10. Installation check with a disposable demo stack (draft, grill-me 2026-11-13)

Owner decisions from the grill-me session. It runs across the Gateway and SAPPHIRE
preparation efforts (#452, #453), stops before models (so it does not involve #454), and
still needs a focused vision before implementation. Anyone who sets up a deployment (an
installer or admin, not only hydrosolutions) can run it to prove the onboarding modules
are configured correctly, and then repeat the same checklist on their own stations.

**Staging (the Nepal workshop is about two weeks away).**
1. *Workshop:* the admin checklist, run by a facilitator on prepared data. A self-service
   check is not buildable in that time. Nepali counterparts may onboard additional
   stations during or after the workshop; the second DHM batch (October 2026 delivery,
   #453 M1) tests part of the onboarding and is not forgotten.
2. *Swiss installation check first:* guided steps with real public Swiss data.
3. *Then a synthetic Nepal test gauge* (not the project's "virtual station", which means
   ungauged/calculated): synthetic observations for history and live, to prove the basin
   extraction package, the real Gateway path and the Nepal weather route. Needs a
   generator that must stay consistent with the real formats, so it comes second.
Later: a one-command mode that does most steps (minimise manual steps); users then choose
guided or one-command.

**What is tested.** The installer's own real configuration (settings, credentials,
Gateway key, overlays) running against a throwaway database in a separate Compose project
(Infomaniak or the user's machine). Teardown is deleting that stack: no deletion
exception and no new deletion code, and the `sandbox` organization exists only inside it.
The one thing that stays is a local evidence file (date, who, tool version, per-step
results) that the installer may send to hydrosolutions; nothing is sent automatically.

**Steps.** A quick read-only configuration check, then the real onboarding steps on two
Swiss discharge stations, from the approved handover to station records, history import
through the delivered-files route, QC, the readiness report, and switching on observation
collection with one live poll for the two stations (well inside the BAFU rate limit). No
models, no forecast. A final check command reports pass or fail per step; known setup
problems get a plain-language fix and a pointer to the checklist row, others show the raw
error with a log pointer. The core check targets about 15 minutes.

**Demo package.** Prebuilt from the project's public BAFU data: basin outlines, static
attributes, daily discharge history and a small weather history, so the check does not
need the Nepal extraction tools or the slow Gateway backfill. Real public Swiss data, no
fake adapter, nothing invented. Hosting: a release file if size allows, otherwise Zenodo
or large-file storage, verified by checksum in the first step. Redistribution licences of
the underlying datasets must be confirmed first.

**Gateway.** The Gateway keeps basins per customer account. Each installer registers the
demo basin once under their own account in an optional second part (with the real
backfill, when they have time); it stays and is reused, and is documented as the one known
leftover until removal is possible. Gateway removal stays excluded; the owner pursues a
scoped removal with the Gateway developer separately.

Open points for the focused vision: licences and hosting of the package; whether Swiss
basins pass through the Gateway and statics tooling (the prebuilt package avoids
extraction, the optional Gateway part still needs it); the exact list of known setup
problems with plain-language fixes; the synthetic gauge generator; and how a user maps
the demo steps to their own stations.

## 11. Order of work to the Nepal workshop (agreed 2026-11-14)

Reference only; estimates are unmeasured and the workshop date (about 27 November 2026)
is approximate. The 19 November demo is not touched. Nothing here authorizes
implementation, deployment or live writes; each step keeps its own gate.

| Track | Work | Days | Gate |
|---|---|---|---|
| A. Gateway rehearsal (#452) | Owner reviews the workflow and supplies the inputs (Gateway base URL, new Gateway name, history window, forecast scope, actor name, review reference, evidence folder outside any repo); offline `prepare` for Trishuli, then live steps; Gateway-developer review of the checklist | 1-2 | Owner |
| B. Delivered files (#453 M1) | Plan; build and test (manifest, admission command, one guard migration, QC, operator report); dry run, then deploy and run on the Nepal host | 1; 5-7; 0.5-1 | Independent review (security review for the guard); owner merges; owner and deployment operator for the host; Nepal-cloud coordination |
| C. Facilitator checklist (#453 M3) | Spike: what a facilitator types today for a new station record, package and weather binding, and the gaps; then checklist v0 with explicit manual rows | 1; 1-2 | After A |
| D. Sandbox groundwork | Confirm data licences, pick the two Swiss stations | 0.5 | Owner |

Critical path is B (reaches the host around day 10-12 if started at once). Cut line
24 November: if B is not deployed by then, the workshop uses the September data and
presents the October delivery as the next step; nothing else depends on it.
Not before the workshop: the self-service sandbox, the general "register a new station"
command (#453 M2; the checklist covers the gap manually), water-level files, datum
offsets and the synthetic Nepal gauge. A general station-registration command appears
not to exist today (the existing stations command covers only the September six);
the spike in C confirms this.

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
