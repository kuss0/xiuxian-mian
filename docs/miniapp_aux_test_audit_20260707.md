# MiniApp 辅助测试审计

日期：2026-07-07

## 边界

本审计由辅助 agent 完成，只覆盖当前工作树的 MiniApp 相关离线测试、编译检查、JS 语法检查和静态风险扫描。未执行服务重启、上线、live probe、生产 DB 读写、commit 或 push。

结论：辅助 agent 代码/测试任务已干完，交付仍为候选 diff，不构成上线授权。

## 当前相关工作树

截至本轮辅助收口时，MiniApp 相关脏文件包括：

- `model/features/cave_treasure_runtime.py`
- `model/features/fishing_runtime.py`
- `model/features/stargazer.py`
- `model/features/stargazer_miniapp.py`
- `model/features/trial_miniapp.py`
- `model/features/trial_runtime.py`
- `model/webapp_core.py`
- `model/ui.py`
- `model/web/pages/index.html`
- `model/web/static/css/ui_fixes.css`
- `model/web/static/js/miniapp_ui.js`
- `tests/test_cave_treasure_runtime.py`
- `tests/test_fishing_runtime.py`
- `tests/test_miniapp_entry_probe.py`
- `tests/test_miniapp_protocol_flows.py`
- `tests/test_stargazer.py`
- `tests/test_trial_runtime.py`
- `tests/test_ui_dual_track.py`
- `tests/test_webapp_core.py`
- `model/miniapp_capture_summary.py`
- `tests/test_miniapp_capture_summary.py`
- `tools/miniapp_capture_summary.py`

另外 `docs/backlog_20260627.md` 也有 MiniApp 进度记录更新。

## 已跑验证

```bash
.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_trial_runtime.py \
  tests/test_cave_treasure_runtime.py \
  tests/test_miniapp_capture_summary.py
# 74 passed
```

```bash
.venv/bin/python -m pytest -q \
  tests/test_miniapp_protocol_flows.py \
  tests/test_miniapp_capture_summary.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_trial_runtime.py \
  tests/test_cave_treasure_runtime.py \
  tests/test_stargazer.py \
  tests/test_fishing_runtime.py \
  tests/test_ui_dual_track.py
# 138 passed
```

```bash
node --check model/web/static/js/miniapp_ui.js
# passed
```

```bash
.venv/bin/python -m py_compile \
  model/webapp_core.py \
  model/ui.py \
  model/features/trial_miniapp.py \
  model/features/trial_runtime.py \
  model/features/cave_treasure_miniapp.py \
  model/features/cave_treasure_runtime.py \
  model/features/stargazer_miniapp.py \
  model/features/fishing_miniapp.py \
  model/miniapp_capture_summary.py \
  tools/miniapp_capture_summary.py
# passed
```

```bash
.venv/bin/python -m py_compile \
  model/webapp_core.py \
  model/miniapp_capture_summary.py \
  model/features/trial_miniapp.py \
  model/features/trial_runtime.py \
  model/features/cave_treasure_miniapp.py \
  model/features/cave_treasure_runtime.py \
  model/features/stargazer_miniapp.py \
  model/features/stargazer.py \
  model/features/fishing_runtime.py \
  model/ui.py \
  tools/miniapp_capture_summary.py
# passed
```

```bash
.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_trial_runtime.py \
  tests/test_cave_treasure_runtime.py \
  tests/test_ui_dual_track.py \
  tests/test_stargazer.py \
  tests/test_fishing_runtime.py
# 178 passed
```

```bash
.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_miniapp_capture_summary.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_trial_runtime.py \
  tests/test_cave_treasure_runtime.py \
  tests/test_stargazer.py \
  tests/test_fishing_runtime.py \
  tests/test_ui_dual_track.py
# 191 passed
```

```bash
git diff --check
# passed
```

## 静态审计结论

- 本轮没有由辅助 agent 新增生产 scheduler 接入。
- 本轮没有由辅助 agent 执行 `systemctl`、restart、live probe、commit、push。
- `tests/conftest.py` 的测试隔离仍应继续作为默认保护；本轮未设置 `XIUXIAN_ALLOW_LIVE_TEST_DB=1`。
- MiniApp 相关文档仍强调不持久化 raw `tgWebAppData/initData/query_id/hash/user` 和 raw `fish_/farm_/trial_/df_` token。
- `trial`、`cave_treasure` 的当前测试覆盖了 runtime 授权/接管、入口诊断、capture summary 和 UI 相关基础回归，但真实生产观察仍归主控。
- `stargazer` MiniApp 已改为手动授权接管；未授权入口只暂停旧文本链，不跑 WebView/HTTP。
- `fishing/trial/cave_treasure/stargazer` runtime 均有全局暂停/身份停用前置保护，避免 MiniApp HTTP 绕过暂停语义。
- capture summary 已对 `latest_source/latest_error/recent.error` 做二次脱敏，adversarial JSONL 样本未泄漏 `trial_/df_` token、hash、Bearer header。

## 主控下一步建议

- 若准备提交，主控应再跑一次它负责的完整上线前套件和当前 live health / watchdog / defensive preflight。
- 对 `model/ui.py`、`miniapp_ui.js`、`model/features/*_runtime.py` 做最终 diff review，重点看手动灰度入口是否仍是单次授权、白名单命令、`track=False/max_retry=0` 诊断路径。
- 对 capture JSONL 再抽样 review 一次，确认没有 raw token/initData/hash/header 泄漏。
- 对观星台 manual-only 行为做最终确认：这会阻止仅因 `stargazer_enabled=True` 而自动跑 MiniApp HTTP。
- 上线和重启仍由主控执行，辅助 agent 本审计不构成上线授权。
