# 天机命脉 MiniApp Lab 审计

日期：2026-07-28

范围：只读页面与单身份 `/start` 探测。未执行 `/draw`、`/interpret`、`/choose` 或 `/settle`，未消耗当日命脉次数，未保存入口 token、initData 或 `tgWebAppData`。

## 已确认协议

- 页面：`/miniapp/xianxia-fate-cards`
- token：`fate_` 或 `fate-` 前缀
- API：`/start`、`/draw`、`/interpret`、`/choose`、`/settle`
- 洞府动态入口：外府目录 `action=fate_cards`
- 页面前端初始问天主题：`cultivation`
- 页面前端初始命择：空值，用户点击选项前“承接命脉”按钮不可用

## 真实只读样本

使用一个登录账号的临时会话副本，通过洞府 `/start -> /details -> external:fate_cards` 取得入口，再请求一次天机命脉 `/start`。回包 HTTP 200，顶层字段为：

`challengeDate`、`choices`、`hasDrawn`、`ok`、`questions`、`record`、`spread`、`traceBalance`

当天状态：

- `hasDrawn=false`
- 六个主题：`cultivation`、`opportunity`、`wealth`、`relationship`、`sect`、`calamity`
- 三个命择：`accept`、`defy`、`hide`
- 三个命择均没有 default/selected 标记
- 服务端没有 `defaultChoiceKey`
- `record` 尚为空

## 结论

原待办中的“保留页面全部默认选择”不能直接形成完整自动链。页面只明确默认了问天主题 `cultivation`，没有默认命择；擅自把第一个选项 `accept` 当默认值会改变不可逆业务选择。

当前实现因此保持 manual-only：

1. 自动 probe 只允许 `/start`。
2. `/draw`、`/interpret`、`/choose`、`/settle` 构造器均要求显式 Lab mutation 授权。
3. 没有服务端默认命择时，状态机返回 `default_choice_unknown` 并阻断。
4. 即使任务完成并出现 `quest.canSettle=true`，当前也只报告 `manual_settle`，不自动结算。

后续要进入单号 canary，必须先确定命择策略是人工配置、固定业务选择，还是等待服务端增加默认字段。确认前不得批量抽牌或结算。

## 2026-07-29 实现与生产 Canary

主线没有把首项当作服务端默认值，而是新增显式命择策略：

- UI 可选择 `accept`（承命）或 `hide`（藏锋）；`defy`（逆命）继续阻断。
- 全局自动开关默认且现网保持关闭；手动动作和后台调度复用同一状态机。
- `draw`、`interpret`、`choose`、静室 `meditation`、最终 `settle` 均为单次 POST，HTTP 层不重试。
- 每次状态变更后重新请求 `/start`，以服务端状态确认动作是否生效；传输未知时不盲目重放。
- 静室使用 `/api/miniapp/xianxia-dwelling/meditation`，不是命脉页面自身的伪结算接口。
- 同日收益写入顶层累计 `gains`；兼容旧记录中的 `meditation.gains` 与 `reward`，且顶层累计存在时不重复并入旧字段。

生产只使用身份 `7538826434` 做单号 canary，命择为 `accept`：

1. 首轮各执行一次 `draw`、`interpret`、`choose` 和静室结算，均为 HTTP 200、`attempt=1`。
2. 首轮静室获得修为 `+18`，任务进度为 `9/30`，状态 `waiting_quest`。
3. 约 30 分钟后静室再次可结算；第二轮仅执行一次静室结算，任务达到 `30/30`。
4. 随后仅执行一次 `/settle`，获得天机残痕 `+2`，最终状态为 `settled`。
5. 最终累计播报与状态记录均为：修为 `+36`、天机残痕 `+2`。

脱敏 capture 中变更请求计数为：`draw=1`、`interpret=1`、`choose=1`、`meditation=2`（两个独立可结算窗口）、`settle=1`；所有变更请求均只有 `attempt=1`，没有重复提交。入口 token、initData、`tgWebAppData` 未写入审计文档或状态记录。

验证：相关聚焦回归 `410 passed, 13 subtests passed`；全量 `3424 passed, 535 subtests passed`；UI HTTP smoke 覆盖 76 条鉴权路由并通过；`compileall`、MiniApp JS 语法检查和 `git diff --check` 均通过。

结论：协议、状态机、UI 与单号生产闭环均已验证。功能可以保留上线，但全局自动开关继续默认关闭；扩大身份范围属于独立业务授权，不由本次 canary 自动开启。
