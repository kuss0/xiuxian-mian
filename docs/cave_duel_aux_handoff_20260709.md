# 洞府 MiniApp / 斗法 UI 辅助交接 2026-07-09

本次为辅助/lab 候选工作，只负责代码、离线测试、mock 测试、外部仓库对照和交接说明；不代表上线授权。未重启服务，未发送 `.洞府` / `.斗法`，未修改生产 DB，未打开生产调度。

## 工作边界

- 当前工作目录：`/opt/xiuxian-main`。
- 外部参考仓库已按 SSH 拉取到 `/tmp/xianxia-companion`。
- 参考仓库远端：`git@github.com:jiven303toto/xianxia-companion.git`。
- 参考仓库 HEAD：`a675e9e bug fixed`。
- 本次验证只跑离线/静态测试，不做 live WebView、HTTP 探测或线上灰度。

## 外部仓库可吸收点

本轮已把 `xianxia-companion` 通过 SSH 更新到 `a675e9e`，并做了全仓只读对照；下面分为已经局部吸收的洞府点，以及后续可拆任务吸收的通用架构点。

### 洞府 MiniApp

- `app/src/tg_game/features/estate/biz_estate_miniapp.py`
- `app/src/tg_game/features/estate/biz_estate_hunt_queue.py`
- `docs/estate-module.md`

对本地有价值的点：

- 洞府 MiniApp 接口与本地候选一致：`/api/miniapp/xianxia-dwelling/hunt`、`hunt/reveal`、`hunt/settle`。
- `hunt/reveal` 使用 0-based `index`，UI/状态展示可用 1-based 文案。
- 自动寻宝应以 MiniApp 返回的 `dwelling.hunt.used/limit/remaining` 为准，不能写死每日次数。
- 一次可信 `.洞府` 回包获取 WebView 后，可在同一 MiniApp HTTP 会话内连续完成当日剩余轮次，避免每轮重复发送 `.洞府`。
- `latestHint.markers` 应优先使用 treasure，再考虑 resource；但必须跳过已 revealed 或本轮已尝试的格子。
- 寻宝策略默认耗尽神识后 settle，不自动触发更激进的“继续深入”。
- 记录轮次摘要时只展示可确认字段，不从旧记录反推伪造每轮数据。

本地本轮已吸收其中两项：天尊 shard bot 白名单、`latestHint.markers` 跳过已 revealed 并优先 treasure/resource。

### MiniApp 通用框架

可参考的 MiniApp 共性实现分布在：

- `app/src/tg_game/features/fishing/biz_fishing_miniapp.py`
- `app/src/tg_game/features/tianji_trial/biz_tianji_trial_miniapp.py`
- `app/src/tg_game/features/xinggong/biz_xinggong_miniapp.py`
- `docs/fishing-overview.md`
- `docs/tianji-trial-miniapp.md`
- `docs/xinggong-starboard-auto.md`

后续本地 MiniApp 框架可以吸收这些共性，但建议抽成一层本地 `MiniAppAdapter` 能力，而不是逐模块复制：

- 入口解析统一兼容 `message.buttons`、`event.buttons`、`reply_markup.rows[].buttons[]`，按钮缺失时写明确诊断并停止 HTTP。
- 当 Telegram runtime 没拿到按钮对象但文案里带可信 MiniApp URL 时，可作为低优先级兜底入口；仍必须经过 host、bot username、token pattern 校验。
- token、start 参数、`initData/tgWebAppData/hash/user/raw URL` 只保留短摘要和字段名，不写原文。
- HTTP host 与 path 必须双白名单，且每个 endpoint 都在 adapter 注册表中显式声明。
- capture 只记录请求/响应 shape、payload keys、耗时、脱敏错误和业务摘要。
- MiniApp 流程由可信 bot 回包触发，页面按钮只登记 pending request，不直接调用 MiniApp HTTP。
- 同一个 MiniApp token/session 的行为边界要写在 adapter 上，例如钓鱼的一竿/多竿、天机试炼一 token 一关、洞府同 session 连续寻宝到当日上限。

本地本轮已吸收：通用入口迭代器改为按钮优先、文本 URL 兜底，并保留既有 `reply_markup` 兼容；五个已接入 MiniApp extractor 共用这层能力。

### 外发命令确认与可信回复

可参考文件：

- `docs/outgoing-command-confirmation.md`
- `app/src/tg_game/storage.py`
- `app/src/tg_game/telegram/runtime.py`
- `app/src/tg_game/runtime/queue_service.py`
- `app/src/tg_game/runtime/executors.py`

可吸收方向：

- `outgoing_commands` 建立 `pending -> sending -> awaiting_confirm -> confirmed / needs_manual_confirm / failed` 状态机。
- Telegram API 发送成功后立即把本账号刚发出的消息写入 `bound_messages direction=outgoing`，即使 outgoing event 漏掉，也能用 bot reply 的父消息确认。
- 自动任务把 `pending/sending/awaiting_confirm` 当作阻塞状态，`needs_manual_confirm` 只在人工确认窗口内阻塞，避免无限卡住。
- 可信回包必须同时校验：绑定 bot id、当前 profile、同 chat/thread、`reply_to_msg_id` 指向本账号 outgoing 父消息、父命令文本匹配。
- 对星宫/天机试炼这类 MiniApp pending 场景，除严格父消息外，还可加入“同线程、同 profile、pending request 时间不早于最近命令、文本指向当前 @用户名”的保守兜底。

这对本地风控价值高：减少重复补发、错收别人回包、服务重启后补发旧命令三类风险。

### 恢复保护与网络熔断

可参考文件：

- `docs/local-runtime-resume.md`
- `app/src/tg_game/telegram/resume_guard.py`
- `app/src/tg_game/telegram/network_guard.py`

可吸收方向：

- worker 心跳间隔超过 15 分钟后进入恢复保护，取消旧 `pending/sending` 命令，进入 30 分钟恢复模式。
- 长离线后把倒计时任务错峰延后，不追补离线期间错过的多轮。
- 网络类发送异常按 profile 熔断 15 分钟；熔断期间不 claim 新命令，不把网络失败当业务失败。
- 熔断结束后先跑同一套恢复保护，再让模块按当前状态重新判断。

本地如果后续接更多 MiniApp 和高频调度，这层比单模块 cooldown 更能降低群信息流。

### 低信息流 UI / 只读聚合

可参考文件：

- `docs/countdown-overview.md`
- `docs/inventory-overview.md`
- `docs/profile-switching.md`

可吸收方向：

- 做一个只读“机缘时辰/模块状态聚合”页面，集中展示各 profile 的倒计时、等待确认、最近业务状态，降低玩家反复发 `.状态` 类命令。
- 倒计时只展示 bot/API 已确认来源，不展示本地猜测长倒计时；例如法宝抚摸只展示 bot 冷却，布道只展示 `.神迹 布道` 冷却。
- 背包/市场类 UI 默认只读，危险操作先生成/复制命令，不直接发送。
- profile 卡片切换时直接跳对应模块，避免为了看不同身份状态反复刷群命令。

### 宗门/角色范围保护

可参考文件：

- `docs/sect-command-scope.md`
- `docs/tianxing-auto.md`
- `docs/wanling-roam-auto.md`

可吸收方向：

- 在统一入队层做宗门命令归属校验，而不是只在页面按钮上校验。
- 天星宗这类高风险路线应默认 dry-run/关闭，低风险查盘/观命/消劫和高风险定命/推命/改命/炼制分层。
- 名单型自动化不要写死名字；万灵放养用当前 payload 里的灵兽名过滤，保存和运行时都二次过滤。
- 当前 profile 离开对应宗门时，自动关闭相关任务并取消尚未发送的同宗门队列。

### 侍妾、法宝、小世界等状态来源

可参考文件：

- `docs/companion-voyage.md`
- `docs/artifact-module.md`
- `docs/small-world.md`

可吸收方向：

- 侍妾远航先刷新 `.我的侍妾`，没有可信面板就不发远航；`.侍妾远航` 发出后也不按固定航线时长写长倒计时，必须等 `.远航状态` 或面板确认。
- 法宝自动任务只在 payload 有器灵时展示；试炼先做灵石/养魂木资源预检。
- 法宝抚摸成功但无冷却时只允许一次短延迟复查；复查仍无冷却才用内部间隔，不把内部间隔展示成业务倒计时。
- 小世界每轮最多一条操作命令，操作后必须等回包，下一轮重新 `.小世界` 再判断；不自动化高消耗/高风险命令。

这些适合本地后续降低无效命令和误倒计时。

### Web 层拆分

外部仓库 `a675e9e` 新增拆分：

- `app/src/tg_game/web/shared_context.py`
- `app/src/tg_game/web/session_helpers.py`
- `app/src/tg_game/web/request_results.py`
- `app/src/tg_game/web/module_detail_state.py`
- `app/src/tg_game/web/profile_card_state.py`
- `app/src/tg_game/web/app_helpers.py`

可吸收方向是“路由瘦身”：把共享模板上下文、session/profile 切换、query result notice、模块页面状态、profile 卡片状态从大 `app.py` 里拆出来。本地 Web UI 后续如果继续扩 MiniApp/斗法/洞府，建议先拆状态构建函数，避免 `model/ui.py` 和前端 JS 越来越难审计。

不过这次外部仓库新拆分不能盲搬：`profile_card_state.py` 里看到疑似拼写回归 `shouldrefresh_cultivator_payload(...)`，应只吸收分层思路，具体代码必须本地重写并补测试。

## 本地候选改动

### MiniApp 通用入口捕获

- `model/webapp_core.py`
  - 新增 `RE_WEBAPP_URL` 与 `iter_webapp_entry_links()`。
  - 入口提取顺序为 Telegram 按钮对象优先，随后扫描 `message_text`、`event.raw_text/text`、`message.message/text/raw_text` 中的 WebApp URL。
  - 文本 URL 只作为入口候选；后续仍走 adapter 的 host、bot username、start_param pattern 校验。
  - 对同一 URL 去重，避免按钮和正文同时存在时重复处理。

- `model/features/fishing_miniapp.py`
- `model/features/trial_miniapp.py`
- `model/features/cave_treasure_miniapp.py`
- `model/features/stargazer_miniapp.py`
- `model/features/tree_miniapp.py`
  - 五个 MiniApp extractor 改为共用 `iter_webapp_entry_links()`。
  - 保持现有手动/lab 边界和生产开关不变；该改动不发命令、不请求 WebView、不调用 HTTP。

- `tests/test_webapp_core.py`
  - 覆盖钓鱼、天机试炼、洞府、观星台、灵树五类 MiniApp 的文本 URL 兜底提取。
  - 继续确认 safe summary 不泄漏 token。

### 洞府 MiniApp

- `model/webapp_core.py`
  - `MiniAppAdapter` 增加 `allowed_bot_username_patterns`。
  - `build_miniapp_launch_request()` 支持 exact bot username 与正则 shard 白名单并存。
  - 仍拒绝未知 bot；无白名单且无固定 bot 的 adapter 才保持不限制。

- `model/features/cave_treasure_miniapp.py`
  - 洞府 adapter 增加允许 bot：
    - `hantianzun\d{2}_bot`
  - `parse_cave_treasure_state()` 读取 `huntRun.latestHint`。
  - `latestHint.markers`、`hint.markers`、顶层 `markers` 都可作为提示来源。
  - marker 目标统一转为对外 1-based target；实际 reveal 请求仍转回 0-based `index`。
  - 已 revealed 的 marker target 会跳过，避免重复点已经翻开的石室。
  - treasure/main/chest 优先级高于 resource/reward/material，再高于 safe/hint/candidate。

- `tests/test_webapp_core.py`
  - 覆盖通用 MiniApp bot pattern 白名单。
  - 覆盖洞府 `hantianzun08_bot` 入口可用、`evil_bot` 被拒。

- `tests/test_miniapp_protocol_flows.py`
  - 覆盖 `latestHint.markers` 跳过已 revealed treasure 后选择 resource。
  - 覆盖 treasure marker 优先于 resource marker。
  - 覆盖决策 `targetIndex` 为 1-based，构造请求 payload `index` 为 0-based。

### 斗法 UI

- `model/web/static/js/module_cards_ui.js`
  - 日常设置弹窗里的“斗法”区域补齐：
    - 目标池输入。
    - 总次数输入。
    - 重置进度 checkbox。
    - 当前进度、下次执行、最近结果、最近异常展示。
    - 保存按钮。
  - 保存复用已有 `/api/duel-config`，不新增后端接口。

- `tests/test_ui_dual_track.py`
  - 覆盖斗法 UI 的配置字段、保存按钮、`/api/duel-config`、`submitDuelConfig`。

## 验证结果

已通过：

```bash
/opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_fishing_runtime.py \
  tests/test_cave_treasure_runtime.py \
  tests/test_trial_runtime.py \
  tests/test_stargazer.py \
  tests/test_miniapp_entry_probe.py
# 239 passed, 10 subtests passed

/opt/xiuxian-main/.venv/bin/python -m py_compile \
  model/webapp_core.py \
  model/features/fishing_miniapp.py \
  model/features/trial_miniapp.py \
  model/features/cave_treasure_miniapp.py \
  model/features/stargazer_miniapp.py \
  model/features/tree_miniapp.py \
  tests/test_webapp_core.py

/opt/xiuxian-main/.venv/bin/python -m pytest -q tests/test_webapp_core.py tests/test_miniapp_protocol_flows.py tests/test_ui_dual_track.py tests/test_duel.py
# 132 passed, 9 subtests passed

/opt/xiuxian-main/.venv/bin/python -m py_compile \
  model/webapp_core.py \
  model/features/cave_treasure_miniapp.py \
  model/features/cave_treasure_runtime.py \
  model/features/duel.py \
  model/ui.py \
  tests/test_webapp_core.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_ui_dual_track.py \
  tests/test_duel.py

node --check model/web/static/js/module_cards_ui.js

git -C /opt/xiuxian-main diff --check
# passed
```

## 2026-07-09 MiniApp 替代旧命令读取：干完了

目标：按“来源新鲜度”补后台字段，让 MiniApp 作为原群命令读取/页面读取的替代来源。储物袋 API 读取链路不变；它仍是独立的库存快照来源，不被本轮 MiniApp 改动收紧或改写。

新增/修改：

- `model/webapp_core.py`
  - `MiniAppFlowPlan` 新增 `replaces_commands`、`read_scope` 与 `state_outputs`。
  - `safe_summary()` 会暴露这些后台字段，供主控/UI 明确区分“MiniApp 替代哪个旧命令、读取范围是什么、会产出哪些后台状态”。
  - 默认 `read_scope` 为 `single_identity_command_replacement`，表示这是单角色命令读法替代，不是 API 全局读取替代。

- `model/features/*_miniapp.py`
  - 已标注替代命令：
    - 洞府：`.洞府`
    - 钓鱼：`.钓鱼`
    - 观星台：`.观星台`
    - 灵树：`.灵树`
    - 天机试炼：`.天机试炼`
  - 已标注 `state_outputs`：
    - 洞府/钓鱼：`module_snapshot`、`daily_counter`、`inventory_delta`
    - 观星台：`module_snapshot`、`inventory_delta`
    - 灵树：`module_snapshot`、`daily_counter`、`score_policy`
    - 天机试炼：`module_snapshot`、`daily_counter`、`reward_delta`

- `model/miniapp_state.py`
  - 新增通用 MiniApp 安全状态快照层。
  - `record_miniapp_state()` 以 `identity_id + game_key` 保存最新状态，不保存 token/initData/WebView URL/raw session。
  - `sanitize_miniapp_state()` 会丢弃敏感键；洞府 `session_id` 只保留 `has_session_id` 与 digest。
  - `get_miniapp_state_snapshot()` 给 UI/主控读取安全 rows/by_identity。
  - `replay_miniapp_capture_records()` 支持用脱敏 capture 回放 parser，便于后续 MiniApp 协议变更离线复核。

- `model/inventory_delta.py`
  - 新增通用库存增量 ledger。
  - 记录字段包含 `identity_id`、`source`、哈希/安全化 `source_id`、`status`、`items`、`updated_at`、`source_summary`。
  - 默认状态为 `pending_inventory_confirm`。
  - `record_inventory_delta()` 对同一 `identity + source + source_id` 幂等；重复洞府结算不会重复叠加。
  - `build_inventory_freshness_snapshot()` 按储物袋快照时间与增量时间合并：
    - `rows[].items` 仍是储物袋/API 基准快照，不变。
    - `rows[].pending_deltas` 表示比储物袋快照更新的候选增量。
    - `rows[].merged_items` 表示 `base_quantity + pending_delta` 的候选合并结果。
    - `rows[].inventory_freshness.stale_deltas` 表示已被更新储物袋快照覆盖的旧增量。
    - 顶层 `storage_bag.inventory_freshness` 给出 pending/stale/total 计数。

- `model/state.py`
  - 新增 `inventory_delta_records` 元状态。
  - 新增 `miniapp_state_records` 元状态。
  - 新增 `get_inventory_delta_records()` / `set_inventory_delta_records()`。
  - 新增 `get_miniapp_state_records()` / `set_miniapp_state_records()`。

- `model/persistence.py`
  - 补上 `inventory_delta_records` 的 meta codec/default，避免库存差量只存在内存。
  - 新增 `miniapp_state_records` 的 meta codec/default，MiniApp 状态快照可随 DB 保存/加载。

- `model/features/cave_treasure_runtime.py`
  - 洞府 MiniApp 仅在 `ok` 且存在 `settled/daily_limit/settled_count/results` 时记录库存增量。
  - 只把游戏物资写入 ledger：奖励物品与 `灵石` 收益；跳过 `修为/经验/贡献` 等非库存字段。
  - `source_id` 由 session/result 摘要生成，不落原始 session，不落 token/initData/raw payload。
  - 不调用 `apply_storage_bag_item_deltas()`，不直接改储物袋库存。
  - 流程返回后额外写入 `cave_treasure` 的 MiniApp 状态快照，输出契约为 `module_snapshot/daily_counter/inventory_delta`。

- `model/ui.py`
  - `get_storage_bag_snapshot()` 保持旧 `items/totals/transfer_identities` 语义。
  - 新增后台字段 `pending_deltas`、`merged_items`、`inventory_freshness`，供主控/UI 后续选择显示。
  - `get_miniapp_status_snapshot()` 通过 flow plan 暴露 `replaces_commands/read_scope/state_outputs`。
  - `get_miniapp_status_snapshot()` 新增 `state_records`，供 UI 查看已记录的安全 MiniApp 状态。

- `tests/test_cave_treasure_runtime.py`
  - 覆盖洞府结算写入 pending delta。
  - 覆盖洞府入口写入 MiniApp 安全状态快照。
  - 覆盖储物袋基准快照不被洞府增量覆盖。
  - 覆盖 `source_id` 不泄漏原始 session。

- `tests/test_storage_bag_api.py`
  - 覆盖新鲜洞府增量进入 `pending_deltas/merged_items`。
  - 覆盖旧增量在储物袋快照更新后变成 stale，不再参与候选库存。
  - 覆盖同 source 幂等去重。

- `tests/test_webapp_core.py` / `tests/test_miniapp_entry_probe.py`
  - 覆盖 MiniApp flow plan 的旧命令替代元数据与输出契约。

- `tests/test_miniapp_state.py`
  - 覆盖 MiniApp 状态敏感字段清洗、状态记录去重、洞府 capture 回放、meta codec 持久化。

最新验证：

```bash
/opt/xiuxian-main/.venv/bin/python -m py_compile \
  model/miniapp_state.py \
  model/inventory_delta.py \
  model/persistence.py \
  model/webapp_core.py \
  model/features/cave_treasure_runtime.py \
  model/features/cave_treasure_miniapp.py \
  model/features/fishing_miniapp.py \
  model/features/stargazer_miniapp.py \
  model/features/tree_miniapp.py \
  model/features/trial_miniapp.py \
  model/ui.py \
  model/state.py

PYTHONPATH=. /opt/xiuxian-main/.venv/bin/pytest -q \
  tests/test_miniapp_state.py \
  tests/test_webapp_core.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_storage_bag_api.py \
  tests/test_cave_treasure_runtime.py
# 163 passed, 10 subtests passed

PYTHONPATH=. /opt/xiuxian-main/.venv/bin/pytest -q \
  tests/test_webapp_core.py \
  tests/test_miniapp_protocol_flows.py \
  tests/test_storage_bag_transfer.py \
  tests/test_storage_bag_api.py \
  tests/test_cave_treasure_runtime.py \
  tests/test_fishing_runtime.py \
  tests/test_stargazer.py \
  tests/test_miniapp_entry_probe.py \
  tests/test_miniapp_state.py
# 331 passed, 10 subtests passed

git -C /opt/xiuxian-main diff --check
```

注意：

- 这只是候选后台字段和离线测试交付，不等于上线授权。
- 储物袋 API 读取不变；本轮没有把 API 改成单角色，也没有改变 `/api/me` / `/api/cultivator/{username}` 的现有刷新策略。
- MiniApp 是替代旧群命令/页面读取，不替代 API 库存刷新链路；API 仍按原手动 UI 触发/低频策略。
- 主控如果要让转移预览/策略真正采用 `merged_items`，需要另起 review；当前仍默认按储物袋 `items` 执行，避免把未确认洞府增量用于自动转移。
- 后续钓鱼、观星台、试炼、灵树也可以逐步接入 `record_inventory_delta()`，但建议先等洞府灰度稳定。

## 当前未处理/需主控复核

- 本轮新增的 MiniApp 文本 URL 兜底只做入口捕获，不代表生产自动化授权；仍需主控 review 后决定是否合入。
- 本次没有执行 live `.洞府`，没有真实 `RequestMainWebView`，没有写入新的生产 capture。
- 洞府真实灰度仍需主控确认：
  - 新天尊 shard 是否都在允许范围内。
  - `latestHint.markers` 在真实回包里是否稳定使用 0-based `index`。
  - 若真实 marker 使用其他字段名，需要按 capture 再补 parser。
- 斗法 UI 只补了配置入口；斗法候选调度逻辑仍需主控按 `docs/duel_aux_handoff_20260708.md` 复核后再决定是否合入/上线。
- 建议主控把“外发命令确认层/可信回复父消息/恢复保护/只读倒计时聚合”作为后续独立基础设施任务，不要混在洞府或斗法单模块上线里一次性放出。
- 当前 worktree 仍有多处非本次范围脏改，例如钓鱼、斗法候选、UI、backlog 等；交付时请主控按文件逐项 review，不要整包盲上。

## 上线建议

主控如准备合入：

1. 先复核 `model/webapp_core.py` 的 bot pattern 白名单是否只影响 MiniApp adapter。
2. 再复核洞府真实 capture，确认 `latestHint.markers[*].index` 语义。
3. 斗法 UI 与 `/api/duel-config` 做一次本地页面点击验证。
4. 保持洞府和斗法生产开关默认关闭，由主控安排灰度窗口。
