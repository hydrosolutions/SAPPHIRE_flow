---
status: DRAFT
created: 2026-09-25
plan: 345
title: The flow map reads the /api/v1 interface — QC rule sets and station skill endpoints, forecast QC flags, and a committed API contract
scope: Give the flow map (a review tool for our forecast products, not an operational dashboard) everything it needs to review QC and skill through the /api/v1 interface, read-only, using an admin token — an admin-only endpoint serving the observation AND forecast QC rule sets, an admin-only per-station skill endpoint, two additive fields on existing responses, and a committed, drift-tested OpenAPI contract covering only the routes the map reads. NOT any change to QC rules, thresholds, selection or verdicts; NOT any skill computation; NOT what a consumer token can read; NOT the Forecast Lab snapshot, which stays forecast-lab-snapshot/v2 unchanged; NOT a QC what-if/dry-run (D9); NOT forcing or basin attributes (last priority, follow-on); NOT per-station overrides (269) or network-specific rules (264/303).
risk: high   # external-facing API contract (docs/workflow.md § High-risk work)
depends_on: []
blocks: []
related: [143, 147, 198, 235, 251, 264, 269, 272, 303, 323, 324, 329, 341]
open_decisions: [D9]
closed_decisions: [D1, D2, D3, D4, D5, D6, D7, D10, D11]   # D2-D4 2026-09-25; D1, D5-D7, D10, D11 2026-09-26
superseded_decisions: [D8]
source: 2026-09-25 — request from the SAPPHIRE-flow-map session (audience Nepal DHM: see which readings QC rejected and why, judge the thresholds, compare models' skill). 2026-09-26 — owner: the map reads the API with an admin token, not an extended snapshot. Measured on origin/main and the staging database.
---

# Plan 345 — the flow map reads the /api/v1 interface

## Status

**DRAFT — HIGH RISK — reviewed once, corrections folded, not re-reviewed.** An external-facing
API contract is a high-risk trigger (`docs/workflow.md` § High-risk work), so in addition to the
ordinary Claude + Codex pair the owner commissions **one more independent review before READY and
again before the implementation PR**. The first Claude and Codex passes (2026-09-26, on
`f71fb951`) found 17 issues, all verified; the owner decided D10 and D11 and had the rest folded.
The corrected plan has had no review. One decision is open (D9, a follow-on).

## Why this exists

The flow map is the MVP we use to demonstrate, test and review the forecast products we provide.
It must let a reviewer — DHM first, the Swiss Forecast Lab too — read observations and forecasts,
**see what QC flagged and judge whether the thresholds are right**, and view skill metrics.
Forcing and basin attributes come last.

Today the map reads one JSON document, the Forecast Lab snapshot, which carries passed readings
only, no rule or threshold, and no skill. The owner's decision (D1): the map moves to the
`/api/v1` interface, which already serves most of it, and this plan fills the gaps.

## What already exists (origin/main, 2026-09-26)

| the map needs | served today by | gap |
|---|---|---|
| stations | `GET /api/v1/stations`, `/stations/{id}` (any valid token) | none for this plan |
| observations **with every QC status and the stored flags** | `GET /api/v1/stations/{id}/observations?parameter=&start=&end=[&qc_status=]` — `ObservationResponse` carries `qc_status` and `qc_flags` (`rule_id`, `rule_version`, `status`, `detail`) | the row's stored `qc_rule_version` is not returned |
| forecasts, including combined ones, with QC status | `GET /api/v1/stations/{id}/forecasts` (`ForecastSummary`, no combination filter), `GET /api/v1/forecasts/{id}` — `qc_status` present | the forecast's `qc_flags` are not returned; `ForecastSummaryRow` (`types/forecast_summary.py`) does not carry them |
| the QC rule sets and thresholds | nothing | **new endpoint** |
| per-station skill rows | nothing — the only skill route, `GET /api/v1/models/{model_id}/skill-chart.json` (`api/routes/models.py:178`), is an admin-only per-model chart export | **new endpoint** |
| a contract the map can pin | nothing — `openapi_url=None` (`api/__init__.py:31`, Plan 147 Slice C) deliberately publishes no schema | **commit one for the map's routes only** |

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

- **Two QC rule sets.** Observation QC: `QcRuleSet`/`QcRuleId` (`types/domain.py:137`) — five
  kinds; severity in `services/qc.py`: `range_check` → `qc_failed`, the other four → `qc_suspect`.
  Forecast QC: `ForecastQcRuleSet`/`ForecastQcRuleId` (`types/domain.py:235`) — seven kinds;
  severity in `services/forecast_qc.py`: `negative_value`, `range_check`, `quantile_crossing` →
  `qc_failed`; `flat_ensemble`, `ensemble_spread`, `climatology_outlier`, `temporal_consistency`
  → `qc_suspect`. **`range_check` exists in both**, so a flag's `rule_id` alone does not identify
  its rule.
- **Three loaders, one pattern.** Observation rules: `flows/ingest_observations.py::_load_qc_rules`
  and an identical copy in `flows/onboard.py::_load_qc_rules` (onboarding also writes stored flags).
  Forecast rules: `flows/run_forecast_cycle.py::_load_forecast_qc_rules`. Each: `SAPPHIRE_CONFIG`
  set → `load_*_qc_rules` (base file + `SAPPHIRE_CONFIG_OVERLAY` overlays), which **itself falls back
  to the built-in Swiss default when the merged file has no `[qc_rules]` / `[forecast_qc_rules]`
  section** (`config/qc_rules.py:262`, tested at `tests/unit/config/test_qc_rules.py:199`); unset →
  built-in default. Ingest applies no per-station override (`overrides=[]`).
- **Version labels are not rule versions.** An observation flag's `rule_version` is the
  code-generation label `"1.2"` (history `"1.0"`) for four kinds and the configured version
  (`"1.0.0"`) for `frozen_sensor` — deliberate (Plan 324). The row's `qc_rule_version` is `"1.2"`,
  `"1.2-datum"` or `"1.2-datum-skip"` (`services/qc_datum.py:21-22`, `services/qc.py`); `-datum-skip`
  means `range_check` and `gross_outlier` were skipped, and water-level thresholds apply to
  datum-shifted values. Forecast flags carry the forecast QC module's own `_RULE_VERSION`.
- **Skill.** `latest_generation_predicate` (`store/skill_store.py`) scopes by station, model,
  artifact, parameter, skill source and forcing type — not time step; harmless today (one cadence).
  `model_artifacts.training_period_start/_end` are NOT NULL (`db/metadata.py:936-937`);
  `SkillScore.lead_time_hours` is a non-null `int`.
- **Assignments.** `services/forecast_lab/db_sources.py::fetch_active_model_assignments` resolves
  station and group assignments (Plan 329: minimum priority, station wins a tie, else lowest
  `group_id`) but projects the winner into a `ModelAssignment` and **discards the winning group**
  (`:198-209`), so its result alone cannot locate a group-scoped artifact.
- **Auth surface.** `security.md:44-49` and the router comment at `api/__init__.py:62-67`: the
  modern `/api/v1/stations|forecasts|alerts` API is the one surface a consumer token reaches; every
  other data route is admin-gated. `require_admin` rejects a non-admin with 403.

## Owner decisions

### D1 — the map reads `/api/v1`, not an extended snapshot. **⚖️ CLOSED — owner, 2026-09-26.**

The owner: *"are these not all part of the same api interface?"* and *"currently the map reads
the json but that could be changed."* QC and skill are served as endpoints, once, for every region;
the snapshot stays `forecast-lab-snapshot/v2` exactly as it is (it still carries the archived BAFU
forecasts, which have no API route). The earlier snapshot decisions — one v3 shared with Plan 251,
and D8 — no longer apply. Plan 251 returns to its own scope; T3 adds forecast `qc_flags` to the API,
which may cover 251's purpose — the owner decides 251 separately.

### D2 — in-sample skill, labelled, active artifact only. **⚖️ CLOSED — owner, 2026-09-25.**

Every row carries its artifact's training period and a derived `evaluated_on` label; only rows on
an **active** artifact are served. The map shows them as fit scores and ranks nothing.

### D3 — Nepal discharge QC starts from the Swiss thresholds. **⚖️ CLOSED — owner, 2026-09-25.**

Selection stays parameter + cadence. The rule-set endpoint lets the map label them as Swiss. Hourly
DHM data stays `qc_unchecked` until hourly rules land (323); the map shows it as unchecked, never
passed.

### D4 — map conventions. **⚖️ CLOSED — map session, 2026-09-25; corrected 2026-09-26.**

Severity is derived from code; missing baselines are shown as absent. **Matching is by rule set and
`rule_id`** — observation flags against the observation set, forecast flags against the forecast
set (corrected: `rule_id` alone is ambiguous, see D10). ⚠️ The map also adopted "no time step on
skill rows" on the strength of a wrong statement from this side — skill rows carry
`time_step_seconds` and `phase_offset_seconds`, and this plan serves both (T6 tells the map).

### D5 — reading `skill_scores`. **⚖️ CLOSED — owner, 2026-09-26.**

Plan 198's bar stays in force **for the snapshot**, which this plan does not touch. The new
endpoint reads `skill_scores` as the admin-only legacy routes already do, with one guard: a row
whose `eval_period_end` is after the request time is not served.

### D6 — flag `detail` text. **⚖️ CLOSED — owner, 2026-09-26: published verbatim.**

The observations endpoint already serves `detail` verbatim; this plan changes nothing there.
⚠️ **Carried forward, not decided here:** the Nepal region-bundle draft forbids copying `detail`
verbatim for restricted DHM data, and under D11 the map's admin token reads every station the
moment it is onboarded. So **before DHM observations are ingested on an instance the map reads**,
the owner decides whether `detail` is stripped for that network. T6 records this in Plan 143 (DHM
onboarding), where it will be seen.

### D7 — all skill breakdowns. **⚖️ CLOSED — owner, 2026-09-26.**

Headline and season/flow-regime rows alike, ≈ 513 rows per station.

### D8 — sharing v3 with Plan 251. **SUPERSEDED by D1, 2026-09-26.**

### D9 — how does a reviewer see the *implications* of a threshold? **OPEN — follow-on.**

This plan lets the map show each threshold and exactly which readings it flagged. It does not let a
reviewer ask "what would a different threshold have flagged?". Re-implementing the checker in the
map would drift from ours (cadence inference, the ingest context window, `gross_outlier`'s
climatological baselines).

**Recommendation:** a separate follow-on plan for a read-only server-side dry run — the real
checker, the stored readings of one station and window, proposed thresholds in the request, flags
in the response, nothing written. Not in this plan.

### D10 — serve the forecast QC rule set too. **⚖️ CLOSED — owner, 2026-09-26.**

The map reviews forecast products, and forecast flags cannot be judged against observation rules
(`range_check` exists in both sets; the other six forecast kinds would match nothing). The rules
endpoint serves both sets, each labelled, and the consumer page states the matching rule (D4).

### D11 — the map uses an admin token. **⚖️ CLOSED — owner, 2026-09-26.**

The map reads with an admin token, so **nothing a consumer token can read changes**: the two new
routes are **admin-only**, like the existing skill chart, and the consumer surface in
`docs/standards/security.md` stays as it is. Consequences recorded here, not re-opened:
- The admin token must stay **server-side** in the map — never shipped to a browser or committed —
  because it also reaches every admin route. T6 states this to the map session.
- Station scope does not filter an admin token: the map sees every station, Nepal included once
  onboarded (hence D6's carry-forward).

## Endpoint contract

Both new routes are `GET`, read-only, registered on a router gated with `Depends(require_admin)`.
A consumer token receives 403. Timestamps follow the API's existing UTC convention.

**`GET /api/v1/qc/rules`** — both QC rule sets as the serving process resolves them:

```text
{ observation: RuleSetBlock, forecast: RuleSetBlock }

RuleSetBlock:
  { version,                                 # the set's version as resolved
    source: "config" | "builtin_default",   # which branch actually supplied the rules; never a path or overlay name
    selection: "parameter_and_cadence",
    rules: [ { rule_id, rule_version, parameter, time_step_seconds,
               severity,                     # qc_failed | qc_suspect, from the code constant (T1)
               thresholds: { name: number | null } } ] }   # every rule, config order
```

`source` is `builtin_default` whenever the built-in rules were used — `SAPPHIRE_CONFIG` unset, **or**
set but its merged file lacks the section.

**`GET /api/v1/stations/{id}/skill[?model_id=]`** — the station's current skill:

```text
{ station_id,
  selection: "latest_generation_on_active_artifact",
  rows: [ { model_id, model_artifact_id, generation_id,      # generation_id null for a pre-235 baseline row
            assignment_scope: "station" | "group",
            skill_source, forcing_type, computation_version,
            time_step_seconds, phase_offset_seconds,
            eval_period_start, eval_period_end,
            training_period_start, training_period_end,      # never null
            evaluated_on: "training_period" | "outside_training_period" | "overlaps_training_period",
            lead_time_hours,                                 # never null
            season, flow_regime,                             # each null when the row is not stratified by it
            metric, score, sample_size } ] }
```

Row selection:
1. The station's **active** assignments, station and group, each with the scope that wins under
   Plan 329's rule (minimum priority; station wins a tie; else lowest `group_id`). T2 extracts this
   resolution into a typed helper returning the assignment **and** its scope (station, or the
   winning group's id); `fetch_active_model_assignments` is re-expressed on top of it so the
   snapshot's behaviour is unchanged and the two can never disagree.
2. Skill rows for each model, parameter `discharge`, via `SkillStore.fetch_latest_scores` (the Plan
   235 predicate).
3. Kept only when `model_artifact_id` is an active artifact for the winning scope —
   `ModelArtifactStore.fetch_artifacts_by_status(model, ACTIVE, station_id=|group_id=)`; never a
   call that loads artifact bytes. Training period from `fetch_artifact_record`.
4. Dropped when `eval_period_end` is after the injected request time (D5).

Sorted by `(model_id, time_step_seconds, lead_time_hours, season, flow_regime, metric)`, nulls
first. `evaluated_on`: eval window inside the training period → `training_period`; disjoint →
`outside_training_period`; otherwise `overlaps_training_period`. Empty `rows` when nothing
qualifies (the baselines today). Unknown station → 404.

**Additive fields on existing responses:** `ObservationResponse.qc_rule_version` (nullable, the
stored value); `ForecastSummary.qc_flags` (the stored forecast QC flags, same four keys; `[]` when
none) — inherited by `ForecastDetail`.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate). Every new route
is added to `tests/unit/api/test_security.py::TestRouteAuthMatrixExhaustive` as ADMIN.

### T1 — `GET /api/v1/qc/rules`

**Outcome:** the endpoint serves both rule sets exactly as the writing flows resolve them, each
with a code-derived severity per rule and a truthful `source`.

**In:**
- `config/qc_rules.py` and `config/forecast_qc_rules.py` — one public resolution function per set
  (`SAPPHIRE_CONFIG` + overlays, else built-in default) returning the rule set **and** which branch
  supplied it, including the missing-section fallback. `flows/ingest_observations.py::_load_qc_rules`,
  `flows/onboard.py::_load_qc_rules` and `flows/run_forecast_cycle.py::_load_forecast_qc_rules`
  call them (behaviour-preserving; the onboard copy is removed).
- `services/qc.py` and `services/forecast_qc.py` — a severity constant keyed by `QcRuleId` /
  `ForecastQcRuleId`, covering every kind.
- A new admin-gated route module (e.g. `api/routes/api_review.py`, holding T2's route too),
  response models in `api/schemas.py`, registration in `api/__init__.py` with
  `Depends(require_admin)`, and the router comment there (`:62-67`) naming the new admin-only routes.

**Out:** editing any rule function, threshold, rule version or selection; publishing a path; any
consumer-token access.

**Pre-change:** (1) per set, a test running each rule kind against a violating input and asserting
the emitted status equals the severity constant fails on the missing constant, then passes unchanged
once it exists — the constant describes the code, it does not change it; (2) a request to the route
returns 404.

**Verification:** `uv run pytest tests/unit/services/test_qc.py tests/unit/services/test_forecast_qc.py tests/unit/config/test_qc_rules.py tests/unit/config/test_forecast_qc_rules.py tests/unit/flows/test_ingest_observations.py tests/unit/flows/test_onboard_flow.py tests/unit/api/` — for each set, three resolution cases: config file with the section → `config` and the file's rules; config file without the section → `builtin_default`; variable unset → `builtin_default`. The three flows' loaded sets are unchanged in every case. Admin token → 200; consumer token → 403; no token → 401.

### T2 — `GET /api/v1/stations/{id}/skill`

**Outcome:** the endpoint serves the rows specified above.

**In:** the typed assignment-and-scope helper (and `fetch_active_model_assignments` re-expressed on
it); a skill-read service function using `station_store`, `group_store.fetch_groups_for_station` /
`fetch_group_model_assignments`, `skill_store` (already in `api/deps.py`), and
`ModelArtifactStore.fetch_artifacts_by_status` / `fetch_artifact_record`; the route in T1's module;
response models.

**Out:** computing or re-selecting skill; `latest_generation_predicate`; skill diagrams; any ranking
or comparability judgement; any change to what the snapshot exports.

**Pre-change:** (1) a test proving the current helper cannot say which group won — a station in two
groups that both assign the same model at different priorities, whose result carries no group
identity; (2) a request to the route returns 404.

**Verification:** `uv run pytest tests/unit/services/forecast_lab/ tests/unit/api/` plus the new test file(s), named in the PR — superseded-artifact rows excluded; a model assigned both directly and through a group uses the winning scope's active artifact; two groups assigning the same model resolve to the group with the smaller `priority` value, then the lower `group_id` on a tie; a row with `eval_period_end` after the injected request time is dropped; stratified and headline rows both present; `evaluated_on` takes each of its three values; `?model_id=` filters; unknown station → 404; the existing snapshot tests pass unchanged.

### T3 — additive QC fields on existing responses

**Outcome:** observations carry `qc_rule_version`; forecast summaries and details carry `qc_flags`.

**In:** `types/forecast_summary.py` (`ForecastSummaryRow` gains `qc_flags`),
`store/forecast_store.py::_row_to_summary` and the summary query, `api/schemas.py`,
`api/routes/api_stations.py`, `api/routes/api_forecasts.py`, and the `ForecastSummaryRow` entry in
`docs/spec/types-and-protocols.md`.

**Out:** any filter change — which observations and forecasts are listed stays as it is.

**Pre-change:** three failing tests, one per gap: the station forecast **list** omits a suspect
forecast's `qc_flags`; the forecast **detail** omits them; an observation omits its stored
`qc_rule_version`.

**Verification:** `uv run pytest tests/unit/api/ tests/unit/store/` plus the store test for `_row_to_summary`, named in the PR — a flagged forecast returns its flags on both routes, an unflagged one `[]`; an observation returns its stored `qc_rule_version`, and `null` when none is stored.

### T4 — commit the contract for the map's routes, and the consumer page

**Outcome:** `docs/spec/api-v1-map.openapi.json` describes **only** the routes the map reads, is
committed, and a test fails when it drifts (the snapshot schema's mechanism, Plan 198 D15).
`openapi_url` stays `None`; nothing is served.

**In:**
- The generated file, built with `fastapi.openapi.utils.get_openapi(routes=<explicit list>)` over
  exactly: `GET /api/v1/stations`, `/stations/{id}`, `/stations/{id}/observations`,
  `/stations/{id}/forecasts`, `/forecasts/{id}`, `/qc/rules`, `/stations/{id}/skill`. A drift test
  in `tests/unit/api/`, and an assertion that the list holds no other route.
- `docs/spec/api-v1-review.md`, a short consumer page: `qc_unchecked` ≠ passed; `raw` = not yet
  checked; match flags **by rule set and `rule_id`**; severity; the `qc_rule_version` labels
  (`"1.2"`, `"1.2-datum"`, `"1.2-datum-skip"`) are code generations, not rule-set versions, and
  `-datum-skip` means two rules were skipped; forecast flag versions; `evaluated_on`; the token must
  stay server-side.
- `docs/standards/security.md` — one sentence adding the two admin-only routes to the admin-gated
  list (the consumer surface is unchanged); the `docs/touchpoint-maps.md` API paragraph (new routes,
  the contract file).

**Out:** serving the schema; versioning the path (`/api/v2`).

**Pre-change:** N/A — new artefact; the drift test is proven by mutating one response model locally
and watching it fail.

**Verification:** `uv run pytest tests/unit/api/` including the drift test and the route-matrix test.

### T5 — route classification for Plan 341

**Outcome:** Plan 341's route-classification list names the two new routes as **admin-only internal
diagnostic** and notes they carry no forecast values, so its publication gate does not apply to them.

**In:** a note in `docs/plans/341-chwrr-forecast-publication-api.md`.

**Out:** changing 341's design.

**Pre-change:** N/A — cross-plan record.

**Verification:** bounded inspection of the note.

### T6 — hand-over and durable records

**Outcome:** the map session has the endpoints and contract; the Nepal onboarding and Plan 251 know
what changed, in the repository and not only in a message.

**In:**
- A reply to the map session: endpoints, `docs/spec/api-v1-map.openapi.json`, the admin token and
  that it must stay server-side, matching by rule set and `rule_id`, the `time_step_seconds`
  correction, the in-sample label, and that the snapshot stays v2 for archived BAFU forecasts.
- **Plan 143** (DHM onboarding): the D6 precondition — before DHM observations are ingested on an
  instance the map reads, the owner decides whether flag `detail` is stripped for that network.
- A note to the region-bundle v3 draft's owning session that QC and skill are station-level
  `/api/v1` endpoints, plus the D6 carry-forward.
- A status note in Plan 251 recording D1's supersession; the `docs/plans/README.md` entry.

**Out:** editing another session's worktree.

**Pre-change:** N/A — documentation and hand-over.

**Verification:** the reply's routes and fields match the committed contract file; the Plan 143 note
exists on main.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/345-flow-map-reads-the-api.md
```

After staging deploy (orchestrator), before the map is told:

1. Run T1's two resolution functions inside `api`, `prefect-worker-ingest` (observation ingest) and
   `prefect-worker` (onboarding's observation QC and the forecast cycle, both on the default pool —
   `cli/register_deployments.py`) and compare the returned sets — equal environment variables do
   not prove equal rules (overlays and bind-mounted file contents also decide).
2. For one station, `/skill` row count equals a direct SQL count of the same selection, and no row
   belongs to a superseded artifact.
3. Admin token → 200 on both new routes; a consumer token → 403.

## Explicitly out of scope

- Any QC behaviour; the flag-version mismatch is declared, not fixed.
- What a consumer token can read.
- The Forecast Lab snapshot (stays v2); moving archived BAFU forecasts to the API.
- QC what-if / dry run (D9); forcing and basin attributes (last priority).
- DHM/Nepal rules (303), hourly rules (323), network selection (264), overrides (269).
- `latest_generation_predicate` lacking time step.

## Changelog

- 2026-09-25 — drafted as a snapshot v3 extension; D2–D4 closed.
- 2026-09-26 — D5–D8 closed; then rewritten for the `/api/v1` interface (D1). File renamed from
  `345-forecast-lab-snapshot-v3-qc-and-skill.md`.
- 2026-09-26 — first Claude + Codex reviews (on `f71fb951`): 17 findings, all verified. Owner
  decided D10 (serve the forecast rule set too) and D11 (admin token; new routes admin-only);
  the rest folded: contract limited to the map's routes with `openapi_url` kept `None`;
  assignment scope kept through a typed helper; truthful `source` incl. the missing-section
  fallback; onboarding's loader copy unified; staging gate compares resolved rules per container;
  non-null training period and lead time, three `evaluated_on` values; T3's real files and
  per-gap failing tests; consumer page semantics; skill-chart premise corrected; D6 precondition
  recorded in Plan 143; D8 moved to `superseded_decisions`; marked high-risk.

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
