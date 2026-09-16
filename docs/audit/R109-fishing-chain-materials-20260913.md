# R109 Fishing Chain Materials

Offline candidate only, under the R108 boundary. No production edits,
requests, services, switches, listeners, skills, commits or push.
R67 subordinate-role production cultivation is still unvalidated.

## Review And Contract Before Implementation

The chain stores only each round's first fish and top-level numeric gain
fields. Reward-only rounds survive only in the last round's diagnostic fields,
so earlier rewards disappear. Runtime's recursive last-prefix stripping can
then treat historical material/gain data as current business facts. Mixed fish
and reward-only chains discard the latter entirely. Gain aliases and nested
summary copies are added together, and zero/invalid reward quantities become
one item.

- Build a business projection for each confirmed round, preserving fish,
  attached rewards and separate reward-only drops through later failures.
  Do not use last_* fields, round diagnostics, proof, session or shop data
  to infer a current gain. Retain independent identical drops across rounds.
- Explicit current catch/reward containers, including empty ones, own their
  family. Read only recognized current result/detail wrappers, not arbitrary
  recursive fields. Conflicting scalar aliases cannot authorize a gain;
  zero remains zero and malformed/negative/fractional/boolean quantities do
  not become positive gains. Missing item quantities still mean one.
- Use the same pure reward/gain decoders in the worker and runtime. The
  generated chain carries explicit standalone rewards even for mixed chains;
  catch-attached rewards are not added a second time. Counts remain retained
  before optional material decoding, per R105.
- Align daily summaries, harvest notices and settlement captures with the
  confirmed business projection. Do not rewrite previously committed history
  or introduce schema, transport/retry, account policy or request changes.

## Acceptance

Use real worker responses and both existing callers to reproduce reward-only
loss, mixed-chain loss, stale diagnostic gains and malformed quantities. Check
partial failures, identical independent rewards, empty authoritative lists and
current legacy single-result compatibility. Run focused then isolated full
regression. Historical receipt replay, unknown-effect recovery, source/day/quota
authority and production acceptance remain separate gates.

## Verification

- Initial reproducer: 37 failed, 6 passed. A fixture item beginning with
  fish- matched the existing credential sanitizer; it was renamed before the
  corrected baseline, which remained 37 failed, 6 passed:
  /tmp/xiuxian-r109-reproducer-corrected-20260913.xml.
- The worker now carries explicit standalone rewards from every confirmed
  round. Mixed fish/reward-only chains, identical independent drops and later
  failures preserve all confirmed material. Runtime and worker share current
  reward/gain decoders; the old recursive runtime collectors were removed.
  Last-result/round/shop/session/proof diagnostics cannot grant income.
- Explicit current families own their projection, including empty lists and
  zero values. Numeric aliases agree rather than add; invalid/nonintegral
  quantities cannot create items. Catch-attached rewards are not counted again
  as standalone rewards. Nested current aggregates retain independent rewards.
- Follow-up review: 9 failed, 63 passed;
  /tmp/xiuxian-r109-review-reproducer-20260913.xml. Fixed invented numeric item
  names, nameless quantity maps, nested standalone reward loss, empty/zero
  attached-reward fallback and ambiguous single-round catches. An ambiguous
  catch result retains its confirmed round count but cannot choose a fish or
  start another round.
- All 73 new cases pass. A real temporary SQLite trigger aborts the mixed
  result transaction; later save/reload/local recovery preserves all rewards
  and exactly one fish/count projection without another game request.
- Final associated suite: 707 passed, 36 subtests, 26.35 seconds;
  /tmp/xiuxian-r109-focused-final-20260913.xml. Final network-isolated full
  regression: 11875 passed, 1275 subtests, 173.20 seconds;
  /tmp/xiuxian-r109-full-final-20260913.xml. Configured Ruff, scoped F841,
  compilation, pip check and git diff --check pass. All test sessions finished.

## Remaining Limits

These are current business projections, not a historical receipt ledger.
Conflicting/invalid optional material is not guessed, and already committed
legacy summaries are not rewritten. New official result variants still need
authoritative fixtures before admission. Reward aliases are treated as one
family; differently named fields are not assumed to grant additional items.

Next review durable per-round identifiers, forced-stop/unknown-effect recovery,
source-day and quota evidence, and unresolved startup phases. In particular,
schedule_fishing_initial_check and its control/startup callers still need a
separate contract for interrupted MiniApp work; this patch does not certify
that path. Shared R07, R65 migration/capacity and whole-project Final Review
remain open. No production, service, live request, configuration, schema,
listener, skill, commit or push changes occurred.
