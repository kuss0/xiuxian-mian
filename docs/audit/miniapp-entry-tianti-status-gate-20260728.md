# MiniApp 入口统一与天阶状态 Gate 审计（2026-07-28）

## 结论

本轮完成洞府 `externalApps` 动态入口解析收口，并新增 `.天阶状态` 的洞府公共入口只读 Gate。Gate 默认关闭、按身份白名单启用，不接管 `.登天阶`、问心台或罡风调度。

## 实现边界

- 统一识别 `data.account.externalApps.groups[].apps[]`、`externalApps.apps[]` 和 `externalApps[]` 三种结构。
- `available` 缺省按可用处理，并兼容布尔、数字和常见字符串值；多个匹配项优先选择明确可用且有 URL/action 的入口，其次选择可用的 key-only 动态入口。
- 钓鱼、天机试炼、观星台、灵树和琉璃问心塔复用同一入口选择层，避免各模块继续维护不同结构判断。
- 新增 `cave_public_tianti_status_enabled` 和 `cave_public_tianti_status_identity_ids`。两项同时满足且已配置公共入口时，所选身份的状态读取走洞府天机阁。
- MiniApp 回包只调用现有天阶面板 reducer 校准状态，不在 HTTP 回包处理中触发登阶。
- 公共入口失败时后台保守退避，不在同一轮补发 `.天阶状态` 群命令；未选中身份继续保留原命令链。
- 身份到期判断显式绑定 `send_as_id`，避免后台候选读取到其他身份的天阶计时器。

## 风险控制

- Gate 默认关闭，身份白名单默认为空。
- 不增加 MiniApp 并发；仍由洞府公共入口全局串行 worker 和 90 次/分钟总限流约束。
- 不改变天阶 reducer、登阶 CD、问心和罡风状态机。
- 不执行生产主动探测；上线后只观察自然到期的只读状态同步。

## 验证

- focused：`156 passed`。
- MiniApp/洞府/钓鱼/灵树/试炼/塔/世界 Boss/UI：`682 passed, 32 subtests passed`。
- 全量：`3371 passed, 535 subtests passed`。
- `python -m compileall -q model tests`、`node --check model/web/static/js/miniapp_ui.js`、`git diff --check` 通过。
- UI 真实 HTTP smoke：17 项通过。

## 上线口径

代码可上线，但 Gate 保持默认关闭。首次启用应只选择一个已开启登天阶的身份，等待自然状态同步，确认日志仅出现天机阁只读校准且没有同轮 `.天阶状态` 群命令后，再扩展白名单。

## 生产结果

- 提交 `7fbb5166` 已推送至 `xiuxian-mian/main`，2026-07-28 03:38 UTC+8 重启 `xiuxian.service`。
- 启动后 24 个身份恢复成功，`global_enabled=1`、pending 队列为空、watchdog 正常。
- 现有生产配置未写入 Gate 字段，归一化结果仍为“关闭 + 空白名单”，上线未触发天阶公共入口请求或群命令。
- health observer 仅保留已知的 listener sidecar 未独立授权告警；主运行时监听正常。
