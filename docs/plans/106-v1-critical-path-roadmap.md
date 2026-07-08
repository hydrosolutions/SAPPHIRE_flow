# Plan 106 — v1 (Nepal DHM) Critical-Path Roadmap

**Status:** DRAFT
**Type:** Roadmap / sequencing plan (no code — sequences other plans, identifies knowledge gaps)
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-08
**Supersedes/organizes:** the "v1 gaps — no plan yet" list in `docs/plans/README.md`; places every category-**B** plan (015, 017, 035, 047, 080, 081, 082) and the deferred auth plan (042) into a locked wave sequence.

> This is a **planning** deliverable. It does not implement anything. It (a) locks the
> v1 wave sequence, (b) classifies every remaining piece as **designable-now** vs
> **blocked-on-external-knowledge**, and (c) lists the concrete questions to send DHM,
> HSOL, and the recap Data-Gateway dev. Each downstream gap-plan gets its own
> grill-me → WF1 plan-review → (optional) independent review → WF2 in an isolated
> worktree, hold-at-PR.

---

## 0. Locked strategic decisions (grill-me, 2026-07-08)

These six decisions frame the whole roadmap. They are the owner's calls; the rest of
this document is derived from them.

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Subsystem-hardening first, not thin-vertical-slice.** Build and harden every designable-now subsystem against **Swiss data** (the live mac-mini), so that when DHM access lands, DHM is an **adapter-swap + config**, not a design effort. | We do not have DHM data access yet. Designing a "Nepal end-to-end slice" now would be designing against unknowns. |
| D2 | **RivRetrieve / other-public-data = optional stress-test, NOT a gated wave.** Build the DHM-shaped subsystems (obs adapter, unit normalization, rating curves) against fakes + unit tests; probe with RivRetrieve foreign gauges opportunistically. **DHM is the first real integration.** | Keeps WIP down; no plan/wave hangs off a foreign-data milestone. |
| D3 | **Wave 0 = lean "stabilize the base" gate.** Reliability fixes that kill the Swiss feed (105 disk hygiene, 090 horizon coverage) land first; observability (097, 103) runs in parallel, non-gating. | We develop against the live mac-mini; a flaky base costs debugging time. Silent feed kills occurred 2026-07-03/06. |
| D4 | **Auth/RBAC/audit + tenant WRITE-isolation are designable now.** The topology model (shared on-prem prod, cloud→staging, station-group-scoped ownership, authn+audit mandatory, no gateway read-isolation) is firm enough to design against. **BLOCKED:** physical hosting location → off-site backup target + network/access + who-admins. | Per `project_nepal_v1_collaborator_requirements`. The *mechanism* is decided; only the deployment's physical home and role/ownership *values* are collaborator input. |
| D5 | **First NEW v1 plans to draft (post-Wave-0):** (1) **081** recap forcing adapter, (2) a **new DHM-observation + water_level-unit-normalization** plan, (3) a **new auth/RBAC/audit + tenant-write-isolation** plan. **035** (rating-curve provenance, genuinely `READY`) goes **straight to implement** (no re-draft). **015** is NOT straight-to-implement: only its v0 `GaugingStatus` enum slice shipped (`1a88f92`); the v1 flow logic (`calculated_station_formulas` table, `COMPONENT_DERIVED` enum value, Flow 2 tiered derivation, Flow 5 branching, QC propagation) is undesigned — so 015 needs a **grill-me + WF1 re-draft before WF2** (README moved it READY→DRAFT in `2795c87`). | These are independent, designable-now tracks that parallelize; they converge only at Flow 0 Nepal onboarding and at live DHM integration. |
| D6 | **v1.0 is HEADLESS.** Flow 3 (review/publish/adjust), the dashboard, Excel bulletin, and the Bikram Sambat calendar are **deferred to v1.x**. v1.0 = gateway forcing → DHM obs → forecast → REST API → webhook alerts; DHM consumes via the API and runs its own review/alerting in-house. | Per `project_nepal_alerting_scope` (DHM may handle alerting in-house; dashboard targets v2). Shrinks the Oct-2026 critical path to the data+model+deploy spine. |

**Corollaries baked in (not re-litigated):**
- **No elevation-band / gridded-NWP extraction for Nepal.** Forcing arrives as basin/band **time-series** from the Data-Gateway API (`recap-dg-client`). ICON-mesh extraction (Plan 087) is Swiss/v0-only.
- **ERA5-Land is NOT a separate CDS adapter.** It is the gateway `era5_land_reanalysis` endpoint, covered by 081/082. README gap #4 is **subsumed**, not a standalone build.
- **IFS ensemble = 1 `fc`(HRES, member_id=0) + 50 `pf` = 51 members** (ECMWF discontinued `cf`; resolved, no gateway ask). Per `project_recap_ifs_fc_hres_member0`.
- **recap-dg-client distribution mirrors FI — but is a PRIVATE repo, so the git-pin is NOT a drop-in copy of the FI exception.** `forecastinterface` is **public** (HTTPS clone, no auth — `ci.yml:98-102`); recap-dg-client needs authenticated clone. Adding it as a git-pin has **four concrete CI/Docker entry-gates** (make these explicit in-scope items for Plan 081's grill-me, else the first WF2 run fails CI):
  1. a **CI secret** — SSH deploy key or fine-grained PAT scoped to the recap-dg-client repo;
  2. **SSH-URL (or token-injected) form** for the pin in `pyproject.toml`;
  3. a **second two-step wheel-guard block** in `ci.yml` mirroring `ci.yml:98-102` — step 1 adds `--no-install-package recap-dg-client` under the `--no-build` guard, step 2 adds `--reinstall-package recap-dg-client`;
  4. a **Dockerfile BUILDER change** if the build runner lacks the key/agent (git is already present from the FI work; the new need is auth, not the git binary).
  These are hard entry-gates for **Plan 082** (the first CI-touching build), **not Plan 081**. Plan 081 stays **offline** (injected fakes only) and explicitly **does not** add recap-dg-client to `pyproject.toml` (`081:65-67`); the git-pin lands in 082 where live smoke needs it (`081:128`). The four gates fold into 082 (with 080 for the eventual wheel migration) for tracking — call them out in Plan 081's grill-me only so the **082** design already carries them, not so 081 blocks on CI.

---

## 1. Locked wave sequence

```
Wave 0  Stabilize the Swiss dev base            [immediate, no external deps]
Wave 1  Nepal forcing spine                     [designable now; live smoke blocked on gateway creds]
Wave 2  Nepal observation & rating-curve path   [mechanism designable now; DHM specifics blocked]
Wave 3  Multi-tenant, auth & deployment hardening [auth designable now; host/off-site blocked]
─────── DHM ACCESS LANDS ───────                [gate: real DHM creds + data + shapefiles arrive]
Wave 4  Nepal integration + go-live             [blocked until access; adapter-swap + config PLUS a Nepal model-training + onboarding run (Flow 6 + Flow 12-B) against the real DHM archive — trained artifacts + model assignments are a hard go-live prerequisite]
Wave 5  v1.x deferred (post-v1.0)               [forecaster-facing + niceties]
```

Waves 1, 2, and 3 are **three independent designable-now tracks** — with review
bandwidth they run **in parallel** after Wave 0. They are drawn as an ordered list only
to show drafting priority (D5), not a hard serial dependency. They **converge** at two
points: **Flow 0 Nepal onboarding** (needs forcing + obs + auth all present) and the
**DHM-access gate** (Wave 4).

### Hard-prerequisite chains (these ARE serial)

```
038 (store write atomicity) ──► 040 (hindcast dedup)   [Wave 0; 040 depends_on 038 — 040 line 5; 040 must not land before 038]
FI published to private index ─────────────► 080 (drop git-pin + CI wheel-guard exception)
081 (offline adapter) ──► 082 (live smoke/coverage) ──► 047 (Nepal data-sources umbrella)
035 (provenance, READY) ──► rating-curve h→Q ingestion ──► Stage 2 QC (2.5–2.7)
water_level unit normalization ──► removes the Plan 101 cm-onboarding guard
046 (staging validation, IN_PROGRESS) ──► 048 (restic + restore rehearsal)   [048/049 are PARALLEL, both downstream of 046; NO 048→049 edge]
046 (staging validation, IN_PROGRESS) ──► 049 (Cloudflare public URL + SSO)  [049 depends_on 046 only — 049 line 5; 048 depends_on 046 only — 048 line 5]
auth/RBAC foundation ──► tenant write-isolation ──► Flow 0 Nepal onboarding (owner-scoped)
DHM obs adapter + unit-norm ──► Nepal obs ingest ──► rating-curve derivation (Stage 2 needs h→Q)
```

### What parallelizes

- **Wave 0** gates only platform stability; its observability items (097, 103) run alongside its reliability items (105, 090).
- **Forcing (Wave 1)**, **Obs/rating (Wave 2)**, and **Auth (Wave 3)** are mutually independent — draft/build concurrently to the extent review bandwidth allows.
- **035** (genuinely READY) can be implemented at any time in parallel with drafting the Wave-1/2/3 leads. **015** is NOT ready-to-build (only its enum slice shipped) — it needs a grill-me + WF1 re-draft first, and it **must merge before 017**. The 017→015 edge is a **behavioral** dependency, not a type one: `GaugingStatus.CALCULATED`/`GAUGED` already exist in the codebase (`src/sapphire_flow/types/enums.py:184-187`, shipped in `1a88f92`). 017 branches on plan-015's **undesigned v1 flow logic** — 015 D6 (CALCULATED stations exempt from rule-based QC — `017:49-58`) and the 015 Flow 4 component-station-freshness contract (`017:53-56`). The type is shipped; the design contract is not.
- Live-dependent tails — **082** live smoke, **Flow 0** Nepal specifics, **048** off-site — block on external answers and are pulled forward only as those answers arrive.

---

## 2. Remaining category-B / gap pieces + prod-gating correctness fixes — classification table

> **Scope of this table:** the category-**B** v1 plans (per `docs/plans/README.md` §Active — "v1 Nepal feature (B)", line 50) plus the
> **gap** items with no plan yet, **plus** the two correctness-bug category-A plans
> that gate any prod deploy (**038** store-write atomicity, **040** hindcast dedup —
> both Swiss-testable NOW). It is **not** a mirror of the full category-A operational
> backlog: the other active category-A plans (**094, 075, 064, 069, 058, 083, 091**
> in `docs/plans/README.md:24-48`) are ordinary Swiss-side hardening tracked in the
> README and are **not** re-sequenced here — they run on the mac-mini dev track
> independently of the Nepal wave sequence.

**Legend — Class:** `NOW` = designable & buildable now (against Swiss data / fakes /
RivRetrieve); `NOW*` = *mechanism* designable now, DHM/collaborator *values* wire in as
config later; `BLOCKED` = core design needs an external answer first (see §3).

| Item | Plan | Status | Class | Depends on | Wave |
|------|------|--------|-------|------------|------|
| Disk hygiene / NWP scratch cleanup | **105** | DRAFT (grill-me done) | NOW | — | 0 |
| NWP incomplete-cycle / horizon coverage (P2) | **090** | DONE (P1 — PR #49); P2 pending (OPTIONAL — re-scope into own plan) | NOW | — | 0 (parallel, NON-gating) |
| Store write atomicity (two-phase insert crash → orphan header) | **038** | DRAFT | NOW | — | 0 |
| Hindcast deduplication unique constraint | **040** | DRAFT | NOW | **038** (`040:5` — queue behind 038 within Wave 0) | 0 |
| Prefect worker observability & home | **103** | DRAFT (grill-me PENDING — D2 mechanism + D3 level-split unresolved) | NOW | supersedes 062 | 0 (parallel) |
| Short-lookback observability | **097** | DRAFT (grill-me done) | NOW | 093 (done — archived at `docs/plans/archive/093-*.md`, shipped #53) | 0 (parallel) |
| recap-dg-client forcing adapter (offline) | **081** | DRAFT | NOW | — | 1 |
| recap Gateway operational + training readiness (live) | **082** | DRAFT | BLOCKED | 081; gateway creds/coverage | 1 |
| Nepal data-sources umbrella (IFS/DHM/ERA5-Land) | **047** | DRAFT (stub — **scope STALE, must be revised before READY**, see §4 action) | NOW* | 081/082; DHM+geometry | 1 |
| ERA5-Land reanalysis source | — | *subsumed by 081/082* | NOW | 081 | 1 |
| FI wheel distribution + drop CI exception | **080** | DRAFT (low-pri) | BLOCKED | FI wheel on private index | 1 (tail) |
| recap-dg-client private wheel + drop CI exception | — | (folds into 081/082/080) | BLOCKED | recap wheel on private index | 1 (tail) |
| **DHM observation adapter** | *to-draft (D5-2)* | gap | NOW* | fakes/RivRetrieve; DHM delivery channel | 2 |
| **water_level unit normalization** (cm/m-agl → m) | *to-draft (D5-2, same plan)* | gap | NOW* | removes Plan 101 cm-guard; DHM per-station units | 2 |
| Rating-curve provenance | **035** | READY → implement | NOW | — | 2 |
| Rating-curve h→Q ingestion + reprocessing (Flow 12-A / Flow 5) | *to-draft* | gap | BLOCKED | 035; DHM hQ table + correction-param semantics | 2 |
| Stage 2 QC (2.5–2.7, conversion validation) | *to-draft* | gap | NOW* | h→Q ingestion | 2 |
| Virtual / calculated station support | **015** | DRAFT (enum slice shipped `1a88f92`; v1 flow logic undesigned) | NOW | — | 2 |
| Manual vs automatic station support | **017** | DRAFT | NOW | 015 (behavioral dep — 015 D6 QC-exemption + Flow 4 component-freshness contract, `017:49-58`; enum already shipped `1a88f92`; 015 must merge first) | 2 |
| **Auth / RBAC / audit** foundation | *to-draft (D5-3)* | gap (042 deferral insufficient) | NOW* | HSOL role/ownership values | 3 |
| **Multi-tenant WRITE-isolation** (station-group-scoped promotion/onboarding) | *to-draft (D5-3, same plan)* | gap | NOW* | auth foundation | 3 |
| API key auth + client SDK | **042** | DEFERRED | NOW | folds under auth foundation | 3 |
| Flow 0 Nepal deployment onboarding | *to-draft* | gap | BLOCKED | Nepal AoI + shapefiles + static datasets | 3 |
| Mac-mini staging validation (Stream D) | **046** | IN_PROGRESS | NOW | — | 3 |
| restic encrypted backup + monthly restore rehearsal | **048** | DRAFT (stub) | BLOCKED | 046; hosting location + off-site target + key mgmt | 3 |
| Cloudflare public URL + SSO | **049** | DRAFT | NOW* | 046; hosting decision | 3 |
| Flow 4 pipeline monitoring (full build) | *to-draft* | gap (039 folds in) | NOW | Swiss-testable | 3 |
| **Nepal model training + onboarding** (Flow 6 + Flow 12-B) | *to-draft* | gap | BLOCKED | DHM historical obs archive (§3-4) + gateway forcing coverage (§3-11) + hQ/Snowmapper confirms (§3-3, §3-12) | 4 |
| **DHM live integration + go-live** | *config/wiring* | gate | BLOCKED | ALL of §3 DHM answers **+** trained Nepal artifacts + model assignments (row above) | 4 |
| Flow 3 review/publish/adjust | — | v1.x | — | headless v1.0 (D6) | 5 |
| Dashboard (099 P2, 102, 104 + full) | 099/102/104 | mixed | — | v1.x (D6) | 5 |
| Excel bulletin generation | — | v1.x | — | DHM bulletin template | 5 |
| Bikram Sambat calendar | — | v1.x | — | needed only for bulletin/dashboard | 5 |
| Gateway read-isolation | — | v1.x (if ever) | — | only if multi-tenant read-isolation becomes a requirement | 5 |

---

## 3. Knowledge gaps to resolve with collaborators

Numbered ask-list. Each maps a specific question → owner → what it unblocks. Send as a
batched request per collaborator. Everything marked `BLOCKED` or `NOW*` in §2 traces to
one of these.

### To DHM (hydromet operations)

1. **Real-time observation delivery channel + format + credentials.** How does SAP3 pull DHM real-time gauge data — API, file drop, push, or portal? What format (units, encoding, station identifiers)? — *Unblocks:* final DHM obs-adapter wiring (Wave 2), Flow 0 Nepal onboarding.
2. **Per-station water_level units + reference datums.** Which stations report **m a.s.l.** vs **cm** vs **m above ground**? Surveyed gauge-zero values for a.s.l. stations? — *Unblocks:* the unit-normalization *values* (mechanism already designable, Wave 2) + removes the Plan 101 cm-onboarding guard.
3. **Rating-curve hQ table format + correction-parameter semantics.** In what format are the hQ tables supplied, and **how does the "correction parameter" modify the h→Q conversion**? (Flagged open in `architecture-context.md` §Flow 2 note 2.5.) — *Unblocks:* h→Q ingestion + Stage 2 QC (Wave 2).
4. **Historical observation availability + import format.** How much back-record exists per station, and how is it delivered (for training/hindcast backfill)? — *Unblocks:* Nepal model training readiness.
5. **Danger-level thresholds + alerting responsibility.** Threshold values per station/level, and **confirm DHM runs review/alerting in-house** (validates the D6 headless deferral). — *Unblocks:* alert config + confirms v1.0 scope.
6. **Nepal area-of-interest + basin/catchment shapefiles + (if any) elevation-band polygons.** Geometry for gateway registration + static-attribute extraction. (Elevation bands must arrive **in the uploaded shapefile** — SAP3 does not auto-generate them.) — *Unblocks:* Flow 0 Nepal onboarding + gateway basin registration.

### To HSOL

7. **Physical hosting location of the shared on-prem production box.** DHM premises (Kathmandu) or HSOL-hosted? Who administers it? Network/access model? — *Unblocks:* 048 off-site backup target + restore-rehearsal logistics + 049 access design.
8. **RBAC role model + station-group ownership boundaries.** Concrete roles, who owns east vs west groups, who approves promotions. (Mechanism designable now; these are the *values*.) — *Unblocks:* final auth/RBAC config (Wave 3).
9. **FI wheel on the private index — timeline.** When does `forecastinterface` ship as a versioned wheel to the private hydrosolutions index (owned by Sandro / packaging)? — *Unblocks:* Plan 080 (migrate git-pin → `==0.1.x`, drop the CI wheel-guard exception).
10. **recap-dg-client private wheel/index availability.** Same pattern as FI (private repo). — *Unblocks:* dropping the recap-dg-client CI wheel-guard exception.

### To the recap Data-Gateway dev

11. **Live gateway credentials (`sapphire_dg_api_key`) + coverage/metadata strategy.** Issue the operational key; confirm whether any coverage endpoint exists or the training-readiness gate must self-detect covered-span from the first fetch (current assumption). — *Unblocks:* 082 live smoke + training-readiness coverage gate.
12. **Snowmapper variable + lag confirmation for the eastern group models.** `hs/rof/swe` names are stable; confirm lag step-counts (FI Q9, owed by Sandro). — *Unblocks:* snow-forced model onboarding.

> **Already resolved — do NOT re-ask:** IFS `fc`+`pf` 51-member scheme; EPSG:4326 throughout; feature-`name` addressing (no leading `0`); string per-polygon column keys; single vs multi-polygon gpkg (SAP3 tests it); no gateway tenant isolation (accepted v1 limitation). See `project_recap_dg_client_geometry_lifecycle`.

---

## 4. Draft these plans next (immediate critical path)

**Wave 0 (first — small, stabilizes the base):**
- **A. Reliability gate:** **105** (disk hygiene, grill-me done) → WF1 → WF2, plus the two Swiss-testable correctness bugs **038** (two-phase-insert crash leaves an orphan header — plan 038 lines 43–47: an orphan hindcast header makes `_reconstruct_ensemble` **raise `ValueError` on fetch**, a crash not a silent degradation) and **040** (hindcast dedup unique constraint) → grill-me/WF1 → WF2. **These two are NOT parallel: 040 depends on 038** (`040:5`; `040:79-81` — with 038 in place a duplicate is data-quality-only, without 038 an orphan header hits the same `_reconstruct_ensemble` `ValueError` 038 is fixing). Sequence: run **038** grill-me → WF1 → WF2 **first**; 040's grill-me may overlap 038's WF2 (the designs are independent), but **040 must not land before 038**. README classes 038/040 as category-A "the gate to any v1 prod deploy" (`README.md:24,38-39`). Plan 090 **P1 already shipped** (PR #49); **090 P2 is OPTIONAL** (plan 090 lines 99, 187: "precision refinement (later, optional)", "deferred precision refinement") — it is NOT a Wave-1 gate. Per plan 090 line 188 ("Re-scope P2 into its own plan when P1 lands"), P2 needs a **new scoped plan file** (there is no P2 scope/phase/exit-gate section in `090` today) before it has a concrete WF1 target; do that opportunistically, not as a Wave-0 blocker. *(Companion edit DONE: the plan 090 file header (`090:2-6`) now reads `DONE (P1 … shipped PR #49); P2 pending (OPTIONAL — … re-scoped into its own plan file …)` — file and roadmap agree.)*
- **B. Observability (parallel, non-gating):**
  - **097** → WF1 → WF2. *(grill-me done.)*
  - **103** → **grill-me FIRST** (D2 APILogHandler attachment mechanism + D3 log-level split are still open — plan 103 lines 54, 62–74, 104) → WF1 → WF2. *No subagent runs from a DRAFT plan; the 103 grill-me is a mandatory gate (§5).*

**Then the three parallel designable-now v1 leads (D5):**
1. **081** recap-dg-client forcing adapter (offline) — grill-me → WF1 → WF2. *The Nepal forcing spine; gates 082/047.* **In-scope for the 081 grill-me (do NOT let it stop at "add a new adapter class"):** the two production dispatch points are **hardcoded to the single Swiss source**, so the adapter would be built but never wired in. **Design the dispatch fix in the 081 grill-me** so 082's CI build does not discover the gap first (the *implementation* is owned by a new Plan 082 task — see the "Ownership" note below). The two targeted gaps are —
   - **Flow 1 — source *selection* already works for BASIN_AVERAGE; the staleness check and the adapter constructor do not.** Correcting a prior mis-statement in this bullet: `_select_nwp_source()` (`run_forecast_cycle.py:79-98`) does **NOT** need a full selector overhaul. Its **second pass** (`:95-97`) already returns `ws.nwp_source` for **any** `SpatialRepresentation.BASIN_AVERAGE` source — including a non-ICON source such as `"ifs_ecmwf"` — so a Nepal station onboarded with a gateway binding using `BASIN_AVERAGE` extraction has its source string returned correctly (the first pass at `:92-94` only *prefers* an exact ICON binding when one is present). The two actual gaps are narrower and distinct:
     - **(a) `_check_nwp_grid_staleness` (`:508-546`) hardcodes `_ICON_NWP_SOURCE = "icon_ch2_eps"` (`:74`)** at the latest-cycle fetch (`:520` — `fetch_latest(_ICON_NWP_SOURCE)`). On an IFS-only Nepal deployment (no ICON Zarr store) this returns `None` **every cycle**, logging `nwp.grid_stale` at ERROR and writing a `PipelineHealthStatus.CRITICAL` record with `check_type=PipelineCheckType.NWP_DELIVERY` (`:536-545`) — a **permanent false-CRITICAL NWP-delivery alarm on every Nepal forecast run**, even when IFS delivery is healthy. Fix: make it **multi-source aware** — pass the active NWP source string in as a parameter (the call site at `:1242-1250` can supply `adapter.NWP_SOURCE` once the adapter is wired, or the ICON constant for Swiss deployments) instead of the module-level constant, or skip the ICON-specific check for gateway-only stations.
     - **(b) the production adapter constructor block (`:964-997`) only ever builds `MeteoSwissNwpAdapter`** (guarded by `if adapter is None:` at `:964`, constructed at `:997`). It needs a **second dispatch branch** that constructs a `RecapGatewayAdapter` from config. Without it, the gateway adapter is dead code even when its `nwp_source` is correctly selected.
   - **Flow 6 / training — the hook points are the factory + dispatch, not the config loader:** the ERA5-Land-via-gateway path branches at `ingest_weather_history.py:168-202` (`build_production_reanalysis_adapter` — the factory that returns a hardcoded `MeteoSwissOpenDataReanalysisAdapter`) and its call site `:277-292` (`if adapter is None: adapter = build_production_reanalysis_adapter(...)`) — **not** `_load_reanalysis_stac_config()` at `:99-138` (a pure config reader). Both the factory and the dispatch block need a second-source branch (consistent with the "ERA5-Land is the gateway endpoint, subsumed by 081/082" corollary). **Structural constraint:** this flow consumes a **local** `_ReanalysisAdapter` Protocol (`ingest_weather_history.py:66-78`) that requires a `NWP_SOURCE: str` class attribute **in addition to** `fetch_reanalysis` — the public `WeatherReanalysisSource` Protocol (`protocols/adapters.py:47-55`) has **only** `fetch_reanalysis`, no `NWP_SOURCE`. The `_reanalysis_sources()` filter (`:243-252`) keys station weather-source records on `adapter.NWP_SOURCE`, so a gateway adapter satisfying only `WeatherReanalysisSource` would raise `AttributeError`/match nothing at runtime. The grill-me must **resolve** whether to (a) add `NWP_SOURCE` to the public `WeatherReanalysisSource` Protocol, (b) keep the local Protocol and require the gateway adapter to satisfy it structurally, or (c) split dispatching into a flow-level strategy; and update `_reanalysis_sources()` (or add a dispatcher) to filter on the gateway source string.
   - **Ownership of the dispatch *implementation* (closes the "designed but nobody builds it" gap):** the 081 grill-me only **designs** this dispatch — **081 is offline-only** (`081:65-67`, non-goals: no Swiss-behavior change, no flow-dispatch tasks), so it does **not** implement it. A **new Plan 082 task (`Task 2F — Flow 1/Flow 6 NWP dispatch generalization`)** owns the implementation, distinct from the config-wiring in Task 2A: (a) add the `RecapGatewayAdapter` construction branch to the `if adapter is None:` block at `run_forecast_cycle.py:964-997`; (b) parameterize `_check_nwp_grid_staleness` (`:508-546`) on the active NWP source string instead of `_ICON_NWP_SOURCE`, wiring the call site at `:1242-1250`; (c) the analogous Flow 6 factory/dispatch branch at `ingest_weather_history.py:168-202`/`:277-292` plus the `_reanalysis_sources()` resolution above. **Completion gate for Task 2F:** a test that routes an IFS-bound (`nwp_source="ifs_ecmwf"`, `BASIN_AVERAGE`) station through the full dispatch and asserts it (i) selects the gateway source, (ii) constructs the gateway adapter (not `MeteoSwissNwpAdapter`), and (iii) does **not** emit a `PipelineHealthStatus.CRITICAL` `NWP_DELIVERY` record from an empty ICON store. Add this Task 2F when 082 is drafted; note it in the 081 grill-me as the downstream owner so the design carries a build home. `_select_nwp_source` needs **no** change (its BASIN_AVERAGE second pass already returns the gateway source) — the earlier framing that it must be "replaced/generalized" was wrong.
2. **NEW plan: DHM observation adapter + water_level unit normalization** — grill-me → WF1 → WF2. *Discharges the Plan 101 cm-guard; the DHM-shaped obs path, built against fakes/RivRetrieve.*
3. **NEW plan: auth/RBAC/audit + tenant write-isolation foundation** — grill-me → WF1 → WF2. *Folds in the deferred Plan 042; gates Flow 0 onboarding.*

**Implement in parallel (already READY — no re-draft):**
- **035** rating-curve provenance (genuinely `READY`).

**Designable-now, but needs a re-draft (NOT straight-to-implement):**
- **015** virtual/calculated station support — enum slice shipped (`1a88f92`); the v1 flow logic (`calculated_station_formulas`, `COMPONENT_DERIVED`, Flow 2/5 branching, QC propagation) is undesigned → **grill-me → WF1 → WF2**, and **015 must merge before 017 starts** — a **behavioral** dep, not a type one (017 branches on 015 D6 QC-exemption + Flow 4 component-freshness, `017:49-58`; `GaugingStatus.CALCULATED`/`GAUGED` already exist at `types/enums.py:184-187`).
  - **Companion edit DONE:** the plan **015 frontmatter now reads `status: DRAFT`** (`015:2`, updated 2026-07-08 with an explanatory NOTE at `015:5-8`), consistent with `docs/plans/README.md:70` (DRAFT). No outstanding edit — 015 no longer invites direct-to-WF2 execution, so this roadmap's Wave-2 gating on the 015 grill-me holds.

**Action — revise Plan 047 stub before it progresses (its scope contradicts this roadmap's corollaries):** `047` still lists **Elevation-band NWP extraction** as in-scope *and* as an open question (`047:19-44`), which the §0 corollary explicitly reverses (no elevation-band / gridded extraction for Nepal — forcing arrives as gateway time-series). Before 047 is promoted stub → DRAFT/READY, strip from its scope: (a) elevation-band extraction, (b) ERA5-Land as a standalone CDS adapter (subsumed by 081/082), (c) the DHM obs adapter (moved to the new D5-2 plan); and align its exit gates to the §0 D-decisions. Otherwise 047's first grill-me re-opens already-decided questions.

**Pulled forward as answers arrive (blocked tails):**
- **082** live smoke ← gateway creds (§3-11).
- Rating-curve **h→Q + Stage 2 QC** ← DHM hQ format (§3-3).
- **Flow 0** Nepal onboarding ← Nepal AoI/shapefiles (§3-6).
- **048** off-site backup ← hosting location (§3-7).
- **080** ← FI wheel published (§3-9).

---

## 5. Process (per downstream gap-plan)

Each plan above follows the standing workflow, unchanged by this roadmap:

1. **grill-me** — resolve the design forks with the owner.
2. **WF1 plan-review** (`.claude/workflows/plan-review.js`) — adversarial, code-grounded planner↔reviewer loop; the updated copy adds an architecture/standards-conformance lens. Converge to no blockers/majors.
3. **(optional) independent external review** — for high-stakes plans; it has repeatedly caught what the automated loop missed (`feedback_independent_review_beats_automated_loop`).
4. **WF2 vision-build** — in an **isolated worktree**, **hold-at-PR** (never auto-merge to main). Claude authors locked regression tests; Codex implements.

**FI adherence is a HARD rule** for anything touching a model or the FI adapter (CLAUDE.md §ForecastInterface Adherence): a model that cannot fit the contract → file an FI-repo issue + co-design, never a SAP3-side workaround.

**This roadmap doc** goes straight to `main` (no PR, no version bump — plan-doc-only rule), then through WF1 plan-review to harden the sequencing/dependencies, and — given the payoff of the independent-review gate — **one independent external review is recommended before the wave sequence is locked**.

---

## 6. Open items for plan-review / independent review to challenge

- Is **Flow 4 pipeline monitoring** truly Wave 3, or a hard prod-blocker that should precede DHM go-live (Wave 4)? (Argument for earlier: an unmonitored Nepal prod deploy is risky; argument for Wave 3: v0 basic health checks suffice until real DHM traffic exists.)
- Should the **auth foundation** precede or follow the **DHM obs path** in drafting order? D5 puts obs second, auth third; a reviewer may argue auth is a longer pole and should start first.
- **048** is classed BLOCKED on hosting, but a *local* encrypted-restic + restore-rehearsal mechanism is designable now — split 048 into a NOW mechanism half and a BLOCKED off-site half?
- Does the **Stage 2 QC** design genuinely need DHM's hQ format, or can it be built `NOW*` against a generic rating-table shape with DHM values wiring in later (like the obs adapter)?
- **RivRetrieve** is D2-optional — but is there a cheap, high-value subset (one UK/US gauge with a known rating curve) worth making a *soft* checkpoint inside Wave 2 to de-risk the obs+rating path before DHM?
- **Nepal model training + onboarding** (new Wave-4 §2 row) is BLOCKED on the DHM obs archive — is any of it de-riskable now against Swiss/fake data (e.g. dry-run the Flow 6 + Flow 12-B path end-to-end on the mac-mini) so go-live is a data-swap, not a first-ever run?
