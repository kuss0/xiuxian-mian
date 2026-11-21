# 青溪单副本入口与预检诊断：待发布验证

当前仅在隔离工作树 `/root/xiuxian-qingxi-single-daily-release-lab-20261121` 验证，生产基线 `28155cd0`。单副本实现来自实验提交 `a50559ae`；预检诊断来自独立补丁。尚未生产部署，不能据此填写任何真实通关槽位。

## 行为与边界

旧 `action=rewards` 会执行整批，无法完成规格要求的逐副本验收。新增认证 `POST /api/qingxi-quests` 操作 `reward_one`，必须显式提供身份和一个 `daily_id`：`moonpalace/bloodland/southgate/escort/mulanshrine/bqpuppet/jianzhong`。`reward_one_refresh` 只核对同一指定范围。旧操作携带单项字段会拒绝，避免静默整批。

- 原生助手仅在内存注入指定单项入口；vendor 文件及其 SHA256 不变。请求策略拒绝其他六副本和双试炼的写操作，首次战败不进入原生重开分支。
- 保留全局任务互斥、本人身份、活动占用、洞府公共入口锁与发送前持久化不确定围栏。成功证明须包含同一目标真实 run、明确有奖 start、成功 end、领奖前后变化、九项新鲜关闭状态、零 pending、最终新鲜任务和服务端飞行关闭。任一显式 run ID 冲突保留 journal，不按优先级吞掉矛盾。
- 同副本当天战败同时阻止单项、手动整批与自动整批；其他副本的手动入口仍须经过原有身份及占用检查。单项结果、范围通知和去重记录独立持久化，不覆写原整批进度或调度时间。
- 单项工作流最长 3600 秒，父进程完整预算 4640 秒；父级入场前、spawn 前和目标原生 start 前核对跨零点预算。刷新不发新 start，错范围或普通刷新不能解除未知单项占用，当前新鲜检查与旧局证明分别保存。
- 单项通知只含本目标 run 的奖励，发送前记录 intent，以普通优先级即时通报；投递未知不自动补发。旧 NPC 通知的 `sent` 仍可能只是汇总入队，本变更不据此宣称其 Telegram 已确认。

两号 Qingxi 自动调度保持关闭。阶段四仍须先通过 WA 两场地第一重单场，之后按六试炼、七副本逐项、整批的顺序验收；目前 WA 双场地当天真实战败停跑，26 槽仍 0/26。本入口的实现与离线测试不越过这些闸门。

## 预检诊断

held 记录继续保留占用风险，去掉重复的“今日未执行”。缺少、非法或未来开始时间的 held/待复核明确报告未知时长 `at_risk`，不能因缺时间漏报；正常记录仍保留 15 分钟宽限。只有匹配同场地、同 run、明确关闭且真实战败的暂停记录降为 `watch`，说明正在按保护策略停跑；陌生或矛盾暂停仍 `at_risk`。CLI 补身份 ID。仅改变诊断，不修改运行放行、暂停或游戏状态。

## 验证

- 单项相关套件：801 passed，92.84 秒；Ruff、Python 编译、Node 语法及 diff 检查通过。部分最后阶段涉及 mock Chromium，已结束，不与后续实机重复运行。
- 主控制器在发布工作树独立撤修：移除整批战败检查后对应业务用例失败；移除显式 run ID 冲突检查后七个端点的反例失败；恢复保护后 8 passed，47 deselected。日志 `/root/xiuxian-qingxi-7plus6-release-20261121/single-daily-validation/`。
- 预检在发布前发现未知年龄 held 漏报后补了回归，76 项通过。第一轮全量因修正该诊断而主动中止，不作为通过结果；中断涉及的天星用例独立重跑通过 110 个子测试。
- 最终隔离全量 **19236 passed, 1561 subtests passed in 557.61s**；全量 Ruff、Python 编译、四项 JS 语法和 diff 检查通过。日志 `/root/xiuxian-qingxi-7plus6-release-20261121/single-daily-validation/full-pytest-final.log`。
- 发布前一致性 SQLite backup 与源归档在 `/root/xiuxian-qingxi-7plus6-release-20261121/single-daily-2200/`，`quick_check=ok`；两主号无 pending/gather/不确定或运行占用、飞行已闭合，两个自动开关 false，WA 双场地暂停保留。尚待推送、仅主服务重启及实态核验。

## 尚未证明

七个副本成功 start/end 的完整真实服务端回包尚未逐一取得；当前 reader 测试结合已存只读状态形状与官方客户端消费字段的模拟回包。字段缺失会保持 held，不能据 mock 推断真实通关。资源缓存 v2、持续格挡和提前躲跃斩候选均未纳入本次变更，未证明加载或胜率改善。
