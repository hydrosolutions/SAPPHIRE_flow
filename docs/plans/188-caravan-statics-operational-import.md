---
status: DRAFT
created: 2026-08-18
plan: 188
title: Run the Caravan statics import for real — the operator entrypoint Plan 155 never got
scope: Plan 155 built and tested `run_operational_caravan_import` but nothing calls it, so no basin in any deployment carries a single `caravan:`-prefixed attribute. Write the operator script, stage the parquet, run it on the mac mini where the Swiss fleet is onboarded. This unblocks BOTH `cmal_pool_PT` (its 50 declared statics resolve through the `caravan:` namespace) and Plan 183's T3 parity check (it compares against `caravan:`-prefixed climate indices that do not exist yet).
depends_on: [155]
blocks: [152, 183]
supersedes: []
---

# Plan 188 — run the Caravan statics import for real

## ⚠️ Note to reviewers: this is a script, not a subsystem

**Plan 155 already built and tested the import.** `store/caravan_import.py` carries the entrypoint,
the manifest-scoped exit gate, transaction atomicity, collision-aware coverage checks and
identical-replay protection, all covered by `tests/integration/store/test_caravan_import.py`. None of
that is re-opened here.

What is missing is the ~150-line operator script that calls it, plus actually running it. Sibling
plans on this track were inflated to 400-583 lines across review rounds by invented scope and had to
be discarded. **Please push back on anything that grows this.**

**Out of scope by decision:** persisting provenance to a new table (Plan 155's own deferred T1
deviation — needs schema); artifact invalidation on source revision; changing the gate, the alias map,
or the collision semantics; a Prefect flow (a supervised script is the right shape, exactly as
`scripts/backfill_meteoswiss_history.py` is for the MeteoSwiss backfill).

## Why this is blocking two other plans

- **`cmal_pool_PT`** declares `StaticNaming.CARAVAN`, so all 50 of its statics resolve through the
  `caravan:` prefix. Without the import it has no static inputs at all.
- **Plan 183 T3** compares our recomputed climate indices against `caravan:`-prefixed reference
  values. Probed on the dev box 2026-08-18: both local basins carry **300 attributes and zero
  `caravan:` keys**; the four indices exist only under bare CAMELS-CH names with different semantics
  (`high_prec_freq` 18.41 days/year vs Caravan's ~0.033 fraction). T3 therefore skips every basin.

## What already exists — do not rebuild

`run_operational_caravan_import(path, *, station_store, basin_store, expected_codes,
required_static_names, extractor_version=None, source_dataset_version=None)`.

Three properties the script must respect rather than reimplement:

1. **`expected_codes` and `required_static_names` have no defaults and are rejected when empty.**
   Omitting one is a `TypeError`; `frozenset()` raises `ConfigurationError`. This closes a review
   finding that calling the flexible internal function bare "writes data and returns success without
   validating any station/static".
2. **The gate is manifest-scoped, never parquet-scoped.** The real parquet holds 296 Swiss codes
   against our 148; the surplus lands in `unmatched_codes` and is *never* fatal. Only a manifest
   station that fails to match, bind a basin, or resolve every required static raises.
3. **`require_real_transaction` refuses to issue any write on an AUTOCOMMIT connection**, so the
   script must open a genuine transaction — otherwise a mid-loop gate failure leaves a partial import.

## Owner decisions

| # | Decision | Recommendation |
|---|---|---|
| **D1** | Manifest: derive live from the DB by T0a's rule, or pin the frozen 148 codes as data? | **Derive live, then assert the count is 148 and fail loudly otherwise.** T0a's rule is `discharge` in both `forecast_targets` and `measured_parameters`, excluding lake/`water_level`-only stations, minus **2446 Gampelen-Zihlbrücke** (owner-dropped: the regulated Zihlkanal outflow, a regulation decision rather than a rainfall-runoff response). A pinned list silently diverges from a changing fleet; a live query with no cross-check silently imports a *different* set. Doing both means a drifted fleet stops the import instead of quietly redefining it. |
| **D2** | Required statics: read from the registered model's `data_requirements.static_features`, or pin PT's 50? | **Read from the model.** It is the single source of truth (`types/model.py:272`), and pinning would let the model's contract and the gate drift apart — the exact failure the gate exists to catch. Fail with a clear message if the model is not registered. |
| **D3** | A `--dry-run` that runs the full gate and reports without committing? | **Yes.** The gate is all-or-nothing and the first real run is against production data on the mini. A rehearsal that rolls back turns "did it work" into a question answered before writing, not after. |
| **D4** | Import now with an unconfirmed release, or wait for Sandro (~29 Aug)? | **Now.** Provenance is *not persisted* (Plan 155's deferred deviation), so the version string writes no durable record. The real constraint is `merge_namespaced_attributes`: identical replay is a no-op success, a *changed* value raises per key. So a corrected parquet later costs a deliberate cleanup, not silent corruption — and two blocked plans are worth more than that risk. |

## Tasks

### T1 — the operator script
`scripts/import_caravan_attributes.py`, mirroring `scripts/backfill_meteoswiss_history.py`: build the
engine, run migrations, open a real transaction, construct `PgStationStore`/`PgBasinStore`, derive
the manifest (D1) and the required statics (D2), call the entrypoint, print
matched/unmatched/`stations_without_basin`/coverage-gap counts, and exit non-zero on the gate.
`--dry-run` (D3) and `--parquet <path>` arguments.

**Acceptance:** against a fixture parquet and a seeded fleet, (a) a clean run reports every manifest
station matched and exits 0; (b) a run with one manifest station missing from the parquet exits
non-zero and *names that station*; (c) `--dry-run` leaves `basins.attributes` byte-identical —
asserted by reading the column back, not by trusting the flag.

### T2 — rename the provenance placeholder
`types/caravan_attributes.py`: `source_dataset_version` default
`"unconfirmed@delivered-2026-08-13"` → `"initial@delivered-2026-08-13"` (owner, 2026-08-18).
"Unconfirmed" reads as a defect to be corrected; this is simply the first delivery. Update the
docstring, which currently tells the reader to treat it as a placeholder pending confirmation.

### T3 — run it on the mini
Stage the parquet on the mac mini (the Swiss fleet is onboarded there — owner-confirmed; the dev box
has 2 stations and cannot exercise this). Dry-run first, then the real import inside a transaction.

**Acceptance:** every one of the 148 manifest stations resolves all of PT's declared statics to finite
values, and a spot-checked basin shows `caravan:`-prefixed keys alongside its existing CAMELS-CH
attributes, with the pre-existing bare-named attributes **unchanged** — the namespace guard means the
merge is structurally incapable of touching them, and this is where that is confirmed against real
data rather than a fixture.

## Follow-on, explicitly not here
Plan 183's T3 parity check becomes runnable once T3 lands. That is the payoff, and it is that plan's
task, not this one's.
