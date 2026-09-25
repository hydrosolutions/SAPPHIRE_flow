---
status: DRAFT
created: 2026-09-24
plan: 341
title: CHWRR forecast review and publication API
depends_on: [340]
blocks: [342, 343, 344]
related: [042, 147, 266, 273]
reviews: []
open_decisions:
  - The CHWRR identity provider is not yet selected. The owner accepted an OIDC contract tested against a test provider; confirm the real issuer, API audience, signing keys/algorithm, subject claims, and dashboard origin before enabling CHWRR writes. Revisit this plan if the chosen provider cannot supply this contract.
  - Confirm that the chosen identity provider supplies an MFA assurance claim (or an equivalent verifiable login policy) meeting `docs/standards/security.md`; a valid signed token alone does not establish MFA.
source: 2026-09-24 owner — a selected CHWRR hydrologist must explicitly publish each chosen forecast for authenticated API use, with historical publication and audited withdrawal.
---

# Plan 341 — CHWRR forecast review and publication API

## Status and scope

**DRAFT — not implementable.** Security, human authorization, database migration, and external API behavior make this high-risk under `docs/workflow.md`. Resolve the independent-review findings before readiness is considered; only the orchestrator may later set READY.

This is backend support for the separately built `sapphire-flow-map` dashboard. It follows Plan 340's first-run evidence capture and covers forecast review, one-forecast publication, withdrawal, and authenticated consumer reads. It does not build the dashboard, bulletin artifacts, warning publication (Plan 342), CAP (Plan 266), email/Slack delivery (Plan 110), or model editing. `docs/v0-scope.md` intentionally deferred Flow 3; this is a CHWRR follow-on, not a claim that v0 already shipped review.

## Existing boundary and decisions

- `ForecastStatus` has `raw | reviewed | published`, and `PgForecastStore.transition_status` changes status in place with optimistic versioning but has no API caller or audit event. `audit_log` and `AuditEventType.FORECAST_STATUS_CHANGE` exist (`types/enums.py`, `store/forecast_store.py`, `db/metadata.py`).
- Current authenticated `GET /api/v1/forecasts/{id}`, station forecast list, and forecast-lab snapshot can expose raw forecasts (`api/routes/api_forecasts.py`, `api_stations.py`, `forecast_lab.py`). A publication gate must cover all consumer-facing forecast paths, including legacy JSON and caches/exports.
- Service access tokens are GET-only consumer/admin identities, not individual hydrologists (`api/security.py`, `standards/security.md`). Keep their read-only contract. A verified CHWRR human identity and a local allowlist grant the write permission.
- The architecture's `users`/password/TOTP model is a v1.x design, not an implemented human-user store or login flow (`docs/standards/security.md`). Reuse its local user identity and audit concept: one local user UUID linked to external `(issuer, sub)`, active/tenant status and station grants. Use OIDC for authentication; do not create a parallel hydrologist identity silo or build local password/TOTP login for this MVP. If the future human-session stack is built, it must reuse these user IDs and grants or migrate them explicitly.
- Activate published-only reads per CHWRR tenant after the review/write path, Plan 342's warning recheck and Plan 340's protected evidence backup/restore are ready. Preserve existing Swiss/internal diagnostic access to RAW forecasts in a separately authorized internal route; do not change every service-token route globally while this tenant-specific workflow is being introduced. Explicitly classify each existing API/legacy JSON/forecast-lab/export route as consumer or internal diagnostic and test both roles.
- Rollback after activation is a security operation as well as a schema operation: a pre-gate API image would expose RAW forecasts through its old reads. The deployment runbook must block CHWRR consumer access at the proxy or keep the new gate-capable image in service while restoring data. Do not redeploy a pre-gate image with CHWRR consumer traffic enabled. Prove the rollback path in staging.
- **One click publishes one immutable forecast ID.** The transition is `RAW -> PUBLISHED`; `REVIEWED` remains reserved for later multi-step review. The publisher may select only one PUBLISHED forecast for each `(station_id, parameter, issued_at)`, independent of model. A rejected/unselected forecast stays RAW.
- Publishing a forecast is a new threshold-check input. Emit a durable, idempotent publication event in the same transaction as the decision, containing the selected forecast ID, immutable output/version and actor. Plan 342 consumes it to evaluate the *selected* forecast under `threshold_check_mode=published|both`, even if the raw candidate came from a different model or pooled strategy. The CHWRR consumer publication mode must not be activated until that recheck and its human warning review path work; `raw` remains an internal early-warning path. Withdrawal also emits an event for warning reconciliation. Document the intentional one-click `RAW -> PUBLISHED` simplification against Flow 3 in `docs/architecture-context.md`.
- `PUBLISHED -> WITHDRAWN` requires a reason and preserves the original forecast and publication audit record. Withdrawal removes it from current consumer reads; a different forecast for that same station/parameter/issue time may then be published. No in-place correction or re-publication of a withdrawn ID. Older issue times remain queryable while published.
- Do not enable the CHWRR withdrawal route for a forecast with a current linked warning until Plan 342 supplies a joint forecast/warning withdrawal transaction. A later outbox event cannot enforce this invariant. Before that transaction exists, reject such withdrawal without changing either record; after it exists, one authorized action must commit both withdrawals and both audit records or neither.
- Consumer endpoints remain authenticated. `latest-published` returns one active published forecast per station/parameter, ordered by issue time with a deterministic tie breaker. Historical list/detail includes all still-published issue times; withdrawn publications remain visible as metadata-only tombstones so downstream caches can invalidate them. An authorized CHWRR audit view includes full withdrawn records and their decision history. No public unauthenticated path in this slice.

## API contract for the dashboard and consumers

| Principal | Route | Behavior |
|---|---|---|
| Verified, locally authorized CHWRR hydrologist | `GET /api/v1/review/forecasts?station_id=...&start=...&end=...` and `GET /api/v1/review/forecasts/{id}` | Scoped candidates with model, quality, version, status, publication history; pagination and bounded date range. |
| Same, with publish permission on station | `POST /api/v1/review/forecasts/{id}/publish` | Body contains `expected_version` and an idempotency key; returns publication decision and new version. |
| Same, with publish permission on station | `POST /api/v1/review/forecasts/{id}/withdraw` | Body contains `expected_version`, idempotency key, and required reason; returns withdrawal decision. |
| Existing service consumer or admin token | `GET /api/v1/stations/{id}/forecasts`, `GET /api/v1/forecasts/{id}`, `GET /api/v1/stations/{id}/forecasts/latest-published` | CHWRR publication gate applies to both; consumer station scope remains enforced, while admin retains its existing unscoped read role per `docs/standards/security.md`. A raw ID returns 404; withdrawn detail returns 410 with metadata-only tombstone. `GET` remains read-only. |
| Same read principals | `GET /api/v1/forecast-publications?cursor=...&limit=...` | Bounded, station-scoped publication and withdrawal pages for downstream cache invalidation; no unpublished forecast values. Return a stable next cursor. |

Response schema exposes stable forecast ID, station, parameter, issue/valid times, model/artifact identity, QC/input-quality fields, `status`, `version`, and publication timestamp. Review detail additionally exposes actor/time/reason decision history. Preserve pagination totals after publication and tenant/station filtering. Choose non-conflicting route ordering before implementation. The change feed must use a transactionally allocated, commit-ordered publication sequence as its cursor rather than timestamp alone; serialized sequence allocation makes a later-committing event sort after already visible events. Return an opaque next cursor and bounded pages in sequence order, including withdrawal tombstones. A client that retries a page or resumes after a same-time concurrent publication must neither lose nor invent an event. Document exact JSON in `docs/spec/types-and-protocols.md` and generated OpenAPI during implementation.

The proposed integration contract is CHWRR OIDC access tokens; the actual provider is not yet known. Implement against a test provider, then validate the real provider's capabilities before CHWRR activation. Validate configured issuer, audience, signature/JWKS, allowed algorithm, `iat`, `exp`, `nbf`, stable `sub`, and a trustworthy MFA assurance claim or equivalent enforced IdP policy; reject missing/malformed claims and cross-tenant or unlisted users. Enforce a maximum 30-minute human access-token lifetime (`exp - iat`) and bounded clock skew, matching `docs/standards/security.md`'s human-token exposure window; a real IdP that cannot supply this contract needs an explicit reviewed security-policy change before activation. Link `(issuer, sub)` to one local user UUID, active status, tenant, and station-scoped `review`/`publish` permissions; an operator-managed grant/revoke command is enough for MVP. Use an additive local user/external-identity schema compatible with the planned `users` model, not a second independent hydrologist account table. Do not derive permission solely from a client-provided name or group label. Capture human UUID in `audit_log.actor_id` (`actor_type=user`), and never log token bytes. IdP endpoint, claims, JWKS rollover, MFA assurance mechanism, and dashboard origin are deployment inputs, not assumed values. No service token may call a write route. Extend the current GET-only CORS policy with only the required review POST routes, headers and exact dashboard origin; do not use wildcard credentialed origins.

## MVP dashboard handoff (separate repository)

The other dashboard implementer can use this proposed contract to demo a station/parameter/issue-time forecast row with model, issue/valid times, QC/input-quality indicator and `RAW | PUBLISHED | WITHDRAWN` badge. A hydrologist with the station's publish grant can tick/select exactly one row and click **Publish**; the dashboard submits that forecast ID and displayed version, then refreshes from the API response. Show the competing model row as still RAW and the published forecast in the authenticated consumer view. Show prior issue times in history, a latest-published view, and an authorized withdrawal action that asks for a reason and produces a tombstone in consumer history. A read-only hydrologist can inspect but sees no enabled write control. The backend, not the UI, enforces all permissions and the one-selected-forecast constraint. Until these DRAFT plans are implemented, a demo must label local/mock responses as illustrative rather than implying the live backend already supports publication.

## Tasks

### T1 — Human principal and scoped authorization

**Outcome.** A CHWRR access token resolves to an attributable, locally authorized human principal; write permission is per station and revocable.

**In / Out.** In: `api/security.py` or separate human dependency, typed auth values, local user/external-identity/scope schema and store compatible with the planned `users` model, operator grant/revoke CLI, API configuration and exact-origin CORS, `docker/bootstrap-roles.sql`, `docs/standards/security.md`. The role bootstrap's blanket table SELECT must be followed by explicit, idempotent `sapphire_worker` SELECT revocation on every new human user, external-identity and grant table; preserve only the API role's required access. Verify the real IdP's MFA guarantee before activation; a test IdP is for contract testing only. Out: password handling, SAPPHIRE-issued human sessions, service-token write grants, dashboard UI.

**Verification.** `uv run pytest tests/unit/api/test_human_auth.py tests/integration/store/test_hydrologist_identity_store.py`; role-grant integration test for `sapphire_api` and explicit `sapphire_worker` SELECT denial on every new human identity/grant table after initial and repeated bootstrap; verify allowed/revoked, expired, wrong audience/issuer/signature, missing/old `iat`, signed token valid for more than 30 minutes, excessive clock skew, absent or inadequate MFA assurance, missing claim, cross-station, and disallowed CORS-origin cases.

**Pre-change.** RED: no human principal/permission store exists and existing service tokens cannot identify an individual reviewer.

### T2 — Atomic forecast decision and durable audit

**Outcome.** A publish or withdraw request commits the status change and exactly one attributable append-only audit event in one transaction; retry and concurrent publisher behavior is deterministic.

**In / Out.** In: additive publication/withdrawal decision state and schema, store transition API, unique partial index for `(station_id, parameter, issued_at)` where publication is active, durable publication/withdrawal event with a transactionally allocated commit-ordered change-feed sequence, `audit_log` use, least-privilege API grants, typed decision/reason schema. Do not persist `ForecastStatus.WITHDRAWN` until a prior image can read the expanded enum: stage the new state in an additive publication table or deploy reader support first, then activate new writes. Preserve one-version schema compatibility and demonstrate rollback per `docs/standards/cicd.md`. Out: deleting forecasts, changing forecast values or model assignments, a general review workflow engine.

**Verification.** `uv run pytest tests/integration/store/test_forecast_publication_store.py tests/integration/db/test_migration_forecast_publication.py`; prove atomic rollback when audit/event insert fails, unique index race, stale version `409`, duplicate idempotency key same result, reused key with different request `409`, withdrawal history retained, invalid transition rejected, and the previous image's reader against the expanded schema before new-state writes are enabled. Check commit-ordered sequence with equal timestamps and concurrent transactions. Before Plan 342's joint transaction is active, a forecast with a current linked warning cannot be withdrawn; after activation, injected failure rolls both decisions and audit records back.

**Pre-change.** RED: status update has no audit and no `withdrawn` state or one-selected-forecast database invariant.

### T3 — Review writes and publication reads

**Outcome.** The dashboard can inspect candidates and publish/withdraw one forecast; activated CHWRR consumer reads return only active published forecasts and retain prior published cycles.

**In / Out.** In: review and consumer API routes/schemas, store queries, OpenAPI, cache invalidation, all forecast-capable API/legacy JSON/forecast-lab/export surfaces, dashboard handoff contract and tenant-scoped activation switch. Out: `sapphire-flow-map` repository, bulk publish, public anonymous feed. Every activated CHWRR consumer route, including legacy `data.json` and forecast-lab if offered to consumers, is PUBLISHED-only; authorized internal diagnostic routes keep explicit RAW access for other tenants and CHWRR reviewers. Historical CHWRR consumer responses include withdrawn tombstones but never withdrawn values.

**Verification.** `uv run pytest tests/unit/api/test_forecast_publication_api.py tests/unit/api/test_api_forecasts.py tests/unit/api/test_api_stations.py tests/unit/api/test_forecast_lab.py`; integration test with two models at one issue time, older issue time, withdrawal/replacement, station-scoped consumer versus unscoped admin, bounded tombstone/change-feed pagination with same-timestamp concurrent inserts and retry/resume, selected-forecast publication event/retry, and a route inventory that cannot disclose RAW/WITHDRAWN values to an activated CHWRR consumer while legacy internal diagnostics still work under explicit authorization. In staging, prove consumer access is blocked before a pre-gate image is run during rollback.

**Pre-change.** RED: existing GET routes expose RAW forecasts; publish/withdraw/latest-published routes return 404 or 405.

### T4 — Documentation and operations handoff

**Outcome.** CHWRR and the other dashboard implementer have an exact workflow and API contract, including the meaning of withdrawal and API visibility.

**In / Out.** In: `docs/architecture-context.md`, `docs/spec/types-and-protocols.md`, `docs/standards/security.md`, `docs/standards/logging.md`, `docs/standards/cicd.md` activation/rollback runbook, API docs and affected handover text. Out: UI implementation or a claim that bulletin publication exists.

**Verification.** Bounded inspection of generated OpenAPI against the documented contract; `uv run pytest tests/unit/api/test_forecast_publication_api.py`.

**Pre-change.** N/A — documentation/handoff task; T1–T3 provide behavioral RED evidence.

## Exit gates

The task checks pass; migration and role tests cover a real PostgreSQL schema and the prior image's read compatibility; the changed-module Ruff and pyright gates in `docs/workflow.md` pass. Before merge, the full suite passes after the final code change. The IdP deployment inputs, MFA guarantee and dashboard origin must be confirmed before enabling writes outside a test IdP. Keep CHWRR withdrawal of linked-warning forecasts disabled until Plan 342's joint transaction exists. Activate CHWRR consumer publication mode only after Plan 342 rechecks the selected forecast and gates every warning outlet and Plan 340's protected backup/restore and no-cleanup policy cover publication history; keep the activation switch off in Swiss/internal deployments.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"]},
    {"id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"]},
    {"id": "phase-4", "tasks": ["T4"], "depends_on": ["phase-3"]}
  ]
}
```
