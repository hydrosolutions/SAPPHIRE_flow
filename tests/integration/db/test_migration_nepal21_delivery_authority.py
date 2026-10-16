from __future__ import annotations

# ruff: noqa: E501
import copy
import dataclasses
import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from itertools import count
from typing import TYPE_CHECKING, Any, Literal, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sapphire_flow.services.nepal21_source_catalog import (
        CanonicalizationPolicy,
        DhmSourceFile,
        SourceCatalog,
    )

from alembic import command

if TYPE_CHECKING:
    from pathlib import Path
from sapphire_flow.db import metadata as db_schema
from sapphire_flow.db.metadata import (
    measurement_feed_evidence,
    provisional_discharges,
    rating_reference_proofs,
)
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.nepal21_delivery_authority import (
    ExpectedQcPolicyPin,
    GenerationBuildInput,
    RestrictedReleaseInput,
    RestrictedReplacementPayload,
    WitnessBytes,
    _normalize_predecessor_row,  # pyright: ignore[reportPrivateUsage]
    canonical_json_bytes,
    fetch_predecessor_semantic_rows,
    insert_restricted_release,
    origin_identity_set_sha256,
    predecessor_semantic_digest,
    publish_generation,
    readback_generation_content_digest,
    run_dhm21_delivery_replacement_dry_run,
    run_dhm21_delivery_replacement_transaction,
    run_dhm21_qc_transaction,
    sha256_hex_bytes,
    station_set_sha256,
)
from sapphire_flow.services.nepal21_payload_builder import (
    SourceStationBinding,
    build_nepal21_replacement_payload,
)
from sapphire_flow.services.nepal21_source_catalog import build_nepal21_source_catalog
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.rating_reference import (
    canonical_content,
    content_digest,
    curve_snapshot,
    measurement_snapshot,
)
from tests.integration.db.test_migration_0065_rejected_forecasts import (
    migration_engine as migration_engine,
)
from tests.integration.store.test_provisional_discharge_store import (
    NOW,
    convert,
    permit_fixture,
    raw_values,
    seed,
    seed_reference,
)
from tests.unit.services.test_nepal21_source_catalog import (
    _sources,  # pyright: ignore[reportPrivateUsage]
    _synthetic_approval,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.services.test_provisional_discharge import inputs

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


def _station(
    conn: sa.Connection, tenant_id: uuid.UUID, *, code: str = "447"
) -> uuid.UUID:
    station_id = uuid.uuid4()
    conn.execute(
        sa.text(
            """
            INSERT INTO stations (
                id, code, name, location, station_kind, timezone,
                measured_parameters, station_status, network, ownership,
                gauging_status, tenant_id
            ) VALUES (
                :id, :code, 'Trisuli at Betrawati', ST_GeomFromText('POINT(85 27)', 4326),
                'river', 'Asia/Kathmandu', ARRAY['discharge'], 'onboarding',
                'dhm', 'foreign', 'gauged', :tenant_id
            )
            """
        ),
        {"id": station_id, "tenant_id": tenant_id, "code": code},
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
            candidate_functions = (
                "public.sf_assert_nepal21_candidate_lineage(uuid)",
                "public.sf_nepal21_candidate_commit_guard()",
                "public.sf_nepal21_candidate_dependency_guard()",
            )
            for signature in candidate_functions:
                assert (
                    conn.scalar(
                        sa.text("SELECT to_regprocedure(:signature)"),
                        {"signature": signature},
                    )
                    is not None
                )
            candidate_triggers = {
                "trg_nepal21_candidate_commit_guard",
                "trg_nepal21_candidate_basin_guard",
                "trg_nepal21_candidate_package_guard",
                "trg_nepal21_candidate_binding_guard",
            }
            assert (
                set(
                    conn.execute(
                        sa.text(
                            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname=ANY(:names)"
                        ),
                        {"names": sorted(candidate_triggers)},
                    ).scalars()
                )
                == candidate_triggers
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
            for signature in candidate_functions:
                assert (
                    conn.scalar(
                        sa.text("SELECT to_regprocedure(:signature)"),
                        {"signature": signature},
                    )
                    is None
                )
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
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            prepared = _prepare_full21_generation(conn, tenant_id, tmp_path)
            run_dhm21_delivery_replacement_transaction(conn, prepared.build)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id=:delivery"
                    ),
                    {"delivery": prepared.build.delivery_id},
                )
                == 0
            )
            # Full21 DFL witnesses now reject this empty generation before the
            # older, later no-observation/no-declaration guard can be reached.
            with pytest.raises(
                DBAPIError,
                match="persisted source occurrences differ from authorized parsed records",
            ):
                _publish_direct_sql(conn, prepared.build.id)

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
            non_old6_station_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO stations (
                        id, code, name, location, station_kind, timezone,
                        measured_parameters, station_status, network, ownership,
                        gauging_status, tenant_id
                    ) VALUES (
                        :id, '225', 'Non old6 station', ST_GeomFromText('POINT(85 27)', 4326),
                        'river', 'Asia/Kathmandu', ARRAY['discharge'], 'onboarding',
                        'dhm', 'foreign', 'gauged', :tenant_id
                    )
                    """
                ),
                {"id": non_old6_station_id, "tenant_id": tenant_id},
            )
            old_curve_id = uuid.uuid4()
            old_obs_id = uuid.uuid4()
            non_old6_old_obs_id = uuid.uuid4()
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
            conn.execute(
                sa.text(
                    """
                    INSERT INTO observations (
                        id, station_id, timestamp, parameter, value, source,
                        delivery_id, qc_status, qc_flags, qc_rule_version, created_at
                    ) VALUES (
                        :id, :station_id, :timestamp, 'discharge', 9.0,
                        'manual_import', 'dhm-barkhk-2026-09-08',
                        'raw', '[]'::jsonb, NULL, :created_at
                    )
                    """
                ),
                {
                    "id": non_old6_old_obs_id,
                    "station_id": non_old6_station_id,
                    "timestamp": datetime(1990, 1, 2, tzinfo=UTC),
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            )
            prepared = _prepare_full21_generation(
                conn, TenantId(tenant_id), tmp_path / "actual"
            )
            build, payload = prepared.build, prepared.payload
            generation_id, release_id = build.id, build.release_id
            preimage_sha = build.preimage_sha256
            new_obs_id = next(
                row["id"]
                for row in payload.observation_rows
                if row["station_id"] == station_id
            )
            dry_payload = dataclasses.replace(payload, preimage_dir=tmp_path / "dry")
            assert (
                run_dhm21_delivery_replacement_dry_run(conn, build, dry_payload)
                == preimage_sha
            )
            assert (
                conn.scalar(
                    sa.text(
                        """
                        SELECT count(*) FROM observations o
                        JOIN stations s ON s.id = o.station_id
                        WHERE o.delivery_id='dhm-barkhk-2026-09-08'
                          AND s.code = '447'
                        """
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
            with pytest.raises(FileExistsError, match="File exists"):
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
                immutable_content_sha256=readback_generation_content_digest(
                    conn, generation_id
                ),
                published_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
            )
            assert (
                conn.scalar(
                    sa.text(
                        """
                        SELECT count(*) FROM observations o
                        JOIN stations s ON s.id = o.station_id
                        WHERE o.delivery_id='dhm-barkhk-2026-09-08'
                          AND s.code = '447'
                        """
                    )
                )
                == 0
            )
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM observations WHERE id = :id"),
                    {"id": non_old6_old_obs_id},
                )
                == 1
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id LIKE 'dhm-nepal21-%'"
                    )
                )
                == 42
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
                readback_generation_content_digest(conn, generation_id),
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
                    *payload.observation_rows[1:],
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
                    *payload.observation_rows[1:],
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
                    *payload.origin_rows[1:],
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
                == 42
            )
            qc_pin = prepared.policy
            qc_policy_sha = qc_pin.policy_sha256
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
            tenant_id = TenantId(_tenant(conn))
            prepared = _prepare_full21_generation(conn, tenant_id, tmp_path / "writer")
            # Actual principal under this legacy grant fixture, not shipped-bootstrap proof.
            _grant_writer_bootstrap_privileges(conn)
        with engine.connect() as conn:
            conn.execute(
                sa.text("SET SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
            )
            assert conn.execute(sa.text("SELECT session_user,current_user")).one() == (
                "sapphire_dhm21_delivery_writer",
                "sapphire_dhm21_delivery_writer",
            )
            try:
                run_dhm21_delivery_replacement_transaction(
                    conn, prepared.build, prepared.payload
                )
                publish_generation(
                    conn,
                    generation_id=prepared.build.id,
                    immutable_content_sha256=readback_generation_content_digest(
                        conn, prepared.build.id
                    ),
                    published_at=prepared.build.created_at,
                )
                completion_sha = run_dhm21_qc_transaction(
                    conn,
                    tenant_id=tenant_id,
                    generation_id=prepared.build.id,
                    expected_policy=prepared.policy,
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
                    "SELECT session_user,current_user,state,qc_policy_sha256,qc_completion_sha256 "
                    "FROM restricted_delivery_generations WHERE id=:id"
                ),
                {"id": prepared.build.id},
            ).one() == (
                "test",
                "test",
                "active_qc_complete",
                prepared.policy.policy_sha256,
                completion_sha,
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id=:delivery AND qc_status='qc_passed'"
                    ),
                    {"delivery": prepared.build.delivery_id},
                )
                == 42
            )

    def test_replacement_refuses_locked_predecessor_drift(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            station_id = _station(conn, tenant_id)
            prepared = _prepare_full21_generation(conn, tenant_id, tmp_path)
            # Real legacy data arrives after the owner pinned the empty predecessor.
            old_curve_id, old_obs_id = _seed_previous_delivery(conn, station_id)
            actual_sha = predecessor_semantic_digest(
                fetch_predecessor_semantic_rows(conn, tenant_id)
            )
            assert actual_sha != prepared.build.predecessor_semantic_sha256
            with pytest.raises(
                ValueError, match="locked predecessor semantic digest differs"
            ):
                run_dhm21_delivery_replacement_transaction(conn, prepared.build, None)
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM observations WHERE id=:id"),
                    {"id": old_obs_id},
                )
                == 1
            )
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM rating_curves WHERE id=:id"),
                    {"id": old_curve_id},
                )
                == 1
            )

    def test_qc_runner_preserves_failed_flags_and_refuses_missing_policy(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        sources: list[DhmSourceFile] = []
        for item in _sources():
            content = item.content
            if item.kind == "DFL":
                content += b"03/Jan/1991,3\n"
                if item.relative_path == "DFL_447.txt":
                    content = content.replace(b"02/Jan/1991,2\n", b"02/Jan/1991,99\n")
            sources.append(
                dataclasses.replace(
                    item, content=content, expected_sha256=sha256_hex_bytes(content)
                )
            )
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            station_id = _station(conn, tenant_id)
            prepared = _prepare_full21_generation(
                conn, tenant_id, tmp_path, sources=sources
            )
            generation_id, delivery_id = prepared.build.id, prepared.build.delivery_id
            obs_ids = [
                row["id"]
                for row in prepared.payload.observation_rows
                if row["station_id"] == station_id and row["value"] == 99.0
            ]
            assert len(obs_ids) == 1
            run_dhm21_delivery_replacement_transaction(
                conn, prepared.build, prepared.payload
            )
            publish_generation(
                conn,
                generation_id=generation_id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, generation_id
                ),
                published_at=prepared.build.created_at,
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
            valid_pin = prepared.policy
            qc_policy_sha = valid_pin.policy_sha256
            missing_network_pin = dataclasses.replace(valid_pin, station_networks={})
            missing_network_pin = dataclasses.replace(
                missing_network_pin,
                policy_sha256=sha256_hex_bytes(missing_network_pin.content_bytes),
            )
            with pytest.raises(ConfigurationError, match="missing network mapping"):
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
                == 42
            )
            for row_policy, flags, refusal in (
                ("stale-policy", [], "QC row policy"),
                (qc_policy_sha, [], "QC completion digest"),
                (
                    qc_policy_sha,
                    [
                        {
                            "rule_id": "range_check",
                            "rule_version": "stale",
                            "status": "qc_passed",
                        }
                    ],
                    "QC flags",
                ),
            ):
                forged_qc = conn.begin_nested()
                try:
                    conn.execute(
                        sa.text(
                            "UPDATE observations SET qc_status='qc_passed', qc_rule_version=:policy, qc_flags=CAST(:flags AS jsonb) WHERE delivery_id=:delivery"
                        ),
                        {
                            "delivery": delivery_id,
                            "policy": row_policy,
                            "flags": json.dumps(flags),
                        },
                    )
                    with pytest.raises(DBAPIError, match=refusal), conn.begin_nested():
                        conn.execute(
                            sa.text(
                                "UPDATE restricted_delivery_generations SET state='active_qc_complete', qc_policy_sha256=:policy, qc_completion_sha256=:completion, qc_completed_at=:at WHERE id=:id"
                            ),
                            {
                                "id": generation_id,
                                "policy": qc_policy_sha,
                                "completion": "6" * 64,
                                "at": datetime(2026, 10, 13, tzinfo=UTC),
                            },
                        )
                finally:
                    forged_qc.rollback()
            stale_rules = dataclasses.replace(
                valid_pin,
                rule_set=dataclasses.replace(
                    valid_pin.rule_set,
                    rules=(
                        dataclasses.replace(
                            valid_pin.rule_set.rules[0],
                            thresholds={"value_min": 0.0, "value_max": 100.0},
                        ),
                    ),
                ),
            )
            with pytest.raises(ValueError, match="QC policy bundle hash mismatch"):
                run_dhm21_qc_transaction(
                    conn,
                    tenant_id=TenantId(tenant_id),
                    generation_id=generation_id,
                    expected_policy=stale_rules,
                    completed_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
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
            with (
                pytest.raises(DBAPIError, match="QC fields are transition-only"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.text(
                        "UPDATE restricted_delivery_generations SET qc_run_id=:run WHERE id=:id"
                    ),
                    {"run": uuid.uuid4(), "id": generation_id},
                )

    def test_accepted_historical_reader_hides_restricted_until_qc_complete_generation(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            station_id = StationId(_station(conn, tenant_id))
            prepared = _prepare_full21_generation(conn, tenant_id, tmp_path)
            run_dhm21_delivery_replacement_transaction(
                conn, prepared.build, prepared.payload
            )
            store = PgObservationStore(conn)
            start = ensure_utc(datetime(1991, 1, 1, tzinfo=UTC))
            end = ensure_utc(datetime(1991, 1, 3, tzinfo=UTC))
            assert [
                row.value
                for row in store.fetch_observations(station_id, "discharge", start, end)
            ] == [2.0, 3.0]
            assert (
                store.fetch_accepted_historical_observations(
                    station_id, "discharge", start, end
                )
                == []
            )
            publish_generation(
                conn,
                generation_id=prepared.build.id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, prepared.build.id
                ),
                published_at=prepared.build.created_at,
            )
            assert (
                store.fetch_accepted_historical_observations(
                    station_id, "discharge", start, end
                )
                == []
            )
            # Even a completed row update does not make a pending generation accepted.
            pending_rows = conn.begin_nested()
            try:
                conn.execute(
                    sa.text(
                        "UPDATE observations SET qc_status='qc_passed',qc_rule_version=:policy "
                        "WHERE delivery_id=:delivery"
                    ),
                    {
                        "policy": prepared.policy.policy_sha256,
                        "delivery": prepared.build.delivery_id,
                    },
                )
                assert (
                    store.fetch_accepted_historical_observations(
                        station_id,
                        "discharge",
                        start,
                        end,
                        qc_status=QcStatus.QC_PASSED,
                    )
                    == []
                )
                with (
                    pytest.raises(
                        DBAPIError,
                        match="QC completion requires release-bound QC policy witness",
                    ),
                    conn.begin_nested(),
                ):
                    conn.execute(
                        sa.text(
                            "UPDATE restricted_delivery_generations SET state='active_qc_complete',"
                            "qc_policy_sha256=:policy,qc_completion_sha256=:completion,qc_completed_at=:at "
                            "WHERE id=:id"
                        ),
                        {
                            "id": prepared.build.id,
                            "policy": "5" * 64,
                            "completion": "6" * 64,
                            "at": datetime(2026, 10, 12, tzinfo=UTC),
                        },
                    )
            finally:
                pending_rows.rollback()
            run_dhm21_qc_transaction(
                conn,
                tenant_id=tenant_id,
                generation_id=prepared.build.id,
                expected_policy=prepared.policy,
                completed_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
            )
            accepted_rows = store.fetch_accepted_historical_observations(
                station_id, "discharge", start, end, qc_status=QcStatus.QC_PASSED
            )
            assert [row.value for row in accepted_rows] == [2.0, 3.0]
            assert (
                store.fetch_accepted_historical_observations(
                    station_id,
                    "discharge",
                    start,
                    end,
                    qc_status=QcStatus.QC_PASSED,
                    expected_qc_policy_sha256=prepared.policy.policy_sha256,
                )
                == accepted_rows
            )
            assert (
                store.fetch_accepted_historical_observations(
                    station_id,
                    "discharge",
                    start,
                    end,
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

    # Full21 cannot coexist under a second tenant with overlapping DHM codes.
    # Real publication refusal + unchanged legacy reads are covered in
    # test_nepal21_tenant_isolation.py, not an illegal partial-positive fixture.

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
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        sources: list[DhmSourceFile] = []
        for item in _sources():
            content = item.content
            if item.relative_path == "RT_447.txt":
                content = (
                    b"Station No:447\nData Type: Rating Table\nLeft Column= Stage in m\n"
                    b"Right Column= Discharge im m3/s\nRating Type No =1\n"
                    b"From Date =01-Jan-2025\nTo Date =31-May-2025\nFrom Stage =0\n1,10\n2,20\n"
                )
            elif item.kind == "DFL":
                content += b"03/Jan/1991,3\n"
            sources.append(
                dataclasses.replace(
                    item, content=content, expected_sha256=sha256_hex_bytes(content)
                )
            )
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            station_id = StationId(_station(conn, tenant_id))
            prepared = _prepare_full21_generation(
                conn, tenant_id, tmp_path, sources=sources
            )
            generation_id = prepared.build.id
            run_dhm21_delivery_replacement_transaction(
                conn, prepared.build, prepared.payload
            )
            (curve,) = PgRatingCurveStore(conn).fetch_delivery_curves(
                prepared.build.delivery_id, [station_id]
            )
            # Literal science oracle precedes proof rebinding; DB content is not the oracle.
            curve_points = cast("list[dict[str, float]]", cast("Any", curve).points)
            assert curve_points == [
                {"water_level": 1.0, "discharge": 10.0},
                {"water_level": 2.0, "discharge": 20.0},
            ]
            assert curve.valid_from == datetime(2024, 12, 31, 18, 15, tzinfo=UTC)
            assert curve.valid_to == datetime(2025, 5, 31, 18, 15, tzinfo=UTC)
            assert curve.interpolation.value == "linear"
            assert curve.rating_type_label == "1"
            curve_valid_to = curve.valid_to
            assert curve_valid_to is not None
            observation, _, feed, proof = inputs()
            measured_at = ensure_utc(datetime(2025, 5, 1, tzinfo=UTC))
            observation = dataclasses.replace(
                observation, station_id=station_id, timestamp=measured_at
            )
            feed = dataclasses.replace(
                feed,
                tenant_id=tenant_id,
                station_id=station_id,
                observation_id=observation.id,
                measurement=measurement_snapshot(observation),
                verified_at=measured_at,
            )
            proof = dataclasses.replace(
                proof,
                tenant_id=tenant_id,
                station_id=station_id,
                rating_curve_id=curve.id,
                curve=curve_snapshot(curve),
                verified_at=measured_at,
            )
            PgObservationStore(conn).store_observations([observation])
            seed_reference(conn, measurement_feed_evidence, feed)
            seed_reference(conn, rating_reference_proofs, proof)
            args = observation, curve, feed, proof
            assert convert(*args).discharge == 15.0
            publish_generation(
                conn,
                generation_id=generation_id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, generation_id
                ),
                published_at=prepared.build.created_at,
            )
            qc_policy_sha = prepared.policy.policy_sha256
            qc_completion = run_dhm21_qc_transaction(
                conn,
                tenant_id=tenant_id,
                generation_id=generation_id,
                expected_policy=prepared.policy,
                completed_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
            )
            origin_set = next(
                row["origin_set_sha256"]
                for row in prepared.payload.declaration_rows
                if row["station_code"] == "447"
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
                "qc_completion_sha256": qc_completion,
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
            before_expiry = ensure_utc(curve_valid_to - timedelta(seconds=1))
            assert observation.timestamp < before_expiry
            assert args[2].verified_at < before_expiry
            assert args[3].verified_at < before_expiry
            with pytest.raises(ValueError, match="must already be expired"):
                PgProvisionalDischargeStore(conn).store_provisional_discharge(
                    protected, captured_at=before_expiry
                )
            with (
                pytest.raises(DBAPIError, match="curve or reference disagreement"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.insert(provisional_discharges).values(
                        **raw_values(protected), captured_at=before_expiry
                    )
                )
            with (
                pytest.raises(Exception, match="curve or reference disagreement"),
                conn.begin_nested(),
            ):
                _copy_provisional_discharge_row(
                    conn, protected, captured_at=before_expiry
                )
            database_now = conn.execute(
                sa.select(sa.func.clock_timestamp())
            ).scalar_one()
            assert isinstance(database_now, datetime)
            future_capture = ensure_utc(database_now + timedelta(days=1))
            with pytest.raises(ValueError, match="capture time is in the future"):
                PgProvisionalDischargeStore(conn).store_provisional_discharge(
                    protected, captured_at=future_capture
                )
            with (
                pytest.raises(DBAPIError, match="capture time is in the future"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.insert(provisional_discharges).values(
                        **raw_values(protected), captured_at=future_capture
                    )
                )
            with (
                pytest.raises(Exception, match="capture time is in the future"),
                conn.begin_nested(),
            ):
                _copy_provisional_discharge_row(
                    conn, protected, captured_at=future_capture
                )
            wrong_generation_doc = json.loads(protected.content)
            wrong_generation_doc["nepal21_expired_permission"]["generation_id"] = str(
                uuid.uuid4()
            )
            wrong_generation_content = canonical_content(wrong_generation_doc)
            wrong_generation = dataclasses.replace(
                protected,
                content=wrong_generation_content,
                fingerprint=content_digest(wrong_generation_content),
            )
            with pytest.raises(ValueError, match="permission fingerprint"):
                PgProvisionalDischargeStore(conn).store_provisional_discharge(
                    wrong_generation, captured_at=NOW
                )
            with (
                pytest.raises(DBAPIError, match="permission fingerprint"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.insert(provisional_discharges).values(
                        **raw_values(wrong_generation), captured_at=NOW
                    )
                )
            with (
                pytest.raises(Exception, match="permission fingerprint"),
                conn.begin_nested(),
            ):
                _copy_provisional_discharge_row(conn, wrong_generation)
            other_tenant = _tenant_with_code(conn, "foreign-roster")
            with (
                pytest.raises(
                    DBAPIError,
                    match="measurement_feed_evidence_station_id_tenant_id_fkey",
                ),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.text("UPDATE stations SET tenant_id = :tenant WHERE id = :id"),
                    {"id": observation.station_id, "tenant": other_tenant},
                )
            for column, value, refusal in (
                ("code", "999", "station outside Nepal21 roster"),
                ("network", "other", "station outside Nepal21 roster"),
                ("code", "450", "declaration is absent"),
            ):
                wrong_roster = conn.begin_nested()
                try:
                    if column == "code" and value == "450":
                        conn.execute(
                            sa.text(
                                "UPDATE stations SET code='fixture-held-450' "
                                "WHERE tenant_id=:tenant AND network='dhm' AND code='450'"
                            ),
                            {"tenant": tenant_id},
                        )
                    conn.execute(
                        sa.text(
                            f"UPDATE stations SET {column} = :value WHERE id = :id"
                        ),
                        {"id": observation.station_id, "value": value},
                    )
                    with pytest.raises(ValueError, match=refusal):
                        PgProvisionalDischargeStore(conn).store_provisional_discharge(
                            protected, captured_at=NOW
                        )
                    with pytest.raises(DBAPIError, match=refusal), conn.begin_nested():
                        conn.execute(
                            sa.insert(provisional_discharges).values(
                                **raw_values(protected), captured_at=NOW
                            )
                        )
                    with pytest.raises(Exception, match=refusal), conn.begin_nested():
                        _copy_provisional_discharge_row(conn, protected)
                finally:
                    wrong_roster.rollback()
            assert (
                PgProvisionalDischargeStore(conn).store_provisional_discharge(
                    protected, captured_at=NOW
                )
                == protected.fingerprint
            )

    @pytest.mark.parametrize("copy_equivalence_digest", [False, True])
    @pytest.mark.parametrize("other_shape", ["raw_usable", "decreasing_invalid"])
    def test_expired_conversion_refuses_equal_start_non_equivalent_all_paths(
        self,
        migration_engine: tuple[sa.Engine, str],
        tmp_path: Path,
        other_shape: str,
        copy_equivalence_digest: bool,
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        rt_header = (
            b"Station No:447\nData Type: Rating Table\nLeft Column= Stage in m\n"
            b"Right Column= Discharge im m3/s\nRating Type No =1\n"
            b"From Date =01-Jan-2025\nTo Date =31-May-2025\nFrom Stage =0\n"
        )
        sources: list[DhmSourceFile] = []
        for item in _sources():
            content = item.content
            if item.relative_path == "RT_447.txt":
                content = rt_header + b"1,10\n2,20\n"
                other_content = rt_header + (
                    b"0,10\n1,20\n" if other_shape == "raw_usable" else b"0,30\n1,20\n"
                )
                sources.append(
                    dataclasses.replace(
                        item,
                        relative_path="RT_447_other.txt",
                        content=other_content,
                        expected_sha256=sha256_hex_bytes(other_content),
                    )
                )
            elif item.relative_path == "DFL_447.txt":
                content = b"Daily Flow of Station: 447 in m3/s\n2025\n01/Jan/2025,2\n02/Jan/2025,3\n"
            elif item.kind == "DFL":
                content += b"03/Jan/1991,3\n"
            sources.append(
                dataclasses.replace(
                    item, content=content, expected_sha256=sha256_hex_bytes(content)
                )
            )
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            station_id = StationId(_station(conn, tenant_id))
            observation, curve, feed, proof = inputs()
            observation = dataclasses.replace(observation, station_id=station_id)
            curve = dataclasses.replace(
                curve,
                station_id=station_id,
                rating_type_label="1",
                valid_from=ensure_utc(datetime(2024, 12, 31, 18, 15, tzinfo=UTC)),
                valid_to=ensure_utc(datetime(2025, 5, 31, 18, 15, tzinfo=UTC)),
            )
            curve_points = cast("list[dict[str, float]]", cast("Any", curve).points)
            assert curve_points == [
                {"water_level": 1.0, "discharge": 10.0},
                {"water_level": 2.0, "discharge": 20.0},
            ]
            feed = dataclasses.replace(
                feed,
                tenant_id=tenant_id,
                station_id=station_id,
                observation_id=observation.id,
                measurement=measurement_snapshot(observation),
            )
            proof = dataclasses.replace(
                proof,
                tenant_id=tenant_id,
                station_id=station_id,
                rating_curve_id=curve.id,
                curve=curve_snapshot(curve),
            )
            PgRatingCurveStore(conn).store_rating_curve(curve)
            PgObservationStore(conn).store_observations([observation])
            seed_reference(conn, measurement_feed_evidence, feed)
            seed_reference(conn, rating_reference_proofs, proof)
            args = observation, curve, feed, proof
            assert convert(*args).discharge == 15.0
            prepared = _prepare_full21_generation(
                conn, tenant_id, tmp_path, sources=sources
            )
            base_declarations = [
                row
                for row in prepared.payload.declaration_rows
                if row["station_code"] == "447"
                and row["native_curve_payload_json"] is not None
                and cast("dict[str, object]", row["native_curve_payload_json"])[
                    "points"
                ]
                == curve_points
            ]
            assert len(base_declarations) == 1
            base_declaration = base_declarations[0]
            assert base_declaration["native_curve_payload_json"] == {
                "points": curve_points,
                "interpolation": "linear",
                "rating_type_label": "1",
                "valid_from": "2024-12-31T18:15:00+00:00",
                "valid_to": "2025-05-31T18:15:00+00:00",
            }
            generated_base_id = base_declaration["usable_rating_curve_id"]
            declaration_rows: list[dict[str, object]] = []
            for row in prepared.payload.declaration_rows:
                row = dict(row)
                if row["usable_rating_curve_id"] == generated_base_id:
                    row["usable_rating_curve_id"] = curve.id
                if (
                    copy_equivalence_digest
                    and row["station_code"] == "447"
                    and row["shape_state"] == "raw_usable"
                ):
                    row["function_equivalence_sha256"] = base_declaration[
                        "function_equivalence_sha256"
                    ]
                declaration_rows.append(row)
            # Bind the source-proven base declaration to an existing untagged native curve.
            payload = dataclasses.replace(
                prepared.payload,
                declaration_rows=declaration_rows,
                rating_curve_rows=[
                    row
                    for row in prepared.payload.rating_curve_rows
                    if row["id"] != generated_base_id
                ],
            )
            assert [
                row["attribution_state"]
                for row in payload.attribution_rows
                if row["station_code"] == "447"
            ] == ["held_equal_start_tie"] * 2
            _grant_writer_bootstrap_privileges(conn)
            conn.execute(
                sa.text(
                    "SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer"
                )
            )
            assert conn.execute(sa.text("SELECT session_user, current_user")).one() == (
                "sapphire_dhm21_delivery_writer",
                "sapphire_dhm21_delivery_writer",
            )
            generation_id = prepared.build.id
            run_dhm21_delivery_replacement_transaction(conn, prepared.build, payload)
            publish_generation(
                conn,
                generation_id=generation_id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, generation_id
                ),
                published_at=prepared.build.created_at,
            )
            qc_policy_sha = prepared.policy.policy_sha256
            qc_completion = run_dhm21_qc_transaction(
                conn,
                tenant_id=tenant_id,
                generation_id=generation_id,
                expected_policy=prepared.policy,
                completed_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
            )
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
            assert (
                conn.scalar(
                    sa.text("SELECT delivery_id FROM rating_curves WHERE id=:id"),
                    {"id": curve.id},
                )
                is None
            )
            with (
                pytest.raises(
                    DBAPIError,
                    match="catalog-selected native rating curve is immutable",
                ),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.text("UPDATE rating_curves SET created_at=:at WHERE id=:id"),
                    {"id": curve.id, "at": datetime(2026, 10, 13, tzinfo=UTC)},
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
            document = json.loads(base.content)
            document["nepal21_expired_permission"] = {
                "permission_id": str(permission_id),
                "permission_version": 1,
                "generation_id": str(generation_id),
                "owner_authorization_sha256": "a" * 64,
                "precedence_sha256": "b" * 64,
                "selection_manifest_sha256": "c" * 64,
                "qc_policy_sha256": qc_policy_sha,
                "qc_completion_sha256": qc_completion,
                "selected_rating_curve_id": str(curve.id),
            }
            content_with_permission = canonical_content(document)
            protected = dataclasses.replace(
                base,
                content=content_with_permission,
                fingerprint=content_digest(content_with_permission),
            )
            message = "equal-start non-equivalent declarations"
            with pytest.raises(ValueError, match=message):
                PgProvisionalDischargeStore(conn).store_provisional_discharge(
                    protected, captured_at=NOW
                )
            with pytest.raises(DBAPIError, match=message), conn.begin_nested():
                conn.execute(
                    sa.insert(provisional_discharges).values(
                        **raw_values(protected), captured_at=NOW
                    )
                )
            with pytest.raises(Exception, match=message), conn.begin_nested():
                _copy_provisional_discharge_row(conn, protected)

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
            with pytest.raises(DBAPIError, match="release-bound writer scope"):
                conn.execute(
                    sa.text("UPDATE observations SET value = 9.0 WHERE id = :id"),
                    {"id": observation_id},
                )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("BEGIN"))
            with pytest.raises(DBAPIError, match="release-bound writer scope"):
                conn.execute(
                    sa.text(
                        "UPDATE observations SET qc_status='qc_passed', "
                        "qc_rule_version='policy', qc_flags='[]'::jsonb WHERE id=:id"
                    ),
                    {"id": observation_id},
                )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT value, qc_status FROM observations WHERE id = :id"),
                {"id": observation_id},
            ).one()
            assert row == (1.0, "raw")

    def test_published_generation_update_matrix_allows_only_expected_transitions(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = _tenant(conn)
            prepared = _prepare_full21_generation(conn, TenantId(tenant_id), tmp_path)
            generation_id = prepared.build.id
            run_dhm21_delivery_replacement_transaction(
                conn, prepared.build, prepared.payload
            )
            publish_generation(
                conn,
                generation_id=generation_id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, generation_id
                ),
                published_at=prepared.build.created_at,
            )
            parent_identity_cases = [
                "delivery_id = 'dhm-nepal21-forged'",
                "network = 'forged'",
                "cohort = 'forged'",
                *[
                    f"{column} = (CASE WHEN left({column},1)='0' THEN '1' ELSE '0' END) "
                    f"|| substring({column} from 2)"
                    for column in (
                        "manifest_sha256",
                        "station_set_sha256",
                        "predecessor_semantic_sha256",
                        "preimage_sha256",
                        "immutable_content_sha256",
                    )
                ],
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

    def test_actual_writer_cannot_insert_generation_without_lock_or_active_directly(
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
            conn.execute(
                sa.text(
                    "SELECT set_config('sapphire.dhm21_delivery_write_lock', :marker, true)"
                ),
                {"marker": f"{tenant_id}:{release_id}"},
            )
            with pytest.raises(DBAPIError, match="requires delivery write lock"):
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO restricted_delivery_generations (
                            id, release_id, tenant_id, network, cohort, delivery_id,
                            predecessor_delivery_id, predecessor_semantic_sha256,
                            preimage_sha256, manifest_sha256, station_set_sha256,
                            state, created_at
                        ) VALUES (
                            :id, :release_id, :tenant_id, 'dhm', 'nepal21', :delivery_id,
                            'dhm-barkhk-2026-09-08', :predecessor, :preimage,
                            :manifest, :station_set, 'building', :created_at
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
                        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
            conn.execute(sa.text("ROLLBACK"))
            conn.execute(sa.text("BEGIN"))
            conn.execute(
                sa.text("SELECT sf_lock_dhm21_delivery_write(:tenant_id, :release_id)"),
                {"tenant_id": tenant_id, "release_id": release_id},
            )
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
            conn.execute(
                sa.text("SELECT sf_lock_dhm21_delivery_write(:tenant_id, :release_id)"),
                {"tenant_id": tenant_id, "release_id": release_id},
            )
            with pytest.raises(
                Exception, match="generation insert must start building"
            ):
                driver_connection = cast("Any", conn.connection.driver_connection)
                cursor = driver_connection.cursor()
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


@dataclasses.dataclass(frozen=True, kw_only=True, slots=True)
class _PreparedFull21Generation:
    release: RestrictedReleaseInput
    build: GenerationBuildInput
    payload: RestrictedReplacementPayload
    policy: ExpectedQcPolicyPin
    catalog: SourceCatalog


def _omission_sources(
    manifest_declarations: list[dict[str, object]],
) -> list[DhmSourceFile]:
    code = manifest_declarations[0]["station_code"]
    sources: list[DhmSourceFile] = []
    for item in _sources():
        if item.relative_path == f"RT_{code}.txt":
            for index, declaration in enumerate(manifest_declarations):
                start = date.fromisoformat(str(declaration["from_local_date"]))
                content = (
                    f"Station No:{code}\nData Type: Rating Table\nLeft Column= Stage in m\n"
                    "Right Column= Discharge im m3/s\n"
                    f"Rating Type No ={declaration['rating_type_label']}\n"
                    f"From Date =01-Jan-{start.year}\nTo Date =31-Dec-2099\n"
                    "From Stage =0\n0,3\n1,2\n"
                ).encode()
                sources.append(
                    dataclasses.replace(
                        item,
                        relative_path=f"RT_{code}_{index}.txt",
                        content=content,
                        expected_sha256=sha256_hex_bytes(content),
                    )
                )
        else:
            content = item.content + (b"03/Jan/1991,3\n" if item.kind == "DFL" else b"")
            sources.append(
                dataclasses.replace(
                    item, content=content, expected_sha256=sha256_hex_bytes(content)
                )
            )
    return sources


def _multi_member_447_sources() -> list[DhmSourceFile]:
    sources: list[DhmSourceFile] = []
    for item in _sources():
        content = item.content + (b"03/Jan/1991,3\n" if item.kind == "DFL" else b"")
        source = dataclasses.replace(
            item, content=content, expected_sha256=sha256_hex_bytes(content)
        )
        sources.append(source)
        if item.relative_path in {"RT_447.txt", "DFL_447.txt"}:
            sources.append(
                dataclasses.replace(
                    source, relative_path=f"duplicate/{item.relative_path}"
                )
            )
    return sources


def _selector_447_sources(
    case: Literal["equal-start-tie", "invalid-latest-no-fallback"],
    *,
    older_end: date = date(1991, 1, 1),
) -> list[DhmSourceFile]:
    sources: list[DhmSourceFile] = []
    header = (
        "Station No:447\nData Type: Rating Table\nLeft Column= Stage in m\n"
        "Right Column= Discharge im m3/s\nRating Type No =1\n"
    )
    for item in _sources():
        if item.relative_path == "RT_447.txt":
            older_start = "01-Jan-1990" if case == "equal-start-tie" else "01-Jan-1989"
            old_content = (
                header
                + f"From Date ={older_start}\nTo Date ={older_end.strftime('%d-%b-%Y')}\n"
                "From Stage =0\n0,1\n1,2\n"
            ).encode()
            latest_points = "0,1\n1,3\n" if case == "equal-start-tie" else "0,2\n1,1\n"
            latest_content = (
                header + "From Date =01-Jan-1990\nTo Date =01-Jan-1991\n"
                "From Stage =0\n" + latest_points
            ).encode()
            sources.extend(
                [
                    dataclasses.replace(
                        item,
                        relative_path="RT_447_old.txt",
                        content=old_content,
                        expected_sha256=sha256_hex_bytes(old_content),
                    ),
                    dataclasses.replace(
                        item,
                        relative_path="RT_447_latest.txt",
                        content=latest_content,
                        expected_sha256=sha256_hex_bytes(latest_content),
                    ),
                ]
            )
        else:
            content = item.content
            if item.relative_path == "DFL_447.txt":
                content = b"Daily Flow of Station: 447 in m3/s\n1990\n02/Jan/1990,2\n03/Jan/1990,3\n"
            elif item.kind == "DFL":
                content += b"03/Jan/1991,3\n"
            sources.append(
                dataclasses.replace(
                    item, content=content, expected_sha256=sha256_hex_bytes(content)
                )
            )
    return sources


def _duplicate_447_sources() -> list[DhmSourceFile]:
    sources: list[DhmSourceFile] = []
    for item in _sources():
        content = item.content
        if item.relative_path == "RT_447.txt":
            content = content.replace(b"0,1\n", b"0,1\n0,1\n").replace(
                b"To Date =31-Dec-1991", b"To Date =01-Jan-1991"
            )
        elif item.relative_path == "DFL_447.txt":
            content = b"Daily Flow of Station: 447 in m3/s\n1990\n02/Jan/1990,2\n03/Jan/1990,3\n"
        elif item.kind == "DFL":
            content += b"03/Jan/1991,3\n"
        sources.append(
            dataclasses.replace(
                item, content=content, expected_sha256=sha256_hex_bytes(content)
            )
        )
    return sources


def _prepare_full21_generation(
    conn: sa.Connection,
    tenant_id: TenantId,
    preimage_dir: Path,
    *,
    sources: Sequence[DhmSourceFile] | None = None,
    canonicalization: CanonicalizationPolicy | None = None,
    catalog: SourceCatalog | None = None,
) -> _PreparedFull21Generation:
    if sources is None:
        sources = [
            dataclasses.replace(
                item,
                content=item.content + b"03/Jan/1991,3\n",
                expected_sha256=sha256_hex_bytes(item.content + b"03/Jan/1991,3\n"),
            )
            if item.kind == "DFL"
            else item
            for item in _sources()
        ]
    if catalog is None:
        catalog = build_nepal21_source_catalog(sources, policy=canonicalization)
    bindings: list[SourceStationBinding] = []
    for code in sorted({record.station_code for record in catalog.daily_records}):
        station_id = conn.scalar(
            sa.text(
                "SELECT id FROM stations WHERE tenant_id=:tenant AND network='dhm' AND code=:code"
            ),
            {"tenant": tenant_id, "code": code},
        )
        if station_id is None:
            station_id = _station(conn, tenant_id, code=code)
        surviving_version = conn.scalar(
            sa.text(
                "SELECT COALESCE(max(version),0) FROM rating_curves WHERE station_id=:station "
                "AND delivery_id IS DISTINCT FROM 'dhm-barkhk-2026-09-08'"
            ),
            {"station": station_id},
        )
        bindings.append(
            SourceStationBinding(
                station_code=code,
                station_id=StationId(station_id),
                tenant_id=tenant_id,
                highest_surviving_version=int(surviving_version or 0),
            )
        )
    now = ensure_utc(datetime(2026, 10, 12, tzinfo=UTC))
    generation_id = uuid.uuid4()
    predecessor = fetch_predecessor_semantic_rows(conn, tenant_id)
    predecessor_sha = predecessor_semantic_digest(predecessor)
    preimage = canonical_json_bytes(
        {
            "codec": "dhm21-preimage-v1",
            "generation_id": str(generation_id),
            "predecessor_delivery_id": "dhm-barkhk-2026-09-08",
            "rows": [_normalize_predecessor_row(row) for row in predecessor],
        }
    )
    release = RestrictedReleaseInput(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        manifest_catalog=catalog.manifest,
        station_set_sha256=station_set_sha256(),
        expected_predecessor_semantic_sha256=predecessor_sha,
        canonicalization_policy=canonicalization.witness if canonicalization else None,
        created_at=now,
    )
    insert_restricted_release(conn, release)
    policy = dataclasses.replace(
        _synthetic_qc_pin(bindings[0].station_id),
        station_networks={binding.station_id: "dhm" for binding in bindings},
    )
    policy = dataclasses.replace(
        policy, policy_sha256=sha256_hex_bytes(policy.content_bytes)
    )
    _insert_qc_policy_witness(conn, release.id, content=policy.content_bytes)
    build = GenerationBuildInput(
        id=generation_id,
        release_id=release.id,
        tenant_id=tenant_id,
        delivery_id=release.allowed_new_delivery_id,
        predecessor_semantic_sha256=predecessor_sha,
        preimage_sha256=sha256_hex_bytes(preimage),
        manifest_sha256=catalog.manifest.sha256,
        station_set_sha256=station_set_sha256(),
        canonicalization_policy_sha256=canonicalization.witness.sha256
        if canonicalization
        else None,
        created_at=now,
    )
    ids = count()
    payload = build_nepal21_replacement_payload(
        catalog,
        build,
        bindings,
        preimage_dir=preimage_dir,
        id_factory=lambda: uuid.uuid5(generation_id, str(next(ids))),
    )
    return _PreparedFull21Generation(
        release=release,
        build=build,
        payload=payload,
        policy=policy,
        catalog=catalog,
    )


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


def _synthetic_qc_pin(station_id: uuid.UUID) -> ExpectedQcPolicyPin:
    policy = ExpectedQcPolicyPin(
        policy_sha256="0" * 64,
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
    return dataclasses.replace(
        policy, policy_sha256=sha256_hex_bytes(policy.content_bytes)
    )


def _insert_qc_policy_witness(
    conn: sa.Connection, release_id: uuid.UUID, *, content: bytes | None = None
) -> str:
    if content is None:
        station_ids = (
            conn.execute(
                sa.text(
                    "SELECT s.id FROM stations s JOIN restricted_delivery_releases r ON r.tenant_id=s.tenant_id WHERE r.id=:release AND s.network='dhm' ORDER BY s.id"
                ),
                {"release": release_id},
            )
            .scalars()
            .all()
        )
        assert station_ids
        pin = dataclasses.replace(
            _synthetic_qc_pin(station_ids[0]),
            station_networks={
                StationId(station_id): "dhm" for station_id in station_ids
            },
        )
        content = pin.content_bytes
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
    source_content: bytes | None = None,
    source_path: str | None = None,
) -> str:
    origin_id = uuid.uuid4()
    origin_kind = "DFL" if set_kind == "DFL_RECORD" else "RT"
    relative_path = source_path or (
        "DFL_447.txt" if origin_kind == "DFL" else "RT_350.txt"
    )
    file_sha = (
        sha256_hex_bytes(source_content) if source_content is not None else "7" * 64
    )
    resolved = origin_set_sha or origin_identity_set_sha256(
        [
            {
                "member_ordinal": 0,
                "origin_id": origin_id,
                "origin_kind": origin_kind,
                "source_root_label": "synthetic_test",
                "relative_path": relative_path,
                "file_sha256": file_sha,
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
            "file_sha": file_sha,
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


def _insert_prepared_payload_direct_sql(
    conn: sa.Connection,
    prepared: _PreparedFull21Generation,
    payload: RestrictedReplacementPayload,
) -> None:
    # Test the SQL boundary independently of the service's earlier catalog check.
    run_dhm21_delivery_replacement_transaction(conn, prepared.build)
    for table, rows in (
        (db_schema.rating_curves, payload.rating_curve_rows),
        (db_schema.observations, payload.observation_rows),
        (db_schema.dhm_delivery_origins, payload.origin_rows),
        (db_schema.dhm_origin_sets, payload.origin_set_rows),
        (db_schema.dhm_origin_set_members, payload.origin_member_rows),
        (db_schema.dhm_rating_declarations, payload.declaration_rows),
        (db_schema.dhm_delivery_observation_attributions, payload.attribution_rows),
    ):
        if rows:
            conn.execute(sa.insert(table), [dict(row) for row in rows])


def _assert_direct_publication_refused_and_rollback(
    conn: sa.Connection, generation_id: uuid.UUID, message: str
) -> None:
    with pytest.raises(DBAPIError, match=message), conn.begin_nested():
        _publish_direct_sql(conn, generation_id)
    assert conn.execute(
        sa.text(
            "SELECT state,published_at FROM restricted_delivery_generations WHERE id=:id"
        ),
        {"id": generation_id},
    ).one() == ("building", None)
    conn.rollback()
    assert (
        conn.scalar(
            sa.text(
                "SELECT count(*) FROM restricted_delivery_generations WHERE id=:id"
            ),
            {"id": generation_id},
        )
        == 0
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
            SET state='active_pending_qc', immutable_content_sha256=public.sf_dhm21_content_digest(id), published_at=:published
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


class TestNepal21TransactionPreconditions:
    @pytest.mark.parametrize("mode", ["engine", "connection"])
    @pytest.mark.parametrize("operation", ["store", "enable", "disable"])
    def test_autocommit_refused_before_sql(
        self, migration_engine: tuple[sa.Engine, str], mode: str, operation: str
    ) -> None:
        from sapphire_flow.services.nepal21_delivery_authority import (
            disable_expired_rating_test_permission,
            enable_expired_rating_test_permission,
        )
        from tests.unit.services.test_provisional_discharge import inputs

        engine, _ = migration_engine
        candidate = (
            sa.create_engine(engine.url, isolation_level="AUTOCOMMIT")
            if mode == "engine"
            else engine
        )
        try:
            with candidate.connect() as conn:
                if mode == "connection":
                    conn = conn.execution_options(isolation_level="AUTOCOMMIT")
                with conn.begin(), pytest.raises(RuntimeError, match="autocommit mode"):
                    if operation == "store":
                        PgProvisionalDischargeStore(conn).store_provisional_discharge(
                            convert(*inputs()),
                            captured_at=NOW,
                        )
                    elif operation == "enable":
                        enable_expired_rating_test_permission(
                            conn,
                            tenant_id=TenantId(uuid.uuid4()),
                            generation_id=uuid.uuid4(),
                            owner_authorization_sha256="1" * 64,
                            precedence_sha256="2" * 64,
                            selection_manifest_sha256="3" * 64,
                            enabled_at=NOW,
                        )
                    else:
                        disable_expired_rating_test_permission(
                            conn,
                            permission_id=uuid.uuid4(),
                            disabled_at=NOW,
                        )
        finally:
            if mode == "engine":
                candidate.dispose()

    def test_static_helper_refuses_second_tenant_before_waiting(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            first_id = _tenant_with_code(conn, "first-static-tenant")
            second_id = _tenant_with_code(conn, "second-static-tenant")
            conn.execute(
                sa.text("SELECT sf_lock_nepal_static_guard(:id)"), {"id": first_id}
            )
            conn.execute(sa.text("SET LOCAL lock_timeout = '250ms'"))
            with pytest.raises(DBAPIError, match="one tenant per transaction"):
                conn.execute(
                    sa.text("SELECT sf_lock_nepal_static_guard(:id)"), {"id": second_id}
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
                FOREACH fn IN ARRAY ARRAY['public.sf_dhm21_content_digest(uuid)',
                             'public.sf_dhm21_qc_completion_digest(uuid,text,timestamptz,uuid)',
                             'public.sf_lock_dhm21_delivery_write(uuid,uuid)',
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
            conn.execute(
                sa.text("SELECT sf_lock_dhm21_delivery_write(:tenant_id, :release_id)"),
                {"tenant_id": tenant_id, "release_id": release_id},
            )
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
                    SET state='active_pending_qc', immutable_content_sha256=public.sf_dhm21_content_digest(id), published_at=:published
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
        tmp_path: Path,
        label: str,
        manifest_declarations: list[dict[str, object]],
        persisted_indexes: list[int],
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        sources = _omission_sources(manifest_declarations)
        code = manifest_declarations[0]["station_code"]
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path, sources=sources
            )
            candidates = sorted(
                (
                    row
                    for row in prepared.payload.declaration_rows
                    if row["station_code"] == code
                ),
                key=lambda row: (row["from_local_date"], row["rating_type_label"]),
            )
            assert len(candidates) == 2
            kept_ids = {candidates[index]["id"] for index in persisted_indexes}
            payload = dataclasses.replace(
                prepared.payload,
                declaration_rows=[
                    row
                    for row in prepared.payload.declaration_rows
                    if row["station_code"] != code or row["id"] in kept_ids
                ],
            )
            assert label
            assert all(
                row["rating_declaration_id"] is None
                for row in payload.attribution_rows
                if row["station_code"] == code
            )
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn,
                prepared.build.id,
                "persisted source occurrences differ from authorized parsed records",
            )

    def test_direct_sql_publication_refuses_same_count_raw_substitution(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path
            )
            declaration_rows = copy.deepcopy(prepared.payload.declaration_rows)
            cast("dict[str, Any]", declaration_rows[0]["raw_declaration_payload_json"])[
                "points"
            ][0]["discharge_m3s"] = 99.0
            payload = dataclasses.replace(
                prepared.payload, declaration_rows=declaration_rows
            )
            assert (
                len(payload.declaration_rows)
                == len(prepared.payload.declaration_rows)
                == 21
            )
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn,
                prepared.build.id,
                "persisted source occurrences differ from authorized parsed records",
            )

    def test_direct_sql_publication_refuses_declaration_digest_only_substitution(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path
            )
            original = prepared.payload.declaration_rows[0]
            digest = cast("str", original["declaration_content_sha256"])
            changed = {
                **original,
                "declaration_content_sha256": ("1" if digest[0] == "0" else "0")
                + digest[1:],
            }
            assert (
                changed["raw_declaration_payload_json"]
                == original["raw_declaration_payload_json"]
            )
            payload = dataclasses.replace(
                prepared.payload,
                declaration_rows=[changed, *prepared.payload.declaration_rows[1:]],
            )
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn, prepared.build.id, "manifest declaration set mismatch"
            )

    def test_direct_sql_publication_refuses_origin_member_mismatch(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path
            )
            old_digest = cast(
                "str", prepared.payload.declaration_rows[0]["origin_set_sha256"]
            )
            bad_digest = ("1" if old_digest[0] == "0" else "0") + old_digest[1:]
            assert bad_digest not in {
                row["origin_set_sha256"] for row in prepared.payload.origin_set_rows
            }
            changes = {
                field: [
                    {**row, "origin_set_sha256": bad_digest}
                    if row["origin_set_sha256"] == old_digest
                    else row
                    for row in cast(
                        "Sequence[Mapping[str, object]]",
                        getattr(prepared.payload, field),
                    )
                ]
                for field in (
                    "origin_set_rows",
                    "origin_member_rows",
                    "declaration_rows",
                    "attribution_rows",
                )
            }
            payload = dataclasses.replace(prepared.payload, **changes)
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn, prepared.build.id, "origin set membership mismatch"
            )

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
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path
            )
            _insert_prepared_payload_direct_sql(conn, prepared, prepared.payload)
            _publish_direct_sql(conn, prepared.build.id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": prepared.build.id},
                )
                == "active_pending_qc"
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id=:delivery"
                    ),
                    {"delivery": prepared.build.delivery_id},
                )
                == 42
            )

    @pytest.mark.parametrize(
        "start_day,end_day",
        [("01-Jan-1990", "01-Jan-1991"), ("01-Jan-1986", "02-Jan-1986")],
    )
    def test_publication_accepts_structural_raw_effective_native_with_separate_rt_dfl_origins(
        self,
        migration_engine: tuple[sa.Engine, str],
        tmp_path: Path,
        start_day: str,
        end_day: str,
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        from sapphire_flow.adapters.dhm_files import (
            parse_daily_flow,
            parse_rating_tables,
        )
        from sapphire_flow.services.nepal21_delivery_authority import (
            declaration_digest,
            function_payload,
        )

        rt_text = (
            "Station No:447\nData Type: Rating Table\nLeft Column= Stage in m\n"
            "Right Column= Discharge im m3/s\nRating Type No =1\n"
            f"From Date ={start_day}\nTo Date ={end_day}\nFrom Stage =0\n0,1\n1,2\n"
        )
        parsed_rt = parse_rating_tables(rt_text)
        block = parsed_rt.blocks[0]
        dfl_text = (
            f"Daily Flow of Station: 447 in m3/s\n{block.from_date.year}\n"
            f"{block.from_date.strftime('%d/%b/%Y')},2\n"
        )
        parsed_dfl = parse_daily_flow(dfl_text)
        raw_payload = _raw_rating_payload(
            "447",
            from_date=block.from_date.isoformat(),
            to_date=block.to_date.isoformat(),
        )
        assert sha256_hex_bytes(
            canonical_json_bytes(raw_payload)
        ) == declaration_digest("447", block)
        function = function_payload("447", block)
        assert function is not None
        declaration = _decl(
            "447",
            declaration_digest("447", block),
            raw_payload,
            from_date=block.from_date.isoformat(),
            shape="raw_usable",
        )
        sources: list[DhmSourceFile] = []
        for item in _sources():
            content = item.content
            if item.relative_path == "RT_447.txt":
                content = rt_text.encode()
            elif item.relative_path == "DFL_447.txt":
                following_day = parsed_dfl.values[0].day + timedelta(days=1)
                content = (
                    dfl_text + f"{following_day.strftime('%d/%b/%Y')},3\n"
                ).encode()
            elif item.kind == "DFL":
                content += b"03/Jan/1991,3\n"
            sources.append(
                dataclasses.replace(
                    item, content=content, expected_sha256=sha256_hex_bytes(content)
                )
            )
        expected_from, expected_to = {
            "01-Jan-1990": (
                datetime(1989, 12, 31, 18, 15, tzinfo=UTC),
                datetime(1991, 1, 1, 18, 15, tzinfo=UTC),
            ),
            "01-Jan-1986": (
                datetime(1985, 12, 31, 18, 30, tzinfo=UTC),
                datetime(1986, 1, 2, 18, 15, tzinfo=UTC),
            ),
        }[start_day]
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            prepared = _prepare_full21_generation(
                conn, tenant_id, tmp_path, sources=sources
            )
            generation_id = prepared.build.id
            selected = next(
                row
                for row in prepared.payload.declaration_rows
                if row["station_code"] == "447"
            )
            assert selected["raw_declaration_payload_json"] == raw_payload
            assert (
                selected["declaration_content_sha256"]
                == declaration["declaration_content_sha256"]
            )
            assert selected["function_payload_json"] == function
            curve = next(
                row
                for row in prepared.payload.rating_curve_rows
                if row["id"] == selected["usable_rating_curve_id"]
            )
            assert (curve["valid_from"], curve["valid_to"]) == (
                expected_from,
                expected_to,
            )
            target_rows = sorted(
                (
                    row
                    for row in prepared.payload.observation_rows
                    if row["station_id"] == curve["station_id"]
                ),
                key=lambda row: cast("datetime", row["timestamp"]),
            )
            assert target_rows[0]["timestamp"] == expected_from
            assert [row["value"] for row in target_rows] == [2.0, 3.0]
            dfl_sets = {
                row["origin_set_sha256"]
                for row in prepared.payload.attribution_rows
                if row["station_code"] == "447"
            }
            assert selected["origin_set_sha256"] not in dfl_sets
            _grant_writer_bootstrap_privileges(conn)
            conn.execute(
                sa.text(
                    "SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer"
                )
            )
            assert conn.execute(sa.text("SELECT session_user,current_user")).one() == (
                "sapphire_dhm21_delivery_writer",
                "sapphire_dhm21_delivery_writer",
            )
            _insert_prepared_payload_direct_sql(conn, prepared, prepared.payload)
            with (
                pytest.raises(DBAPIError, match="immutable content digest"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.text(
                        "UPDATE restricted_delivery_generations SET state='active_pending_qc', "
                        "immutable_content_sha256=:digest, published_at=:at WHERE id=:id"
                    ),
                    {
                        "id": generation_id,
                        "digest": "0" * 64,
                        "at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
            with (
                pytest.raises(DBAPIError, match="generation identity is immutable"),
                conn.begin_nested(),
            ):
                conn.execute(
                    sa.text(
                        "UPDATE restricted_delivery_generations SET state='active_pending_qc', "
                        "preimage_sha256=:forged, immutable_content_sha256=sf_dhm21_content_digest(id), "
                        "published_at=:at WHERE id=:id"
                    ),
                    {
                        "id": generation_id,
                        "forged": (
                            "1" if prepared.build.preimage_sha256[0] == "0" else "0"
                        )
                        + prepared.build.preimage_sha256[1:],
                        "at": datetime(2026, 10, 12, tzinfo=UTC),
                    },
                )
            publish_generation(
                conn,
                generation_id=generation_id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, generation_id
                ),
                published_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )
            conn.execute(sa.text("RESET SESSION AUTHORIZATION"))

    def test_publication_accepts_canonicalized_duplicate_removal_with_policy_witness(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        sources = _duplicate_447_sources()
        policy = _synthetic_approval(sources, "447")
        with engine.begin() as conn:
            prepared = _prepare_full21_generation(
                conn,
                TenantId(_tenant(conn)),
                tmp_path,
                sources=sources,
                canonicalization=policy,
            )
            declaration = next(
                row
                for row in prepared.payload.declaration_rows
                if row["station_code"] == "447"
            )
            assert declaration["shape_state"] == "canonicalized_usable"
            assert cast(
                "dict[str, object]", declaration["raw_declaration_payload_json"]
            )["points"] == [
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 0.0, "discharge_m3s": 1.0},
                {"stage_m": 1.0, "discharge_m3s": 2.0},
            ]
            assert declaration["function_payload_json"] == _effective_payload("447")
            assert declaration["native_curve_payload_json"] == _native_payload(
                valid_from="1989-12-31T18:15:00+00:00",
                valid_to="1991-01-01T18:15:00+00:00",
            )
            assert (
                declaration["canonicalization_approval_id"]
                == policy.approvals[0].approval_id
            )
            assert (
                declaration["canonicalization_approval_sha256"]
                == policy.approvals[0].approval_sha256
            )
            assert [
                row["attribution_state"]
                for row in prepared.payload.attribution_rows
                if row["station_code"] == "447"
            ] == ["selected", "selected"]
            _insert_prepared_payload_direct_sql(conn, prepared, prepared.payload)
            _publish_direct_sql(conn, prepared.build.id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": prepared.build.id},
                )
                == "active_pending_qc"
            )

    def test_publication_refuses_forged_raw_effective_shape_derivation(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        sources = _duplicate_447_sources()
        policy = _synthetic_approval(sources, "447")
        catalog = build_nepal21_source_catalog(sources, policy=policy)
        manifest = json.loads(catalog.manifest.content)
        for declaration in manifest["rating_declarations"]:
            if declaration["station_code"] == "447":
                declaration["shape_state"] = "raw_usable"
        # Deliberately authorize a wrong derived shape, never change parsed source records.
        forged_catalog = dataclasses.replace(
            catalog,
            manifest=WitnessBytes(
                kind="manifest_catalog", content=canonical_json_bytes(manifest)
            ),
        )
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn,
                TenantId(_tenant(conn)),
                tmp_path,
                catalog=forged_catalog,
                canonicalization=policy,
            )
            payload = dataclasses.replace(
                prepared.payload,
                declaration_rows=[
                    {
                        **row,
                        "shape_state": "raw_usable",
                        "canonicalization_approval_id": None,
                        "canonicalization_approval_sha256": None,
                    }
                    if row["station_code"] == "447"
                    else row
                    for row in prepared.payload.declaration_rows
                ],
            )
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn, prepared.build.id, "raw effective declaration derivation mismatch"
            )

    def test_publication_refuses_canonicalized_declaration_without_policy_witness(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        sources = _duplicate_447_sources()
        policy = _synthetic_approval(sources, "447")
        catalog = build_nepal21_source_catalog(sources, policy=policy)
        unapproved_catalog = dataclasses.replace(catalog, canonicalization_policy=None)
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path, catalog=unapproved_catalog
            )
            assert prepared.build.canonicalization_policy_sha256 is None
            _insert_prepared_payload_direct_sql(conn, prepared, prepared.payload)
            _assert_direct_publication_refused_and_rollback(
                conn, prepared.build.id, "canonicalization policy witness is missing"
            )

    def test_publication_refuses_canonicalization_policy_wrong_approval_scope(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        sources = _duplicate_447_sources()
        policy = _synthetic_approval(sources, "447")
        approval_sha = policy.approvals[0].approval_sha256
        wrong_sha = ("1" if approval_sha[0] == "0" else "0") + approval_sha[1:]
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn,
                TenantId(_tenant(conn)),
                tmp_path,
                sources=sources,
                canonicalization=policy,
            )
            payload = dataclasses.replace(
                prepared.payload,
                declaration_rows=[
                    {**row, "canonicalization_approval_sha256": wrong_sha}
                    if row["station_code"] == "447"
                    else row
                    for row in prepared.payload.declaration_rows
                ],
            )
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn,
                prepared.build.id,
                "canonicalization policy witness scope mismatch",
            )

    def test_publication_refuses_native_payload_that_differs_from_actual_curve(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path
            )
            rows = copy.deepcopy(prepared.payload.declaration_rows)
            cast("dict[str, object]", rows[0]["native_curve_payload_json"])[
                "points"
            ] = [{"water_level": 0.0, "discharge": 99.0}]
            payload = dataclasses.replace(prepared.payload, declaration_rows=rows)
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn,
                prepared.build.id,
                "native curve payload does not match actual rating curve",
            )

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
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            prepared = _prepare_full21_generation(conn, tenant_id, tmp_path)
            generation_id = prepared.build.id
            _insert_prepared_payload_direct_sql(conn, prepared, prepared.payload)
            _publish_direct_sql(conn, generation_id)
            with (
                pytest.raises(
                    DBAPIError,
                    match="catalog rows may only be inserted while generation is building",
                ),
                conn.begin_nested(),
            ):
                _insert_origin_set(
                    conn,
                    generation_id=generation_id,
                    tenant_id=tenant_id,
                    set_kind="DFL_RECORD",
                    payload_sha="9" * 64,
                )

            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM dhm_delivery_origins WHERE generation_id=:id"
                    ),
                    {"id": generation_id},
                )
                == 63
            )

    def test_postpublication_catalog_child_copy_is_refused(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            tenant_id = TenantId(_tenant(conn))
            prepared = _prepare_full21_generation(conn, tenant_id, tmp_path)
            generation_id = prepared.build.id
            _insert_prepared_payload_direct_sql(conn, prepared, prepared.payload)
            _publish_direct_sql(conn, generation_id)
            declaration = next(
                row
                for row in prepared.payload.declaration_rows
                if row["station_code"] == "350"
            )
            station_id = declaration["station_id"]
            origin_set = declaration["origin_set_sha256"]
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

            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": generation_id},
                )
                == "active_pending_qc"
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM dhm_delivery_origins WHERE generation_id=:id"
                    ),
                    {"id": generation_id},
                )
                == 63
            )

    def test_publication_refuses_selected_old_curve_when_latest_declaration_is_invalid(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        sources = _selector_447_sources(
            "invalid-latest-no-fallback", older_end=date(2099, 12, 31)
        )
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path, sources=sources
            )
            older = next(
                row
                for row in prepared.payload.declaration_rows
                if row["station_code"] == "447" and row["shape_state"] == "raw_usable"
            )
            assert older["from_local_date"] == date(1989, 1, 1)
            assert [
                row["attribution_state"]
                for row in prepared.payload.attribution_rows
                if row["station_code"] == "447"
            ] == ["held_invalid_latest"] * 2
            target_ids = {
                row["observation_id"]
                for row in prepared.payload.attribution_rows
                if row["station_code"] == "447"
            }
            payload = dataclasses.replace(
                prepared.payload,
                attribution_rows=[
                    {
                        **row,
                        "attribution_state": "selected",
                        "rating_declaration_id": older["id"],
                        "rating_curve_id": older["usable_rating_curve_id"],
                    }
                    if row["station_code"] == "447"
                    else row
                    for row in prepared.payload.attribution_rows
                ],
                observation_rows=[
                    {
                        **row,
                        "rating_curve_id": older["usable_rating_curve_id"],
                        "rating_curve_correction_version": older["rating_type_label"],
                    }
                    if row["id"] in target_ids
                    else row
                    for row in prepared.payload.observation_rows
                ],
            )
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn,
                prepared.build.id,
                "historical rating attribution selector mismatch",
            )

    @pytest.mark.parametrize(
        "case,state",
        [
            ("equal-start-tie", "held_equal_start_tie"),
            ("invalid-latest-no-fallback", "held_invalid_latest"),
        ],
    )
    def test_publication_accepts_historical_held_selector_states(
        self,
        migration_engine: tuple[sa.Engine, str],
        tmp_path: Path,
        case: Literal["equal-start-tie", "invalid-latest-no-fallback"],
        state: str,
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            prepared = _prepare_full21_generation(
                conn,
                TenantId(_tenant(conn)),
                tmp_path,
                sources=_selector_447_sources(case),
            )
            held = [
                row
                for row in prepared.payload.attribution_rows
                if row["station_code"] == "447"
            ]
            assert [row["attribution_state"] for row in held] == [state, state]
            assert all(
                row["rating_curve_id"] is None and row["rating_declaration_id"] is None
                for row in held
            )
            _insert_prepared_payload_direct_sql(conn, prepared, prepared.payload)
            _publish_direct_sql(conn, prepared.build.id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": prepared.build.id},
                )
                == "active_pending_qc"
            )
            assert (
                conn.execute(
                    sa.text(
                        "SELECT attribution_state, rating_declaration_id, rating_curve_id "
                        "FROM dhm_delivery_observation_attributions "
                        "WHERE generation_id=:id AND station_code='447' ORDER BY local_date"
                    ),
                    {"id": prepared.build.id},
                ).all()
                == [(state, None, None)] * 2
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
    def test_publication_refuses_source_identity_tampering(
        self,
        migration_engine: tuple[sa.Engine, str],
        tmp_path: Path,
        override_key: str,
        override_value: object,
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn, TenantId(_tenant(conn)), tmp_path
            )
            original = next(
                row
                for row in prepared.payload.origin_rows
                if row["relative_path"] == "RT_350.txt"
            )
            column = "id" if override_key == "origin_id" else override_key
            assert original[column] != override_value
            origins = [
                {**row, column: override_value} if row["id"] == original["id"] else row
                for row in prepared.payload.origin_rows
            ]
            members = [
                {**row, "origin_id": override_value}
                if override_key == "origin_id" and row["origin_id"] == original["id"]
                else row
                for row in prepared.payload.origin_member_rows
            ]
            payload = dataclasses.replace(
                prepared.payload, origin_rows=origins, origin_member_rows=members
            )
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            message = (
                "origin set membership mismatch"
                if override_key == "origin_id"
                else "persisted source occurrences differ from authorized parsed records"
            )
            _assert_direct_publication_refused_and_rollback(
                conn, prepared.build.id, message
            )

    def test_publication_accepts_complete_rt_and_dfl_multi_member_origin_rosters(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.begin() as conn:
            prepared = _prepare_full21_generation(
                conn,
                TenantId(_tenant(conn)),
                tmp_path,
                sources=_multi_member_447_sources(),
            )
            target_sets = {
                row["origin_set_sha256"]
                for row in prepared.payload.declaration_rows
                if row["station_code"] == "447"
            } | {
                row["origin_set_sha256"]
                for row in prepared.payload.attribution_rows
                if row["station_code"] == "447"
            }
            assert len(target_sets) == 3
            assert [
                (row["set_kind"], row["member_count"])
                for row in prepared.payload.origin_set_rows
                if row["origin_set_sha256"] in target_sets
            ].count(("RT_DECLARATION", 2)) == 1
            assert [
                (row["set_kind"], row["member_count"])
                for row in prepared.payload.origin_set_rows
                if row["origin_set_sha256"] in target_sets
            ].count(("DFL_RECORD", 2)) == 2
            assert len(prepared.payload.observation_rows) == 42
            assert len(prepared.payload.origin_rows) == 66
            _insert_prepared_payload_direct_sql(conn, prepared, prepared.payload)
            _publish_direct_sql(conn, prepared.build.id)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": prepared.build.id},
                )
                == "active_pending_qc"
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM dhm_delivery_origins WHERE generation_id=:id"
                    ),
                    {"id": prepared.build.id},
                )
                == 66
            )

    @pytest.mark.parametrize("tamper", ["source_root", "member_order"])
    def test_publication_refuses_multi_member_identity_tampering(
        self,
        migration_engine: tuple[sa.Engine, str],
        tmp_path: Path,
        tamper: Literal["source_root", "member_order"],
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        with engine.connect() as conn:
            prepared = _prepare_full21_generation(
                conn,
                TenantId(_tenant(conn)),
                tmp_path,
                sources=_multi_member_447_sources(),
            )
            target_set = next(
                row["origin_set_sha256"]
                for row in prepared.payload.declaration_rows
                if row["station_code"] == "447"
            )
            members = [dict(row) for row in prepared.payload.origin_member_rows]
            selected_members = [
                row for row in members if row["origin_set_sha256"] == target_set
            ]
            assert len(selected_members) == 2
            origins = [dict(row) for row in prepared.payload.origin_rows]
            if tamper == "source_root":
                target_id = selected_members[1]["origin_id"]
                for row in origins:
                    if row["id"] == target_id:
                        row["source_root_label"] = "old"
                message = (
                    "persisted source occurrences differ from authorized parsed records"
                )
            else:
                for row in selected_members:
                    row["member_ordinal"] = 1 - cast("int", row["member_ordinal"])
                message = "origin set membership mismatch"
            payload = dataclasses.replace(
                prepared.payload, origin_rows=origins, origin_member_rows=members
            )
            _insert_prepared_payload_direct_sql(conn, prepared, payload)
            _assert_direct_publication_refused_and_rollback(
                conn, prepared.build.id, message
            )


def _prepare_owner_full21_release(
    engine: sa.Engine,
    preimage_dir: Path,
    *,
    sources: Sequence[DhmSourceFile] | None = None,
) -> _PreparedFull21Generation:
    with engine.begin() as conn:
        prepared = _prepare_full21_generation(
            conn, TenantId(_tenant(conn)), preimage_dir, sources=sources
        )
        _grant_writer_bootstrap_privileges(conn)
    return prepared


def _insert_prepared_as_writer(
    conn: sa.Connection,
    prepared: _PreparedFull21Generation,
    payload: RestrictedReplacementPayload,
) -> None:
    conn.execute(
        sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
    )
    assert conn.execute(sa.text("SELECT session_user,current_user")).one() == (
        "sapphire_dhm21_delivery_writer",
        "sapphire_dhm21_delivery_writer",
    )
    _insert_prepared_payload_direct_sql(conn, prepared, payload)


def _assert_writer_publication_refused(
    engine: sa.Engine,
    prepared: _PreparedFull21Generation,
    payload: RestrictedReplacementPayload,
    message: str,
) -> None:
    with engine.connect() as conn:
        _insert_prepared_as_writer(conn, prepared, payload)
        with pytest.raises(DBAPIError, match=message), conn.begin_nested():
            publish_generation(
                conn,
                generation_id=prepared.build.id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, prepared.build.id
                ),
                published_at=prepared.build.created_at,
            )
        assert conn.execute(
            sa.text(
                "SELECT state,published_at FROM restricted_delivery_generations WHERE id=:id"
            ),
            {"id": prepared.build.id},
        ).one() == ("building", None)
        conn.rollback()
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM restricted_delivery_generations WHERE id=:id"
                ),
                {"id": prepared.build.id},
            )
            == 0
        )
        assert conn.execute(
            sa.text(
                "SELECT status,used_at FROM restricted_delivery_releases WHERE id=:id"
            ),
            {"id": prepared.build.release_id},
        ).one() == ("authorized", None)


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
                "persisted source occurrences differ from authorized parsed records",
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
                "persisted source occurrences differ from authorized parsed records",
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
                "persisted source occurrences differ from authorized parsed records",
            ),
        ],
    )
    def test_actual_session_user_refuses_manifest_declaration_omissions(
        self,
        migration_engine: tuple[sa.Engine, str],
        tmp_path: Path,
        manifest_declarations: list[dict[str, object]],
        persisted_indexes: list[int],
        expected: str,
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        prepared = _prepare_owner_full21_release(
            engine, tmp_path, sources=_omission_sources(manifest_declarations)
        )
        code = manifest_declarations[0]["station_code"]
        candidates = sorted(
            (
                row
                for row in prepared.payload.declaration_rows
                if row["station_code"] == code
            ),
            key=lambda row: (row["from_local_date"], row["rating_type_label"]),
        )
        assert len(candidates) == 2
        kept_ids = {candidates[index]["id"] for index in persisted_indexes}
        payload = dataclasses.replace(
            prepared.payload,
            declaration_rows=[
                row
                for row in prepared.payload.declaration_rows
                if row["station_code"] != code or row["id"] in kept_ids
            ],
        )
        _assert_writer_publication_refused(engine, prepared, payload, expected)

    def test_actual_session_user_refuses_same_count_raw_substitution(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        prepared = _prepare_owner_full21_release(engine, tmp_path)
        rows = copy.deepcopy(prepared.payload.declaration_rows)
        cast("dict[str, Any]", rows[0]["raw_declaration_payload_json"])["points"][0][
            "discharge_m3s"
        ] = 99.0
        payload = dataclasses.replace(prepared.payload, declaration_rows=rows)
        assert len(payload.declaration_rows) == 21
        _assert_writer_publication_refused(
            engine,
            prepared,
            payload,
            "persisted source occurrences differ from authorized parsed records",
        )

    def test_actual_session_user_refuses_origin_member_mismatch(
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        prepared = _prepare_owner_full21_release(engine, tmp_path)
        original = prepared.payload.origin_rows[0]["id"]
        changed = uuid.uuid4()
        payload = dataclasses.replace(
            prepared.payload,
            origin_rows=[
                {**row, "id": changed} if row["id"] == original else row
                for row in prepared.payload.origin_rows
            ],
            origin_member_rows=[
                {**row, "origin_id": changed} if row["origin_id"] == original else row
                for row in prepared.payload.origin_member_rows
            ],
        )
        _assert_writer_publication_refused(
            engine, prepared, payload, "origin set membership mismatch"
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
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        prepared = _prepare_owner_full21_release(engine, tmp_path)
        with engine.begin() as conn:
            _insert_prepared_as_writer(conn, prepared, prepared.payload)
            publish_generation(
                conn,
                generation_id=prepared.build.id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, prepared.build.id
                ),
                published_at=prepared.build.created_at,
            )
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                    ),
                    {"id": prepared.build.id},
                )
                == "active_pending_qc"
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM observations WHERE delivery_id=:delivery"
                    ),
                    {"delivery": prepared.build.delivery_id},
                )
                == 42
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
        self, migration_engine: tuple[sa.Engine, str], tmp_path: Path
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0072")
        prepared = _prepare_owner_full21_release(engine, tmp_path)
        selected = next(
            row
            for row in prepared.payload.attribution_rows
            if row["station_code"] == "350"
        )
        original_curve = next(
            row
            for row in prepared.payload.rating_curve_rows
            if row["id"] == selected["rating_curve_id"]
        )
        other_curve = {
            **original_curve,
            "id": uuid.uuid4(),
            "version": cast("int", original_curve["version"]) + 1,
        }
        payload = dataclasses.replace(
            prepared.payload,
            rating_curve_rows=[*prepared.payload.rating_curve_rows, other_curve],
            attribution_rows=[
                {**row, "rating_curve_id": other_curve["id"]}
                if row["observation_id"] == selected["observation_id"]
                else row
                for row in prepared.payload.attribution_rows
            ],
        )
        _assert_writer_publication_refused(engine, prepared, payload, "three-pointer")

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
