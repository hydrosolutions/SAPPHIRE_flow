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

## MVP first proof and deferrals

Owner priority (2026-11-13): NHS staff can onboard their own stations. A cumbersome but
working and documented path is acceptable; the sandbox run in the overarching vision
proves it and the admin checklist lets NHS adapt it to their stations. For the first
proof the sections above apply in reduced form:

- **First proof:** one new trial station, one observation source (or none), no fallback.
- **Precedence:** keep the default (reviewed history over unchecked operational data) and
  record which source supplies which measurement. Per-measurement, per-period precedence
  review is deferred.
- **Fallback gap filling:** deferred. Gaps are retained and reported.
- **Approval:** the reviewer approves or holds the saved report; record reviewer name,
  report hash and reason. Re-approval bound to exact material input and rule versions is
  deferred.
- **Multiple sources per station, plots in the report, BAFU route verification:** deferred.
- **Manual steps stay manual:** tenant declaration and `init`, the DAGs-dashboard
  backfill, Gateway registration by hydrosolutions, and an explicit command to switch on
  observation collection.

## Delivered historical files for existing stations (first slice, MVP)

Owner decisions of 2026-11-13. This slice does not wait for the Gateway Effort (#452):
it needs no basin or Gateway work. It is the one place where #453 touches stations that
already exist, by owner exception; everything else here still uses new trial stations.
Because it writes history into stations that the Nepal-cloud evaluation deployment also
uses, it is coordinated with that session through the owner and starts only after the
owner's go-ahead.

**Outcome.** An authorized operator can admit a delivered batch of daily discharge files
for stations that already exist in their tenant (first customer: DHM's October 2026
delivery), without a code change or migration per delivery, and a hydrologist can see
the data's quality problems. The October rating tables are already admitted through the
existing rating route, so this slice is discharge only. How many October stations
overlap the September six, and on which days, is measured during the dry run.

**Delivery facts are data, not code.** Extend the existing run-time rating manifest
(delivery id, delivered date, per-file station code, network, path, SHA-256) with a file
kind, instead of inventing a second manifest. It is supplied at run time, specific to the
deployment, not committed, and carries no observation values. The new route has no
hard-coded delivery identity, station list or station count. The September import command
stays as it is; only its admission is seeded.

**Admission before write.** One migration adds an admission table and rewrites the
operator guard to check it instead of one fixed delivery identity. A deliberate admission
command, run with the owner-level database role (not hand-written SQL), records delivery,
tenant and manifest hash. The September delivery is seeded. The guard stays at least as
strong as today: untagged rows and other tenants remain refused, rows may not move
between stations or deliveries, and the import role cannot admit anything itself.
Scoping an admission to stations or parameters is later hardening.

**Load as delivered, check with the shared rules.** Values are loaded as delivered (a later delivery's value replaces an earlier one only
through the marked revision below). October
preliminary QC uses the network rules with the same `gross_outlier` skip as the September
import and adds no per-station ceilings (September keeps its own). Flagged values stay
stored with their flags; nothing is dropped or corrected. Days already held by another
admitted delivery are skipped when equal. When a strictly later delivery differs, its value
replaces the earlier one only through an explicit, marked revision: the observation row
holds one value per day (the natural key has no delivery identity), the earlier value and
its delivery are kept in a separate review record outside the observation row, and an
administrator marks the revision accepted. This is the one deliberate exception to "no
in-place edits"; it is recorded, never silent, and replacement is delete plus insert under
the guard, not a move of a row between deliveries. An older delivery loaded after a newer
one never overrides it: the day is skipped and recorded as held by the newer delivery.
This revision review concerns one delivery superseding another; the general
per-measurement precedence review stays deferred. Skipped, held and revised days are
recorded as dates and counts, never values.

**Problems are visible to operators and through the API.** The MVP produces an operator
report or CLI output per station: coverage, counts by QC status, gaps, flagged ranges and
rating-status counts, with no values in logs or issues. The observation API also tells a
consumer, per observation, whether its value is rated, provisional, or has no rating at all
(next subsection). Mapping flagged ranges to model input windows is later work.

**Operator material.** An admin checklist in the style of the approved-basin runbook
(stage table, plain bullets, copy-paste commands).

### Rating status of delivered days (API requirement)

Each delivered discharge day links to the rating table in force for it, selected as the
native rating route selects (each station's newest delivered file, the latest block start
on or before the day), so the October delivery's table is used for stations that have an
October file; the link only ever points at tables of the already admitted rating delivery. The observation API returns, for each such observation, a rating
status and a reason, efficiently (a lookup that scales to a station's full history, with no
per-row queries in a loop; whether it is stored or derived is the plan's decision):

- `rated`: a valid table covers the day and the value is inside that table's range.
- `provisional`: the day lies after the end of the last table and that table is valid (the
  most recent past table is used), or the value lies outside the table's range (reasons
  `after_last_table`, `outside_range_below`, `outside_range_above`).
- `no_rating`: no rating applies at all, with a reason: `invalid_table` (the table in force
  for the day is a block that failed validation; it holds conversion and never causes
  fallback to an earlier one), `before_first_table`, or `no_table`. Invalid blocks are not
  stored; they are audited with their start date and reason only, so the plan decides how
  their span is derived.

Supplied discharge is never re-derived or altered by its status. Twenty older October rating
blocks across five files currently fail numerical validation while the 21 newest validate
(`docs/native-provisional-rating.md`), so those periods report `no_rating` with reason
`invalid_table`.

Level converted to discharge uses the established provisional pathway: a level outside the
table is converted by linear extrapolation of the end segment and flagged below or above
(`convert_provisional_level`); ordinary conversion keeps clamping and reporting the range.
An earlier owner decision (2026-09-19, convert and flag at both ends; refined 2026-09-20 with
bounds: refuse above the table top plus 1.0 m and below half of the lowest tabulated
discharge, never store 0.0) was recorded outside this repository and is not in the code:
today the provisional pathway extrapolates without those bounds and keeps its range
warnings only in the provisional store, not on observations. Recorded here so it is not lost;
whether the bounds stand and where the flag is persisted are owner decisions before the
water-level milestone. This section changes no conversion code.

### Later: water-level files, the 24:00 convention and datum offsets

Not part of the first proof; each needs the owner and, for the datum, DHM.

DHM's delivered daily discharge, water level and rating tables come from gauges that are
not exactly the operational water-level gauges. Time zone is Nepal standard time, as for
discharge. The datum is assumed equal to the operational water level for now and must be
confirmed by DHM; until then it stays explicitly `owner_assumed_same_zero`, and delivered
level would be stored under its own source label, never merged silently into the
operational series. No delivered water-level parser exists yet.

Readings stamped 24:00 would belong to the day they close (assumption: NHS aggregates
days from 01:00 to 24:00; to be confirmed with NHS), stored at 00:00 of the following
day. This is a convention change, not a check: the current local-day code assigns 00:00
to the new day.

A per-station level offset for operational water level, applied before rating conversion,
is an open question and an exception to this vision's rule against new datum adjustment,
so it needs an explicit owner decision. A first-pass, unreviewed comparison of the
September and October rating files (2026-11-13, aggregates only) is a hypothesis to
re-check, not a finding: the deliveries agree where they overlap; most stations change
little between consecutive blocks but about six change by 5-25%, a stage shift explains
most of that (typically 0.1-0.5 m) while shape changes also occur; the files do not record
the datum. If adopted, the offset would be per rating period, not one constant per
station, and would build on the existing assumed/verified reference proofs.

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
