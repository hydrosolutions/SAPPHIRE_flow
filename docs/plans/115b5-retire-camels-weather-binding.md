---
status: DRAFT
created: 2026-07-17
plan: 115b5
parent: 115b
title: Release B — retire the camels-ch weather binding (migration 0033, in-migration guard)
scope: The deferred second release of the 115b4 two-release cutover. Migration 0033 retires the camels-ch weather binding, guarded IN the migration (SELECT + raise, atomic) so no station is stranded. Release A is confirmed serving, so the gate is met.
depends_on: [115b4]
---

# Plan 115b5 — Release B: retire the camels-ch weather binding

## What this is

The **second release** of the 115b4 reader-flip cutover. 115b4 §5E specified retiring the
`camels-ch` **weather binding**, deferred to its own release so a retire migration on Release
A's Alembic head could not fire before the hybrid reader was confirmed serving. **That gate is
now satisfied:** Release A was deployed to staging 2026-07-17, the reader default is `hybrid`,
and the hybrid reader is **proven serving** MeteoSwiss (`meteoswiss_rhiresd` resolved for a
station's 2024 precip). Release B may proceed.

## Design — an IN-MIGRATION guard, not a separate script (reworked after /plan review)

A first draft added a standalone `init`-container audit script; a review round showed that was
over-engineered *and* less safe (a separate script isn't in the shipped image, needs its own
DB-connection code, and opens a two-step delete-vs-guard window). This repo already has the
right pattern: **a `SELECT` guard that `raise`s inside `upgrade()`, atomic with the destructive
change, in the SAME transaction** — precedent `0023_add_regional_basin_and_unique_constraint.py:24-34`
and `0030_weather_source_role.py:32-34`. Migration `0033` does exactly this.

**Migration `0033_retire_camels_ch_weather_binding`** (down_revision `0032`):
1. **GUARD (SELECT + raise, before the DELETE, same txn):** identify any station whose ONLY
   active reanalysis binding is `camels-ch` — i.e., a station with a `camels-ch`/`reanalysis`/
   `active` row that has **no** other active reanalysis binding. If any exist, `raise
   RuntimeError(...)` naming the station ids — the migration aborts and the transaction rolls
   back, deleting nothing. SQL shape:
   ```sql
   SELECT station_id FROM station_weather_sources
    WHERE role='reanalysis' AND status='active' AND nwp_source='camels-ch'
      AND station_id NOT IN (
        SELECT station_id FROM station_weather_sources
         WHERE role='reanalysis' AND status='active' AND nwp_source <> 'camels-ch');
   ```
2. **DELETE** the `camels-ch` weather-binding rows (`WHERE nwp_source='camels-ch' AND
   role='reanalysis'`; PK `(station_id, nwp_source)`, `db/metadata.py:164-193`).
3. **DO NOT touch `historical_forcing`.** The CAMELS forcing rows stay as the 115b3 validation
   reference + audit trail; CAMELS remains the runoff/discharge + static-attribute + basin-polygon
   source. Only the *weather binding* is retired.
- **`downgrade()` is a deliberate NO-OP** (logs a warning, resurrects nothing) — the locked
  `tests/integration/db/test_migration_0026_downgrade.py` traverses every revision, so a `raise`
  would fail it; a no-op keeps the chain traversable while honouring "do not claim reversibility."
- **Rollback = restore the DB backup + previous image** (`cicd.md:137-139`), NOT the schema
  downgrade. Safe because the binding shape is deterministic/reconstructable (`onboarding.py:365-371`).

## Why the guard is REQUIRED (the correctness point the first draft glossed)

A reanalysis binding supplies the hybrid reader with a station's **membership**, not just a
source: `fetch_reanalysis_bindings(sid)` filters weather-source rows by `role`
(`station_store.py:310-317`), and `PerSourceStoreReader` reduces the configs to station IDs
before fetching (`per_source_store_reader.py:47-60`). So the reader keeps serving a station only
while it has *some* active reanalysis binding. Deleting `camels-ch` is safe **only** because each
station also has its `meteoswiss_open_data_reanalysis` binding (115b2 §2A) — but the migration
must *prove* that per-station rather than assume it, hence the guard. (My earlier "safe because
hybrid resolves by `station_id`" was too glib — it conflated *resolution source* with *membership*.)

Note on data coverage (NOT part of the guard): whether the surviving binding's source actually
has forcing rows for a station's parameters is a *pre-existing* concern of Release A's live hybrid
read — the 115b2 backfill populated it, and the retire does not change it (the priority chain
already prefers MeteoSwiss over CAMELS). The retire's own safety invariant is membership, above.

## Tasks

- **B1 — migration `0033`** with the in-migration guard (SELECT + raise) then the DELETE, atomic;
  forcing rows untouched; downgrade = no-op. (No new script, no docker-compose edits.)
- **B2 — reconcile the Release-A head guard test (it WILL break, intentionally).**
  `tests/unit/db/test_alembic_head_release_a.py` asserts the retire migration is **absent** from
  head — the Release-A invariant. Adding `0033` makes head = `0033` with the retire present, so
  that test now fails *by design* (two-release separation is now enforced by these being separate
  PRs). **Replace** it (don't silently delete) with a test pinning the post-Release-B invariant:
  `0033` is the retire, it deletes only the `camels-ch` binding, forcing rows survive, and the
  guard raises when a station has no replacement binding. Flag this expected break in the PR.
- **B3 — doc + wording sync.** Fix the migration docstring and `cicd.md:174-178` wording that
  claims the binding "can never resolve a row"/"is simply never read": accurate statement is *the
  binding no longer selects CAMELS rows (MeteoSwiss wins the priority chain), but it still supplies
  station membership until a replacement binding exists — which is why the retire is guarded*.
  Reconcile migration-numbering (`115b5` takes `0033`; 115c's cleanup → next free revision) across
  **the full inbound-reference set**: `store/station_store.py`, `0032` header, `cicd.md`, AND
  `docs/plans/115c-weather-identity-cleanup.md`. Note in `v0-scope.md`/`architecture-context.md`
  that the camels-ch weather binding is retired (CAMELS = validation-reference + discharge/static/polygon only).

## Tests

- **Guard RAISES on a would-be-stranded station (negative — the load-bearing test):** a station
  with only a `camels-ch`/`reanalysis` binding (no MeteoSwiss binding), even with MeteoSwiss
  forcing rows present, makes `0033.upgrade()` raise and delete nothing. *Soundness: fails against
  a migration that deletes unconditionally.*
- **Guard PASSES + deletes only the binding (positive):** a station with both `camels-ch` and an
  active `meteoswiss_open_data_reanalysis` (`role='reanalysis'`) binding — after `upgrade`,
  `station_weather_sources` has no `camels-ch`/`reanalysis` row, the MeteoSwiss binding survives,
  and `historical_forcing WHERE source='camels-ch'` is **unchanged** (row count identical).
  *(Seed the replacement binding with `role='reanalysis'`, not `forecast`.)*
- **Hybrid reader still serves after retire:** the guarded/positive station resolves reanalysis
  forcing (non-empty) with only its MeteoSwiss binding left. *Soundness: fails if retiring strands the reader.*
- **Downgrade is a safe no-op:** `0033.downgrade()` runs, resurrects no rows, full-chain downgrade
  test still passes.
- **Head-guard replacement (B2):** the new test pins the post-Release-B invariant.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-B",
      "name": "Release B — retire the camels-ch weather binding (in-migration guard)",
      "tasks": ["B1-guarded-retire-migration", "B2-reconcile-head-guard-test", "B3-doc-and-wording-sync"],
      "parallel": false,
      "task_depends_on": {"B2-reconcile-head-guard-test": ["B1-guarded-retire-migration"], "B3-doc-and-wording-sync": ["B1-guarded-retire-migration"]},
      "depends_on": ["plan-115b4-release-A-deployed-and-serving"]
    }
  ]
}
```

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. the new 0033 integration tests (guard raise/pass) + the replaced head-guard test
```

**Deploy (Release B, separate, standard path — only after Release A confirmed serving [DONE]):**
`alembic upgrade head` (→ `0033`) in the `init` container runs the guard then retires the binding;
confirm the `camels-ch` binding is **gone**, its forcing rows **remain**, and the hybrid reader
still serves a station. **Rollback = restore `~/pre-*` backup + previous image**, not a schema downgrade.

## Provenance

Extracted from 115b4 §5E; Release A deployed + confirmed serving 2026-07-17 (gate met). Reworked
2026-07-18 after the `/plan` run: its planner over-engineered a standalone init-guard script, which
the review correctly rejected in favour of an in-migration guard (0023/0030 precedent) and surfaced
the real correctness point — a reanalysis binding supplies station membership, so the retire must
prove a surviving replacement binding per station. DRAFT — re-review (independent Codex) before READY.
