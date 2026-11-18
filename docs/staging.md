# 中转库（staging）

> **beta 功能：默认关闭。** 接口与行为可能变化，不建议作为唯一备份手段。启用前请完整阅读本页，并保留一份可独立恢复的远端备份。

中转库把一次备份拆成两段：

1. 冻结 Minecraft（`save-off`）后，先把世界写入**本地中转库**；
2. 解冻后再由后台线程把新快照复制到**远端库**。

它用「本地磁盘占用」换取更短的 `save-off` 冻结窗口，适合远端上传慢、玩家高峰期长、人多无法长时间save-off的服务器。

## 它解决什么问题

直传远端时，`save-off` 会一直保持到上传结束。150 GB 世界在 20 Mbps 上传下可能冻结十几小时，未落盘的区块持续堆在 JVM 内存里，最终可能挤爆服务器。

中转库把冻结窗口缩短为「读取变更 + 压缩写入本地」，后续增量通常是分钟级。代价是本地磁盘要预留约一个 world 的体积，首次启用仍然要完整写一次本地库。

## 启用前检查

- restic 版本为 0.19.1 或更新（插件内置版本满足要求），`copy` 命令可用；
- 中转库所在磁盘至少能容纳当前 world 体量，并留出 `min_free_percent_after` 的余量；
- 中转库、`cache_dir`、`password_file` 都必须在**所有备份源之外**；
- 已有远端库可以直接启用：本地库会从远端复制 chunker 参数，首次复制只传增量，不会重传整库；
- 恢复流程的保护快照**不走中转库**，始终直传远端，保证回滚快照在恢复开始前已经可用。

## 最小配置

```yaml
staging:
  enabled: true
  repository: "./staging-repo"          # 必须在备份源之外
  password: "换成强密码"                 # 与 password_file 二选一，password 优先
  password_file: ""
  min_free_percent_after: 15
```

保存后执行：

```text
!!m2r reload
!!m2r staging status
```

第一次备份会完整写入本地库；之后的备份只写增量，并由后台复制到远端。

## 工作流程

```text
调度 / 手动备份
└─ 预检（不冻结）
   ├─ 校验配置与路径隔离
   ├─ 确保本地库存在（必要时从远端复制 chunker 参数）
   ├─ 远端可达时比对 chunker_polynomial
   ├─ 本地 backup --dry-run --json 估算写入量
   └─ 检查磁盘剩余
└─ save-off → save-all flush
└─ 本地 restic backup
└─ save-on
└─ 唤醒复制线程
```

预检失败不会进入 `save-off`。本地库写满时，插件会在冻结窗口内执行一次带超时的应急 `forget --prune --max-repack-size 0`，随后仍以备份失败收尾并恢复世界自动保存。

复制线程使用独立取消信号，不占备份槽位：

- 启动、每次本地备份成功、退避到期、手动 `staging copy` 都会触发；
- 只复制游标之后的新快照，显式传快照 ID，避免已从远端删除的旧快照被重新复制回来；
- 远端探测有 `remote_probe_timeout_seconds`（默认 30 秒）独立短超时，远端宕机或断网时复制按退避重试而不是无限挂起；
- 网络/锁冲突按 60、300、900 秒退避重试；鉴权/配额失败停止自动重试；chunker 不匹配为永久失败；
  永久失败后仍每小时检查一次远端落后，修复问题后 `!!m2r staging copy` 会立即重试；
- 游标晚于本地最新快照、或游标记录的快照已不在本地库（时钟回拨、`state.yml` 被恢复/手改、
  本地库回滚）时，游标视为不可信：本轮全量重放并重建游标、无条件跳过本地 prune，
  状态页显示「游标状态：异常」；
- 恢复流程开始时复制让位，稍后自动重试；
- 复制、维护、purge 与手管 restic 命令由 `staging.io_lock` 串行，备份不占该锁。

## 维护

中转库开启后，`maintenance_schedule` 的顺序变为：

1. 先补齐复制；失败或仍有 backlog → **整轮维护跳过**，避免本地 prune 删掉尚未复制的快照；
2. 执行原有远端 `maintenance_commands`；
3. 本地 `forget --keep-last <keep_last> --prune --max-unused <prune_max_unused>`；
4. 到期时执行深度 prune：`--max-unused 0 --max-repack-size <prune_max_repack_size>`；
5. `restic cache --cleanup`。

本地保留数量由 `staging.keep_last` 控制，默认 2：1 个足够作为增量父快照，2 个留回退余地。

## 命令

```text
!!m2r staging status          查看模式、本地/远端最新快照、磁盘剩余、backlog、上次复制结果
!!m2r staging copy            手动触发一次补齐
!!m2r staging purge           重建远端库（仅文件系统型远端）
!!m2r staging purge --force   远端比本地新时确认重建
```

状态页也会增加三行：当前模式（中转库 beta / 直传）、本地最新快照、远端最新快照。

> 直接删除远端库中的复制快照（手动 forget 等）后，该快照不会从游标复活，本地 `keep_last` prune 也可能随后删除本地副本。删除远端快照前请自行确认本地库仍有保留。

`staging purge` 只支持文件系统路径的远端仓库（例如本地目录、挂载盘）。S3、REST、SFTP 等后端请新建空仓库后重新初始化。远端路径必须在备份源之外，且不能与中转库路径互相包含。purge 会把旧远端目录改名为 `<原路径>.purged-<时间>` 保留，再从本地库重新初始化并复制；初始化失败或取消会自动把旧目录改回。

远端库打不开（鉴权失败、配额耗尽、不可达）但目录仍然存在时，purge 默认拒绝执行；确认要重建请加 `--force`，旧目录同样会被保留。

purge 与恢复流程互斥：purge 期间 `!!m2r restore apply` 会被拒绝；`!!m2r cancel` 会在当前 restic 步骤结束后的阶段间隔终止 purge，并把旧远端目录改回原位。

每次 purge 都会留下一个完整的 `<原路径>.purged-<时间>` 副本，插件不会自动删除。确认新副本可恢复后，手动删除旧的 `.purged-*` 目录即可回收空间；删除前请再确认远端新库里至少有一次可用的复制快照。

## 磁盘写满自救

1. `!!m2r staging status` 确认本地库路径与磁盘剩余；
2. 清理中转库所在磁盘上的其他文件；
3. 如远端数据完整，可执行 `!!m2r maintenance` 让本地 prune 回收空间；
4. 如远端数据不完整而本地库完整，可执行 `!!m2r staging purge` 重建远端；
5. 仍无法腾出空间时，临时把 `staging.enabled` 改为 `false` 并 `!!m2r reload`，回到直传模式（本地库不会被自动删除）。

## 回退到直传

```yaml
staging:
  enabled: false
```

执行 `!!m2r reload` 后：

- 不再创建本地库、不启动复制线程、不修改 cache 与备份命令；
- 现有本地库与 `<仓库父目录>/cache` 可以手动删除；
- 远端库不受影响，已有的复制快照继续可用；
- restore 始终只认远端快照 ID，行为不变。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `chunker_polynomial 不一致` | 两端 chunker 参数不同，无法安全复制。用 `staging purge` 重建远端，或清理 staging 配置后重新启用；不要手动改远端库参数 |
| 远端鉴权失败 | 检查 `restic.password` / `password_file`；复制会停止自动重试，修复后 `!!m2r staging copy` |
| 远端配额/空间不足 | 清理远端或扩容；复制会停止自动重试，不会挤占本地磁盘 |
| 复制长期落后 | `!!m2r staging status` 查看 backlog 与上次错误；网络恢复后会按退避重试，也可手动 `staging copy` |
| 维护总是跳过 | 先解决复制 backlog；有 backlog 或游标异常时维护绝不删本地快照 |
| 状态页显示游标异常 | 游标与本地快照不一致（时钟回拨、`state.yml` 被恢复或手改）。插件会自动全量重放并重建游标，期间跳过本地 prune；持续出现请检查系统时钟与 `state.yml` 来源 |
| 恢复被拒绝：purge 正在重建远端 | 等 purge 完成，或先 `!!m2r cancel` 终止 purge 再重试恢复 |
| 首次启用很慢 | 首次本地备份是完整写入，属于预期；后续增量才会缩短冻结窗口 |

## 配置项

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `staging.enabled` | `false` | 总开关；关闭时零副作用 |
| `staging.repository` | `""` | 本地中转库路径；必须在备份源之外，且不得与本地路径型远端仓库相同或互相包含 |
| `staging.password` | `""` | 中转库密码 |
| `staging.password_file` | `./config/mcdr2restic/staging-password.txt` | 密码文件，仅在 `password` 为空时使用；作为实际凭据时文件必须存在 |
| `staging.min_free_percent_after` | `10` | 备份后剩余空间下限（占磁盘总容量百分比） |
| `staging.keep_last` | `2` | 本地保留快照数 |
| `staging.prune_max_unused` | `"5%"` | 日常 prune 允许的未使用数据比例 |
| `staging.deep_prune_interval_days` | `7` | 深度 prune 间隔天数，0 关闭 |
| `staging.deep_prune_max_unused` | `"0"` | 深度 prune 未使用数据上限 |
| `staging.prune_max_repack_size` | `"20G"` | 单次 prune 最大重打包量 |
| `staging.cache_dir` | `""` | 缓存目录；空 = 中转库父目录下的 `cache` |
| `staging.copy_retry_max` | `3` | 复制失败最大重试次数 |
| `staging.copy_retry_backoff_seconds` | `[60, 300, 900]` | 重试退避秒数 |
| `staging.copy_limit_upload_kib` | `0` | 复制到远端的上传限速（KiB/s），0 不限制 |
| `staging.remote_stale_warning_hours` | `24` | 远端落后告警阈值（小时），0 关闭 |
| `staging.emergency_prune_timeout_seconds` | `120` | 盘满应急 prune 的超时 |
| `staging.remote_probe_timeout_seconds` | `30` | 远端探测（cat-config）与远端快照读取的最长等待（秒），0 不限制；到期终止进程并按不可达处理，状态页回退上次已知值 |

## beta 退出条件

后续版本会在满足以下条件后再评估是否扩大提示入口或默认开启：

- 真实环境稳定运行至少 2 周，没有本地磁盘写满、远端长期落后或 purge 事故；
- 至少 3 种部署完成首次启用、旧远端迁移、盘满自救与 purge 演练；
- 首次启用、旧远端迁移、盘满自救、purge 限制与回退直传文档补齐。
