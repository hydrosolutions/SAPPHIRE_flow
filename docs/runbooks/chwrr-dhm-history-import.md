# CHWRR historical discharge import

This procedure loads the six restricted DHM daily-discharge records into the
`chwrr` tenant of the staging database. It does not publish data, set forecast
targets, or promote stations. Keep the delivered `DFL_*.txt` and `RT_*.txt`
files outside the checkout. Never put individual discharge or rating-point
values in logs, tickets, or commits.

The station observations API withholds this delivery from consumer and reviewer
tokens; only an internal admin token can read its individual values. Do not
issue an admin token externally without the data owner's permission.

Run this only after the Plan 268 migration and Plan 510's guard migration (`0067`)
are deployed. The `chwrr` tenant must already exist: the host's overlay declares
`[tenants.chwrr]` with `name = "CHWRR Nepal"` (the Mac-mini's
`config/overlays/mac-mini.toml`) and `init` creates it at deploy time (Plan 513).
No command here creates tenants; if the tenant already exists under another name,
`init` fails and an owner fixes the row with SQL.

Run the commands as the database-limited `sapphire_operator`, not with the owner
credential. It is created without login; an owner activates it once by creating
`./secrets/sapphire_operator_db_password` and deploying with
`docker-compose.operator.yml` (`docs/standards/cicd.md` § Operator role for
delivery replacement, which also has rotation and the emergency revoke). A deploy
without that overlay puts the role back to no login, so a forgotten overlay looks
like a revoke. The database itself limits the role: it can only write rows tagged
with this delivery at the six `chwrr` stations, and each successful command
appends one audit row (counts only, no values). Set the delivery directory once
per shell, then every command below is:

```sh
export SAPPHIRE_DHM_DELIVERY_DIR=/absolute/path/to/restricted-delivery   # outside the checkout
OP="docker compose -f docker-compose.yml -f docker-compose.macmini.yml -f docker-compose.operator.yml run --rm operator"
```

`--dry-run` parses and writes inside a transaction that rolls back (audit row
included); it does not verify a later commit will succeed against concurrently
changed data.

1. Register the approved station metadata:

   ```sh
   $OP stations --tenant chwrr --dry-run
   $OP stations --tenant chwrr
   ```

   The six rows remain `onboarding`, with no forecast target. Rerunning this
   step reuses matching rows and refuses conflicting tenant ownership or
   changed metadata.

2. Check the restricted delivery outside the checkout, then import it (the
   service mounts the directory read-only at `/data/dhm-delivery`):

   ```sh
   uv run python scripts/dhm_delivery/remeasure.py --input-dir "$SAPPHIRE_DHM_DELIVERY_DIR" --check
   $OP replace --tenant chwrr --input-dir /data/dhm-delivery --dry-run
   $OP replace --tenant chwrr --input-dir /data/dhm-delivery
   ```

   The aggregate check compares counts and gap totals with the reviewed
   delivery. The import replaces only this delivery's tagged rows and curves
   in one transaction. A dependent forecast or archive reference blocks the
   replacement and leaves the previous delivery intact. Resolve that
   reference before retrying. If DHM later clarifies the local-day boundary,
   run the replacement under the revised boundary and repeat QC.

3. Run QC after every successful import or replacement:

   ```sh
   $OP qc --tenant chwrr --dry-run
   $OP qc --tenant chwrr
   ```

   The command checks only rows tagged `dhm-barkhk-2026-09-08`. It refuses
   absent or changed DHM rule/ceiling configuration and reports status counts.
   `gross_outlier` is deliberately skipped; one-day segments with no runnable
   cadence remain `QC_UNCHECKED`. Keep those counts in the operator record.

`replace` and `qc` serialise on the tenant with an advisory lock, so the two never
run at the same time whichever role runs them.

The DHM date interpretation is the start of each Nepal local calendar day
under `Asia/Kathmandu`, including the historical 1986 offset transition. It
is a working assumption pending CHWRR confirmation. The old rating tables all
expire before current operations, so this import does not enable a live
level-to-discharge conversion.
