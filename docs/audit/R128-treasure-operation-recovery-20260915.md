# R128 Treasure Dispatch Durability And Interrupted Recovery

Status: scoped implementation, fault tests and final offline verification
complete. Not approved for rollout; the whole-project objective remains open.

## Required Contract

1. Persist owned mutation intent before calling transport. Only literal `True`
   acknowledges the checkpoint. Budget/admission/preparation failures before
   transport are distinct from an exception thrown by an entered transport.
2. Checkpoints retain the selected identity/account, exact request stage,
   hashed session identity, confirmed receipts and current quota evidence.
   No launch token, initData, raw session credential or unrelated payload is
   persisted. Receipts cannot disappear, change owners or move between rounds.
3. A known response must be durable before another mutation. A checkpoint
   failure stops dispatch, and cancellation drains the existing worker and
   its in-progress main-loop save before releasing account exclusion.
4. Restart recovery performs local accounting only, through R127's atomic
   inventory/MiniApp/result commit. A stable operation/checkpoint link prevents
   accounting an interrupted operation twice. Restore only the transaction's
   changes on failed persistence; preserve unrelated updates and newer owners.
5. Pending, corrupt, rebound or unresolved operations block all treasure
   callers and sibling roles sharing either their original or current account.
   Local recovery is permitted during gameplay pause, but does not request
   authentication, a fresh entry, Telegram messages or game actions.
6. Kill/reload tests must cover saved intent, entered transport, known active
   round, confirmed settlement, later-round interruption and local commit.
   A new day, fresh entry or changed payload digest cannot resolve an unknown
   request. Inability to reconcile an original round remains explicit debt,
   not a claim that permanent blocking completes automation.
7. A later read failure cannot replace a previous operation's linked completion
   receipt, inventory evidence or MiniApp projection. A new authoritative
   read-only daily-limit result needs its own acknowledged checkpoint before
   completion; unjournaled mutation facts cannot take the read-failure path.

## Implementation

- Added one owned `treasure_operation` record per identity, bounded to 256 KiB.
  The initial verified read, each mutation intent, known response, settlement
  and final worker evidence are sequence-checked. The worker cannot append a
  receipt to a different session, remove prior receipts, skip request stages
  or dispatch after a failed checkpoint acknowledgement.
- The worker thread synchronously hands each checkpoint to its owner loop.
  A timed-out queued callback is cancelled before writing; a save already
  running drains its acknowledgement. Cancellation preserves returned facts,
  propagates to the caller and retains account exclusion until the worker ends.
- Actual transport entry is tracked separately from HTTP error categories.
  A thrown cancellation or intermediary 4xx is not proof that a mutation was
  unsent. Only an owned explicit business refusal or the guarded pre-dispatch
  boundary can resolve that intent without a game receipt. All requests still
  share the existing budget, with no mutation retries.
- Checkpoints keep canonical validated materials/gains and hashed session
  identity, not raw launch/initData/session credentials or result logs. Invalid
  material fields remain visible as errors; valid portions of a confirmed
  settlement survive later failure. Selected signed player IDs remain bound
  to the original identity and login account.
- R127 result records support a v2 operation/checkpoint link. New journaled
  inventory evidence uses a stable operation source ID, and result validation
  checks the original bases, material counts, state, unknown status and fact
  clock against the checkpoint. Inventory evidence, MiniApp projection and
  completion marker retain their one-transaction commit. Legacy v1 result
  records remain readable; existing historical digest keys are not migrated.
- Local recovery projects only the retained checkpoint, retains its original
  observation time and never creates an entry or sends HTTP/Telegram. A paired
  committed result prevents a second projection even after restart. Unknown
  requests and known-but-unfinished original rounds remain distinct holds.
  Missing, corrupt or rebound evidence cannot become idle by changing day,
  role or account. The original and current accounts both honor sibling holds.
- Public/command/UI/background and paused-main-loop recovery use the same
  admission path. Automatic local save attempts remain at least 60 seconds
  apart. Live switches, actual storage-bag balances and unrelated state are
  not modified by local recovery.
- Final review found a successor-read gap after an earlier completed operation:
  failed initial reads wrote a v1 result over the linked v2 completion, leaving
  the old operation permanently unaccounted. Such read failures now return
  diagnostics without changing the paired record or claiming new stored rows.
  Initial daily-limit business refusals checkpoint their verified quota before
  publishing completion. Unjournaled successful or uncertain game facts still
  block, rather than being accepted as harmless reads.

## Scope And Boundaries

This step covers new treasure request checkpoints and interrupted local
accounting. Historical receipt/digest migration, authoritative unknown-request
reconciliation, cross-day provenance, retention and notification delivery remain
separate requirements. The overall Review -> Rebuild -> Test -> Final Review
goal stays active. R67 subordinate-role cultivation still lacks production
validation.

Offline candidate only: no production edits/configuration/DB, game probes,
services/listeners, deployment, commit/push or skill changes. Preserve World
Boss/refinement switches, inventory API UI-only use, CommandAttempt shadow-only
and Tianxing/deep-retreat independence.

Rollback must retain and reconcile both `treasure_operation` and linked v2
`treasure_result` records. An old reader that ignores the operation column may
see a previous completed result while a new mutation is unresolved; it must
not resume treasure. Only temporary test databases have been migrated.

## Verification

- Original reproducer: **13 failed**, including absent checkpoint support and
  incorrect unsent classification after entered transport. Artifact:
  `/tmp/xiuxian-r128-repro-20260915.xml`.
- The first worker repair passed **266 cases**. Native/runtime tests initially
  exposed an incorrect fixture count of inventory metadata as an item row;
  assertions now exclude only `_meta`, preserving the actual item assertion.
- Expanded save fault tests found **3 cancellation-propagation failures**;
  all four save-cancellation variants now pass. Artifacts:
  `/tmp/xiuxian-r128-faults-first-20260915.xml` and
  `/tmp/xiuxian-r128-cancellation-fixed-20260915.xml`.
- **13 real process-kill/reload boundaries** pass: read snapshot, saved intent,
  entered enter/search/settle transports, known active/revealed rounds, retained
  settlement, a later-round request, finished worker and both sides of the
  accounting transaction. No reload process sends any HTTP. Artifact:
  `/tmp/xiuxian-r128-process-first-20260915.xml`.
- The first expanded associated run had **3390 passed, 1 failed**, with 307
  passing subtests. Its one failure was an old UI fixture that mocked an ID
  list without registering its identity; the fixture now supplies the real
  identity instead of bypassing ownership. Worker-mocked caller/projection
  fixtures explicitly stub the checkpoint writer; new native suites use the
  real writer, worker and SQLite. Controls/probe verification then passed
  **216 cases**, including paused main-loop recovery and signed selections.
- The initial final regression passed **13819 cases and 1275 subtests**. Code
  review then found the successor-read gap above. All **12 native reproducers**
  failed before repair and pass afterward, across public and command callers,
  HTTP/entry/player/checkpoint failures and initial daily-limit responses.
  Artifacts: `/tmp/xiuxian-r128-successor-native-repro-20260915.xml` and
  `/tmp/xiuxian-r128-successor-first-20260915.xml`.
- Added **20 final-review cases**, including literal read-checkpoint
  acknowledgement, SQLite failure/reload followed by a successful new round,
  and refusal of unjournaled game facts. The 182 new R128 cases now pass in
  focused verification (**703 cases and 5 subtests**, 23.83s), artifact
  `/tmp/xiuxian-r128-focused-reviewed-20260915.xml`.
- Final associated verification passes **3417 cases and 307 subtests** in
  104.72s; final full verification passes **13839 cases and 1275 subtests** in
  382.07s. Artifacts:
  `/tmp/xiuxian-r128-related-reviewed-20260915.xml` and
  `/tmp/xiuxian-r128-full-reviewed-20260915.xml`.
- All three final XML reports were parsed: zero failures/errors/skips, and
  identical sets of all **182 new cases**. All test sessions have completed.
  Tests ran with `XIUXIAN_ALLOW_LIVE_TEST_DB=0` in isolated network namespaces;
  only loopback was enabled for local HTTP smoke tests.
- Configured `ruff check model tests`, full `F` checks on the treasure workers,
  runtime, accounting/checkpoint helpers and associated changed tests,
  `compileall`, `pip check`, tracked diff whitespace and selected new-file
  whitespace checks pass. Removed four unused imports found by the expanded
  `F` check. The inherited quiz bank and UI-key tool hashes and candidate HEAD
  remain unchanged. This is offline evidence, not production acceptance.
