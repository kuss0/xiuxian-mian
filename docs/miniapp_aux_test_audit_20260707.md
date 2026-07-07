# MiniApp 辅助测试审计

日期：2026-07-07

## 边界

本审计只覆盖当前工作树 MiniApp 相关候选 diff、离线/mock 测试、编译检查、JS 语法检查和静态风险扫描。未执行服务重启、上线、push、新 live probe、生产 DB 读写或生产调度接入。

结论：辅助 agent 代码/测试任务已干完，交付仍为候选 diff，不构成上线授权。

## 本轮新增覆盖

- 灵树 `jump/fly` proof：
  - `fly` 按 WebView Flappy 式点击上冲物理实现，proof 为 `flaps/durationMs/clientScore`。
  - `jump` 按跳一跳平台/充能落点实现，proof 为 `charges/durationMs/clientScore`。
  - 默认目标分为几十，夹取范围为 `20-80`；测试覆盖 7 被夹到 20、999 被夹到 80。
  - 审计发现目标 80 时跳一跳连击可能冲到 87/94，已修复为接近上限时强制失误；新增回归测试锁定不超过 80。
- 灵树 lab flow：
  - `submit=False` mock 测试确认只调用 `start/run_start`，不会调用 `run_submit`。
  - `submit=True` 仅在 mock transport 中验证提交 payload 形状和脱敏结果。
- UI 配置：
  - `score_controls.tree` 已进入 MiniApp status snapshot。
  - `/api/miniapp-tree-score-config` 只保存 per-identity 目标分配置到 `tree_miniapp_score_configs`，不触发游戏。
  - 前端输入范围为 `20-80`。
- 安全白名单：
  - `MINIAPP_ENTRY_PROBE_COMMANDS` 包含 `tree` 入口诊断。
  - `MINIAPP_MANUAL_RUN_COMMANDS` 不包含 `tree`、`fishing`、`world_boss`。

## 已跑验证

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

额外审计脚本覆盖 `jump/fly`、目标分 `20/30/50/80`、12 个 seed，共 96 组 proof replay；结果 `failure_count=0`，没有 replay mismatch、超分或 token 泄漏。

## 静态审计结论

- 未把灵树候选 flow 接入生产 scheduler、手动运行白名单或 reward claim 自动链。
- 未保存 raw `tgWebAppData/initData/query_id/hash/user`，未把 raw token/runToken/seed 写入结果或文档。
- `run_tree_miniapp_game_lab_flow()` 的返回数据只暴露 state、run 安全字段、proof summary 和目标分配置，不返回 `runToken/seed`。
- 测试环境由 `tests/conftest.py` 指向临时数据目录；本轮未设置 `XIUXIAN_ALLOW_LIVE_TEST_DB=1`。
- 完整 pytest 已覆盖当前 state/persistence 改动，没有发现回归。

## 剩余风险

- live 只验证过一次 `run/start(fly)`，没有 live `run/submit` 样本；生产提交必须由主控单独灰度。
- `run/start` 会消耗小游戏次数，主控灰度前应确认账号次数和目标模式。
- proof 算法来自静态前端协议对照和本地模拟，仍需主控上线前 diff review。

## 完成标记

辅助 agent 已干完本轮 MiniApp 测试审计。上线、重启、生产探测、回滚和 24h 监测仍由主控负责。
