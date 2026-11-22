# NPC 野外预警的原生帧计时

基线 b9f9e9c5，隔离分支 lab/qingxi-native-frame-clock-20261122。
上游原版 2.0.114 与 SHA256 保持一致。Linux 接入层为 NPC 野外预警补充
原生计时来源；上游仍负责预警规则、寻路、输出与任务流程。

## 来源与可复现问题

当前官方 dwelling-field-combat.js（SHA256 57c2e83320a6ae3525f1d38d4fa08c20d39fdc400d7c98716c89bd94f9399081）
的 stats():2724 在 questMode 下返回 wave=null。step():2604、2608、2367
按收到的模拟 dt 推进计时与敌人招式；宿主 dwelling-3d.js:3028、3032
先经过 frameDelta，再调用 step(combatDt)。12:10 重新下载的 dwelling-3d.js
与所读资源一致，SHA256 279e69decbcb430f968a4400256e41fc0995c9e49e06dad1a9b26b6ce6876656。

上游 qiThreats():623、trapZones():879 与弓箭预警:925 在 wave.time 缺失时
使用 performance.now() 墙钟。低帧率或 hitstop 下，同一次招式的 started
会漂移，超过 0.25 秒后重新捕获剑气，覆盖原锁定的横移方向与反应状态。

独立回放 `/root/xiuxian-maintenance-20261122/field-qi-clock-repro.cjs`
执行原版完整 qiThreats：同样推进 6 次原生 0.25 秒，正常时间比只捕获 1 次，
2.6 FPS 墙钟回退捕获 3 次；观察原生 dt 时重新稳定为 1 次。该时间与位移
输入是合成回放，不是 WA 受伤或整场战斗的复现，也不证明其他招式已修好。

## 接入范围

tools/qingxi_combat_clock.js 在工厂返回时接入当前控制器的 step，保留原
参数、this、返回值和原异常。仅把已执行、非暂停、活动且启用控制器的实际 dt
记入私有计时。初始墙钟只作为固定原点；同一控制器内不会用墙钟补进度。
控制器替换、观察方法被覆盖或计时无效时拒绝提供野外时钟。

仅 NPC 任务生成脚本接入此观察。原生 wave.time 优先；野外 wave=null
使用观察计时；其他模式继续现有路径。只读、整批和单项每日副本均不接入。
观测元数据进入 result.json 的 native_clock；引擎 dt、招式、坐标、战报、
结算字段及 vendor 字节均沿用原值。开关、身份和未知请求账本沿用已有边界。

## 验证

初版 622 项定向回归通过；补齐停用分支后，两项帧时钟回归通过。
实际生成的脚本回放覆盖步进参数/返回值、暂停与非活动
控制器、保持 active 的停用控制器、重复安装、控制器所有权、原生异常和
2.6 FPS 下的剑气锁定稳定性。
撤修路径执行原版完整函数，仍捕获 3 次。四种生成脚本语法、Ruff、编译和
diff 检查通过。首次全量在补齐原生停用分支前中断，不计为通过。
最终全量 19433 passed、1561 subtests passed in 559.78s；日志
`/root/xiuxian-maintenance-20261122/native-clock-full.log`。
尚未发布或验证真实 q30 通关。
