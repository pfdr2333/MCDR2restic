# 透传命令与 restic 基础

这一页讲两件事：

- `!!m2r exec` 透传命令怎么用，以及 restic 的常用操作
- 为什么说仓库不绑定插件——任意 restic 二进制都能对它进行读写

只会用 `!!m2r backup` 和恢复命令，也完全可以正常使用插件。这一页是给想把 restic 全部能力用起来的人准备的。

## 先建立一个关键认知：仓库是标准的 restic 仓库

MCDR2Restic 本身不存备份数据，它是把 [restic](https://restic.readthedocs.io/en/stable/) 接入 Minecraft 的自动化外壳：调度、停服保护、恢复队列、通知由插件负责，快照的读写全部交给 restic 完成。

所以插件创建的仓库就是一个标准的 restic 仓库：

- 格式与官方 restic 完全一致，没有任何私有改动
- 任意 restic 二进制——服务器上的、你自己电脑上的、任何操作系统上的——给出仓库地址和密码，就能读、能写
- 插件不在了，仓库还在；换掉插件，备份照用

访问仓库只需要两样东西：

1. 仓库地址：配置里 `restic.repository` 的值。相对路径（如 `./restic-repo`）基于 MCDR 根目录（`config.yml` 所在目录）解析
2. 密码：`restic.password` 的值，或 `restic.password_file` 指向文件的内容

远端仓库（S3、B2、SFTP 等）还要加上对应凭据，就是 `restic.environment` 里配的那些环境变量（例如 `AWS_ACCESS_KEY_ID`）。

## `!!m2r exec` 怎么用

把任意 restic 子命令原样透传给 restic 二进制执行：

```text
!!m2r exec <restic 参数...> [| grep/head/tail 过滤]
```

```text
!!m2r exec snapshots
!!m2r exec ls latest /server | head 20
```

插件会自动补齐的部分：

- 注入 `RESTIC_REPOSITORY`（来自 `restic.repository`）
- 注入 `RESTIC_PASSWORD` 或 `RESTIC_PASSWORD_FILE`（来自 `restic.password` / `password_file`）
- 合并 `restic.environment` 里的自定义环境变量
- 工作目录使用 `restic.working_directory`（留空即 MCDR 根目录）

行为要点（完整说明见[命令](commands.md)页）：

- 与备份、维护占用同一运行槽位，恢复流程执行期间不可用
- 没有超时，长命令用 `!!m2r cancel` 终止
- 聊天里最多实时显示 200 行、单行 300 字符，完整输出始终写入服务端日志
- 命令行参数可以覆盖注入的环境变量，例如 `!!m2r exec -r <另一个仓库> snapshots`
- 不会自动加 `--json`，需要就在参数里自己写

想看 restic 全部子命令，执行 `!!m2r exec help`，或直接翻[官方文档](https://restic.readthedocs.io/en/stable/)。

## 快照管理

`!!m2r status` 只能看快照列表；打标签、对比、统计这些管理动作都走 `exec`。

### 看有哪些快照

```text
!!m2r exec snapshots
!!m2r exec snapshots --json
!!m2r exec snapshots --host mcdr2Restic
!!m2r exec snapshots --group-by host,tags
```

默认备份命令给快照打了 `minecraft` 标签、主机名 `mcdr2Restic`，可以按这两个条件过滤。

### 看快照里有什么

```text
!!m2r exec ls latest
!!m2r exec ls 8f2f9c3d /server
!!m2r exec find --glob "**/level.dat"
!!m2r exec find *.json
```

`ls` 按目录浏览，`find` 按名字全局搜。两个命令都支持 `--json`。

### 对比两个快照：diff

```text
!!m2r exec diff 8f2f9c3d latest
```

输出会列出三组文件：

- `+`：后一个快照新增的
- `-`：后一个快照里删掉的
- `M`：两边都有、但内容改过的

结尾附带文件数与字节数汇总，适合确认"这两个时间点之间世界改动有多大"。

### 看仓库与快照占用

```text
!!m2r exec stats latest
!!m2r exec stats --mode raw-data
```

默认模式是"恢复出来有多大"；`--mode raw-data` 是"去重后仓库里实际占了多少"。想看仓库整体大小，用后者。

### 给快照打标签

```text
!!m2r exec tag 8f2f9c3d --add keep
!!m2r exec tag 8f2f9c3d --remove keep
```

想长期保留的快照先打个 `keep` 之类的标签，之后辨认、过滤都方便。

### 其他值得知道的子命令

- `copy`：把快照复制到另一个仓库（`--from-repo` 指定源仓库，源仓库密码用 `RESTIC_FROM_PASSWORD*` 环境变量给），做异地第二份仓库用
- `mount`：把仓库挂载成本地目录直接浏览快照（Linux / macOS 需要 FUSE，Windows 需要 WinFsp）
- `version`：看当前 restic 版本

## 从快照里取出文件

```text
!!m2r exec dump latest /server/whitelist.json
!!m2r exec restore 8f2f9c3d --target ./restore-out
```

`dump` 把快照里单个文件的内容直接打印出来，适合配置、文本文件；`restore` 把快照内容写进 `--target` 指定的目录。

注意边界：

- 往服务器目录恢复，用插件恢复流程 `!!m2r restore ... apply`，它会停服、做保护快照、失败回滚，见[状态与恢复](restore.md)
- `exec restore` 什么都不做直接写文件，只适合写到服务器之外的目录
- 往服务器外导出也可以用 `!!m2r restore <快照ID> to <目录>`，效果相同还不用记 restic 参数

## 快照清理与剪枝（forget / prune）

restic 删数据分两步，默认维护命令就是这两步合体：

1. `forget`：按保留策略删除快照记录
2. `prune`：清理只被已删快照引用的数据，真正释放空间

```text
!!m2r exec forget --dry-run --keep-within 7d
!!m2r exec forget --keep-within 7d --prune
```

常用保留策略：

- `--keep-last 5`：只留最近 5 份
- `--keep-daily 7`：每天留最近一份，共 7 份
- `--keep-within 7d`：7 天内的全保留

策略可以叠加。先加 `--dry-run` 看会删掉哪些快照，确认后去掉再跑。

要理解的一点：`forget` 执行后，快照记录就没了，即使还没 `prune`，普通方式也无法再按快照恢复；`prune` 执行后，数据被彻底清除，任何工具都找不回。重要快照先打标签，或用 `copy` 存进另一个仓库。

### 磁盘写满时的自救：不重打包的 prune

prune 默认要重打包一部分数据，需要额外的临时空间。仓库把磁盘或配额写满、prune 跑不动时，官方推荐的做法是：

```text
!!m2r exec forget --keep-last 3
!!m2r exec prune --max-repack-size 0
```

`--max-repack-size 0` 让 prune 跳过所有重打包，只删除已无引用的数据，几乎不需要临时空间。代价是"多个快照共用、但部分已失效"的数据块不会立刻腾干净，留给以后的常规 prune 处理。

平时想看 prune 会做什么而不动仓库：`!!m2r exec prune --dry-run`。

## 校验与修复：快照损坏了怎么办

### 平时的体检

```text
!!m2r exec check
!!m2r exec check --read-data-subsets 25%
```

- `check` 核对索引与元数据，速度快，出问题时先跑这个
- `--read-data-subsets 25%` 每次抽读四分之一的数据，跑四次覆盖全部；`--read-data` 一次读全量，最彻底也最耗时

### 先区分"打不开"和"内容坏了"

- 打不开仓库：提示密码错误、连不上 S3、仓库被锁，多半是凭据、网络或锁的问题，不是数据损坏，见[常见问题](faq.md)
- 内容坏了：`check` 明确报 `repository contains errors`、某个 blob `not found in repository`，说明存储介质或传输层出了问题，往下走修复流程

### 官方修复流程（按顺序）

restic 官方文档给损坏仓库的修复顺序是：

第一步永远是修索引。`repair index`（旧版本里叫 `rebuild-index`）会重建索引、剔除指向缺失数据的记录。只有确认仓库真的损坏时才执行，平时不需要。

```text
!!m2r exec repair index
```

如果 `check` 的输出明确提示你运行 `restic repair packs`，接着执行：

```text
!!m2r exec repair packs
```

然后剔除快照里访问不到的内容、生成新快照并删掉损坏的旧快照：

```text
!!m2r exec repair snapshots --forget
```

最后清理残留数据：

```text
!!m2r exec prune
```

修复完成后：

1. 被剔除内容的文件不再完整，重新做一次 `!!m2r backup` 让最新状态进仓库
2. 再跑一次 `check` 确认干净
3. 如果损坏疑似硬盘、对象存储层面的问题，用 `copy` 把快照迁移到新仓库，别只修不挪

两点提醒：

- 个别修复命令可能需要在终端里输入确认（y/N）。聊天框里没法回答这类提示，遇到时就到服务器终端里直接跑 restic，命令参数完全一样，见下文
- restic 能精确告诉你哪里坏了、也能修，但修复只能保住没坏的部分。损坏数据的最终兜底永远是"还有第二份仓库"

## 在插件之外访问仓库

### 同一台机器，直接用终端

服务器上本来就有 restic 二进制（插件自动下载的在 `config/mcdr2restic/restic`，Windows 是 `restic.exe`；或者系统 PATH 里的）。Linux：

```bash
export RESTIC_REPOSITORY=/opt/mcdr/restic-repo
export RESTIC_PASSWORD=你的密码
restic snapshots
```

Windows PowerShell：

```powershell
$env:RESTIC_REPOSITORY = "D:\mc\restic-repo"
$env:RESTIC_PASSWORD = "你的密码"
restic snapshots
```

也可以不设环境变量，直接 `restic -r <仓库地址> -p <密码文件> snapshots`。

### 在你自己的电脑上访问

1. 到 [restic 发布页](https://github.com/restic/restic/releases) 下载对应平台的二进制
2. 准备好仓库地址和密码（远端仓库再带上凭据）
3. 正常执行 restic 命令

本地仓库想从另一台机器访问，得让那个目录可达（网络共享、挂载）。更省事的做法是把仓库目录整个拷贝出来：restic 仓库是自包含的，拷到任何位置、用原密码就能用。拷贝时避开备份进行中的时段，避免拷到写了一半的数据。

远端仓库没有这个问题，任何机器配同样的地址和凭据即可：

```bash
export RESTIC_REPOSITORY="s3:https://s3.example.com/my-backup"
export RESTIC_PASSWORD=你的密码
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
restic snapshots
```

### 版本搭配

`!!m2r exec version` 可以看插件使用的 restic 版本。外部机器建议安装相同或更新的版本：restic 保证新版本能读旧仓库，但太旧的二进制可能读不了新版本写入的仓库格式。

### 和插件同时访问会冲突吗

restic 用锁文件保证同一时刻只有一个进程写仓库。插件和外部 restic 同时操作同一个仓库时，会正常排队或提示仓库被锁。看到锁先确认是不是还有客户端在正常使用，不要急着 `unlock`，见[常见问题](faq.md)。

## 容易遇到的问题

### 提示 `Fatal: wrong password or no key found`

密码不对。外部访问用的密码必须和插件配置里完全一致；配置用 `password_file` 时，把同一个文件交给 restic（`RESTIC_PASSWORD_FILE` 环境变量或 `-p` 参数）。

### 提示仓库被锁

见上文"和插件同时访问会冲突吗"，以及[常见问题](faq.md)。另外，取消正在执行的 `exec`（如 `check`）会强杀 restic 留下本机陈旧锁，之后可用 `!!m2r unlock` 清理；清不掉时按 FAQ 用 `!!m2r unlock --remove-all`。

### exec 的输出被截断 / 只看到一部分

聊天显示有 200 行上限，完整输出在服务端日志里。也可以用过滤管线缩小范围：`| head 50`、`| grep 关键词`、`| tail 20`。

### 参数里的 `|` 不是管道

`!!m2r exec` 自己解析 `|` 作为过滤阶段分隔符，所以 `--tag "a | b"` 这种引号里的独立 `|` 也会被切走。这类值请换个写法。

### `snapshots` 报 unable to open config file

远端仓库多半是凭据没配对：执行命令的环境里要有 S3 / B2 的 key，也就是 `restic.environment` 里配的那些变量。本地仓库则检查仓库路径是否指到了正确目录。

### 备份正常，但外部 restic 读不了仓库

restic 新版本写入的仓库格式，太旧的二进制可能不认识。把外部 restic 升级到与插件相同或更新的版本。

### 忘记密码了

没有任何找回手段。restic 的加密决定了没有密码就读不出任何数据，仓库等于作废。所以配置里的密码请务必另存一份（密码管理器之类），`password_file` 指向的文件本身也要记得备份。

### prune 中途断了 / 报空间不足

prune 的设计保证随时中断都不损坏仓库，重新跑一遍即可。空间不足的对策见上文"磁盘写满时的自救"。

## 使用边界提醒

- `!!m2r exec` 执行的是货真价实的 restic，没有任何拦截和兜底：`forget`、`prune` 删掉的数据插件找不回
- 插件封装过的操作（备份、恢复、维护）优先用插件，插件没封装的才走 exec
- `self-update` 会绕过插件的版本固定与校验机制，请勿对插件自动下载的 restic 二进制使用
