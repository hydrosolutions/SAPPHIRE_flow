---
status: DRAFT
created: 2026-11-18
---

# Nepal MVP model onboarding: climatology, CMAL, engression

## Owner-authorized current-contract rehearsal — 18 November

The owner explicitly wants to exercise onboarding now, using the current
conventions, and retrain/re-evaluate after FI is ready (expected no sooner than
five days). The prior time-grid hold below applies to qualified Nepal-local-day
publication, not this temporary functional rehearsal. Reuse the installed native
climatology unchanged; this is not permission to implement a model outside FI.

Rehearsal status: **READY** after independent Claude operational review on
18 November. The orchestrator accepted the conditions below. This status applies
only to the bounded operator rehearsal; C1–C3 scientific qualification remains
DRAFT. A requested deployment by another session must be coordinated before
starting: record the resulting deployed image and avoid concurrent deployment
or database-changing tests.

Bounded operator sequence:

1. Create a temporary `nepal_onboarding_rehearsal_20261119` database on the
   existing MVP host from a consistent dump of the current schema/data, omitting
   only `historical_forcing` and `audit_log` data. Neither has incoming foreign
   keys (checked live); both empty tables and their grants remain. Climatology
   needs no weather; verify the installed assembly path honours that requirement.
   Budget 3 GiB for the copy, retained dump, new artifacts and logs; stop and
   report if exceeded. Check connection headroom and run serially in a low-load
   window. Do not change live connections, schedules or database grants.
2. Use the exact deployed image, process-local DATABASE_URL selecting that
   database, and a separate writable artifact root. Assert the database name
   on the flow's own connection before any write. Call flows directly in-process,
   not through a deployment with its own environment. Assert a separate artifact
   root and never prune copied artifact records or their live files. All child
   flows inherit that connection scope. Record the Prefect API and confirm no
   competing climatology training run; its concurrency slot and run metadata are
   shared operational side effects. Preserve
   existing tenant isolation and write auditing. Restricted input and logs stay
   outside Git on the private server; report counts and statuses only.
3. Run the actual `onboard_model_flow` first for station 450, with explicit
   station scope, `climatology_fallback`, daily step and 2010–2012 training window.
   Exercise the unchanged smoke/train/store/hindcast/skill/promotion/assignment
   path. Its built-in hindcast uses the training window: label those scores
   in-sample workflow checks, not validation skill. Keep configured gates;
   record a refusal rather than lowering thresholds to manufacture success.
4. Use the existing standalone hindcast and skill paths for a separate 2013
   held-out pilot where observations exist, with a distinct hindcast run ID passed
   through skill computation. Report scores by run ID and evaluation window,
   never an artifact-wide mixture of in-sample and held-out results. Do not
   re-evaluate promotion against that mixture. Then repeat the bounded test for
   the other five September stations. All output stays in the rehearsal database;
   no current-model replacement or ordinary API skill publication occurs.
5. Exercise the general station-observation import route on the October
   delivery in the copy. Its 18 DFL files cover 15 additional station histories,
   two repeats (604.5, 647), and a 2024–2025 extension for 450. With the September
   six this covers all 21 registered stations. Check parser identity, checksums,
   dates, duplicates, source permissions and QC; never replace the guarded
   September delivery through its six-station-only importer. Set the incoming
   directory process-locally to a separate rehearsal location. Verify the installed
   import route preserves source provenance and QC before applying it. Test additional
   station climatologies where sufficient training data exists. Station 610
   ends in 2012, so the proposed 2013 held-out test cannot score it.
6. Read back artifacts, hindcasts, finite values, metric sample counts, gate
   outcomes and assignments, including promotion/assignment audit events when
   promotion succeeds. Compare live model registry, artifact, assignment, skill,
   hindcast, observation and audit counts, live artifact inventory and concurrency
   slot state with the pre-run snapshot; account explicitly for any normal live
   ingestion rather than attributing its writes to the rehearsal. Retain a concise run manifest and
   the temporary results for inspection; remove the temporary copy/artifacts
   after the corrected rerun or an explicit cleanup decision.

This rehearsal deliberately retains the existing UTC alignment, calendar-bin
and output-label limitations. It is a functional onboarding test, not a claim
of correctly aligned Nepal-day skill. A successfully completed Prefect run alone
is insufficient. Do not silently change statistical methods, fabricate reference
days, or use this authorization to change active CMAL's time grid.

### Rehearsal findings and smoke-fixture fix (R1 — superseded for implementation)

The owner requested a deployment-independent MVP plan after the initial patch
was drafted. Further implementation follows
[the reusable model-onboarding plan](2026-11-19-reusable-model-onboarding-mvp.md).
That plan is READY after independent review and incorporation of its findings.
The earlier R1 review below is historical evidence, not readiness for the expanded
scope. The isolated rehearsal authorization remains unchanged.

On 19 November the other session confirmed its 0.1.1154 disposable smoke test
and cleanup passed; the coordination hold is cleared. Live forecasts remain
healthy: both morning runs delivered all 17 stations using the retained weather
product, with the first stored by 06:20 Nepal time.

The isolated database was restored at schema 0087, retaining 1,096 QC-passed
station-450 observations for 2010–2012. Snapshot plus database occupy about
1.4 GB. No live model, assignment or station setting was changed.

The actual onboarding flow exposed two prerequisites:
- Station 450 had no forecast target declared. Applying the runbook's existing
  `PgStationStore.update_station` discharge-target declaration in the test copy
  made compatibility pass. Its lifecycle status stayed unchanged. Check this
  prerequisite for each subsequent rehearsal station; do not bypass compatibility.
- The unchanged synthetic smoke fixture supplies only 21 rows to climatology,
  whose training correctly requires at least 365. The resulting
  `FAILED_SMOKE_TEST` leaves no artifact, hindcast, score or assignment. This
  failure is independent of the pending FI/time-grid work.

Minimal proposed patch: give the shared synthetic train/predict fixture at least
366 rows, retaining the existing larger lookback + horizon + margin calculation.
Its existing base date is 1 January 2000; a daily fixture then covers every
ordinal-day bin in that leap year. Keep the real model's training threshold,
requirements, prediction behavior, dates and operational checks unchanged. No new
configuration, training-contract field, dependency or model-specific bypass.
This is adequate synthetic coverage for the existing built-in climatology, not
a claim that every future model can train on 366 samples.

Owner requirement (19 November): individual station failures must not block
eligible stations from completing onboarding. The flow already continues after
incompatibility, missing training data, model smoke/training failures and skill
gate refusal. Preserve that behavior and add a mixed-station regression proving
one station's training failure still permits another station to be promoted and
assigned, with separate outcomes. Preserve fail-loud storage/integrity errors;
do not add a catch-all around the entire flow or claim arbitrary infrastructure
errors are safely recoverable.

Verification: reproduce the real climatology smoke failure, then test its
train/serialize/reload/predict smoke path and the mixed-station behavior. Run
affected smoke/conformance and onboarding tests, Ruff and changed-module typing;
require independent patch review and full final CI before merge. Update the
model-onboarding documentation. Only after reviewed merge and the ordinary
testing-image delivery may the server rehearsal use the changed code; no
container hotpatch or skipped smoke gate.

Independent Claude plan review completed on 19 November. It found no design
blocker and requested implementation evidence: the real climatology round trip,
mixed-station outcomes with a real model training call, preservation of fatal
storage errors, and smoke/conformance coverage for existing supported model
shapes. The orchestrator accepts these as implementation exit gates and marks
R1 READY. Use behavioral coverage rather than an assertion against a private
fixture row-count calculation. Existing larger-lookback, hourly, group and
ensemble paths must retain their contracts. External import-only artifacts do
not acquire a synthetic-training requirement; import does not call this helper.
Continue with the existing
held-out and station-history rehearsal after this narrowly scoped fix; do not
bundle model migration, time-grid implementation or general failure recovery.

Owner-requested order: reclaim unused server space, then climatology with
hindcasts and skill, then the supplied CMAL static encoder, then engression.
Use existing model import, training, evaluation and assignment mechanisms where
their contracts fit. Do not change the working `cmal_small` deployment as a
side effect of preparation. This plan does not authorize a production release.

## Verified starting point

- Refreshed the isolated onboarding branch to remote main `0815e9a2` on
  18 November (release target 0.1.1155), preserving this plan. Since the previous
  base `e0822bf5`, PR 561 adds the dry-run-first group assignment CLI, PR 560
  qualifies deployment smoke, and PR 550 updates the draft SnowMapper schedule.
  No training, hindcast, skill, observation-import, time-grid or FI/Aquacast pin
  changes landed in that interval. Climatology remains a station-scoped model;
  its rehearsal sequence is unchanged. The SnowMapper document is not a runtime
  scheduling change or a new input dependency for these models.
- The smoke-test images 0.1.1154 are loaded and verified on the server, while the
  operational stack remains 0.1.1152. Neither includes PR 561's assignment CLI;
  do not assume fetching main updates an installed image. Its absence does not
  block the existing climatology onboarding flow. The other session confirmed
  its disposable test and cleanup complete before the rehearsal started.
- Cleanup completed: removed the unused 0.1.1136 application image pair after
  verifying its retained release payload. Recovered 14.06 GiB; approximately
  70 GiB was free immediately afterwards. After image delivery and rehearsal
  preparation, the 19 November check showed about 47 GiB free.
  Running 0.1.1152 and immediate rollback 0.1.1139 remain loaded.
  Installation archives, database backups and application volumes were retained.
- Server has one active `cmal_small` artifact, no climatology artifact, no
  hindcasts and no skill rows (18 November read-only inventory).
- Historical ordinary daily discharge exists for 447, 450, 604.5, 647, 670 and
  684. All have observations in 2013–2019, with missing days at some stations;
  450 also extends through 2023. This does not establish reference coverage for
  all 17 live forecast stations. Do not invent a regional climatology for the
  other stations or treat provisional rating estimates as ordinary truth.
- The pre-2013 inventory is substantial: between 6,178 and 18,122 qualified
  daily values per station, covering all 366 month/day bins. The sparsest bin
  contains 5–13 samples depending on station. Exact support must be rechecked
  after the final period/source restrictions; total row count alone is inadequate.
- Imported discharge dates represent Nepal civil days. Original timestamps are
  18:15 UTC in the modern record and 18:30 UTC before Nepal's offset change.
  Preserve the historical offset and original local dates.
- Current shared training/hindcast assembly still uses UTC buckets. The native
  climatology model indexes UTC ordinal days, declares five forecast steps and
  selects its first quantiles at `issue + step`. Those are legacy semantics,
  not proof of the agreed Nepal current-day product.
- The supplied CMAL and engression configurations declare a 365-day lookback,
  up to 15 forecast days, discharge, precipitation, mean temperature, net thermal
  radiation and net solar radiation. Their producer records name Aquacast
  0.1.390 and FI 0.1.20; this is not a compatibility proof for the installed pin.
  Neither radiation quantity is registered separately in the server parameter
  inventory; generic `radiation` must not be silently substituted.
- Both supplied checkpoint and configuration pairs match their accompanying
  checksum list; both checkpoint hashes also match the manifest. No checkpoint
  has been deserialized or imported by this work.

## Shared prerequisites, not another time-grid specification

Use [the existing time milestones](2026-11-17-hydrological-time-mvp-milestones.md)
and their API/storage decisions. M0/M1/M2a are implemented; the remaining model,
observation, hindcast, skill and artifact consumers are not qualified for Nepal
activation. The agreed target remains 18:00 UTC, with the explicitly accepted
15-minute displacement from modern DHM daily reference periods for preliminary
evaluation. Preserve both physical periods and the local forecast-day identity.
Publish period ends without selecting the following day's climatology.

FI time-grid PR #14 was still open when checked on 18 November. Do not pin an
unreleased branch or invent a private declaration to bypass that dependency.
New or changed model implementations must follow FI, including typed expected
failures. Inventory the minimum required existing milestone work before setting
the implementation slice READY; neither the legacy native climatology API nor
an apparent successful UTC hindcast is an exemption.

There is no authorization here to enable a nonzero grid for the existing
UTC-trained CMAL artifact. Qualify artifacts independently and preserve current
operation until the applicable coordinated cutover is ready. No historical
timestamp rewrite, fabricated hourly discharge or rainfall apportionment.

## C1 — climatology first

**Preparation:** establish exact source delivery/units/QC and usable daily
coverage for the six stations. Exclude failed/missing reference observations;
report omitted days and sample counts. Proposed evaluation split: train only on
available qualified records through local 31 December 2012; hold out local
2013–2019. Treat the additional 2020–2023 record at 450 as a separate optional
evaluation window. Include no held-out observations in fitted quantiles,
normalization or scoring reference baselines. Record the chosen windows before
any fit. This split is a proposal, not an out-of-sample claim for external models.

**Bounded implementation design:** reuse the climatology quantile calculation
and existing metric services where correct, without adding a new statistical
model family. Specify local calendar-day lookup, leap-day behaviour, first and
last forecast periods and horizon explicitly. Check minimum useful support per
calendar bin, not just the existing 365-row total. Keep the five-day existing
horizon unless a reviewed requirement changes it; comparisons with the other
models use common supported leads. Do not introduce smoothing or synthetic
reference values as incidental fixes. Resolve FI compliance and the required
time-milestone consumer changes before execution; this DRAFT does not prescribe
an unreviewed compatibility wrapper.

The target-grid decision for climatology must be explicit: the existing
15-minute permission is for preliminary evaluation, not permission to relabel
18:15 reference targets as exact 18:00 training targets. Earlier 18:30 references
also cannot inherit the modern 15-minute exception. A calendar-date diagnostic
can preserve those source periods, but does not qualify a live artifact for
the deployment grid. Do not add a second live grid or broaden the exception
without settling this in the existing time contract.

**Evaluation:** begin with one station and a short isolated fixture/pilot, then
all six and the held-out window. Keep pilot artifacts, hindcasts, score rows and
baselines out of live selection/publication. Reuse an existing isolated store
route rather than changing live station groups or relying only on an unpublished
generation flag. Compute supported distribution and point metrics by declared
lead, with observed sample counts. Do not invent danger thresholds or promise
threshold-based skill when thresholds are absent. Climatology's own reference
score is not evidence that it predicts events; keep that distinction in labels.

**Exit:** reviewed implementation and final tests; finite complete horizons;
correct local-date/period joins across leap days and year boundaries; no
train/evaluation leakage; reproducible artifact and source identity; stored
hindcasts and skill readable with honest period/source labels. Only then make
qualified climatology artifacts available through the normal operational
assignment route, with existing forecast/alert fallback semantics preserved.
Live API skill exposure also requires the existing time-plan publication gates.

## C2 — supplied CMAL static encoder

The owner selected this artifact on 19 November. Its bounded registration is
covered by [the independently reviewed registration slice](2026-11-19-cmal-static-encoder-registration.md).
This does not lift the input, provenance or scientific activation conditions
below. FI provenance requirements are being coordinated upstream; lack of
metadata not required by FI is not a modeller contract violation.

After C1, verify supplied file digests and reconstruct through the supported,
restricted FI/Aquacast loader. Keep weights and restricted records outside Git.
Establish the complete training history (including pretraining), target units,
daily support and missing-discharge tolerance from the artifact/model contract.
The configuration's 1980–2012 range alone cannot establish all training exposure.

Check all required static attributes and the full 365-day dynamic history.
Confirm gateway availability, variable identities, units, interval semantics and
historical/forecast coverage for both radiation quantities. If gateway
registration is needed, prepare the exact request; do not send it without the
owner's instruction. No alias from generic radiation without physical equivalence.
Prefer the existing import and shim paths; use a distinct model identity and
preserve `cmal_small` until the replacement's station coverage and resource cost
are demonstrated. For group-scoped artifacts, use the new
`sapphire_flow.cli.assign_group_model` command when available in the chosen
runtime: dry run first, then apply the reviewed group/model/step/priority. It
reuses the existing assignment service; it does not import or qualify an
artifact, train a model, or compute hindcasts/skill. Identical active assignments
are no-ops, differing active assignments are overwritten, and inactive ones are
refused. Keep existing artifact and scientific qualification checks before
assignment; the CLI's model-registry check is not an artifact-readiness check.
Agree the operational horizon against actual retained-weather
support: a maximum 15-day model horizon is not a guarantee that yesterday's
15-day product covers all of today's requested days.

Run a bounded hindcast and input/output compatibility check before live
activation. Expand evaluation only where training overlap and reference-day
meaning permit an honest comparison with climatology.

## C3 — engression

Reuse the C2 input audit where identical, but verify its separate artifact and
FI capability. Check installed Aquacast support before adding dependencies or
SAP3 model code. Validate seeded probabilistic output, representation/quantiles,
missing-input failure behaviour and bounded CPU/memory use. Preserve its declared
sampling semantics; do not turn trajectories into unrelated ensemble members.
Use the same qualified reference periods and common leads for comparison.

## Decisions and holds

- Await the model provider's daily-period and complete training-history evidence
  for the supplied external models. The owner was asked for these facts.
- Scientific C1 qualification needs a concrete minimal time/FI implementation
  slice and independent plan review before READY. The separately reviewed,
  isolated rehearsal above may exercise the existing auto-promoting flow.
- No new live models, artifacts, hindcasts, skills or assignments have been
  written. The isolated rehearsal registered climatology and declared the
  station-450 discharge target; both attempts stopped before artifact training.
  No gateway configuration changed.

Independent Claude scientific plan review completed on 18 November; qualified
live activation remains HOLD. Accepted findings: make first/current-day and leap-day semantics concrete,
set an explicit per-calendar-bin support rule, select the FI integration route,
and qualify output anchoring, reference joins and artifact/publication identity
before running auto-promoting onboarding. Climatology needs no weather-forcing
work, so do not bundle radiation or gateway changes into C1. A separate ad-hoc
evaluation implementation is not required merely to keep busy while those
shared prerequisites are unresolved.

The review's proposed lack of pre-2013 training data is contradicted by the live
coverage inventory above; its suggested shorter training split is not adopted.
The review is not approval for implementation or for a new statistical method.

```json
{"cleanup":{"status":"COMPLETE","depends_on":[]},"rehearsal":{"status":"READY","depends_on":["cleanup","deployment_coordination"]},"C1":{"status":"DRAFT","depends_on":["cleanup","qualified_time_and_FI_contract"]},"C2":{"status":"DRAFT","depends_on":["C1","external_training_contract","qualified_radiation_inputs"]},"C3":{"status":"DRAFT","depends_on":["C2","engression_FI_support"]}}
```

Review reports stay outside Git. Record only reviewed decisions and concise
outcomes here; keep this document an execution index rather than duplicating the
time-grid or API specifications.
