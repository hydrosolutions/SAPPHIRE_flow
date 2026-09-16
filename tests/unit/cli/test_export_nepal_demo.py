import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sapphire_flow.cli.export_nepal_demo import export_bundle
from sapphire_flow.cli.nepal_demo_schemas import DemoBundle

ROOT = Path(__file__).resolve().parents[3]
BASIN = ROOT / "docs/handover/nepal-demo-assets/dudh-koshi-rabuwa.geojson"
FILES = {
    "manifest": "region.json",
    "series": "series.json",
    "station": "station.geojson",
    "basin": "basin.geojson",
}


class TestExportBundle:
    def test_module_cli_shifts_all_times(self, tmp_path: Path) -> None:
        environment = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        environment["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "sapphire_flow.cli.export_nepal_demo",
                "--basin-file",
                str(BASIN),
                "--output-dir",
                str(tmp_path / "out"),
                "--issued-at",
                "2025-08-13T00:00:00Z",
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        raw = {
            key: json.loads((tmp_path / "out" / name).read_text())
            for key, name in FILES.items()
        }
        bundle = DemoBundle.model_validate(raw)
        assert bundle.series.forecasts[0].issued_at == "2025-08-13T00:00:00Z"
        assert bundle.series.observations.valid_times[0] == "2025-08-06T00:00:00Z"
        assert bundle.series.observations.valid_times[-1] == "2025-08-14T18:00:00Z"

    def test_invalid_basin_publishes_nothing(self, tmp_path: Path) -> None:
        basin = tmp_path / "invalid.geojson"
        basin.write_text('{"type": "FeatureCollection", "features": []}')
        with pytest.raises(ValueError, match="features"):
            export_bundle(basin_file=basin, output_dir=tmp_path / "out")
        assert list(tmp_path.iterdir()) == [basin]

    def test_data_and_schema_are_reproducible_and_preserve_geometry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import socket

        def no_network(*args: object, **kwargs: object) -> None:
            raise AssertionError("unexpected network access")

        monkeypatch.setattr(socket, "create_connection", no_network)
        monkeypatch.setattr(socket.socket, "connect", no_network)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        for name in ("a", "b"):
            export_bundle(basin_file=BASIN, output_dir=tmp_path / name)
        assert sorted(p.name for p in (tmp_path / "a").iterdir()) == sorted(
            [*FILES.values(), "schema.json"]
        )
        for filename in [*FILES.values(), "schema.json"]:
            assert (tmp_path / "a" / filename).read_bytes() == (
                tmp_path / "b" / filename
            ).read_bytes()
            assert (tmp_path / "a" / filename).read_bytes() == (
                ROOT / "tests/fixtures/nepal_demo_v2" / filename
            ).read_bytes()
        raw = {
            key: json.loads((tmp_path / "a" / filename).read_text())
            for key, filename in FILES.items()
        }
        DemoBundle.model_validate(raw)
        assert raw["basin"]["features"] == json.loads(BASIN.read_text())["features"]
        assert raw["series"]["forecasts"][-1]["horizon_end"] == "2025-08-16T21:00:00Z"
        assert raw["manifest"]["thresholds"] is None
        assert len(raw["series"]["forecasts"]) == 8
        assert "verification" not in raw["series"]
        assert "superseded" not in raw["series"]

    @pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
    def test_existing_destination_unchanged(self, tmp_path: Path, kind: str) -> None:
        dest = tmp_path / "existing"
        if kind == "directory":
            dest.mkdir()
        elif kind == "file":
            dest.write_text("keep")
        else:
            dest.symlink_to(tmp_path / "missing")
        with pytest.raises(FileExistsError, match="existing"):
            export_bundle(basin_file=BASIN, output_dir=dest)
        assert dest.is_symlink() if kind == "symlink" else dest.exists()
        if kind == "file":
            assert dest.read_text() == "keep"

    def test_write_failure_leaves_no_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_write(*args: object, **kwargs: object) -> None:
            raise OSError("injected write failure")

        monkeypatch.setattr(Path, "write_text", fail_write)
        with pytest.raises(OSError, match="injected write failure"):
            export_bundle(basin_file=BASIN, output_dir=tmp_path / "out")
        assert list(tmp_path.iterdir()) == []

    def test_destination_created_during_staging_is_not_overwritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.cli import export_nepal_demo as module

        publish = module.publish_directory
        dest = tmp_path / "out"

        def concurrent_destination(staged: Path, destination: Path) -> None:
            destination.mkdir()
            publish(staged, destination)

        monkeypatch.setattr(module, "publish_directory", concurrent_destination)
        with pytest.raises(FileExistsError, match="out"):
            export_bundle(basin_file=BASIN, output_dir=dest)
        assert dest.is_dir()
        assert list(dest.iterdir()) == []
        assert list(tmp_path.iterdir()) == [dest]
