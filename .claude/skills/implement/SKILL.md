---
name: implement
description: Implement one owner-approved SAPPHIRE Flow plan without automated orchestration. Use when the user supplies a READY plan and asks to build it.
---

Use the plan path supplied by the user. One agent owns the complete implementation.

1. Run `uv run python scripts/check_readiness.py <plan-path>`. Stop if it is not
   READY. Require a clean named feature branch, run `git fetch origin main` once, and stop
   if the plan is stale or superseded.
2. Implement only the plan. Do not split work across agents or add abstractions,
   fallbacks, hardening, or follow-ups that the plan does not require.
3. Run the task checks and focused lint/type checks, update affected documentation,
   and include the required patch version bump in the code commit.
4. Report changed files, checks run, and any unresolved gap concisely.

Do not launch reviewers, retry through findings, push, open or merge a PR, tag, or
deploy. Do not run the repository-wide suite here; it is a separate mandatory gate
after the final code change and before merge.
