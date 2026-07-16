---
status: DRAFT (stub)
created: 2026-07-16
plan: 120
title: Basin/static package importer + §5a Gateway polygon-reference persistence
scope: Import an accepted basin/static artifact package and persist the Gateway polygon-reference mapping; Nepal v1.
depends_on: []
blocks: [082]
---

# Plan 120 — Basin/static package importer + §5a persistence

**Status**: DRAFT (stub)
**Phase**: v1
**Depends on**: an accepted basin/static package (Plan 117 contract); 115a (role model)

---

## Why this exists

`docs/requirements/04-basin-static-artifact-contract.md` §5a states that "the
implementation plan for this artifact contract MUST add an equivalent persistence
target before Nepal production enablement." **Plan 117 is docs-only and explicitly
builds no importer** (`docs/plans/117-basin-static-artifact-architecture.md`) — it
records the boundary and the §5a gap but leaves the import + persistence to a
separate plan. This is that plan.

Plan 082 (recap Gateway operational readiness) ships a thin *store-backed*
`GatewayPolygonResolver` that **reads** the §5a mapping table. Plan 120 owns the
**write** side: validating/importing an accepted basin/static package and populating
that mapping. Until 120 lands and a package is imported, 082's resolver returns
`None` for every station (all-unmappable) — so **082's production run gates on 120**,
though 082's build/tests do not (they use a fixture).

## Scope (to fill in when promoted from stub to DRAFT)

- **§5a persistence — table SCHEMA is owned by Plan 082, not 120.** Plan 082 Task 2D
  adds the minimal additive mapping table keyed by `station_id + gateway_hru_name +
  name` (columns `station_id`, `basin_id`, `gateway_hru_name`, `name`, `spatial_type`,
  `band_id`, per `04` §5a) because its store-backed resolver reads it. **120 does NOT
  define or migrate that table** — 120 owns *populating* it (below). This avoids a
  double-migration: one owner of the schema (082), one owner of the writes (120).
- **Package import + validation** — accept a validated basin/static package
  (`04` contract: `manifest.json`, `basins.gpkg`, `static_attributes.parquet`,
  `feature_catalog.json`, `validation_report.json`), run SAP3-side import acceptance
  rules (`04` §9), write basin geometry + `basins.attributes` + the §5a mapping,
  record provenance (`package_id`, checksums).
- **Provenance** — which package produced each geometry / attribute / mapping row
  (`04` §11).

## Not in scope

- The extraction tool itself (adjacent; `04` boundary — SAP3 does not call it).
- The static feature schema (modeller-owned; `04` §6).
- Gateway operational fetch/watchdog/coverage (Plan 082).

## Open questions (resolve before promoting to READY)

- (Table ownership is SETTLED: 082 Task 2D owns the §5a table schema/migration; 120
  owns population only — see Scope.) If a *fuller* schema than 082's minimal table is
  later needed, 120 proposes it as an additive migration on top of 082's, never a
  redefinition.
- One additive table vs a typed `Basin` metadata field (`04` §5a offers both) — 082
  Task 2D makes this call; 120 follows it.
- Trigger + idempotency for re-import on a new `package_id` (`04` §11 immutability).

## References

- `docs/requirements/04-basin-static-artifact-contract.md` (§5a, §9, §11)
- `docs/plans/117-basin-static-artifact-architecture.md` (the contract-alignment plan)
- `docs/plans/082-recap-gateway-operational-readiness.md` (consumes the §5a mapping)
