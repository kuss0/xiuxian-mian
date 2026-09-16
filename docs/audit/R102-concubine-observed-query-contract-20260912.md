# R102 Observed Native Concubine Queries

Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.
Date: 2026-09-12.
Final offline checkpoint: 2026-09-13.

Offline review/rebuild/test only. No production, live game requests, live
state/config writes, service changes, listeners, skills, commit, push or
deployment. World Boss/refinement stay disabled, inventory API stays UI-only,
CommandAttempt stays shadow-only, and deep retreat never affects Tianxing.
R67 production subordinate-role cultivation remains unvalidated.

## Review

The remaining native legacy/manual status path bypasses the owned query
transaction and source checks. Its save return value is ignored, and the
passive fallback consumes text dedupe before it commits a status result.
It retains no original query clock, so an old read's later edit can appear
newer than a fresh panel. These paths require lifecycle replacement, not just
another timing suppression or another retry.

## Contract Before Implementation

- A status observation requires an official reply in a configured game chat
  to the original, unedited .my-spouse command for one exact registered
  identity. Use the actual configured Chinese command constant. Verify the
  root, actor/account, original command timestamp and server reply clock;
  contradictory hints and absent provenance do not establish an observation.
- Reuse the bounded concubine_status_query JSON column for terminal observed
  reads. An explicit observed origin and original sender/actor distinguish
  them from script-dispatched queries. This is not proof that the script sent
  a command and cannot become a pending mutation or dispatch authorization.
- Preserve the original read clock and exact chat/root across reload. Later
  edits of a completed older query cannot override a newer status snapshot.
  An incomplete read may still become complete through its original edit.
- An authoritative manual read may calibrate disabled modules, but never
  enables a switch or directly continues a legacy gift/spending chain. Current
  scheduler policy owns the next action. A foreign active operation stays held.
- Snapshot changes, the terminal read record and exact legacy query cleanup
  use the existing checked state transaction. False/exception/SQL failure
  restores the entire identity and leaves the same reply replayable.
- Route both direct and passive status observations through that transaction
  before generic consumed-message/text dedupe. Remove the obsolete unowned
  passive snapshot writer. Do not add a second ledger or recovery controller.
- Only the original read pending root may close. An unrelated root, conflicting
  account/command, unknown mutation or replacement operation cannot be cleared.
- Owned native queries and explicit MiniApp readers keep their existing
  operation, actor, controls and request lifecycle. R07 shared no-ID transport
  and CommandAttempt authority remain outside this work.

## Acceptance

Reproduce source violations, ignored failed saves, passive replay loss and
stale read edits, then test the actual direct/native/passive callers, strict
legacy cleanup, disabled observation, no gift continuation, temporary-SQLite
failure/reload, duplicate delivery and new-query continuation. Keep existing
game wording fixtures; add original provenance to tests that previously
claimed native success from a bare string or message ID.

## Record And Recovery Compatibility

- The observed variant has one bounded terminal record: origin, stable
  observed:chat:root key, kind=status, identity/account/chat/root, command,
  original actor, official sender, original request clock, reply clock/ID and
  panel/summary outcome. It has no dispatch/sent time, plan key, spending
  intent or unresolved state. The existing owned record format is unchanged.
- Numeric IDs/times are validated without string/boolean coercion. A native
  timestamp that is present but invalid cannot borrow a context timestamp;
  absence of both original edit status and extracted edit metadata is not
  proof of an unedited command. Duplicate/current and older completed roots
  cannot be promoted by later edits. Same-chat ordering is checked separately
  from clocks; cross-chat equality is not a proven newer read.
- Only an exact, unique legacy read pending row without an operation owner
  can be cleared. The matching positive scalar read phase may close with it,
  including after reload. Foreign operation/account/command metadata remains
  held. A summary is read completion, not a new partner snapshot or permission
  to reset an unproven legacy daily gift attempt.
- Legacy log compensation must not synthesize this provenance from reply
  wording or a scalar phase. Original unedited command evidence and the latest
  qualified reply must be recovered together from existing local evidence;
  missing provenance is not a successful compensation. No new network lookup,
  no-ID recovery owner or mutation retry is introduced.
- The JSON column remains bounded to its latest record; no new table, history
  or runtime dual-write is added. Older candidate readers reject this variant
  as invalid rather than granting dispatch authority. Any eventual rollback
  therefore requires reviewing the paired code/state backup, not dropping a
  proven observation to make an older parser accept it. Deployment/migration
  has not been approved or performed.

## Implementation And Final Review

- Native/manual status observations use the same checked snapshot transaction
  and exact pending cleanup as owned queries, but retain a distinct terminal
  observed record. Failed saves restore the entire identity; duplicate delivery
  does not consume evidence before commit. Disabled modules remain disabled,
  and no legacy gift continuation follows a manual observation.
- The native dispatcher routes status reads, including exact commands without
  a family hint, before generic consumed-message cleanup. The passive route
  uses the same handler before text dedupe. Its old unowned snapshot writer
  and late duplicate status routing are removed.
- Reply resolution preserves original-command edit/forward evidence and
  incoming forwarding evidence through the actual VerifiedGameEvent passive
  wrapper. It does not invent an unedited original when edit metadata is absent.
  Missing-identity diagnostics remain diagnostic-only and leave replay possible;
  committed manual reads are labeled observed_query_result, not owned sends.
- The existing log producer now records message_edited and forwarded only when
  it has the corresponding source metadata. Legacy rows without these flags
  cannot prove an observed read. Channel senders may lack Telegram's user-only
  bot flag; a negative, exact registered actor is admitted without inventing
  that flag. Positive user actors still require explicit non-bot evidence.
- Log recovery requires the original command and latest reply revision together.
  It selects revisions before semantic validation, rejects conflicting/latest
  incomplete evidence, and never treats an outgoing sent row as original native
  provenance. The transaction distinguishes ignored evidence from save_failed:
  only the latter retains a commit retry. Stale/blocked reads no longer create
  a permanent recovery hold. No network lookup or mutation retry is added.
- Review confirms that the bounded observed JSON cannot validate as pending,
  sending, or unknown work. Its original read clock survives reload, and
  notification failures cannot undo completed business state.

## Verification

- Initial source/rollback reproducer: 48 failed, 3 passed; first implementation
  passed those 51 cases. Further real-producer and passive-wrapper tests found
  six failures and one positive control; /tmp/xiuxian-r102-producer-reproducer-20260913.xml.
- Separate stale-hold/missing-provenance reproducer: 11 failed, 2 passed;
  /tmp/xiuxian-r102-hold-provenance-reproducer-20260913.xml. Final producer
  review found four absent-metadata failures with 12 controls passing;
  /tmp/xiuxian-r102-final-source-reproducer-20260913.xml.
- R102 now adds 171 cases. Final source/diagnostic/log-producer suite: 264 passed,
  49 subtests, 1.72 seconds; /tmp/xiuxian-r102-final-source-fixed-20260913.xml.
  Final associated suite: 3938 passed, 62 subtests, 30.98 seconds;
  /tmp/xiuxian-r102-focused-final-v2-20260913.xml.
- First integrated run: 11371 passed, two diagnostic/fixture failures, 1275
  subtests. Those tests now supply actual original-command/account evidence
  and retain their original channel-routing and missing-identity assertions.
- Final full regression in a network-isolated namespace: 11404 passed, 1275
  subtests, 168.26 seconds; /tmp/xiuxian-r102-full-final-20260913.xml. JUnit
  records 12679 total cases, zero failures/errors/skips. All test sessions ended.
- Configured Ruff, scoped F841, compileall, pip check and git diff --check pass.
  A broader optional F401 scan is not clean: eight module-facade imports are
  used by sibling engines, while six old imports/test shims need separate
  cleanup. No blanket unused-import removal was applied.

This scoped candidate contract is implemented and verified offline. It does
not close general cross-writer affinity accounting, overlapping/same-clock
coverage, legacy mutation migration, R65 capacity/migration, R07 durability,
remaining games, production acceptance or the whole-project Final Review.
No production files, data, services, controls or identities were changed;
R67 subordinate-role cultivation has still not been validated in production.
