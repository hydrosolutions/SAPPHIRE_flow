# Nepal21 milestone map

Related Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/354

Outcome authority: [the single Nepal vision](../visions/2026-10-01-nepal6-current-forecast-data-for-the-existing-dashboard.md).
This is planning/status evidence, not a claim of deployed acceptance. The owner
accepted M0 and authorised M1 preparation. M1's new delivery/recovery requirements
are confirmed; its implementation plan remains a proposal pending review/merge.

## Milestone outcomes and checkpoints

| ID | State | Exit evidence | Active work estimate |
|---|---|---|---|
| M0 — baseline | Accepted by owner | Existing code/host/archive and 21-station baseline reconciled; preserve existing evidence. Tracker publication is administrative carry-forward in this documentation pass, not a completed M0 GitHub update. | Complete |
| M1 — runtime/recovery | Active; acceptance pending | Exact CI-tested images privately transferred and deliberately activated; intended DB/API/Prefect workers healthy; no unwanted schedules; first Cloudflare recovery set verified and successor behaviour proven; timed off-host-only restore against four-working-hour/24-hour targets; daily backup enabled and verified pre-change point recorded; measured free capacity. | 3–5 working days |
| M2 — station/model onboarding | Planned; requires M1 owner acceptance | First candidate then batch; preserved identity/history, owner-agreed provisional-input/reference admissibility; qualified QC/conversion/basin/static and current Nepal forcing (or justified per-model exemption), compatible artifacts/bindings, repeat-safe operations and explicit exclusions. Forecast enablement waits for M3's evaluation disposition. | 2–4 days |
| M3 — onboarding evaluation | Planned; requires M2 owner acceptance | Owner-agreed evaluation periods/baselines/training-overlap treatment; hindcasts/skill with legitimate observations and stated limits, or explicit not-assessable outcome for each candidate. Missing skill alone does not block eligible test use. | 1–3 days plus measured compute |
| M4 — normal end-to-end path | Planned; requires M3 owner acceptance | Manual approved Prefect cycle consumes qualified current forcing/persisted inputs, FI returns real output, service/store persists, normal API serves the existing dashboard. Trace identities/times/means/uncertainty/status and per-model failure isolation. Prove invalid lookback propagation, separate invalid selection/last-good, evaluation/alert exclusion and map-owner invalid display where used. Reject unauthorised/out-of-scope reads without leaks; separate regional credentials. | 1–2 days |
| M5 — unattended operation | Planned; requires M4 owner acceptance | Enable intended Nepal schedules after explicit request; observe repeated cycles over 48–72 hours, missing-input/last-good and restart/recovery behaviour. Record actual freshness and operator handoff. | 1–2 days plus observation time |
| M6 — eligible coverage | Readiness investigation ongoing; activation held | All 21 accounted for; extend tested onboarding/evaluation/forecast path to each eligible combination, with actionable exclusions. Report valid, skill-unestablished test and invalid-pathway counts separately. Final acceptance incorporates M5 evidence. Any later stop of a temporary presentation-serving process needs owner permission; dashboards, dirty checkout, snapshots and evidence remain preserved. | 2–4 days, partly overlapping |

Each milestone ends with owner review of evidence, exclusions, risks, estimates and
next actions. No automatic transition to the next milestone. These are outcome
checkpoints, not a separate approval loop for every engineering command. M6
readiness investigation can proceed alongside earlier work; M6 expansion requires
M4 acceptance and repeats applicable M2/M3 prerequisites for newly admitted pairs.
Final M6 completion requires M5 acceptance and unattended evidence for the expanded
cohort; do not inherit healthy-operation proof from different stations/models.

## Two-week planning horizon, not a fixed completion promise

The revised M1 adds private release delivery and full recovery to the original
runtime-only estimate of 1–2 days. That earlier estimate is superseded. In the
next roughly ten working days, aim to complete M1, M2 and evaluation preparation/
M3 as measured inputs and owner checkpoints permit; reach M4 if they fit. Do not
hide additional work by compressing later milestones or promising all 21 will run.

Re-estimate at each checkpoint. Separate engineering days, hindcast compute,
48–72-hour observation, owner review/merge waits and external data dependencies.
Earlier first-forecast estimates of 6–10 days and broader-coverage estimates of
10–15 days were provisional before this M1 refinement, not current deadlines.

## Accepted baseline and current gaps

Accepted M0 source comparison used main c29b1c3ad76081bf9b19eb2f7c5f9391cb5c1a13.
At inspection, the isolated archive held 21 onboarding stations, RAW measured
water levels across 18, and no forecast/model/basin/history-forcing integration.
A fresh dump command succeeded during M1 preparation; integrity, isolated restore
and off-server recovery remain outstanding. Original archive and dirty checkout
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
