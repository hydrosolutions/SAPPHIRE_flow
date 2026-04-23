# Plan 070 — Pre-commit hooks + CI/local gate parity

**Status**: DRAFT
**Date**: 2026-04-22
**Depends on**: none (independent of Plan 069 but intentionally lands first:
stops new lint/format/secret regressions from leaking in while Plan 069
drains the pyright backlog under a ratchet).
**Scope**: Close the "safety net wired but not actually running" gap that
Plan 064 surfaced. Install `pre-commit` (the tool) with ruff, yamllint, and
gitleaks hooks as the developer-tier gate; add a `uv run check` helper that
is the single source of truth for "what CI will check"; audit every CI gate
for local reproducibility and every scheduled workflow for first-fire
confirmation. Pyright joins the pre-commit set only after Plan 069 Phase 1's
ratchet exists (task A4, deferred). No runtime behaviour change.

---

## Cross-plan coordination

This plan is one of three DRAFTs addressing pyright / type-checking
hygiene after Plan 064. The three are:

- **Plan 070** (this plan — pre-commit hooks + gate parity) —
  prevents new lint/format/secret regressions during the drain.
- **Plan 073** (concrete type-violations cleanup outside flows/) —
  fixes 64 real-bug-rule violations before the ratchet captures a
  baseline.
- **Plan 069** (pyright backlog ratchet + drain) — freezes the
  post-073 baseline and drains remaining errors under the ratchet.

**Merge order (mandatory):** 070 → 073 → 069 Phase 1 → 069 Phase 2+.
This plan must land first: it installs the developer-tier gate that
prevents new regressions from leaking in while Plans 073 and 069
drain the existing backlog. Plan 070 must be fully merged before
Plan 069 T3 begins to avoid ci.yml concurrent edits.

**Baseline numbers (for context):**
- 1078 = pre-experiment (no carve-out). Historical reference only.
- 675 = post-experiment, pre-Plan-073 (flows/ carve-out active).
- ~611 = post-Plan-073 (Plan 069's ratchet floor).

**Config location:** `pyrightconfig.json` at repo root is
authoritative. `[tool.pyright]` in `pyproject.toml` is NOT used.
Plan 070's future pre-commit pyright hook (task A4, deferred) must
invoke `uv run pyright src/` (no flags) and rely on JSON config
discovery.

---

## Context

### Why now

Plan 064's first CI run exposed a systemic problem: several safety nets were
**written but not wired**. Specifically:

- `pyright --strict` had been in `ci.yml` for a long time. Nobody had run it
  locally, so nobody noticed the `--strict` CLI flag was removed from
  pyright 1.1.408 and the command would reliably exit with
  "Unexpected option --strict" even if CI had run.
- 19 pre-existing ruff errors lived on HEAD for a long time. Nobody was
  running `ruff check` locally on a pre-commit path.
- Scheduled workflows (`live-lindas-weekly.yml`,
  `integration-nightly.yml`) have never demonstrably fired in this repo's
  history because the remote only came into existence during Plan 064.

The fundamental issue: **the only gate that was ever actually running was
the user's judgment + manual test invocations.** That is not enough for a
Nepal-deployment-grade system.

### Principle

Two gates, both enforced:

1. **Developer-tier gate (pre-commit)** — fast, local, runs before `git
   commit` completes. Prevents lint / format / secret regressions from ever
   reaching a branch. Zero-config for contributors: `uv sync` + `uv run
   pre-commit install` and the hooks fire automatically.
2. **CI gate (GitHub Actions)** — thorough, remote, runs on push + PR.
   Catches what pre-commit misses (integration tests, image builds, Trivy,
   SBOM, wheel-only guard).

The parity invariant: **a developer must never push a commit that CI will
reject for a reason their local environment couldn't have told them about.**

Ratchet the parity, don't big-bang. Start with the fast checks (ruff,
yamllint, gitleaks); add pyright as a pre-commit hook only after Plan 069
Phase 1 lands the ratchet (`tools/pyright_ratchet.py` + `tools/pyright_baseline.json`) that gates on error count rather than pyright's exit code (deferred to A4).

### Non-goals

- **No replacing CI.** Pre-commit is an inner loop, not a replacement.
- **No `pytest` in pre-commit.** Unit tests take ~110s. Too slow for every
  commit. Stays in CI.
- **No `trivy` / `docker build` in pre-commit.** Network + vuln DB + Docker
  daemon dependencies. Stays in CI.
- **No mandatory IDE config.** IDE parity is documented, not enforced.
- **No `--no-verify` workaround policy.** Developers can bypass with
  `--no-verify` in genuine emergencies; CI will still catch it. The ratchet
  (Plan 069) backs this up for pyright specifically.
- **No commitlint / conventional-commit enforcement in pre-commit.** We
  already have a documented convention in CLAUDE.md; adding a hook is
  overhead for modest value. Revisit if commit-message drift becomes real.
- **No secret scanning as the primary defense** against secret leaks —
  GitHub's push-protection (already enabled per Plan 064 non-goals) is the
  hard stop. gitleaks at pre-commit is belt-and-suspenders.

### Inputs

- No `.pre-commit-config.yaml` in the repo today.
- `pyproject.toml` has ruff config; no `pre-commit` dev dep; no
  `[project.scripts]` section.
- `pyrightconfig.json` at repo root — authoritative pyright config.
  Current state: `typeCheckingMode = "strict"` globally, with a
  `flows/` carve-out via `executionEnvironments` that silences the six
  Unknown-cluster rules for `src/sapphire_flow/flows/`. `[tool.pyright]`
  in `pyproject.toml` is NOT present; pyright discovers the JSON file
  automatically. The e2e job is referenced in a comment at the end of
  `ci.yml` but not yet implemented (file ends at line 190).
- `.github/workflows/ci.yml` — 5 jobs: `lint`, `unit`, `wheel-only-guard`,
  `integration`, `build-image-and-scan`. `lint` has pyright commented out
  pending Plan 069.
- `.github/workflows/live-lindas-weekly.yml` — scheduled weekly (Monday
  06:00 UTC). Already has `workflow_dispatch:`. First-fire status unknown.
- `.github/workflows/integration-nightly.yml` — scheduled daily (03:00
  UTC). Already has `workflow_dispatch:`. Renamed during Plan 064; first-fire
  status unknown.
- `docs/standards/cicd.md` — has a "CI workflow tiers" table at lines
  263-280 enumerating 5 jobs with a "Depends on" column. No pre-commit
  guidance.
- `CLAUDE.md` — has `uv` / ruff / type-hints conventions; no pre-commit
  section.
- `src/sapphire_flow/cli/` — exists; contains `__init__.py` and
  `register_deployments.py`. `src/sapphire_flow/tools/` also exists as an
  internal subpackage.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Use `pre-commit` (the tool at pre-commit.com) as the framework.** Config lives at `.pre-commit-config.yaml`. Managed as a dev dep via `uv add pre-commit --dev`. | Standard in the Python ecosystem. Declarative config, pinned hook versions, ecosystem-wide conventions. Lefthook / husky are plausible alternatives but offer no advantage here and add language-ecosystem mismatch. |
| D2 | **Hook set (v0)**: `ruff check`, `ruff format --check`, `yamllint`, `gitleaks`, and basic cross-platform hygiene (`trailing-whitespace`, `end-of-file-fixer`, `check-merge-conflict`, `check-added-large-files`). | Each is fast (< 1s on a typical commit), non-flaky, and already matches a CI gate. **Exception**: gitleaks' first run downloads its rules DB and takes 10-30s; subsequent runs are near-instant. A3 must document this so contributors don't interpret the first-commit latency as a hang. |
| D3 | **No pyright in pre-commit until Plan 069 Phase 1 lands (deferred to A4).** When wired, the hook invokes `uv run pyright src/` — no flags. Pyright discovers `pyrightconfig.json` at repo root automatically (JSON config, not `[tool.pyright]`). | Pre-commit must stay fast. Today pyright takes 30-60s and reports 675 errors (flows/ carve-out active) — would block every commit. After Plan 069's ratchet lands, the ratchet script becomes the gating mechanism. The JSON config is authoritative per P1 decision. |
| D4 | **Pin every hook by `rev:` (git SHA or tag).** Dependabot's `github-actions` ecosystem does NOT cover pre-commit hooks; a separate update cadence is needed. For gitleaks, use the canonical `gitleaks/gitleaks` repo on GitHub (`https://github.com/gitleaks/gitleaks`). | Mutable tags on pre-commit hooks are the same supply-chain risk Plan 064 C1 addressed for GitHub Actions. Same defense, applied consistently. Manual review quarterly until Dependabot adds pre-commit support OR we wire Renovate for this one file. |
| D5 | **`uv run check` lives at `src/sapphire_flow/cli/check.py`**, exposed via `[project.scripts] check = "sapphire_flow.cli.check:main"` in `pyproject.toml`. Invokes: ruff check, ruff format --check, pytest tests/unit, uv sync --frozen --no-build --no-cache --no-install-project. Single source of truth for "what CI checks." | The build backend is `uv_build`; `[project.scripts]` entries must resolve against the installed package namespace. Top-level `tools/` is not a package path installed by `uv_build` — entries there are not importable in a wheel install. A collision also exists: `src/sapphire_flow/tools/` already exists as an internal subpackage, so any `tools.check` reference would conflict. The `cli/` subpackage is the correct home. |
| D6 | **Gate-parity audit** before declaring the plan DONE. Enumerate every CI step, confirm it has a local equivalent (or is explicitly marked as "CI-only" with reason), and confirm every scheduled workflow has fired at least once and produced the expected outcome. | This is the root-cause fix for the class of problem Plan 064 surfaced. Without the audit, we re-accumulate "wired but unrun" gates. |
| D7 | **Scheduled workflows already have `workflow_dispatch:` triggers** (verified in `integration-nightly.yml` and `live-lindas-weekly.yml`). First-fire verification is still required. | The trigger is present; the issue is that neither workflow has a confirmed successful run in this repo's history. C2 focuses on verification, not adding triggers. |
| D8 | **Ruff hooks in `.pre-commit-config.yaml` are `--check` only, never auto-fixing.** Auto-fix at commit time conflicts with the mandatory `bump-my-version` workflow (CLAUDE.md): an auto-fix would mutate files after staging, aborting the commit and desynchronizing the version bump. CI mirrors this: `uv run ruff format --check` and `uv run ruff check` (no `--fix`). Developers run `uv run ruff format` and `uv run ruff check --fix` manually before committing. | Preserving the staging area between pre-commit hooks and the actual commit is essential for the bump-my-version sequence that CLAUDE.md mandates on every commit. The standard `ruff-format` hook from the pre-commit mirror mutates files by default — this must be overridden with `args: [--check]`. |

---

## Task list

### Stream A — Pre-commit framework

#### A1 — Add `pre-commit` dev dep + config

**Files**: `pyproject.toml`, `.pre-commit-config.yaml` (new)

1. `uv add pre-commit --dev` to add the dev dep.
2. Create `.pre-commit-config.yaml` with the v0 hook set (per D2). Pin
   each hook by rev. Order: fastest hooks first (stop on first failure
   for tight feedback).
   - For `ruff-pre-commit` (the Astral mirror): set `args: [--check]` on
     the `ruff-format` hook and `args: []` (no `--fix`) on the `ruff`
     check hook. Both hooks must be check-only per D8.
   - For gitleaks: use `https://github.com/gitleaks/gitleaks` as the
     canonical source repo. Pin to a recent tagged release (e.g.
     `v8.x.x`). Do NOT use `zricethezav/gitleaks` (deprecated mirror).
3. Do NOT enable any hook on files outside `src/`, `tests/`, `docs/`,
   `.github/`, `scripts/`. Pre-commit defaults scan all changed files;
   explicit per-hook `files:` patterns reduce surprise.
4. After creating the config, run `uv run pre-commit run --all-files`
   and fix any failures in the same PR. Do not leave known failures as
   "will fix later" — the exit gate requires a clean run on all existing
   files.

**Exit**: `.pre-commit-config.yaml` parses (`uv run pre-commit
validate-config`); `uv run pre-commit run --all-files` succeeds on a
clean working tree with all existing files passing.

#### A2 — Install pre-commit for the current developer

**Files**: none (git hooks folder is not tracked)

1. Run `uv run pre-commit install`. Installs `.git/hooks/pre-commit`.
2. Verify: make a deliberate lint violation in a scratch file (e.g., an
   unused import), try `git commit`, confirm the hook BLOCKS the commit
   (exits non-zero). The ruff hook must not auto-fix — per D8, the file
   should remain violated and the commit should be refused, not silently
   repaired and re-staged.
3. Undo the scratch violation before proceeding.

**Exit**: `.git/hooks/pre-commit` exists; a deliberately bad commit is
BLOCKED (not auto-fixed) by ruff; a clean commit succeeds.

#### A3 — Document pre-commit in contributor onboarding

**Files**: `CLAUDE.md` (new "Pre-commit" section), or `README.md`, or a
new `docs/standards/precommit.md` — pick the fit.

1. Add a short section explaining:
   - What pre-commit runs.
   - How to install: `uv run pre-commit install`.
   - How to run manually: `uv run pre-commit run --all-files`.
   - How to bypass in emergencies: `git commit --no-verify` +
     acknowledgement that CI is the backstop.
   - Why hooks are check-only (no auto-fix): the mandatory
     `bump-my-version` step must happen before staging; auto-fix
     would desynchronize the version bump.
2. Cross-reference `docs/standards/cicd.md` as the CI-tier
   documentation.

**Exit**: Onboarding doc mentions pre-commit; `grep -ri "pre-commit"
docs/ CLAUDE.md README.md` returns a coherent mention, not a grab-bag.

#### A4 — Wire pyright ratchet into pre-commit (DEFERRED)

**Trigger**: PR for Plan 069 Phase 1 merged.

**Files**: `.pre-commit-config.yaml`

Add a local hook in `.pre-commit-config.yaml` that runs
`uv run python tools/pyright_ratchet.py` (the script Plan 069 T3
creates). The hook must follow the same two-step pattern as Plan 069
T3: capture `uv run pyright --outputjson src/` output (relying on
`pyrightconfig.json` at repo root for config discovery per D3), then
invoke `tools/pyright_ratchet.py` with the captured JSON and
`tools/pyright_baseline.json`.

**No work required in Plan 070's initial implementation.** This task
exists to ensure A4 is tracked and not forgotten when Plan 069 Phase 1
merges.

**Exit (deferred)**: pyright ratchet runs as a pre-commit hook; a
commit that increases any per-file error count above the baseline is
blocked locally, not just in CI.

---

### Stream B — `uv run check` local-gate helper

#### B1 — Add `check` entrypoint at `src/sapphire_flow/cli/check.py`

**Files**: `pyproject.toml`, `src/sapphire_flow/cli/check.py` (new),
`tests/unit/test_check.py` (new)

1. `src/sapphire_flow/cli/` already exists and has `__init__.py`. Add
   `check.py` alongside `register_deployments.py`.
2. Implement `main()` in `check.py`. The function runs in sequence,
   failing fast on first error:
   - `uv run ruff format --check src/ tests/`
   - `uv run ruff check src/ tests/`
   - `uv run pytest tests/unit`
   - `uv sync --frozen --no-build --no-cache --no-install-project`
   Each step is invoked via `subprocess.run(..., check=True)`. Return
   explicit exit codes: 0 on full success, non-zero propagated from the
   failing step.
3. Add to `pyproject.toml`:
   ```toml
   [project.scripts]
   check = "sapphire_flow.cli.check:main"
   ```
   Note: Top-level `tools/` is not a package path installed by
   `uv_build`; entries must live under `src/sapphire_flow/`.
4. Add `tests/unit/test_check.py` with a smoke test that invokes
   `check` via `subprocess.run(["uv", "run", "check", "--dry-run"],
   check=True)` or similar. At minimum, confirm the entrypoint is
   importable and the `main` function is callable without error in a
   dry/no-op path.

**Exit**: `uv run check` runs locally and passes on a green main;
output is legible; any failure is clearly attributable to which step
failed; smoke test in `tests/unit/test_check.py` passes.

#### B2 — Document `uv run check` in `cicd.md`

**Files**: `docs/standards/cicd.md`

1. Add a one-paragraph "Local gate helper" section pointing at
   `uv run check`. Clarify it mirrors the CI lint+unit+wheel-only-guard
   jobs, not the integration / build-image-and-scan jobs (those require
   Docker and postgres and take longer — run in CI only).
2. Cross-reference Plan 070.

**Exit**: `docs/standards/cicd.md` has the section; a developer
reading it can answer "how do I run what CI runs?" without asking.

---

### Stream C — CI/local gate parity audit

#### C1 — Extend the existing CI workflow tiers table in `cicd.md`

**Files**: `docs/standards/cicd.md`

The existing "CI workflow tiers" table at lines 263-280 already
enumerates 5 jobs with "Depends on" column. Extend it rather than
creating a duplicate:

1. Add two new columns to the table: "Local equivalent" and "CI only?
   Reason".
2. Fill in a row for every `run:` step across all workflow files
   (ci.yml, integration-nightly.yml, live-lindas-weekly.yml), not
   just the 5 jobs already listed. Add rows for any steps not yet
   represented (e.g., trivy fs, trivy image, syft).
3. Flag gaps: any CI step whose "local equivalent" column is empty
   or "CI only" without a reason.

**Exit**: The extended table is complete; every row either has a
concrete local command or an explicit "CI only because X" reason;
no empty cells.

#### C2 — First-fire verification of every scheduled workflow

**Files**: workflow files (comment annotations only if needed)

Both scheduled workflows already have `workflow_dispatch:` triggers
(verified: present in `integration-nightly.yml` and
`live-lindas-weekly.yml`). The task is verification, not wiring:

1. Run `gh workflow list` to confirm both workflows appear and their
   status is not disabled.
2. Run `gh workflow run integration-nightly.yml` for first-fire
   verification. Note the run ID from the output.
3. Run `gh workflow run live-lindas-weekly.yml` for first-fire
   verification. Note the run ID.
4. Monitor both runs via `gh run watch <run-id>`. Record the run IDs
   (in a comment in each workflow file header, or in a note in
   `cicd.md`) so future operators can confirm when first-fire occurred.
5. If either run fails, create a fix commit before declaring C2 done.
   Failures are not acceptable as "known issues" — a never-successfully-
   fired scheduled workflow is the exact trap Plan 064 exposed.

**Exit**: Both scheduled workflows have at least one completed
successful run; run IDs recorded; any failures resolved with a fix
commit.

#### C3 — Gate-parity lint script

**Files**: `tools/gate_parity_check.py` (new)

1. Script that reads `ci.yml` + sibling workflows, extracts every
   `run:` command, and compares to the commands in
   `src/sapphire_flow/cli/check.py`.
2. Report drift: any CI `run:` not covered by `uv run check` AND not
   explicitly excluded as "CI only" in a comment or allowlist.
3. Not wired into CI yet — run manually before merging any workflow
   change. Pre-commit hook for it could come later.

**Exit**: Script exists, runs clean against current repo, flags any
future drift between CI and `uv run check`.

#### C4 — Add `uv run check` as a CI step in the `lint` job

**Files**: `.github/workflows/ci.yml`

1. Add a step to the `lint` job in `ci.yml`, after the ruff steps:
   ```yaml
   - run: uv run check
   ```
2. This keeps the local gate helper honest — if `check.py` drifts from
   CI expectations (e.g., a step is removed or renamed), CI catches it
   on every push/PR.

**Exit**: CI green; `uv run check` execised in every push/PR run of
the `lint` job.

---

### Stream D — Documentation

#### D1 — `docs/standards/cicd.md` consolidated gate strategy

**File**: `docs/standards/cicd.md`

Already edited in A3, B2, C1 above. Final pass:

1. Consolidate the pre-commit / `uv run check` / CI tier story into
   one coherent "Two gates, both enforced" section (per the §Principle
   above).
2. Cross-reference Plan 064 §Supply chain, Plan 069 pyright backlog,
   and this plan's residual tasks (if any).

**Exit**: A single diagram or table in `cicd.md` showing: developer
edit → pre-commit → `uv run check` → `git push` → CI → merge. Readers
can trace the full gate lifecycle.

---

## Priority order

| Rank | Task | Effect | Effort | Why |
|------|------|--------|--------|-----|
| 1 | **A1 + A2 + A3** (pre-commit live) | High | Low (~half day) | Stops new lint/format/secret regressions immediately. Unblocks contributors. |
| 2 | **B1 + B2** (`uv run check`) | High | Low-medium | Removes the "what do I run locally?" question. Direct prevention of the Plan-064 class of issue. |
| 3 | **C1 + C2 + C4** (gate-parity audit + scheduled-workflow first-fire + CI smoke) | Medium (audit) + High (first-fire) | Medium | Confirms existing gates actually run. C2 especially important — a never-fired scheduled workflow is effectively uninstalled. C4 keeps check.py honest. |
| 4 | **C3** (gate-parity lint script) | Medium (long-term) | Low | Defense against future drift between CI and local. Optional polish. |
| 5 | **D1** (doc consolidation) | Low (hygiene) | Low | Capstone — runs after other streams land. |

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-precommit",
      "tasks": ["A1", "A2", "A3"],
      "parallel": false,
      "depends_on": []
    },
    {
      "id": "phase-2-local-helper",
      "tasks": ["B1", "B2"],
      "parallel": false,
      "depends_on": ["phase-1-precommit"]
    },
    {
      "id": "phase-3-parity-audit",
      "tasks": ["C1", "C2", "C3", "C4"],
      "parallel": true,
      "depends_on": ["phase-2-local-helper"]
    },
    {
      "id": "phase-4-docs",
      "tasks": ["D1"],
      "parallel": false,
      "depends_on": ["phase-3-parity-audit"]
    },
    {
      "id": "phase-5-deferred",
      "tasks": ["A4"],
      "parallel": false,
      "depends_on": ["plan-069-phase-1"],
      "note": "A4 is deferred — triggers when Plan 069 Phase 1 merges"
    }
  ]
}
```

Phase-1 is sequential (A1 → A2 → A3) because each task depends on the
previous landing. Phase-3 tasks are mostly independent, with the
caveat that C2's first-fire of `integration-nightly.yml` may expose
issues that C1's audit should cover. A4 is explicitly deferred and
blocked on Plan 069 Phase 1.

---

## Open questions for user review

1. **Where does the pre-commit doc live?** A3 lists three options:
   `CLAUDE.md`, `README.md`, or a new `docs/standards/precommit.md`.
   Recommendation: a short section in `CLAUDE.md` (agent-facing
   conventions document) plus a cross-reference from
   `docs/standards/cicd.md` (human-facing ops doc).
2. **Is `gitleaks` worth the pre-commit cost?** GitHub push-protection
   already blocks known-secret-pattern pushes. gitleaks at pre-commit
   is belt-and-suspenders but adds a dep and a hook run. Recommend
   keeping it — failed-secret push takes real time to rotate, and
   pre-commit catches it before the rotation cost triggers.
3. **Dependency-update cadence for pre-commit hook pins (D4).**
   Quarterly manual review vs Renovate Bot vs waiting for Dependabot to
   add pre-commit support. Recommend quarterly manual for v0; revisit
   when Dependabot catches up.
4. **Does C2 (first-fire of scheduled workflows) block the plan's
   move to DONE, or is it OK to close with `integration-nightly` or
   `live-lindas-weekly` in "scheduled but never-fired" state?**
   Recommend: it blocks. A never-fired scheduled workflow is the
   exact Plan 064 trap we're trying to avoid recurring.

## Changelog

- **2026-04-22** — Initial DRAFT. Motivated by the Plan 064 finding
  that safety nets (pyright, ruff in CI, scheduled workflows) existed
  in the repo but had never actually executed before the first push.
  Four-stream plan: pre-commit framework (A) → local gate helper (B)
  → parity audit + scheduled-workflow first-fire (C) → doc
  consolidation (D). Designed to land before Plan 069 so pre-commit
  stops new pyright errors from leaking in while Plan 069 drains the
  backlog under a ratchet.

- **2026-04-22 (rewrite)** — Addressed three critical-review blockers:
  (1) ruff hooks switched to `--check` only (new D8) to avoid conflict
  with mandatory bump-my-version workflow per CLAUDE.md; (2)
  `[project.scripts]` path corrected to `src/sapphire_flow/cli/check.py`
  for `uv_build` compatibility and to avoid collision with existing
  `src/sapphire_flow/tools/` subpackage (per P4 decision); (3) added
  explicit `pyrightconfig.json` handling throughout plus a new D8 and A4
  (deferred) for future pyright hook wiring. Smaller corrections: C2
  rewritten (workflow_dispatch already present), C1 extends existing
  cicd.md table (not duplicates), new C4 adds `uv run check` as a CI
  step. Added Cross-plan coordination section with merge order.
