# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.defaults.default_config_templates import get_default_config_template
from mcdr2restic.defaults.default_constants import (
    CONFIG_NAME,
    LEGACY_CONFIG_NAME,
)


def ensure_config_file_exists(server: PluginServerInterface, language: str):
    data_folder = server.get_data_folder()
    os.makedirs(data_folder, exist_ok=True)
    config_path = get_data_file_path(server, CONFIG_NAME)
    if os.path.exists(config_path):
        return

    with open(config_path, 'w', encoding='utf8') as file:
        file.write(get_default_config_template(language))
    warn_about_legacy_config_if_needed(server)


def warn_about_legacy_config_if_needed(server: PluginServerInterface):
    legacy_path = get_data_file_path(server, LEGACY_CONFIG_NAME)
    if os.path.exists(legacy_path):
        server.logger.warning(
            '已生成新的 YAML 配置 {}。检测到旧 JSON 配置 {}，插件不会继续使用旧文件，请手动迁移需要的配置项。'.format(
                CONFIG_NAME, LEGACY_CONFIG_NAME
            )
        )
        return
    server.logger.info('已生成默认配置文件 {}'.format(get_data_file_path(server, CONFIG_NAME)))


def get_data_file_path(server: PluginServerInterface, file_name: str) -> str:
    return os.path.join(server.get_data_folder(), file_name)
