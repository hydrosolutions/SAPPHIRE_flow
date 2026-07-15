# Security Standards

> This document extends `docs/architecture-context.md`. It adds implementation detail for security concerns. For foundational decisions, see: access roles (architecture-context.md § Access management), DB service users (conventions.md § Database connection patterns), API routes (conventions.md § API conventions). This document does not redefine roles, DB permissions, or API route patterns.

## Authentication (v1)

v0 defers auth — single-user, no access control. Everything below applies from v1.

### Session-based authentication (human users)

- OAuth2 password flow via FastAPI
- MFA: TOTP mandatory for all human roles (org admin, IT admin, model admin, forecaster). Enforced at login — no bypass.
- Access tokens: JWT, short-lived (30 min). Signed with HS256 using `SECRET_KEY`.
- Refresh tokens: opaque, 7-day expiry, stored hashed (SHA-256) in `refresh_tokens` table. HttpOnly, Secure, SameSite=Strict cookies — never in localStorage.
- Token refresh: POST /api/v1/auth/refresh. Issues new access token if refresh token is valid. Refresh token rotation: each use invalidates the old token and issues a new one.
- Concurrent sessions: deployment-configurable maximum active refresh tokens per user (`max_sessions_per_user`, default 5). When the limit is reached, the oldest active refresh token is revoked on new login. Supports multiple devices (desktop, phone) without unbounded token accumulation.
- Session invalidation: password change or account deactivation revokes all refresh tokens for that user. Active JWTs expire naturally (30 min maximum exposure window).
- Token cleanup: a scheduled Prefect task (daily, low priority) deletes expired and revoked refresh tokens older than 30 days.
- Logout: DELETE /api/v1/auth/session. Invalidates refresh token server-side.

### API key authentication (external consumers)

- Long-lived bearer tokens, scoped to read-only endpoints.
- Stored hashed (bcrypt) in `access_tokens` table. Plain-text token shown once at creation, never stored.
- Scoped per consumer: station list, parameter list, geographic boundary. Org admin configures scope.
- API keys cannot trigger flows, modify forecasts, or access audit logs.
- Rotation: org admin can regenerate; old key invalidated immediately.

### Endpoint classification

All state-changing routes (POST, PATCH, DELETE) require a session token — never an API key. API keys are GET-only.

## Initial deployment bootstrap

The authorization matrix requires an org admin to create users (`POST /api/v1/users`), but someone must create the first org admin. This section defines that bootstrap process.

### Prerequisites

The hydromet IT team deploys the stack independently, following the deployment guide. No involvement from the SAPPHIRE development team is required. The IT team must:

1. Provision a VM (Ubuntu, Docker, Caddy)
2. Create the `./secrets/` directory and generate all required secrets (see § Secrets management)
3. Run `docker compose up` and verify `GET /api/v1/health` returns OK

At this point the system is running with zero users.

### Seeding the first org admin

A one-time CLI command, run directly on the server via `docker compose exec`. This is the only path that bypasses the authentication system.

```
docker compose exec api python -m sapphire_flow.cli create-admin \
    --username "<email>" \
    --name "<display name>"
```

The command:
1. Creates a user record with role `org_admin`
2. Generates a temporary password (printed to stdout, single use)
3. Generates a TOTP secret (displayed as QR code or base32 string for authenticator app)
4. Records the creation event in `audit_log`

On first login, the org admin must change the temporary password.

This command requires shell access to the production VM — equivalent to reading `/run/secrets/` directly. It is not a backdoor; it is a structured bootstrap that demands the same privilege level as direct database access.

### User onboarding (post-bootstrap)

After the first org admin exists, all subsequent user management goes through the API/dashboard:

| Action | Who | How |
|---|---|---|
| Create IT admin, model admin, forecaster accounts | Org admin | `POST /api/v1/users` via dashboard |
| Create API keys for external consumers | Org admin | `POST /api/v1/access-tokens` via dashboard |
| Unlock locked accounts | Org admin | Dashboard or API |
| Disable/remove users | Org admin | Dashboard or API |

Each new user receives a temporary password and TOTP setup instructions. The org admin never sees or sets the user's permanent password.

### VM hardening (IT team responsibility)

The following are infrastructure-level security measures — the IT team's responsibility, not the application's. The deployment guide documents them as recommendations. SAPPHIRE Flow does not implement, enforce, or verify any of these — they are outside the application boundary.

| Recommendation | Purpose | Priority |
|---|---|---|
| **SSH key-only authentication** | Disable password-based SSH. Primary attack surface for an on-prem VM. | Critical |
| **SSH IP allowlisting** | Restrict SSH access to known IP ranges (office network, VPN gateway). Example: `ufw allow from 192.168.x.0/24 to any port 22; ufw deny 22`. Ideally, SSH only via VPN — no direct SSH from the internet. | Critical |
| **fail2ban** | Blocks IPs after repeated failed SSH attempts. Complements IP allowlisting. | High |
| **`auditd`** | OS-level audit logging of SSH sessions, `sudo` usage, file access. Feeds into SIEM if available. | High |
| **Full disk encryption (LUKS)** | Protects against physical disk theft. Requires manual unlock or TPM on reboot. | High |
| **`./secrets/` file permissions** | `chmod 600`, owned by root. Prevents other OS users from reading secrets on the host. | High |
| **Firewall** | Only port 443 (HTTPS) and SSH open. All other ports blocked at the OS level (in addition to Docker network isolation). | Critical |
| **Unattended upgrades** | Automatic OS security patches. | High |
| **`pgaudit` extension** | PostgreSQL audit logging of all SQL queries. Detects direct database access that bypasses the application API. See "Threat model" section below. | Recommended |

Application-level encryption of secrets files (e.g., SOPS, age) is not used — the decryption key would need to be co-located on the same machine, adding complexity without meaningful security gain. Docker secrets (tmpfs, never on disk inside containers) is the application's security boundary.

### Operational independence

The hydromet IT team operates the system without the SAPPHIRE development team. The deployment guide covers:
- Stack deployment and upgrades
- VM hardening (see above)
- First admin creation (CLI)
- Backup verification and restore procedures
- Secret rotation
- Monitoring and alerting setup

The org admin (a hydromet staff member) manages all user accounts through the dashboard. No CLI access is needed after the initial bootstrap.

## Authorization matrix

> **v1-only**: The entire authorization matrix applies from v1. v0 has no authentication or authorization.

Role-to-endpoint mapping. Enforced via FastAPI dependency injection (`Depends(require_role(...))`), not frontend visibility.

| Endpoint pattern | Org admin | IT admin | Model admin | Forecaster | API consumer |
|---|---|---|---|---|---|
| `GET /api/v1/stations` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /api/v1/stations/{id}/forecasts` (published) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /api/v1/stations/{id}/forecasts` (all statuses) | ✓ | ✓ | ✓ | ✓ | — |
| `GET /api/v1/stations/{id}/observations` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /api/v1/alerts` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `POST /api/v1/forecasts/{id}/adjust` | — | — | — | ✓ | — |
| `PATCH /api/v1/forecasts/{id}/status` | — | — | — | ✓ | — |
| `POST /api/v1/alerts/{id}/acknowledge` | — | — | ✓ | ✓ | — |
| `POST /api/v1/flows/ingest/trigger` | — | ✓ | ✓ | — | — |
| `POST /api/v1/flows/train/trigger` | — | — | ✓ | — | — |
| `PATCH /api/v1/model-artifacts/{id}/status` | — | — | ✓ | — | — |
| `GET /api/v1/health` (public) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /api/v1/health/detail` | ✓ | ✓ | — | — | — |
| `POST /api/v1/users` | ✓ | — | — | — | — |
| `GET /api/v1/users` | ✓ | — | — | — | — |
| `PATCH /api/v1/users/{id}` | ✓ | — | — | — | — |
| `POST /api/v1/access-tokens` | ✓ | — | — | — | — |
| `GET /api/v1/access-tokens` | ✓ | — | — | — | — |
| `DELETE /api/v1/access-tokens/{id}` | ✓ | — | — | — | — |
| `POST /api/v1/access-tokens/{id}/regenerate` | ✓ | — | — | — | — |
| `PATCH /api/v1/users/me/password` | ✓ | ✓ | ✓ | ✓ | — |

**API consumer scope filtering**: A `✓` for an API consumer means the endpoint is accessible, not that the consumer sees all data. Responses are filtered server-side by the token's `scope` (see `access_tokens.scope` in architecture-context.md § Authentication schemas). A consumer scoped to specific stations receives only those stations from `GET /api/v1/stations`, only their forecasts, observations, and alerts. Requests for out-of-scope station IDs return 404. Human roles (org admin through forecaster) are unscoped — they see all data.

## Secrets management

Email and SMS notifications are out of scope through v1 (see `docs/handover/data-flows.md`). Alert consumers can poll the API; outbound delivery on the SAPPHIRE side is webhook-only.

### Production (Docker Compose)

All secrets use Docker secrets (`secrets:` block in `docker-compose.yml`). Mounted as files at `/run/secrets/` and read at startup. Application code reads secrets from file paths, never from environment variables in production.

Required secrets:
- `db_password` — PostgreSQL password for application users
- `secret_key` — JWT signing key (read from `/run/secrets/secret_key`, referenced as `SECRET_KEY` in application config) *(v1)*
- `totp_encryption_key` — Fernet key for encrypting TOTP seeds at rest (see § TOTP secret encryption) *(v1)*
- `sapphire_dg_api_key` — Data Gateway API key (v1)
- `backup_repo_password` — restic repository password *(v1)*

### Development

Secrets are stored outside the repository at `~/.config/sapphire-flow/secrets/`. A gitignored symlink in the repo root lets Docker Compose resolve `./secrets/` transparently:

```bash
mkdir -p ~/.config/sapphire-flow/secrets
openssl rand -base64 24 > ~/.config/sapphire-flow/secrets/db_password
ln -s ~/.config/sapphire-flow/secrets secrets
```

This preserves the same file-based secrets path as production — no `.env`-based divergence. The symlink is gitignored (`secrets/` in `.gitignore`), so neither the symlink nor the secret values can be committed.

Alternatively, `.env` files can supply secrets as environment variables for local development. `.env` is in `.gitignore` — CI fails if `.env` is committed. Environment variable names match conventions.md § Environment variables.

### Rotation

- `secret_key`: rotated annually and after any suspected compromise. Rotation procedure: generate new key, deploy, old JWTs expire naturally (30 min).
- API keys: rotated per consumer's request or when compromise is suspected. Org admin regenerates via dashboard.
- `db_password`: rotated annually. Requires coordinated restart of all application containers.
- `totp_encryption_key`: rotated rarely (requires re-encrypting all `users.totp_secret` values). Rotation procedure: generate new key, run migration script to decrypt-with-old / encrypt-with-new, deploy new key, verify TOTP login works.
- External API keys (`sapphire_dg_api_key`): rotated per provider schedule.

> **v1-only**: TOTP/MFA is deferred to v1. This section applies from v1 onwards.

### TOTP secret encryption at rest

TOTP seeds (`users.totp_secret`) are encrypted at rest using Fernet symmetric encryption (`cryptography` library, AES-128-CBC + HMAC-SHA256). The encryption key is a dedicated Docker secret (`totp_encryption_key`), separate from the JWT signing key (`secret_key`).

**Why a dedicated key**: Compromising `secret_key` (e.g., via a leaked JWT or log exposure) allows JWT forgery but does not expose TOTP seeds. An attacker with SQL-level access (SQL injection, compromised read-only DB user) can read `totp_secret` column values but cannot decrypt them without filesystem access to `/run/secrets/totp_encryption_key`. This preserves MFA as a second factor even when the database is partially compromised.

**Encryption flow**:
- On user creation: generate TOTP seed → display to user (QR code / base32) → encrypt with `totp_encryption_key` → store ciphertext in `users.totp_secret`.
- On login TOTP verification: read ciphertext from DB → decrypt with `totp_encryption_key` → verify TOTP code → discard plaintext.

**Key generation**: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Store the output in `./secrets/totp_encryption_key`.

**Limitation**: An attacker with root access to the host VM can read both the DB and `/run/secrets/totp_encryption_key`, defeating this protection. This is consistent with the threat model (§ Threat model: host compromise) — application-level encryption protects against DB-level compromise, not host-level compromise.

> **v1-only**: API key management is deferred to v1 (no auth in v0).

## API key lifecycle management

### Dashboard view

The org admin dashboard includes an API key management page showing all tokens (active and revoked). Columns:

| Column | Source |
|---|---|
| Consumer name | `access_tokens.consumer_name` |
| Created | `access_tokens.created_at` |
| Last used | `access_tokens.last_used_at` |
| Requests (30d) | Count from `audit_log` WHERE `event_type = 'api_key_request'` AND `target_id = token.id` AND `created_at > now() - 30d` |
| Scope summary | `access_tokens.scope` (stations, parameters, boundary) |
| Status | Active / Revoked / Inactive (never used or unused >90 days) |

Available actions: revoke, regenerate (rotate), edit scope.

### Usage tracking

`access_tokens.last_used_at` is updated by the API middleware on each authenticated request. This is a single-column UPDATE — lightweight and sufficient for the dashboard view. Historical usage counts are derived from `audit_log` (event type `api_key_request`), which already records every authenticated request.

### Automated alerts

A scheduled Prefect task (daily, low priority) checks API key health and records administrative alerts for the org admin via the webhook notification channel and/or `pipeline_health`. Alert triggers:

| Trigger | Condition | Action |
|---|---|---|
| Unused key | `last_used_at` is NULL or >90 days ago | Webhook / pipeline_health: "API key for *{consumer}* has not been used in 90 days. Review?" |
| Key age | `created_at` >1 year ago AND not regenerated | Webhook / pipeline_health: "API key for *{consumer}* is over 1 year old. Consider rotation." |
| Usage spike | Requests in last 24h >10x the 30-day daily average | Webhook / pipeline_health: "API key for *{consumer}* made {n} requests today (normal: ~{avg}/day)." |

These use the existing notification infrastructure (webhook channel, notification adapters). See architecture-context.md § Notification channels → Alert categories.

> **v1-only** (v0-scope.md §A10): v0 uses simple pg_dump backups. restic encryption is deferred to v1.

## Backup encryption

Handled by `restic` — encrypts all backup data at rest with AES-256-CTR. The repository password (`backup_repo_password`) is available on the VM at runtime as a Docker secret (mounted in-memory via tmpfs, never on disk inside containers) — restic needs it for every backup and restore operation.

A **recovery copy** of the password must be stored separately from the VM, so that backups can be decrypted if the VM is lost:
- Stored in the IT admin's password manager or printed and stored offline
- For Nepal: two copies — one with DHM IT admin, one with project team

See architecture-context.md § Backup and disaster recovery for backup contents and schedule.

## Rate limiting and brute-force protection

Implemented in Caddy (reverse proxy), not in FastAPI — blocks at the network edge before Python is involved.

### Rate limits

| Endpoint pattern | Limit | Scope |
|---|---|---|
| `POST /api/v1/auth/*` | 5 requests / 15 min | Per IP |
| `GET /api/v1/health` (public, unauthenticated) | 60 requests / min | Per IP |
| `GET /api/v1/*/export` (CSV) | 10 requests / min | Per API key |
| All other authenticated `GET` | 120 requests / min | Per API key or session |
| All `POST/PATCH` | 30 requests / min | Per session |

Exceeded requests receive HTTP 429 with `Retry-After` header. Unauthenticated requests to non-public endpoints are rejected with 401 before rate limit evaluation. Rate limits are documented in the API reference (see API documentation) so consumers can implement backoff.

### Account lockout

- 5 consecutive failed login attempts → account locked for 15 minutes
- 10 consecutive failures → account locked until org admin unlocks
- All failed login attempts logged to `audit_log` with IP address and timestamp

## CORS policy

`allow_origins` is an explicit list — never `*` in production. Configured in `config.toml` under `[api.cors]`:

> **v0 exception** (v0-scope.md §J): v0 has no auth, no dashboard, and no sensitive consumers. `docker-compose.yml` defaults to `SAPPHIRE_CORS_ORIGINS=*` for development convenience. Operators deploying on a network with untrusted clients must override this via `.env`. Restrict to an explicit origin list before enabling auth (v1).

Production (v1+) `allow_origins` must include:
- Dashboard origin (same host)
- Registered origins of known API consumers (Bipad portal, DHM dashboard)
- API consumer endpoints using bearer token auth may use `allow_origins = ["*"]` only if explicitly configured per deployment

### CSRF protection

Explicit CSRF tokens are not used. The combination of existing controls is sufficient:

1. **SameSite=Strict cookies**: refresh tokens are never sent on cross-origin requests, so a malicious site cannot trigger authenticated state-changing requests.
2. **CORS policy**: explicit `allow_origins` list prevents cross-origin `XMLHttpRequest` / `fetch` (which HTMX uses internally).
3. **JWT in Authorization header**: access tokens are sent as `Authorization: Bearer <token>`, not as cookies. Cross-origin requests cannot attach this header without CORS preflight approval.

The HTMX dashboard is same-origin — all `hx-post`/`hx-patch` requests go to the same host. A cross-origin attacker cannot forge these requests because the browser blocks both cookie attachment (SameSite) and header attachment (CORS).

## Container privilege model

All service containers:
- Use minimal base image (`python:3.11-slim`)
- Create a named non-root user in the Dockerfile (`RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m app`)
- Use an entrypoint script that starts as root, fixes permissions on mounted volumes and secrets, then drops to the `app` user via `gosu` before executing the application (see "Entrypoint pattern" below)
- Drop all capabilities (`cap_drop: [ALL]` in `docker-compose.yml`)
- Read-only root filesystem where possible (`read_only: true`), with explicit `tmpfs` for writable paths
- Docker socket is never mounted in application containers

### Capabilities

All service containers start from `cap_drop: [ALL]` and add back only the narrow capabilities each service needs. The entrypoint pattern (see below) starts as root, fixes permissions on mounted volumes / secrets, then `gosu app`-drops to UID 1000 — the few `cap_add` entries below are what keeps that boot sequence functional while preserving the least-privilege invariant.

Accepted per-service `cap_add` set (see `docker-compose.yml`):

| Capability | Services | Justification |
|---|---|---|
| `SETUID` | postgres, prefect-worker, api, init | Required for the entrypoint's `gosu app` user drop. Without SETUID the process cannot change UID even from root. |
| `SETGID` | postgres, prefect-worker, api, init | Same rationale — `gosu` sets both UID and GID. |
| `CHOWN` | postgres, prefect-worker, api, init | Required to `chown app:app` named-volume mount points at first boot. Named volumes are root-owned by Docker; without CHOWN the entrypoint cannot transfer ownership to the non-root `app` user. `init` technically mounts only `config.toml` today, but keeps CHOWN for service-set parity and forward-proofing. |
| `FOWNER` | postgres, prefect-worker, api, init | Once volumes accumulate state, the entrypoint's `chown` must succeed over files with arbitrary non-root owners — FOWNER lets root perform chmod/chown irrespective of file UID. Pairs with CHOWN. |
| `DAC_OVERRIDE` | postgres | Postgres-specific: allows bypassing file read/write/execute permission checks during init-db bootstrapping (pre-existing, inherited from the upstream `postgres` image). |
| `NET_BIND_SERVICE` | caddy | Lets a non-root caddy process bind ports 80/443 (privileged ports < 1024). |

All other capabilities remain dropped. Any new `cap_add` entry requires a justification row here plus a corresponding cross-reference from the service's `docker-compose.yml` block.

Documentation landed via Plan 060 (`docs/plans/archive/060-a3-prefect-deployment-compat-sweep.md`). The `CHOWN` + `FOWNER` additions themselves landed via commit `289c5f8` (Plan 058 scope-creep into infra).

### Entrypoint pattern

Containers start as root only during the entrypoint, then drop privileges before running the application. This is necessary because Docker Compose secrets `uid`/`gid`/`mode` options only work in Swarm mode — they are silently ignored in standalone Compose ([compose#9648](https://github.com/docker/compose/issues/9648), [compose#13287](https://github.com/docker/compose/issues/13287)). The actual mount mode may be `0400` (root-only) instead of the documented `0444`, breaking non-root access.

```dockerfile
# Dockerfile
FROM python:3.11-slim
RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m app
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*
COPY --chown=app:app . /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "sapphire_flow"]
```

```bash
#!/bin/sh
# entrypoint.sh — runs as root, then drops to app user
set -e
chown -R app:app /run/secrets 2>/dev/null || true
chown app:app /data/nwp_grids 2>/dev/null || true
mkdir -p /tmp/sapphire_nwp && chown app:app /tmp/sapphire_nwp
exec gosu app "$@"
```

The application process never runs as root. The `gosu` exec replaces the entrypoint process entirely — no root process remains.

### Cross-platform UID/GID compatibility (Mac development, Linux deployment)

Docker Desktop for Mac runs containers in a Linux VM with a VirtioFS translation layer that automatically remaps file ownership. This masks UID/GID mismatch problems that will surface on Linux deployment. Known issues: [docker/for-mac#6243](https://github.com/docker/for-mac/issues/6243), [#6812](https://github.com/docker/for-mac/issues/6812), [#7415](https://github.com/docker/for-mac/issues/7415).

Rules to ensure Mac/Linux consistency:
- **Hardcode UID 1000:1000 in the Dockerfile** — do not use runtime `user:` overrides in `docker-compose.yml`
- **Create a named user** (`app`), not just a numeric UID — some tools require an entry in `/etc/passwd`
- **Use named volumes** (not bind mounts) for persistent data to avoid host UID conflicts. Exception: dev-only overlays may bind-mount read-only static reference datasets (e.g. CAMELS-CH via `CAMELS_CH_HOST_DIR`) — the `:ro` mode sidesteps UID-write collisions. Production and staging overlays use named volumes or controlled host paths per the Mac-mini runbook.
- **Use the entrypoint pattern** above to fix permissions at startup, regardless of how Docker mounted them
- **Do not use** `uid`/`gid`/`mode` in Docker Compose secrets definitions — they only work in Swarm mode
- **Test in Linux CI** (GitHub Actions) — Mac development will hide permission bugs

### Docker secrets access

The entrypoint pattern above handles secrets access: `chown` makes `/run/secrets/` readable by the `app` user before the application starts. This is more robust than relying on Docker's default `0444` mode, which has known bugs in standalone Compose.

### Volume permissions

- `/data/artifacts/` — read-only for `api` container, read-write for `prefect-worker-training` only; read-only for `prefect-worker-ops` and `prefect-worker-hindcast`
- `/data/nwp_grids/` — read-write for `prefect-worker` (v0); not mounted in `api` container (no direct NWP grid access via API)
- `/data/cold/` — read-only for `api` container, read-write for `prefect-worker-ops` (archival task) *(v1, §A2)*; read-only for `prefect-worker-hindcast`

### Upstream images running as root

`prefect-server` uses `prefecthq/prefect:3-python3.11`, which has no `USER` directive (verified 2026-04-20 via `docker run --rm prefecthq/prefect:3-python3.11 id` → `uid=0(root)`). The Prefect project ships no non-root image tag.

Compensating controls already in place: `cap_drop: [ALL]`, no host port binding in the base compose file, `backend`-only network after Plan 049 C1.

Do **not** add a `user:` override in `docker-compose.yml` — this is forbidden by the rule above. Re-evaluate after Plan 062 establishes the full write-path footprint (currently unknown because `PREFECT_HOME` is not set), or if a `-nonroot` upstream tag or community non-root image appears.

## Supply chain

This section is the **canonical policy source** for supply-chain controls across third-party inputs that ship into the built image or run in CI. `docs/standards/cicd.md` summarises the operational workflow and points back here rather than duplicating the policy surface.

The goal is *attributable* risk — when a CVE lands or a build breaks, `git log` should answer "what changed and when?" for every high-leverage external input. The controls below pin what we can pin by immutable identifier, scan what we cannot, and document the residual risk where live feeds remain unavoidable.

### Python dependency policy

- `uv.lock` is committed. Resolver output is reproducible; `uv sync --frozen` is used in every CI workflow step that installs dependencies.
- `pyproject.toml` declares `[tool.uv] required-version = "==0.11.7"` so local `uv` binaries that drift from the repo-standard version fail fast rather than silently re-resolving.
- `pyproject.toml` also declares an explicit `[[tool.uv.index]]` block naming PyPI as the default index. This is informational / future-proofing — not a restrictive control — ahead of any private-index introduction.
- Dependabot raises reviewed upgrade PRs across four ecosystems: `uv` (Python deps), `docker` (Dockerfile base images and `COPY --from=` stages), `docker-compose` (compose services), and `github-actions` (workflow `uses:` entries). Configuration lives at `.github/dependabot.yml`.

### Wheel-only dependency-update guard

A dedicated CI job runs a two-step install sequence. Step 1 = "the wheel-only guard": `uv sync --frozen --no-build --no-cache --no-install-project --no-install-package forecastinterface`. This makes GitHub-hosted CI the first execution environment for dependency-update installs and fails if any non-excepted package version requires Python build-backend / sdist execution on the CI platform.

Step 2 = "post-guard temporary forecastinterface exception install": `uv sync --frozen --no-cache --no-install-project --reinstall-package forecastinterface`. This step is allowed to build only `forecastinterface`, after the wheel-only guard has already checked every other locked dependency.

`--no-install-project` is required because the SAPPHIRE Flow root package is editable-only and has no published wheel — the project itself is never installed from an index. This does **not** weaken the guard: the third-party dependencies (the real concern for install-time code execution) are still exercised end-to-end.

**Documented source-build exceptions**: Plan 079 allows the temporary `forecastinterface` source build because it is first-party, public, pure Python, git-pinned to `v0.1.17` / `303aa422...`, and outside Plan 064's untrusted native-build threat model. The removal trigger is exact: remove this exception once ForecastInterface is published as a versioned wheel to a hydrosolutions package index and SAPPHIRE Flow migrates from the git pin to `forecastinterface==0.1.x`. `exactextract` may require a source build on `linux/arm64` in the Dockerfile builder stage. GitHub-hosted amd64 CI stays wheel-only except for the post-guard temporary `forecastinterface` install. Any new source-build exception must be recorded in the implementation PR and in this section before merge.

### Image pinning

Every externally-pulled image is pinned by **manifest-list digest** (not per-platform digest — the manifest-list resolves per-arch on pull, so the same pin works for both amd64 CI and arm64 Mac mini):

- `Dockerfile` (builder + runtime stages): `python:3.11.12-slim@sha256:...` and `ghcr.io/astral-sh/uv:0.11.7@sha256:...`
- `docker-compose.yml`: `postgis/postgis:16-3.4@sha256:...`, `prefecthq/prefect:3-python3.11@sha256:...`, `caddy:2.9@sha256:...`
- `.github/workflows/ci.yml` integration-job `services.image`: `postgis/postgis:16-3.4@sha256:...` (must match the compose postgis digest to avoid silent integration/prod drift)

The locally-built `sapphire-flow:${VERSION}` image used by the `worker`, `api`, and `init` services is **not** digest-pinned — it is built in this repo, not pulled.

Dependabot's Docker-related ecosystems raise digest-update PRs under review.

### CI action pinning

Every `uses:` entry in `.github/workflows/*.yml` is pinned by commit SHA with the version tag preserved as a trailing comment:

```yaml
uses: actions/checkout@<sha>  # v4.2.2
```

This closes the `tj-actions/changed-files` (Mar 2025) class of attack: action tags are mutable, SHAs are not. Dependabot's `github-actions` ecosystem keeps these current.

### uv toolchain pin

The repo-standard `uv` version is `0.11.7`. It is declared in **three places that must stay synchronised**:

1. `[tool.uv] required-version = "==0.11.7"` in `pyproject.toml` (local guardrail).
2. `COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:... /uv /usr/local/bin/uv` in `Dockerfile` (both build stages).
3. `with: version: "0.11.7"` in every `astral-sh/setup-uv` step across `ci.yml` and `live-lindas-weekly.yml`.

Any `uv` bump must flow through all three; Dependabot's `docker` ecosystem raises the digest-update PR and the project-level `required-version` is updated in the same PR.

### CVE scanning layers

Two Trivy scans run in CI. Both fail the build on `HIGH,CRITICAL` with `--ignore-unfixed`:

- **`trivy fs`** in the lint tier — scans `uv.lock` and `pyproject.toml` without building the image. Fast feedback on dep-only PRs.
- **`trivy image`** post-build — scans the `sapphire-flow:ci-${{ github.sha }}` image produced by the CI build job. Catches OS-level CVEs (Debian + PGDG package layers) that `fs` mode cannot see.

Known-accepted CVEs live in `.trivyignore`. Every entry must carry a dated comment explaining why it is ignored and when to re-review — no undated entries.

### Dependency-bump safety gate (Plan 119)

Green CI is not a merge criterion for dependency bumps that change stateful
or environment-coupled behaviour. Dependabot PR #78
(`postgis/postgis:16-3.4 → 17-3.4`) passed every CI check and was one click
from merge — yet merging it would have taken staging down, because a
PostgreSQL **major** version bump cannot boot against an existing PG16 data
directory without a migration (see Plan 118). CI is structurally blind to
this class: it always starts from an empty database and never sees a
persistent volume.

**Layered controls:**

1. **Prevention — Dependabot `ignore:` rules.** `.github/dependabot.yml`
   carries per-package `version-update:semver-major` (and, for the Python
   base image, `semver-minor` too — see below) `ignore:` rules for every
   image in `docker-compose.yml` that holds a persistent named volume:
   `postgis/postgis`, `prefecthq/prefect`, `caddy`. The dangerous PR simply
   never opens. Re-enabling an ignore rule is a deliberate, auditable
   commit (documented inline in `dependabot.yml`), not silent drift —
   e.g. the postgis rule is re-enabled together with the Plan 118 migration.
2. **Classifier — the `dependency-safety` job** (`.github/workflows/dependency-safety.yml`,
   logic in `tools/dependency_safety.py`). Prevention only binds
   Dependabot; a human hand-editing `docker-compose.yml`, `Dockerfile`, or
   `pyproject.toml` bypasses it entirely. The classifier is the
   defense-in-depth backstop for exactly those manual edits, plus the
   residual field (`requires-python`) Dependabot never touches at all. It
   triggers **unconditionally** on every `pull_request` (no `paths:`
   filter — see `cicd.md` § `dependency-safety.yml` for why) and diffs a
   fixed watched-file set against the PR base SHA:
   `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `uv.lock`,
   `.github/workflows/ci.yml`.

   **BLOCK** (job fails, with an actionable message pointing at the fix —
   e.g. Plan 118 for a postgis bump):
   - a **stateful-service image** major bump in `docker-compose.yml` —
     detected generically as any `image:` with a `volumes:` mount whose
     parsed version increased (no hardcoded per-image list);
   - a **Dockerfile base-image** change — for CPython's `X.Y.Z` tag scheme
     the risk axis is the **minor** (`Y`), not semver-major: `3.14 → 3.15`
     is major `3` on both sides yet is exactly the "CI may not even run
     3.15 yet" threat, so a generic "semver-major increased" test would
     miss it;
   - any **`requires-python`** change in `pyproject.toml`.

   **REVIEW** (job passes, writes an advisory notice to
   `$GITHUB_STEP_SUMMARY` — never a PR comment):
   - a change to the FI/recap git-pin or the `wheel-only-guard` machinery;
   - a **major** bump of a native/compiled-extension runtime dependency
     (`cfgrib`, `rioxarray`, `exactextract`, `forecastinterface`) — ABI/GDAL/
     wheel risk a fresh-env CI run may not surface. Ordinary pure-Python
     library majors (pandas, pydantic, …) are **not** flagged: the
     `unit`/`integration` jobs already exercise them, and flagging every
     library major would reintroduce rubber-stamp fatigue for a risk PR #78
     did not demonstrate;
   - a postgis-major confined to `ci.yml`'s **ephemeral** `integration`
     service container (`ci.yml:108`, no volume mount) — advisory only,
     with a "keep in lockstep with `docker-compose.yml`" note, since the
     data-directory break that motivates BLOCK cannot occur there.

   **ALLOW** (silent): patch/minor of a normal library, action patch bumps,
   dev-dependency patches.

   The version comparison always parses the machine-readable `image:`
   field value (before `@sha256:...`), **never** the trailing
   `# name:tag` comment — Dependabot does not keep that comment in sync
   (PR #78's branch left the comment reading the pre-bump tag), and a
   comment-reading classifier would have silently passed the one PR this
   gate exists to catch.

   **Override — committed allowlist, not a PR label.** `ci.yml`/
   `dependency-safety.yml` trigger only on `[opened, synchronize, reopened]`
   (no `types: [labeled]`), so a label applied to an already-open PR would
   not re-run the check — a BLOCK could never be cleared that way. Instead,
   clearing a BLOCK requires a committed, code-reviewed entry in
   `.dependency-safety-allowlist` (mirrors the `.trivyignore` precedent
   above): one finding key per line, with a dated justification comment.
   Pushing that commit re-runs the check via `synchronize` and leaves a
   durable audit trail in git history.

   **Enforcement status: advisory today.** `main` has no branch protection
   or rulesets (`gh api repos/hydrosolutions/SAPPHIRE_flow/branches/main/protection`
   → 404; `.../rulesets` → `[]`), so a BLOCK is a red check a human can
   still bypass. Making `dependency-safety` a required check needs
   repo-admin access and is an **owner-only manual action** — either full
   required-checks branch protection, or (recommended, narrower) a ruleset
   scoped to just this check:
   ```bash
   gh api --method POST repos/hydrosolutions/SAPPHIRE_flow/rulesets \
     -f name="dependency-safety required" \
     -f target="branch" \
     -f enforcement="active" \
     -f 'conditions[ref_name][include][]=refs/heads/main' \
     -f 'rules[][type]=required_status_checks' \
     -f 'rules[][parameters][required_status_checks][][context]=dependency-safety'
   ```
   (or Settings → Branches → Branch protection rules / Rulesets in the
   GitHub UI). Until that lands, treat a `dependency-safety` BLOCK the same
   as a manual policy gate: do not merge past it without the allowlist
   override.

3. **Out of scope (no Trivy duplication).** The gate does not diff
   `uv.lock` for transitive CVEs — the existing Trivy fs scan (below)
   already gates `HIGH`/`CRITICAL` fixable vulnerabilities on every PR. The
   residual gap (yanked-but-not-CVE releases) is mitigated by the 48 h
   Dependabot cooldown (`dependabot.yml`).

See `docs/plans/119-dependency-bump-safety-gate.md` for the full design and
`docs/standards/cicd.md` § `dependency-safety.yml` for the workflow's
operational shape.

### SBOM generation

`syft` runs after the CI image build and emits a CycloneDX JSON SBOM (`sbom.cdx.json`) uploaded as a workflow artifact on every run. SBOM gives the repo immediate recoverability value: when a future CVE lands, the artifact answers "which historical image contains the affected library?".

Release attachment and registry attestation are future controls — deliberately deferred until an image-publish workflow exists. The SBOM artifact on every CI run is the v0 baseline.

**SBOM and `model_artifacts.sha256_hash` are complementary, not substitutes.** The per-image CycloneDX SBOM answers "which image ships library X?" when a CVE lands; the per-artifact `sha256_hash` (see §Model code trust boundary) protects the runtime integrity of a specific model artifact at load time. Different mechanisms, different purposes — neither replaces the other.

### Vendored PGDG signing key

The PostgreSQL Global Development Group signing key is vendored in the repo at `docker/keys/apt.postgresql.org.asc` (fingerprint `B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8`). The runtime stage of `Dockerfile` `COPY`s this file instead of fetching the key over the network at build time. Rotations are a deliberate reviewed change, not a trust-on-first-use fetch.

### Accepted residual risk — live OS-package feeds

`apt-get install` in the runtime stage still pulls from the live Debian and PGDG apt indexes. No snapshot pinning or internal mirror is attempted in v0 — the maintenance cost would be disproportionate to the marginal benefit at current scale. This residual drift is explicit and monitored via the post-build `trivy image` scan, which catches OS-level CVEs in whatever package set the build happens to pull.

If staging or Nepal deployment surfaces concrete drift-driven breakage, snapshot mirroring can be revisited as a follow-up plan.

## Model code trust boundary

Forecast models — including FI-wrapped ML models via `ForecastInterfaceAdapter` — execute
in the same worker process as DB connections and Docker secrets.

**Trust model:** Model packages are vetted by the IT team and installed at Docker image
build time via Python entry-point registry. No user-supplied or runtime-loaded model code
is permitted. Only registered entry points are discoverable by the model loading mechanism.

**In-process exposure:** The container privilege model (non-root, dropped capabilities)
limits host-level impact but does not isolate model code from in-process state. This is
an accepted risk given the trust model above.

**Artifact serialization preference hierarchy:** Model implementors must use safe serialization formats in priority order:

1. **Format-native serialization** — `numpy.savez_compressed` (linear/statistical models), XGBoost/LightGBM `save_model()`, TF SavedModel / `.keras`, PyTorch `safetensors` for `state_dict()`. Always preferred — these formats cannot execute arbitrary Python code on deserialization.
2. **`skops`** — for sklearn estimators. Preferred over joblib/pickle. Requires an explicit `trusted=[...]` type list in `deserialize_artifact()` to prevent type confusion attacks.
3. **Pickle** — permitted only when no safe alternative covers the use case. Requires explicit justification in the `deserialize_artifact()` docstring and IT review of the model package. Note: `joblib` is not a safe alternative — it uses pickle internally for Python objects.

SHA-256 hash verification (stored in `model_artifacts.sha256_hash`) is the primary artifact integrity control regardless of format. The preference hierarchy is defense-in-depth against deserialization attacks — it reduces but does not eliminate risk for formats lower in the hierarchy.

**Output validation:** Model outputs pass through `SanityCheckFailure` validation
(conventions.md §Custom exceptions) before DB insertion. This is a data integrity check,
not a security boundary — it rejects implausible values but does not sandbox model execution.

## Network policy

### Exposed ports (via Caddy)
- 443 (HTTPS) — the only externally reachable port
- **v0 exception**: Without `SAPPHIRE_DOMAIN`, Caddy serves plain HTTP on port 80. Set `SAPPHIRE_DOMAIN` to enable auto-TLS and restrict to 443-only.

### Internal only (Docker network, not exposed to host)
- PostgreSQL: 5432
- PgBouncer: 6432 *(v1, §A3)*
- Prefect server: 4200
- FastAPI: 8000

Prefect UI (port 4200) is accessible only via SSH tunnel: `ssh -L 4200:localhost:4200 user@vm`. Documented in operational runbook.

## Security headers

Configured in Caddy as global `header` directives. Applied to all responses.

| Header | Value | Purpose |
|---|---|---|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` | HSTS — forces HTTPS for 2 years. Caddy sets this automatically with auto-HTTPS; documented here for explicit confirmation. |
| `X-Content-Type-Options` | `nosniff` | Prevents MIME-type sniffing attacks. |
| `X-Frame-Options` | `DENY` | Prevents clickjacking via iframes. Redundant with CSP `frame-ancestors` but provides fallback for older browsers. |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'` | Controls resource loading for the HTMX dashboard. `'unsafe-inline'` for styles only (HTMX swap operations may inject inline styles); scripts are strictly same-origin. |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limits referrer leakage to origin-only for cross-origin requests. |

API-only responses (JSON) benefit from `X-Content-Type-Options` and `Strict-Transport-Security`. The CSP is primarily relevant for the HTMX dashboard.

## Audit logging **(v1)**

> The `audit_log` table is created in v1; v0 relies on structured application logs for traceability.

The `audit_log` table is INSERT-only for `sapphire_api` (no UPDATE/DELETE). Records:
- All authentication events (login, logout, failed attempts, password changes)
- All forecast status transitions (raw → reviewed → published)
- All forecast adjustments (with forecaster ID and rationale)
- All model promotion/rejection decisions
- All API key creation/revocation

Retention: permanent. Included in database backup.

## OWASP top 10 mitigations

| Risk | Mitigation |
|---|---|
| A01 Broken Access Control | Role-based authorization matrix enforced server-side. API keys are read-only. |
| A02 Cryptographic Failures | Secrets in Docker secrets, not env vars. JWT signed with HS256. Passwords hashed with bcrypt. TOTP seeds encrypted at rest with dedicated key (§ TOTP secret encryption). |
| A03 Injection | All DB queries use parameterized queries via SQLAlchemy/asyncpg. `StationCode` NewType at Protocol boundary. |
| A04 Insecure Design | Protocol-based store interfaces prevent direct SQL. Layering rule enforces separation. |
| A05 Security Misconfiguration | Caddy auto-HTTPS. Container non-root. Capability drop. Read-only filesystems. |
| A06 Vulnerable Components | Python deps: `uv.lock` commits resolver output; Dependabot raises reviewed updates across `uv`, `docker`, `docker-compose`, and `github-actions` ecosystems. Images and CI actions digest/SHA-pinned; CI scans with `trivy fs` (lint) and `trivy image` (post-build) on HIGH/CRITICAL. See §Supply chain. |
| A07 Auth Failures | MFA mandatory. Account lockout. Short-lived JWTs. Refresh token rotation. |
| A08 Data Integrity Failures | Append-only audit log. Forecast adjustments are immutable records. Model artifacts verified by SHA-256 hash. |
| A09 Logging Failures | All auth events logged. Structured JSON logging. Audit log is permanent. |
| A10 SSRF | No user-supplied URLs in adapter calls. All adapter endpoint URLs (NWP sources, BAFU LINDAS, MeteoSwiss STAC) are deployment config only — read from `config.toml` at startup, never from user input or request parameters. |

## Threat model: host compromise

Docker secrets, container isolation, and application-level auth protect against application-layer attacks. They do **not** protect against an attacker with root access to the host VM. This section documents what is and is not within the application's ability to detect or prevent.

### What root access gives an attacker

- Read all Docker secrets (`/run/secrets/`, `./secrets/` on host)
- Connect directly to PostgreSQL (bypassing API auth)
- Modify forecasts, observations, alerts, and model artifacts
- Read and delete audit logs
- Exfiltrate all data

### What SAPPHIRE Flow implements (our scope)

These are application-level mitigations that provide **detection after the fact**, not prevention:

| Mitigation | What it detects | Implemented by |
|---|---|---|
| **Append-only audit log** | Forecast changes, auth events, model promotions without matching audit trail entries indicate unauthorized access. `sapphire_api` has INSERT-only permission — no UPDATE/DELETE. | Application (FastAPI) |
| **Immutable forecast adjustments** | Every manual adjustment is an immutable record. Direct DB modifications leave no adjustment record — visible in audit review. | Application (store layer) |
| **Model artifact SHA-256 hashes** | Tampered model files won't match their stored hash. Detected on next model load. | Application (model registry) |
| **Backup integrity verification** | Monthly automated restore rehearsal (architecture-context.md § Backup and DR). A compromised DB can be compared against a clean backup. | Application (Prefect task) |

### What the hydromet IT team implements (their scope)

These are infrastructure-level mitigations that provide **prevention and real-time detection**:

| Mitigation | What it prevents/detects | Implemented by |
|---|---|---|
| **SSH IP allowlisting + VPN** | Unauthorized SSH access from unknown networks | Firewall (UFW/iptables) |
| **SSH key-only auth + fail2ban** | Brute-force SSH attacks | OS configuration |
| **`auditd`** | All SSH sessions, `sudo` usage, file access on the host — real-time detection of unauthorized activity | OS audit framework |
| **`pgaudit` extension** | All SQL queries logged at the PostgreSQL level — detects direct DB access that bypasses the API | PostgreSQL configuration |
| **Log forwarding to off-host SIEM** | Prevents an attacker from deleting logs after compromise | IT infrastructure |
| **Network segmentation** | Limits lateral movement if VM is compromised | Network infrastructure |

### Responsibility boundary

SAPPHIRE Flow's security boundary ends at the container. The application assumes the host VM is trustworthy. If this assumption is violated, the application provides after-the-fact detection (audit log gaps, hash mismatches) but cannot prevent data modification.

The deployment guide clearly documents this boundary and the IT team's responsibilities. The SAPPHIRE development team does not implement, monitor, or maintain host-level security.
