# Native Fishing Automatic Routing

## Deployment At 09:31 UTC+8

Runtime commit `492d54aa` is deployed and pushed to `xiuxian-mian/main`.
It was based on `5823b1bc`; production was clean before the fast-forward.
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
  Compile checks passed. Natural scheduler/return acceptance remains open.
- Production main/observer/watchdog remain active. Watchdog and defensive
  preflight find no pending tasks. Listener sidecar stays explicitly inactive.
- Current health warning is the retained earlier checkpoint failures, not
  new ordinary-loop failures. Log-bot callback network errors at 09:11 recovered
  at 09:13; deep retreat and tower confirmations continued.

The service was gracefully stopped, PID 0 and inactive verified, then a private
SQLite backup was taken at
`/root/xiuxian-before-native-auto-20261002-0928.db` (`quick_check=ok`, mode 0600).
It restarted once at 09:31:06; main PID 125471, worker 125472, `NRestarts=0`.
Observer/watchdog were not restarted. Their brief service-down observations
at 09:30/09:31 are the planned deployment, not crashes.

Post-start comparison of all 24 identities confirms fishing timers, standalone
fishing switches, World Boss switches and incense-refinement switches unchanged.
Public fishing still selects 23 identities. The new return fact migrated with
zero defaults, not fabricated historical returns. At 09:31:38 deep retreat
started successfully and at 09:31:42 another tower challenge settled normally.
At 09:34 health reports no abnormal modules or pending tasks; the two earlier
checkpoint failures remain visible. No new cast was made during deployment.

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
