# -*- coding: utf-8 -*-
"""Build a cross-platform `.mcdr` archive with importable package paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Sequence
from zipfile import ZIP_DEFLATED, ZipFile


PLUGIN_METADATA_FILE = "mcdreforged.plugin.json"
OPTIONAL_ROOT_FILES = ("requirements.txt",)
DEFAULT_OUTPUT_DIR = "dist"
PACKED_PLUGIN_SUFFIX = ".mcdr"
IGNORED_DIRECTORY_NAMES = frozenset({"__pycache__"})
IGNORED_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, build the archive, and print the output path."""

    args = parse_args(argv)
    root = Path(args.root).resolve()
    output_dir = Path(args.output_dir).resolve()
    archive_path = pack_plugin(root, output_dir)
    print("Packed plugin written to {}".format(archive_path))
    return 0


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line for the local pack helper."""

    parser = argparse.ArgumentParser(
        description="Pack the plugin into a cross-platform .mcdr archive."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Plugin project root that contains mcdreforged.plugin.json",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where the packed plugin should be written",
    )
    return parser.parse_args(argv)


def pack_plugin(root: Path, output_dir: Path) -> Path:
    """Create a packed plugin archive and return its final path."""

    metadata = load_plugin_metadata(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / build_archive_filename(metadata)
    if archive_path.exists():
        archive_path.unlink()
    build_plugin_archive(root, archive_path, metadata)
    return archive_path


def build_plugin_archive(
    root: Path, archive_path: Path, metadata: Dict[str, Any]
) -> None:
    """Write the plugin archive using POSIX paths inside the zip file."""

    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for source_path, archive_name in iter_archive_entries(root, metadata):
            archive.write(source_path, archive_name)


def load_plugin_metadata(root: Path) -> Dict[str, Any]:
    """Read and validate the plugin metadata JSON from the project root."""

    metadata_path = root / PLUGIN_METADATA_FILE
    if not metadata_path.is_file():
        raise FileNotFoundError("Missing plugin metadata: {}".format(metadata_path))
    with metadata_path.open("r", encoding="utf8") as file:
        metadata = json.load(file)
    if not isinstance(metadata, dict):
        raise ValueError("Plugin metadata must be a JSON object")
    plugin_id = str(metadata.get("id") or "").strip()
    if not plugin_id:
        raise ValueError("Plugin metadata must declare a non-empty id")
    package_dir = root / plugin_id
    if not package_dir.is_dir():
        raise FileNotFoundError(
            "Missing plugin package directory: {}".format(package_dir)
        )
    if not (package_dir / "__init__.py").is_file():
        raise FileNotFoundError(
            "Missing plugin package entry file: {}".format(package_dir / "__init__.py")
        )
    return metadata


def build_archive_filename(metadata: Dict[str, Any]) -> str:
    """Resolve the final archive file name from metadata."""

    plugin_id = str(metadata.get("id") or "").strip()
    version = str(metadata.get("version") or "").strip()
    template = str(metadata.get("archive_name") or "{id}_v{version}").strip()
    if not template:
        template = "{id}_v{version}"
    filename = template.format(id=plugin_id, version=version)
    if not filename.endswith(PACKED_PLUGIN_SUFFIX):
        filename += PACKED_PLUGIN_SUFFIX
    return filename


def iter_archive_entries(
    root: Path, metadata: Dict[str, Any]
) -> Iterator[tuple[Path, str]]:
    """Yield unique source files with normalized archive names."""

    seen: set[str] = set()
    for relative_path in iter_required_relative_paths(root, metadata):
        archive_name = normalize_archive_path(relative_path)
        if archive_name in seen:
            continue
        seen.add(archive_name)
        yield root / relative_path, archive_name


def iter_required_relative_paths(
    root: Path, metadata: Dict[str, Any]
) -> Iterator[Path]:
    """Yield every file that must be shipped in the packed plugin."""

    plugin_id = str(metadata.get("id") or "").strip()
    yield Path(PLUGIN_METADATA_FILE)
    for optional_name in OPTIONAL_ROOT_FILES:
        optional_path = root / optional_name
        if optional_path.is_file():
            yield Path(optional_name)
    yield from iter_relative_files(root, Path(plugin_id))
    for resource_name in resource_names(metadata):
        resource_path = Path(resource_name)
        absolute_path = root / resource_path
        if absolute_path.is_dir():
            yield from iter_relative_files(root, resource_path)
            continue
        if absolute_path.is_file():
            yield resource_path
            continue
        raise FileNotFoundError("Missing declared resource: {}".format(absolute_path))


def resource_names(metadata: Dict[str, Any]) -> list[str]:
    """Return normalized resource names from plugin metadata."""

    resources = metadata.get("resources", [])
    if resources is None:
        return []
    if not isinstance(resources, list):
        raise ValueError("Plugin metadata resources must be a list")
    names: list[str] = []
    for item in resources:
        name = str(item or "").strip()
        if not name:
            raise ValueError("Plugin metadata resources cannot contain empty names")
        names.append(name)
    return names


def iter_relative_files(root: Path, relative_dir: Path) -> Iterator[Path]:
    """Yield all files below a relative directory in deterministic order."""

    absolute_dir = root / relative_dir
    if not absolute_dir.is_dir():
        raise FileNotFoundError("Missing directory: {}".format(absolute_dir))
    for absolute_path in sorted(
        path for path in absolute_dir.rglob("*") if path.is_file()
    ):
        if should_skip_pack_file(absolute_path.relative_to(root)):
            continue
        yield absolute_path.relative_to(root)


def normalize_archive_path(relative_path: Path) -> str:
    """Force zip entry names to use `/` so Linux can import packed packages."""

    return relative_path.as_posix()


def should_skip_pack_file(relative_path: Path) -> bool:
    """Skip transient cache files so release archives stay deterministic."""

    if any(part in IGNORED_DIRECTORY_NAMES for part in relative_path.parts):
        return True
    return relative_path.suffix.lower() in IGNORED_FILE_SUFFIXES


if __name__ == "__main__":
    raise SystemExit(main())
