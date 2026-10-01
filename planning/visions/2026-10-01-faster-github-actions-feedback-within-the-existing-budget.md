# Faster CI and conflict-free parallel delivery within the existing budget

## Outcome

Developers should receive all required pull-request check results within ten minutes
of pushing a commit on at least 90% of runs. Improve iteration speed without reducing
validation coverage, weakening safety checks, or increasing CI spending. Make useful
pre-push feedback available locally using the same tools and rules as CI. Independent
PRs should not require routine manual conflict resolution solely because they each
change shared release-version metadata.

The owner confirmed the CI outcome on 2026-10-01 and subsequently approved extending
this same vision to remove routine version-file conflicts in parallel PRs, including
reconsidering when versions are assigned. This is a standalone vision, not an Effort
in the live-dashboard Program. It can proceed alongside the Swiss and Nepal
visions on separate branches. Coordinate overlapping files and shared CI changes,
but do not serialize whole visions or make dashboard delivery depend on this work.
The dashboards' one-week deadline does not impose a separate deadline on this vision.

## Non-negotiable boundaries

- There is no additional budget. Work within the existing GitHub-hosted runner
  spending allowance; do not buy larger paid runners, new subscriptions or a new
  self-hosted runner. Establish the current billing allowance and usage from
  authorized account records, not assumptions about free minutes or repository
  visibility. Runner allocation may change within that envelope. Demonstrate the
  effect on billable usage and total runner-minutes, including setup, duplicated
  work, retries and artifact/cache use; a faster run is not permission to spend more.
- Preserve the tests and required checks that protect each change today. Do not
  obtain a faster green result by silently skipping slow tests, weakening assertions,
  losing the real Aquacast shim/model test coverage, excluding a shard, moving a required
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
| Image build, smoke check, vulnerability scan and SBOM job | 233 seconds |
| Wheel-only dependency guard | 17 seconds |
| Combined unit-coverage job on the successful retry | 24 seconds |
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

Plans 185 (dependency coverage), 201 (test isolation), 309 (step bounds) and 319
(CI sharding), plus `docs/conventions.md`, record prior constraints and decisions.
Plans 185, 201 and 319 are archived. Some workflow comments attribute parallelism
to Plan 300, but that plan number now identifies the DHM observation adapter; do
not use that ambiguous citation as the parallelism specification. Read the actual
source and current tests as well as the applicable plans; reuse completed work. Their
historical implementation choices are not an excuse to preserve a proven bottleneck,
but the safety properties they protect must survive any replacement.

## Parallel delivery and release-version conflicts

The owner reported recurring conflicts in `pyproject.toml`, `uv.lock` and
`src/sapphire_flow/__init__.py` while merging independent PRs, including the Nepal
hosting and global-agent-settings changes (PRs 351 and 355, now merged). Inspection
of `main` at `d739c67ac35b8dcc4c3d87aa3aadcb725f6db90a` confirms the coupling:

- `AGENTS.md` requires a patch bump in every code commit.
- `bump-my-version` writes the project and runtime versions, then invokes `uv lock`
  to update the root package's version in the lockfile.
- `.github/workflows/tag-main.yml` tags the version already recorded on `main`;
  it does not centrally assign a version to each incoming change.

This shared metadata churn is distinct from genuine incompatible code or dependency
changes. Remove the routine release-metadata conflict without suppressing legitimate
conflicts. Do not solve it by making contributors serialize independent PR development
or by repeatedly asking the owner to reconcile these three files.

The owner authorizes reconsidering the per-code-commit bump policy. Investigate
assigning versions at merge or release time instead, but choose the mechanism from
repository evidence; this vision does not prescribe a release tool or a merge queue.
A short serialized version-assignment operation may be appropriate; whole visions
must remain independently developable. No additional spending or merge authority is
granted. The existing bump rule remains effective until an approved, reviewed
implementation changes the policy and its tooling together.

Preserve reproducible dependency resolution and traceable package/image/release
identity. Inspect the version consumers in packaging, runtime reporting, image
build/deployment conventions, `tag-main.yml` and their tests. Define how versions
are assigned uniquely, how builds map to source commits, and how repeated or
concurrent automation avoids conflicting tags or silently reusing an identity for
different release contents. Keep existing tags and history intact; do not make
feature branches publish release tags. A tag remains an identifier, not a substitute
for passing validation or owner approval.

Dependency changes still require intentional review and a lockfile consistent with
the accepted constraints. Never resolve a genuine dependency conflict by blindly
choosing one branch's lockfile, dropping the other change, or resolving unrelated
packages without explanation. Coordinate updates to affected contribution rules,
release documentation, tests and automation so that old and new version policies
do not coexist as contradictory instructions.

Acceptance must exercise at least two independent PR-like branches from the same
base through the proposed integration/release path. Land them in both orders in a
controlled test and demonstrate no manual conflict caused only by version metadata,
correct dependency locks, and traceable release identities. Also test compatible
concurrent dependency edits, a genuinely incompatible dependency case that reports
a meaningful conflict instead of silently discarding a constraint, and repeated or
concurrent version-assignment execution. These are verification cases, not authority
to merge real PRs, mutate protected branches or publish test release tags remotely.

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

Read the live GitHub branch-protection/ruleset requirements and the workflow
triggers to establish the required-check inventory; workflow names alone are not
proof of the merge gate. If account permissions prevent that read, report the gap.
Preserve that inventory and the current validation coverage.

Build a complete per-job and critical-path baseline across the required workflows,
including image build, smoke checks, vulnerability scans, SBOM generation and any
retry waits, wheel guard, coverage aggregation and dependency-safety. The table
above is an observed starting sample, not the complete performance baseline.
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
- the new path works on GitHub Actions, not only on a developer's faster machine;
- independent PRs no longer need routine manual version-metadata conflict repairs,
  while real dependency conflicts and release-identity failures remain visible.

If the ten-minute target cannot be achieved within the fixed budget and preserved
gates, report the measured shortfall and bottleneck. Do not claim completion, spend
more money, or weaken validation to make the target appear satisfied. The owner
must decide any outcome-level trade-off.
