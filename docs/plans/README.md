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
- **100** — Forecast-feed resilience — **base MERGED (#65); 6a floor-gate fix in PR
  #67 (pending merge)** — persist NWP-on across restarts + always-on climatology
  floor + fatal NWP-off gate + staleness/health. NOTE: #65 merged INCOMPLETE (the 6a
  new-onboarding floor gate was missing); **#67 closes that incident-class gap — merge
  it, then archive 100.** Archive pending #67.

## Active — operational hardening (A) — the gate to any v1 prod deploy

- **103** — Prefect worker observability & home — `DRAFT` — persist flow-run logs to
  the Prefect store + writable `PREFECT_HOME`. **Supersedes 062.**
- **105** — Operational disk hygiene & NWP scratch cleanup — `DRAFT` (grill-me done)
  — scratch self-clean on failure + pre-fetch disk tripwire + weekly image prune.
- **097** — Short-lookback observability — `DRAFT` — warn when the delivered lookback
  is shorter than requested.
- **048** — restic encrypted backup + monthly restore rehearsal — `DRAFT (stub)` —
  **HARD prod prerequisite.** Depends on 046.
- **046** — Mac Mini staging deployment + edge-case suite — `IN_PROGRESS`.
- **058** — BAFU LINDAS archive via operational collection — `DRAFT` — depends on 046.
- **091** — Mac-mini NWP-on data-collection runbook — `DRAFT` — depends on 046.
- **094** — Cap onboarding/hindcast window to actual data range — `DRAFT`.
- **038** — Store write atomicity (transactional two-phase insert) — `DRAFT`.
- **040** — Hindcast deduplication unique constraint — `DRAFT`.
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
- **049** — Cloudflare public URL + Entra SSO for staging — `DRAFT` — depends on 046.
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

These are named in `architecture-context.md` / `v0-scope.md` but have no plan:

1. **Multi-tenant / deployment isolation** (east HSOL / west DHM) — blocks the
   multi-tenant wave.
2. **DHM observation adapter** — real-time DHM gauge ingest (distinct from the
   gateway *forcing* adapter 081).
3. **water_level unit normalization** — cm / m-above-ground → canonical metres at the
   adapter boundary (Plan 101 only *guards* the metres assumption).
4. **ERA5-Land reanalysis adapter** (`WeatherReanalysisSource` for Nepal) — folded
   verbally into 081/047, no dedicated build plan.
5. **Flow 0 Nepal deployment onboarding** — AoI definition, bulk static-attribute
   fetch (HydroATLAS/MERIT/DHM GIS).
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

See [archive/](archive/) for completed and archived plans (69 entries).
