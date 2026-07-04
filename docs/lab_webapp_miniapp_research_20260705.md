# Telegram WebApp / MiniApp 调研记录（2026-07-05）

## 背景

灵溪垂钓、世界 boss 已开始迁移到 Telegram Mini App / 小程序形态。旧命令链（`.钓鱼`、`.提竿`、`.世界boss` 等）不能再按普通文本回复自动化处理。后续还可能有更多玩法走 WebApp，因此不能为钓鱼单独写一次性逻辑，应先抽通用 WebApp 诊断与协议层。

本记录仅作为 lab 研究结论，不代表已上线自动化。

## 开源样本

已只读浅克隆并核对以下公开仓库：

- `HiddenCodeDevs/BlumTelegramBot`
- `vanhbakaa/Hamster-kombat`
- `SudoLite/TimeFarmBot`
- `sirbiprod/MemeFiBot`
- `vanhbakaa/Notpixel-bot`
- `dzuhri-auto/depin-alliance`

这些仓库多为 Telegram Mini App 自动化项目，质量参差不齐，其中部分存在长期保存 query、直接用 query 文件跑、混淆代码等高风险做法，只可借鉴模式，不能引入代码或依赖。

## 成熟共性

可吸收的通用模式：

1. 使用 Telegram 客户端 API 打开 WebView，获取带 `tgWebAppData` 的 URL。
   - Pyrogram 常见：`RequestWebView` / `RequestAppWebView`
   - Telethon 对应：`functions.messages.RequestWebViewRequest`
2. 从 WebView URL fragment/query 中解析 `tgWebAppData` / `initData`，再调用玩法自身 HTTP API。
3. WebApp HTTP 层通常需要独立的 headers、User-Agent、platform、token 刷新和限流。
4. 对 `FloodWait`、无效 session、代理失败、HTTP 超时都要显式分类，不能把失败当成游戏失败。
5. 多账号执行通常有启动错峰、账号间随机延迟、token 生命周期刷新，避免并发风暴。

不可直接吸收的做法：

1. 长期明文保存 `query_id` / `tgWebAppData` / `initData`。
2. 把用户手动导出的 query 文件当作常驻凭证反复跑。
3. 未经域名/机器人白名单校验就打开任意 `t.me/...startapp`。
4. 大并发 HTTP 刷接口、绕过现有安全锁或不走本仓库审计链。
5. 引入混淆代码或不可审计依赖。

## 本仓库现状

已有雏形：

- `model/features/tiandao_miniapp.py`
  - 已有 `RequestWebViewRequest`
  - 已有 `tgWebAppData` 提取
  - 已有 token/敏感字段脱敏
  - 已有受控 bot / URL 白名单
- `model/features/tiandao_judgement.py`
  - 已有 miniapp pending item、终态去重、最多一次 retry
- `model/module_manifest.py`
  - 已登记 `miniapp_init_data`
  - 当前定位是 manual-only diagnostics，不应直接变成 scheduler 输入
- `model/app_message_log.py`
  - 当前按钮日志只保存 `url_host`，不保存完整 URL；这对安全有利，但不足以做 WebApp 诊断。

## 建议架构

第一阶段只做通用 WebApp 诊断层，不直接自动钓鱼：

1. `webapp_core`
   - 解析 Telegram 消息按钮：`url` / `web_view`
   - 域名、bot username、startapp 参数白名单
   - 敏感 URL 脱敏：禁止落盘完整 `tgWebAppData`、`initData`、`hash`、`user`
   - 手动触发获取 initData，用短 TTL 内存态保存，只输出脱敏摘要
2. `webapp_capture`
   - 仅记录按钮文本、类型、host、受控 startapp token 摘要、玩法候选名
   - 不把完整 URL 写入常规消息日志
3. `webapp_adapter`
   - 每个玩法单独适配 HTTP API、状态机和 UI
   - 适配器必须有独立开关、dry-run、限流、失败退避、审计日志
4. `webapp_browser`
   - 若玩法需要 canvas/真实交互，再接 Playwright/浏览器自动化
   - 不能直接从命令调度里启动无界浏览器任务

## 钓鱼小程序边界

当前不应恢复旧钓鱼命令硬刷：

- 真实文案已提示“此竿已由灵溪水面接管”“提竿已迁入灵溪水面”
- 旧 `.提竿` / `.钓鱼状态` 链路无法完成小程序内操作
- 旧钓鱼模块应保持关闭或仅做提醒/日志，不主动发送钓鱼命令

后续钓鱼 miniapp 开发顺序：

1. 捕获并脱敏记录“进入灵溪垂钓”按钮信息。
2. 手动诊断获取 WebView initData，并确认 API 域名与请求形态。
3. 在 lab 中实现只读状态探测。
4. 明确一次竿/提竿/结算的状态机、限流、失败退避。
5. 审计通过后再考虑生产开关，默认关闭。

## 安全验收

上线前必须满足：

- 不落盘完整 `tgWebAppData` / `initData` / `hash` / `user`。
- 不从普通消息日志重放敏感 initData。
- 所有 WebApp 行为有玩法开关、全局安全锁、action guard、审计日志。
- HTTP/API 层有超时、重试上限、退避、域名白名单。
- 测试覆盖脱敏、白名单拒绝、session 不可用、FloodWait、HTTP 超时、终态去重。

