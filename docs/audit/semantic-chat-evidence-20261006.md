# Small-World Report Evidence Scope

Base: `b16f4114`. Worktree:
`/root/xiuxian-semantic-chat-evidence-20261006`.

## Findings

The read-only report indexed script sends by message ID without chat ID.
Dual-group ID collisions could replace an owner or attach a foreign reply.
It also changed expected faith before deduplicating evidence: duplicate
log observations, or one reply indexed by both reply-to and mention, could
count twice. Substring username matching accepted another player's suffix.

The initial 14 regression cases fail on the old code. No live ownership
corruption, wrong game action or present-day wrong report was established.
This report has no scheduling or repair authority.

## Repair Boundaries

- Root and reply matching require exact integer chat/message IDs. Missing
  scope is not inferred from a unique message number. Conflicting send
  owners/commands are excluded and counted in the JSON scope diagnostics.
- Broadcast mentions match complete usernames. Unambiguous historical
  aliases remain usable after renaming; aliases observed on multiple IDs
  are excluded rather than assigned to a guessed current owner.
- Each semantic event is keyed by chat/message/kind/content. Candidate
  indexing is unique per identity. Repeated identical events are removed
  before choosing panel intervals, so late log replays cannot become new
  gains in the next interval. Distinct group messages remain distinct.
- Contradictory values for one message/kind are retained as conflicting
  evidence, with no claimed expected balance. This is not an edit reducer
  that guesses which version is authoritative.
- The output is still based on retained observation timestamps. It does
  not recover absent events, resolve delayed original observations against
  server time, infer natural faith decay or prove complete game accounting.
- Existing amount parsing and 0..100 bounds are unchanged. No runtime,
  MiniApp request, repair, notification delivery, database or strategy changes.

Four older single-group fixtures now state their chat ID explicitly. New
fixtures independently cover missing scopes, malformed numeric IDs, owner
conflicts, same-number dual-group events, duplicate edits, late replays,
conflicting revisions, exact aliases, reassigned aliases and non-Bot rows.

The rate CLI help now clarifies that `--limit` and `--window-sec` configure
request counting, not displayed rows or log lookback. Values/defaults and
rate calculations are unchanged. A custom 12/1800-second run is not evidence
of exceeding the official 90/60-second budget.

## Verification

Final targeted tests: 127 passed. Separate maintainer cross-module review:
390 passed / 16 subtests. The review found and covered cross-panel late
replay before the final freeze; this is not an external independent audit.
The earlier full pass was 16523 passed / 1461 subtests; it predates the two
cross-panel replay regressions. Final frozen full regression: 16525 passed /
1461 subtests in 448.51 seconds, JUnit
`/tmp/xiuxian-semantic-chat-evidence-final-20261006.xml`.
All tests used `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. Ruff, compileall and diff checks
pass; the CLI remains stdlib-only and can run without the service.

Read-only October 6 production replay at 18:25: 10 scoped script roots,
six panels, zero missing scopes, conflicting roots or ambiguous aliases.
The previous 3 partially explained / 1 unexplained faith intervals remain
unchanged; their common -2 residual is not assigned to an invented decay
rule or treated as a reason to spend incense. HTTP captures show a maximum
73 requests in 60 seconds, no saturation, with the peak at 13:41-13:42.

## Live Checkpoint

All 23 due deep retreats renewed by 18:20:20, while WA's long retreat was
left untouched. At 18:23:55 Lpprceqei's natural MiniApp status still showed
YuanYing retreat, level 26, approximately 10949 currently accrued cultivation;
next status check is October 7 02:23:54. No recall or launch occurred.
This does not validate the staged sect-dispatch repair or initialize a duel
cultivation baseline. The duel remains 5/10 and held safely on no_baseline.

Main worker still loads `f7958deb`. All service PIDs and the two historical
held notification batches are unchanged. The independent report delivery
repair was already pushed as `b16f4114`, with 277 post-merge tests passing.
No restart or live test send is part of this work. WA's next rift preparation
window starts around 18:45, due 18:55:26; deep retreat is not a Tianxing block.

At 18:35:26 the next ordinary summary confirmed delivery: receipt
`70df68b35e2a4a85aa519cd48f464f90`, 559 UTF-16 units, 14 lines, no mentions.
The temporary in-flight third held batch was removed on confirmation; the
two earlier unknown batches remain. From 17:00 through 18:37, runtime
receipts are 4/4 confirmed with no identical payload repeats and one mention;
independent senders and business-event deduplication are not covered by that
count. Today's seven captured HTTP errors all predate this afternoon window;
the latest is the already documented 13:41 Boss begin rejection/recovery.
