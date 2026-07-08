# MCDR2Restic

MCDR2Restic 是一个 MCDReforged 插件，用于在服务端正常运行时定时调用 restic 备份指定目录。

## 前置要求 / Prerequisites
> 本项目负责调用 restic。默认配置下，如果 MCDR 工作目录找不到 restic，则会自动下载对应系统的 restic ，非默认目录该功能不生效

>在使用本项目之前，请先了解 restic 的基本原理及配置方法，并确保能够独立使用 restic。
restic的使用方法已经超出此文档范围（[restic手册](https://restic.readthedocs.io/en/stable/)）。大部分备份功能均需要基础的restic知识。Restic 是一款快速、高效且安全的开源备份工具，其去重备份功能尤为适合与MC服务器使用，可大幅减小备份体积。

>虽然本插件默认配置会自动处理restic的安装，但仍然建议学习restic使用方法和高级配置，你会用上的。~~当然你也可以问AI,记得开思考模式~~


# 语言 **中文| [English](README_EN.md)**
本项目支持中文和英语，跟随MCDR语言

This project supports both Chinese and English, following the MCDR language conventions.

## 功能

- 定时调用 restic 备份
- 可中断当前备份任务
- 自动安装依赖
- 支持 OneBot QQ 通知和 Discord Webhook 通知
- 中英文消息和配置注释
- 无人游玩跳过正常备份：触发时执行一次 `list`，并结合 join/left 事件判断
- 支持强制备份调度：不受玩家活动感知影响，默认关闭

## 安装
>**快速开始：将插件放入`mcdr`的`plugins`目录然后`!!MCDR reload all`即可以默认配置自动运行**

1. 将 `MCDR2Restic.mcdr`放入 MCDR 的 `plugins/`。
2. 插件加载时会自动检查并补齐 Python 依赖：

   - `PyYAML>=6.0`
   - `websocket-client>=1.8.0`

   MCDR packed plugin 会检查包内根目录的 `requirements.txt`，因此本插件把该文件保留为纯注释，避免 MCDR 在自动安装逻辑运行前拦截加载。OneBot 通知使用的依赖包名是 `websocket-client`，Python 导入名是 `websocket`。如果误装了另一个同名包导致 `websocket.WebSocketApp` 不存在，插件会尝试自动卸载错误包并重装 `websocket-client`。

   如果自动安装失败，可手动执行：

   ```bash
   pip uninstall websocket
   pip install PyYAML websocket-client
   ```

   在国内网络环境下，如果自动安装下载失败，可以给 MCDR 进程设置镜像环境变量后重载插件：

   ```bash
   export MCDR2RESTIC_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
   ```

3. 启动/重载 MCDR 后，如果 `config/mcdr2restic/config.yml` 不存在，插件会自动生成一份适配当前系统的示例配置。注释语言会跟随 MCDR 当前语言：中文使用中文注释，其他语言使用英文注释。

   在 Windows 上首次生成配置时，示例会自动改为 `.\restic.exe`、`.\restic-repo`、`.\server\world` 这类路径，并使用 YAML 单引号避免反斜杠转义问题。

4. 按需修改配置文件，修改完成后执行 `!!restic reload`


## 配置

运行时状态会写入 `config/mcdr2restic/state.yml`，例如玩家进入/退出标志、最近在线检查结果和最近备份结果。

`schedule.require_player_activity_in_wait_period` 为 `true` 时，正常定时备份采用纯事件驱动加触发时检查：

- 本周期有人加入：备份
- 无人加入，触发时 `list` 检查在线人数为 0：跳过
- 无人加入，触发时 `list` 检查在线人数不为 0：备份
- 无人加入，但本周期有人退出，即使触发时在线人数为 0：备份

在线人数通过 MCDR RCON 执行 `schedule.online_check_command`，默认是 Minecraft 的 `list` 命令。建议在 MCDR 中启用 RCON，否则插件只能依赖 join/left 事件估算在线人数。

```yaml
schedule:
  interval_seconds: 0
  cron_expression: "0 0 0,3,6,9,12,15,18,21 * * *"
  require_player_activity_in_wait_period: true
  online_check_command: "list"
```

`force_schedule` 是强制备份调度，不遵循玩家活动感知，默认关闭。它和正常调度一样支持固定间隔或 6 位 cron：`interval_seconds > 0` 时优先使用固定间隔；`interval_seconds = 0` 且 `cron_expression` 不是 `"0"` 时使用 cron；两者都为 0 时关闭。

```yaml
force_schedule:
  interval_seconds: 0
  cron_expression: "0"
```

默认生成的 restic 配置是一个可直接运行的最小本地示例：

```yaml
restic:
  executable: "./restic"
  working_directory: ""
  repository: "./restic-repo"
  password: "123456"
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
    - [
        "forget",
        "--keep-daily", "7",
        "--prune"
      ]
  backup_command:
    - "backup"
    - "./server/world"
    - "--tag"
    - "minecraft"
    - "--host"
    - "mcdr2Restic"
```

这样在 Linux/Windows amd64 上，即使 MCDR 工作目录下还没有默认路径的 restic，插件也会尝试自动下载。首次生成配置时默认只备份 `./server/world`；如果生成时检测到 `./server/world`、`./server/world_nether`、`./server/world_the_end` 三个目录都存在，会自动写入三世界目录。Windows 首次生成配置时会自动使用 `.\restic.exe` 和反斜杠路径，并默认排除 `session.lock`，避免 Minecraft 文件锁导致 restic 返回 3。示例密码 `123456` 只用于降低首次配置门槛，正式使用请改成自己的强密码。

自动下载会先请求 GitHub latest release API；如果 `api.github.com` 失败，会退回到内置的 `v0.19.1` 下载链接。下载时先试官方 GitHub 地址，再按 `download_proxy_prefixes` 顺序尝试代理。

`restic.password` 优先级高于 `restic.password_file`。如果 `password` 留空字符串，插件才会设置 `RESTIC_PASSWORD_FILE` 使用密码文件。`restic.repository` 会自动写入 `RESTIC_REPOSITORY`。

`restic.auto_init_local_repository` 为 `true` 时，如果本地仓库不存在或缺少 `config`，插件会在备份前自动执行 `restic init`。S3、B2、rest、sftp、rclone 等远端仓库不会自动初始化。

`restic.environment` 会在执行每条 restic 命令时叠加到环境变量中，适合加入 S3/B2 等后端需要的密钥变量。`repository`、`password`、`password_file` 会自动转换为 restic 对应环境变量，因此通常不需要在 `environment` 里重复写 `RESTIC_REPOSITORY`、`RESTIC_PASSWORD` 或 `RESTIC_PASSWORD_FILE`。

通知支持 OneBot QQ 和 Discord Webhook，二者默认关闭，可以同时启用。Discord 只需要填入频道 Webhook URL：

```yaml
discord:
  enabled: false
  webhook_url: ""
  username: "MCDR2Restic"
  avatar_url: ""
  message_prefix: "[MCDR2Restic]"
  mention_user_ids: []
  mention_role_ids: []
  mention_everyone: false
  send_timeout_seconds: 10
```

`messages` 里可以自定义管理员通知文本。可用变量包括：`{prefix}`、`{label}`、`{start_time}`、`{end_time}`、`{duration_seconds}`、`{status}`、`{message}`、`{detail}`、`{error}`。如果需要输出字面量花括号，请写成 `{{` 或 `}}`。

## 命令

- `!!restic status` 查看状态
- `!!restic start` 启用定时备份
- `!!restic stop` 禁用定时备份，并请求停止当前备份
- `!!restic backup` 立即备份
- `!!restic reload` 重载配置

默认命令权限等级为 `3`，可在配置中修改。

# 贡献
欢迎提交PR和ISSUE

# 演示
下面的图片显示了使用Restic备份MC的优势，每个快照都是那个时间点的完整备份，仅占用一半空间
![alt text](image.png)

# 许可证

本项目采用 **[GNU General Public License v3.0 (GPL-3.0)](LICENSE)** 许可证发布。
