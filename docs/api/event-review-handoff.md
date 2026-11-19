# Event review frontend handoff — 18 November 2026

**Implemented and locally qualified; not deployed.** This is the ordinary-discharge
options/series slice. Period skill, ranking, comparator and danger thresholds are
not added by this delivery. Existing observations and stored skill stay separate.

Implementation includes the options/series slice and approved correctness fixes.
The PR/release handoff supplies the final immutable source reference; a branch name
alone is not a deployed version. Contract **2.15** is [api-v1-map.openapi.json](../spec/api-v1-map.openapi.json),
SHA-256 `4d2cb76ff925274f6187a50c4523d56481f4f9880cb5931153c3947ca6980ebc`.
The [synthetic series example](examples/review-series.json) validates against it.
Use this contract for development; wait for the release handoff before calling a
deployment. No test/production route is claimed available by this document.

## Requests

1. On station selection, `GET /api/v1/stations/{id}/review-options` supplies model,
   source, run/artifact/forcing/forecast timestep and actual stored `lead_hours` choices.
2. On model/lead/period selection, `GET /api/v1/stations/{id}/review-series` requires
   `model_id`, `lead_hours`, aware `start`, aware `end`, and `source_mode`.
   `operational` needs no hindcast identity. `hindcast` additionally needs the exact
   `hindcast_run_id`, `hindcast_artifact_id`, `forcing_type` and `time_step_seconds`
   returned by options. An old artifact selection fails 404 rather than switching.
3. Keep the existing `/stations/{id}/observations?parameter=discharge&start=...&end=...`
   read and `/stations/{id}/skill` read. Stored skill retains its own dates/sample
   count and is never the score for the selected period.

For the first Swiss deployment check, the plan identifies Thun 2030, station UUID
`780ee2f9-2e8f-4332-9810-81f1e87a277a`, June 2019 and lead 48 hours. Fetch options
first and use its actual CMAL or climatology source tuple. This recommendation is
from the earlier inventory, not a new live availability check. Landquart 2150 is
the earlier partial-coverage case. Do not fabricate source IDs from these examples.

The station must be in the reviewer/admin token's existing scope. Neither this
read nor the frontend adds stations to that scope. Outside scope returns 404;
consumer keys return 403. Shared code applies equally to Nepal and other deployments.

## Display semantics

The period selects valid timestamps in `[start,end)`, not issue timestamps. Display
the returned `lead_definition` verbatim. Each segment contains `source`, `points`
and `gaps`. Its source supplies kind, model/artifact/hash, training dates, forcing,
run, units and forecast timestep. Points carry `issued_at`, `valid_time`, `forecast_id`,
`stored_at`, numeric quartiles, QC, state and reason. First storage time is not
generation or publication time. Unknown metadata remains null.

`time_step_seconds` describes the forecast timestep, not issue frequency. Expected
issue cadence is unknown, so `gaps` stays empty; neither backend nor frontend should
infer missing issues or continuous coverage from spacing. Preserve stored
missing/null points when drawing lines. Failed or suspect
QC never returns numbers. RAW hindcasts are explicitly unchecked. Missing exact
quartiles stay null with a band reason; no interpolation is performed. Climatology
is labelled baseline. Source bounds in the series response cover recorded values
at the selected lead/period; options gives wider bounds. Neither promises complete
coverage or an evaluation period.

Ordinary options and series both exclude superseded forecasts. Active publication
reads instead follow the authoritative selected forecast IDs.

Current stored data has unknown interval support. Separate labelled operational
and hindcast timestamped reads work. `operational_with_hindcast` explicitly returns
422 `review_source_unsupported`; do not implement that fallback in the browser.
Do not infer daily means or compute period skill. Active publication gates prohibit
hindcast lookup and expose only selected operational data. Generic `withheld` for
an empty published period does not assert a hidden forecast exists or reveal its
dates/counts. Keep its returned reason.

Unknown/changed source: 404. Unoffered lead: typed 422 `lead_not_offered`. Offered
lead with an empty period: successful missing result. Limit errors use 422
`review_series_limit_exceeded`; SQL timeout is 503 `review_query_timeout`.
Review errors use `detail.code/message`; existing auth/scope errors retain the
shared API envelope. All review responses and supported preflights are no-store.

## Measured size and latency

Local disposable PostGIS 16 qualification, **not deployed capacity**: Apple M2 Max,
12 host CPUs, 64 GiB host RAM; Docker 12 CPUs/~15.8 GiB RAM. Synthetic year is 366
days, with 21 members and three stored leads per issue. The selected lead is 48 h.
One initial service read and instrumented query plan warm the DB before route
timings; these are single samples, not percentiles. No external network/edge latency.

| Selected period | Points | Serialized series bytes | Actual HTTP result | Local route time |
|---|---:|---:|---|---:|
| Daily year | 366 | 153,223 | 200 | 0.530 s |
| Hourly year | 8,784 | 3,646,692 before rejection | 422, 105-byte error | 3.426 s |
| Hourly 180 days | 4,320 | 1,794,132 | 200 | 2.526 s |

The **2 MiB uncompressed ceiling applies before compression**. The hourly point
limit does not promise that all 8,784 points fit the byte limit. Ask reviewers to
narrow an oversized hourly period; do not silently truncate or make per-issue
requests. 180 days fits this fixture, not every possible provenance/value payload.
The existing hourly-year observations response was 2,275,057 bytes, also above the
frontend's stated 2 MiB history bound; daily-year observations were 94,795 bytes.

Three simultaneous reads with one shared reviewer credential (daily year plus two
hourly 180-day reads) returned 200 in **3.272, 2.660 and 6.722 seconds**. The backend
updates the same token's last-used row inside each request transaction, so shared
credentials can serialize requests. This passed the local 10-second target without
adding infrastructure or indexes; it is not a deployment SLA.

Each series request used **one bulk data statement**, 11 SQL statements total
including auth and transaction/settings work, independent of issue count. No
per-issue HTTP requests or background forecast/skill work. One station/model review
uses options once, then series + observations + stored skill (three bulk data reads;
options is an additional selection read). All requests/retries count toward the
browser ceiling of 30/minute/page; no timer refresh.

The deployed edge limiter's key/window/shared-credential behavior is **unverified**.
The local benchmark bypasses Cloudflare and does not establish independent quotas
per tab, session or user. Two pages may share backend credentials and capacity.
There is no deployed edge configuration in this backend checkout to establish its
quota semantics; verify at rollout before promising them.
The checked-in [base](../../Caddyfile) and [Nepal](../../Caddyfile.nepal) Caddy
configurations contain no rate limiter. That is source evidence, not verification
of the running edge configuration.

## Release verification still required

- Complete owner review and PR approval; hold at PR. The independent patch review
  and approved correctness fixes are complete. The final full default suite passed:
  13,874 passed, 69 skipped, 22 deselected, 65 warnings in 4,353.36 seconds. No code
  changed after that run. Local qualification also passed; the earlier checkout
  and test-runner timer blockers are resolved.
- Supply a published immutable source/contract reference and actual deployment URL.
- On the intended deployment, verify reviewer station scope, options and a known
  daily hindcast; verify partial coverage, publication restrictions and byte errors.
- Confirm the edge limiter's credential/window behavior and observed response times.
- Keep period skill and automatic fallback unavailable until their separate work.
