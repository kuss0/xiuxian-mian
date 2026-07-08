# MiniApp 辅助最终交接（主线审核版）

更新时间：2026-07-08 17:35 CST

2026-07-08 17:35 主线复核补记：灵树已出现防作弊风险信号，主线把分数策略再次收紧为低分随机区间。UI 输入只作为区间中值保存，运行和持久化读取都会把固定值扩成至少 8 分宽区间；普通上限从 80 降为 45，旧 `[126,126]` / `[80,80]` 配置会归一化为安全区间。灵树仍不接生产 scheduler，也不在 `MINIAPP_MANUAL_RUN_COMMANDS`。

合并来源：

- `docs/miniapp_aux_handoff_20260707.md`：灵树 MiniApp 框架、跳一跳/飞一飞、UI 分数配置、mock/完整测试历史。
- `docs/miniapp_aux_test_audit_20260707.md`：MiniApp focused/full pytest、静态审计、灵树剩余风险。
- `docs/miniapp_aux_fix_handoff_20260708.md`：2026-07-08 安全修复、外部仓库吸收、主线复核补记。
- `/root/xianxia-companion`：`git@github.com:jiven303toto/xianxia-companion.git`，HEAD `4daadbc fixed bug`。
- `/root/xiuxian-wxjerry-main`：`git@github.com:wxjerry/xiuxian.git`，branch `main`，HEAD `feb0a59 feat: 支持天机试炼新题型`。

## 边界

本轮辅助只做候选代码、离线/mock 测试、静态审计、外部只读对照和文档合并。未重启服务、未上线、未 push、未发游戏命令、未执行新的 live probe、未读写生产 DB，未打开生产 scheduler 或旧自动化。

主线负责最终 diff review、测试复核、上线窗口、服务重启、生产灰度、回滚、健康检查和 24h 监测。本交付不等于上线授权。

## 当前结论

辅助候选 diff 和文档合并已干完，可以交给主线审核。主线已处理新增静态审计发现：天机试炼 `points/nodes` 为 dict 且 id 只存在于 dict key 时，保留 dict key 作为缺省 id，并补回归测试；planarity 无有效节点时直接失败，不再生成空 proof。

除上述阻断项外，本轮已完成以下候选补强：

- MiniApp 通用入口解析：新增 `iter_webapp_button_links()`，五个 MiniApp adapter 统一支持 `message.buttons`、`event.buttons`、`message.reply_markup`、`event.reply_markup`，并识别 `web_app.url` / `webview.url` / `web_view.url`。
- 天机试炼每日自动调度：新增 `trial_daily_scheduler_confirmed` 二次确认位；空配置默认关闭。主线复核后保留生产旧配置兼容语义：已有 `trial_daily_enabled=true` 且缺少确认位时视为已确认，避免静默停跑。
- 前端 MiniApp 状态徽标：按 `trial_daily_effective_enabled` 展示，避免旧状态残留 `trial_daily_enabled=true` 时误显示自动开启。
- 非幂等 MiniApp POST：显式禁用默认重试，覆盖 `fishing finish/next`、`trial finish/next`、`cave_treasure action`、`stargazer action`、`tree run_start/run_submit`。
- 灵树 MiniApp：保留入口诊断和分数配置，不接生产提交。`tree` 已从 `MINIAPP_MANUAL_RUN_COMMANDS` 移除，`ui_send_miniapp_manual_run()` 中灵树 `submit=True` 授权死代码已删除。
- 灵树分数策略：防作弊后目标改为低分随机区间，普通上限为 `20-45`，默认仍是几十：`jump 24-42`、`fly 24-45`；固定值和旧 126/150 canary 路径都会扩成安全区间。
- 灵树小游戏 proof：`jump` 按跳一跳充能落点，`fly` 按 Flappy 式点击上冲；proof replay 不超过保守低分上限。
- UI 分数配置：`score_controls.tree` 进入 MiniApp status snapshot，`/api/miniapp-tree-score-config` 只保存 per-identity 区间中值配置，不触发游戏。

## 主线阻断项处理记录

天机试炼 dict key-only 兼容缺口已由主线修复。

位置：

- `model/features/trial_miniapp.py::_iter_trial_items()` 当前对 dict 直接返回 `value.values()`，会丢失 dict key。
- 通用 meridian 分支当前也对 `points` dict 取 `values()`，只读取 value 内的 `id/key/name`。
- 平面化 solver 的 `nodes/edges` dict 兼容依赖 value 内有 `id/key/name` 或 edge value 内有 `source/target`。

风险样例：

```python
{
    "id": "meridian-dict-key-only",
    "type": "tianjiMeridianV1",
    "answer": ["p1"],
    "points": {"p1": {"x": 12, "y": 34}},
}
```

修复后 proof 会保留 tap id `p1`，并使用坐标 `12/34`。

```python
{
    "id": "planarity-dict-key-only",
    "type": "tianjiPlanarityV1",
    "nodes": {
        "a": {"x": 20, "y": 20},
        "b": {"x": 80, "y": 20},
    },
    "edges": {"ab": {"source": "a", "target": "b"}},
}
```

修复后会保留 dict key 作为缺省 id；planarity 空节点/无有效节点直接失败；已补 key-only dict 的 meridian/planarity 回归测试。

## 灵树协议事实

- MiniApp host 为 `asc.aiopenai.app`，API 前缀为 `/api/miniapp/xianxia-spirit-tree/`。
- 静态前端页面 `/miniapp/xianxia-spirit-tree` 已对照：`run/start` 需要 `token/initData/mode`，`run/submit` 需要 `token/initData/mode/runToken/proof`。
- `fly` 是 Flappy Bird 式点击上冲，不是长按飞行；proof 形状为 `flaps/durationMs/clientScore`。
- `jump` 是跳一跳充能落点；proof 形状为 `charges/durationMs/clientScore`。
- 曾有一次 lab 探测只调用 `run/start(fly)`，确认返回 `runToken/seed/runNo/used/limit/seasonId/playDate`；没有调用 live `run/submit` 或 `reward/claim`。
- `run/start` 会消耗一次小游戏次数，后续不应再随意 live probe。
- raw token、initData、runToken、seed 不写入文档；只允许保留安全摘要和字段形状。

## 外部参考取舍

可吸收：

- `xianxia-companion` 的“先排普通入口命令、等可信 bot 回包按钮再接管 MiniApp”分层模式。
- 入口摘要只保留 host、按钮文本、start 参数类型/后缀/digest 和敏感字段名，不保存 raw URL/token/initData。
- MiniApp solver 和 HTTP/WebView 接管拆开，方便离线 proof replay 和 mock 测试。
- Telegram 按钮结构兼容：同时扫描 `buttons` 与 `reply_markup`，兼容 `web_app.url`。
- `wxjerry/xiuxian` 的天机试炼字段别名：外层可能是 `data.trial.challenge` / `result.challenge`，challenge 字段可能用 `id/type/answer/solution`，点位和节点可能是 dict。

不照搬：

- 不照搬参考仓库每个 feature 自带一套 parser/redactor/transport；本项目继续以 `model/webapp_core.py` 为通用核心。
- 不照搬自动续排、自动跑完、自动补发状态命令的生产行为；当前辅助候选仍保持 `manual_only/default_enabled=False`，上线和灰度由主控决定。
- 不把 MiniApp 状态混进外部 API payload；本项目继续走现有 state/UI 边界。
- 不照搬 `wxjerry/xiuxian` 的 MiniApp request log：该实现会把 request/response/url 直接落日志，天机试炼路径里还记录过 `initData`，不符合本项目敏感字段脱敏边界。本项目继续使用 `MiniAppCaptureStore` 和 `safe_summary`，只保留脱敏事件。
- 不搬 `wxjerry/xiuxian` 的旧灵树 `.灵树状态/.定脉` 修复到新 MiniApp 链路：本项目 `灵树` manifest 已归档，旧 scheduler 不应被重新启用；wxjerry 旧树修复只作为“低发送、先同步状态”的设计参考。

已对照但无需改代码：

- 观星台 `remainingSeconds` 直读和 `物品x数量` 收获解析：lab `stargazer_miniapp.py` 已覆盖 `remainingSec/remainingSeconds/remainSeconds/remainingText/statusLabel/status`，收获解析已支持 `【物品】xN` 和裸 `物品xN`。
- 天机试炼新题型：lab 已支持 lights-out、memory、stargaze、planarity 和通用 meridian；wxjerry 的 solver 面更窄，本轮只吸收字段形态兼容。
- `356c394` 点卯宗门传功开关与 MiniApp 框架无关，未吸收。

## 修改文件

- `model/ui.py`
- `model/web/static/js/miniapp_ui.js`
- `model/features/tree_miniapp.py`
- `model/features/fishing_miniapp.py`
- `model/features/trial_miniapp.py`
- `model/features/cave_treasure_miniapp.py`
- `model/features/stargazer_miniapp.py`
- `model/webapp_core.py`
- `model/app.py`
- `model/config.py`
- `model/module_manifest.py`
- `tests/test_app_scheduler_contract.py`
- `tests/test_message_contract.py`
- `tests/test_miniapp_entry_probe.py`
- `tests/test_miniapp_protocol_flows.py`
- `tests/test_miniapp_daily_report.py`
- `tests/test_module_manifest.py`
- `tests/test_trial_runtime.py`
- `tests/test_webapp_core.py`
- `docs/miniapp_aux_handoff_20260707.md`
- `docs/miniapp_aux_test_audit_20260707.md`
- `docs/miniapp_aux_fix_handoff_20260708.md`

注意：当前 worktree 还包含 `model/features/wild_training.py` 和 `tests/test_wild_training.py` 的修改，未纳入本 MiniApp 交接结论，主线 review 时请分开看。

## 已验证

```bash
.venv/bin/python -m pytest -q tests/test_miniapp_protocol_flows.py tests/test_miniapp_entry_probe.py tests/test_webapp_core.py
# 87 passed

.venv/bin/python -m pytest -q tests/test_miniapp_protocol_flows.py tests/test_miniapp_entry_probe.py tests/test_webapp_core.py tests/test_persistence_runtime_flags.py
# 110 passed

.venv/bin/python -m py_compile \
  model/features/tree_miniapp.py model/ui.py model/state.py model/persistence.py \
  tests/test_miniapp_protocol_flows.py tests/test_miniapp_entry_probe.py
# passed

node --check model/web/static/js/miniapp_ui.js
# passed

git diff --check
# passed

.venv/bin/python -m pytest -q
# 2586 passed, 368 subtests passed

.venv/bin/python -m pytest -q \
  tests/test_miniapp_entry_probe.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_webapp_core.py \
  tests/test_miniapp_daily_report.py
# 104 passed

.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_miniapp_entry_probe.py
# 105 passed, 5 subtests passed

.venv/bin/python -m pytest -q \
  tests/test_miniapp_protocol_flows.py \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py
# 108 passed, 5 subtests passed

.venv/bin/python -m py_compile \
  model/webapp_core.py \
  model/features/fishing_miniapp.py \
  model/features/trial_miniapp.py \
  model/features/tree_miniapp.py \
  model/features/cave_treasure_miniapp.py \
  model/features/stargazer_miniapp.py \
  tests/test_webapp_core.py
# passed

.venv/bin/python -m py_compile \
  model/features/trial_miniapp.py \
  tests/test_miniapp_protocol_flows.py
# passed

.venv/bin/python -m pytest -q \
  tests/test_miniapp_protocol_flows.py \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_miniapp_daily_report.py \
  tests/test_trial_runtime.py \
  tests/test_module_manifest.py
# 141 passed, 5 subtests passed

node --check model/web/static/js/miniapp_ui.js
# passed

git diff --check
# passed
```

额外审计脚本覆盖 `jump/fly`、目标分 `20/30/50/80`、12 个 seed，共 96 组 proof replay；结果 `failure_count=0`，没有 replay mismatch、超分或 token 泄漏。

本次 02:57 文档合并未改运行代码，未重跑 pytest；只需补跑 `git diff --check` 确认文档 patch 无尾空白。

## 主线审核重点

- 确认“天机试炼 dict key-only 兼容缺口”已由主线修复并通过回归测试。
- 确认 `miniapp_daily` 虽然仍在全局 scheduler 表里，但默认和空配置下不会启动；生产启用必须由主线显式设置 `trial_daily_enabled=true` 和 `trial_daily_scheduler_confirmed=true`。
- 确认 `tree` 不在手动运行白名单；当前只保留入口诊断和分数配置，不提交小游戏成绩。
- 确认灵树分数上限为 80，不再允许普通 UI 或测试 canary 到 126/150。
- 复核非幂等 MiniApp POST 禁用重试是否覆盖主线认为有风险的全部 endpoint；若主线确认某 endpoint 服务端幂等，可单独恢复重试。
- 复核新增 `iter_webapp_button_links()` 是否覆盖生产 Telethon 按钮对象；当前 mock 已覆盖 `reply_markup.rows[].buttons[].button.web_app.url`，未执行 live probe。
- 上线前由主线决定是否补跑更大范围 pytest；辅助未做生产探测。

## 完成标记

辅助 agent 已干完本轮 MiniApp 候选开发、外部参考吸收、测试审计和文档合并。后续交给主线审核、修复阻断项、上线和监测处理。
