# Heart Admission And Fishing Diagnostics

## Scope And Evidence

This bounded continuation addresses two reproduced defects, not all C1-C6
acceptance packages. No gameplay switches, ownership settings, cooldowns or
retained unknown outcomes are cleared. CommandAttempt remains shadow-only.

Production at `fb62a0b3` repeatedly reported a heart safety-lock block for
jfdffdddd, WalterWA2000 and xianxia9527. September 16 12:15-12:49 UTC+8 logs
show the repeated attempts. Read-only SQLite inspection found first steps
marked `unsent`, `msg_id=0`, and guard sessions with `attempt=0` and no send
timestamps. These were not known sent commands awaiting a lost result.

The native heart caller persists its operation and projects `heart_pending`
before entering the transport. The shared guard interpreted that provisional
phase as an older in-flight trial and blocked the same caller. Existing tests
mocked the transport, so they did not expose the integration defect.

## Heart Repair

The runtime now passes its existing source, operation, chain, account and target
chat into guard admission. Only the heart-launch phase check can distinguish
its own currently executing dispatch. Acceptance requires all of:

- A valid single-step active heart record with a `sending`, zero-message first
  step and no reconciliation probe.
- Exact source, operation, chain, account and chat binding, with the operation
  still present in the current process's heart `_INFLIGHT` registry.
- Unchanged projected fields and plan; enabled owner/module, no conflicting
  work, no external recalibration requirement and the existing global gap.
- No existing guard send evidence; remote cooldown and recent-close protection
  are checked first and remain effective.

This is not a generic priority bypass or an unknown-send retry mechanism.
Legacy phases, restored/detached dispatches, unknown steps, stale callers and
ordinary callers without exact intent retain the old guard behavior. No shared
R07 recovery controller or Gate 4 control is introduced. Durable heart intent
is still saved before transport and the existing operation-check is retained.

The new native caller/runtime admission reproducer failed before the repair.
It now runs the first send, three choices and terminal settlement through real
guard admission with synthetic transport only. Thirteen negative cases cover
wrong source/operation/chain/account/chat, absent intent, detached or unknown
work, replaced projection, disabled module, cooldown, prior send and recent
close. This is offline protocol evidence, not a live-game completion claim.

## Fishing Repair

The channel audit found `externalApps.available=false` was unconditionally
reported as `rod_missing`. Availability alone does not establish inventory.
That branch now reports `entry_unavailable` and an unavailable-entry message.
Explicit `no_rod` responses retain their existing missing-rod classification.
Both paths retain their original skip behavior, daily counters and next-day
scheduling; no inventory reads, purchases or extra gameplay probes are added.

## Verification

- Focused: 687 passed, 320 subtests, 11.35 seconds.
- Configured Ruff and whitespace checks passed.
- Full isolated regression: 14855 passed, 1382 subtests, 414.92 seconds.
  JUnit reports are retained under `/root/xiuxian-release-20260916` as
  `xiuxian-heart-fishing-focused-20260916.xml` and
  `xiuxian-heart-fishing-full-20260916.xml`.

Heart repair commit `1c8aa7e3` and fishing diagnostic commit `2a794b2b` are
deployed and pushed to `xiuxian-mian`. The service was gracefully stopped;
inactive/PID 0 was verified before SQLite backup
`/root/xiuxian-release-20260916/pre-heart-fishing-20260916.db` (quick_check OK),
fast-forward and one explicit restart at 13:03:51 UTC+8. Observer and watchdog
were not restarted. No manual database edits or timer accelerations occurred.
The unchanged jfdffdddd next scheduling time was 13:08:55. Natural scheduling
selected it at 13:08:56. The real `.共历心劫` send was logged at 13:09:18,
message 1125592 in chat -1002083016447. Three `.稳` sends followed as messages
1125594, 1125596 and 1125598, all replying to prompt 1125593. The final edit
at 13:10:09 confirmed **cultivation +839, affinity +7, heart demon -5 (now 0)**.
The owned record is `complete/settlement`, all four steps `answered`, phase
`idle`, guard absent and no pending work for the role. Its retained heart
deadline is September 17 01:10:14 UTC+8; next scheduler time is 01:18:01.
This closes the reproduced self-block with a full natural live chain.
WA and xianxia9527 retain their existing later windows and are not claimed
live-validated by this result.

Post-deploy configuration matches the quiesced backup, including module flags,
identity enablement and tower windows; UI HTTP 200 and SQLite quick_check OK.
All three services are active with NRestarts=0. Observer recorded the expected
startup pending-cleanup message as a warning. A single Telegram server-side
`GetChannelDifferenceRequest` 500 occurred on the WA client at 13:08:51;
the heart chain nevertheless completed, and no restart or retry was forced.

## Remaining Debt

- R67: 19 selected channel retreats have live start evidence; later natural
  settlement remains an observation item, not another reason to force-exit.
- Tower: scheduling and one real completion are verified; the other enabled
  roles await their configured windows.
- External trial/stargazer receipts: preserve actual protocol ownership facts;
  absence of response identity is not proof of a wrong selected player.
- Spirit Tree: retained `turnstile_failed` remains blocked, not completed.
- Hehuan cleanup: wisemole command 1125435 in chat -1002083016447 has a
  successful final edit 1125436 at 12:34:27 and a success/CD projection, but
  the generic pending row remained overdue at 12:56. Preserve the evidence
  for a separate ownership-safe cleanup repair; no gameplay retry is needed.
  Existing startup recovery removed that overdue row at 13:04:21 without a
  resend. This does not fix the runtime's terminal pending cleanup omission.
- C1-C6 residuals remain in the master ledger. Historical unknown outcomes,
  retention, missing real-message/remote-CI evidence and unapproved recovery
  control must not be marked resolved by this narrow change.

World Boss and incense refinement remain disabled. All 19 channel identities
retain their group-send freeze; public MiniApp eligibility is unchanged.
