# R93 Heart-Trial Reply Contract

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production changes, live requests, service restarts,
listeners, skill changes, commits, pushes or deployment. Existing World Boss,
refinement, inventory API, CommandAttempt and Tianxing/retreat boundaries hold.
This is a reply-contract checkpoint, not completion of the heart lifecycle.

## Finding And Reproduction

The active heart handler accepted a round or settlement header without its
required body. Partial/duplicate rewards could clear the chain or increment
affinity. An unrecognized reply returned false after clearing pending state
and writing a new timer. A separate passive projection accepted a settlement
header alone, skipped the main handler and wrote another cooldown policy.

The first actual-handler reproducer failed 25 cases while five complete-text
controls passed. Artifact:
/tmp/xiuxian-r93-heart-contract-reproduced-20260912.xml.
Final review reproduced a CRLF false negative and three anchor-loss helper
bypasses through partial/quoted/conflicting text:
/tmp/xiuxian-r93-heart-review-reproduced-20260912.xml.

## Candidate Repair

- Add one bounded, nonmutating heart-trial parser. Normalize Markdown emphasis
  and line endings, validate the event clock, and classify one unambiguous
  outcome. A round needs the correct header pair and complete choice prompt.
  A settlement needs three valid choices and unique cultivation, affinity and
  current/delta heart-demon fields. Numbers have exact grouped-integer syntax
  and signed 64-bit bounds; absent or malformed fields are not zero.
- Require an actual integer prompt ID. Do not use the replied command ID as
  the next bot prompt. Partial, contradictory, summary and unknown text leaves
  active state unchanged rather than closing it as a fallback.
- Require an explicit parseable wait for cooldown. An untimed in-progress
  reply does not manufacture a business CD or clear the active chain. A full
  settlement uses the configured 12h plus buffer from the supplied event clock;
  scheduling jitter remains separate from the business deadline.
- Consume the typed result in the existing active handler and remove the
  unowned passive settlement writer. Known named voyage refusals cannot apply
  to a different partner. Anchor-loss recognition and its direct recovery
  helper both require the same complete text contract.
- Affinity deltas validate current state and resulting bounds before mutation;
  signed loss and zero are handled explicitly. Parsed cultivation/heart-demon
  amounts are evidence, not new accounting writes in this change.

## Verification

- 87 added cases cover complete rounds/results, missing and conflicting
  fields, grouped/signed/invalid numbers, event/prompt types, CRLF/Markdown,
  passive/native entry points, untimed holds, named refusals and anchor helper
  validation. Existing complete three-round and passive recovery tests pass.
- Focused: 2156 passed, 24 subtests, 17.31s.
  /tmp/xiuxian-r93-heart-focused-final-20260912.xml.
- Final isolated full: 10398 passed, 1275 subtests, 156.85s, exit code 0.
  /tmp/xiuxian-r93-full-final-20260912.xml.
- Configured/fatal Ruff, compileall, pip check and diff checks pass. Temporary
  SQLite only; XIUXIAN_ALLOW_LIVE_TEST_DB=0. Full tests have loopback only in an
  isolated network namespace. All test sessions completed.

## Remaining Lifecycle Work

The legacy heart sender/choices still need durable account/chat/operation
ownership, unknown-send retention, exact receipt recovery, round chronology,
transactional completion and duplicate-result accounting. The current legacy
caller/log paths do not all supply an authoritative server timestamp. A pure
parser accepting a valid supplied clock is not proof those sources are trusted.
Legacy resource-shortage/anchor/timeout handlers still contain guessed timers
and broad cleanup; paused results, delayed followups and restart need the next
lifecycle pass. Do not deploy this parser checkpoint as a complete heart fix.

Reacquisition, legacy/manual reconciliation, R65/R74 accounting/capacity,
shared R07 forced-stop durability, rollout/rollback and Final Review remain
open. The earlier R67 selected-player fix is still candidate-only; production
jfdffdddd subordinate-role cultivation was not revalidated in this turn.
