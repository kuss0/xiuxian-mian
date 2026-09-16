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

## Stage Handoff (2026-09-15)

At the user's request, the current offline candidate is delivered as a
[staged handoff](rebuild-stage-handoff-20260915.md), without expanding the next
implementation slice. It records the dirty/uncommitted candidate, latest
verification, remaining C1-C6 scope and production/migration boundaries.
This is not whole-project completion or release approval. C1-C6 remain open,
including R67's 19 subordinate roles without per-role production validation.
No production operation, commit or push is performed by this handoff.

The subsequent [C4 owned-JSON review](C4-owned-json-review-20260915.md)
reproduced invalid operation records becoming empty on real SQLite reload,
allowing a simulated greeting dispatch. Its bounded follow-up now repairs
the eleven-field codec contract and passes full regression. This is not a
live-game observation, whole-C4 acceptance or release approval. Offline
candidate review has started; production scope remains unchanged.

## Remaining Work Checkpoint (2026-09-15)

The six packages below consolidate the existing residual work. They are not
six individual bugs, an estimate of remaining effort, or new implementation
authorization. All six remain open despite their component repairs. The ten
acceptance areas below have not been signed off project-wide, and the numbered
review priorities near the end mix completed history with unresolved work.
Neither the latest finding number nor the passing-test count measures project
completion. A reliable remaining-time estimate is not established.

| Package | Remaining scope | Evidence needed to close |
| --- | --- | --- |
| C1 Sending and reply ownership | R11 scalar/legacy anchors, cross-chat cleanup and manual/replayed reply ownership; R07 pre-dispatch durability and no-message-ID crash outcomes | Native dispatch/reply/reload reproducers and an explicit disposition of every residual; a new shared recovery controller requires separate approval |
| C2 Gameplay state and accounting | Remaining Tianxing, duel, Yinluo/Wanxin and legacy small-world reducers; switch/CD admission; profile/resource updates after completion or out of order | Realistic native/manual/edit replays proving identity ownership, authoritative timers and once-only resource changes; scoped prior fixes are not whole-module acceptance |
| C3 MiniApp completion | Tree original-round recovery and historical accounting (current receipts R133 and operation durability R134 are covered); quiet-room legacy outcomes; fishing history/quota inference; trial unknown rounds; treasure unbound enters/history; remaining existing callers' native acceptance | Per-game caller/scheduler and interruption/reload verification, preserving confirmed results without blindly replaying mutations; R67 subordinate-role cultivation still needs the production evidence in C6 |
| C4 Persistence and capacity | Other JSON consumers and already-lost historical records beyond the eleven-field codec checkpoint; legacy unresolved holds, missing historical evidence, migration, hot/cold retention and remaining cross-flow forced-stop recovery; R134 bounds only the current tree operation | Bounded storage and migration/restart checks plus a documented disposition for evidence that cannot be recovered; clearing a hold to regain liveness is not reconciliation |
| C5 UI and operations | Remaining API/browser/control contracts, operational diagnostics and remote CI evidence; two missing real-message families and five monitor/control contracts | End-to-end control checks and explicit evidence for each missing contract; missing real samples must remain unvalidated, not replaced by synthetic success |
| C6 Final review and production reconciliation | Review the complete candidate diff and all residuals, repeat integration acceptance, compare candidate/deployed behavior, and validate R67 cultivation for each affected subordinate role | Whole-project final review, approved deployment/validation plan and actual per-role evidence; current offline tests do not authorize production writes or prove live recovery |

Actionable offline work belongs to C1-C5 and the candidate-review part of C6.
Missing real-message evidence, remote CI and production reconciliation must be
reported separately from code repairs. R07's new controller is not approved;
CommandAttempt stays shadow-only. Do not silently drop these limits to call the
whole objective complete, or expand architecture without approval.

R135 corrects the earlier Nangongque inventory: it is a disabled protocol-only
adapter, not an existing production combat loop. Its executor now accepts a
shared caller budget and operation check. Adding combat automation is a separate
feature requiring captured-response validation and authorization, not a missing
runtime repair to invent for C3. This does not certify live Nangongque support.

For the user's reported `jfdffdddd` subordinate identities, R67 is a candidate
repair only. No production per-role cultivation verification has been performed
in this rebuild. Keep that incident open and explicitly distinguish candidate
behavior from deployed behavior in every delivery summary.

Closeout discipline: use C1-C6 for remaining-work reporting; retain the detailed
finding history as evidence rather than another outstanding-task count. Map any
new reproducer to its existing acceptance contract, record its effect on the
remaining scope, and do not report added tests as completed user workflows.
R131-R134 are scoped candidate checkpoints, not reasons to restart their review
without new failing evidence. R133 repairs current receipt authority; R134 adds
a bounded current-operation journal and local-only recovery. Neither closes C3
or supplies original-round recovery and historical accounting.
R135 also closes the reproduced Nanlong post-placement cleanup, replay-clock
and deferred-recall sub-scope. The C1 Nanlong pause/recovery checkpoint below
also covers returned receipts after disable, passive cleanup while paused,
native toggle/reload continuity, known-result retention and original-window
replay. Unowned legacy/account binding, missing historical outcomes and general
post-result durability remain open. Do not restart accepted sub-scopes without
new failing evidence.
The subsequent C1 orphan-receipt checkpoint covers missing current child-step
fields only when original native ownership/dispatch evidence survives. Partial
or contradictory legacy steps cannot fall through to another send. This is
known-message-ID reconciliation, not R07's unapproved no-ID controller or
whole-package acceptance.

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
| Final review | Revisit every finding and changed contract; record real residual limits | Full suite, targeted fault replay, diff review, deployment comparison | Started offline: scoped C4 owned-JSON codec repair verified; whole-candidate review and production comparison remain incomplete |

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
| R64 | High | Stale or unproven no-sect checkin replies overwrite newer membership, disable modules and delete unrelated pending/receipts; passive handling bypasses ownership, text-only dedupe drops newer edits, and notification awaits let old routed callbacks clear replacement work | Fixed for no-sect checkin observations and checkin availability cleanup in candidate; exact official command ownership, server/source chronology, native manual/channel and log replay, retained sibling pending/anchors, retry suppression, owner-aware completion and SQLite reload pass; positive checkin/teaching provenance, other resource writers and shared R07 remain open |
| R65 | High | Duel/Yinluo cultivation deltas are not reconciled with absolute profile observations; cross-chat scalar IDs omit losses, interleaved/duplicate Yinluo results charge twice, and delayed snapshots can restore spent cultivation | Open; candidate native financial paths use account-bound facts/reservations and atomic resource projections; covered terminal history now has indexed transactional retirement/restoration; legacy migration, business/cold-storage capacity, complete lifecycle and MiniApp snapshot authority remain incomplete |
| R66 | High | The read-only Tianjige banner bridge invokes the mutation reducer, can debit cultivation, clear reservations and rearm scheduling; delayed reads write through changed owners or business state, and cancellation releases the entry lock while HTTP continues | Fixed for the scoped read-only bridge in candidate; panel-only synchronization, pending/partial rejection, owner and business snapshot checks, and thread draining pass focused and full tests; generic Yinluo accounting remains R65 |
| R67 | High | Public-entry selection carries a channel playerId but deep-retreat and quiet-room mutation requests omit it; unscoped account responses are recorded as channel success | Selected-player transport and response checks fixed in candidate; all four deep actions and quiet-room settlement require an explicit identity, wrong/missing account replies cannot advance state; lifecycle follow-through is in R68/R69, while production channel baselines remain unvalidated |
| R68 | High | Retreat entry/action awaits outlive their owner or controls; cancellation abandons worker results, incomplete dashboards authorize actions, and the MiniApp reducer enters Telegram followups | Scoped standalone lifecycle repaired and tested in candidate; selected-role facts survive cancellation without group followups or Tianxing changes; whole-parent and durable unknown recovery require separate acceptance |
| R69 | High | The fate-card parent lacks whole-chain ownership, discards completed actions on failed reconciliation/cancellation, accepts stale or incomplete state, and repeatedly adds the same gains | Scoped parent/adapter lifecycle rebuilt in candidate; native contracts, ownership, cancellation, codec reload and gains covered; retention and scoped unknown postconditions followed up in R70/R72, while forced-stop and general legacy/incomplete outcome reconciliation remain open |
| R70 | High | A global 300-row MiniApp summary LRU evicts active-role business receipts, reopening unknown actions and daily work after reload; deleted identities leave reusable records | Fixed-size per-registered-role game slots no longer compete with disposable summaries; deletion reclaims them and late unregistered writes are rejected; normal codec, no-replay and scheduling regressions pass, while forced-stop durability remains open |
| R71 | High | MiniApp sanitization stringifies deep/unsupported containers before removing secret keys, leaking nested credentials and converting boolean/number evidence to strings | Reproduced with fixture secrets; bounded structured traversal now drops unsupported containers, preserves primitive types and sanitizes tuple children; no live secret was collected or used |
| R72 | High | Any handled retreat status closes an unknown mutation even when it shows the unchanged precondition; fate prerequisites remain blocked even after the same quest is complete | Candidate status recovery checks action-specific postconditions; exact same-business quest completion can bypass an unnecessary prerequisite without fabricating its missing result or reward; unresolved effects retain explicit evidence |
| R73 | High | External Wanxin commission recovery trusts wording, aliases and local delivery time without native publication/acceptance ownership; conflicting edits and replacement commissions can expose older success, while cosmetic edits move the 24h boundary | Candidate native log decoder and both external-recovery consumers rebuilt; 148 focused cases and 7725 integrated tests pass; direct/pending/send lifecycle and durable post-consumption corrections remain open |
| R74 | High | Wanxin nonfinancial sends do not durably bind owner/actor intent before awaiting transport; early or repeated replies leave stale pending work, old edits replace newer actions, and unknown sends acquire guessed cooldowns | In progress; per-action unresolved slots, pre-send financial mirrors, native active/held reply admission, exact hot/cold completion cleanup and corruption guards pass in candidate; final integrated checkpoint 7857 tests, while legacy/forced-stop migration and post-consumption corrections remain open |
| R75 | High | Small-world mutations omit the selected player, accept missing/selector-only account evidence, and leak foreign result text into cooldown decisions; malformed completion flags also count as success | Fixed for the scoped candidate request/receipt contract; selected-player transport, strict snapshot ownership, protected metadata, typed completion and native-shaped partial receipts pass; full suite 7932 tests, while production and general unknown recovery remain unvalidated |
| R76 | High | Journey accepts unowned/selector-only action results and detached pre-action overviews, exposing foreign rewards, cooldowns and Tianxing consumption; invalid selections and nonboolean completion are accepted | Fixed for the scoped candidate adapter/runtime contract; account checks before projection, raw-snapshot admission, strict completion and preserved HTTP classification pass; full suite 7983 tests, while legacy/forced-stop durability remains open |
| R77 | High | Tianjige commands accept invalid/missing selections and unowned result panels; drained cancellations carry raw HTTP objects, and whitelist rejection happens after authorization | Fixed for the scoped candidate request/receipt contract; module-specific positive/negative controls and full suite 8045 tests pass; YuanYing follow-through is in R78, while Tianti lifecycle and production validation remain open |
| R78 | High | YuanYing entry/status awaits outlive owners and controls, cancellation loses dispatched results, unknown launches are replayable and legacy group continuations bypass public-entry holds | Fixed for scoped candidate lifecycle, retained unknown records and group-send exclusion; all 84 new cases and full suite 8129 tests pass; forced-stop durability, general legacy chronology and production validation remain open |
| R79 | High | Tianti has duplicate read paths with false success and missing await ownership; read cancellation/logging loses workflow truth, ambiguous panels rearm timers and nested failures lose shared-limit metadata | Scoped candidate fixes, 174 new cases and full suite 8303 tests pass; native Tianti and general concubine lifecycle/chronology remain open |
| R80 | High | Native Tianti recovery/dispatch outlives owners and controls, retries uncertain spending, overwrites early results and permits a separate passive reducer; scalar/latest-text replay loses scoped evidence and CD display uses the wrong clock | Scoped candidate native lifecycle and reply recovery fixes pass 284 new cases, focused suite 596 and full suite 8587 tests; production, post-terminal corrections and unowned legacy reconciliation remain open |
| R81 | High | Incomplete concubine panels fabricate ready/zero state, passive reads erase mutations, HTTP reads refresh old Telegram anchors and receipt clocks distort observations | Scoped status contract repaired in candidate; 173 new cases, focused suite 745 and final full suite 8760 pass; native dispatch/restart/recovery ownership remains open |
| R82 | High | Native concubine status queries lose early completion, write through replaced owners, discard unknown state and prematurely mark gift opportunities | Scoped query lifecycle repaired in candidate; 184 new cases, focused suite 853 and final full suite 8944 pass; resource-consuming lifecycle and production acceptance remain open |
| R83 | High | Gift inventory/spending lose in-flight ownership, repeat uncertain effects, accept unowned panels and bypass their reducer through passive inventory/dedupe | Scoped candidate repair passes 139 new cases, focused suite 953 and final full suite 9083; legacy migration, broader accounting and production remain open |
| R84 | High | Daily greetings claim ownership after transport, retry unknown effects, fabricate daily completion on timeout and re-greet after explicit no-partner results | Scoped candidate repair passes 120 new cases, focused suite 931 tests and final full suite 9203; legacy migration, broader accounting and production remain open |
| R85 | High | Partial fragment panels borrow cached completion, passive routing bypasses the panel reducer and timeout fabricates missing pieces | Scoped candidate panel/admission repair passes 63 new cases, focused suite 994 tests and final full suite 9266; fragment/dream/puzzle lifecycle, chronology and production remain open |
| R86 | High | Fragment reads lack persisted dispatch ownership, reset on restart, accept unscoped receipts and let stale or rebound confirmation authorize puzzles | Scoped candidate query/confirmation repair passes 137 new cases, focused suite 1389 tests and final full suite 9403; sibling query saves follow in R87, while dream/puzzle mutations, legacy reconciliation and production remain open |
| R87 | High | Failed status-query completion leaves dirty success/pending cleanup, passive dedupe loses replay and recovery can expire a read after a valid result fails to save | Scoped candidate rollback/replay repair passes 106 new cases, focused suite 1254 tests and final full suite 9509; legacy queries, mutations, shared durability and production acceptance remain open |
| R88 | High | Dream/puzzle claim ownership after untracked sends, retry unknown effects, accept scalar/passive projections and invent dream CD on puzzle completion | Scoped candidate mutation lifecycle repaired; 216 new cases, focused suite 1375 and final full suite 9725 pass; legacy reconciliation, other mutations, shared durability and production acceptance remain open |
| R89 | High | Unknown-time voyages use settlement probes and cached waits, unowned status clears holds, and failed recovery saves can expire valid reads into repeated spending | Scoped owned voyage-status/refusal and transactional read-recovery repair passes 151 new cases, focused suite 1713 and final full suite 9876; voyage mutations, legacy reconciliation and production acceptance remain open |
| R90 | High | Voyage mutations lack durable ownership, retry unknown effects, accept ambiguous settlements and bypass ownership through outer summary replay; zero anchors and permissive completed JSON hide replacement/corrupt state | Scoped launch/return, strict results, transactional recovery and replay-exclusion repair passes 240 new cases, focused suite 835 and final full suite 10116; sibling phase cleanup, legacy/accounting and production acceptance remain open |
| R91 | High | Sibling concubine query, gift/greet and dream/puzzle completion, unsent handling and expiry clear replacement phases through zero/boolean scalar anchors | Scoped common phase-release repair passes 53 new native lifecycle cases, focused suite 1593 and final full suite 10169; legacy phase reconciliation, remaining mutations and production acceptance remain open |
| R92 | High | Divination lacks durable intent, retries unknown spending, fabricates cooldowns and accepts scalar/unowned results; incomplete refusal/backoff types admit wrong state | Scoped owned dispatch/result/recovery repair passes 142 additional cases, focused suite 2069 and final full suite 10311; heart/reacquisition, legacy/accounting/durability and production acceptance remain open |
| R93 | High | Heart headers/partial rewards advance or settle a chain, unknown replies clear pending state, and passive/anchor helpers bypass the active text contract | Scoped strict parser/handler repair passes 87 new cases, focused suite 2156 and final full suite 10398; heart ownership, unknown-send/restart/transactional lifecycle and production remain open |
| R94 | High | Heart launch/choices lack durable ownership, replay unknown spending, accept scalar results and lose continuation through probe/outer-scheduler races | Owned session/reply/recovery repair passes 235 lifecycle cases, focused suite 2349 and final full suite 10591; reacquisition, legacy/accounting and production acceptance remain open |
| R95 | High | Spouse reacquisition retries uncertain delivery, accepts scalar results, mishandles intermediate/final edits and resets redirection limits across reads/unsent attempts | Owned lifecycle repaired in candidate; 173 acquisition and 29 shared-phase cases, focused suite 3516 and final full suite 10793 pass; legacy/manual and production acceptance remain open |
| R96 | High | External partner contracts/gains/losses mutate snapshots without strict ownership or idempotence, strand queued phases and falsely resolve missing-field/older status panels | Scoped persisted observation and absolute calibration repair in candidate; 153 new cases, focused suite 3694 and full suite 10946 pass; legacy/manual and production acceptance remain open |
| R97 | High | Wanxin owner/status titles, incomplete or conflicting fields and refusal text falsely complete actions; later partial/type-changing edits erase accounted affinity evidence | Scoped owner-reply contract repaired in candidate; 112 new cases, focused suite 3579 and full suite 11058 pass; commission semantics followed up in R98, production acceptance remains open |
| R98 | High | Commission titles, missing IDs/gains, ambiguous helper pairs and non-execution text falsely settle owned publication/acceptance/identification/cancellation; latest incomplete edits falsely prove external commissions | Scoped nonfinancial reply contract repaired in candidate; 79 new cases, focused suite 1147 and full suite 11137 pass; forced-stop/legacy reconciliation and production acceptance remain open |
| R99 | High | Restored Wanxin sending blocks independent work forever; unbound financial replies clear beneficiary state before the provider can settle, and native cleanup can discard its unadopted receipt | Scoped process-liveness and exact-receipt repair in candidate; 39 new cases, focused suite 1186 and full suite 11176 pass; unowned legacy/chronology and production acceptance remain open |
| R100 | High | Old moon-status reads overwrite a newer concubine affinity snapshot, and already-covered greeting/seal effects are counted again | Scoped covered-write suppression preserves completion/CD and pending ownership; nine new cases, focused suite 3101 and full suite 11185 pass; general cross-writer chronology remains open |
| R101 | High | Legacy/manual status panels overwrite newer Wanxin affinity, and owned native/MiniApp fingerprints miss affinity changes that return to the original value | Retained native evidence rejects strictly older snapshots without new state or retry control; 48 added cases, focused suite 3421 and final full suite 11233 pass; broader chronology and production acceptance remain open |
| R102 | High | Unowned native/passive status reads bypass source and commit checks, stale query edits overwrite newer panels, and log recovery loses channel/edit/forward provenance or holds rejected reads forever | Scoped observed-read transaction, native/log provenance and replay repaired; 171 new cases, final focused suite 3938 and full suite 11404 pass; general chronology, legacy mutation migration and production acceptance remain open |
| R103 | High | Same-second native reads double-count covered Wanxin gains/costs or overwrite later outcomes; an edited gain round trip defeats scalar plan equality | Verified original read/command order repairs the scoped boundary without new state; 81 new cases, associated suite 3740 and full suite 11485 pass; overlapping/legacy/post-consumption accounting and production acceptance remain open |
| R104 | High | Unprojected owned greeting/gift gains leave a spendable stale balance; older status/moon reads overwrite confirmed gains and newer moon reads fail to clear calibration | Derive calibration from existing completion/read evidence without new state; 65 new cases, associated suite 4091 and full suite 11550 pass; broader affinity history, other mutation engines and production acceptance remain open |
| R105 | High | Fishing cancellation abandons HTTP, budgets are ignored, later failures erase confirmed rounds and chain summaries drop HTTP limit evidence | Scoped worker draining, guards, shared budget and partial-result retention repaired; 51 new cases, associated suite 413 and full suite 11601 pass; outer callers, durability and production remain open |
| R106 | High | Fishing callers overlap, apply stale work to replacement owners, lose drained settlements and partial catches, and misacknowledge daily reports across awaits | Scoped caller guards, one game lock, confirmed cancellation adoption, fact-only projection and retained daily reporting repaired; 95 new cases and full suite 11696 pass; atomic accounting, historical replay and production remain open |
| R107 | High | Fishing splits result/inventory/reminder commits, ignores save failure, reports unsaved completion and has no local-only recovery | Scoped atomic projection and bounded pending recovery repaired; 56 new cases, associated suite 547 and full suite 11752 pass; historical replay, unknown effects, input authority and production remain open |
| R108 | High | Fishing explicit-empty catches fall back to history; unconfirmed or contradictory counts/readiness grant gains, and capture accepts more catches than confirmed rounds | Scoped shared count/catch/readiness authority repaired; 50 new cases, associated suite 523 and full suite 11802 pass; remaining material projection, historical replay and production remain open |
| R109 | High | Fishing drops reward-only rounds, ignores rewards in mixed chains, recursively imports diagnostic gains and fabricates quantities from invalid/zero input | Shared current business projection repaired; 73 new cases, associated suite 707 and full suite 11875 pass; historical replay, unknown effects, source/quota authority and production remain open |
| R110 | High | Fishing abandons uncertain finish and transient result reads, losing dispatch/unknown evidence across partial rounds and cancellation | Bounded same-token result recovery and explicit transport/outcome evidence repaired; 46 new cases, associated suite 753 and full suite 11921 pass; caller/startup ownership, history and production remain open |
| R111 | High | Fishing calibrates quotas from historical/generic fields, conflicting aliases and unconfirmed/stale replies; worker output loses current quota ownership | Scoped current-field authority repaired with one shared parser; 90 new cases, associated suite 846 and full suite 12011 pass; inferred quotas, source clocks, startup/history and production remain open |
| R112 | High | Fishing has no durable per-request ownership across settlement/next boundaries; an old ready result cannot resolve an unknown next | Scoped checkpoints, v2 accounting, both callers, startup/UI and six process-interruption cases verified; 147 new cases, associated suite 964 and full suite 12158 pass; history/cross-owner migration and production remain open |
| R113 | High | Yinluo keeps reservations after proven pre-dispatch cancellation, misreports completed/archived return receipts and permits send-time replacement or stale unsent admission | Scoped Yinluo/financial-Wanxin finalization repaired; 49 added cases and full suite 12207 pass; exact cold receipt lookup, idempotence, SQLite rollback and owner changes covered; R65 migration/capacity and production remain open |
| R114 | High | Expired Yinluo reads leave detached shared pending rows, use preparation clocks despite actual receipts, reject late binding and race active scheduler callers | Scoped atomic read expiry, exact pending cleanup, late-receipt and active-caller ownership repaired; 62 new cases, associated suite 955 and full suite 12269 pass; R65 and production remain open |
| R115 | High | Yinluo saves terminal facts before shared pending cleanup, then loses cleanup after reload/log loss/retirement; guard cleanup ignores exact ownership or cold evidence | Scoped atomic completed-pending retirement, local fact recovery, due-time-independent cleanup and exact hot/cold guard matching repaired; 55 new cases, associated suite 1102 and full suite 12324 pass; R65 and production remain open |
| R116 | High | Resource holds strand completed query pending/guards, skip read expiry and let sticky book gaps trigger ineffective calibration; manual dispatch still encounters old completed guards | Scoped read-only completion/recovery separated from financial admission; 78 new cases, associated suite 1627 and full suite 12402 pass; R65 and production remain open |
| R117 | High | Legacy pending normalization, unavailable-module cleanup and corrupt-root decoding erase unresolved evidence; existing books and the MiniApp bridge miss malformed legacy holds | Scoped evidence retention and observation codec repaired in candidate; 122 added cases, focused suite 253, associated suite 1880 and full suite 12524 pass; legacy outcome migration and production remain open |
| R118 | High | Proven legacy completions remain held; malformed old command/slot/transport evidence and type-erasing staged comparisons can also release unsafe work | Scoped exact native-backed migration verified in candidate; 128 added cases, associated suite 2058 and full suite 12652 pass; general no-ID and production acceptance remain open |
| R119 | High | Terminal work with current balances exhausts capacity without useful native calibration; unknown read retention loses late ownership or fills the receipt book, and full read slots still admit queries | Scoped native calibration and exact expired-read archival verified in candidate; 82 added cases, associated suite 2003 and full suite 12734 pass; business-point/cold-storage capacity and production remain open |
| R120 | High | Distinct assistance beneficiaries permanently exhaust the hot business map; incomplete hot/cold moves, self-retirement and changed native proof can discard or reopen business clocks | Scoped native-backed cold points and manifest/transaction guards verified in candidate; 78 focused cases and final full suite 12812 cases/1275 subtests pass; cold-storage scaling, unproved legacy points and production remain open |
| R121 | High | Trial workers ignore per-run budgets, open unused next rounds and outlive cancelled callers; public/command exclusion and awaited ownership differ, while partial and failed-notification reporting loses facts | Scoped offline lifecycle repair verified: 92 new cases, expanded associated 558 cases/19 subtests and final full suite 12904 cases/1275 subtests pass; finish/quota authority, durable unknown recovery, report retention and production remain open |
| R122 | High | Trial transport success fabricates settlements; loose quota/reward fields and unbound request/challenge metadata can authorize extra rounds or misattribute gains | Scoped receipt, quota and metadata repair verified; 185 new cases, final associated 683 cases/19 subtests and full suite 13089 cases/1275 subtests pass; temporal/history authority, durability and production remain open |
| R123 | High | Trial batches collide, stale authorization and late send failure overwrite ownership/outcomes, and timeout reports retire missing results | Scoped registry, authorization, result precedence and late-report retention verified; 37 new cases, final associated 829 cases/19 subtests and full suite 13126 cases/1275 subtests pass; durability/capacity and production remain open |
| R124 | High | Trial restart loses pending requests/results; loose checkpoint transitions, ownership/save races and active-worker recovery can reopen or misattribute work | Scoped owned journal, strict transitions, checked saves, local-only recovery and caller admission verified; 128 new cases, associated 2668 cases/210 subtests and full 13256 cases/1275 subtests pass; unknown reconciliation, entry-allocation/history authority and production remain open |
| R125 | High | Treasure ignores request budgets/selection, outlives cancellation, overlaps public/command callers, loses partial settlements and mislabels unknown outcomes as daily completion | Scoped shared lifecycle, account exclusion, selected-player dispatch, retained settlements and unknown holds verified; 109 new cases, associated 2722 cases/278 subtests and full 13365 cases/1275 subtests pass; authority/history/durability and production remain open |
| R126 | High | Treasure opens from missing/invalid quota, accepts foreign player/session and nonterminal receipts, fabricates quantities and mixes historical or multiple-round rewards | Scoped current quota, response binding and native material projection verified; 145 new cases, final associated 2867 cases/278 subtests and full 13510 cases/1275 subtests pass; temporal/history identity, durability and production remain open |
| R127 | High | Treasure saves inventory and state separately, acknowledges failed writes, repeats returned results and strands local recovery behind gameplay controls | Scoped atomic returned-result projection, strict local recovery and caller/account admission verified; 147 new cases, associated 3235 cases/307 subtests and full 13657 cases/1275 subtests pass; pre-dispatch durability, history and production remain open |
| R128 | High | Treasure has no durable per-request intent or intermediate receipts; process death loses stage ownership, while transport-entered cancellation and intermediary 4xx can be mistaken for unsent mutations | Scoped journal, strict transitions, linked atomic recovery, account admission and successor-read preservation verified; 182 new cases, associated 3417 cases/307 subtests and full 13839 cases/1275 subtests pass; authoritative unknown reconciliation, historical migration and production remain open |
| R129 | High | Native treasure guesses/clamps missing or sparse cells, repeats unchanged reveals and loses prior board evidence across partial replies; exhausted boards can remain unnecessarily held | Scoped board authority/progress repair verified; all 83 new cases, focused 957 cases/19 subtests, associated 3500 cases/307 subtests and full 13922 cases/1275 subtests pass; original-round recovery and production remain open |
| R130 | High | Already-accounted treasure journals permanently block original rounds; naive continuation can repeat earlier rewards or erase unresolved work | Fixed for scoped original-session recovery in candidate; 70 new cases, six actual continuation kill/reload boundaries and final full 13992 cases/1275 subtests pass; review also closes quota-admission and shortened recovery-spacing gaps; unbound enters, history and production remain open |
| R131 | High | Tree workers outlive cancelled callers, lose accepted results after later failures, omit per-flow budgets and allow stale owners or unknown rounds to reenter through public/UI scheduling | Fixed for scoped lifecycle in candidate; 175 new cases, expanded associated 4361 cases/295 subtests and full 14167 cases/1275 subtests pass; receipt authority, forced-stop recovery and production remain open |
| R132 | High | Tree treats malformed or inconsistent quota as authority, discovers counts in unrelated nested panels and continues from regressed or incomplete post-submit state | Fixed for scoped current quota in candidate; 174 new cases, associated 4535 cases/295 subtests and full 14341 cases/1275 subtests pass; receipt authority, durable recovery and production remain open |
| R133 | High | Tree clears an open round on HTTP success before checking the receipt, accepts coerced/mismatched score evidence, and counts historical or quantity-less rewards | Fixed for current receipt/binding and reward authority; partial known rewards remain separate from completed rounds; 485-case focused native/SQLite and full 14400-case/1275-subtest regressions pass; durable intent/result, original-round recovery and production remain open |
| R134 | High | Tree has no durable per-mutation intent/result boundary, so process death loses pending allocations or returned settlements and final publication can lose its saved-state acknowledgement | Scoped current-operation journal and atomic local publication verified; seven actual kill/reload boundaries, focused 608 cases and full 14428 cases/1275 subtests pass; original-round recovery, history and production remain open |
| R135 | High | Nanlong drops recall after confirmed placement when the prompt expires or rejection is chosen; replay uses old action time, delayed rejection receipts lose protection, and unsent recall consumes retry budget or loses cleanup | Scoped cleanup/recovery/backoff fixed using existing fields; focused 242 cases/329 subtests and full 14440 cases/1284 subtests pass; 12 new cases and nine new subtests, no removals; disabled Nangongque protocol scope and budget composition also corrected, not enabled |

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

### R64 Evidence

- Added 70 regression cases. The first 26 cases had 25 failures on R63,
  reproducing stale direct/passive/edit membership rollback, untrusted or
  wrongly owned denials, cross-chat pending deletion, duplicate disabling and
  destructive scheduler availability cleanup. Evidence:
  `/tmp/xiuxian-rebuild-r64-reproduced-20260910.xml`.
  Follow-up review reproduced discarded newer edits, malformed context inputs
  and replacement/rebinding during notification, rather than treating the first
  passing reproducer suite as final acceptance.
- Both no-sect paths now use one reducer. It requires an existing active
  identity, an official sender in a configured game group, a positive server
  timestamp and exact checkin command/root ownership. Native command text,
  sender and creation time are retained for passive/manual processing, including
  channel senders. Owned pending records support log replay without guessing
  a missing server result time. Conflicting roots, chats, commands or owners,
  malformed values and corrupt profile clocks cannot authorize a mutation.
- Membership is an absolute profile observation using R63 field/source order.
  Older or incomparable same-second evidence cannot replace a newer sect or
  disable its modules. Legacy profile timestamps remain conservative when the
  sect-specific clock is absent. Stale but owned terminal replies can settle
  their own pending; they do not reset another operation's schedule or guard.
- Removed the unproven public disable helper and its broad business-state
  reset. A fresh accepted denial stops sect switches and future checkin/teaching
  scheduling, not already-sent work. Sibling pending rows and sent receipts,
  other modules' cooldowns, scalar reply anchors and in-progress phases survive.
  Existing affected pending rows receive `max_retry=0`; only the exact denied
  checkin root is completed. Scheduler and teaching availability checks likewise
  stop new work without deleting unrelated receipts or last-result anchors.
  This does not introduce a new pending-retention controller: later shared
  timeout retirement and R07 forced-stop durability still require review.
- Native no-sect edits may revisit a previously consumed message; passive
  no-sect observations use durable source chronology instead of a text-only
  cache. Duplicate observations do not re-disable a later UI choice or repeat
  notifications. Newer native edits still advance the membership clock. The
  reducer distinguishes rejected evidence, handled observations and actual
  changes so passive diagnostics do not manufacture changes on duplicates.
- State is saved before notification. Cancellation/failure at notification
  retains the committed sect, switches and exact pending completion after
  temporary SQLite reload. The routed callback rejects missing identities and
  checks its captured identity object/account after the checkin await; it cannot
  continue cleanup into a replacement or rebound owner. Passive handling keeps
  its existing owner check. No new retry/recovery authority was added.
- Final focused tests: **357 passed, 19 subtests**, 2.31 seconds;
  `/tmp/xiuxian-rebuild-r64-targeted-reviewed-20260910.xml`.
  First full run exposed a new test fixture leaking passive diagnostic counters
  into the small-world suite; the fixture now isolates counters and their file
  writes, without changing the small-world assertion or production behavior.
  Final full revalidation: **6808 passed, 1202 subtests**, 92.90 seconds;
  `/tmp/xiuxian-rebuild-r64-full-reviewed-20260910.xml`.
  Full selected-rule Ruff, model/test/tool compilation, dependency `pip check`
  and diff checks pass.
- Positive checkin/teaching observation provenance, duel/Yinluo resource deltas,
  other availability-control cleanup, remaining MiniApp/API workers, R11,
  operations/capacity and whole-project final acceptance remain open. No
  production code/config/DB, service, listener, remote branch, inventory API,
  skill or live switch changed.

### R65 Review Evidence (Open)

- Candidate checked: `8cd733ee`. The isolated reproducer is
  `/tmp/xiuxian-rebuild-r65-resource-probe-20260910.py`.
  It runs `tests/conftest.py` before model imports, uses synthetic identities
  and fixtures, patches module saves, and does not start clients, send requests
  or touch production state. Run with the candidate venv and
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; exit code 1 records the known mismatches.
- Five reducer-level probes failed:
  1. Two 60,000 cultivation losses in different chats, where the second chat's
     message ID is smaller: initial 500,000, expected 380,000, actual 440,000.
  2. An old 60,000 loss arrives after an absolute snapshot already reporting
     200,000: expected 200,000, actual 140,000.
  3. A 60,000 loss at T+20 is applied, then the T+10 absolute snapshot arrives:
     expected 440,000, actual 500,000. Dedupe alone cannot repair this case.
  4. Yinluo conversion A, B, then replay A after the passive-cache TTL:
     expected cultivation 80,000 and sha 4,300; actual 70,000 and 6,300.
  5. The same 50-cost soothe result twice: initial 1,000, expected 950,
     actual 900.
- Current source explains those results: `_record_managed_duel_loss` keeps one
  `resource_last_loss_msg_id` without chat identity or a snapshot baseline.
  Yinluo conversion remembers only its last result key; soothe directly deducts
  on every success. `apply_profile_observation` correctly orders absolute
  observations against other absolute observations, but does not project later
  known deltas over an earlier arriving snapshot.
- These probes establish reducer defects, not their live frequency or
  production losses. Before implementation, trace the real duel reply/manual
  call paths and both Yinluo inputs: mutating results through `passive_inbox`
  and banner snapshots through
  `cave_treasure_runtime._sync_cave_tianjige_read_only_message`. The latter is
  a read-only panel caller, not a mutation receipt. Preserve command/identity/
  chat/server-time or owned MiniApp-read evidence rather than inventing it at
  receipt time.
- Required design: separate absolute baseline observations from owned deltas;
  account each semantic action once, allow distinct out-of-order deltas, retain
  newer deltas when a delayed baseline arrives, reject older corrections and
  define bounded retention without re-enabling evicted charges. Corrupt/legacy
  provenance must not authorize spending from an overestimated balance.
  This is business accounting, not permission to implement R07 transport
  persistence, CommandAttempt recovery or automatic retry control.
- At that review-only checkpoint, no R65 implementation was claimed. R64's
  6808 passed/1202 subtests did not cover the newly reproduced accounting
  defects. The following candidate work is partial implementation, not closure
  of R65 or whole-project acceptance.

### R65 Candidate Cultivation and Duel Accounting

- Work is confined to the offline candidate. The production tree, services,
  switches, database and skill are unchanged. No game requests, inventory API
  reads, listener startup, deployment or push were performed.
- `resource_accounting.py` separates absolute snapshots from signed resource
  facts. Each fact retains an original-command lower bound when proved, a
  result upper bound, revision evidence and an amount-change floor. A baseline
  overlapping execution or a correction is not guessed to include the delta.
  Changed-then-restored amounts cannot let an older identical result shrink
  that floor. Same-second incomparable observations remain unresolved.
- `cultivation_accounting.py` owns the runtime JSON
  `{account_id, ledger, profile_value}` under `xiuwei_accounting`. It reconciles
  accepted Telegram profile snapshots and later deltas without an await.
  Account changes, corrupt state, external profile writes, and API display
  updates cannot silently become verified spending balances. An API request
  start does not establish the server's balance revision, so API reads remain
  display observations, not resource baselines in this candidate.
- The new JSON column is included in normal and delta persistence. Invalid
  JSON remains invalid rather than loading as empty history. Temporary-SQLite
  tests verify that projected profile values and ledger writes roll back
  together on a rejected write, and that source ownership, withdrawn results
  and deduplication survive reload. This is not atomic send/result persistence
  and does not implement R07 or promote CommandAttempt beyond shadow-only.
- Native new/edit events enter duel accounting before later awaited handlers.
  Official group/sender and Telegram server clocks are required. Manual
  battles and results after module/global disable still reconcile facts;
  they do not enable any automation. The old scalar-ID/raw-subtraction duel
  helpers are removed. Both managed winners and losers are accounted for.
- A message first seen without its original command can later acquire that
  evidence without creating a second debit. Duplicate final messages tied to
  one command likewise share an operation. Retained message sources still
  identify the previous owners after a newer edit removes a participant or
  all result text. Unknown amounts, duplicate/conflicting roles and ambiguous
  username aliases invalidate spending authority instead of assuming zero or
  choosing the first identity. Literal zero is a real correction. Decimal
  quantities and supported units are parsed exactly, not through binary float
  or an accepted prefix of an unrecognized numeric token. An out-of-range
  projected balance is not written to SQLite integer profile fields or allowed
  for spending; unpersistable cultivation snapshots are rejected as well.
- Existing daily-log scans retain official edits, including empty/non-report
  edits, and metadata-only intermediate reply nodes. Resource replay follows
  at most eight decreasing message IDs in one group, with official parent
  senders and native nondecreasing server times, to the original command.
  Player links, cycles, conflicting originals, missing clocks and `sent`
  bookkeeping rows do not supply that proof. `ts_epoch` remains local log
  time and is not used as resource evidence. No new remote history reads or
  speculative status requests were added. An edited parent command fetched
  natively is marked as edited and does not prove its original command text.
- A ledger is limited to 256 operation entries and 16 message sources per
  operation. Uncovered overflow blocks spending; only a sufficiently later
  verified baseline clears the gap. Covered entries may be retired, and old
  covered replay does not apply a new debit. Duplicate persisted operation or
  source ownership is rejected. These tests establish bounded ledger behavior,
  not a complete archive/operational-capacity policy.
- New duel dispatch checks both balances and preserves the sending identity
  object/account plus the managed target object/account/username through
  preparation and the existing queue `operation_check`. Ambiguous target
  aliases cannot bypass defender checks as an external target. Missing balance
  evidence does not prevent recovery of an already-sent duel. Equipment,
  manual count/CD reconciliation and other internal awaits still need their
  own ownership review; this does not certify the whole duel state machine.
- Boundary probes found and repaired missing-participant withdrawals, duplicate
  facts after source enrichment, filtered-out edits, source-capacity replay,
  changed-back amounts, malformed numeric prefixes and changed target owners.
  Final focused run: **254 passed, 6 subtests**, 2.00 seconds,
  `/tmp/xiuxian-rebuild-r65-final-focused-20260910.xml`.
  Full isolated run after final integer-range review: **6942 passed,
  1202 subtests**, 91.47 seconds,
  `/tmp/xiuxian-rebuild-r65-full-final-20260910.xml`.
  Ruff, `compileall`, `pip check` and whitespace checks passed.
- R65 remains open. Yinluo conversion/soothe still have their old cultivation
  writers; its sha pool additionally involves sacrifice income, refine
  reservation/rollback, external spending and MiniApp banner panels. Those
  writers must be reconciled together, not mixed with a partially projected
  sha balance. Other resource writers also remain outside this integration.
  Review first-seen results after retired source evidence, cross-day original
  command availability, real MiniApp snapshot authority, account migration,
  and capacity/replay behavior before enabling this candidate. A conservative
  hold is not proof that those automation paths are complete.

### R66 Candidate Read-Only Tianjige Boundary

- Work continues in the offline candidate only. Production, service processes,
  live state/database, listener and skill remain untouched. No game requests,
  automatic inventory reads, deployment, restart or push were performed.
- Reproducers in `tests/test_cave_tianjige_read_only.py` showed that asking for
  `.我的阴罗幡` could accept conversion, soothe, sacrifice or refine wording
  through `apply_yinluo_passive`. Ordinary panels also cleared refine pending,
  changed automatic timers and erased collections absent from the response.
  Owner deletion raised a KeyError, while rebinding, replacement, disable and
  newer business observations did not stop the delayed write.
- The bridge now calls `sync_yinluo_miniapp_status`, which copies panel fields
  without invoking resource deltas, completing an operation, clearing a
  reservation, changing resource-recovery requests or rescheduling automation.
  Active collect/refine/soothe and pending outcomes, plus unanswered mutating
  commands in the identity's pending map, retain their state. Unrelated pending
  work does not prevent a banner read.
- Title-only, missing pool/slot fields, duplicate or unknown slots and partially
  parsed collections cannot replace a snapshot. Missing optional collections
  stay unchanged; a declared empty/unrecognized collection is not guessed to
  mean zero. Both existing real banner fixtures pass. Their server-reported
  sha above capacity remains intact, including `269465 / 25000 (100%)`.
- Read-only requests retain the identity object/account and relevant business
  snapshot, and recheck them before/after session initialization and command
  execution. Entry invalidation and explicit game rejection stop synchronization.
  HTTP admission receives the same check. Existing channel-freeze and
  maintenance-only MiniApp exceptions remain available.
- The command-center flow uses the existing guarded thread-drain helper and
  still makes no HTTP retry. Cancellation before admission prevents dispatch;
  cancellation during HTTP keeps the public-entry lock until the worker ends,
  propagates cancellation and does not save or announce the abandoned read.
  This does not certify mutating callers' result recovery or implement R07.
- Final focused run: **275 passed, 5 subtests**, 2.06 seconds,
  `/tmp/xiuxian-rebuild-r66-final-focused-20260910.xml`.
  Final full isolated run: **6997 passed, 1202 subtests**, 91.28 seconds,
  `/tmp/xiuxian-rebuild-r66-full-final-20260910.xml`.
  Ruff, `compileall`, `pip check` and whitespace checks passed.
- R65 is still open: this is a status/ownership boundary, not a reconciled sha
  ledger, an authoritative MiniApp resource timestamp or a migration strategy.
  Review conversion, soothe, sacrifice, refine reservation/rollback and Wanxin
  external spending together. Native event provenance is still discarded by
  the Yinluo passive caller; generic reducers still need ordered, idempotent
  resource facts. Empty collection semantics and cleanly truncated/cached
  panels also need evidence before this path can supply a spending baseline.

### R65 Offline Yinluo Facts And Projection

- Construction follows `docs/audit/R65-yinluo-resource-contract-20260910.md`.
  Only the financial fact/ownership stage is closed. The staged book is a
  partial implementation of the projection stage, not runtime acceptance.
  The old Yinluo/Wanxin reducers, resource pre-debit, whole-snapshot rollback,
  send lifecycle and passive caller remain unchanged by this checkpoint.
- Strict original-command admission checks the official bot, game chat,
  native command/message clocks, unedited original, unique managed sender and
  account/hint types. The real native resolver supplies the tested metadata.
  Unresolved routing hints do not reject an otherwise proved original sender;
  conflicting hints and edited originals do not create fallback authority.
- Cost/income components preserve unknown amounts, actual server-reported
  conversion costs and separate summon start/backlash phases. Observed 1362
  summon backlash and provider-only 500-cultivation/120-sha strip backlash are
  represented explicitly. A success format without its cost does not inherit
  the old 50/80/120/400/1000 planning defaults as a transaction.
- The caller-supplied cultivation ledger is projected with sha/soul facts as
  one returned value, without duplicate storage or state writes. Native
  snapshots reconcile earlier/later deltas; source-scoped edits and unknown
  fields cannot silently become zero. Record A/B/A, cross-chat equal IDs,
  corrected amounts, duplicate soothe, source enrichment, old bonus removal,
  renamed/ambiguous participants, changed soul types, sparse panels and
  charge/progress/outcome permutations are covered. Existing duel entries
  remain intact during Yinluo projection.
- Empty or contextless native edits of retained official messages invalidate
  only the proved old owner, never credit new text through the fallback.
  Projection-cache disagreement, unknown legacy provenance and account
  rebinding cannot supply spendable values. JSON roundtrip is covered; this
  is not the pending SQLite transaction/reload acceptance.
- Input and history are bounded. Book overflow/command conflict retains the
  old facts and creates a hold, not an evict-and-retry policy. Source-count
  overflow uses the existing scalar-ledger coverage contract. Operational
  archival, reservation retention and projection latency still need review.
- The full 256-receipt scenario exposed repeated whole-ledger copying. Folding
  each command/component locally before merging the resource projection
  reduced that offline test from 6.08 to 0.49 seconds on this host. Uncovered
  shared cultivation entries remain protected; appending a duel entry or
  retiring an already-covered entry does not falsely invalidate the cache.
  This is a bounded construction test, not a live scheduler latency guarantee.
- Final focused run: **407 passed**, 2.45 seconds,
  `/tmp/xiuxian-rebuild-r65-yinluo-foundation-focused-20260910.xml`.
  Final full isolated run: **7328 passed, 1202 subtests**, 91.28 seconds,
  `/tmp/xiuxian-rebuild-r65-yinluo-foundation-full-final-20260910.xml`.
  Ruff, `compileall`, `pip check` and whitespace checks passed. The new
  Yinluo-only suites contain 331 cases; the focused total also includes the
  existing shared resource and cultivation suites.
- No production state, setting, service or skill was changed. The new modules
  are not referenced by runtime callers, and CommandAttempt remains shadow-only.
  R65 stays open; do not promote this pure-layer checkpoint into a claim that
  the real conversion, refining or assistance chains have been fixed.

### R65 Candidate Yinluo/Wanxin Runtime Integration

- This supersedes the foundation-only integration status above. The changes
  remain entirely in the offline candidate. They introduce the bounded
  `yinluo_accounting` runtime JSON column and integrate the reviewed resource
  book with the existing shared cultivation ledger. No second cultivation
  balance or CommandAttempt controller was introduced.
- Yinluo actions and Wanxin banner/strip sends reserve resources before
  dispatch. Preparation, actual operation checks and receipt handling retain
  identity object/account ownership. The Yinluo sect check now reads the real
  `sect_name` field. Definite non-send releases a reservation; unknown send,
  cancellation and timeout preserve it. Original pre-debit, inferred charge,
  whole-observation rollback and text-hash financial dedupe are removed.
- Resource facts, reservations, provider observations and the matching
  beneficiary's observation commit through one normal SQLite save. Failure
  injection proves rollback/reload for one-identity and two-identity writes.
  Staged payloads are validated before mutation, not only when next read;
  invalid intervals, phases, budgets and capacity cannot write a state that
  its own reader subsequently rejects.
- App routing, passive delivery, timeout recovery and local log replay use
  native original-command evidence. Completion is distinct from a changed
  projection, so a receipt already observed at ingress is still handled by
  routing. Unknown financial replies cannot pass through the old Wanxin
  cooldown/commission-invalid branch. Manual replies close only their exact
  chat/root pending and guard. Generic timeout/silence cleanup does not remove
  consuming Yinluo/Wanxin commands or retry them; rechecks are bounded.
- Actual `_start_game_send_rpc` detached completion and runtime finalize tests
  exposed and repaired a receipt contract mismatch: existing pending rows do
  not carry account_id. Exact operation/module/command/chat plus the persisted
  account-bound book allow adoption; conflicting optional account metadata
  still rejects it. This is not R07 shared crash-durable transport ownership.
- Assistance operations bind the beneficiary account, commission ID and
  publication/acceptance clocks. A late result still charges its provider but
  cannot clear a replacement beneficiary/commission. Bindings survive a
  subsequent send, including after a prior reply was deliberately withheld
  from a changed beneficiary. Completion helpers no longer infer the owner
  username from whichever identity the listener is currently using.
- Summon charge and outcome chronology are separate: a late start-charge edit
  updates the financial component without reopening an already confirmed
  final result. An unknown edit of the final result does reopen its own
  operation. Capacity gaps do not apply unretained business results. Unrelated
  native edits are filtered before full per-identity resource projection.
- Local recovery reads at most two daily log tails, 1 MiB per file, 8192 rows
  and 24 hours. It never sends a query or treats `sent` bookkeeping/local time
  as native command evidence. The three earlier full-suite failures were old
  timeout contracts: assumed consumption, unthreaded observation completion
  and summary-triggered retry. They now assert retained uncertainty, with a
  positive native final-edit recovery control.
- Latest focused verification: **558 passed, 13 subtests**, 7.90 seconds.
  Final integrated full run: **7402 passed, 1202 subtests**, 99.09 seconds,
  `/tmp/xiuxian-r65-integrated-final-full-20260910.xml`; process exit code 0
  was collected after the context handoff. Repository Ruff, `compileall`,
  `pip check` and `git diff --check` passed. This closes that verification
  checkpoint, not the remaining R65 acceptance gates.
- R65 remains open. Legacy financial pending work cannot yet migrate safely;
  256 receipts, 64 operations and 120 business points remain bounded holds,
  not accepted long-term retention. Completed assistance bindings now count
  toward the operation bound and must not be discarded to regain capacity.
  A native balance can cover money without proving the old action outcome.
  Nonfinancial Wanxin owner/identify/external-commission paths, retired-source
  corrections and remaining shared lifecycle/module review are still pending.
  See the contract's Remaining Acceptance Gates before attempting retirement.
- Production, services, switches, database, skill and external game endpoints
  were not changed or accessed. World Boss/auto-refine settings remain out of
  scope. No deployment, restart, push or listener activation is authorized by
  this checkpoint; user-owned quiz/tool changes remain excluded from commits.

### R67 Public-Entry Channel Retreat Identity

- The user's report that Jfdffdddd's other identities had not actually been
  retreating prompted read-only production inspection. The service was active
  with `NRestarts=0`; the 19 channel identities were send-as frozen but eligible
  for the separately enabled public-entry route. All had deep-retreat enabled.
  They were being scheduled, so process health or the `running` phase did not
  establish correct per-role execution.
- In the retained 2026-09-10 capture, all **86 deep-seclusion** and **30
  quiet-room settlement** requests attributed to those 19 channels omitted
  `playerId`. The preceding selected-entry request did carry the correct
  negative Telegram channel ID. For example, at 21:36:22 CST the selected
  request used `-1003581351795`; the subsequent deep status request contained
  only action/authentication fields. Channel deadlines clustered around
  2026-09-11 04:59:27-04:59:37, close to the primary account's deadline.
  Those stored phases/deadlines cannot establish the channels' actual game
  state. Full native action bodies are not retained in these HTTP captures;
  the exact resulting server balance/state must not be invented.
- Both request builders and both production flows now require a valid explicit
  selected `player_id`, preserving its server-provided sign. An invalid or
  mismatched selection is rejected before authentication or HTTP. The separate
  public-retreat and fate-card callers pass the verified session selection
  through status/start/settle/force and quiet-room settlement.
- An action response must name the expected account. A selector echo alone is
  not evidence of action ownership; missing, invalid, mismatched or contradictory
  account/selector metadata rejects the response. Rejected bodies cannot become
  rewards or a running timer. The deep reducer repeats the check before writes,
  and records distinguish identity-verified evidence from old unverified rows.
  No retry or group-command fallback was introduced.
- The earlier request-shape test explicitly expected only the action field and
  mocked the selected-session flow without checking its downstream identity.
  It now checks the selected player. Added tests cover every deep action,
  quiet-room settlement, malformed/missing selections, rejected responses,
  the fate-card helper, and a frozen-channel start through the actual HTTP
  adapter/reducer while the primary role has a different phase/deadline.
- Focused verification: **190 passed, 68 subtests**, 19.68 seconds. Integrated
  full suite: **7411 passed, 1265 subtests**, 100.54 seconds, process exit code 0;
  `/tmp/xiuxian-r67-retreat-final-full-20260910.xml`. Selected-file Ruff and
  `git diff --check` passed. No production deployment or automatic state
  calibration is authorized by these tests.
- The sampled command-center (including Yuanying), journey, details and external
  launch requests carried their selected identities. This is not certification
  that every other MiniApp workflow is healthy. Fate-card draw/choose/settle
  use their own selected launch token and are not interchangeable with dwelling
  requests. Their broader lifecycle and result contracts remain separately open.
- Production inspection used only read-only SQLite, existing local captures
  and journal entries. No game request, setting/state write, service restart,
  deployment, push or skill change occurred. Before any later rollout, obtain
  approval and rebaseline affected roles from current identity-bound snapshots;
  do not bulk reset their timers or treat the old `running` rows as confirmed.

#### R67 Read-Only Production Recheck (2026-09-11 04:59 CST)

- The follow-up user report remains valid: production is still on `a41409fd`,
  without the candidate identity fix. All 19 channel roles owned by account
  `301299112` retain `deep_retreat_enabled=1`. Their group-send freeze has a
  matching restore roster, which `is_cave_public_identity_available()` allows
  through the independent public-entry path. Global execution and public
  deep-retreat status are enabled. This is not simply a disabled module.
- Existing local captures, read at 04:59:47 CST without game requests, contain
  1,613 rows for September 10 and 1,177 for September 11. The September 10
  totals remain 86 deep actions and 30 quiet-room settlements without
  `playerId`. September 11 adds 26 deep actions and 23 quiet-room settlements,
  also all without `playerId`, across the same 19 roles. The last such captured
  action is at 02:20:19 CST; these are bounded observations, not future totals.
- Concrete September 11 example: channel `3581351795` is selected with
  `playerId=-1003581351795` at 00:14:17 (capture line 115), but its quiet-room
  settlement at 00:14:38 (line 125) and deep start at 00:14:42 (line 130) omit
  that field. The source tags alone do not bind an action to the channel.
  Production accepts those responses without checking action account ownership.
- All 19 local phases say `running`; their deadlines still cluster around
  04:59:27-04:59:37, beside the primary role's 04:59:40 deadline. Do not present
  these old projections as confirmed channel cultivation. Other selected-role
  requests, including journey and Yuanying, are present; the evidence does not
  support claiming that every MiniApp workflow is idle or correct.
- Rechecked candidate identity, retreat/fate lifecycle and record-retention
  suites: **196 passed, 63 subtests**, 11.04 seconds, exit code 0;
  `/tmp/xiuxian-channel-retreat-recheck-20260911.xml`. Channel freeze/public-entry
  controls separately passed **8 tests**, 0.60 seconds. These focused checks
  are not whole-project acceptance or permission to deploy the dirty candidate.
- Production files, switches, timers, database and services were not modified.
  The issue remains operationally open until separately authorized rollout and
  current identity-bound rebaseline. Do not bulk reset timers, replay uncertain
  mutations, or unblock channel group sends to mask this defect.

#### R67 Read-Only Production Recheck (2026-09-11 06:00 CST)

- Rechecked after the user specifically reported that the subordinate roles
  were not retreating. Production remains on `a41409fd`; main, observer and
  watchdog are active with `NRestarts=0`. Global execution is enabled, with no
  global pause. All 19 subordinate roles owned by `301299112` have deep retreat
  enabled and are present in the channel-freeze restore roster. Their public
  HTTP admission is not disabled by that group-send freeze.
- The bounded September 11 capture now contains **47 deep actions** (17 start,
  10 settle, 20 status) and **23 quiet-room settlements** attributed to these
  roles, all missing `playerId`. The last captured deep status is at 05:51:52
  CST. The September 10 counts remain 86 deep actions and 30 quiet settlements.
- At 05:45:23 CST, role `3581351795` is selected with
  `playerId=-1003581351795`; its immediately following deep-status request again
  has only action/authentication fields. The persisted role record says
  `ok=true`, but has neither a verified identity nor a player ID. Its workflow
  log adopts the unscoped remaining-time reply as that role's running state.
  This is not evidence that the selected role actually entered retreat.
- All 19 local retreat deadlines have advanced to 13:16:20-13:16:22 CST,
  immediately beside the primary role's 13:16:24 deadline. Old projections
  therefore remain untrusted; neither healthy processes nor future timers
  close this incident. Rebaseline must use current identity-bound game facts,
  not bulk timer resets or a claim that all roles are already cultivating.
- Other recorded requests are not universally idle: September 11 includes
  four subordinate Yuanying status and four dispatch requests, plus 30 journey
  requests, each carrying `playerId`. HTTP success alone does not certify
  their complete business outcomes. This recheck does not clear all MiniApp
  workflows or assert that every subordinate role has Yuanying enabled.
- Candidate identity, retreat/fate lifecycle, public runtime/protocol,
  Tianxing-retreat independence and retention tests: **603 passed, 68 subtests**,
  31.70 seconds, exit code 0. Artifact:
  `/tmp/xiuxian-channel-retreat-recheck-20260911-0600.xml`. These are offline
  tests of the candidate, not a production rollout or live-role recalibration.
- Production files, services, configuration and database were not changed;
  no game request was made. Deployment and current per-role calibration still
  require separate authorization. The whole-script goal remains incomplete.
- A further read-only database check at **07:05:34 CST** still finds all 19
  subordinate retreat switches enabled and all 19 local phases `running`, with
  the same unverified 13:16:20-13:16:22 deadlines. The local capture recheck in
  this continuation still contains 47 deep actions and 23 quiet settlements
  without `playerId`. The exact 05:45:23 selection/status pair for role
  `3581351795` was re-read: selection has the channel ID, status omits it.
  These observations confirm an operationally open defect, not successful
  channel cultivation or a need to enable group sends. No rollout approval was
  received; production code/state were not changed and no game probe was sent.

### R68 Deep-Retreat Lifecycle Checkpoint (2026-09-11)

- Follow-up review reproduced stale dispatch after entry reads, writes through
  a replaced/rebound identity, discarded completion after cancellation, and
  audit failures replacing a confirmed result. The standalone route now retains
  its identity object/account, deep-retreat schedule and previous MiniApp record
  across entry/action awaits. A changed control stops admission; an unchanged
  owner still adopts confirmed in-flight completion after disable/pause without
  re-enabling the switch. Explicit manual calls retain their existing semantics.
- The existing blocking-flow runner now owns both deep-seclusion and quiet-room
  HTTP execution. Cancellation drains the worker before releasing the caller's
  entry lock, including repeated cancellation. The standalone route records the
  returned fact before re-raising cancellation. This is not R07 crash-durable
  shared send ownership, and does not add retries or a CommandAttempt takeover.
- Start/force/settle share one action helper and require matching dashboard
  identity plus explicit permission. Unrelated nested status fields, malformed
  counters and contradictory flags cannot fabricate a running/ready state.
  Native remaining seconds take precedence over rounded display text. A missing
  start permission cannot turn an idle message into a 30-second start loop.
- MiniApp reconciliation no longer invokes Telegram reply handlers, delayed
  status probes, message deletion, summary-command replay or group guard closure.
  It applies local deep-retreat facts only; Tianxing state/effects are unchanged.
  Confirmed exit snapshots and the known full summary text are supported.
- A dispatched uncertain mutation, malformed HTTP 200 response, or cancellation
  without a worker result retains uncertainty in the existing MiniApp record.
  Subsequent failed session/status reads do not erase it. A matching status can
  resolve it; an unverified timer or missing receipt cannot. The metadata codec
  round-trip and lock recreation are tested, without a new database column.
  Legacy fallback now requires explicit no-dispatch evidence and no unresolved
  mutation; a bare old `ok=false` row cannot authorize another group command.
- The R67 helper test had mocked out the fate-card parent calls. Follow-up found
  that force/start callers still omitted `session`; both now pass it and the
  parent test checks the exact sessions. The redundant overview-only permission
  argument and duplicate standalone action/reconciliation block were removed.
- Added `tests/test_cave_retreat_lifecycle.py` covers admission/completion races,
  cancellation drain, logging failures, parser conflicts, unknown preservation,
  negative fallback controls and a real-adapter offline sequence: primary entry,
  selected frozen channel, selected start POST, channel-only state update.
  The new suite has 79 cases. Latest integrated run: **7490 passed, 1265 subtests**,
  101.54 seconds, exit code 0, `/tmp/xiuxian-r68-retreat-full-20260911.xml` and
  the adjacent `.log`. Selected-file Ruff, `compileall`, `pip check` and
  `git diff --check` passed.
- Remaining: the whole fate-card parent still needs an owner/business boundary
  spanning draw/interpret/choose/quiet-room/retreat/reward awaits. Its quiet-room
  completion adoption and whole-chain unknown/restart behavior are not certified
  by the standalone retreat tests. R68 is a component checkpoint, not complete
  fate-card acceptance or whole-project Final Review. R65's remaining gates are
  unchanged. Do not deploy this candidate on the strength of a green test count.
- No production deployment, restart, switch/database write, game request, push,
  listener activation or skill edit occurred. Read-only production git status
  still showed only the user's quiz-bank and dump-tool changes. The affected
  channels' old timers were not reset; later authorized rollout still requires
  current, identity-bound recalibration as specified under R67.

### R69 Fate-Card Parent Lifecycle Checkpoint (2026-09-11)

- Continued R68 through the full fate parent rather than treating the selected
  deep-retreat helper as proof of the whole chain. Existing 2026-09-10 local
  captures confirm that fate mutations return a dated `record` with `createdAt`,
  cards, question/choice keys and quest metadata. They do not expose the dwelling
  `account.playerId` contract. No unsupported player ID was added to fate HTTP.
  The parent instead checks both the selected dwelling player and its account
  before trusting an external launch, including after dashboard refreshes.
- The fate adapter now rechecks admission across WebView awaits, uses the
  existing draining worker runner and shares one bounded fate request budget.
  Mutations remain one POST each. An explicit native rejection is distinguished
  from an HTML/missing-contract response or a post-dispatch parser exception;
  the latter cases cannot be mislabeled as known-unsent.
- One parent operation retains identity/account, relevant controls, deep-retreat
  schedule/record and its own prior fate record across the whole chain. It
  separates admission from completion: disabling stops further requests, while
  a confirmed in-flight result can still be retained by its unchanged owner.
  Rebind, replacement, deletion and a newer business record cannot receive old
  writes. Deep actions use the operation captured before entry, not a fresh
  owner captured after unrelated awaits.
- Decision snapshots distinguish incomplete fields from actual false/zero.
  Day, record identity, quest identity, choice and monotonic progress must match.
  A terminal quest cannot trigger interpretation or another retreat. A direct
  completed mutation is retained if its follow-up read fails or cancellation
  arrives, but a failed read cannot authorize the next stage from cached state.
  A conflicting direct action and panel are not combined into one confirmation.
- Gains are collected only from a matching native action, not a whole account
  snapshot or an unbound reward dictionary. Per-stage receipts plus a fixed
  pre-run baseline prevent repeated checkpoints from adding rewards twice.
  Draw rewards are included when actually returned; promised record rewards
  and already-claimed rewards are not newly credited. Missing action balances
  retain the last observation with an explicit non-native flag rather than
  fabricating a zero balance. The driver was reduced from 540 to about 210 lines
  by sharing step execution, waiting and terminal handling.
- Added `tests/test_fate_cards_lifecycle.py`: owner/control interleavings,
  native contract and chronology failures, drained cancellation, unknown
  preservation across normal metadata codec reload, gain dedupe, and a complete
  mocked-HTTP selected-channel chain through the actual adapters. Its channel
  remains group-send frozen, its primary role remains unchanged, and every
  mutation is sent once. These are offline fixtures, not new game outcomes.
  Focused retreat/fate verification: **298 passed, 68 subtests**, 11.16 seconds.
  Final integrated verification: **7568 passed, 1265 subtests**, 113.24 seconds,
  process exit code 0; `/tmp/xiuxian-r69-fate-final-full-20260911.xml` and the
  adjacent `.log`. Selected-file Ruff, `compileall`, `pip check` and
  `git diff --check` pass. No deployment approval follows from these tests.
- Remaining acceptance gaps are explicit: an unresolved quiet-room/deep action
  is retained as a hold, not automatically replayed. A later authoritative
  same-business fate panel can resolve a fate action without inventing gains;
  conflicting and legacy/unowned records remain held. General quiet/deep unknown
  resolution and cross-day reconciliation still need a reviewed contract.
  Normal codec survival is not crash durability: the existing shared MiniApp
  summary retention can evict a record, and this change adds no pre-dispatch
  durable ownership or durable save acknowledgment. That retention boundary
  must be resolved before rollout, without bypassing the unapproved R07 work.
- R65's remaining acceptance gates and whole-project Final Review remain open.
  Production code, database, switches, services, listener and skill were not
  changed. No game requests, deployment, restart, commit or push occurred.
  Production git status still contains only the user's quiz-bank/dump-tool
  changes. Old channel timers were not reset or treated as verified; an
  authorized rollout still needs the per-role rebaseline described under R67.

### R70-R72 MiniApp Record Retention and Outcome Recovery (2026-09-11)

- Reproduced actual runtime effects of summary pressure, not only a smaller
  dictionary: after more than 300 rows and a metadata codec reload, fate draw
  and quiet-room settlement were sent again, the deep-retreat unknown marker
  disappeared, and the treasure guard no longer held. These offline failures
  are distinct from R67's observed missing-player-ID production defect; there
  is no claim that the 19-channel incident itself exceeded the retention cap.
- Runtime records now occupy one fixed slot for each registered identity and
  each of eight declared game keys. Their row bound is `8 * registered_roles`,
  independent of uptime, attempt count and `outcome_unknown` flags. Disposable
  summaries retain a separate 300-row LRU. Arbitrary game keys cannot reserve
  slots, repeated writes replace a slot, and disabling/freezing an identity
  does not discard its evidence. Explicit identity removal reclaims its records
  by exact key prefix; late writes to an unregistered identity cannot recreate
  them. The snapshot exposes both limits and their sum.
- This is a change to retention of existing latest-state records, not a shared
  transport controller, a new retry queue, unbounded pending pinning, or a
  capacity-triggered permanent hold. No SQL schema or codec migration was
  introduced. New business readers must use a declared runtime game key; their
  schema/field bounds and module-specific historical receipts still need their
  own review. Neither this roster bound nor a successful codec reload proves
  crash durability, save acknowledgment or coverage of already-evicted legacy
  evidence. R07 and R65 retention gates are not closed by this change.
- Deep containers previously became raw `str(dict/list)` before secret-key
  filtering; a tuple did so at any depth. Fixture tests reproduced both secret
  exposure and conversion/loss of true, false, zero and numeric evidence at the
  depth limit. Containers beyond the limit and unsupported values are now
  omitted without stringification; supported primitive types remain intact,
  and tuple children receive the same filtering as lists. This does not add
  provenance to missing or truncated business fields.
- An unchanged idle panel cannot resolve an unknown start; an unchanged active
  or settlement-ready panel cannot resolve an unknown force/settle. Both the
  preflight-skip and explicit status paths now keep those results unknown.
  Verified running/ready state can resolve start; verified inactive/startable
  state can resolve an exit. Rejected, contradictory, ambiguous or unrecognized
  action observations do not become confirmation. No mutation retry or
  Telegram/Tianxing followup was added.
- Fate recovery now distinguishes prerequisite outcome from quest completion.
  Only an owned, verified same-day/record/choice/quest observation that is
  terminal or explicitly ready to settle can make an uncertain quiet/deep
  prerequisite unnecessary. The old effect remains marked
  `unconfirmed_prerequisite`; its reward is not invented. A ready quest may
  claim its own native reward once, and an already terminal quest sends no
  mutation. Changed day/record/quest or insufficient progress keeps the hold.
  General reconciliation of incomplete quiet-room work and legacy/unowned
  outcomes remains open; a changed balance or local timer is not a receipt.
- Added retention tests exercise 320 declared runtime slots, arbitrary summary
  churn, disabled/removal/re-add cases, actual runtime guards, daily scheduling
  and codec reload. Retreat/fate lifecycle regressions now include summary
  pressure, completion during pressure, unknown-action postconditions and
  prerequisite completion without guessed gains. Focused verification:
  **556 passed, 78 subtests**, 30.88 seconds;
  `/tmp/xiuxian-r70-miniapp-focused-20260911.xml`. Integrated full regression:
  **7601 passed, 1275 subtests**, 110.64 seconds, process exit code 0;
  `/tmp/xiuxian-r70-miniapp-full-20260911.xml` and the adjacent `.log`.
  Do not treat this component checkpoint as whole-project acceptance.
- Broad `ruff check model tests`, `compileall`, `pip check` and `git diff --check`
  pass. Production git still shows only the user's quiz-bank/dump-tool changes.
  There was no game request, service restart, deployment, production state or
  switch write, listener activation, commit/push or skill edit. R67's per-role
  native rebaseline remains mandatory before any separately approved rollout.

### R65 Covered-History Retirement Checkpoint (2026-09-11)

- Resumed the unfinished retention work after R67-R72. Added two real runtime
  reproducers before wiring the archive: completed assistance stopped on the
  65th operation despite later native balances, and repeated panels overflowed
  the 256-receipt book. The old capacity holds prevented unbounded growth but
  did not establish long-running automation. Both reproducers failed first and
  pass with the candidate path.
- `R65-yinluo-retention-contract-20260911.md` specifies the narrow retirement
  boundary. Only native terminal command groups, with every affected concrete
  resource covered and no possible live owner, may be archived. Unknown soul
  names, conflicting/uncovered sources, unknown actions and legacy gaps stay
  hot/held. Shared foreign cultivation entries and business clocks are not
  discarded. No send, retry or new resource authority is created.
- Archive rows retain original native receipts and the completed operation's
  beneficiary/commission binding. Exact chat/result/sender indexes support
  contextless edit recovery; byte bounds and payload/index validation prevent
  damaged cold rows from becoming current facts. A SQLite writer lock precedes
  CAS checks, and archive insert/delete plus hot-state updates share the normal
  transaction. Tests inject failure after archive changes and verify both SQL
  and in-memory rollback. This is not R07 transport ownership.
- Corrected a review-found second-edit defect: a transient restoration marker
  protected only the first edit of archived manual assistance. Bounded
  `restored_roots` now survives the normal codec and cannot name absent hot
  receipts. A second edit after reload still cannot invent an original manual
  beneficiary. Native and contextless orphan-index cases are both held;
  compaction cannot hide conflicting ownership of a still-hot result.
- Native read calibration keeps a reserved operation slot and can establish
  coverage before transactional retirement. Existing capacity gaps are not
  cleared, unknown commands are not completed by their balances, and no fresh
  inventory API query is introduced. R66 remains observation-only for financial
  authority. Deep retreat/Tianxing and World Boss controls are unchanged.
- An 80-cycle temporary-SQLite stress with six operation slots/12 receipts
  ended with two hot operations, four receipts, 157 cold commands and 92,796
  encoded cold payload bytes. The latter excludes SQLite/WAL overhead. Cold
  evidence has no automatic TTL: disk monitoring/export and storage limits are
  still open operational work, not solved by a bounded hot list. The separate
  120-point business bound and legacy/full-uncovered migration also remain open.
- Final verification after the orphan-index correction: **608 passed, 13
  subtests**, 38.82 seconds in the focused suite; **7639 passed, 1275 subtests**,
  139.24 seconds in the full suite, both process exit code 0. Broad
  `ruff check model tests`, `compileall`, `pip check` and `git diff --check`
  passed. These are component/integration checks, not whole-project Final
  Review or permission to deploy. Artifact paths:
  `/tmp/xiuxian-r65-retention-focused-20260911.{xml,log}` and
  `/tmp/xiuxian-r65-retention-full-20260911.{xml,log}`.
- R65 and whole-project Final Review remain open. The current production
  checkout still has only the user's quiz-bank/dump-tool changes and retains
  R67's old unscoped retreat request builders. No game request, deployment,
  restart, production state/configuration write, listener/skill change, commit
  or push occurred. The 19 channel roles' actual state must still be rebaselined
  from identity-verified native evidence during a separately authorized rollout.

### R73 External Commission Replay Checkpoint (2026-09-11)

- Resumed the unfinished external-commission work after the user's R67 live
  report. The inherited focused process had terminated with one failing old
  fixture: it logged only strip wording, with no original command, bot/chat
  provenance or native clock. The fixture now retains its legitimate scheduler
  behavior using a complete synthetic publication -> acceptance -> strip chain
  in a temporary log file. Provenance checks were not weakened to pass it.
- `wanxin_commission_replay.py` now derives actors from original native commands,
  requires configured game chats/bot IDs, and traverses bounded, same-chat
  original reply links. A username confirms a unique target, not the actor.
  Duplicates and shuffled delivery do not change the result; same-second
  cross-chat or edited sibling ambiguity cannot pick an arbitrary success.
  Conflicting original/intermediate metadata and later unknown replies retain
  uncertainty rather than exposing older successful text.
- Publication ID and the native acceptance command bind the current commission.
  A second acceptance is ambiguous even when one helper is configured locally.
  Later owner publication/cancellation commands remain visible without replies;
  a newer or conflicting lifecycle cannot restore the old commission. Explicit
  `commission_existing` for the same ID and `commission_cancel_blocked` are
  distinguished from successful replacement or unknown outcomes.
- Final diff review found another failure after an initially green full run:
  cosmetic publication edits moved its 24-hour cancellation clock and could
  make an earlier valid acceptance look out of order. A native fact's first
  uninterrupted confirmation and its latest source revision are now separate.
  Corrected/unknown content breaks the earlier confirmation; cosmetic edits
  and uniquely resolved rename aliases do not. Publication deadlines use the
  fact clock, while panel values use the latest revision clock. Equal or newer
  existing panels and unrelated pending actions are preserved. A native
  publication also corrects an exaggerated, unverified local cancel deadline.
- A full scheduler regression reproduced an unnecessary 24-hour hold when the
  first log read already included external completion: the old branch adopted
  acceptance and returned before consuming completion. Both recovery entry
  points now share one completion projection, and a complete native chain is
  settled on the first pass with no cancellation/republication command sent.
- Log IO is bounded to 26 hours, at most three daily tails, 1 MiB per file and
  8192 records. Overflow does not yield a partial batch; truncated originals
  cannot be replaced with `sent` bookkeeping. Non-object/malformed JSON and
  invalid dates/native metadata are rejected. Temporary-file and fault tests
  cover these boundaries; no Telegram history request or game query was added.
- The new replay suite has 86 cases. Combined Wanxin verification is **148
  passed, 13 subtests**, 1.05 seconds, exit code 0;
  `/tmp/xiuxian-r73-commission-replay-20260911.xml`. The earlier broader resource
  checkpoint passed 335 cases and 13 subtests; the pre-clock-fix full run passed
  7718 cases and 1275 subtests. After the clock and first-pass completion fixes,
  final integrated verification passed **7725 tests, 1275 subtests**, 140.32
  seconds, process exit code 0;
  `/tmp/xiuxian-r73-final-full-20260911.xml`. Broad Ruff, `compileall`,
  `pip check` and `git diff --check` pass. These results do not establish
  whole-project Final Review or authorize deployment.
- This is not acceptance of the Wanxin controller. Direct nonfinancial replies,
  owner/accept/identify sends, early receipts, timeout cleanup and shared scalar
  anchors still require review. In particular, failure to recover a log result
  does not prove that cancellation or republication is safe: the current
  scheduler's fallback admission needs an owned current-commission contract.
  Late revisions after consumption and truncated/legacy histories still need
  durable business reconciliation. Do not count these gates as closed by a
  decoder test or introduce a blanket permanent hold as their replacement.
- R65, R07's separately gated transport architecture and whole-project Final
  Review remain open. All work stayed in the offline candidate; production git
  still has only the user's quiz-bank/dump-tool changes. No game request,
  production state/switch write, deployment, restart, listener activation,
  skill edit, commit or push occurred. R67's affected production roles still
  require a separately approved rollout and native per-role rebaseline.

### R74 Nonfinancial Wanxin Lifecycle Checkpoint (2026-09-11)

- This is an in-progress controller rebuild, not acceptance of the entire
  Wanxin module. Owner, acceptance and identification sends now retain an
  operation ID, actor/owner account bindings, original chat, exact command and
  commission snapshot before awaiting transport. Admission is checked again
  inside the existing send queue. A disabled, rebound, replaced or superseded
  owner cannot receive a stale send result. A real completion that arrives
  before the send receipt is not replaced with a new pending operation.
- Known-unsent outcomes use one classified transport result and only defer
  scheduling. Unknown/no-ID/cancelled sends retain the original operation;
  neither a missing receipt nor elapsed reply timeout invents success or a
  gameplay cooldown. The former guessed-cooldown helper and unused permissive
  owner lookup helpers were removed. This does not transfer recovery authority
  to CommandAttempt or introduce an automatic mutation retry.
- Direct nonfinancial replies require a configured official bot/chat, native
  original command/sender and server clocks. Account hints cannot contradict
  the source. Targets must resolve uniquely; native actors, not display names,
  establish ownership. Pending recovery uses the same bounded native ancestry
  decoder as R73 instead of matching result wording or a forum topic root.
- Reproducing tests found duplicate completion returning before cleanup and
  normalization aliasing the compare-and-swap baseline. Normalization now
  owns its nested values. Repeated/cosmetic replies can close their exact
  restored business/shared root without replaying a gain or removing newer
  sibling work. Conflicting shared command/account records are not cleared.
  Native command order dominates an old command's later edit, while a newer
  command is not hidden by the previous command's cosmetic revision clock.
- Reply checkpoints use fixed action keys, exact bounded fields, validated
  command/result/completion points and a semantic digest. Malformed records
  remain visible and hold Wanxin dispatch; they are not silently discarded.
  Cosmetic edits preserve the first completion time. An explicit changed
  remaining cooldown is a different fact and recalibrates that action from
  its native reply time. Normal JSON roundtrip and duplicate handling are
  covered; this is not a claim of forced-stop durability or legacy migration.
- Cancellation requires the current native publication/acceptance chain and
  its real 24-hour boundary. Missing evidence does not authorize cancellation;
  independent owner actions are still considered when only that cancellation
  precondition is absent. A same-ID `commission_existing` reply preserves a
  known external claimant and original publication clock. A stale same-second
  cancel command cannot consume a newer publication.
- Verification collected the inherited process: **193 passed, 13 subtests**.
  Follow-up reproducers exposed six pending/order/unsent failures, ten malformed
  checkpoint cases, and three scoped-cleanup/cooldown-edit failures. A first
  full run also caught an incorrectly aliased corruption fixture, corrected
  to corrupt only the completion point rather than both source clocks.
  Final focused verification: **214 passed, 13 subtests**, 1.39 seconds;
  `/tmp/xiuxian-r74-focused-final-20260911.xml`. Final integrated verification:
  **7791 passed, 1275 subtests**, 143.28 seconds, exit code 0;
  `/tmp/xiuxian-r74-final-full-20260911.xml`. Selected-file Ruff, broad fatal/
  undefined-name Ruff, `compileall`, `pip check` and `git diff --check` pass.
- Remaining gates are substantive: a single unresolved operation still holds
  the controller; truncated/unrecoverable histories need a reviewed liveness
  and migration contract. Same-point contradictory replies, corrections after
  commission consumption, affinity corrections across newer absolute reads
  or other writers, and manual commands arriving during cancellation admission
  still need review. A permanent hold is not the long-running solution, and
  these tests do not close R74, R65, R07 or whole-project Final Review.
- No production deployment, restart, DB/configuration write, game request,
  listener activation, commit/push or skill edit occurred. World Boss and
  automatic incense refinement remain untouched. R67's 19 affected channel
  identities still need a separately authorized rollout and current native
  per-role rebaseline; future local timers are not that evidence.

### R74 Action Isolation and Recovery Review (2026-09-11)

- The per-action isolation contract is recorded in
  `R74-wanxin-unresolved-contract-20260911.md`. Unknown work now occupies fixed
  action slots rather than holding every independent owner action. Commission
  dependencies and affinity-spending exclusion remain intact. Existing local
  native evidence is replayed once per paced batch; no game/history query or
  speculative mutation retry was added. New-operation financial mirrors are
  saved before transport, with unsent rollback when that save fails.
- Owner and assist projections no longer mutate global state before selecting
  the exact operation. Observation, affinity/enabled updates and owned shared
  pending cleanup are saved together and restored on failure. Late results can
  finish a held action without erasing an independent active one. Cancellation,
  early completion and provider owner/object changes retain their original
  operation instead of manufacturing a new pending binding.
- Review reproducers exposed malformed active/held action/status containers,
  different operation UUIDs occupying one native message root, a matching
  provider legacy-check exception, and completed financial replies leaving
  restored owner slots permanently unresolved. These cases are repaired:
  invalid records remain available for audit and block sends/projection without
  crashing; root collisions include chat identity; completion cleanup requires
  the original provider operation and beneficiary snapshot. A conflicting
  account, commission, UUID, command or chat/message root is not silently
  deduplicated or discharged.
- Hot and archived duplicate assistance replies now remove only a matching
  restored Wanxin slot. They cannot replay sha/cultivation deltas, overwrite an
  independent active action, or consume a newer commission. The cold path reads
  the exact archived command without rehydrating its resource history. Actual
  SQLite reload, archive statistics and unchanged provider state are asserted.
- Native identification can reconcile its original pending actor after the UI
  selects another helper. A helper's username-only rename uses existing alias
  history and no longer invalidates an already accepted commission. These
  narrow repairs do not establish complete legacy account/commission migration.
- The inherited full-test process was not a valid integration result. Runtime
  inspection found a completed executor callback waiting in the loop's ready
  queue while the old sandbox rejected the local self-pipe send with
  `PermissionError: Operation not permitted`. Its traceback and probe are in
  `/tmp/xiuxian-r74-pytest-wait-stack.txt` and
  `/tmp/xiuxian-r74-pytest-selfpipe.txt`. The process was explicitly terminated,
  not reported as passing. The unchanged candidate then passed **7823 tests,
  1275 subtests** in 140.35 seconds in a new network namespace with local
  wakeups/loopback allowed and external networking unavailable.
- New fault artifacts include `/tmp/xiuxian-r74-review-before-20260911.xml`,
  `/tmp/xiuxian-r74-legacy-provider-before-20260911.xml`,
  `/tmp/xiuxian-r74-archived-duplicate-before-20260911.xml` and
  `/tmp/xiuxian-r74-helper-before-20260911.xml`. The initial review artifact
  also caught a test assertion assuming an always-present false flag; the
  healthy cross-chat case now uses the documented optional flag.
  The expanded related suites passed **488 tests, 13 subtests**, 40.59 seconds;
  `/tmp/xiuxian-r74-liveness-review-20260911.xml`. After the helper fixes, the
  related subset passed **351 tests, 13 subtests**, 8.99 seconds. Final full
  regression passed **7857 tests, 1275 subtests**, 140.25 seconds, exit code 0;
  `/tmp/xiuxian-r74-final-review-full-20260911.xml`. Broad Ruff, `compileall`,
  dependency, JS syntax and `git diff --check` checks passed.
- Remaining work includes legacy/unattributable operations, persisted sending
  state after forced stop, same-point contradictions, corrections after
  commission consumption and affinity chronology across other writers. R65
  operational migration, R07's separately gated shared transport architecture,
  remaining MiniApp workers and whole-project Final Review are not closed.
- Production git was rechecked read-only and still contains only the original
  quiz-bank and dump-tool changes. No production code/state/switch write,
  deployment/restart, game request, listener activation, skill edit, commit or
  push occurred. R67's 19 channel roles still require separately authorized
  rollout and native per-role rebaseline; the old local timers are not proof
  of successful cultivation.

### R75 Small-World Selected-Player Review (2026-09-11)

- Continued the subordinate-MiniApp audit after the R74 checkpoint. Small-world
  reads carried the selected identity but actual mutations did not. Missing or
  selector-only replies were accepted; a foreign failure's wait/resource text
  could enter local scheduling. The initial scoped reproducer had **60 failing
  cases**, including real entry selection through the production adapter with
  only mocked transport. This is distinct from an actual production probe.
- The builder/flow now require and preserve the selected player's ID. Initial
  snapshots and action receipts use R67's strict account validator before
  business projection. Planner metadata cannot overwrite action, identity or
  auth fields. Dispatch/HTTP backoff survives rejection; partial owned success
  survives missing resource panels and cancellation without repeating a POST.
- Native September 11 captures show two primary-account collect requests
  without `playerId`, with response shapes containing `account.playerId` and
  boolean completion. They do not prove live channel resource spending. The
  offline fixtures were calibrated to that minimal account metadata. A further
  **3-case reproducer** showed that zero/null/string completion flags counted
  as success; the boolean contract is now strict when the field is supplied.
- Related suites passed **414 tests, 68 subtests** before the final expansion
  to all ten small-world actions and malformed completion. Final full isolated
  suite: **7932 passed, 1275 subtests**, 141.32 seconds, exit 0;
  `/tmp/xiuxian-r75-identity-full-20260911.xml`. Broad Ruff, compileall,
  `pip check`, JS syntax and diff checks pass. Details and before artifacts:
  `R75-small-world-player-contract-20260911.md`.
- This does not close R65/R74, shared R07 durability or Final Review. Next
  inspect journey/command-center result ownership and Yuanying's awaited
  lifecycle, then continue the remaining acceptance matrix. No production
  changes, live game request, listener activation, skill edit, commit or push
  occurred; R67 rollout and current identity-bound baselines remain unapproved.

### R76 Journey Selected-Player Review (2026-09-11)

- Followed the same identity contract into journey. Unlike retreat/small-world,
  the sampled production journey requests already carried `playerId`; 36 native
  request/response shapes were inspected without sending game requests. The
  reproduced bug is accepting incomplete/contradictory identity evidence and
  detached overview data, not proven live misattribution for those 36 requests.
- **35 failing regressions** exposed invalid selections, unowned initial and
  final data, nonboolean completion, and a worker consuming Tianxing text from
  selector-only evidence. The request flow and its runtime consumer now both
  enforce the explicit account contract. A verified raw panel replaces cached
  overview admission. Unowned action data cannot publish rewards or server
  cooldowns, while dispatch and HTTP rejection/Retry-After remain distinct.
- Extended integration covers frozen-channel entry selection, all three wild
  strategies, signed/normalized player IDs, unchanged primary state, owned
  rewards and 4xx/5xx classifications. The lifecycle file passes **139 tests**;
  full network-isolated candidate verification passes **7983 tests and 1275
  subtests**, 142.39 seconds, exit 0. Artifact:
  `/tmp/xiuxian-r76-identity-full-20260911.xml`. Ruff, compileall and diff checks
  pass. Fixture corrections and scope are documented in
  `R76-journey-player-contract-20260911.md`.
- Command-center/Yuanying response ownership and awaited lifecycle remain the
  next MiniApp review target. R65/R74 migration and capacity, R07 and whole
  Final Review are still open. No production, service, configuration, timer,
  listener, skill or remote change occurred. R67 rollout is still unapproved.

### R77 Command-Center Selected-Player Review (2026-09-11)

- Reproduced 55 request/response/cancellation contract failures, then repaired
  explicit player selection, account-bound business data, session ownership,
  typed completion and worker-carried result DTOs. No POST retry was added.
  HTTP rejection, Retry-After and uncertain launch outcomes remain distinct.
- Replaced weak cross-module negative fixtures with parseable Tianti/Yinluo
  panels and matching positive controls. Four further failing regressions
  showed invalid commands obtained authorization before whitelist rejection;
  normalization now precedes authorization.
- Full network-isolated suite: **8045 passed, 1275 subtests**, 140.76 seconds;
  `/tmp/xiuxian-r77-command-full-20260911.xml`. Broad Ruff, compileall and diff
  checks pass. Evidence and residual scope:
  `R77-command-center-player-contract-20260911.md`.
- YuanYing still needs owner/control/schedule revalidation throughout its two
  requests, cancellation-result adoption and a bridge independent of legacy
  asynchronous handlers. Tianti awaits and general chronology remain open.
  R65/R74, shared R07 and Final Review are not closed. Production and its
  channel retreat baselines remain unchanged; R67 rollout is not authorized.

### R78 YuanYing Lifecycle Review (2026-09-11)

- Reproduced 41 lifecycle failures and rebuilt the public status/launch chain
  around captured owner, controls, schedule and record evidence. The MiniApp
  bridge no longer calls async group handlers; confirmed dispatched results
  survive cancellation, later switch-off and failed notification without
  overwriting a replacement owner or newer business state.
- Added a retained per-identity launch intent and explicit unknown hold.
  Unchanged warm panels cannot rearm it; normal codec reload and summary
  pressure preserve it. The UI schedules only its status check. Three further
  failing cases exposed legacy warm continuation/queued-send bypasses, now
  blocked through the existing module/send guard boundary. Deep retreat and
  Tianxing are not blocked by this YuanYing-specific hold.
- Two contradictory-panel regressions, seven malformed-marker/rejection
  diagnostics regressions and one zero-countdown postcondition case were also
  repaired. Final isolated full verification passed **8129 tests and 1275
  subtests**, 139.97s, including all 84 new YuanYing cases;
  `/tmp/xiuxian-r78-yuanying-completion-full-20260911.xml`. Broad Ruff,
  compileall, pip check and diff checks pass. Evidence and residual gates:
  `R78-yuanying-lifecycle-contract-20260911.md`.
- This remains candidate work, not production recovery. R67 rollout/channel
  rebaseline, Tianti awaits, general legacy chronology, R65/R74 migration,
  shared R07 durability and whole-project Final Review are not closed.

### R79 Read-Only Lifecycle Review (2026-09-11)

- Initial **58 failing** cases exposed false-success generic Tianti reads,
  unguarded dedicated-entry awaits, cancelled-but-unapplied success, notification
  loss, stale/pending status writes and ambiguous cooldown/progress parsing.
  Both entrypoints now use the same owner/control/schedule/entry guard and
  real status bridge. No mutation command or retry was introduced.
- MiniApp Tianti partial panels now keep all prior state and report incomplete
  synchronization, rather than certify cached progress or reset timers. The
  recorded full panel and existing module behavior remain covered.
- Initial full suite passed **8271 tests, 1275 subtests**, 141.70s. A further
  **18 failing** review cases exposed nested shared-limit metadata loss, local
  wait misclassification and idle concubine unresolved anchors. These are
  repaired; related tests pass **444 tests, 5 subtests** and actual UI/background
  integration passes **278 tests**. Final isolated full suite passes **8303
  tests, 1275 subtests**, 141.67s; this includes 174 new cases. Broad Ruff,
  compileall, pip check and diff checks pass. All test sessions completed.
  Evidence and boundaries: R79-read-only-lifecycle-contract-20260911.md and
  /tmp/xiuxian-r79-read-only-final-full-20260911.xml.
- Production and R67 channel-retreat baselines remain unchanged/unverified.
  Native Tianti continuation/chronology and concubine optional-field/native
  lifecycle work remain open, along with R65/R74, R07 and Final Review.

### R80 Native Tianti Lifecycle Review (2026-09-11)

- Initial **67 failing** cases established recovery-await invalidation,
  replacement-owner receipt writes, transport retries, concurrent dispatch and
  lost unknown spending. Four bounded per-kind records now persist ownership
  before dispatch; all native sends have a queue operation check and no
  transport retries. Terminal results close only their exact pending record.
- The detached post-status climb and passive text reducer are removed. Native,
  passive and explicit UI paths use the same account/chat/root/server-time
  contract. Original Wenxin/Gangfeng policy is preserved; rank choice and
  switches are included in admission, not reread to overwrite a completed send.
- Real-format log replay exposed one more failure after the first full runs:
  a newer manual command hid an older owned receipt in the latest-text lookup.
  Recovery now searches by operation ID and rejects ambiguous roots.
- First and intermediate full suites passed **8569**, **8578** and **8580**
  tests with **1275 subtests**. Seven further failing cases exposed displayed
  CD drift from the actual server-anchored timer, now repaired. Final focused
  native/UI/background/read tests pass **596**; final full suite after the
  display repair passes **8587 tests, 1275 subtests**, 144.66s, including
  **284 new R80 cases**. Broad Ruff, compileall, pip check and diff checks
  pass; all sessions completed. Final JUnit:
  /tmp/xiuxian-r80-tianti-closeout-full-20260911.xml.
  Details: R80-tianti-native-lifecycle-contract-20260911.md.
- This does not approve deployment or certify production subordinate-role
  cultivation. Post-terminal corrections, unowned legacy reconciliation,
  concubine lifecycle, R65/R74, R07 and whole-project Final Review remain open.

### R81 Concubine Status Contract Review (2026-09-11)

- Initial 125 cases produced **118 failures**, covering missing versus
  explicit fields, false freshness, mutation-phase destruction, stale anchors
  and gift-status persistence before its awaited followup.
- Positive panels now require core cooldowns and kind-appropriate affinity.
  Missing optional fields are preserved for the same partner; ambiguous or
  invalid declared fields reject the panel. Passive/native/MiniApp status
  share admission checks without allowing read observations to clear mutation
  work. Native/passive entry uses server evidence time and exact panel chat.
- HTTP status no longer lends its new clock to an old Telegram message.
  Rejected status text cannot fall through to a fragment reward. Future,
  invalid or incomplete cached/logged panels cannot certify freshness.
- First full suite passed **8739 tests, 1275 subtests**, 144.49s. Twelve
  additional failing boundary cases from final review have been repaired.
  Final focused suite passes **745 tests, 54 subtests**, including **173 new
  R81 cases**. Final isolated full suite passes **8760 tests, 1275 subtests**,
  144.20s, after those boundary repairs. Configured Ruff and E9/F63/F7/F82,
  compileall, pip check and diff checks pass. All sessions completed.
  Final artifact: /tmp/xiuxian-r81-concubine-final-full-20260911.xml.
- Contract and remaining gates:
  R81-concubine-status-contract-20260911.md. No production work or rollout.
  Native concubine dispatch, early/late receipt ownership, unknown spending,
  restart recovery, broader chronology and whole-project Final Review remain
  open.

### R82 Native Concubine Queries

- Persist status/gift-status intent before transport; retain unknown reads,
  validate queued module plans and owner/account, and preserve early completion
  against delayed receipts. Runtime tracking is enabled with zero retries.
- Native and passive query completion share exact root/chat/operation cleanup.
  Recovery uses owned receipts, official bot/server-clock evidence and latest
  log revisions. Only these reads may expire for a scheduled requery.
- Save before gift continuation, retain the daily opportunity after failed
  reads/saves, and preserve newer business plans, sibling pending records and
  explicit new manual queries. Older roots cannot re-enter as manual reads.
- Added 184 focused cases. The first full run exposed one outdated fast-due
  fixture (8942 passed); the updated contract still asserts its queue timeout.
  Final focused suite: 853 passed, 73 subtests, 6.21s. Second isolated full run:
  8944 passed, 1275 subtests, 146.38s. Configured Ruff, E9/F63/F7/F82,
  compileall, pip check and diff checks pass. All test sessions completed.
  Final artifact:
  /tmp/xiuxian-r82-concubine-query-verified-full-20260911.xml.
- Contract and remaining gates: R82-concubine-query-lifecycle-20260911.md.
  Resource-consuming concubine chains, R07 durability and production per-role
  cultivation remain unverified. No production operation or rollout.

### R83 Gift Inventory And Spending

- Added bounded owned gift_bag/gift records. Persist before transport, recheck
  queued plans and owners, retain unknown mutations through cancellation and
  restart, and require the exact preceding inventory read before spending.
- Native/passive replies share strict scope/server-clock validation and exact
  operation cleanup. Passive inventory/text shortcuts cannot swallow an owned
  gift result or a retry after failed local completion saving.
- Removed legacy gift send/result implementations and unused failure helpers.
  Legacy scalar pending is held for explicit migration, never erased/retried.
  Ready, confirmed and failed attempts have distinct daily consequences; a
  reset scalar marker cannot override the retained completed fact.
- Saved completion and unchanged-baseline numeric effects are idempotent;
  changed snapshots are not double-adjusted. Broader accounting is not closed.
- 139 new cases. Focused: 953 passed, 24 subtests, 7.72s. First full run:
  9075 passed, 1275 subtests, five unrelated-event callback regressions; removed
  the generic gift callbacks. Second isolated full validation: 9083 passed,
  1275 subtests, 147.11s. Ruff, compileall, pip check and diff checks pass;
  all sessions completed, with no subsequent runtime/test changes.
  Artifact: /tmp/xiuxian-r83-gift-verified-full-20260911.xml.
  Contract: R83-concubine-gift-lifecycle-20260911.md.
- No production operations. Greet and remaining concubine mutations,
  migration, R07 durability and whole-project acceptance remain open.

### R84 Daily Greeting Lifecycle

- Reuse the R83 engine in concubine_affinity_actions.py for owned greeting
  intent, queue/owner checks, tracked zero-retry transport, exact reply
  completion, save rollback and bounded log recovery. Existing gift schema
  and source tags are unchanged; add a separate bounded greet action column.
- Remove legacy greeting send/reply/retry code. Generic summaries cannot
  authorize a retry or fabricate success. Unknown work survives cancellation,
  timeout and restart; only fresh known-unsent evidence allows another send.
- Native/passive replies share ownership, official sender and server-clock
  checks. Daily facts survive scalar resets; gains never increment a newer
  baseline. Failed saves retain pending work. Explicit no-partner results now
  require status calibration instead of another greeting; voyage waits use
  the reply clock.
- Initial 24 failing cases, a reproduced no-partner loop and five additional
  final-review faults are repaired. Added 120 cases. Focused: 931 passed,
  73 subtests, 7.29s. Isolated full: 9203 passed, 1275 subtests, 147.82s.
  Ruff, compileall, pip check and diff checks pass. All test sessions completed;
  only documentation changed after full validation at this checkpoint.
  Artifact: /tmp/xiuxian-r84-greet-full-20260911.xml.
  Contract: R84-concubine-greet-lifecycle-20260911.md.
- No production operations, skill edits, commit or push. Legacy pending,
  broader accounting, remaining concubine mutations, R07 and Final Review
  remain open; subordinate-role production cultivation remains unverified.

### R85 Fragment Panel Contract

- Require a matching partner and complete, consistent Xutian/Cangkun panels;
  missing fields never borrow cached completion. Partial/conflicting panels
  retain pending work and quantities. Timeouts clear confirmation, not pieces.
- Puzzle admission requires current confirmation. Native/passive panels use
  the same reducer; unrelated roots/phases and incomplete replies cannot
  claim completion or alter dream CD. The recorded fragment-scroll fixture
  and a complete edit after a partial panel are covered.
- Initial 22 failures plus three passive-routing and three handled-result
  failures are repaired. Added 63 cases. Focused: 994 passed, 73 subtests,
  7.34s. Full isolated: 9266 passed, 1275 subtests, 148.80s.
  Artifact: /tmp/xiuxian-r85-fragment-full-20260911.xml.
  Contract: R85-concubine-fragment-contract-20260911.md.
- Ruff, compileall, pip check and diff checks pass; all test sessions completed.
  This does not repair legacy scalar dispatch, unchecked completion saves,
  dream/puzzle restart recovery or manual chronology. No production operations,
  skill edits, commit or push; remaining whole-project gates stay open.

### R86 Fragment Query Lifecycle

- Extend the existing bounded read-query slot with fragment ownership and
  confirmation, without a new SQL column. Persist before tracked zero-retry
  dispatch; revalidate queued work and retain unknown reads across restart.
  Exact operation receipts and official server-timed log revisions drive
  recovery; read expiry is separate from mutation recovery.
- Native/passive fragment replies share checked completion, exact cleanup and
  save rollback. Confirmation requires the completed read, unchanged account
  and partner, exact explicit kinds and a server clock within 600 seconds.
  Stale/unowned flags cannot authorize puzzle spending. No-partner results
  calibrate availability without deleting quantities; summary/voyage replies
  do not fabricate dream CD.
- Remove the legacy fragment sender, weak replay/passive descriptors and
  startup/timeout reset branches. Unowned legacy pending is explicitly held.
- Initial 20 lifecycle, four freshness and eight provenance failures are
  repaired. The first full run exposed five missing-sender dispatcher
  regressions; the caller was fixed without weakening those existing tests.
- Added 137 cases. Final focused: 1389 passed, 24 subtests, 10.37s. Full:
  9403 passed, 1275 subtests, 148.75s. Ruff, compileall, pip check and diff
  checks pass. All sessions completed; only docs changed after verification.
  Artifact: /tmp/xiuxian-r86-fragment-confirmation-full-20260911.xml.
  Contract: R86-concubine-fragment-lifecycle-20260911.md.
- At this checkpoint status/gift-status completion saves still needed
  repair; the scoped follow-up is R87 below. Dream/puzzle and remaining
  mutation lifecycles, migration/capacity, R07 and Final Review stay open.
  No production, skill, commit or push operations were performed.

### R87 Query Completion And Replay Saves

- Status/gift-status completion now shares checked whole-projection save and
  rollback with fragment reads. Failed saves restore query, pending, phase,
  partner and schedule and cannot start a gift. Saved late completion does
  not overwrite replacement business; cleanup remains exact-operation only.
- Owned passive status replies now reach the query handler before text
  dedupe. Exact account/chat/root receipts resolve absent identity hints;
  ambiguous owners are rejected and receipt-late replies remain replayable.
  Manual and unowned legacy behavior is separate, not given invented op IDs.
- A shared pure classifier keeps recovery and result handlers consistent.
  Checkpoint saves roll back on failure. Complete trusted evidence cannot
  fall through to read expiry when its local completion fails; incomplete
  replies still permit bounded read expiry. Mutations gain no retry rights.
- Reproduced 57 initial save/routing failures, four receipt-late dedupe
  failures and 15 recovery-save failures. Added 106 cases and strengthened
  the original failed-save assertion. SQLite tests cover real normalized
  receipts, reload and a periodic save after failed completion.
- Final focused: 1254 passed, 75 subtests, 10.18s. Full: 9509 passed,
  1275 subtests, 150.06s. Ruff, compileall, pip check and diff checks pass.
  All test sessions completed; only docs changed after final verification.
  Artifact: /tmp/xiuxian-r87-query-completion-full-20260911.xml.
  Contract: R87-concubine-query-completion-20260911.md.
- Legacy scalar queries still need ownership/migration and completion review;
  dream/puzzle, other mutations, R65/R74, R07 and Final Review remain open.
  Production jfdffdddd subordinate-role cultivation remains unverified.
  No production, skill, commit, push or deployment operations were performed.

### R88 Owned Dream And Puzzle Mutations

- Persist bounded per-identity dream/puzzle intent before tracked zero-retry
  dispatch. Bind partner, fragments, plan and account/chat; puzzle must carry
  a fresh owned fragment-query confirmation. Queue checks reject changed
  controls, replacement work and expired confirmation. Unknown/cancelled
  effects survive restart without automatic replay; only fresh unsent evidence
  permits local retry. Unknown partners are queried, not probed by spending.
- Native/passive completion precedes generic dedupe and uses exact receipts
  and official server clocks. Late results cannot rewind replacement plans.
  Failed completion/recovery saves roll back; late registration of completed
  work gets exact cleanup without clearing unrelated pending entries.
- Typed dream results and explicit CD waits drive game facts. Puzzle clears
  only a confirmed fragment kind and never moves dream CD. Missing-piece text
  does not invent counts; voyage rejection cannot borrow a cached return time.
  Resource backoff is separate local retry state. Removed superseded mutation
  bodies, weak passive projections, scalar log specs and unused inference.
- Universal ban/pardon safety routing is preserved outside action completion;
  ban-only evidence does not erase the retained mutation or its real cooldown.
  Legacy scalar-only dream/puzzle state remains visibly held for reconciliation.
- Added 216 cases, including native transport/log replay, SQLite reload,
  failed saves, identity/plan races and the full dream -> query -> puzzle chain.
  Initial reproduction: 27 failures; review follow-up: eight failures/six
  passes. Final focused: 1375 passed, 49 subtests, 9.37s. Final full: 9725
  passed, 1275 subtests, 150.93s. Ruff, compileall, pip check and diff checks
  pass. All sessions completed; only docs changed after full verification.
  Artifact: /tmp/xiuxian-r88-fragment-full-final-20260911.xml.
  Contract: R88-concubine-fragment-actions-20260911.md.
- Next review: sibling greeting/query voyage-time inference, then divination,
  heart, voyage/reacquisition and the remaining module matrix. Untimed/summary
  results, legacy migration, R65/R74, R07 and whole-project Final Review remain
  open. Production jfdffdddd subordinate-role cultivation is still unverified.
  No production, skill, commit, push or deployment operations were performed.

### R89 Voyage Status And Read-Only Continuation

- Add voyage_status to the existing owned status-query record, using the
  actual concubine_voyage runtime family. Persist before tracked zero-retry
  dispatch, recheck queued business admission, route before native/passive
  dedupe and clean up only the exact completed operation/receipt.
- Remove unowned passive voyage projection and cached return-time inference.
  Explicit untimed refusals close only the refused owned operation without
  daily/CD consumption, then retain sailing/unknown time for read-only status.
  No-settleable-task is needs_status, not idle; only explicit no-active-task
  evidence clears the hold. Unknown-time probes never issue settlement and
  preserve hourly deadlines across restart. Stale reads cannot unlock actions.
- Query and affinity recovery checkpoint/expiry saves roll back on failure;
  valid evidence awaiting persistence cannot expire into another spending
  chain. Late receipt registration cannot leave a completed read pending.
  This supersedes R88's untimed-refusal hold limitation only for explicit owned
  refusals with this read-only continuation, not general incomplete outcomes.
- Added 132 voyage-query and 19 affinity-recovery cases. Reproductions: 26
  voyage failures/one pass, nine affinity failures/one pass, then 10 final-review
  failures for queued pending admission and restart deadline sliding.
  Final focused: 1713 passed, 49 subtests, 11.12s. Final full: 9876 passed,
  1275 subtests, 152.45s. Configured/fatal Ruff, compileall, pip check and
  diff checks pass. All sessions completed; only docs changed after this
  checkpoint. Artifact: /tmp/xiuxian-r89-full-final-20260911.xml.
  Contract: R89-concubine-voyage-query-20260911.md.
- Next review: legacy voyage start/return mutation intent, receipts and retries,
  then remaining divination/heart/reacquisition and module matrix. R65/R74,
  R07, migration/rollout and Final Review remain open. No production, skill,
  commit, push or deployment operations were performed. Production jfdffdddd
  subordinate-role cultivation remains unverified.

### R90 Voyage Launch And Settlement

- Add bounded concubine_voyage_actions JSON for launch/return. Persist scoped
  intent before tracked zero-retry dispatch; recheck queued controls and
  sibling business work. Unknown effects survive restart without blind retry.
  Native/passive owned results precede generic dedupe; recovery uses exact
  receipts and official server clocks. Saves roll back and cleanup stays exact.
- Strict complete result parsing and typed completed JSON reject foreign,
  ambiguous, malformed or incomplete evidence. Explicit affinity losses/gains
  apply once against unchanged snapshots. Missing affinity/partner data asks
  for calibration; thresholds and voyage-off settlement cleanup are preserved.
  Zero-anchor phase cleanup requires the original plan. Rewards remain reports,
  not a new cultivation/inventory accounting source.
- Remove old voyage sends/retries/scalar parsing and the outer dream/return
  summary replay bypass. Other phaseful replay behavior is unchanged.
- Added 238 voyage and two dream-replay cases. Reproduced 27 initial, six
  parser, four shared replay and seven final boundary failures. Focused: 835
  passed, 6.02s. Final isolated full: 10116 passed, 1275 subtests, 153.36s.
  Artifact: /tmp/xiuxian-r90-full-final-20260911.xml.
  Contract: R90-concubine-voyage-actions-20260911.md.
- Configured/fatal Ruff, compileall, pip check and diff checks pass. All
  sessions completed. No production, skill, commit, push or deployment action.
  Similar sibling phase cleanup is next; divination/heart/reacquisition,
  legacy/accounting, R07 and whole-project Final Review remain open.
  Production jfdffdddd subordinate-role cultivation remains unverified.

### R91 Common Concubine Phase Cleanup

- Reproduced 26 failures for zero/boolean-anchor cleanup in status queries,
  affinity actions and fragment mutations. Sixteen controls already passed;
  the final harness corrected three gift fixture timestamps before code edits.
- Reuse one phase-release helper across query, affinity, fragment and voyage.
  Preserve replacements unless the positive integer anchor matches, or the
  zero integer anchor retains the pre-transition plan. Gift amount clearing
  follows the same ownership decision. No schema/transport/CD policy change.
- Added 53 native-route/runtime-receipt cases, including confirmed positive
  anchors, exact old cleanup, duplicate replay, unsent dispatch and read expiry.
  Focused: 1593 passed, 11.53s. Final full: 10169 passed, 1275 subtests, 154.29s.
  Artifact: /tmp/xiuxian-r91-full-final-20260911.xml.
  Contract: R91-concubine-phase-ownership-20260911.md.
- Configured/fatal Ruff, compileall, pip check and diff checks pass; all sessions
  completed. Production and HEAD remain unchanged. Continue divination, heart,
  reacquisition and legacy/lifecycle/accounting/rollout gates. This does not
  validate production subordinate-role MiniApp cultivation or close Final Review.

### R92 Concubine Divination Lifecycle

- Replace untracked divination with a bounded account/chat/operation-bound slot
  persisted before zero-retry dispatch. Unknown/cancelled work never expires
  into another spend; only fresh definitely-unsent evidence permits retry.
- Native/passive results share strict original-receipt, sender and server-clock
  validation. Complete success/CD facts, typed refusals and exact pending cleanup
  preserve newer plans and survive save failure/reload. Cost is evidence only,
  not a new cultivation debit. Queued sibling actions recheck divination holds.
- Remove unowned log projection, provisional cooldown and old timeout branches.
  Final review reproduced seven additional malformed/foreign-state failures;
  their repairs and 188 controls pass. Add 142 cases overall (125 lifecycle,
  17 phase/queued-sibling), preserving R91 cleanup and scheduler continuation.
- Focused: 2069 passed, 24 subtests, 15.86s. Full: 10311 passed, 1275 subtests,
  156.69s. Artifact: /tmp/xiuxian-r92-full-final-20260912.xml.
  Contract: R92-concubine-divination-lifecycle-20260912.md.
- Configured/fatal Ruff, compileall, pip check and diff checks pass. Production,
  HEAD and inherited quiz/UI-helper changes remain untouched; sessions completed.
  Continue heart/reacquisition and legacy/accounting/durability/rollout review.
  Production subordinate-role MiniApp cultivation and Final Review remain open.

### R93 Heart-Trial Reply Contract

- Reproduced 25 incomplete/conflicting/unknown-text failures with five complete
  controls; final review adds CRLF compatibility and three anchor-helper bypass
  failures. Preserve active state on rejected text instead of closing the chain.
- One bounded pure parser requires complete round/choice or settlement fields,
  unique bounded numeric facts and explicit cooldowns. The active handler uses
  those facts; unowned passive settlement writing is removed. Anchor recovery
  and named voyage refusals have matching text/partner checks. Signed affinity
  updates validate current/result bounds; cultivation/demon values are not new
  accounting writers. Untimed in-progress text does not invent a business CD.
- Added 87 cases. Focused: 2156 passed, 24 subtests, 17.31s. Final full: 10398
  passed, 1275 subtests, 156.85s, exit code 0.
  Artifact: /tmp/xiuxian-r93-full-final-20260912.xml.
  Contract: R93-concubine-heart-reply-contract-20260912.md.
- Static checks/compile/dependencies/diff pass; test sessions completed. No
  production, service, live request, skill, commit/push or deployment changes.
  Next complete heart launch/choices/receipt ownership, authoritative clocks,
  unknown-send/restart handling and atomic duplicate-safe effects. Legacy
  timeout/anchor/resource cleanup is not fixed by parsing alone. R67 production
  channel cultivation and whole-project Final Review are still unvalidated.

### R94 Heart-Trial Operation Lifecycle

- Persist one bounded session before tracked zero-retry launch/choices; bind
  identity, account, chat, operation and original prompt. Keep unknown spending
  unresolved, require fresh definitely-unsent evidence for retry, and remove
  scalar result authority, arbitrary panel anchors and guessed timeout CD.
- Sequential same-message edits require owned prompt/sender/server clocks.
  Native/passive handling precedes generic dedupe. Results save atomically,
  duplicate affinity effects are excluded and newer/replacement state survives.
  Owned status probes are read-only, bounded to three, and never infer rewards.
- Review repaired malformed-state exceptions, passive ID coercion, inconsistent
  completion records, late-round/probe interleaving and outer-failure plan
  corruption. Ordinary scheduling resumes paused rounds before old scalar
  timeout; post-commit guard save failure retries only exact cleanup.
- 235 lifecycle cases replace obsolete scalar/CD expectations, while the R93
  parser suite stays. Focused: 2349 passed, 24 subtests, 19.36s.
  Artifact: /tmp/xiuxian-r94-focused-final-20260912.xml.
  First full run found two obsolete startup/public-entry assertions; they now
  assert preserved unresolved state and no I/O. Outer integration: 401 passed,
  5 subtests, 4.56s. Final full: 10591 passed, 1275 subtests, 163.91s.
  Artifact: /tmp/xiuxian-r94-full-final-20260912.xml. All sessions completed;
  configured/fatal Ruff, compileall, pip check and diff checks pass.
  Contract: R94-concubine-heart-lifecycle-20260912.md.
- Reacquisition, manual/legacy reconciliation, R65/R74, shared R07 durability
  and whole-project Final Review remain open. No production, service, gameplay,
  listener, skill, commit, push or deployment changes. R67's subordinate-role
  MiniApp cultivation is not certified or resumed by these offline tests.

### R95 Spouse Reacquisition Lifecycle

- Persist one bounded operation before tracked zero-retry dispatch. Require
  fresh absence evidence, preserve unknown delivery through cancellation/restart,
  recheck owner/controls/siblings in the queue, and keep existing UI controls.
- Typed owned outcomes commit atomically with their projection. Romance
  acknowledgements stay pending; final prompt edits require later server clocks.
  Acquired partners wait for status calibration, and newer/permanent partners
  survive late replies. Scalar/unowned replay and guessed timeout CD are removed.
- Final review reproduced and fixed same-clock edit acceptance in three routes
  and redirection-budget resets across status refresh/definitely-unsent retries.
  One alternate command is allowed before the existing 12-hour policy backoff.
  Early-result cleanup preserves replacement phases and sibling work. Committed
  completion survives notification failure; guard failures retry cleanup only.
- 173 acquisition and 29 additional shared-phase/queued-peer cases. Focused
  final: 3516 passed, 347 subtests, 26.55s.
  Artifact: /tmp/xiuxian-r95-focused-final-20260912.xml.
  Configured/fatal Ruff, compileall, pip check and diff checks pass. Final isolated
  full: 10793 passed, 1275 subtests, 162.08s, exit code 0; all sessions completed.
  Artifact: /tmp/xiuxian-r95-full-first-20260912.xml.
  Contract: R95-concubine-reacquire-lifecycle-20260912.md.
- Legacy/manual reconciliation, R65/R74 accounting/capacity, R07 forced-stop
  durability and whole-project Final Review remain open. No production, service,
  gameplay, listener, skill, commit, push or deployment changes. The R67
  production subordinate-role cultivation complaint is still unvalidated.

### R96 External Partner Observations

- Replace direct contract/gain/depletion/loss projection with one bounded,
  persisted concubine_external_observation per identity. Strict bot/chat/server
  clocks, unique username/alias binding and explicit unmentioned-parent ownership
  precede the save. Malformed or conflicting evidence cannot authorize work.
- Preserve business snapshots, pending operations, phases and timers when an
  observation arrives. Coalesce its timestamp barrier without accelerating the
  60-second calibration interval. Pause permits facts but no new business send.
- Queued mutations recheck pending/invalid observations; definitely-unsent phases
  still release their original ownership. Owned heart choices finish, not restart.
  The outer scan reaches calibration independently of old business deadlines.
- An owned native read dispatched after the event, or a new identity-validated
  MiniApp read, resolves the marker atomically with its absolute panel. Earlier
  reads, missing absolute affinity and contradictory permanent-partner absence
  cannot resolve it. Pending puzzle-ready state can reach a safe status read;
  replacement partners use their new panel cooldowns rather than the old ones.
- Broadcasts no longer preclaim unsaved events or continue iterating identities
  after notifying their unique owner. Remove old direct-delta/acquisition helpers
  and unused loss/contract regexes, retaining actual shared parser dependencies.
- 153 new cases: 122 observation, 26 shared-action, 2 heart and 3 MiniApp worker.
  Final focused: 3694 passed, 183 subtests, 26.57s.
  /tmp/xiuxian-r96-focused-final-20260912.xml.
  Configured/fatal Ruff, compileall, pip check and diff checks pass. Final isolated
  full: 10946 passed, 1275 subtests, 164.28s, exit code 0; all sessions completed.
  Artifact: /tmp/xiuxian-r96-full-first-20260912.xml.
  Contract: R96-concubine-external-observation-20260912.md.
- Missing-field live panels, historical/manual reconciliation, forced-stop
  durability and deployment acceptance remain open. No production, service,
  gameplay, listener, skill, commit, push or deployment changes. R67 production
  subordinate-role cultivation remains unvalidated.

### R97 Wanxin Owner Reply Semantics

- The native route was already owned; the remaining bug was semantic.
  Heading-only owner/status replies, incomplete numeric fields, mixed
  success/refusal text and failed visit/protection wording could still complete
  that owned operation. Partial/type-changing edits could then erase its
  already-accounted affinity delta.
- Add explicit bounded number/complete-panel parsing, separate deltas from
  absolute totals, validate moon partner/resonance/affinity, require supported
  positive bodies and bind refusal/cooldown wording to its own action.
  Unknown outcomes keep their operation and do not mutate business clocks.
- Preserve completed evidence through partial or terminal-type-changing edits.
  Existing same-type correction, native provenance, scoped pending cleanup and
  checked save rollback remain in place; financial assistance still uses R65.
- Added 112 owner-contract/native/replay/SQLite cases. Initial reproduction
  failed 75 cases; context review failed 11 and heading/expense review failed
  six. Real fixtures caught an initial delta-versus-total regression, which
  was fixed without changing those fixtures.
- Focused verification: 3579 passed, 62 subtests in 61.99 seconds.
  /tmp/xiuxian-r97-focused-20260912.xml.
  Final isolated full regression: 11058 passed, 1275 subtests in 160.29 seconds.
  /tmp/xiuxian-r97-full-first-20260912.xml. JUnit reports 12333 cases including
  subtests, no failures/errors/skips. Configured/fatal and scoped unused-symbol
  Ruff, compileall, pip check and whitespace checks pass. All sessions finished.
  Contract: R97-wanxin-owner-reply-contract-20260912.md.
- Moon-join success lacks a real fixture, so its heading-only shortcut is not
  retained. Commission/identification semantics, general resource chronology,
  legacy/manual reconciliation, durability and Final Review remain open.
  No production, service, gameplay, listener, skill, commit, push or deployment
  changes. R67 subordinate-role production cultivation remains unvalidated.

### R98 Wanxin Commission Reply Semantics

- Publication requires a positive bounded ID; acceptance requires one complete
  helper/owner acknowledgement. Identification requires targeted positive
  wording and explicit bounded source/contribution gains, distinguishing zero
  from absence and checking the original helper's identity aliases.
- Replace loose commission branches with contextual result classification.
  Contradictory/processing/non-execution replies remain unknown; real
  cancellation and no-cancellable-task replies still complete. Financial
  banner/strip compatibility and R65 accounting remain separate and unchanged.
- Native unknown/final-edit, helper rename, no-repeat rollover, latest-invalid
  local-log evidence, SQL failure rollback and SQLite reload are covered.
  Added 79 cases; initial reproduction failed 56, expanded refusal review five.
  Original real-message fixtures remain unchanged.
- Focused: 1147 passed, 62 subtests in 43.18 seconds.
  /tmp/xiuxian-r98-focused-20260912.xml.
  Full isolated regression: 11137 passed, 1275 subtests in 162.95 seconds.
  /tmp/xiuxian-r98-full-first-20260912.xml. JUnit reports 12412 cases including
  subtests, no failures/errors/skips. Configured/fatal/scoped-unused Ruff,
  compileall, pip check and whitespace checks pass; all sessions finished.
  Contract: R98-wanxin-commission-reply-contract-20260912.md.
- Remaining: R74 restored sending, legacy/manual evidence and post-consumption
  corrections, R65 retention, R07 durability and whole-project Final Review.
  Moon-join success still lacks an authoritative fixture. Production, services,
  live data, listeners, skills, commits, pushes and deployment remain untouched;
  R67 subordinate-role production cultivation is still unvalidated.

### R99 Wanxin Restored Sending And Financial Binding

- Distinguish an exact active process-local sender from a saved sending intent.
  After its deadline, a fully bound orphan moves to the existing unresolved
  action slot without deleting intent, resource reservations or shared roots.
  Related mutations remain blocked; independent configured actions may proceed.
- Reject owner commission settlement from unbound provider operations. Adopt
  an existing exact shared receipt before native result cleanup, with the
  original validator and checked save. Conflicting metadata/save failure keeps
  the evidence; an old bound completion cannot touch newer unbound work.
- Added 39 cases including two spawned process kill/SQLite reload tests,
  live-caller exclusion, disabled cleanup, original owner/account isolation,
  marker cleanup, failed saves, real detached receipts and exactly-once replay.
  Existing early financial fixtures now assert a hold before receipt binding
  and completion after native replay; their game wording remains unchanged.
- Final focused: 1186 passed, 62 subtests, 47.57 seconds.
  /tmp/xiuxian-r99-focused-final-20260912.xml.
  Full isolated regression: 11176 passed, 1275 subtests, 169.49 seconds.
  /tmp/xiuxian-r99-full-first-20260912.xml. JUnit reports 12451 cases including
  subtests with no failures/errors/skips. Configured/fatal/scoped-unused Ruff,
  compileall, pip check and whitespace checks pass. All sessions finished.
  Contract: R99-wanxin-restored-sending-contract-20260912.md.
- No retry/recovery controller, schema field, runtime flag or CommandAttempt
  authority was added. R07 shared durability, unowned legacy/manual evidence,
  post-consumption corrections, cross-writer chronology and production/Final
  Review remain open. Production, services, live data, listeners, skills,
  commits, pushes and deployment are untouched.

### R100 Covered Wanxin Affinity Writes

- Reproduced four cases where older moon reads or greeting/seal effects rewrote
  an already newer concubine affinity snapshot. A later edit of an old read
  must not acquire a new read timestamp.
- Suppress only the covered affinity write. Keep the original operation's
  completion, parsed delta/dedupe, authoritative CD and exact pending cleanup.
  Newer supported results still project, and the same transaction handles
  rollback and SQLite reload.
- Nine new cases cover the four reproductions, valid newer results, rollback,
  replay and unrelated pending retention. A new test's raw-dictionary reload
  assertion was corrected to respect the existing SQLite pending codec; no
  runtime codec or real wording fixture changed.
- Final focused regression: 3101 passed, 13 subtests, 33.28 seconds.
  /tmp/xiuxian-r100-focused-final-20260912.xml.
  Full isolated regression: 11185 passed, 1275 subtests, 167.46 seconds.
  /tmp/xiuxian-r100-full-first-20260912.xml. JUnit reports 12460 cases including
  subtests, with no failures/errors/skips. Configured/fatal/scoped-unused Ruff,
  compileall, pip check and whitespace checks pass. All test sessions finished.
  Contract: R100-wanxin-covered-affinity-contract-20260912.md.
- General cross-writer chronology, overlapping/same-clock snapshots and post-
  consumption corrections remain open. R101 separately follows up the
  reverse-order concubine reads and retained-evidence gaps.
  No ledger/schema/flag or new recovery controller was introduced. Production
  and whole-project Final Review remain unvalidated.

### R101 Concubine Snapshots After Wanxin Results

- First verified 14 native/MiniApp cross-writer cases against existing query
  fingerprints; those ordinary single-change cases already passed. Additional
  native/passive legacy/manual cases reproduced 209/176/314 becoming an older
  184. Reload and a native same-value round trip brought that set to nine
  failing cases. Two more MiniApp value-round-trip cases failed at the session
  and command-result boundaries because the request clock was also used as
  the processing clock.
- Reuse only the bounded retained Wanxin affinity reply points to reject a
  strictly older concubine snapshot. An original moon read does not gain a
  new read clock from its edit; gain/cost revisions retain the qualified result
  clock. Preserve current identity ownership, native completion/exact cleanup
  and fresh read availability. No ledger/schema/flags or retry control added.
- The public MiniApp caller supplies a separate processing clock at its
  existing admission checks, while snapshots and cooldowns retain request
  time. Other identities and unrelated Wanxin replies do not cancel reads.
  Two final boundary reproductions align the negative check with native
  Wanxin's existing one-second clock allowance; malformed/far-future points
  still do not supply ordering proof.
- Added 48 combined cases, plus the unchanged R100 nine cases: 57 pass in
  1.21 seconds. /tmp/xiuxian-r101-chronology-final-v4-20260912.xml.
  Final focused regression: 3421 passed, 62 subtests, 34.72 seconds.
  /tmp/xiuxian-r101-focused-final-v2-20260912.xml. First full regression before
  the clock boundary passed 11231 tests and 1275 subtests in 168.25 seconds;
  /tmp/xiuxian-r101-full-first-20260912.xml. Final isolated regression: 11233
  passed, 1275 subtests, 169.36 seconds.
  /tmp/xiuxian-r101-full-final-v2-20260912.xml. JUnit reports 12508 total cases
  with no failures/errors/skips. Configured/fatal Ruff, scoped-unused,
  compileall, pip check and whitespace checks pass. All test sessions finished.
  Contract: R101-concubine-wanxin-snapshot-contract-20260912.md.
- Same-clock/overlapping reads, missing/replaced legacy receipts, remaining
  resource writers and post-consumption corrections are not certified. R65
  capacity/migration, R07 shared durability, per-game acceptance and Final
  Review remain open. Production subordinate-role cultivation is unvalidated;
  production, services, live data, listeners, skills, commits and pushes remain
  untouched.

### R102 Observed Native Concubine Queries

- Reproduced unowned native/passive source violations, ignored save failures,
  consumed replay and old-query edit regressions. Reuse the existing bounded
  status-query JSON for terminal observed reads, retaining their original
  actor/account/chat/root and request clock without fabricating a script send.
  Invalid/duplicate/older observations cannot replace active or newer work.
- Native and passive status paths now share the checked snapshot/completion
  transaction before generic dedupe. False/exception/SQLite failure restores
  the identity; an exact legacy read alone may close. Disabled controls and
  financial daily markers remain unchanged. Remove the legacy passive writer
  and duplicate late routing; do not continue gifts from a manual panel.
- Real-producer tests found that channel actors lack sender_is_bot and that
  command edit/forward metadata disappeared before log replay. Preserve known
  observational flags without inventing absent provenance, accept the exact
  registered channel actor without a user-only bot field, and carry forwarding
  through reply resolution and the actual passive wrapper. A later invalid
  reply must not expose an earlier valid-looking panel from the same read.
- A further reproducer found rejected stale/blocked reads were confused with
  failed saves, leaving legacy recovery in a permanent hold. The scoped read
  transaction now distinguishes ignored, save_failed and complete outcomes;
  only a failed local commit holds terminal evidence for replay. No new
  sending/recovery controller, schema, ledger, control flag or network probe is added.
- Added 171 cases, including direct/native/passive source validation, channel
  actors, partial edits, disabled controls, strict pending cleanup, saved-record
  corruption/reload, notification failures, real log production and scheduler
  continuation. The two first-full-suite diagnostic regressions are resolved
  without weakening their channel-route/missing-identity assertions.
- Final source suite: 264 passed, 49 subtests, 1.72 seconds;
  /tmp/xiuxian-r102-final-source-fixed-20260913.xml. Final associated suite:
  3938 passed, 62 subtests, 30.98 seconds;
  /tmp/xiuxian-r102-focused-final-v2-20260913.xml. Full isolated regression:
  11404 passed, 1275 subtests, 168.26 seconds;
  /tmp/xiuxian-r102-full-final-20260913.xml. JUnit has 12679 total cases and
  zero failures/errors/skips. All test sessions ended. Configured Ruff,
  scoped F841, compileall, pip check and whitespace checks pass. An optional
  F401 scan still reports existing module-facade exports and six import/test-
  shim cleanup items; do not apply a blanket import remover to those facades.
  Contract: R102-concubine-observed-query-contract-20260912.md.
- Cross-writer overlap/same-clock reconciliation, unowned legacy mutations,
  capacity/migration and remaining per-game acceptance are still open. No
  production/service/config/data/listener/skill/commit/push action occurred.
  R67 production subordinate-role cultivation and whole-project Final Review
  remain unvalidated. Candidate HEAD is still cbf152cd61325ca9218c74ee995d643038b20905.

### R103 Same-Second Native Affinity Ordering

- A corrected 30-case reproducer found 15 failures: covered greeting 209 became
  218, covered seal 176 became 152, an old moon panel replaced 400 with 314,
  and reverse delivery admitted older native/manual panels. Verify each
  original command point instead of using a reply's later delivery/edit clock.
- Current native coverage needs the exact completed query, current account,
  snapshot clock and panel chat/message. A covered operation still completes,
  advances its cooldown and clears only its own pending root; it does not
  count affinity twice. Incoming verified native panels consult the bounded
  Wanxin receipt points before projection. Legacy/MiniApp reads retain their
  existing scalar checks without invented Telegram provenance.
- A further six lifecycle failures showed an edited +9 to +0 round trip makes
  the old plan fingerprint match again. The valid original Wanxin command
  still proves the old native read predates that operation. Use this negative
  bound without assuming cross-chat or edited-message creation order.
- Source/rollback/reload review passes all 81 new cases in 1.26 seconds;
  /tmp/xiuxian-r103-source-boundary-final-20260913.xml. Associated suite:
  3740 passed, 197 subtests, 29.21 seconds;
  /tmp/xiuxian-r103-focused-final-20260913.xml. Full network-isolated suite:
  11485 passed, 1275 subtests, 170.69 seconds;
  /tmp/xiuxian-r103-full-final-20260913.xml. JUnit records 12760 cases with
  zero failures/errors/skips. Configured Ruff, scoped F841, compileall, pip
  check and whitespace checks pass. All test sessions finished.
  Contract: R103-concubine-wanxin-source-order-20260913.md.
- No schema/record-format/ledger/flag/retry authority changes. This does not
  certify overlapping reads, missing legacy receipts or general post-consumption
  accounting. R65 capacity/migration, shared R07 durability, remaining games,
  production acceptance and Final Review stay open. Production and R67's
  subordinate-role cultivation remain unchanged/unvalidated; no service,
  live request, data/config, listener, skill, commit or push action occurred.

### R104 Owned Affinity Reconciliation

- A corrected 20-case reproducer found 14 failures. Concurrent Wanxin updates
  left an owned confirmed greeting unprojected, but the scheduler still sent
  an inventory query for another gift using 279 instead of the real 309, or
  246 instead of 276. Earlier native/passive status reads could also overwrite
  an applied 300 balance with 270. A green terminal flag was not a usable balance.
- Derive calibration need from existing validated greeting/gift completions
  and absolute-read coverage. Partner-dependent actions and Wanxin moon-seal
  admission cannot spend the uncalibrated scalar; existing owned status reads
  remain available. Preserve daily/terminal facts, one-time inventory debits,
  current schedules and disabled controls. No new state or retry loop added.
- The original query/request clock guards native/MiniApp status projection.
  Equal seconds cannot prove inclusion when the retained completion lacks
  edit provenance. A sibling three-case reproducer led to the same protection
  for old moon panels; a validated later absolute moon read can reconcile the
  held balance without relabeling its old gain as newly applied.
- Final 65 source/lifecycle cases pass in 1.41 seconds;
  /tmp/xiuxian-r104-source-final-20260913.xml. Associated suite: 4091 passed,
  202 subtests, 32.74 seconds; /tmp/xiuxian-r104-focused-final-20260913.xml.
  Full network-isolated suite: 11550 passed, 1275 subtests, 170.56 seconds;
  /tmp/xiuxian-r104-full-final-20260913.xml. JUnit has 12825 cases and zero
  failures/errors/skips. Configured Ruff, scoped F841, compileall, pip check
  and whitespace checks pass; all sessions finished.
  Contract: R104-owned-affinity-reconciliation-20260913.md.
- This is bounded current-receipt reconciliation, not an affinity history or
  certification of other mutation engines. Missing/replaced legacy evidence,
  post-consumption corrections, R65 capacity/migration, shared R07 durability,
  remaining per-game acceptance and Final Review remain open. No production,
  service, live request, data/config, listener, skill, commit or push changes.
  R67 subordinate-role production cultivation remains unvalidated.

### R105 Fishing Worker Lifecycle

- The 22-case reproducer confirmed ignored request bounds, thread abandonment,
  erased gains after a later wait exception and dropped HTTP classification.
  Thirteen calls ran despite a two-call adapter policy. Authorization and
  gameplay waits had no operation guard.
- Fishing now uses the existing draining worker, guard and one request budget
  through authorization, lobby/shop, bite/proof/result waits and all rounds.
  Confirmed counts/gains survive later cancellation, failure and catch-decoder
  errors. Unready results and finish acknowledgements are not settlements.
  Per-round HTTP events preserve Retry-After and error types. Removed only the
  unused unselected-cave shortcut; scoring/global limits remain unchanged.
- All 51 new cases and associated suite 413 tests/36 subtests pass. Full
  network-isolated suite: 11601 passed, 1275 subtests, 175.20 seconds;
  /tmp/xiuxian-r105-full-final-20260913.xml. Configured Ruff/scoped F841 pass.
  Contract: R105-fishing-worker-lifecycle-20260913.md.
- Worker results alone do not certify the two outer callers or persistence.
  R106 starts their lock/ownership/notification review. Unknown-effect and
  restart durability, R65 capacity/migration, general accounting and whole
  Final Review remain open. No production/state/service/skill/push change;
  R67 subordinate-role production cultivation is still unvalidated.

### R106 Fishing Caller Lifecycle

- Initial caller reproducer: 50 failed, 3 passed. Report follow-ups reproduced
  replacement-owner writes, duplicate concurrent reports and failed-delivery
  markers. Three later cases exposed implicit counting of unready work.
- Public and message-entry fishing now share the existing per-identity lock,
  retain exact identity/account/plan ownership and guard every later await.
  The public switch remains independent of legacy fishing. Same-owner drained
  results are adopted before cancellation propagates; changed controls allow
  facts without overwriting replacement schedules. Removed/rebound owners
  cannot receive old work. Notification failure cannot erase a settlement.
- Confirmed counts and catches survive partial no-rod and later HTTP failures;
  partial failures retain Retry-After. No-result cancellation, daily-limit and
  finish/unready acknowledgements do not fabricate completed rounds. Daily
  reports serialize and acknowledge only retained, unchanged owner/fact sets.
- All 95 new cases pass, including real worker cancellation integration and
  four temporary-SQLite reload cases. Associated suite before the final four
  cases: 678 passed, 39 subtests. Final network-isolated regression: 11696
  passed, 1275 subtests, 170.62 seconds;
  /tmp/xiuxian-r106-full-final-20260913.xml. JUnit has 12971 cases and zero
  failures/errors/skips. Configured Ruff, scoped compilation, pip check and
  whitespace checks pass; all sessions finished.
  Contract: R106-fishing-caller-lifecycle-20260913.md.
- Next reproduce unchecked save failures, separately saved inventory and
  historical result replay before claiming durable fishing accounting.
  Unknown-effect/restart recovery, direct-entry day clocks, parser authority,
  R65 capacity/migration, R07 and whole-project Final Review remain open.
  No production/service/live/data/config/listener/skill/commit/push change;
  R67 subordinate-role production cultivation is still unvalidated.

### R107 Fishing Result Commit

- Initial 13 behavioral failures reproduced unchecked saves and two separate
  commits for one result. Follow-ups exposed cross-day read invalidation,
  missing-bait follow-up saves, malformed pending data and a daily-report ack
  that ignored newly pending results.
- Prepared fishing results, counts, inventory, permitted scheduling and
  reminders now use one checked transaction. Failed writes/projections restore
  the prior memory and retain one bounded, account-owned pending projection.
  Both callers and the fishing scheduler retry only local persistence before
  any more gameplay or downstream transfers/reports. Changed controls preserve
  facts only; changed owners/facts/inventory require reconciliation. Invalid
  JSON cannot silently reopen gameplay. No credentials/raw HTTP are retained.
- Public missing-bait scheduling moved into the transaction. Daily/status
  reads preserve pending source-day state; pending changes invalidate report
  acknowledgements. UI exposes the hold and public background admission sees
  local pending work independently of the cached daily limit, without changing
  its control/backoff rules or game request frequency.
- All 56 new cases pass, including a real SQLite transaction abort, pending
  save/reload, once-only local recovery and component exceptions. Associated
  suite: 547 passed, 22 subtests, 6.03 seconds;
  /tmp/xiuxian-r107-focused-final-20260913.xml. Final isolated full suite:
  11752 passed, 1275 subtests, 173.40 seconds;
  /tmp/xiuxian-r107-full-final-20260913.xml. Configured Ruff, scoped F841 and
  compilation, pip check and whitespace checks pass; all sessions finished.
  Contract: R107-fishing-result-commit-20260913.md.
- Next inspect input authority: explicit-empty catch lists, unconfirmed reward
  payloads and conflicting readiness markers. Historical replay, forced-stop
  unknown effects, source clocks, legacy reconciliation, R65/R07 and Final
  Review remain open. No production/service/live/config/listener/skill/commit/
  push action occurred. R67 production subordinate-role cultivation is unvalidated.

### R108 Fishing Result Authority

- Initial 30 failures reproduced explicit-empty fallback, unconfirmed gains,
  coerced/disagreeing counts and conflicting readiness. Follow-up review
  reproduced eight additional failures in summary/notice/capture authority
  and explicit null readiness. Both callers now use the same strict result
  count/catch contract, as do projection, notices and capture. No unconfirmed
  material is granted; positive partial outcomes and independent identical
  catches are preserved. Invalid readiness/wrappers cannot start another round.
- Shared capture failure handling was already correct. Actual caller tests
  confirm its sink failure preserves committed results without a shared patch.
- All 50 new cases pass. Associated suite: 523 passed, 22 subtests, 21.81s;
  /tmp/xiuxian-r108-focused-final-20260913.xml. Full isolated suite:
  11802 passed, 1275 subtests, 173.50s;
  /tmp/xiuxian-r108-full-final-20260913.xml. Configured Ruff, scoped F841,
  compilation, pip check and whitespace checks pass; all sessions finished.
  Contract: R108-fishing-result-authority-20260913.md.
- Next inspect per-round loose rewards, recursive gain/last-result fallbacks
  and material projection authority. Historical replay, unknown spending,
  source/day/quota clocks, startup reconciliation, R65/R07 and Final Review
  remain open. No production/service/live/config/listener/skill/commit/push
  action occurred; R67 production subordinate-role cultivation is unvalidated.

### R109 Fishing Chain Materials

- Corrected baseline: 37 failed, 6 passed. Confirmed reward-only rounds were
  lost except for the last diagnostic payload; mixed chains dropped them all.
  Recursive gain/reward readers imported history and counted repeated scalar
  aliases twice, while zero/invalid item quantities became one.
- Worker and runtime now share bounded current-field decoders. Explicit
  current families override fallback; diagnostic last_*/round/shop/session/
  proof data cannot authorize income. Per-confirmed-round standalone rewards
  survive mixed chains and later failures; catch-attached rewards are not
  repeated. Independent identical drops still accumulate.
- Follow-up review reproduced nine more failures in nested/empty/text rewards,
  malformed item names and ambiguous single-round catches. These are repaired;
  confirmed round counts survive ambiguous optional catch data, but no fish
  is guessed and no next round begins on that result.
- All 73 new cases pass, including actual public/message callers and a real
  temporary SQLite abort/reload/once-only local recovery of a mixed chain.
  Associated suite: 707 passed, 36 subtests, 26.35s;
  /tmp/xiuxian-r109-focused-final-20260913.xml. Isolated full suite:
  11875 passed, 1275 subtests, 173.20s;
  /tmp/xiuxian-r109-full-final-20260913.xml. Configured Ruff, scoped F841,
  compilation, pip check and whitespace checks pass; all sessions finished.
  Contract: R109-fishing-chain-materials-20260913.md.
- Next review durable round identifiers, unknown operations and startup
  reconciliation, source-day/quota authority and legacy material history.
  R07/R65, other per-game gates and whole-project Final Review remain open.
  No production/service/live/config/schema/listener/skill/commit/push action
  occurred. R67 production subordinate-role cultivation is still unvalidated.

### R110 Fishing HTTP Outcomes

- Initial reproducer: 28 failures. Fishing stopped after a lost finish reply
  or transient result-read failure, and callers could not distinguish an
  unsent request from an uncertain round after partial completion.
- One chain request context now retains actual transport-entry and unresolved
  round evidence. Dispatched uncertain finish may make bounded result reads
  with the same token, never another finish/start/next/proof. Authentication,
  access challenges, 429 and long Retry-After stop inline recovery; the existing
  request and consecutive-failure budgets still apply.
- Explicit rejection restores any prior active-round uncertainty. Drained
  cancellation keeps settled facts and later-round uncertainty separately.
  A next acknowledgment retains its new token fingerprint; a lost response
  cannot invent one. Public summaries do not gain raw credentials.
- All 46 new cases pass, including both callers using a real fake transport
  to recover and account a lost finish response once. Associated suite:
  753 passed, 36 subtests, 26.47s;
  /tmp/xiuxian-r110-focused-final-20260914.xml. Isolated full suite:
  11921 passed, 1275 subtests, 172.41s;
  /tmp/xiuxian-r110-full-final-20260914.xml. Configured Ruff, scoped F841,
  compilation, pip check and whitespace checks pass. All sessions ended.
  Contract: R110-fishing-http-outcomes-20260914.md.
- Caller/startup review confirms the MiniApp phase is still cleared as legacy
  text state and public fishing lacks durable unknown-operation ownership.
  Design recovery and binding before adding holds; do not infer completion
  from a retry timer. Historical identifiers, source/quota authority, R07/R65,
  production R67 validation and whole-project Final Review remain open.
  No production, live, service, configuration, listener, skill, commit or push
  action occurred.

### R111 Fishing Quota Field Authority

- Initial reproducer: 59 failed, 12 passed. The recursive reader treated
  last/session/result diagnostics and generic counts as current daily quota,
  ignored conflicting aliases/arithmetic, and changed the limit after failure.
  A reported count could regress already-confirmed work. Both actual callers
  also lost valid quota fields outside the nested result.
- Worker/runtime now share one bounded current-field parser. Explicit current
  empty/invalid fields mask fallback; aliases must agree, quantities must be
  integral JSON numbers within the existing quota bound, and used/remaining
  arithmetic must match. No history/shop/session/proof/round scan remains.
- Confirmed rounds promote a canonical daily object. A newer confirmed round
  without valid quota clears that calibration, while a failed round cannot
  replace it with diagnostics. Raw copied aliases are removed at normalization
  after final review reproduced two competing-alias failures. Runtime rejects
  unconfirmed or regressive quota but keeps independently confirmed catches.
- All 90 new cases pass, including actual public/message callers, cancellation
  draining and real temporary SQLite rollback/reload/once-only local recovery.
  Associated suite: 846 passed, 36 subtests, 26.72s;
  /tmp/xiuxian-r111-focused-final-20260914.xml. Isolated full suite:
  12011 passed, 1275 subtests, 172.90s;
  /tmp/xiuxian-r111-full-final-20260914.xml. Configured Ruff, runtime/new-test
  F841, compilation, pip check and whitespace checks pass. Broader optional
  F841 still reports the pre-existing unused payload in the stargazer test at
  tests/test_webapp_core.py:1800, unchanged from commit 4672b0b5d. All sessions
  ended. Contract: R111-fishing-quota-authority-20260914.md.
- Exact-round recovery must precede startup holds: an unresolved next may
  refer to the previous settled token, so neither a new entry nor its old
  ready result establishes completion. Inferred quota/cache policy, historical
  receipt identity, source-day clocks, R65/R07, other per-game contracts and
  Final Review remain open. No production/live/service/configuration/schema/
  listener/skill/commit/push action occurred. R67 production subordinate-role
  cultivation is still unvalidated.

### R112 Fishing Operation Recovery

- The result-only worker rejects wrong/new tokens, already-confirmed keys,
  corrupt provenance and unknown next-round identities before authorization or
  HTTP. Normal and recovery workers share polling/budget/cancellation rules.
- Synchronous checkpoints precede every mutation and retain sanitized receipt
  prefixes. Non-True acknowledgements stop further dispatch. Decoder errors keep
  a projection-error receipt instead of erasing completion or granting gains.
- Worker-stage full isolated regression: 12081 passed, 1275 subtests, 176.27s;
  /tmp/xiuxian-r112-worker-full-20260914.xml. All 70 new worker/checkpoint cases
  pass. Configured Ruff, scoped F841, compilation and diff checks pass.
- The durable stage adds one bounded account/identity-owned operation, checked
  main-loop persistence before start/finish/next, confirmed receipt prefixes and
  absolute Retry-After. Both callers supply the writer. Startup/controls/report
  reads preserve pending state; scheduler recovery takes the same fishing lock.
- Local result-pending v2 binds operation UUID/revision/digest and commits receipt
  keys together with counts, inventory and reminders. SQL-trigger rollback and
  six SIGKILL boundaries followed by two independent reloads verify once-only
  projection without replaying transport. New/wrong tokens and unknown next are
  held before authorization. Recovery cannot adopt replacement owner/work.
- Review fixes include post-settlement read backoff, stale recovery completion,
  strict intent/receipt binding, closed callback lifetime, corrupt record bounds,
  confirmed-prefix reminders and aligned codec/writer capacity. All 147 new
  R112 cases pass. Final associated suite: 964 passed, 22 subtests, 16.29s;
  /tmp/xiuxian-r112-verified-related-20260914.xml. Final network-isolated full
  suite: 12158 passed, 1275 subtests, 186.28s;
  /tmp/xiuxian-r112-verified-full-20260914.xml. Configured Ruff, scoped fishing
  F841, compilation, pip check and diff checks pass. Broader optional F841 has
  inherited cave/UI/stargazer-test warnings, not introduced or removed here.
  All test sessions ended; HEAD remains cbf152cd61325ca9218c74ee995d643038b20905.
- Contract: R112-fishing-operation-recovery-20260914.md. Missing original-round
  evidence, projection-error receipts, historical/cross-owner reconciliation,
  source-day clocks and inferred quotas remain explicit limits. This is not a
  deployed gate or production proof; R67 production cultivation stays unvalidated.

### R113 Yinluo Send Finalization (2026-09-14)

- Followed R65's capacity/lifecycle review. The 120-business-point bound is a
  bound on field/slot/beneficiary keys, not a counter of operations. Actual
  writers replace clocks for repeated same-key work, so no speculative clock
  eviction was added. Identity/slot churn and disk capacity remain open.
- Four initial failures reproduced real pre-RPC cancellation becoming unknown
  in both Yinluo and financial Wanxin, plus false rejection of a completed
  return receipt. Further review reproduced mismatched unsent ownership,
  retired completion lookup, mutable original send time and redundant saves.
- The caller snapshots prior block evidence and only a new, current, exact
  identity/command unsent descriptor may release the reservation. Existing
  native/transport evidence wins over an absent return object. Unknown sends
  remain reserved without mutation retries; a sent receipt is not completion.
- Exact completed cold operations can be located by the return receipt without
  restoring history. UUID/account/chat/message/send-time checks, archive digest
  and result indices remain mandatory. Repeated facts preserve completion and
  financial state. Real SQLite release failure retains the original reservation
  after reload; replacement/rebind/delete cannot receive the old caller's write.
- 48 new lifecycle-file cases pass plus one additional detached-receipt case
  in the existing suite. Final network-isolated full suite: 12207 passed,
  1275 subtests, 190.48s; `/tmp/xiuxian-r113-full-20260914.xml`.
  Focused artifact: `/tmp/xiuxian-r113-focused-20260914.xml`. Configured Ruff,
  scoped F841/F401, compilation, pip check and whitespace checks pass.
  Final associated suite: 893 passed, 262 subtests, 54.96s;
  `/tmp/xiuxian-r113-related-final-20260914.xml`. All test sessions completed;
  HEAD remains `cbf152cd61325ca9218c74ee995d643038b20905`.
- Contract: `R113-yinluo-send-finalization-20260914.md`. No shared transport
  edit, new field/schema, retry controller, live action or deployment. Production
  state/services/switches, skills, listeners and remotes remain untouched.
  R65 legacy/forced-stop/capacity, R07 and whole-project Final Review stay open.
  R67 subordinate-role production cultivation is still unvalidated.

### R114 Yinluo Read Lifecycle (2026-09-14)

- Seven initial reproductions identified detached read pending that survives
  expiry/reload and blocks shared admission, wrong expiry clocks, lost late
  receipts and scheduler reentry that invalidates active work. Candidate review
  then reproduced incomplete cleanup retirement, contradictory/ambiguous pending
  metadata, repeated receipt reopening and malformed/foreign RPC ownership.
- Read expiry and exact pending removal now share the accounting transaction.
  Failed/exceptional saves roll both back; preparation cannot drop an expired
  record while its cleanup is unresolved. Another chat's same-numbered pending
  row survives. Consuming operations retain their original reservations.
- Late original transport may bind an expired unbound query. Known send clocks
  determine the wait; an identical expired bound receipt is idempotent. Active
  identity/account-bound callers and registered exact RPC handles prevent
  expiry, receipt adoption or scheduler reentry until finalization completes.
- 62 new cases; final focused suite 256 passed, 48.41s;
  `/tmp/xiuxian-r114-focused-final-20260914.xml`. Configured and scoped Ruff,
  compilation, dependency consistency and whitespace checks pass.
  Final associated suite: 955 passed, 262 subtests, 58.98s;
  `/tmp/xiuxian-r114-related-20260914.xml`. Final network-isolated full suite:
  12269 passed, 1275 subtests, 196.62s;
  `/tmp/xiuxian-r114-full-20260914.xml`. No failures, errors or skips; all test
  sessions completed. HEAD remains `cbf152cd61325ca9218c74ee995d643038b20905`.
- Contract: `R114-yinluo-read-lifecycle-20260914.md`. No persisted schema or
  shared send-layer change, live request, production/config/service/listener
  action, skill edit, deployment, commit or push occurred. R65 migration,
  terminal cleanup crash review, storage/capacity, R07 and whole-project Final
  Review remain open. R67 production subordinate-role cultivation is unvalidated.

### R115 Yinluo Completion Cleanup (2026-09-14)

- Thirteen initial failures reproduced native completion persisting before its
  shared pending cleanup, including ordinary/detached sends, reload, absent
  replay logs, cold retirement, manual reentry and consuming timeouts. Candidate
  review then reproduced partly cleaned duplicate rows and seven scheduler/guard
  failures. A missing guard clock required one further strict-input correction.
- Resource facts, completion and exact pending deletion now share the normal
  transaction. Expiry and completion reuse one operation receipt validator.
  Staged cold retirement is included, with SQLite rejection/exception rollback,
  native manual intervals and duplicate/foreign/unknown preservation covered.
- Existing completed pending rows recover from local hot/cold evidence even
  without the old log. Enabled scheduling performs cleanup before business due
  times, without new actions. Guard matching uses its exact current native
  command scope rather than only hot families; real remote cooldowns survive.
  Guard persistence still uses the existing helper and dirty-state lifecycle;
  this is not a new shared guard or transport transaction.
- Final new lifecycle file: 55 passed, 6.19s;
  `/tmp/xiuxian-r115-focused-final-20260914.xml`. Configured/scoped Ruff,
  compilation, dependency consistency and whitespace checks pass.
  Final associated suite: 1102 passed, 262 subtests, 66.42s;
  `/tmp/xiuxian-r115-related-final-20260914.xml`. Final network-isolated full
  suite: 12324 passed, 1275 subtests, 204.54s;
  `/tmp/xiuxian-r115-full-20260914.xml`. No failures, errors or skips. All test
  sessions completed; HEAD remains `cbf152cd61325ca9218c74ee995d643038b20905`.
- Contract: `R115-yinluo-completion-cleanup-20260914.md`. No new persisted field
  or schema, shared sender/guard edit, live request, production/config/service
  action, listener/skill change, deployment, commit or push. R65 legacy/no-ID,
  full forced-stop/storage/capacity, R07 and whole-project Final Review remain
  open. R67 production subordinate-role cultivation is still unvalidated.

### R116 Yinluo Held Read Lifecycle (2026-09-14)

- Initial isolated reproduction: 28 failed, eight preservation controls passed.
  Resource holds blocked exact read pending/guard cleanup in hot and cold
  recovery; book gaps also blocked read completion. Scheduler early returns
  skipped expiry, while sticky gaps could cause ineffective automatic queries.
- Owned stored native read completion now remains separate from financial
  authority. Its exact pending cleanup uses the resource transaction; unknown
  consuming work, resource holds/gaps, archive history and unavailable balances
  remain protected. An unstored capacity-overflow reply cannot claim success.
- Scheduler-local reconciliation/expiry precedes hold/due-time returns, without
  bypassing active caller/RPC exclusion. Sticky barriers stop automatic new
  work, and unchanged hold waits no longer slide forward on every tick.
- Real shared-admission tests reproduced five further failures because manual
  dispatch retained an old completed read guard until after sending. Existing
  exact completion cleanup now runs before preparation/admission; genuine
  remote cooldowns and shared send/guard implementation remain unchanged.
- Focused: 78 passed, 8.84s; `/tmp/xiuxian-r116-focused-20260914.xml`.
  Associated: 1627 passed, 311 subtests, 75.93s;
  `/tmp/xiuxian-r116-related-20260914.xml`. Configured/scoped Ruff, compilation,
  dependency consistency and whitespace checks pass. Full isolated regression:
  12402 passed, 1275 subtests, 212.91s;
  `/tmp/xiuxian-r116-full-20260914.xml`. No failures, errors or skips; all test
  sessions completed. These counts are not production or whole-project
  acceptance.
- Contract: `R116-yinluo-held-read-lifecycle-20260914.md`. No new persisted field,
  schema, production/config/service/listener/skill action, live request,
  deployment, commit or push. R65 legacy/no-ID, full forced-stop/storage and
  operational capacity, R07 and whole-project Final Review remain open. R67
  production subordinate-role cultivation is still unvalidated.

### R117 Yinluo Legacy Evidence Preservation (2026-09-14)

- Initial legacy reproduction: 48 failed, three controls passed, including
  prospective marker checks rather than 48 distinct defects. Normalization
  discarded transport metadata or malformed/falsy pending, aliased original
  nested data and could crash on scalar slots. Bootstrap holds then vanished
  through config/scheduler normalization; existing books ignored untracked
  legacy fields and a new panel replaced the unowned old pending summary.
- Candidate normalization preserves the three legacy pending payloads exactly
  via deep copies. A shared predicate holds both new/existing books, without
  replacing stronger holds. The existing observation JSON gains the explicit
  `legacy_pending_invalid` boolean for invalid roots/payloads. False-like
  malformed values cannot clear it; fresh empty observations remain valid.
- Follow-up tests reproduced 19 MiniApp truthiness failures and eight
  unavailable-module resets. The bridge now uses the shared predicate before
  session/HTTP work. Unavailability still disables the module but no longer
  deletes old observation evidence. Unowned held summaries are not advanced
  by newer panels; strictly owned native resource facts may still be recorded.
- SQLite review reproduced 22 corrupt-root failures plus a passing empty-state
  control. A scoped observation codec now retains quarantine instead of using
  generic empty-default fallback. Established-roster loads do not rewrite the
  raw cell; existing first-load roster writeback and later saves retain the
  marker. The shared loader lifecycle and other modules' codecs are unchanged.
- Final focused suite: 253 passed, 59 subtests, 13.60s;
  `/tmp/xiuxian-r117-focused-final-20260914.xml`. Configured/scoped Ruff,
  compilation, dependency consistency and whitespace checks pass. Final
  associated suite: 1880 passed, 370 subtests, 88.69s;
  `/tmp/xiuxian-r117-related-final-20260914.xml`. Full network-isolated suite:
  12524 passed, 1275 subtests, 217.85s;
  `/tmp/xiuxian-r117-full-20260914.xml`. The checkpoint adds 122 cases. Final
  reports have no failures, errors or skips; all test sessions completed.
  HEAD remains `cbf152cd61325ca9218c74ee995d643038b20905` and inherited
  user-owned quiz/UI-key-tool changes remain intact.
- Contract and rollback restrictions: `R117-yinluo-legacy-evidence-20260914.md`.
  No automatic outcome migration, hold clearance, consuming retry or new
  shared controller is added. Production, switches, services, listeners,
  skills and remotes remain untouched; no deployment, commit or push.
  R65 legacy/no-ID, forced-stop/storage/capacity, R07 and whole-project Final
  Review remain open. R67 production subordinate-role cultivation is unvalidated.

### R118 Yinluo Anchored Legacy Migration (2026-09-14)

- Historical command code retains slot/send-time evidence separately from
  the exact shared pending chat/message/command/send interval and the old
  `yinluo[-auto]-action-time` tag. The candidate now joins those sources to a
  validated native terminal receipt, rather than leaving a proved completion
  held indefinitely. Modern UUID operations do not substitute for old work.
- Native reply processing, bounded local-log recovery, retained hot/cold
  recovery, due scheduling and manual admission reach the same path. It
  removes old slot/transport pending only when each match is unique and all
  other legacy causes are resolved. Unowned summaries, missing commands,
  corrupted payloads, unrelated Wanxin work and stronger barriers remain held.
  A native-owned pending summary supports terminal edits/separate replies.
- Follow-up boundary tests reproduced 21 failures from the empty-book
  cultivation shortcut and conflicting/mistyped slot aliases. Commit review
  reproduced ignored optional transport aliases and integer/boolean/float
  state/key changes that ordinary equality hid. Strict candidate selection
  and type-sensitive staged snapshots repair these cases. One test's guessed
  `collect_enabled` key was corrected to the existing `auto_config.collect`.
- Facts, pending removal, observation and hold transition share the resource
  transaction. Stale sources/configuration/owners or changed archive proof
  reject commit; SQLite/false/exception failures restore the candidate state.
  Old optimistic sha/soul snapshots are never restored over intervening income.
  Local recovery does not repeat financial deltas or send a game request.
- Focused migration suite: 128 passed, 9.17s;
  `/tmp/xiuxian-r118-focused-final-20260914.xml`. Associated suite:
  2058 passed, 372 subtests, 100.82s;
  `/tmp/xiuxian-r118-related-final-20260914.xml`. Configured/scoped Ruff,
  compilation, dependency consistency and whitespace checks pass. Full
  network-isolated regression: 12652 passed, 1275 subtests, 230.45s;
  `/tmp/xiuxian-r118-full-20260914.xml`. Final reports have no failures, errors
  or skips and all test sessions completed. HEAD remains `cbf152cd`; inherited
  user-owned quiz/UI-key-tool changes were not edited by this checkpoint.
- Contract: `R118-yinluo-legacy-migration-20260914.md`. No new persisted field,
  shared controller, production/state/service/listener/skill action, live
  request, deployment, commit or push. World Boss/refinement controls remain
  untouched. General no-ID migration, forced-stop/storage, capacity/beneficiary
  churn, R07 and whole-project Final Review remain open. R67 subordinate-role
  production cultivation remains unvalidated.

### R119 Yinluo Capacity And Expired Read Retention (2026-09-14)

- Completed native sacrifices can fill the reserved mutation/reply threshold
  before their observations become stale. The scheduler was repeatedly held
  instead of obtaining useful banner coverage. Six reproductions failed with
  an independent native-coverage control passing. The corrected fixture uses
  the real next game-day deadline, not a 24-hour stale-observation boundary.
- Capacity inspection shares actual compaction ownership/coverage rules and
  remains read-only. Existing configured automatic work may use a reserved
  native banner read when sha/named-soul coverage can free enough capacity.
  Missing cultivation, unknown outcomes/souls, hard holds and full read slots
  cannot justify it. Incomplete native panels suppress unchanged repeats only
  while their retained hot/cold outcome is still valid. Query non-send backoff
  applies even when daily work remains due; manual mutations are not replaced.
- Repeated unknown reads reproduced a sticky receipt gap. A provisional
  empty-read deletion was replaced after four cases demonstrated missing cold
  ownership, including useful panels later withdrawn. Strict v2 cold payloads
  retain only exact expired native banner operations with nonfinancial
  unknown/conflicting receipts. Terminal financial v1 evidence is unchanged.
  Expiry, cold insertion, hot removal and exact shared pending cleanup are
  atomic. Late/contextless edits preserve the original owner; no-context text
  cannot grant balances. Baselines/conflicts and business clocks are retained.
- A separate full-slot reproducer caught querying without room for its failed
  reply. Both capacity selection and owned preparation now preserve that
  boundary. An independent valid native panel can still reopen capacity through
  the normal staged projection; missing-data gaps are never silently cleared.
- Final focused suite: 82 passed, 20.71s;
  `/tmp/xiuxian-r119-focused-final-20260914.xml`. It includes the real default
  64-operation limit and the complete scheduler/query/retirement/next-sacrifice
  chain, SQLite reload, read-only UI, expired/late receipts, malformed archives,
  active/queued callers, disabled controls and database/false/exception failure
  at expiry, preparation and restoration. Associated suite: 2003 passed,
  323 subtests, 120.58s; `/tmp/xiuxian-r119-related-final-20260914.xml`.
- Final network-isolated full suite: 12734 passed, 1275 subtests, 252.78s;
  `/tmp/xiuxian-r119-full-20260914.xml`. Parsed final reports show no failures,
  errors or skips. Configured/scoped Ruff, compilation, dependency consistency
  and whitespace checks pass; all test sessions completed. HEAD remains
  `cbf152cd`; inherited user quiz/UI-key-tool edits were not changed.
- Contract: `R119-yinluo-capacity-lifecycle-20260914.md`. No production,
  service/listener, database/config, skill, shared transport controller or live
  game request was changed. No deployment, commit or push. World Boss/refinement
  controls, inventory API UI-only use and Tianxing/deep-retreat independence
  remain intact. Business-point/beneficiary churn, full cold-storage and no-ID
  recovery, R07 and whole-project Final Review remain open; R67 production
  subordinate-role cultivation is still unvalidated.

### R120 Yinluo Business-Point Retention (2026-09-14)

- Continuous assistance to distinct beneficiaries reproduced permanent hot-map
  capacity holds, including after old beneficiaries were removed. Earlier
  same-beneficiary cycling did not exercise this lifecycle. Two native
  reproductions failed; the old eight-slot fixture held at the fifth target.
- Only original bound, terminal assistance points may leave the hot map.
  The provider/account/key-scoped cold index retains their exact native
  interval. Fixed clocks and unproved manual points remain hot. A newer owned
  reply may restore a cold key, but an old/duplicate reply cannot regain
  authority. Original commissions, account bindings and resource facts remain
  authoritative through beneficiary churn and SQLite reload.
- Further reproductions found missing-row replay, incomplete move commits and
  source-proof removal between staging and save. A persisted manifest protects
  the cold key/digest set; strict move pairing rejects missing insertion or
  removal. Native proof and cold revisions are checked under the SQLite writer
  lock, and a post-write manifest check precedes the same resource/state commit.
  These are evidence/persistence guards, not new dispatch/retry controllers.
- Final focused suite: 78 passed, 65.61s;
  `/tmp/xiuxian-r120-focused-final-20260914.xml`. The real 120-point limit
  survives 122 distinct beneficiaries, leaving 120 hot and six cold points with
  unchanged correct balances and successful reload. Additional cases cover
  corruption, missing proof/index, two-writer races, false/exception/SQL saves,
  owner changes, native hot/cold sources and disabled controls retaining facts.
- Associated checkpoint: 2048 passed, 72 subtests, 181.88s;
  `/tmp/xiuxian-r120-related-before-slot-20260914.xml`. Two old synthetic capacity
  fixtures now seed their full map before staging, rather than deleting prior
  history inside a transaction. Their capacity and atomic-cleanup assertions
  remain intact. A subsequent two-action shortage reproducer found
  self-retirement of newly staged points. Reclamation now selects only points
  unchanged from the original hot snapshot; terminal evidence is retained even
  when fixed/unproved clocks still require a capacity hold. Four new positive
  and negative cases pass. The earlier full run (12808 cases/1275 subtests,
  `/tmp/xiuxian-r120-full-before-slot-20260914.xml`) is not final acceptance of
  that last change.
- Final network-isolated full run: 12812 passed, 1275 subtests, 326.31s;
  `/tmp/xiuxian-r120-full-20260914.xml`. It includes all associated modules
  after the last guard. Parsed final reports have no failures, errors or skips.
  Configured/scoped Ruff, dependency consistency, compilation and whitespace
  checks pass. All test sessions completed; HEAD remains `cbf152cd` and inherited
  quiz/UI-key-tool edits were not changed.
- Contract: `R120-yinluo-business-retention-20260914.md`. This checkpoint does
  not close cold-storage scaling, manual/unproved-point migration, ambiguous
  existing capacity holds, general no-ID recovery, R07 or Final Review.
  No production, live game request, service/listener, skill, control flag,
  commit/push or deployment changed. R67 per-role production cultivation is
  still unvalidated.

### R122 Trial Receipt And Quota Authority (2026-09-14)

- R121's lifecycle repair did not establish receipt authority. R122 reproduced
  empty/negative finish acceptance, loose quota conversion, unrelated-material
  fallback, duplicate challenge submission and request/identifier overrides.
  The initial receipt run had 21 failures/1 pass; the later metadata/identifier
  boundary run had 20 failures/9 passes.
- A shared current-receipt parser retains supported legacy results and valid
  zero gains, rejects explicit binding/completion conflicts, and projects only
  current recognized rewards. Alias, integer and arithmetic validation prevents
  malformed quotas from authorizing another round. Existing settlements survive
  later bad quota/material fields as partial work.
- Public player selection is checked before authorization. Payload metadata
  cannot replace explicit authentication/selection arguments; proof and receipt
  IDs use one typed normalizer and conflicting aliases stop before finish.
- Final associated regression: 683 passed, 19 subtests, 24.63s, including all
  185 new R122 cases; `/tmp/xiuxian-r122-related-final-20260914.xml`.
  Final isolated full regression: 13089 passed, 1275 subtests, 330.24s;
  `/tmp/xiuxian-r122-full-20260914.xml`. Parsed reports have no failures, errors
  or skips. Scoped Ruff, compileall and whitespace checks pass; all test
  sessions completed, with no change to HEAD or inherited quiz/UI-key edits.
- Contract: `R122-trial-receipt-contract-20260914.md`. Durable unknown-round
  recovery, cross-day/history authority, failed-report retention and production
  acceptance remain open. R67 subordinate-role cultivation remains unvalidated
  in production. No production, live request, service, listener, skill,
  World Boss/refinement switch, commit/push or deployment was changed.

### R123 Trial Batch Registry And Authorization (2026-09-14)

- Reproduced creation-time/count batch collisions, nonmember/finalized writes,
  caller-data aliases, late-send overwrites, stale authorization and UI dispatch
  despite failed admission: 23 failed/1 passed. A later four-case reproduction
  showed that delivered timeout reports deleted missing-result ownership.
- Unique batch IDs and shared member checks protect retained records. Business
  snapshots are copied, transport failure cannot replace a returned outcome,
  and only the matching batch can revoke authorization. UI entry dispatch now
  honors failed authorization without introducing a new retry path.
- Delivered report snapshots suppress duplicate incomplete reports. Missing
  results retain their batch after timeout; entry authorization closes, but
  late owned results can still complete the report with earlier gains intact.
  Retirement requires all expected results and successful unchanged delivery.
- Two R121 fixtures now request explicit batch authorization instead of relying
  on an old link surviving standalone renewal; their ownership assertions are
  unchanged. Final associated regression: 829 passed, 19 subtests, 25.30s,
  including 37 new cases; `/tmp/xiuxian-r123-related-final-20260914.xml`.
  Final isolated full regression: 13126 passed, 1275 subtests, 317.94s;
  `/tmp/xiuxian-r123-full-20260914.xml`. Parsed JUnit includes all 37 new batch
  cases and 185 R122 receipt cases, with zero failures/errors/skips. Configured
  repository-wide/scoped Ruff, compileall, dependency and whitespace checks
  pass. All test sessions completed; branch/HEAD and inherited edits preserved.
- Contract: `R123-trial-batch-lifecycle-20260914.md`. Failed/incomplete reports
  remain memory-only; capacity, persistent delivery, overlapping explicit-run
  attribution and forced-stop/unknown-round recovery remain open. No live
  probe, production/config/DB change, service/listener/skill operation,
  World Boss/refinement change, deployment or commit/push occurred.

### R124 Trial Operation Durability And Local Recovery (2026-09-14)

- Added a per-identity, owner/account-bound latest-operation journal for trial
  requests. Intents are saved after budget/admission and before transport;
  stable hashed round facts and request stages survive successful saves/reload.
  CommandAttempt remains shadow-only and does not own this recovery path.
- Follow-up reproductions found 29 transition/token/owner/save failures, two
  active-worker UI recovery failures, and six lost 408/425 wait hints. Strict
  pending transitions, exact prior-stage rejection/unsent evidence, checked
  owner/record saves, acknowledgement draining and manual lock checks repair
  those paths without a new mutation retry or global hold.
- Known unfinished challenges/entries and unknown requests prevent only that
  identity's new trial entry. Interrupted settled work and pending-save retries
  recover locally. Foreign completed records cannot authorize a new operation;
  invalid/oversized records are explicit holds, not empty journals.
- Seven spawned-process SIGKILL/reload boundaries verify durable stage and
  receipt preservation with zero recovery HTTP. Fault tests also cover actual
  SQLite receipt abort, both native callers' cancellation, normal multiround
  paths, failed/late callbacks, metadata conflicts and replacement owners.
- Final associated regression: 2668 passed, 210 subtests, 62.48s;
  `/tmp/xiuxian-r124-related-verified-20260914.xml`. Final isolated full
  regression: 13256 passed, 1275 subtests, 335.58s;
  `/tmp/xiuxian-r124-full-20260914.xml`. Parsed JUnit reports include all 128 new
  R124 cases, 94 native lifecycle cases (two added cancellation variants), 185
  R122 receipt cases and 37 R123 batch cases, with zero failures/errors/skips.
  Configured Ruff, compileall, dependency and whitespace checks pass. Contract
  and intermediate artifacts are in `R124-trial-operation-recovery-20260914.md`.
  All test sessions completed; HEAD and inherited quiz/UI-key edits are intact.
- Genuine unknown-action reconciliation, lost original entry reconstruction,
  pre-worker external/queued-command entry allocation, cross-day/history and
  durable notifications remain open. Never claim that an unsaved receipt can
  survive a disk failure followed by process death. No production/live request,
  config/DB, service/listener, skill, World Boss/refinement, deployment or
  commit/push action occurred; R67 production cultivation remains unvalidated.

### R125 Treasure Native Lifecycle (2026-09-14)

- Replaced the last raw treasure thread handoff with the existing guarded,
  drained MiniApp worker. A shared per-run budget spans start and every action;
  authentication, budget waits, dispatch and pooled leases honor cancellation.
  Hunt/reveal/settle now retain the selected player ID without changing strategy.
- Public and authorized command callers share identity selection, owner checks,
  result adoption, notification isolation and an account-scoped game lock plus
  the per-identity public-entry lock. The physical-account quota policy and
  explicit channel lab allowlist remain unchanged.
- HTTP success without a real settlement is unknown, not a completed round.
  Final state is updated after each returned action, and earlier settlements
  survive planner/parser/transport failure, budget limits and cancellation.
  Partial business/inventory reporting ignores unconfirmed diagnostic rewards.
- Unknown actions remain held across days and sibling roles, not marked daily
  complete or silently retried. A known active final round remains eligible for
  settlement. 408/425 wait hints are retained locally without shared retry edits.
- Initial 13 reproductions, two later wait-header failures and one pooled-lease
  guard failure are repaired. The 108 treasure cases and one additional
  background case are included in the associated regression: 2722 passed,
  278 subtests, 75.48s;
  `/tmp/xiuxian-r125-related-verified-20260914.xml`. Final isolated full
  regression: 13365 passed, 1275 subtests, 338.92s;
  `/tmp/xiuxian-r125-full-20260914.xml`. Both parsed reports have zero
  failures/errors/skips and contain all 109 new R125 cases. Configured Ruff,
  scoped F checks, compileall, dependency and whitespace checks pass. All test
  sessions completed; branch/HEAD and inherited quiz/UI-key-tool hashes remain
  unchanged.
  Contract, intermediate evidence and test-harness loopback correction:
  `R125-treasure-lifecycle-20260914.md`.
- Strict source/session/quota authority, historical receipt identity, atomic
  accounting and forced-stop recovery remain open. No live game request,
  production file/config/DB mutation, service/listener/skill operation,
  World Boss/refinement change, deployment or commit/push occurred. This does
  not validate R67 subordinate cultivation in production.

### R126 Treasure Receipt And Quota Authority (2026-09-14)

- Added strict current hunt quota and settlement parsing, shared by the worker
  and native material consumers. No recursive panel/reward/session discovery
  may authorize opening or contribute history/diagnostic rewards.
- Selected-player metadata and request sessions must agree. Explicit outer
  noncompletion, unrecognized receipts and mutation-stage daily-limit replies
  cannot count as settlement. Known active partial replies can finish, while
  missing or regressing quota cannot authorize another round. A terminal run
  in a start snapshot is not resettled or booked as a new reward receipt.
- Material quantities/aliases are validated without defaulting to one. Within
  a round, structured evidence and mirrored current logs are reconciled; then
  distinct rounds are added. Native result counts, captures, inventory and
  summaries agree, and invalid material fields remain visible as omissions.
- Original 30 failing cases and nine later main-flag cases are repaired.
  Expanded focus passes 260 cases and 10 subtests, including 145 new receipt
  cases. The first associated run's sole failure exposed a legitimate
  terminal-run replay, now repaired. Final verification on 2026-09-15 passes
  2867 associated cases/278 subtests in 75.12s and 13510 full cases/1275
  subtests in 353.30s. Parsed focused/associated/full reports have no
  failures/errors/skips and contain the exact same 145 new cases. Configured
  Ruff, scoped F checks, compileall, dependency and whitespace checks pass.
  All test sessions completed; branch/HEAD and inherited quiz/UI-key-tool
  hashes remain unchanged. Contract, final artifacts and pre-final checkpoints:
  `R126-treasure-receipt-authority-20260914.md`.
- No production acceptance, historical digest migration, atomic treasure
  accounting or crash-durable unknown resolution is implied. R67 production
  cultivation and whole-project Final Review remain open; live switches,
  service/listener, skill, World Boss/refinement and CommandAttempt are unchanged.

### R127 Treasure Result Commit And Local Recovery (2026-09-15)

- Reproduced six native failures: independent inventory/state writes, false
  success after failed persistence and a second game call instead of local
  recovery. Added bounded owned returned-result projections without changing
  inventory balances, game protocol or CommandAttempt control.
- Prepared inventory and MiniApp rows plus their local completion marker now
  use one literal-True-checked save. Failure/cancellation restores only the
  transaction's effects, preserves unrelated/replacement state and retains a
  pending result. Recovery checks ownership and original row bases; it does
  not obtain a new entry or send commands/HTTP. Unknown outcomes remain held.
- Public/command/UI/background admission and account-shared locks include
  pending results. Paused main-loop recovery is local only, rate limited to
  one save attempt per identity per minute and does not resume gameplay.
  Same-day completion uses the committed receipt even when an unchanged
  summary keeps an earlier date; yesterday's recovery cannot consume today.
- Added 147 result-commit cases. Final focused verification passes 407 cases
  and 10 subtests; expanded associated verification passes 3235 cases and
  307 subtests in 81.05s. The final full regression passes 13657 cases and
  1275 subtests in 352.00s. All three parsed reports contain the exact same
  147 new cases and no failures/errors/skips. Static, compile, dependency and
  whitespace checks pass; all test sessions completed. Contract, artifacts
  and explicit exclusions: `R127-treasure-result-commit-20260915.md`.
- Per-request intent durability, process death before a returned result is
  retained, unknown-action reconciliation and historical identity/digest
  migration remain required work. No production file/config/DB, service,
  listener, skill, World Boss/refinement or commit/push operation occurred.
  R67 production cultivation and whole-project Final Review remain open.

### R128 Treasure Dispatch Durability And Interrupted Recovery (2026-09-15)

- Added bounded owned per-request checkpoints and explicit transport-entry
  evidence. Only literal successful persistence permits mutation dispatch;
  current responses/receipts must be saved before further mutations. The
  worker retains partial facts and its account lock through cancellation.
- New operation-linked v2 result records use R127's atomic projection and
  stable operation inventory keys. Recovery verifies original owner/bases,
  canonical facts, checkpoint identity and observation time, and performs no
  game/auth/entry/Telegram request. Neither new days nor sibling/rebound roles
  can bypass unresolved or mismatched records. Local recovery also runs while
  gameplay is disabled, with the existing 60-second automatic retry spacing.
- The 13 original reproducers failed before repair. Thirteen actual process
  kill/reload boundaries now preserve the exact stage and account materials
  at most once. Save-cancellation fault tests exposed and fixed three missing
  propagation paths. The first associated run passed 3390 cases/307 subtests
  with one old UI identity-registration fixture failure, now corrected.
- Final review found a missed successor path even after the initial green full
  suite: a failed initial read replaced a previous operation's linked result,
  permanently stranding the old journal. Twelve native reproducers failed
  before repair. Failed reads now preserve paired completion/projections, and
  authoritative initial daily-limit results checkpoint before completion.
  Unjournaled game facts remain held. SQLite failure/reload tests confirm a
  later round can still execute without losing or duplicating earlier rewards.
- Final focused verification passes 703 cases/5 subtests in 23.83s; associated
  verification passes 3417 cases/307 subtests in 104.72s; full verification
  passes 13839 cases/1275 subtests in 382.07s. The three parsed final XML reports
  contain identical sets of all 182 new cases and no failures/errors/skips.
  Configured Ruff, scoped full-F, compile, dependency and whitespace checks
  pass; all test sessions ended. Contract, reproduction and final artifacts:
  `R128-treasure-operation-recovery-20260915.md`.
- Known active rounds and unknown requests are held explicitly, not declared
  repaired by waiting or opening a fresh entry. Their authoritative resumption,
  historical receipt/digest migration, cross-day provenance, retention and
  delivery remain required work. No production edit, probe, deployment/restart,
  listener, commit/push, switch or skill operation occurred. R67 production
  cultivation and whole-project Final Review remain open.

### R129 Treasure Active-Run Authority And Progress (2026-09-15)

- Native searches now require explicit current cells, exact indices and
  literal revealed flags. Geometry/AP bounds are validated, hints rank only
  real targets and narrative alone cannot establish a main find. Missing
  cells no longer become random guesses or AP-derived board sizes.
- Reveal responses must prove progress for the selected cell in the same
  session, or an explicit terminal outcome. Unchanged/regressed replies
  retain the exact pending request without another mutation. Same-session
  geometry and earlier revealed cells survive partial replies and checkpoints.
- Review found additional liveness defects: fully exhausted boards need not
  satisfy search AP, and a confirmed partial last-cell response may complete
  the retained board. A revealed target without a next selectable target is
  known progress, not an unknown send; incomplete boards still stay held.
- Reproduction failed 39 of 42 initial cases, then three partial-state cases
  and seven board-completion controls. A final checkpoint test exposed an
  admission gap allowing retained geometry to disappear, now repaired. All
  83 new cases pass. Final focused verification passes 957 cases/19 subtests
  in 58.55s; associated verification passes 3500 cases/307 subtests in
  101.95s. Final full verification passes 13922 cases/1275 subtests in 373.69s.
  All three final XML reports contain identical sets of the 83 new cases and
  zero failures/errors/skips. Configured/scoped Ruff, compile, dependency and
  whitespace checks pass; all test sessions have ended. Contract and artifacts:
  `R129-treasure-run-authority-20260915.md`.
- This repairs a prerequisite for original-round recovery; it adds no
  recovery request, retry, new endpoint or request budget. Pending enters
  without returned session IDs and extending already-accounted checkpoint
  links remain unresolved. No production/config/DB, service, listener, skill
  or commit/push action occurred. R67 production cultivation is unvalidated,
  and whole-project Final Review remains open.

### R130 Treasure Original-Round Recovery (2026-09-15)

- Authorized public/command/UI/background calls can reconcile an owned
  interrupted round through the existing selected-player start read. Exact
  session continuity and reveal progress or a native settlement receipt are
  required; stale/missing/foreign evidence cannot replay an unknown mutation.
  Pending enters without returned session IDs and sibling holds remain held.
- A new version-2 continuation journal retains the prior receipt prefix and
  projects only new receipts under a distinct stable operation key. Previously
  accounted rewards are not recreated even after their inventory delta rows
  are retired. Checked initial saves precede mutations, and later accounting
  keeps its atomic linked state/inventory/result contract. Paused/startup local
  recovery still performs no HTTP. Existing controls and budgets are unchanged.
- The first 12 native reproducers failed. Initial repair exposed six failures
  from the old unknown-state projection guard, now restricted to reject
  unlinked writes while allowing exactly bound continuation accounting.
  All 70 new cases pass, including actual UI/background scheduling, full
  identity loading, two-stage interruption, server waits, owner changes and
  six real SIGKILL/reload boundaries. Final review reproduced four missing
  checkpoint quota checks and two cases where a short Retry-After reduced
  original-round checks from 30 minutes to one minute. Both are repaired:
  malformed quota cannot admit mutations, and identity-loader/worker read
  failures preserve the minimum spacing while honoring longer server waits.
- Final focused verification passes 1027 cases/19 subtests in 69.56s;
  associated verification passes 3570 cases/307 subtests in 113.41s; full
  verification passes 13992 cases/1275 subtests in 391.04s. Parsed final XML
  reports contain identical sets of all 70 new cases with no failures/errors/
  skips. Configured/scoped Ruff, compile, dependency and whitespace checks
  pass; inherited hashes and HEAD are unchanged, and test sessions have ended.
  Contract and artifacts: `R130-treasure-original-round-recovery-20260915.md`.
- No production edit/config/DB, service/listener, deployment, skill or
  commit/push action occurred. Unbound pending-enter correlation, historical
  receipt identity/migration, retention/delivery, R67 production cultivation
  and whole-project Final Review remain open.

### R131 Tree Native Lifecycle (2026-09-15)

- Tree start/single/daily flows now share bounded native request contexts and
  cooperative thread draining. Accepted submissions/materials survive later
  faults; allocated-open and uncertain mutation outcomes are explicit and
  cannot be changed into automatic Retry-After/day/entry retries.
- Command, direct, public and queued scheduler paths retain owner/account,
  record basis and authorization/coordinator generations. Queue reservations
  precede task creation; public execution uses the existing verified-entry UI
  path. State precedes optional capture/summary/notification, and maintenance
  MiniApp availability remains independent of group-send availability.
- Initial reproducers failed 40 worker and 69 caller cases. Later native
  coverage passed 150 cases; final review additionally reproduced/fixed six
  summary/notification/entry-wait failures, the scheduler-only maintenance
  block and four direct-entry bypasses of interrupted running records.
- Final focused verification passes 519 cases/5 subtests in 27.45s; expanded
  associated verification, including treasure suites, passes 4361 cases/295
  subtests in 125.00s; full isolated verification passes 14167 cases/1275
  subtests in 391.36s. Parsed reports have identical sets of all 175 new cases,
  no failures/errors/skips, and all sessions have ended. Configured/scoped
  Ruff, compile, dependencies and whitespace pass; the seven full-F UI
  warnings are identical to HEAD. Inherited hashes and HEAD remain unchanged.
  Contract: `R131-tree-lifecycle-20260915.md`.
- No production/DB/config, deployment, service/listener, commit/push, skill
  or game-probe action occurred. Receipt/quota chronology, crash-safe result
  accounting and original-round recovery remain separate unresolved work;
  holding an unknown round is not finished automation. R67 production
  cultivation and whole-project Final Review remain open.

### R132 Tree Current Quota Authority (2026-09-15)

- Current quota requires exact bounded counts with consistent used/remaining
  arithmetic. Field presence and numeric coercion cannot prove available runs
  or daily exhaustion. Root/single-envelope council state and native submit
  seasonState replace recursive discovery in unrelated result/state panels.
- Continuation checks both modes, stable limits and previously supplied
  season/day context. Regressions cannot rearm a mode. A missing or invalid
  submit snapshot may be repaired by one current read within the original
  budget; otherwise stop while preserving already-returned results/materials.
- Initial native reproducers failed 97 of 111 cases; initial combined worker/
  caller/protocol regression passed 363 cases. Final review reproduced eight
  malformed-season-container failures and repaired that absence/invalidity
  distinction. Ten further failures exposed negative panels whose counts were
  still trusted; explicit non-true flags now invalidate their selected counts.
  Native direct/command/public/scheduled caller cases were added.
- Final focused verification passes 693 cases/5 subtests in 27.97s; associated
  verification passes 4535 cases/295 subtests in 125.88s; full verification
  passes 14341 cases/1275 subtests in 394.18s. Parsed reports contain identical
  sets of 174 added cases, no removed baseline cases and no failures/errors/
  skips. Configured/scoped Ruff, compile, dependency and whitespace checks
  pass; the seven full-F UI warnings remain identical to HEAD. Inherited
  hashes and HEAD are unchanged, and all test sessions have ended. Contract
  and artifacts: `R132-tree-quota-authority-20260915.md`.
- No production/DB/config, deployment, service/listener, commit/push, skill or
  game-probe action occurred. This is not full receipt authority: score/run/
  player/reward contracts, historical reconciliation and crash-safe accounting
  remain open, as do R67 production cultivation and whole-project Final Review.

### R134 Tree Operation Durability (2026-09-15)

- A bounded per-identity `tree_operation` journal now checkpoints owned intent,
  allocation, settlement and completion. Main-loop durable acknowledgements
  precede mutations; unconfirmed outcomes retain their pending descriptor.
- Journal publication and the owned MiniApp view use one save. Local recovery
  performs no HTTP and cannot overwrite a replaced owner or newer view. Held
  roles do not prevent the scheduler from considering later identities.
- Seven real process kill/reload cases cover intent, start dispatch,
  allocation, submit dispatch, settlement, second intent and completion.
  Confirmed prefixes survive; unresolved rounds are never replayed.
- Expanded focused regression: 608 passed in 37.09s. Final full regression:
  14428 passed and 1275 subtests passed in 406.60s. JUnit comparison with R133
  verifies 28 additions, no removed cases, no failures/errors/skips, and all
  focused cases present in the full run. Configured/scoped full-F Ruff,
  compileall, dependency and whitespace checks pass.
- The first full run exposed one legacy test with mocked eligibility but no
  registered owner. Its fixture now registers that owner; the original
  timeout/no-resend assertions remain unchanged. The failed report is retained.
- This closes the current tree-operation durability sub-scope of C3/C4, not
  those packages. Original-round recovery, historical accounting, production
  reconciliation and R67 per-role cultivation evidence remain open. Contract:
  `R134-tree-operation-recovery-20260915.md`.

### R135 Nanlong Closeout And Protocol Scope (2026-09-15)

- Confirmed placement now retains recall when the prompt has expired or a
  rejection was sent. Direct, delayed-receipt and restored-rejection paths
  share finalization; rejection does not wait for a nonexistent trade result
  or repeat after reload. SQLite reload verifies recall and terminal cleanup.
- Log evidence retains its original clock for reply admission, while new
  follow-ups use recovery time. Uncertain sends are not erased solely because
  the prompt expired. Proven-unsent/deferred recall retains its backoff and
  does not consume the sent-confirmation retry allowance; save exceptions
  likewise stop before transport and preserve cleanup.
- Initial replay had seven failures; review reproduced two further rejection
  recovery failures and nine deferred-recall subtest failures. Final focused
  regression passes 242 cases/329 subtests in 8.38s; final full regression passes
  14440 cases/1284 subtests in 404.46s. Parsed JUnit verifies 12 added cases,
  nine additional subtests, no removed cases, no failures/errors/skips and every
  focused case in the full run. Configured/scoped Ruff, compile, dependency and
  whitespace checks pass. Contract: `R135-nanlong-closeout-20260915.md`.
- Source/caller review corrects the old claim of an active Nangongque custom
  loop. Only the disabled protocol adapter exists. Its single-request executor
  can now share a budget and operation check; one-attempt mutation behavior is
  unchanged. No combat loop, new endpoint or enabled switch was added.
- R07/R11, broader Nanlong post-dispatch/legacy ownership, C1-C6 final acceptance
  and R67 production cultivation remain open. No production, commit/push,
  listener or skill operation occurred.

### C1 Nanlong Pause And Recovery (2026-09-15)

- Reproduced loss of returned receipts after dispatch-time disable, ignored
  paused completions, control-layer deletion on expired-prompt enable, and
  timeout resend/deletion of known work. The candidate now retains transport
  facts independently of send admission and persists owed cleanup while off.
- Removed the unsafe sent-command retry/timeout fallback. Local recovery checks
  original and recent windows, honors explicit server timestamps, and rejects
  malformed/unrouted legacy receipts. New prompts cannot replace unresolved
  work; the existing UI snapshot continues to show that work as pending.
- Focused native caller/control/reload/UI regression passes 244 cases and 194
  subtests. Full regression passes 14455 cases and 1325 subtests in 406.29s.
  Parsed JUnit comparison verifies 15 new cases, 41 new subtests, four explicit
  test renames, no lost cases and every focused case in the full run; there are
  no failures/errors/skips. Configured/scoped Ruff, compile, dependency and
  whitespace checks pass.
- This is a scoped C1 checkpoint, not whole-module or whole-package acceptance.
  Missing no-ID/historical outcomes, legacy account/manual ownership and
  post-result durability remain open. No live operation, switch, listener,
  skill, commit, push or deployment change occurred. R67 production cultivation
  remains unvalidated. Contract: `C1-nanlong-pause-recovery-20260915.md`.

### C1 Nanlong Orphan Receipt Recovery (2026-09-15)

- Reproduced orphan placement/recall anchors falling through to new trading or
  being cleared at expiry, partial recall phases repeating a send, and malformed
  Nanlong numeric fields breaking the native UI snapshot.
- The candidate retains ambiguous evidence and restores missing current-step
  fields from exact native pending/sent records only. Original identity,
  account, chat, prompt, chain, operation and dispatch evidence must agree;
  conflicting or later unrepresented operations cannot release an earlier step.
  Recovered replies rejoin existing handlers rather than replaying mutations.
- Native module controls and temporary SQLite reload verify once-only cleanup
  and exact-chat pending removal. Original prompt/send/recent log windows remain
  bounded. The UI shares Nanlong's parser without rewriting malformed state.
- The first full run exposed pollution from the new test fixture's historical
  health-wait clock. An ordered two-test reproducer confirmed it; the fixture
  now restores the clock. No runtime health guard was relaxed. Final focused
  regression passes 340 cases and 494 subtests; full regression passes 14467
  cases and 1376 subtests in 406.98s. Parsed comparison shows 12 added cases, no
  lost prior cases, all focused cases present and no failures/errors/skips.
- This closes only the documented known-orphan sub-scope. Unowned legacy and
  missing historical outcomes, broader business-text/post-result durability,
  R07 and C1-C6 whole-package acceptance remain open. R67's 19 subordinate roles
  still lack production cultivation validation. No production, switch, listener,
  skill, commit, push or deployment changed. Contract:
  `C1-nanlong-orphan-recovery-20260915.md`.

### C4 Owned JSON Reload Repair (2026-09-15)

- The post-handoff review reproduced corrupt owned records becoming empty
  through the generic persistence codec. Nine concubine journals and adjacent
  second-soul/Tianti command journals now preserve invalid evidence; valid
  empty objects remain empty, and unrelated JSON defaults are unchanged.
- Temporary SQLite, real controls/restoration/schedulers, rejected replies and
  UI snapshots confirm the hold survives without leaking fixture secrets or
  blocking a healthy unrelated module. No recovery controller or SQL column
  was added. The original native reproducer now has zero mock sends.
- The final new file passes 234 cases; related regression passes 3736 cases
  and 87 subtests; full regression passes 14701 cases and 1376 subtests in
  404.70 seconds. Parsed comparison shows 234 additions, no removed cases,
  no related case missing from the full run, and zero failures/errors/skips.
  Contract and reports: `C4-owned-json-review-20260915.md`.
- Other JSON contracts, already-erased historical evidence, migration,
  retention and C1-C6 whole-package acceptance remain open. R67's 19 roles
  still lack production cultivation verification. No production, switches,
  listener, skill, commit, push or deployment change occurred.

## Deployment Constraint

The chat-key migration is not a code-only rollback. Once two chats contain the
same message ID for one identity, old loaders collapse those rows in memory.
Any later deployment needs a verified database snapshot and an explicit rollback
procedure; do not run the old code against newly written multi-chat state.
This candidate has not migrated the production database.
The new tianti_commands column retains unresolved native spending. Old readers
that ignore it cannot safely resume those roles; code-only rollback is not a
valid way to clear an unknown operation.
The new fishing_result_pending column retains prepared local accounting after
a failed commit. Rollback must preserve/reconcile it; an old reader ignoring
the column must not resume those identities. Only temporary databases were
migrated in tests, not production.
The fishing_operation column and result-pending v2 reference preserve per-round
ownership and atomic receipt accounting. Rollback must retain both. Old readers
ignoring an unknown operation cannot resume fishing safely; a fresh dwelling
entry or a timer is not original-round recovery evidence.
The treasure_result column retains a bounded returned result and its atomic
projection ownership. Rollback must preserve/reconcile pending and unknown
results; old readers ignoring this column cannot safely restart treasure.
R128 additionally retains `treasure_operation` and linked v2 result records.
Rollback must preserve/reconcile both; an old reader seeing the previous
completed result cannot ignore a newer unresolved operation and resume sends.
Only temporary test databases have been migrated.
R130's version-2 operation records additionally retain already-accounted
receipt prefixes. Preserve them on rollout/rollback; replaying the cumulative
receipt list as a fresh inventory delta would repeat previous rewards.
R131 retains tree open/unknown markers in the existing runtime MiniApp slot.
Future rollout/rollback must preserve those markers; an older reader that
ignores them cannot safely resume affected identities. R131 does not add a
crash-safe tree request journal or validate production migration.
R134 adds the bounded `tree_operation` column. Preserve its pending descriptor,
confirmed facts and publication marker together with the MiniApp view during
rollout/rollback; an older reader must not ignore the journal and reopen a held
round. Only temporary SQLite databases have been migrated and kill-tested.
The concubine_status_query column retains owned status reads. Its additive
schema and reload behavior are tested only in temporary SQLite. A future
deployment/rollback must preserve unresolved query state and the changed
gift-preparation marker timing; ignoring the column is not reconciliation.
R102 also stores a strictly terminal observed-query variant in this column.
Older readers reject it; a code-only rollback is not a migration plan. New log
provenance flags are observational only: their absence in retained old logs
cannot be reinterpreted as evidence of unedited/unforwarded commands.
R86 extends that query JSON with fragment reads and bound confirmation keys.
Old scalar confirmation does not authorize spending; readers that ignore the
new ownership/expiry contract cannot safely resume puzzle automation.
The concubine_gift_actions column retains unknown resource mutations and
completed daily facts. Do not resume old gift/reset code against it. Legacy
scalar-only gift holds need an explicit migration plan before rollout.
The concubine_greet_action column likewise retains unknown greetings and daily
facts. Ignoring it with the old timeout/reset implementation can repeat an
already dispatched action; scalar-only greet holds need explicit reconciliation.
The concubine_fragment_actions column retains unresolved dream/puzzle mutations
and their query-bound confirmation. Old scalar timeout/reset readers cannot
safely resume them. Preserve these slots during migration and rollback; neither
deleting a slot nor replaying an unowned legacy confirmation is reconciliation.
The concubine_voyage_actions column likewise retains launch/settlement intent
and completed outcomes. Preserve its unknown slots on migration/rollback;
old voyage timeout/replay code cannot safely resume them or infer new rewards.
The concubine_tianji_action column retains divination intent and authoritative
completion/CD. Preserve it on migration/rollback; ignoring or deleting an unknown
slot is not reconciliation and must not re-enable old timeout/provisional-CD code.
The concubine_heart_session column retains the whole launch/choice chain,
unknown effects and committed completion. Preserve it on migration/rollback;
old scalar retry/restore/CD readers cannot safely resume these sessions.
The concubine_reacquire_action column retains purchase intent, intermediate
acknowledgement and committed results. Preserve it on migration/rollback;
old scalar timeout/retry readers cannot safely resume unknown acquisitions.
The concubine_external_observation column retains unresolved external changes
and their absolute-calibration barrier. Preserve it on migration/rollback;
old direct-delta or status readers cannot safely ignore pending observations.
The candidate Yinluo archive and persisted restoration origins also require
snapshot/migration/rollback review; older candidate readers do not understand
the new hot/cold retention contract. Do not drop archive tables or restoration
markers to make an old reader accept newer state.
R119 adds a v2 cold payload for exact expired read evidence, without new SQL
columns or hot-state fields. Old validators reject those records. Preserve
their original result ownership through any later rollback/migration; deleting
them to regain capacity would break late-edit attribution.
R120 adds `yinluo_archive_business` and a `business_archive` manifest inside the
existing accounting JSON. Preserve the hot JSON, cold business points and native
command/result archive in one verified snapshot. Old readers cannot safely
resume these identities; deleting the table/manifest or synthesizing a missing
manifest from current rows is not migration or rollback. Only temporary test
databases have used this schema.
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
R65 adds `xiuwei_accounting` as another identity-runtime JSON column. Existing
profile numbers alone do not populate its verified baseline, and external
legacy resource writes leave the balance unverified. A later deployment needs
explicit reconciliation from authoritative evidence and an approved retention
and migration procedure. Do not clear this ledger to bypass a hold, silently
attribute old results to a newly bound account, or run old raw-subtraction
code against new receipt state during a code-only rollback.
The candidate Yinluo integration also adds `yinluo_accounting`, including
reservations and beneficiary bindings. A rollback cannot reinterpret those
fields as the old pre-debited observation/snapshot format. Resolve old pending
ownership and define capacity retirement before approving a production schema
migration; a conservative hold alone is not automation acceptance.
R117 also retains `legacy_pending_invalid` in the existing Yinluo observation
JSON. Older normalizers/loaders ignore this marker and erase malformed pending
evidence. Preserve the marker and original payloads through any separately
approved migration/rollback; do not resume old automation against this state
or clear quarantine merely to bypass an unresolved operation.

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
   R34. R135 covers replay-time versus event-time follow-up deadlines, cleanup
   after a successful placement whose prompt expires or choice becomes reject,
   delayed/restored rejection finalization and deferred-recall backoff. The C1
   Nanlong pause/recovery checkpoint covers post-dispatch disable, actual
   enable/disable handlers, passive paused outcomes, known-result retention,
   original-window replay and malformed legacy rejection. Unowned legacy
   account/manual outcomes and general post-result durability still require
   their own evidence; retaining an unknown outcome is not reconciliation.
   The subsequent C1 orphan-receipt checkpoint now covers missing current child
   fields, exact native evidence recovery, partial/stale phases, toggle/reload
   continuity and the module's malformed numeric UI state. It does not infer
   missing ownership or historical outcomes.
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
   every custom game loop. The later per-game checkpoints below establish their
   scoped budgets and ownership; they do not certify unnamed callers. R135
   confirms Nangongque has no production loop and supplies budget/operation-check
   composition to its disabled protocol executor. Do not invent a runtime loop
   to clear an inaccurate inventory item. Continue existing callers' unknown
   outcomes and owner invalidation that lack native acceptance evidence.
   R37 covers shared pool retention/deletion/rebind behavior. R32 closes Requests' hidden
   redirect path, not the remaining runtime lifecycle or entry-refresh matrix.
   R35/R36 cover the tower scheduler, public wrapper and guarded dwelling
   loading/external and tower HTTP threads. Other game callers still need
   individual admission, cancellation and result-retention tests; the optional
   transport check does not automatically protect every caller. R38 covers
   probe cancellation and entry/claim generation changes, not all public-entry
   refresh/invalidation paths. R40 covers stargazer's action loop, public and
   command callers, including shared budget, thread draining and partial
   collection retention. Remaining existing custom loops still need individual
   review against C3; the Nangongque adapter is not such a loop. No live
   validation is approved.
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
11. R64 covers negative sect-checkin observation/cleanup. R65 now contains the
    scoped Telegram cultivation ledger and duel source/revision integration
    described above, but is still open. Continue with Yinluo cultivation and
    sha accounting as one reviewed contract, then legacy duel count/CD/log
    trust, other resource consumers, MiniApp/API workers and the remaining
    acceptance matrix. Resolve migration/retention limits before any production
    validation; passing offline tests does not authorize deployment.
12. R66 repairs only the read-only Tianjige banner boundary and the shared
    command-center worker's admission/draining contract. Do not count it as
    completion of Yinluo automation or other public-entry workers. Continue
    R65 with native command/result ownership and the complete sha writer set;
    keep R07's unapproved shared architecture and CommandAttempt control out
    of that work.
    The R65 facts/replay/book and resource reservations now reach the candidate
    native/replay/SQLite paths; scoped old financial writers are replaced.
    Next resolve legacy pending migration and operational retention, including
    beneficiary-binding retirement and late corrections. Recheck queue/forced
    stop behavior and nonfinancial Wanxin paths before closing the module.
    Do not clear a resource hold or delete completed bindings merely to regain
    capacity, and do not mistake the offline integration tests for deployment
    acceptance.
    R113 now covers pre-dispatch cancellation, exact completed/archived return
    receipts, immutable original send times and no-op transport persistence.
    Its 49 additional cases and full suite 12207 pass. R114 adds scoped atomic
    read expiry/pending cleanup, late transport and active-caller ownership;
    62 added cases, associated suite 955 and full suite 12269 pass.
    R115 adds scoped atomic completed-pending cleanup and hot/cold recovery,
    including exact guard matching; 55 added cases, associated suite 1102 and
    full suite 12324 pass. R116 separates exact read-only completion, expiry
    and manual admission cleanup from retained financial barriers; 78 added
    cases, associated suite 1627 and full suite 12402 pass. R117 repairs
    legacy-evidence loss through normalization, MiniApp read admission,
    unavailable-module cleanup and the observation codec; focused suite 253
    and associated suite 1880 pass, as do all 122 new cases and the 12524-case
    full regression. R118 adds exact native-backed old-action migration,
    atomic old-pending/hold completion and strict staged types. Its 128 new
    cases and 2058-case/372-subtest associated suite pass, as does the full
    12652-case/1275-subtest suite. R119 follows through on useful native
    calibration at capacity and exact expired-read cold retention; 82 new
    cases, an associated suite of 2003 cases/323 subtests and the full suite
    of 12734 cases/1275 subtests pass. R120 adds native-backed business-point
    retention through distinct-beneficiary churn, paired hot/cold moves,
    manifest integrity and write-locked proof validation. Its 78 focused cases
    pass. The associated 2048-case/72-subtest checkpoint precedes its staged-slot
    guard; the final full rerun passes 12812 cases and 1275 subtests, including
    all associated modules. General legacy no-ID hold
    resolution, complete forced-stop/storage recovery, unproved manual points,
    cold-storage scaling, already-missing receipts and full-capacity recovery
    remain open.
13. R67-R72 cover the channel-retreat player contract, standalone/fate lifecycle,
    latest runtime record retention, structured sanitization and scoped unknown
    recovery. Continue incomplete quiet-room/legacy outcome reconciliation,
    per-game field/history bounds, other custom workers and the remaining
    matrix. Do not substitute these component tests for production per-role
    recalibration, R65 retention/migration, unapproved R07 work or Final Review.
14. R73/R74 cover scoped Wanxin native ownership, unresolved slots and exact
    completion cleanup; legacy/forced-stop migration and post-consumption
    corrections remain open. R75/R76 cover the small-world/journey selected
    player contract; R77 covers command-center request/receipt ownership.
    R78 adds YuanYing whole-operation admission, retained unknown launch state,
    cancellation adoption and scoped legacy-send exclusion. R79 repairs the
    read-only Tianti/Yinluo/concubine entry lifecycle and Tianti MiniApp panel
    policy, including nested limits and local busy integration. R80 adds owned
    native Tianti dispatch, exact completion, strict legacy/reply recovery and
    one passive/native reducer. R81 adds the concubine status parser/reducer,
    entry clocks and anchor contract. R82 adds native status/gift-status query
    dispatch, owned reply recovery, read-only expiration and restart retention.
    R83 adds owned gift inventory/spending, shared reply handling and bounded
    daily facts. R84 adds the daily greeting lifecycle using that same engine.
    R85 adds strict fragment panels; R86 adds owned fragment reads, recovery
    and bounded confirmation provenance. R87 adds owned query completion
    rollback, pre-dedupe passive replay and checked recovery saves. R88 adds
    owned dream/puzzle mutations and fragment-confirmation admission. R89 adds
    owned voyage status, explicit untimed-refusal read-only continuation and
    transactional read recovery/expiry. R90 adds owned voyage launch/settlement,
    typed outcomes and exclusion from unowned outer summary replay. R91 unifies
    owned phase cleanup across query/affinity/fragment/voyage. R92 adds owned
    divination dispatch, terminal facts, recovery and queued sibling exclusion.
    R93 adds strict heart reply facts and removes passive text-only settlement.
    R94 adds owned launch/choices, original prompt edits, bounded read probes,
    unknown-send/restart retention, atomic results and scheduler continuation.
    R95 adds owned spouse reacquisition, bounded command redirection, strict
    acknowledgement/edit clocks, atomic completion and unknown-send retention.
    Its final isolated full regression passes 10793 tests. R96 adds bounded
    external observations, strict source/ownership and atomic absolute calibration;
    its focused 3694 cases and final isolated full suite of 10946 tests pass.
    R97 adds the owner Wanxin reply grammar and preserves accounted outcomes
    through incomplete/type-changing edits. R98 adds the nonfinancial commission
    and identification reply contract, original helper checks and latest-invalid
    log evidence rejection; its final full suite passes 11137 tests.
    R99 adds scoped orphan-sending isolation and strict financial mirror/receipt
    binding; its full suite passes 11176 tests. R100 suppresses only Wanxin
    affinity writes already covered by a newer concubine snapshot; its full
    suite passes 11185 tests. R101 verifies the ordinary reverse ordering and
    repairs the separately reproduced legacy/manual and value-round-trip
    gaps using retained native evidence; focused suite 3421 and final full
    suite 11233 pass. R102 adds strict terminal observed reads, original native/
    log provenance, transactional replay and non-livelocking failed-save holds;
    its final focused suite 3938 and full suite 11404 pass. R103 repairs proven
    same-second native source order, including edited affinity round trips;
    its 81 new cases, associated suite 3740 and full suite 11485 pass. R104 derives
    owned greeting/gift calibration from existing completion/read records and
    protects stale native/MiniApp/moon panels; 65 new cases, associated suite
    4091 and full suite 11550 pass. The earlier request
    to verify unowned status source clocks/rollback is covered in the candidate,
    not in production. Review legacy read/mutation migration and residual
    import/test shims without deleting sibling engines' module-facade exports.
    Continue unproven overlapping/same-clock observations and general legacy/manual/
    post-consumption reconciliation. Moon-join success still requires an
    authoritative fixture.
    Continue legacy/manual reconciliation beyond these observations; do not
    apply read expiration to mutations. Then
    revisit Tianti post-terminal correction/legacy reconciliation and the
    remaining per-game lifecycle and capacity gates. These scoped repairs do
    not authorize production deployment or close whole-project Final Review.

15. R105/R106 repair fishing worker/caller lifetime, owner checks, result
    retention, partial failure handling and notification/report races. The
    R107 adds checked atomic projection, bounded local pending recovery and
    relevant read/report guards; its final full suite passes 11752 tests.
    R108 now repairs explicit-empty catches, unconfirmed rewards and readiness
    authority; its full suite passes 11802 tests. R109 repairs per-round loose
    rewards and recursive material/gain fallback, with 73 new cases and a full
    suite of 11875 tests. R110 adds bounded same-token result recovery and
    explicit transport/unknown evidence, with 46 new cases and a full suite
    of 11921 tests. R111 replaces recursive quota discovery with shared current
    field/alias/arithmetic validation and confirmed nonregressive calibration;
    its 90 new cases, associated suite 846 and full suite 12011 pass.
    Historical replay still needs identifiers, not content dedupe. R111 does
    not certify the old inferred-limit policy when quota fields are absent.
    R112 adds per-request ownership, exact-round result-only recovery, v2 atomic
    accounting, startup/control guards and interruption/reload tests. Its 147
    new cases, associated suite 964 and full suite 12158 pass.
    Continue authoritative cross-day clocks, historical/cross-owner receipt
    identity, old quota inference and broad inventory/manual reconciliation.
    Local projection recovery does not establish once-only historical replay
    or authorize rollout.

16. R121 repairs trial worker/caller lifetime, shared exclusion, bounded per-run
    requests, guarded entry/HTTP waits and retained settlements/transport evidence.
    Its 92 new cases and expanded associated suite of 558 cases/19 subtests pass;
    the first full run's sole failure was the old sleeper clock fixture, now
    corrected without relaxing assertions or runtime limits. The final isolated
    full regression passes 12904 cases and 1275 subtests; parsed JUnit has no
    failures/errors/skips. Contract and artifacts are recorded in
    `docs/audit/R121-trial-lifecycle-20260914.md`. R122 adds scoped current finish,
    reward/quota and request/identifier authority; its 185 new cases and final
    associated 683 cases/19 subtests pass, as does the isolated full regression
    of 13089 cases/1275 subtests.
    R123 adds batch membership, authorization cleanup and retained late-result
    reporting; its 37 new cases and associated 829 cases/19 subtests pass, as
    does the final full suite of 13126 cases/1275 subtests. It does not add
    persistent report storage or automatic notification/gameplay retries.
    R124 adds a bounded owned operation journal, strict pending transitions,
    checked intent/receipt persistence and local-only interrupted recovery;
    seven process-kill boundaries perform no recovery HTTP. The associated
    2668-case/210-subtest suite and full 13256-case/1275-subtest regression pass,
    including all 128 new cases and two added native cancellation variants.
    Continue authoritative unknown-round reconciliation, original-entry
    reconstruction, pre-worker entry allocation, cross-day/history authority,
    historical inventory identity and failed-batch delivery/retention policy.
    This does not validate R67 channel cultivation in production or authorize
    deployment.

17. R125 repairs treasure worker/native lifecycle, selected-player propagation,
    account-shared exclusion, post-settlement state and partial-result retention.
    Unknown results are held without claiming success/daily completion; changing
    day or sibling role cannot clear them. Associated offline regression passes
    2722 cases/278 subtests, including 109 new cases; isolated full regression
    passes 13365 cases/1275 subtests. Parsed reports have no failures/errors/skips.
    R126 verifies scoped current quota/receipt/session authority and removes
    recursive current-result material/session discovery. Its 145 added cases,
    associated 2867 cases/278 subtests and full 13510 cases/1275 subtests pass.
    R127 adds scoped atomic returned-result accounting and local-only
    recovery across callers and pause controls. Its 147 new cases, focused
    407 cases/10 subtests and associated 3235 cases/307 subtests pass, as does
    the final full suite of 13657 cases/1275 subtests. R128 adds new-operation
    dispatch checkpoints, restart-safe linked local accounting and preservation
    of completed evidence across later read failures. All 182 added cases,
    focused 703 cases/5 subtests, associated 3417 cases/307 subtests and full
    13839 cases/1275 subtests pass. R129 adds native board authority, owned
    reveal progress and retained same-session geometry/cells; all 83 new
    cases, focused 957 cases/19 subtests, associated 3500 cases/307 subtests
    and full 13922 cases/1275 subtests pass. R130 adds original-session recovery
    and new-only receipt projection across public/command/UI/background paths;
    70 new cases and six actual continuation kill/reload boundaries pass, as
    do focused 1027 cases/19 subtests, associated 3570 cases/307 subtests and
    full 13992 cases/1275 subtests. Final review also closes malformed-quota
    checkpoint admission and shortened original-round recovery spacing.
    Continue unbound pending-enter correlation, historical/cross-day receipt
    identity, old inventory digest-key migration, retention/delivery and
    authoritative unknown/original-round reconciliation; retaining a hold does
    not finish automation.
    Do not treat a fresh entry, next day or content digest as reconciliation.
    Contracts: `docs/audit/R125-treasure-lifecycle-20260914.md` and
    `docs/audit/R126-treasure-receipt-authority-20260914.md` and
    `docs/audit/R127-treasure-result-commit-20260915.md` and
    `docs/audit/R128-treasure-operation-recovery-20260915.md` and
    `docs/audit/R129-treasure-run-authority-20260915.md` and
    `docs/audit/R130-treasure-original-round-recovery-20260915.md`. R67 production
    cultivation and whole-project Final Review remain open.

18. R131 implements tree worker/caller/scheduler lifecycle, bounded native
    budgets, original owner/control admission, joined cancellation, retained
    facts and no-retry open/unknown outcomes. All 175 new cases, focused 519
    cases/5 subtests, expanded associated 4361 cases/295 subtests and isolated
    full 14167 cases/1275 subtests pass. R132 adds strict current quota and
    supplied season/day context, non-regressing two-mode continuation and
    bounded reads for incomplete submit panels. Its 174 new cases, focused
    693 cases/5 subtests, associated 4535 cases/295 subtests and full 14341
    cases/1275 subtests pass. R133 now covers tree score/verification types,
    supplied round/mode/player binding and current-only reward authority,
    including partial rewards with unknown scores and native/SQLite propagation.
    Its focused 485-case and full 14400-case/1275-subtest regressions pass;
    parsed JUnit confirms 59 added cases and no removed baseline cases.
    R134 adds a bounded current-operation journal, acknowledged durable intent,
    atomic view publication and local-only recovery. Seven real process-kill
    boundaries and focused 608/full 14428-case regressions pass; 28 added cases
    and no removed baseline cases are verified. Continue original-round
    recovery and historical accounting, not the already-verified current
    journal. Do not replay a mutation because a timer, day or entry changed.
    Unknown holds do not complete automation or establish R67 production
    cultivation health.
    R133 removes the legacy recursive reward traversal. Missing echoed
    chronology fields are still not proof of historical round/day ownership. Production remains
    unchanged and unvalidated. Contracts: `R131-tree-lifecycle-20260915.md` and
    `R132-tree-quota-authority-20260915.md`, `R133-tree-receipt-contract-20260915.md`
    and `R134-tree-operation-recovery-20260915.md`.

## Completion Gate

Do not mark the objective complete while any matrix row lacks its required
evidence, a confirmed finding is unresolved, or candidate and deployed behavior
have not been reconciled. Unavailable external-game evidence must be reported
explicitly rather than replaced with a passing mock test.
