---
status: DRAFT
created: 2026-09-04
revised: 2026-09-04
plan: 241
title: Consume the horizon declaration we asked FI for — the adapter currently drops it
scope: Make the model's own AT_MOST/min_future_steps declaration reach resolve_required_steps. It dies at the FI adapter boundary today, so rung 1 can never fire on ANY FI version. Bump the coupled aquacast+FI pins, propagate the field through ForecastInterfaceAdapter into ModelDataRequirements, then retire the interim provider table. NO cmal_small onboarding here.
depends_on: []
blocks: []
source: Measured 2026-09-04 against aquacast main (5460f898), the pinned revision (1937794c), and an independent review that found the adapter gap
---

# Plan 241 — consume the horizon declaration (the adapter drops it today)

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality

**Three tasks, strictly sequential.** Do not add: `cmal_small` onboarding (a separate plan this
unblocks), other dependency bumps that happen to be available, or any change to what
`ModelDataRequirements` means beyond carrying the declared fields.

## The correction that reshaped this plan

An earlier revision said the work was "bump two pins". **That was wrong**, and an independent review
found why: even with the pins moved, the declaration never reaches the code that would use it.

- `_model_declared_floor` reads `getattr(model, "input_requirement", None)`
  (`services/horizon_semantics.py`), and `model` there is the **`ForecastInterfaceAdapter`**, not the
  FI model.
- The adapter consumes `fi_model.input_requirement` internally
  (`adapters/forecast_interface.py:474`) and **never re-exposes it**.
- What it exposes is `ModelDataRequirements` (`types/model.py:275-291`), which carries
  `forecast_horizon_steps: int` and **no horizon-semantics fields at all**.

**So rung 1 cannot fire for any FI-discovered model, on any FI version.** The interim
`HORIZON_CEILING_FLOORS` table is not scaffolding awaiting a version bump — it compensates for a gap
on **our** side of the adapter.

The shim is fine: `_canonical_requirement` preserves every non-name/unit field via
`model_copy(update={"unit": ...})` (`models/aquacast/_shim.py:156-204`). The field survives the shim
and dies one layer later.

**There is a precedent for the fix.** `declared_aggregations` was added to `ModelDataRequirements`
for exactly this shape of problem — a per-variable FI field the adapter was dropping
(`adapters/forecast_interface.py:715`, Plan 228). T2 follows it.

## What is upstream, and what it does and does not decide

aquacast `main` (`5460f898`), checked against the private remote 2026-09-04 — **reported, not
repo-verifiable**: `pyproject.toml:84` pins FI **v0.1.20**, and
`aquacast/operational/requirement.py:215-231` declares
`horizon_semantics = AT_MOST if relaxable else EXACT` with
`min_future_steps = 1 if relaxable else None`, where `relaxable = horizon_fixed_reason(cfg, cfg.model)
is None`.

This is **exactly the contract we asked for** in `docs/fi-issues/002-future-steps-at-most-semantics.md`
— same names, same variable-level placement, `EXACT` default preserved, and the declaration derived
from `_relax_horizon`, the model-side knowledge our issue said was "invisible to consumers".

### 🔑 OWNER DECISION 2026-09-04 — we take the CAPABILITY floor

Our FI issue's property 2 argued `min_future_steps` mattered because "a 15-day model may be useless
at 1 day". aquacast declares **1**, and says plainly this is the **architectural** floor — what the
weights can serve — and that "whether a 1-step lead is worth ACTING on is the consumer's call".

**We asked for a usefulness floor and received a capability floor, and aquacast is right.** A model
serving many consumers cannot know whether a short lead is actionable; that depends on the station,
the decision and the operator. The judgement is ours and was never going to arrive from upstream.

**Decision: adopt the capability floor as declared.** No additional operational minimum is layered on
in this plan. `resolve_required_steps` takes `min(model_floor, declared_steps)`, so a model declaring
`min_future_steps=1` will run on as little as one step of forcing rather than be refused.

⚠️ **Consequence, accepted knowingly.** Today `cmal_pool_pt` is held at 5 steps by the interim table;
after T2+T3 it runs on whatever is available, down to 1. A very short forecast will be **produced and
stored** where one is currently **refused**. Nothing downstream imposes a larger floor — coverage
accepts a single clean row (`services/nwp_coverage.py`), and forecast construction, QC and alert
eligibility impose no horizon minimum. **Whether a 1-step forecast should be published or alerted on
is a separate question this plan does not answer**; if it needs answering it belongs to its own plan,
not to a floor smuggled in here.

## Tasks

### T1 — bump the coupled pins
**Outcome:** `uv lock` resolves on aquacast `5460f898` + FI `v0.1.20`, the version gate passes, and
nothing regresses. Horizon behaviour is UNCHANGED by this task — the adapter still drops the field —
which is why it is safe to land first.
**In:** `pyproject.toml` `[tool.uv.sources]` (aquacast `1937794c` -> `5460f898`, forecastinterface
`v0.1.19` -> `v0.1.20`), `uv.lock`, and `adapters/forecast_interface.py:88`
(`SUPPORTED_FI_VERSION` -> `"0.1.20"`; it hard-fails on mismatch, `:144-155`).
⛔ Both sources in ONE change. Measured: moving FI alone fails — `Failed to resolve dependencies for
aquacast ... conflicting URLs for forecastinterface: v0.1.19 / v0.1.20` — because the pinned aquacast
revision pins v0.1.19 while main pins v0.1.20.
**Out:** any behaviour change; the adapter; the interim table; other dependency bumps.
**Pre-change:** aquacast `1937794c`, FI `v0.1.19`, `SUPPORTED_FI_VERSION = "0.1.19"`; FI v0.1.19 has
no `horizon_semantics`/`min_future_steps`/`AT_MOST` (verified by importing the installed package).
**Verification:** `uv run pytest tests/unit` AND `uv run pytest tests/integration` green, plus
`uv run python -c "import forecast_interface as fi; assert fi.__version__ == '0.1.20'"`. The bump
carries every other upstream change between the two revisions, so the FULL suite is the gate, not a
targeted subset. Re-read the `rich>=15.0.0` workaround (`pyproject.toml:89-98`) in case the newer
revision relaxes it.

### T2 — propagate the declaration through the adapter
**Outcome:** `resolve_required_steps` sees a model's own `AT_MOST`/`min_future_steps`, so rung 1 fires
for an FI model that declares it — which it cannot do today on any FI version.
**In:** `adapters/forecast_interface.py` (capture the declared semantics while projecting
`fi_model.input_requirement`, as `declared_aggregations` already does at `:715`); `types/model.py`
(`ModelDataRequirements` gains the declared horizon semantics — keep the dataclass **hashable**;
`declared_aggregations` uses a `frozenset` of pairs rather than a `dict` for exactly that reason);
`services/horizon_semantics.py` (`_model_declared_floor` reads the projected field, not only
`input_requirement`, so it works for adapter-wrapped and native models alike).
Read defensively: a model declaring nothing must yield "not declared", never a crash — this runs
inside the forecast cycle.
**Out:** changing rung 2 or rung 3; deleting the table (T3); any new operational floor (see the owner
decision); `cmal_small`.
**Pre-change:** `_model_declared_floor` returns `None` for every FI-discovered model, because the
adapter exposes no `input_requirement` and `ModelDataRequirements` has no horizon fields.
**Verification:** `uv run pytest tests/unit` — with a test building an adapter over a fake FI model
declaring `AT_MOST`/`min_future_steps` and asserting `resolve_required_steps` returns
`source="model_at_most"`. ⛔ RED first, failing because the field does not propagate — **not** because
a symbol is missing. Companion tests: a model declaring `EXACT` (expect `source="declared"`) and one
declaring nothing (expect the provider/strict path unchanged).

### T3 — retire the interim provider table, once rung 1 demonstrably fires
**Outcome:** `HORIZON_CEILING_FLOORS` and the provider-opt-in rung are deleted, or this plan records
the measured reason they must stay.
**In:** `types/ids.py:69` (one entry today, `cmal_pool_pt: 5`), `services/horizon_semantics.py`
(rung 2 and the module's interim framing), callers in `services/run_station_forecast.py:255` and
`services/run_group_forecast.py`.
⛔ Gate the deletion on the `horizon.provider_ceiling_opt_in` WARNING ceasing to fire for
`cmal_pool_pt` — the module names that as its own retirement signal.
🔴 A previous revision justified keeping the table by saying an `EXACT` model still needs it. **That
is backwards**: `EXACT` short-circuits *before* the table is consulted. The real hazard is the
opposite — deleting the table while the declaration still does not reach the resolver drops
`cmal_pool_pt` from its 5-step opt-in to its strict declared horizon (~15), breaking a model that
works today. Hence T3 depends on T2, not merely on T1.
**Out:** rung 1, rung 3, floor values, `cmal_small`.
**Pre-change:** rung 2 fires for `cmal_pool_pt` and logs at WARNING on every use.
**Verification:** `uv run pytest tests/unit` — plus a recorded measurement that `cmal_pool_pt`'s
discovered requirement resolves via `source="model_at_most"`. If it resolves `EXACT` or undeclared,
the deletion does NOT happen and the finding is recorded here instead.

## Also in scope — one documentation fix

`docs/fi-issues/002-future-steps-at-most-semantics.md` must record its own resolution: fixed in FI
v0.1.20, adopted by aquacast, **and** that `min_future_steps` turned out to be a *capability* floor
rather than the *usefulness* floor its property 2 assumed. Without that line the next reader
re-derives the whole distinction. This loop stayed open for weeks partly because the issue carried no
"resolved in vX, adopt by doing Y" marker.

## Separately worth knowing (not in scope)

- **`cmal_small` onboarding is what this unblocks**: config + shim subclass, artifact import from the
  owner's model tree, Flow 12 registration. Its 78 declared statics already resolve 78/78
  (`docs/reference/cmal-small-static-features.md`, PR #252).
- Bumping aquacast carries every upstream change between `1937794c` and `5460f898`, not only the
  horizon work. T1's full-suite gate is the check; the diff is worth reading.
- Whether a very short forecast should be **published or alerted on** is deliberately unanswered here.

## Exit gates

```bash
uv lock
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
uv run python -c "import forecast_interface as fi; assert fi.__version__ == '0.1.20', fi.__version__"
```

- T2's RED-first tests are proven red against the pre-change code, and red for the propagation
  failure rather than a missing symbol.
- `uv run pyright` no worse than the recorded ratchet baseline.
- T3 either deletes the interim table or records the measured reason it stays.
- `docs/fi-issues/002` records its resolution and the capability-vs-usefulness distinction.

## Dependency graph

```json
{
  "plan": 241,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false},
    {"id": "T2", "depends_on": ["T1"], "parallel": false},
    {"id": "T3", "depends_on": ["T2"], "parallel": false}
  ]
}
```
