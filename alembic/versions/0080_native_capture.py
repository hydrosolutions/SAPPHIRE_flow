"""Atomic native source captures and version-bound sighting evidence."""

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE TABLE native_measurement_captures (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, station_id uuid NOT NULL,
 observation_id uuid NOT NULL, source_version_sha256 text NOT NULL,
 source_content text NOT NULL, evidence_content text NOT NULL,
 evidence_sha256 text NOT NULL, registered_at timestamptz NOT NULL,
 FOREIGN KEY (station_id,tenant_id) REFERENCES stations(id,tenant_id) ON DELETE
  RESTRICT,
 FOREIGN KEY (observation_id,station_id) REFERENCES observations(id,station_id) ON
  DELETE RESTRICT,
 CONSTRAINT uq_native_capture_version UNIQUE(tenant_id,station_id,observation_id,
  source_version_sha256),
 CONSTRAINT uq_native_capture_scope UNIQUE(id,tenant_id,station_id),
 CONSTRAINT ck_native_capture_source_digest
  CHECK(source_version_sha256=encode(sha256(convert_to(source_content,'UTF8')),'hex')),
 CONSTRAINT ck_native_capture_evidence_digest
  CHECK(evidence_sha256=encode(sha256(convert_to(evidence_content,'UTF8')),'hex'))
);
CREATE INDEX ix_native_capture_station_observation ON
  native_measurement_captures(station_id,observation_id);
CREATE TABLE native_capture_batches (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL, station_id uuid NOT NULL,
 flow_run_id uuid, context_content text NOT NULL, context_sha256 text NOT NULL,
 accepted_input_sha256 text NOT NULL, commit_content text NOT NULL,
 commit_sha256 text NOT NULL, recorded_at timestamptz NOT NULL,
 FOREIGN KEY (station_id,tenant_id) REFERENCES stations(id,tenant_id) ON DELETE
  RESTRICT,
 CONSTRAINT uq_native_capture_batch_scope UNIQUE(id,tenant_id,station_id),
 CONSTRAINT ck_native_capture_batch_context_digest
  CHECK(context_sha256=encode(sha256(convert_to(context_content,'UTF8')),'hex')),
 CONSTRAINT ck_native_capture_batch_commit_digest
  CHECK(commit_sha256=encode(sha256(convert_to(commit_content,'UTF8')),'hex'))
);
CREATE TABLE native_capture_sightings (
 batch_id uuid NOT NULL, capture_id uuid NOT NULL, tenant_id uuid NOT NULL, station_id
  uuid NOT NULL,
 PRIMARY KEY(batch_id,capture_id),
 FOREIGN KEY(batch_id,tenant_id,station_id) REFERENCES native_capture_batches(id,
  tenant_id,station_id) ON DELETE RESTRICT,
 FOREIGN KEY(capture_id,tenant_id,station_id) REFERENCES native_measurement_captures(id,
  tenant_id,station_id) ON DELETE RESTRICT
);
CREATE FUNCTION public.native_capture_insert_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE s jsonb; e jsonb; m jsonb; p jsonb; o public.observations%ROWTYPE; t uuid;
BEGIN
 s := NEW.source_content::jsonb; e := NEW.evidence_content::jsonb;
 m := s->'measurement'; p := s->'provider';
 IF jsonb_typeof(s) IS DISTINCT FROM 'object'
 OR NOT s ?& ARRAY['schema_version','tenant_id','station_id','measurement','provider',
  'level_unit','level_reference']
 OR s - ARRAY['schema_version','tenant_id','station_id','measurement','provider',
  'level_unit','level_reference'] <> '{}'::jsonb
 OR jsonb_typeof(e) IS DISTINCT FROM 'object'
 OR NOT e ?& ARRAY['schema_version','id','source_version']
 OR e - ARRAY['schema_version','id','source_version'] <> '{}'::jsonb
 OR jsonb_typeof(p) IS DISTINCT FROM 'object'
 OR NOT p ?& ARRAY['provider','endpoint','api_station_id']
 OR p - ARRAY['provider','endpoint','api_station_id'] <> '{}'::jsonb
 OR s->>'schema_version' IS DISTINCT FROM '1'
 OR (s->>'tenant_id')::uuid IS DISTINCT FROM NEW.tenant_id
 OR (s->>'station_id')::uuid IS DISTINCT FROM NEW.station_id
 OR (m->>'id')::uuid IS DISTINCT FROM NEW.observation_id
 OR (m->>'station_id')::uuid IS DISTINCT FROM NEW.station_id
 OR e->>'schema_version' IS DISTINCT FROM '1'
 OR (e->>'id')::uuid IS DISTINCT FROM NEW.id
 OR e->'source_version' IS DISTINCT FROM s
 OR s->>'level_unit' IS DISTINCT FROM 'm'
 OR s->>'level_reference' IS NULL
 OR s->>'level_reference' NOT IN ('unknown','gauge_zero','masl')
 OR p->>'provider' IS DISTINCT FROM 'bipad'
 OR p->>'endpoint' IS NULL OR p->>'endpoint' !~ '^https://[^[:space:]@?#]+/$'
 OR p->>'api_station_id' IS NULL OR p->>'api_station_id' !~ '^[1-9][0-9]*$'
 THEN RAISE EXCEPTION 'invalid native capture scope or identity'; END IF;
 IF jsonb_typeof(m) IS DISTINCT FROM 'object'
 OR NOT m ?& ARRAY['id','station_id','timestamp','parameter','value','source',
  'created_at','delivery_id','rating_curve_id','rating_curve_correction_version']
 OR m - ARRAY['id','station_id','timestamp','parameter','value','source','created_at',
  'delivery_id','rating_curve_id','rating_curve_correction_version'] <> '{}'::jsonb
 OR jsonb_typeof(m->'value') IS DISTINCT FROM 'number'
 OR m->>'timestamp' IS NULL OR m->>'timestamp' !~ '(Z|[+]00:00)$'
 OR m->>'created_at' IS NULL OR m->>'created_at' !~ '(Z|[+]00:00)$'
 THEN RAISE EXCEPTION 'invalid native capture measurement shape'; END IF;
 SELECT * INTO o FROM public.observations WHERE id=NEW.observation_id AND
  station_id=NEW.station_id FOR SHARE;
 SELECT tenant_id INTO t FROM public.stations WHERE id=NEW.station_id;
 IF o.id IS NULL OR t IS DISTINCT FROM NEW.tenant_id
 OR m->>'parameter' IS DISTINCT FROM 'water_level' OR m->>'source' IS DISTINCT FROM
  'measured'
 OR m->>'parameter' IS DISTINCT FROM o.parameter OR m->>'source' IS DISTINCT FROM
  o.source
 OR (m->>'timestamp')::timestamptz IS DISTINCT FROM o.timestamp
 OR (m->>'created_at')::timestamptz IS DISTINCT FROM o.created_at
 OR (m->>'value')::float8 IS DISTINCT FROM o.value
 OR NOT ((m->>'value')::float8 > '-Infinity'::float8 AND (m->>'value')::float8 <
  'Infinity'::float8)
 OR m->>'delivery_id' IS DISTINCT FROM o.delivery_id
 OR (m->>'rating_curve_id')::uuid IS DISTINCT FROM o.rating_curve_id
 OR m->>'rating_curve_correction_version' IS DISTINCT FROM
  o.rating_curve_correction_version
 THEN RAISE EXCEPTION 'native capture does not match current raw source'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.native_capture_insert_guard() FROM PUBLIC;
CREATE TRIGGER native_capture_insert_guard BEFORE INSERT ON native_measurement_captures
  FOR EACH ROW EXECUTE FUNCTION public.native_capture_insert_guard();
CREATE FUNCTION public.native_capture_batch_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE c jsonb; r jsonb;
BEGIN
 c:=NEW.context_content::jsonb; r:=NEW.commit_content::jsonb;
 IF jsonb_typeof(c) IS DISTINCT FROM 'object' OR jsonb_typeof(r) IS DISTINCT FROM
  'object'
 OR NOT r ?& ARRAY['batch_id','written_ids','observed_capture_ids','inserted_count',
  'updated_count','unchanged_count']
 OR r - ARRAY['batch_id','written_ids','observed_capture_ids','inserted_count',
  'updated_count','unchanged_count'] <> '{}'::jsonb
 OR r->>'inserted_count' IS NULL OR r->>'inserted_count' !~ '^(0|[1-9][0-9]*)$'
 OR r->>'updated_count' IS NULL OR r->>'updated_count' !~ '^(0|[1-9][0-9]*)$'
 OR r->>'unchanged_count' IS NULL OR r->>'unchanged_count' !~ '^(0|[1-9][0-9]*)$'
 OR c->'schema_version' IS DISTINCT FROM '1'::jsonb
 OR NOT c ?& ARRAY['schema_version','id','tenant_id','station_id','flow_run_id',
  'provider','requested_intervals','request_started_at','retrieved_at','outcome',
  'request_count','accepted_bytes','accepted_input_sha256','level_unit',
  'level_reference']
 OR c - ARRAY['schema_version','id','tenant_id','station_id','flow_run_id',
  'provider','requested_intervals','request_started_at','retrieved_at','outcome',
  'request_count','accepted_bytes','accepted_input_sha256','level_unit',
  'level_reference'] <> '{}'::jsonb
 OR c->>'level_unit' IS DISTINCT FROM 'm'
 OR jsonb_typeof(c->'level_reference') IS DISTINCT FROM 'string'
 OR c->>'level_reference' NOT IN ('unknown','gauge_zero','masl')
 OR jsonb_typeof(c->'outcome') IS DISTINCT FROM 'string'
 OR c->>'outcome' NOT IN ('complete','failed','no_request')
 OR jsonb_typeof(c->'provider') IS DISTINCT FROM 'object'
 OR NOT (c->'provider') ?& ARRAY['provider','endpoint','api_station_id']
 OR (c->'provider') - ARRAY['provider','endpoint','api_station_id'] <> '{}'::jsonb
 OR c->'provider'->>'provider' IS DISTINCT FROM 'bipad'
 OR jsonb_typeof(c->'provider'->'endpoint') IS DISTINCT FROM 'string'
 OR c->'provider'->>'endpoint' !~ '^https://[^/?#@[:space:]]+(/[^?#[:space:]]*)?/$'
 OR jsonb_typeof(c->'provider'->'api_station_id') IS DISTINCT FROM 'number'
 OR c->'provider'->>'api_station_id' !~ '^[1-9][0-9]*$'
 OR jsonb_typeof(c->'requested_intervals') IS DISTINCT FROM 'array'
 OR c->>'retrieved_at' IS NULL
 OR c->>'retrieved_at' !~ '[+]00:00$'
 OR ((c->'request_count') <> 'null'::jsonb AND (
  jsonb_typeof(c->'request_count') <> 'number'
  OR c->>'request_count' !~ '^(0|[1-9][0-9]*)$'))
 OR ((c->'accepted_bytes') <> 'null'::jsonb AND (
  jsonb_typeof(c->'accepted_bytes') <> 'number'
  OR c->>'accepted_bytes' !~ '^(0|[1-9][0-9]*)$'))
 OR jsonb_typeof(r->'inserted_count') IS DISTINCT FROM 'number'
 OR jsonb_typeof(r->'updated_count') IS DISTINCT FROM 'number'
 OR jsonb_typeof(r->'unchanged_count') IS DISTINCT FROM 'number'
 OR (c->>'id')::uuid IS DISTINCT FROM NEW.id
 OR (c->>'tenant_id')::uuid IS DISTINCT FROM NEW.tenant_id
 OR (c->>'station_id')::uuid IS DISTINCT FROM NEW.station_id
 OR (c->>'flow_run_id')::uuid IS DISTINCT FROM NEW.flow_run_id
 OR c->>'accepted_input_sha256' IS DISTINCT FROM NEW.accepted_input_sha256
 OR (r->>'batch_id')::uuid IS DISTINCT FROM NEW.id
 OR NEW.accepted_input_sha256 !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(r->'observed_capture_ids') IS DISTINCT FROM 'array'
 OR jsonb_typeof(r->'written_ids') IS DISTINCT FROM 'array'
 OR (r->>'inserted_count')::int < 0 OR (r->>'updated_count')::int < 0 OR
  (r->>'unchanged_count')::int < 0
 OR (r->>'inserted_count')::int + (r->>'updated_count')::int <>
  jsonb_array_length(r->'written_ids')
 OR (r->>'inserted_count')::int + (r->>'updated_count')::int +
  (r->>'unchanged_count')::int <> jsonb_array_length(r->'observed_capture_ids')
 THEN RAISE EXCEPTION 'invalid native capture batch result'; END IF;
 IF EXISTS (SELECT 1 FROM jsonb_array_elements(c->'requested_intervals') AS x(v)
  WHERE jsonb_typeof(v) <> 'object'
   OR NOT v ?& ARRAY['lower_exclusive','upper_exclusive']
   OR v - ARRAY['lower_exclusive','upper_exclusive'] <> '{}'::jsonb
   OR v->>'lower_exclusive' IS NULL OR v->>'upper_exclusive' IS NULL
   OR v->>'lower_exclusive' !~ '[+]00:00$'
   OR v->>'upper_exclusive' !~ '[+]00:00$'
   OR (v->>'lower_exclusive')::timestamptz >=
      (v->>'upper_exclusive')::timestamptz)
 OR (jsonb_array_length(c->'requested_intervals') > 0 AND (
   c->>'request_started_at' IS NULL OR c->>'request_started_at' !~ '[+]00:00$'
   OR (c->>'request_started_at')::timestamptz >
      (c->>'retrieved_at')::timestamptz))
 OR (jsonb_array_length(c->'requested_intervals')=0 AND
   c->>'request_started_at' IS NOT NULL)
 OR (c->>'request_count'='0' AND (jsonb_array_length(c->'requested_intervals')<>0
   OR c->>'request_started_at' IS NOT NULL))
 OR ((c->>'request_count'='0' OR c->>'outcome'='no_request') AND
   COALESCE(c->>'accepted_bytes','0')<>'0')
 OR (c->>'outcome'='complete' AND jsonb_array_length(c->'requested_intervals')=0)
 OR (c->>'outcome'<>'complete' AND jsonb_array_length(r->'observed_capture_ids')>0)
 OR (c->>'outcome'='no_request' AND (
   jsonb_array_length(c->'requested_intervals')<>0
   OR c->>'request_started_at' IS NOT NULL
   OR COALESCE(c->>'request_count','0')<>'0'))
 THEN RAISE EXCEPTION 'invalid native capture request facts'; END IF;
 PERFORM (c->>'retrieved_at')::timestamptz;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.native_capture_batch_guard() FROM PUBLIC;
CREATE TRIGGER native_capture_batch_guard BEFORE INSERT ON native_capture_batches FOR
  EACH ROW EXECUTE FUNCTION public.native_capture_batch_guard();
CREATE FUNCTION public.native_capture_batch_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE r jsonb; n integer;
BEGIN
 r:=NEW.commit_content::jsonb;
 SELECT count(*) INTO n FROM public.native_capture_sightings WHERE batch_id=NEW.id;
 IF n <> jsonb_array_length(r->'observed_capture_ids') OR EXISTS (
  SELECT 1 FROM jsonb_array_elements_text(r->'observed_capture_ids') AS x(id)
  WHERE NOT EXISTS (SELECT 1 FROM public.native_capture_sightings s WHERE
  s.batch_id=NEW.id AND s.capture_id=x.id::uuid)
 ) OR EXISTS (
  SELECT 1 FROM jsonb_array_elements_text(r->'written_ids') AS w(id)
  WHERE NOT EXISTS (SELECT 1 FROM public.native_capture_sightings s JOIN
  public.native_measurement_captures c ON c.id=s.capture_id WHERE s.batch_id=NEW.id AND
  c.observation_id=w.id::uuid)
 ) OR EXISTS (
  SELECT 1 FROM public.native_capture_sightings s JOIN
   public.native_measurement_captures c ON c.id=s.capture_id
  WHERE s.batch_id=NEW.id AND (
   c.source_content::jsonb->'provider' IS DISTINCT FROM
    NEW.context_content::jsonb->'provider'
   OR c.source_content::jsonb->'level_reference' IS DISTINCT FROM
    NEW.context_content::jsonb->'level_reference'
   OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(
      NEW.context_content::jsonb->'requested_intervals') AS x(v)
     WHERE (c.source_content::jsonb->'measurement'->>'timestamp')::timestamptz
       > (v->>'lower_exclusive')::timestamptz
       AND (c.source_content::jsonb->'measurement'->>'timestamp')::timestamptz
       < (v->>'upper_exclusive')::timestamptz))
 ) THEN RAISE EXCEPTION 'native capture batch sightings are incomplete'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.native_capture_batch_complete() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER native_capture_batch_complete AFTER INSERT ON
  native_capture_batches DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
  public.native_capture_batch_complete();
CREATE TRIGGER native_measurement_captures_immutable BEFORE UPDATE OR DELETE ON
  native_measurement_captures FOR EACH ROW EXECUTE FUNCTION
  public.native_rating_immutable();
CREATE TRIGGER native_capture_batches_immutable BEFORE UPDATE OR DELETE ON
  native_capture_batches FOR EACH ROW EXECUTE FUNCTION public.native_rating_immutable();
CREATE TRIGGER native_capture_sightings_immutable BEFORE UPDATE OR DELETE ON
  native_capture_sightings FOR EACH ROW EXECUTE FUNCTION
  public.native_rating_immutable();
CREATE TRIGGER native_measurement_captures_no_truncate BEFORE TRUNCATE ON
  native_measurement_captures FOR EACH STATEMENT EXECUTE FUNCTION
  public.native_rating_immutable();
CREATE TRIGGER native_capture_batches_no_truncate BEFORE TRUNCATE ON
  native_capture_batches FOR EACH STATEMENT EXECUTE FUNCTION
  public.native_rating_immutable();
CREATE TRIGGER native_capture_sightings_no_truncate BEFORE TRUNCATE ON
  native_capture_sightings FOR EACH STATEMENT EXECUTE FUNCTION
  public.native_rating_immutable();

ALTER TABLE rating_derived_discharge
 ADD COLUMN capture_id uuid,
 ADD COLUMN evidence_version integer NOT NULL DEFAULT 1,
 ADD COLUMN stored_order bigint GENERATED ALWAYS AS IDENTITY,
 ADD COLUMN stored_order_basis text NOT NULL DEFAULT 'migration_registration',
 ADD CONSTRAINT ck_rating_derived_order_basis CHECK(stored_order_basis IN
  ('database_insertion_sequence','migration_registration')),
 ADD CONSTRAINT fk_rating_derived_capture FOREIGN KEY
  (capture_id,tenant_id,station_id) REFERENCES native_measurement_captures
  (id,tenant_id,station_id) ON DELETE RESTRICT,
 ADD CONSTRAINT ck_rating_derived_evidence_version CHECK
  ((evidence_version=1 AND capture_id IS NULL) OR
   (evidence_version=2 AND capture_id IS NOT NULL));
ALTER TABLE rating_derived_discharge ALTER COLUMN stored_order_basis
 SET DEFAULT 'database_insertion_sequence';
CREATE UNIQUE INDEX uq_rating_derived_stored_order ON
 rating_derived_discharge(stored_order);
CREATE FUNCTION public.native_rating_capture_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE d jsonb; c public.native_measurement_captures%ROWTYPE;
BEGIN
 d:=NEW.content::jsonb;
 IF NEW.stored_order_basis IS DISTINCT FROM 'database_insertion_sequence'
 THEN RAISE EXCEPTION 'derived insertion ordering must be database assigned'; END IF;
 IF NEW.evidence_version=1 THEN
  IF d->>'policy' IS DISTINCT FROM 'native-rating-linear-endpoints-v1'
  THEN RAISE EXCEPTION 'invalid legacy derived evidence policy'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO c FROM public.native_measurement_captures WHERE id=NEW.capture_id
  AND tenant_id=NEW.tenant_id AND station_id=NEW.station_id
  AND observation_id=NEW.observation_id;
 IF NOT FOUND OR NEW.evidence_version<>2
  OR d->>'policy' IS DISTINCT FROM 'native-rating-linear-endpoints-v2'
  OR d->'feed' IS DISTINCT FROM jsonb_build_object(
   'kind','native_capture','capture_id',c.id,
   'capture_sha256',c.evidence_sha256,
   'source_version_sha256',c.source_version_sha256,
   'provider',c.source_content::jsonb->'provider','level_unit','m',
   'level_reference',c.source_content::jsonb->'level_reference')
  OR ((d->'source') - ARRAY['qc_status','qc_flags','qc_rule_version'])
   IS DISTINCT FROM (c.source_content::jsonb->'measurement')
  OR d->>'tenant_id' IS DISTINCT FROM NEW.tenant_id::text
  OR d->>'station_id' IS DISTINCT FROM NEW.station_id::text
  OR d->>'observation_id' IS DISTINCT FROM NEW.observation_id::text
  OR d->>'rating_curve_id' IS DISTINCT FROM NEW.rating_curve_id::text
  OR d->>'file_id' IS DISTINCT FROM NEW.file_id::text
  OR (d->'source'->>'timestamp')::timestamptz IS DISTINCT FROM NEW.timestamp
  OR (d->>'discharge')::float8 IS DISTINCT FROM NEW.discharge
  OR d->>'rejection' IS DISTINCT FROM NEW.rejection
 THEN RAISE EXCEPTION 'invalid native capture derived evidence'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.native_rating_capture_guard() FROM PUBLIC;
CREATE TRIGGER native_rating_capture_guard BEFORE INSERT ON
 rating_derived_discharge FOR EACH ROW EXECUTE FUNCTION
 public.native_rating_capture_guard();

CREATE FUNCTION public.native_rating_omission_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE r jsonb; item jsonb;
BEGIN
 r:=NEW.input_lineage::jsonb->'rating_input';
 IF r IS NULL OR NOT (r ? 'unreconciled_sources') THEN RETURN NEW; END IF;
 IF jsonb_typeof(r->'unreconciled_sources') IS DISTINCT FROM 'array'
 THEN RAISE EXCEPTION 'invalid native rating omission shape'; END IF;
 IF jsonb_array_length(r->'unreconciled_sources')=0
 THEN RAISE EXCEPTION 'empty native rating omissions must be omitted'; END IF;
 IF EXISTS (SELECT 1 FROM jsonb_array_elements(r->'unreconciled_sources') AS x(v)
  GROUP BY v->>'observation_id' HAVING count(*)>1)
 THEN RAISE EXCEPTION 'duplicate native rating omitted source'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(r->'unreconciled_sources') LOOP
  IF jsonb_typeof(item) IS DISTINCT FROM 'object'
   OR NOT item ?& ARRAY['observation_id','timestamp','source_snapshot_sha256']
   OR item - ARRAY['observation_id','timestamp','source_snapshot_sha256']<>'{}'::jsonb
   OR jsonb_typeof(item->'observation_id') IS DISTINCT FROM 'string'
   OR jsonb_typeof(item->'timestamp') IS DISTINCT FROM 'string'
   OR jsonb_typeof(item->'source_snapshot_sha256') IS DISTINCT FROM 'string'
   OR item->>'source_snapshot_sha256' !~ '^[0-9a-f]{64}$'
   OR item->>'timestamp' !~ '[+]00:00$'
   OR (date_trunc('day',(item->>'timestamp')::timestamptz AT TIME ZONE 'UTC')
       AT TIME ZONE 'UTC') + interval '24 hours' > NEW.issued_at
   OR NOT EXISTS (SELECT 1 FROM public.observations o
     WHERE o.id=(item->>'observation_id')::uuid AND o.station_id=NEW.station_id
      AND o.timestamp=(item->>'timestamp')::timestamptz
      AND o.parameter='water_level' AND o.source='measured')
  THEN RAISE EXCEPTION 'invalid native rating omitted source scope'; END IF;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(r->'days') AS d(day)
   CROSS JOIN LATERAL jsonb_array_elements_text(day->'fingerprints') AS f(digest)
   JOIN public.rating_derived_discharge s ON s.fingerprint=f.digest
   WHERE s.observation_id=(item->>'observation_id')::uuid)
  THEN RAISE EXCEPTION 'native rating source cannot be consumed and omitted'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.native_rating_omission_guard() FROM PUBLIC;
CREATE TRIGGER native_rating_forecast_omissions BEFORE INSERT ON forecasts
 FOR EACH ROW WHEN (NEW.data_use='provisional_rating')
 EXECUTE FUNCTION public.native_rating_omission_guard();
CREATE TRIGGER native_rating_rejected_omissions BEFORE INSERT ON rejected_forecasts
 FOR EACH ROW WHEN (NEW.data_use='provisional_rating')
 EXECUTE FUNCTION public.native_rating_omission_guard();

CREATE TABLE provisional_evaluation_grants (
 id uuid PRIMARY KEY,
 tenant_id uuid NOT NULL,
 station_id uuid NOT NULL,
 access_token_id uuid REFERENCES access_tokens(id) ON DELETE RESTRICT,
 human_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
 granted_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 granted_by text NOT NULL,
 revoked_at timestamptz,
 revoked_by text,
 FOREIGN KEY(station_id,tenant_id) REFERENCES stations(id,tenant_id) ON DELETE RESTRICT,
 CONSTRAINT ck_evaluation_grant_subject CHECK(num_nonnulls(access_token_id,human_user_id)=1),
 CONSTRAINT ck_evaluation_grant_actor CHECK(length(btrim(granted_by))>0),
 CONSTRAINT ck_evaluation_grant_revocation CHECK(
  (revoked_at IS NULL AND revoked_by IS NULL) OR
  (revoked_at IS NOT NULL AND revoked_at>=granted_at AND revoked_by IS NOT NULL
   AND length(btrim(revoked_by))>0))
);
CREATE UNIQUE INDEX uq_evaluation_token_active ON provisional_evaluation_grants
 (access_token_id,station_id) WHERE revoked_at IS NULL AND access_token_id IS NOT NULL;
CREATE UNIQUE INDEX uq_evaluation_human_active ON provisional_evaluation_grants
 (human_user_id,station_id) WHERE revoked_at IS NULL AND human_user_id IS NOT NULL;
CREATE FUNCTION public.provisional_evaluation_grant_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['revoked_at','revoked_by']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['revoked_at','revoked_by'])
   OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
   OR NEW.revoked_at IS DISTINCT FROM statement_timestamp()
   OR NEW.revoked_by IS NULL OR length(btrim(NEW.revoked_by))=0
  THEN RAISE EXCEPTION 'evaluation grants only allow attributed one-way revocation';
  END IF;
  RETURN NEW;
 END IF;
 IF NEW.granted_at IS DISTINCT FROM statement_timestamp()
  OR NEW.revoked_at IS NOT NULL OR NEW.revoked_by IS NOT NULL
 THEN RAISE EXCEPTION 'evaluation grants require current creation time'; END IF;
 IF NEW.human_user_id IS NOT NULL THEN
  IF NOT EXISTS (SELECT 1 FROM public.users u WHERE u.id=NEW.human_user_id
      AND u.tenant_id=NEW.tenant_id AND u.is_active)
  THEN RAISE EXCEPTION 'evaluation grant human scope is unavailable'; END IF;
 ELSIF NOT EXISTS (SELECT 1 FROM public.access_tokens t
   WHERE t.id=NEW.access_token_id AND t.disabled_at IS NULL
    AND t.expires_at>statement_timestamp() AND (
     t.role='admin' OR (t.tenant_id=NEW.tenant_id AND (
      t.scope_mode='tenant' OR EXISTS (SELECT 1 FROM public.access_token_stations s
       WHERE s.token_id=t.id AND s.station_id=NEW.station_id)))))
 THEN RAISE EXCEPTION 'evaluation grant token scope is unavailable'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.provisional_evaluation_grant_guard() FROM PUBLIC;
CREATE TRIGGER provisional_evaluation_grant_guard BEFORE INSERT OR UPDATE ON
 provisional_evaluation_grants FOR EACH ROW EXECUTE FUNCTION
 public.provisional_evaluation_grant_guard();
CREATE TRIGGER provisional_evaluation_grants_no_delete BEFORE DELETE ON
 provisional_evaluation_grants FOR EACH ROW EXECUTE FUNCTION public.native_rating_immutable();
CREATE TRIGGER provisional_evaluation_grants_no_truncate BEFORE TRUNCATE ON
 provisional_evaluation_grants FOR EACH STATEMENT EXECUTE FUNCTION public.native_rating_immutable();
CREATE VIEW provisional_evaluation_active_grants WITH (security_barrier=true) AS
 SELECT g.id,g.tenant_id,g.station_id,g.access_token_id,g.human_user_id
 FROM public.provisional_evaluation_grants g
 WHERE g.revoked_at IS NULL AND (
  EXISTS (SELECT 1 FROM public.users u WHERE u.id=g.human_user_id
   AND u.tenant_id=g.tenant_id AND u.is_active)
  OR EXISTS (SELECT 1 FROM public.access_tokens t WHERE t.id=g.access_token_id
   AND t.disabled_at IS NULL AND t.expires_at>statement_timestamp()
   AND (t.role='admin' OR (t.tenant_id=g.tenant_id AND (t.scope_mode='tenant'
    OR EXISTS (SELECT 1 FROM public.access_token_stations s
      WHERE s.token_id=t.id AND s.station_id=g.station_id))))));
REVOKE ALL ON provisional_evaluation_grants,provisional_evaluation_active_grants FROM PUBLIC;
REVOKE ALL ON SEQUENCE rating_derived_discharge_stored_order_seq FROM PUBLIC;
REVOKE ALL ON native_measurement_captures,native_capture_batches,
  native_capture_sightings FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='sapphire_api') THEN
  REVOKE ALL ON native_measurement_captures,native_capture_batches,
  native_capture_sightings FROM sapphire_api;
  REVOKE ALL ON provisional_evaluation_grants FROM sapphire_api;
  REVOKE ALL ON SEQUENCE rating_derived_discharge_stored_order_seq FROM sapphire_api;
  GRANT SELECT ON provisional_evaluation_active_grants TO sapphire_api;
 END IF;
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='sapphire_operator') THEN
  REVOKE ALL ON provisional_evaluation_grants FROM sapphire_operator;
 END IF;
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='sapphire_worker') THEN
  REVOKE ALL ON provisional_evaluation_grants,provisional_evaluation_active_grants FROM sapphire_worker;
  REVOKE INSERT ON rating_derived_discharge FROM sapphire_worker;
  GRANT INSERT(fingerprint,tenant_id,station_id,observation_id,rating_curve_id,
   file_id,timestamp,discharge,rejection,content,captured_at,capture_id,evidence_version)
   ON rating_derived_discharge TO sapphire_worker;
  GRANT USAGE ON SEQUENCE rating_derived_discharge_stored_order_seq TO sapphire_worker;
  GRANT SELECT,INSERT ON native_measurement_captures,native_capture_batches,
  native_capture_sightings TO sapphire_worker;
 END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT FROM native_capture_batches) OR EXISTS(SELECT FROM
  native_measurement_captures)
  OR EXISTS (SELECT FROM provisional_evaluation_grants)
  OR EXISTS (SELECT FROM forecasts WHERE data_use='provisional_rating' AND
   (input_lineage::jsonb->'rating_input') ? 'unreconciled_sources')
  OR EXISTS (SELECT FROM rejected_forecasts WHERE data_use='provisional_rating' AND
   (input_lineage::jsonb->'rating_input') ? 'unreconciled_sources')
 THEN RAISE EXCEPTION 'native capture evidence prevents downgrade'; END IF;
END $$;
DROP VIEW provisional_evaluation_active_grants;
DROP TABLE provisional_evaluation_grants;
DROP FUNCTION public.provisional_evaluation_grant_guard();
DROP TRIGGER native_rating_forecast_omissions ON forecasts;
DROP TRIGGER native_rating_rejected_omissions ON rejected_forecasts;
DROP FUNCTION public.native_rating_omission_guard();
DROP TRIGGER native_rating_capture_guard ON rating_derived_discharge;
DROP FUNCTION public.native_rating_capture_guard();
DROP INDEX uq_rating_derived_stored_order;
ALTER TABLE rating_derived_discharge DROP CONSTRAINT fk_rating_derived_capture,
 DROP CONSTRAINT ck_rating_derived_evidence_version,
 DROP COLUMN capture_id, DROP COLUMN evidence_version, DROP COLUMN stored_order,
 DROP COLUMN stored_order_basis;
DROP TABLE native_capture_sightings;
DROP TABLE native_capture_batches;
DROP TABLE native_measurement_captures;
DROP FUNCTION public.native_capture_insert_guard();
DROP FUNCTION public.native_capture_batch_guard();
DROP FUNCTION public.native_capture_batch_complete();
""")
