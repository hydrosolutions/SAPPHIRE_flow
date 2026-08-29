---
status: DRAFT
created: 2026-08-29
plan: 215
title: Consumer token scope has no lifecycle — widening it requires raw SQL, and 34 station promotions are coming
scope: Give consumer access tokens a supported way to gain and lose station scope, and decide the scoping policy for the SAPPHIRE-flow-map token. Does NOT change the auth model, the eligibility filter, or how tokens are minted.
depends_on: []
blocks: []
source: 2026-08-29 — granting station 2011 to the map token required a hand-written INSERT because no CLI command exists
---

# Plan 215 — a token's station scope can be created and destroyed, but never changed

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality

**This is a small gap with an outsized operational tail.** One missing CLI verb, one policy decision.
It is not an auth redesign — the model (consumer tokens, per-station grants, tenant containment,
fail-closed on a null station) is sound and stays exactly as it is.

### Reviewers: DO NOT OVER-ENGINEER THIS PLAN (owner instruction, 2026-08-29)

The whole change is **two CLI verbs, one nullable column, and one branch in the token loader**. It
touches authentication, which invites grand proposals; resist that. **A review that grows this plan
is a worse review than one that finds nothing**, and "no findings" is a complete review.

**In scope for findings:** D2's mechanism is wrong or unsafe; the tenant-containment invariant can be
escaped; a locking test would pass against the buggy implementation; the migration breaks existing
tokens; `station_in_scope`'s contract is actually changed rather than preserved; a command could
leak a token value or hash.

**Explicitly OUT of scope — do not propose, and reject if proposed:**

- Any auth redesign: RBAC, role hierarchies, scopes-as-strings, permission matrices, OAuth/OIDC,
  JWTs, an admin UI, or a policy engine.
- Token rotation, expiry policy, or revocation workflows — listed under Not in scope and unchanged.
- Audit-log subsystems, event sourcing of grants, or "who changed this scope" history. A `created_at`
  already exists; that is the whole audit story this deployment needs.
- Caching or performance work on the token loader. It is one indexed query per authenticated
  request against a 37-row table.
- Changing `access_token_stations`' schema — it is already correct (composite PK, both FKs).
- Generalising beyond two scope modes. There are exactly two, and a third is not anticipated.
- Reopening **D1** (tenant-wide grant, applied and verified) or **T3** (tenant-membership decides
  scope). Both are owner-resolved.

**If a reviewer believes a genuinely blocking problem sits outside these bounds, say so in one
sentence and stop there** — do not design the fix into this plan.

## What happened

Station **2011 (Sion)** became operational. It was fully eligible for the Forecast Lab export —
`bafu` / `river` / `operational`, same tenant — and it was **invisible to the map**, because the
`sapphire-flow-map` consumer token carried explicit grants for only `2009` and `2091`.

Fixing that required a **hand-written `INSERT` into `access_token_stations` against the production
database**, because `cli/access_tokens.py` has `create`, `admin`, `list` and `revoke` and **no verb
that widens or narrows an existing token's scope**. The insert was written to enforce the same
tenant invariant `create_token()` enforces (`_assert_stations_in_tenant`) by joining on
`tenant_id`, and it worked — the API now serves three stations. But an operator reaching for raw
SQL against an access-control table is the symptom this plan exists to remove.

**34 stations are still in `onboarding`.** Every promotion re-creates this situation.

## The two supported paths today, and why both are wrong

| path | cost |
|---|---|
| **Revoke and reissue** with a wider scope | Changes the token value. Every consumer must re-receive it out of band; the map 401s in the gap. For a routine promotion this is absurd. |
| **Raw SQL** | No validation beyond what the operator remembers to write, no audit trail, and it is exactly the habit that produces a bad day eventually. |

## D1 — the scoping policy — RESOLVED (Option A)

The Forecast Lab route computes **eligible ∩ scope** (`routes/forecast_lab.py:117`), so a granted
station that is not operational is inert *for this endpoint*. That makes a broader grant much safer
than it first sounds, and it is the crux of the decision.

**Option A — grant every station in the tenant, once.** The map then picks up each station the
moment it is promoted, with **no token change ever again**. The eligibility filter remains the real
gate. Cost: the token can also read the 34 onboarding stations through the *other* scope-gated
endpoints — `api_stations`, `api_forecasts`, `api_alerts` — where there is no eligibility filter, so
it would see incomplete, unvalidated onboarding data.

**Option B — grant only operational stations, and re-grant on each promotion.** Least privilege
holds exactly. Cost: a manual step 34 more times, which *will* be forgotten, and the failure is
silent — a promoted station simply never appears, exactly as 2011 did not.

**RESOLVED — Option A, applied 2026-08-29.** The `sapphire-flow-map` token now holds **37 grants,
the whole tenant** (34 inserted; 2009/2011/2091 already present). Verified after applying:

- **The endpoint is unchanged: still serves exactly `2009`, `2011`, `2091`.** The eligibility filter
  is doing the gating the decision relied on, so the wider grant is inert for the Forecast Lab route
  — this was the load-bearing assumption and it is now measured, not assumed.
- **The predicted cost is real and bounded:** `GET /api/v1/stations` with this token returns **37**
  stations (34 `onboarding`, 3 `operational`). Every promotion from here appears in the export with
  no token change.
- **No cross-tenant exposure:** the deployment holds 37 stations in total, all in this tenant, so the
  grant reaches nothing it did not already logically cover. **This is why the check matters — on a
  multi-tenant deployment the same statement would be a materially different act**, and the
  `s.tenant_id = a.tenant_id` join is what keeps it contained.

**Recommendation (retained as rationale): A, given this is internal research and the map is the only consumer.** The data
the wider grant exposes is our own onboarding data, on the same tenant, to a token we issued for a
project we control. Weigh that against a silent-failure step repeated 34 times. **If the answer were
"an external partner's token", it would be B without hesitation** — and that difference is the point:
this is a *policy* decision that should be recorded per token, not a default buried in a script.

## Tasks (sketch — D1 and T3 now resolved; D2's mechanism wants a review pass)

**T1 — `access-tokens grant` / `revoke-station`.** Add the missing verbs to
`cli/access_tokens.py`, reusing the existing `_assert_stations_in_tenant` invariant rather than
re-implementing it. Idempotent (`ON CONFLICT DO NOTHING`), and refuses on an admin token exactly as
`create_token()` does ("admin tokens cannot carry a station scope").
*Exit:* a test proving a cross-tenant grant is rejected, and one proving a repeat grant is a no-op.

**T2 — `access-tokens show <name>`.** There is currently no way to see a token's scope without SQL.
`list` prints `scope=N station(s)` — a count, not the codes. An operator debugging "why can't the
consumer see station X" has no supported answer.
*Exit:* prints the station codes for a named token; never prints the token value or its hash.

**T3 — RESOLVED 2026-08-29: the map token covers every BAFU site in the Swiss research tenant,
automatically.** The owner's decision is about *behaviour*: a new BAFU station in this tenant is in
scope without anyone doing anything.

Note this is **broader and simpler than the question originally posed here**, which was whether
*promotion to operational* should trigger a grant. It is not promotion-triggered — it is
tenant-membership. That removes the objection this task was flagged for: there is no implicit
widening driven by a *status* change, because status is no longer what decides scope. The eligibility
filter still gates the Forecast Lab export (measured under D1), so a non-operational station in scope
remains inert there.

### D2 — mechanism (engineering call; recommendation, open to review)

**Do NOT implement this as an auto-grant hook or a reconciliation job.** That materialises a copy of
a fact the database already knows, and every materialised copy needs a sync path, has a drift window
between station creation and grant, and fails silently when the hook does not run — which is exactly
the failure this plan was opened to remove. It would also grow `access_token_stations` without bound
for no informational gain.

**Recommended: make "my scope is my tenant" a first-class mode, resolved at authentication.**

- Add a scope mode to `access_tokens` (`'stations'` | `'tenant'`; default `'stations'`, so every
  existing token is unchanged).
- In the token loader (`store/access_token_store.py:140-147`, which today reads
  `access_token_stations`), a `'tenant'`-mode token instead populates `station_ids` from
  `SELECT id FROM stations WHERE tenant_id = <token tenant>`.
- **`Principal.station_in_scope()` and every call site are untouched** — they keep receiving a
  `frozenset[StationId]` and cannot tell the difference. The auth model does not change; only the
  *source* of the set does.

Why this shape: scope becomes **derived, not stored**, so it cannot drift, needs no backfill, and a
station added at 09:00 is in scope at 09:00. It also makes the tenant-wide grant applied under D1
**redundant** — those 37 rows would be deleted when the map token switches mode, which is the
strongest signal the mode is the right abstraction.

Costs to weigh in review, honestly: one extra query per token load (the loader already queries, so
this replaces rather than adds); a `'tenant'`-mode token silently follows the tenant, so **the
tenant boundary becomes the only thing limiting it** — acceptable for an internal research consumer
we control, and it must be refused for any external-partner token; and `access-tokens list` /
`show` must display the mode prominently, or an operator reading `scope=37 station(s)` will not
realise it is dynamic.

*Exit:* a test proving a `'tenant'`-mode token picks up a station created **after** the token; one
proving a `'stations'`-mode token does not; one proving cross-tenant stations never enter scope in
either mode; and one proving an admin token still rejects any scope.

## Acceptance criteria

1. Widening a live token's scope needs no raw SQL and no reissue.
2. The tenant-containment invariant is enforced by the same code path as `create_token()`, not a
   second copy.
3. An operator can read a token's scope as station codes.
4. No command prints a token value or hash.
5. The map token's scope matches D1, recorded in the plan.
6. A new BAFU station in the Swiss research tenant is visible to the map token with **no operator
   action** — the T3 acceptance test, and the one that matters operationally.

## Not in scope

The auth model itself. Token rotation and expiry. The eligibility filter. Anything about *who* may
mint tokens. The `access_token_stations` schema — it is already correct (composite PK, both FKs).
