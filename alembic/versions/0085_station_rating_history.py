"""Bounded stored rating-result history for scoped API reads."""

from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE INDEX ix_rating_derived_observation_latest
 ON public.rating_derived_discharge
 (tenant_id,station_id,observation_id,stored_order DESC)
""")
    op.execute(r"""
CREATE FUNCTION public.station_rating_history(
 tenant uuid, station uuid, lower_bound timestamptz, upper_bound timestamptz,
 enabled boolean
) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET TimeZone='UTC' AS $$
DECLARE
 o record; r record; cap record; body jsonb; src jsonb; curve jsonb; ref jsonb;
 live jsonb; capture_source jsonb; raw text; curve_raw text; curve_hash text;
 source_raw text; evidence_raw text; ref_raw text; item jsonb;
 source_ids uuid[];
 -- Append to an expandable typed array; encode JSON once in the final envelope.
 result jsonb[] := ARRAY[]::jsonb[]; candidates integer; source_count integer;
 bytes integer:=0; nodes integer:=0; reason text; outcome text;
 assessed boolean; seen_curves text[]:=ARRAY[]::text[];
 validated_refs jsonb := '{}';
 endpoint_parts text[]; authority_parts text[]; authority text; path_part text;
 host text; suffix text; port_number integer; reference_endpoint text;
BEGIN
 IF tenant IS NULL OR station IS NULL OR lower_bound IS NULL OR upper_bound IS NULL
  OR enabled IS NULL OR lower_bound>=upper_bound
  OR upper_bound-lower_bound>interval '7 days'

    OR NOT EXISTS(SELECT 1 FROM public.stations s WHERE s.id=station
    AND s.tenant_id=tenant)
 THEN RAISE EXCEPTION 'station history scope or bounds'; END IF;

 -- Bound physical candidates before parsing any protected evidence. STABLE gives
 -- all reads in this projection the calling statement's snapshot.
 SELECT coalesce(array_agg(q.observation_id),ARRAY[]::uuid[]) INTO source_ids
 FROM (
  SELECT u.observation_id FROM public.bafu_unit_observation_read_state u
  WHERE u.station_id=station AND u.parameter='water_level' AND u.source='measured'
   AND u.timestamp>=lower_bound AND u.timestamp<upper_bound
   AND NOT u.held AND (NOT u.evidence_required OR u.canonical)
  LIMIT 10001
 ) q;
 source_count:=cardinality(source_ids);
 IF source_count>10000 THEN RAISE EXCEPTION 'station history bounds'; END IF;
 SELECT count(*) INTO candidates FROM (
  SELECT d.fingerprint FROM public.rating_derived_discharge d
  WHERE d.tenant_id=tenant AND d.station_id=station
   AND d.observation_id=ANY(source_ids)
  LIMIT 10001
 ) bounded;
 IF candidates+source_count>10000 THEN RAISE EXCEPTION 'station history bounds'; END IF;

 FOR o IN SELECT x.*, count(*) OVER (PARTITION BY x.timestamp) AS duplicates
  FROM public.observations x WHERE x.id=ANY(source_ids)
  ORDER BY x.timestamp,x.id
 LOOP
  assessed:=o.qc_status='qc_passed' AND coalesce(o.qc_rule_version,'')<>''
   AND jsonb_array_length(coalesce(nullif(o.qc_flags,'null'::jsonb),'[]'))>0;
  item:=jsonb_build_object('timestamp',o.timestamp,'source','rating_derived',
   'data_use','provisional_rating','value',NULL,'outcome','held',
   'reason','unreconciled','source_qc_status',o.qc_status,
   'source_assessment',CASE WHEN assessed THEN 'assessed' ELSE 'unassessed' END,
   'warnings','[]'::jsonb,'curve',NULL,'stored_at',NULL,'policy',NULL);
  IF NOT enabled OR o.duplicates>1 THEN
   item:=item||jsonb_build_object('reason',CASE WHEN NOT enabled THEN 'not_configured'
    ELSE 'ambiguous_source' END);
   result:=array_append(result,item); CONTINUE;
  END IF;
  SELECT d.fingerprint,d.rating_curve_id,d.file_id,d.timestamp,d.discharge,
   d.rejection,d.capture_id,d.evidence_version,d.captured_at,
   octet_length(d.content) AS bytes INTO r
   FROM public.rating_derived_discharge d
   WHERE d.tenant_id=tenant AND d.station_id=station AND d.observation_id=o.id
   ORDER BY d.stored_order DESC LIMIT 1;
  IF NOT FOUND THEN result:=array_append(result,item); CONTINUE; END IF;
  IF r.bytes>262144 OR bytes+r.bytes>33554432 THEN
   RAISE EXCEPTION 'station history bounds'; END IF;
  bytes:=bytes+r.bytes;
  SELECT d.content INTO raw FROM public.rating_derived_discharge d
   WHERE d.fingerprint=r.fingerprint;
  IF encode(sha256(convert_to(raw,'UTF8')),'hex') IS DISTINCT FROM r.fingerprint THEN
   RAISE EXCEPTION 'station history digest'; END IF;
  curve_raw:=(raw::json->'curve')::text;
  curve_hash:=encode(sha256(convert_to(curve_raw,'UTF8')),'hex');
  nodes:=nodes+public.rating_history_sample_json_nodes(raw,500000-nodes,
   CASE WHEN curve_hash=ANY(seen_curves) THEN curve_raw ELSE NULL END);
  IF NOT curve_hash=ANY(seen_curves) THEN
   seen_curves:=array_append(seen_curves,curve_hash);
   IF cardinality(seen_curves)>64 THEN RAISE EXCEPTION 'station history bounds'; END IF;
  END IF;
  body:=raw::jsonb; src:=body->'source'; curve:=body->'curve'; ref:=body->'reference';
  IF jsonb_typeof(src) IS DISTINCT FROM 'object'
   OR NOT src ?& ARRAY['id','station_id','timestamp','parameter','value','source',
    'rating_curve_id','rating_curve_correction_version','delivery_id','qc_status',
    'qc_flags','qc_rule_version','created_at']
   OR src->>'qc_status' NOT IN
    ('raw','qc_passed','qc_failed','qc_suspect','qc_unchecked','missing')
   OR jsonb_typeof(src->'qc_status') IS DISTINCT FROM 'string'
   OR jsonb_typeof(src->'qc_rule_version') NOT IN ('null','string')
   OR jsonb_typeof(curve) IS DISTINCT FROM 'object'
   OR jsonb_typeof(ref) IS DISTINCT FROM 'object'
   OR (body->>'tenant_id')::uuid IS DISTINCT FROM tenant
   OR (body->>'station_id')::uuid IS DISTINCT FROM station
   OR (body->>'observation_id')::uuid IS DISTINCT FROM o.id
   OR (body->>'rating_curve_id')::uuid IS DISTINCT FROM r.rating_curve_id
   OR (body->>'file_id')::uuid IS DISTINCT FROM r.file_id
   OR (src->>'id')::uuid IS DISTINCT FROM o.id
   OR (src->>'station_id')::uuid IS DISTINCT FROM station
   OR (src->>'timestamp')::timestamptz IS DISTINCT FROM r.timestamp
   OR src->>'parameter' IS DISTINCT FROM 'water_level'
   OR src->>'source' IS DISTINCT FROM 'measured'
   OR (curve->>'id')::uuid IS DISTINCT FROM r.rating_curve_id
   OR (curve->>'station_id')::uuid IS DISTINCT FROM station
   OR (ref->>'station_id')::uuid IS DISTINCT FROM station
   OR (ref->>'tenant_id')::uuid IS DISTINCT FROM tenant
   OR (ref->>'rating_curve_id')::uuid IS DISTINCT FROM r.rating_curve_id
   OR body->>'level_unit' IS DISTINCT FROM 'm'
   OR body->>'discharge_unit' IS DISTINCT FROM 'm3/s'
   OR jsonb_typeof(body->'discharge') IS DISTINCT FROM
     (CASE WHEN r.discharge IS NULL THEN 'null' ELSE 'number' END)
   OR (body->>'discharge')::float8 IS DISTINCT FROM r.discharge
   OR body->>'rejection' IS DISTINCT FROM r.rejection
   OR jsonb_typeof(body->'warnings') IS DISTINCT FROM 'array'
   OR jsonb_typeof(src->'qc_flags') IS DISTINCT FROM 'array'
   OR curve->>'version' IS NULL OR curve->>'valid_from' IS NULL
   OR (curve->>'valid_from')::timestamptz>r.timestamp
   OR (curve->>'valid_to')::timestamptz<=(curve->>'valid_from')::timestamptz
  THEN RAISE EXCEPTION 'station history identity'; END IF;
  ref_raw:=ref->'curve'->>'content';
  IF ref_raw IS NULL OR octet_length(ref_raw)>262144 THEN
   RAISE EXCEPTION 'station history reference'; END IF;
  IF validated_refs->>curve_hash IS DISTINCT FROM ref_raw THEN
   nodes:=nodes+public.rating_history_json_nodes(ref_raw,500000-nodes);
   IF ref_raw::jsonb IS DISTINCT FROM curve THEN
    RAISE EXCEPTION 'station history reference'; END IF;
   validated_refs:=validated_refs||jsonb_build_object(curve_hash,ref_raw);
  END IF;
  IF r.evidence_version=1 THEN
   IF r.capture_id IS NOT NULL
    OR body->>'policy' IS DISTINCT FROM 'native-rating-linear-endpoints-v1'
    OR (body->'feed'->>'tenant_id')::uuid IS DISTINCT FROM tenant
    OR (body->'feed'->>'station_id')::uuid IS DISTINCT FROM station
    OR body->'feed'->>'endpoint' IS DISTINCT FROM ref->>'endpoint'
    OR body->'feed'->'api_station_id' IS DISTINCT FROM ref->'api_station_id'
    OR NOT (body->'feed'->>'evidence_reference' ~ '[^[:space:]]') IS TRUE
    OR NOT ((body->'feed'->>'start')::timestamptz<=r.timestamp
     AND r.timestamp<(body->'feed'->>'end')::timestamptz) IS TRUE
   THEN RAISE EXCEPTION 'station history custody'; END IF;
  ELSIF r.evidence_version=2 THEN
    -- Match canonical_provider_endpoint: HTTPS, ASCII/lowercase host, default443,
    -- unchanged path except its trailing slash. Provider text stays private.
    endpoint_parts:=regexp_match(ref->>'endpoint',
     '^[Hh][Tt][Tt][Pp][Ss]://([^/?#@[:space:]]+)(/[^?#[:space:]]*)?([?])?(#)?$');
    IF endpoint_parts IS NULL THEN RAISE EXCEPTION 'station history reference'; END IF;
    authority:=endpoint_parts[1]; path_part:=coalesce(endpoint_parts[2],'');
    IF octet_length(authority)<>length(authority) THEN
     RAISE EXCEPTION 'station history reference'; END IF;
    IF left(authority,1)='[' THEN
     authority_parts:=regexp_match(authority,'^\[([^\[\]]+)\](:([0-9]*))?$');
     IF authority_parts IS NULL OR strpos(authority_parts[1],':')=0 THEN
      RAISE EXCEPTION 'station history reference'; END IF;
     host:='['||lower(authority_parts[1])||']';
    ELSE
     authority_parts:=regexp_match(authority,'^([^:\[\]]+)(:([0-9]*))?$');
     IF authority_parts IS NULL THEN
      RAISE EXCEPTION 'station history reference'; END IF;
     host:=lower(authority_parts[1]);
    END IF;
    suffix:='';
    IF nullif(authority_parts[3],'') IS NOT NULL THEN
     port_number:=authority_parts[3]::integer;
     IF port_number>65535 THEN RAISE EXCEPTION 'station history reference'; END IF;
     IF port_number<>443 THEN suffix:=':'||port_number::text; END IF;
    END IF;
    reference_endpoint:='https://'||host||suffix||rtrim(path_part,'/')||'/';
   SELECT c.tenant_id,c.station_id,c.observation_id,c.source_version_sha256,
    c.evidence_sha256,octet_length(c.source_content) AS source_bytes,
    octet_length(c.evidence_content) AS evidence_bytes INTO cap
    FROM public.native_measurement_captures c WHERE c.id=r.capture_id;
   IF NOT FOUND OR cap.tenant_id IS DISTINCT FROM tenant
    OR cap.station_id IS DISTINCT FROM station

    OR cap.observation_id IS DISTINCT FROM o.id
    THEN RAISE EXCEPTION 'station history capture'; END IF;
   bytes:=bytes+cap.source_bytes+cap.evidence_bytes;
   IF cap.source_bytes>262144 OR cap.evidence_bytes>262144
    OR bytes>33554432
    THEN RAISE EXCEPTION 'station history bounds'; END IF;
   SELECT c.source_content,c.evidence_content INTO source_raw,evidence_raw
    FROM public.native_measurement_captures c WHERE c.id=r.capture_id;
   nodes:=nodes+public.rating_history_json_nodes(source_raw,500000-nodes);
   nodes:=nodes+public.rating_history_json_nodes(evidence_raw,500000-nodes);
   capture_source:=source_raw::jsonb;
   IF encode(sha256(convert_to(source_raw,'UTF8')),'hex')
    IS DISTINCT FROM cap.source_version_sha256

    OR encode(sha256(convert_to(evidence_raw,'UTF8')),'hex')
    IS DISTINCT FROM cap.evidence_sha256
    OR body->>'policy' IS DISTINCT FROM 'native-rating-linear-endpoints-v2'
    OR (capture_source->>'tenant_id')::uuid IS DISTINCT FROM tenant
    OR (capture_source->>'station_id')::uuid IS DISTINCT FROM station
    OR capture_source->>'level_unit' IS DISTINCT FROM 'm'
    OR reference_endpoint IS DISTINCT FROM capture_source->'provider'->>'endpoint'
    OR ref->'api_station_id' IS DISTINCT FROM
     capture_source->'provider'->'api_station_id'
    OR evidence_raw::jsonb->'source_version' IS DISTINCT FROM capture_source
    OR (evidence_raw::jsonb->>'id')::uuid IS DISTINCT FROM r.capture_id

    OR src-ARRAY['qc_status','qc_flags','qc_rule_version']
    IS DISTINCT FROM capture_source->'measurement'
    OR body->'feed' IS DISTINCT FROM jsonb_build_object('kind','native_capture',
      'capture_id',r.capture_id,'capture_sha256',cap.evidence_sha256,
      'source_version_sha256',cap.source_version_sha256,'provider',capture_source->'provider',
      'level_unit','m','level_reference',capture_source->'level_reference')
   THEN RAISE EXCEPTION 'station history capture'; END IF;
  ELSE RAISE EXCEPTION 'station history evidence version'; END IF;

  -- Match typed floats/times, not their JSON decimal or UTC spellings. NULL
  -- database QC flags deserialize to [] in the producer.
  live:=to_jsonb(o)-'duplicates';
  live:=live||jsonb_build_object('qc_flags',coalesce(nullif(o.qc_flags,'null'::jsonb),'[]'::jsonb));
  reason:=NULL;
  IF src-ARRAY['value','timestamp','created_at'] IS DISTINCT FROM
     live-ARRAY['value','timestamp','created_at']

   OR NOT (CASE
    WHEN o.value IS NULL THEN src->'value'='null'::jsonb
    WHEN o.value='NaN'::float8 THEN
     src->'value'='{"nonfinite_float":"NaN"}'::jsonb
    WHEN o.value='Infinity'::float8 THEN
     src->'value'='{"nonfinite_float":"+Inf"}'::jsonb
    WHEN o.value='-Infinity'::float8 THEN
     src->'value'='{"nonfinite_float":"-Inf"}'::jsonb
    WHEN jsonb_typeof(src->'value')='number' THEN (src->>'value')::float8=o.value
    ELSE false END) IS TRUE
   OR (src->>'timestamp')::timestamptz IS DISTINCT FROM o.timestamp
   OR (src->>'created_at')::timestamptz IS DISTINCT FROM o.created_at
  THEN reason:='source_changed'; END IF;
  IF reason IS NOT NULL THEN
   result:=array_append(result,item||jsonb_build_object('reason',reason));
   CONTINUE;
  END IF;
  outcome:=CASE WHEN r.discharge IS NULL THEN 'rejected' ELSE 'accepted' END;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(body->'warnings') w(code)
   WHERE code NOT IN ('owner_assumed_same_zero_unverified',
    'rating_expired_at_measurement',
    'qc_unassessed','extrapolated_below','extrapolated_above')) THEN
   RAISE EXCEPTION 'station history warning'; END IF;
  item:=item||jsonb_build_object('outcome',outcome,'value',r.discharge,
   'reason',CASE WHEN r.rejection IS NULL THEN NULL
    WHEN r.rejection='source_qc_rejected'
    THEN r.rejection ELSE 'conversion_rejected' END,
   'warnings',body->'warnings','policy',body->>'policy','stored_at',r.captured_at,
   'curve',jsonb_build_object('curve_id',r.rating_curve_id,'content_sha256',curve_hash,
     'version',curve->>'version','valid_from',curve->>'valid_from','valid_to',curve->>'valid_to'));
  result:=array_append(result,item);
 END LOOP;
 RETURN jsonb_build_object('tenant_id',tenant,'station_id',station,
  'candidate_count',source_count+candidates,'samples',result);
EXCEPTION WHEN data_exception THEN
 RAISE EXCEPTION 'station history malformed evidence' USING ERRCODE='22000';
END $$;
REVOKE ALL ON FUNCTION public.station_rating_history(
 uuid,uuid,timestamptz,timestamptz,boolean) FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='sapphire_api') THEN
  GRANT EXECUTE ON FUNCTION public.station_rating_history(
   uuid,uuid,timestamptz,timestamptz,boolean) TO sapphire_api;
 END IF;
END $$;
""")


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION public.station_rating_history"
        "(uuid,uuid,timestamptz,timestamptz,boolean)"
    )
    op.drop_index(
        "ix_rating_derived_observation_latest", table_name="rating_derived_discharge"
    )
