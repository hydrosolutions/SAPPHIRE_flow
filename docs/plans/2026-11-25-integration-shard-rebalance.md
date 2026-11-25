---
status: READY
---
# Integration shards: add a third curated shard to cut the CI critical path

**Status:** READY rev 2, 2026-11-25 (rev 1 reviewed by Claude and Codex, both NEEDS CHANGES, folded
after checking each finding against the code; rev 2 reviewed READY by both). No plan ID yet (owner grants IDs). HIGH RISK under
`AGENTS.md`: CI gates, `ci-attest`, `WORKFLOW_SHA256`, and the staging-release gate inventory
(an external contract). Needs fresh independent reviews of rev 2; merge needs the owner.
Independent of the CPU-torch plan for `aquacast-release-image-proof`, which is not planned here.

## Problem
Main run `45607849353` (2026-11-25):

| Job | Wall | pytest |
|---|---|---|
| integration (rest) | 21.7 min | 20.6 min (JUnit 1227 s, 2617 tests, 201 files, 56 skips) |
| integration (heavy) | 14.2 min | 11.9 min (JUnit 712 s, 386 tests, 12 files) |
| aquacast-release-image-proof | 22.4 min | being cut to ~16 min by a separate plan |

After CPU torch, `rest` is the critical path. Legs are unbalanced by about 7.5 min.

## How shards work today (read from the code)
- `tools/integration_shards.py::NAMED_GROUPS` lists curated files: `performance` (1 file) and
  `heavy` (12). `rest` is computed: `tests/integration` minus all curated paths. No markers,
  no pytest-split. "Heavy" means "slowest files by JUnit, moved by hand".
- `--check-partition` runs only in the heavy leg. It proves exactly-once collection (selected
  nodes and all non-live files). It proves collection, not execution (rest has 56 skips).
- Measured fixed overhead per leg before pytest: about 57 s (container init 23, apt 16, setup-uv
  9, checkout 5, `uv sync` 3), plus about 25 s collection inside pytest. Heavy adds the 58 s
  partition proof and 19 s performance gate. The 58 s is a baseline, not fixed: `:362-399` runs
  two whole-suite collections plus two per group, so a third group raises collection
  subprocesses from 8 to 10. Session fixtures (migrated container, `committed_database.py:14`)
  are rebuilt per leg.
- Durations come from the uploaded `integration-junit-<shard>` artifacts (14-day retention); no
  new `--durations` step needed. Total test time 1918 s (709 + 1209) plus 19 s performance gate.

## Decision: three curated legs, no xdist
Balance model (derived from JUnit, estimates): heavy = 57 + 58 + 19 + 25 + T_heavy; others = 57 +
25 + T. With the file list below: heavy 586.8 s, mid 664.2 s, rest 667.1 s of test time, so about
12.5 min per leg against 21.7 now. Provisional: it ignores the extra proof collections and the
duplicated fixture setup, so treat 12.5 min as a lower bound, to be replaced by measurement.

| Option | Slowest leg | Runner min |
|---|---|---|
| Today | 21.7 (measured) | 36 |
| 2 legs rebalanced | about 18 | about 36 |
| 3 legs | about 12.5 (provisional) | about 38 (provisional; model gives +1.4 before proof/fixture costs) |
| 4 legs | about 9.5 | about 40 |

The saving of about 9 min is for the integration job only. If the image job lands at ~16 min
(a separate, unreviewed plan), the whole-workflow saving is nearer 6 min (22.4 -> ~16). Four
legs buy nothing once the image job is the critical path.

`pytest-xdist` inside a shard stays rejected (`cicd.md` "Integration shard balance";
`tests/integration/conftest.py` session `PostgresContainer` plus one shared `DATABASE_URL`;
Prefect home contention). Needs per-worker DBs: out of proportion for MVP.

## Candidate list (whole files; recompute from fresh JUnit before freezing)
Move `store/test_observation_unit_readers.py` (71.5 s) and `db/test_operator_role.py` (50.7 s)
(122.2 s) from `heavy` to `mid`; the 586.8/664.2/667.1 s totals include them. Then `mid` also
takes the largest 16 `rest` files, so `mid` has 18 files (about 25 files would put it near 14.5
min). The 16, with seconds in run 45607849353: store/test_daily_forecast_flow 63.3,
db/test_migration_forecast_publication_data_use 46.6, store/test_network_overview_performance 45.6,
store/test_station_history 44.8, store/test_forecast_rating_history 44.6,
db/test_migration_0033_camels_retire 38.2, db/test_migration_0039_basin_static_provenance 36.3,
db/test_bafu_guard_regressions 34.6, db/test_forecast_lineage_historical 28.4,
db/test_migration_provisional_discharge 23.6, db/test_forecast_lineage_roles 23.5,
db/test_provisional_discharge_roles 23.2, api/test_tables_protected_storage 22.9,
store/test_daily_forecast_one_shot_runtime 22.7, db/test_bafu_claude_followups 22.2,
api/test_network_overview 21.7.
- `test_network_overview_performance.py` (timing-sensitive, 45.6 s) lands in `mid`, no longer
  beside the `rest` neighbours; first runs must show it still passes (see Risks).
- Implementation basis (supersedes the 586.8/664.2/667.1 s provisional figures): means over
  runs 45607849353, 45613021117, 45640371082, 45663947921 give the same 16 `rest` files and
  heavy/mid/rest of about 612/653/652 s.
- Spread: the spike run `45608279136` gives the same recipe heavy/mid/rest = 600.2/710.2/685.6 s
  (+7 percent on mid), so run-to-run noise is real. Implementer reads at least these two runs
  plus one fresh run before freezing, records run ids, and re-balances if the legs differ by
  more than ~60 s.

## Change (one PR)
1. `tools/integration_shards.py`: add named group `mid` with the list above; `rest` stays the
   complement; `EXECUTION_GROUPS["mid"] = ("mid",)`. Heavy keeps `--check-partition` and the
   performance gate (smallest diff; Question 2).
2. `ci.yml` `integration-shard`: `shard: [heavy, mid, rest]` only. Aggregate unchanged.
3. `tools/ci_attest.py`: add `"integration (mid)"` to `MANDATORY_STEPS` (same steps as rest);
   then hand-update `WORKFLOW_SHA256` from the final `ci.yml` bytes (no value computed here;
   redo after any later `ci.yml` edit).
4. `tools/private_verification.py`: `Gate(name="integration-mid", timeout_seconds=1500)`, add to
   `integration` `needs` and `CI_JOB_GATES["integration-shard"]`;
   `tools/private_verification_gates.sh:281` label `integration-heavy|integration-mid|integration-rest`.
   Add a test that `shard="${1#integration-}"` yields `mid` for this label.
5. **Staging-release inventory (HIGH RISK, found by review, verified):**
   `tools/staging_release_transfer.py:61-72` `GATE_NAMES` must gain `integration-mid`;
   `staging_release.py:568,:621,:697` and `staging_release_transfer.py:500-501` derive from it,
   and `test_staging_release.py:899` ties it to `verification.GATES`. The receiver does not check
   names (comment at :56-60), so an old receiver accepts the new inventory; the sender fails
   closed if it is missing. Update `tests/fixtures/staging_release/v1-envelope.json` only if a
   test pins its gate list (it lists 12 gates today). `test_staging_release_transfer.py`
   :240-251 and :1497-1540 use `integration-mid` as an arbitrary unknown gate: after this change
   it is a known name, so rename it to a clearly synthetic one (e.g. `unlisted-extra-gate`) to
   keep testing "gate not in the inventory". Describe this to the owner as a boundary change.
6. Tests: `test_integration_shards.py` (`SHARD_IDS`/`GROUPS`/`EXECUTION_GROUPS` pins at
   :163-168, heavy/rest fixtures at :66-75 and :288-415, matrix-vs-`SHARD_IDS` test at :552);
   `test_ci_attest.py:41,:70,:255` hard-code 12 jobs (to 13; the missing-leg test must be
   parametrized over all 13); `test_ci_capacity_preparation.py:513` pins
   `["heavy","rest"]` (add `mid`); `test_private_verification.py:284-287` derives gates from
   the matrix and is the intended coupling. A "mid and heavy disjoint and files exist" test
   only if cheap and Docker-free (pure path checks).
7. Docs: `cicd.md` :718, :828-841, :921, :964-966, :992-1004 and :2019 ("heavy/rest gate
   names"), `docs/touchpoint-maps.md:2667`; add a dated durations note with run ids.

## Gate semantics
- The aggregate (`ci.yml:881-895`) rejects any non-success `needs.integration-shard.result`
  (failure, skipped, cancelled) but does not enumerate legs. Inventory protection comes from the
  matrix-vs-`SHARD_IDS` test and attestation's exact job inventory (`ci_attest.py:177`: a missing
  `integration (mid)` job fails attestation). Keep the aggregate unchanged; existing tests at
  `test_integration_shards.py:704` already cover `always()`/needs/skipped/cancelled.
- Coverage: `rest` is the complement, so new test files land in `rest` automatically. A renamed
  or deleted curated `mid` file fails loudly (`_validate_curated_file_path`, :86-94; a stale
  path breaks collection, :264). But the proof runs only in the heavy leg, so a `mid`-file
  problem surfaces as a heavy-leg failure; say so in the failure note in `cicd.md`.

## Risks
- Isolation: each leg has its own runner and PostGIS service; no shared state is added. File
  grouping changes, so latent state leaks between files can appear; investigate, never retry.
- Private verification runs gates serially (`private_verification.py:116-146,:471`; fixed port
  5432 at `gates.sh:287` is safe only because of that; job timeout 180 min): a third gate makes
  that path about 1-2 min slower, not faster. Accepted, stated.
- Timing-sensitive `test_network_overview_performance.py` and the performance gate: see above.
- List drift only unbalances; it never drops coverage. Rebalance by JUnit, not per PR.

## Acceptance (real CI; no number claimed beyond the runs)
1. Cold run of the PR: all three legs pass; record each leg's wall and step times.
2. At least two further full runs of all three legs (re-run and a later push): zero failures; any
   failure is a new state leak to investigate, not a retry.
3. The partition proof prints exactly `3036 selected nodes, 218 non-live files` (measured identically on origin/main
   `2cac7e00f` and this branch; the older 3004/217 was main at run 45607849353) unless the diff adds tests, in which case state the delta.
4. Slowest leg compared with 21.7 min; goal 15-16 min or better. Runner minutes from `gh run view`.
5. Repo-wide grep finds no stale pair: `integration-rest`, `integration (rest)`, `["heavy"`,
   `heavy/rest`, `GATE_NAMES`, with every hit either updated or justified.
6. Unit/tooling suites green after the final code change; ruff, format, changed-module pyright;
   `tools/integration_shards.py --check-partition` locally (Docker daemon runs on the dev host).

## Revert
Revert the PR: matrix, group list, attest steps, both gate inventories, tests, docs;
`WORKFLOW_SHA256` returns with `ci.yml`. If staging evidence was produced with `integration-mid`
in the envelope, the receiver still accepts it (names unchecked).

## Open questions for the owner
1. Is about +2 runner-minutes per run (provisional) acceptable for ~9 min less integration wall time
   (~6 min whole-workflow if the image job reaches ~16 min)?
2. Move the 58 s partition proof off heavy (lightest leg or its own job)? Recommendation: not now.
3. Automatic JUnit-driven balancer later? Recommendation: no at MVP.
4. Confirm the staging-release gate-inventory change (item 5) is acceptable as part of this PR.
