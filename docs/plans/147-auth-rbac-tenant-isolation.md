---
status: READY
created: 2026-07-23
plan: 147
title: Auth / RBAC / audit + tenant write-isolation foundation (v1.0 headless)
scope: The v1.0-headless authentication + authorization foundation for the multi-tenant Nepal deployment. Its FOUNDATION is a first-class tenant model (a `tenants` table + `TenantId` + `stations.tenant_id`/`station_groups.tenant_id NOT NULL` + per-tenant group-name uniqueness + a station↔group composite-FK invariant), on top of which sit access-token (API-key) auth with per-key station scope filtering, enforcement across ALL HTTP endpoints (closing the current unauthenticated /tables/ + .json data-exposure holes, with only the shallow public liveness probe exempt), a least-privilege DB role model (drop superuser, preserve the documented sapphire_api/sapphire_worker split), an actor-stamped append-only audit log conforming EXACTLY to the authoritative `audit_log` contract, and tenant write-isolation on the flow/CLI write paths (onboarding/training/promotion/assignment) whose write authority derives from CONFIG-declared deployment identity + a validated run principal — never from the target row and never from a read-only access-token. Reuses the spec'd `access_tokens` table + `AccessToken`/`AccessTokenId` types (NOT a new `api_keys` name) and wires the dead `AuditActorType`/`AuditEventType`. Folds in the deferred Plan 042. Human OAuth2 sessions + TOTP MFA + dashboard user-management + alert-acknowledge (state-change) defer to v1.x with Flow 3/the dashboard.
depends_on:
  - none (greenfield; consumes the existing FastAPI app + stores)
---

# Plan 147 — Auth / RBAC / audit + tenant write-isolation foundation (v1.0 headless)

**Status**: READY — owner-flipped 2026-07-23 (converged after /plan escalation + 2 Codex rounds; R4-read + R6-RLS accepted as v1.x deferrals). Build slice-by-slice, hold-at-PR.
**Phase**: v1
**Owner**: Bea (marti@hydrosolutions.ch)
**Created**: 2026-07-23
**Roadmap**: Plan 106 lead D5-3 (§4), D4 (auth designable-now), D6 (v1.0 headless), F3 (bypass routes + grant audit).

> This is the v1 auth foundation. It **gates Flow 0 Nepal onboarding** (tenant-scoped) and is the
> highest-leverage designable-now lead. Large enough that it lands in **slices**, each its own
> `/implement` → hold-at-PR (the Plan-120 pattern). **The tenant model and the audit-log foundation are
> the two root slices** — every audited mutation depends on the audit slice, and every scope/isolation
> slice references tenant identity, so both are built first, not bolted on last.

---

## 0. Locked owner decisions (grill-me, 2026-07-23)

| # | Decision | Consequence |
|---|----------|-------------|
| G1 | **Lean headless-first (per D6).** v1.0 = **read-only** access-token (API-key) auth (station-scoped) + close the live holes + least-privilege DB role + audit log + tenant write-isolation on the flow/CLI write paths. **DEFER** human OAuth2 sessions, TOTP MFA, refresh tokens, dashboard user-management, **and `POST /alerts/{id}/acknowledge`** (a state change — see G4) to **v1.x** (they land with Flow 3 / the dashboard, which D6 defers). Admin + key management is **CLI** for v1.0 (no dashboard). | Cuts the human-session/MFA/dashboard stack (~half of `security.md`) from v1.0. `security.md`'s full spec stays the v1.x target. |
| G2 | **Close the holes INSIDE the auth-enforcement slice** (not a separate first PR). | The `/tables/` browser + `.json` exports + CORS lockdown are guarded as part of the auth slice, not a standalone quick PR. |
| G3 | **Tenant write-isolation = a `WritePrincipal` on the write paths (R5 LOCKED — see G6 for its authority source).** The tenant model itself — a `tenants` table + **canonical `stations.tenant_id NOT NULL`** (R4 LOCKED) + `station_groups.tenant_id NOT NULL` (NOT overloading `network`) + the station↔group composite-FK invariant — is the plan's **first slice** (Slice A). The write paths then thread a distinct **`WritePrincipal`** value (carrying `tenant_id: TenantId | None`, `None` = unscoped/global-admin) into the onboarding/training/promotion/assignment flows+CLI and reject cross-tenant writes. This is a **third principal kind**, separate from the two HTTP read roles below and **NOT** the read-only `AccessTokenId`/`access_tokens` row (R5). Real in-code isolation on the shared prod (D4). | The GET-only HTTP read roles (G4) never authorize a write. The `WritePrincipal`'s authority comes from **config + a validated run principal (G6)** — the write-isolation slice (Slice E) builds on the tenant model (A) + the audit writer (B). |
| G4 | **Access tokens are strictly GET-only (zero divergence from `security.md:31`).** `security.md:31` says *all* state-changing routes need a session token, *never* an API key, and the matrix (`security.md:125`) reserves `acknowledge` for session roles that v1.0 does not have (G1). So the sole HTTP mutation, `POST /alerts/{id}/acknowledge` (`routes/api_alerts.py:107`), is **removed from the v1.0 surface** (unmounted / returns 501, deferred to v1.x). No "operator" role is invented. **v1.0 HTTP roles = `consumer` (read, scoped) + `admin` (read, unscoped, + CLI key/tenant mgmt).** The `WritePrincipal` (G3/G6) is a separate CLI/flow-only concept, not an HTTP role, so the closed 2-role HTTP model is not reopened. | Zero security.md divergence; no bearer key can POST. Residual: the self-asserted `acknowledged_by` hole (`api_alerts.py:122`) is closed by *removing* the endpoint, not by re-authoring it; alert acknowledgement (a human/dashboard action) returns with Flow 3 in v1.x. |
| G5 | **Reuse the authoritative `access_tokens` name + existing types** (NOT a new `api_keys` table). `security.md:24` and `conventions.md:317` already spell it `access_tokens`; `AccessTokenId` exists (`types/ids.py:18`). The plan uses `access_tokens` (+ `access_token_stations` scope join) and wires the dead `AccessTokenId`. | Reuses dead scaffolding, avoids a name fork against 5 authoritative docs. Colloquial "API key" = the credential value inside an `access_tokens` row. |
| **G6** | **Tenant write-isolation authority = config-declared deployment identity + a validated run principal (NEW; supersedes any "materialize a WritePrincipal from an access-token" language).** The **deployment config declares which tenant(s) this host may write** (a `[deployment]` block in `config.toml` — one or more tenant codes, or a `global_admin` flag for an unscoped host). Each write **run** supplies a **trusted run principal** — a `--tenant <code>` CLI arg, or a config-bound run identity for scheduled flows — that is **VALIDATED against that config *and* the `tenants` table** before any write. The target group's/station's `tenant_id` must equal the run principal's `tenant_id`; a config-declared **global-admin** principal is unscoped (may cross tenants). For **scheduled flows** the run principal is the deployment's **config-declared tenant** — one scheduled deployment per tenant — resolved **before** unit selection and **never** derived from a training unit (Slice E). **Write authority comes ONLY from config / the validated run principal — NEVER derived from the target row, and NEVER from a read-only access-token.** This leverages the existing "prod shell access = trust" bootstrap model (`security.md:47-65`): running a write command on the host already implies the privilege level of reading `/run/secrets/` directly, so no second credential system is introduced in v1.0. | Closes the two blockers a token-derived principal created: **(#2) a self-authorizing target** (authority no longer read off the row being written — including the scheduled flow's per-unit derivation) and **(#3) a read-only credential used as a write credential** (the GET-only `access_tokens` row never grants write). The `WritePrincipal` type (Slice E) is populated from config, not from an `access_tokens` lookup or a `TrainingUnit`. |

**Deferred to v1.x (NOT in this plan):** OAuth2 password-flow human sessions, JWT access + opaque
refresh tokens, TOTP MFA, dashboard user-management UI, the forecaster/model-admin HTTP roles that drive
`POST /forecasts/{id}/adjust` + `PATCH /forecasts/{id}/status` + `POST /alerts/{id}/acknowledge` (all need
Flow 3 / a session token, D6-deferred — see G4), concurrent-session limits, account lockout,
per-request `api_key_request` audit logging (high-volume; the enum member exists, wiring is v1.x).
`security.md` §Authentication(v1) §9–19 remains the v1.x target; this plan implements only its
tenant-model + access-token + GET-only authorization-matrix + audit + bootstrap subset.

---

## 1. Current state (state audit, 2026-07-23 — all file:line re-verified)

**There is NO auth, authorization, or tenant scoping in the running code.** Every HTTP endpoint is open,
and no station/group carries a tenant.

- **App:** single FastAPI app `api/__init__.py:18`; routers at `:54-68`; only middleware is conditional
  CORS (`:28-37`, env `SAPPHIRE_CORS_ORIGINS`, default `*` in compose `docker-compose.yml:198`). No auth
  dependency/middleware/`Security` scheme anywhere in `src/`. DB deps only: `api/deps.py:20,26,32`.
- **No tenant model (the foundational gap):** `stations.network` is a free-text label + the
  station-identity/gateway key (`db/metadata.py:240,259`) — overloading it as a tenant key would repeat the
  `nwp_source` mistake (see [[project_weather_data_track]]). `stations.ownership` is own/foreign (`:241`,
  `StationOwnership`), NOT access control. `station_groups` (`:378-391`) has a **global** `name UNIQUE`
  (`:381`) and **no** tenant column; `station_group_members` (`:392-411`) is a bare `(group_id, station_id)`
  composite PK with no tenant column; `group_model_assignments` (`:929-955`) has no tenant column.
  `StationConfig` (`types/station.py:33`, fields `network:47`/`ownership:48`) and `StationGroup`
  (`types/station.py:76`, fields `id/name/station_ids/description/created_at`) carry **no** tenant field.
- **LIVE data-exposure holes (F3(a)):** `/tables/` is a full read-only DB browser dumping any app table's
  rows (`routes/tables.py:73,101,143`, allow-list-only, no auth); the `.json` exports
  (`routes/stations.py:409,458,521,562`, `routes/forecasts.py:121`, `routes/models.py:168`) return raw
  data unauthenticated. **Mitigation correction (stale-citation fix):** the internal API and Postgres ports
  are NOT published, but the **Caddy** service (`docker-compose.yml:232`) **does** publish the application on
  host `80:80`/`443:443` (`docker-compose.yml:234-236`) and reverse-proxies the API. The only external
  mitigation today is network/firewall deployment policy.
- **DB identity = Postgres SUPERUSER (F3(b), worse than documented):** no `sapphire_api`/`sapphire_worker`
  scoped role exists in any executable SQL — the app connects as `${DB_USER:-sapphire}` = the cluster
  superuser (`docker-compose.yml:21,95`). Only grant in-repo is for the separate Prefect DB
  (`docker/init-db.sh:6`). The documented least-privilege split (`conventions.md:315-319`:
  `sapphire_api`/`sapphire_worker`/`sapphire_prefect`) is **not** realized.
- **Single shared DB credential:** `docker/entrypoint.sh` reads **one** `/run/secrets/db_password` (`:6-7`)
  and seds it into whichever username the URL template names (`:12-20`); PostgreSQL, Prefect, API, workers,
  and the migration `init` all consume that same credential (`docker-compose.yml:21,54,95,192,278`).
- **Writes are flow/CLI, not HTTP:** onboarding/promotion/model-assignment run via `flows/onboard.py`,
  `flows/onboard_model.py`, `services/onboarding.py`, `services/model_onboarding.py`,
  `services/training.py`, `services/model_registry.py` — none checks any actor/owner/tenant. The scheduled
  `train_models_flow` promotes with **no principal** (`flows/train_models.py` `_store_artifact_task`,
  `:174-189`); `model_artifacts.promoted_by` (`db/metadata.py:865`, **nullable UUID**) is left NULL by every
  promotion helper (`services/training.py:49,74`). The only HTTP mutation is
  `POST /alerts/{id}/acknowledge` (`routes/api_alerts.py:107`); its `acknowledged_by` UUID is
  **client-self-asserted** (`:122`, no FK/verification).
- **Store transaction injection already exists (scope-cut evidence):** `store_group`
  (`store/station_group_store.py:38`) executes on a `transaction_factory` that is **injectable**
  (`:29-36`) and only *defaults* to `conn.engine.begin`. So an audited caller can pass its own real
  transaction with **no** repo-wide connection refactor — see the Slice-B atomicity note.
- **Dead scaffolding to wire or delete:** `AccessTokenId`/`RefreshTokenId` (`types/ids.py:18,19`, unused);
  `AuditActorType {USER, API_KEY, SYSTEM}` (`types/enums.py:239`, used by nothing); `AuditEventType`
  exists only as **spec design-intent** (`docs/spec/types-and-protocols.md:334`, marked "do not import" at
  `:311`) — not present at runtime in `types/enums.py`; `AuditEntry` design-intent at
  `types-and-protocols.md:1140-1149` (module `types/auth.py`, not implemented); `ForecastAdjustment` +
  `store_adjustment` protocol (`types/forecast.py:102`, `protocols/stores.py:726`, no table/impl). No
  `users`/`api_keys`/`access_tokens`/`audit_log`/`tenants` tables exist. `PrincipalId` does **not** exist
  yet (`types/ids.py` — added in Slice E).
- **Config:** no JWT/API-key/auth/tenant config or secret. CORS reads env, not the (unwired)
  `config.toml:440` block. `sapphire_dg_api_key`/`recap_dg_client_token` authenticate SAP3 → upstream,
  NOT clients → SAP3.

---

## 2. Scope — v1.0 slices

Each slice is its own `/implement` → independent Codex gate → hold-at-PR (Plan-120 pattern). **Slices A (the
tenant model) and B (the audit-log foundation) are the two roots** — neither depends on the other. Every
later slice that performs an audited mutation depends on **B**, so no slice ever ships an un-audited
mutation. **C (access-token auth) depends on A + B; D (least-privilege DB roles) depends on C;
E (tenant write-isolation) depends on A + B.**

**Scope rule for THIS `/implement` run: build Slice D ONLY (least-privilege DB roles, F3(b)) and STOP.** Do NOT
build Slice E in this run — it is a separate later slice with its own PR (branch `feat/plan-147-slice-d` builds
Slice D). **Slices A (tenant model, #130), B (audit-log substrate, #131), and C (access-token auth, #132) are
already on `main` — CONSUME them, do not re-implement.** D depends on C (its `sapphire_api` grants cover
`access_tokens` + the `last_used_at` write). Slice D is a DEPLOYMENT/DB-privilege slice (per the Slice-D block
below): realize the documented `sapphire_api`/`sapphire_worker`/`sapphire_prefect` role split (do NOT collapse
to one role) with **per-table grants** (not blanket UPDATE/DELETE); INSERT+SELECT-only on `audit_log` for both
app roles (defense-in-depth atop B's role-independent guard, never UPDATE/DELETE); **separate credentials** —
distinct owner/migration + `sapphire_api` + `sapphire_worker` secrets so the app CANNOT reconstruct the owner
password (generalize `entrypoint.sh` from the single `db_password` to a named `DB_PASSWORD_SECRET`, per-service
mounts + URL templates); split migrations (run as owner) from app/workers (scoped roles) in compose/init; an
**idempotent privileged bootstrap** (CREATE ROLE IF NOT EXISTS + re-grant, run as owner from `init`) so BOTH a
fresh volume AND an in-place existing-volume upgrade converge to the same roles/grants; and doc updates
(`conventions.md` grant matrix, `security.md`, `cicd.md`). Verify (upgrade+downgrade): app roles cannot
DROP/CREATE or read another DB; `UPDATE`/`DELETE audit_log` fail under both app roles; the full pipeline works
under scoped roles; a migration under a scoped role fails (least-priv proof); fresh + in-place both converge +
the bootstrap re-run is a no-op; the owner password is absent from API/worker containers; scoped-password
rotation works; documented rollback. The **config-identity WritePrincipal / flow-CLI write-isolation is Slice E,
NOT here.** Red-first: today the app runs as the Postgres SUPERUSER (no scoped role exists).

### Slice A — Tenant model foundation (data model, no auth)

The whole plan's tenancy invariants live here, created first so scope filtering (C), and write-isolation
(E) all reference a tenant that already exists on every station/group.

- **`tenants` table + `TenantId` + tenant domain types + default seed.** Columns: `id` (UUID PK), `code`
  (TEXT, `UNIQUE`, the human/config handle e.g. `sapphire`, `dhm`), `name` (TEXT), `created_at`
  (TIMESTAMPTZ). Add a `TenantId` NewType (`types/ids.py`), a `Tenant` frozen dataclass (`types/tenant.py`),
  a `TenantStore` protocol + production impl + fake, and reference fixtures. Seed a default **`sapphire`**
  tenant in the same migration (existing Swiss data backfills onto it below).
- **`stations.tenant_id NOT NULL` FK → `tenants` (canonical — R4 LOCKED).** A station's tenant is
  authoritative on the station itself, not derived from group membership (a station may belong to zero, one,
  or several groups, and model assignments/artifact promotions can target a station directly with no group —
  `types/station.py:56` `ModelAssignment` is station-scoped; `services/model_onboarding.py:861`
  `create_station_assignment` too).
- **`station_groups.tenant_id NOT NULL` FK → `tenants` (additive; a group belongs to exactly one tenant).**
- **Per-tenant group-name uniqueness.** Replace the current **global** `station_groups.name UNIQUE`
  (`db/metadata.py:381`) with `UNIQUE (tenant_id, name)`, and make every group-name lookup
  **tenant-qualified** (name alone is no longer a key). Enumerate + update the name-based lookups in
  `store/station_group_store.py` (e.g. `fetch_group`/name paths) and any TOML-onboarding name resolution.
- **Station↔group tenant-match via a COMPOSITE FOREIGN KEY (structural, fail-closed).** A `CHECK` cannot
  compare rows in two other tables and an application-only invariant leaves the direct membership writers
  (`store/station_group_store.py:38` `store_group`, `:128` `add_station_to_group`, raw SQL) able to miss it.
  Instead make tenant identity **participate in the membership FKs**:
  - add `UNIQUE (id, tenant_id)` to both `stations` and `station_groups`;
  - add a `tenant_id` column to `station_group_members`;
  - two composite FKs from `station_group_members`: `(station_id, tenant_id) → stations(id, tenant_id)` and
    `(group_id, tenant_id) → station_groups(id, tenant_id)`.
  Because both FKs bind the member row's single `tenant_id`, the DB structurally forces
  `station.tenant_id == group.tenant_id == member.tenant_id`: a mismatched membership row is **unrepresentable
  and fail-closed at the DB**, through every writer including raw SQL, with no trigger and no session
  variable. (A composite FK is ordinary referential integrity, not a bespoke trigger and not RLS — so it is
  cheap, standard, and consistent with deferring the session-variable RLS backstop, R6.)
- **Domain + boundary threading:** add a `tenant_id: TenantId` field to `StationConfig`
  (`types/station.py:33`) and `StationGroup` (`types/station.py:76`); thread it through row conversion
  (station/group stores + `_build_group`), the store protocols + fakes + reference fixtures, and every
  affected constructor. **The tenant boundary (parse-don't-validate):** a tenant **code string** from
  `config.toml` (the `[deployment]` block + TOML station/group onboarding) or a `--tenant <code>` CLI arg is
  parsed into a `TenantId` **once, at the config/CLI boundary**, by resolving the code against the `tenants`
  table (unknown code → hard error). Internal domain code handles only `TenantId`; no raw tenant-code
  strings leak past the boundary.
- **Careful membership migration (upgrade + downgrade, both tested on populated data):**
  1. Create `tenants`; seed the default `sapphire` tenant.
  2. `stations`: add `tenant_id` **nullable** FK → backfill **all** existing stations to `sapphire`
     → set `NOT NULL`.
  3. `station_groups`: add `tenant_id` nullable FK → backfill all groups to `sapphire` → **drop the global
     `UNIQUE (name)`** and add `UNIQUE (tenant_id, name)` → set `NOT NULL`.
  4. `station_group_members`: add `tenant_id` → backfill **every** member row from its group's (== its
     station's) tenant, **detecting inconsistencies** (if any existing member's `station.tenant_id !=
     group.tenant_id`, the migration **fails loudly** — those rows must be reconciled, never silently
     coerced) → add `UNIQUE (id, tenant_id)` to `stations`/`station_groups` → add the two composite FKs →
     set `members.tenant_id NOT NULL`.
  5. **Downgrade** reverses in order: drop composite FKs + the `(id, tenant_id)` uniques, drop
     `members.tenant_id`, drop `UNIQUE (tenant_id, name)` and restore global `UNIQUE (name)` (fails loudly
     if two tenants hold a colliding group name — the downgrade cannot re-establish global uniqueness in that
     case, which the test asserts), drop `station_groups.tenant_id`, drop `stations.tenant_id`, drop
     `tenants`.

**Verify (upgrade AND downgrade on populated data — F14):** on a DB pre-seeded with Swiss stations/groups
and members, `alembic upgrade head` lands `sapphire`-tenant rows on every station/group/member with all
FKs/uniques in place; a member row whose station/group tenants disagree makes the upgrade **fail loudly**;
after upgrade, two tenants may hold groups of the same name but one tenant may not; a station cannot be added
to a group of a different tenant (composite FK rejects it through `store_group`/`add_station_to_group`/raw
SQL, `station_group_store.py:38,128`); the tenant boundary rejects an unknown `--tenant`/config code;
`alembic downgrade` cleanly reverses on the populated DB (and fails loudly on a cross-tenant group-name
collision it cannot collapse). Red-first: before the migration, `stations`/`station_groups` have no
`tenant_id` and the group-name unique is global.

### Slice B — Audit-log foundation (table + append-only writer + enums) — root (no tenant/auth dep)

This is the **audit substrate every audited mutation needs**, so it is built as a **root slice** (alongside
A): the access-token `create-admin`/create/revoke events (Slice C) and the flow/CLI write + rejection events
(Slice E) both write through the writer defined here, and neither slice can ship an un-audited mutation
because both **depend on B**. `audit_log` has **no `tenant_id` column** and no FK to `tenants` (tenant context
lives in `detail`), and its append-only guarantee is **self-owned** (below), so B depends on neither Slice A
nor the roles slice.

- **`audit_log` table + migration — the authoritative contract (F4), NOT the invented `action`/`at`/`tenant_id`
  shape** (`docs/spec/database-schema.md:991-1000`, `docs/spec/types-and-protocols.md:1140-1149`,
  `docs/architecture-context.md:2341-2354`):

  | Column | Type | Notes |
  |--------|------|-------|
  | `id` | `BIGSERIAL PK` | |
  | `event_type` | `TEXT NOT NULL` | an `AuditEventType` value (below) |
  | `actor_id` | `UUID NULL` | the acting `access_tokens.id` when `actor_type='api_key'`; **NULL for system** — and **NULL for a config-declared write operator** (F3, Slice E) |
  | `actor_type` | `TEXT NOT NULL` | `AuditActorType`: `'user' \| 'api_key' \| 'system'` |
  | `target_type` | `TEXT NULL` | e.g. `'access_token'`, `'station'`, `'model_artifact'`, `'model_assignment'` |
  | `target_id` | `TEXT NULL` | TEXT for cross-PK flexibility (UUID/BIGSERIAL) |
  | `detail` | `JSONB NULL` | event payload — **tenant context (`tenant_code`) + the config operator handle (`operator`) live here** (there is NO `tenant_id` column), plus rejection `outcome`/`reason` |
  | `ip_address` | `INET NULL` | client IP for HTTP auth events; **NULL for CLI/flow writes** (v1.0's audited writes are all CLI/flow) |
  | `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | (**not** `at`) |

  There is **no FK on `actor_id`** — append-only rows must survive token revocation/deletion (no cascade).
  **System-actor representation (F4):** system/scheduled/config-operator writes are `actor_id = NULL` +
  `actor_type = 'system'` — **no reserved-system-UUID is invented.** Indexes (conforming to the query
  patterns at `security.md:208,216`): `(created_at)`, `(event_type, created_at)`, `(target_type,
  target_id)`, `(actor_id)`.
- **Wire the dead/spec-only enums (F4):**
  - **`AuditActorType`** (`types/enums.py:239`, currently dead): v1.0 uses **`API_KEY`** (an access-token
    actor) and **`SYSTEM`** (scheduled / no-actor / **config-declared write operator** — F3, Slice E);
    **`USER`** stays reserved for v1.x sessions.
  - **`AuditEventType`** — currently spec-only ("do not import", `types-and-protocols.md:311,334`). This slice
    promotes it to runtime by adding it to `types/enums.py`. v1.0 uses the existing spec members that map:
    **`API_KEY_CREATED`**, **`API_KEY_REVOKED`** (token create/revoke + `create-admin`), **`MODEL_PROMOTED`**,
    **`MODEL_REJECTED`** (promotion / gate-rejection). The flow/CLI write events the spec enum does not yet
    cover — station onboarding and model assignment — are added as **additive** members **`STATION_ONBOARDED`**
    and **`MODEL_ASSIGNED`**, applied to **both** spec docs by the doc-update gate (an append-only enum
    extension, NOT a free-form `action` string). **Rejections reuse the attempted event's `event_type`** with
    `detail.outcome = "rejected"` + `detail.reason` (e.g. a cross-tenant promote → `MODEL_REJECTED` with
    `detail.reason="tenant_isolation"`; a cross-tenant onboard → `STATION_ONBOARDED` with
    `detail.outcome="rejected"`), so no rejection-specific enum members proliferate.
  - Add the `AuditEntry` domain type (`types/auth.py`, per `types-and-protocols.md:1140-1149`). Its
    `actor_id` is `UserId | None` (the authoritative spec type) — **`None`** for `system`/config-operator
    events, so the config-operator mapping (F3) needs **no** widening of the actor contract.
    **SUPERSEDED (fixer round, post-implementation review, 2026-07-24):** the implemented contract widens
    `actor_id` to `UserId | AccessTokenId | None` — `AccessTokenId` when `actor_type=API_KEY`, matching
    `types-and-protocols.md`'s own `AuditEntry` shape (`actor_id: UserId | AccessTokenId | None`) and the
    `audit_log.actor_id` column doc two paragraphs above ("the acting `access_tokens.id` when
    `actor_type='api_key'`"). `actor_id` remains **`None` only for `system`/config-operator events**,
    unchanged. The domain type also gained a `__post_init__` invariant (`SYSTEM` ⇒ `actor_id=None`;
    `USER`/`API_KEY` ⇒ `actor_id` present) plus `.system()`/`.user()`/`.api_key()` typed constructors, and
    migration 0045 gained a matching DB-level `ck_audit_log_actor_id_matches_actor_type` CHECK constraint
    as a backstop for writers that bypass the domain type.
- **Append-only enforcement is SELF-OWNED by this slice (F4) — it does NOT depend on the roles slice.** Two
  layers, both owned here so B never waits on Slice D:
  1. **App-layer:** the writer store exposes **only** an INSERT method — no update/delete code path anywhere.
  2. **DB-layer, role-independent:** this slice's migration installs a guard that **rejects `UPDATE`/`DELETE`
     on `audit_log` regardless of role** (a `BEFORE UPDATE OR DELETE` trigger that `RAISE`s, or an
     equivalent rule) — so append-only holds even before the scoped roles exist and even for the table
     owner / migration role.
  Slice D's per-role `INSERT`+`SELECT`-only grants (no `UPDATE`/`DELETE`) are **consistent defense-in-depth**,
  not the primary guarantee — so **B owns append-only and does not depend on D** (resolves the old
  C∥D forward dependency).
- **General append-only writer + typed stamping API.** A single writer inserts an `AuditEntry`-shaped row
  (event_type / actor_type / actor_id / target / detail / ip / created_at). The stamping **call-sites** live
  in the slices that own the mutation — token create/revoke + `create-admin` in Slice C, onboard/promote/
  assign + rejections in Slice E — but the table, enums, writer, and append-only guard are all defined here,
  so those slices import a ready audit path rather than re-creating one.
- **Atomicity (two semantics) — built WITHOUT a repo-wide connection refactor (F17/F5 scope-cut).** No
  "Transaction foundation" rewrite: `store_group` already accepts an injectable `transaction_factory`
  (`station_group_store.py:29-36`), so an audited caller passes its own real (non-AUTOCOMMIT) transaction
  into the stores that already support it. Each audited flow/CLI command acquires a connection via
  `engine.connect()` + `conn.begin()` (a real transaction, not the shared `AUTOCOMMIT` connection at
  `flows/_db.py:80`) and threads that transaction into the mutation store(s) and the audit INSERT.
  AUTOCOMMIT stays for non-audited read/streaming paths. If any additional audited store lacks a
  `transaction_factory` seam, add **that one seam narrowly** (mirroring `store_group`) — not a global
  refactor.
  - **Success path — mutation + success-audit are ONE atomic transaction.** Every successful domain mutation
    and its audit INSERT run in the same RW transaction, so a failed audit insert rolls back the domain write.
    Applies to token `create`/`revoke` (Slice C CLI) and the flow/CLI write paths (Slice E).
  - **Rejection path — no domain write, a durable rejection event in a SEPARATE transaction.** When a write is
    refused (e.g. a cross-tenant `TenantIsolationError`, Slice E), the domain transaction rolls back (no state
    change) and the rejection event is written in a **separate, independently-committed transaction** *after*
    the rollback (the attempted `event_type` + `detail.outcome="rejected"` + `actor_type`/`actor_id` = the
    offending principal). This resolves "rejection is audited" vs "the mutation's transaction rolled back":
    the rejection row never lives in the rolled-back txn. Failed **authentication** attempts are NOT recorded
    as successful actor actions (v1.0 does not per-request-audit; see G1 deferral).
- Leave `store_adjustment`/`ForecastAdjustment` unimplemented (Flow 3 adjust is v1.x — note it).

**Verify:** the `audit_log` schema conforms EXACTLY to the authoritative contract
(`event_type`/`created_at`/`ip_address`/nullable-system-actor, **no `tenant_id`/`action`/`at`**); the dead
`AuditActorType` + spec-only `AuditEventType` are wired (incl. the additive `STATION_ONBOARDED`/
`MODEL_ASSIGNED`); **`UPDATE audit_log` and `DELETE FROM audit_log` FAIL even for the table owner** (the
role-independent guard — append-only owned here, not by the roles slice); the writer inserts exactly one
well-formed row per call; **an audit-insert failure rolls back the paired domain mutation** (rollback test on
a real non-AUTOCOMMIT transaction via the injected `transaction_factory`, not a fake-transactional store);
**a rejected write persists its rejection event** (attempted `event_type` + `detail.outcome="rejected"`)
while making NO domain state change; a system row has NULL `actor_id` + `actor_type='system'` (no reserved
UUID). Red-first: before this slice there is no `audit_log` table, and no append-only guard exists.

### Slice C — Access-token auth + enforcement + close the holes — depends A + B

The `audit_log` table + writer come from **Slice B**; this slice's audited mutations (token create/revoke +
`create-admin`) stamp rows through B's writer, which is why C depends on B as well as A.

- **`access_tokens` table + migration** (authoritative name per `security.md:24`, `conventions.md:317`; NOT
  `api_keys` — G5): hashed key — **R1 LOCKED = HMAC-SHA-256 over the high-entropy random key with a
  server-side pepper** (matching the `refresh_tokens` SHA-256 precedent at `security.md:15`), **not bcrypt**.
  Bcrypt buys no margin over a fast keyed hash for a high-entropy random secret but adds real per-request CPU
  on the hot auth path (project scale: ~1000 stations, sub-daily/high-frequency access). The doc-update gate
  (§4) **corrects `security.md:24`** from bcrypt→keyed-hash so the standards doc-of-record is not left
  silently contradicting `security.md:15`. This supersedes Plan 042's SHA-256-without-pepper. Columns:
  `id` (wire the dead `AccessTokenId`, `types/ids.py:18`), `token_hash`, `key_prefix` (lookup), `name`,
  `role` (below), `tenant_id` (**real FK to `tenants`** — resolvable because Slice A created `tenants`;
  nullable for a global admin), `pepper_version` (SMALLINT, default 1 — the rotation hook, below),
  `expires_at` (mandatory — `042:96`), `disabled_at`, `created_at`, `last_used_at`. Plus an
  **`access_token_stations` scope join** (`042:65`) — R2 LOCKED to the join (not JSONB).
  Index `(key_prefix)` for lookup; `(expires_at)` for cleanup.
  **SUPERSEDED (fixer round, post-implementation review, 2026-07-24):** `(key_prefix)` is now a
  **UNIQUE** index, not a plain one — the fast pre-verification lookup key must never collide
  (`fetch_by_key_prefix`'s `one_or_none()` would otherwise raise `MultipleResultsFound`, a 500, on a
  collision); the CLI retries token generation on the rare collision case. A `ck_access_tokens_role_tenant`
  CHECK constraint was also added: `role='admin' -> tenant_id IS NULL` and `role='consumer' -> tenant_id IS
  NOT NULL`, mirrored by an `AccessToken.__post_init__` invariant — a "tenantless consumer" or "tenant-bound
  admin" is now structurally unrepresentable, both at the dataclass boundary and at the DB layer for any
  writer that bypasses it.
- **Server-side pepper — full lifecycle (F11/F16):**
  - **Dedicated secret** named `access_token_pepper`, a Docker secret file
    (`/run/secrets/access_token_pepper`), mounted into the **API container** (auth verification) **and** the
    token-management CLI (the same `api` service, run via `docker compose exec`) so `create`/`revoke`
    compute the same hash. Not mounted into worker/prefect (they never verify tokens).
  - **Fail-closed startup:** the API refuses to boot (and the CLI refuses to run) if the pepper is
    missing/empty — no fallback to an unpeppered hash.
  - **Log redaction:** the pepper value is never logged; auth failures log the `key_prefix` only, never the
    presented key or the pepper.
  - **Rotation:** v1.0 uses a documented **all-token-reissue** procedure — because the key set is tiny (a
    handful of Nepal/Swiss consumer + admin keys), rotation = deploy the new pepper, then `revoke` + `create`
    every key (re-materializing each key's scope rows, R2). The `pepper_version` column is the **forward hook
    for zero-downtime dual-pepper rotation in v1.x** (validate against `{current, previous}` keyed by the
    row's `pepper_version`, then lazily re-hash); v1.0 does not implement dual-pepper. `cicd.md` documents the
    reissue runbook.
- **Auth dependency:** a FastAPI `Depends`/`Security` (`APIKeyHeader`/`HTTPBearer`) that resolves the
  `Authorization: Bearer <key>` header to a principal on the SAME request connection (`042:105-111` — reuse
  `get_connection`, don't open a second). Missing/invalid/expired/disabled → 401.
- **Endpoint classification — access tokens are strictly GET-only (`security.md:31`, G4):** no bearer key of
  any role may POST/PATCH/DELETE. The sole existing HTTP mutation `POST /alerts/{id}/acknowledge`
  (`routes/api_alerts.py:107`) is **removed from the v1.0 surface** (unmounted / 501, deferred to v1.x). This
  closes the self-asserted `acknowledged_by` hole (`api_alerts.py:122`) by removing the endpoint. No
  "operator" role is invented (G4).
- **Health exemption (health must stay public) — no capability-flag axis (F4/F18 scope-cut).** Keep the model
  simple and 2-role:
  - **`GET /api/v1/health` stays shallow + public** — `security.md:129` marks it public, the API healthcheck
    (`docker-compose.yml:205`) and the watchdog call it unauthenticated, and Caddy startup depends on the API
    becoming healthy (gating it would deadlock the stack).
  - **`GET /api/v1/health/detail` is `admin`-only** (`security.md:129`). There is **no new "endpoint
    capability flag" axis** — the invented `health-reader` capability is **removed**. The watchdog reaches
    `/health/detail` with an **`admin`-scoped token** (below). This is consistent with the trust model:
    the watchdog runs on the prod host, where shell access already equals full trust (`security.md:47-65`),
    so an admin token on that host widens nothing that host access did not already grant.
- **Authenticated detail-probing is a REQUIRED task (major — it definitely breaks the watchdog).** The
  watchdog derives and queries `/health/detail` for BAFU forecast freshness
  (`ops/watchdog.py:_bafu_url_from_health`, probe at `:190`) **and** independently for observation freshness
  (`_bafu_obs_url_from_health`), and the existing tests exercise those URLs unauthenticated. Once auth is
  enforced both probes return 401 and report false staleness. So this slice must:
  - Add optional `Authorization: Bearer` header support to **both** probe callers (forecast + observation
    freshness) plus the `/health/detail` derivation helpers.
  - **The watchdog secret is HOST-level, NOT a Compose mount (F6).** The watchdog runs as a launchd host
    process and already reads host-process secrets from `./secrets/` (e.g. `DEFAULT_SLACK_PATH =
    ./secrets/slack_webhook_url`, `ops/watchdog.py:13-14,57`), **not** Docker secrets. So the admin probe
    token is a **host secret file** at `./secrets/health_probe_token` (perms `0600`, owner-only), read by the
    watchdog host process. Wire it via a watchdog CLI flag / config field (default path
    `./secrets/health_probe_token`), and update the launchd installer + plist
    (`scripts/launchd/install-launchd.sh`, `scripts/launchd/watchdog.sh`,
    `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`) to pass/point at it. Rotation = replace the
    file + reissue the admin token (documented in `cicd.md`).
  - Tests: valid / **missing** / **unreadable** / **empty** host-secret probe token, and valid / missing /
    expired admin token against `/health/detail`.
  **SUPERSEDED (fixer round, post-implementation review, 2026-07-24):** the launchd installer/plist/wrapper
  did not need a code change after all — `DEFAULT_PROBE_TOKEN_PATH` (`./secrets/health_probe_token`) is
  relative and already resolves correctly against the plist's `WorkingDirectory` /
  `watchdog.sh`'s `cd`, the same convention already used for `--slack-path`. All three files
  (`install-launchd.sh`, `watchdog.sh`, the `.plist`) now carry an explicit inline comment saying so, so the
  omission reads as a verified, deliberate no-op rather than a missed wiring step; `install-launchd.sh` also
  gained a preflight WARNING if `./secrets/health_probe_token` is absent. The **unreadable** probe-token test
  case was added (`tests/unit/ops/test_watchdog.py::TestReadProbeToken::test_unreadable_file_returns_none`,
  via a `Path.read_text` monkeypatch — `chmod 0o000` is unreliable when tests run as root).
- **Enforce on EVERY other endpoint incl. the holes (G2, F3(a)):** apply the dependency to the JSON API
  routes AND the legacy `.json` exports AND all HTML routers. CORS: lock to explicit `SAPPHIRE_CORS_ORIGINS`,
  reject `*` when auth is on (`042:117`, wire `config.toml:440` or keep env).
- **Legacy HTML/browser routes — R3 LOCKED = remove/relocate for prod (admin-gate only as fallback).**
  Enumerate every HTML router and treat them uniformly (a scoped key alone would still leak unfiltered global
  data): `/tables/` DB browser (`routes/tables.py:73,101,143`), the HTML dashboard (`routes/dashboard.py:13`),
  and the HTML variants under `routes/stations.py:112`, `routes/forecasts.py:16`, `routes/models.py:16`, plus
  any observations/health-detail HTML view. For v1.0 headless these browser pages are **removed / relocated
  off the proxied surface**; if any is retained it is **admin-gated** (not merely "any valid key"). A
  route-matrix test covers each HTML endpoint.
  **SUPERSEDED (fixer round, post-implementation review, 2026-07-24):** the first committed pass's
  route-matrix test sampled a handful of routes by hand. It is now an EXHAUSTIVE test
  (`tests/unit/api/test_security.py::TestRouteAuthMatrixExhaustive`) that introspects the live
  `app.routes` dependency graph and pins the complete method/path/classification for every mounted route
  (health/observations/forcing/baselines/hindcasts/forecast-data-json/dashboard/model pages/health-detail-html
  and every table variant included) — a newly added or accidentally-ungated route fails immediately, not
  just the ones someone remembered to hand-pick. A parallel DB-backed test
  (`tests/integration/api/test_access_token_auth.py::TestAdminGatedRoutesRejectConsumerAllowAdmin`) fires
  real requests at every one of those admin-gated routes as both a consumer (403) and an admin (clears the
  gate).
- **Station-scoped route matrix — lock the non-station-filterable GETs (F7).** Two JSON GETs do not carry a
  station and cannot be station-filtered:
  - **Global model skill chart** `GET /api/v1/models/{model_id}/skill-chart.json`
    (`routes/models.py:168`, `model_skill_chart_json` — selects by `model_id`/`artifact_id`, not station):
    **admin-gate it.** A scoped `consumer` cannot receive it (there is nothing to scope-filter to their
    stations); `admin` may. Test: consumer → 403/404, admin → 200.
  - **Stationless alerts** `GET /alerts` (`routes/api_alerts.py:49`, `list_alerts` — `station_id` is
    nullable, so `alerts` rows may have a null station): **consumer tokens see ONLY alerts whose `station_id`
    is in their scope; stationless (null-station) alerts are NOT returned to a `consumer` (fail-closed);
    `admin` sees all.** Test: a consumer's `GET /alerts` excludes both out-of-scope-station and null-station
    alerts; admin's includes them.
    **SUPERSEDED (fixer round, post-implementation review, 2026-07-24):** the first committed pass applied
    the scope filter to the route's ALREADY-paginated page (post-`fetch_alerts`, after `LIMIT`/`OFFSET` and
    the unscoped `total` count) — a consumer could get short/empty pages despite later in-scope alerts, and a
    wrong `total`. `AlertStore.fetch_alerts` now takes a `scope_station_ids` parameter applied INSIDE the
    query, before count/limit/offset (`station_id.in_(scope_station_ids)` — NULL never matches, so
    stationless exclusion falls out for free); the route passes `principal.station_ids` when not admin,
    `None` (unscoped) when admin. Locked by a DB-integration test that seeds real
    in-scope/out-of-scope/stationless alerts and asserts correct multi-page pagination.
- **Per-key scope filtering — R2 LOCKED = `access_token_stations` join, STATION scope only.** `security.md:21`
  documents a three-axis scope contract (station + parameter + geographic); v1.0 deliberately implements
  **only the station axis** and **explicitly narrows the v1.0 standard** — parameter and geographic scoping
  are documented as **deferred to v1.x** (doc-update gate amends `security.md:21`). Concrete v1.0 contract:
  - Scope is a normalized **`access_token_stations` join** (`042:65`), not JSONB — queryable/indexable, a
    real FK per station so a scoped id is guaranteed live. JSONB (parameter + geographic) is the v1.x carrier.
  - **JSON responses are filtered by the key's station list; out-of-scope station IDs → 404**
    (`security.md:140`). Forecast/alert detail endpoints fetch by id → extract station_id → check scope
    BEFORE returning (`042:100-103`).
  - **Empty-scope semantics (locked):** a `consumer` token with **zero** `access_token_stations` rows sees
    **nothing** (fail-closed — every station out-of-scope → 404), NOT "all stations". Unscoped read is an
    `admin`-only property.
  - **Scope-membership validation:** every station id inserted into `access_token_stations` MUST belong to
    the token's own `tenant_id` (checked at `create`/`list`; a cross-tenant station id is rejected). This
    keeps station-scope from silently spanning tenants — and is now enforceable because Slice A put
    `tenant_id` on every station.
  - **Scope + rotation:** rotation (revoke-old + create-new) **re-materializes the same scope rows** for the
    new token id; it does not carry, mutate, or drop scope silently.
- **CLI token management (`042:69`) — `create`/`list`/`revoke` only for v1.0 (minor: trim surface).** In-place
  `rotate` and `scope`-edit are **deferred to v1.x**: rotation = `revoke` + `create` (re-materializing scope
  rows) and re-scoping = the same, using the two required primitives. Plus a `create-admin` bootstrap analog
  (`security.md:47-65`) — the only path that mints the first admin token, run via `docker compose exec`; it
  writes the bootstrap `audit_log` row via **Slice B**'s writer. Token `create`/`revoke` and their audit
  insert MUST share one RW transaction (**Slice B** atomicity rule).
- **Roles (headless subset of `042:67`, G4):** `consumer` (read, station-scoped), `admin` (read, unscoped, +
  CLI token/tenant mgmt). The 5-role human matrix + `operator`/`forecaster` session roles are v1.x. **No
  third role** (the removed `health-reader` capability does not add one).
  **SUPERSEDED (fixer round, post-implementation review, 2026-07-24):** the first committed pass let
  `create-admin` accept an optional `--tenant <code>` and persisted it on the token row — but
  `Principal.is_admin`/`station_in_scope` never consult `tenant_id` (admin is unconditionally unscoped, per
  G4), so a "tenant-bound admin" was a misleading, unenforced state. `create-admin` now takes **no** `--tenant`
  flag at all; admin tokens are always minted with `tenant_id=None`, matching the CHECK constraint above.

**Verify:** shallow `GET /api/v1/health` returns **200 without a key**; every *other* endpoint returns 401
without a valid key (incl. `/health/detail`); a scoped consumer key sees only its stations (out-of-scope id
→ 404) and **neither** the global model skill-chart (admin-only) **nor** stationless alerts; every `.json`
route is guarded and every legacy HTML route is removed/relocated or admin-gated (route matrix);
`POST /alerts/{id}/acknowledge` is not reachable (501/unmounted); an expired/disabled key is rejected; the
API/CLI **fail closed with no pepper**; the `create-admin` bootstrap writes exactly one `audit_log` row (via
Slice B's writer, in one transaction); the watchdog reads its **host-secret admin probe token** and both
freshness paths pass, while an unauthenticated `/health/detail` probe gets 401 (and missing/unreadable/empty
probe-token cases are handled). Red-first: the guard tests fail against today's open routes; the health test
asserts public 200 for shallow and 401 for unauthenticated detail.

### Slice D — Least-privilege DB roles (F3(b)) — depends C

Preserve the **documented** `sapphire_api` / `sapphire_worker` / `sapphire_prefect` separation
(`conventions.md:315-319`) — do **NOT** collapse them into one `sapphire_app` role (F12: collapsing widens
the API's blast radius to every worker-writable table with no justification). This slice realizes the
documented split with **per-table grants** (NOT a blanket `UPDATE/DELETE`):

- **`sapphire_api`** (API service; runs the CLI token ops via `docker compose exec api`): `SELECT` on the
  read tables + `access_tokens` (SELECT/INSERT/UPDATE, incl. `last_used_at`), **INSERT + SELECT on
  `audit_log`, never UPDATE/DELETE** (append-only per `conventions.md:317` "INSERT only on audit_log" —
  a **defense-in-depth** grant atop Slice B's role-independent append-only guard).
  **REALIZED (fixer round, post-implementation review, 2026-07-24):** `last_used_at` is now actually written
  — `require_principal` issues a single-column `UPDATE access_tokens SET last_used_at = now()` through a
  dedicated RW connection/transaction on every SUCCESSFUL auth (never on a 401), before this slice it was
  wired end-to-end (column, migration, domain type) but never updated, so it stayed permanently NULL and the
  inactive-key monitoring signal (`security.md` § API key lifecycle) was dead.
- **`sapphire_worker`** (Prefect flows / CLI write paths — onboarding, training, promotion, assignment):
  `SELECT/INSERT/UPDATE` on the domain tables it already writes (per `conventions.md:317`) **plus** the write
  grants the v1.0 flow write paths need (stations/station_groups/station_group_members writes for onboarding;
  `model_artifacts` promotion) **plus INSERT + SELECT on `audit_log`** (Slice E write paths run as the worker
  and must stamp audit rows). The doc-update gate reconciles the exact per-table matrix in
  `conventions.md:315-319` (the additive `audit_log` INSERT for `sapphire_worker` + any onboarding write
  grants are the only widenings, each named). Never UPDATE/DELETE on `audit_log`.
- **`sapphire_prefect`**: unchanged — full access to the `prefect` database only (already provisioned via
  `docker/init-db.sh:6`).
- **No** DDL/superuser/`CREATE` for any app role.
- **Migrations run as the owner role, app/workers as their scoped role.** The `init` container currently runs
  `alembic upgrade head` as the superuser `${DB_USER:-sapphire}` (`docker-compose.yml:274,278`); split it so
  Alembic uses the migrate/owner URL and the API/worker services use their scoped URLs.
- **Separate CREDENTIALS, not just usernames (major — a least-priv role is useless if the app still holds the
  owner password).** Today every service consumes the one `db_password` (`entrypoint.sh:6-7`, seds into the
  URL at `:12-20`). This slice:
  - Defines an **owner/migration** secret (PostgreSQL init + the Alembic `init` container only) and distinct
    **`sapphire_api`** and **`sapphire_worker`** application secrets (each role created with its own password
    by the privileged bootstrap step below).
  - Adds **service-specific secret mounts + URL templates**: `init`/owner services mount the owner secret +
    owner `DATABASE_URL_TEMPLATE`; API mounts only the `sapphire_api` secret + URL; workers/Prefect flow
    connections mount only the `sapphire_worker` secret + URL. API/workers **cannot reconstruct** the owner
    password.
  - Generalizes `entrypoint.sh` from the hard-coded single `db_password` (`:6-7`) to a **named** secret
    (`DB_PASSWORD_SECRET=/run/secrets/<owner|api|worker>_db_password`) so distinct services select distinct
    secrets.
  - Covers **credential creation + rotation** in the deployment tests below.
- **Existing-volume deployment (Major finding):** `docker/init-db.sh` is mounted into PostgreSQL's
  `docker-entrypoint-initdb.d` (`docker-compose.yml:24`) which **only runs on a fresh volume**. Add an
  **idempotent privileged bootstrap/upgrade step** (SQL run as the owner from the `init` container,
  `CREATE ROLE IF NOT EXISTS`-style + re-grant) that runs on every deploy and is a no-op when already
  applied — so both a fresh volume and an in-place upgrade converge to the same roles/grants.
- Wire into `docker/init-db.sh` + compose + the grant audit (`035:672-680`). Document in `security.md` +
  `cicd.md`.

**Verify (upgrade + downgrade — F14):** the app users cannot `DROP`/`CREATE TABLE` or read another DB;
**`UPDATE audit_log` and `DELETE FROM audit_log` both FAIL under `sapphire_api` AND `sapphire_worker`**
(the per-role grant test — **defense-in-depth**; the role-independent append-only guarantee itself is proven
in Slice B); the full pipeline (ingest → forecast → API) still works under the scoped roles; a migration
under a scoped role fails (least-priv proof); **both a fresh volume and an in-place upgrade** end with all
roles present + correctly granted, and the idempotent bootstrap re-run is a no-op; **the owner/migration
password is absent from the API/worker containers**; **rotating a scoped password** leaves the app working
and the owner credential unchanged; the deployment change has a documented rollback (revert compose +
secrets; the roles/grants are additive and safe to leave).

### Slice E — Tenant write-isolation (the Flow-0 gate, G3/G6) — depends A + B

The tenant columns + composite-FK invariant are already in place from **Slice A**, and the audit writer +
rejection-event path from **Slice B**. Slice E adds the config-declared write authority + the enforcing
principal on the write paths.

- **Config-declared deployment identity (G6).** Add a `[deployment]` block to `config.toml` declaring the
  tenant(s) this host may write: either `writable_tenants = ["<code>", ...]` (one or more tenant codes) or
  `global_admin = true` (an unscoped host), plus an optional `operator = "<handle>"` (below). Parsed +
  validated at startup: each declared code is resolved to a `TenantId` against the `tenants` table (unknown
  code → hard startup error); the boundary from Slice A is where the code string becomes `TenantId`.
- **`WritePrincipal` — R5/G6 LOCKED (a distinct value type, populated from config — NOT from an
  access-token, NOT from a target row).** Define `WritePrincipal(id: PrincipalId | None, tenant_id: TenantId |
  None)` where **`PrincipalId = NewType("PrincipalId", str)`** — a **new id in `types/ids.py`** that is the
  **config-declared operator handle** (a short string label from the `[deployment]` block, e.g.
  `ops-nepal`), and is **NOT** a `UserId`/UUID and **NOT** an `AccessTokenId`. `tenant_id=None` ⇒
  unscoped/global-admin (may cross tenants); a set `tenant_id` ⇒ tenant-bound. The principal is **NEVER**
  materialized from an access-token lookup (those rows are GET-only per G4) — reusing one credential type for
  read-HTTP and write-CLI would give the same value contradictory authority per surface (blocker #3). It is
  built from the **validated run identity**:
  - **Interactive CLI:** a `--tenant <code>` arg supplies the run principal's tenant; it is validated to be
    within the host's config-declared `writable_tenants` (or the host is `global_admin`, giving an unscoped
    principal). Absent `--tenant` on a single-tenant host, the principal binds that host's sole writable
    tenant; on a `global_admin` host, absence means unscoped. The run identity — **not the target row** — is
    the sole source of authority (blocker #2). A config `[deployment].operator` (or a `--operator <handle>`
    override) supplies the optional `PrincipalId`.
  - **Audit actor mapping (F3, minimal-conformant option (a)).** A config-declared operator is **not** a user
    and **not** an API key, so its writes are audited as **`actor_type='system'`, `actor_id=NULL`**, with the
    operator handle in **`detail.operator`** and the tenant in **`detail.tenant_code`**. This needs **no
    change** to the `AuditActorType`/`actor_id` contract (which cannot represent a config operator directly)
    — the operator identity is preserved in `detail`, not lost.
  - Each write **rejects** a target whose `tenant_id` differs from the principal's `tenant_id` (unscoped
    global-admin bypasses). Fail-loud (`TenantIsolationError`); the rejection is recorded as a **separate
    rejection-event transaction** (**Slice B** — NOT the rolled-back mutation txn).
  - **Write-path inventory (include the scheduled training flow):**
    - station onboarding (`services/onboarding.py`), group/model assignment
      (`services/model_onboarding.py:861` `create_station_assignment`; `types/station.py:56`), `onboard_model`.
    - `promote_artifact` / `store_and_promote_artifact` (`services/training.py:49,82`) — reached BOTH
      interactively AND by the **scheduled `train_models_flow`** via `_store_artifact_task`
      (`flows/train_models.py:174-189`), which today promotes with **no principal**.
- **Scheduled training runs under a SINGLE config-selected run principal chosen BEFORE unit selection
  (Blocker — write authority must NEVER come from the unit being written).** `train_models_flow` already
  receives a `deployment_config` (`flows/train_models.py:239`, loaded from `SAPPHIRE_CONFIG` at `:267-276`),
  so the run's tenant reaches the flow as a **`[deployment]` config field / deployment parameter**, not from
  any row. A scheduled deployment is **bound to one tenant**: **before** `_determine_scope_task` selects any
  unit (`flows/train_models.py:352`), the flow constructs **exactly one** `WritePrincipal` from that
  config-declared run tenant — **validated against the `tenants` table AND the host's `writable_tenants`**
  (G6) — and **never** from `unit.station_id`/`unit.group_id`. The flow then **selects/filters** `scope.units`
  to the run principal's tenant (pass the tenant into `determine_training_scope`, or filter `scope.units` by
  each unit's `station.tenant_id`/`group.tenant_id` from Slice A) and **excludes-with-an-audited-skip
  (or rejects)** any unit whose target tenant differs — the principal authorizes the selection; a unit
  **never** self-authorizes. Each retained unit's promotion still runs through the identical
  `TenantIsolationError` check.
  - **Per-tenant deployments.** A multi-tenant host runs **one scheduled deployment per tenant**, each pinned
    to its tenant code. A **single-tenant or global-admin** deployment MAY instead declare an **explicitly
    unscoped run** (`global_admin`, `tenant_id=None`) that trains across tenants under **config-authorized**
    authority — an explicit config declaration, not target-derivation.
  - **No per-unit principal, no silent global bypass.** Do **not** construct a `WritePrincipal` from a
    `TrainingUnit`'s station/group tenant (the old per-unit derivation — a permitted-but-wrong-tenant unit
    would self-authorize), and do **not** hand the flow a blanket `tenant_id=None` "system principal" unless
    the deployment **explicitly** declared a global-admin run.
  - Test: a run principal bound to tenant A trains **only** tenant-A units; a tenant-B unit that appears in
    scope is **skipped-with-audit (or rejected)**, never promoted; an **explicitly-declared global-admin** run
    trains across tenants; a `--tenant`/config code outside the host's `writable_tenants` fails validation
    **before** any unit is selected.
- **Provenance is recorded in the append-only `audit_log`, NOT the legacy UUID `promoted_by` (F3 — resolves
  the `PrincipalId`/`actor_id` type mismatch).** `transition_artifact_status` accepts `promoted_by: UUID |
  None` and stores it (`store/model_artifact_store.py:227,234`), but `model_artifacts.promoted_by` is a
  **`UUID`** column (`db/metadata.py:865`, nullable). In v1.0 **headless** there is **no `users` row and no
  user UUID**, and `PrincipalId` is a config **string** handle that does **not** fit a `UUID` column — so
  `promoted_by` stays **NULL** in v1.0 (the column is reserved for the v1.x human-session `UserId`). The
  authoritative v1.0 promotion provenance is instead the **`audit_log` `MODEL_PROMOTED` row** written in the
  same transaction (Slice B): `actor_type='system'`, `actor_id=NULL`, `detail.operator=<PrincipalId>` (when
  the run declared one — omitted otherwise), `detail.tenant_code=<code>`. This captures who/which-tenant
  promoted **without** inventing a reserved system UUID or coercing a string into the UUID column. Thread the
  `WritePrincipal` through `promote_artifact` → `store_and_promote_artifact` → the `ACTIVE` transition so the
  audit stamp is emitted for every promotion (interactive AND scheduled).
- **Read isolation is NOT required** (D4: no gateway read-isolation; **Slice C**'s station-scope filtering +
  Slice A's per-station tenant-membership check limit reads per key). This slice is WRITE-isolation only. See
  residual R4-read.
- **RLS session-variable backstop deferred to v1.x — see residual R6.** v1.0 mitigates the missed-call-site
  risk by routing all promotions through the single `store_and_promote_artifact` chokepoint plus Slice A's
  composite-FK structural guard.

**Verify:** the `WritePrincipal` type exists (with `PrincipalId` a config **string** handle) and is populated
**from config / the validated run principal (never from an access-token, never from a target row)**, exercised
by BOTH the CLI and the scheduled-flow paths; a tenant-A principal cannot onboard/promote/assign into tenant B
(raises `TenantIsolationError` + a persisted rejection event, no domain change); can within tenant A; a
config-declared global-admin can cross tenants; a `--tenant` code outside the host's `writable_tenants` is
rejected at validation; **the scheduled `train_models_flow` builds a SINGLE config-selected run principal
BEFORE unit selection, trains only that tenant's units, and SKIPS-with-audit (or rejects) any foreign-tenant
unit in scope** (never promoting it) — the principal **never** derived from `unit.station_id`/`unit.group_id`;
a promotion writes a `MODEL_PROMOTED` `audit_log` row (`actor_type='system'`, operator/tenant in `detail`)
while `model_artifacts.promoted_by` stays NULL (v1.0 headless — no config-string operator fits the UUID
column); a station cannot be added to a group of a different tenant (the Slice-A composite FK rejects it
through direct `store_group`/`add_station_to_group`/raw SQL); existing single-tenant Swiss flows still work
under the default `sapphire` tenant. Red-first: the cross-tenant write (interactive AND scheduled) succeeds
against today's no-principal paths, and the scheduled flow trains every tenant's units indiscriminately.

---

## 3. Dependency graph

```json
{
  "phases": [
    { "id": "slice-a", "name": "Tenant model foundation", "tasks": ["A"], "depends_on": [] },
    { "id": "slice-b", "name": "Audit-log foundation (table + append-only writer + enums)", "tasks": ["B"], "depends_on": [] },
    { "id": "slice-c", "name": "Access-token auth + enforcement + close holes", "tasks": ["C"], "depends_on": ["slice-a", "slice-b"] },
    { "id": "slice-d", "name": "Least-privilege DB roles", "tasks": ["D"], "depends_on": ["slice-c"] },
    { "id": "slice-e", "name": "Tenant write-isolation (Flow-0 gate)", "tasks": ["E"], "depends_on": ["slice-a", "slice-b"] }
  ]
}
```

**Slices A (tenant model) and B (audit-log foundation) are the two roots.** A creates `tenants` +
`stations.tenant_id`/`station_groups.tenant_id`/`station_group_members.tenant_id` + the composite-FK
invariant; B creates the `audit_log` table + the append-only writer + the wired `AuditActorType`/
`AuditEventType` enums, and **owns append-only itself** (app-layer no-update/delete + a role-independent DB
guard in its own migration — so it needs neither the roles slice nor A). **C (auth) depends on A + B**:
`access_tokens.tenant_id` FKs `tenants` (A), scope-membership validates station tenancy (A), and the
`create-admin` bootstrap + token create/revoke write audit rows via **B**'s writer. **D (DB roles) depends on
C**: its per-table grants reference `access_tokens` (C) and `audit_log` (B, a transitive prerequisite via
C→B), and its `audit_log` `INSERT`+`SELECT`-only grants are **defense-in-depth atop B's own append-only
guard** — so D does **not** own the append-only guarantee (this removes the old C∥D forward dependency).
**E (write-isolation) depends on A (tenancy) and B (the audit writer, for its rejection events)** and is the
hard pre-Flow-0 gate. No audited mutation ships without B present. The authoritative `audit_log` has **no
`tenant_id` column**, so there is no cross-slice FK from `audit_log` to `tenants` (tenant context lives in
`audit_log.detail`). The reading order (A, B, C, D, E) is a valid topological order — no slice depends on a
later one.

## 4. Whole-plan exit gates

- **Tenancy:** every station + station_group carries a `tenant_id NOT NULL`; group names are unique
  **per tenant**; a station↔group tenant mismatch is unrepresentable (composite FK); the tenant migration
  upgrades AND downgrades cleanly on populated data.
- **Auth:** every HTTP endpoint **except the shallow public `GET /api/v1/health`** requires a valid key (401
  otherwise); `/health/detail` is admin-only **and the watchdog probes it with a HOST-secret admin token**
  (both freshness paths); the `/tables/` + `.json` + HTML-route holes are closed (removed/relocated or
  admin-gated per the route matrix); the global model skill-chart is admin-only and stationless alerts are
  hidden from consumers; `POST /alerts/{id}/acknowledge` is not reachable (deferred, G4); CORS is
  explicit-origin; the API/CLI fail closed with no `access_token_pepper`.
- **DB roles:** the app runs as scoped `sapphire_api` / `sapphire_worker` (documented split preserved, NOT
  collapsed) on **distinct application credentials the API/worker containers cannot use to reconstruct the
  owner password**; `audit_log` is INSERT+SELECT only (UPDATE/DELETE denied) for both — **defense-in-depth
  atop Slice B's role-independent append-only guard**; both fresh-volume and in-place-upgrade deployments
  converge to the roles.
- **Audit:** the `audit_log` schema conforms EXACTLY to the authoritative contract
  (`event_type`/`created_at`/`ip_address`/nullable-system-actor, **no `tenant_id`/`action`/`at`**); the dead
  `AuditActorType` + spec-only `AuditEventType` are wired (incl. the additive `STATION_ONBOARDED`/
  `MODEL_ASSIGNED`); **append-only is owned by Slice B** (app-layer no-update/delete + a role-independent DB
  guard; Slice D's grants are defense-in-depth); audited operations run on a **real caller-owned transaction**
  (via the existing injectable `transaction_factory`, no repo-wide refactor); every successful mutation is
  actor-stamped in the SAME transaction, and a **rejected write persists a separate rejection event while
  changing no domain state**.
- **Write-isolation:** write authority derives from **config + the validated run principal (G6), never from
  the target row or an access-token**; the scheduled `train_models_flow` runs under a **SINGLE config-selected
  run principal chosen BEFORE unit selection** (per-tenant deployments), and a foreign-tenant unit in scope is
  **skipped-with-audit or rejected**, never promoted — the principal is **never** derived from a training
  unit; a cross-tenant onboard/train/promote/assign — interactive AND scheduled — is rejected + audited;
  **v1.0 promotion provenance is the append-only `audit_log` `MODEL_PROMOTED` row** (`actor_type='system'`,
  operator/tenant in `detail`), while the legacy UUID `model_artifacts.promoted_by` stays NULL (reserved for
  v1.x sessions — no config-string operator fits a UUID column).
- Full suite green; pyright ratchet held; **every migration has a tested downgrade** (F14).
- **Docs updated (repo rule) —** `security.md` (document the realized v1.0 subset; the GET-only +
  acknowledge-deferral; **narrow the scope contract at `security.md:21`** to station-axis-only with
  parameter/geographic deferred to v1.x; **correct the access-token hash at `security.md:24`**
  bcrypt→HMAC-SHA-256+pepper; document the health exemption + the HOST-secret admin probe token + the pepper
  reissue runbook), `cicd.md` (DB-role split + **separate owner/api/worker credentials + secret mounts** +
  existing-volume bootstrap + pepper/probe-token rotation), `conventions.md:315-319` (the additive
  `sapphire_worker` `audit_log` INSERT + onboarding write grants; `sapphire_api` append-only `audit_log`),
  the **tenant model** additions to `docs/spec/database-schema.md` + `docs/spec/types-and-protocols.md`
  (tenants table, `stations`/`station_groups`/`station_group_members` tenant columns, `TenantId`/`Tenant`,
  the `AuditEventType` additive members `STATION_ONBOARDED`/`MODEL_ASSIGNED`), and the
  **`access_tokens`/`AccessToken`/`AuditEntry`** terminology kept in sync with
  `docs/spec/types-and-protocols.md:1131,1140-1149`, `docs/spec/database-schema.md:467`,
  `architecture-context.md:2300`, `types/ids.py:18` (G5: reuse, do NOT introduce an `api_keys` fork).

## 5. Open forks (residuals)

**LOCKED before READY (flagged as design-blocking, now resolved above):**
- **R1 — key hash: LOCKED = HMAC-SHA-256 + server-side pepper** (not bcrypt), matching the `refresh_tokens`
  precedent (`security.md:15`); the doc-update gate corrects `security.md:24`, and the **full pepper
  lifecycle** (dedicated secret, API+CLI mount, fail-closed startup, log-redaction, all-token-reissue
  rotation with a `pepper_version` forward hook) is specified in Slice C — so R1 is resolved, not merely
  "decided".
- **R2 — scope shape: LOCKED = `access_token_stations` join, station-axis only** (`042:65`); parameter +
  geographic axes (`security.md:21`) deferred to v1.x and the v1.0 standard narrowed accordingly. Empty scope
  = sees nothing (fail-closed); scoped station ids validated to the token's tenant.
- **R3 — legacy HTML/browser routes: LOCKED = remove/relocate for prod (admin-gate fallback).** Reflected in
  Slice C verify + the route matrix (the global model chart + stationless alerts are decided too).
- **R4 — station tenancy: LOCKED = canonical `stations.tenant_id NOT NULL`** (Slice A, the FIRST slice) with
  station↔group tenant-match enforced by a **composite FK**. The "station in two tenants' groups" edge case
  is unrepresentable (fail-closed at the DB).
- **R5 — write principal: LOCKED = a distinct `WritePrincipal(id: PrincipalId | None, tenant_id: TenantId |
  None)` type whose authority is CONFIG-declared + validated-run-principal (G6)** — NOT the read-only
  `AccessTokenId`/`access_tokens` row, NOT derived from the target row, and NOT derived from a training unit;
  `PrincipalId = NewType("PrincipalId", str)` is a config operator handle recorded in `audit_log.detail`
  (actor mapped to `actor_type='system'`, F3). The scheduled `train_models_flow` runs under a **single
  config-selected run principal chosen before unit selection** (per-tenant deployments — Slice E), **never** a
  per-unit target-derived principal and **never** a blanket global-bypass unless the deployment explicitly
  declares a global-admin run.

**Still open (implementation-shape only, not design-blocking):**
- **R4-read — read-isolation discipline:** v1.0 has no DB check that a consumer key's station list stays
  within one tenant beyond the per-station tenant-membership validation at token create (station-scope only;
  accepted D4 risk). Tighten to a tenant-aware read filter later.
- **R6 — RLS backstop (v1.x):** add Postgres RLS keyed on a per-connection `SET LOCAL app.tenant_id` so a
  *missed* write call-site fails closed at the DB — a natural fit atop Slice D's scoped roles, on
  `station_groups`/`stations`/dependent tables. **Deferred for v1.0** because the per-connection
  session-variable plumbing (setting `app.tenant_id` on every HTTP/CLI/Prefect-flow connection) is out of
  v1.0 scope. Distinct from the composite-FK structural guard already in Slice A (which needs no session var).

## 6. References

- State audit (2026-07-23, re-verified): `api/__init__.py:18,28-37,54-68`; `api/deps.py:20,26,32`;
  `routes/tables.py:73,101,143`; `routes/stations.py:112,409,458,521,562`; `routes/forecasts.py:16,121`;
  `routes/models.py:16,168`; `routes/api_alerts.py:49,107,122`;
  `db/metadata.py:240,241,259,378-391,392-411,865,929-955`; `types/station.py:33,47,48,56,76`;
  `types/enums.py:239`; `types/ids.py:18,19`; `types/forecast.py:102`; `protocols/stores.py:726`;
  `store/station_group_store.py:29-36,38,128`; `store/model_artifact_store.py:227,234`;
  `services/onboarding.py`; `services/training.py:49,65,74,82`;
  `services/model_onboarding.py:861`; `flows/train_models.py:174-189,239,267-276,352`; `flows/_db.py:80`;
  `ops/watchdog.py:13-14,57,190`; `docker-compose.yml:21,24,54,95,192,198,205,232,234-236,274,278`;
  `docker/entrypoint.sh:6-7,12-20`; `docker/init-db.sh:6`;
  `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`, `scripts/launchd/watchdog.sh`,
  `scripts/launchd/install-launchd.sh`; `config.toml:440`.
- Authoritative `audit_log` contract: `docs/spec/database-schema.md:991-1000`;
  `docs/spec/types-and-protocols.md:311,334,1140-1149`; `docs/architecture-context.md:2341-2354`;
  `docs/standards/security.md:627-651` (recorded categories).
- `docs/standards/security.md` §Authentication(v1) `:5-31`, §Bootstrap `:33-65`, §Authorization matrix
  `:110-140`, §API key lifecycle `:197-230`, §CORS/CSRF `:264-283`, §Audit logging `:627-651`.
- `docs/conventions.md` §Service users `:315-319`.
- `docs/plans/042-api-auth-client-sdk.md` §Scope `:61-73`, §Issues-before-activation `:75-118`, §Fwd-compat `:120-128`.
- `docs/plans/106-v1-critical-path-roadmap.md` D4/D6 (`:29,31`), §4 lead 3 (`:213`), F3 (`:281`).
- Related: [[project_nepal_v1_collaborator_requirements]] (east/west tenant topology), [[project_v1_critical_path_roadmap]].
