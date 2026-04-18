# Plan 055 — Test hygiene (silent skips, private-attr drift, missing route tests)

**Status**: DRAFT
**Date**: 2026-04-18
**Depends on**: none — all changes are test-only and do not alter production
code. If Plan 052 T1 (GridExtractor NaN fail-fast) lands first, the fake-store
fixtures here stay compatible.
**Scope**: Close the test-suite hygiene gaps surfaced by the 2026-04-18 audit.
Three clusters: (1) a runtime-skipped test family (BAFU reference dataset) that
silently passes on every CI run; (2) ~15 call-sites across unit and integration
tests that reach into `_private` attributes without the documented escape
hatch; (3) missing test files for `services/forecast_combination.py` and the
6 HTML route modules. Excludes trivial-module coverage chasing (`exceptions.py`,
`types/ids.py`, etc.) — coverage for coverage's sake is a CLAUDE.md
anti-pattern.

---

## Context

### Why now

`uv run pytest` reports 1170 tests, 0 collection errors — green. That number
hides three classes of problem the audit surfaced:

1. **Silent skips masquerading as passes.** Three tests in
   `tests/unit/adapters/test_reference_dataset.py` guard their bodies with
   `if not PARQUET_PATH.exists(): pytest.skip(...)`. The fixture file
   `tests/fixtures/reference/bafu_observations.parquet` is absent from the
   repo, so these tests have never actually run on CI or locally. A change
   that breaks the reference-dataset adapter will not trip CI.
2. **Private-attribute drift in tests.** Roughly 15 test sites reach into
   `fake._records`, `fake._stations`, `fake._weather_sources`,
   `fake._observations`, `fake._alerts`, or — more concerning — into
   `adapter._build_sparql_query` and `adapter._parse_bindings` on the
   production `HydroScraper` class. The project's escape hatch is the
   `# type: ignore[attr-defined]` comment; none of these sites use it.
   The production-class cases (hydro_scraper, onboard_script) go beyond
   stylistic: they test the implementation, not the behaviour.
3. **Missing tests for user-facing surfaces.** `services/forecast_combination.py`
   drives BMA / consensus / pooled combination — a correctness-critical path
   with no dedicated unit test. The six HTML route modules under `api/routes/`
   (`forecasts.py`, `dashboard.py`, `models.py`, `stations.py`, `tables.py`,
   `health.py`) render the operational dashboard and have zero test coverage.

### Non-goals

- Chasing 100% coverage (CLAUDE.md Testing Philosophy: "A brittle 100% is worse
  than 85% meaningful coverage").
- Adding tests for trivial type modules (`exceptions.py`, `types/ids.py`,
  `types/skill.py`) unless the audit flagged a behavioural gap — they did not.
- Refactoring `HydroScraper` to expose `_build_sparql_query` as public API
  (that is an adapter-design decision, separate scope).

### Principle

Tests describe contracts. A skipped test is not a contract; it is absent
documentation. A test that touches private attributes is a contract with the
implementation, not the behaviour — and will break on refactor regardless of
whether the behaviour changed.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Generate a committed reference dataset** for `test_reference_dataset.py`. Record a small fixture (~a few MB) from the real BAFU LINDAS SPARQL endpoint into `tests/fixtures/reference/bafu_observations.parquet`. Commit it. | Runtime skip with no failure signal is the worst outcome. A committed fixture is deterministic, CI-friendly, and small enough to live in the repo. Alternative — `@pytest.mark.xfail(reason="...")` — preserves the bad outcome (still no signal). Alternative — delete the tests — loses the contract. |
| D2 | **Expose read-only accessors on fakes** (`fake.records()`, `fake.stations()`, etc.) and migrate tests to use them. Where a test needs to mutate fake state mid-test, add explicit mutator methods (`fake.add_weather_source(s)`) rather than poking the internal list. | Matches the CLAUDE.md convention of testing through public APIs. Fakes are test infrastructure — their "public" API is what tests use — so the fix is adding methods to the fakes, not papering over the access with `# type: ignore`. |
| D3 | **For production-class private accesses (hydro_scraper, onboard_script)**: convert the tests to behavioural tests that drive the public entry point (`adapter.fetch(...)`, `main(...)`) with a recorded response fixture. Remove the direct `_build_sparql_query` / `_parse_bindings` / `_build_parser` assertions. | The current tests assert on implementation details. If the adapter refactors internally (which Plan 021 and others did repeatedly), these tests break with no corresponding behavioural change. Behaviour-level tests with recorded responses survive refactors. |
| D4 | **Write `test_forecast_combination.py` now**; defer route-module tests to a follow-up plan (stub created in T6). | `forecast_combination.py` has direct correctness consequences (wrong alert levels). Route tests are important but bulk-testing 6 templates is a scope-expansion that deserves its own plan with decisions about HTML assertion style (structural vs snapshot). |
| D5 | **No coverage threshold enforcement**. Do not add a CI gate that fails on coverage regression. | CLAUDE.md explicitly warns against coverage chasing. This plan closes specific gaps; policy enforcement is a separate conversation. |

---

## Task list

### T1 — Record the BAFU reference dataset fixture

**Files**: `tests/fixtures/reference/bafu_observations.parquet` (new, committed);
`scripts/record_bafu_reference.py` (new, recording tool).

1. Write a small recording tool at `scripts/record_bafu_reference.py` that:
   - Connects to the BAFU LINDAS SPARQL endpoint via the production
     `HydroScraper` adapter.
   - Fetches ~24 hours of observations for a small fixed set of stations
     (choose 3–5 station IDs that are stable and publicly documented).
   - Writes the result to `tests/fixtures/reference/bafu_observations.parquet`
     as a Polars DataFrame.
   - Prints the commit-ready file size (target: < 5 MB).
2. Run the tool once against the live endpoint. Commit the resulting Parquet
   file alongside the tool.
3. Verify: `uv run pytest tests/unit/adapters/test_reference_dataset.py -v`
   — all three tests must now RUN (not skip). All three must pass against the
   committed fixture.
4. Document in `tests/fixtures/reference/README.md` (create if absent):
   recording procedure, stations included, date range, refresh cadence
   (annual or on BAFU schema change).

**Exit**: three previously-silent tests now execute; any schema change in
the BAFU adapter response trips CI.

### T2 — Fake-store public accessors (services fakes)

**Files**: `tests/fakes/*.py` (identify via `Grep "class Fake" tests/`);
test files listed below.

1. In each fake implementing a store, add read-only accessor methods that
   return copies or iterators — not the underlying mutable collection:
   - `FakeReanalysisSource.records() -> list[ReanalysisRecord]`
   - `FakeStationStore.stations() -> dict[StationId, Station]`
     (return copy, not `self._stations`)
   - `FakeStationStore.weather_sources_for(station_id) -> list[...]`
   - `FakeStationStore.add_weather_source(station_id, source)` (mutator —
     replaces direct `._weather_sources.append`)
   - `FakeObservationStore.observations() -> dict[...]`
   - `FakeAlertStore.alerts() -> list[Alert]`
2. Migrate call sites off private attributes:
   - `tests/unit/services/test_operational_inputs.py:103, 159, 162`
   - `tests/unit/services/test_observation_alert_checker.py:65, 163`
   - `tests/unit/services/test_hindcast.py:73, 711, 713, 1046, 1047, 1122, 1123`
   - `tests/unit/flows/test_run_hindcast.py:82`
3. Run `uv run pytest tests/unit/services/ tests/unit/flows/ -q` — must stay
   green.
4. Verify no remaining `fake\._[a-z]` access in the migrated files:
   ```
   Grep "source\._records\|_stations\[\|_weather_sources\|_observations\|_alerts\."
        tests/unit/services tests/unit/flows
   ```
   Expected: zero matches.

**Exit**: fakes have documented public APIs; tests do not reach into fake
internals.

### T3 — Refactor `test_hydro_scraper.py` to behavioural tests

**File**: `tests/integration/adapters/test_hydro_scraper.py`

1. Capture a minimal fixture response from the BAFU SPARQL endpoint (via the
   recording tool from T1 or a dedicated one-off script). Commit as
   `tests/fixtures/bafu_sparql_response.json` (~a few KB).
2. Replace the 5 `adapter._build_sparql_query` / `adapter._parse_bindings`
   direct tests (lines 222, 235, 261, 286, 368) with:
   - `test_fetch_returns_expected_records_from_fixture_response` — stub the
     HTTP client to return the fixture JSON; assert the adapter emits the
     expected `ObservationRecord` list.
   - `test_fetch_propagates_http_error` — stub the HTTP client to raise;
     assert the adapter raises `AdapterError` with a useful message.
   - `test_fetch_handles_empty_bindings` — stub an empty response; assert
     the adapter returns an empty list (or whatever the documented contract
     is).
3. Remove the 5 implementation-detail tests.
4. Verify `uv run pytest tests/integration/adapters/test_hydro_scraper.py -q`
   stays green.

**Exit**: `HydroScraper` tests describe behaviour. Future refactors of the
internal query-builder / binding-parser do not touch these tests.

### T4 — Refactor `test_onboard_script.py:19` off private

**File**: `tests/unit/scripts/test_onboard_script.py`

1. Line 19 uses `mod._build_parser` to assert CLI argument parsing.
2. Replace with a subprocess-style invocation that calls the script's `main()`
   or `sys.argv`-driven entry point with representative argument sets:
   - `test_onboard_script_parses_typical_args` — call `main(["--config",
     "path"])`; assert it dispatches to the correct handler.
   - `test_onboard_script_fails_on_missing_required_arg` — call `main([])`;
     assert `SystemExit` with a non-zero code.
3. Remove the `_build_parser` direct access.
4. Verify the file's other tests stay green.

**Exit**: onboard-script test file uses only the public entry point.

### T5 — Unit test `services/forecast_combination.py`

**File**: `tests/unit/services/test_forecast_combination.py` (new)

1. Identify the public functions in `services/forecast_combination.py` —
   expect roughly `combine_primary`, `combine_pooled`, `combine_bma`,
   `combine_consensus` or a dispatch via `ModelCombinationStrategy`.
2. For each strategy, write:
   - `test_<strategy>_happy_path` — 3 member forecasts, known expected
     combined output.
   - `test_<strategy>_with_single_member` — degenerate case.
   - `test_<strategy>_with_empty_input_raises` — boundary.
3. For BMA specifically: test that weights sum to 1.0 and a known weight
   vector produces the documented weighted output.
4. Do NOT test via `run_forecast_cycle` — these are unit tests for the
   combination module in isolation.
5. Target: 8–12 focused tests. Do not exceed 15 (CLAUDE.md anti-pattern:
   "100s of trivial tests").

**Exit**: `services/forecast_combination.py` has direct unit test coverage
for each strategy.

### T6 — Route-module test stub (follow-up plan marker)

**File**: `docs/plans/056-api-route-tests.md` (new, stub)

1. Create a `Status: DRAFT (stub)` plan following the Plan 048 pattern.
2. Scope sketch:
   - Test the 6 HTML route modules (`forecasts.py`, `dashboard.py`, `models.py`,
     `stations.py`, `tables.py`, `health.py`).
   - Open question: assertion style — structural (BeautifulSoup-walk specific
     `data-*` attrs) vs snapshot (Syrupy / approvaltests).
   - Open question: test client setup — direct `TestClient` vs
     `pytest-fastapi` idiom already used elsewhere.
3. This stub means route-test work has a home and the absence of tests is
   tracked explicitly, not silently.

**Exit**: follow-up plan filed; route tests are a known deferred item.

---

## Dependency graph

```json
{
  "stream-1-fixtures": {
    "tasks": ["T1"],
    "sequential": true,
    "depends_on": []
  },
  "stream-2-fakes": {
    "tasks": ["T2"],
    "sequential": true,
    "depends_on": []
  },
  "stream-3-adapters": {
    "tasks": ["T3", "T4"],
    "parallel": "both in parallel — independent files",
    "depends_on": []
  },
  "stream-4-forecast-combination": {
    "tasks": ["T5"],
    "sequential": true,
    "depends_on": []
  },
  "stream-5-followup": {
    "tasks": ["T6"],
    "sequential": true,
    "depends_on": []
  }
}
```

All five streams are independent and can run in parallel (subject to subagent
concurrency limits).

---

## Files to create

| Path | Task | Purpose |
|---|---|---|
| `tests/fixtures/reference/bafu_observations.parquet` | T1 | Committed reference dataset for BAFU adapter tests (un-skips 3 silently-skipped tests) |
| `tests/fixtures/reference/README.md` | T1 | Recording procedure, stations, refresh cadence |
| `scripts/record_bafu_reference.py` | T1 | Fixture recording tool |
| `tests/fixtures/bafu_sparql_response.json` | T3 | Minimal SPARQL response fixture for behavioural tests |
| `tests/unit/services/test_forecast_combination.py` | T5 | Unit tests for BMA / consensus / pooled / primary |
| `docs/plans/056-api-route-tests.md` | T6 | Stub for route-module test work |

## Files to modify

| Path | Task | Change |
|---|---|---|
| `tests/fakes/*.py` | T2 | Add public accessor and mutator methods |
| `tests/unit/services/test_operational_inputs.py` | T2 | Migrate 3 private-attr sites |
| `tests/unit/services/test_observation_alert_checker.py` | T2 | Migrate 2 private-attr sites |
| `tests/unit/services/test_hindcast.py` | T2 | Migrate 7 private-attr sites |
| `tests/unit/flows/test_run_hindcast.py` | T2 | Migrate 1 private-attr site |
| `tests/integration/adapters/test_hydro_scraper.py` | T3 | Replace 5 implementation-detail tests with behavioural tests |
| `tests/unit/scripts/test_onboard_script.py` | T4 | Replace `_build_parser` test with `main()` invocation tests |

---

## Exit gates

1. `uv run pytest tests/unit/adapters/test_reference_dataset.py -v` shows all
   three tests RUN (not skip) and pass.
2. `Grep "source\._records\|_stations\[\|_weather_sources\|_observations\|_alerts\."
    tests/unit/services tests/unit/flows` returns zero matches.
3. `Grep "adapter\._build_sparql_query\|adapter\._parse_bindings\|mod\._build_parser"
    tests/` returns zero matches.
4. `uv run pytest tests/unit/services/test_forecast_combination.py -v` passes
   with 8–12 tests.
5. Full `uv run pytest` stays green; total test count increases by roughly
   +8 (T5) − 5 (T3 replaces) + 3 (T1 unskips) + 2 (T4 replaces) ≈ +8.
6. `docs/plans/056-*.md` exists as DRAFT stub.
7. Version bump applied per CLAUDE.md.

---

## Risks

| Risk | Mitigation |
|---|---|
| BAFU LINDAS endpoint unreachable at recording time (T1) | Recording tool is a one-shot; if the endpoint is down, retry later. The plan is not time-critical. Fallback: synthesise a minimal fixture from the existing unit-test expectations. |
| Committed Parquet fixture goes stale when BAFU changes schema | Annual refresh cadence documented in `README.md`. Schema changes trip the tests — which is the point — and prompt a refresh. |
| T2 fake-accessor migration creates churn in many test files | Migrations are mechanical (find/replace within a single test file). Each is small; do them one file at a time and commit per file if desired. |
| T3 behavioural test misses a bug that `_build_sparql_query` would have caught | Recorded fixture covers the documented SPARQL output shape. A query-builder bug that still produces valid SPARQL is out of scope for adapter tests — that is a contract for the BAFU endpoint, not the adapter. |
| T5 test choices lock in an implementation (e.g., weight normalisation) | Tests should assert on the documented contract (weights sum to 1.0, combined output in expected range), not on the specific numeric pipeline. Review BMA test expectations with the ML expert before committing. |
| Route-module stub (T6) sits unowned for a long time | Acceptable for this plan. The stub's existence is itself the mitigation — it surfaces the gap. |

---

## Open questions

Not blocking DRAFT → READY:

1. T1 station selection: which 3–5 BAFU stations are most stable for a
   committed reference dataset? (Recommendation: any of the large Swiss
   lowland gauges — discuss with hydrologist.)
2. T5 BMA weight fixture: use the production seed/weights or a synthetic
   known-value vector? (Recommendation: synthetic — production weights can
   change; tests should not.)
3. T6: should the route-test plan also cover the JSON API routes (`api_alerts`,
   `api_forecasts`, `api_stations`) that already have tests, to document
   consistency? (Recommendation: scope Plan 056 to HTML only.)
