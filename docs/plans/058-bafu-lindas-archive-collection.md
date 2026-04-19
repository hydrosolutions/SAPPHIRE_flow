# Plan 058 — BAFU LINDAS archive via operational collection on Mac Mini v0

**Status**: DRAFT
**Date**: 2026-04-18
**Depends on**: Plan 046 (Mac Mini staging deployment) reaching operational
stability on its current 5-station subset. Once stable, this plan widens the
roster to the full LINDAS catalogue.

---

## Scope

Use the Mac Mini v0 deployment as the LINDAS archive. Flow 2 (observation
ingest) polls every BAFU LINDAS-available gauging station at 10-min
cadence. The `observations` table accumulates readings naturally over
time. There is **no separate archive-collection pipeline**.

Once ≥6 months of data with ≥95% interval coverage have accumulated, a
one-off promotion script exports observations for the 7 reference stations
in `tests/fixtures/reference/stations.toml` to
`tests/fixtures/reference/bafu_observations.parquet`, replacing the
synthetic placeholder Plan 055 T1 reframed as by-design.

## Why this framing

LINDAS exposes only the current reading — `hydro_scraper.py:173-192` binds
a single current-reading subject URI and the endpoint has no historical-
window semantics. A fixture of real readings therefore requires collecting
the current reading over time. The operational v0 deployment already
has to poll LINDAS for the forecast cycle; the archive is a byproduct of
operational work, not a parallel sidecar.

Corollary: every minute of collector downtime = **permanent data loss**.
There is no catchup. Gap semantics are documented in T4 below.

## Current state (2026-04-18)

- Plan 046 has brought up the Mac Mini staging deployment with 5 BAFU
  stations as a validation subset.
- Flow 2 runs on that deployment at its configured cadence (verify in T2).
- `tests/fixtures/reference/bafu_observations.parquet` is synthetic; Plan
  055 T1 has reframed the README as "by-design stand-in pending Plan 058".

---

## Task list

### T1 — Widen Mac Mini deployment to the full LINDAS gauging roster

**Gated on**: Plan 046's 5-station subset running stably for ≥1 week
(proves the Prefect worker, DB, adapter wiring, and cadence all hold).

1. Enumerate all BAFU gauging stations reachable via LINDAS. Candidate
   sources, in descending order of trust:
   (a) A machine-readable catalogue from BAFU (preferred if one exists —
       see open question 1).
   (b) A SPARQL query against the LINDAS endpoint itself asking for all
       subjects matching the `HydrologicalStation` class.
   (c) Manual curation by cross-referencing existing internal station
       lists and BAFU's hydroportal.
2. Emit the result as a `stations.toml` addition for the Mac Mini
   deployment, with `network = "bafu"`, `ownership = "own"`,
   `gauging_status = "GAUGED"`, and per-station `forecast_targets` set
   from the station kind (`["discharge"]` for river stations;
   `["discharge", "water_level"]` for the 33 CAMELS-CH lake stations per
   v0-scope.md §A13).
3. Run the station-onboarding script (`scripts/onboard.py`) with the new
   roster. Station count should jump from 5 to ~170.
4. Verify the operational DB receives data for each newly onboarded
   station within one 10-min cycle after onboarding completes (`SELECT
   station_id, COUNT(*), MAX(timestamp) FROM observations GROUP BY
   station_id` and look for even coverage).
5. Commit the widened `stations.toml` and re-run the Prefect deployment
   registration.

### T2 — Confirm and, if needed, set Flow 2 cadence to 10 min for LINDAS

1. Read the Mac Mini deployment's current `DeploymentConfig` and Prefect
   schedule for Flow 2. Document the current cadence.
2. If the cadence is not 10 min for LINDAS stations, update the
   deployment config. Note that weather-source ingest may run on a
   different cadence (MeteoSwiss has its own publication rhythm); this
   plan only prescribes 10 min for LINDAS.
3. After the change lands, collect one 24-hour window of observations
   and compare: expected per-station poll count = 144; compute actual
   count and the gap percentage. Any station with systematic drops is
   a bug worth investigating before declaring the archive "running".

### T3 — Spec the promotion script (implementation deferred)

**Do not implement until ≥6 months of data exist**. Specify now so the
interface is stable.

- **Entry point**: `sapphire_flow.tools.promote_reference_fixture`
  (mirrors the existing `sapphire_flow.tools.record_fixtures` layout).
- **Inputs**:
  - `--start`, `--end` (UTC timestamps bounding the promotion window).
  - `--stations` (defaults to the 7 station codes in
    `tests/fixtures/reference/stations.toml`).
  - `--parameters` (defaults: `discharge`, `water_level`, `water_temperature`
    for river stations; `water_level` for lake stations).
  - `--output` (defaults to a sibling path of the current fixture —
    never overwrites automatically; a human inspects then renames).
- **Query**: `SELECT station_id, parameter, timestamp, value FROM
  observations WHERE station_id IN (...) AND parameter IN (...) AND
  timestamp BETWEEN :start AND :end`.
- **Output schema**: Parquet matching exactly what
  `tests/unit/adapters/test_reference_dataset.py::test_reference_parquet_schema`
  asserts. The promotion script runs that schema check on its own output
  and fails if it doesn't match.
- **Manifest**: prints per-station row count, expected row count (window
  length × 6 polls/hour × parameter count), and coverage percentage.
  Exits non-zero if any (station, parameter) pair has coverage < 95%.
- **Gap handling**: the script does NOT attempt gap-filling. It reports
  gaps and leaves the operator to decide whether to advance the window.

### T4 — Document gap budget and no-catchup property

1. Add a section to `tests/fixtures/reference/README.md` (Plan 055 T1
   created the "by-design" framing; this extends it) stating:
   - LINDAS has no historical retrieval; the adapter fetches the current
     reading only.
   - Every minute of collector downtime = permanent archive gap.
   - Target: ≥95% interval coverage over any candidate 6-month
     promotion window. Windows below threshold are skipped — the
     synthetic placeholder stays in place.
   - Downtime events (Prefect worker outage, network, DB) MUST be logged
     in an ops journal during the accumulation phase, so the team can
     correlate later gaps to known outages.
2. Add a short note to `docs/v0-scope.md` §E1 cross-referencing this plan
   so future readers understand what "re-record when operational
   deployment accumulates 6+ months" actually entails.

### T5 — Schema-drift watch (reuse, don't rebuild)

1. `test_contract_lindas_response` in
   `tests/integration/adapters/test_hydro_scraper.py` pins the LINDAS
   response shape against a recorded fixture. No new test.
2. Add a scheduled CI job (or ops-runbook weekly check) that runs this
   test against LIVE LINDAS — distinct from the fixture-backed unit
   tests that run on every PR. Failure = schema drift = the archive
   accumulated since the last green run may be silently corrupted.
3. Time bound: the weekly live-LINDAS check runs for the duration of
   the accumulation phase (roughly the 6 months between T1 completion
   and promotion).

### T6 — Lightweight gap-detection until Flow 4 lands

Flow 4 (pipeline monitoring) is deferred to v0c/v1 per v0-scope.md.
Without it, gap detection during the 6-month accumulation is blind.
Cheap mitigation:

1. Write a daily summary script (`sapphire_flow.tools.observation_coverage_summary`)
   that queries `observations` for the last 24 h and emits per-station
   interval counts and gap percentages to structlog / stdout.
2. Run it via a daily Prefect schedule on the Mac Mini.
3. Document that if any station falls below 90% coverage for two
   consecutive days, an ops check is due.

This is not a replacement for Flow 4 — it's a band-aid for the
accumulation phase. Scope limited to ~50 lines of code + a Prefect
deployment.

---

## Dependency graph

```json
{
  "stream-1-prep": {
    "tasks": ["T3", "T4"],
    "parallel": "both in parallel — doc + spec work, no runtime dependency",
    "depends_on": []
  },
  "stream-2-roster": {
    "tasks": ["T1", "T2"],
    "sequential": true,
    "depends_on": ["Plan 046 stable ≥1 week"]
  },
  "stream-3-watch": {
    "tasks": ["T5", "T6"],
    "parallel": "both in parallel",
    "depends_on": ["T1"]
  },
  "promotion": {
    "tasks": ["T3 implementation"],
    "sequential": true,
    "depends_on": ["6 months of ≥95% coverage data", "T3 spec"]
  }
}
```

Stream-1 (T3 spec + T4 docs) can start today — no v0 dependency. Streams
2 and 3 begin once Plan 046 validates the 5-station subset.

---

## Files to create / modify

| Path | Task | Purpose |
|---|---|---|
| `config/mac_mini/stations.toml` (or equivalent) | T1 | Widen from 5 to full LINDAS roster |
| `tests/fixtures/reference/README.md` | T4 | Gap-budget + no-catchup note |
| `docs/v0-scope.md` §E1 | T4 | Cross-reference Plan 058 |
| `src/sapphire_flow/tools/promote_reference_fixture.py` | T3 (deferred impl) | Promotion script |
| `src/sapphire_flow/tools/observation_coverage_summary.py` | T6 | Daily gap-check script |
| `.github/workflows/` or ops-runbook | T5 | Weekly live-LINDAS schema check |

---

## Risks

| Risk | Mitigation |
|---|---|
| LINDAS rate-limits at ~170 stations × 6 polls/hour | Stagger polls within each 10-min window (Flow 2 already batches via `fetch_observations(list[StationConfig])`). If rate-limiting is observed, reduce concurrency or widen the cadence to 15 min. |
| Mac Mini disk fills with ~27M observation rows/year | v0-scope §A1 / §A2 defer partitioning and cold storage. At ≥500 GB disk usage, revisit. Growth projection: ~1 GB/year for observations alone — not a near-term problem. |
| Archive gaps accumulate silently (no Flow 4 yet) | T5 (weekly schema check) + T6 (daily coverage summary) are the band-aid. Document that manual vigilance is required during the 6-month accumulation. |
| Mac Mini outage destroys the archive | v0-scope §A10 prescribes `pg_dump` backups. This plan mandates that pg_dump runs daily from deployment-day-one; without it, a disk failure before promotion wipes the archive. |
| Promotion window never reaches 95% coverage | If coverage never stabilises, the synthetic placeholder stays indefinitely. That is an acceptable outcome — the fixture tests pass against synthetic data; no hard failure. Revisit adapter / deployment reliability rather than weakening the threshold. |
| Station catalogue from LINDAS differs from our `stations.toml` over time | T1 enumeration is a one-off; if BAFU adds/removes stations mid-accumulation, handle manually — do not auto-sync (an auto-sync could drop accumulated data for a deprecated station). |

---

## Open questions (non-blocking)

1. **Station catalogue source.** Is there a machine-readable BAFU LINDAS
   catalogue, or do we have to derive the full roster from a SPARQL query
   or manual curation? Affects T1 scope.
2. **Rate-limit headroom.** LINDAS does not publish rate limits publicly.
   Target 170 × 6 = 1020 requests/hour (1 every ~3.5 s). Within the
   staggered 10-min window, peak concurrency is tunable. Benchmark during
   T1 rollout and adjust.
3. **Promotion window selection.** Once ≥6 months of ≥95%-coverage data
   exist, which window do we promote — the most-recent contiguous span,
   or one chosen to span both wet and dry seasons? Recommend: pick a
   window that includes at least one Spring snowmelt peak and one Summer
   low-flow trough. Decide when T3 is implemented.
4. **Long-horizon retention.** After promotion, do we prune the raw
   `observations` rows for the 7 reference stations to save disk, or
   keep them for future re-promotion? Recommend: keep. Disk is cheap;
   re-promotion may want a different window later.
5. **Nepal generalisation.** The DHM Nepal data source for v1 has similar
   real-time-only characteristics. Does this plan's pattern (operational
   DB = archive; export for fixtures) carry over, or does Nepal need a
   separate plan?

---

## Out of scope

- Redesigning the LINDAS adapter to accept historical windows — the data
  source does not expose them.
- NWP archive collection — tracked separately (ICON-CH2-EPS via Plan 045
  and follow-ups).
- Flow 4 pipeline monitoring implementation — this plan uses T5/T6
  band-aids; Flow 4 is v0c/v1.
- Data versioning of the archive (DVC, LakeFS, etc.) — the operational DB
  is authoritative; promotions are timestamp-bounded exports.
- Committing raw 10-min observations to git — the operational DB is the
  archive; only the 6-month-window Parquet export lands in the repo.
