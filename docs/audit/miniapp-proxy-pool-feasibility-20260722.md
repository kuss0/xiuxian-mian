# MiniApp 代理池可行性评估（2026-07-22）

## 结论

当前不应接入轮换代理池。可保留的最小候选只有“默认直连，连接建立失败后切换到一个已配置代理”的单向兜底，而且必须在独立 Lab 中按请求幂等性分层；HTTP 429、业务限流、次数耗尽和服务端拒绝不得触发换代理。

本机当前 `TG_REQUESTS_PROXIES` 为空，`TG_PROXY_TYPE` 未设置；虽然环境中保留了代理 host 字段，但不会生成 Requests 代理配置。因此现在线上 MiniApp HTTP 实际走直连，没有需要紧急修复的代理故障。

## 现有能力

- 本地 `model/features/world_boss_miniapp_runtime.py` 已为每个身份建立独立 `requests.Session`，同一身份的入场和战斗复用该 Session，并在流程结束或失败时关闭。
- 本地 `model/webapp_core.py` 已有进程级 `90 次/分钟` 滑动窗口限流和 World Boss 优先租约；代理路由不得改变这一预算。
- WebView `initData` 由对应登录账号通过 `RequestMainWebViewRequest` 获取，原文只保存在短生命周期内存中，不写数据库和日志。
- wxjerry `9243e926` 的可取部分是按身份保持 HTTP 路由、默认直连、仅在连接类异常时切一个配置代理、结束时显式关闭 Session；它不是轮换代理池方案。

## 当前差异

本地 `_requests_transport()` 会把 `TG_REQUESTS_PROXIES` 直接传给每次 World Boss 请求。当前配置为空时等价于直连；将来一旦配置代理，则会变成全程强制代理，缺少 direct-first 和身份级路由状态。

这不是立即上线代理池的理由。MiniApp 的 `start/join/hit/finish/claim` 含非幂等 POST；连接中断不总能证明请求未到达服务端。若在传输状态未知时换代理重放，会制造重复入场、重复出手或重复领取。

## 安全口径

1. 每个身份、每场事件固定一个 HTTP Session 和路由，不在同一已建立流程中轮换出口。
2. 只有 DNS、TCP connect、TLS 建连失败等可证明尚未取得 HTTP 响应的连接类错误，才可考虑从直连切到单个配置代理。
3. 非幂等请求即使发生连接类异常，也默认停止本轮；先用服务端只读 `state` 或下一次安全入口校准，不自动重放原 mutation。
4. HTTP 429、`rate_limited`、业务 CD、次数耗尽、token 已使用和验证要求均按服务端退避处理，不换代理、不重取身份规避。
5. 全局仍以 `90 次/分钟` 为硬上限；代理数量不增加预算，也不拆分成多个本地计数器。
6. 代理故障时 fail-closed。禁止在同一请求中代理和直连来回抖动，禁止把代理认证、完整 URL、token 或 initData 写入日志。

## Lab Gate

1. 为 World Boss transport 增加身份级 `route=direct/config_proxy` 状态和显式 Session builder，默认 `trust_env=False`。
2. 用 mock 覆盖直连成功、连接建立失败、代理失败、HTTP 429、非幂等 POST 状态未知及流程结束关闭 Session。
3. 证明路由切换不会重放 `start/join/hit/finish/claim`；只读 `state` 校准另设请求。
4. 继续复用全局 MiniApp limiter，测试代理启用前后 60 秒窗口都不超过 90 次。
5. 只有用户明确配置单个代理并授权 canary 后，才做单身份生产观察；没有代理配置时不改线上路由。

## 销号判断

“代理池可行性评估”已完成：轮换代理池无必要且风险高，不进入生产；单配置代理兜底可作为独立 Lab 候选，但当前没有配置和故障证据，不实施 runtime 变更。
