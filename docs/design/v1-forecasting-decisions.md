# v1 forecasting — cross-cutting decisions (decide these before hardening the cluster)

**Status:** D1/D3/D4/D5 **CONFIRMED** (owner, 2026-07-23); **D2 open** (gateway-dev — completeness manifest).
Created 2026-07-23. The owner-owned forks are decided; the redesign (`docs/design/forecast-cycle-redesign.md`)
builds on them.
**Why this doc exists:** five consecutive `/plan` runs on the v1-forecasting cluster (142 ×2, 144, 145, 126)
stalled — not because the plans are wrong, but because they are a **tightly-coupled cluster gated on a handful
of cross-cutting decisions that aren't locked yet**, two of which need the gateway developer. Grinding `/plan`
per-plan keeps re-surfacing the same unmade decisions. This note pins them once; then 126/144 harden cleanly.

**The cluster:** 126 (ensemble membership / cycle resolution) · 144 (multi-track probabilistic forecasting) ·
145 (future snow) · 146 (past snow) · 143 (onboarding). 126 + 144 are the two blocked on the decisions below.

---

## D1 — Ensemble completeness contract *(owner: you)*
**Question:** when we fetch an IFS ensemble for a cycle, do we require **exactly** the full 51 members
(fc + pf 1..50), or accept a **partial** set above some floor?

**Why it matters:** 126's core contradiction was requiring "exactly 0–50, no partial ever" *and* "accept partial
above `min_operational_ensemble_size`." Those can't both hold. And `min_operational_ensemble_size` (config
default 20, currently **no consumer**) is an **output eligibility** threshold ("publish a forecast if ≥N members
survive QC"), not a fetch-time input-completeness definition — using it for both conflates the two.

**Options:**
- **(A) Always-exact-51 input; walk back to the latest COMPLETE cycle.** `min_operational_ensemble_size` stays
  purely an output gate. Cleanest contract; the ensemble is only meaningful with full spread. Cost: needs a way
  to *verify* 51 members cheaply (→ D2).
- **(B) Accept a partial set above an explicit input floor.** More tolerant of gateway raggedness, but requires
  defining partial-ensemble provenance + semantics, and the consumer coverage gate (`assess_future_coverage`)
  currently only checks "each feature has a non-empty *identical* member set" — a uniform 30-member set passes.

**Recommendation: (A)** exact-51 input, walk back to the latest complete cycle; keep
`min_operational_ensemble_size` as output-eligibility only. Revisit (B) only if D2/D3 show complete cycles are
too rare. **✅ CONFIRMED (owner, 2026-07-23): (A).**

## D2 — How to verify completeness cheaply *(owner: gateway dev)*
**Question:** how do we prove all 51 members exist for a candidate cycle without O(51) fetches?

**Why it matters:** a cheap probe (fc + one representative pf) **cannot** detect a missing middle member (e.g.
27) or ragged variables. Under D1(A), each walk-back candidate would otherwise need full O(51) validation.

**Options:** **(A)** a gateway **manifest / metadata endpoint** that lists which members (and horizons) exist for
a cycle — cheap, authoritative. **(B)** accept bounded O(51) validation per candidate (acceptable only if
walk-back is rare). **Recommendation: ask the gateway for (A)** (folded into the consolidated ask below);
fallback (B).

## D3 — `pf` availability at 06/12/18Z + horizon per cycle *(owner: gateway dev)*
**Question:** which cycles actually produce perturbed members, and to what horizon?

**Why it matters:** the live probe only confirmed **`pf` at 00Z** (06/12/18Z returned "no dataset" for the probed
date); 00/12Z reach ~15 d, 06/18Z may be shorter. This governs the **4×/day sub-daily cadence** (144 D2) and how
often walk-back fires (126). **✅ ANSWERED (owner, 2026-07-23): `pf` is 00Z-only today; all cycles in the
future.** Consequence baked into 126/144: the sub-daily **ensemble** track refreshes **once/day (00Z)** for now
(walk-back always lands on 00Z; 06/12/18Z have no complete ensemble → either a stale-00Z reuse or control-only
degrade, per D1/D4), becoming 4×/day when the gateway adds `pf` to all cycles.

## D4 — Missing/incomplete cycle: retry vs walk-back-only *(owner: you)*
**Question:** if the freshest cycle is incomplete/absent, do we **retry** (wait for it) or **walk back** to the
latest complete cycle?

**Why it matters:** 126 recommended a bounded in-adapter retry, but that needs a retry duration/interval, an
injected clock, cancellation handling, and deployment config — and literal waits make flow timing + tests
nondeterministic. **Recommendation: walk-back-only** — deterministic, no clock/config/cancellation surface,
directly testable. Add retry later only if walk-back proves operationally insufficient. **✅ CONFIRMED (owner,
2026-07-23): walk-back-only.**

## D5 — Narrow 126's scope *(owner: you)*
**Question:** 126 ballooned into a 6-phase build bundling unrelated fixes. Scope it to *just* requirement-aware
cycle resolution?

**Why it matters:** the `/plan` reviewers flagged 126 as front-loading a snow-member broadcast, a mixed-column
`ForcingColumnMode.BOTH` assembly mode, a group-discovery hoist, and a per-assignment `prior_state` fix — most
of which belong elsewhere (snow → 145/146; group-hoist + mixed-column + state-fix are forecast-cycle concerns).

**Recommendation: narrow 126 to** (a) a **typed fetch-requirements object** (per station/binding: required IFS
features + horizon/time-step + assembly mode) threaded through the adapter — replacing the insufficient
`FetchMode` enum — and (b) **requirement-aware cycle resolution**: walk back to the latest cycle that satisfies
the completeness (D1) + horizon requirements, with **candidate-local accumulation** (fetch/validate each
candidate into a fresh accumulator, commit only on full pass — no partial contamination). Evict the rest to
their own plans (mixed-column assembly, group-hoist, and the `prior_state` per-assignment fix each become small
separate plans if still needed). **✅ CONFIRMED (owner, 2026-07-23): narrow it.**

---

## Resolution re-check (2026-07-23, live-probed for 12300)
Owner asked whether the bridge is really 6-hourly and why. **Confirmed 6h, but from ONE source only:**
- `ecmwf.operational(subdaily_resolution=3)` → **HTTP 500** (unsupported); the param accepts only 6/12/24.
- Native steps live: **ERA5-Land observed = 1h**; **raw IFS forecast = 3h for the first ~6 days**, then 6h to
  15 d; **IFS gap-fill = 6h**. The operational series is mixed (1h obs + 3h forecast + 6h fill in one call).
- So the 6h floor bites **only across the gap-fill seam**. Two consequences:
  1. **Our sub-daily 3-day forecast is already 3-hourly** — it lives in the raw forecast's 3h window; the 144
     client-side per-member stitch builds it from raw 3h `pf` **now**, no gateway change needed.
  2. The gateway ask (#1 below) is specifically for a **3-hourly gap-fill** (→ 3-hourly ensemble-operational),
     since the gap-fill is the sole 6h limiter — not a generic "make operational 3h."

## Consolidated gateway ask (send once the above are internally agreed)
One message to the gateway developer, bundling all gateway-owned items (D2 + D3 + the earlier
ensemble-operational request):
1. **A 3-hourly gap-fill → a 3-hourly ensemble "operational" export.** The raw IFS forecast is already 3-hourly
   (first ~6 d) and ERA5 is hourly; the only 6h limiter is the **IFS gap-fill** product. Ask for the gap-fill
   (and hence a per-member ensemble-operational: ERA5 → per-member gap-fill → per-member forecast, member-indexed)
   at **3-hourly**, same UTC / metres-Kelvin conventions. (Efficiency swap for 144's client-side stitch, which
   already delivers 3h.)
2. **A completeness manifest/metadata endpoint** (D2) — for a given cycle, which members (0..50) and horizons
   are available, cheaply, so we can pick the latest complete cycle without O(51) probes.
3. **Confirm `pf` cycle availability** (D3) — which of 00/06/12/18Z produce perturbed members, and the horizon
   each reaches.

---

## Once decided
- **126** re-grounds to the narrowed scope (D5) with D1/D4 locked → confirming `/plan`.
- **144** locks its completeness/cadence assumptions (D1/D3) → confirming `/plan`.
- The gateway ask goes out; the client-stitch (144 D3) proceeds regardless (the seam absorbs the timeline).
