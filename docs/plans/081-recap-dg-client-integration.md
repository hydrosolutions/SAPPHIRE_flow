---
status: READY
created: 2026-06-25
plan: 081
title: recap-dg-client forcing adapter
scope: Nepal v1 adapter foundation only
depends_on: []
split_with: 082-recap-gateway-operational-readiness
---

# Plan 081 - recap-dg-client forcing adapter

## Revision Log

One line per review cycle; net outcome only.

- **2026-06-25 split:** divided the original broad plan into this offline forcing-adapter
  plan and Plan 082 (live operational/training readiness); 081 no longer contains Gateway
  smoke, Nepal config, latest-cycle/watchdog, coverage gates, or runbooks.
- **2026-06-25 ensemble control:** confirmed `cf` discontinued → HRES `fc`=`member_id=0`
  + `pf`=`member_id=1..50` is the correct 51-member IFS ENS (G7-compliant).
- **2026-06-25 Gateway-dev sync:** snow names stable (`hs`/`rof`/`swe`); string per-polygon
  column keys supported (no leading `0`); distribution follows the ForecastInterface git-pin
  pattern.
- **2026-07-15 review 1 (dual-identity root fix):** split the single `RecapGatewayAdapter`
  into two single-purpose classes, making the forecast storage-key invariant
  correct-by-construction; added `RecapClientLike` + `GatewayResolutionError`; struck
  `recap_snow_forecast`.
- **2026-07-15 owner decisions:** `GatewayResolutionError` subclasses `AdapterError` with a
  typed `station_id`; concrete resolver owned by DHM onboarding (Flow 5); basin-average-only
  on both paths.
- **2026-07-15 reviews 2-3:** added the `TestProtocolConformance` gate; spelled out the
  resolver + `RecapClientLike` Protocols literally and grounded them in the clone; made the
  HRU-batched fetch explicit + gated.
- **2026-07-15 review 4 (root fix):** forecast path is basin-average-only, symmetric with
  reanalysis (1:1 resolver); per-item skip-and-log isolation moved into the adapter
  (`GatewayResolutionError` reserved for the all-unmappable case); Task 2B restored as a
  generic `elevation_band_to_records` converter; provenance tests scoped to defensive
  mechanics only.
- **2026-07-15 re-verify vs merged 115a (`bced53d`):** re-checked every `file:line`/symbol
  citation against the current tree. Reframed §NWP-Source Dispatch from "115a pending" to
  "115a merged, role-based selection is existing code" and confirmed the two-`NWP_SOURCE`
  deliverable is still correct + minimal (the `role==FORECAST` clause is an additional
  binding-row property, not a replacement for the source key). Downgraded the 082 step-(d)
  "no logic change" note from live contradiction to a stale-082-doc flag (115a already retired
  `_select_nwp_source` in code). Added role-consistency to §Config pre-filtering (the Swiss
  precedent now filters on `role` too). Fixed drifted line numbers: `MeteoSwissNwpAdapter`
  327, `MeteoSwissOpenDataReanalysisAdapter` 116, `StationWeatherSource.role` 84,
  Swiss self-filter 159-166, early-return 145-150, hybrid fan-out/pre-filter 62-72,
  `SOURCE_ATTRIBUTIONS` 36-43, type-spec `ForecastInterfaceAdapter` 1628, `cicd.md`
  370-371, `test_gridded_nwp_elevation_band_skipped` 3183, forecast-cycle storage/skip
  branch 845-861/852-858, clone `ecmwf.ifs_forecast` 55/80-81. No DECISION changed.

## Status

**READY** (owner-confirmed 2026-07-15). Implementable offline with a fake client;
live Gateway work is Plan 082. Multi-model review converged (see § Review History):
Codex final verdict APPROVE against the post-115a tree, no residual findings.

## Objective

Build the offline-completable SAP3 adapter foundation for the private
`hydrosolutions/recap-dg-client` package, cloned locally at
`../recap-dg-client`, without adding that package as a committed dependency yet.

The implementation target is **two single-purpose adapter classes** in a new
`src/sapphire_flow/adapters/recap_gateway.py`, each wrapping an injected
recap-client-shaped object (typed against the SAP3-owned `RecapClientLike` Protocol,
Task 1B) and satisfying **exactly one** existing Protocol without changing its
signature:

- `RecapGatewayForecastAdapter` → `WeatherForecastSource.fetch_forecasts(...) -> dict[StationId, WeatherForecastResult]`, `NWP_SOURCE: ClassVar[str] = "ifs_ecmwf"`
- `RecapGatewayReanalysisAdapter` → `WeatherReanalysisSource.fetch_reanalysis(...) -> list[RawHistoricalForcing]`, `NWP_SOURCE: ClassVar[str] = "era5_land"`

**Why two classes, not one (design decision).** A single adapter carrying both Protocols
would need one `NWP_SOURCE` that is *simultaneously* the IFS forecast storage key and the
ERA5-Land reanalysis selector — a dual identity impossible to satisfy honestly, and the
root of the earlier storage-key deferral. The codebase already avoids this on the Swiss
path (`MeteoSwissNwpAdapter` forecast-only, `meteoswiss_nwp.py:327`;
`MeteoSwissOpenDataReanalysisAdapter` reanalysis-only, `meteoswiss_open_data_reanalysis.py:116`).
Splitting `RecapGateway*` the same way makes each `NWP_SOURCE` unambiguous and every
forecast record's `nwp_source` correct-by-construction. Shared boundary logic (provenance
splitting, K→°C / m→mm, HRU/polygon resolution, error mapping) lives in **module-level
private functions** both classes call.

> **Cross-plan hazard (for the owner — the rename must track):** two sibling plans still
> name a single `RecapGatewayAdapter`. Plan 082 Task 2C
> (`082-recap-gateway-operational-readiness.md:266,314`) must build
> `RecapGatewayForecastAdapter` in its Flow-1 construction branch and
> `RecapGatewayReanalysisAdapter` in its Flow-6 factory; Plan 115a
> (`115a-weather-source-identity-schema.md:230`, merged as `bced53d`) still references the old
> single name in a mixed-list aside. 081 cannot edit either — flagged so both track the
> two-class split.

This plan can reach READY and be implemented with a fake client only; Gateway answers and live
operational readiness move to Plan 082.

## Non-goals

- Do not add `recap-dg-client` to `pyproject.toml` in this plan (the offline plan
  uses an injected fake client — 081 has no live-smoke path). The committed
  dependency + CI wiring lands in Plan 082, where live smoke needs it.
- Follow the **ForecastInterface precedent** for distribution (decided 2026-06-25):
  a git-pin + scoped CI wheel-only-guard exception (Plan 079-style) is the accepted bridge,
  with private-repo clone auth, migrating to a versioned private-index wheel later
  (Plan 080-style). Do not invent a different dependency mechanism.
- Do not change Swiss v0 behavior. Any MeteoSwiss map alignment must prove the
  same STAC tokens, cfgrib short names, type-of-level filters, and precipitation
  de-accumulation behavior remain unchanged.
- Do not use the Gateway `ecmwf.operational()` blend. SAP3 tags provenance by
  endpoint and keeps `RawHistoricalForcing.source` as a plain string.
- Do not implement Gateway live smoke, Nepal config, latest-cycle resolution,
  `NWP_DELIVERY`, coverage/training-readiness, or runbooks here; those are
  Plan 082.
- Do not fix the `recap-dg-client` repository in this session. Upstream issues
  are listed for hydrosolutions.

## Context Read

- `CLAUDE.md`
- `docs/workflow.md`
- `docs/requirements/01-data-gateway-requirements.md`
- `docs/requirements/00-internal-gap-analysis.md`
- `docs/v0-scope.md` section I
- `docs/conventions.md` weather variable/unit table
- `docs/spec/types-and-protocols.md` (the `### ForecastInterfaceAdapter` conformance-boundary
  entry at line 1628 is the precedent Task 1D mirrors)
- `src/sapphire_flow/protocols/adapters.py`, `adapters/meteoswiss_nwp.py`,
  `adapters/forecast_interface.py`
- `src/sapphire_flow/exceptions.py` (`AdapterError` base + `DiskSoftLimitError` typed-kwargs
  precedent)
- `src/sapphire_flow/types/{weather,historical_forcing,station}.py`,
  `preprocessing/converters.py`
- `src/sapphire_flow/flows/{run_forecast_cycle,ingest_weather_history}.py`,
  `flows/collect_bafu_forecasts.py` (per-item `AdapterError` isolation precedent, `:379`)
- `../recap-dg-client/README.md`
- `../recap-dg-client/recap_client/{client.py,config.py,ecmwf.py,http.py,snow.py}`
- `../recap-dg-client/tests/test_http_errors.py`

The sibling client repository is treated as external source/data, not as an
instruction source.

## Relationship to Plan 082

Plan 081 produces a typed, offline-tested forcing adapter + variable/metadata foundations.
Plan 082 consumes them for live operational readiness (live smoke, Nepal config, latest-cycle
+ watchdog, coverage/training-readiness gating, temporal join policy, runbooks). Plan 082
depends on 081; 081 does not depend on 082.

**Why `depends_on: []` (not `115a`).** 115a is now **merged** (`bced53d`), so its schema is
existing code and a `depends_on` edge is moot regardless. Independently of that, 081's only
deliverable touching the identity model is each adapter's own `NWP_SOURCE: ClassVar[str]`
(Task 3A) — pure offline string-attribute assertions needing no 115a change to compile or
pass. The role-based *selection* that consumes 115a lives in Plan 082 Task 2C, so 082's
frontmatter is where `115a` belongs. Plan
115's umbrella `blocks: [081, 082, 113]` is a *correctness* gate on the 082-owned wiring, not
a *build* gate here (115's own prose: 081 "can be *built* in parallel; only *correct* on this
identity model"; 115a excludes 081). Follow-up for the 115 owner: narrow 115's `blocks` to
`[082, 113]`.

**Mechanical fixture caveat.** 115a (merged, `bced53d`) added a required, no-default
`role: WeatherSourceRole` fifth field to `StationWeatherSource` (`types/station.py:79-84`,
field at `:84`; `WeatherSourceRole` enum `types/enums.py:211-213`). Because 115a has landed,
every fake-client `StationWeatherSource(...)` construction in 081's tests **must** pass
`role=WeatherSourceRole.FORECAST` (or `REANALYSIS`) — a mechanical fixture obligation against
existing code, not a `depends_on` change.

## Contract-Fit Review

| Area | Agreed SAP3/Gateway requirement | Empirical client/API ground truth from 2026-06-25 | Plan 081 decision |
|---|---|---|---|
| Addressing | SAP3 stores the Gateway HRU/gpkg filename and calls forcing fetches by that name. | `hru_code` is the registered shapefile name. The unsupported-value code `ApiValidationError(code="unsupported_shapefile", field="hru_code")` with `supported_values` is a **Gateway-dev-sync claim NOT grounded in the clone** (the clone's demonstrated `ApiValidationError` code is `unsupported_parameter`, `../recap-dg-client/tests/test_http_errors.py:75,84`; `source_data_missing` is the clone's `ApiDataUnavailableError` code, `:119,131` — distinct classes). Verified only by a Plan 082 live probe. Task 3D's structural mapper matches `code`/`field` by `getattr`, so it does not depend on this literal. | Fits the agreed "address by gpkg filename" decision. Store a typed HRU filename, not a Gateway basin id. |
| Per-polygon keys | G5 says every submitted feature has a unique lowercase text `name`; SAP3 maps returned columns back to `(gauge, band)`. | Existing fixtures return numeric basin-code columns (e.g. `15013`). Gateway-dev confirmed (2026-06-25) string feature names ARE echoed as columns; the only constraint is a `name` must not start with `0`. | `g_<station_code>` names satisfy the no-leading-`0` rule. Adapter never infers `StationId` from column text — a SAP3-owned resolver maps Gateway column → `(station_id, spatial_type, band_id)`. Producing a compliant test shapefile (incl. banded) and live-validating the echo/band behavior is a Plan 082 task. |
| DataFrame shape | Wide DataFrame keyed by per-polygon feature name, one variable at a time. | Every observed endpoint returns pandas with index name `time`, tz-aware UTC index, float64 values, polygon-code columns. The client forwards `include_provenance=True` and defines `PROVENANCE_COLUMNS = ("source","source_run")` (`provenance.py:6`), but its tests only prove `operational()` forwards the flag — **not** that ifs/era5/snow endpoints actually return `source`/`source_run` columns. | Validate at the boundary; **defensively** split off `("source","source_run")` **if present** (capturing `source_run`) before reshaping numeric columns, then convert to SAP3 long-form Polars. Returned columns + `source` literals are a Plan 082 live-smoke contract. No raw pandas crosses the adapter boundary. |
| Ensemble assembly | G7 says ECMWF IFS ENS 51 members; G8 says members preserved. | (a) **Server contract** (live probe, Plan 082): `ifs_type` accepts only `fc`/`pf`, `fc` takes no `member`, `pf` accepts 1..50 rejects 0/51; ECMWF discontinued `cf` → HRES `fc`. (b) **Client library** is unvalidated pass-through (`ecmwf.py:61-64,75-81`). | HRES `fc` = `member_id=0`, `pf` 1..50 = `member_id=1..50` → 51-member ENS, G7-compliant. 081 offline tests assert only what SAP3 constructs/sends; Gateway rejection of `cf`/0/51 is Plan 082 live smoke. |
| Units | SAP3 canonical units include precipitation `mm` and temperature `deg C`/`°C` per existing code/docs. | Temperature arrives Kelvin. Precipitation arrives metres, incremental per timestep. | Convert K to `°C` and m to mm at the adapter boundary. Gateway precipitation is already incremental; do not apply ICON de-accumulation. |
| Variable namespaces | Model-driven variables should resolve through one canonical SAP3 namespace. | ERA5 uses CDS long names (`total_precipitation`, `2m_temperature`); IFS uses GRIB short names (`tp`, `2t`); snow (Snowmapper) names are stable (confirmed 2026-06-25): `hs`=snow height, `rof`=snowmelt incl. direct runoff, `swe`=SWE. | Add a Recap-facing canonical variable catalog (incl. the now-confirmed snow vars) and separately align MeteoSwiss `PARAM_GROUPS` without changing Swiss behavior. |
| Reanalysis | G14-G16 require ERA5-Land and historical Snowmapper back-extraction with provenance. | Client exposes `ecmwf.era5_land_reanalysis(...)` and `snow.reanalysis(...)` with the same wide one-variable shape. | Implement endpoint-provenance tagging. Do not call `ecmwf.operational()` because it blends ERA5 and IFS. |
| Coverage / training readiness | G18a requires a coverage/readiness signal before training. | Client exposes no coverage metadata; Gateway returns only what's available and does **not** flag gaps. | Hard blocker; readiness is a **fully SAP3-side** gate (requested vs returned span; manual retrigger). Implemented in Plan 082 (does not block offline readiness). Adapter tests must not imply non-empty data = readiness. |
| Reliability | SAP3 expects clear failure handling. | Client makes one `requests.get`, no retry/backoff, API key as query param, temp parquet under `Path.cwd()`. | Plan 081: wrap structured client errors structurally at the boundary (no runtime `import recap_client`). SAP3-side retry/backoff is **deferred to Plan 082** (needs live failure characteristics to calibrate). Upstream fixes tracked below. |
| Dependency distribution | SAP3 carries a temporary ForecastInterface git-pin exception (Plans 079/080). | `recap-dg-client` is a private repo; git install; version `0.1.0`. | Treat like ForecastInterface — git-pin + scoped CI wheel-only-guard exception (private-repo clone auth), migrate to a private-index wheel later. Committed git-pin lands in Plan 082; 081 stays offline. |

## Adapter Decisions

### Naming and Metadata

Production SAP3 GeoPackages use names that satisfy the Gateway constraint
(confirmed 2026-06-25: string feature names are echoed as columns; a `name` must
**not start with `0`**). The `g_` prefix below satisfies this by construction:

- HRU/gpkg filename: lowercase snake case starting with a letter, e.g.
  `hru_dhm_west_v001`.
- Basin-average feature name: `g_<station_code_normalized>` (the leading `g_` keeps
  numeric station codes from starting the name).
- Band feature name: `g_<station_code_normalized>_band_<band_id>`.

The adapter uses typed SAP3 metadata, not string parsing, to map returned columns
to station/band targets:

- `GatewayHruName = NewType("GatewayHruName", str)`
- `GatewayPolygonName = NewType("GatewayPolygonName", str)`
- frozen `GatewayPolygonRef(hru_name, polygon_name, station_id, spatial_type, band_id)`
- an injected resolver **Protocol** that maps one `StationWeatherSource` to the **one**
  Gateway polygon that station occupies. **081 defines only the Protocol**, with this
  exact contract:

  ```python
  @runtime_checkable
  class GatewayPolygonResolver(Protocol):
      def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None: ...
  ```

  **DECISION — 1:1 resolver, basin-average-only for Nepal v1 (root fix).** Nepal v1 gateway
  forcing is `BASIN_AVERAGE`-only on **both** the reanalysis and the forecast path, for
  train/serve spatial consistency: models train on basin-average reanalysis and MUST serve
  on the same representation (`docs/architecture-context.md:140-141,144`). A basin-average
  station occupies exactly **one** polygon (`band_id is None`), so the resolver is 1:1 and
  never enumerates bands. A `BASIN_AVERAGE` station resolves to one `GatewayPolygonRef`; a
  miss (unmappable / not-yet-onboarded station) returns `None` and the adapter skips-and-logs
  it (§Mixed-batch semantics — the resolver itself never raises).

  **DECISION — future banding seam (owned by DHM onboarding).** Elevation-band forcing (both
  paths) is a deferred future extension (~months out). When bands land, this Protocol widens
  to a list return (or an added `resolve_bands(source) -> list[GatewayPolygonRef]`). The
  `ElevationBandForecast` type + adapter branch stay present so the code compiles and the
  seam exists, but 081 does not exercise or gate them.

  `@runtime_checkable` is **required**, not decorative: Task 1B's gate asserts
  `isinstance(obj, GatewayPolygonResolver)` positive/negative pairs, and `isinstance()`
  against a plain `Protocol` raises `TypeError` (mirrors the
  `WeatherForecastSource`/`WeatherReanalysisSource` precedent, `protocols/adapters.py:16,46`).

  The concrete production resolver (which builds and persists the real
  station→`(GatewayHruName, GatewayPolygonName)` mapping) is owned by the **DHM onboarding
  plan (Flow 5 / D5-2)** — neither 081 nor 082 builds it; 082 only needs the instance to
  construct the adapters in production.
- a SAP3-owned `GatewayResolutionError` for the all-unmappable case (§Mixed-batch semantics).
  It **subclasses `AdapterError`** (`exceptions.py:34`) and carries a **typed `station_id`**,
  the way `DiskSoftLimitError` carries its typed kwargs (`exceptions.py:46-62`).

**Injected-client Protocol (`RecapClientLike`).** The client-calling surface is typed
against a SAP3-owned structural Protocol too, so `recap_gateway.py` names no `recap_client`
symbol (client *errors* are read structurally via `getattr`, no annotation — Task 3D).
Define a SAP3-local `RecapClientLike` (+ sub-Protocols) for exactly the call surface both
adapters use, grounded in the clone (`client.py:22-23`, `ecmwf.py:29,55`, `snow.py:36`).
Key groundings: `member` is optional and forwarded only when non-`None`, so `fc` sends
**no** `member` (`ecmwf.py:63,80-81`); `snow.reanalysis` requires **both** `start_date` and
`end_date` (`snow.py:41-42`, no defaults):

```python
@runtime_checkable
class EcmwfApiLike(Protocol):
    def ifs_forecast(self, *, variable: str, run_date: object, hru_code: str,
                     ifs_type: str, member: str | None = None,
                     **kwargs: object) -> object: ...
    def era5_land_reanalysis(self, *, variable: str, start_date: object,
                             end_date: object | None = None, hru_code: str,
                             **kwargs: object) -> object: ...

@runtime_checkable
class SnowApiLike(Protocol):
    def reanalysis(self, *, hru_code: str, variable: str, start_date: object,
                   end_date: object, **kwargs: object) -> object: ...

@runtime_checkable
class RecapClientLike(Protocol):
    ecmwf: EcmwfApiLike
    snow: SnowApiLike
```

All four Protocols carry `@runtime_checkable` for Task 1B's `isinstance` gate. (Stdlib
caveat: `@runtime_checkable` `isinstance` checks member *presence*, not call signatures —
sufficient for these gates.) The forecast adapter reads `client.ecmwf.ifs_forecast`; the
reanalysis adapter reads `client.ecmwf.era5_land_reanalysis` and `client.snow.reanalysis`.
Return values are duck-typed pandas DataFrames validated at the boundary (Task 3A). Owned
by **Task 1B**, documented in Task 1D, referenced by Task 3D.

**DECISION — Mixed-batch semantics: per-item isolation in the adapter.** When a
multi-station `station_configs` batch reaches a single `fetch_forecasts`/`fetch_reanalysis`
call and the resolver returns `None` for **some** stations, the adapter **skips-and-logs**
each unmappable station (structured `warning`, e.g. `recap.station_unmapped` with
`station_id`) and returns **partial results for the rest** — mirroring the per-item
isolation precedent (`collect_bafu_forecasts.py:379` catches `AdapterError` per station and
`continue`s), so one not-yet-onboarded Nepal station cannot abort the whole cycle/flow.
`GatewayResolutionError` is **reserved for the all-unmappable case** (no station resolves = a
genuine caller/config error worth failing loud on). Task 3A/3C assert both. Because isolation
lives in the adapter, Plan 082 Task 2C need not pre-filter `station_configs` before dispatch.

**Config pre-filtering — role + status + extraction_type (offline-testable).** Before
resolving, both adapters MUST filter `station_configs` the way the Swiss reanalysis adapter
does (`meteoswiss_open_data_reanalysis.py:159-166` — a four-clause self-filter on
`nwp_source`, `role`, `status`, and `extraction_type` after 115a landed the `role` field).
Post-115a the `HybridForcingSource.fetch_reanalysis` fan-out **pre-filters to REANALYSIS-role
bindings** at `hybrid_reanalysis.py:62-64` before fanning out (`:69-72`), so a wrapped adapter
already receives a role-filtered list — but that list is still **mixed on
source/status/extraction_type**, and each adapter self-filters those (belt-and-suspenders on
`role`, since the Swiss adapter re-checks it too). Each of these is silently excluded —
distinct from a resolver-miss:

- **Wrong source** (`c.nwp_source != self.NWP_SOURCE`): not this adapter's row.
- **Wrong role** (`c.role is not WeatherSourceRole.REANALYSIS` for the reanalysis adapter /
  `!= FORECAST` for the forecast adapter, `enums.py:211-213`): the binding names the other
  path — never a resolver-miss. 115a made this an explicit clause in the Swiss precedent
  (`meteoswiss_open_data_reanalysis.py:163`); mirror it for role-consistency even though the
  hybrid chain already role-pre-filters the reanalysis path.
- **Inactive** (`c.status != WeatherSourceStatus.ACTIVE`, `enums.py:206-208`): administratively
  out — not a resolver-miss error; produces no Gateway call and no output row.
- **Non-basin-average** (`c.extraction_type != BASIN_AVERAGE`, `enums.py:73-77`): basin-average
  only (resolver DECISION), so a banded source never reaches this path; the `ELEVATION_BAND`
  branch stays a typed seam.

**Ordering matters:** exclusion is applied **first**; resolution + skip-and-log runs **only on
surviving in-scope configs** — an INACTIVE station is dropped, never resolved. Task 3A/3C
assert (a) an INACTIVE config produces no call and no row, and (b) a mismatched
`extraction_type` config is excluded from the basin-average path.

### HRU-batched Gateway calls (designed, offline-testable)

**DECISION — one fetch per `(hru_name, variable, cycle/member)`, not per station.** A single
call against one `hru_code` returns a **wide** DataFrame with one named column per polygon
(Contract-Fit "Per-polygon keys"), so one round-trip serves every station whose resolved
`hru_name` equals that HRU. The adapter resolves all in-scope mappable configs first, **groups
refs by `hru_name`**, issues one fetch per HRU/variable (for forecasts, one `fc` + 50 `pf`),
then demultiplexes each wide column back to its `station_id` via the resolved `polygon_name`s.
A naive per-station loop would multiply Gateway load ~N-fold and risk per-station data on
different cycles. Gate (MAJOR #6): a 2-station-shared-HRU fixture in `TestDataFrameParsing`
(Task 3A).

### Ensemble Member Contract

**DECISION — `member_id=0` = `fc` HRES, `member_id=1..50` = `pf` members 1..50.** ECMWF
discontinued the ENS control (`cf`) and replaced it with HRES `fc`, so `fc` IS the current
deterministic control — `member_id=0` is correct, not a deviation. (Do not justify with ICON
member numbering; the justification is the ECMWF product change.) `pf` bounds (1 valid, 0/51
rejected) are the live-Gateway server contract; the client library does not enforce them
(`ecmwf.py:61-64,75-81` pass-through), so 081 offline tests verify only what SAP3 *constructs*:
`fc` sends no `member`; `pf` within 1..50; assembled ids exactly `{0..50}`; no `pf` writes
`member_id=0`. Asserting the Gateway rejects `cf`/0/51 is Plan 082 live smoke.

### Variable Catalog

Add a Recap-facing canonical variable catalog that maps SAP3 canonical names to
source names and unit conversions. First-cut entries:

| SAP3 canonical parameter | SAP3 unit | ERA5 name | IFS name | Snow name | Adapter behavior |
|---|---:|---|---|---|---|
| `precipitation` | `mm` | `total_precipitation` | `tp` | - | Gateway value is metres and already incremental per step; multiply by 1000; do not de-accumulate. |
| `temperature` | `°C` | `2m_temperature` | `2t` | - | Gateway value is Kelvin; subtract 273.15. |
| `snow_depth` | `cm` | - | - | `hs` | Snow height. **Canonical target unit is `cm`.** Gateway source unit is UNCONFIRMED (the client forwards only the variable string with no unit metadata, `../recap-dg-client/recap_client/snow.py:36-84`); the m→cm factor is a Plan-082 live-smoke item, NOT committed/tested in 081. |
| `snowmelt` | `mm` | - | - | `rof` | Snowmelt, incl. direct runoff from snow-free areas (Snowmapper semantics, confirmed 2026-06-25). **Canonical target unit `mm`.** Add `snowmelt` to `docs/conventions.md`; source unit/factor UNCONFIRMED → Plan 082 live smoke, not tested in 081. |
| `swe` | `mm` | - | - | `swe` | Snow water equivalent. **Canonical target unit `mm`.** Source unit/factor UNCONFIRMED → Plan 082 live smoke, not tested in 081. |

Snow **names** are stable and safe to commit (Task 2A adds `snowmelt` to
`docs/conventions.md`); snow **source-unit magnitudes** are unconfirmed, so 081 commits only
names/units plus the grounded precip/temperature conversions — snow factors are deferred to
Plan 082.

### MeteoSwiss Map Alignment

`MeteoSwissNwpAdapter.PARAM_GROUPS` is not a units table; it is a live extraction allowlist
of `(STAC item token, cfgrib shortName, typeOfLevel)`. The Recap variable catalog (Task 2A)
is a **separate, never-merged** structure — nothing in Phase 2/3 imports or mutates
`PARAM_GROUPS`, so no code path lets the new catalog regress Swiss extraction. The one guard
warranted — pinning the existing tuples against silent drift — is a single assertion folded
into Task 2A. The invariants it protects:

- `tot_prec` / `tp` / `surface` remains the Swiss precipitation fetch group.
- `t_2m` / `2t` / `heightAboveGround` remains the Swiss temperature fetch group.
- ICON `tp` remains de-accumulated in `meteoswiss_nwp.py`; Gateway `tp` remains
  treated as already incremental.

### Temporal Resolution

The adapter preserves Gateway native valid times and does not resample or
broadcast:

- IFS: 3-hourly to 144 h, then 6-hourly to about 360 h, per live confirmation.
- ERA5-Land: hourly.
- Snow: daily.

Deterministic daily snow must not be broadcast across the 51-member sub-daily
NWP ensemble inside this adapter. Plan 082 owns the model-input temporal join
policy because that is where model feature frames are assembled.

### Provenance and Errors

**DECISION — two different "source" fields, do not conflate.**

- **Reanalysis records** (`RawHistoricalForcing.source`, a plain `str`) carry the
  **endpoint-provenance literal** Task 3C writes: `recap_era5_land_reanalysis` /
  `recap_snow_reanalysis`.
- **Forecast records** (`BasinAverageForecast.nwp_source` / `ElevationBandForecast.nwp_source`,
  `types/weather.py:47,54`) do **NOT** carry a provenance literal. Their `nwp_source` MUST
  equal the **forecast binding key** = the adapter's own `NWP_SOURCE = "ifs_ecmwf"` (the
  `role==FORECAST` binding). Load-bearing: the converter copies it verbatim into stored
  records (`converters.py:30,59`), the store persists/reads by that key
  (`weather_forecast_store.py:34,120`), and Plan 115a §8 requires forecast rows stored under
  the FORECAST binding's `nwp_source`, never a provenance tag (`115a:234-238`). A `recap_*`
  tag here would reproduce the dual-identity bug; the split adapter makes `"ifs_ecmwf"` the
  only forecast-path value (Task 3A/3B assert it). IFS identity stays auditable via the
  per-row `source == "ifs"` column, which never becomes the storage key.
- **`recap_snow_forecast` is struck from Plan 081** — no Phase-3 task owns `snow.forecast()`
  and snow factors are deferred, so snow-forecast support moves to Plan 082. *(Note for 082,
  re-verified against `recap-dg-client` main `60e5d73`, 2026-07-10: `snow.forecast()` now takes
  a `run_hour: int` param, `0/6/12/18` — aligning with the IFS cycle — so 082's `SnowApiLike.forecast`
  must carry it. `snow.reanalysis` — the only snow call Plan 081 uses — is unchanged.)*

**DECISION — per-row provenance columns handled defensively (split-if-present).** The clone
only *forwards* `include_provenance=True` and defines `PROVENANCE_COLUMNS = ("source","source_run")`
(`provenance.py:6`); its tests do **not** prove the ifs/era5/snow endpoints actually return
these columns or specific `source` literals (a Plan 082 live-smoke contract). So the adapter
splits off `("source","source_run")` **by column-name literal, if present** (reimplementing
the drop — it does **not** import the client's `drop_provenance`, staying importable with
`recap_client` absent) before numeric reshaping. 081 tests assert **defensive mechanics only**
(split if present, preserve, tolerate absence); specific `source` literal values are Plan 082
live smoke.

**`source_run` normalization (offline-testable).** `RawHistoricalForcing.version` is `str`
(`types/historical_forcing.py:24`) but the clone's fixture builds `source_run` as a tz-aware
`pd.Timestamp` (real endpoint dtype unproven → Plan 082). The adapter MUST normalize any
parseable `source_run` at the boundary — parse to a UTC `UtcDatetime` for a forecast
`cycle_time`, serialize to a stable ISO-8601 UTC string for `.version`; a non-`str` must never
leak into the string field. Task 3A/3C assert the field is a `str` (and, for forecasts, the
cycle datetime is UTC).

**DECISION — error mapping is structural / duck-typed.** The mapper takes a plain
`BaseException` and reads discriminators via `getattr(exc, "code"/"field"/"supported_values",
None)`, never `isinstance` against imported `recap_client` classes and never a runtime `import
recap_client` (Task 3D). The clone's three error classes do **not** share one attribute set
(only `ApiValidationError` has `supported_values`, `http.py:44`; `ApiRequestError` has
url/params/status_code/body; `ApiDataUnavailableError` has code/field/hint/details), so the
`getattr` defaults — not a superset Protocol — make the mapper total over all three:

- `code="source_data_missing"` (clone `ApiDataUnavailableError`, `http.py:61`): unavailable
  source data; Plan 082 may interpret this during latest-cycle probing.
- `code="unsupported_shapefile"`, `field="hru_code"`: configuration/metadata error, not stale
  delivery. **Unverified literal** (Gateway-dev-sync claim absent from the clone — see the
  Contract-Fit Addressing row; Plan 082 live probe owns confirmation). The mapper matches
  structurally by `getattr`, so it does not depend on this exact literal.
- Other validation errors: request-construction or Gateway contract errors.
- Plain request/network errors: retriable `AdapterError`.

Add custom SAP3 exception subclasses only where callers need distinct behavior.

### NWP-Source Dispatch: 081-side obligation only (role-based selection is now existing code)

The offline adapters are inert until they are wired into the production dispatch points. That
selection layer is **now existing code**: Plan 115a (merged, `bced53d`) replaced the old
`_select_nwp_source` heuristic with explicit **role-based** selection, so 081 relies on it
rather than waiting on it. Concretely, in the current tree:

- **Forecast selection is role-based.** `_select_nwp_source` is retired (0 occurrences in
  `src/`). The forecast extractor now filters bindings by both source-identity **and**
  `WeatherSourceRole.FORECAST`: `ws.nwp_source == result_object.nwp_source and ws.role ==
  WeatherSourceRole.FORECAST` (`run_forecast_cycle.py:804-809`, the role clause at `:808`).
- **Reanalysis selection still keys on `NWP_SOURCE`.** Flow 6 selects via the local
  `_ReanalysisAdapter` Protocol's `NWP_SOURCE` attribute (`ingest_weather_history.py:70`,
  used at `:304,309`); 115a moved the **role** scoping into the store accessor
  `station_store.fetch_reanalysis_bindings(...)` (`_reanalysis_sources`,
  `ingest_weather_history.py:243-252`), which returns only REANALYSIS-role bindings, then
  filters those by `source.nwp_source == adapter.NWP_SOURCE` (`:251`).

**081's entire deliverable remains one class attribute per adapter — and it is still correct
and minimal post-115a:**

- `RecapGatewayForecastAdapter.NWP_SOURCE = "ifs_ecmwf"` — the forecast storage key. It is
  the value the forecast extractor matches against (`run_forecast_cycle.py:807`) and the value
  every forecast record's `nwp_source` must equal (Task 3A/3B). 115a's `role==FORECAST` clause
  is an **additional** binding-row property (owned by config/onboarding, not the adapter class),
  not a replacement for the source key — so this attribute obligation stands unchanged.
- `RecapGatewayReanalysisAdapter.NWP_SOURCE = "era5_land"` — still satisfies Flow-6's local
  `_ReanalysisAdapter` selector Protocol (`ingest_weather_history.py:70,309`). Role-scoping now
  happens in `fetch_reanalysis_bindings`, upstream of and orthogonal to this attribute; the
  `NWP_SOURCE`-as-selector mechanism 081 depends on is unchanged.

081 guarantees the attributes exist and are non-empty (offline string assertion, Task 3A); it
edits **no** `run_forecast_cycle.py` dispatch or storage code.

**Ownership boundary (115a done, 082 pending).** Plan 115a (merged, `bced53d`) already
delivered the `WeatherSourceRole` field, the role-scoped store accessors, the rewired binding
consumers, and the retirement of `_select_nwp_source`'s heuristic (`115a:151-158,234-238`).
Plan 082 Task 2C is the only remaining wiring: it consumes 115a's role-scoped forecast binding
and owns the Recap-specific construction/config, source-aware `NWP_DELIVERY` staleness, the
gateway-binding `BASIN_AVERAGE` validator, live smoke, and dispatch wiring.

> **Note for the owner — no longer a live contradiction.** An earlier draft flagged that Plan
> 082 Task 2C step (d) says "`_select_nwp_source` itself needs **no** logic change"
> (`docs/plans/082-recap-gateway-operational-readiness.md:285-287`) against 115a's "retire the
> heuristic entirely." 115a has since **landed that retirement in code** (`_select_nwp_source`
> is gone), so there is no live conflict in 081's dependency chain — the 082 step-(d) text is
> now simply **082's own stale doc**, to be corrected when 082 is refreshed (082 consumes the
> role-scoped forecast binding; it does not re-derive it). 081 cannot edit 082; flagged for
> the 082 refresh only.

## Test Plan

All required tests in Plan 081 are offline. They use `FakeRecapClient` objects
returning canned pandas DataFrames that match the empirical Gateway shape:
index name `time`, tz-aware UTC index, one variable per call, numeric polygon
columns, float values, and — since the endpoint columns are a 082-owned server
contract — fixtures covering both **with** and **without** the `source`/`source_run`
provenance columns, so the defensive if-present split is exercised both ways.

The full set of required offline assertions is specified per-task in each Phase-2/3 task's
MUST-list (Protocol conformance + storage key, basin-average-only, per-item isolation, config
pre-filtering, DataFrame-boundary/provenance, unit conversions, IFS ensemble assembly,
HRU-batched demux, reanalysis provenance + parameter selection, the `elevation_band_to_records`
converter, and offline-safety error mapping). Those MUST-lists are the authoritative gates;
this section is not a second copy.

**On task-authored gating tests (anti-circularity).** Several Phase-2/3 tasks author the
very test class that gates them, so a bare `::TestFoo` selector on a trivial body would
self-certify. Mitigations: (1) every such task carries an explicit **MUST-assert list**
(Tasks 2A, 3A, 3B, 3C — exact catalog values, `isinstance` positive/negative pairs, recorded
fake-client call kwargs, exact member-id sets), so the gate cannot pass on an empty body; and
(2) where practical the assertion is **independent of the adapter's own code path** (the
`@runtime_checkable` `isinstance` conformance check exercises Protocols directly; the
recording fake client captures the literal kwargs SAP3 sends).

**No live tests in Plan 081.** Every test is offline (fake client). Operational live smoke —
markers, `RECAP_API_KEY` skip handling, default-exclusion gates — belongs entirely to Plan
082, where the committed git-pinned dependency exists.

## Implementation Phases

### Phase 1 - Contract Docs, Metadata, Dependency Strategy

#### Task 1B - Add Gateway polygon metadata types

**Scope in:** Add the typed metadata/resolver/client boundary required by both
adapters:

- `GatewayHruName`, `GatewayPolygonName`, `GatewayPolygonRef`;
- an injected **resolver** Protocol `GatewayPolygonResolver` with the exact contract
  `def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None` (1:1 —
  basin-average station = one polygon, root fix) — a `None` return (miss) is what the
  adapter **skips-and-logs**; the all-unmappable case raises `GatewayResolutionError` —
  see §Adapter Decisions › Naming and Metadata for the literal block and the future
  banding seam;
- the injected **client** Protocol `RecapClientLike` (+ sub-Protocols `EcmwfApiLike`,
  `SnowApiLike`) describing the exact call surface both adapters use — see §Adapter
  Decisions › Naming and Metadata. This is the SAP3-owned structural type that lets
  `recap_gateway.py` name no `recap_client` symbol; Task 3D references it;
- a SAP3-owned `GatewayResolutionError` (an `AdapterError` subclass) carrying a typed
  `station_id`, raised by the adapter only when **every** station in a batch is
  unmappable (per-station misses are skipped-and-logged, not raised).

**Scope out:** Do not implement GeoPackage upload, geometry validation, or live
Gateway discovery.

**Verification:** (a grep for the symbol names would pass even if they only appeared
in comments, so the gate must actually import and exercise the types)

```bash
uv run pyright src/sapphire_flow/adapters/recap_gateway.py
# Discriminating: import the symbols, construct GatewayPolygonRef, and assert its
# dataclass/frozen behavior + the resolver + client Protocols' structural shapes.
# This fails until the real NewTypes / frozen dataclass / Protocols exist (not just
# names in prose).
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestGatewayPolygonTypes
```

`TestGatewayPolygonTypes` (authored here) MUST import all **eight** symbols
(`GatewayHruName`, `GatewayPolygonName`, `GatewayPolygonRef`, `GatewayPolygonResolver`,
`RecapClientLike`, `EcmwfApiLike`, `SnowApiLike`, `GatewayResolutionError`); construct a
`GatewayPolygonRef` and assert its five fields; assert `frozen=True` immutability
(assignment raises `FrozenInstanceError`); assert an object exposing a
`resolve(...) -> GatewayPolygonRef | None` method satisfies `GatewayPolygonResolver`
while one missing it does not; and assert `GatewayResolutionError` subclasses
`AdapterError` and carries a `station_id`.

**Sub-Protocol callable-surface assertions.** Because `@runtime_checkable` `isinstance`
verifies member *presence*, the gate MUST also assert the client call surface both adapters
depend on (grounded in the clone `ecmwf.py:55-69`, `snow.py:36-47`) with positive/negative
pairs:

- **Positive:** an `EcmwfApiLike` fake exposing `ifs_forecast` (with optional `member`) **and**
  `era5_land_reanalysis` passes; a `SnowApiLike` fake exposing `reanalysis` (with required
  `start_date`/`end_date`) passes; a `RecapClientLike` whose `.ecmwf`/`.snow` are those fakes
  passes.
- **Negative:** a fake missing `ifs_forecast` / `era5_land_reanalysis` / `reanalysis` fails; a
  `RecapClientLike` fake missing `.ecmwf` or `.snow` fails. (The negative halves are
  load-bearing — they prove the gate verifies the callable surface, not a trivial stub.)

#### Task 1C - Document dependency distribution strategy

**Scope in:** Update `docs/standards/security.md` and/or `docs/standards/cicd.md`
with the `recap-dg-client` policy (decided 2026-06-25, ForecastInterface precedent):
a git-pin + scoped exception to the existing **wheel-only guard** (the CI job/step
already named `wheel-only-guard`, `docs/standards/security.md:387,389`,
`docs/standards/cicd.md:370-371`; Plan 079-style) is the accepted bridge, the repo
is **private** so CI and the Docker builder need clone auth, and the follow-up is
migration to a versioned private-index wheel (Plan 080-style). The actual
`pyproject.toml` git-pin + CI exception is implemented in Plan 082, not here.

> Terminology: the established codebase term is **"wheel-only guard"** (job id
> `wheel-only-guard`), not "wheel-guard". New standards prose MUST use the established term.

**Scope out:** Do not add `recap-dg-client` to `pyproject.toml`, `uv.lock`, or
`[tool.uv.sources]` in Plan 081.

**Verification:**

```bash
# Positive completion gate — proves the task's new standards prose actually landed:
uv run python -c "from pathlib import Path; files=[Path('docs/standards/security.md'), Path('docs/standards/cicd.md')]; text='\\n'.join(p.read_text() for p in files); assert 'recap-dg-client' in text and 'wheel-only guard' in text and 'private-index wheel' in text and 'clone auth' in text"
# Non-regression guards (already pass before work starts — they enforce the Non-goal
# that 081 adds no committed dependency, NOT that the task ran):
uv run python -c "from pathlib import Path; text=Path('pyproject.toml').read_text(); assert 'recap-dg-client' not in text and 'recap_client' not in text"
uv run python -c "from pathlib import Path; lock=Path('uv.lock'); assert (not lock.exists()) or ('recap-dg-client' not in lock.read_text() and 'recap_client' not in lock.read_text())"
```

#### Task 1D - Document the new types and adapter conformance boundary in the type spec

**Scope in:** Update `docs/spec/types-and-protocols.md` (CLAUDE.md #3-priority,
"authoritative for implementation"; "Every code change updates affected docs — no
exceptions"). Add: `GatewayHruName`, `GatewayPolygonName`, the `GatewayPolygonRef`
frozen dataclass, the SAP3-owned resolver Protocol `GatewayPolygonResolver`, the
SAP3-owned injected-client Protocol `RecapClientLike` (Task 1B), and
`GatewayResolutionError`; plus two short conformance-boundary entries mirroring the
existing `### ForecastInterfaceAdapter` entry at
`docs/spec/types-and-protocols.md:1628`:

**DECISION — do NOT add `RecapErrorLike` to the type spec.** The error-mapper's discriminator
attribute set documents an external, unpinned dependency's internals (clone `b3ce520`) that no
SAP3 code structurally depends on (the mapper takes `BaseException` and reads via `getattr`,
Task 3D). Promoting a never-enforced snapshot to the authoritative spec only rots and misleads;
that knowledge lives as a **docstring on the error-mapping function** in `recap_gateway.py`
instead (Task 3D).

- `### RecapGatewayForecastAdapter` (Module: `adapters/recap_gateway.py`; satisfies
  `WeatherForecastSource`; `NWP_SOURCE="ifs_ecmwf"`).
- `### RecapGatewayReanalysisAdapter` (Module: `adapters/recap_gateway.py`; satisfies
  `WeatherReanalysisSource`; `NWP_SOURCE="era5_land"`).

**Scope in (requirements cross-reference).** Also add a short cross-reference in
`docs/requirements/01-data-gateway-requirements.md` pointing at **this plan** (§Contract-Fit
Review and §Adapter Decisions) as the authoritative, empirically-grounded adapter contract
(HRU addressing, one-variable wide DataFrames, endpoint provenance, HRES-as-control
`member_id=0`, unit conversions, no-coverage as a Plan 082 blocker). Do **not** create a
parallel `docs/design/recap-gateway-adapter-contract.md`: this plan (READY/archived =
"Authoritative" under the trust hierarchy) is itself the single source of truth for the
contract; a second copy is only drift risk.

**Scope out:** Do not add `recap_client` symbols to the spec (they are intentionally
not SAP3 types); do not add `RecapErrorLike` to the spec (see the boxed decision above);
do not document `recap_snow_forecast` (struck from 081 — deferred to Plan 082); do not
restate the full Contract-Fit Review (it lives in this plan); do not create a new
`docs/design/` file.

**Verification:**

```bash
# Positive completion gate — the task's new spec content actually landed:
uv run python -c "from pathlib import Path; t=Path('docs/spec/types-and-protocols.md').read_text(); assert 'RecapGatewayForecastAdapter' in t and 'RecapGatewayReanalysisAdapter' in t and 'GatewayPolygonRef' in t and 'GatewayHruName' in t and 'GatewayPolygonResolver' in t and 'RecapClientLike' in t and 'GatewayResolutionError' in t"
# Non-regression guards (NOT completion gates — these already pass before work starts;
# they exist to catch a task that over-reaches, not to prove the task ran):
uv run python -c "from pathlib import Path; t=Path('docs/spec/types-and-protocols.md').read_text(); assert 'RecapErrorLike' not in t, 'error-mapper doc-Protocol stays out of the canonical spec'"
uv run python -c "from pathlib import Path; t=Path('docs/spec/types-and-protocols.md').read_text(); assert 'recap_snow_forecast' not in t, 'snow-forecast is struck from Plan 081'"
uv run python -c "from pathlib import Path; assert not Path('docs/design/recap-gateway-adapter-contract.md').exists(), 'no parallel design doc'"
# Positive completion gate — the requirements cross-reference actually landed:
uv run python -c "from pathlib import Path; text=Path('docs/requirements/01-data-gateway-requirements.md').read_text(); assert '081-recap-dg-client-integration' in text and ('Contract-Fit' in text or 'adapter contract' in text)"
```

### Phase 2 - Variable Catalog and Converter Foundations

#### Task 2A - Add Recap canonical variable catalog

**Scope in:** Add a SAP3-owned Recap variable catalog for precipitation, temperature,
and the confirmed snow variables (`hs`=snow_depth, `rof`=snowmelt, `swe`=swe) with
source names and unit conversions. Add `snowmelt` to `docs/conventions.md`
(precip/temp/snow_depth/swe already present).

**Scope in (Swiss-behavior sanity guard):** Add a **new, specifically-named method
`test_exact_param_group_tuples`** on the pre-existing `TestParamGroups` class
(`tests/unit/adapters/test_meteoswiss_nwp.py:861`, alongside `test_three_column_shape` — do
**not** create a second `class TestParamGroups:`, which would shadow it). The method MUST
make a **discriminating, exact-value** assertion (the existing `test_three_column_shape`
checks only tuple *shape*):

```python
assert list(PARAM_GROUPS) == [("tot_prec", "tp", "surface"), ("t_2m", "2t", "heightAboveGround")]
```

so the new Recap catalog cannot silently drift Swiss STAC token / cfgrib shortName /
typeOfLevel extraction keys.

**Scope out:** Do not change MeteoSwiss `PARAM_GROUPS`
(`src/sapphire_flow/adapters/meteoswiss_nwp.py:56`) — the Recap catalog is a separate,
never-merged structure. Do not hardcode an unverified snow source-unit factor; Plan
082 live smoke confirms `hs`/`rof`/`swe` magnitudes.

`test_recap_gateway_variables.py` (the task authors it) MUST make **discriminating,
behavioral** assertions against the catalog data structure — not merely token-presence
(the file is the sole gate, so a `def test_placeholder(): pass` body would otherwise
pass; mirror the specificity of Task 1B/3A's MUST-lists):

- Import the catalog object and assert every canonical parameter maps to its documented
  source name(s): `precipitation`→`total_precipitation` (ERA5)/`tp` (IFS);
  `temperature`→`2m_temperature`/`2t`; `snow_depth`→`hs`; `snowmelt`→`rof`; `swe`→`swe`
  (matching the §Variable Catalog table).
- Assert the **conversion functions applied to a sample value** produce the documented
  numeric result — not just that the strings `mm`/`°C` appear somewhere: precipitation
  `f(1.0 m) == 1000.0 mm` (×1000) and temperature `f(300.0 K) == 26.85 °C` (−273.15),
  within float tolerance.
- Assert the snow variables (`hs`/`rof`/`swe`) carry **no committed magnitude
  conversion factor** (identity/pass-through or an explicit "unconfirmed → 082"
  sentinel) — matching the Scope-out that snow source-unit factors are deferred.

**Verification:**

The source-name mapping is asserted **in the test file** by the MUST-list above (the
catalog's symbol/field names are the implementer's to name, so a literal `python -c` on them
would be a fake gate). The shell commands below reference only already-locked symbols
(`docs/conventions.md`, `meteoswiss_nwp.PARAM_GROUPS`).

```bash
uv run pytest tests/unit/adapters/test_recap_gateway_variables.py
# 'snowmelt' must be a real conventions.md TABLE ROW (a pipe-delimited row), not a stray
# mention in prose:
uv run python -c "from pathlib import Path; import re; t=Path('docs/conventions.md').read_text(); assert re.search(r'^\\|[^\\n]*\\bsnowmelt\\b[^\\n]*\\|', t, re.M), 'snowmelt must be a table row, not a bare mention'"
# Swiss PARAM_GROUPS sanity assertion is a new method on the EXISTING class; guard
# against an accidental second (shadowing) class definition, and confirm the
# pre-existing test_three_column_shape is still collected.
uv run python -c "from pathlib import Path; import re; src=Path('tests/unit/adapters/test_meteoswiss_nwp.py').read_text(); assert len(re.findall(r'^class TestParamGroups:', src, re.M)) == 1, 'exactly one TestParamGroups class (no shadowing)'"
# Discriminating: run the NAMED exact-value method specifically (a bare ::TestParamGroups
# selector would pass on test_three_column_shape alone), and independently pin the exact
# tuples so a skipped/weakened assertion is caught:
uv run pytest "tests/unit/adapters/test_meteoswiss_nwp.py::TestParamGroups::test_exact_param_group_tuples"
uv run python -c "from sapphire_flow.adapters.meteoswiss_nwp import PARAM_GROUPS; assert list(PARAM_GROUPS) == [('tot_prec','tp','surface'), ('t_2m','2t','heightAboveGround')], PARAM_GROUPS"
```

#### Task 2B - Add pure `elevation_band_to_records` converter (generic storage support)

**Scope in.** Add a **pure** side-effect-free
`elevation_band_to_records` converter in `src/sapphire_flow/preprocessing/converters.py`,
alongside the existing `point_forecast_to_records` / basin-average converters, that turns
one `ElevationBandForecast` into `WeatherForecastRecord`s with
`SpatialRepresentation.ELEVATION_BAND` and non-null `band_id`. This is **generic
storage-path support** (a banded forecast from *any* source must be persistable), NOT
Recap-forecast-specific — the **Recap forecast adapter does not exercise the banded path
in Nepal v1** (forecast is basin-average-only; the `ELEVATION_BAND` branch is a typed
seam, see §resolver). Wiring this converter into the forecast-cycle pre-extracted-dict
storage branch (`run_forecast_cycle.py:845-861`, where an `ElevationBandForecast` is
currently deferred with a warning at `:852-858`) remains **Plan 082 Task 2C** (a
production flow edit, out of scope for offline-only 081).

**Scope out:** Do not edit `run_forecast_cycle.py`. Do not rewrite any flow-cycle test —
`TestForecastCycle::test_gridded_nwp_elevation_band_skipped`
(`tests/unit/flows/test_run_forecast_cycle.py:3183`, guarding the current
GridExtractor `ElevationBandForecast`-skip-with-warning at `run_forecast_cycle.py:852-858`,
the same branch Plan 082 later rewires) stays untouched by construction (081 edits no flow
code).

**Verification:**

```bash
uv run pytest tests/unit/preprocessing/test_converters.py::TestElevationBandToRecords
```

`TestElevationBandToRecords` (authored here) MUST make **discriminating** assertions:
import `elevation_band_to_records`; build one `ElevationBandForecast` spanning **≥2
distinct `band_id`s** and multiple members/timesteps; assert the record count equals
bands × members × timesteps; assert every record has
`spatial_type == SpatialRepresentation.ELEVATION_BAND`, a **non-null** `band_id`, a
non-null `member_id`, and that per-`(band_id, member_id, time)` values/timestamps are
preserved (no reordering/loss).

### Phase 3 - Offline Recap Adapter Implementation

#### Task 3A - Implement DataFrame parsing and typed request construction

**Scope in:** Implement the **shared boundary functions** (module-level private
helpers both adapters call: splitting off the `source`/`source_run` provenance columns
**by column-name literal** — SAP3 reimplements the drop, it does **not** import the
client's `drop_provenance` helper — capturing `source_run` into the record
version/cycle, DataFrame-to-Polars conversion, per-item resolution via
`GatewayPolygonResolver.resolve` with skip-and-log per unmappable station) plus
`RecapGatewayForecastAdapter` typed request construction, per-station demultiplexing, and
fake-client unit tests. The adapter's production return path yields
`dict[StationId, WeatherForecastResult]`. **For Nepal v1 every resolved forecast is
`BasinAverageForecast` (basin-average-only, root fix);** the `ElevationBandForecast` branch
stays present as a typed seam but no v1 config routes through it, so it is **not gated here**.
Both adapter classes take an injected `RecapClientLike`
(Task 1B). Expose `NWP_SOURCE: ClassVar[str]` on each class
(`RecapGatewayForecastAdapter="ifs_ecmwf"`, `RecapGatewayReanalysisAdapter="era5_land"`).
Assert each attribute is present and non-empty, and — **blocker fix** — assert every
forecast result's `nwp_source == RecapGatewayForecastAdapter.NWP_SOURCE` (`"ifs_ecmwf"`,
the `role==FORECAST` binding key), never a `recap_*` provenance tag or the reanalysis
identity. See §Provenance and Errors and the §NWP-Source Dispatch obligation.

**Protocol-conformance gate (blocker fix).** Both adapter classes are **declared** in this
task — each exposes its `NWP_SOURCE` and at least the *signature* of its Protocol method
(`fetch_reanalysis`'s body is filled by Task 3C). This task authors `TestProtocolConformance`
asserting the single-Protocol identity of each class with **positive + negative** `isinstance`
pairs against the `@runtime_checkable` Protocols (`protocols/adapters.py:17-32,47-55`):

- `isinstance(forecast_adapter, WeatherForecastSource)` is `True` **and**
  `isinstance(forecast_adapter, WeatherReanalysisSource)` is `False`.
- `isinstance(reanalysis_adapter, WeatherReanalysisSource)` is `True` **and**
  `isinstance(reanalysis_adapter, WeatherForecastSource)` is `False`.

This makes an accidental reintroduction of the dual-identity bug (a forecast adapter that
grows a `fetch_reanalysis` method) **fail a named gate**. (`@runtime_checkable` `isinstance`
checks method *presence*, not signature, so the negative half is load-bearing.)

**Scope out:** No real network access in unit tests, no dependency metadata changes, and
**no reference to the name `recap_client` anywhere in `recap_gateway.py`** — not at runtime
and not under `if TYPE_CHECKING:` either (see Task 3D for why).

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestDataFrameParsing tests/unit/adapters/test_recap_gateway.py::TestForecastReturnShape tests/unit/adapters/test_recap_gateway.py::TestForecastStorageKey tests/unit/adapters/test_recap_gateway.py::TestProtocolConformance
```

- `TestProtocolConformance` (blocker gate) MUST make the four positive/negative `isinstance`
  assertions above — the only gate verifying the single-Protocol split; must be named in the
  selector.
- `TestForecastStorageKey` (blocker gate) MUST assert every forecast result's
  `nwp_source == "ifs_ecmwf"` and that no code path lets it become a `recap_*` tag or `"era5_land"`.
- `TestDataFrameParsing` MUST assert, against a recording fake client, the **shared boundary
  helpers** directly, independent of ensemble-assembly orchestration:
  - **Provenance split both ways:** a fixture **with** `("source","source_run")` has them
    split off **by column-name literal** (not the client's `drop_provenance`) before reshaping;
    a **no-provenance** fixture parses cleanly; neither leaks a raw pandas object.
  - **Wide→long reshape:** numeric polygon columns (`g_<station_code>`) map back to `station_id`
    via resolved `polygon_name`s, not string parsing of the column text.
  - **HRU batching / demux (MAJOR #6):** 2 stations resolving to distinct `polygon_name`s under
    the SAME `hru_name` issue **exactly one** fetch per `(hru, variable, cycle)` (for forecasts
    one 51-call ensemble — 51 `ifs_forecast` calls total, **not** 51×2), demultiplexed to the
    correct `station_id` per column. Fails a naive per-station loop.
- `TestForecastReturnShape` MUST assert (a) a Nepal gateway forecast produces
  `BASIN_AVERAGE` results (`BasinAverageForecast`, station-keyed, `band_id`-free); (b)
  **per-item isolation (MAJOR #2)** — one-unmappable+one-mappable batch returns the mappable
  result + logged skip (no raise), all-unmappable raises `GatewayResolutionError` carrying a
  `station_id`; (c) **config pre-filtering** — each of a **wrong-source**
  (`nwp_source != NWP_SOURCE`), an **INACTIVE**, and a **non-`BASIN_AVERAGE`**
  `extraction_type` config yields zero Gateway calls and no output row, excluded *before*
  resolution (all three exclusions per the spec above). **Ownership (3A vs 3B):**
  these tests feed the shared splitting helper synthetic multi-member fixtures directly — they
  do **not** drive the real 51-call `fc`+`pf` orchestration (Task 3B's sole gate,
  `TestIfsEnsembleAssembly`).

#### Task 3B - Implement sanctioned IFS ensemble assembly

**Scope in:** Assemble IFS forecasts as one `fc` HRES control plus 50 `pf`
perturbed calls per variable/HRU/cycle, with member ids exactly 0..50 and a
guard against `pf` member collisions with 0.

**Scope out:** Do not reduce the ensemble to a mean, quantiles, or deterministic
forecast.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestIfsEnsembleAssembly
```

`TestIfsEnsembleAssembly` (the task authors it — a bare selector would pass on a
trivial body) MUST make these **discriminating** assertions against a recording fake
client that captures every `ifs_forecast` call's kwargs:

- Exactly **1 `fc` call + 50 `pf` calls** are issued per variable/HRU/cycle (51 total).
- The single `fc` call sends **no `member`** kwarg/parameter; each `pf` call sends a
  `member` in **1..50** and the set of `pf` members is exactly `{1, ..., 50}`.
- The assembled member ids on the returned forecast are exactly `{0, 1, ..., 50}`, and
  **no `pf` result can be written under `member_id=0`** (0 is reserved for `fc`).
- (These assert what SAP3 *constructs and sends*, NOT that the Gateway rejects
  `cf`/0/51 — that server-contract check is Plan 082 live smoke; see §Ensemble Member
  Contract.)

#### Task 3C - Implement reanalysis conversion

**Scope in:** In `RecapGatewayReanalysisAdapter`, convert ERA5-Land reanalysis and
snow reanalysis (`hs`/`rof`/`swe`) DataFrames into `RawHistoricalForcing` records with
the reanalysis endpoint-provenance literals (`recap_era5_land_reanalysis` /
`recap_snow_reanalysis`, `RawHistoricalForcing.source`) and deterministic
`member_id=None`. Apply only the **grounded** unit conversions (precipitation m→mm,
temperature K→°C). Snow variables map to their canonical *names* and pass values
through **without** a committed magnitude factor. Normalize `source_run` (a
`pd.Timestamp` in the clone) to a stable ISO-8601 UTC **string** for
`RawHistoricalForcing.version` (`types/historical_forcing.py:24` is `str`); assert the
field type is `str`. Provenance handling is **defensive mechanics only** — split
`source`/`source_run` if present, tolerate absence; do **NOT** assert specific `source`
literal values (those move to Plan 082 live smoke). Snow-forecast is out of 081 scope
(deferred to Plan 082).

**Parameter-selection contract (`parameters: list[str]`).** `fetch_reanalysis` receives a
`parameters` list (`protocols/adapters.py:48-55`); Flow 6 passes `_CANONICAL_PARAMETERS`
including `temperature_min`/`temperature_max` (`ingest_weather_history.py:58-63`), absent from
the Recap catalog. Mirroring the precedent (`meteoswiss_open_data_reanalysis.py:145-150`,
early-return when no supported parameter is requested):

- For each **requested** parameter, look it up and issue only its endpoint call; nothing else.
- For a parameter with **no catalog entry** (e.g. `temperature_min`), **skip it** (no rows, no
  raise, no mis-map). If *none* are catalogged, return `[]` before any client call.
- Requesting only `precipitation` issues only the ERA5 `total_precipitation` call, no snow call.

> **Cross-plan hazard (`ForcingSource` registration — for the Plan 082 / hybrid-chain
> owner).** `RawHistoricalForcing.source` is a plain `str`, so 081 writes
> `recap_era5_land_reanalysis` / `recap_snow_reanalysis` verbatim and its offline tests pass.
> **But** `HybridForcingSource` keys priority/fan-out on the `ForcingSource` **enum**
> (`hybrid_reanalysis.py:45-49`) and attribution reads `SOURCE_ATTRIBUTIONS`
> (`types/forcing_sources.py:36-43`, over the `ForcingSource` enum at `:18-34`), neither of
> which knows these two literals. 081 does
> **not** register them (no hybrid consumer offline). Whichever plan wires Recap reanalysis
> into `HybridForcingSource` MUST add `ForcingSource` members + `SOURCE_ATTRIBUTIONS` entries
> for both literals **before** Recap reanalysis can participate in priority resolution or
> attribution.

**Banded reanalysis is out of scope — basin-average only (resolver DECISION).** To gate the
decision (not just assume it), Task 3C **asserts** every produced `RawHistoricalForcing` has
`spatial_type == SpatialRepresentation.BASIN_AVERAGE` and `band_id is None`
(`types/historical_forcing.py:27-28`, whose `_validate_band_id` already forbids a non-null
`band_id` outside `ELEVATION_BAND`). This assertion is the clean seam a future banded
extension flips.

**Scope out:** Do not call `ecmwf.operational()` and do not mark training
readiness from returned timestamps. **Do not extend Task 3C to banded reanalysis**
(basin-average only — see above). **Do not assert any snow source-unit
conversion factor** (m→cm for `hs`, →mm for `rof`/`swe`) as tested behavior — those
magnitudes are unconfirmed (`../recap-dg-client/recap_client/snow.py:36-84` exposes
no unit metadata) and are a Plan 082 live-smoke item.

`TestReanalysisConversion` (authored here) MUST assert, against a recording fake client
returning canned ERA5-Land + snow DataFrames:

- **Provenance literals:** ERA5-Land rows have `source == "recap_era5_land_reanalysis"`;
  snow rows have `source == "recap_snow_reanalysis"` (`RawHistoricalForcing.source`).
- **Deterministic:** every row has `member_id is None`.
- **Basin-average-only invariant:** every row has `spatial_type ==
  SpatialRepresentation.BASIN_AVERAGE` and `band_id is None`
  (`types/historical_forcing.py:27-28`).
- **Grounded conversions only:** precipitation `1.0 m → 1000.0 mm`, temperature
  `300.0 K → 26.85 °C`; snow `hs`/`rof`/`swe` values pass through with **no** committed
  magnitude factor (identity — see Scope-out).
- **`source_run` normalization:** `.version` is a `str` (stable ISO-8601 UTC), never a
  raw `pd.Timestamp` (`types/historical_forcing.py:24`).
- **Parameter selection:** requesting only `precipitation` issues only the ERA5
  `total_precipitation` call and **zero** `snow.reanalysis` calls; an unmapped requested
  parameter (`temperature_min`) yields **no** rows for it and does not raise; an
  all-unmapped request returns `[]` with no client call.
- **Config pre-filtering:** each of a **wrong-source** (`nwp_source != NWP_SOURCE`), an
  `INACTIVE`-status, and a non-`BASIN_AVERAGE` `extraction_type` config yields no client call
  and no row (all three exclusions per the spec above).
- **Per-item isolation (MAJOR #2):** a batch with one resolver-unmappable + one mappable
  station returns the mappable station's `RawHistoricalForcing` rows and logs a structured
  skip for the unmappable one (no raise); an **all-unmappable** batch raises
  `GatewayResolutionError` carrying a `station_id`.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestReanalysisConversion
```

#### Task 3D - Implement SAP3-side error mapping (offline, structural)

**Scope in:** Map `recap_client` structured errors into SAP3 adapter/configuration
errors and preserve unsupported-HRU errors distinctly for Plan 082 watchdog
handling.

**Offline-safety (hard requirement):** the mapping MUST be done **structurally /
duck-typed**. The mapper's parameter is annotated **`BaseException`**, and discriminators are
read via `getattr(exc, "code"/"field"/"supported_values", None)`. The `getattr` defaults are
load-bearing: the clone's three error classes do **not** share one attribute set (only
`ApiValidationError` has `supported_values`), so a single `Protocol` requiring all
discriminator attrs would **fail** to match `ApiRequestError` / `ApiDataUnavailableError`. Do
**not** annotate the mapper input with such a Protocol.

**DECISION — no `RecapErrorLike` Protocol class (see Task 1D box for rationale).** The
discriminator attribute-set knowledge lives as a **docstring on the error-mapping function**
in `recap_gateway.py`, where a maintainer reading the mapper needs it:

```python
def _map_recap_error(exc: BaseException) -> AdapterError:
    """Map a recap-dg-client structured error to a SAP3 AdapterError, structurally.

    Reads discriminators via getattr(exc, "code"/"field"/"supported_values", None) —
    never isinstance against the client's error classes. Clone b3ce520 attribute sets
    (clone http.py:15-85): ApiRequestError -> url/params/status_code/body;
    ApiValidationError -> + code/field/hint/supported_values/details;
    ApiDataUnavailableError -> code/field/hint/details (no supported_values). The
    getattr-None defaults make the mapper total over all three without a shared type.
    """
```

> **Docstring must carry no `recap_client` token.** The substring backstop gate (3) below
> forbids the literal `recap_client` **anywhere** in `recap_gateway.py`, including
> docstrings. So the docstring above uses the hyphenated repo name (`recap-dg-client`) and
> bare clone-relative paths (`clone http.py`, not `recap_client/http.py`) — zero occurrences
> of the module token. Do not reintroduce the underscore module name in prose here.

**The name `recap_client` MUST NOT appear anywhere in `recap_gateway.py` — not at runtime,
and NOT under `if TYPE_CHECKING:` either.** pyright resolves imports inside `if TYPE_CHECKING:`
blocks regardless of `from __future__ import annotations`, so a guarded `import recap_client`
is still a hard `reportMissingImports` under strict mode — and Non-goals forbid adding the
package to `pyproject.toml`/`uv.lock`/`[tool.uv.sources]`, so pyright can never resolve it.
The injected client is therefore typed **exclusively** against SAP3-owned Protocols
(`RecapClientLike` + the resolver, Task 1B); its errors are read structurally via `getattr`,
no error-type annotation. Literal `recap_client.*` annotations are deferred to Plan 082, where
the git-pinned dependency makes the package resolvable (as `forecastinterface`'s top-level
import is safe today because it IS committed).

**Deferred to Plan 082 — bounded retry/backoff.** Retry/backoff has no adapter-layer
precedent (`meteoswiss_nwp.py`/`forecast_interface.py` have none) and the client does a
single `requests.get`; backoff parameters need live Gateway failure characteristics to
calibrate, so they move to Plan 082. Plan 081 maps errors only.

**Scope out:** Do not `import recap_client` at runtime. Do not add retry/backoff.
Do not patch behavior into the external client repository.

`TestErrorMapping` (authored here) MUST map **duck-typed fake exceptions** (plain
`BaseException` subclasses with attributes set, NOT instances of any real `recap_client`
class) and assert each lands on an exact SAP3 exception type with context preserved:

- `code="source_data_missing"` (clone `ApiDataUnavailableError`, `test_http_errors.py:119,138`)
  → SAP3 data-unavailable/retriable `AdapterError` subclass, preserving `code`.
- `code="unsupported_shapefile"`, `field="hru_code"`, `supported_values` list → SAP3
  configuration/metadata error subclass, preserving `field` + `supported_values`. **Unverified
  literal** (Plan 082 live probe owns it — see Contract-Fit Addressing): the test asserts the
  structural mapping by `getattr` code/field and MUST **also** pass for a generic validation
  `code` (e.g. `unsupported_parameter`), so no unverified literal is the sole path.
- Some other validation `code` (no `supported_values`) → generic request/contract `AdapterError`.
- A plain `BaseException` with **none** of the discriminator attributes → retriable
  `AdapterError`, proving the `getattr`-None defaults make the mapper total (no `AttributeError`).

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestErrorMapping
# (1) pyright: catches the TYPE_CHECKING-under-strict-mode reportMissingImports failure mode:
uv run pyright src/sapphire_flow/adapters/recap_gateway.py
# (2) AST import guard: no `import recap_client` at any scope (top-level or inside a function):
uv run python -c "import ast; from pathlib import Path; tree=ast.parse(Path('src/sapphire_flow/adapters/recap_gateway.py').read_text()); bad=[n for n in ast.walk(tree) if (isinstance(n, ast.Import) and any(a.name.split('.')[0]=='recap_client' for a in n.names)) or (isinstance(n, ast.ImportFrom) and (n.module or '').split('.')[0]=='recap_client')]; assert not bad, f'recap_client import found at line(s) {[n.lineno for n in bad]}'"
# (3) Substring backstop: catch dynamic imports / string module refs the AST check cannot see:
uv run python -c "from pathlib import Path; assert 'recap_client' not in Path('src/sapphire_flow/adapters/recap_gateway.py').read_text(), 'recap_client referenced (possibly dynamically)'"
```

## Whole-Plan Exit Gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

(Plan 081 adds no live/slow tests, so no optional-live-exclusion gate is needed here; it
lives in Plan 082.)

## Open Gateway Questions Scoped to Plan 081

None — the Gateway-dev agenda was closed on 2026-06-25:

- String feature `name`s are echoed as columns (constraint: no leading `0`,
  satisfied by `g_`); validating the echo and banded behavior against a SAP3-compliant
  test shapefile is a Plan 082 live-smoke task, not an open question.
- `cf` is not exposed because ECMWF discontinued it; `fc` HRES is the correct control.
- Snow variable names are stable (`hs`/`rof`/`swe`).

## Upstream Issues to File Against recap-dg-client

> **Owner action item — NOT a gated task.** A point-in-time snapshot (clone `b3ce520`), not
> a maintained tracker; no Phase 1/2/3 task owns filing these. Filing them in
> `hydrosolutions/recap-dg-client` is an owner action outside the gated task list; once filed,
> the GitHub tracker is the source of truth and this list will go stale.

- No retry/backoff; every export uses one `requests.get`.
- Temporary parquet files are written under `Path.cwd()` rather than a safe
  tempfile directory, creating race/litter/orphan risks under concurrency.
- API key is sent as an `api_key` query parameter; `_auth_headers()` is a dead
  empty hook. Prefer header auth to reduce log/proxy leakage.
- README quickstart uses `http://` while `verify_tls=True` is the default; this
  invites plaintext API-key transmission.
- Ensure `cf` is never reintroduced as an accepted `ifs_type` (clone `b3ce520` already
  documents only `fc`/`pf` — a "keep it removed" note, not an open bug).
- `member` is typed as `str | None` but behaves as integer 1..50 for `pf`; add
  validation for `ifs_type`, `member`, `level_type`, and `subdaily_resolution`
  using `Literal`/domain types.
- `member=None` on `pf` and invalid members return miscoded
  `invalid_date_range`, which hides request-construction errors.
- Tests cover error mapping only; add parameter-assembly and returned-DataFrame
  shape contract tests.
- No clear CI/release tags beyond version `0.1.0`.
- Both `pyproject.toml` and `setup.py` exist; consolidate build metadata.
- Return contract is undocumented in code: index timezone/name, units, one
  variable per call, and column semantics should be explicit.
- Distribution by git pin would repeat the ForecastInterface CI wheel-only-guard
  exception. Publish a versioned hydrosolutions private-index wheel before SAP3
  depends on it.

## Risks and Recommendation

| Risk | Impact | Mitigation in Plan 081 |
|---|---|---|
| `fc`=HRES used as ensemble control | Raised as a G7-deviation risk. | Resolved: ECMWF discontinued `cf` → HRES `fc` is the correct control (G7-compliant). Pin member ids in tests. |
| Variable/unit drift | Bad model inputs / mixed units. | Central Recap catalog + unit tests; snow names committed, source-unit factors deferred to Plan 082 (081 commits names only). |
| Swiss behavior regression | Existing v0 adapter breaks. | Recap catalog is a separate, never-merged structure; exact-tuple `PARAM_GROUPS` pin (Task 2A) guards fetch keys + ICON de-accumulation. |
| Git-pin dependency (private repo) | CI/build instability + supply-chain exception; needs clone auth. | ForecastInterface pattern (079/080): scoped wheel-only-guard exception now, private-index wheel later. Committed in Plan 082. |

Recommendation: **promote Plan 081 to READY once the user accepts the split**.
It is implementable offline with fake-client contract tests. Do not wait for
Gateway live answers; those are Plan 082.

## Review History

Adversarial, code-grounded review — Claude design/feasibility/completeness/proportionality
panel + Codex repo-grounded (`file:line` against this repo AND the `../recap-dg-client`
clone), every round. No model approved its own output. All rounds 2026-07-15.

| Round | Reviewers | Outcome | Blocking | Status | Key result |
|---|---|---|---|---|---|
| 1 | panel + Codex (4 rounds) | ESCALATE | 7 | resolved | Converged blockers→0 but stalled on 3 design forks the planner would not decide: resolver-miss blast radius, concrete-resolver ownership, banded-reanalysis scope. Surfaced for the owner. |
| 2 | owner | decisions | — | resolved | Owner settled the forks: `GatewayResolutionError(AdapterError)`; DHM-onboarding owns the concrete resolver; basin-average reanalysis first, bands deferred. |
| 3 | panel + Codex (3 rounds) | ESCALATE | 2+3 | resolved | Deeper: the basin-average reanalysis choice left the forecast path half-banded — a 1:1 resolver that cannot express bands, and 082's validator blocking banded bindings. Root fix: basin-average end-to-end (train/serve spatial consistency), banding deferred as one extension. |
| 4 | Codex (final, trimmed doc) | NEEDS_CHANGES | 0 | resolved | One gate-coverage gap: Task 3A/3C config-pre-filter gates did not assert all three exclusions (wrong-source/inactive/non-basin-average). Fixed. |
| 5 | Codex (post-115a re-verify) | APPROVE | 0 | user-confirmed | Re-grounded against merged Plan 115a (`bced53d`): all citations corrected, dispatch section reworked (115a is existing code, `_select_nwp_source` retired, contradiction resolved), the `NWP_SOURCE` deliverable confirmed still-correct (role is additive, not a replacement). No residual findings. Owner promoted to READY. |

Process notes: the review loop's own revise-steps ballooned the doc to ~1490 lines; a
subtractive right-sizing pass cut it back (−386) before the final Codex round. Review 5 was
triggered by the discovery that rounds 1–4 had grounded against a stale checkout predating
115a's merge — re-verification against the current tree closed that gap.

## References

- Plan 082: `docs/plans/082-recap-gateway-operational-readiness.md`
- `docs/requirements/01-data-gateway-requirements.md`
- `docs/requirements/00-internal-gap-analysis.md`
- `docs/v0-scope.md` section I
- Plans 079/080 for the ForecastInterface git-pin/wheel-distribution precedent

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Contract docs, metadata, dependency strategy",
      "tasks": ["1B", "1C", "1D"],
      "parallel": false
    },
    {
      "id": "phase-2",
      "name": "Variable catalog and converter foundations",
      "tasks": ["2A", "2B"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "name": "Offline Recap adapter implementation",
      "tasks": ["3A", "3B", "3C", "3D"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ]
}
```
