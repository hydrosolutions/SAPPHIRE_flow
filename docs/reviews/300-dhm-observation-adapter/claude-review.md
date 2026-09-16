## Findings — Plan 300 (`docs/plans/300-dhm-observation-adapter.md`), design/proportionality pass

Accepted as given: the owner-relayed BIPAD confirmation of the request/response contract (plan:27-30). I did not re-litigate it.

### 1. MAJOR — Backfilled observations are stored but never QC'd, so they are invisible to every consumer (§5, plan:105-112; §Config, plan:192-197; T3 outcome, plan:249-252)

§5 is built for multi-window backlogs ("Divide longer ranges into consecutive bounded windows… Never silently clamp away the oldest portion", `window_hours = 24`, `max_pages_per_station = 100`), and §Config says the adapter "can process an explicitly supplied older watermark in bounded windows". But scheduled QC only ever looks at `now - context_window_hours … now + 1h` with `context_window_hours = 2.0` (`flows/ingest_observations.py:279-280`, `:528`) and only re-processes rows still `RAW` inside that window (`:291-293`). No other scheduled path runs observation QC — `Stage1QualityChecker` has exactly two production callers, this flow and `services/onboarding.py:772` (historical, onboarding-only). Every downstream consumer filters `qc_status=QC_PASSED`: `services/operational_inputs.py:890`, `services/training_data.py:445`, `flows/compute_skills.py:98`, `services/hindcast.py:473`, `services/observation_alert_checker.py:58`.

So anything the adapter fetches older than ~2 h is permanently `raw`: stored, counted in `observations_stored`, and unusable for forecasting, training, baselines or alerts. This is the exact failure Plan 260 exists to remediate for three staging stations (`docs/plans/260-recover-the-un-qcd-station-history.md:29-56`). T3's outcome sentence "feeds observations through existing Flow 2 QC/storage" is therefore true only for the last two hours of any fetch.

**Violated requirement:** `docs/workflow.md` § Plan Structure — a task's Outcome must be one observable behaviour; and the plan must not claim an integration it does not achieve.

**Smallest correction:** in §5/§Config, either bound a DHM fetch to the QC-reachable window (making the multi-window machinery unnecessary in this slice — see the proportionality note below), or state explicitly that rows older than `now - context_window_hours` remain `RAW`/QC-invisible, name that as a limitation of this slice, and record which plan owns re-QC of backfilled rows.

Proportionality corollary: if the limitation is accepted rather than fixed, the multi-window division, `window_hours`, `max_pages_per_station` and "never silently clamp" requirements have no consumer inside this slice (scheduled ingest runs `*/5`, `cli/register_deployments.py:92-99`) and could be descoped with the bootstrap task that will actually need them.

### 2. MAJOR — One adapter receives *every* eligible station; "surface as a configuration problem" is unscoped and can be fatal for the whole run (§8, plan:139-141; §2, plan:77-79)

`ingest_observations_flow` builds a single adapter and hands it all eligible RIVER + LAKE + **WEATHER** stations in one batch (`flows/ingest_observations.py:586-609`, `:636`; the WEATHER inclusion is Plan 217 D1, documented at `docs/touchpoint-maps.md:1076-1089`). The existing adapter treats an unsupported station as a *per-station* skip with a warning (`adapters/hydro_scraper.py:125-131`), never as a run-level error. Plan 300 says unsupported networks/parameters "must be surfaced as configuration problems", and T3's verification says "rejection of incompatible station units/bindings" — if that rejection happens at config load or as a batch-wide failure, a single onboarded Nepal weather station (the DHM precipitation track's own direction) takes the entire ingest run down, including the DHM river gauges that are correctly configured. §2's "mixed-network dispatch … out of scope" leaves the mixed-station-batch case unaddressed, but the flow produces that batch unconditionally.

**Violated requirement:** `docs/workflow.md` § Preserve Existing Logic (per-station failure isolation is the current, deliberate behaviour, `types/observation.py:74-98`).

**Smallest correction:** in §8, state the granularity explicitly — an unsupported/unbound station in the batch yields its own failed `StationFetchOutcome` (or a logged skip, as BAFU does), never a batch-wide or config-load failure — and add that case to T3's fake-store isolation test.

### 3. MODERATE — DHM levels will be QC'd against the deployed Swiss thresholds; Plan 264 is neither cited nor depended on (frontmatter `depends_on: []`, plan:6; §Standards, plan:280-281)

Rule selection is `parameter` + cadence only (`types/domain.py` `rules_for`, quoted in `docs/plans/264-qc-rules-select-on-network.md:56-60`). A DHM 10-minute level series matches the deployed 600 s `water_level` rules in `config.toml:245-278` — `rate_of_change max_rate = 0.5`, `spike max_delta = 1.0`, `frozen_sensor tolerance = 0.001 / min_consecutive = 12` — all Swiss-derived. With the null datum that §4 mandates for `gauge_zero`/`unknown`, only `range_check` and `gross_outlier` are skipped (`services/qc_datum.py:29-32`); the remaining three run with Swiss numbers on a Nepali gauge. Plan 264 (DRAFT) exists precisely because "Plan 268's QC task cannot isolate a DHM rule set from the Swiss one on the current lookup" (264:11), and Plan 268 owns the DHM threshold values. §4 shows awareness of the datum skips but not of the thresholds.

**Violated requirement:** `docs/workflow.md` § Plan review — a plan must state its real dependencies; AGENTS.md § Documentation Hygiene single-source-of-truth.

**Smallest correction:** one sentence in §Standards recording that until Plan 264 lands, DHM water_level is checked against the Swiss 600 s thresholds in `config.toml:245-278`, that Plan 268 owns DHM threshold values, and that this is an operational-activation prerequisite (not an offline-adapter blocker).

### 4. MODERATE — The station-row preconditions for a DHM station to be polled at all are never stated (T3, plan:249-252; §Remaining deployment inputs, plan:306-313)

A station reaches the adapter only if `station_status == 'operational'` **and**, for RIVER/LAKE, `gauging_status == GAUGED` (`flows/ingest_observations.py:601-609`). `GaugingStatus` is documented as a discharge/rating-curve concept — `UNGAUGED` means "no observations to fetch" (`docs/architecture-context.md:278`; `docs/plans/035-rating-curve-provenance.md:371`) — yet the plan states DHM has no rating tables and keeps rating conversion out of scope. A DHM level-only gauge onboarded as `UNGAUGED` is silently never polled, and T3's fake-store test (which constructs its own `StationConfig`s) would pass regardless.

**Violated requirement:** `docs/workflow.md` § Plan Structure — Verification must be a discriminating check of the stated Outcome.

**Smallest correction:** add to §Remaining deployment inputs (or §2) the required station-row attributes for a bound DHM station — `station_kind = RIVER`, `station_status = operational`, `gauging_status = GAUGED`, `network`, `measured_parameters ∋ water_level` — and make T3's fake-store test use exactly that shape.

### 5. MODERATE — §7's cause mapping silently drops `NO_DATA` while redefining an empty poll as success (plan:121-129)

`FetchOutcomeCause` deliberately splits `NO_DATA` from `MALFORMED_RESPONSE` for "a legitimately-empty poll" (`types/enums.py:241-250`), and BAFU returns `NO_DATA` for an empty result (`adapters/hydro_scraper.py:559-560`). §7 enumerates four causes, omits `NO_DATA`, and declares "a completed empty window is a clean empty result". That choice is almost certainly right — at a `*/5` schedule against a 10-minute series most polls are legitimately empty, and `_fetch_health_detail` counts *any* non-null cause as a failed station (`flows/ingest_observations.py:176-205`), so using `NO_DATA` would drive `CRITICAL` health records on healthy runs. But as written it reads as an omission, and it makes `stations_failed` mean different things for the two adapters.

**Violated requirement:** `docs/workflow.md` § Context maintenance — surfaced context is applied, deferred with a reason, or tracked, never silently dropped.

**Smallest correction:** state in §7 that the DHM adapter never emits `NO_DATA` (empty window ⇒ `failure_cause = None`) and why, so the divergence from `hydro_scraper.py:559-560` is a recorded decision.

### 6. MINOR — T3's In/Out excludes the cursor code T3 must change (plan:254-257 vs §8, plan:142-147)

T3 bounds its edit to "the small production-setup section of `flows/ingest_observations.py`" (that is `:549-568`), but station-aware cursor selection lives in `_CURSOR_PARAMETER_BY_KIND` / `_cursor_parameter_for_kind` (`:108-128`) and the `since` loop (`:628-633`). As written the In/Out forbids the change the task's own Pre-change test demands.

**Smallest correction:** name `flows/ingest_observations.py:108-128` and `:628-633` in T3's In, and keep the existing "raises `ConfigurationError` on an unhandled `StationKind` rather than defaulting" property (`:121-128`, `docs/touchpoint-maps.md:1080-1083`) as an explicit non-regression.

### 7. MINOR — T3's documentation targets are incomplete (plan:276-278)

T3 updates `docs/conventions.md`, `docs/spec/types-and-protocols.md` and the captured-example README. Two maintained documents state the facts this plan changes and are not listed: `docs/spec/config-reference.toml:528-534` (documents `type = "hydro_scraper"` as the only `[adapters.river_stations]` adapter type, and is load-tested by `tests/unit/test_config.py:164-174`), and `docs/touchpoint-maps.md:1044` (the `[adapters.river_stations].endpoint` upstream input) and `:1076-1089` (the `_cursor_parameter_for_kind` mapping).

**Violated requirement:** AGENTS.md / `docs/workflow.md` § Documentation Hygiene — every code change updates affected docs.

**Smallest correction:** add both paths to T3's doc list.

### 8. MINOR — the units rule is under-specified for the two cases that actually arise (§4, plan:91-104)

"Require configured station water-level units to agree with metres" leaves two gaps: (a) `StationConfig.water_level_unit` is nullable (`types/station.py:54`) and the only existing guard fires *only* when `water_level` is a forecast target (`services/onboarding.py:464-473`), so a DHM station will commonly carry `None` — the plan does not say whether that is accepted; (b) `SUPPORTED_WATER_LEVEL_UNITS = {"m", "m a.s.l."}` (`services/qc_datum.py:20`), so `"m a.s.l."` "agrees with metres" while contradicting a `gauge_zero` or `unknown` `level_reference`.

**Smallest correction:** in §4, require `water_level_unit == "m"` for a `gauge_zero`/`unknown` binding, permit `"m a.s.l."` only with `level_reference = "masl"`, and say explicitly whether a null unit is rejected.

### 9. MINOR — dangling plan reference (plan:20-21)

"The unrelated Plan 273 illustrative demo does not depend on this work." No plan 273 exists in `docs/plans/` or `docs/plans/archive/` at this base commit (highest active is 272). Given this repo's history of number collisions and the rule that plans are referenced by number, an unresolvable citation is worse than none.

**Smallest correction:** delete the sentence, or replace `273` with a locator a reader can resolve.

---

**Verification limitations**

- Read-only pass using Read/Glob/Grep only, at worktree base `f2dc569c` with the plan uncommitted. No tests, no `pytest`, no network requests, no Codex or other agent invoked; I did not read any other review of this plan.
- I did not verify the captured JSON against the live BIPAD or DHM API. I confirmed offline that `day.json` holds 118 ascending, non-duplicated `waterLevelOn` values from `00:00` to `23:50 +05:45`, that its `next` is populated on a 118-of-500 short page, and that `empty-page.json` also carries a `next` with `count = 9223372036854775807` — so T1/T2's fixture-based verifications are performable as written.
- Claims requiring live database or deployment state — which stations exist with `network = 'dhm'`, their `gauging_status`, the actual overlay in use on the mini — are not checkable from the repository; findings 3 and 4 are argued from code paths and config, not from observed rows.
- I did not assess Plan 106 D5-2's wave sequencing beyond confirming the roadmap rows at `docs/plans/106-v1-critical-path-roadmap.md:130-131` pair the adapter with unit normalisation in one to-draft plan, which Plan 300 deliberately splits.
