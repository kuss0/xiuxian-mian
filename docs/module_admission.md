# 新自动化模块接入准入

目标：新模块先证明自己可观测、可回放、可阻断，再接入发送链路。

## 阶段

0. `report-only` / `default-off`：只允许新增纯决策函数、只读报告、fixture 建议和单元测试。
   - 不加入 `MODULE_NAMES`。
   - 不注册 `ModuleManifest`。
   - 不接 `app.py` scheduler。
   - 不改变 `app.py` handler 顺序。
   - 不加 UI 开关。
   - 不新增任何 live send 面。
   - 不新增 live API 读取；若决策依赖 API/库存/角色快照，必须标注为 backup evidence。
   - 若需要统一审计，登记 `ReportOnlyFeatureContract`，但不登记 `ModuleManifest`。
   - 适用于从 Rust behavior 对齐过来的第一刀，例如 `model/features/auto_repair.py`、`model/features/search_node.py`。

1. `contracted`：模块准备进入自动化合同，但仍不默认发送。
   - 注册 manifest、reply family、replay module、duplicate guard。
   - 补真实文案 fixture 或明确记录 family 级样本缺口。
   - 报告工具必须能暴露 admission 结果。

2. `send-capable`：只有在真实文案、状态收口、CD fail-closed、补发/错峰和发送去重都完整后，才允许接入 scheduler/UI。
   - 默认关闭。
   - 默认以本地文案、广播、编辑、reply-context 和本地状态作为主证据。
   - API 最多作为备用证据；只有用户明确要求的储物袋或“文案 + API”策略才可把 API 纳入主流程。
   - 主动查询必须有低频边界。
   - 部署前必须跑专项测试和 contract report。

3. `archived`：官方玩法已停止或本地决定搁置的模块。
   - 保留历史 `reply_family` 映射，用于识别旧日志和避免未知 family 噪声。
   - 不计入 active readiness 缺口。
   - 默认不从候选工具输出 fixture 建议。
   - 不新增样本、不接 scheduler/UI、不复用旧状态机。
   - 后续同宗门新玩法按新模块重新走 `report-only -> contracted -> send-capable`。

## 必填清单

1. 在 `model/module_manifest.py` 注册 `ModuleManifest`。
   - 配好 `reply_families`、`replay_modules`、`duplicate_guard`。
   - 被动优先模块使用 `SEND_POLICY_PASSIVE_FIRST`。
   - 只有兜底查询才用 `ACTIVE_QUERY_LAST_RESORT`。
   - `tools/message_contract_report.py --contracts` 必须能显示该模块的合同行。

### report-only 合同

`ReportOnlyFeatureContract` 只描述尚未接入的模块草案：

- `stage` 固定为 `report_only`。
- `default_enabled`、`manifest_registered`、`scheduler_connected`、`ui_connected` 必须全为 false。
- `primary_inputs` 只能放本地文案、reply-context、本地状态或已存在快照。
- API、库存或角色接口只能放进 `backup_inputs`，且 `api_policy` 必须是 `backup_only`。
- 如果草案属于现有状态机的一部分，例如 `.搜寻节点` 属于太一，必须写 `parent_module`，不能另开独立发送链。

2. 真实文案必须进 `tests/fixtures/real_message_samples.json`。
   - 每条样本必须有 `source`、`module`、`family`、`event_type`、`text`。
   - 文案变更导致 handler 无法闭环时，只记录 message contract gap，不在群内自动确认修改。

3. 状态机必须有明确收口。
   - 框架批不迁移或重写状态机。
   - 多阶段状态用 `PersistedState` 或等价封装，不裸写散落 pending 字段。
   - 每个 pending 都要有超时、失败原因、恢复策略。

4. CD 必须 fail-closed。
   - 新代码优先用 `timing.cd_decision()` 或 `timing.cd_blocks()`。
   - 时间字段坏、非有限数、不可解析时阻发，并把原因写入 last_error 或 contract gap。

5. 补发和错峰必须进 `delayed_actions`。
   - 不新增散落的 `asyncio.sleep()` 补发链。
   - 必须设置 `source_module`、`op_id` 或 `dedupe_key`，方便查重和恢复。

6. 发送必须低频可解释。
   - 默认被动读取真实回复、广播、编辑消息。
   - 主动查询只作为兜底。
   - 日志群只汇报必要结论，低价值结果走汇总。

## 准入测试

- `tests/test_module_manifest.py` 覆盖 manifest、reply family、replay alias、准入合同。
- `tests/test_timing_cd_blocks.py` 覆盖 CD fail-closed 和可观测原因。
- 新玩法专项测试必须至少包含一条真实文案 replay。

## 现有模块就绪度

`admission OK` 只表示合同结构合法，不表示模块已经完整。现有模块整改看
`tools/message_contract_report.py --readiness`：

- `sample_complete`：该模块所有 reply family 都已有真实文案样本。
- `sample_partial`：已有部分样本，但仍有 family 缺口。
- `sample_missing`：登记了 reply family，但还没有真实文案样本。
- `contract_only`：当前没有 reply family，需确认它确实是 prompt-claim/monitor-only，或补 reply family。
- `archived`：只保留历史映射；不再补样本、不再接发送链路。

长期整改顺序：先补 `sample_missing` 与 `sample_partial` 的真实文案样本，再补 replay/parser/handler 专项测试；不要为了填报告而改运行状态机。

### 真实文案候选

`tools/real_message_candidate_report.py` 用来从本地 JSON/JSONL 里收集 fixture 候选：

- 只读本地文件，不写 `tests/fixtures/real_message_samples.json`。
- 不发送游戏命令，不读取 API。
- 只有记录里已经带 `family` / `reply_family` / `matched_family` 时才输出候选。
- 默认只输出 readiness 缺失 family；需要复核已覆盖 family 时才加 `--include-covered`。
- 默认跳过 archived family；只有历史审计才加 `--include-archived`。
- 输出结果是 fixture 建议，进入 fixture 前仍需人工确认文案确实来自真实 bot/系统消息。

## 改动分层

- 框架层：manifest、BehaviorSpec、准入合同、真实文案 fixture、contract report、只读合同行。
- 适配层：`reply_family` 映射、identity/reply-context 证据、发送元数据补齐。
- 模块层：parser、handler、scheduler、状态机。

同一批改动默认只做框架层。适配层和模块层 bugfix 必须单独列出原因、证据和测试，不能作为“框架整改”隐式带入。
