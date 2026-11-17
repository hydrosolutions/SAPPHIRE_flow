# Agency deployment profile reference

The `show`/`check` CLI now previews desired configuration and checks explicit
Compose wiring offline. **Profile-driven launching is not available.** It never
starts services, contacts a server, reads credential files or changes host state.
The current Swiss/Nepal recipes still report `PENDING` for the policy/reference gaps
listed below; they are not fully validated deployments. This is intentionally a
preparation-only tool: no shipped profile can currently reach `PASS_OFFLINE`.
Completing that coverage requires developer work, not an administrator override.

```bash
uv run python tools/deployment_profile.py swiss show
uv run python tools/deployment_profile.py nepal check --installation /private/installation.toml
uv run python tools/deployment_profile.py --profile-file /private/agency.toml show
uv run python tools/deployment_profile.py --profile-file /private/agency.toml check --installation /private/installation.toml --format json
```

`show` returns 0 for a parseable incomplete agency, 1 for invalid input. `check`
returns 1 for invalid/inconsistent inputs, 2 for pending evidence, and 0 only for a
fully evidenced `PASS_OFFLINE`. None of these checks proves live host state.
`show` does not run Compose. `check` requires a complete explicit installation and
consistent release identity before rendering. It uses the installed Compose CLI
(`docker-compose` on `PATH` or in Docker’s standard system plugin directories) with a clean environment, no project
`.env`, isolated Docker configuration, and bounded
in-memory output. All Compose profiles are included **for inspection only**.
Includes/extends remain unsupported evidence; no services are started.
Any service `env_file` declaration stops at preflight with `ENV_FILES_UNCHECKED`:
no Compose command is invoked and file contents are not read. Some Compose versions
read these files despite advertising `--no-env-resolution`, so the checker defers
them consistently across versions and retains unchecked Compose evidence.
Compose must be discoverable as a trusted `docker-compose` on `PATH` or in a
[standard system plugin directory](https://github.com/docker/cli/blob/master/cli-plugins/manager/manager_unix.go): `/usr/local/lib/docker/cli-plugins`,
`/usr/local/libexec/docker/cli-plugins`, `/usr/lib/docker/cli-plugins`, or
`/usr/libexec/docker/cli-plugins`. The checker invokes that binary directly, avoiding
Docker CLI discovery under an isolated configuration path. An unavailable or older
PATH candidate does not mask a compatible system plugin. User Docker
configuration/plugin directories are not inspected. A plugin available
only through the user's Docker configuration may need a standalone executable on
`PATH`. Capability detection requires `config --no-env-resolution`; the checker
reports missing discovery separately from missing capability and render failure.

JSON has `schema_version: 1`, `evidence_scope`, `launching`, `support`, and the shared
`summary`, `diagnostics`, `provenance`, `pending_checks`, `unverified_runtime` fields.
Support selection uses ordered file aliases, never private paths or names. A supplied
full hexadecimal source revision can be shown; arbitrary release/image strings are
not. Text additionally lists the selected local Compose paths for the administrator.
Errors never print raw parser errors or captured Compose output. A pass permits a
handoff to the selected existing runbook with the complete ordered recipe; pending
or failed results do not confer that handoff.

| Profile | Current referenced recipe | Run instructions |
| --- | --- | --- |
| `swiss` | Existing root config + Mac-mini overlay and Compose recipe | [Mac-mini staging](mac-mini-staging.md) |
| `nepal` | Existing root config + Nepal-cloud overlay and Compose recipe | [Nepal cloud host](nepal-cloud-host.md) |
| `kazhydromet` | Incomplete independent template; no host/source/model/grid choice | No runnable recipe yet |

Swiss and Nepal profiles inherit legacy agency choices. They are not neutral
defaults for a new agency, and their two-file recipes do not inventory a live
host's extra overlays. The separate Nepal-forcing experiment remains unmanaged.

## Files and ownership

Maintainers own public manifests in `deploy/profiles/agency/`. Administrators own
their private profile/installation files outside git, scientists supply scientific
choices, and developers support missing integrations. For a new agency, copy the
Kazhydromet manifest and base outside git, rename the manifest identity and update
every relative reference. Do not fill missing choices using the Swiss base.

Copy `deploy/examples/installation.example.toml` outside git for a synthetic shape
example. Replace its paths with the actual checkout and delivery locations. It
describes **desired state, unverified against host**, and is not consumed by Compose
or host scripts. It never replaces the actual host `.env` or durable Compose list.

Station/rating files stay separate and may be private. An input reference records
purpose, existing parser and optional path; it neither imports nor approves data.
Kazhydromet's source formats and scientific inputs must be supplied by the agency.

## Manifest reference

All files use strict schema version `1`; unknown fields fail. No includes,
expressions, `${...}` substitution or implicit `.env` loading is supported here.

| Field | Meaning |
| --- | --- |
| `schema_version`, `name`, `state` | Version, lowercase identity, and `legacy`, `incomplete` or `configured`. State is a declaration, not validation success. |
| `base_config`, `overlays` | Base path and ordered overlay paths. Scientific settings remain in these files. |
| `compose_files` | Complete ordered recipe. May be empty only for an incomplete profile. |
| `required_inputs` | Unresolved inputs for an incomplete profile; selection does not resolve them. |
| `runbook` | Optional documentation path; selecting a profile is not a launch handoff. |
| `inputs` | Entries with purpose `stations`, `ratings` or `forecast_provider`, paired respectively with `stations_toml`, `rating_delivery_manifest` or `river_stations`, and optional `path`. |
| `scopes` | Optional CHWRR operator and NHS forecast-only exceptions described below. |
| `secret_requirements` | Tables keyed by logical secret name, containing `use`, `consumers`, optional `when_compose`, and build-only `environment_name`. No secret values. |

Paths resolve relative to the declaring TOML, independently of the working
directory. Explicit private profile/installation paths supplied by a caller resolve
relative to that caller's working directory. Missing referenced inputs may remain
unresolved; Selection reads only manifest and installation documents; evaluation reads their application config chain.
Lists preserve order and reject duplicate paths.

`chwrr_import` scope is tied to `docker-compose.operator.yml`, root `config.toml`,
the sole `config/overlays/chwrr-import.toml` overlay and the existing config-relative
`tests/fixtures/dhm/stations.toml` metadata. It cannot inherit the runtime overlay.
`forecast_only` scope is tied to `docker-compose.nhs-forecast.yml` and the
`NHS_PROVIDER_CONFIG_SOURCE` input; it cannot replace the STANDARD worker base or
overlays. The offline checker retains the full worker prefix plus provider-only
suffix. See [NHS lifecycle](nhs-forecast-configuration.md). These declarations do
not enable the optional Compose files or register jobs.

## Installation reference

Required fields are `schema_version`, `agency_profile`, `project_name`,
`project_directory` and a complete ordered `compose_files` list. The list replaces,
not appends to, the profile recipe. `agency_profile` is a shipped name or explicit
manifest path; it must resolve to the same canonical file selected by the caller.
A matching display name is insufficient.

`environment` accepts non-secret names: `VERSION`, `SAPPHIRE_RELEASE_VERSION`,
`SAPPHIRE_SOURCE_REVISION`, `SAPPHIRE_DOMAIN`, `SAPPHIRE_CORS_ORIGINS`,
`SAPPHIRE_REQUIRE_NWP`, `SAPPHIRE_SKIP_DEPLOYMENTS`, `SAPPHIRE_DHM_DELIVERY_DIR`,
`NHS_PROVIDER_CONFIG_SOURCE`, `NHS_FORECAST_JOB_OVERLAYS` and `SCHEDULE_*` names.
Their values are strings; the checker validates schedule names, cron syntax and
forwarding to init using the existing registrar specifications. Secret values, retrieval commands and vendor locators do not belong here.

Ambient configuration/Compose selectors must agree with explicit selection.
The ambient project name must match `project_name`; the ordered Compose list must
match the selected complete list. Other permitted ambient inputs must match the
installation table. Conflicts fail without echoing values. Nothing mutates the
environment. Secret environment variables are not read or returned by selection.

`secret_bindings` maps runtime logical requirements to local delivery-file paths.
No existence, content or provider availability is checked. Missing bindings remain
explicit in the selection result. Optional requirements activate only when their
`when_compose` file is selected; repository-owned paths follow the installation's
`project_directory`, allowing a desired checkout different from the current one.
That mapping is not evidence that the installed file exists or matches this repo.
Renamed/custom overlays retain an explicit review gap even after structural
service-wiring checks. Unknown or build-secret file bindings fail.

Build credentials keep the existing environment-sourced BuildKit inputs. The
manifest records only their variable names, never values; they remain unverified
build requirements. A runtime file binding must not claim to supply a build token.
A later manager integration can supply the existing delivery inputs independently
of agency/scientific configuration; no manager is selected by this increment.

Application fields remain documented in
[`docs/spec/config-reference.toml`](../spec/config-reference.toml). Schedule defaults
remain owned by the registrar, not copied here. Implementation history is maintained separately in the [plan](../plans/2026-11-17-agency-deployment-profiles.md).

## Pure evaluation boundary (developer reference)

`evaluate_deployment_profile(selection, environment=...)` returns an immutable
`ProfileEvaluation`: `summary`, sanitized `diagnostics`, safe `provenance`,
`pending_checks` and `unverified_runtime`. Each diagnostic has an `owner`:
`administrator`, `agency_specialist` or `developer`. Text also prints runtime
limitations; offline evidence never validates credentials, providers or live jobs. It accepts explicitly supplied placeholder
inputs and never falls back to the process environment. An omitted mapping is
empty. Do not pass secret credentials as configuration values.

Evaluation reuses the existing application parsers, including merged M1 grid
validation. Onboarding, deployment identity, QC, forecast QC, Recap and named weather
scope loaders delegate to pure `parse_*` functions with their original semantics.
Nested weather/rating/evaluation models remain validated through `DeploymentConfig`.
Raw tenant declarations are read independently without substitution, even when
runtime interpolation fails; onboarding ownership must agree with write authority.

Provenance comes from the same shared merge traversal as runtime loading: dictionaries
merge, lists replace, and QC overlay prohibitions remain enforced. Sources use
`base`, ordered `overlay[n]`, or named parser/model defaults. A small allowlisted
subset of effective fields is displayed; validation coverage does not mean every
field has a displayed value. Lists, tenant names, station/rating contents, paths,
unknown strings and arbitrary config keys are omitted or redacted. Only recognized
timezone/enum strings and safe numeric/boolean fields may appear as values.

Interpolation evidence records names and present/missing status. Because substitution
happens before TOML parsing and can alter document structure, values originating in
any interpolated document are conservatively redacted, including unrelated values
in that document. No substituted text or raw parser exception is returned. Raw
tenant provenance uses its own reader's name origins and always redacts names.

Swiss/Nepal retain their legacy choices, including defaults named in provenance.
Private/new agency manifests require explicit sensitive fields even when labelled
`configured`; selecting that label cannot turn an empty file into a Swiss deployment.
Unknown requirements and real station/model/training evidence remain unresolved.

The pure evaluator returns `FAIL` for invalid/unresolved inputs, or `PENDING`:
service wiring has not yet been checked, so it cannot return `PASS_OFFLINE`.
The explicit coverage inventory classifies each config module. Flow-owned adapter
policies, additional API/model/monitoring sections, worker-selected BAFU registry
applicability, applicable roster/reference files and exceptional scopes remain
pending where relevant. The CLI adds the wiring checks described below; it does
not discard unresolved reader evidence.
Actual host state, credentials, jobs/pools and provider access stay unverified runtime
matters even after offline checks are complete.


## Offline wiring coverage and remaining evidence

The checker compares init/API/forecast/ingest config chains, read-only mounts,
runtime/build secret consumers and delivery declarations, credential file targets,
requested schedule forwarding, registrar job selection and worker pools. Backup
uses its separate credential/pool contract. No secret target is opened. Local-file,
Proton-delivered and other manager-delivered files use the same logical binding
contract; no manager SDK or retrieval operation is implemented.

The operator retains its fixed independent chain and metadata bind. NHS keeps the
STANDARD worker prefix, provider bind and forecast-only registration suffix. Its
provider-file semantics and ingest-provider comparison remain pending. Installed
config paths mapped to another checkout must have identical nonsecret config
contents; absent/different content remains pending. Extra Compose recipes and
service env-file declarations retain explicit review gaps before rendering.

Job selection is shown as enabled/skipped; skipping does not remove previously
registered live jobs. Registrar-owned cron defaults can be shown when no schedule
override is forwarded; overridden cron values are redacted. Actual jobs/pools and
provider/credential/build availability remain unverified runtime facts.

Swiss/Nepal retain pending flow-owned adapter policies and additional API/model/
monitoring sections. Applicable station/rating references, roster checks and operator
metadata contents remain pending; optional NHS provider semantics remain pending.
The BAFU registry requirement is inapplicable only when no rendered service selects
it; a selected registry remains pending. Deployment support must resolve these
limitations before treating a result as a complete offline pass. This increment is a preparation tool, not a
forecast-system smoke test or a station-onboarding sandbox.


## Actionable diagnostic reference

| Code | Field and action |
| --- | --- |
| `PROFILE_AMBIENT_CONFLICT` | A safe named selector such as `COMPOSE_FILE` conflicts; unset it for the command or align the explicit installation. Unknown names remain redacted. |
| `PROFILE_SELECTION_CONFLICT` | Set installation `agency_profile` to the same canonical manifest selected by the command. |
| `INSTALLATION_SECRET_INVALID` | Compare `secret_bindings` with declared runtime requirements; remove undeclared/build bindings without sharing their values. |
| `PROFILE_SCHEMA_INVALID`, `INSTALLATION_SCHEMA_INVALID` | Check required fields, unknown keys and table/value shapes against this reference. |
| `PROFILE_READ_ERROR`, `PROFILE_PATH_INVALID` | Check selected TOML readability/syntax or use a literal local path; raw error text and private paths are not shared in JSON. |
| `COMPOSE_UNAVAILABLE` | Expose a trusted standalone Compose executable on `PATH`, or install a system-discoverable plugin. |
| `COMPOSE_CAPABILITY_MISSING` | Use Compose whose `config --help` advertises `--no-env-resolution`. |
| `COMPOSE_RENDER_FAILED` | Check explicit installation inputs and recipe privately; raw captured output is intentionally withheld. |
| `DEVELOPER_WORK_REQUIRED` | The selected river-source type has no existing adapter. Ask deployment support about integration; do not substitute another agency's source. |

Known `required_inputs` names are shown with a fixed owner and description;
custom/private requirement names are redacted as additional agency inputs. The
agency supplies scientific/data choices, administrators supply installation and
delivery references, and developers implement unsupported integrations. Changing
`state` or deleting the requirements does not certify scientific readiness.

| Developer-owned pending evidence | Clearing condition (follow-up work) |
| --- | --- |
| Adapter and additional flow/API/model policies | Reuse applicable runtime policy validators without constructing clients, and add parity evidence. |
| Roster and scoped station/rating/provider inputs | Add existing-reader checks and scope/reference comparisons without exposing private contents. |
| Selected BAFU registry | Validate the selected mapped registry using the existing reader, or establish non-applicability. |
| New-agency scientific evidence | Provide and validate the agency's station/model/training handoff; never invent defaults. |
| Custom recipe or missing runbook | Review custom execution semantics and supply matched operational instructions. |

Administrators cannot clear these gaps by editing a flag or supplying an override.
The complete-pass/handoff path is reserved for a future coverage increment; current
preparation results remain FAIL/PENDING. An absent BAFU forecast schedule forwarding
setting is explicitly shown with the registrar default. A differing schedule value
is redacted only for the affected job; unchanged default values remain visible.
