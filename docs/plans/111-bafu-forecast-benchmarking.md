# Plan 111 — Benchmarking SAPPHIRE forecasts against BAFU's operational forecasts

**Status:** DRAFT
**Priority:** Low — nice-to-have. Not on the v1.0 Nepal critical path (Plan 106).
**Type:** Research / publication artifact. If it ever becomes code: hold-at-PR.
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-08
**Next action:** **Request forecast data — and written licence clarity — from BAFU**
(`abfragezentrale@bafu.admin.ch`); see Gate G1. LINDAS carries no forecasts (Phase 0).
A scrape of `hydrodaten.admin.ch` *is* technically viable and fully characterised
(Phase 0b) but is **forward-only** and **legally unresolved**, so it cannot substitute
for the request — it complements it. Everything else here is blocked on that reply.
**Related:** `docs/publication/publication-plan.md` (evaluation framing);
Plan 058 (BAFU LINDAS archive collection); `docs/decisions/bafu-lindas-monday-window.md`;
Flow 8 (skill computation) in `docs/architecture-context.md`.

> **Motivation.** Today every skill number SAPPHIRE produces is scored against a
> *self-generated* reference: climatology quantiles (CRPSss) and persistence (BSS),
> both computed at onboarding (Flow 5 step 5.8). We have never asked the question a
> reviewer will ask first: **is SAPPHIRE better than the forecast the responsible
> national agency already publishes for the same river?** BAFU runs an operational
> discharge forecast on a subset of the very gauges we ingest. A head-to-head on
> those gauges, scored against the observations we already hold, is the single most
> credible external validation available to us — and it is currently absent from the
> repo and from the publication plan.

---

## Phase 0 — Feasibility probe: does LINDAS ship forecasts? **DONE (2026-07-08)**

The pivotal question, resolved before drafting the rest of this plan. **Answer: no.**
LINDAS carries BAFU *observations* only. The trivial path does not exist.

**Evidence** (SPARQL against `https://lindas.admin.ch/query`, 2026-07-08):

1. The `https://lindas.admin.ch/foen/hydro` named graph contains exactly **two**
   `cube:Cube` instances — `.../foen/hydro/river` and `.../foen/hydro/lake`.
2. Observation dimensions on those cubes are:
   `station`, `measurementTime`, `waterLevel`, `discharge`, `dangerLevel`
   (plus `cube:observedBy`, `rdf:type`). There is **no** lead-time, valid-time,
   issue-time, ensemble-member, or forecast dimension.
3. Filtering every predicate in the graph for
   `forecast|prognos|predict|lead` returns **zero** bindings.
4. Across the whole LINDAS triplestore, the only FOEN graphs holding cubes are
   `foen/hydro` (2), `foen/cube` (117 cube URIs / 66 distinct titles),
   `foen/national-forest-inventory`, `foen/forest-fire-risk-warning`,
   `foen/forest-fire-prevention-measures-cantons`. Filtering the `foen/cube`
   titles for `hydro|forecast|prognose|vorhersage|abfluss|discharge` returns
   **zero** rows — that graph is air quality, CO₂, noise, soil, bathing water,
   red lists, COFOG. **No hydrological forecast cube exists on LINDAS.**

Reproduce:

```bash
curl -s -H "Accept: application/sparql-results+json" \
  --data-urlencode 'query=SELECT DISTINCT ?c ?t WHERE { GRAPH <https://lindas.admin.ch/foen/hydro> { ?c a <https://cube.link/Cube> . OPTIONAL { ?c <http://purl.org/dc/terms/title> ?t } } }' \
  https://lindas.admin.ch/query
```

**Where BAFU forecasts actually live** (2026-07-08, needs confirmation in Gate G1):
`hydrodaten.admin.ch` exposes a *human* product — "Stations with forecasts"
(`/en/messstationen-vorhersage`), the flood-alert map, the natural-hazard bulletin,
the flood outlook. The station-with-forecast listing renders client-side; no JSON or
CSV endpoint was visible in the served HTML, and no station links are present in the
static markup. The machine-readable channel for natural-hazard forecasts is
understood to be **GIN** (Gemeinsame Informationsplattform Naturgefahren,
`gin.admin.ch`), which is **access-restricted to authorities** — SAPPHIRE has no
entitlement. Treat both statements as *hypotheses to verify*, not findings.

**Consequence.** This is the complex branch. The plan below is therefore
gated on *sourcing and licensing*, not on engineering — the engineering is small
and is described only so the gates can be evaluated against a real cost.

---

## Phase 0b — Can we scrape the forecasts from hydrodaten? **INVESTIGATED (2026-07-08)**

**Technical answer: yes, trivially. The blocker is licensing, not engineering.**

The forecast plots on `hydrodaten.admin.ch` are **Plotly figures served as JSON**, not
rendered images. The full chain, verified live:

| Step | Endpoint | Yields |
|---|---|---|
| 1 | `GET /web-hydro-maps/hydro_sensor_pq_forecast.geojson` | The forecast-station inventory + `meta.produced_at` |
| 2 | station `plot` property → `/web/hydro/{lang}/hydro_sensor_pq_forecast/{key}/plots` | HTML listing per-parameter plot assets |
| 3 | **`GET /plots/q_forecast/{key}_q_forecast_{lang}.json`** | **The raw Plotly figure — numeric series** |

Step 3 is the find: the path is referenced without extension in the page, but appending
`.json` returns `application/json`, HTTP 200, unauthenticated, ~28 KB.
(`.png`/`.svg`/`.csv` → 404; no extension → 302.) Discharge is `q_forecast`;
water level is `p_forecast` (present on lake/level stations).

**Station inventory (2026-07-08):** **54** forecast stations — 41 river
(`metric: discharge_ms`, unit `m³/s`) and 13 lake (`metric: masl`, unit `m ü.M.`).
The feature `key` (e.g. `2135` = *Aare – Bern, Schönau*) is the **BAFU station number**,
i.e. the same identifier LINDAS uses. **The join to our observations is free** — no
fuzzy name matching, no crosswalk table.

**Payload shape** (station 2135, discharge):

- Five traces: `Min. / Max.`, `Min / Max`, `25.-75. Percentile` (a `fill: tozerox`
  polygon), `Median`, `Measured`.
- **114 hourly steps**, horizon `2026-07-08T15:00+02:00 → 2026-07-13T08:00+02:00`
  (**≈4.7 days**). `Measured` carries 25 trailing observed values.
- Units in `trace.meta.unit` as the string `"m³/s"` — **byte-identical to our canonical
  discharge unit**.
- **Issue time is machine-readable**: `layout.annotations[1].x` =
  `"2026-07-08T15:00:00.000+02:00"`, with `text: "Forecast as of 08.07.26 15:00"`.
  Also `meta.produced_at` on the GeoJSON. (Reading issue time out of an annotation's
  *array position* is brittle — match on the `text` prefix, not the index.)

### Two findings that change the gates

1. **G2 is partly answered without asking BAFU: they publish quantiles, not members.**
   The product is min / p25 / median / p75 / max. Ensemble CRPS is therefore **not
   computable** on the BAFU side, confirming the comparability worry in G2. What
   survives a fair head-to-head: **pinball loss at q25/q50/q75**, MAE / NSE / KGE /
   PBIAS on the median, POD / FAR / CSI against danger thresholds, and peak-timing
   error. `Min`/`Max` are not quantile levels and cannot extend that set.
2. **Forward-only, confirmed.** Every endpoint returns *the current* forecast. There is
   no archive, no issue-time parameter, no history. A scraper therefore yields its first
   scoreable sample only after months of collection — **the exact Plan 058 trap**. This
   does not make route C wrong; it makes route C *slow*, and it means route C and route
   A are complements, not alternatives: **A is the only way to get history.**

### Licensing: unresolved, and it is the whole question

Two sources are in tension and must be reconciled **before any collector is written**:

- The general admin.ch legal notice (`https://www.admin.ch/en/terms-and-conditions`,
  linked from hydrodaten's own footer) states: *"Copyright and any other rights relating
  to texts, illustrations, photos or any other data available on the Federal
  authorities' websites are the exclusive property of the federal authorities … **Any
  reproduction requires the prior written consent of the copyright holder.**"*
- **But** `opendata.swiss` publishes the dataset **"Hydrologische Stationen mit
  Vorhersagen"** (`hydrologische-stationen-mit-vorhersagen`; publisher: *BAFU /
  Abteilung Hydrologie*) with every resource carrying
  `rights: https://opendata.swiss/terms-of-use#terms_open` — the **most permissive**
  OGD tier (open use, commercial use permitted, attribution not even required).

**The catch, stated plainly:** that OGD dataset's resources are the **station-location
layer** (`ch.bafu.hydroweb-messstationen_vorhersage` — a WMS service and a `data.zip` of
points), i.e. *which* stations have forecasts. It also lists `hydrodaten.admin.ch` itself
as a resource under the same `terms_open` rights, which is **suggestive but not
dispositive**. Nothing establishes that `terms_open` extends to the forecast *time
series* served from the portal's internal plot endpoints. Those endpoints are the site's
own React plumbing — **undocumented, unversioned, no stability contract, not a published
API resource** — and they can change without notice.

Supporting facts, neither of which settles it: `hydrodaten.admin.ch/robots.txt` is
**404** (no crawl prohibition is expressed), and the data is public and unauthenticated.

> **Assessment.** The probability that this is open Swiss OGD is high. "High
> probability" is not the standard that should back a **published** benchmark against
> the agency that also supplies our **only** Swiss observation feed. Get it in writing.
> Route A's request (G1) should now *also* ask, explicitly: **"does `terms_open` as
> declared for `hydrologische-stationen-mit-vorhersagen` cover the forecast time series
> rendered at `/plots/q_forecast/…`, and may we archive and republish derived skill
> scores from it?"** That single sentence converts route C from a legal gamble into a
> sanctioned feed.

### If (and only if) licensing clears — the collector's shape

Small: a Prefect flow, hourly, that walks the 54-station GeoJSON, fetches each
`q_forecast`/`p_forecast` JSON, parses the five traces + issue time, and appends to an
archive keyed `(station_key, issued_at, valid_time)`. Notes that matter:

- **Determine the real issue cadence empirically before choosing a poll interval.** On
  2026-07-08 the figure's issue time was 15:00 while `produced_at` was 18:30 — issue and
  publication are *not* the same clock. Dedupe on `issued_at`, not on fetch time; a poll
  that re-fetches an unchanged forecast must be a no-op, not a duplicate row.
- **Be a polite client**: modest rate limit across the 54 stations, a descriptive
  `User-Agent` identifying SAPPHIRE/hydrosolutions with a contact address, conditional
  requests where honoured, and a hard cap on retries. We are a guest.
- **Expect the endpoint to break.** Undocumented internal endpoints change. The
  collector must fail loudly into Flow 4 pipeline monitoring, never silently write empty
  archives. (Compare the `live-lindas-weekly` Monday-window failures —
  `docs/decisions/bafu-lindas-monday-window.md`.)
- **Archive raw.** Persist the untouched Plotly JSON alongside the parsed rows. If our
  trace-name parsing turns out wrong six months in, the raw payload is the only way to
  recover the archive; re-fetching is impossible.
- Storage is negligible: 54 stations × ~28 KB × 24/day ≈ **36 MB/day** raw, far less
  parsed. Wire it into the Plan 105 disk-hygiene budget anyway.

---

## Gate G1 (blocking) — can we lawfully obtain BAFU forecast time series?

> **Owner decision (2026-07-08): take route A — request the data from BAFU.**
> Since LINDAS carries no forecasts, there is no self-serve path. The forecast
> series must be *requested* from BAFU for cross-checking. This is now the plan's
> single next action, and everything below G1 stays blocked until BAFU answers.
>
> **Action — write to `abfragezentrale@bafu.admin.ch`.** The request must ask for,
> and get an explicit answer on, each of:
> 1. **Archived** forecast time series (not just real-time) on forecast-enabled
>    gauges — *this is the make-or-break item*; see the latency warning below.
> 2. Which gauges are forecast-enabled, and over what period the archive extends.
> 3. Whether **ensemble members** exist behind the published quantiles. The public
>    product is min/p25/median/p75/max only (Phase 0b) — if members are available on
>    request, full CRPS comparability is restored and G2 relaxes.
> 4. Forecast product identity: which model(s) produce it, and whether the product
>    changed over the archive period (a mid-archive model swap breaks a naive
>    pooled comparison).
> 5. Delivery format and cadence for any future/forward feed; and the **true issue
>    cadence** (the plot's `issued_at` and `produced_at` differ — see Phase 0b).
> 6. **Licence and publication rights** — explicit permission to compute and
>    *publish* comparative skill scores naming BAFU as the reference. Without this,
>    G3 is pointless: we could compute the benchmark and never show it.
> 7. **The route-C question, in one sentence** (Phase 0b): *does the `terms_open`
>    licence declared on opendata.swiss for `hydrologische-stationen-mit-vorhersagen`
>    extend to the forecast time series served at `/plots/q_forecast/…json`, and may we
>    archive it and publish derived skill scores?* A "yes" sanctions forward collection
>    starting immediately, in parallel with whatever happens to the archive request.
>
> Frame it as scientific validation of a forecasting system, not as a competitive
> exercise — note open question #2 below before sending. We already correspond with
> this address about the LINDAS Monday-window issue
> (`docs/decisions/bafu-lindas-monday-window.md`), so there is a live channel.
>
> **Record the reply in this doc** (date, contact, answer per item above) and only
> then move to G2. A refusal, or archive-unavailable, routes to option D.

Options, in order of preference — **A is chosen**; B/C/D retained as fallbacks
should A be refused:

| # | Route | What to do | Risk |
|---|---|---|---|
| **A** ✅ | **Ask BAFU directly** — *chosen, pending* | Written request to `abfragezentrale@bafu.admin.ch` (see the six items above). | Slow; may be refused. Cheapest and most honest. |
| B | **GIN entitlement** | Establish whether hydrosolutions can hold a GIN account, and whether GIN terms permit *scientific comparison and publication*. | Likely authority-only; publication rights probably restricted. |
| C | **Scrape `hydrodaten.admin.ch`** — *technically proven, legally blocked* | Endpoint chain and payload fully characterised — see **Phase 0b**. Nothing left to reverse-engineer. | **Blocked on licensing, not effort.** `terms_open` is declared for the *station-location* dataset, not demonstrably for the forecast series; the general admin.ch notice demands prior written consent for reproduction. Also **forward-only — no history** (Plan 058 trap). Do not write a collector until G1's reply covers the plot endpoints in writing. |
| D | **Abandon the external benchmark** | Keep climatology/persistence only; state the limitation explicitly in the paper. | Zero cost. **This is an acceptable outcome.** |

**G1 exit:** a written answer in this doc naming the chosen route, the licence /
permission status, and — critically — whether **historical** forecasts are
obtainable or only forward collection.

> If only forward collection is available (route C, and probably B), the plan
> acquires a multi-month latency exactly like Plan 058's observation archive.
> Say so out loud before committing; a benchmark that produces its first number in
> 2027 is not a v1 deliverable.

---

## Gate G2 (blocking) — is the comparison methodologically fair?

Do not skip. A sloppy head-to-head against a national agency is worse than no
head-to-head. Resolve on paper before any code:

- **Station subset.** BAFU forecasts a *subset* of its gauges — **54 as of 2026-07-08**
  (41 river discharge, 13 lake level; Phase 0b). The comparison population is that subset
  ∩ our onboarded stations, and results must not be generalised beyond it. Station keys
  join directly to LINDAS codes.
- **Issue-time alignment.** Compare forecasts issued at (approximately) the same
  time, or explicitly model the offset. A forecast issued 6h later is not a
  competitor; it is a different product.
- **Lead-time alignment.** Score per lead time, per the existing Flow 8 convention.
- **Deterministic vs ensemble — RESOLVED by Phase 0b.** BAFU publishes **quantiles, not
  members**: min / p25 / median / p75 / max. Ensemble CRPS is not computable on their
  side. **Decision:** score the head-to-head on the metrics both products support —
  **pinball loss at q25/q50/q75**, MAE / NSE / KGE / PBIAS on the median,
  POD / FAR / CSI, peak-timing error — and report CRPS for SAPPHIRE only, saying so
  explicitly. Reducing our ensemble to the same five statistics before scoring is the
  fair move; do **not** compare our full-ensemble CRPS against a quantile-derived
  approximation of theirs.
- **Horizon mismatch.** BAFU's horizon is ≈4.7 days hourly (114 steps). Truncate both
  products to the common horizon before scoring, per lead time.
- **Truth series.** Use our QC'd observations (already ingested). Note that BAFU
  produced both the forecast *and* the observation — flag it, it is not a defect.
- **No cherry-picking.** Pre-register the station set, period, and metric list in
  this doc *before* computing anything.

---

## Gate G3 — engineering shape (only if G1 and G2 both pass)

The system has no concept of a *third-party* forecast. `forecast_values` is keyed by
our `ModelId`. Two shapes:

**Recommended — register BAFU as a pseudo-model.** A `ModelId` like
`bafu_reference` whose "predictions" are ingested rather than computed. This
**reuses the entire Flow 8 skill machinery for free** — CRPS, BSS, POD/FAR/CSI,
peak timing, NSE, KGE, per-lead-time / per-season / per-flow-regime slicing,
and the skill-score store. That is most of the work, already written and tested.

The cost is precisely one enum change plus its blast radius:

- `ModelTier` (`src/sapphire_flow/types/enums.py:102`) is currently `SKILL |
  FALLBACK`. A third member — `REFERENCE` (or `EXTERNAL`) — is required, because a
  pseudo-model **must be excluded** from:
  - **multi-model combination** (`POOLED` / `BMA`) — we must never blend a rival's
    forecast into our own operational output;
  - **alerting** — see `AlertEligibility`; a reference model must not raise alerts;
  - **fallback selection** in the Flow 1 priority chain.
- Audit every `ModelTier` call site. The `FALLBACK`-exclusion logic (Plan 100) is the
  template — the same predicates likely need to become "tier is SKILL" rather than
  "tier is not FALLBACK". **This is the one place a careless change leaks a
  third-party forecast into operational output.** Treat as the plan's primary risk.
- An ingest adapter, shaped by G1's answer. Not a `WeatherForecastSource` — that
  Protocol is NWP. This is a new `ExternalForecastSource` concept, or a plain
  offline importer if we get a one-time historical export (route A).

**Rejected — a separate `external_forecast_values` table.** Duplicates the skill
service, the stores, and the metric suite for no gain. Only revisit if the
`ModelTier` blast radius turns out to be larger than the duplication.

**Hard non-goal:** this never enters Flow 1's operational path. It is an offline /
research comparison. No BAFU forecast is ever served by the API as a SAPPHIRE
product, combined into an ensemble, or used to raise an alert.

---

## Sequencing

```
G1  request to BAFU: (a) archive?  (b) licence covers /plots/*.json?
        │
        ├─ (b) yes ──> route C collector may start NOW (forward-only clock starts)
        │
        ├─ (a) yes ──> historical benchmark possible immediately
        │
        └─ both no ──> option D: close plan, record the limitation
                                 in publication-plan.md
        │
G2 (methodology, on paper — deterministic-vs-quantile already resolved)
        │ pass
G3 (ModelTier.REFERENCE + ingest adapter + audit)  ──> skill run ──> paper
```

The two halves of the G1 reply are independent. A licence "yes" alone still starts the
forward-collection clock today, which is the argument for asking now rather than later:
**every week of delay is a week of archive we do not have.**

Rough effort **if** G1 and G2 pass and a historical export exists: 2–4 days
(one enum + call-site audit + importer + a scored run). Rough effort if only forward
collection is possible: **+1 day** for the route-C collector (Phase 0b — the endpoints
are known, the parse is five named traces), **plus 6–12 months of latency** before the
numbers mean anything. Latency, not engineering, is this plan's cost.

## Open questions for the owner

1. ~~Is an external head-to-head wanted at all?~~ **Resolved 2026-07-08: yes — request
   the data from BAFU (route A).** Option D remains the fallback if BAFU refuses or
   holds no archive.
2. **Answer before sending the request.** Does a comparative publication risk the BAFU
   relationship we depend on for LINDAS? LINDAS is our only Swiss observation source,
   and the same office owns both. Framing matters: "scientific validation against the
   operational reference" reads very differently from "we beat BAFU".
3. Same question for Nepal DHM in v1: does a "we beat the national agency" framing
   help or hurt the deployment? Whatever we settle on for BAFU sets the precedent.

## Non-goals

- Ingesting BAFU forecasts operationally.
- Any change to Flow 1, alerting, or the API contract.
- Comparing against non-BAFU forecast providers (out of scope; revisit only if this
  plan lands).
- **Writing the route-C collector before G1's licence answer arrives.** The endpoints are
  characterised (Phase 0b) precisely so this decision can be made on facts. Characterising
  is not consent.
