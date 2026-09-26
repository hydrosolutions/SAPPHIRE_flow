---
status: DRAFT
created: 2026-09-25
plan: 345
title: Forecast Lab snapshot v3 — show what QC rejected, the rules that did it, and the current skill scores
scope: Publish, read-only, three things the Forecast Lab snapshot withholds today — every non-passed observation in the window with its stored QC flags, the observation QC rule set as the exporting process resolves it, and the current skill scores of each active model version — as a new format version, forecast-lab-snapshot/v3, which replaces v2. NOT any change to QC rules, thresholds, selection or verdicts; NOT any skill computation or recompute; NOT the Nepal region bundle (no exporter exists; its carrier is a separate draft); NOT per-station QC overrides (Plan 269); NOT network-specific rules (Plans 264/303); NOT the `verification` block, which stays a sentinel.
depends_on: []
blocks: []
related: [198, 204, 235, 251, 264, 269, 272, 303, 323, 324, 329]
open_decisions: []
closed_decisions: [D1, D2, D3, D4, D5, D6, D7, D8]   # D1-D4 2026-09-25, D5-D8 2026-09-26
source: 2026-09-25 — request from the SAPPHIRE-flow-map session (primary audience Nepal DHM, who need to see which readings QC rejected and why, and compare models); owner decisions the same day. Measured on origin/main 6c77c666 and the staging database.
---

# Plan 345 — Forecast Lab snapshot v3: QC flags, the rule set, and current skill

## Status

**DRAFT — not reviewed.** All eight decisions are closed by the owner (D1–D4 on 2026-09-25,
D5–D8 on 2026-09-26). Not implementable until an independent review is complete and the
orchestrator sets READY.

## Why this exists

The map's consumer is DHM: they need to see **which observations QC rejected and why**, so they
can say which rules are too strict or too loose, and they want to look at models' skill side by
side. The Swiss Forecast Lab should show the same view. The snapshot can show none of it:

- `services/forecast_lab/db_sources.py::fetch_observation_window` reads `qc_passed` only, and
  `ObservationPointSchema.qc_status` is `Literal["qc_passed"]` — a rejected reading, and the
  reason, never leave the backend.
- Nothing in the document names a QC rule or threshold, so a reader cannot judge "too strict".
- `build_snapshot()` **structurally never reads `skill_scores`** (Plan 198 D3; asserted in
  `tests/unit/services/forecast_lab/test_snapshot.py`). D5 lifts that bar for `skill_scores` only.

Nothing new has to be computed. The four-key flag `{rule_id, rule_version, status, detail}` is
already stored (`store/observation_store.py::_serialize_flags`) and already served by
`GET /api/v1/stations/{id}/observations`.

## What is measured (2026-09-25)

Staging database, read-only, discharge only:

| fact | value |
|---|---|
| last 30 days, measured discharge rows | 451 294 `qc_passed`, 7 170 `qc_suspect`, 130 `qc_unchecked`, 2 `raw`, 0 `qc_failed`, 0 `missing`; no null values |
| models with any skill rows | **one** — `nwp_rainfall_runoff`, `skill_source = hindcast_reanalysis`, `time_step_seconds = 86400`, `phase_offset_seconds = 0`. No rows for `persistence_fallback` or `climatology_fallback`. |
| current-generation rows on superseded model versions | 139 of 141 stations have a second, current-by-generation set of scores on a **superseded** artifact, on a different window (2020-01-01 → 2022-12-21 or → 2026-08-28). The generation rule alone does not exclude them. |
| active artifact eval window vs training period | 140 stations: eval 2010-01-01 → 2020-12-30, training 2010-01-01 → 2020-12-31. One station: eval 2019-05-18 → 2020-12-30, same training period. **Every active score is in-sample.** |
| skill rows per station, active artifact | ≈ 513: 44 unstratified (5 lead times × 9 metrics), the rest by season and/or flow regime |
| hindcast rows dated in the future (Plan 198's "2 752 known-bad rows") | **0** of 715 103 |

Repository (origin/main `6c77c666`):

- Severity is fixed per rule kind in code, not configured: `range_check` emits `qc_failed`; the
  other four emit `qc_suspect` (`services/qc.py`).
- Flag `rule_version` is **not** the rule definition's version for four of five kinds: they write
  the code-generation label `_RULE_VERSION = "1.2"` (Plan 324; history also holds `"1.0"`), while
  `frozen_sensor` writes the configured `rule.rule_version` (`"1.0.0"`) — declared deliberate by
  Plan 324. The map therefore matches flags to rules by `rule_id` only (D4).
- The ingest path loads rules via `flows/ingest_observations.py::_load_qc_rules` — the
  `SAPPHIRE_CONFIG` file plus overlays, or the built-in Swiss default when that variable is unset
  or the file has no `[qc_rules]`. No overlay on main touches `[qc_rules]`.
- Ingest always passes `overrides=[]` to the checker. No per-station override is ever applied
  (Plan 269 is DRAFT), so the request's `overrides` field could only ever be empty.
- `skill_scores` **does** carry `time_step_seconds` and `phase_offset_seconds` (the orchestrator's
  first reply said otherwise; corrected in T6). `latest_generation_predicate`'s scope does not
  include time step — harmless today (one cadence exists) and not this plan's to change.
- Flag `detail` is free text holding observation values, neighbour values and, for
  `gross_outlier`, the station's baseline mean and standard deviation.

## Owner decisions

### D1 — one v3, shared with Plan 251. **⚖️ CLOSED — owner, 2026-09-25.**

A single `forecast-lab-snapshot/v3` carries this plan's blocks and Plan 251's combined-forecast
QC fields. v3 **replaces** v2, as v2 replaced v1: the only known consumer asked to cut over, and
`additionalProperties: false` on both sides means a widened v2 would break its validator anyway.
How the two plans share it is D8.

### D2 — publish in-sample skill, labelled, active artifact only. **⚖️ CLOSED — owner, 2026-09-25.**

Every row carries its artifact's `training_period_start`/`training_period_end` and a derived
label (T4). Only rows on the model's **active** artifact are exported. The map shows them as fit
scores and ranks nothing.

### D3 — Nepal discharge QC uses the Swiss thresholds as its starting point. **⚖️ CLOSED — owner, 2026-09-25.**

Rules keep being picked by parameter + cadence as today; `qc_rule_set` is exported so the map can
label them as Swiss. DHM refines from there; nothing waits for a DHM rule set. Hourly DHM data
stays `qc_unchecked` until hourly rules land (Plan 323), and the map shows it as unchecked, never
passed. **This plan carries no Nepal station** — the snapshot's eligibility is `network = bafu` —
so D3 reaches DHM through the region-bundle v3 carrier (T6 hands it the block shapes).

### D4 — map-side conventions. **⚖️ CLOSED — map session, adopted 2026-09-25.**

Flags are matched to rules by `rule_id` only; severity is derived from code; baselines with no
rows are shown as absent. ⚠️ The map also adopted "no time step on skill rows" on the strength of
a wrong statement from this side; this plan exports `time_step_seconds` and T6 tells the map.

### D5 — may the snapshot read `skill_scores` at all? **⚖️ CLOSED — owner, 2026-09-26: YES, `skill_scores` only, with the guard.**

Plan 198 D3 (owner decision O7.1) made the snapshot structurally unable to read `skill_scores`,
`hindcast_forecasts` and `hindcast_values`, for two stated reasons. Checked:

1. *Plan 111 Gate G1 bars a BAFU-derived benchmark.* G1 is about lawfully obtaining and
   publishing **BAFU's forecasts**. `skill_scores` scores **our** hindcasts against BAFU
   observations, which the snapshot already publishes. The BAFU-vs-SAPPHIRE comparison stays the
   `verification` sentinel, untouched.
2. *2 752 known-bad future-dated hindcast rows.* Staging holds **0** today, and this plan reads
   `skill_scores` only. As a structural guard, T4 drops any row whose `eval_period_end` is after
   the snapshot's `data_cutoff_at`, and the test keeps asserting that the two hindcast tables are
   never read.

**Decision:** the bar is lifted for `skill_scores` only, with that guard. This explicitly reverses
the `skill_scores` part of Plan 198 D3; the hindcast tables stay unreadable.

### D6 — publish each flag's free-text `detail`? **⚖️ CLOSED — owner, 2026-09-26: YES, verbatim, in the Swiss snapshot.**

`detail` holds observation values, neighbour values and a baseline mean and standard deviation.
For the Swiss snapshot all of these come from public BAFU observations, and the snapshot is
served only behind a scoped token. The region-bundle v3 draft forbids copying `detail` verbatim
because DHM data is restricted, so the two carriers would differ.

**Decision:** `detail` is published verbatim in the Swiss snapshot — it is the "why" DHM asked
for, and `rule_id` plus the threshold alone cannot show a spike's neighbours. The Nepal carrier
keeps its own rule; the two regions will differ here, deliberately.

### D7 — which skill rows? **⚖️ CLOSED — owner, 2026-09-26: ALL breakdowns.**

The active artifact holds ≈ 513 rows per station, about 72 000 over 141 stations — several times
the size of the rest of the document. Unstratified rows (`season` and `flow_regime` both null)
are 44 per station: every lead time × every metric.

**Decision:** export every row the selection keeps — headline and stratified alike — so
`season` and `flow_regime` carry real values where a row is stratified. (The orchestrator had
recommended headline rows only.) Consequence: the all-station document grows by roughly 72 000
rows. Nothing caps it; the post-deploy gate measures the size and build time and reports both to
the map, and a scoped (per-station) request stays small.

### D8 — how v3 is shared with Plan 251. **⚖️ CLOSED — owner, 2026-09-26: (a), absorb 251 T2.**

Plan 251 is DRAFT and unreviewed, with three open decisions. Its T2 adds `qc_status` and
`qc_flags` to the two combined-forecast objects; its T3 stops hiding a failed combination.

- **(a) Chosen — absorb 251 T2 here as T5.** The fields exist on the stored forecast
  (`types/forecast.py::OperationalForecast.qc_status`/`qc_flags`); populating them is small. v3
  then ships complete without waiting on 251. 251 keeps T3, which shows the rejected combination
  inside v3 with no format change. Its D1 (replace) and D2 (required, nullable) are settled by
  this plan; its D3 (what `available` means) stays with it.
- (b) Rejected — 345 changes the version and v3 waits for 251 T2; the map would wait on an
  unreviewed plan.

## Block shapes (the contract this plan implements)

All new fields are **required**. Nullability is stated per field. Timestamps follow the existing
RFC 3339 `Z` rule.

**Per station, beside `observations`** — `observation_qc: {...} | null`, null exactly when
`observations` is null:

```text
observation_qc:
  window_start, window_end     # identical to observations.window_start/_end
  parameter: "discharge"
  native_step_seconds          # identical to observations.native_step_seconds
  counts: { qc_passed, qc_suspect, qc_failed, qc_unchecked, missing, raw }   # every measured row in the window
  flagged: [                   # every row whose qc_status != qc_passed, valid_time ascending
    { valid_time,
      value,                   # finite float, or null when the stored value is null or non-finite
      qc_status,               # qc_suspect | qc_failed | qc_unchecked | missing | raw
      qc_rule_version,         # the row's stored qc_rule_version, nullable
      flags: [ { rule_id, rule_version, status, detail } ] } ]   # as stored; may be empty
```

`observations.points` is unchanged: passed, measured, finite values only. `counts.qc_passed`
counts rows, so it can exceed `len(points)` by the passed rows whose value is non-finite.
There is no `overrides` field: none is ever applied (see *What is measured*), and Plan 269 owns
adding one.

**Root** — `qc_rule_set`:

```text
qc_rule_set:
  version                      # QcRuleSet.version as resolved
  source: "config" | "builtin_default"
  selection: "parameter_and_cadence"
  rules: [ { rule_id, rule_version, parameter, time_step_seconds,
             severity,         # qc_failed | qc_suspect, derived from code
             thresholds: { name: number | null } } ]   # every rule in the resolved set, config order
```

`source` names the kind only. The config path and overlay file names are internal and are not
published (`docs/standards/security.md`).

**Per station** — `skill`:

```text
skill:
  selection: "latest_generation_on_active_artifact"
  rows: [                      # empty when no row qualifies; sorted by (model_key, time_step_seconds, lead_time_hours,
                               #   season, flow_regime, metric), nulls first
    { model_key, model_artifact_id, generation_id,   # generation_id null for a pre-Plan-235 baseline row
      skill_source, forcing_type, computation_version,
      time_step_seconds, phase_offset_seconds,
      eval_period_start, eval_period_end,
      training_period_start, training_period_end,    # null when the artifact record has none
      evaluated_on: "training_period" | "outside_training_period" | "overlaps_training_period" | "unknown",
      lead_time_hours, season, flow_regime,          # each null when the row is not stratified by it (D7)
      metric, score, sample_size } ]
```

`evaluated_on` is `training_period` when the eval window lies inside the training period,
`outside_training_period` when they are disjoint, `overlaps_training_period` otherwise, and
`unknown` when either training bound is null.

Rows come from the station's **active assignments** as `fetch_active_model_assignments` resolves
them (station and group, Plan 329), parameter `discharge`, selected by
`latest_generation_predicate` (via `SkillStore.fetch_latest_scores`), then kept only when
`model_artifact_id` is an **active** artifact for that assignment's scope (station, or group for
a group assignment), and dropped when `eval_period_end > data_cutoff_at` (D5).

**Combined forecast (D8)** — both `CombinedForecastMembersSchema` and
`CombinedForecastQuantilesSchema` gain `qc_status` and `qc_flags` (same four-key flag shape).

`snapshot_id` changes prefix from `fls2-` to `fls3-`.

## Tasks

Every code task carries the Task Exit Gate (`docs/workflow.md` § Task Exit Gate). T2–T5 each
change the Pydantic models, so each one regenerates the committed JSON Schema and updates the
example fixture and the matching spec section in the same change. They run in order on one branch.

### T1 — Move the format from v2 to v3, shape unchanged

**Outcome:** every producer and checker stamps `forecast-lab-snapshot/v3`; the committed schema
is `docs/spec/forecast-lab-snapshot-v3.schema.json` (the v2 file is removed, as v1's was);
`snapshot_id` uses `fls3-`.

**In:** the sites in Plan 251's table — `api/forecast_lab_schemas.py`,
`api/routes/forecast_lab.py`, `cli/export_forecast_lab.py`, `services/forecast_lab/snapshot.py`,
the schema file, `docs/spec/forecast-lab-snapshot.md` (title, the cutover paragraph, the
`Document shape` path), `tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json`,
`tests/unit/api/test_forecast_lab_schema.py`, `tests/unit/services/forecast_lab/test_snapshot.py`.
Re-grep `forecast-lab-snapshot/v2`, `forecast-lab-snapshot-v2` and `fls2-` over `src/ tests/ docs/`
(excluding `docs/plans/archive/`) before closing.

**Out:** any new field.

**Pre-change:** change the schema-sync test's expected version to v3 first; it fails on the
stamped value.

**Verification:** `uv run pytest tests/unit/api/test_forecast_lab_schema.py tests/unit/services/forecast_lab/test_snapshot.py tests/unit/cli/test_export_forecast_lab.py`, and the re-grep returns only deliberate historical mentions (the spec's cutover history).

### T2 — `observation_qc` per station

**Outcome:** each station entry carries `observation_qc` as specified above, and
`observations.points` is identical to what v2 served for the same data.

**In:** `services/forecast_lab/db_sources.py` (a read of every measured discharge row in the
window, any status, through `ObservationStore.fetch_observations(qc_status=None, source=MEASURED)`),
`services/forecast_lab/snapshot.py`, `api/forecast_lab_schemas.py`, schema/fixture/spec.
`fetch_observation_window`'s filter stays as it is.

**Out:** any change to what `observations` contains or to the `availability`/`status` roll-ups.
Per-station overrides. Any write.

**Pre-change:** a snapshot test over a fake store holding one passed, one suspect, one failed, one
unchecked and one raw row fails because the station entry has no `observation_qc`.

**Verification:** `uv run pytest tests/unit/services/forecast_lab/test_snapshot.py tests/unit/services/forecast_lab/test_db_sources.py tests/unit/api/test_forecast_lab_schema.py` — cases: the five-status window gives the right counts and four `flagged` entries in time order, carrying their stored flags unchanged; an unchecked row with no flags appears with `flags: []`; a non-finite value is served as `null` and still counted; `points` is unchanged.

### T3 — `qc_rule_set` at the root

**Outcome:** the document carries the rule set resolved exactly as ingest resolves it, with a
code-derived severity per rule.

**In:** `config/qc_rules.py` — move ingest's resolution (`SAPPHIRE_CONFIG` + overlays, else the
built-in default) into one public function that also reports which of the two it used;
`flows/ingest_observations.py::_load_qc_rules` calls it (a behaviour-preserving refactor).
`services/qc.py` — a severity constant keyed by `QcRuleId`, covering every kind. Snapshot
assembly, schema, fixture, spec, and both `ForecastLabStores` callers (route, CLI) or the
`build_snapshot` signature — whichever passes the rule set in.

**Out:** editing any rule function, threshold, rule version or selection. Publishing a file path.

**Pre-change:** (1) a test that runs each rule kind against a violating series and asserts the
emitted status equals the new severity constant fails on the missing constant — then passes
unchanged once it exists, proving the constant describes the code rather than changing it;
(2) a snapshot test expecting `qc_rule_set` fails on the missing field.

**Verification:** `uv run pytest tests/unit/services/test_qc.py tests/unit/config/test_qc_rules.py tests/unit/flows/test_ingest_observations.py tests/unit/services/forecast_lab/test_snapshot.py tests/unit/api/test_forecast_lab_schema.py` — cases: with `SAPPHIRE_CONFIG` set, the exported rules equal the file's, with `source: "config"`; unset, the built-in default with `source: "builtin_default"`; ingest's loaded rule set is unchanged in both cases.

### T4 — `skill` per station

**Outcome:** each station entry carries `skill` as specified above, restricted per D2, D5 and D7.

**In:** `ForecastLabStores` gains `skill_store` — **seven constructors plus
`tests/unit/api/conftest.py::fake_stores`** (`docs/touchpoint-maps.md` names them).
`services/forecast_lab/db_sources.py` (active artifact ids via
`ModelArtifactStore.fetch_artifacts_by_status(..., ACTIVE, station_id=|group_id=)` — never a call
that loads artifact bytes; training period via `fetch_artifact_record`), `snapshot.py`, schema,
fixture, spec. The test that asserts `build_snapshot()` never reads the three tables is narrowed
to the two hindcast tables (D5). The spec's `Verification` section and `Non-goals` line say what
`skill` is and that `verification` is still a sentinel.

**Out:** computing, recomputing or re-selecting skill; `latest_generation_predicate`; skill
diagrams; any ranking or comparability judgement in the document.

**Pre-change:** a snapshot test with a fake skill store holding current rows on an active and a
superseded artifact fails on the missing `skill` field.

**Verification:** `uv run pytest tests/unit/services/forecast_lab/ tests/unit/api/test_forecast_lab_schema.py tests/unit/cli/test_export_forecast_lab.py tests/unit/api/` — cases: superseded-artifact rows are excluded; a group-assigned model's rows come from the group's active artifact; a row with `eval_period_end` after `data_cutoff_at` is dropped; stratified rows are included with their `season`/`flow_regime` and a headline row with both null (D7); `evaluated_on` takes each of its four values; a model with no rows contributes nothing; `hindcast_forecasts` and `hindcast_values` are still never read.

### T5 — Combined-forecast `qc_status` and `qc_flags` (D8)

**Outcome:** both combined-forecast objects carry the stored forecast's `qc_status` and
`qc_flags`. Which combinations are shown is unchanged — the `QC_FAILED` exclusion stays, for
Plan 251 T3.

**In:** `api/forecast_lab_schemas.py`, `services/forecast_lab/snapshot.py::_build_combined_forecast`,
schema, fixture, spec; a status note in Plan 251 recording that its T2 moved here and its D1/D2
are settled by D1 and this plan's shape.

**Out:** Plan 251 T3 and D3; the member-model objects.

**Pre-change:** a snapshot test expecting `qc_status` on an available combined object fails on
the missing field.

**Verification:** `uv run pytest tests/unit/services/forecast_lab/test_snapshot.py tests/unit/api/test_forecast_lab_schema.py` — a suspect combination serves `qc_suspect` with its flags; a passed one serves `qc_passed` with `[]`; a failed one is still absent.

### T6 — Hand the contract over

**Outcome:** the map session and the region-bundle v3 carrier each have the final shapes, and the
record agrees with what shipped.

**In:** a reply to the map session (final v3 shapes; the `time_step_seconds` correction to D4;
the in-sample label; `detail` per D6; the full stratified row set per D7, with the measured document size); a note in the unnumbered region-bundle v3
draft's owning session that `observation_qc` and `qc_rule_set` are to be carried with the same
shapes, subject to that draft's own `detail` rule; `docs/plans/README.md` entry; the
`docs/touchpoint-maps.md` Forecast Lab paragraph (the new store and the lifted skill bar).

**Out:** editing the region-bundle draft itself (another session's worktree).

**Pre-change:** N/A — documentation and hand-over.

**Verification:** bounded inspection — the reply's shapes match the committed v3 schema, and
`grep -n "skill_scores" docs/touchpoint-maps.md docs/spec/forecast-lab-snapshot.md` shows no
sentence still saying the snapshot never reads it.

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
uv run python scripts/check_readiness.py docs/plans/345-forecast-lab-snapshot-v3-qc-and-skill.md
```

After staging deploy (orchestrator), before the map is told to cut over:

1. `SAPPHIRE_CONFIG` is the same in the `api` and `worker` containers, so the exported
   `qc_rule_set` is the set ingest actually uses.
2. A full all-station snapshot is fetched once; its size and build time are recorded against a v2
   fetch from before the deploy, in the reply to the map.
3. It validates against the committed v3 schema, and one station's `counts` match a direct
   `GROUP BY qc_status` over the same window.

## Explicitly out of scope

- Any QC behaviour: rules, thresholds, cadence matching, rule versions (the `"1.2"`/`"1.0.0"`
  mismatch is declared, not fixed), re-QC of stored rows.
- DHM/Nepal rules (303), hourly rules (323), network selection (264), overrides (269).
- The Nepal region bundle exporter.
- Skill diagrams, baselines' absence (a data fact, shown as absent).
- `latest_generation_predicate`'s scope lacking time step.

## Changelog

- 2026-09-25 — drafted from the map request and the owner's four decisions; measured on
  `6c77c666` and staging.
- 2026-09-26 — owner closed D5 (lift the bar, with the guard), D6 (publish `detail`), D7 (all
  breakdowns — against the recommendation) and D8 (absorb 251 T2).

## Dependency graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"]},
    {"id": "phase-2", "tasks": ["T2", "T3", "T4", "T5"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T6"], "depends_on": ["phase-2"]}
  ]
}
```
