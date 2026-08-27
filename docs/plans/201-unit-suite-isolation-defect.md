---
status: DRAFT
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

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS A BINDING CONSTRAINT

**This is one state leak and one CI gap.** No test-framework migration, no logging redesign, no
reorganisation of the suite. Reviewers: "no findings" is a complete review; a finding must name a
concrete failure, not a missing feature. Adding length is a cost.

## The defect, measured

| Run mode | Result |
|---|---|
| `pytest tests/unit/` sequential, full | **13 failed**, 4293 passed |
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

## What is known about the mechanism

All 13 failures are **log-assertion tests** (`test_coverage_log_message`, `test_fallback_warning`,
`test_short_lookback_warning`, the `TestPerAssignmentWarmUpState` cases). They capture emitted events
with `structlog.testing.capture_logs()` and assert on them.

The first failure asserts `len(coverage_events) == 1` and gets **`0 == 1` where `0 = len([])`** —
**nothing is captured at all**, rather than the wrong thing being captured. The run also emits
**`ValueError: I/O operation on closed file.`**

**Leading hypothesis (NOT yet proven — proving it is T1):** something reconfigures structlog with
`cache_logger_on_first_use=True` during the run. `src/sapphire_flow/logging.py` has four configurators;
**three set `cache_logger_on_first_use=True`** (`:40`) and only `configure_test_logging()` sets it
`False` (`:107`). Once loggers are cached, a later `capture_logs()` cannot see through them and returns
`[]`. `configure_cli_logging` is called at **8 sites** in `src/` — five CLI modules, two tools, and
`ops/watchdog.py:1782`, which calls it as `configure_cli_logging("INFO")` and is missed by a naive
`configure_cli_logging()` grep. Any test exercising one of those can trigger it.

**A striking fact:** `configure_test_logging()` — the one configurator that is capture-safe — has
**ZERO callers** anywhere in `src/` or `tests/`. It appears to have been written for exactly this
problem and never wired up.

**Ruled out by measurement, so the plan does not re-tread them:** it is not random ordering; it is not
one directory poisoning another (five pairings all pass); it is not `tests/unit/test_logging_override.py`
alone poisoning `test_combined_skill.py` (that pairing passes, 18 tests).

**⚠️ A SECOND, MORE DETAILED HYPOTHESIS WAS ALSO TESTED AND DISPROVED (2026-08-27).** An independent
review proposed this specific chain: `tests/unit/cli/test_export_forecast_lab.py` calls a `main()` that
invokes `configure_cli_logging()` (`cli/export_forecast_lab.py:175`, caching ON) → flow/service tests
then cache the affected loggers → `tests/unit/scripts/test_acquire_era5_cli.py:546`
(`test_hostile_transient_error_never_leaks_through_main`) runs `main()` under `capsys`, so the
`StreamHandler` at `logging.py:53` binds to a temporary stderr that is closed at teardown → later
`capture_logs()` sees `[]` and stdlib routing hits the closed stream.

**It is a coherent story with real citations, and it does NOT reproduce.** Measured:
`test_export_forecast_lab.py` + `test_acquire_era5_cli.py` + `test_combined_skill.py` → **41 passed**.
Adding the caching step (`+ services/test_operational_inputs.py`, in chain order) → **78 passed**.
So the mechanism needs something further, and **T1 remains a real investigation, not a formality.** The
chain is recorded because its components are verified even though the whole is not: the two tests do
call those configurators, and `logging.py:53` does bind a `StreamHandler`.

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

## ⚠️ OPEN DECISION FOR THE OWNER — how much should CI pay to see this?

**T3 is a real trade-off and the plan does not presume the answer.**

- **(a) Add a sequential `tests/unit/` run to CI.** Catches this class directly. **Cost is HIGHER than
  the draft implied** *(corrected by review)*: ~8.5 min is a **local** measurement on this machine. CI's
  own history records the pre-xdist suite at roughly **15 min of a 16-min job** with **fewer** tests than
  today's 4,429 (`ci.yml:272`). Treat the CI cost as ~15 min, not 8.5.
- **(b) Run sequentially only on `main` pushes, not PRs.** Keeps PR feedback fast; catches the leak
  within one merge instead of at the next local run. **Recommended** — it matches how the nightly
  integration job already trades latency for coverage, and this defect's harm is slow (a misleading
  local signal), not acute.
  **Two things (b) must specify, or it is under-defined:** whether the sequential run **replaces** main's
  parallel run or **runs in addition** to it; and that `ci.yml:3` triggers on **every push to `main`**,
  not only merges — including the direct plan-document commits this repo makes constantly, each of which
  would pay the cost.
- **(c) Fix the leak and accept the blind spot.** Cheapest, and honest only if we accept that the next
  leak is found by whoever next runs the suite locally — which is how this one was found.
- **(d) Randomise order and run parallel** (e.g. `pytest-randomly`). Surfaces ordering coupling in
  general, but makes CI **non-deterministic**, which is a poor trade for a repo whose review process
  depends on reproducible gates.

**Recommendation: (b).** It buys the detection at a cost paid on merge rather than on every PR.

## Non-goals

Migrating off `structlog`, redesigning `docs/standards/logging.md`, changing what any of the 13 tests
assert, touching the integration or nightly suites, and fixing unrelated pre-existing failures.

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
