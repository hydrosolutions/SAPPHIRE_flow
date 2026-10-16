from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING, Literal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.services.nepal21_delivery_authority import (
    GenerationBuildInput,
    RestrictedReleaseInput,
    canonical_json_bytes,
    disable_expired_rating_test_permission,
    enable_expired_rating_test_permission,
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
from sapphire_flow.services.nepal21_source_catalog import build_nepal21_source_catalog
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _insert_qc_policy_witness,  # pyright: ignore[reportPrivateUsage]
    _station,  # pyright: ignore[reportPrivateUsage]
    _synthetic_qc_pin,  # pyright: ignore[reportPrivateUsage]
    _tenant,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.db.test_role_bootstrap import (
    role_harness as role_harness,
)
from tests.unit.services.test_nepal21_source_catalog import (
    _sources,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class RaceGeneration:
    engine: sa.Engine
    tenant_id: uuid.UUID
    generation_id: uuid.UUID


@pytest.fixture(scope="module")
def race_generation(
    role_harness: _RoleBootstrapHarness, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[RaceGeneration]:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    engine = role_harness.owner_engine
    sources = [
        replace(
            item,
            content=item.content + b"03/Jan/1991,3\n",
            expected_sha256=sha256_hex_bytes(item.content + b"03/Jan/1991,3\n"),
        )
        if item.kind == "DFL"
        else item
        for item in _sources()
    ]
    catalog = build_nepal21_source_catalog(sources)
    now = ensure_utc(datetime(2026, 10, 12, tzinfo=UTC))
    generation_id = uuid.uuid4()
    with engine.begin() as conn:
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
        predecessor = fetch_predecessor_semantic_rows(conn, tenant_id)
        assert predecessor == []
        predecessor_digest = predecessor_semantic_digest(predecessor)
        preimage = canonical_json_bytes(
            {
                "codec": "dhm21-preimage-v1",
                "generation_id": str(generation_id),
                "predecessor_delivery_id": "dhm-barkhk-2026-09-08",
                "rows": [],
            }
        )
        release = RestrictedReleaseInput(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            manifest_catalog=catalog.manifest,
            station_set_sha256=station_set_sha256(),
            expected_predecessor_semantic_sha256=predecessor_digest,
            canonicalization_policy=None,
            created_at=now,
        )
        insert_restricted_release(conn, release)
        policy = replace(
            _synthetic_qc_pin(bindings[0].station_id),
            station_networks={item.station_id: "dhm" for item in bindings},
        )
        policy = replace(policy, policy_sha256=sha256_hex_bytes(policy.content_bytes))
        _insert_qc_policy_witness(conn, release.id, content=policy.content_bytes)
    build = GenerationBuildInput(
        id=generation_id,
        release_id=release.id,
        tenant_id=tenant_id,
        delivery_id=release.allowed_new_delivery_id,
        predecessor_semantic_sha256=predecessor_digest,
        preimage_sha256=sha256_hex_bytes(preimage),
        manifest_sha256=catalog.manifest.sha256,
        station_set_sha256=station_set_sha256(),
        canonicalization_policy_sha256=None,
        created_at=now,
    )
    ids = count()
    payload = build_nepal21_replacement_payload(
        catalog,
        build,
        bindings,
        preimage_dir=tmp_path_factory.mktemp("race-preimages"),
        id_factory=lambda: uuid.uuid5(generation_id, str(next(ids))),
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
        )
        assert (
            conn.scalar(sa.text("SELECT session_user"))
            == "sapphire_dhm21_delivery_writer"
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
        run_dhm21_qc_transaction(
            conn,
            tenant_id=tenant_id,
            generation_id=generation_id,
            expected_policy=policy,
            completed_at=now,
        )
    yield RaceGeneration(
        engine=engine, tenant_id=tenant_id, generation_id=generation_id
    )


def _lock(
    conn: sa.Connection,
    generation: RaceGeneration,
    kind: Literal["static", "conversion", "permission", "legacy_permission"],
) -> None:
    role = (
        "sapphire_static_importer"
        if kind == "static"
        else "sapphire_dhm21_delivery_writer"
    )
    conn.execute(sa.text(f"SET LOCAL SESSION AUTHORIZATION {role}"))
    assert conn.scalar(sa.text("SELECT session_user")) == role
    if kind == "static":
        conn.execute(
            sa.text("SELECT sf_lock_nepal_static_guard(:tenant)"),
            {"tenant": generation.tenant_id},
        )
    elif kind == "permission":
        conn.execute(
            sa.text("SELECT sf_lock_dhm21_permission_change(:tenant,:generation)"),
            {"tenant": generation.tenant_id, "generation": generation.generation_id},
        )
    elif kind == "legacy_permission":
        conn.execute(
            sa.text("SELECT sf_lock_provisional_permission_disable(:tenant)"),
            {"tenant": generation.tenant_id},
        )
    else:
        conn.execute(
            sa.text("SELECT sf_lock_dhm21_conversion_append(:tenant,:generation)"),
            {"tenant": generation.tenant_id, "generation": generation.generation_id},
        )


@pytest.mark.parametrize(
    "holder_kind,contender_kind",
    [
        ("conversion", "conversion"),
        ("conversion", "static"),
        ("static", "conversion"),
        ("conversion", "permission"),
        ("permission", "conversion"),
        ("conversion", "legacy_permission"),
        ("legacy_permission", "conversion"),
    ],
)
def test_real_bootstrap_shared_lock_blocks_then_releases(
    race_generation: RaceGeneration,
    holder_kind: Literal["static", "conversion", "permission", "legacy_permission"],
    contender_kind: Literal["static", "conversion", "permission", "legacy_permission"],
) -> None:
    with (
        race_generation.engine.connect() as holder,
        race_generation.engine.connect() as contender,
    ):
        holder_tx = holder.begin()
        try:
            _lock(holder, race_generation, holder_kind)
            with pytest.raises(DBAPIError, match="lock timeout"), contender.begin():
                contender.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                _lock(contender, race_generation, contender_kind)
        finally:
            holder_tx.rollback()
        with contender.begin():
            contender.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
            _lock(contender, race_generation, contender_kind)
            assert contender.scalar(sa.text("SHOW lock_timeout")) == "250ms"


@pytest.mark.parametrize(
    "kind", ["static", "conversion", "permission", "legacy_permission"]
)
def test_prelock_refuses_repeatable_read(
    race_generation: RaceGeneration,
    kind: Literal["static", "conversion", "permission", "legacy_permission"],
) -> None:
    with (
        race_generation.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as conn,
        conn.begin(),
        pytest.raises(DBAPIError, match="require READ COMMITTED"),
    ):
        _lock(conn, race_generation, kind)


def test_static_missing_tenant_refused_before_relation_wait(
    race_generation: RaceGeneration,
) -> None:
    missing = RaceGeneration(
        engine=race_generation.engine,
        tenant_id=uuid.uuid4(),
        generation_id=race_generation.generation_id,
    )
    with race_generation.engine.connect() as holder, holder.begin():
        _lock(holder, race_generation, "static")
        with race_generation.engine.connect() as contender, contender.begin():
            contender.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
            with pytest.raises(DBAPIError, match="static tenant is absent"):
                _lock(contender, missing, "static")


def test_legacy_holder_timeout_rolls_back_partial_tenant_lock(
    race_generation: RaceGeneration,
) -> None:
    with (
        race_generation.engine.connect() as legacy,
        race_generation.engine.connect() as contender,
    ):
        transaction = legacy.begin()
        try:
            legacy.execute(sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_worker"))
            assert legacy.scalar(sa.text("SELECT session_user")) == "sapphire_worker"
            legacy.execute(
                sa.text(
                    "SELECT pg_advisory_xact_lock(hashtextextended("
                    "'provisional-discharge:' || CAST(:tenant AS text),0))"
                ),
                {"tenant": race_generation.tenant_id},
            )
            with pytest.raises(DBAPIError, match="lock timeout"), contender.begin():
                contender.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                _lock(contender, race_generation, "conversion")
            # A separate connection proves the failed waiter retained no tenant lock.
            with race_generation.engine.connect() as probe, probe.begin():
                probe.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                _lock(probe, race_generation, "static")
        finally:
            transaction.rollback()
        with contender.begin():
            contender.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
            _lock(contender, race_generation, "conversion")


@pytest.fixture
def enabled_permission(race_generation: RaceGeneration) -> Iterator[uuid.UUID]:
    with race_generation.engine.begin() as conn:
        assert conn.scalar(sa.text("SELECT session_user")) == "test"
        permission_id = enable_expired_rating_test_permission(
            conn,
            tenant_id=TenantId(race_generation.tenant_id),
            generation_id=race_generation.generation_id,
            owner_authorization_sha256="a" * 64,
            precedence_sha256="b" * 64,
            selection_manifest_sha256="c" * 64,
            enabled_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
        )
    yield permission_id
    with race_generation.engine.begin() as conn:
        if _permission_snapshot(conn, permission_id)["state"] == "enabled":
            disable_expired_rating_test_permission(
                conn,
                permission_id=permission_id,
                disabled_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
            )


def _permission_snapshot(
    conn: sa.Connection, permission_id: uuid.UUID
) -> dict[str, object]:
    row = (
        conn.execute(
            sa.text("SELECT * FROM expired_rating_test_permissions WHERE id=:id"),
            {"id": permission_id},
        )
        .mappings()
        .one()
    )
    return dict(row)


def test_actual_disable_waits_for_conversion_then_commits_without_other_drift(
    race_generation: RaceGeneration, enabled_permission: uuid.UUID
) -> None:
    with (
        race_generation.engine.connect() as holder,
        race_generation.engine.connect() as contender,
        race_generation.engine.connect() as observer,
    ):
        holder_tx = holder.begin()
        try:
            _lock(holder, race_generation, "conversion")
            before = _permission_snapshot(observer, enabled_permission)
            assert before["state"] == "enabled"
            with pytest.raises(DBAPIError, match="lock timeout"), contender.begin():
                assert contender.scalar(sa.text("SELECT session_user")) == "test"
                contender.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                disable_expired_rating_test_permission(
                    contender,
                    permission_id=enabled_permission,
                    disabled_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
                )
            assert _permission_snapshot(observer, enabled_permission) == before
        finally:
            holder_tx.rollback()
        with contender.begin():
            disabled_at = ensure_utc(datetime(2026, 10, 13, tzinfo=UTC))
            disable_expired_rating_test_permission(
                contender,
                permission_id=enabled_permission,
                disabled_at=disabled_at,
            )
        with holder.begin():
            assert _permission_snapshot(observer, enabled_permission) == {
                **before,
                "state": "disabled",
                "disabled_at": disabled_at,
            }


def test_uncommitted_actual_disable_blocks_conversion_then_rollback_restores_row(
    race_generation: RaceGeneration, enabled_permission: uuid.UUID
) -> None:
    with (
        race_generation.engine.connect() as holder,
        race_generation.engine.connect() as contender,
        race_generation.engine.connect() as observer,
    ):
        holder_tx = holder.begin()
        try:
            before = _permission_snapshot(observer, enabled_permission)
            assert before["state"] == "enabled"
            disable_expired_rating_test_permission(
                holder,
                permission_id=enabled_permission,
                disabled_at=ensure_utc(datetime(2026, 10, 13, tzinfo=UTC)),
            )
            assert (
                _permission_snapshot(holder, enabled_permission)["state"] == "disabled"
            )
            assert _permission_snapshot(observer, enabled_permission) == before
            with pytest.raises(DBAPIError, match="lock timeout"), contender.begin():
                contender.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                _lock(contender, race_generation, "conversion")
        finally:
            holder_tx.rollback()
        with contender.begin():
            _lock(contender, race_generation, "conversion")
            assert _permission_snapshot(observer, enabled_permission) == before
