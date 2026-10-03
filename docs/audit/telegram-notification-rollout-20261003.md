# Telegram 通知第一阶段上线

后续更新：13:38:07 已上线回执分类修正 `f149bbe4`，见 [分类修正与验证](telegram-notification-outcomes-20261003.md)。仍是仅采样阶段，以下 PID 和回执为首轮上线历史。

## 生效范围

用户在验收后要求上线。本次将验收代码 `814cc620` 从 Lab fast-forward 合入生产 `main`，已推送 `xiuxian-mian/main`。

只开启投递回执采样，保留通知路由和现有发送策略。结构化摘要、持久化桶均未开启；这次上线不宣称通知数量已经下降，也不代表 TG-01 至 TG-06 全部销号。

生产 systemd drop-in：`/etc/systemd/system/xiuxian.service.d/notification-metrics.conf`。

```ini
[Service]
# Stage 1: collect transport receipts; keep notification routing unchanged.
Environment=LOG_GROUP_DELIVERY_METRICS=1
Environment=LOG_GROUP_STRUCTURED_SUMMARY=0
```

生产 `.env` 未配置这两个键。该项目的 dotenv 会覆盖同名进程环境，后续修改 `.env` 时需保持一致，不能只看 systemd 文件推断最终值。

## 验证

- 最终 Lab 全量：15727 passed / 1408 subtests；证据见 [候选验收](telegram-notification-acceptance-20261003.md)。
- 合入生产后的隔离定向复验：198 passed / 47 subtests；使用 `XIUXIAN_ALLOW_LIVE_TEST_DB=0`。
- 上线前 health observer / watchdog 正常；待处理回包为空，天星下一次裂缝尚未进入准备窗口。
- 2026-10-03 12:44:51 UTC+8 重启主服务，PID 从 553961 变为 742344，`active/running`、`NRestarts=0`。未开启既有 inactive listener，主监听照常工作。
- 新进程 `/proc/<pid>/environ` 确认采样 1、结构化摘要 0；12:45:00 UI 启动，12:45:09 自动化系统启动成功。
- 重启后 health observer / watchdog 正常，defensive preflight 无新增危险项。持续核对至 13:02 后，主服务仍为 PID 742344、NRestarts=0。
- 已收到第一条自然通知回执：`37477bbc73d9404e8888e8045d8b3166`，时间戳 `1791003301.1347108`，Bot confirmed，655ms，215 UTF-16 单元、4 行、显式管理员提及 0。采样实际生效；一条样本不代表已取得完整全天基线或降噪验收通过。
- 未发样式测试消息或额外游戏命令，不回滚业务数据、不修改账号开关。现有 R2 备份相关改动保留且未提交。

## 采样与后续

### 2026-10-03 下午复核

- 当前累计采到 7 次传输尝试，均 confirmed；相同正文重复 0，显式
  mention 链接 2，送达耗时 P50 667ms / P95 1048ms。样本不足以评估全天
  降噪比例，且不覆盖独立 watchdog/日报。
- Boss 事件 `2026-10-03:12599460` 实际发了三条：13:40:15 入场、
  13:42:51 个人结算、13:44:21 全服结论附个人汇总。后两条不是相同正文，
  但重复展示同场个人成果，属于 TG-03 待收口项，不能以正文重复为零销号。
- 收口约束：保留有时效的入场提醒；同场个人结果只汇报一次。全服结束
  如果带来此前未知的个人结算或奖励，仍需保留新增事实；不得仅凭全服
  胜利生成个人成功。需覆盖结论先到、个人结算先到和失败后回捞的测试，
  不直接把所有全服结论静音。
- 结构化摘要继续关闭；独立存储故障/held 状态监测及结果未知回退策略
  仍未完整收尾，不因为这次采样全成功就跳过。

```bash
journalctl -u xiuxian.service --since '2026-10-03 12:44:51' -o json --no-pager | .venv/bin/python tools/notification_report.py --project-root /opt/xiuxian-main --journal - --output data/analysis/notification-rollout-20261003.json
```

报表仅统计 runtime 日志群和第二通知渠道的传输尝试，不覆盖独立 watchdog/日报，不把 unknown 算作确定失败。先积累真实基线，再按验收条件处理 held 策略、存储告警和 Bot 超时回退风险，最后评审结构化摘要灰度。

回退：只将 `LOG_GROUP_DELIVERY_METRICS` 改为 0，维持 `LOG_GROUP_STRUCTURED_SUMMARY=0`，daemon-reload 后重启主服务；不要清理或回滚游戏数据库。源码回退不是本阶段关闭采样的必要步骤。
