# Plan 048 — restic + encrypted backup + monthly restore rehearsal (v1 prep)

**Status**: DRAFT (stub)
**Phase**: 10d (production backup hardening)
**Depends on**: Plan 046 (Mac mini staging deployment, DONE — provides the staging host
this plan validates against)

---

## Why this exists

Plan 046 explicitly defers restic, encrypted backup chains, and restore rehearsal to
stay inside v0-scope.md §A10 ("v0: pg_dump to local disk. No restic, no encrypted
backup chain, no restore rehearsal."). The restic pipeline is the first thing that
must be validated on the Mac mini staging host **before** Nepal production relies on
it. This stub exists so the decision isn't lost after Plan 046 ships.

## Scope (to be filled in when promoted to DRAFT)

- Replace `backup_database_flow`'s `pg_dump` output with encrypted restic snapshots.
- 7/4/12 retention (daily / weekly / monthly) per v0-scope.md §A10 full design.
- Monthly automated restore rehearsal: dump → fresh postgres container → load →
  assert row counts + spot-check forecasts → destroy.
- Document the 12-step disaster-recovery procedure.
- Validate the full chain on the Mac mini staging host before Nepal v1 cutover.

## Not in scope

- Off-site backup target (separate follow-up — needs AWS or hydromet availability).
- Production-grade TLS — independent of backup.

## Open questions (to resolve before promoting to READY)

- Encryption key management: file-based secret, Keychain, or KMS?
- Off-site target timing: bundled here or deferred again?
- Rehearsal failure alerting: reuse the §C5 Slack watchdog path or separate channel?

## Exit gates (sketch)

1. restic repo initialised on the Mac mini external USB disk; daily snapshots running.
2. Monthly restore rehearsal flow green for two consecutive months on the Mac mini.
3. Disaster-recovery runbook committed and reviewed.
4. v0-scope.md §A10 updated to reflect the promoted state.
