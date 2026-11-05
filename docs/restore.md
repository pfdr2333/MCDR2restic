# 状态与恢复

这一页专门讲两件事：

- `status` 能看到什么
- 恢复任务到底怎么工作

## 一、`status` 页面怎么看

常用命令：

```text
!!restic status
!!restic status p 2
```

直接输入 `!!restic` 也可以。

## 状态页通常包含什么

状态页通常会显示：

- 插件是否启用
- 当前是否有备份在运行
- 当前是否有恢复流程在运行
- Minecraft 是否已经准备好
- 当前在线人数
- 最近一次在线检查结果
- 下一次正常备份时间
- 下一次强制备份时间
- 最近一次备份状态
- 快照列表

## 快照列表为什么有时会慢一点

因为快照列表默认来自 SQLite 缓存，而不是每次都直接重新查仓库。

默认相关配置：

```yaml
snapshot_cache:
  enabled: true
  page_size: 10
  query_timeout_seconds: 30
  database: "snapshots.sqlite3"
```

第一次查询、缓存失效、或者仓库刚被插件操作过时，插件会刷新缓存，所以会比平时慢一点。

## `status p <页码>` 是什么

它是快照翻页。

例如：

```text
!!restic status p 3
```

表示查看第 3 页快照。

## 二、恢复不是“立刻执行”

MCDR2Restic 的恢复流程是“先入队，再执行”。

你可以把它理解为：

1. 先把要恢复的内容一项项加入队列
2. 检查队列无误后，再统一执行

这能降低误操作概率。

## 恢复队列支持哪些任务

支持三类，每类都可以选择恢复到指定目录（`to <目录>`）：

- 整份快照恢复
- 单文件恢复
- 单文件夹恢复

## 1. 整份快照恢复

```text
!!restic restore 8f2f9c3d
```

作用：

- 把快照 `8f2f9c3d` 的“整份恢复”加入队列

不会发生的事：

- 不会立刻停服
- 不会立刻恢复

## 2. 单文件恢复

```text
!!restic restore 8f2f9c3d file /server/whitelist.json
```

作用：

- 把这个文件恢复任务加入队列

## 3. 单文件夹恢复

```text
!!restic restore 8f2f9c3d folder /server/world/region
```

作用：

- 把这个文件夹恢复任务加入队列

## 4. 恢复到指定目录

在上面三类任务后面加 `to <目录>`，可以把内容恢复到服务器之外的目录：

```text
!!restic restore 8f2f9c3d to ./restore-out
!!restic restore 8f2f9c3d to ./restore-out file /server/whitelist.json
!!restic restore 8f2f9c3d to ./restore-out folder /server/world/region
```

目录写法：

- 相对路径基于 restic 工作目录解析
- 绝对路径允许指向工作目录之外
- 含空格的路径用引号包住

安全限制：

- 不能是 restic 工作目录本身（这种情况请用普通恢复流程）
- 不能位于 restic 仓库内
- 不能位于备份源目录内

## 路径应该怎么写

推荐写法：使用相对 `restic` 工作目录根的路径，并以 `/` 开头。

例如：

```text
/server/whitelist.json
/server/world/region
```

如果你的 `working_directory` 是 MCDR 根目录，那么上面这种写法最容易理解。

## 不要这样写

下面这些路径不应该写：

- `../outside`
- 指向工作目录外部的绝对路径
- 想恢复整份快照时，却写一个空路径

## 查看当前恢复队列

```text
!!restic restore list
```

你会看到类似这样的列表：

```text
恢复任务
  1. [文件] 8f2f9c3d -> /server/whitelist.json
  2. [文件夹] 8f2f9c3d -> /server/world/region
  3. [整份快照] 8f2f9c3d -> / (恢复到 D:\mc\restore-out)
```

## 删除恢复任务

删除一个任务：

```text
!!restic unrestore 2
```

清空全部任务：

```text
!!restic unrestore all
```

## 真正执行恢复：`restore apply`

```text
!!restic restore apply
```

这条命令才是真正开始恢复。

它大致会做这些事：

1. 检查恢复队列不为空
2. 检查当前没有备份在跑
3. 检查 Minecraft 处于可操作状态
4. 自动创建一份保护快照
5. 请求停止 Minecraft
6. 逐条执行恢复任务
7. 启动 Minecraft
8. 如果恢复阶段出错，尝试回滚到刚才的保护快照

如果队列里的所有任务都带 `to <目录>`，流程简化为：直接执行恢复，不停服、不创建保护快照。带 `to` 与不带 `to` 的任务混在同一个队列时，仍然走完整的停服流程（带 `to` 的任务恢复到指定目录，其余恢复到工作目录）。

### 跳过保护快照

```text
!!restic restore apply --skip-safety
```

加 `--skip-safety` 参数可以跳过第 4 步的保护快照，直接停止 Minecraft 并执行恢复，适合明确知道自己在做什么、想省掉一次备份时间的场景。

代价是恢复失败时没有兜底：插件不会回滚，世界可能停留在恢复了一半的状态。除非仓库快照可靠且愿意自行承担后果，否则建议保留保护快照。

## 保护快照是什么

恢复前，插件会先自动做一份备份，用来兜底。

默认标签：

```yaml
restore:
  pre_restore_backup_tag: "mcdr2restic-pre-restore"
```

你可以在仓库里通过这个 tag 找到这类快照。

## 恢复失败时会怎样

如果恢复阶段失败，插件会尝试回滚到刚刚创建的保护快照；如果这次恢复用了 `--skip-safety`，则没有保护快照可回滚。

这意味着：

- 不是“失败了就什么都不做”
- 插件会尽量把服务器状态拉回恢复前

但你仍然应该理解：

- 回滚依赖那份保护快照本身可用
- 远端仓库异常、锁冲突、路径错误等情况，仍然可能导致恢复或回滚失败

## 恢复成功后，队列会怎样

成功的任务会被自动清空；失败时队列会保留，方便你排查后重试。

## 三、恢复前你最好确认的事

- 你选的快照 ID 是正确的
- 你知道恢复的是整份、单文件还是单文件夹
- 你知道目标路径会覆盖现有文件
- 当前没有人正在进行重要操作
- 你愿意接受停服和重启过程

## 四、推荐的恢复操作顺序

### 场景：只想恢复白名单文件

```text
!!restic status
!!restic restore 8f2f9c3d file /server/whitelist.json
!!restic restore list
!!restic restore apply
```

### 场景：只想恢复一个区块文件夹

```text
!!restic restore 8f2f9c3d folder /server/world/region
!!restic restore list
!!restic restore apply
```

### 场景：整份回滚到某个快照

```text
!!restic restore 8f2f9c3d
!!restic restore list
!!restic restore apply
```

### 场景：把快照导出到指定目录，不影响服务器

```text
!!restic restore 8f2f9c3d to ./restore-out
!!restic restore list
!!restic restore apply
```

这个流程不会停止服务器，适合取出存档、异地核对等场景。
