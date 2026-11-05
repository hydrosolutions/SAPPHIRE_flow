from __future__ import annotations

import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import xlsxwriter

from sapphire_flow.basin_operator.inventory import parse_inventory
from sapphire_flow.basin_operator.workspace import ExtractorInterface, extraction_argv


class TestParseInventory:
    def test_preserves_decimal_looking_codes(self, inventory: bytes) -> None:
        stations = parse_inventory(inventory, ".csv")
        assert [s.code for s in stations] == ["001.0", "002"]
        assert stations[0].original.latitude == 28
        assert stations[0].source == "Synthetic survey"

    @pytest.mark.parametrize("replacement", [b"91,85", b"NaN,85", b"28,181"])
    def test_invalid_coordinates(self, inventory: bytes, replacement: bytes) -> None:
        with pytest.raises(ValueError):
            parse_inventory(inventory.replace(b"28,85", replacement), ".csv")

    def test_duplicate_codes(self, inventory: bytes) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            parse_inventory(inventory.replace(b"002,", b"001.0,"), ".csv")

    @pytest.mark.parametrize("numeric", [False, True])
    def test_excel_text_codes_only(self, numeric: bool) -> None:
        content = io.BytesIO()
        with xlsxwriter.Workbook(content) as book:
            sheet = book.add_worksheet()
            sheet.write_row(
                0, 0, ["station_code", "display_name", "latitude", "longitude"]
            )
            sheet.write_row(1, 0, ["001.0", "Synthetic", 28, 85])
            sheet.write_row(2, 0, [2 if numeric else "002", "Other", 28.2, 85.2])
        if numeric:
            with pytest.raises(ValueError, match="text"):
                parse_inventory(content.getvalue(), ".xlsx")
        else:
            assert parse_inventory(content.getvalue(), ".xlsx")[0].code == "001.0"


class TestExtractionArgv:
    def test_station_identity_and_coordinates_are_separate_arguments(
        self, inventory: bytes, tmp_path: Path
    ) -> None:
        stations = parse_inventory(
            inventory.replace(b"Synthetic A", b"Name; not a shell"), ".csv"
        )
        args = extraction_argv(
            ("/external/python", "-m", "static_attrs_nepal.appliance"),
            stations,
            package_id="trial",
            output=tmp_path,
        )
        start = args.index("--station")
        assert args[start : start + 5] == [
            "--station",
            "001.0",
            "Name; not a shell",
            "28.0",
            "85.0",
        ]
        assert args[3:5] == ["--snap-strategy", "distance-first"]

    @pytest.mark.parametrize("interface", list(ExtractorInterface))
    @pytest.mark.parametrize(
        "argument",
        [
            "--station",
            "--station=x",
            "--output",
            "--output=x",
            "--package-id",
            "--package-id=x",
        ],
    )
    def test_owned_arguments_rejected(
        self, interface: ExtractorInterface, argument: str, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="belong to the operator"):
            extraction_argv(
                ("extractor", argument),
                (),
                package_id="test",
                output=tmp_path,
                interface=interface,
            )

    @pytest.mark.parametrize(
        "arguments",
        [
            ("--snap-strategy", "distance-first"),
            ("--snap-strategy=distance-first",),
            ("--snap-audit",),
            ("--snap-audit=true",),
        ],
    )
    def test_release_rejects_v2_options(
        self, arguments: tuple[str, ...], tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="v0.2.0 does not accept"):
            extraction_argv(
                ("extractor", *arguments),
                (),
                package_id="test",
                output=tmp_path,
                interface=ExtractorInterface.RELEASE_V020,
            )

    @pytest.mark.parametrize(
        "arguments",
        [("--snap-strategy", "distance-first"), ("--snap-strategy=distance-first",)],
    )
    def test_legacy_keeps_supported_prefix(
        self, arguments: tuple[str, ...], tmp_path: Path
    ) -> None:
        prefix = ("extractor", *arguments)
        argv = extraction_argv(prefix, (), package_id="test", output=tmp_path)
        assert argv[: len(prefix)] == list(prefix)
        assert sum(arg.startswith("--snap-strategy") for arg in argv) == 1

    @pytest.mark.parametrize(
        "arguments",
        [
            ("--snap-strategy", "area-first"),
            ("--snap-strategy=area-first",),
            ("--snap-strategy",),
        ],
    )
    def test_legacy_rejects_alternate_strategy(
        self, arguments: tuple[str, ...], tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="distance-first"):
            extraction_argv(
                ("extractor", *arguments), (), package_id="test", output=tmp_path
            )
