# Frozen Channel Tower And Fate Closeout

## Scope

The user selected items 1 and 2 from the September 16 audit of 19 channel
identities under account 301299112: tower scheduling and myios17 fate cards.
No other module is enabled, channel group-send freeze is preserved, and no
timer/database reset or inventory probe is used. Production remains on the
normal public-entry scheduler and shared MiniApp limiter.

## Tower

The ordinary scheduler skipped group-disabled roles before reaching the
HTTP-only tower worker. The worker itself already permitted frozen-but-public
eligible roles. Commit `5be671bd` adds a narrow frozen-role branch that invokes
only `run_tower_scheduler`. It checks the captured owner/account, public
eligibility, global enablement, online state, weakness and tower switch. The
existing daily window, completion marker, worker lock, cooldown and HTTP
operation guards remain unchanged. No ordinary group-command scheduler is
admitted through this branch.

At 12:02:58 UTC+8, myios7 was naturally queued. At **12:03:05** the real result
confirmed 19 floors cleared, failure on floor 20, **4440 cultivation and 47
tower seals**, plus material rewards. Its state is `completed/challenged`, and
its next schedule is September 17 11:30:21 UTC+8. It did not challenge again
across the subsequent controlled deployments.

All 18 enabled channel tower schedules advanced from stale August deadlines
into their configured windows. Most windows had passed today; mudamuda0 is
scheduled for September 16 15:04:54, the others for September 17. Those future
challenges are not yet live-validated. xueuode5 remains disabled. This closes
the scheduler omission, not a claim that 18 roles all challenged today.

## Fate Cards

The generic `fate_read_failed` hid a native-contract rejection. `5be671bd`
exposes a bounded parser error code; `c1888d53` distinguishes a missing quest
key from a missing start timestamp. Natural production observations were:

- 12:03:04: `fate_read_failed:quest_identity_missing`.
- 12:07:11: `fate_read_failed:quest_started_at_missing`.

The server's quest has a valid key but no `startedAt`. Local validation
incorrectly required that optional timestamp as part of quest identity.
Commit `6096470f` accepts an absent/null/empty timestamp under the existing
dated-card-record, choice and quest-key binding, requiring a metric when the
timestamp is absent. It does not fabricate a timestamp or substitute the
card creation time for the quest start. Non-string timestamps are rejected;
an already observed start timestamp cannot disappear or change unnoticed.

Unknown-prerequisite reconciliation still requires the same date, card record,
choice, quest key, metric and target, with completed/settle-ready server facts.
Optional timestamps must agree. A missing timestamp does not release an
unmet task, authorize a retry, or grant rewards for an uncertain prerequisite.

At **12:10:46 UTC+8**, myios17 settled successfully. The retained selected
dwelling/external requests carry `playerId=-1003943773722`. The fate run then
performed one start read, **one settle POST**, and one confirmation read. The
final quest is `accept_v1`, `cultivation_gain`, progress 30/30, `settled`.
There were no draw/interpret/choose, meditation or deep-retreat mutations in
this run. Actual newly confirmed reward: **2 Tianji traces**.

The log's 1160 cultivation is inherited cumulative state from earlier runs;
it is not a newly earned amount from this repair. All 19 channel identities
now have September 16 settled fate records. The existing combined daily
summary also reported 24/24 local identities complete.

## Verification And Deployment

- Initial focused suite: 283 passed, 30 subtests.
- Initial full isolated suite: 14832 passed, 1382 subtests, 416.76 seconds.
- Refined diagnostic suite: 294 passed, 75 subtests.
- Final focused suite: 370 passed, 75 subtests.
- Final full isolated suite: 14841 passed, 1382 subtests, 419.04 seconds.
- Configured Ruff and whitespace checks passed.

Each code deployment used graceful main-service stop, confirmed inactive/PID 0,
a private SQLite backup with successful quick_check, fast-forward merge and
explicit start. Three controlled starts were needed to expose the precise
native rejection before changing validation. Observer and watchdog were not
restarted. No old database snapshot was restored. Both main and the candidate
branch are pushed to `xiuxian-mian` (kuss0), not origin/wxjerry.

Backups and read-only audit evidence are in `/root/xiuxian-release-20260916`:
`pre-tower-fate-20260916.db`, `pre-fate-diagnostic-20260916.db`,
`pre-fate-start-time-fix-20260916.db`, `pre-tower-fate-switches.json` and the
adjacent channel MiniApp audit. All 24 identities' module enable flags,
group-send enable flags and tower windows compare equal to the pre-change
baseline. World Boss and incense-to-spiritual-awareness refinement remain off.

Final read-only verification at 12:17 UTC+8 confirmed 19/19 channel identities
still group-send frozen, 18 tower switches enabled and 19/19 fate records
settled for today. All configuration comparisons above remained identical.
Main service, health observer and safety watchdog were active with
`NRestarts=0`; the main service had remained up since 12:10:16. The bounded
post-deployment journal check found no background exception or traceback.
The final JUnit result is retained as
`/root/xiuxian-release-20260916/xiuxian-channel-fate-final-20260916.xml`.
This closeout is documentation-only and does not require another restart.

Remaining unrelated debt includes Spirit Tree Turnstile blocking and strict
per-action ownership evidence gaps for external MiniApps. They were not
changed or declared fixed by this work.
