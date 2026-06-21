# Plan 068 — `onboard-stations` parallelization + decouple historical hindcast

**Status**: DRAFT
**Date**: 2026-04-21
**Depends on**: **Plan 038** (store write atomicity — transactional two-phase inserts) and **Plan 040** (hindcast deduplication constraint). Both are DRAFT; promoting them to READY unblocks this plan's implementation. Informed by dress-rehearsal 2026-04-21 scaling observation (Plan 046 Rev 11, finding: 38 min onboard-stations at 5 → ~26 h at 169).
**Scope**: Restructure `onboard-stations` (Flow 5) so it no longer blocks on per-station per-model historical-hindcast + skill-gate computation. Deliver two complementary changes: **(a) decouple** the historical-hindcast + skill-gate phase from `onboard-stations` into a new asynchronous `backfill-hindcasts` flow — onboarding returns in seconds, backfill runs for minutes-to-hours in the background; **(b) parallelize** the per-(station × model) loop inside the backfill flow using Prefect `task.map` with a bounded concurrency limit. Stations become `operational` (eligible for real-time ingest) as soon as their models are trained — skill scores catch up asynchronously.

---

## Context

### Why now

- 2026-04-21 A3 rehearsal: `onboard-stations` took 38 minutes for 5 stations, all 12 artifacts trained + historical-hindcast + skill-gate included. Linear extrapolation to 169 stations ≈ **26 hours**. That blocks:
  - Plan 046 §A4 (169-station scale-up) on the MacBook Pro.
  - Plan 046 Stream D (Mac-mini operational validation).
  - Nepal cutover in Oct 2026 — an operator cannot be expected to wait a day for onboarding to complete while the system is unavailable.
- The sequential loop is the bottleneck, not any single step. Parallelization is the natural fix; decoupling the long-running phase from the critical onboarding path is additive insurance.

### Observed behaviour (dress rehearsal 2026-04-21)

From `docs/deployment/dress-rehearsal-2026-04-21.md`:
- Onboard-stations wrote ~180 k `hindcast_forecasts` rows and ~19 M `hindcast_values` rows during its 38-minute run.
- 4 stations × 3 models = 12 artifacts; each artifact runs a full historical hindcast across ~45 years of daily forecasts (~16 k issue times per station-model). Sequential, one station/model at a time.
- Model-assignments were created but Murten (lake) was correctly skipped by M.2 compatibility — 3 model_assignments for Murten but 0 artifacts (F7; correct behaviour).

### Sibling plans

- **Plan 058 T2** flags `fetch_observations` as a synchronous per-station loop (~170 s for 170 stations) with a "stagger or parallelise" mitigation step for the ingest path. Same class of problem as this plan addresses for onboarding, but different flow. Keep scopes separate — if a third parallelization target emerges (`run-hindcast` Flow 7?), we extract a shared helper at that point. For now, each plan implements its own loop parallelization.
- **Plan 038** (store write atomicity) is a **hard prerequisite**: parallel writes without transactional wrappers risk orphan header rows on any concurrent failure. Cannot ship this plan before Plan 038 lands.
- **Plan 040** (hindcast dedup unique constraint) is a **hard prerequisite**: parallel historical-hindcast insertion could produce duplicates on retry, and without the unique constraint those duplicates silently land in the DB.
- **Plan 039 (DEFERRED — sensor/model failure visibility)**: overlapping concern is operator visibility into backfill progress. Do not drift into that territory — backfill emits structlog events per (station, model); richer UI surfaces are Plan 039's scope when Flow 4 is scoped.

### Principle

**Operational-readiness is gated on initial training, not on full historical-hindcast coverage.** A station can produce real-time forecasts the moment its models are trained. Skill scores are a retrospective property — nothing breaks operationally if they land hours later.

### Non-goals

- **Not** parallelizing `ingest-observations` — that's Plan 058 T2's job.
- **Not** parallelizing `run-hindcast` (Flow 7) — already supports per-station invocation.
- **Not** batch-parallelising `train_station_model` itself — stays sequential within each model's onboarding loop. Training is fast relative to historical-hindcast; the payoff isn't there.
- **Not** changing the skill-gate thresholds or the auto-promote vs pending-approval logic from v0-scope.md.

---

## Architecture decisions (draft)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Extract historical-hindcast + skill-gate phase** out of `onboard_model_flow` into a new standalone `backfill_hindcasts_flow` (provisionally Flow 7b; name to be confirmed vs architecture-context.md). | Cleanest separation; the extracted flow is independently testable and can be retriggered after corrections. |
| D2 | **`onboard-stations` marks stations `onboarding` → `operational` as soon as artifacts are trained**, regardless of backfill state. Real-time ingest, forecast-cycle, and alert-checking all start immediately. | Operational capability is not gated on historical skill. Skill scores simply appear asynchronously; downstream consumers tolerate their absence for the first run. |
| D3 | **`backfill_hindcasts_flow` uses Prefect `task.map`** over the (station × model) Cartesian product. Bounded concurrency via `DeploymentConfig.backfill_concurrency_limit` (default 4 — matches the host-resource ceiling observed during the 2026-04-21 rehearsal, where 4-way parallel `compute-skills` transient-OOM'd once on 16 GiB Docker Desktop). | `task.map` is the Prefect 3 idiom for fan-out. Concurrency limit prevents host-resource thrashing (finding F6). Default 4 is safe on Mac-mini-class hardware; production can raise. |
| D4 | **Backfill is idempotent.** Re-running the flow against the same (station, model) pair does not produce duplicates — relies on Plan 040's unique constraint to enforce at the DB level. | Operators should be able to re-trigger backfill safely after partial failure; the dedup constraint is the belt-and-braces. |
| D5 | **Runbook change (Plan 046)**: after `onboard-stations` completes, operator triggers `backfill-hindcasts` once per deployment. Wall-clock: minutes-to-hours; can run unattended. `compute-skills` moves in the A3 sequence to after backfill completes (skill scores need hindcast values). | Explicit two-step sequence is clearer than hidden background work. Aligns with how Plan 048 `backup-database` and Plan 058 `ingest-observations` scheduled flows are mentally modelled — each is a deployment the operator triggers (manually or on schedule). |
| D6 | **Skill-gate gating of auto-promote** stays at the initial-training step (M.3 / M.5), not at backfill. If initial training's synthetic-data smoke test (M.2b) passes, the artifact is auto-promoted to `ACTIVE`; backfill's per-day outputs feed `compute-skills` later but do NOT re-gate promotion. | Promotion semantics unchanged from v0-scope. Backfill is informational for skill scores, not a gate. |
| D7 | **Instrumentation**: backfill emits a per-(station, model) structured event with `duration_ms`, `issue_times_processed`, `issue_times_skipped`, `issue_times_failed`. | Operators need to know backfill progress without tailing logs. Event shape matches the `forecast.run_completed` convention. |

---

## Task sketch

- **T1** — **Extract `backfill_hindcasts_flow`** from `onboard_model_flow`'s historical-hindcast + skill-gate phase into a standalone flow in `flows/backfill_hindcasts.py`. Keep the existing per-(station, model) function bodies untouched; just move them. Unit-test the extracted flow's function signature.
- **T2** — **Parallelise with `task.map`.** Refactor the per-station loop to `task.map` over (station, model) tuples. Add `backfill_concurrency_limit` to `DeploymentConfig` (default 4). Unit-test concurrency bounds via a fake sleep task.
- **T3** — **Register deployment** for `backfill-hindcasts` in `cli/register_deployments.py`. No cron — operator-triggered for now (may add cadence later if needed for periodic re-backfill after QC corrections).
- **T4** — **Update `onboard-stations`** to skip the historical-hindcast + skill-gate phase. Stations transition to `operational` immediately after initial training succeeds. Remove the inline historical hindcast loop.
- **T5** — **Plan 046 runbook update.** Rev 12 (or similar) in Plan 046 §A3 re-orders: step 2 = onboard-stations (fast), step 2.5 = backfill-hindcasts (long), step 3 = ingest-observations, step 6 = compute-skills (must run after backfill completes so skill values exist). Exit gates adjusted; A4 unblocked.
- **T6** — **Integration test:** a scenario test that exercises the full onboard-stations → backfill-hindcasts → compute-skills path against 2 stations + 1 model. Asserts that onboard-stations completes in ≤ 30 s and that backfill completes with zero duplicate hindcast rows.
- **T7** — **Timing re-measurement.** Re-run A3 on dress-rehearsal setup; record wall-clock for onboard-stations (target < 60 s) and backfill-hindcasts (target < 3 h at 169 stations extrapolated, but measured at 5). Update dress-rehearsal-YYYY-MM-DD.md or commit a new report.

---

## Files to modify / create (sketch)

- New: `src/sapphire_flow/flows/backfill_hindcasts.py`.
- Modify: `src/sapphire_flow/flows/onboard_model.py` (remove extracted phase).
- Modify: `src/sapphire_flow/flows/onboard.py` (stations `operational` as soon as training succeeds).
- Modify: `src/sapphire_flow/cli/register_deployments.py` (register `backfill-hindcasts` deployment).
- Modify: `src/sapphire_flow/config/deployment.py` (`backfill_concurrency_limit`).
- New: `tests/unit/flows/test_backfill_hindcasts.py`.
- New / modify: integration test covering the new sequence.
- Modify: `docs/plans/046-mac-mini-staging-deployment.md` (Rev 12 — A3 step sequence update).
- Modify: `docs/architecture-context.md` (Flow 5 and 7 descriptions to reflect the split).

---

## Dependency graph (sketch)

```json
{
  "phases": [
    {"id": "extract", "tasks": ["T1"], "parallel": false},
    {"id": "parallelize", "tasks": ["T2"], "parallel": false, "depends_on": ["extract"]},
    {"id": "wire", "tasks": ["T3", "T4"], "parallel": true, "depends_on": ["parallelize"]},
    {"id": "integrate", "tasks": ["T5", "T6"], "parallel": true, "depends_on": ["wire"]},
    {"id": "validate", "tasks": ["T7"], "parallel": false, "depends_on": ["integrate"]}
  ]
}
```

---

## Exit gates (sketch)

1. `uv run pytest tests/unit/flows/test_backfill_hindcasts.py tests/unit/flows/test_onboard.py` green.
2. `uv run pyright src/` clean.
3. Integration test (T6): `onboard-stations` completes ≤ 30 s for 2 stations × 1 model on a fresh DB; `backfill-hindcasts` completes with zero duplicate rows in `hindcast_forecasts` (relies on Plan 040's unique constraint).
4. Re-run A3 dress rehearsal (T7): onboard-stations wall-clock ≤ 60 s for 5 stations.
5. Plan 046 Rev 12 lands with the updated A3 sequence; A4 is no longer blocked on this plan.
6. Version bump applied.

---

## Risks (sketch)

| Risk | Mitigation |
|---|---|
| Plan 038 and Plan 040 are both DRAFT — this plan cannot land until they do | Declare them as hard dependencies in frontmatter; this plan stays DRAFT until 038 and 040 are DONE. |
| Stations going `operational` without backfill may confuse downstream skill-score consumers | D6 keeps skill-gate at initial training; no operational consumer (forecast-cycle, alerts, API) depends on historical skill scores existing. Skill-score UI surfaces (if any) should show "pending backfill" gracefully — verify at integration-test time. |
| Bounded concurrency of 4 is too low for production (Nepal 169 stations × 3 models = 507 units) | Default is a safe-on-Mac-mini value; operators can raise via config. If production still blocks on this, extract a shared parallelism helper in a follow-up (potentially with Plan 058). |
| Plan 058's ingest parallelization converges on a shared abstraction with this plan's backfill parallelization | D-level note: each plan ships its own parallelization first. If a third target emerges (`run-hindcast`?), extract a shared helper then — not pre-emptively. |
| Backfill fails partway, leaving partial historical-hindcast state | Backfill is idempotent per D4; operator re-triggers; Plan 040's unique constraint de-dups. |
| Compute-skills was previously run as part of onboard-stations' inline pipeline; moving it later may surface latent assumptions about when skills are computed | T6 integration test covers the new sequence end-to-end. Any hidden coupling surfaces there before production. |
| `backfill_concurrency_limit` too high → Mac-mini OOM (F6 recurrence) | Default 4 matches dress-rehearsal safe level. Document F6 watch explicitly in the operator runbook. |

---

## Open questions (non-blocking DRAFT → READY)

1. Flow number — is "7b" the right slot given architecture-context.md's numbering, or should this be Flow 14 / something else? (Trivial; confirm at implementation.)
2. Should backfill be cron-scheduled for periodic re-run (e.g., nightly against newly-corrected QC), or strictly operator-triggered? (Recommendation: operator-triggered for v0; add schedule later if QC-correction cadence warrants it.)
3. Should the A3 runbook's `compute-skills` step (currently step 6) explicitly wait on `backfill-hindcasts` completion, or just assume ordering? (Recommendation: explicit wait, with a simple poll loop or Prefect-native dependency.)
4. Does this plan need to coordinate with Plan 066 (retrain strategy) on shared `TrainingDataAssembler` usage? (Recommendation: no direct coupling; but note in Plan 066 that backfill uses whatever forcing-source adapter initial training uses, keeping the two paths consistent.)
5. Is there a smaller first step — just the decoupling (D1+D2), no parallelization — worth shipping first to unblock A4? (Recommendation: yes, T1+T4+T5 alone cut onboard-stations from 38 min to a few seconds; T2+T3 for parallelization come after. Consider splitting into two sub-plans if the dependency on 038/040 drags.)
