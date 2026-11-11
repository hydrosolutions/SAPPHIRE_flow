# Network overview

`GET /api/v1/networks/{network}/overview` is an additive bearer-authenticated
contract. It returns the complete authorized roster or an explicit error, never
a silently truncated roster. Unknown and unauthorized networks both return404.
The default purpose is `standard`. Repeat `data_use` to request standard and/or
`provisional_rating`; provisional requires reviewer/admin. Unknown/duplicate query
values fail after authentication. Existing routes are unchanged.

## Verification

Authentication tests inventory the overview separately because datum disclosure is
role-dependent, and verify scoped consumer/reviewer rosters. Capacity benchmarks
use the dedicated committed-write test database: concurrent requests require
committed fixtures, which must not contaminate rollback-based store tests.
Transport-log suppression tests explicitly enable their test logger and restore
its previous state; API logging initialization can otherwise disable it before
these tests, masking the restoration assertion.

## Station display metadata

Each station includes `thresholds` in the existing station-detail shape:
`danger_level`, `parameter`, `value`, `source`. Rows are ordered by parameter,
danger level and source. An empty array means no configured thresholds; a failed
read fails the request. Water-level thresholds use metres and discharge thresholds
use m³/s, as on station detail. No import, conversion, inferred provenance or alert
configuration change is performed. Thresholds are read in one scoped batch.

`water_level_datum` is tagged: reviewers/admins receive
`{"availability":"disclosed","value_masl":123.0}` or an explicit null value
when no datum is configured. Consumers receive only
`{"availability":"not_disclosed"}`. Nondisclosure must never be interpreted as
no datum. This preserves the existing reviewer-only QC metadata boundary without
fetching QC rules. The value retains existing `water_level_datum_masl` semantics;
no vertical reference system or threshold/sensor compatibility is inferred.
Observation values are not transformed by this metadata projection.

Both fields participate in the semantic ETag. Frontend issue
[SAPPHIRE-flow-map#111](https://github.com/hydrosolutions/SAPPHIRE-flow-map/issues/111)
owns replacing detail/QC fan-out and adding temporary Worker thresholds where the
API has none for that station/parameter. Preserve staged provenance/labels. A
Worker-transformed response needs a validator covering its overlay too; otherwise
use the agreed unconditional polling option rather than forwarding a backend 304
as evidence that Worker-held thresholds are unchanged. Swiss routes stay unchanged.

## Observations

The projection selects72 completed UTC hours with right-closed windows and a
separate168-hour latest window. `hourly_end` is the completed UTC-hour boundary.
`latest_lookback_seconds` and `generated_at` disclose the independent latest window;
`latest.end` is that full200 body's as-of, not a newly refreshed timestamp after304.

Eligible sources are measured and authorized supplied/manual imports, never
rating-derived or component-derived Q. Finite raw, unchecked, suspect and passed
readings retain their QC labels. Failed readings contribute only their authorized
latest-failed timestamp. `newer_failed` is null if there is no selected sample.
Equal-time source conflicts return an `ambiguous_source` gap, even when values
agree, rather than an invented source preference. Trusted unit/quarantine filters
apply before selection. Consumers cannot see protected deliveries; reviewers have
the same scoped private permission as station observation detail.

The client applies180-minute staleness to the actual observation timestamp, not
`generated_at`, HTTP Date, or the slot end. Missing Q is not filled from a forecast
or a rating conversion.

## Forecasts

Each active model and requested purpose has a headline or explicit absence.
Standard daily models retain whole-summary authority; rejected references never
load values or fall back to other rows. Accepted daily outputs retain the existing
passed/suspect and frozen-contract eligibility. Publication selection precedes all
STANDARD value reads for every role. Other ordinary/provisional outputs retain
their existing purpose-specific QC rules; a QC label is not a stored rejection.
Provisional headers are scoped before native lineage access and parent agreement
is validated. No raw lineage or curve files leave the endpoint.

Compact series retain the current support interval, earliest median peak and its
same-time quartiles, endpoints and both sides of gaps. Stored quantile levels are
not treated as members. Member quartiles use type7 with at least five members.
The24-point reduction selects existing points, never interpolates; too many
mandatory anchors retain the headline but make the compact chart unavailable.
Missing bands remain null with explicit band availability. Negative datum-relative
water level remains valid. Full trajectories remain on the ordinary detail route.

Freshness policy is keyed by tenant/network/model/parameter/purpose and is set per
deployment in its configuration. No applicable policy means unknown, never
implicitly current; there is no built-in rule, including for the standard daily
evaluation product. `timezone` sets the product day and delivery deadline;
optional `issue_timezone` (default: `timezone`) sets the zone of `issue_time`.
Prior output stays current until the deadline, then stale until its replacement
is visible. A Nepal example (configuration only, not schedule or deployment
authority):

```toml
[[network_overview.freshness_policies]]
tenant_id = "00000000-0000-0000-0000-000000000001" # use actual tenant
network = "dhm"
model_id = "cmal_small"
parameter = "discharge"
data_use = "provisional_rating"
timezone = "Asia/Kathmandu"
issue_time = "05:45:00" # persisted00UTC, not scheduled06NST
delivery_deadline = "07:00:00"
```

A Swiss standard daily example: issued at 00:00 UTC, due by 07:00 Swiss local time
(06:00 UTC in winter, 05:00 UTC in summer):

```toml
[[network_overview.freshness_policies]]
tenant_id = "00000000-0000-0000-0000-000000000002" # use actual tenant
network = "bafu"
model_id = "cmal_small"
parameter = "discharge"
data_use = "standard"
timezone = "Europe/Zurich"
issue_timezone = "UTC"
issue_time = "00:00:00"
delivery_deadline = "07:00:00"
```

## Polling and limits

Successful200/304 responses use `private, no-cache, max-age=0, must-revalidate`.
Every request reauthenticates and recomputes scope/data before304. The weak ETag
includes token identity, scope, purpose and semantic data; it excludes only body
assembly time and the descriptive latest-query end. Hour/deadline/support changes
can invalidate without inserted rows. A304 retains the previous body's truthful
generated_at. HTTP Date is current check time. Never share personalized cached
bodies across credentials. Errors, redirects and OPTIONS are no-store.

Only this route admits If-None-Match and exposes ETag/Date under existing configured
consumer origins. It adds no origins or credentials. Gzip is negotiated only for
this route; actual Content-Encoding/Content-Length receipts are distinct from
offline gzip estimates. Existing no-store/CORS behavior is unchanged.

Initial admission caps:200 authorized stations, four output entries per station,
512 native valid times/output and250,000 selected value rows overall. These caps
intersect; their Cartesian maximum is not promised. Daily evidence is bounded to
16groups×7days,224 selected latest/authoritative attempts,8MiB frozen payload and
25,000 members/references before materialization. Retry history is not loaded.
A capacity/evidence failure returns503, never an empty success. The20 data-query
budget excludes authentication bookkeeping and timeout-setting statements.

Local PostGIS 16 measurements (11 November; Apple M2 Max, 64 GiB RAM,
Python 3.12.10; four outputs/station,44 native times,
seven quantiles,24 compact points, nonzero varied data; observations every
10 minutes over the full 168 h latest lookback, about 1,830 rows per station) used
nine data queries. Five serial reads/cohort gave warm empirical p95 of
0.519s/3.169s/3.904s for 21/160/200 stations. Actual negotiated gzip sizes were
145,185/1,100,314/1,375,425 bytes. Four simultaneous request connections all
succeeded, with maximum durations 1.458s/9.491s/11.637s respectively. This is a
coincident sixty-second polling burst, not a sustained operational-load or
live-server qualification. Auth bookkeeping was excluded from this benchmark;
actual API-role/real bearer behavior is tested separately.

The observation read walks the station/time index newest-first per station,
parameter and slot, and stops at the first eligible sample, so the per-row
unit-read check runs on candidates only; JIT is off for that statement. On the same
200-station data it took about 0.8 s, versus more than 3.6 s (statement timeout)
for the earlier whole-cohort ranking. Its 2 s statement timeout is unchanged.
An earlier hourly-only fixture (72 rows per parameter) understated this cost.
Early stopping needs eligible samples: a series with none in the window (for
example all unit-held, or all failed QC) is checked row by row across the whole
168 h, as every row was before, and the failed-marker read scans the window for all
stations. The benchmark has no such series and no sub-10-minute cadence.

The one-second target is not met at160/200 stations. With 10-minute observations
both the 160- and 200-station responses exceed 1MiB gzip. Do not advertise these caps
as a one-second guarantee or all sizes as under1MiB. No timeout, cap or index was
expanded to hide the shortfall. These results require owner disposition; no live
acceptance or operational adoption is implied. Header quality/lineage payloads are
also capped at8MiB per selected purpose before Python materialization.
