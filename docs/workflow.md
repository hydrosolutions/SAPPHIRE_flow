# SAPPHIRE Flow — Workflow Conventions

## Orchestration Protocol

**The orchestrator (Opus) NEVER writes code directly.**

1. **Explore** the codebase before each phase to gather context for agent prompts
2. **Use `plan`** for the bounded read-only plan review and owner confirmation
3. **Use `implement`** for READY plans; one Sonnet implementer executes the
   dependency graph in order
4. **Verify** every declared task and exit gate independently on the committed candidate
5. **Review** once with separate Claude and Codex perspectives
6. **Repair** at most once, and only after owner dispositions; then hold at PR

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

Interface details (types, Protocols, signatures) belong in implementation-level
plans only, not high-level plans. The subagent reads the codebase and docs.

Plans end with a JSON dependency graph:

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1a", "1b", "1c"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["2a", "2b"],
      "parallel": true,
      "depends_on": ["phase-1"]
    }
  ]
}
```

The dependency graph records ordering. The default `implement` workflow keeps
one implementer responsible for the whole graph; direct/manual work may delegate
independent tasks in parallel when the graph explicitly permits it.

## Preserve Existing Logic

**Do not break pre-existing data flows, code logic, or documented workflows without
extremely good reason.** The architecture and flow designs represent deliberate decisions.

- Before changing any existing behavior, verify it is genuinely wrong — not just
  unfamiliar or different from what you would have chosen.
- If you believe existing logic or a documented workflow must change, **stop and discuss
  with the user first.** Present the evidence for why the change is necessary.
- Refactors that preserve behavior are fine. Changes that alter behavior require explicit
  approval.

## Plan Readiness

- The author creates a `DRAFT` plan.
- Planning and independent-review agents may read a DRAFT plan, but implementation
  agents may not execute it.
- The owner decides how review findings are resolved in the DRAFT.
- The required confirming review runs after those decisions are folded in.
- Only the owner may set its YAML `status: READY`.

Reviewer recommendations are advisory and never change plan status. Plans 232
and 233 own the executable planning and implementation workflow mechanics.

### Plan status vocabulary

YAML `status:` frontmatter is the only machine-readable status source for active
plans in `docs/plans/`. The canonical active statuses are `DRAFT`, `READY`,
`BLOCKED`, `DEFERRED`, `PARTIAL`, `SUPERSEDED`, and `COMPLETE`:

- **DRAFT** — plan is being written or has not yet been confirmed by the user.
  It may be planned and reviewed, but it is not ready for implementation.
- **READY** — plan is confirmed and ready to execute. Subagents may be
  dispatched.
- **BLOCKED** — execution cannot proceed until a named blocker changes.
- **DEFERRED** — scope-validated, intentionally postponed to a future version
  (v0b, v1, etc.).
- **PARTIAL** — some work landed; remaining work needs renewed owner approval.
- **SUPERSEDED** — another named authority replaces the plan.
- **COMPLETE** — the plan is fully implemented; archive it when references allow.

Do not use `IN_PROGRESS` or `DONE` as active-plan statuses; execution progress
belongs to the branch, run, or PR. `ARCHIVED` is a location, not an active status.
A missing active YAML status is reported as `NONE`, never inferred from body
prose. Historical files already in `docs/plans/archive/` retain their legacy
labels.

## Multi-Model Review

Multi-model review is **mandatory for all non-trivial plans and patches** before
the relevant owner gate. A model may not approve its own output.

**Trivial exemption** (single-perspective self-check is enough) applies *only* to:

- typos
- comments / docstrings
- single-line log text
- mechanical, no-behavior-change edits

When in doubt, treat the work as non-trivial.

### Tasks are the single completion ledger

Every non-trivial plan task contains:

- **Outcome:** one observable behavior or artifact.
- **In / Out:** bounded files and explicit exclusions.
- **Verification:** an exact test node, command, or bounded `file:property`
  inspection.
- **Pre-change:** for changed behavior, how the same evidence fails before the
  change and why. Use `N/A` only for documentation, mechanical, or gate tasks,
  with the reason stated.

Task IDs are the stable keys used by review and implementation. Account for
every task. Do not add a second acceptance ledger, persistent run file, or
parallel completion vocabulary.

### Context packet

Before review, the orchestrator builds one factual context packet and gives the
same packet to both reviewers. It points to canonical sources rather than
duplicating them.

Minimum fields:

- plan path, YAML status, and whole-document fingerprint;
- task IDs, Pre-change modes, and Pre-change/Verification fingerprints;
- exit-gate IDs and fingerprints;
- reviewed `HEAD`;
- applicable authoritative document paths.

The packet is produced by `scripts/check_readiness.py --inspect-json`, is capped at
8,192 UTF-8 bytes, and never contains copied plan text or restated task contracts.
Reviewers and implementers read the plan path directly. Agent command evidence carries
the matching fingerprint, numeric exit, and one output excerpt of at most 1,000
characters. Inspection accepts any structurally valid canonical status, including DRAFT;
the unchanged default checker remains the separate READY-only implementation gate.

Missing or contradictory required context makes the review incomplete.

### Touchpoint maps

Reusable per-subsystem **routing checklists** — the concrete touchpoints, contracts,
and verification a context packet points into when a task touches a given subsystem —
live in **`docs/touchpoint-maps.md`**. Consult the relevant map when building the
context packet. Current maps: **ForecastInterface / model execution**; **Forecast
cycle / assignment selection**; **Persistence / API write path**; **Prefect / Docker /
deployment**; **Training / hindcast / skill**; **Alerting / alert-state**. Their
governance — the right-sizing fitness test — is below (see
Right-sizing).

### Plan review

The `plan` workflow is read-only and has two modes:

Every invocation explicitly classifies `riskClass` as `ordinary` or `high`;
there is no silent default. Confirmation is bound to the prior plan path, mode,
and risk class.

- `review` runs one Claude design/proportionality review and one real Codex
  repo-grounded review in parallel. Both reports are returned separately and the
  result binds them to the reviewed document fingerprint. Task and gate identities
  must exactly match the validated manifest. A missing reviewer yields
  `REVIEW_INCOMPLETE`.
- `confirm` receives the prior packet and owner dispositions. Because the compact
  packet deliberately carries no prior plan text, the same two perspectives
  review the complete current plan once while rechecking prior findings and
  dispositions. The prior and current fingerprints remain separate evidence
  anchors; neither is presented as a text delta.

Each finding has a stable per-report ID, severity, exact location, violated
contract, and smallest sufficient correction. A blocker or major is admissible
only when the plan is unsafe, contradictory, unexecutable, incomplete against
its stated scope, or guarded by evidence that would not discriminate against
the current repository. Reviewer preferences and desirable follow-ons are not
blocking. Reports are never flattened or silently reconciled by another model.

The owner disposes every blocker and major as one of:

- `fix` — amend the plan;
- `reject` — the finding is wrong, with rationale;
- `follow-up` — genuine but out of scope, with rationale;
- `accept-risk` — explicitly accept an in-scope concern, with rationale.

Only the owner may accept risk. An accepted finding is matched by ID, location,
and violated contract, so reusing an ID cannot hide a new concern. Accepted risks remain visible. The workflow
returns advisory `READY`, `NOT_READY`, or `REVIEW_INCOMPLETE`; only the owner
changes YAML status. Initial `review` mode never recommends READY: it returns
`CONFIRM_REQUIRED` when clean, or asks for owner dispositions when blocked.
Only a complete `confirm` pass may advise READY.

### High-risk review

The ordinary Claude+Codex pair is the default floor. Security/auth,
container/privilege or secrets handling, data-loss or migration risk,
external-facing contract or API changes, live-DB impact, Prefect scheduling,
Docker entrypoints, FI contract boundaries, user-visible behavior, and anything
the owner flags as high-risk require an additional independently commissioned
panel. Its result is supplied separately; the ordinary pair cannot waive it.
Ordinary work does not gain reviewers autonomously.

### Right-sizing (guard against over-engineering)

The Claude review includes proportionality: flag gold-plating, speculative
generality, unnecessary phases or abstractions, and reference detail that should
remain in code. Prefer the smallest correction that satisfies the task contract.
For detail-bearing artifacts, use one subtractive right-sizing pass before READY.

**Fitness test — state what the artifact is FOR, then keep only what serves it.** You
cannot judge "too much detail" without it. Example (routing / touchpoint map): every
bullet names a symbol/subsystem to go read; no bullet teaches how the code works; a
"must not change silently" contract covers only a **surprising, high-consequence,
cross-cutting** invariant — a localized fact the named symbol already reveals is not a
contract.

This guard is subject to the trivial exemption: process weight must remain
proportional to the risk it removes.

### Evidence-led implementation

An implementer's “done” claim is evidence, not approval. Every READY-plan task
receives independent evidence and no task may be selected as merely “key.”

Legacy READY plans are migrated only when selected for implementation: set the
plan to DRAFT, normalize its existing work into the task fields and single Exit
gates fence above without expanding scope, run `plan` review, and obtain fresh
owner confirmation. `implement` returns `PLAN_CONTRACT_REQUIRED` when a selected
READY plan lacks that structure. This keeps old plans visible without weakening
evidence requirements or forcing a bulk rewrite.

The `implement` workflow has two modes:

Every invocation explicitly classifies `riskClass` as `ordinary` or `high`;
there is no silent default.

- `build` uses one Sonnet implementer for the whole dependency graph. Behavioral
  tasks report the declared pre-change fingerprint, its expected non-zero exit,
  one bounded output excerpt, and separate `beforeEvidence` and `afterEvidence`. Any task
  declaring a real pre-change check must show its expected failure; an explained
  `N/A` is reported as not applicable. Evidence already green for behavior
  claimed missing makes the plan `PLAN_INCOMPLETE`.
  The implementer runs targeted checks, updates docs, bumps the patch version,
  and commits without tagging.
- A read-only verifier then checks commit advancement, non-empty base diff,
  clean worktree, version bump, scope, every task ID, and all plan gates. Task
  and gate inventories come from the validated manifest and must exactly match
  the evidence. Each result carries the declared fingerprint, numeric exit,
  and one output excerpt of at most 1,000 characters. The verifier also proves the READY plan did not change and
  reruns the deterministic readiness check. It owns one complete exit-gate run
  per committed candidate. Each task is `PASS`, `FAIL`, or `UNVERIFIABLE`;
  anything but complete evidence stops before review.

Implementation starts only on a named feature branch, never `main` or the base
branch. The branch must remain unchanged, a compact tag count and deterministic
tag-set fingerprint must remain equal to their preflight values, and the remote
feature-branch SHA must not move before `PR_READY`; the workflow never tags or
pushes and never transports the full tag list through agent responses.

After verification, one Claude design/proportionality reviewer and one real
Codex repo-grounded reviewer inspect the committed diff in parallel. Reports
remain separate. A missing reviewer makes engineering review incomplete. A
blocker or major returns `OWNER_DECISION_REQUIRED` without changing the patch;
the owner uses the same `fix`, `reject`, `follow-up`, or `accept-risk`
dispositions as plan review.

`confirm` permits one fixer for only owner-disposed `fix` findings, followed by
one independent full verification and one delta-only Claude+Codex confirmation.
There is no second automated repair pass. Minors and follow-ups are never
auto-fixed. Confirmation is bound to the prior mode, plan path, base branch,
reviewed plan/commit, and explicit risk class. High-risk work still requires its
separate independent panel.

Terminal fields are deliberately small:

- `taskCompletion`: `COMPLETE`, `INCOMPLETE`, or `UNVERIFIABLE`;
- `engineeringReview`: `PASS`, `BLOCKED`, or `INCOMPLETE`;
- `recommendation`: `PR_READY` or `NOT_READY`.

Return one canonical copy of task evidence, gate evidence, separate parsed reviews,
deviations, and accepted risks beside those fields. Do not return plan snapshots,
task contracts, full raw command output, Codex raw verdict text, or nested copies of
implementer/fixer/verifier reports. The Codex report retains its exit code and a
`rawVerdictPresent` boolean. No “pass with notes” may hide an incomplete task.

### Human authority

- The **human owner is the terminal authority.**
- The **human approves READY** before implementation.
- The **human approves the PR** before merge.
- **Codex writes code only from a human-approved READY plan.**
- **No actor except the human merges.**
- `PR_READY` never authorizes merge, deployment, operational rollout, or
  scientific adoption. Those remain human decisions.

The first later owner-selected READY implementation using the rewritten workflow
is the behavioral adoption check. Its PR records that every task received
evidence, complete gates ran once per candidate, findings waited for owner
disposition, and no second repair loop occurred. Do not create a test-only build
or permanent run ledger for this check.

### Context maintenance

Context surfaced mid-task must be **applied, deferred with a reason, or tracked**
— never silently dropped.

### Tooling

This section is the **policy**; it stands on its own and holds even when run by
hand. The repo also ships machinery that executes parts of it. Each stage maps to
a tool as follows:

| Policy stage | Tool | Where it lives |
|---|---|---|
| Plan review and confirmation — **default** | **`plan` workflow** — one read-only Claude+Codex review, owner dispositions, and at most one complete confirmation review | `.claude/workflows/plan.js` |
| Interactive plan stress-test / surface design forks | `grill-me` skill | `.claude/skills/grill-me/` |
| READY-plan implementation — **default** | **`implement` workflow** — every task evidenced, one independent full verification per candidate, one Claude+Codex review, owner disposition, and at most one confirmation repair; hold-at-PR | `.claude/workflows/implement.js` |
| Vision → ordered, human-approved milestone list (WF1) | `vision-decompose` skill | skill |
| Manifest-driven milestone build w/ auto-test-authoring (WF2 — **separate track, currently unused**; see Plan 112) | `vision-build` skill | skill, driven by `.claude/workflow-capabilities.json` |
| Task Exit Gate / acceptance gates for WF2 | gate manifest | `.claude/workflow-capabilities.json` (mirrors `.github/workflows/ci.yml`) |

Notes:

- **The policy is not auto-enforced.** No hook blocks a commit or PR for skipping
  multi-model review — the tools above run it, but the orchestrator is
  responsible for invoking them.
- **`plan` / `implement` are the default Codex-backed workflows.** They shell
  out to Codex, so they require **`codex exec --sandbox
  read-only`** to be permitted in the local allowlist (`.claude/settings.local.json`) —
  they hang or are denied without it.
- **Codex is invoked through `scripts/codex-review.sh`, never a hand-rolled
  `codex exec`.** The script closes stdin so a completed review cannot hang until
  timeout. The workflow requires a zero numeric exit and non-empty raw verdict;
  a non-zero exit or unusable output makes the reviewer incomplete.
  The relay writes its prompt to a deterministic, content-fingerprinted, shell-safe
  workflow-specific, gitignored `sapphire-flow-*-codex-review.md` path at the
  worktree root via the file-write tool. Shared permissions allow only that file pattern and the two
  exact relay command prefixes. The relay reads the prompt once and rejects empty
  content or the fixed cleared sentinel before invoking Codex,
  so concurrent identical prompts may safely share a path. It does not use shell heredocs or redirects, which the
  Claude sandbox cannot analyze reliably.
  - A missing or failed Codex report yields `REVIEW_INCOMPLETE` for planning or
    `engineeringReview: INCOMPLETE` for implementation; it is never clean.
  - A manual pass is `./scripts/codex-review.sh <prompt-file>`; a non-zero exit means
    no usable verdict. Scope the prompt — a broad one over a long document can still
    exceed the 10-minute cap.
  - Do **not** treat the string `Reading additional input from stdin` in the output as
    proof of a hang: Codex prints it during normal startup, reads EOF, and proceeds.
    Guarding on it false-positives on every successful review.
- **WF2 (`vision-build`) — first run 2026-07-10 (Plan 105).** It BLOCKED at the
  locked-test-authoring soundness gate (twice): the auto-author kept writing
  tests against the changing `_fetch_nwp_task` signature that *errored* instead
  of failing RED. We pivoted to a **conventional build** — author-controlled
  locked tests + a delegated implementation + an independent patch review
  (which caught 3 blockers). Lesson: for signature-changing work,
  the auto-authored locked tests may not converge; be ready to author them by
  hand and always run the standard independent patch review. Confirm the manifest's
  gate commands locally before a launch (see the manifest's own `_comment`).
  Adoption stance is manual-deploy-first, then WF2 fix-mode on confirmed bugs,
  **hold-at-PR — never auto-merge**.
- **Staleness gate (Plan 200).** Both `plan` and `implement` fail closed before
  work and before a terminal recommendation when the plan's authoritative
  `main`/`origin/main` history moved beyond the branch copy. A failed fetch or git
  check also fails closed. A branch merely behind `origin/main` warns and proceeds.
  A commit-time
  nudge (`unpushed-main-nudge`, `.pre-commit-config.yaml`, `post-commit` stage) separately
  warns — never blocks — when local `main` carries commits `origin/main` doesn't have yet;
  silent on every other branch. See `docs/plans/archive/200-workflows-refuse-a-stale-spec.md`.

## Task Exit Gate

For manual/direct work, the orchestrator verifies:

1. Task's verification command passes
2. `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean
3. `uv run pyright src/` — no type errors in changed modules
4. `uv run pytest` — all tests pass
5. Affected docs updated in the same change

Inside `implement`, the implementer runs task-targeted checks only. The
independent verifier runs every task's exact verification and the plan Exit gates
once on each committed candidate.

## Documentation Hygiene

1. **Every code change updates affected docs.** No stale docs.
2. **Single source of truth.** Each concept defined in one place, others reference it.
3. **No TODO/FIXME without a corresponding open question.**

## Commit Conventions

[Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description
```

**Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
**Scope**: module name — `feat(types): add domain enums`, `test(qc): add range check tests`

Every code commit includes a patch version bump. Never create a tag on a feature branch.
On pushes to `main`, `.github/workflows/tag-main.yml` creates the version tag when absent.
