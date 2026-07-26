# 审计整改 Lab 交付报告

更新时间：2026-07-26 CST

基线：`git HEAD = 171fda75`（2026-07-25 09:30）
来源：`/root/xiuxian-main-audit-20260726.md` 深度审计报告

## 边界

本轮完成代码整改 + lab 验证，**尚未重启 `xiuxian.service`**。生产进程仍运行 04:48 加载的旧代码。

本轮未执行：

- 未重启 `xiuxian.service` / `xiuxian-safety-watchdog.service` / `xiuxian-health-observer.service`。
- 未打开任何模块开关，未改变任何模块的默认启用状态。
- 未新增发送面，未新增 live API 读取，未扩大 MiniApp 动作白名单。
- 未设置 `XIUXIAN_ALLOW_LIVE_TEST_DB=1`，未让测试指向 live DB。
- 未修改状态机语义、调度顺序、handler 顺序。

已执行的运维动作（不依赖重启即生效，且可逆性已确认）：

- `chmod 600 data/session/*.session*`（原有两个账号为 0644）。
- 清理 82 个过期 `chaogu_state` 快照（保留最新 20 个，用户确认保留策略）。
- 收编孤儿 preflight 循环为 systemd timer，停掉原 PID 1437284。
- 删除 miniweb 一个完全重复的 5.8G 遗留副本（另一份含独有早期数据，已保留）。

## 变更清单

26 个文件改动，净 `+750 / -1023`；新增 4 个文件。

### H1 收尾钓鱼重构（工作区半成品）

上一轮遗留的未提交重构（废文本钓鱼、改 MiniApp 唯一出口）导致 14 个测试红。经 `git stash` 对照确认全部由该改动引入。

- `tools/ui_http_smoke.py`：`auto_probe_enabled` 断言改为 `not in fishing`。前端与 `ui.py` 已移除该字段，只有 smoke 未同步；重构者在 `test_fishing_ui.py` 写的 `assertNotIn("auto_probe_enabled", fishing)` 佐证了这是预期契约。
- `tests/test_fishing_runtime.py`：
  - 2 个排队测试改写为断言"多身份并发下不发文本命令"，并断言 `_defer_new_fishing_for_capacity` / `FISHING_MAX_ACTIVE_IDENTITIES` 已移除。
  - 6 个纯发送测试（日切重竿、准备窗口买饵、窝料预打、重复抑制）合并为一个参数化契约测试 `test_scheduler_never_sends_text_commands_across_legacy_scenarios`，保留 5 个场景覆盖。
  - 3 个回复处理测试只修正失效的尾部断言（`pending_action` / `status_msg_id` / 退避时长），核心校准逻辑断言全部保留。
- `tests/test_startup_recovery_guards.py`：同步文案变更。

**核实但未改**：`run_fishing_scheduler` 被掏空**不是缺陷**。`next_fishing_time` 的推进职责已由 MiniApp 结果路径（`fishing_runtime.py:1001-1093`）接管，三个前置函数均为廉价内存检查会正确早退。

### M1 MiniApp 传输层收口（代码质量 + 运行效率）

新增 `model/features/miniapp_common.py`：

- `build_miniapp_transport(timeout, session, proxies)` —— 统一 7 份复制粘贴后各自漂移的 `_requests_transport`（MD5 各不相同）。
- `append_http_event()` —— 统一 6 份同构实现，与原实现字节兼容（相同键、相同顺序、`error` 一律过 `sanitize_webapp_secret_text`）。

**刻意不统一 `_flow_result`**：其各模块变体差异是实质性的（fishing 带 `_active_token`，trial / world_boss 带 `proof`，status 兜底值 `""` vs `"failed"`），强行合并是拿真实回归风险换表面收益。已在 `miniapp_common.py` 注释说明。

`cave_treasure_miniapp.py` 新增 `_flow_transport()`，为 9 个 production flow 各建一个 `requests.Session`：

> 依据：近 7 天 9605 条捕获记录显示 cave_treasure 独占 7862 次请求 / 6.2 小时 / 均值 2847ms（占 MiniApp 网络时间 94%），`POST start` 一项 4881 次 / 4.7 小时。已核实 430 次 start 返回 429 种不同响应——不是无效重复拉取，优化点在省握手而非少调。此前只有 `world_boss_miniapp_runtime.py:84` 复用连接。

同时清理 5 个模块中已无用的 `import requests` 和 `TG_REQUESTS_PROXIES`。

### M2 capture 体积治理（运维成本）

`model/webapp_core.py`：

- `summarize_miniapp_json_shape()` 新增 `max_keys` 宽度上限（`MINIAPP_CAPTURE_SHAPE_MAX_KEYS = 12`），超出部分记为 `keys_truncated` 计数。原实现限深度不限宽度，数组有 `list_sample_limit` 采样而对象无条件全展开。
- `MINIAPP_CAPTURE_SHAPE_MAX_DEPTH` 4 → 2，`LIST_SAMPLE_LIMIT` 2 → 1。深层细节由既有 `body_digest` 兜底。
- `MiniAppCaptureStore` 新增 `retention_days`（默认 7），在 `append` 时清理同 adapter 的过期日分片。实现在基类而非 7 个调用点，且永不抛异常。

实测（真实洞府 start 样本）：`body_shape` 22334 → 2924 字节（-86.9%），单条记录 23610 → 约 4200 字节（**-82%**）。目录 248MB 预计降至约 44MB，叠加 7 天保留后进一步收敛。

### M3 并发丢失更新（正确性）

- `model/features/explore_rift.py:_request_unknown_rift_panel`：await 后重读 `tianxing_observation`。被动侧写入者为 `_handle_routed_reply_event`（`app.py:2787`，Telethon 回调）→ `apply_tianxing_passive`（`app.py:2848`）。
- `model/features/hehuan.py:run_hehuan_scheduler`：**真正的漏洞点是提醒回写那一步**（原 1315 行 `state["hehuan_observation"] = observed`），而非下游写回。改为重读最新快照后只并回提醒流程自己修改的字段（已核实其唯一写入 `valuable_drop_reminders`）。被动侧写入者为 `run_retry_scheduler`（`runtime.py:4614`）→ `_recover_module_managed_timeout_state:2668` → `reconcile_hehuan_timeout_from_pending`。
- `model/features/hehuan.py:_ensure_hehuan_reply_anchor`：同样改为合并回最新快照，并保持调用方持有的入参对象同步。

**并发性已证**：`run_hehuan_scheduler` 跑在后台身份调度 task（`app.py:2777` `asyncio.create_task`），`run_retry_scheduler` 跑在 `main_loop`（`app.py:3656`）——代码里那句「身份调度仍在等待发送队列，**主循环未阻塞**」正是该设计的自白。

两个新增回归测试均通过**有效性验证**：临时回退修复后测试失败，恢复后通过。

### L3 影子持久化可观测性

`model/persistence_shadow.py`：`note_error()` 新增 `reason` 参数，三个 `safe_*` 包装器传入 `_error_reason(exc)`。原实现只累加 `telemetry_error_count`，失败原因完全丢失。原因列表去重且上界 5 条，不改变 fail-safe 语义。

（`persistence_shadow.py` 那 4 处 `except: pass` 是**嵌套内层**，外层已调 `note_error`——真正被吞的是外层异常原因，已修。）

### 运维加固

- `deploy/xiuxian.service`、`deploy/xiuxian-listener.service` 及线上同名 unit：新增 `UMask=0077`，根治 Telethon 新建 session 落成 0644 的问题（线上模板此前与 deploy 模板已有漂移，一并对齐）。
- 新增 `deploy/xiuxian-defensive-preflight.{service,timer}`：收编手工 `while true; sleep 60` 循环，输出改走 journald（自动轮转）。
- 新增 `tools/prune_state_snapshots.py`：快照保留工具，默认 dry-run，按文件名时间戳排序，自动带上 `-shm`/`-wal` 伴生文件，永不触碰活动库。
- `xiuxian-mini-web-backup.service`：新增 `MemoryMax=4G` / `MemoryHigh=3G` / `TimeoutStartSec=45min` / `OnFailure=`；新增 `xiuxian-mini-web-backup-alert.service`（无外部依赖，落盘 breadcrumb + err 级 journal）。告警通道已实测触发。

## Lab 验证结果

按 README「测试和发布前检查」四项：

```
.venv/bin/python -m pytest -q
  → 3294 passed, 412 subtests passed        （审计前基线 3272，净增 22）

.venv/bin/python -m py_compile xiuxian.py model/*.py model/features/*.py tools/*.py
  → 全部通过

.venv/bin/python tools/ui_http_smoke.py
  → result=ok checks=16                     （修复前 15/16）

.venv/bin/python tools/safety_watchdog.py --once --dry-run
  → watchdog ok
```

补充验证：

```
8 个 MiniApp flow plan 经 validate_miniapp_flow_plan 全部 OK
6 个模块 _requests_transport 均可调用
cave_treasure _flow_transport 确认每次返回独立 session
```

新增测试 22 个，覆盖：形状宽度上限与截断标记、capture 保留（含不跨 adapter 误删、非 jsonl 不受影响、可禁用）、共享 transport 的 session 复用与 header 优先级、`append_http_event` 脱敏与 schema、两处并发丢失更新、影子持久化错误原因（含去重、上界、缺省容错）。

## 磁盘回收

| 项 | 变化 |
|---|---|
| `data/state/` 快照 | 417MB → 313MB（删 82 个，保留最新 20） |
| `data/monitoring/` | 40MB → 17MB（24MB 孤儿日志归档为 11KB 尾部） |
| miniweb 遗留副本 | 删除 1 个重复的 5.8G（保留含独有早期数据的另一份） |
| 根分区 | 57G 可用 → 62G 可用 |
| `miniapp_capture/` | 248MB，将随新保留策略自然收敛至约 44MB |

## 上线步骤（待执行）

```bash
systemctl restart xiuxian.service

# README 规定的上线后确认
systemctl is-active xiuxian.service
sqlite3 data/state/chaogu_state.db 'select count(*) from pending_tasks;'
journalctl -u xiuxian.service --since "10 min ago" \
  -g 'Traceback|ERROR|Exception|NameError|TypeError|KeyError|ValueError|UnboundLocalError' --no-pager
```

重启后额外观察（本轮改动的针对性验证）：

- `data/state/miniapp_capture/` 当日分片单条记录应降至约 4KB（`wc -c` 除以 `wc -l`）。
- 洞府流程 `elapsed_ms` 应较此前 2847ms 均值下降（连接复用生效）。
- 7 天前的 capture 分片应在首次 MiniApp 请求后被清理。
- 新建的 session 文件权限应为 0600。

## 未纳入本轮的事项

- **listener sidecar 授权**：4 个账号的 listener session 未独立授权，需本人交互登录，无法自动化。在此之前 health observer 每 15 分钟一条 inactive 告警仍会持续。
- **miniweb 库 retention**：库已 7.4G，`parsed_cards` 2645MB / `raw_messages` 1066MB / `parsed_card_channels` 827MB 带 1285MB 索引。文件名 `before-retention` 说明曾做过 retention 后来停了。这属于 miniweb 项目范围，未在本轮改动。
- **miniweb 活动库最新消息停在 2026-07-09**（文件仍在被写）——审计中发现，未深究，建议单独排查。
- **MiniApp 形状按 `(adapter, step)` 去重**（只在变化时记录）：M2 的进一步优化，本轮的宽度/深度上限已拿到主要收益，可延后。
