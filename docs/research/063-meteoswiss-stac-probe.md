# MeteoSwiss STAC probe (Plan 063 T0)

**Date**: 2026-04-20
**Target**: `https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-forecasting-icon-ch2`
**Purpose**: de-risk the filter/allowlist design for `MeteoSwissNwpAdapter._fetch_grib_files()`.

All probes were executed with `uv run python3 << 'EOF' ... EOF` heredocs; requests used `httpx.Client(timeout=30)`. No authentication was sent.

---

## Summary (TL;DR for implementer)

- **Server-side STAC Query extension is silently IGNORED** on this collection. A `query={"forecast:reference_datetime": {"eq": "1999-01-01T00:00:00Z"}}` still returns the default page. Do **not** rely on server-side field filtering.
- **Server-side filters that DO work**: `datetime` (operates on `properties.datetime` = forecast *valid-time*), `bbox`, `ids` (untested but appears not to filter), and pagination cursor.
- **Only reliable filter strategy is CLIENT-SIDE regex on item IDs**. Item IDs encode cycle time, step, variable, and type: `{MMDDYYYY}-{HHMM}-{STEP}-{var}-{ctrl|perturbed}-{suffix}`.
- **Asset `size` field is NOT present** — the plan's pre-flight size cap cannot use it. Use a constant estimate per item.
- **`relhum_2m` is NOT published** by MeteoSwiss ICON-CH2-EPS. The closest 2m-humidity variable is `td_2m` (dewpoint); RH must be derived downstream from `t_2m` + `td_2m` (Magnus formula) when a model needs it.
- **v0 scope decision (2026-04-20)**: PARAM_GROUPS is narrowed to the two canonical hydrological drivers — **`tp` (precipitation) + `t_2m` (temperature)** — reflecting that all three current v0 models consume zero NWP variables. The other four (`td_2m`, `u_10m`, `v_10m`, `h_snow`/`sd`) are deferred until a downstream v0b/v1 model actually consumes them.
- **Publication lag**: cycle at 12:00 UTC → items `created` at ~13:59 UTC. Confirms the plan's ~2h assumption.
- **Pre-signed URL TTL**: 24 hours (not 1 hour as the plan cautioned). Pagination-time URL expiry is not a realistic concern.
- **No `conformsTo` classes** declared at collection level.

---

## (a) STAC filter-extension probes

### Filter extension (CQL)

Not declared in `conformsTo`. Not attempted — the collection returns empty `conformsTo` so CQL Filter is unsupported.

### Query extension

```python
q = {"forecast:reference_datetime": {"eq": "1999-01-01T00:00:00Z"}}
GET /items?query=<url-encoded-q>&limit=3
# → HTTP 200, features=3 — the impossible ref time is ignored; default page returned
```

Repeated with `forecast:variable`, `forecast:perturbed` filters — all ignored. The server accepts the `query` parameter syntactically but does not apply it.

**Verdict**: **Query extension is NOT viable** against this deployment.

### `ids` parameter

```python
GET /items?ids=04192026-1200-0-alb_rad-ctrl-cv639s0p
# → HTTP 200, features=100 (default page size)
```

`ids=` returned 100 features regardless of the requested single ID. **Not viable**.

### `datetime` + `bbox`

```python
GET /items?datetime=2026-04-19T12:00:00Z/2026-04-19T15:00:00Z&bbox=5.9,45.7,10.5,47.9&limit=3
# → HTTP 200, returns items whose properties.datetime is in range
```

Both filters work as documented. `datetime` operates on `properties.datetime` = **valid-time**, not issue-time. `bbox` accepts `minx,miny,maxx,maxy`.

---

## (b) Pagination shape

Tested with `datetime=2026-04-19T12:00:00Z/2026-04-24T12:00:00Z` (one 120h cycle window) and `limit=100`:

- 100 pages fetched before hitting the scan cap; **10,000+ items** belonged to cycle `04192026-1200-*`.
- Full cycle estimate: 57 distinct variables × 2 types (`ctrl`, `perturbed`) × 121 steps ≈ **13,794 items per cycle**.
- `bbox` addition did not shrink page count (only ~3 features/page in the tested range — because the bbox-restricted set was small OR because bbox only filters spatial extent AFTER the valid-time filter).
- Each page has a `next` link under the HAL-style `links[].rel == "next"` pattern. Cursor is opaque.

**Implication**: the v0 2-variable allowlist (client-side ID regex on `tot_prec` + `t_2m`) reduces the download from ~13,794 items to **484 items** (2 vars × 2 types × 121 steps). That is an **~96% reduction** vs the full cycle, and ~3× further than the originally-planned 6-variable allowlist.

---

## (c) Variable mapping table

Confirmed empirically by enumerating all 57 distinct variable tokens at step 0 for cycle `04192026-1200`:

| STAC item-ID token | `properties.forecast:variable` (UPPER) | cfgrib shortName | type_of_level |
| --- | --- | --- | --- |
| `tot_prec` | `TOT_PREC` | `tp` | `surface` |
| `t_2m` | `T_2M` | `t_2m` (or `t2m`) | `heightAboveGround` |
| `td_2m` ⚠ | `TD_2M` | `td_2m` | `heightAboveGround` |
| `u_10m` | `U_10M` | `u10` | `heightAboveGround` |
| `v_10m` | `V_10M` | `v10` | `heightAboveGround` |
| `h_snow` | `H_SNOW` | `sd` (snow depth) | `surface` |

**⚠ Critical correction to plan D2**: `relhum_2m` is **NOT** published by MeteoSwiss ICON-CH2-EPS. The 2m humidity signal is `td_2m` (dewpoint). Relative humidity must be derived downstream from `t_2m` + `td_2m` (e.g. Magnus formula). Plan 063 T2 must update the allowlist to use `td_2m` instead of `relhum_2m`, and any downstream v0 model that expected RH must either (a) consume `td_2m` directly, or (b) have RH derived in a preprocessing step.

Other humidity-adjacent variables available: `qv` (specific humidity, 3D multi-level), `qc` (cloud water), `rain_gsp`, `snow_gsp`, `w_snow`, `t_snow`, `t_g`, `t_so`. None are a direct substitute for RH at 2m.

The cfgrib shortNames above are inferred from ICON conventions + cfgrib's documented mappings. **Plan 063 T2 implementer must verify cfgrib shortNames empirically against a downloaded GRIB2 file** (`grib_dump` or `cfgrib.open_dataset(...).data_vars` on a real asset). The names in the table are the most likely but cfgrib sometimes normalises (e.g. `t2m` instead of `t_2m`).

---

## (d) Rate-limit / 429 behaviour

~30 rapid back-to-back requests across all probes completed without a 429. No `Retry-After` header or throttling signal observed at this volume. The plan should still implement 429 handling defensively (with exponential backoff) but it is **not** a hot path concern at current v0 volume.

No `X-RateLimit-*` headers in responses.

---

## (e) `asset.size` presence

Sample asset keys: `['title', 'type', 'href', 'roles', 'created', 'updated']`. **No `size` field.** Size cap enforcement cannot be pre-flight per-item; must use a constant estimate:

- Typical ICON-CH2-EPS asset: ~2 MB per (var, type, step). 6-var allowlist full cycle: 6 × 2 × 121 × 2 MB ≈ **2.9 GB**. Plan's 4 GB cap is appropriate.
- The file-count guard (`len > 500`) remains the primary tripwire — the 4 GB cap is belt-and-braces.

---

## (f) Cycle cadence

Confirmed 3-hourly: item IDs observed include `HHMM` tokens `0000, 0300, 0600, 0900, 1200, 1500, 1800, 2100` UTC. The plan's `_CYCLE_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)` is correct.

---

## (g) Publication lag

One data point:
- Cycle `forecast:reference_datetime`: `2026-04-19T12:00:00Z`
- First observed item `created`: `2026-04-19T13:59:46Z`
- **Lag**: 1h 59m 46s

Plan's "~2h" assumption is correct. Operational implication: at 02:30 UTC, the 00Z cycle is NOT yet published (created ~01:59Z, so MIGHT be ready by 02:30, but marginal); `resolve_cycle_time` should fall back to 21Z the previous day if 200+empty is returned.

No official published SLA sighted. A 2.5h stale-cycle tolerance is a reasonable default.

---

## (h) Interval `datetime=start/end` semantics

Confirmed: `datetime=C/C+5d` filters by `properties.datetime` (= forecast valid-time), **not** by `forecast:reference_datetime`. An interval window that covers cycle C's valid-time range (C to C+120h) will return all items for cycle C whose step is in [0, 120h]. It will **also** return items from other cycles whose valid-times fall in that window (e.g. cycle C-3h step=3 has valid-time C).

**Implementer note**: even with a datetime interval scoped to cycle C's window, the adapter MUST apply the client-side ID-prefix filter `^{MMDDYYYY}-{HHMM}-` to exclude items from neighbouring cycles.

---

## (i) Pre-signed asset URL TTL

Sample asset `href`:
```
https://rgw.cscs.ch/cscs.meteoswiss.ogd.nwp/icon-ch2-eps-202604191200-0-alb_rad-ctrl.grib2
  ?AWSAccessKeyId=13GC1T4NT2CY1N0L92YC
  &Signature=EqLnqU08JYg7lTGHJ1syE9KpqsA%3D
  &Expires=1776693586
```

`Expires=1776693586` = `2026-04-20T13:59:46Z` (Unix timestamp).
Item `created`: `2026-04-19T13:59:46Z`.
**URL TTL = 24h** (fixed absolute expiry, set at item creation).

The plan's concern about URLs expiring during pagination is moot: at 5-10 min/cycle pagination, a 24h TTL leaves massive margin. The plan can keep the collect-then-download order OR interleave; either is safe. **Recommendation**: interleave anyway for memory efficiency (don't hold 1000+ URLs in memory), but do NOT quote TTL as the rationale.

---

## Implications for Plan 063

### Must-fix (Sev-1)
- **D2 variable mapping**: replace `("relhum_2m", "rh_2m", …)` with `("td_2m", "td_2m", "heightAboveGround")`. Downstream v0 models that expected RH must either (a) switch to dewpoint or (b) derive RH in a preprocessing step.
- **T1 `resolve_cycle_time`**: the availability probe must be a paginated `datetime=C/C+3h` query followed by client-side ID prefix check. It cannot use STAC Query filters.
- **T2 filter design**: server-side filter is `datetime=C/C+5d` (or tighter interval) + `bbox` (CH envelope). Client-side regex on item IDs selects cycle + allowlist variables. Pagination continues until `next` link is absent.

### Should-adjust (Sev-2)
- **D5 size cap rationale**: drop "asset.size" pre-flight check — use constant estimate (~2 MB/asset) + file-count guard.
- **T2 open question on pagination-TTL**: URL TTL is 24h; pagination-time expiry is not a realistic concern. Keep the interleaved design for memory reasons, not TTL.

### Informational
- Cycle cadence and ~2h publication lag both confirmed.
- Rate limiting not observed; defensive backoff still advisable.

---

## Sample request + response artefacts (for future debugging)

Successful cycle probe:
```
GET /api/stac/v1/collections/ch.meteoschweiz.ogd-forecasting-icon-ch2/items
    ?datetime=2026-04-19T12:00:00Z
    &limit=100
```
Returns HAL-style JSON with `features`, `links[rel=next]`, `numberMatched`, `numberReturned`.

Sample item properties (complete):
```json
{
  "datetime": "2026-04-19T12:00:00Z",
  "title": "ALB_RAD at 19.04.2026 12:00 Step 0 (Control)",
  "created": "2026-04-19T13:59:46.728565Z",
  "updated": "2026-04-19T13:59:46.790840Z",
  "expires": "2026-04-20T13:59:41.145169Z",
  "forecast:reference_datetime": "2026-04-19T12:00:00Z",
  "forecast:horizon": "P0DT00H00M00S",
  "forecast:variable": "ALB_RAD",
  "forecast:perturbed": false
}
```

Sample asset object (complete):
```json
{
  "title": "...",
  "type": "application/grib",
  "href": "https://rgw.cscs.ch/...?AWSAccessKeyId=...&Signature=...&Expires=<unix-ts>",
  "roles": [...],
  "created": "...",
  "updated": "..."
}
```
