# 七派未开局记录恢复

基线 `87aa0ec6`。生产记录 `1795106473722` 的两个直接回复，均在主群 `-1002083016447`、话题 `0`：

- `.开密议` `1634921` → `1634922`：本群还有天澜草原进行中或等待播报，请等结束后再发起。
- `.散议` `1634985` → `1634988`：眼下没有密议。.开密议 发起一局。

本局没有绑定入场，仅有队长这两次已确认发送、阶段为零的动作。后续碰撞 `1635592` 属于另一支队伍，不能用于本队结算，也不能补记奖励。

## 修复

启动本地日志筛选此前没有包含第一条拒绝文本，无法还原完整拒绝链。现在保留这条记录，并通过现有启动回放送入业务 reducer。

恢复必须同时核对机器人、群、话题、原回复 ID、命令 ID、阶段、时间顺序和发送状态。已有散议动作的旧记录，只有两条精确拒绝都匹配才归档为 `cancelled`，释放本地占用。原 attempts、collision 和 quotas 保留，原记录及取消证据写入 reconciliations；下一次安排不早于拒绝发生次日北京时间 00:05。保存失败回滚内存更改。

实时开局拒绝且尚无散议动作时，直接转为 `waiting_room`，沿用既有等空房退避。不会为确定未开房的记录派生 `.散议`，不会补记奖励。陌生队伍开议或终局不能替本队消费奖励配额。

## 验证

```bash
TZ=Asia/Shanghai XIUXIAN_ALLOW_LIVE_TEST_DB=0 /opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_weekly_adventures.py tests/test_council_recovery.py \
  tests/test_dungeon_openings.py tests/test_expedition_integration.py
/opt/xiuxian-main/.venv/bin/python -m ruff check \
  model/app_weekly_adventures.py model/features/weekly_adventures.py tests/test_weekly_adventures.py
git diff --check
```

2026-11-20：300 项通过，Ruff 与差异检查通过。覆盖真实群和话题的临时日志启动恢复、外队报文、17 类污染、实时拒绝后跨超时不发散议。独立纯内存审查另验证保存失败回滚和重复回放不重复归档。

发布通过正常服务重启触发现有本地日志回放；不手改生产数据库，不重新发送历史命令。上线验收应确认原记录取消、两条原回执证据落盘、配额与 attempts/collision 未改，以及服务、发送队列和看门狗状态。
