from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from uuid import UUID

import pytest

from sapphire_flow.services.nepal21_delivery_authority import (
    NEPAL21_STATION_CODES,
    GenerationBuildInput,
    RestrictedReplacementPayload,
    station_set_sha256,
)
from sapphire_flow.services.nepal21_payload_builder import (
    SourceStationBinding,
    build_nepal21_replacement_payload,
)
from sapphire_flow.services.nepal21_source_catalog import (
    CanonicalizationPolicy,
    DhmSourceFile,
    build_nepal21_source_catalog,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from tests.unit.services.test_nepal21_source_catalog import (
    _replace_content,  # pyright: ignore[reportPrivateUsage]
    _sources,  # pyright: ignore[reportPrivateUsage]
    _synthetic_approval,  # pyright: ignore[reportPrivateUsage]
)


def _payload(
    sources: list[DhmSourceFile], policy: CanonicalizationPolicy | None = None
) -> RestrictedReplacementPayload:
    catalog = build_nepal21_source_catalog(sources, policy=policy)
    tenant = TenantId(UUID(int=100000))
    build = GenerationBuildInput(
        id=UUID(int=100001),
        release_id=UUID(int=100002),
        tenant_id=tenant,
        delivery_id=f"dhm-nepal21-{catalog.manifest.sha256[:16]}",
        predecessor_semantic_sha256="1" * 64,
        preimage_sha256="2" * 64,
        manifest_sha256=catalog.manifest.sha256,
        station_set_sha256=station_set_sha256(),
        canonicalization_policy_sha256=policy.witness.sha256 if policy else None,
        created_at=ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
    )
    bindings = [
        SourceStationBinding(
            station_code=code,
            station_id=StationId(UUID(int=1000 + i)),
            tenant_id=tenant,
            highest_surviving_version=3,
        )
        for i, code in enumerate(NEPAL21_STATION_CODES)
    ]
    ids = count(1)
    return build_nepal21_replacement_payload(
        catalog,
        build,
        bindings,
        preimage_dir=Path("unused-pure-unit"),
        id_factory=lambda: UUID(int=next(ids)),
    )


class TestBuildNepal21ReplacementPayload:
    def test_full21_native_and_attribution_bindings(self) -> None:
        payload = _payload(_sources())
        assert len(payload.rating_curve_rows) == len(payload.observation_rows) == 21
        assert len(payload.origin_rows) == 42
        assert {row["version"] for row in payload.rating_curve_rows} == {4}
        assert {row["qc_status"] for row in payload.observation_rows} == {"raw"}
        curves = {row["id"] for row in payload.rating_curve_rows}
        assert {row["rating_curve_id"] for row in payload.attribution_rows} == curves
        assert {row["attribution_state"] for row in payload.attribution_rows} == {
            "selected"
        }
        assert payload == _payload(_sources())

    @pytest.mark.parametrize(
        "latest_from,expected",
        [
            ("01-Jan-1991", "held_invalid_latest"),
            ("01-Jan-1990", "held_equal_start_tie"),
        ],
    )
    def test_latest_invalid_never_falls_back(
        self, latest_from: str, expected: str
    ) -> None:
        sources = _sources()
        additional = (
            "Rating Type No =2\nFrom Date ="
            + latest_from
            + "\nTo Date =31-Dec-1991\nFrom Stage =0\n0,5\n1,1\n"
        ).encode()
        sources[0] = _replace_content(sources[0], sources[0].content + additional)
        payload = _payload(sources)
        attribution = next(
            row for row in payload.attribution_rows if row["station_code"] == "225"
        )
        assert attribution["attribution_state"] == expected
        assert attribution["rating_curve_id"] is None

    def test_equal_start_equivalent_declarations_choose_one_deterministically(
        self,
    ) -> None:
        sources = _sources()
        additional = (
            b"Rating Type No =2\nFrom Date =01-Jan-1990\n"
            b"To Date =31-Dec-1991\nFrom Stage =0\n0,1\n1,2\n"
        )
        sources[0] = _replace_content(sources[0], sources[0].content + additional)
        payload = _payload(sources)
        attribution = next(
            row for row in payload.attribution_rows if row["station_code"] == "225"
        )
        assert attribution["attribution_state"] == "selected"
        assert len(payload.rating_curve_rows) == 22
        assert payload == _payload(sources)

    def test_duplicate_physical_sources_are_not_discarded(self) -> None:
        sources = _sources()
        payload = _payload(
            [*sources, dataclasses.replace(sources[0], relative_path="duplicate.txt")]
        )
        assert len(payload.rating_curve_rows) == 21
        assert len(payload.origin_rows) == 43
        assert (
            sorted(int(str(row["member_count"])) for row in payload.origin_set_rows)[-1]
            == 2
        )

    def test_approved_duplicate_points_emit_unique_native_curve(self) -> None:
        sources = _sources()
        sources[0] = _replace_content(
            sources[0], sources[0].content.replace(b"0,1\n", b"0,1\n0,1\n")
        )
        payload = _payload(sources, _synthetic_approval(sources))
        declaration = next(
            row for row in payload.declaration_rows if row["station_code"] == "225"
        )
        assert declaration["shape_state"] == "canonicalized_usable"
        assert declaration["canonicalization_approval_id"] is not None
        curve = next(
            row
            for row in payload.rating_curve_rows
            if row["id"] == declaration["usable_rating_curve_id"]
        )
        assert curve["points"] == [
            {"water_level": 0.0, "discharge": 1.0},
            {"water_level": 1.0, "discharge": 2.0},
        ]
