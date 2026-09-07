# Decision Record — skill score generations, not collisions (Plan 235)

**Date**: 2026-09-04
**Status**: Implemented on `feat/plan-235-skill-generations`, held at PR.
**Owners**: Bea (orchestrator)
**Cross-reference**: `docs/plans/235-skill-score-generations.md` (source plan),
`docs/decisions/plan-228-hindcast-skill-resampling.md` (D3 — the recompute this
plan unblocks), `alembic/versions/0054_skill_score_generations.py`.

## What this closes

Plan 228 needs a recompute (its D3) to replace ~115,000 corrupt skill scores.
Two problems blocked it: recomputes were silently discarded (the natural key
had no per-run identity), and the obvious fix — bumping `computation_version`
per run — conflates algorithm version with a run counter and can collide or
fragment a fan-out. This plan adds a separate generation identity instead.

## D1 — generation identity: one UUID per task invocation, not an input hash

⚠️ **Revised by the per-run-scope fixer round below (2026-09-04, blocker #3)**
— the "no fingerprinting" position this section originally stated turned out
to be unsound for one specific case (a crash-then-retry under the SAME
invocation id) and was reopened. The paragraphs immediately below are kept
for history; see "Per-run scope fixer round" at the end of this document for
what actually ships.

`generation_id: UUID | None` is a NEW column on `skill_scores`/`skill_diagrams`
(migration 0054), minted once per `compute_skills_task`/
`compute_combined_skills_task` invocation and stamped on every score/diagram
that call produces. "Stable across retries of the same run" is satisfied
because the id is a task PARAMETER — a caller (or a Prefect retry, which
re-invokes with the same bound arguments) that already has one keeps it;
"different when inputs differ" is satisfied trivially because every NEW
invocation (a deliberate recompute) mints a fresh one.

This deliberately does NOT hash/fingerprint inputs to detect "did anything
actually change" (D2b#3's apparent tension: observations are mutable, so
"stable across retries" and "different when inputs differ" cannot both hold
without SOME input-identity rule). The rule adopted is: don't try to answer
that question at write time at all — every invocation gets a new generation,
and D2d makes overlap a non-problem (below). This is a real, deliberate
resolution of D2b#3, not an oversight: building a fingerprinting mechanism
was rejected as unnecessary scope once D2d's read-side-only correctness
guarantee was adopted.

**Why this had to be reopened**: D2d's read-side guarantee covers two
DIFFERENT, both-published generations competing for the same scope — readers
pick the newer one, so an overlap is invisible-but-harmless. It does NOT
cover a crash-then-retry sharing the SAME invocation id: attempt 1 can store
rows then crash before `publish_generation` (an orphaned, never-published
generation); if an observation is corrected before the retry runs, the retry
recomputes DIFFERENT score values but under the IDENTICAL natural key +
`generation_id` as attempt 1's orphaned rows, so `ON CONFLICT DO NOTHING`
silently keeps attempt 1's STALE values and the retry's corrected computation
is discarded, not "one of two valid generations" — a real data-loss bug, not
an overlap. See "Per-run scope fixer round" below for the fix.

## D2/D2b — one predicate, one join, shared by every reader

`store.skill_store.latest_generation_predicate(data_table, generations_table)`
is the single rule every reader now applies — unifying the three
incompatible "what is current" rules the plan's audit found
(`max(computation_version)`, no filter at all, `freshness == "current"`). It
is a composable SQLAlchemy predicate, not a query rewrite, so it slots into
both the store's own `sa.Table`-based queries AND the API's raw reflected-
table reads (`api/routes/models.py`, `api/routes/stations.py`) with the
identical logic — pass the matching (possibly reflected) `skill_generations`
table as `generations_table`.

Precedence (D2b#1): `computation_version` DESC, then `published_at` DESC,
then `id` DESC as a final deterministic tiebreak on an exact timestamp tie.
Scope (D2b#2): `(station_id, model_id, model_artifact_id, parameter,
skill_source, forcing_type)` — narrower than the natural key (which also
varies by `time_step`/`phase`/`lead_time`/`season`/`flow_regime`/`metric`
WITHIN one generation). `skill_diagrams` carries no `forcing_type` column at
all (diagrams are never forcing-scoped), so the predicate folds that side of
the compare to a constant rather than raising on a missing column.

> **Superseded 2026-09-04 (second fixer round, blocker)**: this originally
> read "deliberately NOT including `model_artifact_id` — matching what
> `fetch_latest_scores` always filtered on." That was wrong: without
> `model_artifact_id` in scope, a generation minted for a candidate artifact
> under retraining evaluation could outrank and hide the STILL-ACTIVE
> artifact's generation for the same model, since `model_id` alone does not
> distinguish artifacts. `model_artifact_id` is now part of scope, NULL-safe
> compared (POOLED/BMA combinations carry `NULL` on both sides). See the
> "Fixer round 2" section below.

**Baseline rows** (`generation_id IS NULL`, i.e. every pre-Plan-235 row) get
their OWN precedence rule among themselves — `max(computation_version)`,
restoring exactly `fetch_latest_scores`'s old behaviour. A published
generation only displaces a baseline at the SAME or a HIGHER
`computation_version` — never a lower one (D2b#1 reads the highest eligible
algorithm version FIRST, then decides ties within it by generation ranking).
T1 needed the among-baselines rule because pre-existing data can legitimately
hold baseline rows at more than one `computation_version` (a v2 row written
between Plan 228 and this plan, still with no `generation_id`).

> **Superseded 2026-09-04 (second fixer round, blocker)**: this originally
> read "... until the FIRST generation is ever published for their scope, at
> which point published generations take over entirely" — i.e. version was
> NOT read first: a v1 generation could hide a required v2 baseline the
> instant it existed, inverting D2b#1's own "version first" precedence. See
> the "Fixer round 2" section below.

## D1/T1 — why `generation_id` is NOT a foreign key

The natural write order is: insert scores, insert diagrams, THEN publish (one
row in `skill_generations`) — only once every expected write already
succeeded (D3's completeness gate). If `generation_id` were a foreign key to
`skill_generations.id`, the very first score insert would fail with
`ForeignKeyViolation` (the generation row does not exist yet). An orphaned
`generation_id` that never gets a ledger row is not corruption — it is
EXACTLY the shape of an unpublished/partial generation D3 requires.

## D2c — `mark_stale` cannot run in production; `publish_generation` replaces it

`mark_stale` is a real `UPDATE`, and `sapphire_worker` holds `INSERT` only on
`skill_scores`/`skill_diagrams` (`docker/bootstrap-roles.sql`) — no `UPDATE`
grant exists, and the decision is to not add one. `PgSkillStore.
publish_generation` is the sanctioned, INSERT-only replacement: publishing
with `score_count = diagram_count = 0` for a scope is the "mark stale"
tombstone — it supersedes any older generation there without ever touching
an existing row. `sapphire_worker` gets exactly one new grant: `INSERT ON
skill_generations`.

`mark_stale` itself is UNCHANGED and still present (zero production callers
today; kept for the locking test that proves it fails under the real role —
`tests/integration/db/test_role_bootstrap.py::
TestSkillStoreGenerationPublicationUnderScopedRole`). Removing it outright
was judged unnecessary scope for this plan; Plan 228's D3 (mark + recompute)
must use `publish_generation`, never `mark_stale`, when it executes.

## D2d — no write-time rejection; the read side is the only guarantee

Two overlapping recomputes for the same scope are BOTH accepted and stored —
there is no `SELECT ... FOR UPDATE` (needs the `UPDATE` privilege D2c
declines), no distributed lock, and under READ COMMITTED two concurrent
writers could both pass a naive check anyway. Correctness comes entirely
from `latest_generation_predicate`: a superseded generation's rows persist,
unread. `PgSkillStore.publish_generation` logs the generation id and its
`published_at` at INFO — the only way to notice a collision after the fact.
`concurrency_limit=1` on the standalone deployments remains defence in depth,
not the mechanism.

## D3 — the completeness gate is "publish is the last write," not a transaction

Production connects with `isolation_level=AUTOCOMMIT`
(`flows/_db.py:setup_production_stores`), so there is no cheap way to wrap
"insert scores, insert diagrams, insert the ledger row" in one multi-
statement transaction. The gate does not need one: `publish_generation` is a
SINGLE `INSERT` statement, which is atomic by construction, and it is called
ONLY after `compute_skills_task`/`compute_combined_skills_task` have already
stored every score/diagram that run produced. If the process crashes at any
point before that call, the orphaned score/diagram rows it already wrote sit
invisible (no generation row points to them) and the previous publication —
if any — stays exactly as it was. This is proven directly at the store layer
in `tests/integration/store/test_skill_store.py::
TestSkillScoreGenerations::test_partial_generation_leaves_previous_publication_intact`,
without needing to simulate an actual process crash.

## D4 — retention

No pruning machinery added (Non-goal). `docs/handover/data-flows.md` already
states skill scores are permanent; this plan's `skill_generations` ledger
adds roughly one row per (station, model, parameter, skill_source,
forcing_type) recompute — negligible next to the ~115,000-row score/diagram
growth each full recompute already produces. Diagram retention was
previously unstated; it is now declared permanent alongside scores (see
`docs/handover/data-flows.md`).

## T3 — run scoping

`compute_skills_task`/`compute_skills_flow`'s `hindcast_run_id` and the new
`compute_combined_skills_task`/`compute_combined_skills_flow`'s
`hindcast_run_ids: dict[ModelId, UUID]` are now REQUIRED (no default) —
omitting them is a `TypeError` at the call site, not a silent 1970–2100
unscoped fetch. `store.hindcast_store.PgHindcastStore.fetch_hindcasts_by_station`
gained the matching `hindcast_run_ids` filter (one run id per combined
model, since models are combined pairwise on independent schedules). Station
onboarding's own `fetch_hindcasts` call was ALSO fixed, opportunistically
(the plan marks it explicitly non-blocking, but it turned out contained
enough to do here): `onboard_model` (`services/model_onboarding.py`) now
mints ONE `hindcast_run_id` before its Step 5 (`run_hindcast_fn`) call and
passes the SAME id to Step 6 (`compute_skill_fn`) — `services/onboarding.py`'s
`_run_hindcast`/`_compute_skill` closures both accept it (default `None`,
preserving old unfiltered behaviour for any other caller), so the skill leg
scopes its fetch to what this run just wrote instead of this station/model's
entire training-period history.

## Evidence

- `tests/integration/store/test_skill_store.py::TestSkillScoreGenerations` —
  D1 (recompute survives and becomes current), D2d (overlapping publications
  both persist, reader returns newer), D3 (partial generation leaves the
  previous publication intact).
- `tests/integration/db/test_migration_0054_skill_generations.py` — upgrade
  survives 0052-shaped rows at both `computation_version` bands; a NULL-
  generation old-image write still collides (does not bypass uniqueness).
- `tests/integration/db/test_role_bootstrap.py::
  TestSkillStoreGenerationPublicationUnderScopedRole` — `mark_stale` denied,
  `publish_generation` succeeds, under the REAL `sapphire_worker` role.
- `tests/integration/api/test_skill_readers_generation_aware.py` — all three
  raw-SQL API readers (6, 7, 8, 9 in the plan's audit) select the newest
  generation through the real FastAPI app.
- `tests/unit/flows/test_compute_skills.py::TestRunScopingIsRequired` — T3.

## Fixer round (2026-09-04)

Two independent reviews (including a Codex pass over the diff) of the
implementation found four blockers and five majors. All resolved in the same
PR, held at PR (no merge):

- **Diagram forcing-type scope bug (blocker).** `latest_generation_predicate`
  compared a diagram's constant `''` forcing-type fallback against the
  GENERATION's real value (`ForcingType.REANALYSIS` in every production
  publish) — they could never match, so a pre-Plan-235 baseline diagram
  (`generation_id IS NULL`) stayed visible FOREVER alongside its real
  replacement. Fixed: `_scope_match` skips the forcing-type comparison
  entirely whenever `data_table` has no `forcing_type` column (diagrams are
  never forcing-scoped) — including generation-vs-generation ranking, not
  only the diagram-vs-generation match. Locked by
  `TestDiagramScopeForcingType` (`tests/integration/store/test_skill_store.py`).
- **`hindcast_run_ids={}` fell through to unscoped (blocker).**
  `PgHindcastStore.fetch_hindcasts_by_station`'s `if hindcast_run_ids:` was
  falsy for both `None` (deliberately unscoped) and `{}` (explicitly empty,
  required at the `compute_combined_skills_task` boundary) — an empty
  mapping silently got every historical run instead of zero matches, the
  same mixing-anchored-and-unanchored-cohorts defect T3 exists to prevent.
  Fixed at the store (empty mapping → `{}`, distinct from `None`) AND at the
  task boundary (`ConfigurationError` on an empty mapping, defense in
  depth). Locked by `test_empty_run_ids_mapping_matches_nothing_unlike_
  omitted` (store) and `TestEmptyHindcastRunIdsMapping` (task).
- **Completeness gate lacked a real flow-level test (blocker).** The
  existing "partial generation" test only exercised direct store calls,
  never the task's own cohort loop deciding whether to store/publish at
  all. Added `TestCompletenessGate` (`tests/unit/flows/test_compute_skills.py`)
  driving a real two-cohort fan-out through `compute_skills_task.fn` with
  the second cohort's compute failing — confirms neither cohort's scores
  are stored and `publish_generation` is never called. The REST of this
  finding — that a broader "logical recompute" spanning MULTIPLE mapped
  tasks (different stations/parameters/models) should be atomic across all
  of them — was NOT implemented: D2b#2 is explicit that replacement scope
  is the recompute's OWN (station, model, parameter, ...) domain, and nine
  independent scopes each publishing on their own success is what that
  decision requires, not a cross-scope multi-task transaction. Recorded as
  a disputed finding, not a gap.
- **Version collision with `origin/main` (blocker).** The branch was 17
  commits behind `main` at review time. Rebased cleanly (only the version
  bump lines conflicted); the version is reassigned after rebase per the
  normal patch-bump workflow.
- **BMA diagram identity collision (major).** Both CV folds compute a
  diagram at the identical natural key (same generation, same
  station/model/parameter/.../diagram_type/threshold_level) — the old
  `fold1_diagrams + fold2_diagrams` concatenation left them colliding, so
  `ON CONFLICT DO NOTHING` silently dropped one while `diagram_count`
  counted both. Fixed by `combined_skill._merge_fold_diagrams`, which
  merges same-key diagrams (bin-count sums for `rank_histogram`/
  `reliability`, elementwise mean for `roc`'s rate curves) into ONE
  diagram covering the full evaluated period, mirroring how
  `_average_skill_scores` already treats the two folds' scalar scores.
  Locked by `TestBmaCrossValidationDiagramMerge` (pure-function level, no
  store needed).
- **Publication could certify silently-dropped rows (major).**
  `store_skill_scores`/`store_skill_diagrams` now return the count
  ACTUALLY inserted (via `RETURNING`, not `cursor.rowcount` — measured
  live: psycopg3 returns `-1` for `rowcount` on this project's `INSERT ...
  ON CONFLICT DO NOTHING`, even a plain `INSERT`). `flows.compute_skills`'s
  `_store_skill_results` (and `services/onboarding.py`'s equivalent path)
  compare the returned counts against what was submitted and skip
  `publish_generation` entirely on a mismatch. Locked by
  `TestStoreSkillResultsAccurateRowcount` (store-level) and
  `TestStoreCountMismatchSkipsPublish` (task-level, via a stub store).
- **`published_at` was not the publication instant (major).** Both tasks
  used `all_scores[0].computed_at` — the FIRST cohort's compute time,
  captured before later cohorts finish — as `published_at`, which can
  misorder D2b#1 precedence against a concurrent publication. Fixed:
  `published_at` is now `clock()` called immediately before the
  `publish_generation` insert. `PgSkillStore.publish_generation`'s INFO log
  also gained `published_at` (D2d requires it as the after-the-fact
  collision diagnostic). Locked by `TestPublicationInstant`.
- **`HindcastStore` Protocol not updated (major).** `compute_combined_
  skills_task`'s `hindcast_store` was typed bare `object`, so static
  checking could not catch an incompatible conformer. Fixed: a REAL (not
  TYPE_CHECKING-only) `HindcastStore` import, matching the precedent
  already set for `SkillStore` in the same module; `protocols/stores.py`'s
  `HindcastStore.fetch_hindcasts_by_station` gained the `hindcast_run_ids`
  parameter; `docs/spec/types-and-protocols.md` updated to match. Locked by
  `TestPgHindcastStoreProtocolConformance`/`TestPgSkillStoreProtocolConformance`
  (isinstance against the real Protocol, mirroring the existing fake
  checks in `tests/fakes/test_fakes.py`).
- **D1 retry stability was not actually retry-stable (major).** Both
  tasks minted `generation_id = uuid4()` INSIDE the task body on a `None`
  default — a Prefect retry re-executes that body from scratch, so every
  retry minted a DIFFERENT id, contradicting the design comment's own
  claim. Fixed: `compute_skills_flow`/`compute_combined_skills_flow` (and
  the `.map()` fan-outs in `flows/train_models.py`/`flows/onboard_model.py`,
  one id per mapped station/parameter pair) now mint the id ONCE, before
  calling the task — a retry of the task replays with that same bound
  argument. The task's own `None`-default mint is now only a fallback for
  a caller that invokes it directly (e.g. `.fn()` in tests).
  `PgSkillStore.publish_generation` is also now idempotent under an exact
  replay (`ON CONFLICT (id) DO NOTHING`) and rejects a replay whose scope
  identity doesn't match what was already published under that id (a
  genuine id collision, not a retry). Locked by
  `TestGenerationIdRetryStability` and
  `TestPublishGenerationIdempotentReplay`.
- **Station onboarding was a generationless writer (major/minor — the
  SAME finding surfaced twice, at different severities, by the two review
  passes).** `services/onboarding.py`'s `_compute_skill` wrote only
  baseline (`generation_id=None`) scores and never published a generation
  for its own scope — once ANY generation is ever published for that
  scope by an unrelated recompute, onboarding's baseline writes become
  permanently invisible (`latest_generation_predicate`'s undisplaced-
  baseline rule requires NO generation to have EVER been published).
  Fixed: `_compute_skill` now mints and publishes its own generation,
  with the same store/reconcile/publish sequence `compute_skills_task`
  uses. Locked by `TestMakeSkillFnPublishesGeneration`.
- **`FakeSkillStore.fetch_scores_by_regime` crashed on any match (minor,
  found independently by two reviewers).** Missing the `peers`/
  `peer_forcing_type` keyword arguments every other `_is_current` caller
  supplies. Fixed; locked by `TestFakeSkillStoreFetchScoresByRegime`.
- **Nine-reader lock was incomplete (minor).** Added opposing-generation
  coverage for readers #3 (`fetch_scores_by_regime`) and #4
  (`fetch_skill_scores`, which reader #5 — the promotion gate — reads
  through) in `tests/integration/store/test_skill_store.py`, and a
  negative assertion (the superseded generation's OWN diagram type must
  NOT render) in the existing model-detail API test.

## Fixer round 2 (2026-09-04)

A further independent review (including a Codex pass over the diff) of the
round-1 fixes above found four more blockers, two majors, and a minor. All
resolved in the same PR, still held at PR (no merge):

- **`skill_generations` omitted `model_artifact_id` from scope (blocker).**
  See the superseded D2/D2b text above — this let a generation minted for a
  candidate artifact (e.g. under retraining evaluation) outrank and hide the
  STILL-ACTIVE artifact's generation for the same model. Fixed: migration
  0054 gains a nullable `model_artifact_id` column on `skill_generations`
  (+ `ix_skill_generations_scope` index update); `latest_generation_
  predicate`'s `_scope_match` NULL-safe-compares it
  (`COALESCE(model_artifact_id::text, '')`, matching the natural-key
  indexes' existing pattern); `publish_generation` takes it as a required
  keyword and includes it in the idempotent-replay identity check. Every
  production caller updated: `compute_skills_task` passes the model's own
  `artifact_id`; `compute_combined_skills_task` and `services/onboarding.py`
  pass `None`/the onboarding artifact respectively.
  `tests/fakes/fake_stores.py`'s `_FakeGeneration`/`_scope_key`/`_is_current`
  mirror the same scoping. Locked by
  `TestGenerationScopeIncludesArtifact` (`tests/integration/store/
  test_skill_store.py`).
- **Retry-stable generation IDs made pre-publication retries impossible
  (blocker).** A crash after storing scores/diagrams but before publishing
  meant a retry's rows (new row `id`s, SAME natural key + SAME retry-stable
  `generation_id`) collided against the earlier attempt and reported 0
  newly inserted — `_store_skill_results` read that as "still incomplete"
  and silently skipped `publish_generation` FOREVER, with the Prefect task
  run still reporting SUCCESS. Fixed: a new `SkillStore.count_generation_
  rows(generation_id)` returns the TOTAL persisted count for a generation
  regardless of which attempt wrote it; `_store_skill_results` reconciles
  against that total (not the per-call inserted count) and now RAISES
  `SkillGenerationIncompleteError` on a genuine mismatch instead of
  silently logging and returning — Prefect now correctly sees a FAILED run
  for a real gap, and a benign retry now correctly succeeds and publishes.
  Locked by `TestRetryStablePublicationSucceeds` (retry with orphaned rows
  from an earlier crash) and `TestCountGenerationRows` (store-level).
  `TestStoreCountMismatchSkipsPublish` updated to assert the raise.
- **The completeness gate counted produced ROWS, not expected COHORTS
  (blocker).** A cohort that returned EMPTY results with no exception (a
  malformed hindcast, zero overlapping observations) was invisible to the
  old gate — as long as `all_scores`/`all_diagrams` was non-empty overall,
  the task published, potentially superseding-and-hiding a previously
  COMPLETE generation with a degraded/partial one.
  `compute_combined_skills_task` had the same gap one level up: a REQUESTED
  model missing from `fetch_hindcasts_by_station`'s result (it omits a key
  entirely rather than returning an empty list) was never checked, so a
  combination could silently proceed and publish with fewer models than
  requested. Fixed: `compute_skills_task` tracks `cohorts_missing` (a
  cohort with hindcasts that produced nothing) and only publishes when
  every partitioned cohort scored something;
  `compute_combined_skills_task` tracks `missing_requested_models` (a
  `set` difference against `hindcast_run_ids`) and requires
  `cohorts_combined == len(partitioned_by_key)` too. Either check failing
  skips `publish_generation` — the scores/diagrams still get stored
  (orphaned under an unpublished generation, D3's intended shape), the
  previous generation stays what readers see. Locked by
  `TestSilentCohortGapBlocksPublication` and
  `TestMissingRequestedModelBlocksPublication`.
- **Baseline rows bypassed algorithm-version-first precedence (blocker).**
  See the superseded D2/D2b text above — `no_generation_for_scope` hid
  EVERY baseline the instant ANY generation existed for the scope,
  regardless of version, letting an invalid/incomplete v1 generation stay
  current over a required v2 baseline. Fixed: a generation only displaces a
  baseline at the SAME or a HIGHER `computation_version`
  (`no_generation_at_or_above_version_for_scope`), and a generation is
  itself blocked by a HIGHER-version baseline in its own scope
  (`baseline_outranks_generation`) — version is read first, generation
  ranking only decides ties within it. Mirrored in `FakeSkillStore._is_
  current`. Locked by `TestBaselineVersionFirstPrecedence` (both the v2-
  baseline-survives-v1-generation case and a same-version regression
  guard).
- **BMA diagram merge's ROC average was unweighted (major).** `_merge_
  diagram_data`'s "roc" branch averaged `hit_rate`/`false_alarm_rate`
  across folds with a plain `_nanmean`, giving a fold with 3 events the
  same say as a fold with 300 — the merged curve for a lightly-evented
  fold could dominate one covering most of the evaluated period. Fixed:
  `compute_roc_curve` now also returns `n_events`/`n_non_events`; the merge
  weights each fold's rate series by its own denominator
  (`_weighted_mean_series`). Locked by
  `TestMergeFoldDiagramsRocWeighting.test_roc_merge_weights_by_event_
  count_not_equal_average`.
- **Merged diagram's `eval_period` kept only fold 1's bounds (major).**
  `replace(first, data=merged_data)` in `_merge_fold_diagrams` left
  `eval_period_start`/`eval_period_end` as fold 1's alone, even though the
  merged data spans BOTH folds. Fixed: union across the whole merged
  group (`min`/`max`). Locked by
  `TestMergeFoldDiagramsRocWeighting.test_merged_diagram_eval_period_
  spans_both_folds`.
- **`FakeSkillStore` diagram scope never mirrored the real forcing-type
  exemption (minor, escalated from the first round's fix).** Round 1 fixed
  `latest_generation_predicate`'s diagram-forcing-type bug in the REAL
  store, but `FakeSkillStore._is_current`/`_scope_key` still forced the
  diagram side to a constant `forcing_type=None` while comparing it against
  the GENERATION's real (non-null) value — the identical bug, unfixed in
  the shared test double. Fixed: `_scope_key`/`_FakeGeneration.scope_key`
  take `include_forcing_type`, dropping forcing_type from BOTH sides for a
  diagram caller. Locked by
  `TestFakeSkillStoreDiagramGenerationVisibility` (`tests/fakes/
  test_fakes.py`).

## Per-run scope fixer round (2026-09-04)

A third review round found the completeness gate did not detect incompleteness
in three different ways (the one thing this plan exists to provide), plus a
migration-rollback gap and two majors. Per the plan doc's own framing: "a gate
that reports 'complete' when it is not is worse than no gate." All six items
below are fixed in the same PR, still held at PR (no merge, no push).

- **Dropped inputs never reached the completeness counter (blocker #1).**
  `partition_by_time_step_and_phase` silently drops (logs + `continue`) a
  hindcast whose OWN `valid_time`s internally mix phase — it never reaches a
  cohort at all, so `cohorts_missing` (which only ever counted cohorts that
  survived partitioning and then came back empty) never saw it. A run that
  silently dropped one malformed INPUT still reported `cohorts_complete` and
  published a generation that was a valid SUBSET of the real one. Fixed:
  `partition_by_time_step_and_phase` now returns `(cohorts, rejected_count)`;
  every caller (`compute_skills_task`, `compute_combined_skills_task` — summed
  across every combined model, `services.onboarding._compute_skill`) folds
  `rejected_count` into its own completeness gate alongside `cohorts_missing`.
  Locked by `TestRejectedHindcastBlocksPublication.
  test_internally_mixed_phase_hindcast_blocks_publication` (flows) and
  `TestMakeSkillFnRaisesOnPartialGeneration` (onboarding, which now RAISES
  rather than degrading — see the major below).
- **The combined gate counted attempts, not outputs (blocker #2).**
  `cohorts_combined` incremented BEFORE computation, so pooled/BMA
  legitimately returning `([], [])` still counted as "combined" — and the
  per-cohort acceptance rule was `len(per_model_hindcasts) >= 2`, so a cohort
  carrying only 2 of 3 REQUESTED models ("A+B" when "A/B/C" was asked for)
  satisfied it just as well as the full set. Fixed: each `partitioned_by_key`
  cohort must carry EXACTLY `set(hindcast_run_ids)` (not merely `>= 2`
  models) to be attempted at all, and `cohorts_combined` increments only
  AFTER `compute_combined_skill`/`compute_bma_skill_cross_validated` returns
  non-empty output. `cohorts_complete`'s existing `cohorts_combined ==
  len(partitioned_by_key)` check needed no further change — a cohort that
  fails either new condition simply never increments the numerator. Locked by
  `TestCombinedGateCountsOutputsNotAttempts` (both the model-set-mismatch and
  the empty-output-with-two-real-candidates cases) and
  `TestMissingRequestedModelBlocksPublication` (updated: 3 models requested,
  one with zero hindcasts anywhere, now asserts `scores == []` — the old
  "2-of-3 still combine" assertion documented exactly the behaviour this
  blocker closes).
- **A retry could republish attempt 1's STALE rows under a corrected retry
  (blocker #3, reopens D1's original text above).** `generation_id` was a
  flow-minted invocation id used DIRECTLY as the row identity. A crash after
  storing scores but before publishing, followed by a retry that recomputes
  under CHANGED inputs (an observation corrected in between), produces
  DIFFERENT score values under the IDENTICAL natural key + `generation_id` —
  `ON CONFLICT DO NOTHING` keeps attempt 1's stale values, and the retry's
  correction is silently lost while `count_generation_rows` still reports a
  "complete" count (attempt 1's row count already matched). Fixed: the flow-
  minted id is now only an INVOCATION id. `services.skill.service.
  compute_generation_fingerprint` hashes the actually-computed score/diagram
  VALUES (never `id`/`computed_at`, which legitimately differ per attempt);
  `resolve_generation_id(invocation_id, scores, diagrams)` derives the row
  identity as `uuid5(invocation_id, fingerprint)`; `rebind_generation_id`
  rewrites every score/diagram to that derived id before storing. An
  unchanged-input retry re-derives the SAME id (safe, idempotent collision);
  a changed-input retry derives a DIFFERENT id, so it never collides with the
  earlier orphaned attempt and publishes its own, correct generation.
  `flows.compute_skills`'s `_store_skill_results` moved to
  `services.skill.service.store_skill_results_or_raise` so `services.
  onboarding` shares the identical reconcile-and-raise logic. Locked by
  `TestRetryStablePublicationSucceeds` (updated: asserts the SAME derived id
  across an unchanged-input retry, captured via a spy rather than asserting
  literal equality with the invocation id) and the new
  `TestRetryWithChangedInputsDoesNotCollide.
  test_corrected_observation_between_attempts_is_not_shadowed`.
- **The migration broke one-release rollback (blocker #4).** Migration 0054's
  new indexes permit duplicate natural keys across generations, but a
  rolled-back pre-235 image's readers are generation-unaware — a rollback
  after any generation-tagged write would serve MIXED generations
  (`docs/standards/cicd.md`'s one-release rollback rule). Fixed:
  `DeploymentConfig.enable_skill_generations` (default `true`, so every
  existing caller and test is unaffected) gates the WRITE side only —
  `compute_skills_task`, `compute_combined_skills_task`, and `services.
  onboarding._compute_skill` all check it. When `false`, scores/diagrams are
  written as pre-235 baseline rows (`generation_id=NULL`) and no
  `skill_generations` row is ever published — schema and every generation-
  aware READER (T2) can ship in one release while writes stay legacy-shaped;
  an operator flips the flag `false` for that first release and back to
  `true` (or removes the override) only once every instance is confirmed
  running a generation-aware image. Locked by
  `TestSkillGenerationsCanBeDisabledForRollout` (flows and onboarding).
- **Onboarding could publish a partial generation and never signalled
  failure (major).** `_compute_skill` logged an error and `return`ed (a
  silent no-op) on ANY cohort producing nothing, or on a store insert-count
  mismatch — `onboard_model()` never learned anything went wrong and never
  recorded `FAILED_SKILL`. Unlike the recurring recompute path (which
  degrades quietly so a previously COMPLETE generation stays visible),
  onboarding has no earlier generation to fall back to: its FIRST generation
  being incomplete must surface as a failure. Fixed: `_compute_skill` now
  raises `SkillGenerationIncompleteError` (caught by `model_onboarding.py`'s
  existing `except Exception` around the skill-computation step, which
  already records `FAILED_SKILL`) on any rejected hindcast, any empty
  cohort, or a store count mismatch — via the same shared `store_skill_
  results_or_raise` blocker #3 introduced. Locked by
  `TestMakeSkillFnRaisesOnPartialGeneration.
  test_one_empty_cohort_raises_skill_generation_incomplete`.
- **Diagram generation ranking still collapsed forcing scopes (major,
  escalated from round 1's fix).** Round 1's `scope_includes_forcing_type`
  flag was computed ONCE from the outer `data_table` (`skill_scores` or
  `skill_diagrams`) and reused for every `_scope_match` call, including
  `outranks`/`superseded`'s generation-vs-generation ranking — so when
  `data_table` was `skill_diagrams` (which carries no `forcing_type` column
  of its own), forcing type was skipped EVEN when comparing two `generations`
  rows directly, which always carry a real one. Two diagram generations
  differing ONLY by forcing type therefore ranked as the SAME scope and
  competed, silently hiding one. Fixed: `_scope_match(a, b)` now decides
  per-call, from whether BOTH `a` and `b` actually have a `forcing_type`
  column — never from the outer `data_table`. `FakeSkillStore._is_current`
  mirrors this: a row identified by `generation_id` now looks up its OWN
  generation directly and ranks it against OTHER generations sharing its OWN
  full (forcing-aware) scope, rather than pre-filtering `scoped_generations`
  by the row's own (possibly forcing-less) scope. Locked by
  `TestDiagramForcingTypeScopeDoesNotCollapse.
  test_two_forcing_types_both_stay_current`.

**Deferred, per the plan's explicit scope fence**: Plan 046's staging runbook
no longer matches the required flow signatures (`run-hindcast` without a run
id, `compute-skills` without `hindcast_run_id`) — real, doc-only, and
deliberately NOT fixed in this round.

## Independent-review fixer round (2026-09-04)

An independent Codex pass over the diff raised 5 blockers. All resolved in the
same PR, still held at PR (no merge, no push):

- **Generation-tagged writes were enabled by DEFAULT, violating the
  two-release rollout the previous round's blocker #4 itself specified
  (blocker).** `DeploymentConfig.enable_skill_generations` (and every
  matching `getattr(..., True)` fallback in `flows/compute_skills.py` and
  `services/onboarding.py`) defaulted `True` — so a bare
  `deployment_config=None`/omitted (every caller that does not explicitly
  set the field, which is exactly the SHAPE of "Release A hasn't been
  configured yet") got Release B's generation-tagged writes from the moment
  this plan shipped, defeating the rollout the field exists to gate. Fixed:
  the field, every fallback, `docs/spec/config-reference.toml`, and
  `docs/spec/types-and-protocols.md` all default `False`. Locked by
  `TestEnableSkillGenerationsDefault`
  (`tests/unit/config/test_deployment.py`) — a bare `DeploymentConfig()`, a
  minimal TOML that omits the key, AND the actual shipped `config.toml`
  (which never mentions it) all load `False`. Flipping the default broke 8
  existing tests that relied on the old `True` default to exercise the
  generation-aware write path without saying so explicitly — each now passes
  `deployment_config=make_deployment_config(enable_skill_generations=True)`
  (or `config=...` for onboarding) to keep testing Release B behaviour, not
  Release A's by accident.
- **The generation fingerprint omitted persisted semantic content, letting a
  stale retry row publish (blocker).** `compute_generation_fingerprint`
  (`services/skill/service.py`) omitted `flow_regime_config_id` from both
  scores and diagrams, and excluded a diagram's own `data` JSONB blob
  entirely — on the (wrong) theory that any input drift always shows up in
  some score's `repr(score)` too. It does not: scores and diagrams are
  independent outputs of the same computation, so a retry whose only
  difference is a corrected diagram, or a reconfigured flow-regime boundary
  that happens not to move any scalar score, re-derives the IDENTICAL
  `resolve_generation_id` output and silently loses to `ON CONFLICT DO
  NOTHING` against the earlier attempt's stale row. Fixed: both fields are
  now part of the digest, and `d.data` is folded in via
  `json.dumps(d.data, sort_keys=True, default=str)` (key order in a
  freshly-built dict is never semantically meaningful, so it must not
  perturb the digest). Locked by `TestComputeGenerationFingerprint`
  (`tests/unit/services/skill/test_service.py`) — one case per field,
  proven RED against the pre-fix function (identical hash despite the
  changed field).
- **A BMA cohort with only ONE successful cross-validation fold was treated
  as complete and published (blocker).** `_scores_for_fold` can legitimately
  return `([], [])` on its own (no usable BMA weights from that fold's
  training half, or too few evaluation steps) — `_average_skill_scores`/
  `_merge_fold_diagrams` silently fell back to whichever single fold
  survived, publishing ONE fold's un-cross-validated result as if it were
  the two-fold average `compute_bma_skill_cross_validated` promises. Fixed:
  explicit `fold1_complete`/`fold2_complete` status
  (`bool(fold_scores or fold_diagrams)`), checked BEFORE combining — an
  incomplete fold makes the whole cohort return `([], [])`, which the
  EXISTING flow-level completeness gate
  (`compute_combined_skills_task`'s `cohorts_combined` count, per-run scope
  blocker #2 above) already treats as "this cohort did not combine"; no
  separate flow-level wiring was needed. Locked by
  `TestBmaCrossValidationRequiresBothFolds`
  (`tests/unit/services/skill/test_combined_skill.py`), which forces fold
  1's weight computation to fail via a call-counting monkeypatch while fold
  2 computes normally, and proves the function returns nothing rather than
  fold 2's real scores.
- **An orphan `hindcast_forecasts` header (no matching `hindcast_values`
  rows) was silently dropped before the new rejected-input accounting could
  ever see it, letting a partial generation publish (blocker).**
  `PgHindcastStore.fetch_hindcasts`/`fetch_hindcasts_by_station` log a
  warning and `continue` on an orphan header — this happens at the STORE
  layer, upstream of every caller's `rejected_hindcasts`/`cohorts_missing`
  completeness accounting (per-run scope blocker #1), so a real storage-
  layer gap (a crashed write, an out-of-band delete) silently shrank the
  result with no signal a caller's gate could act on. Fixed: both fetches
  now RAISE `StoreError` on an orphan header **when the caller named a
  specific run** (`hindcast_run_id`/`hindcast_run_ids` is not `None`) —
  which is what every production caller through `compute_skills_task`/
  `compute_combined_skills_task` always does (T3 made both required). An
  UNSCOPED fetch (`hindcast_run_id`/`hindcast_run_ids` omitted — station
  onboarding's optional, non-blocking-gate call per T3, or any other legacy
  caller) still logs and skips exactly as before, since it cannot tell "this
  step legitimately has no hindcast" from "this step's hindcast was dropped
  here" either way. Locked by
  `TestFetchHindcastsOrphanHeaderInScopedRunRaises`
  (`tests/integration/store/test_hindcast_store.py`, real PostgreSQL) —
  one case for `fetch_hindcasts`, one for `fetch_hindcasts_by_station`, each
  seeding a valid header plus an orphan header under the SAME requested run
  and asserting `StoreError`; the pre-existing unscoped orphan-skip tests
  (`TestFetchHindcastsOrphanSkip`) are untouched and still pass.
- **Release-version collision and a missing per-code-commit version bump
  (blocker).** The branch had already been rebased onto the then-current
  `origin/main` before this round started (confirmed: `main..HEAD` showed
  exactly one commit, the version bump lines were the only conflict). This
  round's own commit bumps again (`0.1.876` -> `0.1.878`, skipping `0.1.877`
  — already claimed, uncommitted-but-visible, by a concurrent sibling
  worktree at the time) via `bump-my-version bump patch --new-version`, so
  every retained code commit on this branch carries its own required
  version bump.
