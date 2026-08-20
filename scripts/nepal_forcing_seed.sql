-- Plan 192 Stage B — one-time seed for the Nepal 12300 gateway-forcing feed.
-- Idempotent: safe to re-run. Apply AFTER `alembic upgrade head`.
--
-- The three-way identity is the thing to get right (Plan 192 D3):
--   station code     123    -- the SAP3/DHM gauge identity
--   polygon name     g_123  -- the Gateway VALUE COLUMN; the basin loader
--                              derives it as g_<normalized station_code>
--   gateway HRU      12300  -- what the resolver sends as `hru_code`
-- Confusing these is the easiest way to waste a day here.
--
-- `gauging_status = 'ungauged'` is deliberate (Plan 192 D9): 12300 has no
-- gauge, and 'gauged' would make it eligible for observation ingest.
--
-- The geometry is a PLACEHOLDER. The recap path is basin-average by polygon
-- NAME and SAP3 never computes geometry for it; if this basin ever feeds a
-- model or a grid extractor, replace it with the real HRU outline first.
BEGIN;

INSERT INTO basins (id, code, name, geometry, area_km2, network)
VALUES ('11111111-1111-1111-1111-111111111111', '123', 'g_123',
        ST_Multi(ST_GeomFromText(
          'POLYGON((85.0 27.0, 85.1 27.0, 85.1 27.1, 85.0 27.1, 85.0 27.0))', 4326)),
        1234.5, 'dhm')
ON CONFLICT (id) DO NOTHING;

INSERT INTO stations (id, code, name, location, station_kind, basin_id, timezone,
                      measured_parameters, station_status, network, gauging_status, tenant_id)
VALUES ('22222222-2222-2222-2222-222222222222', '123', 'Nepal test gauge 123',
        ST_GeomFromText('POINT(85.05 27.05)', 4326), 'river',
        '11111111-1111-1111-1111-111111111111', 'UTC',
        ARRAY['discharge'], 'operational', 'dhm', 'ungauged',
        '00000000-0000-0000-0000-000000000001')
ON CONFLICT (id) DO NOTHING;

INSERT INTO station_weather_sources (station_id, nwp_source, extraction_type, status, role)
VALUES ('22222222-2222-2222-2222-222222222222', 'ifs_ecmwf', 'basin_average', 'active', 'forecast')
ON CONFLICT (station_id, nwp_source) DO NOTHING;

INSERT INTO recap_gateway_polygon_bindings (station_id, basin_id, gateway_hru_name, name, spatial_type)
VALUES ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111',
        '12300', 'g_123', 'basin_average')
ON CONFLICT DO NOTHING;

COMMIT;
