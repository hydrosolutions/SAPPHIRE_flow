# Forecast Lab snapshot (`forecast-lab-snapshot/v2`)

**Plan 198, extended by Plan 204.** One self-contained, versioned JSON
document for the separate **SAPPHIRE-flow-map** project: BAFU observations,
archived BAFU forecasts and SAPPHIRE forecasts (per-model and, since Plan 204,
the combined `_pooled`/`_bma` forecast) at the operational BAFU stations,
produced by exactly one code path
(`services/forecast_lab/snapshot.py::build_snapshot()`) whether it is served
over the REST route or written by the CLI export. See the plan docs
(`docs/plans/archive/198-forecast-lab-snapshot-export.md`,
`docs/plans/204-forecast-lab-quantile-models-and-pooled.md`) for the full
design rationale — this page is the consumer/operator-facing reference.

**`v2` is a breaking cutover, not a compatible addition** — every boundary
model forbids unknown fields (`ForecastLabModel`'s `extra: "forbid"`), so a
`v1` validator rejects a document carrying `combined_forecast` or the new
per-entry `representation`. No `v1` document is served any more.

## Document shape

The authoritative shape is the committed JSON Schema
(`docs/spec/forecast-lab-snapshot-v2.schema.json`), generated from
`api/forecast_lab_schemas.py::ForecastLabSnapshot` and checked, in CI, to
never drift from the model (`tests/unit/api/test_forecast_lab_schema.py`,
D15). A sanitized two-station example lives at
`tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json`.

Top level: `schema_version`, `snapshot_id`, `generated_at`, `data_cutoff_at`,
`status` (per-source `ok|error|missing` plus an `overall` roll-up),
`comparison_semantics`, `stations[]`. Per station: `station`, `availability`,
`observations`, `bafu_forecast`, `sapphire_forecasts[]`, `combined_forecast`,
`aligned_daily_comparison[]`, `verification`.

## Timestamps

Every timestamp is RFC 3339 UTC with a literal `Z` suffix (never a `+00:00`
offset) — enforced by a repo-wide test over every `*_at`/`*_start`/`*_end`
leaf. `generated_at` is the injected clock's value at build time (D20);
`data_cutoff_at` is the observation-query anchor (equal to `generated_at` in
this implementation — there is no grid-truncation step).

## Quantile / ensemble summary method

Every available SAPPHIRE entry (per-model or the combined block) carries a
`representation` discriminator — `"members"` or `"quantiles"` — and the two
representations are summarised by DIFFERENT rules. This split exists because
a `QUANTILES` forecast's `ensemble_size`-shaped count is a LEVEL count, not a
member count (the export never confuses the two: a `"members"` entry has a
required `ensemble_size` and no `quantile_level_count`; a `"quantiles"` entry
has a required `quantile_level_count` and no `ensemble_size` — the wrong
pairing is not just untested, it is unrepresentable).

**`representation: "members"` entries.** `minimum` (min member), `p25`,
`median`, `p75` (linear-interpolated order statistics — matching
`numpy.quantile(..., method="linear")` / `polars.quantile_cont`, surfaced
explicitly as `comparison_semantics.sapphire_quantile_method: "linear"`) and
`maximum` (max member). **`sapphire_quantile_method` describes ONLY this
representation** — a `"quantiles"` entry's envelope is never run through this
order-statistic path.

**`representation: "quantiles"` entries.** The stored levels are looked up
directly, never summarised: `p25`/`median`/`p75` are the EXACT stored
`0.25`/`0.50`/`0.75` values. `minimum` and `maximum` are always `null` — the
recognised 7-level set (below) does not contain the ensemble extremes, and a
consumer must not read a `null` extreme as a data error or as zero.

A `QUANTILES` forecast whose stored levels are **not** exactly
`{0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95}` at **every** `valid_time` (the
RECOGNISED-SET RULE — an exact match: wrong count, missing level, extra
level, or a duplicate row at any timestep all disqualify the whole forecast)
is **not** relabelled — it is exported as an explicit
`sapphire_forecasts[].reason == "unsupported_representation"` unavailable
entry, because outer stored quantile levels are not the same statistic as an
ensemble-member summary and presenting them as such would be silently
misleading (D5). **This includes a legitimately onboarded model with more
than seven levels** (e.g. nine): `min_operational_quantile_levels`
(`config/deployment.py`) is a count FLOOR used elsewhere in the codebase
(`>=`/`<` comparisons), not a value-set constraint, so clearing it does not
make a forecast's level set recognised.

**Known, deliberate omission — not free to reverse.** The envelope has no
slot for the `0.05`/`0.10`/`0.90`/`0.95` levels a recognised quantile
forecast also carries; the map shows a narrower band for these entries than
the model actually emits. Accepted "for now, may change later" (owner
decision) rather than extending the envelope. Adding `p05`/`p95` later is
**not** an additive change: `QuantileEnvelopeSchema` forbids unknown fields,
so it would require a `v3`, not a same-version edit.

`is_primary` (on `sapphire_forecasts[].model`) considers BOTH
representations: it is the first entry — in the assignment's
`(priority, model_id)` order — that this builder actually RENDERS (members,
or a recognised quantile set), never merely the first assignment with any
forecast at all. A higher-priority candidate the builder renders unavailable
can never win `is_primary`.

## Combined forecast (`combined_forecast`) — Plan 204

A sibling block on each station entry, **always present, discriminated on
`available`** (mirrors the `bafu_forecast` union) — **not** a
`sapphire_forecasts[]` entry, because that array is contractually "one entry
per assigned model" and the deployment's combined (`_pooled`/`_bma`)
forecast has no assignment row at all.

**Strategy-gated, never fetched unconditionally.** The block reflects the
deployment's *currently configured* `forecast_combination_strategy`
(`config/deployment.py`), matched exhaustively:

| strategy | `available` | `reason` (when `false`) | fetched row |
|---|---|---|---|
| `primary` | `false` | `"strategy_primary"` | none — no lookup at all |
| `pooled` | `true`/`false` | `"no_combined_forecast"` | the `_pooled` sentinel |
| `bma` | `true`/`false` | `"no_combined_forecast"` | the `_bma` sentinel (never `_pooled`) |
| `consensus` | `false` | `"no_combined_forecast"` | none — unsupported, no lookup |

`"no_combined_forecast"` means **"no exportable combined *discharge* forecast
is available under the current strategy"** — not "no stored sentinel row
exists at all". Under `pooled`/`bma` it means the lookup ran (filtered to
`parameter="discharge"`) and returned nothing; a combined row for a *different*
parameter may still exist. Under `consensus` it is a static verdict — no
query is issued (the forecast cycle raises `NotImplementedError` for
`consensus` today, so no row could exist regardless).

The block follows the same "latest available run from each source" rule
every other block in this document uses: once *any* combined discharge row
exists, a later cycle that produces none still shows the previous run rather
than going empty. Current-cycle matching is not implemented.

**Shape when available:** `representation` (`"members"`/`"quantiles"`,
same nested-discriminated split as `sapphire_forecasts[]`), `forecast_id`,
`model_key` (the sentinel actually fetched — `"_pooled"` or `"_bma"`),
`combination_strategy` and `source_model_ids` **reproduced exactly from the
persisted DB row** — the export makes no independent claim about which model
contributed to which parameter — plus the usual `variable`/`unit`/
`issued_at`/`observation_staleness_hours`/`native_step_seconds`/
`horizon_start`/`horizon_end`/`points`. No `qc_status` field: the `_pooled`
row is `raw` while individual forecasts are `qc_passed`, and whether that is
correct by design or a Plan 026 gap is unresolved — an absent field is
honest, a `"raw"` a consumer cannot interpret is not.

**Rolls up like any other rendered SAPPHIRE source.** A rendered combined
forecast counts toward `stations[].availability.sapphire_forecast`, the
document-level `status.sapphire_forecasts` source status and
`latest_available_at`, and joins `aligned_daily_comparison[].sapphire` under
its fetched sentinel key — including on a station whose assigned models
produced nothing renderable, where the combined block is the *only* SAPPHIRE
forecast present.

## BAFU trace normalization — read the sign correction

The BAFU `hydrodaten_plot` archive's `"25.-75. Percentile"` trace is a closed
Plotly polygon: `half = (n - 1) // 2` points tracing one edge forward
(ascending `valid_time`), the other edge backward (descending, reversed to
ascending), then one closing vertex duplicating the first point.

**⚠️ Measured 2026-08-21 on three independent live runs across three BAFU
stations, and corroborated by the checked-in reference fixture for a fourth:
the forward half is the LOWER edge (`p25`) and the backward half is the
UPPER edge (`p75`).** An earlier domain comment in this repo
(`types/bafu_forecast.py`) had the two edges backwards; that comment is now
corrected, and it is the most plausible origin of an inverted mapping
anywhere downstream that copied it. If you are integrating against the raw
BAFU archive directly (not through this snapshot), do not trust "forward =
upper" — verify against a live run before trusting either direction.

The two envelope traces are distinguished **only by punctuation** —
`"Min. / Max."` (with periods) is the upper envelope (`maximum`); `"Min /
Max"` (no periods) is the lower envelope (`minimum`) — and must never be
deduplicated on a normalised trace name.

`issued_at` (BAFU's own "Forecast as of" annotation) and
`inventory_produced_at` (BAFU's `meta.produced_at` inventory-generation
stamp) are kept distinct and are never conflated with a fetch clock — the
archive holds no fetch timestamp at all, so there is no `fetched_at` field.

## Daily alignment (`aligned_daily_comparison`)

One row per UTC calendar day (`day_start`/`day_end` = `[00:00Z, next
00:00Z)`), spanning the union of days covered by the BAFU run's horizon,
every *available* per-model SAPPHIRE forecast's horizon, AND — since
Plan 204 — the combined forecast's horizon when it is `available`. `sapphire`
is a keyed object by `model_key` (per-model entries) or the fetched sentinel
(`"_pooled"`/`"_bma"`, the combined block), never a row per model (avoids
duplicating `observation`/`bafu` values across models). Under
`combination_strategy=primary` the combined block is always unavailable, so
no sentinel key ever appears; T2's quantile rendering can still change this
row's content on a `primary` deployment (a previously-unavailable quantile
fallback becoming renderable). Completeness gates:
`comparison_semantics.bafu_daily_completeness_minimum` (22 hourly points)
and `.observation_daily_completeness_minimum` (130 ten-minute samples) —
below the gate the row is still emitted with its computed value(s) and
`complete: false`, never withheld.

## Partial results, not all-or-nothing

Each of the three sources (observations, BAFU forecasts, SAPPHIRE forecasts)
is assembled independently. A non-poisoning failure — an archive file
missing or unparseable, or a BAFU trace polygon that does not reconstruct —
degrades only that source: `status.<source> = "error"` or `"missing"` with a
short message, and the corresponding per-station section is marked
unavailable rather than aborting the whole document. `status.overall` is
`"ok"` when every source is `ok`, `"unavailable"` when none is, else
`"partial"`.

**When a request spans multiple stations** (`stations[]` has more than one
entry), `status.<source>` is a roll-up over every station actually rendered
in the document: `"ok"` only when **every** requested station has that
source available, `"missing"` when **none** do (including a stationless
request that resolves to zero stations), and `"error"` when **some but not
all** do — `message` then reports the exact count, e.g. `"1 of 2 stations
missing a BAFU run in the requested window"`. This is a document-level
signal for whether a source is worth showing at all in this response, not a
per-station diagnostic: to find out *which* station is missing a source,
read that station's own `stations[].availability` block, which is
unaffected by the document-level roll-up. A single-station request is the
same rule's `N = 1` case (`"ok"` or `"missing"`; `"error"` cannot occur).

A genuinely poisoned database transaction (`SQLAlchemyError`) is **not**
caught anywhere in the assembly path — it propagates to an HTTP `500` (the
route) or a non-zero process exit (the CLI), because a failed statement
inside the shared request-scoped transaction poisons every later query in
that request; there is no partial-snapshot story for that failure mode.

## Verification — a sentinel, not a computed benchmark

`verification.status` is always `"insufficient_data"` in this version.
Plan 111 bars computing a BAFU-derived skill benchmark before its licence
gate (G1) clears, so `build_snapshot()` structurally never queries
`hindcast_forecasts`, `hindcast_values` or `skill_scores` — there is no code
path by which a computed number, or the known-bad future-dated hindcast
rows in those tables, could reach this document.

## Licence marker

`bafu_forecast.licence_status` is always `"unresolved"` — this export is
**research-only** and must not be used for commercial publication of BAFU
forecast data (Plan 111's G1 gate). The marker travels with every exported
copy of the data, because once a snapshot leaves the quarantined archive
(served over HTTP, written to a file, cached by a consumer) it is no longer
reachable by a single `rm -rf` of the archive volume — see **Data lifecycle**
below.

## Data lifecycle — the quarantine's discard property now has three steps

Before this plan, discarding the BAFU forecast archive was one step: `rm -rf`
the archive volume. **That is no longer true.** A REST response and a CLI
export are copies that live outside the archive, and the map is expected to
cache the last good snapshot it received. A full discard of BAFU forecast
data now requires three steps: (1) the archive volume, (2) any exported
snapshot files, (3) the consuming map's own cache. This is a deliberate,
accepted trade-off of serving the archive at all (Plan 111 § Non-goals,
Export extension) — not a defect to fix here.

## Offline use and caching — there is no server-side dedup signal

There is no `ETag`, `If-None-Match`, `304`, `Last-Modified`, or any
server-side content hash. `snapshot_id` (`fls2-<generated_at, compact>`) is
a timestamp label, not a content hash — it promises nothing about the body's
content and must not be used to detect "nothing changed." A consumer that
wants to avoid redundant work already holds the full response body, so
`sha256(body)` on the consumer's own side is one line and needs no server
cooperation. A repeat poll over unchanged data still transfers the full
response (typically ~300–600 KB uncompressed for two stations over a 168 h
observation window) — there is no gzip either (adding it would change every
route's response encoding, out of scope for this plan).

## Authentication and scoping

Same bearer-token auth as every other `/api/v1/...` route
(`api/security.py`). `station_code` is repeatable; an out-of-scope or
unknown code (including one outside the eligible `network=bafu`,
`kind=river`, `status=operational` set) returns `404` — indistinguishable
from a typo, and any bad code among several requested `404`s the **whole**
request rather than a partial result. A stationless consumer token that
supplies no `station_code` gets `200` with `stations: []` (matching
`GET /api/v1/stations`'s own empty-scope behaviour).

## Example request

```bash
curl -sS \
  -H "Authorization: Bearer ${SAPPHIRE_API_TOKEN}" \
  "https://<company-lan-host>/api/v1/forecast-lab/snapshot?station_code=2009&station_code=2091&observation_hours=168"
```

## CLI export

Module invocation, mirroring `cli/bafu_observation_audit.py` (there is no
console-script entry — `docs/conventions.md`):

```bash
python -m sapphire_flow.cli.export_forecast_lab \
  --output /path/to/forecast-lab-snapshot.json \
  [--station-code 2009 --station-code 2091] \
  [--observation-hours 168]
```

**On a deployed image, this CANNOT be run with a bare `docker exec`.** The
image entrypoint composes `DATABASE_URL` from `DATABASE_URL_TEMPLATE` +
`DB_PASSWORD_SECRET` (Plan 161); `docker exec` bypasses it and the export
dies on `KeyError: 'DATABASE_URL'`. The working invocation on a deployed
container is:

```bash
docker exec <api-container> /entrypoint.sh python -m \
  sapphire_flow.cli.export_forecast_lab --output /path/to/snapshot.json
```

Writes atomically (temp file, schema-validated, then `os.replace`) — a
mid-write failure never leaves a partial file at the destination path. Exit
code is non-zero only on a total failure (unknown `--station-code`, a
database or config error); a successfully written document with
`status.overall == "partial"` or `"unavailable"` still exits `0` — that is a
data-availability fact recorded *inside* a validly-written file, not a CLI
failure.

## Non-goals (explicit)

No public internet exposure, no TLS termination here, no frontend, no
alerting, no site-specific forecasts, no river-network processing, no direct
database access for the consuming map, and no computed skill/verification
metrics until Plan 111's Gate G1 clears. See the plan doc's own
"Non-goals" for the complete list.
