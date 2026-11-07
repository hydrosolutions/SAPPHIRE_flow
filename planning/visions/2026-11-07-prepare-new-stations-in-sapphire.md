# Prepare new stations in SAPPHIRE

Program: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/450
Effort: https://github.com/hydrosolutions/SAPPHIRE_flow/issues/453

## Accepted outcome and delivery order

Provide a facilitator-operated, command-line preparation and review process that
brings new stations into SAPPHIRE with consistent station records, approved basins
and static attributes, exact data-source bindings, and honestly qualified inputs.
It must accommodate stations with reviewed historical observations, operational
observations, both, or no observations. Each station retains a clear outcome and
next action; successful requests or database writes are not readiness certificates.

**Prioritize Nepal/NHS onboarding. Prove that route first.** Reuse the Nepal file
and API formats already used in this project. Then verify BAFU onboarding and
address demonstrated gaps in its existing routes. BAFU work must not delay the
initial Nepal proof or substitute for it. Both are within the intended preparation
capability; neither implies support for every file or API from those countries.

Other hydromets, including an upcoming Kazakhstan deployment, will need the same
onboarding process with their own adapters. Keep station preparation separate from
provider-specific parsing and fixed country rosters. Do not implement Kazakhstan
adapters, deployment, speculative formats, or a general integration framework now.

The operational rehearsal destination is the **Infomaniak evaluation server**.
There is no production deployment in this scope. Reuse the existing Nepal-team
access boundary and distinguish research stations. A research label is not
permission to create a new organization, widen access, or change tenant ownership.

## Position in the staged journey

The accepted overarching vision is
`planning/visions/2026-11-05-staged-station-onboarding-toward-a-production-pipeline.md`.
Basin approval and Gateway preparation supply this stage's inputs. Gateway source
coverage does not prove that SAPPHIRE has persisted supported, usable model inputs.
Model training, performance review and forecast activation belong to the following
milestone, not this one. Retirement/reactivation belongs to the later lifecycle
milestone.

At authoring, the basin stage is landed and Gateway implementation PR469 is merged,
but its procedure/live acceptance remains outstanding. Early discovery and permitted
implementation investigation do not make that dependency delivered. Do not claim
this milestone delivered before its prerequisite is landed and its own acceptance
is established. Publication of this vision authorizes no live writes or deployment.

## Station, package and source admission

Start from the immutable approved-only handover and confirmed Gateway evidence.
Preserve station codes as text, including research `00001`, and distinguish station
code/network, static-data gauge identity, Gateway HRU/polygon identity and internal
station identity. Do not infer DHM membership, tenant, a gauged classification or
an observation feed from a research code. Resolve metadata and ownership through
an authorized station-registration path; never use Swiss or six-station Nepal
defaults as a shortcut.

Reuse station, basin/static import and binding services behind the appropriate
admission checks. Preserve package fingerprints, immutable versions, approval
applicability and provenance. Use established package/storage validation without
adding a second basin delineation or hydrologist geometry-approval exercise.
A package import alone is not approval, tenant admission or selected-model readiness.

Preflight existing records and bindings for conflicts. Matching retries must retain
identities and completed work. A conflicting tenant, mapping, package version or
source must be visible and held rather than overwritten. Reconcile against current
state: an old successful receipt does not authorize rolling back a newer package,
and a package no-op does not prove its current bindings are still correct.

Handle partial batches with explicit per-station results. Preserve completed work
across interruption and reconcile before retrying; do not present Gateway work,
file admission and database changes as one atomic transaction. Keep unrelated
stations, observations, assignments and running services unchanged.

## Observation routes, precedence and gaps

Support both supplied discharge and water-level observations converted to discharge
using applicable rating tables. A station may combine historical files with an
operational API, use one API for both periods, or need multiple supported APIs.
Record which provider/file supplies which measurement and period, with exact station
bindings, provenance, units, time support, timezone and water-level datum/reference
where relevant. Verify these meanings before combining or converting data. Do not
silently reinterpret an unfamiliar format or promise history that an API cannot
return.

Reuse project-supported adapters and parsers; identify actual missing capabilities
rather than assuming country support is complete. Retain original observations and
file/delivery lineage. Supplied discharge remains distinct from rating-derived
discharge; never substitute water level for discharge or convert an already supplied
discharge series as if it were level.

The default precedence is **department-reviewed historical data over unchecked
operational data**. File transport is not proof of review. During onboarding the
reviewer approves or changes precedence by measurement and applicable period.
Preserve alternative source evidence and expose disagreements. Arrival order must
not silently determine authority, and conflicting sources must not be averaged
merely to obtain a single value. Unresolved source conflicts hold the affected data.

Gap filling from another observation source requires an approved fallback. The
hydrologist may instead retain gaps. Keep fallback values identified by their real
source, review status and QC; never relabel them as department-reviewed history or
overwrite reviewed historical values. No implicit interpolation, invented values
or automatic source switching. Observation fallback policy does not relax the
separate weather/product contracts. Retained gaps may limit readiness according
to the selected model's actual requirements.

## Rating tables, conversion and quality review

Include rating-table admission and water-level-to-discharge conversion in general
station onboarding, not as a Trishuli-only afterthought. Bind each table to the
correct station and its applicable dates, units and level reference. Preserve its
original delivery, version and the linkage from input level to derived discharge.
Reuse established conversion and QC policies; this is not authority to introduce
new extrapolation, datum adjustment, calibration or scientific acceptance rules.
Any established provisional pathway must remain explicit and distinct from fully
qualified observations, with its existing restrictions and evidence intact.

Run applicable automated QC and preserve the repository distinctions between
passed, raw/unassessed, suspect, failed, missing and unchecked data in the report.
No flags is not necessarily QC-passed. Preserve QC rule versions, decisions and
observation lineage. Human approval must not erase flags,
make incompatible data valid, or bypass required integrity checks.

An initial reviewer/tester approval covers observation-source admission, rating
and conversion evidence where applicable, QC results, precedence and any fallback.
Produce a **private saved report** with plots and tables, source/version references,
coverage gaps, QC findings and conversion results. Approval or hold is an explicit
command-line action tied to the exact prepared result and its material input/rule
versions. Retain reviewer attribution, time and reason where applicable. Changed
material inputs or rules require renewed checks and review; an earlier approval
must not silently authorize a different result. Routine new observations may be
processed under the approved source/conversion/QC setup without requiring manual
approval of every sample, while failures remain visible.

For this evaluation stage a reviewer/tester may approve. Future observation-readiness
permission belongs to designated senior NHS hydrologists using the architecture's
specialized authorization design. Do not invent a replacement role hierarchy or
new login system. Distinguish attributed reviewer identity from authenticated
identity and from the principal authorized to perform writes. In particular,
existing GET-only reviewer/dashboard credentials must not acquire write access
implicitly. Preserve current tenant and restricted-data safeguards for CLI work
and reports.

Keep corrections minimal and traceable: accept corrected source inputs or replacement
rating versions through the supported route, show their effect on readiness, rerun
checks and renew applicable approval. Preserve originals and prior decisions. No
silent in-place observation edits or bulk conversion of failed QC into passed QC.
An interactive QC editor or general rating-management interface is not required.

## Collection, weather preparation and model-input readiness

Approved ongoing observation ingestion may start **before forecast activation**.
Provide a reliable separation between permission to collect/process observations
and permission to participate in forecasts. Do not promote a station to operational
forecast status, add active model assignments, or invoke training merely to get its
observations ingested. Enabling ingestion remains an explicit, scoped operation
after the relevant source/review requirements are met, not a side effect of creating
a station row. Existing station ingestion and forecast behavior must be preserved.

Persist supported historical weather through the existing reanalysis route. Carry
forward the operator-confirmed Gateway scope; history periods remain explicit and
may be revised only through the applicable scope/confirmation process. Do not infer
training periods from a model lookback or old artifact split dates. Keep ERA5-only
historical inputs separate from the newly supported combined operational weather
route, its cache/evidence and any forecast-fill rows. Preserve existing unit,
calendar and temporal-support semantics. This stage does not authorize changing a
shared runtime weather selection or triggering shared Gateway backfills.

Read back persisted observations, forcing, statics and bindings to assess actual
usable support against the selected model declaration and intended input period.
Counts and endpoints alone cannot establish complete support or contiguous required
windows. Account for gaps, duplicates/conflicts, QC eligibility, supported statics,
provenance and source selection. Retain enough version/scope evidence to explain
what qualified; distinguish submitted jobs, completed ingestion, reviewer approval,
data readiness, model compatibility and later forecast activation.

Missing discharge is not automatically a blocker for every model, but it is not an
excuse to ignore ForecastInterface. Use the actual supported declaration and matching
artifact/configuration. Preserve missingness; never invent measured discharge.
Do not present unavailable observed-discharge verification as a weak skill score or
claim model accuracy from successful ingestion.

## Trishuli and model-contract context

The approved Trishuli research station `00001` has no discharge observations and
no operational water-level feed for this rehearsal. Record observation availability
and any known source; do not manufacture a feed, borrow another gauge's measurements
or import an imagined series. This station demonstrates the no-observation preparation
path, not operational observation ingestion or rating conversion.

The owner intends discharge-free use of `cmal_small`. Authoring baseline
`20444391946d6baae963ab3fc1afaf33218558eb` includes an owner-authorized **development/
staging-only** Aquacast pin at `b57a1872188e87e443fed2aa960b0885d4d537e2` (0.1.377).
For the qualified configuration its FI declaration allows missing discharge through
the 30-day lookback while past/future weather remains strict. Aquacast PR144 is still
open at this checkpoint: the staging adoption is not upstream model-owner approval
or production certification. See `docs/deployment/dependency-maintenance-2026-11.md`.

Reverify the installed declaration, matching checkpoint, supported input representation
and qualification evidence before claiming readiness. A missing permitted measurement
is not interchangeable with omitting a required FI product or breaking calendar/shape
requirements. Preserve the required data-availability flags downstream; do not add
SAP-side model bypasses, change the FI contract locally, edit Aquacast, or infer
accuracy from missing-discharge execution capability. Model execution/performance
acceptance itself remains in the following milestone.

## Rehearsal and externally observable success

An operator can use the CLI and its instructions to admit a new Nepal batch, prepare
supported station/package/weather/observation inputs, inspect the review report,
record approval or holds, and enable only the approved observation collection.
Every requested station is accounted for and forecasts remain off. Pausing, restarting
and rerunning preserve completed work and do not duplicate records or discard lineage.
Conflicts, missing inputs and changed approvals produce specific, recoverable holds.

Prove the Nepal journey first on Infomaniak under coordinated live authority. Trishuli
covers the available no-observation case. The owner will provide a further station
with suitable water-level and rating-table data to exercise real conversion. The additional
conversion-test station may be Nepalese or Swiss. A Swiss station can supply that
conversion rehearsal in the subsequent BAFU step; it does not replace the initial
Nepal preparation proof. Continue development and available rehearsals while data are being
obtained; retain real conversion acceptance explicitly pending until suitable data
are available and the route succeeds. Fixtures establish deterministic safety and
failure behavior, not live acceptance. Demonstrate supplied-discharge and derived-
discharge behavior, reviewed precedence, approved fallback and deliberately retained
gaps with appropriate evidence, without fabricating unavailable live inputs.

After the Nepal proof, verify BAFU through its existing supported routes and address
actual gaps. Preserve appropriate Swiss access boundaries and existing services;
country expansion is not permission to expose restricted data or change existing
BAFU forecast stations. Document what is proven, what is fixture-only and what is
still awaiting data or live authorization. Do not close the entire Effort on a
partial or Trishuli-only demonstration while required acceptance remains pending.

## Reuse evidence and known gaps

The current repository provides building blocks, not this complete generic journey:

- `services/basin_importer.py` and `store/basin_importer.py` provide transactional,
  versioned package persistence; they do not create stations or supply the whole
  approval/tenant boundary. Current-package no-op behavior does not repair unrelated
  binding drift.
- `cli/import_dhm_delivery.py` and parts of `services/nepal_onboarding.py` are tied to
  the six existing stations and a particular discharge delivery. Do not relabel new
  research data as that delivery. The CAMELS onboarding path can train/promote models
  and is not an unchanged station-record-only route.
- Named ERA5 history scopes permit onboarding stations but currently impose CHWRR
  authority, exact group membership and existing bindings. Their count/min/max
  summaries are not complete model-input qualification. The six-station qualifier
  also mutates forecast targets; do not carry that side effect into this boundary.
- `adapters/dhm.py` supports Nepal water-level polling and bounded historical windows;
  `adapters/dhm_files.py` parses project-used daily discharge and rating deliveries.
  Rating delivery/conversion/lineage services are reuse points, with source-specific
  admission constraints that must not be bypassed.
- The BAFU operational path is `adapters/hydro_scraper.py`; its LINDAS query provides
  current values, not arbitrary historical retrieval. `adapters/bafu_observation.py`
  is the separate archive-collector path. CAMELS-CH file observation loading exists;
  arbitrary Swiss file formats or rating deliveries are not thereby verified.
- Normal observation ingestion currently depends on operational station state.
  Enabling approved collection during preparation needs a deliberate separation
  without activating forecasts or weakening existing eligibility checks.

Inspect the current contracts, tests, touchpoint maps and standards before choosing
mechanisms. Preserve established scientific behavior and external ownership. Escalate
unsupported external capabilities to the owner instead of forking, monkey-patching or
replacing externally owned tools.

## Explicit exclusions and coordination

No administrator dashboard, forecast-dashboard edits, new authentication system,
training, model-performance approval, forecast activation, production deployment,
public publication, alerts, retirement implementation, permanent deletion, shared
Gateway cleanup or Kazakhstan adapters. The future dashboard should reuse these
preparation/review capabilities; approving prepared data is its first priority, with
QC correction and rating replacement kept controlled and minimal. The separate
operator-documentation/help vision is not absorbed into this Effort.

Use the existing Nepal-team access boundary and a scoped authorized operator.
Coordinate Infomaniak deployment, migrations, source bindings, schedules and live
writes with the owner and the single designated deployment operator before execution.
Separate branches do not isolate shared services. Do not take over the Nepal-weather
or BAFU deployment work, alter their existing station sets, or broaden the previously
authorized Gateway trial into new database/ingestion authority. Owner review/merge,
applicable independent reviews, current verification and explicit live scope remain
required under the repository's current instructions.
