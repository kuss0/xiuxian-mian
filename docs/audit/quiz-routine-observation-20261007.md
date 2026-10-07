# Quiz Routine Observation

Base: `128f0bd0`. Lab: `/root/xiuxian-quiz-routine-observation-20261007`.
No live game request, state correction, switch change or restart.

## Evidence And Scope

At 11:03:15, an ordinary correct answer by unmanaged @wandaozhongdian
still entered the routine notification queue. The previous change suppressed
external timeouts only, not ordinary correct/wrong results or successful
passive learning. The user's request excludes unrelated players' routine
notifications, not the shared bank's integrity problems.

Six routine result branches now share a local reporting helper. Owned
results keep their existing notification path. Unmanaged correct/wrong
results and successful new/existing bank learning become local observations.
Bank answer mismatches, conflicts and write failures still notify. Watcher
consumption, confirmation, persistent bank writes, parsing, answer scheduling,
retries and transport behavior are unchanged. Already queued rows are not
deleted or replayed.

Local result logs have an explicit unmanaged-observation prefix and a
single-line body. The read-only health observer recognizes that exact prefix,
so error words in quiz content do not become runtime failures. Unrecognized
prefixes, actual write failures and managed-account errors stay visible.

## Verification

- Focused regression: **186 passed / 24 subtests**.
- Full frozen suite: **16851 passed / 1486 subtests**, 451.88 seconds;
  `/tmp/xiuxian-quiz-routine-observation-full-20261007.xml`.
- Maintainer second pass: full diff and surrounding result/observer paths
  re-read; **517 passed / 35 subtests** across quiz, health classification,
  callback failures, durable summaries, delivery policy and notification
  acceptance. This is not an independent external review.
- The result matrix covers owned/unmanaged identities, correct/wrong answers,
  known/added/existing bank entries, mismatch/conflict/write failure and
  duplicate broadcasts. Existing temporary-file learning tests still assert
  the saved question/answer, not just a mocked persistence call.
- Actual generated local lines with warning words/newlines are replayed
  through both health classifiers. Invalid observation prefixes remain errors.
- The first fixture omitted normalized empty C/D choices; its expectation was
  corrected. That draft assertion failure was not a product defect.
- Ruff, compilation and whitespace checks pass. Tests run with
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`, isolated state and mocked transport.

## Live Boundary

At 11:39 the main supervisor/worker remain 2901469/2901475, loaded through
`51c2e348`. Observer 2835325 and watchdog 2835315 are unchanged;
NRestarts=0. Watchdog is healthy; the observer only reports the same two
historical held batches. This patch is not runtime acceptance until loaded
and naturally exercised. Do not restart solely for notification cleanup.

WA's disaster broadcast 12643924 in -1001680975844 was followed by exactly
one `.神迹 布道` send 1291496 in -1002083016447. Official reply 1291497
from hantianzun34_bot at 11:30:43 confirms cultivation cost 12000, faith 100
and stability 84. The pending action is cleared, stock remains 205913 and
the god cooldown is 14:30:43. This is not an incense-funded relief action.
The earlier unexplained faith deltas remain unresolved.

WA returned at 11:32:41, relaunched Moon Palace at 11:32:42, and is sailing
until 17:32:46 with no voyage error. Baji remains sailing until 16:11:39.
Summary receipt `dd578a74a75845d89726b81cc54c5c14` at 11:33:16 is confirmed,
276 UTF-16 units / six lines / zero explicit mentions. These are existing
worker observations, not validation of unloaded reward/quiz notice patches.
Follower 33330 remains active until approximately 13:35.
