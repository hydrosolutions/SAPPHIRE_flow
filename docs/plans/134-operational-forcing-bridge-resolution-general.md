---
status: DRAFT
created: 2026-07-21
plan: 134
title: Operational ERA5→forecast forcing bridge + resolution-general (daily/sub-daily) forcing
scope: Channel-separated interim — the operational forecast (Flow-1) path calls the gateway `operational` endpoint (observed ERA5 → IFS gap-fill), parametric in the model time_step; training/hindcast stay on the unchanged pure `era5_land_reanalysis`. Unifying onto one call path is a future step gated on D5. Nepal/gateway only.
depends_on: []
blocks: []
---

# Plan 134 — Operational forcing bridge + resolution-general forcing

## Status

**DRAFT.** Grill-me DONE (2026-07-21) → `/plan` (5 rounds, escalated). All escalation findings folded
(owner, 2026-07-21): **D6** fetch-shape closed = HRU-keyed batch-capable fetch-once; **Scope-3** binding
closed = (b) synthesize from `GatewayPolygonResolver`; **Scope-4(ii)** hardened = strict
`past < issue_time` split, no partial-bucket leak; **D7** added = cadence-snap `issue_time` (fail loud).
Awaiting a confirming independent review → owner READY.

## Context — the ERA5→forecast seam, and the resolution question

At an operational forecast cycle, `services/operational_inputs.py::assemble_station_operational_inputs`
fetches **past_dynamic** reanalysis over `[lookback_start, issue_time]`, then **future_dynamic** NWP
after `issue_time`. But ERA5-Land lags **~8 days (ragged)**, and our
`adapters/recap_gateway.py::RecapGatewayReanalysisAdapter.fetch_reanalysis` (`:934`) calls the **pure**
`client.ecmwf.era5_land_reanalysis` (`:979`) then `_drop_forecast_fill_rows` (`:1005`, strips any
non-observed rows). So the recent-days tail up to `issue_time` comes back **empty**, and the endpoint
**hard-errors** past the latency edge (`ApiDataUnavailableError` → `RecapDataUnavailableError`, which the
forecast cycle's blanket `except` turns into a **per-station hard failure / group skip** for that cycle —
see D1a; *not* the `runoff_only` mode). Verified live 2026-07-20 (Plan 121 §Live probe).

The gateway **can** close the gap: its `operational` / `ifs_gap_fill` endpoints stitch observed ERA5
with **IFS gap-fill** (old IFS forecasts, `source=ifs`, `source_run`=producing cycle, resampled to
`subdaily_resolution ∈ {6,12,24}` h). We do not call them today.

DHM (Nepal) wants **sub-daily** forecasting, so this plan designs the forcing construction
**resolution-general (daily and sub-daily jointly)** from the start.

## Locked decisions (owner grill-me, 2026-07-21)

### D1 — One priority forcing *construction*; this plan wires it on the operational path only (channel-separated interim)

Conceptually there is **one** priority forcing construction: **observed ERA5-Land where it exists; IFS
gap-fill only where ERA5 is absent *and* an IFS forecast for that position exists.** This is exactly the
gateway `operational` endpoint (ERA5-preferred, IFS-fill for the latency window). Because ERA5-Land is
only ever absent near the *real-time edge*, a historical window (training/hindcast) draws **all
observed** anyway, while the live-inference window draws ERA5 body + IFS-fill tail — the same
construction, different data by position.

**What this plan actually builds (scoped precisely, to resolve the D1↔D5 tension):** the **operational
forecast (Flow-1) call site** switches to the gateway `operational` endpoint; **training / hindcast /
Flow-6 keep the unchanged pure `era5_land_reanalysis` + `_drop_forecast_fill_rows`.** This is the SAFE,
channel-separated interim. Unifying both onto a single `operational` call path is an **explicit FUTURE
step, gated on the D5 live-verification** — it is **not** this plan's deliverable.

Concretely, the change is scoped to the *operational call site* (`services/operational_inputs.py::assemble_station_operational_inputs`
past_dynamic fetch, `:406`), **not** to `EcmwfApiLike.era5_land_reanalysis` as a whole and **not** to
every caller of `RecapGatewayReanalysisAdapter.fetch_reanalysis`. An implementer must not route
`training_data.py` / `hindcast.py` / Flow-6 through `operational` on the strength of this plan.

> **Note (why not one call path now):** routing the single `fetch_reanalysis` call through `operational`
> unconditionally — as an earlier draft of D1 implied ("core change: calls operational instead of
> era5_land_reanalysis") — would draw unverified IFS gap-fill into the **training store** before
> point-in-time semantics are confirmed (D5), a real leakage risk. The channel separation here is the
> same shape Plan 121 called "two channels"; the owner's objection was to a permanent *leakage-wall
> architecture*, not to this bounded interim. We keep the two call sites until D5 converges them.

### D1a — Both-absent case: propagate `RecapDataUnavailableError` (which today = per-station hard failure / group skip, NOT the `runoff_only` mode)

D1's construction has a residual position D5's probe actually hit ("operational hit a missing ERA5 date
07-03" with gap-fill also unavailable, Plan 121 §Live probe): a slot inside the requested window where
**both** ERA5 is absent **and** no fill-eligible IFS run exists yet. **Decision:** the bridge does **not**
invent a shorter window silently. It propagates the existing `RecapDataUnavailableError` (as
`fetch_reanalysis` does today via `ApiDataUnavailableError` → `RecapDataUnavailableError`,
`adapters/recap_gateway.py`). This is deliberately *not* a `short_lookback`-style WARNING-and-proceed
(Plan 097): a gap *inside* the past window (not merely a short one) would hand the model a discontinuous
series.

**Correction (reviewer blocker — the earlier "→ runoff-only" claim was FALSE for this call path):** a
`RecapDataUnavailableError` raised during the **past_dynamic** reanalysis fetch does **not** produce the
system's `runoff_only`/`effective_runoff_only` mode. That mode means "skip only NWP(future_dynamic)-consuming
models; native/fallback models on the same station still forecast" (`run_forecast_cycle.py:1790-1799`,
`:1832-1835`). But the past_dynamic bridge error propagates out of `assemble_station_operational_inputs`
and is caught by the **blanket** `except Exception as exc:` in station assembly
(`run_forecast_cycle.py:1868-1873` → `stations_failed += 1; continue`) and by the equivalent
`except Exception` in group assembly (`:2214-2222` → `errors.append`, `continue`). So the actual outcome is
**a per-station hard failure this cycle** (zero forecast for that station, including from models that don't
even need past_dynamic — because assembly is called once per station with the *union* of all assigned
models' requirements, built by `build_superset_requirements` (`operational_inputs.py:258-315`) and applied
in Flow-1 at `run_forecast_cycle.py:1818-1826`) and **the whole group skipped**. This is materially
harsher than `runoff_only`, and it is the same outcome the pre-bridge latency edge already produces
(the earlier text conflated the two paths).

**Scope decision for this plan (baseline = accept today's behaviour):** the bridge simply propagates
`RecapDataUnavailableError`; per-station hard-failure / group-skip is the accepted outcome, requiring **no
new Flow-1 wiring**. Tests pin the *actual* behaviour (station fully skipped / group skipped), not parity
with `effective_runoff_only` (see Tests §both-absent).

**Decision (deferred, was an open fork — reviewer minor): graceful degradation is OUT of this plan.** The
owner's original intent — keep native/fallback runoff-only models forecasting when only the weather-forcing
tail is missing — would require adding Flow-1 handling that catches the bridge `RecapDataUnavailableError`,
rebuilds inputs *without* weather forcing, and lets native/fallback runoff-only models run (analogous to the
existing `runoff_only` path, `run_forecast_cycle.py:1790-1799`,`:1832-1835`). That is a **second, independent
failure-mode redesign** — a behaviour change to failure semantics, not required to close the
ERA5→forecast latency gap this plan exists to fix, and it carries its own station+group tests and Flow-1
branching. **This plan ships the bridge with today's per-station-hard-failure / group-skip outcome unchanged**
(already the accepted baseline, lines above). If the owner still wants graceful degradation, file it as a
separate future plan; **do not bundle it here.**

### D2 — Target design trains on the fill; interim trains on observed (accepted, reported)

The **default/target** is point-in-time-correct forcing (observed body + forecast-fill tail) in both
training and inference — the fill is a legitimate, point-in-time feature (old IFS forecasts issued
*before* the tail they fill), not leakage.

**Interim reality (now):** there is no historical IFS-forecast archive yet, so training over historical
periods fills the recent-tail *position* with actual observed ERA5-Land (which exists historically),
**not** reconstructed old-IFS fill. Inference uses live IFS fill. This is a **known train/inference
mismatch in the tail**, **accepted and reported in results** as a limitation; it converges as we
collect forecasts. This interim is what D1's self-guarding produces automatically (historical training =
all observed).

### D3 — Highest-resolution fetch + consumption-time aggregation (and the real gap: past_dynamic is NOT aggregated today)

The architecture *intends* highest-resolution storage + consumption-time aggregation to the model's
`time_step` (`docs/architecture-context.md`: native `historical_forcing` per-`valid_time`, ERA5-Land
hourly at arch:578, onboarding 5.4 "decades of hourly data"; sub-daily assumed at arch:1059 "daily or
6-hourly", 1598 `lstm_hourly`/`lstm_daily`, timestep-dependent skill S.4).

**Correction to the earlier premise (this was the blocker):** that aggregation is real only for the
**future_dynamic / NWP** path — `operational_inputs._aggregate_nwp_records_to_time_step` →
`resample_to_time_step` (`operational_inputs.py:447`). The **past_dynamic** path does **not** resample:
`operational_inputs.py:406` fetches raw reanalysis, `:412` pivots it via `_raw_forcing_to_dataframe`, and
`:499`/`:501` passes it into `StationInputData.past_dynamic` **as-is** (training and hindcast likewise:
`training_data.py:244` `_select_feature_columns(forcing_df, ...)`, `hindcast.py:196`
`forcing_df.filter(...)`). It "works" today only because the stored reanalysis already arrives at the
daily model step — i.e. the model receives forcing at its step **by coincidence of the store's daily
resolution**, not by an aggregation step.

The bridge breaks that coincidence: `client.ecmwf.operational` serves **6-hourly** by default
(`subdaily_resolution=6`, recap-dg-client `ecmwf.py:98`). A **daily** model consuming a 6-hourly bridge
tail would silently receive 4× the rows per day with no aggregation → wrong features. **So the bridge
MUST add an explicit past_dynamic aggregation to the model `time_step` on the operational path**, mirroring
the NWP path (precip SUM / temp MEAN via the shared `resample_to_time_step`). This is a new task with
daily- and 6-hourly-model known-answer tests (Scope item 4, Tests §resolution-general).

**Bucket-grid alignment (reviewer major — required for non-midnight issue times):** `resample_to_time_step`
uses `group_by_dynamic("timestamp", every=…)` (`training_data.py:93-98`), which is **calendar-bucketed**
(daily buckets anchor to UTC midnight). For a non-midnight `issue_time` (e.g. a 06Z cycle) a raw daily
aggregation therefore (a) **backdates** the tail bucket to UTC midnight *before* `issue_time` and (b) can
emit a **partial** boundary bucket outside the intended `[lookback_start, issue_time)` grid — the exact
failure the **future_dynamic/NWP** path already guards against with `_filter_and_cap_daily_records`
(`operational_inputs.py:452-462`, "Daily UTC-calendar-day bucketing of a non-midnight cycle backdates the
issue-day bucket … Drop backdated buckets … cap to … forecast_horizon_steps"). The past_dynamic aggregation
MUST apply the **same discipline**: define the required output grid as exactly `lookback_steps` **complete**
buckets ending strictly before `issue_time`, and post-filter/cap the aggregated rows so no bucket precedes
`lookback_start`, none lands at/after `issue_time`, and exactly `lookback_steps` reach the model. The daily-
and 6-hourly known-answer tests (Scope item 4) MUST include a **non-midnight (06Z) issue-time regression**
asserting those three grid invariants (Tests §resolution-general).

**Correction (reviewer major — the earlier "arrives daily by coincidence" defence was wrong for this
Nepal-scoped plan):** the previous trade-off note claimed `training_data.py`/`hindcast.py` need no
past_dynamic resample because "current v0 training forcing already arrives daily." That is true only of the
**Swiss/MeteoSwiss** training path, which is unrelated to this Nepal/gateway plan. For the Nepal path this
plan is scoped to, ERA5-Land is **hourly** (Plan 121 §Live probe: "Observed ERA5-Land is hourly"), and
`RecapGatewayReanalysisAdapter.fetch_reanalysis` has **zero production callers in `src/` today** (D6) — so
there is no "daily-by-coincidence" Nepal training behaviour to preserve. The real trigger for the
past_dynamic aggregation gap is **not** "a sub-daily model gets trained"; it is simply **Plan 121 Task 2E
landing** (the sibling plan named in §Interaction), which wires `fetch_reanalysis` into Flow-6/training for
the first time — at which point native-hourly ERA5 rows flow through **un-resampled for ANY model
`time_step`, daily included**, because `training_data.py:244` (`_select_feature_columns`) and `hindcast.py`
pass past_dynamic through raw while only `past_targets`/`future_dynamic` are resampled
(`training_data.py:237-239`,`:245-249`).

**Resolution (fold the fix in now — it is a no-op today):** Scope item 4 therefore ALSO applies
`resample_to_time_step` to `past_dynamic_df` in `training_data.py:244` and the equivalent `hindcast.py`
path, using the same precip-SUM / temp-MEAN methods. `resample_to_time_step` is a **documented no-op when
the cadence already matches** `time_step` (`training_data.py:51` docstring: "Returns as-is if the data
cadence already matches `time_step`"), so it costs nothing for today's Swiss daily-on-daily case and closes
the gap **before Task 2E can trigger it**. **Dependency note:** Plan 121 Task 2E MUST NOT land without either
this resample or an equivalent fix; recorded as a coordination constraint in §Interaction. (This does not
route training/hindcast through the `operational` bridge — the D1 channel separation holds; only the local
aggregation call is added.)

**Schema note (dropped from scope — was speculative):** `types/forcing_schema.py::ForcingResolution`
(`:25`, `DAILY`-only) and `CANONICAL_FORCING_SCHEMA` (`:37`) are **not** on this bridge's code path.
Verified: those symbols are consumed only inside their own module, by
`tests/unit/adapters/test_meteoswiss_open_data_reanalysis.py` (the Swiss/MeteoSwiss conformance test), and
by `tests/unit/types/test_forcing_schema.py` (which pins the daily resolution at `:80-81`) —
**never** by `services/operational_inputs.py`, `adapters/recap_gateway.py`, or `resample_to_time_step`,
which take a plain `timedelta time_step` and are already resolution-general with or without the enum.
Widening the enum unblocks nothing for the Nepal bridge, so **D3 no longer touches the schema** and Scope
item 2 is removed. Defer the `ForcingResolution` generalization to whichever future plan actually
onboards a sub-daily model.

### D4 — Resolution floor, enforced loud

Sources have different native steps: ERA5-Land **1 h**, IFS gap-fill **6 h**, IFS forecast **3 h→6 h**.
You can aggregate *up*, never synthesize *down*. So the finest consistent forcing across the
observed→fill→forecast seam is bounded by the **coarsest source in the window ≈ 6 h**. A model whose
`time_step` is finer than the coarsest source it needs cannot be served a consistent series → **raise
`ConfigurationError`** (the 115a "fail loud on bad config" discipline), not silently under-serve.
An all-observed hourly *training* window is fine (ERA5 is hourly); only the fill/forecast seam imposes
the 6 h floor. **6-hourly is the finest fully-gateway-supported sub-daily grid**; finer (3 h/1 h across
the seam) is **not promised** until the gap-fill is live-verified to deliver it (the probe saw
`subdaily_resolution=3` not rejected at validation but returning no data — unverified).

**Implementation is a direct `timedelta` comparison, independent of the schema enum** (D3): compare the
model's `time_step: timedelta` against the deployment/source minimum cadence (the bridge's
`subdaily_resolution` → a `timedelta`) inside the new bridge-fetch code.

**Scope narrowing (reviewer minor — ship ONLY the consumption backstop; defer the onboarding gate).** An
earlier draft carried *two* enforcement points. But an **onboarding/activation gate** in
`services/model_onboarding.py` guards a case that, by this plan's own admission (D3, Scope item 6), **cannot
currently occur** — no sub-daily model is onboarded or planned here. So this plan ships **only the runtime
consumption backstop** (the sole currently-reachable enforcement), and **defers the onboarding gate** to the
same future sub-daily-onboarding plan that lands D3's training/hindcast aggregation follow-on and the D5
unification. The deferred gate's design (for that plan's benefit, so the reasoning isn't lost): compare the
model's **resolved `requested_time_step`** — the single step it will be run at, the value
`model_onboarding.py:223` already keys on, **NOT** the finest member of `supported_time_steps` (a model
supporting `{1h, 6h}` but requested at 6h is serviceable on a 6-hour bridge and must not be skipped) —
against the deployment/source minimum cadence, skipping before activation only when *that* step is finer.
(FI dynamic input keys are already `timedelta`s — `dynamic: dict[datetime.timedelta, SpatialInputSpec]`,
`forecast_interface/input/requirement.py:42` — so the cadence comparison is well-typed end to end.)

**This plan's single enforcement point — Consumption self-guard (Flow-1 over *all* assignments, not just the
assembly step).** As a runtime backstop, raise `ConfigurationError` when a model's resolved `time_step` is
finer than the coarsest source in the assembled window.

   **Correction (reviewer major — the backstop cannot live inside `assemble_station_operational_inputs`
   alone):** that function receives a **single** `time_step`
   (`services/operational_inputs.py:325`,`:1865`). Flow-1 derives that one step from the first/fallback
   assignment (`run_forecast_cycle.py:1787-1800`), only **warns** on heterogeneous assignment steps
   (`:1838-1844`), then passes **all** `sorted_assignments` onward to `run_station_forecast`
   (`:1892`), which iterates every assignment (`services/run_station_forecast.py:327-356`) — so a
   lower-priority model activated at a finer step than the assembly step would slip past a backstop keyed
   on the single assembly `time_step`. Therefore the floor check must run in **Flow-1 over every active
   assignment's resolved `time_step`** (station and group paths) before assembly/run, OR enough assignment
   context must be passed into the backstop. `assemble_group_operational_inputs`
   (`services/run_group_forecast.py:128-150`) fans a single `time_step` out per member station and needs the
   same treatment. Add a test with a lower-priority 1 h assignment behind a 6 h primary to prove the finer
   assignment is rejected (Tests §resolution floor).

### D5 — Point-in-time is the one invariant; unification is gated by live verification

The only way the *unified* single-path design would leak is a run over a window *near the real-time edge*
(a recent hindcast, or fitting on very recent data) pulling in IFS runs issued *after* its own
issue-time. This depends on the `operational` endpoint honouring `source_run ≤ issue_time`.

**That is unverified** (the endpoint may fill "up to *now* with *current* runs" rather than to an
arbitrary historical issue-time), and the gateway is not reachable from the owner's home network. So the
plan **does not stake anything on it now**: per D1, this plan ships the **channel-separated interim** —
`operational` only at the live inference edge (`issue_time ≈ now`, Flow-1), pure-ERA5 for
training/hindcast — which is **provably leak-free regardless** of the endpoint's point-in-time semantics,
because training never touches the fill.

**Live-verifying `operational` point-in-time semantics is the HARD GATE for the FUTURE unification only**
(routing training/hindcast/Flow-6 onto `operational`), not for anything in this plan (mirrors the Plan
133 §STAC-re-probe "verify the external contract" discipline). The probe is a Scope item (7) with a
documented result; **it has no committed follow-on plan yet — noted as an open owner action** (schedule
when the gateway is reachable). Until it passes, the two call sites stay separate.

Additional self-guards (belt-and-braces, both already true): the inference fill is **fetched fresh per
cycle and never persisted** — `operational_inputs` does not write `historical_forcing` (that is Flow 6 /
training's job), so fill can never enter the training store; and historical training windows are
all-observed by D1.

### D6 — Fetch shape: HRU-keyed, batch-capable fetch-once (DECIDED, owner 2026-07-21)

**Reviewer major — must be resolved in `/plan`, not left implicit.** As drafted, Scope items 2+3 place the
live gateway `operational` HTTP call **inside** `RecapGatewayReanalysisAdapter.fetch_reanalysis`, which
`assemble_station_operational_inputs` invokes **once per station**
(`services/operational_inputs.py:406`), inside Flow-1's per-station assembly loop (the loop with the
blanket `except Exception` at `run_forecast_cycle.py:1868-1873`). `assemble_group_operational_inputs`
fans this out further — it calls `assemble_station_operational_inputs` once per member station in a list
comprehension (`services/run_group_forecast.py:128-150`), so GROUP models get **no batching** either. For a
project explicitly scaled to **~1000 stations, sub-daily** (MEMORY `project_v0_scale`; DHM sub-daily is this
plan's own motivation), that is one live external call per station **per cycle**, plus one per member
inside every group.

**This diverges — unexamined — from the sibling pattern Flow-1 already uses for the structurally identical
problem (external, laggy, real-time data): future_dynamic/NWP is fetched ONCE per cycle at flow-start
(`run_forecast_cycle.py:1014-1142`), basin-extracted, written to `weather_forecast_store`; every station
then does a cheap STORE READ (`weather_forecast_store.fetch_weather_forecasts`,
`operational_inputs.py:426`) rather than a live per-station external call.** Note also that
`RecapGatewayReanalysisAdapter` has **zero production callers** in `src/` today (`select_reanalysis_source`,
`adapters/hybrid_reanalysis_factories.py:78`, is entirely store-mediated for both existing
`reanalysis_source ∈ {single, hybrid}` modes) — so Scope item 3's Nepal wiring is **genuinely new
infrastructure**, exactly the point to choose the fetch shape deliberately rather than inherit the
per-station-call shape of the previously-unwired adapter method.

**Decision (owner): HRU-keyed, batch-capable fetch-once — not a per-station live call.** The fetch is
keyed by **distinct gateway HRU**, not by station: Flow-1 fetches the bridge `operational` data **once per
distinct HRU per cycle** into an in-memory dict passed down for the single cycle (no `historical_forcing`
write — D5 no-persist), and per-station / per-group-member assembly **reads from that dict**
(`assemble_station_operational_inputs` stays a pure reader). This reuses the adapter's existing
`_group_by_hru` grouping (`adapters/recap_gateway.py:574`), which today is defeated by being called with
one station's bindings at a time, and mirrors the NWP fetch-once-then-store-read pattern already proven at
~1000-station/sub-daily scale (`run_forecast_cycle.py:1014-1142`).

**Why HRU-keyed (not "one global batch"): support both wire shapes without redesign (owner).** The gateway
*supports* multi-station batch — it returns a **wide-format DataFrame, one series per polygon** from a
GeoPackage of many basins (`docs/requirements/01-data-gateway-requirements.md:38`, G11 `:141`; owner
confirmed for v1). But the **pinned** client's `operational()`/`ifs_gap_fill()` take a single `hru_code`
(no batch param — `../recap-dg-client/recap_client/ecmwf.py:91-116`). Keying the fetch by HRU makes both
work with **no consumer change**: **now**, one `operational()` call per distinct HRU (N stations over M
distinct HRUs = M calls, not N — the dedup is the win, and stations sharing an HRU cost one call); **later**,
when a batch `operational` endpoint is exposed, the same HRU-keyed producer collapses those M calls into
one (or a few) wide-DataFrame batch calls — the per-HRU cache consumers are unchanged. "Multiple batches,
one per HRU" is exactly this shape. **Dependency flagged:** confirm the batch/wide-DataFrame mechanism
covers the `operational`/gap-fill data (not just plain forcing) when the gateway is reachable — until then
the per-HRU-call implementation is the buildable path and the batch collapse is a drop-in optimization.

### D7 — Issue-time must be cadence-aligned; snap (fail loud if it can't) (DECIDED, owner 2026-07-21)

**Reviewer major.** Flow-1 resolves an omitted `cycle_time` straight from `clock()`
(`run_forecast_cycle.py:553`) with no cadence snap, so a cycle can run at an arbitrary clock time (e.g.
10:37) — for which the model's `time_step` grid and the ≥6 h bridge cadence have no clean bucket
boundaries, and the D3/D4 "exactly `lookback_steps` complete buckets on the issue-relative grid" cannot be
satisfied. **Decision:** before assembly, **snap `issue_time` down to the latest boundary of the model's
`time_step` / bridge cadence** (the largest step in play) and use the snapped value as the assembly
`issue_time`; if snapping is impossible (a step that does not divide the day, or a model/bridge cadence
mismatch) **raise `ConfigurationError`** (fail loud, per the 115a discipline) rather than build a
misaligned grid. Add a regression for an omitted / unaligned `cycle_time` (e.g. a 10:37 wall-clock run →
snapped to the cadence boundary; a non-dividing step → `ConfigurationError`) — Tests §cycle-time-alignment.

## Objective

Give the **operational forecast (Flow-1)** a complete past window up to `issue_time` by drawing IFS
gap-fill where ERA5-Land has not yet arrived, via the gateway `operational` endpoint, with past_dynamic
aggregated to the model `time_step` (daily/sub-daily) at consumption — leaving training/hindcast on the
unchanged pure-ERA5 path so no forecast data can leak into training.

## Non-goals

- **Swiss RprelimD→ICON seam** — *out*. The Swiss recent-tail is `RprelimD` (preliminary **observed**),
  already handled by Plan 115b's `RhiresD → RprelimD` priority chain; there is no forecast-fill on the
  Swiss side. This bridge is **Nepal/gateway-specific**; do not unify.
- **Radar bias-correction** of the delayed IFS fill — a real future enhancement (owner), out of scope.
- **The offline training file** (past ERA5 + forecasts + old forecasts) — a *future training-data*
  artifact for point-in-time-fill training; not this inference bridge.
- **Sub-6 h forcing across the seam** — not promised until the gap-fill is live-verified to deliver it.
- **Persisting inference fill** — deliberately not stored (D5).

## Scope (to harden in `/plan`)

1. **Extend the client Protocol first (`adapters/recap_gateway.py`).** `EcmwfApiLike` (`:171`,
   `@runtime_checkable`) declares only `ifs_forecast` and `era5_land_reanalysis` — it has **no**
   `operational` / `ifs_gap_fill`, so the new bridge call would not **type-check** against the Protocol
   nor be exercised by the runtime-conformance (`isinstance`, `@runtime_checkable`) tests. (`_guarded_fetch`
   itself only calls the passed callable — `adapters/recap_gateway.py:387` — so it needs no change; the
   Protocol extension is for static typing + conformance, not for `_guarded_fetch`.) Add **`operational`
   only** (reviewer minor — hedge resolved): this plan's bridge design calls **only**
   `client.ecmwf.operational` (D1: "exactly the gateway `operational` endpoint"; Scope item 2 is the sole
   call site). `ifs_gap_fill` has **no call site anywhere in this plan** — adding a Protocol method, fakes,
   and conformance tests for it would be speculative generality, so it is **deferred** to whichever future
   plan actually needs the lower-level primitive (e.g. if `operational`'s point-in-time semantics fail the
   D5 live probe and a manual fill construction becomes necessary). Mirror the recap-dg-client signature —
   `operational(*, hru_code, start_date, era5_variable_name, ifs_variable_name, subdaily_resolution=6, ...,
   include_provenance=True)` (`../recap-dg-client/recap_client/ecmwf.py:91`) — following the existing
   `ifs_forecast`/`era5_land_reanalysis` pattern. Extend the fakes and their conformance tests in the same
   task.
2. **Gateway bridge fetch (`adapters/recap_gateway.py`).** A path that calls `client.ecmwf.operational`
   (ERA5 + IFS gap-fill, `include_provenance=True`) at the finest supported `subdaily_resolution` (6 h),
   returning observed body + fill tail with per-row `source`/`source_run` provenance. Used on the
   **operational path only**; the pure-endpoint + `_drop_forecast_fill_rows` stays for
   training/hindcast/Flow-6 (D1). Decide in `/plan` whether this is a new adapter method vs a mode on
   `fetch_reanalysis`, and how it interacts with **Plan 121 Task 2E** A/B/C (the Flow-6 adapter fork).
   Handle the both-absent case per **D1a** (propagate `RecapDataUnavailableError`).
   **Window post-filter (reviewer major — required):** `client.ecmwf.operational` takes only `start_date`,
   no end (`../recap-dg-client/recap_client/ecmwf.py:91`), whereas `WeatherReanalysisSource.fetch_reanalysis`
   receives both `start` and `end` (`protocols/adapters.py:47-54`) and the pure ERA5 adapter already
   post-filters returned rows to `[start, end)` (`adapters/recap_gateway.py:1010-1012`, because the client's
   `_iso_date` strips the window to bare dates). The bridge **must** do the same: post-filter every
   `operational` response to `[start, end)` before building `RawHistoricalForcing`, so rows at/after `end`
   (e.g. beyond `issue_time`) never leak into `past_dynamic`. Add a regression test (Tests §window-filter).
   **Coverage check (reviewer major — required):** D1a's "the bridge does not silently shorten the past
   window" guarantee only holds if `operational` *raises* on an internal gap. It may instead return a
   **partial** DataFrame (a hole *inside* `[start, end)` with rows on both sides). Today neither the pure
   adapter's `[start, end)` filter (`adapters/recap_gateway.py:1013-1027`) nor the downstream
   `_raw_forcing_to_dataframe` / `StationInputData.past_dynamic` path
   (`services/operational_inputs.py:406-421`) nor training/hindcast (`services/training_data.py:93-99`)
   detects such a hole — a discontinuous series would reach the model. So after the `[start, end)` filter
   and **before** the D3 aggregation, the bridge MUST assert completeness: for each requested
   station/parameter, require the expected bridge-cadence grid (or enough complete source buckets to fill
   every output timestep) across `[start, end)`, and **raise `RecapDataUnavailableError`** on any missing
   internal slot (the same error D1a propagates). Add a regression where the faked `operational` endpoint
   returns a partial DataFrame **without raising**, asserting the bridge raises `RecapDataUnavailableError`
   (Tests §partial-coverage).
3. **Flow-1 source selection/wiring (`flows/run_forecast_cycle.py`).** Today Flow-1 builds
   `forcing_source` from the `HistoricalForcingStore` via `select_reanalysis_source` (`:1594`) and passes
   it into station assembly (`:1850`) and group assembly (`:2193`). Add a Flow-1 task to **select/build
   the Nepal Recap operational bridge source** from the Recap config/client/resolver (gated on the
   deployment being the Nepal/gateway one), while leaving `train` / `hindcast` / `onboard` flows on
   `select_reanalysis_source`. The bridge source must satisfy the same `WeatherReanalysisSource`
   Protocol (`protocols/adapters.py:47`) the assembly functions already consume.
   **Fetch shape per D6 (DECIDED): HRU-keyed fetch-once.** The Flow-1 task fetches `operational` once per
   distinct gateway HRU into an in-memory per-cycle dict; station/group assembly reads from it. One
   `operational()` call per distinct HRU now; collapses to a batch wide-DataFrame call when that endpoint
   is exposed (D6). Do **not** inherit the per-station-call shape.

   **REANALYSIS-binding preflight (reviewer major — required; else the bridge silently returns empty
   past_dynamic).** The bridge consumes the *same* REANALYSIS bindings the observed path does: assembly
   passes only `station_store.fetch_reanalysis_bindings(station_id)` into `fetch_reanalysis`
   (`services/operational_inputs.py:405-411`), the store returns **only** `role=REANALYSIS` rows
   (`store/station_store.py:310-317`), and the Recap reanalysis adapter then `_prefilter`s to
   **active basin-average `nwp_source="era5_land"`** rows (`adapters/recap_gateway.py:504-511`,`:944-948`) —
   returning `[]` (→ empty past_dynamic, **no error**) when none match (`adapters/recap_gateway.py:949-950`).
   Existing reanalysis backfill that *creates* those bindings is **MeteoSwiss-specific**
   (`services/reanalysis_backfill.py:57`,`:109-116`); current Recap Flow-1 wiring builds only the *forecast*
   adapter (`flows/run_forecast_cycle.py:1349-1371`). So a Recap deployment could silently produce empty
   past_dynamic because no station carries an active basin-average `era5_land` REANALYSIS binding.
   **This plan closes that hole via (b) (DECIDED, owner 2026-07-21): synthesize the REANALYSIS configs from
   the Gateway polygon-resolver** (`GatewayPolygonResolver`, `adapters/recap_gateway.py:929`) — the *same*
   resolver the FORECAST path already depends on and that the HRU-keyed fetch (D6) already maps stations
   through — so the binding need **not** be pre-seeded in `station_weather_sources`. Option (a) (a new
   Nepal/Recap onboarding step writing `role=REANALYSIS` rows) is **rejected**: no Nepal/Recap
   weather-source-binding onboarding mechanism exists in-repo to extend (`reanalysis_backfill.py` is
   MeteoSwiss-specific; Flow-1 builds only the *forecast* adapter, `run_forecast_cycle.py:1349-1371`), so
   (a) is a materially larger, currently-unscoped deliverable, whereas (b) reuses the working resolver.
   Still add the **Flow-1 preflight** that **fails loud** (not silent-empty) when a bridge-served station
   cannot be resolved to a serviceable HRU, with a test proving a Recap deployment cannot silently return
   empty past_dynamic (Tests §reanalysis-binding-preflight).
4. **Past-dynamic aggregation to `time_step` (`services/operational_inputs.py` + `training_data.py` /
   `hindcast.py`).** (i) **Operational path:** add the missing resample of `past_dynamic` to the model
   `time_step` (D3) — mirror the NWP path (`_aggregate_nwp_records_to_time_step`/`resample_to_time_step`,
   `:447`; precip SUM / temp MEAN) so a daily model consuming a 6-hourly bridge tail is served daily
   buckets, not 6-hourly rows, **with the D3 bucket-grid alignment** (drop backdated/partial buckets for a
   non-midnight `issue_time`; exactly `lookback_steps` complete buckets strictly before `issue_time`,
   mirroring `_filter_and_cap_daily_records`, `operational_inputs.py:452-462`). (ii) **Training/hindcast
   path:** ALSO apply `resample_to_time_step` to `past_dynamic_df` in `training_data.py:244` and the
   equivalent `hindcast.py` path (precip SUM / temp MEAN) — a **documented no-op when cadence already
   matches** (`training_data.py:51`), so it is inert for today's Swiss daily-on-daily case but closes the
   native-hourly-ERA5 gap **before Plan 121 Task 2E can trigger it** (D3 Correction). This does not route
   training/hindcast through the `operational` bridge — the D1 channel separation holds; only the local
   aggregation call is added.
   **No partial-bucket leak across `issue_time` (reviewer major — required).** Hindcast today splits raw
   forcing `<= issue_time` → past / `> issue_time` → future (`hindcast.py:194`), and `resample_to_time_step`
   calendar-buckets via `group_by_dynamic` (`training_data.py:93`); that combination can fold a **partial
   bucket straddling `issue_time`** into `past_dynamic`, admitting post-`issue_time` (future) sub-steps into
   the past — violating the no-future-leakage requirement (`architecture-context.md:1065`). So on both the
   hindcast and operational paths the split must be **strict `past < issue_time` / future `> issue_time`**
   (native sub-steps), resample on the intended **issue-relative** grid, and keep **exactly `lookback_steps`
   complete past buckets** (drop any backdated/partial bucket — the same rule as
   `_filter_and_cap_daily_records`, `operational_inputs.py:452-462`). Add **failing tests with native-hourly
   ERA5**: a daily model and a 6-hourly model, each with a **non-midnight (e.g. 06Z) `issue_time`**,
   asserting no past bucket contains any sub-step `>= issue_time` and that exactly `lookback_steps` complete
   buckets are produced (Tests §issue-boundary-buckets).
5. **Fill-vs-observed diagnostic (NOT the full `{param}_provenance` contract).** `RawHistoricalForcing`
   carries `source` and `version` (`types/historical_forcing.py:23–24`) but `_raw_forcing_to_dataframe`
   (`:241`) drops both. The bridge needs to *know* how much of the tail was IFS-fill vs observed so the D2
   "reported limitation" is measurable.

   **Correction (reviewer majors — do NOT wire the full spec `{param}_provenance` Enum-column contract
   here):** two independent findings converge on cutting this. (a) The `{param}_provenance` contract
   (`PROVENANCE_SUFFIX`, `validate_forcing_provenance`, `types/model.py:27,42`; spec
   `docs/spec/types-and-protocols.md:1253`) is **never populated or read anywhere in production today** —
   `operational_inputs.py`, `training_data.py`, `hindcast.py`, `recap_gateway.py`, `forecast_interface.py`,
   `api/` all omit it; its only exercisers are its own unit tests
   (`tests/unit/types/test_model.py`, `tests/unit/types/test_forcing_provenance.py`). Wiring it for this one
   call site would create an **inconsistent `StationInputData` contract** (bridge path carries provenance
   columns; every other producer doesn't) for a feature with **zero downstream consumers**. (b) There is no
   clean `ForcingProvenance` member for "an old IFS forecast substituting for a missing observed slot":
   `GAP_FILLED_CLIMATOLOGY`/`GAP_FILLED_PERSISTENCE` name specific fill *methods*, `NWP_DIRECT` is the
   future_dynamic case, `REANALYSIS`/`OBSERVED` are the observed body (`types/enums.py:244-252`). Populating
   the column *correctly* would require a **new enum member** (e.g. `GAP_FILLED_FORECAST`) **plus a spec
   change** — out of proportion for a single call site.

   **Scope decision:** carry the Gateway `source`/`source_run` **only** on `RawHistoricalForcing.source` +
   `.version` (already present, `types/historical_forcing.py:23-24`; D5 fetches these fresh per cycle and
   never persists them) — do **not** add `{param}_provenance` columns to the assembled frame. **No new
   metric/log emission is added here either (reviewer minor — same "zero downstream consumers" test that
   cut the `{param}_provenance` column applies to a bespoke fill-vs-observed structlog field/metric: no
   dashboard, alert, or report reads it today).** `RawHistoricalForcing.source`/`.version` already make the
   D2 mismatch inspectable ad hoc if/when Scope item 8's "reported limitation" write-up needs a number; add
   an emitted metric only once a consumer exists. Full, *repo-wide, consistent* `{param}_provenance`
   population (with the new enum member + spec update) is **deferred to whichever future plan first gives
   that column a consumer**; this plan explicitly leaves `past_dynamic` as unadorned as every other current
   producer (no new inconsistency).
6. **Resolution-floor enforcement (D4) — consumption backstop ONLY.** A runtime backstop that raises
   `ConfigurationError` when a model's resolved `time_step` is finer than the coarsest source in the
   assembled window. The backstop must check **every** active station assignment's resolved `time_step`,
   not just the single assembly step (reviewer major — see D4 single enforcement point). Direct `timedelta`
   comparison; no schema-enum dependency. **The onboarding/activation gate is CUT from this plan** (reviewer
   minor): it guards a case that cannot currently occur (no sub-daily model is onboarded or planned here),
   so it — and its two onboarding test bullets — move to the future sub-daily-onboarding plan (its design is
   preserved in D4 for that plan). This plan ships only the self-contained, currently-reachable backstop.
7. **D5 live-verification gate** — a probe (run when the gateway is reachable) proving `operational` is
   point-in-time (`source_run ≤ issue_time`); documented result is the HARD GATE before any future
   unification of the training/hindcast path onto `operational`.
8. **Reported limitation** — the interim tail mismatch (D2) documented as a results caveat.

*(Former Scope item "Resolution-general forcing schema" removed — see D3 schema note: the enum is off
this bridge's code path.)*

## Interaction with other plans

- **Plan 121 Task 2E** (Flow-6 Recap reanalysis adapter A/B/C fork) — this bridge is the operational
  (Flow-1) counterpart; resolve 2E and this together so the adapter's contract is coherent.
  **Sequencing note (reviewer minor — `depends_on` left `[]` deliberately):** 2E is an open A/B/C fork over
  the same `RecapGatewayReanalysisAdapter` class this plan adds a bridge path to; option (C) "Unify the
  Protocol" (Plan 121 §2E, largest blast radius) could reshape that adapter's contract. This plan does **not**
  hard-depend on 2E (it can ship its bridge path first — Scope item 2 explicitly allows "new method vs mode"),
  but **Scope item 2 MUST be re-checked if 121/2E lands option (C) first**, and if (C) lands first this plan
  should conform to the unified contract rather than add a parallel one. Kept as a documented coordination
  constraint instead of a blocking `depends_on` because neither plan is a hard prerequisite of the other.
  **Hard coordination constraint (reviewer major, D3 Correction):** Task 2E is the change that first wires
  `fetch_reanalysis` into Flow-6/training, at which point native-hourly ERA5 flows through
  `training_data.py:244` un-resampled for **any** `time_step`. So **Task 2E MUST NOT land unless the
  training/hindcast `past_dynamic` resample (Scope item 4(ii)) — or an equivalent fix — lands with or
  before it.** Whichever plan ships first must carry the resample; this plan folds it in now (no-op today)
  precisely so 2E cannot regress on it.
- **Plan 082** (recap operational readiness) — the operational forecast path this bridge feeds.
- **Plan 115b** (Swiss forcing) — the Swiss analogue; explicitly *separate* (D-non-goals).
- **Plan 081** (offline adapter) — the future point-in-time-fill training source; separate.

## Tests (to harden in `/plan`)

- **Protocol extension:** the fake `EcmwfApiLike` gains `operational` (only — not `ifs_gap_fill`, Scope
  item 1) and still satisfies `isinstance(fake, EcmwfApiLike)` (runtime-checkable) — mirrors the existing
  `ifs_forecast` fake tests.
- **Priority:** given an ERA5 body + an IFS-fill tail from a faked `operational` response, the
  construction yields observed for the body and fill for the tail.
- **No provenance columns (Scope item 5):** a mixed `source=era5_land` / `source=ifs` faked `operational`
  response yields an assembled frame that carries **no** `{param}_provenance` columns (consistent with
  every other current producer — full contract deferred). No bespoke fill-vs-observed metric/log field is
  asserted (none is emitted — reviewer minor).
- **Self-guard:** a historical window (ERA5 complete) yields **all observed, zero fill**.
- **Partial-coverage (reviewer major):** a faked `operational` endpoint that returns a **partial**
  DataFrame — a hole *inside* `[start, end)`, rows on both sides, **without raising** — makes the bridge
  raise `RecapDataUnavailableError` (the coverage check, Scope item 2), not silently pass a discontinuous
  series to the model.
- **Reanalysis-binding preflight (reviewer major, Scope item 3):** a Recap deployment where a bridge-served
  station has **no** active basin-average `era5_land` `role=REANALYSIS` binding **fails loud** at the Flow-1
  preflight (not a silent-empty `past_dynamic`). Proves `fetch_reanalysis`'s `_prefilter` returning `[]`
  (`recap_gateway.py:949-950`) cannot silently degrade the cycle. (Complementary positive case: with the
  binding present — or synthesized from the polygon resolver, per the (a)/(b) fork — the bridge produces a
  non-empty `past_dynamic`.)
- **Resolution-general (known-answer, the blocker fix):** a **daily** model and a **6-hourly** model each
  get `past_dynamic` aggregated to their step from the same 6-hourly bridge source — assert daily precip
  = SUM of its four 6-h buckets and daily temp = MEAN, and that the 6-hourly model gets the buckets
  unchanged. (This test fails against today's code, which passes `past_dynamic` through un-resampled.)
- **Non-midnight bucket-grid alignment (reviewer major, D3):** the same 6-hourly bridge source with a
  **06Z `issue_time`** aggregated to a **daily** model asserts the three grid invariants — **no** bucket
  precedes `lookback_start`, **none** lands at/after `issue_time`, and **exactly** `lookback_steps`
  complete buckets reach the model (mirrors the NWP guard `_filter_and_cap_daily_records`,
  `operational_inputs.py:452-462`). Fails against a raw calendar-bucketed aggregation.
- **§issue-boundary-buckets — hindcast strict split (reviewer major, Scope 4(ii)):** with **native-hourly
  ERA5**, a **daily** model and a **6-hourly** model, each at a **06Z `issue_time`**, assert **no**
  `past_dynamic` bucket contains any sub-step `>= issue_time` (strict `past < issue_time`) and **exactly**
  `lookback_steps` complete buckets are produced. Fails against the current `<= issue_time` split +
  calendar-bucketing (`hindcast.py:194`, `training_data.py:93`), which folds a partial straddling bucket
  into the past.
- **§cycle-time-alignment (reviewer major, D7):** an omitted / unaligned `cycle_time` (e.g. a **10:37**
  wall-clock run) is **snapped down to the model/bridge cadence boundary** before assembly and the cycle
  proceeds on the aligned grid; a `time_step` that does **not divide the day** raises `ConfigurationError`.
  Fails against today's raw `clock()` resolution (`run_forecast_cycle.py:553`, no snap).
- **Training/hindcast past_dynamic resample is a no-op today (D3 Correction):** with today's daily-cadence
  Swiss training forcing, adding `resample_to_time_step` to `past_dynamic_df` (`training_data.py:244`) leaves
  the frame byte-for-byte unchanged (cadence already matches → `training_data.py:51` returns as-is) — proving
  the fold-in is inert now while closing the gap before Plan 121 Task 2E lands.
- **Window filter (reviewer major):** a faked `operational` endpoint that returns rows beyond `end`
  (`start_date`-only, no end — `ecmwf.py:91`) yields a `RawHistoricalForcing` list post-filtered to
  `[start, end)`; no row at/after `end` survives (mirrors the pure adapter's guard,
  `recap_gateway.py:1010-1012`).
- **Resolution floor (consumption backstop only — onboarding-gate tests moved to the future
  sub-daily-onboarding plan, reviewer minor):**
  - consumption backstop: a finer-than-cadence mismatch at assembly raises `ConfigurationError`.
  - consumption backstop covers EVERY assignment (reviewer major): a station with a **6 h primary** and a
    **lower-priority 1 h** assignment (so the assembly step is the 6 h primary's) still rejects the 1 h
    assignment — proving the floor check iterates all `sorted_assignments`, not just the single assembly
    `time_step` (D4 single enforcement point). Same for a group member.
- **Both-absent (D1a) — pins ACTUAL behaviour, not `runoff_only`:** a faked window where a slot has neither
  ERA5 nor fill-eligible IFS propagates `RecapDataUnavailableError`; assert the **station is fully skipped
  this cycle** (`stations_failed`, the blanket `except` at `run_forecast_cycle.py:1868-1873`) — including a
  station carrying a native model that doesn't need past_dynamic — and, for the group path, the **group is
  skipped** (`:2214-2222`). This is *not* the `effective_runoff_only` mode; do not assert parity with it.
- **No-persist:** the operational bridge fetch does not write `historical_forcing`.
- **Training path unchanged:** `training_data.py` / Flow 6 / hindcast still fetch observed-only via the
  pure `era5_land_reanalysis` path (assert the bridge/`operational` endpoint is never called there).
- **Schema unchanged (regression):** `tests/unit/types/test_forcing_schema.py` (pins daily resolution at
  `:80`) is left green — this plan does not touch `ForcingResolution`; keep the file in the test task to
  prove no accidental schema drift.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

**Doc sync:** `docs/spec/types-and-protocols.md` (`EcmwfApiLike` operational method),
`docs/architecture-context.md` (the operational bridge + the past_dynamic consumption-aggregation note),
Plan 121 Task 2E. *(No `ForcingResolution`/schema doc change — dropped per D3. No `{param}_provenance` spec
change — the full column contract is deferred, not wired here; Scope item 5.)*

## Verification

- Unit tests above (protocol-extension, priority, no-provenance-columns, self-guard, partial-coverage,
  resolution-general, window-filter, floor, both-absent, no-persist, training-unchanged, schema-unchanged).
- **Live gateway (D5, when reachable):** probe `operational`/`ifs_gap_fill` to confirm point-in-time
  (`source_run ≤ issue_time`) and the finest working `subdaily_resolution`; record the result — it gates
  the **future** unification of the training/hindcast path onto `operational` and the sub-6 h claim, not
  anything shipped by this plan (D5).

## References

*(Trimmed — reviewer minor: every file:line citation now lives inline in Context / D1–D6 / Scope, where it
is load-bearing; a duplicate bibliography just doubles the line-drift maintenance surface. Only pointers
NOT already carried inline are kept here.)*

- **Plan 121** §Live probe (the design gap the bridge closes) + Task 2E (the Flow-6 adapter A/B/C fork this
  plan coordinates with) + "two channels" framing (~lines 126–135) — the channel-separation shape D1 keeps
  as a bounded interim.
- **Plan 082** (recap operational readiness), **Plan 115b** (Swiss forcing analogue, explicitly separate),
  **Plan 081** (offline point-in-time-fill training source, separate) — see §Interaction with other plans.
