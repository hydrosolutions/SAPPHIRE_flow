---
status: DRAFT
created: 2026-09-24
plan: 303
title: DHM subdaily precipitation and temperature QC rules
scope: Add DHM-network observation-QC rules at the verified cadence of the Nepal subdaily precipitation and temperature feeds, so Plan 301 data is actually checked. NOT rainfall parsing/timestamp semantics, Gateway weather-forcing retrieval, changes to Swiss rules, or threshold tightening.
depends_on: [264, 272]
blocks: [301, 315]
related: [268, 272, 301, 315, 318]
blocked_by: ["Plan 301 T1 must settle rainfall amount interval and timestamp/cadence before rule cadence is fixed"]
---

# Plan 303 — DHM subdaily observation QC rules

## Status

**DRAFT.** Plan 272 D4 assigns the cadence-specific QC rows here. The plan must
land before Plan 301 enables rainfall ingestion. It must remain unimplementable
until the completed Plan 272 and Plan 264 network-aware selector are in place,
and Plan 301 T1 has established the canonical rainfall interval and cadence.

## Problem and measured boundary

The checked-in QC configuration currently declares daily (`86400 s`) rules for
`precipitation` and `temperature` only. Plan 301's BIPAD rainfall feed is
subdaily, but its exact interval and timestamp contract is still open. A
subdaily observation group therefore has no matching parameter/cadence rule
until this plan adds one. Plan 272 records that the feed must not become live
before those rules exist.

This is observation QC, separate from the successful raw Recap Gateway retrieval
of ERA5-Land/IFS forcing. It does not add weather-forcing variables to an
observation rule set.

## Decisions

### D1 — Add only network-specific rows for the verified cadence

After Plan 301 T1 confirms the feed's canonical cadence, add DHM-network rules
for `precipitation` and `temperature` at that cadence. Use only the deployed
per-observation rule kinds already present for these parameters:
`range_check` and `gross_outlier`. Do not add `rate_of_change`, `spike`, or
`frozen_sensor` to these parameters without a separate scientific decision.

Use the network dimension delivered by Plan 264 so the rows do not change Swiss
selection. Where the existing broad daily bounds are scientifically applicable
to the same canonical units, carry them over without tightening: precipitation
`0..500 mm`, temperature `-50..50 °C`, and the existing `gross_outlier`
`k_sigma` values. If the confirmed subdaily amount is not represented in those
same canonical units, or if a bound is not defensible for the new interval,
resolve that threshold with CHWRR before implementation; do not copy it by
convenience.

The hourly temperature row is dormant configuration unless a DHM observed
temperature source is actually configured. It does not claim that Plan 301
provides temperature observations.

### D2 — Keep deployed Swiss behavior unchanged

Do not edit the values or versions of Swiss rules. Preserve the Swiss selection
snapshot and run its equivalence regression after adding the DHM rows. Every new
DHM rule row and the changed deployed rule-set version must be explicit in the
configuration and tests; no runtime-only injection, overlay replacement, or
implicit default is acceptable.

### D3 — No ingestion before source meaning and rules agree

Plan 301 must not write canonical precipitation observations until its interval,
unit, timestamp and query-window contract is settled. Plan 303 must not declare
a cadence that was guessed from the raw API timestamps. After implementation,
Plan 301's rainfall feed remains gated until Plan 303's selector and runtime
checks pass.

## Tasks

### T1 — Close the subdaily rule contract

**Outcome:** a short, owner-approved rule table names each parameter, network,
cadence, rule kind, canonical unit and threshold, tied to Plan 301's accepted
source interval.

**In:** Plan 301 T1's rainfall semantics, Plan 264's selection contract,
`config.toml`'s current daily rules, and CHWRR's confirmation where needed.
**Out:** adapter code, invented cadence tolerances, new QC rule kinds, Swiss
threshold changes.

**Verification:** cite the source evidence for the cadence and units, show how
the BIPAD example maps to one canonical interval, and state the exact existing
thresholds reused or the CHWRR decision required. Record whether an observed
temperature feed exists; absence leaves its row dormant and is reported.

**Pre-change:** N/A — source/rule contract work; no code behavior changes.

### T2 — Add DHM subdaily rules and version the deployed set

**Outcome:** the selected QC configuration contains the approved DHM rows and
their exact declared cadence, while Swiss rules remain byte-for-byte equivalent
in their effective selections and flags.

**In:** `config.toml`, focused QC configuration/selection tests, and the
Plan 264 Swiss-equivalence baseline. Update the set version as required by Plan
264 whenever the deployed rule definitions change.
**Out:** code defaults, overlays replacing the entire rule array, threshold
changes to existing Swiss rows.

**Verification:** configuration validation accepts the DHM rows; `rules_for`
selects exactly the intended rules for a DHM series at the confirmed cadence;
the Swiss-equivalence test remains unchanged for Swiss stations; no extra rules
resolve. A rule-set selection test fails if the DHM rows are absent or if they
leak into Swiss selections. Run `uv run pytest tests/unit/config/test_qc_rules.py tests/unit/services/test_qc_selection_resolution.py tests/unit/services/test_qc_swiss_equivalence.py`.

**Pre-change:** the current rule table has daily-only precipitation and
temperature rows, so a synthetic subdaily DHM series selects no checking rules.
Capture that failure before adding the rows.

### T3 — Prove the live-feed gate without publishing observations

**Outcome:** Plan 301 can demonstrate its subdaily observations are checked by
the approved DHM rules before any feed is enabled for scheduled ingestion.

**In:** synthetic wet/dry source examples, focused Plan 301 parser-to-QC tests,
and a staging preflight that reports selection and counts without exposing
values.
**Out:** enabling the live feed, changing stored historical verdicts, exporting
data, or treating an empty result as a passing check.

**Verification:** a non-empty canonical test batch resolves the intended rules;
invalid/missing interval semantics block canonical writes; an unsupported
cadence remains QC_UNCHECKED and cannot be treated as a passed check.
Staging output includes counts and rule IDs/versions only. Run
`uv run pytest tests/unit/flows/test_ingest_observations_dhm.py tests/unit/config/test_qc_rules.py tests/unit/services/test_qc_swiss_equivalence.py`; when Plan 301 T2/T3 lands, include `tests/unit/adapters/test_dhm_rain.py`.

**Pre-change:** N/A — integration and release gate; T2 provides the discriminating
selector regression.

## Exit condition

Plan 303 completes when the reviewed subdaily rule table, implementation,
configuration version, and Swiss-isolation tests all agree. Plan 301 may then
activate its precipitation feed subject to its own acceptance gates.

```json
{
  "phases": [
    {"id": "contract", "tasks": ["T1"], "parallel": false},
    {"id": "rules", "tasks": ["T2"], "depends_on": ["contract"]},
    {"id": "feed-gate", "tasks": ["T3"], "depends_on": ["rules"]}
  ]
}
```
