# Yinluo Mixed Sender Trust Hotfix

## Incident

After the stability release, identity scheduling repeatedly raised
`ValueError: Invalid Yinluo resource replay trust sets` from
`owned_yinluo_log_events`, called by `recover_yinluo_resources` when an unresolved
operation became due. The configured global game source list includes the
negative channel sender `-1003983937918`, including in the shipped defaults.
The new resource decoder requires positive bot user IDs. Passing the mixed
global list directly rejected the whole trust set, not only channel replies.
Native resource replies were also rejected by the same positive-ID contract.

Earlier lifecycle fixtures set the list to a single positive test bot. The
migration rehearsal checked retained state, not scheduler execution with the
real mixed source list. The initial short startup window did not exercise this
due recovery path. Those checks did not establish this integration contract.

## Fix And Boundaries

`yinluo_accounting.event_trust` projects the configured source list to strictly
positive integer bot IDs for Yinluo native replies and bounded local replay.
The global list remains unchanged so broadcast consumers retain their sources.
No absolute-value conversion, arbitrary bot fallback, relaxed ownership check,
exception suppression, pending deletion or resource retry is introduced.
An all-channel list produces an empty bot trust set: no resource result is
accepted, but replay can return without crashing the identity scheduler.

## Verification

New native regressions reproduce the pre-fix failure and cover:

- Shipped mixed source defaults plus an official test bot accepting its reply.
- Owned log replay remaining idempotent with mixed sources.
- Channel resource replies being rejected with both mixed and channel-only lists.
- A due unresolved operation traversing the real scheduler without crashing,
  resending or clearing its sent operation.

An isolated network-namespace run passed 698 tests across Yinluo accounting,
replay, facts, gameplay policy, legacy migration, read lifecycle and Wanxin
commission contracts. Configured Ruff and whitespace checks passed.
Report: `/tmp/xiuxian-yinluo-trust-20260916.xml` (also copied to the private
release directory). No live test database or manual gameplay request was used.

## Production Verification

Code commit `41a4aeef` was fast-forwarded to production main and pushed with
the rebuild branch. Before restart, the stopped-service database was backed up
to `/root/xiuxian-release-20260916/pre-yinluo-trust-hotfix.db` (0600).
Service started at 09:27:47 UTC+8 and worker initialization completed at
09:28:32. UI returned HTTP 200; service restart count remained zero.
The global source list, global enable, Yinluo, World Boss and small-world
refinement switches match the pre-hotfix snapshot.

At 09:28:56, the retained `.我的阴罗幡` operation (message 1123504) was complete,
as was a naturally scheduled `.每日献祭` (message 1123684). The retained
observation reported sha=600, max=350000, empty error and empty accounting hold.
This exercises real receipt/recovery and subsequent scheduling, not just a
healthy process. No manual command or database calibration was used. Existing
startup cleanup removed one expired transport pending row; the owned resource
operation and its evidence were retained and subsequently completed.

The post-restart observation found no new trust-set exceptions or tracebacks.
The health observer and soft watchdog remain active. Separately, startup public
entrance history collection timed out and `myios17` fate-cards again reported
`fate_read_failed`; neither is certified fixed by this scoped Yinluo patch.
