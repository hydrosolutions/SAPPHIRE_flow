# Plan 066 — Train-models retrain strategy (configurable)

**Status**: DRAFT
**Date**: 2026-04-21
**Depends on**: none at the code level. Informed by dress-rehearsal 2026-04-21 finding F3.
**Scope**: Make the `train-models` retrain flow's training-data assembly **configurable** so different retraining approaches can be experimented without code changes. The 2026-04-21 dress rehearsal showed the retrain flow assembling only post-onboarding observations (1 row per parameter) instead of the CAMELS-CH historical data that initial training uses — scheduled retraining therefore silently does nothing in production. The best retrain strategy is still under research; this plan delivers the framework + a sensible default, not a final answer.

---

## Context

### Why now

- A3 step 4 failed with "Not enough training rows: need at least 8 (lookback=7 + 1), got 1" despite 130 k historical observations in the DB (F3 in `docs/deployment/dress-rehearsal-2026-04-21.md`).
- Without a working retrain, Flow 9 (scheduled retraining) is a no-op in production. That erodes one of v0's operational commitments — "model skill tracked and artifacts refreshed on a schedule."
- The v0 target is Nepal Oct 2026; retraining cadence and window are active research topics for the ML lead. Hardcoding a single strategy now would lock the architecture before the research concludes.

### Observed behaviour (dress rehearsal 2026-04-21)

- `onboard-stations` (step 2) initial training: uses full CAMELS-CH historical forcing (130 k obs × 4 stations × 3 models → 12 active artifacts, all `status = active`).
- `train-models` (step 4) retrain: pulls 1 row per parameter per station from the DB, fails lookback check, no artifacts produced.

The two paths call `train_station_model()` with the same signature but assemble `data` via different code paths. Initial-training path goes through `onboard_model.py` + CAMELS-CH forcing source; retrain path goes through `flows/train_models.py` which evidently omits the historical window.

### Principle

One training-data-assembly abstraction, strategy-selectable. Initial training and scheduled retraining share it; the difference is the strategy argument, not two forks of similar code.

### Non-goals

- **Not** choosing the "right" retrain strategy. That is ongoing research. This plan ships the framework + at least two representative strategies.
- **Not** changing the initial-training path (M.3 inside `onboard-stations` / `onboard-model`) — it already works. If a future plan wants to unify initial and retrain under one assembler, that's fine, but out of scope here.
- **Not** touching Flow 9's scheduled cadence.
- **Not** introducing per-parameter or per-station strategy knobs. Per-model is the granularity; finer-grained tuning can come later.

---

## Architecture decisions (draft)

| # | Decision | Rationale |
|---|---|---|
| D1 | **`RetrainStrategy` enum** in `types/` with representative values: `HISTORICAL_PLUS_RECENT`, `RECENT_SLIDING_WINDOW`, `FULL_HISTORICAL_REBUILD`. Default: `HISTORICAL_PLUS_RECENT`. | Enum rather than Literal because the strategy set may grow; research-driven. Default matches initial-training semantics so A3 step 4 "just works" once the plan lands. |
| D2 | **Strategy wired via `DeploymentConfig.retrain_strategy`** (per-deployment scalar) with optional per-model override under a new `[[models.*.retrain]]` TOML section. | Per-deployment default keeps config simple; per-model override handles the case where the ML lead wants to try different strategies on different model types. |
| D3 | **Strategy dispatched in `services/training_data.py`**, via a `TrainingDataAssembler` protocol with one implementation per strategy. `train-models` flow selects the assembler based on config before calling `train_station_model`. | Protocol keeps the flow code unchanged; strategies become plug-in. Existing `train_station_model` signature is untouched. |
| D4 | **No change to initial training** — it uses the same assembler protocol but is hardcoded to `HISTORICAL_PLUS_RECENT` semantics (equivalent to what it does today). No behavioural regression. | Initial training is known-good; decoupling now would add risk without benefit. |
| D5 | **Unit tests assert per-strategy row-count** for a fixture DB with known observation/forcing layout — i.e. each strategy returns enough rows for the configured lookback, or documents explicitly why not (e.g., `RECENT_SLIDING_WINDOW` only works after enough polls). | Row-count is the load-bearing semantic; CRITICAL that A3 step 4 stops silently failing. |
| D6 | **When a strategy cannot assemble enough rows, the flow raises a clear per-model error** (not "got 1") — e.g., "`RECENT_SLIDING_WINDOW` needs ≥7 polls; this deployment has 1. Either switch strategy or wait." | Silent failures are what caused the dress-rehearsal confusion. Explicit error message + a pointer to the fix. |

---

## Task sketch

- **T1** — Add `RetrainStrategy` enum + `DeploymentConfig.retrain_strategy` field (default `HISTORICAL_PLUS_RECENT`). Unit tests for enum parsing + default.
- **T2** — Introduce `TrainingDataAssembler` protocol in `services/training_data.py`; implement `HistoricalPlusRecentAssembler` and `RecentSlidingWindowAssembler`. Unit tests per assembler with fixture observation/forcing data.
- **T3** — Wire `train-models` flow to select assembler based on config; replace the current in-line data gathering. Keep existing signature of `train_station_model`.
- **T4** — Implement `FullHistoricalRebuildAssembler` as a third representative strategy. (May be useful for post-QC-correction rebuilds.)
- **T5** — Optional per-model override in TOML + `DeploymentConfig` parsing. Unit tests for override precedence.
- **T6** — A3 integration validation: with default strategy, confirm retrain succeeds on dress-rehearsal-shape DB state (5 stations, 130 k historical obs, a handful of post-onboarding polls). Update Plan 046 Rev 11's F3 pointer to mark step 4 usable again.
- **T7** — Documentation: `docs/standards/` or `docs/design/` note describing the strategy framework; cicd.md or operator-facing doc on selecting the strategy.

---

## Files to modify / create (sketch)

- New: `src/sapphire_flow/types/retrain.py` — `RetrainStrategy` enum.
- Modify: `src/sapphire_flow/config/deployment.py` — `retrain_strategy` field.
- Modify / add: `src/sapphire_flow/services/training_data.py` — assembler protocol + implementations.
- Modify: `src/sapphire_flow/flows/train_models.py` — select assembler from config.
- New: `tests/unit/services/test_training_data_assemblers.py`.
- New: integration test in `tests/integration/` that exercises the default strategy.
- Docs: `docs/standards/cicd.md` or a new `docs/design/retrain-strategy.md`.

---

## Dependency graph (sketch)

```json
{
  "phases": [
    {"id": "types", "tasks": ["T1"], "parallel": false},
    {"id": "assemblers", "tasks": ["T2", "T4"], "parallel": true, "depends_on": ["types"]},
    {"id": "flow-wiring", "tasks": ["T3"], "parallel": false, "depends_on": ["assemblers"]},
    {"id": "override-and-tests", "tasks": ["T5", "T6"], "parallel": true, "depends_on": ["flow-wiring"]},
    {"id": "docs", "tasks": ["T7"], "parallel": false, "depends_on": ["override-and-tests"]}
  ]
}
```

---

## Exit gates (sketch)

1. `uv run pytest tests/unit/services/test_training_data_assemblers.py tests/unit/config/ tests/unit/flows/test_train_models.py` green.
2. `uv run pyright src/` clean.
3. Against a fixture DB matching dress-rehearsal state, `train-models` with default strategy produces ≥1 new artifact per (station, model). No "got 1" errors.
4. `docs/plans/046-*.md` §A3 step 4 reinstated (remove F3 skippable note) and cross-referenced to this plan's DONE commit.

---

## Risks (sketch)

| Risk | Mitigation |
|---|---|
| The "right" retrain strategy is still under research; default may change | D1 documents the default as a starting point. Switching default later is a one-line config change. |
| Strategy proliferation clutters the enum | Keep enum to 3 values; any fourth strategy requires explicit plan-level justification. |
| Per-model override TOML grows complex | T5 is marked optional in the task list; ship default-only if the override complexity doesn't pull its weight. |
| Unit tests depend on fixture shape; DB schema churn | Fixture lives in `tests/fixtures/reference/`; update in lockstep with any schema migration. |

---

## Open questions (non-blocking DRAFT → READY)

1. Does the assembler need QC-filtered observations, or raw rows? (Recommendation: QC-filtered by default, raw as an opt-in for research.)
2. How should `RECENT_SLIDING_WINDOW` be sized — time-based (last N days) or row-based (last N observations)? (Recommendation: both, via two sub-parameters — ML lead decides default.)
3. Should initial training also be re-routed through the assembler protocol, or keep its current path? (Recommendation: re-route in a follow-up plan once this one lands, not now.)
4. What's the right place to host the operator-facing doc — cicd.md, a new standards file, or a retrain-specific design note?
