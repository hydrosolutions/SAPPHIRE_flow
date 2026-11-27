---
status: V1 COMPLETE_LOCAL; V2 preparation READY; V3 execution remains gated
created: 2026-11-27
owner: deployment orchestrator
parent: 2026-11-22-fresh-deployment-comparison.md
---

# Complete one small deployment

## Outcome and owner decisions

Complete a fresh temporary installation with one station and a small existing
model, then leave a reusable administrator procedure. The owner approved this
scope on 27 November: select the latest published release by default, stop manual
requalification for every release, simplify the comparison, defer the separate
climatology-job question, and prefer a simple climatology or regression model.

This plan replaces the full-cohort inventory and historical reconstruction as
prerequisites for this small test. The received full inventory remains reference
evidence; it is not a list of forecasts that must all run. Existing helper limits,
refusals and incomplete qualifications remain truthful historical records. Do not
raise their limits or relabel them successful. The source deployment stays unchanged.

Use the already approved separate temporary host with the current data provider
for private agency data. Another provider may run the public synthetic scenario;
that does not authorize transferring private data there. Destroy all test-owned
cloud resources after success or failure. No production cutover is included.

## Existing capabilities and remaining work

| Existing and reusable | Remaining work in this plan |
| --- | --- |
| Private profiles, ordered overlays, safe preview/provenance and manager-neutral credential files | One documented route through the supported commands; actual private inputs stay outside Git |
| Canonical release identity in `refs/heads/release-state`, delivery receipts and source-CI reuse | A read-only latest-release selector; select once and retain the exact result |
| Credential-free [hello world](../deployment/hello-world.md) with a real worker and authenticated API | Reuse its diagnostics; do not rebuild this smoke test or call it operational ingestion proof |
| [Paused installer](../deployment/profile-installation.md), and the separately tested broader private paused-startup recipe | Select the applicable route explicitly; the two-job installer still cannot commission the broader recipe |
| Station/observation commands, normal model onboarding, worker forecasts and API readers | A bounded walkthrough using one small model, plus separate scheduling evidence |
| Received source inventory and earlier reconstruction experiments | Compare the selected inputs and one forecast; defer full restoration and multi-model parity |

## Release policy

Default to the latest canonical **published** release, resolved through the existing
release-state authority. Do not use the unreleased target on main or a floating
Docker `latest` tag. Allow an explicit published-version override for reproduction.

Resolve once before preparing the host; retain the version, source and delivery
binding. Use a clean matching checkout and immutable delivered images throughout
that run. If the selected delivery is unavailable, report it and use the existing
delivery procedure; never silently select an older version or publish a new one.
A later publication does not retarget an installation or invalidate completed work.
Existing release-build race checks remain intact: if delivery itself refuses, resolve
that delivery failure rather than weakening its publisher checks.

Reuse successful source CI and normal delivery checks. Installation still checks
the supplied image/source binding and transferred bytes automatically. These are
routine input checks, not a new manual qualification milestone or review for each
version. Repeat a deployment test when deployment behavior, a relevant dependency,
host assumptions or a failure warrants it. Record which release was actually tested;
do not claim a historical host result proves an untested release.

## Small forecast scenario

Prefer the existing `linear_regression_daily` model: seven daily lookback steps,
five forecast days, no weather or static model inputs. Its training implementation
accepts at least eight observations. Start with the existing 90-day synthetic
discharge fixture for the walkthrough; this is a candidate end-to-end dataset,
not yet proof that every onboarding gate can complete with 90 days.

Use normal station creation, observation load/QC, model training and promotion.
The station-promotion command currently also checks basin, group/model and weather
bindings; absence of weather inputs in the model does not waive those command
requirements. Confirm the complete route in V2 before cloud execution. Keep all
required metadata explicit. Do not insert operational status/artifacts directly,
disable configured skill gates, or forge external-artifact configuration hashes.
The native climatology model requires a year of training data and cannot currently
use the external-artifact importer unchanged; it is not the default shortcut.

V2 runs in isolated local Compose with synthetic data only; use the existing CI
qualification runner if the local machine cannot run the supported Docker setup.
Record whether promotion's weather checks need bindings alone or actual weather
data, and how the ACTIVE group assignment and basin are supplied normally. If the
90-day fixture lacks required history/sample coverage, extend the same deterministic
fixture method up to 366 daily values and record the reason. Do not tune inputs or
lower thresholds merely to obtain a passing scientific skill verdict. A rejection is an
explicit incomplete result to resolve with the onboarding owner before V3.

For the private comparison, use one explicitly selected station and a bounded
export of its measured discharge, through existing load/QC commands. Compare
timestamps, units, values, load/QC counts and declared daily-grid interpretation
with that export. This is a fresh import, not preservation of old audit chronology,
rating lineage or database identities. Do not silently derive daily measurements
using a different hydrological convention. Missing suitable data is reported, not
replaced with synthetic data under a real-data success label.

Freeze the training/input windows, forecast issue time, seed and numerical tolerance
before the run. Compare the normal worker's result with a direct reference prediction
from the same trained artifact and inputs, and compare stored output with the
authenticated API result. Verify station/model/artifact identity, all expected
valid times and finite quantiles; a completed Prefect flow alone is insufficient.
This proves wiring and reproducibility, not predictive skill or equivalence to a
different model running on the source deployment. Scientific acceptance and the
larger current-model comparison remain separate work.

## Milestones for one worker per pass

| Milestone | Concrete scope and paths | Acceptance and meaningful tests | Dependencies |
| --- | --- | --- | --- |
| V1 — select a release once | Extend `tools/release_identity.py` with a read-only selector using its existing state/tag validation. Cover `tests/unit/tools/test_release_identity.py` and `tests/integration/tools/test_release_identity_git.py`. Document the selector in `docs/standards/cicd.md` and link from the quick-start. | Latest and explicit published selection; missing/malformed/unpublished identity refused; no publish/push/build side effects. Selected binding remains unchanged after a newer release appears. Document the direct mapping from selector output and existing delivery receipts to the installer's current explicit inputs; do not ask administrators to infer it. Matching source/delivery checks stay in their existing tools. No installer schema change unless demonstrably necessary. | This plan reviewed and V1 READY |
| V2 — make the small recipe executable | Private profile, Compose recipe and runbook; reuse `cli/create_station.py`, `cli/load_station_observations.py`, `flows/onboard_model.py`, `cli/promote_station.py` and existing worker/API paths. Use `tests/fixtures/synthetic_station/discharge_daily.csv`. Add administrator instructions to `docs/deployment/operational-comparison.md`; link existing schema and secrets references. | Walk through one synthetic station using supported commands and normal gates; record actual minimum history and metadata. Exact expected forecast and API comparison defined. No new model, second registrar, cloud controller or generic inventory framework. If an existing command blocks this route, isolate one concrete fix with the onboarding owner and review it before proceeding; do not bypass the gate. | V1; existing onboarding behavior. V2 is preparation until its walkthrough succeeds |
| V3 — fresh host, forecast and scheduling | Existing delivery/install or private paused-startup route, then explicit temporary commissioning. Private execution record contains selected release, host/resources, the one real station and discharge source, input hashes, required jobs and finite run limits. | Fresh DB/Prefect/local credentials; services and worker children load intended config. Paused registration verified before workers execute. One normal forecast stored and served over authenticated host-loopback API; unauthenticated request rejected. Input and forecast comparisons pass. Required weather scheduling independently meets the contract below. If real data is unsuitable, record the private comparison incomplete; any synthetic run is separately labelled and cannot replace it. No writes or job changes on the source server. | V2 walkthrough; reviewed concrete execution record with data/provider access, cost/time limits and cleanup commands; orchestrator admission |
| V4 — cleanup and administrator handoff | Provider-side resource readback; private result record; update quick-start and operational-comparison guide, keeping history here. | Remove owned instances, volumes, snapshots, addresses and temporary access rules/keys. Retain private evidence, never credentials. Report each test and cleanup separately. Instructions work with the next published version without replacing embedded version numbers. List remaining scientific/provider inputs for a new agency. | V3 attempted; cleanup runs even after failure |

```json
{
  "milestones": [
    {"id": "V1", "depends_on": []},
    {"id": "V2", "depends_on": ["V1"]},
    {"id": "V3", "depends_on": ["V2"]},
    {"id": "V4", "depends_on": ["V3"], "run_after_failure": true}
  ]
}
```

## Scheduling acceptance: prevent the original omission

Maintain one short private job list with required, manual-only or excluded status
and reasons. Include observation acquisition and recurring weather history where
the intended deployment requires them. The simple regression forecast cannot
prove either is scheduled. Keep the full source-job inventory as a cross-check for
omissions, without importing all its models or local variants.

For each required recurring job, read back its parameters, timezone, schedule,
deployment/pool/queue state and worker configuration from fresh Prefect. Verify a
future due run, one scheduler-created run picked up by the intended worker, and
the expected persisted coverage/freshness. A manual invocation or empty/no-op run
does not pass this check. Distinguish fixed-window import from recurring refresh;
use the existing [registrar contract](../standards/orchestration.md#nepal-operational-era5-refresh).

The private record fixes the single weather scope, bounded lookback/provider
request allowance, wait deadline and stop action before execution. A short temporary
test schedule may prove scheduler-to-worker execution; separately restore and read
back the desired schedule, and label which schedule actually ran. Pause the target
jobs after the bounded test. Keep all unrelated jobs inactive and external
notifications disabled. If provider access or usable data is unavailable, report
the forecast result separately and scheduling as incomplete; do not claim overall
operational deployment success.

## Documentation, review and exit boundary

User instructions belong in the existing quick-start and deployment guides;
configuration details remain linked to `profiles.md`, the orchestration reference
and `secrets-bootstrap.md`. Actual agency values stay private. Local display time,
hydrological boundaries and training-time conventions remain separate inputs.
No secret-manager SDK or authentication-system integration is needed here.

One independent Claude review covers this nontrivial plan. Keep its full report
private; record only outcome and unresolved findings here. The orchestrator sets
READY for the admitted phases after review. One relevant independent patch review
and full final-source CI precede each nontrivial code PR's owner merge. Reuse
unchanged tests; add coverage only for a new selector or a demonstrated wiring gap.
V3 requires its concrete execution record, not another per-release plan. A failed
gate leads to a bounded fix or an explicit incomplete result, not an expanded
full-inventory project. The owner retains PR merge and production authority.

Plan approval does not mean the fresh deployment has run. Completion requires the
V3 forecast and required-ingestion evidence, V4 verified cleanup, and usable
instructions. Full historical reconstruction, operational climatology-job cleanup,
large-model parity, public access, backups qualification and a second agency's
scientific choices are outside this increment.

## Independent review disposition

One independent Claude review on 27 November returned READY for V1 and V2, with
no blockers. Clarifications now cover selector-to-installer mapping, the isolated
walkthrough location, promotion prerequisites, bounded history extension without
weakening skill gates, and explicit real-versus-synthetic outcomes. The quick-start
states that automatic selection is not implemented yet. Existing host requirements
and job-exclusion reasons remain explicit. Full report and finding dispositions
stay private. The orchestrator marks V1 implementation and V2 preparation READY;
V3 remains gated on the successful walkthrough and concrete execution record.

## V1 local implementation — 27 November

`tools/release_identity.py resolve --output PATH` now saves the latest validated
published identity, with `--version X.Y.Z` for an explicit canonical release.
Existing files and concurrent writers are refused. A later publication neither
rewrites the selection nor prevents explicitly selecting an older canonical release.
The command reuses the existing remote snapshot/state/tag checks and does not
publish, build, check out source or start services. The CI/CD reference owns the
selection-to-installation mapping and distinguishes selection from delivery proof.

All 115 focused release-identity unit/Git integration checks passed, including the
new selection cases. Ruff and changed-module Pyright passed. One independent Claude
patch review returned READY with no blockers; its two context questions were
confirmed by the existing parser/CLI and the passing tests. Full reports remain
private. V1 is locally complete; final CI and owner merge remain required. No
release was selected for a live installation and no deployment occurred in this pass.
