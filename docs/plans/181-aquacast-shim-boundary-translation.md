---
status: DRAFT
created: 2026-08-18
plan: 181
title: aquacast shim — proxy the FI surface and translate the name/unit boundary
scope: Plan 159 T1 landed construction and discovery; the shim proxies NOTHING else, so `predict` cannot run. Complete the FI surface and translate the two boundaries in both directions — `mean_temperature`↔`temperature`, and `mm/day`↔`m³/s` (area-aware) / `mm/day`↔`mm`. This is what turns a discoverable model into a usable one.
depends_on: [159]
blocks: [152]
supersedes: []
---

# Plan 181 — aquacast shim: proxy the FI surface, translate the boundary

## ⚠️ Scope note: this is bigger than "declaration rewriting"

The obvious framing — "rewrite the declared names and units" — is **half the job and the smaller
half**. Verified against the merged shim:

```
grep -cE "def (train|predict|serialize_artifact|deserialize_artifact|hindcast)" _shim.py  →  0
```

`AquacastShim` exposes only `artifact_scope` and `input_requirement`. **Zero of the five FI methods
are proxied.**

**And the consequence is worse than "predict would raise" — corrected after review.** `adapt_if_fi`
wraps an object only when `isinstance(obj, ForecastModel)` (`adapters/forecast_interface.py:167`),
and that runtime-checkable Protocol **requires `train`, `predict`, `serialize_artifact` and
`deserialize_artifact`**. The shim satisfies none, so it is returned **unwrapped**:
`discover_models` registers the **raw shim**, with no adapter and therefore **no
`data_requirements`** — the attribute every downstream caller reads.

So T1 is not tidying-up for completeness. **Proxying the five methods is the precondition for the
model being adapted at all**, and only an adapted model has the requirement projection the rest of
SAP3 consumes.

*(This also qualifies a claim made when Plan 159 T1 merged: `discover_models()` does return
`cmal_pool_pt`, but membership alone was a weaker result than it sounded — the registered object is
not the adapter.)*

## The two boundaries, and why declaration alone is not enough

A declaration rewrite changes what SAP3 **fetches**. It does not change what aquacast **receives**.
Both directions are needed, or the model gets canonical data under names and units it does not
understand:

| | aquacast declares | SAP3 canonical | direction needed |
|---|---|---|---|
| temperature | `mean_temperature` | `temperature` | declare out, translate in |
| precipitation | `mm/day` | `mm` | declare out, translate in |
| discharge (target) | `mm/day` | `m³/s` | declare out, translate **out** of results |
| discharge (past input) | `mm/day` | `m³/s` | translate **in** |

`_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py`) has `M3_PER_S`, `MM`, `DEG_C` and
**deliberately omits `MM_PER_DAY`**, so today `fi_unit_to_canonical` raises before a forecast is
built — the G9 blocker, verified by execution on the merged shim.

## The area problem, which sets the shape of the design

`mm/day ↔ m³/s` is **area-dependent** (`_units.py`, already merged and numerically tested). The area
arrives as a **static**, in `StationInputs.static` — so the translation cannot be a pure per-column
map applied before inputs are assembled. It must happen where the station's statics are in hand,
per station, inside `predict`.

**Consequence for GROUP scope**, which `cmal_pool_PT` uses: `ModelInputs.stations` is a dict of
many stations, each with its own area. The conversion is therefore **per station, not per batch** —
applying one area across the group would silently rescale every other station's discharge.

## Tasks

### T1 — proxy the FI surface
Implement `train`, `predict`, `serialize_artifact`, `deserialize_artifact`, and `hindcast` if the
inner model provides it, delegating to `self._inner`.

**Acceptance:** a `predict` call reaches the inner model. Assert on the delegation, not on
`hasattr` — a test that only checks the methods exist would pass against stubs that raise.

### T2 — rewrite the declaration
`input_requirement` returns a requirement with canonical names and units: `mean_temperature` →
`temperature`, `MM_PER_DAY` → `MM` for precipitation, `MM_PER_DAY` → `M3_PER_S` for discharge.

**Preserve the nesting** — `dynamic[step].data[spatial].{past,future}_known[SOURCE][name]`, source
key `"aquacast"` (Plan 159 records this; it is easy to flatten by accident).

**Acceptance:** `fi_unit_to_canonical` succeeds for every declared unit — which it does **not**
today. Also assert `future_steps`, `lookback`, `max_nan` and `ensemble_mode` are **unchanged**:
this task renames and re-labels, it must not alter the contract's shape.

### T3 — translate the data, both directions
- **Inbound** (`predict`/`train`): canonical → aquacast. `temperature` → `mean_temperature`;
  discharge `m³/s` → `mm/day` using that station's `area`; precipitation `mm` → `mm/day`.
- **Outbound** (results): aquacast → canonical. Discharge `mm/day` → `m³/s`, same area.

**Acceptance — a NUMERIC round trip through `predict`**, not a shape check: feed a known discharge
at a known area, assert the value the inner model receives, and assert the value coming back out.
Asserting only that predict "returns something" would pass against a no-op translation, which is the
trap Plan 159's unit tests were specifically written to avoid.

**Per-station, verified:** a GROUP call with two stations of **different** areas must convert each
with its own. A test with equal areas cannot detect the batch-wide bug.

## Decisions (owner, 2026-08-18)

| # | Decision | Resolution |
|---|---|---|
| **D1** | Precipitation `mm/day` → `mm`: relabel or conversion? | **RESOLVED: relabel — and only for DAILY models.** At a daily step the two are numerically identical. **Assert the step is daily and raise otherwise**: `mm/day` over a 3h step is *not* `mm`, so a silent relabel on a future sub-daily branch would be wrong by **8×**. The guard is the point, not the relabel. |
| **D2** | Where does `area` come from — the resolved Caravan static, or `Basin.area_km2`? | **RESOLVED: the static the model was given** (`caravan:area`, resolved by Plan 155 D15/D16), so the conversion uses exactly the number PT was trained against. Reaching past the resolved statics to `Basin.area_km2` would reintroduce the CAMELS-CH/Caravan mismatch D15 exists to prevent — and for `area` specifically that silently rescales every discharge. |
| **D3** | What if `area` is missing at predict time? | **RESOLVED: raise, naming the station.** `_units.py` already does this for non-finite/missing area. **No fallback to a basin lookup** — a fallback would silently reintroduce D2's mismatch at exactly the moment the intended value is absent. |

## Follow-on idea (owner, 2026-08-18) — validate data AVAILABILITY at onboarding

Owner's suggestion: at model onboarding, check that the data a model needs is actually available for
the stations registered to it. **Recorded here as a future plan candidate, deliberately not a task
in 181** — it is a distinct capability, not part of the shim boundary.

**What already exists** — corrected after review, because the first draft cited the wrong function
and overstated the gap. The live path is
`services/model_onboarding.py::validate_compatibility_for_unit` (`:207`), reached from
`flows/onboard_model.py:285`. **`validate_compatibility` (`:159`) is dead code** — it calls
`model_id_from_model()`, which always raises, so it cannot return. Citing it would have sent the
next reader to a function that never runs.

It reports `missing_target_parameters`, `missing_past_dynamic`, `missing_future_dynamic`,
`missing_static_features`, `time_step_compatible`, `fi_unit_mismatches` and
`station_codes_resolvable` (`types/model_onboarding.py:18-27`).

**Static VALUES are already checked for a Caravan model**, contrary to the first draft:
`_resolve_declared_value` applies `_is_finite_numeric` (`services/caravan_statics.py:255`), so
infinities, strings and bools are rejected before compatibility — not merely keys counted.

**The real remaining gap is temporal depth.** Nothing checks that a station has `lookback` steps of
gap-free history. That is what bit Plan 155: a station passes compatibility and is still
unforecastable. Note also that **there is no enforcing runtime guard today** —
`services/operational_inputs.py:445` logs `short_lookback` and *continues*; making it an explicit
failure is still future work in Plan 155 T3. So today a short station is caught by neither
onboarding nor the cycle.

**Worth its own plan.** Sizing note for whoever writes it: the expensive part is not the check, it is
deciding what onboarding should DO with a partial answer — refuse the model, register it with the
short stations excluded, or register and warn.

## Non-goals
- Retraining in SAP3 (Plan 152 D3), multi-resolution (Plan 153), the worker image (Plan 159 T2).
- The horizon ceiling — handled by the interim opt-in and, properly, by aquacast declaring `AT_MOST`.
- Any change to `adapters/forecast_interface.py`: the shim exists to keep per-model translation out
  of the single SAP3↔FI boundary (Plan 159 D17).

## References
- `src/sapphire_flow/models/aquacast/_units.py` — the area-aware conversion, merged and tested.
- Plan 159 § "PT's contract, read off the LIVE model" — the declaration this rewrites.
- Plan 155 D15/D16 — why `area` must come from the resolved Caravan static.
