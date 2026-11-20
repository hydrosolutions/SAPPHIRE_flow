# Preview a persistent installation

The installation tool currently provides **preview only**. It checks the proposed
configuration for a new stack that will remain running with jobs paused and HTTP
access limited to the server. `install`, `verify`, `stop` and `start` are not yet
available. A `PLAN_READY` result does not mean anything has been installed.

## Select and check your inputs

Follow the [deployment quick-start](../deployment-quickstart.md) to create a private
profile and installation file outside the checkout. The
[profile reference](profiles.md) owns the schema, precedence and supported offline
path. Use one explicit writable tenant, stored reanalysis, a disabled forecast
adapter, and exactly `forecast-cycle,backup-database`. Additional provider/operator
scopes and station-file imports need later coverage; do not remove real requirements
to obtain a pass. Supply scientific choices with your data specialists.

In the installation file's existing `[environment]` table, explicitly choose:

```toml
SAPPHIRE_DOMAIN = ":80"
SAPPHIRE_CORS_ORIGINS = ""
```

This declares the initial HTTP/same-origin route. The preview proposes publishing
only Caddy on IPv4 loopback, normally `127.0.0.1:8080`; it does not enable public
access or TLS. Other domain/origin choices are refused instead of silently replaced.
The original Compose files remain unchanged.

Use a clean source checkout matching the release identity in the installation
file. A future installation will require matching, already loaded default and
Aquacast images on a dedicated Linux amd64 host with local rootful Docker Engine
28 or newer. Preview needs the project's installed Python/uv environment and
[compatible Compose](profiles.md#offline-rendering), but no Docker daemon or images.
It does not check the target host, image availability or release admission.

## Run the preview

From the application checkout, substituting your private paths:

```bash
uv run python tools/deployment_install.py --profile-file /private/deployment/profile.toml preview --installation /private/deployment/installation.toml --state-dir /private/installation-state
uv run python tools/deployment_install.py --profile-file /private/deployment/profile.toml preview --installation /private/deployment/installation.toml --state-dir /private/installation-state --http-port 18080 --format json
```

The proposed state directory must be an unused absolute path outside the checkout,
under a stable administrator-owned parent. Preview does not create it. Existing
paths refuse because this work does not adopt existing or partially installed
stacks. The future directory will retain private configuration and installation
records alongside the persistent database; it must not be a temporary report folder.
Its owner, permissions and host resource collisions remain runtime checks.

Runtime secret bindings may point to files that do not yet exist. Preview checks
their declarations and grants without opening them. Deliver all five credentials
according to the [secrets bootstrap](../secrets-bootstrap.md) before any future
installation. Neither a secret manager nor its login is required to preview.

| Result | Exit | Meaning / next action |
| --- | --- | --- |
| `PLAN_READY` | 0 | Supported offline configuration and finite recipe changes checked. Review proposed changes and unverified runtime facts; launching is not implemented. |
| `REFUSED` | 2 | Correct the listed prerequisite or unsupported input. Underlying `offline` diagnostics identify the responsible owner and action. |
| `FAIL` | 1 | Unexpected tool failure. Send the safe JSON report to deployment support. |

JSON contains safe file aliases, offline diagnostics and provenance, the source
revision, proposed port and counts of services, image references, volumes,
configuration mounts and runtime credentials. It excludes private paths, project/profile names,
credential values and executable Compose content. Text also includes the local
state path. Never share raw Compose output or credential files.

Common refusals:

- `SOURCE_DIRTY`: commit or remove your own outstanding source changes, or use a
  separate clean checkout. Do not discard someone else's work.
- `SOURCE_IDENTITY_MISMATCH`: align the checkout and reviewed release inputs;
  do not invent an identity just to pass.
- `LOCAL_ROUTE_REQUIRED`: explicitly set the two route values shown above.
- `STATE_PATH_INVALID` / `STATE_PATH_OCCUPIED`: choose a new external state path;
  an existing installation needs its own recovery procedure.
- `PROTECTED_INPUT`: a proposed nonsecret input aliases a credential delivery or
  `.env`. Separate configuration from credentials; never send their contents.
- `REHEARSAL_*`: a shared preparation check found a scope, tenant, resource or bind
  outside the common supported subset. See the
  [rehearsal boundaries](profile-rehearsal.md); the command still previews a
  persistent installation and does not run a rehearsal.

The preview reports intended removal of builds/build credentials, immutable image
pinning, ownership labels, frozen read-only configuration binds, and loopback-only
publication. It preserves the standard networks, restart policies, data-volume
sharing, service privileges and runtime credential grants. No recipe is emitted
for execution. Infrastructure image references still originate in the standard
digest pins; their proposed image-ID replacement remains unverified until runtime.
`--image-identity ci-development` is reserved for qualification with
matching CI development images; it is not release approval.

## What follows

Installation and retained lifecycle commands are the next implementation milestone.
Their later acceptance checks must prove credential readability, read-only mounts,
paused schedules/pools, service health, authenticated access and persistence.
Public access, automatic forecasting, backup/restore acceptance and scientific
readiness require separate onboarding and activation work. Server secret-manager
automation and agency identity-provider integration are deferred. Implementation
history belongs in the
[installation plan](../plans/2026-11-20-persistent-profile-installation.md).
