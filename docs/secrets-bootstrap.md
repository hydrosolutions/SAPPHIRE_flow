# Deliver deployment credentials

The standard Compose stack consumes five runtime credentials as files. Keep their
values outside all Git repositories, including private configuration repositories.
Any secret manager or administrator can deliver them; the application does not log
in to a vault. Installing a desktop password manager does not configure server
delivery or reboot recovery.

## Required runtime files

| Logical name | Purpose | Mounted into |
| --- | --- | --- |
| `db_password` | Database owner; migrations and orchestration database access | `postgres`, `prefect-server`, `init` |
| `sapphire_api_db_password` | Least-privilege API database role | `api`, `init` |
| `sapphire_worker_db_password` | Forecast and ingest database role | `prefect-worker`, `prefect-worker-ingest`, `init` |
| `sapphire_backup_db_password` | Dedicated backup database role | `prefect-worker-backup`, `init` |
| `access_token_pepper` | Protect stored API access-token hashes | `api` |

Generate each database password independently. A practical format for new
deliveries is 64 random hexadecimal characters (32 random bytes), generated in
your manager. This avoids the current entrypoint's unescaped URL/substitution
limitations. The persistent installer accepts ASCII letters, digits and `-._~`,
with an optional final newline; files are bounded to 4096 bytes. Other password
characters need a separate encoding fix.
Do not change an existing password just to match these new-install instructions.

Without a manager, an administrator can generate one **new** delivery as below,
after creating its private parent directory. Substitute the intended path;
`set -C` refuses an existing file. Then apply the delivery permissions below.
Repeat independently for each logical credential; never reuse one value.

```bash
(umask 077; set -C; openssl rand -hex 32 > /private/credential-delivery/db_password)
```

The pepper must be nonempty after the API reader strips surrounding whitespace.
Use an independently generated high-entropy value; 64 random hexadecimal characters
also work. Changing its effective value invalidates existing API tokens.

## Delivery and access

1. Select a private directory outside the checkout, owned by the administrator,
   with mode `0700`. It must remain available across restarts.
2. Deliver each value into its own regular file. For this rootful Linux Compose
   setup, mode `0444` inside the private directory permits container readers while
   the parent restricts access on the host. Set permissions deliberately on new
   deliveries; do not run recursive ownership changes over an existing vault.
3. Reference the paths through the installation's `secret_bindings` and matching
   Compose `secrets` definitions. See the
   [profile reference](deployment/profiles.md). The base Compose file's relative
   `./secrets/` paths remain available for existing runbooks; they are not a reason
   to put credential values in the checkout.
4. Preserve read-only mounts and existing service grants. Do not mount all
   credentials into every service or expose values through Compose environment
   variables, command arguments, reports or shell tracing.

The application entrypoint reads its DB password as root and then switches to
`app` (UID/GID 1000). Init reads scoped-role files, and API reads the pepper, as
that non-root user. Backup also needs its delivery readable as UID 1000. Prefect's
startup reads the owner file as root; Postgres uses its upstream file reader.
Verify effective readers against the images you deploy. The entrypoint's attempted
`chown` cannot fix a read-only secret mount: deliver usable permissions first.
Standalone Compose file secrets are mounts, not an encrypted host credential store.

A manager may deliver via a symlink to a stable regular file. The selected resolved
target must remain available; changing the alias does not automatically switch an
installation's mounts. Atomic replacement can also leave existing containers
bound to the previous file. Coordinate delivery changes with the deployment
lifecycle instead of assuming a running process reloads credentials.

The [persistent installation preview](deployment/profile-installation.md) does
**not** open delivery files, check their permissions, create fingerprints or start
containers. Runtime readability, content checks, unchanged-original verification
and private salted change detection run during explicit installation. Start checks
all five effective values before starting any service; same-value redelivery is
allowed, rotation is separate. Treat the private fingerprint record and its backups
as sensitive; they contain no raw credentials but permit guesses against weak ones.
Stop remains available when deliveries are missing or changed. Ephemeral
deliveries additionally need verified boot ordering before unattended restarts.

## Other credentials and authentication

Build credentials (`recap_dg_client_token` and `aquacast_token`) belong to the image
build process; they are not runtime requirements for prepared images. Optional
provider/operator overlays introduce their own declared credentials and remain
outside the first persistent installation scope.

The current standard stack does not consume the previously proposed `secret_key`
or `totp_encryption_key` files. Staff identity-provider integration and API consumer
tokens are separate from these database passwords. Follow the
[security standard](standards/security.md) for implemented authentication and token
bootstrap. Server secret-manager automation and agency login integration are later
work, not prerequisites for preview.

## Rotation and recovery

Replacing a delivery file alone is not password rotation: the database and its
consumers must change together. Keep the authoritative values and recovery access
in protected storage, and back up deliveries according to your manager's policy.
Database backups do not replace credential recovery. Lost credentials require
administrator recovery; do not delete application data to regain access.

For existing installations, use the coordinated
[DB-role procedures](standards/cicd.md#db-role-bootstrap-plan-147-slice-d-realized)
and [pepper procedure](standards/cicd.md#pepper-rotation-all-token-reissue--v10-has-no-dual-pepper-support).
These new-install delivery recommendations do not authorize changes to a live
stack, existing secret files or running mounts.
