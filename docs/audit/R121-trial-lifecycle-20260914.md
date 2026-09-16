# R121 Trial Worker And Caller Lifecycle

Status: scoped offline candidate repaired and verified; 92 new lifecycle cases,
the expanded associated 558-case/19-subtest suite and the final isolated full
suite of 12904 cases/1275 subtests pass. Production acceptance and whole-project
Final Review remain open.

## Required Contract

1. One identity run shares a bounded request budget across start, finish,
   embedded challenges, next and subsequent starts. A round limit must stop
   before opening an unused next round. No new mutation retry is authorized.
2. Cancellation drains the in-flight worker before releasing exclusion. It
   stops later requests and retains any already-returned settlements. Proof
   waits, budget waits, WebView RPCs and each HTTP boundary recheck admission.
3. Public and command callers share game exclusion, retain the original
   identity/account and distinguish result ownership from dispatch permission.
   A disabled switch cannot authorize another request or erase a confirmed
   result. A replaced owner cannot inherit it.
4. Later failures/cancellation retain earlier settled rounds and transport
   evidence. Partial work is not full completion, but its confirmed rewards
   still belong in the batch report. Failed notification is not delivery.
5. Preserve existing solvers, daily policies, UI settings and global request
   limits. No live request, deployment, service/DB/config mutation, skill edit,
   commit/push, World Boss enablement or CommandAttempt control.

## Review Findings

- `trial_miniapp` directly awaits `asyncio.to_thread`; caller cancellation can
  release locks while the thread continues through finish/next.
- Each HTTP step currently omits a flow budget and operation check. The loop
  also calls next after completing its final configured round.
- A later proof/wait exception escapes the loop and the production wrapper
  returns an empty failure, losing previous settled rounds.
- Public trial and authorized command trial have independent locks, omit
  awaited owner/control checks and do not adopt drained cancellation results.
- Batch summaries omit material gains from partial results and mark themselves
  finalized before successful notification.

## Candidate Repair

- Single and batch workers now share the lifecycle engine while the single-run
  wrapper preserves its result/proof/event shape. A single adapter-policy budget
  covers start, finish, embedded challenges, next and token-based starts. The
  default remains 32 requests, with existing pacing and no mutation retry.
- A completed requested round limit is checked before another next request.
  Already-returned settlements are retained before later parsing, proof waits,
  budget waits, cancellation or transport failure can end the flow. Partial
  summaries show both the gains and the interruption reason.
- The production wrapper uses the existing draining thread helper. Every RPC
  and subsequent HTTP boundary rechecks admission; repeated cancellation cannot
  release exclusion while the worker still runs.
- Command and public callers use the same identity trial lock; the public caller
  also keeps the public-entry lock. Both retain the original identity object and
  account. UI/background observation guards propagate through entry selection,
  external-entry retrieval and the trial worker. Maintenance still permits
  MiniApp HTTP; a manual pause prevents later dispatch.
- Returned settlements belong to the original owner even after its switch is
  disabled. A removed, replaced or rebound owner cannot inherit them. Cancelled
  failed requests retain transport evidence without inventing rewards; the public
  response carries sanitized HTTP events, status and error for its caller.
- Batch completeness admits only settled/daily-limit outcomes, not partial or
  next-unavailable outcomes. Settled rewards from incomplete flows still count.
  Reporting is serialized per batch and uses an immutable result snapshot;
  only an explicit successful notification can retire the unchanged batch.
  Failure, cancellation, a late result or a replacement batch cannot be erased
  by an older in-flight report.

## Offline Evidence

- Initial nine failing reproducers:
  `/tmp/xiuxian-r121-repro-20260914.xml`.
- Final focused/associated checkpoint: **281 passed, 5 subtests**, 20.06 seconds,
  including **92 new lifecycle cases**; zero failures/errors/skips in
  `/tmp/xiuxian-r121-focused-final-20260914.xml`.
- New coverage includes default-budget exhaustion, embedded and token-based
  continuation, cancellation at start/proof-wait/finish/next, repeated cancellation,
  native public/command exclusion, four WebView wait boundaries, identity/account
  changes, maintenance admission, retained partial/failed-request evidence,
  notification failure/cancellation and concurrent/late/replaced batch reports.
- Two pre-existing batch tests now explicitly return `True` from their audit
  mock, matching the real `send_audit_log` delivery contract. The initial
  associated run also caught and repaired a single-run event-shape regression.
- The first full run had **12903 passed, 1 failed, 1275 subtests**, 322.27 seconds,
  in `/tmp/xiuxian-r121-full-20260914.xml`. Its sole failure was an old sleeper
  fixture that recorded waits without advancing its clock. The fixture now
  advances a fake clock shared with the real default request budget; it still
  asserts exactly one proof wait and additionally asserts two requests. No
  runtime limit or assertion was relaxed.
- The expanded associated rerun passes **558 cases, 19 subtests**, 24.94 seconds,
  including WebAppCore and public UI/background/stargazer lifecycle contracts:
  `/tmp/xiuxian-r121-related-final-20260914.xml`.
- Final isolated full regression: **12904 passed, 1275 subtests**, 325.03 seconds,
  in `/tmp/xiuxian-r121-full-final-20260914.xml`. The parsed JUnit artifact has
  zero failures/errors/skips. Repository undefined-name/redefinition Ruff,
  compileall, dependency checks and whitespace checks also pass.
- No production file, live game request, deployed state/configuration, service,
  listener, World Boss/refinement switch or skill was changed. The candidate
  stays on `rebuild/stability-20260907` at base HEAD `cbf152cd61325ca9218c74ee995d643038b20905`;
  changes remain uncommitted and unpushed.

## Remaining Scope

This lifecycle repair does not certify every finish/remaining-count payload,
cross-day quota authority, legacy result migration, process-kill durability,
once-only inventory accounting or unknown-round recovery. Those need explicit
native receipt/recovery contracts, not blind fresh-entry retries or fabricated
cooldowns. Failed batches remain in memory for an existing later finalization
or explicit retry; this patch does not add automatic notification retries,
persistent report delivery or a failed-batch retention policy. Keep these limits
visible in the parent audit until verified. R67 subordinate-role cultivation
has not been recalibrated in production by this work.
