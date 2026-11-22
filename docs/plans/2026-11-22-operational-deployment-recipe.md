---
status: READY (N1a only; N1b–d execution remains DRAFT)
created: 2026-11-22
owner: deployment orchestrator
baseline: 5dd6232ec
parent: 2026-11-22-fresh-deployment-comparison.md
---

# Reproducible operational deployment recipe

## Decision and scope

Deliver a supported, operator-assisted recipe for the first real-data comparison,
then use its evidence to decide which installation steps merit automation. Reuse
external profiles, ordered Compose files, the existing registrar and onboarding
commands. Do not extend the paused installer into an operational controller in
this slice. Its two-job checks and retained records stay unchanged.

The administrator selects one private profile and its linked runbook. That runbook
owns the complete invocation, explicit registration inputs and supplemental steps;
the administrator must not reconstruct an overlay history or remember interactive
exports. Public documentation describes the procedure; actual configurations,
credentials, schedules, station lists, model choices and evidence stay private.

This plan separates a small code repair from preparation of the concrete operational
recipe. Only the code repair can become implementation-ready in this review.
Preparation of later packets is read-only/offline work; their execution still
requires review of the completed recipe, data handling and commissioning boundary.
No host creation, data transfer, registration or activation is authorized by this
document's preparation status.

## Evidence and existing capabilities

The private N0 inventory found more stored deployments than its captured Compose
invocation recreates, plus an omitted durable provider-path input. That invocation
may not be the operator's complete registration procedure. This is a reproducibility
gap, not authority to repair or re-register the existing server.

Two local, provider-free probes demonstrate a narrower public defect:

- `config/deployment_profile.py::_InstallationInput.permitted_environment` rejects
  both `SCHEDULE_REFRESH_OPERATIONAL_ERA5` and `SAPPHIRE_ERA5_WEATHER_SCOPE`.
  The schedule-key pattern excludes digits; the scope is absent from its allowlist.
- `tools/deployment_profile.py::_check_schedules` indexes the empty-environment
  defaults by every selected job name. A correctly supplied optional refresh job
  raises `KeyError`, because that job deliberately has no default registration.

The registrar already implements paired opt-in, explicit UTC, scope parameters,
pool routing and concurrency for recurring refresh. Do not duplicate that logic.
Its pure `build_deployment_specs(environment)` is the preview authority. The base
Compose init environment does not yet forward the paired refresh inputs.

The broader profile checks deliberately report operational provider/execution
coverage as pending. Fixing the above defects must not turn that pending recipe
into `PASS_OFFLINE`, or make it admissible to the paused installer/rehearsal.

## Dispatchable slices

| Slice | Deliverable / paths | Acceptance and evidence | Dependencies |
| --- | --- | --- | --- |
| N1a — optional weather preview | `src/sapphire_flow/config/deployment_profile.py`, `tools/deployment_profile.py`, `docker-compose.yml`; affected profile/reference documentation and boundary tests | Both absent preserves behavior; both present resolve the real registrar spec without a crash; incomplete pair, invalid cron and unforwarded scope/schedule refuse safely. No operational `PASS_OFFLINE` and no new runtime authority. | This plan's independent review; owner resolves any material findings |
| N1b — complete private recipe | Private profile, installation file, bounded Compose overlay and linked runbook outside Git; generic instructions in `docs/deployment/operational-comparison.md` | A clean-shell render and explicit registration inventory reproduce every required/manual job, including local forecast variants. Exact input references, mounts/readers and release identities accounted for; no secrets in preview. Synthetic Prefect proof covers first and repeated registration and a real child environment. | N0 inventory; N1a; reviewed concrete supplemental registration procedure |
| N1c — input reconstruction packet | Private bounded input manifest and dependency-ordered import procedure; link existing onboarding/model/provider runbooks | Pilot then full cohort, original provenance and time contracts, exact protected-data boundary, table/column closure where needed, target sizing and no copied execution/auth state. Dry-run or synthetic relational proof before real transfer. | N0; N1b configuration and model requirements; onboarding/weather owners' existing mechanisms |
| N1d — commissioning and cleanup packet | Private operations checklist; generic guide and parent plan status | Named operator, precise job enablement, due-run/coverage checks, finite provider budget, stop/recovery path, resource inventory and cleanup on failure. Resolve provider access and target size/cost before provisioning. | N1b/N1c completed and independently reviewed; N1a final source CI and patch review if used |

N1 is complete only when all four slices meet their gates. N1a alone is not an
operational installation qualification. N2 remains blocked on the complete N1
packet; do not silently substitute the synthetic installer qualification.

## N1a implementation contract

1. Add the two exact environment keys to the existing profile allowlist. Keep the
   schema version and all other restrictions; do not broaden the schedule regex
   or admit arbitrary environment names. No cron/scope default is invented.
2. Forward both optional values through normal Compose `init`, defaulting each to
   empty. With both absent, the registrar still creates no refresh deployment.
   A partially supplied pair must retain the registrar's fail-closed behavior.
3. Compare desired and rendered scope as well as schedule. A valid but different
   scope, or a scope/schedule dropped by a later overlay, must produce a value-free
   forwarding error. Keep the registrar as the source of parsing/selection truth.
4. Preview actual optional specs without assuming they occur in the default spec
   map. Mark an opt-in job and schedule as explicitly supplied; never describe it
   as a shared default. Include its selected/skipped disposition when opt-in inputs
   are supplied. Parameters, private scope names and user cron values stay redacted;
   show the logical setting's origin and pool, not arbitrary environment contents.
   A complete, valid, forwarded pair excluded by `ONLY`/`SKIP` is dormant: report
   it as skipped. If only the existing two supported jobs are selected, all existing
   gates may still pass; this does not qualify or enable the excluded refresh job.
   Half-pairs and forwarding errors still refuse even when refresh is filtered out.
5. Preserve unsupported-execution diagnostics, the installer's exact two-job
   inventory, all paused lifecycle guards, existing retained state and ordinary
   registrar behavior. No change to weather flow logic, first-run windows, model
   conventions, database schemas or provider access.

Tests belong in `tests/unit/config/test_deployment_profile.py`,
`tests/unit/tools/test_deployment_profile.py` and existing Compose/registrar tests
where the forwarding contract is owned. Verify actual render -> registrar spec,
not just a YAML substring. Cover neither/both/one opt-in, invalid cron, scope drift,
both `ONLY` and `SKIP` filtering (including the dormant-pair case), optional-job
provenance/redaction, unknown input rejection and
the unchanged two-job pass. An operational profile must still report uncovered
execution/provider checks and be refused by installation preparation. Run affected
lint/types/tests, then the full final code-change suite in CI. One independent
patch review precedes execution of the changed tool on a real deployment recipe.

## N1b: one durable operational entry point

Use the normal Compose route for this assisted comparison. It is separate from
`deployment_install.py`: do not manufacture a minimal profile, install it, then
mutate its frozen records/configuration into an unsupported operational stack.
Keep a fresh local database and fresh Prefect storage, local-only API access,
preloaded admitted images and existing service privilege/credential boundaries.

The private runbook must supply an exact ordered Compose list and environment
file, from a clean source matching the chosen image. Remove superseded overlays
only after comparing the effective service/configuration contracts with N0.
Before any execution, review those concrete bytes and a secret-safe semantic diff.
Do not copy the historical overlay chain and treat its length as completeness.

Record all seven baseline jobs with required/manual-only/excluded disposition.
The normal registrar handles the six known jobs; the additional local forecast
variant needs an explicit, reviewed supplemental registration step using the same
flow and supported Prefect API. Keep that deployment-specific step private for
this comparison; do not add an agency catalogue, arbitrary-flow registry or second
public job schema. The runbook gives one maintained registration invocation that
includes both normal and supplemental steps; leaving a hidden post-install patch
is not acceptance.

Specify fresh-registration parameters, not merely preservation of parameters from
an old Prefect database. Preserve standard versus provisional data use, artifact
import/manual history behavior, concurrency and the held backup disposition. On a
repeated invocation, read back and refuse unexpected parameters/variables rather
than resetting unrelated settings. A partial registration failure leaves pools
paused and workers stopped; retry must check the existing target inventory first.

Create/verify paused pools before registration. Keep workers stopped while any
scheduled deployments might be active; pause all registered deployments/schedules
and inspect pending runs before starting workers. Registration itself is not
activation. Discard only target-created pending runs under an explicit reviewed
step; never carry them across commissioning as unintended backfills.

For each job compare desired -> rendered init -> registered definition -> pool,
queue and actual child process. Use the existing NHS forecast configuration checker
for its exact supported boundary; separately verify the local standard-data variant
and generic worker configuration. Stored environment alone is insufficient because
Prefect's worker environment can override job keys. Use synthetic files and real
Prefect for that proof, followed by UID/readability and parsed-config checks on the
target. No upstream forecast/configuration semantics change to make a recipe pass.

## N1c: reconstruct inputs without cloning a running service

Use existing station creation, group creation, basin import, observation loading,
trusted artifact import and assignment mechanisms where they preserve the needed
contract. Imports are not all lossless: the general observation loader creates RAW
observations and QC results; model import records an external import and promotes
the supplied artifact. Neither automatically reproduces source lineage/identity.
Record these differences before claiming equal inputs or parity; never relabel a
training/import provenance or promote a new scientific choice for this test.

The private manifest must specify each source, target, fixed station/time window,
hash/count, units/QC/data-use/provenance, required foreign keys and reader/write
authority. Sequence: fresh schema/roles and tenant -> stations and groups -> basin,
static and weather bindings -> required observations/ratings/weather -> trusted
artifact metadata/bytes and assignments -> read-only readiness/coverage checks.
Any operator role uses the existing bootstrap/grant path, never the API role or a
blanket grant. Build fresh tokens separately; keep publication and notifications off.

Where imports cannot preserve the comparison contract, prepare a bounded,
input-only export/restore specification against the exact schema before copying:
explicit tables/columns, predicates, lineage and FK closure, ID remapping,
sequence handling, triggers/role constraints, consistent snapshot/cutoff and byte
limits. Review a synthetic restore with that closure. A list of table names or a
full dump with a few deletions is insufficient. This plan does not approve any
such table copy yet. No source Prefect/auth/publication/claim/result state is part
of reconstruction; source forecast evidence remains separate comparison evidence.

Start with the deterministic climatology pilot; expand to all admitted baseline
stations and the full group-model cohort. That sequencing does not shrink the
final comparison objective. Freeze source evidence and comparison tolerances
before target results. If matching CMAL inputs cannot be reconstructed, report
observational results and the missing contract, with no numerical parity pass.

## N1d: operational responsibility and acceptance

The deployment orchestrator is the named temporary-target operator for job
enablement, monitoring, stopping and cleanup. The weather owner retains provider
policy/backfill scope; the source host remains authoritative and unchanged.
Resolve local provider login/project/region and server-creation permissions using
the owner's protected credential source. Availability of a login file is not a
successful authentication or an approved new provider destination.

Before provisioning, record the chosen Infomaniak host size, cost ceiling, timeout
and deletion inventory. Size from source disk/memory/model requirements plus image,
import, working-space and evidence headroom; no capacity claim from DB size alone.
Real data remains at the selected provider. Runtime DB/Prefect destinations must
be target-local; no source tokens, source database access or publication routes.
Use scoped provider access and a finite request budget that cannot starve source
ingestion. The owner-selected secret delivery interface stays file-based and
manager-neutral; no password-manager integration or SSO change is required.

After paused readback and input checks, enable only required jobs/schedules and
their pools/queues in a recorded order. The first rolling refresh window, provider
quota, existing source backfill coordination and weather completeness are explicit
preconditions. Observe actual due runs, including a full daily interval plus the
agreed execution allowance; no shortened cron or manual-run substitute. Confirm
stored coverage and authenticated API results, not merely successful Prefect runs.

Operational stop first pauses target schedules/pools and stops target workers;
verify active child processes have exited before stopping remaining services, DB
last. Preserve failed-attempt evidence. Recovery checks exact images/config/mounts,
DB health, unexpected pending runs and completeness before resuming; no automatic
init rerun or schedule reset. Never call the paused installer's verifier/start on
this independently commissioned route. On timeout/failure, perform the same
owned-resource cleanup as success and verify provider-side deletion of data disks,
server, temporary keys/rules and provider registrations; retain only agreed evidence.

## Documentation and review gates

N1a updates `docs/deployment/profiles.md` for the optional pair and safe preview
limits. N1b–d produce the concise administrator guide, linked from
`docs/deployment-quickstart.md`; refer to the maintained profile, secret bootstrap,
provider, station and artifact runbooks instead of duplicating them. The guide
separates supported commands from still-unqualified preparation. Implementation
history stays in plans, review reports outside Git, and private data outside public
examples. Do not edit the onboarding agent's walkthrough.

One independent Claude review completed on 22 November: no N1a blockers. Clarified
the dormant-pair outcome and explicit optional-job disposition under both filters.
A valid excluded pair does not expand execution coverage: the rendered two-job
inventory and all existing checks still govern acceptance. Keep new operational
drift checks and paused-pool creation in N1b, outside this repair. The review used
bounded code excerpts; the implementation must still verify the full parser,
filtering and real Compose boundary with the tests above. Full report stays local.

The orchestrator marks **N1a READY**. N1b–d execution remains DRAFT until concrete
packets are reviewed. Owner approval/merge and release admission remain separate
from orchestrator readiness. No implementation or deployment occurred in this pass.

```json
{"phases":[{"id":"N1a","status":"READY","depends_on":["N0"]},{"id":"N1b","status":"DRAFT","depends_on":["N1a"]},{"id":"N1c","status":"DRAFT","depends_on":["N1b"]},{"id":"N1d","status":"DRAFT","depends_on":["N1b","N1c"]}],"next_execution":"N2 only after N1a–d acceptance","preparation":"read-only/offline drafting allowed; no target mutations"}
```
