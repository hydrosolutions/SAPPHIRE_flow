---
status: READY
owner: Swiss restoration CI follow-up
---
# Swiss restoration CI gates

## Outcome and authority
Make the unchanged Swiss full-roster performance contract a required fresh-process CI
gate within the existing heavy leg, and give the existing Aquacast image proof enough bounded hosted-runner disk to
complete. This is a CI/test-only follow-up on backend PR #448 at
`1c5a905f2834d14d53904bdf847065d8d2f2973a`; frontend PR #102 stays unchanged.

The orchestrator set this plan READY after independent Claude, Codex and risk reviews.
The final disposition adopts Claude’s existing-gate option and closes the recorded
safety, evidence and timeout findings without expanding host-operation scope. The owner keeps PR approval,
merge, release and deployment. Independent patch review, real-browser/network/two-refresh
acceptance, real-API cap and quartile qualification, deployed source/build provenance,
rollback and owner acceptance remain open. Green CI closes only these CI blockers.
Implementation is limited to the phases and invariants below.

## Verified evidence and limits
- Native CI run `41913566349` ran the production benchmark after the heavy workload and
  failed only its unchanged p95 contract: `1.1292054889499752 s < 1.0 s`. The full local
  suite similarly reported `1.125178 s`. The production case passes in a fresh isolated
  invocation; the complete benchmark module also passes when its stress and production
  cases run together. The committed fixture database and sustained process/host workload
  differ across these runs. Root cause is unconfirmed. Do not claim proof of no product
  regression or a concurrency cause.
- The benchmark currently parametrizes `stress_members` and `production_quantiles` in
  one integration test. `tools/integration_shards.py` assigns that whole file to the
  serial `heavy` matrix leg; its native collection proof already detects missing and
  duplicate selected nodes. The session-scoped committed database is not cleaned between
  cases.
- The production contract is 134 stations × 10 daily steps × 7 stored quantiles, full
  binary64 values, at most 12 queries, raw UTF-8 JSON at most 393,216 bytes, and inclusive
  p95 from 10 unprofiled samples below 1.0 second. Current diagnostics are written only
  after every assertion, so a failing budget loses the useful sample/query/byte evidence.
- Both fresh Aquacast image-proof attempts failed because the GitHub-hosted runner could
  not write its own diagnostic log: `No space left on device`. Neither produced a normal
  build log or reached Trivy or release-identity proof. The default image build, scan,
  SBOM and identity proof passed. PR #448 changes no Dockerfile, lock, dependency or
  model/inference source.
- The locked Torch wheel alone is about 915.7 MB compressed. The unchanged Docker
  build retains a uv cache, copies the virtual environment into the final image, loads
  the image into Docker, and exports it for Trivy. These amplify working storage.
  The proposed 20 GB minimum is an operational precondition, not a measured peak or
  a claim that the image fits.

## Fixed scope and invariants
- No production forecasting, API, service, schema, Dockerfile, dependency, lockfile,
  image content, model, schedule, frontend or deployment change.
- Keep every benchmark functional assertion and both benchmark shapes. Do not skip,
  fake, xfail, retry-until-green, reduce samples, warm away work, loosen `< 1.0 s`, raise
  query/payload limits, reduce 134 × 10 × 7, round values, or drop quartiles.
- Keep checkout identity, pinned buildx/build action, private-git BuildKit secrets,
  Aquacast extra, Trivy severity/gate, and Aquacast release identity/import proof intact.
  No new action, permission, image or dependency.
- Capacity cleanup is exclusive to the disposable GitHub-hosted
  `aquacast-release-image-proof` job. It never runs locally or on staging/production.
  It must not touch the checkout/workspace, `/opt/hostedtoolcache`, Docker data/cache,
  credentials, private files, package-manager caches, or arbitrary filesystem paths.
- PR #448 already targets `0.1.1105`. Per current `AGENTS.md`, further commits in the
  same PR do not advance `RELEASE_TARGET` again.

## Implementation phases

### P1 — Required fresh performance process within the existing heavy leg
Split only the benchmark layout: stress remains in its current test file and production
gets a separate file. Both call a shared helper in a non-collected module (not named
`test_*.py`), preserving setup, values and assertions. Wrappers import only helper and
required fixtures (`assignment as assignment`), never test functions/classes.

Keep CI matrix IDs `heavy` and `rest`, private gate names, aggregate dependencies,
`CI_JOB_GATES`, `GATES`, transfer `GATE_NAMES`, envelope schemas and staging receiver
unchanged. No receiver reinstall or host operation is authorized. Add a selectable
`performance` test group, not a third matrix/private gate. Make execution mapping
explicit: `heavy` runs `performance` then `heavy` in separate pytest processes; `rest`
runs `rest`. Each selected group appears exactly once. The `rest` complement excludes
both curated groups. Audit all shard-tool consumers and update group-versus-job tests.

In `.github/workflows/ci.yml`, run the performance group as a mandatory step before
heavy's main pytest work (after normal setup and partition collection proof), using a
fresh pytest process and its new session-scoped `isolated_committed_db_engine`
testcontainer, not the service database. ID this step `overview_performance`, give it
`timeout-minutes: 3`, and no `continue-on-error`. Keep normal pytest verdicts fatal,
`fail-fast: false`, and the unchanged success-only required `integration` aggregate.
Existing step bounds total 20 minutes; the new performance step and one-minute artifact
upload total 24 within the unchanged 25-minute integration job. Preserve all other bounds.

Mirror the performance-first process in `tools/private_verification_gates.sh` inside
`integration-heavy`, before its main pytest; keep `integration-rest` unchanged and the
existing 1500-second gate/180-minute enclosing budget. Use distinct performance and heavy
JUnit paths without overwriting evidence. This source-script update adds no gate name
or transfer field; verify private/staging contract tests continue to pass unchanged.

Extend native partition proof with the existing `--override-ini addopts=` and non-live
filters. Prove all selected nodes and non-live files are covered exactly once across
performance/heavy/rest selections AND that both public/private execution mappings run
those groups once. Real collection must contain exactly these benchmark nodes globally:
- `tests/integration/store/test_swiss_overview.py::TestSwissOverviewIsolated::test_134_station_bounded_reads` (stress, heavy);
- `tests/integration/store/test_swiss_overview_performance.py::TestSwissOverviewPerformance::test_134_station_bounded_reads` (production, performance).
Performance selects exactly its one node. Detect additional benchmark function names
under other file paths, not just identical node IDs. Stale/empty curated files, missing
mapping, filter drift, duplicate/missing nodes or a permissive aggregate must fail.
Preserve production's rejection of profiling and ten samples; stress retains its current
three samples and optional profiling. The process/container is fresh; do not claim an
entirely separate runner or a diagnosed cause of the earlier timing failure.

### P2 — Persist bounded benchmark evidence before any assertion
Immediately after measurements and serialization complete, construct
and write bounded benchmark JSON before the first post-measurement functional, query,
byte or timing assertion. Preserve earlier setup/profiling precondition failures. The artifact has exactly the following keys, no arbitrary nested data:
- `case`: `production_quantiles` or `stress_members`; `profiled`: boolean (false for production).
- Non-negative integers: `stations`, `horizon_steps`, `forecast_value_rows`, `observation_rows`,
  `bafu_files`, `archive_names`, `archive_enumerations`, `raw_evidence_files`, `raw_bytes`, `gzip_bytes`.
- `members`: null or 20; `quantiles`: null or the fixed seven numeric levels.
- Finite non-negative numbers: `time_step_seconds`, `p95_seconds`.
- Finite non-negative numeric arrays: `route_seconds`, `serialization_seconds`, `samples_seconds`;
  `queries`: non-negative integer array. Arrays have the case's unchanged sample count.
- `latency_target_met`: boolean for production, null for stress; `raw_payload_target_met`: boolean.
Remove free-form `scope`. Never include bodies, SQL, paths, environment, tokens, UUIDs,
principals or configuration. Stop creating the sibling response JSON entirely. Preserve
optional local diagnostic output, and configure one literal runner-temp JSON path only
in the performance step. A write/serialization failure fails pytest; never continue as green.

Use a pure record-then-budget-assert helper in a module without DB/test-module imports,
with synthetic tests under `tests/unit/`, separate from the integration performance file.
Use over-budget input tests for
query, byte and latency failures; prove the exact allowlist and evidence-before-failure
for each. Keep all other functional assertions in the shared benchmark after recording.
Reject extra keys and non-finite diagnostics rather than serializing arbitrary data.

Use the performance pytest step ID `overview_performance`. Upload exactly the single diagnostic file
with the repository's pinned upload action and this condition:
`${{ failure() && !cancelled() && matrix.shard == 'heavy' && steps.overview_performance.outcome == 'failure' }}`.
Set `timeout-minutes: 1`, `continue-on-error: true`, short fixed retention, unique fixed-
structure artifact name and `if-no-files-found: error`. Never use a glob/directory or
upload responses. The original pytest failure remains fatal even if upload fails.
Static workflow contract tests assert exact conditions/flags for success (no upload),
assertion failure, cancellation/job timeout (no artifact expected), missing file and
upload failure (neither can make pytest green). Do not claim simulated GitHub behavior.

### P3 — Add job-local, allowlisted Aquacast capacity preparation
Place one shell step after checkout identity validation and before buildx, with
`if: env.AQUACAST_TOKEN_PRESENT == 'true'` matching the build condition exactly.
Preserve the token-absent fail/degraded paths. Set `timeout-minutes: 2`; bounded step
sum is then 34 minutes within the unchanged 35-minute job. Timeout fails before buildx.
Lock this job to literal `runs-on: ubuntu-latest` (no self-hosted, expressions or arrays).
Use `set -euo pipefail`, no tracing and no secrets in step env. Before filesystem
operations require `GITHUB_ACTIONS=true`, `RUNNER_ENVIRONMENT=github-hosted`, and
`GITHUB_JOB=aquacast-release-image-proof`; absent/mismatched context fails closed.
Tests inject these context values only into the safe temporary-root script copy. Assume the stock runner
root filesystem holds Docker data and runner diagnostics; do not claim coverage of
an arbitrary runner layout.

Read `df -B1 --output=avail /` and require successful exit, exactly its expected header
and one non-negative decimal integer data row. Empty, malformed, extra or command-error
output fails closed before deletion/build. Trim the padded `Avail` header before
matching it. Verify the real runner header in CI; fakes alone do not prove it. Report fixed before/after free-byte numbers
as notices, plus bounded `df` telemetry. If free space is at least 20,000,000,000 bytes,
delete nothing. Otherwise consider only the two literal roots:
`/usr/local/lib/android/sdk` and `/usr/share/dotnet`.

For each existing root, require a real directory, not a symlink, and successful
`realpath -e` equal to that literal path. A dangling symlink or other unexpected object
fails closed; an absent root is recorded as absent. Reject any mount at
or below either root, including same-device bind mounts, using the current Linux mount
table (`/proc/self/mountinfo`, structurally parsed field 5, exact root or root-plus-slash
prefix; never match a sibling such as `dotnet2`). Mount-table read/parse errors fail closed. Do not run `du`: before/after free bytes supply capacity telemetry without an extra
recursive filesystem scan. Delete with literal arguments only:
`sudo rm -rf --one-file-system -- <literal-root>`. Never expand discovered paths or
follow links. The mount rejection plus GNU rm's device boundary safeguard are both
required. Removal errors fail closed. Always recheck free bytes after preparation and
fail before buildx if still below 20,000,000,000.

These stock Android/.NET SDKs are unused by this host job, whose Docker stages supply
build dependencies. The 20 GB value is a minimum precondition, not measured peak usage
or proof the image fits; a runner starting at 21 GB may still fail. Only a complete
build, Trivy and identity run closes the blocker. On insufficient capacity, cleanup
timeout, unsupported layout or another ENOSPC, stop and escalate; never broaden paths,
raise the threshold silently, remove Docker/cache/credentials, or invent a paid runner.

Tests must never run a deletion command against real SDK roots. Execute a test-only
copy of the extracted script with BOTH literals replaced by resolved temporary test
roots; assert the executed copy contains neither original root. The production script
still contains only the exact two literals, with no configurable path override. Run test copies with an explicit minimal environment (not inherited CI context), and
use bash-3.2-compatible syntax for native macOS tests. Fake
`df`, `realpath`, `sudo` and `rm`; the fake remover must refuse any path outside
those temporary roots. Supply a fake mount table through test-only script substitution,
not a production path override. Test no-op, both removals, absent roots, symlinks and
canonical mismatch, same/different-device nested mounts, malformed/error command output,
removal failure, post-cleanup shortfall, bounded telemetry and timeout. Lock exact runner,
job placement, token condition, step budget, existing permissions and proof steps. The
capacity step name must not contain `Build`, preserving existing build-step identification.

### P4 — Documentation and final verification
Update `docs/standards/cicd.md` with the unchanged two-leg matrix and private gate names,
required performance-first separate process in heavy, diagnostic failure artifact, aggregate, Aquacast-only capacity step and
20 GB minimum precondition and existing 35-minute job timeout. Update `docs/standards/security.md` with the exact two-path hosted
runner exception and its prohibited targets. Add a short CI note in `docs/touchpoint-maps.md` so future
changes do not broaden cleanup or merge performance measurement into the sustained main pytest process. Document both
new benchmark node commands here/in the touchpoint map; the original restoration plan
command lists only the old test file.
Do not change the restoration/API/model documentation.

Run focused RED tests first for missing partition membership/required aggregate,
post-assertion evidence loss, and unsafe/insufficient cleanup. Then run the updated shard
unit tests, workflow/security contract tests, `uv run ruff format --check`, `uv run ruff
check`, and the native partition proof. Run the production node alone in its native fresh
Postgres environment and the stress node in heavy scope. Finally require standard CI on
the exact PR head/base pair: every unit shard, both integration shards including the required
performance-first step and required aggregate, default image proof, Aquacast build + Trivy +
release-identity proof, and all other existing jobs. Full-suite authority for this follow-up is the complete CI unit/integration matrix
and required aggregates on the final change, including the required performance-first step and private-path parity.
A local single-process suite may reproduce the timing failure and must remain recorded
as failed, never skipped or reported green. If the fresh performance gate fails, retain
its evidence and escalate; do not retry until green or optimize production without
separate authorization. Only infrastructure failure before pytest starts warrants a
recorded infrastructure retry. Green CI does not close independent patch review, real
API cap/quartiles, authenticated browser/network/two-refresh, provenance, rollback, or
owner acceptance/merge/release/deployment gates. A local inability to reproduce GitHub-hosted capacity is recorded as
not run, never pass.

## Dependency graph
```json
{
  "P1": [],
  "P2": ["P1"],
  "P3": [],
  "P4": ["P1", "P2", "P3"]
}
```
P1/P2 and P3 may be implemented independently only under one implementation owner; P4
and final CI follow both. No automatic readiness, merge, release or deploy follows a
green run.

## Reviewer acceptance
Independent reviewers should reject the patch if it changes forecasting behavior,
weakens any benchmark/image/security proof, loses exactly-once integration coverage,
prints or uploads unlisted diagnostics, deletes outside the two literal SDK roots, uses
an unpinned cleanup action, assumes larger-runner availability, or claims the isolated
pass proves no regression. They should verify the 20 GB rationale is labelled a
minimum operational precondition, not a measured capacity margin and that only a complete Aquacast build, scan and identity run
closes the image-proof blocker.
