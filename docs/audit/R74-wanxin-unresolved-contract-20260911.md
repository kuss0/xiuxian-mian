# R74 Wanxin Unresolved Operations

Status: offline candidate contract; not permission to deploy or change live state.

## Required Behavior

- One active send at a time still uses the existing serial transport queue.
- After the reply deadline, replay available native evidence before changing
  scheduling. Lack of evidence is not success, failure, cooldown, or permission
  to repeat a mutation.
- A fully account-bound unresolved operation moves to a fixed slot keyed by
  its action. There is at most one such slot per declared action. Moving it
  does not release shared pending work, guards, or a Yinluo resource reservation.
- Unknown commission operations block the whole publish/accept/identify/
  banner/strip/cancel chain. Unknown affinity-spending operations block both
  affinity-spending actions. Other unresolved actions block themselves.
- Independent configured actions can proceed serially, without overwriting
  unresolved intent. UI/status must show the held actions even when there is
  no active send waiting for a reply.
- Exact native results can resolve either the active slot or an unresolved
  slot. Completing one cannot clear another action's pending work. Disable
  stops new sends but not read-only replay of existing operation results.
- Recovery reads one bounded local batch per pass and is independently paced.
  Each operation is checked against its original owner/account/chat/command.
  Nothing requests game history or queries inventory as a side effect.

## Persistence And Migration

`pending` remains the active operation. `unresolved_actions` adds fixed action
slots inside the existing observation JSON. `recovery_next_time` schedules
local replay independently of new-action timing. Existing pending records are
preserved; incomplete legacy ownership is not filled in from today's account.
Unattributable or corrupt records hold dispatch until explicitly reconciled.

New sends persist intent before transport admission. No timeout, day rollover,
codec roundtrip, slot pressure, or switch change discards uncertain effects.
Financial assistance remains under R65; its reservation cannot retire merely
because the Wanxin operation moved out of `pending`. No CommandAttempt control
or R07 shared-transport redesign is introduced.

## Acceptance Evidence

Tests must cover independent progress after an unresolved action, no duplicate
mutation, late replies while a different action is pending, no-ID ownership,
disabled cleanup, resource-reservation preservation, JSON/SQLite reload,
malformed and legacy records, and operator-visible unresolved state.

Missing native outcomes cannot be invented. Per-action isolation is not a
claim that every old unknown result is automatically recoverable. Native
history retention, legacy reconciliation, post-consumption corrections and
forced-stop guarantees remain separate acceptance work.

## Candidate Review Evidence

- Implemented fixed action slots and independently paced bounded local replay.
  An unknown visit no longer blocks an independent protection action; the
  original operation, shared pending and financial reservation remain owned.
  Commission actions still share an unresolved dependency, and affinity-
  spending actions exclude each other while either outcome is unknown.
- Replies project into detached observations and commit the exact operation,
  affinity/module updates and shared-root cleanup together. SQL failure restores
  the previous fields. Duplicate replies close only their matching slot, never
  an unrelated active action. Financial mirrors persist before transport; a
  failed mirror save prevents the send and marks the prepared reservation unsent.
- Follow-up reproducers found malformed action/status containers raising during
  normalization, active corruption being coerced into apparently usable state,
  different operation UUIDs sharing a native root, and the provider's legacy
  check raising on a malformed owner action. Corruption is now retained and
  visible while dispatch and business projection remain blocked. Message-root
  collisions are chat-scoped; distinct chats do not collide by message number.
- A completed financial result originally could not remove a restored owner
  slot after commission consumption. Exact provider operation/beneficiary
  evidence now permits only that cleanup, including after SQLite reload and
  cold archival. Neither resource deltas nor a replacement commission are
  replayed. A conflicting account, operation, commission or message binding is
  left unresolved. Cold duplicate handling does not reload history into the hot
  book or rewrite the archive.
- A changed helper selection previously hid the old helper's exact pending
  identification reply. Recovery now follows the original actor/account binding.
  A username-only change of the same helper uses the existing alias history
  instead of declaring its accepted commission unaccepted and accepting again.
  This is not general legacy commission/account migration acceptance.
- Focused resource/Wanxin/UI verification passed 488 tests and 13 subtests;
  the subsequent helper-change subset passed 351 tests and 13 subtests.
  Final integrated result: **7857 passed, 1275 subtests**, 140.25 seconds,
  exit code 0; `/tmp/xiuxian-r74-final-review-full-20260911.xml`.
  Broad Ruff, `compileall`, JS syntax, dependency and whitespace checks passed.

Remaining gates: unowned legacy operations, persisted `sending` work after a
forced process stop, native evidence beyond retention, same-point contradictory
results, corrections after consumption and affinity chronology across other
writers. The component tests do not close R65/R07 or whole-project Final Review.
No production state, switch, code, service, listener or skill was changed; no
game request, deployment, commit or push was made.
