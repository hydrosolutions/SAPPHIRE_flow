---
status: DRAFT
created: 2026-09-11
plan: 270
title: A single missing forcing day silently takes a model off half the fleet, and nothing alerts
scope: Record and frame the decisions on ONE measured failure mode — a single absent day in a model's antecedent window fails that model on every station whose window contains it, with no alert and no stored trace. Covers the detection gap, the ingest gap that lets a source-side publication defect become a multi-week outage, and whether interior holes should ever be filled. NOT an implementation plan yet; NOT a change to Plan 261's tail-only fill; NOT a re-opening of the no-imputation contract.
depends_on: [261]
blocks: []
source: 2026-09-11 — measured on the mac mini while validating Plan 261 T1's first post-deploy cycle. Every number below is from that host on that day; re-measure before quoting.
---

# Plan 270 — a single missing forcing day is a silent fleet-wide outage

> ⚠️ **Plan 270 is not PR #270.** PR #270 is Plan 261's merge (`75cf80cf`, 2026-09-11).
> Different namespaces, same number, same day. Do not conflate them in later references.

## Status

**DRAFT — a filing, not a build order.** The owner asked for the observations to be
recorded so the track can be picked up deliberately. The measurements below are settled;
the decisions are not. No task here is ready to implement.

## What was measured

Plan 261 T1 deployed to staging on 2026-09-11 (v0.1.901) and its first cycle at 12:24Z
behaved exactly as designed:

- `past_forcing_tail_filled` **143 times, `past_forcing_tail_unfilled` 0 times**
- it filled `['precipitation@2026-09-10', 'temperature@2026-09-10']` from `icon_ch2_eps`
- reanalysis reaches 09-09, the fill adds 09-10, the aligned bound is 09-11 00:00 — so the
  operational past leg now reaches `past_targets_end`

**And the headline number did not move.** `nwp_regression` served **73 of 148 stations**,
identical to the five cycles before the deploy:

| model | stations (of 148), 09-10 06Z → 09-11 12Z |
|---|---|
| climatology_fallback | 139 (flat) |
| nwp_rainfall_runoff | 134 (flat) |
| linear_regression_daily | 133 (flat) |
| **nwp_regression** | **73-74 (flat, before AND after)** |
| persistence_fallback | 35 (flat) |

⛔ **That is not the fill failing.** The four models that do not read past forcing are the
control, and they did not move either. The residual is a different gap:

```
nwp_regression.short_forcing_window
error='insufficient antecedent-precip history: got 44, need 45'
model=seasonal_precip_runoff_regression
```

⭐ **Measured on a station where the fill SUCCEEDED**, the 45-day window is missing exactly
two days: `2026-09-10` and `2026-08-18`. The first is the tail, now supplied in memory —
correctly absent from `historical_forcing`, which stores nothing. **The blocker is the single
interior day 2026-08-18.** Before the deploy the shortfall was 2 of 45; the fill closed one
and the interior hole holds the rest.

### The 08-18 hole is a SOURCE-side publication defect, not our ingest

Probed live on 2026-09-11: the per-day STAC item `20260818-ch` returns **HTTP 200 with ZERO
assets**, while `20260817-ch`, `20260819-ch` and `20260820-ch` each carry 5. Our adapter
degraded it to a WARNING-logged gap, which is correct behaviour. In the database the day is
absent for **all 5 products across all 147 stations**.

**It self-heals without us.** The monthly-"last" family publishes a whole-month daily grid per
product, generated from MeteoSwiss's internal archive and independent of per-day publication.
Cadence measured: May → 06-30, June → 07-26, July → 08-26, so **August ≈ 2026-09-26**. The
nightly `ingest-weather-history` runs a rolling **60-day** window, so 08-18 is inside it on
that date and is written as definitive RhiresD + TabsD. Proven in production, not assumed:
per-day items for **2026-07-01..07-12 now carry zero assets**, yet we hold all 31 July days,
and `meteoswiss_tabsd` on 07-04/05/06 carries **two versions**, the second written 2026-08-30
— after July's monthly file published on 08-26.

🪤 The yearly ARCHIVE family stops at **2025**, so for any 2026 day the per-day item and the
monthly-last file are the ONLY two routes.

## Why this is worth a plan

The 08-18 instance resolves itself around 09-26, before it would age out of a 45-day window on
10-02. **The instance is not the problem. The shape is.**

1. **One absent day disables a model on every station whose window contains it** — here 75 of
   148, every cycle, for weeks. The cost scales with the longest lookback in the fleet.
2. **Nothing alerts.** A fleet-wide missing forcing day produced one WARNING line. It was
   found by a model-window audit three weeks later, and only because someone went looking.
3. **It leaves no stored trace.** The failing stations store no forecast row at all, so the
   gap lives in the failure log, not in `forecasts` — invisible to any query over outcomes.
4. 🪤 **The aggregate quality label cannot surface it.** Measured the same cycle: 514 of 514
   forecasts are `degraded`, **every one from `warm_up`**, and `forcing` fires **zero** flags.
   A label that is always on carries no information. (Cause: `model_states` is empty, so every
   model cold-starts. That is a separate, currently unowned defect — see Related.)
5. **A source-side defect became a multi-week outage** because the recovery route
   (monthly-last) publishes on a ~26-day lag and nothing bridges the interval.

## The decisions — all OPEN, none pre-judged

**D1 — should an interior hole ever be filled, and from what?** Plan 261 fills the tail only,
by owner decision, and `docs/touchpoint-maps.md` still forbids imputation everywhere else. The
same NWP material that fills the tail could fill an interior hole. 🪤 But it does not
generalise backwards: `weather_forecasts` holds **2 stations before 2026-08-28** (37 from
08-30, 148 from 09-02) — the NWP archive postdates the fleet, so interior filling is available
for recent holes only, and never for a training window. Note also that this argument is about
OPERATIONAL inputs; nothing here touches training or hindcast.

**D2 — should a known-recoverable gap be fetched on demand rather than waited out?** The
monthly-last file for a completed month can be fetched the moment it exists. A targeted
"re-fetch this month" operation would have closed 08-18 as soon as MeteoSwiss published,
instead of depending on the rolling window happening to still cover it. For a hole older than
60 days the rolling window does NOT cover it and there is currently **no operation that
backfills it at all**.

**D3 — what should alert, and at what threshold?** Candidates: a forcing day absent for more
than N stations; a model's served-station count dropping by more than X% cycle-over-cycle
(which would have caught this on 08-19 without knowing anything about MeteoSwiss); or the
adapter's own gap warnings escalating after N consecutive days. The second is attractive
because it is source-agnostic and needs no new data.

**D4 — should a station-level forcing shortfall be STORED rather than only logged?** Today a
model that fails on short forcing writes nothing, so "why did this station go dark" is
answerable only from container logs, which do not survive a container recreation. 🪤 That is
not hypothetical: this session's deploy destroyed the previous cycle's worker logs.

## Tasks

⛔ **None of these are ready.** They are sketched so the decisions above have something
concrete to attach to. Sequencing, scope and exit gates need an owner pass first.

- **T1 (needs D3)** — a detection signal for a forcing day that is absent fleet-wide, and/or a
  cycle-over-cycle drop in a model's served-station count.
- **T2 (needs D2)** — an operation that re-fetches a named month of reanalysis on demand,
  covering the case where the hole is older than the rolling 60-day window.
- **T3 (needs D4)** — persist a station-level reason when a model is skipped for short
  forcing, so the outage is queryable after the logs are gone.
- **T4 (needs D1)** — only if the owner decides interior filling is wanted at all.

## Related, explicitly NOT in this plan's scope

- **The saturated `warm_up` label.** `model_states` is empty (0 rows, 0 stations), so every
  model on every station reports a cold start and 100% of forecasts are `degraded`. Whether
  cold start should degrade a model that keeps no state is an open question, unowned as of
  2026-09-11, and predates this plan.
- **Plan 261 T3** (`forcing_recent_steps`) — blocked on a residual that does not exist yet:
  the first post-deploy cycle produced **zero** fill declines, and a filled series has no gaps
  to discriminate on.
- Any change to the tail-only rule, the no-imputation contract, or the historical assemblers.
