# Plan 111 — Benchmarking SAPPHIRE forecasts against BAFU's operational forecasts

**Status:** DRAFT
**Priority:** Low — nice-to-have. Not on the v1.0 Nepal critical path (Plan 106).
**Type:** Research / publication artifact. If it ever becomes code: hold-at-PR.
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-08
**Next action:** **Request forecast data from BAFU** (`abfragezentrale@bafu.admin.ch`)
— see Gate G1. LINDAS carries no forecasts (Phase 0, below), so there is no self-serve
path; everything else in this plan is blocked on that reply.
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
> 3. Per record: issue time, lead time / valid time, and whether **ensemble members**
>    are available or only a median + spread (drives G2's metric-comparability
>    question — see the CRPS note).
> 4. Forecast product identity: which model(s) produce it, and whether the product
>    changed over the archive period (a mid-archive model swap breaks a naive
>    pooled comparison).
> 5. Delivery format and cadence for any future/forward feed.
> 6. **Licence and publication rights** — explicit permission to compute and
>    *publish* comparative skill scores naming BAFU as the reference. Without this,
>    G3 is pointless: we could compute the benchmark and never show it.
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
| C | **Scrape `hydrodaten.admin.ch`** | Reverse-engineer whatever XHR feeds the forecast plots. | **Check terms of use before writing a single line.** A federal site's ToS may prohibit automated retrieval and/or redistribution. Also: real-time only (same trap as LINDAS, see Plan 058) — a scrape gives us **no history**, so a benchmark would need months of forward collection before it says anything. Do not start C without a ToS reading and a written note in this plan. |
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

- **Station subset.** BAFU forecasts a *subset* of its gauges (count TBC). The
  comparison population is that subset ∩ our onboarded stations, and results must
  not be generalised beyond it.
- **Issue-time alignment.** Compare forecasts issued at (approximately) the same
  time, or explicitly model the offset. A forecast issued 6h later is not a
  competitor; it is a different product.
- **Lead-time alignment.** Score per lead time, per the existing Flow 8 convention.
- **Deterministic vs ensemble.** If BAFU publishes a median + spread rather than
  members, CRPS is not directly comparable to our ensemble CRPS. Decide the
  reconciliation (score both against a common deterministic reduction? compare only
  on metrics both support — MAE, NSE, KGE, POD/FAR/CSI, peak-timing error?).
  **Recommendation:** report the metrics both products support, and state that CRPS
  is reported for SAPPHIRE only.
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
G1  request data from BAFU (route A)  ──refused / no archive──> option D:
        │ granted (+ publication rights)   close plan, record the limitation
        │                                  in publication-plan.md
G2 (methodology, on paper)
        │ pass
G3 (ModelTier.REFERENCE + ingest adapter + audit)  ──> offline skill run ──> paper
```

Rough effort **if** G1 and G2 pass and a historical export exists: 2–4 days
(one enum + call-site audit + importer + a scored run). Rough effort if only forward
collection is possible: the same, **plus 6–12 months of latency** before the numbers
mean anything.

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
