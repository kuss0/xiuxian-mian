# Rust MiniApp 上游审计（2026-07-30）

## 范围

- 上游仓库：`/opt/xiuxianbot-rs`
- `origin/agent/claude/miniapp-channel=003af8c`
- `origin/agent/claude/xinggong-stars-miniapp=d5c6cce`
- 星宫分支相对共同基线 `b2099d4f` 为 61 个文件、约 5.1 万行新增，包含完整 MiniApp 框架重构，禁止整包合并。
- 本地主线审计基线：`a6720aaf`

本轮只对照协议边界、状态归属和新增写动作。没有发送游戏命令，没有调用新的生产 MiniApp 写接口。

## 已吸收：宗门灵圃内层业务信封

上游明确记录 `/api/miniapp/xianxia-sect-farm/action` 的失败形态：HTTP 200、外层 `ok=true`，但内层 `actionResult.ok=false`。本地生产脱敏 capture 也确认 2026-07-20 至 2026-07-29 共 812 条观星台 action 回包全部包含 `actionResult`，键稳定为 `message,ok`；旧测试却把结果文案伪造成顶层 `message`。

本轮在 `model/features/stargazer_miniapp.py` 做本地化窄修：

- 严格要求 action 回包存在字典型 `actionResult` 且 `actionResult.ok is True`。
- 内层失败按业务失败终止当前 flow，不重试非幂等 action，也不增加成功动作计数。
- 错误和消息继续经过 MiniApp 脱敏器。
- 收获解析递归读取真实 `actionResult.message`，不再依赖不存在的顶层 `message`。

验证：`tests/test_webapp_core.py tests/test_stargazer.py tests/test_miniapp_protocol_flows.py` 为 `171 passed, 10 subtests passed`；合并 observer 回归后 focused 为 `345 passed, 13 subtests passed`，全量为 `3478 passed, 536 subtests passed`。

## 已有等价能力：不搬上游框架

上游 `90da9c1` 给共享 Session 记录产生它的 behavior，避免同一 app 下多个 behavior 时把终态喂错对象。本地主线没有 app 反查首个 behavior 的共享回喂路径；观星台、寻宝、深闭、小世界等 flow 在调用栈内直接消费自己的结果，因此不存在同构误绑点。保留当前显式 flow 所有权，不引入上游 SessionTable。

上游 `FailedBusiness` 是其共享状态机所需的终态类型。本地已有 `MiniAppHttpResult` 的传输/HTTP 分类和模块级业务终态分类；本轮只在有真实协议证据的观星台 action 层补内信封，不把 `actionResult` 规则错误推广到所有 MiniApp。

## 阻断：司星台扩建

上游新增 `action=expand`，按服务端 `upgradeCosts` 和账户贡献判断，并在分支中默认开启。该动作会永久消耗宗门贡献，且本地主线当前只自动执行 `collect -> soothe -> pull -> wait`。

生产结论：

- 不吸收上游默认开启策略。
- 若后续实施，必须独立 UI 开关、默认关闭，成本读取服务端字段，保留贡献下限，并先做只读状态/capture Gate。
- `plots` 缺失、非数组或数量为 0 时必须 fail closed，禁止猜测扩建档位。

## 阻断：晋升星宫长老

上游新增洞府 `POST /star-palace`、`action=promote_elder`，读取 `account.starPalace.elder` 的 `isElder/levelOk/canPromote/cost/contribution`。但上游文档也明确：`available=true` 的 elder 结构只有页面 sampleData，没有真机抓包。

该动作一生一次且消耗贡献。没有本方真实 `available=true` 状态和 action 回包前，不新增生产 adapter，不主动制造样本。未来 Gate 必须默认关闭、单身份授权、单发、不自动重试，并以重新读取权威状态作为终态。

## 阻断：通用宗门捐献补贡献

上游 `sect_donate` 以群命令维持贡献目标，默认关闭，并增加 30 分钟真冷却以等待 HTTP 快照回灌。本地当前只有天星普通闭关换取合气丹时的显式捐献链，受 action guard、回包 reducer 和配置闸门约束。

不把它扩展成通用贡献维持器，也不为了扩建/晋升自动花灵石。若未来确需通用捐献，必须作为独立默认关闭模块，按真实贡献余额、灵石保留量、发送回包和冷却证据设计，不能与 MiniApp 写动作隐式串联。

## 结论

- 已吸收：观星台 `actionResult` 严格业务信封和真实收获文案路径。
- 不吸收：整套 Rust MiniApp 框架、默认扩建、无真机证据的长老晋升、通用自动捐献。
- 后续只保留两个独立候选 Gate：司星台扩建、星宫长老晋升，二者均默认关闭且不得共用一次授权。
- 2026-07-30 11:39 主服务完成本批次唯一一次重启；11:44 只读 health observer 加载最终终态边界。两者 `active`、`NRestarts=0`，24 个身份恢复成功，pending 为空，health/watchdog 正常。
