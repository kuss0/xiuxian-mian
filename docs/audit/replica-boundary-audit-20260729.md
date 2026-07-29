# 副本运行层边界审计

日期：2026-07-29

范围：只读分析 `model/app_replica.py` 的顶层函数、内部调用和生产导入面。本轮不改副本状态机、不发送游戏命令、不在真实副本窗口拆运行代码。

## 工具与验证

- 工具：`tools/replica_boundary_report.py`
- 实现：Python AST，只读取源码，不导入生产模块、不读取或写入状态库。
- 回归：`tests/test_replica_boundary_report.py` 固定边界锚点和生产入口。
- 定向验证：`363 passed, 45 subtests passed`，覆盖边界报告、副本吸收、日志群展示、静场和加入副本。

## 当前规模

- 文件：`14692` 行
- 顶层函数：`616`
- 异步函数：`69`
- 生产导入入口：`17`

按稳定函数锚点划分后的边界：

| 区域 | 行区间 | 函数 | async | 跨区调用 出/入 |
| --- | ---: | ---: | ---: | ---: |
| 通知、按钮与路线提示 | 111-3528 | 133 | 25 | 99 / 79 |
| 轻量房间、组队规划与日志面板 | 3529-8137 | 234 | 1 | 168 / 244 |
| 黄龙征召调度 | 8138-8291 | 8 | 2 | 3 / 0 |
| 虚天殿状态、匹配与 reducer | 8292-12491 | 192 | 20 | 160 / 198 |
| 轻量副本命令 handler | 12492-14142 | 30 | 14 | 169 / 7 |
| 分发与根 handler | 14143-14673 | 19 | 7 | 46 / 117 |

最大跨区耦合：

1. 轻量命令 handler -> 轻量房间域：123 个调用。
2. 轻量房间域 -> 虚天殿状态域：100 个调用。
3. 虚天殿状态域 -> 轻量房间域：66 个调用。
4. 虚天殿状态域 -> 根分发：48 个调用。
5. 轻量房间域 -> 根分发：46 个调用。

函数级调用图没有跨区强连通环，但区域级存在大量双向依赖。这说明可以逐函数抽离，不能按大段行号直接剪文件。

## 生产入口

`model/app.py` 直接导入消息观察、按钮回调、进度 reducer、加入回复、黄龙调度和根分发等入口；`model/control.py` 直接导入日志群副本面板与 CD 展示。拆分期间必须保持这些入口签名稳定，先由 `app_replica.py` facade 转发，不能同时修改 app、control、UI 和状态结构。

## 拆分顺序

### Gate A：黄龙征召叶子

优先候选为 `8138-8291` 的 8 个函数：内部入边为 0，只有 `handle_huanglong_conscription_text` 与 `run_huanglong_conscription_scheduler` 被生产入口直接调用，向外仅 3 个依赖。可先移入独立模块，并在 `app_replica.py` 保留兼容 facade。

验收要求：真实征召文案回放、每日去重、身份选择、发送状态未知处理、原 patch 路径兼容和零额外发送回归。

### Gate B：日志群只读面板

从轻量房间域中只抽取格式化和只读 snapshot，不搬按钮执行与房间写入。先定义只读查询接口，再减少 `model/control.py` 对巨型模块的直接依赖。

### Gate C：轻量命令编排

轻量 handler 只有 7 个内部入边，但向外调用 169 次。必须先建立窄 `ReplicaContext`，提供状态读写、发送、日志、身份和房间查询能力；禁止把整个 `app_replica` module 当 context 传入。

### Gate D：虚天殿与通用房间核心

最后处理。它与轻量房间域有 100/66 个双向调用，是当前最易产生状态分叉、重复加入和静场失效的区域。只有 Gate A-C 降低边界调用后才进入物理拆分。

## 明确不做

- 不按 wxjerry 的目录结构整包覆盖本地主状态机。
- 不在副本事件进行中迁移持久化 schema 或房间结构。
- 不为减少行数删除手工命令、历史状态兼容或频道身份入口。
- 不把 callback token、发送重试、房间 reducer 同时迁移到多个模块。

结论：边界审计完成。下一项可安全实施的是 Gate A 黄龙征召叶子抽离；其余区域继续保持单一 reducer 所有权，直到依赖面先被压缩。

## Gate A 实施结果

同日已完成黄龙征召叶子抽离：

- 新模块：`model/features/replica_huanglong.py`
- `app_replica.py` 保留原函数名 facade，生产 `model/app.py` 与现有测试 patch 路径无需改变。
- 依赖在每次调用时通过 `HuanglongConscriptionContext` 注入，未把巨型 module 或全局 state 当作 context 传递。
- 原文件从 `14692` 行降至 `14597` 行；黄龙 facade 区为 9 个函数，跨区调用降为 `0 / 0`。
- 新增未知发送回归：首次 `send_game_command()` 无结果时只记录尝试时间，一小时内不重复发送，也不伪写已发送日。
- 抽离后定向验证为 `366 passed, 45 subtests passed`。

Gate A 已销号。下一物理拆分候选改为 Gate B 日志群只读面板；房间 reducer、加入/解散和路线执行继续留在原模块。

## Gate B 实施结果

2026-07-30 已完成日志群只读面板拆分：

- 新模块 `model/features/replica_panel.py` 定义不可变只读快照、总览/CD/帮助纯格式化，以及显式 read-model 绑定接口。
- `model/app_replica.py` 只负责从现有单一状态源构建快照，并继续独占房间写入、按钮 token、加入/进入/解散和 reducer；原格式化函数名保留 facade。
- `model/control.py` 仅保留 `build_log_group_replica_panel` 这一项巨型模块依赖，用于附带按钮的面板；三个只读格式化函数改从独立模块导入。
- AST 回归固定该边界，禁止 `control.py` 再次直接导入巨型模块中的只读格式化函数。

验证：`tests/test_replica_panel.py tests/test_replica_boundary_report.py tests/test_replica_huanglong.py tests/test_log_group_display.py tests/test_replica_absorb.py` 为 `308 passed, 45 subtests passed`。边界报告显示 `control.py` 对副本巨型模块的面板导入面已收敛为单个 `build_log_group_replica_panel`。

Gate B 已销号。下一步为 Gate C 轻量命令编排 context；在 context 完成前继续禁止搬动房间生命周期、发送恢复和路线 reducer。

## Gate C1 实施结果

2026-07-30 已完成 Gate C 的第一段低风险边界：

- 新模块 `model/features/replica_commands.py` 定义 `ReplicaTicketQueryContext` 和
  `ReplicaCommandMatchContext`，不导入生产 state、runtime 或巨型副本模块。
- `.查询副本` 的事件认领、只读快照、按钮构造和消息发送编排改由显式 context 注入；
  `app_replica.py` 保留原 handler facade，因此现有 patch 路径和生产入口不变。
- 轻量开房/加入参数解析与 `is_replica_group_command_text()` 的昆吾快速分流移入独立
  模块；开房、加入、进入、解散的房间写入与发送状态机仍由 `app_replica.py` 单一持有。
- AST 报告中轻量命令段跨区出边从 `169` 降为 `165`，入边从 `7` 降为 `6`；巨型文件
  当前 `14571` 行。没有迁移 reducer、按钮 token 或发送恢复。

验证：`tests/test_replica_commands.py tests/test_replica_absorb.py
tests/test_replica_boundary_report.py tests/test_replica_panel.py
tests/test_log_group_display.py` 为 `309 passed, 45 subtests passed`；全量
`3441 passed, 535 subtests passed`，`compileall` 与 `git diff --check` 通过。

Gate C 尚未整体销号。下一段 C2 应优先抽取 open flow 的高层编排端口；join/enter/
dissolve 涉及职业、神识、房间占用和发送未知恢复，继续留到端口边界稳定后处理。
