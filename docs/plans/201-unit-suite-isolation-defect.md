---
status: COMPLETE
created: 2026-08-27
plan: 201
title: 13 unit tests fail in a sequential run and CI cannot see it
scope: Find and fix the state leak that makes 13 log-assertion tests fail in a full sequential `pytest tests/unit/` run while passing in isolation and under `-n auto`, and close the CI blind spot that hides this class of defect. NOT a test-framework migration, NOT a logging redesign, NOT re-running or re-tuning any other suite.
depends_on: []
blocks: []
source: discovered 2026-08-26 while verifying Plan 151 T8b against the plan's "no baseline test regressing" gate
---

# Plan 201 — 13 unit tests fail sequentially, and CI cannot see it

## Status

**IMPLEMENTED — held at PR #220 (2026-08-28).** Branch `fix/plan-201-test-isolation`, v0.1.823. The
decisive gate: sequential `pytest tests/unit/` went **13 failed → 4707 passed, 0 failed**; `-n auto`
unchanged at 4707. Two review majors were verified by direct probe and fixed (the guard had been
installed via the shared `monkeypatch` fixture and was strippable by
`tests/unit/ops/test_watchdog.py:5031`'s `undo()`; the nightly sequential step ran before the slow and
live suites and would have skipped them). The reviewer-proposed behavioural regression test passed
against BOTH the vulnerable and the fixed conftest, so it was replaced with a mechanism-level test
proven to fail against the vulnerable version.

**READY** — owner flip 2026-08-28. T1 is solved (8-second reproducer); T3 is ratified (layered).

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**This is one state leak and one CI gap.** No test-framework migration, no logging redesign, no
reorganisation of the suite. Reviewers: "no findings" is a complete review; a finding must name a
concrete failure, not a missing feature. Adding length is a cost.

## The defect, measured

| Run mode | Result |
|---|---|
| `pytest tests/unit/` sequential, full | **13 failed**, 4692 passed *(re-measured 2026-08-28; was 4293 on 2026-08-27 — the suite grew, the 13 did not)* |
| The 4 affected files alone, sequential | 124 passed |
| The same 4 files under `-n auto` | 124 passed |
| `tests/unit/services/` alone | 1003 passed |
| `ops` / `scripts` / `flows` / `api` / `adapters`, each paired with the failing tests | all pass |
| **CI on `main`** | **green** |

**It is deterministic**: the same 13 tests, in the same order, across two independent full runs on two
different branches (`main` and the Plan 151 T8b branch). That rules out random ordering.

**The current CI partition masks it.** `.github/workflows/ci.yml:277` runs `pytest tests/unit/ -n auto`.
xdist workers each run *chunks* of tests sequentially, so a polluter and victim CAN in principle land in
the same worker — the accurate claim is that **today's partition happens to separate them**, not that CI
could never expose an ordering leak. Either way CI is green while a sequential run is red, and nothing
makes that reproducible.

**It is recent.** A full sequential `tests/unit/` run on 2026-08-24 (the Plan 199 branch) was **4207
passed, 5 skipped — clean**. So the leak entered `main` in a roughly two-day window, which is a small
bisect range.

**Cost today:** anyone running the documented `uv run pytest tests/unit/` locally sees 13 red tests with
no way to know they are spurious. That erodes the signal the whole review process leans on — and it
already cost real time: this was found while checking whether Plan 151 T8b had regressed the baseline,
and answering "no" required a full comparison run against `main`.

## Staleness re-check, 2026-08-28

Re-verified against `main` after Plan 207 merged, since that PR changed `ci.yml`:

- **The defect is live and unfixed.** The four-file reproducer still gives `1 failed, 27 passed` in ~10 s.
- **Counts:** `13 failed, 4692 passed` — the failure count, and its distribution across the same four
  files, is **unchanged**; only the pass count moved as the suite grew.
- **Citations hold:** `ci.yml:277` is still the unit job's command and `ci.yml:3` is still `on:` —
  Plan 207's edits were all below line 500. `integration-nightly.yml` is still `cron: "0 3 * * *"`,
  which T3 layer 3 depends on. `cicd.md:503` is untouched.
- **No collision with Plan 207:** T3 layer 2 adds a step to the `unit` job; 207 changed
  `build-image-and-scan`.

**Plan 206 depends on this plan and cannot see it.** `docs/plans/206-cicd-standard-matches-the-workflows.md`
deliberately leaves the unit-suite row (`cicd.md:503`) to T3 and states that **if Plan 201 is dropped,
that row must be re-filed**. So T3's doc-sync must correct `cicd.md:503`, or 206 lands with a knowingly
stale row.

## ⭐ ROOT CAUSE LOCALISED — a 4-file, 8-second reproducer (2026-08-27)

**T1 is essentially done.** An empirical shrink-bisect from the full 255-file collected order produced
a **minimal reproducer that runs in 8 seconds**, replacing the 8.5-minute full-suite signal:

```bash
uv run pytest \
  tests/unit/cli/test_export_forecast_lab.py \
  tests/unit/flows/test_compute_skills.py \
  tests/unit/scripts/test_backfill_meteoswiss_history_script.py \
  tests/unit/services/skill/test_combined_skill.py
# -> 1 failed, 27 passed   (deterministic: 3/3 runs identical)
```

**Control:** drop the first file and it is **23 passed** — green. The trigger is
`tests/unit/cli/test_export_forecast_lab.py`, which arrived with **Plan 198 inside the two-day window**,
matching the "it is recent" evidence exactly.

**It is a THREE-WAY interaction, which is why eleven build-up attempts failed.** All three are required:

| Role | File |
|---|---|
| **Trigger** | `tests/unit/cli/test_export_forecast_lab.py` — calls a `main()` that runs `configure_cli_logging()` |
| **Sensitiser** | `tests/unit/flows/test_compute_skills.py` — exercises the skill-service logger the victim later asserts on |
| **Any following work** | `tests/unit/scripts/test_backfill_meteoswiss_history_script.py` — one file suffices; several others do NOT, so this slot is not purely "bulk" |
| **Victim** | `tests/unit/services/skill/test_combined_skill.py` — `capture_logs()` returns `[]` |

Removing **any one** of the three makes it pass. That is why no pair, no directory pairing and no
theory-driven chain ever reproduced it.

**Method note for anyone re-running this.** Eleven BUILD-UP attempts (pairing suspects with the victim)
all failed before the shrink-from-full-order approach worked in ~10 iterations. Build-up cannot find a
three-way interaction. Also: **zsh does not word-split unquoted variables** — an early bisect run passed
its whole file list to pytest as ONE argument and reported "no tests ran", which was misread as a pass.
Use `${=VAR}`, and always confirm the run actually collected tests.

## ⚠️ Two mechanism hypotheses are DISPROVED by instrumentation — do not re-adopt them

A probe was injected at the END of both a failing and a passing run, reporting structlog's global state:

```
FAILING:  root_handlers=3  cache_on_first_use=True  closed_streams=0
PASSING:  root_handlers=3  cache_on_first_use=True  closed_streams=0
```

**Identical.** Therefore:

- **`cache_logger_on_first_use=True` is NOT the discriminator.** It is left set in passing runs too. The
  draft's leading hypothesis, and the first review's variant of it, are both **insufficient** — the
  global config is the same either way.
- **No closed stream survives to the end** (`closed_streams=0`), so the `ValueError: I/O operation on
  closed file` is **transient, mid-run** — not the terminal state the fix must target.

**What remains to explain (the real T1 remainder):** why the *particular* skill-service logger the
victim asserts on is left bypassing `capture_logs()`, when global structlog config is identical between
passing and failing runs. The three-way structure points at **which loggers were cached, and in what
order** — per-logger state, not global config.

## Tasks

### T1 — Name the polluter

Bisect the sequential run to the smallest set that reproduces, and identify the exact test (or import)
that leaves structlog in a state where `capture_logs()` returns `[]`. `pytest` supports this directly
(`--deselect`, or running a prefix of the collected order) — **do not build a bisect harness**; a shell
loop over `--deselect` is sufficient.

**Exit:** a named test, and a one-command reproduction of the form "run X then Y, Y fails". Also
establish whether `ValueError: I/O operation on closed file` is the same defect or a second one — it may
be a symptom of the same closed stream, or an independent problem, and the plan must not assume.

### T2 — Fix the leak

Fix depends on T1's finding. If it is the caching hypothesis, the fix is very likely to **use the
capture-safe configurator that already exists** — an autouse fixture in `tests/conftest.py` calling
`configure_test_logging()` (or `structlog.reset_defaults()`) so each test starts from a known state.

**Constraint:** the fix must not silence the symptom by making the affected tests skip, tolerate empty
captures, or assert less. Those tests are the only check on log-event contracts that
`docs/standards/logging.md` defines.

**Red-first:** the reproduction from T1 must fail before the fix and pass after.

### T3 — Let CI see this class of defect

CI's `-n auto` will keep hiding ordering leaks. **Owner decision required — see below.** Whatever is
chosen must be one CI change, not a new workflow.

## ✅ T3 RESOLVED (owner, 2026-08-27) — layered, not a sequential CI run

**All four drafted options were rejected once the costs were measured.** The draft said ~8.5 min; the
first review corrected it to ~15 min; both were wrong. Measured:

| | |
|---|---|
| CI `unit` job today (`-n auto`) | **10-11 min** |
| Local sequential : local parallel | 506 s : 130 s = **3.9x** |
| **Extrapolated CI sequential** | **~20-40 min** |
| Pushes to `main`, last 7 days | **86** — only **18** are PR merges |

Option (b) — the draft's recommendation, and the first review's — looked like 18 runs a week. It is
**86**, because `ci.yml:3` fires on every push and this repo commits plan docs to `main` constantly. At
~30 min each that is **~43 CI-hours per week** for one defect class. Rejected.

**RATIFIED — three layers instead:**

1. **T2 fixes the leak** (unchanged).
2. **A targeted sequential regression check in the existing `unit` job: ~8 seconds.** Run the four-file
   reproducer sequentially and assert it is green. This locks *this* defect permanently at effectively
   zero cost, and is only possible because T1 produced a minimal reproducer.
3. **The full sequential run goes in the existing nightly workflow**
   (`.github/workflows/integration-nightly.yml`, already `cron: "0 3 * * *"`). This catches *future,
   unknown* leaks of the same class, costs **nothing** on PRs or merges, and follows the precedent that
   workflow already sets — trading latency for coverage on expensive checks.

**Accepted costs, named:** a nightly cadence means up to 24 h before a new leak surfaces (acceptable —
the harm is a misleading local signal, not broken production); and layer 2 guards only the *known*
interaction, so if T2's fix is properly general it becomes a canary rather than the defence.

## Non-goals

Migrating off `structlog`, redesigning `docs/standards/logging.md`, changing what any of the 13 tests
assert, touching the integration suite *(the nightly workflow IS touched — T3 layer 3 adds one step there; this Non-goals line predates the ratified T3 decision and is corrected here rather than left to read as a violation)*, and fixing unrelated pre-existing failures.

## Exit gates

```bash
uv run pytest tests/unit/ -q            # sequential, full: 0 failed
uv run pytest tests/unit/ -n auto -q    # unchanged, still green
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

**Doc sync:** `docs/standards/cicd.md:503` **already documents the unit job WITHOUT `-n auto`** — the
doc and `ci.yml:277` disagree today, so T3 must correct that row whichever option is chosen;
`docs/standards/logging.md` if the fix constrains how tests may configure logging;
`CLAUDE.md` § Testing if the local command guidance changes.
