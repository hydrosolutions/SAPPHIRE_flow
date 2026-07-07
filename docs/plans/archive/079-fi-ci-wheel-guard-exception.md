---
Status: READY
Created: 2026-06-24
Plan: 079
Title: FI CI wheel-guard exception
Branch: feat/forecast-interface-adherence
PR: 25
---

# Plan 079 - FI CI wheel-guard exception

## Problem

PR #25 (`feat/forecast-interface-adherence`) is not merge-ready: GitHub reports
it as draft, `mergeable=CONFLICTING`, head
`55ba1894810e23902e1627243f8b66e96e9ed9c9`, with all five CI jobs failing in
run `27847286573` (`lint`, `unit`, `wheel-only-guard`, `integration`,
`build-image-and-scan`). `gh pr view 25` returned that state on 2026-06-24.

The FI dependency itself is intentional and must remain a git pin for this
bridge PR. On the PR branch, `pyproject.toml` declares `forecastinterface` as a
dependency at line 20 and pins its source to
`https://github.com/hydrosolutions/ForecastInterface.git`, rev `v0.1.17`, at
line 80. The branch lockfile records that tag as commit
`303aa422da45e293070ef1522251c782bbbf2b7b` and shows FI depends only on
`polars` and `pydantic` at `uv.lock` lines 998-1003.

The structural CI blocker is the wheel-only guard. Current `ci.yml` documents
and runs a single `uv sync --frozen --no-build --no-cache --no-install-project`
step at lines 64-96. `docs/standards/security.md` makes the same command the
canonical supply-chain policy at lines 385-391, and `docs/standards/cicd.md`
mirrors it in the CI tier table at line 346. A git dependency with no published
wheel cannot pass that command because `--no-build` forbids source builds.

The original private-repo clone failures in the other four jobs should
self-heal now that the ForecastInterface repository is public, but that must be
confirmed by a post-public re-run after the branch is rebased. The wheel-only
guard will not self-heal without a documented, temporary exception.

## Decisions

1. Keep `forecastinterface` as the existing git-pinned dependency for PR #25.
   Do not move this PR to PyPI or a private package index.

2. Do not drop `--no-build` globally. The Plan 064 control is still valid:
   current policy says the guard fails if a new package version requires
   build-backend / sdist execution on GitHub-hosted CI
   (`docs/standards/security.md:385-391`).

3. Implement the FI exception as a two-step guard, because `uv sync --help`
   for repo-standard `uv 0.11.7` exposes `--no-build` as a global source-build
   prohibition and `--no-build-package <package>` only as a per-package
   prohibition. There is no single-command inverse such as
   `--build-package forecastinterface` under global `--no-build`.

   The exception rests on an ordering invariant: the first command must keep
   `--no-build`, must run before the second command, and is the control that
   gates every package except `forecastinterface`. Implement the two commands
   as one GitHub Actions `run: |` block so a future edit cannot silently reorder
   the guard or strip `--no-build` from the first step:

   ```yaml
   - run: |
       set -euo pipefail
       # step 1 IS the guard — must keep --no-build and must precede step 2; step 2 has no --no-build and may build ONLY forecastinterface.
       uv sync --frozen --no-build --no-cache --no-install-project --no-install-package forecastinterface
       uv sync --frozen --no-cache --no-install-project --reinstall-package forecastinterface
   ```

   The first command is the actual third-party wheel-only guard: it installs
   every dependency except the approved exception. The structural argument is
   load-bearing: `forecastinterface` is the only git-sourced package in the PR
   branch lock, and no registry dependency in that lock is sdist-only for the
   GitHub-hosted amd64 guard platform. Therefore, if step 1 passes, every
   non-FI dependency has resolved to an installable wheel; step 2 can only build
   the missing locked FI package. During implementation, verify from a clean CI
   run that the second command changes only `forecastinterface`; if `uv`
   attempts to build any other package in that step, stop and revise the plan
   rather than weakening the guard. Under `--no-cache`, the guard job now clones
   and builds FI from the public GitHub repository on every run; an FI-repo or
   GitHub outage will fail the guard job, which is acceptable for this
   temporary exception and documented here.

4. Document the exception in the Plan 064 supply-chain policy section, not only
   in workflow comments. The documented justification is narrow:
   `forecastinterface` is first-party, public, pure Python, git-pinned to
   `v0.1.17` / `303aa422...`, and currently outside Plan 064's untrusted
   native-build threat model. The removal trigger is explicit: remove the
   exception once FI is published as a versioned wheel to a hydrosolutions
   package index and SAPPHIRE Flow can depend on `forecastinterface==0.1.x`.

5. Treat publishing FI as a wheel as deferred follow-up, not PR #25 scope. The
   user prefers a private hydrosolutions index, even though PyPI is now
   technically viable.

6. Prefer rebasing PR #25 onto `main` after PR #29 lands, or onto whatever
   `main` is at execution time if PR #29 has not landed. `gh pr view 29` on
   2026-06-24 reported it as mergeable and green at head
   `c7b5ba8e6393967db9755361b9405d9648df496a`, with the Docker base bump to
   `python:3.14.6-slim`. That ordering validates FI `v0.1.17` in the Python
   3.14 Docker builder path instead of retrofitting the check after PR #25.
   The `wheel-only-guard` job itself runs Python 3.12; Python 3.14 affects only
   the `build-image-and-scan` Docker path.

7. Follow repository planning and exit-gate conventions: `docs/workflow.md`
   requires plans to be phased tasks with scope and verification at lines
   14-27, and the orchestrator exit gate includes task verification, ruff,
   pyright, pytest, and affected docs at lines 96-104.

## Goal

Get PR #25 CI green and merge-ready with the existing FI git pin, while keeping
the wheel-only guard effective for every package except the approved temporary
`forecastinterface` source-build exception.

## Non-goals

- No changes to FI adapter code, the FI contract, model behavior, or the
  m3/s unit-standardization work already on the PR branch.
- No internalizing ForecastInterface into `sapphire_flow`.
- No global weakening of the wheel-only guard.
- No publishing FI as a wheel in this PR; that is deferred follow-up.
- No human merge action by a subagent. Moving the PR out of draft is allowed
  after verification; the final merge remains a human gate.

## Tasks

1. Rebase PR #25 on current `main` and resolve the known conflict surface.

   **Scope in:** Update `feat/forecast-interface-adherence` with `main`; resolve
   `pyproject.toml`, `src/sapphire_flow/__init__.py`, and `uv.lock`; keep the FI
   dependency and `[tool.uv.sources]` from the branch; keep main's newer version
   and dependency bumps. The PR branch is a single squashed commit over `main`,
   not a long series, so the expected rebase conflict surface is just
   `src/sapphire_flow/__init__.py`, `uv.lock`, and the cryptography/version
   lines in `pyproject.toml`. Branch evidence: FI dependency and source are at
   branch `pyproject.toml:20` and `pyproject.toml:80`; branch version is
   `0.1.484` at branch `src/sapphire_flow/__init__.py:1`; local `main` version
   is `0.1.493` at `src/sapphire_flow/__init__.py:1`. The real reconciled
   deltas are cryptography (`cryptography>=46.0.7` at branch
   `pyproject.toml:15` to `cryptography>=48.0.1` at local `main`
   `pyproject.toml:15`) and the version string. There is no `uv_build` delta to
   take from local `main`: both the PR branch and local `main` already have
   `uv_build>=0.11.8,<0.12.0` at `pyproject.toml:50` on the branch and
   `pyproject.toml:49` on local `main`.

   **Scope out:** Do not hand-merge `uv.lock`; do not modify source/test code
   outside conflict resolution.

   **Verification:** `uv lock`; confirm `uv.lock` still records
   `forecastinterface` from
   `https://github.com/hydrosolutions/ForecastInterface.git?rev=v0.1.17#303aa422da45e293070ef1522251c782bbbf2b7b`;
   `git diff --name-only --diff-filter=U` returns no files.

2. Implement the scoped FI wheel-guard exception in CI.

   **Scope in:** Edit `.github/workflows/ci.yml` only in the `wheel-only-guard`
   job. Replace the single guard run at current `ci.yml:96` with the two-step
   mechanism from Decisions item 3 as one `run: |` block with
   `set -euo pipefail`, step 1 first, and the inline comment from Decision 3.
   Update nearby comments at current `ci.yml:64-81` to name `forecastinterface`
   as the temporary CI exception.

   **Scope out:** Do not remove `--no-build` from the non-FI guard step; do not
   add tokens/secrets for GitHub clone access; do not change unrelated CI jobs.

   **Verification:** In a clean environment on the branch, run:

   ```bash
   uv sync --frozen --no-build --no-cache --no-install-project --no-install-package forecastinterface
   uv sync --frozen --no-cache --no-install-project --reinstall-package forecastinterface
   uv pip check
   ```

   Inspect verbose/local or CI output enough to confirm the second command is
   installing/building only `forecastinterface`.

3. Document the temporary exception in supply-chain docs.

   **Scope in:** Update `docs/standards/security.md` in the wheel-only guard
   section at current lines 385-391. Document what is allowed, why it is
   allowed, why it is temporary, and the exact removal trigger. Update
   `docs/standards/cicd.md` at the CI command table around current line 346;
   the workflow is changing from one command to two, so this update is
   mandatory. Both `docs/standards/security.md` and `docs/standards/cicd.md`
   must label step 1 as "the wheel-only guard" and step 2 as
   "post-guard temporary forecastinterface exception install," preserving the
   Plan 064 property that the guard command stays in sync across
   `.github/workflows/ci.yml`, `docs/standards/security.md`, and
   `docs/standards/cicd.md`.

   **Scope out:** Do not change Plan 064 history beyond cross-referencing this
   plan; do not document FI wheel publishing as already complete.

   **Verification:** `uv run ruff check src/ tests/`; manually confirm
   `docs/standards/security.md` names the removal trigger: FI published as a
   versioned wheel and SAPPHIRE Flow migrated from the git pin to
   `forecastinterface==0.1.x`.

4. Run local repository gates after the rebase and CI/doc edits.

   **Scope in:** Run the standard exit-gate commands required by
   `docs/workflow.md:96-104`, plus the local reproduction of the wheel guard.

   **Scope out:** Do not fix unrelated pre-existing failures unless they are
   caused by this plan's changes.

   **Verification:**

   ```bash
   uv run ruff check src/ tests/
   uv run ruff format --check src/ tests/
   uv run pyright src/
   uv run pytest
   uv sync --frozen --no-build --no-cache --no-install-project --no-install-package forecastinterface
   uv sync --frozen --no-cache --no-install-project --reinstall-package forecastinterface
   ```

   In a scratch/disposable venv and temp project copy, deliberately trip the
   guard by temporarily adding a known sdist-only requirement such as
   `docopt==0.6.2`, then run step 1 and confirm it fails before discarding the
   temp copy. Do not mutate the real `pyproject.toml` or `uv.lock` for this
   demonstration.

5. Verify GitHub CI and move PR #25 to merge-ready.

   **Scope in:** Push the branch; watch the PR CI run; confirm all five jobs are
   green: `lint`, `unit`, `wheel-only-guard`, `integration`, and
   `build-image-and-scan`. Confirm public clone no longer needs a token.
   Prefer executing after PR #29 lands, or rebase on whatever `main` is at
   execution time. If PR #29 has merged first, explicitly confirm the Docker
   builder path builds FI under Python 3.14; the wheel-only-guard job still
   runs Python 3.12.

   **Scope out:** Do not merge PR #25 automatically; do not bypass failing
   checks.

   **Verification:**

   ```bash
   gh pr checks 25 --watch
   gh pr view 25 --json isDraft,mergeable,statusCheckRollup,reviewDecision
   ```

   Exit when `isDraft=false`, all five checks are green, and the PR is ready
   for human merge.

6. Apply repository commit conventions on the PR branch.

   **Scope in:** Before committing implementation changes, honor
   `CLAUDE.md:74-85` by using `uv` only, and honor `docs/workflow.md` commit
   conventions by making a patch version bump for the commit and tagging after
   commit. Use a conventional commit message, likely
   `fix(ci): allow temporary ForecastInterface source build`.

   **Scope out:** Do not squash unrelated branch work; do not include the draft
   plan file unless the user explicitly wants the plan committed.

   **Verification:**

   ```bash
   uv run bump-my-version bump patch
   uv sync
   git status --short
   git tag --points-at HEAD
   ```

## Deferred follow-up

Publish ForecastInterface as a versioned wheel to a private hydrosolutions
package index, then migrate SAPPHIRE Flow from the git source pin to
`forecastinterface==0.1.x`. The wheel should ship the FI conformance suite or
the agreed installable test artifact. In the same follow-up, remove the
`forecastinterface` source-build exception from `.github/workflows/ci.yml`,
`docs/standards/security.md`, and `docs/standards/cicd.md`.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1-rebase",
      "tasks": ["1"],
      "parallel": false
    },
    {
      "id": "phase-2-exception-and-docs",
      "tasks": ["2", "3"],
      "parallel": true,
      "depends_on": ["phase-1-rebase"]
    },
    {
      "id": "phase-3-local-verification",
      "tasks": ["4"],
      "parallel": false,
      "depends_on": ["phase-2-exception-and-docs"]
    },
    {
      "id": "phase-4-ci-and-pr",
      "tasks": ["5", "6"],
      "parallel": false,
      "depends_on": ["phase-3-local-verification"]
    }
  ]
}
```

## Affected Files

- `.github/workflows/ci.yml`
- `pyproject.toml`
- `src/sapphire_flow/__init__.py`
- `uv.lock`

## Docs To Update

- `docs/standards/security.md`
- `docs/standards/cicd.md`
- Optional implementation note in PR #25 body/checklist: name this plan and
  the removal trigger for the temporary exception.

## Evidence Snapshot

- `gh pr view 25 --json ...` on 2026-06-24: draft, conflicting, head
  `55ba1894810e23902e1627243f8b66e96e9ed9c9`, five failing CI jobs from run
  `27847286573`.
- `gh pr view 29 --json ...` on 2026-06-24: mergeable, green, head
  `c7b5ba8e6393967db9755361b9405d9648df496a`, Docker base bump to
  `python:3.14.6-slim`.
- Current `uv --version`: `uv 0.11.7 (9d177269e 2026-04-15
  aarch64-apple-darwin)`.
- `uv sync --help` for `uv 0.11.7`: `--no-build` means "Don't build source
  distributions"; `--no-build-package <package>` means "Don't build source
  distributions for a specific package"; `--no-install-package <package>` means
  "Do not install the given package(s)"; `--reinstall-package <package>`
  reinstalls a specific package.
