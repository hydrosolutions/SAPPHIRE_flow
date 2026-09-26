---
status: DRAFT
created: 2026-09-26
plan: 401
title: A reviewer access token for the review dashboards — read everything a review needs, for one client's stations, write nothing
scope: Add a third HTTP access-token role, `reviewer`, for the dashboards we use to review our forecast products (the BAFU/Swiss dashboard, the Nepal dashboard). A reviewer token is GET-only and tenant-bound and scoped exactly like a consumer token, and additionally reaches routes classified REVIEW (the first two arrive with Plan 345). Includes the role, the database constraint change, the auth dependency, CLI issuance, the route-classification test and the security documents. NOT publishing or any other write (tokens stay GET-only — publishing is a named person's act, Plan 341); NOT human sign-in, sessions or MFA; NOT opening any existing admin-only route to reviewers; NOT changing what consumer or admin tokens can do.
risk: high   # security/auth + migration (docs/workflow.md § High-risk work)
depends_on: []
blocks: [345]
related: [042, 147, 215, 341, 345]
open_decisions: [D3]
closed_decisions: [D1, D2]   # owner, 2026-09-26
source: 2026-09-26 — owner, while reviewing Plan 345: "could we have a special user for the BAFU dashboard and the Nepal dashboard?" — to replace the admin token Plan 345 D11 had the map use.
---

# Plan 401 — a reviewer access token for the review dashboards

## Status

**DRAFT — HIGH RISK — not reviewed.** Authentication and a migration are high-risk triggers
(`docs/workflow.md` § High-risk work): the ordinary Claude + Codex pair, plus one
owner-commissioned review before READY and again before the implementation PR.

## Why this exists

Plan 345 gives the flow map (the dashboard we use to demonstrate and review forecast products)
two new read routes — the QC rule sets and a station's skill — which a consumer token must not
reach. With only two roles today, the map would have had to hold an **admin** token: it reaches
every admin page and route and sees every client's stations, so a Swiss dashboard would also see
Nepal data. The owner asked for a dedicated identity for each dashboard instead.

The Nepal deployment needs three kinds of access, and this plan supplies exactly one of them:

| who | access | provided by |
|---|---|---|
| third parties | read their stations (published forecasts only, once 341 lands) | the existing **consumer** token |
| the Nepal (and Swiss) review dashboard, reading | read everything a review needs, for that client's stations | **this plan — `reviewer`** |
| a hydrologist publishing or withdrawing a forecast | a write, recorded against that person | **Plan 341** — a signed-in, named person with per-station permission; never a shared token |

## What is measured (origin/main, 2026-09-26)

- Roles: `AccessTokenRole` has `CONSUMER` and `ADMIN` (`types/enums.py:330`), documented there and in
  Plan 147 G4 as *"exactly two roles, both GET-only … No third role"*. G4's two halves are separable:
  **GET-only** (every token), and **two roles** (v1.0's role list). This plan changes only the
  second, by owner decision (D1).
- Tokens never write: `POST /api/v1/alerts/{id}/acknowledge` returns 501 (`api/routes/api_alerts.py`);
  writes use a separate `WritePrincipal` built from config, never from a token row
  (`types/write_principal.py`).
- The database pins the pairing: `ck_access_tokens_role` (`role IN ('consumer','admin')`),
  `ck_access_tokens_role_tenant` (admin → no tenant; consumer → a tenant), and
  `ck_access_tokens_tenant_mode_is_consumer` (`scope_mode = 'stations' OR role = 'consumer'`)
  (`db/metadata.py:2063-2102`; migrations 0047, 0049). `AccessToken.__post_init__`
  (`types/auth.py:171-186`) mirrors them.
- Every code site that branches on role tests `is_admin` / `ADMIN` and treats everything else as
  a scoped consumer: `api/security.py:131-142` (`Principal.is_admin`, `station_in_scope`),
  `api/routes/api_stations.py:174`, `api/routes/api_alerts.py:97`, `api/routes/forecast_lab.py:116`,
  `store/access_token_store.py:31,119`, `cli/access_tokens.py:201,303,579-592`. A reviewer that is
  **not** admin therefore falls into the scoped branch everywhere by construction — fail-closed.
- Route gating: routers mount with `Depends(require_principal)` or `Depends(require_admin)`
  (`api/__init__.py:84-101`); `require_admin` rejects a non-admin with 403. The route matrix
  (`tests/unit/api/test_security.py::TestRouteAuthMatrixExhaustive`, `_classify_routes`) derives
  PUBLIC / PRINCIPAL / ADMIN from each route's actual dependency graph.
- CLI: `access-tokens create` (consumer, `--tenant` required), `create-admin`, `list`, `revoke`,
  `show`, `grant`, `revoke-station`, `set-scope-mode` (`cli/access_tokens.py:419-470`).
- Latest migration on main: `0059_forecast_preservation.py`.

## Owner decisions

### D1 — a third HTTP role exists: `reviewer`. **⚖️ CLOSED — owner, 2026-09-26.**

This amends Plan 147 G4's *"no third role"* for the role list only. **GET-only stands for every
token.** The name is `reviewer` (what the dashboards do); a dashboard deployment is one token.

### D2 — a reviewer is a consumer plus the REVIEW routes. **⚖️ CLOSED — owner, 2026-09-26 (as proposed).**

A reviewer token is tenant-bound, uses the consumer's scope rules unchanged (both `scope_mode`s,
404 for an out-of-scope station), reaches every PRINCIPAL route, and additionally reaches routes
gated REVIEW. It never reaches an ADMIN route. No existing route is reclassified; Plan 345 adds the
first two REVIEW routes.

### D3 — publishing is a person, not a dashboard token. **OPEN — confirm.**

The owner asked for the Nepal dashboard to have "read and write for 'publish'". **Recommendation:**
the dashboard's token stays read-only; publishing is Plan 341's named, signed-in hydrologist with
per-station permission and an audit trail, acting through the dashboard. Reason: a publication is
someone accountable approving a forecast, and a shared token cannot say who; Plan 147 G4 also keeps
every token GET-only. This plan assumes the recommendation; if the owner wants a token-level write
instead, this plan's scope and G4 both change and it goes back to review.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate).

### T1 — the role, its invariants, and the migration

**Outcome:** `reviewer` is a valid role in the type and the database, bound to a tenant, allowed
either scope mode; nothing about consumer or admin changes.

**In:** `types/enums.py` (`AccessTokenRole.REVIEWER`, docstring), `types/auth.py::AccessToken.__post_init__`
(reviewer requires a tenant), `db/metadata.py` (the three constraints), a new alembic migration
(next free revision **at implementation time** — other plans are adding migrations) that widens
`ck_access_tokens_role`, `ck_access_tokens_role_tenant` (reviewer → tenant NOT NULL) and
`ck_access_tokens_tenant_mode_is_consumer` (renamed or re-expressed to allow reviewer). Its
**downgrade refuses** with a clear error while any `reviewer` row exists — rows are never rewritten
or deleted by a migration; the operator revokes them first. `store/access_token_store.py` —
reviewer tokens may carry a station scope, like consumers.

**Out:** any change to consumer/admin rows or rules.

**Pre-change:** a test constructing `AccessToken(role=REVIEWER, …)` fails on the missing enum
member; a migration test inserting a reviewer row fails on `ck_access_tokens_role`.

**Verification:** `uv run pytest tests/unit/types/ tests/unit/store/ tests/integration/store/test_access_token_store.py` plus a new `tests/integration/db/test_migration_<rev>_reviewer_role.py` modelled on `test_migration_0049_scope_mode.py`, plus `tests/unit/db/test_alembic_head_release_b.py` if the head pin moves (upgrade; reviewer row with a tenant accepted; reviewer without a tenant rejected; reviewer with `scope_mode = 'tenant'` accepted; downgrade refused while a reviewer row exists, succeeds after revoking it).

### T2 — the auth dependency and route classification

**Outcome:** `require_reviewer` admits reviewer and admin tokens and rejects a consumer with 403;
reviewer tokens are scoped exactly like consumers on every existing route.

**In:** `api/security.py` — `Principal.can_review` (reviewer or admin) and `require_reviewer`;
`station_in_scope` unchanged (reviewer is not admin, so scoped). `tests/unit/api/test_security.py` —
`_classify_routes` learns REVIEW; `TestRouteAuthMatrixExhaustive` gains a reviewer dimension: every
PRINCIPAL route admits a reviewer (scoped), every ADMIN route rejects it with 403. Because no REVIEW
route exists until Plan 345, the dependency is also exercised on a test-only app.

**Out:** gating or reclassifying any existing route.

**Pre-change:** a test calling an ADMIN route with a reviewer token — impossible to construct before
T1; after T1, a test asserting `require_reviewer` admits a reviewer fails on the missing dependency.

**Verification:** `uv run pytest tests/unit/api/test_security.py tests/integration/api/test_access_token_auth.py` — reviewer → 200 on PRINCIPAL routes, 404 on an out-of-scope station, 403 on every ADMIN route; consumer → 403 on a REVIEW route (test app); admin → 200 on a REVIEW route; the station/alert/forecast-lab scope filters give a reviewer exactly a consumer's result for the same scope.

### T3 — issuing and managing reviewer tokens

**Outcome:** an operator can create, show, list, revoke and re-scope a reviewer token.

**In:** `cli/access_tokens.py` — `create-reviewer` (`--name`, `--tenant` required, optional
`--stations`, `--expires-days`), mirroring `create`; `grant`, `revoke-station` and `set-scope-mode`
accept reviewer tokens; `show`/`list` print the role. Structured log events per
`docs/standards/logging.md`.

**Out:** any web issuance or self-service.

**Pre-change:** `access-tokens create-reviewer` exits with an unknown-command error.

**Verification:** `uv run pytest tests/unit/cli/test_access_tokens.py tests/integration/cli/test_access_tokens_cli.py` — create with and without `--tenant` (the latter refused); grant/revoke-station/set-scope-mode round-trip on a reviewer; admin refusals unchanged.

### T4 — documents

**Outcome:** the security standard, touchpoint map and specs describe three roles, with GET-only
unchanged.

**In:** `docs/standards/security.md` — the role model (amending G4's role list, citing D1), the
endpoint matrix (a reviewer column; the REVIEW class), and a line that a reviewer token must be held
server-side by its dashboard; `docs/touchpoint-maps.md` (API auth paragraph: roles, `require_reviewer`,
the matrix); `docs/spec/types-and-protocols.md` (`AccessTokenRole`); the token-issuance instructions in
`docs/handover/it-operations.md` and `docs/standards/plan-147-mini-rollout.md`.

**Out:** rewriting archived Plan 147.

**Pre-change:** N/A — documentation.

**Verification:** `grep -rn "exactly two roles\|No third role\|two-role" docs/ src/ --include=*.md --include=*.py` returns only historical records (archived plans) or text updated by this task.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/401-reviewer-access-token-role.md
```

After staging deploy (orchestrator): run the migration; create one reviewer token for the Swiss
tenant; confirm 200 on `/api/v1/stations` (Swiss stations only), 404 on a station of another
tenant, 403 on `/tables/`; revoke it; confirm the downgrade refusal message against a scratch copy,
not staging.

## Explicitly out of scope

- Any write by a token, alert acknowledgement, publishing (341).
- Human sign-in, sessions, MFA, identity providers (341, v1.x).
- Re-gating any existing admin-only route (e.g. the forcing/baselines `.json` exports) for
  reviewers — each needs its own decision.
- Token expiry/rotation policy changes.

## Changelog

- 2026-09-26 — drafted at the owner's request; D1–D2 closed by the owner the same day, D3 open to confirm.

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
