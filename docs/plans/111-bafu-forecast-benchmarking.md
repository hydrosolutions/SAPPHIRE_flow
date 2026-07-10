# Plan 111 — Benchmarking SAPPHIRE forecasts against BAFU's operational forecasts

**Status:** READY — plan-reviewed 2026-07-10 (3 rounds). **In implementation: the route-C
collector only** (see the Override below). The scoring half (G2 pre-registration + G3
scorer) stays **BLOCKED on external gate G1**: no benchmark can be computed or published
until the BAFU request (archive, licence over `/plots/*.json`, publication rights) returns.

> **Owner override (2026-07-10) — build the route-C collector NOW, pre-G1.** The owner
> accepts the *collect-now / discard-if-refused* posture: start the forward-only archive
> immediately (every week of delay is a week of history we never get back — the endpoint
> holds no past), while the BAFU licence question is still open. This deliberately overrides
> the "do not write the collector before G1" non-goal below. **Bounded by four conditions,
> all mandatory:** (1) an honest, identifying `User-Agent` (SAPPHIRE / hydrosolutions +
> contact) so BAFU can see who we are and object; (2) the archive is **quarantined** —
> written to a dedicated path, never into the operational DB or any `ModelId`, so a single
> `rm` discards it if BAFU refuses; (3) **evaluation-only** — no model ever trains or tunes
> on this data (a discard cannot un-fit a parameter); (4) polite client (rate limit, retry
> cap, raw-payload archival). **The BAFU letter is deferred** (framing undecided, owner
> 2026-07-10) — so collection proceeds *without* proactively contacting BAFU; the honest
> `User-Agent` is the transparency substitute until the letter is sent. Discarding protects
> *publication*, not the *fact* of collection — the owner has weighed and accepted that.
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
`.json` returns `application/json`, unauthenticated. Discharge is `q_forecast`;
water level is `p_forecast` (present on lake/level stations). (Exact HTTP status codes
per extension and the response byte size are endpoint-shape trivia that drive no gate
decision; deliberately not pinned here.)

**Station inventory (2026-07-08):** **54** forecast stations — 41 river
(`metric: discharge_ms`, unit `m³/s`) and 13 lake (`metric: masl`, unit `m ü.M.`).
The feature `key` (e.g. `2135` = *Aare – Bern, Schönau*) is the **BAFU station number**,
i.e. the same identifier LINDAS uses. **The join to our observations is free** — no
fuzzy name matching, no crosswalk table.

**Payload shape** (station 2135, discharge) — only what the gates need:

- Quantile summary, **not** members: min / p25 / median / p75 / max (drives G2's
  metric choice — see Phase 0b finding 1).
- **114 hourly steps**, horizon ≈**4.7 days** (drives G2's horizon truncation).
- Units in `trace.meta.unit` as `"m³/s"` — **byte-identical to our canonical discharge
  unit** (the join is free).
- **Issue time is machine-readable** in the JSON, and `produced_at` on the GeoJSON;
  the two differ (issue ≠ publication — see the collector note below).

(The exact trace names, the `fill: tozerox` render style, and the
annotation-array-index brittleness are scraper-implementation detail; they belong in
the collector's docstring *if and when* it is written, not in a plan that outlives an
undocumented endpoint. Deliberately not pinned here.)

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

### The collector's shape — **NOW BEING BUILT** (owner override 2026-07-10, see Status)

Building pre-G1 under the four bounding conditions in the Status override (identifying
`User-Agent`, quarantined archive, evaluation-only, polite client). In one
paragraph: a small hourly Prefect flow over the 54-station GeoJSON, deduped on
`issued_at` (**not** fetch time — issue ≠ publication; on 2026-07-08 issue was 15:00,
`produced_at` 18:30), archiving the **raw** Plotly JSON alongside parsed rows (re-fetch
is impossible — the endpoint is forward-only), polite client (contact `User-Agent`,
rate limit, retry cap), failing **loudly into Flow 4** monitoring rather than writing
empty archives (compare the `live-lindas-weekly` Monday-window failures,
`docs/decisions/bafu-lindas-monday-window.md`). Storage ≈36 MB/day raw — wire into the
Plan 105 disk-hygiene budget. Full collector design (poll cadence, dedup key, retry
policy) is deferred to the collector's own code when/if G1 clears; the +1-day estimate
already accounts for it, so there is no reason to pre-spend those decisions in this doc
for an endpoint that can change without notice. **Caveat on "loudly into Flow 4":** that
integration point does **not** exist yet for a non-Flow-1 producer — the collector has to
add a `PipelineCheckType` member and a Flow 4 check itself (see G3's Flow-4 hook item); it
is a task, not a free inheritance.

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
- **Forecast-coverage overlap — pre-register it, do not assume it.** A head-to-head needs
  SAPPHIRE forecasts *issued over the same window* the BAFU archive covers — not just BAFU
  forecasts plus our observations. SAPPHIRE's `HindcastForecast` coverage per station is
  bounded by that station's own onboarding/training history and forcing availability
  (`docs/architecture-context.md` H.2/H.3), **not** guaranteed to reach back over a
  multi-year BAFU archive: BAFU could plausibly return 10+ years, while most SAPPHIRE
  stations will have far less hindcast depth. So before committing to the comparison window,
  intersect the BAFU archive period with each candidate station's actual
  `HindcastForecast` / `skill_scores` coverage and state the resulting (likely much smaller)
  overlap explicitly. The real comparison population is the double intersection —
  *BAFU-forecast stations ∩ our onboarded stations ∩ the per-station time window where both
  sides have forecasts* — and the "historical benchmark possible immediately" framing in the
  Sequencing diagram holds only for that overlap, not the full archive.
- **Issue-time alignment.** Compare forecasts issued at (approximately) the same
  time, or explicitly model the offset. A forecast issued 6h later is not a
  competitor; it is a different product.
- **Lead-time alignment.** Score per lead time, per the existing Flow 8 convention.
- **Deterministic vs ensemble — RESOLVED by Phase 0b.** BAFU publishes **quantiles, not
  members**: min / p25 / median / p75 / max. Ensemble CRPS is not computable on their
  side. **Decision:** score the head-to-head on the metrics both products support —
  **pinball loss at q25/q50/q75**, MAE / NSE / KGE / PBIAS on the median,
  POD / FAR / CSI on **median exceedance** (see the contingency caveat below),
  peak-timing error — and report CRPS for SAPPHIRE only, saying so
  explicitly. Reducing our ensemble to the same five statistics before scoring is the
  fair move; do **not** compare our full-ensemble CRPS against a quantile-derived
  approximation of theirs.
- **Contingency-table caveat — the same ban, applied to POD/FAR/CSI.** The existing
  `compute_contingency` (`src/sapphire_flow/services/skill/metrics.py:62-86`) is **not** a
  deterministic function: it derives an exceedance probability
  `forecast_prob = np.mean(ensemble > threshold, axis=1)` over a 2-D
  `(n_times, n_members)` matrix and cuts it at `decision_probability` (default 0.5,
  `service.py:41`); its only caller always passes a full member stack. Feeding BAFU's five
  quantile columns into it as if they were five equiprobable members is **the exact
  pseudo-member substitution this gate forbids for CRPS** — the derived probability could
  take only six coarse values (0/5…5/5) and would silently fold in `min`/`max`, which are
  not quantile levels (Phase 0b finding 1). **Decision:** POD/FAR/CSI for the head-to-head
  are computed **deterministically** — `forecast_yes = (median > danger level)` vs
  `observed_yes = (obs > danger level)`, the same hit/miss/false-alarm arithmetic with
  **no `decision_probability` axis-1 averaging** — applied **symmetrically** to both
  products (BAFU median vs SAPPHIRE ensemble-median). This is a small **new helper**, not
  a reuse of `compute_contingency`; see Gate G3.
- **New-code note.** Both **pinball loss** and the **deterministic contingency helper**
  are new code: `metrics.py` has no quantile-native or single-value-contingency function,
  and the one existing QUANTILES path (`_ensemble_matrix`, `service.py:44-51`) feeds
  quantiles into `compute_crps` as pseudo-members — precisely the approximation forbidden
  here. These are two small new functions; their provenance (a thin `sklearn` wrapper, not a
  from-scratch metric) and cost are stated once in Gate G3 — see there.
- **Horizon mismatch.** BAFU's horizon is ≈4.7 days hourly (114 steps). Truncate both
  products to the common horizon before scoring, per lead time.
- **Truth series.** Use our QC'd observations (already ingested). Note that BAFU
  produced both the forecast *and* the observation — flag it, it is not a defect.
- **No cherry-picking.** Pre-register the station set, period, and metric list in
  this doc *before* computing anything.

---

## Gate G3 — engineering shape (only if G1 and G2 both pass)

> This is a *gate*, not an implementation spec. G3 fires **only if G1 and G2 both pass**,
> and both are still open. The subsystem-level entanglement below is enough to choose a
> route; whoever eventually builds it should grep the then-current code for the real
> touchpoints rather than trust file:line numbers frozen in a doc, which rot the moment
> those files move.

Two shapes were weighed. **The recommendation flipped during design review: the standalone
offline scorer is Recommended, the pseudo-model route is Rejected** — the accounting below
is what flipped it.

**Recommended — a standalone offline scorer writing a run-id-tagged parquet archive**
(owner decision 2026-07-10; the migrated-table variant is retained as the heavier
alternative in the persistence bullet below). A one-off importer loads the BAFU series
(from G1's export or the route-C archive), aligns it to our QC'd observations by station
key, and — this is the load-bearing point the head-to-head turns on — **scores *both* sides
through the same new metric functions**: the BAFU quantile series **and** SAPPHIRE's own
forecast reduced to the same five statistics (G2), each run through the new pinball-loss
wrapper and the new deterministic-contingency helper, then written as sibling rows
discriminated by a `forecast_source` field (`bafu` vs `sapphire`). This is *not* a reuse of
the existing `skill_scores` rows: those carry no pinball loss, and their POD/FAR/CSI come
from the ensemble-probabilistic `compute_contingency` that G2 explicitly forbids here (see
the natural-key bullet). The archive therefore holds a fresh, apples-to-apples SAPPHIRE
score alongside the BAFU one so a single group-by answers the comparison. It **never mints a
`ModelId`, never writes a `ModelAssignment`, and never touches the `FALLBACK_MODEL_IDS`
keyspace** that combination, alerting, and fallback gate on — so it is *structurally
impossible* for this route to leak into Flow 1. That safety property is a guarantee, not an
audit. A flat parquet file for a one-off publication artifact also matches CLAUDE.md's
"Ad-hoc Analyses and One-Time Scripts" convention and keeps a low-priority,
off-critical-path plan out of the migration history entirely.

Genuinely reusable, `ModelId`-free, safe to call directly: the **deterministic** pure-numpy
functions in `src/sapphire_flow/services/skill/metrics.py` — `compute_nse`, `compute_kge`,
`compute_pbias`, `compute_mae`, `compute_peak_timing_error` — which take arrays and
thresholds, not a model registry. **`compute_contingency` is NOT in that list**: it is
ensemble-probabilistic (`metrics.py:62-86`, `np.mean(ensemble > threshold, axis=1)` cut at
`decision_probability`, `service.py:41`), so calling it on BAFU's five quantiles is the
pseudo-member trick G2 bans — POD/FAR/CSI use the new deterministic helper instead (G2's
contingency caveat). The existing QUANTILES path is off-limits for the same reason:
`_ensemble_matrix` (`service.py:44-51`) treats quantile values as pseudo-members and
`_compute_scores` (`service.py:191-229`) runs `compute_crps` over them — the standalone
scorer avoids both by construction.

New code this route needs (none of it "free"):

- **A pinball-loss wrapper** — a thin adapter over `sklearn.metrics.mean_pinball_loss`
  (scikit-learn already a dependency, `pyproject.toml:37`; used for `Ridge` in
  `models/nwp_regression.py:51`), scored at q25/q50/q75. Not a from-scratch metric. Note
  WMO-1364 (`docs/standards/wmo.md:43,105`) names CRPS/Brier/reliability/rank histograms
  as the normative verification set but is **silent on pinball/quantile loss** — a
  WMO-anchor gap for this metric; flag it in the metric's docstring rather than restating a
  definition `wmo.md` does not give (per `docs/touchpoint-maps.md:441`, "cite it, do not
  restate it").
- **A deterministic contingency helper** for POD/FAR/CSI on median exceedance (G2) — no
  member-count dependency, applied symmetrically to both products.
- **The importer + station-key alignment — scoring both sides.** It loads the BAFU series,
  loads SAPPHIRE's own forecast for the same stations/leads (from `HindcastForecast` /
  `skill_scores` provenance), reduces SAPPHIRE's ensemble to the same five statistics (G2),
  aligns both to our QC'd observations, and runs **both** through the new pinball + new
  deterministic-contingency functions, upserting `bafu` and `sapphire` rows.
- **Persistence — run-id-tagged parquet (chosen).** A flat parquet, versioned by
  `scorer_run_id`, deduped on read/aggregation. The multi-run/dedup need (re-runs during the
  months-long BAFU-archive-arrival window, without double-counting) is met by grouping/
  filtering on `scorer_run_id` at aggregation time, at near-zero infra cost — no migration,
  no downgrade test, no schema doc. *Alternative (heavier, not chosen):* a migrated
  `reference_benchmark_scores` table next to `skill_scores`, following the
  migration-not-raw-DDL convention (`docs/standards/cicd.md` § Alembic) with a downgrade-path
  test modelled on `test_migration_00XX_downgrade.py` under `tests/integration/db/`. Revisit
  only if the scored rows genuinely need to be queried alongside `skill_scores` in SQL.
- **A key / idempotency policy — with a `forecast_source` discriminator that is
  load-bearing, not cosmetic.** Whether parquet or table, every scored record is keyed by
  `(station_id, parameter, lead_time_hours, metric, computation_version, forecast_source,
  scorer_run_id)` with `forecast_source ∈ {bafu, sapphire}`. Without `forecast_source` a
  SAPPHIRE record and a BAFU record for the same station/lead/metric collide and one
  silently overwrites the other, **destroying the head-to-head the archive exists to hold**.
  Without `scorer_run_id` dedup the scorer inherits the `store_hindcast` hazard
  (`docs/touchpoint-maps.md:342,487`: writers with no dedup silently duplicate on re-run),
  so re-running during the multi-month archive-arrival window would inflate any downstream
  mean (mean pinball loss, mean CSI). All aggregation MUST group/filter by `scorer_run_id`.
- **A grouping loop** for per-lead / per-season slicing — Flow 8 wires this for `ModelId`
  scores; the standalone route re-implements the small loop over its own returned scores.
- **Doc updates (not optional — CLAUDE.md: "every code change updates affected docs").**
  For the chosen parquet route this is light: a single note in the Training/hindcast/skill
  `docs/touchpoint-maps.md` map recording that a standalone benchmark scorer writes a
  parquet archive **structurally separate from `skill_scores`**, so future agents do not
  conflate it with the skill-score writers, plus the frozen-dataclass domain type for a
  scored record (per the type-driven-development mandate) noted where the scorer lives.
  *(The heavier table alternative would additionally need a `types-and-protocols.md` entry
  for the store Protocol and an `architecture-context.md` schema section, mirroring how
  `skill_scores` is documented in both — that cost is one reason the parquet route was
  chosen.)*
- **A Flow-4 monitoring hook, only if the route-C collector is built** (Phase 0b promises
  the collector "fails loudly into Flow 4"). That safety net is **not yet wired for a
  non-Flow-1 producer**: `PipelineCheckType` (`src/sapphire_flow/types/enums.py:151-162`)
  is a closed enum with no external-scraper-staleness member (`FORECAST_FRESHNESS` /
  `OBSERVATION_FRESHNESS` are declared but not wired to any check logic beyond their
  declaration), and `append_health_record` is today called only from
  `flows/run_forecast_cycle.py` (Flow 1). So the collector must **add** a
  `PipelineCheckType` member and a Flow 4 check for itself — it cannot inherit an
  integration point that does not exist. `docs/touchpoint-maps.md` itself warns several of
  this subsystem's automations are manual-trigger-only or DRAFT.

**Verification** (a deliverable, not an afterthought — CLAUDE.md testing philosophy):

- a pinball-loss unit test against a hand-computed reference value (and/or a direct
  `sklearn.metrics.mean_pinball_loss` cross-check);
- a deterministic-contingency unit test with known hit/miss/false-alarm counts;
- an importer idempotency / re-run test asserting no record duplication on the
  `(…, forecast_source, scorer_run_id)` key;
- a reference fixture capturing one real BAFU `q_forecast` JSON payload shape (the
  `tests/fixtures/reference/**` convention used for other external adapters, plans
  019/020/021/045).

**Rejected — register BAFU as a pseudo-`ModelId` and reuse Flow 8 end-to-end.** This was
the original recommendation; the accounting flipped it. Registering BAFU as a pseudo-model
entangles it with **three real gating subsystems** — the `FALLBACK_MODEL_IDS` /
combinability keyspace (a non-fallback id reads everywhere as a combinable, skill-tier
model, and a `ModelTier.REFERENCE` enum member does *not* fix it because the load-bearing
sites test `FALLBACK_MODEL_IDS` membership, not the tier), the `AlertEligibility`
declare-or-fail gate (no existing value is honest for a deliberately-hidden external
forecast), and the `HindcastForecast` / `model_artifacts` / `ArtifactScope` schema (no
representation for an ingested-not-trained series). Each would have to be generalized, which
turns the plan's core non-goal from *structurally impossible* into merely *policed* — and it
**still** needs the same new pinball + deterministic-contingency code on top. Net: strictly
*more* total work than the standalone scorer, so it is rejected, not cheaper. (Current call
sites rot the moment those files move; whoever revives this route should re-grep rather than
trust a citation frozen here. If the subsystem audit trail has standalone value, it belongs
in a scratch investigation note, not this plan.) `ForeignForecast` is the wrong precedent
too — it is SAPPHIRE-to-SAPPHIRE federation (`upstream_instance_url`, no backing DB table),
not an agency feed.

- **Ingest adapter.** Shaped by G1's answer — not a `WeatherForecastSource` (that Protocol
  is NWP). A plain offline importer for a one-time historical export (route A); a small
  parser over the route-C archive otherwise.

**Hard non-goal:** this never enters Flow 1's operational path. No BAFU forecast is ever
served by the API as a SAPPHIRE product, combined into an ensemble, or used to raise an
alert. The standalone route makes this non-goal *structurally true*; the pseudo-model route
would make it merely *policed*.

---

## Sequencing

```
G1  request to BAFU — THREE independent answers:
      (a) archive available?   (b) licence covers /plots/*.json?   (c) publication
                                                                       rights (item 6)?
        │
        ├─ (b) yes ──> route C collector may start NOW (forward-only clock starts)
        │
        ├─ (a) yes ──> historical data obtainable — but the benchmark is only as wide
        │              as SAPPHIRE's OWN forecast coverage over the archive window
        │              (G2 coverage-overlap item); NOT "the full archive immediately"
        │
        ├─ (c) NO ──> publication rights refused ──> DO NOT proceed to a published G3
        │              run. Fall back to option D (or compute-only-internal, unpublished).
        │              Data access alone does NOT green-light the G2/G3 build:
        │              item 6 is the make-or-break for a "publication artifact" plan.
        │
        └─ (a)&(b) both no ──> option D: close plan, record the limitation
                                         in publication-plan.md
        │
G2 (methodology, on paper — deterministic-vs-quantile already resolved;
        coverage-overlap window pre-registered)
        │ pass  AND  (c) publication rights granted
G3 (standalone scorer: pinball wrapper + deterministic contingency helper + importer
        scoring BOTH bafu & sapphire sides → run-id-tagged parquet keyed by
        forecast_source [migrated table = heavier alternative],
        reusing the deterministic metrics.py functions)  ──> skill run ──> paper
```

The three parts of the G1 reply are independent, and **(c) publication rights gate the
whole G3 build** — per G1 item 6, "without this, G3 is pointless: we could compute the
benchmark and never show it." So archive-yes + licence-yes but publication-no must **not**
trigger the several days of new metrics code, importer, and scoring work for output that can
never appear in the paper. A licence "yes" (b) alone still starts the forward-collection clock
today, which is the argument for asking now rather than later: **every week of delay is a
week of archive we do not have.**

Rough effort **if** G1 (including publication rights, item 6) and G2 pass and a historical
export exists, taking the Recommended standalone route (chosen parquet persistence): ~**3–4
days** — the two small new metric functions (pinball wrapper + deterministic contingency
helper; provenance costed in G3), a small importer + alignment that scores **both** the BAFU
and SAPPHIRE sides, a run-id-tagged parquet archive with a `forecast_source`-discriminated
key and dedup-on-aggregation, a grouping loop for per-lead/per-season slicing, the
Verification tests listed in G3, the light doc update (a touchpoint-maps note), and a scored
run. (The heavier migrated-table alternative adds ~1 day for the Alembic migration +
downgrade test + schema docs — one reason parquet was chosen.) (The earlier "one enum
change, 2–4 days" figure assumed the pseudo-model route reused Flow 8 for free; G3 shows it
does not — that route is strictly *more* work, so it is rejected, not cheaper.) Rough effort
if only forward collection is possible: **+1 day**
for the route-C collector (Phase 0b — endpoints known, parse is five named traces),
**plus 6–12 months of latency** before the numbers mean anything. Latency, not
engineering, is this plan's dominant cost.

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
4. ~~G3 persistence: migrated DB table vs run-id parquet?~~ **Resolved 2026-07-10:
   run-id-tagged parquet** — near-zero infra for a low-priority research artifact that may
   never leave DRAFT; the migrated table is retained in G3 as the heavier alternative.

## Non-goals

- Ingesting BAFU forecasts operationally.
- Any change to Flow 1, alerting, or the API contract.
- Comparing against non-BAFU forecast providers (out of scope; revisit only if this
  plan lands).
- ~~Writing the route-C collector before G1's licence answer arrives.~~ **OVERRIDDEN by
  the owner 2026-07-10** — the collector is being built now under four bounding conditions
  (see the Status override). Characterising was not consent; this is a deliberate,
  owner-accepted *collect-now / discard-if-refused* decision, not a silent workaround.
- **Publishing** any BAFU-derived skill score before G1 returns publication rights. The
  collector may archive; the *scorer* and the paper stay gated. (Unchanged.)
- **Training or tuning any model on the collected BAFU data.** Evaluation-only, always.
