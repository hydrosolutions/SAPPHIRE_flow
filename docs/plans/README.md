# SAPPHIRE Flow — Plan Index

Maintained by hand — update whenever a plan's status changes, a new plan is added,
or a plan is implemented (move it to [archive/](archive/)). Do not auto-generate.

**Context:** v0 is complete (the mac-mini runs NWP-on operational runoff
forecasting). We are marching to **v1 = Nepal DHM deployment** (ECMWF IFS via the
recap Data Gateway, DHM gauges, ERA5-Land, multi-tenant east/west). Category tags:
**A** = v0 operational hardening / reliability (land before any v1 prod deploy) ·
**B** = v1 Nepal feature · **C** = dev-experience / dashboard / deferrable.

## Recently merged (v1 operational hardening — implemented via WF2, independently reviewed)

- **101** — water_level QC datum fix — **MERGED (#66), ARCHIVED** — per-station datum,
  subtract-before-QC across all four QC call sites; the mechanism DHM's mixed
  cm/m/m-a.s.l. units need. 4 design gates + implementation review (regression locks
  verified).
- **100** — Forecast-feed resilience — **MERGED (#65 base + #67 floor-gate fix),
  ARCHIVED** — persist NWP-on across restarts + always-on climatology floor + fatal
  NWP-off gate + new-onboarding floor gate (6a, the incident-class fix) +
  staleness/health. Implemented via WF2, independently reviewed (the review caught
  the 6a gap in #65; #67 closed it, re-verified).
- **105** — Operational disk hygiene & NWP scratch cleanup — **MERGED (#68),
  ARCHIVED** — scratch self-clean on failure + pre-fetch disk tripwire + weekly
  image prune. First Wave-0 lead; conventional build + adversarial review (round-2
  caught 3 blockers a green suite missed).
- **038** — Store write atomicity — **MERGED (#71), ARCHIVED** — injectable-
  transaction DI replaces AUTOCOMMIT two-phase inserts; resilient reads + orphan
  cleanup. Wave-0.
- **040** — Hindcast deduplication constraint — **MERGED (#75), ARCHIVED** — 6-col
  UNIQUE + ON CONFLICT DO UPDATE full-replace upsert (idempotent hindcast writes)
  + migration 0029 dedup. Wave-0; 2 adversarial Codex rounds converged. **All 3
  Wave-0 correctness bugs (105 + 038 + 040) now merged.**

## Active — operational hardening (A) — the gate to any v1 prod deploy

- **103** — Prefect worker observability & home — `DRAFT` — persist flow-run logs to
  the Prefect store + writable `PREFECT_HOME`. **Supersedes 062.**
- **097** — Short-lookback observability — `READY` (WF1 plan-review + independent
  Codex review both converged clean, 2026-07-13) — warn when the delivered lookback
  is shorter than requested. **Next = WF2 (hold-at-PR).**
- **048** — restic encrypted backup + monthly restore rehearsal — `DRAFT (stub)` —
  **HARD prod prerequisite.** Depends on 046.
- **046** — Mac Mini staging deployment + edge-case suite — `IN_PROGRESS`.
- **058** — BAFU LINDAS archive via operational collection — `DRAFT` — depends on 046.
- **091** — Mac-mini NWP-on data-collection runbook — `DRAFT` — depends on 046.
- **094** — Cap onboarding/hindcast window to actual data range — `DRAFT`.
- **083** — Human-readable `station_code` in structured logs — `DRAFT`.
- **075** — Mac Mini Stream C: glue + one-command bootstrap — `READY`.
- **084** — Dev-machine deployment validation (2-station runoff-only) — `READY`
  (validated 2026-06-28; reusable harness not fully built).
- **064** — Supply-chain hardening — `READY` (largely shipped; residuals remain).
- **069** — Pyright backlog cleanup: ratchet + drain — `READY` (P1 shipped; drain
  remaining).
- **062** — Prefect state persistence (`PREFECT_HOME` ↔ volume) — `DRAFT` — **likely
  subsumed by 103**; reconcile.

## Active — v1 Nepal feature (B)

- **106** — v1 (Nepal DHM) critical-path roadmap — `READY` (locked 2026-07-08) — **the
  sequencing plan. Read this first for v1 planning.** Locks the wave order (0 stabilize →
  1 forcing → 2 obs/rating → 3 auth/deploy → 4 DHM go-live → 5 v1.x), classifies every
  remaining piece designable-now vs blocked-on-external-knowledge, and lists the
  collaborator questions (DHM/HSOL/gateway dev). v1.0 is **headless** (Flow 3/dashboard/
  bulletin/Bikram Sambat → v1.x). Reviewed via 2× WF1 plan-review + 2× Codex independent
  review (all fixes applied); the gateway-dispatch fix + multi-year backfill window are
  owned in Plan 082 Tasks 2C/3B.
- **080** — FI wheel distribution — `DRAFT` (low-pri) — publish `forecastinterface`
  as a versioned wheel, migrate off the git-pin, drop the temporary CI wheel-guard
  (Plan 079). **Blocked externally** on FI hitting the private index. Packaging
  prerequisite for a Nepal handover.
- **081** — recap-dg-client forcing adapter — `DRAFT` — the Nepal forcing foundation
  (IFS/ERA5-Land time-series from the gateway). **Offline-completable** against fakes.
- **082** — recap Gateway operational + training readiness — `DRAFT` — live gateway
  smoke, Nepal config, coverage gate, watchdog, runbooks. Depends on 081.
- **047** — Nepal v1 data sources umbrella (IFS, DHM, ERA5-Land) — `DRAFT (stub)` —
  depends on 081/082.
- **117** — Basin/static artifact architecture alignment — `DRAFT` — document the
  adjacent basin/static extraction artifact boundary, GeoPackage requirements,
  gauge-ID feature naming preference, and static-Parquet TBD path. Blocks cleanup
  of the Flow 0 / Flow 5.2 Nepal onboarding architecture before Plan 047 advances.
- **035** — Rating-curve provenance for skill integrity — `READY` — v1 DHM hQ.
- **017** — Manual vs automatic station support — `DRAFT` — v1, DHM mixed networks.
- **015** — Virtual / calculated station support — `DRAFT` — v1 (enum slice shipped).

## Active — dev experience / dashboard (C)

- **102** — Dashboard multi-parameter observation visibility — `READY`.
- **104** — Dashboard hardening (links, chart defaults, skill-chart) — `READY`.
- **099** — Dashboard display timezone — **P1 shipped** (UTC axis labels, #59); **P2
  pending** (UTC↔Europe/Zurich toggle).
- **090** — NWP incomplete-cycle selection + horizon-coverage — **P1 shipped**
  (age-delay guard, #49); **P2 pending** (terminal-valid-time refetch).
- **113** — Align forecast schedule with NWP cycle delivery — `DRAFT` (low-pri) —
  the forecast cron sits on the NWP cycle boundaries → every run uses a 6h-stale
  `fallback` cycle and the **00:00 slot silently drops to obs-only** (1 clean daily
  bucket short). Chosen direction = offset the schedule (opt B); documented, not urgent.
  Diagnosed 2026-07-13.
- **049** — Cloudflare public URL + Entra SSO for staging — `DRAFT` — depends on 046.
- **108** — Swiss market standards posture — `DRAFT` (low-priority v1+) —
  nFADP/DSG, OGC, INTERLIS, and SVGW W12 decision gates for future Swiss partner
  readiness. Docs-first; no change to the v1.0 Nepal critical path.
- **111** — Benchmarking against BAFU's operational forecasts — collector **MERGED
  (#72)**; scorer/publication **BLOCKED on external gate G1** (low-priority). Route-C
  hourly collector archives hydrodaten Plotly-JSON forecasts (54 stations, quantiles
  not members, ~5-day horizon) to a quarantined parquet store; evaluation-only,
  forward-only. Dev collection validated 2026-07-10. G3 scorer + any published
  comparison stay gated on the (unsent) BAFU licence request.
- **111b** — Mac-mini deployment runbook for the collector — `READY (runbook)` —
  deploy wiring in PR #73; hourly schedule + quarantined volume + overlay switch.
  See [111b-bafu-collector-macmini-deployment.md](111b-bafu-collector-macmini-deployment.md).
- **071** — v0b weather-history: MeteoSwiss daily reanalysis adapter — `DRAFT`.
- **072** — v0b weather-history: hybrid forcing resolver — `DRAFT`.
- **066** — Configurable retrain data-window — `DRAFT`.
- **068** — `onboard-stations` parallelization + async backfill — `DRAFT` — depends
  on 038 + 040.
- **057** — API route-module tests — `DRAFT (stub)`.

## Deferred

- **039** — Sensor/Model failure visibility — `DEFERRED` → Flow 4 (pipeline
  monitoring).
- **042** — API Key Auth + Client SDK — `DEFERRED` → post-v0 (but see the multi-tenant
  gap below — a Nepal handover needs auth/RBAC).

## v1 gaps — work with NO plan yet (draft before the waves that need them)

> **All now sequenced + classified in Plan 106** (wave, owner, designable-now vs blocked).
> This list is the raw inventory; Plan 106 is the ordered plan. Gap #4 (ERA5-Land) is
> subsumed by 081/082; the training-forcing backfill window is owned in 082 Task 3B.

These are named in `architecture-context.md` / `v0-scope.md` but have no dedicated plan:

1. **Multi-tenant / deployment isolation** (east HSOL / west DHM) — blocks the
   multi-tenant wave.
2. **DHM observation adapter** — real-time DHM gauge ingest (distinct from the
   gateway *forcing* adapter 081).
3. **water_level unit normalization** — cm / m-above-ground → canonical metres at the
   adapter boundary (Plan 101 only *guards* the metres assumption).
4. **ERA5-Land reanalysis adapter** (`WeatherReanalysisSource` for Nepal) — folded
   verbally into 081/047, no dedicated build plan.
5. **Flow 0 Nepal deployment onboarding** — AoI definition and full onboarding
   flow still need a dedicated plan; the basin/static artifact boundary is now
   tracked in Plan 117.
6. **Rating-curve h→Q ingestion + reprocessing** (Flow 12 Branch A) — 035 covers
   provenance only.
7. **Auth / RBAC / audit** for the multi-tenant handover (Plan 042 deferral is
   insufficient).
8. **Flow 4 pipeline monitoring** full build (v0 is basic-only; 039 folds in).
9. **Bikram Sambat calendar + bulletin generation** (Nepal official reporting).

> **NOT needed for v1: elevation-band / gridded NWP extraction.** Nepal forcing
> arrives as **basin/band time-series directly from the Data Gateway API** — SAP3
> does not extract from grids for Nepal. (The ICON-mesh extraction, Plan 087, is
> Swiss/v0-only.)

## Archived

See [archive/](archive/) for completed and archived plans (73 entries).
