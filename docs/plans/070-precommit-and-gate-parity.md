# Plan 070 — Pre-commit hooks + CI/local gate parity

**Status**: DONE (Phases 1–4 implemented 2026-05-11; A4 deferred pending Plan 069 Phase 1)
**Date**: 2026-04-22 (DRAFT) → 2026-05-11 (READY → DRAFT → READY → DONE, four review rounds + four implementation phases in one day)
**Depends on**: none (independent of Plan 069 but intentionally lands first:
stops new lint/format/secret regressions from leaking in while Plan 069
drains the pyright backlog under a ratchet).
**Scope**: Close the "safety net wired but not actually running" gap that
Plan 064 surfaced. Install `pre-commit` (the tool) with ruff and
gitleaks hooks as the developer-tier gate; add a `uv run check` developer-side helper that mirrors the `lint` job's local-reproducible steps (ruff format/check); CI itself keeps its existing standalone steps; audit every CI gate
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
- 17 pre-existing ruff errors lived on HEAD for a long time. Nobody was
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
gitleaks); add pyright as a pre-commit hook only after Plan 069
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
  GitHub's push-protection (GitHub push-protection is the primary defense — verify it is enabled in repo Settings → Security → Code security and analysis → Push protection; Plan 064 left enablement out of scope, so verify before relying on this claim) is the
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
  `ci.yml` but not yet implemented (the file is 206 lines; the e2e job header is a dangling comment at the end and not yet implemented).
- `.github/workflows/ci.yml` — 5 jobs: `lint`, `unit`, `wheel-only-guard`,
  `integration`, `build-image-and-scan`. `lint` has pyright commented out
  pending Plan 069.
- `.github/workflows/live-lindas-weekly.yml` — scheduled weekly (Monday
  06:00 UTC). Already has `workflow_dispatch:`. **First-fire verified**:
  first success was run `24824715145` (2026-04-23 08:18 UTC,
  `workflow_dispatch`); first scheduled success was run `24980849671`
  (2026-04-27 06:53 UTC). Subsequent Monday schedules at 06:00 UTC fail
  intermittently: 2 of 3 observed Monday-schedule runs failed
  (2026-05-04 + 2026-05-11), against 1 Monday-schedule success
  (2026-04-27). The intermittent-pattern signal is being investigated —
  see `docs/decisions/bafu-lindas-monday-window.md` for evidence (the
  2026-05-11 04:46 UTC integration-nightly run executed the same
  `test_lindas_live_schema` test and passed, before BAFU's 07:03 UTC
  republish overwrote the dataset). 5 total runs by 2026-05-11: 3 successes (2026-04-23 dispatch, 2026-04-27 schedule, 2026-05-04 dispatch) and 2 failures (2026-05-04 schedule, 2026-05-11 schedule). See `docs/decisions/bafu-lindas-monday-window.md` for the per-run breakdown.
- `.github/workflows/integration-nightly.yml` — scheduled daily (03:00
  UTC). Already has `workflow_dispatch:`. Renamed during Plan 064.
  **First-fire verified**: first success was run `24825587484`
  (2026-04-23 08:39 UTC, `workflow_dispatch`); first scheduled success
  was run `24922121174` (2026-04-25 04:02 UTC). 17 consecutive
  scheduled successes from 2026-04-25 through 2026-05-11, 19–22 min
  each, after the `--timeout=3600` + `--override-ini "addopts="`
  patches landed in v0.1.397.
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
| D2 | **Hook set (v0)**: `ruff check`, `ruff format --check`, `gitleaks`, and basic cross-platform hygiene (`trailing-whitespace`, `end-of-file-fixer`, `check-merge-conflict`, `check-added-large-files`). | Each is fast (< 1s on a typical commit), non-flaky, and already matches a CI gate. **Exception**: gitleaks' first run downloads its rules DB and takes 10-30s; subsequent runs are near-instant. A3 must document this so contributors don't interpret the first-commit latency as a hang. **Note**: `trailing-whitespace`, `end-of-file-fixer`, and similar hygiene hooks from the `pre-commit-hooks` repo mutate files by default — the same conflict with the mandatory `bump-my-version` commit sequence that D8 catches for ruff. They cannot be set to `--check`-only (the upstream hooks have no such flag), so A1 must add them with a documented expectation: the FIRST commit on a dirty file is blocked by the mutating hook (file is auto-fixed, exit non-zero), the developer runs `git add <fixed-files>` and re-commits. A3 must call this out in the onboarding doc. The asymmetry with D8's "no auto-fix for ruff" is deliberate: ruff has `--check` mode, the hygiene hooks do not; the hygiene hooks' "first-commit-blocked, re-stage, re-commit" UX is the minimum-viable cross-platform-hygiene option. If this UX cost becomes painful, drop the hygiene hooks from v0 and address whitespace via ruff's whitespace rules (which are check-only). |
| D3 | **No pyright in pre-commit until Plan 069 Phase 1 lands (deferred to A4).** When wired (A4, deferred), the hook follows the two-step pattern Plan 069 T3 establishes: capture `uv run pyright --outputjson src/` to a tempfile, then invoke `uv run python tools/pyright_ratchet.py <tempfile> tools/pyright_baseline.json`. Pyright discovers `pyrightconfig.json` at repo root automatically; A4 carries the inline invocation spec so this plan remains self-contained. | Pre-commit must stay fast. Today pyright takes 30-60s and reports 675 errors (flows/ carve-out active) — would block every commit. After Plan 069's ratchet lands, the ratchet script becomes the gating mechanism. The JSON config is authoritative per the §Inputs note on `pyrightconfig.json`. |
| D4 | **Pin every hook by `rev:` (git SHA or tag).** Dependabot's `github-actions` ecosystem does NOT cover pre-commit hooks; a separate update cadence is needed. For gitleaks, use the canonical `gitleaks/gitleaks` repo on GitHub (`https://github.com/gitleaks/gitleaks`). | Mutable tags on pre-commit hooks are the same supply-chain risk Plan 064 C1 addressed for GitHub Actions. Same defense, applied consistently. Manual review quarterly until Dependabot adds pre-commit support OR we wire Renovate for this one file. |
| D5 | **`uv run check` lives at `src/sapphire_flow/cli/check.py`**, exposed via `[project.scripts] check = "sapphire_flow.cli.check:main"` in `pyproject.toml`. Invokes: ruff check, ruff format --check. Note: invoked locally as `uv run check`; bare `check` on PATH outside `uv run` may resolve to other tools (uv ensures the project venv on PATH inside `uv run`). | The build backend is `uv_build`; `[project.scripts]` entries must resolve against the installed package namespace. Top-level `tools/` is not a package path installed by `uv_build` — entries there are not importable in a wheel install. A collision also exists: `src/sapphire_flow/tools/` already exists as an internal subpackage, so any `tools.check` reference would conflict. The `cli/` subpackage is the correct home. |
| D6 | **Gate-parity audit** before declaring the plan DONE. Enumerate every CI step, confirm it has a local equivalent (or is explicitly marked as "CI-only" with reason), and confirm every scheduled workflow has fired at least once and produced the expected outcome. | This is the root-cause fix for the class of problem Plan 064 surfaced. Without the audit, we re-accumulate "wired but unrun" gates. Scope-limited: `uv run check` exercises the **`lint` job's** local-reproducible ruff steps (`ruff format --check` + `ruff check`). The `unit` and `integration` jobs are CI-only because they require system deps (libeccodes, libgeos) and a postgres service that local-helper invocation should not assume. `uv run check` does NOT invoke `uv sync`: developers typically have a synced venv when invoking it, and CI's lint job runs `uv sync --frozen` at the workflow level before the ruff steps. |
| D7 | **Scheduled workflows already have `workflow_dispatch:` triggers** (verified in `integration-nightly.yml` and `live-lindas-weekly.yml`). First-fire verification is still required. | Both scheduled workflows are now demonstrably running (see §Inputs and C2 for first-success run IDs). The recording task in C2 captures that evidence into a discoverable location so future operators can confirm without re-querying `gh run list`. |
| D8 | **Ruff hooks in `.pre-commit-config.yaml` are `--check` only, never auto-fixing.** Auto-fix at commit time conflicts with the mandatory `bump-my-version` workflow (CLAUDE.md): an auto-fix would mutate files after staging, aborting the commit and desynchronizing the version bump. CI mirrors this: `uv run ruff format --check` and `uv run ruff check` (no `--fix`). Developers run `uv run ruff format` and `uv run ruff check --fix` manually before committing. | Preserving the staging area between pre-commit hooks and the actual commit is essential for the bump-my-version sequence that CLAUDE.md mandates on every commit. The standard `ruff-format` hook from the pre-commit mirror mutates files by default — this must be overridden with `args: [--check]`. |

---

## Task list

### Stream A — Pre-commit framework

#### A1 — Add `pre-commit` dev dep + config

**Files**: `pyproject.toml`, `.pre-commit-config.yaml` (new)

1. `uv add pre-commit --dev` to add the dev dep.
2. Create `.pre-commit-config.yaml` with the v0 hook set (per D2). Pin
   each hook by rev. Use exact git tags (not floating refs); pick the most recent stable tag for each repo at implementation time (verify via that repo's GitHub releases page). At minimum capture: ruff-pre-commit (Astral mirror, v0.x.y), pre-commit-hooks (v5.0.0 or current latest), gitleaks (gitleaks/gitleaks v8.x.y). Record the chosen pins in the commit message so future Dependabot/Renovate adoption has a baseline. Order: fastest hooks first (stop on first failure
   for tight feedback).
   - For `ruff-pre-commit` (the Astral mirror): set `args: [--check]` on
     the `ruff-format` hook and `args: []` (no `--fix`) on the `ruff`
     check hook. Both hooks must be check-only per D8.
   - For gitleaks: use `https://github.com/gitleaks/gitleaks` as the
     canonical source repo. Pin to a recent tagged release (e.g.
     `v8.x.x`). Do NOT use `zricethezav/gitleaks` (deprecated mirror).
   - For `trailing-whitespace` and `end-of-file-fixer` (from
     `https://github.com/pre-commit/pre-commit-hooks`): these mutate
     files. A3's onboarding doc must explain that the first commit on a
     dirty file is blocked by the mutating hook (file is auto-fixed,
     exit non-zero); the developer runs `git add <fixed-files>` and
     re-commits.
3. Do NOT enable any hook on files outside `src/`, `tests/`, `docs/`,
   `.github/`, `scripts/`, `tools/`. (`tools/` is dev-only-script territory but is git-tracked and C3 creates a file there.) Pre-commit defaults scan all changed files;
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
4. Note on the mandatory `bump-my-version` workflow (CLAUDE.md): the verification scratch commit is NOT a real commit — the hook is supposed to block it. If you accidentally run `bump-my-version bump patch` before the failed commit attempt, the version bump file (`src/sapphire_flow/__init__.py` + `pyproject.toml`) will already be modified. To clean up: `git checkout HEAD -- src/sapphire_flow/__init__.py pyproject.toml` to revert the bump, then retry the verification without a version bump. The plan's expectation is that A2 verification runs WITHOUT a bump.

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
   - Insertion point: place the new "Pre-commit" section after the existing "Python Package Management with `uv`" section and before "Ad-hoc Analyses and One-Time Scripts" (verify these section headings exist in CLAUDE.md at implementation time; if the structure has shifted, place it adjacent to the uv-tooling discussion).
2. Cross-reference `docs/standards/cicd.md` as the CI-tier
   documentation.

**Exit**: Onboarding doc mentions pre-commit; `grep -ri "pre-commit"
docs/ CLAUDE.md README.md` returns a coherent mention, not a grab-bag.

#### A4 — Wire pyright ratchet into pre-commit (DEFERRED)

**Trigger**: PR for Plan 069 Phase 1 merged (which lands `tools/pyright_ratchet.py` and `tools/pyright_baseline.json`).

**Files**: `.pre-commit-config.yaml`

When the trigger fires, add a `local` hook to `.pre-commit-config.yaml`:

    - repo: local
      hooks:
        - id: pyright-ratchet
          name: pyright ratchet
          language: system
          pass_filenames: false
          entry: bash -c 'uv run pyright --outputjson src/ > /tmp/pyright.json || true; uv run python tools/pyright_ratchet.py /tmp/pyright.json tools/pyright_baseline.json'

The two-step pattern is required because pre-commit hooks do not pipe stdout
between commands. The bash wrapper captures pyright's JSON output to a tempfile,
then invokes the ratchet script with both file paths as positional args. The
ratchet script is responsible for the exit code (0 = within baseline; 1 = above
baseline). If Plan 069 T3's invocation contract changes later, update A4 then.

**No work required in Plan 070's initial implementation.** This task exists to
ensure A4 is tracked and not forgotten when Plan 069 Phase 1 merges.

**Exit (deferred)**: pyright ratchet runs as a pre-commit hook; a commit that
increases any per-file error count above the baseline is blocked locally, not
just in CI.

---

### Stream B — `uv run check` local-gate helper

#### B1 — Add `check` entrypoint at `src/sapphire_flow/cli/check.py`

**Files**: `pyproject.toml`, `src/sapphire_flow/cli/check.py` (new),
`tests/unit/test_check.py` (new)

1. `src/sapphire_flow/cli/` already exists and has `__init__.py`. Add
   `check.py` alongside `register_deployments.py`.
2. Implement `main() -> int` in `check.py`. The function runs in sequence,
   failing fast on first error and returning the failing step's exit code
   (or 0 on full success):
   - `uv run ruff format --check src/ tests/`
   - `uv run ruff check src/ tests/`
   Each step is invoked via `subprocess.run(..., check=False)` (NOT
   `check=True`); the script inspects `result.returncode` and returns it on
   first non-zero. The console script entrypoint is wired by adding the
   following at module-bottom:
       if __name__ == "__main__":
           import sys
           sys.exit(main())
   And `[project.scripts]` is wired separately (B1 step 3) — for the wrapper
   to propagate exit codes, `main()` MUST be invoked through `sys.exit(...)`,
   not bare-called. Pytest is intentionally NOT in `uv run check`: (a) the
   lint CI job installs no system deps (libeccodes0, libgeos-c1v5) that
   several unit tests require via `cfgrib`/`exactextract`; (b) it would
   duplicate the dedicated `unit` CI job's pytest run. Pytest stays in the
   `unit` job only. `uv sync` is also NOT in `uv run check`: CI's lint job
   already runs `uv sync --frozen` at the workflow level, and developers
   typically have a synced venv when invoking `uv run check`.
3. Add to `pyproject.toml`:
   ```toml
   [project.scripts]
   check = "sapphire_flow.cli.check:main"
   ```
   Note: Top-level `tools/` is not a package path installed by
   `uv_build`; entries must live under `src/sapphire_flow/`.
4. Add `tests/unit/test_check.py` with a smoke test that imports `main` from
   `sapphire_flow.cli.check` and confirms it is callable. Use monkeypatch to
   stub `subprocess.run` so the test does not actually invoke ruff:

       import subprocess
       from sapphire_flow.cli import check as check_module

       def test_main_returns_zero_when_all_steps_succeed(monkeypatch):
           class _Result:
               returncode = 0
           monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
           assert check_module.main() == 0

       def test_main_returns_nonzero_on_first_failure(monkeypatch):
           class _Fail:
               returncode = 2
           monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Fail())
           assert check_module.main() == 2

   Do NOT use a `--dry-run` flag; `main()` takes no CLI args in v0.

**Exit**: `uv run check` runs locally and passes on a green main;
output is legible; any failure is clearly attributable to which step
failed; smoke test in `tests/unit/test_check.py` passes.

#### B2 — Document `uv run check` in `cicd.md`

**Files**: `docs/standards/cicd.md`

1. Add a one-paragraph "Local gate helper" section pointing at
   `uv run check`. Clarify it mirrors the CI `lint` job's ruff steps
   (`ruff format --check` + `ruff check`), not the integration /
   build-image-and-scan jobs (those require Docker and postgres and take
   longer — run in CI only). It does NOT invoke `uv sync`: developers
   typically have a synced venv, and CI's lint job runs `uv sync --frozen`
   at the workflow level. Cross-reference the `unit` and
   `integration` CI jobs as separate steps developers can invoke
   manually (`uv run pytest tests/unit`) when they want pre-merge
   confidence.
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
4. Example rows for the extended table (use these as a starting template):
   | Step | Tier | Depends on | Local equivalent | CI only? Reason |
   |---|---|---|---|---|
   | `uv run ruff format --check src/ tests/` | 1 (lint) | — | `uv run ruff format --check src/ tests/` (also via `uv run check` and pre-commit) | No |
   | `sudo apt-get install -y libeccodes0 libexpat1 libgeos-c1v5` | 2 (unit) | — | Brew/apt on the dev host (developer responsibility) | Yes — system-package install, not project-managed |
   | `docker/build-push-action` | 5 (build-image-and-scan) | unit, integration | `docker buildx build` locally | No (but requires Docker daemon) |
   | `aquasecurity/trivy-action` (image scan) | 5 | build-image-and-scan | `trivy image <tag>` locally | No (but requires the image to be built) |
   | `anchore/sbom-action` | 5 | build-image-and-scan | `syft <image>` locally | No (but requires syft installed) |
   Fill in similar rows for every `run:` step you find across the three workflow files.

**Exit**: The extended table is complete; every row either has a
concrete local command or an explicit "CI only because X" reason;
no empty cells.

#### C2 — Record first-fire status of every scheduled workflow

**Files**: workflow files (header comment annotations) or
`docs/standards/cicd.md`

Both scheduled workflows have already fired multiple times by the
time this plan moves to READY (see §Inputs). The remaining task is
to record the first-fire run IDs and document the intermittent-failure
caveat for `live-lindas-weekly.yml`.

1. Run `gh workflow list` to confirm both workflows are present and
   not disabled.
2. Record first-success run IDs in a header comment of each workflow
   file (authoritative location for first-fire IDs in v0; `cicd.md` may cross-reference but does not duplicate the IDs):
   - `integration-nightly.yml`: first-fire success = run **24825587484**
     (2026-04-23 08:39 UTC, `workflow_dispatch`); first scheduled
     success = run **24922121174** (2026-04-25 04:02 UTC); 17
     consecutive scheduled successes through 2026-05-11.
   - `live-lindas-weekly.yml`: first-fire success = run **24824715145**
     (2026-04-23 08:18 UTC, `workflow_dispatch`); first scheduled
     success = run **24980849671** (2026-04-27 06:53 UTC).
3. **LINDAS carve-out**: the intermittent 2026-05-04 + 2026-05-11
   Monday-morning failures of `live-lindas-weekly.yml` are an upstream
   BAFU LINDAS publishing-pipeline regression, not a workflow defect
   (2 of 3 observed Monday-schedule runs failed; 1 succeeded on
   2026-04-27). Add a "Known external-dependency caveats" note in
   `docs/standards/cicd.md` linking to
   `docs/decisions/bafu-lindas-monday-window.md`
   (BAFU support contact: `abfragezentrale@bafu.admin.ch`).
   Do **not** block Plan 070 DONE on the Monday-schedule failure;
   the workflow itself has demonstrated a successful run path.
4. Cron-rescheduling of `live-lindas-weekly.yml` (e.g. moving off
   `0 6 * * 1` to a later UTC slot) is **out of scope for Plan 070**.
   If today's afternoon retest confirms BAFU recovers later in the
   day, a follow-on one-task plan handles it.

**Exit**: Both first-fire run IDs are recorded in a discoverable
location (workflow header comment or `cicd.md`); the LINDAS
carve-out is documented; no failures from Plan-070-implemented
gates remain unresolved.

#### C3 — Gate-parity lint script

**Files**: `tools/gate_parity_check.py` (new) (top-level `tools/` — dev-only script invoked via `uv run python tools/gate_parity_check.py`; not on the `uv_build` package path, which is why B1's `check.py` lives under `src/sapphire_flow/cli/` instead).

1. Script that reads `ci.yml` + sibling workflows, extracts every
   `run:` command, and compares to the commands in
   `src/sapphire_flow/cli/check.py`.
2. Report drift: any CI `run:` not covered by `uv run check` AND not
   explicitly excluded as "CI only" in a comment or allowlist.
3. Input format: the script accepts no CLI args in v0; it discovers the three workflow YAML files at `.github/workflows/*.yml` and parses them with `yaml.safe_load`.
4. Output format: human-readable table on stdout (one row per CI `run:` step), columns: workflow-name, job-name, step-name, run-command, status (`covered-by-check.py`, `covered-by-uv-sync`, `allowlisted-as-ci-only`, or `drift`).
5. Exit code: 0 if no drift; 1 if any step is `drift`.
6. Allowlist format: a top-level Python dict at the head of the script — `CI_ONLY_ALLOWLIST: dict[tuple[str, str], str] = {("build-image-and-scan", "docker/build-push-action@..."): "requires Docker daemon"}`. Comments inline. No external config file in v0.
7. Heuristic for "covered by check.py": the step's `run:` field, normalized (whitespace-collapsed), starts with one of `uv run ruff format --check` or `uv run ruff check`. Anything else is either covered-by-uv-sync (if it starts with `uv sync`), allowlisted, or drift.
8. The script does NOT yet attempt to parse `uses:` actions vs `run:` shell commands beyond presence-detection — `uses:` rows are auto-routed to the allowlist check.
9. Not wired into CI yet — run manually before merging any workflow
   change. Pre-commit hook for it could come later.

**Exit**: Script exists, runs clean against current repo, flags any
future drift between CI and `uv run check`.

---

### Stream D — Documentation

#### D-Final-Pass — `docs/standards/cicd.md` consolidated gate strategy

**File**: `docs/standards/cicd.md`

If A3, B2, and C1 leave the `cicd.md` content fragmented, this final pass consolidates them.

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
| 3 | **C1 + C2 + C3** (gate-parity audit + scheduled-workflow first-fire + parity script) | Medium (audit) + High (first-fire) | Medium | Confirms existing gates actually run. C2 especially important — a never-fired scheduled workflow is effectively uninstalled. C3 guards against future drift between CI and local. |
| 4 | **D-Final-Pass** (doc consolidation) | Low (hygiene) | Low | Capstone — runs after other streams land. |

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
      "tasks": ["C1", "C2", "C3"],
      "parallel": true,
      "depends_on": ["phase-2-local-helper"]
    },
    {
      "id": "phase-4-docs",
      "tasks": ["D-Final-Pass"],
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

All resolved 2026-05-11 at READY promotion:

1. **Where does the pre-commit doc live?** → **Resolved**: short
   section in `CLAUDE.md` (agent-facing conventions) plus a
   cross-reference from `docs/standards/cicd.md` (human-facing ops
   doc). A3 reflects this.
2. **Is `gitleaks` worth the pre-commit cost?** → **Resolved**: yes,
   keep it. Belt-and-suspenders alongside GitHub push-protection.
   A1/D2 unchanged.
3. **Dependency-update cadence for pre-commit hook pins (D4).** →
   **Resolved**: quarterly manual review until Dependabot supports
   the `pre-commit:` ecosystem. D4 unchanged.
4. **Does C2 (first-fire of scheduled workflows) block DONE?** →
   **Resolved**: C2's first-fire-verification work is materially done
   (see §Inputs), so it becomes a recording task rather than a
   blocking gate. The intermittent `live-lindas-weekly` Monday failures
   are explicitly carved out per C2 step 3 (upstream BAFU
   publishing-pipeline regression, not a Plan 070 defect).

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
  `src/sapphire_flow/tools/` subpackage (per the §Architecture decisions D5 rationale); (3) added
  explicit `pyrightconfig.json` handling throughout plus a new D8 and A4
  (deferred) for future pyright hook wiring. Smaller corrections: C2
  rewritten (workflow_dispatch already present), C1 extends existing
  cicd.md table (not duplicates), new C4 adds `uv run check` as a CI
  step. Added Cross-plan coordination section with merge order.

- **2026-05-11 (READY)** — Flipped DRAFT → READY. All 4 open questions
  resolved with the plan's own recommendations (CLAUDE.md for
  pre-commit docs; keep gitleaks; quarterly manual hook-pin updates;
  C2 blocks DONE in principle but with a LINDAS carve-out). §Inputs
  updated to reflect that both scheduled workflows have demonstrably
  fired since the plan was drafted: `integration-nightly.yml` is green
  for 17 consecutive scheduled nightly runs through 2026-05-11;
  `live-lindas-weekly.yml` had its first manual-dispatch success on
  2026-05-04 15:58 UTC. C2 reshaped from "trigger and verify" to
  "record run IDs and document the LINDAS-Monday BAFU upstream caveat"
  (see `docs/decisions/bafu-lindas-monday-window.md`).
  Cron-rescheduling of LINDAS explicitly punted to a separate
  follow-on plan if today's afternoon retest confirms the BAFU
  pattern.

- **2026-05-11 (READY corrections)** — Post-promotion review by two
  parallel Sonnet 4.6 agents (architectural + risk/correctness)
  identified factual errors and one structural defect introduced by
  the same-day READY edits. Corrections applied: (a) replaced
  incorrect first-fire run IDs (`25300938630`, `25329079256`) with
  the actual first-success runs `24825587484` (integration-nightly,
  2026-04-23 08:39 UTC dispatch) and `24824715145`
  (live-lindas-weekly, 2026-04-23 08:18 UTC dispatch); revised count
  claims (17 consecutive nightly successes since 2026-04-25; 5 total
  live-lindas-weekly runs by 2026-05-11). (b) Recharacterised the
  LINDAS Monday failures as **intermittent** rather than
  "recurring" — 1 of 3 observed Mondays succeeded
  (2026-04-27). (c) Migrated the LINDAS evidence record from a
  private memory file into the repo at
  `docs/decisions/bafu-lindas-monday-window.md`. (d) Removed pytest
  from `uv run check` (B1) because the lint job has no system deps;
  adjusted B2, C4, D6 framing accordingly. (e) Added handling for
  mutating non-ruff hooks (`trailing-whitespace`,
  `end-of-file-fixer`) in D2 and A1 — these cannot use `--check`-only
  and conflict with the bump-my-version sequence the same way ruff
  did. (f) Fixed D7 stale rationale. (g) Replaced orphaned `P1`/`P4`
  decision references with D-numbered citations. (h) Minor: "19
  ruff errors" → "17"; ci.yml "line 190" → "line 206".

- **2026-05-11 (DRAFT — second-round corrections)** — Status reverted
  READY → DRAFT after a three-agent re-review identified 13 P1 + 11 P2
  residual issues introduced by or surviving the first correction pass.
  Design decisions made during this pass: (a) `uv run check` becomes a
  developer-only ergonomic wrapper; C4 deleted; CI's lint job keeps its
  existing standalone ruff steps unchanged. (b) Yamllint dropped from
  the v0 hook set — no CI yamllint counterpart, no `.yamllint.yml` in
  repo, parity-invariant violation eliminated. (c) Stream D's task `D1`
  renamed to `D-Final-Pass` to disambiguate from Architecture Decision
  D1. (d) `check.py` `main() -> int` returns explicit exit codes; the
  `[project.scripts]` wrapper calls `sys.exit(main())` so the console
  script propagates the int. (e) B1 smoke test rewritten to use
  in-process monkeypatching of `subprocess.run` (no `--dry-run` flag).
  (f) A4 (deferred) is now self-contained — carries the inline pyright
  ratchet invocation spec rather than delegating to Plan 069 T3.
  (g) Multiple residual references to pytest, the `memory/...` path,
  "recurring" Monday failures, and the "single source of truth" claim
  were swept. (h) Cross-plan: Plan 069 Changelog had orphaned
  `(per P1/P2/P3/P6 decision)` citations — fixed to D-numbered refs in
  a separate edit pass on that plan.

  This is the second corrections pass. The plan stays DRAFT until the
  orchestrator (Opus) confirms a final review pass is clean.

- **2026-05-11 (DRAFT — final-review touch-ups)** — One additional
  Sonnet 4.6 reviewer ran a post-corrections sweep and surfaced five
  small residuals, all fixed inline: (1) **P1 correctness** — A4's
  deferred pyright-ratchet bash entry used `&&` to chain pyright to
  the ratchet script; pyright exits non-zero whenever errors exist, so
  `&&` would have prevented the ratchet from ever firing and blocked
  every commit. Changed to `|| true; ` so the ratchet always runs
  regardless of pyright's exit code (mirrors Plan 069 T3's pattern).
  (2) D6 rationale's "ruff, sync" framing — stale after `uv sync` was
  dropped from `uv run check`; updated to "ruff format --check + ruff
  check" with an explicit note that `uv run check` does NOT invoke
  `uv sync`. (3) B2 step 1's "sync-frozen verification" phrasing —
  same staleness; cleaned up to match the actual `check.py` scope.
  (4) §Priority order had a duplicate C3 row (rank 3 and rank 4)
  from the C4 deletion; removed rank 4's standalone C3 row and shifted
  D-Final-Pass to rank 4. (5) §Inputs "recurring-pattern signal" →
  "intermittent-pattern signal" to match the §Open Questions and
  decision-record language.

  Ready for orchestrator re-promotion to READY.

- **2026-05-11 (READY — post-review-rounds)** — Status flipped
  DRAFT → READY after a fourth Sonnet 4.6 sanity-check review came
  back clean (one P3 word-count nit in the round-3 changelog —
  "four" → "five" — fixed immediately). Four review rounds total
  applied today; approximately 50 surgical edits. The plan is
  implementation-ready. Implementation is gated on a separate
  go-ahead from the orchestrator.

- **2026-05-11 (DONE — Phases 1–4 landed)** — All four phases
  implemented and committed in the same day. Each phase was
  delegated to a Sonnet 4.6 subagent; each commit ran through the
  newly-installed pre-commit gate (developer-tier gate
  self-validating). Phase 1 also caught one P1 latent bug during
  the second review round: A4's deferred pyright-ratchet hook
  originally used `&&` to chain pyright to the ratchet script;
  pyright exits non-zero on any error, so the ratchet would never
  fire. Fixed to `|| true; ` before READY promotion.

  | Phase | Tasks | Commit | Tag |
  |---|---|---|---|
  | 1 | A1 pre-commit framework + A2 install + A3 CLAUDE.md docs | `0223e8e` | v0.1.422 |
  | 2 | B1 `uv run check` helper + B2 cicd.md doc | `804ac59` | v0.1.426 |
  | 3 | C1 cicd.md tier-table extension + C2 first-fire registers + C3 `tools/gate_parity_check.py` | `0677c0e` | v0.1.427 |
  | 4 | D-Final-Pass cicd.md consolidating gate-lifecycle section | `b94d393` | v0.1.429 |

  Auxiliary commits landed alongside (not numbered phases):
  - `b645796` v0.1.423 — cosmetic `ruff` → `ruff-check` modern hook id
  - `aed6666` v0.1.424 — README §3 pre-commit install instruction
  - `c36e8e7` v0.1.425 + `ed83dad` v0.1.428 — uv.lock alignment (the
    recurring `bump-my-version` rebuild-step drift)

  **A4 remains deferred** — the pyright-ratchet pre-commit hook is
  blocked on Plan 069 Phase 1 landing the `tools/pyright_baseline.json`
  + `tools/pyright_ratchet.py` artifacts. When Plan 069 Phase 1 merges,
  reactivate A4 from this plan's task list and add the local hook
  per the spec already embedded in A4.

  Plan kept at its current (non-archived) path so future-Claude can
  pick up A4 from `docs/plans/070-…md` rather than digging through
  `docs/plans/archive/`. Move to archive only after A4 lands.
