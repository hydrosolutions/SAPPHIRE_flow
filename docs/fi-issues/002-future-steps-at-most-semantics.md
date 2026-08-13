# FI issue draft — `FutureKnownVariable.future_steps` cannot express "at most N"

**Status:** DRAFT, ready to file at `hydrosolutions/ForecastInterface`.
**Raised by:** SAPPHIRE Flow (SAP3), 2026-08-13.
**Related:** `hydrosolutions/ForecastInterface#4` (direct exceedance probability) — same pattern: a
contract gap that both sides are currently working around in prose.

---

## Summary

`FutureKnownVariable.future_steps` has **two incompatible meanings in the wild** and no way to tell
them apart:

- **a floor** — "I require exactly this many future steps; fewer is an error";
- **a ceiling** — "I can use up to this many; fewer yields a correspondingly shorter forecast".

Both a model and a provider must agree on which is meant, and the contract cannot say. Today the
disagreement is resolved by reading source comments.

## How it surfaced

aquacast commit `85e09a45`, *"trained horizon is a maximum, not a fixed input length"*, made its
models accept fewer steps than they declare. Its `requirement_from_config` could not express that, so
it added a comment instead:

> `future_steps` is the horizon CEILING, not a fixed input length: forecast-interface has no "at
> most" form, and a provider that sends fewer steps gets a correspondingly shorter forecast wherever
> the architecture allows it. Declaring the maximum is what tells a provider how much it can usefully
> supply.

The declared value stayed at its maximum (15 daily steps).

SAP3, the provider, reads that same field as a **hard requirement** and refuses to invoke a model
whose future forcing is short — deliberately, to avoid delivering a silently truncated input. Our
national NWP source (MeteoSwiss ICON-CH2-EPS) publishes **120 h**. The result: a model that would
happily produce a 5-day forecast is never called, and the affected stations produce **nothing**.

Both sides are behaving correctly against the contract as written. The contract is the problem.

## Why we are not patching around it

Per SAP3's ForecastInterface-adherence rule, a genuine FI expressiveness gap is fixed **upstream**,
not worked around locally. We can see the two available workarounds and dislike both:

- **Assume ceiling semantics for every model.** A model that genuinely requires its full horizon
  would then be handed a short input and return a plausible-but-wrong forecast. That is exactly the
  silent-wrongness class we have spent considerable effort eliminating elsewhere.
- **Carry a provider-side opt-in list.** Works, but it encodes a property of the *model* in the
  *provider's* configuration, so every consumer must independently rediscover which models tolerate
  truncation — precisely the coordination failure this interface exists to prevent.

We will ship the second as an interim measure, defaulting to today's strict behaviour, and retire it
once the contract can express the intent.

## Proposed contract

Make the requirement state its own semantics. A minimal, backward-compatible shape:

```python
class HorizonSemantics(StrEnum):
    EXACT = "exact"      # current behaviour; fewer steps is an error
    AT_MOST = "at_most"  # fewer steps is acceptable and yields a shorter forecast

class FutureKnownVariable(BaseModel):
    future_steps: int
    horizon_semantics: HorizonSemantics = HorizonSemantics.EXACT   # default preserves today
    min_future_steps: int | None = None   # only meaningful when AT_MOST
    ...
```

Three properties we would want:

1. **The default must be `EXACT`.** Existing models keep their current meaning with no edit, and no
   provider starts truncating silently after an upgrade.
2. **`min_future_steps` matters.** "Fewer is fine" is rarely unbounded — a 15-day model may be
   useless at 1 day. Without a floor, each provider invents its own.
3. **The declaration belongs to the model**, since only the model knows whether its architecture
   degrades gracefully. `aquacast`'s `_relax_horizon` is precisely that knowledge, currently
   invisible to consumers.

## Alternatives considered

- **A separate `max_future_steps` field alongside `future_steps`.** Equivalent expressive power, but
  it invites a state where both are set inconsistently, and it leaves the meaning of `future_steps`
  itself ambiguous.
- **A model-level rather than variable-level flag.** Simpler, but a model may reasonably need one
  forcing in full while tolerating truncation in another. Variable-level matches where
  `future_steps` already lives.
- **Leave it to documentation.** This is the status quo, and it produced two correct
  implementations that cannot interoperate.

## Impact if unresolved

Any provider whose NWP horizon is shorter than a model's training horizon must either refuse to run
the model or guess at its tolerance. For SAP3 specifically this blocks operational forecasting for
Swiss stations, where ICON's 120 h is a hard ceiling and no 15-day source is available for the
region.
