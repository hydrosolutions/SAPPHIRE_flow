---
status: READY
created: 2026-08-29
plan: 215
title: Consumer token scope has no lifecycle — widening it requires raw SQL, and every station created from now on repeats it
scope: Give consumer access tokens a supported way to gain, lose and re-source station scope — three new CLI verbs plus a `show` reader, one `scope_mode` column, one branch in the token loader, and the one DELETE grant that makes scope removal executable under the production DB role. Does NOT change the roles, the route-level authorization call sites, the eligibility filter, or how tokens are minted.
depends_on: []
blocks: []
source: 2026-08-29 — granting station 2011 to the map token required a hand-written INSERT because no CLI command exists
---

# Plan 215 — a token's station scope can be created and destroyed, but never changed

## Status

**READY** — owner confirmed 2026-08-29. D1, T3 and D2.2's unfiltered-tenant question are all
resolved; the review round's four findings were verified against source and are folded. Cleared for
`/implement`, with **T9 excluded**: it is an operator step against production and stays the owner's.

Two residuals remain open and are deliberately **not** blocking — neither affects the code being
built:
1. Whether `'tenant'` mode becomes the standing default for future internal consumer tokens, or
   stays a per-token call. Today it applies to exactly one token.
2. Whether flipping the live map token's mode wants a second pair of eyes. Worth noting the rollback
   is **not** symmetric: reverting to `'stations'` mode leaves the token's scope **empty**, not
   restored, because T7's `revoke-station`/mode-switch deletes the grant rows. Re-granting is a
   separate step. Decide before running T9, not during.

## ⛔ Proportionality

**This is a small gap with an outsized operational tail.** A few missing CLI verbs, one policy
decision. It is not an auth redesign — the model (consumer tokens, per-station grants, tenant
containment, fail-closed on a null station) is sound and stays exactly as it is.

### Reviewers: DO NOT OVER-ENGINEER THIS PLAN (owner instruction, 2026-08-29)

The whole change is **three new CLI verbs (`grant`, `revoke-station`, `set-scope-mode`) plus a
`show` reader, one `NOT NULL` column with two CHECK constraints, one branch in the token loader, and
one added DELETE grant**. It touches authentication, which invites grand proposals; resist that. **A
review that grows this plan is a worse review than one that finds nothing**, and "no findings" is a
complete review.

> **Honest note on growth (review round 1, 2026-08-29).** This section previously said "two CLI
> verbs, one nullable column"; it also miscounted the existing surface (`admin` — the actual verb is
> `create-admin`, `cli/access_tokens.py:217`). Round 1 found two blockers that forced the surface up:
> nothing could put a token *into* `tenant` mode (so D2 was unreachable without the very raw SQL this
> plan exists to delete), and `sapphire_api` holds `INSERT` but not `DELETE` on
> `access_token_stations` (`docker/bootstrap-roles.sql:150`), so `revoke-station` and the mode-switch
> cleanup would both fail with `InsufficientPrivilege` in production. The column is also now
> `NOT NULL DEFAULT 'stations'` rather than nullable, deliberately: a nullable column admits an
> undefined third state in a two-mode design. The growth is exactly these three items and nothing
> else. Final surface: existing `create` / `create-admin` / `list` / `revoke`, plus
> `grant` / `revoke-station` / `show` / `set-scope-mode`.

**In scope for findings:** D2's mechanism is wrong or unsafe; the tenant-containment invariant can be
escaped; a locking test would pass against the buggy implementation; the migration breaks existing
tokens; `station_in_scope`'s contract is actually changed rather than preserved; a command could
leak a token value or hash; a scope verb is a silent no-op.

**Explicitly OUT of scope — do not propose, and reject if proposed:**

- Any auth redesign: RBAC, role hierarchies, scopes-as-strings, permission matrices, OAuth/OIDC,
  JWTs, an admin UI, or a policy engine.
- Token rotation, expiry policy, or revocation workflows — listed under Not in scope and unchanged.
- Audit-log subsystems, event sourcing of grants, or "who changed this scope" history. The scope
  verbs append **one** `audit_log` row each through the existing `PgAuditLogStore.append_entry`
  path, in the same transaction as the write, exactly as `create_token()` / `revoke_token()` already
  do (`cli/access_tokens.py:147-160,172-180`) — one new enum member reusing the existing append
  path is not a subsystem, and it is the whole audit story this deployment needs.
- Caching or performance work on the token loader. It is one indexed query per authenticated
  request against a 37-row table.
- Changing `access_token_stations`' schema — it is already correct (composite PK, both FKs). The
  role **grant** on that table changes (T7); the table itself does not.
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
database**, because `cli/access_tokens.py` has `create`, `create-admin`, `list` and `revoke`
(`cli/access_tokens.py:203,217,230,232`) and **no verb that widens or narrows an existing token's
scope**. The insert was written to enforce the same tenant invariant `create_token()` enforces
(`store/access_token_store.py:35,61`, `_assert_stations_in_tenant`) by joining on `tenant_id`, and it
worked — the API now serves three stations. But an operator reaching for raw SQL against an
access-control table is the symptom this plan exists to remove.

**What is and is not still open (corrected 2026-08-29, post-D1):** the 34 stations that were in
`onboarding` are **no longer the problem** — D1's one-off tenant-wide grant covers all 37 rows
currently in the deployment, so the *existing* promotion queue is handled. The residual problem is
narrower and permanent: (a) there is still no supported verb, so the next scope edit of any kind is
raw SQL again, and (b) every station **created after 2026-08-29** lands outside that materialised
grant set and re-creates the exact 2011 failure, silently. D2 addresses (b); T1/T2/T6 address (a).

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

## T3 — RESOLVED 2026-08-29: the map token covers every BAFU site in the Swiss research tenant, automatically

The owner's decision is about *behaviour*: a new BAFU station in this tenant is in scope without
anyone doing anything.

Note this is **broader and simpler than the question originally posed here**, which was whether
*promotion to operational* should trigger a grant. It is not promotion-triggered — it is
tenant-membership. That removes the objection this task was flagged for: there is no implicit
widening driven by a *status* change, because status is no longer what decides scope. The eligibility
filter still gates the Forecast Lab export (measured under D1), so a non-operational station in scope
remains inert there.

**One clarification, added in review round 2:** "BAFU site" here is *descriptive of today's tenant*,
not a predicate the implementation applies. The mechanism D2.2 specifies is tenant-membership,
unfiltered by `network` or `station_kind` — see D2.2's "deliberately unfiltered" bullet for why, its
cost, and the T5 test that locks it. T3's decision is unchanged; only the wording is disambiguated.

## D2 — mechanism (engineering call; recommendation, open to review)

**Do NOT implement this as an auto-grant hook or a reconciliation job.** That materialises a copy of
a fact the database already knows, and every materialised copy needs a sync path, has a drift window
between station creation and grant, and fails silently when the hook does not run — which is exactly
the failure this plan was opened to remove. It would also grow `access_token_stations` without bound
for no informational gain.

**Recommended: make "my scope is my tenant" a first-class mode, resolved at authentication.**

### D2.1 — the column (exact contract, specified for migration `0049`)

- Column: `access_tokens.scope_mode TEXT NOT NULL` with `server_default='stations'`. Named
  `scope_mode`, not `mode` — a bare `mode` reads as a verbosity/role flag next to `role`.
- **Representation is TEXT + CHECK, not a PG enum** — matching the value-constrained column already
  on this table (`ck_access_tokens_role`, `db/metadata.py:1665-1672`). A PG enum would be the only
  one in the schema and needs a migration to extend.
- `CHECK (scope_mode IN ('stations', 'tenant'))`, name `ck_access_tokens_scope_mode`.
- `CHECK (scope_mode = 'stations' OR role = 'consumer')`, name
  `ck_access_tokens_tenant_mode_is_consumer` — an admin row can never reach `'tenant'` mode. Paired
  with the existing `ck_access_tokens_role_tenant` (`db/metadata.py:1686-1690`), which already forces
  `role='admin' -> tenant_id IS NULL`, "a tenant-mode token with no tenant" becomes structurally
  unrepresentable rather than merely unlikely.
- **Backfill:** `ADD COLUMN ... NOT NULL DEFAULT 'stations'` fills every existing row in one
  statement (PG ≥ 11, no table rewrite). No separate backfill step, and **every existing token —
  consumer and admin alike — keeps exactly today's behaviour**. There is no third state: `NOT NULL`
  plus the value CHECK leaves precisely two.
- **Downgrade:** drop both CHECKs, then drop the column. Tokens revert to materialised scope; a token
  that was in `'tenant'` mode is left with whatever `access_token_stations` rows it has — after T6's
  cleanup, **none**. Downgrade is therefore fail-closed (that token sees nothing), not fail-open.
  Stated here so the operator is not surprised by it.
- `db/metadata.py`'s `access_tokens` table (`:1658-1693`) gains the column and both CHECKs; the
  pinned Alembic head in `tests/unit/db/test_alembic_head_release_b.py:57`
  (`_RELEASE_B_HEAD = "0048"`) advances to `"0049"` with the customary comment line.
- **Parsing is fail-closed:** `ScopeMode(row["scope_mode"])` in `_row_to_token`
  (`store/access_token_store.py:137`). An unexpected value raises `ValueError` and the auth attempt
  fails; it never degrades into "no mode, assume tenant". A NULL is already impossible (`NOT NULL`);
  the enum parse is the second line of defence for a value written out-of-band.
- **`AccessToken` gains `scope_mode: ScopeMode`** (`types/auth.py:148-159`) with a `__post_init__`
  clause mirroring `ck_access_tokens_tenant_mode_is_consumer`, in the same style as the existing
  role/tenant clauses at `types/auth.py:161-172`.

### D2.2 — the loader branch

- In the token loader (`store/access_token_store.py:137-147`, which today reads
  `access_token_stations`), a `'tenant'`-mode token instead populates `station_ids` from
  `SELECT id FROM stations WHERE tenant_id = <token tenant>`.
- The existing fail-closed re-validation (`_assert_stations_in_tenant`, called from the read path at
  `store/access_token_store.py:149-156`) is **kept for `'stations'` mode and skipped for `'tenant'`
  mode**, where the same tenant predicate produced the set and re-asserting it is a tautology over
  the rows just read.
- **`Principal.station_in_scope()` and every call site are untouched**
  (`api/security.py:120,134-142`) — they keep receiving a `frozenset[StationId]` and cannot tell the
  difference.
- **The tenant-mode set is the whole tenant, deliberately unfiltered — decided in review round 2,
  and CONFIRMED BY THE OWNER 2026-08-29** ("no need to filter to BAFU for now — we're doing research
  with public data here"). The widening is accepted knowingly, on the stated basis that this tenant
  holds public Swiss research data and the map is the only consumer. **That basis is the thing to
  re-check if it ever stops being true** — a tenant carrying non-public or partner data must not use
  `'tenant'` mode; `'stations'` mode already expresses the narrower scope exactly.** The query carries no `network` or `station_kind` predicate (`db/metadata.py:238-243` for
  `station_kind`, `:273` for `network`). Round 2 asked whether it should read
  `... AND network = 'bafu' AND station_kind = 'river'` to match T3's "every BAFU site" wording. It
  should not, and the widening is **named rather than left implicit**:
  1. **It is not broader than what is already live.** D1 applied **37 grants — every station in the
     tenant, with no network/kind filter** — and measured the result (`GET /api/v1/stations` → 37,
     34 `onboarding` + 3 `operational`). Tenant mode *derives* exactly that set instead of
     materialising it. A narrowing predicate would make T9's cutover unable to reproduce D1's two
     measured numbers, which is the acceptance criterion (AC5).
  2. A network/kind predicate hidden inside a mode named `tenant` is an invisible policy in a plan
     whose whole point is that scope policy must be visible per token. A BAFU-only scope is already
     exactly expressible — it is `'stations'` mode.

  **The cost, stated plainly, because it is one axis wider than the sentence T3 uses:** T3's "every
  BAFU site in the Swiss research tenant" describes **the tenant's current composition, not a filter
  the code applies**. A `weather`-kind or non-`bafu`-network station added to this tenant later
  **does** enter a tenant-mode token's scope, readable through the endpoints that have no eligibility
  filter — `api_stations`, `api_forecasts`, `api_alerts` — the same endpoints and the same trade D1
  costed and the owner accepted. It is measured rather than assumed: **T9 records today's
  `network` × `station_kind` breakdown of the tenant** alongside D1's two numbers (the one thing D1
  measured only by status), and **T5 locks the semantics with an explicit test** so the behaviour
  cannot drift into either shape by accident.

**On "the auth model does not change" (corrected, review round 1):** the *roles*
(`consumer`/`admin`), the route-level call sites, and `station_in_scope`'s contract are genuinely
unchanged. But the authoritative docs currently lock the scope **carrier** to `access_token_stations`
(`docs/standards/security.md:17,52`; `docs/spec/types-and-protocols.md:1277`;
`docs/touchpoint-maps.md:567`; and the `AccessToken` docstring, `types/auth.py:130-135`). This plan
adds a **second consumer scope-resolution mode** — a real extension of a documented contract — so it
carries a doc task (T8) rather than a "nothing to see here" claim.

Why this shape: scope becomes **derived, not stored**, so it cannot drift, needs no backfill, and a
station added at 09:00 is in scope at 09:00. It also makes the tenant-wide grant applied under D1
**redundant** — those 37 rows are deleted by T6 when the map token switches mode, which is the
strongest signal the mode is the right abstraction.

Costs to weigh in review, honestly:

- One extra query per token load (the loader already queries, so this replaces rather than adds).
  Bounded by stations-in-tenant: 37 today, ~1000 at the documented v0 scale.
- A `'tenant'`-mode token silently follows the tenant, so **the tenant boundary becomes the only
  thing limiting it**. **This is an operator policy, not a code-enforced rule** — corrected from an
  earlier draft that claimed tenant mode "must be refused for any external-partner token".
  `AccessToken` carries name / role / tenant / lifecycle / stations (`types/auth.py:148-159`) and
  nothing that authoritatively distinguishes an internal research consumer from an external partner,
  so no such refusal is implementable today, and asserting it would be a false guarantee. What the
  plan requires instead: `set-scope-mode … tenant` prints the consequence and demands an explicit
  `--yes-follow-the-whole-tenant` confirmation flag (T6), and the mode is displayed by `list` and
  `show` (T1/T2) so it is never invisible. **Adding an authoritative internal/external
  classification is explicitly NOT proposed here** — that is a token-taxonomy change, out of this
  plan's bounds.
- `access-tokens list` / `show` must display the mode prominently, or an operator reading
  `scope=37 station(s)` (`cli/access_tokens.py:187-193`) will not realise it is dynamic.

*Exit (revised — see the two notes below):*

1. a test proving a `'tenant'`-mode token picks up a station created **after** the token;
2. one proving a `'stations'`-mode token does **not**;
3. one proving a **cross-tenant** station never enters a **`'tenant'`-mode** token's scope — new
   coverage, because in that mode the loader's own `tenant_id` predicate is the *only* thing
   containing the set (`_assert_stations_in_tenant` is skipped there, D2.2);
4. one proving a station in the tenant that is **not** `network='bafu'` / `station_kind='river'`
   **does** enter a `'tenant'`-mode token's scope — the deliberate widening named in D2.2, locked so
   it cannot silently change shape;
5. one proving the DB **rejects `scope_mode='tenant'` on an admin row**
   (`ck_access_tokens_tenant_mode_is_consumer`).

Carried alongside, but **not** counted as T5 coverage: the cross-tenant assertion for
`'stations'` mode — a **regression re-assertion of pre-existing behaviour**, see round 2's note.

> **Correction (review round 1):** the previous fourth exit item — "an admin token still rejects any
> scope" — gated nothing about D2. It is already guaranteed by three pre-existing mechanisms this
> plan does not touch: the role/tenant DB CHECK (`db/metadata.py:1686-1690`),
> `_assert_stations_in_tenant`'s tenantless raise (`store/access_token_store.py:61-73`), and
> `Principal.station_in_scope`'s `if self.is_admin: return True` short-circuit
> (`api/security.py:138-139`). It would pass on day one whether or not the mode mechanism worked.
> Replaced with the constraint test above, which does exercise new code; admin rejection stays
> covered as a T1 regression check on `grant`.

> **Correction (review round 2):** this list previously bundled "cross-tenant stations never enter
> scope **in either mode**" into a single exit item. The **`'stations'`-mode half is not new
> coverage** and gates nothing about T5's loader branch. It is already enforced twice today — at
> write time (`_assert_stations_in_tenant` called from `create_token`,
> `store/access_token_store.py:34-35`) and again at read time (the identical re-validation block,
> `store/access_token_store.py:149-156`) — and is already locked green by an existing test,
> `tests/integration/store/test_access_token_store.py:76-93`
> (`test_cross_tenant_station_is_rejected`), which additionally shows a cross-tenant `'stations'`
> scope cannot even be *created* through the normal write path: `create_token` raises
> `CrossTenantScopeError` before a row exists to read back. It would pass on day one, exactly like
> the item struck in round 1 — the same anti-pattern, applied inconsistently. It is therefore split
> out and **labelled a regression re-assertion of pre-existing behaviour**; the implementer must not
> credit it as evidence the tenant-mode branch works. Only item 3 above (tenant mode) is new
> coverage.

## Tasks

Integration verification commands assume the repo's standard Postgres fixture
(`tests/integration/conftest.py`); the unit paths need nothing. `tests/integration/db/test_role_bootstrap.py`
additionally runs the real `docker/bootstrap-roles.sh` in a throwaway container (file `:44`).

### T4 — migration 0049 + `ScopeMode` + the type

**In scope:** `alembic/versions/0049_access_tokens_scope_mode.py` implementing D2.1 exactly (column,
both CHECKs, downgrade); a `ScopeMode` enum in `types/enums.py` (`STATIONS = "stations"`,
`TENANT = "tenant"`); the `scope_mode` field + `__post_init__` clause on `AccessToken`; the column
and both CHECKs in `db/metadata.py`'s `access_tokens` table; advancing `_RELEASE_B_HEAD` to `"0049"`
(`tests/unit/db/test_alembic_head_release_b.py:57`).
**Out of scope:** the loader branch (T5), every CLI change (T1/T2/T6), `access_token_stations`.
**Exit (red-first):** an upgrade test starting from a DB that already holds **one consumer and one
admin token row**, asserting both survive the migration with `scope_mode='stations'` and unchanged
behaviour; the DB rejects `scope_mode='tenant'` on an admin row; the DB rejects an unknown value;
`AccessToken(..., scope_mode=TENANT, role=ADMIN)` raises in `__post_init__`; a
downgrade-then-upgrade round trip leaves the two rows intact.
**Verification:** `uv run pytest tests/unit/db/test_alembic_head_release_b.py tests/integration/store/test_access_token_store.py -v`

### T5 — the token loader branch

**In scope:** D2.2 in `store/access_token_store.py` — `_row_to_token` parses `scope_mode` through
`ScopeMode(...)` (fail-closed) and, for `TENANT`, populates `station_ids` from
`SELECT id FROM stations WHERE tenant_id = <token tenant>`; `_assert_stations_in_tenant` stays on the
`'stations'` path.
**Out of scope:** `api/security.py`, every route, the CLI.
**Exit (red-first):** the five D2 exit tests — in particular "picks up a station created **after**
the token" (item 1) and "a non-`bafu` / non-`river` in-tenant station **is** in scope" (item 4) must
fail against the un-branched loader; plus the fail-closed parse test specified below. The
cross-tenant **`'stations'`**-mode assertion carries over only as a **labelled regression
re-assertion** — already green via `tests/integration/store/test_access_token_store.py:76-93` — and
is not evidence for anything in T5.
**Fail-closed parse test — construction specified (round 2), because none was:** an out-of-band
`scope_mode` **cannot be staged through the database**. The CHECK T4 itself installs
(`ck_access_tokens_scope_mode`) makes Postgres raise on the INSERT, long before `_row_to_token`
reaches its `ScopeMode(...)` parse, and no test in this suite drops a constraint to stage a corrupted
row (`grep -rn "DROP CONSTRAINT" tests/` returns nothing — there is no precedent to copy). So this
exit item is a **plain unit test that `ScopeMode("garbage")` raises `ValueError`** — the exact parse
`_row_to_token` performs (D2.1) — and **not** a DB round trip. Do not write a test whose shape
implies otherwise; the DB-level rejection of an unknown value is already covered by T4's exit list.
**Verification:** `uv run pytest tests/integration/store/test_access_token_store.py tests/unit/api/test_security.py -v`

### T6 — `access-tokens set-scope-mode <token-id> {stations|tenant}`

The verb round 1 found missing: without it, applying D2 to the very token that motivated this plan
still requires raw SQL against an access-control table — the anti-pattern this plan exists to remove
— and the claim that D1's 37 rows "are deleted when the map token switches mode" has no mechanism
behind it.

**In scope:** the new subcommand, targeting the token by **UUID** (consistent with `revoke`,
`cli/access_tokens.py:232-233`). One `engine.begin()` transaction performs all of: update
`scope_mode`; on `stations -> tenant`, `DELETE FROM access_token_stations WHERE token_id = …` (the
now-obsolete materialised rows, including D1's 37); and append one `audit_log` row
(`AuditEventType.API_KEY_SCOPE_CHANGED` — a new enum member; `audit_log.event_type` is free TEXT with
no value CHECK, `alembic/versions/0045_audit_log_table.py:42`, so no migration is needed) via
`PgAuditLogStore.append_entry`, per the Slice B atomicity rule documented at
`cli/access_tokens.py:16-17`. Switching **to** `tenant` requires an explicit
`--yes-follow-the-whole-tenant` flag and prints the station count the token will follow.
**Defined semantics for the reverse transition (`tenant -> stations`): fail-closed.** The token's
grant set is empty (this verb deleted it), so it immediately sees **nothing**; the command says so in
one line and the operator re-grants with T1. It does NOT snapshot the tenant back into grants — a
silent 37-row materialisation is precisely the drift this plan exists to remove.
**Out of scope:** a `--scope-mode` flag on `create` (a new tenant-mode token is `create` then
`set-scope-mode` — two commands, no extra surface); any mode change on an admin token, which
`ck_access_tokens_tenant_mode_is_consumer` forbids at the DB.
**Exit (red-first):** `stations -> tenant` sets the mode **and** deletes every grant row, in one
transaction; a failed audit insert rolls the whole thing back (mode unchanged, grant rows intact);
`tenant -> stations` leaves an empty scope and says so; the confirmation flag is mandatory; refusal
on an admin token; refusal on an unknown token id; exactly one `audit_log` row per successful change;
no output contains the token value or hash (asserted against full captured stdout); missing pepper
fails closed for this subcommand too (the pattern at `tests/unit/cli/test_access_tokens.py:39-56`).
**Verification:** `uv run pytest tests/unit/cli/test_access_tokens.py tests/integration/cli/test_access_tokens_cli.py -v`

### T1 — `access-tokens grant` / `access-tokens revoke-station`

**In scope:** the two missing station verbs in `cli/access_tokens.py`, targeting the token by
**UUID** and stations by **UUID** (matching `create --station`, `cli/access_tokens.py:208-214`; a
bare `stations.code` is not unique — the unique key is `(network, code)`,
`db/metadata.py:300`). Both reuse the existing `_assert_stations_in_tenant` invariant
(`store/access_token_store.py:61`) rather than re-implementing it. `grant` is idempotent
(`ON CONFLICT DO NOTHING`); `revoke-station` is idempotent too (deleting an absent row succeeds with
a "was not in scope" line). Both refuse on an admin token exactly as `create_token()` does
(`store/access_token_store.py:30-33`, "admin tokens cannot carry a station scope"). **Both refuse
explicitly on a `scope_mode='tenant'` token**, with a message pointing at `set-scope-mode` — without
that refusal `revoke-station` would report success while changing no effective authorization, the
most dangerous shape in this plan. Each verb appends one `API_KEY_SCOPE_CHANGED` audit row in the
same transaction as its write. `_print_token_row` (`cli/access_tokens.py:187-193`) gains the mode, so
`list` never shows a bare `scope=37 station(s)` for a dynamic token.
**Out of scope:** the mode column itself (T4), the loader (T5), name-based token lookup.
**Exit (red-first, expanded — round 1 showed the original two exit tests could all pass against a
no-op `revoke-station`):** an in-tenant `grant` makes the station **effectively in scope**, asserted
through a reloaded `AccessToken.station_ids` rather than the insert's own return; `revoke-station`
**removes** effective access, asserted the same way; a repeated `revoke-station` is harmless; a
repeat `grant` is a no-op; a cross-tenant `grant` is rejected with `CrossTenantScopeError`; a grant
naming an unknown station id is rejected; both verbs refuse an admin token; both verbs refuse a
`tenant`-mode token; CLI argument parsing and printed output are asserted; no output contains the
token value or hash; both verbs fail closed with no pepper.
**Verification:** `uv run pytest tests/unit/cli/test_access_tokens.py tests/integration/cli/test_access_tokens_cli.py -v`

### T2 — `access-tokens show <token-id>`

There is currently no way to see a token's scope without SQL. `list` prints `scope=N station(s)`
(`cli/access_tokens.py:192`) — a count, not the codes. An operator debugging "why can't the consumer
see station X" has no supported answer.

**In scope:** a read-only subcommand keyed by **token UUID**. (Round 1: `access_tokens.name` is
neither unique nor uniquely indexed — the only unique index on the table is `key_prefix`,
`db/metadata.py:1691` — so a name lookup could silently pick one of several rows.) It prints name,
role, tenant, status, expiry, `scope_mode`, and one line per in-scope station carrying **both** the
station UUID and its `network/code` (the actually-unique pair, `db/metadata.py:300`), so the output
round-trips straight back into `grant` / `revoke-station`. For a `tenant`-mode token it labels the
listing as derived at load time.
**Out of scope:** any mutation; name-based lookup.
**Exit (red-first):** prints station UUID + `network/code` for a token in each mode; never prints the
token value or its hash (asserted against the full captured stdout); rejects an unknown token id;
fails closed with no pepper.
**Verification:** `uv run pytest tests/unit/cli/test_access_tokens.py tests/integration/cli/test_access_tokens_cli.py -v`

### T7 — `sapphire_api` needs `DELETE` on `access_token_stations`

Round 1 blocker: the CLI runs inside the `api` container as `sapphire_api`
(`docs/standards/security.md:896-903`; `cli/access_tokens.py:6-14` documents the
`docker compose exec api /entrypoint.sh …` invocation), and that role holds
`GRANT INSERT ON access_token_stations` only (`docker/bootstrap-roles.sql:150`; line 149 is the
`access_tokens` grant — citation corrected in review round 2). Both
`revoke-station` (T1) and T6's mode-switch cleanup would fail with `InsufficientPrivilege` in
production while passing every test that connects as the owner role.

**In scope:** `docker/bootstrap-roles.sql:150` becomes
`GRANT INSERT, DELETE ON access_token_stations TO sapphire_api;`, plus the matching correction to the
role matrices in `docs/standards/security.md:896-903` and `docs/conventions.md:337`.
**Out of scope:** any change to `sapphire_worker`'s grants — it must still be unable to `SELECT`,
`INSERT`, `UPDATE` or `DELETE` on either auth table (`docs/conventions.md:338`;
`tests/integration/db/test_role_bootstrap.py:421-455`); any new grant on `access_tokens` itself
(`INSERT, UPDATE` at `docker/bootstrap-roles.sql:149` already covers the `scope_mode` write).
**Exit:** a new case in `tests/integration/db/test_role_bootstrap.py` — which executes the **real**
`docker/bootstrap-roles.sh` (file `:44`) — proving `sapphire_api` **can** `DELETE FROM
access_token_stations`, alongside a re-assertion that `sapphire_worker` still cannot `SELECT` or
mutate it. The widening is one verb on one table, not a hole.
**Verification:** `uv run pytest tests/integration/db/test_role_bootstrap.py -v`

### T8 — documentation (CLAUDE.md: "every code change updates affected docs — no exceptions")

**In scope:**

- `docs/standards/security.md:17,52` — consumer scope is resolved in one of two modes; the
  `access_token_stations` join is the `'stations'` mode, not the only carrier. The role matrix at
  `:896-903` gains the `DELETE` (with T7).
- `docs/spec/types-and-protocols.md:1237-1280` — the `AccessToken` block gains `scope_mode`, and the
  `station_ids` comment at `:1277` ("access_token_stations scope join (R2); consumer-only") stops
  claiming the join unconditionally: populated from the join in `'stations'` mode, derived from
  `stations.tenant_id` at load time in `'tenant'` mode.
- `src/sapphire_flow/types/auth.py:130-135` — the same correction in the `AccessToken` docstring,
  which today states flatly that `station_ids` "is the token's `access_token_stations` scope join".
- `docs/spec/database-schema.md:1025-1037` — the `access_tokens` ERD entity gains
  `TEXT scope_mode "stations | tenant"`.
- `docs/conventions.md:337` — `sapphire_api`'s row gains the `DELETE` grant and its reason.
- `docs/standards/cicd.md` § access-token runbook — the four new/changed verbs, and the note that a
  `'tenant'`-mode token needs nothing re-granted after a pepper-rotation revoke+create beyond
  re-setting its mode (`docs/standards/security.md:321` describes that rotation as
  "re-materializing each key's station scope", which is no longer the only case).
- `docs/touchpoint-maps.md:567` — "a `consumer` token is filtered to its `access_token_stations`
  scope" gains the second mode.
- `src/sapphire_flow/cli/access_tokens.py:2-18` module docstring — the command list, and the fact
  that in-place scope edit is no longer "deferred to v1.x".

**Out of scope:** `architecture-context.md`'s v1 auth vision; anything about *who* may mint tokens.
**Exit:** no doc still states that `access_token_stations` is the only scope carrier.
**Verification:** `uv run pre-commit run --all-files`, plus a
`grep -rn "access_token_stations" docs/ src/` read-through recorded in the task report.

### T9 — deployment cutover (operator step, not code)

**In scope:** on the mini, once the release carrying T4–T8 is deployed and `alembic upgrade head` has
applied 0049: run `docker compose exec api /entrypoint.sh python -m sapphire_flow.cli.access_tokens
set-scope-mode <map-token-uuid> tenant --yes-follow-the-whole-tenant`, then re-run the D1
verification (Forecast Lab still serves exactly 2009/2011/2091; `GET /api/v1/stations` still returns
37) and confirm `access_token_stations` holds no rows for that token. **Also record, once, the
tenant's `network` × `station_kind` breakdown** (`SELECT network, station_kind, count(*) FROM
stations WHERE tenant_id = <tenant> GROUP BY 1,2`) — D1 measured the set only by *status*, and D2.2's
unfiltered tenant-mode query makes the kind/network composition the standing cost to know.
**Out of scope:** any other token; any station promotion.
**Exit:** D1's two measured numbers reproduce **after** the switch, recorded in this plan under D1,
plus the tenant's `network` × `station_kind` breakdown recorded there for the first time.
**Verification:** manual and recorded here — this step runs against production and has no `uv run`
form. It is listed as a task because leaving it implicit is how the 2011 outage happened.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["T4", "T7"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["T5"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["T6", "T1", "T2"],
      "parallel": false,
      "depends_on": ["phase-2"]
    },
    {
      "id": "phase-4",
      "tasks": ["T8"],
      "parallel": false,
      "depends_on": ["phase-3"]
    },
    {
      "id": "phase-5",
      "tasks": ["T9"],
      "parallel": false,
      "depends_on": ["phase-4"]
    }
  ]
}
```

Phase 3 is **not** parallel: T6, T1 and T2 all edit `cli/access_tokens.py`'s subparser block
(`:196-235`) and its output helpers, so concurrent subagents would collide there; run them in the
listed order. T7 touches a different file with a different gate and runs alongside T4.

## Acceptance criteria

1. Widening **or narrowing** a live token's scope needs no raw SQL and no reissue — including
   putting a token into tenant mode (T6), the transition D1's cleanup depends on.
2. The tenant-containment invariant is enforced by the same code path as `create_token()`
   (`_assert_stations_in_tenant`), not a second copy.
3. An operator can read a token's scope as station UUIDs **and** `network/code` pairs, with the
   token's scope mode shown by both `list` and `show`.
4. No command prints a token value or hash — asserted against full captured stdout, per verb.
5. The map token's scope matches D1, recorded in the plan, and the T9 cutover reproduces D1's two
   measured numbers.
6. A new BAFU station in the Swiss research tenant is visible to the map token with **no operator
   action** — the T3 acceptance test, and the one that matters operationally. Per D2.2 this is
   tenant-membership and nothing narrower: **any** station added to that tenant, of any `network` or
   `station_kind`, enters a tenant-mode token's scope. That is the accepted shape, asserted by a T5
   test rather than left to the reader.
7. Every new verb fails closed without a readable pepper, exactly as the existing four do
   (`tests/unit/cli/test_access_tokens.py:39-56`).
8. `sapphire_worker` still cannot read or mutate either auth table after T7's grant widening.

## Not in scope

The roles and the route-level authorization call sites — `Principal.station_in_scope`
(`api/security.py:134-142`) and every caller are untouched. Token rotation and expiry. The
eligibility filter. Anything about *who* may mint tokens. Any internal/external classification of
token holders. The `access_token_stations` **schema** — it is already correct (composite PK, both
FKs); only the `sapphire_api` grant on it changes (T7).
