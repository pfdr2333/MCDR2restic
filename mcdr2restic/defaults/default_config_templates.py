# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
from typing import List, Optional

import yaml

from mcdr2restic.core.i18n import tr
from mcdr2restic.defaults.message_defaults import build_default_messages


DEFAULT_BACKUP_WORLD_PATHS = (
    './server/world',
    './server/world_nether',
    './server/world_the_end',
)

DEFAULT_BACKUP_SOURCE_MARKER = '    __MCDR2RESTIC_DEFAULT_BACKUP_SOURCES__\n'
MESSAGE_MARKER_PREFIX = '__MCDR2RESTIC_MESSAGE_'
MINECRAFT_SAVE_ALL_COMMENT_MARKER = '__MCDR2RESTIC_MINECRAFT_SAVE_ALL_COMMENT__'
DEFAULT_BACKUP_COMMENT_MARKER = '__MCDR2RESTIC_DEFAULT_BACKUP_COMMENT__'
RESTIC_EXECUTABLE_MARKER = '__MCDR2RESTIC_EXECUTABLE__'
RESTIC_REPOSITORY_MARKER = '__MCDR2RESTIC_REPOSITORY__'
CONFIG_VERSION_MARKER_COMMENT = '__MCDR2RESTIC_CONFIG_VERSION_MARKER_COMMENT__'
UNRESOLVED_MARKER_PATTERN = re.compile(r'__MCDR2RESTIC_[A-Z0-9_]+__')


def get_default_config_template(language: str, base_directory: Optional[str] = None) -> str:
    template = tr(language, 'template.default_config')
    template = render_platform_placeholders(template, language)
    template = render_default_message_placeholders(template, language)
    template = render_default_backup_sources(template, base_directory or os.getcwd())
    template = add_windows_session_lock_exclude(template, language)
    validate_rendered_config_template(template)
    return template


def render_platform_placeholders(template: str, language: str) -> str:
    replacements = {
        MINECRAFT_SAVE_ALL_COMMENT_MARKER: platform_template_snippet(language, 'template.snippet.minecraft_save_all_comment'),
        DEFAULT_BACKUP_COMMENT_MARKER: platform_template_snippet(language, 'template.snippet.default_backup_comment'),
        RESTIC_EXECUTABLE_MARKER: platform_template_snippet(language, 'template.snippet.restic_executable'),
        RESTIC_REPOSITORY_MARKER: platform_template_snippet(language, 'template.snippet.restic_repository'),
        CONFIG_VERSION_MARKER_COMMENT: tr(language, 'template.snippet.config_version_marker_comment'),
    }
    for marker, value in replacements.items():
        template = replace_required_marker(template, marker, value)
    return template


def platform_template_snippet(language: str, key_prefix: str) -> str:
    platform_name = 'windows' if os.name == 'nt' else 'posix'
    return tr(language, '{}.{}'.format(key_prefix, platform_name))


def render_default_message_placeholders(template: str, language: str) -> str:
    for key, text in build_default_messages(language).items():
        marker = message_marker(key)
        if marker not in template:
            continue
        template = replace_indented_line_marker(template, marker, text)
    return template


def indent_multiline_block(text: str, indent: str) -> str:
    lines = str(text).splitlines() or ['']
    return '\n'.join(indent + line for line in lines)


def render_default_backup_sources(template: str, base_directory: str) -> str:
    lines = [
        '    - {}\n'.format(yaml_path_scalar(display_backup_source_path(path)))
        for path in get_default_backup_source_paths(base_directory)
    ]
    return replace_required_marker(template, DEFAULT_BACKUP_SOURCE_MARKER, ''.join(lines))


def get_default_backup_source_paths(base_directory: str) -> List[str]:
    paths = [DEFAULT_BACKUP_WORLD_PATHS[0]]
    if all(is_generation_path_directory(base_directory, path) for path in DEFAULT_BACKUP_WORLD_PATHS):
        paths.extend(DEFAULT_BACKUP_WORLD_PATHS[1:])
    return paths


def is_generation_path_directory(base_directory: str, relative_path: str) -> bool:
    return os.path.isdir(resolve_generation_relative_path(base_directory, relative_path))


def resolve_generation_relative_path(base_directory: str, relative_path: str) -> str:
    path = str(relative_path).strip()
    if path.startswith('./') or path.startswith('.\\'):
        path = path[2:]
    parts = [part for part in re.split(r'[\\/]+', path) if part and part != '.']
    return os.path.join(base_directory, *parts)


def display_backup_source_path(relative_path: str) -> str:
    text = str(relative_path).strip()
    if os.name != 'nt':
        return text
    return text.replace('./', '.\\').replace('/', '\\')


def yaml_path_scalar(path: str) -> str:
    text = str(path)
    if os.name == 'nt':
        return "'{}'".format(text.replace("'", "''"))
    return json.dumps(text, ensure_ascii=False)


def add_windows_session_lock_exclude(template: str, language: str) -> str:
    if os.name != 'nt':
        return template
    marker = '    - "--tag"\n'
    if marker not in template:
        return template
    comment = tr(language, 'template.snippet.session_lock_exclude_comment')
    block = '{}\n    - "--exclude"\n    - "session.lock"\n'.format(comment)
    return template.replace(marker, block + marker, 1)


def replace_required_marker(template: str, marker: str, replacement: str) -> str:
    if marker not in template:
        raise ValueError('Missing config template marker: {}'.format(marker))
    return template.replace(marker, replacement)


def replace_indented_line_marker(template: str, marker: str, text: str) -> str:
    pattern = re.compile(r'(?m)^(?P<indent>[ \t]*){}$'.format(re.escape(marker)))
    match = pattern.search(template)
    if match is None:
        raise ValueError('Missing config template marker line: {}'.format(marker))

    replacement = indent_multiline_block(text, match.group('indent'))
    return pattern.sub(lambda _: replacement, template, count=1)


def message_marker(key: str) -> str:
    return '{}{}__'.format(MESSAGE_MARKER_PREFIX, key)


def validate_rendered_config_template(template: str):
    unresolved_markers = sorted(set(UNRESOLVED_MARKER_PATTERN.findall(template)))
    if unresolved_markers:
        raise ValueError('Unresolved config template markers: {}'.format(', '.join(unresolved_markers)))

    try:
        loaded = yaml.safe_load(template) or {}
    except yaml.YAMLError as exc:
        raise ValueError('Rendered default config template is invalid YAML: {}'.format(exc))

    if not isinstance(loaded, dict):
        raise ValueError('Rendered default config template must be a YAML mapping')
