# 常见问题

## 1. 为什么提示仓库没有初始化

如果你看到类似“仓库未初始化”的提示，常见原因有三种：

- 这是一个本地新仓库，还没有 `restic init`
- 这是远端新仓库，插件不会替你自动初始化
- 你关闭了 `auto_init_local_repository`

处理方式：

- 本地仓库：通常可以保持 `auto_init_local_repository: true`
- 远端仓库或你想手动确认：执行 `!!m2r init`

## 2. 为什么提示仓库被锁定

这表示当前仓库已经被某个 `restic` 进程占用。

可能情况：

- 另一个备份还在跑
- 另一个维护任务还在跑
- 别的机器或别的客户端还在使用同一个仓库
- 本机上次异常退出，留下了陈旧锁

处理建议：

1. 先确认是不是还有别的正常任务在运行
2. 确认没有其他客户端在写这个仓库
3. 再考虑执行 `!!m2r unlock`

不要把 `unlock` 当成“看到锁就点”的常规操作。

## 3. 自动下载 `restic` 为什么没有触发

自动下载不是任何情况下都会触发。

常见原因：

- 你没有使用默认可执行文件路径
- 你的平台不是受支持的 Linux amd64 / Windows amd64
- 网络访问 GitHub 或下载代理失败

先检查：

```yaml
restic:
  executable: "./config/mcdr2restic/restic"
```

Windows 下对应的是：

```yaml
restic:
  executable: '.\config\mcdr2restic\restic.exe'
```

另外，如果你已经把 `restic` 装进了系统 PATH，可以在配置里开启 `use_path_restic: true`，插件会优先使用系统的 `restic`，完全绕过下载。

## 4. 自动初始化为什么没发生

自动初始化只针对本地仓库。

如果你写的是这类远端仓库：

```yaml
repository: "s3:https://s3.example.com/my-backup"
```

插件不会自动执行 `init`，需要你自己确认后手动处理。

## 5. 为什么备份被跳过了

如果你启用了玩家活动感知：

```yaml
schedule:
  require_player_activity_in_wait_period: true
```

那正常调度下，插件可能因为“本周期无人活动，且触发时无人在线”而跳过备份。

如果你不希望这样：

- 关闭玩家活动感知
- 或者加一套 `force_schedule`

例如：

```yaml
force_schedule:
  interval_seconds: 0
  cron_expression: "0 0 4 * * *"
```

## 6. 为什么维护没有跟着备份一起跑

因为现在的设计就是分开的。

维护由 `maintenance_schedule` 或 `!!m2r maintenance` 触发，不是备份流程的一部分。

## 7. `maintenance_schedule` 留空和写 `"0"` 有什么区别

区别非常大：

- 留空：按默认每天 `03:00` 处理
- 写 `"0"`：关闭维护调度

示例：

```yaml
maintenance_schedule:
  interval_seconds: 0
  cron_expression: ""
```

这不是关闭，而是默认 `03:00`。

真正关闭应该写：

```yaml
maintenance_schedule:
  interval_seconds: 0
  cron_expression: "0"
```

## 8. 为什么 `schedule.cron_expression` 留空后出问题

因为正常调度和维护调度的语义不一样。

- `maintenance_schedule` 留空会回到默认 `03:00`
- `schedule` 不建议留空

所以如果你想用正常调度，请：

- 填合法的 6 位 cron
- 或者直接用 `interval_seconds`

## 9. RCON 没配好会有什么影响

主要影响玩家在线检查。

默认配置里：

```yaml
schedule:
  online_check_command: "list"
```

插件会尽量通过 `server.rcon_query` 查询当前在线人数；如果 RCON 不可用，就只能更多依赖玩家加入/离开事件来估算。

建议：

- 想让玩家活动感知更稳，尽量把 RCON 配好

## 10. 为什么 `!!m2r stop` 之后备份没有瞬间消失

因为它的语义是“请求取消当前备份”，不是“保证这一瞬间已经完全结束”。

它会：

- 关闭后续调度
- 发送取消请求
- 尝试结束当前 `restic` 进程

但是否立刻完全结束，还取决于当前进程状态。

## 11. 为什么 `restore` 没有立刻生效

因为 `restore` 默认只是加入队列。

真正执行要靠：

```text
!!m2r restore apply
```

如果你只是执行了：

```text
!!m2r restore 8f2f9c3d file /server/whitelist.json
```

那么这只是排队，不是立刻恢复。

## 12. 恢复路径应该怎么填

推荐按 `restic` 工作目录根来写，并用 `/` 开头，例如：

```text
/server/whitelist.json
/server/world/region
```

不要写：

- `../outside`
- 工作目录外的绝对路径（这类路径用于 `to <目录>` 的目标，不用于选择快照内路径）

## 13. 恢复到指定目录为什么被拒绝

`to <目录>` 有五条安全限制：

- 不能写成管理共享 UNC（如 `\\主机\c$\…`）：同一位置请使用盘符路径
- 不能是 restic 工作目录本身：这种情况请用普通恢复流程
- 不能位于 restic 仓库内：恢复内容会写进仓库数据，破坏仓库
- 不能位于备份源目录内：直接改写服务器正在使用的文件，应走停服恢复流程
- 不能包含受保护目录（restic 工作目录、备份源目录、仓库、插件数据目录）：避免恢复覆写这些目录本身

调整目标目录后重新执行即可。

## 14. 配置文件损坏后插件会不会自动帮我修

不会。

如果配置 YAML 已经损坏，插件会快速失败，并保留原文件，不会静默改成默认配置继续运行。

这正是为了避免“看起来还在跑，实际上配置已经被改坏”的情况。

## 15. 为什么出现了 `config.yml.v8.bak` 这类文件

这是配置迁移时留下的旧版本备份文件。

例如：

```text
config.yml.v8.bak
```

表示插件把旧版本配置迁移到新版本前，先备份了当时的 `config.yml`。

这通常是正常现象，方便你回看旧配置内容。

## 16. 国内网络下依赖安装或下载失败怎么办

常见原因：

- Python / pip 拉依赖慢或失败
- 访问 GitHub API 或 GitHub 下载地址失败
- 代理配置有问题

建议按这个顺序排查：

1. 先确认你当前代理是可用还是反而在捣乱
2. 如果代理可能有问题，临时关闭后再试一次
3. 给 MCDR 使用的 Python / pip 配置镜像或代理
4. 自动下载 `restic` 时插件会自动依次尝试官方地址、自建镜像和内置加速前缀，无需额外配置；也可以自己下载 `restic` 放进配置目录，或装进系统 PATH 并开启 `use_path_restic`

## 17. 只想快速判断现在能不能用，最少要看哪几项

先看这几项就够了：

1. `!!m2r status` 能否正常返回
2. `restic.executable` 路径是否正确
3. `restic.repository` 是否写对
4. `backup_command` 是否指向了正确目录
5. `!!m2r backup` 能否成功跑完一次
