---
status: READY
created: 2026-11-17
title: Named agency profiles — validate and preview first
---

# Named agency profiles — validate and preview first

## Settled scope

**Owner decision, 2026-11-17:** deliver named profiles and safe validation/preview
first, preserving existing Swiss/Nepal files. Profile-driven launching and migration
follow separately. Administrators select an agency rather than assemble file lists.
Reuse TOML, existing merge/loaders, Compose and registrar specification building.
No new framework, UI, deployment engine, generalized importer or scientific behavior.

**Owner vision decision:** MVP deployment is a joint effort between the hydromet
team and a supporting developer, designed toward independent administrator setup
once agency scientists supply the required inputs. Make the handoff explicit:
agency specialists supply scientific/source choices; the administrator supplies
host and access details; the supporting developer resolves unsupported integrations
and helps assemble/check the initial profile. Do not assume the administrator can
diagnose Python or Compose internals. This direction does not add a wizard or
launcher to the preview-first increment.

**Owner success criterion:** the first deployment setup milestone is a disposable
hello-world forecast visible through the API. A forecast for one real agency
station follows through the station-onboarding sandbox. Keep these acceptance
milestones separate so platform wiring can be verified before agency-data issues.
T1–T3 deliver the offline preparation increment, not the full setup milestone;
the separately planned runtime smoke test completes that milestone.

**Owner execution decision:** support the same smoke scenario on a developer's
machine and the intended deployment server. Require a successful isolated run on
the target server before real-station onboarding; a local pass alone is insufficient.
Use the same versioned scenario and expected results, with host-specific installation
inputs. Server execution remains an explicitly authorized later operation, not an
action permitted by this planning session.

**Owner cleanup decision:** after a successful smoke run, remove only its disposable
resources by default and retain a sanitized result report. Offer an explicit option
to keep the demo temporarily for troubleshooting, with its identity and cleanup
instructions in the report. Real-station onboarding uses its own setup; never
promote the demo database, credentials or volumes into an agency deployment.

**Owner demo-input decision:** hello-world may run before real agency sources,
stations, models or secrets are available. Use a separate synthetic test scenario;
its success neither completes nor validates the agency profile. Missing agency
choices remain explicit blockers, with no inherited Swiss defaults. Image/build
preparation prerequisites still apply independently of agency credentials.

**Owner maintenance decision:** after initial setup, the hydromet administrator
owns the installation files; agency scientists remain responsible for scientific
choices, and developers support software/integration changes. Public examples live
in this repository; private agency configuration remains outside it. The setup
handoff identifies these responsibilities and the private files to maintain.

**Owner administrator-skill assumption:** administrators can use a terminal, edit
a documented configuration file and manage files on a server. They need not know
Python, Compose overlay ordering or application internals. The quick-start must
provide complete commands and explicit file ownership; diagnostics must state the
next action and distinguish administrator tasks from developer prerequisites.

The owner completed DP0/DP1 and explicitly authorized DP2 pure evaluation.
DP3 and then DP4 were subsequently authorized by the owner. DP4 remains pending
independent implementation review and full-suite evidence. No deployment, secrets,
live configuration or operational data changes are authorized.

Planning baseline was `98ae8af86` on `docs/agency-deployment-profiles`.
At the owner's request, fetched remote main and created a fresh worktree at
`/private/tmp/sapphire-agency-profiles-implementation`, branch
`feat/agency-deployment-profiles`, based on `d777d8b2ed4cf68b171b7c7a7b6e86d9067fbc0c`.
The reviewed plan was copied forward; original dirty checkout and planning worktree
remain untouched. No application implementation or live-host operation has occurred.
Repository recipes do not prove an installation's complete settings.

## Existing configuration paths and gaps

Python paths below are relative to `src/sapphire_flow/`.

| Consumer | Existing path / behavior | Risk or reusable capability |
| --- | --- | --- |
| Shared readers | `config/_overlay.py`: base then `SAPPHIRE_CONFIG_OVERLAY`, left-to-right; recursive tables, whole-list replacement; `${SAPPHIRE_*}` substitution before parsing | Reuse semantics. Relative paths depend on CWD; replacements drop earlier entries. QC and station-QC declarations are forbidden in overlays. |
| Application config | `config/deployment.py`, onboarding/QC/identity/river/Recap readers and nested weather/rating parsers | Typed validation is split across readers. `DeploymentConfig` alone is insufficient. Absent choices can reach Swiss adapter/onboarding defaults. |
| API and forecast/ingest workers | Compose mounts root `config.toml`; host overlays select more files; auth/CORS, paths and NWP requirements also use env | Root config contains Zurich display, Swiss seasons/danger policy, CAMELS, MeteoSwiss/LINDAS, Swiss authority and CHWRR QC. It is not neutral shared config. |
| Init | Migrations → tenant provisioning → role bootstrap → registrar; explicit base/overlay selection | Current main already wires Mac-mini init correctly. `config/declared_tenants.py` deliberately reads raw TOML without interpolation. Never execute init for preview. |
| Scheduled/manual Prefect jobs | `cli/register_deployments.py`: `SCHEDULE_*`, `SAPPHIRE_SKIP_DEPLOYMENTS`, default/ingest/backup pools; child process environment | Reuse spec construction. Nepal already excludes BAFU collectors and operational ERA5 ingest. Skip does not deactivate existing jobs. Base Compose does not forward every Python schedule variable, notably `SCHEDULE_COLLECT_BAFU_FORECASTS`. |
| NHS provisional forecast | `docker-compose.nhs-forecast.yml`, `config/river_stations.py`, `tools/forecast_config.py` | Existing checker covers forecast-specific chain/mounts. Preserve full worker-chain prefix and provider-only exception; STANDARD keeps original scope. Worker env can override generic job env. Registered job variables are another state layer. |
| CHWRR operator import | `docker-compose.operator.yml`, `cli/import_dhm_delivery.py` | Requires root base plus exactly `chwrr-import.toml`, fixed config-relative metadata and bounded CHWRR identity. Never substitute generic runtime chain or advertise as a Kazhydromet importer. |
| Stations/ratings | `tests/fixtures/dhm/stations.toml`, external delivery directory, `cli/rating_delivery_manifest.py`, provider mappings and provisional-rating declarations | Separate files/parsers already exist. A reference is not ingestion, approval or activation. Restricted files stay outside git. |
| Backup | Dedicated PG env/password file and `flows/backup.py` parameters | Does not use `load_config`; mounted TOML does not prove effective backup settings. Preserve dedicated pool/credential. |
| Postgres, Prefect server, Caddy | Compose env, secret references, Caddyfile/domain, volumes/ports | Separate from TOML. `.env` interpolation does not automatically forward values to containers. |
| Secrets/build identity | Compose build tokens, role-specific files read by `docker/entrypoint.sh`, Recap key file/dev env, API auth inputs | Inventory names/references only. Never read secret contents, construct credential URLs or dump environment. Reuse `tools/compose_release_identity.py`. |
| Host/operator commands | Bootstrap/start, watchdog/prune/probe wrappers; project `.env` and complete `COMPOSE_FILE`; CLI/flow parameters | Explicit `-f` replaces durable Compose list. Host tools have separate settings. Desired-config preview cannot prove registered jobs or actual processes agree. |
| Legacy Nepal forcing experiment | `docker-compose.nepal-forcing.yml`, `scripts/launchd/run-nepal-forcing.sh`, `scripts/nepal_forcing_run.py` | Separate project/network, host timer and env-only runner; image fallback references Swiss container. Label separately; do not silently migrate into Nepal cloud profile. |

Other reuse: config suites, Compose-render tests under `tests/unit/deploy/`, schedule
parity tests and declared-tenant validation. `scripts/onboard.py --validate-config`
is a DB-backed QC/registry check that refuses overlays, not an offline deployment
check. `config/paths.py` can create directories; preview must not call it.
Changing adapter type by deep merge can leave old keys. Reject conflicting settings
instead of silently calling them validated.

No general profile/provenance command was found. Existing quick-start advice is
stale around CORS, secrets, backup worker, cadence and Swiss-only onboarding.

## Proposed interface and ownership

New paths:

- `deploy/profiles/agency/{swiss,nepal,kazhydromet}.toml`
- `src/sapphire_flow/config/deployment_profile.py`
- `tools/deployment_profile.py`

Proposed commands, **not available yet**:

```text
uv run python tools/deployment_profile.py swiss show
uv run python tools/deployment_profile.py nepal check --installation /absolute/path/installation.toml
uv run python tools/deployment_profile.py kazhydromet show
uv run python tools/deployment_profile.py --profile-file /absolute/path/agency.toml check --installation /absolute/path/installation.toml
```

CLI synopsis: `(<name> | --profile-file PATH) {show,check}
[--installation PATH] [--format {text,json}]`. Exactly one explicit profile
selector is required for either command. Installation `agency_profile` is an
agreement check, not a second selector: resolve both to the same canonical manifest
path or fail with `PROFILE_SELECTION_CONFLICT`. A matching display name is not
sufficient. Relative CLI paths use invocation CWD; paths inside TOML use that
file's directory. Test private-profile show/check and selection conflicts.

Each manifest names its base, ordered runtime overlays, ordered Compose files and
intentional exceptional scopes. It references station/rating inputs by purpose and
existing parser. Its `secret_requirements` table declares logical names, allowed
consumers and build/runtime purpose. Unknown manifest keys fail. No recursive includes, executable
expressions or arbitrary override language.

Swiss/Nepal reference existing files: no duplicate scientific settings and no edits
to those files. Preview labels inherited legacy choices, without recommending them
as shared defaults. Operator scope retains root `config.toml`, the sole CHWRR overlay
and original metadata layout. No generic profile mount replaces these.

New `config/agencies/kazhydromet.toml` is an **incomplete template**, independent of
the Swiss base. Reuse existing neutral typed defaults for generic operational tuning.
Require explicit agency-sensitive selections before claiming an executable profile;
missing sections cannot silently select LINDAS/MeteoSwiss/CAMELS. Normalization of
legacy bases is deferred, not presented as complete.

An optional installation TOML describes **desired state, unverified against host**.
It is not consumed by host scripts and is not evidence of actual `.env`, processes
or registered jobs. Existing host files remain the runtime source of truth. This
increment does not add a second host-file parser or claim live parity. Every output
labels this limitation; comparing actual host inputs is a later migration gate.

The installation schema has `agency_profile` (name or explicit local manifest path),
`project_name`, `project_directory`, ordered `compose_files`, an allowlisted
`environment` table and `secret_bindings` keyed by runtime logical requirement.
Bindings contain delivery-file references only, never values or retrieval commands.
Build requirements retain the existing BuildKit environment input names and stay
unverified; runtime file bindings do not supply or validate build tokens. Domain,
ports and runtime selectors use their existing environment names, not duplicate
TOML aliases. Unknown fields fail. File references resolve relative to their
declaring file; container paths remain container paths. An explicit Compose list
is complete, never implicitly appended to the profile list. Without installation
input, installation checks are pending. Offline success never means ready to run.

### Administrator journey and file ownership

1. Select `swiss` or `nepal` for an existing recipe. For a new agency, copy the
   documented incomplete Kazhydromet manifest/base to an administrator-owned private
   directory, update its identity and explicit references, and select it with
   `--profile-file /absolute/path/agency.toml` instead of the positional name.
   Do not edit the public template or reuse Swiss defaults to make validation pass.
2. Copy proposed `deploy/examples/installation.example.toml` outside git, fill in
   host selections and secret-file references, and link the chosen profile. Agency
   config holds scientific/source choices; installation config holds host choices.
   Separate station/rating files remain private where necessary. The administrator
   owns these local files; maintainers own shipped profiles, schemas and examples.
3. Gather the required inputs below with the agency's data/model owners. `show`
   lists each unresolved input, its file/key, who supplies it, and whether it needs
   an agency decision, a file or developer work. A supplied value is not evidence
   of scientific approval; do not invent an approval workflow in this CLI.
4. Run `show`, then `check --installation ...`. Resolve failures and review pending
   checks. Inherited Swiss/Nepal choices are informational, not validation failures.
5. Output the exact ordered Compose paths/project directory, labelled **desired
   selection, not executed or verified against host**. Only `PASS_OFFLINE` supplies
   the runtime-verification handoff; other states give the remediation below.
   Use `docs/deployment/nepal-cloud-host.md` for Nepal and
   `docs/deployment/mac-mini-staging.md` for that Swiss installation. Other hosts need their own
   runbook; Kazhydromet has no runnable handoff until prerequisites are resolved.
   Never suggest a generic two-file launch for an installation with extra overlays.
   For NHS-enabled installations, include both the host runbook and
   `docs/deployment/nhs-forecast-configuration.md`. Whenever the desired file list
   differs from the host runbook example, explicitly say shorter-list commands do
   not apply. For other extra overlays, identify their applicable instructions;
   missing matching instructions are a pending handoff check, not permission to
   use the generic command.
   Every CLI summary says profile-driven launching is not available and identifies
   the next check or, on offline pass, runbook. Mention the separate Nepal-forcing
   experiment only as unmanaged; do not validate or configure it here.

Use a controlled evaluation environment. Reject conflicting ambient config/overlay
selectors, `COMPOSE_FILE`, `COMPOSE_PROJECT_NAME`, `SCHEDULE_*` and profile-owned paths.
Never implicitly load another working directory's `.env`. Report variable names and
origins, not arbitrary values. The later launcher must separately settle its exact
pass-through allowlist and relationship to host scripts' existing project `.env`.

The DRAFT `docs/plans/2026-11-11-one-command-deploy.md` proposes host profiles in
`deploy/profiles/`. Update its design to reference these namespaced agency profiles;
do not make incompatible schemas share the same files. No SSH/update/release work.

**Kazhydromet required inputs:** tenant identity; observation, forecast-weather and
historical-weather sources/access; stations/identifiers; rating data, units and datums
where applicable; QC/danger/season policies; models/artifacts and training-time
conventions; display timezone/calendar; hydrological day/grid; schedules; hosting,
paths/domain/ports and secret references. Unknown means unresolved. An unsupported
adapter/importer is a separate prerequisite, not an existing configurable capability.

### Provider-independent secret delivery

**Owner decision, 2026-11-17:** leave the secrets manager open. Proton Pass,
Infomaniak Barbican, Infisical, OpenBao and other standard managers are candidates,
not dependencies or promised integrations in this increment.

Keep two concerns separate:

- Agency profiles declare logical requirements (for example the worker database
  password), consuming services and build-time versus runtime use. They contain
  neither values nor vendor-specific retrieval commands.
- Installation configuration maps those requirements to existing delivery inputs.
  Runtime consumers continue reading their scoped secret files; build credentials
  retain the existing BuildKit secret mechanism. A later deployment-side CLI,
  agent or provisioning step retrieves values from the chosen manager and supplies
  those inputs. Vendor locators and bootstrap authentication stay installation-side,
  outside public agency examples and ordinary preview output.

Changing manager must not require changes to forecast models, services or agency
configuration. It may require a small deployment integration using the provider's
supported tooling; do not promise zero-configuration interchangeability. No vendor
SDK in application code, universal provider plugin framework or automatic fallback
to a different provider. VPN choice is independent of secret delivery.

The later migration must qualify scoped access, bootstrap-token handling, output
permissions, outage/restart behavior and coordinated rotation for the selected
provider. Account for file-bind replacement and consumer reload/recreation: writing
a new secret file alone does not prove a running service has adopted the value.
The current offline checker checks requirement/binding completeness and delivery
wiring only; it never resolves remote references or authenticates to a manager.
Logical requirements and allowed consumers live in the agency manifest. Compare
them with rendered Compose build/runtime secret grants and existing file-input
environment names; reject missing or extra grants, including owner credentials
exposed to workers/API. Compose is the evidence of actual declared grants, not a
second manually copied table. Preserve init's intentional bootstrap access.

## Check and preview contract

`show` may describe an incomplete profile. `check` returns nonzero for unresolved
required agency inputs, invalid selections or inconsistent configuration. No DB,
HTTP, Prefect registration, migrations, directory creation, secret-content reads or
operator data operations. Non-secret input presence/readability/schema checks are
allowed without displaying confidential contents.

Summary/exit contract for `check`: `FAIL` (1) for invalid/inconsistent configuration
or unresolved required agency inputs; `PENDING` (2) when no failures exist but an
offline check lacks installation inputs, tooling or required evidence;
`PASS_OFFLINE` (0) when all applicable offline checks pass. Failure takes precedence
over pending. Every result separately lists unverified runtime matters (actual host
parity, credentials, registered jobs, provider access); these do not become passing
checks or prevent a fully evidenced offline pass. `show` exits 0 for a parseable
incomplete profile but prints the same limitations, and exits 1 for invalid input.
Only `PASS_OFFLINE` hands off to runtime verification in the matching runbook;
it never authorizes launching. Missing agency values are `INPUT_REQUIRED`;
unsupported source/model integrations are `DEVELOPER_WORK_REQUIRED`, both failures.

| Result | Administrator's next step |
| --- | --- |
| FAIL | Resolve the named invalid/missing inputs or developer prerequisite; no launch handoff. |
| PENDING | Supply the specific missing installation input, local tooling, offline evidence or matching instructions; no launch handoff. |
| PASS_OFFLINE | Follow the matching runbook's runtime verification, retaining the exact desired Compose list and unverified-host warning. |

Diagnostic documentation links remain available in every state, but are labelled
as remediation, never as an instruction to launch. `show` is informational and
cannot confer a checked handoff even when its process exit is zero.

Diagnostics carry a stable code, plain-language cause, safe logical file/key or
variable name, next action and documentation link. Do not echo values or arbitrary
exception text. Add `--format json` to the same show/check interface, with
`schema_version: 1`, the summary, evidence scope, provenance and pending checks.
This sanitized stdout is the shareable support report; no separate bundle command.
Include available repository/release identity and ordered logical file references;
omit private absolute paths, provider locators and hashes of secret/private data.
Human output may show local non-secret paths for troubleshooting; JSON intended for
sharing uses logical aliases. Both formats use the same safe structured result.

1. Reuse all applicable readers and registrar spec building without CLI startup.
   Check agency selections, declared/write identity, references and service scopes.
   Keep raw tenant parsing independent of unrelated interpolation failures. Report
   unknown/unchecked sections rather than claiming blanket validation.
2. Show safe effective values with file/key provenance, whole-list replacement and
   named typed defaults. Include jobs, schedules, pools/skips, source choices,
   authority, mounts and exceptions. Derive defaults from existing models, not a
   second value table. A provenance helper beside the merge reuses its traversal;
   parity tests require identical merged output. Give raw declared tenants their
   own provenance path using their actual reader semantics.
3. Track interpolation before substitution; display placeholder/variable names and
   present/missing status, never substituted values. Allowlist safe preview fields;
   redact unknown strings, credential URLs, model blobs and station/provider/rating
   contents. Sanitize errors without raw input, including validation errors and
   subprocess stderr. Secret canaries must not appear even on failures.
4. Offline checking may invoke bounded `docker compose config` with explicit files,
   project directory and controlled environment. It starts no services and needs
   no daemon/network. Capture JSON/stdout/stderr in memory, emit safe summaries,
   never persist raw renders. Do not invent credential-validation success; required
   secret delivery wiring that cannot be checked remains an offline pending item;
   credential contents and availability remain explicitly unverified runtime matters.
   Tests use
   synthetic settings. Avoid imports of application startup modules/path helpers.
5. Compare requested schedules/skips with rendered init environment; reject values
   that are not forwarded. Parse cron locally using the installed scheduling library.
   Explain that skipped existing schedules may remain. Reuse `tools/forecast_config.py`
   when its required evidence is supplied; absent live pool/job evidence is listed
   as unverified runtime, not as an offline failure or proof of compatibility.
6. Derive each service's effective config chain from rendered environment and mounts,
   map its container paths back to readable host inputs, and compare with the
   declared scope. Reject missing/mismatched mounts and wrong overlay order.
   Image-only or uninspectable inputs remain pending, never silently assumed equal.
   Preserve NHS and operator exceptions. Compose subprocess handling belongs in
   `tools/`; the config module only parses/resolves typed profile data.

Maintain an explicit reader-coverage test: deployment, onboarding, QC and forecast
QC, deployment identity, declared tenants, river stations and Recap, plus their
nested weather/rating parsers. For every public config reader, record checked or
exempt with reason; helper modules are not automatically readers. Review new config
modules against this small inventory rather than introducing runtime discovery.
Also classify the existing `bafu_units`, `daily_evaluation`, `dhm`,
`evaluation_forecasts`, `nepal_weather`, `network_overview`, `operational_weather`,
`provisional_rating`, `swiss_overview` and `weather_gap` modules in that coverage
inventory; `paths` is explicitly excluded because it may create directories.
Schedule names/defaults come from registrar specification construction; a parity
test detects divergence from rendered init forwarding. The known BAFU forecast
forwarding repair is a separate follow-up, not silently applied here.
Specifically, an explicit `SCHEDULE_COLLECT_BAFU_FORECASTS` request that is not
forwarded fails. Without such a request, report the limitation and the registrar's
effective default; do not fail merely because the variable exists in Python.
Report skipped collectors as skipped; an explicitly requested but unforwarded
override is still a configuration error. Tests distinguish these cases rather
than requiring every registrar variable to be forwarded unconditionally.

## Dependencies and preservation

- **Secrets direction (owner, 2026-11-17):** retain flexibility to move from
  manually managed files to a secrets manager under the provider-independent
  delivery contract above. Manager integration/migration remains a separate task;
  offline validation must distinguish declared bindings from available credentials.
- M1 [PR #536](https://github.com/hydrosolutions/SAPPHIRE_flow/pull/536) is now merged:
  freshly fetched remote-main history contains `ba8445830`, followed by weather-grid
  changes #537 (`e5fa58eeb`) and #539 (`d777d8b2e`). This replaces the earlier
  open/unmerged observation; the external merge prerequisite is satisfied.
  It owns UTC origins, station overrides, grid validation and rejection of nonzero
  live origins before coordinated cutover. T2 consumes its merged fields/validators;
  recheck the baseline before implementation. Shipped Swiss/Nepal bases now declare
  `daily_grid_origin = "00:00"`; omission remains readable but grid resolution
  rejects it. Consume these existing validators without inferring a Kazhydromet
  origin. No duplicated grid work, resampling,
  retraining or activation. Display timezone, issue timezone, hydrological boundary
  and model training conventions remain distinct.
- Preserve QC overlay prohibition, raw tenant parsing, CHWRR import authority/layout,
  NHS forecast-only scope and stored job-variable handling. Link
  `docs/deployment/nhs-forecast-configuration.md`; do not replace its lifecycle.
- Root config, existing Compose/env files, credentials, roles, project/volume/image
  identities and host scripts remain unchanged. No migration is required for this
  increment. Existing commands remain the run path.
- Later authorized migration must privately inventory actual full chains/settings,
  compare previews and update manual/reboot/operator entry points together. Never
  replace an NHS-enabled chain with a two-file bootstrap example. Changed skip
  lists require explicit schedule handling. Clear NHS-owned job env with the current
  image before removing its mount/image. Preserve release/admission gates; Actions
  delivery is not activation. No automatic data rollback.

## Phases, acceptance and tests

### Worker dispatch milestones

#### DP0 completion and DP1 handoff

DP0 completed after a fresh `git fetch origin main`: HEAD and origin/main both
equal `d777d8b2ed4cf68b171b7c7a7b6e86d9067fbc0c`. M1 commit `ba8445830` is an
ancestor of HEAD; follow-ups #537 and #539 are present. Compared with the original
planning baseline, the relevant config changes are the existing daily-grid fields
and validators already accounted for above. Existing Compose chains and forecast
checker did not change in that interval. No scientific implementation is needed here.

Instruction reconciliation: use the owner's supplied single-Claude review policy
and orchestrator readiness authority, not the fetched AGENTS multi-review panel.
`docs/workflow.md` is absent on this baseline; do not recreate retired workflows.
The tracked `.claude/settings.json` has not been invoked. The owner explicitly
resolved the versioning conflict in favor of current repository policy: follow
`docs/standards/cicd.md`, advancing `RELEASE_TARGET` once per code PR from its tested
base with the existing release-target tool. This supersedes the supplied legacy
per-commit package-version bump instruction. Package versions remain dynamic;
never hand-edit generated metadata, create release tags or publish release identity.
No version files or release identity changed during DP0.

Independent milestone review is complete with no blockers. Its two borderline
interface/test-ownership suggestions were incorporated and subsequently accepted
by the owner's “sounds good” response. The orchestrator sets this plan READY for
the offline increment. HW0–ST1 remain separately gated; READY is not server authority.

Next worker: DP1 only, on `feat/agency-deployment-profiles` in
`/private/tmp/sapphire-agency-profiles-implementation`, starting from the SHA above
plus this uncommitted plan. Read T1, the proposed interface, the secret-delivery
contract and DP1's path/test ownership before edits. Deliver the typed resolver,
three manifests, incomplete Kazhydromet base, synthetic installation example,
schema/selection tests and staged documentation. Preserve existing base/overlay/
Compose files. Do not implement CLI/evaluation, invent grid values or touch real
station/rating data. Report targeted tests and unresolved prerequisites at DP1's
checkpoint. This was the DP0 handoff; the owner subsequently dispatched DP1,
whose implementation checkpoint follows below.

Historical DP0 verification: fetched baseline identity, ancestry and changed-path
inspection; plan whitespace check. At DP0, only this plan was untracked/changed.
No application tests, hooks, services, secret reads or deployments ran during DP0.

#### DP1 checkpoint — implemented, uncommitted

DP1 now provides `load_agency_profile()` and `select_deployment_profile()` in
`src/sapphire_flow/config/deployment_profile.py`. The latter returns immutable
`ProfileSelection(profile, installation, compose_files, missing_secret_bindings,
unverified_build_inputs)`. `AgencyProfile` retains ordered base/overlay references,
exceptional scopes, input references, required inputs and secret requirements;
`Installation` retains explicitly supplied desired host settings. This is selection
evidence only, not the DP2 `ProfileEvaluation` or a successful configuration check.

The shipped Swiss/Nepal manifests reference unchanged legacy files. Kazhydromet's
base is comments only and its required inputs remain unresolved. Optional operator
scope retains the original CHWRR base/overlay/metadata layout; NHS scope cannot
replace the STANDARD worker prefix. Conditional secret requirements map repository
paths into the installation's declared checkout; custom/renamed overlay wiring
remains unchecked until DP3. Runtime file bindings never read their targets, while
build requirements record only existing environment names and remain unverified.

Added staged `docs/deployment/profiles.md`, a synthetic installation example,
quickstart warning and the host-profile draft's namespace/reference note. No CLI,
Compose render, runtime configuration edits, commits or release-target changes.

Verification: 36 focused selection/schema tests passed; Ruff check and format check
and Pyright for both new Python files passed. Tests cover original chain references,
private selection conflicts, file-relative paths, ambient agreement/conflicts,
optional scopes, missing bindings, unknown fields and no secret-content/environment
value reads. Test tooling used a worktree-local editable package with build-backend
generated version metadata and existing runtime dependencies read-only; imports
were verified to resolve to this worktree. No full-suite/integrated independent
implementation review is claimed; those gates remain DP4 before merge.

DP1's next handoff was DP2 pure config evaluation using these typed references;
the owner subsequently authorized it. Do not treat profile `state` or supplied bindings as verified
agency configuration, available secrets or a runnable installation.

#### DP2 checkpoint — pure evaluation implemented, uncommitted

M1 remained an ancestor of dispatch HEAD `d777d8b2e`. Added
`evaluate_deployment_profile(selection, environment=...) -> ProfileEvaluation` with
the five agreed fields, safe fixed diagnostic codes and source aliases. It reuses
actual parsers and the existing merge traversal, not an independent merge engine.
Small parse-helper extractions preserve existing onboarding/identity/QC/forecast
QC/Recap/weather-scope loader semantics. The merged M1 validator is used unchanged.

Explicit injected interpolation never mutates/falls back to `os.environ`; values
from interpolated documents are conservatively redacted. Raw tenant validation and
name origins remain independent of runtime interpolation. New/private profiles must
explicitly supply agency-sensitive fields regardless of `configured` labels.
Onboarding/write-authority mismatches fail. The safe provenance subset covers
grid/display settings, tuning, source choices, list replacement and named typed
defaults; unknown/private strings, keys, tenant names and paths never enter it.

Every module has an explicit reader-coverage disposition. Current results always
retain Compose pending evidence, with additional flow-owned adapter/other section,
registry applicability, roster/reference and exceptional-scope gaps as appropriate.
DP3 must validate or establish non-applicability for those gaps rather than dropping
them to obtain a pass. No CLI, subprocess, startup imports, database/client creation,
secret reads or writes were added. Full suite and independent integrated patch
review remain DP4 gates. At the DP2 checkpoint, DP3 was the next undispatched milestone. The later DP3 checkpoint below records owner authorization.

DP2 verification: all 499 tests under `tests/unit/config/` passed after the final
code change. Ruff check/format passed for all 12 changed Python files. New profile
code/test and the other extracted-reader/provenance modules pass focused Pyright;
the full changed-source type run retains 12 pre-existing rule-parser diagnostics
(`forecast_qc_rules`: 7; `qc_rules`: 5) and passes the repository's per-file baseline
ratchet. This was a changed-source run, not a full-project type pass. Existing
`test_overlay.py` also retains its prior private-import/Unknown-list diagnostics;
no unrelated parser/test typing refactor was performed. Whitespace checks passed.

The milestones below are dispatch units; T1–T3 below remain their acceptance
specification, not additional parallel assignments. One implementer owns each pass.
Run sequentially because the milestones share the profile module and CLI. Prefer
the same worker across adjacent milestones, with a checkpoint after each. No worker
may execute this DRAFT; planning/review is permitted now, implementation requires
orchestrator READY plus a subsequent implementation instruction from the owner.

Every handoff records the exact starting/ending commit or uncommitted diff, changed
paths, commands/results, unresolved findings and next dependency. Do not equate
tests passing with independent approval. Each code milestone updates its affected
documentation. Code commits follow repository version-bump policy; workers stop
at reviewable changes and never merge, deploy or modify credentials.

| ID | Worker deliverable and owned paths | Prerequisite | Completion evidence |
| --- | --- | --- | --- |
| DP0 — Baseline and dispatch gate | Orchestrator refreshes remote-main/M1 status and relevant instructions; reconciles this plan against merged changes. Record dated evidence in this plan, no application edits. | Owner implementation instruction and plan readiness; any resulting material design change returns to review. | Exact baseline commit; actual M1 merge state; scoped worker handoff; no assumptions taken from the old PR-status note. DP1 may proceed before M1; DP2 may not. |
| DP1 — Named profiles | Implement T1: `deploy/profiles/agency/*.toml`, `deploy/examples/installation.example.toml`, `config/agencies/kazhydromet.toml`, `src/sapphire_flow/config/deployment_profile.py`, `tests/unit/config/test_deployment_profile.py`; staged profile guide/quickstart banner and host-profile design note. | DP0. | Typed profile/installation selection, private-file agreement, deterministic paths, explicit requirements/scopes and secret references. Schema/selection tests pass; Swiss/Nepal reference original files; Kazhydromet remains incomplete. No runnable CLI advertised yet. Handoff includes typed resolver interface and synthetic fixture inputs for DP2. |
| DP2 — Effective config and provenance | Implement T2's pure evaluation portion in the profile module; minimal shared provenance changes in `_overlay.py` and raw-tenant reader if needed; config tests and profile reference. No Compose subprocess or user CLI yet. | DP1 and merged M1, rechecked at dispatch. | Applicable readers consume the same chain as current behavior; coverage inventory classifies every reader. Tests prove legacy parity, list/default/interpolation provenance, raw-tenant semantics, missing agency-choice failures and no reader side effects. Return a typed, sanitized evaluation result with explicit unchecked evidence, ready for DP3. |
| DP3 — Offline CLI and service wiring | Finish T2 in `tools/deployment_profile.py`, profile result types as needed, `tests/unit/tools/test_deployment_profile.py`, relevant `tests/unit/deploy/` tests and profile reference. | DP2. | Public show/check commands, all exit states and safe JSON work; actual Compose render tests verify mounts/chain order, secret grants, requested schedule forwarding, operator/NHS exceptions. Canary, no-network/no-write and read-only-CWD tests pass. No service start or secret read. Handoff includes safe Swiss/Nepal/incomplete-agency output examples. |
| DP4 — Administrator walkthrough and release gate | Finish T3 in quickstart, profiles guide, config reference, README and affected standards/touchpoint links; review integrated DP1–DP3 behavior. | DP3. | A synthetic private-agency walkthrough covers selection, failures/pending/pass, JSON support output and overlay-specific runbook handoff without Python/Compose knowledge. All documented commands match CLI help. Focused suites, lint/type gates and full suite pass after final code edits; one independent implementation review with relevant deployment/secrets expertise is complete and material findings reach the owner. Deliver a reviewable PR, no merge or deployment. |

DP2's sanitized evaluation result and DP3's CLI share one representation; do not
create parallel provenance engines. DP1–DP3 documentation may say “in development”;
DP4 is the user-facing completion milestone for the **offline increment**. The
coherent increment can be one PR with worker checkpoints rather than several
partially usable releases. If split into code PRs, each follows the repository's
review/full-suite gates before merge. Do not add a reviewer per internal checkpoint.

Concrete worker handoff contracts:

- DP1 creates `docs/deployment/profiles.md`; DP4 completes that same guide. DP0
  evidence updates are owned by the orchestrator, not an implementation worker.
- DP2 defines `ProfileEvaluation` in `config/deployment_profile.py`, a frozen typed
  result with `summary`, `diagnostics`, `provenance`, `pending_checks` and
  `unverified_runtime` fields. Diagnostics carry the stable codes and remediation
  described above. Record field types and example results in its handoff; DP3
  extends/combines checks through this representation rather than replacing it.
  DP2 adds only merged M1 fields/validators needed by this plan; DP1 invents no
  grid/origin fields. An absent Compose check at DP2 is explicit pending evidence.
- DP2 owns reader coverage, merge/legacy parity, provenance, raw-tenant semantics
  and pure-reader side-effect tests. DP3 owns Compose renders, service mounts,
  secret grants, schedules, CLI exit/handoff behavior, JSON/text redaction and
  read-only-CWD checks. Reader-output redaction is also guarded at DP2's boundary;
  DP3 tests the whole command, not a second implementation of reader tests.
- DP3's real-render tests live in `tests/unit/tools/test_deployment_profile.py`;
  DP4 runs that file plus the existing deployment suite and the full suite. CI
  requires Compose for these tests, with the absence behavior specified under T2.
- The orchestrator commissions the integrated implementation review after final
  DP4 changes/tests and before marking the PR ready for owner approval. A draft PR
  may exist earlier. Material findings go to the owner; workers do not self-approve.
- HW0 fixes the final smoke paths and fixture contract in its independently
  reviewed READY plan before HW1 can be dispatched.

The following milestones record the accepted product direction but are **not
implementation dispatches from this plan**, even after this plan becomes READY:

| ID | Follow-on assignment | Dependency and exit gate |
| --- | --- | --- |
| HW0 — Reconcile sandbox and design smoke test | A planning worker obtains the other session's actual plan/branch and fixture contract, checks packaged FI model availability and proposes `docs/plans/deployment-hello-world.md`. It specifies host inputs, exact command, isolation/cleanup including failure behavior, safe report, resource/runtime limits and CI coverage. No runtime execution. | Separate owner planning instruction; actual sandbox handoff. May be planned while offline work progresses, but not by a second implementer editing these files. One bounded independent review and orchestrator readiness of the follow-on plan before HW1. If the sandbox artifact is unavailable, record the missing dependency rather than inventing it. |
| HW1 — Local hello-world capability | Implement the separately approved smoke plan using its reconciled paths/fixtures; demonstrate one worker-produced forecast through the API, default cleanup and retained safe report locally and in CI. | HW0 READY plus implementation authorization; packaged fixture/model and image preparation prerequisites met. Independent patch review and owner-controlled merge. A unit-test-only or direct in-process forecast does not meet this milestone. |
| HW2 — Target-server proof and handoff | An explicitly authorized operator runs the same versioned scenario on the intended server; records safe result and cleanup or explicit temporary retention. | HW1 qualified artifact and explicit server-run authorization. Successful server result is required before real-station onboarding; failed/incomplete run cannot pass. No real agency data or operational resources touched. |
| ST1 — One real agency station | The station-onboarding sandbox/workstream owns this milestone; link its actual plan when provided. | HW2 passed, required agency inputs and onboarding authorization. Success criteria and scientific acceptance belong to that workstream; no duplicate importer/model work here. |

Profile-driven launching/migration and a selected secrets-manager integration
remain separate future plans, not hidden tasks inside DP4 or HW1.

### T1 — Named selection and explicit scope (phase 1)

Paths: manifests/profile module above; new
`tests/unit/config/test_deployment_profile.py`; installation example;
one-command-deploy plan design note; early quickstart banner pointing to the staged
profile guide and explicitly labelling legacy quickstart instructions Swiss-specific.
Outcome: select an agency without manually ordering files; all scopes use existing
sources. Pre-change evidence: no profile resolver and selection spread across the
inventory. Acceptance/tests: CWD independence, no implicit `.env`, unknown/missing/
conflicting inputs, fixed operator scope, separate legacy forcing project, incomplete
Kazhydromet inputs; provider-independent secret requirements, missing bindings and
build/runtime separation. No base copies or executable launch paths.
T1 tests own schema/selection fixtures, including rejecting host-profile files as
agency profiles and selecting private profiles with agreeing/conflicting installation
references. No newly invented grid fields: defer M1-dependent template fields
to T2. T1 docs must not advertise an unshipped CLI as runnable.

### T2 — Validate and explain effective configuration (phase 2; M1 dependency)

Paths: profile module/tool; `src/sapphire_flow/config/_overlay.py` only as needed for
shared provenance traversal; `src/sapphire_flow/config/declared_tenants.py` only if
needed to expose raw-reader provenance; new `tests/unit/tools/test_deployment_profile.py`
and existing config/deploy suites.
Outcome: `show`/`check` satisfy the contract. Pre-change tests demonstrate absent
choices reaching Swiss defaults, unforwarded schedule settings and missing provenance.
Acceptance/tests: Swiss/Nepal parsed compatibility across all readers; list/default/
interpolation/raw-tenant provenance; conflicting adapters; invalid cron/unforwarded
schedule; service mounts; operator/NHS exceptions; no network/DB/writes; secret canaries
absent on malformed input; real Compose render with synthetic env, no daemon; clear
pending live checks. No execution of startup or operator data commands.
Use synthetic installation bindings representing a local file and two different
external managers: the same agency profile and consumer targets must remain valid
without vendor SDKs or network access. Remote availability must remain explicitly
unverified, and provider locators/credentials must not leak through diagnostics.
T2 tests own diagnostic/output contracts and service wiring: exit precedence,
support-report redaction, incorrect host/container mapping, extra secret grants,
reader coverage and side-effect guards. Test from a read-only working directory
with network/DB/process-launch boundaries guarded; only the bounded Compose render
is permitted. Compare stable safe Swiss/Nepal semantic summaries rather than
snapshotting incidental formatting or private paths. Real Compose tests require
the CLI in CI (absence fails that gate); local absence is an explicit skip, never a
claimed pass. No daemon is needed for this render test.

### T3 — Administrator entry point (phase 3)

Paths: `docs/deployment-quickstart.md`, new `docs/deployment/profiles.md`,
`docs/spec/config-reference.toml`, `README.md`, affected CI/CD/touchpoint links.
Outcome: short create/select/check guide and route to existing run instructions.
Clearly state profile-driven launching is not shipped. Profiles reference owns
manifest/precedence details; existing config reference owns application fields;
secrets/NHS/release/agency runbooks own detailed procedures. Pre-change: stale
quick-start and scattered entry points. Acceptance: synthetic new-agency walkthrough,
command/help parity, valid links, placeholders/synthetic public examples, no invented
Kazhydromet choices. Implementation history stays in this plan, not administrator steps.
Walkthrough covers file ownership, incomplete-input remediation, all result states,
safe support output and exact desired Compose selection/runbook handoff. Correct
stale cadence/CORS/secrets advice against current sources, without copying the
detailed reference into the quickstart.
Schedule documentation links to the registrar/default reference rather than
maintaining another copied default table. Include private-profile commands and
state-specific handoff examples in the walkthrough.

## Deployment “hello world” — follow-on proposal

Recommend a small, repeatable runtime smoke test alongside the station-onboarding
sandbox being developed in another session. This is separate from the offline
T1–T3 increment: running containers and producing a forecast cannot be evidence
from `check`. No sandbox is created or run in this session.

Existing reusable evidence: `tests/integration/test_e2e_pipeline.py` exercises
onboarding → training → hindcast → skill → forecast → API with a disposable PostGIS
container and reference fixtures. `tests/integration/test_fi_adapter_smoke.py`
checks FI model onboarding/prediction with a reference fake. These are developer
tests, not a shipped administrator deployment smoke command; they do not prove
Compose/worker wiring. `scripts/063_e2e_verify.py` contacts live MeteoSwiss and is
not an offline hello-world basis.

Before a follow-on design, reconcile the other session's sandbox plan/branch and
fixture ownership. No matching new sandbox plan was identified in this checkout;
do not infer its interface or duplicate its station importer. Reuse its synthetic
station/input manifest if compatible and its existing reset/run conventions. The
deployment test adds stack/startup/worker/API verification; the onboarding sandbox
owns experimentation with station preparation. A shared fixture is useful; a shared
database or operational volume is not required.

Proposed follow-on acceptance:

- One documented smoke command after explicit image/dependency preparation, with
  start, result and narrowly scoped cleanup instructions. Reuse Compose and existing
  flow/model entry points; do not build a second forecast implementation.
- Dedicated disposable project/network/volumes, localhost API, generated test-only
  credentials, fixed synthetic station/weather data, fixed cycle time and seed,
  and an existing FI-compatible sample model. No agency secrets or live providers;
  no publications, alerts or recurring operational schedules. Images/private build
  dependencies may require preparation access; the actual test runs without remote
  provider access and makes no claim of a public credential-free build.
  Use an explicit smoke-specific project identity, reject conflicting ambient
  Compose selectors, and reject operational/external volumes or fixed container
  names that bypass project isolation. Qualify these checks in the follow-on plan.
- Exercise migrations/init, enqueue one bounded cycle through the worker, persist
  its result and fetch that forecast through the API. Assert station/cycle identity,
  units, expected horizon/member shape and fixture-defined values/tolerances, not
  just container health. Report failing stage and safe diagnostics; exit nonzero
  on missing result, bad output or timeout.
- Repeatable on a clean installation, with cleanup restricted to smoke-owned
  resources; demonstrate no collision with an existing agency stack. One CI smoke
  integration gate plus a documented administrator walkthrough, not another broad
  test framework. Size/runtime budget comes from measuring the selected fixture.
- Success proves packaged pipeline wiring for the test case, not agency data
  access, scientific suitability, hydrological-day agreement or production readiness.

Proposed paths, subject to sandbox reconciliation: `docs/deployment/hello-world.md`,
`docker-compose.smoke.yml`, `tools/deployment_smoke.py`, and
`tests/integration/test_deployment_smoke.py`. Keep smoke fixtures outside
`deploy/profiles/agency/`: a test scenario is not an agency. Dependencies: sandbox
handoff, current FI/M1 contracts and a separately reviewed runtime-test plan. This
plan must first verify that its chosen FI-compatible model/fixture is packaged and
runnable in the worker image; unit-test availability alone is insufficient. This
follow-on does not block delivery of offline profiles and does not authorize a
launcher, deployment or implementation now.

Verification after implementation:

```text
uv run pytest tests/unit/config/ tests/unit/tools/test_deployment_profile.py tests/unit/tools/test_forecast_config.py tests/unit/deploy/ tests/unit/cli/test_register_deployments.py tests/unit/test_compose_schedule_default.py
```

Also run lint/format and changed-module type gates. Full suite must pass after the
final code change before merge, locally or CI. Planning-only verification: diff,
whitespace and paths; no application test pass is claimed in this session.

```json
{
  "execution": "sequential; one implementer per pass",
  "implementation_authorized_milestones": ["DP1", "DP2"],
  "milestones": [
    {"id": "DP0", "kind": "dispatch_gate", "depends_on": [], "gates": ["plan_READY", "owner_implementation_instruction"]},
    {"id": "DP1", "acceptance": "T1", "depends_on": ["DP0"]},
    {"id": "DP2", "acceptance": "T2_pure_evaluation", "depends_on": ["DP1"], "external_dependencies": ["M1_merged"]},
    {"id": "DP3", "acceptance": "T2_CLI_and_wiring", "depends_on": ["DP2"]},
    {"id": "DP4", "acceptance": "T3_and_integrated_review", "depends_on": ["DP3"]}
  ],
  "follow_on_only": [
    {"id": "HW0", "kind": "separate_plan", "external_dependencies": ["owner_planning_instruction", "sandbox_handoff"]},
    {"id": "HW1", "depends_on": ["HW0"], "gates": ["smoke_plan_READY", "implementation_authorization"]},
    {"id": "HW2", "depends_on": ["HW1"], "gates": ["explicit_server_run_authorization"]},
    {"id": "ST1", "depends_on": ["HW2"], "external_dependencies": ["onboarding_workstream", "agency_inputs", "onboarding_authorization"]}
  ]
}
```

## Review and next gate

One independent Claude review completed on 2026-11-17. It found duplicated-base
drift and operator-layout conflict, plus schedule forwarding, tenant/interpolation
provenance, env-file ambiguity and overlapping profile schemas. The owner selected
preview-first scope; this revision preserves legacy chains, makes scope boundaries
explicit and narrows implementation accordingly. Full report remains local at
`/private/tmp/sapphire-agency-profile-review.txt`, outside commits.

At the owner's request, a second independent Claude review assessed this revised
plan from hydromet-administrator and deployment-support-developer perspectives on
2026-11-17. It identified five blocking specification gaps: administrator setup
sequence and file ownership; pass/pending/fail semantics; concrete launch-runbook
handoff; checked installation versus actual host configuration parity; and
service-specific host/container path mapping. Further findings concern actionable
diagnostics, reader coverage, secret-scope drift and schedule-variable coverage.
Owner requested incorporation of the recommendations. This revision resolves the
five specification gaps above and adds bounded diagnostics/coverage tests. Use
explicit desired-state labelling rather than new live-host parsing; one safe JSON
format rather than a support-bundle subsystem; explicit reader coverage rather
than automatic registries. No extra `init` command, vendor SDK or snapshot framework.
The runtime hello-world proposal remains a separately reviewed follow-on. The full report remains local
at `/private/tmp/sapphire-agency-profile-admin-developer-review.txt`, outside commits.

At the owner's request, a third independent Claude review checked the revised
contracts and hello-world proposal on 2026-11-17. It found three remaining blocking
clarifications: explicit private-profile CLI syntax and agreement with installation
selection; result-specific remediation versus launch-runbook handoff; and complete
handoff for NHS/extra-overlay installations whose base runbook uses a shorter list.
Non-blocking notes concern naming the secret-requirements field, qualifying the
known schedule-forwarding gap, unmanaged forcing output and avoiding copied defaults.
The hello-world boundary was supported, with follow-on checks for explicit project
isolation and availability of a packaged FI-compatible fixture model.
Full report: `/private/tmp/sapphire-agency-profile-revised-review.txt` (local only).

Owner requested updates after this review. The plan now specifies selector
agreement, result-specific handoff and extra-overlay runbooks, plus the bounded
non-blocking clarifications. It also distinguishes runtime unknowns from offline
pending checks consistently. The owner then settled the administrator skill level,
joint ownership and staged smoke/onboarding vision, and requested dispatchable
milestones followed by one further independent review. DP0–DP4 and the separately
gated HW0–ST1 milestones implemented that planning request; implementation had not
been authorized at that review checkpoint. The later DP1 authorization and result
are recorded above; the owner later authorized DP2 as recorded in its checkpoint.
At that review checkpoint DP3–DP4 and runtime follow-ons remained undispatched; see the later DP3 checkpoint for current progress.

The owner-requested milestone review found no blocking issue for later offline
dispatch and confirmed the previous three concerns resolved. It identified two
borderline handoff risks: an unnamed DP2 result interface and overlapping T2 test
ownership. The concrete handoff contracts above address both, plus guide ownership,
M1-field timing, review ownership and final smoke-path selection. Full local report:
`/private/tmp/sapphire-agency-profile-milestones-review.txt`. No further review loop
is scheduled. The reviewer sampled file existence rather than auditing runtime
semantics; its statement that this checkout is not a Git repository is incorrect
(it is a linked worktree). No live host, current M1 status or sandbox interface was
verified by the reviewer. DP0 subsequently refreshed baseline/dependency evidence
as recorded above; live-host and sandbox evidence remains outside this increment.

The vision decisions are settled; the owner accepted the milestone result and
requested DP0. The orchestrator's DP0 assessment sets READY for the offline
increment, with the two borderline clarifications incorporated; no automatic
re-review is needed for those bounded handoff clarifications. This does not expand
preview-first scope or authorize later milestones without dispatch.
No automatic review loop is started. Current session
policy requires a Claude review, not the older multi-review panel in fetched
AGENTS.md. No implementation is authorized by this plan or the scope decision alone.


### DP3 checkpoint — offline CLI and bounded wiring evidence (2026-11-17)

Owner authorized DP3 in the existing isolated worktree after DP2. Implementation
adds `tools/deployment_profile.py` and `tests/unit/tools/test_deployment_profile.py`,
plus the minimal explicit-environment registrar spec builder. Existing runtime
registrar callers retain their ambient behavior. No live configuration, secrets,
operational data or services were changed; no commit/push/rebase was performed.

`show`/`check` consume the same immutable `ProfileEvaluation`, with schema-versioned
safe JSON and local text output. Compose rendering is isolated from `.env`, Docker
user configuration and service env-file contents, captures no persistent output,
and has time/size bounds. It never invokes builds or starts services. Pure wiring
checks cover config chains/mounts, secret grants and target inputs, requested
schedule forwarding, job selections/pools and declared operator/NHS exceptions.
Secret-manager neutrality is tested via interchangeable delivery-file bindings;
there is no manager SDK, credential lookup or integration claim.

Current Swiss/Nepal checks remain `PENDING`, not a fully validated offline pass:
flow/API/model policy, applicable roster/reference contents, selected BAFU registry,
operator metadata contents and NHS provider/ingest semantic comparison remain
explicit gaps. No no-op exemptions were added. Runtime host/credential/provider/job
facts remain unverified. The exit-0 PASS branch is tested separately using an
already evidenced typed result, not represented as an end-to-end fixture pass.

DP4 must assess/document the remaining evidence, complete administrator walkthrough
and integrated review, and run the full post-change suite locally or in CI. DP4 and
runtime follow-ons are not dispatched by this checkpoint. READY remains unchanged.

DP3 validation: related deployment/registrar/forecast-tool/release-tool suites passed
367 tests; the final expanded deployment-profile suite passed 36 tests after the
last reporting changes. Changed-source Pyright reports zero errors; Ruff lint and
format pass on the tool, registrar and new tests; `git diff --check` passes. All
project commands used the isolated worktree interpreter and task uv cache with
`PYTHONDONTWRITEBYTECODE=1`, reusing original dependency packages read-only. Actual
Compose renders used synthetic explicit installation values, including Swiss,
Nepal and optional operator/NHS recipes; no daemon or services were needed. Full
suite and independent implementation review remain DP4 gates. Capture size/error
sanitization and deterministic EOF-then-wait-timeout kill/reap are tested without
real waiting or processes in the timeout test. No complete real-host or whole-application offline PASS is claimed.


### DP4 implementation/documentation checkpoint — awaiting independent gates

The owner authorized this final integrated documentation pass. No production code
changed in this pass. Replaced the stale Swiss-only quickstart with create/select/
check and matched-runbook routes; linked README, application config reference,
CI/CD standard and touchpoint map. Removed milestone language from user-facing
instructions; implementation history remains here. Legacy launch recipes remain
unchanged and adopting preview requires no live migration.

Synthetic private-agency walkthrough used temporary files outside git and exact
`uv run python tools/deployment_profile.py` forms, with `UV_NO_SYNC=1` and the
isolated task environment. No scientific choices were fabricated:

- `--help`: exit 0, selector/installation/format and show/check forms present.
- `swiss show --format json`: exit 0, PENDING.
- `nepal check --format json` without installation: exit 2, PENDING.
- `--profile-file <private> show --format json`: exit 0, unresolved FAIL details.
- `--profile-file <private> check --format json`: exit 1, unresolved inputs.
- Same private check with `--installation <private>`: exit 1, INPUT_REQUIRED;
  JSON contains neither the private directory nor agency name, and no render runs.
- Malformed private base TOML, same show command: exit 1, CONFIG_LOAD_INVALID.

Copying the synthetic Nepal installation unchanged to an undeclared new-agency
secret contract correctly fails selection. The quickstart now explicitly tells
administrators to replace its recipe and remove/replace undeclared bindings. This
is example adaptation, not credential migration. PASS_OFFLINE remains an exit
contract tested with a fully evidenced typed result; no synthetic whole-application
or real-host pass is claimed while reader/policy gaps remain.

Both administrator documents' local link targets exist (17 links checked). Ruff
lint/format passes for the tool, registrar and tool tests; changed-source Pyright
reports zero errors; whitespace checks pass. The prior 36-tool-test and 367-related-
test results remain applicable because this pass changes documentation only.
The orchestrator owns the isolated full-suite route and one independent Claude
implementation review. **DP4 is not complete or self-approved** until those gates
and material findings are resolved by the owner/orchestrator. No commit, push,
version change, shared-git change, secret read or service launch occurred.

Orchestrator verification: 535 configuration/profile tests pass, and the full
source Pyright ratchet passes (344 diagnostics against baseline 400). A broader
isolated unit run stopped with 2,594 passes, two skips and ten environment-related
failures: subprocess dependency lookup, unavailable Prefect socket binding and
Compose plugin discovery under isolated HOME. This is not full-suite evidence;
CI must satisfy the final suite gate. No Docker services were started. Remote main
advanced to M2a3 (#540), with documentation-only overlap in this change's scope;
the independent implementation review and branch reconciliation remain pending.


### Independent-review corrections and accepted preparation scope

One independent Claude implementation review completed outside the repository.
The owner accepted **Keep preparation scope**: this increment intentionally has no
reachable complete offline pass for the shipped recipes. Developer-owned reader/
policy coverage and clearing conditions are documented in the profile reference;
full offline pass is follow-up work. This narrows the earlier all-states/runtime-
handoff acceptance: only the status/exit contract is tested for PASS, not an
administrator-reachable end-to-end pass. No exemption or override clears evidence.

Corrections address the actionable-diagnostics, text runtime-limitations and input-
ownership findings: static safe selection guidance, safe selector/table fields,
allowlisted required-input descriptions/owners, developer-work indication for an
unsupported river adapter, and explicit developer ownership of unimplemented
coverage. Empty incomplete requirements still fail. Missing required fields differ
from invalid typed values, so incomplete show does not hide invalid settings.

Delivery/provider/metadata path comparisons now normalize symlinks without opening
delivery contents. The installation example uses absolute delivery references and
commented release-input placeholders. Compose discovery stays limited to trusted
PATH executables; missing isolated discovery and missing no-env-resolution support
have separate diagnostics. No private Docker configuration/plugin scanning was
added, and no unsupported numeric version floor is claimed. Schedule defaults stay
visible per job, absent BAFU forecast forwarding is explicit, and duplicate identical
secret-input errors are collapsed.

Targeted regressions cover actionable conflicts, required-input ownership, runtime
text, whole-command canaries, malformed installation values, invalid incomplete
config, symlink delivery, matching checkout contents, per-job schedule preview,
Compose capability and explicit-versus-ambient registrar behavior. Missing plugin
locally skips explicitly; CI still fails. A fully valid private-agency custom-recipe
PASS remains outside the accepted preparation scope. The full suite/CI gate and
reconciliation with newer main remain orchestrator responsibilities.

The feature branch subsequently rebased cleanly onto remote main
`7b1ebb6f7d941134ae1d36005c9ff8144e482687` (M2a3 #540). Both documentation
additions are retained; no hydrological-time implementation was duplicated.
Release target follows the current once-per-PR process against that baseline.
Post-correction focused validation passed 203 tests, Ruff and changed-source
Pyright. Final CI remains required; the PR stays draft pending full-suite evidence.
