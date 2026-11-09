"""Bounded forecast-specific frozen rating input projection."""

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE FUNCTION public.rating_history_json_nodes(body text, budget integer)
RETURNS integer LANGUAGE plpgsql IMMUTABLE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE items json[] := ARRAY[body::json]; depths integer[] := ARRAY[0];
 node json; entry record; child json; depth integer; seen text[];
 count_nodes integer := 0; idx integer := 1;
BEGIN
 WHILE idx <= cardinality(items) LOOP
  node := items[idx]; depth := depths[idx]; idx := idx+1;
  count_nodes := count_nodes+1;
  IF count_nodes > budget OR depth > 32 THEN
   RAISE EXCEPTION 'rating history bounds';
  END IF;
  IF json_typeof(node) = 'object' THEN
   seen := ARRAY[]::text[];
   FOR entry IN SELECT * FROM json_each(node) LOOP
    IF entry.key = ANY(seen) THEN RAISE EXCEPTION 'rating history shape'; END IF;
    seen := array_append(seen,entry.key);
    items := array_append(items,entry.value); depths := array_append(depths,depth+1);
    IF cardinality(items)>budget THEN RAISE EXCEPTION 'rating history bounds'; END IF;
   END LOOP;
  ELSIF json_typeof(node) = 'array' THEN
   FOR child IN SELECT * FROM json_array_elements(node) LOOP
    items := array_append(items,child); depths := array_append(depths,depth+1);
    IF cardinality(items)>budget THEN RAISE EXCEPTION 'rating history bounds'; END IF;
   END LOOP;
  END IF;
 END LOOP;
 RETURN count_nodes;
END $$;
REVOKE ALL ON FUNCTION public.rating_history_json_nodes(text,integer) FROM PUBLIC;

CREATE FUNCTION public.forecast_rating_input_history(identity uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET TimeZone='UTC'
SET extra_float_digits=3 AS $$
DECLARE
 parent record; r record; cap record; raw text; source_raw text; evidence_raw text;
 lineage jsonb; rating jsonb; day jsonb; d jsonb; s jsonb; c jsonb; ref jsonb;
 capture_source jsonb; capture_evidence jsonb; curve_raw text; curve_hash text;
 reference_curve_raw text; endpoint_parts text[]; authority_parts text[];
 authority text; host text; suffix text; path_part text; port_number integer;
 reference_endpoint text;
 fp text; warning text; flag jsonb; point jsonb; point_level float8;
 start_time timestamptz; stop_time timestamptz; day_time timestamptz;
 stamp timestamptz; valid_from timestamptz; valid_to timestamptz;
 total_bytes bigint := 0; nodes integer := 0; sample_count integer := 0;
 fingerprints text[] := ARRAY[]::text[]; obs_ids uuid[] := ARRAY[]::uuid[];
 stamps timestamptz[] := ARRAY[]::timestamptz[]; dates timestamptz[] :=
  ARRAY[]::timestamptz[];
 curve_keys text[] := ARRAY[]::text[]; rows_json jsonb := '[]'; days_json jsonb := '[]';
 omissions jsonb := '[]'; omission jsonb; out_json jsonb;
 warnings text[]; day_codes text[]; recomputed_codes text[];
 accepted_n integer; rejected_n integer; extrapolated_n integer; unassessed_n integer;
 first_at timestamptz; last_at timestamptz; assessed boolean;
 qc_rejected boolean; source_value float8;
 adjusted_level float8; low_level float8; high_level float8;
 rejection_code text; extrapolation text; expected_endpoints jsonb;
 known_warnings CONSTANT text[] := ARRAY['owner_assumed_same_zero_unverified',
  'rating_expired_at_measurement','qc_unassessed','extrapolated_below','extrapolated_above'];
 day_warnings CONSTANT text[] := ARRAY['owner_assumed_same_zero_unverified',
  'rating_expired_at_measurement','qc_unassessed','extrapolated_below','extrapolated_above',
  'partial_daily_support','accepted_measured_discharge_absent','rating_expired_at_issue',
  'rating_unreconciled_sources_omitted','measured_discharge_rejected'];
BEGIN
 SELECT f.id,f.station_id,f.issued_at,f.model_artifact_id,st.tenant_id,
   octet_length(f.input_lineage) AS bytes
 INTO parent FROM public.forecasts f JOIN public.stations st ON st.id=f.station_id
 WHERE f.id=identity AND f.data_use='provisional_rating' AND f.rating_curve_id IS NULL;
 IF NOT FOUND OR parent.model_artifact_id IS NULL OR parent.bytes IS NULL THEN
  RAISE EXCEPTION 'rating history parent';
 END IF;
 IF parent.bytes>2097152 THEN RAISE EXCEPTION 'rating history bounds'; END IF;
 total_bytes := parent.bytes;
 SELECT f.input_lineage INTO raw FROM public.forecasts f WHERE f.id=identity;
 nodes := public.rating_history_json_nodes(raw,500000);
 lineage := raw::jsonb; rating := lineage->'rating_input';
 IF jsonb_typeof(lineage) IS DISTINCT FROM 'object'
 OR lineage->>'kind' IS DISTINCT FROM 'provisional_rating'
 OR lineage->'snapshots' IS DISTINCT FROM '[]'::jsonb
 OR lineage->'static_attributes' IS DISTINCT FROM '[]'::jsonb
 OR lineage->'provisional_discharge_fingerprints' IS DISTINCT FROM '[]'::jsonb
 OR lineage->'contributor_forecast_ids' IS DISTINCT FROM '[]'::jsonb
 OR jsonb_typeof(rating) IS DISTINCT FROM 'object'
 OR (rating->>'artifact_id')::uuid IS DISTINCT FROM parent.model_artifact_id
 OR (rating->>'issue_time')::timestamptz IS DISTINCT FROM parent.issued_at
 OR rating->>'policy' IS DISTINCT FROM
  'utc-day-sample-first-mean-measured-precedence-v1'
 OR jsonb_typeof(rating->'days') IS DISTINCT FROM 'array'
 OR rating->'days'='[]'::jsonb
 THEN RAISE EXCEPTION 'rating history parent'; END IF;
 stop_time := date_trunc('day',parent.issued_at); start_time := stop_time-interval
  '7 days';
 IF rating ? 'unreconciled_sources' THEN
  IF jsonb_typeof(rating->'unreconciled_sources') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'rating history shape'; END IF;
  FOR omission IN SELECT * FROM
   jsonb_array_elements(rating->'unreconciled_sources') LOOP
   IF jsonb_typeof(omission) IS DISTINCT FROM 'object'
    OR NOT (omission->>'timestamp' ~ '(Z|[+]00:00)$') IS TRUE THEN
    RAISE EXCEPTION 'rating history shape'; END IF;
   stamp := (omission->>'timestamp')::timestamptz;
   IF stamp>=stop_time THEN RAISE EXCEPTION 'rating history shape'; END IF;
   IF stamp>=start_time THEN omissions := omissions || to_jsonb(stamp); END IF;
  END LOOP;
 END IF;
 FOR day IN SELECT * FROM jsonb_array_elements(rating->'days') LOOP
  IF jsonb_typeof(day) IS DISTINCT FROM 'object'
   OR NOT (day->>'day' ~ '(Z|[+]00:00)$') IS TRUE THEN
   RAISE EXCEPTION 'rating history shape'; END IF;
  day_time := (day->>'day')::timestamptz;
  IF day_time IS NULL OR day_time<>date_trunc('day',day_time) OR day_time>=stop_time
   OR day_time=ANY(dates) THEN RAISE EXCEPTION 'rating history day'; END IF;
  dates := array_append(dates,day_time);
  IF day_time<start_time THEN CONTINUE; END IF;
  IF jsonb_typeof(day->'fingerprints') IS DISTINCT FROM 'array'
   OR jsonb_typeof(day->'warnings') IS DISTINCT FROM 'array'
   OR jsonb_array_length(day->'fingerprints')=0 THEN
   RAISE EXCEPTION 'rating history shape'; END IF;
  FOREACH warning IN ARRAY ARRAY['accepted','rejected','extrapolated',
   'assessed','unassessed','measured_rejected'] LOOP
   IF warning='measured_rejected' AND NOT day ? warning THEN CONTINUE; END IF;
   IF jsonb_typeof(day->warning) IS DISTINCT FROM 'number'
    OR NOT (day->>warning ~ '^(0|[1-9][0-9]*)$') IS TRUE THEN
    RAISE EXCEPTION 'rating history count'; END IF;
  END LOOP;
  FOR warning IN SELECT * FROM jsonb_array_elements_text(day->'warnings') LOOP
   IF warning IS NULL OR NOT warning=ANY(day_warnings) THEN
    RAISE EXCEPTION 'rating history code'; END IF;
  END LOOP;
  accepted_n:=0; rejected_n:=0; extrapolated_n:=0; unassessed_n:=0;
  first_at:=NULL; last_at:=NULL;
  day_codes:=ARRAY['partial_daily_support','accepted_measured_discharge_absent'];
  IF coalesce((day->>'measured_rejected')::integer,0)>0 THEN
   day_codes:=array_append(day_codes,'measured_discharge_rejected'); END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(omissions) AS o(value)
   WHERE o.value::timestamptz>=day_time
    AND o.value::timestamptz<day_time+interval '1 day') THEN
   day_codes:=array_append(day_codes,'rating_unreconciled_sources_omitted'); END IF;
  days_json := days_json || jsonb_build_object('day',day_time,
   'accepted',day->'accepted','rejected',day->'rejected','extrapolated',day->'extrapolated',
   'assessed',day->'assessed','unassessed',day->'unassessed',
   'measured_rejected',coalesce(day->'measured_rejected','0'::jsonb),
   'first',day->'first','last',day->'last','warnings',day->'warnings');
  IF sample_count+jsonb_array_length(day->'fingerprints')>10000 THEN
   RAISE EXCEPTION 'rating history bounds'; END IF;
  FOR fp IN SELECT * FROM jsonb_array_elements_text(day->'fingerprints') LOOP
   sample_count := sample_count+1;
   IF sample_count>10000 THEN RAISE EXCEPTION 'rating history bounds'; END IF;
   IF fp IS NULL OR fp !~ '^[0-9a-f]{64}$' OR fp=ANY(fingerprints) THEN
    RAISE EXCEPTION 'rating history fingerprint'; END IF;
   fingerprints := array_append(fingerprints,fp);
   SELECT x.tenant_id,x.station_id,x.observation_id,x.rating_curve_id,x.file_id,
    x.timestamp,x.discharge,x.rejection,x.evidence_version,x.capture_id,
    octet_length(x.content) AS bytes INTO r
   FROM public.rating_derived_discharge x WHERE x.fingerprint=fp;
   IF NOT FOUND THEN RAISE EXCEPTION 'rating history missing'; END IF;
   IF r.bytes>262144 OR total_bytes+r.bytes>16777216 THEN
    RAISE EXCEPTION 'rating history bounds'; END IF;
   total_bytes := total_bytes+r.bytes;
   IF r.tenant_id IS DISTINCT FROM parent.tenant_id
    OR r.station_id IS DISTINCT FROM parent.station_id
    OR r.timestamp<day_time OR r.timestamp>=day_time+interval '1 day'
    OR r.observation_id=ANY(obs_ids) OR r.timestamp=ANY(stamps) THEN
    RAISE EXCEPTION 'rating history identity'; END IF;
   obs_ids := array_append(obs_ids,r.observation_id); stamps :=
    array_append(stamps,r.timestamp);
   SELECT x.content INTO raw FROM public.rating_derived_discharge x WHERE
    x.fingerprint=fp;
   IF encode(sha256(convert_to(raw,'UTF8')),'hex') IS DISTINCT FROM fp THEN
    RAISE EXCEPTION 'rating history digest'; END IF;
   nodes := nodes+public.rating_history_json_nodes(raw,500000-nodes);
   d := raw::jsonb; s := d->'source'; c := d->'curve'; ref := d->'reference';
   IF jsonb_typeof(d) IS DISTINCT FROM 'object'
    OR jsonb_typeof(s) IS DISTINCT FROM 'object' OR jsonb_typeof(c) IS DISTINCT
     FROM 'object'
    OR jsonb_typeof(ref) IS DISTINCT FROM 'object'
    OR (ref->>'tenant_id')::uuid IS DISTINCT FROM r.tenant_id
    OR (ref->>'station_id')::uuid IS DISTINCT FROM r.station_id
    OR (ref->>'rating_curve_id')::uuid IS DISTINCT FROM r.rating_curve_id
    OR ref->>'level_unit' IS DISTINCT FROM 'm'
    OR ref->>'curve_unit' IS DISTINCT FROM 'm'
    OR (ref ? 'basis' AND ref->>'basis' IS DISTINCT FROM 'owner_assumed_same_zero')
    OR (NOT ref ? 'basis' AND NOT ref ?& ARRAY['offset_m','verified_at'])
    OR (d->>'tenant_id')::uuid IS DISTINCT FROM r.tenant_id
    OR (d->>'station_id')::uuid IS DISTINCT FROM r.station_id
    OR (d->>'observation_id')::uuid IS DISTINCT FROM r.observation_id
    OR (d->>'rating_curve_id')::uuid IS DISTINCT FROM r.rating_curve_id
    OR (d->>'file_id')::uuid IS DISTINCT FROM r.file_id
    OR (s->>'id')::uuid IS DISTINCT FROM r.observation_id
    OR (s->>'station_id')::uuid IS DISTINCT FROM r.station_id
    OR NOT (s->>'timestamp' ~ '(Z|[+]00:00)$') IS TRUE
    OR (s->>'timestamp')::timestamptz IS DISTINCT FROM r.timestamp
    OR s->>'parameter' IS DISTINCT FROM 'water_level' OR s->>'source' IS DISTINCT
     FROM 'measured'
    OR d->>'level_unit' IS DISTINCT FROM 'm' OR d->>'discharge_unit' IS DISTINCT
     FROM 'm3/s'
    OR NOT (jsonb_typeof(d->'discharge') IN ('number','null')) IS TRUE
    OR (d->>'discharge')::float8 IS DISTINCT FROM r.discharge
    OR d->>'rejection' IS DISTINCT FROM r.rejection
    OR NOT d ?& ARRAY['discharge','rejection','endpoints']
    OR (r.discharge IS NULL)=(r.rejection IS NULL)
    OR (r.discharge IS NOT NULL AND NOT (r.discharge>=0 AND
     r.discharge<'Infinity'::float8))
    OR (c->>'id')::uuid IS DISTINCT FROM r.rating_curve_id
    OR (c->>'station_id')::uuid IS DISTINCT FROM r.station_id
    OR c->>'interpolation' IS DISTINCT FROM 'linear'
    OR jsonb_typeof(c->'points') IS DISTINCT FROM 'array'
    OR jsonb_array_length(c->'points')<2
    OR NOT c ?& ARRAY['valid_from','valid_to']
    OR NOT (c->>'valid_from' ~ '(Z|[+]00:00)$') IS TRUE
    OR (c->>'valid_to' IS NOT NULL AND NOT (c->>'valid_to' ~ '(Z|[+]00:00)$'))
    OR jsonb_typeof(d->'warnings') IS DISTINCT FROM 'array'
    OR jsonb_typeof(s->'qc_flags') IS DISTINCT FROM 'array'
    OR NOT (jsonb_typeof(s->'qc_rule_version') IN ('string','null')) IS TRUE
    OR NOT (s->>'qc_status' IN ('raw','qc_passed','qc_failed','qc_suspect',
     'missing','qc_unchecked')) IS TRUE THEN
    RAISE EXCEPTION 'rating history sample'; END IF;
   valid_from := (c->>'valid_from')::timestamptz; valid_to :=
    (c->>'valid_to')::timestamptz;
   IF valid_from>r.timestamp OR (valid_to IS NOT NULL AND valid_to<=valid_from) THEN
    RAISE EXCEPTION 'rating history curve'; END IF;
   -- The embedded reference copy was already charged inside derived-content bytes.
   -- Charge its additional parse work, preserving noncanonical assumed snapshots.
   IF jsonb_typeof(ref->'curve'->'content') IS DISTINCT FROM 'string' THEN
    RAISE EXCEPTION 'rating history reference'; END IF;
   reference_curve_raw:=ref->'curve'->>'content';
   IF octet_length(reference_curve_raw)>262144 THEN
    RAISE EXCEPTION 'rating history bounds'; END IF;
   nodes:=nodes+public.rating_history_json_nodes(reference_curve_raw,500000-nodes);
   IF reference_curve_raw::jsonb IS DISTINCT FROM c
    OR jsonb_typeof(ref->'endpoint') IS DISTINCT FROM 'string'
    OR jsonb_typeof(ref->'api_station_id') IS DISTINCT FROM 'number'
    OR NOT (ref->>'api_station_id' ~ '^[1-9][0-9]*$') IS TRUE THEN
    RAISE EXCEPTION 'rating history reference'; END IF;
   curve_raw := (raw::json->'curve')::text;
   curve_hash := encode(sha256(convert_to(curve_raw,'UTF8')),'hex');
   IF NOT (r.rating_curve_id::text||curve_hash)=ANY(curve_keys) THEN
    curve_keys := array_append(curve_keys,r.rating_curve_id::text||curve_hash);
    IF cardinality(curve_keys)>64 THEN RAISE EXCEPTION 'rating history bounds'; END IF;
   END IF;
   IF r.evidence_version=1 THEN
    IF r.capture_id IS NOT NULL OR d->>'policy' IS DISTINCT FROM
     'native-rating-linear-endpoints-v1'
     OR (d->'feed'->>'tenant_id')::uuid IS DISTINCT FROM r.tenant_id
     OR (d->'feed'->>'station_id')::uuid IS DISTINCT FROM r.station_id
     OR ref->>'endpoint' IS DISTINCT FROM d->'feed'->>'endpoint'
     OR ref->'api_station_id' IS DISTINCT FROM d->'feed'->'api_station_id' THEN
     RAISE EXCEPTION 'rating history version'; END IF;
   ELSIF r.evidence_version=2 AND r.capture_id IS NOT NULL THEN
    -- Match canonical_provider_endpoint: HTTPS, ASCII/lowercase host, default443,
    -- unchanged path except its trailing slash. Provider text stays private.
    endpoint_parts:=regexp_match(ref->>'endpoint',
     '^[Hh][Tt][Tt][Pp][Ss]://([^/?#@[:space:]]+)(/[^?#[:space:]]*)?([?])?(#)?$');
    IF endpoint_parts IS NULL THEN RAISE EXCEPTION 'rating history reference'; END IF;
    authority:=endpoint_parts[1]; path_part:=coalesce(endpoint_parts[2],'');
    IF octet_length(authority)<>length(authority) THEN
     RAISE EXCEPTION 'rating history reference'; END IF;
    IF left(authority,1)='[' THEN
     authority_parts:=regexp_match(authority,'^\[([^\[\]]+)\](:([0-9]*))?$');
     IF authority_parts IS NULL OR strpos(authority_parts[1],':')=0 THEN
      RAISE EXCEPTION 'rating history reference'; END IF;
     host:='['||lower(authority_parts[1])||']';
    ELSE
     authority_parts:=regexp_match(authority,'^([^:\[\]]+)(:([0-9]*))?$');
     IF authority_parts IS NULL THEN RAISE EXCEPTION 'rating history reference'; END IF;
     host:=lower(authority_parts[1]);
    END IF;
    suffix:='';
    IF nullif(authority_parts[3],'') IS NOT NULL THEN
     port_number:=authority_parts[3]::integer;
     IF port_number>65535 THEN RAISE EXCEPTION 'rating history reference'; END IF;
     IF port_number<>443 THEN suffix:=':'||port_number::text; END IF;
    END IF;
    reference_endpoint:='https://'||host||suffix||rtrim(path_part,'/')||'/';
    SELECT x.id,x.tenant_id,x.station_id,x.observation_id,x.source_version_sha256,
     x.evidence_sha256,octet_length(x.source_content) AS source_bytes,
     octet_length(x.evidence_content) AS evidence_bytes INTO cap
    FROM public.native_measurement_captures x WHERE x.id=r.capture_id;
    IF NOT FOUND OR cap.tenant_id IS DISTINCT FROM r.tenant_id
     OR cap.station_id IS DISTINCT FROM r.station_id
     OR cap.observation_id IS DISTINCT FROM r.observation_id THEN
     RAISE EXCEPTION 'rating history capture'; END IF;
    IF cap.source_bytes>262144 OR cap.evidence_bytes>262144
     OR total_bytes+cap.source_bytes+cap.evidence_bytes>16777216 THEN
     RAISE EXCEPTION 'rating history bounds'; END IF;
    total_bytes := total_bytes+cap.source_bytes+cap.evidence_bytes;
    SELECT x.source_content,x.evidence_content INTO source_raw,evidence_raw
    FROM public.native_measurement_captures x WHERE x.id=r.capture_id;
    IF encode(sha256(convert_to(source_raw,'UTF8')),'hex') IS DISTINCT FROM
     cap.source_version_sha256
     OR encode(sha256(convert_to(evidence_raw,'UTF8')),'hex') IS DISTINCT FROM
      cap.evidence_sha256 THEN
     RAISE EXCEPTION 'rating history digest'; END IF;
    nodes := nodes+public.rating_history_json_nodes(source_raw,500000-nodes);
    nodes := nodes+public.rating_history_json_nodes(evidence_raw,500000-nodes);
    capture_source := source_raw::jsonb; capture_evidence := evidence_raw::jsonb;
    IF d->>'policy' IS DISTINCT FROM 'native-rating-linear-endpoints-v2'
     OR capture_source->>'schema_version' IS DISTINCT FROM '1'
     OR (capture_source->>'tenant_id')::uuid IS DISTINCT FROM r.tenant_id
     OR (capture_source->>'station_id')::uuid IS DISTINCT FROM r.station_id
     OR capture_source->>'level_unit' IS DISTINCT FROM 'm'
     OR capture_evidence->>'schema_version' IS DISTINCT FROM '1'
     OR (capture_evidence->>'id')::uuid IS DISTINCT FROM r.capture_id
     OR capture_evidence->'source_version' IS DISTINCT FROM capture_source
     OR reference_endpoint IS DISTINCT FROM capture_source->'provider'->>'endpoint'
     OR ref->'api_station_id' IS DISTINCT FROM
      capture_source->'provider'->'api_station_id'
     OR d->'feed' IS DISTINCT FROM jsonb_build_object('kind','native_capture',
      'capture_id',r.capture_id,'capture_sha256',cap.evidence_sha256,
      'source_version_sha256',cap.source_version_sha256,'provider',capture_source->'provider',
      'level_unit','m','level_reference',capture_source->'level_reference')
     OR s-ARRAY['qc_status','qc_flags','qc_rule_version'] IS DISTINCT FROM
      capture_source->'measurement'
     THEN RAISE EXCEPTION 'rating history capture'; END IF;
   ELSE RAISE EXCEPTION 'rating history version'; END IF;
   warnings := ARRAY[]::text[];
   FOR warning IN SELECT * FROM jsonb_array_elements_text(d->'warnings') LOOP
    IF warning IS NULL OR NOT warning=ANY(known_warnings) OR warning=ANY(warnings) THEN
     RAISE EXCEPTION 'rating history code'; END IF;
    warnings := array_append(warnings,warning);
   END LOOP;
   IF ('owner_assumed_same_zero_unverified'=ANY(warnings)) IS DISTINCT FROM
    (ref ? 'basis' AND ref->>'basis'='owner_assumed_same_zero') THEN
    RAISE EXCEPTION 'rating history reference'; END IF;
   assessed := s->>'qc_status'='qc_passed' AND coalesce(s->>'qc_rule_version','')<>''
    AND jsonb_array_length(s->'qc_flags')>0;
   qc_rejected := s->>'qc_status' IN ('qc_failed','qc_suspect','missing');
   FOR flag IN SELECT * FROM jsonb_array_elements(s->'qc_flags') LOOP
    IF NOT (flag->>'status' IN ('raw','qc_passed','qc_failed','qc_suspect',
     'missing','qc_unchecked')) IS TRUE THEN
     RAISE EXCEPTION 'rating history qc'; END IF;
    qc_rejected := qc_rejected OR flag->>'status' IN
     ('qc_failed','qc_suspect','missing');
   END LOOP;
   IF ('qc_unassessed'=ANY(warnings)) IS DISTINCT FROM NOT assessed
    OR ('rating_expired_at_measurement'=ANY(warnings)) IS DISTINCT FROM
     (valid_to IS NOT NULL AND r.timestamp>=valid_to)
    OR (qc_rejected AND r.rejection IS DISTINCT FROM 'source_qc_rejected')
    OR (NOT qc_rejected AND r.rejection='source_qc_rejected') THEN
    RAISE EXCEPTION 'rating history qc'; END IF;
   rejection_code := NULL;
   IF r.rejection IS NOT NULL THEN
    rejection_code := CASE r.rejection
     WHEN 'source_qc_rejected' THEN 'source_qc_rejected'
     WHEN 'missing_source_value' THEN 'missing_source'
     WHEN 'nonfinite_source_value' THEN 'nonfinite_source'
     WHEN 'negative or nonfinite provisional discharge' THEN 'conversion_rejected'
     WHEN 'level must be finite' THEN 'conversion_rejected'
     ELSE NULL END;
    IF rejection_code IS NULL THEN RAISE EXCEPTION 'rating history code'; END IF;
   END IF;
   IF NOT qc_rejected THEN
    IF (r.rejection='missing_source_value'
     AND s->'value' IS DISTINCT FROM 'null'::jsonb)
     OR (r.rejection='nonfinite_source_value' AND NOT
      (s->'value'->>'nonfinite_float' IN ('NaN','+Inf','-Inf')) IS TRUE)
     OR (s->'value'='null'::jsonb
      AND r.rejection IS DISTINCT FROM 'missing_source_value')
     THEN RAISE EXCEPTION 'rating history result'; END IF;
   END IF;
   extrapolation := CASE WHEN 'extrapolated_below'=ANY(warnings) THEN 'below'
    WHEN 'extrapolated_above'=ANY(warnings) THEN 'above' ELSE 'in_range' END;
   IF 'extrapolated_below'=ANY(warnings) AND 'extrapolated_above'=ANY(warnings) THEN
    RAISE EXCEPTION 'rating history extrapolation'; END IF;
   low_level := NULL; high_level := NULL;
   FOR point IN SELECT * FROM jsonb_array_elements(c->'points') LOOP
    IF jsonb_typeof(point->'water_level') IS DISTINCT FROM 'number'
     OR jsonb_typeof(point->'discharge') IS DISTINCT FROM 'number'
     OR NOT ((point->>'water_level')::float8>'-Infinity'::float8
      AND (point->>'water_level')::float8<'Infinity'::float8)
     OR NOT ((point->>'discharge')::float8>=0 AND
      (point->>'discharge')::float8<'Infinity'::float8)
     THEN RAISE EXCEPTION 'rating history curve'; END IF;
    point_level := (point->>'water_level')::float8;
    low_level := least(low_level,point_level); high_level :=
     greatest(high_level,point_level);
   END LOOP;
   IF r.discharge IS NOT NULL THEN
    IF jsonb_typeof(s->'value') IS DISTINCT FROM 'number' THEN
     RAISE EXCEPTION 'rating history value'; END IF;
    source_value := (s->>'value')::float8;
    adjusted_level := source_value + CASE WHEN
     'owner_assumed_same_zero_unverified'=ANY(warnings)
     THEN 0 ELSE (ref->>'offset_m')::float8 END;
    IF adjusted_level IS NULL OR NOT (adjusted_level>'-Infinity'::float8
     AND adjusted_level<'Infinity'::float8) THEN RAISE EXCEPTION
      'rating history reference'; END IF;
    IF extrapolation IS DISTINCT FROM (CASE WHEN adjusted_level<low_level THEN 'below'
     WHEN adjusted_level>high_level THEN 'above' ELSE 'in_range' END) THEN
     RAISE EXCEPTION 'rating history extrapolation'; END IF;
   END IF;
   IF extrapolation<>'in_range' THEN
    IF r.discharge IS NULL THEN RAISE EXCEPTION 'rating history extrapolation'; END IF;
    SELECT jsonb_agg(q.point ORDER BY (q.point->>'water_level')::float8) INTO
     expected_endpoints
    FROM (SELECT value AS point FROM jsonb_array_elements(c->'points')
     ORDER BY CASE WHEN extrapolation='below' THEN (value->>'water_level')::float8
       ELSE -(value->>'water_level')::float8 END LIMIT 2) q;
    IF d->'endpoints' IS DISTINCT FROM expected_endpoints THEN
     RAISE EXCEPTION 'rating history extrapolation'; END IF;
   ELSIF d->'endpoints' IS DISTINCT FROM 'null'::jsonb THEN
    RAISE EXCEPTION 'rating history extrapolation'; END IF;
   day_codes:=day_codes||warnings;
   IF valid_to IS NOT NULL AND valid_to<=parent.issued_at THEN
    day_codes:=array_append(day_codes,'rating_expired_at_issue'); END IF;
   IF r.discharge IS NOT NULL THEN
    accepted_n:=accepted_n+1;
    first_at:=least(first_at,r.timestamp); last_at:=greatest(last_at,r.timestamp);
    IF NOT assessed THEN unassessed_n:=unassessed_n+1; END IF;
    IF extrapolation<>'in_range' THEN extrapolated_n:=extrapolated_n+1; END IF;
   ELSE rejected_n:=rejected_n+1; END IF;
   rows_json := rows_json || jsonb_build_object('timestamp',r.timestamp,'day',day_time,
    'discharge',r.discharge,'rejection',rejection_code,'warnings',to_jsonb(warnings),
    'curve_id',r.rating_curve_id,'curve_digest',curve_hash,
    'valid_from',valid_from,'valid_to',valid_to);
  END LOOP;
  SELECT array_agg(DISTINCT code ORDER BY code) INTO recomputed_codes
   FROM unnest(day_codes) AS code;
  SELECT array_agg(code ORDER BY code) INTO day_codes
   FROM jsonb_array_elements_text(day->'warnings') AS code;
  IF accepted_n=0 OR (day->>'accepted')::integer IS DISTINCT FROM accepted_n
   OR (day->>'rejected')::integer IS DISTINCT FROM rejected_n
   OR (day->>'extrapolated')::integer IS DISTINCT FROM extrapolated_n
   OR (day->>'unassessed')::integer IS DISTINCT FROM unassessed_n
   OR (day->>'assessed')::integer IS DISTINCT FROM accepted_n-unassessed_n
   OR (day->>'first')::timestamptz IS DISTINCT FROM first_at
   OR (day->>'last')::timestamptz IS DISTINCT FROM last_at
   OR day_codes IS DISTINCT FROM recomputed_codes THEN
   RAISE EXCEPTION 'rating history support'; END IF;
 END LOOP;
 out_json := jsonb_build_object('forecast_id',parent.id,'station_id',parent.station_id,
  'tenant_id',parent.tenant_id,'issued_at',parent.issued_at,'artifact_id',parent.model_artifact_id,
  'start',start_time,'end',stop_time,'days',days_json,'samples',rows_json,'omissions',omissions);
 IF octet_length(out_json::text)>2097152 THEN RAISE EXCEPTION
  'rating history bounds'; END IF;
 RETURN out_json;
EXCEPTION WHEN data_exception THEN
 -- Native JSON/UUID/time/number cast errors can echo protected source text.
 -- Preserve cancellation, explicit bounds, and fixed integrity refusals unchanged.
 RAISE EXCEPTION 'rating history malformed evidence' USING ERRCODE='22000';
END $$;
REVOKE ALL ON FUNCTION public.forecast_rating_input_history(uuid) FROM PUBLIC;
DO $$ DECLARE role_name text;
BEGIN
 FOREACH role_name IN ARRAY ARRAY['sapphire_api','sapphire_worker','sapphire_operator',
  'sapphire_publication_health'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
   EXECUTE format('REVOKE ALL ON FUNCTION '
     'public.forecast_rating_input_history(uuid) FROM %I',role_name);
   EXECUTE format('REVOKE ALL ON FUNCTION '
     'public.rating_history_json_nodes(text,integer) FROM %I',role_name);
  END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='sapphire_api') THEN
  GRANT EXECUTE ON FUNCTION public.forecast_rating_input_history(uuid) TO sapphire_api;
 END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.forecast_rating_input_history(uuid)")
    op.execute("DROP FUNCTION public.rating_history_json_nodes(text,integer)")
