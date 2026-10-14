"""Nepal21 restricted delivery authority and protected expiry tests.

Revision ID: 0072
Revises: 0071
"""

# ruff: noqa: E501

from collections.abc import Sequence

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
CREATE TABLE public.restricted_delivery_releases (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES public.tenants(id),
    network text NOT NULL CHECK (network = 'dhm'),
    cohort text NOT NULL CHECK (cohort = 'nepal21'),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    station_set_sha256 text NOT NULL CHECK (station_set_sha256 ~ '^[0-9a-f]{64}$'),
    canonicalization_policy_sha256 text NULL CHECK (
        canonicalization_policy_sha256 IS NULL
        OR canonicalization_policy_sha256 ~ '^[0-9a-f]{64}$'
    ),
    expected_predecessor_delivery_id text NOT NULL CHECK (
        expected_predecessor_delivery_id = 'dhm-barkhk-2026-09-08'
    ),
    expected_predecessor_semantic_sha256 text NOT NULL CHECK (
        expected_predecessor_semantic_sha256 ~ '^[0-9a-f]{64}$'
    ),
    allowed_new_delivery_id text NOT NULL CHECK (
        allowed_new_delivery_id = 'dhm-nepal21-' || substring(manifest_sha256 from 1 for 16)
    ),
    expires_at timestamptz NULL,
    status text NOT NULL CHECK (status IN ('authorized', 'used', 'revoked')),
    created_at timestamptz NOT NULL,
    used_at timestamptz NULL,
    created_by_run_id uuid NULL,
    used_by_run_id uuid NULL,
    CHECK ((status = 'used') = (used_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_restricted_release_active ON public.restricted_delivery_releases
    (tenant_id, network, cohort, manifest_sha256) WHERE status = 'authorized';
CREATE UNIQUE INDEX uq_restricted_release_delivery ON public.restricted_delivery_releases
    (tenant_id, network, cohort, allowed_new_delivery_id);

CREATE TABLE public.restricted_delivery_release_witnesses (
    release_id uuid NOT NULL REFERENCES public.restricted_delivery_releases(id) ON DELETE RESTRICT,
    witness_kind text NOT NULL CHECK (witness_kind IN ('manifest_catalog', 'canonicalization_policy', 'qc_policy')),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    content_bytes bytea NOT NULL CHECK (octet_length(content_bytes) BETWEEN 1 AND 10485760),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (release_id, witness_kind)
);

CREATE TABLE public.restricted_delivery_generations (
    id uuid PRIMARY KEY,
    release_id uuid NOT NULL REFERENCES public.restricted_delivery_releases(id) ON DELETE RESTRICT,
    tenant_id uuid NOT NULL REFERENCES public.tenants(id),
    network text NOT NULL CHECK (network = 'dhm'),
    cohort text NOT NULL CHECK (cohort = 'nepal21'),
    delivery_id text NOT NULL,
    predecessor_delivery_id text NOT NULL CHECK (predecessor_delivery_id = 'dhm-barkhk-2026-09-08'),
    predecessor_semantic_sha256 text NOT NULL CHECK (predecessor_semantic_sha256 ~ '^[0-9a-f]{64}$'),
    preimage_sha256 text NULL CHECK (preimage_sha256 IS NULL OR preimage_sha256 ~ '^[0-9a-f]{64}$'),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    station_set_sha256 text NOT NULL CHECK (station_set_sha256 ~ '^[0-9a-f]{64}$'),
    canonicalization_policy_sha256 text NULL CHECK (
        canonicalization_policy_sha256 IS NULL
        OR canonicalization_policy_sha256 ~ '^[0-9a-f]{64}$'
    ),
    state text NOT NULL CHECK (state IN ('building', 'active_pending_qc', 'active_qc_complete', 'aborted')),
    immutable_content_sha256 text NULL CHECK (
        immutable_content_sha256 IS NULL OR immutable_content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    qc_policy_sha256 text NULL CHECK (qc_policy_sha256 IS NULL OR qc_policy_sha256 ~ '^[0-9a-f]{64}$'),
    qc_completion_sha256 text NULL CHECK (qc_completion_sha256 IS NULL OR qc_completion_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    published_at timestamptz NULL,
    qc_completed_at timestamptz NULL,
    created_by_run_id uuid NULL,
    published_by_run_id uuid NULL,
    qc_run_id uuid NULL,
    UNIQUE (id, tenant_id, cohort),
    UNIQUE (id, tenant_id),
    UNIQUE (id, tenant_id, network, cohort),
    UNIQUE (tenant_id, network, cohort, delivery_id),
    CHECK ((state = 'building') = (published_at IS NULL AND immutable_content_sha256 IS NULL)),
    CHECK (state <> 'active_qc_complete' OR (qc_completed_at IS NOT NULL AND qc_policy_sha256 IS NOT NULL AND qc_completion_sha256 IS NOT NULL))
);
CREATE UNIQUE INDEX uq_restricted_generation_active ON public.restricted_delivery_generations
    (tenant_id, network, cohort) WHERE state IN ('active_pending_qc', 'active_qc_complete');

CREATE TABLE public.dhm_delivery_origins (
    id uuid PRIMARY KEY,
    generation_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    cohort text NOT NULL CHECK (cohort = 'nepal21'),
    origin_kind text NOT NULL CHECK (origin_kind IN ('DFL','RT','GHT_HASH_ONLY','PREIMAGE','SYNTHETIC_TEST')),
    source_root_label text NOT NULL CHECK (source_root_label IN ('old','new','preimage','synthetic_test')),
    relative_path text NOT NULL,
    file_sha256 text NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    parser_version text NOT NULL,
    row_or_block_ordinal integer NULL,
    origin_payload_sha256 text NOT NULL CHECK (origin_payload_sha256 ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY (generation_id, tenant_id, cohort)
        REFERENCES public.restricted_delivery_generations(id, tenant_id, cohort) ON DELETE RESTRICT,
    UNIQUE (id, generation_id, tenant_id, origin_kind),
    CHECK ((origin_kind IN ('DFL','RT')) = (row_or_block_ordinal IS NOT NULL)),
    CHECK (origin_kind <> 'GHT_HASH_ONLY' OR source_root_label = 'synthetic_test' OR row_or_block_ordinal IS NOT NULL)
);

CREATE TABLE public.dhm_origin_sets (
    generation_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    origin_set_sha256 text NOT NULL CHECK (origin_set_sha256 ~ '^[0-9a-f]{64}$'),
    member_count integer NOT NULL CHECK (member_count > 0),
    set_kind text NOT NULL CHECK (set_kind IN ('DFL_RECORD','RT_DECLARATION','GHT_HASH_ONLY','PREIMAGE')),
    PRIMARY KEY (generation_id, tenant_id, origin_set_sha256),
    FOREIGN KEY (generation_id, tenant_id)
        REFERENCES public.restricted_delivery_generations(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE public.dhm_origin_set_members (
    generation_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    origin_set_sha256 text NOT NULL,
    origin_id uuid NOT NULL,
    origin_kind text NOT NULL,
    member_ordinal integer NOT NULL CHECK (member_ordinal >= 0),
    FOREIGN KEY (generation_id, tenant_id, origin_set_sha256)
        REFERENCES public.dhm_origin_sets(generation_id, tenant_id, origin_set_sha256) ON DELETE RESTRICT,
    FOREIGN KEY (origin_id, generation_id, tenant_id, origin_kind)
        REFERENCES public.dhm_delivery_origins(id, generation_id, tenant_id, origin_kind) ON DELETE RESTRICT,
    UNIQUE (generation_id, tenant_id, origin_set_sha256, origin_id),
    UNIQUE (generation_id, tenant_id, origin_set_sha256, member_ordinal)
);

CREATE TABLE public.dhm_rating_declarations (
    id uuid PRIMARY KEY,
    generation_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    station_id uuid NOT NULL,
    cohort text NOT NULL CHECK (cohort = 'nepal21'),
    station_code text NOT NULL,
    from_local_date date NOT NULL,
    to_local_date_inclusive date NOT NULL,
    rating_type_label text NOT NULL,
    from_stage_m double precision NOT NULL CHECK (from_stage_m > '-Infinity'::float8 AND from_stage_m < 'Infinity'::float8),
    raw_declaration_payload_json jsonb NOT NULL,
    declaration_content_sha256 text NOT NULL CHECK (declaration_content_sha256 ~ '^[0-9a-f]{64}$'),
    function_payload_json jsonb NULL,
    function_equivalence_sha256 text NULL CHECK (function_equivalence_sha256 IS NULL OR function_equivalence_sha256 ~ '^[0-9a-f]{64}$'),
    native_curve_payload_json jsonb NULL,
    inventory_semantic_sha256 text NULL CHECK (inventory_semantic_sha256 IS NULL OR inventory_semantic_sha256 ~ '^[0-9a-f]{64}$'),
    shape_state text NOT NULL CHECK (shape_state IN ('raw_usable','duplicate_point_needs_approval','canonicalized_usable','decreasing_invalid')),
    canonicalization_approval_id uuid NULL,
    canonicalization_approval_sha256 text NULL CHECK (canonicalization_approval_sha256 IS NULL OR canonicalization_approval_sha256 ~ '^[0-9a-f]{64}$'),
    usable_rating_curve_id uuid NULL,
    origin_set_sha256 text NOT NULL CHECK (origin_set_sha256 ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY (generation_id, tenant_id, cohort)
        REFERENCES public.restricted_delivery_generations(id, tenant_id, cohort) ON DELETE RESTRICT,
    FOREIGN KEY (usable_rating_curve_id, station_id) REFERENCES public.rating_curves(id, station_id) ON DELETE RESTRICT,
    FOREIGN KEY (generation_id, tenant_id, origin_set_sha256)
        REFERENCES public.dhm_origin_sets(generation_id, tenant_id, origin_set_sha256) ON DELETE RESTRICT,
    UNIQUE (id, generation_id, tenant_id, station_id),
    UNIQUE (id, generation_id, station_id),
    UNIQUE (generation_id, station_id, declaration_content_sha256),
    CHECK (to_local_date_inclusive >= from_local_date),
    CHECK ((shape_state IN ('raw_usable','canonicalized_usable')) = (function_payload_json IS NOT NULL AND function_equivalence_sha256 IS NOT NULL)),
    CHECK ((usable_rating_curve_id IS NOT NULL) = (native_curve_payload_json IS NOT NULL)),
    CHECK (shape_state <> 'canonicalized_usable' OR (canonicalization_approval_id IS NOT NULL AND canonicalization_approval_sha256 IS NOT NULL))
);

CREATE TABLE public.dhm_delivery_observation_attributions (
    generation_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    observation_id uuid NOT NULL,
    station_id uuid NOT NULL,
    cohort text NOT NULL CHECK (cohort = 'nepal21'),
    station_code text NOT NULL,
    origin_set_sha256 text NOT NULL CHECK (origin_set_sha256 ~ '^[0-9a-f]{64}$'),
    rating_declaration_id uuid NULL,
    rating_curve_id uuid NULL,
    attribution_state text NOT NULL CHECK (attribution_state IN ('selected','held_missing_curve','held_invalid_latest','held_equal_start_tie','held_native_mismatch','not_applicable')),
    local_date date NOT NULL,
    reported_q double precision NOT NULL CHECK (reported_q > '-Infinity'::float8 AND reported_q < 'Infinity'::float8),
    FOREIGN KEY (generation_id, tenant_id, cohort)
        REFERENCES public.restricted_delivery_generations(id, tenant_id, cohort) ON DELETE RESTRICT,
    FOREIGN KEY (observation_id, station_id) REFERENCES public.observations(id, station_id) ON DELETE RESTRICT,
    FOREIGN KEY (rating_declaration_id, generation_id, tenant_id, station_id)
        REFERENCES public.dhm_rating_declarations(id, generation_id, tenant_id, station_id) ON DELETE RESTRICT,
    FOREIGN KEY (rating_curve_id, station_id) REFERENCES public.rating_curves(id, station_id) ON DELETE RESTRICT,
    FOREIGN KEY (generation_id, tenant_id, origin_set_sha256)
        REFERENCES public.dhm_origin_sets(generation_id, tenant_id, origin_set_sha256) ON DELETE RESTRICT,
    UNIQUE (generation_id, tenant_id, observation_id),
    CHECK ((attribution_state = 'selected') = (rating_declaration_id IS NOT NULL AND rating_curve_id IS NOT NULL))
);

CREATE TABLE public.expired_rating_test_permissions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES public.tenants(id),
    generation_id uuid NOT NULL,
    cohort text NOT NULL CHECK (cohort = 'nepal21'),
    permission_version integer NOT NULL CHECK (permission_version > 0),
    state text NOT NULL CHECK (state IN ('disabled','enabled')),
    owner_authorization_sha256 text NOT NULL CHECK (owner_authorization_sha256 ~ '^[0-9a-f]{64}$'),
    precedence_sha256 text NOT NULL CHECK (precedence_sha256 ~ '^[0-9a-f]{64}$'),
    selection_manifest_sha256 text NOT NULL CHECK (selection_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    enabled_at timestamptz NULL,
    disabled_at timestamptz NULL,
    enabled_by_run_id uuid NULL,
    disabled_by_run_id uuid NULL,
    FOREIGN KEY (generation_id, tenant_id, cohort)
        REFERENCES public.restricted_delivery_generations(id, tenant_id, cohort) ON DELETE RESTRICT,
    UNIQUE (tenant_id, generation_id, cohort, permission_version),
    CHECK ((state = 'enabled') = (enabled_at IS NOT NULL AND disabled_at IS NULL)),
    CHECK ((state = 'disabled') OR disabled_at IS NULL)
);
CREATE UNIQUE INDEX uq_expired_rating_one_enabled ON public.expired_rating_test_permissions
    (tenant_id, generation_id, cohort) WHERE state = 'enabled';

CREATE OR REPLACE FUNCTION public.sf_assert_restricted_witness() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE rel public.restricted_delivery_releases;
BEGIN
    SELECT * INTO rel FROM public.restricted_delivery_releases WHERE id = NEW.release_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'missing restricted delivery release';
    END IF;
    IF encode(sha256(NEW.content_bytes), 'hex') <> NEW.content_sha256 THEN
        RAISE EXCEPTION 'restricted witness bytes do not match declared hash';
    END IF;
    IF NEW.witness_kind = 'manifest_catalog' AND NEW.content_sha256 <> rel.manifest_sha256 THEN
        RAISE EXCEPTION 'manifest witness hash does not match release';
    END IF;
    IF NEW.witness_kind = 'canonicalization_policy' AND (
        rel.canonicalization_policy_sha256 IS NULL
        OR NEW.content_sha256 <> rel.canonicalization_policy_sha256
    ) THEN
        RAISE EXCEPTION 'canonicalization witness hash does not match release';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_restricted_witness_assert BEFORE INSERT ON public.restricted_delivery_release_witnesses
    FOR EACH ROW EXECUTE FUNCTION public.sf_assert_restricted_witness();

CREATE OR REPLACE FUNCTION public.sf_restricted_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'restricted delivery catalog rows are immutable after insert'
        USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER trg_restricted_witness_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.restricted_delivery_release_witnesses
    FOR EACH STATEMENT EXECUTE FUNCTION public.sf_restricted_immutable();
CREATE TRIGGER trg_dhm_origins_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.dhm_delivery_origins
    FOR EACH STATEMENT EXECUTE FUNCTION public.sf_restricted_immutable();
CREATE TRIGGER trg_dhm_origin_sets_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.dhm_origin_sets
    FOR EACH STATEMENT EXECUTE FUNCTION public.sf_restricted_immutable();
CREATE TRIGGER trg_dhm_origin_members_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.dhm_origin_set_members
    FOR EACH STATEMENT EXECUTE FUNCTION public.sf_restricted_immutable();
CREATE TRIGGER trg_dhm_declarations_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.dhm_rating_declarations
    FOR EACH STATEMENT EXECUTE FUNCTION public.sf_restricted_immutable();
CREATE TRIGGER trg_dhm_attributions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.dhm_delivery_observation_attributions
    FOR EACH STATEMENT EXECUTE FUNCTION public.sf_restricted_immutable();

CREATE OR REPLACE FUNCTION public.sf_restricted_generation_child_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE child_generation uuid;
DECLARE child_tenant uuid;
BEGIN
    child_generation := NEW.generation_id;
    child_tenant := NEW.tenant_id;
    IF NOT EXISTS (
        SELECT 1 FROM public.restricted_delivery_generations g
        WHERE g.id = child_generation
          AND g.tenant_id = child_tenant
          AND g.cohort = 'nepal21'
          AND g.state = 'building'
    ) THEN
        RAISE EXCEPTION 'restricted delivery catalog rows may only be inserted while generation is building'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_dhm_origins_insert_guard BEFORE INSERT ON public.dhm_delivery_origins
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_child_insert_guard();
CREATE TRIGGER trg_dhm_origin_sets_insert_guard BEFORE INSERT ON public.dhm_origin_sets
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_child_insert_guard();
CREATE TRIGGER trg_dhm_origin_members_insert_guard BEFORE INSERT ON public.dhm_origin_set_members
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_child_insert_guard();
CREATE TRIGGER trg_dhm_declarations_insert_guard BEFORE INSERT ON public.dhm_rating_declarations
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_child_insert_guard();
CREATE TRIGGER trg_dhm_attributions_insert_guard BEFORE INSERT ON public.dhm_delivery_observation_attributions
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_child_insert_guard();

CREATE OR REPLACE FUNCTION public.sf_active_accepted_delivery(p_station_id uuid, p_delivery_id text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public, pg_temp AS $$
    SELECT CASE
        WHEN p_delivery_id IS NULL THEN true
        WHEN p_delivery_id <> 'dhm-barkhk-2026-09-08'
             AND p_delivery_id NOT LIKE 'dhm-nepal21-%' THEN true
        WHEN p_delivery_id = 'dhm-barkhk-2026-09-08' THEN NOT EXISTS (
            SELECT 1
            FROM public.restricted_delivery_generations g
            JOIN public.restricted_delivery_releases r ON r.id = g.release_id
            JOIN public.stations s ON s.tenant_id = g.tenant_id
            WHERE s.id = p_station_id
              AND r.expected_predecessor_delivery_id = p_delivery_id
              AND g.state = 'active_qc_complete'
        )
        ELSE EXISTS (
            SELECT 1
            FROM public.restricted_delivery_generations g
            JOIN public.stations s ON s.tenant_id = g.tenant_id
            WHERE s.id = p_station_id
              AND g.delivery_id = p_delivery_id
              AND g.state = 'active_qc_complete'
        )
    END;
$$;


CREATE OR REPLACE FUNCTION public.sf_mark_restricted_release_used(p_generation_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE release_row record;
BEGIN
    SELECT r.* INTO release_row
    FROM public.restricted_delivery_generations g
    JOIN public.restricted_delivery_releases r ON r.id = g.release_id
    WHERE g.id = p_generation_id
    FOR UPDATE OF r;
    IF release_row.id IS NULL THEN
        RAISE EXCEPTION 'restricted generation release is missing';
    END IF;
    IF release_row.status <> 'authorized' THEN
        RAISE EXCEPTION 'restricted delivery release is not authorized';
    END IF;
    IF release_row.expires_at IS NOT NULL AND release_row.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'restricted delivery release is expired';
    END IF;
    UPDATE public.restricted_delivery_releases
    SET status = 'used', used_at = clock_timestamp()
    WHERE id = release_row.id AND status = 'authorized';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'restricted delivery release could not be consumed';
    END IF;
END $$;

REVOKE ALL ON FUNCTION public.sf_mark_restricted_release_used(uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.sf_restricted_release_update_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.network IS DISTINCT FROM OLD.network
           OR NEW.cohort IS DISTINCT FROM OLD.cohort
           OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256
           OR NEW.station_set_sha256 IS DISTINCT FROM OLD.station_set_sha256
           OR NEW.canonicalization_policy_sha256 IS DISTINCT FROM OLD.canonicalization_policy_sha256
           OR NEW.expected_predecessor_delivery_id IS DISTINCT FROM OLD.expected_predecessor_delivery_id
           OR NEW.expected_predecessor_semantic_sha256 IS DISTINCT FROM OLD.expected_predecessor_semantic_sha256
           OR NEW.allowed_new_delivery_id IS DISTINCT FROM OLD.allowed_new_delivery_id
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.created_by_run_id IS DISTINCT FROM OLD.created_by_run_id THEN
            RAISE EXCEPTION 'restricted delivery release authority fields are immutable';
        END IF;
        IF OLD.status = 'authorized' AND NEW.status = 'used' THEN
            IF NEW.used_at IS NULL OR OLD.used_at IS NOT NULL THEN
                RAISE EXCEPTION 'restricted delivery release used transition requires fresh used_at';
            END IF;
        ELSIF OLD.status = 'authorized' AND NEW.status = 'revoked' THEN
            IF NEW.used_at IS NOT NULL OR NEW.used_by_run_id IS NOT NULL THEN
                RAISE EXCEPTION 'restricted delivery release revocation must not set used evidence';
            END IF;
        ELSIF NEW.status IS DISTINCT FROM OLD.status
              OR NEW.used_at IS DISTINCT FROM OLD.used_at
              OR NEW.used_by_run_id IS DISTINCT FROM OLD.used_by_run_id THEN
            RAISE EXCEPTION 'restricted delivery release status transition is invalid';
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'restricted delivery release rows are immutable';
    ELSIF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'restricted delivery release rows are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_restricted_release_update_guard BEFORE UPDATE OR DELETE ON public.restricted_delivery_releases
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_release_update_guard();
CREATE TRIGGER trg_restricted_release_truncate_guard BEFORE TRUNCATE ON public.restricted_delivery_releases
    FOR EACH STATEMENT EXECUTE FUNCTION public.sf_restricted_immutable();

CREATE OR REPLACE FUNCTION public.sf_assert_restricted_publication_catalog(p_generation_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    manifest jsonb;
    expected_station_codes constant jsonb := '["225","280","350","404.7","439.7","440","445","447","450","550.05","602","602.5","604.5","610","620","647","650","652","670","684","695"]'::jsonb;
BEGIN
    IF session_user <> 'sapphire_dhm21_delivery_writer'
       AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
            WHERE n.nspname = 'public'
              AND c.relname = 'restricted_delivery_generations'
              AND r.rolname = session_user
       ) THEN
        RAISE EXCEPTION 'restricted publication requires DHM21 delivery writer or table-owner session (session_user=%, current_user=%)', session_user, current_user
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT convert_from(w.content_bytes, 'UTF8')::jsonb INTO manifest
    FROM public.restricted_delivery_generations g
    JOIN public.restricted_delivery_release_witnesses w
      ON w.release_id = g.release_id AND w.witness_kind = 'manifest_catalog'
    JOIN public.restricted_delivery_releases r ON r.id = g.release_id
    WHERE g.id = p_generation_id
      AND w.content_sha256 = r.manifest_sha256
      AND w.content_sha256 = encode(sha256(w.content_bytes), 'hex')
      AND (r.expires_at IS NULL OR r.expires_at > clock_timestamp());
    IF manifest IS NULL THEN
        RAISE EXCEPTION 'manifest catalog witness is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.restricted_delivery_generations g
        JOIN public.restricted_delivery_releases r ON r.id = g.release_id
        WHERE g.id = p_generation_id
          AND r.status = 'authorized'
          AND g.tenant_id = r.tenant_id
          AND g.network = r.network
          AND g.cohort = r.cohort
          AND g.delivery_id = r.allowed_new_delivery_id
          AND g.manifest_sha256 = r.manifest_sha256
          AND g.station_set_sha256 = r.station_set_sha256
          AND g.predecessor_delivery_id = r.expected_predecessor_delivery_id
          AND g.predecessor_semantic_sha256 = r.expected_predecessor_semantic_sha256
          AND g.canonicalization_policy_sha256 IS NOT DISTINCT FROM r.canonicalization_policy_sha256
    ) THEN
        RAISE EXCEPTION 'restricted generation parent seal does not match authorized release';
    END IF;
    IF manifest ? 'policy_scope' AND manifest->>'policy_scope' <> 'nepal21' THEN
        RAISE EXCEPTION 'manifest policy scope is not authorized for Nepal21';
    END IF;
    IF manifest->'station_codes' IS DISTINCT FROM expected_station_codes THEN
        RAISE EXCEPTION 'manifest station scope must equal Nepal21 roster';
    END IF;
    IF jsonb_typeof(manifest->'rating_declarations') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'manifest rating declarations must be explicit';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(manifest->'rating_declarations') item
        WHERE item->>'station_code' NOT IN ('225','280','350','404.7','439.7','440','445','447','450','550.05','602','602.5','604.5','610','620','647','650','652','670','684','695')
    ) THEN
        RAISE EXCEPTION 'manifest declaration station is outside Nepal21 roster';
    END IF;
    IF EXISTS (
        WITH expected AS (
            SELECT
                item->>'station_code' AS station_code,
                item->>'declaration_content_sha256' AS declaration_content_sha256,
                item->'raw_declaration_payload_json' AS raw_declaration_payload_json,
                item->>'from_local_date' AS from_local_date,
                item->>'rating_type_label' AS rating_type_label,
                item->>'shape_state' AS shape_state
            FROM jsonb_array_elements(manifest->'rating_declarations') item
        ), actual AS (
            SELECT
                d.station_code,
                d.declaration_content_sha256,
                d.raw_declaration_payload_json,
                d.from_local_date::text AS from_local_date,
                d.rating_type_label,
                d.shape_state
            FROM public.dhm_rating_declarations d
            WHERE d.generation_id = p_generation_id
        ), delta AS (
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        )
        SELECT 1 FROM delta
    ) THEN
        RAISE EXCEPTION 'persisted rating declarations differ from manifest declaration set mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.dhm_rating_declarations d
        WHERE d.generation_id = p_generation_id
          AND (
            (d.shape_state IN ('raw_usable','canonicalized_usable') AND (d.function_payload_json IS NULL OR d.function_equivalence_sha256 IS NULL))
            OR (d.shape_state NOT IN ('raw_usable','canonicalized_usable') AND (d.function_payload_json IS NOT NULL OR d.function_equivalence_sha256 IS NOT NULL))
            OR (d.usable_rating_curve_id IS NULL AND d.native_curve_payload_json IS NOT NULL)
            OR (d.usable_rating_curve_id IS NOT NULL AND d.native_curve_payload_json IS NULL)
          )
    ) THEN
        RAISE EXCEPTION 'raw effective native declaration state mismatch';
    END IF;
    IF EXISTS (
        WITH structured AS (
            SELECT d.*,
                   (
                     SELECT jsonb_agg(
                         jsonb_build_object('stage_m', raw_point.stage_m, 'discharge_m3s', raw_point.discharge_m3s)
                         ORDER BY raw_point.stage_m
                     )
                     FROM (
                         SELECT DISTINCT
                             (p->>'stage_m')::numeric AS stage_m,
                             (p->>'discharge_m3s')::numeric AS discharge_m3s
                         FROM jsonb_array_elements(d.raw_declaration_payload_json->'points') p
                     ) raw_point
                   ) AS derived_points,
                   (
                     SELECT count(*) FROM jsonb_array_elements(d.raw_declaration_payload_json->'points')
                   ) AS raw_point_count,
                   (
                     SELECT count(*)
                     FROM (
                         SELECT DISTINCT p->>'stage_m', p->>'discharge_m3s'
                         FROM jsonb_array_elements(d.raw_declaration_payload_json->'points') p
                     ) distinct_raw_point
                   ) AS distinct_point_count,
                   (
                     SELECT count(*)
                     FROM (
                         SELECT p->>'stage_m' AS stage_m
                         FROM jsonb_array_elements(d.raw_declaration_payload_json->'points') p
                         GROUP BY p->>'stage_m'
                         HAVING count(DISTINCT p->>'discharge_m3s') > 1
                     ) conflicting_stage
                   ) AS conflicting_stage_count
            FROM public.dhm_rating_declarations d
            WHERE d.generation_id = p_generation_id
              AND d.function_payload_json IS NOT NULL
        )
        SELECT 1
        FROM structured d
        WHERE d.raw_declaration_payload_json->>'codec' IS DISTINCT FROM 'dhm21-canonical-json-v1'
           OR d.raw_declaration_payload_json->>'station_code' IS DISTINCT FROM d.station_code
           OR d.raw_declaration_payload_json->>'rating_type_label' IS DISTINCT FROM d.rating_type_label
           OR d.raw_declaration_payload_json->>'from_local_date' IS DISTINCT FROM d.from_local_date::text
           OR d.raw_declaration_payload_json->>'to_local_date_inclusive' IS DISTINCT FROM d.to_local_date_inclusive::text
           OR (d.raw_declaration_payload_json->>'from_stage_m')::numeric IS DISTINCT FROM d.from_stage_m::numeric
           OR jsonb_typeof(d.raw_declaration_payload_json->'points') IS DISTINCT FROM 'array'
           OR d.raw_point_count = 0
           OR d.conflicting_stage_count <> 0
           OR d.function_payload_json->>'codec' IS DISTINCT FROM 'dhm21-canonical-json-v1'
           OR d.function_payload_json->>'station_code' IS DISTINCT FROM d.station_code
           OR d.function_payload_json->>'interpolation' IS DISTINCT FROM 'linear'
           OR d.function_payload_json->>'stage_unit' IS DISTINCT FROM 'm'
           OR d.function_payload_json->>'discharge_unit' IS DISTINCT FROM 'm3/s'
           OR d.function_payload_json->>'reference' IS DISTINCT FROM 'delivered_stage'
           OR jsonb_typeof(d.function_payload_json->'points') IS DISTINCT FROM 'array'
           OR d.function_payload_json->'points' IS DISTINCT FROM d.derived_points
           OR (d.shape_state = 'raw_usable' AND d.raw_point_count <> d.distinct_point_count)
           OR (d.shape_state = 'canonicalized_usable' AND d.raw_point_count = d.distinct_point_count)
    ) THEN
        RAISE EXCEPTION 'raw effective declaration derivation mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.dhm_rating_declarations d
        JOIN public.restricted_delivery_generations g ON g.id = d.generation_id
        WHERE d.generation_id = p_generation_id
          AND d.shape_state = 'canonicalized_usable'
          AND (
            g.canonicalization_policy_sha256 IS NULL
            OR NOT EXISTS (
                SELECT 1 FROM public.restricted_delivery_release_witnesses w
                WHERE w.release_id = g.release_id
                  AND w.witness_kind = 'canonicalization_policy'
                  AND w.content_sha256 = g.canonicalization_policy_sha256
                  AND w.content_sha256 = encode(sha256(w.content_bytes), 'hex')
            )
          )
    ) THEN
        RAISE EXCEPTION 'canonicalization policy witness is missing';
    END IF;
    IF EXISTS (
        WITH policy AS (
            SELECT g.id AS generation_id, convert_from(w.content_bytes, 'UTF8')::jsonb AS content
            FROM public.restricted_delivery_generations g
            JOIN public.restricted_delivery_release_witnesses w
              ON w.release_id = g.release_id
             AND w.witness_kind = 'canonicalization_policy'
             AND w.content_sha256 = g.canonicalization_policy_sha256
             AND w.content_sha256 = encode(sha256(w.content_bytes), 'hex')
            WHERE g.id = p_generation_id
        )
        SELECT 1
        FROM public.dhm_rating_declarations d
        JOIN policy p ON p.generation_id = d.generation_id
        WHERE d.shape_state = 'canonicalized_usable'
          AND (
              p.content->>'codec' IS DISTINCT FROM 'dhm21-canonicalization-policy-v1'
              OR p.content->>'policy_scope' IS DISTINCT FROM 'nepal21'
              OR p.content->'station_codes' IS DISTINCT FROM expected_station_codes
              OR NOT EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(p.content->'approvals') approval
                  WHERE approval->>'station_code' = d.station_code
                    AND approval->>'declaration_content_sha256' = d.declaration_content_sha256
                    AND approval->>'approval_id' = d.canonicalization_approval_id::text
                    AND approval->>'approval_sha256' = d.canonicalization_approval_sha256
              )
          )
    ) THEN
        RAISE EXCEPTION 'canonicalization policy witness scope mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.dhm_rating_declarations d
        JOIN public.rating_curves c ON c.id = d.usable_rating_curve_id AND c.station_id = d.station_id
        WHERE d.generation_id = p_generation_id
          AND d.native_curve_payload_json ? 'points'
          AND (
            d.native_curve_payload_json->'points' IS DISTINCT FROM to_jsonb(c.points)
            OR d.native_curve_payload_json->>'interpolation' IS DISTINCT FROM c.interpolation
            OR d.native_curve_payload_json->>'rating_type_label' IS DISTINCT FROM c.rating_type_label
            OR (d.native_curve_payload_json->>'valid_from')::timestamptz IS DISTINCT FROM c.valid_from
            OR (d.native_curve_payload_json->>'valid_to')::timestamptz IS DISTINCT FROM c.valid_to
            OR d.from_local_date IS DISTINCT FROM (c.valid_from AT TIME ZONE 'Asia/Kathmandu')::date
            OR d.to_local_date_inclusive IS DISTINCT FROM (c.valid_to AT TIME ZONE 'Asia/Kathmandu')::date
            OR d.function_payload_json->'points' IS DISTINCT FROM (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'stage_m', (point->>'water_level')::numeric,
                        'discharge_m3s', (point->>'discharge')::numeric
                    )
                    ORDER BY (point->>'water_level')::numeric
                )
                FROM jsonb_array_elements(c.points) point
            )
          )
    ) THEN
        RAISE EXCEPTION 'native curve payload does not match actual rating curve';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.dhm_delivery_observation_attributions a
        JOIN public.restricted_delivery_generations g ON g.id = a.generation_id
        JOIN public.observations o ON o.id = a.observation_id AND o.station_id = a.station_id
        LEFT JOIN public.dhm_rating_declarations d
          ON d.id = a.rating_declaration_id
         AND d.generation_id = a.generation_id
         AND d.tenant_id = a.tenant_id
         AND d.station_id = a.station_id
        WHERE a.generation_id = p_generation_id
          AND a.attribution_state = 'selected'
          AND (
            d.id IS NULL
            OR d.usable_rating_curve_id IS DISTINCT FROM a.rating_curve_id
            OR o.rating_curve_id IS DISTINCT FROM a.rating_curve_id
            OR o.delivery_id IS DISTINCT FROM g.delivery_id
          )
    ) THEN
        RAISE EXCEPTION 'selected attribution three-pointer equality mismatch';
    END IF;
    IF EXISTS (
        WITH evaluated AS (
            SELECT
                a.*,
                (SELECT max(d.from_local_date)
                 FROM public.dhm_rating_declarations d
                 WHERE d.generation_id = a.generation_id
                   AND d.tenant_id = a.tenant_id
                   AND d.station_id = a.station_id
                   AND d.from_local_date <= a.local_date
                   AND d.to_local_date_inclusive >= a.local_date) AS latest_start
            FROM public.dhm_delivery_observation_attributions a
            WHERE a.generation_id = p_generation_id
        ), counts AS (
            SELECT
                e.*,
                (SELECT count(DISTINCT coalesce(d.function_equivalence_sha256, '<NULL>'))
                 FROM public.dhm_rating_declarations d
                 WHERE d.generation_id = e.generation_id
                   AND d.tenant_id = e.tenant_id
                   AND d.station_id = e.station_id
                   AND d.from_local_date = e.latest_start) AS latest_equivalence_count,
                (SELECT count(*)
                 FROM public.dhm_rating_declarations d
                 WHERE d.generation_id = e.generation_id
                   AND d.tenant_id = e.tenant_id
                   AND d.station_id = e.station_id
                   AND d.from_local_date = e.latest_start
                   AND d.usable_rating_curve_id IS NOT NULL) AS latest_usable_count
            FROM evaluated e
        )
        SELECT 1
        FROM counts a
        WHERE (
            a.attribution_state = 'selected'
            AND NOT EXISTS (
                SELECT 1
                FROM public.dhm_rating_declarations d
                WHERE d.id = a.rating_declaration_id
                  AND d.generation_id = a.generation_id
                  AND d.tenant_id = a.tenant_id
                  AND d.station_id = a.station_id
                  AND d.from_local_date = a.latest_start
                  AND d.usable_rating_curve_id = a.rating_curve_id
                  AND a.latest_equivalence_count = 1
                  AND a.latest_usable_count = 1
            )
        ) OR (
            a.attribution_state = 'held_equal_start_tie'
            AND (a.latest_start IS NULL OR a.latest_equivalence_count <= 1)
        ) OR (
            a.attribution_state = 'held_invalid_latest'
            AND (a.latest_start IS NULL OR a.latest_equivalence_count > 1 OR a.latest_usable_count <> 0)
        ) OR (
            a.attribution_state = 'held_missing_curve'
            AND a.latest_start IS NOT NULL
        ) OR (
            a.attribution_state = 'not_applicable'
            AND a.latest_start IS NOT NULL
        )
    ) THEN
        RAISE EXCEPTION 'historical rating attribution selector mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.dhm_delivery_observation_attributions a
        JOIN public.observations o ON o.id = a.observation_id AND o.station_id = a.station_id
        JOIN public.stations st ON st.id = o.station_id
        JOIN public.dhm_origin_sets os ON os.generation_id = a.generation_id
          AND os.tenant_id = a.tenant_id AND os.origin_set_sha256 = a.origin_set_sha256
        WHERE a.generation_id = p_generation_id
          AND (
            o.delivery_id IS DISTINCT FROM (SELECT delivery_id FROM public.restricted_delivery_generations WHERE id = p_generation_id)
            OR a.station_code IS DISTINCT FROM st.code
            OR a.reported_q IS DISTINCT FROM o.value
            OR a.local_date IS DISTINCT FROM (o.timestamp AT TIME ZONE 'Asia/Kathmandu')::date
            OR os.set_kind IS DISTINCT FROM 'DFL_RECORD'
            OR (a.attribution_state <> 'selected' AND (a.rating_declaration_id IS NOT NULL OR a.rating_curve_id IS NOT NULL))
          )
    ) THEN
        RAISE EXCEPTION 'observation attribution content mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.observations o
        JOIN public.restricted_delivery_generations g ON g.delivery_id = o.delivery_id
        WHERE g.id = p_generation_id
    ) AND NOT EXISTS (
        SELECT 1
        FROM public.dhm_rating_declarations d
        WHERE d.generation_id = p_generation_id
    ) THEN
        RAISE EXCEPTION 'publication requires delivery observations';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.observations o
        JOIN public.restricted_delivery_generations g ON g.delivery_id = o.delivery_id
        WHERE g.id = p_generation_id
          AND NOT EXISTS (
              SELECT 1 FROM public.dhm_delivery_observation_attributions a
              WHERE a.generation_id = g.id
                AND a.tenant_id = g.tenant_id
                AND a.observation_id = o.id
                AND a.station_id = o.station_id
          )
    ) OR EXISTS (
        SELECT 1
        FROM public.dhm_delivery_observation_attributions a
        JOIN public.restricted_delivery_generations g ON g.id = a.generation_id
        LEFT JOIN public.observations o ON o.id = a.observation_id AND o.station_id = a.station_id
        WHERE a.generation_id = p_generation_id
          AND (o.id IS NULL OR o.delivery_id IS DISTINCT FROM g.delivery_id)
    ) THEN
        RAISE EXCEPTION 'observation attribution membership mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.dhm_origin_sets s
        LEFT JOIN public.dhm_origin_set_members m
          ON m.generation_id = s.generation_id
         AND m.tenant_id = s.tenant_id
         AND m.origin_set_sha256 = s.origin_set_sha256
        WHERE s.generation_id = p_generation_id
        GROUP BY s.generation_id, s.tenant_id, s.origin_set_sha256, s.member_count, s.set_kind
        HAVING count(m.origin_id) <> s.member_count
           OR min(m.member_ordinal) IS DISTINCT FROM 0
           OR max(m.member_ordinal) IS DISTINCT FROM s.member_count - 1
           OR count(DISTINCT m.member_ordinal) <> s.member_count
    ) THEN
        RAISE EXCEPTION 'origin set membership mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT
                s.origin_set_sha256,
                encode(sha256(convert_to(
                    s.set_kind || '|' || string_agg(
                        m.member_ordinal::text || ':' ||
                        o.id::text || ':' ||
                        o.origin_kind || ':' ||
                        o.source_root_label || ':' ||
                        o.relative_path || ':' ||
                        o.file_sha256 || ':' ||
                        o.parser_version || ':' ||
                        coalesce(o.row_or_block_ordinal::text, '') || ':' ||
                        o.origin_payload_sha256,
                        '|' ORDER BY m.member_ordinal
                    ), 'UTF8')), 'hex') AS computed_sha256
            FROM public.dhm_origin_sets s
            JOIN public.dhm_origin_set_members m
              ON m.generation_id = s.generation_id
             AND m.tenant_id = s.tenant_id
             AND m.origin_set_sha256 = s.origin_set_sha256
            JOIN public.dhm_delivery_origins o
              ON o.id = m.origin_id
             AND o.generation_id = m.generation_id
             AND o.tenant_id = m.tenant_id
             AND o.origin_kind = m.origin_kind
            WHERE s.generation_id = p_generation_id
            GROUP BY s.generation_id, s.tenant_id, s.origin_set_sha256, s.set_kind
        ) checked
        WHERE checked.origin_set_sha256 <> checked.computed_sha256
    ) THEN
        RAISE EXCEPTION 'origin set membership mismatch';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.sf_lock_dhm21_delivery_write(p_tenant_id uuid, p_release_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected DHM21 writes require READ COMMITTED';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.restricted_delivery_releases WHERE id = p_release_id AND tenant_id = p_tenant_id AND cohort = 'nepal21') THEN
        RAISE EXCEPTION 'restricted delivery release is absent or foreign';
    END IF;
    PERFORM pg_advisory_xact_lock(1397115979, ('x' || substr(replace(p_tenant_id::text, '-', ''), 1, 8))::bit(32)::int);
    PERFORM pg_advisory_xact_lock(hashtextextended('provisional-discharge:' || p_tenant_id::text, 0));
    LOCK TABLE public.restricted_delivery_releases, public.restricted_delivery_generations,
        public.provisional_discharge_permissions, public.expired_rating_test_permissions,
        public.dhm_delivery_origins, public.dhm_origin_sets, public.dhm_origin_set_members,
        public.dhm_rating_declarations, public.dhm_delivery_observation_attributions,
        public.rating_curves, public.observations IN SHARE ROW EXCLUSIVE MODE;
END $$;

CREATE OR REPLACE FUNCTION public.sf_lock_dhm21_conversion_append(p_tenant_id uuid, p_generation_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected DHM21 conversion writes require READ COMMITTED';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.restricted_delivery_generations WHERE id = p_generation_id AND tenant_id = p_tenant_id AND cohort = 'nepal21') THEN
        RAISE EXCEPTION 'restricted delivery generation is absent or foreign';
    END IF;
    PERFORM pg_advisory_xact_lock(1397115979, ('x' || substr(replace(p_tenant_id::text, '-', ''), 1, 8))::bit(32)::int);
    PERFORM pg_advisory_xact_lock(hashtextextended('provisional-discharge:' || p_tenant_id::text, 0));
    LOCK TABLE public.restricted_delivery_generations, public.provisional_discharge_permissions,
        public.expired_rating_test_permissions, public.dhm_delivery_origins, public.dhm_origin_sets,
        public.dhm_origin_set_members, public.dhm_rating_declarations,
        public.dhm_delivery_observation_attributions, public.rating_curves, public.observations
        IN SHARE ROW EXCLUSIVE MODE;
END $$;

CREATE OR REPLACE FUNCTION public.sf_lock_dhm21_permission_change(p_tenant_id uuid, p_generation_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.restricted_delivery_generations WHERE id = p_generation_id AND tenant_id = p_tenant_id AND cohort = 'nepal21') THEN
        RAISE EXCEPTION 'restricted delivery generation is absent or foreign';
    END IF;
    PERFORM pg_advisory_xact_lock(1397115979, ('x' || substr(replace(p_tenant_id::text, '-', ''), 1, 8))::bit(32)::int);
    PERFORM pg_advisory_xact_lock(hashtextextended('provisional-discharge:' || p_tenant_id::text, 0));
    LOCK TABLE public.restricted_delivery_generations, public.provisional_discharge_permissions,
        public.expired_rating_test_permissions, public.dhm_delivery_origins, public.dhm_origin_sets,
        public.dhm_origin_set_members, public.dhm_rating_declarations, public.rating_curves
        IN SHARE ROW EXCLUSIVE MODE;
END $$;

CREATE OR REPLACE FUNCTION public.sf_lock_provisional_permission_disable(p_tenant_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(1397115979, ('x' || substr(replace(p_tenant_id::text, '-', ''), 1, 8))::bit(32)::int);
    PERFORM pg_advisory_xact_lock(hashtextextended('provisional-discharge:' || p_tenant_id::text, 0));
    LOCK TABLE public.provisional_discharge_permissions IN SHARE ROW EXCLUSIVE MODE;
END $$;

CREATE OR REPLACE FUNCTION public.sf_lock_nepal_static_guard(p_tenant_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(1397115979, ('x' || substr(replace(p_tenant_id::text, '-', ''), 1, 8))::bit(32)::int);
    LOCK TABLE public.stations, public.basins, public.basin_versions, public.station_weather_sources,
        public.model_assignments, public.recap_gateway_polygon_bindings IN SHARE ROW EXCLUSIVE MODE;
END $$;

CREATE OR REPLACE FUNCTION public.sf_restricted_generation_publish_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RETURN NEW;
    END IF;
    IF OLD.state <> 'building' AND (
        NEW.delivery_id IS DISTINCT FROM OLD.delivery_id OR
        NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256 OR
        NEW.station_set_sha256 IS DISTINCT FROM OLD.station_set_sha256 OR
        NEW.predecessor_semantic_sha256 IS DISTINCT FROM OLD.predecessor_semantic_sha256 OR
        NEW.preimage_sha256 IS DISTINCT FROM OLD.preimage_sha256 OR
        NEW.immutable_content_sha256 IS DISTINCT FROM OLD.immutable_content_sha256 OR
        NEW.release_id IS DISTINCT FROM OLD.release_id OR
        NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
        NEW.network IS DISTINCT FROM OLD.network OR
        NEW.cohort IS DISTINCT FROM OLD.cohort OR
        NEW.canonicalization_policy_sha256 IS DISTINCT FROM OLD.canonicalization_policy_sha256
    ) THEN
        RAISE EXCEPTION 'published restricted generation identity is immutable';
    END IF;
    IF NOT (OLD.state = 'active_pending_qc' AND NEW.state = 'active_qc_complete')
       AND (
        NEW.qc_policy_sha256 IS DISTINCT FROM OLD.qc_policy_sha256 OR
        NEW.qc_completion_sha256 IS DISTINCT FROM OLD.qc_completion_sha256 OR
        NEW.qc_completed_at IS DISTINCT FROM OLD.qc_completed_at
    ) THEN
        RAISE EXCEPTION 'restricted generation QC fields are transition-only';
    END IF;
    IF OLD.state = 'building' AND NEW.state = 'active_pending_qc' THEN
        IF NEW.immutable_content_sha256 IS NULL OR NEW.published_at IS NULL THEN
            RAISE EXCEPTION 'publication requires immutable content digest and timestamp';
        END IF;
        PERFORM public.sf_assert_restricted_publication_catalog(NEW.id);
        PERFORM public.sf_mark_restricted_release_used(NEW.id);
    ELSIF OLD.state = 'active_pending_qc' AND NEW.state = 'active_qc_complete' THEN
        IF NEW.qc_policy_sha256 IS NULL OR NEW.qc_completion_sha256 IS NULL OR NEW.qc_completed_at IS NULL THEN
            RAISE EXCEPTION 'QC completion requires policy digest, completion digest and timestamp';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.observations o WHERE o.delivery_id = NEW.delivery_id
        ) THEN
            RAISE EXCEPTION 'QC completion requires delivery observations';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.observations o
            WHERE o.delivery_id = NEW.delivery_id
              AND o.qc_status = 'raw'
        ) THEN
            RAISE EXCEPTION 'QC completion requires evaluated delivery observations';
        END IF;
        IF (SELECT count(*) FROM public.dhm_delivery_observation_attributions a WHERE a.generation_id = NEW.id)
           <> (SELECT count(*) FROM public.observations o WHERE o.delivery_id = NEW.delivery_id) THEN
            RAISE EXCEPTION 'QC completion requires complete observation attribution';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.restricted_delivery_release_witnesses w
            WHERE w.release_id = NEW.release_id
              AND w.witness_kind = 'qc_policy'
              AND w.content_sha256 = NEW.qc_policy_sha256
              AND w.content_sha256 = encode(sha256(w.content_bytes), 'hex')
        ) THEN
            RAISE EXCEPTION 'QC completion requires release-bound QC policy witness';
        END IF;
    ELSIF NEW.state IS DISTINCT FROM OLD.state THEN
        RAISE EXCEPTION 'invalid restricted generation state transition';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_restricted_generation_publish_guard BEFORE UPDATE ON public.restricted_delivery_generations
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_publish_guard();
CREATE OR REPLACE FUNCTION public.sf_restricted_generation_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NEW.cohort = 'nepal21'
       AND NEW.state <> 'building'
       AND session_user = 'sapphire_dhm21_delivery_writer' THEN
        RAISE EXCEPTION 'restricted Nepal21 generation insert must start building';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_restricted_generation_insert_guard BEFORE INSERT ON public.restricted_delivery_generations
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_insert_guard();

CREATE OR REPLACE FUNCTION public.sf_restricted_generation_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.restricted_delivery_generations
        WHERE cohort = 'nepal21' AND state = 'building'
    ) THEN
        RAISE EXCEPTION 'restricted Nepal21 building generation may not commit unpublished';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_restricted_generation_commit_guard
    AFTER INSERT OR UPDATE ON public.restricted_delivery_generations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_commit_guard();

CREATE OR REPLACE FUNCTION public.sf_restricted_delivery_row_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE row_delivery text;
DECLARE old_delivery text;
DECLARE new_delivery text;
BEGIN
    old_delivery := CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN OLD.delivery_id ELSE NULL END;
    new_delivery := CASE WHEN TG_OP IN ('INSERT','UPDATE') THEN NEW.delivery_id ELSE NULL END;
    IF old_delivery = 'dhm-barkhk-2026-09-08' OR old_delivery LIKE 'dhm-nepal21-%' THEN
        row_delivery := old_delivery;
    ELSE
        row_delivery := new_delivery;
    END IF;
    IF row_delivery IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF row_delivery = 'dhm-barkhk-2026-09-08'
       AND NOT EXISTS (
            SELECT 1 FROM public.restricted_delivery_releases r
            WHERE r.expected_predecessor_delivery_id = row_delivery
       ) THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF row_delivery = 'dhm-barkhk-2026-09-08' OR row_delivery LIKE 'dhm-nepal21-%' THEN
        IF row_delivery LIKE 'dhm-nepal21-%'
           AND session_user <> 'sapphire_dhm21_delivery_writer'
           AND NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
                WHERE n.nspname = TG_TABLE_SCHEMA
                  AND c.relname = TG_TABLE_NAME
                  AND r.rolname = session_user
           ) THEN
            RAISE EXCEPTION 'restricted DHM delivery row write refused for %', session_user
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF row_delivery = 'dhm-barkhk-2026-09-08'
           AND session_user NOT IN ('sapphire_dhm21_delivery_writer', 'sapphire_operator')
           AND NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
                WHERE n.nspname = TG_TABLE_SCHEMA
                  AND c.relname = TG_TABLE_NAME
                  AND r.rolname = session_user
           ) THEN
            RAISE EXCEPTION 'restricted DHM delivery row write refused for %', session_user
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_OP = 'INSERT' AND TG_TABLE_NAME = 'observations' THEN
            IF NEW.qc_status <> 'raw' THEN
                RAISE EXCEPTION 'restricted observation insert must be RAW'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            IF TG_TABLE_NAME = 'observations' THEN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.station_id IS DISTINCT FROM OLD.station_id
                   OR NEW.timestamp IS DISTINCT FROM OLD.timestamp
                   OR NEW.parameter IS DISTINCT FROM OLD.parameter
                   OR NEW.value IS DISTINCT FROM OLD.value
                   OR NEW.source IS DISTINCT FROM OLD.source
                   OR NEW.rating_curve_id IS DISTINCT FROM OLD.rating_curve_id
                   OR NEW.rating_curve_correction_version IS DISTINCT FROM OLD.rating_curve_correction_version
                   OR NEW.delivery_id IS DISTINCT FROM OLD.delivery_id THEN
                    RAISE EXCEPTION 'restricted observation update may change only QC columns'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM public.restricted_delivery_generations g
                    WHERE g.delivery_id = row_delivery
                      AND g.state = 'active_qc_complete'
                ) THEN
                    RAISE EXCEPTION 'restricted QC-complete observations are immutable'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
            ELSE
                RAISE EXCEPTION 'restricted rating curve rows are immutable'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER trg_observations_restricted_delivery_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.observations
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_delivery_row_guard();
CREATE TRIGGER trg_rating_curves_restricted_delivery_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.rating_curves
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_delivery_row_guard();


CREATE OR REPLACE FUNCTION public.sf_restricted_generation_delete_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'restricted Nepal21 generations may not be truncated';
    END IF;
    IF OLD.cohort = 'nepal21' THEN
        IF OLD.state = 'building' THEN
            RAISE EXCEPTION 'restricted Nepal21 building generation may not be deleted before commit';
        END IF;
        RAISE EXCEPTION 'restricted Nepal21 published generation may not be deleted';
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER trg_restricted_generation_delete_guard
    BEFORE DELETE ON public.restricted_delivery_generations
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_generation_delete_guard();
CREATE TRIGGER trg_restricted_generation_truncate_guard
    BEFORE TRUNCATE ON public.restricted_delivery_generations
    FOR EACH STATEMENT EXECUTE FUNCTION public.sf_restricted_immutable();

CREATE OR REPLACE FUNCTION public.sf_restricted_old_delete_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE old_delivery text;
BEGIN
    old_delivery := OLD.delivery_id;
    IF old_delivery = 'dhm-barkhk-2026-09-08'
       AND EXISTS (
            SELECT 1 FROM public.restricted_delivery_releases r
            WHERE r.expected_predecessor_delivery_id = old_delivery
       ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.restricted_delivery_releases r
            JOIN public.restricted_delivery_generations g ON g.release_id = r.id
            WHERE r.expected_predecessor_delivery_id = old_delivery
              AND r.status = 'used'
              AND g.state IN ('active_pending_qc', 'active_qc_complete')
        ) THEN
            RAISE EXCEPTION 'old restricted delivery rows may not commit deleted without published Nepal21 successor';
        END IF;
    END IF;
    RETURN OLD;
END $$;
CREATE CONSTRAINT TRIGGER trg_observations_old_delete_commit_guard
    AFTER DELETE ON public.observations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_old_delete_commit_guard();
CREATE CONSTRAINT TRIGGER trg_rating_curves_old_delete_commit_guard
    AFTER DELETE ON public.rating_curves
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.sf_restricted_old_delete_commit_guard();


CREATE OR REPLACE FUNCTION public.provisional_discharge_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp
SET timezone = 'UTC' AS $$
DECLARE d jsonb; o public.observations;
    c public.rating_curves; feed jsonb; proof jsonb; newest uuid; tenant_code text;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected provisional writes require READ COMMITTED';
    END IF;
    d := NEW.content::jsonb;
    IF NEW.captured_at > clock_timestamp() THEN
        RAISE EXCEPTION 'provisional capture time is in the future';
    END IF;
    -- Serialize owner disable with append without granting UPDATE for row locks.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'provisional-discharge:' || NEW.tenant_id::text, 0));
    IF NOT EXISTS (SELECT 1 FROM public.provisional_discharge_permissions
        WHERE tenant_id = NEW.tenant_id AND state = 'enabled') THEN
        RAISE EXCEPTION 'provisional discharge activation is disabled';
    END IF;
    -- This dormant path favours integrity over throughput: a table SHARE lock
    -- excludes concurrent new curve candidates as well as edits to existing ones.
    LOCK TABLE public.rating_curves IN SHARE MODE;
    SELECT code INTO tenant_code FROM public.tenants WHERE id = NEW.tenant_id;
    SELECT * INTO o FROM public.observations WHERE id = NEW.observation_id FOR SHARE;
    SELECT * INTO c FROM public.rating_curves WHERE id = NEW.rating_curve_id FOR SHARE;
    SELECT id INTO newest FROM public.rating_curves WHERE station_id = NEW.station_id
        ORDER BY valid_from DESC, version DESC LIMIT 1;
    SELECT content::jsonb INTO feed FROM public.measurement_feed_evidence
        WHERE id = NEW.feed_evidence_id;
    SELECT content::jsonb INTO proof FROM public.rating_reference_proofs
        WHERE id = NEW.reference_proof_id;
    IF NOT (o.station_id = NEW.station_id AND c.station_id = NEW.station_id
        AND o.source = 'measured' AND o.parameter = 'water_level'
        AND o.qc_status = 'qc_passed' AND length(o.qc_rule_version) > 0
        AND jsonb_array_length(o.qc_flags) > 0
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(o.qc_flags) f
                        WHERE f->>'status' IS DISTINCT FROM 'qc_passed')
        AND o.timestamp >= c.valid_from AND o.timestamp <= NEW.captured_at
        AND (tenant_code = 'chwrr' OR c.id = newest)
        AND c.valid_from < c.valid_to AND c.valid_to <=
            NEW.captured_at
        AND public.provisional_measurement_content(d->'measurement')
            = public.provisional_measurement_content(to_jsonb(o))
        AND public.provisional_qc_content(d->'qc') =
            public.provisional_qc_content(to_jsonb(o))
        AND public.provisional_curve_content(d->'curve') =
            public.provisional_curve_content(to_jsonb(c))
        AND d->'feed_evidence' = feed AND d->'reference_proof' = proof
        AND public.provisional_measurement_content(
            (feed->'measurement'->>'content')::jsonb)
            = public.provisional_measurement_content(d->'measurement')
        AND public.provisional_curve_content((proof->'curve'->>'content')::jsonb)
            = public.provisional_curve_content(d->'curve')
        AND (feed->>'verified_at')::timestamptz <= NEW.captured_at
        AND (proof->>'verified_at')::timestamptz <= NEW.captured_at
        AND feed->>'endpoint' = proof->>'endpoint'
        AND feed->>'api_station_id' = proof->>'api_station_id'
        AND d->>'conversion_version' = 'expired-rating-linear-reference-v1'
        AND (d->>'discharge')::float8 = NEW.discharge
        AND o.value + (proof->>'offset_m')::float8 >=
            (SELECT min((p->>'water_level')::float8)
             FROM jsonb_array_elements(c.points) p)
        AND o.value + (proof->>'offset_m')::float8 <=
            (SELECT max((p->>'water_level')::float8)
             FROM jsonb_array_elements(c.points) p)
    ) IS TRUE THEN
        RAISE EXCEPTION 'provisional snapshot, QC, curve or reference disagreement';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.sf_nepal21_expired_conversion_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    tenant_code text;
    generation uuid;
    permission_row record;
    expected_permission jsonb;
    content_doc jsonb;
    latest_start date;
    latest_equivalence_count integer;
    latest_usable_count integer;
    unmatched_count integer;
BEGIN
    SELECT code INTO tenant_code FROM public.tenants WHERE id = NEW.tenant_id;
    IF tenant_code IS DISTINCT FROM 'chwrr' THEN
        RETURN NEW;
    END IF;
    SELECT id INTO generation
    FROM public.restricted_delivery_generations
    WHERE tenant_id = NEW.tenant_id
      AND network = 'dhm'
      AND cohort = 'nepal21'
      AND state = 'active_qc_complete';
    IF generation IS NULL THEN
        RAISE EXCEPTION 'Nepal21 expired conversion requires QC-complete generation';
    END IF;
    PERFORM public.sf_lock_dhm21_conversion_append(NEW.tenant_id, generation);
    SELECT p.*, g.qc_policy_sha256, g.qc_completion_sha256
    INTO permission_row
    FROM public.expired_rating_test_permissions p
    JOIN public.restricted_delivery_generations g ON g.id = p.generation_id
    WHERE p.tenant_id = NEW.tenant_id
      AND p.generation_id = generation
      AND p.cohort = 'nepal21'
      AND p.state = 'enabled';
    IF permission_row.id IS NULL THEN
        RAISE EXCEPTION 'Nepal21 expired rating test permission is disabled';
    END IF;
    SELECT max(d.from_local_date) INTO latest_start
    FROM public.dhm_rating_declarations d
    WHERE d.generation_id = generation
      AND d.tenant_id = NEW.tenant_id
      AND d.station_id = NEW.station_id;
    IF latest_start IS NULL THEN
        RAISE EXCEPTION 'Nepal21 expired conversion declaration is absent';
    END IF;
    SELECT count(DISTINCT coalesce(d.function_equivalence_sha256, '<NULL>')) INTO latest_equivalence_count
    FROM public.dhm_rating_declarations d
    WHERE d.generation_id = generation
      AND d.tenant_id = NEW.tenant_id
      AND d.station_id = NEW.station_id
      AND d.from_local_date = latest_start;
    IF latest_equivalence_count > 1 THEN
        RAISE EXCEPTION 'Nepal21 expired conversion has equal-start non-equivalent declarations';
    END IF;
    SELECT count(*) INTO latest_usable_count
    FROM public.dhm_rating_declarations d
    WHERE d.generation_id = generation
      AND d.tenant_id = NEW.tenant_id
      AND d.station_id = NEW.station_id
      AND d.from_local_date = latest_start
      AND d.usable_rating_curve_id = NEW.rating_curve_id;
    IF latest_usable_count <> 1 THEN
        RAISE EXCEPTION 'Nepal21 expired conversion latest declaration is not usable';
    END IF;
    SELECT count(*) INTO unmatched_count
    FROM public.rating_curves selected
    JOIN public.rating_curves candidate
      ON candidate.station_id = selected.station_id
     AND candidate.id <> selected.id
     AND (candidate.valid_from AT TIME ZONE 'Asia/Kathmandu')::date >= latest_start
    WHERE selected.id = NEW.rating_curve_id
      AND selected.station_id = NEW.station_id
      AND NOT EXISTS (
          SELECT 1 FROM public.dhm_rating_declarations d
          WHERE d.generation_id = generation
            AND d.tenant_id = NEW.tenant_id
            AND d.station_id = NEW.station_id
            AND d.usable_rating_curve_id = candidate.id
      )
      AND NOT (candidate.valid_from IS NOT DISTINCT FROM selected.valid_from
           AND candidate.valid_to IS NOT DISTINCT FROM selected.valid_to
           AND candidate.points IS NOT DISTINCT FROM selected.points
           AND candidate.interpolation IS NOT DISTINCT FROM selected.interpolation
           AND candidate.rating_type_label IS NOT DISTINCT FROM selected.rating_type_label);
    IF unmatched_count > 0 THEN
        RAISE EXCEPTION 'Nepal21 expired conversion has unmatched native rating candidate';
    END IF;
    content_doc := NEW.content::jsonb;
    expected_permission := jsonb_build_object(
        'permission_id', permission_row.id::text,
        'permission_version', permission_row.permission_version,
        'generation_id', permission_row.generation_id::text,
        'owner_authorization_sha256', permission_row.owner_authorization_sha256,
        'precedence_sha256', permission_row.precedence_sha256,
        'selection_manifest_sha256', permission_row.selection_manifest_sha256,
        'qc_policy_sha256', permission_row.qc_policy_sha256,
        'qc_completion_sha256', permission_row.qc_completion_sha256,
        'selected_rating_curve_id', NEW.rating_curve_id::text
    );
    IF content_doc->'nepal21_expired_permission' IS DISTINCT FROM expected_permission THEN
        RAISE EXCEPTION 'Nepal21 provisional permission fingerprint disagreement';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_provisional_discharges_nepal21_expired_permission
    BEFORE INSERT ON public.provisional_discharges
    FOR EACH ROW EXECUTE FUNCTION public.sf_nepal21_expired_conversion_guard();

CREATE OR REPLACE FUNCTION public.sf_expired_permission_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state = 'enabled' AND NOT EXISTS (
            SELECT 1 FROM public.restricted_delivery_generations
            WHERE id = NEW.generation_id AND tenant_id = NEW.tenant_id
              AND cohort = NEW.cohort AND state = 'active_qc_complete'
        ) THEN
            RAISE EXCEPTION 'expiry permission requires active QC-complete generation';
        END IF;
        RETURN NEW;
    END IF;
    IF NOT (OLD.state = 'enabled' AND NEW.state = 'disabled'
        AND NEW.disabled_at IS NOT NULL
        AND NEW.id = OLD.id
        AND NEW.tenant_id = OLD.tenant_id
        AND NEW.generation_id = OLD.generation_id
        AND NEW.permission_version = OLD.permission_version
        AND NEW.owner_authorization_sha256 = OLD.owner_authorization_sha256
        AND NEW.precedence_sha256 = OLD.precedence_sha256
        AND NEW.selection_manifest_sha256 = OLD.selection_manifest_sha256) THEN
        RAISE EXCEPTION 'expired rating permission may only be disabled in place';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_expired_rating_permission_guard BEFORE INSERT OR UPDATE ON public.expired_rating_test_permissions
    FOR EACH ROW EXECUTE FUNCTION public.sf_expired_permission_guard();

REVOKE ALL ON public.restricted_delivery_releases, public.restricted_delivery_release_witnesses,
    public.restricted_delivery_generations, public.dhm_delivery_origins, public.dhm_origin_sets,
    public.dhm_origin_set_members, public.dhm_rating_declarations,
    public.dhm_delivery_observation_attributions, public.expired_rating_test_permissions FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.sf_lock_dhm21_delivery_write(uuid, uuid),
    public.sf_lock_dhm21_conversion_append(uuid, uuid), public.sf_lock_dhm21_permission_change(uuid, uuid),
    public.sf_lock_provisional_permission_disable(uuid), public.sf_lock_nepal_static_guard(uuid),
    public.sf_restricted_delivery_row_guard() FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute(r"""
DROP TRIGGER IF EXISTS trg_observations_restricted_delivery_guard ON public.observations;
DROP TRIGGER IF EXISTS trg_rating_curves_restricted_delivery_guard ON public.rating_curves;
DROP TRIGGER IF EXISTS trg_restricted_release_update_guard ON public.restricted_delivery_releases;
DROP TRIGGER IF EXISTS trg_restricted_release_truncate_guard ON public.restricted_delivery_releases;
DROP TRIGGER IF EXISTS trg_restricted_generation_insert_guard ON public.restricted_delivery_generations;
DROP TRIGGER IF EXISTS trg_restricted_generation_truncate_guard ON public.restricted_delivery_generations;
DROP TRIGGER IF EXISTS trg_dhm_origins_insert_guard ON public.dhm_delivery_origins;
DROP TRIGGER IF EXISTS trg_dhm_origin_sets_insert_guard ON public.dhm_origin_sets;
DROP TRIGGER IF EXISTS trg_dhm_origin_members_insert_guard ON public.dhm_origin_set_members;
DROP TRIGGER IF EXISTS trg_dhm_declarations_insert_guard ON public.dhm_rating_declarations;
DROP TRIGGER IF EXISTS trg_dhm_attributions_insert_guard ON public.dhm_delivery_observation_attributions;
DROP TRIGGER IF EXISTS trg_observations_old_delete_commit_guard ON public.observations;
DROP TRIGGER IF EXISTS trg_rating_curves_old_delete_commit_guard ON public.rating_curves;
DROP TRIGGER IF EXISTS trg_provisional_discharges_nepal21_expired_permission ON public.provisional_discharges;
DROP TRIGGER IF EXISTS trg_restricted_generation_delete_guard ON public.restricted_delivery_generations;
DROP TABLE IF EXISTS public.expired_rating_test_permissions;
DROP TABLE IF EXISTS public.dhm_delivery_observation_attributions;
DROP TABLE IF EXISTS public.dhm_rating_declarations;
DROP TABLE IF EXISTS public.dhm_origin_set_members;
DROP TABLE IF EXISTS public.dhm_origin_sets;
DROP TABLE IF EXISTS public.dhm_delivery_origins;
DROP TABLE IF EXISTS public.restricted_delivery_generations;
DROP TABLE IF EXISTS public.restricted_delivery_release_witnesses;
DROP TABLE IF EXISTS public.restricted_delivery_releases;
DROP FUNCTION IF EXISTS public.sf_lock_nepal_static_guard(uuid);
DROP FUNCTION IF EXISTS public.sf_lock_provisional_permission_disable(uuid);
DROP FUNCTION IF EXISTS public.sf_lock_dhm21_permission_change(uuid, uuid);
DROP FUNCTION IF EXISTS public.sf_lock_dhm21_conversion_append(uuid, uuid);
DROP FUNCTION IF EXISTS public.sf_lock_dhm21_delivery_write(uuid, uuid);
DROP FUNCTION IF EXISTS public.sf_active_accepted_delivery(uuid, text);
DROP FUNCTION IF EXISTS public.sf_expired_permission_guard();
DROP FUNCTION IF EXISTS public.sf_restricted_delivery_row_guard();
DROP FUNCTION IF EXISTS public.sf_restricted_old_delete_commit_guard();
DROP FUNCTION IF EXISTS public.sf_restricted_generation_delete_guard();
DROP FUNCTION IF EXISTS public.sf_nepal21_expired_conversion_guard();
DROP FUNCTION IF EXISTS public.sf_restricted_generation_commit_guard();
DROP FUNCTION IF EXISTS public.sf_restricted_generation_publish_guard();
DROP FUNCTION IF EXISTS public.sf_restricted_generation_insert_guard();
DROP FUNCTION IF EXISTS public.sf_restricted_release_update_guard();
DROP FUNCTION IF EXISTS public.sf_restricted_generation_child_insert_guard();
DROP FUNCTION IF EXISTS public.sf_mark_restricted_release_used(uuid);
DROP FUNCTION IF EXISTS public.sf_assert_restricted_publication_catalog(uuid);
DROP FUNCTION IF EXISTS public.sf_restricted_immutable();
DROP FUNCTION IF EXISTS public.sf_assert_restricted_witness();
""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.provisional_discharge_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp
SET timezone = 'UTC' AS $$
DECLARE d jsonb; o public.observations;
    c public.rating_curves; feed jsonb; proof jsonb; newest uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'protected provisional writes require READ COMMITTED';
    END IF;
    d := NEW.content::jsonb;
    IF NEW.captured_at > clock_timestamp() THEN
        RAISE EXCEPTION 'provisional capture time is in the future';
    END IF;
    -- Serialize owner disable with append without granting UPDATE for row locks.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'provisional-discharge:' || NEW.tenant_id::text, 0));
    IF NOT EXISTS (SELECT 1 FROM public.provisional_discharge_permissions
        WHERE tenant_id = NEW.tenant_id AND state = 'enabled') THEN
        RAISE EXCEPTION 'provisional discharge activation is disabled';
    END IF;
    -- This dormant path favours integrity over throughput: a table SHARE lock
    -- excludes concurrent new curve candidates as well as edits to existing ones.
    LOCK TABLE public.rating_curves IN SHARE MODE;
    SELECT * INTO o FROM public.observations WHERE id = NEW.observation_id FOR SHARE;
    SELECT * INTO c FROM public.rating_curves WHERE id = NEW.rating_curve_id FOR SHARE;
    SELECT id INTO newest FROM public.rating_curves WHERE station_id = NEW.station_id
        ORDER BY valid_from DESC, version DESC LIMIT 1;
    SELECT content::jsonb INTO feed FROM public.measurement_feed_evidence
        WHERE id = NEW.feed_evidence_id;
    SELECT content::jsonb INTO proof FROM public.rating_reference_proofs
        WHERE id = NEW.reference_proof_id;
    IF NOT (o.station_id = NEW.station_id AND c.station_id = NEW.station_id
        AND o.source = 'measured' AND o.parameter = 'water_level'
        AND o.qc_status = 'qc_passed' AND length(o.qc_rule_version) > 0
        AND jsonb_array_length(o.qc_flags) > 0
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(o.qc_flags) f
                        WHERE f->>'status' IS DISTINCT FROM 'qc_passed')
        AND o.timestamp >= c.valid_from AND o.timestamp <= NEW.captured_at
        AND c.id = newest AND c.valid_from < c.valid_to AND c.valid_to <=
            NEW.captured_at
        AND public.provisional_measurement_content(d->'measurement')
            = public.provisional_measurement_content(to_jsonb(o))
        AND public.provisional_qc_content(d->'qc') =
            public.provisional_qc_content(to_jsonb(o))
        AND public.provisional_curve_content(d->'curve') =
            public.provisional_curve_content(to_jsonb(c))
        AND d->'feed_evidence' = feed AND d->'reference_proof' = proof
        AND public.provisional_measurement_content(
            (feed->'measurement'->>'content')::jsonb)
            = public.provisional_measurement_content(d->'measurement')
        AND public.provisional_curve_content((proof->'curve'->>'content')::jsonb)
            = public.provisional_curve_content(d->'curve')
        AND (feed->>'verified_at')::timestamptz <= NEW.captured_at
        AND (proof->>'verified_at')::timestamptz <= NEW.captured_at
        AND feed->>'endpoint' = proof->>'endpoint'
        AND feed->>'api_station_id' = proof->>'api_station_id'
        AND d->>'conversion_version' = 'expired-rating-linear-reference-v1'
        AND (d->>'discharge')::float8 = NEW.discharge
        AND o.value + (proof->>'offset_m')::float8 >=
            (SELECT min((p->>'water_level')::float8)
             FROM jsonb_array_elements(c.points) p)
        AND o.value + (proof->>'offset_m')::float8 <=
            (SELECT max((p->>'water_level')::float8)
             FROM jsonb_array_elements(c.points) p)
    ) IS TRUE THEN
        RAISE EXCEPTION 'provisional snapshot, QC, curve or reference disagreement';
    END IF;
    RETURN NEW;
END $$;
""")
