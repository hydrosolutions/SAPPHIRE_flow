# SAPPHIRE Flow — Plan Index

Update this index whenever a plan's status changes or a new plan is added. List
every plan file currently under `docs/plans/`. Do not auto-generate — maintain
by hand.

## Active

- **015** — Virtual Station Support — `READY` — Design for virtual station types, calculated station formulas, flow impacts, and QC propagation (v1 target).
- **017** — Manual vs Automatic Station Support — `DRAFT` — Per-station observation frequency, manual/automatic classification, and watchdog/QC implications for mixed networks.
- **035** — Rating Curve Provenance for Skill Score Integrity — `READY` — Schema, types, and flow logic for rating-curve provenance tracking (v1 Nepal).
- **038** — Store Write Atomicity — `DRAFT` — Wrap two-phase store inserts in transactions to eliminate orphan header rows under crash.
- **040** — Hindcast Deduplication Constraint — `DRAFT` — Add a unique constraint to `hindcast_forecasts` to prevent duplicate rows.
- **046** — Mac Mini Staging Deployment + Edge-Case Test Suite — `IN_PROGRESS` — Staging infrastructure on the Mac mini plus the deployment-validation edge-case suite.
- **047** — Nepal v1 data sources (ECMWF IFS, DHM, ERA5-Land, elevation bands) — `DRAFT (stub)` — Placeholder for Nepal v1 adapter work; filled in once v0 wraps.
- **048** — restic + encrypted backup + monthly restore rehearsal — `DRAFT (stub)` — v1 backup hardening: restic, encryption, and monthly restore rehearsal on staging.
- **049** — Cloudflare Public URL for SAPPHIRE Staging — `DRAFT` — Publish the Mac-mini staging API via Cloudflare Tunnel + Access with Entra SSO and OTP for external viewers.
- **057** — API route-module tests — `DRAFT (stub)` — Test coverage for the HTML route modules under `api/routes/` plus `health.py`.
- **058** — BAFU LINDAS archive via operational collection on Mac Mini v0 — `DRAFT` — Build a BAFU LINDAS archive by running the v0 ingest on the Mac mini after Plan 046 is DONE.
- **062** — Prefect state persistence (`PREFECT_HOME` ↔ `prefect_data` volume) — `DRAFT` — Set `PREFECT_HOME` so SQLite DB, deployments, and flow-run history persist on the named volume.
- **064** — Supply-chain hardening — `DRAFT` — Pin third-party inputs, add CVE scanning and SBOMs, document the new posture.
- **066** — Train-models retrain strategy (configurable) — `DRAFT` — Framework so retrain data-window is selectable; default restores dress-rehearsal A3 step 4 (F3).
- **067** — MeteoSwiss STAC adapter investigation + configurability — `READY` — Root-cause the "cycle late" signal (F4), then move `_MAX_FALLBACK_STEPS` into config and decide pagination-cap fate (F5). Four review rounds.
- **068** — `onboard-stations` parallelization + decouple historical hindcast — `DRAFT` — Cut 38 min onboarding to seconds; move historical hindcast to new async `backfill-hindcasts` flow with `task.map`. Depends on Plans 038 + 040.
- **081** — recap-dg-client forcing adapter — `DRAFT` — Offline-completable Nepal v1 Recap adapter foundation, variable catalog, metadata design, band converter, and fake-client contract tests.
- **082** — recap Gateway operational and training readiness — `DRAFT` — Live Gateway smoke, Nepal config, latest-cycle/watchdog semantics, temporal model-input join, coverage gate, and runbooks. Depends on Plan 081.
- **083** — Human-readable station code in structured logs — `DRAFT` — Bind `station_code` alongside the UUID `station_id` at per-station fan-out boundaries so operators can read logs without a UUID lookup; update `logging.md`.
- **084** — Dev-machine deployment validation (2-station runoff-only) — `READY` — Clean, repeatable end-to-end validation of the local dev stack for 2 BAFU river stations (2009/2091): onboard → operational → runoff-only forecast → idempotency re-run → optional NWP, hardened against the six Mac-mini failure modes before the mini re-attempt.
- **085** — Observation ingest: value-restatement upsert + 5-min poll cadence — `READY` — Scoped `on_conflict_do_update` so BAFU value restatements are captured (last-write-wins on a real value change) with a `value IS DISTINCT FROM` predicate, QC-state reset for in-flow re-QC, and a stored/skipped counting-trap fix; plus raising the default ingest cron `*/30`→`*/5` for the snapshot-only LINDAS adapter. WF2 fix-mode.
- **086** — NWP forecast-cycle memory-bounded (lazy/dask) streaming — `READY` — Fixes the Plan 084 NWP-OOM (which occurs at the eager cfgrib parse/merge in `_parse_grib_files`, before archive/extract) by opening cfgrib with `chunks={}` so the grid cube is dask-backed, **plus** rechunking the source in `ZarrNwpGridStore.archive` to `(1, *shape[1:])` (leading axis derived from a data variable's axis-0, not `ds.dims`) so `to_zarr` streams a small bounded number of `valid_time` slabs (tens–low-hundreds of MB) instead of raising a dask-chunk-overlap `ValueError` (THE BLOCKER, empirically verified on the real `(valid_time, member, values)` ICON mesh fixture — no lat/lon dims). Folds in two guardrails — wiring the existing `max_files` cap into config and a 6–8 GiB `prefect-worker` `mem_limit` (bounds blast radius, not graceful failure). Extraction-from-mesh is a SEPARATE pre-existing gap (Open Item E), out of scope. dask already a dependency. WF2 fix-mode (laziness-property + archive-round-trip repro).

## Deferred

- **039** — Sensor/Model Failure Visibility for Operators — `DEFERRED` — Sensor-offline visibility belongs in Flow 4 (pipeline monitoring); revisit when Flow 4 is scoped.
- **042** — API Key Auth + Client SDK — `DEFERRED` — Auth and SDK deferred to v0b — no external consumers during v0.

## Archived

See [archive/](archive/) for completed and archived plans (46+ entries).
