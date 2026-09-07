---
status: DRAFT
created: 2026-09-03
plan: 243
title: Explicit gap markers for feeds that deliver unmarked absences
scope: STUB — not yet scoped. Give operational ingest the ability to record an expected-but-absent observation as a `MISSING` row, so a silent feed is a countable fact rather than an inference from missing rows. Driven by DHM's API, which withholds values its own QC has flagged and delivers an unmarked gap.
depends_on: [246]
blocks: []
source: 2026-09-03 — owner decision recorded in Plan 246 § Decision
---

# Plan 243 — explicit gap markers for feeds that deliver unmarked absences

## Status

**STUB — DRAFT, not scoped, not reviewed.** Created so that Plan 246's decision has somewhere to
live. It records why this work exists and what it must cover; it does not yet contain a design,
phases, tasks or exit gates. **Do not implement from this file.**

## Why this exists

`QcStatus.MISSING` is defined, its invariant is enforced in the domain type
(`types/observation.py:47-49`) and again as a database check constraint
(`db/metadata.py:543`) — and **no gauged feed ever synthesises one for a timestamp that simply did
not arrive.** Measured on the mac-mini (0.1.833) on 2026-09-03:
`SELECT count(*) FROM observations WHERE qc_status='missing'` returns **0**.

State the gap precisely, because it defines this plan's scope. Two producers do exist:
`services/component_derivation.py:116`, the calculated-station derivation path — which *is* reached
from operational ingest (`flows/ingest_observations.py:378-400,414-491`) and is dormant here only
because this deployment has no calculated stations — and `scripts/dhm_precip/observations.py:61-90`,
an offline research producer. Both emit `MISSING` when a *component or cell* is unusable. Neither
emits it when a sensor goes silent, which is the case this plan owns.

There is a structural reason it cannot work today: the `stations` table carries no expected reporting
cadence, so nothing in the system can distinguish *expected but not received* from *not expected*.

`docs/standards/wmo.md:169` nevertheless records this as an *Addressed in v0* gap against
WMO-168 Vol I. Plan 246 relabels that claim; this plan is what would make it true.

## What decided it

The argument originally offered against building it was that `pipeline_health` already covers the
need. **Review disproved that**: its rows are collector-level and run-level, carrying aggregate
counts and a newest timestamp (`flows/collect_bafu_observations.py:160-199`) or a run's failure
counts and station IDs (`flows/ingest_observations.py:175-240`) — not per-station expected timestamps
or ages, and distinct from the `OBSERVATION_FRESHNESS` check it was mistaken for
(`types/enums.py:193-217`). So the objection never held on its own terms.

**The owner confirmed on 2026-09-03 that DHM's API will deliver unmarked gaps.** DHM's Data
Management System flags a bad value `Erroneous` and then withholds it from the API, so what arrives is
an absence with no marker, indistinguishable from a telemetry outage or from a station that never
reported that parameter. That is the consumer the marker exists for. Full reasoning, including why
this is the low-risk option, is in `docs/plans/246-close-the-store-boundary-seam.md` § Decision — it is
not repeated here.

## ⛔ What this must not be mistaken for

A `MISSING` row records *that* a value was expected and did not arrive. It does **not** record *why*,
and it does not recover DHM's `Erroneous` flag: a DHM-rejected value and a dead telemetry link produce
an identical row. Recovering the reason needs DHM's API to serve the value together with its flag,
which is an open interface question with them
(`docs/requirements/dhm-data-formats-questions.md` § 5) and is out of scope here.

## What scoping must cover

- **The expected-reporting SCHEDULE, per station AND per parameter**, nullable, so a station
  without one materialises nothing and behaves exactly as today. Not merely a cadence: an interval
  cannot express DHM's manual gauges, which report at *fixed local hours* (08:00/12:00/16:00), so the
  schedule needs a form (interval vs fixed times), a timezone and phase — stations already carry a
  timezone (`types/station.py:34-45`) — and an activation period, so a station that started reporting
  in 2024 does not backfill gaps to 1981. Per parameter because the observations key includes
  parameter and `stations` already models parameters per station
  (`measured_parameters`, `forecast_targets`) — a single per-station cadence cannot express a gauge
  reporting level at 15 minutes and precipitation daily, which is exactly the DHM mixed-class case.
  WMO-49 Vol III covers observing-programme definitions and expected data frequency. DHM runs two
  station classes (automatic/telemetric at roughly 10–15 minutes, manual staff-gauge three times
  daily at fixed hours), and only the automatic ones are forecast.

- **Publication grace, and the correction horizon.** A value is not late merely because it has not
  arrived yet: DHM's publication delay, timestamp timezone, historical re-query behaviour,
  corrections and deletions, and source precedence are all still open in the questionnaire
  (`docs/requirements/dhm-data-formats-questions.md:81-85,123-125,156-159`). Materialising from a
  schedule alone, with no watermark and no recheck window, would manufacture false gaps and then
  handle their replacement wrongly. Scope a grace period before a slot is declared missing, and a
  horizon after which a gap is final.

- **Supersession — what happens when a value later arrives for a timestamp already materialised
  `MISSING`.** DHM republication, or a backfill of a value its DMS initially withheld, both hit this.
  **The key is `(station, timestamp, parameter, source)` — `source` included**
  (`store/observation_store.py:24-30`, `db/metadata.py:570-577`), which changes the analysis: a
  same-source arrival *updates* the existing row, while a distinct-source marker *coexists* with it
  (`store/observation_store.py:37-60`). So the real questions are whether a gap marker carries its
  own source, and what downstream deduplication then owes. An in-place update also has to be squared
  with the locked rule at `docs/architecture-context.md:280` ("raw values are never overwritten — QC
  is metadata on the observation, not a replacement") and with the `value is None ⟺ MISSING`
  invariant (`types/observation.py:47-49`, `db/metadata.py:543`). Decide before design whether a gap
  marker is a distinct source, a deletable placeholder, or versioned.
- **⛔ Where materialisation happens in the ingest control flow — the item that decides whether
  silence can be recorded at all.** A wholly silent fetch returns *before* any observation-processing
  step runs (`flows/ingest_observations.py:658-670`), which is exactly the case a gap marker exists
  for: if materialisation sits inside that path it will never fire when it matters most. And the
  feed cursor advances from the maximum observation timestamp with no filter on source or QC status
  (`flows/ingest_observations.py:627-636`, `store/observation_store.py:190-201`), so synthetic
  markers could advance the cursor and make the feed appear to have progressed. Scope: keep
  received-data cursor state separate from synthetic marker timestamps; place materialisation before
  or outside the no-data early return; settle inclusive/exclusive fetch-window semantics; and test
  both an entirely silent feed and a late arrival at a timestamp already marked.

- **⛔ A clean empty poll is not a failed fetch — decide this before any marker is written.** The
  domain already separates them: `RawObservation` carries a `failure_cause`
  (`types/observation.py:75,93`), the adapter treats `NO_DATA`, malformed responses and other
  acquisition errors as failed outcomes (`adapters/hydro_scraper.py:345,559`), and the ingest flow
  records failed station IDs before it takes the empty-result path
  (`flows/ingest_observations.py:650,658`). Without an explicit rule, a collector or upstream outage
  would manufacture a run of gap markers — the system inventing data loss that did not occur, which
  is worse than the silence it set out to record. Scope: whether markers are written only for clean
  successful polls, what happens on a partially failed batch, and the intended semantics of
  `NO_DATA`. Test both.

- **The bound on materialisation** — within the ingest window being processed, never an unbounded
  backfill over history.
- **A retention position on null rows**, including what happens to a station silent for months.
- **The migration**, additive-only per `docs/standards/cicd.md`.
- **Interaction with the QC pipeline** — believed already correct and already proven, since
  `_apply_frozen_sensor` (`services/qc.py:92`) breaks a run on a null value (`:119-124`) and the DHM
  precipitation work runs the production checker over a frame built exactly this way
  (`docs/plans/archive/173-fit-for-purpose-qc-mask.md` D2). Scoping should confirm rather than assume.

- **⛔ Downstream consumers beyond QC — the expensive-to-discover-late one.** Materialising null rows
  changes what every row-counting consumer sees, not just the checker. In scope for the survey:
  model input assembly and the FI `max_nan` gates (`models/nwp_regression.py:205,770,775`;
  `adapters/forecast_interface.py:924-970` counts missing values, `:1009-1021` rejects a station
  before `predict()` is ever called, and `:1063-1081` excludes affected stations on the group path,
  raising only if none remain — the model declarations set `max_nan=0`. **The consequence is a
  pre-predict `ModelOutputError` (station `PREDICT_FAILED`) or silent group-station exclusion, NOT a
  `ModelFailure`** — an FI `ModelFailure` is only converted after `predict()` returns (`:394`), which
  a gated station never reaches); coverage checks behind `AssignmentFailureCause.INSUFFICIENT_COVERAGE`;
  observation-staleness, which feeds `assess_input_quality` and therefore loops straight back into
  Plan 246's newly persisted `input_quality`; hindcast forcing; skill sample size; and Forecast Lab.
  For each path, confirm whether a materialised null is seen as present-but-NaN or as absent.
- **Whether `pipeline_health` and gap rows now overlap**, and if so which is authoritative for what.
  Two mechanisms recording station silence is defensible; two mechanisms disagreeing is not.
