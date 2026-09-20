---
status: DRAFT
created: 2026-09-16
plan: 301
title: DHM precipitation observations from the BIPAD rainfall reference
depends_on: [300]
related: [272, 303]
---

# Plan 301 — DHM precipitation observation adapter

## 🔴 Gating prerequisite — added 2026-09-20 by Plan 272 D4

**Plan 303 must land before this plan's precipitation feed goes live.**

`config.toml` declares QC rules for `precipitation` at **86400 s only**. The scheduled
ingest path infers a series' cadence from a short window and matches it against a rule's
declared step, so a sub-daily precipitation feed resolves **zero rules**. Under Plan 272
D5 such rows are stored `QC_UNCHECKED`, and Plan 272's consumer policy then excludes them
from **alerting, skill scoring, model training, hindcast and the partner export**.

⇒ **If this plan ships first, precipitation is ingested and then used by essentially
nothing.** Plan 303 adds the rule rows at the cadence this feed actually delivers.

This note exists because Plan 272 granted the number 303 in its own prose and nowhere
else — by 272's own standard, *"a vague promise is not a deferral"*, a number recorded
only in the deferring document is a promise wearing a number. Recorded here, in the plan
that is actually gated.

## Problem and outcome

Plan 300 ingests DHM river water levels and explicitly skips WEATHER stations.
Extend that deployment's observation adapter to ingest measured precipitation
at DHM weather stations, alongside river levels, using BIPAD's rainfall feed as
the development reference. This is observed rainfall, not a weather forecast.

The intended output is genuine, non-overlapping precipitation amounts in mm,
stored as `precipitation` observations and passed through existing Flow 2 QC.
No rainfall value may enter that parameter until its accumulation interval and
timestamp convention are established. Canonical precipitation is summed during
aggregation; treating every rolling total as a new increment would overcount rain.

Work starts from Plan 300's branch because PR #278 is still open at drafting.
Reconcile its final merged revision before implementation. No production code is
changed by this draft. Full review transcripts remain local.

## Evidence and unresolved contract

[Working examples, exact requests and provenance](../requirements/dhm-precipitation-api-examples/README.md)
include station summaries, wet and dry records, a 24-record bounded day and an
empty continuation page. The frontend and API were inspected on 2026-09-16.

- `/rain-stations/?latest=true` provides station summaries. `/rain/` exposes
  historical records with `station`, `stationSeriesId`, `measuredOn`,
  `createdOn`, `modifiedOn`, `averages`, `isHourly` and `isDaily`.
- Numeric `station=358`, `is_hourly=true`, measured-time bounds and ordering
  worked for Arghakhanchi. Its series ID is 21004 and source ID is 304; none
  is automatically our official DHM station code.
- `averages` contains entries for intervals 1, 3, 6, 12 and 24. The frontend
  describes accumulated depths in mm. This is evidence of intended display,
  not proof of non-overlapping measurement windows.
- The hourly sample is approximately hourly in NPT, with seconds/microseconds
  after each hour. UTC conversion preserves the instant and yields a :15 phase
  plus those seconds; it does not align the series to a UTC hourly grid.
- All one-hour values in the day sample are zero, while later three-hour
  values are 0.2. The reason is unknown. Do not repair or reinterpret the values.
- A short page still advertises `next`, as does the empty page. Count is a
  placeholder. Retain Plan 300's bounded pagination and empty-page termination.
- The schema is imprecise about the envelope and `averages` shape. Real JSON
  examples govern boundary models; unknown additive fields may be ignored.

**Owner-confirmed open question:** on 2026-09-16 the owner said DHM/BIPAD has not
confirmed what `measuredOn` means or whether the interval-1 value is the preceding
hour's rainfall total. The questions in the evidence README are T1's inputs.
Plan 300's water-level request/response confirmation remains accepted; it does
not settle these new precipitation semantics.

T1 must establish the exact conversion rule before this plan becomes READY for
T2–T4. If the available feed cannot yield trustworthy non-overlapping amounts,
revise the scope with the owner to an explicitly separate raw-capture facility
or a different DHM product. Do not implement an unapproved schema extension or
silently write overlapping products into `precipitation`.

## Proposed decisions

### D1 — Extend the existing DHM deployment path

Extend `DhmAdapter` and `DhmConfig`, preserving both station-source Protocols and
their existing result types. The current `[adapters.river_stations] type = "dhm"`
selection supplies Flow 2's station adapter; retain it for compatibility and
document that the DHM instance now serves river and weather observations.
Do not introduce a second flow, scheduler, adapter registry or all-provider
router. The default BAFU setup and injected-adapter ownership stay unchanged.

Add a separate optional `rain_bindings` collection, default empty so existing
water-level configs behave identically. Each binding explicitly names the DHM
station code and positive numeric rain-station ID, with the confirmed series ID
when required by T1. Keep the existing level-binding schema intact; rain gauges
do not require a level datum. Reject duplicate/ambiguous bindings. A precipitation-
only deployment may specify `bindings = []` with valid rain bindings.

An empty `rain_bindings` collection leaves rainfall polling disabled and preserves
the existing WEATHER skip behavior. Once rainfall is configured, an eligible DHM
weather station without its required binding is a station configuration failure.

Route DHM RIVER stations to `/river/` and DHM WEATHER stations to `/rain/`.
Weather stations must declare `precipitation` in `measured_parameters`; missing
bindings/metadata fail that station before I/O. Unsupported networks/kinds are
skipped without outcomes. No name-based auto-onboarding or station promotion.
If T1 establishes separate rainfall hosts/authentication, refine this config
decision before READY rather than guessing that the water-level endpoint works.

### D2 — Preserve accumulation meaning at the boundary

Pydantic parses external JSON into immutable typed rain samples, preserving
reported timestamps, station identity and duration/value pairs until the approved
conversion into `RawObservation`. There is no duration field in `RawObservation`
or the observation table: only the single, confirmed non-overlapping product
may be projected into the existing canonical parameter.

The candidate is interval 1 from the hourly history stream. Its exact start/end
label, grid phase, publication-delay handling and conversion are **unresolved**.
T1 must specify them with at least one worked wet example and expected UTC
timestamp. Never substitute `createdOn`/`modifiedOn` or round by convenience.
If the confirmed interval cannot be represented without broader time-grid work,
make the relevant Plan 252 work a hard dependency before canonical writes.

Keep genuine zero. An explicitly null/missing chosen interval produces no
measurement, never zero and never a fallback to a longer duration. Reject
duplicate interval keys, invalid scalar types (including booleans), non-finite
values, missing/naive required timestamps, wrong station identity and conflicting
duplicates as malformed station responses. Negative/sentinel treatment must be
fixed by T1: source-confirmed sentinels mean missing; otherwise retain finite
reported values for existing QC to flag, rather than clipping or inventing rain.
Warning/danger flags are not observation QC. No numeric undercatch correction.

Do not difference rolling windows, mix 1/3/6/12/24-hour values, borrow another
station's value, fill gaps from a longer window, or synthesise sub-hourly increments.
Do not impose cross-duration arithmetic as a validity test until T1 establishes
that the windows share an end time and are complete.

### D3 — Bounded history and failure isolation

Use numeric station filtering, the confirmed hourly selector, measured-time
bounds, ascending ordering and a positive page limit. Never copy the frontend's
`limit=-1`. Follow same-origin/path continuations which preserve all selection
filters; reject changed station, time range, hourly selector or unexpected query
keys. Stop on empty results or absent continuation, not count or a short page.

Reuse Plan 300's HTTPS, injected client/clock, timeouts, rate spacing, page budget,
sanitised errors and no-redirect policy. Pacing spans both rain and river calls
on one adapter. A station's page budget spans all its request windows. Factor
only concrete shared transport/pagination helpers inside the adapter; preserve
the different river/rain filters and parsers rather than adding a generic framework.

An incomplete/failed station returns no partial observations, while other stations
continue. Preserve the existing error taxonomy and clean-empty semantics. Duplicate
same-station/same-canonical-time values collapse only when identical; conflicts
fail the station. Rain and river failures remain individually visible in fetch
health, with no outcomes for skipped stations.

T1 must fix how the source query bounds correspond to canonical interval-end
watermarks, including publication delay. Do not inherit the river's one-second
padding as proof that a rainfall interval has been recovered. Final output remains
`since < canonical_timestamp <= captured_run_end`; a confirmed overlapping query
margin is trimmed locally. Corrections to already-stored measurements and
multi-year backfills remain separate work; document this limitation explicitly.

### D4 — Reuse precipitation cursors and include recovered data in QC

Flow 2 already uses `precipitation` as the WEATHER cursor. Preserve this explicit
mapping and Plan 300's DHM water-level cursor; never use discharge or temperature
for a rain gauge. Repeated polls store no duplicates, and a failed station does
not advance its watermark. Use the existing WEATHER eligibility rule: operational
station status, without river gauging/fallback-model requirements.

Extend the existing DHM recovered-time selection in `ingest_observations.py` to
the exact pairs `(DHM RIVER, water_level)` and `(DHM WEATHER, precipitation)`.
Recoveries older than the usual QC lookback must be checked with preceding context
and the exclusive read-end adjustment. Preserve other networks' QC windows and
update only RAW rows. A QC failure must leave genuine stored values intact.

Production precipitation rules currently cover only daily data. The research
hourly rules in `scripts/dhm_precip/qc_ruleset.py` are not deployed policy. Test
with injected hourly rules; do not silently copy research thresholds, Swiss
rules, or warning thresholds into Nepal production. Plans 272 (cadence reachability)
and 264 (network-aware selection/zero-rule policy), plus an approved hourly rain
ruleset, remain activation prerequisites. Sparse or jittered data must not be
declared scientifically validated because no rule happened to match.

## Tasks

### T1 — Resolve the rainfall interval and timestamp contract

**Outcome:** owner-recorded DHM/BIPAD answers establish a testable mapping from a
rain history record to one non-overlapping precipitation interval, or the owner
revises the plan's scope before implementation. This is planning work under DRAFT.

**In:** this plan; `docs/requirements/dhm-precipitation-api-examples/`.
**Out:** production code, invented timing tolerances, automatic READY status.

**Verification:** inspect the preserved request/response examples and hashes;
record a worked wet and dry example with exact source fields, physical interval,
units, UTC timestamp and query/cursor bounds. Explain the captured one-hour versus
three-hour discrepancy, missing/stale signals, and direct-DHM rainfall mapping.
If phase-aware storage/consumption is needed, name the exact dependency on Plan 252.

**Pre-change:** N/A — evidence and contract decision, no behavior change.

### T2 — Typed rainfall configuration and parsing

**Outcome:** explicit rain bindings and captured rainfall JSON can be parsed and,
under T1's confirmed rule, converted without loss of amount or interval meaning.

**In:** `src/sapphire_flow/config/dhm.py`,
`src/sapphire_flow/adapters/dhm_rain.py` (new boundary parser),
`tests/unit/config/test_dhm.py`, `tests/unit/adapters/test_dhm_rain.py` (new).
**Out:** transport, database migrations, changes to river binding requirements.

**Verification:** `uv run pytest tests/unit/config/test_dhm.py tests/unit/adapters/test_dhm_rain.py`.
Cover old config unchanged; rain-only/mixed bindings; ID namespace mistakes;
zeros, missing/null, sentinels per T1, invalid types/non-finite values, duplicate
durations, extra fields, invalid timestamps and the worked wet-example conversion.
Assert that overlapping windows cannot all become canonical increments.

**Pre-change:** new behavior tests fail against the water-level-only implementation;
capture the discriminating failure before implementation.

### T3 — Fetch rainfall with the existing DHM adapter

**Outcome:** both Protocol methods expose identical precipitation observations;
one DHM instance fetches river and rain stations with bounded, isolated outcomes.

**In:** `src/sapphire_flow/adapters/dhm.py`,
`tests/unit/adapters/test_dhm.py`; boundary-parser adjustments within T2's scope.
**Out:** new scheduler, retry system, live provisioning, temperature/humidity/wind.

**Verification:** `uv run pytest tests/unit/adapters/test_dhm.py tests/unit/adapters/test_dhm_rain.py`.
Use captured bodies and fake HTTP/clock: numeric selection, confirmed time margins,
multi-page/window coverage, empty continuation despite non-null next, short page,
loops/budget exhaustion, foreign URLs/changed selectors, 429/non-2xx/transport and
malformed data, later-page failure discarding earlier data, shared request spacing,
mixed river/rain success and independent failure, zero records vs skipped station,
unchanged WEATHER skipping with a legacy water-level-only configuration,
duplicate/conflicting intervals, and parity of both public methods.

**Pre-change:** demonstrate that the old adapter skips a configured DHM WEATHER
station and emits no precipitation for the captured/confirmed example.

### T4 — Integrate rain recovery with Flow 2 and document activation

**Outcome:** stored precipitation advances the proper cursor and all recovered
measurements receive QC without changing water-level or Swiss ingestion behavior.

**In:** `src/sapphire_flow/flows/ingest_observations.py`,
`tests/unit/flows/test_ingest_observations_dhm.py`,
`tests/unit/flows/test_ingest_observations_weather.py`,
`docs/conventions.md`, `docs/spec/types-and-protocols.md`,
`docs/spec/config-reference.toml`, `docs/touchpoint-maps.md`,
`docs/design/v0-flow2-observation-pipeline.md`,
`docs/requirements/dhm-precipitation-api-examples/README.md`, this plan/index.
**Out:** changing `services/qc.py` policy, operational Nepal thresholds, station
onboarding/forecast bindings, model changes, deployment or automatic publication.

**Verification:** `uv run pytest tests/unit/config/ tests/unit/adapters/test_dhm.py tests/unit/adapters/test_dhm_rain.py tests/unit/flows/test_ingest_observations.py tests/unit/flows/test_ingest_observations_dhm.py tests/unit/flows/test_ingest_observations_weather.py tests/unit/flows/test_ingest_observations_fetch_health.py`.
Cover a multi-hour rain outage with preceding QC context and RAW rows inside and
outside the recovered interval; repeat polling; mixed feeds and failure health;
weather eligibility; skipped-station outcome count; production configuration,
client cleanup, injected-client ownership and no-client allocation for an empty run.
Keep Plan 300's water-level recovery and boundary tests passing.

**Pre-change:** show old Flow 2 leaves recovered precipitation outside its ordinary
QC window unprocessed; add the discriminating test before widening the selection.

## Boundaries, dependencies and exit gates

- Plan 300 is the implementation base. Coordinate the shared QC window with
  Plans 272 and 264 when either lands. Phase/alignment work is owned by Plan 252,
  not an implicit timestamp-rounding rule in this adapter.
- This slice ingests station observations only. It does not replace NWP/reanalysis,
  create basin-average forcing, blend gauges, bias-correct precipitation, convert
  rainfall to runoff, change ForecastInterface, or promote a model.
- No live activation until T1 semantics, official station bindings, access settings,
  source latency/rate limits and applicable hourly QC are approved. The existing
  clean-empty fetch status does not establish freshness: Plan 300's activation
  follow-on must include rainfall freshness and supervised gap/RAW recovery.
- Before READY: complete T1, incorporate its decisions into D2/D3 and tests, and
  obtain owner-commissioned Claude, Codex and additional precipitation-contract
  review. Keep reports local. Only the owner marks READY.
- Before implementation exit: focused tests above, Ruff check/format, no new type
  errors under the repository ratchet, all affected docs and required version bump.
  The owner separately commissions patch reviews; the full suite must pass after
  the final code change before merge. Direct-DHM connectivity remains a distinct
  Nepal activation check, never claimed by offline fixtures.

```json
{
  "phases": [
    {"id": "contract", "tasks": ["T1"], "parallel": false},
    {"id": "parse", "tasks": ["T2"], "depends_on": ["contract"]},
    {"id": "fetch", "tasks": ["T3"], "depends_on": ["parse"]},
    {"id": "integrate", "tasks": ["T4"], "depends_on": ["fetch"]}
  ]
}
```
