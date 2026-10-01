---
status: DRAFT
created: 2026-10-01
title: Faster CI feedback and conflict-free release metadata
---

# Faster CI feedback and conflict-free release metadata

Vision: `planning/visions/2026-10-01-faster-github-actions-feedback-within-the-existing-budget.md`

## Outcome and boundaries

All required PR verdicts within ten minutes on at least 90% of representative
runs, without more spending or lost validation. Independent code PRs must not
conflict solely because each bumped release metadata. Improve the existing local
`uv run check` entry point rather than introduce a second validation framework.

The CI/local-feedback slice has completed independent Claude and Codex design
reviews without outstanding findings. Release identity remains proposed pending
resolution and review of its supply-chain findings. Follow current AGENTS.md and
the invoked global implement-vision skill; historical READY/status procedures are
not execution authority. Hold all implementation PRs for owner approval and merge.
No deployment, branch-protection change, paid runner, remote test tag, or
dependency-policy bypass.
The current per-code-commit bump policy remains effective until its reviewed
replacement lands. Do not change models or the ForecastInterface boundary.

## Reconstruction and initial evidence

Discovery target: `c93e6a92b11794a5529e0a40bd4f84addef70e60`. Before implementation,
refreshed and fast-forwarded to `origin/main` at `eb806843` (PR 361 policy cleanup).
The published vision is unchanged. The new policy retires the separate
orchestrator/readiness layer while preserving independent review and owner merges.
The standalone vision is published through PR 356. No open PR was returned during
inspection. The existing authoring clone at `.worktrees/visions/faster-ci-feedback`
is clean on `docs/faster-ci-feedback` and is not implementation work to overwrite.
The new implementation worktree is `.worktrees/visions/faster-ci-implementation`
on `feat/faster-ci-feedback`. Existing four-shard CI and tagging are merged
prerequisites, not evidence that this vision is delivered. No implementation of
this vision was identified; its outcomes remain outstanding. No abandoned
implementation was identified.

Read-only GitHub evidence on 2026-10-01:

- `GET repos/hydrosolutions/SAPPHIRE_flow/branches/main/protection` returned
  404 with `Branch not protected`; branch rules returned `[]`. Preserve the
  documented validation gates despite the absence of enforced GitHub rules.
- Organization billing budgets report Actions cap $0 with
  `prevent_further_usage=true`. The owner supplied billing-page records showing
  current-month Linux usage of 1,153 minutes and last-month Linux usage of
  26,739 minutes, both at $0 net; last-month Actions storage and other runner
  types also show $0 net. Repository API confirms PUBLIC. Every current workflow
  uses standard `ubuntu-latest`. GitHub's current
  [Actions billing documentation](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
  states standard GitHub-hosted runners are free for public repositories.
  Together these establish the current runner envelope without assuming a
  private-repository minute allowance. No larger paid runners, billing change,
  extra paid storage or other new expenditure is authorized. Continue reporting
  runner-minutes, cache/artifact storage and actual net charges.
- Recent PR job records confirm unit imbalance, not just dependency setup:
  PR CI 36842575461 spent 19.7 minutes in services, 13.4 in adapters/flows,
  4.9 in scripts, 4.8 in rest, and 8.7 in integration. Its CI verdict arrived
  about 20 minutes after workflow creation.
- The completed post-shard baseline covers 107 PR heads: 80 settled, 8 obsolete
  cancelled, and 19 incomplete. Of the 80 settled heads, 23 completed all gates
  within ten minutes (28.7%). Of the 63 successful settled heads, 19 completed
  within ten minutes (30.2%). This closes the T1 measurement baseline only; it
  is not target acceptance and it does not restart timing after retries.
- Local diagnostics (same target source, Aquacast installed, two workers) passed
  1,334 services tests in 185.71 seconds without coverage, 337.18 seconds with
  the current coverage core, and 125.29 seconds with sysmon. Both coverage runs
  recorded exactly the same covered-line sets. These local results are not proof
  of end-to-end Actions performance. See D1 for the identified quadratic fake.

Raw read-only timing receipts are retained locally under
`.worktrees/visions/faster-ci-evidence`, not committed. Organization billing
records must not be published in full. Plans 185, 201, 309 and 319 remain source
constraints to inspect in full before deciding optimizations.

## Decisions for review

### D1 — First remove measured work, not tests

Keep the four current shards, runner class, test selections, fixture sizes,
assertions, credential guards and sequential canary. Do not add runners yet.

The first change is the observation fake in `tests/fakes/fake_stores.py`.
`FakeObservationStore.store_observations` and collision validation repeatedly call
`_by_natural_key`, which scans all stored rows. A cProfile run of the two-station,
two-parameter group hindcast test made 373.6 million calls; 48.5 of 49.5 test-body
seconds were in fake observation insertion, including 184.6 million UUID equality
calls. This is quadratic fixture setup, not model computation or a Prefect wait.
The earlier background logging errors are not evidence of the bottleneck's cause.

Build a typed natural-key lookup once per batch from the current observation
mapping; use it for collision preflight and upsert and update it as the batch is
applied. Prefer this batch-local index over a second persistent cache: tests can
seed the existing fake directly, and deletion/QC paths must not leave stale cached
objects. Preserve current duplicate-key ordering, stable stored IDs, raw-write
idempotence, QC reset, rating provenance, delivery collisions and atomic refusal.
Handle repeated IDs/natural-key changes without a stale lookup inside the batch.
Do not alter the production store, model or scientific computation. Do not reduce
the 400-day test fixtures to achieve a quicker pass.

Second, use coverage.py's `sysmon` measurement core for the existing Python 3.12
statement-coverage unit jobs. The installed coverage 7.13.4 supports it. Native
paired local runs of all 1,334 service tests, both with two workers and coverage,
took 337.18 seconds (`ctrace`) and 125.29 seconds (`sysmon`). CoverageData comparison
found exactly the same 269 files and 23,195 covered lines, with no differences.
These were sequential warm-environment experiments, not a controlled Actions
benchmark; a separate one-test cProfile process overlapped the sysmon run.

Set the core explicitly in the unit-job environment, not globally for application
processes. Keep `--cov=src/sapphire_flow` and all coverage aggregation. Sysmon on
Python 3.12 cannot support branch coverage, dynamic contexts or every concurrency
plugin; the current suite requests statement coverage and xdist subprocesses.
Verify the actual active core and absence of fallback warnings on Actions and
compare all shards' coverage before accepting. Future incompatible coverage
configuration must produce an explicit diagnostic, not silently lose measurement.
No coverage.py dependency upgrade is needed solely for this switch.

Add per-test JUnit durations to the existing shard and integration runs, with
small bounded-retention artifacts, to diagnose remaining critical paths. Count
that storage in after-cost evidence. Retain per-step timings for non-test work.
If these changes leave the target unmet, record the measured shortfall and revise
this plan for another reviewed optimization; no automatic extra sharding, new
cache, timeout expansion or integration parallelization is authorized here.

### D2 — Assign semantic versions at an explicit release, not every merge

Use conventional tag-derived package versions (`setuptools-scm` with setuptools
PEP 517 builds). Normal source commits stop editing the three shared version
fields. The project declares a dynamic version, the generated version module is
untracked, and the editable root lock entry has no static release-version field.
Keep exact locked dependency constraints and uv; changing a build backend is not
permission to update unrelated runtime packages. uv remains the only package
manager; PEP 517 backend selection is a separate concern.

**Backend and lock evidence (2026-10-01):** an isolated package under
`.worktrees/visions/versioning-experiments/` used the repository's uv 0.11.7.
The current backend uv_build 0.12.3 rejected `dynamic = ["version"]` with
`missing field version`. The uv-dynamic-versioning 0.14.1 package explicitly
states it does not support uv_build; its documented backend is Hatchling.
Keeping uv_build would therefore require a custom pre-build stamping path, with
extra obligations to prevent unstamped local/Docker builds and lock rewrites.
Prefer setuptools-scm's standard generated version module and sdist metadata
rather than implement that custom path. Hatchling is a viable alternative, not
required to avoid a backend change; no extra package manager is proposed.

A native fixture using setuptools 84.0.0 and setuptools-scm 10.3.4 resolved an
editable root lock entry with **no version field**. Two sibling commits installed
as `0.1.2.dev1+g9291b861c` and `0.1.2.dev1+g18a2fc7c5`, with byte-identical root
locks. Integrating them in both orders succeeded and `uv lock --check` passed.
Thus per-commit dynamic-version lock churn was not reproduced with pinned uv.
This is a small generic-package experiment, not acceptance of the real project:
T3 must repeat the test with actual package metadata and dependency edits. Pin
and review the final build requirements; do not infer that every uv/backend
version has this behavior. The local receipt is `versioning-probe-results.json`
under the discovery evidence directory.

**Shallow CI contract:** ordinary PR/main validation builds are non-release builds.
Derive `0.dev0+g<FULL_CHECKED_OUT_SHA>` from the actual checkout and pass it via
`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW` to every project-install/build
step, including the Docker build's matching metadata input. Do not substitute the
PR author's head SHA for GitHub's tested merge SHA. Verify the format and checkout
identity before using it. This mode deliberately does not infer a release version
from missing tags and needs no additional history/tag fetch in unit, integration,
coverage or image-validation jobs. Existing lint/dependency-safety base-history
requirements remain unchanged. Local full-history development may use normal SCM
derivation; both modes are explicitly non-release.

Include the package-specific version override environment variable in uv's build
cache keys alongside commit/tags and pyproject metadata. An isolated depth-one,
no-tags checkout installed successfully as the exact full-SHA development version
with those keys and no lock change. Release builds use a separate verified-tag
path and never inherit a development pretend-version variable unchecked. Measure
all checkout/build/cache overhead on Actions after T3; the local warm builds
(~one second in the fixture) are not an Actions cost claim. Only the explicit
release path needs release-tag/history validation; never deepen every matrix
checkout merely to recover a semantic development version.

Retire the automatic push-to-main tag action. The replacement is a **local release
command explicitly invoked by the owner using their existing Git credentials**;
there is no new GitHub Actions release trigger, bot token or protected-environment
configuration. Owner authorization remains an operational rule under existing Git
permissions, not a claim of a newly enforced owner-only GitHub ACL. Other existing
writers' Git permissions are not expanded or restricted by this work.

Run reviewed release tooling from a clean checkout of trusted `origin/main`.
Treat requested source SHA and canonical `X.Y.Z` as data. Fetch and verify remote
main/tag state before mutation; require the full source SHA to be on main. Do not
execute code from the requested target or build packages inside the tag-mutation
command. Builds are separate, unprivileged operations. Do not persist credentials
in worktrees or receipts. Only the explicit publication operation calls Git push,
using the owner's existing credential mechanism; implementation/verification uses
isolated local repositories and never publishes a production tag.

For the single release line, compare canonical versions numerically: a new version
must exceed every existing canonical release version, and a source target may not
already carry a different canonical release tag. Repeating the exact version/SHA
pair is success. Reusing a version for a different target, decreasing the version,
or introducing a second canonical tag on the target is an error. Validate numeric
boundaries such as 0.1.999 to 0.1.1000. Existing inaccurate/gappy historical tags
are grandfathered and never moved. No backport release-line policy is introduced.
Concurrent publication must not bypass these checks: test same-name races and
different-name races through the proposed local publication path; fail and require
a fresh validated request when remote state changes, rather than blindly retrying
with an unchecked version. Specify the atomic remote-state mechanism before
implementing this command; Git's per-tag uniqueness alone does not enforce global
release ordering. This remains a release-design review item, not scope for the
first CI/local-feedback PR.

Patch releases are normal; minor/major assignments require the owner's explicit
request. Feature PR merges neither edit release-version files nor publish tags.
The implementation agent does not invoke publication.

Development package versions carry a Git revision suffix; release versions derive
from the verified release tag. Configure full source-revision provenance and
uv's Git commit/tag cache keys so an editable install is refreshed after source
or tag changes. `sapphire_flow.__version__` continues to be a string and reports
the built package identity. No runtime Git subprocess or network access is needed
inside deployed containers. Dirty/shallow/missing-history inputs must not silently
become release builds. Dirty development versions are explicitly non-release and
must not be used as operational release provenance. A release wheel/sdist contains
the generated version and rebuilds without `.git`.

The Docker build path must receive a verified package version and full source
revision before excluding `.git` from its context. Preserve dependency-layer
caching by installing locked dependencies without the project first, then copy
real source and install the actual project with matching version metadata. Do not
build the versioned project from the current dummy `__init__.py`. Preserve current
BuildKit-secret lifetime and privilege boundaries. Fail release builds on absent
or inconsistent identity inputs. Record OCI source revision and version; use the
immutable image digest to distinguish rebuilt OS layers, since the existing apt
upgrade means a version or source SHA alone is not byte-for-byte image identity.
Keep CI image tags commit-specific and never publish images as part of this work.

Update the operator build recipe to derive these inputs from the selected clean
source/tag. Preserve runtime consumers (onboarding records, forecast evidence and
BAFU user agents). The complete packaging, Docker and workflow diff needs the
owner-commissioned additional security/supply-chain review as well as the normal
Claude/Codex reviews. No deployment is included.

### D3 — Reuse the local entry point

Keep `uv run check` with no arguments as the existing ruff-only check, but label
its result as lint-only. Add only an optional focused-test path list, run after
the same ruff commands, and report the exact selected scope and failures. Do not
build a generic task runner or duplicate CI logic in Python.

Document the full local validation recipe beside the existing CI/local parity
table: environment sync with the private extra, real discovery proof, pre-commit,
pre-push pyright ratchet, dependency classifier and map-contract check against an
explicit base, complete default pytest suite, wheel guard and Docker
build/smoke/scan/SBOM checks. Use the same tools, configurations, exclusions and
severity thresholds as CI. Include explicit fail-fast prerequisite checks and
expected results for Docker/Postgres, system libraries, private packages, base
refs, shellcheck, Trivy and Syft; never label a missing prerequisite or silently
skipped private test as full validation. Keep scheduled live/deployment tests
separate from the default PR scope. Identify CI-only publication facilities.
Neither the helper nor the recipe replaces independent Actions validation.

### D4 — Evidence and delivery split

Use two coherent implementation PRs after plan readiness: first the measured CI
and local-feedback improvements (T1, T2, T4), then release identity (T3). T5 covers
the combined vision after both. Keep the old bump rule for the first PR. The
second PR must transition policy and tooling together; its final review must
specify the migration release identity and reconcile any still-open old-policy
branches without adopting unrelated lock changes. Do not claim the vision complete
when only the faster-tests slice lands.

Freeze before/after selection as all eligible PR heads in an explicit observation
window, not selected successful workflows. Target at least 30 completed heads in
each sample, categorized by code/dependency changes and observed cold/warm cache
state. If traffic yields fewer, report preliminary evidence rather than a sustained
90% result. Failures and all attempts count from the first workflow-creation proxy;
deliberately cancelled superseded heads are disclosed separately. Missing workflows
or incomplete API records are unknown/pending, never successful zero-duration runs.
Do not synthesize costly cloud traffic merely to fill the sample without approval.

**Review boundaries:** current AGENTS.md requires independent Claude/Codex plan
and patch reviews and an additional owner-commissioned relevant review for
high-risk work. All three design passes were completed; the release-design
findings remain technical prerequisites for T3. Resolve and review them before
its implementation. Review complete implementation diffs after implementation;
do not treat design reviews as patch proof. No separate orchestrator/status gate
is created. Owner approval and merge remain mandatory.

## Tasks

### T1 — Establish complete feedback and spending evidence

**Outcome:** a reproducible before/after sampling procedure and baseline across
CI plus dependency-safety, with every documented gate included.

**In:** read-only GitHub requirements/rules, workflow triggers, run/job/step logs,
cache and artifact records, authorized billing records, relevant archived plans
and current tests. Inspect version consumers in package/runtime reporting,
Docker/Compose deployment commands, `tag-main.yml` and their policy tests.
**Out:** changing account budgets, protections, permissions or workloads.

**Verification:** record observation window, sample size, normal/dependency changes,
cold/warm evidence and uncertain cache state, failed downloads, all retry waits,
queue/setup/test/build/smoke/scan/SBOM/aggregation times and separately disclosed
cancelled runs. Group workflows by PR head and keep first-event timing across
attempts. Missing jobs or absent checks are incomplete evidence, never zero time
or success. Reconcile actual executed attempts before totaling runner minutes;
include storage/cache charges and setup duplication. Preserve the verified public
standard-runner envelope and check current net charges against account records.
Freeze the sampling method before after-runs.

**Pre-change:** current sampled PR verdicts exceed ten minutes; this is not yet
proof of a population-wide rate. Discovery itself changes no behavior.

### T2 — Reduce measured CI critical paths without reducing gates

**Outcome:** smaller end-to-end and aggregate execution cost within the verified
allowance; unchanged test and required-check protection.

**In:** D1's changes to `tests/fakes/fake_stores.py`, focused fake-store contract
tests, `.github/workflows/ci.yml` measurement-core/timing output, and affected CI
docs. Preserve dependency-safety and shard definitions unchanged in this slice.
**Out:** model changes, assertion weakening, loss of real Aquacast tests, reduced
scan severity, moving checks post-merge, or changing scheduled test scopes.

**Verification:** exact collected-node partition and new/root-level test behavior;
all expected coverage inputs present before combine; exact sequential-canary victim
passes once; real Aquacast discovery/model tests run with credentials; existing
Dependabot absent-secret behavior remains fail-closed outside the accepted case.
Exercise a real failing test, missing coverage artifact, download failure and auth
failure. All must yield the expected failed gate. Test warm and cold dependency
paths on Actions and account for artifacts, retries and duplicated setup.
Use existing tests under `tests/unit/tools/test_unit_shards.py`, relevant workflow
policy tests, and full final repository validation. Add focused RED tests for the
chosen change before implementation.

**Focused commands:**

- `uv run --frozen pytest tests/fakes/test_fakes.py tests/unit/fakes tests/unit/store/test_dhm_delivery_observation_store.py`
- `uv run --frozen pytest tests/unit/tools/test_unit_shards.py`
- `uv run --frozen pytest tests/unit/services -n 2 --cov=src/sapphire_flow --cov-report= --durations=40`
- Repeat the services command with `COVERAGE_CORE=sysmon` and separate
  `COVERAGE_FILE` outputs; compare all measured-file and covered-line sets through
  coverage.py's public `CoverageData` API, as in the discovery receipts.
- Repeat coverage parity for the complete unit collection before accepting D1;
  inspect Actions' actual core, test counts, warnings and JUnit outputs.

**Pre-change:** existing cProfile receipt establishes quadratic setup; retain its
same test/fixture for before/after measurement. Add public-behavior contract tests
for collision atomicity, same-batch duplicates, stable IDs, raw-value/provenance
changes, delete/reinsert and QC updates. Deliberately break an upsert/collision
path to prove the discriminating test fails, then restore it. Do not introduce a
flaky wall-clock threshold into unit tests.

### T3 — Assign traceable versions without per-PR metadata churn

**Outcome:** independent feature branches do not automatically edit shared release
versions; each release/build identity maps unambiguously to source and dependencies.

**In:** package and runtime version sources, lockfile root metadata, build tooling,
`tag-main.yml`, relevant image build/deployment identity consumers, workflow policy
and tagging tests, AGENTS.md, workflow/contribution and CI/CD documentation.
**Out:** rewriting published tags/history, remote test tags, automatic merge,
registry publication, deployment, or blind lockfile conflict resolution.

**Verification:** two PR-like branches from one base integrated in both orders in
controlled local repositories below `.worktrees/`; no version-only conflict,
consistent dependency locks, traceable package/runtime/image identity. Specifically
assert the root `uv.lock` entry does not change after each ordinary sibling commit
and both `uv lock` and `uv sync --locked`, before any release tag is assigned.
Verify full-SHA CI development overrides and their cache invalidation in a
shallow/tag-less checkout without extra history fetch. Verify release mode rejects
unchecked development overrides and resolves the exact requested tag target.
Repeat for
compatible dependency edits and incompatible constraints; retain both intents and
fail meaningfully on an unsatisfiable combination. Test repeated/concurrent and
out-of-order assignment, pre-existing wrong-target tags, shallow/source-archive
builds, and failed publication. Never treat tag existence alone as identity proof.
Check built wheel metadata and runtime version agree and accepted constraints do
not silently change. Explicitly test the dependency-safety classifier across the
root-version-field removal. Verify wheel/sdist resource parity (`py.typed`,
`data/icon_ch2_eps_grid.npz`, API templates and Aquacast YAML configs), then install
built artifacts into clean environments and exercise resource loading/config
hashes, not just metadata inspection. Configure package data explicitly if needed.

Both Docker variants (`WITH_AQUACAST=0` and `1`) must report the same intended
package version/full source SHA. Capture each variant's OCI labels and immutable
local image ID in an operator build receipt outside the mutable image tag; a local
image ID is not a registry manifest digest. Check a test container's image ID
against the receipt. Do not deploy. If private credentials or Docker are absent,
report the unverified variant; do not accept default-image proof for Aquacast.

Require these archive outcomes: a clean verified tag yields exactly X.Y.Z; its
wheel and sdist rebuild/import without Git keep that identity; shallow CI uses
the verified full-SHA development override; a raw source archive with neither
version metadata nor an approved source identity fails clearly. A release build
must not inherit an unchecked development override. Preserve existing public
reporting interfaces or specify an explicit migration.

**Pre-change:** reproduce the three-file version-only conflict in local branches
using the current bump policy. Prove the selected replacement removes only that
conflict and still exposes genuine dependency/identity errors.

### T4 — Provide useful, accurately scoped local feedback

**Outcome:** documented pre-push checks reuse CI configurations and identify exactly
what ran and what requires external prerequisites.

**In:** `src/sapphire_flow/cli/check.py`, its existing script entry and tests,
pre-push documentation and affected hooks only where necessary.
**Out:** a generic task framework or any claim that local success replaces Actions.

**Verification:** `uv run --frozen pytest tests/unit/test_check.py` preserves the
existing no-argument ruff behavior and tests selected paths, invalid scope, child
command failure propagation and lint-only/focused result labels. Walk the full
local recipe with available prerequisites; deliberately absent prerequisites must
produce clear non-success, never a full-suite pass. Document complete equivalents
for integration, image smoke/scan/SBOM and wheel guard plus CI-only facilities.

**Pre-change:** current helper runs only two ruff commands; add focused tests for
the approved additional scope/prerequisite behavior before extending it.

### T5 — Validate the final outcome and hold for approval

**Outcome:** reviewed implementation PR evidence, or an explicit measured shortfall.

**In:** final branch diff, complete tests, independent Claude/Codex patch review,
GitHub after-sample, budget comparison and owner-approved PR delivery.
**Out:** self-approval, autonomous merge, fabricated sustained-performance claims.

**Verification:** run `uv run pytest` after the final change locally or in CI,
plus the final plan's focused checks, ruff and pyright ratchet. Apply the T1 sampling
method unchanged and report at-least-90%-within-ten-minutes against all gates,
including queue/retry waits and nongreen verdicts. Report sample size and limitations;
a small sample is preliminary. No increase in spending or unexplained runner-minute
cost. Verify every vision requirement, release integration cases and security
boundaries. Ask the owner about outcome trade-offs if the target remains impossible;
do not weaken gates. After owner merge, audit current target effects and remove only
this invocation's clean, fully merged disposable checkouts; preserve other work.

**Pre-change:** N/A for final audit; all behavioral tasks require their own RED proof.

## Dependency graph

```json
{
  "phases": [
    {"id": "evidence", "tasks": ["T1"], "parallel": false},
    {"id": "feedback-pr", "tasks": ["T2", "T4"], "depends_on": ["evidence"], "parallel": false},
    {"id": "release-identity-pr", "tasks": ["T3"], "depends_on": ["feedback-pr"], "parallel": false},
    {"id": "verification", "tasks": ["T5"], "depends_on": ["release-identity-pr"], "parallel": false}
  ]
}
```
