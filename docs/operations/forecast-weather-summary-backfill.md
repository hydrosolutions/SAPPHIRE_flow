# Faster forecast detail reads

Forecast details reuse an immutable weather-use summary derived when the forecast
is stored. This applies to every model using selected-weather evidence, including
station, group and combined models with evidence versions 1 or 2. Forecast values,
API JSON, publication decisions and scientific replay are unchanged.

The API still reads and hash-checks canonical evidence (including the artifact),
checks parent identity and compares the summary's manifest/snapshot bindings.
A missing summary uses the original full replay path; an invalid summary returns
503. This is a derived read optimization trusted to the validating worker, not an
independent attestation against a malicious database owner or worker.

## Rollout

1. Apply migration 0094 before starting updated API or workers. It creates the
   append-only `forecast_weather_summaries` table and grants existing API roles
   SELECT, worker roles SELECT/INSERT. Normal role bootstrap also supports it.
   Verify those grants; neither service role may UPDATE, DELETE or TRUNCATE.
2. Update API and workers. Older workers remain compatible but produce no summary.
   New workers write evidence and summary atomically, reusing the weather validation
   already required for forecast storage. Failed projection writes roll back the
   forecast transaction. Identical retries write nothing; replacements have their
   own summaries.
3. Backfill explicitly selected existing forecasts using the worker environment,
   the usual `SAPPHIRE_CONFIG`/overlay and database credentials. Do not use an API
   key or print credentials. Confirm the dry run before adding `--apply`:

   ```sh
   uv run python -m sapphire_flow.cli.backfill_weather_summaries \
     --tenant chwrr --forecast-id <UUID> --forecast-id <UUID>
   ```

   The command accepts 1–100 distinct IDs, preflights the whole list against the
   requested tenant and purpose, then validates canonical evidence one forecast at
   a time. `--data-use provisional_rating` selects that purpose; default is
   `standard`. A mismatched tenant/purpose is refused before any write.
   Each forecast has its own transaction, so a failed run can be resumed. Existing
   identical summaries are unchanged; conflicting summaries are refused. Legacy
   forecasts without selected weather are `not_applicable`. Dry runs report
   `would_create`; apply reports `created`. Errors exit 3 with a sanitized cause.
   A backfill can take time because it fully validates each retained snapshot.
4. Compare cold and repeated detail-read latency and response content for each
   relevant model. No restart or process-cache warmup is needed after backfill.

Full database backups include this table. Restoring without projections remains
readable through canonical fallback, and summaries can be regenerated. Do not
remove original evidence. A future reducer uses a new schema version alongside
existing immutable rows; readers use only their supported version.
