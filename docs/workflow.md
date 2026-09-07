# SAPPHIRE Flow — Workflow Conventions

## Working Protocol

1. One agent owns each planning or implementation pass end to end.
2. Use `/plan` to review or refine a plan with the owner.
3. Only the owner decides whether the plan becomes READY.
4. Use `/implement` to build one READY plan and run its focused checks.
5. When the owner requests review, use the review prompt in independent Claude and
   Codex sessions separately.
6. The owner resolves findings. Nothing automatically retries, fixes, opens a PR,
   merges, tags, or deploys.

## Plan Structure

Plans are organized as **phases** containing **tasks**. Each task is one bounded
unit in the implementation ledger. Under `## Tasks`, write each task heading as
`### <stable-id> — <name>`; IDs such as `T1`, `1a`, and `A0` are valid.

Each task specifies:

1. **Outcome** — one observable behavior or artifact
2. **In / Out** — bounded files and explicit exclusions
3. **Verification** — exact command, test node, or bounded inspection
4. **Pre-change** — discriminating RED evidence for behavioral changes, or an
   explained `N/A` for mechanical/documentation work

Interface details belong in implementation-level plans only when they constrain
interoperability. Agents read the repository rather than receiving copied source
or duplicated task summaries.

Plans end with a small JSON dependency graph:

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1a", "1b"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["2a"],
      "depends_on": ["phase-1"]
    }
  ]
}
```

The graph records ordering. The `/implement` prompt keeps one agent responsible for
the whole graph unless the owner explicitly requests delegation.

## Preserve Existing Logic

**Do not break pre-existing data flows, code logic, or documented workflows without
extremely good reason.** The architecture and flow designs represent deliberate decisions.

- Before changing behavior, verify it is genuinely wrong rather than unfamiliar.
- If existing behavior must change beyond an approved plan, stop and discuss the
  evidence with the user.
- Refactors that preserve behavior are fine. Behavior changes require explicit approval.

## Plan Readiness

- The author creates a `DRAFT` plan.
- Planning and independent-review agents may read a DRAFT plan, but implementation
  agents may not execute it.
- The owner decides how review findings are handled.
- If those decisions materially change the plan, review the complete current plan
  again; there is no separate confirmation mode.
- Only the owner may set its YAML `status: READY`.

Reviewer reports are advisory. A prompt never changes plan status or declares a
plan READY.

### Plan status vocabulary

YAML `status:` frontmatter is the only machine-readable status source for active
plans in `docs/plans/`. The canonical active statuses are `DRAFT`, `READY`,
`BLOCKED`, `DEFERRED`, `PARTIAL`, `SUPERSEDED`, and `COMPLETE`:

- **DRAFT** — being written or awaiting owner confirmation.
- **READY** — owner-confirmed and available for implementation.
- **BLOCKED** — cannot proceed until a named blocker changes.
- **DEFERRED** — intentionally postponed to a later version.
- **PARTIAL** — some work landed; remaining work needs renewed owner approval.
- **SUPERSEDED** — another named authority replaces the plan.
- **COMPLETE** — fully implemented; archive it when references allow.

Do not use `IN_PROGRESS` or `DONE` as active-plan statuses; execution progress
belongs to the branch, run, or PR. `ARCHIVED` is a location, not an active status.
A missing active YAML status is reported as `NONE`, never inferred from body
prose. Historical files already in `docs/plans/archive/` retain their legacy labels.

## Multi-Model Review

Multi-model review is mandatory for every non-trivial plan and patch. A model
may not approve its own output. The trivial exemption is limited to typos,
comments or docstrings, single-line log text, and mechanical edits with no
behavior change. When in doubt, treat the work as non-trivial.

### One completion ledger

Plan tasks are the only completion ledger. Do not create a second acceptance map,
context packet, persistent run file, or evidence schema. Reviewers and implementers
read the current plan and repository directly. They account for every task in
short prose and cite `file:line` when reporting a defect.

### Plan review

The ordinary review is one Claude design/proportionality pass and one independent
Codex repository-grounded pass. The owner starts them separately with `/review`;
neither prompt launches the other. Both review the complete current plan.

Reports contain findings, not plan summaries. Each finding states severity, its
location, the violated requirement, and the smallest sufficient correction.
Reviewers should flag genuine gaps and over-engineering, not alternative designs,
speculative hardening, or unrelated follow-ups.

Reviewers do not reconcile findings, rewrite the plan, recommend READY, or start
another round. The owner resolves findings. Review the complete plan again only
after a material change.

### Implementation and patch review

One agent owns the whole READY plan. Before editing it checks the
exact YAML READY status, a clean named feature branch, and once that the plan was
not superseded or changed upstream. It implements every task, runs each task's
verification, updates affected documentation, bumps the patch version for code,
and commits. It stops instead of claiming completion when a task or check remains
unresolved.

After implementation, the owner separately requests one independent Claude review
and one independent Codex review. Each reads the complete plan and base-branch diff,
then reports concrete findings without editing. The owner decides whether to request
a scoped correction, accept a risk, or open a PR.

### High-risk work

Security/auth, container privilege or secrets handling, data-loss or migration
risk, external-facing contracts or APIs, live-database impact, Prefect scheduling,
Docker entrypoints, ForecastInterface boundaries, user-visible behavior, scientific
behavior with material operational consequences, and anything the owner flags are
high risk. In addition to the ordinary pair, the owner commissions one relevant
independent review before setting a plan READY and again before opening its
implementation PR. The prompts do not build or manage a panel.

### Hard boundaries

- Implementation requires exact leading YAML `status: READY`.
- Implementation starts from a clean named feature branch, never `main`.
- Plan freshness is checked against a freshly fetched `origin/main` once before
  edits; a failed fetch or uncertainty stops the run.
- Every code commit includes its patch version bump.
- An already-satisfied plan must not produce an empty commit.
- Prompts never push, open or merge a PR, tag, deploy, or adopt scientific output.
- A missing or unfinished required review is incomplete, never clean.

### Right-sizing

- Prefer the smallest change that satisfies the plan.
- Do not add abstractions, frameworks, fallback modes, or hardening without a
  stated requirement.
- Do not duplicate plan text in prompts or reports.
- Do not turn reviewer judgment into scoring, fingerprints, or state algebra.
- Do not run automatic fix/review loops. The owner chooses any next step.

### Human authority

Only the human owner sets READY, accepts review findings or risk, authorizes PR
creation, and merges. A review report is evidence for that decision, not the
decision itself.

### Context maintenance

Context surfaced mid-task must be applied, deferred with a reason, or tracked —
never silently dropped.

### Prompt Guides

The repository ships three plain Markdown skills:

```text
/plan docs/plans/NNN-name.md
/implement docs/plans/NNN-name.md
/review docs/plans/NNN-name.md
```

- `/plan` reviews or refines a plan with the owner.
- `/implement` builds one READY plan and stops after focused verification.
- `/review` performs one independent, read-only plan or patch review.

The skills do not call each other, launch agents, retry, reconcile findings, or
manage state. Use the same review instructions deliberately in separate Claude and
Codex sessions when multi-model review is required.

## Task Exit Gate

For manual/direct work, the implementing agent verifies:

1. Task verification commands pass
2. `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean
3. `uv run pyright src/` reports no type errors in changed modules
4. Affected docs are updated in the same change

During `/implement`, run each task's targeted checks and the plan's focused Exit
gates. Do not repeatedly run the repository-wide suite during review iterations.
After the final code change and before merge, `uv run pytest` must pass locally or
in CI.

## Documentation Hygiene

1. **Every code change updates affected docs.** No stale docs.
2. **Single source of truth.** Each concept is defined once; other docs reference it.
3. **No TODO/FIXME without a corresponding open question.**

## Commit Conventions

[Conventional Commits](https://www.conventionalcommits.org/):

```text
type(scope): description
```

**Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

**Scope**: module name — `feat(types): add domain enums`, `test(qc): add range check tests`

Every code commit includes a patch version bump. Never create a tag on a feature branch.
On pushes to `main`, `.github/workflows/tag-main.yml` creates the version tag when absent.
