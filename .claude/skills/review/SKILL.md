---
name: review
description: Independently review a SAPPHIRE Flow plan or implementation. Use when the owner deliberately requests a Claude or Codex review pass.
---

Review the plan or implementation named by the user. Do not edit files or call
another reviewer.

- For a plan, verify its assumptions, scope, dependencies, tasks, exclusions, and
  focused checks against the relevant repository files.
- For an implementation, read the complete READY plan and inspect
  `origin/main...HEAD`. Verify every task, correctness, regressions, test gaps,
  documentation drift, scope creep, and over-engineering.
- Report only concrete findings, ordered by severity. Give `file:line` or task,
  the violated requirement, and the smallest fix. State any check you could not
  complete.

Do not summarize the source, propose unrelated improvements, fix findings, or start
another review. Do not rerun the repository-wide suite unless the owner explicitly
asks; use the recorded final-suite result. If there are no findings, say `CLEAN` and
stop.
