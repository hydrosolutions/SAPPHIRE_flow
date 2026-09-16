# DHM precipitation — BIPAD reference examples

Captured 2026-09-16 from the public [BIPAD portal](https://bipadportal.gov.np/).
These are development reference data, not validation of direct DHM connectivity.
On 2026-09-16 the owner confirmed that the meanings of `measuredOn` and the
one-hour rainfall field have **not yet been confirmed** by DHM/BIPAD.

## Requests and responses

All requests are GETs under `https://bipadportal.gov.np/api/v1/`. Exact encoded
URLs, retrieval times, original-body hashes and stored-file hashes are in
[manifest.json](manifest.json). Readable bodies differ only by a terminal LF.

| File | Request | Observed response |
|---|---|---|
| [latest.json](latest.json) | `rain-stations/?latest=true&limit=2` | Two station summaries, each with 1/3/6/12/24-hour fields |
| [sample.json](sample.json) | `rain/?limit=2` | Two historical records, including nonzero rainfall at Gumthang |
| [history.json](history.json) | `rain/`, title Arghakhanchi, hourly, bounded day, limit 5 | Five hourly-marked records, continuation present |
| [day.json](day.json) | Same day, numeric station 358, hourly, limit 100 | 24 records; a short page still has a continuation |
| [unfiltered.json](unfiltered.json) | Numeric station 358, same day, no hourly flag, limit 5 | Same first five records; this sample does not establish availability of sub-hourly history |
| [empty.json](empty.json) | Day query with offset 100 | Empty results, but still a non-null next URL |

The bounded day uses `measured_on__gt=2026-09-15T00:00:00+05:45`,
`measured_on__lt=2026-09-16T00:00:00+05:45`, `ordering=measured_on`.
Both title and numeric-station filters were exercised. `count` is the placeholder
9223372036854775807: neither count nor short-page length is a completion signal.

The [schema excerpt](schema-excerpt.json) describes the two list endpoints and
Rain/RainStation definitions. It declares `averages` as an object and list
responses as arrays, whereas actual bodies use an `averages` array inside a
paginated envelope. Treat captured bodies as the shape evidence; do not generate
the parser blindly from that schema.

## Fields and semantics

For Arghakhanchi, the station-summary ID and historical `station` are **358**,
`stationSeriesId` is **21004**, and the summary's `dataSourceId` is **304**.
These are distinct identifiers, not an established mapping to our DHM station
code. Historical row `id` identifies a record, not the gauge.

Each record carries `measuredOn`, `createdOn`, `modifiedOn`, `averages`,
`isHourly`, `isDaily`, and station identifiers. The `averages` entries contain
`interval`, `value`, and warning/danger flags. The current frontend labels their
values as millimetres accumulated over the last 1, 3, 6, 12 and 24 hours. Its
detail request uses `title`, `is_hourly`/`is_daily`, measured-time bounds, a
field projection, and `limit=-1`. A production adapter must use bounded pages.

The frontend plots using `createdOn` and sometimes substitutes zero for absent
values. Those UI choices do not establish measurement semantics and must not be
copied into ingestion.

The hourly day has reported timestamps such as
`2026-09-15T01:00:04.874909+05:45`; exact UTC conversion gives
`2026-09-14T19:15:04.874909+00:00`, not a UTC whole-hour boundary. All 24
one-hour fields are zero, but the 22:00 and 23:00 records contain 0.2 mm in
their three-hour and longer fields. This does not establish the cause; it is
a concrete counterexample to assuming a consistent, gap-free hourly series.

## Questions for DHM/BIPAD

1. Is `measuredOn` the measurement-window end, the publication/poll time, or
   something else? Where is the exact accumulation start/end, including the
   seconds offset seen in these captures? Does the relevant grid use NPT or UTC?
2. Is `averages[interval=1].value` the preceding-hour depth in mm, a rolling
   accumulation, an intensity, or another quantity? What does `is_hourly=true`
   select, and do consecutive selected records cover non-overlapping intervals?
3. Why can every hourly value be zero while the longer windows contain rain?
   How are unavailable/stale measurements, sentinels and genuine dry zeroes
   represented? Are records revised later, and is a completeness marker available?
4. Confirm the rainfall request/response mapping for direct DHM access, especially
   numeric IDs and the hourly/history filters. This is a new rainfall question,
   not a request to reconfirm Plan 300's accepted water-level examples.

Record answers and the resulting timestamp/value rule in
[Plan 301](../../plans/301-dhm-precipitation-adapter.md) before implementation.
Do not sum overlapping windows, derive increments by differencing rolling totals,
round timestamps, impute absent rain as zero, or treat alert flags as QC grades.
