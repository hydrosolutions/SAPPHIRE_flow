---
status: READY
created: 2026-11-19
owner: deployment orchestrator
baseline: 0815e9a2d
---

# Complete offline configuration validation

## Outcome and decisions

One administrator-owned profile and installation file can earn `PASS_OFFLINE`
through the existing `tools/deployment_profile.py ... check` command. The owner
confirmed on 2026-11-19 that this means **configuration consistency only**.
It does not certify credentials, provider access, installed images, database
station/model state, scientific readiness, or permission to launch. Those remain
explicit `unverified_runtime` items even on success. No new status, framework,
launcher, admin UI, provider integration or secret-manager client is needed.

The generic preparation feature and deployment smoke command already exist.
The smoke milestone completed on a disposable Linux target with release 0.1.1154;
see [its history](deployment-hello-world.md#current-status--2026-11-19).
A smoke pass never clears profile diagnostics.

Real profiles, station/model choices and secrets remain outside this public
repository. Public material is generic; complete fixtures are labelled synthetic
test inputs, never agency defaults. Four neutral defaults remain Gregorian
calendar and disabled forecast/observation/pipeline alerts. Display timezone is
explicit, normally local, and is independent of hydrological grid and model
training conventions. Preserve the existing time-grid activation restrictions.

## First supported path

Qualify the existing normal Compose topology with one application config chain
shared by init, API and workers, including backup. Permit a private overlay to
bind that chain and explicit nonsecret settings without changing the reviewed
service commands, entrypoints, dependencies or credential roles. There is no
repository-owned agency profile and no special pass for a fixture name/path.

The first positive case uses stored-data forecasting:

- `operational_weather.route = "stored_reanalysis"` and an explicitly disabled
  forecast-weather adapter. No automatic external observation/history ingest is
  selected. Station observations/weather and model artifacts are provisioned by
  separate existing onboarding workflows; validation never asserts they exist.
- Register only `forecast-cycle` and `backup-database`, using the registrar's
  existing explicit subset mechanism. Preserve its only/skip mutual exclusion.
  The private Compose overlay forwards `SAPPHIRE_ONLY_DEPLOYMENTS` into init;
  the checker compares that rendered value with the installation declaration.
  Missing forwarding cannot pass (it would register every default job).
  An idle ingest worker is permitted; do not remove services from legacy recipes.
- No active exceptional operator/provider scopes, external unit registry,
  provisional-rating delivery, optional roster/overview subsystem or referenced
  station/rating/provider file. Those extensions remain pending until covered.
  This is a support boundary, not a flag to ignore declared inputs.
- Current typed root, identity, tenant, onboarding, QC, forecast QC, API and path
  configuration is validated wherever the supported consumers parse it, including
  inactive settings that runtime parsers still require. All required scientific
  choices remain explicit; never fill them from a real agency config.

Backup is a wiring-only consumer: its flow reads backup environment settings,
not the application TOML. Validate its existing config mount for consistency,
but do not claim application-field validation through the backup flow.

This is a useful initial installation configuration, not a promise that a model
can forecast without its required station/weather/artifact data. Enabling another
job, adapter or scope must introduce the corresponding coverage obligation. It
cannot inherit a pass from this smaller path. Existing broader deployments retain
their runtime behavior and honest pending diagnostics.

## Existing evidence and proposed work

| Current evidence / gap | Proposed clearing condition |
| --- | --- |
| Shared overlay merge, provenance, typed config and raw tenant readers already used | Reuse them; preserve raw tenant non-interpolation and file-relative ordering. No second merge engine. |
| `ADAPTER_POLICY_PENDING`; forecast adapter parsing lives in `flows/run_forecast_cycle.py::_load_weather_forecast_adapter_config` | Extract only its pure mapping/environment parsing boundary for runtime and preview to share. Keep environment/file loading, key lookup, scratch directories and client construction outside it. Exact parity includes disabled-adapter validation. Enabled/unsupported adapters stay pending. |
| `UNCHECKED_SECTIONS` and non-neutral default review | Inventory fields actually consumed by the supported jobs, init and API. Validate each through its authoritative reader or report pending. Audit inherited active defaults by field name; values still come from their typed reader. Unknown nested sections/keys cannot silently earn coverage. |
| Unconditional `AGENCY_EVIDENCE_PENDING` | Separate local configuration coverage from explicitly unverified database/scientific readiness. Do not clear malformed/missing local inputs. DB-owned station/model state belongs to runtime evidence; locally declared references retain their own coverage obligation. |
| `SCOPED_INPUTS_PENDING`, `ROSTER_PENDING`, registry/provider checks | Clear only by demonstrated absence/non-applicability in both config and rendered consumers. Declared references remain pending in this increment. Never import the reference-fixture recorder as a generic station parser. |
| `check_wiring` checks config binds, secrets, schedules and pools, but not complete execution semantics | Add finite checks for this standard topology, effective images, entrypoints, commands, relevant environment, mounts and init sequence; include backup. A manifest listing an arbitrary Compose file does not make it trusted. Unsupported execution changes remain pending; contradictions fail. |
| `SAPPHIRE_ONLY_DEPLOYMENTS` exists in the registrar but is absent from profile environment admission/forwarding checks | Admit and validate it with explicit forwarding into rendered init. Reuse `build_deployment_specs`; include fixed job parameters and job-level config overrides when deriving each job's effective inputs. Do not patch the base Compose defaults to select this subset. |
| `RUNBOOK_PENDING` currently checks file existence | Offline success checks a supplied nonempty readable local handoff reference, not human review/operational approval. Label that scope accurately. Use the maintained generic guide in the synthetic example. |
| Secret file/BuildKit wiring checks already exist | Keep content-blind role/consumer/path checks and missing-binding failures. Managers may materialize files without changing this contract. Never read a secret, provider token, env file or manager locator. |
| `PASS_OFFLINE` exists but has no real command-level success fixture | Add a complete generic external fixture using ordinary evaluation and real Compose rendering. No fabricated `ProfileEvaluation`, blanket pending removal, exemption or success override in the acceptance test. |

The full consumer checklist must include init's migration/tenant/role/registrar
commands, API configuration, forecast and backup jobs, their workers and any
operator command claimed by the supplied handoff. An unsupported command is
reported as outside checked coverage, never implied supported. No DB migrations,
model/FI changes, scientific algorithm changes or station-onboarding edits are
part of this work. A new discovery that materially expands the path returns to
planning; do not widen implementation silently.

### Frozen consumer checklist / V1 entry gate

| Consumer | Authoritative boundary and obligations for this path |
| --- | --- |
| Init | Existing `docker-compose.yml` command sequence (migrate, tenants, role bootstrap, registrar); `config/deployment_identity.py`, raw tenant reader and `cli/provision_tenants.py`; existing DB role/secret paths from `docker/bootstrap-roles.sh`. Check declarations/wiring only, never execute them. |
| Forecast worker/job | `config/deployment.py`, `config/operational_weather.py`, QC readers and `flows/run_forecast_cycle.py` configuration loaders. Cover every supported raw key, its reader default and effective environment. `SAPPHIRE_REQUIRE_NWP` is supplied explicitly to pure parsing; no ambient read. Keep the existing config-unset loader branch and its defaults unchanged, outside the shared mapping parser. The supported profile always supplies a config. |
| Job registration | `DeploymentSpec.flow_module`, `flow_attr`, `deployment_name`, `cron`, `concurrency_limit`, `work_pool_name`, `forecast_config_overlay`; source root `/app`. Forecast: `sapphire_flow.flows.run_forecast_cycle:run_forecast_cycle_flow`, pool `default`, concurrency 1. Backup: `sapphire_flow.flows.backup:backup_database_flow`, pool `backup`, concurrency unset. Reuse specs for cron defaults. Require no per-job overlay; `SAPPHIRE_FORECAST_CONFIG_OVERLAY` or `NHS_FORECAST_JOB_OVERLAYS` selects unsupported coverage, never silently passes. |
| Existing registered job state | Registrar preserves existing parameters for both selected jobs and forecast job variables. Those are **not** fixed empty parameters and are unavailable offline. Always report `registered_job_overrides` as unverified; preview checks desired fresh-registration specs only. Never claim to validate deployed job overrides. |
| Backup worker | `flows/backup.py`, `docker/entrypoint.sh`, `tests/unit/deploy/test_compose_backup_pool.py`: image, exact command/pool, `DB_PASSWORD_SECRET`, `SAPPHIRE_BACKUP_PGHOST/PGPORT/PGUSER/PGDATABASE`, `SAPPHIRE_BACKUP_DB_PASSWORD_FILE`, optional positive dump timeout, backup secret role, writable `/data/backups` mount and existing config mount. Do not call `build_pg_child_env` (it reads a secret). |
| API | `api/__init__.py`, `api/deps.py`, `api/cors.py`, deployment/identity readers: shared config, role-scoped database template/secret, explicit allowed origins, Prefect address and public route declarations. Human-auth and optional overview integrations remain pending if selected. No API import that starts clients or reads credentials. |
| Infrastructure/idle ingest worker | Standard Compose Postgres, Prefect, Caddy and worker settings: images, network/dependency wiring, reviewed mounts/commands, and existing credential roles. The idle ingest service still needs consistent config and credentials; its unselected provider jobs are not claimed checked. Any additional execution surface remains pending. |

The field inventory includes all `_PREVIEW_FIELDS`, not just the ones needed by
the positive fixture. Each is explicit, one of the four neutral typed defaults,
or demonstrated inactive for the supported route. An inactive inherited field
must be labelled as such in provenance, never as an endorsed neutral default.
Keep pending for any unaudited applicable field; no broad new defaults allowlist.
This default-review rule applies to the existing preview fields. Covering other
typed fields means reusing their authoritative validation, not expanding the
administrator checklist to every internal default. Add a new explicit choice
only for a demonstrated uncovered deployment decision; do not copy the entire
application config into the example to manufacture coverage.
The external registry is inapplicable only if its selection is absent from both
config and every rendered service/job environment. Check the runbook reference
in `check_wiring` with bounded local read/size handling and no content execution;
never emit its contents.

Permanent runtime-unverified entries include actual host configuration, credential
availability, provider access, station/model database state, scientific readiness,
registered job overrides and actual image identity. Input references, unsupported
sections or consumer drift cannot be moved into that list to manufacture a pass.

## Worker milestones

One implementer owns each pass, sequentially. The orchestrator owns planning and
review dispatch. Every milestone includes its affected documentation and focused
behavior tests. No implementation agent executes this plan while it is DRAFT.

| Milestone | Concrete paths and work | Acceptance / meaningful tests | Dependency |
| --- | --- | --- | --- |
| V0 — close smoke and settle contract | This plan; `docs/plans/deployment-hello-world.md`; `docs/deployment/{hello-world,profiles}.md`; `docs/deployment-quickstart.md`; reconcile external-file ownership in draft `docs/plans/2026-11-11-one-command-deploy.md` | Public smoke summary contains no host/private records; stale follow-up claims fixed; independent Claude review completed, material findings resolved, orchestrator sets READY. | Owner's boundary decision; completed HW2 evidence |
| V1 — pure reader and default coverage | `src/sapphire_flow/config/deployment_profile.py`; small pure parser in `src/sapphire_flow/config/weather_forecast.py` if extraction is needed; delegation from `src/sapphire_flow/flows/run_forecast_cycle.py`; corresponding `tests/unit/config/` and `tests/unit/flows/` tests | Confirm the frozen checklist above before edits; reuse real readers. Valid/invalid/disabled forecast parsing matches runtime, including malformed type with enabled=false and explicit require-NWP input. Config-unset runtime behavior remains unchanged. No filesystem writes, clients, environment reads or credential loading in pure evaluation. Supported defaults have actual reader provenance; unsupported choices remain pending. | V0 |
| V2 — complete bounded wiring | `tools/deployment_profile.py`; environment boundary in `config/deployment_profile.py`; `tests/unit/tools/test_deployment_profile.py` | Full supported topology and each selected job use the same intended config. Missing/reordered mount, backup drift, wrong image/command/entrypoint, unforwarded subset/schedule, conflicting only/skip, unexpected service/job/environment and unknown extension cannot pass. No registrar or Compose runtime behavior change. | V1 |
| V3 — reachable command-level pass and administrator walkthrough | `tests/fixtures/deployment_profile/` (synthetic only); config/tool tests; `docs/deployment-quickstart.md`, `docs/deployment/profiles.md`; touchpoint/reference links only where affected | Copy complete fixture outside checkout; actual CLI + real Compose render exits 0 and reports no offline gaps. Mutating each material obligation produces actionable FAIL/PENDING. Incomplete starter still fails appropriately. Runtime caveats visible in text/JSON on success. Guide explains scope, checking and next handoff without claiming launch approval. | V2 |
| V4 — review and delivery | Independent patch review; local report only; PR with concise evidence | One independent Claude patch review; resolve concrete findings without automatic review loops. Ruff, relevant types and full final-code suite locally or CI. Current release-target policy once per code PR; owner approves/merges. | V3 |

V2 reuses `api/cors.py::parse_exact_origin` through a minimal move to
`config/cors.py`, retaining its API-module export and unchanged behavior. This
avoids importing the API package during offline checking; no middleware/auth
policy changes are included. Add it to config-reader coverage and test the
no-API-import boundary. Compare execution fields against a separately rendered
standard Compose recipe instead of copying its defaults into another table.

V1 may leave the overall result pending while V2 is incomplete. Only V3 provides
the administrator-reachable success claim. Keep the old pending behavior until
the matching evidence exists; never expose a partial success between milestones.

## Verification and preservation

- Unit parity tests exercise the shared parser rather than a duplicate validation
  schema. Cover booleans versus integers, invalid/unknown nested values and both
  valid and invalid disabled-adapter settings that runtime parses.
- Existing reader/registrar/legacy Compose regression tests continue to pass.
  Selecting jobs does not deregister previously installed jobs; document this.
- Real CLI tests run outside the checkout with explicit config/installation,
  fake secret file references and no provider credentials. Compare exit/status,
  safe provenance, selected job inputs and human-readable remediation. A missing
  Docker daemon must not prevent offline rendering; unavailable Compose is pending.
- Canary tests cover secret files, `.env`, service env files, ambient selectors,
  sensitive URLs, arbitrary private names/paths and malformed input exceptions.
  Assert no secret-content read, network/client access, directories created by
  application path helpers, database access or model artifact deserialization.
- Test non-applicability in both directions: absent disabled feature can clear
  its obligation; selecting it through config, service env, a job override or an
  extra service restores the obligation. Runtime-unverified evidence is never
  silently dropped or promoted into offline proof.
- Keep docs ownership: quick-start is the short journey, profile reference owns
  validation details, config reference owns field definitions, this plan owns
  implementation history. No duplicated scientific defaults or provider recipes.

## Optional clean-host qualification

The owner authorized a temporary Infomaniak server, completely removed afterward,
with AWS as fallback. Offline consistency normally needs only real Compose
rendering locally/CI; do not provision a server merely to validate pure code.
Use the authorization only if V3 reveals a concrete clean-host qualification need.

Before creating anything, select one compatible small Linux host from current
provider availability/pricing and available project credentials; use a bounded
test duration. Record provider/project/region and every newly created resource
ID outside the host. Reuse existing image identity/delivery safeguards; no public
application image upload, agency data or production secrets. Install uv in the
disposable workspace and preserve tracked source permissions. No existing staging
host is part of this new qualification.

After success, failure or timeout remove the owned server and any created disks,
snapshots, floating addresses, keys/security groups or other billable resources;
retain only sanitized local evidence. Verify absence via the provider API, not
merely a stopped server or a successful delete request. Reconcile uncertain
creation before retry/fallback; clean Infomaniak resources before trying AWS.
Report any incomplete deletion immediately and continue recovery. Never delete
shared pre-existing resources or remove cloud resources by broad name matching.

## Dependencies and review

Baseline is remote main `0815e9a2d`; refresh before each implementation branch.
Coordinate ownership with station/model onboarding; do not edit their guide,
fixtures, migrations or CLI contracts. The existing one-command deploy draft
remains DRAFT and separate. Launcher implementation and legacy migration follow
this offline contract; secret-manager selection/integration remains separate.

One independent Claude plan review completed on 2026-11-19. It found the route
real/finite and the offline boundary honest, with four required clarifications:
backup's wiring-only role, the private subset-forwarding overlay, disabled-parser
and config-unset behavior, and frozen registrar fields/overrides. The revisions
above address all four, plus explicit runtime state and bounded runbook evidence.
Inspection also confirmed preserved server-side job parameters: they are now
explicitly unverified. No material owner decision remains. The orchestrator sets
READY for these sequential milestones; launcher and live-stack changes remain
outside this plan. Full reports stay local; concise outcomes belong in the PR.
The same review's omitted documentation portion was completed separately with
public repository material and the sanitized smoke summary; it found no blockers.

### V1 checkpoint — 2026-11-19

Shared forecast parsing and runtime delegation are implemented. The config-unset
runtime branch remains; disabled adapters retain their existing validation.
Preview uses explicit environment inputs, limited inactive-field evidence,
authoritative typed readers and finite QC rule-key coverage. Unknown extensions
remain pending. Database/scientific state, registered overrides and actual image
identity are explicitly unverified. No station or model implementation changed.

`EXECUTION_COVERAGE_PENDING` prevents the older partial wiring checker from
exposing a premature pass; V2 must clear it with complete evidence.
`CONFIG_EXTENSION_PENDING` survives independently of profile-file references,
so an absent reference list cannot waive a configured integration.

Focused validation: 586 tests passed, Ruff/format passed and changed-module
Pyright reported zero errors. The broader forecast test file attempted to start
an ephemeral Prefect server and hit the sandbox port restriction; it was stopped.
The full final-code suite remains a delivery gate. V2 is dispatched; independent
patch review and V3's real command-level pass are still outstanding.

### V2/V3 checkpoint — 2026-11-19

The checker now compares a real selected Compose render with a separate standard
recipe render. It checks backup roles/storage, all application config chains,
service execution/environment, exact forecast/backup subset and public API
configuration. Shared-volume substitutions must retain one storage identity
across consumers. External profiles are supported; a different source checkout
is explicitly pending rather than being treated as equivalent.

The complete synthetic fixture lives in `tests/fixtures/deployment_profile/` with
a standalone external-copy walkthrough. Actual CLI subprocesses, without mocked
evaluation or wiring, demonstrated PASS_OFFLINE and the expected FAIL/PENDING
mutations. Runtime caveats and safe JSON remain. No cloud server was needed or
created: the acceptance path uses real Compose configuration rendering without
starting services. Final validation and independent patch review remain gates.

### Independent patch review and corrections — 2026-11-19

One independent Claude patch review identified a false pass when an installation
declared an optional provider/delivery setting that no selected consumer used.
Actual CLI reproduction confirmed it; all three nonempty declarations now retain
pending coverage. The review's nested-mapping concern led to a second reproduced
false pass for an unknown key in a dataclass-backed skill threshold. Traversal
now uses existing dictionary/dataclass field metadata. Already-strict weather
model validation is preserved. No model-onboarding types were changed.

Runbook `.env` references and their aliases are rejected before reading. The
optional import-weight refactor was deferred: the documented full project
environment remains required. No automatic review loop or additional reviewer
was used; full review reports remain local.

After corrections: 804 focused tests passed on Compose 5.6.0; all 116 tool tests,
including actual external CLI acceptance, passed on Compose 2.38.2. Existing API
CORS tests, focused Pyright, Ruff, formatting and documentation links passed.
Full final-code CI remains the last delivery gate and is recorded in the PR.

### Completion — 2026-11-19

PR #565 merged as `813313697fcad1cd150d0bcbb01a38376e1923cb` after full
[final-code CI](https://github.com/hydrosolutions/SAPPHIRE_flow/actions/runs/44487698094)
passed, including both integration suites and deployment smoke qualification.
V0–V4 are complete. No cloud server was created and no live deployment changed.
The next proposed increment is the separately reviewed
[profile launch rehearsal](2026-11-19-profile-launch-rehearsal.md); offline success
retains its configuration-consistency-only meaning.

```json
{"phases":[{"id":"V0","depends_on":[]},{"id":"V1","depends_on":["V0"]},{"id":"V2","depends_on":["V1"]},{"id":"V3","depends_on":["V2"]},{"id":"V4","depends_on":["V3"]}]}
```
