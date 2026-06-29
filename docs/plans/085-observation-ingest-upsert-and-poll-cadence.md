# Plan 085 — Observation ingest: value-restatement upsert + 5-minute poll cadence

**Status**: READY
**Phase**: 8 v0b (observation ingest hardening / Flow 2)
**Related**: Plan 084 (dev-deployment validation — runoff-only e2e, the harness
this rides on), Plan 017 (per-station observation frequency for mixed
manual/automatic networks), Plan 045/021 (NWP path — unrelated, but shares the
`*/N` cron-default convention in `register_deployments.py`),
Plan 058 (BAFU LINDAS archive collection — DRAFT; its T2 already owns changing
this same `SCHEDULE_INGEST_OBSERVATIONS` default, to `*/10`). **Reconciliation:**
085 supersedes/refines 058's cadence intent — 085 ships `*/5` now as the v0
default (2× oversampling of the ~10-min source per D9/Change A), so when 058
advances its T2 must drop its independent `*/30→*/10` step and inherit 085's
`*/5` default (058 keeps only its Mac-Mini-specific benchmark/throttle work).
**Created**: 2026-06-29
**Intended execution**: WF2 fix-mode (`vision-build`) — Claude authors the LOCKED
regression tests first, Codex implements against them. See the "WF2 fix-mode
issue" subsection below.

---

## Problem

BAFU/LINDAS observation ingestion has two coupled correctness gaps, both rooted
in the same fact: **the LINDAS adapter fetches only the current snapshot** (one
`measurementTime` per station per call — the SPARQL query has no time-window or
backfill; `adapters/hydro_scraper.py:184-192`), and BAFU's real-time graph
exposes only the latest value (no history).

1. **First-write-wins drops value restatements.** `store_raw_observations`
   inserts with `on_conflict_do_nothing` on the natural key
   `(station_id, timestamp, parameter, source)`
   (`store/observation_store.py:75-82`). When BAFU **restates** a value for an
   already-stored `measurementTime` (a corrected reading at the same timestamp),
   the new value is silently discarded — the stored value stays the original.
   The desired behaviour is **last-write-wins on a genuine value change**, while
   leaving unchanged re-fetches a true no-op so QC state never churns.

2. **30-minute polling under a snapshot-only fetch silently loses readings.**
   The default `SCHEDULE_INGEST_OBSERVATIONS` is `*/30 * * * *`
   (`cli/register_deployments.py:35`). Because the adapter cannot backfill, **poll
   cadence == data completeness**: a reading that is published and then replaced
   before the next poll is never seen. Polling at (or slower than) the ~10-minute
   source cadence guarantees gaps on phase drift, late publish, or a single
   failed poll.

### Scope

- **In**: (A) raise the default ingest cron to `*/5 * * * *` (code default **and**
  the compose init-container fallback **and** the quickstart example — D10);
  (B) de-duplicate each batch by natural key (last-wins) in Python then switch
  `store_raw_observations` from `on_conflict_do_nothing` to a **scoped**
  `on_conflict_do_update` that updates only on a real value change, resets QC
  state so the in-flow QC pass re-QCs the new value, and fixes the
  stored/skipped accounting (the counting trap, via a uuid4 set-diff); locked
  regression tests.
- **Out**: changing the adapter to backfill / fetch historical windows (LINDAS
  has no history — out of our control); per-station adaptive cadence (Plan 017);
  SPARQL rate-limit mitigation at ~1000 stations (noted as a forward risk, not
  built here); any change to the `Observation` (non-raw) store path
  (`store_observations` already upserts).

---

## Decisions / ground truth (verified against the codebase, 2026-06-29)

| # | Decision | Evidence |
|---|---|---|
| D1 | **`store_raw_observations` is first-write-wins today.** It builds each row with a fresh `id = ObservationId(uuid4())` and `qc_status = QcStatus.RAW.value`, then `pg_insert(...).on_conflict_do_nothing(index_elements=[station_id, timestamp, parameter, source]).returning(id)`. A conflicting (duplicate natural-key) row is skipped and never returned. | `store/observation_store.py:57-90` (rows `:57-70`; `on_conflict_do_nothing` `:78-81`; `.returning(id)` `:81`). |
| D2 | **The in-repo upsert pattern already exists** in the same file: `store_observations` uses `on_conflict_do_update(index_elements=[…], set_={value, qc_status, qc_flags, qc_rule_version})`. Change B mirrors this, adding the value-changed `where` predicate. | `store/observation_store.py:29-47`. |
| D3 | **The natural key is the DB unique index `uq_observations_natural_key`** on `(station_id, timestamp, parameter, source)`. The upsert stays **per-source** (the key includes `source`). | `db/metadata.py:283-290`. |
| D4 | **The QC pass runs LATER in the SAME ingest flow and only touches RAW rows.** `_run_qc_task` fetches the context window then filters `raw_obs = [o for o in all_obs if o.qc_status == QcStatus.RAW]` and calls `update_qc(...)`. So a row whose `qc_status` is reset to `RAW` on a value change WILL be re-QC'd in the same run; a row left untouched (already `qc_passed`) will NOT be re-processed. This is exactly why Change B must reset QC state on a real value change. **Second caller is symmetric:** onboarding's Step 5 also re-QCs by fetching RAW rows in `[start_utc, end_utc]` and calling `update_qc`, so a row restated-to-RAW by the bulk import is re-QC'd there (not orphaned in RAW) — the QC-reset-is-safe argument holds for BOTH callers. | `flows/ingest_observations.py:141-191` (RAW filter `:162`; `update_qc` `:183`); flow calls QC after store at `:328-358`; onboarding re-QC `services/onboarding.py:350-382` (fetches `qc_status=QcStatus.RAW` `:358-359`). |
| D5 | **Counting trap.** `_store_raw_task` returns `len(ids)` and the flow computes `skipped = len(raw_obs) - stored`, logging `ingest.store_complete stored=… skipped=…`. `store_raw_observations` builds `ids` by membership test `if row["id"] in returned`, where `row["id"]` is the **freshly generated uuid4**. With `on_conflict_do_update`, `.returning(id)` returns the **existing** row's id (not the new uuid4), so an *updated* row's id would never match `row["id"]` → an update is miscounted as a skip and `observations_stored` undercounts. Change B MUST fix the accounting so an update counts as a write. | `flows/ingest_observations.py:132-133, 324-326`; `store/observation_store.py:83-88`. |
| D6 | **Second caller exists — `services/onboarding.py:290`.** The onboarding historical import also calls `store_raw_observations` and derives `observations_imported += len(inserted_ids)` and `skipped = len(raw_obs) - len(inserted_ids)` for `observation.duplicate_skipped`. Any change to the return shape/accounting MUST keep this caller correct (and its fake + the `StoreRawObservations` Protocol signature). | `services/onboarding.py:286-303`; Protocol `protocols/stores.py:90`; fake `tests/fakes/fake_stores.py:97`. |
| D7 | **Existing dup test asserts the no-op contract.** `TestStoreRawDuplicateSkip.test_second_insert_returns_empty_and_no_new_rows` re-inserts an **identical** row and asserts the second call returns `[]` and no new DB row. Under Change B's value-changed predicate, an identical re-insert performs no update → returns nothing for that row → this contract is preserved (the test stays green, possibly reworded for the new accounting). | `tests/integration/store/test_observation_store.py:282-303`. |
| D8 | **Default ingest cron is env-overridable.** `cron_ingest = os.environ.get("SCHEDULE_INGEST_OBSERVATIONS", "*/30 * * * *")`. Change A edits the default literal; the `SCHEDULE_INGEST_OBSERVATIONS` override still wins. | `cli/register_deployments.py:35`. |
| D9 | **Adapter is snapshot-only (no backfill).** The SPARQL query binds a single subject URI and filters predicates; there is no time-window, no `?time >= …`, no pagination over history. One `measurementTime` per station per call. | `adapters/hydro_scraper.py:184-192`. |
| D10 | **The code default is NOT the deployed default — compose overrides it (Change A is a no-op without the compose edit).** The init container that runs `register_deployments` sets `SCHEDULE_INGEST_OBSERVATIONS: ${SCHEDULE_INGEST_OBSERVATIONS:-*/30 * * * *}`, so `os.environ.get(..., "*/5 …")` at `cli/register_deployments.py:35` **never** sees the code default — the deployed cron stays `*/30`. Change A MUST also edit the compose fallback **and** the commented quickstart example, else nothing changes in deployment. | `docker-compose.yml:206`; `docs/deployment-quickstart.md:34`. |
| D11 | **The value-change predicate + QC reset is sound against the schema (closes the NULL/float worry).** `observations.value` is a **nullable** `Float`, with check `(qc_status='missing') = (value IS NULL)`. `IS DISTINCT FROM` is the NULL-safe comparator — correct *precisely because* the column is nullable. Raw inserts always carry a non-null `value` + `qc_status=RAW`, so resetting a changed row to `RAW` never violates the check (the row is non-null on both sides). Float equality is exact-bitwise, so an identical republish is a true no-op (predicate false → no write). | `db/metadata.py:241` (nullable Float), `db/metadata.py:265-268` (check); `store/observation_store.py:65` (raw insert non-null value + RAW). |

---

## Change A — poll cadence (default `*/30` → `*/5`)

**Decision: change the DEFAULT** (recommended for v0), not document-only.
Rationale to state in the implementation:

- The adapter is **latest-snapshot-only** with no backfill (D9), so poll cadence
  *is* data completeness. Polling at exactly the ~10-minute source cadence risks
  permanently missing a reading on phase drift, late publish, or a single failed
  poll — there is no second chance to fetch it.
- **5-minute oversampling (~2× the source cadence)** guarantees each distinct
  `measurementTime` is captured at least once; the in-between re-fetch of an
  unchanged snapshot is deduped to a true no-op by Change B (no row write, no QC
  churn).
- **Scale caveat (forward risk, NOT a v0 blocker):** at ~1000 stations,
  `*/5` = up to 1000 SPARQL queries every 5 minutes. This is a future
  rate-limit / batching consideration (Plan 017 / a later cadence plan), not in
  scope here. State it explicitly so it is not lost.
- The `SCHEDULE_INGEST_OBSERVATIONS` env override (D8) remains the operator
  escape hatch (e.g. a deployment that must throttle can set `*/15`). **O4 resolved:**
  ship `*/5` as the default and keep the env override as the throttle for large
  deployments — the ~1000-station scale risk is a forward concern, not a v0 blocker.

**Edits required (all three, or Change A is a deployment no-op — D10):**

1. `cli/register_deployments.py:35` — code default literal `"*/30 * * * *"` →
   `"*/5 * * * *"`.
2. `docker-compose.yml:206` — compose fallback
   `${SCHEDULE_INGEST_OBSERVATIONS:-*/30 * * * *}` →
   `${SCHEDULE_INGEST_OBSERVATIONS:-*/5 * * * *}`. **This is the one that actually
   changes the deployed cron** (the init container's env wins over the code default).
3. `docs/deployment-quickstart.md:34` — commented example
   `# SCHEDULE_INGEST_OBSERVATIONS=*/30 * * * *` →
   `# SCHEDULE_INGEST_OBSERVATIONS=*/5 * * * *` (keep the docs honest).
4. `tests/unit/cli/test_register_deployments.py:25` — existing assertion
   `by_name["ingest-observations"].cron == "*/30 * * * *"` →
   `== "*/5 * * * *"` (this test breaks otherwise — B2).

---

## Change B — scoped `on_conflict_do_update` (last-write-wins on value change)

Switch `store_raw_observations` (`store/observation_store.py:75-90`) from
`on_conflict_do_nothing` to `on_conflict_do_update`, with three hard constraints.

1. **Update ONLY when the value actually changed.** Add a `where` predicate so an
   unchanged re-fetch stays a true no-op (preserving today's common-case
   behaviour and D7's contract):
   `on_conflict_do_update(index_elements=[…], set_={value, qc_status, qc_flags, qc_rule_version}, where=observations_table.c.value.is_distinct_from(pg_insert(...).excluded.value))`
   — i.e. SQL `WHERE observations.value IS DISTINCT FROM excluded.value`. When the
   predicate is false, Postgres performs no update and writes no row.
2. **On a real value change, reset QC state to RAW.** Set
   `qc_status → QcStatus.RAW.value`, `qc_flags → NULL`, `qc_rule_version → NULL`
   in the `set_`. This is required because the QC pass that runs **later in the
   same ingest flow** (`_run_qc_task`) re-processes only rows currently in `RAW`
   (D4). Without the reset, a restated value would keep the stale `qc_passed`
   status and flags computed for the old value, and would not be re-QC'd.
3. **De-duplicate the batch by natural key in Python BEFORE building the upsert
   (last-wins) — REQUIRED, not optional (B3).** `on_conflict_do_update` raises
   `CardinalityViolation` ("ON CONFLICT DO UPDATE command cannot affect row a
   second time") when a single statement's `VALUES` contains two rows with the
   same conflict key. Today's `on_conflict_do_nothing` tolerates intra-statement
   dups; `do_update` does not. The LINDAS ingest flow is snapshot-only (one row
   per station) and cannot hit this, but Change B applies to **all** raw-
   observation ingestion including the onboarding bulk import
   (`services/onboarding.py:286-290`, batches up to `_BATCH_SIZE=5000` at
   `store/observation_store.py:49`), which can carry duplicate natural keys.
   **Fix:** collapse `observations` to one row per `(station_id, timestamp,
   parameter, source)` (last occurrence wins) in Python before constructing the
   `pg_insert(...).values(...)`. This also makes inserted/updated/skipped
   deterministic.
4. **Fix the stored/skipped accounting (the counting trap, D5) — O1/O2 resolved.**
   With `on_conflict_do_update`, `.returning(id)` returns the **existing** row's id
   for an updated row, not the freshly generated uuid4, so the current
   `row["id"] in returned` membership test miscounts updates as skips.
   - **Return shape (O1 — settled):** KEEP the Protocol return as
     `list[ObservationId]` returning the **written** (inserted + updated) ids.
     `RawStoreOutcome` dataclass is **rejected**: it would force edits to ~15
     destructuring call sites the plan never enumerates (e.g.
     `tests/integration/store/test_observation_store.py:70` `[oid] = …`, `:127`
     `[oid0, _oid1] = …`). A list keeps `stored = inserted + updated` and
     `skipped = len(raw) - stored` correct in **both** callers with zero signature
     churn and preserves the `second_ids == []` no-op contract
     (`test_observation_store.py:295`).
   - **Inserted-vs-updated discrimination (O2 — settled): use the uuid4 set-diff,
     NOT `xmax`.** Each row is inserted with a fresh `id = ObservationId(uuid4())`
     (`store/observation_store.py:59`); `id` is **not** in the upsert `set_`, so an
     updated row's `RETURNING id` is the EXISTING id, never one of our generated
     uuid4s. Therefore, per batch:
     `inserted = returned ∩ generated_uuids`,
     `updated = returned − generated_uuids`,
     `skipped = len(batch) − len(returned)` (unchanged-value rows never appear in
     `RETURNING`). Portable, no system column, and the fake reproduces it trivially
     because it owns its ids. The **written list returned** is the union (all of
     `returned`).
   - **Observability (two layers, no signature change):**
     - The **flow** keeps its existing list-based mechanism unchanged:
       `ingest.store_complete stored=<len(ids)> skipped=<len(raw_obs)-stored>`
       (`flows/ingest_observations.py:324-326`), where `stored = inserted + updated`
       (the returned list is the written union) and `skipped` therefore excludes
       genuine writes. The flow CANNOT split inserted-vs-updated (it only receives
       `len(ids)` via `_store_raw_task`, `:132-133`), and must not try to.
     - The **store** emits the finer split as a NEUTRAL, caller-agnostic event
       `observation.raw_upsert inserted=… updated=… skipped=…` via a NEW
       module-level structlog logger added to `store/observation_store.py`
       (the module has no logger today). The event is caller-agnostic on purpose:
       it is emitted correctly for BOTH the LINDAS ingest flow AND the onboarding
       bulk import (D6), so it must NOT be named after either flow. `inserted` /
       `updated` / `skipped` come from the uuid4 set-diff. `observations_stored =
       inserted + updated`. The split is computed and logged INSIDE the store and
       is NOT surfaced through the signature.
5. **Scope (D3/D6).** `store_raw_observations` is the generic raw-observation
   store path; the natural key includes `source`, so the upsert stays per-source.
   The change applies to **all** raw-observation ingestion (the LINDAS ingest
   flow **and** the onboarding historical import), not only LINDAS. Both callers
   must be verified after the change.
6. **Onboarding restatement semantics (O3 — settled).** In the historical bulk
   import (`onboarding.py:290-298`), count inserted + updated as
   `observations_imported`; count only true no-ops (unchanged dups) as
   `duplicate_skipped`. Because the return list now includes updated ids,
   `len(inserted_ids)` already yields this with **no caller code change** —
   `observations_imported += len(inserted_ids)` and
   `skipped = len(raw_obs) - len(inserted_ids)` stay correct. **Behaviour delta to
   document:** previously a re-import of CHANGED data counted as
   `duplicate_skipped`; now it counts as imported. Acceptable.

Keep the change minimal and type-driven per CLAUDE.md: no new domain primitives
(the `RawStoreOutcome` dataclass is rejected — O1); reuse `QcStatus.RAW`; no
behavioural change to `store_observations`. The schema soundness of the predicate
+ QC reset is closed by D11 (nullable Float, `IS DISTINCT FROM` NULL-safe, raw
inserts non-null, exact-bitwise float equality).

---

## Phases

### Phase 1 — Cadence default (Change A)

#### Task 1a — Raise default ingest cron to `*/5 * * * *`

- **Scope (all four edits — the compose one is what actually changes deployment, D10):**
  1. `cli/register_deployments.py:35` default literal `"*/30 * * * *"` → `"*/5 * * * *"`.
  2. `docker-compose.yml:206` fallback `${SCHEDULE_INGEST_OBSERVATIONS:-*/30 * * * *}`
     → `${SCHEDULE_INGEST_OBSERVATIONS:-*/5 * * * *}`.
  3. `docs/deployment-quickstart.md:34` commented example `*/30` → `*/5`.
  4. `tests/unit/cli/test_register_deployments.py:25` assertion `*/30` → `*/5`
     (B2 — this existing test breaks otherwise).
  Preserve the `SCHEDULE_INGEST_OBSERVATIONS` env override in all of the above.
  Out: any other deployment's cron; the adapter; rate-limit batching.
- **Docs (run-count budget invalidated by `*/30→*/5` — MAJOR-2):** `*/5` =
  **288 runs/day**, 6× the documented "48 runs/day". Update every doc that pins the
  old count or budget:
  - `docs/standards/orchestration.md:33` — "observation ingest (48 runs/day)" →
    "observation ingest (288 runs/day)".
  - `docs/standards/cicd.md:162` — the "48 obs ingest runs/day" log-volume budget.
    Update to 288 and add an explicit scale note that **obs-ingest log volume grows
    ~6×** at `*/5` (the ~1000-station "logs rotate within hours" / `max-file: 5` ×
    `max-size: 50m` = 250 MB cap should be revisited per cicd.md's own rotation
    guidance). Keep the existing per-poll SPARQL-rate-limit caveat (Change A,
    ~1000 stations × up to 1000 queries/5 min) — it is additive, not a replacement.
  - Plan 084's D6 line (`084-dev-deployment-validation-2-station-runoff.md:54`,
    "`ingest-observations` (`*/30`)") is presented as current truth → update that
    one mention to `*/5`.
  - Also note the new default + the ~1000-station scale caveat in the
    deployment/operations doc that lists the crons, if not already covered above.
- **Verification**: `uv run pytest tests/unit/cli/ -k register_deployments` (the
  test at `:25` now asserts `*/5 * * * *`, plus the Task 3c env-override
  assertion); `uv run ruff check src/ tests/`.
- **Exit gate**: the registered `ingest-observations` deployment spec carries
  cron `*/5 * * * *` by default; the compose fallback and quickstart example both
  read `*/5`; setting `SCHEDULE_INGEST_OBSERVATIONS=*/15 * * * *` yields
  `*/15 * * * *`; ruff + pyright clean on the changed module.

### Phase 2 — Scoped upsert (Change B)

**Depends on Phase 1** (sequential; matches the dependency graph). The files are
independent, but the gate sequences Phase 2 after Phase 1 so the full suite runs
once against both changes; the locked tests in Phase 3 cover both phases.

#### Task 2a — Value-changed predicate + QC reset in `store_raw_observations`

- **Scope**: Replace `on_conflict_do_nothing` with
  `on_conflict_do_update(set_={value, qc_status→RAW, qc_flags→NULL, qc_rule_version→NULL}, where=value IS DISTINCT FROM excluded.value)`
  in `store/observation_store.py`. Schema soundness is closed by D11 (nullable
  Float; `IS DISTINCT FROM` NULL-safe; raw inserts non-null; exact-bitwise float
  equality). Out: the accounting fix (Task 2c — but it lands together since the
  `.returning` shape couples them); `store_observations`.
- **Verification**: `uv run pytest tests/integration/store/test_observation_store.py`
  (existing + new locked tests from Phase 3); `uv run pyright src/sapphire_flow/store/observation_store.py`.
- **Exit gate**: an identical re-insert writes no row and preserves QC status; a
  changed-value re-insert updates `value` and sets `qc_status='raw'`,
  `qc_flags=NULL`, `qc_rule_version=NULL`; distinct timestamps still create
  distinct rows.

#### Task 2b — In-Python natural-key de-duplication before the upsert (B3)

- **Scope**: BEFORE building `pg_insert(...).values(...)`, collapse the incoming
  `observations` to one row per `(station_id, timestamp, parameter, source)`,
  **last occurrence wins**, in `store/observation_store.py`. Without this,
  `on_conflict_do_update` raises `CardinalityViolation` when a single statement's
  `VALUES` has two rows with the same conflict key — which the onboarding bulk
  import (batches up to `_BATCH_SIZE=5000`) can contain. Out: any change to the
  per-source key (D3); `store_observations`.
- **Verification**: `uv run pytest tests/integration/store/test_observation_store.py -k dedup`
  (the Task 3a intra-batch-dup test) and the onboarding tests; `uv run pyright src/sapphire_flow/store/observation_store.py`.
- **Exit gate**: an `observations` batch containing two rows with the same natural
  key does not raise; the last row's `value` wins; inserted/updated/skipped counts
  are deterministic.

#### Task 2c — Fix stored/skipped accounting + inserted/updated/skipped observability (O1/O2)

- **Scope**: Fix the counting trap (D5) with the **uuid4 set-diff** (O2 — NOT
  `xmax`): `inserted = returned ∩ generated_uuids`,
  `updated = returned − generated_uuids`, `skipped = len(batch) − len(returned)`.
  `generated_uuids` and the returned-ids set **accumulate ACROSS batches** (the
  store loops in `_BATCH_SIZE=5000` chunks): LINDAS ingest is single-batch, but the
  onboarding bulk import multi-batches, so the inserted/updated/skipped totals are
  summed over every batch, not computed per-batch-and-discarded.
  KEEP the Protocol return as `list[ObservationId]` (O1 — dataclass rejected),
  returning the **written** ids (all of `returned` = inserted + updated) so both
  callers (`ingest_observations.py`, `onboarding.py`) stay correct with **zero
  signature churn**. `observations_stored = inserted + updated`.
  **Observability (two layers — MAJOR-1 resolution):** the FLOW keeps its existing
  list-based log unchanged — `ingest.store_complete stored=<len(ids)>
  skipped=<len(raw_obs)-stored>` (`flows/ingest_observations.py:324-326`); it does
  NOT split inserted-vs-updated (it only sees `len(ids)`). The STORE emits the
  finer split via a NEW module-level structlog logger added to
  `store/observation_store.py` (no logger today) as a NEUTRAL, caller-agnostic
  event `observation.raw_upsert inserted=… updated=… skipped=…` — correct for both
  the LINDAS flow and onboarding callers. Out: any flow-named event emitted from
  the store; the Protocol signature (`protocols/stores.py`) and the
  `RawStoreOutcome` dataclass (rejected).
- **Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py tests/integration/store/test_observation_store.py tests/unit/services -k onboard`;
  `uv run pyright src/`.
- **Exit gate**: a changed-value re-ingest counts as a **write** (not a skip);
  the returned list contains inserted + updated ids; the flow's
  `ingest.store_complete` reports `stored`(=inserted+updated)/`skipped` (skipped
  excludes genuine writes); the store emits `observation.raw_upsert` with the
  inserted/updated/skipped split; `onboarding.py`'s
  `observations_imported`/`observation.duplicate_skipped` remain correct **without
  caller changes**; full suite green.

#### Task 2d — Mirror the new contract in `FakeObservationStore` (B4)

- **Scope**: `tests/fakes/fake_stores.py:97-126` currently skips ALL natural-key
  dups, returns only inserted ids, and never overwrites or resets QC. Re-specify
  `store_raw_observations` to mirror the real store so the Task 3b flow test (which
  uses the fake) is satisfiable:
  - dup + unchanged value → skip (return no id for it; no write);
  - dup + value changed → overwrite `value`, set `qc_status=RAW`, `qc_flags=[]`,
    `qc_rule_version=None`, and return the **existing** id as a write;
  - new natural key → insert with a fresh id and return it;
  - intra-call duplicate natural keys → last-wins (mirror Task 2b), no raise;
  - the returned `list[ObservationId]` = inserted + updated (written) ids, so the
    fake reproduces the inserted/updated/skipped signal Task 3b asserts on.
- **Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py`;
  `uv run pyright tests/fakes/fake_stores.py`.
- **Exit gate**: the fake's `store_raw_observations` overwrites-and-re-RAWs on a
  value change, returns the existing id as a write, and the Task 3b re-QC-after-
  restatement assertion passes against it.

### Phase 3 — Locked tests + verification gate

Depends on Phases 1 and 2. Per WF2 fix-mode, these LOCKED tests are authored
BEFORE the implementation lands (they describe the contract; Codex makes them
pass).

#### Task 3a — Locked store-level upsert tests (integration, real Postgres)

- **Scope**: Add to `tests/integration/store/test_observation_store.py`:
  1. **Identical re-ingest is a no-op**: store a raw obs; mark it `qc_passed`
     (via `update_qc`); re-`store_raw_observations` the IDENTICAL
     `(station_id, timestamp, parameter, source, value)`; assert the row is
     UNCHANGED (`value` same, `qc_status` STAYS `qc_passed`, `qc_flags`/
     `qc_rule_version` preserved) and the call reports a **skip** (no write).
     (Generalises existing `TestStoreRawDuplicateSkip`, D7.)
  2. **Changed-value re-ingest updates + resets QC**: store + `qc_passed`;
     re-store same natural key with `value=V2≠V`; assert `value==V2`,
     `qc_status=='raw'`, `qc_flags is NULL`, `qc_rule_version is NULL`, and the
     call reports a **write** (the counting-trap fix), with the **same DB row id**
     (no new row inserted).
  3. **Distinct `measurementTime`s → distinct rows** (dedup non-regression).
  4. **Intra-batch duplicate natural key does not raise (B3)**: a single
     `store_raw_observations([...])` call containing two rows with the SAME
     `(station_id, timestamp, parameter, source)` but different `value` must NOT
     raise `CardinalityViolation`; the last row's `value` wins; exactly one DB row
     exists for that key.
  5. **Split observability (light) — `observation.raw_upsert` classifies correctly
     (MAJOR-1)**: capture structlog events from the store (e.g.
     `structlog.testing.capture_logs`) across a mixed batch — one brand-new key,
     one changed-value restatement, one unchanged dup — and assert the store emits
     `observation.raw_upsert` with `inserted=1`, `updated=1`, `skipped=1` (the
     uuid4 set-diff classification). Keep it light: assert the counts only, not
     wording of other fields.
- **Verification**: `uv run pytest tests/integration/store/test_observation_store.py`.
- **Exit gate**: all five locked tests pass against real Postgres.

#### Task 3b — Locked flow round-trip test (re-QC after restatement)

- **Scope**: In `tests/unit/flows/test_ingest_observations.py` (or a fake-store
  integration), drive the full `ingest_observations_flow`: ingest value V at
  timestamp T → QC passes; re-ingest the same station/T/parameter/source with
  V2≠V → assert the stored value becomes V2 AND the flow's QC pass re-QCs it to
  its proper status (proving the RAW reset feeds `_run_qc_task`, D4), and
  `observations_stored` counts the restatement as a write.
- **Verification**: `uv run pytest tests/unit/flows/test_ingest_observations.py`.
- **Exit gate**: re-QC-after-restatement passes end-to-end through the flow;
  `IngestResult.observations_stored`/`observations_skipped` reflect the write.

#### Task 3c — Locked cadence assertion

- **Scope**: A unit test asserting the default `ingest-observations` cron is
  `*/5 * * * *` and that `SCHEDULE_INGEST_OBSERVATIONS` overrides it. Lives with
  the deployment-registration tests.
- **Verification**: `uv run pytest tests/unit/cli/ -k register_deployments` (or
  wherever deployment specs are tested).
- **Exit gate**: default-`*/5` and env-override assertions pass.

#### Task 3d — Full verification gate

- **Scope**: Full-suite + typecheck + lint gate per `docs/workflow.md` Task Exit
  Gate. Confirm both callers of `store_raw_observations` (flow + onboarding) and
  the fake are consistent.
- **Verification**: `uv run pytest`; `uv run ruff check src/ tests/`;
  `uv run ruff format --check src/ tests/`; `uv run pyright src/`.
- **Exit gate**: all green; affected docs updated; no other prod file changed.

---

## WF2 fix-mode issue (for `vision-build`)

```text
issue:
  Repro: clean DB; ingest discharge for station X at timestamp T with value V
    via store_raw_observations (the QC pass marks it qc_passed). Re-ingest the
    SAME station X / timestamp T / parameter=discharge / source with value
    V2 != V (a BAFU restatement at the same measurementTime).
  Expected: the stored value becomes V2, its qc_status is reset to RAW and the
    in-flow QC pass re-QCs it to its proper status, and the restatement is
    counted as a write (observations_stored increments). An IDENTICAL re-ingest
    (value unchanged) writes nothing and leaves a qc_passed row qc_passed.
  Actual (current): the V2 reading is dropped — store_raw_observations uses
    on_conflict_do_nothing, so the stored value stays V and no re-QC happens.
    Separately, the default ingest cron is */30 under a snapshot-only adapter,
    so readings can be permanently missed. And the deployed cron is set by the
    compose init container (docker-compose.yml:206), so a code-default change
    alone is a deployment no-op.

acceptanceCriteria:
  - store_raw_observations uses on_conflict_do_update scoped by
    "WHERE observations.value IS DISTINCT FROM excluded.value"; an UNCHANGED
    re-poll performs no row write and leaves a qc_passed row qc_passed (no QC
    reset, no QC churn).
  - On a real value CHANGE: value updated; qc_status reset to RAW; qc_flags and
    qc_rule_version reset to NULL; the row is re-QC'd by _run_qc_task in the same
    flow run.
  - An onboarding batch containing two rows with the same natural key
    (station_id, timestamp, parameter, source) does NOT raise
    CardinalityViolation; last-wins; exactly one row results.
  - Counting trap fixed via the uuid4 set-diff (not xmax): an updated row counts
    as a write (observations_stored = inserted + updated), not a skip; the return
    stays list[ObservationId] = inserted + updated ids; onboarding.py
    observations_imported and observation.duplicate_skipped remain correct with
    no caller change.
  - Observability (two layers, both tested): (i) the flow's ingest.store_complete
    reports stored = inserted + updated and skipped excludes genuine writes
    (testable via the returned list length + flow counts); (ii) the store emits the
    neutral, caller-agnostic event observation.raw_upsert with inserted / updated /
    skipped from the uuid4 set-diff (light store-level test, Task 3a item 5).
  - Distinct measurementTimes still produce distinct rows (dedup non-regression).
  - Default SCHEDULE_INGEST_OBSERVATIONS cron is "*/5 * * * *" in BOTH the code
    default (register_deployments.py) AND the compose fallback
    (docker-compose.yml:206); the env override is honoured.
  - Locked tests (3a/3b/3c) authored first and passing; full suite + ruff +
    pyright green.
```

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-cadence-default",
      "tasks": ["1a"],
      "parallel": false,
      "note": "Change A: raise default ingest cron to */5; preserves env override."
    },
    {
      "id": "phase-2-scoped-upsert",
      "tasks": ["2a", "2b", "2c", "2d"],
      "parallel": false,
      "depends_on": ["phase-1-cadence-default"],
      "note": "2a value-changed predicate + QC reset; 2b in-Python last-wins natural-key dedup (B3, avoids CardinalityViolation); 2c accounting fix via uuid4 set-diff keeping list[ObservationId] (O1/O2, couples to the .returning shape so lands with 2a); 2d mirrors the contract in FakeObservationStore (B4)."
    },
    {
      "id": "phase-3-locked-tests-and-gate",
      "tasks": ["3a", "3b", "3c", "3d"],
      "parallel": false,
      "depends_on": ["phase-2-scoped-upsert"],
      "note": "Per WF2 fix-mode the locked tests (3a-3c) are AUTHORED before implementation; 3d is the full verification gate."
    }
  ]
}
```

---

## Resolved decisions (were open questions; all closed before READY)

1. **O1 — return shape: KEEP `list[ObservationId]`** returning the written
   (inserted + updated) ids. `RawStoreOutcome` dataclass **rejected** — it would
   force edits to ~15 unenumerated destructuring call sites (e.g.
   `test_observation_store.py:70` `[oid] = …`, `:127` `[oid0, _oid1] = …`). A list
   keeps `stored = inserted + updated` and `skipped = len(raw) - stored` correct
   in both callers with zero signature churn and preserves the `second_ids == []`
   no-op contract (`test_observation_store.py:295`). See Change B item 4, Task 2c.
2. **O2 — inserted-vs-updated via uuid4 set-diff, NOT `xmax`.** Updated rows
   `RETURNING id` the existing id, never our generated uuid4, so
   `inserted = returned ∩ generated_uuids`, `updated = returned − generated_uuids`,
   `skipped = len(batch) − len(returned)`. Portable, no system column, fake
   reproduces it trivially. See Change B item 4, Task 2c.
3. **O3 — onboarding restatements counted as imported.** Count inserted + updated
   as `observations_imported`; only true no-ops as `duplicate_skipped`. Because
   the returned list now includes updated ids, `len(inserted_ids)` yields this with
   no caller change. Behaviour delta (re-import of CHANGED data was
   `duplicate_skipped`, now `imported`) is documented and acceptable. See Change B
   item 6.
4. **O4 — ship `*/5` default + keep the env override** as the throttle for large
   deployments. The ~1000-station scale risk is a forward concern, not a v0
   blocker. See Change A.

---

## Affected files

- `src/sapphire_flow/store/observation_store.py` — Change B: in-Python last-wins
  natural-key dedup (Task 2b) + scoped `on_conflict_do_update` (value-changed
  predicate, QC reset, Task 2a) + uuid4-set-diff accounting (Task 2c) + a NEW
  module-level structlog logger emitting the neutral `observation.raw_upsert
  inserted/updated/skipped` event (the module has none today — MAJOR-1).
- `src/sapphire_flow/flows/ingest_observations.py` — verify-only for the flow's
  existing list-based `ingest.store_complete stored/skipped` (the returned-list
  count now correctly includes updates; no inserted/updated split is added at the
  flow level — MAJOR-1, Task 2c).
- `src/sapphire_flow/cli/register_deployments.py` — Change A: code default ingest
  cron `*/30` → `*/5`.
- `docker-compose.yml` — Change A: init-container fallback `*/30` → `*/5` (D10 —
  **this is what actually changes the deployed cron**).
- `docs/deployment-quickstart.md` — Change A: commented example `*/30` → `*/5`.
- `tests/unit/cli/test_register_deployments.py` — B2: existing assertion at `:25`
  `*/30` → `*/5`; plus the Task 3c env-override assertion.
- `tests/fakes/fake_stores.py` — B4: re-spec `store_raw_observations` to overwrite
  + re-RAW on value change, last-wins on intra-call dups, return inserted+updated
  ids. Signature unchanged (O1 keeps `list[ObservationId]`).
- `src/sapphire_flow/services/onboarding.py` — NO code change required (O1 keeps
  the return shape; `len(inserted_ids)` accounting stays correct). Verify only;
  may add a comment if the import-vs-skip semantics delta (O3) needs a note.
- `src/sapphire_flow/protocols/stores.py` — NO change (signature unchanged, O1).
- `tests/integration/store/test_observation_store.py`,
  `tests/unit/flows/test_ingest_observations.py` — locked tests (Phase 3).
- `docs/standards/orchestration.md` — line 33: "observation ingest (48 runs/day)"
  → "288 runs/day" (MAJOR-2).
- `docs/standards/cicd.md` — line ~162: "48 obs ingest runs/day" budget → 288 +
  explicit ~6× obs-ingest log-volume scale note / revisit `max-file`×`max-size`
  rotation (MAJOR-2).
- `docs/plans/084-dev-deployment-validation-2-station-runoff.md` — line 54 (D6):
  one-line `ingest-observations (*/30)` → `*/5` (it is presented as current truth,
  MINOR-3).
- `docs/plans/085-observation-ingest-upsert-and-poll-cadence.md` (this plan),
  `docs/plans/README.md` (index entry).
- No other production files.
```
