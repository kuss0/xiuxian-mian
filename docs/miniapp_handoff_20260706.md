# MiniApp 接手交接

更新时间：2026-07-06 01:10 CST

## 分工边界

接手 AI 只负责 MiniApp 相关代码开发、离线测试、mock 测试、文档补全和提交候选方案。它产出的内容只算候选交付，不等于上线授权。

主控补记（2026-07-06 07:45 CST）：用户已授权 MiniApp 已写候选进入受控上线。当前上线范围是通用 MiniApp 框架、钓鱼 MiniApp 入口 handler、观星台/世界 boss 入口识别与 registry；钓鱼仍受 `fishing_enabled` 总开关控制，观星台/世界 boss 不执行未知 HTTP 动作，不恢复旧文本自动链。

接手 AI 不负责生产上线，不允许自行重启 `xiuxian.service`，不允许打开旧钓鱼/观星台/世界 boss 自动化开关，不允许绕过安全锁、全局锁或 watchdog，不允许把 lab 代码直接接入生产 scheduler。

最终上线责任归当前主控：包括 diff review、测试复核、上线窗口选择、服务重启、生产探测、回滚决策、健康检查和 24h live 监测。接手 AI 完成写和测之后必须停在候选方案阶段，等待主控确认后才能进入生产。

## 当前目标

把后续 Telegram Mini App / WebApp 玩法统一到通用框架里，先覆盖：

- 灵溪垂钓：旧文本链已经不完整，入口命令仍是 `.钓鱼`。
- 观星台：入口命令仍是 `.观星台`，旧 `.安抚星辰` 等文本自动链路已不应继续硬刷。
- 世界 boss：已转 MiniApp，当前默认不自动出手，只提醒开打。
- 洞府寻宝：入口命令仍是 `.洞府`，当前只做 lab 协议候选和安全摘要，不接生产发送。

要求是三层结构、可审计、默认关闭、低发送、先 lab 后上线。不要给钓鱼单独造一次性实现，后续还会有更多玩法走 WebApp。

## 已有代码

当前工作树已有 MiniApp 框架，07:45 后已随主服务加载受控生产入口：

- `model/webapp_core.py`
  - `MiniAppAdapter`
  - `MiniAppFlowStep`
  - `MiniAppFlowPlan`
  - `MiniAppAdapterRegistry`
  - `MiniAppLaunchRequest`
  - `MiniAppInitDataStore`
  - URL/path/host 白名单、请求构造、敏感摘要。
  - `MiniAppCaptureRecord` / `MiniAppCaptureStore`
  - 显式 lab/capture 抓包结构：HTTP 执行器可挂 `capture_sink` 输出脱敏 JSONL，记录 endpoint、host/path、payload/response shape、安全体、状态码、耗时和 attempt；默认不启用，不保存 raw `token/initData/tgWebAppData/hash/user/header`。
- `model/features/fishing_miniapp.py`
  - 灵溪垂钓 adapter。
  - `/api/miniapp/xianxia-fishing/start|finish|result|next` 请求构造。
  - flow plan 与生产 flow helper；入口 handler 仍由 `fishing_enabled` 控制。
  - `build_fishing_proof()` 生成自然分数，不固定满分。
  - 连钓不再回群里发第二条 `.钓鱼`：完成当前竿后调用 MiniApp 页面“再来一杆”对应的 `/next` 拿新 token；若 `/next` 无 token 或失败，只落账当前竿并进入 MiniApp 退避。
- `model/features/trial_miniapp.py`
  - 天机试炼 adapter。
  - `/api/miniapp/xianxia-trial/start|finish|next` 请求构造。
  - lab-only flow：`/start` 读取 challenge/sequence/trapIds，本地生成 `trialProof` 后 `/finish`。
  - 当前只作为 MiniApp 统一层候选，不接生产 scheduler，不自动发送 `.天机试炼`。
- `model/features/cave_treasure_miniapp.py`
  - 洞府寻宝 adapter。
  - 已识别真实 `.洞府` 回包按钮 start 参数为 `df_*`；`df_*` 已纳入统一脱敏。
  - 候选请求构造暂按 `/api/miniapp/xianxia-dongfu/start|action`，需真实抓包校准后才能上线动作。
  - lab-only flow：读取页面状态后切换“寻宝”、入府、按提示或随机点小人、命中后再来一次/结算、耗尽后结算。
  - 状态解析明确区分 `神识 8/8` 为单局剩余出手/总出手，`游戏 0/3` 为今日已玩局数/总局数；不写死 7 次或 3 局。
  - 当前不接生产 scheduler，不加 UI 发令入口，不自动发送 `.洞府`。
- `model/features/stargazer_miniapp.py`
  - 观星台 adapter。
  - 对照 wxjerry 线补入 farm 协议声明：`/api/miniapp/xianxia-sect-farm/start|action` 请求构造。
  - 新增 launch/start/decide/action lab flow plan。
  - 新增纯函数解析 `domain.mode=stars` / `plots`：坏盘先 `soothe`，全盘 `可收集` 则 `collect`，空盘则 `pull`，否则按 remaining 等待。
  - 当前只做入口识别、协议样本、离线决策和请求构造，不接生产 scheduler，不自动执行 action。
- `model/features/miniapp_registry.py`
  - 注册 `cave_treasure`、`fishing`、`world_boss`、`stargazer`、`trial`。
  - `cave_treasure`、`world_boss`、`stargazer`、`trial` 目前是 manual-only/default-disabled placeholder。
- `model/features/fishing_runtime.py`
  - `handle_fishing_miniapp_entry()` 已接入生产 reply routing。
  - 入口账号归属按 `钓者：@...` 等文案校验，避免接管别人的按钮。
  - 结果只写安全摘要和日计数，不持久化 raw `initData`。
  - `.钓鱼 <鱼塘> <鱼饵>` 仍是有效入口，鱼塘/鱼饵继续由 UI 配置生成；废弃的是 MiniApp 接管后的旧文本 `.钓鱼状态/.试探咬饵/.提竿/.收竿` 后续链。
  - 入口 handler 会按今日剩余竿数传 `max_rounds` 给 MiniApp flow，一次入口内尽量用 `/next` 连钓；`next_failed/next_unavailable` 不触发群命令兜底。
- `model/ui.py`
  - `POST /api/miniapp-entry-probe`
  - 诊断白名单仅允许原入口命令：`fishing -> .钓鱼`、`stargazer -> .观星台`、`trial -> .天机试炼`。
  - 使用 `send_game_command(... track=False, max_retry=0, source_module="MiniApp诊断", chain_id="miniapp_entry_probe", queue_timeout=90)`，避免进入旧模块 pending 或补发风暴。

## 已有真实证据

已通过低风险入口诊断确认框架能识别按钮摘要：

- `wisemole` 发 `.钓鱼` 后回包：无【青竹钓竿】，这是账号资源问题，不是框架问题。
- `lalasin1` 发 `.观星台` 后回包：
  - 标题：`【星宫 · 观星台】`
  - 按钮：`进入灵圃`
  - `start_param.kind=farm`
  - `game_hint=stargazer`
- 日志中也看到其他账号发 `.钓鱼 青溪浅滩 凡饵` 后回包：
  - 标题：`【灵溪垂钓】`
  - 按钮：`进入灵溪垂钓`
  - `start_param.kind=fish`
  - `game_hint=fishing`
- 2026-07-06 日志中已有 `.洞府` 回包：
  - 标题：`【洞府】`
  - 按钮：`进入洞府`
  - 文案包含“前往外府石室寻宝”
  - `start_param.kind=df`
  - `game_hint=cave_treasure`

注意：诊断 API 已收敛回原命令，不保留 `fishing_qingxi` 白名单。接手方如需测试特定鱼塘/鱼饵，先写离线测试和方案，不要直接扩大生产诊断入口。

## 已验证命令

最近一次主控上线 validation：

```bash
.venv/bin/python -m pytest -q tests/test_retry_scheduler.py tests/test_webapp_core.py tests/test_miniapp_entry_probe.py tests/test_fishing_runtime.py
# 107 passed

.venv/bin/python -m py_compile model/runtime.py model/app.py model/features/fishing_runtime.py model/features/fishing_miniapp.py model/webapp_core.py
.venv/bin/python -m compileall -q model tests
git diff --check
# passed
```

07:45 重启 `xiuxian.service` 后：

- `xiuxian.service` active。
- post-restart journal 未见 Traceback/ImportError/TypeError。
- `global_enabled=1`，pending=0。
- 24 个身份的 `fishing_enabled/stargazer_enabled/guanxing_enabled/world_boss_enabled` 均为 0，`guanxing_monitor_enabled=0`。

前一版 focused validation：

```bash
.venv/bin/python -m pytest -q tests/test_miniapp_entry_probe.py tests/test_webapp_core.py
# 13 passed

.venv/bin/python -m py_compile model/ui.py model/webapp_core.py model/features/fishing_miniapp.py model/features/miniapp_registry.py
git diff --check
# passed
```

更早一次 geniş validation：

```bash
.venv/bin/python -m pytest -q tests/test_miniapp_entry_probe.py tests/test_webapp_core.py tests/test_log_entries.py tests/test_tiandao_judgement.py tests/test_ui_dual_track.py
# 60 passed
```

## 安全要求

必须遵守：

- 不持久化 `tgWebAppData`、`initData`、`query_id`、`hash`、`user`、raw `startapp` token。
- 日志只允许保存按钮文字、host、start 参数摘要、短 suffix、digest、game_hint。
- 抓包/协议样本只允许保存脱敏 record：host/path、payload keys/shape、安全体、response keys/shape、状态码和耗时；禁止保存 raw URL、raw initData、raw token、header 值。
- `fish_/farm_/boss_/rpt_/stk_/trial_/df_` 等 start token 都只允许保存摘要、kind、suffix、digest。
- HTTP 客户端必须走 adapter 的 host/path 白名单。
- MiniApp API 自动化默认关闭，不能接入生产 scheduler。
- 任何 live probe 必须低频、单次、无 retry，并由主控决定是否执行。
- 不能因为 WebApp 流程缺失而恢复旧文本钓鱼/观星台硬刷。
- 不能绕过全局锁、安全锁、watchdog。

## 接手方建议任务

1. 补齐 MiniApp core 单元测试：
   - host 白名单拒绝未知域名。
   - path 白名单拒绝未知路径。
   - initData store 不落盘。
   - sensitive query 参数不会进入日志。

2. 补齐 fishing MiniApp mock 测试：
   - `/start` 第一次返回 waiting/biteAt。
   - 到 biteAt 后 `/start` 第二次返回 challenge。
   - `/finish` 提交自然 proof。
   - `/result` 轮询 ready。
   - `/next` 可选连钓。
   - 失败、超时、非 ready、资源不足都保守退避。

3. 补齐 stargazer/world_boss placeholder 设计：
   - 先只识别入口按钮和 start 参数摘要。
   - 不实现生产动作。
   - 输出需要真实文案样本和 mock fixture。

4. 设计统一 WebApp 执行器，不要直接写进钓鱼模块：
   - launch initData 获取层。
   - API client 层。
   - flow runner 层。
   - audit/event 层。
   - capture/fixture 层：每个新 MiniApp 先沉淀脱敏协议样本，再用样本跑 mock/lab 回放。
   - UI config 层。

5. 形成开发报告：
   - 文件改动列表。
   - 测试命令和结果。
   - 仍未处理的风险。
   - 需要主控上线前确认的事项。

## 主控上线流程

接手方完成代码和测试后，主控负责：

1. review diff，确认没有生产开关被打开、没有 raw token/initData 落盘。
2. 跑 focused tests 和必要 regression tests。
3. 如涉及运行时代码，由主控选择安全窗口重启 `xiuxian.service`。
4. 重启后跑 health observer、defensive preflight、日志扫描。
5. 只在安全窗口做一次低风险 live probe。
6. 观察 24h monitor-repair loop，异常由主控处理或回滚。
