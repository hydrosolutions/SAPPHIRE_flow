# Plan 055 — Test hygiene (synthetic reference fixture, private-attr drift, missing route tests)

**Status**: DONE (archived 2026-04-18 — tags v0.1.316 → v0.1.330, 1167/1167 tests green)
**Date**: 2026-04-18
**Revision**: 8 — sixth-round review (2026-04-18) caught T4 stub-list gap, fixture rename confusion, and a Files-to-modify row contradicting T4 prose.

**Rev 8 fixes (2026-04-18)**:
(a) **T4 `resolve_artifact_dir` added to stub list**: `scripts/onboard.py:259` calls `resolve_artifact_dir()` which reads a filesystem-path env var. In a bare test environment this may raise before reaching `onboard_from_camelsch`. Added to the happy-path stub set.
(b) **T4 existing-fixture clarification**: the current test file has a fixture named `_build_parser` (not `mod`) that already uses `importlib` to load the script — but it returns `mod._build_parser`, not `mod`. The subagent must rewrite the fixture to return the module object (keep the `importlib` loader, change the return value and rename to `mod` or similar). Rev-7 wording "Keep the `mod` / `_SCRIPT_PATH` / `importlib` scaffolding" wrongly implied a ready-to-use `mod` fixture existed.
(c) **T4 Files-to-modify row fixed**: row said "Remove the two `_build_parser`-based tests (and the importlib scaffolding)" — the parenthetical contradicted T4 prose, which says keep the scaffolding. Parenthetical removed.

**Rev 7 fixes (2026-04-18)**:
(a) **T4 `--dry-run` short-circuit**: rev 6's happy-path tests called `main(["--dry-run"])` and asserted the `onboard_from_camelsch` stub was called. But `scripts/onboard.py:194-201` returns `0` in the dry-run branch *before* any DB or workhorse call — the stub is never invoked. T4 is now three tests covering the three real branches: env-guard failure, dry-run success, happy-path with full stubs (no `--dry-run`).
(b) **T4 importability**: `scripts/` has no `__init__.py` and is not on `sys.path`, so `from scripts.onboard import main` does not resolve. T4 now retains the existing `importlib.util.spec_from_file_location` scaffolding from the current test file and calls `mod.main(argv)` directly.
(c) **T4 stub coverage widened**: rev 6 listed 4 stubs but `main()` also calls `_load_qc_rules()` (reads config files) between env-guard and `onboard_from_camelsch`. Stub list updated.
(d) **T4 data_dir default corrected**: when `--data-dir` is omitted, `main()` resolves `config_data_dir / "raw" / "CAMELS_CH"` — the default is NOT `None` as rev 6 claimed.
(e) **T2 line-159 wording fixed**: rev 6 said "replace with the appropriate protocol read method" for `station_store._stations[station_id] = station_cfg`, but that line is a **write**. Correct replacement: `station_store.store_station(station_cfg)`.
(f) **T2 `store_weather_source` dedup note**: this method deduplicates on `(station_id, nwp_source)` before appending — benign for single-source test setup, but the plan now notes the semantic difference from a pure `.append`.
(g) **T3 Files-to-modify row**: rev 6 step 1 named both fixtures but the table row only listed the river fixture. Added the lake fixture.
(h) **Monkeypatch target caveat**: T4 step 1 now instructs the subagent to verify `import sqlalchemy as sa` before writing patch targets — if the import style ever changes to `from sqlalchemy import create_engine`, the target becomes `scripts.onboard.create_engine` instead.
(i) **Stream-3 label renamed** `stream-3-adapters` → `stream-3-private-access`: T4 is a CLI script, not an adapter.
(j) **Rev-5 note (f) marked superseded by rev 6 — see note (a) above**: rev 5 said "T4: 1 removed, 2 added"; rev 6/7 correctly says "2 removed, 3 added".

**Rev 6 fixes (2026-04-18)**:
(a) **T4 rewritten**: rev 5's plan was unexecutable — `scripts/onboard.py` has no `--config` flag, and `main()` takes no arguments (it reads `sys.argv`). The rewrite gives `main()` an `argv: list[str] | None = None` parameter (standard Python CLI idiom; argparse's own docs recommend this), keeping `_build_parser` private. Tests drive `main(argv)` with the three DB side-effects stubbed (`DATABASE_URL`, `sa.create_engine`, `_run_migrations`, `onboard_from_camelsch`). 2 existing tests removed, 3 new tests added → net +1.
(b) **T2 types corrected**: class is `FakeWeatherReanalysisSource` (not `FakeReanalysisSource`); element type is `RawHistoricalForcing` (not the nonexistent `ReanalysisRecord`); `FakeStationStore.stations()` returns `dict[StationId, StationConfig]` (not `dict[StationId, Station]`).
(c) **T2 redundant methods dropped**: the `StationStore` protocol already defines `fetch_weather_sources(station_id)` and `store_weather_source(source)`. Remove the proposed `weather_sources_for` and `add_weather_source` accessors — migrate call sites to the existing protocol methods.
(d) **T3 lake-fixture disambiguation**: rev 5 said "reuse `tests/fixtures/lindas_sample_response.json`" but the lake test needs `tests/fixtures/lindas_lake_sample_response.json` (already in the repo). Step 1 now names both fixtures explicitly.
(e) **T3 stub-response validity risk added**: HTTP-request-capturing stubs must also return a well-formed SPARQL JSON envelope. An empty/invalid response causes `_parse_bindings` to raise, the adapter swallows it, and the URL/SPARQL assertion passes vacuously on an empty result. Captured in Risks table.
(f) **Plan 046 coordination warning removed**: A2.5 has landed (commit `4f42244`); `test_run_forecast_cycle.py` is clean.
(g) **T3 step 3 line-number clarification**: lines 222/235/261/286/368 are private-access lines *within* each test, not the def lines.
(h) **"Depends on" updated**: T4 now alters one line of production code (`main(argv)` parameter). Plan is no longer strictly test-only.

**Rev 5 fixes (2026-04-18)**:
(a) **Scope wording aligned with Context** (line 25): removed leftover "without the documented escape hatch" phrasing — rev 4 removed the fabrication from Context but left this stale reference in Scope.
(b) **D3 rationale qualified** (line 96): "warning log + empty list" was the single-station special case; for multi-station calls the list is partial (the invalid station is absent). D3 now reads "empty/partial list".
(c) **Test name corrected**: `test_fetch_propagates_http_error` contradicted its own Note (the error is caught, not propagated). Renamed to `test_fetch_logs_warning_and_returns_partial_on_http_error`.
(d) **T3 assertion (b) prose** reorganised: the `station_id` parenthetical was mid-sentence about `error`, confusing two different log fields. Split into a clean assertion plus a separate clarifying note.
(e) **T3 "Honest caveat" logic corrected**: rev 4 claimed "refactor removes the regex guard → test fails at assertion (b)". Wrong — if the guard is gone entirely, no `observation.fetch_failed` event is emitted and assertion (a) fails first. Caveat now covers both refactor shapes (guard removed vs guard altered).
(f) **Exit gate 4 math** *(superseded by rev 6 note (a))*: rev 4 said T4 was "2 removed, 2 added — net 0". Rev 5 corrected to "1 removed, 2 added — net +1". Rev 6 rewrote T4 entirely and confirmed the correct count is **2 removed, 3 added — net +1**; total delta ≈ +3.
(g) **Risks table commit wording**: "commit per file if desired" conflicted with exit gate 7's mandatory per-commit version bump. Tightened to mandate per-file commits.

**Rev 4 fixes (2026-04-18)** — six corrections after a second critical review (Opus-orchestrated Sonnet agents, all claims cross-verified against the codebase):
(a) **T3 preserves structural invariants**: removing the 5 implementation-detail tests at lines 222/235/261/286/368 as rev 3 proposed would have dropped two load-bearing guards — the lake-URI-template contract (old `test_lake_station_uri_and_params:286`) and the `since`-not-in-SPARQL invariant (old `test_since_not_in_sparql_query:368`). The latter is the **regression guard for T1's own "LINDAS is real-time only" premise** — if a later refactor embeds `since` in the SPARQL body, the synthetic-fixture rationale collapses and no remaining test catches it. T3 now adds two HTTP-request-capturing public-API tests to preserve both invariants without calling private methods.
(b) **T3 log-context fixed**: `hydro_scraper.py:106-111` logs `station_id=str(station_id)` (UUID) and `error=str(exc)` — the invalid site_code appears inside the error-message string, not as a structured field. Replacement-test assertions updated to match on the `error` substring (`"Invalid site_code"`) rather than the fabricated "invalid code in log context".
(c) **T3 log-capture idiom**: use `structlog.testing.capture_logs()` exclusively (per `docs/standards/logging.md` §Testing and universal practice in the existing suite — zero uses of `caplog`). The rev-3 "or `caplog`" alternative was incorrect.
(d) **T3 except-clause rationale corrected**: `hydro_scraper.py:106-111` catches `(httpx.HTTPError, ValueError, KeyError)` — not just `ValueError`.
(e) **Context escape-hatch fabrication removed**: the rev-3 claim that `# type: ignore[attr-defined]` is "the project's escape hatch" is not supported by any canonical doc. CLAUDE.md §Testing Philosophy prescribes public accessors (D2's approach).
(f) **T2 ordering contract made explicit**: new accessors return `list(self._foo.values())` — insertion order preserved. Several existing tests depend on dict iteration order (`test_observation_alert_checker.py:65` takes the last inserted id); a sorted accessor would silently change semantics.

**Rev 3 fixes (2026-04-18)**:
(a) **T1 reframed**: re-recording the BAFU fixture today is infeasible because LINDAS is real-time only — `hydro_scraper.py:173-192` binds a *single* current-reading subject URI with no time filter, so the adapter cannot retrieve historical windows. T1 becomes a README clarification (explain WHY the fixture is synthetic) plus a follow-up archive-collection plan stub (`docs/plans/058-bafu-lindas-archive-collection.md`, DRAFT). The synthetic fixture stays in place by design until ≥6 months of real readings accumulate (per `docs/v0-scope.md` §E1).
(b) **T5 dropped**: `tests/unit/services/test_forecast_combination.py` already exists (456 lines) and covers POOLED / BMA / PRIMARY strategies — happy path, single-model, empty input, BMA weight math, PRIMARY no-op, error cases. CONSENSUS is intentionally absent (not implemented in v0b per `docs/v0-scope.md` §A8e). No genuine gap. T6 renumbered to T5.
(c) **T3 rewritten**: preserves SPARQL-injection coverage by driving an invalid `station_config.code` through `adapter.fetch_observations(...)` and asserting the observable behaviour (warning log + empty list) rather than `ValueError` propagation — per `hydro_scraper.py:106-111` the exception is caught internally. T3 reuses the existing `tests/fixtures/lindas_sample_response.json` fixture instead of creating a new one.
(d) **D4 rationale corrected**: `forecast_combination.py` is already tested; the defer-routes decision now stands on its own (open design questions about HTML assertion style).
**Depends on**: none. T1, T2, T3, T5 are test-only or doc-only. T4 alters one line of production code — adds an `argv: list[str] | None = None` parameter to `scripts/onboard.py:main()` (argparse uses `sys.argv` when `argv` is `None`, so existing invocations are unaffected). If Plan 052 T1 (GridExtractor NaN fail-fast) lands first, the fake-store fixtures here stay compatible.
**Scope**: Close the test-suite hygiene gaps surfaced by the 2026-04-18 audit.
Three clusters: (1) a synthetic Tier 2 reference fixture deliberately used until
a real BAFU archive can be built (Plan 058 stub) — the problem addressed here is
**documentation clarity**, not fixture replacement; (2) ~25 call-sites across
unit and integration tests that reach into `_private` attributes directly;
CLAUDE.md §Testing Philosophy prescribes adding a public accessor instead;
(3) missing test files for the 6 HTML route modules.
Excludes trivial-module coverage chasing (`exceptions.py`, `types/ids.py`,
etc.) — coverage for coverage's sake is a CLAUDE.md anti-pattern.

---

## Context

### Why now

`uv run pytest` reports ~1160 tests, 0 collection errors — green. That number
hides three classes of problem the audit surfaced:

1. **Synthetic reference fixture without clear rationale.** Three tests in
   `tests/unit/adapters/test_reference_dataset.py`
   (`test_reference_parquet_loads_via_replay_adapter`,
   `test_reference_parquet_schema`, `test_reference_parquet_size_bound`) load
   `tests/fixtures/reference/bafu_observations.parquet` and assert on schema,
   dtype, and size. The file exists and the tests pass — but `README.md:114–118`
   labels the Parquet a **synthetic placeholder** generated by
   `generate_fixtures.py`. The README frames this as a "known limitation" to
   fix by re-recording — but re-recording is infeasible: BAFU LINDAS is
   real-time only. `hydro_scraper.py:173-192` binds the adapter to a single
   current-reading subject URI, ignoring any time range. Building a real
   fixture requires collecting readings over time (≥6 months per
   `docs/v0-scope.md` §E1), which is a separate scheduled-pipeline problem.
   The fix here is **documentation**: explain WHY the fixture is synthetic by
   design, and file a follow-up plan stub for the archive-collection pipeline.
2. **Private-attribute drift in tests.** Roughly 25 test sites reach into
   `fake._records`, `fake._stations`, `fake._weather_sources`,
   `fake._observations`, `fake._alerts`, or — more concerning — into
   `adapter._build_sparql_query` and `adapter._parse_bindings` on the
   production `HydroScraper` class. CLAUDE.md §Testing Philosophy
   prescribes adding a public accessor (`.spec()` / similar) rather than
   inspecting private state — D2 applies that convention to fakes. The
   production-class cases (hydro_scraper, onboard_script) go beyond
   stylistic: they test the implementation, not the behaviour.
3. **Missing tests for user-facing surfaces.** The six HTML route modules
   under `api/routes/` (`forecasts.py`, `dashboard.py`, `models.py`,
   `stations.py`, `tables.py`, `health.py`) render the operational dashboard
   and have zero test coverage. (`services/forecast_combination.py` was
   previously listed here as a gap; the follow-up review confirmed 456 lines
   of coverage already exist — no action needed.)

### Non-goals

- Chasing 100% coverage (CLAUDE.md Testing Philosophy: "A brittle 100% is worse
  than 85% meaningful coverage").
- Adding tests for trivial type modules (`exceptions.py`, `types/ids.py`,
  `types/skill.py`) unless the audit flagged a behavioural gap — they did not.
- Refactoring `HydroScraper` to expose `_build_sparql_query` as public API
  (that is an adapter-design decision, separate scope).
- Designing the BAFU LINDAS archive-collection pipeline — that is Plan 058
  (stub filed here, full design deferred).

### Principle

Tests describe contracts. A skipped test is not a contract; it is absent
documentation. A test that touches private attributes is a contract with the
implementation, not the behaviour — and will break on refactor regardless of
whether the behaviour changed.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Document the synthetic-fixture rationale honestly; file an archive-collection plan stub.** The current README frames the synthetic Parquet as a "known limitation" with "replace by recording" as the remedy. That remedy is not available: LINDAS is real-time only (`hydro_scraper.py:173-192` binds a single current-reading subject URI, ignoring time ranges). The fix is to rewrite the note as a by-design stand-in, cite the LINDAS constraint and the `docs/v0-scope.md` §E1 6-month gate, and file Plan 058 as the follow-up owner of the archive-collection pipeline. The synthetic fixture stays in place; `generate_fixtures.py` continues to drive Plan 043's e2e test. | Re-recording is infeasible with the current adapter and endpoint. Honest documentation is cheap and prevents future readers from chasing an impossible fix. Plan 058 as a DRAFT stub gives the real work a home. |
| D2 | **Expose read-only accessors on fakes** (`fake.records()`, `fake.stations()`, etc.) and migrate tests to use them. Where a test needs to mutate fake state mid-test, add explicit mutator methods (`fake.add_weather_source(s)`) rather than poking the internal list. | Matches the CLAUDE.md convention of testing through public APIs. Fakes are test infrastructure — their "public" API is what tests use — so the fix is adding methods to the fakes, not papering over the access with `# type: ignore`. |
| D3 | **For production-class private accesses (hydro_scraper, onboard_script)**: convert the tests to behavioural tests that drive the public entry point (`adapter.fetch_observations(...)`, `main(...)`) with a recorded response fixture. Remove the direct `_build_sparql_query` / `_parse_bindings` / `_build_parser` assertions. Preserve SPARQL-injection coverage by driving an invalid station code through the public API and asserting the observable behaviour — warning log + empty/partial result list (empty for a single-station call; invalid station absent for a multi-station call, per `hydro_scraper.py:106-111`). | The current tests assert on implementation details. If the adapter refactors internally (which Plan 021 and others did repeatedly), these tests break with no corresponding behavioural change. Behaviour-level tests with recorded responses survive refactors, and the injection coverage is retained. |
| D4 | **Defer route-module tests to a follow-up plan** (stub created in T5). | Route tests raise open design questions (HTML assertion style — structural vs snapshot — and test-client idiom) that deserve their own plan. `forecast_combination.py` was previously listed as in-scope for this plan; the follow-up review confirmed it is already tested (`tests/unit/services/test_forecast_combination.py`, 456 lines) so no action is needed there. |
| D5 | **No coverage threshold enforcement**. Do not add a CI gate that fails on coverage regression. | CLAUDE.md explicitly warns against coverage chasing. This plan closes specific gaps; policy enforcement is a separate conversation. |

---

## Task list

### T1 — Clarify synthetic-fixture rationale + file LINDAS archive-collection plan stub

**Files modified**: `tests/fixtures/reference/README.md` (rewrite the
placeholder note to explain WHY the fixture is synthetic, citing the LINDAS
real-time-only constraint and the `docs/v0-scope.md` §E1 6-month gate).
**Files created**: `docs/plans/058-bafu-lindas-archive-collection.md`
(DRAFT stub, Plan 048-style pattern).

Context: the original T1 (re-record from LINDAS) is infeasible because
`hydro_scraper.py:173-192` binds a *single* current-reading subject URI — no
time filter, no historical retrieval is possible through the adapter. A real
fixture requires building an archive over time. Until a scheduled collection
pipeline accumulates ≥6 months of real readings, the synthetic fixture is a
deliberate stand-in, not a bug.

1. **Edit `tests/fixtures/reference/README.md`**:
   - Keep the "Synthetic placeholder" item but rewrite it from a "known
     limitation" framing to a "by-design" framing. The rewritten note states:
     (a) BAFU LINDAS is real-time only — the adapter fetches the current
     reading, not a time window;
     (b) until a scheduled collection pipeline (see
     `docs/plans/058-bafu-lindas-archive-collection.md`) builds an operational
     archive of ≥6 months of real readings, the fixture stays synthetic on
     purpose;
     (c) when the archive is ready, re-record per `docs/v0-scope.md` §E1.
   - Adjust the "Files" section's one-line description of the Parquet to
     point at the new explanation rather than "recorded from live BAFU LINDAS".
   - Adjust the "Recording BAFU observations" and "Refreshing the dataset"
     sections so they read as *future* (post-Plan 058) instructions, not
     immediately actionable ones.

2. **Create `docs/plans/058-bafu-lindas-archive-collection.md` as a DRAFT
   stub** (Plan 048-style pattern — short, scope sketch, open questions,
   no task list yet). Minimum contents:
   - Status: DRAFT (stub), Date: 2026-04-18.
   - Scope sketch: a scheduled Prefect task records the current LINDAS reading
     for each station in `tests/fixtures/reference/stations.toml` at a safe
     cadence (e.g. hourly); the appended observations land in an append-only
     Parquet layout under `tests/fixtures/reference/archive/` (or a separate
     data repo — see open questions); when ≥6 months accumulate, the archive
     is promoted to `bafu_observations.parquet` per v0-scope.md §E1.
   - Back-pressure / dedup strategy: what happens if the endpoint is down,
     or emits the same timestamp twice.
   - CI impact: do we commit incremental archive rows to the repo, or rebuild
     on demand from an artifact store?
   - Open questions (numbered, non-blocking): collection cadence vs training
     granularity; storage location (in-repo vs separate data repo / artifact
     store); retention policy before promotion; how to detect LINDAS schema
     drift during accumulation.
   - No task list yet — this is a stub.

**Exit**: the README clearly explains the synthetic fixture is a deliberate
stand-in pending Plan 058; `docs/plans/058-bafu-lindas-archive-collection.md`
exists as a DRAFT stub with status, date, scope, and open questions; no
Parquet is overwritten.

**Plan-ID note**: 058 is the next free slot after 057 (used by T5). 047 is
reserved by Plan 054 D4 for Nepal v1 data sources; 056 is the archived
zarr-python 3 migration.

### T2 — Fake-store public accessors (services fakes)

**Files**: `tests/fakes/*.py` (identify via `Grep "class Fake" tests/`);
test files listed below.

1. In each fake implementing a store, add read-only accessor methods that
   return copies — not the underlying mutable collection. Use the
   established idiom `list(self._foo.values())` for dict-backed stores and
   `list(self._records)` for list-backed ones (pattern already in
   `FakeModelStore.fetch_all_models`, `FakeBasinStore.fetch_all_basins`,
   `FakeParameterStore.fetch_all`).

   New accessors to add:
   - `FakeWeatherReanalysisSource.records() -> list[RawHistoricalForcing]`
     (replaces external `source._records` access in
     hindcast/operational-inputs tests). Note: the class is
     `FakeWeatherReanalysisSource` and `self._records` is
     `list[RawHistoricalForcing]`; there is no `ReanalysisRecord` type.
   - `FakeWeatherReanalysisSource.set_records(records)` /
     `.extend_records(records)` mutators — replace direct
     `source._records = records` / `.extend(...)` at
     `test_hindcast.py:73, 713, 1047, 1123` and
     `test_operational_inputs.py:103`.
   - `FakeStationStore.stations() -> dict[StationId, StationConfig]`
     (return copy — `self._stations` holds `StationConfig`, not
     `Station`).
   - `FakeObservationStore.observations() -> list[Observation]` (used by
     8 sites in `test_ingest_observations.py` plus
     `test_observation_alert_checker.py:65`).
   - `FakeAlertStore.alerts() -> list[Alert]`.
   - `FakeWeatherForecastStore.record_count() -> int` — all 4 sites in
     `test_run_forecast_cycle.py` use
     `len(nwp_store._records) [> 0 | == 0]`; exposing just the count is
     simpler than the full list.

   **No new accessors needed for these cases** — the `StationStore`
   protocol already exposes them; migrate call sites to use the existing
   public methods:
   - `test_operational_inputs.py:159` (`station_store._stations[station_id] = station_cfg`)
     is a **write** (assignment). Replace with
     `station_store.store_station(station_cfg)` — the existing protocol
     method on `StationStore` (also implemented by `FakeStationStore`).
     For test-setup reads elsewhere, use `stations()` (accessor above) or
     the appropriate protocol read method.
   - `test_operational_inputs.py:162` (`station_store._weather_sources.append(...)`)
     → use the existing protocol method `store_weather_source(source)`
     (the `StationWeatherSource` already carries the `station_id` field;
     no separate `station_id` argument is required).
     **Semantic note**: `FakeStationStore.store_weather_source`
     deduplicates on `(station_id, nwp_source)` before appending. For
     single-source test setup (the case at :162) this is benign, but if
     a test ever adds two entries with the same `(station_id, nwp_source)`
     key, only the second will persist — check call sites after
     migration.
   - Any site that needs to read weather sources for a station → use the
     existing protocol method `fetch_weather_sources(station_id)`.

   **Ordering contract (mandatory)**: accessors that wrap a dict
   (`_observations`, `_alerts`, `_stations`) return
   `list(self._foo.values())` — insertion order preserved. Several
   existing test assertions depend on dict iteration order
   (`test_observation_alert_checker.py:65` takes the last inserted
   observation id), and a sorted or set-based accessor would silently
   change semantics.

2. Migrate call sites off private attributes. The revised list (25 sites
   across 6 files — up from 13 across 4, after a second grep pass):
   - `tests/unit/services/test_operational_inputs.py:103, 159, 162`
   - `tests/unit/services/test_observation_alert_checker.py:65, 163`
   - `tests/unit/services/test_hindcast.py:73, 711, 713, 1046, 1047, 1122, 1123`
   - `tests/unit/flows/test_run_hindcast.py:82`
   - `tests/unit/flows/test_ingest_observations.py:97, 167, 194, 223, 328, 383, 423, 466`
     — all 8 use `obs_store._observations.values()`; replace with
     `obs_store.observations()` iterator or `.all_observations()` list,
     whichever the fake's new public API lands on.
   - `tests/unit/flows/test_run_forecast_cycle.py:788, 944, 1061, 1182`
     — all 4 are `len(nwp_store._records) [> 0 | == 0]` assertions.
     Simplest public API: `nwp_store.record_count() -> int`. Alternative:
     `nwp_store.records()` list with a `len()` at the call site. Either is fine;
     pick one and apply consistently.
3. Run `uv run pytest tests/unit/services/ tests/unit/flows/ -q` — must stay
   green.
4. Verify no remaining private-attr access in the migrated files:
   ```
   Grep "source\._records|_stations\[|_weather_sources|_observations|_alerts\.|nwp_store\._records"
        tests/unit/services tests/unit/flows
   ```
   Expected: zero matches. (Note: `tests/fakes/*.py` and `tests/unit/api/conftest.py`
   legitimately define `self._foo` inside fake class bodies — those are
   intra-class access, not the target. The grep above only covers the
   consumer-test directories.)

**Exit**: fakes have documented public APIs; tests do not reach into fake
internals across the six listed files.

### T3 — Refactor `test_hydro_scraper.py` to behavioural tests (all structural invariants preserved via HTTP-request capture)

**File**: `tests/integration/adapters/test_hydro_scraper.py`

**HTTP-stub requirement**: replacement tests use a stub transport (e.g.
`httpx.MockTransport` or the recording-adapter pattern already used
elsewhere in the suite — check for precedent before picking an idiom) that
**captures the outgoing request** so the test can assert on the emitted
SPARQL query body and URL path without touching `_build_sparql_query`.
Returning the fixture JSON is not enough: two of the removed tests assert
on the *request shape*, not the response — those assertions must be
preserved through request capture, not dropped.

1. **Reuse both existing fixtures** (no new fixture files are created):
   - `tests/fixtures/lindas_sample_response.json` — **river** response,
     already used by `test_contract_lindas_response` at
     `test_hydro_scraper.py:224-248`. Use for river-station tests.
   - `tests/fixtures/lindas_lake_sample_response.json` — **lake** response,
     already present in the repo. Use for lake-station tests (the lake
     contract / records / URI-path tests).
   Verify the exact paths when editing.

   **Stub-response validity**: the HTTP stub must return a well-formed
   SPARQL JSON envelope even for the request-capture-only tests. An
   empty or malformed body causes `_parse_bindings` to raise; the
   adapter's except-clause at `hydro_scraper.py:106-111` swallows the
   error and returns an empty list; the URL/SPARQL assertion then
   passes **vacuously** on the empty result while actual behaviour is
   broken. Either (a) return the matching river/lake fixture JSON from
   the stub, or (b) return a minimal well-formed empty-bindings
   envelope (`{"results": {"bindings": []}}`) and additionally assert
   that the capture hook recorded exactly one outgoing request (so
   "no request made" and "empty-bindings branch" don't both pass).
2. Replace the 5 private-access tests (private-access lines — *not* the
   `def` lines — at `222, 235, 261, 286, 368`) with **seven** behavioural
   tests driven through `adapter.fetch_observations(...)`:
   - `test_fetch_returns_expected_records_from_fixture_response` — stub the
     HTTP client to return the river fixture JSON; assert
     `fetch_observations(...)` emits the expected `RawObservation` list
     (replaces old `test_contract_lindas_response:235`).
   - `test_fetch_returns_expected_lake_records_from_fixture_response` — same
     pattern for a lake station; assert parameter == `"water_level"` and
     the expected value (replaces old `test_contract_lindas_lake_response:261`).
   - `test_fetch_logs_warning_and_returns_partial_on_http_error` — stub
     the HTTP client to raise `httpx.HTTPError`. `hydro_scraper.py:106-111`
     catches the error internally and logs `observation.fetch_failed`
     (it does **not** propagate). Assert: (i) a single captured event
     `observation.fetch_failed` at `warning` level; (ii) the failing
     station's records are absent from the returned `list[RawObservation]`;
     (iii) other stations' records (if any) are still present.
   - `test_fetch_handles_empty_bindings` — stub an empty bindings response;
     assert `fetch_observations(...)` returns an empty list for that station.
   - **`test_fetch_lake_station_uses_lake_uri_path`** — stub transport
     captures the outgoing request; drive a lake station through
     `fetch_observations(...)`; assert the captured SPARQL body / URL
     contains `lake/observation/<code>` and NOT `river/observation/`.
     Replaces the structural assertion inside
     `test_lake_station_uri_and_params:286`; guards the river-vs-lake URI
     template contract through the public API.
   - **`test_fetch_does_not_embed_since_in_sparql_query`** — stub transport
     captures the outgoing request; drive `fetch_observations(...)` with a
     non-None `since` dict; assert the captured SPARQL body contains none
     of the time-filter tokens (`xsd:dateTime`, `FILTER (?measurementTime`,
     ISO date literals). Replaces old `test_since_not_in_sparql_query:368`
     — **critical**: this is the regression guard for T1's "LINDAS is
     real-time only" premise. If a later refactor embeds `since` in the
     SPARQL, the synthetic-fixture rationale collapses and this is the
     only test that catches it.
   - `test_fetch_rejects_invalid_station_code` — **SPARQL-injection
     regression replacement**. The old
     `test_invalid_station_code_raises_valueerror:214-222` called
     `_build_sparql_query` directly with `"'; DROP TABLE"`. The public-API
     replacement drives an invalid `station_config.code` through
     `adapter.fetch_observations(...)`. Per `hydro_scraper.py:106-111`, the
     adapter catches `(httpx.HTTPError, ValueError, KeyError)` inside
     `fetch_observations` and emits an `observation.fetch_failed` warning,
     returning whatever partial results exist (empty list if this is the
     only station). Using `structlog.testing.capture_logs()` (**not**
     `caplog` — it is not used anywhere in the existing suite, per
     `docs/standards/logging.md` §Testing), assert:
     (a) exactly one captured event has `event == "observation.fetch_failed"`
         and `log_level == "warning"`;
     (b) the structured `error` field contains the substring
         `"Invalid site_code"` — this pins the regex guard specifically.
         (Do **not** assert on `station_id` for this purpose; that field
         carries the UUID, not the raw site_code. The `error` field is
         the one that surfaces the regex-guard message.)
     (c) `fetch_observations(...)` returns an empty list (or, for a
         multi-station call, the list does not contain entries for the
         invalid station).
     — **not** that a `ValueError` propagates (it is caught internally).

   **Honest caveat on SPARQL-injection coverage**: the contract shifts
   from "regex guard raises `ValueError`" to "regex guard raises
   `ValueError` AND the handler logs it with `'Invalid site_code'` in
   the message". Two refactor shapes to keep in mind:
   - If a future refactor **removes the regex guard entirely**,
     `_build_sparql_query` no longer raises for the injection string.
     No `observation.fetch_failed` event is emitted and assertion (a)
     fails first — the test still catches the regression.
   - If a future refactor **keeps the guard but changes its error
     message**, assertion (a) passes and assertion (b) fails on the
     missing substring.
   Either way the contract is enforced. Do not simplify assertion (b)
   to "any event was logged" — that would pass for unrelated failures
   (e.g. transient `httpx.HTTPError`) and miss the injection regression.

3. Remove the 5 private-access tests at lines 222, 235, 261, 286, 368.
4. Verify `uv run pytest tests/integration/adapters/test_hydro_scraper.py -q`
   stays green.

**Exit**: `HydroScraper` tests describe behaviour. Future refactors of the
internal query-builder / binding-parser do not touch the test bodies. The
two HTTP-request-capturing tests preserve the two structural invariants
that actually matter operationally (river-vs-lake URI template; no time
filter in SPARQL — the regression guard underpinning T1).

### T4 — Drive `test_onboard_script.py` through `main(argv)` instead of `_build_parser`

**Files**: `scripts/onboard.py` (production — 1-line change);
`tests/unit/scripts/test_onboard_script.py` (test rewrite).

**Design rationale**: rev 5's design was unexecutable (`main()` took no
arguments; `--config` was not a real flag). The argv-parameter pattern is
the standard Python CLI idiom — argparse's own docs recommend it — and
lets tests drive `main()` end-to-end through its public surface.
`_build_parser` stays private in production.

**Import strategy**: `scripts/` has no `__init__.py` and is not on
`sys.path` — `from scripts.onboard import main` will not resolve. The
current test file already uses `importlib.util.spec_from_file_location`
with `_SCRIPT_PATH` to load the script; however, the existing fixture
(named `_build_parser`) returns `mod._build_parser` (the private
function), not the module. **Rewrite the fixture** to return the `mod`
module object — keep the `importlib` loader and `_SCRIPT_PATH` constant
unchanged, change only the return value and rename the fixture to `mod`
(or similar). Tests then call `mod.main(argv)` and use
`monkeypatch.setattr(mod, ...)` / `monkeypatch.setattr(mod.sa, ...)`.
Do **not** remove the `importlib` scaffolding.

1. **Production change** — in `scripts/onboard.py`, add an `argv`
   parameter to `main()`:
   ```python
   def main(argv: list[str] | None = None) -> int:
       parser = _build_parser()
       args = parser.parse_args(argv)   # argparse reads sys.argv when argv is None
       ...
   ```
   The existing `if __name__ == "__main__": sys.exit(main())` invocation
   continues to work — `None` falls back to `sys.argv` exactly as before.

   Before writing monkeypatch targets, **verify the import style at the
   top of `scripts/onboard.py`**. Current: `import sqlalchemy as sa` — so
   the patch target for the engine factory is `mod.sa.create_engine`
   (equivalent to the string path `scripts.onboard.sa.create_engine`).
   If the style ever changes to `from sqlalchemy import create_engine`,
   the target becomes `mod.create_engine` instead.

2. **Stubbing strategy**. The real `main()` does substantial work between
   `parse_args()` and `onboard_from_camelsch`. The stub set depends on
   which branch each test exercises:

   - **Env-guard branch** (`DATABASE_URL` missing → `return 1`): no stubs
     needed — the guard fires before anything else. Just ensure
     `DATABASE_URL` is **unset** for the test (`monkeypatch.delenv(...,
     raising=False)`).
   - **Dry-run branch** (`--dry-run` → prints summary, `return 0`): only
     `DATABASE_URL` needs to be set. The branch returns before any DB or
     workhorse call.
   - **Happy path** (no `--dry-run`): full stubbing. Patch on the loaded
     `mod` object so the `importlib`-loaded module sees the stubs:
     ```python
     monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
     monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
     monkeypatch.setattr(mod, "_run_migrations", MagicMock())
     monkeypatch.setattr(mod, "_load_qc_rules", MagicMock(return_value=[]))
     monkeypatch.setattr(mod, "resolve_artifact_dir",
                          MagicMock(return_value=Path("/tmp")))
     onboard_stub = MagicMock()
     monkeypatch.setattr(mod, "onboard_from_camelsch", onboard_stub)
     ```
     `MagicMock` chains automatically: `sa.create_engine(...)` returns a
     `MagicMock`, `engine.connect()` returns a `MagicMock` context
     manager (the `with engine.connect() as conn:` block at
     `scripts/onboard.py:232` resolves through `__enter__`/`__exit__`
     auto-magic), and all the `PgXStore(conn)` / `StoreBackedReanalysisSource`
     constructions receive `MagicMock` args silently.

     `resolve_artifact_dir` (called at `scripts/onboard.py:259`) reads a
     filesystem-path env var and may raise in a bare test environment —
     stub it explicitly. If the subagent discovers an additional
     unconditional call site that doesn't chain through `MagicMock`
     cleanly (e.g., a real file read or OS-resource access), add it to
     the stub list.

3. **Remove** the two existing `_build_parser` tests
   (`test_data_dir_default_is_none`, `test_data_dir_explicit_arg_parsed`).
   **Rewrite** the existing `_build_parser` pytest fixture — keep the
   `_SCRIPT_PATH` constant and the `importlib.util.spec_from_file_location`
   + `exec_module(mod)` loader, but change the return value from
   `mod._build_parser` to `mod` (and rename the fixture accordingly,
   e.g., to `mod`). The new tests need the module object, not the
   private function.

4. **Add** three behavioural tests:

   - `test_main_returns_nonzero_without_database_url` — env-guard
     branch. `monkeypatch.delenv("DATABASE_URL", raising=False)`; call
     `mod.main([])`; assert return value `== 1` and that any
     `onboard_from_camelsch` spy installed is **not** called. (No DB
     stubs needed — the guard exits first.)
   - `test_main_dry_run_returns_zero` — dry-run branch. Set
     `DATABASE_URL`; call `mod.main(["--dry-run"])`; assert return
     value `== 0` and that the `onboard_from_camelsch` spy is **not**
     called.
   - `test_main_happy_path_invokes_camelsch_onboarder` — happy path
     with full stubs per section 2 (no `--dry-run`). Call
     `mod.main(["--data-dir", "/tmp/cam"])`; assert the stubbed
     `onboard_from_camelsch` was called exactly once and its
     `data_dir` argument equals `Path("/tmp/cam")`. **Note**: the
     current `main()` may post-process `data_dir` before passing it
     (e.g. `resolve_data_dir`). If the subagent finds that the real
     call site transforms the argument, assert on the **transformed**
     value, not the raw CLI input — or fall back to asserting only
     that the stub was called exactly once.

5. Verify `uv run pytest tests/unit/scripts/test_onboard_script.py -q`
   stays green.

**Test-count delta**: 2 removed, 3 added → net +1.

**Exit**: no private-attr access in the test file; `_build_parser` stays
private in production; three tests exercise the three real branches of
`main()` (env-guard, dry-run, happy path). Total plan delta (T3 + T4)
stays at ≈ +3.

### T5 — Route-module test stub (follow-up plan marker)

**File**: `docs/plans/057-api-route-tests.md` (new, stub). Plan ID **057** —
not 056. Plan 056 is taken (zarr-python 3 migration, archived 2026-04-18) and
047 is reserved for Plan 054 D4's Nepal v1 stub. 057 is the next free slot.
058 is claimed by T1 for the archive-collection plan.

1. Create a `Status: DRAFT (stub)` plan following the Plan 048 pattern.
2. Scope sketch:
   - Test the 5 HTML route modules (`forecasts.py`, `dashboard.py`,
     `models.py`, `stations.py`, `tables.py`) plus `health.py`.
   - **First step before design**: classify `health.py`. `docs/v0-scope.md`
     §J marks `GET /api/v1/health` as done in Plan 041 (JSON endpoint),
     but the file is named `health.py` rather than `api_health.py` (the
     naming used for tested JSON routes: `api_alerts.py`, `api_forecasts.py`,
     `api_stations.py`). Clarify whether `health.py` is (a) the JSON
     endpoint with a naming inconsistency — in which case this is a Plan
     041 test gap, not an HTML-route item — or (b) a separate HTML page.
     The answer changes whether `health.py` belongs in Plan 057's scope at
     all.
   - Open question: HTML assertion style — structural (BeautifulSoup-walk
     specific `data-*` attrs) vs snapshot (Syrupy / approvaltests).
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
  "stream-3-private-access": {
    "tasks": ["T3", "T4"],
    "parallel": "both in parallel — independent files; shared motivation is removing private-method access from tests",
    "depends_on": []
  },
  "stream-4-followup": {
    "tasks": ["T5"],
    "sequential": true,
    "depends_on": []
  }
}
```

All four streams are independent and can run in parallel (subject to subagent
concurrency limits).

---

## Files to create

| Path | Task | Purpose |
|---|---|---|
| `docs/plans/058-bafu-lindas-archive-collection.md` | T1 | DRAFT stub for the scheduled LINDAS archive-collection pipeline (the real follow-up to the synthetic reference fixture) |
| `docs/plans/057-api-route-tests.md` | T5 | Stub for route-module test work (ID 057 because 056 = zarr migration archived, 047 = reserved by Plan 054 D4) |

## Files to modify

| Path | Task | Change |
|---|---|---|
| `tests/fixtures/reference/README.md` | T1 | Rewrite "Synthetic-placeholder" note to explain WHY (LINDAS real-time only; archive collection deferred to Plan 058). Adjust "Files", "Recording BAFU observations", and "Refreshing the dataset" sections to match the by-design framing. |
| `tests/fakes/fake_stores.py` and `tests/fakes/fake_adapters.py` | T2 | Add public accessor and mutator methods (see T2 step 1) |
| `tests/unit/services/test_operational_inputs.py` | T2 | Migrate 3 private-attr sites |
| `tests/unit/services/test_observation_alert_checker.py` | T2 | Migrate 2 private-attr sites |
| `tests/unit/services/test_hindcast.py` | T2 | Migrate 7 private-attr sites |
| `tests/unit/flows/test_run_hindcast.py` | T2 | Migrate 1 private-attr site |
| `tests/unit/flows/test_ingest_observations.py` | T2 | Migrate 8 private-attr sites (lines 97, 167, 194, 223, 328, 383, 423, 466) |
| `tests/unit/flows/test_run_forecast_cycle.py` | T2 | Migrate 4 private-attr sites (lines 788, 944, 1061, 1182). Plan 046 A2.5 has landed (commit `4f42244`) and this file is not in its dirty tree — no coordination required. |
| `tests/integration/adapters/test_hydro_scraper.py` | T3 | Replace 5 implementation-detail tests with **7** behavioural tests: 2 contract tests (river/lake records from fixture), 2 HTTP-request-capture tests that preserve the lake-URI-template and `since`-not-in-SPARQL invariants, 2 error-path tests (HTTP failure → warning log, empty bindings), and 1 SPARQL-injection regression test. Reuse both existing fixtures — `tests/fixtures/lindas_sample_response.json` (river) for river tests and `tests/fixtures/lindas_lake_sample_response.json` (lake) for lake tests. |
| `scripts/onboard.py` | T4 | Add `argv: list[str] | None = None` parameter to `main()`; pass it to `parser.parse_args(argv)`. Backwards compatible — `None` keeps the existing `sys.argv` behaviour. |
| `tests/unit/scripts/test_onboard_script.py` | T4 | Remove the two `_build_parser`-based tests; rewrite the existing fixture to return the loaded module (`mod`) instead of `mod._build_parser` — keep the `importlib` loader and `_SCRIPT_PATH` unchanged. Add three `main(argv)`-driven tests (env-guard failure, dry-run success, happy path) with DB side-effects stubbed via `monkeypatch`. |

---

## Exit gates

1. `uv run pytest tests/unit/adapters/test_reference_dataset.py -v` shows all
   three tests
   (`test_reference_parquet_loads_via_replay_adapter`,
   `test_reference_parquet_schema`,
   `test_reference_parquet_size_bound`) pass against the unchanged synthetic
   fixture — no regression from T1 (T1 is README + plan stub only).
2. `Grep "source\._records|_stations\[|_weather_sources|_observations|_alerts\.|nwp_store\._records"
    tests/unit/services tests/unit/flows` returns zero matches (covers T2's
    6 migrated files).
3. `Grep "adapter\._build_sparql_query|adapter\._parse_bindings|mod\._build_parser"
    tests/` returns zero matches.
4. Full `uv run pytest` stays green; total test count shifts by roughly
   T3 (5 removed, 7 added — net +2) + T4 (2 removed, 3 added — net +1)
   ≈ +3.
5. `docs/plans/058-bafu-lindas-archive-collection.md` exists as DRAFT stub
   (Status, Date, Scope, Open questions; no task list).
6. `docs/plans/057-api-route-tests.md` exists as DRAFT stub.
7. Per CLAUDE.md §Version Bumping — every commit includes
   `uv run bump-my-version bump patch` + `git tag v$(uv run bump-my-version show current_version)`.
   Multi-commit plans bump and tag PER commit, not once overall.

---

## Risks

| Risk | Mitigation |
|---|---|
| Plan 058 (archive collection) sits unowned for a long time | Acceptable for this plan. The stub exists and is reviewed during the next quarterly plan sweep; the synthetic fixture remains functional and — after T1 — documented as a by-design stand-in, so tests keep passing in the meantime. |
| T2 fake-accessor migration creates churn in many test files | Migrations are mechanical (find/replace within a single test file). Each is small; commit one file at a time, bumping the patch version per commit per CLAUDE.md §Version Bumping (see exit gate 7). |
| T3 behavioural test misses a bug that `_build_sparql_query` would have caught | Two HTTP-request-capturing tests (`test_fetch_lake_station_uses_lake_uri_path`, `test_fetch_does_not_embed_since_in_sparql_query`) guard the river-vs-lake URI template and the no-time-filter invariant without calling private methods. A query-builder bug that still produces valid SPARQL with a correct template and no time filter is out of scope for adapter tests — that is a contract for the BAFU endpoint. SPARQL-injection coverage is preserved through `test_fetch_rejects_invalid_station_code`, which pins on the `"Invalid site_code"` substring inside the captured `error` log field (so the regex guard is verified specifically — see T3 "Honest caveat"). |
| T4 parser-test replacement changes what is actually asserted about CLI | Replacement tests exercise `main(argv)` with DB side-effects stubbed — they assert both argparse behaviour (through the argv list) *and* end-to-end dispatch (via the stubbed workhorse calls). Coverage widens, not shrinks. |
| T3 HTTP-request-capturing stubs pass vacuously on malformed responses | The `_parse_bindings` → except-clause path in `hydro_scraper.py:106-111` swallows parse errors and returns an empty list. A stub that captures the request but returns a malformed body makes URL / SPARQL-body assertions pass against that empty list without exercising the real parse path. Mitigation: capture-only tests must return either the matching river/lake fixture JSON *or* a minimal `{"results": {"bindings": []}}` envelope AND assert that exactly one outgoing request was recorded — see T3 step 1 "Stub-response validity". |
| Route-module stub (T5) sits unowned for a long time | Acceptable for this plan. The stub's existence is itself the mitigation — it surfaces the gap. |

---

## Open questions

Not blocking DRAFT → READY:

1. **Plan 058 cadence** — hourly collection means ~8,760 rows/station/year;
   is that the right rate, or should we sub-sample to match the hourly-
   forecast granularity we need for training/hindcast? (Decide when Plan 058
   exits DRAFT.)
2. **Plan 058 archive storage location** — commit to repo, or move to a
   separate data repo / artifact store? (The in-repo option is simplest but
   bloats history; a separate repo / artifact store needs CI access and a
   refresh policy.)
3. **T5 route-test plan scope** — should the route-test plan (057) also cover
   the JSON API routes (`api_alerts`, `api_forecasts`, `api_stations`) that
   already have tests, to document consistency? (Recommendation: scope Plan
   057 to HTML only.)
