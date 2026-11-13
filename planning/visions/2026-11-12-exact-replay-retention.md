# Retain exact replay evidence within affordable windows

Program: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/481
Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/483

MVP scope. Hardening, an archival platform and general replay tooling are deferred,
not forgotten. Status: DRAFT for owner review. No implementation, deletion or host
change is authorised by this document.

## Outcome

A deployment can say, per forecast, whether its model output can be reproduced
exactly, for how long, and what it costs to keep that ability. Nepal targets six
months. Swiss targets seven days unless compact inputs prove six months affordable
(they do not, see below). Retention is per deployment. Nothing is deleted until a
bounded replay shows the retained boundary reproduces the output.

## What already exists (source and measurement)

Source facts:

- Plan 340 captures, per operational forecast, a content-addressed compressed
  snapshot of the exact model-ready inputs (observations with QC state, forcing
  values and source, configuration), the model artifact bytes, thresholds and a
  manifest (`code_version`, `python_version`, dependency versions, optional
  `runtime_image_digest`). Tables `forecast_evidence` and `forecast_evidence_blobs`;
  `services/forecast_evidence.py` already has `restore_snapshot`.
- The evidence tables are append-only by design (migration-level mutation
  rejection) and the protected-backup path expects them kept.
- `forecast_evidence.py` always appends `runtime_image_bytes_unpinned` to the reason,
  so no row can be `complete` until image bytes are proven to survive a restore.
  The `complete` status feeds the CHWRR publication gate. **This Effort does not
  change what `complete` means.**
- `SAPPHIRE_IMAGE_DIGEST` is passed through `docker-compose.yml` but must be set by
  hand from `docker image inspect` (docs/standards/cicd.md).

Measured on the Mac mini, 2026-11-12 (read-only queries, one host; not a statement
about any other deployment):

| Item | Value |
|---|---|
| Evidence blobs | 99,221 blobs, 81 GB |
| Input snapshots | 98,693 blobs, 81 GB, average 856 kB; almost no sharing between forecasts |
| Model artifacts | 528 blobs, 28 MB |
| Orphan blobs | 0 |
| Growth | about 2,400 forecasts/day since 2026-11-05, roughly 1.7-2 GB/day |
| Six months at that rate | about 300 GB of snapshots (extrapolation, not measured) |
| Share of the nightly dump | about two thirds of a 125 GB dump |
| Evidence rows | 100,526, none `complete`; 98,547 only because the image digest is unset |

Measured on the Nepal (Infomaniak) host, 2026-11-13, read-only: 9 blobs, 14 MB in total
(8 snapshots averaging 1.6 MB, 1 model artifact of 1.8 MB), 99 forecasts since 2026-11-07,
one new snapshot per day because the forecasts of a day share it, about 1.7 MB/day. Six months
is therefore roughly 300 MB: storage is not a constraint for Nepal. All 99 rows are
`evidence_incomplete`.

Not measured: whether a snapshot really reproduces a forecast bit for bit, replay cost in time.

## Decisions proposed for the owner

1. **Replay boundary = snapshot + artifact + code identity.** Replay means re-running
   the model on the stored snapshot with the stored artifact, not re-running upstream
   grid processing. Runtime identity is the source revision, `python_version` and
   dependency versions already in the manifest, plus the image ID once it is set.
   Retaining image archives is **not** part of this Effort (owner, 2026-11-13: old images
   are not kept on servers; the stored release number allows rebuilding); state it as a limit.
2. **Nepal: six months. Swiss: seven days**, with the reason (about 300 GB per six
   months on a disk shared with the database and its dumps) written in the operator
   docs. Both configurable per deployment.
3. **Set the runtime image ID routinely** (one documented step, later part of the
   one-command deploy) so manifests record which image ran. This is configuration,
   not new code.
4. **Expiry of old snapshots is a separate, owner-approved step** because it touches
   the append-only protection and the CHWRR evidence promise. Proposal: first measure
   and verify; decide the expiry mechanism only after the replay check passes, and
   only for deployments whose policy allows it. If expiry needs a migration or a
   relaxed guard, that gets its own review.

## Minimum delivery

- **T1 Bounded replay check.** One small script or test that, for a handful of stored
  forecasts (Swiss and Nepal if reachable), restores the snapshot, re-runs the model
  with the stored artifact and compares to the stored values. Reports exact match,
  tolerance match or mismatch with the reason. Reuse `restore_snapshot`; no replay
  platform.
- **T2 Size model.** From the measured snapshot size and forecast rate, write the
  per-deployment cost of 7 days, 30 days and 6 months, (Nepal already measured: about 1.7 MB/day).
- **T3 Retention setting and docs.** A per-deployment retention value (default: keep
  everything, i.e. no behaviour change), the documented Nepal/Swiss windows, the
  image-ID step, and the stated limits. No deletion code in this task.
- **T4 (only after T1 passes and the owner approves expiry)** the smallest expiry that
  deletes snapshot blobs older than the window, never the last verified copy and never
  evidence needed by a protected backup.

Coordinate with Effort 482 (done): excluding weather tables from backups stays a
separate decision after replacement evidence exists. This Effort does not change the
weather cache policy.

## Out of scope

Model retraining, frontend work, an off-server backup service (still absent; see the
backup operator record), replay laboratory, archival platform, compliance guarantees,
changing the `complete` semantics or the CHWRR gate, upstream-archive substitution
(unsupported without exact-version retrieval).

## Acceptance evidence

- Replay result table for the sampled forecasts, with the exactness claim stated per
  row and any mismatch explained.
- Measured Nepal and Swiss snapshot volume and the 7-day / 30-day / 6-month cost.
- Documented retention windows and the stated limits (image bytes not retained;
  unsupported exactness reported, not weakened).
- No deletion has run before T1 evidence exists.

## Risks and open questions

- A snapshot may not reproduce a forecast exactly (non-deterministic models,
  floating-point or dependency drift). T1 decides how "exact" is worded.
- Final liability periods remain with the hydromet partners.
