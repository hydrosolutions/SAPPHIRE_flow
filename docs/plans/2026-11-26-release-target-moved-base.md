---
status: READY
---
# Release-target check: accept a base that moved before the merge commit was built

HIGH RISK under `AGENTS.md` (release identity and CI). MVP, no production: smallest change only.

## Problem

`tools/release_target.py check_pr` (line ~265) requires the PR merge commit's parents to equal
exactly `[event base sha, event head sha]`. The event base SHA is fixed when the
`pull_request` event fires. If main moves before GitHub builds the test merge commit, its
first parent is the newer main and the check fails with "event merge must have exactly
base/head parents in order". A re-run reuses `github.sha` and the event payload, so it is
expected to fail the same way; a new push is the known remedy.

Evidence (owner-observed in `ci.yml` runs, 2026-11-24 to 2026-11-26): about 4 of 11 failed
runs failed this way (two Dependabot PRs, a docs branch, and #676 after #679 merged).
`docs/standards/cicd.md` ("Release identity tags and receipts") is internally uneven: the
dev-phase arithmetic defines the tested base B as "the merge first parent", while the
preceding paragraph requires "exactly event base/head parents". The code follows the
second statement.

## Decision (owner, 2026-11-26)

Option A: make the check tolerant of a moved base. Not a merge queue, not an advisory check.

## Change (`tools/release_target.py`, `check_pr`)

After reading the merge commit's parents `[merge, p1, p2]`:

1. Require exactly two parents (`len(parents) == 3` including the merge SHA, checked BEFORE
   unpacking `p1, p2`; one parent or an octopus refuses) and `p2 == head_sha` (head stays
   exact).
2. Accept `p1 == base_sha`, or `p1` a descendant of `base_sha`. Test the relation with the
   existing idiom `git(["merge-base","--all",base_sha,p1], harden=True).split() ==
   [base_sha]` (not `merge-base --is-ancestor`). `git()` in `tools/release_identity.py`
   raises on any non-zero exit and git may exit 1 for unrelated histories, so wrap the call
   in `try/except ReleaseIdentityError` and translate any failure, as well as a result other
   than `[base_sha]` (a sibling, a criss-cross, or an event base newer than `p1`), into one
   refusal whose message still contains "parents" (existing tests match it). Reword the old
   message ("event merge must have exactly base/head parents in order"), which no longer
   fits.
3. From here on, the ENTIRE body of `check_pr` uses `p1` as the tested base, not the event
   base: the dev-phase gate `release_target_blob(...)` (line ~272), `check_dev_phase_target`
   (merge-base, blobs, arithmetic), the docs-only branch (`merge-base`, `prose_diff(p1, merge)`,
   `read_release_target(p1)`), and the strict branch `next_target(p1)`. The strict-bootstrap
   path is therefore keyed on `p1` (it only matches when `p1` is the historical bootstrap
   commit; harmless).
4. Unchanged: HEAD must equal the merge SHA, objects must be commits, reversed parents and
   single-parent merges refuse, target arithmetic.

Safety: the merge commit is built by GitHub; the author controls the head, not its first
parent. `p2 == head_sha` stays exact. A sibling, older or force-moved base is not a
descendant, so it refuses. The lint checkout uses `fetch-depth: 0` at `github.sha`, so `p1`
is local. `check_pr` is called only from the lint job (`ci.yml:76-81`, `pull_request` only;
no `merge_group` trigger; the main `push` path never runs it).

## Tests (`tests/unit/tools/test_release_target.py`)

New helper: build base1, advance main to base2 (optionally changing `RELEASE_TARGET` or a
code file), merge the PR at base2, return `(base1, base2, head, merge)` (`base2` is needed
for the "event base newer than `p1`" refusal, which passes `base2` as the event base against
a merge built on `base1`). Existing fixtures
(`merged_pr`, `scenario`) build the merge on the base they return, so this is required to
write the red-first case. Add moved-base PASSING cases in each downstream branch: strict
(class with autouse strict), `TestDevPhase` (`dev_mode`), and one docs-only case (a prose PR
whose moved base contains a code change, which exercises `prose_diff(p1, merge)`). A stale
bump after a moved base fails as before, in both modes. Refusals in the existing
`pytest.raises(..., match="parents")` style: event base unrelated or sibling to `p1`; event
base NEWER than `p1` (force-moved back); `p2 != head`; reversed parents and single parent
(existing, keep). Mutate the descendant check and the `p1`-as-base substitution in EACH
downstream branch to confirm the tests bite.

## Docs

`docs/standards/cicd.md` ("Release identity tags and receipts", the paragraph "The PR lint
check ... requires HEAD=`github.sha` with exactly event base/head parents", ~line 570):
REWORD it (do not append a note): the merge commit must have two parents, the second equal to
the event head and the first equal to the event base or a descendant of it; that first
parent is the tested base. No change to the owner checklist.

## Out of scope

Merge queue, advisory/disabled checks, the apt change (separate small PR), changes to
`RELEASE_TARGET` rules or the freeze (`DEV_PHASE_ALLOW_UNCHANGED_TARGET`). The map-contract
check (`tools/check_map_contract_version.py`, `ci.yml` ~104) and `dependency-safety.yml:76`
still diff against the event base; with a moved base that diff also includes main's newer
commits. Not a release-identity matter and not new: unchanged here. The private-admission
sentence "checkout parents exactly event base/head" (`cicd.md` ~2066) describes a separate
record; no code enforces exact parents there (`ci_attest.py`, `ci_provenance.py` only check
that parents are well-formed): leave it alone.

## Review and merge

Independent Claude and Codex plan reviews (round 1 done 2026-11-26; this revision folds
both) before READY; independent patch reviews; workflow-inventory and release-target tests,
ruff and changed-module types; full CI. Owner merges.
