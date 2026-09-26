---
status: DRAFT
created: 2026-09-25
plan: 345
title: The flow map reads the /api/v1 interface — QC rule set and station skill endpoints, forecast QC flags, and a committed API contract
scope: Give the flow map (a review tool for our forecast products, not an operational dashboard) everything it needs to review QC and skill through the existing authenticated /api/v1 interface, read-only — a QC rule-set endpoint, a per-station skill endpoint, two additive fields on existing responses, and a committed, drift-tested OpenAPI contract. NOT any change to QC rules, thresholds, selection or verdicts; NOT any skill computation; NOT the Forecast Lab snapshot, which stays forecast-lab-snapshot/v2 unchanged; NOT a QC what-if/dry-run (D9); NOT forcing or basin attributes (last priority, follow-on); NOT per-station overrides (269) or network-specific rules (264/303).
depends_on: []
blocks: []
related: [198, 235, 251, 264, 269, 272, 303, 323, 324, 329, 341]
open_decisions: [D9]
closed_decisions: [D1, D2, D3, D4, D5, D6, D7, D8]   # D2-D4 2026-09-25; D1, D5-D8 2026-09-26
source: 2026-09-25 — request from the SAPPHIRE-flow-map session (audience Nepal DHM: see which readings QC rejected and why, judge the thresholds, compare models' skill). 2026-09-26 — owner: the map reads the API, not an extended snapshot. Measured on origin/main and the staging database.
---

# Plan 345 — the flow map reads the /api/v1 interface

## Status

**DRAFT — not reviewed.** Rewritten 2026-09-26 after the owner chose the API over extending the
snapshot (D1). One decision open (D9, a follow-on question). Not implementable until an
independent review is complete and the orchestrator sets READY.

## Why this exists

The flow map is the MVP we use to demonstrate, test and review the forecast products we provide.
It must let a reviewer — DHM first, the Swiss Forecast Lab too — read observations and forecasts,
**see what QC flagged and judge whether the thresholds are right**, and view skill metrics.
Forcing and basin attributes come last.

Today the map reads one JSON document, the Forecast Lab snapshot, which carries passed readings
only, no rule or threshold, and no skill. The owner's decision (D1): the map moves to the
authenticated `/api/v1` interface, which already serves most of it, and this plan fills the gaps.

## What already exists (origin/main, 2026-09-26)

| the map needs | served today by | gap |
|---|---|---|
| stations | `GET /api/v1/stations`, `/stations/{id}` | none for this plan |
| observations **with every QC status and the stored flags** | `GET /api/v1/stations/{id}/observations?parameter=&start=&end=[&qc_status=]` — `ObservationResponse` carries `qc_status` and `qc_flags` (`rule_id`, `rule_version`, `status`, `detail`) | the row's stored `qc_rule_version` is not returned |
| forecasts, including combined ones, with QC status | `GET /api/v1/stations/{id}/forecasts`, `GET /api/v1/forecasts/{id}` — `qc_status` present | the forecast's `qc_flags` are not returned |
| the QC rule set and thresholds | nothing | **new endpoint** |
| skill metrics | nothing under `/api/v1` — only the admin-only legacy HTML routes read it | **new endpoint** |
| a contract the map can pin | nothing — no committed OpenAPI document | **commit one, drift-tested** |

All `/api/v1` routes require a bearer token and filter by the token's station scope
(`docs/touchpoint-maps.md`, API auth paragraph). They are **not** limited to BAFU stations, so the
same endpoints serve Nepal stations once onboarded.

## What is measured (2026-09-25, staging, read-only, discharge)

| fact | value |
|---|---|
| last 30 days, measured rows | 451 294 `qc_passed`, 7 170 `qc_suspect`, 130 `qc_unchecked`, 2 `raw`, 0 `qc_failed`, 0 `missing` |
| models with skill rows | one — `nwp_rainfall_runoff`, `hindcast_reanalysis`, `time_step_seconds = 86400`, `phase_offset_seconds = 0`; none for `persistence_fallback` / `climatology_fallback` |
| current-by-generation rows on superseded artifacts | 139 of 141 stations, on different windows — the generation rule alone does not exclude them |
| active artifacts: eval window vs training period | eval 2010-01-01 → 2020-12-30 inside training 2010-01-01 → 2020-12-31 (one station's eval starts 2019-05-18) — **every active score is in-sample** |
| skill rows per station, active artifact | ≈ 513 (44 headline + season/flow-regime breakdowns) |
| future-dated hindcast rows | 0 of 715 103 |

Repository facts this plan relies on:

- Severity is fixed per rule kind in code: `range_check` → `qc_failed`; the other four → `qc_suspect`
  (`services/qc.py`).
- Flag `rule_version` is a code-generation label (`"1.2"`, history `"1.0"`) for four kinds and the
  configured version (`"1.0.0"`) for `frozen_sensor` — deliberate (Plan 324). Match flags to rules
  by `rule_id` (D4).
- Ingest resolves rules in `flows/ingest_observations.py::_load_qc_rules`: `SAPPHIRE_CONFIG` plus
  overlays, else the built-in Swiss default. Ingest applies no per-station override (`overrides=[]`).
- `latest_generation_predicate` (`store/skill_store.py`) scopes by station, model, artifact,
  parameter, skill source and forcing type — **not** time step. Harmless today (one cadence); the
  endpoint does not rely on it for cadence separation.

## Owner decisions

### D1 — the map reads `/api/v1`, not an extended snapshot. **⚖️ CLOSED — owner, 2026-09-26.**

The owner: *"are these not all part of the same api interface?"* and *"currently the map reads
the json but that could be changed."* So QC and skill are served as endpoints, once, for every
region; the snapshot stays `forecast-lab-snapshot/v2` exactly as it is (it still carries the
archived BAFU forecasts, which have no API route). Reasons: the observation flags are already
served; the rule set and skill change rarely, not per cycle; one implementation serves the Swiss
and Nepal views instead of one block per document format; new endpoints break no existing reader.

**This supersedes the 2026-09-25/26 snapshot decisions:** "one v3 shared with Plan 251" and D8
(absorb 251 T2) no longer apply — this plan makes no format version. Plan 251 returns to its own
scope; T3 below adds forecast `qc_flags` to the API, which may cover 251's purpose — the owner
decides 251 separately.

### D2 — in-sample skill, labelled, active artifact only. **⚖️ CLOSED — owner, 2026-09-25.**

Every row carries its artifact's training period and a derived `evaluated_on` label; only rows on
an **active** artifact are served. The map shows them as fit scores and ranks nothing.

### D3 — Nepal discharge QC starts from the Swiss thresholds. **⚖️ CLOSED — owner, 2026-09-25.**

Selection stays parameter + cadence. The rule-set endpoint lets the map label them as Swiss. Hourly
DHM data stays `qc_unchecked` until hourly rules land (323); the map shows it as unchecked, never
passed.

### D4 — map conventions. **⚖️ CLOSED — map session, 2026-09-25; corrected 2026-09-26.**

Match flags by `rule_id`; severity derived from code; missing baselines shown as absent. ⚠️ The map
adopted "no time step on skill rows" on the strength of a wrong statement from this side — skill
rows carry `time_step_seconds` and `phase_offset_seconds`, and this plan serves both (T6 tells the map).

### D5 — reading `skill_scores`. **⚖️ CLOSED — owner, 2026-09-26.**

Closed as "lift the bar, with the guard" for the snapshot. Under D1 the snapshot is untouched, so
Plan 198's bar stays in force **for the snapshot**; the new endpoint reads `skill_scores` as the
legacy HTML routes already do. The guard is kept: a row whose `eval_period_end` is after the
request time is not served.

### D6 — flag `detail` text. **⚖️ CLOSED — owner, 2026-09-26: published verbatim.**

The observations endpoint already serves `detail` verbatim to every in-scope token; this plan
changes nothing there. ⚠️ **Carried forward, not decided here:** the Nepal region-bundle draft
forbids copying `detail` verbatim for restricted DHM data. Before a DHM station is readable by a
map token, the owner decides whether `detail` is stripped for that network. T6 records this where
the Nepal onboarding will see it.

### D7 — all skill breakdowns. **⚖️ CLOSED — owner, 2026-09-26.**

Headline and season/flow-regime rows alike. Per station that is ≈ 513 rows, so size is no longer a
concern under D1.

### D8 — sharing v3 with Plan 251. **SUPERSEDED by D1, 2026-09-26.**

### D9 — how does a reviewer see the *implications* of a threshold? **OPEN — follow-on.**

This plan lets the map show each threshold and exactly which readings it flagged. It does not let a
reviewer ask "what would a different threshold have flagged?". Re-implementing the checker in the
map would drift from ours (cadence inference, the ingest context window, `gross_outlier`'s
climatological baselines).

**Recommendation:** a separate follow-on plan for a read-only server-side dry run — the real
checker, the stored readings of one station and window, proposed thresholds in the request,
flags in the response, nothing written. Not in this plan.

## Endpoint contract

All new routes are `GET`, read-only and token-authenticated; the station route is scope-checked
like its siblings (the rule set is global and carries no station data). Timestamps follow the API's existing UTC convention.

**`GET /api/v1/qc/rules`** — the observation QC rule set as the serving process resolves it:

```text
{ version,                                 # QcRuleSet.version
  source: "config" | "builtin_default",   # never a path or overlay file name (docs/standards/security.md)
  selection: "parameter_and_cadence",
  rules: [ { rule_id, rule_version, parameter, time_step_seconds,
             severity,                     # qc_failed | qc_suspect, from the code constant (T1)
             thresholds: { name: number | null } } ] }   # every rule, config order
```

**`GET /api/v1/stations/{id}/skill[?model_id=]`** — the station's current skill:

```text
{ station_id,
  selection: "latest_generation_on_active_artifact",
  rows: [ { model_id, model_artifact_id, generation_id,      # generation_id null for a pre-235 baseline row
            skill_source, forcing_type, computation_version,
            time_step_seconds, phase_offset_seconds,
            eval_period_start, eval_period_end,
            training_period_start, training_period_end,      # null when the artifact record has none
            evaluated_on: "training_period" | "outside_training_period" | "overlaps_training_period" | "unknown",
            lead_time_hours, season, flow_regime,            # null when the row is not stratified by it
            metric, score, sample_size } ] }
```

Rows: the station's active assignments as `services/forecast_lab/db_sources.py::fetch_active_model_assignments`
resolves them (station and group, Plan 329), parameter `discharge`, selected by
`SkillStore.fetch_latest_scores` (the Plan 235 predicate), kept only when `model_artifact_id` is an
active artifact for that assignment's scope, and dropped when `eval_period_end` is after the request
time (D5). Sorted by `(model_id, time_step_seconds, lead_time_hours, season, flow_regime, metric)`,
nulls first. `evaluated_on`: inside the training period → `training_period`; disjoint →
`outside_training_period`; otherwise `overlaps_training_period`; a null training bound → `unknown`.
Empty `rows` when nothing qualifies (the baselines today). Out-of-scope station → 404, as siblings.

**Additive fields on existing responses:** `ObservationResponse.qc_rule_version` (nullable, the
stored value); `ForecastSummary.qc_flags` (the stored forecast QC flags, same four keys; `[]` when
none) — inherited by `ForecastDetail`.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate). Every new route
is added to `tests/unit/api/test_security.py::TestRouteAuthMatrixExhaustive`.

### T1 — `GET /api/v1/qc/rules`

**Outcome:** the endpoint serves the rule set exactly as ingest resolves it, with a code-derived
severity per rule.

**In:** `config/qc_rules.py` — one public resolution function (`SAPPHIRE_CONFIG` + overlays, else
built-in default) that also reports which it used; `flows/ingest_observations.py::_load_qc_rules`
calls it (behaviour-preserving). `services/qc.py` — a severity constant keyed by `QcRuleId`,
covering every kind. A new route module or `api_stations.py` sibling, response models in
`api/schemas.py`, router registration.

**Out:** editing any rule function, threshold, rule version or selection; publishing a path.

**Pre-change:** (1) a test running each rule kind against a violating series and asserting the
emitted status equals the severity constant fails on the missing constant, then passes unchanged
once it exists — the constant describes the code, it does not change it; (2) a request to the route
returns 404.

**Verification:** `uv run pytest tests/unit/services/test_qc.py tests/unit/config/test_qc_rules.py tests/unit/flows/test_ingest_observations.py tests/unit/api/` — with `SAPPHIRE_CONFIG` set the rules equal the file's and `source` is `config`; unset, the built-in default and `builtin_default`; ingest's loaded set is unchanged in both; unauthenticated → 401.

### T2 — `GET /api/v1/stations/{id}/skill`

**Outcome:** the endpoint serves the rows specified above.

**In:** a skill-read service function (reusing `fetch_active_model_assignments`, `SkillStore.fetch_latest_scores`,
`ModelArtifactStore.fetch_artifacts_by_status(..., ACTIVE, station_id=|group_id=)` — never a call
that loads artifact bytes — and `fetch_artifact_record` for the training period); route, response
models. `api/deps.py` already provides `skill_store`.

**Out:** computing or re-selecting skill; `latest_generation_predicate`; skill diagrams; any
ranking or comparability judgement.

**Pre-change:** a request to the route returns 404.

**Verification:** `uv run pytest tests/unit/api/ tests/unit/services/` (the new test file(s) by name once created) — superseded-artifact rows excluded; a group-assigned model's rows come from the group's active artifact; a row with `eval_period_end` after the injected request time is dropped; stratified and headline rows both present; `evaluated_on` takes each of its four values; `?model_id=` filters; out-of-scope station → 404.

### T3 — additive QC fields on existing responses

**Outcome:** observations carry `qc_rule_version`; forecast summaries and details carry `qc_flags`.

**In:** `api/schemas.py`, `api/routes/api_stations.py`, `api/routes/api_forecasts.py`, and the
forecast-summary read if it does not yet load flags.

**Out:** any filter change — which observations and forecasts are listed stays as it is.

**Pre-change:** a test reading a suspect forecast through `GET /api/v1/forecasts/{id}` fails on the
missing `qc_flags`.

**Verification:** `uv run pytest tests/unit/api/` — a flagged forecast returns its flags, an unflagged one `[]`; an observation returns its stored `qc_rule_version`, and `null` when none is stored.

### T4 — commit the `/api/v1` contract

**Outcome:** `docs/spec/api-v1.openapi.json` is generated from the app, committed, and a test fails
when it drifts — the same mechanism as the snapshot schema (Plan 198 D15).

**In:** the generated file, a drift test in `tests/unit/api/`, a short consumer page
`docs/spec/api-v1-review.md` stating the QC semantics a reader needs (`qc_unchecked` ≠ passed,
`raw` = not yet checked, match flags by `rule_id`, severity, `evaluated_on`), and the
`docs/touchpoint-maps.md` API paragraph (new routes, the contract file).

**Out:** versioning the path (`/api/v2`) — additions stay under `/api/v1`.

**Pre-change:** N/A — new artefact; the drift test is proven by mutating one response model locally and watching it fail.

**Verification:** `uv run pytest tests/unit/api/` including the drift test.

### T5 — route classification for Plan 341

**Outcome:** Plan 341's route-classification list names the new routes as **internal diagnostic**
(review use, raw products), so its future publication gate does not miss them.

**In:** a note in `docs/plans/341-chwrr-forecast-publication-api.md`.

**Out:** changing 341's design.

**Pre-change:** N/A — cross-plan record.

**Verification:** bounded inspection of the note.

### T6 — hand-over

**Outcome:** the map session has the endpoint list and contract; the Nepal carrier and Plan 251 know
what changed.

**In:** a reply to the map session (endpoints, `docs/spec/api-v1.openapi.json`, the `time_step_seconds`
correction, the in-sample label, that the snapshot stays v2 for archived BAFU forecasts); a note to
the region-bundle v3 draft's owning session that QC and skill are station-level `/api/v1` endpoints,
plus the D6 carry-forward; a status note in Plan 251 recording D1's supersession; the
`docs/plans/README.md` entry.

**Out:** editing another session's worktree.

**Pre-change:** N/A — documentation and hand-over.

**Verification:** the reply's routes and fields match the committed OpenAPI file.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/345-flow-map-reads-the-api.md
```

After staging deploy (orchestrator), before the map is told:

1. `SAPPHIRE_CONFIG` is identical in the `api` and `worker` containers, so `/qc/rules` shows the set
   ingest uses.
2. For one station, `/skill` row count equals a direct SQL count of the same selection, and no row
   belongs to a superseded artifact.
3. With a consumer token, an out-of-scope station returns 404 on `/skill`.

## Explicitly out of scope

- Any QC behaviour; the `"1.2"`/`"1.0.0"` flag-version mismatch is declared, not fixed.
- The Forecast Lab snapshot (stays v2); moving archived BAFU forecasts to the API.
- QC what-if / dry run (D9); forcing and basin attributes (last priority).
- DHM/Nepal rules (303), hourly rules (323), network selection (264), overrides (269).
- `latest_generation_predicate` lacking time step.

## Changelog

- 2026-09-25 — drafted as a snapshot v3 extension; D2–D4 closed.
- 2026-09-26 — D5–D8 closed. Then **rewritten**: owner chose the `/api/v1` interface over the
  snapshot (D1). The snapshot v3, the combined-forecast fields (old T5) and the 251 sharing (D8) are
  gone; two endpoints, two additive fields and a committed OpenAPI contract replace them. D9 opened.
  File renamed from `345-forecast-lab-snapshot-v3-qc-and-skill.md`.

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2", "T3"], "parallel": false},
    {"id": "phase-2", "tasks": ["T4", "T5"], "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T6"], "depends_on": ["phase-2"]}
  ]
}
```
