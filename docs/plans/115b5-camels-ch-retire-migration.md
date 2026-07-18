---
status: READY
created: 2026-07-17
plan: 115b5
parent: 115b
title: Release B — retire the camels-ch weather binding (migration 0033, in-migration guard)
scope: The deferred second release of the 115b4 two-release cutover. Migration 0033 retires the camels-ch weather binding, guarded IN the migration (SELECT + raise, atomic) so no station is stranded. Release A is confirmed serving, so the gate is met.
depends_on: [115b4]
---

# Plan 115b5 — Release B: retire the camels-ch weather binding

> Canonical Release-B plan (this file is the one 115b4 / 115c / `cicd.md` link to). Supersedes the
> initial stub committed with #94 and an orphan draft `115b5-retire-camels-weather-binding.md` (removed).

## What this is

The **second release** of the 115b4 reader-flip cutover. 115b4 §5E specified retiring the
`camels-ch` **weather binding**, deferred to its own release so a retire migration on Release A's
Alembic head could not fire before the hybrid reader was confirmed serving. **That gate is now
satisfied:** Release A was deployed to staging 2026-07-17, the reader default is `hybrid`, and the
hybrid reader is **proven serving** MeteoSwiss (`meteoswiss_rhiresd` resolved for a station's 2024
precip). Release B may proceed.

## Design — an IN-MIGRATION guard (reworked after review)

A first draft added a standalone `init`-container audit script; review rejected that as
over-engineered *and* less safe. This repo already has the right pattern: **a `SELECT` guard that
`raise`s inside `upgrade()`, atomic with the DELETE, in the SAME transaction** (Alembic wraps online
migrations in a transaction, `alembic/env.py:39-47`) — precedent
`0023_add_regional_basin_and_unique_constraint.py:24-34` and `0030_weather_source_role.py:47-65`.

**Migration `0033_retire_camels_ch_weather_binding`** (down_revision `0032`):
1. **GUARD (SELECT + raise, before the DELETE, same txn):** raise `RuntimeError` (naming the station
   ids) if any station would be left with **no reanalysis binding** after the delete — i.e. a station
   that has a `camels-ch` weather-source row the reader treats as reanalysis, but **no** surviving
   non-`camels-ch` row the reader treats as reanalysis. The transaction rolls back, deleting nothing.
2. **DELETE** the `camels-ch` weather-binding rows.
3. **DO NOT touch `historical_forcing`** (`db/metadata.py:417-424`). CAMELS forcing rows stay as the
   115b3 validation reference + audit trail; CAMELS remains the runoff/discharge + static-attribute +
   basin-polygon source. Only the *weather binding* is retired.
- **`downgrade()` = deliberate NO-OP** (logs a warning, resurrects nothing). The locked
  `tests/integration/db/test_migration_0026_downgrade.py:73-98` downgrades from head through every
  revision, so a `raise` would fail it; a no-op keeps the chain traversable.
- **Rollback = restore the DB backup + previous image** (`cicd.md:194-198`), NOT the schema downgrade.
  The downgrade is a no-op **because the deleted rows/status cannot be honestly reconstructed from
  schema state alone** — do not claim reversibility.

### The predicate MUST match the reader's effective-membership (the review blocker)

The guard's "reanalysis binding" and the DELETE's target MUST be the **same predicate the hybrid
reader uses for membership** — not `role='reanalysis' AND status='active'` (which is wrong twice):
- **No status filter.** `fetch_reanalysis_bindings(sid)` filters by `role == REANALYSIS` only, with
  **no `status` check** (`station_store.py:310-317`) — so an *inactive* reanalysis binding is still a
  binding to the reader. The migration must not add a `status='active'` filter the reader lacks.
- **NULL roles map to reanalysis.** `_row_to_weather_source` maps a NULL DB `role` to a legacy role
  via `_legacy_role_for_source(nwp_source)` (`station_store.py:386-393`), and the `role` column is
  still nullable (`db/metadata.py:187-190`); for `camels-ch` that legacy role is REANALYSIS. So a
  NULL-role `camels-ch` row IS a reanalysis binding to the reader — a `role='reanalysis'` SQL
  predicate (false for NULL) would MISS it and leave it un-retired.

**Therefore:** DELETE all `camels-ch` weather-source rows the reader treats as reanalysis bindings —
i.e. `nwp_source='camels-ch'` where the *effective* role is REANALYSIS (`role='reanalysis' OR role IS
NULL`, since `camels-ch`'s legacy role is reanalysis; verify there is no `camels-ch`/`forecast` row —
there is none, `camels-ch` is a reanalysis-only source). The GUARD's surviving-binding check uses the
same effective-membership predicate on the *non-camels* rows. Implementer nails the exact SQL; the
tests below pin the role/NULL/status edge cases so it can't silently diverge from the reader.

## Why the guard is REQUIRED (the correctness point the first draft glossed)

A reanalysis binding supplies the hybrid reader with a station's **membership**, not just a source:
`fetch_reanalysis_bindings` returns the reanalysis-role rows, and `PerSourceStoreReader` reduces the
configs to station IDs before fetching (`per_source_store_reader.py:47-60`). So the reader keeps
serving a station only while it has *some* reanalysis binding. Deleting `camels-ch` is safe **only**
because each station also has its `meteoswiss_open_data_reanalysis` binding (115b2 §2A) — but the
migration must *prove* that per-station rather than assume it, hence the guard. (The earlier "safe
because hybrid resolves by `station_id`" conflated *resolution source* with *membership*.)

Data coverage is NOT the guard: CAMELS is not in the hybrid priority chain
(`hybrid_reanalysis_factories.py:37-45`), so it never wins a read anyway; whether the surviving
source has forcing rows is a pre-existing Release-A concern the 115b2 backfill already satisfied, and
the retire does not change it.

## Tasks

- **B1 — migration `0033`** with the in-migration guard (SELECT + raise using the reader's
  effective-membership predicate) then the DELETE, atomic; forcing rows untouched; downgrade = no-op.
  (No new script, no docker-compose edits.)
- **B2 — reconcile the Release-A head guard test (it WILL break, intentionally).**
  `tests/unit/db/test_alembic_head_release_a.py` asserts the retire migration is absent from head
  (Release-A invariant) AND that there is exactly one Alembic head (`:114-130`). Adding `0033` makes
  head=`0033`, so the absence assertion fails *by design*. **Replace** it (don't silently delete) with
  a post-Release-B test that (a) preserves the **single-head invariant** — assert the leaf set is
  exactly `{0033}` — and (b) pins that `0033` deletes only the `camels-ch` binding, forcing rows
  survive, and the guard raises when a station has no replacement binding. Flag the expected break in the PR.
- **B3 — doc + wording sync.** Fix the migration docstring and `cicd.md:174-178` wording claiming the
  binding "can never resolve a row"/"is simply never read": accurate statement is *the binding no
  longer selects CAMELS rows (MeteoSwiss wins the priority chain), but it still supplies station
  membership until a replacement binding exists — which is why the retire is guarded*. Reconcile
  migration-numbering (`115b5`=`0033`; 115c's cleanup → next free revision) across the full inbound set:
  `store/station_store.py:45-48`, `0032` header, `cicd.md:161`, and `115c-weather-identity-cleanup.md:32`.
  Note in `v0-scope.md`/`architecture-context.md` that the camels-ch weather binding is retired.

## Tests

- **Guard RAISES on a would-be-stranded station (negative — load-bearing):** a station with only a
  `camels-ch` reanalysis binding (no MeteoSwiss binding) makes `0033.upgrade()` raise and delete
  nothing — **even with MeteoSwiss forcing rows present**. *Soundness: fails against an unconditional delete.*
- **Edge-case membership (the review majors):** the guard/delete match the reader — cover an
  **inactive** `camels-ch`-only station (must still raise, since the reader ignores status) and a
  **legacy NULL-role** `camels-ch` row (must be treated as reanalysis: guarded + deleted). *Soundness:
  fails against a `status='active'`-only or `role='reanalysis'`-only SQL predicate.*
- **Guard PASSES + deletes only the binding (positive):** a station with both `camels-ch` and an
  active `meteoswiss_open_data_reanalysis` binding (**seed it with `role='reanalysis'`, not
  `forecast`**) — after `upgrade`, no `camels-ch` reanalysis row remains, the MeteoSwiss binding
  survives, and `historical_forcing WHERE source='camels-ch'` row count is **unchanged**.
- **Hybrid reader still serves after retire:** the positive station resolves reanalysis forcing
  (non-empty) with only its MeteoSwiss binding left.
- **Downgrade is a safe no-op:** `0033.downgrade()` runs, resurrects no rows, full-chain downgrade test still passes.
- **Head test (B2):** leaf set is exactly `{0033}`; single-head invariant preserved.

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
uv run pytest                  # incl. the new 0033 integration tests (guard raise/pass, NULL/inactive edges) + the replaced head test
```

**Deploy (Release B, separate, standard path — only after Release A confirmed serving [DONE]):**
`alembic upgrade head` (→ `0033`) in the `init` container runs the guard then retires the binding;
confirm the `camels-ch` binding is **gone**, its forcing rows **remain**, and the hybrid reader still
serves a station. **Rollback = restore `~/pre-*` backup + previous image**, not a schema downgrade.

## Provenance

Extracted from 115b4 §5E; Release A deployed + confirmed serving 2026-07-17 (gate met). Reworked
2026-07-18 after the `/plan` run over-engineered a standalone init-guard script (reviewers rejected it
for an in-migration guard, 0023/0030 precedent) and an independent Codex review then caught the
load-bearing correctness issue: the guard/delete predicate must match the reader's effective-membership
(role-only, no status filter, NULL→legacy-reanalysis). READY (owner, 2026-07-18) — implementation authorised; hold at PR; deploy as the separate Release B.
