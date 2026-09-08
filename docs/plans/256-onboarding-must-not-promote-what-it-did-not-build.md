---
status: DRAFT
created: 2026-09-08
plan: 256
title: Onboarding must not promote what it did not build — hold on invalid geometry, audit every status change, reconcile the mislabelled fleet
scope: Close the two paths by which a station reaches or keeps `station_status = 'operational'` without the substance the status claims — an eligibility exclusion that withholds a station's operational forcing without withholding the station, and a status change that leaves no audit trail. Then reconcile the five staging stations currently mislabelled. Explicitly NOT a change to what onboarding trains, NOT the catchment-outside-Switzerland forcing question (deferred), NOT model-onboarding promotion, NOT geometry repair.
depends_on: [255]
blocks: []
source: 2026-09-08 — a read-only diagnosis of the mac-mini staging host. Five stations carry `operational` although the promotion gate could not have passed them; one station was excluded from operational forcing by an invalid polygon and promoted anyway. Revised the same day after an independent Codex review returned 4 blockers against the first draft, including that T1 did not remediate the very station the plan exists for.
---

# Plan 256 — onboarding must not promote what it did not build

## Status

**DRAFT — revised after independent review, not yet re-reviewed.** Awaiting owner READY.

⚠️ **High-risk.** T1 changes production promotion behaviour; T4 mutates staging data. Per
`docs/workflow.md` §High-risk work this warrants one additional owner-commissioned review beyond the
standard Claude + Codex passes.

⚠️ **Depends on Plan 255 T1 only** — the typed eligibility exclusions. Not on the rest of 255. The
first draft had the dependency backwards, assigning that plumbing here while 255 needed it first.

### What the review changed

Four blockers against the first draft, all verified against source:

1. **T1 did not fix Branson.** It promised the station "stays `onboarding`" — true for a *new*
   station, false for an already-operational one, which is what Branson is.
2. **T2 invented `STATION_PROMOTED` when `STATION_STATUS_CHANGE` already exists**
   (`types/enums.py:361`, `docs/spec/types-and-protocols.md:313`) — and put the audit in a store
   class that has no principal to attribute it to.
3. **T1 named two call sites; there are four.** The first draft grepped `src/` and `tests/` but not
   `scripts/`.
4. **The audit guarantee had a hole**: `scripts/onboard.py:339` takes the unaudited path.

A factual claim was also wrong: Branson does **not** run on stale `camels-ch` forcing. See below.

## Why this exists

Two independent defects, found together.

### Defect 1 — an eligibility exclusion withholds forcing without withholding the station

`services/reanalysis_backfill.py:119` `eligible_meteoswiss_configs` rejects any station without a
valid basin polygon. Its docstring says such stations are "logged and excluded — never silently
dropped". They are logged. They are also **promoted**, because the hold is a set difference:

```
held_out_ids = meteoswiss_eligible_ids - meteoswiss_backfilled     # onboarding.py:765
```

An *ineligible* station was never in `meteoswiss_eligible_ids`, so it cannot be in the difference.
The Plan 115b2 §2C hold — built to stop a station going live with a binding and zero forcing rows —
does not fire for the one case where the station has **no binding at all**. Being more broken puts a
station outside the guard.

On staging this is **2024 Branson**: `Ring Self-intersection[7.20127964026713 46.1757792259881]`, no
MeteoSwiss binding, zero `meteoswiss_*` rows, while the other 147 carry six products through
2026-09-06. It is `operational` and forecasting.

🔴 **Correction to the first draft.** It said Branson "runs on `camels-ch` forcing that ends
2020-12-31". `adapters/hybrid_reanalysis_factories.py` retires the CAMELS-CH tier (Plan 115b4 §5B) —
those rows "are simply never wired into this hybrid chain". Branson's 29 220 `camels-ch` rows are
never read by the operational resolver. The real condition is **no wired operational reanalysis
forcing at all**. The first draft's RED claim, that a climatology floor was "trained on stale
forcing", was false twice over: `models/climatology_fallback.py:45` declares empty
`past_dynamic_features` and `future_dynamic_features` and trains only from targets.

### Defect 2 — a status change leaves no audit trail

`store/station_store.py:320` `update_station_status` is a bare single-column `UPDATE`. It writes no
`audit_log` row and does not touch `updated_at`. So the field that decides whether the forecast cycle
touches a station at all (`flows/run_forecast_cycle.py:2382`) is unattributable. Establishing who set
it required extracting the `stations` table from two 25 GB nightly `pg_dump` archives.

That reconstruction proved the flip did **not** come from the application:

| dump (UTC) | `operational` | `onboarding` |
|---|---|---|
| 2026-09-02 02:00 | **37** | **111** |
| 2026-09-03 02:00 | **148** | 0 |

The 37 are exactly the stations of the three COMPLETED onboarding runs — the gate promoted precisely
what it should have. The other 111 flipped on 2026-09-02, twelve hours after the 08-29 run was
cancelled mid-training, with **no Prefect flow running at that instant** and **zero `audit_log` rows
on 09-02 or 09-03**. 110 share the identical `updated_at` `2026-09-02 15:15:51.638536+00`.

⭐ **A cheaper independent witness.** `pipeline_health.observation_ingest_fetch` records
`stations_polled` every five minutes: **37 at 15:15:00, 148 at 15:20:00** — the same minute, with no
dependence on dumps, audit rows or `updated_at`. It has been `warning` for 1631 consecutive runs
since, because one newly-added station returns `no_data`. Use this first next time.

For 106 of the 111 the write was benign. For five it asserted something untrue.

### The five that the gate could not have passed

`services/onboarding.py:1205` promotes a river station only when
`fetch_artifacts_by_status(climatology_fallback, ACTIVE, station_id)` is non-empty (the write is at
`:1207`); otherwise it appends an error and continues.

| station | artifacts (any status) | baselines | flow regime | forecasts, ever |
|---|---|---|---|---|
| **2041** Oberriet-Blatten | 1 (`nwp_rainfall_runoff`, 2026-09-04, from a *model* onboarding run) | 0 | 0 | 12 |
| **2116** Koblenz | **0** | 0 | 0 | **0** |
| **2392** Rheinau | **0** | 0 | 0 | **0** |
| **2615** Basel-Klingenthalfähre | **0** | 0 | 0 | **0** |
| **2623** Oberwald | **0** | 0 | 0 | **0** |

**The gate was correct. It was bypassed.**

### The root cause: QC never processed their history

Every absence above follows from one upstream fact, and the partition is exact:

| pre-2026 rows ever QC-processed | stations | have baselines |
|---|---|---|
| no | **5** — 2041, 2116, 2392, 2615, 2623 | **0** |
| yes | 143 | **143** |

No QC-passed observations → no `clim_baselines` → no `flow_regime_configs` → no trainable target →
no climatology floor → gate refuses.

**This splits the five into two groups needing opposite treatment:**

| station | pre-2026 rows | state | reading |
|---|---|---|---|
| **2041**, **2116** | 14 610 each | **all still `raw`** | data on disk, QC never touched it |
| **2615** | 9 497 | **all still `raw`** | data on disk, QC never touched it |
| **2392**, **2623** | **0** | — | no history at all; record starts 2026-09-02 |

For 2041, 2116 and 2615 the "data-poor" reading was **wrong** — comparable station 2009 carries
14 519 `qc_passed` + 91 `qc_suspect` alongside its raw rows. These three are likely recoverable from
data already on disk. Why Step 5 skipped stations that *had* rows is **not established**: the
per-station `except` appends to `errors`, a list only ever logged as a length, and the run's logs are
gone. That is the gap Plan 255 closes.

## Deferred, deliberately

A catchment lying partly outside Switzerland will also have no operational forcing, for geographic
rather than defective reasons. Both surface as `NO_OPERATIONAL_FORCING` and both are held by T1.
Telling them apart is a separate investigation the owner parked on 2026-09-08.

## Tasks

### T1 — an excluded station is held, and an already-operational one is actually acted on

**Outcome.** A station excluded from the MeteoSwiss binding by Plan 255 T1's typed exclusion is
withheld from model assignment, training and promotion — **and, when it is already `operational`, is
demoted rather than left alone.**

🔴 **The already-operational case is the whole point and the first draft missed it.**
`store/station_store.py:156` `update_station` does **not** write `station_status`, and Step 8's hold
branch (`onboarding.py:1182`) does `continue` — it never demotes. So re-onboarding Branson today
leaves it `operational`, keeps its assignments and ACTIVE artifacts, and
`run_forecast_cycle.py:2382` keeps selecting it. A fake-store test cannot reveal this:
`tests/fakes/fake_stores.py:1330` replaces the whole station object including status, so the fake
passes where production fails.

Four decisions this task must make explicitly, none of which the first draft made:

1. **Demote, suspend, or leave-and-report** an already-operational excluded station. Demotion stops
   observation ingest (`flows/ingest_observations.py:604` polls only `operational`) and BAFU history
   cannot be back-fetched (`adapters/hydro_scraper.py:109`) — so this decision has the same permanent
   cost as T5's. `suspended` exists in the status check constraint and may be the right answer.
2. **Existing assignments and ACTIVE artifacts** — Step 6/7 skips do not remove them. Deactivate or
   leave?
3. **Is the exclusion hold gated on `require_meteoswiss_backfill`?** The whole hold sits inside
   `if require_meteoswiss_backfill:` (`onboarding.py:764`), which defaults `False`, and the code
   comment says fake-store tests leave the held-out set empty. Inside the flag, a unit test shows no
   difference; outside it, behaviour changes for every caller. **Decide, and say which.**
4. **Which exclusion reasons hold.** `eligible_meteoswiss_configs` also rejects `no_basin_id`,
   dangling basins, wrong-typed and empty geometry. The hold runs *before* the weather-station branch
   at `onboarding.py:1189`, so a blanket union would also withhold weather and no-basin stations.
   Define an explicit reason × station-kind matrix.

⚠️ **The held-out message becomes false.** `onboarding.py:1183` hardcodes "MeteoSwiss reanalysis
binding exists but the per-station backfill produced zero rows"; for an excluded station no binding
exists. Same for the `station_held_out_meteoswiss_backfill` event at `:768`. Both must carry the
reason from Plan 255 T1.

**In.** `src/sapphire_flow/services/onboarding.py` (hold-set construction, Step 8 branch, messages),
`docs/spec/types-and-protocols.md`.
**Out.** No change to `_has_valid_geometry`. No geometry repair. No change to the climatology-floor
gate condition itself.

**Verification.** `uv run pytest tests/unit/services/test_onboarding.py -k excluded_station` **plus**
a test against the **production** store (not the fake) seeded with an already-`operational`
invalid-geometry station, asserting the decided outcome from (1). `tests/unit/services/test_onboarding.py:1347`
must still pass unmodified.

**Pre-change.** RED, and it must be the *already-operational* case: seed a station as `operational`
with an invalid polygon, run onboarding, and observe it still `operational` with its assignments
intact. A new-station test would pass for the wrong reason — new stations are already not promoted,
because they never reach the floor gate. Do **not** assert anything about CAMELS-CH training; that
claim was false.

### T2 — every station status change is audited, at the right layer

**Outcome.** Status changes are audited using the **existing** `AuditEventType.STATION_STATUS_CHANGE`
(`types/enums.py:361`), recording old status, new status and operator.

⛔ **Do not add `STATION_PROMOTED`.** It duplicates the existing member and cannot express demotion
or suspension — which T1 now needs.

⛔ **Do not put the audit inside `update_station_status`.** `PgStationStore.__init__` takes only a
connection — no clock, principal or audit store. Adding them would change the `StationStore`
Protocol, the fake store, integration tests and the e2e caller. `update_station_status` is also a
silent no-op for a missing station (`tests/integration/store/test_station_store.py`), so an audit
there would record a change that did not happen.

Instead add a service-layer helper beside `_write_station_with_audit` (`onboarding.py:428`), wrapped
in `audited_writer.transaction()` exactly as the assignment path does at `onboarding.py:981`.

⚠️ **Close the CLI hole.** `scripts/onboard.py:339` calls `onboard_from_camelsch` passing
`tenant_store` but **not** `principal`, `audit_log_store` or `audited_writer` — it takes the
unaudited direct path. Either wire it as `flows/onboard.py` does, or narrow the guarantee in writing
to the Prefect flow and document the CLI gap. "Every status change" with an unaudited entrypoint is
not every status change.

**In.** `src/sapphire_flow/services/onboarding.py`, `scripts/onboard.py`,
`docs/spec/types-and-protocols.md`, and the audit/logging conventions doc.
**Out.** No change to `store/station_store.py`. No retroactive audit rows for past promotions — that
history is genuinely unknown and inventing it would be worse than the gap. No change to the
`audit_log` append-only triggers.

**Verification.** Audit *contents* in a unit test; **rollback atomicity in
`tests/integration/db/test_slice_e_audit_atomicity.py` against real PostgreSQL** — a unit store test
cannot prove rollback through a real `AuditedWriter` transaction.

⚠️ The first draft cited `tests/unit/store/test_station_store.py`, which **does not exist** (it is
`tests/integration/store/`). That command would have errored rather than failed — indistinguishable
from a pending test.

**Pre-change.** RED: no `audit_log` row of any type follows a promotion today. The atomicity half
cannot be shown as a pre-change failure, because current code attempts no audit insert at all —
state that honestly rather than claiming a RED that cannot exist.

### T3 — why QC skipped three stations that had rows (read-only)

**Outcome.** A written finding establishing why Step 5 did not process the pre-2026 observations of
2041, 2116 and 2615, supported by queries. Read-only; no remediation.

Separating this from the fix is deliberate: the first draft folded cause analysis and a live
mutation into one task scoped as "whatever the cause analysis indicates", which is neither bounded
nor reviewable before a high-risk write.

**In.** A finding appended here.
**Out.** No write of any kind. No status change.

**Verification.** The finding names the mechanism and cites the queries that establish it, or states
plainly that the cause could not be determined from surviving evidence.

**Pre-change.** N/A — investigation task.

### T4 — remediate the three recoverable stations (blocked on T3)

**Outcome.** QC runs over the untouched `raw` history of 2041, 2116 and 2615, producing the
baselines, flow regime and climatology floor that follow — making all three genuinely operational.
No new observations required.

⛔ **Not implementable until T3 reports.** This task must be amended with an exact command and
station-specific pre/post SQL, then re-reviewed, before it runs.

**In.** To be specified by T3.
**Out.** No change to the 143 stations whose history is already QC'd. No re-QC of non-`raw` rows.

**Verification.** Station-specific, not aggregate: for each of 2041, 2116, 2615 assert QC-passed rows
> 0, `clim_baselines` > 0, a `flow_regime_configs` row, and an ACTIVE `climatology_fallback`
artifact — **plus** an assertion that the other 143 stations' artifact counts are unchanged.

⚠️ A bare "`operational`-without-floor count drops 5 → 2" is **not** sufficient: it would pass if the
wrong three stations acquired artifacts.

**Pre-change.** That count is 5 today, and the exact partition (143 QC'd all have baselines, 5
un-QC'd have none) is the discriminating evidence.

### T5 — 2392 and 2623: decided, no write

✅ **DECIDED 2026-09-08 — leave them `operational` so observation collection continues.** The owner's
words: *"we want to keep collecting station observations. leave operational if it costs nothing so we
can collect data."*

**The premise was checked, not assumed.** The cycle already emits `FORECAST_STATION_DARK` per station
per cycle (`flows/run_forecast_cycle.py:846`), naming these stations `critical` /
`all_models_failed` — 20 cycles running for 2116/2392/2615/2623, 8 for 2041 (the 00Z cycles, where
its single model also fails). Nothing pages on them: `ops/watchdog.py` probes only
`bafu_forecast_freshness`, `bafu_observation_freshness` and `forecast_freshness`. The premise holds.

Demotion was authorised and **correctly not executed**: `flows/ingest_observations.py:604` polls only
`operational` stations and BAFU serves real-time only (`adapters/hydro_scraper.py:109`), so demoting
would have stopped the only data source these two will ever have, permanently.

⚠️ Note what this means: the system detects this precisely every six hours and nobody sees it — the
same silence-looks-like-health shape as the July outage. Surfacing `FORECAST_STATION_DARK` belongs
with Plan 257's watchdog-coverage question, not here; 257 covers a dark *product*, this is a dark
*station*.

**In.** This decision record. **Out.** No database write.
**Verification / Pre-change.** N/A — decision task. Plan 255's report must still place both under
**Degraded** with `INSUFFICIENT_OBSERVATION_HISTORY`, so a deliberately wrong label stays visible
rather than becoming folklore.

### T6 — Branson's geometry (investigation only)

**Outcome.** A written finding: whether the invalid ring is present in the CAMELS-CH source geometry
or was introduced by our import, and whether Branson is one of the deferred cross-border cases. T1
already stops the silent exclusion, which is the urgent half.

**In.** A finding appended here.
**Out.** No `ST_MakeValid`, no geometry edit, no re-import — repairing a catchment boundary changes
every basin-averaged forcing value derived from it and every artifact trained on them. Its own plan.

**Verification.** The finding states, with the query that produced it, where the invalid ring
originates.

**Pre-change.** N/A — investigation task.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1", "T2"], "parallel": true },
    { "id": "phase-2", "tasks": ["T3", "T6"], "parallel": true },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5"] }
  ]
}
```
