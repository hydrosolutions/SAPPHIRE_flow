---
status: DRAFT
created: 2026-09-29
revised: 2026-09-29   # round 4 folded in (Claude and Codex, all NEEDS CHANGES, narrow); Plan 268 T8 already done by the one-off route
plan: 510
title: Three levels of database identity — tenants created at deploy time, a narrow database-limited operator role for delivery replacement, no routine job on the owner superuser
scope: Stop routine operator jobs from needing the owner (superuser) credential. Tenant creation moves to deploy time (Plan 513); a least-privilege operator role, row-limited by the database itself, covers the rare delivery-replacement job; schema changes stay with the owner at deploy. Needed for v1.
risk: high   # secrets, database roles, a migration/trigger, Docker wiring (docs/workflow.md § High-risk work)
depends_on: [147, 268, 513]   # 268's code is implemented through T7; its operator run (T8) was done on 2026-09-29 by the owner-approved one-off route
blocks: []
related: [341, 401, 306, 307, 512]
open_decisions: []
closed_decisions: [D1, D2, D3, D4, D5, D6]   # owner, 2026-09-29; D5 revised: tenants come from Plan 513, done first — no stopgap seed migration
source: 2026-09-29 — Plan 268 T8 (the CHWRR delivery import on the Mac-mini) could not run with the worker role and stopped at the owner credential.
---

# Plan 510 — three levels of database identity

## Status

**DRAFT, HIGH RISK.** Four rounds of independent review (Claude and Codex) have returned NEEDS CHANGES; this
revision folds in round 4. It needs one owner-commissioned review before READY and
another before the implementation PR (`docs/workflow.md` § High-risk work). The orchestrator sets READY.

## Why

Plan 268 T8 creates the `chwrr` tenant, registers six stations and replaces a tagged delivery. Grants
measured on the staging database with `has_table_privilege` on 2026-09-29 (reported by the orchestrator
session; the repo's `docker/bootstrap-roles.sql` supports the same matrix but cannot show the live state):

| role | insert `tenants` | insert `stations` | delete `observations` / `rating_curves` |
|---|---|---|---|
| `sapphire_worker` (scheduled flows) | no | yes | no |
| `sapphire_api` | no | no | no |
| `sapphire` (owner, superuser) | yes | yes | yes |

Reading the import code shows the gap is wider than tenant creation and delete:

- **Tenant row lock.** `replace` and `qc` call `lock_tenant`, a `SELECT … FOR UPDATE` on `tenants`
  (`store/tenant_store.py`; `cli/import_dhm_delivery.py:432,600`). PostgreSQL requires UPDATE on at least
  one column for `FOR UPDATE` and for `FOR SHARE` (PostgreSQL documentation; T1 proves it by test). No role
  but the owner can write `tenants`, and table-wide UPDATE would let a role rename any tenant.
- **Upsert and QC.** Observations are written with `INSERT … ON CONFLICT DO UPDATE`; `qc` updates
  observations.
- **Deleting is bounded only by Python.** The "this delivery only" rule is a `WHERE delivery_id = … AND
  station_id IN (…)` in the stores. A role holding `DELETE` on `observations` can delete all Swiss data by
  direct SQL; `WritePrincipal` does not run for direct SQL.
- **No audit row is written today.** The three isolation checks pass `audit_log_store=None`, and that
  function writes only on a rejection. A destructive replacement currently leaves only a log line.

## The split (owner, 2026-09-29)

1. **Runtime roles** (`sapphire_api`, `sapphire_worker`) never need any of this. Unchanged.
2. **Tenants are provisioning, done at deploy time** under the owner, never by an operator job.
3. **A narrow operator role for delivery replacement**, limited by the database and audited.
4. **Schema changes (migrations) stay with the owner** at deploy time.

## Decisions (all closed by the owner, 2026-09-29)

- **D1** — tenants at deploy time; a narrow operator role.
- **D4** — audit on success is required (T2).
- **D2 — row limit: a guard trigger (option a).** Row-level security is unavailable (`bootstrap-roles.sql`
  refuses any table with RLS). The guard, as reviewed and required:
  - a `SECURITY INVOKER` row-level trigger on `observations` and `rating_curves` for INSERT, UPDATE and
    DELETE, with `WHEN (session_user = 'sapphire_operator')` so it costs other roles nothing (measure ingest
    overhead in T4). `session_user`, not `current_user`: a `SECURITY DEFINER` function owned by the owner
    runs with `current_user` = owner and would bypass a `current_user` check;
  - **one authoritative predicate:** a row is permitted only if `delivery_id` equals the import's delivery id
    constant (`DELIVERY_ID`, `types/dhm_delivery.py`) **and** its station belongs to the delivery tenant
    (`chwrr`, looked up by code). This reproduces the Python "this delivery only" rule; a second delivery later
    is a deliberate migration that extends the allow-list. It checks `NEW` on INSERT, `OLD` on DELETE, and
    **both** on UPDATE; an UPDATE may not change `station_id`, `delivery_id` (clearing the tag would launder a
    row out of the guard) or a rating curve's validity dates;
  - **written as a positive allow, hardened:** `IF NOT EXISTS (…) THEN RAISE` (a missing `chwrr` row must
    refuse, not pass as NULL — a deployment that does not declare `chwrr` has no such tenant); relations schema-qualified
    (`public.stations`, `public.tenants`) with the function's `search_path` pinned to `pg_catalog, public,
    pg_temp` (`pg_temp` last). `TEMP` is granted to `PUBLIC` by default and revoking it from the operator alone
    would not remove it (revoking from `PUBLIC` would change other roles), so the plan **does not** claim to
    prevent temporary tables; T4 tests that a temporary table named `stations` cannot influence the guard;
  - a matching `BEFORE INSERT` check on `stations` (the operator may create stations only in the delivery
    tenant). **Invariant, converged by the bootstrap and tested: the operator holds no `UPDATE`, `DELETE` or
    `TRUNCATE` on `stations`** — the predicate depends on `stations.tenant_id`, so re-tenanting a Swiss
    station would make every Swiss row deletable. A `BEFORE TRUNCATE` statement trigger on the guarded tables,
    as migration `0046` does;
  - adding another delivery tenant is a deliberate migration, not a config change;
  - **the bootstrap converges to the safe state:** if any expected guard trigger is missing **or not enabled**
    (`pg_trigger.tgenabled` must be `'O'` or `'A'`; `ALTER TABLE … DISABLE TRIGGER` leaves the row present with
    `'D'`) it grants the operator no DML, **revokes any it holds**, logs a warning, and does not abort `init`
    (an aborted `init` would leave the whole stack down). The expected set is the three row triggers on each of
    `observations` and `rating_curves`, the `stations` INSERT trigger and the TRUNCATE triggers. **The guard migration's downgrade itself revokes the operator's DML before dropping
    the triggers**, so the migration-only path leaves no unguarded interval. Both paths are tested;
  - the operator's block in `bootstrap-roles.sql` revokes all role memberships (no `SET ROLE` route), and
    the plan states the preconditions the other bypasses rest on: `DISABLE TRIGGER` needs table ownership,
    `session_replication_role` needs superuser or an explicit grant, `TRUNCATE` needs the grant;
  - enumerate any `SECURITY DEFINER` or PUBLIC-executable function that writes these tables (T4);
  - **other grants, stated:** `audit_log` `INSERT` only (no `SELECT`/`UPDATE`/`DELETE`; the append-only trigger of
    migration `0046` applies); no access to `observation_versions` (T1 asserts the import writes none); no
    `UPDATE`/`DELETE` on `stations` or `tenants`. Audit rows are written as the system actor and so carry no
    operator identity — attribution is deferred to v1.x;
  - the operator's `SELECT` reaches every tenant's rows unless T1 shows it can be narrowed; state it.
    **Residual, stated:** the guard limits integrity, not availability. A role holding `DELETE`/`UPDATE` can
    still `LOCK TABLE` or `SELECT … FOR UPDATE` and block ingest, and advisory locks are open to any role.
  - *Considered and set aside:* operator with no table access and only `EXECUTE` on `SECURITY DEFINER`
    replacement functions. It would hold the row limit by construction and take the lock and the audit inside
    the function, but costs a bulk-insert path and a larger migration. Revisit if the trigger proves brittle.
- **D3 — tenant lock: an advisory lock (option b).** `lock_tenant` becomes a plain `SELECT` (keeping the
  missing-tenant `ValueError`) plus `pg_advisory_xact_lock` on a namespaced two-integer key from the tenant
  id (another store already uses a single-key advisory lock). **This changes `lock_tenant` for every caller
  and every role**, not only the operator: `replace` and `qc` are its only two callers, and all of them must
  share one key mapping, or an owner-run `replace` and an operator `qc` would not exclude each other. Advisory
  locks coordinate cooperating callers only. Note for mixed-version deployments: an older image still using
  `FOR UPDATE` does not exclude a newer one. (`FOR SHARE` was dropped: it needs the same UPDATE privilege.)
- **D5 — the tenant vehicle: Plan 513 (declared tenants), done first (owner, 2026-09-29).** An earlier draft
  used a seed migration as a stopgap; the owner chose the order 513 → 510 → T8 instead, so no seed migration
  exists and no Swiss-only deployment gets an empty `chwrr` (the Mac-mini hosts both tenants on purpose, see Plan 513). This plan assumes a tenant that Plan 513 has already
  created; an existing tenant is preserved by 513's own contract.
- **D6 — credential and command (recommended path, accepted for now; reworked in rounds 3 and 4).** Compose
  secrets are `file:` entries and a missing file stops the service, so the optional secret cannot live in the
  base file (a mount in `init` would take down every existing deployment). Follow the repo's own precedent for an
  optional secret — the Nepal-only gateway key is provided by an overlay, not the base file
  (`docker-compose.yml:534-538`): a new **`docker-compose.operator.yml` overlay** declares the secret
  `./secrets/sapphire_operator_db_password`, adds it and `SAPPHIRE_OPERATOR_DB_PASSWORD_FILE` to `init`, and
  defines the `operator` service. The base compose is unchanged, so the existing exact secret-consumer sets
  (`tests/unit/deploy/test_compose_db_roles.py`, `test_compose_backup_pool.py`) still hold.
  `sapphire_operator` is always created **without login**. `bootstrap-roles.sh` treats an unset variable as
  "leave it without login" and the SQL has an explicit no-secret branch (an unset psql variable is otherwise
  a syntax error). **Activation** = the owner creates the secret file on the host and deploys with the
  overlay; **rotation** = replace the file and re-run `init`.
  **Revocation has two parts.** *Deploy without the overlay* makes the bootstrap set `NOLOGIN` again, which
  prevents **new** connections only; a session already open keeps working. So the owner also has a documented,
  `init`-independent procedure (`ALTER ROLE sapphire_operator NOLOGIN` then `pg_terminate_backend` for its
  sessions, with a verification query), because a failing tenant declaration (Plan 513) can stop `init` before
  the role bootstrap runs. This differs deliberately from `sapphire_publication_health`, whose login persists.
  **The `operator` service needs, and T4 must supply and test:** the base config and the checkout-shaped
  inputs the import reads relative to the config path (`config/overlays/chwrr-import.toml` — checked for exact
  contents — and `tests/fixtures/dhm/stations.toml`; the image does not ship `tests/` or `docs/`, see
  `.dockerignore`); a config carrying the QC rules `qc` needs; the restricted delivery directory mounted
  **outside** the checkout, read-only (the import refuses one inside it); `SAPPHIRE_CONFIG`,
  `SAPPHIRE_CONFIG_OVERLAY`, `DATABASE_URL_TEMPLATE` for `sapphire_operator` and the secret path. Commands run
  as `docker compose -f … -f docker-compose.operator.yml run --rm operator …`; base compose exposes no
  Postgres port, so this needs host access. Per-person identity is deferred to v1.x.

## Tasks

### T1 — Measure what each operator job needs (diagnostic matrix)
- **Outcome:** a *diagnostic* grant matrix in this plan, produced by running the three commands
  against a real Postgres under a scratch role that starts with no grants; proves the lock/`FOR SHARE`
  privilege claim; shows whether the operator needs `UPDATE` on `rating_curves` (expected: no) and whether
  the token CLI and fine-tuning jobs are already covered by existing roles. The **final** grants are set by
  T4 and revalidated in T6 (T2's audit writes will change the matrix).
- **In:** an integration test harness; this plan. **Out:** any production grant.
- **Verification:** the harness test passes with exactly the matrix; removing any grant makes a named step
  fail with a permission error.
- **Pre-change:** RED — under the empty role each of `stations`, `replace`, `qc` fails at its first missing
  privilege, each at a named statement.

### T2 — Audit on success for the import commands
- **Outcome:** each of `stations`, `replace`, `qc` writes one `audit_log` row in the same transaction as its
  mutation (tenant, delivery id, aggregate counts, no restricted values); a failed audit rolls the mutation
  back; a dry run writes none. (`audit_log.event_type` has no CHECK constraint, so a new enum member needs no
  migration.)
- **In:** `cli/import_dhm_delivery.py`, an `AuditEventType` member, tests. **Out:** other jobs.
- **Verification:** `tests/unit/cli/test_import_dhm_delivery.py` and `tests/integration/cli/test_import_dhm_delivery.py`
  gain tests for success, audit failure rollback, and dry run.
- **Pre-change:** RED — today a successful replacement leaves no `audit_log` row.

### T3 — Confirm the tenant contract from Plan 513
- **Outcome:** the import's three commands work against a tenant created by Plan 513's deploy step (`chwrr`,
  random or fixed id, looked up by code). The guard's absent-tenant refusal is verified in T4, where the guard
  is built.
- **In:** an integration test using a 513-created `chwrr`. **Out:** creating tenants (Plan 513 owns it) and the guard.
- **Verification:** the integration test.
- **Pre-change:** N/A (a contract check on 513's output; the RED belongs to Plan 513 T2).

### T4 — The operator role, its guard, and the lock (D2, D3, D6)
- **Outcome:** `sapphire_operator` exists with only the grants T1 and T2 require; the guard (D2) blocks every
  write outside the delivery tenant; `lock_tenant` uses the shared advisory key for all callers (D3); the
  bootstrap wrapper, secret handling and the `operator` overlay and service exist (D6). Re-running the
  bootstrap converges after a stale broad grant, and to the safe state when the guard is missing.
- **In:** `docker/bootstrap-roles.sql` (its own convergence block, modelled on `sapphire_publication_health`),
  `docker/bootstrap-roles.sh`, `docker-compose.operator.yml` (new), the guard migration (its downgrade revokes
  first), `store/tenant_store.py`, `tests/unit/db/test_alembic_head_release_b.py`, the import
  config inputs (`tests/fixtures/dhm/stations.toml` and the CHWRR overlay must be readable by the operator
  service — the code reads them relative to the config path). **Out:** any change to `sapphire_api` or
  `sapphire_worker` grants.
- **Verification:** extend `tests/integration/db/test_role_bootstrap.py` and `tests/unit/deploy/test_compose_db_roles.py`
  (and its `init` ordering test); operator attempts that must all fail: delete or update a Swiss row, another
  delivery's row, a station outside the delivery; INSERT (including bulk `COPY` and upsert) of a Swiss-station
  row or a rating curve; clearing `delivery_id`; moving `station_id`; a row tagged with a *different*
  delivery id at a CHWRR station; `UPDATE`/`DELETE`/re-tenanting `stations`; a `CREATE TEMP TABLE stations`
  shadow; the guard downgrade with an active operator; the bootstrap with
  the guard missing; `SET ROLE`; `session_replication_role`; `DISABLE TRIGGER`; `TRUNCATE`; inserting or
  updating `tenants`; **the guard refusing when `chwrr` is absent** (moved here from T3); **the guard against
  the real Mac-mini state — rows the owner wrote earlier with the delivery tag, the random tenant id and the
  existing station rows — including the first operator `replace` deleting and re-inserting them**; a guard
  trigger present but **disabled** (the bootstrap must revoke); a `CREATE TEMP TABLE stations` shadow not
  influencing the guard. Absent-overlay startup: the base compose still starts and the operator has no login.
  Extend the concurrency test at
  `tests/integration/cli/test_import_dhm_delivery.py:368` to operator and mixed-role connections.
- **Pre-change:** RED — with plain table `DELETE`/`INSERT`/`UPDATE` granted and no guard, the row-scope attempts
  (Swiss rows, other delivery, tag clearing, station move, temp shadow) succeed. The privilege-escalation
  attempts (`SET ROLE`, `DISABLE TRIGGER`, `TRUNCATE`, tenant writes) are refused even without the guard and
  are regression tests, not RED.

### T5 — Retire `bootstrap-tenant` from the operator path
- **Outcome:** no operator command creates tenants; the import-specific `_require_admin_bootstrap_identity`
  and the `bootstrap-tenant` subcommand are removed (`global_admin` itself is a general deployment identity
  used by unscoped hosts in `deployment_identity.py` and `write_principal.py`, and stays); the "tenant must be
  bootstrapped first" error says the tenant is created at deploy time.
- **In:** `cli/import_dhm_delivery.py`; every test that references `bootstrap-tenant`/`bootstrap_tenant`
  (`grep -rn` lists them; today `tests/unit/cli/test_import_dhm_delivery.py`,
  `tests/unit/cli/test_import_dhm_replacement.py`, `tests/integration/cli/test_import_dhm_delivery.py`);
  `docs/runbooks/chwrr-dhm-history-import.md` step 1. **Out:** the tenant store, and **Plan 268's text** — the
  single edit to that READY plan is owned by T7 and applied only after its own independent review.
- **Verification:** the import suite passes with tenants created by Plan 513's deploy step; **a test asserts the retired command is
  unavailable**.
- **Pre-change:** RED — the command exists today.
- **Ordering:** T5 runs after T4 (both edit `tests/integration/cli/test_import_dhm_delivery.py`).
- **Plan 268 T8 status (2026-09-29), reported by the orchestrator session and not verifiable from the repo:**
  it was executed by the owner-approved one-off route with the owner credential — the `chwrr` tenant, six
  stations, 112 curves, 99,246 observations and delivery QC (all `qc_passed`) are in place (aggregate record on
  the Mac-mini). That tenant has a random id and was created by `bootstrap-tenant`, exactly the "existing
  tenant" case Plan 513 must preserve. Plan 268's own text still describes T8 as pending; reconciling it is
  T7's edit. This plan's T6 below therefore verifies *future* replacements and corrections run as the operator
  role, not the first import.

### T6 — End-to-end under the real operator login
- **Outcome:** all three commands, with their audit writes and the shared lock, run successfully as
  `sapphire_operator` through the D6 path on a real Postgres, and `replace` (its `DELETE`) is refused as
  `sapphire_worker`. The worker keeps the grants it has today (station insert, observation update, audit
  insert), so `stations` and `qc` are not expected to fail as the worker.
- **In:** an integration test and a documented operator command. **Out:** production.
- **Verification:** the integration test; a fresh-install and an existing-database bootstrap check.
- **Pre-change:** RED — no operator login can run them today.

### T7 — Documentation, in the same change
- **Outcome:** `docs/standards/security.md`, `docs/standards/cicd.md` (`init`, role bootstrap, rotation and
  revocation, rollback), `docs/conventions.md` (grant matrix), the import runbook and
  `docs/touchpoint-maps.md` say what the code does. **T7 also owns the one edit to Plan 268** (T8 done by the
  bridge; tenants declared per Plan 513; the operator role per this plan). Plan 268 is `READY`, so that edit is
  applied only after its own independent review; Plan 513 T3 only proposes it. Plan 512 moves the runbook; edit it at its then-current path.
- **In:** those files. **Out:** code. **Pre-change:** N/A (documentation only).
- **Verification:** grep for `bootstrap-tenant` and "documented residual" finds no stale statement.

## Rollback

- **Operator login:** deploy without the operator overlay (bootstrap sets the role back to no-login).
- **Removing the role:** a documented owner step (`DROP OWNED` then `DROP ROLE`) run **before** switching to an
  older image; an older `init` does not know the role and will not touch it. The guard triggers stay unless the
  guard migration is downgraded; that downgrade revokes the operator's DML first, and a bootstrap that finds the
  guard missing grants none and revokes any it finds.
- **Emergency revoke:** the `init`-independent owner procedure in D6 (`NOLOGIN` plus terminating open sessions).
- **The lock change is not undone** by removing the role: `lock_tenant` uses the advisory key for every caller
  from the day T4 lands.
- **A mistaken tenant:** never deleted automatically (Plan 513); removal is an owner action.
- **The import itself** is transactional and has `--dry-run`.

## Exit gates

`uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run pyright src/`, the tests named
per task, the real-Postgres role and concurrency integration tests, and a full `uv run pytest` before merge.
Affected docs updated in the same change.

## Not in scope

Per-person identity and audit of *who* ran an operator job (named-human sessions exist for publication, Plan 341;
operator-job identity is deferred to v1.x in `security.md`); a UI for triggering jobs; runbook layout (Plan 512);
creating tenants (Plan 513, a prerequisite). This plan reduces use of the owner credential for operator jobs; the
separate Prefect owner-credential residual in `security.md` is not closed by it.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2", "T3"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5"], "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T6"], "depends_on": ["phase-4"] },
    { "id": "phase-6", "tasks": ["T7"], "depends_on": ["phase-5"] }
  ]
}
```
