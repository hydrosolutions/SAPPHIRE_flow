---
status: READY
created: 2026-09-29
revised: 2026-09-29   # rounds 1–3 folded in (Claude and Codex, all NEEDS CHANGES, narrowing); T8 already done by the one-off route
plan: 513
title: Declared tenants — a host's config lists its tenants and the deploy creates them
scope: A per-host declaration of tenants that a new `init` step creates idempotently and atomically, so no tenant needs a migration and no host carries a tenant it does not host. Done first: Plan 510 and Plan 268 T8 build on it.
risk: high   # a new step in every deployment's `init`, configuration schema, compose wiring (retired workflow § High-risk work)
depends_on: [147]
blocks: [510]
related: [268, 269, 512]
open_decisions: []
closed_decisions: [D1, D2, D3]   # recommended answers below; owner to confirm at the owner-commissioned review
source: 2026-09-29 — owner: "what we actually need is option b" (declared tenants), and the order is 513, then 510, then Plan 268 T8; no stopgap seed migration.
---

# Plan 513 — declared tenants

## Status

**READY — HIGH RISK.** Set 2026-09-29 by the orchestrator on the owner's instruction, after independent review by
Claude and Codex: repeated rounds, then a final gate at commit `7f8b5c39` in which all four reviewers (two per plan)
recommended READY with no blockers. The high-risk rule's owner-commissioned review is taken as satisfied by the
owner's instruction. The notes below came from that final gate and do not change any decision.

## Why

Tenants are provisioning, done at deploy time under the owner (Plan 510's split). Migration `0041` is the only
migration seed (`sapphire`, fixed id); the `bootstrap-tenant` command of Plan 268 is the only other way to create
one, and it needs the owner database credential at run time. Seeding `chwrr` by migration would put an empty
tenant in every database permanently and require a migration per future tenant. Each host should declare the
tenants it hosts instead.

## Facts from the repo (2026-09-29 — this replaces the measurement task)

- **`init` loads no configuration today.** The base `init` service (`docker-compose.yml:399-460`) sets neither
  `SAPPHIRE_CONFIG` nor `SAPPHIRE_CONFIG_OVERLAY` and mounts only `./config.toml`. `register_deployments` reads
  no TOML; `alembic` and `bootstrap-roles.sh` read only database and password inputs; `load_config()` without a
  path fails when `SAPPHIRE_CONFIG` is unset.
- **Only `docker-compose.staging.yml` passes an overlay to `init`** (`staging-5-stations.toml`). **`docker-compose.macmini.yml`
  has no `init` block**: it wires `mac-mini.toml` into the two workers and the API only.
- **Where `chwrr` lives.** Plan 268 D11 and T8 put it in the existing **Mac-mini staging database**, the shared stack
  that also hosts the Swiss `sapphire` tenant. There is no other host: `docker-compose.nepal-forcing.yml` is a
  forcing-only Postgres with no `init`, API or workers. So on the Mac-mini both tenants are *intended*; the
  "no unwanted tenant" benefit applies to Swiss-only deployments, which never declare `chwrr`.
- **`config/overlays/chwrr-import.toml` must not change.** The import checks its contents against exactly
  `{"deployment": {"writable_tenants": ["chwrr"]}}` (`cli/import_dhm_delivery.py:178-185`); a declaration placed
  there would make every import fail.
- **Existing behaviour to keep.** `bootstrap_tenant` (`cli/import_dhm_delivery.py:199-224`) keeps an existing
  tenant's id, generates `uuid4()` only on first creation, and fails if the same code has a different display
  name; identity is `chwrr` / `CHWRR Nepal`. `PgTenantStore.store_tenant` is a plain insert, and `tenants.code` is
  unique but names are not.
- **Config merge.** The overlay merge is per key and replaces arrays wholesale (`config/_overlay.py`), so a list
  of tenants split across base and overlay would not append.
- **`init` is a `&&` chain** (`alembic upgrade head && bootstrap-roles.sh && register_deployments && echo`), with
  `restart: "no"`; every worker and the API wait on `init` completing successfully. A failed step aborts the
  chain and undoes nothing before it. Upgrades already require the workers to be stopped (`cicd.md`).

## Reported live state (orchestrator-reported on 2026-09-29; **not verifiable from the repo**)

`chwrr` already exists on the Mac-mini: Plan 268 T8 ran by the owner-approved one-off route, the tenant was created
by `bootstrap-tenant` with a random id and the name `CHWRR Nepal`, with six stations and the delivery. Adding the
declaration later must keep that row and id, so T2's "existing tenant created by the old command" case is real.
**Owner pre-deploy check:** read that `tenants` row (code exactly `chwrr`, name exactly `CHWRR Nepal`, no trailing
whitespace) before the first `init` that carries the declaration — under D2 a mismatch stops `init`, and on the
Mac-mini that also blocks the Swiss stack.

## Design (recommended answers; owner confirms at the owner-commissioned review)

- **D1 — where the declaration lives: a table keyed by tenant code in the host's overlay** (`mac-mini.toml` for
  the Mac-mini), e.g. `[tenants.chwrr]` with `name = "CHWRR Nepal"`. A table merges per key across base and overlay
  (no wholesale array replacement), duplicates are impossible, and a typo cannot become a write authority because
  it is a different setting from `writable_tenants`. Declaring never widens or narrows `writable_tenants`.
- **D2 — conflicts: fail.** The same code with a different name aborts the step, naming the code and both names;
  a rename is an owner action (SQL, documented), not automatic. `sapphire` may be declared only with its seeded
  name (`SAPPHIRE (Swiss v0)`); anything else fails.
- **D3 — ids: never in the declaration.** An existing tenant keeps its id; a new one gets `uuid4()`, as today.
  Only migration `0041`'s `sapphire` id is fixed. A fixed id in config would collide with a row created earlier.
- **Validation** at the config boundary (Pydantic), then a frozen `DeclaredTenant` dataclass: a code pattern
  (lowercase letters, digits, `_` and `-`, starting with a letter, bounded length) and a non-blank, bounded name.
- **The step.** A dedicated command, run by `init` after `alembic upgrade head` and before `bootstrap-roles.sh`
  (new rows need no grant work) and before `register_deployments`. **Reading the config:** it parses the base
  config and the resolved overlays with plain `tomllib` and merges **only their `tenants` tables** — it does not
  call `load_config()` and does not expand `${…}` environment placeholders (`load_merged_toml` expands them across
  the whole file and raises for an unset variable, which would let an unrelated runtime setting break `init`).
  It creates all missing declared tenants **in one transaction**, **in sorted code order** (two concurrent runs
  cannot deadlock on opposite orders), with `INSERT … ON CONFLICT (code) DO NOTHING` followed by the name check as
  a separate statement (Read Committed lets it see a concurrent winner). **Behaviour, one rule each:** an unset
  `SAPPHIRE_CONFIG` is a **hard error** (T2 wires it on every stack that runs `init`; a silent no-op would let a
  mis-wired Mac-mini deploy succeed with no `chwrr`); a loaded config with no `[tenants]` table is a **no-op**, so
  existing deployments are unaffected; a non-table `tenants` value is rejected; a misspelled section name cannot
  be detected (no closed schema), so the step **logs how many tenants were declared on every run**. `created_at`
  comes from the database default; the id from `uuid4()` behind an injectable factory. It is a hard failure:
  a conflict stops `init`, and the message names the tenant. **Consequence for the owner: on the Mac-mini `init`
  gates the whole stack, so a bad declaration also blocks the Swiss workers, API and deployment registration, and
  (being ahead of the role bootstrap) skips role bootstrap on that deploy — deliberate, because a half-provisioned
  host is worse.** It is safe to run with services up (additive), but a full `init` still follows the upgrade
  procedure that stops the workers first.
- **Removing a declaration deletes nothing.** A mistaken tenant is removed by the owner with SQL, and only while
  it owns no rows.

## Tasks

### T1 — The declaration schema and parser
- **Outcome:** the `[tenants.<code>]` table is parsed and validated into `DeclaredTenant` objects; invalid codes,
  blank or over-long names, a mismatched `sapphire` name and a non-table `tenants` value are rejected with clear errors.
- **In:** a Pydantic model at the config boundary, the dataclass, `docs/spec/config-reference.toml`.
  **Out:** creating tenants; `writable_tenants`.
- **Verification:** `tests/unit/config/` tests for valid, invalid, merged-overlay and empty declarations.
- **Pre-change:** RED — no parser exists; today a `[tenants]` table is silently ignored, and the test fails because the declared tenants are not returned.

### T2 — The provisioning step and `init` wiring
- **Outcome:** a fresh database and an existing one gain the declared tenants; an existing matching tenant is
  kept with its id; a name conflict aborts and leaves the batch uncommitted; concurrent runs are safe; a
  declaration-less host does nothing. **Every stack that runs `init` gets the config path**: `SAPPHIRE_CONFIG:
  /app/config.toml` on the base `init` (its mount already exists), which staging inherits; the Mac-mini gains an
  `init:` block passing its overlay (matching the staging one). Without this the step would fail on every stack
  after the migrations, even with nothing declared.
- **In:** the new command; a new store method `ensure_tenant` (insert-or-skip plus the name check) on the
  `TenantStore` Protocol, its Postgres implementation and the fake, with `docs/spec/types-and-protocols.md`
  updated; `DeclaredTenant` in `types/tenant.py`; `docker-compose.yml` (`init` command chain and
  `SAPPHIRE_CONFIG`), `docker-compose.macmini.yml`, `docker-compose.staging.yml`. **Out:** the `bootstrap-tenant`
  code (Plan 510 T5).
- **Verification:** `tests/integration/db/` tests for a fresh database, an existing `chwrr` created by the old
  command (random id, matching name), **a two-declaration batch whose second entry conflicts (the first must roll
  back)**, an existing tenant holding stations, a rerun, and two concurrent runs presenting overlapping batches
  in opposite orders; a run with no declarations succeeds through the real config-reading path **even when the
  config contains a runtime-only `${SAPPHIRE_FOO}` placeholder unset in `init`**; an unset `SAPPHIRE_CONFIG`
  fails loudly; `tests/unit/deploy/` extends the compose tests: the base file, the base merged with the staging
  overlay and the base merged with the Mac-mini overlay (a small YAML merge of the map-form `environment` and
  `volumes`, in the style of the existing tests, or `docker compose config` where the binary is available) each
  pass the config path and, where declared, the overlay to `init`, and `init`'s command order is pinned.
- **Pre-change:** RED — the compose test fails today because `init` has no `SAPPHIRE_CONFIG` (so the step would
  hard-error) and the Mac-mini has no `init` overlay; the integration test fails because no step creates the tenant.

### T3 — Declare `chwrr` for the Mac-mini and update the dependants
- **Outcome:** `mac-mini.toml` declares `chwrr` / `CHWRR Nepal`; the docs say tenants are declared.
- **In:** `config/overlays/mac-mini.toml`; `docs/standards/cicd.md` (`init` steps and the overlay-wiring note),
  `docs/standards/security.md` (§ Tenant write-isolation), `docs/spec/config-reference.toml`,
  `docs/operations/mac-mini-deploy-runbook.md`, `docs/runbooks/chwrr-dhm-history-import.md`, the `Tenant` type
  comment. **Out:** Plan 268 (see below) and `chwrr-import.toml`.
- **Verification:** a test that the declared name equals `DELIVERY_TENANT_NAME` (`cli/import_dhm_delivery.py`) and
  that `sapphire` matches migration `0041`'s name (the strings must have one source of truth); a test that a
  database prepared by the old command keeps its tenant and id when the declaration is added; `grep -rn "bootstrap-tenant" docs/operations docs/runbooks docs/standards` shows the
  runbook describing **both** paths (declared tenants preferred, the command kept until Plan 510 T5 removes it) and
  nothing else stale. Plan text (268, 510, 513) is excluded: it legitimately discusses the command.
- **Pre-change:** N/A (documentation and one overlay entry; the behaviour is covered in T2).
- **Plan 268 is `READY`.** Its T8 and D11 still prescribe `bootstrap-tenant`. This plan **proposes** the change
  (T8 has since been run by the owner-approved one-off route on 2026-09-29, so the proposal reduces to: record that
  and describe declared tenants as the way to create the next one); the edit to a READY plan
  needs its own independent review before it is applied. This plan only proposes it; **Plan 510 T7 owns and applies
  the single edit** (so the two plans do not both touch it).

## Rollback

- **A wrong declaration:** fix or remove it and re-run `init`; nothing was committed if the step failed.
- **A tenant created by mistake:** never deleted automatically; owner SQL, only while it owns no rows.
- **A blocked `init`:** the step is a no-op when nothing is declared, so removing the declaration unblocks it.

## Exit gates

`uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run pyright src/`, the tests named
per task, and a full `uv run pytest` before merge; affected docs updated in the same change.

## Not in scope

Renaming tenants; deleting tenants; per-host declarations of anything but tenants; the operator database role
(Plan 510).

## Implementation notes from the final gate reviews (non-normative; none blocks READY)

- T2's concurrency test cannot present unsorted batches through the step (it sorts its input); it calls
  `ensure_tenant` directly, or the batch function with unsorted input, to prove the deadlock protection.
- The same code declared in two overlays with different names resolves silently to the rightmost overlay through the
  merge; D2 still catches any mismatch against the database. A parser test states this behaviour.
- An owner-renamed `sapphire` row makes `init` fail (D2); the runbook says so.
- T3's "existing tenant keeps its id" test creates the tenant directly through the store, so it survives Plan 510 T5
  removing `bootstrap_tenant`; T2's test remains the source of truth for that behaviour.
- The runbook edit in T3 is made at the runbook's then-current path (Plan 512 may move it).
- The `init` ordering test at `tests/unit/deploy/test_compose_db_roles.py:147` gains the tenant step between
  `alembic` and the role bootstrap.
- Plan 268 stays stale until Plan 510 T7 lands; its `bootstrap-tenant` step is idempotent, so a reader who re-runs T8
  from it does no harm.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
