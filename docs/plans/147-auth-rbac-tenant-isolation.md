---
status: DRAFT
created: 2026-07-23
plan: 147
title: Auth / RBAC / audit + tenant write-isolation foundation (v1.0 headless)
scope: The v1.0-headless authentication + authorization foundation for the multi-tenant Nepal deployment — API-key auth with per-key station/tenant scope filtering, enforcement across ALL HTTP endpoints (closing the current unauthenticated /tables/ + .json data-exposure holes), a least-privilege DB role (drop superuser), an actor-stamped audit log, and tenant write-isolation on the flow/CLI write paths (onboarding/promotion/assignment). Folds in the deferred Plan 042. Human OAuth2 sessions + TOTP MFA + dashboard user-management defer to v1.x with Flow 3/the dashboard.
depends_on:
  - none (greenfield; consumes the existing FastAPI app + stores)
---

# Plan 147 — Auth / RBAC / audit + tenant write-isolation foundation (v1.0 headless)

**Status**: DRAFT
**Phase**: v1
**Owner**: Bea (marti@hydrosolutions.ch)
**Created**: 2026-07-23
**Roadmap**: Plan 106 lead D5-3 (§4), D4 (auth designable-now), D6 (v1.0 headless), F3 (bypass routes + grant audit).

> This is the v1 auth foundation. It **gates Flow 0 Nepal onboarding** (owner-scoped) and is the
> highest-leverage designable-now lead. Large enough that it lands in **slices**, each its own
> `/implement` → hold-at-PR (the Plan-120 pattern).

---

## 0. Locked owner decisions (grill-me, 2026-07-23)

| # | Decision | Consequence |
|---|----------|-------------|
| G1 | **Lean headless-first (per D6).** v1.0 = API-key auth (read-only, station/tenant-scoped) + close the live holes + least-privilege DB role + audit log + tenant write-isolation on the flow/CLI write paths. **DEFER** human OAuth2 sessions, TOTP MFA, refresh tokens, and dashboard user-management to **v1.x** (they land with Flow 3 / the dashboard, which D6 defers). Admin + key management is **CLI** for v1.0 (no dashboard). | Cuts the human-session/MFA/dashboard stack (~half of `security.md`) from v1.0. `security.md`'s full spec stays the v1.x target. |
| G2 | **Close the holes INSIDE the auth-enforcement slice** (not a separate first PR). | The `/tables/` browser + `.json` exports + CORS lockdown are guarded as part of Slice A, not a standalone quick PR. |
| G3 | **Tenant write-isolation = a principal on the write paths.** Add a `tenants` table + `station_groups.tenant_id` (NOT overloading `network`), thread an operator/service principal into the onboarding/promotion/assignment flows+CLI, and reject cross-tenant writes. Real in-code isolation on the shared prod (D4). | Slice D adds a principal concept to the flow/CLI layer — the write paths are not HTTP, so this is where isolation lives. |

**Deferred to v1.x (NOT in this plan):** OAuth2 password-flow human sessions, JWT access + opaque
refresh tokens, TOTP MFA, dashboard user-management UI, the forecaster/model-admin HTTP roles that drive
`POST /forecasts/{id}/adjust` + `PATCH /forecasts/{id}/status` (those endpoints need Flow 3, D6-deferred),
concurrent-session limits, account lockout. `security.md` §Authentication(v1) §9–19 remains the v1.x target;
this plan implements only its API-key + authorization-matrix-for-GET + audit + bootstrap subset.

---

## 1. Current state (state audit, 2026-07-23 — all file:line verified)

**There is NO auth, authorization, or tenant scoping in the running code.** Every HTTP endpoint is open.

- **App:** single FastAPI app `api/__init__.py:18`; routers at `:54-68`; only middleware is conditional
  CORS (`:28-37`, env `SAPPHIRE_CORS_ORIGINS`, default `*` in compose `docker-compose.yml:198`). No auth
  dependency/middleware/`Security` scheme anywhere in `src/`. DB deps only: `api/deps.py:20,26,32`.
- **LIVE data-exposure holes (F3(a)):** `/tables/` is a full read-only DB browser dumping any app table's
  rows (`routes/tables.py:73,101,143`, allow-list-only, no auth); the `.json` exports
  (`routes/stations.py:409,458,521,562`, `routes/forecasts.py:121`, `routes/models.py:168`) return raw
  data unauthenticated. Currently mitigated only by the deployment being a non-exposed "v0 test server"
  (`docker-compose.yml:196-197`).
- **DB identity = Postgres SUPERUSER (F3(b), worse than documented):** no `sapphire_worker`/scoped role
  exists in any executable SQL — the app connects as `${DB_USER:-sapphire}` = the cluster superuser
  (`docker-compose.yml:21,95`). Only grant in-repo is for the separate Prefect DB (`docker/init-db.sh:6`).
- **No tenant model:** `stations.network` is a free-text label + the station-identity/gateway key
  (`db/metadata.py:240,259`) — overloading it as a tenant key would repeat the `nwp_source` mistake
  (see [[project_weather_data_track]]). `stations.ownership` is own/foreign (`:241`, `StationOwnership`),
  NOT access control. `station_groups` (`:378-390`) + `group_model_assignments` (`:929-955`) have **no**
  owner/tenant column.
- **Writes are flow/CLI, not HTTP:** onboarding/promotion/model-assignment run via `flows/onboard.py`,
  `flows/onboard_model.py`, `services/onboarding.py`, `services/model_onboarding.py`,
  `services/model_registry.py` — none checks any actor/owner/tenant. The only HTTP mutation is
  `POST /alerts/{id}/acknowledge` (`routes/api_alerts.py:107`), and its `acknowledged_by` UUID is
  **client-self-asserted** (`:122`, no FK/verification, `alerts.acknowledged_by` nullable).
- **Dead scaffolding to reuse or delete:** `AccessTokenId`/`RefreshTokenId` (`types/ids.py:18,19`, unused),
  `AuditActorType {USER, API_KEY, SYSTEM}` (`types/enums.py:239`, used by nothing), `ForecastAdjustment` +
  `store_adjustment` protocol (`types/forecast.py:102`, `protocols/stores.py:726`, no table/impl). No
  `users`/`api_keys`/`access_tokens`/`audit_log` tables exist.
- **Config:** no JWT/API-key/auth config or secret. CORS reads env, not the (unwired) `config.toml:440`
  block. `sapphire_dg_api_key`/`recap_dg_client_token` authenticate SAP3 → upstream, NOT clients → SAP3.

---

## 2. Scope — v1.0 slices

Each slice is its own `/implement` → independent Codex gate → hold-at-PR (Plan-120 pattern). Slice A first
(it establishes the auth dependency + closes the holes); B/C run in parallel after A; D (the Flow-0 gate)
builds on A + C.

### Slice A — API-key auth + enforcement + close the holes (the core)

- **`api_keys` table + migration:** hashed key (bcrypt per `security.md:24`; reconcile vs Plan 042's SHA-256
  — grill-me residual R1), `key_prefix` for lookup, `name`, `role` (see below), `tenant_id` (FK, nullable
  for a global admin), `expires_at` (mandatory expiry — `042:96`), `disabled_at`, `created_at`, `last_used_at`.
  Plus `api_key_stations` scope join (`042:65`) OR a JSONB `scope` (stations/parameters/geographic) per
  `security.md:25,140` — grill-me residual R2.
- **Auth dependency:** a FastAPI `Depends`/`Security` (`APIKeyHeader`/`HTTPBearer`) that resolves the
  `Authorization: Bearer <key>` header to a principal on the SAME request connection (`042:105-111` —
  reuse `get_connection`, don't open a second). Missing/invalid/expired/disabled → 401.
- **Endpoint classification (`security.md:31`):** API keys are **GET-only**; the one mutation
  (`POST /alerts/{id}/acknowledge`) requires an operator-role key, and its actor is the **authenticated
  principal**, not the request body (fixes the self-asserted `acknowledged_by`, `api_alerts.py:122`).
- **Enforce on EVERY endpoint incl. the holes (G2, F3(a)):** apply the dependency to the JSON API routes
  AND the legacy `.json` exports AND `/tables/`. For `/tables/` + the HTML dashboard: gate behind an admin
  role OR remove/relocate them (grill-me residual R3 — 042:90-94 options a/b/c). CORS: lock to explicit
  `SAPPHIRE_CORS_ORIGINS`, reject `*` when auth is on (`042:117`, wire `config.toml:440` or keep env).
- **Per-key scope filtering (API-layer; stores stay auth-unaware, `042:65`):** responses filtered by the
  key's station/tenant scope; out-of-scope station IDs → 404 (`security.md:140`). Forecast/alert detail
  endpoints fetch by id → extract station_id → check scope BEFORE returning (`042:100-103`).
- **CLI key management (`042:69`):** `manage-api-keys` (create/list/revoke/rotate/scope) + a `create-admin`
  bootstrap analog (`security.md:47-65`) — the only path that mints the first admin key, run via
  `docker compose exec`.
- **Roles (headless subset of `042:67`):** `consumer` (read, scoped), `operator` (read + acknowledge
  alerts, scoped), `admin` (all + key mgmt + `/tables/`, unscoped). The 5-role human matrix is v1.x.

**Verify:** every endpoint returns 401 without a valid key; a scoped consumer key sees only its stations
(out-of-scope id → 404); `/tables/` + every `.json` route are guarded; an operator key can acknowledge and
the persisted `acknowledged_by` equals the authenticated principal (not a body field); an expired/disabled
key is rejected; admin key required for `/tables/`. Red-first: the guard tests fail against today's open routes.

### Slice B — Least-privilege DB role (F3(b))

- Replace the superuser connection with a scoped **`sapphire_app`** role: `CONNECT` + `SELECT/INSERT/UPDATE/
  DELETE` on app tables, `USAGE` on sequences, **no** DDL/superuser/`CREATE`. Migrations still run as an
  owner/admin role (init-time), the app/worker runs as `sapphire_app`.
- Wire it into `docker/init-db.sh` + compose (`DB_USER` split: migrate-user vs app-user) + the grant audit
  (`035:672-680`). Document in `security.md` + `cicd.md`.

**Verify:** the app user cannot `DROP`/`CREATE TABLE` or read another DB; the full pipeline (ingest →
forecast → API) still works under `sapphire_app`; a migration under the app role fails (proving least-priv).

### Slice C — Audit log

- **`audit_log` table + migration:** `id`, `actor_type` (wire the dead `AuditActorType`,
  `types/enums.py:239`), `actor_id` (api_key id / system), `action`, `target_type`, `target_id`, `tenant_id`,
  `at`, `detail` JSONB. Append-only.
- **Stamp mutations:** alert-ack, api-key create/revoke/rotate (admin actions, `042:98`), and the flow/CLI
  write paths (onboard/promote/assign — actor-stamped via the Slice-D principal). Also record the
  `create-admin`/bootstrap event (`security.md:61`).
- Reuse the `ForecastAdjustment` scaffolding ONLY if Flow 3's adjust endpoint is in scope — it is NOT
  (v1.x), so leave `store_adjustment` unimplemented (note it).

**Verify:** each mutation writes exactly one `audit_log` row with the real actor; the log is append-only
(no update/delete path); an unauthenticated/failed action is NOT logged as a successful actor action.

### Slice D — Tenant model + write-isolation (the Flow-0 gate, G3)

- **`tenants` table** (id, name/code, created_at) + **`station_groups.tenant_id` FK** (additive; a group
  belongs to exactly one tenant). Station tenancy derives via group membership (or an explicit
  `stations.tenant_id` — grill-me residual R4). Backfill existing Swiss groups to a default `sapphire`
  tenant.
- **Principal on the write paths:** thread an operator/service principal (an admin/operator api-key
  identity, or a CLI-supplied service principal) into `onboard_model`, `promote_artifact`,
  `onboard_stations`, group/model assignment. Each write **rejects** a target whose group's `tenant_id`
  differs from the principal's tenant (a global admin bypasses). Fail-loud (raise/return a
  `TenantIsolationError`), audit the rejection (Slice C).
- **Read isolation is NOT required** (D4: no gateway read-isolation; API-consumer scope filtering in Slice A
  already limits reads per key). This slice is WRITE-isolation only.

**Verify:** an operator principal for tenant A cannot onboard/promote/assign into tenant B's group (raises +
audited); can within tenant A; a global admin can cross tenants; existing single-tenant Swiss flows still
work under the default tenant. Red-first: the cross-tenant write succeeds against today's no-actor paths.

---

## 3. Dependency graph

```json
{
  "phases": [
    { "id": "slice-a", "name": "API-key auth + enforcement + close holes", "tasks": ["A"], "depends_on": [] },
    { "id": "slice-b", "name": "Least-privilege DB role", "tasks": ["B"], "depends_on": [] },
    { "id": "slice-c", "name": "Audit log", "tasks": ["C"], "depends_on": ["slice-a"] },
    { "id": "slice-d", "name": "Tenant write-isolation (Flow-0 gate)", "tasks": ["D"], "depends_on": ["slice-a", "slice-c"] }
  ]
}
```

Slice A gates C+D (the principal/actor concept). B is independent (DB role) — parallelizable. D is the
hard pre-Flow-0 gate.

## 4. Whole-plan exit gates

- Every HTTP endpoint requires a valid key (401 otherwise); the `/tables/` + `.json` holes are closed; CORS
  is explicit-origin. App runs as `sapphire_app` (not superuser). Every mutation is actor-stamped in
  `audit_log`. A cross-tenant onboard/promote/assign is rejected + audited. Full suite green; pyright ratchet
  held; `security.md` + `cicd.md` updated to describe the realized v1.0 subset (and what stays v1.x).

## 5. Open forks for the `/plan` grill-me (residuals — not owner-blocking)

- **R1 — key hash:** bcrypt (`security.md:24`) vs SHA-256 (`042:63`). Bcrypt for keys is slow per-request;
  a high-entropy random key + SHA-256 (or a fast keyed HMAC) is the common API-key choice. Decide in `/plan`.
- **R2 — scope shape:** `api_key_stations` join (`042:65`) vs JSONB `scope` (`security.md:140`). Join is
  queryable/indexable; JSONB is flexible (parameters + geographic). Lean join for v1.0, JSONB later?
- **R3 — `/tables/` disposition:** admin-gate (a) vs remove (b) vs relocate to a non-proxied port (c)
  (`042:90-94`). It is a dev convenience — removal/relocation is simplest + safest for prod.
- **R4 — station tenancy:** derive via group membership vs an explicit `stations.tenant_id`. A station in
  groups of two tenants is the edge case that decides this.
- **R5 — service principal for CLI/flow writes:** an api-key identity vs a distinct service-account concept
  vs a CLI `--as-tenant`/`--principal` arg validated against a stored operator. Shapes Slice D's actor.

## 6. References

- State audit (2026-07-23): `api/__init__.py:18,28-37,54-68`; `api/deps.py:20,26,32`;
  `routes/tables.py:73,101,143`; `routes/stations.py:409,458,521,562`; `routes/forecasts.py:121`;
  `routes/models.py:168`; `routes/api_alerts.py:107,122`; `db/metadata.py:240,241,259,378-390,929-955,1414`;
  `types/enums.py:194,239`; `types/ids.py:18,19`; `types/forecast.py:102`; `protocols/stores.py:726`;
  `docker-compose.yml:21,95,196-198`; `docker/init-db.sh:6`; `config.toml:440`.
- `docs/standards/security.md` §Authentication(v1) `:5-31`, §Bootstrap `:33-65`, §Authorization matrix
  `:110-140`, §API key lifecycle `:197-230`, §Rate limiting `:242-262`, §CORS/CSRF `:264-283`.
- `docs/plans/042-api-auth-client-sdk.md` §Scope `:61-73`, §Issues-before-activation `:75-118`, §Fwd-compat `:120-128`.
- `docs/plans/106-v1-critical-path-roadmap.md` D4/D6 (`:29,31`), §4 lead 3 (`:213`), F3 (`:281`), §3-8 (`:179`).
- Related: [[project_nepal_v1_collaborator_requirements]] (east/west tenant topology), [[project_v1_critical_path_roadmap]].
