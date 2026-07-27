# 频道冻结期间野外 MiniApp 调度修复（2026-07-28）

## 问题

频道 send-as 因 `SendAsPeerInvalidError` 冻结后，统一洞府层仍允许 `restore_identity_ids` 使用纯公共入口 HTTP；但野外历练的全局到期扫描先用 `get_identity_enabled()` 过滤，导致这些身份永远无法进入 MiniApp worker。

生产状态显示 20 个频道身份的野外计时停留在 2026-07-26：

- 16 个非天星身份，最早到期 00:32:06、最晚到期 01:25:56。
- 4 个天星身份，最早到期 00:58:43、最晚到期 01:21:17。
- 同期 WA 与 Lsfnqy 两个可用个人身份已于 2026-07-28 正常完成两轮，证明公共野外协议本身可用，故异常位于候选过滤而非 MiniApp 接口。

## 修复

- 野外全局到期扫描改用 `is_cave_public_identity_available()`，纳入仅因频道健康被冻结、但仍在恢复名单内的身份。
- 对冻结且尚无计时器的公共身份补初始化候选，避免永远没有首轮调度。
- 非天星身份可继续走洞府公共入口，全局仍保持单 worker、身份间至少 30 秒。
- 天星身份只有在已有有效探索推命/改命证据时才可继续公共 MiniApp；前置不满足时只写入短复查，不发送 `.天机盘`、`.推命`、`.改命` 等群命令。
- health observer 新增 `channel-frozen public wild-training lag` 只读告警，专门识别冻结恢复名单内非天星公共野外调度滞后，避免被总的频道冻结告警掩盖。

## 验证

- focused：`263 passed, 8 subtests passed`。
- 全量：`3376 passed, 535 subtests passed`。
- `py_compile`、`git diff --check` 通过。
- 上线前只读 observer 已从 1 项业务告警增加为 2 项，新增项准确命中公共野外滞后队列。

## 生产验收门槛

- 16 个非天星频道身份开始按全局串行补跑，且不发送旧 `.野外历练` 或任何入口群命令。
- 4 个天星频道身份在缺保护时保持等待，不尝试群命令。
- 不出现并发任务堆积、429、502、身份错绑或重复结算。
- 对应身份计时器推进后，`channel-frozen public wild-training lag` 告警应自动收敛；频道 cohort 冻结告警仍保留，直至频道恢复。

