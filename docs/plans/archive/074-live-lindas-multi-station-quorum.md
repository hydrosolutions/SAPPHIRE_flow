# Plan 074 — Harden live-LINDAS schema check with multi-station quorum

**Status**: DONE
**Date**: 2026-05-04
**Depends on**: none
**Blocks**: nothing — quality-of-signal improvement to the weekly workflow.
**Scope**: Replace the single-station live-LINDAS schema check with a multi-station, mixed-kind quorum so a transient empty response from one gauge no longer reds the weekly workflow. Test-only change in `tests/integration/live/test_lindas_live_schema.py`. No adapter changes, no workflow file changes.

---

## Context

### What failed

The `Live LINDAS weekly schema check` workflow run on 2026-05-04 went red:

```
tests/integration/live/test_lindas_live_schema.py::TestLiveLindasSchema::test_fetch_returns_non_empty_raw_observations FAILED
AssertionError: LINDAS returned 0 observations for station 2044 — endpoint may be down or schema has changed
```

CI log captured:

```
observation.http_response  status_code=200  response_bytes=117  url=https://lindas.admin.ch/query
observation.fetch_completed  duration_ms=502.4  record_count=0  station_id=…002044
```

### Diagnosis (verified 2026-05-04)

- Manually probing the same SPARQL query against `https://lindas.admin.ch/query` for station `2044` returned the full expected response: `discharge`, `waterLevel`, `waterTemperature`, `measurementTime`. Endpoint up, schema unchanged.
- A SPARQL query against a deliberately fake station ID (`9999999`) returned exactly 117 bytes — the empty-bindings shape `{"head":{"vars":[…]},"results":{"bindings":[]}}`. So the CI failure was an empty-bindings result for a real station, not a transport or schema error.
- This is consistent with BAFU LINDAS being **real-time only** (per project memory: "BAFU LINDAS real-time only — No historical time series; deployment must ingest over time to build archive"). If station 2044's record was briefly absent from the named graph (sensor offline minute, mid-refresh, etc.), a single-station test fails despite no real schema or endpoint problem.
- Live probe on 2026-05-04 14:10 UTC showed station `2004` behaves as a lake-shaped LINDAS resource: queried as `river/observation/2004` it returned the 117-byte empty-bindings response; queried as `lake/observation/2004` it returned one `water_level` observation. The six other reference stations (`2009`, `2033`, `2044`, `2091`, `2159`, `2085`) returned all three river parameters: `discharge`, `water_level`, `water_temperature`.

### Problem statement

The current test (`tests/integration/live/test_lindas_live_schema.py:80`) queries one station and asserts `len(observations) >= 1`. A single transient empty result from real-time-only LINDAS reds the weekly workflow. The intended signal of the test — *schema drift* or *endpoint outage* — is not what this assertion is actually measuring.

A schema rename or endpoint outage on the river path would empty **every** reference river station simultaneously; an empty single station is noise.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Query a fixed set of well-known active BAFU gauges, split by LINDAS kind.** The six river stations form the quorum; station `2004` is an opportunistic lake-path sample. | A lake response must not be allowed to make a broken river path look green. Conversely, a transient empty lake response from the single lake sample must not red the river schema check. |
| D2 | **Use the 7 station codes already enumerated in `tests/fixtures/reference/stations.toml`** (2004, 2009, 2033, 2044, 2091, 2159, 2085), but hardcode the LINDAS kind per live behavior: `2004 = StationKind.LAKE`; the other six are `StationKind.RIVER`. | The fixture file is the source for the code roster only, not for the live-test LINDAS kind. `tests/fixtures/reference/stations.toml` currently marks `2004` as `river`; this test intentionally overrides that because the 2026-05-04 probe confirmed LINDAS serves `2004` under the lake path (`water_level` only). |
| D3 | **River quorum passes only if ≥1 river station returns ≥1 observation; 2004 is not counted in that quorum.** | The original failure was on the river path. If all six river gauges are empty while the lake sample returns water level, treat that as a real river-path outage/schema signal. |
| D4 | **Assert global river-parameter coverage across returned river observations, not per-station exact coverage.** | Not every site-kind exposes every parameter. The schema signal we need is that, across live river gauges, the adapter still sees `discharge`, `water_level`, and `water_temperature`. |
| D5 | **Schema-shape assertions still run on every observation that does come back**, river and lake. | If station A returns valid `discharge` but station B returns a non-finite value or unknown parameter name, that's still drift; we want to see it. |
| D6 | **Distinguish failure messages**: "all N river stations empty" (river-path drift / outage), "missing expected river parameter(s)" (parameter drift), vs. per-observation shape violations (parser drift). | Triage clarity for the operator who reads the workflow log on Monday morning. |
| D7 | **Keep the test under `-m live_lindas`** with no marker, fixture, or workflow changes. | The weekly workflow already runs `uv run pytest -m live_lindas -v`; the marker contract is stable and shared with the integration-nightly `live` umbrella. |
| D8 | **Sequential queries, not parallel.** 7 stations × ~500 ms = ~3.5 s — well inside the existing 30 s `_LIVE_TIMEOUT_S` per-call budget and the 10 min workflow timeout. | Simpler code, no shared `httpx.AsyncClient` or thread-pool to maintain in a once-a-week test. |
| D9 | **Test-only change. Do not touch `HydroScraperAdapter`.** | Adapter behaviour is correct; production ingest is happy returning `[]` for an empty graph — that's the contract. The brittleness lives in the test, not the adapter. |
| D10 | **No retry-with-backoff inside the test.** | A retry would mask the very signal we want — schema drift or sustained outage. The quorum across independent stations is the resilience mechanism. |

Rejected alternatives:

- **Retry the same single station** — masks signal, slows the test, doesn't address schema drift.
- **Pad with many more stations (50+)** — diminishing returns, longer runtime, more noise from any one station's metadata changes.
- **Allow xfail when all empty** — defeats the purpose of the workflow; the operator must see red when LINDAS is genuinely broken.

---

## Phases

### T1 — Add the multi-station fixture-list helper

File: `tests/integration/live/test_lindas_live_schema.py`.

Replace `_make_station_2044()` with `_make_reference_stations()` returning a `list[StationConfig]` populated from the 7 reference codes. Build via the same helper pattern (frozen `StationConfig`, deterministic UUIDs from the code, `created_at`/`updated_at` set to `2026-01-01T00:00:00Z`, `network="bafu"`, `ownership=StationOwnership.FOREIGN`).

Hardcode the seven `(code, name, lon, lat, station_kind, measured_parameters)` tuples directly in the test file rather than parsing the TOML at runtime — keeps the live test independent of the reference-fixture loader and obvious to read at the failure site.

Important implementation note: the codes come from `tests/fixtures/reference/stations.toml`, but the LINDAS kind does not blindly follow that file. `2004` intentionally uses `StationKind.LAKE` in this live test even though the current TOML entry says `station_kind = "river"`. Do not "correct" the live test back to river to match the fixture; the live endpoint serves `2004` as `lake/observation/2004`.

Roster:

| Code | LINDAS kind | `measured_parameters` (StationConfig) | Expected live adapter parameters |
|---|---|---|---|
| 2004 | `StationKind.LAKE` | `frozenset({"water_level"})` | `{"water_level"}` |
| 2009 | `StationKind.RIVER` | `frozenset({"discharge", "water_level", "water_temperature"})` | `{"discharge", "water_level", "water_temperature"}` |
| 2033 | `StationKind.RIVER` | `frozenset({"discharge", "water_level", "water_temperature"})` | `{"discharge", "water_level", "water_temperature"}` |
| 2044 | `StationKind.RIVER` | `frozenset({"discharge", "water_level", "water_temperature"})` | `{"discharge", "water_level", "water_temperature"}` |
| 2091 | `StationKind.RIVER` | `frozenset({"discharge", "water_level", "water_temperature"})` | `{"discharge", "water_level", "water_temperature"}` |
| 2159 | `StationKind.RIVER` | `frozenset({"discharge", "water_level", "water_temperature"})` | `{"discharge", "water_level", "water_temperature"}` |
| 2085 | `StationKind.RIVER` | `frozenset({"discharge", "water_level", "water_temperature"})` | `{"discharge", "water_level", "water_temperature"}` |

The adapter's SPARQL builder consults `station_kind` only (not `measured_parameters`); the column above mirrors the expected live parameters for clarity, not because the adapter reads it.

### T2 — Rewrite the single test method as a quorum check

Replace the body of `test_fetch_returns_non_empty_raw_observations` with the quorum logic:

1. Single `HydroScraperAdapter.fetch_observations(stations, since)` call covering all 7 stations.
2. Group results by `station_id`; split station IDs into `river_station_ids` and `lake_station_ids`.
3. **River quorum assertion**: `assert river_non_empty_count >= 1, f"All {len(river_stations)} reference BAFU river stations returned 0 observations — LINDAS river endpoint outage or schema drift. Codes: {river_codes}"`.
4. **Global river-parameter coverage assertion**: collect parameters across returned river observations and assert `{"discharge", "water_level", "water_temperature"}.issubset(river_parameters)`. This is intentionally not a per-station exact-parameter assertion; station `2004` is lake-shaped and returns only `water_level`, and future individual gauges may have station-specific gaps.
5. **Opportunistic lake-path assertion**: if station `2004` returns observations, they must be well formed and their parameters must be within `{"water_level"}`. Do not fail solely because the single lake sample is empty; a robust lake quorum needs more lake stations and is deferred.
6. **Per-observation shape assertions** (existing logic, unchanged in substance) on every observation across all stations:
   - timestamp is tz-aware UTC,
   - value is a finite `float`,
   - parameter is in `{"discharge", "water_level", "water_temperature"}`.
7. Rename method to `test_reference_station_quorum_returns_well_formed_observations` to match what it now asserts.

Update the module docstring to reflect the multi-station quorum design and the "all-empty = drift / outage" failure semantics.

### T3 — Local validation

Run the live test locally to confirm green against the actual endpoint:

```bash
uv run pytest -m live_lindas -v
```

Expected: 1 passed, 1287 deselected. Print the per-station record counts via the structlog `observation.fetch_completed` events already emitted by the adapter — eyeball that the quorum test is exercising what it claims to.

If any station returns 0 in the local run, that's expected (real-time-only). Confirm the test still passes when ≥1 river station returns data and the returned river observations collectively include the three expected river parameters. Confirm `2004` is queried with the lake URI path and, when non-empty, returns `water_level`.

### T4 — Commit, bump, tag

1. `uv run ruff format` + `uv run ruff check --fix`.
2. `uv run pytest tests/unit tests/integration -x -q` to confirm no collateral breakage (live tests are deselected by default).
3. `uv run bump-my-version bump patch`.
4. Stage `tests/integration/live/test_lindas_live_schema.py`, `pyproject.toml`, `src/sapphire_flow/__init__.py`. The plan file's `Status: READY` header stays unchanged on this commit; the README entry stays under Active. Both flip on the archive commit (step 8).
5. Commit: `test(plan-074): harden live-LINDAS check with mixed-kind quorum`. Include a one-line note in the body that the weekly workflow needs no change (still `-m live_lindas`).
6. `git tag v$(uv run bump-my-version show current_version)`.
7. Optionally, manually trigger the workflow via `gh workflow run live-lindas-weekly.yml` and watch one run go green to close the loop. (Not required for plan exit; the local run plus deterministic logic is enough.)
8. Archive in a second commit:
   - flip `Status: READY` → `Status: DONE` in the plan header,
   - `git mv docs/plans/074-live-lindas-multi-station-quorum.md docs/plans/archive/074-live-lindas-multi-station-quorum.md`,
   - remove the "074" line from `docs/plans/README.md` Active section (the "Archived" pointer is unchanged — it just says "see archive/"),
   - patch bump again, commit `docs(plan-074): archive completed plan`, tag.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `tests/integration/live/test_lindas_live_schema.py` | T1 + T2 | Replace single-station station factory + single test with mixed-kind 7-station check; count only six river stations in the river quorum; query 2004 as an opportunistic lake-path sample; rename test method; update docstring. |
| `pyproject.toml`, `src/sapphire_flow/__init__.py` | T4 | Patch version bump (×2 — implementation commit + archive commit). |
| `docs/plans/074-live-lindas-multi-station-quorum.md` | T4 | Add (already present from this plan-write step), then archive-move. |
| `docs/plans/README.md` | T4 | Already lists 074 under Active; remove the line on archive commit. |

No adapter changes (`src/sapphire_flow/adapters/hydro_scraper.py` untouched).
No workflow changes (`.github/workflows/live-lindas-weekly.yml` untouched).
No standards or spec doc changes.

---

## Exit gates

1. `uv run pytest -m live_lindas -v` passes locally against the live LINDAS endpoint.
2. `uv run pytest tests/unit tests/integration -x -q` is green (default-CI surface unchanged).
3. Test method asserts river quorum (`river_non_empty_count >= 1`), global river-parameter coverage, and shape (timestamp/value/parameter) — verified by reading the diff.
4. Failure message on simulated all-river-empty case (e.g. by temporarily flipping the six river station codes to fake IDs) is the new "all N river stations returned 0" message, not the old single-station message. (Optional manual sanity check; not required for plan exit.)
5. Commit landed, tag applied, plan archived, README index updated.
6. **Soft check, one week out**: the next scheduled Monday run of `live-lindas-weekly.yml` is green. If it goes red, follow the new failure-message triage path; if it stayed red because all six river stations are empty, treat as a real river-path outage and investigate LINDAS.

---

## Risks

| Risk | Mitigation |
|---|---|
| All six river reference stations *do* go empty simultaneously on a Monday morning (e.g. BAFU maintenance window) — workflow still flakes. | Acceptable: this is the signal the test is supposed to give. Operator response = check LINDAS status page, re-run the workflow. The signal has gone from "single station empty (noise)" to "all six river stations empty (real)" — strictly higher specificity. |
| Station `2004` is the only lake-path sample, so a transient empty `2004` response cannot robustly distinguish lake-path schema drift from lake-station noise. | Do not fail solely on `2004` empty in this plan. When non-empty, it still exercises the lake URI path and water-level parsing. A real lake quorum needs additional lake stations and belongs in a follow-up once the lake roster is widened. |
| One of the 6 river codes is decommissioned by BAFU between now and a future run, so the quorum quietly shrinks. | Low impact (still 5 stations). Caught at next CAMELS-CH refresh / station-roster review. Could mitigate by also asserting `len(grouped) >= 5` to catch silent shrinkage, but that adds brittleness for marginal benefit; **not adopted** unless we see drift. |
| Increased per-run query volume (1 → 7 SPARQL POSTs / week) on the public LINDAS endpoint. | 7 requests per week is well below any conceivable rate limit. Federal Office for the Environment publishes LINDAS as an open SPARQL endpoint without published quotas. |
| Hardcoded station tuples drift out of sync with `tests/fixtures/reference/stations.toml`. | Comment in the test points back to the TOML and Plan 058 §T1 as the code-roster source. The `2004` kind mismatch is intentional and documented in the test. Do not parse the TOML at runtime; that would reintroduce the wrong river path for `2004` unless/until the fixture is reviewed separately. |
| 30 s `_LIVE_TIMEOUT_S` not enough for 7 sequential queries if LINDAS slows down. | 7 × ~500 ms baseline = 3.5 s; even at 5× degradation that's 17.5 s. If we ever see a real timeout failure, raise `_LIVE_TIMEOUT_S` to 60 s — trivial follow-up, no plan needed. |

---

## Deferred to follow-up plans

- **Lake-station quorum**: this plan includes station `2004` as an opportunistic lake-shaped sample but does not fail solely on `2004` being empty. A separate parametrised case can add a true multi-lake quorum once Plan 058 §T1 widens the roster operationally.
- **Per-parameter shape assertion**: today the test checks parameter ∈ {discharge, water_level, water_temperature} but not that, say, discharge is positive. Domain-range checks belong in QC, not in a schema-drift smoke test.
- **Alerting on red workflow runs**: out of scope; CI ↔ alerting wiring belongs to Plan 046 / Flow 4 monitoring, not to this test.

## Implications for Plan 058 (LINDAS roster widening)

Live probing on 2026-05-04 confirmed that LINDAS's `lake/observation/<code>` URI path is not a hydrological-type designation but a "water-level-only resource" designation. Station `2004` ("Bern, Schönau (Aare)") is a hydrological river gauge that LINDAS only addresses under the `lake/` path; the `river/` path returns empty bindings. Other gauges in the BAFU roster of ~170 stations are likely to share this quirk.

Plan 058 §T1 onboards stations using `station_kind` to drive `_build_sparql_query`. If §T1 derives `station_kind` purely from a hydrological catalogue (BAFU, CAMELS-CH, etc.) without probing both URI paths, it will silently produce empty observations for every river-typed gauge that LINDAS exposes only as a `lake/` resource — the same failure mode this plan is hardening the test against.

**Recommended Plan 058 §T1 addition**: probe each candidate gauge under both `river/observation/<code>` and `lake/observation/<code>` during roster construction. Persist the URI-path discriminator alongside `station_kind` (or override `station_kind` to `LAKE` when only the lake path returns data). Do not rely on hydrological classification alone. Capture this as an open question in Plan 058 if not yet noted.

---

## Open questions

Not blocking DRAFT → READY:

1. Should the test additionally probe `verify_gauge_reachable` for one station per call? It's a different SPARQL shape and would catch onboarding-path drift independently. **Tentative: no** — keep the live workflow's blast radius narrow; if onboarding-path drift becomes a real concern, add a second live test rather than overloading this one.
2. Should the failure message include a link to the LINDAS status page (if one exists) for triage? Worth checking; not blocking. The federal data portal does not publish a status page that I'm aware of.
