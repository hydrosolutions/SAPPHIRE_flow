# Plan 058 — BAFU LINDAS archive collection (stub)

**Status**: DRAFT (stub)
**Date**: 2026-04-18
**Depends on**: none — orthogonal to the operational forecast cycle.
Relates to Plan 055 T1 (this plan is the follow-up owner of the real
reference-fixture replacement).

---

## Scope (sketch)

Build an append-only archive of real BAFU LINDAS readings for the 7 stations
listed in `tests/fixtures/reference/stations.toml`. Once ≥6 months of data
have accumulated, the archive is promoted to
`tests/fixtures/reference/bafu_observations.parquet` per `docs/v0-scope.md`
§E1, replacing the synthetic placeholder that currently stands in.

The LINDAS adapter (`src/sapphire_flow/adapters/hydro_scraper.py`) binds a
single current-reading subject URI (see `hydro_scraper.py:173-192`) and
cannot retrieve historical windows. A fixture that represents the real
adapter contract therefore requires collecting the *current* reading over
time — this is the scheduled collection pipeline described below.

### Collection pipeline (sketch)

- A scheduled Prefect task polls `adapter.fetch_observations(...)` for the
  7 reference stations at a safe cadence (e.g. hourly).
- Each run appends new rows to an append-only Parquet layout. Candidate
  locations:
  - `tests/fixtures/reference/archive/YYYY/MM/observations-YYYY-MM-DD.parquet`
    (in-repo, partitioned by day);
  - or a separate data repo / artifact store (see open questions).
- Dedup key: `(station_code, timestamp, parameter)`. Re-running the task
  must not double-insert.
- Back-pressure: if the endpoint is unreachable, the task logs and exits
  cleanly — no retries that would spam LINDAS.

### Promotion to the reference fixture

- When the archive spans ≥6 months (per `docs/v0-scope.md` §E1), run a
  promotion step that concatenates the partitioned Parquets into
  `tests/fixtures/reference/bafu_observations.parquet`, filters to the
  intended reference window, and validates schema against the existing
  `tests/unit/adapters/test_reference_dataset.py` tests.
- The Plan 055 README note is reversed at that point from "synthetic by
  design" to "recorded from live BAFU LINDAS" and a recording command /
  promotion script is documented.

### Schema-drift detection

- While the archive accumulates, `test_contract_lindas_response` in
  `tests/integration/adapters/test_hydro_scraper.py` remains the canonical
  guard on the real LINDAS response shape. The archive-collection task
  should fail loudly (log + Prefect failure) if it sees an unexpected
  binding key, rather than silently appending malformed rows.

---

## Open questions (non-blocking; to be resolved when this plan exits DRAFT)

1. **Collection cadence.** Hourly polls produce ~8,760 rows/station/year =
   ~61,000 rows across 7 stations annually. Does hourly match the
   training/hindcast granularity we actually need, or should we sub-sample
   (every 6 h? every 24 h?) to keep the archive lean?
2. **Archive storage location.**
   - In-repo (`tests/fixtures/reference/archive/...`): simplest; no extra
     CI / auth plumbing; but bloats git history over time.
   - Separate data repo (e.g. `SAPPHIRE_flow_data`): keeps main repo lean;
     needs a submodule-or-equivalent story and CI access.
   - Artifact store (S3-compatible, pulled at test time): cleanest for
     large archives but introduces a network dependency in what is
     currently a purely-local test suite.
3. **Retention policy before promotion.** Do we keep the full partitioned
   archive after promotion, or prune to just the promoted window?
4. **CI impact.**
   - If the archive lives in-repo, do we commit every hourly append, or
     batch (daily / weekly commits)?
   - If the archive lives out-of-repo, how do CI jobs obtain a snapshot
     (lazy fetch? nightly rebuild?).
5. **Schema drift during accumulation.** If LINDAS changes a binding key
   mid-accumulation, do we roll forward (new column, backfill nulls) or
   restart the archive from the drift point?
6. **Station selection stability.** If `stations.toml` changes (add/remove
   station) during accumulation, how is the archive kept consistent?
7. **Promotion cutover.** Does Plan 055 T1's README note get reverted by
   this plan at promotion time, or does the promotion script take
   responsibility for the docs update?

---

## Out of scope (for this plan)

- Redesigning the LINDAS adapter to accept historical windows — the endpoint
  does not support it; this is a property of the data source, not the
  adapter.
- NWP (ICON-CH2-EPS) reference fixtures — tracked separately under Phase 3
  v0b.

---

## Task list

Not yet drafted. This is a stub pending resolution of the open questions
above. A future revision will expand the sketch into a concrete task list
(scheduled task implementation, append-only layout, dedup logic, promotion
script, README updates).
