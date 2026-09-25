---
status: DRAFT
created: 2026-09-24
plan: 340
title: Immutable forecast evidence capture for CHWRR
depends_on: []
blocks: [341, 342, 343, 344]
related: [035, 147, 317]
reviews: []
open_decisions:
  - CHWRR will set evidence retention to at least six years after forecast valid time. Confirm the protected backup target and capacity before CHWRR publication is activated; Plan 344 adds the longer-term cold archive and diagnostic replay.
source: 2026-09-24 owner — retain the as-used evidence from the first Nepal forecast runs so later errors can be diagnosed.
---

# Plan 340 — immutable forecast evidence capture for CHWRR

## Status and scope

**DRAFT — not implementable.** This is the independent first slice of the CHWRR post-event work. It captures evidence before mutable observations, thresholds, forcing or model references change. Plan 341 adds human forecast publication, Plan 342 adds the alert evaluation and decision ledger, Plan 343 verifies outcomes, and Plan 344 adds long-term cold archive and replay. The earlier combined Plan 340 was split so this plan has no functional dependency on human authentication. Database migration, retention and model input provenance make it high-risk under `docs/workflow.md`.

Current forecast rows hold a weather cycle and model artifact ID but cannot reconstruct the exact model-ready observation and forcing arrays or the QC and threshold configuration used. `observations` and `station_thresholds` are upserted. A later change cannot reliably recover what a past run saw. Do not claim completeness for pre-migration forecasts.

## Evidence contract

- Every newly produced operational forecast links to an immutable manifest and compressed, content-addressed snapshot of model-ready observations (values, times, source, QC flags/status/rule version), forcing values/source/cycle, model/artifact/state identity and hashes, runtime image/dependency and code/config version, random seed if relevant, time step, input-quality flags, and thresholds in force. Capture the effective forecast QC rule parameters, station overrides, climatology baselines and water-level datum, rather than just a rule-set version. Record threshold direction and values at the run; Plan 342 separately captures the strategy and threshold actually used for each evaluation.
- Capture provenance before QC/source fields are lost during input assembly and bind it to the exact array passed into prediction. Cover native station models, group models with per-member station lineage, ForecastInterface-adapted models, and combined/pooled/BMA virtual forecasts with contributing IDs, weights and each contributor's forcing cycle. Do not change the ForecastInterface contract to carry SAPPHIRE metadata.
- References to an existing weather archive or model artifact need an immutable ID/hash, but the snapshot must reconstruct the actual prediction arrays without consulting mutable current rows. Pin or copy artifact and runtime-image bytes by digest before their source can be pruned; Plan 344 tests later diagnostic replay. A supplied runtime digest does not prove image-byte retention: T1 marks that gap `evidence_incomplete`, and T2 must preserve and restore the bytes before claiming completeness. Keep capture-time status immutable; any later proof for an earlier run is a separate append-only attestation. Missing material is visibly `evidence_incomplete` with cause, not silently treated as reproducible.
- A combined forecast's contributor IDs and evidence hashes must resolve to persisted evidence in the same transaction that stores the combined forecast. Missing, mismatched or incomplete contributor evidence makes the combination `evidence_incomplete`, even when the in-memory contributor object looked complete.
- Commit each forecast, its values, and either its evidence link or a specific incomplete marker in one transaction; an evidence insert failure rolls the forecast back. Test this with an injected capture/store failure so no unlinked new forecast can commit.
- The new manifest, snapshot and provenance rows are append-only: migration-level role-independent `UPDATE`/`DELETE`/`TRUNCATE` rejection, insert-only grants for writer roles, and mutation-denial tests for both service and privileged non-owner roles. A mutable current-state or ingestion projection, if needed, is separate. Content hashes detect corruption but do not replace database enforcement.
- Until Plan 344 proves cold retention and restore, no cleanup may delete evidence-linked forecast outputs, snapshots, artifact/image bytes or provenance. An interim protected backup outside the active database volume, a successful restore, and a six-year capacity estimate are required before the CHWRR publication switches in Plans 341/342 can be enabled. The existing seven-copy database backup default alone is not the retention design. Keep an explicit no-cleanup gate for these records and monitor backup freshness; Plan 342 extends that gate to evaluations and decisions, and Plan 343 extends it to truth and cases.

## Tasks

### T1 — Capture as-used forecast inputs and configuration

**Outcome.** For a new forecast ID, a reviewer can retrieve the exact model-ready input snapshot and provenance, or an explicit `evidence_incomplete` marker with cause.

**In / Out.** In: typed manifest/snapshot schema, content hash and compressed payload store, provenance capture at shared input-assembly/persistence seams for ForecastInterface-adapted, native station/group and combined forecasts, effective QC configuration, persisted-contributor checks, model artifact and weather/observation provenance, migration guards and least-privilege grants. Deduplicate identical snapshots within a station/model/cycle; measure per-cycle write latency and size in a representative six-station run. Out: ForecastInterface contract changes, retroactive reconstruction of old forecasts, permanent copies of whole external source archives, warning decisions.

**Verification.** `uv run pytest tests/integration/store/test_forecast_evidence_store.py tests/unit/flows/test_forecast_evidence.py`; mutate current observation/QC and threshold rows after a forecast and verify as-used evidence is unchanged; compare captured arrays with prediction inputs for ForecastInterface, native station and group paths, degraded input and combined contributor/weight lineage; check missing-evidence marker. As both service roles and a privileged non-owner role, reject `UPDATE`, `DELETE` and `TRUNCATE` on append-only tables after role bootstrap; permit required inserts.

**Pre-change.** RED: the current forecast row cannot reconstruct the observation/QC and exact forcing frame passed to prediction.

### T2 — Protect the first captured runs until cold archiving exists

**Outcome.** Early Nepal evidence and linked forecast outputs survive a database-volume loss and cannot be pruned before Plan 344 supplies longer-term archival.

**In / Out.** In: CHWRR-set `evidence_retention_days` with a minimum of 2,192 days after forecast valid time (covering any six-calendar-year span), cleanup exclusion for evidence-linked outputs/snapshots/artifact and image bytes, protected backup target outside the active database volume, backup-freshness health signal, one representative restore and a six-year capacity estimate. The protected backup manifest and restore test must include snapshot and artifact blobs plus pinned runtime image bytes by digest; a database-only dump is insufficient. Add an append-only preservation attestation for an earlier T1 run only after its full chain passes restore verification, leaving the original capture-time status unchanged. The attestation binds forecast ID, capture-manifest hash, artifact/image digests, protected backup identity and restore-check result/time. Expose both immutable `capture_status` and a derived `effective_preservation_status` with attestation ID and remaining reasons: it becomes complete only when every capture gap is covered by verified retained material; an absent/stale/unverifiable attestation or any other gap remains incomplete. Backup freshness remains a separate live health gate. Record the backup location and the no-cleanup activation gate in `docs/standards/cicd.md`. Out: cold Parquet/object-store tier, six-year-old replay, a new object-storage service without demonstrated need. Retention configuration may be increased by CHWRR but never set below the floor; leave deletion disabled until Plan 344's linked-record restore passes.

**Verification.** Focused config/cleanup and restore checks; below-floor retention is rejected, a seeded evidence/output/artifact/image chain survives cleanup and database-volume replacement, missing/stale protected backup blocks publication activation, and a representative Nepal-sized capacity and restore-time measurement is recorded. A T1 run initially incomplete only for unpinned image bytes gains a complete effective-preservation view after a valid attestation without changing its capture status; missing image bytes, a mismatched manifest or another unresolved capture gap leave it incomplete.

**Pre-change.** RED: the existing backup defaults to seven copies, and the architecture's future hot/cold cleanup does not preserve a six-year forecast-evidence chain.

### T3 — Document the capture and interim preservation contract

**Outcome.** Operators know which runs are reproducible, how evidence gaps appear, and what must be protected before CHWRR publication.

**In / Out.** In: `docs/architecture-context.md`, `docs/spec/types-and-protocols.md`, `docs/standards/security.md`, `docs/standards/logging.md`, `docs/standards/cicd.md`, `docs/touchpoint-maps.md` and affected handover text. Out: warning API, dashboard UI or a claim that six-year cold replay is already available.

**Verification.** Bounded inspection against the schema, capture tests, backup restore evidence and activation gate.

**Pre-change.** N/A — documentation; T1 and T2 provide behavioral RED evidence.

## Exit gates

All task checks pass, including real PostgreSQL grants/guards, a representative six-station capture and protected restore. The changed-module Ruff and pyright gates in `docs/workflow.md` pass, and the full suite passes after the final code change before merge. No CHWRR consumer publication mode or warning release is enabled by this plan. Its evidence can be captured on the first Nepal test cycles without Plan 341 or a selected IdP.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"]},
    {"id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"]}
  ]
}
```
