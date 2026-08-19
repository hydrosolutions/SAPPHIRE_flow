---
status: DRAFT
created: 2026-08-19
revised: 2026-08-19
reviewed: independent Codex pass 2026-08-19 — AGREE-WITH-CHANGES on T4 (its corrections adopted verbatim)
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

## T4 — a stalled apt fetch must fail fast, not eat the whole job budget

*Distinct concern from T1–T3, folded in because it edits the same files in the same review context.*

**Observed (run 32223476140, PR #189).** The `unit` job was cancelled at 25m15s. Per-step timings show it
never reached pytest:

```
 4. Install system deps for cfgrib / rioxarray / exactextract: cancelled  06:27:28 -> 06:52:34
 5..11 (incl. "Run uv run pytest tests/unit/ ..."):                       SKIPPED
```

The step's log ends at `06:28:06  Get:5 https://archive.ubuntu.com/ubuntu noble-security InRelease`, then
24m28s of silence, then `##[error]The operation was canceled` — the job-level `timeout-minutes: 25`
(`ci.yml:88`) firing. Contemporaneous `main` runs at 06:12/06:22/06:32 completed the same step normally, so
the cause was transient and environmental, not the PR's code.

**Precision (Codex, 2026-08-19).** The log proves the step *stopped making observable progress*. It does
**not** prove the fetch itself hung rather than APT processing or the runner's network becoming unhealthy.
The 25-minute alignment with the cap is what makes the timeout the compelling explanation — PR concurrency
(`ci.yml:23`) can also cancel a run, but not with that timing.

**Why it is worth fixing even though the cause was transient.** The failure presents as *"CI cancelled after
25 minutes"*, which reads as a slow or hanging test suite — it sent this session investigating its own new
tests first. A step-level deadline converts a 25-minute mystery into a fast, correctly-attributed failure.

**The change — apply this exact step body to ALL THREE occurrences** (`ci.yml:100` in `unit`, `ci.yml:323` in
`integration`, and `.github/workflows/integration-nightly.yml:42`):

```yaml
- name: Install system deps for cfgrib / rioxarray / exactextract
  timeout-minutes: 5
  run: |
    sudo apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update
    sudo apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 \
      install -y --no-install-recommends \
      libeccodes0 libexpat1 libgeos-c1v5
```

**`Acquire::http::Timeout=30` is load-bearing, not decoration.** This session's first proposal was retries
alone, which is wrong: *retries retry downloads that FAIL; they cannot retry a request that never fails.*
A stalled socket needs a transport timeout. HTTPS inherits the HTTP setting unless separately overridden, so
one option covers both. The step-level `timeout-minutes: 5` is the backstop for everything the transport
timeout does not cover; the job-level 25 remains the outer limit and whichever deadline arrives first wins.

**Do NOT "simplify" this by deleting the apt step** — checked, and it is required:
- unit tests parse real GRIB through cfgrib (`tests/unit/adapters/test_meteoswiss_nwp_real.py:54`);
- they execute the real exactextract path (`tests/unit/preprocessing/test_exact_extract_grid_extractor.py:100`);
- decisively, the locked `eccodes` package does **not** pull `eccodeslib` — it lists only the thin bindings
  and `findlibs` (`uv.lock:1181`) — so `libeccodes0` must come from apt.

`libexpat1` and `libgeos-c1v5` *may* be redundant for current x86_64 manylinux wheels, but proving that needs
a clean-runner experiment and would not remove the `apt-get update` that ecCodes still requires. **Out of
scope** — recorded so it is a deliberate deferral.

**Acceptance:** the three steps carry the deadline and both apt options; a normal run is unaffected. Not
test-locked — this is workflow configuration whose failure mode is a real network stall, which a unit test
cannot reproduce honestly. Deliberate: a test asserting the YAML string would be the very brittleness T1
exists to remove.

## Non-goals

- No change to what CI actually runs. `-n auto` stays; this plan does not relitigate #185.
- No redesign of the Plan 185 credential-absence guard.
- No sweep of other guard files. Two pins were examined; only one is misscoped.
- T4 adds no caching, mirror rewriting, or package removal. Codex assessed those and found none warranted.

## Open question for the owner

**Who lands this?** The test belongs to Plan 185/186's author. If that session is mid-flight, a competing
PR collides. Options: (a) this session opens the PR now, since main is red and the fix is three assertions;
(b) hand it to the owning session. **Recommend (a)** — main being red blocks everyone, and the change is
confined to one test class plus one comment.
