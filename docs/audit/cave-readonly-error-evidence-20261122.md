# 远航只读回包诊断

生产基线 f11feb3f。此前已接入“正在洞府外等你接引”尾句，但竹叶清风
正常只读入口仍返回 unparsed_voyage_status。完整回包未保留在 HTTP 错误
响应中，现有样本不足以判断剩余差异。

`run_cave_public_tianjige_read_only` 已在身份和入口核验后返回 command、
reason 和 raw_message；通用 `_write_json_result` 在 ok=false 时丢弃 extra。
本候选只给 `/api/cave-public-entry-run` 的 `concubine_voyage_status` 增加
有界诊断：仅 `.远航状态`、unparsed_voyage_status、最多 4096 字符的文本
可进入响应。URL 会替换为 `[link]`，其余 extra、凭据和快照不进入响应。

仍返回 HTTP 400 / ok=false。身份失败、其他原因、其他动作、变更指令和
过长文本沿用原有错误封装。没有扩大远航解析器、结算或调度条件。

定向回归 175 passed；全量 19422 passed、1561 subtests passed in 564.46s。
全量 Ruff、编译和 diff 检查通过。生产校准待完成。
完整日志 `/root/xiuxian-maintenance-20261122/readonly-evidence-full.log`。
