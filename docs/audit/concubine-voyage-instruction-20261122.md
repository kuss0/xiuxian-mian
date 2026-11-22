# 远航等待接引的官方结算提示

基线 ba160348。只读诊断已于 11:31 取得竹叶清风完整回包：

```
侍妾【南宫婉·月影】已自【月殿寻痕】航线归来，正在洞府外等你接引。
请使用 `.远航归来` 结算本次远航。
```

此前 f11feb3f 仅适配第一行。`_parse_voyage_status_text` 的严格匹配因此
仍拒绝实际两行文本。本候选只给原返回句追加可选的固定官方提示行；
仍完整匹配，不丢弃任意第二行或附加段落。提示不是结算回执：状态仍为
returned，配额、奖励和结算动作不由提示完成。

fixture 保留实际回包。定向 319 passed；直接、原生、被动三条归属路径
均关闭原只读 pending，不补发、不结算，重复回执无变更。HTTP 路径
同步 returned，未知尾文、其他指令、已结算和无任务混搭均拒绝。
撤修核对：基线正则不匹配真实规范化文本，候选匹配。
全量 19431 passed、1561 subtests passed in 566.57s；Ruff、编译和 diff
检查通过。完整日志 `/root/xiuxian-maintenance-20261122/voyage-instruction-full.log`。
WA 原生主线低血收尾后，11:48 发布并推送 cebeaeaf，仅重启主服务。
一致性备份 voyage-instruction-pre-release.db 已通过 quick_check；发布前
UI 队列、持久 pending 均空，没有活动浏览器。启动恢复 24 身份，UI HTTP
200、watchdog 正常。11:49:59 同一身份的正常只读回包已同步 returned、
人物南宫婉·月影、路线月殿寻痕，不把提示当成已结算或奖励。
实机响应 `/root/xiuxian-maintenance-20261122/voyage-instruction-live-confirmed.json`。
