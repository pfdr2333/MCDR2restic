# 配置

这一页按“你想实现什么效果”来讲配置，而不是把所有字段生硬列成字典。

默认配置文件路径：

```text
config/mcdr2restic/config.yml
```

## 先看一眼完整结构

当前配置大致长这样：

```yaml
enabled: true
language: zh_cn

command:
  root: "!!restic"
  aliases:
    - "!!m2r"
  permission_level: 3

schedule: {}
force_schedule: {}
maintenance_schedule: {}
update_check: {}
minecraft: {}
restic: {}
onebot: {}
discord: {}
notification: {}
snapshot_cache: {}
restore: {}
messages: {}
config_version: 10
```

## 1. 总开关和命令入口

### 总开关

```yaml
enabled: true
```

- `true`：允许调度继续工作
- `false`：关闭后续定时备份

如果你习惯用命令控制，也可以直接执行 `!!restic start` 和 `!!restic stop`。

### 命令入口

```yaml
command:
  root: "!!restic"
  aliases:
    - "!!m2r"
  permission_level: 3
```

- `root`：主命令根节点
- `aliases`：命令别名
- `permission_level`：执行命令所需的 MCDR 权限等级

如果你把 `root` 改成 `!!backup`，那命令就会变成 `!!backup status`、`!!backup reload` 之类。

## 2. 正常定时备份

### 什么时候该改 `schedule`

当你希望“平时自动备份，但尽量只在有人活动时才备份”时，用这一段。

```yaml
schedule:
  interval_seconds: 0
  cron_expression: "0 0 0,3,6,9,12,15,18,21 * * *"
  require_player_activity_in_wait_period: true
  online_check_command: "list"
```

规则是：

- `interval_seconds > 0` 时，优先按固定间隔运行
- `interval_seconds = 0` 时，使用 6 位 cron
- `require_player_activity_in_wait_period = true` 时，会结合玩家活动决定这次要不要备份

玩家活动感知的判断逻辑：

- 本周期有人加入：备份
- 没人加入，但触发时检查到有人在线：备份
- 没人加入，但本周期有人退出：备份
- 没人加入、没人退出、触发时也没人在线：跳过

### 常用示例：只有玩家活跃时才做正常备份

```yaml
schedule:
  interval_seconds: 0
  cron_expression: "0 0 */6 * * *"   # 每 6 小时检查一次
  require_player_activity_in_wait_period: true
  online_check_command: "list"
```

### 常用示例：完全按时间走，不看玩家活动

```yaml
schedule:
  interval_seconds: 7200              # 每 2 小时一次
  cron_expression: "0 0 0,3,6,9,12,15,18,21 * * *"
  require_player_activity_in_wait_period: false
  online_check_command: "list"
```

说明：

- 这里虽然保留了 `cron_expression`，但真正生效的是 `interval_seconds: 7200`
- `online_check_command` 默认是 `list`

### 重要提醒

- 正常调度的 `cron_expression` 不建议留空
- 它不是维护调度，留空不会自动变成默认时间

## 3. 强制备份调度

### 它和正常调度有什么区别

`force_schedule` 不看玩家活动。只要到点，就直接启动备份。

```yaml
force_schedule:
  interval_seconds: 0
  cron_expression: "0"
```

默认是关闭状态。

### 常用示例：每天凌晨强制留一份

```yaml
force_schedule:
  interval_seconds: 0
  cron_expression: "0 30 4 * * *"    # 每天 04:30 强制备份
```

### 关闭方式

推荐明确写成：

```yaml
force_schedule:
  interval_seconds: 0
  cron_expression: "0"
```

## 4. 独立维护调度

### 这不是“备份前步骤”

维护是完全独立的流程。它不会自动插进每次备份前面。

```yaml
maintenance_schedule:
  interval_seconds: 0
  cron_expression: "0 0 3 * * *"
```

关键行为：

- 留空表示默认每天 `03:00`
- 写成 `"0"` 表示关闭
- 如果和备份同一时刻触发，先备份
- 维护不会执行 Minecraft 的 `save-off`、`save-all`、`save-on`

### 常用示例：每天凌晨 3 点维护

```yaml
maintenance_schedule:
  interval_seconds: 0
  cron_expression: "0 0 3 * * *"
```

### 常用示例：禁用自动维护，只保留手动维护

```yaml
maintenance_schedule:
  interval_seconds: 0
  cron_expression: "0"
```

## 5. Minecraft 保存命令

备份开始前，插件会调用这里的命令。

```yaml
minecraft:
  save_off_command: "save-off"
  save_all_command: "save-all flush"
  save_on_command: "save-on"
  wait_after_save_off_seconds: 2
  wait_after_save_all_seconds: 10
  wait_after_save_on_seconds: 1
```

说明：

- Java 版常见配置是 `save-all flush`
- 如果你的服务端不支持 `flush`，可改成 `save-all`
- 维护流程不会使用这些命令

## 6. restic 核心配置

这一段最重要，决定你到底备份什么、备份到哪里、怎么认证。

### 最小本地仓库示例

```yaml
restic:
  executable: "./config/mcdr2restic/restic"   # Windows 可改为 '.\config\mcdr2restic\restic.exe'
  working_directory: ""
  repository: "./restic-repo"
  password: "请改成你自己的强密码"
  password_file: ""
  auto_download: true
  download_version: "latest"
  download_proxy_prefixes:
    - "https://gh.llkk.cc/"
    - "https://gh-proxy.com/"
    - "https://hub.gitmirror.com/"
  download_timeout_seconds: 120
  auto_init_local_repository: true
  environment: {}
  maintenance_commands:
    - ["forget", "--keep-daily", "7", "--prune"]
  backup_command:
    - "backup"
    - "./server/world"
    - "--tag"
    - "minecraft"
    - "--host"
    - "mcdr2Restic"
  timeout_seconds: 0
  progress_interval_seconds: 5
```

### `executable`

推荐直接保留默认值。

这样做的好处：

- 插件更容易自动下载 `restic`
- Windows / Linux 的默认路径都已经考虑好了

### `working_directory`

- 留空：继承 MCDR 当前工作目录
- 不留空：按你填写的目录作为 `restic` 工作目录

如果你不确定，第一次通常保持空字符串最稳妥。

### `repository`

仓库路径或仓库地址。

本地示例：

```yaml
repository: "./restic-repo"
```

远端示例：

```yaml
repository: "s3:s3.example.com/my-minecraft-backup"
```

### `password` 和 `password_file`

优先级是：

1. `password`
2. `password_file`

也就是说，只要 `password` 不是空字符串，就优先用它。

如果你想改用密码文件：

```yaml
restic:
  password: ""
  password_file: "./config/mcdr2restic/restic-password.txt"
```

### `auto_download`

只有在你使用默认可执行文件路径族时，自动下载才有意义。它主要用于默认路径下缺少 `restic` 的场景。

当前适用范围：

- Linux amd64
- Windows amd64

### `auto_init_local_repository`

```yaml
auto_init_local_repository: true
```

含义：

- 本地仓库不存在，或缺少 `config` 文件时，插件会自动执行 `restic init`
- 远端仓库不会自动初始化

### `environment`

适合放远端仓库需要的环境变量，例如 S3、B2 等。

示例：

```yaml
restic:
  repository: "s3:https://s3.example.com/my-backup"
  password: ""
  password_file: "./config/mcdr2restic/restic-password.txt"
  environment:
    AWS_ACCESS_KEY_ID: "改成你自己的 Access Key"
    AWS_SECRET_ACCESS_KEY: "改成你自己的 Secret Key"
    AWS_DEFAULT_REGION: "ap-east-1"
```

注意：

- 不需要在这里重复写 `RESTIC_REPOSITORY`
- 不需要在这里重复写 `RESTIC_PASSWORD`
- 如果某个环境变量值写成 `null`，插件会尝试移除继承来的同名变量

### `maintenance_commands`

这一段就是维护时真正执行的 `restic` 命令列表。

常见示例：

```yaml
maintenance_commands:
  - ["forget", "--keep-daily", "7", "--prune"]
```

### `backup_command`

这是最容易改错的一段，因为它直接决定“备份什么”。

最简单示例：

```yaml
backup_command:
  - "backup"
  - "./server/world"
  - "--tag"
  - "minecraft"
  - "--host"
  - "mcdr2Restic"
```

如果你想备份多个目录：

```yaml
backup_command:
  - "backup"
  - "./server/world"
  - "./server/world_nether"
  - "./server/world_the_end"
  - "--tag"
  - "minecraft"
  - "--host"
  - "mcdr2Restic"
```

### `timeout_seconds`

- `0` 表示不限制
- 大于 `0` 表示整个 `restic` 命令流程允许占用的总时间

### `progress_interval_seconds`

控制 `backup` / `restore --json` 进度在日志中的回显间隔。默认每 5 秒输出一条进度。

### 高级：成功码和错误判定

```yaml
success_exit_codes:
  - 0
error_regexes:
  - '(?i)^fatal:'
ignore_error_regexes:
  - '(?i)errors?:\s*0\b'
max_output_chars_in_notification: 1800
```

适合什么时候改：

- 你很清楚自己在做什么
- 你需要对某些特殊输出重新判定成功或失败

普通用户建议保持默认。

## 7. 通知配置

### OneBot QQ 最小示例

```yaml
onebot:
  enabled: true
  ws_url: "ws://127.0.0.1:8777"
  access_token: "改成你的令牌，没有就留空"
  use_header_auth: false
  admin_qqs:
    - 123456789
  message_prefix: "[MCDR2Restic]"
  connect_timeout_seconds: 10
  send_timeout_seconds: 10
  reconnect_interval_seconds: 5
```

### Discord Webhook 最小示例

```yaml
discord:
  enabled: true
  webhook_url: "改成你的 Discord Webhook URL"
  username: "MCDR2Restic"
  avatar_url: ""
  message_prefix: "[MCDR2Restic]"
  mention_user_ids: []
  mention_role_ids: []
  mention_everyone: false
  send_timeout_seconds: 10
```

### 控制哪些阶段发通知

```yaml
notification:
  notify_on_start: true
  notify_on_success: true
  notify_on_failure: true
  notify_on_skip: false
```

## 8. 快照缓存

`status` 命令里的快照列表依赖这段配置。

```yaml
snapshot_cache:
  enabled: true
  page_size: 10
  query_timeout_seconds: 30
  database: "snapshots.sqlite3"
```

说明：

- 默认每页 10 条
- 首次刷新或缓存失效后，状态查询会重新调用 `restic snapshots --json`
- 数据库存放在插件配置目录下

## 9. 恢复配置

```yaml
restore:
  pre_restore_backup_tag: "mcdr2restic-pre-restore"
  stop_timeout_seconds: 120
  start_timeout_seconds: 120
```

最重要的是：

- `restore apply` 开始前会自动做一份保护快照
- 这份保护快照会带上 `pre_restore_backup_tag`
- 如果恢复阶段失败，插件会尝试回滚到这份保护快照

恢复到指定目录的行为见[状态与恢复](restore.md)，它不依赖这里的停服超时配置。

## 10. 自定义通知文本

你可以改 `messages` 里的模板。

可用变量：

- `{prefix}`
- `{label}`
- `{start_time}`
- `{end_time}`
- `{duration_seconds}`
- `{status}`
- `{message}`
- `{detail}`
- `{error}`

示例：

```yaml
messages:
  backup_start: |-
    {prefix} 备份开始
    触发来源：{label}
    开始时间：{start_time}
  backup_success: |-
    {prefix} 备份完成
    触发来源：{label}
    耗时：{duration_seconds} 秒
    结束时间：{end_time}
  backup_failure: |-
    {prefix} 备份失败
    触发来源：{label}
    状态：{status}
    详情：{detail}
```

如果你想输出字面量的大括号，请写成 `{{` 和 `}}`。

## 11. 更新检查

```yaml
update_check:
  enabled: true
  check_on_startup: true
  daily_time: "00:00"
  api_url: "https://api.github.com/repos/pfdr2333/MCDR2restic/releases/latest"
  release_page_url: "https://github.com/pfdr2333/MCDR2restic/releases/latest"
  proxy_prefixes:
    - "https://gh.llkk.cc/"
    - "https://gh-proxy.com/"
    - "https://hub.gitmirror.com/"
  timeout_seconds: 10
```

它只会检查和提示，不会自动更新插件。

## 12. 几组可直接参考的配置片段

### 只保留正常备份，不要强制备份

```yaml
force_schedule:
  interval_seconds: 0
  cron_expression: "0"
```

### 普通备份和强制备份并存

```yaml
schedule:
  interval_seconds: 0
  cron_expression: "0 0 */6 * * *"
  require_player_activity_in_wait_period: true
  online_check_command: "list"

force_schedule:
  interval_seconds: 0
  cron_expression: "0 0 4 * * *"
```

说明：

- 平时每 6 小时检查一次，有玩家活动才备份
- 每天 04:00 再强制补一份，不看玩家活动

### 单独做维护，不自动强制备份

```yaml
force_schedule:
  interval_seconds: 0
  cron_expression: "0"

maintenance_schedule:
  interval_seconds: 0
  cron_expression: "0 0 3 * * *"

restic:
  maintenance_commands:
    - ["forget", "--keep-daily", "7", "--prune"]
```

## 13. 配置迁移和备份文件

配置版本升级时，插件可能会迁移你的 `config.yml`。

你可能看到这些文件：

- `config.yml`：当前配置
- `config.yml.v8.bak`：旧版本配置备份，文件名里的数字会因原版本而变化
- `config.yml.temp`：迁移中的临时文件，正常成功后不会留下

如果配置损坏，插件会快速失败，而不是把你原来的配置静默重写掉。
