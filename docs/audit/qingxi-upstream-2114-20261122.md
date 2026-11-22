# 青溪上游 2.0.114 接入

2026-11-22，基线生产 52b15bf9，隔离分支 lab/qingxi-upstream2114-20261122。
已发布并推送为 8f1906e5。

刷新 origin/main 到 860d9a1f9d33059bc9ca80251878c5af52fd31e5（PR13）。
与 2.0.110/8a6938d0 相比，只有 dwelling-autobot/trial-bot.user.js 改动。
采用完整原版 2.0.114，SHA256
b4f9f241166e70802e6338cf1c77615e6ceb5e1e6df2ef52ecea93c23d393de8，
vendor 已逐字节核对为该上游 git blob。

新增逻辑由上游实现：月殿持续按原生冷却点杀，允许无持续伤害的安全读条；
普通法宝和术法消耗后灵力必须高于 200，点杀和血契打断按各自实际消耗与
第三阶段预留判断。危险、移动、血契或血池窗口临近时取消/拒绝读条。
计数只用于观察，发出巨剑不等于已确认打断。

本地只更新 provenance、已验证的普通收尾/低血文案版本集合和 README。
冻结帧采集停稳修复继续位于 Linux 接入层，vendor 不含本地改动。
历史单次故障复核分支仍保留原版本、原样本和原条件。
自动 NPC 与整批开关不变；不切换 Electron、应用持久化或主发送架构。

验证：2515 passed、21 subtests passed in 126.78s，涵盖青溪、长风、
钓鱼和活动耦合；原版 Node focus 10/10；四种实际生成脚本语法、Ruff、
Python 编译和 diff 检查通过。新增回放执行实际完整 moonCombatFrame，
覆盖 200 灵力边界、低灵力点杀、第三阶段巨剑预留、血契例外、持续伤害禁用、
安全读条启动和危险取消。上述均为隔离验证，不称为真实月殿验收。

发布前，2.0.110 原局已正常 bounded_stop，返回洞府、满血、正确身份，
新鲜任务回执和飞行关闭均已核对，浏览器内存/磁盘 pending 为空。
随后官方 HTTP 收尾已完成，宿主 waiting_gameplay、无 pending/gather/uncertain。
该局 q23、q24、q25、q26 已交付；q27 保留巨剑门拜访进度，q31 尚未解锁。
q31 的全局选项 `jujian` 由用户指定为巨剑门，
参与名单仍为 WA 和吧唧，没有提前做不可更改的选择。

09:37:15 只重启 xiuxian.service，MainPID 2744878、NRestarts=0。
09:37:32 恢复 24 个身份；09:38 UI HTTP 200、实际发送队列 0，持久 pending 0，
watchdog/health observer active，NPC 和整批自动开关仍 false。
一致性备份 upstream114-pre-release.db 的 quick_check=ok。
实际 vendor 再次核对为 2.0.114/860d9a1f；新版月殿尚无实机通关证据。
