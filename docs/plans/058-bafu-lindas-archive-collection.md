# Plan 058 — BAFU LINDAS archive via operational collection on Mac Mini v0

**Status**: DRAFT
**Date**: 2026-04-18
**Revision**: 2 — corrections after review round found multiple wrong claims.

**Rev 2 fixes (2026-04-18)**:
(a) **Flow 2 current cadence is 30 min, not 10 min.** `register_deployments.py` defaults `SCHEDULE_INGEST_OBSERVATIONS` to `"*/30 * * * *"`. T2 now owns the decision and implementation of changing it to `"*/10 * * * *"` (cicd.md log-volume math assumes 48 runs/day — 10-min cadence triples that to 144/day; cross-reference in T2).
(b) **`fetch_observations` does NOT batch concurrently.** It is a synchronous for-loop over stations. At 170 stations × ~1 s/request, a single poll takes ~170 s — borderline for a 10-min window. Risks table updated; T2 adds a mitigation step (stagger or parallelise) if benchmarks show headroom is insufficient.
(c) **`scripts/onboard.py` is CAMELS-CH-specific**, not for TOML-driven LINDAS roster onboarding. T1 now points at the correct entry point: `sapphire_flow.flows.onboard.onboard_stations_flow` driven from a stations TOML via `record_fixtures.parse_stations_toml` helpers (or equivalent — verify at implementation time).
(d) **`config/mac_mini/stations.toml` does not exist.** The deployment config is `config.toml` at repo root with `[onboarding].basin_ids`. **This file is actively owned by Plan 046** — T1 cannot touch it until Plan 046 is DONE. Files-to-modify table corrected.
(e) **Gate for T1/T2 tightened** from "Plan 046 stable ≥1 week" (too weak — could fire mid-046) to "Plan 046 DONE (exit gate 4 passed — Stream D validation report committed with `go` recommendation)".
(f) **T5 test name** corrected: `test_contract_lindas_response` does not exist post-Plan-055. The correct test is `test_fetch_returns_expected_records_from_fixture_response` in `tests/integration/adapters/test_hydro_scraper.py`.
(g) **T5 live-LINDAS ambiguity**: the existing fixture-backed test cannot simply "run against live LINDAS". T5 must either add a `--live` mode (env-var-gated variant) or create a separate schedule-only test. Clarified in T5.
(h) **Lake forecast_targets correction**: v0-scope.md guiding principles say lake stations forecast `water_level` only. Rev 1 had `["discharge", "water_level"]` — wrong. Set to `["water_level"]`.
(i) **§A13 cross-reference** for the 33-lake-station count is wrong — that number is in the guiding-principles intro (line 11), not §A13. Corrected.
(j) **§I5 gauging_status branching** note added to T1: setting `gauging_status = "GAUGED"` in the roster does NOT license flow code to skip the gauging_status branch. v0-scope §I5 is the hardest v1-compatibility guardrail.
(k) **pg_dump attribution**: Plan 046 / Plan 044 already wire daily `backup_database_flow` at 02:00 UTC. Plan 058 does not introduce this — risks row corrected.
(l) **T5 CI workflow isolation**: Plan 046 B1 adds deployment test markers to `pyproject.toml`. T5's live-LINDAS check must use its own workflow file and its own marker (e.g. `live_lindas`) to avoid coupling to Plan 046 B1's ship order.
(m) **Stream classification after review**: T3 + T4 remain SAFE-NOW (no Plan 046 overlap). T5 CI work can start NOW with its own marker (not coupled to Plan 046). T6 code can be written NOW but the Prefect deployment registration waits for Plan 046 DONE. T1 + T2 remain BLOCKED on Plan 046 DONE.

**Depends on**: Plan 046 (Mac Mini staging deployment) reaching exit gate 4 — Stream D validation report committed with `go` recommendation — at which point `config.toml` and deployment config are settled and safe to widen. Until then, only T3 / T4 / T5 / T6 (code-only, non-deployment portions) are actionable.

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
2. Update the deployment config — `config.toml` at repo root, section
   `[onboarding].basin_ids` — to include every LINDAS-available gauge.
   Station count should move from the 5-station staging subset (Plan
   046) to ~170. For each station, ensure:
   - `network = "bafu"`, `ownership = "own"` (v0-scope §A4 defaults).
   - `gauging_status = "GAUGED"` (BAFU LINDAS gauges are automated).
   - `forecast_targets = ["discharge"]` for river stations;
     `["water_level"]` for the 33 CAMELS-CH lake stations (v0-scope.md
     guiding-principles paragraph, line 11: "river stations forecast
     discharge, lake stations (33 in CAMELS-CH) forecast water_level").
     Do NOT add `discharge` to lake targets — that would silently trigger
     training for a parameter the CAMELS-CH training data may not
     support for lakes.

   **§I5 guardrail**: setting `gauging_status = "GAUGED"` for every
   roster station does NOT license flow code to skip the `gauging_status`
   branch. v0-scope §I5 is the hardest v1-compatibility rule ("manual
   stations and ungauged stations will be introduced in v1; if v0 flow
   code never consults `gauging_status`, every such code path needs
   retrofitting"). T1 changes the roster, not the flow logic.
3. Onboard the widened roster. `scripts/onboard.py` is CAMELS-CH-specific
   (it calls `onboard_from_camelsch` and takes `--basin-ids`, not a
   stations TOML) and is NOT the right entry point for a general
   LINDAS roster. Use the onboarding flow directly
   (`sapphire_flow.flows.onboard.onboard_stations_flow` — verify name
   at implementation time) driven from the updated `config.toml`. If a
   TOML-driven helper does not exist yet, small additions to the
   onboarding flow or a new `scripts/onboard_lindas.py` are in scope.
4. Verify the operational DB receives data for each newly onboarded
   station within one ingest cycle (10 min after T2 lands; 30 min
   before T2): `SELECT station_id, COUNT(*), MAX(timestamp) FROM
   observations GROUP BY station_id` and look for even coverage.
5. Commit the widened `config.toml` and re-run the Prefect deployment
   registration.

### T2 — Change Flow 2 cadence from 30 min to 10 min for LINDAS

The current default in `src/sapphire_flow/cli/register_deployments.py`
is `SCHEDULE_INGEST_OBSERVATIONS = "*/30 * * * *"` — every 30 minutes.
T2 decides and implements the change to `"*/10 * * * *"`.

1. Set `SCHEDULE_INGEST_OBSERVATIONS = "*/10 * * * *"` in the Mac Mini
   deployment env (either via the docker-compose env-var override or
   by changing the default in `register_deployments.py`, whichever
   Plan 046's C1/C2 ends up mandating). Re-register deployments.
2. **Benchmark before declaring done**: `HydroScraperAdapter.fetch_observations`
   is a sequential synchronous for-loop over stations (no `task.map`,
   no `asyncio.gather`). At 170 stations × ~1 s/request, one poll takes
   ~170 s — ~28% of a 10-min window. A single slow LINDAS response or
   a network blip pushes the poll past 10 min and the next window
   collides. Run one 24-hour window after the cadence change and
   measure the 95th percentile of `fetch_observations` wall time. If it
   exceeds 300 s, either:
   (a) widen the cadence to 15 or 20 min; or
   (b) open a follow-up to parallelise via `task.map` /
       `asyncio.gather` / explicit per-station stagger.
3. Collect one 24-hour window after stabilisation: expected per-station
   poll count = 144. Compute actual count and gap percentage; any
   station with systematic drops is a bug to investigate before
   declaring the archive "running".
4. Update `docs/standards/cicd.md` log-volume math: the 48 obs-ingest
   runs/day figure becomes 144. Verify the daily structlog/Prefect-log
   volume projection still fits the retention policy (`PREFECT_API_DATABASE_PRUNE_OLDER_THAN=30`
   and whatever Caddy/Docker-journald limits are set by Plan 046 D12).

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

**Post-hoc scope-creep note on commit `289c5f8`**: the T4 implementation
agent (landed 2026-04-19 on `main` as `289c5f8 docs(plan-058/T4): …`)
also scooped up pre-existing uncommitted `CHOWN` + `FOWNER` `cap_add`
changes in `docker-compose.yml` that were sitting in the working tree
from earlier Plan 060 T2 work. Those cap_add additions are **Plan 060 T2
scope**, not Plan 058 T4, and are documented as such in Plan 060 T2
(see `docs/plans/060-*.md` line 76 — "commit `289c5f8` already merged
the CHOWN + FOWNER cap_add additions into main") and in Plan 046
revision 9 (which correctly attributes the cap_add landing to Plan 060,
not to this plan). Nothing in the docker-compose diff in `289c5f8`
belongs to Plan 058 T4 — T4 is only the `tests/fixtures/reference/README.md`
and `docs/v0-scope.md` changes in that commit. Left as-is rather than
amended; the attributions in Plans 046 and 060 are authoritative.

### T5 — Schema-drift watch against live LINDAS

1. `test_fetch_returns_expected_records_from_fixture_response` in
   `tests/integration/adapters/test_hydro_scraper.py` (post-Plan-055 T3)
   pins the LINDAS response shape against a recorded fixture. That test
   cannot be reused as-is against live LINDAS — it depends on the
   fixture's specific values. T5 adds a **new** live-endpoint test in
   a separate marker-gated module, e.g.
   `tests/integration/live/test_lindas_live_schema.py`, that:
   - Is skipped by default (marker `live_lindas`, gated off PRs and
     regular CI).
   - Calls `HydroScraperAdapter.fetch_observations` with one real
     reference station (e.g. `"2044"`).
   - Asserts that the response unpacks into `RawObservation` records
     with the expected shape (timestamp-parseable, value numeric,
     parameter in the expected set) — **not** against specific values,
     which vary over time.
2. Add a scheduled weekly GitHub Actions job that runs only the
   `live_lindas` marker. Use a **distinct workflow file** (e.g.
   `.github/workflows/live-lindas-weekly.yml`) to avoid coupling to
   Plan 046 B1's deployment-test marker changes in `pyproject.toml`;
   the `live_lindas` marker is independent.
3. Failure = schema drift = the archive accumulated since the last
   green run may be silently corrupted; halt the accumulation phase
   and investigate.
4. Time bound: the weekly live-LINDAS check runs for the duration of
   the accumulation phase (roughly the 6 months between T1 completion
   and promotion). After promotion, keep the check as a guard on the
   fixture's real-world validity.

**Parallel-safety note**: T5 is safe to implement NOW (code + workflow)
even though T1/T2 are gated on Plan 046 DONE. The `live_lindas` test
and its weekly workflow are additive and do not touch Plan 046 files.

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

**Parallel-safety note**: the coverage-summary script
(`src/sapphire_flow/tools/observation_coverage_summary.py`) can be
written and unit-tested NOW. The Prefect deployment registration waits
for Plan 046 DONE — it adds a new scheduled deployment to the Mac Mini
stack, which Plan 046 currently owns. Keep the module under `tools/`
(not `flows/`) so it is clearly framed as a utility, not a new Flow.

**Flow-4 scope note**: the v0-scope.md "Deferred beyond v0" table lists
Flow 4 (pipeline monitoring) as v0c/v1. T6 is a functional subset of
Flow 4 (cron-scheduled pipeline health check). Document T6 in the
future Flow 4 design notes as a "precursor" so its scope is tracked
rather than silently accumulating into Flow 4's eventual scope.

---

## Dependency graph

```json
{
  "stream-1-prep-now": {
    "tasks": ["T3", "T4"],
    "parallel": "both in parallel — doc + spec work, no Plan 046 overlap",
    "depends_on": []
  },
  "stream-2-watch-code-now": {
    "tasks": ["T5", "T6-code"],
    "parallel": "both in parallel — code + workflow, additive only, no Plan 046 file overlap",
    "depends_on": []
  },
  "stream-3-roster": {
    "tasks": ["T1", "T2"],
    "sequential": true,
    "depends_on": ["Plan 046 DONE (exit gate 4 + go recommendation)"]
  },
  "stream-4-watch-deploy": {
    "tasks": ["T6-prefect-deployment-registration"],
    "sequential": true,
    "depends_on": ["Plan 046 DONE", "T6-code"]
  },
  "promotion": {
    "tasks": ["T3 implementation"],
    "sequential": true,
    "depends_on": ["6 months of ≥95% coverage data", "T3 spec"]
  }
}
```

Streams 1 + 2 (T3 spec, T4 docs, T5 live-LINDAS check + workflow, T6
code) can start today without touching Plan 046's files. Streams 3 + 4
wait on Plan 046 DONE (Stream D validation report, `go` recommendation).

---

## Files to create / modify

| Path | Task | Purpose | Conflict with Plan 046? |
|---|---|---|---|
| `config.toml` (`[onboarding].basin_ids`) | T1 | Widen from 5 to full LINDAS roster | YES — owned by Plan 046 until DONE |
| `src/sapphire_flow/cli/register_deployments.py` (or Mac Mini env override) | T2 | Change `SCHEDULE_INGEST_OBSERVATIONS` from `*/30` to `*/10` | YES — Plan 046 C1/C2 owns deployment wiring |
| `docs/standards/cicd.md` (log-volume math) | T2 | Update 48 runs/day → 144 runs/day for obs ingest | Low — append-only edit |
| `tests/fixtures/reference/README.md` | T4 | Gap-budget + no-catchup note | No |
| `docs/v0-scope.md` §E1 | T4 | Cross-reference Plan 058 | Low — §E1 vs Plan 046's §E5 |
| `src/sapphire_flow/tools/promote_reference_fixture.py` | T3 (deferred impl) | Promotion script | No (new file) |
| `src/sapphire_flow/tools/observation_coverage_summary.py` | T6 code | Daily gap-check script | No (new file) |
| `.github/workflows/live-lindas-weekly.yml` | T5 | Weekly live-LINDAS schema check (own workflow file, own `live_lindas` marker) | No (new file) |
| `tests/integration/live/test_lindas_live_schema.py` | T5 | Live-endpoint test gated by `live_lindas` marker | No (new file) |

---

## Risks

| Risk | Mitigation |
|---|---|
| Per-poll wall time approaches the 10-min budget | `HydroScraperAdapter.fetch_observations` is a **sequential synchronous loop** — no `task.map`, no `asyncio.gather`, no inter-station stagger. 170 stations × ~1 s/request ≈ 170 s (~28% of the 10-min window). A network blip or slow LINDAS response pushes a poll past 10 min and the next cycle collides. T2 step 2 mandates a post-deployment benchmark; mitigation is either widen cadence (15–20 min) or parallelise via `task.map` / `asyncio.gather`. |
| LINDAS rate-limits at ~170 stations × 6 polls/hour (1020 req/h) | Benchmark during T1 rollout. If observed, reduce concurrency (once parallelisation is added) or widen cadence to 15 min. Open question 2 tracks the actual limit. |
| Mac Mini disk fills with ~27M observation rows/year | v0-scope §A1 / §A2 defer partitioning and cold storage. Growth projection: ~1 GB/year for observations alone (170 × 3 × 6 polls/h × 24 × 365 × ~40 bytes/row). Plan 046 already prescribes daily `backup_database_flow` at 02:00 UTC; that backup volume scales with this projection. At ≥500 GB total DB size, revisit partitioning per v0-scope §A1. |
| Archive gaps accumulate silently (no Flow 4 yet) | T5 (weekly schema check) + T6 (daily coverage summary) are the band-aid. Document that manual vigilance is required during the 6-month accumulation. T6 is framed as a Flow 4 precursor; its scope is tracked in Flow 4's future design notes. |
| Mac Mini outage destroys the archive | Plan 044 / Plan 046 already wire `backup_database_flow` at 02:00 UTC via `SCHEDULE_BACKUP_DATABASE = "0 2 * * *"`. Plan 058 does **not** introduce this requirement — it relies on it. Action: verify this schedule is actually running on the Mac Mini before the accumulation phase begins. |
| Promotion window never reaches 95% coverage | If coverage never stabilises, the synthetic placeholder stays indefinitely. That is an acceptable outcome — the fixture tests pass against synthetic data; no hard failure. Revisit adapter / deployment reliability rather than weakening the threshold. |
| Station catalogue from LINDAS differs from our `config.toml` over time | T1 enumeration is a one-off; if BAFU adds/removes stations mid-accumulation, handle manually — do not auto-sync (an auto-sync could drop accumulated data for a deprecated station). |
| T1 edits `config.toml` while Plan 046 is still active | Gate T1 on Plan 046 exit gate 4 (Stream D validation report committed, `go` recommendation). The "stable ≥1 week" phrasing used in the rev 1 stub was too weak — it could fire mid-046 while `config.toml` is still in flux. |
| T6 adds a Prefect deployment that overlaps Flow 4 scope | Document T6 explicitly in Flow 4's future design as a "precursor". Keep the module under `tools/` (not `flows/`). Code can be written now; Prefect deployment registration waits for Plan 046 DONE. |

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
