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
Define "the PR did not touch the target" as: head target == merge-base target (the target
on the branch point), not head == current base. For a non-docs PR the lint check passes if
1. **the PR did not touch the target** and the tested merge target equals the base target
   (normal case; also covers a branch that went stale because a bump PR landed on main
   after it was cut: head X, merge X+1 passes), or
2. **the PR bumped by exactly one patch** over the base and the merge target equals the head
   target (existing arithmetic, used for deliberate bumps).

Still fails: +2 or more, a decrease relative to the merge-base, a malformed file, a changed
major/minor, a merge target that matches neither case. The docs-only exemption is
unchanged. (Review finding B1: comparing head to the current base alone would only move
the conflict from every PR to every release, because a stale branch after a bump would fail
the existing "merge target differs from PR head target" check.)

Release publication is unchanged. `tools/release_identity.py publish` still requires the
requested version to equal `RELEASE_TARGET` at the source commit (release_identity.py:939)
and to be greater than the current release state. So before each release a small bump PR
lands on main. Agents prepare it; the owner merges and publishes (AGENTS.md). This moves
the cost from every PR to every release.

## Second change: stop the stale-base race
`check_pr` currently requires the test merge to have parents exactly `[event base, head]`
(release_target.py:226-232). When main moves between the PR event and the CI start, the
event base is stale and lint fails with "event merge must have exactly base/head parents
in order" although nothing is wrong (seen on #637, 2026-11-24).

Proposal: require the merge to have exactly two parents with `parents[2] == head_sha`, and
use the merge's own first parent as the base everywhere `base_sha` is used in `check_pr`
(:222 object check, :226-229, :234 merge-base, :242 and :244 prose exemption, :249 via
`next_target`). `HEAD == merge_sha` and the commit-object checks stay. The `--base-sha`
CLI argument becomes optional and ignored in this mode, so `ci.yml:81` and
`tools/private_verification_gates.sh:195` stay unchanged
(`test_release_identity_workflow_policy.py:34-40` pins that command line). The base needs
no "is on main" check: `pull_request` is limited to `branches: [main]` (ci.yml:6-7).
The PR author cannot forge `github.sha`, so the practical risk is low; the owner still
reviews this loosening explicitly.
Not in scope but flagged: `tools/check_map_contract_version.py` (gates.sh:197,
ci.yml:102) also uses the event base and can hit the same race.

## Scope (files)
- `tools/release_target.py`: new rule and first-parent base; a module constant
  `DEV_PHASE_ALLOW_UNCHANGED_TARGET = True` guards it so reverting is one line.
- `tests/unit/tools/test_release_target.py`: rewrite the eight `match="exactly once"`
  cases (lines 135, 432, 457, 487, 496, 547, 561, 609); under the new rule most of those
  PRs pass. "Existing tests unchanged" is false for this file. Recheck
  `test_reversed_parents_refuse` (:75).
- `tests/unit/tooling/test_workflow_policy_coherence.py`:42-60 asserts exact `cicd.md`
  sentences; update with the paragraph.
- `docs/standards/cicd.md`: the paragraph "Each non-docs PR advances ..." (~545-548), the
  "CI action-pin updates" text (:15-19), and the owner pre-merge checklist (~568-588,
  including "owner must prevent same-target parallel landings", which is now the norm).
- `docs/plans/521-pr-release-target.md`: one-line pointer to this plan.
- No change to `.github/workflows/ci.yml`, so `WORKFLOW_SHA256` does not move.
- Out of scope: `WORKFLOW_SHA256` automation, release publication, branch protection,
  merge queue, package versions. **This plan does not remove the hand-maintained
  `WORKFLOW_SHA256` step for workflow or Dependabot pin PRs.** Only the release-number
  half of that pain goes away.

## Tests (red first)
- unchanged target passes; base+1 passes; base+2 fails; decrease vs merge-base fails;
  malformed fails; head target differs from merge target (when bumped) fails.
- stale branch after a bump landed on main (head X, merge X+1) passes.
- two PRs both bump +1: the second one's head equals the new base and passes only as
  "untouched"; document the effect.
- merge with 3 or more parents fails; `parents[2] != head` fails.
- docs-only PR with unchanged target still passes; the prose exemption uses the first parent.
- stale event base: merge parents `[newer main, head]` passes with the CLI base ignored.
- constant `False` (monkeypatch) restores the exact-once rule, including the stale-parent
  case.
- existing release-identity and staging tests still pass.

## Risks
- **Two PRs ship with the same version.** Harmless in development: the version only
  matters at publication, which re-checks the target. A release built from main before a
  bump PR is refused by `publish` (target must match the requested version).
- **Forgotten bump before a release.** `publish` fails loudly, so it is caught at release
  time, not silently.
- **Weaker parent check.** Mitigated by keeping `HEAD == merge_sha`, the head parent
  check and commit-object checks. Owner reviews this item specifically.
- **Downstream readers of the target.** `deployment_rehearsal.py:217` and
  `deployment_smoke.py:229` use `RELEASE_TARGET` as the expected release version, and
  `staging_release.py:207` compares it to the binding. With many commits sharing one
  target, these only identify the commit through `SAPPHIRE_SOURCE_REVISION`. Acceptable,
  and stated here.
- **Open PRs that already bump.** Still valid (base + 1); they only conflict as before and
  can drop their bump commit.

## Exit
Revert when the owner says so: set the constant to `False` (or delete the branch of
logic), restore the `cicd.md` paragraph and the exact-once tests and the policy-coherence
strings, and run the full tool tests. Record the date in
this plan and move it to `archive/`.

## Open questions for the owner
1. Is landing a small bump PR before each release acceptable?
2. Is the parent-check relaxation acceptable, or keep it strict and only allow the
   unchanged target?
3. Add a helper that recomputes `WORKFLOW_SHA256` (separate plan), or leave it?
