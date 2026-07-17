---
status: DRAFT
created: 2026-07-17
plan: 122
title: Operational one-shot ops → Prefect flows/deployments; retire the redundant/loose CLI scripts
scope: Make the operational one-shot operations (weather-history backfill, forcing-reference validation) runnable on a deployed host the SAME way everything else operational runs — as registered Prefect deployments (in-image via `COPY src/`) — and retire scripts that merely duplicate an existing flow. Replaces the earlier "package loose scripts into the image" framing.
depends_on: []
supersedes_framing: "earlier 122 draft (scoped Dockerfile COPY of loose scripts)"
---

# Plan 122 — Operational one-shot ops → Prefect flows/deployments

## What changed since the first draft (and why)

The first draft framed the problem as *"the Dockerfile doesn't copy `scripts/`, so package the
loose scripts into the image."* A review pass then flagged that a scoped `COPY` allowlist is a
manually-maintained list that re-creates its own root cause, and the owner asked the sharper
question: **"we already have a Prefect onboarding flow — why do we need the scripts at all?"**

That question is correct and reframes the plan. **The system's operational mechanism is Prefect
deployments, not CLI scripts** — there are **11 registered deployments**
(`src/sapphire_flow/cli/register_deployments.py:50-111`: `ingest-observations`, `forecast-cycle`,
`backup-database`, `train-models`, `run-hindcast`, `compute-skills`, `compute-combined-skills`,
`onboard-stations`, `onboard-model`, `ingest-weather-history`, `collect-bafu-forecasts`), and they
are **all in the image automatically** because flows live under `src/sapphire_flow/flows/` and ride
the existing `COPY --from=builder /app/src /app/src` (`Dockerfile:63`). The loose `scripts/*.py`
are a *parallel* surface bolted alongside that mechanism.

**So the fix is not to package the scripts — it is to make the operational one-shot operations
Prefect flows/deployments like everything else, and retire the scripts that merely duplicate an
existing flow.** This *self-ships* (no Dockerfile allowlist to maintain — the "no future script
silently un-runnable" goal is met for free), is *observable* (Prefect logging/retries/UI, same as
the other 11), and is *consistent* with the architecture.

## Evidence

- **`onboard.py` is REDUNDANT with an existing flow.** `scripts/onboard.py` does not wrap the flow;
  it imports the **same** service the flow calls — `from sapphire_flow.services.onboarding import
  onboard_from_camelsch` (`scripts/onboard.py:42`) — with hand-rolled store wiring, while
  `onboard-stations` (`register_deployments.py:91-93` → `sapphire_flow.flows.onboard:onboard_stations_flow`)
  is the deployed path. For the deployed system the script adds nothing the flow lacks.
- **The 115b2 historical backfill and 115b3 validation were never wired as flows.** The backfill is
  `services/reanalysis_backfill.py` (`run_backfill`) + the `scripts/backfill_meteoswiss_history.py`
  CLI; the validation is `services/validation_gate.py` + `scripts/validate_forcing_reference.py`.
  Neither is a registered deployment — unlike the *scheduled* rolling ingest, which **is** a
  deployment (`ingest-weather-history`, `register_deployments.py:102-104`). So the one-shot ops are
  script-only by omission, not by design.
- **The gap this surfaced (2026-07-17):** running the 115b3 GO/NO-GO validation on the mac-mini
  staging host required **bind-mounting the host `scripts/`** into a one-off container
  (`-v ~/SAPPHIRE_flow/scripts:/app/scripts:ro`) because the Dockerfile copies `.venv`, `src/`,
  `alembic.ini`, `alembic/` — **not `scripts/`** (`Dockerfile:62-65`). A one-time expedient; the
  durable fix is that these ops become deployments (already in `src/`, no mount).

## Triage (classification rule + per-script decision)

**Rule:** an operation that is *run against the deployed system* (populates/reads the live DB, is a
host procedure or deploy gate) belongs in the **deployed operational surface = a Prefect
flow/deployment** (it then rides `COPY src/` automatically). A script is retired if a flow already
covers it. Genuinely dev-only tooling (plan/docs helpers, one-shot local asset recipes, e2e
harnesses) stays a loose `scripts/*.py`, **out of the image**.

| Script | Decision | Rationale |
|---|---|---|
| `onboard.py` | **RETIRE** | `onboard-stations` flow already calls the same `onboard_from_camelsch` service; the script is a duplicate. Fresh operational-test onboarding = trigger the deployment. |
| `backfill_meteoswiss_history.py` (115b2) | **→ flow + deployment** `backfill-meteoswiss-history` | Supervised batch that populates `historical_forcing`; a flow gives it in-image availability, Prefect observability, and a natural home for the 115b2/v0b `task.map` parallelisation. |
| `validate_forcing_reference.py` (115b3) | **→ flow + deployment** (FORK — see grill-me) | GO/NO-GO analysis over the live DB. Flow = consistent + observable; but it is a one-shot gate, so "keep as a supervised operator CLI that is nonetheless shipped" is a defensible alternative. |
| `plan100_forecast_feed_resilience.py` | **DECIDE — flow vs CLI (FORK)** | Interactive operator admin (`capture-snapshot`/`audit-priorities`/`reconcile-priorities`/…). Weaker flow case — some subcommands are interactive/one-off; may legitimately stay a shipped CLI. |
| `check_readiness.py` | DEV — leave loose | Reads plan-doc frontmatter (`scripts/check_readiness.py:2-8`); `docs/` is excluded from the image (`.dockerignore`), so its inputs aren't even present. Never a runtime concern. |
| `regenerate_icon_grid_asset.py` | DEV — leave loose | One-shot local recipe writing the committed repo asset `icon_ch2_eps_grid.npz`; workers are `read_only` so it can't run there anyway. |
| `063_e2e_verify.py` | DEV — leave loose | e2e/verification harness, not a host procedure. |

## Phases

### Phase 1 — the immediate operational gap (115b2/b3 ops become deployments)

- **1A — `backfill-meteoswiss-history` flow + deployment.** Wrap `services/reanalysis_backfill`'s
  `run_backfill` (+ the §2A binding step) in a `sapphire_flow.flows.backfill_meteoswiss_history`
  flow; register it in `register_deployments.py`. Preserve the existing behaviour (idempotent,
  resumable, `--bind-only`/batch-size become flow parameters). In-image automatically via `COPY src/`.
- **1B — `validate-forcing-reference` flow + deployment** (pending the 1D fork) — wrap
  `services/validation_gate`'s reference comparison; emit the per-basin GO/NO-GO report as the flow
  result / an artifact.
- **1C — retire `scripts/onboard.py`.** Delete it (or reduce to a one-line dev shim that triggers
  the deployment). Update the README onboarding section to say: deployed onboarding = the
  `onboard-stations` deployment; the local dev bootstrap = trigger it / a documented alternative.
  Verify nothing references `scripts/onboard.py` operationally (`git grep`).
- **1D — resolve the two forks** (validate flow-vs-CLI; plan100 flow-vs-CLI) — see grill-me.
- **1E — doc sync:** `docs/deployment/mac-mini-staging.md` (backfill + validation are now
  `docker compose run … prefect … deployment run` / triggered deployments, no bind-mount);
  `docs/standards/cicd.md` (operational ops are deployments shipped via `COPY src/`; loose `scripts/`
  are dev-only, not shipped — and correct the stale "runtime stage copies only `.venv`" line);
  `docs/spec/types-and-protocols.md` + `docs/touchpoint-maps.md` refs to the retired script.

### Phase 2 — (optional) sweep remaining loose operational scripts

Only if 1D classifies `plan100` (or anything else) as operational-flow: convert it the same way.
Otherwise this phase is empty and the plan closes at Phase 1.

## Grill-me (owner decisions before READY)

1. **`validate-forcing-reference`: flow, or a shipped one-shot operator CLI?** A flow is
   consistent/observable; but it is a run-once-per-cutover gate, so a supervised CLI (that we *do*
   ship, via the same in-`src/` route or a console entry point) is defensible. Which?
2. **`plan100_forecast_feed_resilience.py`: flow or keep-as-CLI?** Its subcommands are operator
   admin (some interactive/one-off). Convert, or ship it as a CLI?
3. **Retire vs. keep a dev shim for `onboard.py`?** Delete outright, or leave a one-line
   `scripts/onboard.py` that just runs the flow for muscle-memory/local dev?
4. **If any op is kept as a shipped CLI (not a flow):** how is it shipped — a `sapphire_flow.cli.*`
   console entry point (rides `COPY src/`, needs the `_REPO_ROOT`→`flows._db.run_migrations` fix for
   the migration-running scripts), *not* a loose-`scripts/` `COPY` allowlist? (The point of this plan
   is to stop shipping loose `scripts/`.)

## Tests

- **Flow behaviour parity:** `backfill-meteoswiss-history` flow reproduces the script's outcome —
  bind-before-backfill, idempotent/resumable, advancing per-source `MAX(valid_time)` (assert the
  effect, not `rows_stored`); `validate-forcing-reference` flow reproduces the gate verdicts
  (pass/flag/escalate incl. non-finite/degenerate → DATA_QUALITY_ESCALATE). Reuse the existing
  115b2/b3 service tests; add flow-level tests. *Soundness: each fails against the stated broken impl.*
- **Deployment registration:** the new deployments appear in `register_deployments` and register
  without error (extend the existing registration test).
- **Retirement:** `git grep -n "scripts/onboard.py"` returns no operational reference; onboarding
  still works via the `onboard-stations` deployment (existing flow test unaffected).
- **Deploy-gate (staging, the regression this kills):** on the built image with **no `scripts/`
  bind-mount**, the backfill + validation run via their deployments (`prefect deployment run …` /
  worker), and complete. *Must fail against the pre-Plan-122 image (ops absent / bind-mount required).*

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/            # ratchet vs baseline
uv run pytest
```
Plus the staging deploy-gate above (deployments run in-image, no bind-mount).

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Operational one-shot ops become deployments; retire onboard.py",
      "tasks": ["1A-backfill-flow", "1B-validate-flow", "1C-retire-onboard", "1D-resolve-forks", "1E-doc-sync"],
      "parallel": false,
      "task_depends_on": {
        "1B-validate-flow": ["1D-resolve-forks"],
        "1E-doc-sync": ["1A-backfill-flow", "1B-validate-flow", "1C-retire-onboard"]
      },
      "note": "1D (forks) gates 1B's shape; 1A + 1C independent; 1E after the code lands. All in-image via COPY src/ — no Dockerfile change.",
      "depends_on": []
    },
    {
      "id": "phase-2",
      "name": "(optional) convert any remaining operational script classified as a flow in 1D",
      "tasks": ["2A-convert-remaining"],
      "parallel": false,
      "depends_on": ["phase-1"]
    }
  ]
}
```

## Provenance

Reframed 2026-07-17 after the owner asked *"we have a Prefect onboarding flow — why do we need the
scripts?"* — which exposed that the first draft (scoped Dockerfile `COPY` of loose scripts) papered
over the real issue: operational one-shot ops should be Prefect deployments (the system's actual
in-image operational mechanism), and `onboard.py` is a redundant duplicate of the `onboard-stations`
flow. Surfaced while running the 115b3 staging GO/NO-GO validation, which needed a `scripts/`
bind-mount because the ops weren't in the image. DRAFT — plan-review (incl. independent Codex) +
owner decisions on the grill-me before READY.
