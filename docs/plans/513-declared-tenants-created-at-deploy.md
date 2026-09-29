---
status: DRAFT
created: 2026-09-29
revised: 2026-09-29   # round 1: the independent Claude and Codex reviews (both NEEDS CHANGES) folded in
plan: 513
title: Declared tenants — a host's config lists its tenants and the deploy creates them
scope: A per-host declaration of tenants that a new `init` step creates idempotently and atomically, so no tenant needs a migration and no host carries a tenant it does not host. Done first: Plan 510 and Plan 268 T8 build on it.
risk: high   # a new step in every deployment's `init`, configuration schema, compose wiring (docs/workflow.md § High-risk work)
depends_on: [147]
blocks: [510]
related: [268, 269, 512]
open_decisions: []
closed_decisions: [D1, D2, D3]   # recommended answers below; owner to confirm at the owner-commissioned review
source: 2026-09-29 — owner: "what we actually need is option b" (declared tenants), and the order is 513, then 510, then Plan 268 T8; no stopgap seed migration.
---

# Plan 513 — declared tenants

## Status

**DRAFT, HIGH RISK.** Round 1 review (Claude and Codex, 2026-09-29) returned NEEDS CHANGES from both; this revision
folds it in. It needs a second independent pair and one owner-commissioned review before READY
(`docs/workflow.md` § High-risk work). The orchestrator sets READY.

## Why

Tenants are provisioning, done at deploy time under the owner (Plan 510's split). Migration `0041` is the only
migration seed (`sapphire`, fixed id); the `bootstrap-tenant` command of Plan 268 is the only other way to create
one, and it needs the owner database credential at run time. Seeding `chwrr` by migration would put an empty
tenant in every database permanently and require a migration per future tenant. Each host should declare the
tenants it hosts instead.

## Facts (measured from the repo, 2026-09-29 — this replaces the measurement task)

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
  (new rows need no grant work) and before `register_deployments`. It reads the base config and the overlay,
  creates all missing declared tenants **in one transaction** (`INSERT … ON CONFLICT (code) DO NOTHING`, then the
  name check, so two concurrent `init` runs cannot both fail on the unique code), and is a **no-op when nothing is
  declared**, so existing deployments are unaffected. It is a hard failure: a conflict stops `init`, and the
  message names the tenant. It is safe to run with services up (additive), but a full `init` still follows the
  upgrade procedure that stops the workers first.
- **Removing a declaration deletes nothing.** A mistaken tenant is removed by the owner with SQL, and only while
  it owns no rows.

## Tasks

### T1 — The declaration schema and parser
- **Outcome:** the `[tenants.<code>]` table is parsed and validated into `DeclaredTenant` objects; invalid codes,
  blank or over-long names and a mismatched `sapphire` name are rejected with clear errors.
- **In:** a Pydantic model at the config boundary, the dataclass, `docs/spec/config-reference.toml`.
  **Out:** creating tenants; `writable_tenants`.
- **Verification:** `tests/unit/config/` tests for valid, invalid, merged-overlay and empty declarations.
- **Pre-change:** RED — today a `[tenants]` table is ignored or rejected by the config loader.

### T2 — The provisioning step and `init` wiring
- **Outcome:** a fresh database and an existing one gain the declared tenants; an existing matching tenant is
  kept with its id; a name conflict aborts and leaves the batch uncommitted; concurrent runs are safe; a
  declaration-less host does nothing. `init` on the Mac-mini receives the config and the overlay (an `init:` block in
  `docker-compose.macmini.yml`, matching the staging one, plus `SAPPHIRE_CONFIG`).
- **In:** the new command, `docker-compose.yml` (`init` command chain), `docker-compose.macmini.yml`,
  `docker-compose.staging.yml`. **Out:** the `bootstrap-tenant` code (Plan 510 T5).
- **Verification:** `tests/integration/db/` tests for a fresh database, an existing `chwrr` created by the old
  command (random id, matching name), a name conflict, an existing tenant holding stations, a rerun, and two
  concurrent runs; `tests/unit/deploy/` extends the compose tests: every compose stack that runs `init` with a
  declaring overlay passes both the config and the overlay, and `init`'s command order is pinned.
- **Pre-change:** RED — the compose test fails today because `init` on the Mac-mini has no config or overlay;
  the integration test fails because no step creates the tenant.

### T3 — Declare `chwrr` for the Mac-mini and update the dependants
- **Outcome:** `mac-mini.toml` declares `chwrr` / `CHWRR Nepal`; the docs say tenants are declared.
- **In:** `config/overlays/mac-mini.toml`; `docs/standards/cicd.md` (`init` steps and the overlay-wiring note),
  `docs/standards/security.md` (§ Tenant write-isolation), `docs/spec/config-reference.toml`,
  `docs/operations/mac-mini-deploy-runbook.md`, `docs/runbooks/chwrr-dhm-history-import.md`, the `Tenant` type
  comment. **Out:** Plan 268 (see below) and `chwrr-import.toml`.
- **Verification:** a test that a database prepared by the old command keeps its tenant and id when the
  declaration is added; `grep -rn "bootstrap-tenant" docs/` lists only Plan 268 and the retirement note.
- **Pre-change:** N/A (documentation and one overlay entry; the behaviour is covered in T2).
- **Plan 268 is `READY`.** Its T8 and D11 still prescribe `bootstrap-tenant`. This plan **proposes** the change
  (T8 is gated on deployed 513 **and** 510, and the tenant comes from the declaration); the edit to a READY plan
  needs its own independent review before it is applied, and this plan does not make it.

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

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
