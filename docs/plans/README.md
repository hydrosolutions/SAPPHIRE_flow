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

## Deferred

- **039** — Sensor/Model Failure Visibility for Operators — `DEFERRED` — Sensor-offline visibility belongs in Flow 4 (pipeline monitoring); revisit when Flow 4 is scoped.
- **042** — API Key Auth + Client SDK — `DEFERRED` — Auth and SDK deferred to v0b — no external consumers during v0.

## Archived

See [archive/](archive/) for completed and archived plans (46+ entries).
