# R104 Owned Affinity Reconciliation

Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline review/rebuild/test only. No production, live requests, data/config
writes, services, listeners, skills, commits, pushes or deployment.
World Boss/refinement stay disabled; inventory API stays UI-only;
CommandAttempt stays shadow-only; deep retreat never affects Tianxing.
R67 production subordinate-role cultivation remains unvalidated.

## Review

Owned greeting/gift completion records can retain a confirmed gain without
projecting it when another writer changes the original affinity baseline.
Review whether downstream policy still treats that old scalar as spendable,
and whether an earlier native query can overwrite an already applied result.
The current writer declines arithmetic when the baseline changed; that alone
does not establish that the remaining balance is authoritative.

The corrected 20-case reproducer produced 14 failures and six passing later-read
controls. With a 270 baseline, a separate Wanxin +9 leaves 279 after the owned
30-point greeting is confirmed but not projected. The due scheduler sends an
inventory query for another gift instead of calibrating the real 309 balance.
A concurrent seal exposes the same problem at 246 versus the real 276. Older
native/passive queries can also replace an applied 300 balance with 270.

## Contract Before Implementation

- Reuse validated bounded greeting/gift completion records. Never guess the
  missing arithmetic from a changed scalar, recreate a mutation, clear its
  daily fact or use CommandAttempt as a recovery controller.
- A current-owner/current-partner successful gain that was not projected and
  is not covered by a later absolute read requires status calibration. It
  cannot supply the cached affinity for another spend. Unrelated owners,
  replacement partners and non-affinity outcomes do not manufacture a hold.
- A known successful owned affinity result, including an applied result,
  rejects an older native/MiniApp status read. Preserve the original native
  query clock. These records do not retain reply edit provenance, so an equal
  second alone cannot prove inclusion; require a later read instead of
  inventing a Telegram point from a local send clock.
- Apply the same boundary to native moon-status absolute panels. Their
  completion remains independent of projection. A validated handled moon
  panel whose original command is strictly later than the owned gain may
  reconcile it; an old read's later edit, future/malformed receipt or mutation
  result is not that authority.
- Keep read admission separate from spending admission. Existing owned status
  queries and their timeouts/rate policy provide calibration. Do not add a new
  retry loop, timer override, durable flag, ledger or schema.
- Preserve terminal completion, cooldown/day facts and exact pending cleanup.
  Failed saves leave the original state replayable. Paused/disabled modules,
  replacement schedules and current MiniApp/command controls remain intact.

## Acceptance

Use actual Wanxin and greeting/gift handlers, native/passive/MiniApp status
readers and scheduler/admission paths. Cover changed balances, old and later
reads, equal-second ambiguity, owner/account/partner changes, false/throwing
saves, SQLite reload, duplicate replay and paused controls.

## Implementation And Final Review

- The existing affinity engine derives calibration need from validated,
  current-account greeting/gift successes, their applied flag, partner and
  current absolute-read coverage. It does not change the stored amount or
  mark an unprojected gain as applied. Known invalid operation state remains a
  hold under the existing record validator.
- Partner-dependent spending and Wanxin moon-seal admission reject an
  uncalibrated balance. Read admission stays independent, so the existing due
  scheduler can issue its ordinary owned status query. No new timer, retry
  loop, record field, controller, schema or ledger is introduced. Replacement
  schedules and disabled/global controls remain intact.
- Concubine native/MiniApp panels use the original query/request clock against
  retained completed gains. Missing reply-edit metadata means equal seconds
  are not proof of inclusion. Stale panels cannot overwrite a confirmed gain;
  later absolute reads can calibrate without changing its completion record.
- A follow-up reproducer found the sibling moon-status path still overwrote
  owned gains and failed to release calibration after a newer absolute read.
  Its old read now retains completion/CD without affinity projection. Only a
  validated handled absolute moon panel with a strictly later original command
  and nonfuture reply can reconcile the owned gain. Edited old reads,
  mutations, malformed receipts and unhandled panels cannot grant that use.
- Greeting/gift completion and daily facts remain separate from projection;
  gift inventory is debited once even when affinity needs calibration. False,
  exception and temporary-SQLite failures preserve the original receipt and
  rollback the panel. Reload and duplicate replay preserve both the hold and
  later reconciliation. Status output names the pending affinity calibration.

## Verification

- Corrected initial reproducer: 14 failed, six passed;
  /tmp/xiuxian-r104-reproducer-v2-20260913.xml. First fixed associated suite:
  436 passed in 3.90 seconds; /tmp/xiuxian-r104-first-fixed-20260913.xml.
- Extended greeting/gift/native/passive/MiniApp lifecycle suite: 48 passed;
  /tmp/xiuxian-r104-lifecycle-fixed-20260913.xml.
- Moon-panel follow-up: three failed, 49 passed;
  /tmp/xiuxian-r104-moon-reproducer-20260913.xml. Fixed associated subset:
  142 passed; /tmp/xiuxian-r104-moon-fixed-20260913.xml.
- Final source/lifecycle suite: 65 passed in 1.41 seconds;
  /tmp/xiuxian-r104-source-final-20260913.xml.
- Associated regression: 4091 passed, 202 subtests, 32.74 seconds;
  /tmp/xiuxian-r104-focused-final-20260913.xml.
- Full network-isolated regression: 11550 passed, 1275 subtests, 170.56 seconds;
  /tmp/xiuxian-r104-full-final-20260913.xml. JUnit records 12825 cases with
  zero failures/errors/skips. Configured Ruff, scoped F841, compileall, pip
  check and whitespace checks pass. All test sessions finished.

This scoped candidate contract is implemented and verified offline. It uses
currently retained evidence, not a full affinity history: lost/replaced legacy
receipts, every overlapping interval, other mutation engines and post-consumption
corrections are not certified. Wider legacy/interval/post-consumption accounting,
R65 capacity/migration, shared R07 durability, remaining games and whole-project
Final Review remain open. No production recovery or R67 subordinate-role
cultivation recovery is claimed. Production/data/configuration/services/listeners/
skills/commits/pushes remain unchanged.
