# Native Fishing Automatic Routing

## Status At 09:20 UTC+8

Candidate only, based on production `5823b1bc`. Production remains unchanged.
The preceding final-only proof deployment already completed one real catch:
see [checkpoint acceptance](native-fishing-checkpoint-canary-20261002.md).
This document does not claim all selected identities have fished.

## Changes

- Route an explicitly integrated fishing directory to the native worker.
  Preserve legacy unresolved-receipt checks, owner binding, both existing locks,
  shared HTTP budget, one-shot final submission and no fallback after dispatch.
- Existing public-auto selection authorizes native work independently of the
  standalone module switch. Removing that selection stops the active operation;
  confirmed gains can still be accounted without scheduling another rod.
- Missing rod, missing companion and exhausted daily quota are identity-local
  terminal outcomes, not global errors. Save the next-day timer before marking
  the identity done. Failed saves and cancelled/stale owners cannot close a day.
  Check eligibility before choosing bait or planning purchases.
- A recovered old rod can refresh today's authoritative quota and bag without
  adding yesterday's fish, rewards or rod to today's summary. Use the durable
  cast-intent date for summary attribution; do not rewrite historical live rows.
- Clear dated skip markers on rollover. A terminal skip can trigger the existing
  aggregate completion check, but cannot invent a catch or reward.
- Persist `concubine_voyage_settled_at` only from a confirmed return result.
  A return grants at most 15 minutes for already-enabled, due fishing before
  relaunch. Existing no-rod/quota holds, disabled fishing, absent entry and old
  unresolved receipts do not hold voyage indefinitely. Return/status remain
  allowed. Live fishing locks and a recent native rod's bounded settlement
  window prevent a simultaneous launch. Duplicate/late replies, ordinary status
  reads and restart do not extend the return window.

## Live Read-Only Evidence

At 09:13, identity `3504367852`, owned by account `301299112`, was selected as
player `-1003504367852`. Entry/start, selected start, details and fishing context
all returned HTTP 200. The directory declares fishing `integrated`.

The context explicitly reports `enabled=false`,
`unavailable=fishing_companion_missing`, `rod=null`, no active session and
quota `used=0 / remaining=5 / limit=5`. This is not an entry failure and does
not authorize bait purchases. The probe made no game mutation or DB write.
Private sanitized evidence:
`/root/xiuxian-native-fishing-channel-readonly-20261002-0911.json`.

The selected public cohort has 23 identities; standalone fishing is enabled
for three. Their real timers are in `identity_timers`, not obsolete duplicate
columns in `identity_runtime_state`. All 23 selected identities currently have
October 3 midnight timers. Do not reset those timers to manufacture acceptance.
Baji and WA have normal voyage returns due at 13:33 and 14:11 respectively;
because fishing is already deferred to tomorrow, this patch alone does not
claim they will fish at today's return.

## Validation

- Related suite: 4291 passed, 81 subtests passed, 55.04 seconds.
- Ruff and `git diff --check`: passed.
- Full isolated suite: 15409 passed, 1399 subtests passed, 436.28 seconds.
  Compile checks passed. Candidate is ready for a backed-up deployment;
  natural scheduler/return acceptance remains open.
- Production main/observer/watchdog remain active. Watchdog and defensive
  preflight find no pending tasks. Listener sidecar stays explicitly inactive.
- Current health warning is the retained earlier checkpoint failures, not
  new ordinary-loop failures. Log-bot callback network errors at 09:11 recovered
  at 09:13; deep retreat and tower confirmations continued.

## Remaining Acceptance

- Natural public-native scheduling through the saved timers, including one
  channel's no-companion/no-rod skip. No blanket standalone enabling.
- Natural companion return, bounded fishing window and subsequent voyage.
- Gift/open follow-up review: the older Lab contains a separate candidate;
  do not merge that worktree wholesale or re-enable old Telegram fishing.
- Confirm a clean new-day aggregate report. Already-written historical summary
  inflation is retained as evidence, not manually rewritten.

World Boss stays off; both incense-to-shenshi switches stay off. Channel group
sends remain frozen, inventory queries manual-only, CommandAttempt shadow-only.
