---
status: DRAFT
created: 2026-07-17
plan: 115b5
parent: 115b
title: Release B — retire the camels-ch weather binding (migration 0033)
scope: The migration ONLY. Splits out of 115b4 §5E so it can never share an Alembic
  head with the un-confirmed reader flip.
depends_on: [115b4]
blocks: []
---

# Plan 115b5 — Release B: retire the camels-ch weather binding

> **Design source: [Plan 115b4](115b4-reader-flip-cutover.md) §5E.** This plan carries
> ONLY the second, later release the plan mandates — it does not repeat 115b4's design
> rationale, only the deploy mechanics for shipping it safely.

## Status

**DRAFT — blocked on the Release-A deploy-gate, not on code.** The migration
(`0033_retire_camels_ch_weather_binding.py`) and its integration test
(`tests/integration/db/test_migration_0033_camels_retire.py`) are already fully authored
— they were built alongside 115b4 and then split OUT of that branch/commit by the 115b4
fixer round (independent Codex review, round-1/round-2 blockers 1+2+5) because a plain
`alembic upgrade head` deploy on a branch carrying BOTH the flip (115b4 Release A) and the
retire migration (this plan's Release B) in the same Alembic head collapses the two
sequenced releases the owner explicitly decided on 2026-07-16 — see
`docs/plans/115b4-reader-flip-cutover.md` §5E and `docs/standards/cicd.md` "Two-release
reader flip + camels-ch retirement".

**This plan's content lives on a separate branch, not on `main`, and not in this
plan-doc's diff.** `tests/unit/db/test_alembic_head_release_a.py` (on `main`, part of the
115b4 fixer round) enforces this mechanically: it fails if a camels-ch-retire migration
file appears in `alembic/versions/` before this plan is ready to merge.

## Merge gate (hard requirement, not a suggestion)

Do **not** merge this plan's branch to `main` until ALL of the following are true on the
target deployment:

1. Release A (115b4 §5A-5D + phase-6) has been deployed to staging.
2. `ingest-weather-history` reports a **non-zero** effect (`WEATHER_HISTORY_INGEST`
   `PipelineHealthRecord` = `OK`, per §6B health-by-effect — an advancing
   `MAX(valid_time)`, not merely `rows_stored`).
3. A station serves past-dynamic features via the `RHIRESD → RPRELIMD`/`TABSD`/…
   priority chain (a real forecast cycle or dashboard forcing-endpoint read, not a
   repo-review inference).
4. A forecast cycle completes on the new (hybrid-resolved) series.

Only once all four hold: merge this branch, delete/update
`tests/unit/db/test_alembic_head_release_a.py`'s `_RELEASE_A_HEAD` constant to `"0033"`
as PART of that same merge (not a follow-up commit), and deploy on the standard
`alembic upgrade head` path per `docs/standards/cicd.md`.

## Scope

- **5E — retire the camels-ch weather binding.** Migration `0033`
  (`down_revision = "0032"`): `DELETE FROM station_weather_sources WHERE nwp_source =
  'camels-ch'`. Does NOT touch `historical_forcing` — the camels-ch forcing ROWS remain
  the Plan 115b3 validation reference + audit trail, readable by a direct source-keyed
  fetch. `downgrade()` is a deliberate no-op (logs a warning, does not raise) — see the
  migration's own docstring for the full rationale (real rollback is backup-restore +
  previous image, not schema `downgrade()`).
- Integration tests (already authored, carried on the separate branch):
  upgrade-deletes-only-the-binding, downgrade-is-a-noop, and a Release-A-serving proof
  (hybrid reader serves rows with schema at `0032`, one revision before this one).

## Rollback

Standard path only (restore from backup + redeploy previous image tag,
`docs/standards/cicd.md`) — not a schema `downgrade()`. See `docs/plans/115b4-reader-flip-cutover.md`
§5E for why a fabricated resurrection of deleted binding rows is not offered.

## Provenance

Split out of `docs/plans/115b4-reader-flip-cutover.md` §5E by the 115b4 fixer round
(2026-07-17), resolving an independent Codex review blocker: the retire migration must
never share `main`'s Alembic head with the unconfirmed reader flip.
