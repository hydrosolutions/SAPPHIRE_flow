# The `/api/v1` interface for review dashboards (Plan 402)

**Consumer page for the SAPPHIRE-flow-map project** (and any other reviewer
dashboard). The map reads authenticated `/api/v1` routes, including
`GET /api/v1/forecast-lab/snapshot` for archived BAFU forecasts and ordinary
SAPPHIRE comparisons. Its `forecast-lab-snapshot/v2` wire format is unchanged;
see `docs/spec/forecast-lab-snapshot.md`. It does not replace authoritative
evaluation selection, QC diagnostics or skill routes.

The authoritative shape for the dashboard routes is the committed,
drift-tested contract: `docs/spec/api-v1-map.openapi.json`
(`api/map_contract.py::build_map_openapi()`, `tests/unit/api/test_map_contract.py`).
This page explains conventions the schema alone does not — auth, error
shape, query conventions, and the QC/skill semantics a reviewer needs to
interpret what is served.

## Versioning (D14)

`docs/spec/api-v1-map.openapi.json`'s `info.version` is a **hand-maintained
constant** (`api/map_contract.py::MAP_CONTRACT_VERSION`), never the package
version — start `"1.0"`. An additive change (a new field, route or enum
member) bumps the **minor** number; any other change bumps the **major**
number and is announced to the map session **before** deploy. Which kind of
change a given diff is stays a **review judgement**, not machine-checked
(owner, 2026-09-26); `tools/check_map_contract_version.py` (CI, PR-only)
mechanically fails a pull request that changes the file without SOME greater
version. Regenerate with `uv run python tools/generate_map_contract.py`
after editing routes/schemas or bumping the constant.

## Bulk latest observations

`GET /api/v1/observations/latest?parameter=discharge` accepts an authenticated
consumer, reviewer or admin bearer token. It returns `items`, `total`, `limit`,
`offset`, `parameter`, `source`, `window_start` and `generated_at`. Each item has
`station_id`, `code`, `network`, `measured_parameters` and nullable `observation`
(the existing observation DTO with timestamp, value, source and typed QC flags).

- `observation_hours`: default168, range1–720; the window includes both endpoints.
- `source`: one ObservationSource value, default `measured`.
- `limit`: default200, maximum200; `offset`>=0. All scoped stations count, sorted
  by UUID before pagination, including stations without a visible reading.
- `parameter` accepts `discharge` and `water_level` (contract 2.4). Invalid/missing query values
  return FastAPI422 (`detail` list), not the ordinary HTTP error envelope.
- Null means no visible record in this window. It does not reveal hidden history
  or assert that the station has never measured this parameter. A latest MISSING
  record retains its timestamp/QC and null value; failed/raw/unchecked QC is not
  silently replaced by an older passed reading. Clients derive age from the
  timestamps and must not label stale or unverified readings as current trusted
  values. No new unit certification or conversion is implied.
- Trusted-unit holds and consumer/reviewer delivery restrictions are applied
  before selection. Admin retains existing delivery visibility. Observation reads
  follow the existing observation route, not the forecast publication gate.
- Success, validation, authentication and internal-error responses are `no-store`.
  Nepal monitoring may request one bounded latest-water-level summary (720 hours),
  then a six-hour history only for the selected station. This does not authorize
  fleet history downloads. Historical visibility does not prove ingestion is current.

## Snapshot contract export

Contract 2.3 adds the existing snapshot route, including its bounded observation
window, repeated station-code filter and error responses. All snapshot responses
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

Every route below requires a Bearer access token (`security.md` §
Authentication). `GET /api/v1/qc/rules` and `GET /api/v1/stations/{id}/skill`
are **REVIEW-gated** (Plan 401): a `reviewer` or `admin` token only — a
`consumer` token gets `403`. `GET /api/v1/stations/{id}/rejected-forecasts`
is **REVIEW_OR_HUMAN-gated** (Plan 404 T3): a `reviewer`/`admin` token OR a
named human with a current station `review` grant (Plan 341) — a `consumer`
token still gets `403`; the two principal kinds use different verifiers,
picked from the bearer's shape, with a single shared `401` body on any
failure. Every route applies the token's **station scope**: an out-of-scope
or unknown station/forecast is `404` (never `403`
— existence is not revealed outside scope); the station **list**
(`GET /api/v1/stations`) is the one exception — it silently filters to the
token's scoped stations rather than 404ing. **The raw token must stay
server-side** in the map's own secret store, never in client-side code.

## Error envelope

`400`/`401`/`403`/`404` all share one JSON body: `{"error": <message>,
"detail": null}` (`api/errors.py`). FastAPI's own request-validation `422`
is different and NOT normalised: `{"detail": [...]}` — a list of validation
errors, one per malformed/missing field.

`GET /api/v1/stations`, `GET /api/v1/stations/{id}/observations`,
`GET /api/v1/stations/{id}/forecasts`, `GET /api/v1/qc/rules`,
`GET /api/v1/stations/{id}/skill` and `GET /api/v1/stations/{id}/rejected-forecasts`
return `400` for a malformed query value
or `station_id`. **`GET /api/v1/stations/{id}` and `GET /api/v1/forecasts/{id}`
do not** — their path id is parsed unguarded today and a malformed one
returns `500`, not `400`. `/qc/rules` (the station variant), `/skill`
(Plan 402) and `/rejected-forecasts` (Plan 404, reusing the same helper)
were built with a guarded parse; the two pre-existing detail
routes were left as they are (out of scope for those plans).

`GET /api/v1/qc/rules?station_id=` additionally returns `500` — in the same
`{"error": "internal_server_error", "detail": null}` shape — when the
server's own onboarding configuration is invalid (e.g. a duplicate declared
per-station threshold block); the deployment variant (no `station_id`)
still answers `200` in that case, since it never reads the onboarding
config.

## Query conventions

Timestamps are ISO-8601; a naive (no offset) value means UTC. `end` is
**exclusive** where a route takes a `start`/`end` window. The forecast
list's default window (no `start`/`end` given) is the last 7 days up to
request time. `parameter` values are the deployment's parameter names
(`discharge`, `water_level`, …), not aliases. Paginated list routes
(`/stations`, `/stations/{id}/forecasts`) take `limit`/`offset`, capped at
`limit<=200`; `/stations/{id}/observations` is unpaginated — every matching
row in the window is returned.

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
(`OBSERVATION_QC_THRESHOLD_CONFIG`) — never in this route's response. The
station view mirrors **scheduled ingest only**: history checked at
onboarding used the network thresholds; history checked by a delivery
import (the six DHM stations, once Plan 268 T7 has run) used their declared
ceilings — the view's current limits are **not** necessarily the ones
behind that historical data's flags. Scheduled DHM ingest judges **water
level only** (touchpoint-maps.md) — for the six DHM stations the station
view shows the network limits (`station_qc: "not_judged"`) until Plan 300's
activation follow-on promotes them, and even once operational, scheduled
ingest there judges water level only — discharge ceilings shown after
promotion apply to **no scheduled reading**; discharge QC on those stations
comes only from Plan 268's import until a rating-curve path exists.

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

## Skill (`GET /api/v1/stations/{id}/skill`)

One row per `(model, artifact, skill_source, forcing_type, time_step,
phase_offset, lead_time, season, flow_regime, metric)` on the artifact the
station's **forecast** currently uses (station-scoped first, else an active
group artifact) — headline and season/flow-regime breakdowns alike.
`time_step_seconds`/`phase_offset_seconds` ARE carried (a prior message to
the map session wrongly said skill rows had no time step). `evaluated_on`
compares the row's eval window against its artifact's training period as
**closed intervals**: fully inside -> `"training_period"`; no shared instant
-> `"outside_training_period"`; anything else, including an eval window
starting exactly at `training_period_end` -> `"overlaps_training_period"`.
Today every active artifact's eval window is inside its training period —
every served score is in-sample; the map shows these as fit scores and
ranks nothing. `score: null` means the metric is undefined for that
stratum (a non-finite stored value), not zero. Empty `rows` is normal — it
is what the two fallback model tiers (`persistence_fallback`,
`climatology_fallback`) serve today. Combined (pooled/BMA) forecasts have
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
configures its own season names (`architecture-context.md`); do not
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

`docs/architecture-context.md` § S.4 carries the full definitions and
interpretation-band literature references (Moriasi et al. 2007) — this
table is the map's quick-reference, not the source of truth.

## Observations and forecasts (existing routes, D13's two additive fields)

`GET /api/v1/stations/{id}/observations` and the forecast routes are
unchanged in shape except two **additive** fields, visible to **every**
authenticated role including `consumer` (D13 — thresholds are not sensitive,
and a caller looking at a flagged reading/forecast needs to see why):

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
active (the Swiss deployment today), reviewer and admin tokens both see
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
consumer tokens are not accepted. The map OpenAPI contract is
version 2.7, including the unchanged compact series described below. The payload has `schema_version: 1`. Success, errors, and GET-only CORS
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
no ordinary, yesterday, or staged fallback. All current groups are validated before
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
budget. No backend scheduler or cache is added. See `swiss-overview.example.json` for a one-station excerpt from the isolated synthetic
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
