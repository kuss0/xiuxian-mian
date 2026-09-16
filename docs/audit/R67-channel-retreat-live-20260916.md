# R67 Channel Retreat Production Reconciliation

## Authorized Scope

The user selected residual item 3: validate the 19 channel identities' public
MiniApp cultivation. This is not authorization to unfreeze Telegram channel
sends, enable unrelated modules, force-exit retreats or restart broad audits.
All requests below use the service's normal public-entry scheduler, existing
per-identity/global locks, configured 20-second inter-action delay and shared
MiniApp rate limiter. No second Telegram client or unauthenticated UI route is
introduced. No direct production state/timer reset is performed.

## Findings And Fixes

The 19 identities were all in the frozen-but-restorable cohort, so public
MiniApp admission already allowed them. All had deep retreat enabled. Eighteen
records still came from the old unscoped parser, with no `identity_verified`
marker and next deadlines clustered at 14:03 on September 16. Those old timers
prevented checking the selected role even after the player-scoping fix shipped.

Commit `27c48d40` adds a bounded migration admission: a successful legacy cave
record without identity verification schedules a status read before trusting
its old timer. It never authorizes a mutation from the old record. The usual
backoff, global pause, switches, serial worker and ownership checks remain.
Typed, non-secret snapshot fields are retained for rejected-response diagnosis;
raw text, inventory and authentication are not persisted by this addition.

Natural live checks then exposed a separate protocol bug: the response combines
`actionResult.completed` (command processing completed) with
`dwelling.meditation.deepSeclusion.completed` (retreat completed). The extractor
incorrectly preferred the command acknowledgement, treating a valid idle panel
as `completed=true, canStart=true` and rejecting it as conflicting.

Commit `d864cacd` obtains retreat completion from the retreat panel. The older
direct action format remains supported only without a panel or command-center
command marker. An acknowledgement alone cannot grant settlement. Existing
identity ownership, real contradictory panels and unknown-operation guards
remain strict. Parser version 2 allows one status recheck of old false-conflict
records; unknown mutation evidence is retained until verified postconditions.

## Verification

- Initial rebaseline suites: 411 tests plus 68 subtests passed.
- Parser and fate-card integration: 505 tests plus 68 subtests passed.
- Broader retreat/background/Yuanying/fate/phaseful integration: 1037 tests plus
  92 subtests passed, 16.54 seconds.
- Full isolated regression: **14828 passed, 1376 subtests passed**, 413.66
  seconds, exit code 0. Artifact: `xiuxian-r67-full-20260916.xml` in the
  private evidence root below.
- Configured Ruff and whitespace checks passed. All tests use private temporary
  state and a separate network namespace.
- Private evidence root: `/root/xiuxian-release-20260916`. SQLite backups:
  `pre-r67-rebaseline.db` (online consistent SQLite backup during stop drain,
  not the last quiesced state) and `pre-r67-parser.db` (after full service stop).

The last code restart was 10:39:40 UTC+8. Selected HTTP captures show exact
negative channel `playerId` values on status/settle/start actions. At 10:40:27,
myios17's retained unknown settlement reconciled from its verified idle panel;
at 10:42:39 it started an actual new eight-hour retreat. gyurihero similarly
settled and restarted. No forced exit or blind retry was used.

## Other Cultivation Switches

Only xueuode5, growrdick, xuruode6 and imcanonical_ai had Yuanying enabled.
Today's existing captures show each `.元婴出窍` request carried its own channel
playerId. Current local timers were 12:59-13:16; those future natural outcomes
are not claimed as tested by the present retreat repair. The other 15 Yuanying
switches remain off, as configured.

## Per-Role Closeout

At **2026-09-16 10:59:35 UTC+8**, all **19/19** roles pass the independent
acceptance check: parser version 2, verified ownership, reducer handled,
`phase=running`, snapshot active, positive remaining duration and future
server deadline, no unknown outcome, plus a matching successful selected-role
start capture. All 19 deep-retreat switches remain enabled and all 19 group-send
switches remain disabled. Old `running` rows, HTTP 200 alone and source labels
were not counted as acceptance.

Times below are September 16 UTC+8. Start is the captured response time;
deadline is the game panel's end time, so subsecond rounding may differ.

| Role | Identity | Start | Deadline | Result |
| --- | --- | --- | --- | --- |
| xuruode8 | 3504367852 | 10:43:54 | 18:43:54 | PASS |
| zhengyuan0213 | 3581351795 | 10:43:28 | 18:43:28 | PASS |
| boxboxji | 3756719391 | 10:55:34 | 18:55:34 | PASS |
| xueuode5 | 3765328695 | 10:47:46 | 18:47:46 | PASS |
| dingfengbosushi | 3777092103 | 10:47:19 | 18:47:19 | PASS |
| growrdick | 3800619925 | 10:52:09 | 18:52:09 | PASS |
| xuruode6 | 3820064579 | 10:48:10 | 18:48:10 | PASS |
| tutuerduoxiao | 3823558636 | 10:46:53 | 18:46:53 | PASS |
| lalasin1 | 3852827410 | 10:56:52 | 18:56:52 | PASS |
| jihejish | 3870643893 | 10:43:03 | 18:43:03 | PASS |
| xuruode4 | 3872695780 | 10:51:34 | 18:51:34 | PASS |
| xuruode1 | 3888882303 | 10:53:01 | 18:53:01 | PASS |
| myios7 | 3922509228 | 10:56:01 | 18:56:00 | PASS |
| mudamuda0 | 3930920736 | 10:46:28 | 18:46:28 | PASS |
| iceeet1 | 3943539390 | 10:56:29 | 18:56:28 | PASS |
| myios17 | 3943773722 | 10:42:39 | 18:42:39 | PASS |
| fanb0x | 3947749189 | 10:52:35 | 18:52:35 | PASS |
| gyurihero | 3958882972 | 10:42:12 | 18:42:12 | PASS |
| imcanonical_ai | 4319360789 | 10:59:28 | 18:59:28 | PASS |

The post-restart cohort capture contains 42 deep actions: 18 status, 5 settle,
19 start; zero failed HTTP actions, zero player-ID mismatches and no force exit.
Each action carries `playerId=-1000000000000-identity_id`. The verified response
ownership and typed panel checks above provide business acceptance beyond HTTP
success. Peak captured cave requests were 9 per wall-clock minute; this is a
bounded cave-capture measurement, not a claim about all network traffic.

World Boss and small-world auto-refinement remain disabled for all 24 local
identities. Main service, health observer and safety watchdog are active; main
service has `NRestarts=0`. No new parser conflict or background-scheduler error
was observed in the post-restart check. No runtime restart is needed for this
documentation closeout.

Private evidence: `r67-final-20260916.json`, `r67_accept.py` and regression XML
under `/root/xiuxian-release-20260916`. The acceptance helper is read-only and
performs no game requests. R67's selected-role rebaseline/start defect is closed.
The new retreats' natural settlements at **18:42-18:59 UTC+8** remain future
observations, not completed tests. This does not close all MiniApp workflows,
the separate fate-card debt, the later Yuanying cycle, or the wider C1-C6 audit.
