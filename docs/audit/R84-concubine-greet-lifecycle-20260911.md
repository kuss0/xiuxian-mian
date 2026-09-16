# R84 Daily Greeting Lifecycle

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

This is an offline candidate repair of .每日问安, not .婉影问安.
It does not close the concubine module or the whole-project objective.
Production services, databases, configuration, game traffic, listeners,
remotes, skills and the user-owned quiz/tool files are unchanged. World Boss
and refinement remain disabled; CommandAttempt remains shadow-only.

## Evidence

The initial 24 cases failed. The old sender claimed the operation only after
awaiting transport, did not track its receipt, and wrote through deleted,
replaced or rebound identities. Missing receipts used the timeout retry path.
The second timeout fabricated today's completed marker without game evidence.
Startup discarded scalar pending anchors, permitting another mutation.

After the first repair, integration review reproduced a separate loop: an
explicit no-partner result scheduled a delay but left the cached partner
available, so the next action was another greeting rather than calibration.
That path now requires a status read. Final fault review also reproduced
contradictory/duplicate result acceptance and malformed affinity conversion;
the five failing cases are repaired.

Existing greeting tests now create an actual owned dispatch and supply the
official sender, chat and server event clock. Assertions that previously
required retrying summary text or inventing a completed day were replaced
with pending-retention assertions. Voyage-lock admission uses affinity below
300, matching the real scheduler prerequisite.

## Shared Implementation

R83's concubine_gift.py is moved to concubine_affinity_actions.py and extended
with the greeting business rules. There is one dispatch, ownership, receipt,
save-rollback and recovery implementation, not a copied greeting engine.
The existing concubine_gift_actions JSON schema and gift source tags are
unchanged. The new bounded concubine_greet_action column contains one greeting
record and is registered in defaults, runtime columns, JSON codec and both
additive/new SQLite schemas. Both action fields are excluded from the plan
fingerprint to prevent self-invalidating writes.

The original greeting prerequisites remain: enabled Tianji support, Star
Palace membership, an available Dao-heart partner, affinity below 300 and no
confirmed greeting for the current game day. Greetings do not require gift
inventory. Invalid affinity data cannot enter greeting transport.

Intent is saved before dispatch, with explicit identity/account/chat, unique
operation ID, partner, baseline, day and plan. Queue admission rechecks the
captured owner, controls, plan, day and summary window. Sends use track=True,
max_retry=0 and the existing 900-second reply wait. An in-flight token and
persisted unresolved record prevent concurrent or restart reentry.

Only fresh, explicitly unsent evidence permits a scheduled retry. No receipt,
invalid receipts, exceptions and cancellation retain unknown work. An early
completion wins over late transport completion or exception. An intent save
failure does not send. Unknown greetings never expire into another greeting,
including on later days. This is not a new shared retry controller.

## Results And Recovery

Native and passive greeting replies enter the owned reducer before generic
consumed-message cleanup or text dedupe. Require the exact root/chat,
official sender and server clock; reject contradictory identity/account,
root or edited-command metadata. Legacy/unowned replies cannot invent an
operation or completion. A real runtime receipt may omit account_id only
when its unique operation ID and unchanged captured owner identify it.

Only one unambiguous result is accepted: matching-partner positive gain,
explicit daily limit, explicit no-partner or an explicit voyage lock.
Generic retreat/YuanYing summaries, unknown text, impossible gains and mixed
or duplicate results retain pending work. A summary is not proof that the
greeting failed or succeeded and never authorizes a blind retry.

Success and daily-limit results own the operation day, not local delivery day.
Their saved fact prevents another greeting even if UI scalar day markers are
cleared. Confirmed gains apply once only to an unchanged known baseline;
newer snapshots are not incremented again. If no authoritative baseline was
captured, record the result but require status calibration before spending.
Disabling a module after dispatch does not erase the fact or let its callback
overwrite newly configured schedules.

No-partner results invalidate cached availability and require calibration,
without fabricating daily success or immediately acquiring another partner.
Voyage waits use the reply's server clock, not delayed local receipt time.
Those known failures do not consume a successful daily greeting marker.

Completion, numeric projection and exact operation cleanup save together
before returning. A failed/throwing save restores the pending operation and
local projection; replay can complete without another send. Cleanup cannot
delete a replacement operation at the same root. Recovery reuses the bounded
exact receipt/log search from R82/R83 and passes evidence through this reducer.
Newer log edits supersede older text and equal-clock conflicts are rejected.

## Verification

- New greeting lifecycle cases: 120.
- Focused suite: 931 passed, 73 subtests, 7.29s.
- Focused artifact:
  /tmp/xiuxian-r84-greet-verified-focused-20260911.xml.
- Full isolated suite: 9203 passed, 1275 subtests, 147.82s.
  /tmp/xiuxian-r84-greet-full-20260911.xml.
- Configured Ruff, E9/F63/F7/F82, compileall, pip check and diff checks pass.
- All test sessions completed. Only documentation changed after full
  verification; no runtime/test edits followed it at this checkpoint.
- Initial reproductions:
  /tmp/xiuxian-r84-greet-before-20260911.xml.
  /tmp/xiuxian-r84-greet-calibration-before-20260911.xml.
  /tmp/xiuxian-r84-greet-final-review-before-20260911.xml.
- R83 gift and R82 query cases remain in the focused suite. SQLite reload,
  true runtime receipt shape, native/passive dispatch, cancellation, owner
  replacement, duplicate results, local save failures and log edits are tested.
- Temporary SQLite only. Test processes run in separate network namespaces;
  the full suite enables only loopback for its local fake HTTP servers.

## Remaining Gates

- Legacy scalar-only greeting pending is held for explicit migration, not
  erased, retried or upgraded to a fabricated operation ID. This hold is not
  proof of legacy liveness. Migration/reconciliation is required before rollout.
- One retained daily operation is not an affinity/history ledger. Manual or
  unthreaded effects, post-terminal corrections and the rest of the module's
  scalar resource chronology remain open.
- Dream, puzzle, divination, heart and voyage sends still need lifecycle
  review. R84's reply parsing of a voyage lock is not a voyage rewrite.
- Follow-up R85-concubine-fragment-contract-20260911.md covers authoritative
  fragment panels and puzzle admission only, not those mutation lifecycles.
- Shared forced-stop/save durability, malformed raw JSON, evidence missing
  beyond bounded log windows and operational capacity remain open under
  R07/persistence and the existing migration/retention review.
- Deployment needs snapshot, schema, legacy-pending and rollback review. Old
  greeting code cannot ignore the new record and resume its retry path safely.
- Production jfdffdddd subordinate-role MiniApp cultivation is still
  unverified. No production recovery or live health claim follows from these
  tests. Whole-project Final Review and the other matrix gates remain open.
