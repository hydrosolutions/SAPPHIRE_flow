# Faster GitHub Actions feedback within the existing budget

## Outcome

Developers should receive all required pull-request check results within ten minutes
of pushing a commit on at least 90% of runs. Improve iteration speed without reducing
validation coverage, weakening safety checks, or increasing CI spending. Make useful
pre-push feedback available locally using the same tools and rules as CI.

The owner confirmed this outcome on 2026-10-01. This is a standalone vision, not an
Effort in the live-dashboard Program. It can proceed alongside the Swiss and Nepal
visions on separate branches. Coordinate overlapping files and shared CI changes,
but do not serialize whole visions or make dashboard delivery depend on this work.
The dashboards' one-week deadline does not impose a separate deadline on this vision.

## Non-negotiable boundaries

- There is no additional budget. Work within the existing GitHub-hosted runner
  budget. More runner minutes, larger paid runners, new subscriptions and a new
  self-hosted runner are not implicitly authorized. Demonstrate the cost impact of
  changes, including setup, duplicated work, retries and artifact/cache use.
- Preserve the tests and required checks that protect each change today. Do not
  obtain a faster green result by silently skipping slow tests, weakening assertions,
  losing the Aquacast integration coverage, excluding a shard, moving a required
  pre-merge check until after merge, or changing branch protection.
- Local checks provide earlier feedback; they do not replace independent GitHub
  Actions validation. The final code state still needs the repository's complete
  required test and review gates before merge.
- Preserve security boundaries for private dependencies, tokens, logs and caches.
  Do not expose credentials or make an untrusted PR privileged to improve speed.
- This vision authorizes no deployment, model change, new dashboard work or general
  CI framework rewrite. Changes must address measured iteration bottlenecks.

## What was measured

These are baseline observations, not promised future runtimes. Refresh them before
implementation and use representative before/after evidence.

Recent successful `main` CI workflows took approximately 21–38 minutes from workflow
creation to completion. One inspected run is
[CI 36826980858](https://github.com/hydrosolutions/SAPPHIRE_flow/actions/runs/36826980858)
for PR 351 at `c44d83ccb3919262c73e937b3c3b26c48d160861`:

| Work | Observed duration |
| --- | --- |
| Lint job, including type checking | 65 seconds |
| Scripts unit-shard pytest step | 129 seconds |
| Services unit-shard pytest step | 626 seconds |
| Adapters/flows unit-shard pytest step | about 1,090 seconds |
| Integration pytest step | 565 seconds |
| Successful Aquacast installs on sibling shards | 39–48 seconds |
| Failed rest-shard Aquacast install | ten-minute timeout, before tests started |

The failed job passed on retry without a code change. Dependency-download outliers
and uneven test execution are separate problems; a successful retry does not solve
either systematically. Cache-save collision warnings also appeared. Investigate
whether they affect usable caches rather than assuming the warnings explain latency.
The separate dependency-safety workflow was still pending when the main CI rerun
finished; the final PR verdict must account for every required workflow, not just
one named `CI`.

## Existing design to reuse

Read `.github/workflows/ci.yml`, the other required workflows,
`docs/standards/cicd.md`, `pyproject.toml`, `tests/conftest.py`, and the relevant tests
before choosing mechanisms. Relevant existing behavior includes:

- Four unit shards (`scripts`, `services`, `adapters-flows`, `rest`), each using
  `pytest -n auto`, with centralized selection in `tools/unit_shards.py`.
- `tests/unit/tools/test_unit_shards.py` proves the shard selections form an exact
  partition. The catch-all includes new directories and root-level test files.
  Preserve exhaustive coverage if the partition strategy changes.
- Combined coverage is assembled from all shards and remains informational. Missing
  shard output must not silently become a successful complete report.
- A deliberately sequential ordering-leak regression runs once, on the scripts
  shard. Parallelization must not erase the ordering behavior it tests.
- Unit jobs install the Aquacast extra and prove its real discovery test ran when
  credentials are available. Existing absent-secret handling distinguishes supported
  Dependabot degradation from unsafe missing coverage.
- Dependency caching, bounded network/install steps and cancellation of superseded
  PR runs already exist. Diagnose their actual effect rather than adding duplicates
  or treating larger timeouts as an optimization.
- Local pre-commit/pre-push checks already cover formatting, lint, secrets and the
  type-checking ratchet. Reuse or simplify existing entry points rather than creating
  a parallel definition of what a valid change means.

Plans 185 (dependency coverage), 201 (test isolation), 300 (local parallelism), 309
(step bounds) and 319 (CI sharding) record prior constraints and decisions. Read the
actual source and current tests as well as those plans; reuse completed work. Their
historical implementation choices are not an excuse to preserve a proven bottleneck,
but the safety properties they protect must survive any replacement.

## Local iteration experience

A contributor should have a clear, reproducible way to run the useful checks for a
change before pushing, plus the complete locally runnable validation when needed.
Use the project-managed environment and the same configurations as CI. Make the
scope explicit: a focused local pass must never look like a full-suite pass. State
which checks require Docker, private dependencies or external CI facilities, and
report missing prerequisites instead of silently omitting their coverage.

Prefer changes that reduce repeated feedback cycles as well as cloud runtime.
Choose the commands and implementation from repository evidence; this vision does
not prescribe a new runner, test selector or scheduling framework.

## Evidence of success

Measure push-to-verdict time across all required PR checks, including runner queue,
setup, dependency acquisition, tests, scans and aggregation. If GitHub's available
event timestamps require a proxy for push time, state that limitation and do not
quietly report only test-step duration as end-to-end latency.

Establish a reproducible representative before/after sample before claiming the
90% target. Include normal code and dependency changes, cache-cold and cache-warm
conditions, and failed downloads/retries; exclude deliberately cancelled obsolete
runs from the completed-verdict sample but disclose them. Do not select only green,
warm-cache runs or reset the latency clock when retrying the same commit. Record
sample size, measurement window, check inventory, at-least-90%-within-ten-minutes
result and runner-cost evidence. A small sample or one fast run is preliminary
evidence, not proof of sustained performance.

Also verify:

- the collected test coverage and required check coverage remain complete;
- a real failing test still produces a failed required result;
- dependency/auth failures cannot masquerade as successful tests;
- local commands give useful results and clearly identify their scope;
- the new path works on GitHub Actions, not only on a developer's faster machine.

If the ten-minute target cannot be achieved within the fixed budget and preserved
gates, report the measured shortfall and bottleneck. Do not claim completion, spend
more money, or weaken validation to make the target appear satisfied. The owner
must decide any outcome-level trade-off.
