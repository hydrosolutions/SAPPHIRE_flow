# Nepal21 milestone map

Related Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/354

Outcome authority: [the single Nepal vision](../visions/2026-10-01-nepal6-current-forecast-data-for-the-existing-dashboard.md).
This is planning/status evidence, not a claim of deployed acceptance. The owner
accepted M0 and authorised M1 preparation. On 2026-10-21 the owner deferred backup
commissioning and recovery testing, including plaintext tests, and confirmed the
hosting assumption covers all 21 stations including historical water levels.
M1 remains active and unaccepted. Independent planning review of the reduced scope
is complete; implementation approval is pending. This documentation update grants
no implementation or deployment authority.

## Milestone outcomes and checkpoints

| ID | State | Exit evidence | Active work estimate |
|---|---|---|---|
| M0 — baseline | Accepted by owner | Existing code/host/archive and 21-station baseline reconciled; preserve existing evidence. Tracker publication is administrative carry-forward in this documentation pass, not a completed M0 GitHub update. | Complete |
| M1 — direct delivery/runtime | Active; acceptance pending | Exact tested default/Aquacast amd64 images privately transferred after owner publication and deliberately activated; intended isolated target/API/held Prefect workers healthy; original archive/UUIDs/RAW/evidence preserved; no unwanted queues across init/restart; measured free delivery capacity and private credential/output controls. Backup/recovery acceptance is deferred, not required or complete. | Reduced-scope re-estimate pending |
| M2 — station/model onboarding | Planned; requires M1 owner acceptance | First candidate then batch; preserved identity/history, owner-agreed provisional-input/reference admissibility; qualified QC/conversion/basin/static and current Nepal forcing (or justified per-model exemption), compatible artifacts/bindings, repeat-safe operations and explicit exclusions. Forecast enablement waits for M3's evaluation disposition. | 2–4 days |
| M3 — onboarding evaluation | Planned; requires M2 owner acceptance | Owner-agreed evaluation periods/baselines/training-overlap treatment; hindcasts/skill with legitimate observations and stated limits, or explicit not-assessable outcome for each candidate. Missing skill alone does not block eligible test use. | 1–3 days plus measured compute |
| M4 — normal end-to-end path | Planned; requires M3 owner acceptance | Manual approved Prefect cycle consumes qualified current forcing/persisted inputs, FI returns real output, service/store persists, normal API serves the existing dashboard. Trace identities/times/means/uncertainty/status and per-model failure isolation. Prove invalid lookback propagation, separate invalid selection/last-good, evaluation/alert exclusion and map-owner invalid display where used. Reject unauthorised/out-of-scope reads without leaks; separate regional credentials. | 1–2 days |
| M5 — unattended operation | Planned; requires M4 owner acceptance | Enable intended Nepal ingestion/forecast schedules after explicit request, excluding backup unless separately reopened and approved; observe repeated cycles over 48–72 hours, missing-input/last-good, restart and failure-handling behaviour; full disaster-recovery proof remains deferred. Across at least two scheduled cycles, trace newly persisted results on dashboard reload with the map owner, without manual export/redeploy. Record actual freshness and operator handoff. | 1–2 days plus observation time |
| M6 — eligible coverage | Readiness investigation ongoing; activation held | All 21 accounted for; extend tested onboarding/evaluation/forecast path to each eligible combination, with actionable exclusions. Report valid, skill-unestablished test and invalid-pathway counts separately. Final acceptance incorporates M5 evidence. Any later stop of a temporary presentation-serving process needs owner permission; dashboards, dirty checkout, snapshots and evidence remain preserved. | 2–4 days, partly overlapping |

Each milestone ends with owner review of evidence, exclusions, risks, estimates and
next actions. No automatic transition to the next milestone. These are outcome
checkpoints, not a separate approval loop for every engineering command. M6
readiness investigation can proceed alongside earlier work; M6 expansion requires
M4 acceptance and repeats applicable M2/M3 prerequisites for newly admitted pairs.
Final M6 completion requires M5 acceptance and unattended evidence for the expanded
cohort; do not inherit healthy-operation proof from different stations/models.

## Deferred work and estimate history — owner update 2026-10-21

Backup commissioning and recovery testing are **DEFERRED, NOT ACCEPTED/COMPLETE**.
They are not dependencies of M1, M2 or the later forecast/dashboard milestones.
This includes Cloudflare allowance/overlap checks, rotating complete recovery sets,
daily and prechange backup commissioning, fresh verified off-host prechange points,
plaintext/timed restore exercises and the former 24-hour/four-working-hour proofs.
The [vision](../visions/2026-10-01-nepal6-current-forecast-data-for-the-existing-dashboard.md)
retains the historical policy and records the operational loss/reconstruction risk.
Cloudflare access and a recovery machine are not active-path blockers. Reopening
this work needs a separate owner decision, review and activation; it is not a new
milestone hidden in the dependency chain.

Preservation remains mandatory: no destructive/in-place changes to the original
archive or protected evidence under this deferral. Any authorised target work is
additive and isolated. Independent custody is recorded, not assumed to exist.

The old 1–2-day runtime estimate and 3–5-day expanded M1 estimate were superseded.
Detailed planning of the former full delivery/recovery scope reached a conditional
**12–23 active-day** envelope, excluding review/access/merge waits and observation;
that is historical full-scope context, not the current reduced-scope estimate.
Deferring recovery does **not** restore the 3–5-day estimate. Re-estimation of the
active delivery/runtime work is pending after its bounded feasibility check.

No ten-working-day M1/M2/M3 completion promise remains. Re-estimate at each owner
checkpoint, separating engineering, hindcast compute, 48–72-hour observation,
review/merge waits and external data dependencies. Earlier first-forecast and
coverage ranges are historical, not deadlines or commitments to all 21 stations.

## Accepted baseline and current gaps

Accepted M0 source comparison used main c29b1c3ad76081bf9b19eb2f7c5f9391cb5c1a13.
At inspection, the isolated archive held 21 onboarding stations, RAW measured
water levels across 18, and no forecast/model/basin/history-forcing integration.
A dump command succeeded during M1 preparation; that is not verified DR.
Backup/restore/off-server recovery acceptance remains outstanding and is now
deferred, not a prerequisite of the additive target path. Original archive and dirty checkout
must remain intact. Do not confuse presentation success with server deployment.

The 21-station roster (codes only; no restricted measurements) is:
225, 280, 350, 404.7, 439.7, 440, 445, 447, 450, 550.05, 602, 602.5, 604.5, 610, 620, 647, 650, 652, 670, 684, 695.

This list comes from the accepted readiness ledger; code spelling is preserved.
Revalidate exact identities against the admitted registry before execution. The private station-readiness
ledger remains the detailed working evidence and is not copied into this public
planning record. Each execution checkpoint supplies a sanitized aggregate/coverage
report with links to controlled evidence, never restricted observations or tokens.

Current priorities: 450/604.5/684 first-path candidates; 447/647/670 known mandatory
recent-discharge gaps; remaining 15 require their own coverage qualification,
including held 280/445/695. Neither archive completeness nor local basin/static
work proves current server inputs. Existing Nepal CLI history calls and operational
weather/config registration need reconciliation; do not execute old plans wholesale.

## Tracker policy

Reuse Effort #354 and its single vision. Update the Nepal entry of Program #352,
retain the historic demonstration scope/date and Swiss independence. Keep #378's
applicable contract work but do not expand Swiss scope or mark its items completed
from Nepal evidence. Milestones here are the technical outcome map; do not create
seven competing Efforts or claim GitHub delivery from a planning PR. No issue is
closed in this publication pass.
