# Explicit reconciliation of old concubine checkpoints

WalterWA2000's October 12 voyage-return operation and xianxia9527's October
17 divination operation have sent command IDs but no accessible result. The
normal conservative recovery correctly refuses to infer completion or retry
spending. Consequently, an operator needs a separate reconciliation path when
current server state demonstrably provides a new action entitlement.

The read-only cave probe now supports `.我的侍妾` and `.远航状态`. The new
`tools/reconcile_concubine_checkpoint.py` is an explicit maintenance tool,
not an automatic timeout/reset path. Normal runtime recovery is unchanged.

The tool accepts only an exact known sent operation older than 24 hours,
the unchanged identity/account/partner/phase/root anchor, and a complete,
selected-player-bound HTTP status panel obtained within 120 seconds. It
requires either divination ready with no active effect or a same-route voyage
explicitly returned and awaiting settlement. Recent, unknown-without-receipt,
foreign, incomplete, stale, conflicting or currently active transport work
remains blocked. A later local divination cooldown also prevents release.

Applying requires `xiuxian.service` inactive/MainPID=0 and a new full SQLite
backup with quick_check=ok. One transaction archives the full old action with
outcome_unknown=true and its proof digest, removes only its blocking action
slot/anchor, and forces availability unknown and snapshot invalidation. The
normal scheduler must acquire another authoritative status panel before it
can choose a new action. No request is sent by this tool. No old reward,
affinity delta, receipt, action success, or notification delivery is inferred.
The archive survives runtime reload in a dedicated per-operation meta key.

Generate a fresh status proof while the worker is idle, then stop the worker
gracefully. Preview the concrete reconciliation using `--db`, `--proof` and
`--expected-op`. Only after validation, apply with `--apply --backup PATH` and
restart the worker. Identity flags, module switches, other timers, gains and
unrelated pending work are preserved. If the panel changes or the proof ages
out, obtain a new status read; never edit a proof or replay an uncertain action
to force the tool to accept it.

The separate Lpprceqei fate issue is not resolved by this tool. A direct
authoritative fate read on November 4 confirmed progress 0/30 with the same
record creation time and accept_v1 quest. A dwelling read showed an active
deep retreat ending around 14:16, not an overdue unclaimed completion.
The original progress-10 snapshot and numeric guard remain unchanged.

Validation: 861 focused reconciliation/divination/voyage/query/status tests
passed. The additional read-only probe suite passed 44 tests; rerunning it
with the 31 new reconciliation cases passed 75 tests. Compile, Ruff and diff
checks passed. SQLite tests cover atomic archive/checkpoint writes, rollback
if the worker restarts, mandatory backup and stopped-worker checks, dry-run
non-mutation, pending transports and owner changes. Production acceptance is
recorded separately after release; test success alone does not prove recovery.
