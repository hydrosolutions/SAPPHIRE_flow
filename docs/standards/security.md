# Security Standards

> This document extends `docs/architecture-context.md`. It adds implementation detail for security concerns. For foundational decisions, see: access roles (architecture-context.md § Access management), DB service users (conventions.md § Database connection patterns), API routes (conventions.md § API conventions). This document does not redefine roles, DB permissions, or API route patterns.

## Authentication (v1)

v0 defers auth — single-user, no access control. Everything below applies from v1.

### CHWRR named-human foundation (Plan 341 T1)

Plan 341 T1 adds the `users`, `user_external_identities` and
`human_station_grants` tables without enabling forecast publication. These
are the first columns of the planned local `users` model; password/TOTP
sessions and user-management HTTP routes remain deferred. One local user UUID
belongs to one tenant and may be linked to one or more exact `(issuer, sub)`
OIDC identities. A signed access token must pass the configured HTTPS JWKS,
issuer, API audience, RS256 or ES256 algorithm, required `iat`/`nbf`/`exp`,
30-minute maximum lifetime, 60-second maximum clock skew and configured MFA
`amr` or `acr` assurance value. An unlisted or disabled local user is denied.
Service access tokens remain GET-only and cannot act as named humans.

Station permissions are local `review` and `publish` grants. Publish requires
an existing review grant; revoking review also removes publish. The database
binds each grant to a user and station in the same tenant. The API loads grants
for every request, so disabling a user or revoking a grant takes effect on the
next request. Operator-only CLI actions run inside the API container through
`python -m sapphire_flow.cli.hydrologists --operator <handle> ...` and commit
their user/grant change with a system-actor audit row; the handle is recorded
in audit detail, not presented as a verified human UUID. T2's publication
store records the verified hydrologist UUID in its decision and audit row;
T3 will expose the human write route.

If all `SAPPHIRE_HUMAN_OIDC_*` values are absent, human authentication is off;
a partial configuration fails API startup. Required values are `ISSUER`,
`AUDIENCE`, `JWKS_URL`, `ALGORITHM`, and at least one of `MFA_AMR` or `MFA_ACR`
(comma-separated accepted claim values). The actual CHWRR provider, its MFA
assurance policy and signing-key rollover must be verified before activation.
`SAPPHIRE_HUMAN_DASHBOARD_ORIGIN` is an exact origin: only that origin gets
GET/POST CORS permission under `/api/v1/review/forecasts`, for the named-human
dashboard contract; other configured consumer origins retain GET only.
No human review or publish route is mounted by T1. **Plan 404 T3** extends
this origin to a SEPARATE, GET-only CORS policy scoped to exactly
`/api/v1/stations/{id}/rejected-forecasts` (`api/cors.py::ScopedCorsMiddleware`)
— that route never needs POST, unlike the review-forecasts policy above; with
`SAPPHIRE_HUMAN_DASHBOARD_ORIGIN` unset the path gets no CORS at all, same as
the review prefix today.

Plan 341 T2 adds a host-only `sapphire_publication_health` database role. It
starts `NOLOGIN`; an operator enables a separate credential on the protected
backup host only when the DHM target and retention policy are ready. It may
write the derived backup-health/proof projection and read publication decisions
to calculate the backlog, but cannot publish forecasts.
The API can read that projection and insert publication decisions, selections,
audit and feed events, but cannot forge health/proof rows. Forecast workers
cannot write either publication decisions or health. The backup host reads its
database URL from an owner-only file; the credential is never mounted into API
or worker containers. Missing, stale or unhealthy health fails closed for new
publication; a reasoned withdrawal is still allowed. T2 implements no API
publication route or CHWRR activation switch.
Plan 341 T2b records exact per-forecast attestations and decision-linked proof
checks for all pending published IDs in a consistent dump; `sapphire_api` and
`sapphire_worker` cannot forge either proof or global health. Pending manifests
on the protected target are excluded from healthy status until reconciliation
finishes their attestations.
Two scoped `SECURITY DEFINER` functions lock current human grants and the
candidate forecast row inside a decision transaction. Execute rights go to
`sapphire_api` only; it receives no UPDATE grant on `forecasts` or
`human_station_grants`. The functions use fixed schema names and search path.
A revocation waits for an in-flight decision to commit, or wins before its
grant check.

### v1.0 headless subset (implemented, Plan 147 Slices A-E)

Plan 147 (v1.0-headless, per `docs/plans/106-v1-critical-path-roadmap.md` D6) implements a
**deliberately narrowed** subset of the full v1 authentication/authorization design below, ahead of
the human-session/dashboard stack. Everything in this subsection is REALIZED code; the rest of
§Authentication (v1) — OAuth2 sessions, TOTP MFA, JWT/refresh tokens, dashboard user management, the
5-role human matrix — remains the **v1.x target** and is not yet built.

- **Three HTTP roles**: `consumer` (read, station-scoped — the scope resolved in one of two
  `scope_mode`s, below), `reviewer` (read, scoped exactly like a consumer, plus the REVIEW routes —
  § Reviewer tokens below) and `admin` (read, unscoped + CLI token/tenant management). Plan 147 G4
  had *two* roles; Plan 401 D1 (owner, 2026-09-26) amends G4's **role list only** — GET-only (next
  bullet) stands for every token. No session roles, no `operator`/`forecaster` role.
- **Restricted DHM history**: `GET /api/v1/stations/{station_id}/observations`
  withholds Plan 268's delivery-tagged discharge rows from consumer and reviewer
  tokens even when the station is in scope. Internal admin tokens can read them;
  do not issue such a token externally without the data owner's permission.
- **Access tokens are strictly GET-only** — no bearer key of any role may POST/PATCH/DELETE. The
  sole state-changing v0/v1.0 route, `POST /alerts/{id}/acknowledge`, is removed from the v1.0 surface
  (returns `501`); it returns with the Flow 3 dashboard + session tokens in v1.x.
- **Key hash: HMAC-SHA-256 + a server-side pepper** (see the § API key lifecycle correction below —
  this supersedes the bcrypt line in the general design).
- **Health exemption**: `GET /api/v1/health` is the ONLY unauthenticated route (shallow liveness —
  the Caddy/Compose healthcheck and startup ordering depend on it staying reachable pre-auth).
  `GET /api/v1/health/detail` is `admin`-only. There is no separate "health-reader" capability — an
  admin-scoped token is required, matching the existing trust model (prod shell access already implies
  this privilege level, § Bootstrap below). The host-level watchdog (`ops/watchdog.py`) presents an
  admin-scoped bearer token read from a **HOST secret file**, `./secrets/health_probe_token` (chmod
  600, NOT a Docker/Compose mount — the watchdog is a launchd host process, same convention as
  `./secrets/slack_webhook_url`). Missing/unreadable/empty falls back to the pre-existing unauthenticated
  probe-failure path (`found=False`), never a crash.
- **Dead-man's-switch ping URL (Plan 163)**: same HOST-secret-file convention as the two secrets
  above — `./secrets/deadman_url` (chmod 600, git-ignored). It is a **bearer capability**: anyone
  holding the URL can ping the external check and thereby *mask* an outage, so it is handled with the
  same care as the Slack webhook (never logged, never committed). Missing/empty/unreadable/undecodable
  ⇒ the watchdog emits no heartbeat and raises no error (feature-off by default in dev/CI, where the
  file is absent). All outbound HTTP call sites in `ops/watchdog.py` (health probe, BAFU-detail probe,
  Slack POST, dead-man POST) catch `httpx.HTTPError`, `httpx.InvalidURL` (verified NOT a subclass of
  `HTTPError`), `OSError` (covers `ssl.SSLError`/socket errors) and `UnicodeError` (malformed IDNA), plus
  a final defensive `except Exception` — never `BaseException` — so a malformed hand-pasted URL cannot
  kill a watchdog tick.
- **Every other endpoint requires a valid, non-expired, non-disabled key** — the JSON `/api/v1/...`
  API, the legacy `.json` exports, and every HTML router (`/tables/`, `/stations/`, `/forecasts/`,
  `/models/`, the dashboard). The legacy HTML/browser routers and the `.json` exports that share a
  router module with them are **admin-gated in full** (R3) rather than individually scope-filtered —
  the modern `/api/v1/stations|forecasts|alerts` JSON API is the one surface a `consumer` token
  reaches, with per-endpoint station-scope filtering. A `reviewer` token reaches that same surface,
  identically scoped, plus the REVIEW routes; never an admin-gated route.
- **Scope contract narrowed to the station axis only for v1.0.** § API key lifecycle below documents
  a 3-axis scope (station + parameter + geographic boundary) as the full v1 design; v1.0 implements
  **only** the station axis, resolved in one of two `scope_mode`s (Plan 215 D2.1): `'stations'` (the
  default) is a normalized `access_token_stations` join, not JSONB; `'tenant'` derives the scope from
  `stations.tenant_id` at load time instead — no materialised rows, so a station added to the tenant
  after the token is in scope immediately, at the cost of being unfiltered by network/station kind
  (`cicd.md` § access-token runbook). Parameter and geographic scoping are **deferred to v1.x**.
  The same rules apply to consumer and reviewer tokens: empty scope = the token sees nothing
  (fail-closed); out-of-scope station ids return 404 (not 403 — do not reveal existence), and so does
  an out-of-scope forecast id, with the same body as an absent one; a null (stationless)
  `station_id` (e.g. some `alerts` rows) is never in their scope either.
- **CORS**: `SAPPHIRE_CORS_ORIGINS="*"` is rejected at API startup once auth is enforced (a wildcard
  origin would let any site's JS ride a browser-held bearer token). Unset = no CORS middleware
  (same-origin only); set an explicit comma-separated origin list for a browser-based consumer.
- **`create-admin` / `create` / `create-reviewer` / `list` / `revoke` / `show` / `grant` /
  `revoke-station` / `set-scope-mode` CLI** — see § Initial deployment bootstrap below and `cicd.md`
  § access-token runbook. `show`/`grant`/`revoke-station`/`set-scope-mode` are Plan 215 (T1/T2/T6): a
  token's station scope now has a supported edit path, so scope-edit is **no longer** deferred to
  v1.x; they act on consumer and reviewer tokens alike. `create-reviewer` is Plan 401. In-place key
  **rotation** is still deferred; v1.0 rotation = `revoke` + `create`/`create-reviewer`
  (re-materializing the token's scope rows, or re-running `set-scope-mode` for a `'tenant'`-mode
  token).
- **Least-privilege DB roles are REALIZED** (Plan 147 Slice D — see § Least-privilege DB roles below):
  the app runs as scoped `sapphire_api`/`sapphire_worker` roles (never the owner/migration superuser),
  each with its own credential, per-table grants (not blanket `UPDATE`/`DELETE`), and no
  `CREATE`/`DROP`/cross-database access. `sapphire_prefect` remains the owner credential — a documented
  residual, not built by this slice.
- **Tenant write-isolation is REALIZED** (Plan 147 Slice E — see § Tenant write-isolation below): the
  flow/CLI write paths (station onboarding, group/model assignment, model promotion, incl. the
  scheduled `train_models_flow`) reject a cross-tenant write via a config-declared `WritePrincipal` —
  never a read-only access token, never the target row itself.
- **Deferred to v1.x** (not built by Plan 147): OAuth2 human sessions, TOTP MFA, JWT/refresh tokens,
  dashboard user management, the `forecaster`/`model-admin` session roles, `POST
  /forecasts/{id}/adjust`, `PATCH /forecasts/{id}/status`, `POST /alerts/{id}/acknowledge`, concurrent-
  session limits, account lockout, per-request `api_key_request` audit logging, parameter/geographic
  scope axes, in-place token rotation, and a distinct scoped `sapphire_prefect` role.

### Reviewer tokens (v1.0, Plan 401)

A `reviewer` token is the identity of one review dashboard (the Swiss/BAFU dashboard, the Nepal
dashboard) — one token per dashboard deployment, **held server-side by that dashboard**, never shipped
to a browser.

- **A consumer plus the REVIEW routes** (Plan 401 D2). A reviewer token is GET-only and tenant-bound,
  uses the consumer's scope rules unchanged (both `scope_mode`s, 404 for an out-of-scope station),
  reaches every PRINCIPAL route exactly as a consumer does, and additionally reaches routes gated
  **REVIEW** (`api/security.py::require_reviewer`, which admits `reviewer` and `admin` and refuses a
  `consumer` with 403). It never reaches an ADMIN route. No existing route was reclassified; the first
  two REVIEW routes arrive with Plan 402 (`GET /api/v1/qc/rules`, `GET /api/v1/stations/{id}/skill`).
- **The REVIEW class.** `require_reviewer` checks the role only. Every REVIEW route serving station
  data applies the principal's station scope itself — 404 on detail routes, filtering on collections.
  A REVIEW route serving forecast values withholds those values and the flag `detail` from reviewer
  tokens where Plan 341's publication gate is active, still returning the rule fields (Plan 404 D4);
  admins keep full access there (Plan 341). `GET /api/v1/qc/rules` without `station_id` is
  deployment-global and station-agnostic (no scope check applies — it carries no station data); with
  `station_id` it is station-scoped exactly like any other detail route: 404 for an out-of-scope or
  unknown station (D15). `GET /api/v1/stations/{id}/skill` is station-scoped the same way.
- **No unpublished forecasts, no writes.** Where Plan 341's gate is active a reviewer token is gated
  exactly like a consumer on the forecast routes; only Plan 341 may change that default. Publishing
  and withdrawal are a named, signed-in person's act (Plan 341), never a token's (Plan 401 D3).
- **Tenant binding (Plan 401 D4, confirming Plan 268 D11).** A token is bound to one tenant for life
  and cannot span or change tenants — `grant` refuses a station from another tenant. A dashboard token
  may use `scope_mode = tenant` **only when every station in its tenant belongs to that dashboard's
  client**; until then it uses `scope_mode = stations` with an explicit station list. The Swiss
  dashboard's token (tenant `sapphire`) therefore stays in `stations` mode while any non-Swiss station
  remains in `sapphire`. The Nepal dashboard's token binds to the DHM tenant only. Admin tokens stay
  global (no tenant, no station scope).
- **Rollback.** An image older than Plan 401 cannot parse the role: `cicd.md` § Rollback deletes every
  reviewer token before such an image starts; migration `0061`'s downgrade refuses while one exists.

### Tenant write-isolation (v1.0, Plan 147 Slice E)

Write authority on the flow/CLI write paths (onboarding, group creation and group/model assignment, model promotion) is
**config-declared, never derived from the target row and never from a read-only access token** (G3/G6).
A separate principal kind — distinct from the three HTTP read roles above.

- **`[deployment]` config block** (`config.toml`): `writable_tenants = ["<code>", ...]` (one or more
  tenant codes this host may write to) OR `global_admin = true` (an unscoped host — mutually exclusive
  with `writable_tenants`), plus an optional `operator = "<handle>"` label. Parsed by
  `config/deployment_identity.py`; every declared code is resolved against the `tenants` table at
  principal-resolution time — an unknown code is a hard `ConfigurationError`, not a silent skip.
- **Tenants are declared, not seeded (Plan 513).** Migration `0041` seeds only `sapphire`; every other tenant
  is declared per host as `[tenants.<code>]` (`name`, never an id) in the host's overlay and created by the
  `init` step `cli/provision_tenants.py` (idempotent, one transaction, name conflict aborts). Declaring a tenant
  is provisioning: it never widens or narrows `writable_tenants` (a different setting). The import CLI
  has no tenant-creating command (Plan 510 removed `bootstrap-tenant`): the tenant exists before any
  operator job runs, and the `stations`/`replace`/`qc` commands only look it up by code.
- **`WritePrincipal`** (`types/write_principal.py`): `WritePrincipal(id: PrincipalId | None, tenant_id:
  TenantId | None)`. `PrincipalId = NewType("PrincipalId", str)` is the config operator handle — never a
  `UserId`/UUID, never an `AccessTokenId`. `tenant_id=None` = unscoped/global-admin (may write to any
  tenant); a set `tenant_id` binds every write to that one tenant.
  - **Interactive CLI / flow param**: an explicit `--tenant <code>` (or `tenant_code=` flow parameter)
    is validated against the host's `writable_tenants` (bypassed for `global_admin`) and resolved to a
    `TenantId`. Absent, a single-writable-tenant host binds its sole tenant; a `global_admin` host is
    unscoped; a host declaring more than one writable tenant with no explicit code is ambiguous and
    raises.
  - **Scheduled `train_models_flow`**: builds exactly ONE run principal from config **before**
    `_determine_scope_task` selects any training unit — never from `unit.station_id`/`unit.group_id`.
    `scope.units` is then filtered to that principal's tenant; a foreign-tenant unit already in scope is
    **skipped-with-audit**, never trained/promoted. One scheduled deployment per tenant (or an
    explicitly-declared `global_admin` run that trains across tenants).
- **Enforcement** (`services/write_principal.py::enforce_tenant_isolation`, called at every write
  chokepoint — `services/training.py::promote_artifact`/`store_and_promote_artifact`,
  `services/model_onboarding.py::create_station_assignment`/`create_group_assignment`,
  `services/onboarding.py::onboard_from_camelsch`, and
  `scripts/create_station_group.py::apply_station_group` plus its CLI pre-check): a target whose `tenant_id` differs from the
  principal's raises `TenantIsolationError` **before** any domain-state write, and persists a
  `system`-actor `audit_log` rejection row (operator handle + both tenant ids in `detail` — `actor_id`
  stays `NULL`, a config operator is not a `UserId`/`AccessTokenId`). An unscoped (`global_admin`)
  principal bypasses the check.
- **Promotion provenance**: a successful promotion writes a `MODEL_PROMOTED` `audit_log` row
  (`actor_type='system'`, `detail.operator`/`detail.tenant_id`). `model_artifacts.promoted_by` (the
  legacy nullable UUID column) stays `NULL` in v1.0 headless — a config-string `PrincipalId` does not
  fit a UUID column; it is reserved for the v1.x human-session `UserId`.
- **Read isolation is explicitly OUT of scope** (D4): Slice C's per-key station-scope filtering already
  bounds what a `consumer` token can read; this slice is write-isolation only.

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
- Stored hashed — **HMAC-SHA-256 over the raw key with a server-side pepper** (`access_token_pepper`,
  a dedicated Docker secret; corrects an earlier bcrypt draft — bcrypt buys no margin over a fast
  keyed hash for a high-entropy random secret but adds real per-request CPU on the hot auth path at
  project scale). Matches the `refresh_tokens` SHA-256 precedent above. A `pepper_version` column is
  the forward hook for v1.x zero-downtime dual-pepper rotation; v1.0 rotation is documented in
  `cicd.md` (all-token-reissue). Plain-text token shown once at creation, never stored.
- Scoped per consumer: station list, parameter list, geographic boundary (the full v1 design — v1.0
  implements the station axis only, see § v1.0 headless subset above).
- API keys cannot trigger flows, modify forecasts, or access audit logs.
- Rotation: org admin can regenerate; old key invalidated immediately. v1.0: `revoke` + `create`
  (CLI) re-materializing scope; in-place rotation is v1.x.

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
docker compose exec api /entrypoint.sh python -m sapphire_flow.cli.access_tokens create-admin \
    --name "<display name>"
# `/entrypoint.sh` supplies DATABASE_URL (docker compose exec bypasses the ENTRYPOINT);
# create-admin takes only --name (+ optional --expires-days), no --username.
```

The command:
1. Creates a user record with role `org_admin`
2. Generates a temporary password (printed to stdout, single use)
3. Generates a TOTP secret (displayed as QR code or base32 string for authenticator app)
4. Records the creation event in `audit_log`

On first login, the org admin must change the temporary password.

This command requires shell access to the production VM — equivalent to reading `/run/secrets/` directly. It is not a backdoor; it is a structured bootstrap that demands the same privilege level as direct database access.

**v1.0 headless implementation (Plan 147 Slice C):** at that stage there was no `users` table (§ v1.0 headless
subset above) — the ACTUAL bootstrap command mints an **unscoped admin ACCESS TOKEN**, not a
user+password+TOTP record:

```
docker compose exec api python -m sapphire_flow.cli.access_tokens create-admin \
    --name "<operator/purpose label>"
```

Prints the raw bearer key once (never persisted/logged) and writes exactly one `API_KEY_CREATED`
`audit_log` row (`actor_type='system'`) in the same transaction as the token insert. Same trust
model as above: requires shell access to the production VM (`docker compose exec`), which already
implies reading `/run/secrets/` directly (including `access_token_pepper`). Ongoing token management
(`create` for scoped consumer tokens, `create-reviewer` for review-dashboard tokens, `list`,
`revoke`, and the scope verbs) uses the same module — see § API key lifecycle management below.

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
| **`./secrets/` file permissions** | `chmod 600`, owned by root. Prevents other OS users from reading secrets on the host. **Linux cloud hosts (Plan 511):** bind-mounted secrets keep host ownership and the containers drop capabilities, so the directory is `750 root:docker` and the files `644` root-owned — the directory, not the file mode, excludes other users (`docs/deployment/nepal-cloud-host.md` § 2; to be confirmed at first boot). | High |
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
>
> **v1.0 headless REALIZED status (Plan 147 Slice C, Plan 401):** only the **API consumer** column
> (renamed `consumer` in code) and the **Reviewer token** column (`reviewer`, Plan 401) are
> implemented, plus an unscoped `admin` role not shown as a separate column below. Every `Org admin`/`IT admin`/`Model admin`/`Forecaster` column, and every row that is
> exclusively a human-session route (`POST /forecasts/{id}/adjust`, `PATCH /forecasts/{id}/status`,
> the flow-trigger/model-artifact-status routes, all `/users`/`/access-tokens` HTTP management routes),
> is the **v1.x target** — v1.0 access-token CLI (`create`/`create-reviewer`/`list`/`revoke`/
> `create-admin`) replaces the `/access-tokens` HTTP surface for now. `POST /alerts/{id}/acknowledge` is unreachable in v1.0 (501)
> regardless of role. `GET /api/v1/health/detail` is `admin`-only in v1.0 (not IT-admin-only as drawn
> below — there is no IT-admin role yet).

Role-to-endpoint mapping. Enforced via FastAPI dependency injection (`Depends(require_role(...))`), not frontend visibility.

| Endpoint pattern | Org admin | IT admin | Model admin | Forecaster | API consumer | Reviewer token |
|---|---|---|---|---|---|---|
| `GET /api/v1/stations` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /api/v1/stations/{id}/forecasts` (published) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /api/v1/stations/{id}/forecasts` (all statuses) | ✓ | ✓ | ✓ | ✓ | — | — |
| `GET /api/v1/stations/{id}/observations` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /api/v1/alerts` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| REVIEW routes (Plan 402: `GET /api/v1/qc/rules`, `GET /api/v1/stations/{id}/skill`) | v1.x | v1.x | v1.x | v1.x | — | ✓ |
| `POST /api/v1/forecasts/{id}/adjust` | — | — | — | ✓ | — | — |
| `PATCH /api/v1/forecasts/{id}/status` | — | — | — | ✓ | — | — |
| `POST /api/v1/alerts/{id}/acknowledge` | — | — | ✓ | ✓ | — | — |
| `POST /api/v1/flows/ingest/trigger` | — | ✓ | ✓ | — | — | — |
| `POST /api/v1/flows/train/trigger` | — | — | ✓ | — | — | — |
| `PATCH /api/v1/model-artifacts/{id}/status` | — | — | ✓ | — | — | — |
| `GET /api/v1/health` (public) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /api/v1/health/detail` | ✓ | ✓ | — | — | — | — |
| `POST /api/v1/users` | ✓ | — | — | — | — | — |
| `GET /api/v1/users` | ✓ | — | — | — | — | — |
| `PATCH /api/v1/users/{id}` | ✓ | — | — | — | — | — |
| `POST /api/v1/access-tokens` | ✓ | — | — | — | — | — |
| `GET /api/v1/access-tokens` | ✓ | — | — | — | — | — |
| `DELETE /api/v1/access-tokens/{id}` | ✓ | — | — | — | — | — |
| `POST /api/v1/access-tokens/{id}/regenerate` | ✓ | — | — | — | — | — |
| `PATCH /api/v1/users/me/password` | ✓ | ✓ | ✓ | ✓ | — | — |

**Reviewer token column (Plan 401):** a reviewer gets exactly the consumer's access on every existing route, plus the REVIEW class; an admin token also reaches REVIEW routes; human-role access to them (`v1.x`) is decided with the session stack. "All statuses" stays `—` for a reviewer where Plan 341's publication gate is active (§ Reviewer tokens).

**API consumer scope filtering**: A `✓` for an API consumer (or a reviewer token, scoped identically) means the endpoint is accessible, not that the consumer sees all data. Responses are filtered server-side by the token's `scope` (see `access_tokens.scope` in architecture-context.md § Authentication schemas). A consumer scoped to specific stations receives only those stations from `GET /api/v1/stations`, only their forecasts, observations, and alerts. Requests for out-of-scope station IDs return 404. Human roles (org admin through forecaster) are unscoped — they see all data.

### Input-quality visibility (Plan 253 OD-2 — supersedes Plan 023:128-143)

Plan 023 required the threshold-bearing `input_quality`/`input_quality_flags` detail to be
role-filtered once authorization existed, on the assumption a `forecaster`/`operator` role would
exist to filter *to*. Only the HTTP token roles `consumer`, `reviewer` (Plan 401) and `admin` were
built (v1.0 headless subset above), and there is no forecaster/operator role. The owner decided (2026-09-04) the thresholds are not
sensitive and that a forecaster looking at a degraded forecast needs to see why: `input_quality` and
`input_quality_flags` are visible, unfiltered, to **every authenticated role** —
`GET /api/v1/stations/{id}/forecasts` and `GET /api/v1/forecasts/{id}` both expose them to
`consumer`, `reviewer` and `admin` alike. This knowingly supersedes 023:128-143 — record it here so the next
reader does not mistake the dropped prerequisite for an oversight.

**Plan 402 D13 (2026-09-26):** the same reasoning extends to QC. `ForecastSummary.qc_flags` /
`ForecastDetail.qc_flags` (added to routes consumers already read) and `ObservationResponse.qc_rule_version`
are visible, unfiltered, to **every authenticated role** — `consumer`, `reviewer` and `admin` alike. This
is the **one** change Plan 402 makes to what a `consumer` token can read; every other new field/route in
that plan is either REVIEW-gated (`/qc/rules`, `/skill`) or additive-and-already-visible.

**Plan 402 D6 — carried forward, not decided here.** The Nepal region-bundle draft forbids copying QC
flag `detail` verbatim for restricted DHM data. Under Plan 401 D12 the Swiss dashboard cannot see Nepal
stations, and the Nepal dashboard sees only its own client's data — so the exposure left is to a
**Nepal consumer token** (a third party): observation flag `detail` today, and forecast flag `detail`
after Plan 402 T3 (the latter includes observation-derived baseline statistics for
`climatology_outlier`). **Before DHM observations are readable by a Nepal consumer token, the owner
must decide whether observation and forecast flag `detail` is stripped for consumers on that network**
(recorded in Plan 143, DHM onboarding).

**Plan 404 D4 (2026-09-26) — a new route-matrix class, REVIEW_OR_HUMAN.**
`GET /api/v1/stations/{id}/rejected-forecasts` admits a reviewer or admin
service token (the same two roles as REVIEW) OR a named human with a
current station `review` grant (Plan 341). One route-level dependency,
`api/review_auth.py::require_reviewer_or_human`, decides which verifier to
call from the bearer's SHAPE alone — exactly one `.` (a service token,
`prefix.secret`, both `secrets.token_urlsafe` output, which never contains
a dot itself) calls `require_principal`/`require_reviewer`; exactly two
`.` (three JWT segments) calls `require_human_principal`; any other shape,
or either verifier's own failure (including human auth being disabled —
`require_human_principal`'s 503 never reaches the caller), is the SAME
`401` body — no fallback from one verifier to the other. Registered on its
own router, deliberately OUTSIDE Plan 402's `require_reviewer` router
(`api/__init__.py`), since it admits a principal kind that dependency does
not. Where Plan 341's tenant publication gate is active
(`services/publication_gate.py::publication_gate_active`), a **reviewer**
service token's items are withheld (`withheld: true`, `values: null`,
every flag's `detail: null`) — **admin** tokens and a **granted human**
always see the full record; a **consumer** token gets `403`, a revoked
grant or out-of-scope/unknown station `404`. `tests/unit/api/test_security.py`'s
route-matrix (`_classify_routes`, `TestRouteAuthMatrixExhaustive`) carries
this class and pins the route's classification.

## Secrets management

Email and SMS notifications are out of scope through v1 (see `docs/handover/data-flows.md`). Alert consumers can poll the API; outbound delivery on the SAPPHIRE side is webhook-only.

### Production (Docker Compose)

All secrets use Docker secrets (`secrets:` block in `docker-compose.yml`). Mounted as files at `/run/secrets/` and read at startup. Application code reads secrets from file paths, never from environment variables in production.

Required secrets:
- `db_password` — PostgreSQL password for application users
- `sapphire_api_db_password`, `sapphire_worker_db_password`, `sapphire_backup_db_password` — the distinct passwords of the least-privilege `sapphire_api`, `sapphire_worker` and `sapphire_backup` database roles (Plan 147 Slice D; Plan 162 T1). All three are declared in the base `docker-compose.yml` and mounted into `init`, which bootstraps the roles, so a stack cannot start without them. Generate each independently of `db_password`.
- Build-time secrets (environment-sourced, never on disk): `recap_dg_client_token` and `aquacast_token`, needed to build the images (`docs/deployment/nepal-cloud-host.md`).
- `access_token_pepper` — server-side pepper for `access_tokens.token_hash` (Plan 147 Slice C, REALIZED). Mounted only into the `api` service (auth verification + the token-management CLI, run via `docker compose exec api`). The API refuses to boot without it (fail-closed, no unpeppered fallback).
- `secret_key` — JWT signing key (read from `/run/secrets/secret_key`, referenced as `SECRET_KEY` in application config) *(v1)*
- `totp_encryption_key` — Fernet key for encrypting TOTP seeds at rest (see § TOTP secret encryption) *(v1)*
- `sapphire_dg_api_key` — recap Data Gateway API key (**Nepal v1 only**). Declared NOT in the base `docker-compose.yml` but in the Nepal overlay `docker-compose.recap.yml` (Plan 082 Task 2A): it adds the top-level `secrets.sapphire_dg_api_key.file: ./secrets/sapphire_dg_api_key` and the secret ref on both `prefect-worker` and `prefect-worker-ingest` (Compose merges service `secrets` additively). Nepal deploys start with `docker compose -f docker-compose.yml -f docker-compose.recap.yml up`. **Swiss deployments omit the overlay** (plain `docker compose up`) and therefore need **no `./secrets/sapphire_dg_api_key` file at all** — the Recap adapters are never constructed on Swiss (Task 2C/2D `type` selector), and the base compose declares no such secret to resolve. Read at runtime via `config.recap_gateway.load_recap_api_key()`, which falls back to the `RECAP_API_KEY` env var for local dev (same pattern as `db_password`/`DB_PASSWORD` in `docker/entrypoint.sh`).
- `backup_repo_password` — restic repository password *(v1)*

### Development

Secrets are stored outside the repository at `~/.config/sapphire-flow/secrets/`. A gitignored symlink in the repo root lets Docker Compose resolve `./secrets/` transparently:

```bash
mkdir -p ~/.config/sapphire-flow/secrets
openssl rand -base64 24 > ~/.config/sapphire-flow/secrets/db_password
# Plan 147 Slice C: access_token_pepper — the API fails closed at startup
# without it (§ v1.0 headless subset above).
openssl rand -base64 32 > ~/.config/sapphire-flow/secrets/access_token_pepper
ln -s ~/.config/sapphire-flow/secrets secrets
```

This preserves the same file-based secrets path as production — no `.env`-based divergence. The symlink is gitignored (`secrets/` in `.gitignore`), so neither the symlink nor the secret values can be committed.

Alternatively, `.env` files can supply secrets as environment variables for local development. `.env` is in `.gitignore` — CI fails if `.env` is committed. Environment variable names match conventions.md § Environment variables.

### Rotation

- `secret_key`: rotated annually and after any suspected compromise. Rotation procedure: generate new key, deploy, old JWTs expire naturally (30 min).
- API keys: rotated per consumer's request or when compromise is suspected. Org admin regenerates via dashboard (v1.x); v1.0 = `revoke` + `create` (or `create-reviewer`) CLI, re-materializing scope.
- `access_token_pepper` (Plan 147 Slice C, REALIZED): v1.0 rotation is **all-token-reissue** — the key set is tiny (a handful of Nepal/Swiss consumer, reviewer and admin keys). Deploy the new pepper, then `revoke` + `create`/`create-reviewer`/`create-admin` every existing key, by its role (re-materializing each key's station scope). The `pepper_version` column is the forward hook for v1.x zero-downtime dual-pepper rotation (validate against `{current, previous}`, then lazily re-hash) — not implemented in v0/v1.0. Runbook: `cicd.md` § Access-token pepper + probe-token rotation.
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

Plan 340 T2's interim CHWRR evidence bundle is a host-side operation. It does
not mount the Docker socket or the broad-read backup credential into a model
worker. The backup worker reads the database; the forecast worker has only
INSERT on the append-only attestation table. The host operator exports Docker
images and must place the bundle on a separately mounted, access-restricted,
encrypted backup volume before publication is enabled. Dumps and manifests are
created with owner-only file permissions. The Mac mini has no such volume, so
its CHWRR publication gate remains closed. The DHM target and encryption setup
must be verified during deployment; this interim bundle does not claim restic
encryption.
The host `assess` read path uses the backup role and validates hashes on the
protected volume. That volume is not mounted into the API; Plan 341 may expose
only the derived status through an authenticated route.
Plan 340 records no hydrologist publish decision and grants no forecast-evidence
API write path. Treat evidence snapshots, artifact bytes, dumps and image
archives as operationally sensitive: keep the protected target encrypted and
access-restricted, and return only status, attestation identity and reasons in
future review responses. A valid capture or attestation is not a substitute for
the live protected-backup health check before CHWRR publication is activated.

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

`allow_origins` is an explicit list — never `*`. Configured via the `SAPPHIRE_CORS_ORIGINS` env var
(comma-separated origins; `config.toml`'s `[api.cors]` block is the forward-looking config-file carrier,
not yet wired).

> **Plan 147 Slice C (REALIZED, supersedes the v0 exception below):** now that auth is enforced,
> `SAPPHIRE_CORS_ORIGINS="*"` is **rejected at API startup** — a wildcard origin would let any site's
> JS ride a browser-held bearer token. `docker-compose.yml` defaults to an EMPTY value (no CORS
> middleware — same-origin only), not `*`. Set an explicit comma-separated origin list for a
> browser-based consumer.
>
> **Historical v0 exception** (v0-scope.md §J, no longer in effect): v0 had no auth, no dashboard, and
> no sensitive consumers, so a wildcard default was acceptable. That precondition ended with Plan 147.

Production `allow_origins` should include:
- Dashboard origin (same host) — once the v1.x dashboard exists
- Registered origins of known API consumers (Bipad portal, DHM dashboard)

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
- Read-only root filesystem where possible (`read_only: true`), with explicit `tmpfs` for writable paths. Code that resolves paths under the data root must tolerate this — see the `resolve_data_dir` invariant in `docs/conventions.md` § Invariants.
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

Do **not** add a `user:` override in `docker-compose.yml` — this is forbidden by the rule above. The write-path footprint is now known and settled by **Plan 103**: `PREFECT_HOME=/tmp/prefect` on the three Prefect-client services (`prefect-worker`, `prefect-worker-ingest`, `init` — **not** `api`, which is HTTP-only and imports no Prefect client), landing on the writable `/tmp` tmpfs each already has. This is CLI-profile / local result-persistence scratch only — durable orchestration state stays in Postgres (`prefect-server`). Re-evaluate this `user:`-override prohibition only if a `-nonroot` upstream tag or community non-root image appears.

## Supply chain

This section is the **canonical policy source** for supply-chain controls across third-party inputs that ship into the built image or run in CI. `docs/standards/cicd.md` summarises the operational workflow and points back here rather than duplicating the policy surface.

The goal is *attributable* risk — when a CVE lands or a build breaks, `git log` should answer "what changed and when?" for every high-leverage external input. The controls below pin what we can pin by immutable identifier, scan what we cannot, and document the residual risk where live feeds remain unavoidable.

### Root build backend provenance

The root package uses exact pinned isolated build backend inputs in `[build-system].requires`.
`setuptools`, `setuptools-scm`, `packaging`, and `vcs-versioning` are the only root build-backend pins.
Changing `[build-system]` or future `[tool.uv].build-constraint-dependencies` is a dependency-safety REVIEW event because build backends execute during package construction.
Do not duplicate these pins in a second build-constraint list unless a future plan makes that list the single source of truth.

Release-version metadata is generated by `setuptools-scm` into `src/sapphire_flow/_version.py`.
That file is ignored by Git and Docker context filtering.
Docker and CI builds that do not have authoritative tag history must set `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW` from the verified full source SHA or release version.
Credential values remain BuildKit secrets or existing Git credentials; they are not build args, labels, cache keys, or receipt fields.
Image proof commands inspect a mutable local tag only to capture the immutable image ID, then run label, runtime, resource, and optional-dependency capability probes against that image ID with the non-root app user and the package Python entrypoint. The Aquacast proof must import `aquacast` and `torch`, then verify the supported SAP3 shim registration classes (`CmalPoolPT`/`CmalSmall`) rather than a nonexistent discovery module. They must not require database credentials, host mounts, or `PYTHONPATH` shortcuts.

Release identity trust model: release tags and the `release-state` ref are intentionally unsigned (`commit.gpgsign=false`, `tag.gpgSign=false`) so deterministic helper objects can be reproduced. Authenticity therefore rests on GitHub repository access control for pushing `refs/tags/v*`, `refs/heads/release-state`, and `main`; this document does not claim or verify any specific ref-protection rule. The helper's destination checks protect against local URL rewrite or credential-redirection mistakes before publication, but they are not a substitute for repository access control or owner review.

### Python dependency policy

- `uv.lock` is committed. Resolver output is reproducible; `uv sync --frozen` is used in every CI workflow step that installs dependencies.
- The 2026-10-01 security update locks transitive `urllib3` to 2.8.0, addressing
  CVE-2026-97687 (HTTPS proxy TLS configuration override) and CVE-2026-97689
  (unbounded chunk-parser memory allocation). No scanner exemption is added;
  future dependency resolutions must continue to pass the vulnerability gates.
- `pyproject.toml` declares `[tool.uv] required-version = "==0.11.7"` so local `uv` binaries that drift from the repo-standard version fail fast rather than silently re-resolving.
- `pyproject.toml` also declares an explicit `[[tool.uv.index]]` block naming PyPI as the default index. This is informational / future-proofing — not a restrictive control — ahead of any private-index introduction.
- Dependabot raises reviewed upgrade PRs across four ecosystems: `uv` (Python deps), `docker` (Dockerfile base images and `COPY --from=` stages), `docker-compose` (compose services), and `github-actions` (workflow `uses:` entries). Configuration lives at `.github/dependabot.yml`.

### Wheel-only dependency-update guard

A dedicated CI job runs a two-step install sequence. Step 1 = "the wheel-only guard": `uv sync --frozen --no-build --no-cache --no-install-project --no-install-package forecastinterface --no-install-package recap-dg-client`. This makes GitHub-hosted CI the first execution environment for dependency-update installs and fails if any non-excepted package version requires Python build-backend / sdist execution on the CI platform.

Step 2 = "post-guard temporary exception install": `uv sync --frozen --no-cache --no-install-project --reinstall-package forecastinterface --reinstall-package recap-dg-client`. This step is allowed to build only the two excepted packages, after the wheel-only guard has already checked every other locked dependency.

`--no-install-project` is required because the SAPPHIRE Flow root package is editable-only and has no published wheel — the project itself is never installed from an index. This does **not** weaken the guard: the third-party dependencies (the real concern for install-time code execution) are still exercised end-to-end.

**Documented source-build exceptions**: Plan 079 allows the temporary `forecastinterface` source build because it is first-party, public, pure Python, git-pinned to `v0.1.17` / `303aa422...`, and outside Plan 064's untrusted native-build threat model. The removal trigger is exact: remove this exception once ForecastInterface is published as a versioned wheel to a hydrosolutions package index and SAPPHIRE Flow migrates from the git pin to `forecastinterface==0.1.x`. `exactextract` may require a source build on `linux/arm64` in the Dockerfile builder stage. GitHub-hosted amd64 CI stays wheel-only except for the post-guard temporary `forecastinterface`/`recap-dg-client` installs. Any new source-build exception must be recorded in the implementation PR and in this section before merge.

**`recap-dg-client` distribution — LANDED (Plan 082 Task 2H)**: the `hydrosolutions/recap-dg-client` Data Gateway client is distributed by **git pin plus a scoped exception to the wheel-only guard** (`pyproject.toml` `[tool.uv.sources]`, rev `60e5d736dec663d013ce2c05a6f067b82254cd7f`), the same Plan 079-style bridge used for `forecastinterface`. Unlike `forecastinterface`, `recap-dg-client` is a **private** repository, so both GitHub-hosted CI (`lint`, `unit`, `integration-shard`, `wheel-only-guard` jobs) and the Docker builder stage need clone auth. This is provided by a single repo secret, **`RECAP_DG_CLIENT_TOKEN`**. **As of 2026-07-21 (Plan 137) this is a fine-grained PAT scoped to `hydrosolutions/recap-dg-client` ONLY with Contents:Read-only (+ Metadata:Read-only), expiring 2027-07-17** — replacing an earlier broad `gho_` gh-CLI OAuth token that had write access to all repos. **It must be set in BOTH the Actions secret store AND the Dependabot secret store** (Settings → Secrets and variables → Actions *and* → Dependabot): GitHub does NOT share Actions secrets with Dependabot-triggered workflow runs, so a Dependabot-store copy is required or every Dependabot PR's fresh-clone jobs (`wheel-only-guard`, `build-image-and-scan`) fail closed. Renewal: the PAT expires 2027-07-17 — re-mint + update both stores + the mac-mini `secrets/recap_dg_client_token` before then (or migrate to the removal trigger below). CI jobs rewrite `https://github.com/hydrosolutions/recap-dg-client.git` to an authenticated URL via `git config --global url.<...>.insteadOf`, scoped to that one repository URL — never a blanket github.com credential rewrite. The Docker builder stage uses a BuildKit `--mount=type=secret` instead (`docker build --secret id=recap_dg_client_token,env=RECAP_DG_CLIENT_TOKEN`), writing then deleting `/root/.gitconfig` within the same `RUN` so the token never lands in a committed image layer. **Removal trigger**: migrate off the git pin to a versioned **private-index wheel** (a Plan 080-style hydrosolutions package index) and drop both the source-build exception and the `RECAP_DG_CLIENT_TOKEN` secret once that wheel is published — tracked as an IT-specialist follow-up.

### Image pinning

Every externally-pulled image is pinned by **manifest-list digest** (not per-platform digest — the manifest-list resolves per-arch on pull, so the same pin works for both amd64 CI and arm64 Mac mini):

- `Dockerfile` (builder + runtime stages): `python:3.11.12-slim@sha256:...` and `ghcr.io/astral-sh/uv:0.11.7@sha256:...`
- `docker-compose.yml`: `postgis/postgis:16-3.4@sha256:...`, `prefecthq/prefect:3-python3.11@sha256:...`, `caddy:2.9@sha256:...`
- `.github/workflows/ci.yml` integration-shard job `services.image`: `postgis/postgis:16-3.4@sha256:...` (must match the compose postgis digest to avoid silent integration/prod drift)
- `scripts/restore-rehearsal.sh` (`RESTORE_IMAGE`, Plan 162 T5): `postgis/postgis:16-3.4@sha256:44126d872...` — same pin, same vendor as the bullet above (see caveat + owner decision below).

⚠️ **Caveat found 2026-08-18 (Plan 162 T5 restore-path fix):**
`docker buildx imagetools inspect` on the `postgis/postgis:16-3.4@sha256:44126d872...` digest pinned in
`docker-compose.yml` / `ci.yml` (and, since, `restore-rehearsal.sh`) shows a **single-platform `linux/amd64`
manifest, not a manifest list** — and Docker Hub confirms `postgis/postgis` has never published an arm64 image
for **any** `16-3.4*` tag. So "the same pin works for both amd64 CI and arm64 Mac mini" does **not** hold for
that pin today; the mac-mini's Postgres container runs it under emulation. *(⛔ Terminology
corrected 2026-09-23: this said "production Postgres container". **There is no production
deployment** — the mini is a test/staging host, see the README. The emulation fact is unchanged.)*

**Owner decision (2026-08-18, same day, superseding an intermediate round of this fix):** an earlier round of
`restore-rehearsal.sh` re-pinned to `imresamu/postgis:16-3.4@sha256:6da75969...` instead — verified
(`imagetools inspect`) to be a genuine `linux/amd64` + `linux/arm64` manifest list, published by the same
maintainer who publishes the official `postgis/postgis` images. The owner reverted that: **keep the
already-vetted, compose-pinned vendor and accept the emulation** rather than move this container onto a second,
less-audited vendor namespace. Rationale: emulation costs speed only, not correctness; the two real deployment
targets (DHM, AWS) are x86 where `postgis/postgis` runs native, so this is a staging-only (arm64 mac-mini) cost;
and this container's whole job is to hold a fully-restored production dump, including every tenant's
access-token hashes, which is exactly the kind of container where introducing an additional, independently-less-
audited vendor is not worth it just to buy native arm64 on the mini. `restore-rehearsal.sh` keeps the
`RESTORE_IMAGE` override so anyone who wants a native-arm64 image locally can still opt in. Reconciling the
`docker-compose.yml` / `ci.yml` pin itself (same emulation trade-off, already accepted on the
staging host — ⛔ *this said "in production"; there is none*) is out of
scope here — that pin was never changed by T5.

Because this container holds a fully-restored, decrypted dump (including `access_tokens` — token hashes,
tenant_id, scopes across every tenant), `restore-rehearsal.sh` runs it with **`docker run --network none`**
regardless of which image pin is in effect. Nothing needs container-initiated network access — every
interaction is `docker exec` / `docker cp` from the host — so this closes the exfiltration path as
belt-and-braces alongside the already-vetted image pin, independent of the digest pin above.

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

The image scan (Plan 180) runs as scan-once/derive-many: `trivy image` writes one JSON
report (`ignore-unfixed: true`, all severities, non-gating); `trivy convert` then derives
the printed table (the gate — `--exit-code 1 --severity HIGH,CRITICAL`), the SARIF, and
uploads the SARIF to the GitHub Security tab via `github/codeql-action/upload-sarif`, on
both the pass and the fail path. Deriving every output from the same report is what makes
the printed table and the uploaded SARIF provably the same data as what failed the gate.
`upload-sarif` requires `security-events: write` on the job's `GITHUB_TOKEN`; the
`build-image-and-scan` job declares `permissions: {contents: read, security-events:
write}` explicitly rather than relying on the repository's ambient
default-workflow-permissions setting.

**Policy for a CVE with a fix we cannot adopt (Plan 180 D2/D3).** `ignore-unfixed: true`
already drops every CVE with no published fix before either scan can even report it, so
`.trivyignore` is never the right place for a "not fixed yet" case — that describes a
finding Trivy structurally cannot produce here. `.trivyignore` exists for the opposite
situation: a fix **is** published, but we cannot take it (the fix lives in a package
suite we do not track, or in a Python dependency we cannot move yet). Add a dated entry
with a re-review date. That re-review date is a dated **comment**, not an enforced
check — nothing currently re-checks it automatically or fails CI when it passes; a
comment does not expire by itself. Building an enforcement mechanism for it is a
deliberate scope decision to defer, not an oversight.

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
   `.github/workflows/ci.yml`, **plus the gate's own self-policy files**
   (`tools/dependency_safety.py`, `.github/workflows/dependency-safety.yml`,
   `.github/dependabot.yml`, `.dependency-safety-allowlist`) — added in the
   2026-07-15 hardening so a PR cannot weaken the classifier, its trigger,
   Dependabot's ignore rules, or the override allowlist silently.

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
   - a change to **any of the gate's own self-policy files** (the classifier,
     its workflow, `dependabot.yml`, or `.dependency-safety-allowlist`) — so
     self-modification and allowlist edits are surfaced, never silent;
   - a change to the FI/recap git-pin or the `wheel-only-guard` machinery;
   - a **major** bump of a native/compiled-extension runtime dependency
     (`cfgrib`, `rioxarray`, `exactextract`, `forecastinterface`) — ABI/GDAL/
     wheel risk a fresh-env CI run may not surface. Ordinary pure-Python
     library majors (pandas, pydantic, …) are **not** flagged: the
     `unit`/`integration-shard` jobs already exercise them, and flagging every
     library major would reintroduce rubber-stamp fatigue for a risk PR #78
     did not demonstrate;
   - a postgis-major confined to `ci.yml`'s **ephemeral** `integration-shard`
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

See `docs/plans/archive/119-dependency-bump-safety-gate.md` for the full design and
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

**Implemented (Plan 147 Slice B, 2026-07-24):** the `audit_log` table (migration `0045`), its
role-independent append-only guard (migration `0046` — a `BEFORE UPDATE OR DELETE` trigger that
RAISEs for every role, including the table owner), and the `PgAuditLogStore` writer
(`store/audit_log_store.py`). **Append-only is now ALSO enforced by per-role grants (Plan 147 Slice
D, REALIZED):** `sapphire_api` and `sapphire_worker` both exist (`docker/bootstrap-roles.sql`) with
`INSERT`+`SELECT`-only on `audit_log` — defense-in-depth atop the role-independent trigger, not the
primary guarantee (the trigger alone already rejects `UPDATE`/`DELETE`/`TRUNCATE` for every role,
including the table owner). Access-token create/revoke (Slice C) stamp rows through this writer.
**Also REALIZED (Plan 147 Slice E):** the onboard/promote/assign write chokepoints
(`services/onboarding.py::onboard_from_camelsch`,
`services/training.py::promote_artifact`/`store_and_promote_artifact`,
`services/model_onboarding.py::create_station_assignment`/`create_group_assignment`) and the scheduled
`train_models_flow`'s foreign-tenant-unit skip all stamp `audit_log` rows through this writer — both
the success path (`STATION_ONBOARDED`/`MODEL_ASSIGNED`/`MODEL_PROMOTED`) and the tenant-mismatch
rejection path (`detail.outcome = "rejected_tenant_mismatch"` / `"skipped_foreign_tenant"`).

Records (`AuditEventType`, `types/enums.py`):
- All authentication events (login, logout, failed attempts, password changes) — v1.x, session auth
- All forecast status transitions (raw → reviewed → published) — v1.x
- All forecast adjustments (with forecaster ID and rationale) — v1.x
- All model promotion/rejection decisions (`MODEL_PROMOTED`/`MODEL_REJECTED`) — Slice E
- All API key creation/revocation (`API_KEY_CREATED`/`API_KEY_REVOKED`) — Slice C
- Station onboarding / model assignment (`STATION_ONBOARDED`/`MODEL_ASSIGNED`, additive members) — Slice E

Atomicity: each DISCRETE audited write — model promotion (`promote_artifact` /
`store_and_promote_artifact`) and station/group-model assignment (`create_station_assignment` /
`create_group_assignment`) — runs its domain mutation and its `audit_log` INSERT in ONE real
(non-AUTOCOMMIT) transaction, so a failed audit insert rolls the domain write back. The production
flow/service call sites own that transaction via the `AuditedWriter` seam (`store/audited_writer.py`),
which builds the mutation store(s) + `PgAuditLogStore` on ONE `engine.begin()` connection — no
repo-wide connection refactor (the Slice C token CLI does the same with its own `engine.begin()`
block). Non-audited flow work stays on the shared AUTOCOMMIT connection (`flows/_db.py`). Covered:
`train_models_flow` (store-and-promote), `onboard_model_flow` (promote, assignment), and the CAMELS-CH
batch's per-station STATION_ONBOARDED (the station-row `store_station`/`update_station` + its audit
row), per-station model assignment, and per-unit promotion/assignment (`onboard_from_camelsch` →
`_run_onboarding` / `onboard_model`). Each per-station / per-unit write is its OWN transaction — the
CAMELS-CH batch is deliberately NOT wrapped in a single transaction (a mid-batch failure must keep
already-onboarded stations), so partial-progress-on-failure is preserved. `STATION_ONBOARDED` is
therefore emitted PER STATION, atomically with that station's row write (there is no longer a single,
non-atomic, post-batch batch-summary audit row); observations/forcing/baselines/QC stay on AUTOCOMMIT
to preserve per-station resilience.

Rejection: Slice E's tenant-isolation check runs BEFORE any domain-state write is attempted, so a
rejection needs no rollback — it persists its rejection event (`detail.outcome =
"rejected_tenant_mismatch"` for onboard/promote/assign, `detail.outcome = "skipped_foreign_tenant"`
for the scheduled flow's unit filter) on the DURABLE (AUTOCOMMIT) connection — a separate,
independently-committed event that never rolls back with a write txn — and raises
`TenantIsolationError` fail-loud (never swallowed into a per-unit "failed" outcome), with no domain
change either way. `train_models_flow` pre-filters foreign-tenant units before the atomic write;
`onboard_model_flow` and the service `onboard_model` pre-authorize EACH unit on the durable connection
at the TOP of its iteration — before compatibility/training and before the FIRST store write — so a
foreign-tenant unit never leaves a durable TRAINING artifact/lineage row (and a skill-gate early-return
can never skip the check); `onboard_from_camelsch`/`_run_onboarding` additionally reject a cross-tenant
`(code, network)` collision (a batch that would otherwise flip an existing station's tenant) on the
durable connection before the update. A station update never changes a row's tenant.

Retention: permanent. Included in database backup.

## Least-privilege DB roles **(REALIZED, Plan 147 Slice D)**

The app no longer runs as the Postgres bootstrap superuser. `docker/bootstrap-roles.sql` (run
idempotently by the `init` service, as the DB owner, immediately after `alembic upgrade head`)
creates two scoped, non-superuser roles and grants them per-table:

- **`sapphire_api`** — connects as itself (`docker-compose.yml` `DATABASE_URL_TEMPLATE`), its own
  Docker secret (`sapphire_api_db_password`, distinct from the owner's `db_password`). Ordinary
  table SELECT excludes protected inputs; `forecasts` and `rejected_forecasts` use explicit
  safe-column SELECT without `input_lineage` (see the raw-lineage boundary below).
  `INSERT`/`UPDATE` on `access_tokens`; `INSERT`, `DELETE` on `access_token_stations`
  (`DELETE` added Plan 215 T7 — `revoke-station` and the `set-scope-mode ... tenant` cleanup both
  delete grant rows, running as this role); `INSERT`-only on `audit_log`. No write grant on any other
  domain table — matches the GET-only HTTP surface (G4).
- **`sapphire_worker`** — connects as itself (`prefect-worker`/`prefect-worker-ingest`), its own
  secret (`sapphire_worker_db_password`). Ordinary table SELECT excludes protected inputs and
  auth/identity tables; forecast parents use the same explicit safe-column SELECT, not raw
  lineage. Per-table `INSERT`/`UPDATE`/`DELETE` on the
  domain tables the flow/CLI write paths actually write (see `conventions.md` § Service users for the
  exact matrix); `INSERT`-only on `audit_log`. Plan 340 T1 also grants `INSERT` only on
  `forecast_evidence` and `forecast_evidence_blobs`. Migration 0057 rejects `UPDATE`, `DELETE` and
  `TRUNCATE` on both evidence tables for every role, including the owner. The API role has no
  evidence write grant; this slice adds no evidence endpoint.
- **Neither role** has `CREATE`/`DROP`/`CREATEDB`/`CREATEROLE`/superuser, and neither can `CONNECT` to
  the separate `prefect` database (`REVOKE CONNECT ... FROM PUBLIC` — revoking from the named role
  alone is insufficient, since every role implicitly inherits PUBLIC's ACL).
- **Migrations run as the owner** (`init`'s own `DATABASE_URL_TEMPLATE`, unchanged) — `sapphire_api`/
  `sapphire_worker` cannot run DDL, so a migration accidentally invoked under a scoped role fails
  closed rather than silently degrading privilege checking.
- **Credential separation**: the owner/migration secret (`db_password`) is mounted ONLY into `init`
  (which also needs the two scoped secrets, to bootstrap/rotate their passwords) and `postgres`/
  `prefect-server` (unchanged — `sapphire_prefect` is a documented residual, still the owner
  credential). `api`/`prefect-worker`/`prefect-worker-ingest` mount only their OWN role's secret —
  none can reconstruct the owner password.
- **Idempotent, converges fresh-volume AND in-place-upgrade deployments**: role creation is
  create-if-not-exists / else `ALTER ROLE ... PASSWORD`, and every `GRANT`/`REVOKE` is a no-op when
  already applied. Re-running `init` on an existing database (the standard upgrade procedure,
  `cicd.md`) re-applies the same bootstrap harmlessly and picks up a rotated password.
- **`sapphire_prefect` is UNCHANGED by this slice** — `prefect-server` still connects with the owner
  credential against the separate `prefect` database (`docker/init-db.sh`). Realizing a distinct
  scoped `sapphire_prefect` role is a documented residual, not built here.

### Three levels of database identity (Plan 510)

No routine job runs on the owner (superuser) credential.

1. **The owner** — deploy time only: migrations, `docker/bootstrap-roles.sql`, and the declared tenants
   (Plan 513). Mounted only into `init`, `postgres` and `prefect-server` (the documented Prefect residual
   is not closed by this plan).
2. **The runtime roles** — `sapphire_api`, `sapphire_worker`. Grants unchanged by Plan 510.
3. **`sapphire_operator`** — a narrow, database-limited role for the rare DHM delivery replacement
   (`cli/import_dhm_delivery.py`: `stations`, `replace`, `qc`). It is always created `NOLOGIN`; a password
   exists only when the host deploys the `docker-compose.operator.yml` overlay (secret
   `./secrets/sapphire_operator_db_password`, consumed by `init` and the `operator` service only). The base
   compose file is unchanged and needs no such file.

**What the operator may do** (measured against a real Postgres by
`tests/integration/db/test_operator_grant_matrix.py` and compared with the real role in
`tests/integration/db/test_operator_role.py`; the matrix is `SELECT` on `tenants`; `SELECT`/`INSERT` on
`stations`; `SELECT`/`INSERT`/`DELETE` on `rating_curves`; `SELECT`/`INSERT`/`UPDATE`/`DELETE` on
`observations`; `INSERT` only on `audit_log` plus `USAGE` on `audit_log_id_seq`). `SELECT` on `tenants` and
`stations` is what the guard's own lookups need. The operator holds **no** `UPDATE`, `DELETE` or `TRUNCATE`
on `stations` or `tenants` and no `UPDATE` on `rating_curves`, no access to `observation_versions` (the
import writes none) and no `SELECT` on `audit_log`: the store drops the implicit `RETURNING` so an
`INSERT`-only role can append. Its `SELECT` reaches every tenant's rows; the guard limits integrity, not
confidentiality or availability (a role holding `DELETE`/`UPDATE` can still `LOCK TABLE` or
`SELECT ... FOR UPDATE` and block ingest, and advisory locks are open to any role).

**The row limit is the database's, not Python's** (migration `0067`). A `SECURITY INVOKER` row trigger with
`WHEN (session_user = 'sapphire_operator')` on `observations` and `rating_curves` (INSERT, UPDATE, DELETE),
a matching `BEFORE INSERT` trigger on `stations`, and `BEFORE TRUNCATE` statement triggers refuse every
write whose row is not (a) tagged with the delivery id (`DELIVERY_ID`) **and** (b) at a station of tenant
`chwrr`, looked up by code (never by id). It checks `NEW` on INSERT, `OLD` on DELETE and both on UPDATE, and
an UPDATE may not change `station_id`, `delivery_id` or a curve's validity dates. It is a positive allow:
if `chwrr` does not exist the guard refuses. `session_user` (the login) is tested, not `current_user`, so a
`SECURITY DEFINER` function owned by the owner cannot slip past it. Relations are schema-qualified and the
function's `search_path` is pinned to `pg_catalog, public, pg_temp` (`pg_temp` last), so a temporary table
named `stations` or `tenants` cannot influence it; **temporary tables themselves are not prevented** (`TEMP`
is granted to `PUBLIC` by default and revoking it from the operator alone would not remove it). A second
delivery later is a deliberate migration that extends the allow-list.

**Bypasses and the preconditions they rest on** (none is granted): `DISABLE TRIGGER` needs table ownership;
`session_replication_role` needs superuser or an explicit `GRANT SET`; `TRUNCATE` needs the grant (and is
guarded); `SET ROLE` needs a role membership (the bootstrap revokes every membership and resets role-level
settings on every run). No function in `public` that writes the three tables is `SECURITY DEFINER`
(asserted by a test).

**The bootstrap converges to the safe state.** It grants the operator table privileges only while every
guard trigger exists and is enabled (`pg_trigger.tgenabled` `'O'` or `'A'`; `DISABLE TRIGGER` leaves the row
present with `'D'`). If any is missing or disabled it grants nothing, revokes what the role holds, logs a
warning, and lets `init` continue (an aborted `init` would leave the whole stack down). The guard
migration's downgrade revokes the operator's DML **before** dropping the triggers, so the migration-only
path leaves no unguarded interval.

**Residual: forged audit rows.** `INSERT`-only on `audit_log` still lets the operator write rows with any `event_type` and `actor_type`; the append-only trigger stops edits, not forgeries. Attribution of who ran a job is deferred to v1.x.

**Overhead of the guard triggers (measured, not conclusive).** 20,000-row `store_raw_observations` batches into `observations` through a real non-owner login, 7 runs per configuration and phase, seconds as min / median / max. With the guard triggers (two phases): insert 3.45 / 6.20 / 10.07 and 6.28 / 6.50 / 6.65; upsert 6.39 / 6.98 / 12.96 and 6.52 / 6.60 / 6.75. With them disabled (two phases): insert 5.83 / 6.22 / 6.47 and 5.94 / 6.31 / 6.63; upsert 6.07 / 6.36 / 6.66 and 6.01 / 6.53 / 7.31. Median differences were between about 0 and 10 %, inside the spread of the runs (one phase had a 3.45 s and a 12.96 s outlier). Measured once on a laptop container, single station, no concurrent load; this shows no large cost, not that the cost is zero.

**Temporary objects, large objects and session termination.** The operator may create temp objects and large objects (`TEMP` and `lo_creat` are granted to `PUBLIC`). The bootstrap's FIRST step puts the role in its safe state before any preflight that could abort: its backends are terminated first (an operator transaction can hold locks that block the restriction itself, e.g. `ALTER ROLE ... PASSWORD` on its own pg_authid tuple), then `NOLOGIN`, memberships and every table/sequence/schema/database privilege are revoked as autocommitted statements under a short lock timeout with up to three retry rounds (if the role still cannot be neutralised, `init` aborts loudly; every other operator-controlled condition is survived). This stage is bounded per statement by `lock_timeout` (2 s), not by a round deadline: the worst case is about (number of blocked statements) x 2 s x 3 rounds, measured at about 8 s wall time for a single blocked `ALTER ROLE`, and it grows with the number of blocked membership revokes. Membership and database revoke errors are covered by the re-check only through the resulting no-login / no-ACL state, whose scope is exactly what the block revokes (relations and schema `public`); a stale grant in another schema is removed later by `DROP OWNED`, not treated as a failure, so the restriction is committed before the sweep and a reconnect is refused; then the backends are terminated twice with a short bounded wait that clears the statistics snapshot (a survivor only raises a WARNING; its privileges are already revoked), and large objects it owns are unlinked. This happens when the bootstrap SQL runs, i.e. after the wrapper's checks and the migrations. Every bootstrap therefore ends operator sessions; that is intended (deploys stop workers first, and an in-flight import rolls back atomically). The backup role's RLS preflight and the operator ownership preflight ignore objects of ANY class in a temporary namespace (the schema is resolved with `pg_identify_object`) but still refuse persistent ones; `DROP OWNED` runs under `lock_timeout = 10s`. The guard check compares each trigger's `WHEN` expression, case-sensitively, with PostgreSQL 16's canonical `(SESSION_USER = 'sapphire_operator'::name)`, so a missing, `false`, wrong-role, differently-cased or spaced condition counts as a broken guard (a PostgreSQL major upgrade that changes the canonical text would make the check refuse safely, not grant).

**Attribution.** Audit rows are written as the system actor (`AuditEventType.DELIVERY_IMPORTED`, counts
only, in the mutation's own transaction; a failed audit write rolls the mutation back). They carry no
operator identity; per-person attribution is deferred to v1.x. Rejections stay log-only.

**Tenant lock.** `lock_tenant` is a plain `SELECT` plus `pg_advisory_xact_lock(namespace, key)` for every
caller and role (`FOR UPDATE`/`FOR SHARE` need `UPDATE` on `tenants`, which no routine role holds).
Advisory locks coordinate cooperating callers only; an older image still using `FOR UPDATE` does not
exclude a newer one. `cicd.md` § DB role bootstrap has the operator's activation, rotation, revocation and
rollback procedures.

See `docker/bootstrap-roles.sql` (the grants), `docker/bootstrap-roles.sh` (the psql wrapper, reads
`$DATABASE_URL` + the two scoped-password secret files), `docker/entrypoint.sh`
(`$DB_PASSWORD_SECRET` — which secret file a given service reads), `cicd.md` § DB role bootstrap
(deploy/rotation runbook), and `conventions.md` § Service users (the full grant matrix).

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

### Protected provisional-discharge storage (dormant)

Migration `0068` adds physically separate `provisional_discharges`,
`measurement_feed_evidence`, `rating_reference_proofs` and
`provisional_discharge_permissions`. Ordinary observations, their sources, readers,
training inputs and QC semantics are unchanged. These relations contain restricted
copied measurement/QC/curve evidence. Do not publish their content or diagnostics.

The API, worker, delivery-only `sapphire_operator` and publication-health roles have
no proof, feed-evidence or provisional-data privileges. Runtime SELECT enumeration excludes these relations entirely, including during
a failed bootstrap. Stale table and column grants (including PUBLIC grants) are
removed before runtime re-grants and later backup preflights. Successful bootstrap
grants API/worker only SELECT on the gate's
`tenant_id` and `state`. The migration also strips inherited default relation grants.
There is no proof/association approval writer, activation command, credential change,
new role or role membership. Backup retains its existing protected full-database
read capability; this is not consumer access or new publication authority.

Role-independent `SECURITY INVOKER` triggers refuse evidence UPDATE, DELETE and
TRUNCATE, including owner DML. Gate deletion/TRUNCATE is also refused. The sole gate
UPDATE exception is the actual table-owner session changing enabled to disabled
without changing any other field. Re-enable and metadata rewriting remain refused;
runtime roles have no gate write grants. Tenant advisory transaction locks serialize
this fail-closed disable with append without adding UPDATE privileges for row locks.
Provisional INSERT fails without an explicit enabled tenant permission. Invoker functions pin their search path and schema-qualify their reads.
Composite FKs bind tenant/station/measurement/curve/proof identities. The store and
SQL guard compare persisted measurement, exact QC generation/flags, current curve
content and persisted proofs under locks. Content hashes include all copied facts,
not run/capture times. Changed facts append; original evidence is never upserted.
A curve-table SHARE lock excludes concurrent new candidates as well as curve edits;
this is deliberately conservative and unsuitable for unreviewed high-volume wiring.

`MeasurementFeedEvidence` is a narrow explicit attestation, not inferred from
`ObservationSource.MEASURED` or today's adapter config. It pins the measured-value
snapshot to a sanitized endpoint/API station identity and evidence reference,
actor and time. Restated values need new evidence; QC-only changes reuse the feed
attestation but create new conversion lineage. Reference proofs pin exact curve
content, confirmed reference labels, metre units and an explicit finite metre offset,
including evidenced zero offsets. Missing or incompatible evidence holds conversion.

**Still held:** authorized feed/proof recording, applicable-rule configuration
verification, complete deployment/rollback inventory and activation capability,
forecast-class isolation and downstream invalidity/exclusion/read contracts. The
permission relation is guard scaffolding, not a usable activation protocol; no
production role can write it. Disposable owner fixture seeds are tests, not operator
instructions. Do not enable this path or deploy a conversion writer from this slice.

The SQL insert guard establishes snapshot, identity, QC and domain consistency; it
**does not recompute discharge**. Owner-only direct SQL can submit a different finite
Q with matching content and SHA. The typed append store re-converts and refuses that
mismatch. No runtime role can INSERT this data. Numerical authority and this database
boundary must be explicitly reviewed before any later writer grant or activation;
do not describe the current SQL trigger as proof of numerical conversion.

The generic admin table browser excludes all four protected provisional relations
from inventory/COUNT and both detail/rows-by-name paths. Admin authentication is not
permission to expose protected snapshots; excluded names return 404 without granting
DB reads. Ordinary authorized table browsing remains available.

Persisted capture times cannot exceed PostgreSQL `clock_timestamp()`, enforced by
both the append store and INSERT trigger. A caller cannot expire a current curve by
supplying a future capture time. The pure converter keeps its injected time for
isolated tests; original curve validity and measured timestamps are not rewritten.
Reference labels and offsets are evidenced assertions: equal labels do not imply zero
offset, and different labels do not imply a nonzero offset. No datum is inferred.

Protected parent FKs intentionally hold delivery replacement that would delete a
referenced measurement or curve. There is no delete cascade or evidence cleanup to
bypass that hold. Operator replacement must stop and seek an authorized retention
resolution. Raw owner SQL/driver exceptions can echo restricted bound content; no
claim is made that owner SQL errors are redacted. These stores are unwired, have no
public error route, and no runtime SQL writer is granted. Any future wiring must
project safe errors rather than expose exception text or database parameters.

**Supported write isolation is READ COMMITTED only.** The typed append store and
SQL guards reject REPEATABLE READ, SERIALIZABLE and other isolation settings before
protected insertion. This covers feed/reference attestations, provisional values,
permission insertion and owner disable. Advisory/table locks do not refresh a fixed
MVCC snapshot: without this refusal, a transaction could miss a committed disable or
newer curve. READ COMMITTED wait-path tests observe actual database blocking before
committing the other session, then prove gate/candidate rechecking refuses the append.
Immutable historical reads and the pure converter are not restricted to this isolation.

Conversion retains the original lower validity bound: measured time must be at or
after the newest curve's `valid_from`. There is no older-curve fallback or timestamp
shift. Once that curve is expired at actual capture time, readings within its original
valid interval are allowed only as provisional test-purpose inputs, as are readings
at/after expiry. This does not certify each input as temporally expired and never
upgrades any result into the ordinary observation/valid-forecast path.

Future nonowner writers must have a separately reviewed source-lock privilege matrix:
TABLE SHARE and FOR SHARE may require underlying table privileges beyond SELECT.
No such grants are added here. Authoring must resolve contradictory feed attestations
rather than infer source from today's config; there is currently no authoring/selection
workflow. Pydantic boundary ValidationError text can include protected payloads too.
Future public or scheduled wiring must project safe errors instead of logging/returning
raw validation/driver exceptions. These remain explicit holds, not completed safeguards.


### Dormant rejection and publication guards

Migration `0070` adds normal-publication exclusion and dormant rejection classification.
Publication guards reject current/replaced TEST references and reviewed/published TEST
states even through direct SQL. Typed rejection capture validates the whole batch before
encoding or writing; rejection payload construction gains no throwing validation.
TEST rejection INSERT/COPY
unconditionally refuses, independently of unchanged `0069`. Existing append-only guards
protect rejection class and lineage. API/workers cannot activate anything; the delivery-only
operator gains no authority or privileges. No new private relation or catalog grant is added.

Ordinary review listing checks the store's public read-only purpose after station and
tenant authorization, before any count or forecast read. Unknown/non-STANDARD purpose
returns a safe 503 without metadata/counts, even beyond the last page. No-grant and
foreign-tenant refusals keep their existing 404 ordering. Review serialization also
refuses TEST payloads. Publication readers filter the primary forecast's class;
replaced/event-reference safety relies on database guards and migration preflight,
not reader sanitization of arbitrary states created after disabling those guards.

Rejection lineage SQL checks only top-level shape, not source/tenant linkage or actual
consumption. These remain explicit preactivation holds. Revision `0071` protects both
`rejected_forecasts.input_lineage` and `forecasts.input_lineage` as described below.
This partial reader boundary does not authorize scheduled/public TEST wiring.

### Dormant forecast data-use partition

Migration `0069` refuses every `expired_rating_test` forecast INSERT at PostgreSQL,
including owner SQL and COPY under normal execution (not deliberate DBA guard
disabling). `data_use` and consumed lineage cannot be changed by
UPDATE. No output enable flag, session override or runtime/operator grant is added.
The provisional-input permission does not attest output deployment compatibility.
A separate reviewed rollout must replace the unconditional guard, close ordinary
raw-SQL/alert/state readers and authorize the required narrow join writes.
Normal publication exclusion is separately enforced by `0070`.
Revision `0071` removes runtime/PUBLIC table-wide SELECT and protected-column SELECT
on both forecast parent tables. API/worker grants list allowed columns explicitly;
STANDARD stores request a NULL lineage projection, not the protected column. Bootstrap
excludes both parents from automatic catalog grants. Owner and approved backup access
remain unchanged. Downgrade retains these ACL restrictions.

Bootstrap commits early revokes before later preflights. A failed transactional migration
instead rolls its changes back: failure is not proof that the old ACL was repaired.
Inherited/SET ROLE authority, catalog-dependent owner views (including whole-row reads),
and unknown executable permanent application SECURITY DEFINER functions refuse
preflight rather than silently changing unrelated objects. All three object scans exclude
actual PostgreSQL temporary namespaces, including parsed-body functions and views held
by live runtime sessions; permanent unsafe controls still refuse. Existing managed
publication-health restrictive attributes/membership normalization runs before bootstrap
preflight, after operator safe-state, without changing LOGIN/credentials or later ownership
checks and narrow grants. The two maintained publication-lock functions
and extension-owned functions remain trusted infrastructure. Catalog inspection cannot
certify arbitrary owner-created copies or future owner DDL; review those separately.

Legacy forecast list/detail/data readers, dashboard counts/latest/status, and generic
browser parent/child/evidence/blob reads filter STANDARD before pagination. Superseded
STANDARD history remains readable. Shared blobs remain visible through a STANDARD
reference. The browser never projects either raw lineage column. This is partial T1d:
Remaining evaluation/state/health paths remain held. Synthetic full-dump/restore
and narrow publication-health evidence is scoped [below](#mixed-backup-restore-canaries). The bounded factory/training/observation-alert canaries are scoped
[below](#ordinary-input-isolation-canaries). The named operator tools follow the contract below. Publication consumer pre-query boundaries are described below.
Modern forecast detail/station/rejection routes and Forecast Lab require explicit
STANDARD store purpose before forecast counts/pages/detail or scalar cycle reads.
Unknown/TEST dependencies return safe 503; wrong-class detail returns the same 404 as
absence, while mixed pages/snapshots fail wholly without exposing counts or values.
Known station/tenant/human authorization precedes these checks. By-ID misconfiguration
returns uniform 503 before lookup: station-specific scope is not yet knowable.
Shared detail serialization is a backstop for other callers, not their pre-query gate.
Synthetic disposable structural tests may remove only the refusal trigger transactionally;
this is test setup, never an operator activation procedure.

Role bootstrap excludes `forecast_input_stations` from catalog runtime SELECT and
revokes its stale table/column ACLs in the existing early safe-state block, including
on later preflight failure. Backup SELECT is preserved. Generic table-browser inventory,
detail and rows never admit this relation. Combined contributor evidence checks use
the store's purpose; other-class evidence is unavailable, not implicitly trusted.
SQL snapshot checks are bounded structure/identity checks; strict canonical parsing
remains a type/store obligation. Review any future direct writer before activation.

Snapshot/decode validation errors suppress raw Pydantic details and exception chains.
Purpose-bound TEST store SQLAlchemy failures raise a safe `StoreError` for that store
operation, without raw statement/parameters in conventional rendered tracebacks. This is
not guaranteed flow termination: existing best-effort rejected capture catches and logs
storage failures. Renderers must honor suppressed context; trusted inspection of Python
`__context__` is outside this rendered-output boundary. STANDARD storage errors retain their existing
raw exception contract; retry/conflict decisions are not translated. This is not a global
SQL logging sanitizer: PostgreSQL statement/error logs, psycopg/debug parameter logging,
SQL echo and direct owner SQL remain privileged tooling, not authorized TEST wiring.

Future nonowner TEST contributors also require separately reviewed read/write authority:
the `0069` SECURITY INVOKER lineage trigger reads contributor raw lineage, denied by
`0071`. Do not grant that column or add a SECURITY DEFINER escape to enable output.
The operator tooling boundary is documented below. `scripts/restore-rehearsal.sh`
retains its narrower checks; expanded mixed-content inspection belongs to the
[separate local canaries](#mixed-backup-restore-canaries).
Beyond the [bounded input canaries](#ordinary-input-isolation-canaries),
beyond the [bounded hindcast canaries](#hindcast-input-isolation-canaries),
skills/components/onboarding/calibration, state/health and T1c/T3 consumption
remain held. See the [separate mixed restore canaries](#mixed-backup-restore-canaries)
for bounded local preservation/publication-health proof, not deployment readiness.

### Bounded publication consumer isolation

The canonical [publication consumer contract](../spec/types-and-protocols.md#bounded-publication-consumer-isolation)
requires purpose refusal before lookup/selection and whole-batch class validation
before metadata. Authentication and known station/tenant/publication policy gates
retain their order. By-ID misconfiguration yields uniform 503 before station scope
is knowable; healthy STANDARD missing/foreign/wrong-class details remain 404.
This adds no grants, publication permission or TEST writer authority.

The publication slice alone does not close ordinary-input/evaluation canaries;
see the later [bounded input evidence](#ordinary-input-isolation-canaries). Mixed
backup/restore proof is scoped by the [local canaries](#mixed-backup-restore-canaries);
T3 state/health/alert/actual-consumption work and activation remain held.
The named operator tooling projections follow the contract below.
Full backups must continue to preserve both forecast classes and protected lineage.


### Operator forecast tooling isolation

`scripts/forecast_feed_resilience.py` is the loose domain implementation;
`scripts/plan100_forecast_feed_resilience.py` remains its direct-CLI compatibility
wrapper. Neither is packaged as a runtime CLI or copied into the runtime image.
All five subcommands, flags/defaults, priority reconciliation safeguards and the
`plan100_step0_snapshot.json` filename/top-level JSON keys are retained. The wrapper
delegates to its sibling loose file: both files must travel together, preferably in
a repository checkout. Standalone copying of only the legacy file is not supported.

These queries require a compatible migrated database. `data_use` starts at `0069`:
the new standing counter query is incompatible with pre-`0069` schemas, whereas the
former query did not require that column. The safe projection and runtime role
contract require `0071` and corresponding role
bootstrap. The supported rollout requires schema `0071` and its roles. The last
root-observed host schema `0067` is not compatible; this code grants no live invocation
or upgrade authority. Standing snapshot retains its existing empty-counter behavior
when the counter query fails. Empty counters are not compatibility or successful-read
proof. No pre-`0069` fallback may silently drop the class filter.

`blackout_forecasts` selects an explicit reviewed safe forecast-header projection
for `STANDARD` only. It never selects or emits `input_lineage`, forecast values or
linked evidence. All other existing header fields remain. The issue-time window
is lower-inclusive/upper-exclusive; ordering remains issue time, station and model.
No status/QC/current predicate is added: superseded STANDARD history stays present.
Both blackout windows retain fixed parameterized PostgreSQL timestamp binding from
CLI strings. The alert window's prior VARCHAR/timestamptz binding failure is repaired
without a new timestamp parser, defaults or timezone policy; boundaries/order stay
unchanged for default ISO and explicit offset inputs.
This operator export is not authorized consumer/publication output. Future columns
require explicit projection review rather than automatic inclusion.

Standing snapshot has an intentional operator JSON key change. The old ambiguous
`forecasts` and `forecasts_latest` keys are removed. `live.counters` now contains:

- `forecasts_standard_count` and `forecasts_standard_latest_issued_at`;
- `forecasts_expired_rating_test_count` and
  `forecasts_expired_rating_test_latest_issued_at`;
- `forecasts_all_classes_audit_count` and
  `forecasts_all_classes_audit_latest_issued_at`.

Values remain strings; an empty-class latest timestamp is `-`. Both class histories
include superseded rows. All-class fields and their rendering are audit-only, never
ordinary freshness. None of these issue-time aggregates establishes freshness, QC,
validity or model coverage. Other counters, health/findings and read-only transport
are unchanged. No in-repository keyed consumer of the removed names was found;
external snapshot consumers may need an explicit update. This is not a claim that
the operator JSON schema stayed unchanged.

Disposable role-backed tests prove safe projection under actual API/worker grants,
class-disjoint history and raw-lineage denial. The full standing counter query is
exercised as an operator aggregate; forecast-only branches are also tested under
runtime safe-column grants. No role privileges are expanded. Structural TEST seeds
remove only the dormant forecast writer guard inside a rolled-back test transaction;
this is not activation authority.

This closes only the named tooling readers. Beyond the later
[bounded input canaries](#ordinary-input-isolation-canaries), remaining evaluation,
T1c/T3 actual-consumption/state/health/alert work remains held. Separate
[mixed restore canaries](#mixed-backup-restore-canaries) cover local preservation/proof only. Full backups must retain both classes and protected lineage.

### Ordinary input isolation canaries

`tests/integration/services/test_forecast_data_use_isolation.py` exercises actual
factory-created PostgreSQL observation stores. API and worker roles cover ordinary
fetch/batch/latest plus protected-read denial. Only the worker role executes
station/group training target assembly and observation-alert service canaries.
Nonempty typed `QC_PASSED` ordinary `MANUAL_IMPORT` and historically valid
`RATING_CURVE_DERIVED` discharge remain readable. Same-time and newer extreme
provisional discharge, with exact typed measurement/feed/curve/reference evidence,
cannot alter these ordinary results. Ordinary control changes alter targets and
alerts; provisional-only history neither raises an alert nor resolves an existing one.

Training evidence covers two hourly-aligned discharge targets within a four-hour
window, not complete input coverage. Requirements exclude forcing/static features;
no forcing data is fetched, and the real basin store is passed but not read. Existing
SAP3 test doubles supply requirements only: no model training, prediction or FI
execution runs. Ordinary history is seeded with valid typed QC state; this is not
Stage1 QC execution evidence or a new requirement for optional QC flags/rule metadata.
No ordinary-QC negative control or additional unit-test cases are claimed here.

This is evidence for existing physical storage/factory separation, not a runtime
filter or provisional-reader injection contract. Synthetic owner permission exists
only in disposable rollback transactions; runtime protected reads remain denied.
No TEST forecast writer, publication guard or role grant is bypassed.

This bounded slice does **not** close evaluation or onboarding. Separate
[hindcast canaries](#hindcast-input-isolation-canaries) cover their named input boundary;
skills, components, onboarding and calibration remain held. The [mixed backup/restore canaries](#mixed-backup-restore-canaries)
separately cover local preservation and narrow-principal publication-health proof. T1c consumption linkage/activation and T3 complete-lookback
lineage, state, freshness and forecast/combined-alert isolation remain held. Observation
alert evidence is not forecast-alert evidence or authority for operational use.

### Mixed backup restore canaries

`tests/integration/ops/test_mixed_forecast_backup_restore.py` and its scoped fixture
exercise the actual exported-snapshot dump worker using `sapphire_backup` over TCP.
The test injects the child environment and relocates `pg_dump` through `docker exec`
into its owned source container. It proves snapshot/principal behavior, not secret-file
loading or password validation on that dump leg; its authentication policy is not inferred.
A full custom-format dump preserves nonempty STANDARD and TEST forecasts/rejections,
values, raw lineage, protected provisional measurements/feed/reference/curve snapshots,
content fingerprints and evidence. Exact as-stored row manifests survive restore into
a separate fresh disposable database. Shared blobs remain deduplicated and TEST-only
blobs remain present. Exact compared relations are `forecasts`, `forecast_input_stations`,
`forecast_values`, `forecast_evidence`, `forecast_evidence_blobs`, `rejected_forecasts`,
`observations`, `rating_curves`, `measurement_feed_evidence`, `rating_reference_proofs`,
`provisional_discharges`, `provisional_discharge_permissions`, and the three
`forecast_publication_decisions`/`forecast_publication_selections`/`forecast_publication_events`
relations. The nonempty input-station association is also checked against fixture IDs,
station and tenant. Other relations are not claimed as exact-manifest assertions;
parent foreign keys and post-restore health checks are distinct evidence.

A late ordinary publication decision is absent at the dump cutoff. The selected-history
fixture uses an owner status update, matching the stored superseded-selection contract,
not executing a retry/supersession workflow. Real publication-store reads for that
publication station retain the selected superseded STANDARD result.

Only the two exact dormant TEST refusal triggers are temporarily removed during
synthetic seeding, then their exact definitions are restored before source commit/dump.
Negative writes prove refusal remains active. Synthetic provisional permission is
disabled before dumping. Publication guards remain enabled throughout. Created source
and target container IDs are recorded before dump/recreate/restore, and cleanup checks
only those owned handles. They are registered before startup so known partial-start
handles can be cleaned up. Absence requires explicit Docker object-not-found for that ID;
daemon/authentication/CLI errors cannot pass. Cleanup attempts every known handle and
preserves an original failure if cleanup also fails. Resource-record write errors are
collected and reported after cleanup, never allowed to skip stop/dispose/absence checks.
Deterministic fake-handle tests inject readiness, record-write and stop failures without
Docker. Testcontainers' framework-managed
reaper is not adopted or stopped by this fixture. No development deployment or existing
database is involved. This fixture is a disposable committed restore seed, unlike the
rollback-only ordinary-input fixture; exact refusal guards are back before commit.

Restore uses the rehearsal's single-transaction, exit-on-error, no-owner/no-ACL flags.
Unmodified role bootstrap then establishes runtime ACLs. Actual API/worker readers
retain ordinary positives, exclude TEST, and deny raw-lineage/wildcard/provisional,
input-station/feed/proof and protected permission-column reads. Permitted `tenant_id`/
`state` permission projections remain readable; not every permission column is denied.
The actual backup principal retains full reads. Owner and permitted API attempts to
create TEST publication references fail under unchanged guards.

The actual worker preservation writer appends validated STANDARD and TEST attestations.
The dedicated publication-health principal still cannot SELECT forecasts. Only exact
legitimate ordinary publication attestations/decisions advance proof counts; TEST
preservation alone does not. Persisted TEST attestation, invalid/verified health states,
identity/target/retention fields and proof counts are checked. A TEST forecast paired
with a legitimate ordinary decision fails without inventing a TEST publication decision.
Missing attestations and mismatched decisions fail closed; ordinary proof writes are
idempotent. This is supported guarded composition, not a new
class join, broader grant or universal defense against guard-disabled corruption.

The negative content comparison changes an expected manifest in memory, not a dump.
It proves comparator sensitivity, not corrupted-dump detection. Expanded mixed-content
inspection belongs to these tests; the unchanged restore-rehearsal CLI still verifies
its existing representative evidence chain and pending-publication guarantees. Health
image-archive descriptors are synthetic: this is not actual image archival, off-box
replication, retention, real-data recovery or deployment-readiness evidence. Remaining
skills/components/onboarding/calibration, T1c and T3 state/freshness/forecast-alert/
actual-consumption holds remain; [hindcast canaries](#hindcast-input-isolation-canaries)
cover only their named ordinary-input boundary. These are baseline-green regression canaries for
existing behavior, not runtime fixes or manufactured failing-source evidence. Permission
refusals and expected-manifest negatives prove only their named boundaries. No activation
or overall T1d closure is authorized.


### Hindcast input isolation canaries

`tests/integration/services/test_hindcast_data_use_isolation.py` exercises station and
group hindcast services with real factory observation/station/basin/group readers under
`sapphire_worker`. The real `PgHindcastStore` uses its public transaction factory to
write through savepoints on that **same worker-role connection**. Each write asserts
`current_user`. The default independent committed writer transaction and production
factory writer composition are **not** exercised; an owner engine must not silently
stand in for that role across a new transaction.

The existing deterministic ReferenceFI model is called through the real FI adapter.
Only its public `predict` call is recorded; model behavior and requirements are unchanged.
The fixture covers four hourly issues, three lookback buckets and three horizon steps,
one station or two group members. Expected FI target/forcing frames, units, grids and
keys are asserted independently. STATION uses FI's fixed `station` key; GROUP uses the
actual station-code resolver. Nonempty offline forcing satisfies the reference model's
`precipitation_forecast` declaration, using the existing mean fallback for that synthetic
parameter name. This is not a real-provider, meteorological aggregation or forcing-store
validation. No static input is declared; the basin store is passed but not read.

Dense 15-minute ordinary discharge includes valid historical rating-derived Q before
curve expiry and manual history in later buckets. Typed measured-level/feed/reference
and provisional content, links and fingerprints are checked as owner. Same-time, older
and newer extreme provisional Q falls inside consumed lookback buckets at every issue.
For each issue, a protected point at issue minus 7m30s is strictly newer than the latest
ACTUAL stored ordinary row in that consumed window and remains before the issue.
The converter accepts those timestamps; typed content and valid curve chronology remain
checked, alongside the older and same-time controls.
The worker's protected provisional SELECT remains denied. Provisional additions leave
exact FI inputs and stored hindcast semantics unchanged; a legitimate ordinary change
alters the expected target bucket by the expected amount and leaves other buckets and
members unchanged. Reference predictions ignore Q numerically: equal outputs alone are
not the sensitivity evidence. Fresh output UUIDs alone are excluded from cross-scenario
comparison; run, artifact, QC, time/grid, values and multiplicity remain compared.

Provisional-only station history produces explicit insufficient-data outcomes with no
FI call or stored result. A group retains its ordinary healthy member while reporting
the provisional-only member's missing input. This is the existing hindcast contract,
not proof of operational T3 group fan-out. All data is synthetic and rolled back;
ordinary typed QC state is not a Stage1 QC execution claim. Only the fixture's newly
owned disposable container is cleaned up, using the reviewed strict cleanup helpers.
No TEST forecast/publication guard or grant is bypassed.

These canaries exposed a real clean-baseline group temporal defect: legacy stacking
moved the first aligned future bucket into past forcing. The group-only repair now
preserves the assembler's partitions. The eight-case public-FI matrix in
`tests/unit/services/test_hindcast_group_partitions.py` covers hourly/daily cadences,
aligned/off-boundary issues and H1/H3: four aligned cases failed before the repair,
while four off-boundary controls passed. Both forcing partitions and target history
are compared against independent values and grids. The matrix uses the existing mean
fallback for forcing, not the declared-aggregation path or a new scientific policy.
H1 previously failed before public
`predict`; the contract-consistent test recorder returns typed input failure for short
inputs that reach it. Recorder-only mixed result tests do not certify adapter/service
handling of omitted members or add an adapter row-count gate.

This preserves the CURRENT shipped Plan239 left-label convention, not Plan267 or held
phase-policy work. The helper itself is label-agnostic. Public same-worker hindcast-store
writes also accept an aligned first valid time equal to issue time. This synthetic test
output convention is separate from the unchanged ReferenceFI model's output convention.
The corrected group inputs can change predictions; material historical output/score
impact has not been measured. Short input does not imply short output. No historical
rows or scores are recomputed, deleted, superseded or backfilled here. Owner assessment
and a separate decision are required before relying on potentially affected historical
results; this does not authorize host queries or remediation.

They do not prove skills/POOLED/BMA, component derivation, onboarding callbacks/history,
calibration/baselines, actual model training, artifact discovery/promotion, scheduled
flows, real weather, state, freshness or forecast/combined alerts. T1c/T3 activation and
all remaining consumer/coverage holds stay in force; no full T1d closure is claimed.

### Clean skill acceptance before isolation

The earlier PR380 checkpoint passed six clean SINGLE/POOLED/BMA task cases with
synthetic stored hindcasts and generation writes OFF/ON. It established real
worker persistence/readback of complete scalar/diagram outputs, including FLOAT
NaNs and supported JSONB rate nulls. It did not include protected intrusions or
ordinary-input perturbations. The later measured scope is recorded below.

### Stored-hindcast skill isolation canaries

P2a passed six clean/mixed pairs (twelve task calls). P2b passed twelve first-half
or second-half sensitivity cases (thirty-six task calls). Each sensitivity case
used an independent clean baseline and two separately rolled-back perturbed
scenarios. These were real factory-store worker calls, not model executions.

Owner setup checked all 13 fields of 64 ordinary discharge records and the full
lineage, QC, tenant, content and fingerprints of 48 protected conversions. Every
consumed bucket in both chronological halves contained same-time, older and
strictly newer provisional extremes, checked against ACTUAL stored ordinary
rows. Measured water-level parents remained worker-readable but were excluded
by the discharge parameter; SELECT denial applied separately to the protected
provisional relation. The scoped database fixture did not leak its URL.

One named ordinary raw quarter increased by 24 in each sensitivity scenario.
Forecasts and protected lineage stayed fixed. The expected discriminating score
and ON generation changed at the same invocation and clock. Protected additions
left complete paired outputs and generation references unchanged. Only newly
minted output primary UUIDs were excluded across scenarios; ordinary provenance
and same-source readback remained exact.

See the [oracle and metadata contract](../spec/types-and-protocols.md#paired-stored-hindcast-skill-isolation-contract)
and [runnable regression route](../touchpoint-maps.md#stored-hindcast-skill-isolation-regression).
This proves only the named rollback-local paired/sensitivity cases. The subsequent
sequential replay evidence is below. P3 negatives, durable AUTOCOMMIT atomicity,
historical impact, scientific calibration and activation remain unproved. It does not close whole T1d, T1c/T3
or Effort 354, or authorize deployment or historical remediation.

### Sequential skill replay acceptance status

The six-case replay gate passed all thirty calls, including identical-ID replay,
new-invocation selection, old-ID non-promotion and changed-content publication. Its
[contract](../spec/types-and-protocols.md#sequential-skill-replay-acceptance-contract)
and [route](../touchpoint-maps.md#sequential-skill-replay-regression) preserve real
worker stores, protected-present lineage and the same rollback connection. No new
privilege, durable recovery claim, P3 evidence or activation authority follows.

### Protected-only skill-input acceptance status

The bounded P3a gate passed six pairs/twelve task calls across SINGLE/POOLED/BMA
and generation OFF/ON. Each unchanged positive was rolled back before its
protected-only negative. See the [contract](../spec/types-and-protocols.md#protected-only-skill-input-acceptance-contract)
and [regression route](../touchpoint-maps.md#protected-only-skill-input-regression).

Owner `session_user` and `current_user` were both `test` before the exact64-row
ordinary-discharge fixture deletion. This matters because migration0067 checks
`session_user`, not a role switch. The 64 Q IDs were disjoint from 48 measured
parents and had no referencing rows in the three observation-FK relations.
Protected conversion/feed/proof/permission/curve content was checked as owner
before handoff only. Worker parameter-scoped inventories independently retained
48 water levels and all requested hindcasts while exposing zero ordinary Q.
The denial probe targeted `provisional_discharges`; it does not claim that every
permission-table SELECT is denied. No privileged read was used during computation.

Real factory stores and both public readers ran as worker on the same rollback
connection. All negative results, skill INSERT events and persisted/public scopes
were empty. Positive INSERT controls and listener removal passed. Expected
empty-input structlog diagnostics are distinct from pytest warnings; the focused
gate reported no pytest warnings or skips. Only newly owned disposable resources
were cleaned, with independent exact-ID absence evidence outside Git.

QC-excluded/None/join/time cases, missing CV half/prior selection, absent requested
run and incomplete trailing buckets remain held. RAW hindcasts are not new QC
certification; FI/models were not invoked. There is no new minimum-N rule,
scientific endorsement, durable-history proof, historical remediation, deployment
or activation authority. P3, T1d, T1c/T3 and Effort354 remain incomplete.

### First-bucket skill boundary status

The bounded first-bucket gate passed six cases/fourteen calls. See the
[contract](../spec/types-and-protocols.md#first-completed-skill-bucket-acceptance)
and [route](../touchpoint-maps.md#first-bucket-skill-regression).
Owner setup preserved full64Q/48protected lineage; worker real stores and readers
ran on independently rolled-back scenarios with14 observer removals. BEFORE
returned/wrote/published nothing in allthree strategies. Only SINGLE brackets E
with an AT positive; POOLED AT was not run and BMA's16-step empty-fold result is
not its fewer-than-two guard or a measured first usable BMA boundary.

Completed means time elapsed, not density. Future rows and created_at=C were
preloaded; E outputs retained full T..T+30h evaluation bounds. This is not
historical as-of evidence. One-sample CURRENT/full future bounds remain an open
owner scientific-policy question, not approval or activation authority. Evidence
NaN tags affect property copies only; source/raw/public assertions are unchanged.
No owner protected read is claimed after worker handoff, and no general permission-
table denial is inferred. RAW hindcasts/FI-not-executed and Plan234 limits remain.
Other P3 cases, wholeT1d/T1c/T3/Effort354, durable history and activation remain held.
