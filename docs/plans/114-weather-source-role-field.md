---
status: DRAFT
created: 2026-07-13
plan: 114
title: StationWeatherSource forecast/reanalysis role field
scope: Swiss-testable schema + flow-filter change; prerequisite for 081/082 NWP-source dispatch
depends_on: []
blocks: [082]
---

# Plan 114 — `StationWeatherSource` forecast/reanalysis role field

## Status

**DRAFT.** Do not implement or dispatch subagents until promoted to READY.
Grill-me **DONE** (2026-07-13, 7 decisions locked below). Plan-review rounds 1 and 2
folded in (2026-07-14 — see § Review deltas). Round 2 **escalated** with 2 blockers +
2 majors; all four are now resolved by owner decision (2026-07-14, § Review deltas
round 2). Next: 1 independent (Codex) review → owner READY → WF2.

## Provenance

Surfaced by the **independent Codex review of the Plan 081 grill-me (2026-07-13)**.
The review found that the repo distinguishes a station's *operational forecast*
source from its *training/reanalysis* source only **implicitly, by
`extraction_type`**: Swiss onboarding stores `camels-ch`/`POINT` (reanalysis) +
`icon_ch2_eps`/`BASIN_AVERAGE` (forecast) (`services/onboarding.py::onboard_stations`,
Step 4b), and `_select_nwp_source` picks the `BASIN_AVERAGE` binding while
`_reanalysis_sources` matches by source name. **This collapses for Nepal**, where
gateway forcing is `BASIN_AVERAGE` for *both* IFS (forecast) and ERA5-Land
(reanalysis). Two concrete failures the implicit scheme cannot prevent:

- **`_select_nwp_source` non-determinism** — it returns the *first* `BASIN_AVERAGE`
  binding, with no ordering (`store/station_store.py::fetch_weather_sources`) and
  no role field on `StationWeatherSource` (`types/station.py::StationWeatherSource`),
  so a station with both an `ifs_ecmwf` and an `era5_land` `BASIN_AVERAGE` binding
  can route the forecast path to the reanalysis source
  (`flows/run_forecast_cycle.py::_select_nwp_source`).
- **Forecast/reanalysis source-key confusion** — with no role field, the
  `RecapGatewayAdapter` (Plan 081) is forced into a single `NWP_SOURCE` identity
  that cannot be both the IFS forecast storage key and the ERA5-Land reanalysis
  selector at once (Plan 081 "NWP-Source Dispatch Design"; Plan 082 Task 2C
  Phase A→B round-trip).

The implicit scheme is **already leaking today, not only under Nepal**:
`adapters/meteoswiss_open_data_reanalysis.py::fetch_reanalysis` — the adapter wired
as the production `[adapters.weather_reanalysis]` in `config.toml` — selects its
bindings on `extraction_type == BASIN_AVERAGE`, i.e. a *reanalysis* fetch keyed off
the very attribute `_select_nwp_source` treats as the *forecast* marker. The two
subsystems read the same field with opposite meanings. (Found in plan-review; it
also breaks the naive backfill — see §3.)

Decision (owner grill-me 2026-07-13): fix it at the root with an **explicit role
field**, not a fragile implicit proxy. This is the "invalid states unrepresentable"
/ enums-over-implicit-proxies discipline (`CLAUDE.md` Type Driven Development).

## Objective

Add an explicit `WeatherSourceRole` enum and a `role` field to
`StationWeatherSource`, migrate the store, set the role explicitly at onboarding,
and make every selector (both flows, the reanalysis adapter, **and every service
that assembles reanalysis inputs**) filter on `role` instead of inferring intent
from `extraction_type`. The change unblocks a correct multi-source (Nepal) dispatch
in Plans 081/082.

**Swiss behavior is preserved for every correctly-onboarded station** — i.e. every
station whose bindings onboarding actually wrote. It is **deliberately not**
preserved for one degenerate shape: a station with **zero** weather-source rows,
which today still forecasts via a hardcoded ICON fallback. That fallback is retired
(§5, owner decision 2026-07-14). This is the plan's single intentional behavior
change; it is called out rather than hidden behind a blanket "byte-identical" claim.

## Non-goals

- No gateway/Nepal wiring, no `RecapGatewayAdapter` (Plan 081), no dispatch
  generalization (Plan 082 Task 2C — this plan is its prerequisite).
- No change to `extraction_type` semantics or to the `(station_id, nwp_source)`
  uniqueness of a binding.

## Scope — grill-me decisions (locked 2026-07-13)

### 1. Type — `role` is a required enum field, no default

Add `WeatherSourceRole(Enum)` = `FORECAST = "forecast" | REANALYSIS = "reanalysis"`
to `types/enums.py` (mirrors the `WeatherSourceStatus` / `SpatialRepresentation`
lowercase-value convention). Add `role: WeatherSourceRole` to the frozen
`StationWeatherSource` (`types/station.py`).

**Required, with no default.** There is no sane default: a default would silently
mis-role exactly the Nepal bindings this plan exists to disambiguate, which is the
bug rather than a mitigation of it. A missing `role` must be a construction error,
not a guess.

**Two values suffice — no `BOTH`.** A source that serves both roles does not arise:
snow forecast and snow reanalysis are distinct `nwp_source` strings, hence distinct
bindings under the `(station_id, nwp_source)` primary key.

> **Scoped exception to "no guessing"** — the *legacy-row read path* during the
> migration window (§3) maps a `NULL` role via the documented backfill rule with a
> WARNING. That is boundary parsing of pre-114 DB rows ("parse, don't validate"),
> not a domain-type default: no in-repo `StationWeatherSource(...)` call site may
> omit `role`. The shim is deleted in the follow-on release (§3.1).

### 2. Construction-site sweep

`role=` must be added to **every** `StationWeatherSource(...)` call — dozens, spanning
`services/onboarding.py`, `store/station_store.py::_row_to_weather_source`, the fakes,
and the test fixtures. Because the field is required and keyword-only, pyright plus
failing constructors surface **every** miss; no site can be silently skipped, so no
exact inventory is carried here (it would only rot).

### 3. Store + migration

Add a `role` column to `station_weather_sources` in `db/metadata.py`, mirroring how
`extraction_type` and `status` are declared. Thread it through `store_weather_source`
(values **and** the `on_conflict_do_update` `set_` clause) and
`_row_to_weather_source` (`store/station_store.py` — note these are two separate
functions, `store_weather_source` and `_row_to_weather_source`; grep, do not trust a
line number).

**Backfill rule — by source name, NOT by `extraction_type`.** The original draft
backfilled `WHEN extraction_type = 'point' THEN 'reanalysis' ELSE 'forecast'`. Plan-review
falsified that: `adapters/meteoswiss_open_data_reanalysis.py::fetch_reanalysis` matches
`extraction_type == BASIN_AVERAGE` for a **reanalysis** fetch, so any row it serves
(`nwp_source = 'meteoswiss_open_data_reanalysis'`, `BASIN_AVERAGE`) would be backfilled
`FORECAST` — the exact inversion of its real role — and then be silently dropped by the
new role-filtered `_reanalysis_sources`. The correct invariant is that **`FORECAST` is the
closed set**: the only writer of a forecast binding is `services/onboarding.py`, which
hard-codes `nwp_source="icon_ch2_eps"` (the same literal as
`run_forecast_cycle.py::_ICON_NWP_SOURCE`). Every other binding onboarding writes is a
forcing/reanalysis binding whose name comes from the data (`forcing[0].source`).

```sql
UPDATE station_weather_sources
   SET role = CASE WHEN nwp_source = 'icon_ch2_eps' THEN 'forecast'
                   ELSE 'reanalysis' END;
```

**Pre-flight audit (mandatory, blocks the migration).** Before trusting the CASE, run
against staging **and** production:

```sql
SELECT DISTINCT nwp_source, extraction_type FROM station_weather_sources;
```

Expected set: `icon_ch2_eps`/`BASIN_AVERAGE`, `camels-ch`/`POINT`,
`meteoswiss_open_data_reanalysis`/`BASIN_AVERAGE`. The migration carries the same guard
in code — it raises if any `nwp_source` falls outside that allowlist, rather than guessing
a role for an unknown source. An unknown name is a human decision, not a `CASE` fallthrough.

**Release split (cicd.md compliance).** `docs/standards/cicd.md` § Rollback: *"Migrations
must be backwards-compatible for one version (additive only: new columns nullable, no
destructive changes in a single release)."* A single revision that adds `role` **NOT NULL**
would break exactly that: the pre-114 image's `store_weather_source` / onboarding never sets
`role`, so it could not insert or upsert a `station_weather_sources` row against the new
schema, killing the documented rollback path (restore backup + redeploy previous tag). The
draft's one-shot NOT NULL is therefore **split into two releases**:

**Revision `0030`** (this plan; off head `0029`, `alembic/versions/0029_hindcast_dedup_constraint.py`
— chain committed and continuous):

1. `add_column` `role` **nullable**,
2. pre-flight allowlist guard (raise on unknown `nwp_source`),
3. backfill via the CASE above,
4. `CheckConstraint("role IS NULL OR role IN ('forecast', 'reanalysis')")` — NULL-tolerant,
   so the previous image tag can still write during the rollback window.

App-side in the same release: `role` is required on the dataclass and written at **every**
site, so the new image never emits a NULL. `_row_to_weather_source` carries a **transitional
NULL shim** — a NULL role (only reachable if the *old* image wrote a row during the window)
is mapped by the same rule (`nwp_source == "icon_ch2_eps"` → FORECAST, else REANALYSIS) and
logged at WARNING (`weather_source.legacy_null_role`). Explicitly marked `# Plan 114 §3.1:
delete with revision 0031`.

**Trade-off noted (not a silent regression):** this keeps the rollback window open at the
cost of one release during which the DB *can* hold a NULL role. The type stays required; the
shim is a boundary parse, not a default (§1).

#### 3.1 Follow-on release (tracked here, ships after the rollback window closes)

**Revision `0031`**: re-run the backfill for any straggler NULLs, `alter_column role
nullable=False`, tighten the check to `role IN ('forecast', 'reanalysis')`, and delete the
`_row_to_weather_source` NULL shim. This is a ~30-line follow-up; it is listed in this plan
so it is not lost, but it does **not** gate Plans 081/082 (which need the field, not the
constraint).

### 4. Onboarding sets the role explicitly

`services/onboarding.py::onboard_stations` (Step 4b — the two
`StationWeatherSource(...)` constructions): the forcing binding (`forcing[0].source`,
e.g. `camels-ch`) → `role=REANALYSIS`; the `icon_ch2_eps` binding → `role=FORECAST`.
Explicit at the construction site; the backfill rule is never re-derived here.

### 5. Flow 1 — `_select_nwp_source` becomes a role lookup that fails loudly **but locally**

`flows/run_forecast_cycle.py::_select_nwp_source` currently runs a two-pass heuristic:
exact `icon_ch2_eps` match, then first `BASIN_AVERAGE` binding, then a `_ICON_NWP_SOURCE`
fallback string. **Retire all three passes, the `_ICON_NWP_SOURCE` fallback, and the
now-false docstring.**

Replacement: select the single binding with `role == FORECAST`. Raise
`ConfigurationError` (`exceptions.py::ConfigurationError`) when there is **0** or **more
than 1** — both are station-config faults that must surface at the boundary rather than be
papered over by picking a member of the set. This is what makes the selection deterministic
for a Nepal station carrying two `BASIN_AVERAGE` bindings; the old code's non-determinism
came precisely from tolerating an ambiguous set.

**Role only — no `status` filter (owner decision 2026-07-14).** An earlier draft of this
section said "the single *active* binding". That silently introduced a `status == ACTIVE`
filter that **does not exist today**: neither pass of the current `_select_nwp_source` checks
`ws.status`, and `fetch_weather_sources` returns all rows regardless of status, so a station
with an INACTIVE `icon_ch2_eps` binding *is* currently selected and forecast. Adding the
filter here would silently start skipping such stations on day one. Plan 114 stays chartered
to **role disambiguation**; selection ignores `status` exactly as it does today.
> **Follow-up (not this plan):** that an INACTIVE binding still drives a forecast is a real
> bug. It needs its own plan and its own decision about what deactivating a source should
> mean operationally. Do not smuggle the fix in here.

**The 0-binding case hard-fails; the `_ICON_NWP_SOURCE` fallback is retired (owner decision
2026-07-14).** Today `_select_nwp_source` ends with `return _ICON_NWP_SOURCE`, and
`tests/unit/flows/test_run_forecast_cycle.py::test_falls_back_to_underscore_icon_source_string`
locks that in: it clears **all** weather-source rows and asserts a forecast is still stored.
That fallback *guesses* a Swiss source string — precisely the guessing this plan exists to
kill — and is flatly wrong for Nepal, where no ICON source exists. A station with no FORECAST
binding is misconfigured, and post-114 it fails loudly and locally (contained per below)
rather than silently forecasting off an assumed source.

**Required test change (in scope, not a surprise):** `test_falls_back_to_underscore_icon_source_string`
is **rewritten**, not deleted — same zero-weather-source setup, but it now asserts the loud
contained skip (`stations_failed == 1`, an entry in `errors`, `forecasts_stored == 0`, flow
returns normally). The underscore-vs-hyphen spelling bug it originally guarded is **still
covered** by the sibling tests that exercise a real `icon_ch2_eps` binding
(`test_exact_icon_wins_over_earlier_basin_average_source` and the deterministic-selection test
above it), so retiring the fallback does not reopen that bug.

**The raise MUST be contained at the per-station call site (blocker from plan-review).**
The two call sites have asymmetric exception context, and only one is safe today:

- **Group loop** (`run_forecast_cycle.py`, the `_select_nwp_source` dict-comprehension inside
  the per-group `try:`) — **already contained**: the group `try` has `except StoreError: raise`
  followed by `except Exception as exc:` which logs `forecast_cycle.group_forecast_failed` and
  `continue`s to the next group. A `ConfigurationError` there fails that one group. No change
  needed. (It does fail the *whole* group, since the comprehension spans the group's stations —
  correct: a group forecast needs all its members.)
- **Per-station loop** (`for station in operational:` → `nwp_source: str = _select_nwp_source(...)`)
  — **NOT contained**. The call sits *before* the nearest `try:` (which wraps only
  `assemble_station_operational_inputs`), and the function-level `try:` opened near the top of
  `run_forecast_cycle_flow` has **no `except` at all** — verified: the only clause at that
  indent level is a `finally:`. An uncaught `ConfigurationError` would therefore propagate out
  of the entire flow, aborting the cycle for **every** station and **every** group — one
  mis-bound station taking down a ~1000-station run, and directly contradicting the function's
  own per-station fault-isolation convention.

**Required implementation step** (not optional, not "if convenient"): wrap the per-station
`_select_nwp_source(...)` call in its own `try/except ConfigurationError`, mirroring the
*existing* pattern used for the structurally identical "configured model missing" config fault
in the same loop — log an error event (`forecast_cycle.station_skipped_bad_weather_source_config`),
`errors.append(...)`, `stations_failed += 1`, unbind the `station_id` contextvar, `continue`.
Loud, attributable, and isolated: the misconfigured station fails; the cycle does not.

> Rejected alternative: making `_select_nwp_source` return `str | None` and treating `None` as
> a soft skip. It buries a config fault as a routine "no NWP" skip (indistinguishable from the
> legitimate `inputs_result is None` path) and re-introduces the silent-wrong-source class of
> bug this plan exists to kill. Raise + contain keeps the loud signal *and* the isolation.

### 6. Role-based selection everywhere reanalysis is chosen

An earlier draft of this section covered only Flow 6 and the MeteoSwiss adapter. Plan-review
round 2 falsified that as **sufficient**: the *live* forecast path goes through **neither**.
The full set of places that choose reanalysis bindings — all of which must filter on `role`:

**6a. The service call sites (the ones that actually matter).** Four sites hand the **raw,
unfiltered** `station_store.fetch_weather_sources(station_id)` list straight into
`fetch_reanalysis(station_configs=...)`:

- `services/operational_inputs.py::assemble_station_operational_inputs` (~:327-329) — the
  **live per-station past-dynamic assembly**, called from the operational forecast cycle,
- `services/hindcast.py` (~:305-306 and ~:499-500) — both hindcast paths,
- `services/training_data.py` (~:186-187).

Add a shared helper — `reanalysis_bindings(sources) -> list[StationWeatherSource]`, filtering
`role is WeatherSourceRole.REANALYSIS` — and apply it at **all four** call sites before the
list is passed as `station_configs`. One helper, one definition of "the reanalysis bindings",
used everywhere the question is asked.

**6b. Guard inside the source implementations too (owner decision 2026-07-14: belt-and-braces).**
Call-site filtering alone leaves a future caller free to reintroduce the bug by passing the raw
list. So each `WeatherReanalysisSource` implementation that iterates `station_configs` also
skips non-REANALYSIS configs:

- `adapters/store_backed_reanalysis.py::StoreBackedReanalysisSource.fetch_reanalysis` — **this
  is the concrete source wired behind all four call sites above** (via
  `adapters/hybrid_reanalysis_factories.py::select_reanalysis_source`), and it does **no**
  role/extraction filtering whatsoever today: it calls
  `fetch_forcing(source=cfg.nwp_source, ...)` for **every** config it is handed. Add the
  `role is REANALYSIS` skip.
- `adapters/meteoswiss_open_data_reanalysis.py::fetch_reanalysis` — add
  `c.role is WeatherSourceRole.REANALYSIS` to its `station_configs` match. Its existing
  `extraction_type == BASIN_AVERAGE` check **stays**: there it is a genuine emission-shape
  guard (the adapter only emits basin-average rows), not a role proxy.

**6c. Flow 6** — `flows/ingest_weather_history.py::_reanalysis_sources` — add
`source.role is WeatherSourceRole.REANALYSIS` to the existing `nwp_source` name match.

**Why this is the blocker, not a nicety.** For a Nepal station carrying an IFS/FORECAST and an
ERA5-Land/REANALYSIS binding (both `BASIN_AVERAGE`), the unguarded path issues a
`fetch_forcing(source=<the FORECAST nwp_source>)` alongside the reanalysis one and **silently
merges whatever rows exist under the forecast source name into the "past dynamic" (reanalysis)
features** used for live forecasting, hindcast, and training. That is the exact
silent-wrong-source class of bug this plan exists to eliminate — relocated into the data path
that matters rather than removed. It is benign in Switzerland today only by accident (no
forcing rows are stored under `icon_ch2_eps`); it would not be benign for Nepal, and Plan 082
would inherit it.

### 7. API + dashboard surface the role

The operator-facing surface for verifying a station's FORECAST vs REANALYSIS bindings is the
station-detail page — the whole motivation for this plan. Pyright will **not** catch this gap
(`WeatherSourceResponse` is a separate Pydantic model, not a `StationWeatherSource`
construction site), so it is an explicit task:

- `api/schemas.py::WeatherSourceResponse` — add `role: str`.
- `api/routes/api_stations.py::_to_weather_source_response` — populate `role=ws.role.value`.
- `api/templates/stations/detail.html` — add a `Role` column to the Weather Sources table,
  alongside the existing Extraction / Status columns.

### 8. Tests

- Swiss round-trip is **unchanged** for every correctly-onboarded station (regression floor:
  the existing onboarding → Flow 1 → Flow 6 behaviour must be identical). The **one**
  sanctioned exception is the zero-weather-source station (§5).
- A station with **two `BASIN_AVERAGE` bindings** (one FORECAST, one REANALYSIS) resolves
  each path to the correct source by role — the Nepal shape, testable on Swiss infrastructure
  today.
- A forecast target with **0 FORECAST bindings** raises `ConfigurationError` (unit).
- A forecast target with **2 FORECAST bindings** raises `ConfigurationError` (unit).
- **A FORECAST binding with `status = INACTIVE` is still selected** (unit) — locks the
  owner decision that Plan 114 does *not* add a status filter (§5), so a later change
  cannot silently introduce one.
- **Rewritten:** `test_falls_back_to_underscore_icon_source_string` — same zero-weather-source
  setup, now asserts the loud contained skip (`stations_failed == 1`, entry in `errors`,
  `forecasts_stored == 0`, flow returns normally) instead of the retired ICON fallback (§5).
- **The reanalysis-path blocker (§6), tested where it actually bites:** a station with two
  `BASIN_AVERAGE` bindings (IFS/FORECAST + ERA5-Land/REANALYSIS) is run through
  `assemble_station_operational_inputs` → `select_reanalysis_source(mode="single")` →
  `StoreBackedReanalysisSource`, and the **FORECAST binding's `nwp_source` is never queried**
  against the forcing store. Soundness: this test must **fail** against an implementation
  that passes the unfiltered `fetch_weather_sources()` list through. Equivalent coverage for
  `services/hindcast.py` and `services/training_data.py`.
- The `StoreBackedReanalysisSource` role guard holds **even when handed a raw unfiltered
  list** (locks §6b independently of the call-site helper).
- **Flow-level containment (locks the blocker fix):** in a cycle with several operational
  stations where exactly **one** has a broken role binding (0 or 2 FORECAST), the other
  stations still produce forecasts, the bad station is counted in `stations_failed` with an
  entry in `errors`, and the flow returns normally. Soundness: this test must **fail** against
  an implementation that raises out of the per-station loop uncontained.
- Migration backfill correctness, including the case plan-review found: a
  `meteoswiss_open_data_reanalysis` / `BASIN_AVERAGE` row backfills to **REANALYSIS**, not
  FORECAST; `icon_ch2_eps` / `BASIN_AVERAGE` → FORECAST; `camels-ch` / `POINT` → REANALYSIS.
- Migration allowlist guard raises on an unknown `nwp_source`.
- `_reanalysis_sources` and `fetch_reanalysis` both exclude a FORECAST-role binding that
  shares the reanalysis source name.
- Onboarding sets both roles.
- API: `WeatherSourceResponse` exposes `role`.

## Relationship to 081 / 082

- **Plan 081** (offline adapter) can be *built* in parallel — it does not need this field. But
  its "one adapter, two Protocols" dispatch design is only *correct* once this field exists
  (forecast storage keys off the `role==FORECAST` binding's source name; the adapter's
  `NWP_SOURCE` is the reanalysis identity only).
- **Plan 082 Task 2C** (dispatch implementation) **depends on this plan** — its Phase A→B
  round-trip and `_select_nwp_source`/`_reanalysis_sources` wiring assume role-based
  selection. `082.depends_on` gains `114`. It depends on revision `0030` (the field), **not**
  on `0031` (the NOT NULL tightening).

## Review deltas (plan-review round 1, 2026-07-14)

- **Blocker** — uncontained `ConfigurationError` at the per-station call site would abort the
  whole cycle → §5 now mandates a per-station `try/except` + a flow-level containment test.
- **Major** — single-release NOT NULL violated `cicd.md`'s one-version-backward-compatible
  rule → §3 split into `0030` (nullable + backfill) and `0031` (NOT NULL, §3.1).
- **Major** — the `extraction_type`-based backfill would invert the role of
  `meteoswiss_open_data_reanalysis` rows → §3 backfills by source name with a pre-flight
  allowlist audit; §6 also role-fixes that adapter's own matching.
- **Major** — API/dashboard never exposed `role` (and pyright cannot catch it) → new §7.
- **Minors** — wrong `_row_to_weather_source` citation, drifting line-number citations, and the
  "42 across 22 files" count all removed in favour of `file.py::function` references.

## Review deltas (plan-review round 2, 2026-07-14 — ESCALATED, owner-resolved)

Round 2 did **not** converge: the loop stalled with 2 blockers + 2 majors (round 1's fixes were
sound, but round 2's lenses found new ground). Both blockers were independently **verified
against the code** before being actioned. All four are resolved below by owner decision
(2026-07-14):

- **Blocker — the plan broke a currently-passing locked test.** §5 retired the
  `_ICON_NWP_SOURCE` fallback while the Objective claimed Swiss behaviour stayed
  "byte-identical"; `test_falls_back_to_underscore_icon_source_string` clears *all*
  weather-source rows and asserts a forecast is still produced. Both could not be true.
  → **Owner decision: hard-fail.** The fallback is retired, the test is *rewritten* to lock the
  loud contained skip, and the blanket byte-identical claim is retracted in favour of one named,
  justified exception (Objective + §5). Rationale: the fallback guesses a Swiss source string —
  the very guessing this plan exists to kill — and is wrong for Nepal.
- **Blocker — §6 was fixing paths the live forecast never takes.** `services/operational_inputs.py`,
  `services/hindcast.py` (×2) and `services/training_data.py` pass the **raw unfiltered**
  weather-source list into `fetch_reanalysis`, and the concrete `StoreBackedReanalysisSource`
  behind all four does **no** role filtering — so the FORECAST binding's source would be queried
  and merged into past-dynamic features for a Nepal station.
  → **Owner decision: belt-and-braces.** §6 rewritten: a shared `reanalysis_bindings` helper at
  all four call sites (§6a) **plus** a `role is REANALYSIS` guard inside the source
  implementations (§6b), so a future caller cannot reintroduce it.
- **Major — §5 silently added a `status == ACTIVE` filter** that does not exist today (an
  INACTIVE binding *is* currently used for forecasting), which would have started skipping such
  stations on day one.
  → **Owner decision: drop it.** Selection is role-only, exactly as today. The INACTIVE-binding
  issue is a real but separate bug, explicitly deferred to its own plan (§5), with a test added
  to lock the current behaviour so it cannot drift in silently.
- **Major — §7 (API/dashboard role column) flagged as out-of-scope gold-plating** by the
  proportionality lens.
  → **Kept.** It is three small edits and it *is* the operator surface for verifying the
  FORECAST/REANALYSIS bindings this plan introduces — the plan's own stated motivation. Rejecting
  it would leave the new field invisible to the people who must confirm it is right.

## Exit gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

**Doc sync (mandatory — CLAUDE.md "Every code change updates affected docs"):**

- `docs/spec/types-and-protocols.md` — the `StationWeatherSource` block gains `role:
  WeatherSourceRole`; add the `WeatherSourceRole` enum to the enums section.
- `docs/standards/cicd.md` — note the `0030`→`0031` two-release sequence if the rollback
  section needs the pointer.

## References

- Plan 081 `docs/plans/081-recap-dg-client-integration.md` (dispatch design)
- Plan 082 `docs/plans/082-recap-gateway-operational-readiness.md` (Task 2C)
- Plan 106 §4 (v1 critical-path roadmap — Wave 1 forcing spine)
- `docs/standards/cicd.md` § Rollback (migration backwards-compatibility rule)
