## Plan 300 — independent Claude design/proportionality review

Reviewed `docs/plans/300-dhm-observation-adapter.md` at `78a2b9ff` in `/private/tmp/sapphire-plan300-review` against the repository. Findings only, ordered by severity.

---

### 1. MAJOR — Permanent `CONFIGURATION_ERROR` outcomes pin the Plan 175 fetch-health signal

**Where:** decisions 2 (`300:80-82`), 7 (`300:155-158`), 8 (`300:170`), T3 verification (`300:319-321`).

Every operational WEATHER station is eligible and is handed to the single adapter (`flows/ingest_observations.py:592-609, 636`). The plan requires each unsupported network/kind to return a *failed* outcome. `_fetch_health_status` (`flows/ingest_observations.py:193-205`) treats any non-`None` `failure_cause` as a failure: ≥1 ⇒ `WARNING`, all ⇒ `CRITICAL`; `fetch_errors`/`stations_failed` (`:650-656, 772-782`) likewise. A Nepal deployment with any weather station therefore reports `WARNING` on every run forever, and — before the still-outstanding station bindings are approved (`300:377-380`) — reports `CRITICAL`, indistinguishable from a total outage. This inverts the exact signal the code was built to provide ("any failure is invisible is the defect being fixed", `:196-200`), and it reverses today's behaviour: `HydroScraperAdapter` skips WEATHER with a log line and **no outcome at all** (`adapters/hydro_scraper.py:123-132`). `docs/workflow.md` § Preserve Existing Logic requires that change to be justified as wrong rather than unfamiliar; the plan neither states the consequence nor argues it.

**Smallest correction:** in decision 7, keep the existing skip-without-outcome behaviour for structurally unsupported kinds/networks (WEATHER, non-`dhm` networks) and reserve `CONFIGURATION_ERROR` for `network = "dhm"` stations that are eligible but unbound or unit/datum-incompatible — or state explicitly that `CONFIGURATION_ERROR` is excluded from `_fetch_health_detail`'s failure count.

---

### 2. MAJOR — All-or-nothing multi-window fetch can stall one station indefinitely, including its current data

**Where:** decisions 5 (`300:122-124`) and 6 (`300:139-141`), § Configuration (`300:211-214`).

A later-window/later-page failure fails the whole station fetch, and `types/observation.py:93-97` makes "failed outcome carrying observations" unrepresentable, so **no** rows are returned — including the newest window that fetched cleanly. The watermark (`flows/ingest_observations.py:630-633`) therefore never advances. One permanently-rejected record anywhere in the recovery range (a conflicting duplicate under `300:123-124`, a malformed non-null value under `300:92`) stops that gauge's ingest forever, not just its history: live levels stop arriving for a flood-forecasting station, and the condition self-heals only if the provider corrects the record. The plan names neither the stall mode nor any escape.

**Smallest correction:** add one sentence to § Configuration/T3 naming this failure mode and the documented operator procedure for advancing a stuck station's watermark past a permanently rejected record; list it beside the existing "resolve before unattended operational activation" item (`300:233-237`).

---

### 3. MAJOR — The QC claim generalises one station-day's cadence and omits the in-repo plan that owns the silent-miss defect

**Where:** § Standards and dependencies (`300:347-355`), Remaining deployment inputs (`300:378-380`).

"A DHM ten-minute level series would therefore use the Swiss 600 s water-level rules" holds only when the median inter-row gap in the QC window is *exactly* 600 s: `_infer_time_step` takes the median and returns 1 h for fewer than two rows (`services/qc.py:40-47`), `rules_for` matches on equality (`types/domain.py:160-167`), and a miss produces no flags, which `_aggregate_qc_status` reports as `QC_PASSED` with zero rules run (`flows/ingest_observations.py:130-135`). `docs/plans/272-qc-rules-unreachable-on-inferred-cadence.md:26-63` documents precisely this and **blocks Plan 264**, which Plan 300 does list as an activation prerequisite. The cadence claim rests on one station-day whose own README disclaims coverage (`dhm-api-examples/README.md:140-142`) and which is gappy (26 of 144 slots absent; gaps of 20/30/40/60 min). Any DHM gauge delivering hourly or 15-minutely, or any sparse window, silently passes QC having run nothing.

**Smallest correction:** restate the claim conditionally ("only when the inferred median is exactly 600 s; any other DHM cadence, or a sparse window, matches no rule and reports `QC_PASSED` with zero rules run") and add Plan 272 next to Plan 264 in the activation prerequisites.

---

### 4. MEDIUM — Dropping `NO_DATA` removes the only ingest-side signal that a DHM gauge has gone dark

**Where:** decision 7 (`300:146-149`).

The per-poll reasoning is sound, but failure causes are the *only* input to Flow 2's health record (`flows/ingest_observations.py:176-205`); there is no ingest-side staleness check. `observation_staleness_warning_hours` is consumed only by forecast input quality (`services/run_station_forecast.py:585`, `services/run_group_forecast.py:319`), and the stale-feed heartbeat at `flows/collect_bafu_observations.py:451-497` belongs to the separate archive collector. A DHM station that stops delivering entirely therefore reports clean polls indefinitely. The plan says this "leaves BAFU unchanged" but does not note that the DHM path loses the detection BAFU keeps.

**Smallest correction:** one sentence in decision 7 naming this as an accepted limitation with its owning follow-on, or a bounded condition (no rows for > N × `[adapters.river_stations.monitoring].expected_interval_hours` ⇒ failed outcome) using the config key that already exists at `config.toml:441-442`.

---

### 5. MINOR — T1's verification depends on an artifact T2 owns

`300:266-267` requires unit/reference incompatibilities to surface as "station-local failures", which needs `FetchOutcomeCause.CONFIGURATION_ERROR`; T2's In/Out claims that enum value and its spec (`300:274-275`), and the phase graph runs parse → fetch sequentially (`300:388-392`). T1 as written cannot pass.

**Correction:** move the additive enum value (and its spec entry) into T1's In/Out, or move those assertions into T2.

---

### 6. MINOR — T3's verification omits two regression modules that exercise the code it changes

T3 changes `_run_qc_task`'s interval parameters and its call sites (`300:294-296`), but its command list (`300:316-325`) omits `tests/unit/flows/test_ingest_observations_restatement.py` (`TestIngestRestatement::test_restatement_updates_value_and_reqcs`, `::test_identical_reingest_no_write_and_no_qc_churn` — both turn on which rows the QC window re-reads) and `tests/unit/flows/test_ingest_observations_derivation.py` (post-QC step in the same flow).

**Correction:** add both files to T3's verification command.

---

### 7. MINOR — "through at least its latest fetched measurement" meets an exclusive upper bound

`PgObservationStore.fetch_observations` filters `timestamp >= start` and `timestamp < end` (`store/observation_store.py:173-174`). An implementer reading `300:126-128` literally sets `end = latest_fetched` and drops the newest recovered row — the defect the task exists to fix — and the union rule at `300:128-129` masks it except under clock skew.

**Correction:** state the upper bound as strictly greater than the latest fetched measurement.

---

### 8. MINOR — Two affected documents are outside T2/T3's doc scope

`docs/design/v0-flow2-observation-pipeline.md:300-332` states as global Flow 2 facts that observation ingest is "NOT incremental", that `since` is "never read", and describes one LINDAS adapter — all falsified by this slice. `FetchOutcomeCause` is documented as "the per-station **LINDAS** fetch failure taxonomy" in `types/enums.py:240-244` and `docs/spec/types-and-protocols.md:250-256`; adding a DHM-only value without correcting that leaves the definition wrong. Neither is in the lists at `300:274-275` or `300:331-334`. `docs/workflow.md` § Documentation Hygiene forbids the resulting stale docs.

**Correction:** add both to the respective In/Out lists.

---

### 9. MINOR — "not a general sweep of historical RAW rows" is inaccurate within the extended interval

`300:133-135` disclaims sweeping RAW rows left by earlier failed runs, but `_run_qc_task` selects and updates *every* RAW row in the window it reads (`flows/ingest_observations.py:291-295, 322-327`). Widening that window for DHM necessarily re-QCs any such rows that fall inside it. The true limitation is only that rows older than the extended interval are untouched.

**Correction:** restate as "rows older than the extended interval are not swept"; drop the claim that pre-existing RAW rows are excluded.

---

### 10. MINOR — A second consumer of `[adapters.river_stations]` ignores `type`

`tools/record_fixtures.py:236-254` reads `[adapters.river_stations].endpoint` from `config.toml` and constructs `HydroScraperAdapter` unconditionally. Under a `type = "dhm"` deployment, `--source bafu` POSTs a SPARQL body to the DHM host. The plan's integration note (`300:50-53`) and T3's In/Out (`300:294-296`) cover only the flow.

**Correction:** one line in T3 requiring that tool to assert `type == "hydro_scraper"` before constructing the LINDAS adapter.

---

## Verification limitations

- Read-only tools only, as instructed: no tests, linters, type checks, or shell commands were run; every behavioural claim above is derived from reading source, not execution.
- I did not open `original-responses.zip` or recompute any `sha256`/`stored_sha256` — no hashing tool was available. I accepted the owner-relayed BIPAD contract confirmation and the manifest's two-hash scheme as described.
- `day.json`'s 118 records and their timestamps were confirmed by pattern extraction over the single-line file (first record exactly at the `__gt` lower bound; last at 23:50; `next` non-null despite 118 < `limit=500`). I did not verify the individual `waterLevel` values, nor the full contents of the other captures beyond `history.json`, `empty-page.json` and `manifest.json`.
- Per instruction I did not read `docs/reviews/300-dhm-observation-adapter/` (README, claude, codex, contract reports), so overlap with earlier rounds is possible and none of the above should be read as agreeing or disagreeing with them.
- No live database, staging host, Prefect deployment parameters, or DHM endpoint was inspected; the DHM cadence assessment rests solely on the one captured station-day.
