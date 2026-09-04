---
status: READY
created: 2026-09-03
revised: 2026-09-04
plan: 237
title: The NWP fetch must produce a complete, unambiguous cycle or fail loudly — and its abort must not report COMPLETED
scope: Make the NWP fetch/parse yield either a complete, duplicate-free dataset or a named failure — de-duplicate on a collision-safe identity, make the download destination injective, and assert completeness so the outer join cannot NaN-fill silently. Make the NWP-fetch abort reach a non-successful terminal state. And alert once per zero-forecast incident instead of every 30 minutes. Three tasks. No retry logic, no re-fetch, no STAC pagination rework, no alert-channel work.
depends_on: []
blocks: []
source: Measured on the mac mini 2026-09-03/04 after 15 Slack alerts in one day; four failing cycles analysed, hardened through two independent review rounds
---

# Plan 237 — the NWP fetch must be complete and unambiguous, or fail loudly

## Status

**READY.** Owner confirmed 2026-09-04, after four independent review rounds.

## ⛔ Proportionality — two tasks

**Do not add:** retry-on-failure, a re-fetch path, STAC pagination rework, **new** alert channels, a
general gap-marking overhaul (that is Plan 235/236 territory), or a fourth task.

⚠️ **T3 was added 2026-09-04, after this plan reached READY.** Revisions 1-4 said "no third task"
and "no alert-channel work", and four review rounds were conducted under that constraint. The owner
then decided the re-alert cadence question that those revisions had deliberately left open, so the
decision is recorded here as T3 rather than in a separate plan. T3 changes **cadence policy only**,
reusing a policy and helper that already exist in this repo — it adds no channel, no threshold and
no new mechanism. It is independent of T1/T2 and touches a different file. Reviewers: this is the
one relaxation; do not treat it as licence for more.

⚠️ **One earlier constraint is deliberately relaxed.** Revisions 1-2 of this plan forbade any
completeness check, deferring it to Plan 213 D3c. **The second review round showed de-duplication is
unsafe without one** (see "Why de-duplication alone makes things worse"), so a narrow completeness
**assertion** — fail loudly when the combined dataset is short — is now inside T1. This is not the
completeness *probe* of 213 D3c, which pre-checks the catalogue before fetching; it is a
post-condition on data we already hold. Reviewers: do not grow it into the probe.

## What is measured — four failing cycles, and the incomplete case is the majority

A complete ICON-CH2-EPS cycle is **484 files**: 121 hourly steps (0…120) x 2 allowlisted variables
(`tot_prec`, `t_2m`) x 2 files each (`ctrl`, `perturb`) — consistent with `PARAM_GROUPS`
(`meteoswiss_nwp.py:56-62`) and the inclusive cycle→+120 h window (`:704-709`).

| cycle (UTC) | downloads | **distinct** | short by | duplicates | outcome |
|---|---|---|---|---|---|
| 2026-09-02T12 | 484 | 484 | 0 | 0 | ok |
| **2026-09-02T18** | 501 | **473** | **11** | 28 (`perturb` only) | **aborted** |
| 2026-09-03T00 | 484 | 484 | 0 | 0 | ok |
| 2026-09-03T06 | 484 | 484 | 0 | 0 | ok |
| **2026-09-03T12** | 488 | **484** | 0 | 4 (step 35, all four types) | **aborted** |
| **2026-09-03T18** | 308 | **302** | **182** | 6 (`perturb`, steps 17/24/112) | **aborted** |
| 2026-09-04T00 | — | — | — | — | ok |
| 2026-09-04T06 | — | — | — | — | ok |

**4 of the 9 cycles listed or attributable above produced zero forecasts (~44 %)** — the six rows
between 2026-09-02T12 and 2026-09-04T06, plus 2026-09-01T18 (which also failed; its counts fell
outside the retained log window) and the two 09-04 rows whose file counts were not captured but
which completed normally. An earlier revision said "4 of 11 (~36 %)"; the denominator was not
reconstructible from the evidence shown, so it is restated here against only what is listed. Every failure shows the same error, and in every measured
case the duplicates span **both** `PARAM_GROUPS`:

```
nwp.param_parse_skipped  error="cannot reindex or align along dimension 'number'
                                because the (pandas) index has duplicate values"
nwp.fetch_failed         error='No parameter groups could be parsed from GRIB2 files'
forecast_cycle.nwp_fetch_failed_aborting
```

**Only one of the measured failures is a purely-our-bug case.** 2026-09-03T12 fetched a complete 484
and was destroyed by 4 duplicates. The other two were **substantially incomplete** (473 and 302
distinct) *and* duplicated. So the honest answer to "MeteoSwiss outage or our bug?" is **both — and
the incomplete case is the more common one**.

### The mechanism

1. `_fetch_grib_files` appends every downloaded asset path with **no de-duplication**
   (`meteoswiss_nwp.py:816`); `_download_asset` collapses the URL to its **basename** (`:887-906`),
   so two listings of one asset put the same path twice in the returned list while overwriting one
   file on disk.
2. `_parse_grib_files` opens every list entry, so a duplicated step is opened twice.
3. `_combine_cfgrib_datasets` (`:275`) groups by `valid_time` and concatenates along `number`; the
   duplicate contributes the same member values again.
4. xarray raises `cannot reindex or align along dimension 'number'…`; the handler treats that group
   as unparseable and `continue`s (`:961-977`).
5. A single duplicate skips only **its own** variable — each STAC item carries one variable. Zero
   output requires duplicates spanning **both** `PARAM_GROUPS`, which is what every measured failure
   had.

### 🔴 Why de-duplication ALONE makes things worse — the finding that reshaped this plan

De-duplicating and stopping there would **convert a loud abort into silently stored bad data** for
the incomplete cycles, i.e. for the majority of failures:

- `_combine_cfgrib_datasets` concatenates with **`join="outer"`** (`meteoswiss_nwp.py:343-350`), so
  absent members/times are **NaN-filled**, not dropped.
- The ensemble-member count check then sees all 21 members present (`:982-994`).
- Extraction retains partially-missing members (`mesh_basin_extractor.py:121-154`).
- `converters.py:109-124` constructs every `WeatherForecastRecord` with **`is_gap=False`,
  `gap_status=None` — unconditionally**. A NaN is therefore stored as a real value with no gap
  marker.
- There is **no minimum-completeness check anywhere** in the adapter (the only `min` is
  `_MIN_ENSEMBLE_MEMBERS`, which the NaN-fill satisfies).

An earlier revision claimed the per-model `resolve_required_steps` / `assess_future_coverage` path
(`run_station_forecast.py:255-262`) would catch a short cycle. **It would not** — coverage sees the
NaN-filled cells as present. That claim is withdrawn.

So T1 must assert completeness in the same change that removes the duplicate crash. Shipping
de-duplication on its own is the one outcome worse than today's outage.

### Why the duplicates appear — NOT established, out of scope

Most likely the STAC pagination behaviour already on record for this catalogue. **Unverified** — the
listing was not captured at failure time. It does not matter for T1: being handed the same asset
twice must not destroy or corrupt a cycle. T1's WARNING gives a later plan the raw material.

## Where the alerting actually stands

An early revision of this plan claimed the abort was invisible. **Wrong** — the watchdog fires
correctly and the owner received 15 Slack alerts in one day:

```
[SAPPHIRE staging] forecast cycle stored ZERO forecasts — status: critical
[SAPPHIRE staging] forecast production RECOVERED
```

First burst 2026-09-02T22:07Z → `RECOVERED` 00:27Z (the moment the 00:00 cycle stored forecasts);
second from 2026-09-03T12:08Z; **~every 30 minutes, 8 alerts for one failed cycle**.

Detection is not the gap. What remains is that **Prefect reports `COMPLETED`** while the watchdog
calls the same event critical — two subsystems disagreeing (T2) — and the repeat volume (owner
question below).

## Tasks

### T1 — the fetch yields a complete, unambiguous dataset, or a named failure
**Outcome:** a duplicated STAC asset can no longer enter the parse twice, two different assets can no
longer collide on one local filename, and an incomplete cycle fails with a named error instead of
returning a NaN-filled dataset that is stored as real data.
**In:** `src/sapphire_flow/adapters/meteoswiss_nwp.py` (`_fetch_grib_files`, `_download_asset`,
`_combine_cfgrib_datasets`/`_parse_grib_files`), `tests/unit/adapters/test_meteoswiss_nwp.py`.
Three coupled parts of one change:
(1) **De-duplicate on `netloc` + `path`** of the `href`, query stripped — not the raw signed `href`
(its query carries a per-request signature; do **not** justify this by expiry, which the repo's probe
records as fixed at item creation, `docs/research/063-meteoswiss-stac-probe.md:141-155`), not the
basename (two different assets could share one), not the asset key (cross-item uniqueness is never
enforced, `:153-161`). Preserve first-seen order; emit one WARNING naming how many duplicates were
dropped and which steps/variables.
(2) **Make the destination collision-proof** — a short digest of `netloc`+`path` inserted **before**
the `.grib2` suffix. Keep the flat layout and existing filename tokens: consumers assume flat files
whose names carry cycle/step/variable/variant (`tests/unit/adapters/test_meteoswiss_nwp.py:1020-1032`,
`scripts/063_e2e_verify.py:81-91`). A digest is collision-*resistant*, not injective — acceptable only
as belt-and-braces behind part 1's exact-identity dedup.
(3) **Assert the full expected `(valid_time` x `member)` grid per parameter — both checks mandatory.**
(i) observed `valid_time` set equals the expected set implied by the requested window
(`window_end = cycle_time + 120 h`, `:704-709`) and cadence — derive it, do **not** hardcode `121`
(that is the empirical value from three healthy 484-file cycles; thread the window through if the
combining code cannot see it); fail naming the missing steps. (ii) within every present `valid_time`,
no `(parameter, member)` cell may be wholly NaN; fail naming the missing members and times.
⛔ **(ii) is not optional:** if only the `ctrl` **or** only the `perturb` file is missing for a step,
the other still supplies that `valid_time`, so (i) passes while `join="outer"` NaN-fills the absent
members and the 21-member check still succeeds (`:301-350,979-994`) — the observed production shape,
since the 09-02T18 and 09-03T18 duplicates were `perturb`-only.
Skip the assertion when `max_files` is set and log why: it is an asset-count cutoff with no temporal
boundary (`:789-885`), so there is no requested-step set to intersect. Production never sets it —
default `None` (`:375`), flow passes `None` (`run_forecast_cycle.py:345`), `config.toml:413` commented
out — and a test must assert that, so the exemption cannot silently become the norm.
**Out:** retrying, re-fetching, pagination changes, the byte/file caps, the age guard,
`_combine_cfgrib_datasets`' member contract, and how gaps are marked downstream (`is_gap` is Plan
235/236 — T1 fails before a record is built).
**Pre-change:** `_fetch_grib_files` appends every asset with no de-duplication (`:816`);
`_download_asset` names files by basename alone (`:887-906`); there is no completeness check anywhere
(the only `min` is `_MIN_ENSEMBLE_MEMBERS`, which NaN-fill satisfies).
**Verification:** `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py` — (a) a listing repeating
items across **both** `PARAM_GROUPS` and containing at least one non-duplicated valid_time (a single
duplicated time returns before cross-time alignment and would not reproduce the fault): no duplicate
paths, WARNING names the drops, parse succeeds; (b1) a listing missing entire steps: error names the
missing steps, no record produced; (b2) a listing missing only `perturb` for present steps: error
names the missing members — the case (i) alone would wrongly pass; (c) two different URL paths sharing
a basename land in two files. ⛔ (a), (b1) and (b2) must be proven RED against the pre-change code;
the alignment error is re-surfaced as a generic `AdapterError` (`:961-977`), so (a) asserts on that or
the debug record, not raw pandas text.

### T2 — the NWP-fetch abort must not report COMPLETED
**Outcome:** Prefect stops reporting a zero-forecast NWP abort as a successful run, so it agrees with
the watchdog that already calls the same event critical.
**In:** `src/sapphire_flow/flows/run_forecast_cycle.py`, `tests/unit/flows/test_run_forecast_cycle.py`.
Raise (or set a failed state) at the **flow** level, **after** the zero-forecast CRITICAL record is
written (`:2669-2680`). ⛔ "Re-raise" is not available at the task site: `_fetch_nwp_task` has already
swallowed the exception into a returned outcome (`:1340-1349`), and raising inside the task would make
`.result()` fail before the existing emitter runs. Confirm exactly one freshness/CRITICAL record and
that the end-of-cycle emitter (`:3612-3618`) is not double-run.
**Out:** new alert channels, new health metrics, retry policy, and the other zero-output paths — no
operational stations (`:2384-2403`) and ordinary end-of-cycle zero storage (`:3612-3658`) — which
still return normally.
**Pre-change:** the flow returns `ForecastCycleResult(health=FAILED)` *normally* (`:2667-2691`) — a
domain-level FAILED inside a Prefect COMPLETED. No retry is configured, so failing does not trigger a
re-fetch.
**Verification:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py` — the abort path does not
end successfully **and** the CRITICAL record is written exactly once. ⛔ RED first.

### T3 — forecast-production freshness alerts once per incident, not every 30 minutes
**Outcome:** one zero-forecast incident spanning many ticks produces exactly one critical alert and
exactly one `RECOVERED`, instead of eight pages.
**In:** `src/sapphire_flow/ops/watchdog.py`, `tests/unit/ops/test_watchdog.py`. Reuse the existing
"alert once" policy (`:13-18`, `_backup_notification_kind:1132`) rather than writing a second
mechanism. Its delivery-retry behaviour must be preserved — "alert once" must mean once *delivered*,
not once *attempted*.
**Out:** the health, BAFU-forecast and BAFU-observation checks (their 5-min probe cadence makes the
current hysteresis correct — do **not** change them); new channels; thresholds; what counts as a
zero-forecast cycle; escalation on consecutive misses.
**Pre-change:** forecast-production freshness uses the 6th-consecutive-failure hysteresis
(`watchdog.py:8-11`), calibrated for a 5-minute probe and applied to a 6-hourly batch, so it alerts on
ticks 1, 6, 12, … while nothing can change between them.
**Verification:** `uv run pytest tests/unit/ops/test_watchdog.py` — several consecutive zero-forecast
ticks yield exactly one alert plus one on recovery; a second test asserts the health/BAFU checks still
alert on the 6th consecutive failure. ⛔ RED first.

🚀 **T3 deploys differently from T1/T2.** The watchdog runs as a **host process** under launchd
(`uv run python -m sapphire_flow.ops.watchdog`,
`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`), not in a container — it ships by pulling
the host checkout on the mini, **not** by rebuilding the image. T1/T2 ship in the image.

## The re-alert question — DECIDED 2026-09-04 (now T3)

Revisions 1-4 left this open for the owner. **Decided: alert once per incident.**

The governing principle: **never re-notify faster than the underlying condition can change.** The
forecast cycle runs every 6 h, so between 12:00 and 18:00 UTC nothing can make the state improve —
no retry, no reprocessing, no self-healing. Every repeat inside that window is information-free by
construction: eight pages carrying one bit. Noise is not free; it trains the reader to swipe away the
channel that must eventually carry a flood alert.

The current cadence is not careless — `ops/watchdog.py:8-11` documents deliberate hysteresis (alert
on first failure, then every 6th consecutive failure ≈ 30 min at 5-min ticks, then once on recovery).
It is simply calibrated for a **5-minute probe**, where the condition genuinely can change between
alerts, and then applied to a **6-hourly batch**, where it cannot.

**The policy T3 adopts already exists in this repo and is in production**: backup staleness uses a
dedicated "alert once" policy (`ops/watchdog.py:13-18`, `_backup_notification_kind:1132`) — exactly
one alert on the first stale tick, silence while it stays stale, exactly one recovery alert.

*Not adopted, deliberately:* simply widening 30 → 60 min (halves the noise without fixing the
reasoning, and would be revisited), and escalation on consecutive missed cycles (a second
consecutive failed cycle IS worse news than the first and worth a distinct louder alert — but that
is a new signal, not a cadence fix, and belongs to its own plan if wanted).

**T3 — Forecast-production freshness alerts once per incident, not every 30 minutes.**
*Scope (in):* apply the existing "alert once" policy to the forecast-production freshness check —
one alert on entering the zero-forecast state, silence while it persists, exactly one `RECOVERED`.
**Reuse the backup-staleness policy already in this file** (`ops/watchdog.py:13-18`,
`_backup_notification_kind:1132`) rather than writing a second mechanism; the delivery-retry
behaviour it documents (a notification that fails to post is retried each tick until it lands) must
be preserved, because "alert once" must mean once *delivered*, not once *attempted*.
*Scope (out):* the health, BAFU-forecast and BAFU-observation checks (their 5-min probe cadence
makes the current hysteresis correct — do **not** change them); new channels; thresholds; what
counts as a zero-forecast cycle; escalation on consecutive misses.
*Exit:* one zero-forecast incident spanning several ticks produces exactly one critical alert and
exactly one `RECOVERED`; the other checks' cadence is byte-for-byte unchanged.
*Verification:* `uv run pytest tests/unit/ops/test_watchdog.py -q` — a test driving several
consecutive zero-forecast ticks and asserting exactly one alert, plus one on recovery, and a test
asserting the health/BAFU checks still alert on the 6th consecutive failure. **RED first** against
today's code, which alerts on ticks 1, 6, 12, …

🚀 *Deploying T3 differs from T1/T2.* The watchdog runs as a **host process** under launchd
(`uv run python -m sapphire_flow.ops.watchdog`, `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`),
not inside a container — so it ships by **pulling the host checkout on the mini, not by rebuilding
the image**. T1/T2 ship in the image. Do not assume one deploy covers both.

## Separately worth knowing (not in scope, do not fix here)

- **The incomplete-cycle fault is the majority case and is only made *diagnosable* here, not fixed.**
  T1 turns 473- and 302-file cycles into named completeness failures; they still produce no
  forecasts. Why MeteoSwiss served an incomplete cycle 6+ hours after reference time — when measured
  publication latency is 160-173 min — is unexplained and deserves its own plan.
- The mini runs `nwp_cycle_min_age_minutes = 105`; `main` has `210` (Plan 213, merged **and
  archived**, undeployed there). For the next deploy. **Not** related to this failure: at an on-grid
  cron instant the candidates are ~0 and ~360 min old, so any guard in (0, 360) decides identically —
  an existing parametrised test proves it (`tests/unit/adapters/test_meteoswiss_nwp.py:1685-1719`).
- `converters.py:119` hardcodes `is_gap=False` for every record. T1 sidesteps it by failing earlier;
  the underlying gap-marking gap belongs to Plan 235/236.
- This adapter has been broken by upstream ICON schema drift before (Plan 160); the
  `ctrl=0 / perturb=1..20` contract lives only in a docstring (`:259-272`).

## Exit gates

```bash
uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py tests/unit/flows/test_run_forecast_cycle.py tests/unit/ops/test_watchdog.py
uv run ruff check src/sapphire_flow/adapters/meteoswiss_nwp.py src/sapphire_flow/flows/run_forecast_cycle.py src/sapphire_flow/ops/watchdog.py
uv run ruff format --check src/sapphire_flow/adapters/meteoswiss_nwp.py src/sapphire_flow/flows/run_forecast_cycle.py src/sapphire_flow/ops/watchdog.py
uv run pytest tests/unit
```

- Every RED-first test above is proven red against the pre-change code, and green after.
- `uv run pyright` no worse than the recorded ratchet baseline.
- The flat `*.grib2` layout and filename tokens are unchanged; `scripts/063_e2e_verify.py` still parses them.
- The `max_files` live smoke path still passes, and a test asserts production leaves `max_files` unset.

## Dependency graph

```json
{
  "plan": 237,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": true},
    {"id": "T2", "depends_on": [], "parallel": true},
    {"id": "T3", "depends_on": [], "parallel": true}
  ]
}
```
