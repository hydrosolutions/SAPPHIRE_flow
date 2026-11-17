# Prepare an agency deployment

Use a named profile to preview an agency's configuration and check desired service
wiring before following its host runbook. **Profile-driven launching is not
available.** The current Swiss/Nepal profiles still report `PENDING` for checks
listed in the [profile reference](deployment/profiles.md#offline-wiring-coverage-and-remaining-evidence).
No shipped profile can currently reach `PASS_OFFLINE`; developer-owned coverage
gaps must be completed in a follow-up. An offline preview does not inspect or
change a running server.

## 1. Select an existing agency or create a private profile

Use a checkout with its Python/uv environment installed using the project's
[developer setup](../README.md#developer-quick-start). `show` needs no Docker;
`check` also needs Docker Compose with `config --no-env-resolution` support.
Run commands below from the checkout; `/private/agency/` denotes a directory you
own outside the public repository, not a required location.

For an existing agency:

```bash
uv run python tools/deployment_profile.py swiss show
uv run python tools/deployment_profile.py nepal show
```

For a new agency, copy these two templates into your private directory:

- `deploy/profiles/agency/kazhydromet.toml` → `/private/agency/profile.toml`
- `config/agencies/kazhydromet.toml` → `/private/agency/base.toml`

In the copied profile, change `name` to your agency's lowercase identifier and
`base_config` to `"base.toml"`. Keep `state = "incomplete"`, the unresolved input
list and empty host recipe until those choices have been supplied. Paths inside
the profile resolve relative to that file. Do not copy the Swiss base or fill
missing choices using another agency's defaults.

```bash
uv run python tools/deployment_profile.py --profile-file /private/agency/profile.toml show
uv run python tools/deployment_profile.py --profile-file /private/agency/profile.toml check
```

The first command can succeed while describing missing inputs; the second returns
`FAIL` until the required choices are supplied. A developer supports configuration
and host wiring; agency specialists supply sources, stations/identifiers,
rating units/datums, QC/alert/season policy, models/artifacts and training
conventions. **Display timezone, hydrological day grid and training-time conventions
are separate choices.** Kazhydromet has no assumed data source, station set, model
or hydrological day.

Keep station/rating deliveries separate and reference them by their supported
purpose/parser as described in the [profile reference](deployment/profiles.md).
The maintained [application configuration reference](spec/config-reference.toml)
owns field details. Public examples must contain no credentials or confidential
agency data.

## 2. Describe the intended installation

Copy `deploy/examples/installation.example.toml` outside git and edit it. It shows
a synthetic Nepal-shaped installation, not defaults for a new agency: replace its
recipe and remove/replace bindings not declared by your agency profile. Set
`agency_profile` to the same named profile or canonical private-profile path you
select on the command line. Supply the actual checkout directory, project name
and **complete ordered Compose file list**, including any operator, NHS or other
installed overlays. A private new agency needs a reviewed host recipe; the empty
template is not runnable.

Fill non-secret installation inputs using the host's
[release instructions](deployment/actions-staging-release.md). Runtime
`secret_bindings` contain only delivery-file paths; use
[secrets bootstrap](secrets-bootstrap.md) and the host runbook for credential
provisioning. Existing build credentials retain their environment-name mechanism.
A secrets manager can supply delivery inputs, but this tool does not retrieve or
validate credentials. Installation files describe desired state; they do not
replace the server's `.env` or alter Compose configuration.

## 3. Check and interpret the result

```bash
uv run python tools/deployment_profile.py nepal check --installation /private/agency/installation.toml
uv run python tools/deployment_profile.py --profile-file /private/agency/profile.toml check --installation /private/agency/installation.toml --format json
uv run python tools/deployment_profile.py --help
```

| Result | Exit | Administrator action |
| --- | --- | --- |
| `FAIL` | 1 | Correct the listed invalid/inconsistent inputs or supply unresolved agency choices; follow each diagnostic owner/action and check again. |
| `PENDING` | 2 | Supply missing evidence or ask deployment support to resolve the named coverage gap. This is not a checked launch handoff. |
| `PASS_OFFLINE` | 0 | Reserved for completed offline coverage; not currently reachable for shipped profiles. Even then, host, credentials, providers and registered jobs need runtime verification. |

`show` exits 0 for a parseable incomplete profile and 1 for invalid selection or
input. It is informational, never a launch approval. JSON includes safe diagnostic
codes, provenance and ordered file aliases for support; private paths and arbitrary
config values are omitted/redacted. Text also shows local desired paths. Do not
attach credential files or the raw rendered Compose document to a support request.

Schedules and skipped jobs come from the
[registrar](../src/sapphire_flow/cli/register_deployments.py) and
[orchestration reference](standards/orchestration.md#scheduling), not a second
schedule table here. Skipping a job does not remove an already registered live job.
Configure actual CORS origins using the [security standard](standards/security.md);
wildcard origins are not a substitute for authentication setup.

## 4. Follow the host's run instructions

- Swiss: [Mac-mini staging](deployment/mac-mini-staging.md).
- Nepal: [Nepal cloud host](deployment/nepal-cloud-host.md) and its exact-image route.
- Forecast-only NHS overlay: additionally follow the
  [NHS lifecycle](deployment/nhs-forecast-configuration.md).
- New agency: have deployment support supply a matching host runbook before launch.

Keep every selected Compose file in every operational command. A shorter example
in a host runbook does not replace the installation's full list. Existing Swiss
and Nepal files and launch commands are preserved; adopting this preview requires
no live migration. Inventory the actual host recipe before describing an existing
installation, since the shipped profiles cannot discover extra host overlays.

A repeatable deployment forecast smoke test remains proposed follow-on work.
Offline `check` does not produce a forecast, and the separate station-onboarding
sandbox does not establish deployment wiring or host readiness.
