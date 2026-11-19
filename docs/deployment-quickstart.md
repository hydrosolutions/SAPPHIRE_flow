# Prepare a deployment

This tool previews private deployment inputs and checks desired service wiring.
It does not launch services, migrate a host, retrieve credentials or produce a
forecast. A complete configuration can earn `PASS_OFFLINE` for the
[supported stored-data path](deployment/profiles.md#supported-offline-path).
Other integrations retain explicit coverage gaps. Actual deployment files belong
outside the public application repository.

For a separate check of installation and worker/API wiring, run the
[synthetic deployment smoke test](deployment/hello-world.md). It needs no agency
data or provider credentials and does not validate your private profile.
To try this offline checker first, the
[synthetic scenario](../tests/fixtures/deployment_profile/README.md) has a complete
copy-and-check walkthrough. It is test data, not a deployment recipe.

The narrower [disposable rehearsal preview](deployment/profile-rehearsal.md)
can describe a supported private profile’s isolated startup plan after
`PASS_OFFLINE`. It does not start services or provide a launch command.

## 1. Copy the generic examples into private storage

Use a checkout with its [Python/uv environment](../README.md#developer-quick-start)
installed. Copy these files together into a directory you own outside this checkout:

- `deploy/examples/profile.example.toml`
- `deploy/examples/application.example.toml`
- `deploy/examples/installation.example.toml`

They are annotated incomplete shapes, not runnable recipes. `/private/deployment/`
below denotes your directory; it is not a required location. You may maintain these
files in a separate private Git repository. Credentials stay in a secret manager
or delivery files, never in either Git repository.

Keep the profile's relative `base_config` reference aligned with the copied
application file. Give the profile any private label; it is not a lookup key and
is not emitted in shareable output. Paths inside TOML resolve relative to their
own file, not the current working directory.

## 2. Supply the short required checklist

The application example shows commented key shapes. Work with the indicated owners:

| Owner | Inputs to supply |
| --- | --- |
| Administrator | Tenant/write authority, retention, host recipe/paths/domain, schedules and logical secret-delivery references. |
| Data/science specialists | Observation/forecast/history sources, declared model-input capabilities, hydrological grid, station/rating files, units/datums, QC/alert/season policy, model/artifact choices and training conventions. |
| Developer support | Unsupported source formats, missing integrations and coverage gaps reported as developer-owned. |

Reuse existing **typed defaults** for optional presentation and disabled alerts.
Choose `default_display_timezone` explicitly as a valid named timezone, normally
the local operational zone; explicit `UTC` is valid. Named zones handle daylight
saving where applicable. Incomplete previews label omitted UTC as a provisional
`fallback`, not a completed choice. Gregorian calendar and disabled
forecast/observation/pipeline alerts are neutral initial defaults; omit these
four fields unless you need an override.
Other inherited defaults require review. Do not copy every field from the reference
or treat another deployment's scientific settings as general defaults.

Hydrological grid, display timezone and model training conventions are separate.
Explicitly declare `available_nwp_parameters` and
`available_past_only_nwp_parameters`; an empty list is a valid capability declaration.
An enabled `meteoswiss_nwp` adapter also requires its publication-delay choice.
Keep station/rating deliveries separate and reference their supported parser/file
in the profile. The [configuration reference](spec/config-reference.toml) owns
field details; the [profile reference](deployment/profiles.md) owns selection,
precedence and diagnostic behavior.

For the supported stored-data path, station/model database provisioning and
scientific acceptance stay with the onboarding workflow. The offline pass does
not require invented station/model files or certify that those database records
exist. Declared local references and unresolved configuration choices still
prevent a pass; do not remove requirements merely to silence a diagnostic.

Leave `state = "incomplete"` while choices remain unresolved. Remove a
`required_inputs` entry only when its choice/file is supplied. `configured` is a
declaration, not validation success; it cannot waive required scientific inputs.

## 3. Preview, describe the installation, then check

From the application checkout:

```bash
uv run python tools/deployment_profile.py --profile-file /private/deployment/profile.example.toml show
uv run python tools/deployment_profile.py --profile-file /private/deployment/profile.example.toml check --installation /private/deployment/installation.example.toml --format json
uv run python tools/deployment_profile.py --help
```

`show` needs no Docker. `check` also requires compatible Compose; see the
[renderer requirements](deployment/profiles.md#offline-rendering).

Edit the installation example's placeholder checkout and complete ordered Compose
file list. Set `agency_profile` to the same private manifest path. Supply release
identity from the reviewed build/release record. Align runtime `secret_bindings`
with the profile's declared logical requirements; file references are relative to
the installation file unless absolute. These inputs describe desired state and do
not replace the server's `.env` or change Compose files.

| Result | Exit | Action |
| --- | --- | --- |
| `FAIL` | 1 | Supply missing inputs or correct invalid/inconsistent settings using the diagnostic owner and action. |
| `PENDING` | 2 | Supply missing administrator evidence or ask developers to complete the stated coverage gap. |
| `PASS_OFFLINE` | 0 | Supplied configuration and supported service wiring are consistent. Review the listed runtime checks with deployment support before any launch. |

A parseable incomplete `show` exits 0 with unresolved input diagnostics; invalid
TOML or supplied typed values exit 1. It never approves a launch. JSON reports safe
provenance and file aliases, omitting private names/paths and arbitrary values.
Do not share credential files or raw Compose output. Text includes local selected
paths and always lists unverified runtime facts.

## 4. Continue with deployment support

A matching host runbook must preserve the **complete ordered recipe** and provide
runtime credential/provider/job checks, authentication, backups and upgrade steps.
Existing source-specific runbooks and configurations are compatibility material,
not generic starters; their migration is separate work. Existing runtime readers
and recipes remain unchanged by this tool.

Schedule defaults remain owned by the [registrar](../src/sapphire_flow/cli/register_deployments.py).
Skipping a job does not remove an already registered job. Use the
[security standard](standards/security.md) and [secrets bootstrap](secrets-bootstrap.md)
for credential/authentication details. Broader offline coverage, manager
integration and profile-driven launch remain follow-on work. The separate
[synthetic smoke test](deployment/hello-world.md) checks installation and internal
worker/API wiring; the station onboarding sandbox checks a different workflow.
Neither validates this private profile or proves public host access.
