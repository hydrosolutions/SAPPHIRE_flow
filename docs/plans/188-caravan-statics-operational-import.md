---
status: DRAFT
created: 2026-08-18
revised: 2026-08-18
plan: 188
title: Run the Caravan statics import for real — the operator entrypoint Plan 155 never got
scope: Plan 155 built and tested `run_operational_caravan_import` but nothing calls it, so no basin in any deployment carries a single `caravan:`-prefixed attribute. Write the operator CLI, add the one missing recovery primitive, run it on the mac mini where the Swiss fleet is onboarded. This unblocks BOTH `cmal_pool_PT` (its 50 declared statics resolve through the `caravan:` namespace) and Plan 183's T3 parity check (it compares against `caravan:` climate indices that do not exist yet).
depends_on: [155]
blocks: [152, 183]
supersedes: []
---

# Plan 188 — run the Caravan statics import for real

## Status note (2026-08-18): RECONSTRUCTED after a `/plan` round inflated it

The review round produced four genuinely valuable findings (folded below) and then tripled the plan
around them — 97 → 343 lines, 3 tasks → 5, a new Compose `profiles:` mechanism with **zero precedent
in this repo**, the 148 manifest codes dumped verbatim into the plan, and a third re-transcription of
the lake-exclusion list inside a test. The round's own reviewers flagged that inflation as their two
residual majors; rather than grind another round, the plan is reconstructed here at the size the work
actually is. **The four findings are kept. The scaffolding around them is not.**

## ⚠️ Note to reviewers: this is a CLI and a run

**Plan 155 already built and tested the import.** `store/caravan_import.py` carries the entrypoint,
the manifest-scoped exit gate, transaction atomicity, collision-aware coverage checks and
identical-replay protection, covered by `tests/integration/store/test_caravan_import.py`. None of
that is re-opened.

**Out of scope by decision:** persisting provenance to a new table (Plan 155's deferred deviation —
needs schema); artifact invalidation on source revision; changing the gate, the alias map, or the
collision semantics; a Prefect flow; a new Compose service. **This plan should leave review at
roughly its current length.** A round that adds a task, a module, or new infra has gone wrong.

## Why this blocks two other plans

- **`cmal_pool_PT`** declares `StaticNaming.CARAVAN`, so all 50 of its statics resolve through the
  `caravan:` prefix. Without the import it has no static inputs at all.
- **Plan 183 T3** compares recomputed climate indices against `caravan:` reference values. Probed on
  the dev box 2026-08-18: both local basins carry **300 attributes and zero `caravan:` keys**; the
  four indices exist only under bare CAMELS-CH names with different semantics (`high_prec_freq`
  18.41 days/year vs Caravan's ~0.033 fraction). T3 skips every basin.

## What already exists — do not rebuild

`run_operational_caravan_import(path, *, station_store, basin_store, expected_codes,
required_static_names, extractor_version=None, source_dataset_version=None)`.

1. **`expected_codes` and `required_static_names` have no defaults and are rejected when empty** —
   omitting one is a `TypeError`, `frozenset()` raises. This closes a Plan 155 review finding that
   calling the internal function bare "writes data and returns success without validating any
   station/static".
2. **The gate is manifest-scoped, never parquet-scoped.** The parquet holds 296 Swiss codes against
   our 148; the surplus lands in `unmatched_codes` and is never fatal.
3. **`require_real_transaction` refuses to write on an AUTOCOMMIT connection**, so the CLI must open
   a genuine transaction or a mid-loop gate failure leaves a partial import.

## Owner decisions — CONFIRMED (owner, 2026-08-18), refined by review

| # | Decision |
|---|---|
| **D1** | **Derive the manifest live, cross-check against one pinned literal.** T0a's rule: `discharge` in both `forecast_targets` and `measured_parameters`, scoped to `network == "bafu"` (the importer hardcodes the `("bafu", code)` join — `store/caravan_import.py:157`), `StationKind.RIVER` (`types/enums.py:144-147`) and the `sapphire` tenant, minus **2446 Gampelen-Zihlbrücke** (owner-dropped: the regulated Zihlkanal outflow). **Review finding, kept: the check is SET IDENTITY, not cardinality** — `len(...) == 148` also passes a fleet that loses one station and gains another. The pinned side is a single `SWISS_CARAVAN_MANIFEST_CODES` constant in the CLI module, reviewed once against Plan 155's published table when the PR lands. *Not* re-derived at runtime, and *not* re-derived again in a test: a second derivation pipeline is not an independent reference. |
| **D2** | **Read required statics from the DISCOVERED ADAPTER.** **Review finding, kept and important:** `ModelDataRequirements.static_features` (`types/model.py:272`) is populated only by `discover_models()` (`services/model_registry.py:92-128`); the DB row is a bare `ModelRecord` with no requirements field (`types/model.py:259-264`). So "read from the registered model" means the runtime adapter, never the `models` table — the first draft implied otherwise. Discovery **swallows an entry-point import failure and continues** (`model_registry.py:122-127`), so a missing aquacast extra appears as an absent dict key, not an error. The CLI therefore does an explicit named preflight (`if model_id not in discover_models(): raise ConfigurationError(...)`) naming the id and the aquacast requirement — never a bare lookup. |
| **D3** | **`--dry-run` runs the full gate and rolls back**, implemented by raising a private sentinel from *inside* `with engine.begin():` so the rollback is structural, not a flag the code must remember to honour. |
| **D4** | **Import now, and add the one missing recovery primitive** (T3). Provenance is not persisted, so the version string writes no durable record; the real constraint is `merge_namespaced_attributes` (`basin_store.py:147`) — identical replay is a no-op, a *changed* value raises per key. **Review finding, ACCEPTED against the first draft:** `basin_store.py` has exactly three write paths (`store_basin:62`, `merge_namespaced_attributes:147`, `update_basin_from_package:250`) and **none deletes or replaces a `caravan:` key**, so a corrected parquet would have meant hand-written SQL against production. The draft called a helper "untested-until-needed code" — backwards: a helper ships *with* a test, while a runbook recipe is untested by construction and is the one path meant to be executed live, under pressure, against production. |

## Tasks

### T1 — the operator CLI
`scripts/import_caravan_attributes.py`, mirroring `scripts/backfill_meteoswiss_history.py`: build the
engine, run migrations, open a real transaction, construct `PgStationStore`/`PgBasinStore`, derive the
manifest (D1) and required statics (D2), call the entrypoint, print
matched/unmatched/`stations_without_basin`/coverage-gap counts, exit non-zero on the gate.
`--dry-run` and `--parquet <path>`.

**Acceptance** — against a fixture parquet and a seeded fleet:
- a clean run reports every manifest station matched and exits 0;
- a manifest station missing from the parquet exits non-zero and **names that station**;
- `--dry-run` leaves `basins.attributes` byte-identical, asserted by reading the column back rather
  than by trusting the flag;
- a fleet whose derived set differs from the pinned constant by a swap (one code out, one in) fails
  the preflight and prints the symmetric difference — this is the case cardinality misses;
- with the model absent from `discover_models()`, the CLI fails naming the model id, rather than
  raising `KeyError` or proceeding with an empty static set.

### T2 — rename the provenance placeholder
`types/caravan_attributes.py`: `source_dataset_version` default `"unconfirmed@delivered-2026-08-13"`
→ `"initial@delivered-2026-08-13"` (owner). "Unconfirmed" reads as a defect awaiting correction; this
is simply the first delivery. Update the docstring and any assertion that pins the old string.

### T3 — the recovery primitive (D4)
`replace_namespaced_attributes` on `PgBasinStore` — same hardcoded `caravan:` prefix guard and
`SELECT ... FOR UPDATE` locking as `merge_namespaced_attributes`, but replacing the value of an
already-present key instead of refusing. ~15 lines.

**Acceptance:** replaces a changed `caravan:` value; **refuses a non-prefixed key** (the guard is
hardcoded, not a parameter — a caller must not be able to reach `area`); and concurrent callers
serialise on the row lock rather than last-write-wins.

### T4 — run it on the mini
The Swiss fleet is onboarded there (owner-confirmed); the dev box has 2 stations and cannot exercise
this. Reading statics from the adapter (D2) needs the aquacast extra, which the deployed worker image
does not carry (`WITH_AQUACAST=0` by default). **Use the existing build arg** —
`docker build --build-arg WITH_AQUACAST=1` (`Dockerfile:32-44`) — for a one-off image. No new Compose
service: `profiles:` has **zero precedent** in this repo, and inventing throwaway infra that must
later be "deleted or repointed" is the kind of scope this plan exists to avoid.

Stage the parquet, dry-run, read the printed diff, then run for real.

**Acceptance:** all 148 manifest stations resolve every declared static to a finite value, and a
spot-checked basin shows `caravan:` keys alongside its existing CAMELS-CH attributes with the
**bare-named attributes unchanged** — the namespace guard makes the merge structurally incapable of
touching them, and this is where that is confirmed against real data rather than a fixture.

## Follow-on, explicitly not here
Plan 183's T3 parity check becomes runnable once T4 lands. That is the payoff, and it is that plan's
task.
