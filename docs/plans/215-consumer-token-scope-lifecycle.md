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
fail-closed on a null station) is sound and stays exactly as it is. Reviewers: do not propose
role hierarchies, scopes-as-strings, OAuth, or an admin UI.

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

## Tasks (sketch — not sliced until D1 is answered)

**T1 — `access-tokens grant` / `revoke-station`.** Add the missing verbs to
`cli/access_tokens.py`, reusing the existing `_assert_stations_in_tenant` invariant rather than
re-implementing it. Idempotent (`ON CONFLICT DO NOTHING`), and refuses on an admin token exactly as
`create_token()` does ("admin tokens cannot carry a station scope").
*Exit:* a test proving a cross-tenant grant is rejected, and one proving a repeat grant is a no-op.

**T2 — `access-tokens show <name>`.** There is currently no way to see a token's scope without SQL.
`list` prints `scope=N station(s)` — a count, not the codes. An operator debugging "why can't the
consumer see station X" has no supported answer.
*Exit:* prints the station codes for a named token; never prints the token value or its hash.

**T3 — decide whether promotion should grant automatically.** If D1 is B, a station promoted to
`operational` arguably should be granted to every token that already holds the tenant's other
operational stations. **This is the risky one** — implicit privilege widening driven by a status
change is how scopes quietly become universal. Probably better as a *warning* in the promotion path
("token X does not cover this station") than an automatic grant. **Do not implement without an
explicit decision.**

## Acceptance criteria

1. Widening a live token's scope needs no raw SQL and no reissue.
2. The tenant-containment invariant is enforced by the same code path as `create_token()`, not a
   second copy.
3. An operator can read a token's scope as station codes.
4. No command prints a token value or hash.
5. The map token's scope matches whatever D1 decides, recorded in the plan.

## Not in scope

The auth model itself. Token rotation and expiry. The eligibility filter. Anything about *who* may
mint tokens. The `access_token_stations` schema — it is already correct (composite PK, both FKs).
