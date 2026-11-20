# Rehearse a private profile on a disposable test daemon

This tool starts a fresh, isolated copy of the supported standard stack, checks
initialization and internal authenticated API access, then removes its resources.
It never adopts or updates an existing deployment. Success means startup and
cleanup worked; it proves no forecast, scientific readiness, real credentials,
host-port access, DNS/TLS, backup/restore or persistent deployment readiness.

Start with the [administrator quick-start](../deployment-quickstart.md) and obtain
`PASS_OFFLINE` using the [profile checker](profiles.md#supported-offline-path).
Use the documented project environment and Compose renderer. Preview needs no
Docker daemon. Runtime needs a **dedicated test daemon** on
`unix:///var/run/docker.sock`, both prepared application images and the recipe's
pinned infrastructure images already loaded. The launcher never builds or pulls.
Internal networks and targeted cleanup do not isolate host CPU, memory or disk.

## Select and preview

```sh
uv run python tools/deployment_rehearsal.py --profile-file /private/deployment/profile.toml preview --installation /private/deployment/installation.toml
uv run python tools/deployment_rehearsal.py --profile-file /private/deployment/profile.toml preview --installation /private/deployment/installation.toml --format json
uv run python tools/deployment_rehearsal.py --help
```

Use a clean checkout matching the installation's project directory and full
`SAPPHIRE_SOURCE_REVISION`. With the default `release-target` identity,
`VERSION` and `SAPPHIRE_RELEASE_VERSION` equal `RELEASE_TARGET`. Both default and
Aquacast images must match that source/version. Preview checks the declarations;
runtime probes the prepared images and then uses immutable IDs. Prepared release
evidence remains authoritative; probing is not release attestation.

Developers may explicitly select `--image-identity ci-development` with
`SAPPHIRE_RELEASE_VERSION=0.dev0+g<full checkout HEAD>`, the same source revision
and a Docker-safe `VERSION` tag. This is development evidence, not release
qualification. There is no arbitrary expected-version or force override.

Only the standard stored-data topology with exactly forecast and backup jobs is
supported. Declare one writable tenant matching onboarding, without global admin,
exceptional scopes or local station/rating references. Use standard named data
volumes, without external resources or custom names. Application configuration
must be literal, without `${...}` substitution. The
[profile reference](profiles.md) owns the schema, defaults and precedence rules.

Keep the five runtime secret bindings as references. Their delivery files are
never read, overwritten or deleted. A rehearsal generates independent test
credentials in a private workspace. Do not use a delivery file or `.env` as a
nonsecret input, including through aliases. Unset Docker endpoint/context/TLS
overrides and conflicting profile selectors for these commands.

`PLAN_READY`/0 is a complete preview, not an image/host check or launch approval.
`FAIL`/1 means invalid selection, identity mismatch, protected input or detected
drift. `PENDING`/2 means unavailable evidence or an unsupported selection; dirty
or mismatched source checkouts and Docker context overrides are pending. Follow
the fixed diagnostic action and rerun after stabilizing the inputs. JSON omits
private labels/configuration and never emits an executable recipe.

## Run and recover

On the dedicated test daemon, use a new absolute report directory outside the
checkout. Its parent must exist; the tool creates the directory with mode 0700.

```sh
uv run python tools/deployment_rehearsal.py --profile-file /private/deployment/profile.toml run --installation /private/deployment/installation.toml --report-dir /private/rehearsals/run-001
uv run python tools/deployment_rehearsal.py cleanup --report-dir /private/rehearsals/run-001
```

Runtime repeats validation and freezes exact nonsecret file bytes and executable
bits. Snapshot copies gain read bits for container UIDs inside the private 0700
workspace; original file permissions are unchanged. It replaces names, credentials,
volumes and images; snapshots bind read-only
without implicit host-directory creation. It removes builds and published ports,
makes both networks internal, sets Caddy to HTTP `:80` and disables restarts.
Commands, privileges, health checks, configuration order and data sharing remain
unchanged. Selected domain/CORS choices remain untested desired settings.

The sequence is fresh PostgreSQL and Prefect health, verified paused pools before
normal init, migration/tenant/role checks, exactly two jobs with paused schedules,
worker heartbeats, then Caddy/API health and unauthenticated 401/authenticated 200
with an empty station collection. The short-lived tenant consumer token stays in
one API process. Scheduled/Pending records may exist; any actual flow execution
fails the rehearsal. No external network is attached to repair startup failures.

Each run allows fifteen minutes plus a separate two-minute cleanup reserve.
Success is `PASS_REHEARSAL`/0 only after cleanup. Preflight refusal exits 2;
runtime, assertion or cleanup failure exits 1. `forecast_executed` is false only
with confirmed no-execution evidence, true if execution was detected, otherwise
unknown. Reports distinguish completed checks from unverified outcomes.

Cleanup rechecks daemon identity and both ownership labels before each removal.
It discovers stopped containers and interrupted probes, never prunes images and
never runs broad Compose teardown. It works without the original profile or
credentials and is idempotent. Credentials/config snapshots are erased after a
cleanup attempt even if Docker cleanup fails. `cleanup_incomplete` keeps the run
failed and preserves its credential-free recovery record; rerun the cleanup
command on the same daemon. A changed daemon or foreign resource refuses removal.
An uncatchable kill or host restart is not a cleanup guarantee: retain the private
report directory and run recovery after the daemon returns. Do not edit ownership
records to bypass a refusal.

Only the allowlisted `summary.json` may be shared after review. Never upload raw
logs, recipe/configuration snapshots, generated credentials or recovery records.
The runtime summary contains image/source IDs and explicit health/auth/worker and
cleanup evidence, without private tenant names or connection strings.

## Qualification

The [synthetic runtime fixture](../../tests/fixtures/deployment_rehearsal/README.md)
requires explicit dedicated-daemon opt-in. Hosted CI builds both development image
variants, preloads infrastructure separately, runs the actual CLI twice and then
injects an owned-init failure. An unrelated sentinel must remain unchanged and
all test-owned resources must be removed. Missing private build access is reported
as qualification **not performed**, never a pass. Hosted development evidence is
separate from later disposable Linux-provider/release qualification.

The existing [synthetic forecast smoke test](hello-world.md) remains a separate
gate. This rehearsal runs no forecast and does not replace it.

## Runtime diagnostic guide

Text refusals list every fixed code and action. JSON preserves `failure_code` and
`cleanup_cause` separately when cleanup also fails; no private command output is
retained. Failed subprocesses add fixed operation labels without arguments or
private paths. Init waits within the original startup deadline, without extending it.
SIGINT, SIGTERM and SIGHUP (where available) attempt the same guarded cleanup.

| Code | Next action |
| --- | --- |
| `image_identity_invalid`, `infrastructure_image_unpinned` | Prepare the exact source-bound application images and checked pinned infrastructure. |
| `postgres_platform_mismatch` | Prepare the required Linux amd64 Postgres image. |
| `resource_collision` | Use a fresh run; never adopt existing objects. |
| `compose_unavailable`, `docker_unavailable` | Check local client prerequisites and the dedicated daemon. |
| `init_failed`, `command_failed`, `isolated_startup_failed` | Check the reported stage, bootstrap inputs and image compatibility. |
| `command_timeout` | Check runner capacity before retrying within the startup budget. |
| `cleanup_incomplete` | Resolve the separate cleanup cause, then use the saved recovery command. |
| `daemon_changed`, `foreign_resource_refused` | Reconnect the original daemon or inspect changed ownership manually; do not bypass guards. |
| `unexpected_failure` | Share only the safe summary with a developer. |
