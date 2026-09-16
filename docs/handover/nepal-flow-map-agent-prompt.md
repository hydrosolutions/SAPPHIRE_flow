# Current Nepal backend handoff

This replaces the earlier producer/consumer proposal. The authoritative backend
worktree is `/private/tmp/sapphire-nepal-demo`, branch `feat/nepal-demo-export`.
Read `docs/spec/nepal-demo-bundle.md` and Plan 273 there.

Use the backend’s four-file export, mapping region.json -> manifest,
series.json -> series, station.geojson -> station, basin.geojson -> basin.
The backend adopts your existing series field layout, half-open windows,
`synthetic_eligible` playback status and synthetic verification outturn.

Update the old Nepal location and basin schema to Dudh Koshi at Rabuwa:
fictional display point longitude 86.668726, latitude 27.269326; source basin
NP_1_00092, EPSG:4326 MultiPolygon. Preserve the real basin outline from Sandro’s
fine-tune station nepal_20010. Basin properties are basin_id, name and source;
feature ID is NP_1_00092. See the backend spec for exact fields.

The backend’s generated schema uses standard JSON Schema ($ref/$defs/anyOf,
tuple and array constraints). Your current custom validators do not implement
that vocabulary fully; use a complete validator or add support with negative
tests, including invalid nullable values. Keep geometry types MultiPolygon-capable.

Default: demonstration issue date 2025-08-12T00:00:00Z; 168 hourly history samples,
72 forecast steps at +1..+72h, matching synthetic verification (starts_at_issue_time
false), explicit null gaps. Values are invented and never operational. Thresholds
and comparator stay null. No checksums. Do not regenerate a different scenario.

Retain Swiss defaults and region isolation. Frontend rendering, startup selection,
basemap attribution and animation remain owned by your repo. Backend completion
is not a claim that your import/rendering has been verified.
