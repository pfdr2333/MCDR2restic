# -*- coding: utf-8 -*-
"""Verify language resources, release metadata, and a packed plugin archive."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from zipfile import ZipFile

try:
    from .pack_plugin import (
        PLUGIN_METADATA_FILE,
        build_archive_filename,
        iter_archive_entries,
        load_plugin_metadata,
    )
except ImportError:
    from pack_plugin import (
        PLUGIN_METADATA_FILE,
        build_archive_filename,
        iter_archive_entries,
        load_plugin_metadata,
    )


TAG_PATTERN = re.compile(r"^v(\d+\.\d+\.\d+)$")
ROOT_FILES = ("README.md", "README_EN.md", "LICENSE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the plugin release archive.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--git-tag", default="")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    archive_path = resolve_archive(Path(args.archive).resolve())
    metadata = load_plugin_metadata(root)
    verify_language_resources(root)
    verify_git_tag(metadata, args.git_tag)
    verify_archive(root, archive_path, metadata)
    print("Plugin verification passed: {}".format(archive_path))
    return 0


def resolve_archive(path: Path) -> Path:
    if path.is_file():
        return path
    archives = sorted(path.glob("*.mcdr"))
    if len(archives) != 1:
        raise ValueError("Expected exactly one .mcdr archive in {}".format(path))
    return archives[0]


def verify_language_resources(root: Path) -> None:
    package_dir = root / "mcdr2restic" / "lang"
    root_dir = root / "lang"
    package_files = {path.name for path in package_dir.glob("*.json")}
    root_files = {path.name for path in root_dir.glob("*.json")}
    if package_files != root_files:
        raise ValueError(
            "Language file sets differ: package={}, root={}".format(
                sorted(package_files), sorted(root_files)
            )
        )
    for filename in sorted(package_files):
        package_path = package_dir / filename
        root_path = root_dir / filename
        with package_path.open("r", encoding="utf8") as file:
            package_messages = json.load(file)
        with root_path.open("r", encoding="utf8") as file:
            root_messages = json.load(file)
        expected = {
            "mcdr2restic." + key: value for key, value in package_messages.items()
        }
        if root_messages != expected:
            raise ValueError("Language resources are out of sync: {}".format(filename))


def verify_git_tag(metadata: dict[str, object], git_tag: str) -> None:
    if not git_tag:
        return
    match = TAG_PATTERN.fullmatch(git_tag)
    if match is None:
        raise ValueError("Git tag must use vX.X.X format: {}".format(git_tag))
    version = str(metadata.get("version") or "").strip()
    if version != match.group(1):
        raise ValueError(
            "Plugin version {} does not match Git tag {}".format(version, git_tag)
        )


def verify_archive(root: Path, archive_path: Path, metadata: dict[str, object]) -> None:
    expected_name = build_archive_filename(metadata)
    if archive_path.name != expected_name:
        raise ValueError(
            "Archive name {} does not match {}".format(archive_path.name, expected_name)
        )

    expected_entries = {
        archive_name: source_path
        for source_path, archive_name in iter_archive_entries(root, metadata)
    }
    with ZipFile(archive_path, "r") as archive:
        actual_entries = set(archive.namelist())
        if actual_entries != set(expected_entries):
            missing = sorted(set(expected_entries) - actual_entries)
            unexpected = sorted(actual_entries - set(expected_entries))
            raise ValueError(
                "Archive entries differ; missing={}, unexpected={}".format(
                    missing, unexpected
                )
            )
        for archive_name, source_path in expected_entries.items():
            if archive.read(archive_name) != source_path.read_bytes():
                raise ValueError("Archive content differs: {}".format(archive_name))

    for root_file in ROOT_FILES:
        if root_file not in expected_entries:
            raise ValueError(
                "Archive is missing required root file: {}".format(root_file)
            )
    if PLUGIN_METADATA_FILE not in expected_entries:
        raise ValueError("Archive is missing plugin metadata")


if __name__ == "__main__":
    raise SystemExit(main())
