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

- **103** — Writable `PREFECT_HOME` under the read-only container — `READY, implemented (hold-at-PR)` — set
  `PREFECT_HOME=/tmp/prefect` on the 3 client services (worker, worker-ingest, init). **Supersedes 062 and
  141.** Trivial/env-only. The flow-run-**log-persistence** half was **split to
  Plan 142** (2026-07-23) — it needed a load-bearing deployment-entrypoint change.
- **142** — Persist Prefect flow-run logs — `DRAFT` — carved out of 103; module-path deployment entrypoints
  (dot, ⚠️ no colon) + guarded `flows/__init__.py` hook + `APILogHandler` on a `sapphire_flow`-scoped logger.
  Load-bearing; depends on 103; needs its own /plan → /implement.
- **141** — Prefect writable home under read-only container — `SUPERSEDED by 103` — a redundant re-draft of
  103's D1 (`PREFECT_HOME=/tmp/prefect`); folded into 103 (owner 2026-07-22).
- **097** — Short-lookback observability — `READY` (WF1 plan-review + independent
  Codex review both converged clean, 2026-07-13) — warn when the delivered lookback
  is shorter than requested. **Next = WF2 (hold-at-PR).**
- **048** — restic encrypted backup + monthly restore rehearsal — `DRAFT (stub)` —
  **HARD prod prerequisite.** Depends on 046.
- **046** — Mac Mini staging deployment + edge-case suite — `IN_PROGRESS`.
- **058** — BAFU LINDAS archive via operational collection — `SUPERSEDED by 136`.
- **136** — BAFU LINDAS observation archive collector (quarantined, all gauges) — `READY`.
- **091** — Mac-mini NWP-on data-collection runbook — `DRAFT` — depends on 046.
- **094** — Cap onboarding/hindcast window to actual data range — `DRAFT`.
- **083** — Human-readable `station_code` in structured logs — `DRAFT`.
- **075** — Mac Mini Stream C: glue + one-command bootstrap — `READY`.
- **084** — Dev-machine deployment validation (2-station runoff-only) — `READY`
  (validated 2026-06-28; reusable harness not fully built).
- **064** — Supply-chain hardening — `READY` (largely shipped; residuals remain).
- **069** — Pyright backlog cleanup: ratchet + drain — `READY` (P1 shipped; drain
  remaining).
- **062** — Prefect state persistence (`PREFECT_HOME` ↔ volume) — `SUPERSEDED by 103` (reconciled
  2026-07-22; also carried a stale SQLite-server premise — prefect-server is Postgres-backed).

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
- **082** — recap Gateway operational + training readiness — `READY` — Flow-1
  forecast dispatch, cycle fallback, source-aware watchdog, coverage manifest, §5a
  polygon store/resolver, secret plumbing, runbook. **Implemented + Codex-reviewed to
  convergence (3 rounds), open in PR #91** (hold-at-PR; CI blocked on the
  `RECAP_DG_CLIENT_TOKEN` secret). Depends on 081/115a. Flow-6 reanalysis wiring +
  the training-gate/snow wiring are **carved out to Plan 121**.
- **121** — Recap Gateway: Flow-6 reanalysis + deferred integration follow-ons —
  `DRAFT (stub)` — carved out of 082: the Flow-6 `_ReanalysisAdapter` Protocol fork
  (115b1 mismatch), coverage training-gate wiring, snow-forecast Flow-1 wiring, and
  the `RECAP_DG_CLIENT_TOKEN` CI-secret follow-up. Needs the `plan` workflow before READY.
- **143** — DHM/v1 basin + gauge onboarding — `DRAFT` — GeoPackage → **N gauges** → forecast-ready
  (geometry via Plan 120 + station/rating + gateway binding + subscriptions). Owner-aligned 2026-07-23; needs
  `/plan`. Blocks 144.
- **144** — Multi-track probabilistic forecasting over the IFS ensemble — `DRAFT` (re-grounded 2026-07-23 after a
  `/plan` reckoning). Per-station tracks **data-driven by assigned models** (a station's daily ≤15 d + sub-daily
  3-hourly ≤3 d @00/06/12/18Z); per-member forcing via an `EnsembleForcingProvider` **seam** (client-stitch now /
  3-hourly gateway ensemble-operational later). **COMPOSES existing infra** — reuses `ensemble_fanout`
  (stateless-only) + `ForecastEnsemble` + `forecast_qc`; depends on **134** (control 6h bridge), **126** (ensemble
  membership + horizon walk-back), **139** (daily model), + a new **snow-forcing** plan. Needs a confirming `/plan`.
- **145** — Future-snow forecast forcing wiring — `DRAFT` — carved from 139 W7, then **SPLIT** (2026-07-23). The
  FUTURE channel: `fetch_snow_forecast` (zero callers → broadcast no-op) scoped + wired into the cycle → store →
  broadcast WITH snow-scoped degradation, + the aggregation fix (`swe`/`snow_depth` MEAN, `snowmelt` **SUM**). No
  blocker; unblocks 144. Needs a confirming `/plan`.
- **146** — Antecedent (past) snow reanalysis channel — `DRAFT` — the SPLIT-off load-bearing half: a supported
  `ForcingSource` for `recap_snow_reanalysis` + a **dedicated recap-reanalysis ingest flow/schedule** (the
  blocker — no production caller today) + read-side hybrid snow tier so stored snow reaches `past_dynamic` in
  training/hindcast/live. Depends on 082 + 145. Blocks 139/144 snow-lookback. Needs `/plan`.
- **124** — Station active-assignment consistency — `DRAFT` — **scope-locked, ready to implement
  directly (owner 2026-07-18).** NARROW: INACTIVE station assignments stop forecasting + leave the
  alert-priority index (match the group path); the fallback-priority-drift health check stays
  **all-status** (Plan 100 untouched). Fix = a separate active-filtered view for forecasting/alerts,
  raw dict kept for drift. (`plan` workflow escalated 3× by over-scoping a tiny fix — implementing
  directly with a red-first test instead.) Store stays all-status (real callers); no group-side bug.
- **125** — Inactive assignments fully inert — `DRAFT (stub)` — follow-up to 124: also make INACTIVE
  invisible to the fallback-priority-drift detector, which **requires an owner-ratified supersession
  of Plan 100 C1c**. Coherence/cleanup; not deployment-critical. Depends on 124.
- **127** — fc-first minimal unblock — **MERGED (#97 → `d317af0`, 2026-07-19)** — the
  deployment-critical forcing path is COMPLETE (082 + 124 + 127). Tolerant `pf` fetch + `SINGLE`-model
  bare columns keyed on `ensemble_mode` + a mixed-model fail-fast guard. Critical Codex review caught
  a ratchet-masked type bug + a mixed-model regression (both fixed, round-2 APPROVE). Sandro's live
  control-only models now forecast end-to-end.
- **123** — Model-driven forcing membership (CONTROL_ONLY + NONE) — `DRAFT (DEFERRED)` — the full
  flow-level membership design (skip `pf` entirely for control-only + real `NONE` skip +
  staleness/provenance). Genuinely multi-part; **ESCALATED 2×**. **No longer the blocker** (127
  unblocks the deployment); this is the efficiency/completeness follow-up, revisit after 127.
- **126** — Requirement-aware ensemble cycle resolution — `DRAFT` (re-grounded + **narrowed** 2026-07-23). A
  typed fetch-requirements object (features/horizon/mode, replacing `FetchMode`) + walk-back to the latest
  **complete** cycle (D1 exact-51; D3 `pf` 00Z-only → once/day; D4 walk-back-only) + candidate-local accumulation
  (no walk-back contamination). Evicts mixed-column assembly / group-hoist / snow / state-fix. Decisions locked in
  `docs/design/v1-forecasting-decisions.md`. Depends on 123; consumed by 144. Needs a confirming `/plan`.
- **047** — Nepal v1 data sources umbrella (IFS, DHM, ERA5-Land) — `DRAFT (stub)` —
  depends on 081/082.
- **117** — Basin/static artifact architecture alignment — `READY` — documents the
  **adjacent** basin/static extraction artifact boundary: SAP3 consumes a validated
  package and does not integrate the extractor's code. Covers the GeoPackage
  terminology + naming rules (`g_<station_code>`), single-kind Gateway HRUs, and the
  confirmed static-Parquet shape. Unblocks the
  **basin/static architecture cleanup only** — 047 separately needs its
  **re-scope per Plan 106** before it advances.
- **120** — Basin/static importer + §5a persistence + versioned basin state —
  `READY` (**implemented, all 4 slices — Phase 0/1/2/3 merged/landed**) — the importer the `04`
  contract §5a calls for. Phase 0 (provenance/versioning schema), Phase 1 (package loader +
  validation), and Phase 2 (dissolve/persist/correct into `basins`/`basin_versions`/§5a) are
  merged to `main` (#124/#126/#128). Phase 3 (the `import_basin_package_from_directory`
  orchestration entrypoint + `python -m sapphire_flow.cli.import_basin_package` CLI + the
  operator-facing acceptance report + the importer runbook) is THIS slice. The extractor package
  landed and was tested (HRU 12300). No longer gated on the extractor — the remaining gate is
  operational only: run the importer against an accepted package before 082's resolver starts
  returning non-`None` in production (see Plan 120 "Production-gate note").
- **035** — Rating-curve provenance for skill integrity — `READY` — v1 DHM hQ.
- **017** — Manual vs automatic station support — `DRAFT` — v1, DHM mixed networks.
- **015** — Calculated station support (component-derived) — **MERGED (#109 storage+trigger,
  #112 Flow 2 step-2.5 derivation, #113 TOML onboarding), 2026-07-21.** Move to archive/ once
  confirmed. Ungauged half split to 016.
- **016** — Ungauged station support — `DRAFT` — split out of 015. **Reframed 2026-07-21:**
  not fully blocked — a **SAP3 scaffolding slice is buildable now** (Step-8 gate refactor,
  zero-row past_targets plumbing, gauging_status branching, donor-CV skill framework). *Live*
  ungauged forecasting still needs an FI operational model (modelling team; mountain
  snow+glacier+bands — paradigm under discussion) + basin geometry (117/120). The floor is
  deferrable + downstream of the model choice; basin user-upload+security is optional.

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
