---
status: DRAFT
created: 2026-09-26
plan: 401
title: A reviewer access token for the review dashboards — read everything a review needs, for one client's stations, write nothing
scope: Add a third HTTP access-token role, `reviewer`, for the dashboards we use to review our forecast products (the BAFU/Swiss dashboard, the Nepal dashboard). A reviewer token is GET-only and tenant-bound and scoped exactly like a consumer token, and additionally reaches routes classified REVIEW (the first two arrive with Plan 402). Includes the role, the database constraint change, the auth dependency, CLI issuance, the route-classification test, the rollback procedure and the documents. NOT publishing or any other write (tokens stay GET-only — publishing is a named person's act, Plan 341); NOT access to unpublished forecasts where Plan 341's gate is active (341 decides); NOT human sign-in, sessions or MFA; NOT opening any existing admin-only route to reviewers; NOT changing what consumer or admin tokens can do (T2 only aligns one out-of-scope error message, which revealed only an out-of-scope forecast's existence).
risk: high   # security/auth + migration (docs/workflow.md § High-risk work)
depends_on: []
blocks: [402, 404]
related: [042, 147, 215, 268, 341, 402, 404]
open_decisions: []
closed_decisions: [D1, D2, D3, D4]   # owner, 2026-09-26
source: 2026-09-26 — owner, while reviewing Plan 402: "could we have a special user for the BAFU dashboard and the Nepal dashboard?" — to replace the admin token Plan 402 D11 had the map use.
---

# Plan 401 — a reviewer access token for the review dashboards

## Status

**DRAFT — HIGH RISK — review corrections folded, not re-reviewed.** Authentication and a
migration are high-risk triggers (`docs/workflow.md` § High-risk work): the ordinary Claude + Codex
pair on the current text, plus one owner-commissioned review before READY and again before the
implementation PR. All decisions are closed.

## Why this exists

Plan 402 gives the flow map (the dashboard we use to demonstrate and review forecast products)
two new read routes — the QC rule sets and a station's skill — which a consumer token must not
reach. With only two roles today, the map would have had to hold an **admin** token: it reaches
every admin page and route and sees every station, so a Swiss dashboard would also see Nepal data.
The owner asked for a dedicated identity for each dashboard instead.

**Separation needs separate clients.** A token is bound to one tenant (client), so tenant binding
keeps two dashboards apart only when their stations sit in different tenants. Today every station
is in the single seeded tenant `sapphire` (`alembic/versions/0041_tenants_table.py:9`;
`config.toml:178` `writable_tenants = ["sapphire"]`). Plan 268 T2b creates the DHM tenant with six
*new* stations; it moves no existing station, so any Nepal station already in `sapphire` stays there.
The rule is therefore not "after 268" but: a dashboard token may use `tenant` mode **only when every station in its tenant belongs to that
dashboard's client**. A token is bound to one tenant for life and cannot span two — `grant` refuses
a station from another tenant (`store/access_token_store.py:90-95`). So a dashboard token sees only
its own tenant's stations, and until the rule above holds it uses `scope_mode = stations` with an
explicit station list. Which tenant each dashboard's token binds to is D4.

The Nepal deployment needs three kinds of access; this plan supplies exactly one of them:

| who | access | provided by |
|---|---|---|
| third parties | read their stations — published forecasts only, in a tenant where Plan 341's gate is active | the existing **consumer** token |
| the Nepal (and Swiss) review dashboard, reading | everything a consumer reads for that client's stations, plus the REVIEW routes | **this plan — `reviewer`** |
| a hydrologist reviewing candidates and publishing or withdrawing a forecast | reads of unpublished candidates and a write, recorded against that person | **Plan 341** — a signed-in, named person with per-station permission. The shared `reviewer` token gets neither. Only the unpublished-read default may be changed, and only by 341 (D2); no token gains a write (D1, D3). |

## What is measured (origin/main, 2026-09-26)

- **Roles.** `AccessTokenRole` has `CONSUMER` and `ADMIN` (`types/enums.py:330`), documented there,
  in `api/security.py:10` and in `docs/standards/security.md:17` as *"two roles only"*, per Plan 147
  G4. G4's two halves are separable: **GET-only** (every token), and **two roles** (v1.0's role
  list). This plan changes only the second, by owner decision (D1).
- **Tokens never write.** `POST /api/v1/alerts/{id}/acknowledge` returns 501 for every token
  (`api/routes/api_alerts.py`); writes use a separate `WritePrincipal` built from config, never from
  a token row (`types/write_principal.py`).
- **The database pins the pairing**: `ck_access_tokens_role` (`role IN ('consumer','admin')`),
  `ck_access_tokens_role_tenant` (admin → no tenant; consumer → a tenant) and
  `ck_access_tokens_tenant_mode_is_consumer` (`scope_mode = 'stations' OR role = 'consumer'`)
  (`db/metadata.py:2063-2102`; migrations 0047, 0049). `access_token_stations.token_id` has no
  cascade (`db/metadata.py:2110-2117`). `AccessToken.__post_init__` (`types/auth.py:171-186`)
  mirrors the constraints.
- **Where the code branches on role — two kinds of site, both must be handled:**
  - Sites testing `is_admin` / `ADMIN` put a non-admin reviewer in the scoped branch — correct by
    construction: `api/security.py:131-142`, `api/routes/api_stations.py:174`,
    `api/routes/api_alerts.py:97`, `api/routes/forecast_lab.py:116`,
    `store/access_token_store.py:31,119`, `cli/access_tokens.py:201,303`.
  - Sites testing `CONSUMER` that would **fail open** — a reviewer falls into the tenantless,
    admin-like branch — and **must change**: `types/auth.py:172` (a tenant is required only for a
    consumer) and `cli/access_tokens.py:579-592` (role choice; `--tenant` read only for consumers).
  - Predicates that **fail closed** but are too strict and **must change**:
    `ck_access_tokens_role_tenant` (`db/metadata.py:2090-2091`, admits only admin+NULL or
    consumer+tenant, so rejects every reviewer row) and `ck_access_tokens_tenant_mode_is_consumer`
    (`:2099`, rejects a reviewer in tenant mode).
- **Out-of-scope behaviour differs by route.** Detail routes return 404 for an out-of-scope
  station (`api/security.py::ensure_station_in_scope`); collection routes filter instead — the
  station list (`api/routes/api_stations.py:174`) and alerts, which answer an explicit
  out-of-scope `station_id` with 200 and an empty result (`api/routes/api_alerts.py:84-89`).
- **Route gating.** Routers mount with `Depends(require_principal)` or `Depends(require_admin)`
  (`api/__init__.py:84-101`); `require_admin` rejects a non-admin with 403. The route matrix
  (`tests/unit/api/test_security.py::TestRouteAuthMatrixExhaustive`, `_classify_routes`) derives
  PUBLIC / PRINCIPAL / ADMIN from each route's dependency graph; PRINCIPAL includes the
  acknowledgement POST, which returns 501.
- **CLI.** `python -m sapphire_flow.cli.access_tokens` — `create` (consumer, `--tenant` required,
  repeatable `--station`), `create-admin`, `list`, `revoke`, `show`, `grant`, `revoke-station`,
  `set-scope-mode` (`cli/access_tokens.py:419-470`, `:616`). There is no `access-tokens` console
  script. `revoke` only sets `disabled_at` (`store/access_token_store.py:177-181`): the row stays.
- **Rollback.** `docs/standards/cicd.md` § Rollback: no schema downgrade in practice — restore from
  backup and redeploy the previous image, which must run against the new schema. The previous
  image parses roles fail-closed with `AccessTokenRole(row["role"])`
  (`store/access_token_store.py:233`), so **any** reviewer row — revoked or not — makes its token
  listing crash and turns a reviewer request into a 500 instead of a 401.
- Latest migration on main: `0059_forecast_preservation.py`; `tests/unit/db/test_alembic_head_release_b.py:86`
  pins `_RELEASE_B_HEAD = "0059"`.

## Owner decisions

### D1 — a third HTTP role exists: `reviewer`. **⚖️ CLOSED — owner, 2026-09-26.**

This amends Plan 147 G4's *"no third role"* for the role list only. **GET-only stands for every
token.** The name is `reviewer` (what the dashboards do); a dashboard deployment is one token.

### D2 — a reviewer is a consumer plus the REVIEW routes. **⚖️ CLOSED — owner, 2026-09-26 (as proposed).**

A reviewer token is tenant-bound, uses the consumer's scope rules unchanged (both `scope_mode`s,
404 for an out-of-scope station), reaches every PRINCIPAL route exactly as a consumer does, and
additionally reaches routes gated REVIEW. It never reaches an ADMIN route. No existing route is
reclassified; Plan 402 adds the first two REVIEW routes.

**Where Plan 341's publication gate is active, a reviewer token gets no access to unpublished
forecasts.** (Where it is not, reviewers — like consumers — read forecasts as today.) There, a reviewer is gated exactly like a consumer on the forecast routes. The default
is **no**; only Plan 341 may change it, by an explicit, recorded decision. Plan 341 already records
this (PR #313): the reviewer token "sees only published values on ordinary forecast routes and cannot
read this plan's unpublished candidates" (`341:28,30,48`), and its tests cover reviewer/consumer
parity (`341:106`). Reviewing unpublished candidates is the named hydrologist's job in 341.

### D3 — publishing is a person, not a dashboard token. **⚖️ CLOSED — owner, 2026-09-26.**

The Nepal dashboard's token stays read-only. Publishing and withdrawal are Plan 341's named,
signed-in hydrologist with per-station permission and an audit trail, acting through the
dashboard. No token role gains a write.

### D4 — The DHM gauges get their own tenant. **⚖️ CLOSED — owner, 2026-09-26.**

This **confirms Plan 268 D11** (owner, 2026-09-10): the DHM gauges go into a new tenant in the
existing database, which Plan 268 T2b provisions (fetch-before-create, with the code and display
name fixed in 268's artifact). Where a client's stations all sit in its own tenant, consumer and
reviewer tokens of different clients are separated by tenant; **admin tokens stay global** (they
carry no tenant and bypass station scope, `types/auth.py:177`, `api/security.py:138`). This plan adds
no tenant and names none. Tenant mode for a dashboard token follows the rule in *Why this exists*:
in particular, the `sapphire`-tenant (Swiss) dashboard token stays in `stations` mode for as long as
any non-Swiss station remains in `sapphire`. Plan 402 D12 states the same rule.

**The Nepal dashboard's token binds to the DHM tenant only** (owner, 2026-09-26). A Nepal station
left in `sapphire` — e.g. test basin 12300 seeded by Plan 192, if it exists on the instance — is not
visible to that token. Moving such a station into the DHM tenant is a Nepal-onboarding step, not
this plan's; if it is moved, the Nepal token needs no change.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate).

### T1 — the role, its invariants, the migration and the rollback procedure

**Outcome:** `reviewer` is a valid role in the type and the database, bound to a tenant, allowed
either scope mode; nothing about consumer or admin changes; the rollback procedure is written down.

**In:**
- `types/enums.py` — `AccessTokenRole.REVIEWER`.
- `types/auth.py::AccessToken.__post_init__` — reviewer requires a tenant (the `CONSUMER`-only test
  at `:172` becomes "not admin").
- `db/metadata.py` and a new alembic migration (next free revision **at implementation time** —
  other plans are adding migrations): `ck_access_tokens_role` → `role IN ('consumer','reviewer','admin')`;
  `ck_access_tokens_role_tenant` → admin has no tenant, consumer and reviewer have one;
  `ck_access_tokens_tenant_mode_is_consumer` **keeps its name** and its predicate becomes
  `scope_mode = 'stations' OR role IN ('consumer', 'reviewer')` — roles named explicitly, like the
  other role constraints (so no test or doc that matches the name breaks).
- The migration's **downgrade refuses while any `reviewer` row exists, revoked or not**, naming the
  step: delete the token's `access_token_stations` rows, then its `access_tokens` row (no cascade;
  the API's database role has only INSERT and UPDATE on `access_tokens` —
  `docker/bootstrap-roles.sql:149` — so this is an operator step on the owner role). A migration never deletes them itself. The
  refusal message carries the exact statements:
  ```sql
  DELETE FROM access_token_stations
    WHERE token_id IN (SELECT id FROM access_tokens WHERE role = 'reviewer');
  DELETE FROM access_tokens WHERE role = 'reviewer';
  ```
  run as the database owner: `docker compose exec -T postgres psql -U ${DB_USER:-sapphire} -d sapphire`
  (the owner role of `docker/bootstrap-roles.sql:3`).
- `tests/unit/db/test_alembic_head_release_b.py` — the head pin moves to the new revision.
- Tests: `tests/unit/types/test_auth.py`, `tests/integration/store/test_access_token_store.py` (the
  cross-tenant cases parameterized), and the new
  `tests/integration/db/test_migration_<rev>_reviewer_role.py`.
- `store/access_token_store.py` — no logic change (it refuses only admin scopes); the comment at
  `:67-73` stops saying a scope belongs only to a consumer.
- `docs/standards/cicd.md` § Rollback — **before redeploying an image older than this plan, delete
  every reviewer token** with the same two statements and command, because that image cannot parse
  the role.

**Out:** any change to consumer/admin rows or rules.

**Pre-change:** with only the enum member added, a test asserting that
`AccessToken(role=REVIEWER, tenant_id=None, …)` raises fails — it constructs, because
`__post_init__` requires a tenant only for consumers (the fail-open gap); and a migration test
inserting a reviewer row with a tenant fails on `ck_access_tokens_role`.

**Verification:** `uv run pytest tests/unit/types/test_auth.py tests/unit/db/test_alembic_head_release_b.py tests/integration/store/test_access_token_store.py tests/integration/db/` including a new `tests/integration/db/test_migration_<rev>_reviewer_role.py` modelled on `test_migration_0049_scope_mode.py` — upgrade; reviewer with a tenant accepted; reviewer without a tenant rejected; reviewer with `scope_mode = 'tenant'` accepted; the existing cross-tenant store cases
(`tests/integration/store/test_access_token_store.py`, the consumer-only cases at `:96`, `:146`,
`:340`) parameterized over consumer **and** reviewer — a foreign-tenant station is refused at
create/grant and never enters a tenant-mode scope; admin with `scope_mode = 'tenant'` still rejected; downgrade refused while a reviewer row exists **and still refused after revoking it**; downgrade succeeds on a database with no reviewer rows, **after which** a reviewer insert fails on
`ck_access_tokens_role`, and the definitions of all three constraints read back with
`pg_get_constraintdef` equal the readback **captured at the prior revision before upgrading** (Postgres
normalises the text, so the comparison is readback against readback, not against the migration
source).

### T2 — the auth dependency and route classification

**Outcome:** `require_reviewer` admits reviewer and admin tokens and rejects a consumer with 403;
reviewer tokens behave exactly like consumers on every existing route; an out-of-scope forecast id
answers exactly like an absent one, for consumer and reviewer tokens.

**In:** `api/security.py` — `Principal.can_review` (reviewer or admin) and `require_reviewer`;
`station_in_scope` unchanged. `tests/unit/api/test_security.py` — `_classify_routes` learns REVIEW;
`TestRouteAuthMatrixExhaustive` gains a reviewer dimension. No REVIEW route exists until Plan 402,
so the dependency is also exercised on a test-only app.
`api/routes/api_forecasts.py` — `GET /api/v1/forecasts/{id}` answers an out-of-scope forecast with the
same 404 **body** as an absent one ("Forecast not found"), instead of "Station not found", which today
confirms that another scope's forecast exists (security review, 2026-09-26). This changes an error
text only — the one change here that consumer tokens also see.
`tests/integration/api/test_access_token_auth.py` — the cross-tenant HTTP cases parameterized.

**Out:** gating or reclassifying any existing route.

**Pre-change:** two test-app routes standing for a REVIEW route, each taking a station: one gated
with `require_principal` admits a consumer (the test asserting 403 fails), and one gated with
`require_admin` refuses a reviewer with 403 (the test asserting 200 fails) — the two halves of the
gap. T2 switches both to `require_reviewer`. And the absent-vs-out-of-scope parity test on
`GET /api/v1/forecasts/{id}` fails today — the bodies are "Forecast not found" vs "Station not found".

**Verification:** `uv run pytest tests/unit/api/test_security.py tests/integration/api/test_access_token_auth.py` — on the test-app REVIEW route, reviewer → 200 for an in-scope station and 404 for an out-of-scope
one (a REVIEW route applies the principal's station scope like any other); reviewer → 200 on every **GET** PRINCIPAL route for an in-scope station; for an out-of-scope station, 404 on detail routes and 200 with a filtered or empty result on collection routes (station list, alerts) — exactly what a consumer gets; the acknowledgement POST → 501, as for a consumer; 403 on every ADMIN route; consumer → 403 on a REVIEW route (test app); **reviewer → 200 on it**; admin → 200 on it; the station, alert and forecast-lab scope filters give a reviewer exactly a consumer's result for the same scope; for consumer and reviewer tokens, `GET /api/v1/forecasts/{id}` returns an identical status **and body** for an absent forecast and an out-of-scope one; the existing cross-tenant HTTP cases in `tests/integration/api/test_access_token_auth.py` (consumer-only at `:354`, `:555`) parameterized over consumer and reviewer — a station outside the token's tenant is rejected, and an out-of-band cross-tenant scope row yields 401.

### T3 — issuing and managing reviewer tokens

**Outcome:** an operator can create, show, list, revoke and re-scope a reviewer token.

**In:** `cli/access_tokens.py` — `create-reviewer` (`--name`, `--tenant` required, repeatable
`--station`, `--expires-days`), mirroring `create`; the role choice at `:579-592` handles three
roles; `grant`, `revoke-station` and `set-scope-mode` accept reviewer tokens; `show`/`list` print the
role. Structured log events per `docs/standards/logging.md`.

**Out:** any web issuance or self-service.

**Pre-change:** `uv run python -m sapphire_flow.cli.access_tokens create-reviewer --name x --tenant y` exits with an invalid-choice error.

**Verification:** `uv run pytest tests/unit/cli/test_access_tokens.py tests/integration/cli/test_access_tokens_cli.py` — create with and without `--tenant` (the latter refused); grant/revoke-station/set-scope-mode round-trip on a reviewer; admin refusals unchanged.

### T4 — documents and docstrings

**Outcome:** every document and docstring describing the role set or the token CLI describes three
roles, with GET-only unchanged.

**In:**
- `docs/standards/security.md` — the role model at `:17` (amending G4's role list, citing D1); the
  CLI list (`:63`); "the two HTTP read roles" (`:88`); the realised-status note (`:254-262`); the
  input-quality section's "only `consumer` and `admin`" (`:296-301`); pepper rotation (`:341`);
  the endpoint matrix (a reviewer column; the REVIEW class); and a line that a reviewer token is
  held server-side by its dashboard; the consumer-surface sentence (`:48`); the scope rules worded
  for consumers only (`:57-59`); the CLI summary (`:204`); the REVIEW class, stating that every
  REVIEW route serving station data applies the principal's station scope (404 on detail routes,
  filtering on collections), and that a REVIEW route serving forecast values applies Plan 341's
  publication gate to reviewer tokens where it is active (Plan 404 D4; admins keep full access there, per Plan 341); and the D4 tenant-mode rule (tenant mode only when every station in
  the tenant belongs to that dashboard's client; tokens cannot span or change tenants; the Nepal
  token binds to the DHM tenant).
- `docs/standards/cicd.md` — the pepper-rotation re-creation step (`create`/`create-admin` →
  add `create-reviewer`); § Station scope management (`grant`, `revoke-station`, `set-scope-mode`
  act on reviewer tokens too, and its "when to reach for tenant mode" guidance carries the D4
  rule); and any mention of `ck_access_tokens_tenant_mode_is_consumer`'s meaning.
- `docs/architecture-context.md` (`access_tokens.role` values), `docs/spec/database-schema.md:1075`
  (`role "consumer | admin"`), `docs/conventions.md:74-77` (roles and CLI command list), `docs/v1-scope.md:379` (role list), `docs/handover/it-operations.md` and `docs/standards/plan-147-mini-rollout.md` (token
  issuance), `docs/touchpoint-maps.md` (API auth paragraph), `docs/spec/types-and-protocols.md`
  (the `AccessTokenRole`/`ScopeMode` definitions at `:304-310` — "exactly 2 roles", consumer-only
  scope modes; `AccessTokenRole` at `:1347`, "consumer-only" at `:1354`, the CLI set at `:1374`, "the two HTTP
  read roles" at `:1418-1419`, and `require_principal`/`require_admin` at `:1370` — add
  `require_reviewer`).
- Docstrings and comments: `api/security.py:10-14,135-137`, `types/auth.py:129-155,185`,
  `types/enums.py:331-350`, `types/write_principal.py:14`, `db/metadata.py:2041-2053`,
  `cli/access_tokens.py:1-6,588-590`.

**Out:** rewriting archived Plan 147.

**Pre-change:** N/A — documentation.

**Verification:** a bounded inspection. Run
`grep -rniE 'two roles|two HTTP|third.{0,6}role|exactly two|consumer-only|consumer.{0,12}admin|CONSUMER.{0,40}ADMIN|create-admin' docs/ src/ --include='*.md' --include='*.py' | grep -v 'docs/plans/'`
and read every hit: each must either name all three roles, or be one of the expected residual
classes — a `create-admin` command line (bootstrap/rotation instructions), a legitimate per-role
code branch, or an unrelated match (e.g. "exactly two sentences"). Plan 341 still carries the reviewer
default (`341:28,30,48,106`), and **each In-listed location is changed in the branch diff, checked item by item**
(the grep alone cannot show that).

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/401-reviewer-access-token-role.md
```

After staging deploy (orchestrator): run the migration; create one reviewer token with
`--tenant sapphire`, **scoped to one station** (`scope_mode = stations`); confirm 200 on `/api/v1/stations`
listing only that station, 404 on another existing station's detail route, 403 on `/tables/`; then
**delete** that one token by id — its `access_token_stations` rows, then its `access_tokens` row
(`... WHERE token_id = '<id>'` / `WHERE id = '<id>'`); the role-wide statements of T1 are for
downgrade and rollback only, and would also delete any dashboard tokens already issued. The downgrade refusal is exercised by T1's migration test, never on staging.

## Explicitly out of scope

- Any write by a token, alert acknowledgement, publishing (341).
- Reviewer access to unpublished forecasts under 341's gate (341 decides).
- Human sign-in, sessions, MFA, identity providers (341, v1.x).
- Re-gating any existing admin-only route (e.g. the forcing/baselines `.json` exports) for
  reviewers — each needs its own decision.
- Token expiry/rotation policy changes.

## Changelog

- 2026-09-26 — drafted at the owner's request. Decisions: D1 (a third role, `reviewer`), D2 (a
  consumer plus REVIEW routes; no unpublished-forecast access), D3 (publishing is a named person), D4 (confirms Plan 268 D11: the DHM gauges get their own tenant; the Nepal dashboard's token binds to it only).
- 2026-09-26 — the owner-commissioned security review found one minor issue (an out-of-scope
  forecast's 404 revealed its existence); fixed in T2.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"]},
    {"id": "phase-2", "tasks": ["T2", "T3"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"]}
  ]
}
```
