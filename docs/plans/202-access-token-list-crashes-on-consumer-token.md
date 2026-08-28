---
status: DRAFT
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

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

> **Owner directive, 2026-08-28: do not over-engineer this.**
> Recorded here, in the plan doc, because per-run scope passed as a workflow argument is silently
> discarded — the workflow reads only `planPath`, `repo`, `maxRounds` and `codexTimeoutMs`
> (`.claude/workflows/plan.js`). Every reviewer and every revision round is bound by what follows.

**This is a one-line fix and one test.** It is a genuine crash on `main`, not a design question.
The whole change is `str()` around one value in one f-string, plus a regression test that fails
without it.

**This plan should LEAVE review at or below its current length (~110 lines).** A round that adds a
task, a module, a decision table, or a second concern has misread the assignment. Deleting is a
valid outcome; growing is not.

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

It has been latent since Plan 147 Slice C because **the only token on the mini was an admin one**,
and an admin token has `tenant_id = None` — so the `or '-'` branch yields a `str`, which accepts
`:36`. The first consumer token ever minted on that box exposed it.

## Why it matters

`list` is the **only** way to see what tokens exist, who they belong to, whether they are disabled
and when they expire. It is the CLI's audit surface.

The failure mode is worse than losing one row: the exception aborts the whole listing, so a single
consumer token makes **every** token invisible, admin ones included. On the mini today,
`access_tokens list` shows one row and then a traceback. An operator auditing access — or trying to
find a token to revoke — sees a crash.

**Verified 2026-08-27 on the mac mini** (`sapphire-flow:0.1.806`): minting the `sapphire-flow-map`
consumer token broke `list` immediately. Creation, verification and scoping are all unaffected —
the new token authenticates and is correctly refused from admin-only routes.

## Fix

```python
f"tenant={str(t.tenant_id) if t.tenant_id else '-':36}  {status:8}  "
```

`str()` before the width spec. Note the `or '-'` idiom is retained in spirit but made explicit —
`str(None)` would print `"None"`, so the conditional is required, not cosmetic.

**Scope (out):** the output format itself, a `--json` mode, `create`/`revoke`/`create-admin`, the
`AccessToken` type, and anything in `api/security.py`.

## Task

**T1 — Fix the format and lock it.**
*Scope (in):* the one-line change at `cli/access_tokens.py:191`, plus a regression test that calls
`_print_token_row` with a token whose `tenant_id` is a real `TenantId` and asserts it does not raise
and that the tenant appears in the output. The test must **fail against the current code** — that is
the whole point, since the existing tests pass while the bug is live.
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

Worth one line, because it is the reusable lesson: the CLI's tests cover token **creation and
verification** — the security-critical paths Plan 147 was written for — and exercise `list` only
with admin tokens, where `tenant_id is None` and the bug is unreachable. **A fixture that never
populates an optional field cannot test the field.** The same shape was found twice in Plan 198's
acceptance suite (an observation filter untested because every fixture row already satisfied it).

## Deployment

None. The fix ships with the next image; the mini is on `0.1.806` and `list` stays broken until
then. **Workaround meanwhile:** query the DB directly —
`select id, name, role, tenant_id, disabled_at, expires_at from access_tokens;`

## Review

Trivial-adjacent but a real crash on a security-surface CLI. One independent `codex exec` pass over
the diff is enough; this does not need the `plan` workflow.
