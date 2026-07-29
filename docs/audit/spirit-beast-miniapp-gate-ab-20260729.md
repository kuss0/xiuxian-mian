# 万兽谷·驭灵行迹 MiniApp Gate A/B 审计（2026-07-29）

## 结论

wxjerry `origin/main=8ff9d90f` 对照确认，上游已将旧万兽谷放养链替换为
“万兽谷·驭灵行迹” MiniApp。本地主线此前没有对应 adapter。本轮只吸收协议层，
完成 Gate A/B 的离线建设；不启用生产调度，也不发送 HTTP 或游戏群命令。

## 已落地

- `model/features/spirit_beast_miniapp.py`
  - 入口按钮优先、文本 URL 兜底提取；只接受 Telegram host、可信 Bot shard 和
    `spiritbeast_*` start 参数。
  - 白名单 API：`start`、`expedition/start`、`expedition/choose`。
  - 请求构造复用统一 MiniApp adapter；safe summary 不保存原始 token/initData。
  - 只解析次数、可出行灵兽数、行迹阶段和奖励摘要，不保存 `runToken`。
  - flow plan 明确所有状态变更 POST 在 Gate C 前禁止传输层重试。
- `model/webapp_core.py`
  - 增加 `spiritbeast` token 脱敏和万兽谷入口推断。
- `model/features/miniapp_registry.py`
  - 注册 `spirit_beast` adapter/flow plan，`manual_only=true`、`default_enabled=false`。

## 未做与边界

- 没有新增 `spirit_beast` scheduler、持久化字段、UI 自动开关或生产 reducer。
- 没有执行 `start/expedition/start/choose` live 请求；没有从上游整块搬运同步
  requests、路线权重或重试状态机。
- Gate C 需要录制脱敏回包、确认 `expedition` 状态终态/次数/奖励字段，并完成单
  身份串行状态机和请求预算测试后另审。旧 `model/features/ranch.py` 保持不变，
  不在本轮切换主链。

## 验证

```text
tests/test_spirit_beast_miniapp.py
tests/test_webapp_core.py
tests/test_miniapp_command_catalog.py
101 passed, 10 subtests passed

tests/test_miniapp_entry_probe.py
tests/test_cave_treasure_runtime.py
tests/test_tower_miniapp.py
tests/test_tree_runtime.py
tests/test_trial_runtime.py
193 passed, 5 subtests passed
```

另通过 `py_compile` 和 `git diff --check`。本轮未重启服务、未打开开关、未读写
生产状态。
