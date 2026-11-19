# Preview a disposable profile rehearsal

The preview checks whether your private profile fits the supported rehearsal
shape and describes the intended changes. It starts no services, contacts no
Docker daemon, reads no credential delivery files and creates no report directory.
`run` and `cleanup` are not available yet. A successful preview is **not** image,
host, forecast or deployment-readiness evidence.

Start with the [administrator quick-start](../deployment-quickstart.md) and obtain
`PASS_OFFLINE` from the [profile checker](profiles.md#supported-offline-path).
Use the documented project Python environment and a trusted standalone Compose
renderer as described in [offline rendering](profiles.md#offline-rendering).
No Docker daemon is needed for preview.

```sh
uv run python tools/deployment_rehearsal.py --profile-file /private/deployment/profile.toml preview --installation /private/deployment/installation.toml
uv run python tools/deployment_rehearsal.py --profile-file /private/deployment/profile.toml preview --installation /private/deployment/installation.toml --format json
uv run python tools/deployment_rehearsal.py --help
```

Use a clean source checkout matching the installation's project directory,
`SAPPHIRE_SOURCE_REVISION` and `RELEASE_TARGET`. For the default `release-target`
identity, both `VERSION` and `SAPPHIRE_RELEASE_VERSION` equal that target. This
checks the intended identity only; the images remain unprobed.
Developers can explicitly select `--image-identity ci-development` with
`SAPPHIRE_RELEASE_VERSION=0.dev0+g<full checkout HEAD>`, the same full source
revision and a Docker-safe `VERSION` tag. This is development evidence, never
release qualification. There is no arbitrary expected-version override.

Only the standard stored-data topology with exactly forecast and backup jobs is
supported. It must declare one writable tenant matching onboarding, with no
exceptional scopes or local station/rating references. Use standard named data
volumes; host data directories, external resources and custom resource names are
outside this rehearsal. Application configuration must be literal, without
`${...}` substitutions. Both default and Aquacast images and pinned infrastructure
images will need to be preloaded before a future runtime rehearsal.

Keep the five existing runtime secret bindings as file references in the
installation. The preview checks their roles but does not open them or require
them to exist. A future rehearsal will generate independent test credentials,
never use your real deliveries. Do not reference a secret delivery or `.env` file
as application configuration, Compose input or runbook; aliases are refused too.
Unset ambient Docker endpoint/context/TLS overrides and conflicting profile
selectors for this command. Nothing changes the live deployment's environment.

## Interpret the result

| Result | Exit | Meaning and next step |
| --- | --- | --- |
| `PLAN_READY` | 0 | Complete preview only. Review the planned replacements and unresolved runtime prerequisites. No launch command is available. |
| `FAIL` | 1 | Invalid input, conflicting selection, identity mismatch, protected input or detected drift. Correct the selected files and rerun. |
| `PENDING` | 2 | Missing offline evidence or selection outside the narrower rehearsal boundary. Follow profile diagnostics or ask deployment support; there is no force option. |

Docker endpoint/context overrides, `SOURCE_DIRTY` and
`SOURCE_CHECKOUT_MISMATCH` are `PENDING` (2): unsupported or unavailable evidence.
A conflicting profile selector, `SOURCE_IDENTITY_MISMATCH`, detected drift and
malformed inputs are `FAIL` (1). `INPUT_INVALID` is a sanitized boundary failure;
check the selected files and retry after stabilizing any concurrent edits.

`SOURCE_DIRTY` requires a clean checked checkout, not ignoring changed files.
`SOURCE_IDENTITY_MISMATCH` requires a matching version/source declaration;
`SOURCE_CHECKOUT_MISMATCH` requires running from the selected checkout.
`INPUT_DRIFT` / `SOURCE_DRIFT` mean files changed during preview: stabilize the
inputs and retry. `DYNAMIC_CONFIG_UNSUPPORTED` requires literal application inputs.
`REHEARSAL_*_UNSUPPORTED` identifies a selection outside the support limits above.
Existing configuration diagnostic codes retain their meaning in the
[profile reference](profiles.md#results-and-diagnostics).

The plan replaces the project/resource names, credentials and data volumes;
pins images to IDs that have **not yet been resolved**; snapshots selected
nonsecret configuration and tracked initialization/Caddy files read-only;
removes builds and published ports; makes both networks internal; sets Caddy to
HTTP `:80`; and disables restarts. File bytes, executable modes, config order,
commands, privilege limits, health checks and storage sharing must be preserved.
Snapshot binds forbid implicit host-directory creation. Your selected domain and
CORS-origin settings remain desired, untested settings in the private installation;
the rehearsal's internal HTTP route would not test them.

JSON contains safe codes, fixed plan descriptions, source revision and explicit
unverified boundaries. It contains neither private configuration nor an executable
Compose recipe. Immutable image IDs, generated paths and names stay unresolved.
The private in-memory transformation is checked against the complete original
render; future runtime execution must repeat validation and freeze the inputs.

The intended later sequence is fresh database/Prefect startup, verified paused
pools before normal initialization, two registered jobs with paused schedules,
worker/API startup, internal authentication checks with no flow execution, then
ownership-checked cleanup. None of that runtime sequence is delivered by preview.
For an existing synthetic forecast test, use the separate
[installation smoke guide](hello-world.md); it does not validate a private profile.
