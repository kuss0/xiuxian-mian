# MiniApp 辅助开发交接

更新时间：2026-07-07 12:10 CST

## 边界

辅助 agent 只负责 MiniApp 候选代码、离线/mock/lab 测试、静态审计和文档。不得重启服务、push、打开生产开关、接生产 scheduler、执行新的 live probe、发送游戏命令、读写生产 DB，不能绕过全局锁/安全锁/watchdog。

主控负责最终 diff review、上线窗口、服务重启、生产灰度、回滚、健康检查和 24h 监测修复循环。本交付不等于上线授权。

## 当前结论：已干完

本轮辅助任务已完成灵树 MiniApp 新框架、跳一跳/飞一飞 proof 候选、UI 目标分配置、mock 测试和审计文档。代码仍是候选 diff，未接生产运行入口。

审计追加：发现跳一跳在目标 80 时可能因连击加分冲到 87/94，已改为接近上限时强制失误，确保 proof replay 分数不超过 80。

## 灵树协议事实

- MiniApp host 为 `asc.aiopenai.app`，API 前缀为 `/api/miniapp/xianxia-spirit-tree/`。
- 静态前端页面 `/miniapp/xianxia-spirit-tree` 已对照：`run/start` 需要 `token/initData/mode`，`run/submit` 需要 `token/initData/mode/runToken/proof`。
- `fly` 是 Flappy Bird 式点击上冲，不是长按飞行；proof 形状为 `flaps/durationMs/clientScore`。
- `jump` 是跳一跳充能落点；proof 形状为 `charges/durationMs/clientScore`。
- 已有一次后续 lab 探测只调用 `run/start(fly)`，确认返回 `runToken/seed/runNo/used/limit/seasonId/playDate`；没有调用 live `run/submit` 或 `reward/claim`。注意：`run/start` 会消耗一次次数，后续不应再随意 live probe。
- raw token、initData、runToken、seed 不写入文档；只允许保留安全摘要和字段形状。

## 代码实现

- `model/features/tree_miniapp.py`
  - 注册独立 `tree` adapter，`manual_only=True`、`default_enabled=False`。
  - 实现 `start` state parser、`run_start/run_submit` request 构造、`run_tree_miniapp_game_lab_flow()`。
  - `submit=False` 时只做到 `start + run_start + 本地 proof 准备`，不提交成绩。
  - `submit=True` 仅作为 mock/lab 候选路径；生产 wrapper 存在但未接 UI 手动运行、scheduler 或自动化。
  - 跳一跳/飞一飞目标分默认是几十：`jump 24-42`、`fly 24-45`；硬性夹到 `20-80`，避免个位数和异常高分。
- `model/state.py` / `model/persistence.py`
  - 新增 `tree_miniapp_score_configs` meta JSON，用于持久化 UI 分数配置。
- `model/ui.py`
  - `/api/miniapp-status?send_as_id=...` 返回 `score_controls.tree`。
  - 新增 `/api/miniapp-tree-score-config` 保存 per-identity 目标分配置，只保存配置，不触发游戏。
  - `MINIAPP_MANUAL_RUN_COMMANDS` 仍不包含 `tree`。
- `model/web/static/js/miniapp_ui.js` / `ui_fixes.css`
  - MiniApp 页面新增灵树目标分输入，范围 `20-80`。
- 旧 `灵树` automation 已归档；不得重新启用旧钓鱼/观星台/世界 boss 自动化或旧灵树 scheduler。

## 已验证

```bash
.venv/bin/python -m pytest -q tests/test_miniapp_protocol_flows.py tests/test_miniapp_entry_probe.py tests/test_webapp_core.py
# 85 passed

.venv/bin/python -m py_compile \
  model/features/tree_miniapp.py model/ui.py model/state.py model/persistence.py \
  tests/test_miniapp_protocol_flows.py tests/test_miniapp_entry_probe.py
# passed

node --check model/web/static/js/miniapp_ui.js
# passed

git diff --check
# passed

.venv/bin/python -m pytest -q
# 2583 passed, 368 subtests passed
```

额外审计脚本覆盖 `jump/fly`、目标分 `20/30/50/80`、12 个 seed，共 96 组 proof replay；结果 `failure_count=0`。

## 主控复核点

- 重点 review `tree_miniapp.py` 的 proof 生成是否符合静态前端协议和“几十分、不追榜”的策略。
- 确认 `tree` 仍未进入 `MINIAPP_MANUAL_RUN_COMMANDS`、生产 scheduler、旧 tree scheduler 或 reward claim 自动链。
- 若要生产灰度，主控另开授权窗口，先用极少次数验证 `run/submit`；辅助 agent 不再消耗 live 次数。

## 完成标记

辅助 agent 已干完：灵树 MiniApp 候选框架、跳一跳/飞一飞 proof、UI 可调目标分、持久化配置、mock 测试、完整测试和文档均已完成。上线仍由主控负责。
