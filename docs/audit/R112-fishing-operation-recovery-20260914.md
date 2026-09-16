# R112 Fishing Operation Recovery

Offline candidate only. Production, live requests, services, configuration,
listener, skill, commits and pushes remain unchanged. R67 production cultivation
is unvalidated. This is module-local ownership, not shared R07 transport control
or a promotion of CommandAttempt beyond shadow-only.

## Contract Before Implementation

The R110/R111 evidence is insufficient for restart admission: a pre-chain marker
cannot distinguish a crash after the first settlement from a crash during the
following next request. One final chain save cannot close this window.

1. Preserve one account/identity-owned operation and its confirmed prefix before
   admitting another chain. Save an intent before every mutating fishing HTTP
   dispatch and a confirmed receipt before advancing to the next round. A save
   failure must stop further gameplay. Worker-to-runtime checkpoints must be
   serialized on the main event loop, not write shared state from its HTTP thread.
2. Bind every receipt to the exact round fingerprint, not catch contents. A
   successful next acknowledgment identifies its new round; a lost next response
   knows only the previous request round. That previous ready result cannot
   resolve the uncertain next. Keep this distinction explicit in worker evidence.
3. A recovery worker may only read result for the exact known pending round.
   Reject mismatched/new tokens, already-confirmed rounds, corrupt provenance
   and a next with an unknown new round before authorization or HTTP. Never
   send start, finish, next, proof, bait purchase or a text fallback to recover.
   Use the same request/error budgets, bounded result polling, Retry-After,
   operation checks and cancellation draining as ordinary fishing.
4. The durable confirmed prefix and new recovery result must use the checked
   local accounting transaction. Applying that prefix and advancing/clearing
   its operation ownership must commit together. A pending local projection
   is recovered before any WebView/HTTP work. Disable/rebind/replacement and
   stale callbacks cannot discard or adopt another owner's operation.
5. Startup, enable/disable and legacy retirement must preserve unresolved
   MiniApp work. Public/message entry and reporting need the same admission
   contract. Expose why a record is held and which evidence can reconcile it;
   a timer, empty queue or fresh unrelated entry cannot release it. Raw tokens,
   URLs, initData and proofs are not business-ledger fields.

## Acceptance

- Worker result-only tests must prove exact binding, no mutation dispatch,
  strict readiness, budget/backoff behavior, cancellation and credential safety.
- Temporary SQLite and spawned-process tests must cover interruption before
  transport, after a result, during next, after a next acknowledgment and before
  local accounting. Restore/replay must apply each retained receipt once and
  never repeat an unknown mutation or reclassify an old result as a new round.
- Cover both public and message callers, their shared lock, startup/control/UI
  admission and accounting save failures. Then run the isolated full suite and
  review the diff. Passing only worker tests does not close R112.

## Progress

- Review and recovery contract recorded.
- Exact-round result-only worker and synchronous checkpoint protocol validated:
  45 recovery cases and 25 checkpoint cases. Normal/recovery workers share the
  bounded result reader. A checkpoint must acknowledge exactly True; false,
  exceptions, coroutine acknowledgements and stale guards stop further HTTP.
  Confirmed round fingerprints and sanitized receipts survive cancellation and
  partial chains. Ambiguous/failed decoding retains a distinct projection-error
  receipt, not a falsely complete catch. Next-token aliases must agree.
- Worker-stage isolated full regression: 12081 passed, 1275 subtests, 176.27s;
  /tmp/xiuxian-r112-worker-full-20260914.xml. Associated run before the final
  additional case: 915 passed, 36 subtests, 27.12s;
  /tmp/xiuxian-r112-worker-related-20260914.xml. Configured Ruff, scoped F841,
  compilation and whitespace checks pass. Worker tests alone do not close R112.
- Durable checkpoints, caller/accounting integration, startup/UI and process
  recovery are implemented in the candidate. The new tests use fake transports
  and temporary SQLite only; no production hold or migration is active.

## Durable Integration

- One bounded fishing_operation record per identity retains its original
  account, operation UUID, projection basis, confirmed receipts, accounted keys
  and absolute retry boundary. Worker-local sequences are mapped by a captured
  writer to checked durable revisions; a new recovery worker cannot overwrite
  another run or reset the retained prefix.
- The blocking worker hands checkpoints to the captured main event loop.
  State/SQLite writes happen there synchronously; the worker waits for a checked
  acknowledgement before dispatch. A failed write stops gameplay and retains
  explicit evidence for local recovery. A saved intent is always ambiguous
  after process death, even when its action_dispatched field is false.
- Extend the local result transaction with a versioned operation reference.
  Receipt accounting and operation advancement commit together; legacy v1
  projections remain readable. The scheduler uses the same game lock as both
  callers before performing local recovery or followups.
- Recovery admission first handles local receipts. Only an exact known pending
  round may use an incoming original launch for result-only HTTP. A public
  dwelling entry is not that launch. Missing original tokens, unknown next
  rounds, changed projection bases and incomplete projection-error receipts
  remain visible holds, not permission to create a new round.
- Preserve Retry-After across process death, including result-read failures.
  Startup/reset/disable/report reads must not clear retained work or normalize
  its projection basis before recovery.

Historical operations predating the ledger, server-day/source clocks, legacy
inferred quotas, R65/R07 and whole-project Final Review remain separate open
gates. Unknown next without a known new token requires authoritative new-round
evidence; do not substitute a newly generated entry for that evidence.

## Durable Verification

- Added 71 durable-operation cases and six spawned-process cases, in addition
  to the 70 worker cases above. Actual public/message callers now supply the
  same main-loop checkpoint writer. The scheduler takes their fishing game lock
  before local recovery, reporting or transfers. Startup, reset, legacy cleanup
  and report reads preserve unresolved operations and their projection basis.
- The six process cases SIGKILL a worker at an acknowledged pre-transport
  intent, a settlement, next dispatch, next acknowledgement, before accounting
  and during local accounting. Two independent reloads verify zero repeated
  HTTP and once-only retained counts/items. A prior ready token cannot resolve
  unknown next; distinct rounds with identical fish still count separately.
- The result-pending v2 reference binds operation UUID, revision, digest and
  receipt keys. Counts, inventory, reminders and accounted keys commit together.
  SQL-trigger abortion checks the real rollback, not just a mocked false return.
  V1 projections remain readable but cannot silently adopt a v2 operation.
- Review reproduced eight failures in recovery owner/revision replacement,
  post-settlement Retry-After, initial-intent/receipt binding and corrupt-input
  bounds. Five later guard cases covered closed-writer callbacks and falsey
  corrupt state. A confirmed-prefix reminder loss was reproduced after fixing
  that fixture's companion flag. Two serialization-boundary cases then exposed
  inconsistent whitespace/node budgets between validation and persistence.
  These candidate defects are repaired. Initial missing-parameter assertions,
  invalid pond/mock recursion and second-empty-reload assertions were test
  harness corrections, not claimed production reproductions.
- The JSON codec and writer share the 256 KiB bound and the same structural
  limit. Checkpoints contain no token, URL, initData or proof. An oversized or
  corrupt record holds explicitly. An incomplete projection-error receipt is
  never marked fully accounted; other confirmed receipts can still commit.
- Associated final regression: 964 passed, 22 subtests, 16.29s;
  /tmp/xiuxian-r112-verified-related-20260914.xml. This includes all 147 new R112
  cases and relevant caller, UI, startup and existing fishing contracts.
- Final isolated full regression: 12158 passed, 1275 subtests, 186.28s;
  /tmp/xiuxian-r112-verified-full-20260914.xml. Configured Ruff, scoped fishing
  F841, changed-code compilation, pip check and whitespace checks pass. All
  test sessions ended. The scoped R112 candidate implementation is verified;
  the deployment and reconciliation limits below are not closed by this run.
- Optional broader F841 still reports inherited unused locals in cave trial
  overview and UI inventory/error handling, as well as the previously noted
  stargazer test payload. No repository-wide F841-clean claim is made; these
  unrelated warnings and the inherited dirty quiz/tool work were preserved.

## Remaining Boundaries

- Recovery needs an incoming launch for the exact known pending round. Public
  dwelling refresh alone cannot supply that authority, and raw tokens are not
  persisted. Unknown next, unavailable original tokens and incomplete projection
  receipts stay visible holds. This is not a claim of automatic recovery for
  every possible server state or a reason to clear a hold on a timer.
- The ledger protects the captured owner and does not apply a stale callback to
  replacement work. Cross-owner migration and historical reconciliation after
  explicit identity deletion/recreation are not certified by these tests.
- Full inventory/manual reconciliation, authoritative cross-day source clocks
  and quota inference remain open. Valid account bindings and pending-record
  preservation must be checked before any separate rollout approval. Old code
  ignoring fishing_operation must not resume identities with retained work;
  code-only rollback is not an outcome reconciliation procedure.
- Production, services, configuration, live requests, listeners, skills,
  commits and pushes remain unchanged. R67 production cultivation is unvalidated.
