# 快速开始

## 使用前要准备什么

- 你已经安装并运行了 MCDReforged
- 你接受本插件实际进行文件备份的是 `restic`
- 你的系统为 Windows x64 或 Linux x64（否则需要手动安装 `restic` 并修改一些配置）

如果你完全没接触过 `restic`，也可以先按默认配置直接使用（这会使得备份与源文件在同一服务器，占用同一块硬盘空间，通常不是好的实现），再按需配置备份频率或对象存储。

默认设置：

- 备份频率为每三小时备份一次，24 小时内备份全保留，之后每天保留一个，共保留 7 天，可调节调度设置改变效果
- 默认备份存储位置为 `./restic-repo`（相对路径，默认基于 MCDR 工作目录解析）

## 第一步：安装插件

插件已入库 MCDR 插件仓库，在 MCDR 终端执行一条命令即可安装：

```text
!!MCDR plugin install mcdr2restic
```

后续更新同样只需一条命令：

```text
!!MCDR plugin install -U mcdr2restic
```

手动安装：在 [GitHub Releases](https://github.com/pfdr2333/MCDR2restic/releases/latest) 中下载最新版本，然后将其放入 MCDR 的 `plugins` 文件夹中，使用 `!!MCDR plugin load mcdr2restic.mcdr` 加载插件。

重载后，如果 `config/mcdr2restic/config.yml` 不存在，插件会自动生成一份示例配置。

## 第二步：按需修改配置文件

1. 配置文件注释和默认插件语言会跟随 MCDR 的语言设置，目前支持 `en_us`、`zh_cn`、`zh_tw`
2. 默认的 `restic` 备份命令会识别 `/server/` 下是原版单文件夹世界还是 Paper 的三文件夹世界并自动添加（无论有没有识别成功都会自动添加 `/server/world` 进备份范围）
3. 按需调整备份范围和保留时间。默认只备份世界目录；建议改为备份整个 `/server` 目录甚至整个 MCDR 目录，保护存档之外的配置、插件和文件状态
4. 按需调整存储方式和位置

## 第三步：重载配置

改完配置后，在 MCDR 里执行：

```text
!!m2r reload
```

如果你把命令根节点改掉了，就把 `!!m2r` 换成你自己的命令根。

## 第四步：查看当前状态

执行：

```text
!!m2r status
```

或者直接：

```text
!!m2r
```

你应该至少能看到这些状态信息：

- 插件是否启用
- 当前是否在备份
- Minecraft 是否已经处于可操作状态
- 下一次正常备份时间
- 下一次强制备份时间

如果快照缓存开启，状态页还会附带快照列表。第一次查快照时，可能需要等待插件调用一次 `restic snapshots --json`。

## 第五步：手动做一次备份

执行：

```text
!!m2r backup
```

这条命令会尝试按下面的顺序工作：

1. 检查 Minecraft 是否已经正常运行
2. 检查本地仓库是否被放进了备份源目录里
3. 如果你保持默认 `restic` 路径：优先用系统 PATH 中的 `restic`（开启 `use_path_restic` 时），否则配置目录内没有就自动下载，已有但低于内置信任版本会自动升级
4. 如果本地仓库还没初始化，就自动执行 `restic init`
5. 执行 Minecraft 保存相关命令
6. 执行 `restic backup`

第一次成功后，再执行一次 `!!m2r status`，通常就能看到快照列表。

## 最容易踩的 5 个坑

### 1. 仓库路径放进了备份源目录

错误示例：

```yaml
restic:
  repository: "./server/world/restic-repo"
  backup_command:
    - "backup"
    - "./server/world"
```

这样会让仓库被备份进自己，插件会直接拒绝执行。请把仓库放到备份源目录外面。

### 2. 没改示例密码就长期使用

示例密码只是为了降低第一次试运行门槛。正式使用时请一定改成你自己的强密码，或者改用 `password_file`。

### 3. 把 `schedule.cron_expression` 留空

正常调度的 `schedule` 不应该把 `cron_expression` 留空。要么填合法的 6 位 cron，要么改用 `interval_seconds`。

### 4. 以为维护会跟着每次备份一起跑

不会。维护由 `maintenance_schedule` 单独调度，或者你手动执行 `!!m2r maintenance`。

### 5. 以为 `restore` 命令会立刻恢复

不会。`restore` 默认只是把任务加入队列，执行要靠 `restore apply`。

## 国内网络环境提示

如果你在中国大陆使用：

- `pip` 装依赖失败，可能是网络或代理问题
- 自动下载 `restic` 失败，也可能是网络问题

这时建议：

- 先确认代理是否可用，或临时关闭有问题的代理再试
- 给 MCDR 使用的 Python / pip 配置镜像或代理
- 自动下载 `restic` 会自动依次尝试官方地址、自建镜像和内置加速前缀；也可以自行安装 `restic`，首次生成配置时插件会探测 PATH 并直接使用它
