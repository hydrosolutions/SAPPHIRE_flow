---
status: COMPLETE
created: 2026-09-16
revised: 2026-09-16
plan: 273
title: Nepal illustrative multi-cycle export v2
related: [139, 143, 192, 198, 204, 219, 268]
---

# Plan 273 — Nepal multi-cycle animation data

## Owner request and scope

The owner forwarded `SAPPHIRE-flow-map/docs/SAPPHIRE_FLOW_NEPAL_CYCLES_EXPORT_PROMPT.md`
and requested changed backend output. This supersedes the single-issue scenario
committed as 715c6aaa (v0.1.902). Prior automode authorization persists for bounded
implementation and reviews; no push, PR, merge or deployment is authorized.

Supply eight issues six hours apart, each a full 72-hour band at three-hourly steps;
one hourly observation record from seven days before first issue THROUGH final issue;
remove separate verification and superseded arrays; avoid designed convergence;
update supersession metadata. Values remain synthetic, geometry real and unchanged.
The exact v2 contract is `docs/spec/nepal-demo-bundle.md`. Breaking shape gets v2,
not a changed interpretation of v1. Preserve v1 schema/example for reference.

Cadence: owner's stated 3h/72h/6h product matches aquacast's global subdaily config.
Dudh Koshi's fine-tune has hourly targets; document the difference rather than claim
this demo runs that model. First issue 2025-08-12T00:00:00Z, final +42h. Forecast
samples +3..+72h (24); horizon end +75h exclusive. Observations hourly -168..+42h
(211), end +43h exclusive, gaps [-48,-42) and [17,19). Display obs <= active issue.

Independent random streams for observations and forecasts. Issue peak time relative
to first issue, amplitude and width are independent draws from fixed distributions,
never functions of issue index or observation error. Time only selects each issue's
sampling window. No fitted convergence, scores, weather, FI model, DB/API changes,
operational onboarding or frontend edits. Same seed and first issue -> same bytes.

The JSON Schema is explicitly Draft 2020-12 and ships as schema.json alongside the
four data files. Frontend replaces its partial validator. An optional separate,
labelled render-only geometry derivative is acceptable, with source/method/tolerance
provenance and geometry/outlet checks, preserving the authoritative full outline;
its generation remains frontend-owned and outside the exported bundle schema.

## Repository and execution

Worktree `/private/tmp/sapphire-nepal-demo`, branch `feat/nepal-demo-export`.
Fresh origin/main checked this turn: f2dc569c, no upstream Plan273 replacement.
Start implementation from a clean named branch after the reviewed plan is committed.
Reuse the existing service/domain/boundary/CLI modules and atomic no-replace publisher.
No dependencies or packaging changes except mandatory patch version bump.

## Tasks

### T1 — Pin v2 contract and review

Outcome: versioned specification, cadence decision, schema dialect, render-copy
policy and exact consumer migration. In/out: plan/spec/handoff, no frontend edits.
Pre-change: N/A, contract work; v1 only contains one forecast.
Verification: independent Claude design, Codex repository, and focused contract
reviews of full revised plan before implementation. Resolve concrete findings
under owner's automode authorization, then record READY.

### T2 — Generate and validate multiple issues

Outcome: immutable scenario/issue values, independent seeded streams, eight complete
quantile forecasts, one spanning observation record, no separate verification series.
In: types/nepal_demo.py, services/nepal_demo.py, cli/nepal_demo_schemas.py and tests.
Pre-change: tests for eight issues/new shape fail against v1.
Verification: targeted service and boundary tests check exact schedules, IDs,
observation cutoff at every issue, nonnegative ordered quantiles, masks, invalid
version/count/order/cadence/QC/geometry rejection. A changed observation RNG must
leave every forecast unchanged; fixed-seed peak parameters must not be sorted into
a progressive convergence story. No skill scoring in runtime or output.

### T3 — Export, schema and handoff

Outcome: four data files plus schema.json in fresh output directory, v2 example
at tests/fixtures/nepal_demo_v2 and schema at docs/spec/nepal-demo-bundle-v2.schema.json.
Retain v1 reference files, existing geometry and atomic filesystem behavior.
In: CLI serialization, generated schema/fixture, existing CLI tests, runbook/handoff.
Pre-change: old CLI emits v1/no schema, short observations and one issue.
Verification: `uv run pytest tests/unit/services/test_nepal_demo.py tests/unit/cli/test_nepal_demo_schemas.py tests/unit/cli/test_export_nepal_demo.py -q`;
validate generated fixture with Draft202012Validator, including negative nullable
value/schema tests; two exports byte-equal; v1 rejected; existing destination/write
failure/race protections pass. Run CLI with fresh destination and no infrastructure.

### T4 — Verify and deliver

Outcome: consumer handoff answers all five changes, cadence, dialect/vocabulary and
render-copy policy. In: affected docs, patch version, committed branch and local export.
Pre-change: N/A, delivery/checks. Verification: focused tests + existing 28 station/
forecast API regressions, repository lint/format, changed-module pyright, independent
Claude and Codex patch reviews plus focused contract review. Full suite required
before merge; frontend import/rendering is separately verified by frontend owner.
Do not claim the animation complete from backend tests.

## Review record

V2 independent Codex repository and focused contract reviews found no defects.
Claude design review clarified that schema_version changes value, replaced
ambiguous “duplicate outturn” wording with “no separate verification series”, and
specified the illustrative observation curve. These wording corrections are applied.
The frozen v1 contract is included in the plan commit. READY records the owner’s
forwarded implementation request and continuing automode authorization. V1's 61
passing checks do not establish v2 behavior; new RED/GREEN evidence follows.

## Implementation evidence — 2026-09-16

T1 complete: reviewed v2 plan/spec/handoff committed as 92bbf660 before code.
T2/T3 complete: RED reproduced against v1 (missing build_issue and missing
series.forecasts), followed by 48 passing Nepal generator/boundary/export checks.
Eight complete forecasts, independent RNG streams, spanning observations and
strict sequence/gap validation are implemented. V1 schema/example are preserved.
Five-file export at `/private/tmp/nepal-demo-rabuwa-v2/` equals the v2 fixture;
Draft 2020-12 and semantic validation pass, and basin.geojson is byte-identical to v1.

T4 verification: 76 targeted checks pass (48 Nepal + 28 existing API regressions).
`uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` pass;
pyright on the four changed modules reports no errors. Broader `ruff check .` has
12 pre-existing E501 findings in migrations 0037/0038; `ruff format --check .`
reports pre-existing formatting in migrations 0031/0036. These untouched files are
outside the workflow's src/tests gate. The default full regression suite passed
after the final code change; see the close-out below.
Version is bumped to 0.1.910. No model/DB/API/frontend changes or external deployment.
Codex repository patch review and focused contract patch review found no defects.
Claude patch review also found no defects. Its non-blocking seed-label note is
resolved by documenting generator_seed as the base seed, with forecast stream
base+1 (already explicit in per-asset provenance). T4 complete; backend is ready
for frontend import; browser rendering remains separately verified by the frontend
owner. The backend full-suite gate is now satisfied for commit f3c7596b.


## Regression close-out — 2026-09-16

Executed on code commit `f3c7596b`:
`uv run --no-sync --no-cache pytest -q --maxfail=5`.
Exit status 0: **6311 passed, 51 skipped, 15 deselected, 54 warnings** in
1092.09 seconds (18m12s). The failure limit was not reached; the selected suite
completed at 100%. Docker-backed integration tests used the disposable PostGIS
fixture. An initial sandbox-limited collection was interrupted; the completed run
used Docker access outside the sandbox.

This is the repository's default selection from pyproject.toml, excluding markers
`deployment`, `live`, `live_lindas`, `live_stac` and `slow`. It is not a claim that
live-service or full deployment tests ran. Warnings include upstream deprecations,
an unregistered existing live_lindas_lag marker, numerical warnings in skill tests,
CRS conversion and structlog formatting; none caused a failure. No code changes
were needed. Local raw log: `/private/tmp/nepal-backend-full-suite.log`.

The backend regression gate is closed. Frontend import/playback evidence remains
owned by the flow-map session; real model/station deployment belongs to the
DL deploy session. No push, PR, merge or deployment was performed here.

```json
{"phases":[{"id":"contract","tasks":["T1"],"parallel":false},
{"id":"producer","tasks":["T2"],"depends_on":["contract"]},
{"id":"export","tasks":["T3"],"depends_on":["producer"]},
{"id":"handoff","tasks":["T4"],"depends_on":["export"]}]}
```
