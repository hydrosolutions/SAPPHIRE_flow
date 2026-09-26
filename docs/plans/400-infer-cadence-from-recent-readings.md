---
status: DRAFT
created: 2026-09-26
revised: 2026-09-26
plan: 400
title: Work out a series' reporting interval from its recent readings, not from the two-hour check window
scope: Infer each observation group's cadence from a separate, bounded, CONFIGURABLE look-back of its most recent readings (default the last 50 distinct readings within 30 days), while the rules keep running on today's two-hour check window. This removes the zero-rule checks that a single missing reading causes at hourly stations. NOT the check window itself (it stays 2 h — the owner rejected widening it, 2026-09-26), NOT how selection MATCHES a cadence (exact equality stays), NOT nearest-rule matching (no plan owns it; unnecessary for every measured case), NOT an hourly `frozen_sensor` rule (needs a longer check window; no plan yet), NOT the network dimension of selection (264), NOT `rate_of_change`'s arithmetic (313), NOT the onboarding QC path (315), NOT forecast QC, NOT re-QC of stored history, NOT any threshold.
depends_on: [323]
blocks: []
related: [264, 272, 313, 315, 317, 318, 323, 403]
open_decisions: []
source: 2026-09-25 — the owner, after an independent review of Plan 323 found that hourly rules alone leave ~5% of hourly checks with no rule and the Plan 318 watchdog failing ~64% of the time — "first accept the leftover alert rate and then fix how it works out the interval for hourly stations … so 2 plans". Number granted by the owner 2026-09-25. 2026-09-26, after independent Claude + Codex reviews of the first draft (which widened the check window to 24 h): the owner chose a separate inference look-back instead (option B) and required it to be configurable. Figures in § What is measured come from staging data pulled 2026-09-25 (staging running `main` at `7a7f2aae`) and code at `main` `2fe660e2`; each says how.
---

# Plan 400 — work out a series' reporting interval from its recent readings, not from the two-hour check window

## Status

**DRAFT — redesigned 2026-09-26.** The first draft widened the check window to 24 h. Both
independent reviews found that doing so changes what the rules compare — not only how the interval
is inferred — and the owner chose the narrower design below. Seven review rounds have run (§ Review
record); rounds 6, 7 and 8 READY from both reviewers, and round 8 CLEAN on the text round 7's fold
produced. Only this status paragraph and the review record have changed since. ⛔ No implementation until
an independent review of this exact state is complete and the orchestrator sets READY. It runs
**after Plan 323**.

⚠️ **What it cannot fix alone:** at a water-level station with **no datum** — every Swiss river
station today (Plan 323 D4) — only neighbour-comparing rules run, so the reading after a missed hour
has nothing to judge it however well the cadence is inferred. Plan 323 D4/D5 stores it
`QC_UNCHECKED` in a separate, non-alarming record; **Plan 403** (datums) is what ends it.

## Why this plan exists

Observation QC infers a group's cadence from the median gap between the distinct timestamps in its
check window, then selects the rules declared for exactly that cadence (`services/qc.py:47-69`,
`types/domain.py:160-167`; cadence stays *inferred, not declared* — owner, Plan 272 D1). The check
window is `[now − 2 h, now + 1 h)` (`flows/ingest_observations.py:431-432`; the store's start bound
is inclusive and its end exclusive, `store/observation_store.py:190-191`), and the **same** fetch
feeds both inference and the rules.

For an hourly series run at `now` = a few minutes past the hour, that window holds the **two** most
recent readings. One missing reading leaves one, which infers `None`; a run that lands exactly on
the hour sees three and a gap of 7200 s. Either way no rule is selected, the reading is stored
`QC_UNCHECKED`, and the watchdog reports it (Plan 323 § 10).

⭐ **The cadence is not wrong in the data; the sample is too small.** The fix is to infer from more of
the series — without changing what the rules look at.

⭐ **This revives a design Plan 272 drafted and parked.** 272 § "The inference lookback, bounded"
(archived plan, around line 1209) specified a separate bounded fetch — 30 days, 50 rows, SQL
`LIMIT` — and 272's closure marked it *"SUPERSEDED by 323 … a revival needs a NEW argument; it was a
robustness improvement, never a correctness fix."* **The new argument:** at hourly cadence the check
window holds two or three readings, so a single missing reading is enough to leave a gauge
unchecked. Measured: ~5% of all hourly checks, enough to keep the watchdog failing most of the
time (Plan 323 § 10). That is an operability defect, not a robustness nicety.

## What is measured

1. **Which inference sample recovers the cadence.** Replay over staging data: for each stored
   reading, infer the cadence from a sample ending at that reading (median of gaps between distinct
   instants, as `infer_time_step` does) and compare with the series' own multi-day median.

   | inference sample | 10-min groups (333 groups, ~142k checks, 3 days) | hourly groups (9 groups, 14 days) |
   |---|---|---|
   | 2 h window (today's shape) | 99.96% | **95.33%** |
   | 6 h / 12 h / 24 h window | 100 / 100 / 100% | 95.97 / 99.78 / 100% |
   | 2 h, minimum gap / most frequent gap | 92.63% / 99.99% | 96.82% / 96.82% (3 days) |
   | **last 10 distinct readings** (≤ 30 d) | 99.98% | 99.75% |
   | **last 25 distinct readings** (≤ 30 d) | **100.00%** | **100.00%** |
   | **last 50 distinct readings** (≤ 30 d) — the default | **100.00%** | **100.00%** |

   ⇒ A count-capped sample scales with the cadence — 50 readings is ~8 h at 600 s, ~2 days at
   3600 s, and a daily series' last 30 days within the time bound — and reaches 100% on both
   measured cadences. Changing the statistic inside the 2 h window does not. The count-capped
   rows were replayed on 2,376 hourly checks (14 days less a 60 h warm-up) and 141,733 10-minute
   checks. ⚠️ **Anchoring:** every row is anchored at a stored reading's timestamp, not at a
   5-minute run clock (§ Why), and uses readings as stored now rather than as available at run
   time; the figures estimate production's rate, they do not reproduce it. T2's pre-merge replay
   uses the production bounds.
2. **Where selection changes, and where the replay cannot say.** In the replay, every check whose
   2 h inference matched the series' own multi-day cadence also matches under the last-50 sample.
   That covers the compared population only. Two populations it does **not** cover:
   - **After Plan 323, a check can select non-zero rules for the wrong cadence** — e.g. a
     10-minute series whose 2 h window infers 3600 s now selects hourly rules. The look-back may
     move it back to 600 s: a non-zero → non-zero change. T2's pre-merge replay reports these.
   - 🔴 **A station that changes its reporting interval.** The median of the last 50 readings
     trails a switch by ~25 readings: after 10 min → hourly, the 600 s rules keep running on an
     hourly series for about a day; after hourly → 10 min, the 3600 s rules run for ~4 h. Today's
     2 h window switches within ~2 h. The same holds for a station whose recent history was
     imported at another cadence. And as Plan 272 recorded, N also bounds how long a
     *degradation* episode can be repaired: once off-grid gaps are the majority of the last N,
     the look-back returns the off-grid median too. ⇒ Accepted consequences of option B,
     stated; `max_readings` is configurable (owner) and is the lever. T2 tests a switch; T4
     lists every group whose inferred cadence changed.
3. **What changes for the checks it does repair (Plan 272 C3, bounded).** A check that today infers
   `None` or an off-grid gap because of a missing reading will now select its cadence's rules, and
   those rules run on the unchanged 2 h window. `range_check` and `gross_outlier` judge each
   reading alone — except for water level without a datum, where both are skipped
   (`services/qc_datum.py:32-35`) and a lone reading stays unjudged (Plan 323 D4). **`rate_of_change` and `spike` compare a reading with its neighbours in that
   window — which, across the missing reading, are further apart than the cadence the thresholds
   were sized for,** and `rate_of_change` compares raw differences without dividing by elapsed time
   (Plan 313). So a repaired check can raise a `QC_SUSPECT` that a gap-free series would not.
   Bounded in the ordinary case: the neighbour is at most one window (2 h) away, and it affects
   only the repaired checks — ~4.7% of hourly checks and ~0.04% of 10-minute checks in the replay.
   ⚠️ **Not bounded on the DHM catch-up path:** there the check window widens to cover the
   recovered readings (`flows/ingest_observations.py:433-445`) and the rules compare adjacent rows
   with no gap limit (`services/qc.py:331-340`), so a repaired check can compare across a longer
   gap. T2 tests that case; T4 counts it. ⚠️ Stated, not
   hidden; T4 counts it on staging.
4. **Daily series may start being checked.** A daily series has at most one reading in a 2 h window,
   so today it infers `None` and is never checked. With a 30-day, 50-reading sample it infers
   86400 s and selects the daily rules; per Plan 272's table only `range_check` and
   `gross_outlier` can then fire (one reading in the window gives `rate_of_change` and `spike` no
   neighbours). The replay above did not include daily groups (none were in the river-parameter
   pull); **T2's pre-merge replay must list any that exist on staging**.
5. **Cost.** One extra indexed query per `(station, parameter)` that received rows in the run
   (`station_params`, `flows/ingest_observations.py:902-904` — not the whole fleet), every 5 minutes
   (`cli/register_deployments.py:44`). With the SQL `LIMIT` the query stops after N rows; a group
   with fewer than N readings within the time bound walks the whole bound — Plan 272 computed
   ~13 k index entries per such group per run at 30 days, and called that arithmetic, not a
   measurement. `uq_observations_natural_key` covers `(station_id, timestamp, parameter, source)`
   (`db/metadata.py:571-578`), so `parameter` is an index condition. T4 measures the flow duration.

## Design

- **Two fetches, deliberately.** The existing check-window fetch in `_run_qc_task` is unchanged and
  still feeds the rules. A second fetch returns only the **timestamps** of the group's most recent
  readings for inference. ⛔ Widening the check window instead was the first draft and the owner
  rejected it (it changes `frozen_sensor`, lets `rate_of_change` compare across outages up to the
  window length, and widens the DHM catch-up reach — both reviews, 2026-09-26). State in the code
  why there are two fetches.
- **Configurable bounds (owner, 2026-09-26).** A new `[qc_rules.cadence_inference]` table in
  `config.toml` with `lookback_days` (default 30) and `max_readings` (default 50), parsed at the
  config boundary into a frozen dataclass whose `__post_init__` rejects non-positive values and
  `max_readings < 3` (two gaps are the fewest a median can use sensibly). An absent table means the
  defaults. Documented in `docs/spec/config-reference.toml`.
- **Additive store method.** `fetch_recent_timestamps(station_id, parameter, *, not_before,
  before, limit) -> list[UtcDatetime]` — ⛔ **no filter on `qc_status` or `source`**, for the same
  reason the check fetch is unfiltered (Plan 317): inference describes the series, whatever its
  verdicts — on the `ObservationStore` Protocol
  (`protocols/stores.py:105`), `PgObservationStore` and `FakeObservationStore`
  (`tests/fakes/fake_stores.py:132`): `SELECT DISTINCT timestamp … ORDER BY timestamp DESC LIMIT
  :limit`, returned chronological. 🔴 **The cap is a SQL `LIMIT`, not a client-side slice** (272's
  rule: a slice bounds the median's input and nothing else). ⛔ `fetch_observations` is not changed
  — its ordering guarantee is load-bearing (Plan 228 comment in `store/observation_store.py`).
- **One inferred cadence per group, used by every selection consumer.** `_run_qc_task` infers the
  cadence once from the fetched timestamps and passes it to **both** `Stage1QualityChecker.check`
  and `resolve_selection`, so the rules that run and the zero-rule record agree by construction —
  the property Plan 272 T3 built — and by Plan 323 T4's new checker method (flags plus the judged
  set), which receives the same inputs as `check`, so the same `time_steps` keyword reaches it and
  its "could any rule judge it" decision uses the look-back cadence. Mechanism: an optional keyword `time_steps: Mapping[tuple[
  StationId, str], timedelta | None] | None = None`; when `None`, both infer from the observations
  exactly as today. **A group absent from a non-`None` mapping** is inferred from its observations
  as today — the flow always supplies every group, so the fallback only protects other callers. ⇒
  Other callers — onboarding (`services/onboarding.py`) and
  `scripts/dhm_precip/`, which calls `check` positionally — are unchanged.
  `QualityChecker.check` in `protocols/stores.py` gains the same optional keyword.
- **Inference stays one function.** A timestamp-level function (median of gaps between distinct
  instants; fewer than two ⇒ `None`) that `infer_time_step` delegates to, so the rule is written
  once. ⛔ Fewer than two distinct readings in the sample still infers `None` and the group stays
  `QC_UNCHECKED` — the Plan 272 fail-closed property.
- 🔴 **A selected rule is not a rule that ran — relies on Plan 323 T4.** Once the cadence comes
  from outside the check window, a group can select rules none of which can evaluate the pending
  reading. Measured case (Codex, round 4): a water_level station with no datum and the
  missing-hour fixture — the look-back infers 3600 s, the datum skip removes `range_check` and
  `gross_outlier`, and `rate_of_change`/`spike` return at once with no neighbour in the one-reading
  window (`services/qc.py:137`, `:220`). Without a guard that reading would be stored `QC_PASSED`.
  The case is reachable under Plan 323 alone too, so the owner put the guard there (323 D4/T4:
  a reading passes only if some selected check could judge it; otherwise `QC_UNCHECKED` with
  reason `no_check_could_run`). This plan **depends on it** and tests it on the look-back path.

## Owner decisions

None open. Closed by the owner:
- **The split** (2026-09-25): Plan 323 accepts the leftover; this plan fixes inference.
- **A separate inference look-back, not a wider check window** (2026-09-26, option B).
- **The look-back is configurable** (2026-09-26).

⚠️ Consequence the owner accepted with option B: the hourly `frozen_sensor` rule, which Plan 323
deferred to this plan, **cannot** be delivered here — a stuck-sensor run needs more readings in the
*check* window, which stays at 2 h. It is recorded as needing a later plan (§ Explicitly out of
scope).

## Tasks

### T1 — Make the inference look-back configurable

**Outcome.** The look-back's two bounds are read from configuration, validated once at the
boundary, and reach the ingest flow as a typed value.

**In.** The `[qc_rules.cadence_inference]` table (§ Design) set explicitly in `config.toml`; its
parser beside `load_qc_rules` (`config/qc_rules.py:251`); the frozen dataclass; the flow receives it
the way it receives `qc_rules`; `docs/spec/config-reference.toml` documents both keys and their
defaults.

**Out.** ⛔ Any use of the value (T2). ⛔ Other `[qc_rules]` keys.

**Pre-change.** N/A as a behavioural red — this task adds a configuration surface with no prior
behaviour to fail against (a test of the new loader would be red only on its absence). T2's RED test
is the behavioural one.

**Verification.** Defaults when the table is absent; explicit values round-trip; `0`, a negative
value and `max_readings = 2` are rejected with a message naming the key.

### T2 — Infer cadence from the bounded look-back

**Outcome.** A group's cadence is inferred from its most recent readings, so an hourly series with
an occasional missing reading selects its rules, while the rules still run on the 2 h window.

**In.** § Design's store method (Protocol, Pg, fake), the timestamp-level inference function, the
optional `time_steps` keyword on `Stage1QualityChecker.check`, on Plan 323 T4's new checker method
(flags plus the judged set — the one `_run_qc_task` calls after 323), on `resolve_selection` and on
the `QualityChecker` Protocol, and the second fetch in `_run_qc_task` with `not_before =
min(now − lookback, window_start)` — so on the DHM catch-up path, where the window can start more
than `lookback` ago, the look-back never excludes the window's own readings — `before` = the check
window's end, `limit = max_readings`.

- Re-point `tests/unit/flows/test_ingest_observations_recheck.py::test_already_checked_neighbours_stay_in_the_group`
  (`:233`). It guards the unfiltered check fetch by relying on cadence inference; once cadence
  comes from the look-back, a narrowed fetch would still select `range_check` and the test would
  stay green through the regression it exists to catch. Make the unchecked reading's verdict
  depend on a `QC_PASSED` neighbour through a temporal rule (e.g. `rate_of_change`).

**Out.** ⛔ The check window (`context_window_hours`, the DHM `fetched_times` widening). ⛔
`fetch_observations`. ⛔ Onboarding and `scripts/dhm_precip/` (they keep inferring from their own
rows). ⛔ Matching, thresholds, the pick-up set.

**Pre-change.** A RED test in the production shape: an hourly **discharge** series (so a
per-reading rule, `range_check`, exists — Plan 323 D4) with readings on the hour from
−23 h to 0 h **except −1 h**, the 0 h reading pending, `now` = 0 h + 5 min, run through
`_run_qc_task` with the rule set loaded from the shipped `config.toml` (which after Plan 323
declares 3600 s rows). Today the window `[−1 h 55 min, …)` holds only the 0 h reading, inference
returns `None`, and the reading is stored `QC_UNCHECKED` with a `no_cadence_inferable` entry in
the task's `QcTaskOutcome.zero_rule_groups` (the flow, not `_run_qc_task`, writes the health
record). It must fail on that status, not on a fixture or config key.

**Verification.**
- The RED test passes: the pending reading gets a real verdict; `zero_rule_groups` is empty.
- **The knob is real:** the same series with `max_readings = 3` (the smallest T1 allows) samples
  0 h, −2 h and −3 h, infers 5400 s, and the reading stays `QC_UNCHECKED` with
  `no_rule_declares_it` — proving the configured value, not a constant, drives inference.
- **The configuration reaches the scheduled flow:** the same fixture run through
  `ingest_observations_flow` with the look-back loaded from configuration — `max_readings = 50`
  gives a verdict, `max_readings = 3` leaves `QC_UNCHECKED` — so the setting is proven to travel
  from the config file through the flow (`flows/ingest_observations.py:934-943`) to `_run_qc_task`,
  not only when passed to the task directly.
- **Missing key:** a non-`None` `time_steps` mapping without the group falls back to inferring
  from the observations.
- **Cadence switch (§ 2):** a series that moves from 600 s to 3600 s keeps inferring 600 s until
  hourly gaps are the majority of the last `max_readings` — asserted, so the lag is a known
  quantity rather than a surprise.
- **DHM catch-up (§ 3):** a recovered DHM water-level batch with a gap longer than 2 h, repaired
  by the look-back, runs `rate_of_change` across that gap — asserted and named, not prevented.
- **Fail-closed kept:** a group with one distinct reading in the look-back stays `QC_UNCHECKED`
  with `no_cadence_inferable`.
- **No silent pass on the look-back path (Plan 323 D4/T4's guard):** the missing-hour fixture as
  water_level with no datum — the look-back infers 3600 s, the lone reading is judged by nothing,
  and it is stored `QC_UNCHECKED` and listed in the task's `QcTaskOutcome` unjudged ids (Plan 323
  T4; the record itself is the flow's), not `QC_PASSED`; the same series with a datum gets a real
  verdict from `range_check`.
- **Look-back bound on catch-up:** a DHM recovery whose window starts more than `lookback` ago still
  infers its cadence from the window's own readings.
- **Unchanged where it should be:** a gap-free 600 s group selects the same rules as before, and the
  existing ingest tests pass unmodified, except the re-pointed Plan 317 guard above — including
  `tests/unit/flows/test_ingest_observations_dhm.py::TestDhmIngest::test_six_hour_recovery_qcs_old_rows_with_preceding_context`,
  which the first draft's wider window broke.
- **Agreement:** a test where the check-window rows alone would infer `None` but the look-back
  infers 3600 s asserts that `check` ran the 3600 s rules **and** `resolve_selection` reported a
  non-zero count — the two cannot disagree.
- **The SQL bound:** an integration test against real Postgres (beside
  `tests/integration/store/test_observation_store.py`) that the method returns at most `limit`
  distinct timestamps, the most recent ones, in chronological order, with boundary fixtures proving
  `not_before <= timestamp < before`; and a unit assertion on the compiled statement that the
  distinct selection and the `LIMIT` are in the SQL — ⛔ result-shape tests alone pass equally with
  an unbounded query sliced in Python, which § Design forbids.
- **Before merge, on a copy of staging data:** a replay with the production functions, the
  post-Plan-323 rule set, the production window and look-back bounds, and only the readings that
  existed at each simulated run time, listing every group whose selection changes, with its old and
  new cadence (§ 2, § 4). Anything other than "zero rules → its cadence's rules" — in particular
  every non-zero → non-zero change — is reported to the owner before merge.

### T3 — Documentation

**Outcome.** The two-fetch design and its knob are written where the next person meets them.

**In.** `docs/spec/types-and-protocols.md` — the `QualityChecker` Protocol (around line 741), the
`time_steps` keyword on Plan 323 T4's checker method, and the new `ObservationStore` method; the observation-ingest map in `docs/touchpoint-maps.md` beside
the Plan 317 entry (two fetches, why, the config keys) — and correct that entry's sentence that the
unfiltered fetch supplies what "cadence inference and the temporal rules need": after this plan it
supplies the temporal rules; inference has its own, equally unfiltered, fetch; Stage 1 QC (step 2.3) in
`docs/architecture-context.md`; replace Plan 323 T3's "until Plan 400 lands" sentence with the
landed state. (The README's Plan 272 entry already records the revival.)

**Out.** ⛔ Changing Plan 272's archived text.

**Pre-change.** N/A — documentation.

**Verification.** `grep -n "cadence_inference" docs/spec/config-reference.toml docs/touchpoint-maps.md`
returns both; the Protocol in the spec matches `protocols/stores.py`.

### T4 — Prove it on staging

**Outcome.** While delivering, the five hourly gauges stop producing alarming zero-rule records
(`observation_qc_unchecked`); what remains unjudged is datum-less water level, recorded without an
alarm (Plan 323 D5) until Plan 403; and the cost and the § 3 exposure are measured, not assumed.

**In.** For the 24 h before and the 24 h after deploy, each with the query recorded here:
- **Five-station outcome.** Numerator: the five stations' readings received in the period whose
  stored status at the end of the period is `QC_PASSED`, `QC_SUSPECT` or `QC_FAILED` — ⛔ not
  "not `QC_UNCHECKED`", which would count `RAW` rows a failed QC run left behind (the flow catches
  a QC exception per group, `flows/ingest_observations.py:952`). Denominator: all their readings
  received in the period. `RAW` reported separately. Reported per parameter; discharge and
  water_temperature must reach at least 95%; water level is reported with its datum status.
- **Records.** After deploy, while the station was delivering, **no** `observation_qc_unchecked`
  record for these stations with reason `no_cadence_inferable` or a cadence that is a multiple of
  3600 s. An off-grid cadence (e.g. 3900 s from a jittered timestamp) is allowed but listed with
  its readings. Unjudged readings (`observation_qc_unjudged`, Plan 323 D5) are counted as distinct
  observation ids — records count runs — and expected only for datum-less water level.
- **Watchdog.** Its state is **reported, not required**: the watchdog alarms on a zero-rule record
  from *any* station (`ops/watchdog.py:220`), so a new or silent station elsewhere can keep it
  failing for reasons this plan does not own.
- **§ 3 exposure and selection changes — by replay, because no record holds them.** Neither a
  reading row nor any health record stores the cadence of a group that selected rules, and "was
  zero-rule before the change" is a counterfactual after deploy. So T2's pre-merge replay is re-run
  over the 24 h after deploy, computing old (window-only) and new (look-back) inference per run:
  every group whose inferred cadence differs (cadence switches included, § 2), and the
  `QC_SUSPECT`/`QC_FAILED` verdicts from `rate_of_change` and `spike` on readings the old
  inference left zero-rule — counted, with examples, the DHM catch-up path separately. Plus the
  distinct observation ids in the live `observation_qc_unjudged` records.
- **Cost.** `ingest-observations` flow-run duration, median and maximum. If the median more than
  doubles, report it to the owner before closing.

**Out.** ⛔ Tuning anything in response — findings go to the owner.

**Pre-change.** The "before" figures, recorded first.

**Verification.** Every figure recorded in this plan beside the query that produced it.

## Explicitly out of scope

- **The check window** — stays 2 h (owner, option B).
- **An hourly `frozen_sensor` rule** — needs a check window that holds a flat stretch worth
  flagging. Deferred from Plan 323 D2 to this plan, and this plan cannot carry it: **no plan owns it
  yet**. Plan 323 T1 records the flat-run measurements it will need.
- **Nearest-rule matching** — no plan owns it; § 1 shows it is unnecessary for every measured case.
- **The network dimension of selection** — Plan 264. ⚠️ Its in-flight rewrite (uncommitted, another
  session, seen 2026-09-25) changes the same `check`/`rules_for` signatures to add a network. The
  two changes are independent in meaning but touch the same lines; whichever lands second rebases.
- **`rate_of_change` dividing by elapsed time** — Plan 313. § 3 is the interaction: this plan lets
  rate and spike run on some checks that today run nothing, across a gap of up to one window.
  313 fixes the arithmetic; until then T4 counts the exposure. ⚠️ Plan 313 § 6 says a gap longer
  than the window leaves the first reading with no neighbour; that stays true under this plan.
- **Onboarding's QC** — Plan 315. ⚠️ 315 D1 runs after every plan that changes the rule set's
  shape or the cadences it declares, and names 303, 269, 313 and 264. This plan changes which
  cadence a group *resolves to*. Recording it for 315's owner; 315 is not edited here.
- **Climatological baselines** for series that have none (Plan 323 § 12).
- **Re-QC of stored history** — the owner's standing answer (Plan 323, Plan 315 D3): leave it.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false},
    {"phase": 2, "tasks": ["T2"], "parallel": false},
    {"phase": 3, "tasks": ["T3", "T4"], "parallel": false}
  ]
}
```

## Review record

- **2026-09-26 — independent Claude review and independent Codex review of the first draft (24 h
  check window): both NOT READY.** Both found the wider window changes `rate_of_change`/`spike`
  neighbours, not only `frozen_sensor`, and breaks the DHM six-hour recovery test; the Claude pass
  also found the watchdog promise outside the plan's control, the load sentence wrong (only groups
  with fetched rows run), the "window must fill" clause wrong, and 272's parked design uncited; the
  Codex pass found the replay and the live check counted different populations. The owner then
  chose option B (this redesign). Folded: the design; § 1's anchoring note; § 3; T4's defined
  numerator, denominator and reported-not-required watchdog; the recovery test named as a
  must-stay-green; § Why's citation of 272; the 313 and 315 notes.

- **2026-09-26 — round 3: independent Claude review and independent Codex review of `cd6ead60`:
  both NOT READY; all findings folded.** Both: the knob test used `max_readings = 2`, which T1
  rejects (→ 3, asserting 5400 s and `no_rule_declares_it`). Codex: `RAW` counted as success
  (T4); the 2 h exposure bound fails on the DHM catch-up path (§ 3, T2, T4); § 2's
  preservation claim covered a narrower population than it concluded about (§ 2, T2 replay).
  Claude: the Plan 317 guard test would stay green through its own regression (T2 In, T3); a
  cadence switch lags ~N/2 readings and N bounds repairable degradation (§ 2 — accepted
  consequences of option B, stated and tested); a missing mapping key was undefined (§ Design);
  the RED test named a record `_run_qc_task` does not write (T2); T1's pre-change would have been
  red only on a missing loader (T1 → N/A with the reason).
- **2026-09-26 — round 4: independent Claude review and independent Codex review of `7e0886d0`:
  both NOT READY, no decision contradicted; all findings folded.** Codex (P1): a group can select
  rules none of which can judge the pending reading, and would be stored `QC_PASSED` — the Plan
  272 fail-open through a new door. Reachable under 323 alone, so the owner moved the guard into
  Plan 323 (D4/T4); this plan depends on it and tests it on its path (§ Design, T2). Codex: the SQL-bound test could not tell a `LIMIT`
  from a Python slice, and omitted `before` (T2). Claude: two T4 items had no record to query
  (→ replay); the look-back's lower bound could exclude a DHM catch-up window's own rows
  (`not_before = min(…)`); "existing tests unmodified" contradicted the re-pointed guard; the README
  entry was stale; the JSON graph sat inside the review record.
- **2026-09-26 — round 5: independent Claude review and independent Codex review of `ba93dc9d`:
  both NOT READY; all findings folded.** Claude (HIGH): datum-less water level — every Swiss river
  station — keeps an unjudged leftover this plan cannot remove (§ Status, § 3; T4 outcome and gate
  narrowed); owner: datums via **Plan 403**, and unjudged readings recorded without an alarm
  (Plan 323 D5). Both: T4's gate rejected the `no_check_could_run` outcome it must now expect.
  Claude: Plan 323 T4's decision is a third selection consumer (§ Design); the RED test names
  discharge; § Status was stale. Codex: no test proved the configuration reaches the scheduled flow
  (T2).
- **2026-09-26 — round 6: independent Claude review and independent Codex review of `a2f32b5f`:
  both READY for this plan (Claude with LOW findings), NOT READY for 323 and 403.** Folded: § Design
  no longer says 323 T4's decision takes `resolve_selection`'s result (it returns a count) — it is a
  new checker method receiving the same `time_steps`; the T4 records gate says what an off-grid
  record means; unjudged readings are counted as distinct ids; the duplicated datum-less test
  bullets merged.
- **2026-09-26 — round 7: READY from both reviewers (Claude with LOW findings), folded.** T2 In and
  T3 name Plan 323 T4's checker method, which `_run_qc_task` calls after 323; the no-silent-pass test
  asserts the task's outcome, not a record only the flow writes.
- **2026-09-26 — round 8: READY from both reviewers, CLEAN** — no findings on the round-7 fold.
