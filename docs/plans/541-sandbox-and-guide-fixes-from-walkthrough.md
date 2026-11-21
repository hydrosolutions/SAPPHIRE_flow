---
status: READY
created: 2026-11-17
revised: 2026-11-17
plan: 541
title: Sandbox exercise and new-station guide fixes from the walkthrough
scope: Record what the guide branch changes in docs/operations/new-station-by-hand.md (edits S1 to S6, already applied there and independently reviewed) for the gaps found when a fresh administrator agent followed the guide from zero, and why. Lists the Gateway-runbook lines that now disagree with the guide; does not edit them. NOT the onboarding commands (Plan 540), NOT production hardening (MVP), NOT the hydrologist pathway design (program #450 job B).
depends_on: ["the station guide docs/operations/new-station-by-hand.md (merged to main in PR #591)"]
blocks: []
related: [450, 452, 540]
open_decisions: []
source: 2026-11-16/17 round-2 walkthrough (fresh agent as administrator, Trishuli 00001), the independent end-state check, the first guide review (findings F1-F16, folded into the guide branch), and the 2026-11-17 administrator and developer reviews of this plan. Number chosen by the orchestrator session 2026-11-17 at the owner's request.
---

# Plan 541 - sandbox exercise and guide fixes from the walkthrough

## Status

**READY** (set by the orchestrator 2026-11-17 after two independent reviews of the exact text: a developer and administrator review, then a fresh final review that found it ready). Docs-only. The guide edits S1 to S6 below are **already applied** on the unmerged guide branch (uncommitted edits in the `sapphire-guide-gaps` worktree) and have been independently reviewed there. This plan documents what they change and why, so a later reviewer can judge them without the session that produced them. Owner decision 2026-11-17: **no separate PR now**; the edits are folded into the later fix PR together with the guide branch.

## Why

A fresh administrator agent followed `new-station-by-hand.md` from zero. It reached a stored forecast, and a second agent confirmed the end state from the database. But it needed several steps the guide did not give, hit errors that hide their cause, and the first teardown stopped unrelated processes. The guide branch already held fixes for the first review round (registration steps, key handling, evidence folder mode, pid-file teardown). This plan covers what round 2 added.

## Dependency on the guide branch

Everything below that says "in the guide" refers to the file on branch `docs/452-guide-gaps-walkthrough`. On `main` the text is absent (for example the `mkdir -m 700` note, the `SANDBOX-REHEARSAL-<date>` placeholder, the `pgrep` leftover check, the `--rm` note). Merge order: the guide branch with these edits first (one PR), then Plan 540's doc edits, which touch the same guide and runbook steps. If the guide branch is abandoned, S4 and S5 must be re-applied on top of `main`.

## Evidence

The walkthrough log was kept as `wt453/LOG.md` in the scratchpad of the 2026-11-17 session. It is **not** in the repository and a later reviewer cannot open it, so the table below is the durable summary. Log line numbers are as cited in the administrator review of this plan; they identify the stuck point, nothing here depends on them.

| # | Gap found | Observed (log) | Handled in |
|---|---|---|---|
| 1 | Station not subscribed to ERA5/IFS parameters; history job failed with "not subscribed to ERA5 parameter" | log 19-27 | S1 (subscribe step) |
| 2 | Manual Gateway dashboard jobs (ERA5 targeted, IFS backfill) not in the guide; no run identifier visible; `history` does not echo the job reference; `resume` said "eligible" while IFS backfill still ran | log 19-27, 31-32 | S1 |
| 3 | First cycles failed with `missing_support` until the Gateway's combined product showed the recent days; dashboard "finished" was not a signal. Cause **inferred**, never observed directly | log 28-32 | S1 (wait rule, read-only check) |
| 4 | Flows hide the real error (`Operational ERA5 history failed`); no way to tell weather from observation failures | log 19, 21, 29 | S2 |
| 5 | Discharge QC: guide's first synthetic series gave 24 suspect days of 31 (spike rule) and flat runs (frozen-sensor rule); suspect days count as gaps (`cadence_mismatch`); only 7 usable days of 30 in the first lookback; final series had every day passed | log 16, 33, 34 | S2 (coverage check), S3 |
| 6 | Discharge not among the station's measured parameters (step 3 lists water level only) | log 33-34 | S3 |
| 7 | Workers need the Gateway key; start commands had to be assembled by hand | log, "ESCALATION 2" | S4, S6 (who-does-what) |
| 8 | Teardown: zsh error on empty `*.pid` glob; macOS `xargs` has no `-r`; pid files hold launchers, real processes die only via `pgrep -P`; database port not checked; `--rm` deletes data | log 36 | S4 |
| 9 | "Degraded" cycle result (`nwp.grid_stale`, `alert.suppressed_fallback_only`) read as failure; station code `00001`; forecast level not matching synthetic series | log, result section | S5 |
| 10 | Evidence folder created with mode 755: tool printed only "Gateway preparation held or failed", empty folder | log, "Stuck 1" | Guide branch round 1 (`mkdir -m 700`) |
| 11 | `reconcile --evidence` wording when only HTTP success exists | log 12 | Guide covers the normal case; the HTTP-only case is a gap, see "Open gaps" |
| 12 | Two step numberings in the runbook (checklist 1 to 7, technical list 2 Prepare, 3 Register, 4 Subscribe) | runbook lines 30 and 155 | S6 |

## Edits on the guide branch

All edits are prose and (for S3) one replacement snippet. They fold into the later fix PR with the guide branch.

### S1 - Gateway data steps the guide skipped

- New section "After registration: subscribe, then start the Gateway data jobs". **Subscribe** the new Gateway name to the ERA5 and IFS parameters (`prepare_gateway subscribe`, preview first, then confirm the printed revision). Without it the history flow fails.
- The **manual Gateway dashboard jobs** the runbook already lists: the targeted ERA5-Land backfill (one manual run for the new basin) and the shared IFS backfill of `ifs__subdaily` (last 7 days, re-process). Only the IFS backfill is a shared Gateway action. Owner decision 2026-11-17: **an administrator may start the IFS backfill under a standing rule and records it in the evidence folder** (time, job, reason). An assistant never starts either job; the person does.
- Says the free-text job reference is accepted, a **run identifier may not be visible**, `history` does not echo the reference back, and `resume` reported "eligible" while the IFS backfill was running, so "eligible" is not a signal to proceed.
- **Wait rule:** the dashboard showing the IFS backfill finished is not enough. Worded as inferred ("if the first cycle reports `missing_support`, wait and retry"). Includes a read-only snippet that asks the Gateway's operational API for the station's precipitation and temperature over the model's 30-day window and lists days without data. It needs the Gateway key, so the person runs it. Plan 540 M4's readiness report is meant to replace it.

### S2 - Understanding and diagnosing a failed run

- Section "When a run fails: finding the real reason". Defines `missing_support` (past weather: no snapshot, window shorter than lookback, no day with samples, a required day lacking precipitation or temperature; future leg: no stored forecast covering the times and members). Says what it does not mean: our code rejecting good data (ruled out offline from the saved Gateway frames).
- Explains the flows hide the error on purpose (the history flow raises only `Operational ERA5 history failed`; the key can appear in a Gateway library address). Names the two log lines that mattered: `operational_inputs.cadence_mismatch_skip` (discharge observations, not weather) and `run_group_forecast.station_inputs_unavailable` (its `reason` field).
- **Coverage check** before the cycle: count daily discharge values with QC status passed **or unchecked** (the cycle accepts both, `services/input_quality.py`) in the model's 30-day lookback; suspect, failed and missing count as gaps and give `cadence_mismatch`. Read-only SQL, expected result 30.

### S3 - Synthetic data that passes QC (interim, until Plan 540 M3)

- Rule: **no noise, no flat runs**. The replacement generator is baseflow plus smooth decaying storm pulses with a minimum 3 mm daily step; a linear reservoir driven by stored ERA5 precipitation is the alternative. The one-run-only warning is kept.
- **Generator evidence (reviewer asked for it):** the guide's generator was run through the real `Stage1QualityChecker` by an independent reviewer: **1723 of 1723 values passed**, for both water level (datum-shifted, rule version `1.2-datum`) and discharge (rule version `1.2`). This is reported by that reviewer and was not re-run for this revision. The guide's snippets also check `1.2-datum`, not `1.2-datum-skip`, in the stored `qc_rule_version`.
- Discharge must be in the station's measured parameters before step 6 (step 3 lists only water level); `forecast_targets` is set in step 8, not step 3.
- States that Plan 540 M3 replaces the snippet with a command that refuses a series that does not pass the real QC.

### S4 - Sandbox lifecycle

- Worker start commands (environment, key read from the key file inside the person's own terminal, pid files); workers and the cycle are started by the person, never the assistant.
- Teardown fixes: blocks are **bash** (zsh stops with "no matches found" on an empty `*.pid` glob; type `bash` first); no `xargs -r` (macOS has none); check the sandbox database port as well as 4299; pid files hold the `uv run` launchers, the real server and workers die only through the `pgrep -P` step; leftover check `pgrep -lf "prefect (worker|server)"`; the database container runs with `--rm`, so `docker stop` also deletes its data.
- Kept: private evidence folder (`mkdir -m 700`), smooth-series note, pid files only, never `pkill -f`, never stop a process you did not start.
- Both start and stop blocks are copy-paste text, not a script (the owner dropped the helper script). Intended check, not yet recorded as run: execute the start and stop blocks once under bash and once under zsh (via `bash` first) and note the result in the fix PR.

### S5 - What a good result looks like

- Station code in the rehearsal is `00001`.
- Explains the "degraded" result: `nwp.grid_stale` (logged as an error, critical in the health table) because the stored grid was 32 to 37 hours old, an older cycle having been replayed with no live grid refresh in the sandbox; `alert.suppressed_fallback_only` because `cmal_small` declares alert eligibility `no_event_information`, not because thresholds are missing; observation and weather staleness are measured against the clock. None of this makes the forecast wrong. The forecast level will not match the synthetic series (Swiss-trained model): plumbing proof only.
- Verified end state as a checklist (one station, rating curve labelled synthetic, every lookback day passed, group and model assignment, three weather bindings, one forecast with quantiles from the real model).

### S6 - Numbering, who-does-what, cross-references

- The runbook has two numberings (admin checklist 1 to 7; technical list 2 Prepare, 3 Register, 4 Subscribe). The guide states "checklist step N" always means the admin checklist; "Step 3" to "Step 8" with no other word are the guide's own.
- A who-does-what table (person versus assistant): key export, workers, cycle, the read-only Gateway check, Gateway dashboard jobs, `prepare_gateway` previews versus shared-Gateway writes, teardown, promotion.
- Cross-references Plan 540 M6 and M8. The "overlay replaces the table wholesale" claim was in the guide and is already corrected there; Plan 540 M6 owns any remaining overlay text.
- Review-reference guidance (administrator review item): a sandbox may use the placeholder `SANDBOX-REHEARSAL-<date>` (owner decision OD3, accepted); a real onboarding must use the real review reference from the Gateway integration review.

### Step-order table of the by-hand steps (administrator review item)

An administrator meets three command styles: `uv run python -m sapphire_flow.cli.<x>` (for example `prepare_gateway`), `scripts/<x>.py` (for example `create_station_group.py`) and `onboard_nepal <subcommand>`, plus inline heredocs. A short table in step order (checklist steps 1 to 7, then guide Steps 3 to 8, each with the command style used) would let the administrator keep them straight until Plan 540 gives a single style. **Not found in the guide branch as of this revision**: see "Open gaps".

## Gateway runbook lines that still demand a go-ahead

**Update (PR #593):** the go-ahead lines below, and the runbook's "The tool lists the job reference" line (the tool saves it in the state file and does not print it back), were aligned with the guide and the code in PR #593. The table is kept as the record of what was changed.

The standing rule (an administrator may start the IFS backfill, recording it in the evidence folder) contradicts these lines of `docs/operations/recap-gateway-runbook.md` as it stands on the guide branch (verified by grep, 2026-11-17):

| Line | Wording |
|---|---|
| 100-103 | "the **owner or the Gateway admin** starts it, not the station admin alone ... If that is not confirmed, stop." |
| 111 | "do not start the IFS backfill without the owner's or the Gateway admin's go-ahead" |
| 122 | In **Rules that never change**: "the owner or Gateway admin starts it (see Step 5 in detail)" |
| 146-147 | "The 7-day IFS backfill needs the owner's or Gateway admin's go-ahead" |
| 239-240 | "It needs the owner's or Gateway admin's go-ahead" |

Line 122 sits in a list titled "Rules that never change", so aligning it changes a rule, not just a sentence. **Aligning the runbook is for whoever owns Gateway onboarding** (another agent is working on that runbook); this plan only lists the lines and does not edit them. Until then the guide says plainly that the runbook still demands a go-ahead and is being aligned. The runbook's own check ("check which basins are subscribed, ask which outputs will be overwritten, agree a time with other deployments' owners") is useful for an administrator too and is not contradicted by the standing rule; whoever aligns it decides whether to keep it as a recommendation. `tests/unit/docs/test_recap_runbook.py` does not pin these lines (checked by the developer review).

## Open gaps (not yet in the guide branch)

Listed so the fix PR can close them; each is prose only.

- **Step-order table** with the three command styles (above).
- **`reconcile --evidence` when only HTTP success exists:** the guide says to write one honest sentence tying the Gateway admin-panel entry to the upload. It does not say what to write when only the HTTP success is known and the panel entry cannot be checked. Needs a one-line rule (for example: state exactly what was observed and by whom, and do not claim panel confirmation).
- **Expected output of the read-only Gateway check** (the "every day has data" line is described, not shown) and **one example `station_inputs_unavailable` log line** with its `reason` field.
- **Gateway data wait and failure prose** exists (wait rule, "when a run fails"); the administrator review asked that the wait signal be stated as "all days present", not the dashboard. The guide says so; keep it when editing.
- **Test the shell blocks** (start and stop) under bash and zsh, noting that teardown must run under bash.

## What belongs to Plan 540, not here

Gateway key from a file (M0), the QC-clean synthetic generator (M3), the readiness report with the coverage count (M4), usable Gateway-prepare messages and the example scope file (M8), the Plan 540 doc edits to the same guide and runbook steps. Cross-references only.

## Acceptance

- Each of S1 to S6 above is present on the guide branch, with no claim that was not observed in the log (see the evidence table) or verified in code.
- The independent review of the guide branch edits is recorded (below); findings folded.
- The "Open gaps" are closed or consciously deferred in the fix PR.
- The runbook go-ahead lines are aligned by the Gateway onboarding owner, or the guide keeps its "being aligned" note.
- Optional, owner's call: a last fresh walkthrough after Plan 540 M0 and M3 land.

## Open decisions

- **OD1 (decided 2026-11-17).** Prose only; no helper script. The helper idea (formerly S7) is dropped.
- **OD2 (decided 2026-11-17).** Standing rule: an administrator may start the shared IFS backfill without asking each time, and records it in the evidence folder (time, job, reason). The ERA5 targeted run is the administrator's own. Consequence: the runbook lines listed above need aligning by the Gateway onboarding owner.
- **OD3 (decided 2026-11-17).** The `SANDBOX-REHEARSAL-<date>` placeholder is the standing sandbox review reference; real onboardings need a real review reference.
- **OD4 (decided 2026-11-17).** No separate PR now; these edits fold into the later fix PR with the guide branch.

## Risks

- R1. Guide prose drifts from the Gateway runbook; until the listed lines are aligned the two documents disagree on who may start the IFS backfill. The guide states the disagreement openly.
- R2. The read-only Gateway check is a snippet, not a tested tool; if the Gateway API call changes, it breaks silently. Mitigation: Plan 540 M4 replaces it.
- R3. The synthetic generator is evidenced by one reviewer's run (1723 of 1723 values). A change to the QC rules invalidates that evidence until Plan 540 M3 replaces the snippet.

## Review record

- **2026-11-17, administrator review** (a fresh agent as administrator, read both plans and the walkthrough log): verdict READY after small fixes. Findings folded here: who-does-what table, Gateway data wait and failure prose, `reconcile --evidence` with HTTP success only, real-onboarding review reference, shell blocks tested under bash and zsh, step-order table with three command styles, runbook alignment for the standing rule.
- **2026-11-17, developer review** (checked against main 98ae8af86, read only): verdict READY after fixes. Findings folded here: stale optional-helper wording removed, guide-branch dependency made explicit, runbook go-ahead line list corrected and completed, evidence moved into this plan, generator verified through the real QC, overlay item dropped (already corrected on the guide branch).
- Independent review of the guide branch edits S1 to S6: done on the guide branch, findings folded there. Independent review of this plan's final text: still required before READY. The orchestrator sets READY.
