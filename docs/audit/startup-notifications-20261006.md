# Compact Startup Notifications

Base: `c95ea62f`. Worktree: `/root/xiuxian-startup-notification-20261006`.
Status: `ba2ff926` fast-forwarded to production disk and pushed; the loaded
worker changes on next start. No service restart was performed for this patch.

## Evidence And Scope

The 05:36 production restart queued two routine rows: the UI bind address and
a startup message containing all 24 identities plus implementation details.
The saved startup text is 32 lines / 1075 characters, even though the final
summary formatter folds/clips it.
Ordinary startup needs the identity count and recovery state, not the roster.

- UI bind remains a local console message after successful binding; it no
  longer creates its own TG queue entry. Existing-server return and bind
  failures keep their original behavior.
- Bootstrap keeps a local roster, while its ordinary notice has two lines:
  system started; identity count, recovery result and login wait if needed.
  Timeout-scan and account-integrity diagnostics remain appended, unchanged.
- The neutral startup heading no longer contains the legacy `startup success`
  or `state recovery` low-priority match strings. Previously those strings
  overrode medium-priority account failures and module-closure warnings.
  Existing priority classification now sees the actual warning text; urgent
  markers such as a ban still remain high. No shared classifier change.
- Bootstrap admission, global pause, state loading, initialization, account
  checks, persistence and game scheduling are untouched. The tests mock all
  network paths and exercise the actual bootstrap function.

## Validation

First test attempt used an unavailable pytest async marker; converted to the
repo's asyncio.run convention, without installing dependencies. A partial
full run was intentionally stopped before acceptance to preserve the existing
not-logged-in notice and add its regression matrix. It is not a full pass.

Focused final login/recovery matrix plus persistence and shutdown: 67 passed;
an additional bind-failure regression is included in the subsequent full run.
Matrix covers first initialization, existing state without timers, restored
timers, logged in/out, normal state, closed modules, failed account and bans.
Normal messages stay below 100 UTF-16 units and two lines. Roster is local,
real diagnostics remain visible with low/medium/high priority as appropriate.
Final frozen full suite: **16017 passed, 1449 subtests**, 450.33s,
JUnit `/tmp/xiuxian-startup-notification-full-20261006.xml`.
Second review after the full suite: **205 passed, 17 subtests**. These are
separate maintainer passes, not independent external reviews. Ruff,
compilation and diff checks pass. No dependency or test plugin was installed.

Reviewed both complete call sites and paused/failed-load bootstrap returns.
Searched runtime/tools references to the changed wording: there is no readiness
or health parser depending on the old startup title. Service health and worker
tracking are not inferred from this notification. Existing global pause and
state-load safety tests still pass.

Frozen hashes:

```text
1da40ee517074061682c0668814b3fa50f3e681d236e4b772012b5b07d458ea1  model/app.py
3dbf1859110becdf8bccf31d5c8be3dc9e20b267a363197df9de3e11eaa7ac83  model/ui.py
87b763e3f89663f3799a302b22a13253606925a0e85808027c81c65ab447014a  tests/test_startup_notifications.py
```

## Activation Boundary

Only bootstrap and UI-server startup call sites change. After validation, the
patch may be fast-forwarded and pushed without restarting a healthy service:
production hot reload is off and these call sites next run on normal startup.
Do not call bootstrap or create another UI server for live acceptance. Do not
erase already-queued old startup notices. Record the disk revision separately
from the loaded worker revision until a later normal restart verifies it.

The separate trial child-result handoff, durable daily-report delivery and
old unknown summary remain open. This change does not close TG-03 or TG-06.

## Live Review During Tests

The running worker remains `2207227` with supervisor `2207210`, loaded from
`f7958deb`. Hot reload is disabled by default and the supervisor journal
confirms it. WA's natural Mulan support completed at 05:58:44, official edit
1283621 bound to command 1283620: +536 cultivation, +180 stones, tier-two
demon pill x2; phase cooldown, reply pointer zero and last_error empty.

At 06:03 pending game queue remains empty and no new runtime error/restart
was seen. Notification telemetry since October 5 00:00 has 255 confirmed
attempts and the one previously-held unknown attempt, not a new daily-report
incident. Existing summary rows will still contain the old startup wording
at the next flush; disk-only promotion cannot retroactively change them.

## Disk Promotion Checkpoint

Post-merge isolated regression: 68 passed. Supervisor 2207210, worker 2207227,
observer 2178717 and watchdog 2178662 are unchanged and active, NRestarts=0.
The worker still has `f7958deb` loaded; `ba2ff926` is the next-start code.
Production's only dirty file remains runtime quiz learning data. No gameplay
or summary database was modified as part of this disk-only promotion.

At 06:06:07 the already-queued old notices and natural results were delivered
in an ordinary digest: confirmed receipt `604c83183c6b4b148e5b80ac13aba7dd`,
405 UTF-16 units, seven lines, no mentions. This verifies continued delivery,
not the new startup copy. The next real worker startup remains its natural
acceptance gate; no restart or synthetic notification is justified solely
to manufacture that sample.
