# -*- coding: utf-8 -*-
import copy
import importlib
import importlib.metadata
import bz2
import json
import os
import platform
import re
import shlex
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from mcdreforged.api.all import *


BOOTSTRAP_LOGS: List[str] = []


def _bootstrap_log(message: str):
    BOOTSTRAP_LOGS.append(message)
    print('[MCDR2Restic bootstrap] {}'.format(message))


def _has_distribution(requirement_name: str, min_version: str) -> bool:
    try:
        from packaging.version import Version
        version = importlib.metadata.version(requirement_name)
        return Version(version) >= Version(min_version)
    except Exception:
        return False


def _pip_install(requirements: List[str]) -> bool:
    base_command = [
        sys.executable,
        '-m',
        'pip',
        'install',
        '--disable-pip-version-check',
        '--no-input'
    ]
    extra_args = shlex.split(os.environ.get('MCDR2RESTIC_PIP_ARGS', ''))
    index_url = os.environ.get('MCDR2RESTIC_PIP_INDEX_URL', '').strip()
    commands = []
    command = base_command + extra_args
    if index_url:
        command = command + ['-i', index_url]
    commands.append(command + requirements)
    if not index_url:
        commands.append(base_command + extra_args + ['-i', 'https://pypi.tuna.tsinghua.edu.cn/simple'] + requirements)

    for index, command in enumerate(commands, start=1):
        _bootstrap_log('正在尝试安装 Python 依赖（第 {} 次）: {}'.format(index, ' '.join(command[4:])))
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if result.returncode == 0:
            _bootstrap_log('Python 依赖安装完成')
            importlib.invalidate_caches()
            return True
        _bootstrap_log('pip 安装失败，退出码 {}:\n{}'.format(result.returncode, tail_bootstrap_text(result.stdout, 2000)))
    return False


def tail_bootstrap_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text.strip()
    return '...\n{}'.format(text[-max_chars:].strip())


def _ensure_python_dependencies():
    missing: List[str] = []
    if not _has_distribution('PyYAML', '6.0'):
        missing.append('PyYAML>=6.0')
    if not _has_distribution('websocket-client', '1.8.0'):
        missing.append('websocket-client>=1.8.0')
    if missing and not _pip_install(missing):
        raise RuntimeError('MCDR2Restic 无法自动安装 Python 依赖: {}'.format(', '.join(missing)))


_ensure_python_dependencies()

import yaml

try:
    import websocket as websocket_client
    if not hasattr(websocket_client, 'WebSocketApp'):
        _bootstrap_log('检测到 websocket 模块但缺少 WebSocketApp，正在尝试卸载错误的 websocket 包并重装 websocket-client')
        subprocess.run(
            [sys.executable, '-m', 'pip', 'uninstall', '-y', 'websocket'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        _pip_install(['--force-reinstall', 'websocket-client>=1.8.0'])
        importlib.invalidate_caches()
        import websocket as websocket_client
        if not hasattr(websocket_client, 'WebSocketApp'):
            websocket_client = None
except Exception:  # pragma: no cover - optional runtime dependency
    websocket_client = None


PLUGIN_ID = 'mcdr2restic'
CONFIG_NAME = 'config.yml'
STATE_NAME = 'state.yml'
LEGACY_CONFIG_NAME = 'config.json'
CONFIG_VERSION = 5
RESTIC_FALLBACK_RELEASE: Dict[str, Any] = {
    'tag_name': 'v0.19.1',
    'assets': [
        {
            'name': 'restic_0.19.1_linux_amd64.bz2',
            'browser_download_url': 'https://github.com/restic/restic/releases/download/v0.19.1/restic_0.19.1_linux_amd64.bz2'
        },
        {
            'name': 'restic_0.19.1_windows_amd64.zip',
            'browser_download_url': 'https://github.com/restic/restic/releases/download/v0.19.1/restic_0.19.1_windows_amd64.zip'
        }
    ]
}

DEFAULT_CONFIG: Dict[str, Any] = {
    'enabled': True,
    'command': {
        'root': '!!restic',
        'aliases': ['!!m2r'],
        'permission_level': 3
    },
    'schedule': {
        'interval_seconds': 0,
        'cron_expression': '0 0 0,3,6,9,12,15,18,21 * * *',
        'require_player_activity_in_wait_period': True,
        'online_check_command': 'list'
    },
    'force_schedule': {
        'interval_seconds': 0,
        'cron_expression': '0'
    },
    'minecraft': {
        'save_off_command': 'save-off',
        'save_all_command': 'save-all flush',
        'save_on_command': 'save-on',
        'wait_after_save_off_seconds': 2,
        'wait_after_save_all_seconds': 10,
        'wait_after_save_on_seconds': 1
    },
    'restic': {
        'executable': './restic',
        'working_directory': '',
        'repository': './restic-repo',
        'password': '123456',
        'password_file': '',
        'auto_download': True,
        'download_version': 'latest',
        'download_proxy_prefixes': [
            'https://gh.llkk.cc/',
            'https://gh-proxy.com/',
            'https://hub.gitmirror.com/'
        ],
        'download_timeout_seconds': 120,
        'auto_init_local_repository': True,
        'environment': {},
        'maintenance_commands': [
            ['forget', '--keep-daily', '7', '--prune']
        ],
        'backup_command': [
            'backup',
            './server/world',
            './server/world_nether',
            './server/world_the_end',
            '--tag',
            'minecraft',
            '--host',
            'mcdr2Restic'
        ],
        'timeout_seconds': 3600,
        'success_exit_codes': [0],
        'error_regexes': [
            '(?i)^fatal:',
            '(?i)^error(?:s)?\\b(?!:\\s*0\\b)',
            '(?i)\\b(permission denied|input/output error|read error|unreadable|failed to|unable to)\\b',
            '(?i)\\bno such file or directory\\b'
        ],
        'ignore_error_regexes': [
            '(?i)errors?:\\s*0\\b',
            '(?i)no errors? (?:were )?found'
        ],
        'max_output_chars_in_notification': 1800
    },
    'onebot': {
        'enabled': False,
        'ws_url': 'ws://127.0.0.1:8777',
        'access_token': '',
        'use_header_auth': False,
        'admin_qqs': [123456789],
        'message_prefix': '[MCDR2Restic]',
        'connect_timeout_seconds': 10,
        'send_timeout_seconds': 10,
        'reconnect_interval_seconds': 5
    },
    'discord': {
        'enabled': False,
        'webhook_url': '',
        'username': 'MCDR2Restic',
        'avatar_url': '',
        'message_prefix': '[MCDR2Restic]',
        'mention_user_ids': [],
        'mention_role_ids': [],
        'mention_everyone': False,
        'send_timeout_seconds': 10
    },
    'notification': {
        'notify_on_start': True,
        'notify_on_success': True,
        'notify_on_failure': True,
        'notify_on_skip': False
    },
    'messages': {
        'backup_start': '{prefix} 备份开始\n触发: {label}\n时间: {start_time}',
        'backup_success': '{prefix} 备份成功\n触发: {label}\n耗时: {duration_seconds} 秒\n结束时间: {end_time}',
        'backup_failure': '{prefix} 备份异常\n触发: {label}\n状态: {status}\n详情: {detail}\n结束时间: {end_time}',
        'backup_skip_no_player': '{prefix} 跳过备份\n本周期没有玩家加入或退出，触发检查时也没有玩家在线',
        'backup_not_ready': '{prefix} 备份异常\n到达备份时间，但 Minecraft 服务端尚未确认正常运行，跳过本次备份',
        'schedule_config_error': '{prefix} 调度配置错误\n计算下次备份时间失败：{error}'
    },
    'config_version': CONFIG_VERSION
}

DEFAULT_RUNTIME: Dict[str, Any] = {
    'player_activity_since_last_backup': False,
    'player_joined_since_last_backup': False,
    'player_joined_since_last_check': False,
    'player_left_since_last_check': False,
    'known_online_players': [],
    'current_online_players': 0,
    'last_online_check': None,
    'last_online_check_source': None,
    'last_online_check_result': None,
    'last_player_joined': None,
    'last_player_left': None,
    'last_backup_start_time': None,
    'last_backup_end_time': None,
    'last_backup_status': 'never',
    'last_backup_message': ''
}

DEFAULT_MESSAGES_EN: Dict[str, str] = {
    'backup_start': '{prefix} Backup started\nTrigger: {label}\nTime: {start_time}',
    'backup_success': '{prefix} Backup completed\nTrigger: {label}\nDuration: {duration_seconds}s\nEnd time: {end_time}',
    'backup_failure': '{prefix} Backup problem\nTrigger: {label}\nStatus: {status}\nDetail: {detail}\nEnd time: {end_time}',
    'backup_skip_no_player': '{prefix} Backup skipped\nNo players joined or left during this period, and nobody was online at the final check',
    'backup_not_ready': '{prefix} Backup problem\nThe schedule fired, but the Minecraft server has not reached startup state. This backup was skipped.',
    'schedule_config_error': '{prefix} Schedule configuration error\nFailed to calculate the next backup time: {error}'
}

CONFIG: Dict[str, Any] = {}
STATE: Dict[str, Any] = {}
CONFIG_LOCK = threading.RLock()
SERVER: Optional[PluginServerInterface] = None
SERVER_READY = False

SCHEDULER: Optional['BackupScheduler'] = None
ONEBOT: Optional['OneBotClient'] = None
DISCORD: Optional['DiscordWebhookClient'] = None

BACKUP_LOCK = threading.Lock()
BACKUP_CANCEL = threading.Event()
CURRENT_PROCESS: Optional[subprocess.Popen] = None
CURRENT_BACKUP_THREAD: Optional[threading.Thread] = None
CURRENT_BACKUP_LABEL: Optional[str] = None
PLUGIN_STOPPING = threading.Event()


def default_config_for_language(language: str) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if not is_zh_language(language):
        cfg['messages'] = copy.deepcopy(DEFAULT_MESSAGES_EN)
    return cfg


def is_zh_language(language: str) -> bool:
    normalized = str(language or '').lower().replace('-', '_')
    return normalized.startswith('zh')


def get_mcdr_language(server: Optional[PluginServerInterface]) -> str:
    if server is None:
        return 'zh_cn'
    try:
        return str(server.get_mcdr_language())
    except Exception:
        return 'zh_cn'


def get_default_config_template(language: str) -> str:
    if is_zh_language(language):
        template = DEFAULT_CONFIG_TEMPLATE_ZH
    else:
        template = DEFAULT_CONFIG_TEMPLATE_EN
    return adapt_default_config_template_for_platform(template)


def adapt_default_config_template_for_platform(template: str) -> str:
    if os.name != 'nt':
        return template
    replacements = [
        ('# Linux Java 版示例。若服务端不支持 save-all flush，可改为 save-all。', '# Windows Java 版示例。若服务端不支持 save-all flush，可改为 save-all。'),
        ('# Linux Java Edition example. If your server does not support save-all flush, use save-all instead.', '# Windows Java Edition example. If your server does not support save-all flush, use save-all instead.'),
        ('executable: "./restic"', "executable: '.\\restic.exe'"),
        ('repository: "./restic-repo"', "repository: '.\\restic-repo'"),
        ('    - "./server/world"', "    - '.\\server\\world'"),
        ('    - "./server/world_nether"', "    - '.\\server\\world_nether'"),
        ('    - "./server/world_the_end"', "    - '.\\server\\world_the_end'")
    ]
    for old, new in replacements:
        template = template.replace(old, new)
    return template


DEFAULT_CONFIG_TEMPLATE_ZH = r"""# MCDR2Restic 配置文件
# state.yml为运行时状态文件，请勿更改

# 总开关。
enabled: true

command:
  # MCDR 命令根节点
  root: "!!restic"
  #别名
  aliases:
    - "!!m2r"
  # MCDR 权限等级。
  permission_level: 3

schedule:
  # interval_seconds > 0 时使用固定间隔。
  # interval_seconds = 0 时使用下面的 6 位 cron_expression。
  interval_seconds: 0
  # 6 位 cron：秒 分 时 日 月 周。默认每 3 小时检查一次正常备份。
  cron_expression: "0 0 0,3,6,9,12,15,18,21 * * *"
  # 正常定时备份是否启用玩家活动感知。
  # 触发时会执行一次 list 检查当前在线人数；join/left 事件会记录本周期是否有人进入或退出。
  # 判断流程：
  # - 本周期有人加入：备份
  # - 无人加入，list 检查在线人数为 0：跳过
  # - 无人加入，list 检查在线人数不为 0：备份
  # - 无人加入，但有人退出，即使 list 检查为 0：备份
  require_player_activity_in_wait_period: true
  # 用于查询当前在线人数的 Minecraft 命令。默认 list。
  # 通过 server.rcon_query 执行；如果 RCON 不可用，将只依赖 join/left 事件估算。
  online_check_command: "list"

force_schedule:
  # 强制备份调度，不遵循玩家活动感知。默认关闭。
  # interval_seconds > 0 时使用固定间隔。
  # interval_seconds = 0 且 cron_expression 不是 "0" 时使用 6 位 cron。
  # interval_seconds = 0 且 cron_expression = "0" 表示关闭强制备份。
  interval_seconds: 0
  cron_expression: "0"

minecraft:
  # Linux Java 版示例。若服务端不支持 save-all flush，可改为 save-all。
  save_off_command: "save-off"
  save_all_command: "save-all flush"
  save_on_command: "save-on"
  wait_after_save_off_seconds: 2
  wait_after_save_all_seconds: 10
  wait_after_save_on_seconds: 1

restic:
  # 所有命令都以 executable 开头执行。
  # 默认表示 MCDR 工作目录下的 restic 可执行文件。
  executable: "./restic"
  # restic 进程工作目录。
  # 留空字符串或 null 表示继承 MCDR 当前工作目录。
  working_directory: ""
  # restic 存储库。默认使用 MCDR 工作目录下的本地仓库。
  repository: "./restic-repo"
  # 存储库密码。优先使用这里的直接密码；留空字符串时再看 password_file。
  # 示例密码方便开箱测试，正式使用请改成自己的强密码。
  password: "123456"
  # 密码文件。仅当 password 为空时生效。
  password_file: ""
  # 如果仍使用默认 executable 路径且找不到 restic，则自动下载 restic。
  # 仅支持 Linux amd64 和 Windows amd64；其他系统会跳过。
  auto_download: true
  # restic 下载版本。latest 表示 GitHub 最新 release，也可以写 v0.18.0 这类版本号。
  download_version: "latest"
  # 下载加速代理前缀。会先尝试官方 GitHub 下载，再按顺序尝试这里的代理。
  download_proxy_prefixes:
    - "https://gh.llkk.cc/"
    - "https://gh-proxy.com/"
    - "https://hub.gitmirror.com/"
  download_timeout_seconds: 120
  # 本地存储库不存在或缺少 config 时自动执行 restic init。
  # S3/B2/rest/sftp/rclone 等远端仓库不会自动初始化。
  auto_init_local_repository: true
  # 执行 restic 时会继承 MCDR 进程环境，并叠加这里的变量。
  # 值为 null 表示删除继承来的同名变量。
  # repository/password/password_file 会自动写入 RESTIC_REPOSITORY/RESTIC_PASSWORD/RESTIC_PASSWORD_FILE。
  # S3/B2 等后端可以在这里加入对应云服务环境变量。
  environment: {}
  # 备份前的仓库维护命令。每一项都会自动在前面加 executable，例如 restic forget ...
  maintenance_commands:
    - [
        "forget",
        "--keep-daily", "7",
        "--prune"
      ]
  # 备份命令。同样会自动在前面加 executable，例如 restic backup ...
  backup_command:
    - "backup"
    - "./server/world"
    - "./server/world_nether"
    - "./server/world_the_end"
    - "--tag"
    - "minecraft"
    - "--host"
    - "mcdr2Restic"
  # 整个备份流程共用的超时，默认一小时。
  timeout_seconds: 3600
  # restic 退出码。0 成功；3 表示有源文件不可读，本插件默认按失败处理。
  success_exit_codes:
    - 0
  # 即使退出码为 0，只要输出匹配这些规则，也按异常处理。
  error_regexes:
    - '(?i)^fatal:'
    - '(?i)^error(?:s)?\b(?!:\s*0\b)'
    - '(?i)\b(permission denied|input/output error|read error|unreadable|failed to|unable to)\b'
    - '(?i)\bno such file or directory\b'
  ignore_error_regexes:
    - '(?i)errors?:\s*0\b'
    - '(?i)no errors? (?:were )?found'
  max_output_chars_in_notification: 1800

onebot:
  # OneBot V11 正向 WebSocket。填好 admin_qqs 后把 enabled 改为 true。
  enabled: false
  # WebSocket 服务器地址
  ws_url: "ws://127.0.0.1:8777"
  # 用于连接验证的令牌
  access_token: ""
  # true 使用 Header: Authorization: Bearer xxx；false 使用 URL 参数 access_token，NapCat 常用。
  use_header_auth: false
  #需要通知的QQ
  admin_qqs:
    - 123456789
  #消息前缀
  message_prefix: "[MCDR2Restic]"
  # 网络超时与重连策略
  connect_timeout_seconds: 10
  send_timeout_seconds: 10
  reconnect_interval_seconds: 5

discord:
  # Discord Webhook 通知。填好 webhook_url 后把 enabled 改为 true。
  enabled: false
  # Discord 频道 Webhook URL。
  webhook_url: ""
  # Webhook 显示名称和头像，可留空使用 Discord Webhook 默认值。
  username: "MCDR2Restic"
  avatar_url: ""
  # Discord 通知消息前缀，消息正文仍使用 messages 中的模板。
  message_prefix: "[MCDR2Restic]"
  # 可选提及对象。默认不提及任何人，避免误 ping。
  mention_user_ids: []
  mention_role_ids: []
  mention_everyone: false
  send_timeout_seconds: 10

notification:
  # 备份生命周期通知策略
  # 控制在哪些备份阶段向上述管理员推送 QQ 消息
  notify_on_start: true # 备份任务开始时是否发送通知
  notify_on_success: true # 备份任务成功完成时是否发送通知
  notify_on_failure: true # 备份任务失败时是否发送通知
  notify_on_skip: false # 备份任务被跳过（如无需备份）时是否发送通知

messages:
  # 管理员通知消息模板，可自行修改文本。
  # 可用变量：{prefix} {label} {start_time} {end_time} {duration_seconds} {status} {message} {detail} {error}
  # 如果需要输出字面量花括号，请写成 {{ 或 }}。
  backup_start: |-
    {prefix} 备份开始
    触发: {label}
    时间: {start_time}
  backup_success: |-
    {prefix} 备份成功
    触发: {label}
    耗时: {duration_seconds} 秒
    结束时间: {end_time}
  backup_failure: |-
    {prefix} 备份异常
    触发: {label}
    状态: {status}
    详情: {detail}
    结束时间: {end_time}
  backup_skip_no_player: |-
    {prefix} 跳过备份
    本周期没有玩家加入或退出，触发检查时也没有玩家在线
  backup_not_ready: |-
    {prefix} 备份异常
    到达备份时间，但 Minecraft 服务端尚未确认正常运行，跳过本次备份
  schedule_config_error: |-
    {prefix} 调度配置错误
    计算下次备份时间失败：{error}

# 配置文件版本标识。请保留在文件尾部，方便后续迁移。
config_version: 5
"""


DEFAULT_CONFIG_TEMPLATE_EN = r"""# MCDR2Restic configuration
# state.yml is the runtime state file. Do not modify it.

# Master switch.
enabled: true

command:
  # MCDR command root
  root: "!!restic"
  # Aliases
  aliases:
    - "!!m2r"
  # MCDR permission level.
  permission_level: 3

schedule:
  # interval_seconds > 0 uses a fixed interval.
  # interval_seconds = 0 enables the 6-field cron_expression below.
  interval_seconds: 0
  # 6-field cron: second minute hour day month weekday. Default: check normal backups every 3 hours.
  cron_expression: "0 0 0,3,6,9,12,15,18,21 * * *"
  # Whether normal scheduled backups use player activity sensing.
  # At trigger time, the plugin runs list once to check the current online count;
  # join/left events record whether anyone entered or left during this period.
  # Flow:
  # - someone joined this period: back up
  # - nobody joined and list reports 0 online: skip
  # - nobody joined and list reports non-zero online: back up
  # - nobody joined but someone left, even if list reports 0 online: back up
  require_player_activity_in_wait_period: true
  # Minecraft command used to query current online players. Default: list.
  # It is executed with server.rcon_query. If RCON is unavailable, the plugin falls
  # back to join/left event estimates.
  online_check_command: "list"

force_schedule:
  # Forced backup schedule. It ignores player activity sensing and is disabled by default.
  # interval_seconds > 0 uses a fixed interval.
  # interval_seconds = 0 with cron_expression not equal to "0" enables the 6-field cron.
  # interval_seconds = 0 with cron_expression = "0" disables forced backups.
  interval_seconds: 0
  cron_expression: "0"

minecraft:
  # Linux Java Edition example. If your server does not support save-all flush, use save-all instead.
  save_off_command: "save-off"
  save_all_command: "save-all flush"
  save_on_command: "save-on"
  wait_after_save_off_seconds: 2
  wait_after_save_all_seconds: 10
  wait_after_save_on_seconds: 1

restic:
  # Every command is executed with executable prepended automatically.
  # Default: the restic executable placed in MCDR's working directory.
  executable: "./restic"
  # Working directory for the restic process.
  # Empty string or null means inheriting MCDR's current working directory.
  working_directory: ""
  # restic repository. Default: a local repository in MCDR's working directory.
  repository: "./restic-repo"
  # Repository password. This direct password has priority; when empty, password_file is used.
  # The sample password is only for easy first-run testing. Change it for production.
  password: "123456"
  # Password file. Used only when password is empty.
  password_file: ""
  # Automatically download restic if executable still uses the default path and is missing.
  # Only Linux amd64 and Windows amd64 are supported; other systems are skipped.
  auto_download: true
  # restic download version. latest uses the latest GitHub release; version tags such as v0.18.0 are also accepted.
  download_version: "latest"
  # Download proxy prefixes. The plugin tries official GitHub first, then these proxies in order.
  download_proxy_prefixes:
    - "https://gh.llkk.cc/"
    - "https://gh-proxy.com/"
    - "https://hub.gitmirror.com/"
  download_timeout_seconds: 120
  # Automatically run restic init when a local repository is missing or lacks config.
  # Remote repositories such as S3/B2/rest/sftp/rclone are not initialized automatically.
  auto_init_local_repository: true
  # restic inherits MCDR's process environment and then applies these variables.
  # A value of null removes an inherited variable with the same name.
  # repository/password/password_file automatically set RESTIC_REPOSITORY,
  # RESTIC_PASSWORD and RESTIC_PASSWORD_FILE.
  # You can also add cloud backend environment variables here, such as those for S3/B2.
  environment: {}
  # Repository maintenance commands executed before backup.
  # executable is prepended automatically to each command, e.g.:
  # restic forget ...
  maintenance_commands:
    - [
        "forget",
        "--keep-daily", "7",
        "--prune"
      ]
  # Backup command.
  # executable is prepended automatically, e.g.:
  # restic backup ...
  backup_command:
    - "backup"
    - "./server/world"
    - "./server/world_nether"
    - "./server/world_the_end"
    - "--tag"
    - "minecraft"
    - "--host"
    - "mcdr2Restic"
  # Timeout shared by the entire backup workflow. Default: one hour.
  timeout_seconds: 3600
  # restic exit codes.
  # 0 indicates success.
  # 3 indicates some source files were unreadable and is treated as failure by default.
  success_exit_codes:
    - 0
  # Even if the exit code is 0, matching any of these patterns marks the run as abnormal.
  error_regexes:
    - '(?i)^fatal:'
    - '(?i)^error(?:s)?\b(?!:\s*0\b)'
    - '(?i)\b(permission denied|input/output error|read error|unreadable|failed to|unable to)\b'
    - '(?i)\bno such file or directory\b'
  ignore_error_regexes:
    - '(?i)errors?:\s*0\b'
    - '(?i)no errors? (?:were )?found'
  max_output_chars_in_notification: 1800

onebot:
  # OneBot V11 forward WebSocket.
  # After filling admin_qqs, set enabled to true.
  enabled: false
  # WebSocket server address
  ws_url: "ws://127.0.0.1:8777"
  # Token used for connection authentication
  access_token: ""
  # true uses Header: Authorization: Bearer xxx;
  # false uses the URL parameter access_token (commonly used by NapCat).
  use_header_auth: false
  # QQ accounts to receive notifications
  admin_qqs:
    - 123456789
  # Message prefix
  message_prefix: "[MCDR2Restic]"
  # Network timeout and reconnect strategy
  connect_timeout_seconds: 10
  send_timeout_seconds: 10
  reconnect_interval_seconds: 5

discord:
  # Discord webhook notifications. Fill webhook_url, then set enabled to true.
  enabled: false
  # Discord channel webhook URL.
  webhook_url: ""
  # Webhook display name and avatar. Leave avatar_url empty to use the Discord webhook default.
  username: "MCDR2Restic"
  avatar_url: ""
  # Discord notification prefix. Message bodies still use templates under messages.
  message_prefix: "[MCDR2Restic]"
  # Optional mentions. Empty by default to avoid accidental pings.
  mention_user_ids: []
  mention_role_ids: []
  mention_everyone: false
  send_timeout_seconds: 10

notification:
  # Backup lifecycle notification policy.
  # Controls at which backup stages QQ notifications are sent to the administrators above.
  notify_on_start: true # Send a notification when a backup task starts.
  notify_on_success: true # Send a notification when a backup task completes successfully.
  notify_on_failure: true # Send a notification when a backup task fails.
  notify_on_skip: false # Send a notification when a backup task is skipped (e.g. backup not required).

messages:
  # Administrator notification message templates. You may freely customize the text.
  # Available variables:
  # {prefix} {label} {start_time} {end_time} {duration_seconds} {status} {message} {detail} {error}
  # To output literal braces, use {{ or }}.
  backup_start: |-
    {prefix} Backup started
    Trigger: {label}
    Time: {start_time}
  backup_success: |-
    {prefix} Backup completed
    Trigger: {label}
    Duration: {duration_seconds}s
    End time: {end_time}
  backup_failure: |-
    {prefix} Backup problem
    Trigger: {label}
    Status: {status}
    Detail: {detail}
    End time: {end_time}
  backup_skip_no_player: |-
    {prefix} Backup skipped
    No players joined or left during this period, and nobody was online at the final check
  backup_not_ready: |-
    {prefix} Backup problem
    The scheduled backup time was reached, but the Minecraft server has not yet been confirmed to be fully running. This backup was skipped.
  schedule_config_error: |-
    {prefix} Schedule configuration error
    Failed to calculate the next backup time: {error}

# Config file version marker. Keep it at the end for future migrations.
config_version: 5
"""


class BackupProblem(Exception):
    pass


class BackupCanceled(BackupProblem):
    pass


class CronError(ValueError):
    pass


@dataclass
class ResticCommandResult:
    phase: str
    args: List[str]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class CronExpression:
    def __init__(self, expression: str):
        self.expression = expression.strip()
        fields = self.expression.split()
        if len(fields) != 6:
            raise CronError('cron 表达式必须是 6 字段：秒 分 时 日 月 周')

        self.seconds, _ = self._parse_field(fields[0], 0, 59)
        self.minutes, _ = self._parse_field(fields[1], 0, 59)
        self.hours, _ = self._parse_field(fields[2], 0, 23)
        self.days, self.any_day = self._parse_field(fields[3], 1, 31)
        self.months, _ = self._parse_field(fields[4], 1, 12)
        self.weekdays, self.any_weekday = self._parse_field(fields[5], 0, 7, sunday_7=True)

    @staticmethod
    def _parse_field(text: str, minimum: int, maximum: int, sunday_7: bool = False) -> Tuple[Set[int], bool]:
        values: Set[int] = set()
        wildcard = text == '*'
        for part in text.split(','):
            part = part.strip()
            if not part:
                raise CronError('cron 字段包含空片段')

            if '/' in part:
                base, step_text = part.split('/', 1)
                try:
                    step = int(step_text)
                except ValueError:
                    raise CronError('cron 步长不是整数: {}'.format(step_text))
                if step <= 0:
                    raise CronError('cron 步长必须大于 0')
            else:
                base, step = part, 1

            if base == '*':
                start, end = minimum, maximum
                wildcard = True
            elif '-' in base:
                start_text, end_text = base.split('-', 1)
                start, end = int(start_text), int(end_text)
            else:
                start = end = int(base)

            if start > end:
                raise CronError('cron 范围起点大于终点: {}'.format(base))

            for value in range(start, end + 1, step):
                if value < minimum or value > maximum:
                    raise CronError('cron 值越界: {}'.format(value))
                if sunday_7 and value == 7:
                    value = 0
                values.add(value)

        return values, wildcard

    def next_after(self, after: datetime) -> datetime:
        start = after.replace(microsecond=0) + timedelta(seconds=1)
        for day_offset in range(0, 366 * 5):
            day = (start + timedelta(days=day_offset)).date()
            if day.month not in self.months:
                continue
            if not self._day_matches(day):
                continue
            for hour in sorted(self.hours):
                if day_offset == 0 and hour < start.hour:
                    continue
                for minute in sorted(self.minutes):
                    if day_offset == 0 and hour == start.hour and minute < start.minute:
                        continue
                    for second in sorted(self.seconds):
                        if day_offset == 0 and hour == start.hour and minute == start.minute and second < start.second:
                            continue
                        return datetime(day.year, day.month, day.day, hour, minute, second)
        raise CronError('5 年内找不到下一次 cron 执行时间')

    def _day_matches(self, day) -> bool:
        dom_match = day.day in self.days
        cron_weekday = (day.weekday() + 1) % 7
        dow_match = cron_weekday in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return dow_match
        if self.any_weekday:
            return dom_match
        return dom_match or dow_match


class OneBotClient:
    def __init__(self, server: PluginServerInterface, cfg: Dict[str, Any]):
        self.server = server
        self.cfg = copy.deepcopy(cfg)
        self.enabled = bool(self.cfg.get('enabled', False))
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.send_lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.ws = None

    def start(self):
        if not self.enabled:
            return
        if websocket_client is None:
            self.server.logger.warning(
                'OneBot 通知已启用，但 websocket-client 不可用；请执行 '
                'pip uninstall websocket && pip install websocket-client'
            )
            return
        self.thread = threading.Thread(target=self._thread_main, name='MCDR2Restic-OneBot', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=3)

    def send_private_msg(self, user_id: int, text: str):
        if not self.enabled or websocket_client is None:
            return
        threading.Thread(
            target=self._send_private_msg,
            args=(user_id, text),
            name='MCDR2Restic-OneBot-Send',
            daemon=True
        ).start()

    def _thread_main(self):
        while not self.stop_event.is_set():
            url, headers = self._build_connect_auth()
            app = None

            def on_open(ws):
                self.ws = ws
                self.connected_event.set()
                self.server.logger.info('OneBot WS 已连接: {}'.format(self._safe_url(url)))

            def on_error(_ws, error):
                if not self.stop_event.is_set():
                    self.server.logger.warning('OneBot WS 连接异常: {}'.format(error))

            def on_close(_ws, _close_status_code, _close_msg):
                self.connected_event.clear()
                self.ws = None

            try:
                websocket_client.setdefaulttimeout(float(self.cfg.get('connect_timeout_seconds', 10)))
                app = websocket_client.WebSocketApp(
                    url,
                    header=headers or None,
                    on_open=on_open,
                    on_error=on_error,
                    on_close=on_close
                )
                self.ws = app
                app.run_forever(ping_interval=0)
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.server.logger.warning('OneBot WS 连接异常: {}'.format(exc))
            finally:
                self.connected_event.clear()
                if self.ws is app:
                    self.ws = None
            if not self.stop_event.is_set():
                self._sleep(float(self.cfg.get('reconnect_interval_seconds', 5)))

    def _send_private_msg(self, user_id: int, text: str):
        timeout = float(self.cfg.get('send_timeout_seconds', 10))
        if not self.connected_event.wait(timeout=timeout):
            self.server.logger.warning('OneBot 发送 QQ {} 失败: OneBot WS 未连接'.format(user_id))
            return
        ws = self.ws
        if ws is None:
            self.server.logger.warning('OneBot 发送 QQ {} 失败: OneBot WS 未连接'.format(user_id))
            return
        action = {
            'action': 'send_private_msg',
            'params': {
                'user_id': int(user_id),
                'message': text
            },
            'echo': 'mcdr2restic-{}'.format(int(time.time() * 1000))
        }
        try:
            with self.send_lock:
                ws.send(json.dumps(action, ensure_ascii=False))
        except Exception as exc:
            self.server.logger.warning('OneBot 发送 QQ {} 失败: {}'.format(user_id, exc))

    def _sleep(self, seconds: float):
        end = time.monotonic() + max(0.0, seconds)
        while not self.stop_event.is_set() and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))

    def _build_connect_auth(self) -> Tuple[str, Optional[Dict[str, str]]]:
        url = str(self.cfg.get('ws_url', 'ws://127.0.0.1:8777'))
        token = str(self.cfg.get('access_token', '') or '')
        if not token:
            return url, None
        if bool(self.cfg.get('use_header_auth', False)):
            return url, {'Authorization': 'Bearer {}'.format(token)}
        separator = '&' if '?' in url else '?'
        return '{}{}access_token={}'.format(url, separator, token), None

    @staticmethod
    def _safe_url(url: str) -> str:
        return re.sub(r'([?&]access_token=)[^&]+', r'\1***', url)


class DiscordWebhookClient:
    def __init__(self, server: PluginServerInterface, cfg: Dict[str, Any]):
        self.server = server
        self.cfg = copy.deepcopy(cfg)
        self.enabled = bool(self.cfg.get('enabled', False))

    def send_message(self, text: str):
        if not self.enabled:
            return
        threading.Thread(
            target=self._send_message,
            args=(text,),
            name='MCDR2Restic-Discord-Send',
            daemon=True
        ).start()

    def _send_message(self, text: str):
        webhook_url = str(self.cfg.get('webhook_url', '') or '').strip()
        if not webhook_url:
            self.server.logger.warning('Discord 通知已启用，但 webhook_url 为空')
            return
        payload = self._build_payload(text)
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                'User-Agent': 'MCDR2Restic/{}'.format(PLUGIN_ID),
                'Content-Type': 'application/json'
            },
            method='POST'
        )
        timeout = max(1, int(self.cfg.get('send_timeout_seconds', 10)))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, 'status', 204)
                if status < 200 or status >= 300:
                    self.server.logger.warning('Discord Webhook 返回异常状态码: {}'.format(status))
        except Exception as exc:
            self.server.logger.warning('Discord Webhook 发送失败: {}'.format(exc))

    def _build_payload(self, text: str) -> Dict[str, Any]:
        content = self._with_mentions(text)
        payload: Dict[str, Any] = {
            'content': truncate_discord_content(content),
            'allowed_mentions': self._allowed_mentions()
        }
        username = str(self.cfg.get('username', '') or '').strip()
        avatar_url = str(self.cfg.get('avatar_url', '') or '').strip()
        if username:
            payload['username'] = username
        if avatar_url:
            payload['avatar_url'] = avatar_url
        return payload

    def _with_mentions(self, text: str) -> str:
        mentions: List[str] = []
        if bool(self.cfg.get('mention_everyone', False)):
            mentions.append('@everyone')
        for role_id in self.cfg.get('mention_role_ids', []) or []:
            value = str(role_id).strip()
            if value:
                mentions.append('<@&{}>'.format(value))
        for user_id in self.cfg.get('mention_user_ids', []) or []:
            value = str(user_id).strip()
            if value:
                mentions.append('<@{}>'.format(value))
        if not mentions:
            return text
        return '{}\n{}'.format(' '.join(mentions), text)

    def _allowed_mentions(self) -> Dict[str, Any]:
        parse: List[str] = []
        if bool(self.cfg.get('mention_everyone', False)):
            parse.append('everyone')
        users = [str(item).strip() for item in (self.cfg.get('mention_user_ids', []) or []) if str(item).strip()]
        roles = [str(item).strip() for item in (self.cfg.get('mention_role_ids', []) or []) if str(item).strip()]
        return {
            'parse': parse,
            'users': users[:100],
            'roles': roles[:100],
            'replied_user': False
        }


def truncate_discord_content(text: str) -> str:
    text = str(text or '')
    if len(text) <= 2000:
        return text
    return '{}\n...'.format(text[:1996])


class BackupScheduler:
    def __init__(self, server: PluginServerInterface):
        self.server = server
        self.stop_event = threading.Event()
        self.wakeup_event = threading.Event()
        self.thread = threading.Thread(target=self._normal_main, name='MCDR2Restic-Scheduler-Normal', daemon=True)
        self.force_thread = threading.Thread(target=self._force_main, name='MCDR2Restic-Scheduler-Force', daemon=True)

    def start(self):
        self.thread.start()
        self.force_thread.start()

    def stop(self):
        self.stop_event.set()
        self.wakeup_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.force_thread.is_alive():
            self.force_thread.join(timeout=5)

    def wakeup(self):
        self.wakeup_event.set()

    def _normal_main(self):
        self.server.logger.info('MCDR2Restic 正常调度线程已启动')
        while not self.stop_event.is_set():
            cfg = get_config_snapshot()
            if not bool(cfg.get('enabled', True)):
                self._wait(5)
                continue

            try:
                wait_seconds, due_text = compute_wait_seconds(cfg)
            except Exception as exc:
                self.server.logger.error('计算下次备份时间失败: {}'.format(exc))
                notify_admins('schedule_config_error', {'error': str(exc)}, cfg=cfg, failure=True)
                self._wait(60)
                continue

            self.server.logger.info('下次正常备份等待 {} 秒（{}）'.format(int(wait_seconds), due_text))
            if self._wait(wait_seconds):
                continue
            if self.stop_event.is_set():
                break

            cfg = get_config_snapshot()
            if not bool(cfg.get('enabled', True)):
                continue
            if not is_mc_ready(self.server):
                message = '到达备份时间，但 Minecraft 服务端尚未确认正常运行，跳过本次备份'
                self.server.logger.warning(message)
                notify_admins('backup_not_ready', {'message': message}, cfg=cfg, failure=True)
                continue
            if should_skip_for_no_player_activity(cfg):
                message = '本周期没有玩家加入或退出，触发检查时也没有玩家在线，跳过本次正常备份'
                self.server.logger.info(message)
                if cfg.get('notification', {}).get('notify_on_skip', False):
                    notify_admins('backup_skip_no_player', {'message': message}, cfg=cfg)
                continue

            run_backup_locked(self.server, 'scheduled')
        self.server.logger.info('MCDR2Restic 正常调度线程已停止')

    def _force_main(self):
        self.server.logger.info('MCDR2Restic 强制调度线程已启动')
        while not self.stop_event.is_set():
            cfg = get_config_snapshot()
            if not bool(cfg.get('enabled', True)):
                self._wait(5)
                continue

            try:
                force_wait = compute_force_wait_seconds(cfg)
            except Exception as exc:
                self.server.logger.error('计算下次强制备份时间失败: {}'.format(exc))
                notify_admins('schedule_config_error', {'error': str(exc)}, cfg=cfg, failure=True)
                self._wait(60)
                continue

            if force_wait is None:
                self._wait(60)
                continue

            wait_seconds, due_text = force_wait
            self.server.logger.info('下次强制备份等待 {} 秒（{}）'.format(int(wait_seconds), due_text))
            if self._wait(wait_seconds):
                continue
            if self.stop_event.is_set():
                break

            cfg = get_config_snapshot()
            if not bool(cfg.get('enabled', True)):
                continue
            if not is_mc_ready(self.server):
                message = '到达强制备份时间，但 Minecraft 服务端尚未确认正常运行，跳过本次备份'
                self.server.logger.warning(message)
                notify_admins('backup_not_ready', {'message': message}, cfg=cfg, failure=True)
                continue

            run_backup_locked(self.server, 'forced')
        self.server.logger.info('MCDR2Restic 强制调度线程已停止')

    def _wait(self, seconds: float) -> bool:
        end = time.monotonic() + max(0.0, seconds)
        self.wakeup_event.clear()
        while not self.stop_event.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            timeout = min(30.0, remaining)
            if self.wakeup_event.wait(timeout=timeout):
                self.wakeup_event.clear()
                return True
        return True


def on_load(server: PluginServerInterface, prev_module):
    global SERVER, SERVER_READY, SCHEDULER, ONEBOT, DISCORD
    if prev_module is not None and hasattr(prev_module, '_shutdown_runtime'):
        try:
            prev_module._shutdown_runtime(server, 'plugin reload')
        except Exception:
            server.logger.warning('清理上一插件实例时发生异常:\n{}'.format(traceback.format_exc()))

    SERVER = server
    for message in BOOTSTRAP_LOGS:
        server.logger.info('[bootstrap] {}'.format(message))
    BOOTSTRAP_LOGS.clear()
    PLUGIN_STOPPING.clear()
    BACKUP_CANCEL.clear()
    SERVER_READY = server_is_running(server)
    load_config(server)
    register_commands(server)
    server.register_help_message(get_command_root(), 'restic 自动备份管理')

    ONEBOT = OneBotClient(server, get_config_snapshot().get('onebot', {}))
    ONEBOT.start()
    DISCORD = DiscordWebhookClient(server, get_config_snapshot().get('discord', {}))

    SCHEDULER = BackupScheduler(server)
    SCHEDULER.start()
    server.logger.info('MCDR2Restic 已加载')


def on_unload(server: PluginServerInterface):
    _shutdown_runtime(server, 'plugin unload')


def on_server_startup(server: PluginServerInterface):
    global SERVER_READY
    SERVER_READY = True
    server.logger.info('检测到 Minecraft 服务端启动完成，允许备份')


def on_server_stop(server: PluginServerInterface, server_return_code: int):
    global SERVER_READY
    SERVER_READY = False
    with CONFIG_LOCK:
        ensure_runtime(CONFIG)
        runtime = CONFIG['runtime']
        runtime['known_online_players'] = []
        runtime['current_online_players'] = 0
        runtime['last_online_check'] = now_text()
        runtime['last_online_check_source'] = 'server stop'
        runtime['last_online_check_result'] = '0 online after server stop'
        save_config_unlocked(server)
    if is_backup_running():
        server.logger.warning('Minecraft 服务端已停止，正在请求中止当前备份')
        request_cancel_current_backup('server stopped')


def on_mcdr_stop(server: PluginServerInterface):
    _shutdown_runtime(server, 'MCDR stop')


def on_player_joined(server: PluginServerInterface, player: str, info: Info):
    with CONFIG_LOCK:
        ensure_runtime(CONFIG)
        runtime = CONFIG['runtime']
        known_players = runtime_player_set(runtime)
        already_known = player in known_players
        known_players.add(player)
        previous_current = non_negative_int(runtime.get('current_online_players', 0))
        current_online = max(len(known_players), previous_current + (0 if already_known else 1))
        runtime['known_online_players'] = sorted(known_players)
        mark_player_activity_unlocked(runtime, current_online, 'join event')
        runtime['player_joined_since_last_backup'] = True
        runtime['player_joined_since_last_check'] = True
        runtime['player_activity_since_last_backup'] = True
        CONFIG['runtime']['last_player_joined'] = '{} @ {}'.format(player, now_text())
        save_config_unlocked(server)
    server.logger.debug('记录玩家进入，允许本等待周期触发备份: {}'.format(player))


def on_player_left(server: PluginServerInterface, player: str):
    with CONFIG_LOCK:
        ensure_runtime(CONFIG)
        runtime = CONFIG['runtime']
        known_players = runtime_player_set(runtime)
        was_known = player in known_players
        known_players.discard(player)
        previous_current = non_negative_int(runtime.get('current_online_players', 0))
        current_online = max(len(known_players), previous_current - (1 if was_known or previous_current > 0 else 0))
        runtime['known_online_players'] = sorted(known_players)
        runtime['current_online_players'] = current_online
        runtime['player_left_since_last_check'] = True
        runtime['player_activity_since_last_backup'] = True
        runtime['last_player_left'] = '{} @ {}'.format(player, now_text())
        runtime['last_online_check'] = now_text()
        runtime['last_online_check_source'] = 'left event'
        runtime['last_online_check_result'] = '{} online after {} left'.format(current_online, player)
        save_config_unlocked(server)
    server.logger.debug('记录玩家离开，当前估计在线人数: {} -> {}'.format(player, current_online))


def _shutdown_runtime(server: PluginServerInterface, reason: str):
    global SCHEDULER, ONEBOT, DISCORD
    PLUGIN_STOPPING.set()
    if SCHEDULER is not None:
        SCHEDULER.stop()
        SCHEDULER = None
    if is_backup_running():
        request_cancel_current_backup(reason)
        thread = CURRENT_BACKUP_THREAD
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
    try_force_save_on(server, reason)
    if ONEBOT is not None:
        ONEBOT.stop()
        ONEBOT = None
    DISCORD = None
    if SERVER is not None:
        server.logger.info('MCDR2Restic 已停止: {}'.format(reason))


def register_commands(server: PluginServerInterface):
    roots = [get_command_root()] + list(get_config_snapshot().get('command', {}).get('aliases', []))
    seen: Set[str] = set()
    for root_name in roots:
        root_name = str(root_name).strip()
        if not root_name or root_name in seen:
            continue
        seen.add(root_name)
        server.register_command(build_command_tree(root_name))


def build_command_tree(root_name: str):
    return (
        Literal(root_name)
        .runs(command_status)
        .then(Literal('status').runs(command_status))
        .then(Literal('start').runs(command_start))
        .then(Literal('stop').runs(command_stop))
        .then(Literal('backup').runs(command_backup))
        .then(Literal('reload').runs(command_reload))
    )


def command_status(source: CommandSource):
    if not check_command_permission(source):
        return
    cfg = get_config_snapshot()
    server = SERVER or source.get_server()
    language = get_mcdr_language(server)
    source.reply(render_status_output(cfg, language, server))


def render_status_output(cfg: Dict[str, Any], language: str, server: Optional[PluginServerInterface]) -> str:
    running = is_backup_running()
    runtime = cfg.get('runtime', {})
    current_online = non_negative_int(runtime.get('current_online_players', 0))
    joined = bool(runtime.get('player_joined_since_last_check', False)) or bool(runtime.get('player_joined_since_last_backup', False))
    left = bool(runtime.get('player_left_since_last_check', False))
    last_online_check = runtime.get('last_online_check') or localized_never(language)
    last_online_source = localized_online_source(runtime.get('last_online_check_source'), language)
    last_backup_status = localized_backup_status(runtime.get('last_backup_status', 'never'), language)
    normal_next_text = schedule_status_text(cfg, False, language)
    force_next_text = schedule_status_text(cfg, True, language)

    if is_zh_language(language):
        return '\n'.join([
            'MCDR2Restic 状态',
            '启用: {}'.format(localized_bool(bool(cfg.get('enabled', True)), language)),
            '备份中: {}'.format(localized_bool(running, language)),
            'MC 就绪: {}'.format(localized_bool(is_mc_ready(server), language)),
            '玩家活动:',
            '  当前在线: {}'.format(current_online),
            '  本周期有人加入: {}'.format(localized_bool(joined, language)),
            '  本周期有人退出: {}'.format(localized_bool(left, language)),
            '  最近在线检查: {}'.format(last_online_check),
            '  检查来源: {}'.format(last_online_source),
            '调度:',
            '  正常备份: {}'.format(normal_next_text),
            '  强制备份: {}'.format(force_next_text),
            '最近备份状态: {}'.format(last_backup_status)
        ])
    return '\n'.join([
        'MCDR2Restic Status',
        'Enabled: {}'.format(localized_bool(bool(cfg.get('enabled', True)), language)),
        'Backup running: {}'.format(localized_bool(running, language)),
        'Minecraft ready: {}'.format(localized_bool(is_mc_ready(server), language)),
        'Player activity:',
        '  Current online: {}'.format(current_online),
        '  Joined this period: {}'.format(localized_bool(joined, language)),
        '  Left this period: {}'.format(localized_bool(left, language)),
        '  Last online check: {}'.format(last_online_check),
        '  Check source: {}'.format(last_online_source),
        'Schedules:',
        '  Normal backup: {}'.format(normal_next_text),
        '  Forced backup: {}'.format(force_next_text),
        'Last backup status: {}'.format(last_backup_status)
    ])


def schedule_status_text(cfg: Dict[str, Any], forced: bool, language: str) -> str:
    try:
        result = compute_force_wait_seconds(cfg) if forced else compute_wait_seconds(cfg)
        if result is None:
            return '关闭' if is_zh_language(language) else 'disabled'
        wait_seconds, due_text = result
        schedule = cfg.get('force_schedule' if forced else 'schedule', {})
        detail = localized_schedule_detail(schedule, due_text, language)
        if is_zh_language(language):
            return '{} 秒后（{}）'.format(int(wait_seconds), detail)
        return 'in {}s ({})'.format(int(wait_seconds), detail)
    except Exception as exc:
        if is_zh_language(language):
            return '无法计算：{}'.format(exc)
        return 'cannot calculate: {}'.format(exc)


def localized_schedule_detail(schedule: Dict[str, Any], due_text: str, language: str) -> str:
    try:
        interval_seconds = int(schedule.get('interval_seconds', 0)) if isinstance(schedule, dict) else 0
    except Exception:
        interval_seconds = 0
    if interval_seconds > 0:
        if is_zh_language(language):
            return '固定间隔 {} 秒'.format(interval_seconds)
        return 'fixed interval {}s'.format(interval_seconds)
    return due_text


def localized_bool(value: bool, language: str) -> str:
    if is_zh_language(language):
        return '是' if value else '否'
    return 'yes' if value else 'no'


def localized_never(language: str) -> str:
    return '从未' if is_zh_language(language) else 'never'


def localized_backup_status(status: Any, language: str) -> str:
    text = str(status or 'never')
    if not is_zh_language(language):
        return text
    mapping = {
        'never': '从未',
        'running': '运行中',
        'success': '成功',
        'failed': '失败',
        'canceled': '已取消'
    }
    return mapping.get(text, text)


def localized_online_source(source: Any, language: str) -> str:
    if not source:
        return localized_never(language)
    text = str(source)
    if not is_zh_language(language):
        return text
    mapping = {
        'join event': '玩家加入事件',
        'left event': '玩家退出事件',
        'server stop': '服务端停止'
    }
    if text.startswith('rcon '):
        return 'RCON {}'.format(text[5:])
    return mapping.get(text, text)


def command_start(source: CommandSource):
    if not check_command_permission(source):
        return
    server = SERVER or source.get_server()
    with CONFIG_LOCK:
        save_enabled_unlocked(server, True)
        save_config_unlocked(server)
    wake_scheduler()
    source.reply('MCDR2Restic 定时备份已启用')


def command_stop(source: CommandSource):
    if not check_command_permission(source):
        return
    server = SERVER or source.get_server()
    with CONFIG_LOCK:
        save_enabled_unlocked(server, False)
        save_config_unlocked(server)
    wake_scheduler()
    if is_backup_running():
        request_cancel_current_backup('manual stop')
        source.reply('MCDR2Restic 定时备份已禁用，已请求停止当前备份')
    else:
        source.reply('MCDR2Restic 定时备份已禁用')


def command_backup(source: CommandSource):
    if not check_command_permission(source):
        return
    if not is_mc_ready(SERVER or source.get_server()):
        source.reply('Minecraft 服务端尚未确认正常运行，拒绝备份')
        return
    if start_backup_thread(SERVER or source.get_server(), 'manual'):
        source.reply('已开始立即备份，完成结果会发送到日志和已启用的通知渠道')
    else:
        source.reply('当前已有备份在执行，拒绝重复启动')


def command_reload(source: CommandSource):
    if not check_command_permission(source):
        return
    server = SERVER or source.get_server()
    load_config(server, source)
    restart_onebot(server)
    restart_discord(server)
    wake_scheduler()


def check_command_permission(source: CommandSource) -> bool:
    level = int(get_config_snapshot().get('command', {}).get('permission_level', 3))
    try:
        allowed = source.has_permission(level)
    except Exception:
        allowed = False
    if not allowed:
        source.reply('权限不足，需要 MCDR 权限等级 >= {}'.format(level))
    return allowed


def load_config(server: PluginServerInterface, source: Optional[CommandSource] = None):
    global CONFIG, STATE
    language = get_mcdr_language(server)
    defaults = default_config_for_language(language)
    ensure_config_file_exists(server, language)
    loaded = load_yaml_mapping(get_data_file_path(server, CONFIG_NAME))
    if not isinstance(loaded, dict):
        loaded = copy.deepcopy(defaults)
    loaded = strip_comment_keys(loaded)
    loaded.pop('runtime', None)
    migrate_legacy_config(loaded)
    state = load_state_file(server)
    with CONFIG_LOCK:
        CONFIG = loaded
        STATE = state
        merge_defaults(CONFIG, defaults)
        ensure_runtime(CONFIG)
        save_config_unlocked(server)
    migrate_config_file(server, language, get_config_snapshot())
    if source is not None:
        source.reply('MCDR2Restic 已从 {} 重载配置'.format(CONFIG_NAME))


def save_config_unlocked(server: Optional[PluginServerInterface] = None):
    target = server or SERVER
    if target is None:
        return
    state = {'runtime': copy.deepcopy(CONFIG.get('runtime', DEFAULT_RUNTIME))}
    STATE.clear()
    STATE.update(copy.deepcopy(state))
    save_yaml_file(get_data_file_path(target, STATE_NAME), state)


def get_config_snapshot() -> Dict[str, Any]:
    with CONFIG_LOCK:
        snapshot = copy.deepcopy(CONFIG or DEFAULT_CONFIG)
        ensure_runtime(snapshot)
        return snapshot


def ensure_runtime(cfg: Dict[str, Any]):
    runtime = cfg.setdefault('runtime', {})
    for key, value in DEFAULT_RUNTIME.items():
        runtime.setdefault(key, copy.deepcopy(value))
    state_runtime = STATE.get('runtime') if isinstance(STATE, dict) else None
    if isinstance(state_runtime, dict):
        for key, value in state_runtime.items():
            runtime[key] = copy.deepcopy(value)
    runtime.pop('max_online_players_in_wait_period', None)


def merge_defaults(target: Dict[str, Any], defaults: Dict[str, Any]):
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target.get(key), dict) and isinstance(value, dict):
            merge_defaults(target[key], value)


def migrate_legacy_config(cfg: Dict[str, Any]):
    schedule = cfg.get('schedule')
    if isinstance(schedule, dict):
        old_key = 'require_player_joined_in_wait_period'
        new_key = 'require_player_activity_in_wait_period'
        if new_key not in schedule and old_key in schedule:
            schedule[new_key] = bool(schedule.get(old_key, True))
        schedule.pop('online_check_interval_seconds', None)
    restic = cfg.get('restic')
    if isinstance(restic, dict):
        environment = restic.get('environment')
        if not isinstance(environment, dict):
            environment = {}
        if 'repository' not in restic and environment.get('RESTIC_REPOSITORY'):
            restic['repository'] = environment.get('RESTIC_REPOSITORY')
        if 'password' not in restic and environment.get('RESTIC_PASSWORD'):
            restic['password'] = environment.get('RESTIC_PASSWORD')
        if 'password_file' not in restic and environment.get('RESTIC_PASSWORD_FILE'):
            restic['password_file'] = environment.get('RESTIC_PASSWORD_FILE')
        if 'password' not in restic and restic.get('password_file'):
            restic['password'] = ''
    cfg['config_version'] = CONFIG_VERSION


def migrate_config_file(server: PluginServerInterface, language: str, cfg: Dict[str, Any]):
    path = get_data_file_path(server, CONFIG_NAME)
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf8') as file:
            lines = file.readlines()
        original = ''.join(lines)
        lines = ensure_schedule_migration_lines(lines, language, cfg)
        lines = remove_deprecated_schedule_lines(lines)
        lines = ensure_restic_migration_lines(lines, language, cfg)
        lines = ensure_force_schedule_block(lines, language, cfg)
        lines = ensure_discord_block(lines, language, cfg)
        lines = ensure_config_version_tail(lines, language)
        updated = ''.join(lines)
        if updated != original:
            with open(path, 'w', encoding='utf8') as file:
                file.write(updated)
            server.logger.info('已迁移并补全配置文件 {}'.format(CONFIG_NAME))
    except Exception as exc:
        server.logger.warning('迁移配置文件 {} 失败: {}'.format(CONFIG_NAME, exc))


def ensure_schedule_migration_lines(lines: List[str], language: str, cfg: Dict[str, Any]) -> List[str]:
    schedule = cfg.get('schedule', {}) if isinstance(cfg.get('schedule'), dict) else {}
    insertions: List[str] = []
    if not has_nested_key(lines, 'schedule', 'require_player_activity_in_wait_period'):
        insertions.extend(get_schedule_activity_lines(language, schedule))
    if not has_nested_key(lines, 'schedule', 'online_check_command'):
        insertions.extend(get_schedule_online_command_lines(language, schedule))
    if not insertions:
        return lines
    return insert_into_top_level_block(lines, 'schedule', insertions)


def ensure_force_schedule_block(lines: List[str], language: str, cfg: Dict[str, Any]) -> List[str]:
    if has_top_level_key(lines, 'force_schedule'):
        return lines
    force_schedule = cfg.get('force_schedule', {}) if isinstance(cfg.get('force_schedule'), dict) else {}
    block = get_force_schedule_lines(language, force_schedule)
    return insert_before_config_version_or_end(lines, block)


def ensure_restic_migration_lines(lines: List[str], language: str, cfg: Dict[str, Any]) -> List[str]:
    restic = cfg.get('restic', {}) if isinstance(cfg.get('restic'), dict) else {}
    insertions: List[str] = []
    for key, builder in [
        ('repository', get_restic_repository_lines),
        ('password', get_restic_password_lines),
        ('password_file', get_restic_password_file_lines),
        ('auto_download', get_restic_auto_download_lines),
        ('download_version', get_restic_download_version_lines),
        ('download_proxy_prefixes', get_restic_download_proxy_lines),
        ('download_timeout_seconds', get_restic_download_timeout_lines),
        ('auto_init_local_repository', get_restic_auto_init_lines)
    ]:
        if not has_nested_key(lines, 'restic', key):
            insertions.extend(builder(language, restic))
    if not insertions:
        return lines
    return insert_into_top_level_block(lines, 'restic', insertions)


def ensure_discord_block(lines: List[str], language: str, cfg: Dict[str, Any]) -> List[str]:
    if has_top_level_key(lines, 'discord'):
        return lines
    discord = cfg.get('discord', {}) if isinstance(cfg.get('discord'), dict) else {}
    block = get_discord_block_lines(language, discord)
    return insert_before_top_level_key(lines, 'notification', block)


def ensure_config_version_tail(lines: List[str], language: str) -> List[str]:
    marker_comment = '# 配置文件版本标识。请保留在文件尾部，方便后续迁移。\n' if is_zh_language(language) else '# Config file version marker. Keep it at the end for future migrations.\n'
    version_line = 'config_version: {}\n'.format(CONFIG_VERSION)
    cleaned: List[str] = []
    for line in lines:
        if re.match(r'^config_version\s*:', line):
            if cleaned and is_config_version_comment(cleaned[-1]):
                cleaned.pop()
            continue
        cleaned.append(line)
    lines = cleaned
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and not lines[-1].endswith('\n'):
        lines[-1] = lines[-1] + '\n'
    if lines and lines[-1].strip():
        lines.append('\n')
    lines.append(marker_comment)
    lines.append(version_line)
    return lines


def is_config_version_comment(line: str) -> bool:
    stripped = line.strip().lower()
    return stripped.startswith('#') and (
        '配置文件版本标识' in stripped or
        'config file version marker' in stripped
    )


def remove_deprecated_schedule_lines(lines: List[str]) -> List[str]:
    start, end = find_top_level_block(lines, 'schedule')
    if start is None:
        return lines
    deprecated_keys = {
        'require_player_joined_in_wait_period',
        'online_check_interval_seconds'
    }
    remove_indexes: Set[int] = set()
    key_pattern = re.compile(r'^\s+([A-Za-z_][A-Za-z0-9_-]*)\s*:')
    for index in range(start + 1, end):
        match = key_pattern.match(lines[index])
        if not match or match.group(1) not in deprecated_keys:
            continue
        remove_indexes.add(index)
        comment_index = index - 1
        while comment_index > start and is_deprecated_schedule_comment(lines[comment_index]):
            remove_indexes.add(comment_index)
            comment_index -= 1
    if not remove_indexes:
        return lines
    return [line for index, line in enumerate(lines) if index not in remove_indexes]


def is_deprecated_schedule_comment(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith('#'):
        return False
    lowered = stripped.lower()
    keywords = [
        'require_player_joined_in_wait_period',
        'online_check_interval_seconds',
        '没人进入',
        '等待周期内每隔',
        '采样',
        'nobody joined',
        'sampling interval',
        'waiting period'
    ]
    return any(keyword in lowered for keyword in keywords)


def get_schedule_activity_lines(language: str, schedule: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(schedule.get('require_player_activity_in_wait_period', True))
    if is_zh_language(language):
        return [
            '  # 正常定时备份是否启用玩家活动感知。旧配置 require_player_joined_in_wait_period 会迁移到这里。\n',
            '  require_player_activity_in_wait_period: {}\n'.format(value)
        ]
    return [
        '  # Whether normal scheduled backups use player activity sensing. Migrated from require_player_joined_in_wait_period when present.\n',
        '  require_player_activity_in_wait_period: {}\n'.format(value)
    ]


def get_schedule_online_command_lines(language: str, schedule: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(schedule.get('online_check_command', 'list'))
    if is_zh_language(language):
        return [
            '  # 触发正常备份时用于查询当前在线人数的 Minecraft 命令。\n',
            '  online_check_command: {}\n'.format(value)
        ]
    return [
        '  # Minecraft command used at normal backup trigger time to query current online players.\n',
        '  online_check_command: {}\n'.format(value)
    ]


def get_force_schedule_lines(language: str, force_schedule: Dict[str, Any]) -> List[str]:
    interval = yaml_scalar(force_schedule.get('interval_seconds', 0))
    cron = yaml_scalar(force_schedule.get('cron_expression', '0'))
    if is_zh_language(language):
        return [
            '\n',
            'force_schedule:\n',
            '  # 强制备份调度，不遵循玩家活动感知。默认关闭。\n',
            '  # interval_seconds > 0 时使用固定间隔；interval_seconds = 0 且 cron_expression = "0" 表示关闭。\n',
            '  interval_seconds: {}\n'.format(interval),
            '  cron_expression: {}\n'.format(cron)
        ]
    return [
        '\n',
        'force_schedule:\n',
        '  # Forced backup schedule. It ignores player activity sensing and is disabled by default.\n',
        '  # interval_seconds > 0 uses a fixed interval; interval_seconds = 0 with cron_expression = "0" disables it.\n',
        '  interval_seconds: {}\n'.format(interval),
        '  cron_expression: {}\n'.format(cron)
    ]


def get_restic_repository_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(restic.get('repository', './restic-repo'))
    if is_zh_language(language):
        return [
            '  # restic 存储库。默认使用 MCDR 工作目录下的本地仓库。\n',
            '  repository: {}\n'.format(value)
        ]
    return [
        "  # restic repository. Default: a local repository in MCDR's working directory.\n",
        '  repository: {}\n'.format(value)
    ]


def get_restic_password_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(restic.get('password', '123456'))
    if is_zh_language(language):
        return [
            '  # 存储库密码。优先使用这里的直接密码；留空字符串时再看 password_file。\n',
            '  password: {}\n'.format(value)
        ]
    return [
        '  # Repository password. This direct password has priority; when empty, password_file is used.\n',
        '  password: {}\n'.format(value)
    ]


def get_restic_password_file_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(restic.get('password_file', ''))
    if is_zh_language(language):
        return [
            '  # 密码文件。仅当 password 为空时生效。\n',
            '  password_file: {}\n'.format(value)
        ]
    return [
        '  # Password file. Used only when password is empty.\n',
        '  password_file: {}\n'.format(value)
    ]


def get_restic_auto_download_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(restic.get('auto_download', True))
    if is_zh_language(language):
        return [
            '  # 如果仍使用默认 executable 路径且找不到 restic，则自动下载 restic。\n',
            '  # 仅支持 Linux amd64 和 Windows amd64；其他系统会跳过。\n',
            '  auto_download: {}\n'.format(value)
        ]
    return [
        '  # Automatically download restic if executable still uses the default path and is missing.\n',
        '  # Only Linux amd64 and Windows amd64 are supported; other systems are skipped.\n',
        '  auto_download: {}\n'.format(value)
    ]


def get_restic_download_version_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(restic.get('download_version', 'latest'))
    if is_zh_language(language):
        return [
            '  # restic 下载版本。latest 表示 GitHub 最新 release，也可以写 v0.18.0 这类版本号。\n',
            '  download_version: {}\n'.format(value)
        ]
    return [
        '  # restic download version. latest uses the latest GitHub release; version tags such as v0.18.0 are also accepted.\n',
        '  download_version: {}\n'.format(value)
    ]


def get_restic_download_proxy_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    prefixes = restic.get('download_proxy_prefixes', DEFAULT_CONFIG['restic']['download_proxy_prefixes'])
    if not isinstance(prefixes, list):
        prefixes = DEFAULT_CONFIG['restic']['download_proxy_prefixes']
    lines = [
        '  download_proxy_prefixes:\n'
    ] + ['    - {}\n'.format(yaml_scalar(prefix)) for prefix in prefixes]
    if is_zh_language(language):
        return [
            '  # 下载加速代理前缀。会先尝试官方 GitHub 下载，再按顺序尝试这里的代理。\n'
        ] + lines
    return [
        '  # Download proxy prefixes. The plugin tries official GitHub first, then these proxies in order.\n'
    ] + lines


def get_restic_download_timeout_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(restic.get('download_timeout_seconds', 120))
    if is_zh_language(language):
        return [
            '  # 单个下载地址的超时时间，单位秒。\n',
            '  download_timeout_seconds: {}\n'.format(value)
        ]
    return [
        '  # Timeout for each download URL, in seconds.\n',
        '  download_timeout_seconds: {}\n'.format(value)
    ]


def get_restic_auto_init_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    value = yaml_scalar(restic.get('auto_init_local_repository', True))
    if is_zh_language(language):
        return [
            '  # 本地存储库不存在或缺少 config 时自动执行 restic init；远端仓库不会自动初始化。\n',
            '  auto_init_local_repository: {}\n'.format(value)
        ]
    return [
        '  # Automatically run restic init when a local repository is missing or lacks config; remote repositories are skipped.\n',
        '  auto_init_local_repository: {}\n'.format(value)
    ]


def get_discord_block_lines(language: str, discord: Dict[str, Any]) -> List[str]:
    enabled = yaml_scalar(discord.get('enabled', False))
    webhook_url = yaml_scalar(discord.get('webhook_url', ''))
    username = yaml_scalar(discord.get('username', 'MCDR2Restic'))
    avatar_url = yaml_scalar(discord.get('avatar_url', ''))
    prefix = yaml_scalar(discord.get('message_prefix', '[MCDR2Restic]'))
    timeout = yaml_scalar(discord.get('send_timeout_seconds', 10))
    if is_zh_language(language):
        return [
            '\n',
            'discord:\n',
            '  # Discord Webhook 通知。填好 webhook_url 后把 enabled 改为 true。\n',
            '  enabled: {}\n'.format(enabled),
            '  # Discord 频道 Webhook URL。\n',
            '  webhook_url: {}\n'.format(webhook_url),
            '  # Webhook 显示名称和头像，可留空使用 Discord Webhook 默认值。\n',
            '  username: {}\n'.format(username),
            '  avatar_url: {}\n'.format(avatar_url),
            '  # Discord 通知消息前缀，消息正文仍使用 messages 中的模板。\n',
            '  message_prefix: {}\n'.format(prefix),
            '  # 可选提及对象。默认不提及任何人，避免误 ping。\n',
            '  mention_user_ids: []\n',
            '  mention_role_ids: []\n',
            '  mention_everyone: false\n',
            '  send_timeout_seconds: {}\n'.format(timeout)
        ]
    return [
        '\n',
        'discord:\n',
        '  # Discord webhook notifications. Fill webhook_url, then set enabled to true.\n',
        '  enabled: {}\n'.format(enabled),
        '  # Discord channel webhook URL.\n',
        '  webhook_url: {}\n'.format(webhook_url),
        '  # Webhook display name and avatar. Leave avatar_url empty to use the Discord webhook default.\n',
        '  username: {}\n'.format(username),
        '  avatar_url: {}\n'.format(avatar_url),
        '  # Discord notification prefix. Message bodies still use templates under messages.\n',
        '  message_prefix: {}\n'.format(prefix),
        '  # Optional mentions. Empty by default to avoid accidental pings.\n',
        '  mention_user_ids: []\n',
        '  mention_role_ids: []\n',
        '  mention_everyone: false\n',
        '  send_timeout_seconds: {}\n'.format(timeout)
    ]


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return 'null'
    return json.dumps(str(value), ensure_ascii=False)


def has_top_level_key(lines: List[str], key: str) -> bool:
    pattern = re.compile(r'^{}\s*:'.format(re.escape(key)))
    return any(pattern.match(line) for line in lines)


def has_nested_key(lines: List[str], block_key: str, nested_key: str) -> bool:
    start, end = find_top_level_block(lines, block_key)
    if start is None:
        return False
    pattern = re.compile(r'^\s+{}\s*:'.format(re.escape(nested_key)))
    return any(pattern.match(line) for line in lines[start + 1:end])


def insert_into_top_level_block(lines: List[str], block_key: str, insertions: List[str]) -> List[str]:
    start, end = find_top_level_block(lines, block_key)
    if start is None:
        block = ['\n', '{}:\n'.format(block_key)] + insertions
        return insert_before_config_version_or_end(lines, block)
    return lines[:end] + insertions + lines[end:]


def insert_before_config_version_or_end(lines: List[str], insertions: List[str]) -> List[str]:
    version_index = None
    for index, line in enumerate(lines):
        if re.match(r'^config_version\s*:', line):
            version_index = index
            break
    if version_index is None:
        return lines + insertions
    return lines[:version_index] + insertions + lines[version_index:]


def insert_before_top_level_key(lines: List[str], key: str, insertions: List[str]) -> List[str]:
    pattern = re.compile(r'^{}\s*:'.format(re.escape(key)))
    for index, line in enumerate(lines):
        if pattern.match(line):
            return lines[:index] + insertions + lines[index:]
    return insert_before_config_version_or_end(lines, insertions)


def find_top_level_block(lines: List[str], block_key: str) -> Tuple[Optional[int], int]:
    start = None
    pattern = re.compile(r'^{}\s*:'.format(re.escape(block_key)))
    top_level = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]*\s*:')
    for index, line in enumerate(lines):
        if pattern.match(line):
            start = index
            break
    if start is None:
        return None, len(lines)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if top_level.match(line):
            end = index
            break
    return start, end


def ensure_config_file_exists(server: PluginServerInterface, language: str):
    data_folder = server.get_data_folder()
    os.makedirs(data_folder, exist_ok=True)
    config_path = get_data_file_path(server, CONFIG_NAME)
    if os.path.exists(config_path):
        return
    with open(config_path, 'w', encoding='utf8') as file:
        file.write(get_default_config_template(language))
    legacy_path = get_data_file_path(server, LEGACY_CONFIG_NAME)
    if os.path.exists(legacy_path):
        server.logger.warning(
            '已生成新的 YAML 配置 {}。检测到旧 JSON 配置 {}，插件不会继续使用旧文件，请手动迁移需要的配置项。'.format(
                CONFIG_NAME, LEGACY_CONFIG_NAME
            )
        )
    else:
        server.logger.info('已生成默认配置文件 {}'.format(config_path))


def get_data_file_path(server: PluginServerInterface, file_name: str) -> str:
    return os.path.join(server.get_data_folder(), file_name)


def load_yaml_mapping(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf8') as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        return {}
    return data


def save_yaml_file(path: str, data: Dict[str, Any]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8') as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False, default_flow_style=False)


def load_state_file(server: PluginServerInterface) -> Dict[str, Any]:
    state = load_yaml_mapping(get_data_file_path(server, STATE_NAME))
    runtime = state.get('runtime')
    if not isinstance(runtime, dict):
        state['runtime'] = copy.deepcopy(DEFAULT_RUNTIME)
    else:
        merge_defaults(runtime, DEFAULT_RUNTIME)
    return state


def strip_comment_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_comment_keys(item)
            for key, item in value.items()
            if not str(key).startswith('_comment') and not str(key).endswith('_comment')
        }
    if isinstance(value, list):
        return [strip_comment_keys(item) for item in value]
    return value


def save_enabled_unlocked(server: PluginServerInterface, enabled: bool):
    CONFIG['enabled'] = bool(enabled)
    path = get_data_file_path(server, CONFIG_NAME)
    ensure_config_file_exists(server, get_mcdr_language(server))
    enabled_text = 'enabled: {}\n'.format('true' if enabled else 'false')
    with open(path, 'r', encoding='utf8') as file:
        lines = file.readlines()
    for index, line in enumerate(lines):
        if re.match(r'^enabled\s*:', line):
            lines[index] = enabled_text
            break
    else:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] = lines[-1] + '\n'
        lines.append(enabled_text)
    with open(path, 'w', encoding='utf8') as file:
        file.writelines(lines)


def get_command_root() -> str:
    cfg = get_config_snapshot()
    return str(cfg.get('command', {}).get('root', '!!restic'))


def restart_onebot(server: PluginServerInterface):
    global ONEBOT
    if ONEBOT is not None:
        ONEBOT.stop()
    ONEBOT = OneBotClient(server, get_config_snapshot().get('onebot', {}))
    ONEBOT.start()


def restart_discord(server: PluginServerInterface):
    global DISCORD
    DISCORD = DiscordWebhookClient(server, get_config_snapshot().get('discord', {}))


def wake_scheduler():
    if SCHEDULER is not None:
        SCHEDULER.wakeup()


def start_backup_thread(server: PluginServerInterface, label: str) -> bool:
    global CURRENT_BACKUP_THREAD
    if not BACKUP_LOCK.acquire(blocking=False):
        return False
    thread = threading.Thread(
        target=run_backup_with_acquired_lock,
        args=(server, label),
        name='MCDR2Restic-Backup-{}'.format(label),
        daemon=True
    )
    CURRENT_BACKUP_THREAD = thread
    thread.start()
    return True


def run_backup_locked(server: PluginServerInterface, label: str) -> bool:
    if not BACKUP_LOCK.acquire(blocking=False):
        server.logger.warning('当前已有备份在执行，跳过 {} 触发'.format(label))
        return False
    run_backup_with_acquired_lock(server, label)
    return True


def run_backup_with_acquired_lock(server: PluginServerInterface, label: str):
    global CURRENT_BACKUP_LABEL, CURRENT_BACKUP_THREAD
    CURRENT_BACKUP_LABEL = label
    BACKUP_CANCEL.clear()
    started = time.monotonic()
    start_time = now_text()
    cfg = get_config_snapshot()
    status = 'failed'
    message = ''
    detail = ''
    duration_seconds = 0
    save_on_errors: List[str] = []

    try:
        try:
            with CONFIG_LOCK:
                ensure_runtime(CONFIG)
                CONFIG['runtime']['last_backup_start_time'] = start_time
                CONFIG['runtime']['last_backup_end_time'] = None
                CONFIG['runtime']['last_backup_status'] = 'running'
                CONFIG['runtime']['last_backup_message'] = '{} backup started'.format(label)
                save_config_unlocked(server)

            server.logger.info('开始 {} 备份'.format(label))
            if cfg.get('notification', {}).get('notify_on_start', True):
                notify_admins('backup_start', {'label': label, 'start_time': start_time}, cfg=cfg)

            run_backup_body(server, cfg, label)
            status = 'success'
            duration_seconds = int(time.monotonic() - started)
            message = '{} 备份成功，用时 {} 秒'.format(label, duration_seconds)
            server.logger.info(message)
        except BackupCanceled as exc:
            status = 'canceled'
            duration_seconds = int(time.monotonic() - started)
            detail = str(exc)
            message = '{} 备份已取消：{}'.format(label, exc)
            server.logger.warning(message)
        except Exception as exc:
            status = 'failed'
            duration_seconds = int(time.monotonic() - started)
            detail = str(exc)
            message = '{} 备份失败：{}'.format(label, exc)
            server.logger.error('{}\n{}'.format(message, traceback.format_exc()))
        finally:
            try:
                try_force_save_on(server, 'backup finally')
            except Exception as exc:
                save_on_errors.append(str(exc))
                server.logger.error('备份结束阶段执行 save-on 失败: {}'.format(exc))

            finished = now_text()
            if save_on_errors and status == 'success':
                status = 'failed'
                detail = 'save-on 恢复失败：{}'.format('; '.join(save_on_errors))
                message = '{}；但 {}'.format(message, detail)
            elif save_on_errors:
                detail = '{}；save-on 恢复失败：{}'.format(detail, '; '.join(save_on_errors)).strip('；')

            with CONFIG_LOCK:
                ensure_runtime(CONFIG)
                CONFIG['runtime']['last_backup_end_time'] = finished
                CONFIG['runtime']['last_backup_status'] = status
                CONFIG['runtime']['last_backup_message'] = message
                save_config_unlocked(server)

            notify_end = (
                (status == 'success' and cfg.get('notification', {}).get('notify_on_success', True)) or
                (status != 'success' and cfg.get('notification', {}).get('notify_on_failure', True))
            )
            if notify_end:
                template_key = 'backup_success' if status == 'success' else 'backup_failure'
                notify_admins(
                    template_key,
                    {
                        'label': label,
                        'status': status,
                        'message': message,
                        'detail': detail or message,
                        'start_time': start_time,
                        'end_time': finished,
                        'duration_seconds': duration_seconds
                    },
                    cfg=cfg,
                    failure=(status != 'success')
                )
    finally:
        CURRENT_BACKUP_LABEL = None
        CURRENT_BACKUP_THREAD = None
        BACKUP_CANCEL.clear()
        try:
            BACKUP_LOCK.release()
        except RuntimeError:
            pass


def run_backup_body(server: PluginServerInterface, cfg: Dict[str, Any], label: str):
    if not is_mc_ready(server):
        raise BackupProblem('Minecraft 服务端尚未确认正常运行')

    restic_cfg = cfg.get('restic', {})
    timeout_seconds = int(restic_cfg.get('timeout_seconds', 3600))
    deadline = time.monotonic() + max(1, timeout_seconds)
    ensure_default_restic_executable_available(server, restic_cfg)
    newly_initialized = ensure_restic_repository_initialized(server, restic_cfg, deadline)

    if newly_initialized:
        server.logger.info('本地 restic 仓库刚初始化，跳过本次备份前维护命令')
    else:
        for command in restic_cfg.get('maintenance_commands', []):
            check_canceled()
            result = run_restic_command(restic_cfg, command, 'maintenance', deadline)
            assert_restic_success(restic_cfg, result)

    check_canceled()
    execute_mc_command(server, cfg, cfg.get('minecraft', {}).get('save_off_command', 'save-off'), 'save-off')
    sleep_or_cancel(float(cfg.get('minecraft', {}).get('wait_after_save_off_seconds', 2)))

    check_canceled()
    execute_mc_command(server, cfg, cfg.get('minecraft', {}).get('save_all_command', 'save-all flush'), 'save-all')
    sleep_or_cancel(float(cfg.get('minecraft', {}).get('wait_after_save_all_seconds', 10)))

    check_canceled()
    result = run_restic_command(restic_cfg, restic_cfg.get('backup_command', []), 'backup', deadline)
    assert_restic_success(restic_cfg, result)


def ensure_default_restic_executable_available(server: PluginServerInterface, restic_cfg: Dict[str, Any]):
    if not bool(restic_cfg.get('auto_download', True)):
        return
    executable = str(restic_cfg.get('executable', './restic') or '').strip()
    if not is_default_restic_executable_path(executable):
        return
    target_path = resolve_restic_executable_path(restic_cfg, executable)
    if os.path.exists(target_path):
        return

    platform_info = get_restic_download_platform()
    if platform_info is None:
        server.logger.warning('当前系统或架构不支持自动下载 restic，请手动放置可执行文件: {}'.format(target_path))
        return

    system_name, asset_keyword, output_name = platform_info
    server.logger.info('默认 restic 可执行文件不存在，准备自动下载 {} amd64 版本: {}'.format(system_name, target_path))
    download_and_install_restic(server, restic_cfg, asset_keyword, output_name, target_path)


def is_default_restic_executable_path(executable: str) -> bool:
    normalized = executable.replace('\\', '/').lower()
    return normalized in ('./restic', 'restic', './restic.exe', 'restic.exe')


def resolve_restic_executable_path(restic_cfg: Dict[str, Any], executable: str) -> str:
    expanded = os.path.expanduser(os.path.expandvars(executable))
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    cwd = restic_cfg.get('working_directory') or os.getcwd()
    return os.path.abspath(os.path.join(str(cwd), expanded))


def get_restic_download_platform() -> Optional[Tuple[str, str, str]]:
    machine = platform.machine().lower()
    is_amd64 = machine in ('x86_64', 'amd64')
    if not is_amd64:
        return None
    if sys.platform.startswith('linux'):
        return 'linux', 'linux_amd64.bz2', 'restic'
    if os.name == 'nt':
        return 'windows', 'windows_amd64.zip', 'restic.exe'
    return None


def download_and_install_restic(
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    asset_keyword: str,
    output_name: str,
    target_path: str
):
    timeout = max(10, int(restic_cfg.get('download_timeout_seconds', 120)))
    version = str(restic_cfg.get('download_version', 'latest') or 'latest').strip()
    release = fetch_restic_release_metadata(version, timeout)
    asset = select_restic_release_asset(release, asset_keyword)
    asset_url = str(asset.get('browser_download_url', '') or '')
    asset_name = str(asset.get('name', '') or '')
    if not asset_url:
        raise BackupProblem('restic release 资产缺少下载地址: {}'.format(asset_name or asset_keyword))

    proxy_prefixes = restic_cfg.get('download_proxy_prefixes', [])
    urls = build_download_urls(asset_url, proxy_prefixes)
    last_error = ''
    os.makedirs(os.path.dirname(target_path) or '.', exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mcdr2restic-') as temp_dir:
        archive_path = os.path.join(temp_dir, asset_name or 'restic-download')
        for url in urls:
            try:
                server.logger.info('正在下载 restic: {}'.format(mask_download_url(url)))
                download_file(url, archive_path, timeout)
                install_restic_archive(archive_path, asset_keyword, output_name, target_path)
                server.logger.info('restic 自动下载并安装完成: {}'.format(target_path))
                return
            except Exception as exc:
                last_error = str(exc)
                server.logger.warning('restic 下载或安装失败，尝试下一个地址: {}'.format(last_error))
        raise BackupProblem('自动下载 restic 失败: {}'.format(last_error or asset_url))


def fetch_restic_release_metadata(version: str, timeout: int) -> Dict[str, Any]:
    if version.lower() == 'latest':
        url = 'https://api.github.com/repos/restic/restic/releases/latest'
    else:
        url = 'https://api.github.com/repos/restic/restic/releases/tags/{}'.format(version)
    try:
        data = download_bytes(url, timeout)
    except Exception:
        if version.lower() == 'latest':
            return copy.deepcopy(RESTIC_FALLBACK_RELEASE)
        raise
    try:
        metadata = json.loads(data.decode('utf-8'))
    except Exception as exc:
        raise BackupProblem('解析 restic release 信息失败: {}'.format(exc))
    if not isinstance(metadata, dict) or not isinstance(metadata.get('assets'), list):
        raise BackupProblem('restic release 信息格式异常')
    return metadata


def select_restic_release_asset(release: Dict[str, Any], asset_keyword: str) -> Dict[str, Any]:
    for asset in release.get('assets', []):
        name = str(asset.get('name', '') or '')
        if asset_keyword in name:
            return asset
    raise BackupProblem('未找到匹配的 restic release 资产: {}'.format(asset_keyword))


def build_download_urls(asset_url: str, proxy_prefixes: Any) -> List[str]:
    urls = [asset_url]
    if isinstance(proxy_prefixes, str):
        proxy_prefixes = [proxy_prefixes]
    if isinstance(proxy_prefixes, (list, tuple, set)):
        for prefix in proxy_prefixes:
            prefix_text = str(prefix or '').strip()
            if not prefix_text:
                continue
            urls.append(prefix_text.rstrip('/') + '/' + asset_url)
    seen: Set[str] = set()
    unique_urls: List[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    return unique_urls


def download_file(url: str, path: str, timeout: int):
    data = download_bytes(url, timeout)
    with open(path, 'wb') as file:
        file.write(data)


def download_bytes(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'MCDR2Restic/{}'.format(PLUGIN_ID),
            'Accept': 'application/octet-stream, application/json'
        }
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise BackupProblem('下载失败 {}: {}'.format(mask_download_url(url), exc))


def install_restic_archive(archive_path: str, asset_keyword: str, output_name: str, target_path: str):
    if asset_keyword.endswith('.zip'):
        with zipfile.ZipFile(archive_path, 'r') as archive:
            member = find_zip_member(archive, output_name)
            with archive.open(member, 'r') as source, open(target_path, 'wb') as target:
                shutil.copyfileobj(source, target)
    elif asset_keyword.endswith('.bz2'):
        with open(archive_path, 'rb') as source:
            data = bz2.decompress(source.read())
        with open(target_path, 'wb') as target:
            target.write(data)
    else:
        raise BackupProblem('不支持的 restic 压缩包类型: {}'.format(asset_keyword))
    if os.name != 'nt':
        mode = os.stat(target_path).st_mode
        os.chmod(target_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def find_zip_member(archive: zipfile.ZipFile, output_name: str) -> str:
    output_name_lower = output_name.lower()
    for name in archive.namelist():
        if name.rstrip('/').lower().endswith(output_name_lower):
            return name
    raise BackupProblem('zip 中未找到 {}'.format(output_name))


def mask_download_url(url: str) -> str:
    return re.sub(r'([?&](?:token|access_token)=)[^&]+', r'\1***', url)


def ensure_restic_repository_initialized(server: PluginServerInterface, restic_cfg: Dict[str, Any], deadline: float) -> bool:
    if not bool(restic_cfg.get('auto_init_local_repository', True)):
        return False
    env = build_restic_environment(restic_cfg)
    repository = str(env.get('RESTIC_REPOSITORY', '') or '').strip()
    if not repository:
        return False
    if not is_local_restic_repository(repository):
        server.logger.debug('restic 仓库不是本地路径，跳过自动初始化: {}'.format(repository))
        return False

    repository_path = resolve_restic_repository_path(restic_cfg, repository)
    config_path = os.path.join(repository_path, 'config')
    if os.path.isfile(config_path):
        return False
    if os.path.exists(repository_path) and not os.path.isdir(repository_path):
        raise BackupProblem('本地 restic 仓库路径已存在但不是目录: {}'.format(repository_path))

    os.makedirs(repository_path, exist_ok=True)
    server.logger.info('本地 restic 仓库不存在或未初始化，正在执行 restic init: {}'.format(repository_path))
    result = run_restic_command(restic_cfg, ['init'], 'init', deadline)
    assert_restic_success(restic_cfg, result)
    return True


def build_restic_environment(restic_cfg: Dict[str, Any]) -> Dict[str, str]:
    env = os.environ.copy()
    configured_env = restic_cfg.get('environment', {})
    if isinstance(configured_env, dict):
        for key, value in configured_env.items():
            if value is None:
                env.pop(str(key), None)
            else:
                env[str(key)] = str(value)

    repository = str(restic_cfg.get('repository', '') or '').strip()
    if repository:
        env['RESTIC_REPOSITORY'] = repository

    password = str(restic_cfg.get('password', '') or '')
    password_file = str(restic_cfg.get('password_file', '') or '').strip()
    if password:
        env['RESTIC_PASSWORD'] = password
        env.pop('RESTIC_PASSWORD_FILE', None)
        env.pop('RESTIC_PASSWORD_COMMAND', None)
    elif password_file:
        env.pop('RESTIC_PASSWORD', None)
        env['RESTIC_PASSWORD_FILE'] = password_file
    return env


def is_local_restic_repository(repository: str) -> bool:
    repo = repository.strip()
    if not repo:
        return False
    if re.match(r'^[A-Za-z]:[\\/]', repo):
        return True
    lowered = repo.lower()
    remote_prefixes = (
        'sftp:', 'rest:', 's3:', 'b2:', 'azure:', 'gs:', 'rclone:', 'swift:',
        'opendal:', 'http:', 'https:'
    )
    return not lowered.startswith(remote_prefixes)


def resolve_restic_repository_path(restic_cfg: Dict[str, Any], repository: str) -> str:
    repository = os.path.expanduser(os.path.expandvars(repository))
    if os.path.isabs(repository):
        return os.path.abspath(repository)
    cwd = restic_cfg.get('working_directory') or os.getcwd()
    return os.path.abspath(os.path.join(str(cwd), repository))


def run_restic_command(restic_cfg: Dict[str, Any], configured_args: Any, phase: str, deadline: float) -> ResticCommandResult:
    global CURRENT_PROCESS
    args = normalize_command_args(configured_args)
    if not args:
        raise BackupProblem('restic {} 命令为空'.format(phase))
    executable = str(restic_cfg.get('executable', 'restic'))
    command = [executable] + args
    env = build_restic_environment(restic_cfg)
    cwd = restic_cfg.get('working_directory') or None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BackupProblem('备份总超时已耗尽，未执行 restic {}'.format(phase))

    popen_kwargs: Dict[str, Any] = {
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
        'stdin': subprocess.DEVNULL,
        'cwd': cwd,
        'env': env,
        'text': True,
        'encoding': 'utf-8',
        'errors': 'replace'
    }
    if os.name == 'nt':
        popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    else:
        popen_kwargs['start_new_session'] = True

    started = time.monotonic()
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except FileNotFoundError:
        raise BackupProblem('找不到 restic 可执行文件: {}'.format(executable))
    except Exception as exc:
        raise BackupProblem('启动 restic {} 失败: {}'.format(phase, exc))

    CURRENT_PROCESS = process
    try:
        stdout, stderr = process.communicate(timeout=max(1, int(remaining)))
    except subprocess.TimeoutExpired:
        terminate_process(process)
        stdout, stderr = process.communicate()
        raise BackupProblem('restic {} 超时（{} 秒），进程已终止\n{}'.format(
            phase,
            int(time.monotonic() - started),
            tail_text((stdout or '') + '\n' + (stderr or ''), int(restic_cfg.get('max_output_chars_in_notification', 1800)))
        ))
    finally:
        CURRENT_PROCESS = None

    if BACKUP_CANCEL.is_set():
        raise BackupCanceled('收到停止请求')

    return ResticCommandResult(
        phase=phase,
        args=args,
        return_code=int(process.returncode),
        stdout=stdout or '',
        stderr=stderr or '',
        duration_seconds=time.monotonic() - started
    )


def assert_restic_success(restic_cfg: Dict[str, Any], result: ResticCommandResult):
    success_codes = set(int(code) for code in restic_cfg.get('success_exit_codes', [0]))
    combined = '{}\n{}'.format(result.stdout, result.stderr)
    suspicious_lines = detect_error_lines(
        combined,
        restic_cfg.get('error_regexes', []),
        restic_cfg.get('ignore_error_regexes', [])
    )
    if result.return_code not in success_codes:
        raise BackupProblem(
            'restic {} 退出码异常：{}，用时 {} 秒\n{}'.format(
                result.phase,
                result.return_code,
                int(result.duration_seconds),
                tail_text(combined, int(restic_cfg.get('max_output_chars_in_notification', 1800)))
            )
        )
    if suspicious_lines:
        raise BackupProblem(
            'restic {} 输出疑似包含文件/运行错误：\n{}'.format(
                result.phase,
                tail_text('\n'.join(suspicious_lines), int(restic_cfg.get('max_output_chars_in_notification', 1800)))
            )
        )


def normalize_command_args(value: Any) -> List[str]:
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    raise BackupProblem('命令参数必须是字符串或数组: {}'.format(value))


def detect_error_lines(text: str, patterns: Iterable[str], ignore_patterns: Iterable[str]) -> List[str]:
    compiled = [re.compile(pattern) for pattern in patterns if pattern]
    ignored = [re.compile(pattern) for pattern in ignore_patterns if pattern]
    lines: List[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if any(regex.search(line) for regex in ignored):
            continue
        if any(regex.search(line) for regex in compiled):
            lines.append(line)
    return lines


def execute_mc_command(server: PluginServerInterface, cfg: Dict[str, Any], command: str, label: str):
    if not command:
        return
    if not server_is_running(server):
        raise BackupProblem('执行 {} 前检测到 Minecraft 服务端不在运行状态'.format(label))
    server.logger.info('执行 Minecraft 命令: {}'.format(label))
    try:
        server.execute(command)
    except Exception as exc:
        raise BackupProblem('执行 Minecraft 命令 {} 失败: {}'.format(label, exc))


def try_force_save_on(server: Optional[PluginServerInterface], reason: str):
    if server is None:
        return
    cfg = get_config_snapshot()
    command = cfg.get('minecraft', {}).get('save_on_command', 'save-on')
    if not command or not server_is_running(server):
        return
    try:
        server.logger.info('尝试恢复自动保存 save-on ({})'.format(reason))
        server.execute(command)
        wait = float(cfg.get('minecraft', {}).get('wait_after_save_on_seconds', 1))
        if wait > 0:
            time.sleep(min(wait, 5.0))
    except Exception as exc:
        raise BackupProblem('执行 save-on 失败: {}'.format(exc))


def request_cancel_current_backup(reason: str):
    BACKUP_CANCEL.set()
    process = CURRENT_PROCESS
    if process is not None and process.poll() is None:
        terminate_process(process)
    if SERVER is not None:
        SERVER.logger.warning('已请求停止当前备份: {}'.format(reason))


def terminate_process(process: subprocess.Popen):
    try:
        if os.name == 'nt':
            process.terminate()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=10)
    except Exception:
        try:
            if os.name == 'nt':
                process.kill()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            pass


def is_backup_running() -> bool:
    return BACKUP_LOCK.locked()


def check_canceled():
    if BACKUP_CANCEL.is_set():
        raise BackupCanceled('收到停止请求')


def sleep_or_cancel(seconds: float):
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        check_canceled()
        time.sleep(min(0.2, end - time.monotonic()))


def compute_wait_seconds(cfg: Dict[str, Any]) -> Tuple[float, str]:
    return compute_schedule_wait_seconds(
        cfg.get('schedule', {}),
        '0 0 0,3,6,9,12,15,18,21 * * *',
        disabled_when_zero_cron=False
    )


def compute_force_wait_seconds(cfg: Dict[str, Any]) -> Optional[Tuple[float, str]]:
    return compute_schedule_wait_seconds(cfg.get('force_schedule', {}), '0', disabled_when_zero_cron=True)


def compute_schedule_wait_seconds(
    schedule: Dict[str, Any],
    default_cron: str,
    disabled_when_zero_cron: bool
) -> Optional[Tuple[float, str]]:
    if not isinstance(schedule, dict):
        schedule = {}
    interval_seconds = int(schedule.get('interval_seconds', 0))
    if interval_seconds > 0:
        return float(interval_seconds), '固定间隔 {} 秒'.format(interval_seconds)
    if interval_seconds < 0:
        raise ValueError('interval_seconds 不能小于 0')
    cron_text = str(schedule.get('cron_expression', default_cron) or '').strip()
    if disabled_when_zero_cron and cron_text in ('', '0'):
        return None
    cron = CronExpression(cron_text)
    next_time = cron.next_after(datetime.now())
    return max(0.0, (next_time - datetime.now()).total_seconds()), next_time.strftime('%Y-%m-%d %H:%M:%S')


def player_activity_required(cfg: Dict[str, Any]) -> bool:
    schedule = cfg.get('schedule', {})
    if 'require_player_activity_in_wait_period' in schedule:
        return bool(schedule.get('require_player_activity_in_wait_period', True))
    return bool(schedule.get('require_player_joined_in_wait_period', True))


def runtime_player_set(runtime: Dict[str, Any]) -> Set[str]:
    players = runtime.get('known_online_players', [])
    if isinstance(players, str):
        players = [players]
    if not isinstance(players, (list, tuple, set)):
        return set()
    return {str(player) for player in players if str(player).strip()}


def non_negative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return default


def mark_player_activity_unlocked(runtime: Dict[str, Any], current_online: int, source: str, result: Optional[str] = None):
    current_online = non_negative_int(current_online)
    runtime['current_online_players'] = current_online
    if current_online > 0:
        runtime['player_activity_since_last_backup'] = True
    runtime['last_online_check'] = now_text()
    runtime['last_online_check_source'] = source
    runtime['last_online_check_result'] = result or '{} online'.format(current_online)


def reset_player_activity_period_unlocked(runtime: Dict[str, Any]):
    current_online = non_negative_int(runtime.get('current_online_players', 0))
    runtime['player_activity_since_last_backup'] = current_online > 0
    runtime['player_joined_since_last_backup'] = False
    runtime['player_joined_since_last_check'] = False
    runtime['player_left_since_last_check'] = False


def parse_online_list_output(output: str) -> Tuple[Optional[int], List[str]]:
    text = re.sub(r'§.', '', str(output or '')).strip()
    patterns = [
        r'\bThere are\s+(\d+)\s+of\s+a\s+max\s+of\s+\d+\s+players?\s+online\b',
        r'\b(\d+)\s*/\s*\d+\s+players?\s+online\b',
        r'\((\d+)\s*/\s*\d+\)',
        r'\b(\d+)\s+players?\s+online\b',
        r'(?:当前)?(?:有)?\s*(\d+)\s*(?:个)?玩家在线'
    ]
    count: Optional[int] = None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            count = non_negative_int(match.group(1))
            break

    names: List[str] = []
    if ':' in text:
        tail = text.rsplit(':', 1)[1].strip()
        if tail:
            names = [name.strip() for name in tail.split(',') if name.strip()]
    if count is None and names:
        count = len(names)
    return count, names


def sample_online_players(server: Optional[PluginServerInterface], cfg: Dict[str, Any], reason: str) -> Optional[int]:
    if server is None or not player_activity_required(cfg):
        return None
    if not server_is_running(server):
        return None

    schedule = cfg.get('schedule', {})
    command = str(schedule.get('online_check_command', 'list') or '').strip()
    if not command:
        return None

    rcon_query = getattr(server, 'rcon_query', None)
    if not callable(rcon_query):
        server.logger.debug('无法执行在线人数采样：当前 MCDR 不支持 rcon_query')
        return None

    try:
        result = rcon_query(command)
    except Exception as exc:
        server.logger.debug('在线人数采样失败（{}）：{}'.format(reason, exc))
        return None
    if result is None:
        server.logger.debug('在线人数采样未返回结果（{}），请确认 MCDR RCON 已启用'.format(reason))
        return None

    count, names = parse_online_list_output(str(result))
    if count is None:
        server.logger.debug('无法解析在线人数采样输出（{}）：{}'.format(reason, tail_text(str(result), 300)))
        return None

    with CONFIG_LOCK:
        ensure_runtime(CONFIG)
        runtime = CONFIG['runtime']
        runtime['known_online_players'] = sorted(names) if names else ([] if count == 0 else sorted(runtime_player_set(runtime)))
        mark_player_activity_unlocked(runtime, count, 'rcon {}'.format(command), tail_text(str(result), 300))
        save_config_unlocked(server)
    return count


def should_skip_for_no_player_activity(cfg: Dict[str, Any]) -> bool:
    if not player_activity_required(cfg):
        return False
    sample_online_players(SERVER, cfg, 'schedule trigger')
    with CONFIG_LOCK:
        ensure_runtime(CONFIG)
        runtime = CONFIG['runtime']
        current_online = non_negative_int(runtime.get('current_online_players', 0))
        joined = (
            bool(runtime.get('player_joined_since_last_check', False)) or
            bool(runtime.get('player_joined_since_last_backup', False))
        )
        left = bool(runtime.get('player_left_since_last_check', False))
        has_activity = (
            current_online > 0 or
            joined or
            left
        )
        reset_player_activity_period_unlocked(runtime)
        save_config_unlocked(SERVER)
    return not has_activity


def server_is_running(server: Optional[PluginServerInterface]) -> bool:
    if server is None:
        return False
    startup_method = getattr(server, 'is_server_startup', None)
    if callable(startup_method):
        try:
            return bool(startup_method())
        except Exception:
            pass
    if SERVER_READY:
        return True
    running_method = getattr(server, 'is_server_running', None)
    if callable(running_method):
        try:
            return bool(running_method())
        except Exception:
            pass
    return bool(SERVER_READY)


def is_mc_ready(server: Optional[PluginServerInterface]) -> bool:
    return server_is_running(server)


class SafeFormatDict(dict):
    def __missing__(self, key):
        return '{' + str(key) + '}'


def render_message(
    template_key: str,
    values: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    prefix: Optional[str] = None
) -> str:
    cfg = cfg or get_config_snapshot()
    onebot_cfg = cfg.get('onebot', {})
    messages = cfg.get('messages', {})
    template = messages.get(template_key)
    if not isinstance(template, str):
        template = DEFAULT_CONFIG.get('messages', {}).get(template_key, template_key)
    data = SafeFormatDict()
    data.update({
        'prefix': str(prefix if prefix is not None else onebot_cfg.get('message_prefix', '[MCDR2Restic]')),
        'plugin': 'MCDR2Restic',
        'template_key': template_key
    })
    if values:
        for key, value in values.items():
            data[str(key)] = '' if value is None else str(value)
    try:
        return template.format_map(data)
    except Exception as exc:
        if SERVER is not None:
            SERVER.logger.warning('消息模板 {} 格式化失败: {}'.format(template_key, exc))
        return '{} {}'.format(data['prefix'], template_key)


def notify_admins(template_key: str, values: Optional[Dict[str, Any]] = None, cfg: Optional[Dict[str, Any]] = None, failure: bool = False):
    cfg = cfg or get_config_snapshot()
    onebot_cfg = cfg.get('onebot', {})
    discord_cfg = cfg.get('discord', {})
    log_text = render_message(template_key, values, cfg)
    if SERVER is not None:
        if failure:
            SERVER.logger.warning(log_text)
        else:
            SERVER.logger.info(log_text)

    admin_qqs = onebot_cfg.get('admin_qqs', [])
    if onebot_cfg.get('enabled', False) and ONEBOT is None:
        if SERVER is not None:
            SERVER.logger.warning('OneBot 未启动，无法发送通知: {}'.format(template_key))
    elif onebot_cfg.get('enabled', False):
        text = render_message(template_key, values, cfg, str(onebot_cfg.get('message_prefix', '[MCDR2Restic]')))
        for qid in admin_qqs:
            try:
                ONEBOT.send_private_msg(int(qid), text)
            except Exception as exc:
                if SERVER is not None:
                    SERVER.logger.warning('发送 OneBot 通知到 QQ {} 失败: {}'.format(qid, exc))

    if discord_cfg.get('enabled', False):
        if DISCORD is None:
            if SERVER is not None:
                SERVER.logger.warning('Discord 未初始化，无法发送通知: {}'.format(template_key))
            return
        text = render_message(template_key, values, cfg, str(discord_cfg.get('message_prefix', '[MCDR2Restic]')))
        try:
            DISCORD.send_message(text)
        except Exception as exc:
            if SERVER is not None:
                SERVER.logger.warning('发送 Discord 通知失败: {}'.format(exc))


def tail_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text.strip()
    return '...\n{}'.format(text[-max_chars:].strip())


def now_text() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
