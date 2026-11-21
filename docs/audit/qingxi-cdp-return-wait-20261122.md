# 青溪原生返回回执的 CDP 等待修复

WA job `quest-5adea6a3095a46799d2bdca323dc478c` 在主线开始前约32.5秒即报 `qingxi_native_health_return_timeout`。原生返回等待仍有900秒预算，但 pinned v13 的 `Page.js` 在入坊后使用单次30秒评估上限，`Page.wait` 不捕获此 `TimeoutError`。旧日志将发起返回、轮询和读取结果合并处理，因此不能确定这次超时发生在哪一段，也不能据末端坐标证明返回成功。

这次人物预热正常：35次调用完成，无编译、轮询或预算错误，模型与入坊场景曾真实就绪，退出时恢复渲染器。最终只留下4条成功任务查询、0次施法，无主线、奖励、未决或收尾错误，任务fresh。浏览器已清理，宿主保留held；同号q20仍未推进。

worker现在将原生返回与重新入坊各发起一次，在原900/650秒预算内只读同一份完成回执。pinned CDP评估读取超时可以继续等；发起阶段超时、连接断开和其他协议错误仍停止。每次只读超时受剩余总预算限制，新增分阶段诊断，不重复发起操作。原洞府/坊市、所有权、生命、回执及场景校验沿用原规则，未改pinned助手。

宿主另有本次旧格式记录的窄对账：同身份、精确版本、完整四笔成功只读回执、无主线/施法/奖励/未决、初始满血非交战场景；前一次已审阅故障的原始结果与保存摘要须一致、审阅准确绑定其目录与时序。只有本次新鲜任务读取后，再取得新鲜飞行关闭回执，并复核文件和宿主记录没有变化，才关闭这一worker占用。field聚合仍可为unknown，不据此声明服务端全部idle。新worker含分阶段诊断的未知停止不匹配这个旧记录谓词。保存失败回滚，前次审阅引用保留。

隔离最终树 `/root/xiuxian-qingxi-cdp-return-release-20261122`，全量 **19380 passed、1561 subtests passed in 579.72s**，发布前461项相关测试、Ruff/编译/JS语法/diff检查通过。worker与宿主分别撤修后真实回归用例失败，恢复后通过。全量之后仅更正一段注释，Python AST相同，证明见 `cdp-return-validation/post-full-comment-only.json`。

生产提交 `b34b264a`、`5fa0652b` 已推 `xiuxian-mian/main`，01:58:25在发送队列和全局pending为空、无浏览器的窗口仅重启主服务，PID2491533/NRestarts0。UI200、24身份、watchdog与健康观察active，青溪两自动开关false。备份和验证位于 `/root/xiuxian-qingxi-7plus6-release-20261121/cdp-return-release-0158/` 与 `cdp-return-validation/`。本修复不是q20完成或试炼通关证明；真实后续运行另记验收事实。
