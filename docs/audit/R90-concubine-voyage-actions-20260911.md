# R90 Owned Voyage Launch And Settlement

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

Offline candidate only. No production file, service, database, configuration,
live gameplay request, listener, skill, commit, push or deployment operation.
World Boss/refinement disablement, explicit UI-only inventory API,
CommandAttempt shadow-only and Tianxing/retreat policy remain unchanged.
This checkpoint is not whole-project Final Review or production acceptance.

## Reproductions

- Voyage launch/return used untracked transport, scalar ownership, timeout
  retry and permissive mutation parsing. Early results could be overwritten,
  unknown spending retried and late results projected over newer snapshots.
  Initial reproduction: 27 failed, 4 passed.
  /tmp/xiuxian-r90-voyage-reproduced-20260911.xml.
- Parser review reproduced six failures for ambiguous/incomplete panels.
  /tmp/xiuxian-r90-review-before-20260911.xml.
- The outer phaseful-summary replay controller could directly resend both
  voyage return and dream commands, bypassing their persisted operation owner.
  Reproduction: 4 failed, 2 passed.
  /tmp/xiuxian-r90-shared-replay-reproduced-20260911.xml.
- Final boundary review reproduced seven failures: zero/boolean compatibility
  anchors let old completions or unsent receipts clear a replacement phase;
  completed JSON accepted false as a zero return timestamp via dict equality.
  /tmp/xiuxian-r90-boundaries-reproduced-20260911.xml.

## Implementation

Add concubine_voyage_actions, a bounded identity JSON column with at most
voyage and voyage_return records. Both use native family/source
concubine_voyage and the existing concubine_voyage_msg_id compatibility anchor.
Register defaults, runtime/JSON persistence and both SQLite schema declarations.

Persist identity/account/chat/operation/partner/route/affinity/snapshot/voyage
intent before tracked zero-retry dispatch. Revalidate controls, the business
plan and sibling pending work at dispatch. Unknown/cancelled sends remain
unresolved across reload; only fresh explicit unsent evidence permits a later
local retry. Scalar-only legacy work is retained, not assigned a guessed owner.

Native and passive replies route before generic dedupe. Hot handlers use exact
native receipts without log scans; recovery uses the original operation's sent
receipt and official server-clock reply revisions. Passive replies without an
identity hint require one unique scoped receipt. Ambiguity remains replayable.
Completion and recovery saves roll back on false/exception; cleanup matches the
exact operation, including native registration arriving after early completion.
Audit failure/cancellation cannot roll back or repeat committed settlement.

Strict start/return parsers reject foreign partner/route, duplicate/quoted
headers, incomplete rewards, malformed quantities and contradictory results.
Status-like refusals are not reward settlement. Explicit affinity loss or gain
is applied once only against unchanged partner/snapshot/voyage/affinity.
The existing positive-affinity moon-voyage fixture is now handled. Affinity
shortage requests calibration instead of inventing an exact balance.

Launch thresholds remain 120 by default and 160 for moon voyages. Disabling
voyage prevents new launches, while cleanup of an already-returned voyage can
continue if global/identity controls permit. Missing partner information at a
due return now schedules the owned partner-status read. Unknown voyage times
continue through R89's read-only status flow, never a settlement probe.

Phase cleanup accepts a positive exact anchor or a zero anchor with the same
plan; booleans and unanchored replacement phases are not cleared. Completed
result voyages receive explicit typed validation, not only dictionary equality.

Remove legacy voyage sends, retries, scalar replay specs and unowned loss/cap
projection. Remove dream and voyage-return from the outer summary replay
whitelist and delete their legacy prepare/finalize branches. Existing tree and
duel replay behavior is unchanged. Rewards remain reported, not newly booked
into cultivation/inventory accounting.

## Verification

- Added 238 voyage-action cases and two dream summary-replay cases. Migrated
  20 affinity expectations and five generic phaseful observer/replay tests.
- Focused lifecycle checkpoint: 835 passed, 6.02s.
  /tmp/xiuxian-r90-boundaries-focused-20260911.xml.
- Final isolated full: 10116 passed, 1275 subtests, 153.36s, exit code 0.
  /tmp/xiuxian-r90-full-final-20260911.xml.
- Configured Ruff, E9/F63/F7/F82, compileall, pip check and git diff --check
  pass. Tests use temporary SQLite and XIUXIAN_ALLOW_LIVE_TEST_DB=0 inside an
  isolated network namespace; only loopback is enabled for fake HTTP servers.
- All test sessions completed. Only audit documentation changed after the full
  checkpoint. HEAD remains unchanged and inherited dirty work is preserved.

## Remaining Gates

- The new column must survive any future migration/rollback. Old timeout/reset
  code ignoring it can repeat unresolved mutations. Deleting unknown rows or
  treating scalar legacy anchors as empty is not reconciliation.
- Similar zero-anchor cleanup in status, affinity and fragment helpers needs
  the next focused review. This checkpoint certifies the voyage helper only.
  Follow-up: R91-concubine-phase-ownership-20260911.md covers those helpers.
- Voyage reward accounting, legacy evidence, divination, heart, reacquisition,
  remaining lifecycle coverage, R65/R74 capacity/accounting, R07 forced-stop
  durability and rollout/rollback remain open.
- Production jfdffdddd subordinate-role MiniApp cultivation remains unverified;
  this offline result does not establish resumed or completed live cultivation.
