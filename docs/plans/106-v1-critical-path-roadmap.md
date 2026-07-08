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
| D5 | **First NEW v1 plans to draft (post-Wave-0):** (1) **081** recap forcing adapter, (2) a **new DHM-observation + water_level-unit-normalization** plan, (3) a **new auth/RBAC/audit + tenant-write-isolation** plan. The READY plans **015** (virtual stations) and **035** (rating-curve provenance) go **straight to implement** (no re-draft). | These three are independent, fully designable-now tracks that parallelize; they converge only at Flow 0 Nepal onboarding and at live DHM integration. |
| D6 | **v1.0 is HEADLESS.** Flow 3 (review/publish/adjust), the dashboard, Excel bulletin, and the Bikram Sambat calendar are **deferred to v1.x**. v1.0 = gateway forcing → DHM obs → forecast → REST API → webhook alerts; DHM consumes via the API and runs its own review/alerting in-house. | Per `project_nepal_alerting_scope` (DHM may handle alerting in-house; dashboard targets v2). Shrinks the Oct-2026 critical path to the data+model+deploy spine. |

**Corollaries baked in (not re-litigated):**
- **No elevation-band / gridded-NWP extraction for Nepal.** Forcing arrives as basin/band **time-series** from the Data-Gateway API (`recap-dg-client`). ICON-mesh extraction (Plan 087) is Swiss/v0-only.
- **ERA5-Land is NOT a separate CDS adapter.** It is the gateway `era5_land_reanalysis` endpoint, covered by 081/082. README gap #4 is **subsumed**, not a standalone build.
- **IFS ensemble = 1 `fc`(HRES, member_id=0) + 50 `pf` = 51 members** (ECMWF discontinued `cf`; resolved, no gateway ask). Per `project_recap_ifs_fc_hres_member0`.
- **recap-dg-client distribution mirrors FI:** git-pin + scoped CI wheel-guard exception now → private-index wheel later (it is a **private** repo → CI/Docker need clone auth). Folds into 081/082/080.

---

## 1. Locked wave sequence

```
Wave 0  Stabilize the Swiss dev base            [immediate, no external deps]
Wave 1  Nepal forcing spine                     [designable now; live smoke blocked on gateway creds]
Wave 2  Nepal observation & rating-curve path   [mechanism designable now; DHM specifics blocked]
Wave 3  Multi-tenant, auth & deployment hardening [auth designable now; host/off-site blocked]
─────── DHM ACCESS LANDS ───────                [gate: real DHM creds + data + shapefiles arrive]
Wave 4  Nepal integration + go-live             [blocked until access; = adapter-swap + config]
Wave 5  v1.x deferred (post-v1.0)               [forecaster-facing + niceties]
```

Waves 1, 2, and 3 are **three independent designable-now tracks** — with review
bandwidth they run **in parallel** after Wave 0. They are drawn as an ordered list only
to show drafting priority (D5), not a hard serial dependency. They **converge** at two
points: **Flow 0 Nepal onboarding** (needs forcing + obs + auth all present) and the
**DHM-access gate** (Wave 4).

### Hard-prerequisite chains (these ARE serial)

```
FI published to private index ─────────────► 080 (drop git-pin + CI wheel-guard exception)
081 (offline adapter) ──► 082 (live smoke/coverage) ──► 047 (Nepal data-sources umbrella)
035 (provenance, READY) ──► rating-curve h→Q ingestion ──► Stage 2 QC (2.5–2.7)
water_level unit normalization ──► removes the Plan 101 cm-onboarding guard
046 (staging validation, IN_PROGRESS) ──► 048 (restic + restore rehearsal) ──► 049 (Cloudflare public URL)
auth/RBAC foundation ──► tenant write-isolation ──► Flow 0 Nepal onboarding (owner-scoped)
DHM obs adapter + unit-norm ──► Nepal obs ingest ──► rating-curve derivation (Stage 2 needs h→Q)
```

### What parallelizes

- **Wave 0** gates only platform stability; its observability items (097, 103) run alongside its reliability items (105, 090).
- **Forcing (Wave 1)**, **Obs/rating (Wave 2)**, and **Auth (Wave 3)** are mutually independent — draft/build concurrently to the extent review bandwidth allows.
- **015** and **035** (both READY) can be implemented at any time in parallel with drafting the Wave-1/2/3 leads.
- Live-dependent tails — **082** live smoke, **Flow 0** Nepal specifics, **048** off-site — block on external answers and are pulled forward only as those answers arrive.

---

## 2. Every remaining piece — classification table

**Legend — Class:** `NOW` = designable & buildable now (against Swiss data / fakes /
RivRetrieve); `NOW*` = *mechanism* designable now, DHM/collaborator *values* wire in as
config later; `BLOCKED` = core design needs an external answer first (see §3).

| Item | Plan | Status | Class | Depends on | Wave |
|------|------|--------|-------|------------|------|
| Disk hygiene / NWP scratch cleanup | **105** | DRAFT (grill-me done) | NOW | — | 0 |
| NWP incomplete-cycle / horizon coverage (P2) | **090** | P1 shipped; P2 pending | NOW | — | 0 |
| Prefect worker observability & home | **103** | DRAFT (grill-me done) | NOW | supersedes 062 | 0 (parallel) |
| Short-lookback observability | **097** | DRAFT (grill-me done) | NOW | 093 (done) | 0 (parallel) |
| recap-dg-client forcing adapter (offline) | **081** | DRAFT | NOW | — | 1 |
| recap Gateway operational + training readiness (live) | **082** | DRAFT | BLOCKED | 081; gateway creds/coverage | 1 |
| Nepal data-sources umbrella (IFS/DHM/ERA5-Land) | **047** | DRAFT (stub) | NOW* | 081/082; DHM+geometry | 1 |
| ERA5-Land reanalysis source | — | *subsumed by 081/082* | NOW | 081 | 1 |
| FI wheel distribution + drop CI exception | **080** | DRAFT (low-pri) | BLOCKED | FI wheel on private index | 1 (tail) |
| recap-dg-client private wheel + drop CI exception | — | (folds into 081/082/080) | BLOCKED | recap wheel on private index | 1 (tail) |
| **DHM observation adapter** | *to-draft (D5-2)* | gap | NOW* | fakes/RivRetrieve; DHM delivery channel | 2 |
| **water_level unit normalization** (cm/m-agl → m) | *to-draft (D5-2, same plan)* | gap | NOW* | removes Plan 101 cm-guard; DHM per-station units | 2 |
| Rating-curve provenance | **035** | READY → implement | NOW | — | 2 |
| Rating-curve h→Q ingestion + reprocessing (Flow 12-A / Flow 5) | *to-draft* | gap | BLOCKED | 035; DHM hQ table + correction-param semantics | 2 |
| Stage 2 QC (2.5–2.7, conversion validation) | *to-draft* | gap | NOW* | h→Q ingestion | 2 |
| Virtual / calculated station support | **015** | READY → implement | NOW | — | 2 |
| Manual vs automatic station support | **017** | DRAFT | NOW | 015 (orthogonal) | 2 |
| **Auth / RBAC / audit** foundation | *to-draft (D5-3)* | gap (042 deferral insufficient) | NOW* | HSOL role/ownership values | 3 |
| **Multi-tenant WRITE-isolation** (station-group-scoped promotion/onboarding) | *to-draft (D5-3, same plan)* | gap | NOW* | auth foundation | 3 |
| API key auth + client SDK | **042** | DEFERRED | NOW | folds under auth foundation | 3 |
| Flow 0 Nepal deployment onboarding | *to-draft* | gap | BLOCKED | Nepal AoI + shapefiles + static datasets | 3 |
| Mac-mini staging validation (Stream D) | **046** | IN_PROGRESS | NOW | — | 3 |
| restic encrypted backup + monthly restore rehearsal | **048** | DRAFT (stub) | BLOCKED | 046; hosting location + off-site target + key mgmt | 3 |
| Cloudflare public URL + SSO | **049** | DRAFT | NOW* | 046; hosting decision | 3 |
| Flow 4 pipeline monitoring (full build) | *to-draft* | gap (039 folds in) | NOW | Swiss-testable | 3 |
| **DHM live integration + go-live** | *config/wiring* | gate | BLOCKED | ALL of §3 DHM answers | 4 |
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
- **A.** Grill-me + finalize **090** P2 (horizon coverage) and **105** (disk hygiene) → WF1 → WF2. *(Reliability gate.)*
- **B.** In parallel, non-gating: **097** and **103** → WF1 → WF2. *(Observability.)*

**Then the three parallel designable-now v1 leads (D5):**
1. **081** recap-dg-client forcing adapter (offline) — grill-me → WF1 → WF2. *The Nepal forcing spine; gates 082/047.*
2. **NEW plan: DHM observation adapter + water_level unit normalization** — grill-me → WF1 → WF2. *Discharges the Plan 101 cm-guard; the DHM-shaped obs path, built against fakes/RivRetrieve.*
3. **NEW plan: auth/RBAC/audit + tenant write-isolation foundation** — grill-me → WF1 → WF2. *Folds in the deferred Plan 042; gates Flow 0 onboarding.*

**Implement in parallel (already READY — no re-draft):**
- **015** virtual/calculated station support.
- **035** rating-curve provenance.

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
```
