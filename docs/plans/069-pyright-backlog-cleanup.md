# Plan 069 — Pyright backlog cleanup: ratchet + drain

**Status**: DRAFT
**Date**: 2026-04-22
**Depends on**: Plan 073 (concrete violations outside `flows/`) → Plan 069 Phase 1 →
Phase 2+. See §Cross-plan coordination for the full merge order.
**Scope**: Re-enable pyright type-checking as a CI merge gate by
(a) verifying and documenting the existing `pyrightconfig.json` (already strict
globally, with a `flows/` carve-out for Prefect decorator type erasure),
(b) fixing the stale `--strict` CLI reference in `docs/workflow.md`,
(c) capturing the post-Plan-073 error count (~611) as a ratchet baseline, and
(d) draining the remaining backlog under that ratchet. No runtime behaviour
change.

---

## Cross-plan coordination

This plan is one of three DRAFTs addressing pyright / type-checking
hygiene after Plan 064. The three are:

- **Plan 070** (pre-commit hooks + gate parity) — prevents new
  lint/format/secret regressions during the drain.
- **Plan 073** (concrete type-violations cleanup outside flows/) —
  fixes 64 real-bug-rule violations before the ratchet captures a
  baseline.
- **Plan 069** (this plan — pyright backlog ratchet + drain) —
  freezes the post-073 baseline and drains remaining errors under
  the ratchet.

**Merge order (mandatory):** 070 → 073 → 069 Phase 1 → 069 Phase 2+.
Rationale: 070 stops new regressions from leaking in; 073 fixes real
bugs so the ratchet floor is clean; 069 Phase 1 captures the
post-Plan-073 total (~611) as the ratchet floor; 069 Phase 2+ drains
what remains.

**Baseline numbers:**
- 1078 = pre-experiment (no carve-out). Historical reference only.
- 675 = post-experiment, pre-Plan-073 (flows/ carve-out active).
- ~611 = post-Plan-073 (this plan's ratchet floor).

**Config location:** `pyrightconfig.json` at repo root is
authoritative. `[tool.pyright]` in `pyproject.toml` is NOT used —
pyright gives JSON precedence and silently ignores the TOML block
when both exist.

---

## Context

### Why now

Plan 064's tier-1 CI run surfaced that `uv run pyright --strict src/`
had never actually passed — `--strict` was removed as a CLI flag in
pyright 1.1.408. Plan 064 disabled the gate with a comment pointing
to "a separate follow-up plan". This is that plan.

On 2026-04-22, a configuration experiment was run: the existing
`pyrightconfig.json` (already `typeCheckingMode: "strict"` globally)
was augmented with an `executionEnvironments` carve-out that silences
the six Unknown-cluster rules inside `src/sapphire_flow/flows/`.
Result: 1078 → 675 errors (−403). The carve-out is correct: those
403 errors are Prefect `@flow`/`@task` decorator type erasure and
pandas/xarray/numpy propagation noise — not real bugs. The carve-out
is already committed to `pyrightconfig.json`.

Separately, investigation of the remaining 675 errors identified 64
concrete violations outside `flows/` (Plan 073 scope) and 166
concrete violations inside `flows/` (Plan 069 Phase 2 scope). After
Plan 073 lands, the ratchet floor is ~611.

The `lint` CI job currently runs only ruff + ruff format. Static
type-checking is our largest protection against refactor regressions
in a codebase heavy on generics (`Protocol` signatures, `NewType`,
frozen dataclasses) per CLAUDE.md's type-driven-development rules.
Leaving it off indefinitely erodes that investment.

### The shape of the backlog (measured 2026-04-22, post-carve-out)

**Total: 675 errors pre-Plan-073 (expected ~611 post-Plan-073)**

| Count | Pyright rule | Category |
|---|---|---|
| 146 | `reportUnknownVariableType` | "Unknown" cluster (carve-out silences this inside `flows/`) |
| 107 | `reportUnknownMemberType` | " |
| 102 | `reportUnknownArgumentType` | " |
| 37  | `reportUnknownParameterType` | " |
| 33  | `reportMissingTypeArgument` | " |
| 19  | `reportMissingParameterType` | Our own missing annotations — NOT silenced by the carve-out; fix in-place (T6/Phase 3) |
| 1   | `reportUnknownLambdaType` | " |
| **445** | **"Unknown" subtotal (non-flows)** | Phase 3 drain target |
| 103 | `reportArgumentType` (flows/) | **Phase 2 — T4** |
| 51  | `reportAttributeAccessIssue` (flows/) | **Phase 2 — T5** |
| 12  | `reportCallIssue`, `reportPrivateUsage`, etc. (flows/) | **Phase 2 — T6** |
| 124+76+other | all concrete rules (non-flows) | **Plan 073 scope — NOT this plan** |

**Concentration (top 10 files, post-carve-out):**

| Count | File |
|---|---|
| 121 | `src/sapphire_flow/services/onboarding.py` |
| 79  | `src/sapphire_flow/services/operational_inputs.py` |
| 50  | `src/sapphire_flow/services/model_onboarding.py` |
| 39  | `src/sapphire_flow/services/forecast_qc.py` |
| 36  | `src/sapphire_flow/flows/train_models.py` |
| 34  | `src/sapphire_flow/services/hindcast.py` |
| 33  | `src/sapphire_flow/flows/run_forecast_cycle.py` |
| 28  | `src/sapphire_flow/services/training_data.py` |
| 26  | `src/sapphire_flow/flows/run_hindcast.py` |
| 26  | `src/sapphire_flow/services/run_station_forecast.py` |

Flows files dropped dramatically vs the pre-carve-out table
(e.g. `run_forecast_cycle.py`: 144 → 33; `train_models.py`: 125 → 36)
because the Unknown-cluster rules are now silenced inside `flows/`.
Baseline captured to `/tmp/pyright_rewrite.json` on 2026-04-22.

### Principle

Ratchet, don't big-bang. We cannot pause feature work for weeks to
drain 611 errors, but we also cannot keep letting the count grow while
we drain it. The ratchet model: lock Plan-073's landing count as the
floor, fail CI if the count rises, then reduce the baseline one batch
at a time.

### Non-goals

- **No runtime behaviour change.** This plan only adds type
  annotations, moves callsite argument types, or suppresses a false
  positive with `# pyright: ignore[<rule>]` + comment. If a fix would
  change behaviour, stop and flag it as a latent bug for a separate
  plan.
- **No pyright version bump.** Locked at `1.1.408` in `uv.lock` today;
  re-evaluate only if a rule's severity changes materially.
- **No mypy addition.** Pyright stays the single type-checker.
- **No migration from `pyrightconfig.json` to `[tool.pyright]`.**
  `pyrightconfig.json` at repo root is authoritative; pyright ignores
  `[tool.pyright]` in `pyproject.toml` when both exist (see D1, D2).
- **No Prefect stub contribution upstream.** The `flows/` carve-out is
  our local mitigation for Prefect decorator type erasure; upstream
  contribution is out of scope.
- **No `# pyright: ignore` as a first-line tool.** Only accept an
  ignore when the underlying rule is a demonstrable false positive AND
  the comment explains why. Unbounded ignore sprawl defeats the point.

### Inputs

- `.github/workflows/ci.yml` — the `lint` job's pyright step is
  currently commented out (Plan 064 b17eaad), with a TODO pointing at
  this plan.
- `pyrightconfig.json` at repo root — `typeCheckingMode: "strict"`,
  `pythonVersion: "3.12"`, `executionEnvironments` carve-out silencing
  Unknown-cluster rules inside `flows/`. This is the authoritative
  config; it already exists and is correct.
- `docs/workflow.md` — Task Exit Gate still references the removed
  `uv run pyright --strict src/` flag; T1b fixes this.
- `uv.lock` — `pyright 1.1.408` (direct dev dep).
- `src/sapphire_flow/types/`, `.../protocols/`, `.../exceptions.py` —
  type-heavy core, already under full strict via `pyrightconfig.json`
  (no carve-out applies here).
- `src/sapphire_flow/flows/`, `.../services/` — where the backlog
  concentrates.
- `/tmp/pyright_rewrite.json` — 675-error baseline captured
  2026-04-22 (pre-Plan-073).

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **`pyrightconfig.json` is already correct and authoritative. Do NOT migrate to `[tool.pyright]` in `pyproject.toml`.** The existing config is `typeCheckingMode: "strict"` globally with an `executionEnvironments` carve-out for `flows/` that silences the six Unknown-cluster rules. Core paths (`types/`, `protocols/`, `exceptions.py`) are fully strict — the carve-out does not apply to them. | Pyright gives `pyrightconfig.json` precedence and silently ignores `[tool.pyright]` when both exist. The JSON file is already correct. Adding a TOML block would be a no-op that would confuse future agents. |
| D2 | **Pyright CLI arg: `uv run pyright` (no flags).** Pyright picks up `pyrightconfig.json` automatically. The `--strict` CLI flag was removed in pyright 1.1.408; all mode control is via config. | Removes all stale flag references. Plan 064's disabled CI step is superseded here. |
| D3 | **Error-count ratchet via an explicit baseline file at `tools/pyright_baseline.json`.** CI fails if any error count (per file or total) rises above the baseline. Updated only by a deliberate commit. | Classic shrink-only ratchet. File-granular baseline means one file getting worse is caught even if overall total drops. A single JSON file keeps git diffs readable. |
| D4 | **Fix the 166 concrete violations inside `flows/` (Phase 2) before the Unknown cluster (Phase 3).** Concrete = potential real bug. Unknown = mechanical / stylistic noise from Prefect type erasure. | Parallel to Plan 073 (which fixes 64 concrete violations outside `flows/`). Together, Phase 2 + Plan 073 clear all concrete errors before the Unknown drain starts. |
| D5 | **Drain the Unknown cluster (non-flows) file-by-file, largest-first.** Each file's drain lands as a single commit with an updated ratchet. | Keeps diffs reviewable. File-first ordering means early commits reduce the ratchet meaningfully. |
| D6 | **No `# pyright: ignore` without a dated comment and a rule name.** Format: `# pyright: ignore[<rule>]  # <reason>; re-review YYYY-MM-DD`. | Mirrors the `.trivyignore` convention from Plan 064 A2. Makes sprawl auditable. |
| D7 | **The `lint` CI job re-enables `uv run pyright` in Phase 1.** From that point on, CI gates on pyright. Baseline is updated only by deliberate PR. | The gate is the whole point. Without it, progress erodes. |

---

## Task list

### Phase 1 — Verify config + land the ratchet

#### T1 — Verify and document the existing `pyrightconfig.json`

1. Run `uv run pyright` (no flags). Confirm it reads `pyrightconfig.json`
   from the repo root (pyright prints which config it finds).
   - If Plan 073 has landed: expect ~611 errors.
   - If Plan 073 has not yet landed: expect ~675 errors.
   Either is acceptable; T2 captures the live number.
2. Document the carve-out rationale in `docs/standards/pyright.md` (new
   file, short — under 40 lines). Cover: `typeCheckingMode: "strict"`
   global; `executionEnvironments` carve-out for `src/sapphire_flow/flows/`
   silences six Unknown-cluster rules because Prefect `@flow`/`@task`
   decorators erase types and pandas/xarray propagation adds noise that
   isn't real bugs; link back to this plan's §Context. Do NOT add an
   inline `_comment` field to `pyrightconfig.json` — pyright 1.1.408
   validates the JSON against its schema and emits
   `Config contains unrecognized setting "_comment"` on stderr for any
   unknown top-level key. JSON does not support comments; the standards
   doc is the documentation surface.
3. Do NOT add `[tool.pyright]` to `pyproject.toml` — it would be silently
   ignored (see D1, D2).

**Exit**: `uv run pyright` runs without flags, reads `pyrightconfig.json`,
produces either ~611 or ~675 errors depending on whether Plan 073 has
landed; `docs/standards/pyright.md` exists and explains the carve-out
rationale; no stderr warnings from pyright about unknown config keys.

#### T1b — Fix stale `--strict` references across docs

**Files**: `docs/workflow.md` (primary), plus every other docs/ file that
still references the removed flag.

1. Replace `uv run pyright --strict src/` → `uv run pyright src/`
   (no flags; config in `pyrightconfig.json` controls mode) in every
   location returned by `grep -rn "pyright --strict" docs/`.
2. As of 2026-04-22 the known stale locations are (verify live at
   execution time — other PRs may land new references before T1b runs):
   - `docs/workflow.md:102` — Task Exit Gate section (primary target).
   - `docs/v0-scope.md:404`
   - `docs/architecture-context.md:3041`
   - `docs/plans/066-train-models-retrain-strategy.md:94`
   - `docs/plans/067-meteoswiss-stac-adapter-investigation.md:144`
   - `docs/plans/068-onboard-stations-parallelization.md:106`
3. Archived plans (under `docs/plans/archive/`) are historical artefacts —
   leave them alone even if they contain the stale flag.
4. After edits, `grep -rn "pyright --strict" docs/` should return no
   non-archive hits.

**Exit**: `grep -rn "pyright --strict" docs/ | grep -v docs/plans/archive/`
returns nothing; every remaining reference to pyright invokes it as
`uv run pyright src/` (or `uv run pyright --outputjson src/` for the
ratchet capture).

#### T2 — Capture the baseline

**File**: `tools/pyright_baseline.json` (new)

This is a reusable CI utility (not a one-time script), so it is written as a
`.py` file per CLAUDE.md's "reusable logic → proper Python scripts" rule.

1. Write `tools/pyright_baseline.py` — a script that runs
   `uv run pyright --outputjson src/` and reduces the output to:
   ```json
   {
     "total": 611,
     "by_file": {
       "src/sapphire_flow/services/onboarding.py": 121,
       ...
     }
   }
   ```
   Include only files with ≥1 error. The `total` is the live measured count.
2. Run the script to produce `tools/pyright_baseline.json`.
   The total will be ~611 (if Plan 073 has landed) or ~675 (if not).
   The agent running T2 must measure the live count at execution time
   and commit that number — do not hard-code 611.
3. Commit both files.
4. Wire the script as a helper: `uv run python tools/pyright_baseline.py`
   regenerates the baseline when a deliberate ratchet update is needed.

**Exit**: `tools/pyright_baseline.json` exists; `total` matches
`uv run pyright --outputjson src/` live count; `by_file` has an entry
for every file with ≥1 error.

#### T3 — CI ratchet check

**File**: `.github/workflows/ci.yml`

1. Uncomment the pyright step (Plan 064 left it as
   `# - run: uv run pyright src/`). Replace with:
   ```yaml
   - run: uv run pyright --outputjson src/ > pyright.json || true
   - run: uv run python tools/pyright_ratchet.py pyright.json tools/pyright_baseline.json
   ```
2. Write `tools/pyright_ratchet.py`: load both JSON, compare
   per-file and total, fail with exit code 1 if ANY count is higher
   than baseline. Print the diff in a human-readable table.
3. The `--outputjson ... || true` ensures the step doesn't fail on
   pyright's nonzero exit; the ratchet script is the real gate.

**Exit**: CI lint job runs pyright + ratchet. Baseline equals live
output → CI passes. Any file with more errors than baseline → CI
fails with a clear "file X was at N, now at M" message. The baseline
correctness is T2's responsibility; T3 trusts the committed baseline.

### Phase 2 — Fix the 166 concrete violations inside `flows/`

**Scope note**: Concrete violations outside `flows/` are owned by
Plan 073 (see `docs/plans/073-concrete-type-violations-cleanup.md`).
Plan 069 Phase 2 does NOT touch non-flows files.

#### T4 — `reportArgumentType` sweep inside `flows/` (103 errors)

1. List instances: `uv run pyright --outputjson src/ | python3 -c "import json,sys; [print(e['file'],e['message']) for e in json.load(sys.stdin)['generalDiagnostics'] if e.get('rule')=='reportArgumentType' and '/flows/' in e['file']]"`
2. For each callsite: fix callsite (cast/reshape/guard), fix target signature, or flag as latent bug (separate plan/issue).
3. Land as small commits; update `tools/pyright_baseline.json` each commit.

**Exit**: Zero `reportArgumentType` inside `flows/`; latent bugs tracked separately; baseline updated.

#### T5 — `reportAttributeAccessIssue` sweep inside `flows/` (51 errors)

Same pattern as T4 for the 51 attribute-access errors inside `flows/`.

**Exit**: Zero `reportAttributeAccessIssue` inside `flows/`; baseline updated.

#### T6 — Remaining concrete violations inside `flows/` (12 errors)

`reportCallIssue` (6), `reportPrivateUsage` (3), `reportReturnType` (1),
`reportUnnecessaryIsInstance` (1), `reportUnusedFunction` (1). Same pattern.

**Exit**: Zero errors of these rules inside `flows/`; baseline updated.

### Phase 3 — Drain the Unknown cluster (non-flows), largest-first

Phase 3 targets the 445 non-flows Unknown-cluster errors that survive after
Plan 073's concrete-violation fixes. These are `reportUnknownVariableType`,
`reportUnknownMemberType`, `reportUnknownArgumentType`,
`reportUnknownParameterType`, `reportMissingTypeArgument`,
`reportMissingParameterType`, `reportUnknownLambdaType` in services/, store/,
api/, and tools/ files.

#### T7 — `services/onboarding.py` (121 errors)

1. Categorize which upstream types propagate as `Unknown` (pandas DataFrame,
   xarray DataArray, SQLAlchemy result rows, etc.).
2. For each callsite: add explicit local annotation, or (for demonstrable
   upstream stub gaps) `# pyright: ignore[<rule>]` with dated comment per D6.
   Budget: at most 20% ignores per file; more indicates deeper redesign needed.
3. Update `tools/pyright_baseline.json` as the count drops.

**Exit**: File count in `pyright_baseline.json` for `onboarding.py` reduced
by ≥80% OR fully zero; every surviving entry has a dated ignore per D6.

#### T8..T14 — Drain each of the next 9 largest files (non-flows)

Same pattern as T7, for (post-carve-out count order):

| File | Count (pre-073) | Note |
|---|---|---|
| `services/operational_inputs.py` | 79 | All Unknown-cluster |
| `services/model_onboarding.py` | ~32 | Unknown-cluster subset; Plan 073 takes concrete ones |
| `services/hindcast.py` | ~31 | Unknown-cluster subset; Plan 073 takes concrete ones |
| `services/training_data.py` | ~26 | Unknown-cluster subset; Plan 073 takes concrete ones |
| `services/run_station_forecast.py` | 26 | All Unknown-cluster |
| `api/routes/stations.py` | ~17 | Unknown-cluster subset; Plan 073 takes concrete ones (api/routes/*) |
| `store/hindcast_store.py` | 18 | All Unknown-cluster |
| `store/forecast_store.py` | ~14 | Unknown-cluster subset; Plan 073 takes concrete ones |
| `services/forecast_qc.py` | ~7 | Unknown-cluster subset; Plan 073 takes concrete ones |

Each task = one file, one commit, one ratchet update; ≥80% reduction exit gate.
Agents must measure live counts at execution time for files shared with Plan 073.

### Phase 4 — Tail

#### T15 — Sweep remaining files

The remaining errors across files not covered by T7–T14 (roughly 20 files,
~100 errors). Same pattern, but batch files by "similar fix shape" where
possible (e.g., all `store/*` files sharing a DataFrame-shape issue).

**Exit**: Total pyright error count ≤ 100. All surviving entries have
dated ignore comments per D6.

#### T16 — Flip the ratchet to "zero tolerance"

Once the tail is drained:

1. Remove `tools/pyright_baseline.json`.
2. Replace the CI ratchet step with a plain `uv run pyright src/`.
3. Any new pyright error fails CI immediately.

**Exit**: `pyright_baseline.json` deleted; `ci.yml` runs pyright without
the ratchet script; CI is zero-tolerance for new errors.

---

## Priority order

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 1 | T1 + T1b + T2 + T3 (Phase 1) | High (enabler) | Low (~1 day) | Nothing in Phase 2 or 3 matters until the ratchet is live. T1b fixes the stale workflow.md flag. Unblocks everything. |
| 2 | T4 + T5 + T6 (flows/ concrete violations) | High (latent-bug value) | Medium | 166 errors, each potentially a real bug in Prefect flow wiring. Parallel to Plan 073 which covers non-flows. |
| 3 | T7..T14 (top non-flows files, Unknown cluster) | Medium (mechanical hygiene) | Medium-high per file | 445 errors but concentrated in 10 files. Linear progress, ratchet makes it safe to spread over weeks. |
| 4 | T15 + T16 (tail + flip) | Medium | Low | Close out. Only meaningful once Phase 3 has drained the concentration. |

Phase 1 is a hard prerequisite for everything else. Within Phase 2,
T4/T5/T6 are independent and can run in parallel. Phase 3 tasks are
independent per file but each updates the same `pyright_baseline.json`
so they should serialize by commit (can run as parallel branches,
merge serially).

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-ratchet",
      "tasks": ["T1", "T1b", "T2", "T3"],
      "parallel": false,
      "depends_on": []
    },
    {
      "id": "phase-2-concrete-flows",
      "tasks": ["T4", "T5", "T6"],
      "parallel": true,
      "depends_on": ["phase-1-ratchet"]
    },
    {
      "id": "phase-3-drain",
      "tasks": ["T7", "T8", "T9", "T10", "T11", "T12", "T13", "T14"],
      "parallel": true,
      "depends_on": ["phase-2-concrete-flows"]
    },
    {
      "id": "phase-4-tail",
      "tasks": ["T15", "T16"],
      "parallel": false,
      "depends_on": ["phase-3-drain"]
    }
  ]
}
```

Phase-3 tasks CAN run in parallel (one branch per file) but all merge
into the same `tools/pyright_baseline.json` so they serialize on merge.

---

## Open questions for user review

1. **Strict-on-core scope** — `types/`, `protocols/`, `exceptions.py` are already
   fully strict (the carve-out applies only to `flows/`). Should `config/` also
   be guarded against future carve-out proposals? Recommendation: no action now.
2. **Ratchet tolerance for upstream stub changes** — if Prefect/pandas adds
   50 errors repo-wide, CI fails until we fix them. No escape hatch: either
   pin the old version or land a coordinated ratchet bump in one commit.
3. **Scope of T4's "latent bug" escalation** — if T4 finds 10 latent
   bugs in `flows/`, do we stop T4 to fix them, or log and keep draining?
   Recommendation: log each as an issue / follow-up plan, keep T4
   focused on pyright-cleanup; fix the bugs in separate commits with
   their own justification.
4. **Phase 2 task ordering** — T4 (103 errors) is substantially larger
   than T5 (51) and T6 (12). Should T4 be split by flow file for more
   reviewable diffs? Recommendation: split if any single-file commit
   would exceed ~50 changes; otherwise keep per-rule batching.

## Changelog

- **2026-04-22** — Initial DRAFT. Motivated by the Plan 064 discovery
  that `pyright --strict` had never actually passed in CI. Backlog
  size measured at 1078 errors across 20+ files, with 53% concentrated
  in the top 5 Prefect-flow / service modules. Four-phase plan:
  configure + ratchet (Phase 1) → concrete violations (Phase 2) →
  file-by-file drain of the Unknown cluster (Phase 3) → flip to
  zero-tolerance (Phase 4).

- **2026-04-22 (rewrite)** — Rescoped based on 2026-04-22 pyright
  configuration experiment (1078 → 675 via flows/ carve-out) and sibling
  Plan 073 DRAFT (concrete violations outside flows/). Key changes: T1
  replaced with verify+document task (no config rewrite, per P1 decision);
  Phase 2 scoped to flows/-only (per P2 decision); added T1b for
  workflow.md stale-flag fix (per P6 decision); baseline numbers corrected
  to 675 pre-073 / ~611 post-073; added Cross-plan coordination section
  with binding merge order (per P3 decision).
