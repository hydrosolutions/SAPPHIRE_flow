# Plan 112 — Reimplement vision-build (WF2) as a repo-level workflow

**Status:** DRAFT (stub — tracking only; do not start until scheduled)
**Type:** Tooling / workflow (dev-experience; no application code)
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-10
**Priority:** Medium (quality-of-workflow; not on the v1 Nepal critical path)
**Relates to:** `docs/workflow.md` § Post-WF2 adversarial review rounds; `.claude/workflows/plan-review.js` (#69); `.claude/workflow-capabilities.json`

## Why

WF2 (`vision-build`) is currently a **built-in** workflow, so its
implementer↔reviewer quality-gate loop cannot be edited in-repo. Two consequences
surfaced on the first WF2 run (Plan 105, 2026-07-10):

1. Its built-in quality gate is **necessary but not sufficient** — a green suite
   plus one review pass hid 3 real correctness blockers that a *manual* second
   adversarial Codex round caught. Today we compensate with the **manual**
   "Post-WF2 adversarial review rounds" convention (`docs/workflow.md`).
2. Its loop policy (rounds, convergence, escalation) is fixed. WF1
   (`plan-review`) was already updated to **loop-until-converge + escalate-after-5**
   (#69); WF2 should match, but can't until it's a repo file.

## Goal

Reimplement `vision-build` as `.claude/workflows/vision-build.js` (mirroring the
built-in), with the quality gate changed to:

- an **adversarial review → repair loop that runs UNTIL the implementer and
  reviewers converge** (no blockers/majors) — cross-vendor (strong Claude +
  `codex exec review` on the committed diff);
- **hard-max 5 rounds**, then **escalate loudly** (`escalated`/`escalationReason`)
  instead of merging — same policy as `plan-review.js`;
- a **test-soundness check** baked in (a fixed blocker's locking test must FAIL
  against the pre-fix code — green ≠ correct);
- everything else (planner↔reviewer step planning, locked-test authoring, the
  manifest-driven regression/acceptance/conformance gates, hold-at-PR) preserved.

## Open questions (resolve at grill-me, before starting)

- How much of the built-in `vision-build` must be reproduced vs. can we wrap it?
- Where does the locked-test-authoring soundness gate live, and can it be made
  robust to signature-changing milestones (the Plan 105 failure mode)?
- Manifest (`.claude/workflow-capabilities.json`) changes needed?

## Non-goals

- Not on the v1 Nepal critical path (Plan 106). Schedule opportunistically.
- Until this ships, the **manual** post-WF2 adversarial-rounds convention in
  `docs/workflow.md` is the standing mitigation.
