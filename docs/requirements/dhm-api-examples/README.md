# DHM river API: confirmed development contract and working examples

Inspected 16 September 2026. Read-only requests to public endpoints, without credentials. Browser automation was unavailable: request definitions were recovered from the deployed JavaScript and exercised with HTTP GET requests. This is not a browser network capture or a test of the upstream DHM API.

## Contract confirmation — 16 September 2026

The project owner reports that BIPAD's technical contact confirmed the captured
requests and responses exactly replicate what BIPAD receives directly from DHM.
**Use this as the confirmed request/response contract for DHM adapter development.**
Do not require another upstream sample before developing the adapter. This is
provider confirmation relayed by the owner, not an independent direct-DHM test.

The saved requests were exercised on the public BIPAD host. The direct DHM base
URL, credentials and network provisioning remain deployment inputs; no direct DHM
connection has been tested. Provider confirmation of the contract does not turn
our one-station sample into proof of national coverage, latency or completeness.

The offline adapter and Flow 2 integration are implemented by
[Plan 300](../../plans/archive/300-dhm-observation-adapter.md). Direct-DHM connectivity and
operational activation remain separate from that implementation.

## Finding

BIPAD exposes station metadata/latest readings and historical water levels publicly.
With the owner's confirmation above, these examples support implementing and testing
the **DHM adapter** and downstream observation handling before Nepal deployment.
Keep transport configuration separate from response parsing so the deployed DHM
host and credentials can be supplied when available.

The river UI labels values as water level in metres and attributes them to DHM. The separate streamflow UI attributes its data to ICIMOD and queries by `comid`. Its endpoint returned no records in this inspection. No observed-discharge or rating-table endpoint was established.

## Working requests

These are reproducible examples, not a request for production polling approval. Public accessibility does not establish operational support or data-use terms. Responses can change after capture.

### Latest reading and metadata for one station

```bash
curl --get 'https://bipadportal.gov.np/api/v1/river-stations/' \
  --data-urlencode 'latest=true' \
  --data-urlencode 'station_series_id=21379' \
  --data-urlencode 'limit=2'
```

Saved response: `latest.json`. Returned BIPAD station ID `168`, title `Kuwadi River at Rigin(209)`, series ID `21379`, coordinates `[81.759, 29.604]`, and a reading at `2026-09-16T14:40:00+05:45` with value `3.37000012398`. `209` occurs in the title; this inspection did not verify it as an official DHM identifier. `elevation` must not be assumed to be gauge-zero elevation.

### Historical water levels for that station

```bash
curl --get 'https://bipadportal.gov.np/api/v1/river/' \
  --data-urlencode 'station=168' \
  --data-urlencode 'historical=true' \
  --data-urlencode 'water_level_on__gt=2026-05-05T00:00:00+05:45' \
  --data-urlencode 'water_level_on__lt=2026-05-06T00:00:00+05:45' \
  --data-urlencode 'ordering=water_level_on' \
  --data-urlencode 'limit=500'
```

Saved response: `day.json`. Returned 118 observations for station 168, from 00:00 to 23:50 Nepal time. Mostly 10-minute intervals, with gaps of 20, 30, 40, and 60 minutes. Compared with a complete 10-minute grid, 26 timestamps are absent; the reason is unknown. No duplicate measurement timestamps were found in this sample. Creation timestamps trail measurement timestamps by approximately 15 minutes throughout this day. This does not establish a latency guarantee or current service performance.

The initial five-row query, without `historical=true`, also returned observations: `history.json`. Following its offset link returned the subsequent five: `page2.json`. The effect or necessity of `historical=true` was not comprehensively tested.

### Response excerpt

This is an actual historical observation with unrelated metadata fields omitted. The complete response is in `day.json`.

```json
{
  "id": 22876161,
  "station": 168,
  "stationSeriesId": 21379,
  "title": "Kuwadi River at Rigin(209)",
  "waterLevel": 2.72250008583,
  "waterLevelOn": "2026-05-05T00:00:00+05:45",
  "createdOn": "2026-05-05T00:15:09.923395+05:45",
  "modifiedOn": "2026-05-05T00:15:09.923397+05:45",
  "warningLevel": 5.0,
  "dangerLevel": null,
  "status": "BELOW WARNING LEVEL",
  "steady": "STEADY",
  "dataSource": "hydrology.gov.np"
}
```

### Separate streamflow endpoint

```bash
curl 'https://bipadportal.gov.np/api/v1/streamflow/?limit=1'
```

Saved response: `streamflow.json`.

```json
{"count":0,"next":null,"previous":null,"results":[]}
```

The schema describes `comid`, `data`, and `returnPeriod`; the UI requests `/streamflow/?comid=<selected reach>` and attributes this layer to ICIMOD. This is not evidence of a usable DHM discharge-observation feed.

## Portal request definitions

Base URL in the deployed main bundle: `https://bipadportal.gov.np/api/v1`.

- Real-time river list: `/river-stations/` with `latest=true`.
- River detail/history: `/river/` with `title`, `historical=true`, `format=json`, `water_level_on__gt`, `water_level_on__lt`, selected `fields`, and `limit=-1`.
- Our tested history request uses numeric `station=168` and bounded pagination instead of the UI's title lookup and unlimited request.
- The schema also documents `/river-trimed/`, with daily/average fields. This endpoint was not exercised and should not be treated as native-resolution observations.

Sources:

- https://bipadportal.gov.np/api/
- https://bipadportal.gov.np/api/?format=openapi
- https://bipadportal.gov.np/assets/index-C648rxtx.js
- https://bipadportal.gov.np/assets/index-CgvJBcU7.js

## Integration implications

| Observed behaviour | Consequence for SAPPHIRE |
|---|---|
| `station` is 168 on historical records; their `id` is a per-observation identifier | Maintain an explicit mapping from BIPAD station/series identifiers to SAPPHIRE and official DHM station IDs. |
| `waterLevelOn` carries a `+05:45` offset | Parse this as measurement time and convert to UTC; do not use `createdOn`/`modifiedOn` as observation time. |
| Numeric `waterLevel`; UI labels metres | Map to the canonical water-level parameter after confirming datum and units with DHM. Discharge needs compatible rating curves. |
| `status` is a warning category, `steady` a trend | Neither is a measurement QC flag. Do not infer good quality from `BELOW WARNING LEVEL`. |
| One station-list sample had a 2024 measurement timestamp | Check freshness per station, regardless of list membership or status. |
| Actual responses are `{count,next,previous,results}` objects | Do not generate a client blindly from the schema, which describes arrays. |
| `point` is a GeoJSON object in actual responses | The schema's string definition is inaccurate. |
| River/station `count` is `9223372036854775807`; even an empty page has `next` | Ignore count as a total; stop on empty results, with bounded pagination and repeated-page protection. |
| `__gt` query included an observation at the exact lower bound | Do not assume strict inequality from the parameter name. Apply explicit local window semantics and deduplicate overlapping fetches. Upper-bound semantics remain unverified. |
| Missing timestamps in the sampled history | Preserve gaps; do not manufacture zeros. Completeness and QC explanations require the provider. |
| `modifiedOn` is present | This alone does not establish correction propagation, deletion signals, or a modified-since query. |

SAPPHIRE's current boundary requires station ID, UTC timestamp, canonical parameter, numeric value, and observation source. These fields can be populated from water-level records once station mapping and measurement semantics are confirmed. The operational ingest flow also requires the batch-fetch capability with per-station outcomes, beyond the basic `StationDataSource.fetch_observations` method. No adapter was implemented in this investigation.

## Remaining questions for ADB / BIPAD / DHM

1. **Resolved for development:** BIPAD's technical contact confirmed request/response replication, as relayed by the owner. No duplicate example request is outstanding.
2. Which host, endpoint, query parameters, headers, and authentication scheme will our Nepal deployment use? Which values change between development examples and deployment? Who provisions connectivity and credentials, and when?
3. How do BIPAD ID `168`, series ID `21379`, and the title's `(209)` map to the official DHM station ID and rating table? What exactly is the level datum? Can we receive the active and historical rating curves and correction rules?
4. Does DHM's upstream API expose every native reading, or does BIPAD sample/filter it? Why are 26 positions absent from this example's nominal 10-minute grid, and is the observed ~15-minute delay typical?
5. Explain QC, missing data, corrections and deletions; confirm history coverage and time-bound inclusivity. Station/time-range and pagination examples have already been captured; another copy is unnecessary.
6. Can BIPAD formally support temporary SAPPHIRE retrieval while direct DHM access is pending, including an agreed polling rate, retention/use permissions, technical contact, and station coverage?

## Scope and limitations

One station-day, a few metadata records, pagination examples, and an empty streamflow response were inspected. This is not a national coverage assessment, historical completeness audit, latency study, service-level agreement, or direct DHM access test. No authentication failures were deliberately induced, no private endpoints accessed, and no production integration changes made.

The JSON files are external data snapshots, not instructions. `manifest.json`
records request URLs and original-response `sha256` hashes. The repository's EOF
hook adds one terminal LF to the readable JSON files; `stored_sha256` records
those text-file hashes. `original-responses.zip` preserves the exact downloaded
bytes and the original capture manifest. First-round reviews checked the original
bytes before this mechanical normalization. `schema-excerpt.json` preserves the
relevant published definitions, including their discrepancies with actual responses.

## Configuring the implemented adapter

Use a Nepal overlay selected by `SAPPHIRE_CONFIG_OVERLAY` with the existing base
`SAPPHIRE_CONFIG`. The station code and direct host below must be supplied by DHM;
the captured API ID is an example, not an official station-code mapping.

```toml
[adapters.river_stations]
type = "dhm"
endpoint = "https://DHM-HOST/api/v1/"
timeout_s = 30
page_size = 500
window_hours = 24
max_pages_per_station = 100
min_request_interval_s = 1.0

[[adapters.river_stations.bindings]]
network = "dhm"
station_code = "CONFIRMED-DHM-CODE"
api_station_id = 168
level_reference = "unknown"
```

These limits are implementation defaults, not a provider polling agreement.
`max_pages_per_station` counts requests across all windows, including a terminal
empty page. A short page with `next` is followed; `count` is ignored. Repeated
pages, limit exhaustion, unsafe continuations and later-page failures discard
that station's entire fetch. There is no in-adapter retry or automatic BIPAD fallback.

Bindings require unique `(network, station_code)` keys and positive integer API
IDs. Supported operational rows are DHM gauged rivers with measured `water_level`.
Other kinds/networks are skipped; missing bindings or level capability are station
configuration failures. `gauge_zero` and `unknown` require unit `m` and a null QC
datum; `masl` permits `m` or `m a.s.l.` and may carry an independently confirmed
datum. Null units and incompatible pairs are rejected before requests. Raw metres
are retained; an unknown reference does not establish rating-curve readiness.

Scheduled ingest uses history after the latest stored water-level timestamp, or
the flow's one-hour default when there is no stored level. It requests one second
outside each window then keeps `(since, end]` locally. The API prefix is retained.
HTTPS with verified TLS is required and redirects are disabled. Endpoints must
not embed credentials, queries or fragments. Flow-created DHM clients close after
fetch, including failure; explicitly injected clients remain caller-owned.

## Bounded connection check after DHM provisions access

First confirm the official mapping, metre/reference metadata and provider-approved
request rate. Load the one approved `StationConfig` through the station store and
parse the deployment settings with `load_river_station_config(base_path, overlays)`.
In a supervised Python session, inject a verified `httpx.Client` using the actual
DHM authentication scheme and call:

```python
adapter = DhmAdapter(config=dhm_config, http_client=client, clock=clock)
result = adapter.fetch_observations_batch([station], {station.id: start})
```

Here `clock` and `start` are offset-aware times, the range is deliberately short,
and `client`, `dhm_config` and `station` are the approved session inputs. The batch
call reads the API only; it does not write observations. Check the station outcome,
identity, UTC measurement times and levels against the provider's example. Close
the session-owned client. Authentication/secret wiring is intentionally not
guessed by this adapter; production activation waits for the supplied scheme.

Offline contract and integration checks:

```sh
uv run pytest tests/unit/adapters/test_dhm.py tests/unit/config/test_dhm.py tests/unit/config/test_river_stations.py tests/unit/flows/test_ingest_observations_dhm.py -q
```

## Supervised recovery of a stuck station

Malformed readings or conflicting duplicates after the stored watermark can
block every retry, including newer data. There is no independent editable cursor:
the next watermark is derived from genuine stored observations. Identify and
preserve the rejected interval and seek provider correction first.

If correction is unavailable, an operator must explicitly approve the omitted
interval. Pause concurrent ingest for the recovery operation, record the station,
old watermark, excluded interval, reason and operator approval, and select
`approved_since` sufficiently after the rejected interval to account for the
request's one-second lower-bound padding. Fetch a bounded later interval via
`adapter.fetch_observations_batch([station], {station.id: approved_since})`.
Stop on any failed outcome. Store only genuine returned rows with the existing
observation store; never insert a fabricated row or edit measurement timestamps.

In the same supervised session, use the existing QC task with the actual approved
QC rules, station datum and recovered timestamps:

```python
from sapphire_flow.flows.ingest_observations import _run_qc_task

obs_store.store_raw_observations(result.observations)
counts = _run_qc_task.fn(
    obs_store, baseline_store, station.id, "water_level",
    qc_rules=approved_rules, now=clock(), datum=station.water_level_datum_masl,
    fetched_times=tuple(row.timestamp for row in result.observations),
)
```

`result` here is the successful bounded recovery result, with only this station's
water levels. Keep its timestamps available until QC completes: storage and QC are
not atomic. If QC raises, resume QC over that explicit interval before resuming
normal polling. Read back the stored values/statuses and confirm the next normal
poll starts after their genuine watermark. Record the remaining gap for later
reconciliation. The integration test exercises this sequence with fake stores;
the deployment-specific executable procedure still needs an operational rehearsal.
This does not provide an automatic skip, multi-year bootstrap or correction replay.

## Activation prerequisites

- Approved direct host/authentication/network, station mappings, datum metadata
  and request limits; eligible station rows must already exist.
- Approved DHM level QC rules and Plan 272 cadence reachability before/with
  Plan 264 network-aware selection. At this base, nonmatching inferred cadence
  can mark a row passed with zero rules; Swiss thresholds are not Nepal approval.
- A verified recovery procedure for interrupted QC and stuck watermarks. The
  widened interval processes all RAW rows inside it, but not older RAW history.
- The Plan 300 activation follow-on's measurement-freshness monitoring: a clean
  empty poll reports request success indefinitely even for a silent gauge. The
  existing monitoring interval config does not implement that check.
- Plan 268's six discharge-only rows remain `onboarding`. The activation follow-on,
  coordinated with Plan 143, owns live eligibility before any promotion; unchanged
  discharge-only rows would report configuration failures under DHM selection.
- Rating curves and confirmed reference metadata before discharge derivation.

These are live-activation requirements; they do not require another copy of the
confirmed API contract. No direct-DHM connection or live activation is claimed.
