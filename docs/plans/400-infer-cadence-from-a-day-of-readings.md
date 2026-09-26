---
status: DRAFT
created: 2026-09-26
revised: 2026-09-26
plan: 400
title: Work out a series' reporting interval from a day of readings, not two hours
scope: Widen the observation-QC context window from 2 h to 24 h so that cadence inference over a series with occasional missing readings recovers the true cadence, and — once that window exists — add the hourly `frozen_sensor` rows Plan 323 deferred. NOT how selection MATCHES a cadence (exact equality stays), NOT nearest-rule matching (no plan owns it; this plan makes it unnecessary for the measured cases), NOT the network dimension of selection (264), NOT `rate_of_change`'s arithmetic (313), NOT the onboarding QC path (315), NOT forecast QC, NOT re-QC of stored history, NOT any 600 s or 86400 s threshold.
depends_on: [323]
blocks: []
related: [264, 272, 313, 315, 317, 318, 323]
open_decisions: []
source: 2026-09-25 — the owner, after an independent review of Plan 323 found that hourly rules alone leave ~5% of hourly checks with no rule and the Plan 318 watchdog failing ~64% of the time. Owner's words — "first accept the leftover alert rate and then fix how it works out the interval for hourly stations … so 2 plans". Plan number granted by the owner 2026-09-25. Every figure in § What is measured was measured on 2026-09-25 against the staging store (running `main` at `7a7f2aae`) and the code at `main` `2fe660e2`; each says how.
---

# Plan 400 — work out a series' reporting interval from a day of readings, not two hours

## Status

**DRAFT.** ⛔ No implementation until an independent review of this exact state is complete and the
orchestrator sets READY. It runs **after Plan 323** (owner, 2026-09-25): 323 adds the hourly rules and
accepts a leftover; this plan closes the leftover.

## Why this plan exists

Observation QC infers a series' cadence from the median gap between the readings in its QC window,
then selects the rules declared for exactly that cadence (`services/qc.py:47-69`,
`types/domain.py:160-167`; the owner closed the cadence as *inferred, not declared* on 2026-09-19,
Plan 272 D1). The window is `[now − 2 h, now + 1 h]` (`flows/ingest_observations.py:431-432`,
`context_window_hours = 2.0` at `:428` and `:743`).

For a 10-minute series that window holds ~13 readings and the median is robust. For an hourly series
it holds **three**, so two gaps — and one missing reading makes the median 7200 s, which no rule
declares. The check then stores the reading `QC_UNCHECKED` and the watchdog reports it. Plan 323
§ (10) measured this at ~5% of the hourly stations' checks, enough to keep the watchdog failing
most of the time.

⭐ **The cadence is not wrong in the data; the sample is too small.** Over a day the same series has
~20 gaps and the median is 3600 s every time (§ 1). The fix is to look at more of the series, not to
loosen what counts as a match.

## What is measured

1. **Inference accuracy by window length.** Replay: for each stored reading, infer the cadence from
   the distinct timestamps in the preceding window of the stated length (the production function's
   logic: median of gaps between distinct instants), and compare with the series' own multi-day
   median.

   | window | 10-min groups (333 groups, 142,399 checks, 3 days) | hourly groups (9 groups, 2,679 checks, 14 days) |
   |---|---|---|
   | **2 h (today)** | 99.96% | **95.33%** |
   | 6 h | 100.00% | 95.97% |
   | 12 h | 100.00% | 99.78% |
   | **24 h** | **100.00%** | **100.00%** |
   | 2 h, minimum gap instead of median | 92.63% | 96.82% (3-day) |
   | 2 h, most frequent gap | 99.99% | 96.82% (3-day) |

   ⇒ Only a longer window reaches 100% on both; changing the statistic inside a 2 h window does not
   (the minimum gap is *worse* at 600 s, because jittered timestamps produce short gaps). 6 h barely
   helps hourly — a 6 h window still holds only ~6 readings and two gaps of 7200 s move the median.
   The replay ignores series with a single reading in 4 days (one station, three parameters, last
   reading 2026-09-21), which infer `None` under every window and are correctly unchecked.
2. **It also closes the 10-minute residue.** The 0.04% of 10-minute checks that miss today are the
   same mechanism at a smaller scale (Plan 272 measured ~3.5% of windows on a gappy DHM 10-minute day,
   `docs/requirements/dhm-api-examples/day.json`). 24 h removes them in the Swiss replay; the DHM day
   is one day long and cannot show a 24 h window, so T1 does not claim it.
3. **What the window feeds besides inference.** `_run_qc_task` fetches the window unfiltered, runs
   every selected rule over it, and persists verdicts only for `RAW`/`QC_UNCHECKED` rows
   (`flows/ingest_observations.py:448-470`, the Plan 317 pick-up set). Per rule:
   - `range_check`, `gross_outlier` — per reading; the window length is irrelevant.
   - `rate_of_change`, `spike` — compare a reading with its immediate neighbours, which the 2 h window
     already holds; unchanged.
   - `frozen_sensor` — counts a run of near-identical readings **inside the window**
     (`services/qc.py:152-210`). This is the one rule whose result depends on window length.
4. **`frozen_sensor` at 600 s, 2 h vs 24 h window** (replay over the preceding 3 days at the
   configured thresholds; a reading counts as flagged when the run ending at it reaches
   `min_consecutive` — an approximation of `_apply_frozen_sensor`, which flags every reading in such
   a run):

   | parameter | checks | flagged, 2 h | flagged, 24 h | note |
   |---|---|---|---|---|
   | discharge (0.001, 12) | 57,271 | 4,800 | 4,866 | +1.4% |
   | water_level (0.001, 12) | 58,559 | 10,349 | 9,073 | −12%: a longer window anchors a run earlier, so slow drift leaves the tolerance sooner |
   | water_temperature (0.01, 18) | 26,569 | **0** | 2 | 18 readings = 3 h exceeds the 2 h window, so today this rule **cannot fire**; 24 h switches it on |

   ⇒ Widening the window changes live verdicts at 10-minute stations, in both directions. T1 states
   this and T3 re-measures it; it is the price of the fix, not a side effect to hide.
   ⚠️ The high base rate (8% / 18%) is the drought — flat water at a 1 mm / 1 L tolerance — and is
   Plan 323 § (11)'s concern, not this plan's.
5. **Load.** Ingest runs every 5 minutes by default (`cli/register_deployments.py:44`) and calls
   `_run_qc_task` for every eligible `(station, parameter)` (`flows/ingest_observations.py:931-943`),
   each doing one windowed fetch. At 24 h a 10-minute group fetches ~145 rows instead of ~13 — about
   **50,000 rows per run instead of ~4,500** across 342 groups. Probably fine for Postgres; not
   measured, so T3 measures it.
6. **Plan 317's pick-up set widens with it.** A `QC_UNCHECKED` reading is re-judged while it is inside
   the window, so it is re-examined for 24 h instead of 2 h. For a transient cause that is the
   intent; for a series whose cadence no rule declares, the same rows are rewritten for 24 h —
   ~12× the writes for that group. Recorded, accepted as small.

## Design

**Change the window, not the matcher.** Raise the `context_window_hours` default from 2.0 to 24.0 in
both places it is declared (`flows/ingest_observations.py:428` and `:743`). Nothing else in the
selection path changes: inference stays the median of distinct gaps, matching stays exact equality,
the pick-up set stays `{RAW, QC_UNCHECKED}`.

**Rejected — infer from a longer lookback but run the rules on the 2 h window.** It would leave
`frozen_sensor` untouched at 10-minute stations, but it needs the inferred cadence passed into
`QualityChecker.check`, which changes the Protocol (`protocols/stores.py`) and every caller —
ingest, onboarding (`services/onboarding.py`) and `scripts/dhm_precip/` — which is exactly the seam
Plan 264's in-flight rewrite is changing. It also keeps `frozen_sensor` unable to see a day, which
is the second half of this plan's purpose.

**Rejected — nearest-rule matching.** It changes selection for every station, and the measurement
shows it is not needed for any observed case once the sample is large enough (§ 1).

## Owner decisions

None open. The two decisions this plan rests on are the owner's and closed:
- **The split** (2026-09-25): Plan 323 accepts the leftover; this plan fixes inference.
- **Hourly `frozen_sensor` waits for this plan** (2026-09-25, Plan 323 D2), with its count set from
  the gauges' measured flat stretches (Plan 323 D1).

⚠️ If review finds the § 4 behaviour change at 10-minute stations unacceptable, that is a new owner
decision, not a detail to fold.

## Tasks

### T1 — Widen the QC context window to 24 h

**Outcome.** A series with an occasional missing reading infers its true cadence, so an hourly
station's checks select their rules on every run where a day of history exists.

**In.**
- `context_window_hours` default 2.0 → 24.0 at `flows/ingest_observations.py:428` and `:743`.
- The observation-ingest map in `docs/touchpoint-maps.md` (beside the Plan 317 entry) and Stage 1 QC
  (step 2.3) in `docs/architecture-context.md`: the window length, why it is a day (§ 1), and that
  `frozen_sensor` is the one rule whose result depends on it (§ 3). Replace Plan 323 T3's "until
  Plan 400 lands" sentence with the landed state.

**Out.** ⛔ Any change to `infer_time_step`, `rules_for`, `resolve_selection` or the pick-up set.
⛔ The onboarding path (`services/onboarding.py`) — its window is its own (Plan 315). ⛔ Any threshold.

**Pre-change.** A RED test: an hourly series with readings on the hour from −23 h to 0 h **except
−1 h**, the 0 h reading pending, `now` = 0 h, checked through `_run_qc_task` with the default window
and the rule set loaded from the shipped `config.toml` (which after Plan 323 declares 3600 s rows).
Today the 2 h window holds −2 h and 0 h only — one gap of 7200 s — so **no rule is selected** and the
pending reading is stored `QC_UNCHECKED`. With 24 h the gaps are twenty-one of 3600 s and one of
7200 s, median 3600 s. ⚠️ The test must fail on that status, not on a missing fixture or config key.

**Verification.**
- The RED test passes: the pending reading gets a real verdict and `resolve_selection` reports the
  3600 s cadence.
- A 10-minute group's selection is unchanged (asserted), and a series with fewer than two distinct
  readings in 24 h still infers `None` and stays `QC_UNCHECKED` — the Plan 272 fail-closed property
  must survive a wider window.
- The existing `tests/unit/flows/test_ingest_observations_recheck.py` catch-up test passes an
  explicit `context_window_hours=2.0` and must stay green unchanged — it tests the widening
  mechanism, not the default.

### T2 — Add the hourly `frozen_sensor` rows (Plan 323 D2)

**Outcome.** Hourly discharge, water_level and water_temperature are checked for a stuck sensor over
a span the window can actually hold.

**In.**
- Three `[[qc_rules.rules]]` rows, `rule_id = "frozen_sensor"`, `time_step_seconds = 3600`, for
  `discharge`, `water_level` and `water_temperature`. `tolerance` as the same parameter's 600 s row
  (0.001, 0.001, 0.01).
- `min_consecutive` derived from Plan 323 T1's **longest flat run in hours** per parameter, converted
  to a reading count at 3600 s, with the T1 statistic cited in a comment beside it (Plan 323 D1's
  rule: no invented numbers).
- 🔴 **Stop condition:** a count must be ≤ the readings a 24 h window holds (~24). If Plan 323 T1's
  measured flat runs put the count above that, the rule could never fire — stop and escalate to the
  owner rather than write an inert row.

**Out.** ⛔ The 600 s `frozen_sensor` rows, including water_temperature's 18 (§ 4 records what the
wider window does to them; retuning them is not this plan). ⛔ `exclude_at_or_below`.

**Pre-change.** A RED test against the shipped `config.toml`: an hourly discharge series flat for
longer than the chosen count selects no `frozen_sensor` rule today and is not flagged.

**Verification.**
- A flat hourly series of `min_consecutive` readings is flagged `QC_SUSPECT`; one reading shorter is
  not — both edges, so the count cannot be off by one or by an order of magnitude unnoticed.
- The hourly groups select exactly Plan 323's rows plus `frozen_sensor`, asserted by `rule_id`.

### T3 — Prove it on staging

**Outcome.** The live defect is closed, and the cost of the fix is measured rather than assumed.

**In.** Over the first full 24 h after deploy (the window must fill before inference can use it):
- Zero-rule checks for the five hourly stations: **none** whose inferred cadence is a multiple of
  3600 s. Any remaining ones are listed with their cause.
- The Plan 318 watchdog posts its `observation QC RECOVERED` message and stays quiet for the rest of
  the 24 h. ⭐ This is the owner-visible outcome the whole two-plan split exists for.
- `frozen_sensor` flags at 10-minute stations over that day, compared with the § 4 replay — same
  direction and order of magnitude; report the per-parameter numbers.
- `ingest-observations` flow-run duration: median and maximum over the day before and the day after.
  If the median more than doubles, report it to the owner before closing.

**Out.** ⛔ Tuning anything in response — findings go to the owner.

**Pre-change.** The same four numbers for the 24 h before deploy, recorded first.

**Verification.** Each number recorded in this plan with the query that produced it, so a reviewer
can re-run it.

## Explicitly out of scope

- **Nearest-rule matching** — no plan owns it; § 1 shows it is unnecessary for every observed case.
- **The network dimension of selection** — Plan 264. ⚠️ Its in-flight rewrite (uncommitted, another
  session, 2026-09-25) states "the default scheduled window remains three hours". Whichever of 264
  and this plan lands second must update that sentence.
- **`rate_of_change` dividing by elapsed time** — Plan 313. A wider window does not change which
  neighbours `rate_of_change` compares, so the interaction is unchanged from Plan 323's note.
- **Onboarding's QC** — Plan 315.
- **Climatological baselines** for the series that have none (Plan 323 § 12).
- **Re-QC of stored history** — the owner's standing answer (Plan 323, Plan 315 D3): leave it.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false},
    {"phase": 2, "tasks": ["T2"], "parallel": false},
    {"phase": 3, "tasks": ["T3"], "parallel": false}
  ]
}
```
