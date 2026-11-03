import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest

from sapphire_flow.config.swiss_overview import SwissOverviewConfig, load_swiss_roster


class TestSwissRoster:
    def test_packaged_approved_roster(self) -> None:
        raw = (
            files("sapphire_flow.config")
            .joinpath("swiss_display_roster.json")
            .read_bytes()
        )
        assert (
            hashlib.sha256(raw).hexdigest()
            == "2473843709831dcae8266e51757efccb563eb287871caffcc276835ca4007be1"
        )
        roster = load_swiss_roster(
            SwissOverviewConfig(
                roster_sha256="2473843709831dcae8266e51757efccb563eb287871caffcc276835ca4007be1",
                roster_count=134,
            )
        )
        assert len(roster) == 134
        assert all(row.network == "bafu" for row in roster)

    @pytest.mark.parametrize(
        "fault",
        ["digest", "count", "duplicate", "duplicate_code", "cross_network_code"],
    )
    def test_corruption_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
    ) -> None:
        import sapphire_flow.config.swiss_overview as config

        raw = (
            files("sapphire_flow.config")
            .joinpath("swiss_display_roster.json")
            .read_bytes()
        )
        rows = json.loads(raw)
        if fault == "duplicate":
            rows[1] = rows[0]
        elif fault in {"duplicate_code", "cross_network_code"}:
            rows[1]["code"] = rows[0]["code"]
            if fault == "cross_network_code":
                rows[1]["network"] = "other"
        raw = json.dumps(rows).encode()
        (tmp_path / "swiss_display_roster.json").write_bytes(raw)
        monkeypatch.setattr(config, "files", lambda _: tmp_path)
        with pytest.raises(ValueError, match="Swiss roster"):
            load_swiss_roster(
                SwissOverviewConfig(
                    roster_sha256="0" * 64
                    if fault == "digest"
                    else hashlib.sha256(raw).hexdigest(),
                    roster_count=133 if fault == "count" else 134,
                )
            )
