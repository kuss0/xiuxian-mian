# Attempt 检查点的群范围边界

## 发现

`attempt_shadow_checkpoint.py` 的发送 parity 只比较消息 ID 集合。Telegram 消息 ID 在群内唯一，不是双群全局唯一；另一群恰好相同的 ID 可能掩盖目标群缺失。当前 `command_attempts` 没有 chat_id，`runtime_shadow.note_sent()` 也未保存群范围，已有绑定 evidence 只保存 bind_reason/anchor 等标签。

因此，`missing_root_count=0` 只能说明保留日志中存在同 ID；`non_strong_written_bindings=0` 只能说明标签均属于既定强锚类别。两者均不是独立同群绑定精度验收，不能据此宣称零误绑或批准 Gate 4。此处是审计能力缺口，尚无证据证明本轮线上真实误绑或双群多发。

## 本批收敛

- 保留原 ID-only 计数，添加 `verification=partial_id_only`、`attempts_without_chat_scope` 和明确 policy；有未验证范围就输出报告 warn，不再输出误导性的全绿。
- 检测保留的真实 sent 日志中，涉及本批 Attempt 根 ID 的跨群重号。相同群重复日志不算跨群；没有群、零/布尔/字符串群 ID 不作为群证据；无关 ID 和普通入站消息不计入。
- 绑定分布保留，但 `precision_verified=false`，明确未独立审计精度。没有保留根样本时使用 `no_retained_root_sample`，不把无样本当作验证成功。
- 不从默认群、当前群、唯一日志候选或旧 evidence 推断并回填账本；不改 runtime shadow write/bind、数据库表、游戏状态、任何发送/恢复/重试或归档策略。
- 检查点服务只执行只读 CLI、写报告文件，没有自动修复或 TG 告警发送器。本批不接入健康暂停或发送熔断，主 worker 不需重启。

## 自然证据

2026-10-06 12:08:39 Lab 以只读 SQLite 和现有日志回放（使用无效测试配置占位，仅避免加载 Lab 缺失的 `.env`；未复制凭据，未联网）：

- 发送 Attempt 23798 条，其中 19827 条早于保留消息日志窗口；窗口从 2026-09-13 00:00:01 开始。
- 保留根 ID 3971/3971 均存在，跨群重号 0；3971 条缺少账本群范围，故为 partial，不是完整绑定验收。
- 27445 份 evidence 的标签都是 `exact_reply_to_root`，仍不能证明同群精度。
- 全库文件 67776512 字节；Attempt/evidence payload 原有近似统计 6028782，不是准确 UTF-8 字节统计，也不是独立 Attempt 表占用。继续不归档 open 账本。
- 原有 `recent attempt errors 1` 来自 `concubine_tianji:a947c44d-eea0-418a-9b08-beb9d3cbd5d5`：04:27:13 备份停止时 caller cancelled after RPC started。业务回包 `1283333` 已在 04:41:43 经原 reducer 核销；影子 send_unknown 记录保留，既不清除也不据此补发。

## 验收

- 先增加回归，旧实现三项失败：单群候选也误报已验证、双群同 ID 误报 ok、重复日志缺少范围报告。
- 修复后范围测试 12 passed；扩大到 store/runtime_shadow/bind/archive 的隔离回归 46 passed。
- 覆盖数据库字节不变、旧日志排除、旧异常继续保留、非强绑定原告警不消失。Ruff、py_compile、diff 通过。
- 维护者二次复核消息日志、时钟报表、通知报告与归档契约：103 passed，不是独立第三方审计。最终全量 16180 passed / 1461 subtests（465.78s）；所有测试 `XIUXIAN_ALLOW_LIVE_TEST_DB=0`。
- 运行中 worker 继承环境核实：shadow_write=1、shadow_bind=1、recover_report_only=0、control_modules/control_identities 为空；本轮未修改。

## 仍需另审

完整群范围持久化、runtime 入站绑定与历史无范围记录的处理须补设计与跨群乱序回放，不能以这次报表修正代替完成。保持既有 Gate 4 禁止控制边界；下一次定时检查点出现范围 warn 是披露已有缺口，不是新业务故障。

## 定时验收

`df64e818` 已合入 main 并推送 `xiuxian-mian/main`，合入复验 46 passed。12:17:39 原定时器自然触发，12:17:40 的报告包含 `partial_id_only` 和 `precision_verified=false`，12:17:50 正常退出（status 0）。统计与前述只读回放一致；Attempt 总数 28119、blocked 4287、send_unknown 32，均未重写。

12:18:57 health 仍只有两份 held 通知告警、游戏 pending=0，watchdog 正常；主服务/observer/watchdog PID 未变。未将报表 warn 接入业务暂停。下一次定时检查为 10 月 7 日 00:15:35。仅检查点口径修正销号，同群事实持久化和完整精度审计仍待办。
