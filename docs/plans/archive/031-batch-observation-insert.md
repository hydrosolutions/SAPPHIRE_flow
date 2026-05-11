# Plan 031 — Batch INSERT for Observation Store

**Status**: READY
**Phase**: 2 (Stores)

## Context

`store_raw_observations()` executes **one INSERT per observation** — 29,220 DB round-trips per station. Batching to multi-row INSERT (like the forcing store already does) reduces this to 6 round-trips per station.

### Measured timings (from 167-station onboarding run, sleep gaps excluded)

| Phase | Active time | Per station | Round-trips/station |
|---|---|---|---|
| **Observation store (current)** | **79 min** | **30.1s** | **29,220** |
| Forcing store (already batched at 5000) | 10 min | 4.0s | 6 |
| QC | 25 min | 10.1s | — |
| Baselines | 0.7 min | 0.3s | — |
| Flow regimes | 0.2 min | 0.1s | — |

Observation writes dominate at 69% of active processing time. The forcing store handles comparable data volumes (29K records/station) in 4s/station using batch INSERT — that's the target pattern.

### Caller analysis (verified)

| Caller | Uses returned IDs? | Order-dependent? |
|---|---|---|
| `flows/ingest_observations.py:115` | `len(ids)` only | No |
| `services/onboarding.py:290` | `len(inserted_ids)` only | No |
| Integration tests (2 cases) | `[oid] = ...` unpacking | Yes — order must match input |
| Unit tests (all) | Ignore return, fetch from store dict | No |

**update_qc is completely independent** — both production callers fetch observation IDs from the DB via `fetch_observations()`, not from `store_raw_observations()` return values.

### Constraint: RETURNING + ON CONFLICT DO NOTHING

With batch INSERT + `ON CONFLICT DO NOTHING`, PostgreSQL's `RETURNING` clause only returns rows that were actually inserted (not duplicates). We pre-generate all IDs, then compare returned IDs against the input set to determine which succeeded.

---

## Risk Assessment

### 1. Intra-batch duplicate natural keys
If two rows in the same batch have the same `(station_id, timestamp, parameter, source)`, PostgreSQL's ON CONFLICT DO NOTHING silently drops the duplicate — same as the row-by-row approach. RETURNING only returns the inserted row. **No semantic change.**

In practice, CAMELS-CH data has unique timestamps per station/parameter, and the LINDAS adapter produces one observation per fetch. Intra-batch duplicates shouldn't occur.

### 2. Batch-level error semantics
If one row violates a CHECK constraint (e.g., `ck_observations_missing_value`), the **entire batch fails** — the current row-by-row approach would only fail that single row.

Mitigating factors:
- `RawObservation.value` is typed as `float` (not `float | None`)
- We always set `qc_status='raw'`, so the `(missing = value IS NULL)` constraint cannot fire
- Both CAMELS-CH and LINDAS adapters filter out NaN values before creating RawObservation

**Risk is negligible**, but this IS a behavioral difference from the row-by-row approach. If a future adapter passes bad data, it would fail a whole batch of 5000 instead of just the offending row.

### 3. FakeObservationStore is NOT changed
Only `PgObservationStore` changes. All unit tests use the fake and are unaffected. Integration tests use the real Pg store and exercise the new batch code.

---

## Tasks

### Task 1: Batch `store_raw_observations` in `PgObservationStore`

Replace the row-by-row loop with batched multi-row INSERT, following the `historical_forcing_store.py:32-53` pattern.

**Before** (lines 49-79):
```python
for raw in obs_list:
    oid = ObservationId(uuid4())
    stmt = pg_insert(observations).values(...).on_conflict_do_nothing(...).returning(observations.c.id)
    result = self._conn.execute(stmt)
    row = result.fetchone()
    if row is not None:
        ids.append(oid)
```

**After**:
```python
_BATCH_SIZE = 5000  # same as historical_forcing_store; 9 columns × 5000 = 45K params, safe under psycopg 65K limit

# Pre-generate all rows with UUIDs
rows = [{...} for raw in obs_list]

# Batch insert with RETURNING
for i in range(0, len(rows), _BATCH_SIZE):
    batch = rows[i : i + _BATCH_SIZE]
    stmt = pg_insert(observations).values(batch).on_conflict_do_nothing(...).returning(observations.c.id)
    returned = {row[0] for row in self._conn.execute(stmt).fetchall()}
    # Append in input order, only IDs that were inserted
    for row in batch:
        if row["id"] in returned:
            ids.append(row["id"])
```

**Contract preserved**: returns `list[ObservationId]` in input order, excluding duplicates.

**Files**: `src/sapphire_flow/store/observation_store.py`

### Task 2: Verify all existing tests pass

Key files to watch:
- `tests/integration/store/test_observation_store.py` — tests that unpack `[oid] = result`
- `tests/unit/flows/test_ingest_observations.py` — uses FakeObservationStore (unchanged)
- `tests/unit/services/test_onboarding.py` — uses FakeObservationStore (unchanged)

No test changes expected — the batch implementation preserves identical semantics.

---

## Dependency Graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["1"]},
    {"id": "phase-2", "tasks": ["2"], "depends_on": ["phase-1"]}
  ]
}
```

## Verification

```bash
uv run pytest tests/integration/store/test_observation_store.py -x -q
uv run ruff check src/sapphire_flow/store/observation_store.py
uv run pytest --tb=short -q
```
