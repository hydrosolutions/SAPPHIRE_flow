from __future__ import annotations

# ruff: noqa: E501
import dataclasses
import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

from alembic import command

if TYPE_CHECKING:
    from pathlib import Path
from sapphire_flow.db.metadata import provisional_discharges
from sapphire_flow.services.nepal21_delivery_authority import (
    ExpectedQcPolicyPin,
    GenerationBuildInput,
    RestrictedReplacementPayload,
    _normalize_predecessor_row,  # pyright: ignore[reportPrivateUsage]
    canonical_json_bytes,
    fetch_predecessor_semantic_rows,
    origin_identity_set_sha256,
    predecessor_semantic_digest,
    publish_generation,
    run_dhm21_delivery_replacement_dry_run,
    run_dhm21_delivery_replacement_transaction,
    run_dhm21_qc_transaction,
    sha256_hex_bytes,
)
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.rating_reference import canonical_content, content_digest
from tests.integration.db.test_migration_0065_rejected_forecasts import (
    migration_engine as migration_engine,
)
from tests.integration.store.test_provisional_discharge_store import (
    NOW,
    convert,
    permit_fixture,
    raw_values,
    seed,
)

TABLES = (
    "restricted_delivery_releases",
    "restricted_delivery_release_witnesses",
    "restricted_delivery_generations",
    "dhm_delivery_origins",
    "dhm_origin_sets",
    "dhm_origin_set_members",
    "dhm_rating_declarations",
    "dhm_delivery_observation_attributions",
    "expired_rating_test_permissions",
)


def _tenant(conn: sa.Connection) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO tenants (id, code, name) VALUES (:id, 'chwrr', 'CHWRR Nepal')"
        ),
        {"id": tenant_id},
    )
    return tenant_id


def _tenant_with_code(conn: sa.Connection, code: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    conn.execute(
        sa.text("INSERT INTO tenants (id, code, name) VALUES (:id, :code, :name)"),
        {"id": tenant_id, "code": code, "name": f"Tenant {code}"},
    )
    return tenant_id


def _release(
    conn: sa.Connection,
    tenant_id: uuid.UUID,
    *,
    predecessor_sha256: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[uuid.UUID, bytes, str]:
    release_id = uuid.uuid4()
    content = b'{"station_codes":["225","280","350","404.7","439.7","440","445","447","450","550.05","602","602.5","604.5","610","620","647","650","652","670","684","695"],"rating_declarations":[]}'
    digest = hashlib.sha256(content).hexdigest()
    conn.execute(
        sa.text(
            """
            INSERT INTO restricted_delivery_releases (
                id, tenant_id, network, cohort, manifest_sha256, station_set_sha256,
                expected_predecessor_delivery_id, expected_predecessor_semantic_sha256,
                allowed_new_delivery_id, expires_at, status, created_at
            ) VALUES (
                :id, :tenant_id, 'dhm', 'nepal21', :digest, :station_set,
                'dhm-barkhk-2026-09-08', :predecessor,
                :delivery_id, :expires_at, 'authorized', :created_at
            )
            """
        ),
        {
            "id": release_id,
            "tenant_id": tenant_id,
            "digest": digest,
            "station_set": "1" * 64,
            "predecessor": predecessor_sha256 or "2" * 64,
            "delivery_id": f"dhm-nepal21-{digest[:16]}",
            "expires_at": expires_at,
            "created_at": datetime(2026, 10, 12, tzinfo=UTC),
        },
    )
    return release_id, content, digest


def _station(conn: sa.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    station_id = uuid.uuid4()
    conn.execute(
        sa.text(
            """
            INSERT INTO stations (
                id, code, name, location, station_kind, timezone,
                measured_parameters, station_status, network, ownership,
                gauging_status, tenant_id
            ) VALUES (
                :id, '447', 'Trisuli at Betrawati', ST_GeomFromText('POINT(85 27)', 4326),
                'river', 'Asia/Kathmandu', ARRAY['discharge'], 'onboarding',
                'dhm', 'foreign', 'gauged', :tenant_id
            )
            """
        ),
        {"id": station_id, "tenant_id": tenant_id},
    )
    return station_id


def _seed_previous_delivery(
    conn: sa.Connection, station_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    old_curve_id = uuid.uuid4()
    old_obs_id = uuid.uuid4()
    conn.execute(
        sa.text(
            """
            INSERT INTO rating_curves (
                id, station_id, version, valid_from, valid_to, points,
                interpolation, delivery_id, rating_type_label, created_at
            ) VALUES (
                :id, :station_id, 1, :valid_from, :valid_to,
                '[{"water_level": 0.0, "discharge": 1.0}]'::jsonb,
                'linear', 'dhm-barkhk-2026-09-08', '1', :created_at
            )
            """
        ),
        {
            "id": old_curve_id,
            "station_id": station_id,
            "valid_from": datetime(1990, 1, 1, tzinfo=UTC),
            "valid_to": datetime(1991, 1, 1, tzinfo=UTC),
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO observations (
                id, station_id, timestamp, parameter, value, source,
                rating_curve_id, rating_curve_correction_version,
                delivery_id, qc_status, qc_flags, qc_rule_version, created_at
            ) VALUES (
                :id, :station_id, :timestamp, 'discharge', 1.0,
                'manual_import', :curve_id, '1', 'dhm-barkhk-2026-09-08',
                'raw', '[]'::jsonb, NULL, :created_at
            )
            """
        ),
        {
            "id": old_obs_id,
            "station_id": station_id,
            "timestamp": datetime(1990, 1, 2, tzinfo=UTC),
            "curve_id": old_curve_id,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
    )
    return old_curve_id, old_obs_id


class TestNepal21AuthorityMigration:
    def test_empty_roundtrip(self, migration_engine: tuple[sa.Engine, str]) -> None:
        engine, _ = migration_engine
        config = Config("alembic.ini")
        command.upgrade(config, "0071")
        command.upgrade(config, "0072")
        with engine.connect() as conn:
            inspector = sa.inspect(conn)
            assert set(TABLES).issubset(inspector.get_table_names())
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT to_regprocedure('public.sf_lock_dhm21_delivery_write(uuid, uuid)')"
                    )
                )
                is not None
            )
        command.downgrade(config, "0071")
        with engine.connect() as conn:
            assert set(TABLES).isdisjoint(sa.inspect(conn).get_table_names())
            removed_functions = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        """
                        SELECT proname
                        FROM pg_catalog.pg_proc p
                        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'public'
                          AND proname = ANY(:function_names)
                        """
                    ),
                    {
                        "function_names": [
                            "sf_nepal21_expired_conversion_guard",
                            "sf_lock_dhm21_delivery_write",
                            "sf_lock_dhm21_conversion_append",
                            "sf_lock_dhm21_permission_change",
                            "sf_lock_nepal_static_guard",
                        ]
                    },
                )
            }
            assert removed_functions == set()
            lingering_triggers = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        """
                        SELECT tgname
                        FROM pg_catalog.pg_trigger
                        WHERE NOT tgisinternal
                          AND tgname LIKE ANY(:trigger_patterns)
                        """
                    ),
                    {
                        "trigger_patterns": [
                            "trg_restricted%",
                            "trg_dhm_%",
                            "trg_nepal21_%",
                        ]
                    },
                )
            }
            assert lingering_triggers == set()
            restored = conn.scalar(
                sa.text(
                    "SELECT pg_get_functiondef("
                    "'public.provisional_discharge_insert()'::regprocedure)"
                )
            )
            assert "tenant_code" not in restored
            assert "c.id = newest" in restored
        command.upgrade(config, "0072")

    def test_witness_bytes_are_exact_and_immutable(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, content, digest = _release(conn, tenant_id)
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_release_witnesses
                    (release_id, witness_kind, content_sha256, content_bytes, created_at)
                    VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                    """
                ),
                {
                    "release_id": release_id,
                    "digest": digest,
                    "content": content,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            with pytest.raises(DBAPIError, match="immutable"):
                conn.execute(
                    sa.text(
                        "UPDATE restricted_delivery_release_witnesses SET content_bytes = '\\x7b7d'"
                    )
                )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, content, _ = _release(conn, tenant_id)
            with pytest.raises(DBAPIError, match="do not match"):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO restricted_delivery_release_witnesses
                        (release_id, witness_kind, content_sha256, content_bytes, created_at)
                        VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                        """
                    ),
                    {
                        "release_id": release_id,
                        "digest": "0" * 64,
                        "content": content,
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )

    def test_building_generation_cannot_commit_unpublished(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with (
            pytest.raises(DBAPIError, match="may not commit unpublished"),
            engine.begin() as conn,
        ):
            tenant_id = _tenant(conn)
            release_id, _, digest = _release(conn, tenant_id)
            conn.execute(
                sa.text(
                    """
                        INSERT INTO restricted_delivery_generations (
                            id, release_id, tenant_id, network, cohort, delivery_id,
                            predecessor_delivery_id, predecessor_semantic_sha256,
                            preimage_sha256, manifest_sha256, station_set_sha256,
                            canonicalization_policy_sha256, state, created_at
                        ) VALUES (
                            :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                            'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                            :manifest, :station_set, NULL,
                'building', :created_at
                        )
                        """
                ),
                {
                    "id": uuid.uuid4(),
                    "release_id": release_id,
                    "tenant_id": tenant_id,
                    "delivery_id": f"dhm-nepal21-{digest[:16]}",
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": digest,
                    "station_set": "1" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )

    def test_release_use_guard_refuses_revoked_and_expired_releases(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            revoked_tenant_id = _tenant_with_code(conn, "revoked-release")
            expired_tenant_id = _tenant_with_code(conn, "expired-release")
            revoked_release_id, _, revoked_digest = _release(conn, revoked_tenant_id)
            expired_release_id, _, expired_digest = _release(
                conn,
                expired_tenant_id,
                expires_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            conn.execute(
                sa.text(
                    "UPDATE restricted_delivery_releases "
                    "SET status = 'revoked' WHERE id = :id"
                ),
                {"id": revoked_release_id},
            )
            generation_rows = [
                {
                    "id": uuid.uuid4(),
                    "release_id": revoked_release_id,
                    "tenant_id": revoked_tenant_id,
                    "delivery_id": f"dhm-nepal21-{revoked_digest[:16]}",
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": revoked_digest,
                    "station_set": "1" * 64,
                    "immutable": "4" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    "published_at": datetime(2026, 10, 13, tzinfo=UTC),
                    "expected_error": "restricted delivery release is not authorized",
                },
                {
                    "id": uuid.uuid4(),
                    "release_id": expired_release_id,
                    "tenant_id": expired_tenant_id,
                    "delivery_id": f"dhm-nepal21-{expired_digest[:16]}",
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": expired_digest,
                    "station_set": "1" * 64,
                    "immutable": "4" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    "published_at": datetime(2026, 10, 13, tzinfo=UTC),
                    "expected_error": "restricted delivery release is expired",
                },
            ]
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_generations (
                        id, release_id, tenant_id, network, cohort, delivery_id,
                        predecessor_delivery_id, predecessor_semantic_sha256,
                        preimage_sha256, manifest_sha256, station_set_sha256,
                        canonicalization_policy_sha256, state,
                        immutable_content_sha256, created_at, published_at
                    ) VALUES (
                        :id, :release_id, :tenant_id, 'dhm', 'nepal21',
                        :delivery_id, 'dhm-barkhk-2026-09-08', :predecessor,
                        :preimage, :manifest, :station_set, NULL,
                        'active_pending_qc', :immutable, :created_at,
                        :published_at
                    )
                    """
                ),
                generation_rows,
            )
            for row in generation_rows:
                with (
                    pytest.raises(DBAPIError, match=str(row["expected_error"])),
                    conn.begin_nested(),
                ):
                    conn.execute(
                        sa.text("SELECT sf_mark_restricted_release_used(:id)"),
                        {"id": row["id"]},
                    )

    def test_publication_refuses_zero_delivery_observations(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, []
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            with pytest.raises(
                DBAPIError, match="publication requires delivery observations"
            ):
                _publish_direct_sql(conn, generation_id)

    def test_building_generation_delete_is_refused(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, _, digest = _release(conn, tenant_id)
            generation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_generations (
                        id, release_id, tenant_id, network, cohort, delivery_id,
                        predecessor_delivery_id, predecessor_semantic_sha256,
                        preimage_sha256, manifest_sha256, station_set_sha256,
                        canonicalization_policy_sha256, state, created_at
                    ) VALUES (
                        :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                        'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                        :manifest, :station_set, NULL,
                'building', :created_at
                    )
                    """
                ),
                {
                    "id": generation_id,
                    "release_id": release_id,
                    "tenant_id": tenant_id,
                    "delivery_id": f"dhm-nepal21-{digest[:16]}",
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": digest,
                    "station_set": "1" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            with pytest.raises(DBAPIError, match="may not be deleted"):
                conn.execute(
                    sa.text("DELETE FROM restricted_delivery_generations WHERE id=:id"),
                    {"id": generation_id},
                )

    def test_replacement_transaction_writes_preimage_deletes_old_inserts_new_and_publishes(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            old_curve_id = uuid.uuid4()
            old_obs_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO rating_curves (
                        id, station_id, version, valid_from, valid_to, points,
                        interpolation, delivery_id, rating_type_label, created_at
                    ) VALUES (
                        :id, :station_id, 1, :valid_from, :valid_to,
                        '[{"water_level": 0.0, "discharge": 1.0}]'::jsonb,
                        'linear', 'dhm-barkhk-2026-09-08', '1', :created_at
                    )
                    """
                ),
                {
                    "id": old_curve_id,
                    "station_id": station_id,
                    "valid_from": datetime(1990, 1, 1, tzinfo=UTC),
                    "valid_to": datetime(1991, 1, 1, tzinfo=UTC),
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        rating_curve_id, rating_curve_correction_version,
                        delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 1.0,
                        'manual_import', :curve_id, '1', 'dhm-barkhk-2026-09-08',
                        'raw', '[]'::jsonb, NULL, :created_at
                    )
                    """
                ),
                {
                    "id": old_obs_id,
                    "station_id": station_id,
                    "timestamp": datetime(1990, 1, 2, tzinfo=UTC),
                    "curve_id": old_curve_id,
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            )
            # Use the service fetch for the exact required curve/observation field set.
            fetched = fetch_predecessor_semantic_rows(conn, TenantId(tenant_id))
            predecessor_sha = predecessor_semantic_digest(fetched)
            release_id, content, digest = _release(
                conn, tenant_id, predecessor_sha256=predecessor_sha
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_release_witnesses
                    (release_id, witness_kind, content_sha256, content_bytes, created_at)
                    VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                    """
                ),
                {
                    "release_id": release_id,
                    "digest": digest,
                    "content": content,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            generation_id = uuid.uuid4()
            preimage_sha = sha256_hex_bytes(
                canonical_json_bytes(
                    {
                        "codec": "dhm21-preimage-v1",
                        "generation_id": str(generation_id),
                        "predecessor_delivery_id": "dhm-barkhk-2026-09-08",
                        "rows": [_normalize_predecessor_row(row) for row in fetched],
                    }
                )
            )
            new_curve_id = uuid.uuid4()
            new_obs_id = uuid.uuid4()
            new_obs_id_2 = uuid.uuid4()
            origin_id = uuid.uuid4()
            origin_set = origin_identity_set_sha256(
                [
                    {
                        "member_ordinal": 0,
                        "origin_id": origin_id,
                        "origin_kind": "DFL",
                        "source_root_label": "synthetic_test",
                        "relative_path": "DFL_447.txt",
                        "file_sha256": "7" * 64,
                        "parser_version": "test",
                        "row_or_block_ordinal": 0,
                        "origin_payload_sha256": "8" * 64,
                    }
                ],
                set_kind="DFL_RECORD",
            )
            payload = RestrictedReplacementPayload(
                preimage_dir=tmp_path / "actual",
                rating_curve_rows=[
                    {
                        "id": new_curve_id,
                        "station_id": station_id,
                        "version": 2,
                        "valid_from": datetime(1991, 1, 1, tzinfo=UTC),
                        "valid_to": datetime(1992, 1, 1, tzinfo=UTC),
                        "points": [{"water_level": 0.0, "discharge": 2.0}],
                        "interpolation": "linear",
                        "delivery_id": f"dhm-nepal21-{digest[:16]}",
                        "rating_type_label": "2",
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    }
                ],
                observation_rows=[
                    {
                        "id": new_obs_id,
                        "station_id": station_id,
                        "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                        "parameter": "discharge",
                        "value": 2.0,
                        "source": "manual_import",
                        "rating_curve_id": new_curve_id,
                        "rating_curve_correction_version": "1",
                        "delivery_id": f"dhm-nepal21-{digest[:16]}",
                        "qc_status": "raw",
                        "qc_flags": [],
                        "qc_rule_version": None,
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                    {
                        "id": new_obs_id_2,
                        "station_id": station_id,
                        "timestamp": datetime(1991, 1, 3, tzinfo=UTC),
                        "parameter": "discharge",
                        "value": 3.0,
                        "source": "manual_import",
                        "rating_curve_id": new_curve_id,
                        "rating_curve_correction_version": "1",
                        "delivery_id": f"dhm-nepal21-{digest[:16]}",
                        "qc_status": "raw",
                        "qc_flags": [],
                        "qc_rule_version": None,
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                ],
                origin_rows=[
                    {
                        "id": origin_id,
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "cohort": "nepal21",
                        "origin_kind": "DFL",
                        "source_root_label": "synthetic_test",
                        "relative_path": "DFL_447.txt",
                        "file_sha256": "7" * 64,
                        "parser_version": "test",
                        "row_or_block_ordinal": 0,
                        "origin_payload_sha256": "8" * 64,
                    }
                ],
                origin_set_rows=[
                    {
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "origin_set_sha256": origin_set,
                        "member_count": 1,
                        "set_kind": "DFL_RECORD",
                    }
                ],
                origin_member_rows=[
                    {
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "origin_set_sha256": origin_set,
                        "origin_id": origin_id,
                        "origin_kind": "DFL",
                        "member_ordinal": 0,
                    }
                ],
                declaration_rows=[],
                attribution_rows=[
                    {
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "observation_id": new_obs_id,
                        "station_id": station_id,
                        "cohort": "nepal21",
                        "station_code": "447",
                        "origin_set_sha256": origin_set,
                        "rating_declaration_id": None,
                        "rating_curve_id": None,
                        "attribution_state": "not_applicable",
                        "local_date": datetime(1991, 1, 2, tzinfo=UTC).date(),
                        "reported_q": 2.0,
                    },
                    {
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "observation_id": new_obs_id_2,
                        "station_id": station_id,
                        "cohort": "nepal21",
                        "station_code": "447",
                        "origin_set_sha256": origin_set,
                        "rating_declaration_id": None,
                        "rating_curve_id": None,
                        "attribution_state": "not_applicable",
                        "local_date": datetime(1991, 1, 3, tzinfo=UTC).date(),
                        "reported_q": 3.0,
                    },
                ],
            )
            build = GenerationBuildInput(
                id=generation_id,
                release_id=release_id,
                tenant_id=TenantId(tenant_id),
                delivery_id=f"dhm-nepal21-{digest[:16]}",
                predecessor_semantic_sha256=predecessor_sha,
                preimage_sha256=preimage_sha,
                manifest_sha256=digest,
                station_set_sha256="1" * 64,
                canonicalization_policy_sha256=None,
                created_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
            )
            dry_payload = dataclasses.replace(payload, preimage_dir=tmp_path / "dry")
            assert (
                run_dhm21_delivery_replacement_dry_run(conn, build, dry_payload)
                == preimage_sha
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id='dhm-barkhk-2026-09-08'"
                    )
                )
                == 1
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id LIKE 'dhm-nepal21-%'"
                    )
                )
                == 0
            )
            with pytest.raises(FileExistsError):
                run_dhm21_delivery_replacement_dry_run(conn, build, dry_payload)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id LIKE 'dhm-nepal21-%'"
                    )
                )
                == 0
            )
            run_dhm21_delivery_replacement_transaction(conn, build, payload)
            publish_generation(
                conn,
                generation_id=generation_id,
                immutable_content_sha256="4" * 64,
                published_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id='dhm-barkhk-2026-09-08'"
                    )
                )
                == 0
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id LIKE 'dhm-nepal21-%'"
                    )
                )
                == 2
            )
            generation_row = conn.execute(
                sa.text(
                    """
                    SELECT state, preimage_sha256, immutable_content_sha256,
                           published_at IS NOT NULL AS published
                    FROM restricted_delivery_generations
                    WHERE id = :id
                    """
                ),
                {"id": generation_id},
            ).one()
            assert generation_row == (
                "active_pending_qc",
                preimage_sha,
                "4" * 64,
                True,
            )
            release_row = conn.execute(
                sa.text(
                    """
                    SELECT status, used_at IS NOT NULL AS used
                    FROM restricted_delivery_releases
                    WHERE id = :id
                    """
                ),
                {"id": release_id},
            ).one()
            assert release_row == ("used", True)
            persisted_attribution = conn.execute(
                sa.text(
                    """
                    SELECT observation_id, station_id, origin_set_sha256,
                           attribution_state, local_date, reported_q
                    FROM dhm_delivery_observation_attributions
                    WHERE generation_id = :generation_id
                    ORDER BY local_date, observation_id
                    """
                ),
                {"generation_id": generation_id},
            ).all()
            assert persisted_attribution == [
                (
                    row["observation_id"],
                    row["station_id"],
                    row["origin_set_sha256"],
                    row["attribution_state"],
                    row["local_date"],
                    row["reported_q"],
                )
                for row in sorted(
                    payload.attribution_rows,
                    key=lambda item: (item["local_date"], str(item["observation_id"])),
                )
            ]
            dry_files = list((tmp_path / "dry").glob("*.json"))
            actual_files = list((tmp_path / "actual").glob("*.json"))
            assert len(dry_files) == 1
            assert len(actual_files) == 1
            assert dry_files[0].read_bytes() == actual_files[0].read_bytes()
            assert sha256_hex_bytes(actual_files[0].read_bytes()) == preimage_sha
            run_dhm21_delivery_replacement_transaction(conn, build, payload)
            assert len(list((tmp_path / "actual").glob("*.json"))) == 1
            tampered_payload = dataclasses.replace(
                payload,
                observation_rows=[
                    {**payload.observation_rows[0], "id": uuid.uuid4()},
                    payload.observation_rows[1],
                ],
            )
            with pytest.raises(
                ValueError,
                match="existing restricted generation observation replay mismatch",
            ):
                run_dhm21_delivery_replacement_transaction(
                    conn, build, tampered_payload
                )
            tampered_content_payload = dataclasses.replace(
                payload,
                observation_rows=[
                    {**payload.observation_rows[0], "value": 99.0},
                    payload.observation_rows[1],
                ],
            )
            with pytest.raises(
                ValueError,
                match="existing restricted generation observation replay mismatch",
            ):
                run_dhm21_delivery_replacement_transaction(
                    conn, build, tampered_content_payload
                )
            tampered_origin_payload = dataclasses.replace(
                payload,
                origin_rows=[
                    {**payload.origin_rows[0], "parser_version": "unexpected"},
                ],
            )
            with pytest.raises(
                ValueError,
                match="existing restricted generation origin replay mismatch",
            ):
                run_dhm21_delivery_replacement_transaction(
                    conn, build, tampered_origin_payload
                )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id LIKE 'dhm-nepal21-%'"
                    )
                )
                == 2
            )
            qc_policy_sha = _insert_qc_policy_witness(conn, release_id)
            qc_pin = ExpectedQcPolicyPin(
                policy_sha256=qc_policy_sha,
                rule_set=QcRuleSet(
                    version="synthetic-nepal21",
                    rules=(
                        QcRuleParams(
                            rule_id="range_check",
                            rule_version="synthetic-range",
                            parameter="discharge",
                            time_step=timedelta(days=1),
                            thresholds={"value_min": 0.0, "value_max": 10.0},
                            network="dhm",
                        ),
                    ),
                ),
                station_networks={StationId(station_id): "dhm"},
                overrides=[],
                baselines=[],
            )
            completion_sha = run_dhm21_qc_transaction(
                conn,
                tenant_id=TenantId(tenant_id),
                generation_id=generation_id,
                expected_policy=qc_pin,
                completed_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
            )
            qc_row = conn.execute(
                sa.text(
                    """
                    SELECT o.qc_status, o.qc_rule_version, g.state,
                           g.qc_policy_sha256, g.qc_completion_sha256
                    FROM observations o
                    CROSS JOIN restricted_delivery_generations g
                    WHERE o.id=:observation_id AND g.id=:generation_id
                    """
                ),
                {"observation_id": new_obs_id, "generation_id": generation_id},
            ).one()
            assert qc_row == (
                "qc_passed",
                qc_policy_sha,
                "active_qc_complete",
                qc_policy_sha,
                completion_sha,
            )
            run_dhm21_delivery_replacement_transaction(conn, build, payload)

    def test_actual_writer_runs_replacement_publish_and_qc_service_path(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            predecessor_sha = predecessor_semantic_digest([])
            generation_id = uuid.uuid4()
            release_id, content, digest = _release(
                conn, tenant_id, predecessor_sha256=predecessor_sha
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_release_witnesses
                    (release_id, witness_kind, content_sha256, content_bytes, created_at)
                    VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                    """
                ),
                {
                    "release_id": release_id,
                    "digest": digest,
                    "content": content,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            qc_policy_sha = _insert_qc_policy_witness(conn, release_id)
            _grant_writer_bootstrap_privileges(conn)
        preimage_sha = sha256_hex_bytes(
            canonical_json_bytes(
                {
                    "codec": "dhm21-preimage-v1",
                    "generation_id": str(generation_id),
                    "predecessor_delivery_id": "dhm-barkhk-2026-09-08",
                    "rows": [],
                }
            )
        )
        delivery_id = f"dhm-nepal21-{digest[:16]}"
        observation_id = uuid.uuid4()
        observation_id_2 = uuid.uuid4()
        origin_id = uuid.uuid4()
        origin_identity = {
            "member_ordinal": 0,
            "origin_id": origin_id,
            "origin_kind": "DFL",
            "source_root_label": "synthetic_test",
            "relative_path": "DFL_447_writer.txt",
            "file_sha256": "7" * 64,
            "parser_version": "test",
            "row_or_block_ordinal": 0,
            "origin_payload_sha256": "8" * 64,
        }
        origin_set = origin_identity_set_sha256(
            [origin_identity], set_kind="DFL_RECORD"
        )
        payload = RestrictedReplacementPayload(
            preimage_dir=tmp_path / "writer",
            rating_curve_rows=[],
            observation_rows=[
                {
                    "id": observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                    "parameter": "discharge",
                    "value": 2.0,
                    "source": "manual_import",
                    "rating_curve_id": None,
                    "rating_curve_correction_version": None,
                    "delivery_id": delivery_id,
                    "qc_status": "raw",
                    "qc_flags": [],
                    "qc_rule_version": None,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
                {
                    "id": observation_id_2,
                    "station_id": station_id,
                    "timestamp": datetime(1991, 1, 3, tzinfo=UTC),
                    "parameter": "discharge",
                    "value": 3.0,
                    "source": "manual_import",
                    "rating_curve_id": None,
                    "rating_curve_correction_version": None,
                    "delivery_id": delivery_id,
                    "qc_status": "raw",
                    "qc_flags": [],
                    "qc_rule_version": None,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            ],
            origin_rows=[
                {
                    "id": origin_id,
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "cohort": "nepal21",
                    "origin_kind": "DFL",
                    "source_root_label": "synthetic_test",
                    "relative_path": "DFL_447_writer.txt",
                    "file_sha256": "7" * 64,
                    "parser_version": "test",
                    "row_or_block_ordinal": 0,
                    "origin_payload_sha256": "8" * 64,
                }
            ],
            origin_set_rows=[
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "origin_set_sha256": origin_set,
                    "member_count": 1,
                    "set_kind": "DFL_RECORD",
                }
            ],
            origin_member_rows=[
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "origin_set_sha256": origin_set,
                    "origin_id": origin_id,
                    "origin_kind": "DFL",
                    "member_ordinal": 0,
                }
            ],
            declaration_rows=[],
            attribution_rows=[
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": observation_id,
                    "station_id": station_id,
                    "cohort": "nepal21",
                    "station_code": "447",
                    "origin_set_sha256": origin_set,
                    "rating_declaration_id": None,
                    "rating_curve_id": None,
                    "attribution_state": "not_applicable",
                    "local_date": date(1991, 1, 2),
                    "reported_q": 2.0,
                },
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": observation_id_2,
                    "station_id": station_id,
                    "cohort": "nepal21",
                    "station_code": "447",
                    "origin_set_sha256": origin_set,
                    "rating_declaration_id": None,
                    "rating_curve_id": None,
                    "attribution_state": "not_applicable",
                    "local_date": date(1991, 1, 3),
                    "reported_q": 3.0,
                },
            ],
        )
        build = GenerationBuildInput(
            id=generation_id,
            release_id=release_id,
            tenant_id=TenantId(tenant_id),
            delivery_id=delivery_id,
            predecessor_semantic_sha256=predecessor_sha,
            preimage_sha256=preimage_sha,
            manifest_sha256=digest,
            station_set_sha256="1" * 64,
            canonicalization_policy_sha256=None,
            created_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
        )
        qc_pin = ExpectedQcPolicyPin(
            policy_sha256=qc_policy_sha,
            rule_set=QcRuleSet(
                version="synthetic-nepal21",
                rules=(
                    QcRuleParams(
                        rule_id="range_check",
                        rule_version="synthetic-range",
                        parameter="discharge",
                        time_step=timedelta(days=1),
                        thresholds={"value_min": 0.0, "value_max": 10.0},
                        network="dhm",
                    ),
                ),
            ),
            station_networks={StationId(station_id): "dhm"},
            overrides=[],
            baselines=[],
        )
        with engine.connect() as conn:
            conn.execute(
                sa.text("SET SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
            )
            assert conn.execute(sa.text("SELECT session_user, current_user")).one() == (
                "sapphire_dhm21_delivery_writer",
                "sapphire_dhm21_delivery_writer",
            )
            try:
                run_dhm21_delivery_replacement_transaction(conn, build, payload)
                publish_generation(
                    conn,
                    generation_id=generation_id,
                    immutable_content_sha256="4" * 64,
                    published_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
                )
                completion_sha = run_dhm21_qc_transaction(
                    conn,
                    tenant_id=TenantId(tenant_id),
                    generation_id=generation_id,
                    expected_policy=qc_pin,
                    completed_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
                conn.commit()
        with engine.connect() as conn:
            assert conn.execute(
                sa.text(
                    """
                    SELECT session_user, current_user, state, qc_policy_sha256,
                           qc_completion_sha256
                    FROM restricted_delivery_generations
                    WHERE id = :id
                    """
                ),
                {"id": generation_id},
            ).one() == (
                "test",
                "test",
                "active_qc_complete",
                qc_policy_sha,
                completion_sha,
            )

    def test_replacement_refuses_locked_predecessor_drift(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            old_curve_id, old_obs_id = _seed_previous_delivery(conn, station_id)
            actual_sha = predecessor_semantic_digest(
                fetch_predecessor_semantic_rows(conn, TenantId(tenant_id))
            )
            approved_sha = "f" * 64
            assert actual_sha != approved_sha
            release_id, content, digest = _release(
                conn, tenant_id, predecessor_sha256=approved_sha
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_release_witnesses
                    (release_id, witness_kind, content_sha256, content_bytes, created_at)
                    VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                    """
                ),
                {
                    "release_id": release_id,
                    "digest": digest,
                    "content": content,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            generation_id = uuid.uuid4()
            build = GenerationBuildInput(
                id=generation_id,
                release_id=release_id,
                tenant_id=TenantId(tenant_id),
                delivery_id=f"dhm-nepal21-{digest[:16]}",
                manifest_sha256=digest,
                station_set_sha256="1" * 64,
                predecessor_semantic_sha256=approved_sha,
                preimage_sha256="0" * 64,
                canonicalization_policy_sha256=None,
                created_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
            )
            with pytest.raises(
                ValueError, match="locked predecessor semantic digest differs"
            ):
                run_dhm21_delivery_replacement_transaction(conn, build, None)
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM observations WHERE id = :id"),
                    {"id": old_obs_id},
                )
                == 1
            )
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM rating_curves WHERE id = :id"),
                    {"id": old_curve_id},
                )
                == 1
            )

    def test_qc_runner_preserves_failed_flags_and_refuses_missing_policy(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, content, digest = _release(conn, tenant_id)
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_release_witnesses
                    (release_id, witness_kind, content_sha256, content_bytes, created_at)
                    VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                    """
                ),
                {
                    "release_id": release_id,
                    "digest": digest,
                    "content": content,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            generation_id = uuid.uuid4()
            delivery_id = f"dhm-nepal21-{digest[:16]}"
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_generations (
                        id, release_id, tenant_id, network, cohort, delivery_id,
                        predecessor_delivery_id, predecessor_semantic_sha256,
                        preimage_sha256, manifest_sha256, station_set_sha256,
                        canonicalization_policy_sha256, state, created_at
                    ) VALUES (
                        :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                        'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                        :manifest, :station_set, NULL,
                'building', :created_at
                    )
                    """
                ),
                {
                    "id": generation_id,
                    "release_id": release_id,
                    "tenant_id": tenant_id,
                    "delivery_id": delivery_id,
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": digest,
                    "station_set": "1" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="DFL_RECORD",
                payload_sha="6" * 64,
            )
            obs_ids = [uuid.uuid4(), uuid.uuid4()]
            for obs_id, stamp, value in (
                (obs_ids[0], datetime(1991, 1, 2, tzinfo=UTC), 99.0),
                (obs_ids[1], datetime(1991, 1, 3, tzinfo=UTC), 3.0),
            ):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO observations (
                            id, station_id, timestamp, parameter, value, source,
                            delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                        ) VALUES (
                            :id, :station_id, :timestamp, 'discharge', :value,
                            'manual_import', :delivery_id, 'raw', '[]'::jsonb, NULL, :created_at
                        )
                        """
                    ),
                    {
                        "id": obs_id,
                        "station_id": station_id,
                        "timestamp": stamp,
                        "value": value,
                        "delivery_id": delivery_id,
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO dhm_delivery_observation_attributions (
                            generation_id, tenant_id, observation_id, station_id, cohort,
                            station_code, origin_set_sha256, rating_declaration_id,
                            rating_curve_id, attribution_state, local_date, reported_q
                        ) VALUES (
                            :generation_id, :tenant_id, :observation_id, :station_id,
                            'nepal21', '447', :origin_set, NULL, NULL,
                            'not_applicable', :local_date, :reported_q
                        )
                        """
                    ),
                    {
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "observation_id": obs_id,
                        "station_id": station_id,
                        "origin_set": origin_set,
                        "local_date": stamp.date(),
                        "reported_q": value,
                    },
                )
            conn.execute(
                sa.text(
                    """
                    UPDATE restricted_delivery_generations
                    SET state = 'active_pending_qc', immutable_content_sha256 = :content_sha,
                        published_at = :published_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": generation_id,
                    "content_sha": "4" * 64,
                    "published_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            nested = conn.begin_nested()
            with pytest.raises(DBAPIError, match="QC fields are transition-only"):
                conn.execute(
                    sa.text(
                        """
                        UPDATE restricted_delivery_generations
                        SET qc_policy_sha256 = :qc_policy
                        WHERE id = :id
                        """
                    ),
                    {"id": generation_id, "qc_policy": "9" * 64},
                )
            nested.rollback()
            qc_policy_sha = _insert_qc_policy_witness(conn, release_id)
            missing_network_pin = ExpectedQcPolicyPin(
                policy_sha256=qc_policy_sha,
                rule_set=QcRuleSet(
                    version="synthetic-nepal21",
                    rules=(
                        QcRuleParams(
                            rule_id="range_check",
                            rule_version="synthetic-range",
                            parameter="discharge",
                            time_step=timedelta(days=1),
                            thresholds={"value_min": 0.0, "value_max": 10.0},
                            network="dhm",
                        ),
                    ),
                ),
                station_networks={},
                overrides=[],
                baselines=[],
            )
            with pytest.raises(Exception, match="missing network mapping"):
                run_dhm21_qc_transaction(
                    conn,
                    tenant_id=TenantId(tenant_id),
                    generation_id=generation_id,
                    expected_policy=missing_network_pin,
                    completed_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
                )
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM observations WHERE qc_status='raw'")
                )
                == 2
            )
            valid_pin = dataclasses.replace(
                missing_network_pin, station_networks={StationId(station_id): "dhm"}
            )
            completion = run_dhm21_qc_transaction(
                conn,
                tenant_id=TenantId(tenant_id),
                generation_id=generation_id,
                expected_policy=valid_pin,
                completed_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
            )
            failed = conn.execute(
                sa.text(
                    """
                    SELECT o.qc_status, o.qc_rule_version, o.qc_flags, g.qc_policy_sha256,
                           g.qc_completion_sha256
                    FROM observations o CROSS JOIN restricted_delivery_generations g
                    WHERE o.id=:id AND g.id=:generation_id
                    """
                ),
                {"id": obs_ids[0], "generation_id": generation_id},
            ).one()
            assert failed[0] == "qc_failed"
            assert failed[1] == qc_policy_sha
            assert failed[2][0]["rule_id"] == "range_check"
            assert failed[3] == qc_policy_sha
            assert failed[4] == completion
            nested = conn.begin_nested()
            with pytest.raises(DBAPIError, match="QC fields are transition-only"):
                conn.execute(
                    sa.text(
                        """
                        UPDATE restricted_delivery_generations
                        SET qc_completion_sha256 = :qc_completion
                        WHERE id = :id
                        """
                    ),
                    {"id": generation_id, "qc_completion": "9" * 64},
                )
            nested.rollback()

    def test_accepted_historical_reader_hides_restricted_until_qc_complete_generation(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, content, digest = _release(conn, tenant_id)
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_release_witnesses
                    (release_id, witness_kind, content_sha256, content_bytes, created_at)
                    VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                    """
                ),
                {
                    "release_id": release_id,
                    "digest": digest,
                    "content": content,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        qc_status, qc_flags, qc_rule_version, delivery_id
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 1.0,
                        'manual_import', 'raw', '[]'::jsonb, NULL, :delivery_id
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                    "delivery_id": f"dhm-nepal21-{digest[:16]}",
                },
            )
            store = PgObservationStore(conn)
            assert (
                store.fetch_accepted_historical_observations(
                    StationId(station_id),
                    "discharge",
                    ensure_utc(datetime(1991, 1, 1, tzinfo=UTC)),
                    ensure_utc(datetime(1991, 1, 3, tzinfo=UTC)),
                    qc_status=QcStatus.QC_PASSED,
                )
                == []
            )
            generation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_generations (
                        id, release_id, tenant_id, network, cohort, delivery_id,
                        predecessor_delivery_id, predecessor_semantic_sha256,
                        preimage_sha256, manifest_sha256, station_set_sha256,
                        canonicalization_policy_sha256, state, created_at
                    ) VALUES (
                        :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                        'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                        :manifest, :station_set, NULL,
                'building', :created_at
                    )
                    """
                ),
                {
                    "id": generation_id,
                    "release_id": release_id,
                    "tenant_id": tenant_id,
                    "delivery_id": f"dhm-nepal21-{digest[:16]}",
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": digest,
                    "station_set": "1" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="DFL_RECORD",
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_delivery_observation_attributions (
                        generation_id, tenant_id, observation_id, station_id, cohort,
                        station_code, origin_set_sha256, attribution_state,
                        local_date, reported_q
                    ) VALUES (
                        :generation_id, :tenant_id, :observation_id, :station_id, 'nepal21',
                        '447', :origin_set, 'held_missing_curve', :local_date, 1.0
                    )
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": observation_id,
                    "station_id": station_id,
                    "origin_set": origin_set,
                    "local_date": date(1991, 1, 2),
                },
            )
            conn.execute(
                sa.text(
                    """
                    UPDATE restricted_delivery_generations
                    SET state = 'active_pending_qc', immutable_content_sha256 = :content_sha,
                        published_at = :published_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": generation_id,
                    "content_sha": "4" * 64,
                    "published_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            conn.execute(
                sa.text(
                    """
                    UPDATE observations
                    SET qc_status = 'qc_passed', qc_rule_version = 'policy'
                    WHERE station_id = :station_id AND delivery_id = :delivery_id
                    """
                ),
                {"station_id": station_id, "delivery_id": f"dhm-nepal21-{digest[:16]}"},
            )
            missing_policy = conn.begin_nested()
            try:
                with pytest.raises(
                    DBAPIError,
                    match="QC completion requires release-bound QC policy witness",
                ):
                    conn.execute(
                        sa.text(
                            """
                            UPDATE restricted_delivery_generations
                            SET state = 'active_qc_complete', qc_policy_sha256 = :qc_policy,
                                qc_completion_sha256 = :qc_completion, qc_completed_at = :qc_completed_at
                            WHERE id = :id
                            """
                        ),
                        {
                            "id": generation_id,
                            "qc_policy": "5" * 64,
                            "qc_completion": "6" * 64,
                            "qc_completed_at": datetime(2026, 10, 12, tzinfo=UTC),
                        },
                    )
            finally:
                missing_policy.rollback()
            qc_policy_sha = _insert_qc_policy_witness(conn, release_id)
            conn.execute(
                sa.text(
                    """
                    UPDATE restricted_delivery_generations
                    SET state = 'active_qc_complete', qc_policy_sha256 = :qc_policy,
                        qc_completion_sha256 = :qc_completion, qc_completed_at = :qc_completed_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": generation_id,
                    "qc_policy": qc_policy_sha,
                    "qc_completion": "6" * 64,
                    "qc_completed_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            accepted_rows = store.fetch_accepted_historical_observations(
                StationId(station_id),
                "discharge",
                ensure_utc(datetime(1991, 1, 1, tzinfo=UTC)),
                ensure_utc(datetime(1991, 1, 3, tzinfo=UTC)),
                qc_status=QcStatus.QC_PASSED,
            )
            assert len(accepted_rows) == 1
            assert (
                store.fetch_accepted_historical_observations(
                    StationId(station_id),
                    "discharge",
                    ensure_utc(datetime(1991, 1, 1, tzinfo=UTC)),
                    ensure_utc(datetime(1991, 1, 3, tzinfo=UTC)),
                    qc_status=QcStatus.QC_PASSED,
                    expected_qc_policy_sha256=qc_policy_sha,
                )
                == accepted_rows
            )
            assert (
                store.fetch_accepted_historical_observations(
                    StationId(station_id),
                    "discharge",
                    ensure_utc(datetime(1991, 1, 1, tzinfo=UTC)),
                    ensure_utc(datetime(1991, 1, 3, tzinfo=UTC)),
                    qc_status=QcStatus.QC_PASSED,
                    expected_qc_policy_sha256="9" * 64,
                )
                == []
            )

    def test_authorized_release_alone_does_not_hide_legacy_old6_history(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            obs_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        qc_status, qc_flags, qc_rule_version, delivery_id
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 1.0,
                        'manual_import', 'qc_passed', '[]'::jsonb, 'policy',
                        'dhm-barkhk-2026-09-08'
                    )
                    """
                ),
                {
                    "id": obs_id,
                    "station_id": station_id,
                    "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                },
            )
            _insert_policy_release(conn, tenant_id, [])
            store = PgObservationStore(conn)
            assert [
                row.id
                for row in store.fetch_accepted_historical_observations(
                    StationId(station_id),
                    "discharge",
                    ensure_utc(datetime(1991, 1, 1, tzinfo=UTC)),
                    ensure_utc(datetime(1991, 1, 3, tzinfo=UTC)),
                    qc_status=QcStatus.QC_PASSED,
                )
            ] == [obs_id]

    def test_revoked_release_does_not_hide_legacy_old6_history(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            obs_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        qc_status, qc_flags, qc_rule_version, delivery_id
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 1.0,
                        'manual_import', 'qc_passed', '[]'::jsonb, 'policy',
                        'dhm-barkhk-2026-09-08'
                    )
                    """
                ),
                {
                    "id": obs_id,
                    "station_id": station_id,
                    "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                },
            )
            release_id, _, _ = _insert_policy_release(conn, tenant_id, [])
            conn.execute(
                sa.text(
                    """
                    UPDATE restricted_delivery_releases
                    SET status = 'revoked'
                    WHERE id = :release_id
                    """
                ),
                {"release_id": release_id},
            )
            store = PgObservationStore(conn)
            assert [
                row.id
                for row in store.fetch_accepted_historical_observations(
                    StationId(station_id),
                    "discharge",
                    ensure_utc(datetime(1991, 1, 1, tzinfo=UTC)),
                    ensure_utc(datetime(1991, 1, 3, tzinfo=UTC)),
                    qc_status=QcStatus.QC_PASSED,
                )
            ] == [obs_id]

    def test_cross_tenant_qc_complete_successor_does_not_hide_legacy_old6_history(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            other_tenant_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO tenants (id, code, name)
                    VALUES (:id, 'other-tenant', 'Other tenant')
                    """
                ),
                {"id": other_tenant_id},
            )
            obs_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        qc_status, qc_flags, qc_rule_version, delivery_id
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 1.0,
                        'manual_import', 'qc_passed', '[]'::jsonb, 'policy',
                        'dhm-barkhk-2026-09-08'
                    )
                    """
                ),
                {
                    "id": obs_id,
                    "station_id": station_id,
                    "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                },
            )
            other_release_id, _, other_digest = _release(conn, other_tenant_id)
            other_qc_policy = _insert_qc_policy_witness(conn, other_release_id)
            other_generation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_generations (
                        id, release_id, tenant_id, network, cohort, delivery_id,
                        predecessor_delivery_id, predecessor_semantic_sha256,
                        preimage_sha256, manifest_sha256, station_set_sha256,
                        state, immutable_content_sha256, qc_policy_sha256,
                        qc_completion_sha256, created_at, published_at, qc_completed_at
                    ) VALUES (
                        :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                        'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                        :manifest, :station_set, 'active_qc_complete', :content_sha,
                        :qc_policy, :qc_completion, :created_at, :published_at,
                        :qc_completed_at
                    )
                    """
                ),
                {
                    "id": other_generation_id,
                    "release_id": other_release_id,
                    "tenant_id": other_tenant_id,
                    "delivery_id": f"dhm-nepal21-{other_digest[:16]}",
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": other_digest,
                    "station_set": "1" * 64,
                    "content_sha": "4" * 64,
                    "qc_policy": other_qc_policy,
                    "qc_completion": "6" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    "published_at": datetime(2026, 10, 12, tzinfo=UTC),
                    "qc_completed_at": datetime(2026, 10, 13, tzinfo=UTC),
                },
            )
            store = PgObservationStore(conn)
            assert [
                row.id
                for row in store.fetch_accepted_historical_observations(
                    StationId(station_id),
                    "discharge",
                    ensure_utc(datetime(1991, 1, 1, tzinfo=UTC)),
                    ensure_utc(datetime(1991, 1, 3, tzinfo=UTC)),
                    qc_status=QcStatus.QC_PASSED,
                )
            ] == [obs_id]

    def test_direct_sql_expired_conversion_requires_nepal21_permission(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            args = seed(conn, tenant_id=TenantId(tenant_id))
            permit_fixture(conn, TenantId(tenant_id))
            result = convert(*args)
            with (
                pytest.raises(DBAPIError, match="QC-complete generation"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO provisional_discharges (
                            fingerprint, tenant_id, station_id, observation_id,
                            rating_curve_id, feed_evidence_id, reference_proof_id,
                            discharge, content, captured_at
                        ) VALUES (
                            :fingerprint, :tenant_id, :station_id, :observation_id,
                            :rating_curve_id, :feed_evidence_id, :reference_proof_id,
                            :discharge, :content, :captured_at
                        )
                        """
                    ),
                    {**raw_values(result), "captured_at": NOW},
                )

    def test_downgrade_removes_nepal21_expired_guard_and_restores_provisional_insert(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        tenant_id: uuid.UUID
        raw_insert_values: dict[str, object]
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            args = seed(conn, tenant_id=TenantId(tenant_id))
            permit_fixture(conn, TenantId(tenant_id))
            raw_insert_values = {**raw_values(convert(*args)), "captured_at": NOW}
            with pytest.raises(DBAPIError, match="QC-complete generation"):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO provisional_discharges (
                            fingerprint, tenant_id, station_id, observation_id,
                            rating_curve_id, feed_evidence_id, reference_proof_id,
                            discharge, content, captured_at
                        ) VALUES (
                            :fingerprint, :tenant_id, :station_id, :observation_id,
                            :rating_curve_id, :feed_evidence_id, :reference_proof_id,
                            :discharge, :content, :captured_at
                        )
                        """
                    ),
                    raw_insert_values,
                )
        command.downgrade(Config("alembic.ini"), "0071")
        with engine.begin() as conn:
            assert not conn.scalar(
                sa.text(
                    "SELECT to_regclass('public.expired_rating_test_permissions') IS NOT NULL"
                )
            )
            downgraded_tenant_id = _tenant(conn)
            downgraded_args = seed(conn, tenant_id=TenantId(downgraded_tenant_id))
            permit_fixture(conn, TenantId(downgraded_tenant_id))
            downgraded_values = {
                **raw_values(convert(*downgraded_args)),
                "captured_at": NOW,
            }
            insert_sql = sa.text(
                """
                INSERT INTO provisional_discharges (
                    fingerprint, tenant_id, station_id, observation_id,
                    rating_curve_id, feed_evidence_id, reference_proof_id,
                    discharge, content, captured_at
                ) VALUES (
                    :fingerprint, :tenant_id, :station_id, :observation_id,
                    :rating_curve_id, :feed_evidence_id, :reference_proof_id,
                    :discharge, :content, :captured_at
                )
                """
            )
            conn.execute(insert_sql, downgraded_values)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM provisional_discharges "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": downgraded_tenant_id},
                )
                == 1
            )

            stale_curve_tenant_id = _tenant_with_code(conn, "stale-curve-0068")
            stale_args = seed(conn, tenant_id=TenantId(stale_curve_tenant_id))
            permit_fixture(conn, TenantId(stale_curve_tenant_id))
            stale_values = {**raw_values(convert(*stale_args)), "captured_at": NOW}
            _, stale_curve, _, _ = stale_args
            stale_points = conn.scalar(
                sa.text("SELECT points FROM rating_curves WHERE id = :id"),
                {"id": stale_curve.id},
            )
            assert stale_points is not None
            conn.execute(
                sa.text(
                    """
                    INSERT INTO rating_curves (
                        id, station_id, version, valid_from, valid_to, points,
                        interpolation, created_at
                    ) VALUES (
                        :id, :station_id, :version, :valid_from, :valid_to,
                        CAST(:points AS jsonb), :interpolation, :created_at
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "station_id": stale_curve.station_id,
                    "version": stale_curve.version + 1,
                    "valid_from": stale_curve.valid_from + timedelta(days=1),
                    "valid_to": stale_curve.valid_to,
                    "points": json.dumps(stale_points),
                    "interpolation": stale_curve.interpolation,
                    "created_at": NOW,
                },
            )
            with pytest.raises(
                DBAPIError, match="snapshot, QC, curve or reference disagreement"
            ):
                conn.execute(insert_sql, stale_values)

    def test_nepal_static_guard_serializes_concurrent_importers(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
        with engine.connect() as first, engine.connect() as second:
            trans = first.begin()
            try:
                first.execute(
                    sa.text("SELECT sf_lock_nepal_static_guard(:tenant_id)"),
                    {"tenant_id": tenant_id},
                )
                second.execute(sa.text("SET lock_timeout = '250ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    second.execute(
                        sa.text("SELECT sf_lock_nepal_static_guard(:tenant_id)"),
                        {"tenant_id": tenant_id},
                    )
            finally:
                trans.rollback()

    def test_lock_preconditions_fail_before_waiting_on_tenant_lock(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
        tenant_lock_sql = sa.text(
            """
            SELECT pg_advisory_xact_lock(
                1397115979,
                ('x' || substr(replace(CAST(:tenant_id AS uuid)::text, '-', ''), 1, 8))::bit(32)::int
            )
            """
        )
        cases = [
            (
                "SELECT sf_lock_dhm21_delivery_write(:tenant_id, :id)",
                "restricted delivery release is absent or foreign",
            ),
            (
                "SELECT sf_lock_dhm21_conversion_append(:tenant_id, :id)",
                "restricted delivery generation is absent or foreign",
            ),
            (
                "SELECT sf_lock_dhm21_permission_change(:tenant_id, :id)",
                "restricted delivery generation is absent or foreign",
            ),
        ]
        with engine.connect() as first:
            trans = first.begin()
            try:
                first.execute(tenant_lock_sql, {"tenant_id": tenant_id})
                for sql, message in cases:
                    with engine.connect() as second:
                        second.execute(sa.text("SET lock_timeout = '250ms'"))
                        with pytest.raises(DBAPIError, match=message):
                            second.execute(
                                sa.text(sql),
                                {"tenant_id": tenant_id, "id": uuid.uuid4()},
                            )
            finally:
                trans.rollback()

    def test_conversion_append_lock_serializes_concurrent_nepal21_writers(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, content, digest = _release(conn, tenant_id)
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_release_witnesses
                    (release_id, witness_kind, content_sha256, content_bytes, created_at)
                    VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                    """
                ),
                {
                    "release_id": release_id,
                    "digest": digest,
                    "content": content,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            generation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_generations (
                        id, release_id, tenant_id, network, cohort, delivery_id,
                        predecessor_delivery_id, predecessor_semantic_sha256,
                        preimage_sha256, manifest_sha256, station_set_sha256,
                        state, immutable_content_sha256, qc_policy_sha256,
                        qc_completion_sha256, created_at, published_at, qc_completed_at
                    ) VALUES (
                        :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                        'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                        :manifest, :station_set, 'active_qc_complete', :content_sha,
                        :qc_policy, :qc_completion, :created_at, :published_at,
                        :qc_completed_at
                    )
                    """
                ),
                {
                    "id": generation_id,
                    "release_id": release_id,
                    "tenant_id": tenant_id,
                    "delivery_id": f"dhm-nepal21-{digest[:16]}",
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": digest,
                    "station_set": "1" * 64,
                    "content_sha": "4" * 64,
                    "qc_policy": "5" * 64,
                    "qc_completion": "6" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    "published_at": datetime(2026, 10, 12, tzinfo=UTC),
                    "qc_completed_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
        with engine.connect() as first, engine.connect() as second:
            trans = first.begin()
            try:
                first.execute(
                    sa.text(
                        "SELECT sf_lock_dhm21_conversion_append(:tenant_id, :generation_id)"
                    ),
                    {"tenant_id": tenant_id, "generation_id": generation_id},
                )
                first_pid = first.scalar(sa.text("SELECT pg_backend_pid()"))
                advisory_locks = set(
                    first.execute(
                        sa.text(
                            """
                            SELECT classid::bigint
                            FROM pg_catalog.pg_locks
                            WHERE pid = :pid AND locktype = 'advisory' AND granted
                            """
                        ),
                        {"pid": first_pid},
                    ).scalars()
                )
                assert 1397115979 in advisory_locks
                relation_locks = set(
                    first.execute(
                        sa.text(
                            """
                            SELECT c.relname
                            FROM pg_catalog.pg_locks l
                            JOIN pg_catalog.pg_class c ON c.oid = l.relation
                            WHERE l.pid = :pid
                              AND l.locktype = 'relation'
                              AND l.mode = 'ShareRowExclusiveLock'
                              AND l.granted
                            """
                        ),
                        {"pid": first_pid},
                    ).scalars()
                )
                assert {
                    "restricted_delivery_generations",
                    "provisional_discharge_permissions",
                    "expired_rating_test_permissions",
                    "dhm_delivery_origins",
                    "dhm_origin_sets",
                    "dhm_origin_set_members",
                    "dhm_rating_declarations",
                    "dhm_delivery_observation_attributions",
                    "rating_curves",
                    "observations",
                }.issubset(relation_locks)
                second.execute(sa.text("SET lock_timeout = '250ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    second.execute(
                        sa.text(
                            "SELECT sf_lock_dhm21_conversion_append(:tenant_id, :generation_id)"
                        ),
                        {"tenant_id": tenant_id, "generation_id": generation_id},
                    )
            finally:
                trans.rollback()

    def test_store_expired_conversion_requires_permission_fingerprint_and_catalog_selection(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            args = seed(conn, tenant_id=TenantId(tenant_id))
            observation, curve, _, _ = args
            conn.execute(
                sa.text(
                    "UPDATE stations SET code = '447', network = 'dhm' WHERE id = :id"
                ),
                {"id": observation.station_id},
            )
            curve_points_native = cast(
                "list[dict[str, float]]", cast("Any", curve).points
            )
            curve_valid_to = curve.valid_to
            assert curve_valid_to is not None
            curve_points: list[dict[str, float]] = [
                {"stage_m": point["water_level"], "discharge_m3s": point["discharge"]}
                for point in curve_points_native
            ]
            curve_from_local_date = (
                curve.valid_from + timedelta(hours=5, minutes=45)
            ).date()
            curve_to_local_date = (
                curve_valid_to + timedelta(hours=5, minutes=45)
            ).date()
            raw_payload = _raw_rating_payload(
                "447",
                from_date=curve_from_local_date.isoformat(),
                to_date=curve_to_local_date.isoformat(),
                points=curve_points,
            )
            effective_payload = _effective_payload("447", points=curve_points)
            native_payload = _native_payload(
                valid_from=curve.valid_from.isoformat(),
                valid_to=curve_valid_to.isoformat(),
                points=curve_points_native,
            )
            declaration = _decl(
                "447",
                sha256_hex_bytes(canonical_json_bytes(raw_payload)),
                raw_payload,
                from_date=curve_from_local_date.isoformat(),
                shape="raw_usable",
            )
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_generations (
                        id, release_id, tenant_id, network, cohort, delivery_id,
                        predecessor_delivery_id, predecessor_semantic_sha256,
                        preimage_sha256, manifest_sha256, station_set_sha256,
                        canonicalization_policy_sha256, state, created_at
                    ) VALUES (
                        :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                        'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                        :manifest, :station_set, NULL,
                'building', :created_at
                    )
                    """
                ),
                {
                    "id": generation_id,
                    "release_id": release_id,
                    "tenant_id": tenant_id,
                    "delivery_id": delivery_id,
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": digest,
                    "station_set": "1" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="RT_DECLARATION",
                payload_sha="8" * 64,
            )
            declaration_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_rating_declarations (
                        id, generation_id, tenant_id, station_id, cohort, station_code,
                        from_local_date, to_local_date_inclusive, rating_type_label,
                        from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                        function_payload_json, function_equivalence_sha256,
                        native_curve_payload_json, shape_state, usable_rating_curve_id,
                        origin_set_sha256
                    ) VALUES (
                        :id, :generation_id, :tenant_id, :station_id, 'nepal21', '447',
                        :from_local_date, :to_date, '1', 0.0,
                        CAST(:raw AS jsonb), :decl_sha,
                        CAST(:function AS jsonb), :function_sha,
                        (
                            SELECT jsonb_build_object(
                                'points', points,
                                'interpolation', interpolation,
                                'rating_type_label', rating_type_label,
                                'valid_from', valid_from,
                                'valid_to', valid_to
                            )
                            FROM rating_curves WHERE id = :curve_id
                        ), 'raw_usable', :curve_id,
                        :origin_set
                    )
                    """
                ),
                {
                    "id": declaration_id,
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "station_id": observation.station_id,
                    "raw": json.dumps(raw_payload),
                    "decl_sha": declaration["declaration_content_sha256"],
                    "function": json.dumps(effective_payload),
                    "function_sha": sha256_hex_bytes(
                        canonical_json_bytes(effective_payload)
                    ),
                    "native": json.dumps(native_payload),
                    "curve_id": curve.id,
                    "origin_set": origin_set,
                    "from_local_date": curve_from_local_date,
                    "to_date": curve_to_local_date,
                },
            )
            delivery_observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 1.0,
                        'manual_import', :delivery_id, 'raw', '[]'::jsonb, NULL, :created_at
                    )
                    """
                ),
                {
                    "id": delivery_observation_id,
                    "station_id": observation.station_id,
                    "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            dfl_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="DFL_RECORD",
                payload_sha="6" * 64,
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_delivery_observation_attributions (
                        generation_id, tenant_id, observation_id, station_id, cohort,
                        station_code, origin_set_sha256, rating_declaration_id,
                        rating_curve_id, attribution_state, local_date, reported_q
                    ) VALUES (
                        :generation_id, :tenant_id, :observation_id, :station_id,
                        'nepal21', '447', :origin_set, NULL, NULL,
                        'not_applicable', :local_date, 1.0
                    )
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": delivery_observation_id,
                    "station_id": observation.station_id,
                    "origin_set": dfl_origin_set,
                    "local_date": date(1991, 1, 2),
                },
            )
            conn.execute(
                sa.text(
                    """
                    UPDATE restricted_delivery_generations
                    SET state = 'active_pending_qc', immutable_content_sha256 = :content_sha,
                        published_at = :published_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": generation_id,
                    "content_sha": "4" * 64,
                    "published_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            conn.execute(
                sa.text(
                    """
                    UPDATE observations
                    SET qc_status = 'qc_passed', qc_rule_version = 'policy'
                    WHERE id = :id
                    """
                ),
                {"id": delivery_observation_id},
            )
            qc_policy_sha = _insert_qc_policy_witness(conn, release_id)
            conn.execute(
                sa.text(
                    """
                    UPDATE restricted_delivery_generations
                    SET state = 'active_qc_complete', qc_policy_sha256 = :qc_policy,
                        qc_completion_sha256 = :qc_completion, qc_completed_at = :qc_completed_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": generation_id,
                    "qc_policy": qc_policy_sha,
                    "qc_completion": "6" * 64,
                    "qc_completed_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            permission_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO expired_rating_test_permissions (
                        id, tenant_id, generation_id, cohort, permission_version,
                        state, owner_authorization_sha256, precedence_sha256,
                        selection_manifest_sha256, enabled_at
                    ) VALUES (
                        :id, :tenant_id, :generation_id, 'nepal21', 1, 'enabled',
                        :owner_hash, :precedence_hash, :selection_hash, :enabled_at
                    )
                    """
                ),
                {
                    "id": permission_id,
                    "tenant_id": tenant_id,
                    "generation_id": generation_id,
                    "owner_hash": "a" * 64,
                    "precedence_hash": "b" * 64,
                    "selection_hash": "c" * 64,
                    "enabled_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            permit_fixture(conn, TenantId(tenant_id))
            base = convert(*args)
            with pytest.raises(ValueError, match="permission fingerprint"):
                PgProvisionalDischargeStore(conn).store_provisional_discharge(
                    base, captured_at=NOW
                )
            with (
                pytest.raises(DBAPIError, match="permission fingerprint"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.insert(provisional_discharges).values(
                        **raw_values(base), captured_at=NOW
                    )
                )
            with (
                pytest.raises(Exception, match="permission fingerprint"),
                conn.begin_nested(),
            ):
                _copy_provisional_discharge_row(conn, base)
            document = json.loads(base.content)
            document["nepal21_expired_permission"] = {
                "permission_id": str(permission_id),
                "permission_version": 1,
                "generation_id": str(generation_id),
                "owner_authorization_sha256": "a" * 64,
                "precedence_sha256": "b" * 64,
                "selection_manifest_sha256": "c" * 64,
                "qc_policy_sha256": qc_policy_sha,
                "qc_completion_sha256": "6" * 64,
                "selected_rating_curve_id": str(curve.id),
            }
            content_with_permission = canonical_content(document)
            protected = dataclasses.replace(
                base,
                content=content_with_permission,
                fingerprint=content_digest(content_with_permission),
            )
            newer_curve = conn.begin_nested()
            try:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO rating_curves (
                            id, station_id, version, valid_from, valid_to, points,
                            interpolation, delivery_id, rating_type_label, created_at
                        ) VALUES (
                            :id, :station_id, 99, :valid_from, :valid_to,
                            '[{"water_level": 1.0, "discharge": 10.0}, {"water_level": 2.0, "discharge": 20.0}]'::jsonb,
                            'linear', 'native-newer-test', '1', :created_at
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "station_id": observation.station_id,
                        "valid_from": datetime(2025, 7, 1, tzinfo=UTC),
                        "valid_to": datetime(2026, 1, 1, tzinfo=UTC),
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
                with pytest.raises(
                    ValueError, match="unmatched native rating candidate"
                ):
                    PgProvisionalDischargeStore(conn).store_provisional_discharge(
                        protected, captured_at=NOW
                    )
                with (
                    pytest.raises(
                        DBAPIError, match="unmatched native rating candidate"
                    ),
                    conn.begin_nested(),
                ):
                    conn.execute(
                        sa.insert(provisional_discharges).values(
                            **raw_values(protected), captured_at=NOW
                        )
                    )
                with (
                    pytest.raises(Exception, match="unmatched native rating candidate"),
                    conn.begin_nested(),
                ):
                    _copy_provisional_discharge_row(conn, protected)
            finally:
                newer_curve.rollback()
            current_curve = conn.begin_nested()
            try:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO rating_curves (
                            id, station_id, version, valid_from, valid_to, points,
                            interpolation, delivery_id, rating_type_label, created_at
                        ) VALUES (
                            :id, :station_id, 100, :valid_from, :valid_to,
                            '[{"water_level": 1.0, "discharge": 11.0}, {"water_level": 2.0, "discharge": 21.0}]'::jsonb,
                            'linear', 'native-current-test', '1', :created_at
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "station_id": observation.station_id,
                        "valid_from": datetime(2025, 7, 1, tzinfo=UTC),
                        "valid_to": datetime(2027, 1, 1, tzinfo=UTC),
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
                with pytest.raises(
                    ValueError, match="unmatched native rating candidate"
                ):
                    PgProvisionalDischargeStore(conn).store_provisional_discharge(
                        protected, captured_at=NOW
                    )
            finally:
                current_curve.rollback()
            equal_start = conn.begin_nested()
            try:
                with pytest.raises(
                    DBAPIError,
                    match="catalog rows may only be inserted while generation is building",
                ):
                    conn.execute(
                        sa.text(
                            """
                            INSERT INTO dhm_rating_declarations (
                                id, generation_id, tenant_id, station_id, cohort, station_code,
                                from_local_date, to_local_date_inclusive, rating_type_label,
                                from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                                function_payload_json, function_equivalence_sha256,
                                native_curve_payload_json, shape_state, usable_rating_curve_id,
                                origin_set_sha256
                            ) VALUES (
                                :id, :generation_id, :tenant_id, :station_id, 'nepal21', '447',
                                :from_local_date, '2099-12-31', '1', 0.0,
                                '{"case":"raw-tie"}'::jsonb, :decl_sha,
                                '{"case":"effective-tie"}'::jsonb, :function_sha,
                                '{"case":"native-tie"}'::jsonb, 'raw_usable', :curve_id,
                                :origin_set
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "generation_id": generation_id,
                            "tenant_id": tenant_id,
                            "station_id": observation.station_id,
                            "from_local_date": curve.valid_from.date(),
                            "decl_sha": "d" * 64,
                            "function_sha": "e" * 64,
                            "curve_id": curve.id,
                            "origin_set": origin_set,
                        },
                    )
            finally:
                equal_start.rollback()
            invalid_latest = conn.begin_nested()
            try:
                with pytest.raises(
                    DBAPIError,
                    match="catalog rows may only be inserted while generation is building",
                ):
                    conn.execute(
                        sa.text(
                            """
                            INSERT INTO dhm_rating_declarations (
                                id, generation_id, tenant_id, station_id, cohort, station_code,
                                from_local_date, to_local_date_inclusive, rating_type_label,
                                from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                                function_payload_json, function_equivalence_sha256,
                                native_curve_payload_json, shape_state, usable_rating_curve_id,
                                origin_set_sha256
                            ) VALUES (
                                :id, :generation_id, :tenant_id, :station_id, 'nepal21', '447',
                                :from_local_date, '2099-12-31', '1', 0.0,
                                '{"case":"raw-invalid"}'::jsonb, :decl_sha,
                                NULL, NULL,
                                NULL, 'decreasing_invalid', NULL,
                                :origin_set
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "generation_id": generation_id,
                            "tenant_id": tenant_id,
                            "station_id": observation.station_id,
                            "from_local_date": date(2026, 1, 1),
                            "decl_sha": "f" * 64,
                            "origin_set": origin_set,
                        },
                    )
            finally:
                invalid_latest.rollback()

            conn.execute(
                sa.text(
                    """
                    INSERT INTO rating_curves (
                        id, station_id, version, valid_from, valid_to, points,
                        interpolation, delivery_id, rating_type_label, created_at
                    ) VALUES (
                        :id, :station_id, 99, :valid_from, :valid_to,
                        '[{"water_level": 1.0, "discharge": 10.0}, {"water_level": 2.0, "discharge": 20.0}]'::jsonb,
                        'linear', 'native-equivalent-test', :rating_type_label, :created_at
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "station_id": observation.station_id,
                    "valid_from": curve.valid_from,
                    "valid_to": curve.valid_to,
                    "rating_type_label": curve.rating_type_label,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            direct_sql = conn.begin_nested()
            try:
                conn.execute(
                    sa.insert(provisional_discharges).values(
                        **raw_values(protected), captured_at=NOW
                    )
                )
            finally:
                direct_sql.rollback()
            direct_copy = conn.begin_nested()
            try:
                _copy_provisional_discharge_row(conn, protected)
            finally:
                direct_copy.rollback()
            assert (
                PgProvisionalDischargeStore(conn).store_provisional_discharge(
                    protected, captured_at=NOW
                )
                == protected.fingerprint
            )

    def test_direct_restricted_delivery_sql_is_refused_for_runtime_roles(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            conn.execute(sa.text("CREATE ROLE sapphire_worker LOGIN"))
            conn.execute(sa.text("GRANT INSERT ON observations TO sapphire_worker"))
            conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO sapphire_worker"))
        with engine.connect() as conn:
            allowed = conn.begin()
            conn.execute(sa.text("SET SESSION AUTHORIZATION sapphire_worker"))
            ordinary_observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        qc_status, delivery_id
                    ) VALUES (
                        :id, :station_id, :timestamp,
                        'discharge', 1.0, 'manual_import', 'raw',
                        'ordinary-manual-import'
                    )
                    """
                ),
                {
                    "id": ordinary_observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
                },
            )
            allowed.commit()
            denied = conn.begin()
            with pytest.raises(DBAPIError, match="restricted DHM delivery"):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO observations (
                            id, station_id, timestamp, parameter, value, source,
                            qc_status, delivery_id
                        ) VALUES (
                            gen_random_uuid(), :station_id, now(),
                            'discharge', 1.0, 'manual_import', 'raw',
                            'dhm-nepal21-0000000000000000'
                        )
                        """
                    ),
                    {"station_id": station_id},
                )
            denied.rollback()
            denied_copy = conn.begin()
            driver_connection = cast("Any", conn.connection.driver_connection)
            cursor = driver_connection.cursor()
            with (
                pytest.raises(Exception, match="restricted DHM delivery"),
                cursor.copy(
                    "COPY public.observations ("
                    "id, station_id, timestamp, parameter, value, source, "
                    "qc_status, delivery_id) FROM STDIN"
                ) as copy,
            ):
                copy.write_row(
                    (
                        uuid.uuid4(),
                        station_id,
                        datetime(2026, 1, 2, tzinfo=UTC),
                        "discharge",
                        2.0,
                        "manual_import",
                        "raw",
                        "dhm-nepal21-0000000000000000",
                    )
                )
            denied_copy.rollback()
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
            conn.commit()
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM observations WHERE id = :id"),
                    {"id": ordinary_observation_id},
                )
                == 1
            )

    def test_runtime_worker_cannot_clear_restricted_delivery_tag(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            observation_id = uuid.uuid4()
            ordinary_observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        qc_status, delivery_id
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 1.0,
                        'manual_import', 'raw', 'dhm-barkhk-2026-09-08'
                    ), (
                        :ordinary_id, :station_id, :ordinary_timestamp, 'discharge', 2.0,
                        'manual_import', 'raw', 'ordinary-manual-import'
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "ordinary_id": ordinary_observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
                    "ordinary_timestamp": datetime(2026, 1, 2, tzinfo=UTC),
                },
            )
            _insert_policy_release(conn, tenant_id, [])
            conn.execute(sa.text("CREATE ROLE sapphire_worker LOGIN"))
            conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO sapphire_worker"))
            conn.execute(
                sa.text("GRANT SELECT, UPDATE ON observations TO sapphire_worker")
            )
            conn.execute(
                sa.text(
                    "CREATE ROLE sapphire_dhm21_delivery_writer LOGIN "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                    "NOREPLICATION NOBYPASSRLS"
                )
            )
            conn.execute(
                sa.text(
                    "GRANT USAGE ON SCHEMA public TO sapphire_dhm21_delivery_writer"
                )
            )
            conn.execute(
                sa.text(
                    "GRANT SELECT, UPDATE ON observations "
                    "TO sapphire_dhm21_delivery_writer"
                )
            )
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sa.text("SET SESSION AUTHORIZATION sapphire_worker"))
            conn.execute(sa.text("BEGIN"))
            with pytest.raises(DBAPIError, match="restricted DHM delivery"):
                conn.execute(
                    sa.text(
                        "UPDATE observations SET delivery_id = NULL WHERE id = :id"
                    ),
                    {"id": observation_id},
                )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("BEGIN"))
            with pytest.raises(DBAPIError, match="restricted DHM delivery"):
                conn.execute(
                    sa.text(
                        """
                        UPDATE observations
                        SET delivery_id = 'dhm-barkhk-2026-09-08'
                        WHERE id = :id
                        """
                    ),
                    {"id": ordinary_observation_id},
                )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
            conn.execute(
                sa.text("SET SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
            )
            conn.execute(sa.text("BEGIN"))
            with pytest.raises(DBAPIError, match="change only QC columns"):
                conn.execute(
                    sa.text("UPDATE observations SET value = 9.0 WHERE id = :id"),
                    {"id": observation_id},
                )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("BEGIN"))
            conn.execute(
                sa.text(
                    """
                    UPDATE observations
                    SET qc_status = 'qc_passed', qc_rule_version = 'policy',
                        qc_flags = '[]'::jsonb
                    WHERE id = :id
                    """
                ),
                {"id": observation_id},
            )
            conn.execute(sa.text("COMMIT"))
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT value, qc_status FROM observations WHERE id = :id"),
                {"id": observation_id},
            ).one()
            assert row == (1.0, "qc_passed")

    def test_published_generation_update_matrix_allows_only_expected_transitions(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            declaration = _decl("350", "a" * 64, {"case": "update-matrix"})
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
            )
            _publish_direct_sql(conn, generation_id)
            parent_identity_cases = [
                "delivery_id = 'dhm-nepal21-forged'",
                "manifest_sha256 = '9' || substring(manifest_sha256 from 2)",
                "station_set_sha256 = '8' || substring(station_set_sha256 from 2)",
                "predecessor_semantic_sha256 = '7' || substring(predecessor_semantic_sha256 from 2)",
                "preimage_sha256 = '6' || substring(preimage_sha256 from 2)",
                "immutable_content_sha256 = '5' || substring(immutable_content_sha256 from 2)",
                "network = 'forged'",
                "cohort = 'forged'",
            ]
            for assignment in parent_identity_cases:
                nested = conn.begin_nested()
                with pytest.raises(DBAPIError, match="identity is immutable"):
                    conn.execute(
                        sa.text(
                            f"""
                            UPDATE restricted_delivery_generations
                            SET {assignment}
                            WHERE id = :id
                            """
                        ),
                        {"id": generation_id},
                    )
                nested.rollback()
            nested = conn.begin_nested()
            with pytest.raises(DBAPIError, match="QC fields are transition-only"):
                conn.execute(
                    sa.text(
                        """
                        UPDATE restricted_delivery_generations
                        SET qc_policy_sha256 = :qc_policy
                        WHERE id = :id
                        """
                    ),
                    {"id": generation_id, "qc_policy": "5" * 64},
                )
            nested.rollback()
            nested = conn.begin_nested()
            with pytest.raises(
                DBAPIError, match="invalid restricted generation state transition"
            ):
                conn.execute(
                    sa.text(
                        """
                        UPDATE restricted_delivery_generations
                        SET state = 'building'
                        WHERE id = :id
                        """
                    ),
                    {"id": generation_id},
                )
            nested.rollback()

    def test_actual_writer_cannot_insert_active_generation_directly(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, []
            )
            _grant_writer_bootstrap_privileges(conn)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(
                sa.text("SET SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
            )
            conn.execute(sa.text("BEGIN"))
            with pytest.raises(
                DBAPIError, match="generation insert must start building"
            ):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO restricted_delivery_generations (
                            id, release_id, tenant_id, network, cohort, delivery_id,
                            predecessor_delivery_id, predecessor_semantic_sha256,
                            preimage_sha256, manifest_sha256, station_set_sha256,
                            state, immutable_content_sha256, created_at, published_at
                        ) VALUES (
                            :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                            'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                            :manifest, :station_set, 'active_pending_qc', :content,
                            :created_at, :published_at
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "release_id": release_id,
                        "tenant_id": tenant_id,
                        "delivery_id": delivery_id,
                        "predecessor": "2" * 64,
                        "preimage": "3" * 64,
                        "manifest": digest,
                        "station_set": "1" * 64,
                        "content": "4" * 64,
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                        "published_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("BEGIN"))
            with pytest.raises(
                Exception, match="generation insert must start building"
            ):
                driver_connection = cast("object", conn.connection.driver_connection)
                cursor = cast("Any", driver_connection.cursor())  # type: ignore[attr-defined]
                with cursor.copy(
                    "COPY public.restricted_delivery_generations ("
                    "id, release_id, tenant_id, network, cohort, delivery_id, "
                    "predecessor_delivery_id, predecessor_semantic_sha256, "
                    "preimage_sha256, manifest_sha256, station_set_sha256, state, "
                    "immutable_content_sha256, created_at, published_at) FROM STDIN"
                ) as copy:
                    copy.write_row(
                        (
                            uuid.uuid4(),
                            release_id,
                            tenant_id,
                            "dhm",
                            "nepal21",
                            delivery_id,
                            "dhm-barkhk-2026-09-08",
                            "2" * 64,
                            "3" * 64,
                            digest,
                            "1" * 64,
                            "active_pending_qc",
                            "4" * 64,
                            datetime(2026, 10, 12, tzinfo=UTC),
                            datetime(2026, 10, 12, tzinfo=UTC),
                        )
                    )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))

    def test_actual_writer_cannot_rewrite_release_authority_fields(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, _, _ = _insert_policy_release(conn, tenant_id, [])
            _grant_writer_bootstrap_privileges(conn)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(
                sa.text("SET SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
            )
            conn.execute(sa.text("BEGIN"))
            with pytest.raises(DBAPIError):
                conn.execute(
                    sa.text(
                        """
                        UPDATE restricted_delivery_releases
                        SET expected_predecessor_semantic_sha256 = :digest
                        WHERE id = :id
                        """
                    ),
                    {"id": release_id, "digest": "f" * 64},
                )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))


def _decl(
    station_code: str,
    digest: str,
    raw: dict[str, object],
    *,
    from_date: str = "1980-01-01",
    label: str = "1",
    shape: str = "decreasing_invalid",
) -> dict[str, object]:
    return {
        "station_code": station_code,
        "declaration_content_sha256": digest,
        "raw_declaration_payload_json": raw,
        "from_local_date": from_date,
        "rating_type_label": label,
        "shape_state": shape,
    }


def _raw_rating_payload(
    station_code: str,
    *,
    from_date: str = "1990-01-01",
    to_date: str = "1991-01-01",
    points: list[dict[str, float]] | None = None,
) -> dict[str, object]:
    return {
        "codec": "dhm21-canonical-json-v1",
        "station_code": station_code,
        "rating_type_label": "1",
        "from_local_date": from_date,
        "to_local_date_inclusive": to_date,
        "from_stage_m": 0.0,
        "points": points
        or [
            {"stage_m": 0.0, "discharge_m3s": 1.0},
            {"stage_m": 1.0, "discharge_m3s": 2.0},
        ],
    }


def _effective_payload(
    station_code: str,
    *,
    points: list[dict[str, float]] | None = None,
) -> dict[str, object]:
    return {
        "codec": "dhm21-canonical-json-v1",
        "station_code": station_code,
        "interpolation": "linear",
        "stage_unit": "m",
        "discharge_unit": "m3/s",
        "reference": "delivered_stage",
        "points": points
        or [
            {"stage_m": 0.0, "discharge_m3s": 1.0},
            {"stage_m": 1.0, "discharge_m3s": 2.0},
        ],
    }


def _native_payload(
    *,
    valid_from: str = "1990-01-01T00:00:00+00:00",
    valid_to: str = "1991-01-01T00:00:00+00:00",
    points: list[dict[str, float]] | None = None,
) -> dict[str, object]:
    return {
        "points": points
        or [
            {"water_level": 0.0, "discharge": 1.0},
            {"water_level": 1.0, "discharge": 2.0},
        ],
        "interpolation": "linear",
        "rating_type_label": "1",
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


def _canonicalization_policy_bytes(
    *,
    station_code: str,
    declaration_sha: str,
    approval_id: uuid.UUID,
    approval_sha: str,
    policy_scope: str = "nepal21",
) -> bytes:
    return canonical_json_bytes(
        {
            "codec": "dhm21-canonicalization-policy-v1",
            "policy_scope": policy_scope,
            "station_codes": [
                "225",
                "280",
                "350",
                "404.7",
                "439.7",
                "440",
                "445",
                "447",
                "450",
                "550.05",
                "602",
                "602.5",
                "604.5",
                "610",
                "620",
                "647",
                "650",
                "652",
                "670",
                "684",
                "695",
            ],
            "approvals": [
                {
                    "station_code": station_code,
                    "declaration_content_sha256": declaration_sha,
                    "approval_id": str(approval_id),
                    "approval_sha256": approval_sha,
                }
            ],
        }
    )


def _policy_manifest_bytes(
    declarations: list[dict[str, object]], *, policy_scope: str = "nepal21"
) -> tuple[bytes, str]:
    content = canonical_json_bytes(
        {
            "policy_scope": policy_scope,
            "station_codes": [
                "225",
                "280",
                "350",
                "404.7",
                "439.7",
                "440",
                "445",
                "447",
                "450",
                "550.05",
                "602",
                "602.5",
                "604.5",
                "610",
                "620",
                "647",
                "650",
                "652",
                "670",
                "684",
                "695",
            ],
            "rating_declarations": declarations,
        }
    )
    return content, hashlib.sha256(content).hexdigest()


def _insert_policy_release(
    conn: sa.Connection,
    tenant_id: uuid.UUID,
    declarations: list[dict[str, object]],
    *,
    policy_scope: str = "nepal21",
    canonicalization_policy: bytes | None = None,
) -> tuple[uuid.UUID, str, str]:
    release_id = uuid.uuid4()
    content, digest = _policy_manifest_bytes(declarations, policy_scope=policy_scope)
    canonicalization_policy_sha = (
        hashlib.sha256(canonicalization_policy).hexdigest()
        if canonicalization_policy is not None
        else None
    )
    delivery_id = f"dhm-nepal21-{digest[:16]}"
    conn.execute(
        sa.text(
            """
            INSERT INTO restricted_delivery_releases (
                id, tenant_id, network, cohort, manifest_sha256, station_set_sha256,
                canonicalization_policy_sha256,
                expected_predecessor_delivery_id, expected_predecessor_semantic_sha256,
                allowed_new_delivery_id, status, created_at
            ) VALUES (
                :id, :tenant_id, 'dhm', 'nepal21', :digest, :station_set,
                :canonicalization_policy_sha,
                'dhm-barkhk-2026-09-08', :predecessor,
                :delivery_id, 'authorized', :created_at
            )
            """
        ),
        {
            "id": release_id,
            "tenant_id": tenant_id,
            "digest": digest,
            "station_set": "1" * 64,
            "canonicalization_policy_sha": canonicalization_policy_sha,
            "predecessor": "2" * 64,
            "delivery_id": delivery_id,
            "created_at": datetime(2026, 10, 12, tzinfo=UTC),
        },
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO restricted_delivery_release_witnesses
            (release_id, witness_kind, content_sha256, content_bytes, created_at)
            VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
            """
        ),
        {
            "release_id": release_id,
            "digest": digest,
            "content": content,
            "created_at": datetime(2026, 10, 12, tzinfo=UTC),
        },
    )
    if canonicalization_policy is not None:
        conn.execute(
            sa.text(
                """
                INSERT INTO restricted_delivery_release_witnesses
                (release_id, witness_kind, content_sha256, content_bytes, created_at)
                VALUES (
                    :release_id, 'canonicalization_policy', :digest, :content,
                    :created_at
                )
                """
            ),
            {
                "release_id": release_id,
                "digest": canonicalization_policy_sha,
                "content": canonicalization_policy,
                "created_at": datetime(2026, 10, 12, tzinfo=UTC),
            },
        )
    return release_id, digest, delivery_id


def _insert_qc_policy_witness(conn: sa.Connection, release_id: uuid.UUID) -> str:
    content = canonical_json_bytes({"policy": "synthetic-nepal21-qc"})
    digest = sha256_hex_bytes(content)
    conn.execute(
        sa.text(
            """
            INSERT INTO restricted_delivery_release_witnesses
            (release_id, witness_kind, content_sha256, content_bytes, created_at)
            VALUES (:release_id, 'qc_policy', :digest, :content, :created_at)
            """
        ),
        {
            "release_id": release_id,
            "digest": digest,
            "content": content,
            "created_at": datetime(2026, 10, 12, tzinfo=UTC),
        },
    )
    return digest


def _insert_building_generation(
    conn: sa.Connection,
    *,
    tenant_id: uuid.UUID,
    release_id: uuid.UUID,
    manifest_sha: str,
    delivery_id: str,
    canonicalization_policy_sha256: str | None = None,
) -> uuid.UUID:
    generation_id = uuid.uuid4()
    conn.execute(
        sa.text(
            """
            INSERT INTO restricted_delivery_generations (
                id, release_id, tenant_id, network, cohort, delivery_id,
                predecessor_delivery_id, predecessor_semantic_sha256,
                preimage_sha256, manifest_sha256, station_set_sha256,
                canonicalization_policy_sha256, state, created_at
            ) VALUES (
                :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                :manifest, :station_set, :canonicalization_policy_sha,
                'building', :created_at
            )
            """
        ),
        {
            "id": generation_id,
            "release_id": release_id,
            "tenant_id": tenant_id,
            "delivery_id": delivery_id,
            "predecessor": "2" * 64,
            "preimage": "3" * 64,
            "manifest": manifest_sha,
            "station_set": "1" * 64,
            "canonicalization_policy_sha": canonicalization_policy_sha256,
            "created_at": datetime(2026, 10, 12, tzinfo=UTC),
        },
    )
    return generation_id


def _insert_origin_set(
    conn: sa.Connection,
    *,
    generation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    set_kind: str = "RT_DECLARATION",
    payload_sha: str = "8" * 64,
    origin_set_sha: str | None = None,
    member_count: int = 1,
) -> str:
    origin_id = uuid.uuid4()
    origin_kind = "DFL" if set_kind == "DFL_RECORD" else "RT"
    relative_path = "DFL_447.txt" if origin_kind == "DFL" else "RT_350.txt"
    resolved = origin_set_sha or origin_identity_set_sha256(
        [
            {
                "member_ordinal": 0,
                "origin_id": origin_id,
                "origin_kind": origin_kind,
                "source_root_label": "synthetic_test",
                "relative_path": relative_path,
                "file_sha256": "7" * 64,
                "parser_version": "test",
                "row_or_block_ordinal": 0,
                "origin_payload_sha256": payload_sha,
            }
        ],
        set_kind=set_kind,
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO dhm_delivery_origins (
                id, generation_id, tenant_id, cohort, origin_kind, source_root_label,
                relative_path, file_sha256, parser_version, row_or_block_ordinal,
                origin_payload_sha256
            ) VALUES (
                :id, :generation_id, :tenant_id, 'nepal21', :origin_kind,
                'synthetic_test', :relative_path, :file_sha, 'test', 0, :payload_sha
            )
            """
        ),
        {
            "id": origin_id,
            "generation_id": generation_id,
            "tenant_id": tenant_id,
            "origin_kind": origin_kind,
            "relative_path": relative_path,
            "file_sha": "7" * 64,
            "payload_sha": payload_sha,
        },
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO dhm_origin_sets (
                generation_id, tenant_id, origin_set_sha256, member_count, set_kind
            ) VALUES (:generation_id, :tenant_id, :origin_set, :member_count, :set_kind)
            """
        ),
        {
            "generation_id": generation_id,
            "tenant_id": tenant_id,
            "origin_set": resolved,
            "member_count": member_count,
            "set_kind": set_kind,
        },
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO dhm_origin_set_members (
                generation_id, tenant_id, origin_set_sha256, origin_id, origin_kind, member_ordinal
            ) VALUES (:generation_id, :tenant_id, :origin_set, :origin_id, :origin_kind, 0)
            """
        ),
        {
            "generation_id": generation_id,
            "tenant_id": tenant_id,
            "origin_set": resolved,
            "origin_id": origin_id,
            "origin_kind": origin_kind,
        },
    )
    return resolved


def _insert_origin_set_from_identities(
    conn: sa.Connection,
    *,
    generation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    set_kind: str,
    identities: Sequence[Mapping[str, object]],
    member_ordinals: list[int] | None = None,
    origin_set_sha: str | None = None,
) -> str:
    origin_set = origin_set_sha or origin_identity_set_sha256(
        identities, set_kind=set_kind
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO dhm_origin_sets (
                generation_id, tenant_id, origin_set_sha256, member_count, set_kind
            ) VALUES (:generation_id, :tenant_id, :origin_set, :member_count, :set_kind)
            """
        ),
        {
            "generation_id": generation_id,
            "tenant_id": tenant_id,
            "origin_set": origin_set,
            "member_count": len(identities),
            "set_kind": set_kind,
        },
    )
    for index, identity in enumerate(identities):
        conn.execute(
            sa.text(
                """
                INSERT INTO dhm_delivery_origins (
                    id, generation_id, tenant_id, cohort, origin_kind, source_root_label,
                    relative_path, file_sha256, parser_version, row_or_block_ordinal,
                    origin_payload_sha256
                ) VALUES (
                    :id, :generation_id, :tenant_id, 'nepal21', :origin_kind,
                    :source_root_label, :relative_path, :file_sha256,
                    :parser_version, :row_or_block_ordinal, :origin_payload_sha256
                )
                """
            ),
            {
                "id": identity["origin_id"],
                "generation_id": generation_id,
                "tenant_id": tenant_id,
                "origin_kind": identity["origin_kind"],
                "source_root_label": identity["source_root_label"],
                "relative_path": identity["relative_path"],
                "file_sha256": identity["file_sha256"],
                "parser_version": identity["parser_version"],
                "row_or_block_ordinal": identity["row_or_block_ordinal"],
                "origin_payload_sha256": identity["origin_payload_sha256"],
            },
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO dhm_origin_set_members (
                    generation_id, tenant_id, origin_set_sha256, origin_id,
                    origin_kind, member_ordinal
                ) VALUES (
                    :generation_id, :tenant_id, :origin_set, :origin_id,
                    :origin_kind, :member_ordinal
                )
                """
            ),
            {
                "generation_id": generation_id,
                "tenant_id": tenant_id,
                "origin_set": origin_set,
                "origin_id": identity["origin_id"],
                "origin_kind": identity["origin_kind"],
                "member_ordinal": member_ordinals[index]
                if member_ordinals is not None
                else identity["member_ordinal"],
            },
        )
    return origin_set


def _insert_declaration_row(
    conn: sa.Connection,
    *,
    generation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    station_id: uuid.UUID,
    origin_set: str,
    declaration: dict[str, object],
    raw_override: dict[str, object] | None = None,
) -> None:
    conn.execute(
        sa.text(
            """
            INSERT INTO dhm_rating_declarations (
                id, generation_id, tenant_id, station_id, cohort, station_code,
                from_local_date, to_local_date_inclusive, rating_type_label,
                from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                shape_state, origin_set_sha256
            ) VALUES (
                :id, :generation_id, :tenant_id, :station_id, 'nepal21', :station_code,
                :from_date, :to_date, :label, 0.0, CAST(:raw AS jsonb), :digest,
                :shape, :origin_set
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "generation_id": generation_id,
            "tenant_id": tenant_id,
            "station_id": station_id,
            "station_code": declaration["station_code"],
            "from_date": declaration["from_local_date"],
            "to_date": "2099-12-31",
            "label": declaration["rating_type_label"],
            "raw": json.dumps(
                raw_override or declaration["raw_declaration_payload_json"]
            ),
            "digest": declaration["declaration_content_sha256"],
            "shape": declaration["shape_state"],
            "origin_set": origin_set,
        },
    )


def _publish_direct_sql(conn: sa.Connection, generation_id: uuid.UUID) -> None:
    conn.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_dhm21_delivery_writer') THEN
                    CREATE ROLE sapphire_dhm21_delivery_writer SUPERUSER;
                END IF;
                EXECUTE 'GRANT sapphire_dhm21_delivery_writer TO ' || quote_ident(current_user);
            END $$
            """
        )
    )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_dhm21_delivery_writer"))
    assert (
        conn.scalar(sa.text("SELECT current_user")) == "sapphire_dhm21_delivery_writer"
    )
    conn.execute(
        sa.text(
            """
            UPDATE restricted_delivery_generations
            SET state='active_pending_qc', immutable_content_sha256=:content, published_at=:published
            WHERE id=:id
            """
        ),
        {
            "id": generation_id,
            "content": "4" * 64,
            "published": datetime(2026, 10, 12, tzinfo=UTC),
        },
    )


def _copy_provisional_discharge_row(
    conn: sa.Connection,
    discharge: Any,
    *,
    captured_at: datetime = NOW,
) -> None:
    driver_connection = cast("object", conn.connection.driver_connection)
    cursor = cast("Any", driver_connection.cursor())  # type: ignore[attr-defined]
    with cursor.copy(
        "COPY public.provisional_discharges ("
        "fingerprint, tenant_id, station_id, observation_id, "
        "rating_curve_id, feed_evidence_id, reference_proof_id, "
        "discharge, content, captured_at) FROM STDIN"
    ) as copy:
        values = raw_values(discharge)
        copy.write_row(
            (
                values["fingerprint"],
                values["tenant_id"],
                values["station_id"],
                values["observation_id"],
                values["rating_curve_id"],
                values["feed_evidence_id"],
                values["reference_proof_id"],
                values["discharge"],
                values["content"],
                captured_at,
            )
        )


def _grant_writer_bootstrap_privileges(conn: sa.Connection) -> None:
    conn.execute(
        sa.text(
            """
            DO $$
            DECLARE rel text; fn text;
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_dhm21_delivery_writer') THEN
                    CREATE ROLE sapphire_dhm21_delivery_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
                END IF;
                GRANT USAGE ON SCHEMA public TO sapphire_dhm21_delivery_writer;
                GRANT SELECT ON public.tenants TO sapphire_dhm21_delivery_writer;
                FOREACH rel IN ARRAY ARRAY['tenants', 'stations', 'restricted_delivery_releases',
                                          'restricted_delivery_generations', 'restricted_delivery_release_witnesses',
                                          'dhm_delivery_origins', 'dhm_origin_sets', 'dhm_origin_set_members',
                                          'dhm_rating_declarations', 'dhm_delivery_observation_attributions',
                                          'observations', 'rating_curves'] LOOP
                    IF to_regclass('public.' || rel) IS NOT NULL THEN
                        EXECUTE format('GRANT SELECT ON public.%I TO sapphire_dhm21_delivery_writer', rel);
                    END IF;
                END LOOP;
                FOREACH rel IN ARRAY ARRAY['restricted_delivery_generations', 'dhm_delivery_origins',
                                          'dhm_origin_sets', 'dhm_origin_set_members', 'dhm_rating_declarations',
                                          'dhm_delivery_observation_attributions'] LOOP
                    IF to_regclass('public.' || rel) IS NOT NULL THEN
                        EXECUTE format('GRANT INSERT ON public.%I TO sapphire_dhm21_delivery_writer', rel);
                    END IF;
                END LOOP;
                FOREACH rel IN ARRAY ARRAY['observations', 'rating_curves'] LOOP
                    IF to_regclass('public.' || rel) IS NOT NULL THEN
                        EXECUTE format('GRANT INSERT, UPDATE, DELETE ON public.%I TO sapphire_dhm21_delivery_writer', rel);
                    END IF;
                END LOOP;
                IF to_regclass('public.restricted_delivery_generations') IS NOT NULL THEN
                    EXECUTE 'GRANT UPDATE ON public.restricted_delivery_generations TO sapphire_dhm21_delivery_writer';
                END IF;
                FOREACH fn IN ARRAY ARRAY['public.sf_lock_dhm21_delivery_write(uuid,uuid)',
                                         'public.sf_lock_dhm21_conversion_append(uuid,uuid)',
                                         'public.sf_lock_dhm21_permission_change(uuid,uuid)',
                                         'public.sf_lock_provisional_permission_disable(uuid)',
                                         'public.sf_lock_nepal_static_guard(uuid)',
                                         'public.sf_active_accepted_delivery(uuid,text)',
                                         'public.sf_assert_restricted_publication_catalog(uuid)'] LOOP
                    IF to_regprocedure(fn) IS NOT NULL THEN
                        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO sapphire_dhm21_delivery_writer', fn);
                    END IF;
                END LOOP;
            END $$
            """
        )
    )


def _prepare_owner_release(
    engine: sa.Engine,
    declarations: list[dict[str, object]],
    *,
    policy_scope: str = "nepal21",
) -> tuple[uuid.UUID, uuid.UUID, str, str, uuid.UUID]:
    with engine.begin() as conn:
        tenant_id = _tenant(conn)
        station_id = _station(conn, tenant_id)
        release_id, digest, delivery_id = _insert_policy_release(
            conn, tenant_id, declarations, policy_scope=policy_scope
        )
        _grant_writer_bootstrap_privileges(conn)
    return tenant_id, release_id, digest, delivery_id, station_id


def _run_publication_as_writer(
    engine: sa.Engine,
    *,
    tenant_id: uuid.UUID,
    release_id: uuid.UUID,
    manifest_sha: str,
    delivery_id: str,
    station_id: uuid.UUID,
    declarations: list[dict[str, object]],
    persisted_indexes: list[int] | None = None,
    raw_override: dict[str, object] | None = None,
    origin_set_override: str | None = None,
    member_count: int = 1,
) -> uuid.UUID:
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    generation_id = uuid.uuid4()
    try:
        conn.execute(
            sa.text("SET SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
        )
        identity = conn.execute(sa.text("SELECT session_user, current_user")).one()
        assert identity == (
            "sapphire_dhm21_delivery_writer",
            "sapphire_dhm21_delivery_writer",
        )
        conn.execute(sa.text("BEGIN"))
        try:
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=manifest_sha,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                origin_set_sha=origin_set_override,
                member_count=member_count,
            )
            selected_indexes = (
                persisted_indexes
                if persisted_indexes is not None
                else list(range(len(declarations)))
            )
            for index in selected_indexes:
                _insert_declaration_row(
                    conn,
                    generation_id=generation_id,
                    tenant_id=tenant_id,
                    station_id=station_id,
                    origin_set=origin_set,
                    declaration=declarations[index],
                    raw_override=raw_override,
                )
            conn.execute(
                sa.text(
                    """
                    UPDATE restricted_delivery_generations
                    SET state='active_pending_qc', immutable_content_sha256=:content, published_at=:published
                    WHERE id=:id
                    """
                ),
                {
                    "id": generation_id,
                    "content": "4" * 64,
                    "published": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            conn.execute(sa.text("COMMIT"))
        except BaseException:
            conn.execute(sa.text("ROLLBACK"))
            raise
        return generation_id
    finally:
        conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
        conn.close()


class TestNepal21WitnessCatalogPublicationAuthority:
    def test_direct_sql_catalog_refuses_nonfinite_numeric_fields(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                tenant_id = _tenant(conn)
                station_id = _station(conn, tenant_id)
                release_id, digest, delivery_id = _insert_policy_release(
                    conn, tenant_id, []
                )
                generation_id = _insert_building_generation(
                    conn,
                    tenant_id=tenant_id,
                    release_id=release_id,
                    manifest_sha=digest,
                    delivery_id=delivery_id,
                )
                origin_set = _insert_origin_set(
                    conn,
                    generation_id=generation_id,
                    tenant_id=tenant_id,
                    set_kind="DFL_RECORD",
                )
                nonfinite_declaration = conn.begin_nested()
                with pytest.raises(DBAPIError, match="violates check constraint"):
                    conn.execute(
                        sa.text(
                            """
                            INSERT INTO dhm_rating_declarations (
                                id, generation_id, tenant_id, station_id, cohort, station_code,
                                from_local_date, to_local_date_inclusive, rating_type_label,
                                from_stage_m, raw_declaration_payload_json,
                                declaration_content_sha256, shape_state, origin_set_sha256
                            ) VALUES (
                                :id, :generation_id, :tenant_id, :station_id, 'nepal21', '350',
                                '1991-01-01', '1991-01-02', '1', 'Infinity'::float8,
                                '{"case":"nonfinite-stage"}'::jsonb, :digest,
                                'raw_usable', :origin_set
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "generation_id": generation_id,
                            "tenant_id": tenant_id,
                            "station_id": station_id,
                            "digest": "7" * 64,
                            "origin_set": origin_set,
                        },
                    )
                nonfinite_declaration.rollback()
                observation_id = uuid.uuid4()
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO observations (
                            id, station_id, timestamp, parameter, value, source,
                            delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                        ) VALUES (
                            :id, :station_id, :timestamp, 'discharge', 1.0,
                            'manual_import', :delivery_id, 'raw', '[]'::jsonb,
                            NULL, :created_at
                        )
                        """
                    ),
                    {
                        "id": observation_id,
                        "station_id": station_id,
                        "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                        "delivery_id": delivery_id,
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
                nonfinite_attribution = conn.begin_nested()
                with pytest.raises(DBAPIError, match="violates check constraint"):
                    conn.execute(
                        sa.text(
                            """
                            INSERT INTO dhm_delivery_observation_attributions (
                                generation_id, tenant_id, observation_id, station_id, cohort,
                                station_code, origin_set_sha256, attribution_state,
                                local_date, reported_q
                            ) VALUES (
                                :generation_id, :tenant_id, :observation_id, :station_id,
                                'nepal21', '350', :origin_set, 'not_applicable',
                                '1991-01-02', 'Infinity'::float8
                            )
                            """
                        ),
                        {
                            "generation_id": generation_id,
                            "tenant_id": tenant_id,
                            "observation_id": observation_id,
                            "station_id": station_id,
                            "origin_set": origin_set,
                        },
                    )
                nonfinite_attribution.rollback()
            finally:
                trans.rollback()

    @pytest.mark.parametrize(
        ("label", "manifest_declarations", "persisted_indexes"),
        [
            (
                "omitted newest invalid",
                [
                    _decl("350", "a" * 64, {"case": "older"}, from_date="1980-01-01"),
                    _decl(
                        "350",
                        "b" * 64,
                        {"case": "newest_invalid"},
                        from_date="2020-01-01",
                    ),
                ],
                [0],
            ),
            (
                "omitted earlier invalid winner",
                [
                    _decl(
                        "602.5",
                        "c" * 64,
                        {"case": "earlier_invalid"},
                        from_date="1980-01-01",
                    ),
                    _decl("602.5", "d" * 64, {"case": "later"}, from_date="2020-01-01"),
                ],
                [1],
            ),
            (
                "same-start tie omission",
                [
                    _decl(
                        "404.7",
                        "e" * 64,
                        {"case": "tie_a"},
                        from_date="2000-01-01",
                        label="2",
                    ),
                    _decl(
                        "404.7",
                        "f" * 64,
                        {"case": "tie_b"},
                        from_date="2000-01-01",
                        label="3",
                    ),
                ],
                [0],
            ),
        ],
    )
    def test_direct_sql_publication_refuses_manifest_declaration_omissions(
        self,
        migration_engine: tuple[sa.Engine, str],
        label: str,
        manifest_declarations: list[dict[str, object]],
        persisted_indexes: list[int],
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with (
            pytest.raises(DBAPIError, match="manifest declaration set mismatch"),
            engine.begin() as conn,
        ):
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, manifest_declarations
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            for index in persisted_indexes:
                _insert_declaration_row(
                    conn,
                    generation_id=generation_id,
                    tenant_id=tenant_id,
                    station_id=station_id,
                    origin_set=origin_set,
                    declaration=manifest_declarations[index],
                )
            assert label
            _publish_direct_sql(conn, generation_id)

    def test_direct_sql_publication_refuses_same_count_raw_substitution(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl("350", "a" * 64, {"case": "expected"})
        with (
            pytest.raises(DBAPIError, match="manifest declaration set mismatch"),
            engine.begin() as conn,
        ):
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
                raw_override={"case": "substituted"},
            )
            _publish_direct_sql(conn, generation_id)

    def test_direct_sql_publication_refuses_origin_member_mismatch(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl("350", "a" * 64, {"case": "origin"})
        with (
            pytest.raises(DBAPIError, match="origin set membership mismatch"),
            engine.begin() as conn,
        ):
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                origin_set_sha="9" * 64,
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
            )
            _publish_direct_sql(conn, generation_id)

    def test_direct_sql_publication_refuses_unauthorized_policy_scope(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl("350", "a" * 64, {"case": "scope"})
        with (
            pytest.raises(DBAPIError, match="manifest policy scope"),
            engine.begin() as conn,
        ):
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration], policy_scope="foreign"
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
            )
            _publish_direct_sql(conn, generation_id)

    def test_witness_hash_refuses_copied_hash_with_changed_bytes(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, content, digest = _release(conn, tenant_id)
            assert content
            with pytest.raises(DBAPIError, match="bytes do not match"):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO restricted_delivery_release_witnesses
                        (release_id, witness_kind, content_sha256, content_bytes, created_at)
                        VALUES (:release_id, 'manifest_catalog', :digest, :content, :created_at)
                        """
                    ),
                    {
                        "release_id": release_id,
                        "digest": digest,
                        "content": b'{"changed":true}',
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )

    def test_direct_sql_publication_accepts_complete_manifest_catalog(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl("350", "a" * 64, {"case": "complete"})
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
            )
            _publish_direct_sql(conn, generation_id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )

    def test_publication_accepts_structural_raw_effective_native_with_separate_rt_dfl_origins(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        raw_payload = _raw_rating_payload("447")
        function = _effective_payload("447")
        native = _native_payload(
            valid_from="1989-12-31T18:15:00+00:00",
            valid_to="1990-12-31T18:15:00+00:00",
        )
        declaration = _decl(
            "447",
            sha256_hex_bytes(canonical_json_bytes(raw_payload)),
            raw_payload,
            from_date="1990-01-01",
            shape="raw_usable",
        )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            curve_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO rating_curves (
                        id, station_id, version, valid_from, valid_to, points,
                        interpolation, delivery_id, rating_type_label, created_at
                    ) VALUES (
                        :id, :station_id, 1, :valid_from, :valid_to,
                        CAST(:points AS jsonb), 'linear', :delivery_id, '1', :created_at
                    )
                    """
                ),
                {
                    "id": curve_id,
                    "station_id": station_id,
                    "valid_from": datetime(1989, 12, 31, 18, 15, tzinfo=UTC),
                    "valid_to": datetime(1990, 12, 31, 18, 15, tzinfo=UTC),
                    "points": json.dumps(native["points"]),
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            rt_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="RT_DECLARATION",
                payload_sha="8" * 64,
            )
            declaration_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_rating_declarations (
                        id, generation_id, tenant_id, station_id, cohort, station_code,
                        from_local_date, to_local_date_inclusive, rating_type_label,
                        from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                        function_payload_json, function_equivalence_sha256,
                        native_curve_payload_json, shape_state, usable_rating_curve_id,
                        origin_set_sha256
                    ) VALUES (
                        :id, :generation_id, :tenant_id, :station_id, 'nepal21', '447',
                        '1990-01-01', '1991-01-01', '1', 0.0,
                        CAST(:raw AS jsonb), :decl_sha, CAST(:function AS jsonb),
                        :function_sha, CAST(:native AS jsonb), 'raw_usable',
                        :curve_id, :origin_set
                    )
                    """
                ),
                {
                    "id": declaration_id,
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "station_id": station_id,
                    "raw": json.dumps(raw_payload),
                    "decl_sha": declaration["declaration_content_sha256"],
                    "function": json.dumps(function),
                    "function_sha": sha256_hex_bytes(canonical_json_bytes(function)),
                    "native": json.dumps(native),
                    "curve_id": curve_id,
                    "origin_set": rt_origin_set,
                },
            )
            observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        rating_curve_id, rating_curve_correction_version,
                        delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 2.0,
                        'manual_import', :curve_id, '1', :delivery_id,
                        'raw', '[]'::jsonb, NULL, :created_at
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(1990, 1, 2, tzinfo=UTC),
                    "curve_id": curve_id,
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            dfl_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="DFL_RECORD",
                payload_sha="6" * 64,
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_delivery_observation_attributions (
                        generation_id, tenant_id, observation_id, station_id, cohort,
                        station_code, origin_set_sha256, rating_declaration_id,
                        rating_curve_id, attribution_state, local_date, reported_q
                    ) VALUES (
                        :generation_id, :tenant_id, :observation_id, :station_id,
                        'nepal21', '447', :origin_set, :declaration_id,
                        :curve_id, 'selected', :local_date, 2.0
                    )
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": observation_id,
                    "station_id": station_id,
                    "origin_set": dfl_origin_set,
                    "declaration_id": declaration_id,
                    "curve_id": curve_id,
                    "local_date": date(1990, 1, 2),
                },
            )
            _publish_direct_sql(conn, generation_id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )

    def test_publication_accepts_canonicalized_duplicate_removal_with_policy_witness(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        raw_payload = _raw_rating_payload(
            "447",
            points=[
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 1.0, "discharge_m3s": 2.0},
            ],
        )
        function = _effective_payload("447")
        native = _native_payload(
            valid_from="1989-12-31T18:15:00+00:00",
            valid_to="1990-12-31T18:15:00+00:00",
        )
        declaration_sha = sha256_hex_bytes(canonical_json_bytes(raw_payload))
        approval_id = uuid.uuid4()
        approval_sha = "d" * 64
        policy = _canonicalization_policy_bytes(
            station_code="447",
            declaration_sha=declaration_sha,
            approval_id=approval_id,
            approval_sha=approval_sha,
        )
        declaration = _decl(
            "447",
            declaration_sha,
            raw_payload,
            from_date="1990-01-01",
            shape="canonicalized_usable",
        )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn,
                tenant_id,
                [declaration],
                canonicalization_policy=policy,
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
                canonicalization_policy_sha256=hashlib.sha256(policy).hexdigest(),
            )
            curve_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO rating_curves (
                        id, station_id, version, valid_from, valid_to, points,
                        interpolation, delivery_id, rating_type_label, created_at
                    ) VALUES (
                        :id, :station_id, 1, :valid_from, :valid_to,
                        CAST(:points AS jsonb), 'linear', :delivery_id, '1', :created_at
                    )
                    """
                ),
                {
                    "id": curve_id,
                    "station_id": station_id,
                    "valid_from": datetime(1989, 12, 31, 18, 15, tzinfo=UTC),
                    "valid_to": datetime(1990, 12, 31, 18, 15, tzinfo=UTC),
                    "points": json.dumps(native["points"]),
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            rt_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="RT_DECLARATION",
                payload_sha="8" * 64,
            )
            declaration_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_rating_declarations (
                        id, generation_id, tenant_id, station_id, cohort, station_code,
                        from_local_date, to_local_date_inclusive, rating_type_label,
                        from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                        function_payload_json, function_equivalence_sha256,
                        native_curve_payload_json, shape_state, usable_rating_curve_id,
                        canonicalization_approval_id, canonicalization_approval_sha256,
                        origin_set_sha256
                    ) VALUES (
                        :id, :generation_id, :tenant_id, :station_id, 'nepal21', '447',
                        '1990-01-01', '1991-01-01', '1', 0.0,
                        CAST(:raw AS jsonb), :decl_sha, CAST(:function AS jsonb),
                        :function_sha, CAST(:native AS jsonb), 'canonicalized_usable',
                        :curve_id, :approval_id, :approval_sha, :origin_set
                    )
                    """
                ),
                {
                    "id": declaration_id,
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "station_id": station_id,
                    "raw": json.dumps(raw_payload),
                    "decl_sha": declaration_sha,
                    "function": json.dumps(function),
                    "function_sha": sha256_hex_bytes(canonical_json_bytes(function)),
                    "native": json.dumps(native),
                    "curve_id": curve_id,
                    "approval_id": approval_id,
                    "approval_sha": approval_sha,
                    "origin_set": rt_origin_set,
                },
            )
            observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        rating_curve_id, rating_curve_correction_version,
                        delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 2.0,
                        'manual_import', :curve_id, '1', :delivery_id,
                        'raw', '[]'::jsonb, NULL, :created_at
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(1990, 1, 2, tzinfo=UTC),
                    "curve_id": curve_id,
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            dfl_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="DFL_RECORD",
                payload_sha="6" * 64,
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_delivery_observation_attributions (
                        generation_id, tenant_id, observation_id, station_id, cohort,
                        station_code, origin_set_sha256, rating_declaration_id,
                        rating_curve_id, attribution_state, local_date, reported_q
                    ) VALUES (
                        :generation_id, :tenant_id, :observation_id, :station_id,
                        'nepal21', '447', :origin_set, :declaration_id,
                        :curve_id, 'selected', :local_date, 2.0
                    )
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": observation_id,
                    "station_id": station_id,
                    "origin_set": dfl_origin_set,
                    "declaration_id": declaration_id,
                    "curve_id": curve_id,
                    "local_date": date(1990, 1, 2),
                },
            )
            _publish_direct_sql(conn, generation_id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )

    def test_publication_refuses_forged_raw_effective_shape_derivation(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        raw_payload = _raw_rating_payload(
            "350",
            points=[
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 1.0, "discharge_m3s": 2.0},
            ],
        )
        declaration = _decl(
            "350",
            sha256_hex_bytes(canonical_json_bytes(raw_payload)),
            raw_payload,
            from_date="1990-01-01",
            shape="raw_usable",
        )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_rating_declarations (
                        id, generation_id, tenant_id, station_id, cohort, station_code,
                        from_local_date, to_local_date_inclusive, rating_type_label,
                        from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                        function_payload_json, function_equivalence_sha256,
                        shape_state, origin_set_sha256
                    ) VALUES (
                        :id, :generation_id, :tenant_id, :station_id, 'nepal21', '350',
                        '1990-01-01', '1991-01-01', '1', 0.0,
                        CAST(:raw AS jsonb), :decl_sha, CAST(:function AS jsonb),
                        :function_sha, 'raw_usable', :origin_set
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "station_id": station_id,
                    "raw": json.dumps(raw_payload),
                    "decl_sha": declaration["declaration_content_sha256"],
                    "function": json.dumps(_effective_payload("350")),
                    "function_sha": "b" * 64,
                    "origin_set": origin_set,
                },
            )
            with pytest.raises(
                DBAPIError, match="raw effective declaration derivation mismatch"
            ):
                _publish_direct_sql(conn, generation_id)

    def test_publication_refuses_canonicalized_declaration_without_policy_witness(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        raw_payload = _raw_rating_payload(
            "350",
            from_date="1991-01-01",
            to_date="1992-01-01",
            points=[
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 1.0, "discharge_m3s": 2.0},
                {"stage_m": 1.0, "discharge_m3s": 2.0},
            ],
        )
        declaration = _decl(
            "350",
            sha256_hex_bytes(canonical_json_bytes(raw_payload)),
            raw_payload,
            from_date="1991-01-01",
            shape="canonicalized_usable",
        )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_rating_declarations (
                        id, generation_id, tenant_id, station_id, cohort, station_code,
                        from_local_date, to_local_date_inclusive, rating_type_label,
                        from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                        function_payload_json, function_equivalence_sha256,
                        shape_state, canonicalization_approval_id,
                        canonicalization_approval_sha256, origin_set_sha256
                    ) VALUES (
                        :id, :generation_id, :tenant_id, :station_id, 'nepal21', '350',
                        '1991-01-01', '1992-01-01', '1', 0.0,
                        CAST(:raw AS jsonb), :decl_sha,
                        CAST(:function AS jsonb), :function_sha,
                        'canonicalized_usable', :approval_id, :approval_sha,
                        :origin_set
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "station_id": station_id,
                    "raw": json.dumps(raw_payload),
                    "decl_sha": declaration["declaration_content_sha256"],
                    "function": json.dumps(_effective_payload("350")),
                    "function_sha": sha256_hex_bytes(
                        canonical_json_bytes(_effective_payload("350"))
                    ),
                    "approval_id": uuid.uuid4(),
                    "approval_sha": "c" * 64,
                    "origin_set": origin_set,
                },
            )
            with pytest.raises(
                DBAPIError, match="canonicalization policy witness is missing"
            ):
                _publish_direct_sql(conn, generation_id)

    def test_publication_refuses_canonicalization_policy_wrong_approval_scope(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        raw_payload = _raw_rating_payload(
            "350",
            points=[
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 1.0, "discharge_m3s": 2.0},
            ],
        )
        declaration_sha = sha256_hex_bytes(canonical_json_bytes(raw_payload))
        approval_id = uuid.uuid4()
        approval_sha = "c" * 64
        wrong_policy = _canonicalization_policy_bytes(
            station_code="350",
            declaration_sha=declaration_sha,
            approval_id=approval_id,
            approval_sha="d" * 64,
        )
        declaration = _decl(
            "350",
            declaration_sha,
            raw_payload,
            from_date="1990-01-01",
            shape="canonicalized_usable",
        )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn,
                tenant_id,
                [declaration],
                canonicalization_policy=wrong_policy,
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
                canonicalization_policy_sha256=hashlib.sha256(wrong_policy).hexdigest(),
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_rating_declarations (
                        id, generation_id, tenant_id, station_id, cohort, station_code,
                        from_local_date, to_local_date_inclusive, rating_type_label,
                        from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                        function_payload_json, function_equivalence_sha256,
                        shape_state, canonicalization_approval_id,
                        canonicalization_approval_sha256, origin_set_sha256
                    ) VALUES (
                        :id, :generation_id, :tenant_id, :station_id, 'nepal21', '350',
                        '1990-01-01', '1991-01-01', '1', 0.0,
                        CAST(:raw AS jsonb), :decl_sha, CAST(:function AS jsonb),
                        :function_sha, 'canonicalized_usable', :approval_id,
                        :approval_sha, :origin_set
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "station_id": station_id,
                    "raw": json.dumps(raw_payload),
                    "decl_sha": declaration_sha,
                    "function": json.dumps(_effective_payload("350")),
                    "function_sha": "b" * 64,
                    "approval_id": approval_id,
                    "approval_sha": approval_sha,
                    "origin_set": origin_set,
                },
            )
            with pytest.raises(
                DBAPIError, match="canonicalization policy witness scope mismatch"
            ):
                _publish_direct_sql(conn, generation_id)

    def test_publication_refuses_native_payload_that_differs_from_actual_curve(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        raw_payload = _raw_rating_payload("350")
        declaration = _decl(
            "350",
            sha256_hex_bytes(canonical_json_bytes(raw_payload)),
            raw_payload,
            from_date="1990-01-01",
            shape="raw_usable",
        )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            curve_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO rating_curves (
                        id, station_id, version, valid_from, valid_to, points,
                        interpolation, delivery_id, rating_type_label, created_at
                    ) VALUES (
                        :id, :station_id, 1, :valid_from, :valid_to,
                        '[{"water_level": 0.0, "discharge": 1.0}]'::jsonb,
                        'linear', :delivery_id, '1', :created_at
                    )
                    """
                ),
                {
                    "id": curve_id,
                    "station_id": station_id,
                    "valid_from": datetime(1990, 1, 1, tzinfo=UTC),
                    "valid_to": datetime(1991, 1, 1, tzinfo=UTC),
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_rating_declarations (
                        id, generation_id, tenant_id, station_id, cohort, station_code,
                        from_local_date, to_local_date_inclusive, rating_type_label,
                        from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                        function_payload_json, function_equivalence_sha256,
                        native_curve_payload_json, shape_state, usable_rating_curve_id,
                        origin_set_sha256
                    ) VALUES (
                        :id, :generation_id, :tenant_id, :station_id, 'nepal21', '350',
                        '1990-01-01', '1991-01-01', '1', 0.0,
                        CAST(:raw AS jsonb), :decl_sha,
                        CAST(:function_payload AS jsonb), :function_sha,
                        CAST(:native_payload AS jsonb),
                        'raw_usable', :curve_id, :origin_set
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "station_id": station_id,
                    "raw": json.dumps(raw_payload),
                    "decl_sha": declaration["declaration_content_sha256"],
                    "function_payload": json.dumps(_effective_payload("350")),
                    "function_sha": sha256_hex_bytes(
                        canonical_json_bytes(_effective_payload("350"))
                    ),
                    "native_payload": json.dumps(
                        _native_payload(
                            points=[{"water_level": 0.0, "discharge": 99.0}]
                        )
                    ),
                    "curve_id": curve_id,
                    "origin_set": origin_set,
                },
            )
            with pytest.raises(
                DBAPIError,
                match="native curve payload does not match actual rating curve",
            ):
                _publish_direct_sql(conn, generation_id)

    def test_all_catalog_child_tables_have_building_insert_guard(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        expected = {
            "dhm_delivery_origins": "trg_dhm_origins_insert_guard",
            "dhm_origin_sets": "trg_dhm_origin_sets_insert_guard",
            "dhm_origin_set_members": "trg_dhm_origin_members_insert_guard",
            "dhm_rating_declarations": "trg_dhm_declarations_insert_guard",
            "dhm_delivery_observation_attributions": "trg_dhm_attributions_insert_guard",
        }
        with engine.connect() as conn:
            rows = {
                str(row[0]): str(row[1])
                for row in conn.execute(
                    sa.text(
                        """
                        SELECT c.relname, t.tgname
                        FROM pg_catalog.pg_trigger t
                        JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
                        WHERE c.relname = ANY(:tables)
                          AND NOT t.tgisinternal
                          AND t.tgfoid = 'public.sf_restricted_generation_child_insert_guard()'::regprocedure
                        """
                    ),
                    {"tables": list(expected)},
                )
            }
        assert rows == expected

    def test_postpublication_catalog_child_insert_is_refused(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl("350", "a" * 64, {"case": "complete"})
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
            )
            _publish_direct_sql(conn, generation_id)
            with pytest.raises(
                DBAPIError,
                match="catalog rows may only be inserted while generation is building",
            ):
                _insert_origin_set(
                    conn,
                    generation_id=generation_id,
                    tenant_id=tenant_id,
                    set_kind="DFL_RECORD",
                    payload_sha="9" * 64,
                )

    def test_postpublication_catalog_child_copy_is_refused(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl("350", "a" * 64, {"case": "copy-guard"})
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            origin_set = _insert_origin_set(
                conn, generation_id=generation_id, tenant_id=tenant_id
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
            )
            _publish_direct_sql(conn, generation_id)
            copy_cases: list[tuple[str, tuple[object, ...]]] = [
                (
                    "COPY public.dhm_delivery_origins ("
                    "id, generation_id, tenant_id, cohort, origin_kind, "
                    "source_root_label, relative_path, file_sha256, parser_version, "
                    "row_or_block_ordinal, origin_payload_sha256) FROM STDIN",
                    (
                        uuid.uuid4(),
                        generation_id,
                        tenant_id,
                        "nepal21",
                        "DFL",
                        "synthetic_test",
                        "DFL_350_after_publish.txt",
                        "9" * 64,
                        "test",
                        0,
                        "8" * 64,
                    ),
                ),
                (
                    "COPY public.dhm_origin_sets ("
                    "generation_id, tenant_id, origin_set_sha256, member_count, set_kind"
                    ") FROM STDIN",
                    (generation_id, tenant_id, "9" * 64, 1, "DFL_RECORD"),
                ),
                (
                    "COPY public.dhm_origin_set_members ("
                    "generation_id, tenant_id, origin_set_sha256, origin_id, "
                    "origin_kind, member_ordinal) FROM STDIN",
                    (generation_id, tenant_id, origin_set, uuid.uuid4(), "DFL", 99),
                ),
                (
                    "COPY public.dhm_rating_declarations ("
                    "id, generation_id, tenant_id, station_id, cohort, station_code, "
                    "from_local_date, to_local_date_inclusive, rating_type_label, "
                    "from_stage_m, raw_declaration_payload_json, "
                    "declaration_content_sha256, shape_state, origin_set_sha256"
                    ") FROM STDIN",
                    (
                        uuid.uuid4(),
                        generation_id,
                        tenant_id,
                        station_id,
                        "nepal21",
                        "350",
                        date(1991, 1, 1),
                        date(1991, 1, 2),
                        "usable",
                        0.0,
                        json.dumps({"case": "copy-after-publish"}),
                        "7" * 64,
                        "raw_usable",
                        origin_set,
                    ),
                ),
                (
                    "COPY public.dhm_delivery_observation_attributions ("
                    "generation_id, tenant_id, observation_id, station_id, cohort, "
                    "station_code, origin_set_sha256, attribution_state, local_date, "
                    "reported_q) FROM STDIN",
                    (
                        generation_id,
                        tenant_id,
                        uuid.uuid4(),
                        station_id,
                        "nepal21",
                        "350",
                        origin_set,
                        "not_applicable",
                        date(1991, 1, 2),
                        1.0,
                    ),
                ),
            ]
            driver_connection = cast("object", conn.connection.driver_connection)
            cursor = cast("Any", driver_connection.cursor())  # type: ignore[attr-defined]
            for copy_sql, row in copy_cases:
                nested = conn.begin_nested()
                with (
                    pytest.raises(
                        Exception,
                        match="catalog rows may only be inserted while generation is building",
                    ),
                    cursor.copy(copy_sql) as copy,
                ):
                    copy.write_row(row)
                nested.rollback()

    def test_publication_refuses_selected_old_curve_when_latest_declaration_is_invalid(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        old_raw = _raw_rating_payload(
            "447", from_date="1989-01-01", to_date="2099-12-31"
        )
        old_function = _effective_payload("447")
        old_native = _native_payload(
            valid_from="1988-12-31T18:15:00+00:00",
            valid_to="2099-12-30T18:15:00+00:00",
        )
        invalid_raw = _raw_rating_payload(
            "447",
            points=[
                {"stage_m": 0.0, "discharge_m3s": 2.0},
                {"stage_m": 1.0, "discharge_m3s": 1.0},
            ],
        )
        old_declaration = _decl(
            "447",
            sha256_hex_bytes(canonical_json_bytes(old_raw)),
            old_raw,
            from_date="1989-01-01",
            shape="raw_usable",
        )
        invalid_declaration = _decl(
            "447",
            sha256_hex_bytes(canonical_json_bytes(invalid_raw)),
            invalid_raw,
            from_date="1990-01-01",
            shape="decreasing_invalid",
        )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [old_declaration, invalid_declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            curve_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO rating_curves (
                        id, station_id, version, valid_from, valid_to, points,
                        interpolation, delivery_id, rating_type_label, created_at
                    ) VALUES (
                        :id, :station_id, 1, :valid_from, :valid_to,
                        CAST(:points AS jsonb), 'linear', :delivery_id, '1', :created_at
                    )
                    """
                ),
                {
                    "id": curve_id,
                    "station_id": station_id,
                    "valid_from": datetime(1988, 12, 31, 18, 15, tzinfo=UTC),
                    "valid_to": datetime(2099, 12, 30, 18, 15, tzinfo=UTC),
                    "points": json.dumps(old_native["points"]),
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            rt_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="RT_DECLARATION",
                payload_sha="8" * 64,
            )
            old_declaration_id = uuid.uuid4()
            for (
                declaration_id,
                raw_payload,
                function_payload,
                native_payload,
                declaration,
            ) in (
                (
                    old_declaration_id,
                    old_raw,
                    old_function,
                    old_native,
                    old_declaration,
                ),
                (uuid.uuid4(), invalid_raw, None, None, invalid_declaration),
            ):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO dhm_rating_declarations (
                            id, generation_id, tenant_id, station_id, cohort, station_code,
                            from_local_date, to_local_date_inclusive, rating_type_label,
                            from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                            function_payload_json, function_equivalence_sha256,
                            native_curve_payload_json, shape_state, usable_rating_curve_id,
                            origin_set_sha256
                        ) VALUES (
                            :id, :generation_id, :tenant_id, :station_id, 'nepal21', '447',
                            :from_date, :to_date, '1', 0.0,
                            CAST(:raw AS jsonb), :decl_sha, CAST(:function AS jsonb),
                            :function_sha, CAST(:native AS jsonb), :shape, :curve_id,
                            :origin_set
                        )
                        """
                    ),
                    {
                        "id": declaration_id,
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "station_id": station_id,
                        "from_date": raw_payload["from_local_date"],
                        "to_date": raw_payload["to_local_date_inclusive"],
                        "raw": json.dumps(raw_payload),
                        "decl_sha": declaration["declaration_content_sha256"],
                        "function": json.dumps(function_payload)
                        if function_payload is not None
                        else None,
                        "function_sha": sha256_hex_bytes(
                            canonical_json_bytes(function_payload)
                        )
                        if function_payload is not None
                        else None,
                        "native": json.dumps(native_payload)
                        if native_payload is not None
                        else None,
                        "shape": declaration["shape_state"],
                        "curve_id": curve_id if native_payload is not None else None,
                        "origin_set": rt_origin_set,
                    },
                )
            observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        rating_curve_id, rating_curve_correction_version,
                        delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 2.0,
                        'manual_import', :curve_id, '1', :delivery_id,
                        'raw', '[]'::jsonb, NULL, :created_at
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(1990, 1, 2, tzinfo=UTC),
                    "curve_id": curve_id,
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            dfl_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="DFL_RECORD",
                payload_sha="6" * 64,
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_delivery_observation_attributions (
                        generation_id, tenant_id, observation_id, station_id, cohort,
                        station_code, origin_set_sha256, rating_declaration_id,
                        rating_curve_id, attribution_state, local_date, reported_q
                    ) VALUES (
                        :generation_id, :tenant_id, :observation_id, :station_id,
                        'nepal21', '447', :origin_set, :declaration_id,
                        :curve_id, 'selected', :local_date, 2.0
                    )
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": observation_id,
                    "station_id": station_id,
                    "origin_set": dfl_origin_set,
                    "declaration_id": old_declaration_id,
                    "curve_id": curve_id,
                    "local_date": date(1990, 1, 2),
                },
            )
            with pytest.raises(
                DBAPIError, match="historical rating attribution selector mismatch"
            ):
                _publish_direct_sql(conn, generation_id)

    @pytest.mark.parametrize(
        ("case", "manifest_declarations", "persisted_declarations", "state"),
        [
            (
                "equal-start-tie",
                [
                    _decl(
                        "447",
                        sha256_hex_bytes(
                            canonical_json_bytes(_raw_rating_payload("447"))
                        ),
                        _raw_rating_payload("447"),
                        from_date="1990-01-01",
                        shape="raw_usable",
                    ),
                    _decl(
                        "447",
                        sha256_hex_bytes(
                            canonical_json_bytes(
                                _raw_rating_payload(
                                    "447",
                                    points=[
                                        {"stage_m": 0.0, "discharge_m3s": 1.0},
                                        {"stage_m": 1.0, "discharge_m3s": 3.0},
                                    ],
                                )
                            )
                        ),
                        _raw_rating_payload(
                            "447",
                            points=[
                                {"stage_m": 0.0, "discharge_m3s": 1.0},
                                {"stage_m": 1.0, "discharge_m3s": 3.0},
                            ],
                        ),
                        from_date="1990-01-01",
                        shape="raw_usable",
                    ),
                ],
                [
                    (
                        sha256_hex_bytes(
                            canonical_json_bytes(_raw_rating_payload("447"))
                        ),
                        _raw_rating_payload("447"),
                        sha256_hex_bytes(
                            canonical_json_bytes(_effective_payload("447"))
                        ),
                        _effective_payload("447"),
                        "raw_usable",
                    ),
                    (
                        sha256_hex_bytes(
                            canonical_json_bytes(
                                _raw_rating_payload(
                                    "447",
                                    points=[
                                        {"stage_m": 0.0, "discharge_m3s": 1.0},
                                        {"stage_m": 1.0, "discharge_m3s": 3.0},
                                    ],
                                )
                            )
                        ),
                        _raw_rating_payload(
                            "447",
                            points=[
                                {"stage_m": 0.0, "discharge_m3s": 1.0},
                                {"stage_m": 1.0, "discharge_m3s": 3.0},
                            ],
                        ),
                        sha256_hex_bytes(
                            canonical_json_bytes(
                                _effective_payload(
                                    "447",
                                    points=[
                                        {"stage_m": 0.0, "discharge_m3s": 1.0},
                                        {"stage_m": 1.0, "discharge_m3s": 3.0},
                                    ],
                                )
                            )
                        ),
                        _effective_payload(
                            "447",
                            points=[
                                {"stage_m": 0.0, "discharge_m3s": 1.0},
                                {"stage_m": 1.0, "discharge_m3s": 3.0},
                            ],
                        ),
                        "raw_usable",
                    ),
                ],
                "held_equal_start_tie",
            ),
            (
                "invalid-latest-no-fallback",
                [
                    _decl(
                        "447",
                        sha256_hex_bytes(
                            canonical_json_bytes(
                                _raw_rating_payload("447", from_date="1989-01-01")
                            )
                        ),
                        _raw_rating_payload("447", from_date="1989-01-01"),
                        from_date="1989-01-01",
                        shape="raw_usable",
                    ),
                    _decl(
                        "447",
                        sha256_hex_bytes(
                            canonical_json_bytes(
                                _raw_rating_payload(
                                    "447",
                                    points=[
                                        {"stage_m": 0.0, "discharge_m3s": 2.0},
                                        {"stage_m": 1.0, "discharge_m3s": 1.0},
                                    ],
                                )
                            )
                        ),
                        _raw_rating_payload(
                            "447",
                            points=[
                                {"stage_m": 0.0, "discharge_m3s": 2.0},
                                {"stage_m": 1.0, "discharge_m3s": 1.0},
                            ],
                        ),
                        from_date="1990-01-01",
                        shape="decreasing_invalid",
                    ),
                ],
                [
                    (
                        sha256_hex_bytes(
                            canonical_json_bytes(
                                _raw_rating_payload("447", from_date="1989-01-01")
                            )
                        ),
                        _raw_rating_payload("447", from_date="1989-01-01"),
                        "3" * 64,
                        _effective_payload("447"),
                        "raw_usable",
                    ),
                    (
                        sha256_hex_bytes(
                            canonical_json_bytes(
                                _raw_rating_payload(
                                    "447",
                                    points=[
                                        {"stage_m": 0.0, "discharge_m3s": 2.0},
                                        {"stage_m": 1.0, "discharge_m3s": 1.0},
                                    ],
                                )
                            )
                        ),
                        _raw_rating_payload(
                            "447",
                            points=[
                                {"stage_m": 0.0, "discharge_m3s": 2.0},
                                {"stage_m": 1.0, "discharge_m3s": 1.0},
                            ],
                        ),
                        None,
                        None,
                        "decreasing_invalid",
                    ),
                ],
                "held_invalid_latest",
            ),
        ],
    )
    def test_publication_accepts_historical_held_selector_states(
        self,
        migration_engine: tuple[sa.Engine, str],
        case: str,
        manifest_declarations: list[dict[str, object]],
        persisted_declarations: list[
            tuple[str, dict[str, object], str | None, dict[str, object] | None, str]
        ],
        state: str,
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, manifest_declarations
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            rt_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="RT_DECLARATION",
                payload_sha="8" * 64,
            )
            for (
                declaration_sha,
                raw_payload,
                function_sha,
                function_payload,
                shape,
            ) in persisted_declarations:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO dhm_rating_declarations (
                            id, generation_id, tenant_id, station_id, cohort, station_code,
                            from_local_date, to_local_date_inclusive, rating_type_label,
                            from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                            function_payload_json, function_equivalence_sha256,
                            shape_state, origin_set_sha256
                        ) VALUES (
                            :id, :generation_id, :tenant_id, :station_id, 'nepal21', '447',
                            :from_date, :to_date, '1', 0.0,
                            CAST(:raw AS jsonb), :decl_sha, CAST(:function AS jsonb),
                            :function_sha, :shape, :origin_set
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "station_id": station_id,
                        "from_date": raw_payload["from_local_date"],
                        "to_date": raw_payload["to_local_date_inclusive"],
                        "raw": json.dumps(raw_payload),
                        "decl_sha": declaration_sha,
                        "function": json.dumps(function_payload)
                        if function_payload is not None
                        else None,
                        "function_sha": function_sha,
                        "shape": shape,
                        "origin_set": rt_origin_set,
                    },
                )
            observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 2.0,
                        'manual_import', :delivery_id, 'raw', '[]'::jsonb, NULL,
                        :created_at
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(1990, 1, 2, tzinfo=UTC),
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            dfl_origin_set = _insert_origin_set(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="DFL_RECORD",
                payload_sha="6" * 64,
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_delivery_observation_attributions (
                        generation_id, tenant_id, observation_id, station_id, cohort,
                        station_code, origin_set_sha256, rating_declaration_id,
                        rating_curve_id, attribution_state, local_date, reported_q
                    ) VALUES (
                        :generation_id, :tenant_id, :observation_id, :station_id,
                        'nepal21', '447', :origin_set, NULL, NULL,
                        :state, :local_date, 2.0
                    )
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": observation_id,
                    "station_id": station_id,
                    "origin_set": dfl_origin_set,
                    "state": state,
                    "local_date": date(1990, 1, 2),
                },
            )
            _publish_direct_sql(conn, generation_id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )

    @pytest.mark.parametrize(
        ("override_key", "override_value"),
        [
            ("origin_id", uuid.uuid4()),
            ("relative_path", "RT_350_tampered.txt"),
            ("file_sha256", "9" * 64),
            ("parser_version", "tampered-parser"),
            ("row_or_block_ordinal", 99),
        ],
    )
    def test_origin_identity_digest_binds_complete_source_identity(
        self,
        migration_engine: tuple[sa.Engine, str],
        override_key: str,
        override_value: object,
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl("350", "a" * 64, {"case": "complete"})
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            expected_origin: dict[str, object] = {
                "member_ordinal": 0,
                "origin_id": uuid.uuid4(),
                "origin_kind": "RT",
                "source_root_label": "synthetic_test",
                "relative_path": "RT_350.txt",
                "file_sha256": "7" * 64,
                "parser_version": "test",
                "row_or_block_ordinal": 0,
                "origin_payload_sha256": "8" * 64,
            }
            actual_origin: dict[str, object] = dict(expected_origin)
            actual_origin[override_key] = override_value
            origin_set = origin_identity_set_sha256(
                [expected_origin],
                set_kind="RT_DECLARATION",
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_delivery_origins (
                        id, generation_id, tenant_id, cohort, origin_kind, source_root_label,
                        relative_path, file_sha256, parser_version, row_or_block_ordinal,
                        origin_payload_sha256
                    ) VALUES (
                        :id, :generation_id, :tenant_id, 'nepal21', 'RT', 'synthetic_test',
                        :relative_path, :file_sha, :parser_version, :row_or_block_ordinal,
                        :payload_sha
                    )
                    """
                ),
                {
                    "id": actual_origin["origin_id"],
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "relative_path": actual_origin["relative_path"],
                    "file_sha": actual_origin["file_sha256"],
                    "parser_version": actual_origin["parser_version"],
                    "row_or_block_ordinal": actual_origin["row_or_block_ordinal"],
                    "payload_sha": actual_origin["origin_payload_sha256"],
                },
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_origin_sets (
                        generation_id, tenant_id, origin_set_sha256, member_count, set_kind
                    ) VALUES (:generation_id, :tenant_id, :origin_set, 1, 'RT_DECLARATION')
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "origin_set": origin_set,
                },
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_origin_set_members (
                        generation_id, tenant_id, origin_set_sha256, origin_id, origin_kind, member_ordinal
                    ) VALUES (:generation_id, :tenant_id, :origin_set, :origin_id, 'RT', 0)
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "origin_set": origin_set,
                    "origin_id": actual_origin["origin_id"],
                },
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
            )
            with pytest.raises(DBAPIError, match="origin set membership mismatch"):
                _publish_direct_sql(conn, generation_id)

    def test_publication_accepts_complete_rt_and_dfl_multi_member_origin_rosters(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl(
            "447",
            "a" * 64,
            {"case": "multi-member"},
            from_date="1990-01-01",
        )
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            rt_identities = [
                {
                    "member_ordinal": ordinal,
                    "origin_id": uuid.uuid4(),
                    "origin_kind": "RT",
                    "source_root_label": "synthetic_test",
                    "relative_path": f"RT_447_part_{ordinal}.txt",
                    "file_sha256": f"{ordinal + 7}" * 64,
                    "parser_version": "rt-parser-v1",
                    "row_or_block_ordinal": ordinal,
                    "origin_payload_sha256": f"{ordinal + 1}" * 64,
                }
                for ordinal in range(2)
            ]
            rt_origin_set = _insert_origin_set_from_identities(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="RT_DECLARATION",
                identities=rt_identities,
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=rt_origin_set,
                declaration=declaration,
            )
            observation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 2.0,
                        'manual_import', :delivery_id, 'raw', '[]'::jsonb, NULL,
                        :created_at
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "station_id": station_id,
                    "timestamp": datetime(1988, 1, 2, tzinfo=UTC),
                    "delivery_id": delivery_id,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            dfl_identities = [
                {
                    "member_ordinal": ordinal,
                    "origin_id": uuid.uuid4(),
                    "origin_kind": "DFL",
                    "source_root_label": "synthetic_test",
                    "relative_path": f"DFL_447_part_{ordinal}.txt",
                    "file_sha256": f"{ordinal + 3}" * 64,
                    "parser_version": "dfl-parser-v1",
                    "row_or_block_ordinal": ordinal,
                    "origin_payload_sha256": f"{ordinal + 5}" * 64,
                }
                for ordinal in range(2)
            ]
            dfl_origin_set = _insert_origin_set_from_identities(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="DFL_RECORD",
                identities=dfl_identities,
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO dhm_delivery_observation_attributions (
                        generation_id, tenant_id, observation_id, station_id, cohort,
                        station_code, origin_set_sha256, rating_declaration_id,
                        rating_curve_id, attribution_state, local_date, reported_q
                    ) VALUES (
                        :generation_id, :tenant_id, :observation_id, :station_id,
                        'nepal21', '447', :origin_set, NULL, NULL,
                        'not_applicable', :local_date, 2.0
                    )
                    """
                ),
                {
                    "generation_id": generation_id,
                    "tenant_id": tenant_id,
                    "observation_id": observation_id,
                    "station_id": station_id,
                    "origin_set": dfl_origin_set,
                    "local_date": date(1988, 1, 2),
                },
            )
            _publish_direct_sql(conn, generation_id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )

    @pytest.mark.parametrize("tamper", ["source_root", "member_order"])
    def test_origin_identity_digest_binds_multi_member_roster(
        self, migration_engine: tuple[sa.Engine, str], tamper: str
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declaration = _decl("447", "a" * 64, {"case": "multi-negative"})
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            station_id = _station(conn, tenant_id)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, [declaration]
            )
            generation_id = _insert_building_generation(
                conn,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
            )
            identities = [
                {
                    "member_ordinal": ordinal,
                    "origin_id": uuid.uuid4(),
                    "origin_kind": "RT",
                    "source_root_label": "synthetic_test",
                    "relative_path": f"RT_447_part_{ordinal}.txt",
                    "file_sha256": f"{ordinal + 7}" * 64,
                    "parser_version": "rt-parser-v1",
                    "row_or_block_ordinal": ordinal,
                    "origin_payload_sha256": f"{ordinal + 1}" * 64,
                }
                for ordinal in range(2)
            ]
            actual_identities = [dict(identity) for identity in identities]
            member_ordinals: list[int] | None = None
            if tamper == "source_root":
                actual_identities[1]["source_root_label"] = "old"
            if tamper == "member_order":
                member_ordinals = [1, 0]
            origin_set = origin_identity_set_sha256(
                identities,
                set_kind="RT_DECLARATION",
            )
            _insert_origin_set_from_identities(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                set_kind="RT_DECLARATION",
                identities=actual_identities,
                member_ordinals=member_ordinals,
                origin_set_sha=origin_set,
            )
            _insert_declaration_row(
                conn,
                generation_id=generation_id,
                tenant_id=tenant_id,
                station_id=station_id,
                origin_set=origin_set,
                declaration=declaration,
            )
            with pytest.raises(DBAPIError, match="origin set membership mismatch"):
                _publish_direct_sql(conn, generation_id)


class TestNepal21WitnessCatalogPublicationActualSessionUser:
    @pytest.mark.parametrize(
        ("manifest_declarations", "persisted_indexes", "expected"),
        [
            (
                [
                    _decl("350", "a" * 64, {"case": "older"}, from_date="1980-01-01"),
                    _decl(
                        "350",
                        "b" * 64,
                        {"case": "newest_invalid"},
                        from_date="2020-01-01",
                    ),
                ],
                [0],
                "manifest declaration set mismatch",
            ),
            (
                [
                    _decl(
                        "602.5",
                        "c" * 64,
                        {"case": "earlier_invalid"},
                        from_date="1980-01-01",
                    ),
                    _decl("602.5", "d" * 64, {"case": "later"}, from_date="2020-01-01"),
                ],
                [1],
                "manifest declaration set mismatch",
            ),
            (
                [
                    _decl(
                        "404.7",
                        "e" * 64,
                        {"case": "tie_a"},
                        from_date="2000-01-01",
                        label="2",
                    ),
                    _decl(
                        "404.7",
                        "f" * 64,
                        {"case": "tie_b"},
                        from_date="2000-01-01",
                        label="3",
                    ),
                ],
                [0],
                "manifest declaration set mismatch",
            ),
        ],
    )
    def test_actual_session_user_refuses_manifest_declaration_omissions(
        self,
        migration_engine: tuple[sa.Engine, str],
        manifest_declarations: list[dict[str, object]],
        persisted_indexes: list[int],
        expected: str,
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        tenant_id, release_id, digest, delivery_id, station_id = _prepare_owner_release(
            engine, manifest_declarations
        )
        with pytest.raises(DBAPIError, match=expected):
            _run_publication_as_writer(
                engine,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
                station_id=station_id,
                declarations=manifest_declarations,
                persisted_indexes=persisted_indexes,
            )

    def test_actual_session_user_refuses_same_count_raw_substitution(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declarations = [_decl("350", "a" * 64, {"case": "expected"})]
        tenant_id, release_id, digest, delivery_id, station_id = _prepare_owner_release(
            engine, declarations
        )
        with pytest.raises(DBAPIError, match="manifest declaration set mismatch"):
            _run_publication_as_writer(
                engine,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
                station_id=station_id,
                declarations=declarations,
                raw_override={"case": "substituted"},
            )

    def test_actual_session_user_refuses_origin_member_mismatch(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declarations = [_decl("350", "a" * 64, {"case": "origin"})]
        tenant_id, release_id, digest, delivery_id, station_id = _prepare_owner_release(
            engine, declarations
        )
        with pytest.raises(DBAPIError, match="origin set membership mismatch"):
            _run_publication_as_writer(
                engine,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
                station_id=station_id,
                declarations=declarations,
                origin_set_override="9" * 64,
            )

    def test_actual_session_user_refuses_unauthorized_policy_scope(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declarations = [_decl("350", "a" * 64, {"case": "scope"})]
        tenant_id, release_id, digest, delivery_id, station_id = _prepare_owner_release(
            engine, declarations, policy_scope="foreign"
        )
        with pytest.raises(DBAPIError, match="manifest policy scope"):
            _run_publication_as_writer(
                engine,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha=digest,
                delivery_id=delivery_id,
                station_id=station_id,
                declarations=declarations,
            )

    def test_actual_session_user_accepts_complete_manifest_catalog(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declarations = [_decl("350", "a" * 64, {"case": "complete"})]
        tenant_id, release_id, digest, delivery_id, station_id = _prepare_owner_release(
            engine, declarations
        )
        generation_id = _run_publication_as_writer(
            engine,
            tenant_id=tenant_id,
            release_id=release_id,
            manifest_sha=digest,
            delivery_id=delivery_id,
            station_id=station_id,
            declarations=declarations,
        )
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )

    def test_actual_session_user_refuses_generation_parent_seal_mismatch(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declarations = [_decl("350", "a" * 64, {"case": "seal"})]
        tenant_id, release_id, digest, delivery_id, station_id = _prepare_owner_release(
            engine, declarations
        )
        assert digest != "9" * 64
        with pytest.raises(DBAPIError, match="parent seal"):
            _run_publication_as_writer(
                engine,
                tenant_id=tenant_id,
                release_id=release_id,
                manifest_sha="9" * 64,
                delivery_id=delivery_id,
                station_id=station_id,
                declarations=declarations,
            )

    def test_actual_session_user_refuses_selected_attribution_three_pointer_mismatch(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        raw_payload = _raw_rating_payload(
            "350",
            from_date="1991-01-01",
            to_date="1992-01-01",
            points=[{"stage_m": 0.0, "discharge_m3s": 1.0}],
        )
        declaration = _decl(
            "350",
            sha256_hex_bytes(canonical_json_bytes(raw_payload)),
            raw_payload,
            from_date="1991-01-01",
            shape="raw_usable",
        )
        tenant_id, release_id, digest, delivery_id, station_id = _prepare_owner_release(
            engine, [declaration]
        )
        conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        generation_id = uuid.uuid4()
        try:
            conn.execute(
                sa.text("SET SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
            )
            assert conn.execute(sa.text("SELECT session_user, current_user")).one() == (
                "sapphire_dhm21_delivery_writer",
                "sapphire_dhm21_delivery_writer",
            )
            conn.execute(sa.text("BEGIN"))
            try:
                generation_id = _insert_building_generation(
                    conn,
                    tenant_id=tenant_id,
                    release_id=release_id,
                    manifest_sha=digest,
                    delivery_id=delivery_id,
                )
                origin_set = _insert_origin_set(
                    conn, generation_id=generation_id, tenant_id=tenant_id
                )
                curve_a = uuid.uuid4()
                curve_b = uuid.uuid4()
                obs_id = uuid.uuid4()
                decl_id = uuid.uuid4()
                for curve_id, version in ((curve_a, 1), (curve_b, 2)):
                    conn.execute(
                        sa.text(
                            """
                            INSERT INTO rating_curves (
                                id, station_id, version, valid_from, valid_to, points,
                                interpolation, delivery_id, rating_type_label, created_at
                            ) VALUES (
                                :id, :station_id, :version, :valid_from, :valid_to,
                                '[{"water_level": 0.0, "discharge": 1.0}]'::jsonb,
                                'linear', :delivery_id, '1', :created_at
                            )
                            """
                        ),
                        {
                            "id": curve_id,
                            "station_id": station_id,
                            "version": version,
                            "valid_from": datetime(1991, 1, 1, tzinfo=UTC),
                            "valid_to": datetime(1992, 1, 1, tzinfo=UTC),
                            "delivery_id": delivery_id,
                            "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                        },
                    )
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO observations (
                            id, station_id, timestamp, parameter, value, source,
                            rating_curve_id, rating_curve_correction_version,
                            delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                        ) VALUES (
                            :id, :station_id, :timestamp, 'discharge', 1.0,
                            'manual_import', :curve_id, '1', :delivery_id,
                            'raw', '[]'::jsonb, NULL, :created_at
                        )
                        """
                    ),
                    {
                        "id": obs_id,
                        "station_id": station_id,
                        "timestamp": datetime(1991, 1, 2, tzinfo=UTC),
                        "curve_id": curve_a,
                        "delivery_id": delivery_id,
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO dhm_rating_declarations (
                            id, generation_id, tenant_id, station_id, cohort, station_code,
                            from_local_date, to_local_date_inclusive, rating_type_label,
                            from_stage_m, raw_declaration_payload_json, declaration_content_sha256,
                            function_payload_json, function_equivalence_sha256,
                            native_curve_payload_json, shape_state, usable_rating_curve_id,
                            origin_set_sha256
                        ) VALUES (
                            :id, :generation_id, :tenant_id, :station_id, 'nepal21', '350',
                            '1991-01-01', '1992-01-01', '1', 0.0,
                            CAST(:raw AS jsonb), :digest, CAST(:function_payload AS jsonb), :function_sha,
                            CAST(:native_payload AS jsonb), 'raw_usable', :curve_a, :origin_set
                        )
                        """
                    ),
                    {
                        "id": decl_id,
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "station_id": station_id,
                        "raw": json.dumps(declaration["raw_declaration_payload_json"]),
                        "digest": declaration["declaration_content_sha256"],
                        "function_payload": json.dumps(
                            _effective_payload(
                                "350", points=[{"stage_m": 0.0, "discharge_m3s": 1.0}]
                            )
                        ),
                        "function_sha": sha256_hex_bytes(
                            canonical_json_bytes(
                                _effective_payload(
                                    "350",
                                    points=[{"stage_m": 0.0, "discharge_m3s": 1.0}],
                                )
                            )
                        ),
                        "native_payload": json.dumps(
                            _native_payload(
                                valid_from="1991-01-01T00:00:00+00:00",
                                valid_to="1992-01-01T00:00:00+00:00",
                                points=[{"water_level": 0.0, "discharge": 1.0}],
                            )
                        ),
                        "curve_a": curve_a,
                        "origin_set": origin_set,
                    },
                )
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO dhm_delivery_observation_attributions (
                            generation_id, tenant_id, observation_id, station_id, cohort,
                            station_code, origin_set_sha256, rating_declaration_id,
                            rating_curve_id, attribution_state, local_date, reported_q
                        ) VALUES (
                            :generation_id, :tenant_id, :observation_id, :station_id,
                            'nepal21', '350', :origin_set, :declaration_id,
                            :wrong_curve_id, 'selected', :local_date, 1.0
                        )
                        """
                    ),
                    {
                        "generation_id": generation_id,
                        "tenant_id": tenant_id,
                        "observation_id": obs_id,
                        "station_id": station_id,
                        "origin_set": origin_set,
                        "declaration_id": decl_id,
                        "wrong_curve_id": curve_b,
                        "local_date": datetime(1991, 1, 2, tzinfo=UTC).date(),
                    },
                )
                with pytest.raises(DBAPIError, match="three-pointer"):
                    conn.execute(
                        sa.text(
                            """
                            UPDATE restricted_delivery_generations
                            SET state='active_pending_qc', immutable_content_sha256=:content, published_at=:published
                            WHERE id=:id
                            """
                        ),
                        {
                            "id": generation_id,
                            "content": "4" * 64,
                            "published": datetime(2026, 10, 12, tzinfo=UTC),
                        },
                    )
                conn.execute(sa.text("ROLLBACK"))
            except BaseException:
                if conn.scalar(
                    sa.text("SELECT txid_current_if_assigned() IS NOT NULL")
                ):
                    conn.execute(sa.text("ROLLBACK"))
                raise
        finally:
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
            conn.close()

    def test_definer_current_user_owner_does_not_bypass_unauthorized_session_user(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        declarations = [_decl("350", "a" * 64, {"case": "unauthorized"})]
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            release_id, digest, delivery_id = _insert_policy_release(
                conn, tenant_id, declarations
            )
            generation_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO restricted_delivery_generations (
                        id, release_id, tenant_id, network, cohort, delivery_id,
                        predecessor_delivery_id, predecessor_semantic_sha256,
                        preimage_sha256, manifest_sha256, station_set_sha256,
                        state, immutable_content_sha256, created_at, published_at
                    ) VALUES (
                        :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                        'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                        :manifest, :station_set, 'active_pending_qc', :content_sha,
                        :created_at, :published_at
                    )
                    """
                ),
                {
                    "id": generation_id,
                    "release_id": release_id,
                    "tenant_id": tenant_id,
                    "delivery_id": delivery_id,
                    "predecessor": "2" * 64,
                    "preimage": "3" * 64,
                    "manifest": digest,
                    "station_set": "1" * 64,
                    "content_sha": "4" * 64,
                    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    "published_at": datetime(2026, 10, 12, tzinfo=UTC),
                },
            )
            conn.execute(
                sa.text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nepal21_unauthorized_runtime') THEN
                            CREATE ROLE nepal21_unauthorized_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
                        END IF;
                    END $$
                    """
                )
            )
        conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            conn.execute(
                sa.text("SET SESSION AUTHORIZATION nepal21_unauthorized_runtime")
            )
            identity = conn.execute(sa.text("SELECT session_user, current_user")).one()
            assert identity == (
                "nepal21_unauthorized_runtime",
                "nepal21_unauthorized_runtime",
            )
            with pytest.raises(
                DBAPIError,
                match="session_user=nepal21_unauthorized_runtime, current_user=",
            ):
                conn.execute(
                    sa.text(
                        "SELECT public.sf_assert_restricted_publication_catalog(:id)"
                    ),
                    {"id": generation_id},
                )
        finally:
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
            conn.close()
