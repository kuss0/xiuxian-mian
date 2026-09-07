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
| Scheduling | Every active module honors its own switch, authoritative cooldown, prerequisites, and mutual exclusion | Module inventory; enabled/disabled and resource-boundary tests | Normal/phaseful and queued fast-due owner invalidation fixed in candidate; module-wide switch/CD and internal-await review still pending |
| MiniApp | Current public entry, bounded reconnect, shared rate limits, isolated sessions; no blind mutation replay | HTTP/browser fault tests; public-entry and scheduler integration tests | Generic HTTP retry permission and flow budget fixed in candidate; per-game retry/reentry, redirects, session lifecycle and current-entry integration still pending |
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
| R16 | High | Second-soul warnings are discarded after a status panel sets the same phase, and warning callbacks enter a deleted identity after awaited logging or sending | Fixed in candidate; panel-first warnings acquire an exact route, legacy anchors gain a chat without resending, and both deletion boundaries are tested |
| R17 | High | Checkin/sect-teaching send receipts overwrite an early result's next step or update a removed/disabled identity; repeated success increments teaching twice and cleanup loses concurrently added work | Fixed in candidate for the reproduced interleavings; send-return ownership checks, shared persisted completion keys and chat-scoped incremental cleanup are covered by new tests |
| R18 | High | Judgement rewrites stale pending snapshots, requeues prompts after disable/deletion, and uses pre-refresh button coordinates; account fallback can choose the wrong channel identity | Reproduced and fixed in candidate; exact terminal anchors precede unique-name broadcast matching, original-chat sends and current-entry updates are tested; queued transport cancellation and unknown outcomes remain under R07/scheduling review |
| R19 | High | Nanlong omits the prompt chat, overwrites newer work after awaits, and accepts stale trade broadcasts during placement/recall; deleting an identity during send updates another identity | Reproduced and fixed for these boundaries in candidate; original route/receipt metadata is persisted, terminal transitions precede notifications, and real cross-group broadcasts still complete; pre-receipt outcome and uncertain-send review remains open |
| R20 | High | A throwing registered pre-send guard is skipped, and a guard returning a Future is treated as allowed without awaiting its decision; decision-normalization errors are misclassified as unknown sends | Fixed in candidate; synchronous/async faults stop before transport as definitely unsent, all awaitables are awaited, and cancellation still propagates |
| R21 | High | Passive checkin marks the day complete before classifying the reply; repeated success rewinds queued teaching; old-day replies reset current-day progress; unknown replies are treated as terminal | Reproduced and fixed in candidate; direct and passive checkin share idempotent completion, old days cannot roll back state, and unknown replies retain pending ownership |
| R22 | High | Nanlong can reenter while sending, retries unknown sends as failures, rearms them when a choice changes, and discards real receipts after a post-dispatch choice change | Fixed for reproduced boundaries in candidate; persist the existing pending decision before sending, retry only explicit unsent results, and keep operation ownership across option changes; early-result and late-receipt recovery remain open |
| R23 | High | Retry timeout notifications and send receipts mutate replacement pending work; blocked retries rewrite the original send time, and an old refresh timeout clears newer or cross-chat ambiguous refresh anchors | Fixed in candidate; complete terminal cleanup before notification, recheck owner and pending snapshots after transport, keep detached receipts no-retry, persist separate retry backoff, and clear only an exact unambiguous refresh anchor |
| R24 | High | Nanlong log recovery misses unthreaded cross-group trade results, trusts player copies of result wording, and leaves a confirmed detached command pending after business completion | Fixed for reproduced cases in candidate; replay trusted incoming evidence through the existing direct/broadcast handlers, preserve source-chat boundaries, and clear only the confirmed command's exact pending key; no-ID/early-receipt recovery remains open |
| R25 | High | A queued send uses an identity after deletion, replacement, account rebinding or disable; a deleted implicit context falls back to another role, and a pause after RPC task creation still permits dispatch | Fixed in candidate; capture the existing owner/account, revalidate after preparation and at actual dispatch, and keep a deleted active context from selecting another identity; post-dispatch receipt durability and module-switch admission remain under review |
| R26 | High | Ordinary, cleanup and phaseful scheduling continue into later modules after an awaited operation removes, replaces, rebinds or disables their identity | Fixed in candidate; retain the identity object and account, revalidate before later module calls and proxy reads, and stop on account-offline/global-pause changes; independent phaseful polling and continuation of unaffected roles are tested; R27 covers queued fast-due dispatch |
| R27 | High | Fast-due queues execute invalidated identities; Tianxing continues after an invalidating preparation; late errors overwrite new cooldowns or crash again while entering a deleted identity | Fixed in candidate for wild training, rift, concubine, Tianxing and queued timeline follow-up; retain scan-time ownership, recheck at task dispatch and after preparation, and compare business snapshots before failure backoff; per-module internal await boundaries still require review |
| R28 | High | Passive teaching treats the real success suffix as an already-done reply, stops at 1/3, and consumes the third-success dedupe key before direct cleanup/notification; a cleanup await can notify for a replaced role | Fixed in candidate; share one teaching result handler, prefer success over the already-done substring, use explicit server counts without rewinding on older replies, and validate owners after cleanup/passive awaits; real wording, delivery order and SQLite reload tests pass |
| R29 | High | HTTP capture construction/storage errors escape after a MiniApp response was received, losing a confirmed result; capture construction also runs when no sink exists | Fixed in candidate; build and emit HTTP captures inside one diagnostic-only exception boundary, log only the exception class, and leave the original HTTP result/budget/retry policy intact; an actual tower-flow replay retains its reward result after capture failure |
| R30 | High | Business-capture construction escapes the diagnostic boundary; World Boss business-capture write errors interrupt an accepted hit or completed settlement | Fixed in candidate; isolate construction/redaction/storage in both business-capture helpers, retain cancellation propagation and secret-free error-class warnings; complete battle replays preserve accepted hits, the final result and the exact request sequence |
| R31 | High | Generic HTTP retries uncertain requests without a replay-safety contract; generic flow execution ignores the adapter's request budget | Fixed in candidate; default to one attempt, require explicit boolean retry safety, share one budget across flow steps/retries, and retain bounded read-only World Boss state reconciliation; per-game manual loops/reentry remain under review |

Baseline inventory: 284 tracked Python files, approximately 271k lines including tests;
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
  `12137042` is edited from a warning to success. The baseline selected a unique
  local `heart_demon_pending` identity without that anchor; the shared topic
  reply header (`7310786`) did not identify the owner.
- R11 second-soul/R16 candidate: 3836 passed, 591 subtests passed, 57.27
  seconds. JUnit: `/tmp/xiuxian-rebuild-r11-second-soul-20260907.xml`; Ruff
  and diff checks pass. The targeted second-soul, persistence, early-replay and
  UI suite passed 79 tests and 10 subtests. No live probes, production changes,
  deployment, push or skill changes occurred in this checkpoint.
- Second-soul terminal edits now require the warning/choice chat and message
  ID, persisted across reload. The app-dispatch regression rejects another
  user's same-topic result and another chat's same-ID result. Missing chat
  evidence cannot fall back to the primary group. Username matching uses exact
  case-insensitive mentions instead of prefixes. Panel-first warnings can
  establish their route; legacy warning state is enriched without resending.
  Early completion cannot be overwritten by a late send receipt, and deletion
  during either await cannot recreate or enter the removed identity. The new
  identity-deletion, username and panel-order tests failed before their fixes.
- R11 checkin/sect-teaching/R17 candidate: 3849 passed, 599 subtests passed,
  60.79 seconds. JUnit: `/tmp/xiuxian-rebuild-r11-checkin-20260907.xml`.
  The focused lifecycle, no-sect, control, persistence, passive identity and
  early-replay suite passed 143 tests and 16 subtests. Ruff and diff checks
  pass. No deployment, restart, push, game send or live state change occurred.
- Checkin and teaching anchors now persist their originating chat through
  replies, the next queued step, save/reload, control resume and daily reset.
  A legacy queued reply without a chat stops rather than guessing a primary
  group. Direct and passive teaching results share at most three command keys
  per game day, so duplicate delivery does not advance the count twice, and
  either delivery order retains the next step. The send callback cannot clear
  a next step established during the send await or enter a removed identity.
- Cleanup records use chat/message pairs and remove only the successfully
  deleted snapshot entries. Same-ID messages in different chats remain
  distinct, ambiguous legacy IDs cannot select the primary group, and work
  added during a deletion is retained. The new route/lifecycle tests reproduced
  eight failing cases before the first fix; subsequent tests reproduced the
  passive-first followup gap and both checkin send-return races before repair.
- R11 judgement/R18 candidate: 3871 passed, 609 subtests passed, 58.92 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r18-judgement-20260907.xml`; Ruff and diff checks
  pass. Focused judgement tests passed 54 tests and 10 subtests. The first
  boundary run reproduced six failures; additional ownership/lifecycle tests
  reproduced eight more before their fixes.
- Judgement pending writes now retain newly queued work and cannot restore
  removed entries after a send or MiniApp result. Prompt identity resolution
  rechecks module state, identity existence, and MiniApp terminal evidence.
  Button dispatch rechecks state after RPC-slot acquisition, after fetch, and
  between clicks, and derives positions from the fetched original-chat message.
  Tests also reject fetches returning a different message or chat.
- Exact sender identity takes precedence over account ownership; a shared
  account without an exact identity cannot select its first channel. Log
  fallback requires a known chat. Explicit terminal anchors outrank target-name
  ambiguity; name-only broadcasts require one current candidate, and stale or
  conflicting explicit replies cannot clear work. Real unthreaded success
  broadcasts remain supported. This does not certify the existing retry policy
  for unknown sends or mutation replay; those remain open in the full matrix.
  Production, live switches, services, skill and remote branches are unchanged.
- R11 Nanlong/R19 candidate: 3889 passed, 615 subtests passed, 58.55 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r19-nanlong-20260907.xml`; Ruff and diff checks
  pass. The initial lifecycle suite reproduced 17 failures before the fix.
  Nanlong, control and persistence tests passed 70 tests and 78 subtests;
  subsequent real-message cases passed with the full suite.
- Prompt replies, placement, recall and log recovery now preserve their
  originating chat and identity. Unknown prompt provenance does not select the
  primary group. New prompt keys and send-route metadata survive SQLite save,
  delta update and reload; completed prompt receipts survive transient cleanup
  so a late edit cannot requeue the same decision. Old nonempty prompts lacking
  route metadata require anchored recovery or a fresh prompt before sending.
- Async receipt updates revalidate the current identity object and operation;
  deleting an identity, disabling its module or replacing its prompt cannot
  rewrite another role or newer work. Result state transitions happen before
  audit awaits, preventing duplicate broadcasts from clearing a pending recall.
  Broadcast acceptance requires an exact unambiguous target, a current exchange
  and time evidence; conflicting explicit reply anchors are rejected.
- Read-only samples from `2026-09-06.log` confirm old-group prompts `12172842`
  and `12173842` produce new-group unthreaded results `1062207` and `1062809`.
  Both exact wording pairs are fixtures in `nanlong_cross_chat_20260906.json`.
  The replay verifies original-chat dispatch and cross-group completion with
  mocked transport, not a newly executed live trade. Production remains untouched.
- R20 pre-send-guard candidate: 3893 passed, 617 subtests passed, 58.24 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r20-send-guards-20260907.xml`; Ruff and diff
  checks pass. Runtime-send, Tianxing and retry-scheduler tests passed 331 tests
  and 13 subtests. Four initial failing cases demonstrated actual transport
  invocation after a guard failure/Future denial, or an unsent decision error
  being marked unknown. Cancellation propagation is also tested.
- A guard failure now returns the existing `pre_send_guard` block code and is
  recorded as definitely unsent. No pending reply is created, no cooldown is
  inferred, and no new retry policy or CommandAttempt authority was introduced.
  These changes are local candidate commits only; no deployment, restart,
  live command, production state change or push was performed.
- R07 detached-result follow-up: 3900 passed, 619 subtests passed, 59.62 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r07-detached-20260907.xml`; Ruff and diff checks
  pass. The focused send, retry, persistence and real-handler replay suite
  passed 132 tests and 9 subtests. Six initial failing cases established the
  untracked cancellation and stale-recovery gaps before their fixes.
- A cancelled `track=False` caller now retains an acknowledged late send as
  no-retry pending work. Bot-silence handling and broad module cleanup cannot
  erase that unresolved evidence. Exact real replies still finish the business
  transition and clear the row; SQLite save/reload preserves both the marker
  and the later exact cleanup. Recovery awaits cannot remove replaced or
  updated pending work or enter an identity deleted during the await.
- This verifies acknowledged, message-keyed late sends only. It does not
  establish crash-durable ownership before the message ID is known, and it
  does not add an outbox or authorize Attempt recovery. Production, skill,
  live switches, services and remote branches remain untouched.
- R07 forced-stop probe now uses separate spawned processes and a temporary
  SQLite database: `tools/lab_send_crash_probe.py --assert-safe`. A fake RPC
  records its effect, saves state successfully, and waits without returning a
  message ID. The parent kills that worker with SIGKILL, then a fresh process
  loads the same database and attempts the same command through runtime.
- Both `--command checkin` and `--command rift` reproduce a second transport
  invocation after reload (`safe=false`, assertion exit 1). The guarded rift
  case persisted `attempt=0` and zero pending rows. This is a reproduced open
  safety failure, not a passing crash-safety test. The probe cannot connect to
  Telegram and removes its isolated state when finished. The proposed durable
  ownership design remains unimplemented and requires review independent of
  CommandAttempt's shadow-only ledger.
- R21 checkin-terminal candidate: 3908 passed, 625 subtests passed, 59.64
  seconds. JUnit: `/tmp/xiuxian-rebuild-r21-checkin-terminal-20260907.xml`;
  Ruff and diff checks pass. The focused checkin, passive identity, persistence
  and real-handler replay suite passed 79 tests and 17 subtests. Eight initial
  failing cases reproduced false completion, passive/direct delivery-order
  differences, duplicate rescheduling and old-day rollback before the fix.
- Checkin completion now has one shared state transition. Success/already-done
  evidence can establish an exact route later, but cannot rewind an active
  teaching chain or change its timer on duplicate delivery. Unknown checkin and
  teaching text no longer terminates pending recovery; the real-handler replay
  test retains unresolved ownership without resending. Save/reload preserves
  duplicate suppression. New negative samples are injected boundary cases, not
  claims of recently observed production wording. No live mutation occurred.
- R22 Nanlong-send candidate: 3921 passed, 631 subtests passed, 57.64 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r22-nanlong-send-20260908.xml`; Ruff and diff
  checks pass. Focused Nanlong, control and persistence tests passed 69 tests
  and 32 subtests. Eleven initial failing cases reproduced send reentry,
  unknown retry, option-change races, ignored save failure and false terminal
  replies. Three additional review cases corrected the candidate's handling
  of definitely-unsent placement, protected exchange and confirmation retry.
- Existing Nanlong fields now record the pending decision before transport;
  no receipt or send time is invented at that boundary. A failed save stops
  dispatch, an unknown result remains unscheduled, and explicit unsent results
  restore the previous receipt and confirmed protection before retrying.
  Save/reload preserves the hold. Option changes cannot rearm an unresolved
  send or discard a returned receipt for the current prompt. Unknown reply
  wording no longer closes the pending result as terminal.
- This is a Nanlong state-machine checkpoint, not a general durable outbox or
  resolution of R07. Result-before-receipt, late detached receipt adoption and
  recovery of unthreaded/cross-group results remain under review. Skill,
  production, switches, services and remote branches are unchanged.

- R23 retry-ownership recheck: focused runtime-send, retry-scheduler and
  persistence tests passed 119 tests and 12 subtests. Five regression tests
  cover pending replacement during notification or transport, failed-retry
  backoff without rewriting send evidence, and newer/cross-chat ambiguous
  identity-refresh anchors. SQLite delta-save/reload also preserves the
  separate backoff field.
- R23 repeated full suite: 3926 passed, 634 subtests passed, 59.53 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r23-retry-ownership-recheck-20260908.xml`.
  Ruff and diff checks pass. This verifies the current local candidate rather
  than relying on the previous run's test summary.
- Timeout cleanup now finishes before awaited audit delivery. A retry receipt
  cannot replace changed pending work; a real new send still retains a
  no-retry detached pending row until its own reply is reconciled. An unsent
  retry does not invent a new send time. This change does not resolve R07's
  no-message-ID crash gap or establish all module-level retry safety.
- R24 reproducers initially failed for cross-group result recovery, a strict
  new official bot shard, completed-trade recall selection, result recovery
  after the prompt deadline, and a player copying placement-success wording.
  Two additional live-handler/log-replay subcases reproduced detached pending
  rows surviving confirmed business completion.
- Recovery now performs one bounded log-window scan, accepts only incoming
  official-bot evidence, and reuses the live business handlers. Cross-group
  unthreaded results are considered only in the configured game groups or the
  recorded source group; exact direct replies keep their original chat/root.
  The existing real `2026-09-06` wording pairs are replayed from temporary log
  files. Repeated trade evidence sends only the existing recall step once,
  rather than another exchange. This does not add automatic recovery for
  sends that still have no known message ID, or change CommandAttempt authority.
- R24 candidate full suite: 3935 passed, 645 subtests passed, 57.62 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r24-nanlong-log-recovery-20260908.xml`.
  Focused Nanlong/control/persistence/early-replay tests passed 98 tests and
  44 subtests before the additional SQLite terminal-cleanup regression, which
  passes in the full suite. Save/reload preserves completed business state and
  exact detached-row removal while retaining a same-numbered row in another
  chat. Ruff and diff checks pass. No production or skill changes were made.
- R25 initially reproduced 12 invalidated-owner sends across entity resolution,
  an awaited guard and the RPC-task dispatch boundary, plus one implicit-role
  fallback and one pause-after-task-creation send. Each failing case observed
  an actual fake `SendMessageRequest`, not merely a changed log message.
- Runtime now binds each in-memory send to its existing identity object and
  account. Deletion, replacement, rebinding and a newly disabled identity stop
  before dispatch as definitely unsent. The RPC task checks again before it
  marks the transport started. A deleted active identity context is no longer
  replaced by the first remaining identity at the send entrypoint.
- Existing explicitly admitted commands for already-disabled identities are
  preserved, including manual/probe use; this is not a new global module-switch
  policy. A real receipt is still retained if disable occurs after the RPC has
  started. The change adds no persistent send controller, does not read
  CommandAttempt for decisions, and does not resolve the forced-stop R07 gap.
- R25 focused runtime-send, retry, early-replay and Tianxing tests passed
  367 tests and 29 subtests. Full integration verification follows below.
- R25 full suite: 3940 passed, 657 subtests passed, 59.47 seconds.
  JUnit: `/tmp/xiuxian-rebuild-r25-send-owner-20260908.xml`; Ruff and diff
  checks pass. The isolated SIGKILL probes were also rerun for both checkin
  and rift: each still reports `safe=false` with a second transport invocation
  after reload and exits 1 under `--assert-safe`. That expected failure is an
  open acceptance item, not part of the passing-suite claim.
- R26 reproduction failed all 12 original identity-change scenarios across the
  phaseful, cleanup and ordinary passes. The transport guard alone was not
  sufficient: later module code was still invoked with an invalid identity
  context and could update the first remaining role through `StateProxy`.
- Both the normal identity pass and the independent phaseful pass now retain
  the original state object/account and revalidate before subsequent scheduler
  calls. Deletion, replacement, rebinding, disable, account-offline and global
  pause stop the affected work without preventing unaffected roles from running.
  The existing deep-retreat/Tianxing exception is unchanged.
- R26 focused scheduler contracts: 78 passed, 24 subtests passed. Full suite:
  3942 passed, 681 subtests passed, 58.99 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r26-scheduler-owner-20260908.xml`. Ruff and diff checks
  pass. The fast due candidate queues, awaited Tianxing follow-ups and their
  exception handlers are not covered by this fix and remain open. No skill,
  production configuration/database, service or remote branch was changed.
- R27 reproducer: 135 failing cases before the fix, including stale candidate
  dispatch, replacement-role timer writes, secondary KeyErrors after deletion,
  and use of the pre-preparation time after an awaited Tianxing step.
- Fast-due queues now retain the original state object/account and recheck
  identity availability, the module switch, weakness and global pause before
  dispatch. Wild training retains the existing public-entry exception for a
  channel-health-frozen identity. Tianxing rechecks after timeline preparation
  and refreshes its execution time. Timeline follow-up uses scan-time ownership.
- Late timeout/error backoff only applies while both the owner and relevant
  business cooldown/anchor snapshot are unchanged. A result or replacement
  arriving during the await keeps its state. Genuine unchanged failures retain
  the existing bounded backoff; caller cancellation drains the child and
  propagates without fabricating business failure or running later candidates.
- R27 focused scheduler suites: 85 passed, 188 subtests passed. Full suite:
  3949 passed, 845 subtests passed, 59.85 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r27-due-owner-20260908.xml`. Ruff and diff checks pass.
  This introduces no persisted send fence, new retry controller or Attempt
  decision authority. R07 crash durability and module-internal ownership
  checks remain open; no production, skill or remote changes were made.
- R28 initially reproduced four failures: the real `sect_teach.success` fixture
  stopped at 1/3 because its success suffix also matched the already-done test;
  passive-first third success skipped both cleanup and the completion notice;
  an explicit 2/3 counter was recorded as 1/3; cleanup could notify after the
  identity had been replaced. The fixture is retained unchanged; 2/3 and 3/3
  counter variations are explicit test cases, not new live captures.
- Direct and passive delivery now share one result handler. Success has priority
  over the overlapping already-done wording; authoritative counters advance
  monotonically, and older replies do not requeue an earlier step. New terminal
  success clears the chain and performs cleanup/notification once in either
  delivery order. Disabled modules still observe facts without active cleanup,
  notification or game sends. No new persisted completion controller was added.
- Cleanup retains account ownership as well as the identity object. Completion
  notification is explicitly scoped to the original identity and is skipped
  after invalidation. The passive dispatcher rechecks ownership after awaited
  handlers before using proxy state or closing guards; teaching and small-world
  dispatch invalidation are covered. Other family-level guard binding remains
  part of R11 and is not claimed complete here.
- R28 focused replay/control/persistence suites: 105 passed, 25 subtests passed
  before the additional small-world dispatcher regression. Full suite:
  3959 passed, 850 subtests passed, 60.03 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r28-teach-reducer-20260908.xml`. Ruff and diff checks pass.
  SQLite reload preserves terminal count/keys and does not repeat completed
  cleanup/notification. This does not prove crash-time delivery of an audit
  notice or close R07's unknown-send gap. Production and skill remain unchanged.
- R29 four pre-fix failures demonstrate that capture write/serialization errors
  escaped instead of returning a confirmed HTTP result or request-budget denial.
  A tower-flow reproducer received the successful challenge settlement but
  raised during capture, so its caller never received the gains.
- HTTP capture construction, redaction and append are now isolated from business
  outcomes. No sink means no diagnostic record construction. A failed capture
  emits only an exception-class warning, never the exception text, token or
  payload. Real HTTP errors remain errors and retain their existing retry policy;
  no capture failure adds a transport call or changes request-budget accounting.
- R29 focused HTTP/protocol/tower/capture suites: 181 passed, 12 subtests passed
  before additional on-disk failure and retry-policy cases. Full suite:
  3965 passed, 852 subtests passed, 60.52 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r29-capture-isolation-20260908.xml`. Ruff and diff checks
  pass. The tower replay calls only start/challenge and returns its original
  gains even when challenge capture fails. No live MiniApp probe was issued.
- MiniApp review remains open: most explicit mutation call sites already pass
  `backoff_sec=()`, but this is not proof of all endpoint/reconnect semantics.
  The generic HTTP helper and generic flow runner still permit retries without
  an endpoint-level idempotence contract. Per-flow budget wiring, runtime
  mutation/reentry behavior, and session lifecycle/capacity need review before
  the MiniApp matrix row can be signed off. R29 does not change these policies.
- R07 probes repeated after R29: isolated `checkin` and `rift` processes still
  report `safe=false` and exit 1 under `--assert-safe`. Each first worker saves
  after dispatch with no pending receipt; the reloaded worker sends again.
  This is current failing evidence, not a passing test or an approved recovery
  design. No production state or Telegram transport was used.
- R30 reproducer: 10 failures before the fix, comprising nine diagnostic
  exception escapes and one missing diagnostic warning. Failures cover detail
  conversion, timestamps, redaction, callable/append sinks, and a full World
  Boss flow with storage errors after accepted hits and final settlement.
  Both business-capture helpers now isolate construction and storage errors;
  warnings contain only exception classes, and cancellation still propagates.
  All five existing business-field whitelists and normal redaction remain
  intact. Successful and HTTP-failed settlements match the no-fault baseline
  and issue exactly `start -> hit -> finish` without capture-driven retries.
- R30 focused capture/protocol suites: 251 passed, 14 subtests passed. Full
  suite: 3985 passed, 852 subtests passed, 60.63 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r30-business-capture-20260908.xml`. Full Ruff and diff
  checks pass. No production, skill, switches, services or remote branches
  were changed. R07, MiniApp request retry semantics and the remaining full
  acceptance matrix are still open; World Boss remains disabled.
- R31 pre-fix behavioral replay: 23 failures and one passing read-recovery
  control. Unclassified start/finish/next/result requests were repeated after
  timeouts, HTTP 503/429 or malformed JSON. Six actual game flows repeated
  `/start` after an unknown response (five up to four calls, fate cards twice),
  and a generic mutating flow also replayed the request four times.
- HTTP now requires literal `retry_safe=True` before applying retry delays;
  `retryable` continues to classify the transport failure and is not replay
  permission. Generic flow steps default false, reject ambiguous non-boolean
  values, and share the adapter budget across steps and retries. Tests cover
  request limits, per-request attempts, consecutive failures, pacing, local
  steps, dry preparation, cancellation, capture accounting and the global
  limiter. Existing retry tests now use an explicitly replay-safe result read.
- World Boss's read-only join-state reconciliation retains bounded retries;
  `/start` is not implicitly classified as safe. Fate cards no longer describes
  an unproven endpoint as idempotent or requests a blind start retry. Known
  mutating steps keep their existing no-retry behavior. This does not certify
  higher-level reconnect loops, automatic rescheduling after an unknown result,
  HTTP redirects, or budget ownership in every custom game flow.
- R31 focused suites: 307 passed, 14 subtests passed. Full suite: 4025 passed,
  852 subtests passed, 59.65 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r31-miniapp-retry-contract-20260908.xml`. Ruff and diff
  checks pass. The changes are offline candidates only; production, skill,
  live configuration and services remain unchanged.

## Deployment Constraint

The chat-key migration is not a code-only rollback. Once two chats contain the
same message ID for one identity, old loaders collapse those rows in memory.
Any later deployment needs a verified database snapshot and an explicit rollback
procedure; do not run the old code against newly written multi-chat state.
This candidate has not migrated the production database.
Checkin cleanup JSON also changes from bare IDs to chat/message pairs; even
without a same-ID collision, that data must not be handed to the old loader
and cleanup code during a code-only rollback.

## Next Review Priorities

1. R11 follow-through: audit scalar module anchors (`*_msg_id`), business-level
   guard closures without an expected root/chat, and follow-up sends whose
   `reply_to` has no explicit target chat. The shared pending/history contract
   is now tested end-to-end; it does not prove every module's ownership rules.
   Jiyin/quiz/Tianji routing, second-soul heart-demon broadcasts and the
   checkin/teaching, judgement and Nanlong routes are covered; remaining
   second-soul scalar reply guards and wrapped send calls still require review.
   Nanlong's send-in-flight reentry and unknown-send automatic retry are now
   covered by R22, and trusted cross-group unthreaded result recovery and exact
   terminal pending cleanup by R24. Result-before-receipt and late detached
   receipt adoption still need reconciliation before its lifecycle is signed
   off. Passing route tests does not close R07 or R11.
2. R07: establish crash-durable ownership before a send can cross the transport
   boundary, and reconcile an outcome without a message ID. Preserve the
   CommandAttempt shadow-only boundary; a new retry/recovery controller is not
   approved by these fixes. Current source still creates `_GAME_SEND_TASKS`
   receipts only in memory and persists message-keyed pending rows after a
   message ID is available. An action-guard session with attempt zero is
   considered closeable, so it cannot substitute for durable no-ID ownership.
   Forced-stop and reload reproducers must exercise this actual boundary.
3. Continue the full acceptance matrix: module enable/disable and authoritative
   cooldowns, MiniApp mutation/reconnect behavior, persistence capacity, UI
   control contracts and operations. Existing mocks and process uptime cannot
   replace missing real-game evidence or the final integration review.
   Checkin's terminal classification and duplicate scheduling are covered by
   R21, and teaching's real wording, passive-first terminal cleanup/notification
   and monotonic counters by R28. A teaching send with no receipt and no early reply still
   needs the R07 durable-unknown policy, not a claim that its existing timer is
   a confirmed failure.

## Completion Gate

Do not mark the objective complete while any matrix row lacks its required
evidence, a confirmed finding is unresolved, or candidate and deployed behavior
have not been reconciled. Unavailable external-game evidence must be reported
explicitly rather than replaced with a passing mock test.
