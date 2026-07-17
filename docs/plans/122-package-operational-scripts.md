---
status: DRAFT
created: 2026-07-17
plan: 122
title: Ship operational scripts in the deployed image (scoped Dockerfile COPY; optional cli/ promotion)
scope: Operational one-off scripts must be runnable on a deployed host. Today several are loose `scripts/*.py` the Dockerfile does not copy, so they only run via a bind-mount workaround.
depends_on: []
---

# Plan 122 — Ship operational scripts in the deployed image

## The gap (evidenced on staging 2026-07-17)

Running the 115b3 GO/NO-GO validation on the mac-mini staging host exposed a real
deployment defect: **the Dockerfile does not copy `scripts/` into the image.** The
runtime stage copies `.venv`, `src/` (hence everything under `sapphire_flow/`),
`alembic.ini`, and `alembic/` — and **nothing else** (`Dockerfile:62-65` — `.venv` at 62,
`src` at 63, `alembic.ini` at 64, `alembic/` at 65). So a loose
`scripts/*.py` file is simply **absent** from the deployed image:

```
$ docker compose run --rm prefect-worker python scripts/backfill_meteoswiss_history.py --help
python: can't open file '/app/scripts/backfill_meteoswiss_history.py': [Errno 2] No such file or directory
```

The 115 staging validation only proceeded by **bind-mounting the host repo's `scripts/`**
into a one-off container (`-v ~/SAPPHIRE_flow/scripts:/app/scripts:ro`) — an ad-hoc,
undocumented mount, not a committed operational path.

### Correction: `config.toml` is bind-mounted, not baked (precedent matters)

An earlier draft of this plan claimed the Dockerfile also copies `config.toml`. **It does
not** — `grep -n config Dockerfile` is empty; the runtime COPY block is exactly `.venv`,
`src`, `alembic.ini`, `alembic` (`Dockerfile:62-65`). `config.toml` is never baked into the
image; it is **bind-mounted at run time** by compose (`docker-compose.yml:110,159,204,283`,
`- ./config.toml:/app/config.toml:ro`) with `SAPPHIRE_CONFIG=/app/config.toml`
(`docker-compose.yml:81,132`). This matters because it means the repo already has **two**
supported, git-tracked mechanisms for getting a file into the running container: bake via
`COPY` (used for code/migrations) **and** a committed compose bind-mount (used for
`config.toml`). Neither is a "hack." The design question below is therefore *which
supported mechanism* fits operational scripts — not "workaround vs. supported."

### Current split — loose vs packaged

| Location | In the image? | Files |
|---|---|---|
| `scripts/*.py` (loose) | **NO** (not copied) | `onboard.py`, `backfill_meteoswiss_history.py`, `validate_forcing_reference.py`, `plan100_forecast_feed_resilience.py`, `check_readiness.py`, `regenerate_icon_grid_asset.py`, `063_e2e_verify.py` |
| `src/sapphire_flow/cli/*.py` (packaged) | **YES** (via `COPY src/`) | `check.py`, `register_deployments.py` |
| `[project.scripts]` console entry points | — | only `check = "sapphire_flow.cli.check:main"` (`pyproject.toml:49-50`) |

Note on the "established pattern": only `check` is wired as a console entry point.
`register_deployments.py` has a `main()` (`src/sapphire_flow/cli/register_deployments.py:179`)
but is **not** registered — it runs only via `python -m sapphire_flow.cli.register_deployments`.
So the `cli/` + `[project.scripts]` precedent is only **half-realized** today; do not
overestimate how proven the full end-state is.

### Why it matters (beyond 115)

- **`backfill_meteoswiss_history.py` (115b2)** and **`validate_forcing_reference.py`
  (115b3)** are the immediate, *evidenced* blockers: run against the deployed image on the
  mini and failed (`No such file`); only the ad-hoc bind-mount got the b1→b4 gate through.
- **`plan100_forecast_feed_resilience.py`** is *also* run against the deployed worker
  environment per the runbook (`docs/deployment/mac-mini-staging.md:333-334`: "Run Plan 100
  administration checks from the worker environment … before and after priority
  reconciliation"). It has real operational subcommands — `capture-snapshot`,
  `audit-priorities`, `reconcile-priorities`, `audit-floor`, `audit-forecast-alerts`
  (`scripts/plan100_forecast_feed_resilience.py:359,365,371,380,384`). **It is operational
  and equally absent from the image** — the earlier draft misclassified it as dev-only.
- Any FUTURE plan that ships an operational script inherits this bug silently — a loose
  script passes CI (tests import it fine from the source tree) yet is un-runnable in prod.

## Classification rule (checkable — replaces the old "confirm at plan-review" hedge)

> A script is **OPERATIONAL** iff it is invoked **against the running deployed image** as
> part of a documented host procedure (a runbook step, a deploy gate, or an onboarding step
> that touches the live DB), i.e. it must run **without a source checkout present**.
> Everything else — plan/docs tooling, one-shot local asset regeneration, e2e/dev harnesses —
> is **DEV** and stays loose and out of the image.

Applying the rule (each row cites the evidence):

| Script | Class | Evidence | In scope? |
|---|---|---|---|
| `backfill_meteoswiss_history.py` | OPERATIONAL | ran vs deployed image on mini (115b2); failed absent | **YES** |
| `validate_forcing_reference.py` | OPERATIONAL | 115b3 GO/NO-GO ran vs deployed image; failed absent | **YES** |
| `plan100_forecast_feed_resilience.py` | OPERATIONAL | runbook: run from worker environment (`mac-mini-staging.md:333`) | **YES** |
| `onboard.py` | OPERATIONAL (owner decision 2026-07-17) | The *deployed* onboarding path is the registered Prefect deployment `onboard-stations` → `sapphire_flow.flows.onboard` (`register_deployments.py:91-93`), which is **already in-image** via `COPY src/` — so `scripts/onboard.py` is a **CLI convenience wrapper** (the README `--download` CAMELS bootstrap for a *fresh* setup, `README.md:121-138`), not a hard blocker. **Owner still wants it shipped:** they will re-run a fresh onboarding at least once for operational testing, and want a **complete, distributable package**. | **YES** |
| `check_readiness.py` | DEV (docs tooling) | reads plan-doc frontmatter (`scripts/check_readiness.py:2-8`); docs are excluded from the image (`.dockerignore:9` `docs/`), so its inputs are not even present; never DB/API/runtime | **NO** |
| `regenerate_icon_grid_asset.py` | DEV (one-shot local recipe) | "One-shot regeneration recipe" writing the committed repo asset `src/sapphire_flow/data/icon_ch2_eps_grid.npz` (`scripts/regenerate_icon_grid_asset.py:1-6`); run once from a checkout before committing; worker containers are `read_only: true` (`docker-compose.yml:99,150`) so it *cannot* write there anyway | **NO** |
| `063_e2e_verify.py` | DEV (verification harness) | e2e harness, not a host procedure | **NO** |

**Result of re-triage vs. the earlier draft:** `plan100_…` moves DEV→OPERATIONAL (in
scope); `check_readiness.py` and `regenerate_icon_grid_asset.py` move OPERATIONAL→DEV
(explicitly **out of scope** — not a deployment concern, not "leave loose for now");
`onboard.py` is **now confirmed IN scope** (owner decision 2026-07-17 — see the row above:
the deployed onboarding *flow* is already in-image, but the owner wants the `onboard.py`
CLI shipped too for fresh operational-test setups and a complete distributable package).

## Options

- **(A) `COPY scripts/ scripts/` (blanket).** One line, but ships the dev/verification
  harnesses (`063_e2e_verify.py`) into the production image. Rejected: no reason to ship dev
  code to prod.
- **(B) Permanent compose bind-mount `- ./scripts:/app/scripts:ro`** (the same mechanism
  used for `config.toml`). Supported and committed — but scripts are **code**, and a
  host-checkout bind-mount reintroduces exactly the host/image **version-skew** that baking
  `src/` into the image exists to prevent (the running container would execute whatever is in
  the host checkout, which can drift from the image's `src/`). `config.toml` is
  deployment-*config* (intended to vary per host, hence bind-mounted); operational scripts
  are *code* that must match the image. Rejected for that reason — but named explicitly so the
  precedent is not mischaracterized.
- **(C) Full promotion to `sapphire_flow.cli.*` console entry points** for every operational
  script. Most discoverable (`docker compose run --rm prefect-worker backfill-meteoswiss-history`),
  but the heaviest: it moves script logic into new modules (which **breaks the scripts'
  `_REPO_ROOT` path logic** — see Phase 2), retargets tests, and rewrites doc invocation
  forms. Disproportionate for scripts that may run only a handful of times (e.g. the 115b2
  one-off migration).
- **(D) Scoped Dockerfile `COPY` of exactly the operational script files** — **recommended
  baseline.** `WORKDIR /app` is pinned in both build stages (`Dockerfile:8,57`), so once the
  file is present, `docker compose run --rm prefect-worker python scripts/foo.py --help` is
  not WORKDIR-fragile; it simply lacks a bare command name. This bakes the operational
  scripts into the image (no version-skew, unlike B), ships **no** dev harnesses (unlike A),
  requires **no** code move / path-logic change / test retarget (unlike C), and keeps existing
  `python scripts/…` doc invocations valid.

**Recommendation: (D) as the Phase-1 defect fix.** The evidenced defect is "operational
scripts are absent from the image"; a scoped `COPY` fixes exactly that, minimally, and is a
committed/supported path (not a workaround) once merged. **Promotion to `cli/` (C) is a
separate, explicitly lower-priority Phase 2** justified *only* by bare-command
discoverability — not by the defect — and is left OPTIONAL pending owner decision. This
resolves the proportionality concern (no ~1200-line relocation for possibly-one-off scripts)
while still killing the regression.

## Phase 1 — scoped image packaging (the defect fix)

Ships the confirmed-operational scripts into the image via a scoped `COPY`, with a deploy
gate that proves they run with **no bind-mount**. No code, test, or doc-invocation changes.

- **1A — Add a scoped `COPY` to the Dockerfile runtime stage — copy from the build context,
  NOT `--from=builder`.** After the final `alembic/` COPY (`Dockerfile:65` — the runtime COPY
  block is lines 62-65, so this new line goes *after* line 65, not after `alembic.ini` at line
  64), add a plain-context COPY naming exactly the operational files:

  ```dockerfile
  COPY --chown=app:app scripts/backfill_meteoswiss_history.py scripts/validate_forcing_reference.py scripts/plan100_forecast_feed_resilience.py scripts/onboard.py scripts/
  ```

  (`scripts/onboard.py` is included per the confirmed owner decision — Task 1C). Do **not**
  copy `063_e2e_verify.py`, `check_readiness.py`, or `regenerate_icon_grid_asset.py`.
  - **Why context-copy, not `--from=builder` (correctness — a prior draft was unbuildable):**
    `COPY --from=builder` can only copy paths that already exist in the builder stage's
    filesystem, and the builder stage **never copies `scripts/`** — its only COPY set is
    `pyproject.toml uv.lock README.md` (`Dockerfile:17`), `src/` (`Dockerfile:22`),
    `alembic.ini` (`Dockerfile:23`), `alembic/` (`Dockerfile:24`). A `COPY --from=builder
    /app/scripts/...` would fail the build with `"/app/scripts/...": not found`. These scripts
    are plain interpreted Python needing **no build step** (unlike `.venv`, which requires
    `uv sync` with the build-essential/cmake/libgeos-dev toolchain), so copy them straight from
    the build context — exactly the pattern already used for `COPY docker/entrypoint.sh
    /entrypoint.sh` at `Dockerfile:59` (no `--from=builder`). This is one fewer cross-stage hop
    and requires **no** builder-stage edit. `.dockerignore` does **not** exclude `scripts/`
    (`.dockerignore` excludes `tests/` at line 8 and `docs/` at line 9, not `scripts/`), so the
    context copy resolves.
  - *Verify:* `grep -n "scripts/" Dockerfile` shows exactly the operational files on a single
    context `COPY` (no `--from=builder`); the three DEV scripts are absent.
- **1B — Deploy-gate smoke matrix (the regression this plan kills).** Build the image and run
  `--help` for each shipped script inside a one-off container with **no `scripts/`-source
  bind-mount** (the point is that the baked scripts run without `-v ~/…/scripts:/app/scripts`;
  the normal committed config bind-mount `- ./config.toml:/app/config.toml:ro` from
  `docker-compose.yml:110` may remain — this gate is specifically about not needing a
  *source/scripts* mount):
  `docker compose run --rm --no-deps prefect-worker python scripts/<name>.py --help` exits 0.
  For `plan100_forecast_feed_resilience.py`, cover **each subcommand** `--help`:
  `capture-snapshot`, `audit-priorities`, `reconcile-priorities`, `audit-floor`,
  `audit-forecast-alerts` (`scripts/plan100_forecast_feed_resilience.py:359-384`).
  - *Verify:* every listed invocation exits 0 against a freshly built image with **no
    `scripts/` `-v` mount**; the same commands fail (`No such file`) against the pre-Plan-122
    image (proves the gate is real).
- **1C — `onboard.py` classification: RESOLVED (owner, 2026-07-17) → IN scope.** Investigation:
  the *deployed* onboarding path is the registered Prefect deployment `onboard-stations`
  (`register_deployments.py:91-93`, flow `sapphire_flow.flows.onboard`), already in-image via
  `COPY src/` — nothing on the mini invokes `scripts/onboard.py` directly (no launchd/cron/
  runbook step; the two LaunchAgents are `ch.hydrosolutions.sapphire`(.watchdog)). So the CLI
  is **convenience, not a blocker**. The owner nonetheless wants it shipped (fresh
  operational-test setups + a complete distributable package), so it **is** in the 1A `COPY`
  set and the 1B smoke matrix.
  - *Verify:* the `COPY` set includes `scripts/onboard.py`; the 1B matrix runs `onboard.py
    --help` (and any subcommands) against the built image with no bind-mount.
- **1D — Doc sync (grep-driven, enumerated).** Phase 1 keeps the `python scripts/<name>.py`
  invocation form, so **no invocation strings change**; the doc work is limited to
  documenting the new guarantee. Add to `docs/standards/cicd.md`: *operational scripts named
  in the Dockerfile scoped `COPY` are baked into the image and runnable on the deployed host
  with no bind-mount; loose `scripts/` (dev/verification/local-recipe) are not shipped.*
  **Also correct the now-conflicting sentence** in the same file that reads "the runtime stage
  copies only `.venv` and remains slim" (`docs/standards/cicd.md:29`) — it must reflect that
  the runtime stage also bakes `src/`, `alembic.ini`, `alembic/`, and the scoped operational
  `scripts/` set (the "only `.venv`" wording was already inaccurate re `src`/`alembic` and this
  plan makes it more so). Add the deploy-gate smoke matrix to
  `docs/deployment/mac-mini-staging.md` next to the existing Plan 100 procedure
  (`mac-mini-staging.md:333`).
  - *Verify:* `grep -rn "scripts/.*bind\|/app/scripts" docs/deployment/mac-mini-staging.md`
    no longer implies a manual mount is required for the shipped scripts; `cicd.md` states the
    baked-vs-loose rule **and** no longer says the runtime stage "copies only `.venv`".

## Phase 2 — promote to `cli/` console entry points (owner-favoured for public distribution)

**Owner lean (2026-07-17): DESIRED, cost-permitting.** The owner wants a *complete,
distributable package* — a clean `onboard` / `backfill-meteoswiss-history` / `validate-forcing`
command set reads far better for public distribution than loose `python scripts/foo.py`. So
Phase 2 is no longer "elect only if you want discoverability"; it is the intended end-state
**if it is not disproportionate** (owner: "if it's not too much work"). It remains **strictly
additive over Phase 1** (the defect fix ships first, standalone) and carries the correctness
work the earlier draft omitted (the `_REPO_ROOT` fix in 2A). Sequence Phase 1 → confirm cost
of Phase 2 at that point → do Phase 2 unless the `_REPO_ROOT`/test/doc-retarget work proves
heavier than the distribution benefit warrants.

> **Citation-drift caveat (Phase 2 is gated, not scheduled).** The `file:line` citations
> below (especially the enumerated doc-invocation set in 2E) are accurate as of 2026-07-17 but
> will very likely drift before Phase 2 is ever elected, since Phase 1 and unrelated work will
> keep touching these files. **If Phase 2 is elected later, re-verify every `file:line` in
> this section with a fresh grep before implementing** — treat the numbers as pointers, not
> ground truth. (We keep the tasks broken out rather than collapsing to a prose "future
> option" because a reviewer requires an explicit Phase-2 test task for `plan100` and an
> explicit 2D deletion↔Dockerfile branch — both of which need task-level detail to be
> unambiguous. The trade-off is accepted line-number rot, mitigated by this re-verify rule.)

- **2A — Move each promoted script's logic into `src/sapphire_flow/cli/<name>.py`** with a
  `main()`, preserving the argparse surface verbatim.
  - **Path-logic fix (blocker):** `onboard.py` and `backfill_meteoswiss_history.py` compute
    `_REPO_ROOT = Path(__file__).resolve().parent.parent` (`scripts/onboard.py:57`,
    `scripts/backfill_meteoswiss_history.py:61`) — correct from `scripts/`, **wrong** from
    `src/sapphire_flow/cli/` (that resolves to `src/sapphire_flow/`, so `alembic.ini` is not
    found and migrations break). **Replace their local `_run_migrations` with
    `sapphire_flow.flows._db.run_migrations`**, whose `_REPO_ROOT` is computed from
    `_db.py`'s own location (`src/sapphire_flow/flows/_db.py:9-10`, four `.parent` hops to
    repo root) and is therefore correct regardless of the caller's location.
  - *Verify (unit):* a test asserts `sapphire_flow.flows._db._REPO_ROOT / "alembic.ini"`
    resolves to an existing file, and that the promoted `cli.<name>` modules call
    `flows._db.run_migrations` (no module-local `_REPO_ROOT` remains). This catches the
    relocation bug that `--help` cannot.
- **2B — Register `[project.scripts]` console entry points** for the promoted scripts
  (kebab-case: `backfill-meteoswiss-history`, `validate-forcing`, `plan100-audit`, and
  `onboard` iff promoted). `pyproject.toml:49-50` currently has only `check`.
  - *Scope note:* an earlier draft also folded in registering the pre-existing
    `register_deployments` entry point (`src/sapphire_flow/cli/register_deployments.py:179` has
    a `main()` not wired in `[project.scripts]`). **Dropped from this plan** — it is a separate,
    pre-existing loose end unrelated to the evidenced defect (operational scripts absent from
    the image), and it is not unrunnable today (`python -m
    sapphire_flow.cli.register_deployments` works). If worth doing, it is a one-line follow-up
    outside Plan 122's scope.
  - *Verify:* `uv run backfill-meteoswiss-history --help` exits 0; `python -c "import
    importlib.metadata as m; …"` lists each registered entry point.
- **2C — Retarget behavior tests to the packaged modules (major).** The existing tests load
  the loose files via `importlib.util.spec_from_file_location` from a `_SCRIPT_PATH`
  (`tests/unit/scripts/test_onboard_script.py:15`,
  `tests/unit/scripts/test_backfill_meteoswiss_history_script.py`,
  `tests/unit/scripts/test_validate_forcing_reference_script.py:26`). Repoint their behavior
  assertions at `sapphire_flow.cli.<name>` imports. Add a small argparse-parity test pinning
  the option set so a silent CLI regression fails.
  - *Verify:* `uv run pytest tests/unit/scripts` green with imports from `sapphire_flow.cli`;
    no test still `exec_module`s a loose promoted script.
- **2C-plan100 — Add the missing `plan100` CLI test (major — it has no test today).** There is
  currently **no** test matching `plan100_forecast_feed_resilience`, `capture_snapshot`,
  `reconcile_priorities`, `audit_floor`, or `audit_forecast_alerts` anywhere under `tests/`
  (verified by grep 2026-07-17). Add a test for the promoted `cli.plan100_*` (or, if `plan100`
  is not promoted in 2A, for the loose module) covering:
  1. **Parser parity** — all five subparsers exist (`capture-snapshot`, `audit-priorities`,
     `reconcile-priorities`, `audit-floor`, `audit-forecast-alerts`) with their options
     (`scripts/plan100_forecast_feed_resilience.py:359-386`).
  2. **Reconciliation safety guards** — `reconcile_priorities(args)` with `--apply` set but no
     `--backup-reference` raises `RuntimeError("… --backup-reference is required …")`
     (`scripts/plan100_forecast_feed_resilience.py:225-226`), and with `--apply` +
     `--backup-reference` but no `--maintenance-mode-confirmed` raises the
     `--maintenance-mode-confirmed`-required `RuntimeError`
     (`scripts/plan100_forecast_feed_resilience.py:228-231`). This is the DB-mutating path the
     `--help` smoke gate cannot exercise.
  - *Note (owner option):* because Phase 1 ships `plan100` unchanged, this guard test could be
    added as a small **Phase-1** unit test if the owner wants guard coverage before electing
    Phase 2; it is placed here to keep Phase 1 to a pure packaging change.
  - *Verify:* the guard assertions fail against a version with the guards removed (proves the
    test is real); `uv run pytest tests/unit/scripts` green.
- **2D — Thin compatibility wrappers or deletion — pick exactly ONE branch, and keep the
  Dockerfile consistent (major).** Phase 1 (Task 1A) bakes
  `scripts/backfill_meteoswiss_history.py`, `scripts/validate_forcing_reference.py`, and
  `scripts/plan100_forecast_feed_resilience.py` via a scoped context `COPY`. Whichever branch
  is chosen, the Dockerfile must stay buildable:
  - **Branch KEEP-WRAPPERS:** keep `scripts/<name>.py` as
    `from sapphire_flow.cli.<name> import main; main()` (so `python scripts/…` keeps working).
    **The Phase-1 scoped `COPY` stays as-is** and the 1B smoke matrix continues to pass
    (wrappers are still present in the build context). No Dockerfile change.
  - **Branch DELETE-WRAPPERS:** delete the loose `scripts/<name>.py` files **and in the same
    change remove the now-dangling `scripts/<file>.py` entries from the Task-1A `COPY` line**
    (a `COPY` naming a deleted context path fails the build with `not found`). Switch the 1B
    smoke matrix and every doc invocation (Task 2E) to the console-entry-point form
    (`docker compose run --rm prefect-worker backfill-meteoswiss-history --help`). Do **not**
    leave the Phase-1 `scripts/` `COPY` pointing at deleted files.
  - *Verify:* the image builds and the smoke matrix passes under whichever branch is chosen —
    KEEP-WRAPPERS via `python scripts/<name>.py --help`, DELETE-WRAPPERS via the entry-point
    command; `grep -n "scripts/" Dockerfile` matches the chosen branch (present for KEEP,
    absent for DELETE).
- **2E — Doc sync (grep-driven, enumerated) — only if invocation form changes.** If wrappers
  are dropped, every `python scripts/<name>.py` reference must move to the entry-point form.
  Enumerated reference set (not "any runbook"):
  `README.md:126,138` (onboard), `docs/touchpoint-maps.md:120` (backfill),
  `docs/spec/types-and-protocols.md:2725` (validate_forcing),
  `docs/spec/config-reference.toml:215` (onboard), `docs/architecture-context.md:651`
  (onboard), `docs/deployment/mac-mini-staging.md:333` (plan100).
  - *Verify (exit gate):* `grep -rn "python scripts/\(onboard\|backfill_meteoswiss_history\|validate_forcing_reference\|plan100_forecast_feed_resilience\)" README.md docs/`
    returns nothing (or only inside retained-wrapper compatibility notes).

## Tests

- **Phase 1 deploy-gate (integration, not unit):** Task 1B matrix — every shipped script
  (and every plan100 subcommand) `--help` exits 0 on a freshly built image with **no
  bind-mount**; the same commands fail against the pre-Plan-122 image. *This is the
  regression the plan exists to kill.*
- **Phase 2 migration-path unit test (Task 2A):** asserts the migration config path resolves
  after relocation (via `flows._db.run_migrations`) — the correctness gap `--help` cannot
  catch.
- **Phase 2 import + argparse-parity (Task 2C):** promoted `cli.<name>` imports cleanly,
  exposes `main`, and pins the option set.
- **Phase 2 plan100 CLI test (Task 2C-plan100):** parser parity for all five subcommands **and**
  the two reconciliation safety guards (`--apply` without `--backup-reference` /
  `--maintenance-mode-confirmed` each raise `RuntimeError`) — the DB-mutating path `--help`
  cannot reach. `plan100` has no test today.

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/           # ratchet vs baseline
uv run pytest
```
Plus the **Phase-1 deploy-gate**: build the image and confirm each shipped script (and each
plan100 subcommand) runs `--help` inside a one-off container with **no bind-mount**. If
Phase 2 runs, add the doc-invocation grep gate (Task 2E).

## Dependency graph

```json
{
  "phase1": {
    "tasks": {
      "1A": {"scope": "scoped Dockerfile COPY of operational scripts", "depends_on": []},
      "1B": {"scope": "deploy-gate smoke matrix incl. plan100 subcommands", "depends_on": ["1A"]},
      "1C": {"scope": "confirm onboard.py classification; adjust COPY set", "depends_on": []},
      "1D": {"scope": "doc sync: cicd.md rule + mac-mini smoke matrix", "depends_on": ["1A"]}
    }
  },
  "phase2_optional": {
    "gate": "owner elects cli/ promotion for discoverability",
    "tasks": {
      "2A": {"scope": "move logic to cli/<name>.py; fix _REPO_ROOT via flows._db.run_migrations", "depends_on": ["phase1"]},
      "2B": {"scope": "register [project.scripts] entry points (register_deployments dropped from scope)", "depends_on": ["2A"]},
      "2C": {"scope": "retarget behavior tests to cli.*; argparse-parity pin", "depends_on": ["2A"]},
      "2C-plan100": {"scope": "add plan100 CLI test: parser parity + reconciliation safety guards (no test exists today)", "depends_on": ["2A"]},
      "2D": {"scope": "KEEP-WRAPPERS (COPY stays) XOR DELETE-WRAPPERS (remove scripts/ from Dockerfile COPY + switch to entry-point invocations)", "depends_on": ["2A"]},
      "2E": {"scope": "grep-driven doc invocation sync (only on DELETE-WRAPPERS branch)", "depends_on": ["2D"]}
    }
  }
}
```

## Provenance

Surfaced 2026-07-17 while running the 115b3 staging GO/NO-GO validation over SSH — the
backfill (115b2) and validation (115b3) scripts were absent from the deployed image and only
ran via an ad-hoc `scripts/` bind-mount. Revised after an independent Codex review pass:
corrected the `config.toml` bake→bind-mount claim, reclassified `plan100_…` as operational
and `check_readiness.py`/`regenerate_icon_grid_asset.py` as out-of-scope DEV tooling per an
explicit classification rule, split the work into a proportionate scoped-COPY Phase 1 (the
defect fix) and an optional `cli/` Phase 2 (discoverability, carrying the `_REPO_ROOT`
path-logic fix + test retarget), and enumerated the doc-sync set. Revised again after a second
independent Codex pass: **corrected Task 1A from an unbuildable `COPY --from=builder` (the
builder stage never copies `scripts/`, `Dockerfile:17,22-24`) to a plain context `COPY` from
the build context matching the `docker/entrypoint.sh` precedent (`Dockerfile:59`)**; fixed the
stale `Dockerfile:61-64`→`62-65` line citations and re-anchored the 1A insertion after
`alembic/` (line 65, not `alembic.ini` at 64); added an explicit KEEP-WRAPPERS↔DELETE-WRAPPERS
branch to Task 2D that keeps the Dockerfile `COPY` consistent; added the missing `plan100` CLI
test task (2C-plan100, parser parity + reconciliation safety guards); dropped the unrelated
`register_deployments` registration from 2B; folded the `cicd.md:29` "copies only `.venv`"
correction into the 1D doc-sync; tightened the 1B "no bind-mount" wording to "no `scripts/`
source bind-mount (committed config bind-mounts may remain)"; and added a Phase-2
citation-drift re-verify caveat. DRAFT — plan-review (incl. an independent Codex pass) before
READY; owner to confirm `onboard.py` (Task 1C) and whether Phase 2 is wanted.
