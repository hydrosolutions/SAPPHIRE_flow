# API contract maintenance and specialist reference

For requests and everyday interpretation, use the [API guide](../api/README.md).
Maintain user-facing semantics there and field details at their existing schema
sources. This reference owns maintainer rules and specialist contracts; dated
rollout context is in [implementation history](api-v1-implementation-history.md).

The [generated OpenAPI contract](api-v1-map.openapi.json) defines request/response
structure. `src/sapphire_flow/api/map_contract.py` exports it;
`tests/unit/api/test_map_contract.py` checks drift and synthetic examples. Public
interactive API docs remain disabled. Source presence is not deployment or
scientific acceptance.

## Versioning (D14)

Contract 3.0 permits null training bounds and `evaluated_on="unknown"` for
explicitly unknown external training provenance. Consumers must display unknown
exposure without interpreting scores as independent validation. Known artifacts
retain their existing response values. Coordinate this nullable-response change
with map consumers before deploying unknown artifacts.

`docs/spec/api-v1-map.openapi.json`'s `info.version` is a **hand-maintained
constant** (`api/map_contract.py::MAP_CONTRACT_VERSION`), never the package
version. An additive change (a new field, route or enum
member) or documentation-only metadata bumps the **minor** number; a breaking
contract change bumps the **major** number and is announced to the map session **before** deploy. Which kind of
change a given diff is stays a **review judgement**, not machine-checked
(owner, 2026-09-26); `tools/check_map_contract_version.py` (CI, PR-only)
mechanically fails a pull request that changes the file without SOME greater
version. Regenerate with `uv run python tools/generate_map_contract.py`
after editing routes/schemas or bumping the constant.

Metadata-only version increments do not imply changed runtime behavior.
[API examples](../api/examples/) are synthetic and schema-validated; link to them
instead of copying full payloads.

## Bulk latest observations

See [observations in the guide](../api/README.md#get-observations-and-discharge-history).
Trusted-unit holds and consumer/reviewer delivery restrictions precede selection;
admins retain existing delivery visibility. This follows observation visibility,
not the forecast publication gate. All responses are no-store. Historical access
does not establish current ingestion or authorize unbounded fleet downloads.
The `source` filter defaults to `measured`; scoped stations are sorted by UUID
before pagination. Selection keeps the newest visible record even when its QC is
failed, raw or unchecked, rather than substituting an older passed reading.

## Snapshot contract export

The [forecast-lab snapshot](forecast-lab-snapshot.md) has a bounded observation
window, repeated station-code filter and declared errors. All snapshot responses
are `no-store`. Unknown/ineligible/out-of-scope requested codes reject the whole
request; publication-gated stations return 403, and unavailable ordinary forecast
reads return 503. Omitting codes selects the eligible scoped fleet; frontend fleet
use still requires its separate operational admission.

The maintained exporter removes boolean `available` discriminator hints only in
OpenAPI. The nested `oneOf`/`const` validation and string `representation`
discriminators remain. Runtime validation and the standalone v2 JSON Schema do
not change. No public OpenAPI endpoint is enabled. Pull the committed contract
from this repository; regenerate it with the command above.

## Auth

See [authentication and roles](../api/README.md#connect-and-make-your-first-request)
and the [security standard](../standards/security.md). Station/tenant scope checks
precede protected reads. Out-of-scope and unknown identities are indistinguishable;
station lists filter to authorized stations. Specialist contracts below specify
additional reviewer or granted-human requirements. QC rules, skill, Swiss overview,
Swiss observation history and rating-input history require reviewer/admin bearer
access. Rejected/evaluation forecasts admit reviewer/admin or a named human with
current station review grants; consumer keys are refused.

## Error envelope

See the [guide error table](../api/README.md#resolve-common-errors) for ordinary and
framework envelopes and legacy malformed-ID caveats. Do not standardize errors
as part of a documentation change. `/qc/rules?station_id=...` can return sanitized
500 for invalid station onboarding configuration; its deployment variant does
not read that configuration and can still return 200.

## Query conventions

See the [guide](../api/README.md#get-forecasts) and generated parameter schemas.
Ordinary station/forecast pages cap at 200, rejected forecasts at 50; station
observations are unpaginated. Legacy parsing accepts naive dates as UTC; guide
examples use explicit offsets.
Use the deployment's parameter names, not aliases.

## QC rule sets (`GET /api/v1/qc/rules`)

`scope` tells the two response variants apart: `"deployment"` (no
`station_id`) or `"station"` (with it) — a pydantic discriminated union,
rendered in the contract as `oneOf` + `discriminator`.

**Without `station_id`**: one document, the same for every client's token —
deployment-global, not tenant-scoped. Every rule in both sets (observation
and forecast), in config order, each with a code-derived `severity`
(`qc_failed`/`qc_suspect`) and the **current** configured thresholds. This
is **not** proof of what was in force when a stored flag was written — a
flag is matched to a candidate rule by rule set, parameter, `rule_id` and
(for observations) network, and — since cadence is not stored on an
observation — every matching rule of that parameter/rule_id is a
**candidate** (e.g. both `rate_of_change` rows at different cadences).

**With `station_id`** (D15): the observation rules selected for that
station's network, with the thresholds ingest currently applies there
merged in. `station_qc: "judged"` means the station is ELIGIBLE for
scheduled-ingest QC — **not** that every reading was checked (a judged
station can still leave a reading `qc_unchecked` when no cadence can be
inferred or no rule matches for it). `station_qc: "not_judged"`: the rows
shown are the network rules resolved **now**, without the station's own
declared thresholds (`station_thresholds: []` everywhere) — re-read the
route once the station is judged. Per row: `station_thresholds` names which
threshold keys came from this station's own declared override (`[]` = none
applied); `skipped: "no_datum"` marks a water-level rule needing a datum at
a station that has none (ingest skips it there — never mistake this for
"disabled everywhere"). `water_level_datum_masl`: non-null means served
readings are m a.s.l. and water-level thresholds apply to the value **minus**
this datum (visible only inside a flag's `detail`, never applied to the
served `value`); null at a water-level station means the datum-dependent
rules (`range_check`, `gross_outlier`) are skipped and readings may be
gauge-relative.

`station_thresholds: []` on a row means **no override is currently
applied** — this includes a declared block ingest has **rejected or not
applied**: a pending block (waiting on a not-yet-provisioned network/tenant)
appears only in ingest's own log (`ingest.qc_threshold_pending`); a rejected
or not-applicable one also in ingest's pipeline-health record
(`OBSERVATION_QC_THRESHOLD_CONFIG`) — never in this route's response. The station
view describes scheduled ingest now; historical imports and
onboarding may have used different thresholds. The old activation context is in
[implementation history](api-v1-implementation-history.md#historical-dhm-qc-activation-context).

A `null` threshold value is a non-finite configured value (TOML `nan`/`inf`),
never a "no limit" sentinel to compute against.

Since PR #315 a **new** observation flag's `rule_version` is its configured
rule's version, but **stored history** also holds earlier code-generation
labels (`"1.0"`, `"1.2"`) and forecast flags always carry `"1.0"` — so
matching a stored flag to a served rule never relies on `rule_version`
equality.

The observation rule set's `selection` is `"parameter_cadence_network"`: a
network-specific rule replaces the generic one **only** for the exact same
`(rule_id, parameter, time_step_seconds)` — at a different cadence the
generic rule still applies. The forecast rule set's `selection` is
`"parameter_and_cadence"` (no network dimension; forecast QC applies no
per-station overrides — the forecast block in the station variant is
identical to the deployment one).

`source` on a rule set is `"config"` when the merged config file declared
that section, `"builtin_default"` when the variable was unset OR the merged
file simply lacks the section — never a path or overlay name.

## Scored artifacts (`GET /api/v1/stations/{id}/skill-options`)

Contract 3.8 adds reviewer/admin discovery for one scoped station, with optional
exact `model_id`. The response is discharge-only and lists artifacts with readable
stored scalar scores, including inactive candidates. Unassigned models are also discoverable when their scored artifact is pending
approval, active or superseded. Existing assigned-model candidate eligibility is
unchanged. It uses the
same latest-generation predicate and excludes future evaluation end dates.
`availability: no_stored_skill` with an empty list means nothing readable matches;
out-of-scope stations remain 404. Discovery does not create model assignments.

Each artifact has recorded training dates, evaluation scopes (generation, source,
forcing, computation version, cadence/phase, dates and `evaluated_on`) and
`forecasting_status: active_at_station | not_active_at_station`. Current active
status requires both an active model assignment and the selected active artifact;
it does not identify the artifact behind an older displayed forecast. Use the
network overview's nullable `model_artifact_id`, copied from the visible forecast,
for `/skill?model_id=...&artifact_id=...`. Absent/withheld overview entries expose
no artifact. A null artifact must not silently fall back to the current artifact.

The catalogue projects metadata only, not score rows, artifact bytes or diagrams.
At most 256 evaluation scopes are returned; overflow is 503 (narrow by model),
never truncation. All responses/preflights are no-store. No computation, activation,
model ranking or claim of complete/out-of-sample evaluation occurs on this read.
Unknown training dates remain null; outside-training labels do not rule out donor
pretraining leakage. Scalar sample/excluded counts remain in `/skill`.
The synthetic [catalogue example](../api/examples/skill-options.json) is a contract
fixture, not evidence of deployed model coverage.

## Stored diagrams (`GET /api/v1/stations/{id}/skill-diagrams`)

Contract 3.9 adds one bulk reviewer/admin read for reliability and ROC. Required
selectors are exact `model_id`, UUID `artifact_id`, repeated `lead_hours` (1–16
nonnegative integer values), and repeated `threshold_level` (1–8 names, at most
64 characters each). For example: `?model_id=cmal_small&artifact_id=<uuid>&lead_hours=24&lead_hours=48&threshold_level=High`.
The response covers discharge, all seasons and all flow regimes together; it does
not recompute or slice skill to a requested event period. Inactive artifacts remain
readable under the same benchmark-artifact eligibility and station scope as `/skill`.

Both diagram kinds retain their stored arrays under `data`: reliability has `bins`,
`forecast_freq`, `sample_counts`, `observed_freq`; ROC has `thresholds`, `hit_rate`,
`false_alarm_rate`, `n_events`, `n_non_events`. Legacy payloads retain their stored
shape. Undefined entries remain null, never zero. `threshold_context`, when stored,
provides the threshold snapshot/source, scored and excluded counts, event counts,
probability method and temporal caveats. Absent historical context remains null;
no counts or source are inferred. Forcing comes from the published generation;
legacy rows without a generation report null forcing.

Each diagram states its generation, source, computation version, evaluation dates,
cadence/phase and `evaluated_on`; the envelope carries training dates. The current
scalar generation/cohort must match: a newer scalar publication cannot leave an
older curve visible. Sources/forcing cohorts remain separate rows. These remain
stored evaluations, not a score of today's displayed forecast or proof of held-out
training. Match their artifact and generation to the scalar read.

`availability` is `available`, `partial` or `no_stored_diagrams`. `missing` lists
requested lead/threshold/kind combinations without any matching readable current
diagram. This does not claim that the historical run itself is complete. An unknown,
unassigned or mismatched artifact returns no diagrams, without exposing metadata
from another artifact. Out-of-scope stations return 404.
Empty results always have null training dates, including an existing artifact of
the same model that has no readable diagrams at this station.

The uncompressed response ceiling is 4 MiB, with a maximum of 1024 diagram rows.
The database also bounds stored JSON text before transfer; whitespace can make
that pre-check slightly stricter than the compact response size.
Oversize reads fail with 422 and an error envelope, never truncation; invalid query
shapes use FastAPI's validation envelope (both are in the contract). Narrow the bulk
selection on overflow. Responses and allowed GET preflights are no-store. No
per-lead or per-threshold HTTP fan-out is needed. The synthetic
[diagram fixture](../api/examples/skill-diagrams.json) illustrates the wire shape.

## Skill (`GET /api/v1/stations/{id}/skill`)

Optional `artifact_id=<UUID>` selects stored skill for that artifact, including
inactive, rejected or failed artifacts with stored results, without activation. Response `selection` is
`explicit_artifact`; omission preserves `latest_generation_on_forecast_artifact`.
Reviewer/admin station scope still applies. Assigned models retain existing
candidate review eligibility; unassigned models require a pending-approval, active
or superseded artifact with eligible published scores at this station. An unknown or mismatched artifact yields
empty rows; malformed UUIDs yield 422. Future evaluation end dates remain
excluded. Candidate skill is deliberately visible to the station's reviewers;
this does not publish or activate candidate forecasts. Combined null-artifact
scores cannot match the selector.

These are stored results for each row's evaluation period and sample count,
including interim evaluations of incomplete hindcasts. They are not skill for
a requested event period or proof that a multi-year run is complete. Version-4
threshold rows include probability exclusions and threshold provenance;
undefined scores are JSON null. The selector does not add a diagram endpoint.


By default, one row per `(model, artifact, skill_source, forcing_type, time_step,
phase_offset, lead_time, season, flow_regime, metric)` on the artifact the
station's **forecast** currently uses (station-scoped first, else an active
group artifact) — headline and season/flow-regime breakdowns alike.
`time_step_seconds`/`phase_offset_seconds` ARE carried (a prior message to
the map session wrongly said skill rows had no time step). `evaluated_on`
compares the row's eval window against its artifact's training period as
**closed intervals**: fully inside -> `"training_period"`; no shared instant
-> `"outside_training_period"`; anything else, including an eval window
starting exactly at `training_period_end` -> `"overlaps_training_period"`.
Unknown training exposure returns `"unknown"` with both training bounds null;
these scores do not establish independence from training.
`score: null` means the metric is undefined for that
stratum (a non-finite stored value), not zero. Empty `rows` is normal: fallback
tiers may have no skill rows. Combined (pooled/BMA) forecasts have
**no** skill rows here (their scores carry no single artifact, so the
selection never serves them). Skill is served for **discharge only**.

Two configurations are explicitly out of scope and recorded on the map's
behalf: a station in **several groups** that each hold an active artifact
of the **same** model — the forecast path itself picks one arbitrarily, so
"the artifact the forecast uses" is undefined there (a possible forecasting
fault, tracked separately); and a model reached through a **group**
assignment while the station **also** holds an active station-scoped
artifact of it — the forecast uses the group artifact, but this route's
skill rows come from the station-scoped one (the two can disagree).

`season` is a plain string, not a closed `Literal` — the deployment
configures its own season names ([architecture context](../architecture-context.md)); do not
hardcode a fixed set of seasons in the map.

**Metrics** (`metric` values actually emitted, `services/skill/service.py`),
each in the served parameter's unit (discharge m³/s, water level m) unless
noted:

| `metric` | direction | notes |
|---|---|---|
| `crps` | lower is better | Continuous Ranked Probability Score |
| `nse` | higher is better | Nash-Sutcliffe Efficiency, dimensionless, max 1.0 |
| `kge` | higher is better | Kling-Gupta Efficiency, dimensionless, max 1.0 |
| `pbias` | closer to 0 is better | percent bias, `%`, signed — neither higher nor lower alone is "better" |
| `mae` | lower is better | Mean Absolute Error |
| `sharpness_p10_p90`, `sharpness_p25_p75` | narrower is more informative | ensemble spread width — only "better" alongside good calibration; a narrow but wrong ensemble is worse than a wide correct one |
| `ensemble_range` | context-dependent | full ensemble spread |
| `peak_timing_error` | closer to 0 is better | hours, signed (early/late) |
| `bss_danger_<level>` | higher is better | Brier Skill Score for that danger level, dimensionless, max 1.0 |
| `pod_danger_<level>` | higher is better | probability of detection, 0-1 |
| `far_danger_<level>` | lower is better | false alarm ratio, 0-1 |
| `csi_danger_<level>` | higher is better | critical success index, 0-1 |

[Architecture context](../architecture-context.md) § S.4 carries the full definitions and
interpretation-band literature references (Moriasi et al. 2007) — this
table is the map's quick-reference, not the source of truth.

## Observations and forecasts (existing routes, D13's two additive fields)

These QC fields are visible within existing scope and publication boundaries
(Plan 402 D13):

- `ObservationResponse.qc_rule_version` (nullable): the stored value —
  `"1.2"`, `"1.2-datum"`, `"1.2-datum-skip"` and (pre-Plan-324 rows)
  `"1.0"`, `"1.1-datum"`, `"1.1-datum-skip"`. These are **code-generation
  markers**, not rule-set versions; `-datum` means water-level thresholds
  were applied to the value minus the station datum, `-datum-skip` means no
  datum existed so `range_check`/`gross_outlier` were both skipped and
  nothing was shifted.
- `ForecastSummary.qc_flags` / `ForecastDetail.qc_flags` (`[]` when none):
  the forecast's own QC flags, the same typed shape as an observation's
  `qc_flags` (`QcFlagResponse`: `rule_id`, `rule_version`, `status`,
  `detail`).

`qc_status` on both observations and forecasts is now a typed enum over
every stored value, including `raw` and `missing` (never emitted by a
`QcFlag` itself, but always possible at row level): `qc_unchecked` is
**not** the same as passed (no rule could be selected for that cadence/
group — usually transient); `raw` means **not yet checked** — a `raw`
forecast's `qc_flags: []` is *not* evidence it passed, only that QC has not
run.

Calculated-station observation flags (`rule_id: "upstream_propagated"`,
`rule_version: "component_derivation/v1"`, frequently `status: "qc_passed"`)
match no configured rule; their `detail` is JSON naming the component
stations, not a threshold.

**A failed member or group forecast is never stored as a forecast; it is
recorded on the rejected-forecast route** (Plan 404) — only a *combined*
forecast is ever stored with a failed verdict — so the absence of
`qc_failed` member/group forecasts through these routes says nothing about
whether the thresholds are being exceeded; see `GET
/api/v1/stations/{id}/rejected-forecasts` below for the full record. A
rejected *combined* forecast on a tenant where Plan 341's publication gate
is active is visible only through Plan 341's human-review routes, never to
a reviewer token — **on a gated tenant, the reviewer token sees only
published forecasts, and a `qc_failed` forecast is never published**, so
forecast QC failures are not visible through the ordinary forecast routes
there.

## Rejected forecasts (`GET /api/v1/stations/{id}/rejected-forecasts`, Plan 404)

Every member or group-station forecast QC rejected for this
station, in the query window — the record `forecasts` never gets, since a
`QC_FAILED` assignment is never stored there (D1/D2). **Every parameter of
a rejected assignment is returned, including `qc_passed` and `qc_suspect`
ones — each with its own status**; `qc_unchecked` means a later parameter's
block errored after an earlier one had already failed and is never read as
a pass. One rejection is the group `(attempt_id, station_id, model_id,
group_id)` — `group_id` is `null` for a member (station) rejection.
`attempt_id` distinguishes a Plan 327 resume or Plan 328 retry of the same
cycle; `recorded_at` is the server-side write time (distinct from
`issued_at`, the cycle's issue time). `values` is keyed by member id or
quantile level (as a string), each an array of `{valid_time, value}`
points — each series keeps its OWN timeline (members may have different
valid-time sets); non-finite values use the same encoding as forecast
evidence (`{"nonfinite": "nan" | "inf" | "-inf"}`).

**Gated by `require_reviewer_or_human`** (Plan 404 T3): a reviewer or admin
service token, OR a named human with a current station `review` grant
(Plan 341) — a consumer token gets `403`. Where Plan 341's publication gate
is active for the tenant, a **reviewer** service token's items carry
`withheld: true`, `values: null` and every flag's `detail: null` — every
other field, including `id`, `model_artifact_id`, `group_id` and
`qc_status`, is still returned. **Admin** tokens and a **granted human**
always see the full record (`withheld: false`). Where the gate is not
active (deployment-dependent), reviewer and admin tokens both see
everything.

Query conventions match the rest of this page: `start`/`end` optional,
default the last 7 days ending at request time, `end` exclusive;
`model_id` filters to one model. Paginated with its OWN ceiling —
`limit` default 20, capped at 50 (not 200 like the other list routes),
since each item carries a full ensemble. Items are ordered newest first,
`(issued_at DESC, recorded_at DESC, id DESC)`. A malformed `station_id`
is `400`; an unknown or out-of-scope/ungranted station is `404`.

This route is **never** a source of a publishable forecast ID: neither
landing order (this plan or Plan 341) permits a `rejected_forecasts` id on
a forecast publication route.

## Forecast flag `detail` — data sensitivity carried forward, not decided here

`detail` on both observation and forecast QC flags is served **verbatim**
(D6). For `climatology_outlier` specifically, a forecast flag's `detail`
includes observation-derived baseline statistics. Under D12 the Swiss
dashboard cannot see Nepal stations, and each dashboard's token is scoped
to its own client's stations — so the only exposure left is a **Nepal
consumer token** (a third party) reading Nepal (DHM) observation/forecast
flag `detail`. **Before DHM observations are readable by a Nepal consumer
token, the owner must decide whether flag `detail` is stripped for
consumers on that network** — recorded in Plan 143 (DHM onboarding), not
resolved by this plan.

## What the map should NOT infer

- `/qc/rules` never says "Swiss" anywhere — until a DHM-specific rule is
  configured, Nepal (DHM) stations get the same generic rules Swiss
  stations do; the map itself should label these as "Swiss starting
  values" from its own knowledge of the deployment.
- Neither route lets a reviewer ask "what would a different threshold have
  flagged" (a QC what-if/dry-run, D9) — a separate follow-on, not built
  here.
- `/qc/rules` never proves which thresholds produced a *past* flag — only
  the deployment's/station's **current** resolution.

## Swiss live overview (Phase B)

`GET /api/v1/swiss/overview` is a bearer reviewer/admin read. Human sessions and
consumer tokens are not accepted. The payload has `schema_version: 1`. Success, errors, and GET-only CORS
preflights use `Cache-Control: no-store`.

The route defaults to disabled (503). A reviewed deployment overlay must include
`[swiss_overview]`, `roster_count = 134`, and
`roster_sha256 = "2473843709831dcae8266e51757efccb563eb287871caffcc276835ca4007be1"`.
It also requires `[evaluation_forecasts]`; that section owns the tenant and daily
model/group configuration. The immutable manifest ships in the wheel at
`config/swiss_display_roster.json`. Metadata drift fails 503 before observations
or forecasts are read. Admins are restricted to the configured tenant and manifest;
reviewers further intersect their explicit station grants. No roster expansion is
inferred from mutable group membership. Codes are unique across networks because
BAFU archives are code-keyed.

The header declares measured discharge and the canonical observation display unit
`m3/s`, not a per-record source-unit certificate. Every scoped station is returned.
`generated_at` is one injected UTC clock, not source freshness. `observation.latest`
is the latest trusted QC-passed reading in the preceding 168 hours, or null.
The three `hourly` entries end at UTC hour boundaries H-2h, H-1h, H, oldest first.
Each selects the last reading in `(hour_end-1h, hour_end]`; gaps remain null.
Protected Nepal delivery rows are excluded even for admins. The existing
`/observations/latest` defaults and water-level behavior are unchanged.

`forecast` is only today's shared CURRENT daily evaluation selection. There is
no ordinary, yesterday, or staged fallback; the one exception is the labelled
retained result described below. All current groups are validated before
selection; an invalid daily authority makes every CMAL component unavailable with
`authority_integrity_error`. A typed failure decoding an exact authoritative stored
record has the same component-only result; observations and BAFU survive.
Stored flag arrays require object entries with their required string fields;
rejected series require arrays of two-element timestamp/value points and valid
numeric/nonfinite encodings. Malformed JSON shapes and known Polars value-frame
construction/cast failures become typed decode errors for exact STANDARD reads.
Legacy null flag arrays remain empty; non-STANDARD errors retain their boundary.
Database, protected-lineage and unrelated programming errors are not relabeled.
Candidate keys retain the full frozen claim and output reference, so another
group cannot overwrite the first group’s eligibility. The station
panel still runs its fallback/history/diagnostic path; yesterday-only diagnostic
corruption can fail that panel while a valid current-only overview remains usable.

Retained result (contract 3.11, issue #581). When a later attempt of the same
issue day is authoritative but did not produce a station (model failed, input
excluded, integrity rejected, or the station is absent from it), the newest
earlier attempt of that day that did produce it may stay visible. The CMAL
object (available or withheld) then carries `retained_result`
`{kind: "earlier_attempt_after_failed_retry", original_issued_at,
original_attempt_id, superseding_attempt_id, superseding_state,
superseding_reason}`; the last two are the superseding attempt's member state
and reason, null when the station is absent from it. The field is null or absent
otherwise. `output_id` and `issued_at` are those of the retained output. The
earlier output passes the same eligibility, QC, superseded and withholding rules
as any current output; if it is rejected, expired or not covering the time the
headline is unavailable, never an older attempt. The Swiss overview and the
network overview apply this (contract 3.12 for the latter, below); the station
evaluation selection is unchanged (a follow-up, #581 T3), so until then the
evaluation detail can show a fallback model or nothing while the overviews show
the earlier CMAL forecast.

Available CMAL includes `output_id`, `output_source` (`forecast` or `rejected`),
`qc_status`, `model_id`, source `daily_cmal`, and input-quality labels. Eligible
rejected/suspect outputs remain visibly labeled. Peaks are maximum per-time
ensemble medians over still-valid intervals; earliest time wins a tie. Horizon end
is the exclusive end of the last interval. `bafu_comparison` is independent and
never replaces CMAL. It uses one archive listing and scoped selected files through
the full envelope parser. The overview additionally requires the same-stem raw
JSON (at most 1 MiB), matching issue annotation and relevant trace times/values.
Median and both Min/Max traces must explicitly declare `m³/s` or `m3/s` on the
same axis. An omitted trace `yaxis` means the primary `"y"` axis, including on
Measured traces; explicit null, empty or conflicting axis values are not defaulted.
This follows Plotly's `Scatter.yaxis` contract (Plotly 6.6.0 installed API
documentation and [scatter reference](https://plotly.com/python/reference/scatter/#scatter-yaxis):
`"y"` is the default and refers to `layout.yaxis`). Only the percentile fill band
may inherit that shared-axis evidence. Axis defaulting never supplies missing units.
A parquet unit alone is not evidence: the collector may have defaulted it.
Missing/inconsistent raw evidence makes only that BAFU component unavailable;
the existing station snapshot parser remains unchanged. Source issue time is not
archive collection time. Malformed timestamp filenames are ignored as non-runs,
never treated as valid. Corruption in a selected latest run never selects an older
run. Null medians or non-monotonic envelopes are `invalid_source_data`; exhausted
intervals are `expired_horizon`. A CMAL quantile output without a 0.5 quantile is
`invalid_source_data`, not an expired horizon. Shared configuration/read failures
are HTTP 503; unexpected errors are HTTP 500, both declared in OpenAPI.

When the publication gate is active, reviewer CMAL retains only state/reason,
output identity/source, model, issue time and QC status. Admin CMAL follows the
existing evaluation behavior. BAFU is withheld for both roles, retaining only
`state`, `reason`, and constant `source: "bafu"`.

Reported levels, flow classes and threshold onboarding belong to Phase C.
They explicitly report `unavailable` / `not_onboarded`; thresholds are `[]` and
inferred levels/provenance are null. Unknown never means level 1.

Frontend integration must use visible-tab polling, bypass proxy caches, clear
live numeric fields on HTTP failure, and preserve the station panel's three-read
budget. No backend scheduler or cache is added. See [swiss-overview.example.json](swiss-overview.example.json) for a one-station excerpt from the isolated synthetic
PostgreSQL/archive response, with station_count reduced to match (not production data). See the schema for exact unions:

```json
{"state":"unavailable","reason":"pending_current_issue"}
```
```json
{"state":"withheld","reason":"publication_gate","source":"bafu"}
```

The midnight pending interval is intentional. A separately labeled panel fallback
may still exist during it. Polling cadence, browser acceptance, and deployment
performance remain rollout checks, not guarantees from this contract.


### Swiss overview compact CMAL series (map contract 2.6)

An available `daily_cmal` headline carries optional `series`. New responses emit either an available 1–10 point median/p25/p75 interval-start series or component-local `point_limit`/`invalid_series` unavailability. The field is optional for rollback compatibility. It inherits the headline output identity and unit. Each point is `[valid_time, median, p25, p75]`; `quartile_method` is `stored_quantiles` or `member_empirical_type7`. The series must match the headline horizon, peak value and earliest peak time. Missing bounds are null and are never replaced with zero-width bands. Other headline variants do not carry this field.


### Swiss observation history (map contract 2.7, disabled build)

`GET /api/v1/swiss/observation-history?window_hours=168` is a separate reviewer/admin bearer read. `window_hours` accepts only a single `72` or `168`; omission means 168. Unknown/duplicate keys fail with framework 422 `{"detail":[...]}` after authentication. Human sessions and consumer tokens are not admitted. Other HTTP errors use `{"error":...,"detail":null}`; unsupported methods use the framework detail envelope. Every path, including preflight and failures, is no-store.

Top-level deployment setting `swiss_observation_history = false` is independent of the digest-pinned `swiss_overview` roster and `evaluation_forecasts` tenant settings, which are also required. It must remain false until qualification and owner admission. Metadata drift and bounded unavailability return generic 503; unexpected faults remain sanitized 500. No forecast/provider/archive reads occur. Admin remains roster-bound; reviewer receives the tenant/grant intersection, including empty stations.

The schema_version 1 compact body has a fresh UUID generation, `generated_at=as_of=A`, `expires_at=A+20 minutes`, exact `window_start=S=A-W`, and `native_start=J=floor_3h_UTC(A-1h)`. Coarse owns `[S,J)`; native owns `[J,A]`. Shared grid starts at `floor_3h_UTC(S)`, step 10800 seconds, with the first interval clipped to S. Station tuples are `[sample_mean,min,max,eligible_count]`; empty is `[null,null,null,0]`. Native tuples are `[timestamp,value]`. UTC microseconds and binary64 precision are retained. Means use exact-ratio accumulation rounded once, not a time average. Denser sampling weighs more. Coverage is always unknown, including singletons; no cadence, interpolation, carry-forward or percentage is inferred. Latest/hourly overview and forecast semantics are unchanged.

Trust/hold, protected-delivery, scope, QC-passed measured-discharge and finite-value eligibility precede every statistic and volume decision in one SQL snapshot. The request-local statement timeout is five seconds; only SQLSTATE 57014 maps to 503 and failed transactions roll back. Row-cap+1 is an overflow sentinel, never truncated success.

Provisional safeguards are 200 stations, 2500 eligible rows/station, 500000/fleet and 2097152 raw UTF-8 bytes. They are not approved operational budgets. The proposed <=6 total SQL and <3-second p95 still require representative qualification; explicit-grant auth currently requires seven statements including tenant-integrity validation and SET LOCAL. Do not weaken authorization to fit that proposal. Existing overview gates remain 393216 bytes, <1.25-second p95 and <=12 queries. See the [reviewed implementation/qualification contract](../plans/2026-11-05-swiss-mixed-resolution-history.md); browser retention and owner visual/operational admission remain separate gates.


Singleton blocks require numeric `mean == min == max` at both the domain and
HTTP boundary. Signed zeros compare numerically: `statistics.mean([-0.0])`
returns `+0.0`, while extrema and native values preserve `-0.0`. No mean
algorithm, rounding, wire field, or map-contract version changes for this check.
The route wire regression includes fractional microseconds and JSON `-0.0`/`2.0`.

The expensive history benchmark is a **provisional qualification diagnostic**,
not an admitted production regression or nightly qualification. It requires an
explicit opt-in in addition to the slow-test selection. Run from the backend root
with the existing test environment and Docker available:

```sh
SAPPHIRE_RUN_HISTORY_QUALIFICATION=1 SAPPHIRE_HISTORY_BENCHMARK_DIR=/tmp/sapphire-history-benchmark uv run --no-sync pytest tests/integration/api/test_observation_history_performance.py -m slow -v -rs
```

Without that environment flag, even an explicit slow run visibly skips these
history diagnostics with the unadmitted-gate reason. Ordinary history route/store
tests and all existing overview gates remain normal tests. The flag does not
turn failures into expected failures, approve limits, or activate the route.
Required performance qualification remains open: representative p95, auth-inclusive
query cost, caps/encoded-size boundaries, actual per-request allocation and
concurrent history plus overview traffic. Fixture-process peak RSS includes setup
and process lifetime; it is not request memory and must not be extrapolated
linearly to fleet caps. Single SQL timings are diagnostics, not p95 evidence.

## Selected operational weather facts (map contract 2.8)

See [weather facts in the guide](../api/README.md#get-forecasts).
Preserve `WeatherSourceRunResponse` for source/run pairs and the distinct
`WeatherSourceResponse` used by `StationDetail.weather_sources`. Documentation
metadata must not change component identity. The original additive-change handoff
is in [history](api-v1-implementation-history.md#operational-weather-contract-28).

## Provisional forecast reads (map contract 2.9)

See [category usage](../api/README.md#get-forecasts). Scoped-header authorization
precedes protected lineage, values and weather. GET never converts/reconciles or
falls back to another purpose. Frozen lineage still validates when summary/detail
do not expose exact curves. Superseded provisional history remains readable.
All four shared paths, outer errors, redirects and allowed preflights are no-store;
CORS is unchanged and no raw derived-table grants are implied.

SQL/decode/fetch failures are translated only at the provisional boundary.
Unexpected TypeError/KeyError faults remain sanitized 500. Structured diagnostics
contain only operation/category/whitelisted exception class and validated SQLSTATE,
never raw exceptions, SQL, IDs or evidence. Regression and compatibility evidence
is [retained separately](api-v1-implementation-history.md#provisional-read-regression-and-compatibility-evidence).

## Forecast-specific rating-derived input context (contract2.10)

See [daily input history](../api/README.md#get-observations-and-discharge-history).
Preserve frozen-evidence validation, exact curve identity/validity, scoped
reviewer/admin authorization and seven completed issue-anchored daily periods.
Contract 3.7 adds nullable `period_definition` and support `available_at`. Legacy
v1 history keeps UTC-midnight periods; declared v2 history uses its retained
physical boundary and MEAN/MAX aggregation. `day` remains the physical period
start even for END-labelled model inputs. Original native timestamps are retained. Evidence
failures/limits return 503 rather than current-curve recomputation. Responses,
outer errors, redirects and preflights are no-store. Existing summary/detail
`rating_provenance_status` does not change.

History validation reuses an exact frozen curve/reference text pair only within
one request. The first occurrence is fully traversed; later samples still check
surrounding duplicate keys, their own digests, identities, captures, QC, validity
and discharge semantics. Changed curve or reference bytes cannot reuse validation.
The owner-only helper skips only the already-validated top-level curve subtree,
at the same depth. The 500,000-node work budget charges the full subtree once,
then one node per repeated occurrence. The 64-entry limit applies to exact
curve/reference text pairs; formatting variants consume separate entries.
Existing byte, depth, sample and output limits remain; large unique evidence
still returns503. Migration0084 adds this optimization;
it does not change evidence, API authorization, output or public error handling.

Current routing and verification remain in the
[touchpoint map](../touchpoint-maps.md#forecast-specific-frozen-rating-history).

Known first-cut availability limit: valid owner-assumed reference JSON can contain
high-precision decimal spellings that the producer rounds to float64. PostgreSQL's
exact numeric comparison can reject that representation even when the producer
accepts it. This returns503, never empty success or altered Q. It is a representation
compatibility limit, not corrupt input. Follow-up must align this narrow comparison
without weakening primary frozen-curve byte identity or parse budgets. Qualified
fixtures do not establish full21-station or all-history availability.

## Network overview

The [overview contract](network-overview.md) owns observation/forecast selection,
entitlement, compact series and private conditional polling. Configured thresholds
are batched; datum metadata retains reviewer/admin-only disclosure. Threshold
imports and alert activation remain separate deliveries.

Retained result (contract 3.12, issue #581 T2). The daily CMAL forecast of a
station whose authoritative same-day attempt did not produce it (the member is
present but failed or integrity rejected) shows the newest earlier attempt of the
same issue day that did produce it, as for the Swiss overview. That forecast item
then carries `retained_result` (same object as the Swiss overview). The field is
omitted otherwise. The retained forecast passes the same publication, QC,
superseded and 168-hour rules as any forecast; if it fails them the item stays
`withheld_or_ineligible`, never an older attempt. A station absent from an attempt
is not substituted.

## Shared station discharge history (map contract 2.13)

The [client guide](../api/README.md#shared-nativehourly-discharge-history)
owns request/response usage. Preserve separate ordinary/provisional series,
scoped reviewer/admin access for provisional values, newest-before-match stored
selection, source/QC agreement and frozen provenance. Warnings are retained worker
assertions, not independently recomputed or certified by this API.
Existing overview, observations, Swiss history and forecast input-history contracts
are unchanged. See the [touchpoint map](../touchpoint-maps.md#shared-discharge-history-boundary)
for implementation and verification routing.

Initial bounds are seven days, 10,000 scoped candidates including retained versions
and source levels, 2 MiB response, 32 MiB aggregate parsed evidence, 256 KiB per
evidence document, 500,000 parsing nodes, 64 frozen curves and a five-second SQL
timeout. The new endpoint's aggregate evidence limit covers measured valid
seven-day histories above the original 16 MiB draft limit. Other caps and existing
forecast-input history limits are unchanged. Resource/evidence failures return503
without truncation. Sequential seven-day Nepal reads were verified during the
[16 November deployment](api-v1-implementation-history.md#nepal-discharge-history-deployment-16-november-2026).
Concurrent roster load and live Swiss data remain unqualified.

The API must load the deployment's existing `provisional_ratings` bindings as
well as the workers. An absent station binding yields held `not_configured`
results even when stored conversions exist. The Nepal API uses the same
read-only rating configuration as its workers; GET still performs no conversion.

The station-history projection accumulates scalar samples in a typed array and
encodes JSON once, avoiding repeated copying of a growing JSON history. Ordering,
response content, evidence validation and all resource limits are unchanged.


## Operational per-time probabilities (forecast detail, contract 3.10)

`GET /api/v1/forecasts/{id}` now includes `threshold_probabilities` for the exact
forecast, model, artifact and issue time identified by the parent response. The
same additive field is available on authorized `data_use=provisional_rating`
detail, with all existing provisional labels. Publication and station scope are
checked before reading thresholds or returning probabilities; withdrawn forecasts
retain their tombstone without probability data. Overview remains compact; use
its existing detail URL. No extra per-threshold or per-time requests are needed.

The block contains current `thresholds` (stable keys matching event review, values,
canonical units, authority/inferred source and update time) and `points` keyed by
`valid_time`. Each point maps a threshold key to `probability`, `lower`, `upper`,
`method` and `reason`, using the same converter as historical review. The comparison
is strict exceedance, with no tolerance or rounding. Quantiles interpolate only
inside their supplied range; unresolved tails have a null point probability and
bounds. Empirical member frequencies are labelled; single-member deterministic
forecasts return unavailable probabilities. Failed/suspect/missing forecast QC
returns unavailable probabilities; raw/unchecked quality remains the parent's
explicit status and applies equally to the probabilities.

Only discharge in `m3/s` or its `m³/s` spelling is comparable in this MVP. Other
parameters return `parameter_not_supported`; incompatible discharge units return
`forecast_units_not_canonical_discharge`. Water-level thresholds are never used
for discharge. This is shared behavior across deployments, not a regional switch.
Missing matching thresholds yields `thresholds_not_configured`, not zero risk.
Thresholds are the current snapshot, not necessarily those in effect at issue time;
`updated_at` is not an inferred historical effective date.

`temporal_support` is `declared_period` with `period_definition` and explicit
half-open `support_start`/`support_end` per point, or `unknown` with null support
for legacy forecasts. No interval is guessed from the timestamp or step. An
exceedance of a daily mean is not the probability that an instantaneous flood peak
crosses the danger level during that day. `window_event_probability` is explicitly
`unsupported`: no joint event likelihood is fabricated from marginal quantiles.
These values do not issue warnings or activate notification delivery.

`availability` distinguishes `available` (including bounded tails), `partial`,
`unavailable`, and `thresholds_not_configured`. Point-level reasons preserve
unavailable data as null. The field is nullable for compatibility with detail
serializers that do not provide this enrichment; it must not be interpreted as
zero risk. This delivery does not compute stored model skill on reads.

Invalid threshold snapshots (nonfinite values, duplicate keys or station mismatch)
make the probability enrichment unavailable; the admitted forecast remains readable.


### Declared hindcast periods and evaluation identity (contract 3.14)

Reviewer skill rows, skill-options evaluations and stored diagrams now expose
nullable `evaluation_context`: `period_definition`, `reference_period_definition`,
`reference_displacement_seconds`, and `input_availability_policy`. Periods use the
existing step/origin/label/aggregation payload. Null is legacy unknown support.
Every compatible current identity is returned; exact and approximate reference
periods, START/END labels and availability assumptions are never collapsed. A
new generation replaces only the evaluation identities it covers. Scalar/diagram
joins require the same context. These are additional scientific identity fields,
not evidence that unknown training provenance has been resolved.

Hindcast review sources now carry their `period_definition` and
`input_availability_policy`, with `temporal_support="declared_period"`; point
`valid_interval_start`/`valid_interval_end` describe physical coverage. The requested
half-open review window must fully contain each declared interval. `valid_time`
retains its declared label. Leads remain exact integer elapsed hours from issue
to label, including negative START leads; the skill-diagrams selector accepts
negative leads too. Legacy operational review remains timestamp-based and accepts
nonnegative leads only. Mixed legacy/declared run identities refuse; automatic
operational/hindcast fallback remains unsupported. Publication, tenant, purpose,
reviewer authorization and work/response limits are unchanged.

For example, an 18:00 START daily forecast issued at 00:00 has first lead -6;
the same interval END-labelled has lead +18. An accepted +900-second reference
comparison covers 18:15–18:15 instead and remains separately labelled in skill.
Do not combine scores across these contexts or label a daily aggregate as an
instantaneous peak. The legacy administrator hindcasts.json route returns 422
for declared-period data and directs callers to review-series.

Declared `evaluation_context` additionally retains `source_periods` (the canonical native input/verification source catalogue) and `source_periods_sha256`. Native period labels, sampling, stored-label shifts, input displacements and evidence survive independently of the canonical comparison label. Source qualifications with identical numerical scores remain distinct.

## Provisional peak water-level comparison (contract 3.13)

Reviewers/admins may request
`GET /api/v1/forecasts/{id}?data_use=standard&rating_comparison=provisional_rating`.
Use `data_use=provisional_rating` for a provisional parent forecast. The optional
`peak_water_level_comparison` is independently labelled `provisional_rating`;
it does not change the parent's purpose, publication state, QC or provenance.
Absent opt-in, the field is null and no curve is read. Consumers receive 403
before parent lookup. Existing hidden/out-of-scope 404 and withdrawn 410 responses
carry no comparison. Responses remain `no-store`.

`remaining_peaks` contains quantiles 0.5 and 0.9. These are independent maxima of
pointwise discharge quantiles over remaining stored timesteps; their times can
differ. Call P90 an **upper envelope**, not a percentile of trajectory maxima or
a 90% probability of remaining below a level. Stored percentiles require exact
0.5/0.9 coordinates. Member P50 is the median; P90 uses type-7 interpolation with
at least five members. Missing P90 leaves the median usable. Any missing required
percentile at an eligible time makes that entire percentile peak unavailable.

The response echoes `as_of` and the parent `period_definition`. Declared periods
are included when their physical end exceeds `as_of`, including partly elapsed
periods. Unknown support includes labels at/after `as_of`, without invented
bounds. Each peak retains its label, support bounds and method. A daily-mean
forecast produces the **water-level equivalent of its discharge statistic**,
not mean water level or an instantaneous flood maximum. Quantiles are computed
in discharge space before inversion; nonlinear transformation of member values
before type-7 interpolation would give a different quantity.

Conversion uses the exact configured station/tenant/model provisional reference
and the ordinary stored curve matching its complete immutable snapshot. The
response exposes curve id/version, validity dates, interpolation and tabulated
ranges, but no curve points or private reference evidence. Curve validity is
assessed at `as_of` and over each peak's physical support. `valid_to` is exclusive.
Expired configured curves remain explicitly provisional with expiry warnings;
not-yet-valid curves and periods beginning before validity have no numeric level.
This is read-time comparison context, not a claim about a curve used by the model
at issuance. No historic forecast values are changed.

Only the existing provisional linear/nonnegative curve policy is admitted.
Inverse conversion never clamps or extrapolates: `range_flag=below|above` gives
null level; exact discharge plateaus give `ambiguous`. Other reasons include
`reference_not_configured`, `reference_ambiguous`, `curve_missing`, `curve_changed`,
`reference_from_future`, `unsupported_interpolation`, and invalid forecast/QC.
Discharge peaks remain present when only the conversion is unavailable.

Verified reference offsets are subtracted once to return the measurement frame.
Match frontend threshold units/reference to `water_level_unit` (`m`) and
`water_level_reference` (`gauge_zero` or `masl`). Only `masl` means metres above
sea level. Owner-assumed same-zero comparisons carry that explicit basis, a
warning, and null reference; they cannot establish compatibility with an explicit
datum threshold. They may be displayed only with the same disclosed assumption.
The Nepal overview currently exposes no backend thresholds for its 21 stations;
this feature imports none and does not certify the frontend's threshold metadata.
