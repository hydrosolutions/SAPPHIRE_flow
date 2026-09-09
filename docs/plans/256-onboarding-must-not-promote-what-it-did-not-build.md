---
status: DRAFT
created: 2026-09-08
plan: 256
title: Onboarding must not promote what it did not build — hold excluded stations at promotion, and audit the status transitions onboarding writes
scope: Stop onboarding PROMOTING a station whose operational forcing it withheld, and make the status transitions ONBOARDING WRITES attributable. Narrowed on owner decision 2026-09-08 — this plan does NOT demote stations already operational, because every non-operational status stops observation ingest and BAFU history cannot be back-fetched. Explicitly NOT a demotion of Branson or any live station, NOT the QC remediation (Plan 260), NOT geometry repair, NOT model-onboarding promotion, NOT an audit boundary that captures direct SQL writes (see the limit stated below).
depends_on: [255]
blocks: [260]
source: 2026-09-08 — a read-only diagnosis of the mac-mini staging host. Five stations carry `operational` although the promotion gate could not have passed them; one station was excluded from operational forcing by an invalid polygon and promoted anyway. Revised twice after independent Codex passes (4 blockers, then 6 further blockers including that the first revision still promised a demotion it could not deliver).
---

# Plan 256 — onboarding must not promote what it did not build

## Status

**DRAFT — revised after two independent review rounds.** Awaiting owner READY.

⚠️ **High-risk.** T1 changes production promotion behaviour. No task in this plan writes to staging.

⚠️ **Depends on Plan 255** — now scoped to exactly the typed eligibility exclusions this plan
consumes. The reporting half became **Plan 259**.

### Scope narrowed by owner decision, 2026-09-08

The first two drafts promised that an excluded station "stays `onboarding`". For a **new** station
that is achievable. For an **already-operational** one it is not, and pursuing it would cause
permanent harm:

- `store/station_store.py:156` `update_station` never writes `station_status`, and Step 8's hold
  branch (`onboarding.py:1182`) only `continue`s — re-onboarding cannot demote.
- Adding a demotion would stop the station's observation ingest: `flows/ingest_observations.py:604`
  polls **only** `operational` stations, and **every** other status value — `onboarding`,
  `suspended`, `decommissioned` — stops collection. BAFU serves real time only
  (`adapters/hydro_scraper.py:109`), so the gap would be **permanent and unrecoverable**.

🔑 **`station_status` is overloaded**: it gates forecasting *and* ingest, so it cannot express "keep
collecting, don't forecast". Decoupling them is a separate change and is **not** attempted here.

**Owner decision:** leave already-operational excluded stations operational, and report them. So this
plan prevents the **next** wrong promotion; it does not repair the existing one. Branson stays
operational and keeps collecting, with Plan 259's report and the existing `FORECAST_STATION_DARK`
record carrying the truth. The title is narrowed accordingly.

## Why this exists

### Defect 1 — an eligibility exclusion withholds forcing without withholding the station

`services/reanalysis_backfill.py:119` rejects any station without a valid basin polygon. Those
stations are logged — and promoted, because the hold is a set difference:

```
held_out_ids = meteoswiss_eligible_ids - meteoswiss_backfilled     # onboarding.py:765
```

An *ineligible* station was never in `meteoswiss_eligible_ids`, so it cannot be in the difference.
The Plan 115b2 §2C hold — built to stop a station going live with a binding and zero forcing rows —
cannot fire for the one case where there is **no binding at all**. Being more broken puts a station
outside the guard.

On staging this is **2024 Branson**: `Ring Self-intersection[7.20127964026713 46.1757792259881]`, no
MeteoSwiss binding, zero `meteoswiss_*` rows, while the other 147 carry six products through
2026-09-06.

🔴 **Correction.** An earlier draft said Branson "runs on `camels-ch` forcing that ends 2020-12-31".
`adapters/hybrid_reanalysis_factories.py:9` retires the CAMELS-CH tier — those rows are never wired.
The real condition is **no wired operational reanalysis forcing at all**. The earlier RED claim about
"a climatology floor trained on stale forcing" was false twice over:
`models/climatology_fallback.py:45` declares empty dynamic features and trains only from targets
(`:62`).

### ⛔ A limit this plan does NOT overcome

The staging incident was a **direct database write**. No service-layer helper can observe one, and
this plan does not add a database-level trigger. T2 therefore makes *onboarding-written* transitions
attributable — which is what turns a future onboarding promotion into evidence — and leaves
`store/station_store.py:320` an unaudited primitive. A direct `UPDATE` would still be invisible.
Closing that would mean an audit trigger on `stations`, a much larger commitment than this plan, and
it is deliberately not attempted. The title and scope are narrowed to match.

### Defect 2 — a status change leaves no audit trail

`store/station_store.py:320` `update_station_status` is a bare single-column `UPDATE` — no
`audit_log` row, and it does not touch `updated_at`. So the field that decides whether the forecast
cycle touches a station (`flows/run_forecast_cycle.py:2382`) is unattributable. Establishing who set
it required extracting `stations` from two 25 GB nightly `pg_dump` archives:

| dump (UTC) | `operational` | `onboarding` |
|---|---|---|
| 2026-09-02 02:00 | **37** | **111** |
| 2026-09-03 02:00 | **148** | 0 |

The 37 are exactly the stations of the three COMPLETED onboarding runs. The other 111 flipped on
2026-09-02 with **no Prefect flow running** and **zero `audit_log` rows on 09-02 or 09-03**; 110
share the identical `updated_at` `2026-09-02 15:15:51.638536+00`.

⭐ **A cheaper witness for next time.** `pipeline_health.observation_ingest_fetch` records
`stations_polled` every five minutes: **37 at 15:15:00, 148 at 15:20:00** — same minute, no
dependence on dumps, audit rows or `updated_at`. It has been `warning` for 1631 consecutive runs
since, because one newly-added station returns `no_data`.

For 106 of the 111 the write was benign. For five it asserted something untrue: **2041, 2116, 2392,
2615, 2623** have no ACTIVE `climatology_fallback` artifact, which `onboarding.py:1205` requires
before the promotion write at `:1207`. Four have no artifact of any model, ever.

**Root cause, and it is not this plan's to fix.** QC never processed those five stations' pre-2026
observations, and the partition is exact: of 148 stations, the 143 whose pre-2026 rows were
QC-processed all have baselines; the 5 whose rows were not have none. 2041, 2116 and 2615 still hold
14 610 / 14 610 / 9 497 rows in `raw`; 2392 and 2623 have no pre-2026 history at all. **Plan 260**
owns the remediation, blocked on T3 below.

## Tasks

### T1 — an excluded station is not promoted

**Outcome.** A **river or lake** station excluded from the MeteoSwiss binding for one of four
holding reasons (`GEOMETRY_INVALID`, `GEOMETRY_EMPTY`, `GEOMETRY_WRONG_TYPE`, `BASIN_NOT_FOUND`) is
withheld from model assignment, training and the promotion write — so it is never *newly* promoted
without operational forcing. `NO_BASIN_ID` does not hold, and weather stations are never held. A
station already `operational` is left alone and reported (owner decision above).

**The four decisions, locked:**

| decision | resolved |
|---|---|
| already-`operational` excluded station | **left operational**, recorded with `INVALID_BASIN_GEOMETRY` in its Plan 259 outcome. No status write. |
| its existing assignments and ACTIVE artifacts | **untouched** — removing them would silently stop forecasts for a station the owner chose to leave running |
| gated on `require_meteoswiss_backfill`? | **yes** — the exclusion hold joins the existing block at `onboarding.py:764`. The production path sets the flag `True` (`flows/onboard.py`); fake-store unit tests that leave it `False` keep today's behaviour exactly, so no existing test changes meaning |
| which reasons hold, for which station kinds | river/lake stations: `GEOMETRY_INVALID`, `GEOMETRY_EMPTY`, `GEOMETRY_WRONG_TYPE`, `BASIN_NOT_FOUND`. **`NO_BASIN_ID` does not hold** — a station legitimately without a basin is not a forcing failure. **Weather stations are never held**: they are forcing sources, not forecast targets, and the hold runs before the weather branch at `onboarding.py:1189` |

⚠️ **The held-out message becomes false and must carry the reason.** `onboarding.py:1183` hardcodes
"MeteoSwiss reanalysis binding exists but the per-station backfill produced zero rows"; for an
excluded station no binding exists. Same for the event at `:768`.

**In.** `src/sapphire_flow/services/onboarding.py` (hold-set construction, Step 8 branch, both
messages); `docs/touchpoint-maps.md:194`; `docs/v0-scope.md:70`.
**Out.** No change to `_has_valid_geometry` or to the climatology-floor gate condition. No geometry
repair. **No explicit demotion or status write** — the behavioural change is that a newly-excluded
station never *reaches* the promotion write, not that any station's status is rewritten. No live
station is demoted.

**Verification.**
`uv run pytest tests/unit/services/test_onboarding.py::TestExcludedStationHold` — with
`require_meteoswiss_backfill=True`, a **new** river station with a self-intersecting polygon ends
`ONBOARDING`, absent from `model_assignments`, carrying `INVALID_BASIN_GEOMETRY` (Plan 259's record); a **weather**
station with the same polygon is still promoted; and a station with `NO_BASIN_ID` is unaffected.
`tests/unit/services/test_onboarding.py:1347` passes unmodified.

**Pre-change.** RED: the new-river-station case ends `OPERATIONAL` today, because the exclusion
removes it from `meteoswiss_eligible_ids` and therefore from `held_out_ids`.

⚠️ **The fixture must pin three prerequisites or the RED fails for the wrong reason.** Step 8 promotes
a river only when `artifact_store is not None` (`onboarding.py:1199`) **and** an ACTIVE
climatology floor exists (`:1205`); Steps 6 and 7 are skipped entirely unless their optional model
infrastructure is wired (`:911`, `:1014`). So the fixture must supply the artifact store, seed the
active floor, and wire the model stores — otherwise the station fails to be promoted for want of a
floor, which is *today's correct behaviour*, not the defect. Assert assignment/training skipping with
a training spy rather than by absence, for the same reason.

Do **not** assert anything about CAMELS-CH training — that claim was false. Do not use an
already-operational station as the RED: it would pass for the wrong reason, since this plan
deliberately no longer demotes.

### T2 — every status transition is audited, at the right layer

**Outcome.** Status transitions written by onboarding are audited using the **existing**
`AuditEventType.STATION_STATUS_CHANGE` (`types/enums.py:361`,
`docs/spec/types-and-protocols.md:313`), recording old status, new status and operator.

⛔ **Do not add `STATION_PROMOTED`** — it duplicates the existing member and cannot express demotion
or suspension.
⛔ **Do not put the audit in `update_station_status`.** `PgStationStore.__init__` takes only a
connection — no clock, principal or audit store — and the method is a silent no-op for a missing
station, so an audit there would record a change that did not happen. Add a service-layer helper
beside `_write_station_with_audit` (`onboarding.py:420`), wrapped in `audited_writer.transaction()`
as the assignment path is at `onboarding.py:981`.

**Two behaviours specified, not left open:**

- **The CLI is wired**, not exempted. `scripts/onboard.py:339` passes `tenant_store` but not
  `principal`, `audit_log_store` or `audited_writer`, so it takes the unaudited path. It gets the
  same wiring as `flows/onboard.py`.
- **No event when `old == new`.** Step 8 performs its promotion write unconditionally, so a re-run
  over an already-operational station would otherwise emit a false "status change" every time.

**In.** `src/sapphire_flow/services/onboarding.py`, `scripts/onboard.py`,
`tests/unit/scripts/test_onboard_script.py`, `docs/spec/types-and-protocols.md`,
`docs/standards/logging.md`.
**Out.** No change to `store/station_store.py` or the `StationStore` Protocol. No retroactive audit
rows — that history is unknown and inventing it would be worse than the gap. No change to the
`audit_log` append-only triggers.

**Verification.**
`uv run pytest tests/unit/services/test_onboarding.py::TestStatusChangeAudit` — a promotion writes
exactly one `STATION_STATUS_CHANGE` naming both statuses; a re-run over an already-operational
station writes **none**; and
`uv run pytest tests/integration/db/test_slice_e_audit_atomicity.py::TestStatusChangeRollback`
against real PostgreSQL — an audit-insert failure leaves `station_status` unchanged. A unit store
test cannot prove rollback through a real `AuditedWriter`.

And the CLI wiring is verified, not merely required:
`uv run pytest tests/unit/scripts/test_onboard_script.py::TestAuditWiring` — the call to
`onboard_from_camelsch` receives a principal, an audit log store and an audited writer. Today it
passes none of them (`scripts/onboard.py:339`), so a requirement with no test would silently not
happen.

⚠️ An earlier draft cited `tests/unit/store/test_station_store.py`, which **does not exist** (it is
`tests/integration/store/`). That command would have errored rather than failed — indistinguishable
from a pending test.

**Pre-change.** RED: **no `STATION_STATUS_CHANGE` row** follows a promotion today. Assert that
specific type — not "no audit row of any type", which is false, since station registration already
writes `STATION_ONBOARDED` (`onboarding.py:420`). The rollback half has no pre-change failure to
show, because current code attempts no audit insert at all; state that rather than invent a RED.

### T3 — why QC skipped three stations that had rows (read-only)

**Outcome.** A written finding establishing why Step 5 did not process the pre-2026 observations of
2041, 2116 and 2615, with the queries that establish it — or a plain statement that the cause cannot
be determined from surviving evidence.

Separating investigation from remediation is deliberate: an earlier draft folded them into one task
scoped as "whatever the cause analysis indicates", which is neither bounded nor reviewable before a
live write. **Plan 260** owns the remediation and is blocked on this.

**In.** A finding appended to this plan.
**Out.** No write of any kind. No status change. No QC re-run.

**Verification.** A bounded checklist, each with its query and result recorded in the finding:
(a) do the three stations' pre-2026 rows carry a `parameter` matching their `forecast_targets`?
(b) do their timestamps fall inside the run's `start_utc`/`end_utc` window?
(c) does `errors` in any surviving run record name them?
(d) do they differ from a QC-processed control station (2009) in `station_kind`, `gauging_status`,
`water_level_datum_masl` or `measured_parameters`?

An inconclusive result is acceptable **only** if it records every query, its result, and the
hypotheses thereby eliminated. "Could not be determined" with no evidence is not an outcome — an
earlier draft admitted this task could not fail, which `docs/workflow.md:20` forbids.

**Pre-change.** N/A — investigation task.

### T4 — Branson's geometry (read-only)

**Outcome.** A written finding on **provenance only**: whether the invalid ring is present in the
CAMELS-CH source geometry or was introduced by our import.

⛔ **Whether Branson is a cross-border catchment is explicitly out of scope** — an earlier draft had
this task investigating the very question the plan's scope excludes. That belongs to the deferred
outside-Switzerland investigation.

**In.** A finding appended to this plan.
**Out.** No `ST_MakeValid`, no geometry edit, no re-import — repairing a catchment boundary changes
every basin-averaged forcing value derived from it and every artifact trained on them. Its own plan.

**Verification.** The finding states, with the query that produced it, where the invalid ring
originates.

**Pre-change.** N/A — investigation task.

## Decision record — 2392 and 2623

✅ **2026-09-08: leave them `operational` so observation collection continues.** Owner: *"we want to
keep collecting station observations. leave operational if it costs nothing so we can collect data."*

The premise was checked. `flows/run_forecast_cycle.py:846` already emits `FORECAST_STATION_DARK` per
station per cycle, naming these stations `critical` / `all_models_failed` — 20 cycles running for
2116/2392/2615/2623, 8 for 2041. Nothing pages on them: `ops/watchdog.py` probes only
`bafu_forecast_freshness`, `bafu_observation_freshness` and `forecast_freshness`.

⚠️ Which means the system detects this precisely every six hours and nobody sees it — the same
silence-looks-like-health shape as the July outage. Surfacing `FORECAST_STATION_DARK` belongs with
Plan 257's watchdog-coverage question; 257 covers a dark *product*, this is a dark *station*.

Under Plan 259's derivation rule both stations report `NO_CLIMATOLOGY_FLOOR`.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T2"] },
    { "id": "phase-2", "tasks": ["T1"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3", "T4"], "parallel": true }
  ]
}
```
