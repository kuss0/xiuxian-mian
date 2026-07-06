# MiniApp Lab 候选交付报告

更新时间：2026-07-06 02:05 CST

本轮补充复核：2026-07-06（02:05 版本未执行生产动作；07:45 主控复核后进入受控上线）

## 边界

02:05 版本只完成 MiniApp lab/dev 候选代码、mock 测试和审计说明，不代表上线授权。

07:45 主控收到明确上线授权后，边界调整为“代码受控上线、开关控制运行、默认不放量”。钓鱼 MiniApp 入口 handler 可进入生产进程，但仍受 `fishing_enabled` 总开关约束；观星台、世界 boss、洞府寻宝仍只上线入口识别/摘要/诊断框架或 lab 候选，不恢复旧文本自动链，不执行未知 HTTP API。

02:05 版本未执行以下操作：

- 未重启 `xiuxian.service` 或任何 systemd 服务。
- 未接入生产 scheduler。
- 未打开钓鱼、观星台、世界 boss、洞府寻宝旧自动化开关或新生产动作。
- 未执行 live probe。
- 未设置 `XIUXIAN_ALLOW_LIVE_TEST_DB=1`。
- 未读写 live DB。

07:45 受控上线前仍保持以下限制：

- 不打开钓鱼、观星台、世界 boss、洞府寻宝旧自动化开关或新生产动作。
- 不持久化 `tgWebAppData/initData/query_id/hash/user`。
- 不绕过全局锁、安全锁、watchdog。
- 不把观星台/世界 boss/洞府寻宝接入未知 HTTP 动作。
- 不扩大入口诊断白名单。

## 参考对照

本轮补充按三类来源对照，避免继续闭门造轮子：

- Telegram Mini Apps / Web Apps 官方规范：`initData` 解析、`data_check_string`、`hash` HMAC 校验、`auth_date` 时效检查。
- Python 生态成熟实现：aiogram 等库把 WebApp init data 收敛为 parse / validate / safe parse 三层，避免业务模块各自实现验签。
- Rust 参考仓库 `/tmp/xiuxianbot-rs-miniapp-read`：
  - `src/tg/miniapp.rs`：`tgWebAppData` 从 WebView URL fragment 提取、t.me 链接解析。
  - `src/xiuxian/miniapp_http.rs`：API base 规约到 origin、POST envelope、瞬态错误重试、敏感错误脱敏。
  - `src/xiuxian/miniapp_fishing.rs` 与 `src/xiuxian/behaviors/fishing/core.rs`：钓鱼 start / wait / proof / finish 结构与 MiniAppWait / MiniAppReel / MiniAppFlush 状态边界。

## 本轮改动

- `model/webapp_core.py`
  - 增加 `extract_miniapp_init_data_from_url()`，从 WebView URL 的 `tgWebAppData` 提取 raw `initData`。
  - 增加 `parse_miniapp_init_data()`、`build_miniapp_init_data_check_string()`、`sign_miniapp_init_data()`、`validate_miniapp_init_data()`。
  - `validate_miniapp_init_data()` 按 Telegram 官方算法校验 HMAC，检查重复字段、缺 hash、签名不匹配、`auth_date` 过期或未来偏移，并只输出安全摘要。
  - 修复无 scheme 的 `t.me/<bot>/<app>?startapp=...` 链接解析，避免 bot username 误读。
  - `build_miniapp_api_url()` 对齐 Rust 参考实现，将 `api_base_url` 规约到 `scheme://host[:port]` 后再拼 endpoint。
  - 增加通用 MiniApp HTTP envelope 分类：成功、应用层错误、瞬态错误。
  - 增加有限退避执行器，HTTP 执行通过注入 `transport`，默认不主动联网。
  - 增加 flow plan 校验和 runner，可准备请求、记录安全事件、执行 mock transport。
  - 增加 MiniApp flow/event/result 安全摘要，避免 raw token/initData/hash/user 进入审计摘要。
  - 增加 MiniApp 协议抓包结构：`MiniAppCaptureRecord`、`MiniAppCaptureStore`、`summarize_miniapp_json_shape()`、`build_miniapp_capture_record()`，以及 `execute_miniapp_http_request(..., capture_sink=...)`。抓包样本只保存 host/path、payload/response shape、安全体、状态码、耗时和 attempt；默认不启用，不保存 raw token/initData/header。
  - 加强 `sanitize_webapp_secret_text()`：额外脱敏 `token/startapp/start_param` 和常见 `fish_/farm_/boss_/rpt_/stk_/trial_/df_` token。
- `model/features/fishing_miniapp.py`
  - 保持 adapter、request builder、lab-only flow plan。
  - 将 `build_fishing_proof()` 改为公式自洽 proof：按目标评分带生成 stability/danger/slack，并用前端公式回算分数。
  - 增加 duration 上限，避免异常 `minDurationMs` 放大等待时间。
  - 增加 MiniApp 应用错误分类：already settled、expired、unbindable、failed。
  - 增加 `run_fishing_miniapp_lab_flow()` mock/lab flow：`/start -> wait biteAt -> /start -> /finish -> /result`。
  - 增加 `run_fishing_miniapp_loop_lab_flow()`：当前竿完成后调用页面“再来一杆”对应的 `/next` 获取新 token 后继续连钓；`/next` 无 token 或失败时停止连钓，不回退群命令。
  - 生产入口取 initData 改用 `RequestMainWebViewRequest(start_param=token)`；按钮 URL 只做白名单和 token 校验。
  - MiniApp 入口接管后独占分发，不再让同一条回包落入旧 `handle_fishing_reply`；MiniApp 失败后长退避，不回落旧 `.钓鱼状态/.提竿` 链。
  - 每日竿数不写死 5/20，按鱼篓/本地/MiniApp daily used-limit-remaining 动态计算，钓到页面或接口上限为止。
- `model/features/stargazer_miniapp.py`
  - 新增观星台 MiniApp adapter。
  - 对照 wxjerry 线补入 farm 协议声明：`/api/miniapp/xianxia-sect-farm/start|action` 请求构造。
  - 新增 launch/start/decide/action lab flow plan。
  - 新增入口摘要和 WebView launch args 构造。
  - 新增 `parse_stargazer_farm_state()` 与 `choose_stargazer_farm_action()` 纯函数：坏盘先安抚，全盘可收集则收集，空盘则牵引，否则等待。
  - 当前只做入口识别、协议样本、离线决策和请求构造，不接生产 scheduler，不自动执行 action。
- `model/features/trial_miniapp.py`
  - 新增天机试炼 MiniApp adapter。
  - 新增 `/api/miniapp/xianxia-trial/start|finish|next` 请求构造。
  - 新增 `build_trial_proof()`：按 challenge sequence 生成 taps，避开 trapIds，并模拟合理耗时。
  - 新增 `run_trial_miniapp_lab_flow()` mock/lab flow：`/start -> solve -> /finish`，默认不接生产 scheduler。
- `model/features/cave_treasure_miniapp.py`
  - 新增洞府寻宝 MiniApp adapter。
  - 已按真实 `.洞府` 回包识别 `df_*` start 参数，接入统一 URL 摘要、registry 和脱敏。
  - 候选请求构造暂按 `/api/miniapp/xianxia-dongfu/start|action`，必须等真实抓包后校准 endpoint/payload。
  - 新增 `parse_cave_treasure_state()`：`神识 8/8` 解析为单局剩余出手/总出手，`游戏 0/3` 解析为今日已玩局数/总局数。
  - 新增 `choose_cave_treasure_action()`：切换寻宝、入府、优先按答案/提示点小人，无提示才随机；命中后再来一次/结算，耗尽后结算；不写死 7 次或 3 局。
  - 新增 `run_cave_treasure_miniapp_lab_flow()` mock/lab flow，默认不接生产 scheduler，不自动发送 `.洞府`。
- `model/features/miniapp_registry.py`
  - 接入观星台、天机试炼、洞府寻宝 MiniApp adapter 和 flow plan。
  - fishing/stargazer/trial/cave_treasure 均保持 `manual_only=True`、`default_enabled=False`。
- `tests/test_webapp_core.py`
  - 覆盖 `initData` 提取、HMAC 验签、过期/篡改/重复字段拒绝、无 scheme t.me 链接解析、API base origin 规约。
  - 覆盖通用 HTTP retry/app-error no-retry、flow runner、事件脱敏、host/path 白名单。
  - 覆盖钓鱼 proof 自洽、duration cap、完整 mock flow、far bite not_ready、unbindable 分类。
  - 覆盖观星台入口摘要、launch args、lab-only flow plan。
  - 覆盖天机试炼 request、launch args、proof、mock flow、daily limit 分类。
  - 覆盖洞府寻宝入口摘要、`df_*` 脱敏、ratio 语义、动作决策和 mock flow。

## 已验证

2026-07-06 wxjerry 观星台对照与抓包结构补充验证：

```bash
.venv/bin/python -m pytest -q tests/test_webapp_core.py tests/test_stargazer.py tests/test_miniapp_entry_probe.py
# 66 passed

.venv/bin/python -m py_compile model/webapp_core.py model/features/stargazer_miniapp.py tests/test_webapp_core.py tests/test_stargazer.py
# passed

git diff --check -- model/webapp_core.py model/features/stargazer_miniapp.py tests/test_webapp_core.py docs/miniapp_handoff_20260706.md docs/miniapp_lab_delivery_20260706.md
# passed
```

```bash
.venv/bin/python -m pytest -q tests/test_retry_scheduler.py tests/test_webapp_core.py tests/test_miniapp_entry_probe.py tests/test_fishing_runtime.py
# 107 passed

.venv/bin/python -m py_compile model/runtime.py model/app.py model/features/fishing_runtime.py model/features/fishing_miniapp.py model/webapp_core.py
# passed

.venv/bin/python -m compileall -q model tests
# passed

git diff --check
# passed
```

前一版验证记录：

```bash
.venv/bin/python -m pytest -q tests/test_webapp_core.py tests/test_miniapp_entry_probe.py
# 31 passed

.venv/bin/python -m py_compile \
  model/webapp_core.py \
  model/features/fishing_miniapp.py \
  model/features/stargazer_miniapp.py \
  model/features/miniapp_registry.py
# passed

.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_log_entries.py \
  tests/test_tiandao_judgement.py \
  tests/test_ui_dual_track.py
# 78 passed
```

更早验证记录：

```bash
.venv/bin/python -m pytest -q tests/test_webapp_core.py tests/test_miniapp_entry_probe.py
# 26 passed

.venv/bin/python -m py_compile \
  model/webapp_core.py \
  model/features/fishing_miniapp.py \
  model/features/stargazer_miniapp.py \
  model/features/miniapp_registry.py
# passed

.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_log_entries.py \
  tests/test_tiandao_judgement.py \
  tests/test_ui_dual_track.py
# 73 passed

git diff --check
# passed
```

## 安全审计结论

- 没有新增生产 scheduler 入口。
- 没有新增服务重启、上线、live probe 逻辑。
- 新增 initData 验签能力是纯函数 / lab 工具，未接入生产调度或真实 HTTP 自动链。
- MiniApp HTTP 执行器必须传入 `transport`，本轮默认不会自行联网。
- Flow/event/result 的 `safe_summary()` 不包含 raw `initData`、`tgWebAppData`、`hash`、`user`、raw `fish_/farm_/trial_/df_` start token。
- Capture record / JSONL 同样不包含 raw `initData`、`tgWebAppData`、`hash`、`user`、raw `fish_/farm_/trial_/df_` token 或 header 值；响应中的 `nextToken`、错误文本和 source 文本也会脱敏。
- `build_miniapp_http_request()` 仍通过 adapter host/path 白名单构造 URL。
- 钓鱼 MiniApp flow 只作为 lab/mock 函数存在，未接 `fishing_runtime` 或任何生产状态机。
- 观星台 MiniApp 未恢复旧 `.安抚星辰`、`.牵引星辰`、`.收集星辰精华` 文本自动链。
- 洞府寻宝只完成 lab/mock 候选，不接生产 scheduler、UI 发令入口或 `.洞府` 自动发送。

## 主控上线前确认

- Review diff，确认 `manual_only/default_enabled` 未被改成生产启用。
- 如要做 live 读取，只允许主控安排一次低频、单次、无 retry 的入口 probe。
- 观星台真实 HTTP API 尚未确认；当前不能上线观星台动作，只能上线入口识别/诊断层。
- 洞府寻宝真实 HTTP API 尚未确认；当前不能上线洞府动作，只能保留 lab 候选和脱敏抓包结构。
- 如果主控后续把 initData 验签接入生产，bot token 来源、生命周期、错误落日志和失败策略需要单独 review。
- 钓鱼真实 `/finish` 后是否必须 `/result` 仍需 live 证据确认；当前 mock flow 已支持 `/result`，但不应直接作为生产调度链。
- 生产接入前还需要单独设计 UI config、action guard、global lock/watchdog 集成和 24h 观察方案。
