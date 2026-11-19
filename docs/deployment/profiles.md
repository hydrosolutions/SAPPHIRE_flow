# Deployment input reference

Deployment manifests are administrator-owned files outside the public application
repository. The repository provides only generic examples and tooling. There is
no built-in catalogue, name-based lookup or legacy validation exemption.

The feature remains preparation-only: missing inputs fail; unchecked evidence is
PENDING. The supported path below can earn PASS_OFFLINE for configuration
consistency. Profile-driven launching remains separate work.
It never inspects a live server, opens credential files or retrieves manager secrets.

The [synthetic deployment smoke test](hello-world.md) independently checks generic
worker/API wiring. It does not select, launch or validate a private profile.

For a clean, source-matched standard profile, the separate
[rehearsal preview](profile-rehearsal.md) checks a narrower disposable-startup
plan. It leaves images and runtime evidence unresolved and exposes no run command.

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
An inherited preview field proven unused by the selected configuration is labelled
`default_status = inactive`, with its original reader provenance. This is not a
neutral default endorsement: enabling its consumer restores the review obligation.
Invalid supplied values still fail their reader even when a feature is disabled.

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
`SAPPHIRE_REQUIRE_NWP`, `SAPPHIRE_ONLY_DEPLOYMENTS`, `SAPPHIRE_SKIP_DEPLOYMENTS`,
`SAPPHIRE_BACKUP_DUMP_TIMEOUT_S`, `SAPPHIRE_DHM_DELIVERY_DIR`,
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

Checks compare init/API/forecast/ingest/backup config chains and read-only binds,
secret consumer grants/targets, schedule forwarding and worker pools. Backup is
checked for wiring, including its backup credential, database environment and
storage; its flow does not parse application TOML. The standard Compose recipe is
rendered separately as execution evidence rather than copying its defaults into
the checker. Shared named-volume replacements must point all consumers at the
same storage while preserving each consumer's access mode.

External manifests may live anywhere, but `project_directory` must identify the
same source checkout as the running checker for complete execution coverage.
Another checkout remains pending: run the checker from the matching checkout.
Actual host files and image contents remain unverified. A runbook reference is
checked only for bounded, readable nonempty text; this does not approve its
instructions. Extra recipe execution semantics still require developer review.

Supported compatibility scopes are tightly constrained to the existing checkout:
`chwrr_import` retains root config, the sole operator overlay and its fixed metadata
layout; `forecast_only` retains the STANDARD worker chain and provider-only suffix.
Their declarations do not activate files or jobs. See the existing
[provider lifecycle](nhs-forecast-configuration.md). Generic examples omit these scopes.

## Results and diagnostics

`check`: FAIL/1 for invalid or unresolved required inputs; PENDING/2 for evidence
not established; PASS_OFFLINE/0 for completed supported offline coverage. `show` returns
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
Station/model database state, scientific readiness, preserved registered-job
overrides and actual installed image identity remain unverified offline. The
registrar preserves existing job parameters; a desired preview does not inspect
or replace those server-side values.

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
Broader offline coverage, launching and manager integration remain follow-ups.
The separate [synthetic smoke test](hello-world.md) now checks installation and
internal worker/API wiring; it does not clear profile diagnostics. Existing
operational recipes are compatibility material; their migration is separate from
this generic preparation feature.

## Supported offline path

The first complete path uses the ordinary repository Compose topology and
administrator-owned configuration mounted through a private overlay:

- Stored-data forecasting: `operational_weather.route = "stored_reanalysis"`
  and `adapters.weather_forecast.enabled = false`. Station observations, weather
  and model artifacts are provisioned separately. Their presence and suitability
  are not checked offline.
- Exactly `forecast-cycle` and `backup-database` selected through explicit
  `SAPPHIRE_ONLY_DEPLOYMENTS`, forwarded by the private overlay to init. Do not
  combine it with a skip list. The ingest worker may remain idle. Re-registration
  does not remove jobs already installed on a server.
- A common ordered config chain across all application consumers; standard
  commands, images, entrypoints, networks, dependencies and credential roles.
  Changing these requires corresponding coverage. Image declarations are
  compared; actual installed image identity is still a runtime check.
- The base file is mounted at `/app/config.toml`; overlay targets are direct
  `.toml` files under `/app/config/overlays/`, in the declared order. These are
  container paths, not restrictions on the private files' host location.
- Explicit public route and API-origin inputs, with no additional human-auth,
  operator/provider scope, registry, roster/overview or provisional-rating
  integration. Per-job config overlays and additional selected jobs stay pending.
  Supply `SAPPHIRE_CORS_ORIGINS` explicitly; an empty string selects same-origin
  use. `SAPPHIRE_DOMAIN = ":80"` declares a plain HTTP test route, not loopback
  isolation. Neither option proves DNS, TLS or host-port access. An optional backup dump timeout
  must be a positive finite number of seconds.
- No declared station/rating/provider input files in this first path. Adding one
  restores its validation obligation. The historical station-file readers are
  specific to their workflows; a filename or parser label alone is not evidence
  of generic validation.

The four neutral defaults remain available. Choose retention periods and the
stored-weather route explicitly, alongside the required identity, source and
scientific declarations. Other typed options retain their existing readers and
defaults; do not copy every application field into a private config.

`PASS_OFFLINE` certifies these supplied declarations and wiring only. It always
lists actual host/credential/provider state, station/model database state,
scientific readiness, registered job overrides and installed image identity as
unverified. The separate onboarding and deployment workflows resolve those facts.

To exercise the checker without agency inputs, use the
[synthetic offline scenario](../../tests/fixtures/deployment_profile/README.md).
It demonstrates copying files outside the checkout and running the actual command.
Its scientific values, release identity and secret references are test data;
never launch it or use it as an agency's configuration.
