---
status: READY
created: 2026-09-03
plan: 253
title: Quality signals that are computed and then dropped at the store boundary
scope: Persist and serve the input-quality assessment Plan 023 specified; quality-control the pooled combination forecast; state the `MISSING` claim truthfully until its producer lands; and re-verify the WMO compliance table against the running system. Explicitly NOT the `MISSING` producer itself (owner decided 2026-09-03 to build it — that is Plan 250), NOT the historical water-level QC gap, NOT Stage 2 QC, NOT any new QC rule.
depends_on: []
blocks: [250]
source: 2026-09-02/03 — a live audit of the mac-mini (0.1.833) against `docs/standards/wmo.md`, prompted by a DHM counterpart discussion
---

# Plan 253 — closing the store-boundary seam

## Status

**READY — owner confirmed 2026-09-04.** Every defect below was measured against the running staging deployment
(SAPPHIRE Flow 0.1.833) on 2026-09-02 and 2026-09-03, not inferred from reading code. The measured
evidence is quoted inline so a reviewer can re-run it.

## Review scope — read this before reviewing

**This review covers Plan 253 AND Plan 250 together**
(`docs/plans/250-explicit-gap-markers-for-unmarked-feeds.md`). They were split on 2026-09-03 from a
single owner decision and are meaningless apart: 253 records the decision and relabels the
documentation; 250 is the work that decision authorises. The review workflow takes one plan path, so
the joint scope is declared here.

Assess them differently, because they are at different stages:

- **Plan 253 — full review.** Design, proportionality, task contracts, verification, exit gates,
  readiness. This is the plan that could reach `READY`.
- **Plan 250 — premise and completeness of its scoping checklist ONLY.** It is a deliberate stub,
  marked do-not-implement, with no design, phases, tasks or gates. **Do not report its missing design
  as a finding — that absence is intentional and stated.** The questions worth answering about it are:
  is its stated premise accurate; is the split between 253 and 250 drawn in the right place; and does
  its "what scoping must cover" list omit a dimension that would be expensive to discover later.

`riskClass: high` — this plan carries a migration on the operational `forecasts` table, an
external-facing API contract change, and live-DB impact. *(An earlier draft also claimed user-visible
alert-eligibility behaviour. That was false — the stored combination is not an alert input at all —
and it is corrected in T2a's Out. Three criteria still hold, so the classification stands.)*

## The common seam

Three of the four defects are the same class, and that is why they are one plan: **a quality signal
is computed correctly, and then discarded at the boundary where it would have to be persisted or
served.** The type exists, the function runs, the value is right — and nothing downstream can ever
see it. Each one reads as "implemented" from the source tree and as absent from the database.

The fourth is the bookkeeping consequence: `docs/standards/wmo.md` § 5 records two of these as
*Addressed in v0*, which made the drift invisible for months.

## What is wrong

### D-A — the input-quality assessment is computed, then dropped

`services/input_quality.py:17` assesses, for every forecast, whether it ran on stale observations,
an aged warm-up state, or an old NWP cycle. Both forecast paths call it and attach the result
(`services/run_station_forecast.py:519-560`, `services/run_group_forecast.py:307-346`).

It is then never stored. `store/forecast_store.py:82-90` writes `qc_status` and `qc_flags` and
nothing else of the pair; the `forecasts` table carries no such columns (`db/metadata.py:1143-1148`);
and the read path reconstructs the forecast without them (`store/forecast_store.py:381-389,414`), so
`OperationalForecast.input_quality` falls back to its dataclass default of `FULL` on every read. The
API schema has no field for it either (`api/schemas.py:88-99`).

**Measured (mini, 2026-09-02):** the live `forecasts` table columns are `id, station_id, model_id,
model_artifact_id, issued_at, nwp_cycle_reference_time, representation, status, version,
warm_up_source, warm_up_state_age_hours, observation_staleness_hours, created_at, updated_at,
parameter, units, nwp_cycle_source, qc_status, qc_flags, combination_strategy, source_model_ids,
rating_curve_id`. No `input_quality`. No alembic migration mentions one.

**This is Plan 023's unfinished half, not a new idea.** That plan specifies the columns by name —
`docs/plans/archive/023-degraded-forecast-input-quality.md:734` — under cicd.md's additive-only
migration rule, and § "API exposure" (`:472-477`) specifies the response fields and a filter for
finding degraded forecasts. The plan sits in `docs/plans/archive/` still carrying `status: READY`,
archived by commit `33bdc640` (13 Apr 2026), **a pure file move containing no code**, and its
deferred prerequisites were never picked up.

**Why it matters beyond tidiness.** `docs/standards/wmo.md:171` lists this as an *Addressed in v0*
gap against WMO-1072 and QMF-H: *"Forecasters informed when forecast produced under degraded input
conditions."* A forecaster cannot currently be informed, because the answer is discarded before it
reaches them. Of everything in this plan, this is the one with an external commitment attached.

### D-B — the pooled combination forecast is stored unchecked

`services/forecast_combination.py:404` hard-codes `qc_status=QcStatus.RAW` on the combined forecast,
and `:406` hard-codes `input_quality=InputQualityLevel.FULL`. The flow stores it without ever calling
`ForecastOutputQualityChecker` (`flows/run_forecast_cycle.py:2929-2947`). Every contributing member
model's ensemble is quality-controlled; the product built out of them is not.

**Measured (mini, 2026-09-02):** forecasts by model and status —

| model | qc_status | count |
|---|---|---|
| `climatology_fallback` | qc_passed | 393 |
| `persistence_fallback` | qc_passed | 393 |
| `nwp_rainfall_runoff` | qc_passed / qc_suspect | 278 / 38 |
| `nwp_regression` | qc_passed / qc_suspect | 201 / 17 |
| `linear_regression_daily` | qc_passed | 116 |
| **`_pooled`** | **raw** | **30** |

All 30 are dated 2026-08-27 or later — the day `forecast_combination_strategy = "pooled"` was enabled
in `config/overlays/mac-mini.toml`. The unchecked rows are exactly the pooled ones, and they are the
product the Forecast Lab exists to compare.

The hard-coded `input_quality=FULL` is the same seam as D-A wearing a different hat: a combination
assembled entirely from degraded-input members presently claims full input quality.

### D-C — no gauged feed ever synthesises an expected-but-absent observation

The vocabulary is real and defended: `QcStatus.MISSING` exists, the domain type enforces
`value is None ⟺ qc_status == MISSING` (`types/observation.py:47-49`), and the database enforces the
same as a check constraint, `(qc_status = 'missing') = (value IS NULL)` (`db/metadata.py:543`).

**The precise claim, corrected in review.** An earlier draft said "nothing in operational ingest
ever writes one". That is false, and the correction matters because it changes what Plan 250 has to
build. Two producers exist:

- `services/component_derivation.py:116`, the calculated-station derivation path — and it *is*
  reached from operational ingest (`flows/ingest_observations.py:378-400,414-491` derives, counts a
  null result as missing, and stores it). It is dormant here only because this deployment has no
  calculated stations.
- `scripts/dhm_precip/observations.py:61-90`, an offline research producer.

The true gap is narrower and is what Plan 250 owns: **no direct gauged-feed path synthesises a row
for a timestamp that was expected and did not arrive.** Derivation emits `MISSING` when a *component*
is unusable; nothing emits it when a sensor simply goes silent.

**Measured (mini, 2026-09-02/03):** `SELECT count(*) FROM observations WHERE qc_status='missing'`
returns **0**. And all 148 stations are `gauging_status = 'gauged'` — there are no `CALCULATED`
stations on this deployment, so the sole producer has no live caller here either.

There is also a structural reason it cannot currently work: nothing records a station's expected
reporting **schedule**, per station *and* per parameter (`types/station.py:42-45` already carries
several measured parameters per station), so nothing can distinguish *expected but not received*
from *not expected*. How that schedule is represented and stored is Plan 250's to design, not this
plan's to prescribe. `docs/standards/wmo.md:169` nevertheless records this as *Addressed in v0*:
*"Expected-but-not-received observations are represented as explicit gap markers."*

**Decision taken 2026-09-03 — see § Decision. The producer will be built, and it is Plan 250, not
a task here.** What remains in this plan is making `wmo.md` tell the truth in the meantime.

### D-D — the WMO compliance table asserts outcomes that were specified, not verified

`docs/standards/wmo.md` § 5 "Addressed in v0" lists three resolved gaps. Re-checked against the
running system:

| Row | Claim | Measured |
|---|---|---|
| Sharpness metric (WMO-1364) | P10–P90, P25–P75, mean ensemble range | **Holds.** `services/skill/metrics.py:107-117`, emitted at `services/skill/service.py:448-455` |
| QC "missing" status (WMO-168 Vol I) | explicit gap markers | **Vocabulary yes, production no** — D-C |
| Degraded-input notification (WMO-1072, QMF-H) | on the record, exposed by API, shown on dashboard | **Not met** — D-A |

One row in the *Deferred* table is also stale: WIGOS Station Identifiers (WMO-1192) reads *"Swiss
stations have WIGOS IDs in v0"*. Measured: the `wigos_id` column exists and is populated on **0 of
148** stations.

Two further items were checked and **hold**, and should be recorded as verified rather than left
ambiguous: the QC flag vocabulary maps cleanly onto WMO-168's good / suspect / erroneous / missing
(plus `RAW` as a pre-check state), and the automated range and temporal-consistency checks are
present. The absence of automated spatial consistency is a **reasoned, recorded deferral**
(`wmo.md:182`), not drift, and must not be "fixed" by this plan.

## Decision — resolved 2026-09-03 (owner)

**D-C: should operational ingest synthesise `MISSING` rows at all? — Yes. Build the producer.**

The question was left open on a rationale that **review proved wrong, and the record should say so.**
The argument against building the producer was that `pipeline_health` already meets the underlying
WMO-168 concern. It does not. Its rows are **collector-level and run-level**, carrying aggregate
counts and a single newest timestamp (`flows/collect_bafu_observations.py:160-199`) or a run's
failure counts and station IDs (`flows/ingest_observations.py:175-240`) — not per-station expected
timestamps or ages. The enum itself distinguishes this from the per-station `OBSERVATION_FRESHNESS`
check it was mistaken for (`types/enums.py:193-217`). So the "it is already covered" objection never
held, and the decision below is better founded than the reasoning originally offered against it.

**The owner confirms the consumer exists: DHM's API will deliver unmarked gaps.** DHM's Data
Management System flags a bad value `Erroneous` and then withholds it from the API, so what arrives
is an absence with no marker — indistinguishable from a telemetry outage, and from a station that
never reported that parameter at all. That is precisely the case an explicit gap marker exists for,
and it was the stated condition for choosing this option.

Three things make it the low-risk choice rather than merely the chosen one:

- **The consuming code already exists and is proven on Nepali data.** `MISSING` rows are not a new
  shape for the QC pipeline. `_apply_frozen_sensor` (`services/qc.py:92`) breaks a run on a null value
  (`:119-124`), which is exactly the behaviour that stops a gap silently bridging two
  distant readings into one apparent run. The DHM precipitation work already constructs `MISSING`
  rows over a 26-station, 1.37 M-value frame and runs the production checker across them
  (`docs/plans/archive/173-fit-for-purpose-qc-mask.md` D2). We are moving a validated mechanism into
  operations, not inventing one.
- **The invariant is already enforced at both levels** — `types/observation.py:47-49` and the
  `(qc_status = 'missing') = (value IS NULL)` check constraint at `db/metadata.py:543`. A wrong
  implementation fails loudly rather than writing bad rows.
- **It degrades gracefully.** Expected cadence is per station and nullable; a station with no declared
  cadence materialises nothing and behaves exactly as today. Nothing about the Swiss deployment has
  to change on day one.

**⛔ What a `MISSING` row does NOT do — do not let this be misread later.** It records *that* a value
was expected and did not arrive. It does **not** record *why*, and it does not recover DHM's
`Erroneous` flag. A DHM-rejected value and a dead telemetry link both produce an identical `MISSING`
row. Recovering the reason is not a SAP3-side problem and cannot be solved here: it needs DHM's API to
serve the value together with its flag, which is an open interface question with them
(`docs/requirements/dhm-data-formats-questions.md` § 5). The marker makes the absence countable and
first-class; the questionnaire is what would make it explicable.

**Scope consequence: the producer is Plan 250, not a phase here.** This plan is about signals that are
computed and then dropped at the store boundary. The `MISSING` producer is a different shape of work —
it *creates* a signal that does not exist today, and needs expected-schedule metadata whose shape is
Plan 250's to design (it is explicitly not merely a cadence) plus a migration, a gap-materialisation step in the ingest window, a bound on how far back materialisation
reaches, and a retention position on null rows. That is a plan, not a task, and this plan said so
before the decision was taken. What stays here is Phase 3: making `wmo.md` state the truth in the
interval before Plan 250 lands.

## Non-goals

- **Historical water-level QC.** ~1.96 M imported `water_level` rows sit at `qc_status = 'raw'`
  because onboarding checks only each station's forecast-target parameter
  (`services/onboarding.py:662-671`) and all 148 stations target discharge alone. Real, and a different
  shape of problem — it needs the deferred Flow 12B branch C (QC re-evaluation over stored history,
  `docs/v0-scope.md:27`), not a store-boundary fix. Give it its own plan.
- **Stage 2 QC / rating-curve conversion validation.** v1, correctly deferred
  (`docs/architecture-context.md:270-271,305-309`).
- **Any new or retuned QC rule**, in either rule set.
- **Automated spatial consistency.** A recorded deferral; leave it recorded.
- **Populating `wigos_id`.** D-D corrects the *claim*; filling the column is separate work.

## RETRACTED — a stale measurement carried as a live premise

**This section previously claimed Plan 228/235's recompute had nothing to act on. That was wrong,
and the retraction is kept rather than deleted because the mistake is instructive.**

The 2026-09-02/03 audit measured **0 rows in `skill_scores`, `hindcast_forecasts` and
`skill_diagrams`** against 619 `model_artifacts`, and this plan inferred that Plan 235's premise —
replacing ~114,987 scores — rested on a population that no longer existed.

**Re-measured 2026-09-08** on the same container (`sapphire_flow-postgres-1`) and database
(`sapphire`), after a peer session challenged it:

    SELECT count(*), min(computed_at)::date, max(computed_at)::date FROM skill_scores;
    -- 139712 | 2026-09-04 | 2026-09-04

**Every row was computed on 2026-09-04 — one day after the original measurement.** The table was
genuinely empty when measured; a skill run then populated it. `model_artifacts` grew 619 → 902 over
the same window, so the system was actively training throughout.

**And the conclusion is still that Plan 228's recompute has nothing to act on — for the opposite
reason to the one originally given.** Verified by two peer sessions on 2026-09-08: every one of the
139,712 rows carries `computation_version = 2`, with **zero rows below 2**, and Plan 228's recompute
is scoped to exactly the sub-2 population.

The stronger form of that, established in the repo rather than the database: **commit `8f87eb68` —
Plan 228's own fix — moved `_COMPUTATION_VERSION` from 1 to 2**, so the 2026-09-04 skill run executed
on already-fixed code. D3's population is therefore empty because **the recompute has already
happened**, not merely because the labels happen to read 2. That distinction matters: the weaker
statement invites someone to ask whether the version labels can be trusted; the stronger one closes
the question.

The original instinct — that a recompute premised on that population might do nothing — was right,
and its stated reason was wrong. The reasoning is retained here rather than the conclusion alone,
because a right answer resting on a false premise is one re-measurement away from becoming a wrong
one.

⛔ **The lesson, because it will recur.** The original measurement was correct and correctly dated.
The error was quoting it six days later as if it were current, and building an argument on it. **A
measurement of a live system has a shelf life.** This deployment changes daily: forecasts grew from
~1,466 rows to 7,793 and artifacts by 283 in the same window. Any figure quoted from it must carry
its date, and any decision resting on one must re-measure first. That obligation is on the person
citing the number, not the person who took it.

## Owner decisions taken 2026-09-04

**OD-1 — a combined forecast that fails QC is STORED, marked failed.** This is a deliberate,
owner-approved exception to existing behaviour, not an oversight: station execution returns an
assignment failure on `QC_FAILED` (`services/run_station_forecast.py:484-517`), group execution
returns no forecast (`services/run_group_forecast.py:274-305`), and the spec routes a failed station
forecast to fallback rather than storage (`docs/spec/types-and-protocols.md:705-726`). The exception
is justified by what the combination is *for*: it exists to compare models, and a silently dropped
row leaves no record of what was rejected or why. Unlike a member model, a failed combination has no
next candidate to fall through to, so dropping it forfeits the evidence rather than substituting for
it. T2a records the rule and locks it with a test.

**OD-1a — refined 2026-09-04, after review established what surfacing would cost.** The stored
failed combination is **excluded from the Forecast Lab, not shown there**. Surfacing it requires
adding `qc_status` and `qc_flags` to the Lab's published snapshot format, whose combined-forecast
objects are strict (`additionalProperties: false`, eight permitted fields —
`docs/spec/forecast-lab-snapshot-v2.schema.json`), so it would mean a v3 transition across the eight
files that stamp or check `forecast-lab-snapshot/v2`: `cli/export_forecast_lab.py`,
`api/forecast_lab_schemas.py`, `api/routes/forecast_lab.py`, the schema file,
`docs/spec/forecast-lab-snapshot.md`, `tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json`,
`tests/unit/services/forecast_lab/test_snapshot.py` and `docs/plans/204-...`. That is a versioned
external-format change riding on a persistence fix, so it becomes **Plan 251** and this plan
excludes the row instead.

⛔ **Exclusion must be explicit, not incidental.** The Lab treats every renderable row as available
(`services/forecast_lab/snapshot.py:453-504`) and fetches a matching combination without consulting
QC (`services/forecast_lab/db_sources.py:184-204`), so doing nothing would make a failed combination
appear there as an ordinary healthy forecast — worse than absent. The record lives in the database
until Plan 251 surfaces it.

**OD-2 — input-quality level AND its flags are visible to every authenticated role.** Plan 023
required the threshold-bearing flag details to be role-filtered once authorization existed
(`023:128-143`). Authorization is now live but only `consumer` and `admin` roles were ever built
(`docs/standards/security.md:12-20`, `api/security.py:119-142`) — there is no forecaster or operator
role to filter *to*. The owner has decided the thresholds are not sensitive and that a forecaster
looking at a degraded forecast needs to see why. **This knowingly supersedes 023:128-143**; T1c
records the supersession in `docs/standards/security.md` so the next reader does not mistake it for
the same prerequisite being dropped again.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md:198`): its own verification
command, `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`,
`uv run pyright src/`, `uv run pytest`, and affected docs updated in the same change.

Phase 1 (T1a-T1c) persists and serves input quality (D-A). Phase 2 (T2a-T2b) quality-controls the
pooled combination (D-B). Phase 3 (T3a) states the `MISSING` claim truthfully (D-C). Phase 4
(T4a-T4c) re-verifies the compliance table (D-D).

### T1a — additive migration for the input-quality pair

**Outcome:** `input_quality` and `input_quality_flags` exist on `forecasts` as nullable columns, the
alembic head advances, and every pre-existing row remains readable and reads as unknown.

**In:** one alembic migration; the `forecasts` table definition in `db/metadata.py:1143-1148`; the
pinned-head test every new migration must advance
(`tests/unit/db/test_alembic_head_release_b.py:56-58,96-129`).

**Out:** `hindcast_forecasts` (Plan 023 scoped this to `OperationalForecast`). Any backfill.
Explicitly excluded: the `input_quality TEXT DEFAULT 'full'` variant Plan 023 suggests at
`023:738-740` — a server default would make every pre-migration row read `FULL`, the exact failure
exit gate 2 forbids. `docs/standards/cicd.md:186` requires nullable, not defaulted.

**Pre-change:** `uv run python -c "from sapphire_flow.db.metadata import forecasts; print([c.name for c in forecasts.columns])"` prints a list containing neither `input_quality` nor `input_quality_flags`, and `grep -rl input_quality alembic/versions/` is empty.

**Verification:** `uv run pytest tests/unit/db/test_alembic_head_release_b.py tests/integration/db/test_migration_input_quality.py` — a revision-independent filename, deliberately, so the command stays executable while the revision itself is chosen at implementation time — the revision number is chosen against the branch at implementation time — not fixed here, since it drifted three times during review — and the migration's `down_revision` and `_RELEASE_B_HEAD` (`tests/unit/db/test_alembic_head_release_b.py:67`, assertions from `:105`) are updated together; the head-pin test passes with the new revision; the columns are nullable with no server default; a row inserted before the migration survives it with `NULL` in both columns; and downgrade removes both, and the new migration's own upgrade/downgrade test runs against real PostgreSQL per `docs/v0-scope.md:434-446`.

### T1b — write and read the pair

**Outcome:** a forecast assessed `DEGRADED` at write time reads back `DEGRADED` with its flags, and a
row written before the migration reads back as unknown rather than as `FULL`.

**In:** `store/forecast_store.py` insert (`:82-90`) and both row-to-domain paths (`:381-389`, `:414`).
The domain type must change to make "unknown" representable: `input_quality: InputQualityLevel | None`
on `OperationalForecast` (`types/forecast.py:67`), with the knock-on for `aggregate_input_quality`
(`types/domain.py:125`). Docs: `docs/spec/types-and-protocols.md:1777-1778` and its `ForecastStore`
Protocol section; and `docs/architecture-context.md:1852-1853`, which still declares both columns
`NOT NULL` with confident defaults and must be corrected to describe legacy `NULL` as unknown.
Depends on T1a.

**Out:** changing the assessment logic in `services/input_quality.py` — it is correct.

**Pre-change:** a store round-trip asserting a `DEGRADED` forecast reads back `DEGRADED` fails, returning `InputQualityLevel.FULL` and an empty flag tuple, because the read path never sets the field.

**Verification:** `uv run pytest tests/integration/store/test_forecast_store.py` — a `DEGRADED` forecast with at least two flags survives write-then-read compared by value, not by count; and a row inserted without the columns reads back unknown, not `FULL`.

### T1c — expose input quality on the API

**Outcome:** every authenticated role can see both the input-quality level and its flags on a
forecast, and can list the degraded ones (OD-2).

**In:** `api/schemas.py` `ForecastSummary` (`:88-99`); `api/routes/api_stations.py:105-117` where
`ForecastSummary` is built, plus the list endpoint's query parameter; the **separate** detail
serializer at `api/routes/api_forecasts.py:56-80`, which constructs its response explicitly and will
not inherit the fields; `types/forecast_summary.py:18-28`; `store/forecast_store.py`
`fetch_forecast_summaries:199-236` and `_row_to_summary:405`; the Protocol at
`protocols/stores.py:200-211`; the fake at `tests/fakes/fake_stores.py:328-360`, which must gain both
the fields and the filter semantics; `docs/spec/types-and-protocols.md:1777-1778`. Docs:
`docs/standards/security.md` records OD-2 as superseding `023:128-143`. Depends on T1b.

Both the summary and the detail response expose `input_quality` as **nullable**, since T1a/T1b make
legacy rows read as unknown; the plan fixes unknown flags as `null`, not `[]`, so "no assessment"
and "assessed with no flags" stay distinct. An implementer who makes the field non-nullable turns
every legacy response into a validation failure, so this is stated rather than left inferable.

**Out:** the dashboard indicator Plan 023 also specifies — a separate surface that must not gate the
API fix. T4b marks the architecture doc's existing dashboard claim as unimplemented rather than
building it.

**Pre-change:** a JSON assertion for `input_quality` and `input_quality_flags` on a degraded forecast fails because the keys are absent — the schema has no such field (`api/schemas.py:88-99`) and the serializer never supplies it (`api/routes/api_stations.py:105-118`); the existing response itself stays valid.

**Verification:** `uv run pytest tests/unit/api/ tests/integration/store/test_forecast_summary.py` — the fields appear on both the summary and the detail response, both roles see level and flags, a legacy row with no assessment serialises as `null` on both summary and detail without failing validation, and the filter returns exactly the degraded set — proven against the real SQL predicate, not only the fake — with unknown rows **not** reported as degraded.

### T2a — quality-control the combined ensemble

**Outcome:** a `_pooled` forecast carries a real `qc_status` and flags, on the same rules and the same
datum handling as its members, from every path that stores one; and a `QC_FAILED` combination is
stored marked failed per OD-1.

**In:** both `build_combined_forecasts` call sites — `flows/run_forecast_cycle.py:2929-2947` and
`:3254-3291` — preferably via one shared helper both call. Remove the hard-coded `QcStatus.RAW` at
`services/forecast_combination.py:404`. The member path's datum handling must come too: the
water-level shift and datum-provenance helpers (`services/qc_datum.py:35-64,93-114`) and the station
datum map, as used at `services/run_station_forecast.py:484-505` — without them, raw
metres-above-sea-level values run against the `-2..20 m` bounds
(`config/forecast_qc_rules.py:149-176`) and fail falsely, reintroducing the Plan 101 defect.

**Excluding the failed combination from the Forecast Lab (OD-1a).** The Lab's combined-forecast
selection must skip a `QC_FAILED` combination — `services/forecast_lab/db_sources.py:184-204`, which
currently fetches a matching combination without consulting QC. This is a **filter, not a schema
change**: no field is added and the snapshot format stays at v2, so nothing in
`api/forecast_lab_schemas.py` or the published schema moves. Surfacing it is Plan 251.

Docs: `docs/spec/types-and-protocols.md:727`, which currently reads that an aggregate `QC_FAILED`
raises `SanityCheckFailure` and the flow tries a fallback model, while only `QC_PASSED` or
`QC_SUSPECT` results are stored. After this task that is false for the combination, and leaving it
would be the same drift class this plan exists to correct. Add the one-sentence exception: a
combined (`_pooled`) forecast that fails QC is stored marked `QC_FAILED` rather than routed to
fallback, because it has no next candidate — cite OD-1. Also `docs/architecture-context.md:90` and
`:114`, which still state categorically that `QC_FAILED` triggers fallback and that only passed or
suspect forecasts are stored; `:114` additionally claims group failures are stored, which the group
path contradicts by returning no forecast (`services/run_group_forecast.py:295-305`). All three
statements need the station / group / combined distinction spelled out.

**Out:** changing any QC rule or threshold. Any change to alert routing: the stored combination is not
an alert input at all — alerting re-pools member ensembles independently
(`services/alert_strategy.py:_pool_ensembles:99-143`) and partitions them by `AlertEligibility` alone
(`flows/run_forecast_cycle.py:854-903`), so a `QC_FAILED` combination changes nothing about alerting.

**Pre-change:** `SELECT model_id, qc_status, count(*) FROM forecasts WHERE model_id='_pooled' GROUP BY 1,2` returns only `raw` rows (30 on the mini at 2026-09-02), including cycles where a contributing member was flagged `qc_suspect`.

**Verification:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py tests/unit/services/test_forecast_combination.py tests/unit/services/forecast_lab/test_snapshot.py` — a combination over ensembles that trip a real forecast QC rule is stored `QC_FAILED` with flags (OD-1, locking the exception); one over clean members is stored `QC_PASSED`; water-level combinations are tested with a datum present and absent; no stored combination row can carry `qc_status='raw'` from either call site; a `QC_FAILED` combination is **absent** from the Lab snapshot rather than present-and-available (OD-1a); and the emitted snapshot still validates against the unchanged v2 schema.

### T2b — the combination inherits its contributors' input quality

**Outcome:** a combination reports the aggregate input quality of the models that actually
contributed to *that parameter*.

**In:** replace the hard-coded `InputQualityLevel.FULL` at `services/forecast_combination.py:406`
with an aggregate over the per-parameter contributor set — pooling selects contributors separately
per parameter and excludes missing or non-`MEMBERS` ensembles
(`services/forecast_combination.py:65-92,308-317`), so aggregating over every
`multi_result.combinable_results` entry would degrade a product a model never fed. Reuse
`aggregate_input_quality` (`types/domain.py:125`). Depends on T1b and T2a.

**Out:** inventing a new aggregation rule.

**Pre-change:** a test combining one `FULL` and one `DEGRADED` contributor asserts `DEGRADED` and fails, returning `FULL`, because the field is a literal.

**Verification:** `uv run pytest tests/unit/services/test_forecast_combination.py` — one `FULL` plus one `DEGRADED` contributor yields `DEGRADED` with the contributing flags; an all-`FULL` combination yields `FULL`; and a degraded model excluded from a given parameter (absent, or quantile-represented) does **not** degrade that parameter's product.

### T3a — state the `MISSING` claim truthfully

**Outcome:** every doc claiming operational gap markers exist says instead that no gauged feed
synthesises them, cites the measurement, and points at Plan 250.

**In:** `docs/standards/wmo.md:169`; `docs/architecture-context.md:2208` (and the schema comments at
`:2232`, `:2240`), which states MISSING rows are "set during gap detection in the observation ingest
pipeline" — the claim that would route Plan 250's implementer wrongly; and
`docs/architecture-context.md:400`, which asserts a QC-failed filter on the alert path that does not
exist; and `docs/plans/README.md:100-127`, whose entries for both plans were corrected in this plan's own
authoring and must be **verified as still correct and preserved**, not rewritten again. The index is
an active-plan document carrying the same no-stale-docs obligation
(`docs/workflow.md:392-396`). The corrected wording must **not** claim `pipeline_health` already provides per-station
freshness: its records are collector-level and run-level
(`flows/collect_bafu_observations.py:160-199`, `flows/ingest_observations.py:175-240`), distinct from
the per-station `OBSERVATION_FRESHNESS` check (`types/enums.py:193-217`).

**Out:** deleting `QcStatus.MISSING`, its invariant, or its check constraint — all load-bearing and
all prerequisites for Plan 250. Pre-announcing Plan 250's design.

**Pre-change:** N/A — documentation task. The measurement it records is D-C's: `SELECT count(*) FROM observations WHERE qc_status='missing'` returns 0.

**Verification:** N/A — documentation task. Each amended row cites the query establishing the current state, so the next audit re-runs it rather than trusting the sentence.

### T4a — relabel every unevidenced compliance row

**Outcome:** every "Addressed in v0" row in `wmo.md` § 5 either cites the test or query establishing
it with a date, or is relabelled **specified, not verified**. Depends on T3a, which rewrites the
`MISSING` row this task then audits.

**In:** `docs/standards/wmo.md` § 5, both tables, including the stale WIGOS row (`wigos_id` populated
on 0 of 148 stations). The final row inventory must also **add the two rows D-D found holding and
promised**, with their evidence: the QC flag vocabulary mapping onto WMO-168's good / suspect /
erroneous / missing (`types/enums.py` `QcStatus`) and the automated range and temporal-consistency
checks (`services/qc.py:50`, `:225`). A promise in a design section that no task delivers is not
implementation-accountable (`docs/workflow.md:129`).

**Out:** re-auditing the WMO publication inventory in § 1-3; the catch-efficiency position
(`wmo.md:78-100`), which is current and well-evidenced. Flipping any row to verified — that is T4c.

**Pre-change:** N/A — documentation task. The drift it corrects is measured in D-D.

**Verification:** N/A — documentation task. Each row's cited command must be runnable as written.

### T4b — mark the unimplemented dashboard claim

**Outcome:** the architecture doc no longer asserts that input quality is already visible on the
dashboard.

**In:** `docs/standards/logging.md:264-270` and `docs/standards/orchestration.md:191-194`, which
assert that `forecast.input_quality_assessed` is emitted for non-`FULL` assessments; neither forecast
path emits it (`services/run_station_forecast.py:519-559`,
`services/run_group_forecast.py:307-348`). Mark it specified-but-unimplemented rather than building
it — emitting the event is outside this plan's persist-and-serve scope. Also
`docs/architecture-context.md:110`, which describes input quality as visible to forecasters
through both the API and the dashboard. The API half becomes true in T1c; the dashboard half is out
of scope here and must be marked unimplemented rather than left standing. (`:1821` is NOT in scope —
it merely lists the fields as operational metadata and asserts neither claim; the `NOT NULL`
assertion is at `:1852-1853` and belongs to T1b.)

**Out:** building the dashboard indicator.

**Pre-change:** N/A — documentation task correcting a claim about a surface that does not exist.

**Verification:** N/A — documentation task.

### T4c — flip the two rows that Phase 1 earns, and add the standing rule

**Outcome:** the degraded-input row moves to verified with its evidence once Phase 1 has landed; the
missing-marker row stays **specified, not verified** until Plan 250 lands; and a rule prevents the
recurrence. Depends on T1c and T4a.

**In:** `docs/standards/wmo.md` § 5 (the two rows) plus one paragraph stating that a compliance row
moves to *Addressed* only on evidence from the running system or a named test — never on a plan's
status, and never on a plan's declared-but-unclaimed prerequisite. Both failure modes must be named:
Plan 023 was archived at `READY` (commit `33bdc640`, a pure file move) **and** deferred these columns
to Phases 8/9 as prerequisites (`023:733-740`, `023:472-477`) that no later phase picked up. The
second is what actually happened.

**Out:** any tooling to enforce the rule. This is a convention; the audit that found the drift was
cheap.

**Pre-change:** N/A — documentation task; the rule does not exist today.

**Verification:** N/A — documentation task.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/253-close-the-store-boundary-seam.md
```

Five conditions hold in addition to the commands above:

1. **A locking test per code defect, each demonstrated failing before its fix.** T1b's must assert the
   round-tripped *value* — the bug is that a plausible default is substituted, so a test asserting
   "a level is present" passes against the broken code.
2. **No historical row is backfilled to `FULL`.** An unknown input quality must read as unknown.
   Replacing one false confident answer with another is not a fix.
3. **`wmo.md` § 5 contains no row this plan has not either verified or relabelled**, and
   `architecture-context.md:90`, `:110`, `:114`, `:400`, `:1852-1853`, `:2208`,
   `docs/spec/types-and-protocols.md:727` and the Plan 253/250 entries in `docs/plans/README.md`
   are corrected.
4. **The pooled-forecast tests use a real QC-tripping ensemble**, not a mocked checker verdict, cover
   both call sites, and cover water level with and without a station datum.
5. **Plan 250 exists at `status: DRAFT` and is listed in `docs/plans/README.md`.**

## Dependency graph

```json
{
  "nodes": [
    {"id": "T1a", "phase": 1, "depends_on": []},
    {"id": "T1b", "phase": 1, "depends_on": ["T1a"]},
    {"id": "T1c", "phase": 1, "depends_on": ["T1b"]},
    {"id": "T2a", "phase": 2, "depends_on": []},
    {"id": "T2b", "phase": 2, "depends_on": ["T1b", "T2a"]},
    {"id": "T3a", "phase": 3, "depends_on": []},
    {"id": "T4a", "phase": 4, "depends_on": ["T3a"]},
    {"id": "T4b", "phase": 4, "depends_on": []},
    {"id": "T4c", "phase": 4, "depends_on": ["T1c", "T4a"]}
  ]
}
```

`depends_on` is the only ordering statement; anything not named there runs independently. T2a, T3a
and T4b have no prerequisites and may run in parallel with Phase 1. T3a → T4a → T4c is a real chain,
not bookkeeping: all three edit the same `wmo.md` § 5 rows, T3a rewriting the `MISSING` row, T4a
relabelling every unevidenced row, and T4c flipping the two that Phase 1 earns.
