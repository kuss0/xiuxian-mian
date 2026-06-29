# 天星宗探索链路接入 lab 记录

日期：2026-06-28

## 范围

- 只在 `/opt/xiuxian-main` lab 线开发与验证。
- 不触碰 `/opt/xiuxian`、`/opt/xiuxian-c`。
- 不 push、不上线、不重启生产。

## 已验证的真实文案结论

### 1. 探寻裂缝属于 `探索` 路线

真实样本已验证：

- `命盘【贪狼】照命` 会出现在裂缝成功结果里。
- `【天星偏转】` 会出现在裂缝成功/风暴类结果里。
- `【改命回天】` 会在裂缝风暴/败退场景触发，把“重伤/掉修为”扳成“带回少量收益且本次未损修为”。

因此口径明确：

- `.探寻裂缝` 吃 `定命`
- `.探寻裂缝` 吃 `.推命 探索`
- `.探寻裂缝` 吃 `.改命 探索`

### 2. 野外历练同样属于 `探索` 路线

真实文案中长期稳定出现：

- `【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30`
- `【改命待发】此道改命尚可维持 ...`
- `【天星偏转】 趋吉偏转，材料显化上扬`
- `【野外历练 · 改命脱险】 ... 【改命回天】`

因此口径同样明确：

- `.野外历练` 吃 `定命`
- `.野外历练` 吃 `.推命 探索`
- `.野外历练` 吃 `.改命 探索`

### 3. 定命 / 推命 / 改命的作用边界

- `定命`：当天命盘被动，不是直接收益命令。
- `推命`：路线押注，命中返天机值/贡献，落空加逆命劫。
- `改命`：`3` 点天机值换一条 `24h` 路线级后手，不是直接加收益。
- 对探索链来说，最值钱的是 `改命 探索`，因为它保失败。

## 本轮 lab 落地

### 已接入发送前预检的模块

1. `探寻裂缝`
2. `野外历练`

### 预检顺序

对 `探索` 动作统一执行：

1. 未定命 -> 先 `定命`
2. 已定命、无探索改命、天机足够 -> 先 `改命 探索`
3. 不在模块前置阶段盲插 `推命`
4. 条件满足 -> 再发送下游探索动作

### 为什么本轮撤掉模块前置 `推命`

- `推命` 不是“哪个动作现在要发了就补一下”的按钮。
- 它本质上依赖更高一层的**时间线规划**：
  - 接下来 8 小时主要走什么路线
  - 哪个动作窗口最值得押
  - 会不会因为中途改做别的事导致 `推命落空`

- 如果把 `推命` 当成裂缝/野外模块的即时前置动作，就会退化成：
  - 局部最优
  - 全局时间线失真
  - 更容易逆命

- 因此当前 lab 口径调整为：
  - `定命` / `改命` 可以由模块前置处理
  - `推命` 只能由上层时间线规划器决定何时发

### 逆命保护（本轮新增）

- 如果当前存在**别的路线推命**且仍在生效，例如当前是 `闭关` 推命未应验，则：
  - `探寻裂缝`
  - `野外历练`

  都必须阻断，不允许继续发送，也不允许硬补新的探索推命。

- 原因：这时强行发送探索动作，等价于主动吃 `推命落空 -> 逆命劫 +1` 的风险。

- 当前 lab 规则：
  - 有异路推命且 `prediction_until > now`：直接阻断到该时间点后再恢复调度。
  - 有异路推命但时间不可解析：阻断本轮，避免猜测。

### 重要执行策略

- 预检命令与探索主命令**不同轮发送**。
- 本轮若插入了 `定命/改命/推命`，则本轮直接返回，不继续发裂缝/野外。
- 下一轮调度再继续判断，避免同轮连发导致安全锁风险。

## 本轮顺手修掉的真实 bug

天星 preflight 原本只返回：

- `prepare_action`
- `prepare_command`

但没返回参数本体，比如：

- `set_star` 需要 `太阴`
- `predict` 需要 `探索`
- `change_fate` 需要 `探索`

这会导致真实执行层可能调用成：

- `execute_tianxing_manual_action("set_star", "")`
- `execute_tianxing_manual_action("predict", "")`
- `execute_tianxing_manual_action("change_fate", "")`

本轮已补：

- `build_tianxing_manual_plan(...)` 返回 `arg`
- `build_tianxing_route_preflight_plan(...)` 返回 `prepare_arg`
- 下游模块按 `prepare_action + prepare_arg` 调用

## 当前代码位置

- 天星预检计划层：
  - `model/features/tianxing.py`
- 探寻裂缝发送前接入：
  - `model/features/explore_rift.py`
- 野外历练发送前接入：
  - `model/features/wild_training.py`

## 当前仍是 lab 口径的边界

- 还没上线生产。
- 还没做长时间在线观察。
- 还没把同样策略接到闭关/斗法/炼制。
- 还没扩到生产 UI 的显式提示文案与开关策略审计。

## 已完成测试

### 天星 + 裂缝

- `.venv/bin/python -m pytest tests/test_explore_rift.py tests/test_tianxing.py -q`
  - `48 passed, 7 subtests passed`

### 真实回放

- `.venv/bin/python -m pytest tests/test_real_message_replay.py -q`
  - `15 passed, 2 subtests passed`

### 天星 + 野外 + 回放

- `.venv/bin/python -m pytest tests/test_wild_training.py tests/test_tianxing.py tests/test_real_message_replay.py -q`
  - `61 passed, 6 subtests passed`

### 天星 + 探索双链最终回归

- `.venv/bin/python -m pytest tests/test_explore_rift.py tests/test_wild_training.py tests/test_tianxing.py tests/test_real_message_replay.py -q`
  - `87 passed, 9 subtests passed`

### 格式自检

- `git diff --check`
  - 通过

## 接手建议

- 下一步若继续扩展，优先把“探索链路”视为一个共用抽象，而不是继续在每个模块里复制预检判断。
- 但在抽象之前，先确认是否还要把 `探寻裂缝`、`野外历练` 之外的探索子玩法纳入同一路由。
- 若明天进入生产前评审，先看：
  1. 预检是否会拖慢探索节奏
  2. 是否需要把预检等待时间改成独立短重试
  3. 是否需要 UI 明示“当前卡在定命/改命/推命哪一步”
