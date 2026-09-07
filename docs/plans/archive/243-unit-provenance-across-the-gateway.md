---
status: COMPLETE
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

**COMPLETE — merged 2026-09-07 (#262).** Q1 resolved as recommended: the register lives in the
adapter table, beside the `unit`/`convert` fields the code already reads.

Both exit gates met:

1. Every ingested variable now records its assumed source unit, canonical unit, converter and named
   authority in `RECAP_VARIABLES` — plus the D2 radiation trap (`ssr`/`str` are ACCUMULATED J/m²,
   not W/m²) recorded where anyone would add them.
2. No statement anywhere still contradicts the register. The independent Codex review found the
   sweep had missed three copies of one paragraph — it searched for the WORDING ("unconfirmed") and
   not the CLAIM; those said the preservation question was open and the factors an assumption, when
   the first is measured-and-answered and the second is contract-guaranteed. Fixed in all three.

Codex independently verified the load-bearing facts against ECMWF's parameter database: `tp` metres,
`2t` kelvin, `ssr`/`str` accumulated J/m². No other findings.

**What this does NOT do, and it matters:** it makes a unit error ATTRIBUTABLE, not DETECTABLE. If a
number is ever wrong, the register shows what we assumed and who said so — but nothing here will
notice the error. Detection still needs the units pass-through (§ D3, requested upstream and in
progress) or a basin that actually has snow; HRU 12300 is flat 0.0000 in every season and cannot
ground the snow units.

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

**This is our CONTRACT with the Gateway** (owner, 2026-09-07) — not an assumption awaiting
confirmation. Basin-averaging is the only operation it performs on a value, so upstream units reach
us intact by agreement.

**What remains true even so:** a contract breach would be SILENT. If a transformation were ever
introduced — a unit normalisation, an accumulation-to-rate conversion — every assumption here would
break and nothing in the response would show it, because the response carries no units to contradict
us. That is a reason the units pass-through (§ D3) matters, **not** a doubt about the contract — and
it is why a magnitude-based alarm was considered and then cut: it could not tell the two apart.

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

## D3 — upstream: the units pass-through is already asked for

**✅ Units pass-through: ASKED, and in progress** (owner, 2026-09-07). The Gateway has been asked to
preserve the source `units` attribute through extraction; the work is under way on their side.

**This is the real fix.** Once units arrive in the response, checking becomes DIRECT — compare the
declared unit against what we assumed. A magnitude-based alarm was considered as a stopgap and **cut
on review**: the probe sees RAW Gateway values, so a "metres where millimetres expected" rule would
flag CORRECT data, and the pass-through would make it obsolete before it shipped.

*(No second ask. The no-transformation guarantee is our contract, not an open question.)*

**Sequencing consequence:** if the pass-through lands before T2 starts, **skip T2 entirely** and
check the declared unit instead. T2 exists only for the window in which we receive no metadata.

## Open questions the owner owns

- **Q1:** where does the register live — extra fields beside `unit`/`convert` in the adapter table
  (smaller), or a doc table (more readable, drifts more easily)? **Recommendation: the adapter
  table**, since it is what the code reads.
- *(Q2 on where an alarm lives is GONE — there is no alarm. Q3 on scope is CLOSED: the five ingested variables. `ssr`/`str` are recorded as a trap in D2, not
  taken on.)*

## Phases

### T1 — write the register, and retire the statements it contradicts

Record D1's table where the code reads it, with the authority beside each converter. **Citation, not
investigation** — the contract settles every authority. Include D2's `ssr`/`str` warning for whoever
adds radiation.

**Then delete the statements that are now false.** These survived Plan 219's update, which fixed the
per-variable comments but not the surrounding prose:

* `adapters/recap_gateway.py:71-72` — "their Gateway source-unit magnitudes are UNCONFIRMED, so no
  factor is committed in Plan 081 — that is a Plan 082 live-smoke item."
* `adapters/recap_gateway.py:86` — "magnitude factors are deferred to Plan 082 (`convert=None`)."

*(The "owner-supplied, not measured" caveats in `v0-scope.md`, the recap-gateway runbook and the
nepal-forcing runbook were already retired by PR #256; verify rather than assume.)*

**One task. No second phase.**

```json
{
  "phases": [
    {"id": "T1", "parallel": false, "depends_on": []}
  ]
}
```

## Exit gates

1. Every ingested variable has a recorded assumed source unit, canonical unit, converter and **named
   authority**, sitting where the code reads it.
2. No statement anywhere still says snow units are unconfirmed or that factors are deferred.

## Deferred (explicitly not this plan)

A unit column on `weather_forecasts` or `WeatherForecastRecord`; a `Quantity`/dimensional type;
dimensional analysis; converting historical rows (impossible in place — see § Why this is not merely
tidy-up); unit handling for non-Gateway sources (MeteoSwiss, BAFU, CAMELS-CH); blocking ingest on a plausibility
failure; and **any magnitude-based plausibility alarm at all** — cut on review (2026-09-07): the probe
sees RAW Gateway values, so a "metres where mm expected" rule would flag CORRECT data (conversion
happens later in the adapter), and the units pass-through already under way makes magnitude inference
obsolete before it could ship.
