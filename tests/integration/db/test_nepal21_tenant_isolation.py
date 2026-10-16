from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING
from uuid import uuid4, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db import metadata as db
from sapphire_flow.services.nepal21_delivery_authority import (
    NEPAL21_STATION_CODES,
    GenerationBuildInput,
    RestrictedReleaseInput,
    canonical_json_bytes,
    insert_restricted_release,
    predecessor_semantic_digest,
    publish_generation,
    readback_generation_content_digest,
    sha256_hex_bytes,
    station_set_sha256,
)
from sapphire_flow.services.nepal21_payload_builder import (
    SourceStationBinding,
    build_nepal21_replacement_payload,
)
from sapphire_flow.services.nepal21_source_catalog import build_nepal21_source_catalog
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import StationId, TenantId
from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _station,  # pyright: ignore[reportPrivateUsage]
    _tenant,  # pyright: ignore[reportPrivateUsage]
    _tenant_with_code,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.unit.services.test_nepal21_source_catalog import (
    _sources,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )


def test_foreign_incomplete_publication_is_refused_without_hiding_legacy_history(
    role_harness: _RoleBootstrapHarness, tmp_path: Path
) -> None:
    # A second full21 tenant is impossible under unique(network, code).
    # This is publication-refusal coverage, NOT a successful foreign-generation
    # read-isolation claim. The former partial-positive fixture was illegal.
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    now = ensure_utc(datetime(2026, 10, 12, tzinfo=UTC))
    catalog = build_nepal21_source_catalog(_sources())
    with role_harness.owner_engine.connect() as conn:
        conn.begin()
        tenant_a = _tenant(conn)
        station_a = StationId(_station(conn, tenant_a, code="447"))
        tenant_b = TenantId(_tenant_with_code(conn, "other-tenant"))
        observation_id = uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO observations "
                "(id,station_id,timestamp,parameter,value,source,qc_status,qc_flags,"
                "qc_rule_version,delivery_id) VALUES "
                "(:id,:station,:at,'discharge',1.0,'manual_import','qc_passed',"
                "'[]'::jsonb,'legacy','dhm-barkhk-2026-09-08')"
            ),
            {
                "id": observation_id,
                "station": station_a,
                "at": datetime(1991, 1, 2, tzinfo=UTC),
            },
        )
        with (
            pytest.raises(DBAPIError, match="uq_stations_network_code"),
            conn.begin_nested(),
        ):
            _station(conn, tenant_b, code="447")
        stations_b = {
            code: StationId(_station(conn, tenant_b, code=code))
            for code in NEPAL21_STATION_CODES
            if code != "447"
        }
        missing_station_id = StationId(uuid4())
        bindings = [
            SourceStationBinding(
                station_code=code,
                station_id=stations_b.get(code, missing_station_id),
                tenant_id=tenant_b,
                highest_surviving_version=0,
            )
            for code in NEPAL21_STATION_CODES
        ]
        generation_id = uuid4()
        predecessor_digest = predecessor_semantic_digest([])
        release = RestrictedReleaseInput(
            id=uuid4(),
            tenant_id=tenant_b,
            manifest_catalog=catalog.manifest,
            station_set_sha256=station_set_sha256(),
            expected_predecessor_semantic_sha256=predecessor_digest,
            canonicalization_policy=None,
            created_at=now,
        )
        insert_restricted_release(conn, release)
        preimage = canonical_json_bytes(
            {
                "codec": "dhm21-preimage-v1",
                "generation_id": str(generation_id),
                "predecessor_delivery_id": "dhm-barkhk-2026-09-08",
                "rows": [],
            }
        )
        build = GenerationBuildInput(
            id=generation_id,
            release_id=release.id,
            tenant_id=tenant_b,
            delivery_id=release.allowed_new_delivery_id,
            predecessor_semantic_sha256=predecessor_digest,
            preimage_sha256=sha256_hex_bytes(preimage),
            manifest_sha256=catalog.manifest.sha256,
            station_set_sha256=station_set_sha256(),
            canonicalization_policy_sha256=None,
            created_at=now,
        )
        sequence = count()
        payload = build_nepal21_replacement_payload(
            catalog,
            build,
            bindings,
            preimage_dir=tmp_path,
            id_factory=lambda: uuid5(generation_id, str(next(sequence))),
        )
        missing_origins = {
            row["id"]
            for row in payload.origin_rows
            if row["relative_path"] in {"RT_447.txt", "DFL_447.txt"}
        }
        assert len(missing_origins) == 2
        missing_sets = {
            row["origin_set_sha256"]
            for row in payload.origin_member_rows
            if row["origin_id"] in missing_origins
        }
        before = conn.execute(
            sa.text("SELECT to_jsonb(o) FROM observations o WHERE station_id=:station"),
            {"station": station_a},
        ).scalar_one()
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
        )
        assert (
            conn.scalar(sa.text("SELECT session_user"))
            == "sapphire_dhm21_delivery_writer"
        )
        conn.execute(
            sa.text("SELECT sf_lock_dhm21_delivery_write(:tenant,:release)"),
            {"tenant": tenant_b, "release": release.id},
        )
        conn.execute(
            db.restricted_delivery_generations.insert().values(
                id=build.id,
                release_id=release.id,
                tenant_id=tenant_b,
                network="dhm",
                cohort="nepal21",
                delivery_id=build.delivery_id,
                predecessor_delivery_id="dhm-barkhk-2026-09-08",
                predecessor_semantic_sha256=predecessor_digest,
                preimage_sha256=build.preimage_sha256,
                manifest_sha256=build.manifest_sha256,
                station_set_sha256=build.station_set_sha256,
                state="building",
                created_at=now,
            )
        )
        # Direct SQL deliberately stages only the legally owned twenty stations.
        # All source-file/record witnesses remain the valid owner-pinned full21.
        for table, rows in (
            (db.rating_curves, payload.rating_curve_rows),
            (db.observations, payload.observation_rows),
            (db.dhm_delivery_origins, payload.origin_rows),
            (db.dhm_origin_sets, payload.origin_set_rows),
            (db.dhm_origin_set_members, payload.origin_member_rows),
            (db.dhm_rating_declarations, payload.declaration_rows),
            (db.dhm_delivery_observation_attributions, payload.attribution_rows),
        ):
            retained = [
                row
                for row in rows
                if row.get("station_id") != missing_station_id
                and row.get("id") not in missing_origins
                and row.get("origin_id") not in missing_origins
                and row.get("origin_set_sha256") not in missing_sets
            ]
            assert retained
            conn.execute(table.insert(), retained)
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM dhm_delivery_origins WHERE generation_id=:id"
                ),
                {"id": generation_id},
            )
            == 40
        )
        with (
            pytest.raises(
                DBAPIError,
                match="persisted source occurrences differ from authorized parsed "
                "records",
            ),
            conn.begin_nested(),
        ):
            publish_generation(
                conn,
                generation_id=generation_id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, generation_id
                ),
                published_at=now,
            )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                ),
                {"id": generation_id},
            )
            == "building"
        )
        conn.execute(sa.text("RESET SESSION AUTHORIZATION"))
        assert (
            conn.execute(
                sa.text(
                    "SELECT to_jsonb(o) FROM observations o WHERE station_id=:station"
                ),
                {"station": station_a},
            ).scalar_one()
            == before
        )
        rows = PgObservationStore(conn).fetch_accepted_historical_observations(
            station_a,
            "discharge",
            ensure_utc(datetime(1991, 1, 1, tzinfo=UTC)),
            ensure_utc(datetime(1991, 1, 3, tzinfo=UTC)),
            qc_status=QcStatus.QC_PASSED,
        )
        assert [row.id for row in rows] == [observation_id]
