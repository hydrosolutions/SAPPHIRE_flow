# SAPPHIRE Flow API guide

Read station data and forecasts with your personal API key. The API is shared
across deployments; your administrator supplies the base URL, access scope and
available features. All examples below are read-only.

| I want to… | Request below the API base URL |
|---|---|
| See station names, codes and configured status | `GET /stations` |
| See a network's observations and forecasts together | `GET /networks/{network}/overview` |
| List one station's forecasts | `GET /stations/{station_id}/forecasts` |
| Download a forecast's complete ensemble | `GET /forecasts/{forecast_id}` |
| Read a station's observation time series | `GET /stations/{station_id}/observations` |
| Find the latest stored reading at each station | `GET /observations/latest` |
| Inspect daily rating-derived inputs to a forecast | `GET /forecasts/{forecast_id}/rating-input-history` |

The [OpenAPI reference](../spec/api-v1-map.openapi.json) defines exact request and
response structures for the supported dashboard/read routes. It does not describe
every internal or administrator route. The [maintainer reference](../spec/api-v1-review.md)
covers specialist QC, skill, evaluation and deployment contracts.

## Connect and make your first request

Set `API_BASE` to the URL supplied by your administrator, ending in `/api/v1`
with no trailing slash. Load your personal key into `SAPPHIRE_API_KEY` using your
organization's secure instructions. Keep it out of URLs, screenshots and shared
scripts. Requests authenticate with `Authorization: Bearer`:

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${SAPPHIRE_API_KEY}" \
  "${API_BASE}/stations?kind=river&limit=200"
```

The dashboard's emailed one-time login code is **not** an API key. Your API key
is reusable until it expires or is revoked; a one-time link used to retrieve it
does not make the key single-use. Dashboard access can have additional deployment
login steps. For a hosted dashboard, keep the API key in its server-side secret
store, never in browser code.

Your key also determines which stations and routes you can read. Ordinary reads
accept consumer, reviewer or administrator keys. **Provisional-rating forecasts
and daily rating-input history require reviewer or administrator access.** A
consumer key gets `403` for those requests.

## Find stations

The station list returns `items`, `total`, `limit` and `offset`. This is an
**excerpt** from the [complete synthetic station example](examples/stations.json):

```json
{"id":"00000000-0000-4000-8000-000000000001","code":"DEMO-1","name":"Example River","station_status":"operational"}
```

Use `id`, the UUID, in API paths. `code` is the provider's display identifier;
passing a code instead of a UUID does not work. `station_status` is configured
lifecycle status: `operational` does not promise recent sensor readings or a
successful forecast. `measured_parameters` also describes configuration, not
data availability. `has_provisional_forecast` is an existence hint for reviewers,
not a freshness check; it is `null` for consumers.

For a smaller terminal table, with `jq` and `column` installed:

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${SAPPHIRE_API_KEY}" \
  "${API_BASE}/stations?kind=river&limit=200" \
  | jq -r '["CODE","NAME","STATUS"], (.items | sort_by(.code)[] | [.code,.name,.station_status]) | @tsv' \
  | column -t -s "$(printf '\t')"
```

The station list supports `kind` and `status`, for example
`kind=river&status=operational`. It has no network filter. A page contains at most
200 stations; when `offset + items.length < total`, request the next page with
an increased `offset`. The table command displays one page.

## Get forecasts

Set `STATION_ID` from the station list. Ordinary forecasts use `data_use=standard`:

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${SAPPHIRE_API_KEY}" \
  "${API_BASE}/stations/${STATION_ID}/forecasts?parameter=discharge&data_use=standard&limit=20"
```

Lists are newest first and default to the preceding seven days of **issue time**.
Use `model_id`, `start`, `end`, `limit` and `offset` to narrow or page results.
Use ISO 8601 times with `Z` or a UTC offset; `start` is inclusive and `end` is
exclusive. A model or category can legitimately return an empty `items` list.

For **both categories**, reviewer/admin callers make two list requests: one with
`data_use=standard`, one with `data_use=provisional_rating`. Omitting the parameter
returns standard only. The list and detail routes accept one category per request.
The two categories can coexist at the same issue time; neither automatically wins.
Keep the category alongside the forecast ID.

Set `FORECAST_ID` from a list item and `DATA_USE` to the category used for that list:

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${SAPPHIRE_API_KEY}" \
  "${API_BASE}/forecasts/${FORECAST_ID}?data_use=${DATA_USE}"
```

Using the wrong category returns `404`. A detail includes `ensemble.units`,
`ensemble.representation`, `ensemble.valid_times` and `ensemble.series`. Each series
array aligns with `valid_times`; its key is a member ID or quantile level according
to `representation`. See the complete synthetic
[ordinary](examples/forecast-standard.json) and
[provisional](examples/forecast-provisional.json) detail responses.

This ordinary detail **excerpt** illustrates the timing distinction:

```json
{"issued_at":"2026-11-16T00:00:00Z","created_at":"2026-11-16T00:30:00Z","nwp_cycle_reference_time":"2026-11-15T00:00:00Z"}
```

`issued_at` is the forecast issue time, `created_at` is storage time, and the NWP
reference is the weather run. `weather_use` adds retained weather-source/freshness
facts when recorded. Missing weather metadata means unknown, not current.

Where a deployment enables human publication, ordinary reads follow that gate:
an unpublished forecast is not a public forecast; withdrawn detail can return
`410`. Provisional forecasts have `badge: "PROVISIONAL"` and
`publication_applicability: "not_applicable"`. Preserve their warnings.

### Network overview

Set `NETWORK` to a network returned by your station list. Unlike list/detail,
the overview accepts repeated `data_use` values for both categories in one request
(reviewer/admin access required):

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${SAPPHIRE_API_KEY}" \
  "${API_BASE}/networks/${NETWORK}/overview?data_use=standard&data_use=provisional_rating"
```

Use each forecast's `detail_url` unchanged for category-correct detail lookup.
Forecast `freshness` is separate from input quality; `unknown` does not mean
current. The overview includes a compact series, not the full ensemble. Its
observation blocks contain an independent seven-day latest-reading lookup and
72 completed hourly slots, selecting the last eligible reading in each hour.
They are not hourly means. Missing slots stay gaps. In the documented Nepal
baseline, the discharge block does **not** project rating-table conversions.
See the [overview contract](../spec/network-overview.md) for availability states,
compact series and private conditional polling.

## Get observations and discharge history

Choose a bounded window appropriate to the selected station:

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${SAPPHIRE_API_KEY}" \
  "${API_BASE}/stations/${STATION_ID}/observations?parameter=water_level&start=2026-11-15T00:00:00Z&end=2026-11-16T00:00:00Z"
```

Replace the sample dates with your window. This returns a JSON array at native
stored timestamps, with no pagination. Both dates are required; `end` is exclusive.
Use `parameter=discharge` for stored discharge. This route has **no `data_use`
switch** and does not expose provisional rating-table conversions. Adding
`data_use=provisional_rating` does not turn water levels into discharge.
An empty array means no visible matching observations, not zero flow. See the
[complete synthetic observation example](examples/observations.json).

For the latest reading across your station scope:

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${SAPPHIRE_API_KEY}" \
  "${API_BASE}/observations/latest?parameter=water_level&observation_hours=168&limit=200"
```

`observation_hours` accepts 1–720 hours. The inclusive window ends at `generated_at`.
Each station has a nullable `observation`: no visible record gives `null`; a stored
missing record retains its timestamp and QC with `value: null`. Latest does not
mean QC-passed or recent enough for your application; read the timestamp and QC.

For daily rating-derived context, use a **provisional forecast ID** and a
reviewer/admin key:

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${SAPPHIRE_API_KEY}" \
  "${API_BASE}/forecasts/${FORECAST_ID}/rating-input-history"
```

This returns seven completed UTC days before that forecast's issue date, with
daily discharge, support, warnings and frozen curve identities/validity. It is
daily input-assembly context, **not** native discharge history or the final model
tensor. `no_retained_derived_day` does not prove the model had no input. The
forecast detail's empty `rating_curves` with `rating_provenance_status: "not_exposed"`
means that detail does not list curves, not that none were used.

**Not yet deployed in the documented Nepal baseline:** a shared station discharge
history route for native/hourly rating-derived values. Do not infer it exists from
the daily forecast-input route. The separate
[Swiss observation-history route](../spec/api-v1-review.md#swiss-observation-history-map-contract-27-disabled-build)
is configuration-dependent and disabled by default; it is not a Nepal substitute.

## Interpret quality and missing values

Keep these messages separate:

| Field or condition | Meaning |
|---|---|
| `input_quality_flags[].category = "observation"` | River-observation input assessment; read `detail` for missing or insufficient observations. |
| `input_quality_flags[].category = "forcing"` | Past weather input assessment. Recent estimated gaps have detail beginning `Missing weather data estimated in`; forcing degradation can have other causes. |
| `input_quality_flags[].category = "nwp"` | Future weather input assessment, separate from past forcing. |
| Provisional `warnings` | Rating-derived provenance/quality warnings; retain alongside input-quality messages. |
| `qc_status`, `qc_flags` | Checks on stored observations or forecast outputs, not input freshness. `raw` and `qc_unchecked` are not a pass. |
| `null` or unavailable component | Missing, withheld or unknown according to that field; never substitute zero. |

For example, `accepted_measured_discharge_absent` can accompany forecasts that
used converted water levels; it does not mean all river observations are absent.
Older weather gaps filled under the configured policy need not carry a degraded
input flag. Keep the backend's detail text visible rather than treating every
`forcing` flag as gap filling. An input-quality `null` means unknown; an assessed
empty flag list means something different. Overview `input_quality_codes` contain
compact `category:level` pairs; retrieve detail for full messages.

Read units from the payload when supplied. Discharge is expressed in cubic metres
per second (`m³/s` or `m3/s`); water level is in metres, with the gauge/absolute
reference determined by station datum metadata. Do not compare levels from
different references without that information.

## Resolve common errors

| Status | What to check |
|---|---|
| `400` / `422` | Parameter names, accepted values, UUIDs and dates for this particular route. |
| `401` | Missing, expired, revoked or invalid key. |
| `403` | Your key's role, or a deployment publication/feature restriction. |
| `404` | Wrong ID/category, outside your scope, unavailable publication, or a disabled feature that hides its existence. |
| `410` | Ordinary forecast publication was withdrawn; preserve the returned metadata. |
| `500` | Server failure; report the route and time without your key. Some older routes also return this for malformed UUIDs. |
| `503` | Required evidence/configuration is unavailable or a bound was exceeded. Do not turn this into an empty successful result. |

Most HTTP errors use `{"error":"…","detail":null}`. Framework validation uses
`{"detail":[...]}`; overview validation instead uses the ordinary error envelope.
Clients must handle both. Use UUIDs returned by the API instead of guessing them.

## Deployment compatibility

As checked on **16 November 2026**, the Nepal test deployment runs **v0.1.1136**
with contract **2.11** behavior, including rating-input history and network overview.
This documentation's contract **2.12** adds descriptive metadata only; it does not
announce a new deployed release. Features requiring deployment configuration remain
conditional. When sharing instructions, link to a documentation commit or tag that
matches the recipient's server, not automatically to the latest branch.
