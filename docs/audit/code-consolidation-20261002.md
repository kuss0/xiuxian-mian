# 2026-10-02 有界代码收敛收尾

## 目标与边界

对照基线 `e477a7a2`，只收敛已有重复和确认不可达的私有代码，不扩功能、不改变账号配置、不迁移发送或恢复控制权。本批不是宣称全仓历史债务清零。

生产外独立 worktree：`/root/xiuxian-consolidation-20261002`，分支 `cleanup/consolidation-20261002`。生产现存 R2 备份改动另案保留。

## 已完成代码

| 项目 | 改动 | 保持不变 |
| --- | --- | --- |
| 储物袋解析 | `model/storage_bag_api_payload.py` 统一 9 个纯解析函数及默认鱼饵名称表，UI/runtime 用原名称导入 | 空库存与未知库存区别、数量/名称别名、嵌套字段优先级、身份候选后缀规则 |
| 配置布尔值 | `model/config_values.py` 统一 state/control/UI 三份相同实现 | 中英文开关、大小写、空值、未知值默认值和数字语义 |
| 旧副本入口 | 删除 4 个无调用、无路由注册的私有处理器及其 3 个独占辅助函数、1 个正则 | 当前轻量组队、外部拉人开关/去重、被动回包和历史流程解析 |

业务源码新增 223 行、删除 597 行，净减 **374 行**；新增回归测试 99 行，不将测试和说明文档冒充业务减行。没有新增依赖或通用框架。

删除的私有符号：

- `_handle_virtual_hall_auto_open_command`
- `_handle_virtual_hall_auto_dissolve_command`
- `_handle_virtual_hall_auto_dispatch_observer`
- `_handle_replica_dispatch_command`
- `_make_virtual_hall_auto_flow_id`
- `_has_active_virtual_hall_auto_flow`
- `_merge_virtual_hall_auto_dispatch_usernames`
- `_VIRTUAL_HALL_AUTO_OPEN_COMMAND_RE`

删除证据：前四个符号在仓库可执行源码/脚本中只出现于定义，未列入 `__all__`，也不在 `_handle_replica_group_command` 的显式分发表中；后三个辅助函数和正则只有上述处理器引用。未发现动态注册这些名称的代码。保留 `_handle_replica_dispatch_group_command`、`_handle_replica_external_dispatch_command`、轻量组队入口及 legacy notice；不能用“近期没有发送”替代可达性判断。

## 不合并的差异

- UI 与后台储物袋写入：后台支持 `write_empty`，空库存落账、跳过计数、认证/保活状态和测试注入边界不同；本次只共享纯解析，不合并有状态编排。
- MiniApp 暂停判断、提交 worker、消息尾部读取虽有相似代码，但依赖状态归属、异常恢复或 monkeypatch 边界，不顺手合并。
- 不修改 scheduler、reducer、天星保护、CommandAttempt Gate、限流、安全锁、Boss/验证逻辑；不重新启用已关闭业务。
- 现有自然业务验收项（原生钓鱼、Boss、备份等）仍按各自账本处理，不靠本批全量测试销号。

## 验证

- 第一轮储物袋/UI/配置相关回归：158 passed，26 subtests passed。
- 解析边界及副本相关回归：395 passed，4 subtests passed。
- 新增测试覆盖配置真/假/未知值、UI/runtime 别名复用、空库存区别、各库存容器、自定义名称、损坏 JSON、嵌套 owner 优先级、用户名后缀、不修改输入。
- 离线差分：固定 seed `20261002`，从基线 AST 单独加载 UI/runtime 原解析函数，对 3,000 组混合输入的库存、owner、扁平化、JSON、候选名称和数量共比较 36,000 次，输出及异常类型/消息一致。
- AST 核对五个被精简模块：所有保留的顶层函数/类函数体与基线一致，仅删除/移动目标定义。当前分发表及发送调用没有被改写。
- 仓库 Ruff 规则、`compileall model tests`、`git diff --check` 通过。
- 最终全量隔离测试：`15647 passed, 1408 subtests passed in 435.10s`。测试使用临时状态库，没有请求真实游戏或修改生产数据库。

## 发布与回退

发布前复核 pending、天星准备窗口及 health/watchdog；旧 worker 正常停机后用 SQLite backup API 创建独立快照，再快进合并并启动一次。检查身份模块开关及 MiniApp 配置保持一致，保留 sidecar 的明确关闭状态。

最终测试结果、提交号、备份路径及发布观察待下方补记。回退时用新的反向提交撤销本批代码，不 reset 工作区、不还原游戏运行数据；已有业务状态无需迁移。
