---
status: DRAFT
---
# Temporary: stop requiring a release-target bump in every PR

**Status:** DRAFT, 2026-11-24. No plan ID yet (owner grants IDs). HIGH RISK under
`AGENTS.md` (release identity and CI). Needs owner approval and independent Claude and
Codex reviews before it is marked READY. Nothing here is implemented.

## Problem
Plan 521 requires every non-docs PR to advance `RELEASE_TARGET` by exactly one patch from
its tested base (`docs/standards/cicd.md`, "Release identity tags and receipts").
During active development this causes:
- every open PR edits the same one-line file, so each merge makes all other open PRs
  conflict or fail the lint check;
- PRs can only land one at a time, and each needs a fresh 20-30 minute CI cycle after the
  previous merge;
- Dependabot pin bumps need a hand-made bump commit and a new `WORKFLOW_SHA256` before
  they can merge (`cicd.md`, "CI action-pin updates").

Observed 2026-11-24: about two hours spent keeping three Dependabot PRs mergeable.
Per-PR versions are not needed during development. Versions only matter when a release is
published and deployed.

## Decision sought
Temporarily allow PRs to leave `RELEASE_TARGET` unchanged. Keep the target honest when it
does change. Revert this rule when the owner declares the rapid-development phase over.

## Proposed rule (dev phase)
For a non-docs PR, the lint check passes if the PR head target is either
1. **equal to the base target** (new, the normal case), or
2. **exactly base + 1 patch** (existing arithmetic, used for deliberate bumps).

Everything else still fails: +2 or more, a decrease, a malformed file, a changed
major/minor, a head target that differs from the tested merge target. The docs-only
exemption is unchanged.

Release publication is unchanged. `tools/release_identity.py publish` still requires the
requested version to equal `RELEASE_TARGET` at the source commit. So before each release
the owner (or an agent on request) lands one small bump PR on main. That moves the cost
from every PR to every release.

## Second change: stop the stale-base race
`check_pr` currently requires the test merge to have parents exactly `[event base, head]`.
When main moves between the PR event and the CI start, the event base is stale and lint
fails with "event merge must have exactly base/head parents in order" although nothing is
wrong. This happened on #637 (observed 2026-11-24).

Proposal: require `parents[2] == head_sha` and use the merge commit's own first parent as
the base for the arithmetic, instead of requiring it to equal the event base SHA.
`HEAD == merge_sha` and the commit-object checks stay. The owner reviews this
relaxation explicitly, because it loosens an identity check.

## Scope (files)
- `tools/release_target.py`: accept unchanged target; take the base from the merge's first
  parent; a module constant `DEV_PHASE_ALLOW_UNCHANGED_TARGET = True` guards the new
  behavior so reverting is a one-line change.
- `tests/unit/tools/test_release_target.py`: new cases below.
- `docs/standards/cicd.md`: rewrite the paragraph "Each non-docs PR advances ..." to state
  the temporary rule, why, and how to revert; update the owner pre-merge checklist (no
  "base moved, update target" step unless the PR itself bumps).
- `docs/plans/521-pr-release-target.md`: one-line pointer to this plan (Plan 521 stays the
  record of the permanent rule).
- No change to `.github/workflows/ci.yml`, so `WORKFLOW_SHA256` does not move.
- Out of scope: `WORKFLOW_SHA256` automation, release publication, branch protection,
  merge queue, any version of the package itself.

## Tests (red first)
- unchanged target passes; base+1 passes; base+2 fails; decrease fails; malformed fails.
- head target differs from merge target fails.
- docs-only PR with unchanged target still passes (existing behavior).
- stale event base: merge parents `[newer main, head]` with unchanged target passes;
  `parents[2] != head` fails.
- constant set to `False` restores the exact-once rule (revert path is tested).
- existing release-identity and staging tests still pass unchanged.

## Risks
- **Two PRs ship with the same version.** Harmless in development: the version only
  matters at publication, which re-checks the target. A release built from main before a
  bump PR is refused by `publish` (target must match the requested version).
- **Forgotten bump before a release.** `publish` fails loudly, so it is caught at release
  time, not silently.
- **Weaker parent check.** Mitigated by keeping `HEAD == merge_sha`, the head parent
  check and commit-object checks. Owner reviews this item specifically.
- **Open PRs that already bump.** Still valid (base + 1); they only conflict as before and
  can drop their bump commit.

## Exit
Revert when the owner says so: set the constant to `False` (or delete the branch of
logic), restore the `cicd.md` paragraph, and run the full tool tests. Record the date in
this plan and move it to `archive/`.

## Open questions for the owner
1. Is landing a small bump PR before each release acceptable?
2. Is the parent-check relaxation acceptable, or keep it strict and only allow the
   unchanged target?
3. Add a helper that recomputes `WORKFLOW_SHA256` (separate plan), or leave it?
