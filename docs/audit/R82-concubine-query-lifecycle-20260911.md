# R82 Concubine Query Lifecycle

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

This is an offline repair of native status and gift-preparation status reads.
It does not close the entire concubine module or the whole-project goal.
Production, live configuration/database, services, game traffic, remotes,
skills and the user-owned quiz/tool changes were not modified.

## Review Evidence

The initial query suite reproduced 38 failures. Both send entry points wrote
pending state only after awaiting transport. An early complete reply could be
overwritten by the delayed send receipt, replacement owners could receive old
writes, and startup/timeout cleanup erased uncertain query ownership. Starting
the gift status read also marked the daily gift attempt before a usable panel
existed.

Integration and final review additionally found:

- Latest incomplete log edits exposed older complete versions of the same
  message. Equal-time contradictory versions were not distinguished.
- Malformed query kinds could raise instead of retaining the unresolved work.
- Fractional pending keys could be truncated into a different command root.
- An account rebind could leave this read waiting indefinitely.
- A failed completion save left the gift-attempt day marked despite withholding
  the followup.
- An older automatic query's edited panel could pass through the manual-read
  compatibility path after a newer query completed.

The integration fixture now uses real tuple pending keys, account binding,
chat/dispatch/receipt times and isolated runtime dedupe registries. Existing
success fixtures and the application's fast-due query fixture were updated to
the tracked, non-retrying send contract. The fast-due test still requires its
90-second queue timeout; production validation was not weakened for a mock.

## Ownership And Dispatch

The bounded concubine_status_query JSON column stores one query per identity:
kind, operation ID, owner identity/account/chat, command, start and receipt
times, lifecycle status, module-plan fingerprint and completion evidence.
It is registered in the runtime fields, JSON codec, new schema and additive
migration schema. Temporary SQLite tests cover reload and subsequent completion.

Before transport, both entry points save the owned intent and pending phase.
The send explicitly supplies identity, chat and operation ID, tracks the
runtime receipt, uses max_retry=0 and the existing 900-second reply timeout.
The existing fast-scan queue-timeout override remains effective.

A synchronous operation_check rechecks owner object/account, global and
identity controls, module controls, the module plan and the phaseful-summary
window before dispatch. A per-identity in-flight token prevents query reentry.
After awaits, an obsolete owner or operation cannot write through a replacement.
An early terminal reply wins over a delayed receipt or exception. Delayed
receipt registration is cleaned only for that original operation.

An explicit, current unsent block permits a scheduled retry. An absent/stale
block, exception, cancellation or malformed receipt retains unknown state.
No unknown query is immediately retried or treated as completed business.
Failure to save the intent prevents dispatch.

## Reply And Recovery Contract

Native routing and passive routing share the owned-query handler. The native
route bypasses the generic consumed-message/pending cleanup for these owned
reads; that cleanup cannot remove a replacement operation at the same root.
An unresolved query also blocks cached/HTTP/passive snapshot shortcuts even
when an unrelated UI reset clears its scalar phase or message anchor.

Receipt adoption requires the original operation ID, source module, identity,
command and chat. Explicit account metadata must agree. Current runtime sent
logs and pending rows can omit account_id; the saved unique operation ID and
unchanged owner account supply the binding, not a guessed username or nearby
command. Real runtime receipt and real sent-log roundtrip tests cover this
case. Contradictory or multiple roots are not adopted.

Recovery searches the original and recent 20-minute log windows at most once
per minute. This bounds time ranges, not the physical bytes of a daily log
file. Only exact-root official-bot messages/edits with valid server clocks are
eligible. Per-message latest revisions supersede earlier text; incomplete or
equal-time conflicting revisions cannot reveal an older panel. Receipt time
does not substitute for the panel's server observation time.

A complete owned read closes once. Its panel applies only while the original
module plan and R81 snapshot admission remain valid. A superseded plan can
close its own read without changing the newer partner, timers or mutation
phase. Cleanup matches operation as well as identity/chat/root. A no-partner
panel does not delete sibling pending commands. Phaseful settlement text can
close the read without certifying a partner snapshot or starting a gift.

Both query kinds are reads: after exact recovery fails and the 900-second
reply window expires, they may expire and permit a later scheduled query.
After rebinding, an expired old-account read is retired without rewriting the
new account's schedule. No resource-consuming command inherits this policy.
Structured corrupt records remain held and are visible as a query-record
error; they do not fall through to the legacy startup reset.

Completion and exact cleanup save before gift inventory continuation. No
incomplete, unknown, expired or stale read consumes the daily gift-attempt
marker. At R82, a failed completion save withheld this callback's followup and
restored only that marker. R87-concubine-query-completion-20260911.md replaces
that partial rollback with checked whole-projection completion and replay.
Already-complete/expired roots cannot trigger followups
again. A later explicit manual status read remains usable, but older roots
in the same chat cannot masquerade as a new manual observation.

## Verification

- New R82 cases: 184.
- Final focused suite: 853 passed, 73 subtests, 6.21s.
- Focused artifact:
  /tmp/xiuxian-r82-concubine-query-verified-focused-20260911.xml.
- First isolated full run: 8942 passed, 1275 subtests, one outdated fast-due
  fixture failed. The fixture now has an account, a real-shaped receipt and
  explicit assertions for ownership, tracking, zero retries and queue timeout.
- Second isolated full run: 8944 passed, 1275 subtests, 146.38s; artifact:
  /tmp/xiuxian-r82-concubine-query-verified-full-20260911.xml.
- Configured Ruff, E9/F63/F7/F82 checks, compileall, pip check and diff checks
  pass. All test sessions completed; no runtime/test changes followed the
  final full run.
- Tests use the dedicated candidate venv and temporary test data. Full-suite
  execution has its own network namespace, with only its loopback enabled.

Reproducer artifacts include:

- /tmp/xiuxian-r82-concubine-query-before-20260911.xml
- /tmp/xiuxian-r82-concubine-query-integration-before-20260911.xml
- /tmp/xiuxian-r82-concubine-query-review-before-20260911.xml
- /tmp/xiuxian-r82-concubine-late-edit-before-20260911.xml

## Remaining Gates

- R86-concubine-fragment-lifecycle-20260911.md extends this bounded query
  engine with owned fragment reads and checked fragment/expiry saves.
  R87-concubine-query-completion-20260911.md completes the scoped owned
  status/gift-status save rollback, pre-dedupe replay and recovery-save repair.

- Follow-up: R83-concubine-gift-lifecycle-20260911.md covers owned gift
  inventory/spending. R83 moves the attempt marker to complete bag evidence;
  a status panel alone no longer consumes that marker. Its legacy migration
  and broader accounting gates remain open.
- R84-concubine-greet-lifecycle-20260911.md covers owned daily greetings,
  shared native/passive completion and retained unknown mutations. It does
  not replace the outstanding legacy-pending migration or remaining actions.
- At R82 closure, gift inventory, gift spending, greet, dream, puzzle,
  divination, heart and voyage sends retained legacy internal-await/unknown/
  restart behavior. See R83 for the scoped gift follow-up; the saved status
  completion is not a guarantee about remaining downstream effects.
- Legacy scalar-only queries and legacy log readers are not migrated into
  fabricated owned records. Legacy resource-consuming pending resets remain
  a separate repair, not permission to retry them by the read timeout.
- Field/resource-delta chronology, general manual-read provenance and
  post-terminal corrections still need module-wide acceptance. One retained
  terminal query is not a general history/accounting ledger.
- Shared forced-stop/save-failure durability and malformed raw JSON recovery
  remain under R07/persistence review. R87 repairs dirty in-memory owned
  query completion after failed saves, but does not repair legacy unowned
  completion or add an atomic recovery controller or take over CommandAttempt.
- Any deployment needs snapshot/schema/rollback review. Do not drop the query
  column or run old reset logic against new unresolved state as a shortcut.
- R65/R74 migration/capacity, remaining gameplay lifecycles and whole-project
  Final Review remain open. Production jfdffdddd subordinate-role cultivation
  remains unverified; these tests are not evidence of its live recovery.
