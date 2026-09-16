# R81 Concubine Status Contract

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

This is an offline component repair, not whole-project completion, deployment
approval, or evidence that production subordinate identities resumed cultivation.
Inherited candidate changes are preserved. Production, live configuration,
services, game traffic, skills and repository remotes were not changed.

## Findings And Evidence

The initial 125 regression cases produced 118 failures and 7 passes:

- A partner header could fabricate zero affinity, ready cooldowns, an empty
  oath and an empty divination chain. The reducer certified freshness and
  released pending work without the required observations.
- Passive status processing could reset active gift/dream/voyage phases and
  erase their anchors. The old special case preserved only part of a heart
  operation while still changing its observations.
- A MiniApp refresh updated the snapshot clock while retaining an older
  Telegram message ID, making that old message appear newly reusable.
- Gift-status completion was not saved before the awaited inventory followup.
  Cancellation could therefore leave only the earlier pending state durable.
- Unknown/negative/contradictory cooldown strings were treated as ready, and
  duplicate fields or multiple partner headers were accepted.

First full regression: 8739 tests and 1275 subtests passed in 144.49s.
Final review then added 21 boundary cases, with 12 failures and 9 passes:
malformed Markdown status fell through to fragment rewards, invalid gift-query
clocks raised before validation, corrupt cached clocks raised during lookup,
and excessive numeric values exceeded persistence/calendar bounds.

Reproducer artifacts:

- /tmp/xiuxian-r81-concubine-before-20260911.xml
- /tmp/xiuxian-r81-concubine-final-review-before-20260911.xml

## Parser And Reducer Rules

1. A complete positive panel requires one nonempty partner header and explicit
   dream, divination and heart cooldowns. Dao-heart partners also require an
   explicit affinity observation. Red-dust panels legitimately omit affinity.
2. Missing oath, divination chain, fragments or voyage data is not a zero or
   an empty observation. For the same partner, omitted values are preserved.
   Changing partners clears omitted partner-specific values to their existing
   unknown/default sentinels instead of inheriting the previous partner's data.
3. Explicit zero affinity and an explicit absent divination chain remain valid
   observations. Declared but invalid fields, duplicates and contradictory
   headers reject the panel as a whole.
4. Cooldown grammar distinguishes exact ready tokens from whole-number
   durations. The existing minute-rounding margin and protection of future
   locally established cooldowns are retained.
5. Declared fragment/voyage fields have their own status grammar. Fragment
   counts cannot exceed totals; integers must fit SQLite's signed integer
   range, and timestamps must fit the local calendar. Unknown voyage text
   cannot borrow a prior cached voyage value to certify a complete panel.
6. Partial/invalid/stale panels do not update business state, certify freshness,
   clear timeout errors, clear pending work or launch gift followups. Native
   reads can still receive a complete later edit; ordinary read-timeout policy
   remains responsible for eventual rechecking.
7. Native status, passive status and MiniApp status share the same reducer
   admission checks. Active mutations and unresolved mutation anchors/tasks
   prevent status application. Only the native status handler can opt into
   completing an already-matched status/gift-status phase.
8. Rejected status-family text cannot fall through to generic fragment or
   voyage event reduction. Already-routed status replies are not applied again
   by the passive path.

These rules do not turn a panel into proof that an uncertain resource-consuming
command completed. Native command ownership remains a separate acceptance gate.

## Clocks, Anchors And Persistence

The main application and passive entry pass Telegram server message/edit time
and chat identity into status handling. A missing/invalid clock at those entry
points is not replaced by the receipt clock. A delayed valid panel derives its
cooldowns and freshness from observation time, not processing time.

The reducer also rejects observations older than the current snapshot.
Negative no-partner status retains that chronology watermark. Direct helpers
and the legacy log-recovery adapter still use their explicit timestamp argument;
this does not establish server provenance for legacy log timestamps.

A MiniApp status has no Telegram reply anchor. A successful HTTP snapshot
clears the prior panel message/chat anchor; native/passive callers attach their
own message/chat only after the panel was accepted. Future or invalid cached
panel clocks are not reusable, and incomplete logged panels are not reusable
heart templates. Existing generic logged heart-template selection policy is
otherwise unchanged; this repair does not establish ownership of those logs.

Accepted native status completion is saved before gift inventory continuation
or notification. This is not a durable dispatch journal and does not solve
shared save-failure/crash durability under R07.

## Verification

- New R81 cases: 173.
- Final focused suite: 745 passed, 54 subtests, 3.89s.
- Final focused artifact:
  /tmp/xiuxian-r81-concubine-final-focused-20260911.xml.
- Initial full artifact:
  /tmp/xiuxian-r81-concubine-full-20260911.xml.
- Final isolated full suite: 8760 passed, 1275 subtests, 144.20s.
- Final full artifact:
  /tmp/xiuxian-r81-concubine-final-full-20260911.xml.
- Configured Ruff and E9/F63/F7/F82 checks over model/tests pass.
- compileall, pip check and git diff --check pass.
- Tests use the dedicated candidate venv, temporary test data and an isolated
  network namespace. Full-suite loopback is enabled only inside that namespace.
  All test sessions completed; no source change followed the final full run.

Existing success fixtures now include the required cooldown observations.
The former active-heart snapshot test now asserts that the entire active state
is preserved, instead of allowing an unrelated panel to change its fields.

## Remaining Acceptance Gates

- Native status/gift-status dispatch ownership was not covered by R81.
  R82-concubine-query-lifecycle-20260911.md now records its scoped followup:
  early/late receipts, owner/control changes, unknown reads and restart.
- Greet, gift, dream, puzzle, divination, heart and voyage operations still need
  complete unknown-send, retry, restart and exact-completion ownership review.
  restore_concubine_runtime and the generic timeout paths clear legacy pending
  state; a read parser repair does not make that safe for uncertain spending.
- Legacy pending replay still relies on scalar roots, topic/local-log evidence
  and bounded latest-text searches. Establish account/chat/operation and server
  clock provenance before using it as a general recovery guarantee.
- Other writers update concubine_last_snapshot_at. This component does not
  provide field-level resource chronology, durable result idempotence or
  reconciliation of competing same-second/post-terminal edits.
- R65/R74 migration/capacity, R07 durability, remaining gameplay lifecycles,
  production per-role validation and whole-project Final Review remain open.
- World Boss and incense refinement stay disabled; inventory API is explicit
  UI-only; CommandAttempt stays shadow-only. Deep retreat does not block or
  consume Tianxing effects.
