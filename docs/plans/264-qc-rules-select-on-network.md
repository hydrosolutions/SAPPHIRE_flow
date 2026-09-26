---
status: COMPLETE
created: 2026-09-10
plan: 264
title: QC rules select on network, not only parameter and cadence
scope: Add a network dimension to observation QC rule selection so one deployment can carry rules for more than one network without them colliding. NOT the DHM threshold values themselves (Plan 268), NOT forecast QC, NOT a new rule kind, NOT a change to any threshold currently in force.
blocks: [268, 269]
reviews:
  - "codex 2026-09-10 — reviewed as a set with 268; NOT READY, 3 blockers; the call-site audit was wrong"
  - "claude 2026-09-25 — NEEDS_CHANGES on SHA 9f47a31eb8b0a7e86f1854854273a985849ddf845f7da37b39d5a24b1cd5bc77; selector-count path, version semantics, and QC-overlay enforcement findings folded"
  - "codex 2026-09-25 — NEEDS_CHANGES on SHA 82150dcaff901c8616604032dee2ccdfabeb6a7482a7b357c59fe6512b552ee0; caller error handling, integration coverage, zero-rule scope, API provenance, and default/test-path findings folded"
  - "codex 2026-09-25 — NEEDS_CHANGES on SHA 63286e4644c731a14cbea37e610cd0be9b9952d127ead764b5ed11cf4b6632f2; T1/T2 selector sequencing and missing T2 test ownership folded"
  - "codex 2026-09-26 — NEEDS_CHANGES on SHA 95f29804076839de1e53f00b68da7537a57ba36e6f2a1bb1f0902b28629bd34d; added the omitted DHM and recheck ingest-flow tests to T2"
  - "codex 2026-09-26 — NEEDS_CHANGES on SHA fbb33ef23ad16e9d2bd4cfec69a2a51c49a1d2908b5f0bd7a59fea64a52cfdef; moved configured flag-version assertions from T4 to T4b and assigned row-marker checks to the ingest-flow test"
  - "codex 2026-09-26 — NEEDS_CHANGES on SHA 72ab5035ec79c2dd446f82446e7877aa35db97d6e6bcbc05d7868eed799316a5; excluded the intentionally changing flag version from T4's golden comparison and named T3's tests"
  - "codex 2026-09-26 — NO_FINDINGS on SHA b80d5889af9c5f6b25e220105a0f7eb2ff182cd8965bdbdf95dd7b735c867757; final Plan 264 review"
open_decisions: []
source: 2026-09-10 — the owner's answer to Plan 268 D15. D4 closed by the owner on 2026-09-25: hydromet rules extend the shared base configuration.
---

# Plan 264 — QC rules select on network

## Status

**COMPLETE — merged to `main` in PR #315 on 2026-09-26** (`dbbea4d9`).

The implementation adds `QcRuleSet.rules_for(parameter, time_step, *, network=None)` and
network-aware rule selection to the checker, selection reporter and
offline DHM precipitation path; passes station networks through their callers; and records
configured rule versions on flags. It enforces the shared-base configuration policy for QC rules.
The Swiss equivalence fixture and complete test suite passed before PR #315 merged.

Split out of Plan 268 deliberately. The owner chose this over the in-process workaround, and
it changes shared code that the running Swiss deployment depends on — that risk deserves its
own review and its own rollout, not a paragraph inside a Nepal data-import plan.

## Problem before implementation

Before this plan, observation QC rules were selected by **measurement type and cadence only**:

```
QcRuleSet.rules_for(parameter, time_step)   # before PR #315
```

There was no network dimension. Two consequences were measured while planning Plan 268:

1. **Rules for a second network collided with the first.** Adding a DHM daily discharge rule
   beside the Swiss one made *both* match any daily discharge series;
   `aggregate_qc_status` then took the worst. A Nepali rule set could not loosen a Swiss limit,
   only tighten it — so calibrating for Nepal achieved nothing while the Swiss rule was present.
2. **Putting a second set in a config overlay replaced the first.** `load_qc_rules`
   returns the built-in defaults **only when no `[qc_rules]` section exists**
   (`config/qc_rules.py:251-268`), and the overlay's `_deep_merge` replaces lists wholesale
   (`config/_overlay.py:49-62`). A deployment that added Nepali rules by overlay lost the Swiss
   ones — silently, because the defaults *function* is untouched and any test asserting "the
   defaults are unchanged" still passes. D4 therefore places multi-network rule definitions in
   the base configuration, not in overlays.

   **D4 resolves this composition boundary.** The shared base configuration is the authoritative
   rule list for every network served by that deployment. DHM-specific rules are added to that
   list alongside the generic rules; overlays are not used to add QC rules because their arrays
   replace the base array wholesale. Network-specific rules override the generic rule with the
   same rule, parameter and cadence. This plan changes selection, not overlay merge semantics.

The underlying overlay behavior remains, but it is not a limitation on a multi-network base
configuration under D4's policy. The checked-in deployment overlays do not define QC rules.

## What this does not change

**No threshold currently in force changes value.** This plan is about *selection*. A
deployment running only Swiss stations must produce byte-identical QC results before and
after — that is the acceptance bar, not a hope.

## Design sketch

`QcRuleParams` gains `network: str | None = None`, and `rules_for` accepts an optional network
and resolves
**most-specific-wins**: a rule naming the network beats a rule with `None` for the same
`(rule_id, parameter, time_step)`; `None` means "applies where nothing more specific exists".
Omitting the lookup's network preserves the current generic-only behavior for direct callers;
the QC checker and selection reporter still require an explicit station-to-network map under T2.

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
(`services/qc.py:289`) receives observations, which carry `station_id` but not network, and
the service is pure — it has no store and should not acquire one.
*Recommendation: the caller passes a `station_id -> network` mapping.* It keeps the service
pure and puts the lookup where the station data already is. Every call site must then supply
it; see T2 for the audit.

**D3 — What happens when a station's network is not in the mapping?** *Recommendation: raise.*
The alternative is to fall back to the `None` rules, which is indistinguishable from correct
behaviour and would hide a wiring mistake behind plausible-looking QC results.

**D4 — How does a hydromet extend the deployed rule set? CLOSED: add network-specific rows to
the shared base configuration (owner, 2026-09-25).**

`config.toml` is the authoritative QC rule list for a deployment, and
`docs/spec/config-reference.toml` mirrors its structure. A deployment serving multiple networks
keeps all of their rules in this list. Add a DHM rule as a `network = "dhm"` row beside the
generic (`network = None`) rule it specializes. The network-specific rule replaces the generic
rule for the same `(rule_id, parameter, time_step)`; it carries its own complete thresholds.
Where no network-specific rule exists, the generic rule continues to apply, as specified in the
design sketch above.
Station-specific changes remain in Plan 269's `[[onboarding.station_qc_thresholds]]` surface.

`_deep_merge` replaces arrays wholesale, so a config overlay cannot safely add one QC rule: a
`[[qc_rules.rules]]` array there would replace the base rule array. A version-only `[qc_rules]`
overlay would also change provenance without changing the rule definitions. Network-specific QC
rules belong in the shared base file and its reference copy. Rule changes are made through reviewed
base-config changes; the loader rejects an overlay declaring `[qc_rules]` before merging, while
unrelated overlay settings remain supported. A multi-network deployment must load an explicit
`[qc_rules]` section in its base config rather than relying on the Swiss-only built-in fallback.

**D5 — What do the two QC version fields mean? CLOSED: flags carry their rule's configured version; rows retain the Plan 324 generation marker.**

At current `main`, five flag-producing sites in `services/qc.py` stamp the hard-coded
`_RULE_VERSION = "1.2"` (constant at `:29`); `_apply_frozen_sensor` already uses the configured
`rule.rule_version` (`:152`, assignment at `:203`). T4b makes every `QcFlag.rule_version` identify
the rule that produced that flag, so a single row may correctly contain flags with different
versions when both a network-specific rule and a generic fallback run.

`Observation.qc_rule_version` is a different field. Plan 324 T2 defines it as the observation-QC
generation marker: `"1.2"` for non-water-level rows, `"1.2-datum"` after datum shifting, and
`"1.2-datum-skip"` when datum-dependent rules are skipped (`services/qc_datum.py:18-29`). Keep
that contract; it cannot equal every flag's rule version on a row with mixed rules. Historical
rows retain their stored markers and flags without backfill.

**D6 — How are QC rules updated? CLOSED: reviewed base-config changes (owner, 2026-09-25).**
The base `config.toml` and its reference copy are the rule source. Reject overlays that declare
`[qc_rules]` before merge; unrelated overlay settings remain valid. This permits QC rules to evolve
through reviewed base-config updates without allowing a deployment overlay to silently replace the
shared list or its version. A rule behavior change bumps that rule's `rule_version`; the
configuration-set version is updated in `[qc_rules].version`. `QcFlag` records the producing rule
version when a flag is emitted. `Observation.qc_rule_version` remains the processing-generation
marker, so a passing observation does not currently persist the configuration-set version.

## Tasks

### T1 — The network dimension in the rule model and lookup

**Outcome**: `QcRuleParams` carries `network: str | None = None`; existing rules and constructors
remain generic by default. `rules_for(parameter, time_step, *, network=None)` selects
most-specific-wins; the rule set rejects ambiguity at construction; TOML parsing accepts and
validates the field.
**In**: `src/sapphire_flow/types/domain.py`; `src/sapphire_flow/config/qc_rules.py`;
`config.toml` and `docs/spec/config-reference.toml` (rejecting an unknown field is a parsing
behaviour change and both files must satisfy the stricter parser); `src/sapphire_flow/config/_overlay.py`;
`tests/unit/types/test_qc_rule_selection.py` (new), `tests/unit/config/test_qc_rules.py`, and
`tests/unit/config/test_overlay.py`.
**Out**: no threshold value changes; no change to `ForecastQcRuleSet` (D1); no change to any
call site (T2); no change to overlay array-merge semantics (D4).
**Verification**: `uv run pytest tests/unit/types/test_qc_rule_selection.py tests/unit/config/test_qc_rules.py tests/unit/config/test_overlay.py` asserting:
- a network-specific rule wins over a `None` rule of the same id/parameter/cadence —
  **with the two carrying different `rule_version`s**, so the test discriminates. Selection
  groups by `(rule_id, parameter, time_step)` while uniqueness keys on that plus
  `rule_version` and `network`; if the fixture's two rules share a version, an implementation
  that wrongly grouped by the full uniqueness key would pass while letting a real Swiss and a
  real DHM rule both fire;
- a `None` rule still applies where no specific rule exists;
- an omitted lookup network behaves as `None`, preserving existing direct callers until T2
  threads station mappings through the checker and cadence guard;
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
  does no runtime type validation today); an omitted network parses as `None`, and every
  `_default_swiss_qc_rules()` entry remains generic.
- an overlay declaring `[qc_rules]` is rejected before merge, while an overlay containing unrelated
  settings still loads; cover both a version-only QC overlay and a rules-array overlay.
- both checked-in base rule files agree on the network declarations and rule-set version.
**Pre-change**: a RED test proving that today a **network-specific** rule cannot suppress a
generic one of the same id, parameter and cadence — both return from one `rules_for` call. Not
the earlier formulation ("two rules of the same id, parameter and cadence both return"), which
is a *feature* the precipitation rule set depends on and asserts.

### T2 — Thread network through QC execution and selection reporting

**Outcome**: `Stage1QualityChecker.check` and `resolve_selection` receive the same required
station-to-network mapping, so executed rules and the count used to store `QC_UNCHECKED` agree.
**In**: `src/sapphire_flow/services/qc.py` (`check` and `resolve_selection`); the
`QualityChecker` Protocol in `src/sapphire_flow/protocols/stores.py`; and every caller in
`src/sapphire_flow/flows/ingest_observations.py` (`check` and `resolve_selection`),
`src/sapphire_flow/services/onboarding.py`, and `scripts/dhm_precip/qc_mask.py`;
`tests/unit/flows/test_ingest_observations.py`,
`tests/unit/flows/test_ingest_observations_dhm.py`,
`tests/unit/flows/test_ingest_observations_recheck.py`,
`tests/unit/services/test_onboarding.py`,
`tests/unit/services/test_qc.py`, `tests/unit/config/test_qc_rules.py`,
`tests/unit/scripts/test_dhm_precip_ruleset.py`, `tests/unit/scripts/test_dhm_precip_mask.py`,
`tests/unit/services/test_qc_selection_resolution.py`, and `tests/fakes/test_fakes.py`. The
scheduled flow builds the map from its `StationConfig` records; onboarding builds it from
`station_by_id`. The precipitation mask is exclusively DHM data: bind its network to `"dhm"` for
`rules_for` in the cadence guard
and for both checker passes. The cadence guard must test the effective DHM rules, so a generic
rule replaced by a DHM rule is not reported as a cadence mismatch. `build_dudh_koshi_handover.py`
reaches those passes through `qc_mask._station_mask` and must remain covered.
**Out**: forecast QC remains unchanged. `ForecastOutputQualityChecker` and its
`ForecastQualityChecker` Protocol use `ForecastQcRuleSet`, a separate selection path.
**Verification**: both `Stage1QualityChecker.check` and `QualityChecker.check` require a
keyword-only `station_networks: Mapping[StationId, str]`; `resolve_selection` requires the same
mapping. `inspect.signature` assertions in `tests/fakes/test_fakes.py` prove the Protocol and
implementation parameters match and that the argument is required and keyword-only. A missing
station mapping raises `ConfigurationError`. Re-raise this configuration error before
the per-station catch-all in scheduled ingest and onboarding, so the wiring defect aborts the
affected flow instead of leaving its station un-QC'd. Add caller-level tests proving the ingest
map uses each station's configured network, onboarding uses its resolved `station_by_id` network,
and a missing mapping propagates in both paths. Add a mixed Swiss/DHM test in
`tests/unit/services/test_qc_selection_resolution.py` proving that `check` emits the flags for
each network's selected rules and `resolve_selection` reports the matching runnable-rule counts,
including a skipped-rule case. The precipitation cadence guard and both passes use the fixed DHM
network, and an overridden generic rule is not reported as a mismatch; cover these in
`tests/unit/scripts/test_dhm_precip_mask.py`. Run these focused tests, the whole suite, and the T4
golden equivalence.
**Pre-change**: demonstrate that one mixed Swiss/DHM input cannot currently select a distinct
network rule consistently in `check` and `resolve_selection`. Focused command:
`uv run pytest tests/unit/services/test_qc.py tests/unit/services/test_qc_selection_resolution.py tests/unit/flows/test_ingest_observations.py tests/unit/flows/test_ingest_observations_dhm.py tests/unit/flows/test_ingest_observations_recheck.py tests/unit/services/test_onboarding.py tests/unit/config/test_qc_rules.py tests/unit/scripts/test_dhm_precip_ruleset.py tests/unit/scripts/test_dhm_precip_mask.py tests/fakes/test_fakes.py`.

### T3 — Preserve zero-rule status semantics

**Outcome**: in scheduled ingest, network-aware selection preserves Plan 272's distinction
between a group with applicable rules that pass and a group with no applicable rules. The latter
remains
`QC_UNCHECKED`; it does not become `QC_PASSED` or raise. An explicitly empty rule set supplied by
the DHM precipitation handover remains an intentional no-op. Missing station-to-network mappings
are a separate configuration error under D3.
**In**: the zero-rule status path in `flows/ingest_observations.py`; the existing handover path
through `scripts/dhm_precip/qc_mask.py`; `tests/unit/flows/test_ingest_observations.py` for
scheduled zero-rule statuses and `tests/unit/scripts/test_dhm_precip_mask.py` for the handover
no-op.
**Out**: no new fail-closed policy, no change to Plan 272's `QC_UNCHECKED` routing, and no change
to resolved-rule behavior.
**Verification**: run `uv run pytest tests/unit/flows/test_ingest_observations.py
tests/unit/scripts/test_dhm_precip_mask.py`. A scheduled-ingest group with a non-empty rule set
but no matching parameter/cadence stores `QC_UNCHECKED`; a matched rule set with no flags remains
`QC_PASSED`; and the handover's explicit empty rule set remains a no-op. Onboarding's separate
zero-rule behavior is unchanged by this plan. T2 verifies that a missing station mapping raises
the D3 configuration error and is propagated by both production callers.
**Pre-change**: N/A — these status semantics already exist and are preserved as the network
dimension is added.

### T4 — Equivalence for a Swiss-only deployment

**Outcome**: proof that a deployment serving one network is unaffected.
Added because "byte-identical before and after" had no oracle — a post-change test cannot
compare against a "before" that no longer exists.
**In**: the new `tests/unit/services/test_qc_swiss_equivalence.py`; a golden fixture under
`tests/fixtures/qc/` — a fixed observation series plus **the
rule set loaded from `config.toml`, which is what actually runs**, not
`_default_swiss_qc_rules()`, which `load_qc_rules` never returns while a `[qc_rules]` section
exists (`config/qc_rules.py:251-268`). A fixture built from the defaults function would protect
code that no deployment executes. Expected flags and statuses **generated before T1 lands** and
committed as the baseline. Note "thresholds" are not a returned field — they appear only inside
`QcFlag.detail`, so the comparison is over `detail` text.
**Out**: not a new QC behaviour; purely a regression bar.
**Verification**: `uv run pytest tests/unit/services/test_qc_swiss_equivalence.py` — the Swiss
fixture's flags, statuses and thresholds match the golden artifact, excluding only each flag's
`rule_version` field. T4b owns the configured-version assertion. The row-level
`qc_rule_version` remains the Plan 324 generation marker for the parameter/datum path.
**Pre-change**: N/A — the fixture *is* the pre-change evidence, and it must be generated and
committed first.

### T4b — The flag-version correction (D5)

**Outcome**: each QC flag records the configured version of the rule that produced it. The
row-level `qc_rule_version` remains the Plan 324 observation-QC generation marker; it does not
repeat per-rule versions.
**In**: `src/sapphire_flow/services/qc.py` (replace `_RULE_VERSION` at the five flag sites; the
frozen-sensor path already uses `rule.rule_version`); `tests/unit/api/test_api_stations.py` and
`tests/unit/flows/test_ingest_observations.py` (row-marker behavior). Add mixed-network
coverage: DHM-specific flags and a generic fallback flag on the same observation must each carry
their own rule's version, while the row marker remains the expected Plan 324 value.
**Out**: no threshold change; no change to which rules run.
**Verification**: cover each flag-producing rule and mixed network selection; a rule configured
with version `"2.0.0"` emits that version on its flags even though the row marker remains the
Plan 324 generation value. Assert that ordinary, datum-shifted and datum-skipped rows retain
their respective `"1.2"`, `"1.2-datum"` and `"1.2-datum-skip"` markers. No historical row is
rewritten. The station API continues to use the same response shape but exposes the configured
per-rule version in `qc_flags[].rule_version`; assert that intentional value change in the route
test. Run `uv run pytest tests/unit/services/test_qc.py tests/unit/services/test_qc_selection_resolution.py tests/unit/api/test_api_stations.py tests/unit/flows/test_ingest_observations.py`.
**Pre-change**: a RED assertion that a non-frozen rule configured as `"2.0.0"` emits
`_RULE_VERSION` rather than `"2.0.0"`.

### T5 — Documentation

**Outcome**: the QC selection contract is documented as network-aware; the forecast-QC
asymmetry (D1), the shared-base rule configuration policy and overlay boundary (D4/D6), and the
flag-version provenance contract (D5) are written down rather than left for the next reader to
discover.
**In**: `docs/spec/types-and-protocols.md` and `docs/architecture-context.md` (document the
distinction between per-flag configured rule versions and the observation-QC generation marker);
`docs/touchpoint-maps.md`; `docs/standards/wmo.md` if it states the QC rule contract.
**Out**: no code change.
**Verification**: bounded inspection — every doc statement about QC rule selection names the
network dimension; the forecast-QC exemption, the shared-base rule policy and the distinction
between per-flag rule versions and the row generation marker are stated consistently in the spec
and architecture context. Confirm checked-in
overlays do not declare `[qc_rules]`.
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
  does not add that schema. **Nor does Plan 269, and this note said otherwise until 2026-09-11.**
  Plan 269 delivers per-station thresholds as **onboarding configuration**, matching
  `docs/spec/types-and-protocols.md` § StationQcOverride ("Loaded from station onboarding TOML;
  v1 migrates to DB"); it explicitly out-of-scopes the table, store, migration and grant. **The
  persisted tier is therefore UNOWNED and deferred to v1** — say so rather than pointing at a
  plan that dropped it. The earlier note here also said Plan 268 T7 would construct overrides in
  memory; that is no longer the route either. This plan still owns only which rule an override
  merges into. **How the two mechanisms compose is Plan 269 D2**: 264 selects *which rule*
  applies, 269 adjusts *that rule's thresholds*. The earlier phrasing here — "station beats
  network beats generic" — is **retracted**: an override cannot suppress a rule, and having no
  `rule_version` it merges onto **every** resolved rule sharing its four-part key.
- Any change to a threshold *value* currently in force. (Flag `rule_version` **does** change,
  once and deliberately — D5.)
- Fixing configuration composition so overlays can add rules incrementally (D4).
