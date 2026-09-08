---
status: DRAFT
created: 2026-09-08
plan: 256
title: Onboarding must not promote what it did not build — hold on invalid geometry, audit every status change, reconcile the mislabelled fleet
scope: Close the two paths by which a station reaches `station_status = 'operational'` without the substance the status claims — a basin polygon that fails validation silently downgrading the station to stale forcing, and a promotion that leaves no audit trail. Then reconcile the five staging stations currently mislabelled: three are recoverable from history already on disk that QC never processed, two have no history at all and must not simply be demoted (demotion stops their ingest, and BAFU history cannot be back-fetched). Explicitly NOT a change to what onboarding trains, NOT the catchment-outside-Switzerland forcing question (deferred, see below), NOT model-onboarding promotion.
depends_on: [255]
blocks: []
source: 2026-09-08 — a read-only forensic diagnosis of the mac-mini staging host. Five stations carry `operational` although the promotion gate in `services/onboarding.py:1207` could not have passed them; one station lost operational forcing to an invalid polygon and was promoted anyway.
---

# Plan 256 — onboarding must not promote what it did not build

## Status

**DRAFT.** Awaiting owner READY. Depends on Plan 255 for the outcome vocabulary.

⚠️ **High-risk.** T1 changes production promotion behaviour and T3 re-runs QC over staging data. Per
`docs/workflow.md` §High-risk work this warrants one additional owner-commissioned review beyond the
standard Claude + Codex passes.

🔴 **A demotion was authorised on 2026-09-08 and then NOT executed**, because measuring the
consequence first showed it would cause permanent harm: `flows/ingest_observations.py:604` polls only
`operational` stations, and BAFU LINDAS cannot be back-fetched, so demoting 2392 and 2623 would stop
the only data source they have with no way to recover the gap. The same measurement showed 2041, 2116
and 2615 are not data-poor at all — their history is on disk and un-QC'd. T3/T3b carry both findings.
No write was made to staging in that session.

## Why this exists

There are two independent defects, found together.

### Defect 1 — an invalid basin polygon silently downgrades a station

`services/reanalysis_backfill.py::eligible_meteoswiss_configs` excludes any station whose basin fails
`_has_valid_geometry` (empty, or not `geometry.is_valid`). Its docstring says such stations are
"logged and excluded — never silently dropped". They are logged. They are also **promoted**, because
the hold set is computed as:

```
held_out_ids = meteoswiss_eligible_ids - meteoswiss_backfilled
```

An *ineligible* station was never in `meteoswiss_eligible_ids`, so it cannot be in the difference. The
Plan 115b2 §2C hold — built precisely to stop a station going live with a binding and zero forcing
rows — does not fire for the one case where the station has **no binding at all**. The station is
promoted with whatever forcing it happens to have.

On staging this is **2024 Branson**: polygon invalid
(`Ring Self-intersection[7.20127964 46.1757792]`), no MeteoSwiss binding, and `camels-ch` forcing
that ends **2020-12-31** — five and a half years stale — while the other 147 stations carry six
MeteoSwiss products through **2026-09-06**. Branson is `operational` and forecasting.

The failure mode is worse than the missing data: a station that is *held* is visibly incomplete,
whereas a station that is *downgraded* looks identical to a healthy one from every surface we have.

### Defect 2 — promotion leaves no audit trail

`store/station_store.py::update_station_status` is a bare single-column `UPDATE`. It writes no
`audit_log` row and does not touch `updated_at`. `AuditEventType` has `STATION_ONBOARDED`,
`MODEL_ASSIGNED`, `MODEL_PROMOTED` and `MODEL_REJECTED` — there is no station-promotion event.

So a station's transition to `operational` — the single field that decides whether the forecast cycle
touches it at all (`flows/run_forecast_cycle.py:2381`) — is unattributable. Establishing who set it,
on staging, required extracting the `stations` table from two 25 GB nightly `pg_dump` archives to
bracket the change between them.

That reconstruction proved the flip did **not** come from the application:

| dump (UTC) | `operational` | `onboarding` |
|---|---|---|
| 2026-09-02 02:00 | **37** | **111** |
| 2026-09-03 02:00 | **148** | 0 |

The 37 are exactly the stations of the three COMPLETED onboarding runs — the gate promoted precisely
what it should have. The other 111 flipped on 2026-09-02, twelve hours after the 08-29 run was
cancelled mid-training, with **no Prefect flow running at that instant** and **zero `audit_log` rows
on 09-02 or 09-03**. 110 of them share the identical `updated_at`
`2026-09-02 15:15:51.638536+00` — one transaction.

⭐ **A third, independent witness pins the same minute.** The observation-ingest health check records
how many stations it polled, every five minutes:

| checked_at | status | stations_polled |
|---|---|---|
| 2026-09-02 15:15:00 | ok | **37** |
| 2026-09-02 15:20:00 | warning | **148** |

The fleet the ingest flow sees jumped from 37 to 148 in the interval containing
`15:15:51.638536`, and `observation_ingest_fetch` has been `warning` for 1631 consecutive runs since
— one of the newly-added stations returns `no_data`. That check has minute resolution and no
dependence on the dumps, the audit log or `updated_at`.

For 106 of the 111 that write was benign; they would have passed the gate. For five it asserted
something untrue.

### The five that the gate could not have passed

`services/onboarding.py:1207` promotes a non-weather station only when
`fetch_artifacts_by_status(climatology_fallback, ACTIVE, station_id)` is non-empty, otherwise it
appends an error and continues. These five have no such artifact — and four have no artifact of any
model, ever:

| station | artifacts (any status) | baselines | flow regime | forecasts, ever |
|---|---|---|---|---|
| **2041** Oberriet-Blatten | 1 (`nwp_rainfall_runoff`, added 2026-09-04 by a *model* onboarding run) | 0 | 0 | 12 |
| **2116** Koblenz | **0** | 0 | 0 | **0** |
| **2392** Rheinau | **0** | 0 | 0 | **0** |
| **2615** Basel-Klingenthalfähre | **0** | 0 | 0 | **0** |
| **2623** Oberwald | **0** | 0 | 0 | **0** |

**The gate was correct. It was bypassed.**

### ⭐ The root cause, found 2026-09-08: QC never processed their history

Every absence above follows deterministically from one upstream fact. Step 5 QC never ran over these
five stations' pre-2026 observations, and the correspondence is exact:

| pre-2026 rows ever QC-processed | stations | have baselines |
|---|---|---|
| no | **5** — 2041, 2116, 2392, 2615, 2623 | **0** |
| yes | 143 | **143** |

No QC-passed observations → no `clim_baselines` → no `flow_regime_configs` → no trainable target →
no `climatology_fallback` floor → gate refuses. One cause, five symptoms, 148 stations partitioned
perfectly.

**This splits the five into two groups that need opposite treatment:**

| station | pre-2026 rows | status of those rows | reading |
|---|---|---|---|
| **2041** Oberriet-Blatten | 14 610 | **all still `raw`** | data exists, QC never touched it |
| **2116** Koblenz | 14 610 | **all still `raw`** | data exists, QC never touched it |
| **2615** Basel-Klingenthalfähre | 9 497 | **all still `raw`** | data exists, QC never touched it |
| **2392** Rheinau | **0** | — | no history at all; record starts 2026-09-02 |
| **2623** Oberwald | **0** | — | no history at all; record starts 2026-09-02 |

For 2041, 2116 and 2615 the earlier "data-poor" reading was **wrong**. Their history is present and
sitting untouched in `raw`; a comparable station (2009) carries 14 519 `qc_passed` + 91 `qc_suspect`
alongside its raw rows. These three are very likely recoverable from data already on disk, with no
new observations required.

For 2392 and 2623 there genuinely is no history — Step 3 stored nothing for them either.

Why QC skipped three stations that had rows is **not established**. Step 5 wraps each station in
`try/except` and appends to `errors`, a list that is only ever logged as a length. The run's logs are
gone. That is precisely the gap Plan 255 exists to close.

## Deferred, deliberately

**A catchment lying partly or wholly outside Switzerland will also have no operational forcing**, for
a reason that is geographic rather than a defect. This plan does not attempt to distinguish
"invalid polygon" from "valid polygon outside the MeteoSwiss domain" — both surface as
`NO_OPERATIONAL_FORCING` in the Plan 255 report, and both are held by T1. Telling them apart, and
deciding what forcing a cross-border catchment should get, is a separate investigation the owner has
explicitly parked (2026-09-08).

## Tasks

### T1 — an ineligible station is held, not downgraded

**Outcome.** A station excluded from the MeteoSwiss binding for want of a valid basin polygon is
added to `held_out_ids`, so the existing Step 6 / Step 7 / Step 8 hold applies to it unchanged: no
model assignment, no training, no promotion. The station stays `onboarding` with a recorded reason
until its geometry is fixed and onboarding is re-run.

The fix is to the *set arithmetic*, not to the gate: the hold set becomes eligible-minus-backfilled
**plus** the excluded set, so a station is held whether its forcing is absent because the fetch
returned nothing or because it was never eligible to fetch.

⚠️ `eligible_meteoswiss_configs` currently returns only the eligible configs and drops the exclusion
reasons on the floor. It must return both, which is a signature change with two call sites —
`services/onboarding.py` and `bind_meteoswiss_reanalysis_fleet` in the same module. The fleet-binding
caller must keep its present behaviour exactly (it binds what is eligible and reports counts); it is
not a promotion path.

**In.** `src/sapphire_flow/services/reanalysis_backfill.py`,
`src/sapphire_flow/services/onboarding.py` (hold-set construction only).
**Out.** No change to `_has_valid_geometry` itself — the predicate is correct and caught Branson. No
geometry repair, no `ST_MakeValid`. No change to the Step 8 gate condition.

**Verification.** `uv run pytest tests/unit/services/test_onboarding.py -k invalid_geometry` — a
fake-store run over two stations, one with a self-intersecting polygon, ends with the invalid one
still `ONBOARDING`, absent from `model_assignments`, and carrying `INVALID_BASIN_GEOMETRY` in its
Plan 255 outcome; the valid one is `OPERATIONAL`.

**Pre-change.** RED: that same test on today's code shows the invalid-geometry station reaching
`OPERATIONAL` **with a climatology floor trained on stale forcing** — which is the defect exactly, and
distinguishes it from a station that merely fails for want of an artifact. Use a self-intersecting
ring, not an empty polygon: an empty polygon would also fail several unrelated downstream paths and
would not isolate the hold-set arithmetic.

### T2 — every station status change is audited

**Outcome.** `AuditEventType` gains `STATION_PROMOTED`, and `update_station_status` writes an audit
row carrying the station id, the previous and new status, and the run principal. A status change
becomes attributable without reading a database dump.

Follow the `MODEL_ASSIGNED` precedent in `services/onboarding.py:970` — on the production path the
status write and its audit row go through `audited_writer.transaction()` so a failed audit insert
rolls the status change back, and the direct AUTOCOMMIT path stays for injected-store tests.

**In.** `src/sapphire_flow/types/enums.py`, `src/sapphire_flow/store/station_store.py`,
`src/sapphire_flow/services/onboarding.py` (the two `update_station_status` call sites at 1191 and
1207).
**Out.** No retroactive audit rows for past promotions — the history is genuinely unknown and
inventing it would be worse than the gap. No change to the `audit_log` append-only triggers.

**Verification.** `uv run pytest tests/unit/store/test_station_store.py -k promoted_audit` — promoting
a station writes exactly one `STATION_PROMOTED` row naming both statuses; and an audit-store failure
leaves `station_status` unchanged.

**Pre-change.** RED: on today's code the audit assertion finds zero rows of any type after a
promotion. To prove the *transactional* half rather than only the missing-row half, the failure-path
assertion must show the status committed while the audit insert failed — that is the behaviour the
`audited_writer` wrapping exists to prevent.

### T3 — recover the three stations whose history was never QC'd

**Outcome.** QC is run over the untouched `raw` pre-2026 observations of **2041**, **2116** and
**2615**, and the baselines, flow regime and `climatology_fallback` floor that follow are produced —
making all three genuinely operational rather than nominally so. This is a re-run of work onboarding
was supposed to do, over data already on disk; it needs no new observations.

Determine first **why** Step 5 skipped them. Fixing the symptom without the cause leaves the next
onboarding run free to repeat it silently on different stations.

**In.** Whatever the cause analysis indicates — most likely a targeted re-onboarding of these three
station codes.
**Out.** No change to the 143 stations whose history is already QC'd. No re-QC of rows that already
carry a non-`raw` status.

**Verification.** After the run, the count of stations that are `operational` **and** lack an active
`climatology_fallback` artifact drops from 5 to 2 — asserted by query against the artifact table the
gate itself reads, not by any row count of the change.

**Pre-change.** That query returns **5** today. The discriminating pre-change evidence is the exact
partition measured on 2026-09-08: every one of the 143 stations with QC-processed history has
baselines, and every one of the 5 without has none — so a fix that produces baselines for these three
without touching the other 143 is proven by the same query.

### T3b — 2392 and 2623: a status decision, not a repair

**Outcome.** The owner decides what to do with the two stations that have **no history at all** —
their entire record begins 2026-09-02.

🔴 **Demotion is NOT the safe default here, contrary to this plan's first draft.**
`flows/ingest_observations.py:604` polls only stations whose `station_status` is `operational`, and
BAFU LINDAS serves **real-time only** — historical series cannot be back-fetched
(`memory/project_bafu_lindas_realtime_only.md`). Demoting these two therefore stops the only source
of data they will ever have, and the resulting gap is **permanent and unrecoverable**. The honest
label would be bought with the very history they need to become viable.

✅ **DECIDED 2026-09-08 — leave them `operational` so observation collection continues.** The owner's
words: *"we want to keep collecting station observations. leave operational if it costs nothing so we
can collect data."* Accumulating the record is worth more than a correct label on two stations that
produce no forecasts either way. Revisit once ingest is decoupled from `station_status`, or once they
have enough history to onboard properly — whichever comes first.

**The "costs nothing" premise was checked, not assumed.** The forecast cycle already emits a
`FORECAST_STATION_DARK` record per station per cycle
(`flows/run_forecast_cycle.py:846`), and on staging it names exactly these stations,
`status = critical`, `reason = all_models_failed`, 20 cycles running:

| station | dark records | |
|---|---|---|
| 2116, 2392, 2615, 2623 | 20 each | every cycle |
| 2041 | 8 | the 00Z cycles, where its single model also fails |

So the cost of leaving them operational is 4–5 `critical` rows per cycle. **Nothing pages on them** —
`ops/watchdog.py` probes only `bafu_forecast_freshness`, `bafu_observation_freshness` and
`forecast_freshness`. The premise holds, and the decision stands.

⚠️ But note what that means: **the system detects this precisely, every six hours, and no one sees
it.** That is the same silence-looks-like-health shape as the July outage. Surfacing
`FORECAST_STATION_DARK` is small and belongs with Plan 257's watchdog-coverage question rather than
here — 257 covers a dark *product*, this is a dark *station*, and the two should not collide.

**In.** A written decision recorded here. Nothing else.
**Out.** No database write. No status change to 2392 or 2623.

**Verification.** N/A — decision task, recorded above. The Plan 255 report must still place both
stations under **Degraded** with `INSUFFICIENT_OBSERVATION_HISTORY`, so the label being deliberately
wrong stays visible rather than becoming folklore.

**Pre-change.** N/A.

### T4 — Branson's geometry

**Outcome.** The owner is given what is needed to decide 2024 Branson: the source of the invalid
polygon (CAMELS-CH delivery vs. our import path), whether the self-intersection is repairable, and
whether Branson's catchment is one of the cross-border cases parked above. No repair is performed
under this plan — T1 already stops the silent downgrade, which is the urgent half.

**In.** A written finding appended to this plan.
**Out.** No `ST_MakeValid`, no geometry edit, no re-import. Repairing a catchment boundary changes
every basin-averaged forcing value derived from it and every artifact trained on those values; that
is its own plan.

**Verification.** The finding states, with the query that produced it, whether the invalid ring is
present in the CAMELS-CH source geometry or was introduced downstream.

**Pre-change.** N/A — investigation task.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1", "T2"], "parallel": true },
    { "id": "phase-2", "tasks": ["T3", "T3b"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-1"] }
  ]
}
```
