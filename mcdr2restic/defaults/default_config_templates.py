# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import List, Optional

import yaml

from mcdr2restic.defaults.config_template_resources import (
    config_template_text,
    load_base_config_template as load_config_template_text,
)
from mcdr2restic.defaults.default_constants import (
    CONFIG_VERSION,
    DEFAULT_MAINTENANCE_CRON,
)
from mcdr2restic.defaults.message_defaults import build_default_messages


DEFAULT_BACKUP_WORLD_PATHS = (
    "./server/world",
    "./server/world_nether",
    "./server/world_the_end",
)

DEFAULT_BACKUP_SOURCE_MARKER = "    __MCDR2RESTIC_DEFAULT_BACKUP_SOURCES__\n"
MESSAGE_MARKER_PREFIX = "__MCDR2RESTIC_MESSAGE_"
MINECRAFT_SAVE_ALL_COMMENT_MARKER = "__MCDR2RESTIC_MINECRAFT_SAVE_ALL_COMMENT__"
DEFAULT_BACKUP_COMMENT_MARKER = "__MCDR2RESTIC_DEFAULT_BACKUP_COMMENT__"
RESTIC_EXECUTABLE_MARKER = "__MCDR2RESTIC_EXECUTABLE__"
RESTIC_REPOSITORY_MARKER = "__MCDR2RESTIC_REPOSITORY__"
CONFIG_VERSION_MARKER_COMMENT = "__MCDR2RESTIC_CONFIG_VERSION_MARKER_COMMENT__"
LANGUAGE_MARKER = "__MCDR2RESTIC_LANGUAGE__"
UNRESOLVED_MARKER_PATTERN = re.compile(r"__MCDR2RESTIC_[A-Z0-9_]+__")


def get_default_config_template(
    language: str, base_directory: Optional[str] = None
) -> str:
    template = load_base_config_template(language)
    template = render_platform_placeholders(template, language)
    template = replace_required_marker(
        template, LANGUAGE_MARKER, yaml_language_scalar(language)
    )
    template = render_default_message_placeholders(template, language)
    template = render_default_backup_sources(template, base_directory or os.getcwd())
    template = rewrite_maintenance_command_comments(template)
    template = add_windows_session_lock_exclude(template, language)
    template = insert_maintenance_schedule_block(template, language)
    template = render_config_version(template)
    validate_rendered_config_template(template)
    return template


@lru_cache(maxsize=None)
def load_base_config_template(language: str) -> str:
    return load_config_template_text(language)


DEFAULT_CONFIG_TEMPLATE_ZH = load_base_config_template("zh_cn")
DEFAULT_CONFIG_TEMPLATE_EN = load_base_config_template("en_us")


def adapt_default_config_template_for_platform(
    template: str, language: str = "zh_cn"
) -> str:
    return render_platform_placeholders(template, language)


def render_platform_placeholders(template: str, language: str) -> str:
    replacements = {
        MINECRAFT_SAVE_ALL_COMMENT_MARKER: config_template_text(
            language, "template.snippet.minecraft_save_all_comment"
        ),
        DEFAULT_BACKUP_COMMENT_MARKER: config_template_text(
            language, "template.snippet.default_backup_comment"
        ),
        RESTIC_EXECUTABLE_MARKER: config_template_text(
            language, "template.snippet.restic_executable"
        ),
        RESTIC_REPOSITORY_MARKER: config_template_text(
            language, "template.snippet.restic_repository"
        ),
        CONFIG_VERSION_MARKER_COMMENT: config_template_text(
            language, "template.snippet.config_version_marker_comment"
        ),
    }
    for marker, value in replacements.items():
        template = replace_required_marker(template, marker, value)
    return template


def render_default_message_placeholders(template: str, language: str) -> str:
    for key, text in build_default_messages(language).items():
        marker = message_marker(key)
        if marker not in template:
            continue
        template = replace_indented_line_marker(template, marker, text)
    return template


def indent_multiline_block(text: str, indent: str) -> str:
    lines = str(text).splitlines() or [""]
    return "\n".join(indent + line for line in lines)


def render_default_backup_sources(template: str, base_directory: str) -> str:
    lines = [
        "    - {}\n".format(yaml_path_scalar(display_backup_source_path(path)))
        for path in get_default_backup_source_paths(base_directory)
    ]
    return replace_required_marker(
        template, DEFAULT_BACKUP_SOURCE_MARKER, "".join(lines)
    )


def get_default_backup_source_paths(base_directory: str) -> List[str]:
    paths = [DEFAULT_BACKUP_WORLD_PATHS[0]]
    if all(
        is_generation_path_directory(base_directory, path)
        for path in DEFAULT_BACKUP_WORLD_PATHS
    ):
        paths.extend(DEFAULT_BACKUP_WORLD_PATHS[1:])
    return paths


def is_generation_path_directory(base_directory: str, relative_path: str) -> bool:
    return os.path.isdir(
        resolve_generation_relative_path(base_directory, relative_path)
    )


def resolve_generation_relative_path(base_directory: str, relative_path: str) -> str:
    path = str(relative_path).strip()
    if path.startswith("./") or path.startswith(".\\"):
        path = path[2:]
    parts = [part for part in re.split(r"[\\/]+", path) if part and part != "."]
    return os.path.join(base_directory, *parts)


def display_backup_source_path(relative_path: str) -> str:
    text = str(relative_path).strip()
    if os.name != "nt":
        return text
    return text.replace("./", ".\\").replace("/", "\\")


def yaml_path_scalar(path: str) -> str:
    text = str(path)
    if os.name == "nt":
        return "'{}'".format(text.replace("'", "''"))
    return json.dumps(text, ensure_ascii=False)


def yaml_language_scalar(language: str) -> str:
    return json.dumps(str(language or ""), ensure_ascii=False)


def add_windows_session_lock_exclude(template: str, language: str) -> str:
    if os.name != "nt":
        return template
    marker = '    - "--tag"\n'
    if marker not in template:
        return template
    comment = config_template_text(
        language, "template.snippet.session_lock_exclude_comment"
    )
    block = '{}\n    - "--exclude"\n    - "session.lock"\n'.format(comment)
    return template.replace(marker, block + marker, 1)


def rewrite_maintenance_command_comments(template: str) -> str:
    replacements = {
        "# 备份前的仓库维护命令。每一项都会自动在前面加 executable，例如 restic forget ...": (
            "# 仓库维护命令，由 maintenance_schedule 单独调度执行。每一项都会自动在前面加 executable，例如 restic forget ..."
        ),
        "# Repository maintenance commands executed before backup.": (
            "# Repository maintenance commands run from maintenance_schedule, independently of backups."
        ),
    }
    for old, new in replacements.items():
        template = template.replace(old, new)
    return template


def insert_maintenance_schedule_block(template: str, language: str) -> str:
    if "\nmaintenance_schedule:\n" in template:
        return template
    marker = "\n\nupdate_check:\n"
    if marker not in template:
        return template
    return template.replace(marker, maintenance_schedule_block(language) + marker, 1)


def maintenance_schedule_block(language: str) -> str:
    if str(language).lower().startswith("zh"):
        return (
            "\n\nmaintenance_schedule:\n"
            "  # 仓库维护调度。默认每天 03:00 执行一次；cron_expression 留空时仍使用默认值。\n"
            "  # interval_seconds > 0 时使用固定间隔。\n"
            "  # interval_seconds = 0 且 cron_expression = \"0\" 表示关闭维护调度。\n"
            "  interval_seconds: 0\n"
            '  cron_expression: "{}"'.format(DEFAULT_MAINTENANCE_CRON)
        )
    return (
        "\n\nmaintenance_schedule:\n"
        "  # Repository maintenance schedule. Default: run once per day at 03:00; empty cron_expression keeps this default.\n"
        "  # interval_seconds > 0 uses a fixed interval.\n"
        "  # interval_seconds = 0 with cron_expression = \"0\" disables maintenance scheduling.\n"
        "  interval_seconds: 0\n"
        '  cron_expression: "{}"'.format(DEFAULT_MAINTENANCE_CRON)
    )


def render_config_version(template: str) -> str:
    return re.sub(
        r"(?m)^config_version\s*:\s*\d+\s*$",
        "config_version: {}".format(CONFIG_VERSION),
        template,
        count=1,
    )


def replace_required_marker(template: str, marker: str, replacement: str) -> str:
    if marker not in template:
        raise ValueError("Missing config template marker: {}".format(marker))
    return template.replace(marker, replacement)


def replace_indented_line_marker(template: str, marker: str, text: str) -> str:
    pattern = re.compile(r"(?m)^(?P<indent>[ \t]*){}$".format(re.escape(marker)))
    match = pattern.search(template)
    if match is None:
        raise ValueError("Missing config template marker line: {}".format(marker))

    replacement = indent_multiline_block(text, match.group("indent"))
    return pattern.sub(lambda _: replacement, template, count=1)


def message_marker(key: str) -> str:
    return "{}{}__".format(MESSAGE_MARKER_PREFIX, key)


def validate_rendered_config_template(template: str):
    unresolved_markers = sorted(set(UNRESOLVED_MARKER_PATTERN.findall(template)))
    if unresolved_markers:
        raise ValueError(
            "Unresolved config template markers: {}".format(
                ", ".join(unresolved_markers)
            )
        )

    try:
        loaded = yaml.safe_load(template) or {}
    except yaml.YAMLError as exc:
        raise ValueError(
            "Rendered default config template is invalid YAML: {}".format(exc)
        )

    if not isinstance(loaded, dict):
        raise ValueError("Rendered default config template must be a YAML mapping")
