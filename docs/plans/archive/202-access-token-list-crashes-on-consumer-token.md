---
status: COMPLETE
created: 2026-08-27
plan: 202
title: `access_tokens list` crashes on any consumer token
scope: One format-string fix in one CLI print helper, plus the regression test that would have caught it. No behaviour change to token creation, verification, scoping or the API.
depends_on: []
blocks: []
source: Found 2026-08-27 while minting the Plan 198 consumer token for SAPPHIRE-flow-map on the mac mini
---

# Plan 202 — `access_tokens list` crashes on any consumer token

## Status

**COMPLETE.** Shipped in PR #225, merged to `main` as `f8ba52ad` (v0.1.830 — the
branch bumped to 0.1.826, but a concurrent session's higher version won the merge;
0.1.826 is one of the expected gaps in the tag series).

Outcome against the acceptance criteria:

1-3. Met by `!s:36` at `cli/access_tokens.py:191`.
4. The test was written first and proven RED against the unfixed code, failing with
   the real `TypeError: unsupported format string passed to UUID.__format__` rather
   than a fixture error. It was **additionally** proven to reject a `str()` fix that
   drops the `:36` — that variant passes on the consumer row (a UUID is already 36
   chars) while silently misaligning admin rows, so criterion 3 is genuinely locked.
5. Local: 5337 passed / 0 failed across `tests/unit` + `tests/integration`, ruff
   clean, pyright ratchet 404 <= baseline 432. CI: all 7 checks green.

Two findings from the independent review proved out in practice:

- **`uv.lock` is a third generated bump output.** It did change on the bump; staging
  only the two files the plan first named would have failed CI on a dirty lock.
- **No sibling defect exists.** Every width-formatted f-string field in `src/` was
  enumerated: six total, of which four are ints (`month`, `year`) and two are
  strings. This bug class lives in exactly one place, now fixed.

The post-implementation `codex exec --sandbox read-only` pass over the committed
diff returned **NONE — no findings**, having independently reproduced the
`TypeError` and confirmed the width-lock rather than agreeing by inspection.

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

> **Owner directive, 2026-08-28: do not over-engineer this.**
> Recorded here, in the plan doc, because per-run scope passed as a workflow argument is silently
> discarded — the workflow reads only `planPath`, `repo`, `maxRounds` and `codexTimeoutMs`
> (`.claude/workflows/plan.js`). Every reviewer and every revision round is bound by what follows.

**This is a one-line fix and one test.** It is a genuine crash on `main`, not a design question.
The entire *behavioural* change is a **one-character** `!s` conversion in one f-string, plus a
regression test that fails without it. The commit additionally carries the standard generated
version-bump files — **three of them, not two**: `pyproject.toml`, `src/sapphire_flow/__init__.py`
**and `uv.lock`**, because `[tool.bumpversion] pre_commit_hooks = ["uv lock"]`
(`pyproject.toml:148`) re-resolves the lockfile, whose `sapphire-flow` entry carries the version.
**Stage all three.** Leaving `uv.lock` out re-creates the version drift that was just repaired, and
leaves a dirty tree that the next `uv run` hook trips over. Mandatory for every code commit, and
not a widening of scope.

**This plan closed review at 153 lines** — 129 at round 1, then two folds: `file:line` grounding
and the mandated review gates (round 1), and the two verified findings below (round 2). **Still one
task, one file, one test.** The ceiling exists to stop *scope* growth, not to forbid corrections,
and no round added a task, a module, a decision table or a second concern. Deleting stays a valid
outcome; adding scope does not.

### Rules binding every reviewer

- **"No findings" is a complete and welcome review** — and for a change this size it is the most
  likely correct answer. Do not manufacture findings to justify the pass.
- **A finding must name a CONCRETE FAILURE** — an input, a state, and the wrong output or crash.
  "Consider also…", "while we're here…", "future-proof by…" are not findings.
- **Explicitly out of scope, do not propose:** reworking the CLI's output format; a `--json` or
  `--format` mode; pagination; colourisation; touching `create` / `create-admin` / `revoke`; the
  `AccessToken` type; anything in `api/security.py` or the auth model; a migration; new tooling.
- **Do not widen the fix.** Other `f"{...:N}"` uses elsewhere in the repo are not this plan's
  business unless a reviewer can show one crashes today.
- **The measured facts are settled**: the crash was reproduced locally and on the mac mini
  2026-08-27. Challenge them only with contrary evidence, not reasoning.

## The defect

`src/sapphire_flow/cli/access_tokens.py:191` formats the tenant with a width specifier:

```python
f"tenant={t.tenant_id or '-':36}  {status:8}  "
```

`t.tenant_id` is a `TenantId` (a `UUID`). **`UUID` implements no `__format__`**, so any non-empty
format spec raises. Reproduced:

```
>>> f"tenant={uuid4() or '-':36}"
TypeError: unsupported format string passed to UUID.__format__
```

Latent since Plan 147 Slice C because **the only token on the mini was an admin one**: `tenant_id`
is `None`, so `or '-'` yields a `str`, which accepts `:36`. The first consumer token exposed it.

## Why it matters

`list` is the **only** way to see what tokens exist, who they belong to, whether they are disabled
and when they expire. It is the CLI's audit surface.

Rows print one at a time inside the loop (`cli/access_tokens.py:252`), ordered by `created_at`
(`store/access_token_store.py:121-123`), so the first consumer token raises and **that row and every
row after it** is lost; earlier rows stay on screen. That is what makes it dangerous — the output
still looks like a listing, but it is truncated, so a **complete audit is impossible**. On the mini
today, `access_tokens list` shows one row and then a traceback.

**Verified 2026-08-27 on the mac mini** (`sapphire-flow:0.1.806`): minting the `sapphire-flow-map`
consumer token broke `list` immediately. Creation, verification and scoping are unaffected.

## Fix

```python
f"tenant={t.tenant_id or '-'!s:36}  {status:8}  "
```

**One character: `!s`.** The conversion applies `str()` to whichever value `or` already selected —
the `UUID` or the `'-'` — *before* the width spec is applied, so the existing idiom is preserved
**exactly**, not merely in spirit.

*(An earlier draft used a ternary with an explicit `str()`. Also correct, but a bigger diff solving
a `str(None)` problem `!s` never creates. Verified byte-identical for both cases. The independent
review caught that this plan's own "do not widen the fix" rule applied to the fix itself.)*

**Scope (out):** the output format itself, a `--json` mode, `create`/`revoke`/`create-admin`, the
`AccessToken` type, and anything in `api/security.py`.

## Task

**T1 — Fix the format and lock it.**
*Scope (in):* the one-line change at `cli/access_tokens.py:191`, plus **one** regression test
function that calls `_print_token_row` twice — once with a consumer token whose `tenant_id` is a real
`TenantId`, once with an admin token (`tenant_id is None`) — and asserts the exact captured
`tenant=…` → `status` segment for both, admin padding included. Asserting only "the UUID appears"
would not lock acceptance 2-3: a `str(...)` fix that also drops the `:36` passes it (a UUID is
already 36 chars) while silently misaligning admin rows. The test must **fail against the current
code** — that is the whole point, since the existing tests pass while the bug is live.
*Scope (out):* everything above.
*Exit:* `uv run pytest tests/unit/cli/test_access_tokens.py -q` green, and demonstrably RED when the
`str()` is reverted.

## Acceptance criteria

1. `_print_token_row` renders a token with a UUID `tenant_id` without raising.
2. It still renders an admin token (`tenant_id is None`) as `-`, unchanged.
3. The tenant column stays width-aligned for both cases.
4. The new test fails against the unfixed code (proven by reverting, not asserted).
5. `uv run pytest`, `ruff check`, `ruff format --check`, `pyright` ratchet all pass.

## Why the existing tests missed it

The obvious explanation ("no consumer fixture") is **wrong**. Consumer rows *are* covered:
`tests/integration/cli/test_access_tokens_cli.py:73-84` creates a CONSUMER token with a real tenant
and retrieves it through `list_tokens`. The gap is the *renderer*:
that test imports only the persistence helpers (`:12`), and the unit `list` tests stop at the
fail-closed pepper gate (`tests/unit/cli/test_access_tokens.py:34-37`), so `_print_token_row` is
never called by any test. **Coverage of the data path is not coverage of the display path.**

## Deployment

None. The fix ships with the next image; the mini is on `0.1.806` and `list` stays broken until
then. **Workaround meanwhile:** query the DB directly —
`select id, name, role, tenant_id, disabled_at, expires_at from access_tokens;`

## Review

Small, but **not trivial** under `docs/workflow.md:102-107` (the exemption is typos, comments,
single-line log text, mechanical no-behaviour edits — this changes behaviour), and it touches the
access-token CLI, which `docs/workflow.md:156-161` names as high-risk auth surface. So the required
gates apply and this plan does not get to opt out of them:

- **Before READY:** the `plan` workflow (Claude design + a real independent Codex pass every round).
- **After the owner approves:** the `implement` workflow (red-first locking test → independent
  verify → Codex diff-review rounds), hold-at-PR.

This is a *review* obligation, not licence to grow the change: the proportionality constraint above
still binds every round, and "no findings" remains the most likely correct outcome.
