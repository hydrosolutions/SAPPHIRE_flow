---
status: DONE
created: 2026-04-08
scope: bug-fix + hardening — make station onboarding idempotent and re-runnable
depends_on: []
---

# 022 — Idempotent Onboarding

## Problem

Running `scripts/onboard.py` a second time for stations that already exist in the
database produces `Observations: 0` — all historical observation and forcing data is
silently dropped. This blocks the intended workflow of re-running onboarding to ingest
additional data or to update station metadata.

### Root cause

`onboard_from_camelsch()` calls `load_stations()` which mints fresh `StationId(uuid4())`
values on every invocation. `obs_by_station` and `forcing_by_station` are keyed by these
fresh UUIDs. Later, `_run_onboarding()` Step 2 discovers the station already exists in the
DB and resolves `station_map[code] = existing.id` (the DB UUID). Steps 3/4 then check
`if station_id not in resolved_station_ids` — the fresh-UUID keys never match the DB UUIDs,
so all observations and forcing records are silently skipped.

### Secondary issues exposed by investigation

Even after fixing the ID mismatch, several stores are not re-run safe:

| Step | Re-run safe? | Issue |
|---|---|---|
| Basin store | Yes | Skip-if-exists guard works |
| Station store | **Partial** | Skips correctly but metadata changes are silently dropped (no `update_station()`) |
| Raw observations | **No** | `store_raw_observations()` uses bare INSERT; hits `UniqueViolation` on `(station_id, timestamp, parameter, source)` |
| Historical forcing | Yes | `ON CONFLICT DO NOTHING` already |
| Weather sources | Yes | `ON CONFLICT DO UPDATE` already |
| QC | Yes (no-op) | Only processes RAW; already-QC'd obs skipped |
| Baselines | Yes | `ON CONFLICT DO UPDATE` already |
| Flow regimes | **Accumulates** | No uniqueness constraint; `version` is always `1`, so duplicate rows make `fetch_latest()` non-deterministic |
| Model assignment | Yes | `ON CONFLICT DO UPDATE` already |
| Training / artifact | **No** | `onboard_model()` unconditionally retrains; accumulates orphaned TRAINING→SUPERSEDED artifact rows, cascading hindcast + skill duplication |
| Hindcasts | **No** | No uniqueness constraint; duplicates accumulate and corrupt skill computation |
| Skill scores | **Latent bug** | `fetch_latest_scores()` uses `MAX(computation_version)` but version is always `1`; returns merged scores from ALL artifacts, not just current ACTIVE. `fetch_skill_scores(model_id, artifact_id)` (used by skill gate) is unaffected. |

### Test gaps

- No test for calling `_run_onboarding()` twice with the same data.
- `FakeObservationStore.store_raw_observations` always appends (no dedup), diverging from
  production's unique constraint behaviour.
- No integration test for the full onboarding pipeline against Postgres.

## Scope

Fix the critical bug and harden the onboarding path so that re-running with the same
(or extended) data produces the same end state without errors or silent data loss.

### What to implement

Structured in tiers. Each tier is independently shippable and testable.

---

### Tier 1 — Fix the immediate bug (critical)

**1a. Resolve station IDs before loading observations** (`onboarding.py`)

In `onboard_from_camelsch()`, after `load_stations()` and before `load_observations()`,
look up existing DB station IDs via `station_store.fetch_station_by_code()` and patch the
`station_map` so that observations and forcing are keyed by the correct (DB or fresh) IDs
from the start. No downstream remap needed.

```
stations, basins = load_stations(data_dir, clock, basin_ids)

station_map: dict[str, StationId] = {}
for s in stations:
    existing = station_store.fetch_station_by_code(s.code, s.network)
    station_map[s.code] = existing.id if existing is not None else s.id

obs_by_station = load_observations(data_dir, station_map, ...)
forcing_by_station = load_forcing(data_dir, station_map, ...)
```

**1b. Make `store_raw_observations()` idempotent** (`observation_store.py`)

Change bare `sa.insert()` to `pg_insert().on_conflict_do_nothing()` on the existing unique
index `uq_observations_natural_key` — `(station_id, timestamp, parameter, source)`.

Must specify `index_elements=["station_id", "timestamp", "parameter", "source"]` explicitly
(not bare `on_conflict_do_nothing()`) to avoid silently swallowing conflicts on any future
unique constraint added to the table. `store_observations()` already specifies
`index_elements` (with `on_conflict_do_update`); `historical_forcing_store.py` uses bare
`on_conflict_do_nothing()` without `index_elements` — prefer the explicit form.

Return value: only return `ObservationId` values for actually-inserted rows. The only
production caller (`onboarding.py:279`) currently discards the return value and uses
`len(raw_obs)` — that must change to `len(obs_store.store_raw_observations(raw_obs))` so
that `OnboardingResult.observations_imported` reflects actual inserts, not input count. This
is required for the Tier 1d test assertion (`observations_imported=0` on second pass) to hold.

Rationale: historical observation data is immutable. If the natural key matches, the value
is the same. New observations (extended date range) are inserted normally. Already-QC'd
observations are untouched — Step 5 only fetches `qc_status=RAW`.

In `_run_onboarding()`, after calling `store_raw_observations()`, compare input count vs
returned IDs. If any rows were skipped, emit a DEBUG-level `observation.duplicate_skipped`
log event with `station_id` and `skipped_count` as inline keyword arguments (stores must not
call `bind_contextvars` — per `docs/standards/logging.md` rule 6, services inherit context
from the calling flow).

**Protocol contract change:** Update `docs/spec/types-and-protocols.md`
`store_raw_observations()` return semantics from "Returns assigned IDs" to "Returns IDs of
newly inserted rows; rows matching an existing natural key `(station_id, timestamp,
parameter, source)` are silently skipped." This is a breaking change to the
`ObservationStore` Protocol contract. Safe to make because the only production caller
(`onboarding.py:279`) currently discards the return value — but must be updated atomically
with the implementation change.

**1c. Update `FakeObservationStore`** (`tests/fakes/`)

Make `FakeObservationStore.store_raw_observations` skip duplicates by natural key
`(station_id, timestamp, parameter, source)`, matching production behaviour.

**1d. Add re-run idempotency test** (`tests/unit/services/test_onboarding.py`)

Test that calls `_run_onboarding()` twice with the same stations/observations and asserts:
- Second pass: `stations_skipped=N`, `observations_imported=0` (all skipped by natural key),
  zero errors, QC counts unchanged, baselines/flow-regimes not duplicated.

**1e. Add store-level duplicate-insert test** (`tests/integration/store/test_observation_store.py`)

Call `store_raw_observations()` twice with the same natural-key data against Postgres.
Assert: second call returns an empty list (no new IDs), total row count unchanged. This
validates `on_conflict_do_nothing` at the DB level — the fake-based Tier 1d test cannot
cover this.

---

### Tier 2 — Station metadata update (important)

**2a. Add `update_station()` to Protocol and implementations**

- Protocol: `StationStore.update_station(station: StationConfig) -> None`
- `PgStationStore`: `UPDATE stations SET name, location, measured_parameters, forecast_targets WHERE id = :id`. Must NOT update `id`, `code`, or `network` (identity fields). Fields present in the DB but absent in the new `StationConfig` are left unchanged (no zeroing-out).
- `FakeStationStore`: overwrite by ID in the dict (same field semantics)
- Update `docs/spec/types-and-protocols.md` to add `update_station()` to the `StationStore` Protocol definition.

**2b. Use update in `_run_onboarding()` Step 2**

When station exists: call `update_station()` to refresh metadata (name, coordinates,
measured parameters, forecast targets) instead of silently skipping. Emit a
`station.metadata_updated` INFO log event with `station_id`, `code`, `network`, and
`duration_ms=round(duration_ms, 1)` as **inline keyword arguments** to `log.info()` — do
NOT call `bind_contextvars()` inside the service (per `docs/standards/logging.md` rule 6:
services inherit context from the calling flow; D6: `duration_ms` is mandatory on
operational INFO events; must be rounded to one decimal place per logging standard). Include
`stations_updated` in the summary `log.info("onboarding_flow_complete", ...)` event.

Note: adjacent existing log events in `_run_onboarding()` (`station_already_exists`,
`basin_stored`, etc.) do not follow the `{entity}.{action}` naming convention from
`docs/standards/logging.md`. Fixing pre-existing event names is out of scope for this plan.

Add `stations_updated: int = 0` field to `OnboardingResult` (default required because the
frozen dataclass uses `slots=True` — a field without default breaks existing instantiation
sites). Add `OnboardingResult` to `docs/spec/types-and-protocols.md` with all fields — it
is currently absent from the spec despite existing in code since Phase 5.

---

### Tier 3 — Model re-training safety (important for repeated onboarding)

**3a. Skip training when ACTIVE artifact exists** (`onboarding.py` Step 7)

Before triggering training, check if an ACTIVE artifact already exists for
`(station_id, model_id)` via `ModelArtifactStore.fetch_active_artifact_for_station(
station_id, model_id)` — returns `tuple[ArtifactId, bytes] | None`, so a simple `is not
None` check suffices. This is more direct than `fetch_artifacts_by_status()` (single call,
no list-emptiness check). If an ACTIVE artifact exists, skip training entirely — the
existing model is still valid.

Note: the original analysis claimed that a second ACTIVE artifact would violate the
partial unique index `ix_model_artifacts_station_model_active`. This was incorrect —
`promote_artifact()` in `training.py` already supersedes the existing ACTIVE artifact
before activating the new one. The actual problem is that unconditional retraining
produces orphaned TRAINING→SUPERSEDED rows, wasted compute, and cascading hindcast +
skill score duplication. Guarding at the training decision point prevents the entire
cascade.

**Important:** This means re-running onboarding will *not* retrain models, even if
additional observation data has been ingested. Deliberate retraining after data extension
requires Flow 9 (model retraining), not re-running onboarding.

**3b. Skip hindcast + skill when training is skipped**

When 3a skips training (ACTIVE artifact exists), also skip hindcast generation and skill
computation — no new artifact means existing hindcasts and skill scores remain valid.
No `delete_hindcasts()` method is needed.

**Step 8 (station status) is unaffected by training skip.** Step 8 queries artifact state
independently via `fetch_artifacts_by_status(..., ACTIVE, station_id)` — it finds the
pre-existing ACTIVE artifact and correctly marks the station OPERATIONAL even when Step 7
was skipped. No code change needed in Step 8 for Tier 3.

**Counter note:** `stations_marked_operational` over-counts on re-run — it increments
whenever `update_station_status(OPERATIONAL)` succeeds, including for already-OPERATIONAL
stations. This is a reporting cosmetic, not a correctness bug. Acceptable for v0.

---

### Tier 4 — Minor accumulation (low priority)

**4a. Flow regime accumulation**: The `version` parameter is always `1` in
`_run_onboarding`, so duplicate rows are inserted with the same version. `fetch_latest()`
uses `ORDER BY version DESC` — with multiple `version=1` rows, the result is
non-deterministic.

**Risk assessment:** If onboarding is re-run with the *same* observation data, flow regimes
produce identical values and the non-determinism is harmless. However, the plan's stated
use case — "re-running onboarding to ingest additional data" — means p50/p90 boundaries
will legitimately change with extended data. Multiple `version=1` rows with *different*
threshold values make `fetch_latest()` semantically incorrect, not merely duplicated.

Acceptable for initial ship because flow regimes are not yet consumed by any alerting or
API path, but **must be resolved before Flow 1 (forecast cycle) goes live** — otherwise
alert threshold lookups become non-deterministic. Fix: add a `DELETE WHERE station_id=:sid
AND parameter=:param` before insert, or add a unique constraint and use `ON CONFLICT DO
UPDATE`. **Note:** the unique-constraint option requires an Alembic migration (additive —
a new constraint on the existing `flow_regime_configs` table).

**4b. `fetch_latest_scores()` latent bug**: `fetch_latest_scores(station_id, model_id)`
queries by `MAX(computation_version)`, but `computation_version` is hardcoded to `1`. On
re-runs with new artifacts, it returns merged scores from ALL artifacts — not just the
current ACTIVE one. The skill gate (`evaluate_skill_gate()`) is unaffected because it uses
`fetch_skill_scores(model_id, artifact_id)` which filters by artifact ID. The fix is to
add a `model_artifact_id` filter to `fetch_latest_scores()`, or to resolve the latest
ACTIVE artifact and pass it. Not blocking because the method is currently unused, but must
be fixed before any API or dashboard consumer calls it.

### What NOT to implement

- No changes to the adapter layer (`camelsch_adapter.py`) — `load_stations()` remains a
  pure data-loading function with no DB access. ID resolution stays in the orchestration
  layer.
- No schema migrations in Tier 1 — `store_raw_observations` already has the required
  unique index.
- No structural changes to the Prefect flow entry point (`flows/onboard.py`) — it delegates
  to `onboard_from_camelsch()` which gets the fix. Only change: include `stations_updated`
  in the summary log event (Tier 2b).
- No retroactive cleanup of accumulated flow regime or hindcast rows from prior runs.

## Design notes

### Why resolve IDs in `onboard_from_camelsch()`, not in `_run_onboarding()`?

`_run_onboarding()` is the generic orchestration function — it should not need to know that
its caller might have built dicts with wrong keys. Fixing the key mismatch at the source
(before dicts are constructed) is cleaner than remapping after the fact.

**Accepted redundancy:** After Tier 1a, each station is looked up via
`fetch_station_by_code()` twice per run — once in `onboard_from_camelsch()` (for ID
resolution before dict construction) and once in `_run_onboarding()` Step 2 (for
create-vs-skip logic). Both lookups are necessary: the first ensures `obs_by_station` /
`forcing_by_station` are keyed correctly; the second decides whether to insert or skip the
station. Merging them would couple `_run_onboarding()` to caller-specific ID resolution
concerns. For v0 with ~1000 stations, the extra queries are negligible.

### Why `ON CONFLICT DO NOTHING` for observations, not `DO UPDATE`?

Historical observation records are immutable by definition — the value, timestamp, and
source are fixed at import time. If the natural key matches, the existing row is correct.
`DO UPDATE` would add unnecessary write amplification for identical data. If a future
use case requires correcting historical values, that should be a distinct "correction"
workflow with audit trail, not a silent overwrite.

### v0 simplification: detect-then-branch vs. architecture's upsert

`architecture-context.md` Step 5.1 states station registration is "Idempotent (upsert on
`code`)". This plan uses detect-then-branch (`fetch_station_by_code()` → insert or update)
rather than a true `INSERT ... ON CONFLICT DO UPDATE` upsert. This is an accepted v0
simplification — it is simpler to implement and sufficient for single-threaded,
operator-initiated onboarding.

### TOCTOU race on station fetch + insert

`onboard_from_camelsch()` calls `fetch_station_by_code()` then later `_run_onboarding()`
calls it again in Step 2. Under concurrent onboarding runs this is a race condition.
Acceptable for v0 — onboarding is single-threaded and operator-initiated. For v1, the
station store should use `INSERT ... ON CONFLICT DO UPDATE` directly (matching the
architecture spec) and add a `concurrency("observation_write:{station_id}")` guard to
prevent overlap with Flow 2 (observation ingest).

### Impact on existing data

- **No existing data is modified or deleted.** Tier 1 only adds conflict-skip behaviour.
- **QC results are preserved.** Step 5 fetches only `RAW` observations. Already-QC'd
  records (from a prior successful run) are not re-processed.
- **Baselines are recomputed idempotently.** `store_baselines()` uses `ON CONFLICT DO
  UPDATE` — recomputing from the same QC-passed data produces identical values.

## Dependency graph

```
Tier 1a ──┬── Tier 1b ──┬── Tier 1c ──── Tier 1d
          │             │
          │             └── Tier 1e (integration test)
          │
          ├── Tier 2a ──── Tier 2b
          │
          └── Tier 3a ──── Tier 3b
```

Tier 1a (ID resolution) is the prerequisite for everything. Tiers 1b-1e, 2, and 3 can
proceed in parallel — they are independent branches. Tier 3a's training-skip guard uses
`ModelArtifactStore.fetch_active_artifact_for_station()`, which has no coupling to Tier 2's
`update_station()` functionality.

## Files affected

| File | Change |
|---|---|
| `src/sapphire_flow/services/onboarding.py` | Tier 1a: resolve IDs before obs loading. Tier 1b: use return value for `observations_imported` counter. Tier 2b: update existing stations + log event. Tier 3a: skip training when ACTIVE artifact exists. |
| `src/sapphire_flow/store/observation_store.py` | Tier 1b: `on_conflict_do_nothing(index_elements=[...])` in `store_raw_observations()`. |
| `src/sapphire_flow/protocols/stores.py` | Tier 2a: add `update_station()` to `StationStore` Protocol |
| `src/sapphire_flow/store/station_store.py` | Tier 2a: implement `update_station()` (update name, location, measured_parameters, forecast_targets; preserve id, code, network) |
| `tests/fakes/fake_stores.py` | Tier 1c: dedup in fake obs store. Tier 2a: implement `update_station()` in fake. |
| `tests/unit/services/test_onboarding.py` | Tier 1d: re-run idempotency test |
| `tests/integration/store/test_observation_store.py` | Tier 1e: duplicate-insert integration test against Postgres |
| `src/sapphire_flow/types/onboarding.py` | Tier 2b: add `stations_updated: int = 0` field |
| `src/sapphire_flow/flows/onboard.py` | Tier 2b: include `stations_updated` in summary log event |
| `docs/spec/types-and-protocols.md` | Tier 1b: update `store_raw_observations()` docstring (return semantics for conflict-skip). Tier 2a: add `update_station()` to `StationStore` Protocol. Tier 2b: document `OnboardingResult` with all fields. |
