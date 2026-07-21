---
status: DRAFT
created: 2026-07-21
plan: 134
title: Operational ERA5→forecast forcing bridge + resolution-general (daily/sub-daily) forcing
scope: One priority forcing construction (observed ERA5 → IFS gap-fill) via the gateway operational endpoint, parametric in the model time_step. Nepal/gateway only.
depends_on: []
blocks: []
---

# Plan 134 — Operational forcing bridge + resolution-general forcing

## Status

**DRAFT.** Grill-me DONE (2026-07-21; decisions locked below). For `/plan` adversarial review before READY.

## Context — the ERA5→forecast seam, and the resolution question

At an operational forecast cycle, `services/operational_inputs.py::assemble_station_operational_inputs`
fetches **past_dynamic** reanalysis over `[lookback_start, issue_time]`, then **future_dynamic** NWP
after `issue_time`. But ERA5-Land lags **~8 days (ragged)**, and our
`adapters/recap_gateway.py::RecapGatewayReanalysisAdapter.fetch_reanalysis` (`:934`) calls the **pure**
`client.ecmwf.era5_land_reanalysis` (`:979`) then `_drop_forecast_fill_rows` (`:1005`, strips any
non-observed rows). So the recent-days tail up to `issue_time` comes back **empty**, and the endpoint
**hard-errors** past the latency edge (`ApiDataUnavailableError` → `RecapDataUnavailableError` →
runoff-only). Verified live 2026-07-20 (Plan 121 §Live probe).

The gateway **can** close the gap: its `operational` / `ifs_gap_fill` endpoints stitch observed ERA5
with **IFS gap-fill** (old IFS forecasts, `source=ifs`, `source_run`=producing cycle, resampled to
`subdaily_resolution ∈ {6,12,24}` h). We do not call them today.

DHM (Nepal) wants **sub-daily** forecasting, so this plan designs the forcing construction
**resolution-general (daily and sub-daily jointly)** from the start.

## Locked decisions (owner grill-me, 2026-07-21)

### D1 — One priority forcing construction, not two channels

There is **one** forcing construction, used identically for training and inference: **observed
ERA5-Land where it exists; IFS gap-fill only where ERA5 is absent *and* an IFS forecast for that
position exists.** This is exactly the gateway `operational` endpoint (ERA5-preferred, IFS-fill for the
latency window). The core change is: the gateway reanalysis path calls `operational` instead of the
pure `era5_land_reanalysis` + strip.

The earlier "two channels with a leakage wall" framing was **over-engineered** (owner). The priority is
**self-guarding**: ERA5-Land is only ever absent near the *real-time edge*, which historical training
never touches — so historical training draws **all observed** (no fill, no guard needed); inference
draws ERA5 body + IFS-fill tail (the bridge, for free).

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

### D3 — Highest-resolution ingest + consumption-time aggregation (extends the existing architecture)

The repo **already** does this (`docs/architecture-context.md`): forcing is **stored at native
resolution** (`historical_forcing` per-`valid_time`; ERA5-Land hourly, arch:578, onboarding 5.4 "decades
of hourly data"), and **aggregated to the model's `time_step` at consumption** in the service
(`operational_inputs._aggregate_nwp_records_to_time_step` → shared `resample_to_time_step`, precip SUM /
temp MEAN); the model receives forcing at *its* step and never aggregates itself. Sub-daily is already
assumed (arch:1059 "daily or 6-hourly", 1598 per-time-step models `lstm_hourly`/`lstm_daily`,
timestep-dependent skill S.4).

So the bridge **follows this principle**: fetch at highest native resolution (ERA5 **hourly**; IFS-fill
finest the gateway serves = **6 h**; IFS forecast native 3 h→6 h); store hourly; aggregate to the
model's `time_step` at consumption — daily *and* sub-daily, one stored series serving any step.

The one gap: `types/forcing_schema.py::ForcingResolution` is **`DAILY`-only** (`:26`), and
`CANONICAL_FORCING_SCHEMA.resolution = DAILY` (`:55`) — the *machinery* handles any step, the
*declaration* has not caught up. **D3 extends the schema** to be resolution-general (declare sub-daily
steps, or carry a parametric `time_step`), matching the machinery.

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

### D5 — Point-in-time is the one invariant; (a) trust the endpoint, gated by live verification

The only way D1 leaks is a run over a window *near the real-time edge* (a recent hindcast, or fitting on
very recent data) pulling in IFS runs issued *after* its own issue-time. Owner chose **(a): trust the
`operational` endpoint honors `source_run ≤ issue_time`.**

**But this is unverified** (the `operational` endpoint may fill "up to *now* with *current* runs" rather
than to an arbitrary historical issue-time), and the gateway is not reachable from the owner's home
network. So **live-verifying `operational` point-in-time semantics is a HARD GATE** before the
training/hindcast path relies on it (mirrors the Plan 133 §STAC-re-probe / "verify the external contract"
discipline). **Fallback if verification fails** (kept on the shelf, not built now): call `operational`
only at the live inference edge (`issue_time ≈ now`) and keep pure-ERA5 for training/hindcast — same
priority construction, provably leak-free.

Additional self-guards (belt-and-braces, both already true): the inference fill is **fetched fresh per
cycle and never persisted** — `operational_inputs` does not write `historical_forcing` (that is Flow 6 /
training's job), so fill can never enter the training store; and historical training windows are
all-observed by D1.

## Objective

Give the operational forecast a complete past window up to `issue_time` by drawing IFS gap-fill where
ERA5-Land has not yet arrived, via one resolution-general (daily/sub-daily) priority construction that
serves any model `time_step`, without leaking forecast data into training.

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

1. **Gateway bridge fetch (`adapters/recap_gateway.py`).** A path that calls
   `client.ecmwf.operational` (ERA5 + IFS gap-fill, `include_provenance=True`) at the finest supported
   `subdaily_resolution`, returning observed body + fill tail with per-row `source`/`source_run`
   provenance. Replaces the pure-endpoint + `_drop_forecast_fill_rows` on the operational path. Decide
   in `/plan` whether this is a new method vs a mode on `fetch_reanalysis`, and how it satisfies the
   Protocol (interacts with **Plan 121 Task 2E** A/B/C — the Flow-6 adapter fork).
2. **Resolution-general forcing schema (`types/forcing_schema.py`).** Extend `ForcingResolution` /
   `CANONICAL_FORCING_SCHEMA` to declare sub-daily; keep storage native-resolution; consumption
   aggregates to the model `time_step` via the existing `resample_to_time_step`.
3. **Resolution-floor enforcement.** At consumption, raise `ConfigurationError` when the model
   `time_step` is finer than the coarsest source its window needs.
4. **Operational assembly wiring (`services/operational_inputs.py`).** Use the bridge on the forecast
   path; keep training/hindcast (`training_data.py`, Flow 6, `hindcast.py`) on the observed path until
   D5 verification.
5. **D5 live-verification gate** — a probe (run when the gateway is reachable) proving `operational` is
   point-in-time; documented result gates the training/hindcast path.
6. **Reported limitation** — the interim tail mismatch (D2) documented as a results caveat.

## Interaction with other plans

- **Plan 121 Task 2E** (Flow-6 Recap reanalysis adapter A/B/C fork) — this bridge is the operational
  (Flow-1) counterpart; resolve 2E and this together so the adapter's contract is coherent.
- **Plan 082** (recap operational readiness) — the operational forecast path this bridge feeds.
- **Plan 115b** (Swiss forcing) — the Swiss analogue; explicitly *separate* (D-non-goals).
- **Plan 081** (offline adapter) — the future point-in-time-fill training source; separate.

## Tests (to harden in `/plan`)

- Priority: given an ERA5 body + an IFS-fill tail from a faked `operational` response, the construction
  yields observed for the body and fill for the tail, with provenance preserved.
- Self-guard: a historical window (ERA5 complete) yields **all observed, zero fill**.
- Resolution-general: a daily and a 6-hourly model each get a consistent series aggregated to their step
  from the same native-resolution source.
- Resolution floor: a model declaring a step finer than the coarsest source in its window raises
  `ConfigurationError`.
- No-persist: the operational bridge fetch does not write `historical_forcing`.
- Training path unchanged: `training_data.py` / Flow 6 / hindcast still fetch observed-only.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

**Doc sync:** `docs/spec/types-and-protocols.md` (ForcingResolution / schema), `docs/architecture-context.md`
(the bridge + the resolution-general forcing note), Plan 121 Task 2E.

## Verification

- Unit tests above (priority, self-guard, resolution-general, floor, no-persist, training-unchanged).
- **Live gateway (D5, when reachable):** probe `operational`/`ifs_gap_fill` to confirm point-in-time
  (`source_run ≤ issue_time`) and the finest working `subdaily_resolution`; record the result — it gates
  the training/hindcast path and the sub-6 h claim.

## References

- Plan 121 §Live probe (the design gap) + Task 2E.
- `adapters/recap_gateway.py::RecapGatewayReanalysisAdapter` (`:934/979/1005`);
  `services/operational_inputs.py` (`_aggregate_nwp_records_to_time_step`, `resample_to_time_step`);
  `types/forcing_schema.py` (`ForcingResolution` `:26`, `CANONICAL_FORCING_SCHEMA` `:55`);
  `protocols/adapters.py::WeatherReanalysisSource` (`:47`).
- `docs/architecture-context.md` (highest-resolution storage + consumption aggregation; sub-daily
  assumptions: `:578`, `:1059`, `:1598`, S.4).
- recap-dg-client `ecmwf.operational` / `ecmwf.ifs_gap_fill` (`subdaily_resolution`, `source`/`source_run`).
