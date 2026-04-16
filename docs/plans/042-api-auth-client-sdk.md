# Plan 042 — API Key Auth + Client SDK

**Status**: DEFERRED (post-v0 deployment, target v0b)
**Phase**: 9b (API auth + consumer interface)
**Depends on**: Plan 041 (REST API endpoints, deployed and stable)

## Why deferred

Plan 042 was originally scoped for v0. After critical review, the team decided
to defer it. The reasoning:

### 1. v0 has no external consumers

v0 is Swiss public data, ~170 stations, single VM, 1-2 team members. There are
no third-party API consumers. The full auth model (per-station access, 3 roles,
CLI key management, client SDK) adds 15 new files and modifies 13 existing files
for a feature with zero runtime users at v0.

### 2. v0 explicitly specifies "no auth"

The decision is documented in v0-scope.md (§J, §B, §F), security.md (§2, §7),
and the security audit (Plan 037, finding C-1). All independently say "v0 has
no authentication or authorization." This was a deliberate decision, not an
oversight — the server is SSH-accessible only, behind Caddy, serving public
Swiss hydrological data.

### 3. The client SDK is premature

The API is pre-1.0. Endpoint paths, response schema fields, pagination format,
and error structure will change during v0 testing. A typed client SDK published
now creates a maintenance burden and becomes a compatibility constraint. It will
be rewritten for v1 anyway (which adds JWT sessions, 5 roles, TOTP MFA).

### 4. Auth can be added incrementally

Adding auth later does not require redesigning the API. The architecture is
already auth-ready:
- Stores are auth-unaware (Plan 041's D2) — filtering is an API-layer concern
- The 7 REST API endpoints go through `get_stores()` + Pydantic serialization —
  adding a `Depends(get_current_api_key)` and a filter call is mechanical. The
  16+ legacy dashboard/table/JSON endpoints use `get_connection()` directly and
  must also be protected (see auth bypass issue below).
- The station filtering logic (a WHERE clause or list comprehension) is
  architecturally trivial

### 5. If the API must be protected before v0b

If the server becomes network-exposed before Plan 042 lands, a **shared-secret
middleware** (one env var `SAPPHIRE_API_KEY`, 20 lines of code) provides 95%
protection at 5% cost. No DB tables, no per-station filtering, no roles.

---

## What this plan will implement (when activated)

The full scope below is preserved for when external consumers appear (expected:
v0b or pre-v1 Nepal preparation). All design decisions from the original plan
remain valid. Activating this plan means changing its status to DRAFT and going
through the normal review cycle.

### Scope summary

1. **API key auth** — SHA-256 hashed keys, `Authorization: Bearer` header,
   FastAPI dependency middleware
2. **Per-station access filtering** — `api_key_stations` join table, API-layer
   filtering (stores stay auth-unaware)
3. **3 roles** — consumer (read-only, station-filtered), operator (read +
   acknowledge alerts, station-filtered), admin (all, no station filter)
4. **CLI key management** — `manage_api_keys` tool (create/list/revoke/
   add-stations/remove-stations)
5. **Client SDK** — `sapphire_flow.client.SapphireClient` subpackage, extract
   to standalone package at v1
6. **DB schema** — `api_keys` + `api_key_stations` tables, Alembic migration

### Issues to address before activation

The critical review identified issues that must be resolved when this plan is
activated:

**Auth bypass via legacy `.json` endpoints (CRITICAL):** The existing
`stations/{station_id}/observations.json`, `stations/{station_id}/forcing.json`,
`stations/{station_id}/baselines.json`, `stations/{station_id}/hindcasts.json`,
`forecasts/{forecast_id}/data.json`, and `models/{model_id}/skill-chart.json`
endpoints are under `/api/v1/` and would remain unauthenticated if only the new
route files get auth — these legacy routes use `get_connection()` directly, not
`get_stores()`, so adding auth to `get_stores()` alone would not protect them.
Plan 042 must also add auth to these legacy endpoints, or move them behind Caddy
path restrictions, or restructure the dashboard to use authenticated endpoints.

**Unauthenticated dashboard + table browser (HIGH):** The `/tables/` route
is a full read-only database admin panel. If the API is internet-exposed, the
dashboard leaks all data without auth. Options: (a) add auth to dashboard
routes, (b) add Caddy path restrictions, (c) move dashboard to a separate
app on a non-proxied port (like Prefect UI).

**Admin key blast radius (HIGH):** Admin keys bypass all station filtering
and can trigger flows. Must have mandatory expiry (e.g., 90-day max),
rotation CLI command, and structlog audit events for all admin actions.

**Forecast detail station check (HIGH):** `GET /api/v1/forecasts/{id}`
fetches by forecast_id. Must fetch the forecast first, extract station_id,
then check against the caller's allowed stations before returning data.
Same pattern for alert acknowledge.

**Connection lifecycle (HIGH):** Auth middleware (`get_current_api_key`)
must use the same connection as `get_stores()` — not open an additional
connection. Plan 041's `deps.py` refactoring (D7) ensures FastAPI dependency
caching reuses `get_connection` within a request, which makes this
straightforward. Note: `acknowledge_alert` already uses two connections
(`get_stores` → read, `get_connection_rw` → write) by design; the auth
dependency should chain through `get_connection` (the read path).

**Client SDK `list_alerts` params (MEDIUM):** Must include all filter
parameters (`station_id`, `level`, `limit`, `offset`) not just `status`
and `source`.

**CORS tightening (MEDIUM):** When auth is active, CORS must use the
explicit origin list from `SAPPHIRE_CORS_ORIGINS`, never `*`.

### Forward compatibility with v1

| v0b (Plan 042) | v1 migration |
|---|---|
| API keys only | Add `users`, JWT sessions, TOTP MFA alongside |
| 3 roles | consumer → api_consumer, operator → deprecated (sessions), admin → org_admin |
| SHA-256 key hash | Upgrade to bcrypt via key rotation if needed |
| Station allowlist per key | Add parameter-level and geographic-boundary scoping |
| CLI key management | Add admin API endpoints + dashboard UI |

---

## Activation criteria

Activate this plan (change status to DRAFT, re-review) when **any** of:

1. An external consumer needs API access (e.g., DHM testing, partner org)
2. The API server is exposed beyond SSH-accessible networks
3. The team begins v1 Nepal preparation and needs to validate auth flows
4. v0 is deployed and stable (Plan 041 endpoints exercised in production)
