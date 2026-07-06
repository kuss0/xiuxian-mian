# MiniApp 辅助开发交接

更新时间：2026-07-07 02:11 CST

## 边界

辅助 AI 只负责代码候选、离线/mock/lab 测试、脱敏回包 fixture、静态审计和文档。不得自行重启服务、push、打开生产开关、执行 live probe、发送游戏命令、读写生产 DB，不能绕过全局锁/安全锁/watchdog。

主控负责最终 diff review、上线窗口、服务重启、生产灰度、回滚、健康检查和 24h 监测修复循环。

## 辅助 agent 已干完

本轮辅助 agent 已按 lab 边界补完 MiniApp 候选代码、mock/离线测试和审计文档；这不等于上线授权。

1. `观星台 MiniApp`
   - `stargazer_miniapp` 已补 `精华已成` 状态兼容，和 `可收集` 一样进入 collect 决策。
   - invalid domain/mock 坏样本会停在本地失败，不继续 action。
   - runtime 已改为和 `trial/cave_treasure` 一致的手动授权接管：只靠 `stargazer_enabled=True` 不再自动跑 WebView/HTTP；未授权但识别到入口时只暂停旧文本链并审计。
   - 增加 per-identity MiniApp run lock，重复入口不会并发跑 HTTP。

2. `洞府寻宝 MiniApp`
   - 已补 daily exhausted start fixture：`games used == limit` 且不在局内时直接 `daily_limit`，不发送 `hunt`。
   - 已补 reveal/session expired/app error fixture：错误分支停止本地流程，capture/result 不泄漏 `df_` token、hash、Bearer header。
   - runtime 已加全局暂停/身份停用前置保护，真实 launch 确认后才消耗手动授权。

3. `天机试炼 MiniApp`
   - 已补 solver 失败止损：无法求解的题面返回 `solve_failed`，不会向 `/finish` 提交猜测 proof。
   - 已补 K5 非平面图坏样本，确认失败时只调用 `/start`，不调用 `/finish`。
   - runtime 已加全局暂停/身份停用前置保护，真实 launch 确认后才消耗手动授权。

4. `钓鱼 MiniApp`
   - 已补全局暂停/身份停用前置保护：暂停时不跑 WebView/HTTP，写本地退避并低优先级审计。
   - 保持既有上线链路的 `max_rounds/next` 约束和 per-identity lock。

5. `MiniApp 通用框架/协议摘要`
   - `sanitize_webapp_secret_text()` 增加 Authorization/Bearer/Cookie 类 header 脱敏。
   - `MiniAppCaptureStore.append(raw dict)` 现在会递归清洗，防止绕过正常 capture 构造器。
   - capture summary 对 `latest_source/latest_error/recent.error` 做二次脱敏。
   - UI/命令白名单测试锁定：入口诊断仅 `cave_treasure/fishing/stargazer/trial`，手动执行仅 `cave_treasure/stargazer/trial`，不含 `world_boss`，也不把 `fishing` 放进手动执行。

## 已有代码入口

- 通用框架：`model/webapp_core.py`
- registry：`model/features/miniapp_registry.py`
- 观星台：`model/features/stargazer_miniapp.py`
- 洞府寻宝：`model/features/cave_treasure_miniapp.py`、`model/features/cave_treasure_runtime.py`
- 天机试炼：`model/features/trial_miniapp.py`、`model/features/trial_runtime.py`
- 钓鱼 MiniApp runtime 边界：`model/features/fishing_runtime.py`
- UI/API：`model/ui.py`、`model/web/static/js/miniapp_ui.js`、`model/web/static/css/ui_fixes.css`
- 回包摘要：`model/miniapp_capture_summary.py`、`tools/miniapp_capture_summary.py`
- 测试：`tests/test_webapp_core.py`、`tests/test_miniapp_protocol_flows.py`、`tests/test_miniapp_entry_probe.py`、`tests/test_trial_runtime.py`、`tests/test_cave_treasure_runtime.py`、`tests/test_stargazer.py`、`tests/test_fishing_runtime.py`、`tests/test_miniapp_capture_summary.py`、`tests/test_ui_dual_track.py`

## 必跑测试

```bash
.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_trial_runtime.py \
  tests/test_cave_treasure_runtime.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_miniapp_capture_summary.py

node --check model/web/static/js/miniapp_ui.js

.venv/bin/python -m py_compile \
  model/features/stargazer_miniapp.py \
  model/features/cave_treasure_miniapp.py \
  model/features/cave_treasure_runtime.py \
  model/features/trial_miniapp.py \
  model/features/trial_runtime.py \
  model/features/stargazer.py \
  model/features/fishing_runtime.py \
  model/webapp_core.py \
  model/miniapp_capture_summary.py \
  tools/miniapp_capture_summary.py \
  model/ui.py

git diff --check
```

## 安全要求

- 禁止保存 raw `tgWebAppData/initData/query_id/hash/user`。
- 禁止保存 raw `fish_/farm_/trial_/df_` token。
- capture/fixture 只能保留 endpoint、host/path、payload/response shape、安全摘要、状态码、耗时和 attempt。
- HTTP API 必须经 adapter host/path 白名单。
- 未能解析或求解时必须停止在本地失败，不向 `/finish` 提交猜测 proof。
- runtime 接管 WebView/HTTP 前必须尊重全局暂停和身份停用。
- 观星台 MiniApp 只能由手动授权接管；模块启用不等于自动跑 MiniApp HTTP。
- 不恢复旧文本自动链，不把 lab flow 接入生产定时 scheduler。

## 主控上线前事项

- 主控仍需最终 diff review，尤其是观星台 manual-only 行为是否符合本次上线策略。
- 主控负责上线窗口、服务重启、灰度账号真实探测、回滚、健康检查和 24h 监测。
- 若要继续扩大样本，只能由主控授权生产灰度；辅助 agent 不再消耗生产次数。

## 交付格式

辅助方完成后提交：

- 改动文件列表。
- 新增 fixture/测试说明。
- 测试命令与结果。
- 仍缺真实样本的分支。
- 主控上线前必须复核的风险点。
