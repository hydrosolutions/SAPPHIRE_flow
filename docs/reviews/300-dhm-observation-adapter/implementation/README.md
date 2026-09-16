# Plan 300 — independent implementation review

Reviewed 2026-09-16 on branch `docs/plan-300-review` in the separate worktree
`/private/tmp/sapphire-plan300-review`.

Reviewed implementation: `9b46b390296c913c3e6e0f09d74346a952c19a64`.
Approved READY plan: `aa50f8865069e6e72b74eef59fbbeaf6fa8d33d5`,
`docs/plans/300-dhm-observation-adapter.md`. The current plan is
[COMPLETE and archived](../../../plans/archive/300-dhm-observation-adapter.md).
All reviewers used the approved plan and current implementation; archive status
does not represent patch-review approval or live activation.

Base-branch diff: `origin/main...9b46b390`, with merge base
`f2dc569ccb513ac913126d3d24d58ad2d1b18d84`. The shared `origin/main` ref was
`925ec5999862190f8c16c60c8664b9932699f5b3` when recorded during review; its
advancement did not change that merge base or the reviewed diff.

- [Claude review](claude-review.md): five low-severity findings; no blocking defect.
- [Independent Codex review](codex-review.md): one minor documentation finding,
  overlapping Claude's stale-status finding; no implementation defect.
- [Additional API-contract and ingestion-integration review](contract-review.md): CLEAN.

Claude and Codex ran in separate CLI sessions. Claude had Read, Glob and Grep
tools only and received a materialized base-branch diff excluding prior review
reports. Codex ran in a read-only sandbox. The additional contract pass was an
independent Codex agent. All passes excluded previous and concurrent review
reports. Reports are preserved as returned, with terminal newline normalization.

The five distinct findings concern the stale DRAFT label, an expanded verification
command in the archived plan, two missing explicit test assertions, and the
documented meaning of CONFIGURATION_ERROR for an absent caller watermark.
Findings have not been accepted, rejected or folded in by this review pass.

Recorded verification: 308 focused tests passed; Ruff lint and format checks
passed; the pyright ratchet passed at 363 diagnostics against a baseline of 400,
with no diagnostics in the new adapter/config modules. Reviewers did not rerun
the repository-wide suite or contact DHM. Codex additionally checked capture
hashes and ZIP integrity. Each report states its verification limits.

The full-suite merge gate and live Nepal activation prerequisites remain open.
No implementation changes, push, PR, merge or deployment were performed.

## Subsequent full-suite run and PR

The owner subsequently requested the full suite followed by a PR. `uv run pytest`
passed on `558dd18d`: 6,335 passed, 51 skipped, 15 deselected, 54 warnings in
996.31 seconds. [PR #278](https://github.com/hydrosolutions/SAPPHIRE_flow/pull/278)
was opened. Current main (`925ec599`) was then merged into the feature branch to
resolve conflicts confined to the package-version entries in `pyproject.toml`,
`src/sapphire_flow/__init__.py` and `uv.lock`; the combined version is `0.1.913`.
No DHM behavior changed during that resolution.

The full default suite was rerun after the merge and version bump: **6,408 passed,
53 skipped, 15 deselected, 54 warnings in 952.16 seconds**. The repository's
default selection excludes live, deployment and slow tests. Review findings
remain unapplied and are disclosed in the PR; live Nepal activation prerequisites
remain open.
