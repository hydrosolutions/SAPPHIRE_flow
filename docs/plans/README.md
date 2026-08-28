# SAPPHIRE Flow — Plan Index

Maintained by hand — update whenever a plan's status changes, a new plan is added,
or a plan is implemented (move it to [archive/](archive/)). Do not auto-generate.

## Status convention (added 2026-08-28 after a stale-status audit)

**A plan's status lives in ONE place: a `status:` key in the YAML frontmatter.** Values:
`DRAFT` · `READY` · `PARTIAL` · `COMPLETE` · `DEFERRED` · `SUPERSEDED`. When a plan reaches
COMPLETE, `git mv` it into [archive/](archive/) **and** grep for references first — a plan
path is cited from other plans and, in at least one case, from a workflow comment; archiving
Plan 174 once broke a test that read the doc from disk.

**Why this is written down.** An audit on 2026-08-28 scanned for stale statuses and found the
scan itself could not work: statuses were recorded in three different ways — frontmatter
`status:`, a legacy `**Status**:` line (27 plans), and 11 plans with no status marker at all.
A scan keyed on frontmatter silently skips the other 38, which is how Plan 064 sat reading
`READY` while ~90% shipped. **Do not add a second status marker to a plan that already has
one**, and do not rely on a status scan that only checks one format.

**Context:** v0 is complete (the mac-mini runs NWP-on operational runoff
forecasting). We are marching to **v1 = Nepal DHM deployment** (ECMWF IFS via the
recap Data Gateway, DHM gauges, ERA5-Land, multi-tenant east/west). Category tags:
**A** = v0 operational hardening / reliability (land before any v1 prod deploy) ·
**B** = v1 Nepal feature · **C** = dev-experience / dashboard / deferrable.


## Archived by the 2026-08-28 stale-status audit

Each had `status: READY` while its work was already present on `main`. Archived on **code-artifact
evidence** (plan-named tests and source in the tree), **not** on a task-by-task re-verification of
exit criteria — Plan 212 owns that deeper screening.

- **090** — NWP incomplete-cycle selection — header already said DONE (P1, PR #49, `ab54d24e` on main); P2 optional, must be re-scoped.
- **140** — ICON-CH2-EPS STAC pagination fix — body already said "COMPLETE — shipped as `3264a45`"; confirmed on main.
- **082** — recap Gateway operational readiness (19 plan-named test files, 10 source files).
- **117** — basin/static artifact architecture.
- **129** — continuous precipitation knit (RhiresD → RprelimD → NWP).
- **130** — temperature reanalysis live-tail.
- **145** — future-snow (JSNOW) forcing wiring — present in the recap adapter, the reanalysis ingest and the forecast cycle.
- **161** — DATABASE_URL credential parsing.

**Held back deliberately, with reasons:**
- **138** (BAFU precip+temp+runoff regression) — its own body says "**T1 is PARTIAL, not done**". Archiving it would hide outstanding work.
- **035** (rating-curve provenance) — contradictory: the header says implementation begins at v1, yet `tests/unit/services/test_rating_conversion.py` and a `0035` migration downgrade test already exist. Needs a decision, not a status flip.
- **162** (robust database backup) — the work looks shipped, but `tests/unit/ops/test_restore_rehearsal.py:10` cites its path in a docstring. Moving it dangles that reference, and editing a test file is a code change belonging in a PR.
- **201**, **206** — COMPLETE (merged #220/#222) but not moved: `.github/workflows/integration-nightly.yml:144` cites Plan 201's path.

## Recently merged (v1 operational hardening — implemented via WF2, independently reviewed)

- **184 / 193 / 205 / 209** — **DHM precipitation research arc (M-A6 → M-A9) — MERGED (#211, #212,
  #213, #215, #218), ARCHIVED.** Gauge vs ERA5-Land, temporal characterisation, elevation and regime
  structure, and the Phase-2 recommendation. Each plan independently reviewed **before** implementation
  (7, 7 and 12 findings folded on 193/205/209 respectively); 184 was implemented first and paid twelve
  retrospective amendments, which is what established the review-first order. **Outcome:** the sample
  supports characterisation well and correction poorly — a precipitation–elevation lapse rate is not
  obtainable from it (needs OD-10), forcing correction transfers only below ~2,000 m, and every route
  to the snow-dominated high basins is closed by the data. Recommendation at
  `docs/design/dhm-precipitation-phase2-recommendation.md`. **Remaining on the track:** M-I1 (QC rules),
  M-I3 (WMO catch-efficiency inventory), M-A5b (IMERG, rewritten around Early).

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

- **163** — Watchdog dead-man's switch + HTTP hardening — `READY, implemented
  (hold-at-PR)` — the mac-mini watchdog went silent ~03:54 2026-08-16 with no
  alert (the exact silence-looks-like-health shape of the 29-July 14-day outage).
  Adds an off-box dead-man's-switch heartbeat (Healthchecks.io) POSTed after every
  tick that COMPLETES AND PERSISTS its state — an unhealthy stack still pings
  (Slack is the *detected-failure* channel, the dead-man is the
  *watchdog-died-before-it-could-report* channel), but a tick that raises before
  persistence correctly emits no heartbeat, never placed in a `finally` (which
  would falsely mark a crashed tick healthy). Also hardens all four outbound HTTP
  call sites (health probe, BAFU-detail probe, Slack POST, dead-man POST) against
  `httpx.InvalidURL` (verified NOT a subclass of `httpx.HTTPError`), `OSError`
  and `UnicodeError` — a malformed hand-pasted URL could otherwise kill a tick at
  exactly the moment it tries to report an outage. Routes all four Slack call
  sites through a safety helper so an unexpected delivery exception can no longer
  lose Plan 162 Phase A's `backup_notification_pending` transition.
- **160** — BAFU forecast adapter schema-drift resilience — `READY, implemented
  (hold-at-PR)` — fixes the live BAFU forecast collector outage (dead since
  2026-08-12 ~16:00 UTC, caught by the Plan 158 Slack alert): BAFU added the icon
  value `river_missing`, which `BafuIcon`'s flat `Literal["river","lake","missing"]`
  rejected, and whole-batch validation aborted the inventory for all 54 stations on
  that ONE bad value. The icon is now modelled compositionally (water-body `kind` ×
  `BafuGaugeDataStatus`, D1), so `lake_missing` is supported before it has ever been
  seen (D6, forward-looking lock). Routing follows KIND only — `_missing` does NOT
  suppress the fetch (D2/D8: probed directly, BAFU still publishes a full forecast
  for a station whose live gauge is down). `fetch_station_inventory` now validates
  per FEATURE (D3, the class fix): a bad feature is skipped and recorded, the rest
  of the batch is still returned; an unrecognised icon SKIPS its station rather than
  falling through to a river-shaped default fetch (D4, fail-safe not fail-open). A
  skipped station is a WARNING + a queryable `pipeline_health` WARNING status (D5).
  Auditing the other Literal-typed external vocabularies (`BafuMetric`,
  `BafuForecastVariant`, `LindasKind`, `BafuObservationParameter`) is a named
  follow-on (D9), not in scope here.
- **154** — Recap IFS fetch containment — `READY, implemented (hold-at-PR)` — a
  station-scoped `RecapDataUnavailableError` (one HRU's control fetch missing) no longer
  discards every other HRU's already-accumulated rows or escalates into the flow's
  cycle-wide runoff-only degradation. Per-HRU exception containment with an
  all-or-nothing HRU commit (an HRU is the Gateway call unit — station-level
  partiality is unrepresentable at this boundary until Plan 151's per-track path).
  Per-HRU divergence is treated as an ANOMALY (owner-confirmed, publication is
  global): healthy HRUs are still served, and `_fetch_nwp_task` reconciles
  requested-vs-returned stations, alarming a CRITICAL `pipeline_health` record +
  DEGRADED cycle health rather than silently darkening the whole deployment. No
  adapter return-type/Protocol change. Independent of the forecast-cycle redesign;
  Plan 151 D7 needs this same containment for its own per-track path (154 first
  shrinks 151's T4). **Fixer round (2026-08-12, post-implementation review):** folded
  in a mixed-empty/populated-control-variable `AdapterError` guard (D2's "complete
  variable set" invariant covers this shape too, not just the raising case), a
  `run_forecast_cycle_flow` end-to-end test proving the divergence wiring (was
  previously only unit-tested in isolation), and an accurate total-loss re-raise
  message that preserves the original Gateway diagnostic as `__cause__`. See the
  plan doc's "Fixer round" section.
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
- **058** — BAFU LINDAS archive via operational collection — `SUPERSEDED by 136` (archived).
- **136 / 175 / 176 / 186 / 189** — **BAFU LINDAS observation archiving — COMPLETE, ARCHIVED
  (2026-08-21).** The whole family is merged and running on the mac-mini in image `0.1.775`:
  **136** the quarantined all-gauge archive collector (#121, `ea33394`), **175** LINDAS rate-limit
  resilience (#172, `9c96792`), **176** the 10-minute grid (#181, `afbf7e2`), **186** whole-graph
  operational ingest resolving 175's deferred D5 (#188, `4ae7cf8`), **189** audit edge + poll bound
  (#193, `d1f0837`). Live evidence 2026-08-21: **233 gauges × 495 rows per 10-minute slot, 700
  snapshots / 81 MB**, and the on-demand T8 audit measures completeness at **95.8 %** (322/336)
  post-176 against **16.3 %** (94/576) on the old hourly cadence. Remaining gaps are BAFU publish
  stalls upstream, not collector faults. The archive stays quarantined — no gauge is onboarded, and
  nothing here reaches the `observations` table. See
  [archive/136-bafu-lindas-observation-archive-collector.md](archive/136-bafu-lindas-observation-archive-collector.md).
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
- **192** — Gateway forcing for 12300 on the mac-mini — `DRAFT` — the weather-forecast adapter selector is
  deployment-wide (one adapter per stack), so 12300 cannot co-host with the Swiss/BAFU stations. **Two
  independent Codex rounds folded.** Round 2 forced a restructure: **Stage A** is a throwaway proof
  (disposable DB, seeded records, one manual 00Z fetch — no schedule, no code change); **Stage B**, the
  standing daily stack, is built only if the owner wants a daily feed. Corrected a real identity error
  (station code is `123`, `12300` is the Gateway HRU, polygon `g_123` — the loader derives
  `g_<station_code>`). Live re-probe: same-day 00Z IFS (+14.75 d), ~4-day retention, and the mini's own
  probe had produced **0 `ok=True` in 31 days**. Needs `/plan`.
- **143** — DHM/v1 basin + gauge onboarding — `DRAFT` — GeoPackage → **N gauges** → forecast-ready
  (geometry via Plan 120 + station/rating + gateway binding + subscriptions). Owner-aligned 2026-07-23; needs
  `/plan`. Blocks 144.
- **144** — Multi-track probabilistic forecasting — `SUPERSEDED by docs/design/forecast-cycle-redesign.md`
  (2026-07-23). The multi-track/ensemble orchestration is folded into the forecast-cycle redesign (its D1–D6
  decisions carry over). Six /plan stalls proved it needs a forecast-cycle re-architecture, not incremental patches.
- **145** — Future-snow forecast forcing wiring — `DRAFT` — carved from 139 W7, then **SPLIT** (2026-07-23). The
  FUTURE channel: `fetch_snow_forecast` (zero callers → broadcast no-op) scoped + wired into the cycle → store →
  broadcast WITH snow-scoped degradation, + the aggregation fix (`swe`/`snow_depth` MEAN, `snowmelt` **SUM**). No
  blocker; unblocks 144. Needs a confirming `/plan`.
- **146** — Antecedent (past) snow reanalysis channel — `DRAFT` — the SPLIT-off load-bearing half: a supported
  `ForcingSource` for `recap_snow_reanalysis` + a **dedicated recap-reanalysis ingest flow/schedule** (the
  blocker — no production caller today) + read-side hybrid snow tier so stored snow reaches `past_dynamic` in
  training/hindcast/live. Depends on 082 + 145. Blocks 139/144 snow-lookback. Needs `/plan`.
- **148** — Forecast-cycle redesign **Phase 1**: `ModelRunContext` + per-assignment `prior_state` —
  `COMPLETE (archived, PR #139)` — first behaviour-preserving slice of `docs/design/forecast-cycle-redesign.md`:
  split warm-up state loading to per-`(station_id, model_id)` + the assignment-keyed run unit. `/plan`-reviewed (0
  blockers, majors folded). Fixes a latent shared-state bug; foundation for later phases. Merged to `main` at
  `fa14b9a`. See [archive/148-forecast-redesign-phase1-modelruncontext.md](archive/148-forecast-redesign-phase1-modelruncontext.md).
- **149** — Reconcile the forecast-cycle redesign with the repo architecture + standards — `SUPERSEDED / ABSORBED`
  (2026-07-24). The alignment findings + 3 real contract gaps were folded DIRECTLY into `forecast-cycle-redesign.md`
  (§ Formal contracts/layering), `architecture-context.md` (Flow 1 + combination rule), and
  `types-and-protocols.md` (widened Protocol + types) — a /plan loop over-expanded the meta-plan, so the
  reconciliation was done by direct fold + an alignment re-review instead. Do not implement from 149.
- **150** — Forecast-cycle redesign **Phase 2**: per-assignment outcome SHAPE — **COMPLETE, ARCHIVED (#141, 2026-08-10)** — the
  outcome-SHAPE sub-slice of build-sequence item 2 (option A): `_run_single_model` /
  `run_all_station_forecasts` / `MultiModelForecastResult.failed_models` migrated from `StationForecastResult | str`
  to a discriminated `AssignmentSuccess | AssignmentFailure` (assignment-level `AssignmentFailureCause`), plus a
  loop-level backstop closing a latent fallback-invariant gap (an unanticipated exception in a lower-priority
  assignment no longer darkens a station whose higher-priority assignment already succeeded). Does NOT complete
  build-item 2 alone — the FI typed `ModelFailure`-signal preservation (Phase 2-FI follow-on) and the runner's
  `ModelRunContext`-consumption seam (Phase 3) are explicit named follow-ons. See [archive/150-forecast-redesign-phase2-assignment-outcome.md](archive/150-forecast-redesign-phase2-assignment-outcome.md).
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
- **126** — Requirement-aware ensemble cycle resolution — `SUPERSEDED by docs/design/forecast-cycle-redesign.md`
  (2026-07-23). Cycle-resolution can't bolt onto the single-cycle-per-batch v0 flow; folded into the redesign
  (components 2/3: per-requirement resolved-cycle map + candidate-local accumulation).
- **047** — Nepal v1 data sources umbrella (IFS, DHM, ERA5-Land) — `DRAFT (stub)` —
  depends on 081/082.
- **117** — Basin/static artifact architecture alignment — `READY` — documents the
  **adjacent** basin/static extraction artifact boundary: SAP3 consumes a validated
  package and does not integrate the extractor's code. Covers the GeoPackage
  terminology + naming rules (`g_<station_code>`), single-kind Gateway HRUs, and the
  confirmed static-Parquet shape. Unblocks the
  **basin/static architecture cleanup only** — 047 separately needs its
  **re-scope per Plan 106** before it advances.
- **120** — Basin/static importer + §5a persistence + versioned basin state — **COMPLETE, ARCHIVED
  (all 4 slices merged: #124 foundation / #126 loader / #128 write-side / #129 entrypoint+docs,
  2026-07-23).** Build-complete: a basin/static package imports end-to-end via
  `import_basin_package_from_directory` / `python -m sapphire_flow.cli.import_basin_package`, and Plan 143
  calls the `import_loaded_basin_package` core programmatically. Remaining gate is OPERATIONAL only (run
  the importer against a real accepted package before 082's resolver returns non-`None` in production —
  the "Production-gate note"). See [archive/120-basin-static-importer.md](archive/120-basin-static-importer.md).
- **147** — Auth / RBAC / audit + tenant write-isolation foundation (v1.0 headless) — **COMPLETE, ARCHIVED
  (all 5 slices merged: #130 tenant model / #131 audit-log substrate / #132 access-token auth+enforcement /
  #134 least-privilege DB roles / #140 tenant write-isolation, 2026-08-10).** Config-declared `WritePrincipal`
  (never target-derived, never a read-token) enforced pre-write on every flow/CLI write path, with success-path
  mutation+audit atomicity; hardened through 3 independent Codex rounds. Unblocks Flow-0 Nepal onboarding. See
  [archive/147-auth-rbac-tenant-isolation.md](archive/147-auth-rbac-tenant-isolation.md).
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
- **194** — The backup target must be the device it claims to be — `COMPLETE`, archived — shipped in
  PR #200 (`357386b`) with the marker-file follow-up in #201. `/plan` escalated and over-expanded on
  this one (Codex failed 3 of 4 rounds); it was reconstructed and reviewed by hand. Carried forward:
  the device predicate now exists in **three** independent copies (`bootstrap-mac-mini.sh`,
  `start-sapphire.sh`, `watchdog.py`) — verified identical 2026-08-21, but nothing keeps them so.
- **195** — A launchd agent that cannot run must not look healthy — `COMPLETE`, archived — shipped in
  PR #216 (`6af4aa0`, `v0.1.817`). The watchdog now probes `launchctl list` (never `print`, whose
  output Apple explicitly disclaims) for the installer-managed labels, latched per label, with
  probe-unreadability latched separately so "the monitor stopped monitoring" cannot present as "no
  agents failing". Four Codex rounds; two blockers were errors introduced *while fixing* earlier
  findings, and two properties were asserted-but-unlocked until mutation testing exposed them.
  ⚠️ **Not yet deployed to the mini.**
- **208** — Backups must leave the box — `DRAFT` — the off-box sink Plan 162 D4 named but never
  created (162's `blocks:` is empty). Owner 2026-08-28: no backup drive for the mini ever; separation
  arrives with the **AWS** deployment, sink is **S3**. Two findings make it more than a port: 162
  deferred this on the reasoning that "an encrypted artifact is safe wherever it lands", but
  **encryption never shipped** (D5 was Phase B; the mini's dumps are plaintext, verified) — so it is a
  prerequisite, not a companion. And **Plan 194's device predicate does not port**: meaningless on S3,
  and on EBS it passes trivially while the volume still shares an AZ with the database — a green light
  for separation that does not exist. Four decisions open.
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

See [archive/](archive/) for completed and archived plans (124 entries).

## Superseded / stranded branches (recorded 2026-08-17)

**`docs/plan-158-session-independence` — SUPERSEDED, do not build from it.** Its plan docs live only on that
branch and were never on `main`; it is now **82+ commits behind**. Everything operationally load-bearing in it
has been rebuilt on current `main` instead, because the branch had diverged too far to merge:

| Plan 158 task | Delivered by |
|---|---|
| T1 — dead-man ping | **Plan 163** (merged, PR #162) |
| T1b — forecast-production freshness | **Plan 116** (merged, PR #167) — *small version; the `(station, model, parameter)` coverage ledger was deliberately excluded after it drew 5 blockers* |
| T2/T3/T5 — watchdog in the system domain | **Plan 164** (READY — console runbook + fresh-host installer guard) |

**Still unique to that branch, if anyone wants it:** the Docker endpoint contract (`scripts/launchd/docker-endpoint.sh`,
`SAPPHIRE_DOCKER_BIN`/`SAPPHIRE_DOCKER_HOST`), the `bootstrap-mac-mini.sh` service-account and teardown fixes,
and the excluded coverage ledger. Extract deliberately; do not merge the branch.

**⚠️ Plan-number collision:** that branch also contains a `159-headless-container-runtime-migration.md`, while
`main`'s **159** is `aquacast shim (in-repo optional extra) + forecast-cycle worker image` — a different plan by
another session. Renumber the headless-runtime plan if it is ever revived.
