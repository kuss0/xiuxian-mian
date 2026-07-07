# MiniApp 辅助测试审计

日期：2026-07-07

## 边界

本审计由辅助 agent 完成，只覆盖当前工作树的 MiniApp 相关离线测试、编译检查、JS 语法检查和静态风险扫描。未执行服务重启、上线、live probe、生产 DB 读写、commit 或 push。

结论：辅助 agent 代码/测试任务已干完，交付仍为候选 diff，不构成上线授权。

## 2026-07-07 灵树 MiniApp 追加审计：已干完

### 本轮新增/变更范围

- `model/features/tree_miniapp.py`：新增灵眼之树 MiniApp adapter、start request、launch/initData helper、start-only lab flow、flow plan 和 state parser。
- `model/webapp_core.py`：当前已有 `tree_` token 脱敏和灵树入口推断，本轮测试继续覆盖该能力。
- `model/features/miniapp_registry.py`：注册 `tree` adapter/flow plan，仍是 `manual_only=True`、`default_enabled=False`。
- `model/ui.py`：只允许 `.灵树` entry probe；未加入 MiniApp manual run。
- `model/module_manifest.py`、`model/app.py`、`model/control.py`：旧灵树 automation 归档，移除旧 tree scheduler 顺序，启动恢复清理旧 tree runtime。
- `model/features/tree.py`：保留旧文件供回溯/解析测试，补了简写脉象 parser 和可用命令选择测试兼容；没有重新接入 scheduler。

### 本轮安全结论

- 未重启 `xiuxian.service` 或任何生产服务。
- 未 commit/push。
- 未设置 `XIUXIAN_ALLOW_LIVE_TEST_DB=1`。
- live 侧只使用过一次已授权 `.灵树` 入口诊断；没有调用 `action/run_start/run_submit/reward_claim`，没有提交 `jump/fly` 成绩。
- raw `tgWebAppData/initData/query_id/hash/user` 和 raw `tree_` token 未写入文档；测试覆盖了脱敏结果。
- 旧灵树 reply family 仍保留历史映射，但 readiness/manifest 标记为 archived，默认 execution order 不再包含旧灵树。

### Rust 对照

已只读查看 `/tmp/xiuxianbot-rs-readonly`：

- `src/xiuxian/miniapp_http.rs` 支持通用 endpoint 拼接、POST envelope 分类、退避和错误脱敏。
- `src/tg/miniapp.rs` 将 WebView/initData 解析和 RPC 封装成可测纯函数 + 联网方法。
- `src/xiuxian/miniapp_fishing.rs` 体现“纯 proof/状态解析 + mock HTTP 测试 + 联网执行器”分层。

当前 Python 灵树候选与这些原则一致，但保守停在 start/readiness，不实现 proof 和自动执行器。

### 本轮新增验证

```bash
.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_miniapp_capture_summary.py \
  tests/test_fishing_runtime.py \
  tests/test_stargazer.py \
  tests/test_cave_treasure_runtime.py \
  tests/test_trial_runtime.py \
  tests/test_tree.py \
  tests/test_startup_recovery_guards.py \
  tests/test_app_scheduler_contract.py \
  tests/test_control_bool_coercion.py \
  tests/test_module_manifest.py \
  tests/test_message_contract.py
# 378 passed, 5 subtests passed
```

```bash
.venv/bin/python -m py_compile \
  model/app.py model/control.py model/module_manifest.py model/features/tree.py \
  model/webapp_core.py model/features/tree_miniapp.py model/features/miniapp_registry.py model/ui.py \
  tests/test_app_scheduler_contract.py tests/test_module_manifest.py tests/test_message_contract.py \
  tests/test_miniapp_protocol_flows.py tests/test_webapp_core.py tests/test_miniapp_entry_probe.py \
  tests/test_startup_recovery_guards.py tests/test_tree.py tests/test_control_bool_coercion.py
# passed
```

```bash
git diff --check
# passed
```

### 剩余风险

- `jump/fly` 小游戏 proof 未实现，后续需要主控授权新抓包后单独设计。
- `reward/claim` 只在 flow plan 中声明为候选端点，不能上线自动领取。
- 旧树归档会让 `灵树` readiness 统计从 sample_complete 变为 archived；主控上线前需要确认这是预期。

## 当前相关工作树

截至本轮辅助收口时，当前相关脏文件包括：

- `docs/miniapp_aux_handoff_20260707.md`
- `docs/miniapp_aux_test_audit_20260707.md`
- `model/app.py`
- `model/control.py`
- `model/features/miniapp_registry.py`
- `model/features/tree.py`
- `model/features/tree_miniapp.py`
- `model/module_manifest.py`
- `model/ui.py`
- `tests/test_app_scheduler_contract.py`
- `tests/test_control_bool_coercion.py`
- `tests/test_message_contract.py`
- `tests/test_miniapp_entry_probe.py`
- `tests/test_miniapp_protocol_flows.py`
- `tests/test_module_manifest.py`
- `tests/test_startup_recovery_guards.py`
- `tests/test_webapp_core.py`

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
