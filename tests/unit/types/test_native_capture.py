from datetime import UTC, datetime
from uuid import UUID

import pytest

from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.native_capture import (
    CanonicalProviderIdentity,
    Sha256Digest,
    accepted_capture_input_digest,
    canonical_capture_rows,
)
from sapphire_flow.types.observation import RawObservation


def raw(value: float = 1.2) -> RawObservation:
    return RawObservation(
        station_id=StationId(UUID(int=1)),
        timestamp=datetime(2026, 11, 3, 11, tzinfo=UTC),
        parameter="water_level",
        value=value,
        source=ObservationSource.MEASURED,
    )


class TestCanonicalProviderIdentity:
    def test_equivalent_configuration_spelling_normalizes(self) -> None:
        assert CanonicalProviderIdentity.parse(
            "https://BIPADPORTAL.gov.np:443/api/v1", 66
        ) == CanonicalProviderIdentity.parse("https://bipadportal.gov.np/api/v1/", 66)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://bipadportal.gov.np/api/v1/",
            "https://secret@bipadportal.gov.np/api/v1/",
            "https://bipadportal.gov.np/api/v1/?key=private",
            "https://bipadportal.gov.np/api/v1/#private",
        ],
    )
    def test_unsafe_identity_has_sanitized_error(self, endpoint: str) -> None:
        with pytest.raises(
            ValueError, match="provider endpoint must be sanitized HTTPS"
        ) as exc:
            CanonicalProviderIdentity.parse(endpoint, 66)
        assert "private" not in str(exc.value)
        assert "secret" not in str(exc.value)


class TestAcceptedCaptureInputDigest:
    def test_identical_duplicates_have_one_scientific_identity(self) -> None:
        assert accepted_capture_input_digest(
            (raw(), raw())
        ) == accepted_capture_input_digest((raw(),))
        assert canonical_capture_rows((raw(), raw())) == (raw(),)

    def test_conflicting_duplicates_do_not_silently_choose_last(self) -> None:
        with pytest.raises(ValueError, match="conflicting observations"):
            accepted_capture_input_digest((raw(), raw(2.0)))

    def test_correction_changes_payload_identity(self) -> None:
        assert accepted_capture_input_digest((raw(),)) != accepted_capture_input_digest(
            (raw(2.0),)
        )

    def test_nonfinite_capture_is_not_serialized(self) -> None:
        with pytest.raises(ValueError, match="must be finite"):
            accepted_capture_input_digest((raw(float("nan")),))


class TestSha256Digest:
    def test_rejects_noncanonical_digest(self) -> None:
        with pytest.raises(ValueError, match="lowercase hexadecimal"):
            Sha256Digest(value="A" * 64)
