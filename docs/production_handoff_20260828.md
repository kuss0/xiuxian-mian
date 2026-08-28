# 修仙脚本生产维护交接（2026-08-28）

> 本文件是换人维护后的首要入口。生产事实以 `/opt/xiuxian-main` 当前 checkout、实时日志、SQLite 状态和认证 UI 回读为准；历史交接与审计只作背景参考。

## 0. 接手清单

| 项 | 当前值 |
|---|---|
| 生产仓库 | `/opt/xiuxian-main` |
| 分支 / HEAD | `main` / `ba1760fb` |
| 生产远端 | `xiuxian-mian` -> `git@github.com:kuss0/xiuxian-mian.git` |
| 上游参考 | `origin` -> `git@github.com:wxjerry/xiuxian.git`，不要向它推生产提交 |
| 工作树 | 交接前干净，`main...xiuxian-mian/main = 0/0` |
| 主服务 | `xiuxian.service` active，PID `1701920`，`NRestarts=0` |
| 防护服务 | `xiuxian-safety-watchdog.service` active，`NRestarts=0` |
| 只读健康观察 | `xiuxian-health-observer.service` active，`NRestarts=0` |
| listener sidecar | `xiuxian-listener.service` inactive/disabled；缺少独立授权 session，当前不要启动 |
| UI | `127.0.0.1:3030`，写配置必须走已认证 UI/API |
| 状态库 | `data/state/chaogu_state.db` |
| 最近全量测试 | `3674 passed, 572 subtests passed` |
| 当前健康 | `93 / warn`；只因上一场世界 Boss 的历史失败样本扣 7 分 |

交接基线采集时间：`2026-08-28 15:34 UTC+8`。

## 1. 不可破坏的维护原则

1. 默认 `passive-first`、低发送，以真实 BOT 回包、编辑消息、广播和服务器状态为准。
2. 不因超时盲目补发高风险命令；先按命令消息 ID 回捞消息日志并走原 handler/reducer。
3. MiniApp 正常限流上限按全局 `90 requests/min` 约束；429 后共享退避，不并发重试。
4. 配置写入走认证 UI/API，不用脱离生产身份上下文的 Python 进程直接改 SQLite。
5. 不重放已关闭的世界 Boss、斗法、资源消费或一次性票据请求。
6. 双群都监听，但只向一个有权威回包的路由发送，禁止两个群同时补发同一业务。
7. 深度闭关不消费、不削弱、也不阻断天星推命/改命效果，不要重新引入该错误假设。
8. `/opt/xiuxian`、`xiuxian-c` 和其他 checkout 不是当前生产目标，除非用户明确指定，否则不要碰。

## 2. 生产架构与入口

- `xiuxian.py`：主进程入口。
- `model/runtime.py`：Telegram 监听、发送、回包路由和运行控制。
- `model/state.py` / `model/persistence.py`：内存状态、SQLite 持久化与迁移。
- `model/features/`：业务模块；世界 Boss 当前核心为 `world_boss.py` 和 `world_boss_miniapp.py`。
- `model/ui.py`：本地 UI/API；前端资源在 `model/web/`。
- `tools/health_observer.py`：只读健康审计，不发送命令、不重启服务。
- `data/messages/miniapp-captures/`：脱敏 MiniApp HTTP 证据。
- `data/state/health_observer/latest.json`：最新健康快照。
- `data/state/backups/`：上线前状态库备份。

生产代码不热重载。运行代码变更经过测试后必须显式重启 `xiuxian.service`；不要连带重启 watchdog/observer，除非它们自己的代码发生变化。

## 3. 当前运行事实

### 3.1 总体

- `global_enabled=true`。
- safety watchdog 未熔断。
- health observer 没有模块级 error/warn，当前总分 `93`。
- 当前健康扣分来自旧世界 Boss 场次：4 个身份均为 `boss_window_expired`；这是历史证据，不代表新代码已经再次失败。
- 频道身份 cohort 有 19 个身份因 `SendAsPeerInvalidError` 冻结，health 中仅作 INFO。轮询仍在运行；不要用批量主动命令测试恢复。
- 洞府公共入口当前有 3 个动态入口，未被 token circuit 阻断，身份间隔为 20 秒。

### 3.2 双群路由

| 角色 | 群 ID | topic |
|---|---:|---:|
| preferred send | `-1002083016447` | `0` |
| alternate send | `-1001680975844` | `7310786` |

两个群均监听。当前几个登录账户最近的 BOT 回复仍可能来自旧群；发送选路必须以实时 BOT 活跃与回包证据为准，不能把“主群”理解成永远固定发送目标。

### 3.3 CommandAttempt

CommandAttempt 仍是 Gate 0-3 影子事实账本，没有接管 reducer、调度、恢复或补发：

```text
XIUXIAN_ATTEMPT_SHADOW_WRITE=1
XIUXIAN_ATTEMPT_SHADOW_BIND=1
XIUXIAN_ATTEMPT_RECOVER_REPORT_ONLY=0
XIUXIAN_ATTEMPT_CONTROL_MODULES=
XIUXIAN_ATTEMPT_CONTROL_IDENTITIES=
```

交接时账本：`20846` attempts；`sent=17047`、`blocked=3772`、`send_unknown=25`、`queued=2`；`resend_count` 总计 `0`；business 全部保持 `open`，这是当前影子设计而非业务完成状态。Gate 4 仍未批准，禁止据此自动补发、闭账、归档或删除。

## 4. 世界 Boss 当前状态

### 4.1 生产配置

- 自动化开启。
- 登录账户上限：`1`。
- 唯一启用身份：`Wwlafe[缘初子]`，identity/account `8613500668`。
- 轮换账户：仅 `8613500668`。
- 轮换目标：`斩青元者`。
- 已排除当前其他候选：`301299112`、`3504367852`、`7538826434`、`8659059191`。
- 当前策略是只让一个尚未拿过第一的身份参与，不能在未授权时恢复四账号并行。

### 4.2 8 月 28 日失败根因

旧场次 `2026-08-28:12012225` 并非已证实的 IP 限制：四个身份的入场、WebSocket ticket、battle start/begin 均成功；本地随后为每个身份连续揭示 18 个窗口，约 88 秒后才统一申请 `charge-start`，服务器返回 HTTP 409 `boss_window_expired`。

修复提交 `ba1760fb` 将动态协议改成：

```text
start -> begin -> window -> charge-start -> hit
                         -> window -> charge-start -> hit
                         -> ... -> finish
```

即每次只揭示一个窗口，完成该次蓄势与命中后再取下一个窗口。请求票据仍为一次性，capture 与日志必须持续脱敏 `chargeTicket`、token 和 initData。

### 4.3 尚未完成的生产验收

`ba1760fb` 部署后还没有新的自然 Boss 场次。最新 capture 仍停在 `2026-08-28 13:42:41 UTC+8` 的旧失败，因此本项状态是“代码、测试、部署完成，待下一场真实事件验收”，不能提前销号。

下一场只核对自然事件，不手动重放旧场：

1. 自动入场身份必须只有 `8613500668`。
2. capture 顺序必须呈现逐窗口 `window -> charge_start -> hit`，不能先出现一串 window。
3. `chargeTicket` 存在但不泄漏明文。
4. `accepted_hit_count > 0`，并记录 `accepted_perfect_count` 与伤害。
5. 不得出现 `boss_window_expired`、`boss_charge_not_ready`、`boss_charge_ticket_invalid`、`boss_hit_outside_window` 风暴。
6. 最终只能调用一次 `/finish`，不得补打关闭场次。
7. 全局 MiniApp 请求速率不得超过 90/min。

证据文件：`data/messages/miniapp-captures/world_boss-2026-08-28.jsonl`。旧场共有 110 条 capture，禁止拿它作为新代码成功样本。

## 5. 最近已上线改动

| Commit | 内容 |
|---|---|
| `ba1760fb` | 世界 Boss 动态窗口改为逐次揭示和命中 |
| `35b4a881` | MiniApp 共享限流退避与天机试炼 partial 分类 |
| `ca2f6136` | 共享 429 后停止 fallback |
| `757ad99d` | 洞府公共入口共享限流暂停 |
| `0149af73` | 世界 Boss 动态窗口揭示节奏 |
| `20e32113` | 健康观察忽略临时频道历史读取失败 |
| `74ce2e03` | 世界 Boss charge ticket 脱敏 |
| `bd0886d6` | 世界 Boss 动态票据协议 |
| `bbe6f635` | 天机命脉每日聚合通报 |
| `6d261687` | 天机命脉衔接静室/深度闭关 |

## 6. 测试和上线流程

世界 Boss 聚焦回归：

```bash
cd /opt/xiuxian-main
.venv/bin/python -m pytest -q \
  tests/test_world_boss_miniapp.py \
  tests/test_world_boss_miniapp_runtime.py \
  tests/test_world_boss.py \
  tests/test_miniapp_entry_probe.py
# 交接前结果：234 passed
```

全量回归：

```bash
.venv/bin/python -m pytest -q
# 交接前结果：3674 passed, 572 subtests passed
```

生产部署顺序：

1. `git status --short -b`，确认没有混入他人未提交改动。
2. 用 SQLite 在线 `.backup` 生成备份并执行 `pragma integrity_check;`。
3. 跑聚焦测试、全量测试和 `git diff --check`。
4. 提交到 `main`，推送 `git push xiuxian-mian main`，不要推 `origin`。
5. 运行代码变化后仅执行 `systemctl restart xiuxian.service`。
6. 核对主服务、watchdog、health observer 均 active，主服务 `NRestarts=0`。
7. 检查重启后的 journal、health 快照和真实业务状态；不能只看进程存活。

最近可用备份：

- `data/state/backups/chaogu_state.pre-world-boss-single-identity-20260828-145641.db`
- `data/state/backups/chaogu_state.pre-closeout-trial-rate-20260828-0836.db`

两份在交接时均通过 `pragma integrity_check`。

## 7. 接手者第一小时

```bash
cd /opt/xiuxian-main
git status --short -b
git rev-list --left-right --count main...xiuxian-mian/main

systemctl is-active \
  xiuxian.service \
  xiuxian-safety-watchdog.service \
  xiuxian-health-observer.service \
  xiuxian-listener.service
systemctl show xiuxian.service -p MainPID -p NRestarts -p ExecMainStartTimestamp

journalctl -u xiuxian.service -f -n 0 --no-pager -o short-iso
```

另开终端只读查看：

```bash
jq '{ts,status,health,reasons,safety}' data/state/health_observer/latest.json
journalctl -u xiuxian.service --since '-30 min' --no-pager -o short-iso \
  | rg -i 'traceback|exception|error|failed|失败|world.?boss|真仙|safety|fused'
```

如果看到世界 Boss 新场次，优先保存并检查新 capture，不要先补丁、补发或手动重试。

## 8. 当前未决项与优先级

### P0：下一场世界 Boss 真实验收

按 §4.3 验证单身份与逐窗口协议。若仍失败，先按 capture 定位具体 endpoint、HTTP 状态和业务 error，再决定是否是 IP、票据、时钟或协议问题。

### P1：频道身份冻结观察

当前 19 个频道身份被冻结，但目标群 membership 只读校准为 member。继续观察自动探测与真实回包；不要用批量发言制造恢复证据。MiniApp 公共入口与群命令通道是共存关系，频道命令冻结不等于 MiniApp 必须全局暂停。

### P2：CommandAttempt 影子债务

保留 `queued=2`、`send_unknown=25` 和空 command family 等历史事实；它们是未来报告型 Gate 4 评审材料，不是当前自动恢复授权。

### P2：listener sidecar

sidecar 缺少独立授权 session，已明确 inactive/disabled。除非完成独立账号授权并重新审计，不要复制主 session，也不要为了消除告警强行启动。

## 9. 历史文档使用顺序

1. 当前生产交接：本文件。
2. CommandAttempt 决策：`docs/audit/ADR-20260712-command-attempt-conditional-approval.md`、`docs/audit/command-attempt-72h-closeout-20260715.md`。
3. 世界 Boss 历史：`docs/audit/world-boss-miniapp-closure-repair-20260728.md`，其 7 月协议与多账号配置已被本文件 §4 覆盖。
4. MiniApp 历史：`docs/miniapp_aux_fix_handoff_20260708.md`、`docs/cave_duel_aux_handoff_20260709.md`，只用于追溯设计，不作为当前配置事实。

## 10. 一句话交接

生产仓库已同步并保持干净，主服务/watchdog/observer 正常；当前唯一重要未验收项是 `ba1760fb` 的世界 Boss 逐窗口协议，生产只启用 `Wwlafe[8613500668]` 一个尚未拿过第一的轮换身份。新维护者应接管只读监测，等待下一场自然 Boss，以脱敏 capture 验收，禁止重放旧场、恢复四账号并行或开启 CommandAttempt Gate 4。
