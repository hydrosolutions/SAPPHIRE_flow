---
status: READY
---
# CI critical path: rebalance the integration shards (#584, Part 1 only)

MVP, no production: smallest change. Branch from current `main`. Touches only test-shard
membership in `tools/integration_shards.py`: no workflow, attestation, job-name or
required-check change. No coverage or threshold weakening.

## Baseline (measured 2026-11-26, `gh` job timings of green `ci.yml` runs)

- 20 PR runs: wall (first job start to last job end) p50 16.3 min, p95 34.8. About 82
  runner-minutes per run. Setup/install steps are 0.1-0.5 min per job: no cache win left.
- PR p50 per job (min): `integration (heavy)` 15.7; `aquacast-release-image-proof` 15.1;
  `integration (mid)` 11.9; `integration (rest)` 11.8. Heavy = tests 13.2 + partition proof 1.5
  (heavy only) + performance gate 0.4.
- Run ids behind these numbers are not recorded; the implementer re-measures (below) and
  records the run ids it uses.

## Decision (owner, 2026-11-27): option B

Only rebalance the shards. Splitting the Aquacast proof is NOT done: it needs a new
private-verification gate, a staging gate change, a capacity-preparation guard exception and
another Aquacast image build, for about 1-2 minutes. Expected gain of this plan: heavy drops
about 2-3 min, so PR wall p50 goes from about 16.3 to about 15 min, after which the Aquacast
proof (about 15.1) is the limit. That is the honest target; it is not promised, measurement
decides.

## Change

Shard membership lives only in `tools/integration_shards.py` (`NAMED_GROUPS`; `rest` is the
computed catch-all). Following that file's rule ("rebalance by JUnit, never per PR"): take
per-file durations from the `integration-junit-<shard>` artifacts (14-day retention) of at
least 5 recent green PR runs, as the 2026-11-25 rebalance did
(`docs/plans/2026-11-25-integration-shard-rebalance.md`). Move whole files so the three shard
job times (tests plus fixed overhead: heavy about 2.3 min incl. partition proof and
performance gate, mid and rest about 1.5) land within about 1 minute of each other, i.e. move
roughly 2-3 min of tests out of `heavy`. Keep the invariants the tool already checks (stress
benchmark node stays in heavy; production performance benchmark stays out of heavy). Do not
move the partition proof (workflow bytes are hash pinned). Update the dated rebalance comment
with the run ids and numbers used.

Constraints found in review:
- `tests/unit/tools/test_integration_shards.py` pins some memberships: `test_skill_data_use_isolation.py`
  stays in heavy (~:137-146), three named files stay in mid (~:149-161), and
  `test_daily_forecast_one_shot_runner.py` and `test_swiss_overview.py` stay in heavy
  (~:163-178). Prefer moving unpinned heavy files (for example `api/test_native_selected_weather.py`,
  `db/test_bounded_qc_serialization.py`, `db/test_migration_input_quality.py`,
  `db/test_role_bootstrap.py`, `flows/test_train_models_warm_start_pg.py`,
  `ops/test_protected_evidence_restore.py`, `db/test_migration_0041_0044_tenant_model.py`). If
  moving a pinned file is needed for balance, update its pin in the same PR and say why.
- `TestInjectedPartitionProof` (~:295-302) asserts a hard-coded message ("N selected nodes, M
  non-live files") derived from the curated lists. Moves between named groups (heavy to mid)
  keep it; any change to the curated total (for example a move into computed `rest`) requires
  updating that assertion in the same PR.
- Heavy has only about 10 files and several are very slow, so the 2-3 min may not be
  reachable by whole-file moves; if so, report the best achievable balance.
- Heavy keeps its fixed overhead (partition proof, performance gate), so heavy test time
  should land about 1 min below mid and rest. The overhead figures here are PR p50 and are not
  comparable to the 2026-11-25 plan (58 s proof, 19 s gate); the implementer's re-measure rules.
- After the move, heavy and the Aquacast proof both sit near 15 min, so the wall-time gain may be
  under 1 min; about 15 min is close to the floor for this approach.
- Docs: add a one-line dated note to `docs/standards/cicd.md` (~:833-842, the shard membership
  narrative) when membership changes.

## Verification and acceptance

`uv run python tools/integration_shards.py --check-partition` and the tool's unit tests pass;
ruff. Before/after shard job times and PR wall times from at least 5 comparable PR runs each
(p50, p95, small-sample caveat). The PR's own CI run is the evidence for the new split. If the
gain is under 1 minute, report that honestly and do not iterate.

## Out of scope

Aquacast proof split, outlier tail, more runners or a fourth shard, moving the partition
proof, test-suite speedups.

## Review and merge

Independent Claude and Codex plan reviews before READY; independent patch review; full CI.
Owner merges; deployment is not involved.
