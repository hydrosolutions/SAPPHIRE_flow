from __future__ import annotations

import dataclasses
import hashlib
import uuid
from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.cli.import_basin_package import run_nepal_static_import_transaction
from sapphire_flow.services.nepal21_delivery_authority import (
    OLD6_STATION_CODES,
    GenerationBuildInput,
    RestrictedReleaseInput,
    _normalize_predecessor_row,  # pyright: ignore[reportPrivateUsage]
    canonical_json_bytes,
    fetch_predecessor_semantic_rows,
    insert_restricted_release,
    predecessor_semantic_digest,
    publish_generation,
    readback_generation_content_digest,
    run_dhm21_delivery_replacement_transaction,
    run_dhm21_qc_transaction,
    sha256_hex_bytes,
    station_set_sha256,
)
from sapphire_flow.services.nepal21_payload_builder import (
    SourceStationBinding,
    build_nepal21_replacement_payload,
)
from sapphire_flow.services.nepal21_source_catalog import (
    DhmSourceFile,
    build_nepal21_source_catalog,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _insert_qc_policy_witness,  # pyright: ignore[reportPrivateUsage]
    _seed_previous_delivery,  # pyright: ignore[reportPrivateUsage]
    _station,  # pyright: ignore[reportPrivateUsage]
    _synthetic_qc_pin,  # pyright: ignore[reportPrivateUsage]
    _tenant,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.services.test_nepal21_static_import import (
    _copy_full21_static_package,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.services.test_nepal21_source_catalog import (
    _replace_content,  # pyright: ignore[reportPrivateUsage]
    _sources,  # pyright: ignore[reportPrivateUsage]
    _synthetic_approval,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )


def test_full21_parsed_sources_replace_old6_publish_qc_and_replay(
    role_harness: _RoleBootstrapHarness,
    tmp_path: Path,
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    sources = _sources()
    sources = [
        _replace_content(item, item.content + b"03/Jan/1991,3\n")
        if item.kind == "DFL"
        else item
        for item in sources
    ]
    extra = (
        b"Rating Type No =2\nFrom Date =01-Jan-1990\nTo Date =31-Dec-1991\n"
        b"From Stage =0\n0,1\n1,2\n"
    )
    sources[0] = _replace_content(sources[0], sources[0].content + extra)
    sources[2] = _replace_content(
        sources[2], sources[2].content.replace(b"0,1\n", b"0,1\n0,1\n")
    )
    ght = b"synthetic hash-only GHT provenance, not gauge proof"
    sources.append(
        DhmSourceFile(
            kind="GHT_HASH_ONLY",
            source_root_label="new",
            relative_path="225.ght",
            content=ght,
            expected_sha256=hashlib.sha256(ght).hexdigest(),
            station_code="225",
        )
    )
    canonicalization = _synthetic_approval(sources, "280")
    catalog = build_nepal21_source_catalog(sources, policy=canonicalization)
    now = ensure_utc(datetime(2026, 10, 12, tzinfo=UTC))
    generation_id = uuid.uuid4()
    with role_harness.owner_engine.begin() as conn:
        tenant_id = TenantId(_tenant(conn))
        bindings = [
            SourceStationBinding(
                station_code=code,
                station_id=StationId(_station(conn, tenant_id, code=code)),
                tenant_id=tenant_id,
                highest_surviving_version=0,
            )
            for code in sorted({item.station_code for item in catalog.daily_records})
        ]
        old_ids = [
            _seed_previous_delivery(conn, item.station_id)
            for item in bindings
            if item.station_code in OLD6_STATION_CODES
        ]
        predecessor = fetch_predecessor_semantic_rows(conn, tenant_id)
        predecessor_digest = predecessor_semantic_digest(predecessor)
        preimage_content = canonical_json_bytes(
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
            expected_predecessor_semantic_sha256=predecessor_digest,
            canonicalization_policy=canonicalization.witness,
            created_at=now,
        )
        insert_restricted_release(conn, release)
        policy = dataclasses.replace(
            _synthetic_qc_pin(bindings[0].station_id),
            station_networks={item.station_id: "dhm" for item in bindings},
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
        predecessor_semantic_sha256=predecessor_digest,
        preimage_sha256=hashlib.sha256(preimage_content).hexdigest(),
        manifest_sha256=catalog.manifest.sha256,
        station_set_sha256=station_set_sha256(),
        canonicalization_policy_sha256=canonicalization.witness.sha256,
        created_at=now,
    )
    ids = count()
    payload = build_nepal21_replacement_payload(
        catalog,
        build,
        bindings,
        preimage_dir=tmp_path,
        id_factory=lambda: uuid.uuid5(generation_id, str(next(ids))),
    )
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
        )
        assert (
            conn.scalar(sa.text("SELECT session_user"))
            == "sapphire_dhm21_delivery_writer"
        )
        target_observation = payload.observation_rows[0]["id"]
        forged_hold = dataclasses.replace(
            payload,
            preimage_dir=tmp_path / "forged-hold",
            observation_rows=[
                {
                    **row,
                    "rating_curve_id": None,
                    "rating_curve_correction_version": None,
                }
                if row["id"] == target_observation
                else row
                for row in payload.observation_rows
            ],
            attribution_rows=[
                {
                    **row,
                    "attribution_state": "held_native_mismatch",
                    "rating_declaration_id": None,
                    "rating_curve_id": None,
                }
                if row["observation_id"] == target_observation
                else row
                for row in payload.attribution_rows
            ],
        )
        with (
            pytest.raises(
                DBAPIError, match="historical rating attribution selector mismatch"
            ),
            conn.begin_nested(),
        ):
            run_dhm21_delivery_replacement_transaction(conn, build, forged_hold)
            publish_generation(
                conn,
                generation_id=generation_id,
                immutable_content_sha256=readback_generation_content_digest(
                    conn, generation_id
                ),
                published_at=now,
            )
        run_dhm21_delivery_replacement_transaction(conn, build, payload)
        publish_generation(
            conn,
            generation_id=generation_id,
            immutable_content_sha256=readback_generation_content_digest(
                conn, generation_id
            ),
            published_at=now,
        )
        with pytest.raises(DBAPIError, match="only QC columns"), conn.begin_nested():
            conn.execute(
                sa.text("UPDATE observations SET created_at=:at WHERE id=:id"),
                {
                    "at": datetime(2026, 10, 13, tzinfo=UTC),
                    "id": payload.observation_rows[0]["id"],
                },
            )
        run_dhm21_qc_transaction(
            conn,
            tenant_id=tenant_id,
            generation_id=generation_id,
            expected_policy=policy,
            completed_at=now,
        )
        run_dhm21_delivery_replacement_transaction(conn, build, payload)
    with role_harness.owner_engine.connect() as conn:
        assert (
            conn.execute(
                sa.text(
                    "SELECT state FROM restricted_delivery_generations WHERE id=:id"
                ),
                {"id": generation_id},
            ).scalar_one()
            == "active_qc_complete"
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM observations WHERE delivery_id=:delivery "
                    "AND qc_status='qc_passed'"
                ),
                {"delivery": build.delivery_id},
            )
            == 42
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(DISTINCT station_id) FROM observations "
                    "WHERE delivery_id=:delivery"
                ),
                {"delivery": build.delivery_id},
            )
            == 21
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM rating_curves WHERE delivery_id=:delivery"
                ),
                {"delivery": build.delivery_id},
            )
            == 22
        )
        for curve_id, observation_id in old_ids:
            assert not conn.scalar(
                sa.text("SELECT EXISTS(SELECT 1 FROM observations WHERE id=:id)"),
                {"id": observation_id},
            )
            assert not conn.scalar(
                sa.text("SELECT EXISTS(SELECT 1 FROM rating_curves WHERE id=:id)"),
                {"id": curve_id},
            )
    package = _copy_full21_static_package(
        tmp_path / "static", package_id="nepal21_20261012", hru="nepal21_20261012"
    )
    with role_harness.owner_engine.begin() as conn:
        before_stations = conn.execute(
            sa.text(
                "SELECT id, to_jsonb(stations) - 'basin_id' FROM stations "
                "WHERE tenant_id=:tenant ORDER BY id"
            ),
            {"tenant": tenant_id},
        ).all()
        before_weather = conn.scalar(
            sa.text("SELECT count(*) FROM station_weather_sources")
        )
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        assert conn.scalar(sa.text("SELECT session_user")) == "sapphire_static_importer"
        report = run_nepal_static_import_transaction(
            conn,
            tenant_id=tenant_id,
            package_dir=package,
            expected_package_id="nepal21_20261012",
            expected_hru="nepal21_20261012",
            clock=lambda: now,
        )
        assert len(report.accepted) == 21
        assert (
            conn.execute(
                sa.text(
                    "SELECT id, to_jsonb(stations) - 'basin_id' FROM stations "
                    "WHERE tenant_id=:tenant ORDER BY id"
                ),
                {"tenant": tenant_id},
            ).all()
            == before_stations
        )
        assert (
            conn.scalar(sa.text("SELECT count(*) FROM station_weather_sources"))
            == before_weather
        )
    persisted = list(tmp_path.glob("*.json"))
    assert len(persisted) == 1
    assert persisted[0].read_bytes() == preimage_content
