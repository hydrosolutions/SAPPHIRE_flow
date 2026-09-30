---
status: DRAFT
---

# Plan 049 — Cloudflare edge for the flow-map dashboards (Nepal MVP first, BAFU second)

**Date**: rewritten 2026-09-30 (original 2026-04-17; a first rewrite the day before targeted a Mac-mini tunnel and is dropped)
**Related**: Plan 511 (Nepal API host), Plan 516 (real-forecast region-bundle export), Plan 401 (reviewer key), Plan 402 (map read routes),
Plan 404 (rejected-forecasts route), Plan 273 (synthetic Nepal demo bundle, COMPLETE), Plan 111 (BAFU licence
gate), the map project's `docs/NEPAL_MVP_DEPLOYMENT_PLAN.md` and `docs/NEPAL_DEPLOYMENT.md` (its plan of record)
**Scope**: what Cloudflare must do so **Nepal counterparts (DHM) can inspect our forecasts visually** in the
flow-map dashboard, and so a **second, BAFU/Swiss dashboard** can follow. This plan covers access control,
reachability, the data path into each dashboard, and the runbook. It does **not** edit the map repository
(the map session owns that) and it does **not** expose the office-LAN Mac mini (no tunnel).

---

## What exists today (verified 2026-09-30)

- **Two Workers, one per region**, by design (map `NEPAL_DEPLOYMENT.md`): `sapphire-flow-map` (Swiss) and
  `sapphire-flow-map-nepal`. The Nepal Worker is deployed at `sapphire-flow-map-nepal.hsol.workers.dev`
  (manual wrangler deploy, 11 days ago; **0 invocations in the last 24 h**; no custom domain; assets binding
  only). Each Worker holds only its own region's secrets.
- **Access to the dashboard today** is an application-level shared passphrase (HMAC-SHA-256, signed
  session cookie, fail-closed, 250 ms failure delay; `worker/auth.ts`), independent of any email service.
- **The map's plan of record** (`NEPAL_MVP_DEPLOYMENT_PLAN.md`, 2026-09-18): a login-protected six-station
  dashboard for a **late-November 2026 visit to DHM; the map's plan set a hard freeze of 2026-11-13, which the owner moved to the end of November 2026 (2026-09-30: the trip moved; this plan assumes **2026-11-30** until the new date is given — the map's own document still carries the old date and needs the same change).** Data reaches the page through a
  **publisher** (fetch or fixture → validate → immutable `runs/<id>/` bundles in R2 → conditional PUT of
  `current.json`), so the dashboard does **not** need a live backend. Until a real export exists it runs on
  **synthetic fixtures** (Plan 273's bundle is synthetic).
- **A second, live path** exists since 2026-09-29: the Worker's allow-listed proxy to SAPPHIRE Flow
  `/api/v1` (QC rules, station skill; Plans 401/402), which needs a reachable HTTPS API (Plan 511 for Nepal).
- **Cloudflare can now put Access in front of a single Worker**, including its `workers.dev` URL, with no zone
  or domain (Workers changelog, Aug 2026; the **Access** tab is visible on the Worker page). Policies can
  allow specific email addresses or an email domain; one-time-PIN codes are emailed to allowed addresses only
  (10-minute, single-use codes). Worker-level Access does not support WebSockets. Requests from other Workers
  can use Access service-token headers.
- **A subdomain zone under `hydrosolutions.ch` is not available** (Enterprise-only; error 1116 on both the
  dashboard and the API per the map docs). Our DNS is at Hostpoint. Moving `hydrosolutions.ch` to Cloudflare
  would put company mail and website DNS at risk for a pilot and is **not** proposed.
- **The gap this plan cannot close alone:** the map's plan asks the backend for a real
  `flow-map-region-bundle/v3` export (real Nepal forecasts, not fixtures). **No plan in this repository owns it**
  (only mentions in Plan 402 D13 and its hand-off note). Without it, "Nepal counterparts inspect *our*
  forecasts" means synthetic or illustrative content in November. See T3.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **DECIDED (owner, 2026-09-30): the passphrase gate is enough for the Nepal dashboard for now.** Cloudflare Access with named emails stays an option to add later (T1 dry run). | The gate is built, tested and independent of email deliverability into a government mail system; Access adds a second login step and a dependency on OTP mail arriving. |
| D2 | **The BAFU/Swiss dashboard gets Cloudflare Access with named emails from day one.** | BAFU data is licence-limited (Plan 111 G1); named, revocable access is the point, and no publication decision is made by this plan. |
| D3 | **`workers.dev` first; a small dedicated domain on Cloudflare only if DHM's network blocks `workers.dev`.** Not the `hydrosolutions.ch` zone. | Matches the map's §9a criterion (owner opens the URL from inside DHM's network). A ~$10/yr domain has no blast radius. |
| D4 | **The API host (Plan 511) stays behind its reviewer key.** Access-on-Worker protects people reaching the dashboard, not the API origin; a Cloudflare Access service token in front of the API is possible only if that hostname is on a Cloudflare zone, which it is not (DNS at Hostpoint). | The Worker already refuses `http` and holds the key server-side; the key is the gate. |
| D5 | **The mini is never exposed.** The Swiss dashboard reads a **read-only Swiss copy of SAPPHIRE Flow on a cloud server** through the same allow-listed `/api/v1` proxy the Nepal dashboard uses; the mini pushes a filtered export **outward** (no inbound path). | Owner decision 2026-09-29: no tunnel into the office network. The API and the map's proxy exist now (Plans 401/402/404), so this reuses them unchanged; a snapshot-to-R2 design would need new export formats and a new read path in the map. |
| D6 | **Dashboards are read-only demos** (owner, 2026-09-29): every route they read is GET-only; nothing here adds writes. | Unchanged. |

## Tasks

### T0 — Owner decisions
**Outcome**: the open choices are recorded in this plan with a date.
- **Nepal gate** (D1): **decided 2026-09-30 — passphrase only for now.**
- **BAFU dashboard data path** (T4): read-only cloud copy (recommended, D5) or the snapshot push alternative.
- **Who may see the BAFU dashboard** (named people; Plan 111 G1 record) and the DHM addresses to allow, when known.

**In / Out**: this file only.
**Verification**: answers written under "Decisions recorded".
**Pre-change**: N/A (documentation).

### T1 — Access dry run on the Nepal Worker
**Outcome**: we know whether Access-on-Worker works for an external person before relying on it.
- In the Cloudflare dashboard (Zero Trust must be enabled on the account): enable Access on
  `sapphire-flow-map-nepal`, policy = one named external test email, one-time PIN. Confirm the OTP arrives,
  the session length, that an address not on the list gets no code, and that the passphrase gate still works
  behind it. Record what happens to `scripts/verify_nepal_gate.sh` (unauthenticated requests will now be
  redirected by Access before the Worker answers 401); the map repo must adapt the script, which is a hand-off,
  not done here.
- Confirm the free-plan seat limit and price in the account's own billing page (I could not verify it from
  documentation).

**In / Out**: Cloudflare configuration and a record here. Out: map-repo code.
**Verification**: a dated record of each check; the disable switch works and restores the passphrase-only state.
**Pre-change**: N/A (configuration). Baseline: no Access policy on the Worker.

### T2 — Reachability from DHM
**Outcome**: known before the visit whether `workers.dev` opens from DHM's network.
- The owner (or a DHM contact) opens the URL from DHM's network and from a phone tether; record the result.
- If blocked: register a small domain on Cloudflare, add it as a Worker custom domain (a configuration change; the
  map keeps no hostname in code and its cookie has no `Domain`), and re-run the map's gate verification.

**In / Out**: an owner action and a record.
**Verification**: dated results; the chosen hostname listed.
**Pre-change**: N/A. Baseline: never tested from DHM.

### T3 — Real Nepal forecasts into the dashboard
**Outcome**: a named owner and plan exist for the real export, and the November data path is decided.
- Record which content the November visit will show: fixtures (today), or real bundles from the backend once a
  region-bundle export exists. The publisher, R2 buckets and the bundle schema are map-side milestones (M5); the
  backend's export route is unowned.
- **The backend export is Plan 516** (owner-assigned 2026-09-30), from the map's
  `SAPPHIRE_FLOW_REGION_BUNDLE_EXPORT_PROMPT.md`. It must not depend on Nepal onboarding plans that will not
  converge by November, matching the map's own constraint.
- Once Plan 511's host exists, set the Nepal Worker's `SAPPHIRE_API_BASE_URL` and secret `SAPPHIRE_API_TOKEN`
  (the live QC/skill panels); this is independent of the publisher path.

**In / Out**: a decision record and one new plan request. Out: the export implementation, map code.
**Verification**: Plan 516's status is recorded here, with the content the visit will show.
**Pre-change**: N/A. Baseline: Plan 516 is a DRAFT (2026-09-30).

### T4 — BAFU / Swiss dashboard data path (after Nepal)
**Outcome**: a chosen way for Swiss forecasts to reach the Swiss Worker without opening the office network.
- **Recommended (D5): a read-only Swiss copy on a cloud server** — API and database only, no workers and no
  ingest — sized about 4 vCPU / 8 GB / 100 GB (estimate), reusing Plan 511's host recipe, and kept separate
  from the Nepal host (licences differ). The mini uploads a **filtered export** (the few GB a map read needs,
  not the 104 GB database or the grid archive) outward to R2 or by SSH push; the cloud host restores it.
  Freshness is the push cadence; live collectors on the cloud copy are a later option (LINDAS rate limits
  forbid two collectors polling at once). The Swiss Worker points at it with the same variables the Nepal
  Worker uses.
- **Alternative: snapshot push to R2**, no API host: the map reads bundles from R2. Rejected as the default
  because the Swiss dashboard now reads QC and skill live through the API proxy, so it would need new
  QC/skill export formats and a new read path in the map.
- Either way: Access with named emails (D2), and no BAFU publication decision until Plan 111 G1 is settled.

**In / Out**: the choice, then its own plan. Out: implementation here.
**Verification**: the choice recorded with its reason; a follow-on plan ID.
**Pre-change**: N/A.

### T5 — Runbook
**Outcome**: `docs/deployment/cloudflare-dashboards-runbook.md`: who has access to each Worker, how to add or
remove a named person, how to rotate the reviewer key and the passphrase, how to take a dashboard offline, and
what the visit-day fallback is.
**In / Out**: docs only.
**Verification**: inspection against T1–T4; `uv run pre-commit run --all-files` clean.
**Pre-change**: N/A.

## Exit gates

1. Nepal: a named DHM person can open the dashboard through the decided gate (T1), from a network that
   allows it (T2), before **the end of November 2026 (assumed 2026-11-30; the freeze date moved with the trip)**.
2. The Nepal Worker reads its live routes from the Plan 511 host with its own key, or the record states why not.
3. Plan 516 has landed and the Nepal dashboard shows its output, or the owner has recorded that the visit shows fixtures.
4. The BAFU dashboard's access rule and data path are chosen (T4); nothing is published before Plan 111 G1.
5. The runbook is committed.

## Not in this plan

A tunnel or any inbound path to the Mac mini; moving `hydrosolutions.ch` DNS; the map repository's code (the
publisher, R2 read path, gate changes); the backend export route's implementation; individual hydromet logins in
the backend (Plan 341); production.

## Risks

| Risk | Mitigation |
|---|---|
| `workers.dev` is blocked on DHM's network | T2 tests early; a small dedicated domain is the fallback. |
| OTP emails to DHM addresses are filtered | D1 keeps the passphrase as the primary gate; T1 tests delivery; DHM is asked to allow the Cloudflare sender. |
| The demo shows synthetic content while claiming "our forecasts" | T3 forces the decision; the map's page text already labels illustrative data. |
| Access and the passphrase double-gate confuse visitors | D1 makes the double gate opt-in after the dry run. |
| Free-plan seat cap or pricing changes | T1 records the account's own limit; Access can be switched off without touching the dashboard. |
| The visit is late November; the freeze is now assumed 2026-11-30 | T2 is quick and owner-run; T3 (Plan 516) is the long pole. |

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T0"] },
    { "id": "phase-2", "tasks": ["T1", "T2", "T3"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T4"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T5"], "depends_on": ["phase-3"] }
  ]
}
```
