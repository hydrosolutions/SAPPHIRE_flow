# Deployment input reference

Deployment manifests are administrator-owned files outside the public application
repository. The repository provides only generic examples and tooling. There is
no built-in catalogue, name-based lookup or legacy validation exemption.

The feature remains preparation-only: missing inputs fail; unchecked evidence is
PENDING. Complete offline PASS and profile-driven launching require follow-up work.
It never inspects a live server, opens credential files or retrieves manager secrets.

The [synthetic deployment smoke test](hello-world.md) independently checks generic
worker/API wiring. It does not select, launch or validate a private profile.

## Selection and file ownership

```bash
uv run python tools/deployment_profile.py --profile-file /private/deployment/profile.toml show
uv run python tools/deployment_profile.py --profile-file /private/deployment/profile.toml check --installation /private/deployment/installation.toml
uv run python tools/deployment_profile.py --profile-file /private/deployment/profile.toml check --installation /private/deployment/installation.toml --format json
```

Exactly one `--profile-file PATH` is required. An installation's `agency_profile`
is a path resolved relative to the installation file and must identify the same
canonical manifest. A matching display label is insufficient. CLI paths resolve
relative to the invocation directory; file references resolve relative to their
own declaring TOML. Lists preserve order and reject duplicate canonical paths.
No includes, retrieval commands, `${...}` expressions or implicit `.env` loading
are supported in these boundary files.

Copy the three `deploy/examples/*.example.toml` files into private storage. Private
version control is appropriate for non-secret configuration. Keep credentials in
manager-delivered or manually provisioned files outside Git. Public examples must
contain no confidential agency data or credentials.

## Manifest schema

Strict `schema_version = 1`; unknown fields fail.

| Field | Meaning |
| --- | --- |
| `name` | Arbitrary nonempty private label; never a selector or shareable-output name. |
| `state` | `incomplete` or `configured`; neither bypasses scientific/input checks. |
| `base_config`, `overlays` | Application base and ordered overlay paths. Existing readers merge dictionaries and replace lists. |
| `compose_files` | Complete ordered recipe; empty is allowed only while incomplete. |
| `required_inputs` | Unresolved inputs; known names get safe descriptions and owners, custom names are redacted. |
| `runbook` | Optional matched operational instructions; not launch authorization. |
| `inputs` | Separate references with purpose `stations`, `ratings` or `forecast_provider`, paired with `stations_toml`, `rating_delivery_manifest` or `river_stations`, plus optional path. They do not import data. |
| `secret_requirements` | Logical names with `use` (`runtime`/`build`), `consumers`, optional `when_compose`, build-only `environment_name`; never values. |
| `scopes` | Explicit compatibility contracts described below, not generic extension points. |

The application reference is maintained in
[`docs/spec/config-reference.toml`](../spec/config-reference.toml). Runtime config
readers remain authoritative; this feature does not add a second defaults/merge
engine. Preview coverage does not mean every application field is displayed.

## Defaults and required choices

Set `default_display_timezone` explicitly to a valid named timezone, normally the
local operational zone; explicit `UTC` is valid. Named zones handle daylight
saving where applicable. This is one choice per deployment, not per user. Missing
values produce administrator-owned `INPUT_REQUIRED`; empty, invalid, offset-only
or non-string values fail validation without echoing the input. Incomplete `show`
remains informational for omission and labels the reader's UTC value
`default_status = fallback`. Configured profiles never show an omitted timezone
as effective UTC; `check` and configured `show` fail for the missing choice.
Invalid supplied values never receive a fallback. Existing runtime defaults and
UI formatting are unchanged. Display time, hydrological boundaries, storage and
transport timestamps, and model training conventions remain separate.

The name-only neutral allowlist contains exactly `calendar`, `enable_forecast_alerts`, `enable_observation_alerts` and
`enable_pipeline_alerts`. Effective values come from existing typed readers.
When incomplete/invalid inputs
prevent full parsing, absent neutral fields can still be shown using the
reader’s literal field defaults; supplied invalid values are never replaced or
labelled neutral. Missing retention is named as administrator-owned
`max_retention_days`.
Omitted values have their reader origin and `default_status = neutral`; explicit
overrides show file origin and `default_status = explicit`. Other previewed
inherited defaults have `default_status = review` and aggregate into developer-owned
`DEFAULT_REVIEW_PENDING`. They are visible evidence, not endorsed deployment choices.

Identity/write authority, sources, hydrological grid and scientific policy remain
explicit. Model training conventions must be documented independently. The closed
provider-default audit requires `reanalysis_source`, `available_nwp_parameters` and
`available_past_only_nwp_parameters`; both capability sets may explicitly be empty.
An enabled `meteoswiss_nwp` adapter additionally requires explicit
`nwp_cycle_min_age_minutes`. Existing grid activation restrictions are preserved.
Unsupported integrations need developer work; unknown or incomplete inputs never
become a pass. Do not use another deployment's defaults to fill scientific gaps.

Station/rating/model references and scientific decisions belong to data owners;
host paths, schedules and delivery references belong to administrators. Existing
adapter/flow policies not covered by pure readers remain developer review gaps.

## Installation schema

Required: `schema_version`, `agency_profile` (manifest path), `project_name`,
`project_directory`, and complete ordered `compose_files`. This list replaces,
not appends to, the manifest recipe. It describes desired configuration only.

`environment` permits non-secret `VERSION`, `SAPPHIRE_RELEASE_VERSION`,
`SAPPHIRE_SOURCE_REVISION`, `SAPPHIRE_DOMAIN`, `SAPPHIRE_CORS_ORIGINS`,
`SAPPHIRE_REQUIRE_NWP`, `SAPPHIRE_SKIP_DEPLOYMENTS`, `SAPPHIRE_DHM_DELIVERY_DIR`,
`NHS_PROVIDER_CONFIG_SOURCE`, `NHS_FORECAST_JOB_OVERLAYS` and `SCHEDULE_*` names.
Actual schedule-name/cron/forwarding checks use the existing registrar. Obtain
release inputs from the reviewed build record, not invented placeholders.

Ambient configuration/Compose selectors must agree with the explicit selection;
conflicts fail without printing their values. Secret environment values are not
read. Runtime `secret_bindings` contain delivery-file paths only. Paths are
normalized without opening their targets, so manager-delivered symlinks work.
Build secrets keep environment-name requirements; a file binding cannot claim to
supply a build token. There is no provider SDK or manager integration.

## Offline rendering

Compose is discovered on `PATH` or in Docker's four
[standard system plugin directories](https://github.com/docker/cli/blob/master/cli-plugins/manager/manager_unix.go).
The binary is invoked directly; no private Docker configuration/plugin directory is
inspected. Older/broken candidates cannot mask a compatible permitted binary.
`config --no-env-resolution` is required; rendering uses no project `.env`, an
isolated Docker configuration, bounded in-memory output and all profiles for
inspection only. No builds, containers, daemon operations or services are started.

Any service `env_file` declaration stops at preflight with `ENV_FILES_UNCHECKED`
before Compose invocation. Some Compose versions read those files despite the flag;
contents are never read here. Includes/extends retain unsupported evidence.

Checks compare init/API/forecast/ingest config chains and read-only binds, secret
consumer grants/targets, schedule forwarding and worker pools. Different checkout
config contents must match the evaluated files; absent/different files remain
pending. Backup has its separate credential/pool contract. Extra recipe execution
semantics still require developer review.

Supported compatibility scopes are tightly constrained to the existing checkout:
`chwrr_import` retains root config, the sole operator overlay and its fixed metadata
layout; `forecast_only` retains the STANDARD worker chain and provider-only suffix.
Their declarations do not activate files or jobs. See the existing
[provider lifecycle](nhs-forecast-configuration.md). Generic examples omit these scopes.

## Results and diagnostics

`check`: FAIL/1 for invalid or unresolved required inputs; PENDING/2 for evidence
not established; PASS_OFFLINE/0 is reserved for completed coverage. `show` returns
0 for a parseable incomplete profile with missing-input diagnostics, 1 for invalid
selection/TOML/supplied typed fields. Neither is launch approval.

JSON schema version 1 includes `evidence_scope`, `launching`, `support`, `summary`,
`diagnostics`, `provenance`, `pending_checks` and `unverified_runtime`. Diagnostic
owners are `administrator`, `agency_specialist` or `developer`. JSON uses ordered
file aliases and `private-profile`, never private labels/paths. Arbitrary strings,
keys, tenants, station contents and lists are redacted. Interpolation records only
safe variable names and presence; values from interpolated documents are redacted.
Raw tenant declarations retain their separate, unsubstituted reader semantics.

Text additionally prints local desired paths and unverified host, credentials,
providers, build credentials and registered jobs/pools. Job selections show
active/skipped state; skips do not deregister existing jobs. Registrar defaults
are review-required, and differing overridden cron values are redacted per job.

| Code | Action |
| --- | --- |
| `PROFILE_AMBIENT_CONFLICT` | Unset the named safe selector for this command or align the explicit installation. |
| `PROFILE_SELECTION_CONFLICT` | Match installation `agency_profile` to the canonical private manifest. |
| `INSTALLATION_SECRET_INVALID` | Remove bindings not declared as runtime requirements; do not share contents. |
| `PROFILE_SCHEMA_INVALID`, `INSTALLATION_SCHEMA_INVALID` | Correct required fields, unknown keys or table/value shapes. |
| `PROFILE_READ_ERROR`, `PROFILE_PATH_INVALID` | Check private TOML syntax/readability and literal local paths. |
| `COMPOSE_UNAVAILABLE`, `COMPOSE_CAPABILITY_MISSING` | Supply a trusted compatible binary in a permitted location. |
| `COMPOSE_RENDER_FAILED` | Check explicit recipe inputs privately; raw captured output is withheld. |
| `INPUT_REQUIRED` | Supply the indicated owner's missing decision/reference. |
| `DEVELOPER_WORK_REQUIRED` | Ask support to implement an unsupported integration or identify a supported source. |
| `DEFAULT_REVIEW_PENDING` | Developers audit inherited non-neutral scientific/operational choices. |

Remaining developer coverage includes adapter/flow/API/model policy parity,
roster/station/rating/provider reference semantics, selected external registry
validation and scientific model/training evidence. Clearing a gap requires actual
reader/validator evidence or demonstrated non-applicability, never a flag override.
Full offline validation, launching, manager integration and a runtime forecast smoke
test remain follow-ups. Existing operational recipes are compatibility material;
their migration is separate from this generic preparation feature.
