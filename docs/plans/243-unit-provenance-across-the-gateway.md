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

## The load-bearing premise (owner, 2026-09-07)

> The Gateway does **no data transformation except the basin average**. So for `tp` we get exactly the
> unit ERA5-Land and IFS publish — **their documentation is the reference**. For snow forecasts, the
> snow modeller's specification is the reference.

That collapses most of this plan. A spatial average does not change units, so **the authority for
every variable is its UPSTREAM SOURCE**, not the Gateway and not a measurement we have to invent.

**Corroborated:** the first IFS `tp` value measured on 2026-09-07 was `1.144e-05`. In metres that is
0.011 mm over a 3-hourly step — ordinary light precipitation. In millimetres it would be 1.1e-05 mm,
which is not a real quantity. Consistent with raw ECMWF units passing straight through.

**This is our CONTRACT with the Gateway** (owner, 2026-09-07) — not an assumption awaiting
confirmation. Basin-averaging is the only operation it performs on a value, so upstream units reach
us intact by agreement.

**What remains true even so:** a contract breach would be SILENT. If a transformation were ever
introduced — a unit normalisation, an accumulation-to-rate conversion — every assumption here would
break and nothing in the response would show it, because the response carries no units to contradict
us. That is an argument for detection (§ D3), **not** a doubt about the contract.

## D1 — a register of the assumed unit and its AUTHORITY

One table, in one place, for every ingested variable: canonical name, source name, assumed source
unit, our canonical unit, the converter, and the **named authority**. With the premise above, the
authority is now known for all five:

| canonical | source | assumed source unit | ours | converter | authority |
|---|---|---|---|---|---|
| precipitation | `tp` (IFS) / `total_precipitation` (ERA5-Land) | metres | mm | ×1000 | ECMWF parameter documentation |
| temperature | `2t` / `2m_temperature` | kelvin | °C | −273.15 | ECMWF parameter documentation |
| snow_depth | `hs` | metres | cm | ×100 | snow modeller's spec, 2026-09-07 |
| snowmelt | `rof` | mm (**hourly increment**) | mm | identity | snow modeller's spec, 2026-09-07 |
| swe | `swe` | mm (state) | mm | identity | snow modeller's spec, 2026-09-07 |

Nothing here is "none, inherited" any more — **T1 is citation, not investigation.**

## D2 — the radiation trap, before anyone ingests it

`ssr` and `str` are already in the standing probe and are the obvious next additions. **ECMWF
publishes both as ACCUMULATED J/m², not instantaneous W/m²** — a distinction of the same kind as
`rof` being an hourly increment rather than a rate. Adding them without settling that is exactly the
mistake this plan exists to prevent, so it is recorded here even though ingesting them is out of
scope.

## D3 — make a silent change DETECTABLE, cheaply

Since the Gateway sends no metadata, detection can only be **magnitude-based**: a plausibility range
per canonical variable, alarming when a series sits far outside it. Daily precipitation of 5e-05 mm
means metres arrived where millimetres were expected.

**Deliberately crude, and it must NOT block ingest** — an alarm, not a gate.

⚠️ **Two blind spots, stated rather than engineered around:** an all-zero series (snow at 12300)
passes any range; and two candidate units differing by a factor near 1 are not separable by magnitude.

## D4 — upstream: the units pass-through is already asked for

**✅ Units pass-through: ASKED, and in progress** (owner, 2026-09-07). The Gateway has been asked to
preserve the source `units` attribute through extraction; the work is under way on their side.

**This changes what D3 is for.** Once units arrive in the response, checking becomes DIRECT — compare
the declared unit against what we assumed — and the magnitude alarm becomes redundant. **So D3 is a
BRIDGE, not a destination: keep it cheap, and expect to retire it.** Do not build anything for D3
that would be painful to delete.

*(No second ask. The no-transformation guarantee is our contract, not an open question.)*

**Sequencing consequence:** if the pass-through lands before T2 starts, **skip T2 entirely** and
check the declared unit instead. T2 exists only for the window in which we receive no metadata.

## Open questions the owner owns

- **Q1:** where does the register live — extra fields beside `unit`/`convert` in the adapter table
  (smaller), or a doc table (more readable, drifts more easily)? **Recommendation: the adapter
  table**, since it is what the code reads.
- **Q2:** does the plausibility alarm belong in the ingest path, or in the standing Gateway probe
  (which already runs every 3 h and stores nothing)? **Recommendation: the probe** — it cannot break
  ingest there, and it already has the data.
- *(Q3 on scope is CLOSED: the five ingested variables. `ssr`/`str` are recorded as a trap in D2, not
  taken on.)*

## Phases

### T1 — write the register (D1)
Record D1's table where the code can see it, with the authority beside each converter. **Citation,
not investigation** — the owner's premise settles every authority. Include D2's radiation warning for
whoever adds `ssr`/`str`.

### T2 — plausibility alarm (D3, gated on Q2) — **SKIP IF the Gateway's units pass-through lands first**
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
