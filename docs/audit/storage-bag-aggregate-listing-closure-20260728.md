# 储物袋聚合挂单验收（2026-07-28）

## 结论

“目标号只上架一次，其他来源号按份数购买”的洗灵石聚合链已在主线实现并经过生产闭环，不需要继续保留为代码进行中事项。

## 实现

- `model/ui.py` 接收 `listing_unit_price`，按用户给定的上架数量和单价计算总价，不自动改价。
- `model/web/static/js/storage_bag_ui.js` 提供上架数量、单价和只读总价回显。
- `model/features/storage_bag.py` 在单次挂单成功后建立来源购买队列，逐身份发送 `.购买 <挂单ID>*<份数>`，并按真实购买回包推进下一来源。
- 首次实现提交为 `889ee301`，当前主线继续包含后续超时和回包恢复修复。

## 生产证据

`data/state/workflow_logs/storage_bag_transfer/2026-07-14.jsonl` 已记录两次完整聚合闭环：

- 挂单 `24181`：目标身份单次上架 `黄芽丹*50`，5 个来源分别购买 12、12、11、11、2 份，最终 `购买成功 5/5`。
- 挂单 `24182`：目标身份单次上架后由 11 个来源分摊购买，最终 `购买成功 11/11`。
- 每个购买步骤均有发送消息 ID、Bot 成功回包和本地储物袋核销记录，不是仅凭本地队列判定完成。

## 验证

- `tests/test_storage_bag_transfer.py tests/test_storage_bag_api.py`: `95 passed`。
- 当前全量：`3374 passed, 535 subtests passed`。
