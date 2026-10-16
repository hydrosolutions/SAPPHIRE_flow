from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING, Literal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.services.nepal21_delivery_authority import (
    GenerationBuildInput,
    RestrictedReleaseInput,
    WitnessBytes,
    _normalize_predecessor_row,  # pyright: ignore[reportPrivateUsage]
    canonical_json_bytes,
    fetch_predecessor_semantic_rows,
    insert_restricted_release,
    predecessor_semantic_digest,
    publish_generation,
    readback_generation_content_digest,
    run_dhm21_delivery_replacement_transaction,
    station_set_sha256,
)
from sapphire_flow.services.nepal21_payload_builder import (
    SourceStationBinding,
    build_nepal21_replacement_payload,
)
from sapphire_flow.services.nepal21_source_catalog import (
    build_nepal21_source_catalog,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _station,  # pyright: ignore[reportPrivateUsage]
    _tenant,  # pyright: ignore[reportPrivateUsage]
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


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "rt_path",
        "rt_ordinal",
        "rt_file",
        "rt_duplicate",
        "dfl_value",
        "dfl_omitted",
        "dfl_cross_station",
        "dfl_detached_payload",
        "rt_detached_record",
        "held_rt_station",
        "missing_rt_station",
    ],
)
def test_source_witness_binds_physical_records_before_publication(
    role_harness: _RoleBootstrapHarness,
    tmp_path: Path,
    mutation: Literal[
        "none",
        "rt_path",
        "rt_ordinal",
        "rt_file",
        "rt_duplicate",
        "dfl_value",
        "dfl_omitted",
        "dfl_cross_station",
        "dfl_detached_payload",
        "rt_detached_record",
        "held_rt_station",
        "missing_rt_station",
    ],
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    sources = _sources()
    extra = (
        b"Rating Type No =2\nFrom Date =01-Jan-1988\nTo Date =31-Dec-1989\n"
        b"From Stage =0\n0,5\n1,4\n"
    )
    sources[0] = dataclasses.replace(
        sources[0],
        content=sources[0].content + extra,
        expected_sha256=hashlib.sha256(sources[0].content + extra).hexdigest(),
    )
    catalog = build_nepal21_source_catalog(sources)
    if mutation == "missing_rt_station":
        document = json.loads(catalog.manifest.content)
        document["source_files"] = [
            item
            for item in document["source_files"]
            if not (item["kind"] == "RT" and item["station_code"] == "225")
        ]
        document["source_records"] = [
            item
            for item in document["source_records"]
            if not (item["origin_kind"] == "RT" and item["station_code"] == "225")
        ]
        document["rating_declarations"] = [
            item
            for item in document["rating_declarations"]
            if item["station_code"] != "225"
        ]
        catalog = dataclasses.replace(
            catalog,
            manifest=WitnessBytes(
                kind="manifest_catalog", content=canonical_json_bytes(document)
            ),
            declarations=tuple(
                item for item in catalog.declarations if item.station_code != "225"
            ),
        )
    now = ensure_utc(datetime(2026, 10, 12, tzinfo=UTC))
    generation_id = uuid.uuid4()
    with role_harness.owner_engine.connect() as conn:
        conn.begin()
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
        predecessor_digest = predecessor_semantic_digest(predecessor)
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
            expected_predecessor_semantic_sha256=predecessor_digest,
            canonicalization_policy=None,
            created_at=now,
        )
        insert_restricted_release(conn, release)
        build = GenerationBuildInput(
            id=generation_id,
            release_id=release.id,
            tenant_id=tenant_id,
            delivery_id=release.allowed_new_delivery_id,
            predecessor_semantic_sha256=predecessor_digest,
            preimage_sha256=hashlib.sha256(preimage).hexdigest(),
            manifest_sha256=catalog.manifest.sha256,
            station_set_sha256=station_set_sha256(),
            canonicalization_policy_sha256=None,
            created_at=now,
        )
        if mutation in ("rt_path", "rt_ordinal", "rt_file", "rt_duplicate"):
            declaration = catalog.declarations[0]
            occurrence = declaration.occurrences[0]
            changed = (
                dataclasses.replace(
                    occurrence,
                    source=dataclasses.replace(
                        occurrence.source, relative_path="forged/225.rt"
                    ),
                )
                if mutation == "rt_path"
                else dataclasses.replace(occurrence, ordinal=999)
            )
            if mutation == "rt_file":
                content = occurrence.source.content + b"\n"
                changed = dataclasses.replace(
                    occurrence,
                    source=dataclasses.replace(
                        occurrence.source,
                        content=content,
                        expected_sha256=hashlib.sha256(content).hexdigest(),
                    ),
                )
            occurrences = (
                (*declaration.occurrences, occurrence)
                if mutation == "rt_duplicate"
                else (changed, *declaration.occurrences[1:])
            )
            catalog = dataclasses.replace(
                catalog,
                declarations=(
                    dataclasses.replace(declaration, occurrences=occurrences),
                    *catalog.declarations[1:],
                ),
            )
        elif mutation == "dfl_value":
            record = catalog.daily_records[0]
            catalog = dataclasses.replace(
                catalog,
                daily_records=(
                    dataclasses.replace(
                        record,
                        value=dataclasses.replace(record.value, discharge_m3s=999.0),
                    ),
                    *catalog.daily_records[1:],
                ),
            )
        elif mutation == "dfl_omitted":
            catalog = dataclasses.replace(
                catalog, daily_records=catalog.daily_records[1:]
            )
        elif mutation == "dfl_cross_station":
            record = catalog.daily_records[0]
            other = next(
                item
                for item in catalog.daily_records
                if item.station_code != record.station_code
            )
            catalog = dataclasses.replace(
                catalog,
                daily_records=(
                    dataclasses.replace(record, occurrences=other.occurrences),
                    *catalog.daily_records[1:],
                ),
            )
        ids = count()
        payload = build_nepal21_replacement_payload(
            catalog,
            build,
            bindings,
            preimage_dir=tmp_path,
            id_factory=lambda: uuid.uuid5(generation_id, str(next(ids))),
        )
        if mutation == "dfl_detached_payload":
            target = payload.observation_rows[0]["id"]
            payload = dataclasses.replace(
                payload,
                observation_rows=[
                    {**row, "value": 999.0} if row["id"] == target else row
                    for row in payload.observation_rows
                ],
                attribution_rows=[
                    {**row, "reported_q": 999.0}
                    if row["observation_id"] == target
                    else row
                    for row in payload.attribution_rows
                ],
            )
        elif mutation == "rt_detached_record":
            rows = [
                row for row in payload.declaration_rows if row["station_code"] == "225"
            ]
            assert len(rows) == 2
            swapped = {
                rows[0]["id"]: rows[1]["origin_set_sha256"],
                rows[1]["id"]: rows[0]["origin_set_sha256"],
            }
            payload = dataclasses.replace(
                payload,
                declaration_rows=[
                    {**row, "origin_set_sha256": swapped[row["id"]]}
                    if row["id"] in swapped
                    else row
                    for row in payload.declaration_rows
                ],
            )
        elif mutation == "held_rt_station":
            wrong_station = next(
                item.station_id for item in bindings if item.station_code == "280"
            )
            held = next(
                row
                for row in payload.declaration_rows
                if row["usable_rating_curve_id"] is None
            )
            payload = dataclasses.replace(
                payload,
                declaration_rows=[
                    {**row, "station_id": wrong_station}
                    if row["id"] == held["id"]
                    else row
                    for row in payload.declaration_rows
                ],
            )
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
        )
        assert (
            conn.scalar(sa.text("SELECT session_user"))
            == "sapphire_dhm21_delivery_writer"
        )
        if mutation != "none":
            with pytest.raises(DBAPIError, match="source"), conn.begin_nested():
                run_dhm21_delivery_replacement_transaction(conn, build, payload)
                publish_generation(
                    conn,
                    generation_id=generation_id,
                    immutable_content_sha256=readback_generation_content_digest(
                        conn, generation_id
                    ),
                    published_at=now,
                )
            assert not conn.scalar(
                sa.text(
                    "SELECT EXISTS(SELECT 1 FROM restricted_delivery_generations "
                    "WHERE id=:id)"
                ),
                {"id": generation_id},
            )
        else:
            run_dhm21_delivery_replacement_transaction(conn, build, payload)
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM dhm_rating_declarations "
                        "WHERE generation_id=:id AND shape_state='decreasing_invalid' "
                        "AND function_payload_json IS NULL "
                        "AND native_curve_payload_json IS NULL"
                    ),
                    {"id": generation_id},
                )
                == 1
            )
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
                == "active_pending_qc"
            )
