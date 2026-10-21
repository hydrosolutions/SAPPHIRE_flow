# Nepal21 forecasts on Infomaniak through the existing dashboard

Program: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/352
Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/354

## Outcome and scope

Assess all 21 delivered Nepal stations and deploy every forecast-capable
station/model combination to the Infomaniak **test** system. Use the intended
Prefect → services/adapters/stores → ForecastInterface (FI) → persisted forecasts
→ normal authenticated API → existing SAPPHIRE-flow-map dashboard architecture.
Do not turn the local presentation runner/provider into the permanent system.

Keep all eligible available models visible per station. CMAL/Aquacast remains an
explicit target; existing legitimate regression models can also qualify. A model
or station failure must not prevent healthy combinations from completing or
becoming readable. Report missing CMAL coverage rather than quietly substituting
a regression-only outcome. Do not promise that all 21 will meet their inputs.

Onboarding includes hindcasts and skill assessment, or a specific, recorded
**not-assessable** outcome. Lack of established skill alone does not exclude an
otherwise forecast-capable combination from testing. Missing mandatory FI inputs
still blocks execution. Test enablement is not operational promotion: no alerts,
warning-service claims or automatic scientific-validity upgrade.

SAPPHIRE_flow owns backend readiness and delivery. The existing
SAPPHIRE-flow-map project owns UI and consumer implementation. Coordinate necessary
contract/binding work with that owner; no replacement dashboard, new selector or
analysis-panel requirement. Swiss work remains independent. The shared contract
issue #378 remains applicable, not a new dependency on completing Swiss delivery.

## Status and historical continuity

The owner accepted M0 baseline reconciliation and authorised M1 preparation.
M1 is active and unaccepted. On 2026-10-21 the owner deferred backup
commissioning and recovery testing, including plaintext recovery tests. These are
not accepted or complete and no longer gate M1, M2 or the forecast/dashboard
critical path. Every milestone still ends with owner review and re-estimation.
This scope update authorises planning changes only, not implementation or deployment.

This is the continuation of the same Nepal Effort, not a second vision. Its
historical filename is retained for stable links. The October 7 demonstration
scope/deadline and exclusion of hindcast/skill work are superseded by the owner's
Nepal21 test-deployment scope. The earlier agreement remains available in
[the prior vision](https://github.com/hydrosolutions/SAPPHIRE_flow/blob/7942349d49f5dd764b88cd7fd6ad038aa596e5b8/planning/visions/2026-10-01-nepal6-current-forecast-data-for-the-existing-dashboard.md)
and Git history; it is not a competing active plan.

The presentation finished with genuine local H10 model output for stations 450,
604.5 and 684. That proves local execution and display, not operational validity,
server integration, skill or delivery for all 21. Preserve the local dashboards,
source snapshots, failed attempts and evidence. Do not rerun acquisitions/models
merely to reconstruct an already documented result.

The accepted M0 inspection found 21 onboarding stations, 18 with RAW measured
water-level archives, 1,233,045 stored observations, and zero stored forecasts in
the isolated archive database. It found no basin links, model assignments,
historical forcing, hindcasts or skill results there, and no normal application
stack running on the inspected Infomaniak daemon. These are dated baseline facts,
not an assertion about the host at every future invocation. RAW water level is
not qualified discharge or forecast readiness. Stations 280, 445 and 695 were
held without admitted database observations. The other stations remain in the
readiness account, not silently dropped.

The initial server-path candidates are 450/604.5/684, subject to fresh input
qualification. The other original-six stations 447/647/670 had recent discharge
gaps under the demonstrated model's mandatory-input declaration. The other 15
stations need individual identity/data/basin/static/forcing/model qualification.
Local results or complete archives are not permission to bypass those checks.

## Model, data and scientific boundaries

BIPAD remains the owner-selected live observation source, not an automatic fallback
from another provider. Verify explicit per-station API/gauge bindings and actual
product/history support; a similar place name is not identity proof. Its measured
water levels require the qualified conversion/reference policy below before use
as discharge. Reuse the existing DHM/BIPAD adapter rather than creating another
observation runner. Extend the station scope without silently changing this source
decision; report unsupported bindings as gaps.

Target genuine current weather-driven forecasting. Historical or climatological
output alone cannot establish that delivery. Legitimate baseline/regression products
may be available alongside it, but report remaining target-model/weather coverage
separately rather than declaring completion from those substitutes.

- Follow FI exactly. Fix a noncompliant model, or co-design a genuine interface gap
  upstream. Never invent discharge, silently fill mandatory inputs, weaken NaN
  gates, or patch around FI in the orchestrator. Anticipated model failures return
  the FI failure value; exception catching is not the intended failure protocol.
- Preserve real deterministic means independently of quantiles/median, all native
  quantiles, units, resolutions, horizons, issue/valid times, statuses and flags.
  The demonstrated H10 product is evidence, not a universal horizon restriction.
- Maintain provenance for raw measurements, QC, temporal aggregation, permitted
  conversion, historical forcing, forecast source cycle, imputations and artifacts.
  Source age and partial-day coverage must remain explicit. Do not conflate source
  time, model execution time, persisted issue time and API-response generation time.
- Reuse original station UUIDs and RAW custody when transferring the archive into
  the intended database. Preserve originals; migration-number equality alone does
  not prove schema, privilege or trigger compatibility.
- Imported artifacts/checkpoints and source dependencies need accepted identities,
  compatible runtimes and safe loading. Demonstrated local changes are not all on
  main; integrate only necessary reviewed fixes, not wholesale snapshot copies.
- Before M2 scientific activation, settle station-specific admissibility of
  provisional observations and conversion/reference evidence. Before M3 evaluation,
  settle evaluation periods, legitimate observations/baselines and treatment of
  unknown training overlap. These are bounded scientific decisions, not reasons
  to reopen the deployment architecture or silently waive requirements.
- Hindcast/skill reports must distinguish actual evaluation from not-assessable
  cases, with reasons, sample/forcing limits and training-overlap uncertainty.
  Invalid demonstration outputs or provisional expired-curve discharge are not
  legitimate training, calibration, hindcast or skill evidence. Skill results do
  not grant new rights to publish restricted derived metrics.

### Retained expired-rating test boundary

The prior bounded same-gauge expired-rating exception remains a distinct invalid
pathway-test class, not ordinary test eligibility or operational validity. Use the
newest curve for the same verified gauge, preserve its original dates/version and
measured-level lineage, and establish units, reference/datum and domain compatibility.
Unknown reference compatibility remains held; do not borrow curves or silently
assume zero offset. Do not generalise local presentation workarounds to server
operation. Server use of the retained invalid class requires the M2 station-specific
owner admissibility decision, same-gauge curve and verified reference compatibility.

Every affected forecast and downstream combined/group product retains machine-readable
invalidity and safe reasons across the full consumed lookback. Keep invalid test
selection, retry/state and last-good handling separate from ordinary valid results.
Such inputs/outputs cannot enter training, evaluation, alerts or replace valid
latest/last-good output. Consumer display must make invalidity unmistakable before
invited-viewer use; no restricted observations, curve coefficients or sensitive QC
values may leak through those reasons. An unsupported persistence/selection boundary
is a concrete implementation hold, not permission to flatten the classification.

## Current, truthful data through the existing API

A dashboard reload reads the latest eligible persisted result for each authorised
station/model without a separate publishing-batch delay. Reads never trigger model
training or prediction. Preserve version/supersession, publication policy, data-use
classification and QC/lifecycle semantics; withheld/withdrawn results stay withheld.
Backend scheduled production is required eventually; live push/browser auto-refresh
while a page stays open is not an added acceptance gate.

Trace displayed values to stored forecast/model/station identity and actual issue
and valid times. Preserve healthy members independently. A failed cycle or refresh
retains correctly labelled last-good data, or explicit unavailability if none exists.
A fresh HTTP response or copied file does not make an old forecast current.

Agree and test applicable identity, network, source-specific observation support and
time semantics, forecast uncertainty, freshness, safe failures and optional existing
metadata with the map owner. Missing optional thresholds/calibration metadata is
explicitly absent/unknown/withheld, not fabricated and not automatically a blocker.
No new UI features or analysis bindings are implied by onboarding evaluation.

## Active M1 delivery policy — owner update 2026-10-21

1. **No container registry.** Keep upstream repositories and installed dependency
   code private. CI builds and verifies both default and Aquacast linux/amd64
   application variants, then transfers the exact tested images privately to
   Infomaniak. The server verifies and loads them; it does not rebuild them.
2. **Publication, transfer and activation are distinct.** The owner publishes
   release identity. Verified transfer is automatic after that accepted release;
   activation needs an explicit deployment request. A merge is neither publication
   nor activation authority. No production authority is granted here.
3. Preserve existing receipts and immutable image identities. Mandatory tests,
   actual-image proofs and security scans remain. Keep private images, dependency
   evidence, station data, checkpoints and credentials out of public CI outputs.
   Externally enforced credential/ref/reviewer controls must admit only trusted
   code; runtime secrets and model/data artifacts stay outside images.
4. **No paid image hosting/cache expansion or automatic paid fallback.** Measure
   cold build/export disk, time, archive size and the permitted private transfer
   route on existing approved free resources. Unknown capacity is a delivery hold,
   not permission to publish private artifacts or rebuild on the server.
5. Preserve the isolated archive, original UUIDs and RAW provenance, original dirty
   checkout, local dashboards and scientific evidence. Only separately authorised
   additive work in a new isolated target is contemplated. Destructive or in-place
   source changes remain held for separate protection and approval. Compare actual
   schema/roles/guards and transferred content; migration labels alone are not proof.
   Keep the previous application release locally when one exists. Image rollback
   is not database recovery and cannot undo a migration.
6. Keep schedules and pending/manual/retry queues held during commissioning,
   including repeated init and restart. Avoid unintended Swiss/default work.
   M1 does not enable backup schedules, ingestion, forecasts or training. Later
   Nepal schedule activation still needs the appropriate milestone and explicit
   request. Runtime checks do not authorise models or station activation.

### Deferred recovery scope — not accepted or complete

The owner's 2026-10-21 decision supersedes the earlier requirement to finish full
recovery before runtime activation/seeding and the earlier M1 daily-backup
exception. Cloudflare account access, allowance checks and a recovery machine are
**not current blockers**. Backup commissioning, plaintext recovery tests and
recovery acceptance are outside the active forecast/dashboard path, not hidden
prerequisites for a later milestone.

The prior 2026-10-20 policy remains the historical specification for deferred work:
one encrypted private Cloudflare recovery set containing DB/current images/model
artifacts/protected configuration; successor verification before predecessor
deletion; measured free account storage/operation allowance and peak overlap;
independent key/secret custody; complete daily and pre-deployment/pre-DB-change
backups; a fresh verified off-host prechange point; and proved 24-hour loss and
four-working-hour restoration targets. None of these is claimed implemented,
verified or waived into a successful result. Reopening that work needs separate
planning, review, protection/access decisions and explicit activation. No second
automatic replica or paid fallback is authorised.

Proceeding without those gates carries operational data-loss, reconstruction and
downtime risk. Recovery may exceed the former targets or be impossible for lost
state; a successful dump, local image rollback or healthy API is not verified DR.
Preserve originals and record actual independent delivery/evidence custody without
inventing copies or permission to delete them. The original dirty checkout is on
the local Mac, not established as being on Infomaniak. This deferral does not relax
protected-data access or preservation guards or permit destructive source changes.

## Data protection and authority

Keep invited access, regional isolation, scoped authentication and no office-network
exposure. The [DHM hosting assumption](../../docs/decisions/2026-09-30-dhm-data-hosting-assumption.md)
is owner authority, not DHM consent. Retain its objection/changed-conditions process
and the distinction between protected preservation and operational replication.
On 2026-10-21 the owner confirmed that this assumption covers **all 21 delivered
Nepal stations, including their historical water-level records**. The earlier
six-station scope is superseded for this owner hosting/display decision, not for
scientific qualification or station identity checks. It is not independent legal
consent from DHM. Existing restrictions and the objection/changed-conditions
process remain in force.

The Cloudflare destination remains a deferred private-recovery choice, not public
release/download permission or current backup activation. Restricted recovery
objects would still require encryption before any future authorised upload. No
plaintext recovery-test or decrypted-runtime-secret hosting authority is inferred
for CI or another machine. A DHM objection or changed conditions also stops new
Cloudflare replication and invokes the same hosting-removal/change process.
Protect delivery-tagged observations and recoverable values in diagnostics,
thresholds and derived output. No bulk-download or derived-metric-publication expansion.

This document records confirmed outcomes; it does not approve an unreviewed patch.
Follow independent Claude/Codex review, additional owner-commissioned review for
high-risk changes, required tests and owner-controlled PR approval/merge. Do not
silently expand production/scientific authority. No new platform, Kubernetes,
custom supervisor or model framework is requested.

## Milestones and success evidence

The [milestone map](../nepal21/milestones.md) is the single milestone-status record;
its [dependency graph](../nepal21/milestone-dependencies.json) distinguishes active
work, owner checkpoints and parallel readiness investigation. The
[M1 plan](../nepal21/m1-direct-transfer-runtime.md) is the current implementation
proposal. M0 accepted/M1 active is not a claim that M1 has passed or its plan is
implementation-ready before review and owner merge.

Success requires a full 21-station/model readiness account; persisted legitimate
onboarding/evaluation outcomes; source-bound FI forecasts using qualified current forcing stored through normal
services; authorised API/browser evidence and rejection of unauthorised/out-of-scope
reads without restricted-data/diagnostic leaks; separate Nepal/Swiss credentials
and no backend credentials in browser code; independent failure and last-good
behaviour; and repeated unattended cycles with an operator handoff that states the known
loss/reconstruction risks. Verified disaster recovery remains explicitly deferred,
not a forecast/dashboard completion claim.
Across at least two scheduled cycles, newly persisted results must be readable on
dashboard reload without manual export/redeployment, traced with the map owner to
forecast/model identity and issue time.
Report valid coverage, skill-unestablished test coverage and invalid pathway tests
separately. Do not mark the Effort delivered from a health check, model run, merge,
local presentation or smaller working subset alone.
