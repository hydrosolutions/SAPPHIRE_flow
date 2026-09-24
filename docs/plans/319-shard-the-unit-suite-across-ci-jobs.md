---
status: READY
created: 2026-09-24
plan: 319
title: Shard the unit suite across parallel CI jobs
scope: Split the single `unit` CI job into N jobs that run concurrently on separate runners, so the suite's wall-clock on GitHub stops being bounded by one small machine. The CI workflow, whatever proves the split is exhaustive, and the CI documentation AGENTS.md requires every code change to update (`docs/standards/cicd.md`). NOT the tests themselves, NOT `-n auto` within a job (already there, Plan 300), NOT the integration or nightly jobs, NOT runner size (costs money, not effort).
depends_on: []
blocks: []
related: [201, 300]
open_decisions: []
reviews:
  - "codex 2026-09-24 r1 — NOT READY, 3 HIGH: wrong-kind counts, a T1 self-contradiction, and a directory check that misses the root-level files"
  - "codex 2026-09-24 r2 — NOT READY, 1 HIGH: counts STILL wrong — measured in the Plan 316 worktree, not the main checkout"
  - "codex 2026-09-24 r3 — evidence INDEPENDENTLY VERIFIED (5842, every row, 44 root-level, parts sum); one leftover contradicting word, folded"
source: 2026-09-24 — the owner, after PR #300 landed: "CI is still really slow on github". Measured: #300 fixed FLAKINESS, not speed — CI already ran `-n auto`, so it got no faster.
---

# Plan 319 — shard the unit suite across parallel CI jobs

⚠️ **Plan number PROVISIONAL until the owner grants it.**

## Status

**READY** — set by the orchestrator 2026-09-24 on the owner's instruction, after
three independent review rounds (see frontmatter) and with both decisions
closed.

⚠️ **Not high-risk.** CI configuration only; no product code, no deployment.
(There is no production deployment — see README.)

## Why this plan exists

Measured on this machine, same tree, `tests/unit`:

| | |
|---|---|
| serial | 11m18s |
| `-n auto` (12 cores) | 2m49s |
| `-n auto` **with coverage**, as CI runs it | 3m09s |

**CI takes 12–13 minutes for the same job** (`unit` on PR #297: 11m53s; on
#299: 13m22s). ⇒ It is already parallel, and coverage costs only ~11%. The
difference is the machine: `ubuntu-latest` gives 2–4 cores against 12 here, on
slower silicon. **More parallelism inside one runner cannot fix that. More
runners can.**

## What is measured

Measured on `main` at `241e97be`, **in the main checkout** — ⚠️ not in a feature worktree; see § (5).

1. **The job and its command.** `.github/workflows/ci.yml:331` —
   `uv run pytest tests/unit/ -n auto --cov=src/sapphire_flow --cov-report=term-missing`,
   `runs-on: ubuntu-latest` (`:95`), `timeout-minutes: 30` (`:105`).
2. 🔴 **The setup is NOT free, and this is the plan's central trade-off.** Before
   pytest runs, the job does checkout, `setup-uv`, and **`apt-get` for cfgrib /
   rioxarray / exactextract** — bounded at 8 minutes (`:137`) with a comment
   recording that it **stalled EIGHT times on 2026-08-19** where a sibling
   runner finished the same command in ~75 s. ⇒ **N shards pay that setup N
   times and take N times the exposure to that step.** ⚠️ **Stated as a risk to
   MEASURE, not a predicted failure rate:** the workflow also records a
   subsequent mirror change (`:120`) intended to fix exactly that stall, and
   this plan has **no post-fix failure rate** to argue from. The honest claim is
   that N shards multiply the exposure N-fold, not that a red is likely. ⇒ D1's
   shard count should be treated as a dial to turn, not a target to maximise.
3. **Coverage is INFORMATIONAL, not a gate.** `--cov-report=term-missing`
   prints; no `--cov-fail-under` exists anywhere in `pyproject.toml` or the
   workflow. ⇒ Sharding it is a reporting question, not a pass/fail one.
4. **One step must NOT be sharded.** `.github/workflows/ci.yml:315-325` runs four
   named files **sequentially** as the Plan 201 ordering-leak canary, and
   asserts an EXACT node id reports PASSED. It must run exactly once, in one
   place, unchanged.
5. **Test distribution, by `pytest --collect-only`** — measured on `main` at
   **`241e97be`**, in the MAIN CHECKOUT.

   ⛔ **Two earlier drafts got this wrong, in two different ways, and both are
   recorded because the METHOD was the error:**
   - The first counted `def test_` in files. Wrong in KIND, not just value:
     parametrisation makes definitions and collected tests diverge unevenly
     (`flows` — 530 definitions, **601** collected).
   - The second used `--collect-only` but **ran it in the wrong worktree** — the
     Plan 316 branch, which carries 316's own new tests *and a `tests/unit/fakes`
     directory created by 316 T1*. Those numbers described a tree that is not
     this baseline. A reviewer caught it by recomputing and predicting the
     baseline total exactly.

   | dir | collected | | dir | collected |
   |---|---|---|---|---|
   | `scripts` | 1757 | | `api` | 97 |
   | `services` | 1215 | | `tools` | 77 |
   | `adapters` | 679 | | `deploy` | 71 |
   | `flows` | 601 | | `store` | 40 |
   | `ops` | 340 | | `preprocessing` | 40 |
   | `types` | 237 | | `db` | 21 |
   | `config` | 208 | | `diagnostics` | 16 |
   | `models` | 156 | | `docs` | 13 |
   | `tooling` | 115 | | **root-level** | **44** |
   | `cli` | 115 | | | |

   **Whole suite: 5842 collected** — and the parts sum to exactly that, which is
   the arithmetic check the first two drafts never performed.

6. 🔴 **THERE ARE TEST FILES DIRECTLY IN `tests/unit/`, IN NO SUBDIRECTORY — 44
   collected tests.** `test_config.py`, `test_check.py`,
   `test_structlog_cache_isolation.py` and others. ⛔ **An earlier draft of this
   plan warned that a directory split silently drops what it forgets, and then
   recommended a directory split that dropped these 44.** The hazard section
   below is not theoretical; it already caught this plan's own recommendation.

7. **`pytest-split` is not a dependency** (`pyproject.toml`, `uv.lock`).

## The hazard this plan exists to avoid

🔴 **A test in no shard runs nowhere, and NOTHING FAILS.** Any split by name or
directory silently stops covering whatever it forgets — a new top-level
directory, a renamed one. The suite goes green having run less.

⇒ **Whatever D1 chooses, the split must be exhaustive BY CONSTRUCTION or proved
exhaustive by a test that fails when it is not.** This is not optional and it is
the one thing an implementer must not treat as a nicety.

## Owner decisions

### D1 — How to split, and into how many?

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **4 shards**: `scripts` (1757) / `services` (1215) / `adapters`+`flows` (1280) / **everything else, COMPUTED** (1590, the 44 root-level tests included). | No new dependency. ⛔ The fourth group must be *"whatever the first three are not"*, derived at run time — **never a list**, or § (6)'s 44 root-level tests come back. |
| (b) | **`pytest-split`**, which balances on a recorded durations file. | Properly balanced on DURATION, and shard count becomes a number to turn. A new dependency, plus a durations file that must be regenerated as the suite changes or the balance silently rots. |
| (c) | **2 shards.** | Halves the setup multiplication and the § (2) exposure. Roughly half the speedup. |

**⚖️ CLOSED — owner, 2026-09-24: (a), four shards.** Recorded with what it can
and cannot promise stated plainly rather than implied.

⚠️ **A count-based estimate of ~3.3×, NOT a ceiling.** The largest shard
(`scripts`, 1757) is **30% of 5842**, and a sharded job is only as fast as its
slowest shard — so *if tests took equal time* the best case would be ~30% of the
current wall-clock rather than 25%. ⛔ *An earlier draft called this a "ceiling".
It is not: collected counts cannot bound wall-clock at all. The real figure could
be better or considerably worse, which is precisely why T1 must report each
shard's measured time.*

⚠️ **And collected counts are still not DURATION.** They are a better proxy than
definition counts (§ 5), not a good one — a single slow integration-ish unit test
outweighs a hundred fast ones. ⇒ **T1 must report each shard's wall-clock on the
first run**, and if the split proves lopsided, (b) is the answer. This plan says
so now rather than discovering it later.

### D2 — What happens to the coverage number?

Today one job prints whole-suite coverage. Sharded, each shard sees a fraction.

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Each shard writes a coverage data file; one small follow-on job combines them and prints the whole-suite number.** | Preserves exactly what exists today. One more job, and the shards must upload/download artifacts. |
| (b) | **Drop coverage from the PR path; measure it on `main` only.** | Simplest, and removes the ~11%. ⛔ The number disappears from the PR where someone might act on it. |
| (c) | Print per-shard coverage and accept four partial numbers. | ⛔ Rejected: four numbers that each look like a regression against the old one, and none is comparable. |

**⚖️ CLOSED — owner, 2026-09-24: (a), stitch the shards back into ONE number.**
⚠️ It is informational either way (§ 3) — this was about what the owner wants to
SEE on a PR, not about a gate. ⛔ Do NOT add a threshold while wiring this up;
none exists today and adding one is a different decision.

## Tasks

### T1 — Shard the job, and prove the split is exhaustive

**Outcome.** The unit suite runs across N concurrent jobs; every test that ran
before still runs; the wall-clock of the slowest shard is reported.

**In.**
- A matrix over the D1 groups, each running the same pytest invocation scoped to
  its group, with `-n auto` retained inside each shard.
- 🔴 **The exhaustiveness proof, and it must be by COLLECTED NODE ID.** ⛔ *An
  earlier draft proposed checking DIRECTORIES. That is insufficient twice over:
  it cannot see the 44 root-level tests in § (6), and directory coverage plus a
  total count still cannot show that each node runs EXACTLY ONCE rather than one
  node twice and another never.* The check compares **the union of the shards'
  `--collect-only` node ids against the unsharded collection** — equal as sets,
  and no node in two shards.
- **The Plan 201 sequential canary stays in exactly ONE place**, unchanged and
  unsharded (§ 4).
- **Each shard reports its own wall-clock**, so D1's proxy can be checked
  against reality on the first run.

**In (cont.).** The `docs/standards/cicd.md` update — AGENTS.md requires every
code change to update affected docs, and the CI topology is exactly that.

**Out.** ⛔ Any change to a test. ⛔ **Removing or altering `-n auto`** — it stays
inside each shard exactly as it is today (⛔ *an earlier draft's Out-list said
"`-n auto` inside a shard", which contradicted the In-list requiring it; an
implementer could not satisfy both*). ⛔ Integration or nightly jobs. ⛔ Runner
size.

**Pre-change.** A RED test asserting the DESIRED behaviour: the exhaustiveness
check fails when a directory is missing from the shard definitions. ⛔ *Not "the
job is slow", which is not a test.*

**Verification.**
- The union of the shards' collected NODE IDS equals the unsharded collection,
  as sets, with no node in two shards.
- Removing any group from the shard definitions **fails** the check — and so
  does duplicating one, which a count-only check would not catch.
- **The 44 root-level tests run in exactly one shard**, asserted by name, because
  they are what this plan's own first draft dropped.
- The Plan 201 canary still runs sequentially, once, and still asserts its exact
  node id.

### T2 — Keep the coverage number whole (D2)

**Outcome.** Whatever D2 chooses, a reader of a PR gets one coverage number or a
stated reason there is none.

**In.** D2's choice, and a one-line note in the workflow saying which was chosen
and why.

**Out.** ⛔ Introducing a coverage THRESHOLD — none exists today (§ 3) and adding
one is a different decision.

**Pre-change.** N/A — reporting.

**Verification.** A PR shows one whole-suite number (D2a), or the documented
absence (D2b).

## Explicitly out of scope

- **`-n auto` within a job** — Plan 300, merged.
- **Bigger runners** — costs money, not effort; the owner can take that
  independently and it would compose with this.
- **The integration and nightly jobs** — different shapes, different budgets.
- **Making the apt step reliable** — § (2) is a REASON for caution here, not a
  task; it has its own history and its own comment.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] }
  ]
}
```
