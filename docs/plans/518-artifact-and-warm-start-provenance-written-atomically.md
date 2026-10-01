---
status: DRAFT
created: 2026-09-30
plan: 518
title: Artifact + warm-start provenance are written atomically — a failed provenance write leaves no half-stored artifact
scope: Move the warm-start provenance INSERT of a retrain into the same real database transaction as the artifact INSERT (extend the Plan 147 Slice E audited-writer seam), delete the weights file when that transaction rolls back, pin it with red-first real-Postgres tests, and correct the docs that say atomicity is not claimed. Basin lineage is decided explicitly (kept out, rationale corrected). The existing orphan on the mini is a separate owner-approved hand step, not code.
risk: medium   # NOT high-risk by the retired workflow criteria — see D8; the owner may still flag it
related: [399, 405, 147, 120, 157, 350, 514, 517, 323]
open_decisions: [Q1, Q2, Q3, Q4]
---

# Plan 518 — artifact and warm-start provenance are written atomically

## Status

**DRAFT.** Follow the global skill and `AGENTS.md`; required independent review still applies.
Nothing here touches a host or a database; T6 (the existing orphan) is an operational hand step the owner decides.

## Why

On the Mac-mini a warm-start fine-tune trained about eight minutes, then failed on `INSERT INTO
model_artifact_warm_start` (`sapphire_worker` lacked INSERT; fixed by PR #350). It left an **orphan**: a
`model_artifacts` row (`5ed37297-12b0-4edc-92a1-575c5ffddb40`, status `training`, never promoted, inert) with its
weights file on disk and no provenance row. The missing grant was the trigger; the defect is that the two writes
are separate commits, so **any** failure of the second leaves the first committed.

## Facts (read at `origin/main` 87d33338, 2026-09-30; T1 re-verifies)

- `train_models.py::_store_artifact_task` runs the artifact write in ONE real transaction
  (`AuditedWriter.transaction()`, `store/audited_writer.py`; Plan 147 Slice E). For a retrain (`promote=False`) it
  calls `store_artifact` only — no promote, **no audit row** — so that transaction holds a single INSERT.
- `_record_warm_start_provenance` then runs **after** that task, on `PgWarmStartWriter(_conn)`, where `_conn` is the
  AUTOCOMMIT connection from `setup_production_stores`. Two commits.
- `PgModelArtifactStore.store_artifact` writes the file **first**, then INSERTs; it unlinks the file only if **its
  own** INSERT raises (Plan 157 T3 G8). A later rollback in the same transaction cannot undo the file write.
- The record is fully built and validated **before training** (Plan 405 T1: `check_config_provenance` /
  `check_params_provenance`, donor config resolved and threaded). Only `artifact_id` is unknown until the store
  runs, because `store_artifact` mints it. Nothing needs computing late. `record_warm_start(conn, record)` is a
  plain INSERT.
- `model_artifact_warm_start.model_artifact_id` is PK and FK to `model_artifacts.id`; `base_artifact_id` is an FK
  to `model_artifacts.id` (alembic 0060). Inside one transaction the artifact row is visible to the provenance
  INSERT, so the FK is satisfied.
- Precedent for exactly this pattern already exists: `services/model_import.py` runs store + provenance + audit in
  one `writer.transaction()` and unlinks the file when the transaction rolls back (~lines 510-540), with an
  `_InMemoryAuditedWriter` that gives fakes rollback semantics.
- Basin lineage (`_record_lineage_task`, `store/model_artifact_lineage.py`) is the same non-atomic pattern, and the
  warm-start writer "mirrors it deliberately". Its written rationale: "matches the pre-existing store+promote
  relationship, which is already non-atomic under the AUTOCOMMIT connection". **That premise is stale**: since Plan
  147 Slice E, store + promote + audit ARE atomic on the production path.
- **Which worker runs training** (verified): `register_deployments.py` registers `train-models` on `WORK_POOL =
  "default"`; in `docker-compose.yml` that pool is served by `prefect-worker` (the `sapphire-flow-aquacast` image).
  `prefect-worker-ingest` serves only the `ingest` pool. **UNVERIFIED on the host**: that the mini's overlay does
  not change this.
- There is **no `doctor` command** in `src/sapphire_flow/cli/` (only `check.py`, the developer lint gate). A reaper
  would be new.

## Decisions (recommended answers; the owner confirms)

| # | Decision | Why |
|---|---|---|
| D1 | **Write the warm-start row inside the same `writer.transaction()` as the artifact store.** `make_audited_stores` gains a `warm_start_store` (`PgWarmStartWriter(conn)`, the existing class) bound to the transaction connection. A failure rolls back both rows and the run fails loudly. | Smallest change; the seam, the precedent (`model_import`) and the FK ordering already fit. |
| D2 | **The record is built before training as a spec without `artifact_id`** (`WarmStartSpec`, a frozen dataclass next to `WarmStartRecord`, with `to_record(artifact_id)`), passed into `_store_artifact_task`, and turned into the record inside the transaction right after `store_artifact` returns the id. | `artifact_id` only exists once the store runs; nothing else is deferred, so the Plan 405 ordering rule ("do not re-resolve at the record site") is preserved. |
| D3 | **File handling: write first, commit the database, compensate on rollback.** If the transaction raises, unlink the file that `store_artifact` wrote in this call (path taken from `StoredArtifact.artifact_path`), then re-raise. Cleanup wraps **only** the `with writer.transaction()` block, never later steps. | Same as `model_import`. A crash between file write and rollback leaves an orphan **file** with no row, which is inert. |
| D4 | **Rejected: write to a temp name, rename after commit.** | A crash between commit and rename leaves a **committed row pointing at a missing file**, which is worse than an orphan file (the reader fails at load time). It also changes the path contract `store_artifact` returns. |
| D5 | **Retrain (`promote=False`) only.** A fresh train (`promote=True`) has no donor and records nothing (Plan 399 T4); its path keeps store + promote + audit exactly as today, but **gains the same file cleanup** because the audit-insert-failure case leaks the file today (T3 measures it). | Same cleanup, no new write. |
| D6 | **Fake / `audited_writer is None` path: explicitly exempt from database atomicity, but still fails loudly.** Fakes have no transactions. The flow keeps calling the injected `warm_start_writer.record(...)` after the store; a raise from it propagates. A unit test pins that. Production cannot reach this path: `audited_writer` is built whenever `_conn` exists. | Reusing `model_import`'s `_InMemoryAuditedWriter` would drag a second snapshot mechanism into the train flow for no production benefit. |
| D7 | **Basin lineage stays out of this plan; its stale rationale is corrected.** See "Lineage decision". | Different failure semantics and a second flow (`onboard`) share it. |
| D8 | **Not high-risk** (ordinary pair; owner may flag otherwise). Criteria checked: no auth/secrets/privilege change; no migration; no external contract or API change; no Prefect scheduling or entrypoint change; no ForecastInterface boundary change; no scientific behaviour change. The seam is shared with promote/audit (Plan 147), but the change to it is one **added key** in `make_audited_stores`; the promote path's statements are untouched. The new file deletion is bound to the path this call just wrote. That the mini runs it is not grounds (retired workflow). | Judge by what the change does. |
| D9 | **Per-unit failure policy is unchanged.** The store call is outside the per-unit training guard, so a provenance failure aborts the run. Kept: it is loud. T1 confirms this from the code. | Not a new decision. |

## Lineage decision (Plan 120 `_record_lineage_task`)

The docstring argues non-atomic on purpose. **Its premise no longer holds** (Slice E made store + promote + audit
atomic). The argument for leaving it anyway is different, and this plan states it instead of inheriting the old one.

- **For joining now:** the same orphan class exists (an artifact without lineage); one seam.
- **Against joining now (recommended):** (a) lineage runs after **promote** on the fresh-train path, so joining it
  would let a lineage integrity error (`ValueError`: dangling basin, "Task 0A invariant violated") roll back the
  promotion of a good newly trained model, a behaviour change to the fresh-train and onboarding paths that needs
  its own decision; (b) `onboard_model_flow` shares the writer (`flows/onboard.py:110-146`), so the change is wider;
  (c) lineage rows are additive and re-derivable from the artifact's stations, unlike warm-start provenance, which
  cannot be recovered after the fact (its own docstring); (d) this fix is small.
- **Outcome:** lineage unchanged. T5 corrects the stale sentence in `store/model_artifact_lineage.py` and
  `_record_lineage_task` to say what is true now, and names atomic lineage as an owner-decided follow-on (Q2).

## Not in scope

- The existing orphan `5ed37297-...` (T6, hand step). Any deletion on the mini.
- Basin lineage atomicity (D7). Promotion, audit, or authorisation behaviour. Any change to `store_artifact`'s
  own signature or the `ModelArtifactStore` Protocol.
- A reaper / orphan report command (named as a follow-on in T5's note; see Risks).
- The warm-start recorder's fields, the donor resolution or refusal (Plan 405), the grant (PR #350).
- ForecastInterface: **SAP3-internal, no model or FI change.** No FI issue is needed; the FI contract is not touched.

## Tasks

### T1 — Re-verify the facts and the flow's failure path (read-only)
**Outcome:** the Facts above hold at the implementation base; the store call sits outside the per-unit guard; the
`typed_audit is None` branch in `_store_artifact_task` is understood (a `writer` present with `audit_log_store`
None currently falls to the direct path, which would silently keep the two-commit behaviour for a retrain — the
new path must be transactional whenever a writer exists and a warm-start spec is given).
**In / Out:** a dated note in this plan. Out: any edit.
**Verification:** the note quotes the lines.
**Pre-change:** N/A (read-only).

### T2 — Failing tests first (real Postgres)
**Outcome:** the tests in "Tests" exist and fail for the reason the defect exists.
**In / Out:** `tests/integration/flows/test_train_models_warm_start_pg.py` (extend), `tests/integration/db/test_slice_e_audit_atomicity.py` (extend) and a new small file if cleaner; `tests/unit/flows/test_train_models.py` for the fake path. Out: `src/`.
**Verification:** `uv run pytest tests/integration/flows/test_train_models_warm_start_pg.py tests/integration/db/test_slice_e_audit_atomicity.py -q` (local testcontainer per project memory) — the new cases are RED.
**Pre-change:** RED is the deliverable: provenance-failure leaves a `model_artifacts` row and a file (asserted by the test, not by reasoning).

### T3 — The atomic write and the file cleanup
**Outcome:** a retrain writes artifact + warm-start row in one transaction; any exception inside the transaction
block removes the file written by this call (retrain and fresh-train paths alike) and re-raises; the fake path is
per D6.
- `store/audited_writer.py`: add `"warm_start_store": PgWarmStartWriter(conn)` to `make_audited_stores`.
- `store/model_artifact_warm_start.py`: `WarmStartSpec` + `to_record`.
- `flows/train_models.py`: build the spec before the store (from the values already resolved), pass it to
  `_store_artifact_task`; inside the transaction call `warm_start_store.record(spec.to_record(aid))` after the
  store; remove the post-hoc `_record_warm_start_provenance` call on the production path (keep it for the D6 fake
  path). Capture the written path with a small recording wrapper around the transaction's artifact store
  (rejected alternative: changing `store_and_promote_artifact`'s return, a shared service signature).
- The unlink is skipped when the database state was not rolled back (mirror the `model_import` guard).
**In / Out:** the three files above. Out: `store_artifact`, `store_and_promote_artifact`, migrations, lineage.
**Verification:** T2's tests GREEN; `uv run pytest tests/unit/flows tests/integration/flows tests/integration/db/test_slice_e_audit_atomicity.py -q`; `uv run pyright`; `uv run ruff check`; `uv run ruff format --check`.
**Pre-change:** T2's RED evidence.

### T4 — Mutation checks
**Outcome:** each check is run once by hand and shown to fail a named test, then reverted.
**In / Out:** none permanent. **Verification:** the note lists check, failing test.
**Pre-change:** N/A.

### T5 — Docs and stale text
**Outcome:** the wrong text is **replaced**, not annotated.
- `docs/touchpoint-maps.md` ~lines 994-1000 ("Atomicity is NOT claimed" and "Nothing cleans it up"): rewrite to
  the new contract (one transaction, file compensated on rollback, orphan **file** possible only on a crash inside
  the window, the record still built before training). Line ~846 (`store_artifact` "can partial-write"): keep as
  the general flow rule and add that the train path is atomic for a retrain.
- `store/model_artifact_lineage.py` docstring and `_record_lineage_task` docstring: replace the stale
  "already non-atomic" premise with the D7 reasoning.
- `docs/plans/archive/405-...md` (the "Atomicity is NOT attempted" lines ~170 and ~201): they were a true
  decision then; add one dated pointer "superseded by Plan 518" in place, do not rewrite history.
- Checked and **no change needed**: `docs/conventions.md` (lists only the grant), `docs/standards/orchestration.md`
  (no atomicity text found), Plan 399 (asserts no atomicity, per Plan 405 §46).
- `docs/plans/README.md`: status row.
**Verification:** `grep -rn "Atomicity is NOT\|Nothing cleans" docs/touchpoint-maps.md` returns nothing stale; version bump per AGENTS.md.
**Pre-change:** N/A (documentation).

### T6 — The existing orphan (operational; the owner decides; NOT run by this plan)
Artifact `5ed37297-12b0-4edc-92a1-575c5ffddb40` on the mini. Read-only checks first: (1) no `model_artifact_warm_start`
row for it; (2) not referenced by any `hindcast_*`, `skill_*`, assignment, `model_artifact_basin_versions`, or
`base_artifact_id` row; (3) its file exists at `<artifact_dir>/<model_id>/5ed37297-...bin`. Then the owner picks
one: **delete** the row and the file (with a backup of the database first), or **keep** it inert. Recommendation:
delete after the checks, once T3 is deployed, and re-run the fine-tune. It is inert today (never promoted).
**Pre-change:** N/A.

## Tests (T2; red-first, real Postgres)

1. **Provenance fails, nothing remains.** A retrain whose spec violates a constraint (a `base_artifact_id` that does
   not exist, an FK violation) raises; afterwards no `model_artifacts` row and no file under a temp artifact dir.
   RED today: row and file remain.
2. **Same, via the real grant.** On a scratch DB with the worker role's INSERT on `model_artifact_warm_start`
   revoked (extends `test_role_bootstrap.py`'s scoped-role style): no row, no file. This is the incident, replayed.
3. **Happy path.** Both rows exist, the file exists, the artifact is `training`, never promoted.
4. **Audit insert fails on the fresh-train path (existing behaviour).** No `model_artifacts` row (already true) and
   **no file** (RED today: the file leaks).
5. **Cleanup is scoped.** A failure raised **after** a successful commit (for example a later step in the loop)
   does not delete the committed artifact's file.
6. **Fake path (unit).** With an injected fake writer and no `audited_writer`, a raising `record` propagates; no file
   is unlinked; a retrain with no donor writes nothing.
7. **A fresh train records no warm-start row** (D5).

**Mutation checks (T4):** (a) delete the unlink: tests 1, 2, 4 fail. (b) move the warm-start write back out of the
transaction: tests 1 and 2 fail (row survives). (c) drop `warm_start_store` from `make_audited_stores`: test 3 fails.
(d) widen the cleanup to wrap the whole loop body: test 5 fails. (e) remove the writer-present/`audit None` branch
fix (T1): a retrain test with `audit_log_store=None` and a real writer fails.

## Risks

- **A crash between the file write and the rollback** leaves an orphan file with no row. Inert. Not detectable today
  (no `doctor`). A read-only "files with no `model_artifacts` row" report is a cheap follow-on; a "`training` row
  without provenance" report is **not** possible by status alone, because a retrain's artifact is legitimately
  `training` until a human promotes it, and only the warm-start row says it was a retrain.
- **Deleting a file that a committed row uses** would be worse than the orphan. Mitigation: unlink only inside the
  transaction block's exception path, only the path written by this call, only when the store was rolled back;
  test 5 and mutation (d).
- **Shared seam.** Adding a key to `make_audited_stores` builds one more store object per transaction (cheap, no
  SQL). The promote/audit tests in `test_slice_e_audit_atomicity.py` must stay green untouched.
- **Rollout.** A change to the train flow reaches the mini only through a deploy. The documented upgrade procedure
  (`docs/standards/cicd.md` § Upgrade procedure) quiesces and restarts **both** workers, including
  `prefect-worker-ingest`. ⛔ The Plan 323 T5 measurement window on the ingest worker runs **2026-10-01
  00:00Z-24:00Z (read 2026-10-02)**: **no deploy that restarts ingest before that read.** Verified from
  `docker-compose.yml` and `register_deployments.py`: training runs on `prefect-worker`, not the ingest worker, so
  recreating only that service (`docker compose up -d --no-deps prefect-worker` with the same `-f` overlays and
  exported tokens) is technically possible, but it departs from the documented procedure (the ingest container
  would keep running the older image tag while `.env` `VERSION` moves) and the bind-mount trap after a `git pull`
  applies. Recommendation: **wait until after 2026-10-02** and use the normal procedure; the change is not urgent
  because #350 already fixed the grant.

## Owner decisions (2026-09-30)

The owner accepted the recommendation on all five open questions below (Q1 roll back the whole saved model when its record fails: yes; Q2 lineage: a separate decision after this ships; Q3 leftover-file report: deferred; Q4 deploy after the 2026-10-02 ingest measurement read, via the normal procedure; Q5 delete the leftover model after the read-only checks and a backup). Status stays DRAFT until reviewed.

## Open questions (plain language, with a recommendation)

- **Q1 — Should a failed fine-tune record roll back the whole saved model?** Yes means a failure leaves nothing
  half-saved, at the cost of losing the file of a fine-tune that took eight minutes (you re-run it).
  *Recommendation:* yes; a saved model with no record of what it was tuned from cannot be trusted later.
- **Q2 — Should the basin-lineage record get the same all-or-nothing treatment?** It would mean a bad basin
  record could block a newly trained model from going into service. *Recommendation:* not in this plan; decide it
  separately, after this one is live.
- **Q3 — Do you want a small report that finds leftover model files with no database row?** *Recommendation:*
  defer; it is cheap but nothing has shown a need yet. (A report for "saved retrain without provenance" cannot be
  built from status alone.)
- **Q4 — Deploy timing.** *Recommendation:* deploy through the normal procedure after the ingest measurement is read
  on 2026-10-02, not by restarting one service earlier.
- **Q5 (hand step T6) — Delete the leftover model `5ed37297...` or keep it?** *Recommendation:* delete after the
  read-only checks and a database backup; it is unused.

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T4", "T5"], "depends_on": ["phase-3"], "parallel": true },
    { "id": "phase-5", "tasks": ["T6"], "depends_on": ["phase-3"] }
  ]
}
```

T6 is an owner-approved operational step; it is not part of the implementation PR.
