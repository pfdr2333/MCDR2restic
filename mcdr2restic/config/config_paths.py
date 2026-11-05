# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
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

    # 函数内导入避免与 state_store 的模块级循环依赖（state_store 导入本模块）
    from mcdr2restic.config.state_store import save_text_file_atomically

    # 首次生成同样走原子写：中断留下的半截 YAML 会让下次启动报 yaml_invalid（P1-8）
    save_text_file_atomically(config_path, get_default_config_template(language))
    warn_about_legacy_config_if_needed(server)


def warn_about_legacy_config_if_needed(server: PluginServerInterface):
    legacy_path = get_data_file_path(server, LEGACY_CONFIG_NAME)
    if os.path.exists(legacy_path):
        server.logger.warning(
            server_tr(
                server,
                "warn.config.legacy_json_detected",
                config_name=CONFIG_NAME,
                legacy_name=LEGACY_CONFIG_NAME,
            )
        )
        return
    server.logger.info(
        server_tr(
            server,
            "info.config.default_created",
            path=get_data_file_path(server, CONFIG_NAME),
        )
    )


def get_data_file_path(server: PluginServerInterface, file_name: str) -> str:
    return os.path.join(server.get_data_folder(), file_name)
