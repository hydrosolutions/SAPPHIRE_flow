---
status: READY
created: 2026-09-04
revised: 2026-09-05
plan: 241
title: Consume the horizon declaration we asked FI for — the adapter currently drops it
scope: Make the model's own AT_MOST/min_future_steps declaration reach resolve_required_steps. It dies at the FI adapter boundary today, so rung 1 can never fire on ANY FI version. Bump the coupled aquacast+FI pins, propagate the field through ForecastInterfaceAdapter into ModelDataRequirements, then retire the interim provider table. T4/T5 then make a one-step forecast — which T2/T3 newly make REACHABLE — actually correct to store and to pool. NO cmal_small onboarding here.
depends_on: []
blocks: []
source: Measured 2026-09-04 against aquacast main (5460f898), the pinned revision (1937794c), and an independent review that found the adapter gap
---

# Plan 241 — consume the horizon declaration (the adapter drops it today)

## Status

**READY (T1-T3).** Owner confirmed 2026-09-04, after an independent cross-check found the adapter
gap. T1-T3 are implemented and green.

**T4/T5 added 2026-09-05, AWAITING THE OWNER'S READY.** They exist because an independent review of
the implemented T1-T3 diff returned NO — not safe to open as a PR — on defects this plan makes
live. T4 is partly implemented already (the migration, the store change and the validation, plus a
constraint-parity fix); its backfill and its tests are NOT yet what this revision requires. T5 is
not implemented at all. See the proportionality note for why they belong here rather than in a
follow-on.

## ⛔ Proportionality

**Five tasks, strictly sequential.** Do not add: `cmal_small` onboarding (a separate plan this
unblocks), other dependency bumps that happen to be available, or any change to what
`ModelDataRequirements` means beyond carrying the declared fields.

### 🔴 Why this grew from three tasks to five — read before judging the scope

The original three tasks were correct and are unchanged. T4 and T5 were added on 2026-09-05
because **T2/T3 make a one-step forecast reachable for the first time**, and two defects that were
harmless while every forecast had >= 2 steps become live the moment it is:

- the store never persisted the ensemble's cadence, it INFERRED it from the gap between
  timestamps and fabricated one hour when there was only one (T4);
- the pooled-forecast persistence boundary silently DISCARDS any single-timestamp result (T5).

Landing T1-T3 alone would therefore ship a known-live defect: a one-step forecast would be stored
with a fabricated hourly cadence, and its pooled counterpart would vanish with only a warning.
**That is the argument for extending this plan rather than deferring** — the defects are made live
by this plan, so they belong to it. Both were found by independent review of the T1-T3 diff, not
by scope drift.

⛔ This is the ONLY sanctioned extension. Anything else found along the way gets its own plan.

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

### T4 — persist the ensemble cadence instead of inferring it

**Outcome:** a forecast's cadence is STORED with it, and a one-step forecast round-trips carrying
its true step rather than a fabricated hour.

**The defect.** `PgForecastStore` never persisted `ForecastEnsemble.time_step` — a value known at
construction time — and derived it on read from the gap between `valid_time`s, defaulting to
`timedelta(hours=1)` when there was only one step. A one-step DAILY forecast round-tripped as
HOURLY, and the API and Forecast Lab published the fabricated cadence as truth. Latent while the
multi-step floor guaranteed >= 2 timestamps; live as soon as T2/T3 land.

**This is the SAME defect Plan 228 fixed for hindcasts in revision `0050`** — which fixed
`hindcast_forecasts` only and left the operational `forecasts` table untouched. T4 mirrors it.

**In:** a new `alembic/versions/0053_forecasts_time_step.py` (`time_step_seconds`, Integer, NOT
NULL, positive check constraint); `db/metadata.py` (the SAME constraint, declared table-level and
NAMED — see below); `store/forecast_store.py` (writer persists the ensemble's own step; reader
takes the column as authoritative and the gap-inference is DELETED, not merely guarded);
`types/model.py` (`ModelDataRequirements.__post_init__` rejects an incoherent horizon declaration —
a semantics value other than `exact`/`at_most`, a floor without `at_most`, or a floor < 1;
`resolve_required_steps` treats any non-boolean int as a floor, so a `0` would quietly have meant
"require nothing").

**⛔ THE BACKFILL MUST BE COMPUTED PER ROW, NOT A CONSTANT.** The first implementation stamped
every existing row `86400` on the reasoning that all six operational models are daily-stepped.
**That was asserted from the model list, never measured** — the staging host is off-LAN and the
one query that would settle it could not be run. Meanwhile a non-daily forecast is a fully
supported and heavily exercised shape in this repo (the shared ensemble fixture in
`tests/conftest.py` is HOURLY; ~95 non-daily `time_step` constructions across the suite), and
`time_step` originates from `var_output.metadata.timedelta` with FI's `dynamic` dict keyed by
timedelta — so nothing structurally forbids a sub-daily operational model.

Derive each row's step from the actual spacing of its own `forecast_values.valid_time`s. Fall back
to the `86400` server default ONLY where no delta is derivable — a single-timestamp row, which by
construction should not exist before this plan. A computed backfill is correct whether or not
non-daily rows exist, and removes the dependency on a measurement that cannot be taken.

🔴 **Record, do not fix:** `0050` used the same constant for `hindcast_forecasts`. If non-daily
hindcast rows exist, `0050` already mislabelled them. That is a separate plan.

🔴 **Migration/metadata parity is part of the task, not an afterthought.** `metadata.py` must
declare the check constraint table-level and NAMED, matching what `0053` creates and what
`0050`/`hindcast_forecasts` already does. There is no `naming_convention` on this `MetaData`, so an
unnamed column-level constraint emits an anonymous `CHECK` that Postgres auto-names
`forecasts_time_step_seconds_check` — whereupon the migration's own downgrade, which drops by the
explicit name, FAILS against any `create_all`-built schema, and autogenerate sees a permanent
phantom diff. **This is the drift class revision `0051` exists to repair.**

**Out:** the pooled persistence boundary (T5); any change to what a cadence MEANS; fixing `0050`.

**Verification:** `uv run pytest tests/unit`, with tests that did not exist in the first
implementation — a review finding in their own right:
- a ONE-STEP forecast round-trips with its declared step (the defect itself, RED first);
- a non-daily MULTI-step forecast round-trips unchanged;
- the computed backfill assigns the true step to a pre-existing non-daily row;
- metadata/migration parity — the constraint name emitted from `metadata.py` EQUALS the one `0053`
  creates (assert on the emitted DDL, not on the source text);
- `downgrade()` runs clean.

### T5 — let a one-step pooled forecast persist

**Outcome:** a station's pooled/BMA forecast exists whenever its per-model forecasts do. Today a
single-timestamp pooled result is silently discarded with only a warning, so after T2/T3 a one-step
forecast would be stored and alerted on while its pooled counterpart vanished.

**In:** `services/forecast_combination.py`, the persistence boundary only.

**🔴 The precise finding — a first reading of it was wrong, so state it exactly.** The guard is
TWO checks with DIFFERENT justifications, and only one is obsolete:

1. `forecast_horizon_steps < _MIN_PERSISTED_TIMESTAMPS` (= 2). Its comment names its sole rationale
   as the store fabricating a one-hour step for a single-timestamp forecast. **T4 removes that
   fabrication, so this floor's stated reason is gone.**
2. `_derive_uniform_time_step(ensemble) is None` -> skip. **This one SURVIVES T4** and must be
   kept: it carries a second, independent rationale — a uniformly COARSENED intersection (every
   contributor losing the same interior timestamps) leaves `ensemble.time_step` at the ref
   contributor's stale DECLARED step. The code already repairs exactly that, one line later, with
   `replace(ensemble, time_step=derived_time_step)`.

⛔ **Deleting check 1 alone accomplishes NOTHING.** `_derive_uniform_time_step` builds its delta set
from consecutive pairs, so a single timestamp yields an EMPTY set, returns `None`, and check 2
drops the forecast regardless. Both must be handled together or the task is a no-op that looks
like a fix.

**Mechanism:** handle the single-timestamp case explicitly — persist it using `ensemble.time_step`,
the declared step carried from the ref contributor, which is the only cadence information that
exists for one timestamp and which T4 now stores faithfully. Keep refusing genuinely non-uniform
grids, and keep the coarsened-grid repair.

**⚠️ Open question, for the pre-implementation review to settle:** for a one-step pooled forecast
there is no observed spacing to validate the ref contributor's declared step against. Is persisting
it correct, or should a pooled result whose contributors DISAGREE on their declared step be refused
instead? Do not implement past this question — answer it first.

**Out:** the pooled combination maths; the skill path — `services/skill/combined_skill.py` calls
`combine_ensembles_pooled` too and a single hindcast step is normal there, so the floor belongs
ONLY at this persistence boundary and must not migrate into the shared helper.

**Verification:** `uv run pytest tests/unit` — a one-step pooled forecast is persisted with its
declared step (RED first against the current skip); a genuinely non-uniform grid is still refused;
the coarsened-grid repair still fires and still corrects the label.

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
- T4's backfill is COMPUTED per row; no constant is stamped over rows whose real cadence is
  derivable, and metadata/migration constraint-name parity is asserted on emitted DDL.
- T5 handles the single-timestamp case and the uniformity check TOGETHER; a one-step pooled
  forecast is persisted, a non-uniform grid is still refused.
- ⛔ Every claim about the live database is MEASURED or explicitly marked unmeasured. The staging
  host was off-LAN on 2026-09-05; nothing in T4 may depend on an unverified assertion about what
  rows exist.

## Dependency graph

```json
{
  "plan": 241,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": false},
    {"id": "T2", "depends_on": ["T1"], "parallel": false},
    {"id": "T3", "depends_on": ["T2"], "parallel": false},
    {"id": "T4", "depends_on": ["T3"], "parallel": false},
    {"id": "T5", "depends_on": ["T4"], "parallel": false}
  ]
}
```
