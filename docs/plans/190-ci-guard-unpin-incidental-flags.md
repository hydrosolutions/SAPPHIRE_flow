---
status: DRAFT
created: 2026-08-19
revised: 2026-08-19
plan: 190
title: Unpin the CI unit-step guard from flags it does not care about
scope: Fix the guard assertion that has held main red across four merges, and remove the recurrence. Explicitly NOT redesigning the Plan 185 guard, NOT touching the credential-absence logic, NOT changing what CI runs.
depends_on: []
blocks: [every merge to main]
source: observed CI failure on d532a130, 4ae7cf82, e74e3e1c (2026-08-18/19)
---

# Plan 190 — unpin the CI unit-step guard from flags it does not care about

## Status
**DRAFT.** Not for implementation until the owner confirms.

**Urgency:** `main` is red and has been across four consecutive merges. Every PR branched from `main`
inherits the failure, so no one can get a clean CI signal on their own work — including the restore-
rehearsal fix currently in flight.

## What broke, and why nothing caught it

Two PRs, each green on its own branch, are red in combination:

| PR | Change |
|---|---|
| **#185** (`fd96b56f`) | changed the `unit` job's final step to `uv run pytest tests/unit/ -n auto --cov=src/sapphire_flow --cov-report=term-missing` |
| **#187** (Plan 185) | added a guard asserting that step equals `uv run pytest tests/unit/ --cov=src/sapphire_flow --cov-report=term-missing -v` |

`-n auto` was added; `-v` was removed. The failure:

```
tests/unit/tooling/test_ci_credential_absence_guard.py:382
AssertionError: assert 'uv run pytes...=term-missing' == 'uv run pytes...rm-missing -v'
```

**This is a semantic conflict git cannot detect** — the two PRs touch different files, and nothing links
the test to the workflow it pins. Neither author did anything wrong. The merge order decided who broke.

## The actual defect is scope, not the literal

`TestFinalUnitTestStepUnchanged` exists to prove Plan 185's new credential-gating steps did not *replace or
weaken the real test run*. That is a good guard. But it asserts **full-string equality on a shell command**,
so it also pins `-v` and forbids `-n auto` — verbosity and parallelism, which have nothing to do with its
safety property.

Note that the file's own documented rationale scopes full-string comparison narrowly, to `if:` conditions
(`test_ci_credential_absence_guard.py:9-12`): *"Where an `if:` condition is itself the safety property under
test … assertions compare the FULL normalized condition string, not substrings — a substring check passes
even after the condition's `!`/`&&`/`||` structure is broken."* That reasoning is sound **for boolean
expressions**, where partial matching genuinely hides broken logic. A `run` command has no boolean structure
to break, so the rule was over-applied here.

## T1 — assert the property, not the string  *(unblocks main)*

Replace the equality with assertions on what the guard actually protects:

1. the last step still runs `pytest` over **`tests/unit/`** — the whole directory;
2. coverage is still measured on **`src/sapphire_flow`**;
3. the suite is **not narrowed** — no `-k`, `-m`, `--ignore`, or `--deselect`.

(3) matters: relaxing (1)+(2) alone would let someone quietly shrink the suite, which is exactly the
regression this guard exists to prevent. Verbosity, parallelism and coverage-report format stay unpinned.

**Acceptance:** the assertion passes against `ci.yml` as it stands today, and fails if the step is narrowed
with `-k`/`--ignore` or loses `--cov`. Both directions test-locked; the narrowing case proven red.

## T2 — make the coupling visible from the workflow side

Add a comment above the `unit` job's final step in `.github/workflows/ci.yml` naming the guard file that
pins it. There is **no back-pointer today** (`grep` for the guard file in `ci.yml` returns nothing), so a
person editing CI has no way to know a test constrains it. One comment line converts a post-merge surprise
into a pre-edit signal.

## T3 — decide the sibling pin, do not sweep

`test_ci_credential_absence_guard.py:301` also pins a full command: `uv sync --frozen --extra aquacast`.

**Recommendation: leave it.** That command *is* the safety property — the exact install behaviour under
test — and it carries no verbosity/parallelism knobs to drift. Loosening it would weaken a guard that is
not currently broken. Recorded here so the decision is deliberate rather than an omission.

## Non-goals

- No change to what CI actually runs. `-n auto` stays; this plan does not relitigate #185.
- No redesign of the Plan 185 credential-absence guard.
- No sweep of other guard files. Two pins were examined; only one is misscoped.

## Open question for the owner

**Who lands this?** The test belongs to Plan 185/186's author. If that session is mid-flight, a competing
PR collides. Options: (a) this session opens the PR now, since main is red and the fix is three assertions;
(b) hand it to the owning session. **Recommend (a)** — main being red blocks everyone, and the change is
confined to one test class plus one comment.
