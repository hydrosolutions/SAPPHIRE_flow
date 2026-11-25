---
status: DRAFT
---
# Integration shards: add a third curated shard to cut the CI critical path

**Status:** DRAFT, 2026-11-25. No plan ID yet (owner grants IDs). HIGH RISK under `AGENTS.md`
(CI gates, `ci-attest`, `WORKFLOW_SHA256`). Needs independent Claude and Codex plan reviews
before implementation; merge needs the owner. Not reviewed yet. Companion to, and independent
of, the CPU-torch plan for `aquacast-release-image-proof`; not planned here.

## Problem
Main run `45607849353` (2026-11-25), wall time per job:

| Job | Wall | Of which pytest |
|---|---|---|
| integration (rest) | 21.7 min | 20.6 min (junit suite 1227 s, 2617 tests, 201 files) |
| integration (heavy) | 14.2 min | 11.9 min (junit suite 712 s, 386 tests, 12 files) |
| aquacast-release-image-proof | 22.4 min | being cut to ~16 min elsewhere |

Once CPU torch lands, `rest` is the critical path of the whole workflow. The two legs are
unbalanced by about 7.5 min.

## How shards work today (read, not assumed)
- `tools/integration_shards.py::NAMED_GROUPS` lists curated files: `performance` (one
  benchmark file) and `heavy` (12 files). `rest` is computed: `tests/integration` minus every
  curated path (`--ignore=`). No markers, no pytest-split.
- "Heavy" means "curated, moved out by timing", not a kind of test: its files are the slowest
  by JUnit (largest `test_skill_data_use_isolation.py`, 136 s over 60 tests).
- `--check-partition` (heavy leg only, 58 s in the measured run) collects the suite whole and
  per group, and fails on any node in no shard, in two shards, or with a different count.
  Tests cannot silently drop out. `tests/unit/tools/test_integration_shards.py` pins
  `SHARD_IDS == ("heavy", "rest")` and `GROUPS` (:163-168).
- The `integration` aggregate uses `needs.integration-shard.result != success`, which is
  `failure` if any matrix leg fails, is cancelled or is skipped, so a missing leg already
  fails the gate. A new matrix entry is covered with no aggregate change.
- Fixed overhead per leg, measured (`rest`): container init 23 s, checkout 5 s, setup-uv 9 s,
  apt 16 s, `uv sync` 3 s, about 57 s before pytest. `heavy` adds the partition proof (58 s)
  and the performance gate (19 s): pytest starts at 2 min 15 s. Collection inside pytest
  is a further ~25 s per leg (rest: 1238 s wall vs 1209 s summed test time).
- Per-test durations exist: each leg uploads `integration-junit-<shard>` (14-day retention).
  I downloaded both for the run above (`gh run download`); no new `--durations` step is needed.
- Test-time distribution in `rest`: 201 files, 1209 s total. The largest 20 files sum to 616 s
  (51 percent); 109 files take under 2 s each. Many small files means whole-file moves
  balance well. Total work across legs is 1918 s (709 + 1209) plus 19 s performance gate.

## Decision: three curated shards, not pytest-xdist, not more
Balance model (all numbers derived from the JUnit above, estimates not measurements):
heavy leg = 57 s setup + 58 s proof + 19 s gate + 25 s collect + T_heavy, other legs =
57 s + 25 s + T. Equal wall with three legs gives T_heavy about 590 s and T about 665 s each
(sum 1918 s). That is about 12.5 min wall per leg, versus 21.7 today.

| Option | Estimated slowest leg | Runner minutes | Verdict |
|---|---|---|---|
| Today (2 legs) | 21.7 min (measured) | 36 | baseline |
| Re-balance 2 legs by duration | about 18 min | about 36 | misses the 15-16 min goal |
| **3 legs** | about 12.5 min | about 38 | recommended |
| 4 legs | about 9.5 min | about 40 | pointless: aquacast-image (~16 min) becomes the critical path |

The 2-leg estimate: (1918 + 19) / 2 = 969 s pytest plus ~140 s overhead. A third leg costs
about 2 extra runner-minutes per CI run (one more ~82 s setup plus collection), against a
saving of about 9 minutes of wall time. Estimates; acceptance below uses real runs.

`pytest-xdist` inside a shard is rejected, as `docs/standards/cicd.md` ("Integration shard
balance") already says. Evidence in the code: `tests/integration/conftest.py` has a
session-scoped `PostgresContainer` and exports `DATABASE_URL`, and the CI leg also exports
one shared service DB. Several workers would share schema state and Prefect home
(`project_prefect_home_test_contention`); isolating them needs a per-worker database and
home, which is a test-infrastructure change with its own flake risk. Not proportionate.

## Change (one PR)
1. `tools/integration_shards.py`:
   - Add named group `mid` (curated list, placed between `heavy` and the computed `rest`);
     `SHARDS` and `build_shards` need no structural change because `rest` stays the computed
     complement of every named group.
   - Move `test_observation_unit_readers.py` (71.5 s) and `db/test_operator_role.py` (50.7 s)
     from `heavy` to `mid` so heavy drops from 709 s to about 587 s (it carries the proof
     and gate). Fill `mid` with the largest `rest` files, largest first, until its summed
     JUnit time is about 665 s (the top 20 give 616 s; about 25 files). The implementer
     takes the list from fresh JUnit artifacts and records the run id in the diff.
   - `EXECUTION_GROUPS`: add `"mid": ("mid",)`. Keep the whole-file moves: no test, case or
     parameter is dropped; `slow` and `live` exclusions are unchanged.
   - Keep the performance gate and `--check-partition` in `heavy` (lowest-risk placement).
     Open question 2 covers moving the proof.
2. `.github/workflows/ci.yml` (`integration-shard`): `shard: [heavy, mid, rest]`. Nothing else.
   `timeout-minutes: 25` stays. The leg-specific `if: matrix.shard == 'heavy'` steps stay.
3. `tools/ci_attest.py`: add `"integration (mid)"` to `MANDATORY_STEPS` with the same steps as
   `rest` (Checkout exact source, Set SCM version override..., Run integration tests).
   Update `WORKFLOW_SHA256` by hand after the final `ci.yml` bytes (do not compute it here;
   recompute from the committed file and re-check after any later edit to `ci.yml`).
4. `tools/private_verification.py`: add `Gate(name="integration-mid", timeout_seconds=1500)`,
   include it in `integration` `needs` and in `CI_JOB_GATES["integration-shard"]`. In
   `tools/private_verification_gates.sh:281` extend the case label to
   `integration-heavy|integration-mid|integration-rest`.
5. Tests: `test_integration_shards.py` (`SHARD_IDS`, `GROUPS`, `EXECUTION_GROUPS`, heavy/rest
   pinning tests at :129-170, fake collectors at :66-75 and :288-415 gain `mid`);
   `test_ci_attest.py` (job inventory at :101 and :193 name-derived; add `integration (mid)`);
   `test_private_verification.py:284-287` already derives the gates from the ci.yml matrix, so it
   fails until the gate is added, which is the intended coupling.
6. Docs: `docs/standards/cicd.md` lines 718, 828-841 (history paragraph stays), 921, 964-966,
   992-1004 ("TWO parallel shard jobs", "two-leg matrix", step table), replacing "two" with
   "three" and adding a dated durations note with the run id.

## Gate semantics (must not weaken)
- The aggregate stays `needs: [integration-shard]` with `!= success` failing. A skipped, cancelled
  or failed leg fails it. Add a test asserting the aggregate step text is unchanged.
- ci-attest builds its expected inventory from `MANDATORY_STEPS`; a missing `integration (mid)`
  job already fails attestation (`set(selected) != set(MANDATORY_STEPS)`), keep that test.
- `--check-partition` (exactly once) is the coverage proof for the new shard too. Add a unit
  test that the curated `mid` and `heavy` files are disjoint and exist.

## Risks and flakiness
- Isolation: each leg has its own runner and PostGIS service, so splitting adds no shared-state
  risk. Ordering inside a leg changes (different files run together); files that leaked state
  into the next file in `rest` could surface as new failures. Mitigation: acceptance runs
  below, and any failure is investigated, not retried.
- Timing-sensitive tests (`test_network_overview_performance.py`, 45.6 s; the performance
  gate) assert on durations; moving files changes machine load. Keep both where they run
  alongside the same neighbours or note the first-run result.
- Staleness: the curated list drifts as tests are added; `rest` absorbs new files so drift only
  unbalances, never drops coverage. Rebalance by JUnit (existing cicd.md rule), not per PR.

## Acceptance (real CI, no numbers claimed beyond what runs show)
1. Cold run on the PR branch: record each leg's wall and step times; slowest leg target
   15-16 min or better (estimate about 12.5).
2. At least two further runs (re-run and a following push), to show the spread; record
   each leg's wall time. No new failure in any leg; none retried.
3. The partition proof passes and prints the same total selected-node count as main.
4. `ci-attest` and `private_verification` unit tests pass; full suite green after the final
   code change; local: ruff, format, changed-module pyright, `tools/integration_shards.py
   --check-partition` (needs Docker locally; the daemon is available on this host).
5. Runner-minute delta is read from the run (`gh run view`), expected about +2 min per run.

## Revert
Revert the PR (single commit set: matrix, group list, attest steps, gate, tests, docs).
`WORKFLOW_SHA256` returns to its previous value with the file. No data, schema or release
identity involved.

## Open questions for the owner
1. Is the extra ~2 runner-minutes per run acceptable for ~9 minutes less wall time?
2. Move `--check-partition` (58 s) to the lightest leg or its own tiny job to shave ~1 min
   from heavy? Recommendation: leave it for now (smallest diff, no new job in attestation).
3. Is an automatic balancer (JUnit-driven LPT in the tool) wanted later? Recommendation: no,
   a curated list plus the exactly-once proof is enough at MVP.
4. Run the fresh-JUnit refresh step locally or from CI artifacts only (14-day retention)?
