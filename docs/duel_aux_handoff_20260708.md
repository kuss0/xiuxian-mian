# 斗法辅助交接 2026-07-08

本次为辅助/lab 候选改动，只负责代码、离线测试和交接说明；不代表上线授权。未重启服务，未发 `.斗法`，未修改生产 DB，未开启斗法模块。

## 边界

- 主控负责最终 diff review、上线窗口、重启、生产探测、回滚和后续监测。
- 本次候选只改斗法代码与测试：`model/features/duel.py`、`tests/test_duel.py`。
- 当前工作区另有 `model/features/small_world.py`、`tests/test_small_world.py` 脏改，非本次斗法改动，未处理、未回退。
- 只读确认当前 `identity_module_state`：24 个身份，`duel_enabled=0`。

## 真实日志校准

只读扫描 `data/messages/2026-07-01.log` 到 `2026-07-08.log`，按 `(chat_id, message_id)` 去重，只统计正文以 `.斗法` 开头的真实命令。

- 去重 `.斗法` 命令：227 条，均为 `event_type=message`，未见脚本 `event_type=sent`。
- 有明确终局回包：204 条。
- 终局分类：正式战报 169，逃脱 17，锁定目标天机反噬 12，未踏入仙途 3，冷却 2，出手次数过多 1。
- 同一发起人相邻斗法间隔：p10 约 4.88 分钟，p50 约 49.52 分钟。
- 同一发起人同一目标相邻斗法间隔：p10 约 5.72 分钟，p50 约 49.53 分钟。
- 若上一条是真战报，同一发起人下一次斗法：p10 约 13.55 分钟，p50 约 49.62 分钟；同一发起人同一目标 p10 约 16.43 分钟。

因此原候选固定 5 分 15 秒太贴近游戏硬 CD，不安全；已改为更保守的随机节奏。

## 候选逻辑

- 正式战报或 `【斗法终局】` 仍计为一次完成。
- 非战报但明确说明命令已被游戏处理的回包也计为一次尝试并清 pending：
  - `凭借神通侥幸逃脱`
  - `锁定目标时遭遇天机反噬`
  - `尚未踏入仙途`
  - `出手次数过多`
  - `元神尚未平复` / `无法再次斗法`
  - `神念不足` / `神念耗尽`
  - `虚弱` / `无法锁定对手` / `对方正在斗法` / `你已在斗法` / `小隐于野`
- 中间态 `正在锁定对手天机`、`法宝齐出`、`战斗结束正在整理战报` 只延长等待，不计次数。
- 正常/胜利结果后随机冷却：18-32 分钟 + `CD_BUFFER_SEC`。
- 虚弱、逃脱、锁定失败、出手受限、未知/失败结果后随机长冷却：30-55 分钟 + `CD_BUFFER_SEC`。
- 多目标池额外增加 3-8 分钟随机错峰。
- 斗法回复超时也按未知结果处理，使用 30-55 分钟随机长冷却，避免固定重试节奏。
- 不自动删除目标、不自动开启模块、不自动改生产配置。

## 测试

已在 `/opt/xiuxian-main` 运行：

```bash
/opt/xiuxian-main/.venv/bin/python -m pytest -q tests/test_duel.py
# 25 passed, 4 subtests passed

/opt/xiuxian-main/.venv/bin/python -m py_compile model/features/duel.py tests/test_duel.py

git -C /opt/xiuxian-main diff --check

/opt/xiuxian-main/.venv/bin/python -m pytest -q tests/test_module_manifest.py tests/test_app_scheduler_contract.py tests/test_message_evidence.py
# 109 passed, 49 subtests passed
```

## 主控复核建议

- 重点审查非战报终局计数口径是否符合 GM 预期；用户已确认“计入”。
- 重点审查 18-32 分钟、30-55 分钟、额外 3-8 分钟这组节奏是否还要继续放慢。
- 上线前确认斗法仍为默认关闭，并只对明确授权身份配置小批次数。
- 上线后第一轮建议只给单身份、小次数、人工观察，不接大批量。

已干完，等待主控审核处理。
