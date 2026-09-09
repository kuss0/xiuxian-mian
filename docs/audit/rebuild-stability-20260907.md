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
| Lifecycle | Startup, shutdown, reconnect, and task cancellation preserve pending work and release resources | Supervisor and async lifecycle failure tests; bounded live observation | Candidate shutdown and guarded tower/dwelling thread draining repaired; other flows, reconnect and forced-stop durability review still pending |
| Sending | No duplicate side effects after queue expiry, uncertain send, toggle-off, or cancellation | Reproducers spanning enqueue, await, transport result, and business transition | Identity admission, Nanlong and rift queued-operation checks repaired in candidate; shared no-ID crash durability and other modules' business admission still pending |
| Reply routing | Exact identity/chat ownership; manual actions and edits reconcile once; broadcasts do not establish send health | Cross-chat, multi-account, out-of-order and duplicate-event replay | Shared pending/history routing repaired in candidate; module scalar anchors and final integration still pending |
| Scheduling | Every active module honors its own switch, authoritative cooldown, prerequisites, and mutual exclusion | Module inventory; enabled/disabled and resource-boundary tests | Normal/phaseful and queued fast-due owner invalidation fixed in candidate; module-wide switch/CD and internal-await review still pending |
| MiniApp | Current public entry, bounded reconnect, shared rate limits, isolated sessions; no blind mutation replay | HTTP/browser fault tests; public-entry and scheduler integration tests | Generic HTTP policy, tower/public-entry ownership, stargazer/small-world thread draining and confirmed-result retention, and bounded owner-aware pool leases fixed in candidate; other per-game retry/reentry and current-entry integration still pending |
| Gameplay | Tianxing, duel, retreat, Yinluo/Wanxin, concubine, small world, fishing, tree, tower, trials, and remaining modules close their state transitions correctly | Per-module review and realistic response fixtures, including failure paths | Scoped Tianxing ownership, expiry, calibration, effect chronology, rift dispatch/unknown recovery, scoped result accounting and rebirth lifecycle repaired in candidate; legacy resource/farm/scalar results and remaining modules still pending |
| Persistence | Atomic saves, compatible reloads, bounded history, no secret/test-state leakage | Crash/reload, corrupted-state, retention, and test-isolation checks | Chat-scoped pending/history, delta recovery snapshots and atomic rift-result/inventory commits repaired; shared forced-stop durability and capacity still pending |
| UI/control | Saved settings match runtime behavior; no stale-response overwrite or unintended send; access controls hold | API and browser/control contract checks | Public-entry UI, scoped profile-refresh lifecycle, breakthrough chronology and manual profile API ownership repaired in candidate; remaining profile/resource writers, API/browser and supplemental-read contracts pending |
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
| R32 | High | Requests follows API redirects outside the validated route and request budget, including replaying credential-bearing POSTs; Tiandao accepts 3xx JSON as success | Fixed in candidate; disable automatic redirects in the shared/direct/pooled, World Boss and Tiandao transports, require Tiandao HTTP 2xx success, and verify real local HTTP redirect behavior without reaching the game service |
| R33 | High | Nanlong loses results received before a send receipt, cannot adopt detached receipts, and installs old-account receipts after rebinding; using enqueue time as a result bound can claim an older trade broadcast | Fixed for reproduced interleavings in candidate; exact detached receipt adoption, immediate normal-handler replay, actual dispatch-time evidence, account ownership checks, and SQLite reload are covered; R34 covers queued invalidation, while forced-stop durability remains open |
| R34 | High | Nanlong queued commands still dispatch after module disable, prompt replacement/clear, choice change or expiry; post-send validation arrives too late to stop the game action | Fixed for reproduced boundaries in candidate; a pure synchronous module operation check is revalidated by the existing owner checks and immediately before RPC dispatch; normal steps, recall cleanup and post-dispatch receipt controls pass |
| R35 | High | Tower workers start and write results after identity deletion/replacement/rebinding or switch-off; public-entry awaits continue with stale owners, and tower status falls back to another identity's result | Fixed at queued worker and asynchronous public-entry/session boundaries in candidate; exact owner/account capture, schedule snapshots, guarded fallback and confirmed-result controls pass; R36 covers guarded tower/dwelling threads, not all other game flows |
| R36 | High | Cancelling a tower caller releases public-entry locks while its HTTP thread continues and challenges; module switch-off also permits another in-thread action; missing challenge state is falsely accepted as daily completion, and notification faults lose confirmed completion | Fixed for tower and its dwelling start/details/external path in candidate; cooperative request checks, joined threads, result-carrying cancellation, strict server-state parsing and notification isolation pass full caller/fake-HTTP tests; forced stop and other game flows remain open |
| R37 | High | Pooled HTTP sessions survive identity deletion/replacement/account rebinding; close tears down active sessions and their exclusion locks, while idle entries and locks are unbounded | Fixed in candidate; owner-aware leases retain serial exclusion across route/owner changes, defer active closes, reclaim idle entries with TTL/LRU, and bound active/retired/closing capacity; per-game lifecycle and live validation remain open |
| R38 | High | Local identity invalidation defers shared entry revalidation for six hours; cancelled tasks strand the claim, and old probes overwrite newer claims/entry lists or continue after manual pause | Fixed in candidate; exact owner/entry/claim checks propagate into the loader, cancellation releases only its own claim without changing health evidence, and later scheduler ticks select remaining eligible roles; real server-error backoff is preserved |
| R39 | High | Public-entry UI treats local cancellation/skip as entry recovery, claims before local admission, overwrites newer shared health, and retries downstream failures through another URL | Fixed for reproduced UI caller boundaries in candidate; real loader evidence, exact owner/health snapshots, scoped claim cleanup, no post-read fallback, preserved confirmed results and genuine failure/rate-limit controls pass; per-game workers and remaining UI/background contracts remain open |
| R40 | High | Stargazer releases its caller while an HTTP thread still runs, has no shared per-run budget, loses confirmed collections on later parse failures, and permits duplicate entries or stale results to rewrite a running operation | Fixed in candidate; joined cooperative threads, one run budget, public/manual shared exclusion, owner/choice/schedule admission and partial-result retention pass; notification-time owner replacement cannot return an old result for the replacement role |
| R41 | High | Public-entry background work captures only an identity number; queued actions ignore changed controls, cancellation and local busy results become 30-minute failures, and late completions overwrite newer retry/slot state or mark the next day complete | Fixed for reproduced background-job boundaries in candidate; enqueue-time owner/control snapshots, repeated UI/loader checks, exact in-memory job ownership, cancellation-aware completion and original-day terminal markers pass; downstream unguarded game workers remain open |
| R42 | High | Small-world HTTP-envelope success hides business rejection; raw threads outlive cancellation, stale/incomplete panels become current resource balances, and late results overwrite owners, clocks or newer panels | Fixed for reproduced MiniApp boundaries in candidate; joined cooperative execution, shared per-flow budget, explicit business confirmation, complete snapshot evidence, owner/control checks, guarded cooldown updates and notification-safe persistence pass; legacy command internals and other MiniApp game flows remain open |
| R43 | High | Wild-training workers outlive their owner/configuration/entry, continue with stale Tianxing protection, infer completion from HTTP success, and lose confirmed results on cancellation or notification failure | Fixed for reproduced wild-worker/public-journey boundaries in candidate; original-owner admission, action-time preflight, joined HTTP completion, explicit business evidence, protected result clocks and retained rate-limit evidence pass; shared Tianxing scheduler/craft internals still require review |
| R44 | High | Tianxing timeline lock/queue waits admit invalidated work; old send returns and cancellations overwrite newer plans or confirmations; recovered no-ID sends are rearmed; explicit expired prediction deadlines are extended | Fixed for reproduced timeline boundaries in candidate; transient identity/config/parent checks, exact sending snapshots, receipt-owned persistence, strictly unsent retry classification, chat-scoped guard closure and authoritative expiry pass; outer auto/farm callers and disabled-module reducer reconciliation still require review |
| R45 | High | Outer Tianxing schedulers use stale identities/configuration after lock/child waits; automatic receipts reopen completed pending work or overwrite new clocks; pause/resume clears dispatched auto pending | Fixed for reproduced outer automatic/daily/follow-up boundaries in candidate; captured controls, post-await auto-state comparisons, queued-plan checks, exact pending receipt ownership and pause-preserved pending pass; unknown auto-send retries, craft/retreat internals and other reducer anchors remain open |
| R46 | High | Automatic Tianxing clears unknown sends and expired pending, accepts malformed receipts, and closes work on action name alone; timeline/downstream paths can bypass that unresolved action | Fixed for reproduced automatic evidence boundaries in candidate; explicit-unsent classification, retained unknown operations, exact receipt/log correlation, dispatch-time bounds, bounded early-result deduplication and pending-aware route admission pass; legacy no-ID work without authoritative evidence, general reducer freshness and craft/retreat internals remain open |
| R47 | High | Craft farm and prediction-consumption callers can overlap, retry uncertain sends, overwrite early results or new work, and mistake panel calibration for craft completion; business-denial backoff is ignored | Fixed for reproduced craft lifecycle boundaries in candidate; shared operation lock, pre-dispatch ownership/evidence checks, persisted unknown pending, strict existing-receipt adoption, independent craft/calibration anchors, idempotent last-result handling and preserved business backoff pass; retreat lifecycle, general reducer ordering and R07 durability remain open |
| R48 | High | Ordinary retreat and its material chain do not claim pending work before sending; scheduler ticks erase reply-wait phases, panel replies complete unrelated work, missing potions loop, and local guard delays lose the intended next action | Fixed for scoped candidate operations; shared farm transport/receipt code, retreat serialization, strict command/quantity/result ownership, retained unknown work, separate calibration, bounded denial/unsent retries and force-exit caller propagation pass; legacy unanchored farm state, general reducer ordering and shared R07 durability remain open |
| R49 | High | Tianxing guard cleanup treats cached fields as fresh command evidence, closes unrelated chat/account/action sessions, and accepts late or partial panels; generic routed/passive cleanup bypasses module checks | Fixed for the six direct Tianxing families in candidate; exact reply/session ownership, captured sending account, explicit parsed outcomes, original panel dispatch ordering and expected root/chat closure pass through both dispatchers and SQLite replay; farm-family cleanup, general observation ordering and R07 durability remain open |
| R50 | High | Empty Tianxing panels, craft-start acknowledgements and zero-quantity results clear pending work before the final edit; an early result followed by its transport receipt loses business completion or leaves a new guard stranded | Fixed in candidate; shared terminal classification preserves incomplete work, exact late receipts close once without repeating reducers, farm/material guards use the module evidence contract, and malformed pending/context cases fail closed; general reducer ordering and R07 durability remain open |
| R51 | High | Observation normalization deletes prediction-consumption evidence based solely on the latest action label, resurrecting an old effect and permitting downstream release after restart | Fixed in candidate; normalization preserves consumption and only a strictly newer prediction timestamp supersedes it; real unrelated replies, read-only status, route admission and SQLite reload are covered; parsed-result provenance and chronology remain open |
| R52 | High | Auto/craft/retreat replies update authoritative Tianxing fields before validating the pending operation, so rejected roots/chats/accounts/arguments still change effects, resources and timestamps | Fixed for pending auto/farm operations in candidate; ownership and terminal-command checks run before observation writes, including exact integral references and early receipt adoption; active timeline provenance and general post-completion ordering remain open |
| R53 | High | Active Tianxing timeline steps confirm from merged cached fields, accept unowned negative replies, and recover a nearby bot reply without an actual reply root | Fixed for active-step reply confirmation in candidate; operation receipts precede state writes, direct and panel-query evidence is correlated, early results replay from bounded trusted logs, and partial calibration fields remain pending; downstream release/calibration shortcuts and general reducer chronology remain open |
| R54 | High | Effect deadlines include retry padding or invented lifetimes; cached release/observation fields bypass unresolved calibration, while partial effect replies lose native pending/guard ownership and late originals cannot settle after calibration timeout | Fixed for scoped expiry, downstream admission and retained-operation reconciliation in candidate; exact effect clocks, owned complete panels, partial/final native replay, late success/CD/refusal and SQLite reload are covered; general field provenance and post-completion chronology remain open |
| R55 | High | Native effects use local receipt time, stale replies overwrite newer effects, route results acknowledge unrelated mutations, and replay batches select revisions by arrival rather than server order; calibration helpers bypass effect provenance | Fixed for scoped native event/replay paths in candidate; server timestamps, per-effect evidence, genuine versus repeated edits, corruption repair, retained mutation ownership and SQLite replay are covered; counters/resource deltas and legacy route-result log readers remain open |
| R56 | High | Legacy rift recovery trusts a bare message ID and log arrival time, accepts unrelated/untrusted replies, and replays stale edits or old commands as fresh work | Fixed for the scoped log readers in candidate; owned command/root/chat correlation, trusted server-timed revisions, ambiguity rejection, bounded reads and SQLite pending reload are covered; scalar live anchors, uncertain-send retry policy and panel-only outcome inference remain open |
| R57 | High | Rift timeouts erase in-flight ownership and later resend; cached panels falsely prove execution or non-execution; uncertain query receipts and UI/startup resets can revive completed or replaced work | Fixed for timeout/query/recovery boundaries in candidate; retained unknown work, bounded owned replay, one-shot persisted queries, native late result/CD handling, account/operation checks and pause/reload retention pass; original rift dispatch awaits, legacy scalar anchors and post-completion resource idempotence remain open |
| R58 | High | Original rift dispatch is not persisted before transport, overwrites early replies with late receipts, admits invalidated queued work and can adopt another manual command; Tianxing loses unresolved-rift exclusion when its release lease expires | Fixed for new original-rift dispatch operations in candidate; saved intent, immutable operation ownership, current business/effect admission, strict receipts, cancellation/reload, early results and exact-operation log recovery pass; legacy scalar reducers, rebirth and shared transport durability remain open |
| R59 | High | Rift final-result deduplication remembers only the last text hash; interleaved results and edits repeat rewards or regress cooldowns, same IDs collide across chats, completion and inventory save separately, and escape handling writes after an awaited notification | Fixed for new scoped rift-result evidence in candidate; bounded command receipts, ordered revisions, atomic completion/inventory saves, rollback/reload, notification cancellation and native replay pass; pre-migration accounting, legacy scalar ownership and rebirth operations remain open |
| R60 | High | Rebirth request/select operations lack durable ownership before send, retry unknown requests, overwrite early outcomes, accept unrelated prompts and can block later deaths or reopen an already restored body | Fixed for new scoped rebirth operations in candidate; saved intent, strict receipts/parent ownership, cancellation/reload, known-unsent backoff, one-shot blind choice, authoritative server auto-choice, new-death admission, late-edit rejection and paused native replay pass; unowned legacy operations and shared R07 transport durability remain open |
| R61 | High | Second-soul replies rely on scalar IDs, lose early results, repeat uncertain purge spending, overwrite newer cultivation after manual reads, and cross account/lifecycle boundaries; old warnings renew their choice window and passive handling bypasses direct checks | Fixed for scoped new operations in candidate; persisted command ownership, one direct/passive reply contract, strict native/replay clocks, bounded unknown recovery, manual/automatic coexistence, owner-aware UI reads and choice admission pass; full suite 6621 passed/1198 subtests, while legacy evidence/shared R07 remain open |
| R62 | High | Explicit profile refresh reenters before its first receipt, loses early cards, accepts wrong-chat/account/stale replies, clears unrelated pending work and lets followup/retry awaits write through changed requests; passive cards can bypass routing and overwrite newer observations | Fixed for scoped profile requests in candidate; bounded request/command ownership, native early replay, one owned retry, guarded cleanup, per-field server clocks and temporary-SQLite reload covered; remaining profile writers, supplemental module flows and shared R07 stay open |
| R63 | High | Breakthrough broadcasts and manual profile API writes bypass field chronology, match username prefixes or ambiguous fallback owners, and apply late responses after identity/credential changes; same-second cards can regress request payloads and evict their own evidence | Fixed for these profile observations in candidate; native new/edit provenance, bounded per-field source order, exact identity/request/config ownership, conservative API freshness, corruption/reload and same-second request/reply retention pass; resource reducers, other API workflows and shared R07 remain open |

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
- R32 local HTTP reproducer: 30 redirect cases failed before the fix and five
  ordinary-success controls passed. Direct, supplied-session, pooled, World
  Boss and Tiandao transports followed 302/307/308 redirects, including to a
  hostname outside the adapter's allowlist; 307/308 preserve the POST payload.
  The hidden requests bypass the caller's rate/budget accounting. Tiandao's
  `Response.ok` check also treats 3xx JSON with `ok=true` as success.
- All four MiniApp Requests call sites now disable implicit redirects. The
  original 3xx reaches the protocol failure path; no new hop is attempted, no
  token/initData is forwarded, and no redirect is claimed as a settlement.
  Both same-origin and other-host redirects are covered, with exactly one
  observed POST per invocation. Tests use a loopback-only HTTP server and
  close its worker, sockets and isolated session pool after each case.
- R32 focused transport/core/runtime/routing suites: 225 passed, 24 subtests
  passed. Full suite: 4060 passed, 852 subtests passed, 61.11 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r32-miniapp-redirects-20260908.xml`. Full Ruff and diff
  checks pass. No production request, configuration change, deployment,
  service restart, push or skill edit was performed.
- R33 initially reproduced four failures: an unthreaded result preceding its
  normal receipt remained pending, a detached receipt was not adopted, a late
  receipt without a result could not enter an explicit no-resend wait, and
  an account rebind during sending still installed the former account's receipt.
  The expanded time-bound review then reproduced two additional failures:
  ordinary and detached paths both accepted an older trade broadcast received
  after enqueue but before the command actually started sending.
- Nanlong sends now attach existing source/chain/op metadata containing the
  identity, account, original prompt, command, retry round and local attempt
  timestamp. Only one exact matching detached pending receipt can be adopted;
  missing metadata, conflicting ownership/routes/timestamps and duplicate
  candidates are not guessed. After adoption, the exact chat/message reference
  owns that receipt. A receipt without a result stays no-resend and emits one
  warning while bounded log checks continue; changing the choice cannot rearm it.
- Runtime already receives a send-start timestamp; it now records the actual
  RPC dispatch instant and exposes it on the returned receipt and existing
  pending recovery JSON. The receipt's `sent_at`, retry deadlines and business
  CD calculations are unchanged. No new table, sending fence, outbox or
  CommandAttempt control path was added. Nanlong uses dispatch time to reject
  pre-send broadcasts while retaining genuine results earlier than the receipt.
  Legacy receipts lacking dispatch evidence use the conservative receipt-time
  bound, not the earlier enqueue timestamp.
- The complete protected `place -> exchange -> recall` chain is replayed with
  a real temporary message log: each result arrives before its receipt, each
  command occurs once, and duplicate edits do not restart the chain. Separate
  tests cover late place/recall/reject receipts, caller cancellation, exact
  cleanup, module disable/clear, account rebinding during receipt/notification,
  one-warning no-resend waits, and a real SQLite save/reload/adopt/complete/reload
  cycle. Simulated timestamps in receipt tests are isolated from the process's
  Bot-health clock; the first combined run exposed that test-state leak, which
  was corrected without relaxing production health or send guards.
- R33 focused lifecycle/persistence/runtime suites: 166 passed, 91 subtests
  passed. Full suite: 4075 passed, 879 subtests passed, 60.10 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r33-nanlong-receipts-20260908.xml`. Full Ruff and diff
  checks pass. Production, live configuration, services, skill and remote
  branches remain unchanged. This does not close R07, queued business
  admission checks, capacity review or the whole-project acceptance matrix.
- R34 reproduced 15 actual fake-client transport invocations for invalidated
  Nanlong operations. Each of disable, new prompt, clear, choice change and
  expiry was injected during entity preparation, an awaited shared guard, and
  the gap after RPC-task creation but before dispatch. Earlier module
  post-await checks prevented stale state writes but did not prevent the send.
- The legacy send API now accepts an optional pure synchronous
  `operation_check`, outside serialized intent, pending data and Attempt.
  Existing identity-owner validation calls it at preparation/queue boundaries
  and immediately before dispatch. Only literal `True` permits sending;
  exceptions, non-boolean values and awaitables are definitely-unsent failures.
  Rejected coroutine results are closed. No new persistent fence, recovery
  controller, retry behavior or CommandAttempt control path was introduced.
  Callers that omit the check retain existing behavior.
- Nanlong supplies its operation snapshot, enabled flag, current choice and
  real prompt deadline for placement/exchange/reject sends. Recall still
  checks ownership and module enablement but does not depend on an expired
  trade prompt or a later choice edit. The real runtime/fake-client tests also
  prove all four normal steps can dispatch, disable blocks all four, and a
  choice edit after dispatch does not discard the original command's receipt.
  Unknown-send and detached-receipt behavior remains covered by R33.
- R34 focused suites: 170 passed, 122 subtests passed. Full suite: 4079 passed,
  910 subtests passed, 59.09 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r34-send-operation-admission-20260908.xml`. Full Ruff
  and diff checks pass. The separate isolated SIGKILL probes for checkin and
  rift still report `safe=false` and exit 1: the first worker saves zero
  pending receipts after sending, and the reload worker invokes transport
  again. R34 is not a crash-durability fix. No production state, service,
  skill, configuration or remote branch was changed.

- R35's initial worker reproducer failed in 19 owner-invalidating interleavings
  and one cross-identity status case. Resetting the frozen-clock rate-limit
  fixture removed accidental five-second waits without removing any failure.
  Another 30 public-entry/session interleavings reproduced continued requests,
  stale result writes or notifications after deletion, replacement or rebind.
- Tower tasks now capture the identity object, account and existing scheduling
  fields before task creation. They recheck after lock/gap waits, before entry
  fallback and before failure backoff. A later timer edit or passive completion
  invalidates the old operation; another scheduler tick cannot renew a running
  task's lease. The shared public-entry loader rechecks ownership after initData,
  initial/selected start and details, before caching a directory. Tower's public
  wrapper rechecks between flows and before recording results. These are
  in-memory checks, not a persistent send fence or an Attempt controller.
- Automatic tower calls supply an operation check; explicit manual calls do
  not depend on `tower_enabled`. Channel-send-only freezes and the existing
  Tianzun-maintenance exception still permit MiniApps. A confirmed result for
  the same owner is retained after switch-off/pause; a replaced or rebound
  identity never receives it. Status no longer borrows another identity's row,
  including when the explicit identity context was deleted.
- R35 focused suites: 144 passed, 92 subtests passed. Full suite: 4094 passed,
  997 subtests passed, 59.20 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r35-tower-owner-20260908.xml`. This does not establish
  in-thread cancellation safety: the existing `asyncio.to_thread` flow can
  still outlive its awaiting caller or a switch change during a pooled/global
  limiter wait. That next review remains required before MiniApp lifecycle
  acceptance. No production state, service, skill or remote branch changed.

- R36 reproduced three real-thread failures with a fake Requests session:
  cancelled callers finished and released public-entry locks before the HTTP
  request returned, disabled operations still dispatched challenge after start,
  and cancellation discarded an already-completed challenge's daily fact.
  A later notification-error injection also reproduced completed gameplay
  becoming a retry. All tests use isolated state and no external game traffic.
- The generic HTTP kernel and pooled transport accept an optional strict
  synchronous operation check, before and after budget/global-limiter waits
  and after pool-lock acquisition. Invalid checks are definitely unsent and
  cannot invoke retries; preparation faults are separate from dispatched
  request errors. Stopping during retry backoff retains the preceding HTTP
  evidence instead of declaring that earlier request unsent. The global
  90/minute limiter and explicit replay-safety permission remain in place.
- Guarded blocking flows preserve ContextVars, signal cancellation to waits
  and future requests, and join the actual executor Future before releasing
  their caller's locks. Repeated cancellation does not detach the thread.
  Already-dispatched HTTP is allowed to return under its existing transport
  timeouts, not force-killed or replayed. A result-carrying CancelledError lets
  tower persist a confirmed result and daily fact for the same owner, then
  propagate cancellation. This is not a persistent outbox, Attempt controller,
  or SIGKILL recovery mechanism.
- Tower and its public-entry start/details/external requests now use the guard.
  Tests cover all three dwelling cancellation boundaries, start-to-challenge
  switch-off/rebind, pool and global-priority waits, repeated cancellation,
  notification cancellation/failure, and the complete successful
  dwelling-start -> details -> external -> tower-start -> challenge chain.
  The full-chain test verifies selected player IDs, unchanged initData and
  exactly one completed tower record plus the daily scheduling fact.
- The cancellation replay exposed a separate parser flaw: an empty HTTP result
  became a default state with `canChallenge=false` and was treated as done.
  Tower now requires an explicit boolean server flag; missing/malformed state,
  a lost challenge response and budget exhaustion cannot fabricate completion.
  Start and challenge share one request budget. Diagnostic notification errors
  preserve the returned gameplay result and log only the exception class.
- R36 focused verification: 121 passed, 94 subtests passed. Final full suite:
  4119 passed, 1004 subtests passed, 64.71 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r36-miniapp-thread-lifecycle-verified-20260908.xml`.
  Full Ruff, Python compilation and diff checks pass. One added full-chain
  assertion initially expected an integer external `playerId`; the existing
  builder deliberately uses a string for external and an integer for details.
  The test now verifies those exact existing contracts; protocol code was not
  changed to satisfy that assertion. Production, skill, switches, DB and remote
  branches remain unchanged.

- R37 replaces the private acquire/is-current protocol with scoped pool leases.
  Transport creation retains the exact identity object/account; a removed,
  replaced or rebound owner cannot dispatch later requests or reuse the old
  owner's session. Profile/business-field changes still reuse the same owner's
  session. Unregistered protocol-test identities cannot keep using a transport
  after that ID becomes a registered role.
- Pool slots count active and queued lease holders, so close, invalidation and
  proxy changes cannot replace the identity lock until every holder exits.
  Retiring an active session delays physical close until its request returns.
  Idle sessions are reclaimed lazily on checkout or explicit prune after 30
  minutes, with idle LRU eviction at the default 256-entry capacity. Active,
  retired and currently-closing resources all count against that limit; a full
  active pool rejects before HTTP rather than evicting an active connection.
  Close runs outside the pool metadata lock. Init/close exceptions release
  metadata and do not print credential-bearing exception text.
- R37 verification covers real worker threads with fake HTTP sessions: expiry
  at the exact TTL boundary, TTL from release rather than checkout, idle LRU,
  all-active and retired capacity, blocked close with and without remaining
  capacity, init/close exceptions, proxy-switch serialization, cancellation
  while queued, identity churn and owner changes during an in-flight request.
  The two existing tests that used the removed private acquire API were migrated
  to leases and behavioral concurrency assertions, not a compatibility shim.
  Focused suite: 189 passed, 17 subtests passed. Full suite: 4140 passed,
  1004 subtests passed, 67.13 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r37-session-lifecycle-20260908.xml`.
  Full Ruff, changed-file compilation and diff checks pass. Tests remain offline
  and use temporary state. No skill, production, live DB or remote branch change.

- R38 reproduced 14 failing cases and four passing controls before the fix.
  A real loader interrupted by deletion/replacement/rebind/disable changed the
  shared retry timestamp from an already-due time to now + six hours and replaced
  the token-expiry reason with a local cancellation message. Manual pause during
  initialization still reached HTTP. Cancelled tasks retained a 20-minute claim,
  and stale results could clear or worsen a newer claim/entry generation.
- Entry probes now retain the identity owner, configured URL signature, blocked
  signature and existing canary timestamp. They revalidate these boundaries in
  the guarded loader and before publishing the result. A local cancellation only
  clears a claim belonging to this exact scheduler tick; it does not reset the
  failure counter, rewrite the health reason, move the health retry timer or
  mutate a replacement claim. Task cancellation still propagates. No new
  persistent controller or claim schema was introduced.
- Scheduler integration reports cancellation separately rather than a started
  failed probe. A two-tick test runs the real scheduler/probe/loader path: the
  first identity is disabled during initialization, then a different eligible
  identity revalidates the same entry on the next tick, without a same-tick
  fanout. Real token-expired and HTTP 502 controls keep their existing backoff;
  channel-only freeze plus Tianzun maintenance still permits MiniApp HTTP.
  A follow-up ownership test also reproduced and fixed using another tick's
  pre-existing claim. Focused suite: 244 passed, 8 subtests passed. Full suite:
  4160 passed, 1004 subtests passed, 68.42 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r38-entry-probe-lifecycle-20260908.xml`.
  Full Ruff, changed-file compilation and diff checks pass. All verification is
  offline, with temporary state and no production/skill/remote changes.

- R39 initially reproduced 23 failing public-entry UI cases and three passing
  controls. A cancelled or locally skipped callback cleared shared token/circuit
  health; busy and invalid actions stranded a canary, while removed/rebound
  identities and changed entry lists still reached fallback or shared writes.
- UI action dispatch is now lazy and validated before claiming a canary. It
  retains the real identity/account owner and rechecks admission after lock
  acquisition, inside dwelling loading, after the callback and before fallback.
  Manual global pause still blocks new requests, while channel-only freeze and
  Tianzun maintenance continue to allow eligible public-entry HTTP.
- An in-memory entry-health snapshot compares configured URLs, claim timestamp,
  blocked signature and existing failure fields. It is shared with R38's probe
  path and adds no persistent control schema. Cancellation and callback faults
  release only the original claim; old results cannot clear a replacement
  claim, a newer failure on the same URL, or a newer upstream circuit.
- Task-local read observations bind the expected identity/token and operation
  check to the real dwelling loader. Business success wording alone is not
  entry-health evidence. Once a dwelling read is verified, downstream failure
  does not replay the business chain through another URL. A local skipped URL
  is not counted as expired, and manually testing an unrelated URL or just one
  configured candidate cannot establish that the full configured list expired.
- Confirmed same-owner outcomes survive a post-dispatch pause/disable without
  further requests or shared-health writes. Fate-card notification faults no
  longer discard confirmed rewards, and logging exposes only exception class.
  Real HTTP 502 controls still open the upstream circuit, genuine canary server
  failures retain the six-hour retry, and shared 429 deadlines stop fanout and
  remain monotonic. Successful dwelling reads cannot conceal a later HTTP 502.
- The dedicated lifecycle suite now has 45 cases. Legacy UI fixtures now
  register actual owners; the all-expired test now supplies two real token
  failures instead of incorrectly treating a local throttle as the second one.
  Manual-only actions remain excluded from the batch allowlist. Related suite:
  174 passed, 45 subtests passed. Final full suite: 4205 passed, 1004 subtests
  passed, 68.71 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r39-public-ui-lifecycle-20260908.xml`.
  Full Ruff, changed-file compilation and diff checks pass. No production,
  live configuration, service, skill or remote branch was changed. This does
  not close R07, R11 or the remaining per-game lifecycle/final-review work.

- R40 first reproduced 32 failing lifecycle cases and two passing controls.
  Stargazer now uses the existing guarded blocking-flow runner rather than raw
  `asyncio.to_thread`. The start and every soothe/collect/pull request share one
  request budget. Every WebView await and HTTP dispatch checks the current
  operation; cancellation joins the worker, including repeated cancellation,
  before releasing either the public-entry or game-specific lock.
- The in-memory operation captures the exact identity/account, module switch,
  selected star and four existing scheduling fields. Public and command-entry
  callers share the same game lock. A duplicate entry is rejected before any
  legacy-chain or timer modification. A fresh timer, changed star, pause,
  deletion or account rebind stops further requests and cannot be overwritten
  by the old operation.
- Confirmed collections survive a later missing farm snapshot, exhausted
  request budget, transport failure, cancellation or switch-off. Same-owner
  partial rewards are recorded once without overwriting new scheduling. A
  replaced owner receives neither those deltas nor an old result. Notification
  failures log only their exception class and do not discard results; actual
  cancellation still propagates, carrying a confirmed result only while its
  identity/account owner remains current.
- Follow-up review reproduced the duplicate-entry scheduler corruption and
  six public notification-time ownership cases. The latter and equivalent
  manual-path cases are covered after the fix. The four WebView tests initially
  omitted the required launch URL; their fixtures were corrected to supply a
  valid Telegram URL, without relaxing production launch validation.
- R40 verification: the new lifecycle file contains 60 cases. Related suite:
  484 passed, 22 subtests passed. Full suite: 4265 passed, 1004 subtests passed,
  72.76 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r40-stargazer-lifecycle-20260908.xml`.
  Full Ruff, changed-file compilation and diff checks pass. All HTTP and
  Telegram boundaries are fake test transports; no production, skill, live DB,
  configuration, service or remote branch was changed. Full project acceptance
  remains open.

- R41 reproduced 22 failing background-job cases and two passing controls.
  An in-memory operation now retains the exact identity/account, relevant
  automatic and module controls, entry URLs, schedule fields, game day and
  existing retry value. Admission is rechecked after enqueue; the same control
  predicate reaches the public UI and guarded dwelling loader. Removing or
  replacing an identity, rebinding its account, changing its star/selection,
  pausing, disabling automation, replacing an entry or rescheduling cannot
  start stale work. Unrelated configuration saves do not cancel the operation.
- Only the owning job releases its running slot, including cancellation before
  the coroutine first executes. Task-creation failures close the unused
  coroutine. A replaced operation for the same identity/action is also distinct;
  the old job cannot clear the new slot or its status. No persistent claim,
  outbox or recovery controller was introduced, and CommandAttempt remains
  shadow-only.
- Cancellation without an outcome no longer writes a 30-minute failure retry.
  A late failure cannot overwrite new scheduling or shorten another retry
  deadline. Confirmed terminal markers remain attached to the original owner
  and operation day, including result-carrying cancellation; completion after
  midnight cannot mark the next day done merely from the worker's local clock.
  Actual server rate limits still propagate globally after owner invalidation,
  remain monotonic and keep the normal send-spacing constraint.
- Follow-up real-UI tests reproduced three additional false-failure cases:
  the UI lock, public identity lock and stargazer game lock becoming busy after
  enqueue. They now wait without an identity failure retry; the game-specific
  busy result has a structured status and is not logged as a failure.
- R41 verification covers all nine background action switches, explicit
  fishing/Tianti selections, manual deep-retreat rescheduling, pre-start task
  cancellation, same-key operation replacement, real UI-to-dwelling-loader
  invalidation and secret-free exception diagnostics. Channel freeze plus
  maintenance still permits public HTTP, and explicit UI requests remain
  independent of the automatic switch. Five older worker fixtures now construct
  a valid admitted operation instead of bypassing identity and control setup.
  New lifecycle suite: 59 cases. Related suite: 414 passed, five subtests passed.
  Final full suite: 4324 passed, 1004 subtests passed, 71.90 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r41-background-lifecycle-final-20260908.xml`.
  Full Ruff, compilation and diff checks pass. Production, services, skill,
  live DB/configuration and remote branches remain unchanged.

- R42 first reproduced 36 failing small-world lifecycle cases. Follow-up tests
  caught incomplete resource domains becoming zero balances, stale nested
  envelopes hiding an action snapshot, early cooldowns after a slow loader,
  and cancellation during WebView initialization being labelled a failure.
  The flow now uses the existing joined blocking runner and one request budget
  for its start/action steps, reuses the loader's initial snapshot, checks every
  Telegram initialization await, and never retries the spending action. HTTP
  events and Retry-After survive start, selected-player and details failures.
- An action requires explicit `actionResult.ok == true` and no explicit
  incomplete result. Transport success alone, missing receipts and explicit
  foreign-player replies cannot establish completion. Confirmed receipts remain
  available when the following panel is absent or malformed. The action payload
  protocol is unchanged: no unverified player-selection field was introduced.
- Full resource snapshots require finite nonnegative population, faith,
  stability, stock and uncollected-incense balances. Missing domains no longer
  manufacture a zero-resource world, and explicit zero values cannot fall back
  to stale legacy fields. An incomplete initial panel does not reach the
  spending planner or start repeated prayer refreshes. Missing post-action
  panels preserve old numbers but invalidate their existing cache timestamps,
  so command-side cached decisions cannot treat them as current facts.
- The public caller retains the exact owner/account, existing module controls,
  schedules, panel and MiniApp record in memory. It checks them after loading
  and before later HTTP dispatch; it does not overwrite a replacement identity,
  a newer record/panel, or newly edited six/eight-hour deadlines. Concurrent
  public calls and an already-pending command chain return local busy before
  modifying the minimum-request timer. This is not a durable fence, outbox or
  recovery controller, and it does not replace command-side reducers.
- A confirmed harvest retains its eight-hour completion clock through task
  cancellation or switch-off, while independently edited clocks remain intact.
  Cooldowns use elapsed completion time rather than a potentially old loader
  start time. Server prayer waits take precedence over stale prayer presence;
  a partial manifest result cannot schedule an immediate re-manifest. Ordinary
  maintenance stays six-hourly, harvest stays eight-hourly, and optional refresh
  stays ten-minute/max-five with the persisted ten-minute admission interval.
  The 1150 reserve and disabled automatic incense-to-consciousness choices are
  unchanged. Explicit manual UI runs, maintenance and channel-freeze HTTP
  availability remain supported.
- State snapshots and confirmed-action metadata are saved together before
  notification. A failed notification cannot lose a completed harvest; a
  cancelled notification carries the completed result without replaying it.
  These metadata fields use the existing bounded MiniApp snapshot record and
  are not read as an automatic recovery authority. New lifecycle suite:
  73 cases, including a real caller-to-thread cancellation/lock test and real
  fake-HTTP loader rate-limit tests. Final full suite: 4397 passed, 1004 subtests
  passed, 71.89 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r42-small-world-20260908.xml`.
  Full Ruff, compilation and diff checks pass. No production, service, skill,
  live DB/configuration, remote branch or gameplay action was touched.

- R43 first reproduced 35 failing wild-training lifecycle cases. Subsequent
  fault tests covered current-entry changes, malformed optional fields and
  explicit invalid player IDs; they also caught a NaN timer that could never
  pass its ownership check. The final focused suite has 88 cases, including
  real worker-to-HTTP cancellation, queued-owner invalidation, and normal
  authoritative-cooldown controls. Related wild/cave suites: 230 passed,
  5 subtests passed.
- Wild workers capture the existing identity/account, controls, schedule and
  entry list before queuing. They recheck after their serial wait and Tianxing
  preparation, during public-entry loading and at the actual HTTP boundary.
  Done callbacks remove only their own task; failed task creation closes its
  coroutine. Late errors cannot enter a deleted/replaced identity or overwrite
  independently edited deadlines. These are in-memory ownership checks, not
  new durable fences or a CommandAttempt recovery controller.
- Journey HTTP now uses the existing cooperative thread runner and a bounded
  request budget, retaining HTTP status, attempts and Retry-After. Cancellation
  joins the in-flight thread before releasing public/worker locks and carries
  any returned business result. Entry fallback is limited to explicit expired
  entry evidence before the journey action; timeouts, missing panels, action
  errors and rate-limited responses cannot trigger another URL/action.
- Explicit `actionResult.ok == true` and no explicit incomplete result are
  required for completion. Missing/foreign/malformed identity evidence cannot
  publish another player's resources or cooldown. A selected request's valid
  partial receipt can complete without a repeated identity block; it does not
  turn the pre-action panel into a current post-action snapshot. Missing journey
  domains and invalid counters do not authorize actions. Malformed optional
  loot/mode fields cannot erase a confirmed result. Identical rewards from
  distinct confirmed runs have distinct inventory evidence keys.
- Confirmed outcomes persist across cancellation and switch-off; independently
  edited clocks and newer MiniApp records remain intact. Cooldowns use the
  completion time and server evidence, with no invented 12-hour rule. Explicit
  HTTP rejections retain Retry-After without being mistaken for Tianxing
  consumption. Notification failures cannot convert completion into a gameplay
  retry. Existing cautious/balanced/deep choices, cautious low-Tianji fallback,
  daily spread and serial admission remain in place.
- The HTTP preflight checks current prediction expiry/consumption and current
  strategy after loading, not only before its awaits. Explicit expired
  prediction timestamps cannot be extended by the shared preflight's inferred
  lifetime at this boundary. Late receipts do not consume newly observed
  predictions. Tests confirm that active, near-expiry and expired deep retreat
  do not block or consume Tianxing effects. This does not yet establish the
  internal lifecycle correctness of `run_tianxing_timeline_scheduler` or
  `run_tianxing_consume_craft_prediction`: their own queued sends, await-time
  snapshots, no-ID recovery and disabled-module result reconciliation remain
  the next targeted review. No production or live-game verification was run.
- R43 final full suite: 4485 passed, 1004 subtests passed, 71.58 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r43-wild-training-20260908.xml`.
  Full Ruff, compilation and diff checks pass. The changes remain offline and
  local; the production tree, live state/configuration, services, skill and
  remote branches were not touched.

- R44 first reproduced 30 failures in 34 timeline lifecycle cases. Follow-up
  fault tests exposed unclassified send errors being retried, malformed message
  IDs being accepted, expired release evidence, and a sending-save failure
  whose in-memory mutation obscured the reported outcome. The final lifecycle
  suite has 68 cases, plus 21 real-runtime subtests spanning entity resolution,
  awaited pre-send guards and the actual RPC dispatch boundary. All transport
  clients are fakes; no game service was contacted.
- Timeline operations retain the identity object/account and normalized
  controls before waiting for the existing lock. A parent operation check
  propagates the wild-worker lifetime into queued Tianxing preparation. The
  existing runtime revalidates the exact plan/step and current manual-action
  prerequisites before dispatch. Dry-run/action-off changes and expired
  deadlines cannot execute an already-queued plan. No new durable fence,
  outbox or CommandAttempt recovery controller was added.
- Send results are applied only to their original plan, index and complete
  sending snapshot. Early confirmations, replacement identities/plans and
  independently advanced farm bookkeeping survive both late receipts and
  cancellation. Actual receipts remain recorded after a post-dispatch switch
  change. Callers no longer blindly store a returned stale timeline. Sending
  state must save successfully before the transport is called.
- No-ID recovered sending, malformed IDs, arbitrary exceptions and unknown
  block codes stay in the existing acknowledgement-timeout/calibration path.
  Only explicit unsent evidence permits a bounded retry, with backoff measured
  after the await. An isolated SQLite save/reload test confirms no rearming of
  unknown sends. Timeline cleanup requires the exact message and chat; without
  evidence it cannot close a newer family guard. This does not close R07's
  shared forced-stop problem or R11's other scalar anchors/observation cleanup.
- A positive server prediction expiry is authoritative even after it passes.
  Missing-expiry legacy inference is retained. Releasing downstream now checks
  current unconsumed, fresh effect evidence; old positive test fixtures were
  completed with the prediction timestamps their assertions require. Deep
  retreat remains unrelated to Tianxing effect consumption or admission.
- R44 related tests: 500 passed, 81 subtests passed. Final full suite:
  4554 passed, 1025 subtests passed, 70.91 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r44-tianxing-timeline-20260908.xml`.
  Full Ruff and diff checks pass. The candidate is not deployed; production
  files/state/configuration, services, skill and remotes remain untouched.

- R45 first reproduced 46 failures in 64 automatic lifecycle cases. Follow-up
  tests cover inactive child returns, post-craft waits, real pause/resume and
  real-message early replies. The final focused suite has 84 cases; another
  21 real-runtime subtests exercise automatic commands after entity resolution,
  awaited guards and immediately before RPC dispatch. Fake transports only.
- Automatic, daily-bootstrap and timeline-follow-up entrypoints capture the
  original owner/account/configuration and control values before their shared
  lock. Existing module-off/unavailable/paused handling is preserved for work
  admitted in that state; a change while waiting cancels the old operation.
  Timeline draining rechecks its parent lifetime between steps. Outer callers
  reload observations after child awaits and do not overwrite newer automatic
  clocks or pending/plan metadata, even when the child returns inactive.
- Automatic commands preclaim their existing pending fields and require a
  successful save before queueing. Their in-memory operation check validates
  the current plan and observation at actual dispatch. Late receipts require
  the original pending action/command/message/chat/timestamps and identity,
  preserving early replies and replacement work. Metadata is merged only where
  it is still unchanged; actual receipt facts survive post-dispatch switch-off
  and a newer manual clock. No CommandAttempt control transition was made.
- Pausing, resuming and paused scheduler ticks no longer erase dispatched
  automatic pending work. An actual pause during send keeps the eventual
  receipt as well as the pause deadline/status. Automatic pending records now
  include the receipt chat; anchored log recovery uses that chat or an
  unambiguous stored receipt, and refuses an unknown route instead of assuming
  the currently selected group. Other unthreaded recovery paths still need R11
  review; this is not a claim that all Tianxing replies are fully attributed.
- R45 related tests: 585 passed, 102 subtests passed. Final full suite:
  4639 passed, 1046 subtests passed, 73.10 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r45-tianxing-auto-20260908.xml`.
  Full Ruff and diff checks pass. The existing automatic no-receipt failure
  branch and pending-timeout retry policy remain unsafe for uncertain
  resource-spending actions and are not certified by these ownership tests.
  Craft-consumption/craft-farm/retreat-farm callees also remain to be repaired;
  guarding their caller does not validate their internal sends. Production,
  services, live state, skill and remote branches were not changed.

- R46 distinguishes a normal explicitly unsent result from an unknown send.
  Unknown block codes, missing block evidence, exceptions, cancellation and
  malformed message IDs retain the original automatic pending work. Timeout
  does not rearm a mutation, including after repeated ticks and SQLite reload.
  Read-only panel/observe queries keep their existing bounded retry policy.
  Mutation log recovery runs only when its existing due time expires, with a
  24-hour maximum lookback and a 30-minute interval between unresolved checks.
  This is local evidence reading, not additional game polling or blind retry.
- Each automatic operation now stores a unique correlation ID and its account
  in the existing observation JSON before sending. A unique existing runtime
  pending receipt with the same operation, command, source module, account and
  valid dispatch interval can be adopted; ambiguous, older or foreign receipts
  cannot. A real `_finalize_game_command_sent` test verifies this contract.
  No outbox, new shared durable fence or CommandAttempt controller was added.
  No-ID legacy entries without these anchors remain unresolved rather than
  being guessed from command text. R07's shared forced-stop problem is open.
- Actual routed and passive callers pass chat, command-root and result-message
  provenance to the reducer. Mutation completion requires the original route,
  action, successful argument and non-stale dispatch bound; an anchored game
  rejection also closes the attempt without pretending it succeeded. Recovery
  accepts trusted bot results only, refuses an unknown chat instead of using
  the primary group, and uses each result message's latest edit. Header-only
  panels and blank star/prediction/change fields are not explicit state.
- An early clear-calamity result can arrive before its transport receipt.
  Its delta is applied once while the operation remains pending; later receipt
  adoption or log recovery closes the operation without decrementing it again.
  The pending-only receipt set survives reload, is capped at 64 entries and
  never evicts an applied delta to make space. Full capacity rejects a new
  unaccounted delta and retains the unresolved task. This does not certify all
  unsolicited/manual result idempotence or general out-of-order observations.
- Existing timeline entry and queued admission, downstream route preflight and
  the final route guard now respect an unresolved automatic mutation. Unrelated
  observations cannot erase its diagnostic or retry clock. Deep retreat is not
  part of this condition and does not consume Tianxing effects. Initial full
  suite: 4709 passed, 1046 subtests; follow-up edit, routing and persistence
  regressions were added before final verification. One new real-runtime
  fixture initially leaked a synthetic send into the health timer; the fixture
  now isolates that observer, and the formerly failing ordered group passes.
  All work remains in the offline candidate; no production changes or probes.
- R46 final verification (2026-09-09): 83 focused evidence cases; 4722 tests
  and 1046 subtests pass in the full suite (80.58 seconds). JUnit:
  `/tmp/xiuxian-rebuild-r46-tianxing-auto-evidence-20260908.xml`.
  The ordered related group also passes (613 tests and 151 subtests before the
  last two panel/route cases). Full Ruff, compilation and diff checks pass.
  The goal is still active; this is not whole-project or production acceptance.

- R47 serializes the two craft entrypoints with one identity lock and reuses
  the existing Tianxing operation checks. Identity replacement, rebinding,
  switch/pause/configuration changes, parent invalidation and newer farm state
  are checked after waits and immediately before runtime dispatch. Prediction
  evidence must still be valid at dispatch; a queued craft cannot consume a
  replacement prediction or run after a newer clock or task takes ownership.
- Craft sends persist their operation/account/command claim before transport.
  Only an explicitly unsent outcome can retry; exceptions, cancellation,
  unknown block evidence and malformed receipts retain unresolved work across
  ticks and SQLite reload. Queue timeout keeps the existing 10-20 minute
  stagger. Original craft ownership is separate from later panel calibration:
  a returned panel never proves that the craft completed or authorizes another
  craft. Existing runtime receipts can be adopted only by exact operation,
  source, command, account, chat and dispatch-time evidence. No new recovery
  controller or CommandAttempt authority was introduced.
- Actual craft replies reconcile the original command root, chat, account,
  dispatch bound and item. The last settled craft is not accounted twice.
  Early replies and post-dispatch switch-off retain confirmed facts; a later
  transport return does not reopen a completed task, overwrite a new clock,
  or recreate its cleared error. A calibration panel dispatched before that
  result cannot roll the observation backward, including when the panel reply
  itself arrives before its transport receipt. A genuinely newer panel remains
  usable. Business rejection retains its future backoff across either entrypoint
  and a day boundary; expiry still permits the next normally admitted action.
- Pending craft work also gates automatic Tianxing mutations, timeline sends
  and downstream route admission; only that operation's original first craft
  dispatch is exempt. Wild/rift preparation passes its parent ownership checks
  to craft consumption and recomputes time after awaited work. The ordinary
  retreat caller only gains parent/queue/post-await checks in this finding:
  its complete send/outcome lifecycle is not certified. Deep retreat still
  does not block or consume Tianxing effects.
- R47 final verification (2026-09-09): 205 craft lifecycle cases pass; the
  ordered Tianxing/wild/rift/runtime group passes 946 tests and 149 subtests.
  The real-runtime craft admission matrix covers entity resolution, registered
  guards and the final dispatch boundary for both entrypoints. Full suite:
  4929 passed, 1088 subtests passed, 76.62 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r47-craft-final-20260909.xml`.
  Full configured Ruff, compilation and `git diff --check` pass. No deployment,
  restart, push, production configuration/DB mutation or game request occurred;
  user-owned quiz-bank/tool changes remain excluded from this local commit.

- R48 starts from 159 retreat lifecycle cases: 104 failed and 55 passed before
  changes. Ordinary retreat, potion use, exchange, donation and configured force
  exit now share the tested farm send/receipt implementation with craft, while
  keeping separate business reducers and identity locks. The duplicate retreat
  transport implementation and now-unreachable waiting branch were removed.
  Pending intent is saved before transport; lock/queue waits recheck the owner,
  account, controls, parent, deep-retreat occupancy and action prerequisites.
- Each new retreat-chain mutation retains its original command, operation,
  account, source module, chat and dispatch time independently of a later panel
  query. Missing, uncertain, cancelled or malformed transport results do not
  rearm the action, including across SQLite reload. Explicit-unsent outcomes
  retain a bounded retry and the original chain action; material preparation
  is skipped once its known retreat cooldown has elapsed. Local action-guard
  delays no longer replace potion/exchange/donation with premature retreat.
- Result reconciliation requires the original command family, root, chat,
  account, dispatch bound and requested quantity when present. A panel cannot
  close an unresolved mutation. Early results and late receipts cannot reopen
  settled work or overwrite newer clocks. Older calibration panels are ignored
  before changing observations, while a genuinely newer panel remains valid.
  Missing potions without exchange authorization and resource denials retain
  their backoff instead of repeating the same resource command every tick.
- The real force-exit summary caller now forwards reply provenance and records
  the original farm result before awaiting summary finalization/notification.
  Exact pending replies can settle after either module is disabled without
  enabling automation. A replaced/deleted/rebound owner is not mutated after
  that await. Only unresolved ordinary retreat consumes/reserves the Tianxing
  route: deep retreat and material preparation do not acquire that effect gate.
- R48 final verification (2026-09-09): 226 retreat lifecycle cases; the real
  runtime tests add five command types across 21 admission interleavings each
  (105 inner and five outer subtests). Related suite: 1273 passed, 259 subtests.
  Full suite: 5156 passed, 1198 subtests passed, 79.71 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r48-retreat-final-20260909.xml`.
  Full configured Ruff, compilation and diff checks pass. This is offline
  candidate evidence only: no game traffic, production writes, service changes,
  skill edits, deployment or push. General manual/older-than-last reducer
  ordering and legacy farm entries without operation provenance remain open;
  per-farm pending state is not a solution to the shared R07 forced-stop gap.

- R49 starts from 142 guard-evidence cases: 105 failed and 37 passed. A separate
  60-case real-dispatcher matrix exposed 36 failures in generic routed/passive
  cleanup even after the internal helper was fixed. Both dispatchers now leave
  the six direct Tianxing guard families to the module's correlated result
  handling; unrelated modules and farm-family cleanup are not changed.
- Removed cached-observation guard cleanup from the scheduler and timeline
  confirmer. The replacement accepts only the original identity/account,
  command, chat and root message, with a valid sending timestamp and explicit
  parsed outcome. Missing legacy account/chat/timestamp evidence remains
  unresolved by this path; no status request or mutation retry is introduced.
- A panel may calibrate predict/change/set-star guards only when its existing
  runtime pending record proves that the query was actually dispatched after
  the target command in the same chat/account. Partial fields cannot borrow
  cached values, older-day fixed stars cannot confirm a new day, and every
  close supplies expected root/chat. Query receipt arrival does not substitute
  for actual dispatch. Only existing exact-key in-memory records are read;
  there is no history scan or new recovery controller in the send path.
- Sending account metadata survives the existing JSON guard persistence;
  actual runtime receipt registration and log replay pass both before and
  after SQLite reload. Added malformed-reference tests exposed seven more
  failures: fractional IDs were truncated and boolean timestamps were accepted.
  These inputs are now rejected as closure evidence.
- R49 final verification (2026-09-09): 218 guard-evidence cases; full suite
  **5374 passed, 1198 subtests passed**, 80.90 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r49-final-20260909.xml`. Selected Ruff checks,
  compilation and `git diff --check` pass. This does not certify the global
  observation reducer's freshness/idempotence or every generic pending cleanup.
  Production, live switches, listener, skill and CommandAttempt remain untouched.

- R50 starts from 30 intermediate/final-edit cases (20 failed), followed by 14
  early-result/late-transport cases (all failed). The routed handler, passive
  inbox and SQLite replay now distinguish a diagnostic or preparation reply
  from an explicit terminal business result. Empty panels do not renew the
  authoritative observation or confirm a timeline step; craft preparation
  advances only its existing waiting phase, with native/business pending intact.
- Positive, command-matching exchange/donation quantities are required for
  success. A further 43-case review reproduced incomplete/contradictory craft
  counts, malformed observation/context/pending shapes, and empty ordinary-
  retreat pending commands. Removed the craft reducer's `count or 1` fallback;
  classification and the reducer both reject zero or inconsistent totals.
  The malformed pending guards retain uncertainty without new sends. Known
  deep-retreat/material preparation still does not block Tianxing effects.
- Farm/material guard families now share the exact correlated closure path.
  A confirmed early result is not applied twice when the send receipt arrives;
  replay may close its newly registered guard without rerunning the reducer.
  Existing receipt adoption uses processing time only to locate a receipt
  already recorded in memory. Effect/result/CD timestamps remain the original
  game-event time, and actual dispatch evidence is required when a result
  precedes receipt delivery. No speculative query, resend or controller added.
- R50 final verification (2026-09-09): 87 new reply-completion cases, 1286
  related tests and 14 subtests; full suite **5461 passed, 1198 subtests passed**,
  84.46 seconds. JUnit: `/tmp/xiuxian-rebuild-r50-final-20260909.xml`.
  Selected Ruff checks, compilation and `git diff --check` pass. Candidate-only;
  no production configuration/database changes, deployment, restart, game
  commands, listener activation, skill edits or push.

- R51 reproduces consumption resurrection in 48 of 55 new cases before the
  fix. Merely reading/normalizing a state whose last action was observe,
  set-star, predict, change-fate, panel or clear-calamity erased the consumption
  marker; unrelated real replies and a partial panel could also expose this
  old prediction as usable. Removed that heuristic instead of inferring a new
  effect from an unrelated action label.
- The existing timestamp comparison now retains consumed state unless an
  explicit prediction timestamp is newer. A genuine new predict result still
  renews the effect. Corrected two old tests that required unsafe resurrection
  or restart release, and gave the positive calibration-skip test an actually
  newer prediction timestamp rather than contradictory consumed state.
  Read-only status and preflight after SQLite reload preserve the marker.
- R51 final verification (2026-09-09): 299 related tests and 9 subtests; full
  suite **5516 passed, 1198 subtests passed**, 81.61 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r51-final-20260909.xml`. Selected Ruff, compilation and
  diff checks pass. This is a normalization fix, not certification of the
  timestamp's provenance: stale/unrelated parsed results and partial panels
  still require reducer-level review. No production actions were taken.

- R52 strengthens 36 existing operation-ownership cases with authoritative
  observation assertions: 31 failed, while the five force-exit cases already
  kept observations unchanged. Pending auto, craft and ordinary-retreat/material
  results now pass the existing identity/account/chat/root/time/argument checks
  before changing effects, resource counts or observation timestamps. The same
  rejection applies to a full result from another command, not just partial
  replies; rejecting a result does not silently mark it as handled or consumed.
- All six automatic reply types now require an actual matching root, including
  panel/observe. Early results wait for an already registered transport receipt
  and then use existing replay exactly once; the old early-calamity test now
  verifies that no delta is guessed before that receipt exists. Updated the
  read-only positive fixtures to carry their actual chat/root/dispatch evidence,
  rather than weakening receipt lookup to accept impossible ordering.
  Twenty-one additional fractional-reference cases cover auto and both farms.
- R52 final verification (2026-09-09): 1362 related tests and 14 subtests; full
  suite **5537 passed, 1198 subtests passed**, 82.84 seconds. JUnit:
  `/tmp/xiuxian-rebuild-r52-final-20260909.xml`. Selected Ruff, compilation and
  diff checks pass. This does not certify active timeline-step confirmation,
  legacy unscoped state, manual-operation arbitration, or old replies after
  their pending operation has completed. No live/production changes or push.
- R53 begins with 118 timeline ownership/completion cases, 97 failing before
  implementation. These expose source-less cache confirmation, cross-chat and
  wrong-root results, unbound/rebound operation ownership, negative-result
  mutation and early replies accepted before a transport receipt exists.
  Follow-up review reproduces another 36 failures for empty sending accounts
  and missing, boolean, nonfinite, reversed or not-yet-available receipt times.
- Timeline sends now retain a unique operation ID, original account, queue
  time and actual dispatch/receipt metadata. Existing registered transport
  receipts can be adopted by the same operation; neither an unanchored reply
  nor an invalid message ID becomes evidence that an action completed.
  Processing time is used only to adopt an existing receipt; game-effect
  timestamps retain the original event time.
- Active-step admission runs before authoritative observation writes. Success
  and refusal/CD transitions use the actual parsed reply, with exact
  account/chat/root and command-argument checks. A later panel can calibrate
  a target only through its own verified query record and dispatch ordering;
  absent fields cannot borrow cached values or imply a negative result.
  Guard closure and timeline confirmation share query ownership inspection.
- Scheduler-side cache confirmation and the bot-only unthreaded recovery
  heuristic are removed. Recovery uses the existing bounded log-tail reader
  (512 KiB per day, at most two days), configured game-bot evidence and the
  exact reply root, retaining the latest edit. This also replays a result
  that arrived before the send call returned, without another game request.
  Standalone results without attributable roots remain unresolved.
- R53 verification includes 261 new cases: direct and negative results,
  invalid references/clocks, early detached receipts, verified/partial/older
  panels, native routed pending cleanup, latest-edit replay, real send-call
  interleaving and SQLite reload/account rebinding. Existing positive tests
  now supply actual receipt/context fixtures instead of source-free cached
  success; two bare AsyncMock receipts now have explicit integral IDs.
- R53 final verification (2026-09-09): full suite **5798 passed, 1198 subtests
  passed**, 86.19 seconds. JUnit:
  /tmp/xiuxian-rebuild-r53-final-20260909.xml. Selected Ruff, compilation and
  diff checks pass. No deployment, service restart, push, game request,
  production configuration/DB write or skill edit was performed.

### R54 Evidence

- Initial expiry/admission tests reproduced 38 failures among 53 cases before
  implementation. Effect deadlines no longer receive the 60-second retry
  buffer or default eight/twenty-four-hour lifetimes. `change pending` timers
  are parsed only from their own text segment, not unrelated business CD text.
- Freshness requires a valid effect timestamp and reported future deadline.
  Last-action labels, whole-panel observation times and release records cannot
  fill missing timestamps. Unknown-lifetime predictions still block conflicting
  routes, without authorizing consumption or inventing an expiration time.
  Dirty/future/boolean clocks cannot authorize duel or wild-training release.
  Deep retreat remains independent of Tianxing effects.
- Removed both cache-based calibration/route-result shortcuts. An unresolved
  timeline mutation prevents downstream release and cannot be discarded for
  a newly planned send after its calibration query times out. A complete
  query must retain the same account/chat, its root and actual dispatch order;
  a target-matching panel confirms the original operation rather than treating
  it as unconfirmed. Original success or refusal/CD can reconcile the retained
  operation, including after `active_step` has been cleared.
- Native routed/log replay reproduced eight partial-effect failures: the
  original mutation's native pending or guard was cleared before the final
  edit. Terminal result matching now requires the reported effect deadline;
  retained mutation steps participate in pending detection during calibration.
  A follow-up two-case reproducer caught abandoned read-only steps keeping
  completed queries pending; historical scanning is limited to mutations.
- The 122-case R54 suite includes SQLite roundtrips between partial/final
  replies on both native paths, and before/after late success, same-route CD
  and conflicting-route refusal. Invalid original receipt clocks, cached
  fields, effect-expiration boundaries and continued deep-retreat availability
  are covered. Positive legacy fixtures now supply actual effect times and
  receipt evidence; tests for removed shortcuts assert retained uncertainty.
- Related regression set: **1015 passed, 20 subtests passed** in 6.40 seconds.
  Final full suite (2026-09-09): **5920 passed, 1198 subtests passed**, 85.59
  seconds. JUnit: `/tmp/xiuxian-rebuild-r54-final-20260909.xml`.
  Selected Ruff checks over model/tests/tools, changed-file compilation and
  `git diff --check` pass. No deployment, restart, push, production write,
  game request, listener start or skill edit was performed.

### R55 Evidence

- Resumed the existing 14-case failing run and collected its terminal result:
  twelve native message/edit cases used delivery time for effect deadlines,
  and two route-result cases erased an unrelated pending mutation and guard.
  Removed the route-only acknowledgement branch; a result can consume its
  matching released route without confirming a different predict/change send.
- Added explicit `server_event_at` metadata from Telegram `date`/`edit_date`
  through routed delivery, `VerifiedGameEvent`, MessageBox and logged replay.
  Log `ts`/`ts_epoch` remains local receipt time for file lookup and other
  modules' existing contracts. Missing/invalid server clocks are not relabeled
  receipt times; Tianxing retains pending work until valid evidence arrives.
  Native edits without `edit_date` cannot borrow the original message date.
- Prediction and change each retain bounded evidence: server time, chat/message,
  revision kind, completeness and a small semantic signature. Older effects,
  explicit absence and consumption cannot be overwritten by delayed native
  replies. Same-second message ordering is local to a chat. An unchanged
  complete reply cannot extend an effect, but a genuine panel edit can update
  it. Additional tests reproduced seven failures around edited panels,
  cross-route consumption and newer same-second panel evidence before fixing
  those paths. Absence evidence survives SQLite reload.
- Effect fields are written by the observation reducer, not copied again by
  timeline/guard confirmation. A correlated old query may acknowledge its
  operation without replacing newer effects. Corrupt evidence does not
  authorize downstream work; a complete, owned panel dispatched after the
  previous observation can repair the affected field. The first ten corrupt
  metadata reproducers all failed before the checks; expanded cases cover
  invalid containers, records, clocks, revision kinds, signatures and IDs.
- Shared Tianxing revision selection now serves automatic/timeline recovery,
  native pending-log replay, and cached/logged replies that arrived before
  send registration. Five dedicated recovery failures and six native replay
  failures reproduced arrival-order selection of an obsolete result. Selection
  requires a trusted bot and real event clock, keeps the latest revision per
  chat/message, and does not guess between conflicting edits in one server
  second. This does not create retries or transfer recovery to CommandAttempt.
- Full-suite review caught a rift integration failure: the known exploration
  family was not passed to the Tianxing parser. Live routed rift replies now
  carry both their family and server-time context. The legacy rift log readers
  are still a separate unresolved source/ownership review, not certified by
  this native-path correction.
- Expanded regression set: **1974 passed, 147 subtests passed**, 12.43 seconds.
  The first full run found **6031 passed, 1 failed, 1198 subtests passed**,
  85.60 seconds; the rift failure above was corrected afterward. Its JUnit is
  `/tmp/xiuxian-rebuild-r55-first-full-20260909.xml`.
  The next full run had **6064 passed, 1 failed, 1198 subtests passed**:
  the storage-transfer routed-rift mock still asserted the old call contract.
  Its fixture now supplies original/edit server dates and checks the complete
  identity/chat/root/time context, without removing the implementation guard.
  Related storage-transfer, event-time and observation tests: **189 passed**.
  Final full suite: **6065 passed, 1198 subtests passed**, 85.90 seconds;
  JUnit: `/tmp/xiuxian-rebuild-r55-verified-20260909.xml`.
  Full selected-rule Ruff, changed-file compilation, `pip check` and diff
  checks pass. No production deployment, restart, push,
  game request, configuration/DB write, listener start or skill edit occurred.

### R56 Evidence

- Initial offline reproducer: **59 failed, 5 passed**. Wrong chats, unrelated
  identities/commands, missing bot provenance, invalid server clocks, duplicate
  IDs, late originals and conflicting edits reached rift or Tianxing state.
  JUnit: `/tmp/xiuxian-rebuild-r56-repro-20260909.xml`.
- All three legacy readers now require a matching identity-owned command in
  the same chat, from registered pending evidence or an actual outgoing log
  record. Manual command messages require their real server clock and actor;
  an explicit logged account mismatch is rejected. Existing sent logs and
  pending rows do not universally record historical account ownership, so this
  does not close account-rebind/lifecycle work under R07/R11.
- Reply selection reuses R55's trusted-bot/server-version checks. A later
  arrival cannot replace a newer server edit; conflicting same-second edits
  and multiple eligible chat/root pairs remain unresolved. The newest version
  is chosen before checking result text, so an unrecognized latest edit cannot
  reveal an obsolete final result. Both direct-root and pending-result replay
  preserve identity, chat, root, message, server time and processing time.
- Local receipt times are only used for bounded log lookup, not effect/CD
  deadlines. A command's actual server/dispatch time controls recent-send
  discovery when available; a delayed old command cannot become a new unknown
  send merely by arriving now. Follow-up corruption tests reproduced **6 more
  failures** before checking query IDs/clocks without coercing invalid values.
- Reads are capped at 512 KiB per daily file over at most 36 hours. Missing
  evidence outside the retained tails is not reconstructed. A future indexed
  history may improve coverage, but reopening unbounded daily scans on every
  scheduler tick is not an accepted fallback. Five old fixtures now include
  their outgoing command/source/server clock instead of accepting bare IDs.
- Focused regression set: **316 passed, 5 subtests passed**, 2.83 seconds;
  includes SQLite reload and a real reply that preceded the local send receipt.
  Final full suite: **6145 passed, 1198 subtests passed**, 85.62 seconds;
  JUnit: `/tmp/xiuxian-rebuild-r56-final-20260909.xml`.
  Full selected-rule Ruff, changed-file compilation and diff checks pass.
  This is still offline candidate work; production, skill, listener, flags,
  configuration and live DB are untouched. No deployment, restart or push.

### R57 Evidence

- The initial unknown-lifecycle reproducer recorded **26 failed, 2 passed**;
  JUnit: `/tmp/xiuxian-rebuild-r57-repro-20260909.xml`. The resumed focused
  run found **178 passed, 1 failed**, with the remaining old assertion still
  expecting a one-tick pause. It now verifies persisted legacy migration,
  one notification and no retry on later scheduler ticks.
- Known command IDs and no-ID unknown operations survive timeouts, UI off/on,
  initial checks and SQLite reload. Incomplete result edits keep their original
  result anchor. Elapsed time does not clear pending work, invent a complete
  game cooldown or consume Tianxing effects. Legacy unknown summaries block
  Tianxing downstream work even before the rift scheduler migrates them.
- An exactly owned panel may update effect observations, but never establishes
  that this rift executed or was unsent. Query state is saved before awaiting
  transport; cancellation retains it, an unknown query is not repeated, and
  only an explicitly unsent query becomes retryable after backoff. Save failure
  prevents dispatch. No CommandAttempt recovery/controller behavior was added.
- Additional fault tests reproduced **9 failures** in parent/query ownership,
  operation-ID aliasing, cross-account receipt adoption, pre-query receipts,
  unknown start-anchor retention and pre-dispatch result clocks. These checks
  now retain the original identity object/account and immutable operation ID,
  and preserve exact root/chat/server-time evidence. Command route lookup uses
  only bounded log tails, not the runtime helper's full daily-file fallback.
- Eight native message/edit and pending-log replay cases exercise final results
  and cooldown replies with the module disabled. They close the original work
  at the server event's clock without enabling the module; repeat delivery does
  not change business state or notify again. The stale-start/final-edit sequence
  and cancelled query are also tested across a real temporary SQLite reload.
- The first full suite found **6198 passed, 1 failed, 1198 subtests passed**;
  JUnit: `/tmp/xiuxian-rebuild-r57-full-20260909.xml`. The obsolete startup test
  expected in-flight IDs to be erased. It now asserts retention, alongside a
  separate fresh-identity initial-scheduling regression.
- Final full suite: **6200 passed, 1198 subtests passed**, 85.40 seconds;
  JUnit: `/tmp/xiuxian-rebuild-r57-final-20260909.xml`. Full selected-rule Ruff,
  changed-file compilation, `pip check` and diff checks pass. Production,
  configuration/DB, listener, skill and automation switches remain untouched;
  no deployment, restart, game request or push was performed.
- R57 does not certify the original `CMD_EXPLORE_RIFT` dispatch await. That
  caller still lacks pre-dispatch persisted operation ownership, can overwrite
  an early result with a late receipt, and needs cancellation/queue/rebind
  tests. General scalar reply ownership and post-completion resource-delta
  deduplication remain separate open requirements.

### R58 Evidence

- Initial original-dispatch lifecycle reproducer: **31 failed**; JUnit:
  `/tmp/xiuxian-rebuild-r58-repro-20260909.xml`. Missing pre-send persistence,
  cancelled sends, overwritten early start/final/CD replies, changed owners,
  queue admission, malformed receipts and unrelated manual-command adoption
  all reproduced against the R57 candidate.
- New rift operations persist their identity/account, immutable operation ID,
  request-start clock and sending phase inside the existing observation JSON
  before calling transport. Save failure does not dispatch. Real transport
  receipts retain their root/chat; unknown, malformed or cancelled outcomes
  retain the operation instead of rearming a command. No DB column or general
  recovery controller was introduced; CommandAttempt remains shadow-only.
- The original identity object/account is checked after the module lock and
  preparation waits. Actual queued dispatch checks the current switches,
  manual hold, cooldown snapshot, realm/resources and Tianxing protection.
  A valid receipt is still retained after post-dispatch pause, but does not
  enable the module or overwrite an early start/final/CD transition.
- Modern no-ID replay requires the persisted command operation ID, not just
  a nearby manual command. A cancelled operation can recover from a detached
  receipt and final edit after a real temporary SQLite reload. Reads remain
  bounded to the existing 512 KiB per daily-file tails over at most 36 hours;
  missing evidence after retention expiry remains unresolved.
- Unresolved rifts now block competing Tianxing consumers and mutations even
  without a current release lease. Only the original module/operation while
  actually in its sending phase may pass that exclusion, and it still needs
  valid effects. Deep retreat remains outside Tianxing consumption blocking.
  Additional positive-admission tests caught a self-blocking high-resource
  branch, and a queued manual-hold test caught a missing control check; both
  were corrected without weakening the unknown-operation guard.
- Focused rift regression set: **249 passed, 5 subtests passed**, 1.91 seconds;
  JUnit: `/tmp/xiuxian-rebuild-r58-focus-verified-20260909.xml`. Old fixtures now
  include the actual runtime receipt chat, explicit test clocks and the
  operation-check call contract. Audit notifications are mocked in that legacy
  class so a malformed test receipt cannot attempt to use a disconnected
  notification client.
- Full suite: **6244 passed, 1198 subtests passed**, 86.65 seconds; JUnit:
  `/tmp/xiuxian-rebuild-r58-full-20260909.xml`. Full selected-rule Ruff,
  changed-file compilation and diff checks pass. This remains local candidate
  work; no deployment, restart, push, live game request, production config/DB
  change, listener start, skill edit or automation-switch change occurred.
- This does not prove shared R07 transport durability, old unscoped scalar
  result ownership, resource-delta idempotence after completion, or the fatal/
  rebirth reducers' internal awaits. The new per-operation intent is not a
  substitute for reviewing those paths or the remaining project matrix.

### R59 Evidence

- Initial result-lifecycle reproducer: **60 failed, 24 passed**; JUnit:
  `/tmp/xiuxian-rebuild-r59-repro-20260909.xml`. Failures include A/B/A reward
  replay, same-operation wording edits, chat-ID collisions, stale clocks,
  missing/mismatched routing evidence, split commits and post-notify writes.
- Added `explore_rift_result_evidence` to identity runtime JSON. Receipts are
  scoped by chat and original command with an exact account owner, result ID,
  server-event order, digest and cumulative item amounts. Older delivery cannot
  reopen a terminal outcome; a newer same-result reward edit applies only its
  difference without extending the original cooldown. Distinct same-ID chats
  remain distinct operations. Conflicting same-second edits are not guessed.
- Normal result transition and inventory delta now precede one `save_state()`.
  The inventory helper stages a copy of the affected role and supports an
  explicit deferred save; existing callers keep immediate-save behavior.
  Synchronous reducer exceptions restore the original identity/inventory, and
  temporary SQLite write-failure tests prove rollback of both halves followed
  by an exactly-once replay. Save failure can also retry from retained memory.
  Newer absolute inventory snapshots are not incremented by older rewards.
- Escape completion is synchronous through its commit. Notifications run only
  afterwards, with no trailing result writes; deletion, replacement, rebinding,
  newer results and notification cancellation cannot corrupt another role.
  The second result notification rechecks the captured owner after the first.
- Detailed receipts retain up to **64 operations**, pruning detail older than
  **72 hours** as fresh evidence advances while keeping the current/latest
  receipt. Per-chat
  retired-root watermarks prevent old edits from becoming new rewards after
  expiry/eviction; watermark metadata is capped at 32 chats and new untracked
  chats are rejected at capacity. Corrupt evidence is not silently reset.
  Retention tests reload real temporary SQLite state before replaying old edits.
  Evidence outside this retained detail cannot be reconstructed by inference.
- Preexisting unit/real-message fixtures now carry the native router's scope
  and server-event fields. The original assertions on cooldowns, resources and
  Tianxing behavior remain; independent same-second examples use distinct
  commands instead of presenting contradictory texts as one operation.
  Focused original/new rift regression checkpoint: **350 passed, 5 subtests**.
  Additional native/corruption/schema tests: **129 passed, 2 subtests**;
  `/tmp/xiuxian-rebuild-r59-native-20260909.xml`. Final focused checkpoint with
  real wording and legacy holds: **381 passed, 7 subtests**;
  `/tmp/xiuxian-rebuild-r59-final-focus-20260909.xml`.
- Pre-commit review retained the old last-result ID as an explicitly unscoped
  hold, including after clearing module state, accepting a new result or SQLite
  reload. It is not assigned a guessed chat or treated as an unclaimed reward.
  Older pre-ledger history still needs reconciliation before deployment.
- Final full suite: **6362 passed, 1198 subtests passed**, 89.91 seconds; JUnit:
  `/tmp/xiuxian-rebuild-r59-final-20260909.xml`. This includes 118 new result
  lifecycle cases, legacy-schema upgrade and the final clear-before-migration
  regression. Full selected-rule Ruff, changed-file compilation and diff
  checks pass.
- R59 does not establish what an old unscoped result already awarded before
  this receipt ledger existed, or repair shared R07 unknown-send durability,
  legacy scalar anchors, rebirth request/select sends or every Tianxing counter.
  Those are still review work, not permission to deploy this candidate.
  No production file/configuration/database, service, skill, listener, game
  request, remote branch or automation switch was changed.

### R60 Evidence

- Initial lifecycle reproducer: **27 failed, 6 passed**;
  `/tmp/xiuxian-rebuild-r60-repro-20260910.xml`. The first handoff checkpoint
  had 33 passing new tests, not a completed review. Resumed legacy regressions
  found **7 failed, 393 passed, 5 subtests**;
  `/tmp/xiuxian-rebuild-r60-legacy-first-20260910.xml`.
- Added `explore_rift_rebirth_operation` as persisted identity JSON. Request
  and selection share a dispatch lifecycle with a saved operation ID, exact
  identity/account, original command/chat, send clocks and parent request.
  The queue rechecks current controls, configuration and operation ownership.
  Cancellation or missing/malformed receipts preserve unknown work; only a
  definitely-unsent operation may be replanned after its backoff. Late returns
  cannot overwrite an early result or a replaced/rebound identity.
- Rebirth results require the owned original request or selection, trusted
  router scope and server chronology before state mutation. Old prompts and
  duplicate options do not select twice. Paused options remain available for
  the current configured choice after resume. The existing blind-index setting
  remains supported once, only after a confirmed request's actual **6-minute**
  reply deadline. An uncertain request cannot authorize blind selection.
- Rebirth transition and operation evidence save together before notifications.
  Failed saves remain replayable; unexpected synchronous save failures restore
  the previous identity. Notification failure/cancellation cannot reopen a
  completed recovery. Temporary SQLite reload and old-schema migration are
  covered, without using production data or inventory APIs.
- Follow-up reproducers found **17 failed, 36 passed**;
  `/tmp/xiuxian-rebuild-r60-followup-repro-20260910.xml`. They cover a second
  fatal/escape cycle after restoration, late death edits, repeated searching
  acknowledgements starving timeout, malformed evidence, no-ID mutual
  exclusion and late receipts whose real deadline has not yet elapsed.
  New deaths start distinct operations. The death that caused a completed
  recovery, and death evidence older than that recovery, cannot reopen it.
  Repeated searching log replay now reaches timeout without postponing it.
- Native reply/edit and log replay tests exercise pause, SQLite reload and
  loss of the in-memory consumed-event cache. They found and fixed the module's
  rejection of an owned completed reply while disabled. If a restart occurs
  after the business commit but before shared pending cleanup is flushed,
  replay clears the residual pending without repeating recovery or notices.
  This is not a claim of atomic shared pending/result commits under R07.
- Pre-commit review found **3 failed** server auto-choice cases;
  `/tmp/xiuxian-rebuild-r60-auto-choice-repro-20260910.xml`. A terminal result
  on the original request can finish the owned request/selection chain even
  after local selection has queued or become unknown. Its original request
  receipt remains the proof, and the queued selection is invalidated. Ordinary
  old parent options still cannot trigger another selection. The auto-choice
  wording uses the existing classifier contract and a synthetic fixture;
  it is not newly collected live-game evidence.
- Legacy tests now include real-shaped message-log ownership and full
  transport receipts. Timeout fixtures use the actual configured deadline;
  uncertain selection IDs stay retained instead of being erased. No runtime
  ownership checks were loosened to preserve incomplete fixtures.
- Final new lifecycle suite: **90 passed**, 1.48 seconds;
  `/tmp/xiuxian-rebuild-r60-rebirth-final-20260910.xml`. Intermediate full suite:
  **6449 passed, 1198 subtests**, 91.25 seconds. Final full suite after the
  auto-choice review: **6452 passed, 1198 subtests**, 89.17 seconds;
  `/tmp/xiuxian-rebuild-r60-full-final-20260910.xml`. Full selected-rule Ruff,
  changed-file compilation and diff checks pass.
- The evidence record is bounded to the current operation and its one parent,
  not an unbounded history. Legacy scalar IDs can migrate only with unique,
  owned command evidence; absent or contradictory evidence remains unresolved.
  Shared R07 durability, broader R11 scalar ownership, pre-ledger accounting,
  other gameplay workers and the remaining acceptance matrix are still open.
  No production code/config/DB, service, remote branch, listener, skill or
  automation switch changed; World Boss and incense refinement stay closed.

### R07 Revalidation After R60

- Both original forced-stop cases remain unsafe at candidate `a5db2b32`:
  tracked checkin and rift send again after no-ID process loss, even after a
  successful isolated state save. These are confirmed open failures.
- Expanded `tools/lab_send_crash_probe.py` with `--crash-point after_id`,
  `--untracked` and isolated `--advance-seconds`. Untracked checkin also sends
  twice after receipt registration but before the caller receives it.
  Untracked rift repeats after a 24-hour clock advance, despite a saved
  attempt-one guard. Merely deleting a durable marker on receipt or extending
  a temporary guard would leave the handoff defect unresolved.
- The separate proposal and acceptance contract are in
  `docs/audit/R07-transport-ownership-design-20260910.md`. Approval for a
  behavior-changing offline shared-send implementation has been requested;
  no runtime fence/controller or Attempt decision reader was added here.
  R07 remains open; independent module/UI review can proceed.

### R61 Evidence

- Revalidated the prior handoff's **141 passed, 7 subtests** result from its
  actual test process. Added **54 failing follow-up cases** before their
  corresponding fixes, covering corrupt queued records, wrong-chat records,
  identity deletion/replacement/rebind at all three UI reads, unowned legacy
  pending work, original warning/settlement clocks, definitely-unsent purge
  scheduling, manual-result coexistence, cross-identity choice admission,
  legacy purge retries after disable, older replies after newer broadcasts
  and the duplicate passive text reducer bypassing direct reply checks.
- `second_soul_commands` is bounded to five module-specific records:
  status, training, purge, demon status and explicit UI status reads. New
  automatic operations save their identity/account, original chat/command and
  operation ID before dispatch. Existing transport operation checks reject
  stale owners, switches, business snapshots or changed/corrupt records.
  This is not a shared-send controller and does not read Attempt for decisions.
- Cancellation and uncertain receipts preserve the original operation. Late
  receipts cannot undo an early outcome. Only definitely-unsent work gets a
  resend backoff; purge and post-purge query backoffs retain their next action,
  rather than returning to ordinary status/training and skipping mitigation.
  Disabling automation retains outcome evidence and disables retry admission,
  including existing tracked purge and demon-status requests.
- Native replies and message-log recovery require the exact identity, account,
  command, chat and root. Final edits use server event time, with dispatch time
  rather than delayed RPC receipt time as the lower bound. Duplicate training
  results do not extend the 24-hour cooldown. Queries reporting no second soul
  enter the existing seven-day backoff. Recovery clears only its exact pending
  root; another group's same-ID/same-command work remains intact. A reply older
  than the latest accepted broadcast cannot roll the business state backward.
- Removed the independent second-soul text reducer in `passive_inbox`. Passive
  delivery now uses the module's same anchored handlers and server clock, and
  skips replies already handled by the direct route. Missing/wrong roots or
  chats cannot clear a pending purge through a copied panel. Added raw and
  `VerifiedGameEvent` coverage, completed-result replay, module-off completion
  and identity deletion during the newly awaited passive handler.
- A high-moran read cannot prove an uncertain purge failed or authorize another
  spend. A later low-moran read can calibrate that operation; unresolved work
  stays held beyond the one-hour log lookup window. No missing reply is treated
  as permission to purge again. The configured threshold stays **60** and the
  automatic purge cap stays **2**. A read or manual purge during cultivation,
  injury or heart-demon work updates observed moran without scheduling training
  or another purge. A stale manual command edit cannot restart the cooldown.
- UI level reads still work while second-soul automation is disabled and do
  not interrupt an unresolved purge. The actual `refresh_identity_info` caller
  captures one identity object/account/request generation for all reads, and
  rechecks ownership at queue admission and after awaits. A current manual
  training result is not blocked by a separate unfinished UI status read.
  General identity-refresh reentry, early-result tracking, followup scheduling
  and all other UI contracts remain under separate review.
- Heart-demon choice admission uses the warning owner's context, not the
  callback's ambient identity. Original message creation bounds the choice
  deadline; delayed edits cannot renew it. Settlement uses the original server
  event time. Newer training is not replaced by an older return/warning.
  Auto-choice disable, identity replacement/rebind/deletion and expired windows
  stop queued choices. Legacy warning records never acquire an account by
  guessing from the current binding.
- Targeted final result: **202 passed, 7 subtests**, 1.67 seconds;
  `/tmp/xiuxian-rebuild-r61-targeted-reviewed-20260910.xml`. The first full
  checkpoint passed **6601 tests, 1198 subtests**, 93.18 seconds;
  `/tmp/xiuxian-rebuild-r61-full-review-20260910.xml`. Final full revalidation
  after the passive-contract and ownership review: **6621 passed, 1198
  subtests**, 90.49 seconds;
  `/tmp/xiuxian-rebuild-r61-full-reviewed-20260910.xml`. Full selected-rule
  Ruff, module/test/tool compilation and diff checks pass. The old failed
  `/tmp/xiuxian-rebuild-r61-full-final-20260910.xml` is not acceptance evidence.
- The broad R07 crash windows, old unowned scalar operations, pre-ledger
  resource accounting, remaining MiniApp workers, UI and operational acceptance
  remain open. No production code/config/DB, service, listener, remote branch,
  inventory API, skill or live automation switch changed.

### R62 Evidence

- Added 55 dedicated lifecycle/record/parse/replay cases and one actual
  transport-preparation test with four subtests. The first 14 regressions
  failed on the previous candidate, including early-card loss, pre-receipt
  double-click admission, followup reentry, cross-chat cleanup, account changes,
  unknown/cancelled sends and treating zero current cultivation as missing.
  Initial evidence: `/tmp/xiuxian-rebuild-r62-reproduced-20260910.xml`.
- `identity_info_refresh` retains one explicit request, with identity, account,
  original chat and an independent request ID. Primary and followup reads each
  have at most two command records; each command retains at most eight reply
  IDs. Save-before-dispatch reserves the operation. Caller and queue checks use
  the original identity object, request generation and immutable route/command
  fields, including after awaits and at the existing final RPC admission.
  Header or record rewrites do not become a new owner merely by reusing a dict.
- Normal transport receipts use the existing sent observer. The actual early
  reply cache/replayer test first rejects an unconfirmed root, then applies the
  card after its exact receipt, without resending. A late receipt after caller
  cancellation can likewise complete the retained read. Normal completion does
  not cancel the two existing supplemental level reads; they keep the same
  request owner and original route. R61 still owns the second-soul read reducer.
- Profile and battle-power requests retain their existing one-retry limit.
  Only their source-tagged pending rows enter the scoped retry reservation.
  Concurrent retries, an already completed original result, request replacement
  and owner changes stop queued work. Unknown retry outcomes remain held;
  definitely-unsent retries honor backoff without charging a sent retry. New
  explicit refreshes retire the old owned rows' retry permission, not another
  chat's rows. Unrelated sender/retry families and Attempt decisions are unchanged.
- Direct results require the exact request account, chat, command sender/root
  and server event time. Cleanup uses original chat/message pairs and rechecks
  ownership inside the account RPC and after its await. The broad same-family
  pending deletion and receipt-time request-generation resets were removed.
  Old incomplete edits no longer postpone an already scheduled followup.
- Native passive cards now carry verified source context and server clocks;
  the fallback does not reapply a rejected automatic profile reply. Valid
  manual/unthreaded cards and prior username aliases remain supported. Six
  bounded per-field clocks keep newer profile observations from being overwritten
  by delayed primary/followup data; a followup does not restamp primary values.
  Malformed requests cannot authorize sends; malformed clocks cannot be reported
  as a successful profile refresh. Confirmed results are saved before notification, and
  notifier failure cannot put the query back in flight.
- Dedicated and related final tests: **371 passed, 308 subtests**, 11.61 seconds;
  `/tmp/xiuxian-rebuild-r62-targeted-reviewed-20260910.xml`. Initial full
  checkpoint: **6665 passed, 1198 subtests**, 92.03 seconds;
  `/tmp/xiuxian-rebuild-r62-full-review-20260910.xml`.
  Final full revalidation after immutable-operation and native-preparation
  review: **6677 passed, 1202 subtests**, 89.91 seconds;
  `/tmp/xiuxian-rebuild-r62-full-reviewed-20260910.xml`. Full selected-rule
  Ruff, code/test/tool compilation and diff checks pass.
- This is a bounded, read-only business lifecycle, not an R07 shared durable
  send controller. Other profile writers (including breakthrough/rebirth and
  API-derived updates), supplemental modules' independent retry/reply flows,
  remaining MiniApp workers, UI/operational acceptance and forced-stop transport
  durability remain open. No production files/config/DB, service, listener,
  remote branch, inventory API, skill or live automation switch changed.

### R63 Evidence

- Added 61 regression cases across profile observations and the existing
  refresh lifecycle tests. The initial 25-case suite had 24 failures on R62:
  native breakthrough rollback, prefix/case mismatches, untrusted broadcasts,
  identity replacement/rebinding, stale credentials and wrong-role API fallback.
  Evidence: `/tmp/xiuxian-rebuild-r63-reproduced-20260910.xml`. Further review
  reproduced same-second ordering, malformed/ambiguous API owners, nested role
  labels, duplicate row counts, lost partial progress and profile-evidence
  eviction; those regressions are now covered, not waived.
- `profile_observation.py` admits observations only for existing identities.
  The existing profile-clock JSON retains nine bounded field groups and at most
  one source descriptor per group. Telegram descriptors carry chat, message ID
  and edit kind; API descriptors carry the original request start. Neither
  unknown sources nor malformed clocks authorize an update. Existing scalar
  clocks remain readable without inventing message/source metadata.
- New-message and edited breakthrough dispatchers supply actual server-time
  context. Exact, unique tags replace substring matching; previous username
  aliases and case-insensitive matches remain supported. Wrong senders/chats,
  missing/future server clocks and stale promotions cannot update the profile.
  An unchanged realm still advances valid evidence, so an older card cannot
  subsequently lower it. Ignored backward/stale broadcasts do not emit repeated
  warnings. Accepted changes are saved before notification.
- Same-second new Telegram messages are ordered only inside the same chat;
  edits outrank their own original message, not unrelated same-second edits.
  Cross-chat ties remain unresolved. API request starts are lower-bound local
  observations, not server timestamps: their field clocks use whole seconds,
  Telegram evidence wins a tie, and response arrival never manufactures
  freshness. This does not certify the external API's caching behavior.
- R62 command records retain the exact source of their current parsed payload.
  A same-second older card cannot rewrite payload/pending/followup state.
  Bounded unparsed-reply tracking cannot evict that payload's evidence ID.
  Identical newer cards advance order without repeating the success notification;
  genuine native edits still complete a partial card. SQLite save/reload retains
  this evidence and the per-field chronology.
- Explicit profile API reads capture each identity object/account, a local
  request token, and credential generation/signature. They recheck before and
  after every HTTP await, including errors. Changed owners stop further reads;
  other still-owned roles in a bulk request continue. Old responses cannot
  replace newer UI credentials, status or request ownership, including a UI
  credential change away and back. Cancellation releases only the owned slot.
- Returned IDs must be valid and unambiguous. A nameless payload may fall back
  only through a unique selected cultivator query; `/api/me` and character lists
  never inherit the selected identity. Shared display names can be resolved by
  an explicit ID, but conflicting owners cannot. Current usernames are not
  overwritten by API aliases. Profile updates and snapshots retain newer shared
  values; duplicate rows count once and nested objects never become labels.
  A later read failure preserves already committed progress; local parsing
  failures do not mark the external login unhealthy. Inventory remains untouched.
- Final focused tests: **181 passed, 5 subtests**, 1.72 seconds;
  `/tmp/xiuxian-rebuild-r63-targeted-reviewed-20260910.xml`.
  Initial full checkpoint: **6732 passed, 1202 subtests**, 92.14 seconds;
  `/tmp/xiuxian-rebuild-r63-full-review-20260910.xml`.
  Final full revalidation: **6738 passed, 1202 subtests**, 89.21 seconds;
  `/tmp/xiuxian-rebuild-r63-full-reviewed-20260910.xml`.
  Full selected-rule Ruff, model/test/tool compilation, dependency `pip check`
  and diff checks pass.
- This is profile observation and explicit read ownership, not an R07 transport
  controller. Checkin sect changes, duel/Yinluo cultivation deltas and other
  writers still require their own chronology/accounting review. API verification,
  inventory refresh and keepalive use separate workflows and are not certified
  by these profile-read tests. R11, remaining MiniApp workers, browser/operations
  and whole-project final acceptance remain open. No production code/config/DB,
  service, listener, remote branch, inventory API, skill or live switch changed.

## Deployment Constraint

The chat-key migration is not a code-only rollback. Once two chats contain the
same message ID for one identity, old loaders collapse those rows in memory.
Any later deployment needs a verified database snapshot and an explicit rollback
procedure; do not run the old code against newly written multi-chat state.
This candidate has not migrated the production database.
Legacy active Tianxing steps without account/chat/receipt metadata are not
silently attributed to the current account. Their registered evidence must be
reconciled before any later deployment; inventing missing receipt fields is
not an approved migration strategy.
Legacy rebirth requests/selections likewise require their unique owned command
receipt before automatic continuation. An unknown no-ID request is not proof
that another request or a blind body choice is safe. Reconcile these holds
before deployment; the candidate intentionally does not guess their owner.
Legacy effect state without a valid `set_at` or reported expiry can no longer
authorize downstream work through a release-cache fallback. Resolve it from
existing anchored evidence or separately approved calibration before deployment;
do not manufacture effect clocks from a last-action label or current time.
Old Telegram log rows without `server_event_at` do not establish effect clocks
or qualify for native Tianxing replay. Before any deployment, reconcile retained
operations using available authoritative message dates or separately approved
fresh queries. Do not backfill this field from log receipt timestamps.
Checkin cleanup JSON also changes from bare IDs to chat/message pairs; even
without a same-ID collision, that data must not be handed to the old loader
and cleanup code during a code-only rollback.
The rift result ledger adds an identity-runtime JSON column. The temporary-DB
schema addition is tested; a missing old ledger is not evidence that historical
rewards were never applied. Reconcile pre-migration results and inventory before
deployment. Do not run the older single-result-hash reducer against new receipt
state as a code-only rollback: it can replay rewards already committed here.
Second-soul runtime adds the bounded command JSON and warning-account columns.
New-operation save/reload is tested against temporary SQLite. Legacy training,
purge and heart-demon operations without owned account/chat/receipt evidence
are retained for explicit reconciliation; switching on or restarting does not
erase them. An old no-account warning cannot authorize a new-account choice.
Resolve these holds before any approved deployment rather than inventing
owners, dropping pending work or interpreting missing evidence as failure.
Profile refresh adds two identity-runtime JSON columns for its bounded request
and field clocks. The candidate tests save/reload against temporary SQLite only.
Legacy scalar-only UI followups do not acquire an account or original chat by
guessing; they wait for explicit reconciliation or a new explicit read. Other
profile writers have not all joined this chronological contract. These changes
do not authorize a production schema migration, code-only rollback or deployment.
R63 adds bounded `_evidence` descriptors inside the existing profile-clock JSON
and an optional `profile_evidence` entry in R62 command records. No further SQL
column is added. Older code does not understand the source descriptors; any
future rollback needs a tested data-compatible procedure, not a code-only swap.
Existing timestamp-only observations do not acquire guessed chat/message IDs.

## Next Review Priorities

1. R11 follow-through: audit scalar module anchors (`*_msg_id`), business-level
   guard closures without an expected root/chat, and follow-up sends whose
   `reply_to` has no explicit target chat. The shared pending/history contract
   is now tested end-to-end; it does not prove every module's ownership rules.
   Jiyin/quiz/Tianji routing, second-soul heart-demon broadcasts and the
   checkin/teaching, judgement and Nanlong routes are covered; remaining
   second-soul scalar reply guards and wrapped sends are now covered for new
   operations by R61, including their passive fallback. Unowned legacy state
   and other module scalar anchors still require review.
   Nanlong's send-in-flight reentry and unknown-send automatic retry are now
   covered by R22, and trusted cross-group unthreaded result recovery and exact
   terminal pending cleanup by R24. Result-before-receipt and late detached
   receipt adoption are covered by R33, and queued operation invalidation by
   R34. Replay-time versus event-time follow-up deadlines, cleanup after a
   successful placement whose prompt has expired or whose choice becomes
   reject, and ownership after post-dispatch disable still require review.
   Passing route tests does not close R07 or R11.
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
4. MiniApp follow-through: R31 covers only the generic HTTP/flow runner, not
   every custom game loop. Most custom flows still lack a shared per-run
   budget; Nangongque constructs a new budget for each request. Review
   unknown-result rescheduling and owner invalidation during threaded flows.
   R37 covers shared pool retention/deletion/rebind behavior. R32 closes Requests' hidden
   redirect path, not the remaining runtime lifecycle or entry-refresh matrix.
   R35/R36 cover the tower scheduler, public wrapper and guarded dwelling
   loading/external and tower HTTP threads. Other game callers still need
   individual admission, cancellation and result-retention tests; the optional
   transport check does not automatically protect every caller. R38 covers
   probe cancellation and entry/claim generation changes, not all public-entry
   refresh/invalidation paths. R40 covers stargazer's action loop, public and
   command callers, including shared budget, thread draining and partial
   collection retention. Other custom loops, including Nangongque, still need
   individual review. No live validation is approved.
5. R39 covers the public-entry UI caller's health writes, canary ownership and
   fallback admission independently of R38. Continue through per-game workers,
   background scheduling/batch continuation and the remaining UI control
   contracts. Guarding the entry loader does not stop an unguarded game's
   later HTTP requests or prove its result persistence/notification behavior.
   R41 covers queued background ownership, independent automatic switches,
   completion bookkeeping and local busy admission. It does not make the
   remaining raw threaded game functions cooperative. R42 covers public
   small-world ownership, business evidence, resource snapshot freshness and
   six/eight-hour clock updates, but not the legacy small-world command reducer's
   own internal awaits and cross-chat anchors. Continue with public wild-training
   and its Tianxing preparation/consumption chain, then the other per-game
   workers. Preserve disabled incense-to-consciousness controls and the rule
   that deep retreat does not block or consume Tianxing effects.
   R43 now covers the wild-training worker and public journey action, including
   its caller-side Tianxing preparation checks. Continue with the shared
   Tianxing timeline/craft functions' internal awaits and queued command
   admission; passing mocked preparation tests does not prove those callees.
   R44 now covers the shared timeline's lock, queued command, cancellation and
   receipt boundaries, plus explicit expiry and recovered sending-state safety.
   Continue with outer automatic/follow-up scheduling and craft-consumption
   internals, which still hold state across awaits; also review result reducers
   after module disable. Timeline tests do not certify these callers or R07.
   R45 now covers the outer auto/daily/follow-up lifecycle and auto receipt
   ownership, including retaining pending work across pause/resume. Its
   remaining unknown-send/expired-pending follow-up is covered by R46 below;
   craft/retreat callees and disabled-module reducers are still open.
   R46 now retains uncertain automatic sends and expired mutation pending,
   adopts only exactly correlated existing runtime receipts, and requires
   anchored, argument-appropriate results to close mutations. It does not
   authorize a speculative status-query loop or replay of legacy no-ID work.
   R47 now covers both craft entrypoints and their parent callers, including
   early/unknown transport results, separate panel ownership and business
   backoff. R48 covers new ordinary-retreat/material operations and the actual
   force-exit reply caller, with shared farm receipt handling and independent
   effect admission. Continue with general reducer freshness/idempotence
   (including legacy farm state without correlation, results after disable,
   older-than-last craft replies and unthreaded scalar guard cleanup). The
   bounded pending/last-result evidence is not a replacement for those broader
   reducer contracts or R07 crash-durable transport ownership.
   R49 removes source-less cached guard closure and the two dispatcher bypasses
   for the six direct Tianxing families. It does not validate route-result
   freshness or the distinction between an observed partial reply and terminal
   business completion. Review the routed consumed-message marker and pending
   cleanup on incomplete panels, older-than-last outcomes, farm-family guard
   cleanup, and the normalization branch that clears consumed prediction
   evidence based only on `last_action`. Material receipt quantities of zero
   and malformed ordinary-retreat pending commands also need explicit cases.
   R50 now covers incomplete versus terminal replies, farm/material guard
   closure, late receipt completion and zero-quantity results. General
   observation provenance, older-than-last results, partial-panel field
   freshness and consumed-effect resurrection during normalization still need
   review; the full-suite checkpoint is not final project acceptance.
   R51 removes consumption resurrection from normalization. Continue with
   parsed-result ownership and timestamp provenance, including old predict
   replies and new panels that must not borrow a cached field. Do not infer
   global reducer correctness from safe read-only normalization.
   R52 adds pre-write ownership checks for pending automatic/craft/retreat
   operations. R53 extends this to active timeline reply confirmation, direct
   refusal/CD handling, query-based calibration and bounded receipt replay.
   R54 removes the separate cache-based calibration-release and route-result
   invalidation shortcuts, requires explicit effect clocks for downstream
   admission and retains unresolved mutation ownership through partial replies
   and calibration timeout. Native reply/edit and SQLite reload tests cover
   these paths; they do not establish field-level provenance for every
   observation or replace general chronological reconciliation.
   Replies after pending completion, older-than-last farm results and old
   prediction results still need durable idempotence and chronology tests.
6. R55 covers native effect chronology and the shared Tianxing replay paths,
   not all observation fields. Next review resource/counter deltas after
   pending completion, out-of-order absolute panels, and legacy farm results.
   R56 repairs the legacy rift readers' server clocks and identity/chat/command
   ownership, not all rift behavior. Continue through the live scalar result
   anchors, post-completion item-delta deduplication, and registered account
   ownership across rebinds. Tail truncation is missing evidence, not failure.
7. R57 covers rift timeout retention, panel-only outcome inference, query
   ownership, legacy unknown-state migration and native late-result recovery.
   R58 covers new original-rift dispatch intent, queue admission, early-result
   retention and operation-scoped replay. Continue with the fatal/rebirth
   reducers and their internal awaits, legacy scalar result anchors, duplicate
   resource deltas and result recovery across owner changes. Shared R07
   durability and unknown-operation evidence beyond retention also remain
   open. The candidate is not approved for production deployment.
8. R59 covers new scoped rift-result accounting, bounded replay evidence and
   atomic reward/completion saves. R60 covers new rebirth request/select
   lifecycles, including no-ID holds, known-unsent backoff, cancellation,
   early/late receipts, original-request server completion, new death cycles,
   stale death edits and paused native replay. These targeted repairs do not
   resolve shared R07 crash durability or general R11 scalar ownership.
   Continue legacy scalar/pre-migration accounting reconciliation and module-wide
   resource idempotence separately; do not infer those guarantees from R59/R60.
9. R61 covers second-soul operation/reply/choice ownership, unknown spending,
   server clocks, manual-result coexistence and the UI level-read owner chain.
   R62 now covers explicit profile refresh reentry, early profile replies,
   request-scoped timeout/cleanup and followup/retry scheduler awaits. Revisit
   legacy second-soul evidence and notification/replay limits before deployment.
10. R63 covers breakthrough broadcasts and explicit profile API observations,
    including same-second ordering and request/config ownership. It does not
    certify every profile writer or the supplemental modules' state machines.
    Next inspect `features/checkin.py` sect writes and `features/duel.py` /
    `features/yinluo.py` cultivation reducers for authoritative chronology and
    idempotent accounting. Resource deltas must not be treated as absolute
    profile snapshots just to reuse the observation helper. Separately audit
    API verification, inventory refresh and keepalive awaits, then remaining
    UI refresh/save contracts and MiniApp workers. Do not infer complete UI or
    gameplay health from these tests. R07 still requires separate approval;
    do not introduce its shared controller here.

## Completion Gate

Do not mark the objective complete while any matrix row lacks its required
evidence, a confirmed finding is unresolved, or candidate and deployed behavior
have not been reconciled. Unavailable external-game evidence must be reported
explicitly rather than replaced with a passing mock test.
