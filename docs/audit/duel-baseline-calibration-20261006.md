# Duel Missing-Baseline Calibration

## Evidence And Scope

Base: `92789ca8`. Lab: `/root/xiuxian-duel-baseline-calibration-20261006`.
Production worker still loads `f7958deb`; this candidate has not run live.

Lpprceqei (`7538826434`) is the only online identity with duel enabled at the
12:40 checkpoint. It has completed 5/10 configured fights against `@ccahen`,
but `xiuwei_accounting` is empty and no trusted cultivation observation clock
exists. The 12:40:39 scheduler only postpones to 13:10:39 with `no_baseline`.
Repeated postponement cannot create the missing baseline.

Today's native `.我的灵根` replies include an absolute cultivation balance in
the profile card. Example command/reply IDs: 1283706/1283709, 1283968/1283970,
1284465/1284467. Existing identity refresh already binds account, identity,
chat, operation and server-clock evidence. MiniApp display balances are not
promoted to spending baselines by this patch.

## Candidate

- Only an originally due, enabled attacker's `no_baseline` hold can request
  calibration. Eligible realm, execution window, configured remaining work
  and known daily mind exhaustion are checked first. Target-side calibration,
  corrupt balances and unverified profile balances remain out of scope.
- Reuse `refresh_identity_info(source="duel_baseline")` with the new optional
  `include_auxiliary=False`. The default UI behavior remains unchanged.
  YuanYing and second-soul auxiliary queries are omitted; existing bounded
  primary read retry and battle-power followup are unchanged.
- At most one fresh refresh operation per rolling 24 hours, based on existing
  persisted request timestamps. This is not a claim of one network send per
  day: an existing operation can perform its bounded read retry/followup.
- Active, sending, sent or unknown refresh operations are not replaced, even
  when old. Invalid records and malformed/non-finite/negative/future clocks
  cannot become permission to issue another request. An absent/default empty
  dictionary is distinguished from a malformed empty list/string/bool/null.
- Save the normal duel hold before requesting the read, then return. No
  immediate fight, timer reset, target cooldown bypass, reserve bypass,
  equipment preparation, Tianxing consumption or account-switch change.

## Audit And Validation

The initial audit and second review are separate passes by the same maintainer,
not an independent third-party audit. All test commands set
`XIUXIAN_ALLOW_LIVE_TEST_DB=0`; no production probe or fault injection was used.

Initial tests failed on the original implementation because the due hold did
not request calibration and the primary-only refresh option was absent.
Focused acceptance before the final malformed-record guard: 352 passed and
6 subtests. First full candidate: 16202 passed and 1461 subtests; this predates
the final work/window guards and must not be cited as final acceptance.

Second review found that false-y malformed refresh records could be treated as
absence. The guard now rejects them; supplementary wrong-group, wrong-sender,
account-rebind and no-Tianxing/no-equipment tests also pass. Focused regression:
274 passed and 6 subtests, covering duel, refresh lifecycle and resource
observations. Expanded regression: 361 passed and 6 subtests; supplementary
profile/accounting/manual-routing/daily-report review: 87 passed.
Ruff, py_compile and diff checks pass. The intermediate 16206-test run
predates the malformed-record guard and is not final acceptance.

Final frozen full suite: **16215 passed, 1461 subtests passed**, 446.79 seconds.
JUnit: `/tmp/xiuxian-duel-baseline-final-20261006.xml`. Code acceptance is
complete; do not mark the live issue closed before runtime loading and a
natural owned-response sample. Frozen source hashes:

```text
a8f4f63a0f9bb38ce73b52035e2bf83ece5be14a5185d6b6b68dac71cfcbadb5  model/control.py
aeb764bedba2dd81ce5fc2206cd9cc9493d565dac1f6cd1c6c657665bed3427f  model/features/duel.py
c9b56c7cc154f85b76c66b992a6dba631858070f993881a6b838778e4a7e7d25  tests/test_duel_baseline_calibration.py
```

The existing lifecycle suite additionally covers reply-before-receipt,
durable unknown/cancelled sends, ownership changes across await, bounded retry,
UI duplicate clicks and operation-scoped pending cleanup. The new caller adds
no alternate transport or reply handler.

## Deployment Boundary

No production restart, switch mutation, manual cultivation correction or duel
is part of this acceptance. Merge/staging and running-process loading must be
reported separately. After a normal validated maintenance load, observe the
original scheduled read and official reply, then the subsequent protected
duel decision. A successful profile refresh does not itself authorize a fight.

13:04 live preflight: game pending empty, watchdog OK; two historical held
notification batches still require review and are not replayed. Main,
observer and watchdog PIDs unchanged. WA rift remains due at 18:55:26 with
protection preparation to be checked around 18:45. Incense refining remains
off for WA and jfdffdddd; no gameplay settings were changed.

At 13:10:40 the unchanged production worker postponed the same identity to
13:40:40. The last profile-request timestamp remained June 28, the baseline
remained empty and no duel command was pending. This is fresh evidence of
the old liveness gap, not a failed acceptance of the un-loaded candidate.
