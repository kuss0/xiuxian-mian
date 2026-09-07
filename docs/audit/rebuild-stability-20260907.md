# Full Stability Review and Rebuild

## Objective and Evidence

Complete Review -> Rebuild -> Test -> Final Review for the whole automation
service. Passing existing tests alone does not establish correct operation.
Each corrected behavior needs a reproducer, a state-transition assertion, and
evidence that cancellation, restart, or stale input cannot repeat its effects.

Baseline: `a41409fd15ea6e1d62c87dc4cca25abb38c82700`, production branch `main`.
Candidate: `rebuild/stability-20260907` in
`/root/xiuxian-main-rebuild-20260907`.

The existing quiz-bank edits and untracked `tools/dump_ui_write_keys.py` were
copied into the candidate for source parity. They remain user-owned and must
not be included in implementation commits by accident. No credentials, live
sessions, or live database were copied. Tests use temporary data directories.

Initial live check: main service, health observer, and watchdog are active;
listener is inactive; main service `NRestarts=0`. This is a checkpoint, not
proof that gameplay is healthy. Production files have not been changed.

## Requirements and Acceptance Matrix

| Area | Required behavior | Evidence required | Status |
| --- | --- | --- | --- |
| Lifecycle | Startup, shutdown, reconnect, and task cancellation preserve pending work and release resources | Supervisor and async lifecycle failure tests; bounded live observation | Candidate shutdown repaired; reconnect and forced-stop durability review still pending |
| Sending | No duplicate side effects after queue expiry, uncertain send, toggle-off, or cancellation | Reproducers spanning enqueue, await, transport result, and business transition | Pending |
| Reply routing | Exact identity/chat ownership; manual actions and edits reconcile once; broadcasts do not establish send health | Cross-chat, multi-account, out-of-order and duplicate-event replay | Shared pending/history routing repaired in candidate; module scalar anchors and final integration still pending |
| Scheduling | Every active module honors its own switch, authoritative cooldown, prerequisites, and mutual exclusion | Module inventory; enabled/disabled and resource-boundary tests | Pending |
| MiniApp | Current public entry, bounded reconnect, shared rate limits, isolated sessions; no blind mutation replay | HTTP/browser fault tests; public-entry and scheduler integration tests | Pending |
| Gameplay | Tianxing, duel, retreat, Yinluo/Wanxin, concubine, small world, fishing, tree, tower, trials, and remaining modules close their state transitions correctly | Per-module review and realistic response fixtures, including failure paths | Pending |
| Persistence | Atomic saves, compatible reloads, bounded history, no secret/test-state leakage | Crash/reload, corrupted-state, retention, and test-isolation checks | Chat-scoped pending/history and delta recovery snapshots repaired; forced-stop durability and capacity still pending |
| UI/control | Saved settings match runtime behavior; no stale-response overwrite or unintended send; access controls hold | API and browser/control contract checks | Pending |
| Operations | Reproducible dependencies, usable diagnostics, distinguish business failure from transport failure | Clean-environment tests and current health evidence | Pending |
| Final review | Revisit every finding and changed contract; record real residual limits | Full suite, targeted fault replay, diff review, deployment comparison | Not started |

## Preserved Operating Decisions

- Public MiniApp entry and group commands coexist. Select transport per module;
  a group sending problem alone must not globally disable available MiniApps.
- Deep retreat does not consume or block Tianxing effects.
- World Boss remains disabled unless the user separately asks to enable it.
- CommandAttempt remains an evidence ledger, not a retry/recovery controller.
  Gate 4 is outside the approved operational scope.
- Resource-spending actions need verified results and bounded recovery.
- Inventory API reads remain explicit UI operations, not background polling.
- Do not activate the inactive listener as a side effect of deploying fixes.
- Preserve the user's live switches, identities, equipment, and unrelated edits.

## Findings

| ID | Severity | Finding and reproduction | Candidate status |
| --- | --- | --- | --- |
| R01 | High | `official_schedule.py` list/delete RPCs reference an undefined `runtime`; both new RPC tests failed with NameError | Fixed; account fallback also removed; focused tests pass |
| R02 | High | `config.py` computes data/session/state paths before reading `.env`; isolated import creates the wrong directories | Fixed; subprocess test verifies effective paths and absence of stray default data |
| R03 | High | Test-mode config still imports unspecified fields from project `.env` and can call public IP services | Fixed; subprocess tests reject credential import and external requests |
| R04 | High | Global pause, shutdown quiesce, and route-protection changes during entity resolution are not checked before SendMessageRequest | Fixed; one shared validation function at all three boundaries; 308 related tests and 11 subtests pass |
| R05 | Medium | Cancelling `_run_account_rpc` before acquiring its account lock leaves the supplied coroutine unclosed | Fixed; coroutine lifecycle regression passes |
| R06 | Medium | MiniApp retries omit all backoff/Retry-After waits when no custom sleeper is supplied | Fixed; default-sleeper 429/503 regressions pass; 265 MiniApp tests and 12 subtests pass |
| R07 | High | Cancelling a caller after its shielded send RPC starts can abandon result tracking while the RPC continues | Late-result ownership and serial barriers fixed in candidate; cancellation-before-dispatch, duplicate registration, and detached-pending tests pass; shutdown/unknown-result durability remains under review |
| R08 | High | Pending recovery and live routing close pending tasks before successful business handling; family cleanup can remove newer work | Candidate log/live paths now clear only the handled root and guard; real checkin, failure, intermediate-ack, identity-card continuation and replay-idempotence tests pass |
| R09 | Medium | Shutdown cancels identity/background tasks without consistently joining them before final state save | Fixed in candidate; named/background/UI/login/provider task cleanup is joined, repeat cancellation is avoided, final save follows disconnect and is skipped on incomplete drain |
| R10 | Medium | No dependency lock or static undefined-name gate; baseline tests did not cover broken official-schedule RPCs | Clean dependency install, `pip check`, Ruff and full suite pass; CI workflow added but not yet run remotely |
| R11 | High | Pending/message-index SQLite tables and several in-memory trackers use message ID without a full chat/identity key; distinct groups can reuse message IDs | Candidate pending/history now use chat plus message ID within each identity, including save/reload, recovery, retry, cleanup and UI; scalar business anchors and unanchored business-level guard closures still need review |
| R12 | High | Second-soul and phaseful timeout cleanup call the all-identities pending-clear helper without an owner argument | Fixed; both callers supply the active identity, helper requires explicit scope, global World Boss cleanup is explicit |
| R13 | Medium | A TypeError inside a sent-command observer is mistaken for a legacy signature and invokes that observer again | Fixed; removed the re-invocation fallback, verified both registered observers accept metadata, and isolated callback failures |
| R14 | High | Delta-save snapshots omit pending chat, topic and recovery metadata; a receipt-only or detached-send update can be skipped after an earlier save | Fixed; route and recovery-only edits trigger an identity write, and reload preserves the no-retry marker and replay receipts |
| R15 | High | Tianji quiz scheduler writes an old pending-map snapshot after awaits, deleting newly queued prompts, restoring cleared work, and sending cancelled later items | Fixed in candidate; per-entry ownership is rechecked before dispatch and after awaited work; three interleaving regressions pass |

Inventory: 284 tracked Python files, approximately 271k lines including tests;
no duplicate top-level Python definitions found by AST inspection. Static
undefined-name/redefinition checks found R01 plus a duplicate `deque` import,
and are green after the candidate fixes. These checks do not establish business
correctness or complete the whole-project review.

The existing fixture/readiness report covers 106 of 108 active reply families.
`hehuan_escape` and `wanxin_moon_join` lack registered real-message samples;
five monitor/control-only contracts need separate behavioral verification.

## Verification Log

- Baseline full-suite run completed in the isolated worktree, with
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and a local explicit UI base URL.
- Baseline JUnit output: `/tmp/xiuxian-rebuild-baseline-20260907.xml`.
- Baseline result: 3727 passed, 575 subtests passed, 60.74 seconds.
- Foundation candidate in a newly created Python 3.13 venv, installed only
  from `requirements-dev.txt`: 3738 passed, 579 subtests passed, 55.86 seconds.
  JUnit: `/tmp/xiuxian-rebuild-clean-env-20260907.xml`. `pip check`, selected
  Ruff checks across all code/tests/tools, and `git diff --check` pass.
- Live settings remain unchanged: global enabled, World Boss disabled globally
  and on all 24 identities, automatic incense refinement disabled on all
  identities. The production worktree still contains only the two original
  user changes.
- Production read-only database checkpoint: 24 identities, zero pending tasks,
  53 MiB DB; shadow ledger has 19113 sent/open, 3784 blocked/open,
  25 send_unknown/open, and 2 queued/open rows. Open-ledger capacity is still
  a review item; these counts do not authorize business recovery or deletion.
- No live game commands or configuration writes have been issued for this review.
- R08 full suite: 3749 passed, 579 subtests passed, 58.60 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r08-20260907.xml`; Ruff and `git diff --check` pass.
  Recovery now dispatches anchored official-bot evidence to business handlers,
  preserves unresolved replies without retrying after log expiry, and cannot
  clear sibling pending rows or a newer action guard. Intermediate replies keep
  their pending state; per-pending replay receipts are bounded to 64 entries.
  Receipt/edit ordering and duplicate replay are tested after in-memory dedupe
  state is cleared. No production deployment or remote CI run has occurred.
- R07/R08 candidate full suite: 3758 passed, 579 subtests passed, 55.79 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r07-r08-20260907.xml`. Follow-up persistence,
  runtime send and reply-replay tests: 98 passed, 2 subtests passed.
  A restart roundtrip exposed missing recovery-field persistence; additive
  `pending_tasks.recovery_json` now preserves the bounded receipts and detached
  caller marker. Legacy-column migration and rejection of metadata overrides
  are tested. Late RPCs have strong ownership and a 120-second completion
  deadline; waiting sends obey their own queue deadline and do not overlap an
  in-flight request. A caller cancelled before dispatch cannot initiate an RPC.
  An already-sent detached command retains a no-retry pending row until reply
  reconciliation. The no-message-ID/forced-shutdown case is not yet signed off.
- R09 full suite: 3767 passed, 579 subtests passed, 57.76 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r09-20260907.xml`; Ruff and `git diff --check` pass.
  Eight lifecycle regressions cover cleanup ordering, individual cleanup
  failures, repeated quiesce, active HTTP requests, login workers, quiz-provider
  cancellation, rejection of new background work during shutdown, and failed
  drain behavior. Tower/wild/trial/World Boss/notification workers now share the
  background-task ownership registry; no automation switch was changed.
  Shutdown has a bounded cleanup budget before the supervisor's 20-second
  worker-stop timeout. External threads and no-ID sends remain part of the
  forced-stop durability review, not evidence of end-to-end acceptance.
- Fresh production read-only check after R09: HEAD remains `a41409fd`; only the
  original quiz-bank edit and `tools/dump_ui_write_keys.py` are present. Main,
  watchdog and observer are active with `NRestarts=0`; listener remains inactive.
  These process facts do not claim that all gameplay is healthy.
- R11 database candidate full suite: 3773 passed, 579 subtests passed, 57.12
  seconds. JUnit: `/tmp/xiuxian-rebuild-r11-db-20260907.xml`. Pending and sent
  message rows now use identity plus message ID, so one identity cannot overwrite
  another's rows. Legacy rows, defaults and ordinary indexes survive migration;
  one savepoint rolls back both table changes on failure. Unknown schemas,
  triggers, unique constraints and incoming/outgoing foreign keys require manual
  review rather than silent loss of behavior. This does not yet solve two groups
  using the same message ID for one identity, or bare in-memory reply indexes.
  R11 remains open. The skill and production files/services remain unchanged.
- R08/R11 routing candidate: 3786 passed, 579 subtests passed, 58.24 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r11-routing-20260907.xml`; Ruff and diff checks
  pass. Eight new regressions first reproduced premature pending removal,
  cross-chat routing, broken root propagation and newer-guard closure. Follow-up
  tests cover ambiguous log rows, early-reply cache collisions, unknown legacy
  chat provenance and identity-card partial/final edits. In-memory indexes now
  include chat and identity; known entries bypass the log scan. Live dispatch
  clears only a successfully handled root and uses its message ID to close the
  action guard. Identity refresh now reports accepted partial cards as handled,
  while preserving the follow-up and allowing final edits. No service restart,
  production mutation, game send, deployment or push was performed.
- R12 candidate: 3790 passed, 579 subtests passed, 56.76 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r12-20260907.xml`; Ruff and diff checks pass. Two
  scheduler regressions reproduced one identity clearing another's pending
  commands. The helper now requires an explicit identity or explicit global
  scope, and both timeout paths provide their owner. The disabled World Boss
  path retains only its existing explicit all-identity cleanup behavior.
- R13 candidate: 3793 passed, 581 subtests passed, 56.86 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r13-20260907.xml`; Ruff and diff checks pass. The two
  failure regressions first reproduced duplicate callback execution and an
  exception preventing the next observer from recording the send. Removing the
  unused signature fallback fixes both without adding another dispatch layer.
- Latest read-only production checkpoint: `main` remains at `a41409fd`, with
  only the user's original quiz-bank edit and untracked helper. Main service,
  observer and watchdog are active with `NRestarts=0`; listener is inactive.
  No production state/configuration or monitoring skill was modified.
- R11 chat-scoped candidate: 3811 passed, 584 subtests passed, 57.33 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r11-chat-20260907.xml`; Ruff and diff checks pass.
  Initial failing tests demonstrated same-identity cross-chat overwrite and
  last-log-row route selection. Subsequent reproducers caught cross-chat action
  guard closure and lost family resolution after history-only reload.
  A finalizer -> SQLite save/reload -> exact reply cleanup -> second save/reload
  test preserves both chats. Real checkin-handler replay, scoped retry with a
  colliding new ID, delayed message cleanup, and JSON UI snapshots also pass.
- Both SQLite keys are now `(send_as_id, chat_id, msg_id)`; both per-identity
  maps use `(chat_id, msg_id)`. Known-chat lookups use the composite key directly.
  Old global or identity-only primary keys migrate in one savepoint. Unknown
  legacy provenance remains zero and does not authorize automatic retry or
  deletion in the primary group. Conflicting legacy and composite references
  fail the save transaction instead of silently replacing a row.
- Message-log lookup treats an explicit zero chat as unresolved, not an
  unscoped query. An intentionally unscoped read also rejects cross-chat ID
  collisions. Phaseful replay retains its originating chat; shared reply and
  timeout guard closure checks both the root ID and chat. No CommandAttempt
  recovery authority or new send/retry policy was introduced.
- R14 regression changes only topic, detached-send flag or replay receipts
  after an initial save; each edit must cause a delta write. The loaded row
  retains every edited field. This checks the incremental-save path, not only
  initial serialization of a new pending row.
- R11/R14 pre-commit review repeated the isolated full suite: 3811 passed,
  584 subtests passed, 57.46 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r11-precommit-20260907.xml`; Ruff and diff checks pass.
  The shared-key candidate is ready for a local commit, not deployment or R11
  completion. Module-level anchors and guard closure remain under review.
- Production was checked again read-only after the candidate changes: HEAD
  `a41409fd`, only the original quiz-bank/helper changes, main/watchdog/observer
  active with `NRestarts=0`, listener inactive. No deployment, restart, push,
  live command or skill modification has occurred.
- R11 prompt-route/R15 candidate: 3826 passed, 584 subtests passed, 58.84
  seconds. JUnit: `/tmp/xiuxian-rebuild-r11-prompts-20260907.xml`; Ruff and
  diff checks pass. Delayed replies now preserve their chat through schedule,
  persistence, reload, send and callback; legacy replies without a chat fail
  before sending. Jiyin callbacks cannot clear another chat's same-ID prompt.
  Quiz command fallback and Tianji answers use the original prompt chat.
- Tianji regressions first reproduced cross-chat result binding, a wrong
  explicit reply falling back to a target name, and nonterminal/player replies
  ending a task. Recovery now requires official-bot evidence and terminal
  wording in the original chat. The old recovery test was corrected to register
  its identity; it had previously passed via the missing-identity early exit.
  Three further failing tests established R15 before the per-entry update fix.
- Read-only real-message sampling found second-soul warning/result edits retain
  the warning's chat/message ID; for example, 2026-09-04 old-group message
  `12137042` is edited from a warning to success. The result handler still
  selects a unique local `heart_demon_pending` identity without that anchor.
  This is a follow-through item, not verified correct behavior.

## Deployment Constraint

The chat-key migration is not a code-only rollback. Once two chats contain the
same message ID for one identity, old loaders collapse those rows in memory.
Any later deployment needs a verified database snapshot and an explicit rollback
procedure; do not run the old code against newly written multi-chat state.
This candidate has not migrated the production database.

## Next Review Priorities

1. R11 follow-through: audit scalar module anchors (`*_msg_id`), business-level
   guard closures without an expected root/chat, and follow-up sends whose
   `reply_to` has no explicit target chat. The shared pending/history contract
   is now tested end-to-end; it does not prove every module's ownership rules.
   Jiyin/quiz/Tianji routing is covered; second soul, Nanlong, sect teaching,
   judgement and remaining wrapped send calls still require review.
2. R07: establish crash-durable ownership before a send can cross the transport
   boundary, and reconcile an outcome without a message ID. Preserve the
   CommandAttempt shadow-only boundary; a new retry/recovery controller is not
   approved by these fixes.
3. Continue the full acceptance matrix: module enable/disable and authoritative
   cooldowns, MiniApp mutation/reconnect behavior, persistence capacity, UI
   control contracts and operations. Existing mocks and process uptime cannot
   replace missing real-game evidence or the final integration review.

## Completion Gate

Do not mark the objective complete while any matrix row lacks its required
evidence, a confirmed finding is unresolved, or candidate and deployed behavior
have not been reconciled. Unavailable external-game evidence must be reported
explicitly rather than replaced with a passing mock test.
