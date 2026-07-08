# MiniApp 辅助测试审计

日期：2026-07-08

## 2026-07-08 修复补记

本文件保留 2026-07-07 测试审计历史；主线交接请以最终合并版 `docs/miniapp_aux_fix_handoff_20260708.md` 为准。

本次辅助修复已干完：focused MiniApp 测试 `104 passed`，Python 编译、前端 JS 语法和 `git diff --check` 均通过。主线复核后补充旧生产配置兼容测试，focused MiniApp 测试为 `105 passed`。验证仍限离线/mock/静态范围；未上线、未重启、未发游戏命令、未做 live probe、未读写生产 DB。

主线 2026-07-08 18:05 防作弊补记：灵树分数策略已从本历史审计的 `20-80` 继续收紧为 20 内低分随机区间，单点目标分改为区间中值并自动扩成随机区间；旧固定分配置只作为迁移测试样本。

## 边界

本审计只覆盖当前工作树 MiniApp 相关候选 diff、离线/mock 测试、编译检查、JS 语法检查和静态风险扫描。未执行服务重启、上线、push、新 live probe、生产 DB 读写或生产调度接入。

结论：辅助 agent 代码/测试任务已干完，交付仍为候选 diff，不构成上线授权。

13:01 CST 补充复核：未再改动代码；重新跑过 Python 编译、前端 JS 语法、`git diff --check` 和 MiniApp focused 测试，均通过。未执行上线、重启、新 live probe、生产 DB 访问或生产调度接入。

## 本轮新增覆盖

- 灵树 `jump/fly` proof：
  - `fly` 按 WebView Flappy 式点击上冲物理实现，proof 为 `flaps/durationMs/clientScore`。
  - `jump` 按跳一跳平台/充能落点实现，proof 为 `charges/durationMs/clientScore`。
  - 默认目标分为 20 内低分；主线防作弊复核后默认 `jump 8-16`、`fly 8-18`，固定高值会扩成 `14-20` 随机区间。
  - 审计发现高目标时跳一跳连击可能冲分，已修复为接近上限时强制失误；主线新增回归测试锁定不超过保守上限。
  - 飞一飞规划参数已加本地上限保护：`beam_width <= 640`、规划时长 `<= 120000ms`、规划帧数 `<= 7600`。
- 灵树 lab flow：
  - `submit=False` mock 测试确认只调用 `start/run_start`，不会调用 `run_submit`。
  - `submit=True` 仅在 mock transport 中验证提交 payload 形状和脱敏结果。
- UI 配置：
  - `score_controls.tree` 已进入 MiniApp status snapshot。
  - `/api/miniapp-tree-score-config` 只保存 per-identity 区间中值配置到 `tree_miniapp_score_configs`，不触发游戏。
  - 前端输入范围由主线统一策略下发，当前为 20 内低分区间。
  - 新增身份隔离测试：不同 `send_as_id` 保存不同目标分，未配置身份回落默认几十区间。
- 安全白名单：
  - `MINIAPP_ENTRY_PROBE_COMMANDS` 包含 `tree` 入口诊断。
  - `MINIAPP_MANUAL_RUN_COMMANDS` 不包含 `tree`、`fishing`、`world_boss`。

## 已跑验证

```bash
.venv/bin/python -m pytest -q tests/test_miniapp_protocol_flows.py tests/test_miniapp_entry_probe.py tests/test_webapp_core.py
# 87 passed

.venv/bin/python -m pytest -q tests/test_miniapp_protocol_flows.py tests/test_miniapp_entry_probe.py tests/test_webapp_core.py tests/test_persistence_runtime_flags.py
# 110 passed

# 2026-07-07 13:01 CST 补充复核，同一 focused suite 仍为 110 passed

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
```

额外审计脚本覆盖 `jump/fly`、目标分 `20/30/50/80`、12 个 seed，共 96 组 proof replay；结果 `failure_count=0`，没有 replay mismatch、超分或 token 泄漏。

## 静态审计结论

- 未把灵树候选 flow 接入生产 scheduler、手动运行白名单或 reward claim 自动链。
- 未保存 raw `tgWebAppData/initData/query_id/hash/user`，未把 raw token/runToken/seed 写入结果或文档。
- `run_tree_miniapp_game_lab_flow()` 的返回数据只暴露 state、run 安全字段、proof summary 和目标分配置，不返回 `runToken/seed`。
- `tree_miniapp_score_configs` 已有保存/重载 roundtrip 测试，确认服务重载后配置可恢复。
- 测试环境由 `tests/conftest.py` 指向临时数据目录；本轮未设置 `XIUXIAN_ALLOW_LIVE_TEST_DB=1`。
- 完整 pytest 已覆盖当前 state/persistence 改动，没有发现回归。

## 剩余风险

- live 只验证过一次 `run/start(fly)`，没有 live `run/submit` 样本；生产提交必须由主控单独灰度。
- `run/start` 会消耗小游戏次数，主控灰度前应确认账号次数和目标模式。
- proof 算法来自静态前端协议对照和本地模拟，仍需主控上线前 diff review。

## 完成标记

辅助 agent 已干完本轮 MiniApp 测试审计。上线、重启、生产探测、回滚和 24h 监测仍由主控负责。
