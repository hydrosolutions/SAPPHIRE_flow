# CHWRR historical discharge import

This procedure loads the six restricted DHM daily-discharge records into the
`chwrr` tenant of the staging database. It does not publish data, set forecast
targets, or promote stations. Keep the delivered `DFL_*.txt` and `RT_*.txt`
files outside the checkout. Never put individual discharge or rating-point
values in logs, tickets, or commits.

The station observations API withholds this delivery from consumer and reviewer
tokens; only an internal admin token can read its individual values. Do not
issue an admin token externally without the data owner's permission.

Run this only after the Plan 268 migration is deployed. Set `DATABASE_URL` to
the intended staging database and `SAPPHIRE_CONFIG` to this checkout's
absolute `config.toml` path. Confirm both before a write. `--dry-run` parses
and writes inside a transaction that rolls back; it does not verify a later
commit will succeed against concurrently changed data.

1. Create a temporary admin overlay **outside** the checkout containing only:

   ```toml
   [deployment]
   global_admin = true
   writable_tenants = []
   ```

   Set `SAPPHIRE_CONFIG_OVERLAY` to that file. Run the `bootstrap-tenant`
   command first with `--dry-run`, then without it:

   ```sh
   uv run python -m sapphire_flow.cli.import_dhm_delivery bootstrap-tenant --tenant chwrr --dry-run
   uv run python -m sapphire_flow.cli.import_dhm_delivery bootstrap-tenant --tenant chwrr
   ```

   The tenant is `chwrr` / `CHWRR Nepal`. Remove the temporary admin overlay
   after bootstrapping. Ordinary import commands reject it.

2. Set `SAPPHIRE_CONFIG_OVERLAY` to this checkout's absolute
   `config/overlays/chwrr-import.toml` path. The command accepts only this
   CHWRR-scoped deployment overlay. Register the approved station metadata:

   ```sh
   uv run python -m sapphire_flow.cli.import_dhm_delivery stations --tenant chwrr --dry-run
   uv run python -m sapphire_flow.cli.import_dhm_delivery stations --tenant chwrr
   ```

   The six rows remain `onboarding`, with no forecast target. Rerunning this
   step reuses matching rows and refuses conflicting tenant ownership or
   changed metadata.

3. Check the restricted delivery outside the checkout, then import it:

   ```sh
   uv run python scripts/dhm_delivery/remeasure.py --input-dir /absolute/path/to/restricted-delivery --check
   uv run python -m sapphire_flow.cli.import_dhm_delivery replace --tenant chwrr --input-dir /absolute/path/to/restricted-delivery --dry-run
   uv run python -m sapphire_flow.cli.import_dhm_delivery replace --tenant chwrr --input-dir /absolute/path/to/restricted-delivery
   ```

   The aggregate check compares counts and gap totals with the reviewed
   delivery. The import replaces only this delivery's tagged rows and curves
   in one transaction. A dependent forecast or archive reference blocks the
   replacement and leaves the previous delivery intact. Resolve that
   reference before retrying. If DHM later clarifies the local-day boundary,
   run the replacement under the revised boundary and repeat QC.

4. Run QC after every successful import or replacement:

   ```sh
   uv run python -m sapphire_flow.cli.import_dhm_delivery qc --tenant chwrr --dry-run
   uv run python -m sapphire_flow.cli.import_dhm_delivery qc --tenant chwrr
   ```

   The command checks only rows tagged `dhm-barkhk-2026-09-08`. It refuses
   absent or changed DHM rule/ceiling configuration and reports status counts.
   `gross_outlier` is deliberately skipped; one-day segments with no runnable
   cadence remain `QC_UNCHECKED`. Keep those counts in the operator record.

The DHM date interpretation is the start of each Nepal local calendar day
under `Asia/Kathmandu`, including the historical 1986 offset transition. It
is a working assumption pending CHWRR confirmation. The old rating tables all
expire before current operations, so this import does not enable a live
level-to-discharge conversion.
