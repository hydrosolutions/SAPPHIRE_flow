---
status: DRAFT
created: 2026-09-25
plan: 402
title: The flow map reads the /api/v1 interface — QC rule sets and station skill endpoints, forecast QC flags, and a committed API contract
scope: Give the flow map (a review tool for our forecast products, not an operational dashboard) everything it needs to review QC and skill through the /api/v1 interface, read-only, using a tenant-scoped reviewer token (Plan 401) — a reviewer-gated endpoint serving the observation AND forecast QC rule sets, a reviewer-gated per-station skill endpoint, two additive fields on existing responses (visible to every authenticated role, D13), and a committed, drift-tested OpenAPI contract covering only the routes the map reads. NOT any change to QC rules, thresholds, selection or verdicts; NOT any skill computation; NOT any other change to what a consumer token can read; NOT the Forecast Lab snapshot, which stays forecast-lab-snapshot/v2 unchanged; NOT a QC what-if/dry-run (D9); NOT forcing or basin attributes (last priority, follow-on); NOT per-station overrides (269) or network-specific rules (264/303).
risk: high   # external-facing API contract (docs/workflow.md § High-risk work)
depends_on: [401]
blocks: []
related: [143, 147, 198, 235, 251, 253, 264, 269, 272, 303, 323, 324, 329, 340, 341]
open_decisions: [D9]
closed_decisions: [D1, D2, D3, D4, D5, D6, D7, D10, D12, D13]   # D2-D4 2026-09-25; the rest 2026-09-26
superseded_decisions: [D8, D11]
source: 2026-09-25 — request from the SAPPHIRE-flow-map session (audience Nepal DHM: see which readings QC rejected and why, judge the thresholds, compare models' skill). 2026-09-26 — owner: the map reads the API, not an extended snapshot; then, instead of an admin token, a dedicated reviewer token per dashboard (Plan 401). Measured on origin/main and the staging database.
---

# Plan 402 — the flow map reads the /api/v1 interface

## Status

**DRAFT — HIGH RISK — review corrections folded, not re-reviewed.** An external-facing API
contract is a high-risk trigger (`docs/workflow.md` § High-risk work): the ordinary Claude + Codex
pair on the current text, plus one owner-commissioned review before READY and again before the
implementation PR. One decision is open (D9, a follow-on). This plan **depends on Plan 401**.

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
| observations **with every QC status and the stored flags** | `GET /api/v1/stations/{id}/observations?parameter=&start=&end=[&qc_status=]` — `ObservationResponse` carries `qc_status` and `qc_flags` as untyped `list[dict[str, object]]` (`api/schemas.py:85`) | the stored `qc_rule_version` is not returned; the flag shape is not typed |
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
  → `qc_suspect`. **`range_check` exists in both.** Within one set a `rule_id` also repeats across
  parameters and cadences with different thresholds — e.g. observation `rate_of_change` is
  `max_rate = 50` at 600 s and `500` at 86400 s (`config/qc_rules.py`, `config.toml`). Thresholds
  are `dict[str, float]` (`types/domain.py:151`), never null.
- **What a stored flag does not record.** Neither an observation nor a forecast stores which
  thresholds or which cadence produced its flags. Observation cadence is **inferred** from the rows
  in the ingest window (`services/qc.py`, `infer_time_step`); the rule set in force can change
  between the verdict and a later reading. Plan 340 captures the effective **forecast** QC
  parameters per forecast as evidence; nothing does so for observations.
- **Four loaders, one pattern.** Observation rules: `flows/ingest_observations.py::_load_qc_rules`,
  `flows/onboard.py::_load_qc_rules`, `scripts/onboard.py::_load_qc_rules` (`:70`, called `:240`;
  patched in `tests/unit/scripts/test_onboard_script.py`). Forecast rules:
  `flows/run_forecast_cycle.py::_load_forecast_qc_rules`. Each: `SAPPHIRE_CONFIG` set →
  `load_*_qc_rules` (base file + `SAPPHIRE_CONFIG_OVERLAY` overlays), which **itself falls back to
  the built-in Swiss default when the merged file has no `[qc_rules]` / `[forecast_qc_rules]`
  section** (`config/qc_rules.py:262`, tested at `tests/unit/config/test_qc_rules.py:199`); unset →
  built-in default. Ingest applies no per-station override (`overrides=[]`).
- **Version labels are not rule versions.** An observation flag's `rule_version` is the
  code-generation label `"1.2"` (history `"1.0"`) for four kinds and the configured version
  (`"1.0.0"`) for `frozen_sensor` — deliberate (Plan 324). The row's `qc_rule_version` is `"1.2"`,
  `"1.2-datum"` or `"1.2-datum-skip"` (`services/qc_datum.py:21-22`); `-datum-skip` means
  `range_check` and `gross_outlier` were skipped, and water-level thresholds apply to datum-shifted
  values. Forecast flags carry the forecast QC module's own `_RULE_VERSION` (`"1.0"`). **These labels are
  not a closed set:** rows written before Plan 324 hold `"1.0"`, `"1.1-datum"`, `"1.1-datum-skip"`,
  and every calculated-station observation carries a flag `rule_id = "upstream_propagated"`,
  `rule_version = "component_derivation/v1"`, possibly with status `qc_passed`, and
  `qc_rule_version = "component_derivation/v1"` (`services/component_derivation.py:30-31`,
  `flows/ingest_observations.py:687-689`); its `detail` is JSON naming the component stations.
  A flag's `rule_version` never equals a served rule's `rule_version`. Water-level
  **forecasts** on a station without a datum skip `range_check`, `negative_value` and
  `climatology_outlier` (`services/qc_datum.py:15-17,37-40`), and a forecast stores no
  `qc_rule_version`, so that skip is invisible in a forecast response.
- **Forecast flag `detail` holds numbers.** Thresholds and forecast values
  (`services/forecast_qc.py:40,62,82,200`), and for `climatology_outlier` observation-derived
  baseline statistics.
- **Skill.** `latest_generation_predicate` (`store/skill_store.py`) scopes by station, model,
  artifact, parameter, skill source and forcing type — not time step; harmless today (one cadence).
  Combined (pooled/BMA) skill rows carry `model_artifact_id = NULL` (`db/metadata.py:1742-1748`).
  `SkillScore.forcing_type` and `phase_offset_seconds` are nullable, `lead_time_hours` is not
  (`types/skill.py`); `model_artifacts.training_period_start/_end` are NOT NULL
  (`db/metadata.py:936-937`).
- **Which artifact a station's forecast uses.** `ModelArtifactStore.fetch_active_artifact_for_station`
  (`store/model_artifact_store.py:147-199`, called from `services/run_station_forecast.py:359`):
  the station's own active artifact first, otherwise an active group artifact of a group the station
  belongs to. Assignment priority plays no part. It also loads the artifact bytes.
- **Auth surface.** `security.md:44-49` and the router comment at `api/__init__.py:64-70`: the
  modern `/api/v1/stations|forecasts|alerts` API is the one surface a consumer token reaches; every
  other data route is admin-gated. `security.md` § Input-quality visibility (Plan 253 OD-2) records
  the owner's 2026-09-04 decision that threshold-bearing forecast input-quality detail is visible to
  every authenticated role.

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
the artifact the station's forecast actually uses are served. The map shows them as fit scores and
ranks nothing.

### D3 — Nepal discharge QC starts from the Swiss thresholds. **⚖️ CLOSED — owner, 2026-09-25.**

Selection stays parameter + cadence. Nothing in the rules response says "Swiss": the map labels
the thresholds as Swiss starting values from its own knowledge of the deployment, until DHM
refinements exist. Hourly DHM data stays `qc_unchecked` until hourly rules land (323); the map shows
it as unchecked, never passed.

### D4 — map conventions. **⚖️ CLOSED — map session, 2026-09-25; corrected 2026-09-26.**

Severity is derived from code; missing baselines are shown as absent. A flag is matched to rules
by **rule set, parameter and `rule_id`**, and by cadence where it is known. For an observation the
cadence is not stored, so the map shows every matching rule of that set and parameter as a
**candidate** (e.g. both `rate_of_change` rows), and labels the thresholds as the **current**
configuration — not proof of what was in force when the flag was written. ⚠️ The map also adopted
"no time step on skill rows" on the strength of a wrong statement from this side — skill rows carry
`time_step_seconds` and `phase_offset_seconds`, and this plan serves both (T6 tells the map).

### D5 — reading `skill_scores`. **⚖️ CLOSED — owner, 2026-09-26.**

Plan 198's bar stays in force **for the snapshot**, which this plan does not touch. The new
endpoint reads `skill_scores` as the admin-only legacy routes already do, with one guard: a row
whose `eval_period_end` is after the request time is not served.

### D6 — flag `detail` text. **⚖️ CLOSED — owner, 2026-09-26: published verbatim.**

The observations endpoint already serves `detail` verbatim; T3 adds forecast flags, also verbatim
(D13). ⚠️ **Carried forward, not decided here:** the Nepal region-bundle draft forbids copying
`detail` verbatim for restricted DHM data. Under D12 the Swiss dashboard cannot see Nepal stations,
and the Nepal dashboard sees its own client's data. The exposure left is to **Nepal consumer
tokens** (third parties): observation flag `detail` today, and forecast flag `detail` after T3 —
the latter includes observation-derived baseline statistics for `climatology_outlier`. So **before
DHM observations are readable by a Nepal consumer token**, the owner decides whether observation
and forecast flag `detail` is stripped for consumers on that network. T6 records this in Plan 143
(DHM onboarding).

### D7 — all skill breakdowns. **⚖️ CLOSED — owner, 2026-09-26.**

Headline and season/flow-regime rows alike, ≈ 513 rows per station.

### D8 — sharing v3 with Plan 251. **SUPERSEDED by D1, 2026-09-26.**

### D9 — how does a reviewer see the *implications* of a threshold? **OPEN — follow-on.**

This plan lets the map show the current thresholds and which readings QC flagged. It does not let a
reviewer ask "what would a different threshold have flagged?", nor prove which thresholds produced
a past flag (D4). Re-implementing the checker in the map would drift from ours (cadence inference,
the ingest context window, `gross_outlier`'s climatological baselines).

**Recommendation:** a separate follow-on plan for a read-only server-side dry run — the real
checker, the stored readings of one station and window, proposed thresholds in the request, flags
in the response, nothing written. Not in this plan.

### D10 — serve the forecast QC rule set too. **⚖️ CLOSED — owner, 2026-09-26.**

The map reviews forecast products, and forecast flags cannot be judged against observation rules
(`range_check` exists in both sets; the other six forecast kinds would match nothing). The rules
endpoint serves both sets, each labelled, and the consumer page states the matching rule (D4).

### D11 — the map uses an admin token. **SUPERSEDED by D12, 2026-09-26.** Kept for the record.

The map was to read with an admin token and the two new routes were to be admin-only. Replaced
because an admin token reaches every admin route and every client's stations.

### D12 — each dashboard uses a reviewer token (Plan 401). **⚖️ CLOSED — owner, 2026-09-26.**

The owner asked for a dedicated identity per dashboard (BAFU/Swiss, Nepal) instead of an admin
token. Plan 401 adds a `reviewer` role: GET-only, bound to one tenant, scoped exactly like a
consumer, plus the routes gated REVIEW. The two new routes are gated with `require_reviewer`:
reviewer and admin tokens pass, a consumer gets 403. The Swiss dashboard's token sees only Swiss
stations. The token stays server-side in the map. This plan waits for Plan 401.

### D13 — T3's two fields are visible to every authenticated role. **⚖️ CLOSED — owner, 2026-09-26.**

Forecast `qc_flags` and observation `qc_rule_version` are added to routes consumers already read, so
consumers see them too. This follows the owner's 2026-09-04 decision for forecast input quality
(`security.md` § Input-quality visibility): thresholds are not sensitive, and someone looking at a
flagged forecast needs to see why. It is the **one** change to what a consumer can read in this
plan, and T4 records it in `security.md` beside that section.

## Endpoint contract

Both new routes are `GET`, read-only, registered on a router gated with `Depends(require_reviewer)`
(Plan 401). A consumer token receives 403; a reviewer token is station-scoped as on every other
route (the rule sets carry no station data). Timestamps follow the API's existing UTC convention.
Every closed set below is a `Literal` in the response model. Stored labels are **not** closed sets:
`QcFlagResponse.rule_id`, `QcFlagResponse.rule_version` and `ObservationResponse.qc_rule_version`
are plain `str` (see *Repository facts*).

**`GET /api/v1/qc/rules`** — both QC rule sets as **the serving process resolves them now**:

```text
{ observation: RuleSetBlock, forecast: RuleSetBlock }

RuleSetBlock:
  { version,                                 # the set's version as resolved
    source: "config" | "builtin_default",   # which branch actually supplied the rules; never a path or overlay name
    selection: "parameter_and_cadence",
    rules: [ { rule_id, rule_version, parameter, time_step_seconds,
               severity: "qc_failed" | "qc_suspect",   # from the code constant (T1)
               thresholds: { name: number } } ] }       # every rule, config order
```

`source` is `builtin_default` whenever the built-in rules were used — `SAPPHIRE_CONFIG` unset, **or**
set but its merged file lacks the section.

**`GET /api/v1/stations/{id}/skill[?model_id=]`** — the station's current skill:

```text
{ station_id,
  selection: "latest_generation_on_forecast_artifact",
  rows: [ { model_id, model_artifact_id, generation_id,      # generation_id null for a pre-235 baseline row
            skill_source, forcing_type,                      # forcing_type nullable
            computation_version,
            time_step_seconds, phase_offset_seconds,         # phase_offset_seconds nullable
            eval_period_start, eval_period_end,
            training_period_start, training_period_end,      # never null
            evaluated_on: "training_period" | "outside_training_period" | "overlaps_training_period",
            lead_time_hours,                                 # never null
            season, flow_regime,                             # each null when the row is not stratified by it
            metric, score, sample_size } ] }
```

Row selection:
1. The models of the station's active assignments, as `services/forecast_lab/db_sources.py::fetch_active_model_assignments`
   lists them (station and group, Plan 329) — used only to list models.
2. For each model, the artifact the station's forecast uses — the same resolution as
   `fetch_active_artifact_for_station` (station's own active artifact first, else an active group
   artifact of a group containing the station) — through a new **ID-only** store method that loads
   no bytes (T2). A model with no such artifact contributes no rows.
3. Skill rows for that model, parameter `discharge`, via `SkillStore.fetch_latest_scores` (the Plan
   235 predicate), kept only when `model_artifact_id` equals that artifact. Training period from
   `fetch_artifact_record`.
4. Dropped when `eval_period_end` is after the injected request time (D5).

Sorted by `(model_id, skill_source, forcing_type, time_step_seconds, phase_offset_seconds,
lead_time_hours, season, flow_regime, metric)`, nulls first. `evaluated_on`: eval window inside the
training period → `training_period`; disjoint → `outside_training_period`; otherwise
`overlaps_training_period`. Empty `rows` when nothing qualifies (the baselines today). Unknown or
out-of-scope station → 404.

**Typed flags and additive fields on existing responses (T3):** one `QcFlagResponse` model
(`rule_id: str`, `rule_version: str`, `status` — a `Literal` over every `QcStatus` value, `qc_passed`
included — and `detail: str | None`) replaces the untyped `qc_flags` of
`ObservationResponse` and types the new `ForecastSummary.qc_flags` (`[]` when none; inherited by
`ForecastDetail`). `ObservationResponse.qc_rule_version` (nullable, the stored value). The JSON on the
wire for observations is unchanged by the typing.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate). Every new route
is added to `tests/unit/api/test_security.py::TestRouteAuthMatrixExhaustive` as REVIEW (Plan 401).

### T1 — `GET /api/v1/qc/rules`

**Outcome:** the endpoint serves both rule sets as the serving process resolves them now, through
the same resolution functions every writing flow uses, each with a code-derived severity per rule
and a truthful `source`.

**In:**
- `config/qc_rules.py` and `config/forecast_qc_rules.py` — one public resolution function per set
  (`SAPPHIRE_CONFIG` + overlays, else built-in default) returning the rule set **and** which branch
  supplied it, including the missing-section fallback. The four loaders call them
  (behaviour-preserving): `flows/ingest_observations.py::_load_qc_rules`,
  `flows/onboard.py::_load_qc_rules`, `scripts/onboard.py::_load_qc_rules`,
  `flows/run_forecast_cycle.py::_load_forecast_qc_rules`; the duplicate bodies are removed.
- `services/qc.py` and `services/forecast_qc.py` — a severity constant keyed by `QcRuleId` /
  `ForecastQcRuleId`, covering every kind.
- A new reviewer-gated route module (e.g. `api/routes/api_review.py`, holding T2's route too),
  response models in `api/schemas.py`, registration in `api/__init__.py` with
  `Depends(require_reviewer)`, and the router comment there (`:64-70`) naming the new REVIEW routes.

**Out:** editing any rule function, threshold, rule version or selection; publishing a path; any
consumer-token access; the reviewer role itself (Plan 401).

**Pre-change:** (1) per set, a test running each rule kind against a violating input and asserting
the emitted status equals the severity constant fails on the missing constant, then passes unchanged
once it exists — the constant describes the code, it does not change it; (2) a request to the route
returns 404.

**Verification:** `uv run pytest tests/unit/services/test_qc.py tests/unit/services/test_forecast_qc.py tests/unit/config/test_qc_rules.py tests/unit/config/test_forecast_qc_rules.py tests/unit/flows/test_ingest_observations.py tests/unit/flows/test_onboard_flow.py tests/unit/flows/test_run_forecast_cycle.py tests/unit/scripts/test_onboard_script.py tests/unit/api/` — for each set, three resolution cases: config file with the section → `config` and the file's rules; config file without the section → `builtin_default`; variable unset → `builtin_default`. Each of the four loaders returns what the shared function returns (one test per loader, named in the PR). A rule set holding the same `rule_id` at two cadences returns both rows with their own thresholds. Reviewer and admin tokens → 200; consumer token → 403; no token → 401.

### T2 — `GET /api/v1/stations/{id}/skill`

**Outcome:** the endpoint serves the rows specified above, on the artifact the station's forecast
uses.

**In:** `protocols/stores.py::ModelArtifactStore` — an ID-only method (e.g.
`fetch_active_artifact_id_for_station(station_id, model_id) -> ArtifactId | None`) with the same
resolution as `fetch_active_artifact_for_station`; its implementation in
`store/model_artifact_store.py` (sharing the existing query, not copying it); the fake in
`tests/fakes/fake_stores.py`; the Protocol entry in `docs/spec/types-and-protocols.md`. A
skill-read service function using `fetch_active_model_assignments` (to list models), the new method,
`skill_store` (already in `api/deps.py`) and `fetch_artifact_record`; the route in T1's module;
response models.

**Out:** computing or re-selecting skill; `latest_generation_predicate`; skill diagrams; any ranking
or comparability judgement; any change to the snapshot or to `fetch_active_model_assignments`.

**Pre-change:** (1) a request to `GET /api/v1/stations/{id}/skill` returns 404; (2) a store test
asserting that, for a group-scope model assigned directly to a station, the ID-only method returns
the group artifact the forecast path returns — fails on the missing method; after it exists, the
route test for the same case returns that artifact's rows.

**Verification:** `uv run pytest tests/unit/api/ tests/integration/store/` plus the new test files, named in the PR — the ID-only method agrees with `fetch_active_artifact_for_station` for a station-scoped artifact, a group-scoped one, and a station holding both; superseded-artifact rows are excluded; a row with `eval_period_end` after the injected request time is dropped; stratified and headline rows both present, in the specified order; `evaluated_on` takes each of its three values; `?model_id=` filters; unknown station → 404; a reviewer token for a station outside its scope → 404.

### T3 — typed flags and additive QC fields on existing responses

**Outcome:** observations and forecasts carry typed `qc_flags`; observations carry `qc_rule_version`;
forecast summaries and details carry `qc_flags` — for every authenticated role (D13).

**In:** `api/schemas.py` (`QcFlagResponse`; the new fields), `types/forecast_summary.py`
(`ForecastSummaryRow` gains `qc_flags`), `store/forecast_store.py` (`_row_to_summary` and the summary
query), `tests/fakes/fake_stores.py` (the fake summary rows), `api/routes/api_stations.py`,
`api/routes/api_forecasts.py`, and the `ForecastSummaryRow` entry in
`docs/spec/types-and-protocols.md`.

**Out:** any filter change — which observations and forecasts are listed stays as it is.

**Pre-change:** three failing tests, one per gap: the station forecast **list** omits a suspect
forecast's `qc_flags`; the forecast **detail** omits them; an observation omits its stored
`qc_rule_version`.

**Verification:** `uv run pytest tests/unit/api/ tests/integration/store/test_forecast_summary.py` — a flagged forecast returns its flags on both routes, an unflagged one `[]`; the store's summary query returns the stored flags; an observation returns its stored `qc_rule_version`, and `null` when none is stored; a **consumer** token receives the same fields (D13); the observation `qc_flags` JSON is byte-identical to before for the same row — including a
calculated-station row (`upstream_propagated`, status `qc_passed`) and a pre-324 row (`"1.1-datum"`),
whose `qc_rule_version` is also returned unchanged.

### T4 — commit the contract for the map's routes, the consumer page, and the security record

**Outcome:** `docs/spec/api-v1-map.openapi.json` describes **only** the routes the map reads, with
the flag fields typed, is committed, and a test fails when it drifts (the snapshot schema's
mechanism, Plan 198 D15). `openapi_url` stays `None`; nothing is served.

**In:**
- The generated file, built with `fastapi.openapi.utils.get_openapi(routes=<explicit list>)` over
  exactly: `GET /api/v1/stations`, `/stations/{id}`, `/stations/{id}/observations`,
  `/stations/{id}/forecasts`, `/forecasts/{id}`, `/qc/rules`, `/stations/{id}/skill`. Because
  `require_principal` reads the header from `Request` (`api/security.py:152-181`), the generator
  sees no auth: the file **adds explicitly** a bearer `securityScheme` and a `security` requirement
  on every operation, and each operation's description states which token roles it admits
  (REVIEW for the two new routes) and that an out-of-scope station returns 404 (detail routes) or a
  filtered result (the station list). A drift test in `tests/unit/api/` asserts the file equals the
  regenerated one, that the route list holds no other route, and that every operation carries the
  bearer requirement.
- `docs/spec/api-v1-review.md`, a short consumer page: `qc_unchecked` ≠ passed; `raw` = not yet
  checked; the matching rule of D4 (candidates; the rules served are the **current** resolution, not
  the thresholds behind past flags); severity; the `qc_rule_version` labels (`"1.2"`, `"1.2-datum"`,
  `"1.2-datum-skip"`, and pre-324 `"1.0"`, `"1.1-datum"`, `"1.1-datum-skip"`) are code
  generations, not rule-set versions, and `-datum-skip` means two rules were skipped; a flag's
  `rule_version` never equals the served rule's `rule_version`, and matching never uses it;
  calculated-station flags (`upstream_propagated` / `component_derivation/v1`) match no rule and
  their `detail` names the component stations; combined (pooled/BMA) forecasts have no skill rows
  here; water-level **forecasts** without a datum skip three rules and a forecast
  response cannot show it; forecast flag versions; `evaluated_on`; the token must stay server-side.
- `docs/standards/security.md` — the two routes in the REVIEW class Plan 401 introduces, and D13's
  visibility decision beside § Input-quality visibility; the `docs/touchpoint-maps.md` API
  paragraph (new routes, the contract file).

**Out:** serving the schema; versioning the path (`/api/v2`).

**Pre-change:** N/A — new artefact; the drift test is proven by renaming one `QcFlagResponse` field
locally and watching it fail.

**Verification:** `uv run pytest tests/unit/api/` including the drift test and the route-matrix test.

### T5 — cross-plan records for Plan 341

**Outcome:** Plan 341 records (a) the two new routes are REVIEW-class internal diagnostic and carry
no forecast values, so its publication gate does not apply to them; (b) Plan 401's reviewer token
sees the same published forecast values as a same-scope consumer on ordinary routes, while only a
named, station-granted hydrologist reads unpublished candidates on 341's review routes; (c)
`docs/spec/api-v1-map.openapi.json` and its drift test cover `/stations/{id}/forecasts` and
`/forecasts/{id}` once Plan 402 lands. If 402 lands first, 341 updates that file; if 341 lands first,
402 T4 creates it from the then-current forecast schema. Neither plan creates a duplicate map
contract; (d) after T3, `qc_flags[].detail` on both forecast routes contains forecast values and,
for `climatology_outlier`, observation-derived baseline statistics — so 341's published-only rule
and metadata-only tombstones must strip or gate that field.

**In:** a note in `docs/plans/341-chwrr-forecast-publication-api.md`.

**Out:** changing 341's human-only candidate-read decision.

**Pre-change:** N/A — cross-plan record.

**Verification:** bounded inspection of the note.

### T6 — hand-over and durable records

**Outcome:** the map session has the endpoints and contract; the Nepal onboarding and Plan 251 know
what changed, in the repository and not only in a message.

**In:**
- A reply to the map session: endpoints, `docs/spec/api-v1-map.openapi.json`, the reviewer token
  (one per dashboard, issued with `python -m sapphire_flow.cli.access_tokens create-reviewer`) and
  that it must stay server-side, the D4 matching rule and its limits, the `time_step_seconds`
  correction, the in-sample label, and that the snapshot stays v2 for archived BAFU forecasts.
- **Plan 143** (DHM onboarding): the D6 precondition — before DHM observations are readable by a
  Nepal consumer token, the owner decides whether observation and forecast flag `detail` is
  stripped for consumers on that network.
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
uv run python scripts/check_readiness.py docs/plans/402-flow-map-reads-the-api.md
```

After staging deploy (orchestrator), before the map is told:

1. Run T1's two resolution functions inside `api`, `prefect-worker-ingest` (observation ingest) and
   `prefect-worker` (onboarding's observation QC and the forecast cycle, both on the default pool —
   `cli/register_deployments.py`) and compare the returned sets — equal environment variables do
   not prove equal rules (overlays and bind-mounted file contents also decide).
2. For one station, `/skill` row count equals a direct SQL count of the same selection, and every
   row's `model_artifact_id` equals what `fetch_active_artifact_for_station` currently returns for
   that row's model.
3. A Swiss reviewer token **scoped to one station** → 200 on both new routes for that station, and
   404 on `/skill` for another **existing** Swiss station; a consumer token → 403.

## Explicitly out of scope

- Any QC behaviour; the flag-version mismatch is declared, not fixed; recording which thresholds
  produced an observation flag.
- Any change to what a consumer can read beyond D13's two fields.
- The Forecast Lab snapshot (stays v2); moving archived BAFU forecasts to the API.
- QC what-if / dry run (D9); forcing and basin attributes (last priority).
- DHM/Nepal rules (303), hourly rules (323), network selection (264), overrides (269).
- `latest_generation_predicate` lacking time step.
- Combined (pooled/BMA) forecast skill — its rows have no artifact, so the selection never serves
  them; staging holds none today.

## Changelog

- 2026-09-26 — renumbered the active plan from 345 to 402 to resolve a plan-number conflict; the
  old draft filename below is retained only as history.

- 2026-09-25 — drafted as a snapshot v3 extension. Decisions D2–D4.
- 2026-09-26 — rewritten for the `/api/v1` interface (D1); file renamed from
  `345-forecast-lab-snapshot-v3-qc-and-skill.md`. Decisions D5–D7, D10, D12 (reviewer token,
  Plan 401; D11's admin token superseded), D13 (T3's fields visible to every role); D8 superseded.

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
