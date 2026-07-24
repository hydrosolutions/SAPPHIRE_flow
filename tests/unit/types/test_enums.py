from sapphire_flow.types import enums
from sapphire_flow.types.enums import (
    AuditActorType,
    GaugingStatus,
    NwpCycleSource,
    ParameterDomain,
)

# AuditEventType (Plan 147 Slice B) is imported via the module object, not a
# top-level `from ... import AuditEventType`, so a missing symbol fails each
# test as a real assertion (guarded by `_require_audit_event_type`) rather
# than an ImportError that would abort collection of this whole file.


def _require_audit_event_type() -> type[enums.Enum]:
    audit_event_type = getattr(enums, "AuditEventType", None)
    assert audit_event_type is not None, (
        "AuditEventType is spec-only design-intent "
        "(docs/spec/types-and-protocols.md:334) — Plan 147 Slice B must "
        "promote it to types/enums.py"
    )
    return audit_event_type


class TestParameterDomain:
    def test_has_exactly_five_values(self) -> None:
        assert len(ParameterDomain) == 5

    def test_values_match_spec(self) -> None:
        expected = {"river", "weather", "water_quality", "groundwater", "soil"}
        assert {d.value for d in ParameterDomain} == expected


class TestGaugingStatus:
    def test_has_exactly_three_values(self) -> None:
        assert len(GaugingStatus) == 3

    def test_values_match_spec(self) -> None:
        expected = {"gauged", "ungauged", "calculated"}
        assert {s.value for s in GaugingStatus} == expected

    def test_round_trips_from_string(self) -> None:
        for member in GaugingStatus:
            assert GaugingStatus(member.value) is member


class TestNwpCycleSource:
    """epic-088 M4: RUNOFF_ONLY joins PRIMARY/FALLBACK as a provenance source."""

    def test_runoff_only_member_exists(self) -> None:
        assert hasattr(NwpCycleSource, "RUNOFF_ONLY")

    def test_runoff_only_value(self) -> None:
        assert NwpCycleSource.RUNOFF_ONLY.value == "runoff_only"

    def test_runoff_only_round_trips_from_string(self) -> None:
        assert NwpCycleSource("runoff_only") is NwpCycleSource.RUNOFF_ONLY

    def test_primary_and_fallback_unchanged(self) -> None:
        assert NwpCycleSource.PRIMARY.value == "primary"
        assert NwpCycleSource.FALLBACK.value == "fallback"

    def test_has_exactly_three_sources(self) -> None:
        assert {s.value for s in NwpCycleSource} == {
            "primary",
            "fallback",
            "runoff_only",
        }


class TestAuditActorType:
    """Plan 147 Slice B: wires the previously-dead enum (`types/enums.py:239`)."""

    def test_has_exactly_three_values(self) -> None:
        assert len(AuditActorType) == 3

    def test_values_match_spec(self) -> None:
        assert {a.value for a in AuditActorType} == {"user", "api_key", "system"}


class TestAuditEventType:
    """Plan 147 Slice B: promotes the spec-only enum
    (`docs/spec/types-and-protocols.md:334`) to runtime, plus the additive
    STATION_ONBOARDED/MODEL_ASSIGNED members."""

    def test_exists_as_runtime_enum(self) -> None:
        _require_audit_event_type()

    def test_has_exactly_seventeen_values(self) -> None:
        audit_event_type = _require_audit_event_type()
        assert len(audit_event_type) == 17

    def test_values_match_spec_plus_additive_members(self) -> None:
        audit_event_type = _require_audit_event_type()
        expected = {
            "login",
            "logout",
            "login_failed",
            "password_changed",
            "user_created",
            "user_deactivated",
            "api_key_created",
            "api_key_revoked",
            "api_key_request",
            "forecast_status_change",
            "forecast_adjusted",
            "model_promoted",
            "model_rejected",
            "station_status_change",
            "observation_reprocessed",
            "station_onboarded",
            "model_assigned",
        }
        assert {e.value for e in audit_event_type} == expected

    def test_additive_members_exist(self) -> None:
        audit_event_type = _require_audit_event_type()
        assert audit_event_type.STATION_ONBOARDED.value == "station_onboarded"
        assert audit_event_type.MODEL_ASSIGNED.value == "model_assigned"

    def test_v1_0_wired_members_exist(self) -> None:
        audit_event_type = _require_audit_event_type()
        assert audit_event_type.API_KEY_CREATED.value == "api_key_created"
        assert audit_event_type.API_KEY_REVOKED.value == "api_key_revoked"
        assert audit_event_type.MODEL_PROMOTED.value == "model_promoted"
        assert audit_event_type.MODEL_REJECTED.value == "model_rejected"

    def test_round_trips_from_string(self) -> None:
        audit_event_type = _require_audit_event_type()
        for member in audit_event_type:
            assert audit_event_type(member.value) is member


class TestPipelineCheckTypeRecapSnowReanalysisIngest:
    """Plan 146 D7: a DEDICATED check type, distinct from WEATHER_HISTORY_INGEST."""

    def test_member_exists_and_round_trips_value(self) -> None:
        from sapphire_flow.types.enums import PipelineCheckType

        assert (
            PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST.value
            == "recap_snow_reanalysis_ingest"
        )
        assert (
            PipelineCheckType("recap_snow_reanalysis_ingest")
            is PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST
        )

    def test_distinct_from_weather_history_ingest(self) -> None:
        from sapphire_flow.types.enums import PipelineCheckType

        assert (
            PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST
            is not PipelineCheckType.WEATHER_HISTORY_INGEST
        )
