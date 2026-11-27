# Install a private profile and leave it paused

The installation tool creates a **new** persistent stack with jobs paused and HTTP
access limited to the server. Preview first, then explicitly install. Retained
`verify`, `stop` and `start` commands use the private installation record.
A `PLAN_READY` result means nothing has been installed; `INSTALLED_PAUSED` means
service wiring passed, not that forecasting or backups are operational.

**Release selection:** use the latest canonical published release by default, or
an explicitly selected published version for reproduction. Resolve it once and
retain its exact source and delivered image identities for the whole installation.
Use the existing [release authority and delivery checks](../standards/cicd.md#release-identity-tags-and-receipts);
do not use an unreleased target on main or a floating Docker `latest` tag. An
automated read-only [selector](../standards/cicd.md#select-a-published-release-for-installation)
saves the version/source and explains their direct mapping to the installation
file. The installer uses those explicit inputs; it does not reselect a release.

[Hosted CI](../../tests/fixtures/deployment_installation/README.md) runs the
real-container install, retained lifecycle, failure and cleanup checks. Reuse the
successful source-CI evidence and normal delivery checks for the selected release;
a failed or skipped required check is not a pass. There is no separate manual
host-qualification project for every release. Repeat relevant deployment tests
when behavior, dependencies, host assumptions or a failure warrants it.

The [acceptance history](../plans/2026-11-20-persistent-profile-installation.md#pi4-releasehost-acceptance-complete)
records the exact release and host on which restart/reboot recovery was exercised.
It is evidence for that run, not a claim that later versions or other hosts were
tested. After a restart, allow services to recover and rerun `verify`; follow
[failure and recovery](#failure-and-recovery) if it keeps failing.

The supported scope remains the bounded new-install profile with durable local
credential files. Ephemeral secret-manager boot ordering, backups, public access,
scientific readiness and adoption or upgrade of an existing stack remain outside
its acceptance. Existing deployments keep their current runbooks.

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
file. Installation requires matching, already loaded default and
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
stacks. The directory retains private configuration and installation
records alongside the persistent database; it must not be a temporary report folder.
Its owner, permissions and host resource collisions remain runtime checks.

Runtime secret bindings may point to files that do not yet exist. Preview checks
their declarations and grants without opening them. Deliver all five credentials
according to the [secrets bootstrap](../secrets-bootstrap.md) before
installation. Neither a secret manager nor its login is required to preview.

| Result | Exit | Meaning / next action |
| --- | --- | --- |
| `PLAN_READY` | 0 | Supported offline configuration and finite recipe changes checked. Review proposed changes and unverified runtime facts; explicit installation is a separate action. |
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

## Install and inspect

Prepare the dedicated Linux amd64 host with rootful Docker Engine 28 or newer,
Compose, the project's installed Python/uv environment, and all matching images
already loaded. Image publication and admission remain separate reviewed steps;
this tool never builds or pulls. Use a local administrator account that can reach
`/var/run/docker.sock`, create `/run/lock/sapphire-flow-install.lock`, and own the
state directory. Use the same account for later commands. The host-wide lock
prevents concurrent installer operations; it does not coordinate manual Docker
commands. No other containers may be running at initial installation.

Deliver the five [credential files](../secrets-bootstrap.md). Keep the selected
paths available for the installation lifetime. Unset secret-value environment
fallbacks and remote Docker selectors. Create a stable, administrator-owned parent
for the state directory, without group/other write permissions. The state directory
itself must not exist. Reserve the chosen local HTTP port.

```bash
uv run python tools/deployment_install.py --profile-file /private/deployment/profile.toml install --installation /private/deployment/installation.toml --state-dir /private/installation-state --format json
uv run python tools/deployment_install.py verify --state-dir /private/installation-state --format json
```

Install checks credential format and actual reader access before creating persistent
resources. It freezes nonsecret inputs, pins image IDs, records daemon/resource
ownership, and inspects read-only delivery mounts before starting entrypoints.
It starts PostgreSQL and Prefect, creates paused pools, runs normal init once,
checks the database roles, pauses schedules, and then starts API, Caddy and workers.
All three pools and both registered jobs must remain paused with no execution.
Host loopback health must return 200 and the protected station collection 401.
An internal Caddy probe checks authenticated access using a temporary, empty-scope
consumer token, then revokes it and verifies rejection. No permanent token is issued.

Reports contain safe status/error codes and unverified facts, never credentials,
fingerprints or raw container output. Read-only `verify` checks the retained
recipe/resources, delivery values, service health and paused state. It does not
mint tokens, run init, change schedules or repair anything. On a stopped stack it
reports `STOPPED`, explicitly leaving live checks unperformed.

| Runtime result | Exit | Meaning |
| --- | --- | --- |
| `INSTALLED_PAUSED` | 0 | Install or explicit restart passed the paused service checks. |
| `VERIFIED_PAUSED` | 0 | Running retained stack passed read-only checks. |
| `STOPPED` | 0 | Owned containers stopped, or verify found a stopped stack; data retained. |
| `REFUSED` | 2 | Prerequisite, ownership, credential or paused-state mismatch; correct it with support. |
| `FAILED` | 1 | Runtime failure; inspect the containment result and retain the private state. |

## Stop and start without reinstalling

```bash
uv run python tools/deployment_install.py stop --state-dir /private/installation-state --format json
uv run python tools/deployment_install.py start --state-dir /private/installation-state --format json
```

These commands take only the existing state directory. Stop retains all volumes,
containers, private configuration and records; it works even when a credential
file is missing or changed. Start checks all five effective credential values
before starting services. It starts PostgreSQL, then Prefect, verifies persisted
pauses, then starts API/Caddy and workers. It never reruns init, migrations,
registration, Compose creation or a whole-project start. Changed original profile
files do not update this installation. There is no adoption, upgrade, rotation,
automatic resume or activation command.

Same-value credential redelivery is allowed. Changed values, missing/corrupt
fingerprints, missing containers or changed ownership refuse instead of resetting
the baseline. Back up the whole private state directory with database recovery
material. It includes private paths, frozen configuration and salted credential
fingerprints: keep its directory at 0700 and records at 0600, outside Git and public
reports. Do not move it or edit its records to bypass checks. It is not a vault;
strong credentials and their independent recovery copies are still required.

Standard `unless-stopped` policies are retained. Automatic daemon/host restart does
not run the explicit start prechecks: it relies on persistent Prefect pauses and
credential files already being available. Ephemeral manager deliveries need
separate boot-order verification; the qualified durable-file setup is described
under Qualification status above.

## Failure and recovery

Operations have a 15-minute runtime deadline and a separate two-minute containment
reserve. A startup failure or catchable interruption attempts to stop owned workers
first and PostgreSQL last. `containment: stopped_data_retained` confirms that stop;
`incomplete` requires prompt administrator inspection. An uncatchable kill cannot
guarantee containment. The installer never removes persistent volumes, prunes the
daemon, downgrades a schema or restores automatically.

Keep the safe JSON result, stage/phase and containment flag in private
`installation.json`, all data,
and the complete state directory. Run the guarded `stop` command first if a failed
attempt might still be running. A failed initial install remains a failed attempt
even after stop; repeated install and start refuse. Ask deployment support to inspect
the retained resources before deciding recovery. Do not delete the database to retry.
Temporary probe cleanup failures retain a private `sapphire-install-preflight-*`
recovery directory in the host temporary directory (restart probe records remain in installation
state); support must inspect the
`probes-*.json`, daemon identity and exact probe labels before removing probe containers.

If installation state is lost, there is no automatic adoption. With deployment
support, inspect the local Docker daemon ID and the exact containers' labels,
resource IDs and Compose project. Compare them against an independent private
backup of the installation record. Both `org.sapphire.installation` and
`com.docker.compose.project` must match on every resource. Re-inspect those IDs
immediately before individually stopping workers, API/Caddy, init if running,
Prefect and finally PostgreSQL. Do not use name-prefix deletion or project-wide
cleanup. If ownership cannot be established, stop and investigate; do not guess.
Preserve volumes and consult the [backup/restore procedures](../standards/cicd.md).

## Handoff for onboarding

Backups are registered but paused. This is infrastructure readiness only. Follow
the existing [station onboarding guide](../operations/new-station-by-hand.md),
[model artifact instructions](../operations/model-artifact-import-runbook.md) and
[authentication instructions](../standards/security.md) for their separate steps.
Before operational activation, settle real sources/models, controlled forecasting,
backup destination/retention and a restore test, access/TLS, and which jobs to enable.
Enabling jobs moves outside this tool's paused verification contract.
Server secret-manager automation and agency identity-provider integration remain
separate work. Implementation history belongs in the
[installation plan](../plans/2026-11-20-persistent-profile-installation.md).
