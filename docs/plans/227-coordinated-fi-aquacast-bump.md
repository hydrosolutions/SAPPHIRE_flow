---
status: DRAFT
created: 2026-09-01
plan: 227
title: Bump ForecastInterface and aquacast together, and honour the new horizon semantics
scope: One coordinated dependency bump (FI v0.1.19 -> v0.1.20, aquacast 1937794c -> 5cbfdbb7) plus whatever the adapter needs to honour AT_MOST horizon semantics. No change to the forecast cycle, no new model, no image work.
depends_on: []
blocks: []
source: 2026-09-01 local test run — re-pinning aquacast alone fails to resolve; FI is the real coupling, not rich
---

# Plan 227 — the aquacast bump is really an FI contract bump

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

Two pinned revisions move together, and the adapter learns one new concept. **Do not** turn this
into a redesign of horizon handling — the forecast-cycle redesign
(`docs/design/forecast-cycle-redesign.md`) already owns that, and this plan must not pre-empt it.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.**
2. **A finding must name a CONCRETE FAILURE** with `file:line`.
3. **Do not propose new apparatus** — no new abstraction layer, no horizon framework.
4. **Do not propose splitting the bump.** § D1 shows the resolver forbids it.
5. **Adding length is a cost.**

## Why this is not a one-line pin change — measured, not assumed

Re-pinning aquacast alone **fails to resolve**. Reproduced locally 2026-09-01 in a throwaway
worktree:

```
× Failed to resolve dependencies for `aquacast` (v0.1.346)
╰─▶ Requirements contain conflicting URLs for package `forecastinterface`:
    - git+.../ForecastInterface@v0.1.19   <- ours
    - git+.../ForecastInterface@v0.1.20   <- required by aquacast v0.1.346
```

**The `rich` floor was NOT the blocker**, contrary to the standing worry recorded at
`pyproject.toml:89-97`. Verified at the current pin: `override-dependencies = ["rich>=14,<15"]`
resolves rich to **14.3.3**, aquacast imports fine, and none of `aquacast/__init__.py`,
`operational/horizon.py`, `operational/model.py` or `operational/requirement.py` imports rich on
`main`. That override stays and remains correct; it is not this plan's problem.

## What the contract actually changed

FI v0.1.19 → v0.1.20 is **2 commits, 9 files**: *"feat(input): declare horizon semantics on
future-known variables"*. `FutureKnownVariable` gains:

- **`HorizonSemantics`** — `EXACT` ("fewer future steps is an error") or `AT_MOST` ("fewer is
  acceptable and yields a correspondingly shorter forecast");
- **`min_future_steps: int | None`**, required for `AT_MOST` and forbidden for `EXACT`, enforced by
  a `model_validator` with `validate_assignment=True`.

**The default is `EXACT`, which is today's behaviour** — so the FI bump alone changes nothing.

## The actual risk — and it is not hypothetical

**The new aquacast emits the new semantics; our side has never heard of them.**

| where | references to `horizon_semantics` / `AT_MOST` / `min_future_steps` |
|---|---|
| `aquacast/operational/requirement.py` (new rev) | 3 |
| `aquacast/operational/horizon.py` (new rev) | 1 |
| `aquacast/operational/model.py` (new rev) | 1 |
| **`adapters/forecast_interface.py` (ours)** | **0** |
| **`models/aquacast/_shim.py` (ours)** | **0** |

So after the bump a model may legitimately declare *"I need at most N future steps, and at least
M"* while our adapter reads only `future_steps` and treats every requirement as EXACT. The likely
symptom is **spurious failure**: we refuse to run a model that would have been satisfied by a
shorter horizon — the opposite of the AT_MOST feature's intent.

## Decisions

- **D1 — The two bumps CANNOT be split.** `uv` rejects two different git URLs for one package, so
  FI v0.1.20 and aquacast `5cbfdbb7` land in the same commit or neither does. This is a resolver
  constraint, not a preference.

- **D2 — Honour `AT_MOST`, or fail loudly. Never silently treat it as `EXACT`.** Silent coercion
  is the FI-adherence violation CLAUDE.md forbids: *if our side cannot express what the contract
  needs, file an issue upstream — do not patch around it on the SAP3 side.* If honouring AT_MOST
  turns out to need more than the adapter can express, **stop and raise an FI issue** rather than
  widening this plan.

- **D3 — Test against the REAL package, not fakes.** Proven necessary today: with aquacast absent,
  `tests/unit/models/test_aquacast_shim*.py` report **21 passed / 1 skipped**; with it installed,
  **27 passed / 0 skipped**. The fake-based majority would pass through a contract change
  unchanged, so a green suite without the extra proves nothing about this bump.

- **D4 — This plan does NOT enable aquacast in the deployed image.** That works at the *current*
  pin and is separate (Plan 218's outstanding gate + the `WITH_AQUACAST=1` build). Keeping them
  apart means a failure here cannot block getting a working model onto the mini.

## Tasks

### T1 — bump both pins and make the lock resolve
*In:* `pyproject.toml` FI `v0.1.19` → `v0.1.20` and aquacast `1937794c` → `5cbfdbb7`; `uv lock`.
*Exit:* `uv lock` succeeds; `uv sync --extra aquacast` installs; `rich` still resolves to 14.x
(the override must not have been disturbed).

### T2 — teach the adapter the new semantics
*In:* whatever `adapters/forecast_interface.py` (and `_shim.py` if it translates requirements)
needs to read `horizon_semantics` and `min_future_steps` and honour AT_MOST.
*Out:* changing the forecast cycle's horizon handling · anything in the redesign's scope ·
inventing a horizon abstraction.
*Exit:* an AT_MOST requirement with `min_future_steps` is satisfied by a shorter horizon and is
**not** rejected; an EXACT requirement still fails on a short horizon exactly as today; a
red-first test proves both.

### T3 — prove it against the real package
*In:* run the `importorskip`-gated shim tests with the extra installed.
*Exit:* the previously-skipped tests run and pass (expect 27+/0 skipped, not 21/1); full
`uv run pytest` green; ruff and the pyright ratchet pass.

## Non-goals

Enabling aquacast in the image (D4) · the `rich` override (verified fine) · the forecast-cycle
horizon redesign · onboarding `cmal_small` or any model · touching the mini.

## Exit gates

- `uv lock` resolves with both pins bumped, and rich stays 14.x.
- AT_MOST is honoured, EXACT is unchanged, both locked by tests proven RED first.
- The real-package shim tests run (0 skipped) and pass.
