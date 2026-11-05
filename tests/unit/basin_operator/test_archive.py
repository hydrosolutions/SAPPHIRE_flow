from __future__ import annotations

import shutil
import stat
import zipfile
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.basin_operator.archive import unpack_package
from sapphire_flow.basin_operator.review import file_hash

if TYPE_CHECKING:
    from pathlib import Path


class TestUnpackPackage:
    def test_realistic_zip_preserves_original(
        self, tmp_path: Path, synthetic_package: Path
    ) -> None:
        archive = tmp_path / "package.zip"
        shutil.make_archive(str(archive.with_suffix("")), "zip", synthetic_package)
        before = file_hash(archive)
        unpack_package(archive, tmp_path / "unpacked")
        assert file_hash(archive) == before
        assert file_hash(tmp_path / "unpacked" / "basins.gpkg") == file_hash(
            synthetic_package / "basins.gpkg"
        )

    @pytest.mark.parametrize(
        "name", ["../escape", "/escape", "nested/manifest.json", "unexpected.txt"]
    )
    def test_rejects_noncontract_members(
        self, tmp_path: Path, synthetic_package: Path, name: str
    ) -> None:
        archive = tmp_path / "package.zip"
        shutil.make_archive(str(archive.with_suffix("")), "zip", synthetic_package)
        with zipfile.ZipFile(archive, "a") as stream:
            stream.writestr(name, "bad")
        with pytest.raises(ValueError, match="root-level"):
            unpack_package(archive, tmp_path / "unpacked")
        assert not (tmp_path / "unpacked").exists()

    def test_rejects_link_member(self, tmp_path: Path, synthetic_package: Path) -> None:
        archive = tmp_path / "package.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            for path in synthetic_package.iterdir():
                if path.name == "README.md":
                    member = zipfile.ZipInfo(path.name)
                    member.create_system = 3
                    member.external_attr = (stat.S_IFLNK | 0o777) << 16
                    stream.writestr(member, "../elsewhere")
                else:
                    stream.write(path, path.name)
        with pytest.raises(ValueError, match="regular files"):
            unpack_package(archive, tmp_path / "unpacked")
