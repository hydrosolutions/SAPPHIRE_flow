---
status: DRAFT
created: 2026-09-25
plan: 402
title: The flow map reads the /api/v1 interface — QC rule sets and station skill endpoints, forecast QC flags, and a committed API contract
scope: Give the flow map (a review tool for our forecast products, not an operational dashboard) everything it needs to review QC and skill through the /api/v1 interface, read-only, using a tenant-bound reviewer token (Plan 401) — a reviewer-gated endpoint serving the observation AND forecast QC rule sets, a reviewer-gated per-station skill endpoint, two additive fields on existing responses (visible to every authenticated role, D13), and a committed, drift-tested OpenAPI contract covering only the routes the map reads. NOT any change to QC rules, thresholds, selection or verdicts; NOT any skill computation; NOT any other change to what a consumer token can read; NOT the Forecast Lab snapshot, which stays forecast-lab-snapshot/v2 unchanged; NOT a QC what-if/dry-run (D9); NOT forcing or basin attributes (last priority, follow-on); NOT per-station overrides (269), DHM/Nepal rules (303) or changes to network selection (264).
risk: high   # external-facing API contract (docs/workflow.md § High-risk work)
depends_on: [401]
blocks: [404]
related: [143, 147, 198, 235, 251, 253, 264, 269, 272, 303, 323, 324, 329, 340, 341, 404]
open_decisions: [D9]
closed_decisions: [D1, D2, D3, D4, D5, D6, D7, D10, D12, D13, D14]   # D2-D4 2026-09-25; the rest 2026-09-26
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
  Forecast QC: `ForecastQcRuleSet`/`ForecastQcRuleId` (`types/domain.py:262`, set at `:283`) — seven kinds;
  severity in `services/forecast_qc.py`: `negative_value`, `range_check`, `quantile_crossing` →
  `qc_failed`; `flat_ensemble`, `ensemble_spread`, `climatology_outlier`, `temporal_consistency`
  → `qc_suspect`. **`range_check` exists in both.** Within one set a `rule_id` also repeats across
  parameters and cadences with different thresholds — e.g. observation `rate_of_change` is
  `max_rate = 50` at 600 s and `500` at 86400 s (`config/qc_rules.py`, `config.toml`). Thresholds
  are `dict[str, float]` (`types/domain.py:152`), never null.
- **What a stored flag does not record.** Neither an observation nor a forecast stores which
  thresholds or which cadence produced its flags. Observation cadence is **inferred** from the rows
  in the ingest window (`services/qc.py`, `infer_time_step`); the rule set in force can change
  between the verdict and a later reading. Plan 340 captures the effective **forecast** QC
  parameters per forecast as evidence; nothing does so for observations.
- **Four loaders, one pattern.** Observation rules: `flows/ingest_observations.py::_load_qc_rules`,
  `flows/onboard.py::_load_qc_rules`, `scripts/onboard.py::_load_qc_rules` (`:70`, called `:240`;
  patched in `tests/unit/scripts/test_onboard_script.py`). Forecast rules:
  `flows/run_forecast_cycle.py::_load_forecast_qc_rules`. Each: `SAPPHIRE_CONFIG` set →
  `load_*_qc_rules` — observation rules effectively from the base file (the overlays are still read,
  but since PR #315 `load_merged_toml` rejects an overlay that sets `qc_rules`, `config/_overlay.py:22-26`), forecast rules from the base file plus
  `SAPPHIRE_CONFIG_OVERLAY` overlays — which **itself falls back to
  the built-in Swiss default when the merged file has no `[qc_rules]` / `[forecast_qc_rules]`
  section** (`config/qc_rules.py:295`, tested at `tests/unit/config/test_qc_rules.py:269`); unset →
  built-in default. Ingest applies no per-station override (`overrides=[]`).
- **Version labels.** An observation flag's `rule_version` is now its configured rule's version
  (PR #315); stored flags from before PR #315 carry the code labels `"1.2"`
  (history `"1.0"`) for four kinds, and the configured version for `frozen_sensor` (Plan 324). The row's `qc_rule_version` is `"1.2"`,
  `"1.2-datum"` or `"1.2-datum-skip"` (`services/qc_datum.py:21-22`); `-datum` means water-level
  thresholds were applied to the value minus the station datum; `-datum-skip` means no datum exists,
  so `range_check` and `gross_outlier` were skipped and nothing was shifted
  (`services/qc_datum.py:24-40`). Responses serve raw values (m a.s.l.); the datum appears only
  inside a flag's `detail`. Forecast flags carry the forecast QC module's own `_RULE_VERSION` (`"1.0"`). **These labels are
  not a closed set:** rows written before Plan 324 hold `"1.0"`, `"1.1-datum"`, `"1.1-datum-skip"`,
  and every calculated-station observation **with a non-null value** carries a flag
  `rule_id = "upstream_propagated"` (a missing derived point has no flags and a null
  `qc_rule_version`, `services/component_derivation.py:116`, `flows/ingest_observations.py:695`),
  `rule_version = "component_derivation/v1"`, possibly with status `qc_passed`, and
  `qc_rule_version = "component_derivation/v1"` (`services/component_derivation.py:30-31`,
  `flows/ingest_observations.py:694-696`); its `detail` is JSON naming the component stations.
  A flag's `rule_version`: since PR #315 every observation flag carries its configured rule's
  version (`services/qc.py:135,154,214,243,261,288`); **stored history** also holds the earlier code
  labels `"1.0"`/`"1.2"`; forecast flags carry `"1.0"`. The row's `qc_rule_version` stays a
  processing-generation marker (`docs/architecture-context.md:2331`).
- **Observation rule selection is by network too** (PR #315, Plan 264): `QcRuleParams.network`
  (`types/domain.py:153`); `QcRuleSet.rules_for` keeps rules whose network is null or matches the
  station's, and an exact network rule replaces the generic one with the same `rule_id`, parameter
  and time step (`:169-194`). Forecast selection has no network (`:287`). No network-specific rule
  is configured yet. Water-level
  **forecasts** on a station without a datum skip `range_check`, `negative_value` and
  `climatology_outlier` (`services/qc_datum.py:15-17,37-40`), and a forecast stores no
  `qc_rule_version`, so that skip is invisible in a forecast response.
- **Forecast flag `detail` holds numbers.** Thresholds and forecast values
  (`services/forecast_qc.py:40,62,82,200`), and for `climatology_outlier` observation-derived
  baseline statistics.
- **Only combined forecasts are stored when they fail QC.** A member (station) or group forecast
  whose worst flag is `qc_failed` is dropped, not stored (`services/run_station_forecast.py:590-599`,
  `services/run_group_forecast.py:310-317`); only a combined forecast is stored marked failed
  (`services/forecast_combination.py:530-536`). So `negative_value`, `range_check` and
  `quantile_crossing` rejections of member and group forecasts can never be seen through the API.
- **Skill.** `latest_generation_predicate` (`store/skill_store.py`) scopes by station, model,
  artifact, parameter, skill source and forcing type — not time step; harmless today (one cadence).
  Combined (pooled/BMA) skill rows carry `model_artifact_id = NULL` (`db/metadata.py:1742-1748`).
  `SkillScore.forcing_type` and `phase_offset_seconds` are nullable, `lead_time_hours` is not
  (`types/skill.py`); `model_artifacts.training_period_start/_end` are NOT NULL
  (`db/metadata.py:936-937`).
- **Which artifact a station's forecast uses.** `ModelArtifactStore.fetch_active_artifact_for_station`
  (`store/model_artifact_store.py:147-199`, called from `services/run_station_forecast.py:359`):
  the station's own active artifact first, otherwise an active group artifact of a group the station
  belongs to. Assignment priority plays no part. It also loads the artifact bytes. When a station
  belongs to **several** groups that each hold an active artifact for the same model, it returns one
  of them arbitrarily (`order_by` on a constant priority, `limit(1)`,
  `store/model_artifact_store.py:183-190`), while a **group** model's forecast is run once per group
  (`services/run_group_forecast.py:483`). That case is out of scope here (see below).
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
the artifact the station's forecast actually uses are served — except the two cases in *Explicitly
out of scope*. The map shows them as fit scores and ranks nothing.

### D3 — Nepal discharge QC starts from the Swiss thresholds. **⚖️ CLOSED — owner, 2026-09-25.**

Observation rules are selected by parameter, cadence and network (PR #315); until a DHM-specific
rule is configured, Nepal stations get the generic rules — the Swiss thresholds. Nothing in the
rules response says "Swiss": the map labels
the thresholds as Swiss starting values from its own knowledge of the deployment, until DHM
refinements exist. Hourly DHM data stays `qc_unchecked` until hourly rules land (323); the map shows
it as unchecked, never passed.

### D4 — map conventions. **⚖️ CLOSED — map session, 2026-09-25; corrected 2026-09-26.**

Severity is derived from code; missing baselines are shown as absent. A flag is matched to rules
by **rule set, parameter, `rule_id` and — for observations — the station's network** (from
`StationSummary`; a network rule replaces the generic one **for the same `rule_id`, parameter and
`time_step_seconds`** — at other cadences the generic rule remains a candidate), and by cadence where it is known. For an observation the
cadence is not stored, so the map shows every matching rule of that set and parameter as a
**candidate** (e.g. both `rate_of_change` rows), and labels the thresholds as the **current**
configuration — not proof of what was in force when the flag was written. ⚠️ The map also adopted
"no time step on skill rows" on the strength of a wrong statement from this side — skill rows carry
`time_step_seconds` and `phase_offset_seconds`, and this plan serves both (post-deploy step 4 tells the map).

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
reviewer and admin tokens pass, a consumer gets 403. The Swiss dashboard's token sees only the
stations it is scoped to. It may use tenant scope only when every station in its tenant belongs to
its client (Plan 401, *Why this exists*): the `sapphire` (Swiss) dashboard token stays on an explicit
station list for as long as any non-Swiss station remains in `sapphire`, whatever other tenants exist. The token stays
server-side in the map. This plan waits for Plan 401.

### D13 — T3's two fields are visible to every authenticated role. **⚖️ CLOSED — owner, 2026-09-26.**

Forecast `qc_flags` and observation `qc_rule_version` are added to routes consumers already read, so
consumers see them too. This follows the owner's 2026-09-04 decision for forecast input quality
(`security.md` § Input-quality visibility): thresholds are not sensitive, and someone looking at a
flagged forecast needs to see why. It is the **one** change to what a consumer can read in this
plan, and T4 records it in `security.md` beside that section.

### D14 — the map contract carries a version, and every change to it is visible. **⚖️ CLOSED — owner, 2026-09-26.**

The committed file's `info.version` is a hand-maintained constant (start `"1.0"`), never the package
version. Additive changes (a new field, route or enum member) bump the minor number; any other change
bumps the major number and is announced to the map session **before** deploy. **CI fails** a pull
request whose file differs from its merge-base copy without a greater version (T4). Whether a change
is an addition (minor) or anything else (major) is a **review judgement**, not machine-checked (owner,
2026-09-26). `main` has no branch protection, so the check blocks only because merges wait for green.
Plans 341 and 404, which regenerate the file, follow the same rule.

## Endpoint contract

Both new routes are `GET`, read-only, registered on a router gated with `Depends(require_reviewer)`
(Plan 401). A consumer token receives 403; a reviewer token is station-scoped as on every other
route (the rule sets carry no station data). Timestamps follow the API's existing UTC convention.
Every closed set below is a `Literal` in the response model. Stored labels are **not** closed sets:
`QcFlagResponse.rule_id`, `QcFlagResponse.rule_version` and `ObservationResponse.qc_rule_version`
are plain `str` (see *Repository facts*).

**`GET /api/v1/qc/rules`** — both QC rule sets as **the serving process resolves them now**:

```text
{ observation: ObservationRuleSet, forecast: ForecastRuleSet }

ObservationRuleSet / ForecastRuleSet (two models; `rule_id` is `Literal` over `QcRuleId` /
`ForecastQcRuleId` respectively, so an observation block cannot hold a forecast rule; observation
rows add `network: str | null` — null = applies to any network without a network rule for the same
`rule_id`, parameter and `time_step_seconds` — and the observation block's `selection` is
`"parameter_cadence_network"`, a network rule replacing the generic one for that same key; the forecast
block keeps `"parameter_and_cadence"`):
  { version,                                 # the set's version as resolved
    source: "config" | "builtin_default",   # which branch actually supplied the rules; never a path or overlay name
    selection: "parameter_cadence_network" | "parameter_and_cadence",   # observation | forecast
    rules: [ { rule_id, rule_version, parameter, time_step_seconds,
               network,                      # observation rows only; null = generic
               severity: "qc_failed" | "qc_suspect",   # from the code constant (T1)
               thresholds: { name: number | null } } ] }   # every rule, config order; a non-finite
                                                         # configured value (TOML nan/inf) is served as null
```

`source` is `builtin_default` whenever the built-in rules were used — `SAPPHIRE_CONFIG` unset, **or**
set but its merged file lacks the section.

**`GET /api/v1/stations/{id}/skill[?model_id=]`** — the station's current skill:

```text
{ station_id,
  selection: "latest_generation_on_forecast_artifact",
  rows: [ { model_id, model_artifact_id, generation_id,      # generation_id null for a pre-235 baseline row
            skill_source,      # Literal over SkillSource (types/enums.py:70)
            forcing_type,      # Literal over ForcingType (types/enums.py:65) | null
            computation_version,
            time_step_seconds, phase_offset_seconds,         # phase_offset_seconds nullable
            eval_period_start, eval_period_end,
            training_period_start, training_period_end,      # never null
            evaluated_on: "training_period" | "outside_training_period" | "overlaps_training_period",
            lead_time_hours,                                 # never null
            season, flow_regime,  # each null when the row is not stratified by it; flow_regime is
                                  # Literal["low", "high", "flood"] (FlowRegime); season is a plain str
            metric, score, sample_size } ] }        # score nullable: a non-finite stored score (e.g. an
                                                     # undefined skill score) is served as null
```

Row selection:
1. The models of the station's active assignments, as `services/forecast_lab/db_sources.py::fetch_active_model_assignments`
   lists them (station and group, Plan 329) — used only to list models.
2. For each model, the artifact the station's forecast uses — the same resolution as
   `fetch_active_artifact_for_station` (station's own active artifact first, else an active group
   artifact of a group containing the station) — through a new **ID-only** store method that loads
   no bytes (T2). A model with no such artifact contributes no rows.
3. Skill rows for that model, parameter **`discharge` only**, via `SkillStore.fetch_latest_scores` (the Plan
   235 predicate), kept only when `model_artifact_id` equals that artifact. Training period from
   `fetch_artifact_record`.
4. Dropped when `eval_period_end` is after the injected request time (D5).

Sorted by `(model_id, skill_source, forcing_type, time_step_seconds, phase_offset_seconds,
lead_time_hours, season, flow_regime, metric)`, nulls first. `evaluated_on` treats both periods as
**closed** intervals `[start, end]`: eval window inside the training period → `training_period`;
no shared instant (eval ends before training starts, or starts after training ends) →
`outside_training_period`; otherwise — including an eval window that starts exactly at
`training_period_end` — `overlaps_training_period`. Closed intervals never overstate independence. Empty `rows` when nothing qualifies (the baselines today). Unknown or
out-of-scope station → 404.

**Typed flags and additive fields on existing responses (T3):** one `QcFlagResponse` model
(`rule_id: str`, `rule_version: str`, `status: Literal["qc_passed", "qc_suspect", "qc_failed", "qc_unchecked"]` — the
values a `QcFlag` can hold (`types/domain.py:94-101` rejects `raw` and `missing`) — and `detail: str | None`) replaces the untyped `qc_flags` of
`ObservationResponse` and types the new `ForecastSummary.qc_flags` (`[]` when none; inherited by
`ForecastDetail`). `ObservationResponse.qc_rule_version` (nullable, the stored value). The row-level
`qc_status` of `ObservationResponse` and `ForecastSummary` is typed as a `Literal` over every
`QcStatus` value — it can hold `raw` and `missing`, which a flag cannot. The JSON on the wire for
observations is unchanged by the typing.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate). Every new route
is added to `tests/unit/api/test_security.py::TestRouteAuthMatrixExhaustive` as REVIEW (Plan 401).

### T1 — `GET /api/v1/qc/rules`

**Outcome:** the endpoint serves both rule sets as the serving process resolves them now, through
the same resolution functions every writing flow uses, each with a code-derived severity per rule
and a truthful `source`.

**In:**
- `config/qc_rules.py` and `config/forecast_qc_rules.py` — one public resolution function per set
  (`SAPPHIRE_CONFIG` + overlays, else built-in default). For observation rules the shared part is
  the code from `load_merged_toml(path, _resolve_overlay_paths())` onward: it takes a path, keeps the
  PR #315 overlay rejection, and returns the rule set plus `source`, decided at the `qc_rules` section
  check. `load_qc_rules` keeps its `config_path` parameter and its raise when nothing is set (tested,
  `tests/unit/config/test_qc_rules.py:279-287`) and calls that shared part; the public resolution
  function reads `SAPPHIRE_CONFIG`, returns `builtin_default` when it is unset, and otherwise calls
  the same shared part. It returns the rule set **and** which branch
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

**Verification:** `uv run pytest tests/unit/services/test_qc.py tests/unit/services/test_forecast_qc.py tests/unit/config/test_qc_rules.py tests/unit/config/test_forecast_qc_rules.py tests/unit/flows/test_ingest_observations.py tests/unit/flows/test_onboard_flow.py tests/unit/flows/test_run_forecast_cycle.py tests/unit/scripts/test_onboard_script.py tests/unit/api/` — for each set, three resolution cases: config file with the section → `config` and the file's rules; config file without the section → `builtin_default`; variable unset → `builtin_default`. Each of the four loaders returns what the shared function returns (one test per loader, named in the PR). A rule set holding the same `rule_id` at two cadences returns both rows with their own thresholds; a set holding a generic and a network-specific rule for the same `rule_id` returns both rows with their `network`; a configured `nan`/`inf`/`-inf` threshold is served as `null` with valid JSON, and QC execution is unchanged; an observation block cannot validate a forecast `rule_id`. An overlay that sets `[qc_rules]` is still rejected through the shared function. Reviewer and admin tokens → 200; consumer token → 403; no token → 401.

### T2 — `GET /api/v1/stations/{id}/skill`

**Outcome:** the endpoint serves the rows specified above, on the artifact the station's forecast
uses — except the two cases in *Explicitly out of scope*.

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

**Verification:** `uv run pytest tests/unit/api/ tests/integration/store/` plus the new test files, named in the PR — the ID-only method agrees with `fetch_active_artifact_for_station` for a station-scoped artifact, a group-scoped one, and a station holding both; superseded-artifact rows are excluded; a row with `eval_period_end` after the injected request time is dropped; stratified and headline rows both present, in the specified order; `evaluated_on` takes each of its three values, and an eval window starting exactly at `training_period_end` is `overlaps_training_period`; a stored NaN score is served as `null` and the JSON is valid; `?model_id=` filters; unknown station → 404; a reviewer token for a station outside its scope → 404.

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
- One generator function, e.g. `api/map_contract.py::build_map_openapi()`, called both by the
  command that writes the file and by the drift test, so the post-processing below is shared code.
  It builds the document with `fastapi.openapi.utils.get_openapi(routes=<explicit list>)` over
  exactly: `GET /api/v1/stations`, `/stations/{id}`, `/stations/{id}/observations`,
  `/stations/{id}/forecasts`, `/forecasts/{id}`, `/qc/rules`, `/stations/{id}/skill`. Because
  `require_principal` reads the header from `Request` (`api/security.py:152-181`), the generator
  sees no auth: the file **adds explicitly** a bearer `securityScheme` and a `security` requirement
  on every operation, and each operation's description states which token roles it admits
  (REVIEW for the two new routes) and that an out-of-scope station returns 404 (detail routes) or a
  filtered result (the station list). A drift test in `tests/unit/api/` asserts the file equals the
  regenerated one, that the route list holds no other route, that every operation carries the
  bearer requirement, and that `info.version` is the D14 constant. **D14's CI check**:
  `tools/check_map_contract_version.py` (beside the repo's other CI-only gates), run as a **named** step (`Check map contract version`) in
  the `lint` job of `.github/workflows/ci.yml` on `pull_request` only, with `fetch-depth: 0` and
  `--base-ref "${{ github.event.pull_request.base.sha }}"`; the script compares the file with its
  copy at `git merge-base HEAD <base>`. Rules: a file absent at the base passes; an unchanged file
  passes; a changed file passes only if its `info.version` is greater, compared as integer
  `(major, minor)` tuples. Direct pushes to `main` are not checked — code, and so this generated
  file, only reaches `main` through a pull request. The step is also recorded in
  `docs/standards/cicd.md`'s `ci.yml` step table and in `tools/gate_parity_check.py`'s
  `CI_ONLY_ALLOWLIST`. The generator also adds the
  400 (on the routes that parse query values, `api/routes/api_stations.py:136-151`) and 401/403/404
  responses (403 on the two REVIEW routes) referencing the existing `ErrorResponse`
  (`api/schemas.py:18`), since those errors use `{"error": …, "detail": null}` (`api/errors.py:10`)
  while 422 keeps FastAPI's `{"detail": [...]}`.
- `docs/spec/api-v1-review.md`, a short consumer page: the D14 version rule; the error envelope
  (400/401/403/404) and the 422 exception; query conventions — ISO-8601 times with naive meaning UTC, `end` exclusive,
  the `parameter` values, the forecast list's default window (last 7 days to request time), `limit`
  ceilings, observations unpaginated; the skill metrics the service emits, each with its unit and
  whether higher or lower is better, and that `season` names are deployment-configured; that
  `/qc/rules` is deployment-global, the same document for every client's token; `qc_unchecked` ≠
  passed; `raw` = not yet checked (so `qc_flags: []` on a `raw` forecast is not a pass); the matching rule of D4 (candidates; the rules served are the **current** resolution, not
  the thresholds behind past flags); severity; the `qc_rule_version` labels (`"1.2"`, `"1.2-datum"`,
  `"1.2-datum-skip"`, and pre-324 `"1.0"`, `"1.1-datum"`, `"1.1-datum-skip"`) are code
  generations, not rule-set versions, and `-datum-skip` means two rules were skipped; since PR #315 a new observation flag's `rule_version` is its configured rule's version, but
  stored history holds code labels (`"1.0"`, `"1.2"`) and forecast flags carry `"1.0"` — so matching
  never relies on it; observation rules can be network-specific: a network rule replaces the
  generic one only for the same `rule_id`, parameter and time step, and the matching rule uses the
  station's network;
  `score: null` means the skill score is undefined for that stratum; a `null` threshold is a
  non-finite configured value; on `-datum` rows and on water-level forecasts at a station with a
  datum, thresholds apply to value minus the station datum, visible only in a flag's `detail`;
  **a failed member or group forecast is never stored**, so stored `qc_failed` forecasts are
  combined forecasts only — the absence of such rejections says nothing about the thresholds;
  calculated-station flags (`upstream_propagated` / `component_derivation/v1`) match no rule and
  their `detail` names the component stations; combined (pooled/BMA) forecasts have no skill rows
  here; water-level **forecasts** without a datum skip three rules and a forecast
  response cannot show it; forecast flag versions; `evaluated_on`; the token must stay server-side;
  **on a tenant where Plan 341's publication gate is active, the reviewer token sees only published
  forecasts, and a `qc_failed` forecast is never published — so forecast QC failures are not visible
  through the forecast routes there**. Once Plan 404 lands, rejected **member and group** forecasts
  appear on its own route (rule fields only — no values or flag `detail` — on a gated tenant); a rejected **combined**
  forecast is visible on a gated tenant only through Plan 341's human review routes, never to a
  reviewer token; for a station in several groups that each hold an active artifact of one model,
  the skill rows served are one of those artifacts' and may not be the one the forecast used; and
  for a model reached through a group assignment while the station also holds an active
  station-scoped artifact of it, the forecast uses the group artifact but the skill rows come from
  the station artifact.
- `docs/standards/security.md` — the two routes in the REVIEW class Plan 401 introduces, D13's
  visibility decision beside § Input-quality visibility, and D6's precondition next to it (before
  DHM observations are readable by a Nepal consumer token, the owner decides whether flag `detail` is
  stripped for consumers on that network); the `docs/touchpoint-maps.md` API
  paragraph (new routes, the contract file); `docs/conventions.md` § API routes (`:40-65`, the route
  list `security.md:3` points to) — both routes, marked REVIEW (reviewer or admin token).
- **If Plan 341's route inventory (its T3) exists on the base branch:** classify
  `GET /api/v1/qc/rules` and `GET /api/v1/stations/{id}/skill` there as REVIEW diagnostics with no
  publication filter, and extend its test — Plan 341 hands this to this plan when 341 lands first.

**Out:** serving the schema; versioning the path (`/api/v2`).

**Pre-change:** N/A — new artefacts. The drift test is proven by renaming one `QcFlagResponse` field
locally and watching it fail; the version check is proven by its own cases below, where the same
changed file fails with an unchanged version and passes with a greater one.

**Verification:** `uv run pytest tests/unit/api/ tests/unit/tools/test_check_map_contract_version.py`
including the drift test, the route-matrix test and the version-check cases — file absent at base →
pass; unchanged → pass; changed with the same version → fail; changed with a greater version → pass;
`1.9` → `1.10` counts as greater; `uv run python tools/gate_parity_check.py` lists the new step as `allowlisted-ci-only` under the key
`("ci", "Check map contract version")` and reports no drift row beyond the base branch's (it already
reports unrelated drift today); and a bounded inspection that the consumer page carries every caveat listed in In, and that
the `security.md` REVIEW-class and D13 entries, the `touchpoint-maps.md` paragraph and the
`conventions.md` routes, the D6 precondition, the `ci.yml` step with its base fetch, the
`cicd.md` step-table row and the `CI_ONLY_ALLOWLIST` entry are in the branch diff; that the two routes are
classified in Plan 341's route inventory if it exists; and that Plan 341 still states, by content:
the REVIEW-diagnostic classification, reviewer tokens see published values only, the contract's
creation order, the gating of `qc_flags[].detail`, and that updates to the map contract follow D14.

### T6 — hand-over and durable records

**Outcome:** the Nepal onboarding, Plan 251 and the plan index know what changed, in the
repository. Messages to other sessions are not this task's: they are orchestrator steps after the
staging checks (see *After staging deploy*).

**In:**
- **Plan 143** (DHM onboarding): the D6 precondition — before DHM observations are readable by a
  Nepal consumer token, the owner decides whether observation and forecast flag `detail` is
  stripped for consumers on that network.
- A status note in Plan 251: "Plan 402 T3 adds forecast `qc_flags` to `/api/v1`, which may cover
  this plan's purpose; the owner decides."
- The existing `docs/plans/README.md` entry for 402, updated to its implemented status.

**Out:** editing another session's worktree.

**Pre-change:** N/A — documentation and hand-over.

**Verification:** the Plan 143 and 251 notes and the updated README entry are in the feature-branch
diff.

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
   not prove equal rules (bind-mounted file contents, and overlays for forecast rules, also decide). **Pass:** the
   observation sets from all three are equal, and the forecast sets from `api` and `prefect-worker`
   are equal. Otherwise stop before step 4 and escalate.
2. For one station, `/skill` row count equals a direct SQL count of the same selection, and every
   row's `model_artifact_id` equals what `fetch_active_artifact_for_station` currently returns for
   that row's model — choosing a station that is not in several groups holding an active artifact of
   the same model (there the pick is arbitrary), and whose group-assigned models have no active
   station-scoped artifact.
3. A reviewer token (`--tenant sapphire`) **scoped to one station** → 200 on both new routes for
   that station, and 404 on `/skill` for another **existing** station; a temporary consumer token
   created for this check → 403. Then **delete both tokens by id** (Plan 401's single-token
   procedure — never the role-wide statements).
4. Only then does the orchestrator send the reply to the map session: endpoints,
   `docs/spec/api-v1-map.openapi.json`, the reviewer token (one per dashboard, issued with
   `python -m sapphire_flow.cli.access_tokens create-reviewer`) and that it must stay server-side,
   the D4 matching rule and its limits, `score: null`, that QC-rejected member and group forecasts
   are not stored (Plan 404), the `time_step_seconds` correction, the in-sample label, and that the
   snapshot stays v2 for archived BAFU forecasts — checked against the committed contract file.
   **The raw token is never in the reply.** It also tells the region-bundle v3 draft's owning
   session that QC and skill are station-level `/api/v1` endpoints, plus the D6 carry-forward.
5. The orchestrator or owner issues one reviewer token per dashboard — an explicit station list
   unless every station in its tenant belongs to its client (Plan 401) — and delivers the raw key out of band into the map's server-side secret store —
   never in a message or document.

## Explicitly out of scope

- Any QC behaviour; the code labels in stored (pre-PR-#315) flags stay as they are; recording which
  thresholds produced an observation flag.
- Any change to what a consumer can read beyond D13's two fields.
- The Forecast Lab snapshot (stays v2); moving archived BAFU forecasts to the API.
- QC what-if / dry run (D9); forcing and basin attributes (last priority).
- DHM/Nepal rules (303), hourly rules (323), changes to network selection (264, shipped in PR #315),
  overrides (269).
- `latest_generation_predicate` lacking time step.
- Combined (pooled/BMA) forecast skill — its rows have no artifact, so the selection never serves
  them; staging holds none today.
- Skill for any parameter other than discharge.
- Surfacing QC-rejected member and group forecasts — they are not stored today. Plan 404
  (owner, 2026-09-26) keeps them in a separate record, served by its own REVIEW route.
- A station in several groups that each hold an active artifact for the same model: the forecast
  path itself picks one arbitrarily, so "the artifact the forecast uses" is not defined there. A
  possible fault in forecasting, to be investigated separately (owner informed 2026-09-26).
- A group-assigned model at a station that also holds an active station-scoped artifact of it: the
  forecast uses the group artifact, the skill endpoint the station one; recorded on the consumer page.

## Changelog

- 2026-09-26 — renumbered from 345 to 402 to resolve a plan-number conflict (owner); the old
  filenames below are history only.
- 2026-09-25 — drafted as a snapshot v3 extension. Decisions D2–D4.
- 2026-09-26 — rewritten for the `/api/v1` interface (D1); file renamed from
  `345-forecast-lab-snapshot-v3-qc-and-skill.md`. Decisions D5–D7, D10, D12 (reviewer token,
  Plan 401; D11's admin token superseded), D13 (T3's fields visible to every role); D8 superseded.
  The DHM gauges get their own tenant (Plan 401 D4).
  After the owner-commissioned API-contract review: D14 (the contract's version rule), the error
  envelope and query conventions in the contract and consumer page, typed row-level `qc_status`.
  T5 removed: Plan 341 already records its cross-plan facts (PR #313); its one remaining duty moved
  to T4.
  QC-rejected member and group forecasts are not stored; surfacing them is Plan 404
  (owner, 2026-09-26).

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1", "T2", "T3"], "parallel": false},
    {"id": "phase-2", "tasks": ["T4"], "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T6"], "depends_on": ["phase-2"]}
  ]
}
```
