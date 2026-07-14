---
status: DRAFT
created: 2026-07-14
plan: 115
title: Weather-source identity model — umbrella
scope: The single owning track for weather ingest + management. Umbrella over 115a/115b/115c. Supersedes 114; gates 081/082/113.
depends_on: []
blocks: [081, 082, 113]
supersedes: [114]
children: [115a, 115b, 115c]
---

# Plan 115 — Weather-source identity model (umbrella)

## Status

**DRAFT — umbrella.** No code lands from *this* document; it holds the shared analysis and
the locked decisions so the three child plans need not repeat them.

- **[115a — identity, accessors, consumers](115a-weather-source-identity-schema.md)** — schema +
  routing. No behaviour flip. **This is what unblocks 081/082.**
- **[115b — Flow 6 reachability + hybrid default](115b-weather-flow6-reachability.md)** — the
  risky one. Deserves to fail alone.
- **[115c — cleanup](115c-weather-identity-cleanup.md)** — `0031` NOT NULL, API/dashboard, docs.

**⛔ The live DB audit (§Audit) gates 115a READY.** Mac-mini unreachable 2026-07-14.

## Why this track exists

Weather ingestion was designed along **two independent lines that never reconciled**
(independent Codex architecture investigation, 2026-07-14):

- The **071/072 line** — `historical_forcing.source` is **immutable product provenance**;
  multi-source reads are resolved at *read* time (`HybridForcingSource`, `PerSourceStoreReader`).
- The **081/082/114 line** — `station_weather_sources.nwp_source` is a **role-specific
  operational binding**.

**They collide inside Flow 6**: it selects by binding identity, stores by product provenance,
and the default reader looks up by binding identity again.

Root cause: **`nwp_source` is used as four things at once** — station binding key, adapter
selector, forecast storage key, and historical provenance tag.

| symptom | where |
|---|---|
| no forecast/reanalysis role; the forecast path can select the reanalysis source | `types/station.py`, `run_forecast_cycle.py::_select_nwp_source` |
| unfiltered binding lists reach consumers that cannot cope — found by **three** separate reviews, each in a **different** consumer | `run_forecast_cycle.py:1247`, `operational_inputs.py:327`, `hindcast.py:287/455`, `training_data.py:181`, `onboard_model.py:527` |
| Flow 6 can select **zero** configs and report **success** (`0/0/0`) | `ingest_weather_history.py:309` |
| Flow 6's rows are **unreadable** by the default reader even when it runs | writes product tags (`meteoswiss_open_data_reanalysis.py:251`) / reads binding name (`store_backed_reanalysis.py:31`) |
| `extraction_type` is **already wrong** in the DB | CAMELS forcing is `BASIN_AVERAGE` (`camelsch_adapter.py:130`), onboarding writes the binding `POINT` (`onboarding.py:364`) |
| `config.toml`'s adapter `type` is **decorative** — runtime hardcodes the adapter | `run_forecast_cycle.py:1090`, `ingest_weather_history.py:168` |

**Plan 114 is superseded.** It diagnosed one facet (the missing role) and failed three
successive reviews, each finding a different missed consumer or a false rationale — because it
was patching a symptom of an identity problem it did not own. Its reviewed content is carried
into 115a/115c.

## Locked decisions (owner, 2026-07-14)

### D1 — Split binding identity from data provenance

- **`station_weather_sources` = OPERATIONAL BINDING.** *"Where do I get data for this station,
  and what is it for?"* — `(station_id, nwp_source, role, extraction_type, status)`.
- **`historical_forcing.source` = PROVENANCE.** *"Where did this number actually come from?"* —
  `camels-ch`, `meteoswiss_rprelimd`, `meteoswiss_tabsd`, …

Provenance is **preserved, never collapsed**: preliminary (`rprelimd`) vs definitive (`tabsd`)
is a real distinction that will matter when definitive data supersedes preliminary.

> **Rejected:** having Flow 6 write under the binding name to force one namespace. Simpler, and
> the `single` reader would work immediately — but it destroys product provenance.

**Invariant (owner decision): one `nwp_source` string serves exactly ONE role for a station.**
The PK is `(station_id, nwp_source)` (`db/metadata.py:186`), so a name cannot hold two roles —
a second row would silently overwrite the first via the upsert (`station_store.py:243`). This
already holds in practice (Nepal: `ifs_ecmwf` forecast, `era5_land` reanalysis; a snow forecast
and a snow reanalysis are likewise distinct names). **Enforce it loudly**; do not migrate the key.
*(An earlier draft claimed such a source would be "two bindings under the PK" — that was
incoherent. Corrected after review.)*

### D2 — `hybrid` becomes the production default for reanalysis reads

> ## ⚠️ D2 IS SUPERSEDED by [115b §0](115b-weather-flow6-reachability.md) (owner, 2026-07-14)
>
> **What survives:** `hybrid` still becomes the production default. It is the only reader that resolves
> across provenance tags, and we need exactly that.
>
> **What changed — the chain contents.** D2 was written to read across **CAMELS + MeteoSwiss product
> tags**. Under §0 there is **no CAMELS tier at all**:
>
> ```
> precipitation:   RHIRESD → RPRELIMD     (definitive supersedes preliminary)
> temperature:     TABSD
> temperature_min: TMIND
> temperature_max: TMAXD
> ```
>
> **Why:** CAMELS-CH forcing is built on **`RhiresD`** (confirmed: Höge et al. 2023, *ESSD* 15, 5755,
> App. A1.2), while Flow 6 was ingesting **`RprelimD`** — the *preliminary* product. Plan 071 chose it
> on the false premise that daily `RhiresD` needs a commercial licence; **it is free in open data, daily,
> back to 1961**, in the collection the adapter already points at. So we derive the **whole series
> ourselves** (RhiresD + TabsD/TminD/TmaxD, 1981 → T-45d, **our** basin polygons), use `RprelimD` for the
> live tail only, and **supersede** it when `RhiresD` publishes. **CAMELS forcing becomes a validation
> reference, not a data tier.**
>
> Read D2 below as *"hybrid is the right reader"* — and 115b §0 for *what it reads*.

**Corrected after review — the earlier rationale was wrong.** `HybridForcingSource` is **not** a
binding→provenance resolver. It fans out to a **deployment-global, hardcoded per-parameter
priority chain** (`hybrid_reanalysis_factories.py:26-32`: precipitation →
`(METEOSWISS_RPRELIMD, CAMELS_CH)`, temperature → `(METEOSWISS_TABSD, CAMELS_CH)`, …), and each
child `PerSourceStoreReader` reads a **fixed** source tag keyed only by `station_id`
(`per_source_store_reader.py:45-52`). The binding contributes nothing but the station ID.

The **effect** still stands and is what we want: hybrid is the only reader that can see Flow 6's
product-tag rows, and it merges them with CAMELS by priority. But the resolution is **global
config, not per-binding** — say so plainly rather than dressing it up as an identity resolver.

Two consequences, both owned by **115b**:

1. **⚠️ Hybrid silently DROPS any parameter outside the four hardcoded chains.**
   `hybrid_reanalysis.py:66-72`: `chain = self._priority.get(key[2], ())` → `winner = next(...)`
   over an empty chain → `None` → `continue`, and the row is discarded. `StoreBackedReanalysisSource`
   (today's default) passes **any** parameter through. **Flipping the default as-is is a silent
   data-loss regression** — the exact bug family this track exists to kill. Must be fixed *before*
   the flip: an unknown parameter must pass through or raise, never vanish.
2. **The chain is Swiss-hardcoded.** There is no ERA5-Land tier. Nepal needs one — a dependency
   for 081/082, not a blocker for 115a.

### D3 — Role filtering moves into the store

Replace the raw `fetch_weather_sources(station_id)` with **role-scoped accessors**:

```python
def fetch_forecast_binding(station_id) -> StationWeatherSource        # exactly 1, else ConfigurationError
def fetch_reanalysis_bindings(station_id) -> list[StationWeatherSource]  # 0..n
```

A caller then **cannot obtain** an unfiltered mixed list to misuse. This is the `CLAUDE.md`
"invalid states unrepresentable" discipline, and the only option that structurally ends the
whack-a-mole: **three** reviews each found a **different** consumer that forgot to filter, which
is the signature of a missing type, not of careless callers.

`fetch_weather_sources` survives **only** for display (`api/routes/api_stations.py:181`), which
legitimately wants every binding — now showing each one's role.

## Audit — RUN 2026-07-14 (staging, mac-mini). Results below.

> ## ✅ AUDIT COMPLETE — and it confirms the diagnosis
>
> | | result |
> |---|---|
> | **A1** forecast-binding cardinality | ✅ **PASS — 0 rows.** Both operational stations (`Porte_du_Scex`, `Rheinfelden-Messstation`) have **exactly 1** forecast binding + 1 reanalysis binding. **No station breaks under 115a.** 115a is cleared to land. |
> | **A2** source allowlist | ✅ **Complete.** The only bindings that exist are `camels-ch`/`point`/`active` and `icon_ch2_eps`/`basin_average`/`active`. The backfill rule (`icon_ch2_eps` → FORECAST, else REANALYSIS) covers reality exactly, with nothing left over. |
> | **A3** the dark feed | 🔴 **CONFIRMED — and worse than suspected.** |
>
> ### A3 — `historical_forcing`, in full:
>
> ```
>   source   | count |   first    |    last
> -----------+-------+------------+------------
>  camels-ch | 58440 | 1981-01-01 | 2020-12-31
> ```
>
> **One source. `camels-ch`. Nothing else.** No `meteoswiss_rprelimd`, no `tabsd`, no `tmind`, no
> `tmaxd`. The scheduled `ingest-weather-history` deployment has **never stored a single row in
> production** — and the forcing archive is **frozen at 2020-12-31**, five and a half years stale.
>
> The flow has been reporting **green** for its entire operational life while doing nothing at all.
> Both halves of the double-dark diagnosis are now fact, not inference:
> **(1)** no `meteoswiss_open_data_reanalysis` binding exists → Flow 6 matches zero stations and
> returns `0/0/0` as a success; **(2)** even had it run, its product-tag rows were unreadable by the
> `single` default reader.
>
> **Consequence: 115b is a FIRST IMPLEMENTATION, not a fix.** There is no MeteoSwiss reanalysis data
> to repair — there has never been any. Any model needing recent past-dynamic forcing has been
> unservable since 2020, and would be silently unservable for Nepal too.
>
> *(It has not yet bitten operationally only because today's models declare no past-dynamic weather
> features — see A4. That is luck, not design.)*

The queries, retained for re-running against production:

### A1 — Forecast-binding cardinality (THE gating query)

*(Blocker from review round 6. The earlier audit could not prove 115a safe to land — it checked
source names but never the thing 115a makes **fatal**.)*

115a's `fetch_forecast_binding` **raises** on 0 forecast bindings, and retires the fallback that
today silently returns `icon_ch2_eps` (`run_forecast_cycle.py:106`). **A station with zero forecast
bindings currently forecasts fine** — the fallback covers it, and `operational_inputs.py:356` only
hard-skips when *future* NWP features are required, which today's models mostly do not need. Such a
station would **start failing on the day 115a ships**.

So the audit must count roles **per operational forecast station**, applying the post-`0030` backfill
rule:

```sql
-- Forecast-binding count per operational station, under the 0030 backfill rule.
-- Expected: exactly 1 for every operational forecast target. Anything else is a finding.
SELECT s.id, s.name,
       COUNT(*) FILTER (WHERE ws.nwp_source = 'icon_ch2_eps')  AS forecast_bindings,
       COUNT(*) FILTER (WHERE ws.nwp_source <> 'icon_ch2_eps') AS reanalysis_bindings
  FROM stations s
  LEFT JOIN station_weather_sources ws ON ws.station_id = s.id
 WHERE s.station_status = 'operational'
   AND s.station_kind <> 'weather'
 GROUP BY s.id, s.name
HAVING COUNT(*) FILTER (WHERE ws.nwp_source = 'icon_ch2_eps') <> 1;
```

**Every row this returns is a station that forecasts today and would break under 115a.** Each needs
an explicit owner decision — backfill the binding, or accept the loud skip. **115a cannot be READY
until this returns zero rows, or every row it returns has a decision attached.**

### A2 — Source allowlist (does the migration's `CASE` cover reality?)

```sql
SELECT DISTINCT nwp_source, extraction_type, status FROM station_weather_sources;
```

### A3 — Is the reanalysis feed dark, and is forcing frozen?

```sql
SELECT source, COUNT(*), MIN(valid_time), MAX(valid_time) FROM historical_forcing GROUP BY source;
```

`MAX(valid_time)` per source shows instantly whether `historical_forcing` is **frozen at the CAMELS
import**. **If frozen, 115b is a first implementation, not a fix** — and past-dynamic features have
been stale in every forecast since onboarding.

Without the DB: **is `weather_history.no_stations` firing** in the `ingest-weather-history` Prefect
logs? That alone proves the dark feed.

### A4 — Active-model provenance (gates 115b's flip, not 115a)

Which models are active, and do their `past_dynamic` / `future_dynamic` requirements draw on forcing
that the hybrid flip would re-source? Plan 072 §175 already recorded the **distribution-shift risk**
of a `single`→`hybrid` flip: a model trained on CAMELS-sourced features that suddenly reads
MeteoSwiss-sourced features is being fed a different distribution than it was fitted on. Review
found that today's registered models are **probably** unaffected (native/fallback models declare no
past/future dynamic features; the FI NWP model needs only *future* precip/temperature) — but that is
a repo-level inference, and the live artifact/assignment table is the only thing that settles it.

**Blocked 2026-07-14:** mac-mini unreachable (full-subnet sweep: no ICMP, no SSH, no ARP). The
host has **no power management configured anywhere in the repo** (`scripts/bootstrap-mac-mini.sh`
covers Docker/secrets/disk/LaunchAgents but no `pmset`, no wake-on-LAN, no auto-restart, no static
IP) — it sleeps and silently drops off the network. Worth fixing on its own; it is the same host
as the Plan 100 blackout.

## Relationship to other plans

- **114** — superseded; content carried into 115a/115c.
- **081** (gateway adapter) — can be *built* in parallel; only *correct* on this identity model.
  Note `config.toml`'s adapter `type` is decorative, so 081's adapter can be fully built and still
  be **dead in production wiring**; 081/082 own that dispatch fix, this track owns the identity it
  dispatches on. Nepal also needs an ERA5-Land tier in the hybrid chain (D2).
- **082 Task 2C** — depends on **115a** (`082.depends_on`: `115`).
- **113** (schedule alignment) — sequence after; align the schedule once the source path is coherent.
- **091** — stale against current code (claims 090 unmerged, and that `mac-mini.toml` disables NWP;
  neither is true). Flag for cleanup, out of scope.

## Track sequence

1. **Live DB audit** — gates 115a.
2. **115a** — identity, accessors, consumer rewiring, migration `0030`, containment.
3. **081/082** — gateway adapter + dispatch, on a correct foundation. *(Parallel with 115b.)*
4. **115b** — Flow 6 reachability, hybrid parameter-drop fix + default flip, existing-station backfill.
5. **115c** — `0031` NOT NULL, API/dashboard role, doc sync.
6. **082 Task 3B** — parametric multi-year backfill (Flow 6 is hardcoded to 60 days,
   `ingest_weather_history.py:50`).
7. **113** — schedule alignment.

## Review history

Six rounds. Each one falsified something the previous round was confident about.

1. Grill-me (2026-07-13, as 114).
2-3. Plan-review loop rounds 1-2 — **escalated** (stalled, 2 blockers + 2 majors), owner-resolved.
   *The loop also **introduced** a bogus `status == ACTIVE` filter and a false Provenance rationale
   of its own.*
4. Independent Codex review of 114 — NOT-READY. Found the plan fixed the **reanalysis** direction and
   left the **forecast** direction wide open.
5. **Architecture investigation** (Codex) — found the actual root cause (`nwp_source` = four things),
   the two colliding design lines, and the double-dark Flow 6. **114 superseded** → this track.
6. Independent Codex review of 115 — NOT-READY, **3 claims falsified** → the 3-way split.
7. Independent Codex review of 115a/b/c — **all three NOT-READY**, 4 blockers, and it falsified the
   split's own claims (below). Folded 2026-07-14.

**Falsified claims worth remembering** — the track's recurring failure mode is *confident
completeness*:

- "every consumer is accounted for" — **false three separate times**, each in a different consumer.
- "hybrid is the binding→provenance resolver" — false; it is a deployment-global priority chain.
- "creating the binding at onboarding makes Flow 6 non-empty" — false for **existing** stations.
- "115a is no behaviour change" — false; it has exactly one, now named (zero-forecast-binding
  stations start being skipped).
- "the live DB audit gates 115a" — **incomplete**: the audit did not check the one thing 115a makes
  fatal (forecast-binding cardinality). Now A1, and it is the gating query.
- "115c is non-gating" — false while it held doc-sync that 115a's own schema change requires.

**The lesson, recorded so the next agent does not relearn it:** every automated round converged on
its own output and had to be caught from outside. When a plan fails review repeatedly, suspect the
*unit of work*, not the reviewer.
