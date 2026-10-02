# Telegram 通知展示收敛

## 范围

- 仅收敛日志群展示，不改变告警分级、汇总间隔、发送模式、失败回存、按钮或游戏调度。
- 不修改任何身份开关、资源策略和数据库业务状态。
- Lab：`/root/xiuxian-notification-compact-20261002`，基线 `2e16f808`。

## 展示规则

- 定时汇总使用 `运行汇总 · 时间`，首屏保留条数、类别数；明细为 Telegram 原生可折叠引用。
- 单次记录省略 `x1`，重复记录仍展示次数、最后时间与身份；类别上限及排序规则不变。
- 实时通知使用 `运行通知 · 时间`。超过 240 个 UTF-16 单元或三行的正文折叠过程明细；标题、失败、安全警告、人工处置要求和收获摘要保持可见。
- 短通知不加折叠，已有引用不重复包裹，管理员提醒位于折叠外。
- HTML 通过 Telethon 解析实体后按 UTF-16 长度裁剪，保留链接和表情。汇总明细上限 3200 单元，实时正文先按调用方预算裁剪，上限 3000 单元。
- 只精简精确匹配的低优先级成功模板：闭关开始、闭关结算、元婴出窍和香火收割。资源数量、预计时长不变，未知文案与失败文案不改写。
- 控制台沿用原文及原长度限制；消息原始证据不变。Telegram 不是无限长度的完整归档，超出原有条数/长度预算的内容仍应查本地日志。
- 稀有奖励日报排除新通知标题，避免把通知里的奖励再次记入成果。

## 验证

- 已安装的 Telethon 1.43.1 支持 `expandable_blockquote`，账户发送回退无需额外 HTML 解析器。
- 日志群已发送一条标注样式预览，消息 ID `52563`。Bot API 返回 HTTP 200，实体包含 `expandable_blockquote`、`code`、`text_link`。没有发送游戏指令或提醒管理员。
- 首轮全量：`15552 passed, 1406 subtests passed`，449.53 秒。
- 补充人工处置、身份转义、按钮透传、管理员提醒位置、重复计数与控制台原文测试后：`34 passed, 20 subtests passed`。
- 最终全量：`15556 passed, 1408 subtests passed`，431.85 秒；编译与 `git diff --check` 通过。
- 18:33 只读回捞样式预览 `52563`，Telegram 实际保存 `MessageEntityBlockquote(collapsed=True)`、代码和链接实体，与 Bot API 返回一致。

## 发布门槛

- 等待 WA 2026-10-02 17:38:17 裂缝链路核销，不在准备/发送窗口内重启。
- 全量回归通过后，只合入此 Lab；保留生产现有备份脚本与备份测试的无关改动。
- 受控停机、SQLite 一致性备份、快进合并、启动；检查新进程、近期 journal、health observer 和 watchdog。
- 推送到 `xiuxian-mian/main`，不是上游 `origin`。
- 上线后检查自然通知与定时汇总，无需重复发送样式测试。

## 当前状态

已上线、推送并完成自然通知验收。运行修正版 `00213f14`；最终全量 `15558 passed, 1408 subtests passed`，436.29 秒。WA 裂缝在发布前已正常核销，发布未修改业务策略和账号开关。

## 上线记录

- 运行版本 `7fc871ec`，已推送 `xiuxian-mian/main`。
- 18:36:35 受控停止主服务，18:36:42 正常退出；SQLite 快照 `/root/xiuxian-before-notification-compact-20261002-1836.db`，权限 0600、`quick_check=ok`。
- 快进合并后 18:39:28 启动，18:39:47 完成初始化；主 PID `325371`，worker `325383`，24 身份，`NRestarts=0`。此次停机期间 observer 的 inactive 告警为主动发布，不是崩溃。
- 18:41 health observer、watchdog 正常，pending 队列为空；MiniApp 配置、身份归属、全局启用配置与备份一致。双群配置仅被动更新 `bot_activity_at_by_group` 活动时间。
- 生产原有 `deploy/xiuxian-r2-backup.sh`、`deploy/backup_engine.py`、`tests/test_snapshot_sqlite_db.py` 的 SHA-256 上线前后相同，未提交这些无关改动。
- 18:49:37 自然汇总 `52570` 已保存可折叠实体，无发送异常；验收发现原 `plain` 字段中的 `<code>` 被原样显示，且从 `pre` 改成普通引用后，用户名重新被 Telegram 自动识别为提及。
- 补充修正：展示从已有 HTML 正确提取文本，折叠明细加 `code` 实体维持原先禁止自动提及的行为；不改聚合键、优先级或发送间隔。36 项针对性测试及 20 项子测试通过。
- 只编辑原样例 `52563` 验证新结构，Bot API HTTP 200，返回相同范围的 `expandable_blockquote` 与 `code`；未追加测试推送。
- 修正版 `00213f14` 日志相关回归 `83 passed, 61 subtests passed`，已推送 `xiuxian-mian/main`。18:57:40 开始受控停止，18:57:48 正常退出，18:57:49 启动，18:58:09 初始化完成；主 PID `332358`、worker `332359`，`NRestarts=0`。
- 修正前一致性快照 `/root/xiuxian-before-notification-no-mention-20261002-1857.db`，0600、`quick_check=ok`。18:58 health/watchdog 正常；24 身份、每身份 52 项模块开关，以及 MiniApp/身份归属/全局配置与首次上线前一致，两号香火转神识仍关闭。
- 19:07:58 自然汇总 `52571`（Telegram 时间 19:07:59）验收通过：2 条记录、2 类；`MessageEntityBlockquote(collapsed=True)` 与 `MessageEntityCode` 范围相同；无 `MessageEntityMention`、无裸 HTML 标签。没有额外测试推送或调整汇总周期。
- 19:06 health observer/watchdog 均正常，主 PID `332358`、`NRestarts=0`。本项已销号，常驻健康监测服务保持运行。
