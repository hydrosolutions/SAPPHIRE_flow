# Plan 069 — Pyright backlog cleanup: ratchet + drain

**Status**: READY
**Date**: 2026-04-22 (DRAFT) → 2026-05-11 (READY, post four review rounds)
**Depends on**: Plan 073 (concrete violations outside `flows/`) → Plan 069 Phase 1 →
Phase 2+. See §Cross-plan coordination for the full merge order.
**Scope**: Re-enable pyright type-checking as a CI merge gate by
(a) verifying and documenting the existing `pyrightconfig.json` (already strict
globally, with a `flows/` carve-out for Prefect decorator type erasure),
(b) fixing the stale `--strict` CLI reference in `docs/workflow.md`,
(c) capturing the post-Plan-073 error count (≤609) as a ratchet baseline, and
(d) draining the remaining backlog under that ratchet. No runtime behaviour
change.

---

## Cross-plan coordination

This plan is one of three DRAFTs addressing pyright / type-checking
hygiene after Plan 064. The three are:

- **Plan 070** (pre-commit hooks + gate parity) — prevents new
  lint/format/secret regressions during the drain.
- **Plan 073** (concrete type-violations cleanup outside flows/) —
  fixes 65+ real-bug-rule violations before the ratchet captures a
  baseline.
- **Plan 069** (this plan — pyright backlog ratchet + drain) —
  freezes the post-073 baseline and drains remaining errors under
  the ratchet.

**Merge order (mandatory):** 070 → 073 → 069 Phase 1 → 069 Phase 2+.
Rationale: 070 stops new regressions from leaking in; 073 fixes real
bugs so the ratchet floor is clean; 069 Phase 1 captures the
post-Plan-073 total (≤609) as the ratchet floor; 069 Phase 2+ drains
what remains.

**Baseline numbers:**
- 1078 = pre-experiment (no carve-out). Historical reference only.
- 676 = live baseline at 2026-05-11, pre-Plan-073 (flows/ carve-out active).
- ≤609 = post-Plan-073 (this plan's ratchet floor).

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

Separately, investigation of the remaining 676 errors identified 65+
concrete violations outside `flows/` (Plan 073 scope) and 166
concrete violations inside `flows/` (Plan 069 Phase 2 scope). After
Plan 073 lands, the ratchet floor is ≤609.

The `lint` CI job currently runs only ruff + ruff format. Static
type-checking is our largest protection against refactor regressions
in a codebase heavy on generics (`Protocol` signatures, `NewType`,
frozen dataclasses) per CLAUDE.md's type-driven-development rules.
Leaving it off indefinitely erodes that investment.

### The shape of the backlog (measured 2026-04-22, post-carve-out)

**Total: 676 errors pre-Plan-073 at 2026-05-11 (expected ≤609 post-Plan-073)**

| Count | Pyright rule | Category |
|---|---|---|
| 146 | `reportUnknownVariableType` | "Unknown" cluster (carve-out silences this inside `flows/`) |
| 107 | `reportUnknownMemberType` | " |
| 102 | `reportUnknownArgumentType` | " |
| 37  | `reportUnknownParameterType` | " |
| 33  | `reportMissingTypeArgument` | " |
| 19  | `reportMissingParameterType` | Our own missing annotations — NOT silenced by the carve-out; fix in-place (Phase 3 only) |
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
Baseline captured to `/tmp/pyright_rewrite.json` on 2026-04-22 (historical 2026-04-22 snapshot — generate fresh at execution time via `uv run pyright --outputjson src/`).

### Principle

Ratchet, don't big-bang. We cannot pause feature work for weeks to
drain ≤609 errors, but we also cannot keep letting the count grow while
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

**Sprint 2 preconditions** (verify before starting implementation):
- **Plan 070** (pre-commit hooks + CI/local gate parity) — **DONE** as
  of 2026-05-11 (commit `b94d393`, Phase 4 landed). Pre-commit hooks
  are installed; `uv run check` developer-side helper exists;
  `tools/gate_parity_check.py` is in place.
- **Plan 073** (concrete pyright violations outside `flows/`) —
  **READY** as of 2026-05-11 (commit `5d601fc`, six review rounds).
  Must land BEFORE Plan 069 Phase 2 starts so the ratchet baseline
  is captured at the post-073 floor (≤609).

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
- Generated fresh at execution time via `uv run pyright --outputjson src/`; the 2026-04-22 experiment snapshots are historical context only and not used by the implementation.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **`pyrightconfig.json` is already correct and authoritative. Do NOT migrate to `[tool.pyright]` in `pyproject.toml`.** The existing config is `typeCheckingMode: "strict"` globally with an `executionEnvironments` carve-out for `flows/` that silences the six Unknown-cluster rules. Core paths (`types/`, `protocols/`, `exceptions.py`) are fully strict — the carve-out does not apply to them. | Pyright gives `pyrightconfig.json` precedence and silently ignores `[tool.pyright]` when both exist. The JSON file is already correct. Adding a TOML block would be a no-op that would confuse future agents. |
| D2 | **Pyright CLI arg: `uv run pyright` (no flags).** Pyright picks up `pyrightconfig.json` automatically. The `--strict` CLI flag was removed in pyright 1.1.408; all mode control is via config. | Removes all stale flag references. Plan 064's disabled CI step is superseded here. |
| D3 | **Error-count ratchet via an explicit baseline file at `tools/pyright_baseline.json`.** CI fails if any error count (per file or total) rises above the baseline. Updated only by a deliberate commit. | Classic shrink-only ratchet. File-granular baseline means one file getting worse is caught even if overall total drops. A single JSON file keeps git diffs readable. |
| D4 | **Fix the 166 concrete violations inside `flows/` (Phase 2) before the Unknown cluster (Phase 3).** Concrete = potential real bug. Unknown = mechanical / stylistic noise from Prefect type erasure. | Parallel to Plan 073 (which fixes 65+ concrete violations outside `flows/`). Together, Phase 2 + Plan 073 clear all concrete errors before the Unknown drain starts. |
| D5 | **Drain the Unknown cluster (non-flows) file-by-file, largest-first.** Each file's drain lands as a single commit with an updated ratchet. | Keeps diffs reviewable. File-first ordering means early commits reduce the ratchet meaningfully. |
| D6 | **No `# pyright: ignore` without a dated comment and a rule name.** Format: `# pyright: ignore[<rule>]  # <reason>; re-review YYYY-MM-DD`. | Mirrors the `.trivyignore` convention from Plan 064 A2. Makes sprawl auditable. |
| D7 | **The `lint` CI job re-enables `uv run pyright` in Phase 1.** From that point on, CI gates on pyright. Baseline is updated only by deliberate PR. | The gate is the whole point. Without it, progress erodes. |

### Maintenance: handling upstream stub changes

The per-file ratchet's strictness has a known maintenance burden:
upstream stub changes (Prefect, pandas, xarray version bumps) can
introduce new pyright errors in many files at once. When this happens,
CI fails until the baseline is bumped. **Procedure**:

1. Identify the upstream change driving the new errors (commit message
   on the deps PR + diff of CI failure rows).
2. Run `uv run python tools/pyright_baseline.py` locally to regenerate
   the baseline with the new floor.
3. Commit the updated baseline alongside the deps bump (single commit,
   not two separate ones — keep them atomic).
4. Note the baseline bump in the commit message: "baseline bumped
   from N to M due to <upstream stub change>".

If the new errors are real bugs the upstream surfaced (not just stub
noise), fix them in the same or follow-up commit. The ratchet's job
is to make new errors visible; the response shape is human judgment.

### Cascade resolution

A fix in one file (e.g. tightening a return type in
`services/onboarding.py`) may cascade-resolve errors in downstream
files (e.g. `services/operational_inputs.py`). The baseline should be
updated atomically to reflect ALL affected files in a single commit.
Procedure: after a fix, run `uv run python tools/pyright_baseline.py`
to regenerate the baseline with the new floors for every file (not
just the file you edited). Commit the multi-file baseline diff
alongside the fix.

### Existing `# pyright: ignore` comments

The codebase has ~11 undated `# pyright: ignore` comments predating
D6's dated convention (e.g. in `config/_overlay.py`,
`store/zarr_nwp_grid_store.py`). D6 is forward-looking — new ignores
must be dated. **Existing ignores are out of scope for this plan**;
they will be retroactively dated as part of the files' natural drain
in Phase 3 / T15. The "20% ignores per file" budget (D6/T7) applies
to NEW ignores only; existing undated ones are not counted against the
budget but should be either removed or re-dated when the implementer
touches the file.

---

## Task list

### Phase 1 — Verify config + land the ratchet

#### T1 — Verify and document the existing `pyrightconfig.json`

1. Run `uv run pyright` (no flags). Confirm it reads `pyrightconfig.json`
   from the repo root (pyright prints which config it finds).
   - If Plan 073 has landed: expect ≤609 errors.
   - If Plan 073 has not yet landed: expect ~676 errors.
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

   Use this content template (under 40 lines; trim or extend as needed,
   but keep the five sections):

   ````markdown
   # Pyright type-checking — configuration policy

   ## What's configured

   `pyrightconfig.json` at the repo root is authoritative. `typeCheckingMode`
   is `"strict"` globally. An `executionEnvironments` carve-out silences
   the six Unknown-cluster rules (`reportUnknownVariableType`,
   `reportUnknownMemberType`, `reportUnknownArgumentType`,
   `reportUnknownParameterType`, `reportMissingTypeArgument`,
   `reportUnknownLambdaType`) inside `src/sapphire_flow/flows/`.

   ## Why the flows/ carve-out

   Prefect `@flow`/`@task` decorators erase types in ways pyright cannot
   follow. Combined with pandas/xarray/numpy stub propagation noise, this
   produces ~400 spurious errors that do not indicate real bugs. The
   carve-out silences only the Unknown cluster — real-bug rules like
   `reportArgumentType`, `reportAttributeAccessIssue`, `reportCallIssue`
   still fire at full strength inside `flows/`. See Plan 069 §Context for
   the empirical justification (1078 → 675 errors when the carve-out
   landed; the 403-error reduction was all Unknown noise).

   ## Ratchet

   CI runs `uv run pyright --outputjson src/` and pipes the result through
   `tools/pyright_ratchet.py`, which compares per-file and total counts
   against `tools/pyright_baseline.json`. A PR that raises any count above
   the baseline fails CI. The baseline is shrink-only and updated by
   deliberate commits.

   ## How to update the baseline

   After fixing pyright errors, run:

   ```bash
   uv run python tools/pyright_baseline.py
   ```

   This regenerates `tools/pyright_baseline.json` with the live count.
   Commit the updated baseline alongside the fix.

   ## References

   - `docs/plans/069-pyright-backlog-cleanup.md` — the plan that
     established this configuration and ratchet.
   - `pyrightconfig.json` — the authoritative config.
   - `tools/pyright_ratchet.py` — the CI gating script.
   ````

3. Do NOT add `[tool.pyright]` to `pyproject.toml` — it would be silently
   ignored (see D1, D2).

**Exit**: `uv run pyright` runs without flags, reads `pyrightconfig.json`,
produces approximately 676 errors pre-Plan-073 or ≤609 errors post-Plan-073
(Plan 073 is READY as of 2026-05-11). Acceptable tolerance: ±5 errors due to
pyright measurement variance. Live count must be recorded in the implementation
commit message body per **Plan 073 §D9 convention** (Plan 069's own
architecture-decisions table is D1–D7; D9 is a cross-plan reference
to Plan 073).
`docs/standards/pyright.md` exists and explains the carve-out
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
   - `docs/plans/068-onboard-stations-parallelization.md:106`
   Plan 067 was archived 2026-05-11 (commit `0a4819e`, tag `v0.1.432`);
   its `pyright --strict` references survive in the archived copy and are
   excluded by the `-v docs/plans/archive/` filter.
3. Archived plans (under `docs/plans/archive/`) are historical artefacts —
   leave them alone even if they contain the stale flag.
4. After edits, `grep -rn "pyright --strict" docs/` should return no
   non-archive hits.

**Exit**:
```
grep -rn "pyright --strict" docs/ \
  | grep -v docs/plans/archive/ \
  | grep -v docs/plans/069-pyright-backlog-cleanup.md
```
returns nothing; every remaining reference to pyright invokes it as
`uv run pyright src/` (or `uv run pyright --outputjson src/` for the
ratchet capture).

**Note**: The plan file `docs/plans/069-pyright-backlog-cleanup.md`
itself contains multiple `pyright --strict` references as explanatory
prose (documenting what's being changed). These are intentional and
excluded from the cleanup by the additional `-v` filter.

#### T2 — Capture the baseline

**File**: `tools/pyright_baseline.json` (new)

This is a reusable CI utility (not a one-time script), so it is written as a
`.py` file per CLAUDE.md's "reusable logic → proper Python scripts" rule.

1. Write `tools/pyright_baseline.py` — a script that runs
   `uv run pyright --outputjson src/` and reduces the output to:
   ```json
   {
     "total": 609,
     "by_file": {
       "src/sapphire_flow/services/onboarding.py": 121,
       ...
     }
   }
   ```
   (`"total"` is the live measured count at execution time; ≤609 is the
   post-Plan-073 expected value. Do not hard-code this number — measure live.)
   Include only files with ≥1 error.

   **Subprocess handling**: pyright exits non-zero whenever it finds any
   errors (which is always the case during baseline capture — that's why
   we have a baseline). Use `subprocess.run([...], check=False, capture_output=True, text=True)`
   to capture both the exit code and stdout. **Do NOT** use `check=True`
   — the script would fail before writing the baseline JSON. Use the
   returncode only to distinguish pyright crashes (e.g. exit codes 2 or
   higher, or malformed JSON) from the normal "errors found" exit 1.
   Reduce the JSON output to the `{"total": N, "by_file": {...}}` shape
   documented in T2 step 1.

2. Run the script to produce `tools/pyright_baseline.json`.
   The total will be ≤609 (if Plan 073 has landed) or ~676 (if not).
   The agent running T2 must measure the live count at execution time
   and commit that number — do not hard-code any specific value.
3. Commit both files.
4. Wire the script as a helper: `uv run python tools/pyright_baseline.py`
   regenerates the baseline when a deliberate ratchet update is needed.

**Exit**: `tools/pyright_baseline.json` exists; `total` matches
`uv run pyright --outputjson src/` live count; `by_file` has an entry
for every file with ≥1 error.

#### T3 — CI ratchet check

**File**: `.github/workflows/ci.yml`

1. The commented-out pyright step in `.github/workflows/ci.yml`
   spans **approximately 7 lines** (a multi-line comment block, not a
   single line). Verify by reading `.github/workflows/ci.yml` and
   locating the 7-line comment block ending with the line
   `# - run: uv run pyright src/` in the lint job (earlier lines in the
   block explain why pyright is disabled and reference this plan as
   the follow-up). Replace the entire comment block with **two new
   `run` steps** at the same indentation level as the surrounding
   lint-job steps:
   ```yaml
   - run: uv run pyright --outputjson src/ > /tmp/pyright.json || true
   - run: uv run python tools/pyright_ratchet.py /tmp/pyright.json tools/pyright_baseline.json
   ```
   **YAML indentation matters**: the `- run:` prefix must align with
   the other steps in the `lint` job's `steps:` list. Wrong indentation
   silently re-attributes the step to the wrong job or breaks the
   YAML. After the edit, run `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` to confirm valid YAML.
2. The temp file path `/tmp/pyright.json` matches Plan 070 A4's
   pre-commit hook convention (also writes to `/tmp/pyright.json`).
   Using `/tmp/` avoids creating an untracked `pyright.json` in the
   repo root that an implementer might accidentally commit.

   **Also update `docs/standards/cicd.md`**: Plan 070 C1 extended the
   CI workflow tiers table; T3 adds two new pyright steps to the `lint`
   tier. Add two new rows to the table for these steps with
   `Local equivalent: uv run pyright src/` and `CI only? No`.

3. Write `tools/pyright_ratchet.py`: load both JSON, compare
   per-file and total, fail with exit code 1 if ANY count is higher
   than baseline. Print the diff in a human-readable table.
4. The `--outputjson ... || true` ensures the step doesn't fail on
   pyright's nonzero exit; the ratchet script is the real gate.

**Edge cases `tools/pyright_ratchet.py` must handle**:

1. **New file in live JSON not in baseline**: treat as
   `baseline[new_file] = 0`. Any error in a previously-unseen file
   fails the ratchet (correct behaviour — new files should start clean).
2. **File in baseline with 0 errors in live JSON**: silent pass
   (the file is either fixed or deleted; both are improvements;
   don't require an explicit "remove from baseline" step until the
   implementer regenerates the baseline).
3. **Live count is lower than baseline for a file**: silent pass.
   Update the baseline manually via `uv run python tools/pyright_baseline.py`
   when ready to lock in the improvement.
4. **Pyright subprocess returns non-zero exit code**: pyright exits
   non-zero whenever it finds errors. Use `subprocess.run(..., check=False)`
   to capture both the exit code and the JSON output. Only fail the
   ratchet on a malformed JSON OR a higher count, NOT on pyright's
   non-zero exit. Plan 070 A4's bash entry uses `|| true` for the same
   reason.
5. **Malformed pyright JSON output**: exit code 2 (distinguish from
   exit code 1 = ratchet violation). Print the malformed first 200
   chars to stderr for debugging.
6. **Empty pyright JSON output** (no errors at all): treat as
   `total = 0` and all files at 0. The ratchet passes if baseline
   also has total = 0 (which would only happen post-T15b).

**Why `/tmp/pyright.json` and not a repo-root path**: keeps the
ratchet's working file outside the git tree; no `.gitignore` entry
needed; matches Plan 070 A4's pre-commit hook convention.

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

#### T8..T14 — Drain each of the next 7 largest dedicated-task files (non-flows)

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

Each task = one file, one commit, one ratchet update; ≥80% reduction exit gate.
Agents must measure live counts at execution time for files shared with Plan 073.

**Excluded from dedicated T8–T14 drain tasks** (handled in T15 catch-all):
- `store/forecast_store.py` (~14 errors): shared with Plan 073's
  concrete-violation scope (T12). The Unknown-cluster residual after
  Plan 073 lands is small; folding into T15 avoids double-touching the
  file across two phases.
- `services/forecast_qc.py` (~7 errors): same reasoning — Plan 073's
  T4, T5, T13.5a all touch this file. The remaining Unknown-cluster
  residual is small and drained in T15.

### Phase 4 — Tail

#### T15 — Sweep remaining files

The remaining errors across files not covered by T7–T14 (roughly 20 files,
~100 errors). Same pattern, but batch files by "similar fix shape" where
possible (e.g., all `store/*` files sharing a DataFrame-shape issue).

**Guard**: Do NOT modify any file listed in Plan 073's §Cross-plan
coordination section. The full list of Plan 073-owned files is:
`services/model_onboarding.py`, `services/forecast_qc.py`,
`services/hindcast.py`, `services/alert_checker.py`,
`store/observation_store.py`, `store/forecast_store.py`,
`tools/record_fixtures.py`, `tools/observation_coverage_summary.py`,
`api/routes/tables.py`, `api/routes/dashboard.py`,
`api/routes/forecasts.py`, `api/routes/models.py`,
`api/routes/stations.py`, `api/routes/api_stations.py`,
`services/baselines.py`, `services/qc.py`,
`services/training_data.py`, `api/__init__.py`,
`config/forecast_qc_rules.py`, `config/qc_rules.py`,
`adapters/meteoswiss_nwp.py`, `types/domain.py`.
If T15 sweeps one of these files and finds residual Unknown-cluster errors
after Plan 073 has landed, proceed — but do not re-fix sites that Plan 073
already addressed.

**Exit**: Total pyright error count ≤ 100. All surviving entries have
dated ignore comments per D6.

#### T15b — Drain residual errors to zero

After T15 brings the total pyright error count to ≤100 across the
remaining ~20 tail files, T15b drains the residual to **exactly zero**.
This is a focused sweep targeting whatever errors remain after T15's
batched fixes.

1. Run `uv run pyright --outputjson src/` and capture the output to
   `tools/pyright_baseline.json` (regenerate via
   `uv run python tools/pyright_baseline.py`).
2. For each remaining error, either:
   - Fix it with an annotation, narrowing, or refactor (preferred), or
   - Add a `# pyright: ignore[<rule>]` with the D6 dated-comment format
     and a re-review date 6 months out. Budget: at most 5 ignores total
     across the residual (the ratchet is designed to surface real bugs,
     not absorb mass ignores).
3. After each fix, re-run `uv run pyright src/` and confirm the count
   strictly decreases. Update `tools/pyright_baseline.json` after each
   commit to reflect the new floor.

**Exit gate**: `uv run pyright src/` reports **exactly 0 errors**
across all included paths. `tools/pyright_baseline.json` total is 0.
At this point T16 (flip to zero-tolerance) is safe to execute.

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
| 4 | T15 + T15b + T16 (tail + drain to zero + flip) | Medium | Low | Close out. T15b drains residual ≤100 errors to exactly zero before T16 flips to zero-tolerance. Only meaningful once Phase 3 has drained the concentration. |

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
      "tasks": ["T15", "T15b", "T16"],
      "parallel": false,
      "depends_on": ["phase-3-drain"],
      "task_deps": {
        "T15b": {"depends_on": ["T15"]},
        "T16": {"depends_on": ["T15b"]}
      }
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
2. **Ratchet tolerance for upstream stub changes** — **Resolved** by the
   §Maintenance subsection added in the 2026-05-11 corrections pass.
   Procedure: regenerate the baseline via
   `uv run python tools/pyright_baseline.py` and commit atomically with
   the upstream version bump.
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
  replaced with verify+document task (no config rewrite, per D1);
  Phase 2 scoped to flows/-only (per D4); added T1b for
  workflow.md stale-flag fix (per T1b — workflow.md stale-flag fix); baseline numbers corrected
  to 675 pre-073 / ~611 post-073; added Cross-plan coordination section
  with binding merge order (per the §Cross-plan coordination merge order).

- **2026-05-11 (DRAFT — corrections pass)** — Three parallel Sonnet
  4.6 reviewers (architecture / risk-correctness / implementation-
  feasibility) found ~25 issues. Corrections applied this pass:
  (1) Baseline-number sweep — replaced stale `~611` references with
  `≤609` in 6+ prose locations; `675` → `676`; `64` → `65+`. T2's
  example JSON value updated to `≤609` with explicit "measure live
  at execution time" note. (2) §Backlog table `reportMissingParameterType`
  annotation corrected from `T6/Phase 3` to `Phase 3 only`. (3) T8–T14
  table shrunk from 9 to 7 files; `store/forecast_store.py` and
  `services/forecast_qc.py` demoted to T15 with explicit shared-scope
  rationale. (4) New **T15b ("drain to zero")** task inserted between
  T15 and T16 to prevent T16 from firing on a non-zero error state.
  (5) T1b grep exit gate now also excludes the plan file itself
  (`docs/plans/069-pyright-backlog-cleanup.md`); archived Plan 067
  path removed from the "known stale locations" list. (6) T1 exit
  gate tolerance specified (±5 errors); pyright.md content template
  provided in full. (7) T2 + T3 edge cases for the ratchet script
  documented (new file, file fixed to 0, pyright non-zero exit,
  malformed JSON, empty JSON). (8) T3 ci.yml edit reframed as a 7-line
  comment-block replacement with YAML-indentation guidance and a
  `python3 -c yaml.safe_load` verification step; tempfile path
  switched to `/tmp/pyright.json` matching Plan 070 A4's convention.
  (9) `/tmp/pyright_*.json` historical references in §Context / §Inputs
  reframed as "historical context only, generate fresh at execution
  time". (10) New §Maintenance subsection documenting the per-file
  ratchet baseline-bump procedure for upstream stub changes; new
  §Cascade resolution paragraph; new §Existing ignores note explaining
  the forward-looking scope of D6.

  Plan ready for orchestrator final review.

- **2026-05-11 (READY — post four review rounds)** — Status flipped
  DRAFT → READY after four review rounds with a diminishing find-rate
  (25 → 1 → 2 → 3, all P2/P3 by round 4 — no P1 issues survived past
  round 1). Round 2 surfaced cross-plan baseline stragglers in
  Plans 070 and 073; those were patched in lockstep with this plan's
  promotion commit (Plan 070 §Cross-plan baseline numbers updated;
  Plan 073 §Non-goals 64→65+). Round 4 final touch-ups: D9 reference
  qualified as cross-plan to Plan 073 (Plan 069's own D-table goes
  D1–D7); §Inputs Sprint 2 preconditions block added documenting
  Plan 070 DONE and Plan 073 READY status; Open Question 2 marked
  resolved (the §Maintenance subsection added in the corrections
  pass fully answers it).

  Sprint 2 status as of this commit:
  - Plan 070: DONE (Phases 1–4 implemented today; A4 deferred to this
    plan's Phase 1 landing).
  - Plan 073: READY (six review rounds; not yet implemented).
  - Plan 069: READY (this plan; not yet implemented).

  Implementation gated on a separate orchestrator go-ahead. Sprint 2
  merge order: 070 (DONE) → 073 → 069 Phase 1 → 069 Phase 2+.
