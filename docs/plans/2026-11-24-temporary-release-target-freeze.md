---
status: READY
---
# Temporary: stop requiring a release-target bump in every PR

**Status:** READY rev 4, 2026-11-24 (rounds 1-2 both NEEDS CHANGES, folded; round 3: Codex READY, Claude 3 blockers, folded). No plan ID yet (owner grants IDs). HIGH RISK under
`AGENTS.md` (release identity and CI). Round 4 (delta): Claude and Codex both READY, 2026-11-24.
Owner approved implementing change 1 only; change 2 is not approved. Merge needs the owner.
Implementation notes from review: also name the cicd.md :584-586 "update target" sentence in
the checklist edit; use `tests/unit/tools/test_deployment_smoke.py`; `merge-base --all` is on
release_target.py:235.

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

## Change 1 (this plan's implementation PR): the target rule
Let A = target at the merge-base, B = target at the tested base (merge first parent),
H = head target, M = merged target. "Untouched" means H == A (not H == B).
For a non-docs PR, lint passes if
1. **untouched** (H == A) and M == B (normal case; also a branch gone stale because a bump
   landed on main after it was cut: A = H = X, B = M = X+1), or
2. **bumped exactly one patch over the tested base** (H == B+1) and M == H (deliberate bump).

Truth table (A, B, H, M; major/minor unchanged):

| Case | A | B | H | M | Result |
|---|---|---|---|---|---|
| Unchanged | X | X | X | X | pass (1) |
| Base+1 | X | X | X+1 | X+1 | pass (2) |
| Stale branch after a bump | X | X+1 | X | X+1 | pass (1) |
| Two PRs both bumped | X | X+1 | X+1 | X+1 | **fail**: drop the bump commit |
| Base+2 | X | X | X+2 | X+2 | fail |
| Decrease (H < A) | X | X | X-1 | X-1 | fail |
| Malformed, major/minor change, M matches neither | - | - | - | - | fail |
| Zero or more than one merge-base (`merge-base --all`, release_target.py:234) | - | - | - | - | fail |
| Missing or invalid target at the merge-base | - | - | - | - | fail (see below) |

The "two bumped" row is deliberate and common: every open PR that already bumps goes red the
moment another bump lands (it does not merely conflict). `test_release_target.py:595-610`
already pins this; keep it. A = X, B = X+1, H = X+2 would pass rule 2 on paper but cannot be
merged by git (both sides edit the same line); treat as unreachable.

Today multiple or zero merge-bases only disable the docs exemption, and a bump with a missing
merge-base target still passes by arithmetic (release_target.py:239-252). The new rule makes
both hard failures. Bootstrap: when the tested base is `BOOTSTRAP_BASE_SHA` (no target file at B), the dev rule does
not apply and the existing path runs unchanged (`next_target`, release_target.py:89-96: the PR
must set `BOOTSTRAP_TARGET`). The dev rule applies only when B has a valid target. Otherwise a
missing or invalid A is fatal. Add a bootstrap test through `check_pr` (existing bootstrap tests
at :98-123 call `next_target` only).
Mode: the fail-closed rows (zero or multiple merge-bases, missing A) and the untouched rule exist
only in dev mode. With the constant `False`, behavior is exactly today's (no new fail-closed
checks). Exit removes them with the dev branch.
The docs-only exemption is unchanged (strict mode); in dev mode it is subsumed by rules 1 and 2.

Accepted risk, stated: in dev mode nothing requires a code-changing PR to carry any bump.
Workflow-code and Dependabot pin PRs no longer need a bump either; they still need the
hand-made `WORKFLOW_SHA256` edit (not removed by this plan, `tools/ci_attest.py:122`).

Release publication is unchanged: `tools/release_identity.py publish` needs a version equal to
`RELEASE_TARGET` at the source (release_identity.py:938-941) and above the release state. It
does not require a fresh bump if an advanced, unpublished target already exists, and an earlier
same-source/version acknowledgment returns before the target check (:919-923). Before a release
the owner checks the target is above the release state; if not, a small bump PR lands first.
Agents prepare it; the owner merges and publishes (AGENTS.md). Cost moves from every PR to
every release.

Rollout: merge this change first, then tell authors of open bumped PRs to drop the bump commit.

### Scope (change 1)
- `tools/release_target.py`: rule above; constant `DEV_PHASE_ALLOW_UNCHANGED_TARGET = True`.
  `False` restores today's behavior in full, including today's merge-base handling (no new
  fail-closed checks), mandatory `--base-sha` and exact event parents (change 1 does not touch
  the parent check).
- `tests/unit/tools/test_release_target.py`: parametrize the existing cases on the constant, so
  the strict run keeps today's assertions unchanged; add dev-mode expectations where they
  differ. Of the eight `exactly once` cases (135, 432, 457, 487, 496, 547, 561, 609), six pass in
  dev mode (135, 432, 457, 487, 547, 561); 496 (1.2.2 -> 1.2.5) and 609 still fail with new
  messages. Also pass in dev mode because the target is untouched: `test_concurrent_base_cannot_hide_pr_code_change`
  (:550), the rename and nonregular-file cases (:451/:461/:525), the `src/merge-only.py` case
  (:509-521); these are NOT security refusals in dev mode, only in strict mode. Still refused in
  dev mode: :595 and the RELEASE_TARGET parametrization of :510. `test_reversed_parents_refuse`
  (:77) is unchanged.
- `tests/unit/tooling/test_workflow_policy_coherence.py:42-60`: pins `cicd.md:545-546` and the
  "head and merge targets must agree and equal base patch+1" sentence (cicd.md:564); update both with the
  paragraph. Also check `test_release_identity_workflow_policy.py:68-72` (greps
  release_target.py text for any new constant).
- `docs/standards/cicd.md`: paragraph :545-548; pin-update text :15-19 (a pin PR now needs only
  the `WORKFLOW_SHA256` edit); :563 and :581; owner checklist from :584 (replace the sentence at :586-587 (owner must prevent parallel PRs landing with the same
  unchanged target) with: in dev mode same-target landings are allowed; the owner instead checks
  before each release that the target is above the release state, and lands a bump PR if not).
- `docs/plans/521-pr-release-target.md`: one-line pointer.
- No `.github/workflows/ci.yml` change, so `WORKFLOW_SHA256` does not move.
- Out of scope: `WORKFLOW_SHA256` automation, publication, branch protection, merge queue,
  package versions, anything in change 2.

### Tests (red first, change 1)
- Every truth-table row, including both fail-closed merge-base rows and the bootstrap exception.
- Unchanged workflow-code PR (`ci.yml` edit, target untouched) passes.
- Constant `False` (monkeypatch) restores every existing assertion.
- Same-target / different-source negative cases, one per reader, in `test_staging_release.py`,
  `test_qualify_image_exporter.py`, `test_deployment_smoke.py` and `test_deployment_rehearsal.py`
  (reuse an existing source-mismatch test if one already covers it; no reader code changes).
- Bootstrap PR through `check_pr` (see above).
- Existing release-identity and staging tests still pass; full suite before merge.

## Change 2 (SEPARATE PR, owner decides, higher risk): stale-base race
Not part of change 1. Recommendation: do not start until the owner has seen the #637 evidence.
Evidence owed: event base/head, `github.sha`, checkout SHA and actual merge parents for #637.
Main advancing does not change the parents of an already created merge commit; the cause is
unproven. Today `check_pr` requires parents exactly `[event base, head]` (release_target.py:226-232).

Proposal if justified: require exactly two parents with `parents[2] == head_sha`, use the merge's
first parent as the base everywhere `base_sha` is used in `check_pr` (:222, :226-229, :234,
:242/:244, :249); keep `HEAD == merge_sha` and the commit-object checks; `--base-sha` optional
and ignored (so `ci.yml:81` and `private_verification_gates.sh:195` stay unchanged, pinned by
`test_release_identity_workflow_policy.py:34-40`). Trust boundary: the first parent is only
internally consistent; nothing proves it is on main (`ci.yml:6-7` is caller provenance, not
ancestry).
It does not fix the race alone. Also consuming the event base: `tools/check_map_contract_version.py`
(gates.sh:197, ci.yml:102) and `tools/private_verification.py:296-298` (exact `[base, head]`
parents; binding at :307, exported at :448; same text in `cicd.md:2032`). The private-verification
binding is an admission/evidence boundary and must not change silently. Change 2 would also
carry its own tests: `parents[2] != head` and 3+ parents fail; stale event base passes with the
CLI base ignored; short-base-SHA case (:577-593); the strict-mode constant restores exact parents.

## Risks
- **Two PRs ship with the same version.** Harmless in development; see publication above.
- **Forgotten bump before a release.** Caught only if the target is not above the release
  state; the owner checklist must say so.
- **Downstream readers of the target.** `deployment_rehearsal.py:217` and `deployment_smoke.py:229`
  use it as the expected release version; `staging_release.py:207` compares it to the binding;
  `qualify_image_exporter.py:170-178` binds it to a canonical release tag and source. With many
  commits sharing one target, the distinguishing protection is `SAPPHIRE_SOURCE_REVISION` plus
  staging's release-state, tag-object and peeled-source checks (`staging_release.py:180-209`).
  A stale image with the same target passes version checks; add same-target/different-source
  negative tests.
- **Open PRs that already bump** fail lint once another bump lands (see rollout).

## Exit
Revert when the owner says so: delete the constant and the dev-mode branch (a dead flag is a
standing loosening), restore the `cicd.md` paragraph, the pin-update text and the policy-coherence
strings, run the full suite. Record the date here and move the plan to `archive/`.
