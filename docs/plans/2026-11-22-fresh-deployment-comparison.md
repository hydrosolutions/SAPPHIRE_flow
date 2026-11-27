---
status: READY (N0 only; N1–N4 and K0 remain DRAFT)
created: 2026-11-22
owner: deployment orchestrator
baseline: 5dd6232ec
---

# Fresh deployment comparison, then a second agency

**Scope update, 27 November:** the owner selected the
[small-deployment path](2026-11-27-small-deployment-validation.md): latest published
release by default, one station and a simple model, with separate ingestion
scheduling checks. Full historical reconstruction and current-model parity below
are deferred. Their earlier prerequisites do not block the smaller test; recorded
evidence and limitations remain unchanged.

## Outcome and decisions

Reconstruct the existing Nepal MVP on a **separate temporary Infomaniak server**,
verify its weather scheduling and compare its forecasts with the existing server.
The owner selected this host arrangement on 22 November. Keep the existing server
running unchanged. A later Kazakh deployment reuses the generic procedure after
the Nepal comparison; it does not inherit Nepal's scientific or provider choices.
This pass is planning and read-only investigation, not host provisioning, data
transfer, job activation, a release, or a cutover.

Actual agency profiles, schedules, provider mappings, data/artifacts, credentials
and comparison evidence stay outside this public repository. Public instructions
and examples remain generic. Preserve the [hosting decision and its limits](../decisions/2026-09-30-dhm-data-hosting-assumption.md);
the temporary target is private, with no public publication or dashboard change.
Do not introduce a backup-commissioning prerequisite contrary to the owner's
recorded deferral, or describe this comparison as disaster-recovery qualification.

## What exists and what does not

- External profiles, safe provenance, offline checking and synthetic installation
  qualification exist. [The installer](../deployment/profile-installation.md)
  currently accepts the stored-weather subset with exactly `forecast-cycle` and
  `backup-database`, both paused. It does **not** commission operational weather
  ingestion, adopt a populated database, or verify an activated installation.
- The normal registrar and existing operational runbooks support more jobs and
  provider wiring. Full operational-profile coverage is still missing from the
  generic checker/installer. Do not remove real requirements or loosen guards to
  obtain `PASS_OFFLINE` / `VERIFIED_PAUSED`.
- [Recurring weather Phase A](2026-11-21-nepal-recurring-weather.md) is implemented
  in merged #599: `refresh-operational-era5-history` requires both
  `SCHEDULE_REFRESH_OPERATIONAL_ERA5` and `SAPPHIRE_ERA5_WEATHER_SCOPE` at registration.
  The old `ingest-operational-era5-history` remains an unscheduled fixed-window
  import. The unrelated generic `ingest-weather-history` is not a substitute.
- Read-only live inventory on 22 November found the recurring refresh registered,
  an active UTC schedule and a supplied weather scope. Future automatically
  scheduled runs exist. The bounded run inventory contained no completed refresh;
  successful execution and database coverage are therefore **not established** by
  this investigation. One manual run was scheduled but had not started in that
  readback; record its purpose/state with the weather owner, never count it as
  scheduler or coverage proof. This is a dated observation, not a persistent health claim.
- The live application image source was `ebd29075970fe2e6d6c9acd41519c39634f26e13`.
  Git ancestry confirms this source includes #599; image-label readback alone
  does not replace release/delivery proof. Previously qualified release 0.1.1170
  predates the recurring refresh. Do not use
  its synthetic acceptance as evidence for the newer operational recipe.
- Forecast weather acquisition, retained fallback and exact-cycle arrival polling
  are distinct from ERA5 history refresh. Phase B of the recurring-weather plan
  remains DRAFT. Preserve the existing forecast policy for the first comparison;
  do not implement another polling mechanism here.

## Sequence and ownership

One implementer owns each pass. Independent review uses one Claude reviewer per
nontrivial plan/patch under repository policy. The deployment orchestrator owns
this comparison; the weather owner retains refresh/polling, the onboarding owner
retains station/model tools, and the time-grid owner retains scientific timing.
Record handoff source/image identities and pending work before each dispatch.
Do not edit the station-onboarding walkthrough or change live assignments/schedules.

| Milestone | Work and concrete paths | Acceptance / meaningful tests | Dependencies |
| --- | --- | --- | --- |
| N0 — freeze the private baseline | Private evidence outside Git; read the existing complete Compose chain, application/job overlays and registrar inputs; this plan records only outcomes. Reuse `build_deployment_specs` and the NHS configuration verifier. | Minimum packet: release/image/schema identities and deployable-image proof availability; disposition of every live job; unsupported-input list; selected cohort, input identities, windows and predetermined tolerances. If inputs cannot be frozen, label observational, not parity. Use bounded coverage/sizing summaries; detailed reconstruction inventory follows only where N1 needs it. Record access/permissions gaps. No source writes, provider calls or first refresh/backfill. | Owner's separate Infomaniak decision; independent review of this planning slice |
| N1 — settle the smallest operational installation route | Reuse `tools/deployment_profile.py`, `tools/deployment_install.py`, `tools/deployment_install_runtime.py`, `tools/deployment_install_state.py`, `src/sapphire_flow/cli/deployment_checks.py`, `cli/register_deployments.py` and `tools/forecast_config.py` only where N0 demonstrates a gap. Write a bounded implementation slice before code. | Exact supported jobs/parameters/schedules/provider mounts and import sequence specified; one reviewed route to fresh paused services and one separately defined commissioning step with a named operator for enabling jobs, stopping and recovery before N2. Confirm target capacity and permitted input handling. Preserve old two-job behavior and old retained-state compatibility. No arbitrary-job framework, second schema, vault SDK or generic SSH controller. | N0 evidence; material decisions resolved; independent implementation-plan review |
| N2 — fresh private installation and input reconstruction | Reviewed release/delivery path, existing station/observation/model import commands and the selected private input manifest. Administrator steps in proposed `docs/deployment/operational-comparison.md`, linked from the quick-start and configuration reference. | Fresh application and Prefect storage, fresh local credentials, source-independent paths and no unintended execution. Verify source/artifact checksums, relationships, tenant/write authority and actual in-container readers. Authenticated local API and rejected unauthenticated access. No inherited source tokens, queued jobs or notification destinations. | N1 implemented/reviewed with full final CI; exact approved image pair; target/data recipe reviewed |
| N3 — prove schedules and forecast parity | Private bounded commissioning/comparison record; existing registrar, Prefect readback, weather stores, forecast/API readers and readiness commands. | Expected jobs and runtime wiring match; real scheduled execution and complete weather support proved; bounded forecast comparisons have explicit outcomes and tolerances; source unchanged apart from its ordinary operation. No cutover. | N2; admitted provider access and agreed run/window limits |
| N4 — cleanup and reusable handoff | Concise outcome in this plan; generic administrator guide above; private cleanup evidence. | Temporary provider registrations/access, tokens, data, server, disks and network resources removed with readback. Retain only agreed protected evidence. Record remaining gaps without claiming readiness. | N3 completed or aborted; cleanup applies on failure too |
| K0 — second-agency intake | New private profile from generic examples and a separate bounded execution plan. | Sources/access, station/rating inputs, models/FI compatibility, QC policy, display zone, hydrological grid, training conventions and schedule expectations explicitly supplied. Re-run the same checks; Nepal settings are not defaults. | N4 findings incorporated; owner supplies the second agency's inputs |

**Readiness is phase-specific.** This document does not dispatch implementation,
provisioning or commissioning. N0 alone is READY after the review recorded below.
N1–N4 and K0 remain DRAFT until their concrete input/import/operational boundary is
settled; do not turn conditional descriptions into executable instructions.

### N0 completion and N1 preparation

Read-only N0 inventory completed on 22 November. Private evidence contains the
complete job/mount/configuration inventory, six verified active artifact hashes,
release metadata checks, bounded coverage/sizing and a comparison contract.
No source jobs, data or configuration were changed. The recorded registration
recipe reproduces only a manual import, rather than all seven stored jobs; a
durable provider-path input is also missing from that captured invocation.
These are reconstruction gaps, not proof that no separate operator procedure exists.

The owner subsequently supplied a protected local credential source containing
Infomaniak account/cloud-project details. Authentication and provisioning rights
remain unverified; no temporary server has been created.

The [N1 implementation slices](2026-11-22-operational-deployment-recipe.md) separate
the demonstrated optional-weather preview defects from preparation of a complete
operator-assisted recipe, input reconstruction and commissioning. The paused
installer's current scope remains unchanged. N1 as a whole and N2–N4 remain DRAFT;
only individually reviewed implementation slices may become READY.
Independent review of that bounded follow-up found no implementation blocker;
the orchestrator marked **N1a only READY**, with explicit dormant-input and
optional-job preview requirements. The complete operational recipe is not yet READY.

## Required-job matrix: prevent the original omission

Keep this matrix in the private deployment record, derived from the actual complete
recipe and registrar inputs rather than an agency catalogue in code. For each job
record: purpose, mandatory disposition (required, manual-only or excluded with a
reason), pool, source entrypoint,
UTC cron and display-zone equivalent, parameters, per-job configuration chain,
credential/mount readers, concurrency, activation state, freshness/completeness
criterion and recovery action. Include all live jobs, even locally registered
ones that the standard registrar does not reproduce (for example a separate
climatology deployment). The initial inventory has seven jobs; observation ingest,
climatology and artifact import each need their own explicit disposition. Do not silently omit those jobs or invent their defaults.

Trace durable host input -> Compose init environment -> registrar definition ->
stored deployment and schedule -> pool/queue/worker -> child-process configuration.
An environment value in a host file is insufficient if Compose never passes it.
Check `ONLY`/`SKIP` conflicts, omitted opt-ins, missing scopes, unsupported job
overlays and UTC versus display timezone. Generic typed defaults remain shared;
scientific timing and source declarations stay explicit.

Runtime acceptance checks BOTH schedule activity and deployment/pool/queue pauses,
worker heartbeat and actual pickup. A manually triggered success is not scheduler
acceptance. Observe at least one real due run of every required recurring ingest
and forecast path, including a daily refresh: allow a full daily interval plus the
agreed execution allowance. Keep the target cost/time bound in the private plan;
if no due run is observed, report the gate pending rather than shortening the cron
and claiming the intended schedule worked. Compare next expected UTC fire times
before and after re-registration/restart; preserve approved parameters and scopes.

After weather refresh, use bounded database readback for every admitted station
and parameter in the exact eligible window. Check gaps, units, hourly support,
source identity and relevant freshness, not just a latest timestamp, HTTP 200 or
Prefect `COMPLETED`. Missing/degraded results stay visible. The first rolling
refresh can backfill data: record its exact scope and provider request budget
before executing it; coordinate with the weather owner to avoid duplicate live
backfills. N0 records but never executes this scope. The known older gap ages out
of the rolling window after 3 December per the weather plan; that is a weather-owner
recovery concern, not authority to run a backfill here. Exact-cycle polling remains its separate dependency.

## Fresh reconstruction and comparison boundaries

N0 reads must not display credential values, mounted-file contents or provider
keys. Its credential/mount columns contain logical names and reader identities,
never values. Keep any private path/identity evidence in protected local records.

N0 must identify a reproducible, minimum **input** set using existing supported
imports where possible: station/group metadata, source bindings, observations with
QC/provenance, ratings where actually used, required weather, model assignments,
trusted artifact bytes and declared model/time contracts. Preserve identifiers,
relations and lineage. The owner's statement that the data is available guides
this inventory; imported coverage and model compatibility still need readback.

Do not clone the live Prefect database, queues, workers, schedules or operational
identity. Do not blindly restore application authentication/publication state,
execution claims or cached outputs and then mistake them for fresh test results.
A full application snapshot is not an approved shortcut. If existing input imports
cannot express the required set, N1 must review the exact export/restore table set,
foreign-key/provenance closure, schema/role bootstrap ordering, secret separation
and capacity before any copy. No automatic migration/adoption or direct state-file
editing to get around the installer's fresh-storage contract.

Start with the exact approved deployed image/artifact/configuration semantics;
record any unavoidable version differences before comparison. Separate installation
parity from intentionally added scheduling fixes. First use a bounded common cohort
and frozen input/issue windows, then the complete eligible baseline cohort. Compare
station/model eligibility, omissions and reason codes, source/cycle and fallback
evidence, physical forecast periods, horizon, units, quantiles/finite values and
authenticated API results. Distinguish newly generated target results from copied
input records. Never rerun or write on the existing server merely to manufacture a
matching baseline; retain its existing evidence at a stated cutoff.

Set deterministic equality or model-appropriate numerical/stochastic tolerances in
N0 before viewing target results. If shared inputs are not available, label the
comparison observational and explain the difference; it cannot pass reproducible
parity. Pin the current FI/Aquacast and artifact training conventions. Display time,
hydrological boundary, model issue and NWP initialization remain distinct. Reuse
[time milestones](2026-11-17-hydrological-time-mvp-milestones.md) and
[model-onboarding work](2026-11-18-nepal-model-onboarding.md); do not bundle a new grid,
retraining, model promotion or scientific qualification into deployment testing.

The paused installer's no-execution contract ends at commissioning. N1 must state
which existing operational checks replace it and who owns stop/recovery after jobs
run. Do not advertise `verify`/`start` as operational lifecycle support or edit its
record to suppress refusal. The existing server remains the service of record;
there is no DNS/dashboard switch, public endpoint, notification delivery or copy of
test results back to it. Keep target runtime database/Prefect connections local to the target; verify it
cannot connect to the source database or Prefect API. Separate or appropriately
scoped provider credentials allow only needed reads. Account for shared provider
quota so target polling cannot starve the live deployment. If N0/N1 stops before
provisioning, only the agreed private-evidence retention/deletion remains.

## Verification and documentation

For a demonstrated code gap, add behavioral tests at its existing boundary:
`tests/unit/tools/test_deployment_profile.py`, installation runtime/state suites,
`tests/unit/cli/test_deployment_install.py`, `tests/unit/cli/test_register_deployments.py`
and the existing dedicated-daemon integration harness as applicable. Cover a missing
required schedule, inactive schedule or paused pool/queue, wrong scope/timezone,
missing worker/provider mount, parameter drift on repeated registration, incomplete
weather support, input-only reconstruction and isolation from the source. Keep
synthetic fixtures public and agency/provider data private. Full final code-change
CI and one independent patch review are required before execution of changed code.

The generic administrator guide should explain preparation, schedule selection,
preview limitations, installation, commissioning, expected evidence and cleanup;
link `profiles.md`, `secrets-bootstrap.md`, provider and onboarding runbooks rather
than duplicate their configuration catalogues. Implementation/acceptance history
stays here. No server secret-manager automation or agency SSO prerequisite is added.

```json
{"phases":[{"id":"N0","status":"READY","depends_on":[]},{"id":"N1","status":"DRAFT","depends_on":["N0"]},{"id":"N2","status":"DRAFT","depends_on":["N1"]},{"id":"N3","status":"DRAFT","depends_on":["N2"]},{"id":"N4","status":"DRAFT","depends_on":["N3"]},{"id":"K0","status":"DRAFT","depends_on":["N4"]}],"failure_cleanup":"N2 or N3 failure still requires N4"}
```

## Independent review

One independent Claude review completed on 22 November. Incorporated the bounded
N0 evidence packet, explicit disposition for all live jobs, private-reader limits,
manual-run/first-refresh uncertainty, image-proof dependency, commissioning/recovery
ownership, provider quota/source isolation and cleanup after early abort.

Two proposed factual changes were not adopted: absence in a bounded run readback
does not prove a refresh has never completed; and Git ancestry confirms deployed
source `ebd290759` includes #599. Neither changes the need for actual coverage and
release proof. The orchestrator marks **N0 only READY**; every later phase remains
DRAFT. No unresolved blocker to read-only N0 work. Full reports stay local; owner
approval/merge and later implementation reviews remain separate.
