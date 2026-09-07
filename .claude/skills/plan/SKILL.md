---
name: plan
description: Review or refine a SAPPHIRE Flow plan with the owner. Use when drafting a plan, checking whether it is ready, or folding accepted review findings into it.
---

Use the plan path supplied by the user.

1. Read the plan, `docs/v0-scope.md`, `docs/workflow.md`, and only the repository
   files needed to verify the proposal.
2. Check the plan number, assumptions, scope, dependencies, tasks, exclusions, and
   focused verification. Prefer the smallest complete design; reject speculative
   hardening and new machinery without a requirement.
3. Report only concrete findings, ordered by severity. Give the location, violated
   requirement, and smallest correction. Do not repeat the plan.
4. Discuss findings with the owner. Edit only the corrections the owner accepts.

Do not implement code, set the plan to READY, launch reviewers, or start another
review round automatically. If no finding remains, say `CLEAN` and stop.
