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
