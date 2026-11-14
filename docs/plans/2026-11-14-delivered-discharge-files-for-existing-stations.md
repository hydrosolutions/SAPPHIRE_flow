---
status: DRAFT
created: 2026-11-14
title: Delivered daily-discharge files for existing tenant stations (#453 M1)
---

# Delivered daily-discharge files for existing tenant stations

DRAFT. Not executable until independently reviewed and set READY by the orchestrator.
Program #450, Effort #453, milestone M1. Authority: the vision
`planning/visions/2026-11-07-prepare-new-stations-in-sapphire.md`, sections "MVP first
proof and deferrals" and "Delivered historical files for existing stations (first slice,
MVP)". Planning base: `origin/main` at `0020d0498` (Alembic head `0084`,
`RELEASE_TARGET` `0.1.1134`). No live operation, deployment or restricted file is part
of writing or reviewing this plan.

## Outcome

An authorized operator admits a delivered batch of DHM daily-discharge files (`DFL_*`)
for stations that already exist in their tenant, and a hydrologist sees each station's
quality problems. The first customer is DHM's October 2026 delivery for tenant `chwrr`.
The second delivery needs no code change and no migration. A third (for example DHM's
next batch) needs only a new run-time manifest entry and one admission command.

## Non-goals

- Water-level (`GHT_`) files, the 24:00 convention and datum offsets (vision § Later).
- Rating tables: the October tables are already admitted through `ratings-only`.
- New stations, station metadata changes, tenant creation or re-tenanting.
- Retraining, model-input qualification, forecast activation, publication.
- Per-station or per-delivery QC ceilings, changes to the shared `config.toml`.
- Per-measurement or per-period precedence review, fallback gap filling.
- A new API endpoint (only if the operator report proves insufficient; see D-g).
- Changing the September command (`stations | replace | qc`) beyond its admission seed.

## Closed decisions (owner, 2026-11-13)

- **D-a** This slice is the one exception to "new trial stations only".
- **D-b** It does not wait for the Gateway Effort (#452).
- **D-c** Delivery facts are run-time data: the existing rating manifest gains a file
  kind. The manifest is not committed and holds no values. The new route hard-codes no
  delivery identity, station list or station count. The September command stays as is;
  only its admission is seeded.
- **D-d** Admission is a command run with the owner-level database role, not hand-written
  SQL. One migration adds the admission table and rewrites the operator guard, seeded with
  September. The guard keeps every current property: untagged rows refused, other tenants
  refused, no row moves between stations or deliveries, the operator cannot admit itself.
  Scoping an admission to stations or parameters is deferred.
- **D-e** Values load as delivered. QC uses the network (`dhm`) rules with the same
  `gross_outlier` skip as September, no per-station ceilings, no change to the shared
  config. Flagged values are stored with their flags; nothing is dropped or corrected.
- **D-f** (owner, second revision 2026-11-14) The latest delivery wins. A day already held
  by another admitted delivery is skipped when equal. When it differs, the newer
  delivery's value is loaded and the day is marked as revised: the previous value and
  delivery are kept in a small revisions table, and an administrator reviews the day and
  marks it accepted. "Latest" is the later `delivered_on` in the manifest, never arrival
  order. Until reviewed, the revised value is the one in use. Replacement is delete the
  old row plus insert the new one in the same transaction, so the guard keeps its current
  property that no row moves between deliveries. If the old row is referenced elsewhere
  and cannot be deleted, that day is held and reported instead. Reports show station,
  dates and counts only. Mapping the mark to a QC flag or an API field is deferred.
- **D-g** (revised 2026-11-14, vision merged in #516) Problems are visible in two places:
  the operator CLI report per station (coverage, counts by QC status, gaps, flagged
  ranges, rating-status counts, no values in logs or issues) and the observation API,
  which returns a rating status and reason for every delivered observation (D-l). Mapping
  flagged ranges to model windows is deferred.
- **D-j** (owner, 2026-11-14; aligned with the merged vision) October daily values link to
  the rating table in force for the day, selected as the native rating route selects:
  per station the newest delivered rating file wins, and within it the block with the
  latest start on or before the day. Candidates are curves tagged with the rating delivery
  ids named in the manifest; never the September `replace` delivery's own curves
  (`dhm-barkhk-2026-09-08`: October rows linked to them would block September's
  `replace`). A station with no October rating file (for example 450) falls back to its
  September-delivered table through the same rule. The status of each day:
  `rated` (in force, inside validity, value inside the table's discharge range);
  `provisional` with reason `after_last_table` (the in-force valid block's validity has
  ended: link stays to that most recent past table), `outside_range_below` or
  `outside_range_above`; `no_rating` with reason `invalid_table` (the in-force block failed
  validation: conversion is held, never a fallback to an earlier block),
  `before_first_table` or `no_table`. Supplied discharge is never altered or refused by
  its status. Invalid blocks are not stored (admission audits only their start date and
  reason), so their spans are derived from the rating files named in the manifest with the
  same validation as admission (T3). Q8 is closed by the merged vision.
- **D-k** (owner, 2026-11-14) October values get exactly September's visibility: hidden
  from consumer tokens, visible to reviewers in scope and to admins (restricted DHM data;
  public release was never authorized). Hence T4.
- **D-l** (owner, 2026-11-14) The observation API exposes the rating status and reason of
  every delivered observation (null for other rows), efficiently. The status is **stored at
  load time** in two nullable columns on `observations` (`rating_status`, `rating_reason`,
  `CHECK`ed enumerations), because the invalid-block and before-first-table cases cannot be
  derived from stored curves alone. The extrapolation bounds agreed in September **still
  stand** (confirmed 2026-11-14): above the table top plus 1.0 m refuse, below half the
  lowest tabulated discharge refuse, never store 0.0. They govern level-to-discharge
  conversion, which is outside this slice (discharge only); they do not touch supplied
  discharge. Where the conversion milestone persists its range flag is decided there,
  and may reuse these columns.
- **D-h** Discharge only. Water level, 24:00 and datum offsets are out.
- **D-i** Coordinated with the Nepal-cloud session through the owner; implementation
  starts only on the owner's go-ahead. Deployment to the Nepal host belongs to the owner
  and the designated deployment operator, never to implementers.

## Facts from the code that shape the design

1. `types/dhm_delivery.py::DELIVERY_ID` (September) is a literal in migration `0067`'s
   guard function and its error text; `docker/bootstrap-roles.sql` checks the guard's
   trigger names, types, `WHEN` text and function name, not the function body.
2. The guard is `SECURITY INVOKER`: any table it reads needs `SELECT` for
   `sapphire_operator`. Bootstrap grants the operator anything only while every guard
   trigger is present and enabled (`operator_guard_ok`).
3. Natural key `uq_observations_natural_key` = `(station_id, timestamp, parameter,
   source)`, with no delivery identity. `store_raw_observations` raises
   `DeliveryCollisionError` when a key is held by a different tag (including untagged), and
   its upsert writes only when value or rating provenance changed (a same-tag re-run with
   equal values writes nothing). This forces D-f.
4. **Restricted-data withholding is keyed on the September literal**:
   `api/routes/api_stations.py` (latest observations and the station observations route),
   `api_swiss_overview.py`, `api_swiss_observation_history.py` and
   `store/observation_store.py` (`ProtectedObservationVisibility.EXCLUDE`, used by
   `api/network_overview.py`). Rows tagged with any other delivery are returned to
   consumer tokens. Loading October without changing this would expose restricted
   values. Generalising it is in scope as a safeguard, not a new feature (T4).
5. `ratings-only` cannot run as the operator: the guard refuses its `rating_curves` tags
   and the operator holds no grant on `rating_delivery_files`/`_curves` (bootstrap revokes
   them as protected tables). Admitted native rating curves are immutable
   (`native_rating_curve_guard`, migration `0079`) and carry the rating delivery's id as
   their tag. The operator already holds `SELECT` on `rating_curves`
   (`docker/bootstrap-roles.sql`), so the link lookup needs no new grant.
6. `init` runs `alembic upgrade head` before `provision_tenants`; on a fresh database the
   `chwrr` tenant does not exist when migrations run. Today's guard therefore looks the
   tenant up by code, and a missing tenant refuses.
7. `services/nepal_onboarding.py::DeliveryObservationReader` (training readiness) reads only
   the September delivery. October rows will not reach retraining without a follow-up (Q6).
8. `fetch_delivery_observations` uses the per-row unit-read predicate (PR #507). New bulk
   reads must follow the same pattern; the old flattened form held the tenant lock for over
   10 minutes.

## Design

**Admission table** (migration `0085`, chained on `0084`). `delivery_admissions`:
`delivery_id text PRIMARY KEY` (one tenant per id), `tenant_code text NOT NULL` (by code,
as today's guard: fact 6), `delivered_on date NOT NULL` (the order of deliveries lives in
the database: "latest" never depends on a manifest that may not contain the earlier
delivery), `manifest_entry_sha256 text NULL` (NULL only for the September seed, `CHECK` on a
64-hex pattern otherwise), `admitted_at`, `admitted_by` (label, not an authenticated
identity), `reason`, `revoked_at timestamptz NULL`. The migration seeds
`('dhm-barkhk-2026-09-08', 'chwrr', '2026-09-08', NULL, …)`. **Admission rows are never
deleted**: a trigger refuses DELETE and TRUNCATE for every role, and UPDATE except setting
`revoked_at` from NULL. Revoking therefore cannot race a load into leaving tagged rows
without an admission (withholding stays on for every delivery ever admitted). Grants:
`SELECT` only, to `sapphire_operator` (the guard's lookup) and `sapphire_api`
(withholding); no worker reader exists. Nobody but the owner writes it.

**Guard rewrite.** `CREATE OR REPLACE FUNCTION public.operator_guard_delivery_row()` keeps
its name, `SECURITY INVOKER`, pinned `search_path`, schema-qualified relations, OLD/NEW
checks and the no-move rule. Only the predicate changes, from "tag = literal AND station in
tenant `chwrr`" to "an admission exists for `NEW/OLD.delivery_id`, is not revoked, AND the
station's tenant code equals that admission's `tenant_code`". `NULL` tags fail the
`EXISTS`, so untagged rows stay refused. Triggers, the station-insert guard (still
`chwrr`-only, used only by the September `stations` command) and the truncate guard are
untouched, so the bootstrap's `operator_guard_ok` check stays valid. Bootstrap adds the
`SELECT` grants inside its existing `operator_guard_ok` block (the operator grant is
already inside it).

**Downgrade** (security-sensitive ordering): (1) revoke operator DML, as `0067` does;
(2) refuse if any `observations` row carries a tag admitted other than the seed (rows
would become unwithheld under the older code: fact 4; rating curves carrying the October
tag do not matter); (3) restore the `0067` literal function body; (4) drop the table and its
triggers.

**Manifest file kind.** In `cli/rating_delivery_manifest.py` each file gains
`kind: Literal["rating_table", "daily_discharge"] = "rating_table"`, so existing manifests
stay valid. `load_rating_manifest` takes only `rating_table` files, so `ratings-only` is
unchanged for any manifest. A new `load_discharge_manifest(path, delivery_id)` returns the
selected delivery's `daily_discharge` files as a frozen dataclass. It checks: size bound,
SHA-256 of the original bytes, `parse_daily_flow`, the header code against the declared
code, at most one file per station, the path outside the checkout. The **manifest-entry
hash** is SHA-256 of a canonical JSON of `{delivery_id, delivered_on, sorted [(network,
station_code, kind, sha256)]}` for the selected entry. It excludes paths, so a dry-run copy
and the host give the same hash, and other entries, so adding November later does not
invalidate October.

**Admission command** (owner role): `python -m sapphire_flow.cli.delivery_admission admit
--tenant T --manifest M --delivery D --admitted-by NAME --reason TEXT [--dry-run]`, run as a
one-off command of the existing `init` service, which already holds the owner credential (Q4;
the runbook adds a mount for the manifest). It takes `lock_tenant`, checks the tenant and
every declared station exist, uniquely, in that tenant, and records the row with the
entry's `delivered_on` and one `DELIVERY_IMPORTED` audit entry (`command: "admit"`, hash,
station count; no values). The same id with the same hash is a no-op; the same id with a
different hash, or another tenant, is refused. `revoke --delivery D` takes `lock_tenant`
and sets `revoked_at` (the operator can then write nothing for `D`; rows stay withheld),
with an audit entry. Order for an undo: `discharge-withdraw` first, `revoke` after.

**Operator commands** (added to `cli/import_dhm_delivery.py`, same `operator` service and
CHWRR overlay; the tenant-generic CLI identity is deferred):

- `discharge --tenant T --manifest M --delivery D [--dry-run]`. Load and verify the files,
  take `lock_tenant` (serialises with `replace`/`qc`/`admit`/`revoke`), and only then
  check that the admission for `D` exists, is not revoked, and has the same tenant and
  hash. Resolve stations by code and network in the tenant, as `admit_rating_deliveries`
  does: missing, ambiguous or foreign refuses. Build `RawObservation(parameter="discharge",
  source=MANUAL_IMPORT, delivery_id=D, rating_curve_id=<per D-j>, rating_status and
  rating_reason per D-j)` at
  `nepal_day_start(day)`. Classify each key against existing rows, read per station by time
  range with the per-row predicate (fact 8): *new* → insert; *own tag* → upsert (equal =
  no-op; a changed link resets QC to RAW, so a re-run is zero-write only when links are
  unchanged); *other admitted tag, equal value* → skip and record; *other admitted tag,
  different value* → by delivery date from `delivery_admissions`: if `D` is strictly later,
  replace the day and record a revision (D-f; until T3b lands: hold that day only); if `D`
  is earlier, skip and record "held by newer delivery" (an older delivery never reverts a
  newer one); equal dates → refuse; *untagged or non-admitted tag* → refuse
  (`DeliveryCollisionError`). Equality means exact float equality. Read back and compare the
  intended and written key sets. One audit entry: per station written/skipped/held counts,
  skipped and held date ranges with the holding delivery id, provisional and unlinked
  days, the manifest-entry hash.
- `discharge-qc --tenant T --delivery D [--dry-run]`. Rows tagged `D` only. Rules: `dhm`
  network discharge rules from the loaded config, `skipped_rule_ids={"gross_outlier"}`,
  `overrides=[]`. Refuse if no `dhm` discharge rule resolves. Reuse `_local_day_segments`,
  `resolve_selection`, `Stage1QualityChecker` and `update_delivery_qc`. Single-day segments
  stay `QC_UNCHECKED`. Days skipped as equal stay tagged September, so October's series has
  holes and more single-day segments; the audit entry and report count the fragmentation
  (using other admitted rows as read-only context is deferred). The audit entry carries
  status counts, the resolved rule ids and thresholds, and `obs_qc_rule_version`.
- `discharge-report --tenant T --delivery D`. Read-only, stdout table. Per station: first
  and last day, day count, missing-day count and gap ranges, counts by QC status, flagged
  date ranges per rule id, provisional/unlinked/held/revised day counts. Dates and counts
  only.
- `discharge-withdraw --tenant T --delivery D [--dry-run]`. Deletes this delivery's rows in
  the tenant, then audit counts. Refuses the seeded September id (its own `replace`
  exists) and refuses while any revision exists for `D` (a revised day's earlier value
  lives only in the revisions table; restoring it is a deferred, owner-decided step). A
  referencing row aborts it atomically.

**Withholding.** Replace the September-literal exclusion (fact 4) with "the row's
`delivery_id` is in `delivery_admissions`" (every row ever admitted, revoked or not). Use
one store helper and one SQL predicate (`delivery_id IS NULL OR delivery_id NOT IN (SELECT
delivery_id FROM delivery_admissions)`), evaluated per row, not as a flattened semi-join,
behind the existing `excluded_delivery_id`/`ProtectedObservationVisibility` call sites.
Each path keeps its current September behaviour for every admitted delivery: Swiss overview
and Swiss history exclude it for everyone including admins; latest observations exclude it
for every non-admin; the station observations route excludes it for non-reviewers; the
network overview shows it to reviewers. Untagged and non-admitted rows (for example
BIPAD-feed tags) stay visible. D-k means "exactly as September today", path by path.

**Rating link and status (D-j, D-l).** The discharge manifest entry also lists the
station's rating files (kind `rating_table`) and the rating delivery ids that may be
linked. The load parses those files with `parse_rating_tables` and the admission's
validation to learn every block's start, validity and validity outcome; it then finds, per
day, the in-force block (latest start on or before the day, over valid and invalid
blocks), looks up the stored curve by station, delivery tag and start date, and sets
`rating_curve_id`, `rating_status` and `rating_reason`. A stored curve that disagrees with
the parsed file refuses the station. Additive migration `0086_observation_rating_status`
adds the two nullable columns (cheap, metadata-only; not security-sensitive, so it ships in
PR-B, and T3b's migration becomes `0087`). `RawObservation`/`Observation`, the store
insert/upsert/read paths, `ObservationResponse` (new optional fields, default null, so old
clients are unaffected) and the observation routes carry them. The upsert treats a changed
status like a changed link.

**Revised days (D-f, T3b; built only if the dry run shows differing days).** A separate
small migration (`0087`) creates `delivery_value_revisions`: station, timestamp,
parameter, previous value, previous delivery, previous `rating_curve_id`, new delivery,
revised time, `status` (`pending` default, or `accepted`), reviewer name, reviewed time. It
holds restricted values: no API role can read it, it joins the admin table browser's
protected list (`api/routes/tables.py`), and the operator gets a column-level `INSERT` that
excludes status and reviewer columns, plus `SELECT`. The owner-level `discharge-review
--tenant T --delivery D [--accept --reviewer NAME]` lists pending days (dates and counts)
and marks them accepted, with an audit entry. Replacement is delete plus insert in one
transaction, each revised day inside a SAVEPOINT that catches a foreign-key violation
(the operator cannot read the tables that reference observation ids: versions, feed
evidence, provisional discharges, rating-derived discharge, captures, BAFU unit events); a
day whose old row is referenced is held and reported. The existing `observation_versions`
cannot serve here: it stores a value only together with the rating curve that produced it.
Because the operator can delete rows of any admitted delivery in the tenant, the guard
bounds the set of deliveries, not which one a run touches; `security.md` says so plainly.
Consequences the runbook must state: after any revision September's `replace` fails
(it would re-insert keys now held by October); revised days leave September's series in
training readiness (which reads September-tagged rows only); withdraw is blocked while
revisions exist.

## Questions settled from the code

1. **Natural key vs delivery tag:** the tag is not in the key, so per-day ownership is
   exactly one tag. Skip-equal leaves the September row and tag in place; the October audit
   entry records that day as held by September.
2. **Live BIPAD water-level rows:** no interaction. They are `water_level`/`measured`, a
   different key. The guard refuses operator writes to them (tags not admitted), and
   withholding leaves them visible. Their `bipad-water-level-feed-*` tags have no producer
   on main; that part is not verified.
3. **Migration number:** `0085`. No remote branch claims it. The stopped local one-off patch
   has an uncommitted `0085`, which must not be revived. Bump `_RELEASE_B_HEAD` in
   `tests/unit/db/test_alembic_head_release_b.py` to `"0085"` with its comment line.
4. **Idempotent re-runs:** `admit` with the same hash is a no-op. `discharge` with
   unchanged files writes zero rows and still writes its audit entry. `discharge-qc`
   re-evaluates and gives the same result.
5. **`--dry-run`:** as today, the real transaction (audit included) rolls back. It does not
   prove a later commit will succeed against concurrently changed data.
6. **Report production:** a read-only operator subcommand with `SELECT` it already has. No
   new grant, no API.

## Open questions for the owner (plain language)

1. **Name of the October discharge delivery.** *Closed 2026-11-14: same name.* The October rating files are already
   recorded under one delivery name. Should the discharge files go under the same name or
   a new one? *Recommendation:* the same name. It is one DHM delivery, and the manifest
   gains a file kind rather than a second entry.
2. **Who may see October values.** *Closed 2026-11-14 (D-k):* same as September.
3. **Link to rating tables.** *Closed 2026-11-14 (D-j).*
4. **How the owner-level admission is run.** *Closed 2026-11-14: as recommended.*
   *Recommendation:* a one-off command through the
   existing set-up container, which already holds the owner credential. This adds no new
   service and puts the credential nowhere new. The security reviewer should confirm it.
5. **One differing day.** *Closed 2026-11-14 (D-f):* hold only the differing day; load
   everything else; corrections are an explicit later step. *Superseded by D-f (second
   revision): latest delivery wins, marked, admin review.*
6. **Retraining.** *Closed 2026-11-14: follow-up, as recommended.* The training-readiness check reads only September today. Is it fine that
   making it read October is the next milestone's first small task, not this one?
   *Recommendation:* yes. It keeps this slice to loading and checking.

7. **Days after the last rating block ends.** *Closed 2026-11-14 (D-j):* provisional,
   linked to the most recent past valid table.
8. **Days inside an invalid rating block.** *Closed 2026-11-14:* the merged vision (#516)
   adopts the native rule: such days are `no_rating`/`invalid_table`, and only days after the
   in-force valid block's validity are provisional.

## Tasks

Red-first means each new behaviour has a failing test first, failing for the reason the
gap exists. Integration tests run against the real PostGIS testcontainer through real
logins (`sapphire_operator`, `sapphire_api`, owner), as the existing operator tests do.
Fixtures are synthetic; no delivered values.

- **T1 Admission table, guard rewrite, bootstrap** (high risk; security review). Files:
  `alembic/versions/0085_delivery_admissions.py`, `src/sapphire_flow/db/metadata.py`,
  `docker/bootstrap-roles.sql`, `tests/unit/db/test_alembic_head_release_b.py`,
  `tests/unit/db/test_operator_guard_migration.py` (add: seed equals `DELIVERY_ID`),
  `tests/integration/db/test_operator_role.py`, `test_operator_grant_matrix.py`,
  `test_operator_guard_historical.py`, new
  `tests/integration/db/test_delivery_admission_guard.py`. Tests: the September
  `replace`/`qc` pass unchanged on the seed. Untagged, non-admitted, revoked,
  admitted-but-other-tenant and missing-tenant writes are refused (INSERT NEW, DELETE OLD,
  UPDATE both). No move of station or tag. Curve validity dates are fixed. The operator and
  api cannot INSERT/UPDATE/DELETE/TRUNCATE `delivery_admissions` (grant matrix updated and
  still minimal). Admission rows cannot be deleted by any role; UPDATE is refused except
  setting `revoked_at`. A temporary table named `delivery_admissions` cannot satisfy the
  guard. The function stays `SECURITY INVOKER` with a pinned `search_path`; no `SECURITY
  DEFINER` function writes the guarded tables or the admission table. `operator_guard_ok` is
  still true after upgrade. Downgrade revokes DML before restoring the literal body and
  refuses while non-seed tagged `observations` rows exist; upgrade, downgrade, upgrade
  round-trips. Exit gate: security review clean; full suite green.
- **T2 Manifest file kind and admission commands.** Files:
  `src/sapphire_flow/cli/rating_delivery_manifest.py`,
  `src/sapphire_flow/types/rating_delivery.py` (or a sibling `types/discharge_delivery.py`),
  new `src/sapphire_flow/cli/delivery_admission.py`, new
  `tests/unit/cli/test_rating_delivery_manifest.py` (the loader has no test today),
  `tests/integration/cli/test_rating_only.py`, new
  `tests/integration/cli/test_delivery_admission.py`. Tests: old manifests load unchanged. A
  mixed manifest gives `ratings-only` only the rating files. The discharge loader refuses a
  hash mismatch, a header mismatch, a duplicate station, an unknown kind, an oversize file
  or a path inside the checkout. The entry hash ignores paths and other entries. `admit`
  works as owner and fails as operator (permission); it records `delivered_on`. Same hash:
  no-op; different hash or tenant: refused. Dry-run leaves no row. `revoke` sets
  `revoked_at`, takes the tenant lock, and the operator can then write nothing for the
  delivery. A concurrent `discharge` and `revoke` cannot leave a state where tagged rows
  are served to consumer tokens.
- **T3 Load, QC, report, withdraw.** Files: `src/sapphire_flow/cli/import_dhm_delivery.py`,
  new `src/sapphire_flow/services/delivered_discharge.py`,
  `src/sapphire_flow/store/observation_store.py` (bounded per-station existing-key reader),
  `protocols/stores.py` and the fake store if a method is added, new
  `tests/unit/services/test_delivered_discharge.py` (pure overlap classification, rating
  link selection, report aggregation), `tests/integration/cli/test_import_dhm_delivery.py`
  plus a new integration module for the discharge route. Tests: with the September seed
  loaded, equal days are skipped and recorded. A differing day is held alone in this task
  (existing value untouched, every other day and station loads); an older delivery never
  overrides a newer one; equal delivery dates refuse. An untagged collision refuses. A
  revoked or wrong-hash delivery is refused by Python (the guard never sees the hash; it
  refuses non-admitted or revoked tags). Missing or foreign stations refuse. The admission
  is checked after the tenant lock. A re-run with unchanged links writes zero rows.
  Dry-run rolls back. Audit `detail` contains no delivered value (asserted by scanning for
  every fixture value). Rating link and status per D-j: candidates only from the named rating deliveries, never
  the September `replace` tag; the newest delivered file wins per station; the in-force
  block is the latest start on or before the day, over valid and invalid blocks; a day whose
  in-force block is invalid is `no_rating`/`invalid_table`; before the first block is
  `before_first_table`; a day after the in-force valid block's validity is `provisional`/
  `after_last_table` and keeps the link; a value outside the table's discharge range is
  `provisional`/`outside_range_*` with the value unchanged; a disagreement between a parsed
  rating file and the stored curves refuses that station; none of these is guessed. QC uses no
  station override, `gross_outlier` is never applied, single-day segments are
  `QC_UNCHECKED`, flags are stored not dropped, and fragmentation is counted. The report
  has dates and counts only. Withdraw removes only `D`'s rows, refuses the seed, and
  refuses while revisions exist. Water-level rows at the same stations are untouched.
- **T3c Rating status stored and served** (additive migration; depends on T3). Files: new
  `alembic/versions/0086_observation_rating_status.py`, `db/metadata.py`,
  `types/observation.py`, `store/observation_store.py`, `protocols/stores.py` and fakes,
  `api/schemas.py` (`ObservationResponse`), `api/routes/api_stations.py`,
  `api/routes/api_swiss_observation_history.py` where observation rows are returned, the
  API/OpenAPI documents under `docs/spec/`, `tests/unit/migrations`, store and API tests.
  Tests: the two columns are nullable with enumerated values; old rows read as null; a
  delivered row round-trips status and reason; the API returns them and old consumers still
  parse the response; one request for a station's full history stays a bounded number of
  queries (no per-row lookups); consumer tokens still do not see withheld delivery rows;
  the upsert resets QC only when the link or status changes.
- **T3b Revised days and review** (conditional: build only if the dry run finds differing
  days; separate migration `0087`, PR-C). Files: new
  `alembic/versions/0087_delivery_value_revisions.py`, `db/metadata.py`,
  `docker/bootstrap-roles.sql`, `src/sapphire_flow/api/routes/tables.py` (protected list),
  `services/delivered_discharge.py`, `cli/delivery_admission.py` (`discharge-review`), a
  store method for delete-plus-insert, grant-matrix and table-browser tests. Tests: a
  differing day in a strictly later delivery is replaced; the revision row holds the
  previous value, delivery and curve, status pending; an operator cannot insert an
  `accepted` row; a referenced old row (foreign-key violation, caught per day with a
  savepoint) holds that day and the rest of the load commits; a re-run writes nothing;
  `discharge-review` lists dates and counts only, `--accept` marks accepted with the
  reviewer name and an audit entry, and fails as operator; no API role reads the table and
  the admin table browser still counts tables; the report counts pending revisions;
  withdraw refuses while revisions exist.
- **T4 Withholding generalised** (restricted data; covered by the same security review).
  Files: `src/sapphire_flow/api/routes/api_stations.py`, `api_swiss_overview.py`,
  `api_swiss_observation_history.py`, `src/sapphire_flow/store/observation_store.py`,
  `src/sapphire_flow/api/network_overview.py` (call site only),
  `src/sapphire_flow/types/observation_history.py` (`excluded_delivery_id` is validated
  non-empty), `protocols/stores.py` and the fakes, their API/store tests. Tests: each of the
  five paths treats every admitted delivery exactly as it treats September today (see
  Withholding). Untagged and non-admitted tagged rows stay visible. September behaviour is
  unchanged. A wall-time note on a realistic fixture replaces a query-plan assertion.
- **T5 Documentation** (see below). Exit gate: docs match the merged code.
- **T6 Dry run and host run** (owner and deployment operator only; not implementers). See
  Rollout.

```json
{
  "tasks": [
    {"id": "T1", "depends_on": [], "estimate_days": 1.75, "risk": "high"},
    {"id": "T2", "depends_on": ["T1"], "estimate_days": 1.0, "risk": "medium"},
    {"id": "T3", "depends_on": ["T1", "T2"], "estimate_days": 3.0, "risk": "medium"},
    {"id": "T3c", "depends_on": ["T3"], "estimate_days": 1.5, "risk": "medium"},
    {"id": "T4", "depends_on": ["T1"], "estimate_days": 1.0, "risk": "high"},
    {"id": "T5", "depends_on": ["T2", "T3", "T3c", "T4"], "estimate_days": 0.5, "risk": "low"},
    {"id": "T6", "depends_on": ["T5"], "estimate_days": 0.75, "risk": "high", "owner_run": true},
    {"id": "T3b", "depends_on": ["T6"], "estimate_days": 1.25, "risk": "medium", "conditional": true}
  ]
}
```

Estimates are unmeasured. Build without T3b is 8.75 days in sum (T1 1.75, T2 1.0, T3 3.0,
T3c 1.5, T4 1.0, T5 0.5; T2 and T4 run in parallel after T1, so the critical path
T1-T2-T3-T3c-T5 is about 7.75), plus 0.75 for the owner-run host steps; T3b
adds about 1.25 only if the dry run finds differing days. 14 November 2026 is a Saturday,
so the 24 November cut line (about seven working days, with reviews and merges on top) is
**not reachable** with the API requirement; expect early December. The agreed fallback
applies: the workshop uses the September data and presents the October delivery as the next
step. If time is short, cut the report's flagged-range detail first, never T1, T3c's
tests, or T4.

**PRs.** PR-A = T1 + T4 (database and withholding; the security review covers both; it
deploys standalone and safely). PR-B = T2 + T3 + T3c + T5 (T3c's additive migration is not security-sensitive). PR-C =
T3b, only if needed. PR-A
merges and deploys first or together with PR-B; no October load before both are deployed.
Each code PR advances `RELEASE_TARGET` exactly once (`.venv/bin/python -I
tools/release_target.py bump --base-sha <base>`; `docs/standards/cicd.md` § Release
identity), with no hand-edited package versions and no tags. Lint, format, changed-module
types and the full suite must pass after the final change.

## Rollout (owner-run)

1. **Dry run on a copy.** Use a copy of the Nepal database restored on the Nepal host (never
   on a laptop or in CI), with the release under test and the manifest mounted into
   `init`. Run `admit --dry-run`, then for real on the copy, then `discharge --dry-run`,
   `discharge`, `discharge-qc`, `discharge-report`. Record the counts, skipped, held and
   differing days, provisional and unlinked days, QC fragmentation, refusals and wall
   times. This measures the overlap with September (the vision leaves it to the dry run)
   and decides whether T3b is built. Run `discharge` again and expect zero rows written
   (links unchanged). Run `discharge-withdraw`, then `revoke`, to prove the undo path.
2. **Owner go-ahead**, coordinated with the Nepal-cloud session (D-i).
3. **Host run** by the owner or the designated deployment operator: the same commands
   without `--dry-run`, through the operator overlay; `admit`/`revoke` through `init`.
4. **Read-back:** report counts equal the dry run. A consumer-token request to each of the
   five paths treats October rows as it treats September's; a reviewer token in scope sees
   them where September's are visible. September row and QC counts are unchanged.
   Water-level row counts are unchanged. The audit entries exist and carry no values. The
   operator role goes back to no login (deploy without the overlay) when done.

## Rollback

- **Undo a loaded delivery:** `discharge-withdraw` (operator), then `revoke` (owner sets
  `revoked_at`). The append-only audit log keeps the history. Without revisions,
  September rows are never touched, because skipped days were never written under October.
  With revisions (T3b), a revised day's earlier value lives only in the revisions table;
  withdraw refuses while any exists, and restoring them is an owner-decided step to
  design if it is ever needed.
- **Image rollback below PR-A while October rows exist is forbidden.** The older code hides
  only September (fact 4), so October values would reach consumer tokens. Withdraw first.
  The migration downgrade enforces this (T1); the runbook states it for image-only
  rollback, which the downgrade cannot catch.
- **Guard problem:** the existing emergency revoke (operator `NOLOGIN` plus
  `pg_terminate_backend`, `docs/standards/cicd.md`) stops all operator writes at once.

## Documentation updates (T5)

- New admin checklist `docs/runbooks/delivered-discharge-files.md` in the style of
  `docs/operations/recap-gateway-runbook.md` § admin checklist. It has an inputs table
  (manifest, delivery dir, delivery id, tenant, admitter name, reason), a stage table
  (admit, load, QC, report, read-back, close login), copy-paste commands, "rules that never
  change" (no values in logs or issues; files outside the checkout; no image rollback below
  PR-A while rows exist) and an "if it stops" table (refused admission, held days,
  collision, missing station, lock timeout, withdraw blocked by a reference or by
  revisions) and the consequences of revisions (September `replace` stops working;
  revised days leave September's series in training readiness). The runbook also tells the
  admin to mount the manifest into `init`.
- `docs/runbooks/chwrr-dhm-history-import.md`: cross-reference. September is now an
  admitted delivery (seed); its commands are unchanged.
- `docs/touchpoint-maps.md`: Persistence / API write path operator bullets (the "a second
  delivery is a new migration" line becomes admission), Observation QC threshold
  configuration (the delivered-discharge QC uses network rules only), and a short new
  "Delivered discharge admission" map.
- `docs/standards/security.md`: three levels of identity (the owner also admits deliveries;
  the guard predicate; the admission-table grants) and the restricted DHM history bullet
  (all admitted deliveries). `docs/standards/cicd.md` § Operator role (admission through
  `init`). `docs/spec/database-schema.md` (new table). `docs/native-provisional-rating.md`
  (manifest `kind`). The `0067` docstring stays historical; `0085` states the supersession.
  `docs/standards/security.md` also states plainly that the operator can delete or
  re-tag rows of any admitted delivery in its tenant (the guard bounds the set of
  deliveries, not which one a run touches). Also `docs/spec/api-v1-review.md`,
  `docs/spec/network-overview.md` and `docs/spec/types-and-protocols.md` (observation types,
  `ObservationResponse`, the rating status and reason fields and their enumerations) where the
  withholding signature or wording changes, the "second delivery is a migration" text in
  `security.md`, and `docs/plans/README.md` index entry.

## Risks

- **Restricted-data exposure** through withholding gaps or image rollback (facts 4, T4,
  Rollback). This is the highest risk, and the reason PR-A deploys first.
- **Guard weakening by the rewrite.** Mitigation: the T1 test matrix mirrors every current
  property, plus a security review of the exact diff.
- **Owner credential use outside deploy.** Bounded to a one-off `init` command; reviewed.
- **Lock duration on the live host.** About 254k new rows under `lock_tenant` would block
  `replace`/`qc`, and ingest's shared station locks. Mitigation: per-row predicate reads,
  5,000-row batches, wall time measured in the dry run; run outside ingest peaks.
- **QC across delivery boundaries** (for example station 450, whose October period may
  start right after September's) is not checked. QC runs per delivery. Deferred.
- **Old-period rows in current inputs:** the latest October day is 2025 (briefing). Verify
  in the dry run that no active forecast lookback reaches it; September rows set the
  precedent.
- **Rating link:** unlinked and provisional days are reported, never guessed. The October
  curves exist on the host only for stations whose newest rating file stored blocks;
  verify in the dry run.
- **Status derivation correctness:** stored statuses are only as good as the parsed rating
  files; a disagreement between parsed files and stored curves refuses the station, and
  the dry run reports counts per status. A wrong enumeration value is blocked by `CHECK`.
- **Two migrations:** `0085` (guard, security review) and `0086` (additive columns). They
  ship in separate PRs so the security review stays small.
- **Revoke versus load race:** closed by never deleting admission rows and taking the
  tenant lock in `admit`, `revoke` and `discharge` (admission checked after the lock).
- **Wrong admission:** harmless until rows are written; `revoke` stops further writes, and
  `discharge-withdraw` removes the rows.

## MVP deferrals

Station or parameter scoping of admissions; a tenant-generic operator CLI identity (the
overlay stays CHWRR); re-approval bound to exact input and rule versions; cross-delivery QC
continuity (and using other admitted rows as QC context); mapping flags to model windows;
a JSON report output and a query-plan assertion; restoring September values after a
revision; a dashboard view; persisting a conversion range flag (the level conversion
milestone decides); reading
October in training readiness (Q6); water level, 24:00 and datum (vision § Later);
attribution beyond a label (the existing forged-audit residual stays).

## Review plan

- Plan: one independent Claude review (owner confirmed 2026-11-14).
- PR-A: an independent security-minded review of the migration, bootstrap grants, downgrade
  ordering and withholding, on the exact diff. PR-B: one independent review.
- The orchestrator sets READY after review. The owner decides findings, approves and merges
  every PR, and authorizes the host run.
