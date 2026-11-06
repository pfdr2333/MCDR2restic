# 命令

所有命令的默认根是 `!!restic`，别名 `!!m2r`。下文统一用更短的 `!!m2r` 示例（两者完全等价）。

命令树概览：

```text
!!m2r
├─ status
│  └─ p <页码>
├─ start
├─ stop
├─ cancel
├─ backup
├─ init
├─ maintenance
├─ unlock
├─ restore
│  ├─ list
│  ├─ apply
│  │  └─ --skip-safety
│  └─ <快照ID>
│     ├─ file <路径>
│     ├─ folder <路径>
│     └─ to <目录>
│        ├─ file <路径>
│        └─ folder <路径>
├─ unrestore all
├─ unrestore <任务ID>
└─ reload
```

注意：

- 直接输入 `!!m2r`，效果就是查看状态
- 如果你改了 `command.root`，所有命令都要跟着改
- 默认权限等级是 `3`

## 日常使用命令

### `!!m2r`

查看当前状态。

适合什么时候用：

- 想确认插件有没有启用
- 想看服务端当前是否允许备份
- 想看下一次备份时间
- 想顺便看快照列表

### `!!m2r status`

和直接输入 `!!m2r` 一样，都是查看状态。

### `!!m2r status p 2`

查看第 2 页快照列表。页码从 `1` 开始。

适合什么时候用：

- 你的快照很多，第一页看不完
- 想快速翻页查找某个时间点的快照

### `!!m2r backup`

立即执行一次手动备份。

它会先做的事：

- 检查你有没有权限
- 检查是否有恢复流程正在执行
- 检查 Minecraft 服务端是否已经准备好
- 备份开始前执行 Minecraft 保存相关命令

常见用途：

- 大更新前手动留一份快照
- 改完配置后验证备份是否可用
- 重大操作前先手动保底

### `!!m2r start`

启用定时备份总开关。

它做的事很直接：把配置里的 `enabled` 改成 `true`，然后唤醒调度线程。

### `!!m2r stop`

关闭后续定时备份，并请求停止当前备份。

这条命令的边界很重要：

- 它不是“立刻强杀一切任务”
- 如果当前没有备份在跑，它只是关闭后续调度
- 如果当前有备份在跑，它会发出取消请求，并尝试结束当前 `restic` 进程

只想取消当前任务、又不想动定时备份开关时，用下面的 `cancel`。

### `!!m2r cancel`

请求取消当前正在运行的任务，不改变定时备份开关。

- 正在备份：发出取消请求，并尝试结束当前 `restic` 进程
- 正在恢复：终止当前 `restic` 进程；若已进入停服恢复阶段，会按失败流程回滚到保护快照并自动重启服务端
- 两者都没有在跑：仅提示当前没有任务

## 仓库管理命令

### `!!m2r init`

按当前配置手动执行 `restic init`。

适合什么时候用：

- 你关闭了 `auto_init_local_repository`
- 你使用的是远端仓库，插件不会自动初始化
- 插件提示仓库尚未初始化，希望你手动确认后再初始化

注意：

- 这不是普通备份命令
- 它会占用和备份相同的运行槽位，所以备份或恢复执行期间不能用
- 成功后会让快照缓存失效，下次查状态时会重新刷新

### `!!m2r unlock`

按当前配置手动执行 `restic unlock`。

适合什么时候用：

- 插件明确提示仓库已被锁定
- 你确认没有其他备份、恢复、维护或外部 `restic` 客户端还在使用同一个仓库

注意：

- 解锁前请先确认不是“别的机器还在正常写这个仓库”
- 插件只会自动处理“本机死进程遗留的陈旧锁”
- 其他锁需要你自己判断后再手动解

### `!!m2r maintenance`

立即执行一次仓库维护。

它执行的是 `restic.maintenance_commands` 里配置的命令，例如：

```yaml
restic:
  maintenance_commands:
    - ["forget", "--keep-within", "1d", "--keep-daily", "7", "--prune"]
```

这条命令的边界也很重要：

- 它不会执行 `save-off`
- 它不会执行 `save-all`
- 它不会执行 `save-on`
- 它不是“备份前预处理”

## 恢复相关命令

### `!!m2r restore <快照ID>`

把“整份快照恢复”加入恢复队列。

示例：

```text
!!m2r restore 8f2f9c3d
```

这时不会立刻恢复，只是排队。

### `!!m2r restore <快照ID> file <路径>`

把“恢复单个文件”加入恢复队列。

示例：

```text
!!m2r restore 8f2f9c3d file /server/whitelist.json
```

### `!!m2r restore <快照ID> folder <路径>`

把“恢复单个文件夹”加入恢复队列。

示例：

```text
!!m2r restore 8f2f9c3d folder /server/world/region
```

### `!!m2r restore <快照ID> to <目录>`

把“整份快照恢复到指定目录”加入恢复队列。目录可以是相对 restic 工作目录的路径，也可以是绝对路径；含空格时用引号包住。

示例：

```text
!!m2r restore 8f2f9c3d to ./restore-out
!!m2r restore 8f2f9c3d to "D:\mc restore"
```

### `!!m2r restore <快照ID> to <目录> file <路径>`

把“恢复单个文件到指定目录”加入恢复队列。

示例：

```text
!!m2r restore 8f2f9c3d to ./restore-out file /server/whitelist.json
```

### `!!m2r restore <快照ID> to <目录> folder <路径>`

把“恢复单个文件夹到指定目录”加入恢复队列。

示例：

```text
!!m2r restore 8f2f9c3d to ./restore-out folder /server/world/region
```

### `!!m2r restore list`

查看当前恢复队列。

### `!!m2r unrestore 3`

删除任务 ID 为 `3` 的恢复任务。

### `!!m2r unrestore all`

清空当前恢复队列。

### `!!m2r restore apply`

真正执行恢复队列。

这是恢复流程里最关键的命令。它会：

1. 检查队列是否为空
2. 检查当前是否已有备份在跑
3. 检查 Minecraft 是否处于可操作状态
4. 自动先做一份保护快照
5. 停止 Minecraft
6. 执行恢复任务
7. 启动 Minecraft
8. 如果恢复阶段出错，尝试回滚到刚才那份保护快照

如果队列里的所有任务都带 `to <目录>`（恢复到指定目录），流程会简化为：直接执行恢复，不创建保护快照、不停服。目标目录不能是 restic 工作目录本身，也不能位于 restic 仓库或备份源目录内。

### `!!m2r restore apply --skip-safety`

和 `restore apply` 相同，但跳过恢复前的保护快照环节，直接停止 Minecraft 并执行恢复。

恢复失败时没有保护快照可回滚，世界可能停留在恢复了一半的状态，只在确认仓库快照可靠、愿意自行承担后果时使用。

## 配置和热重载命令

### `!!m2r reload`

重新读取配置文件。

适合什么时候用：

- 你修改了 `config.yml`
- 你改了备份路径、仓库、通知或命令根
- 你想应用新的调度配置

如果配置文件损坏或 YAML 不合法，插件会快速失败，不会静默重置成默认值。

## 常用操作示例

### 立即备份一次

```text
!!m2r backup
```

### 查看快照第二页

```text
!!m2r status p 2
```

### 手动执行一次维护

```text
!!m2r maintenance
```

### 先加入两个恢复任务，再执行

```text
!!m2r restore 8f2f9c3d file /server/whitelist.json
!!m2r restore 8f2f9c3d folder /server/world/region
!!m2r restore list
!!m2r restore apply
```

### 把快照恢复到指定目录（不停服）

```text
!!m2r restore 8f2f9c3d to ./restore-out
!!m2r restore apply
```

### 改完配置后重载

```text
!!m2r reload
```
