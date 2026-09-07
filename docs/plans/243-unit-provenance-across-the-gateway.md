---
status: DRAFT
created: 2026-09-07
plan: 243
title: Units across the Gateway boundary — we receive none, so every unit we use is an unchecked assumption
scope: Establish, in one place, the unit we assume for every variable we ingest and where that assumption comes from; verify each against an authoritative source; and make a silent unit change DETECTABLE, given the Gateway sends no units at all. Register + plausibility check. NOT a units framework, NOT a schema change.
depends_on: [219]
blocks: []
source: 2026-09-07 — owner asked whether we get units from the Gateway for all variables and what happens to them on conversion. Measured the same day: NO variable carries a unit, for any endpoint.
---

# Plan 243 — the Gateway sends no units, for anything

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ DO NOT OVER-ENGINEER — binding on this plan AND on every reviewer

The obvious response to "we have no units" is a units system: a unit column, a quantity type, a
dimensional-analysis layer. **All of that is out of scope.** This plan writes down what we assume,
checks those assumptions once against an authority, and adds a cheap alarm for silent drift.

1. **"No findings" is a complete and welcome review.** Do not manufacture findings.
2. A finding must name a **CONCRETE DEFECT** with `file:line`.
3. **No new apparatus** — no unit column, no `Quantity` type, no dimensional analysis, no registry
   service, no config system.
4. **Do not widen scope** — § Deferred stays deferred.
5. **Adding length is a cost. Prefer DELETING to adding.**

## What we measured (2026-09-07, not inferred)

Every Gateway response, for every endpoint tried, returns exactly three columns —
`g_123` (the value), `source`, `source_run`:

| endpoint | `DataFrame.attrs` | any unit column | first value |
|---|---|---|---|
| `ifs_forecast` `tp` | empty | none | 1.144e-05 |
| `snow.forecast` `hs` | empty | none | 0 |
| `era5_land_reanalysis` `total_precipitation` | empty | none | 0.0 |

**No units, no CF metadata, nothing.** The snow modeller confirmed his source NetCDFs carry full CF
metadata (`units`, `long_name`, `cell_methods`) — so the units exist at source and are **stripped by
the Gateway's extraction to parquet**. He independently flagged the same possibility.

**Consequence: every unit in `RECAP_VARIABLES` is an out-of-band assumption with nothing in the data
to check it against.** Today that is five variables (precipitation, temperature, snow_depth,
snowmelt, swe) and grows with each one we add — `ssr`/`str` are already probed and would be next.

## Why this is not merely tidy-up

A unit error is **silent, plausible-looking, and permanent**:

* Nothing fails. The values are numbers of the right shape.
* **It cannot be repaired by re-running.** The store ignores repeats and `value` is not part of the
  natural key (`store/weather_forecast_store.py`, `db/metadata.py`), so corrected rows are dropped.
* The 12300 test basin **cannot** reveal a snow-unit error — snow depth is flat 0.0000 there in every
  season (measured, Plan 219). The one basin we watch is blind to it.

## D1 — a written register of what we assume, and on whose authority

One table, in one place, for every ingested variable: canonical name, source name, **assumed source
unit**, our canonical unit, the converter, and **the authority for the assumption** (a document, a
person, a measurement — or "none, inherited"). Anything whose authority is "none" is a known
unknown rather than a silent one.

Where it lives is an owner call (§ Q1) — the adapter table already carries `unit` and `convert`, so
adding the authority beside them may be enough. **Prefer that over a new file.**

## D2 — verify each assumption ONCE against an authority

Snow is done: the modeller's specification (2026-09-07) confirms `swe: mm`, `hs: m`, `rof: mm`, and
that our aggregation matches his `cell_methods`. Precipitation and temperature come from ECMWF IFS /
ERA5-Land, whose units are published; confirm and cite, rather than trusting that our `m→mm` and
`K→°C` factors were once checked.

**Expected outcome: most assumptions are correct.** The value is the citation, not the correction.

## D3 — make a silent change DETECTABLE, cheaply

Since the Gateway sends no metadata, detection can only be **magnitude-based**: a plausibility range
per canonical variable, checked where data arrives, alarming when a series sits far outside it. Daily
precipitation of 5e-05 mm means metres arrived where millimetres were expected.

**Deliberately crude, and it must NOT block ingest** — an alarm, not a gate. A plausibility rule that
rejects real data is worse than the problem.

⚠️ **Two known blind spots, to be stated rather than engineered around:** an all-zero series (snow at
12300) passes any range; and a variable whose two candidate units differ by a factor near 1 will not
be separated by magnitude.

## D4 — ask the Gateway to carry units through

The cheapest real fix is upstream: if the extraction preserved the source `units` attribute, every
assumption here becomes checkable at ingest instead of asserted. **Not a code task** — an ask, and
the modeller has already raised it from his side. Worth pairing with the existing open thread.

## Open questions the owner owns

- **Q1:** where does the register live — extra fields beside `unit`/`convert` in the adapter table
  (smaller), or a doc table (more readable, drifts more easily)? **Recommendation: the adapter
  table**, since it is what the code reads.
- **Q2:** does the plausibility check (D3) belong in the ingest path, or in the standing Gateway
  probe (which already runs every 3 h and stores nothing)? **Recommendation: the probe** — it cannot
  break ingest there, and it already has the data.
- **Q3:** scope now — the five variables we ingest today, or also `ssr`/`str`, already probed and
  likely next? **Recommendation: the five, plus a note for whoever adds radiation.**

## Phases

### T1 — write the register (D1, D2)
Record the assumed unit and its authority for all five ingested variables. Cite ECMWF documentation
for precipitation/temperature, the modeller's spec for the three snow variables.

### T2 — plausibility alarm (D3, gated on Q2)
One expected range per canonical variable; alarm on a series far outside it. Never blocks ingest.
Tests: a metres-where-mm-expected series alarms; an all-zero series does NOT alarm (documented blind
spot, asserted so it is not mistaken for coverage).

### T3 — docs
The register, and the fact that the Gateway carries no units — the reason any of this exists.

```json
{
  "phases": [
    {"id": "T1", "parallel": false, "depends_on": []},
    {"id": "T2", "parallel": false, "depends_on": ["T1"]},
    {"id": "T3", "parallel": false, "depends_on": ["T1", "T2"]}
  ]
}
```

## Exit gates

1. Every ingested variable has a recorded assumed unit AND a named authority; anything without one is
   explicitly marked unknown.
2. A metres-where-millimetres-expected series raises an alarm in a test, and ingest still completes.
3. The all-zero blind spot is asserted by a test, so it is documented behaviour rather than a gap.

## Deferred (explicitly not this plan)

A unit column on `weather_forecasts` or `WeatherForecastRecord`; a `Quantity`/dimensional type;
dimensional analysis; converting historical rows (impossible in place — see § Why this is not merely
tidy-up); unit handling for non-Gateway sources (MeteoSwiss, BAFU, CAMELS-CH); and blocking ingest on
a plausibility failure.
