---
status: DRAFT
created: 2026-09-29
plan: 513
title: Declared tenants — each deployment's config lists its tenants and the deploy creates them
scope: A per-deployment declaration of tenants that the deploy step creates idempotently. Needed so a Swiss deployment does not carry a Nepal partner's tenant, and a new tenant needs no migration. Done first: Plan 510 and Plan 268 T8 build on it.
risk: high   # deploy-step change, configuration schema, touches every deployment's startup (docs/workflow.md § High-risk work)
depends_on: []
blocks: [510]
related: [268, 269, 147, 512]
open_decisions: [D1, D2, D3]
closed_decisions: []
source: 2026-09-29 — owner: "what we actually need is option b" (declared tenants), and the order is 513, then 510, then Plan 268 T8; no stopgap seed migration.
---

# Plan 513 — declared tenants

## Why

Tenants are provisioning, done at deploy time under the owner (Plan 510's split). The only existing example is
migration `0041`, which seeds `sapphire` with a fixed id. Seeding `chwrr` the same way would put an empty Nepal
tenant in every database, including Swiss ones, permanently, and every future tenant would need a migration.
The owner wants each deployment to declare the tenants it hosts, and asked for this to be done first.

## What has to be designed (from the Plan 510 reviews)

- **A tenant declaration, separate from runtime write authority.** `writable_tenants` is a list of tenants a
  host may *write to* and must reference existing tenants. Reusing it for creation would turn a typo into a new
  tenant. The declaration needs its own schema and parser under `config/`.
- **Where it lives.** The base `config.toml` is shared by every deployment; declaring `chwrr` there recreates the
  problem. It belongs in a per-deployment overlay, and that overlay must be loaded by `init`, which today mounts
  only the base config (the overlay is wired only in some compose files; T1 checks which).
- **What the deploy step does.** `init` is a `&&` chain (`alembic upgrade head && bootstrap-roles.sh &&
  register_deployments`), so a failing step leaves a half-deployed stack. Rules to settle: create if absent;
  keep an existing tenant's id; **fail on a name conflict**; never delete a tenant when its declaration
  disappears; a stable id chosen how (a fixed id in the declaration, or generated once and kept).
- **Existing tenants are preserved.** A deployment may already hold `chwrr` (a one-off owner-credential run of
  Plan 268's `bootstrap-tenant`). The deploy step must keep that tenant, its id and its data, and never delete a
  tenant because a declaration disappeared.
- **Plan 268/269 behaviour.** Those plans treat "tenant absent" and "tenant present but empty" differently.
  With declared tenants a deployment that does not declare `chwrr` simply does not have it, so the absent case
  is real again.

## Decisions (owner)

- **D1** — declaration in a per-deployment overlay, in the base config, or in a small dedicated file.
- **D2** — the name-conflict rule (fail, or warn and keep).
- **D3** — how the stable id is chosen.

## Tasks

### T1 — Measure how `init` and the overlays load config today
- **Outcome:** a short note in this plan: which compose files give `init` an overlay, what `init` reads, and the
  exact failure behaviour of each step in the chain.
- **In:** `docker-compose*.yml`, `docker/entrypoint.sh`, `cli/register_deployments.py`. **Out:** code changes.
- **Verification:** bounded inspection, stated as file and line references. **Pre-change:** N/A (measurement).

### T2 — The declaration and the deploy step
- **Outcome:** a declared tenant is created on `init` when absent, kept when present with the same name, and a
  name conflict follows D2; removing a declaration deletes nothing; re-running is a no-op.
- **In:** a config schema and parser, one idempotent step in the `init` chain, tests. **Out:** runtime write
  authority (`writable_tenants` unchanged).
- **Verification:** integration tests on a fresh, existing-empty and existing-populated database, and a
  name-conflict case. **Pre-change:** RED — today a declared tenant is not created by any deploy step.

### T3 — Declare `chwrr` for the Nepal deployment and update the dependants
- **Outcome:** the Nepal deployment's overlay declares `chwrr`; Plan 268's T8 sequence, the import runbook and
  `docs/standards/cicd.md` (`init` steps) say tenants are declared, and `bootstrap-tenant` is no longer the
  way to create one.
- **In:** the overlay entry, docs, Plan 268 T8 text. **Out:** the `bootstrap-tenant` code removal (Plan 510 T5).
- **Verification:** an integration test that a database prepared by the old command keeps its tenant and id when
  the declaration is added; grep for stale statements. **Pre-change:** RED — the tenant is not created by any
  deploy step today.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
