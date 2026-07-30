# CommandAttempt 72h Observation Closeout

- **Verdict: PASS (72h shadow capacity + Gate 0–3 sustained)**
- **Checkpoint tool status: `warn`** (explained; does not reverse hard gates)
- **Gate 4+: still NOT APPROVED**
- Date: 2026-07-15 08:09 UTC+8
- Accepting party: Grok
- Observation start: 2026-07-12 02:05:04 UTC+8
- Window: **78.08 h** (exceeds 72h target of 2026-07-15 02:05)
- Prior acceptance: `docs/audit/command-attempt-gate0-3-acceptance-20260713.md`
- Live report: `data/state/command_attempt_checkpoints/checkpoint-20260715-080949.json`

## Decision

| Item | Result |
|------|--------|
| 72h observation window complete | **PASS** (78h) |
| Gate 0–3 hard criteria sustained | **PASS** |
| Capacity acceptable for continued shadow | **PASS** |
| Gate 4 / production control | **NOT APPROVED** |

Shadow write + strict bind may **continue in production** under the same flags.
No module control, recovery automation, or archive/delete job is authorized.

## Hard criteria (must-pass)

| Criterion | 31h (7/13) | 78h (7/15) | Result |
|-----------|------------|------------|--------|
| Sent-log root parity | 1698/1698 | **3689/3689** | **PASS** |
| Written bind precision | 2174 exact | **4911 exact_reply_to_root** | **PASS** |
| Non-strong written bindings | 0 | **0** | **PASS** |
| Control lists empty | yes | **yes** | **PASS** |
| recover_report_only | 0 | **0** | **PASS** |
| Secrets in Attempt payloads | 0 | **0** | **PASS** |
| Journal `database is locked` (72h) | — | **0** | **PASS** |
| Core services active | yes | **yes** (NRestarts=0 on main) | **PASS** |
| Focused unit tests | 31 | **31 passed** | **PASS** |

## Live snapshot

```text
status: warn
reasons: ["stale transport rows 2", "attempt errors 18"]
attempts: 4136
  sent/open:         3689
  blocked/open:       427
  send_unknown/open:   18
  queued/open:          2   ← both stale >300s
evidence: 4911 (all exact_reply_to_root / reply_to_msg_id)
rate: ~52.97 attempts/h
db_file: ~10.4 MB
payload approx: ~0.95 MB
transitions: 16187
message_log_days: 2026-07-12 … 2026-07-15
```

### Flags (unit file + live MainPID environ)

- `XIUXIAN_ATTEMPT_SHADOW_WRITE=1`
- `XIUXIAN_ATTEMPT_SHADOW_BIND=1`
- `XIUXIAN_ATTEMPT_RECOVER_REPORT_ONLY=0`
- `XIUXIAN_ATTEMPT_CONTROL_MODULES=`
- `XIUXIAN_ATTEMPT_CONTROL_IDENTITIES=`

### Runtime envelope

| Check | Result |
|-------|--------|
| xiuxian / listener / health-observer / safety-watchdog | active |
| health_observer | status=ok, score 92 |
| safety fused | false |
| attempt checkpoint timer | next ~12:19 UTC+8 |
| resend_count > 0 | 0 |
| business ≠ open | 0 (shadow-correct) |

## Why checkpoint is `warn` (not hard fail)

The checkpoint marks **any** stale transport or `last_error` as `warn`. At 72h this is **useful signal**, not a Gate 0–3 failure:

### 1. Stale `queued` × 2

| op_id | module | command | stuck since |
|-------|--------|---------|-------------|
| `attempt:5f4049d8-…` | 天星宗 | `.观命` | 2026-07-14 02:27 |
| `attempt:7b44afba-…` | 深度闭关 | `.深度闭关` | 2026-07-14 02:49 |

Both stopped after `created → queued` with **no terminal transport transition**.
Shadow correctly retained them; production send path did not get Attempt-driven resend (`resend_count=0`).
**Follow-up (non-blocking):** optional report-only reconciler or abandoned-on-process-exit for orphaned `queued` — still **not** Gate 4 control.

### 2. `last_error` × 18 == all `send_unknown`

| last_error | n |
|------------|---|
| `You can't send messages as the specified peer (SendMessageRequest)` | 17 |
| `scope_exit_without_terminal` | 1 |

These are **real Telegram/runtime outcomes recorded on the ledger**. At Gate 0–3 accept (31h) production had zero `send_unknown`; the 72h window **filled that sample gap**.
`definitely_unsent=0` on these rows matches conservative “outcome unresolved / exception path” semantics; blocked peer-invalid paths also appear under `block_code=send_as_peer_invalid` (107).

Recording errors is **in-scope success** for shadow. Automatic resend from these rows remains **forbidden**.

### 3. Blocked diversity (good for observation)

Top `block_code`: `global_recovery_cooldown` 212, `send_as_peer_invalid` 107, `supervisor_quiesce` 47, `scope_exit_without_terminal` 41, plus quiet/guard/timeout tails.

## Capacity note

| Metric | 31h | 78h | Comment |
|--------|-----|-----|---------|
| Attempts | 1744 | 4136 | ~53/h, stable vs earlier ~56/h |
| Evidence | 2174 | 4911 | |
| DB file | ~4.8 MB | ~10.4 MB | fine |
| Payload approx | ~0.4 MB | ~0.97 MB | fine |
| 72h projection (from tool) | — | tool self-projects ~3814 | actual 4136 slightly higher due to blocked/unknown growth; still small |

**Retention:** still report-only; **no** archive/delete job approved. Revisit only if DB growth becomes operationally painful (not the case now).

## Residual debts (still non-blockers)

1. **2 orphaned `queued`** — shadow lifecycle gap on abrupt stop; fix later as observational cleanup.
2. **Peer-invalid noise** — many blocked + send_unknown from bad send-as peer; ops/account hygiene, not bind precision.
3. **Empty `command_family`** — still present (~369); reporting debt.
4. **Sent without reply evidence** — expected for keepalives/no-reply; not counted as weak binds.
5. **Listener session degradation** — health warn only; out of Attempt control scope.
6. **business always open** — correct until Gate 4 designs business projection.

## Explicit non-approvals (unchanged)

- No `CONTROL_MODULES` / `CONTROL_IDENTITIES`
- No recover-report-only **actions**
- No reducer/scheduler/resend takeover
- No legacy recovery removal
- No Gate 4 pilot (`explore_rift` or otherwise)

## Sign-off

| Role | Outcome |
|------|---------|
| Grok 72h closeout | **PASS** — continue shadow production |
| Gate 0–3 (7/13) | **Still in force** |
| Gate 4 | **Not scheduled / not approved** |

### What you should remember next

| When | What |
|------|------|
| **Done 2026-07-15** | 72h closeout PASS (this doc) |
| Ongoing | Daily 00:15 / 12:15 checkpoint; treat hard fail as parity/bind/control breach, not mere `warn` with explained stale/error samples |
| Gate 4 | Only if you **explicitly** request a new ADR review |

**Bottom line:** 72 小时观察窗收口通过；影子继续跑；**不要**因为 `status=warn` 或出现了 `send_unknown` 样本就开控制面——那些正是影子该看见的事实。

## 2026-07-30 follow-up checkpoint

Read-only checkpoint at 10:20 UTC+8, after 440.25 hours of shadow observation:

| Check | Current evidence |
|------|------|
| Sent-log root parity | **12317/12317**, missing 0 |
| Written bindings | **14084 exact_reply_to_root**, non-strong 0 |
| Attempt rows | 13738 |
| Transport samples | sent 12317, blocked 1394, send_unknown 25, queued 2 |
| Automatic resend | **0** |
| Business takeover | **0** (`business` remains open by design) |
| Capacity | DB 33.85 MB; approximate Attempt/evidence payload 2.91 MB |

The 25 `send_unknown` rows and two orphaned `queued` rows are historical; the newest
sample was last updated on 2026-07-22. They remain evidence for a future report-only
design review, not authorization to recover, resend, close, archive, or mutate runtime
state. Empty `command_family` rows have grown to 1039, mainly manual anchors, phaseful
keepalives, duel equipment commands, and replica choices; this remains reporting debt
under the shared send/reply freeze rather than a reason to alter transport semantics.

**Decision unchanged:** Gate 0-3 remains healthy, shadow write/bind may continue, and
Gate 4 is still not approved.
