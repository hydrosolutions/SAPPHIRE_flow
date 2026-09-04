---
status: DRAFT
created: 2026-09-04
plan: 241
title: Adopt aquacast's declared horizon semantics — bump both pins together, then delete the interim scaffolding
scope: Move the aquacast and ForecastInterface pins forward TOGETHER (they are coupled), bump SUPPORTED_FI_VERSION, and retire HORIZON_CEILING_FLOORS + services/horizon_semantics.py once the models declare AT_MOST themselves. Unblocks running a 10-day-trained model on our 5-day forcing. NO cmal_small onboarding here.
depends_on: []
blocks: []
source: Measured 2026-09-04 against aquacast main (5460f898) and the pinned revision (1937794c)
---

# Plan 241 — adopt the declared horizon semantics

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality

**Two tasks.** Do not add: `cmal_small` onboarding (its config, shim subclass, artifact import and
Flow 12 registration are a separate plan that this one unblocks), a horizon *policy* change beyond
the one owner decision below, or any other dependency bump that happens to be available.

## What is measured

**The upstream work is already done.** aquacast `main` (`5460f898`) — checked directly against the
remote 2026-09-04:

| fact | evidence |
|---|---|
| aquacast pins **FI v0.1.20** | its `pyproject.toml:84` |
| models **declare** the semantics | `aquacast/operational/requirement.py:215-231` |
| `relaxable = horizon_fixed_reason(cfg, cfg.model) is None` | `:215` |
| `horizon_semantics = AT_MOST if relaxable else EXACT` | `:215` |
| `min_future_steps = 1 if relaxable else None` | `:231` |
| tests assert both branches | `tests/operational/test_requirement.py:329-333` |

**We are pinned behind it:** aquacast `1937794c` (v0.1.343) and FI `v0.1.19`.

### The two pins are COUPLED — bump them together or not at all

Moving FI alone **fails to resolve**, measured:

```
× Failed to resolve dependencies for `aquacast` (v0.1.343)
  ╰─▶ Requirements contain conflicting URLs for package `forecastinterface`:
      - git+.../ForecastInterface@v0.1.19   (aquacast 1937794c pins this)
      - git+.../ForecastInterface@v0.1.20   (our proposed pin)
```

The old aquacast revision pins FI v0.1.19; `main` pins v0.1.20. So the bump is **one atomic change
to both**, not two independent ones. An earlier attempt moved only FI and hit exactly this.

### Our shim needs NO change

`_canonical_requirement` delegates to aquacast's own `input_requirement` and translates only
names/units via `variable.model_copy(update={"unit": ...})`
(`models/aquacast/_shim.py:156-173`). Pydantic's `model_copy` preserves every other field, so
`horizon_semantics` and `min_future_steps` propagate **automatically** once the pins move. This was
checked rather than assumed; it is the reason T1 is small.

### 🔴 What Sandro's change does NOT decide — and hands back to us

`min_future_steps=1` is the **architectural** floor. aquacast's own comment is explicit:

> "``_relax_horizon`` accepts any ``1 <= horizon < full``, so 1 is the ARCHITECTURAL floor, and that
> is the whole of what this field declares: what the weights can serve. **Whether a 1-step lead is
> worth ACTING on is the consumer's call** — it depends on the station, the decision the forecast
> feeds and the lead time the operator needs, none of which the checkpoint knows."

So "is a 10-day-trained model sound at 5 days?" is **answered architecturally and NOT answered
operationally**. Earlier notes in this repo treated the horizon question as blocked on the modeller;
that was only half right, and the remaining half is ours.

**This has a concrete consequence.** `resolve_required_steps` computes
`steps = min(model_floor, declared_steps)` (`services/horizon_semantics.py`). With `model_floor = 1`,
`required_steps` becomes **1** — i.e. after the bump we would accept a run with a **single step** of
forcing, where today a strict provider refuses anything short. That is a real behaviour change and
it is why the owner question below is not optional.

## Tasks

### T1 — bump both pins together, and confirm the declaration arrives
**Outcome:** `uv lock` resolves, the FI version gate passes, and a discovered aquacast model's
`data_requirements` carries `horizon_semantics`/`min_future_steps` through our shim.
**In:** `pyproject.toml` (`[tool.uv.sources]`: aquacast `1937794c` -> `5460f898`, forecastinterface
`v0.1.19` -> `v0.1.20`), `uv.lock`, `src/sapphire_flow/adapters/forecast_interface.py`
(`SUPPORTED_FI_VERSION` `"0.1.19"` -> `"0.1.20"` — it hard-fails on any mismatch, `:88,:147-153`).
Bump both sources in ONE change; see the coupling above.
**Out:** any other dependency bump; shim changes (none are needed — verified); `cmal_small`; the
`HORIZON_CEILING_FLOORS` deletion (that is T2).
**Pre-change:** aquacast `1937794c` pins FI v0.1.19; our pin is v0.1.19; `SUPPORTED_FI_VERSION` is
`"0.1.19"`; FI v0.1.19 has no `horizon_semantics`, `min_future_steps` or `AT_MOST` (verified by
importing the installed package).
**Verification:** `uv run pytest tests/unit -q` — plus a new test asserting that for a discovered
aquacast model the shim-exposed `FutureKnownVariable` carries a non-None `horizon_semantics`,
proving the field survives `_canonical_requirement`. ⛔ RED first: it cannot pass on v0.1.19, where
the field does not exist.

### T2 — retire the interim horizon scaffolding, if and only if it is now dead
**Outcome:** `HORIZON_CEILING_FLOORS` and `services/horizon_semantics.py`'s provider-opt-in rung are
deleted, or the plan records exactly why they must stay.
**In:** `src/sapphire_flow/types/ids.py:69` (`HORIZON_CEILING_FLOORS`, currently one entry:
`cmal_pool_pt: 5`), `src/sapphire_flow/services/horizon_semantics.py`, its callers in
`services/run_station_forecast.py:255` and `services/run_group_forecast.py`.
**Both are marked DELETE-ON-ARRIVAL**; the module names its own retirement signal — the
`horizon.provider_ceiling_opt_in` WARNING ceasing to fire.
⛔ **Gate the deletion on evidence, not on the bump landing.** `relaxable` is per-architecture
(`horizon_fixed_reason`), so a model whose head bakes in the horizon still gets `EXACT` and would
still need rung 2. Establish, for `cmal_pool_pt` specifically, that it now declares `AT_MOST`. If it
declares `EXACT`, T2's outcome is to RECORD that and keep the table — deleting it would silently
re-strict a model that works today.
**Out:** removing rung 3 (strict default) or rung 1; changing any floor value; `cmal_small`.
**Pre-change:** rung 2 fires for `cmal_pool_pt` and logs at WARNING on every use.
**Verification:** `uv run pytest tests/unit -q` — and, before deleting, a test or a recorded
measurement showing `cmal_pool_pt`'s discovered requirement declares `AT_MOST`. If it declares
`EXACT`, the deletion does not happen and the finding is recorded here instead.

## Owner question — must be answered before T1 ships

**What is OUR minimum acceptable forecast lead?** aquacast declares `min_future_steps=1` and says in
terms that whether a 1-step lead is worth acting on is the consumer's call. Because
`resolve_required_steps` takes `min(model_floor, declared_steps)`, adopting the declaration as-is
means we would accept a **1-step** forecast rather than refusing a short feed.

Options: accept the architectural floor (simplest, but a 1-step flood forecast is unlikely to be
actionable); apply our own operational floor on top (e.g. the 5 days ICON-CH2-EPS gives us, which is
what the interim table encoded); or make it per-model config. This is an operational judgement, it
is **not** decided by this plan, and T1 should not ship without it.

## Separately worth knowing (not in scope)

- **`cmal_small` onboarding is the follow-on this unblocks**: its config + shim subclass, importing
  the artifact from the owner's model tree, and Flow 12 registration. Its 78 declared statics now
  resolve 78/78 (`docs/reference/cmal-small-static-features.md`, PR #252).
- Bumping aquacast also carries every other upstream change between `1937794c` and `5460f898` —
  not just the horizon work. T1's full-suite run is the check on that, and the diff is worth a look.
- `pyproject.toml:89-97` records a `rich>=15.0.0` floor workaround for aquacast; re-read it after the
  bump in case the newer revision relaxes it.

## Exit gates

```bash
uv lock
uv run pytest tests/unit
uv run ruff check src tests && uv run ruff format --check src tests
uv run python -c "import forecast_interface as fi; assert fi.__version__ == '0.1.20', fi.__version__; print('FI', fi.__version__)"
```

- The new RED-first test proves the declaration survives the shim, and is red on v0.1.19.
- `uv run pyright` no worse than the recorded ratchet baseline.
- T2 either deletes the scaffolding or records the measured reason it stays.

## Dependency graph

```json
{
  "plan": 241,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false},
    {"id": "T2", "depends_on": ["T1"], "parallel": false}
  ]
}
```
