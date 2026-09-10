---
status: DRAFT
created: 2026-09-10
plan: 264
title: QC rules select on network, not only parameter and cadence
scope: Add a network dimension to observation QC rule selection so one deployment can carry rules for more than one network without them colliding. NOT the DHM threshold values themselves (Plan 263), NOT forecast QC, NOT a new rule kind, NOT a change to any threshold currently in force.
blocks: [263]
source: 2026-09-10 — the owner's answer to Plan 263 D15. Opened because Plan 263's QC task cannot isolate a DHM rule set from the Swiss one on the current lookup; both independent reviews of that plan found the same thing.
---

# Plan 264 — QC rules select on network

## Status

**DRAFT.** Owner sets READY. Non-trivial and it touches a live path: one independent Claude
and one independent Codex review before READY.

Split out of Plan 263 deliberately. The owner chose this over the in-process workaround, and
it changes shared code that the running Swiss deployment depends on — that risk deserves its
own review and its own rollout, not a paragraph inside a Nepal data-import plan.

## Problem

Observation QC rules are selected by **measurement type and cadence only**:

```
QcRuleSet.rules_for(parameter, time_step)   # types/domain.py:160-167
```

There is no network, tenant or station dimension. Two consequences, both measured while
planning Plan 263:

1. **Rules for a second network collide with the first.** Add a DHM daily discharge rule
   beside the Swiss one and *both* match any daily discharge series;
   `aggregate_qc_status` then takes the worst. A Nepali rule set cannot loosen a Swiss limit,
   only tighten it — so calibrating for Nepal achieves nothing while the Swiss rule is present.
2. **Delivering a second set through configuration deletes the first.** `load_qc_rules`
   returns the built-in defaults **only when no `[qc_rules]` section exists**
   (`config/qc_rules.py:263-268`), and the overlay's `_deep_merge` replaces lists wholesale
   (`config/_overlay.py:44-56`). A deployment that adds Nepali rules by config loses the Swiss
   ones — silently, because the defaults *function* is untouched and any test asserting "the
   defaults are unchanged" still passes.

This is a real limitation for the Nepal deployment generally, not only for the historical
import that surfaced it: any deployment serving both networks hits it.

## What this does not change

**No threshold currently in force changes value.** This plan is about *selection*. A
deployment running only Swiss stations must produce byte-identical QC results before and
after — that is the acceptance bar, not a hope.

## Design sketch

`QcRuleParams` gains `network: str | None`, and `rules_for` takes the network and resolves
**most-specific-wins**: a rule naming the network beats a rule with `None` for the same
`(rule_id, parameter, time_step)`; `None` means "applies where nothing more specific exists".

That shape is chosen for two reasons. It is backward compatible by construction — every
existing rule keeps `None` and keeps applying exactly where it does today. And it fixes the
collision at the root: a DHM rule *replaces* the generic one for DHM stations rather than
stacking with it, which is what makes a looser Nepali limit possible at all.

The alternative — exact match only, every rule naming its network — was rejected: it would
require the built-in defaults to enumerate every network a deployment might serve, and a
station whose network nobody listed would silently get **no rules**, which
`aggregate_qc_status([])` then reports as *passed*. Fail-open by omission is the failure mode
Plan 263 already had to design out of its QC task; do not reintroduce it here.

## Owner decisions

**D1 — Does forecast QC get the same treatment?** `ForecastQcRuleSet.rules_for`
(`types/domain.py:260-267`) has the identical shape and the identical latent problem.
*Recommendation: no — leave it, and record the asymmetry in the touchpoint map.* Forecast QC
has no second-network pressure yet, and widening the blast radius of a change to a live path
for a problem nobody has is not a good trade. Revisit when a Nepal forecast path exists.

**D2 — How does the checker learn a station's network?** `Stage1QualityChecker.check`
(`services/qc.py:226`) receives observations, which carry `station_id` but not network, and
the service is pure — it has no store and should not acquire one.
*Recommendation: the caller passes a `station_id -> network` mapping.* It keeps the service
pure and puts the lookup where the station data already is. Every call site must then supply
it; see T2 for the audit.

**D3 — What happens when a station's network is not in the mapping?** *Recommendation: raise.*
The alternative is to fall back to the `None` rules, which is indistinguishable from correct
behaviour and would hide a wiring mistake behind plausible-looking QC results.

## Tasks

### T1 — The network dimension in the rule model and lookup

**Outcome**: `QcRuleParams` carries `network: str | None`; `rules_for` selects
most-specific-wins; TOML parsing accepts and validates the field.
**In**: `src/sapphire_flow/types/domain.py`; `src/sapphire_flow/config/qc_rules.py`;
unit tests.
**Out**: no threshold value changes; no change to `ForecastQcRuleSet` (D1); no change to any
call site (T2).
**Verification**: `uv run pytest tests/unit/types/test_qc_rule_selection.py` asserting: a
network-specific rule wins over a `None` rule of the same id/parameter/cadence; a `None` rule
still applies where no specific rule exists; the two never both return for one lookup; and an
unknown field in TOML is rejected rather than ignored.
**Pre-change**: a RED test proving that today two rules of the same id, parameter and cadence
both return from one `rules_for` call — the collision itself, not a proxy for it.

### T2 — Thread the network through every call site

**Outcome**: every `Stage1QualityChecker.check` caller supplies the station-to-network
mapping, and none can silently omit it.
**In**: `src/sapphire_flow/services/qc.py`; the call sites —
`flows/ingest_observations.py:305`, `services/onboarding.py:796`, and the forecast-path
callers at `services/run_station_forecast.py:549,557`,
`services/run_group_forecast.py:285,293`, `services/forecast_combination.py:332`.
**Audit the forecast callers before changing them**: they pass forecast ensembles through this
same observation checker, and whether "network" is even meaningful there is not obvious. If it
is not, say so in the plan and give them an explicit exemption rather than a plausible-looking
default.
**Out**: no behaviour change for a single-network deployment.
**Verification**: the mapping is a required parameter, so omission is a type error rather than
a silent default; `uv run pytest` passes whole; and a Swiss-only fixture produces **identical
flags, statuses and rule versions** before and after — the byte-identical bar from § What this
does not change.
**Pre-change**: N/A — mechanical threading, guarded by the equivalence test above.

### T3 — Documentation

**Outcome**: the QC selection contract is documented as network-aware, and the forecast-QC
asymmetry (D1) is written down rather than left for the next reader to discover.
**In**: `docs/spec/types-and-protocols.md`; `docs/touchpoint-maps.md`;
`docs/standards/wmo.md` if it states the QC rule contract.
**Out**: no code change.
**Verification**: bounded inspection — every doc statement about QC rule selection names the
network dimension, and the forecast-QC exemption is stated with its reason.
**Pre-change**: N/A — documentation.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```

## Explicitly out of scope

- The DHM threshold *values* — Plan 263 D14 and its QC task own those.
- Forecast QC rule selection (D1).
- Per-station QC overrides. `StationQcOverride` is a dataclass with no table, no store and no
  loader, and both production callers hard-code an empty list. That is a separate gap; this
  plan does not close it and does not depend on it.
- Any change to a threshold currently in force.
