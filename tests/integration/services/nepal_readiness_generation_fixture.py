from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid5

import sqlalchemy as sa

from sapphire_flow.services.nepal21_delivery_authority import (
    NEPAL21_STATION_CODES,
    ExpectedQcPolicyPin,
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
from sapphire_flow.services.nepal_onboarding import DeliveryObservationReader
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import (
    InterpolationMethod,
    ObservationSource,
    QcStatus,
    StationStatus,
)
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.rating_curve import RatingCurve
    from sapphire_flow.types.station import StationConfig


def _uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError("prepared observation identity must be a UUID")
    return value


@dataclass(frozen=True, kw_only=True, slots=True)
class ReadinessGeneration:
    generation_id: UUID
    delivery_id: str
    policy: ExpectedQcPolicyPin
    completion_sha256: str
    ordinary: tuple[Observation, ...]
    curve: RatingCurve


def seed_readiness_generation(
    conn: sa.Connection,
    *,
    stations: list[StationConfig],
    predecessor: list[Observation],
    omit_ids: frozenset[ObservationId],
    now: UtcDatetime,
    evidence_dir: Path,
) -> ReadinessGeneration:
    tenant_id = stations[0].tenant_id
    namespace = UUID("00000000-0000-0000-0000-000000052016")
    generation_id = uuid5(namespace, "generation")
    store = PgStationStore(conn)
    by_code = {station.code: station for station in stations}
    for code in NEPAL21_STATION_CODES:
        if code not in by_code:
            station = make_station_config(
                station_id=StationId(uuid5(namespace, code)),
                code=code,
                network="dhm",
                tenant_id=tenant_id,
                name=f"Synthetic readiness station {code}",
                station_status=StationStatus.ONBOARDING,
                forecast_targets=None,
            )
            store.store_station(station)
            by_code[code] = station
    PgObservationStore(conn).store_observations(predecessor)
    planned: list[Observation] = []
    sources: list[DhmSourceFile] = []
    for code in NEPAL21_STATION_CODES:
        station = by_code[code]
        # Each declared local day begins at 18:15 UTC on the preceding date.
        # The original daily scientific values retain their UTC resampling bins.
        daily = [
            replace(
                row,
                timestamp=ensure_utc(row.timestamp + timedelta(hours=18, minutes=15)),
            )
            for row in predecessor
            if row.station_id == station.id and row.id not in omit_ids
        ]
        if not daily or code == "450":
            outside = [
                Observation(
                    id=ObservationId(uuid5(namespace, f"outside-window:{code}")),
                    station_id=station.id,
                    timestamp=ensure_utc(datetime(1991, 1, 1, 18, 15, tzinfo=UTC)),
                    parameter="discharge",
                    value=2.0,
                    source=ObservationSource.MANUAL_IMPORT,
                    rating_curve_id=None,
                    rating_curve_correction_version=None,
                    qc_status=QcStatus.QC_PASSED,
                    qc_flags=[],
                    qc_rule_version=None,
                    created_at=now,
                    delivery_id=None,
                )
            ]
            outside.append(
                replace(
                    outside[0],
                    id=ObservationId(uuid5(namespace, f"outside-window-2:{code}")),
                    timestamp=ensure_utc(outside[0].timestamp + timedelta(days=1)),
                    value=3.0,
                )
            )
            daily = [*outside, *daily]
        planned.extend(daily)
        rt = (
            f"Station No:{code}\nData Type: Rating Table\nLeft Column= Stage in m\n"
            "Right Column= Discharge im m3/s\nRating Type No =1\n"
            "From Date =10-Jan-2025\nTo Date =08-Jan-2026\nFrom Stage =1\n"
            "1,4\n2,10000\n"
        ).encode()
        records = "".join(
            f"{(row.timestamp + timedelta(hours=5, minutes=45)).strftime('%d/%b/%Y')},"
            f"{row.value}\n"
            for row in daily
        )
        years = "\n".join(
            str(year) for year in sorted({row.timestamp.year for row in daily})
        )
        dfl = f"Daily Flow of Station: {code} in m3/s\n{years}\n{records}".encode()
        source_contents: tuple[tuple[Literal["RT", "DFL"], bytes], ...] = (
            ("RT", rt),
            ("DFL", dfl),
        )
        for kind, content in source_contents:
            sources.append(
                DhmSourceFile(
                    kind=kind,
                    source_root_label="synthetic_test",
                    relative_path=f"readiness-v2/{kind}_{code}.txt",
                    content=content,
                    expected_sha256=sha256_hex_bytes(content),
                )
            )
    catalog = build_nepal21_source_catalog(sources)
    previous = fetch_predecessor_semantic_rows(conn, tenant_id)
    assert len(previous) == len(predecessor) == 60
    predecessor_digest = predecessor_semantic_digest(previous)
    preimage = canonical_json_bytes(
        {
            "codec": "dhm21-preimage-v1",
            "generation_id": str(generation_id),
            "predecessor_delivery_id": "dhm-barkhk-2026-09-08",
            "rows": [_normalize_predecessor_row(row) for row in previous],
        }
    )
    release = RestrictedReleaseInput(
        id=uuid5(namespace, "release"),
        tenant_id=tenant_id,
        manifest_catalog=catalog.manifest,
        station_set_sha256=station_set_sha256(),
        expected_predecessor_semantic_sha256=predecessor_digest,
        canonicalization_policy=None,
        created_at=now,
    )
    insert_restricted_release(conn, release)
    policy = ExpectedQcPolicyPin(
        policy_sha256="0" * 64,
        rule_set=QcRuleSet(
            version="readiness-v2",
            rules=(
                QcRuleParams(
                    rule_id="range_check",
                    rule_version="readiness-range-v2",
                    parameter="discharge",
                    time_step=timedelta(days=1),
                    network="dhm",
                    thresholds={"value_min": 0.0, "value_max": 1000.0},
                ),
            ),
        ),
        station_networks={station.id: "dhm" for station in by_code.values()},
        overrides=[],
        baselines=[],
    )
    policy = replace(policy, policy_sha256=sha256_hex_bytes(policy.content_bytes))
    conn.execute(
        sa.text(
            "INSERT INTO restricted_delivery_release_witnesses "
            "(release_id,witness_kind,content_sha256,content_bytes,created_at) "
            "VALUES (:release,'qc_policy',:digest,:content,:created)"
        ),
        {
            "release": release.id,
            "digest": policy.policy_sha256,
            "content": policy.content_bytes,
            "created": now,
        },
    )
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
    sequence = count()
    payload = build_nepal21_replacement_payload(
        catalog,
        build,
        [
            SourceStationBinding(
                station_code=code,
                station_id=station.id,
                tenant_id=tenant_id,
                highest_surviving_version=0,
            )
            for code, station in sorted(by_code.items())
        ],
        preimage_dir=evidence_dir,
        id_factory=lambda: uuid5(namespace, f"payload:{next(sequence)}"),
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
    reader = DeliveryObservationReader(conn)
    history_start = ensure_utc(datetime(1991, 1, 1, tzinfo=UTC))
    assert PgObservationStore(conn).fetch_observations(
        stations[0].id,
        "discharge",
        history_start,
        now,
    )
    assert (
        reader.fetch_accepted_historical_observations(
            stations[0].id,
            "discharge",
            history_start,
            now,
            expected_qc_policy_sha256=policy.policy_sha256,
        )
        == []
    ), "pending generation must not supply readiness history"
    completion = run_dhm21_qc_transaction(
        conn,
        tenant_id=tenant_id,
        generation_id=generation_id,
        expected_policy=policy,
        completed_at=now,
    )
    accepted = reader.fetch_accepted_historical_observations(
        stations[0].id,
        "discharge",
        history_start,
        now,
        qc_status=QcStatus.QC_PASSED,
        expected_qc_policy_sha256=policy.policy_sha256,
    )
    expected_values = [
        row.value
        for row in sorted(planned, key=lambda row: row.timestamp)
        if row.station_id == stations[0].id
    ]
    assert [row.value for row in accepted] == expected_values
    assert not PgObservationStore(conn).fetch_delivery_observations(
        "dhm-barkhk-2026-09-08", [station.id for station in stations]
    )
    ids = {
        (row["station_id"], row["timestamp"]): row["id"]
        for row in payload.observation_rows
        if row["parameter"] == "discharge"
    }
    # IDs come from the prepared writer input. Values/status expectations come
    # only from declared inputs and the independently specified range rule.
    expected = tuple(
        replace(
            row,
            id=ObservationId(_uuid(ids[(row.station_id, row.timestamp)])),
            delivery_id=build.delivery_id,
            created_at=now,
            qc_flags=[],
            qc_status=QcStatus.QC_PASSED,
            qc_rule_version=policy.policy_sha256,
        )
        for row in planned
    )
    curves = PgRatingCurveStore(conn).fetch_all_curves_for_station(stations[0].id)
    assert len(curves) == 1
    _assert_declared_curve_science(curves[0])
    return ReadinessGeneration(
        generation_id=generation_id,
        delivery_id=build.delivery_id,
        policy=policy,
        completion_sha256=completion,
        ordinary=expected,
        curve=curves[0],
    )


def _assert_declared_curve_science(curve: RatingCurve) -> None:
    # Independent literals from the declared RT, not the parser or DB output.
    expected = {
        "points": [
            {"water_level": 1.0, "discharge": 4.0},
            {"water_level": 2.0, "discharge": 10000.0},
        ],
        "interpolation": InterpolationMethod.LINEAR,
        "rating_type_label": "1",
        "valid_from": ensure_utc(datetime(2025, 1, 9, 18, 15, tzinfo=UTC)),
        "valid_to": ensure_utc(datetime(2026, 1, 8, 18, 15, tzinfo=UTC)),
    }
    observed = asdict(curve)
    actual = {field: observed[field] for field in expected}
    assert actual == expected, "declared readiness RT scientific fields changed"
