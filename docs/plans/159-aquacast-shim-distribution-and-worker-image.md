---
status: DRAFT
created: 2026-08-13
plan: 159
title: aquacast shim (in-repo optional extra) + forecast-cycle worker image
scope: Build the aquacast shim — a zero-argument entry-point class per trained config, owning the mm/day↔m³/s unit boundary and the mean_temperature↔temperature name boundary — plus the worker image that carries it, so an aquacast model is discoverable and unit-correct in production. **RESCOPED 2026-08-14 (owner): the shim lives IN THIS REPO as an optional extra, not as a separate `sapphire-aquacast` distribution.** It was split out of Plan 157 on 2026-08-13 on the assumption it had to be external; that assumption is what made it untestable, and it no longer holds.
depends_on: [152, 155, 157]
blocks: [152]
supersedes: []
---

# Plan 159 — aquacast shim distribution + worker image

## D17 — the shim lives IN THIS REPO (owner, 2026-08-14)

**Decision: an optional extra in `SAPPHIRE_flow`, not a separate `sapphire-aquacast` distribution.**

**Why the original split no longer holds.** Plan 157's expensive lesson was that *this repo cannot
build or test a distribution that lives outside it* — `/implement` produced a shim test that
monkeypatched `importlib.metadata.entry_points` with a fabricated class (green whether or not the
package existed) and a "cold-start `discover_models()`" test that ran on the host interpreter. Both
were deleted. **That lesson is an argument against externality, not against the shim.** In-repo, both
tests become real: the entry point is genuinely installed, and `discover_models()` genuinely resolves
it. The standing warning survives in weaker form — *never assert on a fabricated entry point* — but
`/implement` CAN drive this plan now.

**Why not the alternatives** (weighed 2026-08-14, verified against the cloned aquacast):
- *Entry points inside aquacast itself* — fewest moving parts, but it exports **our** deployment
  concerns (SAP3's zero-arg plugin convention, our canonical names and units) into a general-purpose
  research library with other consumers, and every trained config would need declaring in the
  modeller's repo.
- *Teach `discover_models` to construct with arguments* — changes the plugin contract for every
  model, and the config→model binding still has to come from somewhere.

**Honest scoping note.** Only two of the four mismatches actually *force* a shim: zero-argument
construction (`AquacastModel.__init__` requires a `template` — verified at
`aquacast/operational/model.py:446`) and discovery (**aquacast declares no entry points at all** —
verified). The unit and name boundaries are **ownership choices, not necessities**: SAP3 *does* have
`area` (it is one of PT's 50 statics, merged in Plan 155), so the area-dependent `mm/day ↔ m³/s`
conversion could live in our FI adapter. Keeping it in the shim keeps
`adapters/forecast_interface.py` — the single SAP3↔FI boundary — free of per-model special cases.
That is the real argument; "impossible in SAP3" is not.

### Consequences of D17 (verified 2026-08-14)
- **This introduces the repo's FIRST `[project.optional-dependencies]` extra.** aquacast pulls
  `torch==2.9.*`, `flashrnn`, `laplace-torch`, `curvlinops-for-pytorch`, `scikit-learn`, `scipy`,
  `matplotlib` — the base install, and the `default`/`ingest` workers, must **not** pull it.
- **aquacast is a PRIVATE repo**, so the build needs the same BuildKit-secret + scoped
  git-credential-rewrite pattern already used for `recap-dg-client` (`Dockerfile:23-31`), with its
  own token.
- **Python is fine:** `torch 2.9.1` ships `cp314` wheels and our image is `python:3.14.6-slim`;
  aquacast needs `>=3.12`, we declare `>=3.12`. Checked, because a missing wheel here would have
  killed the in-repo approach outright.
- **FI must be v0.1.19** (T0) — aquacast pins it.

## Objective

Make an aquacast model **discoverable and unit-correct in production**. Plan 157 delivered the import
path; without this plan an imported artifact still cannot be constructed, cannot convert its units,
and is invisible to `discover_models()` in the deployed image.

## The problems (carried from Plan 157)

### G3 — the entry-point registry cannot construct an aquacast model
`discover_models` constructs each entry point **with no arguments** — `raw_instance = cls()`
(`services/model_registry.py:87`). aquacast needs
`AquacastModel(ModelTemplate.from_yaml(...), device=...)`, and the adapter computes
`data_requirements` at construction (`adapters/forecast_interface.py:453`), so **the config must bind
at import time**: one entry point per trained config (Plan 152 D1 — config ships as package data).

### G9 — **`mm/day` has no SAP3 canonical unit** (the blocker)
`_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py:123-134`) deliberately omits `MM_PER_DAY`
and `fi_unit_to_canonical` (`:157-164`) raises for it; two locked tests enforce this. **Re-verified by
execution against post-156 `main` (2026-08-13): still raises.** Since `_ensemble_from_variable_output`
calls it at `:199` before building the ensemble, **every predict would raise**.

Resolution belongs in the **shim**, not SAP3's unit map: our canonical discharge is `m³/s` and
`mm/day ↔ m³/s` is **area-dependent**, so a bare map entry would be numerically wrong.

### G15 — `mean_temperature` vs `temperature` is a NAME boundary
aquacast declares `mean_temperature`; SAP3's canonical names are `{"precipitation", "temperature"}`
(`config/deployment.py:132`). Same class as G9, same owner: expose canonical `temperature` outward,
translate internally.

### G10 — the shim is invisible in the production image unless the extra is installed
`discover_models()` sees only **installed** entry points; the runtime image runs
`uv sync --frozen --no-dev` against **this repo's** lockfile (`Dockerfile:32`) and copies only that
virtualenv (`:82`).

**Post-156 this failure is now SILENT.** `discover_models` gained an
`except UnsupportedModelRequirementError` clause (`services/model_registry.py:96`), so an
unrepresentable model is skipped per entry point. A missing or unsupported shim therefore surfaces
downstream as `MODEL_NOT_FOUND`, not as a clear packaging error.

## T0 — PREREQUISITE: bump ForecastInterface to v0.1.19 (drift check, 2026-08-14)

**aquacast merged `feat/subdaily-operational` (PR #127) on 2026-08-14 and now pins FI `v0.1.19`;
this repo pins `v0.1.17`.** The shim imports both, so installing aquacast into a `v0.1.17`
environment is an **unsatisfiable resolve** — the bump is a prerequisite for T1, not a follow-on.

**Drift check performed against our merged code — nothing we shipped is affected.** The four changes
between v0.1.17 and v0.1.19:

| change | impact |
|---|---|
| `AggregationMethod.MAX` added | **None for PT.** Returned only for `*_peak` channels; `cmal_pool_PT`'s config has no peak channel (verified). |
| `RunConfig` base + opaque config mapping (new `interface/run_config.py`) | **None** — additive, unconsumed. |
| `train`/`retrain`: `config: Any` → `config: Mapping[str, Any]` | **None** — we pass `ModelParams`, which *is* `dict[str, Any]` (`types/model.py:30`), so the narrowed type is already satisfied. |
| `predict` | **Unchanged** — the forecast path and everything Plan 155 touched are untouched. |

So the bump is low-risk: three additive changes plus one narrowing we already satisfy.

### Two semantic findings the shim must own
- **`MM_PER_HOUR` is cadence-dependent in aquacast.** Its own comment: at a sub-daily cadence
  `MM_PER_HOUR` means mm per *fine step*, **not** mm per wall-clock hour — *except* for peak
  channels, where it genuinely is per wall-clock hour. T1 owns the unit boundary, so this ambiguity
  lands on the shim. Inert for `cmal_pool_PT` (DAILY), live the moment sub-daily is in scope.
- **Models now declare `AggregationMethod` per variable**, deliberately using "the same predicate the
  trained binning used ... so what we declare and what the weights were fit on cannot drift apart."
  **We currently ignore it** — acceptable for a daily model, and a real question for Plan 153.

## PT's contract, read off the LIVE model (2026-08-15)

Constructed `AquacastModel(ModelTemplate.from_yaml(cmal_pool_PT/config.yaml))` against real aquacast
`0.1.343` + FI `0.1.19` and dumped `input_requirement`. **This supersedes any description of the
contract elsewhere — it is what the model actually declares.**

```
targets:  discharge  unit=mm/day  representations=[quantiles, deterministic]
dynamic:  P1D -> data[basin_average]
  past_known[aquacast]:   discharge        lookback=210 max_nan=0 unit=mm/day  agg=sum
                          precipitation    lookback=210 max_nan=0 unit=mm/day  agg=sum
                          mean_temperature lookback=210 max_nan=0 unit=°C      agg=mean
  future_known[aquacast]: precipitation    future_steps=15 max_nan=0 unit=mm/day agg=sum  ens=single
                          mean_temperature future_steps=15 max_nan=0 unit=°C     agg=mean ens=single
static:   50 names        artifact_scope: GROUP
```

### Four corrections to this plan's assumptions
1. **`precipitation` is `mm/day` too, not just discharge.** G9 says to "audit precipitation and
   expose canonical `MM` if it declares `MM_PER_DAY`" — it does. So the unit boundary covers **two**
   variables, and only the discharge one is area-dependent; precipitation `mm/day → mm` over a daily
   step is a pure relabel, not a conversion.
2. **There is a SOURCE layer this plan never mentions**: the shape is
   `dynamic[time_step].data[spatial].{past_known,future_known}[SOURCE][name]`, and the source key
   here is the literal string `"aquacast"`. Any shim translation must preserve that nesting.
3. **`aggregation` is now declared** (`sum`/`mean`) — the FI v0.1.19 feature from T0, live on this
   artifact. We ignore it today; it is a Plan 153 question.
4. **`future_steps=15` is still declared** even though the modeller relaxed the horizon to a
   *maximum*. This is exactly the FI gap written up in `docs/fi-issues/002-future-steps-at-most-
   semantics.md` — the declaration cannot say "at most", so a strict provider still refuses. **The
   shim cannot fix this by lying about the number**; it needs the FI contract change, or a
   provider-side opt-in. Unchanged by anything here.

## T0b — the extra is NOT resolution-neutral (found while building T1, 2026-08-15)

**Adding the `aquacast` extra bumps shared dependencies for the BASE install too.** uv resolves a
single lockfile across all extras, so aquacast's floors propagate to everyone:

| package | main | with the extra declared |
|---|---|---|
| numpy | 2.4.3 | **2.5.2** (aquacast: `>=2.4.6`) |
| polars | 1.39.3 | **1.43.2** (aquacast: `>=1.41.2`) |
| scikit-learn | 1.8.0 | **1.9.0** (aquacast: `>=1.9.0`) |

**Consequence measured:** the pyright ratchet fails with **+7 errors in files that have nothing to do
with aquacast** — `models/linear_regression_daily.py`, `models/nwp_regression.py`,
`services/hindcast.py`, `services/skill/diagrams.py`, `services/training_data.py`,
`flows/run_hindcast.py`. Every one is a *"type is unknown / partially unknown"* diagnostic, i.e. new
type-stub inference noise from the version bumps, **not** a logic regression. Verified by running the
ratchet on clean `main` (**OK, 428 ≤ 459**) and on the branch (**FAILS**) with no other difference.

**"Optional extra" therefore means optional at INSTALL time, not at RESOLVE time.** D17's cost is
higher than recorded: taking the shim in-repo means the whole repo moves to aquacast's dependency
floors.

**Options, none free — owner decision before T1 lands:**
1. **Accept the bumps** and regenerate the pyright baseline deliberately, documenting that +7 is
   stub noise. Cheapest, but it raises the baseline permanently and blunts the ratchet.
2. **Fix the newly-surfaced annotations** in those six files. Keeps the ratchet sharp; unbounded
   effort in code unrelated to this plan.
3. **Split the resolution** (uv conflict/extra markers) so the base install keeps today's versions.
   Preserves the status quo, adds lockfile complexity.
4. **Reopen D17** — an external distribution has its own lockfile and cannot move ours. The
   trade-off that made in-repo attractive (testability) is unchanged; only the cost side moved.

**Note this is orthogonal to the FI bump (T0),** which was verified version-neutral: the resolved FI
commit was identical before and after.

## T0c — FI v0.1.20 lands the horizon contract we asked for (2026-08-15)

**`v0.1.20` adds exactly what `docs/fi-issues/002-future-steps-at-most-semantics.md` proposed** —
verified against the tag, not assumed:

```python
class HorizonSemantics(Enum):
    EXACT = "exact"      # fewer steps is an error
    AT_MOST = "at_most"  # fewer is acceptable, yields a shorter forecast

class FutureKnownVariable(BaseModel):
    horizon_semantics: HorizonSemantics = HorizonSemantics.EXACT   # default preserves today
    min_future_steps: int | None = None                            # only meaningful when AT_MOST
```

All three properties the issue argued for are present: the default is `EXACT` so no existing model
changes meaning, `min_future_steps` exists so "fewer is fine" is not unbounded, and the declaration
belongs to the **model**. A `model_validator` enforces coherence between the pair.

### ⚠️ This does NOT unblock the Swiss forecast on its own
The contract can now *express* a ceiling, but `cmal_pool_PT` still **declares** `future_steps=15`
with the default `EXACT`. Until **aquacast declares `AT_MOST` (+ a sensible `min_future_steps`) on
its future-known variables**, a strict provider still refuses ICON's 120 h. The remaining work is on
the modeller's side, not ours.

### ⚠️ Sequencing trap: bumping us to v0.1.20 BREAKS the extra
**aquacast pins `tag = "v0.1.19"`, which is exact.** Two different tags of the same git URL are two
different sources to uv — the identical class of conflict T0/#155 just fixed. So moving to v0.1.20
before aquacast does makes the `aquacast` extra unresolvable again.

**The ask to the modeller is therefore a single coordinated change:** move aquacast to FI
**v0.1.20** *and* declare `horizon_semantics=AT_MOST` with `min_future_steps` on
`precipitation`/`mean_temperature`. Doing only the first keeps us blocked on the horizon; doing
neither keeps us pinned at v0.1.19.

## Tasks

### T1 — the aquacast shim (IN THIS REPO, optional extra — see D17)
One zero-argument entry-point class per trained config, binding `ModelTemplate.from_yaml(...)` +
device at construction and declaring the `model_tier` / `alert_eligibility` attributes
`_assert_model_classification_declared` requires (`services/model_registry.py:61-76`). Config ships
as package data (Plan 152 D1).

**It owns both boundaries:**
- **units (G9)** — expose `M3_PER_S` for discharge, doing the **area-aware** conversion internally;
  audit precipitation and expose canonical `MM` if it declares `MM_PER_DAY`.
- **names (G15)** — expose canonical `temperature`, translate internally to `mean_temperature`.

**Acceptance — a NUMERIC area-aware round trip**: a known `mm/day` at a known `area` arrives as the
correct `m³/s`, asserted on the value. **Asserting that `fi_unit_to_canonical` merely succeeds proves
nothing** — `M3_PER_S` and `MM` already map, so that test passes on day one. This is the exact trap
that produced two unsound tests in Plan 156.

**Acceptance — discovery, with the extra installed:** `discover_models()` **returns** the aquacast
model. This must be a **positive** assertion: post-156 a broken or unrepresentable model is *silently
skipped* per entry point (`services/model_registry.py:96`), so "it constructs" is not "it is
registered". Compatibility must also report **zero** missing statics for a Swiss target station —
which is now meaningful, because Plan 155 merged the resolution that makes it pass.

**These tests are real in-repo (D17)**: the entry point is genuinely installed and genuinely
resolved. Never substitute a monkeypatched `importlib.metadata.entry_points` with a fabricated class
— that is the Plan 157 test that could not fail.

### T2 — the worker image (this repo, but only once T1 exists)
Per Plan 157 D13, the aquacast image **is the forecast-cycle worker image** — a superset of the
standard one on its own pool; `default`/`ingest` stay torch-free because they no longer run the cycle.

**Do not rebuild the pool before the image differs.** Plan 157 shipped exactly that and it was
reverted: `prefect-worker-forecast-cycle` used `build: *app-build` and the same
`sapphire-flow:${VERSION}` image, so the split bought nothing while adding a third pool, a
mixed-version upgrade window, and contradictions with `orchestration.md`. **The pool and the image
must land together**, with `import-model-artifact` routed alongside `forecast-cycle`.

**Acceptance:** a cold-start `discover_models()` **inside the built container**, asserting the
aquacast entry point resolves. A host-interpreter check does not count — that is precisely the gap
this task exists to close.

**Also required:** update `docs/standards/cicd.md` (topology + an upgrade procedure that stops the
cycle worker during migration) and `docs/standards/orchestration.md` (which still describes two
pools), and stop both workers during rerouting so no mixed-version window opens.

## Non-goals
- The import path and provenance — **delivered by Plan 157**.
- Retraining in SAP3 (Plan 152 D3), multi-resolution support (Plan 153).

## References
- `docs/plans/157-...md` — the in-repo half; its rescoping note explains why this plan exists.
- `docs/plans/152-...md` — artifact contract, decisions D1/D10/D13, and the four substitutions.
