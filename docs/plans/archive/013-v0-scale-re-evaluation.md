---
status: RESOLVED
created: 2026-03-30
revised: 2026-03-31
reviewed: >
  2026-03-31 (critical review pass 2: fixed rounding propagation 1.3–2.5→1.2–2.4 GB/day,
  added DECISION/BENCHMARK output format per Task 2 item, added Task 3→Task 4 and
  Task 3→Task 5 dependencies, moved line 606 timing from Task 1 to Task 2, added
  v0-scope.md line 175 and logging.md line 337 to affected files, added DLQ health
  endpoint JSON to Task 5 chain reaction, sharpened SMN co-location constraint, fixed
  database-schema.md line 357→358–359 citation)
  2026-03-31 (critical review pass 3: fixed stale fill-time ~400–770→~415–830 days in
  Affected Files line 2721 note, fixed flow2 line 383→384 for hot→cold lifecycle ref,
  added line 2696 citation for DLQ in health endpoint JSON, added → DECISION markers
  to 4 lower-priority Task 2 items per scope contract, added Task 1→Task 2 dependency
  for line 2721 disk sizing, added A1↔SMN sensitivity note conditioning partitioning
  volumes on co-location outcome)
  2026-03-31 (critical review pass 4: fixed v0-scope.md §I line numbers 485–486→495–496
  in Affected Files and Task 5, fixed database-schema.md retention lines 358–359→357–358
  and notified_at NULL note 357→356, fixed flow2 hot→cold lifecycle line 384→383,
  fixed A2 hot-tier volume attribution "of forecast_values"→"of total DB storage",
  fixed ConcurrentTaskRunner asyncio attribution — marked as Prefect 3 knowledge not
  orchestration.md content, expanded in-process execution BENCHMARK scope to cover
  runner selection and async def requirements, added orchestration.md update as
  deliverable, added A2 SMN conditioning clause parallel to A1, marked line 496
  (rating curve nullable column) as second false positive alongside line 479,
  added Task 1↔Task 5 coordination note for arch-context line 2694 health JSON,
  resolved DECISION-or-BENCHMARK ambiguity — A1→DECISION conditioned on SMN,
  onboarding timing→DECISION, noted Task 1 A6 line 100 edit conditional on Task 2,
  added missing Affected Files: orchestration.md line 57 single-pool claim,
  logging.md lines 149 and 326 for fan-out log concerns, flow2 line 116 for ~170
  BAFU station bound, moved line 496 from main Affected Files bullets to false
  positives block to prevent contradiction with Task 5)
  2026-03-31 (critical review pass 5: verified against Prefect 3.6.23 —
  ConcurrentTaskRunner is a backwards-compat alias for ThreadPoolTaskRunner (same
  class), not an asyncio-based runner. Rewrote in-process execution section: removed
  false async-vs-threads framing, documented default max_workers=sys.maxsize as the
  key scaling risk at 1000-station fan-out, identified async with concurrency() in
  orchestration.md line 167 as a sync/async bug, added max_workers tuning as primary
  BENCHMARK lever, fixed Task 3 ConcurrentTaskRunner-vs-ThreadPoolTaskRunner comparison
  → max_workers tuning, merged duplicate orchestration.md line 57 Affected Files entry
  into lines 56–57 entry, added orchestration.md line 167 async with bug to Affected
  Files, added logging.md line 187 ThreadPoolTaskRunner contextvars isolation to
  Affected Files, expanded deliverables to include sync/async concurrency() fix and
  ConcurrentTaskRunner alias cleanup)
scope: >
  documentation sweep — update ~50 station references to ~1000, re-evaluate
  affected rationales and performance budgets. Re-evaluations that produce
  architectural decisions (e.g. advancing A1 partitioning, requiring chunked
  fan-out) must yield an inline DECISION marker in the updated doc or a new
  follow-on plan if implementation work is required. Questions requiring
  benchmarking are out of scope — document the uncertainty and flag as
  requiring a follow-on benchmarking plan.
depends_on: []
---

# 013 — v0 Scale Re-evaluation (~50 → ~1000 Stations)

## Problem

Multiple docs reference "~50 stations" as the v0 scale target. This is outdated —
v0 starts smaller but must scale to ~1000 stations with sub-daily data for planned
large-scale experiments. The "~50 stations" figure drives simplification rationales,
performance budgets, and "acceptable at v0 scale" justifications that need re-evaluation.

## Affected Files

### `docs/v0-scope.md`

- Line 9: `~50 stations, single VM` (guiding principles)
- Line 34: `Manual supervision suffices at ~50 stations` (Flow 4 deferral rationale)
- Line 50: `~50 stations with daily forecasts produce a few GB/year — negligible` (A1 partitioning rationale)
- Line 58: `v0 data fits in a few GB` (A2 data retention rationale)
- Line 259: `Target: full forecast cycle for 50 stations in < 60 seconds` (§D header)
- Line 267: `50 stations x 21 members x 120 timesteps = 126K rows` (D2 batch writes)
- Line 295: `Target per-step budgets (50 stations):` (D6 table header)
- Line 495: `small data` (§I "Not risks" table — partitioning)

- Line 100: `At v0 scale, resource isolation is unnecessary` (A6 simplification rationale)
- Line 175: `sufficient for v0 scale` (A12 SMN forcing rationale — parallel to
  architecture-context.md line 135; both must be updated together)

**False positives to skip**:
- Line 328 (`~50-100 MB in tests/fixtures/reference/`) refers to fixture file size, not station count.
- Line 479 (`safe migration on small data` in §I3) refers to adding a nullable `alert_priority` column — not station-count-dependent. The "small data" qualifier here refers to the column addition being metadata-only in PostgreSQL regardless of row count.
- Line 496 (`Nullable column addition` in §I "Not risks" table — rating curve columns) — same property as line 479: nullable column additions are metadata-only in PostgreSQL regardless of row count. Not station-count-dependent.

### `docs/architecture-context.md`

- Line 10: `Runs on Docker Compose on a single VM` (intro)
- Line 135: `sufficient for v0 scale (~50 Swiss stations)` (SMN forcing source rationale)
- Line 606: `~10 seconds for 50 stations` (onboarding timing estimate)
- Line 1505: `A group-scoped ML model assigned via model_assignments to 50 stations = 50 rows`
  — concrete "50 stations" example in group-model assignment explanation; minor prose update
- Line 2694: `"stations_total": 42` in health endpoint JSON example — hardcoded small-fleet
  value in sample API output. **Also**: same JSON block contains `"dead_letter_queue"`
  status field (line 2696) — relevant if A1 partitioning is advanced and DLQ
  re-introduced (see Task 5 DLQ chain reaction)
- Line 2720: `500-station` worked example (1.5 GB/day) — not 50, but underscaled vs ~1000
  target. **NB**: The worked example's own arithmetic is inconsistent — 500 × 21 × 4 × 120
  × 60 bytes ≈ 302 MB/day, not 1.5 GB/day; the "1.5 GB" figure includes "Parquet cold storage
  and model artifacts" per its own qualifier. Any extrapolation from this figure must be
  re-derived from first principles, not linearly scaled.
- Line 2721: `Minimum disk: 1 TB SSD recommended for v1` — sized for the 500-station worked
  example; at ~1000 stations with single-parameter forecasting this fills in ~415–830 days
  depending on PostgreSQL overhead assumptions (1.2–2.4 GB/day — see Task 2); needs
  re-examination

### `docs/spec/database-schema.md`

- Line 10: `~50 stations, single VM`
- Line 11: `no cold storage` (schema-level anchor for A2 deferral)
- Lines 111, 132, 228: inline `v0: not partitioned` comments per domain (observations,
  weather/NWP, forecasts)
- Lines 253, 276: `denorm — not partitioned in v0` column comments on `forecast_values.issued_at`
  and `hindcast_values.hindcast_step` (partition-readiness markers)
- Lines 357–358: `pipeline_health` 30-day / `alerts` 90-day retention — growth rate increases ~20×
  at 1000 stations (line 356 is the `notified_at` NULL note; retention text spans 357–358)
- Line 430: DLQ deferral rationale: `No partitioning = no DLQ needed` — if A1 (partitioning) is
  advanced, this rationale collapses and DLQ must be re-evaluated simultaneously

### `docs/design/v0-flow2-observation-pipeline.md`

- Line 61: `~10–50 stations` (v0 station selection range)
- Line 385: `query-time aggregation is viable at v0 scale (~50 stations, 2.6M rows/year)`
- Line 387: `completes in <1 second for 50 stations with existing indexes`
- Line 559: `expand to ~50 for validation` (§9 open question — station selection guidance)
- Lines 389–395: v1 benchmark `500 stations, 39M rows, <5s` — already stale vs 1000-station
  target; at 1000 stations → ~78M rows in hot window
- Lines 397–399: implementation trigger `when sub-daily operational data arrives` — at 1000
  stations with sub-daily, this is day 1, not a deferred milestone
- Line 383: hot→cold lifecycle reference presupposes A2 resolution; inconsistent if A2 stays
  deferred

### `docs/standards/cicd.md`

- Line 9: `Single VM deployment`
- Lines 72–74: work pool resource limits (`mem_limit: 4g, cpus: 2.0` for ops) — these
  are **v1-only** (cicd.md line 66: v0 uses a single `default` work pool per §A6). v0's
  single worker container has no declared per-pool limits. However, the v1 defaults
  inform future sizing and all limits are deployment-configurable (line 76).
- Line 141: Prefect DB log retention 30 days — Prefect DB grows ~20× faster at 1000 stations;
  disk impact not addressed elsewhere
- Line 125: `max-size: 50m, max-file: 5` per container (250 MB cap) — at 20× more log
  volume, logs rotate much faster and recent diagnostic history is lost during incident
  response. This is an operational configuration issue, not just prose — likely requires
  increasing `max-file` or `max-size` for the worker container.
- Line 145: `~100 MB/day` log volume estimate — implicitly assumes small station count;
  at 1000 stations each fan-out emits ~20× more log lines. The interaction with the
  static `max-size`/`max-file` cap (line 125) means this is not merely a prose fix.

### `docs/standards/logging.md`

- Line 337: `Ephemeral (50 MB x 5 files per container)` — mirrors cicd.md line 125.
  If the worker container log cap is increased per Task 2 recommendations, this line
  must be updated in lockstep. Downstream dependency of cicd.md log cap changes.

### `docs/standards/orchestration.md`

- Lines 56–57: in-process task runner constraint (`ThreadPoolTaskRunner`; note:
  `ConcurrentTaskRunner` is a backwards-compatibility alias for the same class in
  Prefect 3.6) — SQLAlchemy connections are not pickle-serializable, locking v0 into
  in-process execution. At 1000 stations, `task.map()` creates ~1000 concurrent OS
  threads (default `max_workers` is unbounded) with unexamined memory, thread limit,
  and connection pool pressure. Line 57 also contains `v0 uses a single work pool
  with in-process execution` — scale-dependent claim that must be updated alongside
  A6 references in other files.
- Line 171: named Prefect concurrency slots (`observation_write:{station_id}`) — 1000 active
  named slots during reprocessing events are untested at this scale
- Lines 48–49: existing mitigation for high fan-out (remove inner `@task` decorators at
  "hundreds+" invocations) — Task 3 should reference this rather than re-derive
- Lines 100–117: training fan-out across `(station, model)` pairs — at 1000 stations with
  multiple model types, this means thousands of concurrent in-process tasks within one process
  under v0's single-pool in-process runner. Memory and connection pool pressure unexamined.
- Lines 164–169: `concurrency("db_bulk_write", occupy=1)` guard — at 1000 stations, all
  `forecast_station.map()` tasks converge on this single named slot, creating a write-throughput
  serialization bottleneck. **This is at least as architecturally significant as the
  ThreadPoolTaskRunner gap** — it directly limits how fast bulk writes can proceed regardless
  of available parallelism.
- Line 167: `async with concurrency("db_bulk_write", occupy=1)` — uses the async
  variant of Prefect's concurrency context manager. This only works in `async def` task
  bodies; sync tasks need `from prefect.concurrency.sync import concurrency` with plain
  `with`. This is an existing bug/ambiguity that must be resolved as part of the runner
  documentation deliverable (see Task 2).

### `docs/standards/logging.md` (additional)

- Line 326: `log_prints=False` directive for high-fan-out `task.map()` patterns —
  at 1000 stations, omitting this setting causes Prefect to capture stdout from every
  mapped task, multiplying Prefect DB writes by ~1000. Directly compounds the Prefect
  DB retention concern (cicd.md line 141).
- Line 149: `10K+ events/day` DEBUG warning — calibrated for a small fleet; at 1000
  stations with sub-daily ingest, DEBUG volume is dramatically higher. Worth
  re-calibrating the warning text.
- Line 187: `ThreadPoolTaskRunner copies contextvars per submission` — documents
  structlog context isolation for thread-based fan-out. This is the only runner-specific
  logging guarantee in the codebase; relevant to the Task 2 runner documentation
  deliverable.

### `docs/design/v0-flow2-observation-pipeline.md` (additional)

- Line 117: `~170 automated BAFU stations have real-time telemetry` — provides an
  actual upper bound on Swiss v0 station count from the LINDAS source perspective.
  Relevant to the SMN co-location constraint discussion (Task 2) as a complementary
  bound.

## Tasks

### Task 1 — Update station count references

**Depends on**: Task 2 (architecture-context.md line 2721 disk sizing annotation
requires the corrected daily growth figures from the A1 re-derivation).

Update all station count references to reflect the scale range (starting smaller,
scaling to ~1000). Ensure "single VM" references across all four files (v0-scope,
architecture-context, database-schema, cicd) are treated consistently.

Includes minor prose updates that don't require re-evaluation:
- architecture-context.md line 1505 (50 stations group-model example → 1000)
- architecture-context.md line 2694 (`"stations_total": 42` → update sample JSON).
  **Coordination**: Task 5 may also edit this JSON block (DLQ field) if A1 partitioning
  is advanced — edits must not clobber each other. Sequence or coordinate with Task 5.
- database-schema.md lines 357–358 (note 20× retention growth rate in comments)
- v0-scope.md line 100 (A6 "At v0 scale" qualifier — **conditional** on Task 2's A6
  DECISION: if pool separation is advanced, this line needs replacement, not just a
  scale-qualifier update)
- v0-flow2-observation-pipeline.md line 559 (`expand to ~50 for validation`)

### Task 2 — Re-evaluate scale-dependent simplification rationales

Re-evaluate simplifications whose rationales depend on small scale. Per the scope
rule, each item below must produce one of:
- **→ DECISION** (inline marker in the updated doc), or
- **→ BENCHMARK** (flag as requiring a follow-on benchmarking plan, with stated
  assumptions and uncertainty range documented inline).

- **A1 (no partitioning)**: ~1000 stations × sub-daily × multiple parameters may
  produce significantly more than "a few GB/year." Data volume estimates at 1000
  stations with 4 cycles/day:

  **Parameter count clarification**: §A13 (v0-scope.md line 181) says "v0 exercises
  this with discharge (river) and water_level (lake) forecasting." River stations
  forecast discharge; lake stations (33 in CAMELS-CH) forecast water_level. They are
  not both produced per station. The estimates below use **1 forecast parameter per
  station** as the baseline. If a future requirement adds dual-parameter forecasting
  per station (e.g. Nepal v1: discharge + water_level stage), multiply by 2.

  `observations` (10-minute frequency, 144 obs/station/day):
  - 1 parameter: 1000 × 144 × 365 = ~52.6M rows/year
  - Some stations may report 2+ parameters (e.g. discharge + water_level); assume
    ~1.2 parameters/station average → ~63M rows/year upper bound

  `forecast_values` (21 members × 120 timesteps per cycle):
  - **Baseline (1 param/station)**: 1000 × 21 × 120 × 4 × 365 = **~3.7B rows/year**
  - Dual-parameter upper bound: ~7.4B rows/year (not expected in v0)

  Daily write volume — re-derived from first principles, not the broken worked
  example in architecture-context.md line 2720 (see Affected Files note above):
  - `forecast_values` raw (1 param): 1000 × 21 × 120 × 4 cycles × ~60 bytes/row
    ≈ **0.6 GB/day raw rows**
  - PostgreSQL on-disk (heap overhead, indexes, WAL): ~2–4× raw ≈ **1.2–2.4 GB/day
    total storage growth**
  - `observations` raw: under 50 MB/day (negligible vs forecasts)

  Re-assess whether partitioning is still safely deferred. → DECISION (conditioned
  on SMN co-location outcome — the volume analysis is arithmetic, not measurement;
  if volumes exceed ~1 GB/day the deferral needs revision, if SMN bounds the fleet
  at ~300 stations the deferral is defensible).

  **Sensitivity**: The 1000-station figure is unconditional here, but the SMN
  co-location analysis (below) may conclude Swiss-only v0 is bounded at far fewer
  stations. If the co-located pair count is e.g. ~300, these volumes drop ~3× and the
  partitioning deferral becomes much more defensible. The A1 conclusion should be
  conditioned on the SMN DECISION outcome.

  **Dependency**: If partitioning is advanced, the DLQ deferral (database-schema.md
  line 430: "No partitioning = no DLQ needed") must be re-evaluated simultaneously.

- **A2 (no tiered data retention)**: Same "few GB" assumption as A1. At the volumes
  above, "everything stays in PostgreSQL" with no archival may become untenable within
  the first 1–2 operational years. Note: even after lifting A2, the full design's hot
  window is 548 days (architecture-context line 2595 for forecast_values) — the hot tier
  alone will hold ~0.64–1.28 TB of total DB storage before cold archival begins (1.2–2.4
  GB/day × 548 days). Re-assess whether hot→cold archival needs to move earlier, and
  whether the 548-day hot window itself needs revisiting. If A2 stays deferred,
  update flow2 line 383 to remove or qualify the hot→cold lifecycle reference.
  → DECISION.

  **Sensitivity**: Like A1, the A2 conclusion should be conditioned on the SMN
  DECISION outcome — the same volume figures are used, and if the co-located pair
  count bounds Swiss-only v0 at ~300 stations, A2's retention concern drops ~3×
  (0.4–0.8 GB/day), making the deferral much more defensible.

- **A6 (single Prefect work pool)**: "At v0 scale, resource isolation is unnecessary"
  — at 1000 stations, training (Flow 6→7→8) across all stations becomes a heavier
  concurrent workload that could starve the operational forecast cycle. Re-assess
  whether pool separation needs to move earlier. Note: if pool separation is advanced,
  orchestration.md lines 125–127 (cross-pool submission patterns, currently v1-only)
  and pool-level concurrency limits (v1 `ops` pool: concurrency 4, cicd.md line 72)
  also become relevant to v0. → DECISION.

- **Flow 4 deferral**: Manual supervision at ~1000 stations is less credible than
  at ~50. Re-assess timeline. → DECISION.

- **D2 batch write budgets**: 1000 stations × 21 members × 120 timesteps = **2.52M
  rows per forecast cycle** (1 param/station baseline; not 126K). Verify COPY
  performance at this scale. → BENCHMARK (COPY at 2.52M rows is untested).

- **Flow 2 query-time aggregation**: The flow2 pipeline doc claims `<1 second for
  50 stations` and `2.6M rows/year`. At 1000 stations this becomes ~52–63M rows/year
  (1–1.2 parameters/station average). The doc frames pre-computed daily aggregates as
  a v1 necessity, but at ~1000 stations it may become a v0b necessity. The `<1 second`
  claim depends on "existing indexes" which are defined only in architecture-context.md
  — verify index strategy still holds. Also note the flow2 v1 benchmark (lines 389–395:
  500 stations, 39M rows) is already stale vs the 1000-station target and must be
  updated alongside the v0 figures. → BENCHMARK (index strategy at 52–63M rows
  untested; flow2 doc's `<1 second` claim needs verification).

- **SMN forcing source** (architecture-context line 135, v0-scope.md line 175):
  "sufficient for v0 scale (~50 Swiss stations)." The SMN network has ~140 total
  automatic stations (external knowledge — not documented in the repo; needs
  verification and citation). However, the binding constraint is not total SMN
  station count but rather how many BAFU river gauges have co-located SMN weather
  stations with sufficient hourly history (1981–present) for ML training — this
  number is likely substantially smaller than 140. If v0 targets 1000 river gauges,
  there are not enough co-located SMN weather stations for all of them. This is a
  forcing architecture question, not just a text update — clarify whether "1000
  stations" includes non-Swiss deployments (where SMN is irrelevant) or whether the
  Swiss-only v0 is actually bounded by available co-located pairs. → DECISION.

- **In-process execution at 1000 stations** (orchestration.md line 57): v0 uses
  `ThreadPoolTaskRunner` (OS threads) because SQLAlchemy connections are not
  pickle-serializable, ruling out subprocess/distributed runners. `task.map()` across
  1000 stations creates ~1000 concurrent OS threads within one process.

  **Runner clarification** (verified against Prefect 3.6.23): `ConcurrentTaskRunner`
  is a **backwards-compatibility alias** for `ThreadPoolTaskRunner` — they are the same
  class (`ConcurrentTaskRunner is ThreadPoolTaskRunner` → `True`). There is no
  asyncio-based runner in Prefect 3's public API. Both orchestration.md line 57 and
  `train_models.py:301-303` correctly name `ThreadPoolTaskRunner`; references to
  `ConcurrentTaskRunner` elsewhere in project docs are technically valid (alias) but
  should use the canonical name for clarity.

  **Default `max_workers` is unbounded** (`sys.maxsize`). At 1000-station fan-out,
  this means 1000 OS threads are spawned simultaneously — the primary tuning lever is
  `ThreadPoolTaskRunner(max_workers=N)` to cap concurrency. No flow decorator in the
  codebase sets `task_runner=` or `max_workers`; the default is inherited.

  **`async with` bug in orchestration.md**: Line 167 uses `async with concurrency(...)`,
  which requires `from prefect.concurrency.asyncio import concurrency` and an `async def`
  task body. Prefect provides a separate sync variant (`from prefect.concurrency.sync
  import concurrency` with plain `with`). Since `ThreadPoolTaskRunner` runs sync task
  bodies in threads, any sync `@task` using `async with concurrency(...)` will fail.
  orchestration.md should use the sync variant or document when each is appropriate.

  Benchmark scope: memory footprint and OS thread limits at 1000 concurrent threads,
  connection pool sizing (1000 threads competing for pool slots), and `max_workers`
  tuning (e.g., cap at 50–100 with chunked fan-out). Python's GIL means threads
  provide concurrency for I/O-bound work (DB writes, network) but not CPU-bound
  parallelism — CPU-bound steps (model inference) need separate analysis.

  This may be the binding constraint on single-VM viability. → BENCHMARK (thread
  count limits, memory footprint, connection pool pressure, and `max_workers` tuning
  require measurement).

  **Deliverables**:
  - Establish sync vs async task body convention for v0 flows (recommend `def` for
    I/O-bound DB work in threads) and document in orchestration.md — this determines
    which `concurrency()` import is canonical throughout the codebase
  - Update orchestration.md to document `ThreadPoolTaskRunner` as the sole runner,
    `max_workers` as the concurrency tuning lever, and the sync/async `concurrency()`
    distinction
  - Fix orchestration.md line 167 `async with` → `with` (or document when each variant
    applies, conditioned on the sync/async convention above)
  - Standardize on `ThreadPoolTaskRunner` name throughout project docs (drop
    `ConcurrentTaskRunner` alias usage for clarity)

- **`db_bulk_write` serialization bottleneck** (orchestration.md lines 164–169):
  `concurrency("db_bulk_write", occupy=1)` serializes all bulk DB writes to avoid
  connection pool saturation. At 1000-station fan-out, this single slot becomes the
  write-throughput ceiling — all mapped tasks queue behind it regardless of available
  parallelism. Assess whether the slot can be widened (e.g. `occupy=2–4`) with a
  correspondingly sized connection pool, or whether writes should be batched into
  fewer, larger COPY operations. → DECISION.

- **Training fan-out** (orchestration.md lines 100–117): Station-scoped models fan
  out across `(station, model)` pairs. The orchestration.md code sketch shows a
  sequential `for group, model, period in scope:` loop — no `.map()` at the group
  level — but if parallelism is added via `task.map()`, 1000 stations with multiple
  model types creates thousands of concurrent in-process tasks. Group-scoped models
  fan out across `(group, model)` and then per-station for hindcast/skill — still
  potentially thousands of tasks. Assess memory pressure alongside the forecast
  fan-out analysis.
  → BENCHMARK (memory pressure at thousands of concurrent in-process tasks untested).

- **Station onboarding timing** (architecture-context.md line 606): "~10 seconds for
  50 stations." At 1000 stations, the onboarding timing depends on which steps are
  O(n) vs fixed-cost (e.g., CAMELS-CH download is fixed, but QC/climatology/training
  trigger scale linearly). Re-estimate with step-level analysis. → DECISION
  (the step-level O(n) vs fixed-cost decomposition is analytical reasoning,
  not measurement — produce a revised estimate with stated assumptions).

Lower priority:
- **A3 (no PgBouncer)**: Weakly coupled to station count (more about connection count),
  but at 1000 stations with sub-daily ingest the connection pressure increases. Worth
  a note in the rationale but unlikely to change the deferral decision. Note: v0b's
  GridExtractor subflow concurrency could be the actual connection pressure source.
  → DECISION (keep deferred; add note about connection pressure at scale).

- **Cron schedule staggering** (orchestration.md line 31): All cron schedules are
  deployment-configurable. With 1000 stations on a single `default` work pool (v0),
  staggering forecast cycles, obs ingest (48 runs/day), and backup flows is a concrete
  operational lever to reduce contention. → DECISION (document as recommended practice
  in orchestration.md).

- **Container log cap** (cicd.md line 125): `max-size: 50m, max-file: 5` gives 250 MB
  per container. At 20× more log volume, recent diagnostic history is lost faster during
  incident response. → DECISION (increase `max-file` or `max-size` for the worker
  container, or route structured logs to a persistent sink). Also update cicd.md line
  145 (`~100 MB/day` log volume estimate) to reflect 1000-station volume.

- **Named concurrency slot bookkeeping** (orchestration.md line 171): 1000 active
  `observation_write:{station_id}` slots during reprocessing events generate Prefect
  DB I/O for slot acquisition/release — compounding with the Prefect DB retention
  concern (cicd.md line 141: 30-day log retention already flagged for 20× growth).
  → DECISION (keep as-is; note Prefect DB I/O concern alongside retention flag).

### Task 3 — Re-derive §D performance budgets for 1000-station target

**Depends on**: Task 2 (architectural re-assessments must complete first — partitioning,
fan-out strategy, and concurrency model decisions affect D6 budget derivation). If Task 2
conclusions are pending benchmarking, Task 3 should produce budget ranges with stated
assumptions rather than point estimates.

The entire §D is parameterized for 50 stations. This is not a matter of "updating
numbers" — the D6 per-step budget table must be **re-derived** at 1000-station scale:

- The 60-second headline target (line 259) needs reframing. Three options to evaluate:
  (a) per-station budget (e.g. < 60ms/station), (b) revised absolute wall-clock target
  with parallelism (e.g. < 10 minutes), or (c) "TBD pending benchmarks" with the
  per-station formulation as a design target. Specify which framing to use.
- The D2 row count formula (line 267) must reflect **2.52M rows/cycle** (1 forecast
  parameter/station baseline), not 126K (50 stations).
- Every row in the D6 budget table (line 295+) needs fresh estimates — particularly
  step 1.11 (store results) and steps 1.12–1.14 (alert checking) which scale linearly.
  The `db_bulk_write` single-slot bottleneck (orchestration.md lines 164–169) directly
  constrains step 1.11 throughput.
- The D3 parallelization strategy may save wall-clock time for CPU-bound steps, but
  I/O-bound steps (NWP fetch, DB writes) need separate analysis.
- The in-process task runner constraint (orchestration.md lines 56–57) is the binding
  concurrency model — 1000-station `task.map()` runs entirely in one process. Reference
  the existing high-fan-out mitigation (orchestration.md lines 48–49: remove inner
  `@task` at "hundreds+" invocations) and assess whether chunked fan-out is needed.
  Evaluate `max_workers` tuning on `ThreadPoolTaskRunner` for the forecast fan-out
  (default is unbounded — 1000 tasks = 1000 OS threads; capping at e.g. 50–100 with
  chunked submission may be necessary).

### Task 4 — Assess "single VM" viability

**Depends on**: Task 2 (concurrency model and partitioning decisions affect VM sizing),
Task 3 (disk sizing uses the daily storage growth figures re-derived in §D).

Check if "single VM" still holds at 1000-station scale. The claim appears in four files:
- `docs/v0-scope.md` line 9
- `docs/architecture-context.md` line 10
- `docs/spec/database-schema.md` line 10
- `docs/standards/cicd.md` line 9

**Premature optimization guard**: v0a starts with pre-extracted point weather, a smaller
initial fleet, and one forecast parameter per station. The full "1000 stations with
sub-daily gridded NWP" scenario represents the far end of the scale range. Task 4
should distinguish between: (a) updating documentation to reflect the eventual target,
and (b) making implementation decisions now. Only (a) is warranted without measurements;
(b) should be gated on a follow-on benchmarking plan.

**4a. Performance saturation** (can one VM handle the workload?):
- Does the 20× increase in forecast_values rows (2.52M/cycle at 1 param/station)
  saturate I/O on a single VM?
- Does daily storage growth (~1.2–2.4 GB/day total including indexes/WAL) require disk
  sizing changes? The current recommendation (architecture-context line 2721: 1 TB SSD)
  was sized for 500 stations; at 1000 stations this fills in ~415–830 days depending
  on PostgreSQL overhead.
- Does ICON-CH2-EPS NWP ingest with GridExtractor (v0b) add significant CPU load at
  this scale? Architecture-context describes bulk extraction (one grid read, all
  geometries in one pass) but provides no CPU scaling data — **this requires
  benchmarking** (out of scope for this plan; flag as follow-on).
- Does in-process `task.map()` across 1000 stations (orchestration.md lines 56–57)
  exceed single-process memory limits? — **requires benchmarking** (flag as follow-on).
- v0 uses a single `default` work pool (v0-scope.md §A6) with no declared per-container
  resource limits. The v1 three-pool limits in cicd.md lines 72–74 (`mem_limit: 4g,
  cpus: 2.0` for ops) are **v1-only** (cicd.md line 66) and do not apply to v0's single
  worker container. However, cicd.md line 76 notes all limits are deployment-configurable
  — this is the existing escape hatch for resource sizing. Document recommended v0
  resource limits for the 1000-station target.

**4b. HA escape hatch** (separate concern from performance):
- The architecture-context HA section (line 2643) addresses *availability failover*
  ("migration to Docker Swarm/Kubernetes is feasible"), not I/O/CPU saturation. It
  remains a valid escape hatch for VM failure, but does not answer 4a.
- Line 2724 communicates the HA deferral to the hydromet team (paragraph after
  capacity planning) — confirm this is still sufficient framing or needs updating.

### Task 5 — Update §I "Not risks" qualifications and DLQ exit path

**Depends on**: Task 2 (A1 partitioning re-assessment determines whether the "small
data" claim is still defensible), Task 3 (row count figures from §D re-derivation
needed to quantify the §I qualification).

Lines 495–496 in v0-scope.md use "small data" / "Nullable column addition" to
justify safe deferral of partitioning and rating curve migrations. At ~1000 stations,
partitioning migration is no longer on "small data" (~3.7B forecast_values rows/year
at 1 param/station baseline). Qualify or revise the "small data" claim (line 495).

**Note**: Line 496 (`Nullable column addition (metadata-only in PostgreSQL)` for
rating curve columns) is a **false positive** — like §I3 line 479, nullable column
additions are metadata-only in PostgreSQL regardless of row count. Do not modify.

**Note**: §I3 (line 479: `safe migration on small data` for `alert_priority` column)
is a **false positive** — it refers to adding a nullable column, which is metadata-only
in PostgreSQL regardless of row count. Do not modify.

**DLQ exit path**: If Task 2 concludes A1 (partitioning) must be advanced, this
triggers a chain reaction:
- database-schema.md line 430 ("No partitioning = no DLQ needed") becomes a dangling
  false rationale
- v0-scope.md §B deferred schemas table entry for `dead_letter_queue` must be updated
- architecture-context.md line 2694 (`"dead_letter_queue"` in health endpoint JSON
  example) becomes relevant — currently shows a DLQ status field that v0 does not
  implement; if DLQ is re-introduced, this example becomes accurate rather than
  aspirational
- orchestration.md line 27 (`drain_dlq` listed as v1-only in the flow-to-Prefect
  mapping table) must be updated if DLQ is re-introduced in v0
- A new follow-on plan must be created covering DLQ re-evaluation alongside
  partitioning implementation

If Task 2 concludes A1 remains safely deferred, qualify the "small data" claim with
the updated row counts and note the threshold at which the deferral should be revisited.

## Urgency

Foundational — affects performance targets and simplification rationales across all
docs. Should be resolved early to avoid building on wrong assumptions.

## Origin

Extracted from plan 011 §G.
