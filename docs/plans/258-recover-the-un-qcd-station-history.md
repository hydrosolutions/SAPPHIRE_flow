---
status: BLOCKED
created: 2026-09-08
plan: 258
title: Recover the three staging stations whose history QC never processed
scope: Run QC over the untouched `raw` pre-2026 observations of 2041, 2116 and 2615 so the baselines, flow regime and climatology floor that follow can exist, making three nominally-operational stations genuinely operational. Explicitly NOT a change to the QC rules, NOT a re-QC of rows already carrying a non-`raw` status, NOT any change to the other 143 stations, NOT a status change for any station.
depends_on: [256]
blocks: []
source: 2026-09-08 — split out of Plan 256 after an independent Codex review found the remediation task unimplementable while its cause was unknown, and that a READY plan may not defer its own inputs (`docs/workflow.md:129`).
---

# Plan 258 — recover the three stations whose history QC never processed

## Status

🚧 **BLOCKED on Plan 256 T3.** Not implementable until the cause analysis reports. This plan exists
so the remediation has an owner and a number, not so it can be started.

Splitting it out was a review finding: Plan 256's earlier T4 scoped its own input as "to be specified
by T3" and required later amendment plus re-review. `docs/workflow.md:129` requires the implementing
agent to implement **every** task and to stop rather than claim completion on anything unresolved, so
a plan containing that task could not be marked READY.

## Why this exists

Five staging stations carry `station_status = 'operational'` with no ACTIVE `climatology_fallback`
artifact. The cause is upstream of every symptom, and the partition across the fleet is exact:

| pre-2026 rows ever QC-processed | stations | have `clim_baselines` |
|---|---|---|
| no | **5** — 2041, 2116, 2392, 2615, 2623 | **0** |
| yes | 143 | **143** |

No QC-passed observations → no `clim_baselines` → no `flow_regime_configs` → no trainable target →
no climatology floor → the gate at `services/onboarding.py:1205` correctly refuses promotion.

**Three of the five are recoverable from data already on disk:**

| station | pre-2026 rows | state |
|---|---|---|
| **2041** Oberriet-Blatten | 14 610 | all still `raw` |
| **2116** Koblenz | 14 610 | all still `raw` |
| **2615** Basel-Klingenthalfähre | 9 497 | all still `raw` |

For comparison, station 2009 carries 14 519 `qc_passed` + 91 `qc_suspect` alongside its raw rows.
These three need no new observations — only the QC pass that never ran.

**2392 and 2623 are out of scope**: they have no pre-2026 history at all (their record begins
2026-09-02), so there is nothing to re-process. Their disposition is decided in Plan 256 — left
`operational` so they keep collecting, because ingest polls only operational stations and BAFU
history cannot be back-fetched.

## What must be settled before this plan can be written

Plan 256 T3 must report **why** Step 5 skipped three stations that had rows. Each station is wrapped
in `try/except` whose message is appended to `errors` — a list the Prefect flow reduces to a count
(the CLI does print it, `scripts/onboard.py:374`) — and that run's logs are gone.

Fixing the symptom without the cause would leave the next onboarding run free to repeat it silently
on different stations. That is the entire reason this plan is blocked rather than drafted.

## Tasks

**Deliberately unwritten.** The remediation's shape — a targeted re-onboarding, a standalone QC pass,
or a data repair — depends on what T3 finds. Writing tasks now would be guessing, and a plan whose
tasks are guesses cannot carry a meaningful exit gate.

When Plan 256 T3 reports, this plan gains tasks with an exact command and station-specific pre/post
SQL, and is then reviewed as a new plan rather than amended in place.

## Verification this plan will have to meet

Recorded now so the eventual tasks are held to it:

- **Station-specific, not aggregate.** For each of 2041, 2116, 2615: QC-passed rows > 0,
  `clim_baselines` rows > 0, a `flow_regime_configs` row, and an ACTIVE `climatology_fallback`
  artifact.
- **Non-interference.** The other 143 stations' artifact and baseline counts are unchanged.
- ⛔ A bare *"`operational`-without-floor count drops from 5 to 2"* is **not** sufficient — it would
  pass if the wrong three stations acquired artifacts.

**Pre-change evidence, already measured (2026-09-08):** that count is 5, and the 143-vs-5 partition
above is the discriminating fact.
