# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from typing import List, Optional

from mcdr2restic.core.language import is_zh_language


DEFAULT_BACKUP_WORLD_PATHS = (
    './server/world',
    './server/world_nether',
    './server/world_the_end',
)

DEFAULT_BACKUP_SOURCE_MARKER = '    __MCDR2RESTIC_DEFAULT_BACKUP_SOURCES__\n'


def get_default_config_template(language: str, base_directory: Optional[str] = None) -> str:
    template = DEFAULT_CONFIG_TEMPLATE_ZH if is_zh_language(language) else DEFAULT_CONFIG_TEMPLATE_EN
    template = render_default_backup_sources(template, base_directory or os.getcwd())
    return adapt_default_config_template_for_platform(template, language)


def render_default_backup_sources(template: str, base_directory: str) -> str:
    lines = [
        '    - "{}"\n'.format(path)
        for path in get_default_backup_source_paths(base_directory)
    ]
    return template.replace(DEFAULT_BACKUP_SOURCE_MARKER, ''.join(lines))


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


def adapt_default_config_template_for_platform(template: str, language: str) -> str:
    if os.name != 'nt':
        return template
    replacements = [
        ('# Linux Java 版示例。若服务端不支持 save-all flush，可改为 save-all。', '# Windows Java 版示例。若服务端不支持 save-all flush，可改为 save-all。'),
        ('# Linux Java Edition example. If your server does not support save-all flush, use save-all instead.', '# Windows Java Edition example. If your server does not support save-all flush, use save-all instead.'),
        ('  # 新生成配置默认只写入 ./server/world；如果生成配置时检测到三世界目录全部存在，会自动加入 world_nether 和 world_the_end。', '  # 新生成配置默认只写入 .\\server\\world；如果生成配置时检测到三世界目录全部存在，会自动加入 world_nether 和 world_the_end。'),
        ('  # Newly generated configs include only ./server/world by default. If all three world directories exist when the file is generated, world_nether and world_the_end are added automatically.', '  # Newly generated configs include only .\\server\\world by default. If all three world directories exist when the file is generated, world_nether and world_the_end are added automatically.'),
        ('executable: "./restic"', "executable: '.\\restic.exe'"),
        ('repository: "./restic-repo"', "repository: '.\\restic-repo'"),
        ('    - "./server/world"', "    - '.\\server\\world'"),
        ('    - "./server/world_nether"', "    - '.\\server\\world_nether'"),
        ('    - "./server/world_the_end"', "    - '.\\server\\world_the_end'"),
    ]
    for old, new in replacements:
        template = template.replace(old, new)
    return add_windows_session_lock_exclude(template, language)


def add_windows_session_lock_exclude(template: str, language: str) -> str:
    marker = '    - "--tag"\n'
    if marker not in template:
        return template
    if is_zh_language(language):
        comment = '    # Windows 下 Minecraft 会锁定 session.lock，默认排除以避免 restic 返回 3。\n'
    else:
        comment = '    # On Windows, Minecraft locks session.lock; exclude it by default to avoid restic exit code 3.\n'
    block = comment + '    - "--exclude"\n    - "session.lock"\n'
    return template.replace(marker, block + marker, 1)


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

update_check:
  # 版本更新检查。启用后插件加载时检查一次，并在每天 00:00 检查一次。
  enabled: true
  check_on_startup: true
  daily_time: "00:00"
  # GitHub latest release API。网络不佳时会按 proxy_prefixes 尝试代理。
  api_url: "https://api.github.com/repos/pfdr2333/MCDR2restic/releases/latest"
  release_page_url: "https://github.com/pfdr2333/MCDR2restic/releases/latest"
  proxy_prefixes:
    - "https://gh.llkk.cc/"
    - "https://gh-proxy.com/"
    - "https://hub.gitmirror.com/"
  timeout_seconds: 10

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
  # 新生成配置默认只写入 ./server/world；如果生成配置时检测到三世界目录全部存在，会自动加入 world_nether 和 world_the_end。
  backup_command:
    - "backup"
    __MCDR2RESTIC_DEFAULT_BACKUP_SOURCES__
    - "--tag"
    - "minecraft"
    - "--host"
    - "mcdr2Restic"
  # 整个 restic 命令流程共用的超时。0 表示不限制。
  timeout_seconds: 0
  # restic backup/restore --json 进度回显间隔，单位秒。
  progress_interval_seconds: 5
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

snapshot_cache:
  # status 命令中的 restic 快照列表缓存。
  # 缓存写入 SQLite 文件，只有仓库被本插件操作后才会标记失效。
  enabled: true
  # 每页显示的快照数量。默认 10。
  page_size: 10
  # 缓存刷新时执行 restic snapshots --json 的超时时间。
  query_timeout_seconds: 30
  # SQLite 缓存文件名，位于本插件配置目录。
  database: "snapshots.sqlite3"

restore:
  # 执行恢复前会自动备份一次，并给该保护快照追加这个 tag。
  pre_restore_backup_tag: "mcdr2restic-pre-restore"
  # 恢复流程通过 MCDR 服务端停止/启动 hook 接力，这两个值保留给状态机/外部看门狗使用。
  stop_timeout_seconds: 120
  start_timeout_seconds: 120

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
config_version: 9
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

update_check:
  # Version update checks. When enabled, the plugin checks once on load and once every day at 00:00.
  enabled: true
  check_on_startup: true
  daily_time: "00:00"
  # GitHub latest release API. When the network is slow or blocked, proxy_prefixes are tried in order.
  api_url: "https://api.github.com/repos/pfdr2333/MCDR2restic/releases/latest"
  release_page_url: "https://github.com/pfdr2333/MCDR2restic/releases/latest"
  proxy_prefixes:
    - "https://gh.llkk.cc/"
    - "https://gh-proxy.com/"
    - "https://hub.gitmirror.com/"
  timeout_seconds: 10

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
  # Newly generated configs include only ./server/world by default. If all three world directories exist when the file is generated, world_nether and world_the_end are added automatically.
  backup_command:
    - "backup"
    __MCDR2RESTIC_DEFAULT_BACKUP_SOURCES__
    - "--tag"
    - "minecraft"
    - "--host"
    - "mcdr2Restic"
  # Timeout shared by restic command workflows. 0 means unlimited.
  timeout_seconds: 0
  # Progress echo interval for restic backup/restore --json, in seconds.
  progress_interval_seconds: 5
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

snapshot_cache:
  # restic snapshot list cache used by the status command.
  # The cache is stored in SQLite and invalidated only after this plugin changes the repository.
  enabled: true
  # Number of snapshots shown per page. Default: 10.
  page_size: 10
  # Timeout for restic snapshots --json when refreshing the cache.
  query_timeout_seconds: 30
  # SQLite cache file name under this plugin's config directory.
  database: "snapshots.sqlite3"

restore:
  # Before applying restore tasks, the plugin creates one safety backup with this tag.
  pre_restore_backup_tag: "mcdr2restic-pre-restore"
  # The restore workflow is driven by MCDR server stop/start hooks; these values are reserved for the state machine or external watchdogs.
  stop_timeout_seconds: 120
  start_timeout_seconds: 120

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
config_version: 9
"""
