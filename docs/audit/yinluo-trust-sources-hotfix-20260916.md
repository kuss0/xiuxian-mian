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

Post-deploy observation is recorded below after restarting the service.
