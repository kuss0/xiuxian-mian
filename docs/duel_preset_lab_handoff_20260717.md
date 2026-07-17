# 斗法角色预设 Lab 交接（2026-07-17）

> **给主 AI / 上线审核用**。本 lab **未碰生产**：无 prod 写库、无服务重启、无 `/opt/xiuxian-main` 代码改动。

---

## 0. 主 AI 接手清单（先看这里）

| 项 | 内容 |
|---|---|
| Worktree | `/root/xiuxian-main-duel-preset-lab-20260717` |
| Branch | `lab/duel-preset-20260717` |
| Base HEAD | `cd2a2e64`（fix wanxin commission recovery and gift UI refresh） |
| 提交状态 | 主线已完成代码审计与全量回归；本文件随验收提交一起进入 branch |
| 生产路径 | `/opt/xiuxian-main` — **干净、未改** |
| 验证（主线验收） | `3152 passed, 396 subtests`；斗法/相位/天星聚焦 `412 passed, 13 subtests` |
| 范围 | 斗法门槛 / 修为保留 UI / 角色预设 / 可配执行窗 / 容量提示 / 分组可视化 / 相位结算中间态修复 |
| **不在范围** | Gate4 双发、上游 `.对决`/`.决斗` 协议、批量开 `duel_route_enabled`、prod 部署 |
| 天星契约 | **对齐原版**：默认不绑 `duel_route`；预设不碰天星；关模块才 `cancel_duel_tianxing_route` |

**立刻可跑：**

```bash
cd /root/xiuxian-main-duel-preset-lab-20260717
/opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_duel.py tests/test_phaseful_summaries.py tests/test_tianxing.py tests/test_ui_dual_track.py
# 主线验收：412 passed, 13 subtests passed
```

**稳妥上线顺序（推荐）：**

1. Review + merge **本 worktree 改动**（不要从 lab 直接写 `/opt`）。
2. 上线前再跑上述 pytest。
3. 启动后 DB 自动 `ALTER` 三列（见 §7）；**无需手工刷数**。
4. **默认全天窗**，不要批量收窄时间窗。
5. **不要**因本 lab 批量打开 `tianxing_auto_config.duel_route_enabled`。
6. 预设优先 UI 单身份「套用」；批量 `apply_duel_presets_for_all_identities(force=True)` 仅维护窗。
7. 容量红字 = 提示 only，不当硬闸。

---

## 1. Lab 位置

| 项 | 值 |
|---|---|
| Worktree | `/root/xiuxian-main-duel-preset-lab-20260717` |
| Branch | `lab/duel-preset-20260717` |
| Base HEAD | `cd2a2e64` |
| 生产路径 | `/opt/xiuxian-main`（旧门槛：`元婴后期` + 修为 66 万；**无本 lab 改动**） |

## 2. 需求对齐（已实现）

| 规则 | 实现 |
|---|---|
| 可打境界 | **结丹后期** 可打；**元婴须元婴后期+**（元婴初/中期拦） |
| 修为门槛 | 默认保留 **20 万**（`DUEL_RESERVE_XIUWEI`）；**UI 可按身份改**；不再叠加单场风险 6 万 |
| 元婴预设 | 目标固定 `@ccahen`，次数 **10** |
| 结丹预设 | 默认开，次数 **10**，目标 **均分** 打当前元婴号池（稳定 round-robin） |
| 吧唧 / WA | 预设 **默认关**（identity / username / label 识别） |
| 操作入口 | UI「套用角色预设」+ 手动打开斗法且配置为空时自动套预设 |
| **执行时间窗** | 按身份可配；默认全天 `00:00-23:59`；窗外不发起新斗法 |
| **日容量预检** | 粗算能否排下；**仅提示、不拦截** |
| **预设/分组可视化** | `duel_preset_plan.groups` + 每身份 `duel_preset_preview` |

> 上游参考（只读）：wxjerry `global_duel` / `group_duel`（`.对决`/`.决斗` 语义不同，**只吸收窗口/容量/分组思路**，不改命令协议）。

## 3. 改动文件

```
model/features/duel.py                 # 门槛 + 预设 + 修为保留 + 时间窗 + 容量 + groups + 天星稳妥
model/features/_phaseful.py             # 斗法根消息先收到结算时不即时补发第二场
model/control.py                       # 手动 enable 空配置 → 套预设
model/ui.py                            # snapshot/API：window/capacity/preset_plan + duel-config/preset-apply
model/state.py                         # duel_reserve_xiuwei + duel_window_{start,end}_minute
model/persistence.py                   # DB 列迁移 ALTER + CREATE
model/web/static/js/module_cards_ui.js # 修为保留 / 时间窗 / 容量(仅预估) / 预设预览 / 同目标负载
model/web/static/css/ui_fixes.css      # hint / actions 样式
tests/test_duel.py                     # 门槛 + 预设 + 保留 + 窗口 + 容量 + 天星稳妥
tests/test_phaseful_summaries.py        # 结算中间态不触发斗法即时补发
tests/test_ui_dual_track.py            # 前端契约
docs/duel_preset_lab_handoff_20260717.md
```

`git diff --stat`（相对 base，约）：

```
9 files changed, ~1295 insertions, ~27 deletions
+ docs/duel_preset_lab_handoff_20260717.md (untracked)
```

## 4. 核心 API（`model/features/duel.py`）

### 境界 / 修为 gate

- `_realm_gate_reason(realm)`：结丹后期 **或** ≥ 元婴后期 才通过；元婴初/中期明确报错。
- `_profile_gate_reason()`：境界 + **生效修为保留**。

### 修为保留（UI 可配，按身份）

| 项 | 说明 |
|---|---|
| 模块默认 | `DUEL_RESERVE_XIUWEI = 200_000` |
| 状态字段 | `state["duel_reserve_xiuwei"]` |
| `0` / 未配 | **回落默认 20 万**（不是「0 保留可打」） |
| 读 | `get_duel_reserve_xiuwei()` / `get_duel_min_xiuwei()` |
| 写 | `apply_duel_config(..., reserve_xiuwei=...)`；空串 → 落库 0 = 跟随默认 |
| 上限 | `DUEL_MAX_CONFIG_RESERVE_XIUWEI = 100_000_000` |
| DB | `duel_reserve_xiuwei INTEGER DEFAULT 0`（ALTER 自迁移） |

### 执行时间窗

| 项 | 说明 |
|---|---|
| 默认 | start=`0`，end=`1439`（全天）→ **未收窄 ≈ 原版随时可打** |
| 状态 | `duel_window_start_minute` / `duel_window_end_minute` |
| 判定 | `is_within_duel_exec_window` / `next_duel_exec_window_open` |
| 调度 | 窗外不发新斗法；改期到 `open_at`；**进入 lead 仍可提前备天星** |
| 次日批次 | `_next_daily_duel_time` 落在窗口起点附近 + 身份散列 |
| DB | 两列 INTEGER，默认 0 / 1439 |

### 日容量预检（**纯提示**）

- `estimate_duel_capacity(...)` → `ok / self_max / target_max / reason`
- 只按斗法 CD 粗算，**不含**天星推命耗时
- **不拦截**保存、不拦截发送

### 角色预设 + 分组

- `plan_duel_presets`：纯规划；含 `rows` / `groups` / `target_hits` / 每行 `capacity`
- `apply_duel_preset_row`：**只写斗法**开关/目标/次数；**不碰** `duel_route_enabled`、不 `cancel` 推命
- `apply_duel_presets_for_all_identities`：批量工具路径（lab 不自动跑）
- 排除：吧唧 / WA（id / username / label）

```python
DUEL_PRESET_EXCLUDED_IDENTITY_IDS = {301299112, 8659059191}
DUEL_PRESET_EXCLUDED_USERNAMES = {"jfdffdddd", "jfdffdddd1", "walterwa2000", "wa2000"}
DUEL_PRESET_EXCLUDED_LABELS = {"吧唧", "wa2000", "walterwa2000"}
```

### 天星×斗法（原版语义 + 稳妥补丁）

| 行为 | 说明 |
|---|---|
| 原版保留 | `_prepare_duel_tianxing_route` / future-due 提前备 / 发送前 preflight / 关模块 `cancel_duel_tianxing_route` |
| 默认 | `duel_route_enabled=False` → 不插推命时间线，可直接 `.斗法`（非天星/未开模块同理放行） |
| 预设 | 绝不改 `tianxing_auto_config`；排除预设关斗法 ≠ 关模块 cancel |
| 窗外 | 改期 `open_at` 后，lead 内仍 `prepare(due=open_at)`，本 tick 不发送 |

### UI / API

- 面板：修为保留、窗口起止、容量（仅预估不拦截）、预设预览、同目标负载、保存/套用
- `POST /api/duel-config`：`target` / `total_count` / `reserve_xiuwei` / `window_*_minute` / `reset_progress`
- `POST /api/duel-preset-apply`：`{ send_as_id, force=true }`
- Snapshot：身份级 `duel_*` + 顶层 `duel_preset_plan`

## 5. 测试

```bash
cd /root/xiuxian-main-duel-preset-lab-20260717
/opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_duel.py tests/test_phaseful_summaries.py tests/test_tianxing.py tests/test_ui_dual_track.py
# 主线验收：412 passed, 13 subtests passed

/opt/xiuxian-main/.venv/bin/python -m pytest -q
# 主线验收：3152 passed, 396 subtests passed
```

另：`py_compile`（duel/control/ui/state/persistence）、`node --check module_cards_ui.js`。

覆盖要点：

- 境界 / 修为保留 gate；保留默认与 UI 覆盖
- 时间窗 bounds / 容量粗算 / 配置写窗
- 预设均分 + 吧唧/WA 关 + groups/target_hits
- **预设不改 `duel_route_enabled`；排除预设不 cancel 推命**
- **窗外不发送；lead 内可提前备天星**
- 原版天星：提前备 / 默认不插路线 / 关模块 cancel
- UI 契约：`window_*` / `仅预估不拦截` / 预设预览
- 真实乱序：同一斗法根消息先回 `【元婴闭关结算】`，再回锁定/战报，只计一场且不即时补发

## 6. 稳妥策略（对齐原版天星）

| 原则 | 落地 |
|---|---|
| 默认 ≈ 原版 | 全天窗未收窄时不改发送节奏 |
| 天星线默认关 | 预设/斗法配置不开关 `duel_route_enabled` |
| 关模块才拆线 | 仅 control 关斗法 → `cancel_duel_tianxing_route` |
| 容量不拦截 | 提示 only |
| 窗外可备天星 | 改期后 lead 内 prepare，不发命令 |
| 容量不含推命 | 开了 duel_route 时数字可能偏乐观 |

## 7. 主 AI 上线步骤（本 lab 不做）

1. **只 merge 本 branch/worktree**；禁止 lab 直写 `/opt` 或 `systemctl restart`。
2. 上线前再跑 §5 pytest。
3. DB 启动自迁移：
   - `duel_reserve_xiuwei` DEFAULT 0（= 用 20 万）
   - `duel_window_start_minute` DEFAULT 0
   - `duel_window_end_minute` DEFAULT 1439
4. 配置：见 §0 / §6；**勿批量收窄窗、勿批量开 duel_route**。
5. 观察：gate、保留、预设目标、天星号未开 route 时与原版一致。
6. **Gate 4 / 双发** 不在本 lab。

## 8. 生产未触碰自检（交接时）

| 检查 | 结果 |
|---|---|
| lab path ≠ `/opt/xiuxian-main` | ✅ |
| prod `duel.py` md5 `56fa4ee2…` ≠ lab `c787432d…` | ✅ |
| prod `git status` | ✅ `main...xiuxian-mian/main` 干净 |
| 未 restart / 写 prod 库 | ✅ |
| 全部改动仅 lab worktree | ✅ 未 commit |

## 9. 风险与边界

- 结丹带仅 **结丹后期**；元婴池空 → 结丹预设关。
- `duel_reserve_xiuwei=0` = 跟随默认 20 万，不是零门槛。
- 预设 `force=False` 不覆盖已启用自定义；UI 套用默认 `force=true`。
- 批量 apply 会 `reset_progress`。
- 吧唧/WA 事后可手动再开，预设不强制二次关掉。
- 容量 / 时间窗 / 预设 **不替代** 天星 preflight；高风险天星动作仍走原模块。
- **不吸收** `.对决`/`.决斗` 命令语义。

## 10. 一句话

Lab 已完成并通过主线验收：**结丹后期 + 元婴后期、默认 20 万可配保留、角色预设（元婴@ccahen×10 / 结丹均分×10 / 吧唧WA默认关）、默认全天可配执行窗、容量仅提示、预设不碰天星、窗外改期可提前备天星、相位结算不再终结或即时补发斗法**；全量 **3152 passed**。生产部署仍须按 §0/§6 做上线前检查。
