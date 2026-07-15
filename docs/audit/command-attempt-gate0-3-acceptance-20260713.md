# CommandAttempt Gate 0–3 Acceptance

- **Verdict: PASS (Gate 0–3 shadow only)**
- **Gate 4+: NOT APPROVED** (separate ADR required)
- Date: 2026-07-13 09:04 UTC+8
- Accepting party: Grok (independent acceptance; implementer self-check is not sufficient)
- Scope: `/opt/xiuxian-main` production shadow write + strict shadow bind
- Governing ADR: `docs/audit/ADR-20260712-command-attempt-conditional-approval.md`
- Gate 0 spec: `docs/audit/command-attempt-gate0-spec.md`
- Observation log: `docs/audit/command-attempt-shadow-observation-20260712.md`

## Decision Summary

CommandAttempt **Gate 0–3** is accepted for continued production **shadow** operation:

| Gate | Scope | Result |
|------|--------|--------|
| Gate 0 | Spec / invariants / flags / retention | **PASS** (docs + code present) |
| Gate 1 | Store / ledger / idempotency | **PASS** (schema live; tests green) |
| Gate 2 | Shadow send lifecycle | **PASS** (≥24h live; parity 100%) |
| Gate 3 | Shadow evidence binding | **PASS** (written bindings 100% strong) |
| Gate 4+ | Recovery / reducer / module control | **NOT APPROVED** |

This acceptance does **not** authorize:

- send/resend or cooldown control;
- reducer or scheduler takeover;
- recovery automation (including report-only recovery driving actions);
- non-empty `XIUXIAN_ATTEMPT_CONTROL_MODULES` / `CONTROL_IDENTITIES`;
- archive/delete jobs beyond report-only retention policy.

## Live Evidence (acceptance snapshot)

Checkpoint tool: `tools/attempt_shadow_checkpoint.py`
Report: `data/state/command_attempt_checkpoints/checkpoint-20260713-090453.json`
Status: **`ok`** (reasons: empty)

| Metric | Value |
|--------|--------|
| Observation window | **30.997 h** (start 2026-07-12 02:05:04 UTC+8) |
| Attempt rows | 1744 |
| Transport | sent/open **1698**; blocked/open **46** |
| `send_unknown` | **0** |
| Stale created/queued/sending >300s | **0** |
| `last_error` rows | **0** |
| Sent-log root parity | **1698 / 1698** (missing 0) |
| Evidence rows | 2174 |
| Bind reason | **2174 × `exact_reply_to_root`** |
| Bind anchor | **2174 × `reply_to_msg_id`** |
| Non-strong written bindings | **0** |
| Orphan evidence | **0** |
| Blocked `definitely_unsent` | **46 / 46** |
| Rate | ~56.3 attempts/h |
| 72h projection | ~4051 attempts; ~0.95 MB payload; DB file ~4.8 MB |
| Secret pattern scan (intent/meta/evidence) | **0 hits** |
| Journal 24h `database is locked` | **0** |
| Journal 24h CommandAttempt failure strings | **0** |

### Feature flags (unit + live process)

From `xiuxian.service.d/command-attempt-shadow.conf` and `/proc/<main>/environ`:

- `XIUXIAN_ATTEMPT_SHADOW_WRITE=1`
- `XIUXIAN_ATTEMPT_SHADOW_BIND=1`
- `XIUXIAN_ATTEMPT_RECOVER_REPORT_ONLY=0`
- `XIUXIAN_ATTEMPT_CONTROL_MODULES=` (empty)
- `XIUXIAN_ATTEMPT_CONTROL_IDENTITIES=` (empty)
- `production_control_enabled` → **False**

### Runtime / safety envelope

| Check | Result |
|-------|--------|
| `xiuxian.service` | active, NRestarts=0 (entered 2026-07-13 04:27) |
| `xiuxian-listener.service` | active |
| `xiuxian-health-observer.service` | active; latest **status=ok**, score 92 |
| `xiuxian-safety-watchdog.service` | active; fused=false |
| Shadow checkpoint timer | loaded; next ~12:17 UTC+8 |
| Attempt code on `main` | ancestors include `2d66f594`, `2635c57e`, `75d6e922` |

Health note (out of Attempt scope): listener heartbeat reports `degraded_no_connected_accounts` for four dedicated listener sessions — does **not** fail Gate 0–3 (main worker send path remains healthy; Attempt parity holds).

### Lab / unit verification at acceptance

```text
.venv/bin/python -m pytest -q \
  tests/test_command_attempt_store.py \
  tests/test_command_attempt_runtime_shadow.py \
  tests/test_command_attempt_bind.py \
  tests/test_attempt_shadow_checkpoint.py
```

Result: **31 passed**.

## Gate checklist vs ADR observation rules

| # | Criterion | Result |
|---|-----------|--------|
| 1 | Every sent Attempt maps to durable sent-log root | **PASS** 1698/1698 |
| 2 | Anchored written binding precision 100% | **PASS** all exact_reply_to_root |
| 3 | Ambiguous / guessed bindings never written | **PASS** non_strong=0 |
| 4 | No stale transport rows / continuous abnormal leak of created·queued | **PASS** stale=0 |
| 5 | No SQLite lock errors material to send path | **PASS** journal 0 |
| 6 | No secrets in Attempt payloads | **PASS** scan 0 |
| 7 | Control flags remain disabled | **PASS** lists empty, recover_report_only=0 |

Five ADR design prerequisites (consistency model, strict bind outcomes, ordering/idempotency, layered flags, retention/privacy) were specified before shadow deploy and remain satisfied for **observational** gates. Module↔Attempt dual-write consistency for **control** is deferred to Gate 4 design.

## Residual debts (accepted non-blockers)

These do **not** reverse Gate 0–3 PASS; they are follow-ups before any Gate 4 review:

1. **Empty `command_family`**: 178 rows (~10%). Dominated by keepalive/`在` (深度闭关/元婴), single-char `。` (合欢宗), and some dungeon/runtime commands. Reporting debt only.
2. **Sent without reply evidence**: ~290 sent Attempts have no evidence row. Expected for no-reply keepalives and unanchored paths; **not** a precision failure (unwritten ≠ guessed). Coverage metric for later gates.
3. **Production `send_unknown` still 0**: path covered in lab (`queued_send_timeout`); live sample still absent — continue observing, do not force.
4. **Business always `open`**: correct for shadow (no reducer consumption). Gate 4 must define terminal business projection rules before control.
5. **Observation < 72h**: 31h exceeds the **24h** minimum; window may run to 72h for capacity confidence. **No archive/delete job** until separate approval.
6. **Listener session degradation**: separate ops item; not Attempt control surface.
7. **Uncommitted `health_observer` dirty tree**: unrelated to this acceptance; land via normal PR discipline.

## Explicit non-approvals

- **Gate 4 is closed.** No pilot module (including `explore_rift`) may take Attempt-driven control under this document.
- Enabling non-empty control lists, recover-report-only **action**, or deleting legacy recovery is **No-Go** until a new ADR and dual-run comparison.
- Retention prune/archive remains **report-only** per `command-attempt-shadow-retention-20260712.md`.

## Required ongoing posture

1. Keep `SHADOW_WRITE=1` / `SHADOW_BIND=1` / control empty.
2. Keep periodic checkpoint timer; treat `status != ok` as incident.
3. Optional: complete 72h capacity observation, then freeze a 72h capacity note (still not Gate 4).
4. Gate 4 entry only after separate review covering dual consistency, resend policy, and low-risk pilot selection.

## Sign-off

| Role | Party | Outcome |
|------|--------|---------|
| Independent acceptance | Grok | **Gate 0–3 PASS** |
| Implementer | prior GPT work + commits on main | implementation under review only |
| Product / owner | human | may continue shadow; Gate 4 requires explicit re-approval |

**Bottom line:** CommandAttempt shadow ledger and strict bind are **production-accepted for observation**. They must not gain production control without a new gate review.

## Timeline / 时间节点（防遗忘）

观测起点：`2026-07-12 02:05:04 UTC+8`（首条 Attempt / observation.started_at）

| 节点 | 时刻 (UTC+8) | 含义 | 你要做什么 |
|------|----------------|------|------------|
| T0 影子上线 | **2026-07-12 02:05** | 生产 SHADOW_WRITE/BIND 开启 | 已完成 |
| T+24h 最低观测满 | **2026-07-13 02:05** | ADR 24h 门槛 | 已满足 |
| **Gate 0–3 正式验收** | **2026-07-13 09:04** | 独立验收 PASS | **已完成**（本文档） |
| 例行 checkpoint | 每天 **00:15 / 12:15**（±5min 抖动） | timer 只读汇总 | 看 `status==ok` 即可 |
| **T+72h 容量收口** | **2026-07-15 02:05** 目标；实际验收 **08:09** | 72h 观察窗结束 | **已完成 PASS** → `command-attempt-72h-closeout-20260715.md` |
| 72h 后例行 | 每天 00:15 / 12:15 | 持续影子 | 看 hard 门槛；`warn` 需解释不必恐慌 |
| Gate 4 评审 | **未排期** | 控制权单独 ADR | **默认不开**；要开必须你显式发起 |

### 建议你记的三个日子

1. **2026-07-13**：Gate 0–3 已验收（影子生产 OK）。
2. **2026-07-15 02:05 前后**：做 **72h 收口检查**（不是重新验收 Gate 4）。
3. **Gate 4**：没有日期 = **不要并控制面**；想开再单独约评审。

### 72h 收口时最少看这 7 项

```bash
cd /opt/xiuxian-main
.venv/bin/python tools/attempt_shadow_checkpoint.py
# 期望: status=ok, missing_root_count=0, non_strong=0,
#       send_unknown 可仍为 0, control 仍空
systemctl is-active xiuxian xiuxian-listener xiuxian-health-observer xiuxian-safety-watchdog
# 确认 Environment 仍无 CONTROL / recover_report_only=0
```

报告目录：`data/state/command_attempt_checkpoints/`
若 `status != ok`：当事故处理，**不要**为修数去开 Gate 4。
