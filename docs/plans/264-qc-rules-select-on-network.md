---
status: DRAFT
created: 2026-09-10
plan: 264
title: QC rules select on network, not only parameter and cadence
scope: Add a network dimension to observation QC rule selection so one deployment can carry rules for more than one network without them colliding. NOT the DHM threshold values themselves (Plan 268), NOT forecast QC, NOT a new rule kind, NOT a change to any threshold currently in force.
blocks: [268]
reviews:
  - "codex 2026-09-10 — reviewed as a set with 268; NOT READY, 3 blockers; the call-site audit was wrong"
open_decisions: [D4]
source: 2026-09-10 — the owner's answer to Plan 268 D15. Opened because Plan 268's QC task cannot isolate a DHM rule set from the Swiss one on the current lookup; both independent reviews of that plan found the same thing.
---

# Plan 264 — QC rules select on network

## Status

**DRAFT — NOT READY.** One independent Codex review has run (2026-09-10, reviewing this plan
together with Plan 268 as a set) and is folded. An independent Claude pass is still owed.

**The review found the author's call-site audit was wrong**, which is worth recording because
it was the one part of this plan asserting a fact about the repository. T2 listed three
forecast files as callers of the observation quality checker. They are not: they call
`ForecastOutputQualityChecker`, a separate Protocol over `ForecastEnsemble` and
`ForecastQcRuleSet` (`services/run_station_forecast.py:229`). The author matched on the method
name `check(` and attributed the hits to the wrong checker. The real production callers of the
observation checker are **two**: `flows/ingest_observations.py:304` and
`services/onboarding.py:772`. As scoped, T2 would have either changed forecast QC — directly
against D1 — or left the `QualityChecker` Protocol (`protocols/stores.py:1012`) stale.

Three further faults from that round, all folded below: the plan diagnosed a
configuration-composition problem it never fixed; most-specific-wins had no uniqueness
invariant; and the "byte-identical" bar had no oracle.

**Round 2 (2026-09-10) then found the corrected audit was STILL incomplete, and that the
uniqueness invariant would break working code.** Both are recorded here because they are the
third and fourth time this plan's claims about the repository have been wrong:

- **`scripts/dhm_precip/` is a third caller** — `qc_mask.py:203,208` and
  `build_dudh_koshi_handover.py:175` call `Stage1QualityChecker.check` **positionally** with
  four arguments, and are covered by `tests/unit/scripts/`. T2's new required parameter breaks
  them, and its own gate ("`uv run pytest` passes whole") would have failed.
- **The uniqueness invariant would make an existing, deliberate, tested rule set
  unconstructible.** `scripts/dhm_precip/qc_ruleset.py:96-108` builds two `frozen_sensor` rules
  sharing `rule_id`, `parameter` and `time_step`, distinguished only by `rule_version`; its
  docstring documents this as intentional and
  `tests/unit/scripts/test_dhm_precip_ruleset.py:54` asserts both are returned. The proposed
  RED test would have tried to prove as a defect the behaviour the repo relies on as a feature.

Split out of Plan 268 deliberately. The owner chose this over the in-process workaround, and
it changes shared code that the running Swiss deployment depends on — that risk deserves its
own review and its own rollout, not a paragraph inside a Nepal data-import plan.

## Problem

Observation QC rules are selected by **measurement type and cadence only**:

```
QcRuleSet.rules_for(parameter, time_step)   # types/domain.py:160-167
```

There is no network, tenant or station dimension. Two consequences, both measured while
planning Plan 268:

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

   **This plan does not fix (2), and the first revision was wrong to imply it did.** Adding a
   network dimension changes *selection*, not *composition*: after this plan ships, an overlay
   that supplies a `[qc_rules]` section still replaces the built-in set wholesale. What changes
   is that a complete rule set can now express both networks without collision. **Configuration
   overlays remain unsuitable for adding rules incrementally**, and D4 records that as a stated
   limitation rather than a fixed one.

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
Plan 268 already had to design out of its QC task; do not reintroduce it here.

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

**D4 — Configuration composition: fix it here, or state the limitation? NEW, open.**
The Problem section's second defect survives this plan. Options: fix `load_qc_rules` and the
overlay so rule lists merge rather than replace; or state plainly that a deployment must supply
a complete rule set and that overlays cannot add rules incrementally.
**The author's first recommendation ("state the limitation") was a false binary, and the owner
rejected it.** Asking whether a hydromet should extend a base set is the right question. What
the evidence says, measured across the 26 deployed rules:

- **Rule *kinds* are universal.** Range, rate-of-change, spike, frozen-sensor and outlier are
  WMO-168 Vol I standard checks (`docs/standards/wmo.md:62`); every hydromet runs the same five.
- **Thresholds split in two.** Some are physics and travel: discharge and precipitation cannot
  be negative, air temperature sits within ±50 °C, the outlier sigma multiple is a statistical
  convention. Others are meaningless off-site: the discharge ceiling, every rate limit, and
  **all** of water level, since a gauge datum is local by definition.
- **Inheriting kinds is valuable; inheriting thresholds is dangerous.** A missing rule fails
  open — you lose a check, silently. We have that today: `config.toml` carries 26 rules against
  28 in code, and among the missing pair is the daily discharge frozen-sensor check. Nobody
  decided that; it drifted. An inherited *threshold*, by contrast, fails loud and wrong — the
  Swiss 5,000 ceiling condemns 1,125 genuine monsoon peaks as bad data.

*Recommendation: a base set that declares **which checks apply to which parameter and cadence,
carrying no threshold values at all**, with every threshold declared locally — and a declared
check with no local threshold being an **error**, not a default.* That buys drift protection
without threshold inheritance, and the merge becomes trivial and safe because what is merged is
a checklist, not values.

**Scope note:** that is a broader change than this plan, which is about *selection*. It also
does not reach the variation that actually bit us — six stations needing six ceilings is below
anything a network-level base set can express, and that already has a working mechanism. Decide
whether it belongs here, in its own plan, or after this one ships.

**D5 — Who owns the hard-coded flag version? NEW, open — and it is a genuine trap.**
`services/qc.py` emits `_RULE_VERSION = "1.0"` at five of six flag sites (`:22`) instead of the
configured `rule.rule_version`. Plan 268 needs DHM flags to carry the DHM rule set's version,
which means fixing those sites. **But the Swiss rules declare `rule_version="1.0.0"`**
(`config/qc_rules.py:47`), so fixing them changes Swiss flags' recorded version from
`"1.0"` to `"1.0.0"` — breaking this plan's byte-identical bar and Plan 268's "no operational
QC change" exclusion at the same time. Neither plan owned this until the set review found it.

Two corrections from round 2: **not every** flag changes — `_apply_frozen_sensor`
(`services/qc.py:140`) already uses the configured version. And the fix has **a second half
nobody owns**: the row-level `observations.qc_rule_version` column is written from
`services/qc_datum.py:23-26`, which hard-returns `"1.0"` for every non-`water_level` parameter
independently of `QcFlag.rule_version`. Fixing the five flag sites leaves that column at the
Swiss constant, so Plan 268's "flags carry the DHM version" gate could pass while the persisted
row still says otherwise. Whatever D5 decides must cover both, and must note that the changed
value is serialised into existing `observations.qc_flags` rows, the forecast and hindcast
stores, and the stations API — over a corpus of `"1.0"` flags with no backfill.
**CLOSED: fix it** (owner, 2026-09-10), covered by T4b. The owner also settled the historical
half and the risk framing behind it: **the Swiss deployment is a sandbox, not production** — it
can be wiped and rebuilt if needed. Existing `"1.0"` flags therefore need no migration decision,
and this plan's repeated "changes shared code the live Swiss deployment depends on" framing is
**overstated**: the blast radius is a test deployment. The byte-identical bar in T4 is kept
anyway — it is cheap and it catches unintended selection changes — but it is a correctness
check, not a production-safety gate, and no task should be scoped as though a national service
were downstream. Original reasoning: — the current behaviour records a version that does not match the rule that ran,
which is a provenance defect in its own right, and shipping the network dimension without
fixing it means DHM flags would be stamped with a Swiss rule's version. The byte-identical bar
then applies to flags, statuses and thresholds, with `rule_version` explicitly exempted and
covered by its own before/after assertion.

## Tasks

### T1 — The network dimension in the rule model and lookup

**Outcome**: `QcRuleParams` carries `network: str | None`; `rules_for` selects
most-specific-wins; the rule set rejects ambiguity at construction; TOML parsing accepts and
validates the field.
**In**: `src/sapphire_flow/types/domain.py`; `src/sapphire_flow/config/qc_rules.py`;
`config.toml` and `docs/spec/config-reference.toml` (rejecting an unknown field is a parsing
behaviour change and both files must satisfy the stricter parser); unit tests.
**Out**: no threshold value changes; no change to `ForecastQcRuleSet` (D1); no change to any
call site (T2); no change to configuration composition (D4).
**Verification**: `uv run pytest tests/unit/types/test_qc_rule_selection.py` asserting:
- a network-specific rule wins over a `None` rule of the same id/parameter/cadence —
  **with the two carrying different `rule_version`s**, so the test discriminates. Selection
  groups by `(rule_id, parameter, time_step)` while uniqueness keys on that plus
  `rule_version` and `network`; if the fixture's two rules share a version, an implementation
  that wrongly grouped by the full uniqueness key would pass while letting a real Swiss and a
  real DHM rule both fire;
- a `None` rule still applies where no specific rule exists;
- the two never both return from one lookup;
- **duplicates are rejected at construction** — two rules sharing
  `(rule_id, **rule_version**, parameter, time_step, network)` raise rather than both firing.
  **`rule_version` is in the key deliberately**: `scripts/dhm_precip/qc_ruleset.py:96-108`
  ships two `frozen_sensor` rules that differ only by version, on purpose and under test, and
  an invariant without `rule_version` would make that rule set unconstructible. Plan 268's
  isolation gate consumes "exactly one resolves" — with this key it gets that for its own
  single-version DHM set without outlawing the precipitation one;
- TOML round-trips a valid `network = "dhm"`, rejects a non-string network, and rejects an
  unknown field rather than ignoring it (the hand-written parser at `config/qc_rules.py:25-37`
  does no runtime type validation today).
**Pre-change**: a RED test proving that today a **network-specific** rule cannot suppress a
generic one of the same id, parameter and cadence — both return from one `rules_for` call. Not
the earlier formulation ("two rules of the same id, parameter and cadence both return"), which
is a *feature* the precipitation rule set depends on and asserts.

### T2 — Thread the network through the observation-QC call sites

**Outcome**: every `Stage1QualityChecker.check` caller supplies a station-to-network mapping,
and none can silently omit it.
**In**: `src/sapphire_flow/services/qc.py`; **`src/sapphire_flow/protocols/stores.py`** — the
`QualityChecker` Protocol at `:1012` declares this signature and must change with it; and
**all** callers — `flows/ingest_observations.py:304`, `services/onboarding.py:773`, and
**`scripts/dhm_precip/qc_mask.py:203,208`** reached from
**`build_dudh_koshi_handover.py:175`**, which call positionally and sit outside the pyright
gate, so only `tests/unit/scripts/` will catch a break.
**Out**: **the forecast QC path is untouched.** `services/run_station_forecast.py:549,557`,
`services/run_group_forecast.py:285,293` and `services/forecast_combination.py:332` call
`ForecastOutputQualityChecker` — a *separate* Protocol over `ForecastEnsemble` and
`ForecastQcRuleSet`. (Precisely: `ForecastOutputQualityChecker` is a concrete class at
`services/forecast_qc.py:230`; the Protocol at `protocols/stores.py:1025` is
`ForecastQualityChecker`. The earlier revision conflated the two names — the conclusion,
leave that path alone, is unchanged.) The first revision of this plan listed them as observation-QC
call sites; that was wrong, and acting on it would have changed forecast QC against D1.
**Verification**: the mapping is a required parameter — **ordered before `skipped_rule_ids`
or keyword-only**, since that parameter carries a default in both `services/qc.py:232` and
`protocols/stores.py:1019`, and `scripts/dhm_precip/qc_mask.py:203,208` call positionally; **a station absent from the mapping raises** (D3) — asserted by a test, not
only stated; `uv run pytest` passes whole; and the golden-fixture equivalence in T4 holds.
**Pre-change**: a RED test proving that today a station's network cannot influence which rules
run, because the checker has no access to it.

### T3 — Fail closed when no rule resolves

**Outcome**: `Stage1QualityChecker` distinguishes "rules ran and found nothing" from "no rules
resolved", and the second is an error rather than a pass.
Added after the set review: the author's justification for most-specific-wins was that
exact-match could resolve zero rules and be reported as passed — but **most-specific-wins does
not close that path either**. An empty rule set, a TOML set omitting the generic rules, or a
series whose parameter/cadence matches nothing all still yield empty flag lists, and
`aggregate_qc_status([])` returns `QC_PASSED` (`types/domain.py:104-109`). Today the live
ingest and onboarding paths are fail-open in exactly this way. Plan 268 was going to bolt a
local assertion onto its own import; the policy belongs here, once, for every caller.
**In**: `src/sapphire_flow/services/qc.py`; `src/sapphire_flow/protocols/stores.py`;
every call site's handling of the new error.
**🔴 It must not break a deliberate empty pass.** `build_dudh_koshi_handover.py:154` defines
`_empty_rule_set()` and passes it into `check` on every iteration of `attribute_mask_by_rule`
(`:174-190`) — an intentional, tested no-rules call. A blanket "no rules resolved → raise"
breaks it. The contract must distinguish **accidental** non-resolution (a rule set that should
have matched and did not) from an **explicitly empty** rule set the caller supplied on purpose:
an empty `QcRuleSet` is a caller's declared intent and passes; a non-empty rule set that
resolves nothing for a series is the error.
**Out**: no change to what any *resolved* rule does.
**Verification**: a **non-empty** rule set that resolves nothing for a series raises rather
than returning empty flags; a series that resolves rules and trips none still returns
`QC_PASSED`; **an explicitly empty `QcRuleSet` still returns empty flags without raising** —
asserted directly against the handover builder's path
(`tests/unit/scripts/test_dhm_precip_*`), because without that case a blanket "no rules
resolved → raise" satisfies every other assertion here and breaks a deliberate caller; and the
three outcomes are distinguishable in the caller's logs.
**Pre-change**: a RED test proving that today a rule set with no matching rule marks every
observation `qc_passed` with zero rules run.

### T4 — Equivalence for a Swiss-only deployment

**Outcome**: proof that a deployment serving one network is unaffected.
Added because "byte-identical before and after" had no oracle — a post-change test cannot
compare against a "before" that no longer exists.
**In**: a golden fixture under `tests/fixtures/qc/` — a fixed observation series plus **the
rule set loaded from `config.toml`, which is what actually runs**, not
`_default_swiss_qc_rules()`, which `load_qc_rules` never returns while a `[qc_rules]` section
exists (`config/qc_rules.py:262-268`). A fixture built from the defaults function would protect
code that no deployment executes. Expected flags and statuses **generated before T1 lands** and
committed as the baseline. Note "thresholds" are not a returned field — they appear only inside
`QcFlag.detail`, so the comparison is over `detail` text.
**Out**: not a new QC behaviour; purely a regression bar.
**Verification**: `uv run pytest tests/unit/services/test_qc_swiss_equivalence.py` — the Swiss
fixture's flags, statuses and thresholds match the golden artifact exactly. **`rule_version` is
the one exempted field** and carries its own assertion: it changes from `"1.0"` to the
configured `"1.0.0"` per D5, deliberately and once.
**Pre-change**: N/A — the fixture *is* the pre-change evidence, and it must be generated and
committed first.

### T4b — The flag-version correction (D5)

**Outcome**: a QC flag records the version of the rule that actually produced it, and the
row-level column agrees with it.
Added after round 4 found D5 was load-bearing — 268's QC task consumes it, and T4's own
verification already asserts its outcome — while no task owned it.
**In**: `src/sapphire_flow/services/qc.py` (the five sites using `_RULE_VERSION`; the sixth,
`_apply_frozen_sensor` at `:140`, already uses the configured version and is the model);
**`src/sapphire_flow/services/qc_datum.py:23-26`** — the second half, which writes
`observations.qc_rule_version` independently and hard-returns `"1.0"` for every non-water-level
parameter. Fixing only the flag sites leaves the persisted column disagreeing with the flags it
describes.
**Out**: no threshold change; no change to which rules run.
**Verification**: for a Swiss daily series, every flag's `rule_version` equals its rule's
configured version, and `observations.qc_rule_version` equals it too; the golden fixture (T4)
records the change from `"1.0"` to `"1.0.0"` as the single intended difference.
**Pre-change**: a RED test proving that today a rule configured with version `"2.0.0"` still
emits flags stamped `"1.0"` — the defect itself, at both the flag and the column.
**Blast radius, stated because it is not small**: the changed value is serialised into existing
`observations.qc_flags` rows, `store/forecast_store.py:92`, `store/hindcast_store.py:56` and
`api/routes/api_stations.py:96`, over a corpus of `"1.0"` flags with no backfill. D5 must say
whether that corpus is left as-is (recommended — historical flags honestly record the version
that ran at the time) or migrated.

### T5 — Documentation

**Outcome**: the QC selection contract is documented as network-aware; the forecast-QC
asymmetry (D1), the composition limitation (D4) and the version migration (D5) are written down
rather than left for the next reader to discover.
**In**: `docs/spec/types-and-protocols.md`; `docs/touchpoint-maps.md`;
`docs/standards/wmo.md` if it states the QC rule contract.
**Out**: no code change.
**Verification**: bounded inspection — every doc statement about QC rule selection names the
network dimension; the forecast-QC exemption, the overlay limitation and the version change are
each stated with their reason.
**Pre-change**: N/A — documentation.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T4"] },
    { "id": "phase-2", "tasks": ["T1"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T2", "T3", "T4b"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5"], "depends_on": ["phase-3"] }
  ]
}
```

T4 runs **first**: the golden fixture is the pre-change baseline, and it cannot be generated
after the change it exists to detect. T4b (D5) is sequenced with T2/T3 rather than left
unowned — T4's verification already asserts its outcome.

**Cross-plan sequencing:** T4's fixture and Plan 268 T7's rule additions both touch
`config.toml`'s `[qc_rules]` array. The fixture must be captured **before** any DHM row is
added, or it bakes in the change it exists to detect.

## Explicitly out of scope

- The DHM threshold *values* — Plan 268 D14 and its QC task own those.
- Forecast QC rule selection (D1).
- Per-station QC overrides **as persisted rows**. `StationQcOverride` is a dataclass with no
  table, no store and no loader, and both production callers hard-code an empty list. This plan
  does not add that schema. **Note for the set:** the dataclass *can already* express a
  per-station threshold in memory, and Plan 268's six station-specific limits are built that
  way — so the capability Plan 268 needs is not blocked on this exclusion. Plan 268 T7 owns
  constructing them; this plan owns only which rule they merge into.
- Any change to a threshold *value* currently in force. (Flag `rule_version` **does** change,
  once and deliberately — D5.)
- Fixing configuration composition so overlays can add rules incrementally (D4).
