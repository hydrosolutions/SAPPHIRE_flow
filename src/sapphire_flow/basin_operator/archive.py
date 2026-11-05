"""Unpack only the bounded basin-artifact ZIP layout, preserving its source."""

from __future__ import annotations

import shutil
import stat
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_REQUIRED = frozenset(
    {
        "manifest.json",
        "basins.gpkg",
        "static_attributes.parquet",
        "feature_catalog.json",
        "validation_report.json",
        "README.md",
    }
)
_ALLOWED = _REQUIRED | {
    "bands.gpkg",
    "official_geometry_comparison.parquet",
    "checksums.sha256",
}
_MAX_EXPANDED_BYTES = 2 * 1024**3


def unpack_package(archive: Path, destination: Path) -> None:
    if archive.is_symlink():
        raise ValueError("extractor ZIP must not be a symbolic link")
    if destination.exists():
        raise ValueError("unpack destination already exists")
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            names = [member.filename for member in members]
            if (
                len(set(names)) != len(names)
                or not set(names) >= _REQUIRED
                or not set(names) <= _ALLOWED
            ):
                raise ValueError(
                    "extractor ZIP must contain unique root-level contract files only"
                )
            if sum(member.file_size for member in members) > _MAX_EXPANDED_BYTES:
                raise ValueError("extractor ZIP expanded content exceeds 2 GiB")
            for member in members:
                mode = member.external_attr >> 16
                if member.is_dir() or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise ValueError("extractor ZIP must contain regular files only")
                if member.flag_bits & 1:
                    raise ValueError("encrypted extractor ZIP is unsupported")
            destination.mkdir()
            try:
                for member in members:
                    with (
                        package.open(member) as source,
                        (destination / member.filename).open("xb") as output,
                    ):
                        shutil.copyfileobj(source, output, length=1024 * 1024)
            except Exception:
                shutil.rmtree(destination)
                raise
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid extractor ZIP") from exc
