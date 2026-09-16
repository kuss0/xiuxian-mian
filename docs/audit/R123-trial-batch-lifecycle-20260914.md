# R123 Trial Batch Registry And Authorization

Status: scoped offline candidate repaired and verified. The final associated
829-case/19-subtest suite and isolated full regression of 13126 cases/1275
subtests pass; production acceptance and whole-project Final Review remain open.

## Review Findings

- Batch IDs used only integer creation time and member count, so two requests
  in the same second replaced retained records. Old timeout work could then
  refer to the newer record under the same ID.
- Result writers admitted nonmembers/finalized records and retained nested
  aliases to caller data. A late failed send unconditionally replaced an
  already returned settlement, including its confirmed materials.
- Manual authorization kept old batch links after expiration or standalone
  renewal, accepted nonexistent/finalized batches, and could be revoked by
  another batch's delayed send failure. Finalization cleared the link but not
  unconsumed authorization, turning a later entry into standalone work.
- Successful timeout notification deleted batches with missing results. Later
  owned results no longer had a batch to update. The UI batch runner also sent
  its entry command regardless of whether authorization was accepted.

## Required Contract

1. Batch identifiers cannot collide merely because creation time and size match.
   Retained results and an old timeout must not become another batch's evidence.
2. Only batch members may contribute transport or business results. Accepted
   result snapshots do not alias their callers, and a later send failure cannot
   overwrite a returned business outcome. Real business results may supersede
   earlier provisional send failures.
3. Batch authorization requires a current unfinished batch and membership.
   Expiration, completion and standalone reauthorization remove stale links;
   old-batch cleanup must not revoke a newer authorization.
4. The UI dispatch loop honors failed batch admission before sending commands.
   Preserve existing worker/game exclusion, controls, request budgets, audit
   delivery semantics and no-mutation-retry policy.
5. A delivered timeout report is not proof that all members completed. Close
   old entry authorization but retain missing-result ownership until real
   results arrive. An identical delivered report is not resent; late results
   can produce a changed final report without losing earlier materials.

## Candidate Repair

- A UUID suffix distinguishes batches independently of creation time/count.
  Membership/finalization checks are shared by result and transport writers;
  authorization additionally checks that entry admission remains open.
- Accepted results are copied deeply. Transport failure records a provisional
  business failure only when no business outcome exists. A real later result
  can supersede that provisional state without losing confirmed materials.
- Expired/invalid authorizations clean their link, explicit standalone renewal
  unlinks an older batch, and batch-scoped revocation cannot remove a newer
  batch or standalone authorization. The UI checks authorization before each
  entry command and uses scoped cleanup after an unsuccessful send.
- An explicitly successful report acknowledges only its copied snapshot.
  Identical delivered snapshots are not resent by concurrent or repeated
  finalization; false/exception/cancelled delivery does not acknowledge them.
  Changed late results remain reportable under the existing per-batch lock.
- After an unchanged successful report, entry authorization closes and only
  that batch's outstanding links are revoked. A batch is retired only when
  every expected member has a result. Missing-result batches remain available
  for owned late completion; they cannot authorize another entry. No new
  notification task, automatic retry or gameplay recovery path was added.

## Offline Evidence

- First batch/authorization reproduction: 23 failed, 1 passed;
  `/tmp/xiuxian-r123-repro-20260914.xml`.
- The first related run found two R121 fixtures performing standalone renewal
  after inserting a batch link: 217 passed, 2 failed;
  `/tmp/xiuxian-r123-focused-20260914.xml`. They now explicitly request batch
  authorization. Original cancellation/replacement/result ownership assertions
  remain unchanged.
- Expanded associated checkpoint before timeout-retention repair: 825 passed,
  19 subtests, 25.48s; `/tmp/xiuxian-r123-related-20260914.xml`.
- Four later timeout/late-result cases reproduced loss of batch ownership;
  `/tmp/xiuxian-r123-timeout-repro-20260914.xml`.
- Final associated regression: 829 passed, 19 subtests, 25.30s, including all
  37 new cases; `/tmp/xiuxian-r123-related-final-20260914.xml`. Coverage includes
  valid UI sends, no retries, stale cleanup across awaits, failed reports,
  membership, concurrent incomplete reports and aggregate early/late materials.
- Final isolated full regression: 13126 passed, 1275 subtests, 317.94s;
  `/tmp/xiuxian-r123-full-20260914.xml`. Parsed JUnit contains all 37 new batch
  cases and 185 R122 receipt cases, with zero failures, errors or skips.
- Configured repository-wide and scoped Ruff, compileall, `pip check` and
  whitespace checks pass. All test sessions completed. Branch remains
  `rebuild/stability-20260907`, HEAD `cbf152cd61325ca9218c74ee995d643038b20905`;
  changes remain uncommitted/unpushed, with inherited quiz/UI-key edits preserved.

## Limits

No new gameplay recovery authority, persistent report store, notification retry,
failed-batch capacity policy or process-kill recovery is implied. Overlapping
explicit runs and historical receipt attribution need their own evidence.
Retained failed/incomplete reports remain memory-only and have no automatic
capacity reclamation. Do not delete unresolved records merely to claim bounded
retention; a durable archive/recovery contract remains separate work.
Production cultivation for R67 subordinate roles remains unvalidated. No
production/live request, service, listener, config/DB, skill, World Boss or
refinement change, deployment, commit or push is permitted by this patch.
