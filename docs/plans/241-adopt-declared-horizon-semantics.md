---
status: READY
created: 2026-09-04
revised: 2026-09-05
plan: 241
title: Consume the horizon declaration we asked FI for — the adapter currently drops it
scope: Make the model's own AT_MOST/min_future_steps declaration reach resolve_required_steps. It dies at the FI adapter boundary today, so rung 1 can never fire on ANY FI version. Bump the coupled aquacast+FI pins, propagate the field through ForecastInterfaceAdapter into ModelDataRequirements, then retire the interim provider table. T4 then makes a one-step forecast — which T2/T3 newly make REACHABLE — correct to STORE, adding forecasts.time_step_seconds nullable-first. NO cmal_small onboarding here.
depends_on: []
blocks: []
source: Measured 2026-09-04 against aquacast main (5460f898), the pinned revision (1937794c), and an independent review that found the adapter gap
---

# Plan 241 — consume the horizon declaration (the adapter drops it today)

## Status

**READY (T1-T3).** Owner confirmed 2026-09-04, after an independent cross-check found the adapter
gap. T1-T3 are implemented and green.

**T4 approved by the owner 2026-09-07 — `status: READY`.** T1-T3 landed; T4 was held at `PARTIAL`
through three review rounds and is now authorised to execute.

T4 exists because an independent review of the implemented T1-T3 diff returned NO — not safe to
open as a PR — on a defect this plan makes live. A pre-implementation review of the T4/T5 amendment
then returned NO in turn, with three blockers; this revision folds them:

- the `NOT NULL` migration violated `docs/standards/cicd.md`'s one-version compatibility rule ->
  T4 is now NULLABLE-FIRST, and needs no backfill at all;
- the computed backfill proposed to replace the unmeasured constant could not be made correct ->
  removed, with the reasoning recorded;
- a proposed T5 was unreachable from this plan -> removed;
- database claims were to be proven by a unit run -> moved to `tests/integration` against real
  Postgres.

T4 is PARTLY implemented on `feat/plan-241-horizon-semantics` in its FIRST, now-superseded shape
(`NOT NULL` + `86400` server default). ⛔ That implementation must be revised to match this
revision before it is reviewed again — the committed migration is not what this plan now asks for.

## ⛔ Proportionality

**Four tasks, strictly sequential.** Do not add: `cmal_small` onboarding (a separate plan this
unblocks), other dependency bumps that happen to be available, or any change to what
`ModelDataRequirements` means beyond carrying the declared fields.

### 🔴 Why this grew from three tasks to four — read before judging the scope

The original three tasks were correct and are unchanged. T4 was added on 2026-09-05
because **T2/T3 make a one-step forecast reachable for the first time**, and one defect that was
harmless while every forecast had >= 2 steps becomes live the moment it is: the store never
persisted the ensemble's cadence, it INFERRED it from the gap between timestamps and fabricated one
hour when there was only one (T4).

Landing T1-T3 alone would therefore ship a known-live defect — a one-step forecast stored with a
fabricated hourly cadence. **That is the argument for extending this plan rather than deferring:**
the defect is made live by this plan, so it belongs to it. It was found by independent review of
the T1-T3 diff, not by scope drift.

A proposed T5 (the pooled one-step guard) was added and then REMOVED by the next review round, which
showed it is unreachable from this plan. See the removal note after T4 — the analysis is kept, the
work is not.

⛔ T4 is the ONLY sanctioned extension. Anything else found along the way gets its own plan.

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
HOURLY, and the API and Forecast Lab published the fabricated cadence as truth.

**Pre-change (evidence, and how it fails):** construct a one-step daily `ForecastEnsemble`, store
it through `PgForecastStore`, read it back, assert `time_step == timedelta(days=1)`; the readback
returns `timedelta(hours=1)` — a fabricated value, not a missing attribute.

⛔ **The RED must be demonstrated against `main` (a20e38b3), NOT against this branch.** T4's first
shape is already committed here, so the writer already stores the step and the reader already
returns it — the test is GREEN on `feat/plan-241-horizon-semantics` and proves nothing if run in
place. Verify the red on a clean `main` checkout (or `git stash` the store change), record the
observed `1:00:00`, and only then re-apply. A green test presented as a red is exactly the trap
this repo has been bitten by before.

**This is the SAME defect Plan 228 fixed for hindcasts in revision `0050`**, which fixed
`hindcast_forecasts` only and left the operational `forecasts` table untouched.

#### 🔴 NULLABLE-FIRST — this task was redesigned after review; do not restore the first shape

The first implementation added the column `NOT NULL` with a `server_default` of `86400`, stamping
every existing row "daily". **Two independent problems, both blocking:**

1. **It violates a mandatory repo standard.** `docs/standards/cicd.md` (§Rollback) requires
   migrations to be backwards-compatible for one version — *"additive only: new columns nullable,
   no destructive changes in a single release"* — so the previous image tag can run against the new
   schema during the migration window. `station_weather_sources.role` (Plan 115a/115c) is the
   worked precedent: add nullable with a NULL-tolerant check, tighten in a LATER release.
2. **The backfill value was never measured.** `86400` was asserted from the model list. The staging
   host was off-LAN on 2026-09-05 and the settling query could not be run, while a non-daily
   forecast is a fully supported and heavily exercised shape (the shared ensemble fixture in
   `tests/conftest.py` is HOURLY; ~95 non-daily `time_step` constructions across the suite).

**A computed backfill was considered and REJECTED as unnecessary.** It cannot be made reliably
correct here anyway — `forecast_values` holds one row per member (or quantile) per `valid_time`, so
a naive gap query double-counts; an intersection may be non-uniform; and a single-timestamp row has
no derivable delta at all, so any value invented for it is fabrication in a new costume.

**Nullable-first removes the need for a backfill entirely:** leave existing rows `NULL` and let the
reader keep inferring for them; populate the column for every row written from now on; tighten in a
LATER release once the rollback window has closed.

#### 🔴 The legacy path KEEPS its fallback — do not delete it

A previous revision of this task argued that gap-inference is correct for every existing row
because *"a one-step forecast was unreachable before T2/T3"*, and on that basis deleted the
`timedelta(hours=1)` branch outright. **That premise is FALSE, and two independent reviews agree.**

Nothing at the storage boundary ever enforced a two-step minimum. `ForecastEnsemble.from_members` /
`from_quantiles` require a non-empty frame and >= 1 member and nothing more
(`types/ensemble.py`), `PgForecastStore` has no such guard on write, and in the whole pre-change
store the only `>= 2` is the reader's own inference (`main:store/forecast_store.py:330`). The
multi-step floor is a property of the RESOLVER, not an invariant of the table — so every path that
builds an ensemble directly (replay and recording tools, imports, the Forecast Lab, tests that
write to a real database, manual insertion) bypasses it entirely. The `else` branch exists because
whoever wrote it treated a one-timestamp forecast as reachable.

⛔ **Deleting that branch trades a wrong NUMBER for a failed READ** — `valid_times[1]` raises
`IndexError` on a previously readable row — and stakes it on a database that could not be queried.
That is strictly worse than the defect being fixed.

**So the reader keeps exactly the behaviour it has today whenever the column is `NULL`**, including
the one-hour fallback, now behind a WARNING log naming the forecast id. Legacy rows therefore
behave precisely as they do now — fabrication and all — while every row written after this release
carries its true cadence and never reaches that branch. No behaviour change for old data, full
correctness for new, and no crash risk resting on an unverifiable premise.

⚠️ Note this consciously leaves legacy one-timestamp and non-uniform rows reporting the same value
they report today. Correcting them is the deferred tightening's problem, not this release's, and it
needs the live-data measurement this plan could not take.

#### ⚠️ Rollback-window skew, both directions

`cicd.md`'s rule is about the OLD image running against the NEW schema, and nullable-first
satisfies it: an old writer omits the column, the row is `NULL`, and the new reader infers. **But
the reverse skew is real and must be stated:** if the deployment is rolled back, an OLD reader
ignores `time_step_seconds` entirely and re-derives from the gaps — so a one-step daily row written
by the new image reads back as hourly under the rolled-back image. Rollback is already
restore-from-backup plus the previous image tag (`docs/standards/cicd.md` §Rollback), so such a row
would normally not survive the restore; the exposure is real but bounded, and it disappears at
tightening. Recorded so nobody rediscovers it during an incident.

**In:**
- `alembic/versions/0053_forecasts_time_step.py` — `time_step_seconds`, Integer, **NULLABLE**, no
  `server_default`, with a **NULL-tolerant named** check constraint
  (`time_step_seconds IS NULL OR time_step_seconds > 0`).
- `db/metadata.py` — the same column and the same constraint, declared **table-level and NAMED**.
  🔴 There is no `naming_convention` on this `MetaData`, so an unnamed column-level constraint
  emits an anonymous `CHECK` that Postgres auto-names `forecasts_time_step_seconds_check` —
  whereupon the migration's own downgrade, which drops by the explicit name, FAILS against any
  `create_all`-built schema and autogenerate sees a permanent phantom diff. **This is the drift
  class revision `0051` exists to repair.**
- `store/forecast_store.py` — the writer persists the ensemble's own step; the reader takes the
  column as authoritative **when present**, and when it is `NULL` keeps TODAY'S behaviour verbatim
  — gap-inference, including the one-hour fallback for a single timestamp — now behind a WARNING
  log naming the forecast id. ⛔ That branch is retained, NOT deleted; see above for why.
- `types/model.py` — `ModelDataRequirements.__post_init__` rejects an incoherent horizon
  declaration (a semantics value other than `exact`/`at_most`, a floor without `at_most`, or a
  floor < 1). `resolve_required_steps` treats any non-boolean int as a floor, so a `0` would
  quietly have meant "require nothing".
  **Pre-change:** `ModelDataRequirements(declared_min_future_steps=0, ...)` constructs today and
  silently disables the floor; after the change it raises.

**Out:** any data backfill; tightening to `NOT NULL`; fixing `0050`; the pooled persistence
boundary (removed from this plan — see below).

📌 **The deferred tightening needs a named follow-on, not a promise.** The cited 115a/115c
precedent works because `115c` is a real plan with a number. This one is not yet written. It must
decide what a truthful tightening does with rows whose cadence is genuinely UNDERIVABLE — a legacy
single-timestamp row has no spacing to recover, so `NOT NULL` can only be reached by inventing a
value or by deleting/quarantining those rows. ⛔ Do not close this plan while that follow-on is
unwritten; record it as an explicit debt with the measurement it needs (the live-DB cadence
census that could not be run on 2026-09-05).

🔴 **Record, do not fix:** `0050` added `hindcast_forecasts.time_step_seconds` as `NOT NULL` with
the same unmeasured `86400` constant. It carries both defects named above — the standard violation
and the unverified stamp. Out of scope here; it needs its own plan.

**Verification.**
`uv run pytest tests/unit` — only what genuinely needs no database:
- the incoherent-declaration rejections on `ModelDataRequirements`.

`uv run pytest tests/integration` — ⛔ every claim about STORING or READING a forecast is a
database claim and belongs here, alongside the existing suite
(`tests/integration/store/test_forecast_store.py`), not in a unit run:
- a one-step daily forecast round-trips with `time_step == 1 day` (RED first against `main` — see
  Pre-change above);
- a non-daily multi-step forecast round-trips unchanged;
- a row whose column is `NULL` still infers correctly from >= 2 timestamps;
- a row whose column is `NULL` with exactly ONE timestamp still returns today's one-hour value and
  logs the warning — the regression guard for the branch this task deliberately keeps.

`uv run pytest tests/integration` for anything that is a claim about the DATABASE — ⛔ a unit run
cannot establish migration behaviour, and `tests/integration/db/test_migration_0052_partial_index.py`
is the precedent for doing this against real Postgres:
- `upgrade()` then `downgrade()` runs clean;
- the constraint NAME created by `0053` equals the one emitted from `metadata.py` — asserted on
  **emitted DDL / the live catalogue**, never on source text;
- a pre-existing row survives the upgrade with `NULL` and reads back with its inferred step.

## ⛔ REMOVED FROM THIS PLAN — the pooled one-step guard (was T5)

A previous revision added a T5 to stop `build_combined_forecasts` discarding single-timestamp
pooled forecasts. **Review established it is not reachable from this plan, and it has been dropped.**

`cmal_pool_pt` — the only model T3 makes horizon-relaxable, and so the only way this plan could
produce a one-step forecast — is `ArtifactScope.GROUP` (`models/aquacast/_shim.py:566`), and
**GROUP dispatch never combines**: combination is STATION / Phase B only
(`docs/touchpoint-maps.md:376`). The pooled persistence boundary is therefore never reached by
anything this plan changes.

The guard is still worth understanding when it does become reachable, so record the analysis rather
than lose it — a first reading of it was WRONG:

- `forecast_horizon_steps < _MIN_PERSISTED_TIMESTAMPS` (= 2): its comment names its sole rationale
  as the store fabricating a one-hour step. T4 removes that rationale.
- `_derive_uniform_time_step(...) is None` -> skip: this **survives** T4, carrying an independent
  rationale — a uniformly COARSENED intersection leaves `ensemble.time_step` at the ref
  contributor's stale DECLARED step, which the code repairs one line later via
  `replace(ensemble, time_step=derived_time_step)`.
- ⛔ Deleting the count floor ALONE is a no-op that looks like a fix: a single timestamp yields an
  empty delta set, so the uniformity check drops the forecast regardless.
- ⚠️ And an unresolved design question: for one timestamp there is no observed spacing, so the
  persisted cadence would come from whichever contributor is iterated first
  (`services/forecast_combination.py:136-166`) — order-dependent unless one-step contributors are
  required to AGREE on their declared step.

**This belongs to whichever plan first onboards a STATION-scoped relaxable model** — `cmal_small`
being the immediate candidate. It is a latent defect this plan does not create and cannot trigger.

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
- T4's column is NULLABLE with a NULL-tolerant named constraint and carries NO data backfill; the
  reader's legacy `NULL` path is UNCHANGED in behaviour (one-hour fallback retained, now logged).
- ⛔ The one-step RED was observed against `main`, not against this branch, and the observed
  pre-change value is recorded in the task.
- The deferred `NOT NULL` tightening has a named follow-on plan, or this plan does not close.
- Migration behaviour (upgrade, downgrade, constraint-name parity, a surviving NULL row) is proven
  in `tests/integration` against real Postgres — NOT asserted from a unit run.
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
    {"id": "T4", "depends_on": ["T3"], "parallel": false}
  ]
}
```
