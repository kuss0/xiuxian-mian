# Yinluo Daily Supply Priority

Base: `e9e012b7`. Status: validated candidate; not deployed.

## Evidence

At 2026-10-06 02:59 CST, identity 8613500668 has Yinluo and daily
sacrifice enabled, last_daily_sacrifice_day=2026-09-18 and an expired
next_daily_sacrifice_time. The native banner observed on October 5 has
sha=440, exhausted slot 1, no pending operation or financial hold.
Cultivation accounting has no baseline; the displayed 11 is not spendable
evidence. The scheduler repeatedly selects soothe, records the missing
cultivation baseline, and moves auto_next_time one hour ahead. Due sacrifice
allows re-entry during that wait but loses to soothe again.

## Change

Move the existing due-sacrifice selection immediately before soothe, keeping
the banner-hint prerequisite. Remove its now-unreachable lower selection.
Unknown balances still cannot fund soothe/conversion. No new send, fallback,
state schema, configuration, timer, accounting projection or notification API.
The existing action executor continues to own dispatch and receipt handling.

Unchanged higher-priority gates: module/identity eligibility, accounting holds,
pending operations, sha calibration and explicit recovery, stale/missing banner,
pending settlements, ready collection and due-slot calibration. Daily toggle,
day and cooldown checks, phaseful settlement guard and capacity remain intact.

The patch fixes daily-supply starvation only. It does not make cultivation
available or enable any other resource-consuming action. Baseline acquisition
is a separate follow-up requiring native evidence, not a copied UI value.

## Validation

- Before the patch, the new regression fails for missing, insufficient and
  sufficient cultivation: first two send nothing; the last sends soothe.
- Focused scheduler, accounting runtime, send lifecycle and UI: 250 passed,
  11 subtests. Missing-banner, stale/calibration, ready collection, legacy
  pending, phaseful guard, daily toggle/day/cooldown and pending no-repeat
  are covered. Sending daily does not debit or invent cultivation.
- Test fixtures clear preceding subcase observations before seeding native
  resources; an initial fixture-isolation failure was corrected, not hidden
  by changing the runtime. The early full run was interrupted and is not
  acceptance evidence.
- Frozen full suite: 15924 passed, 1449 subtests, 457.15s;
  `/tmp/xiuxian-yinluo-daily-priority-20261006.xml`.
- Separate maintainer review: 1428 passed, 41 subtests, 178.48s. Re-read the
  real dispatch guard, financial admission, pending receipt retention, daily
  result reducer and priority branches. No independent external audit implied.
- Ruff, compileall and diff checks pass. Runtime change is four lines added
  and four removed, with no shared transport or persistence changes.

## Release And Observation

Only promote after full validation and a separate maintainer review pass.
Use the main repository fast-forward, preserve runtime quiz learning, and
back up stopped-state SQLite databases. Code rollback must not roll back game
state. Do not change identity controls, mutate production timers, force an
action, or replay held notifications for acceptance.

Watch the normal scheduler for one daily sacrifice, a strictly owned official
reply, updated daily/CD state and no repeated send. Continue natural fishing
acceptance around 03:18 (jfdffdddd) and 04:18 (WA). The fate 5-to-0 discrepancy,
historical unknown notification delivery and shutdown-drain work stay open.
