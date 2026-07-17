---
status: DRAFT
created: 2026-07-17
plan: 115b5
parent: 115b
title: Release B — retire the camels-ch weather binding (migration 0033)
scope: The deferred second release of the 115b4 two-release cutover. Ships the camels-ch weather-binding retirement as its own migration + deploy, now that Release A is confirmed serving.
depends_on: [115b4]
---

# Plan 115b5 — Release B: retire the camels-ch weather binding

## What this is

The **second release** of the 115b4 reader-flip cutover. 115b4 §5E specified retiring the
`camels-ch` **weather binding**, but structured it as a SEPARATE release because a retire
migration on Release A's Alembic head would fire (via the `init` container's `alembic
upgrade head`) *before* any running system could confirm the hybrid reader is serving —
stranding stations. So Release A shipped the flip with head `0032` and a **guard test**
asserting the retire migration is absent from head; Release B (this plan) ships the retire
migration on its own deploy.

**The Release-A gate is now satisfied:** Release A was deployed to staging 2026-07-17, the
reader default is `hybrid`, and the hybrid reader is **proven serving** MeteoSwiss
(`meteoswiss_rhiresd` resolved for a station's 2024 precip). So Release B may proceed.

## The design (locked in 115b4 §5E — carried here verbatim)

- **Migration `0033_retire_camels_ch_weather_binding`** (down_revision `0032`) — **DELETE the
  `camels-ch` weather-binding rows** from `station_weather_sources` (`WHERE nwp_source =
  'camels-ch' AND role = 'reanalysis'`; PK `(station_id, nwp_source)`, `db/metadata.py:164-193`).
- **DO NOT delete the CAMELS forcing ROWS** (`historical_forcing WHERE source = 'camels-ch'`).
  They stay as the 115b3 validation reference + audit trail; CAMELS remains the
  runoff/discharge + static-attribute + basin-polygon source. Only the *weather binding* is retired.
- **`downgrade()` is a deliberate NO-OP** that logs a warning and never resurrects rows —
  NOT a fabricated restore. Reason (from the 115b4 build): the pre-existing LOCKED test
  `tests/integration/db/test_migration_0026_downgrade.py` downgrades from head through **every**
  revision, so a `raise` in `0033.downgrade()` would fail that unrelated test; a no-op keeps
  the chain mechanically traversable while honouring "do not claim reversibility."
- **Rollback = the repo's standard path** (restore the DB backup + previous image,
  `cicd.md:137-139`) — NOT the schema downgrade. Safe because the binding shape is deterministic
  and reconstructable (`nwp_source=forcing[0].source`, `extraction_type=POINT`, `status=ACTIVE`,
  `role=REANALYSIS`, `onboarding.py:365-371`).

## Why retiring the binding is safe (no station stranded)

Release A's hybrid reader resolves reanalysis reads via the deployment-global priority chain
over `historical_forcing`, keyed on `station_id` — the binding contributes only the station's
membership, not the source. Each station still has its **`meteoswiss_open_data_reanalysis`**
binding (created by 115b2 §2A), so `fetch_reanalysis_bindings(sid)` remains non-empty and the
hybrid reader keeps serving after the `camels-ch` binding is gone. (115b4's 5E test already
proved the reader serves without any dependency on this migration.)

## Tasks

- **B1 — the retire migration** `0033_retire_camels_ch_weather_binding.py` (delete the
  `camels-ch`/`reanalysis` binding rows; forcing rows untouched; downgrade = no-op).
- **B2 — reconcile the Release-A head guard test (REQUIRED — it WILL break, and that break is
  the point).** `tests/unit/db/test_alembic_head_release_a.py` was written to assert the retire
  migration is **absent** from head (the Release-A invariant). Adding `0033` makes head = `0033`
  with the retire present, so that test would now fail. It must be **retired/rewritten**: the
  two-release *separation* is now enforced by these being separate PRs, so the invariant it
  guarded is intentionally lifted. Replace it (do not just delete silently) with a test that
  pins the *new* intended state — e.g. `0033` is the retire migration, it deletes only the
  `camels-ch` binding, and the CAMELS forcing rows survive. Call out in the plan/PR that this
  guard test change is expected, not a regression.
- **B3 — doc sync:** update `115c-weather-identity-cleanup.md` + `store/station_store.py` +
  `cicd.md` migration-numbering (115b5 takes `0033`; 115c's cleanup shifts to the next free
  revision `0034`); note in `docs/v0-scope.md`/`architecture-context.md` that the `camels-ch`
  weather binding is retired (CAMELS is now validation-reference + discharge/static/polygon only).

## Tests

- **Retire deletes only the binding:** after `upgrade`, `station_weather_sources` has **no**
  `camels-ch`/`reanalysis` row; `historical_forcing WHERE source='camels-ch'` is **unchanged**
  (row count identical). *Integration test against real Postgres. Soundness: fails against a
  migration that also touches historical_forcing, or one that deletes the wrong role.*
- **Hybrid reader still serves after retire:** a station with only its `meteoswiss_*` binding
  left still resolves reanalysis forcing (non-empty). *Soundness: fails if retiring the binding
  strands the reader.*
- **Downgrade is a safe no-op:** `0033.downgrade()` runs without error, resurrects **no** rows,
  and the full-chain downgrade test still passes. *Soundness: fails against a downgrade that
  raises or fabricates a restore.*
- **Guard-test replacement (B2):** the new head/state test pins the post-Release-B invariant and
  would fail if `0033` deleted forcing rows or targeted the wrong binding.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-B",
      "name": "Release B — retire the camels-ch weather binding",
      "tasks": ["B1-retire-migration", "B2-reconcile-head-guard-test", "B3-doc-sync"],
      "parallel": false,
      "task_depends_on": {"B2-reconcile-head-guard-test": ["B1-retire-migration"], "B3-doc-sync": ["B1-retire-migration"]},
      "depends_on": ["plan-115b4-release-A-deployed-and-serving"]
    }
  ]
}
```

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest                  # incl. the new migration 0033 integration tests + the replaced guard test
```

**Deploy (Release B, separate, standard path — only after Release A confirmed serving [DONE]):**
`alembic upgrade head` (→ `0033`) in the `init` container retires the binding; confirm the
`camels-ch` binding is **gone** while its forcing rows **remain** and the hybrid reader still
serves a station. **Rollback = restore `~/pre-*` backup + previous image**, not a schema downgrade.

## Provenance

Extracted from 115b4 §5E (the two-release split, owner decision 2026-07-16). Release A deployed +
confirmed serving on staging 2026-07-17, satisfying the gate for Release B. DRAFT — plan-review
(incl. independent Codex) before READY; the B2 guard-test reconciliation is the one non-obvious
point a reviewer should scrutinise.
