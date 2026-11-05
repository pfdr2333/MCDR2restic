# MCDR2Restic

MCDR2Restic 是一个基于 MCDReforged 的 Minecraft 备份插件。它将 [restic](https://restic.readthedocs.io/en/stable/) 无缝接入 Minecraft 的生命周期，在服务器运行时提供自动化增量备份，并集成定时调度、快照恢复、仓库维护及 IM 通知。

[![CI](https://github.com/pfdr2333/MCDR2restic/actions/workflows/ci.yml/badge.svg)](https://github.com/pfdr2333/MCDR2restic/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](https://github.com/pfdr2333/MCDR2restic/blob/main/LICENSE)

## 核心功能

- **去重增量备份**：基于 restic，每个快照都是完整备份，只占用增量空间
- **定时调度**：6 位 cron 或固定间隔；无人游玩自动跳过；可另设强制调度
- **快照恢复**：整份快照、单文件、文件夹，支持恢复到指定目录（不停服）
- **安全兜底**：恢复前自动创建保护快照，恢复失败自动回滚
- **仓库维护**：`forget`/`prune` 等按计划独立执行
- **多通道通知**：OneBot QQ、Discord Webhook 与自定义 Webhook
- **开箱即用**：自动下载 restic、自动初始化本地仓库、自动生成配置

## 建议阅读顺序

1. [快速开始](getting-started.md)
2. [命令](commands.md)
3. [配置](configuration.md)
4. [状态与恢复](restore.md)
5. [常见问题](faq.md)

## 术语说明

### restic 仓库

`restic` 用来保存快照数据的地方。可以是本地目录，也可以是 S3、B2、SFTP 之类的远端仓库。

### 快照

某一时刻的备份结果。`restic` 会把它记录成一个快照 ID。恢复时就是从某个快照恢复。一个快照就是一份备份。

### 正常调度

配置里的 `schedule`。它默认会结合周期内是否有玩家加入判断这次要不要备份。

### 强制调度

配置里的 `force_schedule`。只要到点就执行。默认关闭。

### 维护

配置里的 `maintenance_schedule` 和 `restic.maintenance_commands`。它们只负责仓库维护，例如 `forget --prune`，与备份流程分开执行。

### 恢复队列

通过 `!!restic restore ...` 加入的一组待恢复任务。它们不会立刻执行，需要 `!!restic restore apply` 命令以确认执行。
