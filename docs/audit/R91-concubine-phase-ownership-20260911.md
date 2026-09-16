# R91 Concubine Phase Cleanup Ownership

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production files, services, settings, database,
live requests, listeners, skill changes, commits, pushes or deployments.
Existing World Boss/refinement, explicit inventory API, CommandAttempt and
Tianxing/retreat boundaries are unchanged. Whole-project Final Review is open.

## Finding And Reproduction

The status-query, affinity and fragment helpers repeated R90's zero-anchor
cleanup defect. They treated zero (including false) as an owned message anchor
even when the plan changed after dispatch. Native completion, a definitely
unsent queued command and read expiry could consequently clear the replacement
phase. Exact pending cleanup and retained timers alone did not prevent this.

The corrected test harness reproduces 26 failures with 16 controls passing:
/tmp/xiuxian-r91-phase-reproduced-final-20260911.xml.
The earlier harness artifact includes three invalid gift-receipt timestamps;
those were fixed in tests before the production implementation was changed.

## Candidate Repair

Consolidate phase release in _release_owned_concubine_phase. It changes only
the expected phase and compatibility message key. A positive exact integer
anchor may release that phase; a zero integer anchor additionally requires
the plan captured before completion/expiry to be unchanged. Boolean anchors,
different message IDs and zero anchors belonging to replacement plans remain
untouched. Gift amount cleanup also requires successful owned phase release.

Query, affinity, fragment and voyage wrappers use this one rule. All unsent,
expiry and result call sites pass the pre-transition plan comparison explicitly.
No schema, transport, retry, cooldown, switch or resource-accounting policy was
added. Confirmed facts and exact old pending cleanup still complete even when
a newer plan must remain intact. Legacy ambiguous phases are not guessed idle.

## Verification

- Added 53 cases through native routing and real runtime receipt registration.
  Covers status, gift status, fragments, voyage status, gift inventory, gift,
  greeting, dream, puzzle, voyage start and voyage return.
- Negative cases preserve replacement phase/anchor/timer and reject duplicate
  completion without another send. Positive cases prove exact old anchors can
  close without overwriting newer snapshots or scheduling. Unsent dispatch
  and read-only expiry have separate regression cases.
- Focused lifecycle suite: 1593 passed, 11.53s.
  /tmp/xiuxian-r91-focused-final-20260911.xml.
- Final isolated full: 10169 passed, 1275 subtests, 154.29s, exit code 0.
  /tmp/xiuxian-r91-full-final-20260911.xml.
- Configured/fatal Ruff, compileall, pip check and diff checks pass. Tests use
  temporary SQLite and XIUXIAN_ALLOW_LIVE_TEST_DB=0. The full suite runs with
  only loopback available in an isolated network namespace.
- All test sessions completed. Only audit documentation changed after the
  full checkpoint; inherited quiz-bank and UI-helper changes remain intact.

## Remaining Work

Divination, heart and reacquisition still need their complete lifecycle review.
Legacy phase reconciliation, shared forced-stop durability, accounting/capacity,
deployment/rollback and the whole-project Final Review remain open. Preserving
an unowned replacement phase is not proof it has independently reconciled.
Production jfdffdddd subordinate-role MiniApp cultivation remains unverified.
