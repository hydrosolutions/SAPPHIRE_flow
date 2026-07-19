---
status: READY
created: 2026-07-17
plan: 122
title: Operational one-shot ops → in-image surface (backfill deployment + validate CLI); retire the duplicate onboard.py script
scope: Make the operational one-shot operations (weather-history backfill, forcing-reference validation) runnable on a deployed host the SAME way everything else operational runs — in-image via `COPY src/`: the backfill as a registered Prefect deployment, the exit-code-gated validation as a `sapphire_flow.cli.*` console entry point (a flow cannot preserve its GO/NO-GO exit contract) — and retire scripts that merely duplicate an existing flow. Replaces the earlier "package loose scripts into the image" framing.
depends_on: []
supersedes_framing: "earlier 122 draft (scoped Dockerfile COPY of loose scripts)"
---

# Plan 122 — Operational one-shot ops → in-image surface (backfill deployment + validate CLI); retire onboard.py

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
the existing `COPY --from=builder --chown=app:app /app/src /app/src` (`Dockerfile:76`). The loose `scripts/*.py`
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
  `alembic.ini`, `alembic/` — **not `scripts/`** (`Dockerfile:75-78`). A one-time expedient; the
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
| `validate_forcing_reference.py` (115b3) | **→ shipped one-shot CLI (console entry point), NOT a flow** (fork #1 resolved — see below) | GO/NO-GO gate whose contract is a **machine-checkable exit code** (`scripts/validate_forcing_reference.py:31-36`, `:255`: exit 0 only on non-empty all-PASS; exit 1 on any FLAG/ESCALATE/DATA_QUALITY_ESCALATE/empty). It is *designed to be scripted from a shell/CI step*. A Prefect deployment cannot preserve that without a new synchronous poll-and-map-to-exit-code mechanism — every `prefect deployment run` in this repo is fire-and-forget (no `--watch`, no exit-code check; `docs/deployment-quickstart.md:91,123`, `docs/plans/091-macmini-nwp-on-data-collection.md:360,404`), and the one blocking mechanism (`run_deployment()`, `docs/standards/orchestration.md:134`) is a flow-to-flow parent/child pattern, not an operator-shell gate. Ship it in-image as a `sapphire_flow.cli.*` console entry point (rides `COPY src/`) preserving the nonzero exit — no loose-`scripts/` `COPY`. |
| `plan100_forecast_feed_resilience.py` | **LEAVE LOOSE for now; NOT a flow; CLI-ification DEFERRED — Plan 122 does not touch it** (owner-deferred 2026-07-19) | Operator admin / destructive one-shot with explicit confirmation flags (`capture-snapshot` / `audit-priorities` / `reconcile-priorities --apply --backup-reference --maintenance-mode-confirmed`; `scripts/plan100_forecast_feed_resilience.py:355,371`, `:227`). Not a scheduled/observable pipeline step and gated on operator-supplied confirmation flags after pausing writers — a weak flow case. It stays a loose `scripts/*.py` here; whether to move it behind a `sapphire_flow.cli.*` console entry point is a **separate follow-up out of this plan's scope**. Plan 122 neither ships nor moves it. |
| `check_readiness.py` | DEV — leave loose | Reads plan-doc frontmatter (`scripts/check_readiness.py:2-8`); `docs/` is excluded from the image (`.dockerignore`), so its inputs aren't even present. Never a runtime concern. |
| `regenerate_icon_grid_asset.py` | DEV — leave loose | One-shot local recipe writing the committed repo asset `icon_ch2_eps_grid.npz`; workers are `read_only` so it can't run there anyway. |
| `063_e2e_verify.py` | DEV — leave loose | e2e/verification harness, not a host procedure. |

## Phases

### Phase 1 — the immediate operational gap (115b2 → deployment; 115b3 → shipped in-image CLI)

The one open fork (validate: flow-vs-CLI) is **resolved in the triage table above before
implementation begins** — validate ships as an in-image `sapphire_flow.cli.*` console entry point,
not a flow and not a loose script (see the grill-me section, which records the author resolution +
evidence for owner sign-off, and no longer gates the task graph's *shape*). `plan100` is **left
untouched by this plan** (owner-deferred: stays a loose `scripts/*.py` for now; NOT a flow;
CLI-ification is a separate follow-up). Implementation tasks:

- **1A — `backfill-meteoswiss-history` flow + deployment.** Wrap `services/reanalysis_backfill`'s
  `run_backfill` (+ the §2A binding step) in a `sapphire_flow.flows.backfill_meteoswiss_history`
  flow; register it in `register_deployments.py`. Preserve the existing behaviour (idempotent,
  resumable, `--bind-only`/batch-size become flow parameters). In-image automatically via `COPY src/`.
  Register the `DeploymentSpec` with **serialized concurrency** (`concurrency_limit=1`) — it is a heavy
  batch, matching how every other heavy deployment is registered (`forecast-cycle`, `train-models`,
  `onboard-model`, `ingest-weather-history`, `collect-bafu-forecasts` all set `concurrency_limit=1`;
  `register_deployments.py:61,73,99,106,113`). *(Backfill is a supervised batch with no exit-code gate
  contract — a flow is the right fit.)*
- **1B — `validate-forcing-reference` in-image CLI (console entry point).** Move the existing
  `scripts/validate_forcing_reference.py` logic behind a `sapphire_flow.cli.*` entry point that rides
  `COPY src/` (so it is in-image without a `scripts/` bind-mount) **and preserves the machine-checkable
  GO/NO-GO exit-code contract verbatim** (`scripts/validate_forcing_reference.py:31-36`, `:255`: exit 0
  only on a non-empty all-PASS reference comparison; exit 1 on any FLAG/ESCALATE/DATA_QUALITY_ESCALATE
  basin, or zero stations/result rows; the 4C/4D live-tail is diagnostic-only). If a migration-running
  path is needed it uses the `flows._db.run_migrations` fix, not a loose-`scripts/` `COPY`.
  **Contract-preservation is a hard acceptance criterion** — see the Tests section's per-state gate.
  Moving the logic off `scripts/validate_forcing_reference.py` **breaks its script-test**
  (`tests/unit/scripts/test_validate_forcing_reference_script.py:21` loads the script file directly via
  `importlib` `spec_from_file_location`) — **rewrite that test module in this same task** to target the
  new `sapphire_flow.cli.*` console entry point (drive the CLI and assert its exit code), or remove it
  as superseded by the new CLI exit-code tests (mirrors how 1C handles `test_onboard_script.py`).
- **1C — retire `scripts/onboard.py` (guarded by a flow-parity precondition).** Two sub-steps, in
  order:
  1. **Close the parity gap first.** `scripts/onboard.py` has a `--skip-meteoswiss-backfill` branch
     (`scripts/onboard.py:153,308`) that **writes the §2B binding but skips the fetch while still
     holding the eligible stations out of promotion** — the deployed flow has no JSON-serializable
     equivalent (it always builds the adapter on the production path,
     `src/sapphire_flow/flows/onboard.py:152,173`, and unconditionally sets the hold-gate
     `require_meteoswiss_backfill = True`, `src/sapphire_flow/flows/onboard.py:174`). Add a
     `skip_meteoswiss_backfill: bool = False` parameter to `onboard_stations_flow` that writes the
     binding + holds stations but skips the fetch, so the flow covers that branch before the script is
     removed.
  2. **Then delete `scripts/onboard.py`** (owner-confirmed 2026-07-19: delete outright, no dev shim).
     This **breaks `tests/unit/scripts/test_onboard_script.py`**, which imports the file directly
     (`tests/unit/scripts/test_onboard_script.py:10,15`) — **remove that test module** in this same
     task. Update the README onboarding section: deployed onboarding = the `onboard-stations`
     deployment; local dev bootstrap = trigger it / a documented alternative. Verify nothing references
     `scripts/onboard.py` operationally (`git grep`).
- **1D — doc sync.** Two categories: the new deployment's orchestration docs, and the full
  `scripts/onboard.py` retirement sweep.
  - **New-deployment / staging docs:** `docs/deployment/mac-mini-staging.md` (backfill is now a
    triggered deployment, validation is now the in-image CLI — no `scripts/` bind-mount);
    `docs/standards/cicd.md` and `docs/v0-scope.md` (correct the stale "runtime stage copies only
    `.venv`" claim — `docs/v0-scope.md:203` says "the runtime image copies only the compiled `.venv`",
    but `Dockerfile:75-78` copies **both** `.venv` **and** `src/`; also: operational ops are
    deployments/CLIs shipped via `COPY src/`, loose `scripts/` are dev-only, not shipped).
  - **Orchestration doc (adding a Prefect deployment requires it):** `docs/standards/orchestration.md`
    — (a) add a `backfill-meteoswiss-history` row to the flow→deployment mapping table (the
    `onboard.py` mapping rows sit at `docs/standards/orchestration.md:313-314`; the new backfill flow
    needs its own `@flow` mapping row + run-name template); (b) satisfy the run-name **Coverage rule**
    (`docs/standards/orchestration.md:318-320`: "Every run-name template must be covered by
    `tests/unit/flows/test_run_names.py`") — the new flow's run-name template must be covered there.
  - **`scripts/onboard.py` retirement sweep (deleting the script — reconcile EVERY non-archive hit).**
    Run `git grep -n "scripts/onboard.py"` (and `git grep -n "onboard.py"` as a backstop) and reconcile
    every non-archive hit, docs **and** the one code comment:
    - `README.md:129` and `README.md:138` — the `uv run python scripts/onboard.py --download[…]`
      bootstrap commands: replace with the deployed `onboard-stations` path / documented dev bootstrap.
    - `docs/architecture-context.md:651` — "The current `scripts/onboard.py --download` effectively
      performs steps 0.2 + 0.3…": reword to the deployment.
    - `docs/design/v0-flow2-observation-pipeline.md:464` — "a CLI script (`scripts/onboard.py`) invokes
      `onboard_from_camelsch()`": reword.
    - `docs/spec/config-reference.toml:224` — the comment "Read by scripts/onboard.py and
      flows/onboard.py" (line is **:224**, not the `:215` an earlier draft cited): drop the retired-script
      reference, keep `flows/onboard.py`.
    - `src/sapphire_flow/services/onboarding.py:441` — a **code comment** ("production onboarding always
      supplies one via scripts/onboard.py + the deployed flow"): update the comment text (no logic
      change).
    - Explicit instruction: **sweep `git grep -n 'scripts/onboard.py'` and reconcile every non-archive
      hit (docs + the code comment).** `docs/plans/archive/**` and other plan docs (058/115a — historical
      records) are left as-is. *(Dropped `docs/spec/types-and-protocols.md` + `docs/touchpoint-maps.md` —
      reviewer verified neither references the script; add a file back only if a future `git grep` hit
      appears.)*

### Phase 2 — none

After the fork resolution there is no remaining operational script to convert in this plan: the
backfill becomes a deployment (1A), validate becomes an in-image CLI (1B), and `onboard.py` is
deleted as a duplicate (1C). `plan100_forecast_feed_resilience.py` is **left untouched** (owner-deferred);
moving it behind a `sapphire_flow.cli.*` console entry point is a separate follow-up, out of scope
here. No flow conversion is pending. **The plan closes at Phase 1.**

## Grill-me (owner-confirmed 2026-07-19)

The forks were author-resolved on the evidence below and **owner-confirmed 2026-07-19**; recorded
here as the settled resolutions.

1. **`validate-forcing-reference`: RESOLVED → shipped in-image CLI (console entry point), not a flow.**
   *Deciding fact:* the script's contract is a machine-checkable exit code consumed by a shell/CI gate
   (`scripts/validate_forcing_reference.py:31-36`, `:255`). A Prefect deployment cannot surface PASS
   vs FLAG/ESCALATE synchronously — every `prefect deployment run` in this repo is fire-and-forget
   (`docs/deployment-quickstart.md:91,123`; `docs/plans/091-macmini-nwp-on-data-collection.md:360,404`),
   and the only blocking primitive (`run_deployment()`, `docs/standards/orchestration.md:134`) is a
   flow-to-flow parent/child pattern, not an operator-shell gate. A flow would force *either* a new
   poll-and-map-to-exit-code wrapper *or* raising on a FLAG verdict (a departure from how every other
   flow treats its business-logic outcomes). The CLI preserves the contract with zero new machinery.
   **Owner CONFIRMED (2026-07-19): CLI, not flow** — its actual use is an occasional cutover/onboarding
   gate (ran once, 2026-07-17), not a scheduled op. The validation logic stays a shared service
   (`services/validation_gate.py`), so a scheduled forcing-drift *monitoring* flow can be added later,
   calling the same service, without disturbing this gate (out of scope here).
2. **`plan100_forecast_feed_resilience.py`: DEFERRED → left untouched by this plan.** It is clearly
   **not a flow** (operator admin / destructive one-shot gated on operator-supplied confirmation flags
   `--apply --backup-reference --maintenance-mode-confirmed`, `scripts/plan100_forecast_feed_resilience.py:227,355,371`
   — not a scheduled/observable pipeline step). **Owner DEFERRED (2026-07-19): it stays a loose
   `scripts/*.py` for now; NOT a flow; its CLI-ification is a separate follow-up, out of scope. Plan 122
   does not touch, ship, or move `plan100`.**
3. **`onboard.py`: RESOLVED → DELETE outright (owner-confirmed 2026-07-19, no dev shim), after 1C's
   parity precondition.** The `--skip-meteoswiss-backfill` branch is preserved by the new flow
   parameter (1C step 1) and the direct-import test is removed (1C step 2). **Owner CONFIRMED
   (2026-07-19): delete outright.**
4. **Shipping mechanism for the CLIs: `sapphire_flow.cli.*` console entry points that ride `COPY src/`
   (needs the `flows._db.run_migrations` fix for any migration-running path), NOT a loose-`scripts/`
   `COPY` allowlist.** The whole point of this plan is to stop shipping loose `scripts/`. Settled.

## Tests

- **Backfill flow parity:** `backfill-meteoswiss-history` flow reproduces the script's outcome —
  bind-before-backfill, idempotent/resumable, advancing per-source `MAX(valid_time)` (assert the
  effect, not `rows_stored`). Reuse the existing 115b2 service tests; add flow-level tests.
  *Soundness: fails against the stated broken impl.*
- **Validate CLI — GO/NO-GO exit-code contract (hard gate).** The in-image `validate-forcing-reference`
  CLI must **preserve the exit-code semantics verbatim** (`scripts/validate_forcing_reference.py:31-36`,
  `:255`). Add a test per verdict state that asserts the process exit code:
  - non-empty **all-PASS** reference comparison → **exit 0**;
  - any **FLAGGED** basin → exit 1;
  - any **ESCALATED** basin → exit 1;
  - any **DATA_QUALITY_ESCALATE** (non-finite/degenerate) basin → exit 1;
  - **zero stations / zero result rows** (vacuous "all pass") → exit 1;
  - the 4C/4D live-tail residual never changes the exit code on its own (diagnostic-only).
  *Soundness: each exit-1 case must fail (return 0) against an impl that only emits a report without
  mapping the verdict to a nonzero exit.* Reuse the existing 115b3 `services/validation_gate` tests
  for the verdict maths; the new tests cover the CLI's exit-code mapping. **The existing script-test
  `tests/unit/scripts/test_validate_forcing_reference_script.py` (which loads the script file directly
  via `importlib` at `:21`) is rewritten to target the CLI entry point, or removed as superseded by
  these CLI exit-code tests** (mirrors the `test_onboard_script.py` cleanup in Retirement).
- **onboard flow parity (`skip_meteoswiss_backfill`):** with `skip_meteoswiss_backfill=True` the flow
  writes the §2B binding and holds eligible stations out of promotion but performs **no** fetch —
  matching `scripts/onboard.py:308`; with the default (`False`) it fetches as today. *Soundness: the
  skip-true test fails against a flow that still fetches or that promotes the held stations.*
- **Deployment registration:** the `backfill-meteoswiss-history` deployment appears in
  `register_deployments` and registers without error, with `concurrency_limit=1`. Extend the existing
  registration test — the hard-coded spec count goes **11 → 12** (`tests/unit/cli/test_register_deployments.py:98`
  `assert len(specs) == 11`), and `backfill-meteoswiss-history` must be added to the `DEPLOYMENT_NAMES`
  set the test compares against (`:99`); add a `concurrency_limit == 1` assertion alongside the existing
  heavy-deployment checks (`:61-64`). *(The validate CLI is a console entry point, not a deployment — no
  registration entry; `plan100` is untouched by this plan.)*
- **Retirement:** `git grep -n "scripts/onboard.py"` returns no operational reference;
  `tests/unit/scripts/test_onboard_script.py` is removed or rewritten (it imports the deleted file
  directly, `:10,:15`); onboarding still works via the `onboard-stations` deployment.
- **Deploy-gate (staging, the regression this kills):** on the built image with **no `scripts/`
  bind-mount**, the backfill runs via its deployment (`prefect deployment run …` / worker) and the
  validation runs via its in-image CLI console entry point (`docker compose run … validate-forcing-reference`)
  and **returns its GO/NO-GO exit code** to the operator shell. *Must fail against the pre-Plan-122
  image (ops absent / `scripts/` bind-mount required).*

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
      "name": "115b2 backfill → deployment; 115b3 validate → in-image CLI; retire onboard.py",
      "tasks": ["1A-backfill-flow", "1B-validate-cli", "1C-retire-onboard", "1D-doc-sync"],
      "parallel": false,
      "task_depends_on": {
        "1D-doc-sync": ["1A-backfill-flow", "1B-validate-cli", "1C-retire-onboard"]
      },
      "note": "Forks are resolved in the triage table BEFORE implementation (no in-graph resolve-forks task). 1A + 1B + 1C independent; 1D after the code lands. All in-image via COPY src/ — no Dockerfile change. 1B ships the validate console entry point preserving the exit-code GO/NO-GO contract; 1C adds skip_meteoswiss_backfill to the flow, then deletes the script + its direct-import test.",
      "depends_on": []
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
bind-mount because the ops weren't in the image. Grill-me forks **owner-confirmed 2026-07-19**
(validate → CLI; plan100 deferred/untouched; onboard.py → delete outright). A confirming independent
Codex review (2026-07-19) verified the core technical claims and caught six completeness gaps — the
incomplete onboard.py retirement sweep, the validate/onboard script-test retargets, the registration
count (11→12), the orchestration-doc row, and stale citations — **all folded**. **READY (owner,
2026-07-19)** — implementation authorised; hold at PR; code goes through the `implement` workflow.
