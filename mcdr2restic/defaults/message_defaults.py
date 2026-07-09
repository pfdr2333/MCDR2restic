# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
from typing import Dict

from mcdr2restic.defaults.default_freeze import freeze_default
from mcdr2restic.core.language import is_zh_language


_DEFAULT_MESSAGES_ZH: Dict[str, str] = {
    'backup_start': '{prefix} 备份开始\n触发: {label}\n时间: {start_time}',
    'backup_success': '{prefix} 备份成功\n触发: {label}\n耗时: {duration_seconds} 秒\n结束时间: {end_time}',
    'backup_failure': '{prefix} 备份异常\n触发: {label}\n状态: {status}\n详情: {detail}\n结束时间: {end_time}',
    'backup_skip_no_player': '{prefix} 跳过备份\n本周期没有玩家加入或退出，触发检查时也没有玩家在线',
    'backup_not_ready': '{prefix} 备份异常\n到达备份时间，但 Minecraft 服务端尚未确认正常运行，跳过本次备份',
    'schedule_config_error': '{prefix} 调度配置错误\n计算下次备份时间失败：{error}',
}

_DEFAULT_MESSAGES_EN: Dict[str, str] = {
    'backup_start': '{prefix} Backup started\nTrigger: {label}\nTime: {start_time}',
    'backup_success': '{prefix} Backup completed\nTrigger: {label}\nDuration: {duration_seconds}s\nEnd time: {end_time}',
    'backup_failure': '{prefix} Backup problem\nTrigger: {label}\nStatus: {status}\nDetail: {detail}\nEnd time: {end_time}',
    'backup_skip_no_player': '{prefix} Backup skipped\nNo players joined or left during this period, and nobody was online at the final check',
    'backup_not_ready': '{prefix} Backup problem\nThe schedule fired, but the Minecraft server has not reached startup state. This backup was skipped.',
    'schedule_config_error': '{prefix} Schedule configuration error\nFailed to calculate the next backup time: {error}',
}

DEFAULT_MESSAGES_ZH = freeze_default(_DEFAULT_MESSAGES_ZH)
DEFAULT_MESSAGES_EN = freeze_default(_DEFAULT_MESSAGES_EN)


def build_default_messages(language: str = 'zh_cn') -> Dict[str, str]:
    if is_zh_language(language):
        return copy.deepcopy(_DEFAULT_MESSAGES_ZH)
    return copy.deepcopy(_DEFAULT_MESSAGES_EN)


def get_default_message_template(template_key: str, language: str = 'zh_cn') -> str:
    return build_default_messages(language).get(template_key, template_key)
