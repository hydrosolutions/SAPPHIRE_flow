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

Review history: grill-me (2026-07-13, 7 decisions) → plan-review loop rounds 1-2 (escalated;
owner-resolved) → **independent Codex review, round 3** (NOT-READY: 1 blocker + 3 majors, all
folded). See the three § Review deltas sections.

**One open item blocks READY** — the pre-flight audit in §3
(`SELECT DISTINCT nwp_source, extraction_type FROM station_weather_sources;` against staging
**and** production). It is not a formality: it settles whether Flow 6's reanalysis ingest is
currently a silent no-op, and the migration's allowlist depends on the answer.

Then: owner READY → WF2.

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

**Correction (independent review, 2026-07-14).** An earlier revision of this section
claimed the scheme was "already leaking today" because
`adapters/meteoswiss_open_data_reanalysis.py::fetch_reanalysis` selects bindings on
`extraction_type == BASIN_AVERAGE`. **That claim was FALSE and is retracted.** Verified
at `adapters/meteoswiss_open_data_reanalysis.py:155-162`, the match is
`c.nwp_source == self.NWP_SOURCE` **and** `c.status == ACTIVE` **and**
`c.extraction_type == BASIN_AVERAGE` — i.e. it matches on the **source name first**, and
the `extraction_type` check is a genuine *emission-shape* guard (the adapter only emits
basin-average rows), exactly as §6 says. It is **not** a role proxy, and the two
subsystems do **not** read the field with opposite meanings.

Do not "fix" that adapter by deleting its `extraction_type` check — the check is doing
real work. The backfill still keys off the source **name** rather than `extraction_type`
(§3), which remains the right rule for independent reasons; only the false justification
is removed.

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
backfilled `WHEN extraction_type = 'point' THEN 'reanalysis' ELSE 'forecast'`. That rule is
rejected, but **not** for the reason the first revision gave (see the retraction in
Provenance). The real reason is simpler and stronger:

**`FORECAST` is a closed set, and `extraction_type` is not a role.** `services/onboarding.py`
is the **sole writer** of `station_weather_sources` rows — verified exhaustively: no script,
no Alembic data migration, no API route, and no bootstrap importer writes one
(`api/routes/stations.py:266` reflects the table for a **read-only** select). Onboarding emits
exactly two shapes:

| written by onboarding | `nwp_source` | `extraction_type` | role |
|---|---|---|---|
| forcing binding (`onboarding.py:365-370`) | `forcing[0].source` (e.g. `camels-ch`) | `POINT` | REANALYSIS |
| ICON binding, non-weather stations only (`onboarding.py:379-386`) | hard-coded `"icon_ch2_eps"` | `BASIN_AVERAGE` | FORECAST |

The only forecast binding anyone writes is the hard-coded `icon_ch2_eps` literal (the same
string as `run_forecast_cycle.py::_ICON_NWP_SOURCE`). Every other binding is a
forcing/reanalysis binding whose name comes from the data. So the name is the role; the
`extraction_type` is incidental (and would become actively wrong for Nepal, where the
reanalysis binding is `BASIN_AVERAGE` too).

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

Expected set, per the writer inventory above: `icon_ch2_eps`/`BASIN_AVERAGE` and
`camels-ch`/`POINT` (more precisely, whatever `forcing[0].source` values that deployment
onboarded). The migration carries the same guard in code — it raises if any `nwp_source`
falls outside the allowlist, rather than guessing a role for an unknown source. An unknown
name is a human decision, not a `CASE` fallthrough.

> **⚠️ Open question the audit must settle — do NOT skip it.** A previous revision of this
> plan listed `meteoswiss_open_data_reanalysis`/`BASIN_AVERAGE` in the expected set. But per
> the writer inventory above, **nothing in this repo ever writes such a row**: onboarding
> writes the forcing binding as `POINT` under the *data's* source name. Yet Flow 6 selects
> reanalysis bindings **by that name** (`_reanalysis_sources(store, adapter.NWP_SOURCE)`), and
> the adapter returns `[]` before downloading anything when no config matches
> (`meteoswiss_open_data_reanalysis.py:155-163`). If no such row exists in the live DB, then
> **the scheduled `ingest-weather-history` deployment is currently ingesting nothing** — a
> pre-existing production bug that Plan 114 does not cause but does expose. The audit query
> settles it either way. If the rows are absent, file that as its own bug **before** 114 ships,
> because 114's role filter would otherwise be blamed for a dark feed it did not create.

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

**The shim must carry the migration's allowlist, not an open `else` (independent review).**
The migration's guard is one-time, but the rollback window lets the *old* image keep writing —
and the old writer accepts an **arbitrary** `nwp_source` string with no role
(`store/station_store.py:233-250`). With an unrestricted `else`, a NULL row under an unknown
source name would be silently classified REANALYSIS, bypassing the allowlist and directly
contradicting this plan's own "an unknown name is a human decision" rule. So the shim applies
the **same allowlist**: `icon_ch2_eps` → FORECAST; a known reanalysis/forcing name → REANALYSIS;
**anything else raises** `ConfigurationError` rather than guessing. Revision `0031` re-runs the
allowlist guard over any remaining NULL rows *before* its final backfill, for the same reason.

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

### 6. Role-based selection at EVERY consumer of `fetch_weather_sources`

Two successive review rounds each found a *different* consumer of the raw weather-source list
that the previous revision had missed (round 2: the reanalysis services; the independent review:
the forecast fan-out). That is a pattern, not bad luck — patching consumers one at a time as
reviewers find them does not converge. So this section is **exhaustive by construction**: the
table below is the complete `grep -rn "fetch_weather_sources" src/` result, and **every** row is
accounted for. Any future consumer must be added here.

**Two symmetric helpers** (one definition each of "the forecast bindings" / "the reanalysis
bindings", used everywhere the question is asked):

```python
def forecast_bindings(sources) -> list[StationWeatherSource]:    # role is FORECAST
def reanalysis_bindings(sources) -> list[StationWeatherSource]:  # role is REANALYSIS
```

**6a. Every consumer, and the role it needs:**

| # | Consumer | Needs | Status |
|---|---|---|---|
| 1 | `flows/run_forecast_cycle.py:1243-1247` — Phase A `all_weather_sources` → `flat_weather_configs` → `_fetch_nwp_task(station_configs=…)` → the `WeatherForecastSource` | **FORECAST** | **NEW — the independent review's blocker** |
| 2 | `flows/run_forecast_cycle.py` — Phase A `configs_for_source` (grid extraction) | **FORECAST** | **NEW** — filter by role **and** matching `nwp_source` |
| 3 | `flows/run_forecast_cycle.py::_select_nwp_source` — Phase B per-station loop | **FORECAST** | §5 |
| 4 | `flows/run_forecast_cycle.py::_select_nwp_source` — Phase B group loop | **FORECAST** | §5 |
| 5 | `services/operational_inputs.py:327` — **live** per-station past-dynamic assembly | REANALYSIS | §6b |
| 6 | `services/hindcast.py:287` — per-station hindcast | REANALYSIS | §6b |
| 7 | `services/hindcast.py:455` — group hindcast | REANALYSIS | §6b |
| 8 | `services/training_data.py:181` | REANALYSIS | §6b |
| 9 | `flows/ingest_weather_history.py:250::_reanalysis_sources` (Flow 6) | REANALYSIS | §6b |
| 10 | `api/routes/api_stations.py:181` — station-detail display | *none* — shows **all** bindings, with their role (§7) | ✓ |

**6b. The reanalysis call sites (#5-#9)** hand the **raw, unfiltered** list straight into
`fetch_reanalysis(station_configs=…)`. Wrap each in `reanalysis_bindings(...)`.

**6c. The forecast fan-out (#1, #2) — the independent review's blocker.** Phase A flattens
**every** binding of **every** operational station into `flat_weather_configs` and passes it to
the forecast adapter. For a correctly-onboarded Swiss station that list contains **both**
`camels-ch` (REANALYSIS) and `icon_ch2_eps` (FORECAST). Wrap it in `forecast_bindings(...)`, and
filter `configs_for_source` by role **and** `nwp_source` before grid extraction.

Why this has gone unnoticed: the **production** ICON adapter ignores `station_configs` entirely
(`adapters/meteoswiss_nwp.py:587`, `# noqa: ARG002`) — it downloads the whole grid. But:
- `adapters/replay/nwp.py:40-42` (`ReplayNwpAdapter`, an explicit v0 test adapter) derives the
  source from `station_configs[0].nwp_source` and **raises `AdapterError` on a mixed list** — so
  it is order-dependent or outright broken for a two-binding station.
- **Decisively:** Plan 081's `RecapGatewayAdapter` is a *per-station* `WeatherForecastSource`
  that returns `dict[StationId, WeatherForecastResult]` — it **will** read `station_configs`.
  Handed the raw list, it receives the ERA5-Land REANALYSIS binding and tries to fetch a
  *forecast* for it. This hole sits **directly on the 081/082 path this plan exists to unblock**,
  which is what makes it a blocker rather than a tidy-up.

**6d. Guard inside the source implementations too (belt-and-braces).** Call-site filtering alone
lets a future caller reintroduce the bug by passing the raw list. Every implementation that
iterates `station_configs` also enforces the role it expects:

- `adapters/store_backed_reanalysis.py:31` — the concrete source behind call sites #5-#8 (via
  `hybrid_reanalysis_factories.py::select_reanalysis_source`). Does **no** role/name filtering
  today: it calls `fetch_forcing(source=cfg.nwp_source)` for **every** config handed to it. Add
  the `role is REANALYSIS` skip.
- `adapters/per_source_store_reader.py:45-52` — **worse, and missed by the previous revision**:
  it *discards* `cfg.nwp_source` altogether, reducing configs to unique `station_id`s and
  reading against a **fixed** source tag. So a FORECAST-only config list still yields
  "reanalysis" rows for that station — it fabricates reads for stations that have no reanalysis
  binding at all. Must filter to REANALYSIS before the `dict.fromkeys` reduction.
- `adapters/hybrid_reanalysis.py:61` — `HybridForcingSource` fans the raw `station_configs` into
  its child sources; hybrid mode is a real runtime option (`config/deployment.py:111-113`).
  Filter to REANALYSIS before the fan-out.
- `adapters/meteoswiss_open_data_reanalysis.py:155-162` — add `c.role is REANALYSIS` to its
  existing match. Its `nwp_source == NWP_SOURCE`, `status == ACTIVE` and
  `extraction_type == BASIN_AVERAGE` checks all **stay** (the last is an emission-shape guard —
  see the Provenance retraction).
- `adapters/replay/nwp.py:40-42` — its same-source homogeneity check **stays**; post-114 it only
  ever sees FORECAST bindings, so the check becomes a real invariant instead of a tripwire.

**Why this is the heart of the plan.** For a Nepal station carrying an IFS/FORECAST and an
ERA5-Land/REANALYSIS binding (both `BASIN_AVERAGE`), the unguarded paths (a) merge whatever rows
exist under the *forecast* source name into the "past dynamic" reanalysis features used for live
forecasting, hindcast and training, and (b) hand the *reanalysis* binding to the forecast
adapter. Both are the silent-wrong-source class of bug this plan exists to eliminate. Switzerland
is spared today only by accident — no forcing rows are stored under `icon_ch2_eps`, and the
production forecast adapter ignores its config list. Nepal would not be spared, and Plan 082
would inherit both.

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
  list** (locks §6d independently of the call-site helper).
- **The forecast fan-out (§6c):** a forecast cycle over a correctly-onboarded station carrying
  **both** a `camels-ch`/REANALYSIS and an `icon_ch2_eps`/FORECAST binding passes **only** the
  FORECAST binding to the `WeatherForecastSource`. Run it against `ReplayNwpAdapter`, which
  raises `AdapterError` on a mixed `station_configs` list — so the test **fails** against an
  implementation that forwards the raw `flat_weather_configs`. That adapter is the natural
  positive control here; use it rather than a bespoke fake.
- **Grid extraction** receives only configs whose role is FORECAST *and* whose `nwp_source`
  matches the selected source.
- **The hybrid stack (§6d):** `PerSourceStoreReader` and `HybridForcingSource`, handed a raw
  list containing a FORECAST binding (and, separately, a FORECAST-**only** list), produce **no**
  reanalysis rows for that station. `PerSourceStoreReader` needs its own case because it
  discards `nwp_source` and keys on `station_id` — a call-site-only fix would leave it green
  while still broken. Note `tests/unit/adapters/test_per_source_store_reader.py:190-199`
  currently locks the mixed-config behaviour and will need updating.
- **The NULL-role shim (§3):** a NULL role under an **unknown** source name raises
  `ConfigurationError` rather than defaulting to REANALYSIS.
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

## Review deltas (independent Codex review, 2026-07-14 — round 3)

Run after the plan-review loop's escalation was owner-resolved, precisely because the loop had
converged on its own output. Verdict **NOT-READY**; 1 blocker + 3 majors, **every one verified
against the code before folding**. All are now resolved in the doc.

- **BLOCKER — the plan had fixed the reanalysis direction and left the forecast direction open.**
  Phase A flattens every binding into `flat_weather_configs` (`run_forecast_cycle.py:1243-1247`)
  and hands the raw list to the `WeatherForecastSource` (`:1291`); `ReplayNwpAdapter` raises on a
  mixed list (`replay/nwp.py:40-42`), and Plan 081's per-station `RecapGatewayAdapter` *will* read
  it. → §6 rewritten around a **complete, grep-derived consumer table** with symmetric
  `forecast_bindings` / `reanalysis_bindings` helpers. Two rounds each found a different missed
  consumer; enumerating them all is the only fix that converges.
- **MAJOR — the "belt-and-braces" guard missed the hybrid stack.** `PerSourceStoreReader`
  (`per_source_store_reader.py:45-52`) *discards* `nwp_source` and keys on `station_id` against a
  fixed source tag, so it fabricates reanalysis reads for stations with no reanalysis binding;
  `HybridForcingSource` fans the raw list to its children. → both added to §6d, with tests.
- **MAJOR — the NULL-role shim had an open `else`.** Rollback lets the *old* image write an
  arbitrary `nwp_source` with no role, which the shim would have silently called REANALYSIS,
  bypassing the migration allowlist. → §3: the shim carries the **same allowlist** and raises on
  an unknown name.
- **MAJOR — doc sync was incomplete.** `docs/spec/database-schema.md` and
  `docs/architecture-context.md` both define `station_weather_sources` without `role`. → added to
  Exit gates, along with a pre-existing `active`→`status` staleness in the same blocks.
- **Also folded (found in parallel, not by Codex): the Provenance section was factually wrong** —
  it claimed the MeteoSwiss reanalysis adapter reads role out of `extraction_type`, contradicting
  §6d of the same plan. Retracted; the adapter matches on **source name** first. And the
  migration's expected-set listed a `meteoswiss_open_data_reanalysis` binding that **no writer in
  the repo creates** — which, if the live DB confirms it, means Flow 6's reanalysis ingest is
  currently a **silent no-op in production**. The pre-flight audit must settle this before 114
  ships (§3).

**Codex positively verified** (not just "found no problem"): the four reanalysis call sites; the
current `_select_nwp_source` heuristic and its uncontained per-station call site; the group loop's
existing try/except containment; Alembic head `0029`; and that `PgStationStore.store_weather_source`
is the only production writer of `station_weather_sources`.

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
- `docs/spec/database-schema.md:88-92` **and** `:542-546` — the authoritative
  `station_weather_sources` definitions; both omit `role`. *(Missed by the earlier revision —
  found in independent review.)* **While here, fix the pre-existing staleness in these blocks:
  they still show `active: BOOL`, but the column has been `status` since Alembic `0009`
  (`db/metadata.py:179-185`).**
- `docs/architecture-context.md:1718-1723` — same `station_weather_sources` block, same two
  fixes (`role` added, stale `active` → `status`).
- `docs/conventions.md:396` — the enum-value table lists
  `station_weather_sources.extraction_type` / `SpatialRepresentation`; add a
  `station_weather_sources.role` / `WeatherSourceRole` row (`forecast`, `reanalysis`).
- `docs/standards/cicd.md` — note the `0030`→`0031` two-release sequence if the rollback
  section needs the pointer.
- `docs/touchpoint-maps.md` — the operational-inputs / time-series-preprocessing map should name
  the role filter, since `assemble_station_operational_inputs` is a listed touchpoint.

## References

- Plan 081 `docs/plans/081-recap-dg-client-integration.md` (dispatch design)
- Plan 082 `docs/plans/082-recap-gateway-operational-readiness.md` (Task 2C)
- Plan 106 §4 (v1 critical-path roadmap — Wave 1 forcing spine)
- `docs/standards/cicd.md` § Rollback (migration backwards-compatibility rule)
