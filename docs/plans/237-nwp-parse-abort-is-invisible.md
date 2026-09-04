---
status: DRAFT
created: 2026-09-03
revised: 2026-09-04
plan: 237
title: The NWP fetch must produce a complete, unambiguous cycle or fail loudly — and its abort must not report COMPLETED
scope: Make the NWP fetch/parse yield either a complete, duplicate-free dataset or a named failure — de-duplicate on a collision-safe identity, make the download destination injective, and assert completeness so the outer join cannot NaN-fill silently. Separately, make the NWP-fetch abort reach a non-successful terminal state. Two tasks. No retry logic, no re-fetch, no STAC pagination rework, no alert-channel work.
depends_on: []
blocks: []
source: Measured on the mac mini 2026-09-03/04 after 15 Slack alerts in one day; four failing cycles analysed, hardened through two independent review rounds
---

# Plan 237 — the NWP fetch must be complete and unambiguous, or fail loudly

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality — two tasks

**Do not add:** retry-on-failure, a re-fetch path, STAC pagination rework, new alert channels, a
general gap-marking overhaul (that is Plan 235/236 territory), or a third task.

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

**T1 — The fetch yields a complete, unambiguous dataset, or a named failure.**

*Scope (in) — three parts of one change:*

1. **De-duplicate on a collision-safe identity.** Key on the **URL path of the `href` with the query
   string removed** — i.e. **`netloc` + `path`, not `path` alone**: two different hosts can serve
   the same path, so path-only is not collision-safe. Not the raw signed `href` (its query carries a
   per-request signature; note the repo's probe records the *expiry* as fixed at item creation, so
   do not justify this by expiry — `docs/research/063-meteoswiss-stac-probe.md:141-155`), not the
   basename (two different assets could share one), and not the asset key (cross-item uniqueness is
   never enforced, `:153-161`). Preserve first-seen order. Emit one WARNING naming how many
   duplicates were dropped and which steps/variables they were.
2. **Make the download destination injective.** `_download_asset` names the local file from the
   basename alone (`:887-906`), so two *different* URL paths sharing a basename silently overwrite.
   Derive the destination so that two different `netloc`+`path` values cannot collide — e.g. a short
   digest of `netloc`+`path` inserted **before** the `.grib2` suffix.
   ⚠️ **Preserve the flat `*.grib2` layout and the existing filename tokens.** Checked-in consumers
   assume flat files whose names carry the cycle/step/variable/variant tokens
   (`tests/unit/adapters/test_meteoswiss_nwp.py:1020-1032`, `scripts/063_e2e_verify.py:81-91`), so
   nested subdirectories, or a digest placed after the suffix, would break them. A digest is
   collision-*resistant*, not injective; that is acceptable here only because the disambiguation is
   belt-and-braces behind part 1's exact-identity de-duplication. **Prefer this to a fatal collision guard:** an
   earlier revision proposed raising, but catalogue-wide basename uniqueness is unproven (the
   evidence is one probe URL and six fixtures), so raising could turn a legitimate listing into a
   whole-cycle abort — the very outcome this plan exists to prevent. Making collisions *impossible*
   needs no such assumption.
3. **Assert completeness against the EXPECTED step grid — not against NaNs in what arrived.**
   🔴 An earlier revision specified "no `(valid_time, member)` slice may be wholly NaN". **That does
   not work**, and a review round proved why: `_combine_cfgrib_datasets` builds `by_valid_time`
   *exclusively from the files that arrived* (`meteoswiss_nwp.py:293-350`), so a step missing from
   **every** file never enters the coordinate at all and no NaN slice exists to find. It would pass
   cleanly on the 302-file cycle — the exact case it was added for — and return a silently shortened
   dataset. Withdrawn.

   Instead: assert over the **full expected `(valid_time` x `member)` grid, per parameter** — not
   over `valid_time` alone, and **both checks are mandatory**:

   - **(i) step coverage** — the observed `valid_time` set must equal the expected set implied by the
     requested window and cadence; fail naming the missing steps and their count.
   - **(ii) member coverage** — within every present `valid_time`, no `(parameter, member)` cell may
     be wholly NaN; fail naming the missing members and times.

   🔴 **(ii) is NOT optional, and an earlier revision that made it "secondary" was wrong.** Step
   coverage alone does not prove member completeness: if only the `ctrl` **or** only the `perturb`
   file is missing for a step, the other still contributes that `valid_time`, so (i) passes while
   `join="outer"` NaN-fills the absent members and the global 21-member check still succeeds
   (`meteoswiss_nwp.py:301-350,979-994`). That is exactly the observed production shape — the
   2026-09-02T18 and 2026-09-03T18 duplicates were **`perturb`-only** — so (i) alone would let the
   majority case through and store NaN as real data.

   *Deriving "expected":* take it from the window the walk actually requests
   (`window_end = cycle_time + 120 h`, `meteoswiss_nwp.py:704-709`) and the cadence, rather than a
   literal `121` in the assertion — 121 is the empirical value confirmed by three healthy cycles at
   exactly 484 files, not an independently sourced constant, and hardcoding it would silently
   mis-fire if MeteoSwiss ever changes cadence or horizon. If the combining code cannot see the
   requested window, thread it in rather than re-deriving it there.

   ⚠️ **The `max_files` smoke path.** `tests/integration/live/test_meteoswiss_nwp_live.py:66-77,89-120`
   deliberately fetches only a handful of files, so a full-grid assertion would fail it by design.

   **Decision — REVERSED from the previous revision, and here is why.** That revision chose to
   "scope the check to the steps actually requested (window grid intersected with `max_files`
   truncation)" and rejected an exemption as creating a silent no-op. A review round showed the
   chosen option is **not implementable**: `max_files` is an *asset-count* cutoff that stops after
   the Nth downloaded asset in catalogue order (`meteoswiss_nwp.py:789-885`); it defines and retains
   **no temporal boundary**, so there is no unambiguous "requested step set" to intersect with, and
   inferring one from the files that arrived is circular — it could never detect an omitted step.

   **So: skip the completeness assertion when `max_files` is set, and log at WARNING that it was
   skipped and why.** The no-op objection does not apply in production: `max_files` defaults to
   `None` (`:375`), the flow passes `None` explicitly (`run_forecast_cycle.py:345`), and the shipped
   `config.toml` leaves it commented out (`:413`). Add a test asserting the production path leaves it
   unset, so the exemption cannot silently become the norm.

*Scope (out):* retrying, re-fetching, changing pagination, the byte/file caps, the age guard,
`_combine_cfgrib_datasets`' member contract, and any change to how gaps are marked downstream
(`is_gap` belongs to Plan 235/236 — T1 fails before a record is built rather than marking one).

*Exit:* a fetch handed duplicated assets returns the distinct set and parses; a fetch of an
incomplete cycle fails with a named error — **both** when whole steps are missing (naming them)
**and** when only the `ctrl` or only the `perturb` files are missing for present steps (naming the
missing members) — instead of returning a shortened or NaN-filled dataset; the assertion is skipped
only when `max_files` is set, which the production path never does; two different
`netloc`+`path` values sharing a basename land in two different files; the flat `*.grib2` layout and
filename tokens are unchanged, and `scripts/063_e2e_verify.py` still parses them; the `max_files`
live smoke path still passes.

*Verification:* `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py -q`, with three tests:

- **(a)** a faked listing repeating items across **both** `PARAM_GROUPS` **and containing at least
  one non-duplicated valid_time** — a single duplicated time returns before cross-time alignment and
  would not reproduce the fault. Assert no duplicate paths, the WARNING names the drops, and the
  parse succeeds.
- **(b)** two incomplete shapes, both RED first: **(b1)** a listing missing **entire steps** —
  assert the error names the missing steps; **(b2)** a listing missing only the `perturb` files for
  some present steps — assert the error names the missing **members**. (b2) is the production shape
  and is the case that step-coverage-alone would wrongly pass. In both, assert **no** record is
  produced.
- **(c)** two different URL paths sharing a basename — assert two distinct local files.

**RED first** for (a) and (b) against today's code. Note the alignment error is caught and
re-surfaced as a generic `AdapterError` (`:961-977`), so (a) must assert against the debug record or
the `AdapterError` message, not the raw pandas text.

**T2 — The NWP-fetch abort must not report COMPLETED.**
*Scope (in):* today `_fetch_nwp_task` converts the adapter exception into a **returned outcome**
(`run_forecast_cycle.py:1340-1349`) and the flow then returns `ForecastCycleResult(health=FAILED)`
*normally* (`:2667-2691`) — a domain-level FAILED inside a Prefect COMPLETED. The flow must instead
reach a non-successful terminal state. **"Re-raise" is not literally available at the task site** —
the exception is already swallowed there, and raising inside the task would make `.result()` fail
before the existing emitter runs. Raise (or set a failed state) at the flow level **after** the
zero-forecast CRITICAL record is written (`:2669-2680`), which the watchdog probes independently of
Prefect state (`ops/watchdog.py:1996-2024`). Confirm exactly one freshness/CRITICAL record is
emitted and that the end-of-cycle emitter (`:3612-3618`) is not double-run.
*Scope (out):* new alert channels, new health metrics, retry policy, and the other zero-output paths
— no operational stations (`:2384-2403`) and ordinary end-of-cycle zero storage (`:3612-3658`) —
which still return normally and are **not** in scope.
*Exit:* the NWP-fetch abort yields a non-successful terminal state; exactly one CRITICAL record; no
new alert.
*Verification:* `uv run pytest tests/unit/flows/test_run_forecast_cycle.py -q` — a test asserting the
abort path does not end successfully **and** that the CRITICAL record is written exactly once.
**RED first.**

## Open question for the owner

**Should a zero-forecast cycle keep re-alerting every 30 minutes?** One failure produced eight pages.
Options: alert once per state transition (the watchdog already emits a distinct `RECOVERED`, so the
machinery exists); widen the interval; or leave it. An operational-signal judgement, **out of T2's
scope**, best answered after T1 lands — the honest volume is unknown until the duplicate bug stops
causing most of it.

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

- `uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py tests/unit/flows/test_run_forecast_cycle.py -q` passes, with the RED-first tests above proven red against the pre-change code.
- `uv run ruff check` and `uv run ruff format --check` clean on changed files.
- `uv run pyright` no worse than the recorded ratchet baseline.

## Dependency graph

```json
{
  "plan": 237,
  "tasks": [
    {"id": "T1", "depends_on": [], "parallel": true},
    {"id": "T2", "depends_on": [], "parallel": true}
  ]
}
```
