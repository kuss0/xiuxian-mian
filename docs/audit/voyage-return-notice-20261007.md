# Voyage Return Notice

Base: `4cd4fb3d`. Lab: `/root/xiuxian-voyage-return-notice-20261007`.
No production restart, game probe, state correction or notification replay.

## Evidence And Scope

Baji returned through the public-entry command center at 10:11:34 and
started another Moon Palace voyage at 10:11:35. Captures contain exactly one
successful request for each action, HTTP 200. State is sailing, next return
16:11:39, no pending reply/error, fishing still 5/5. Its two queued notices
only say returned/started. The return handler already exposes the accepted
result text but replaces its notification with a fixed sentence.

The current capture preserves response shapes, not reward values. The new
voyage has cleared the prior runtime result; today's exact gains cannot be
reconstructed from this evidence and must not be invented or replayed.

Extract the command-path reward formatting into one shared helper, preserving
its existing normalization, eight-line cap and fallback behavior. Only an
already accepted MiniApp return adds that detail to the existing low-priority
notice. HTML-escape the added text. The returned API message/extra, launch
notice, state parser, ownership, affinity, deadlines, sends, retries and
summary delivery policy do not change. This is presentation, not a reward
ledger or a new parser. Unsupported wording retains the legacy behavior.

A formatting exception falls back to the original notice and logs only its
exception type. A send failure/cancellation retains the original completed
business response. A second return invocation remains not-due and sends
neither a new game request nor another notice.

## Verification

- Red baseline: three missing-reward regressions failed; four negative
  controls passed on the original implementation.
- Final related regression: **558 passed**. Includes command/MiniApp voyage,
  affinity, duplicate return, HTML material names, wrong player/partner,
  unparsed/unknown responses, formatting/send/cancellation and durable summary
  admission followed by one collapsed delivery with no explicit mention.
- A legacy-format test draft omitted identity context and failed five cases.
  Its in-progress full run was terminated; it is not acceptance evidence.
  The fixture now uses the existing isolated identity context.
- Frozen full suite: **16824 passed / 1486 subtests**, 439.72 seconds.
  XML: `/tmp/xiuxian-voyage-return-notice-full-20261007.xml`.
- Maintainer second review re-read the complete diff and ran an additional
  **654 tests** covering command identity, public-entry and background
  lifecycles, cancellation, summary persistence and notification acceptance.
  This is a second review pass, not an independent external reviewer.
- Ruff, compileall and `git diff --check` pass. Every pytest invocation uses
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; no test uses production state or transport.

## Live Boundary

At 10:40 main worker 2901475 still runs code through `51c2e348`; supervisor
2901469, observer 2835325 and watchdog 2835315 are unchanged, NRestarts=0.
The candidate remains unaccepted in a natural runtime notice until loaded.
Do not restart solely for this text change or replay Baji's earlier result.

The 09:39-10:26 retreat window produced 23 settles and 23 restarts, exactly
one capture per action, all HTTP 200. State has no overdue/non-running retreat.
Fate remains 24/24 settled. Routine summary 10:21:33 confirmed as
`ee04e63c5c044f88a46fb7a2d8301d54`, 781 UTF-16 / 23 lines / no mention.
Historical held batches remain two. Foreground follower **33330** replaces
expired **56988** and runs until approximately 13:35. Next routine summary is
10:53:10, WA voyage 11:27:55. Trial durable reward handoff, fate regression
root cause, held notices and unloaded changes remain separate debts.

## Natural Acceptance On October 7

After the 13:45 controlled load, Baji naturally returned at 16:18:28.
Captured HTTP shows one command-center return, status 200 / attempt 1.
The accepted notice retains cultivation +286, stones +64, 素女禁纹 x1,
spirit reserve +5 and affinity -8; exactly one notice row was queued.

Existing enabled companion work ran before the next voyage: one status
query, divination (cultivation -180), dream (no fragment), then three
steady heart choices. Official root 1293058 was edited through settlement
at 16:20:07, with cultivation +711 and affinity +7. Its durable session is
complete, so affinity reconciles as 390 - 8 + 7 = 389. These are separate
game results, not additional voyage rewards. New voyage launched once at
16:20:10, also status 200 / attempt 1; next return 22:20:14.

The return reward notice remained separate from the launch row until the
ordinary combined batch at 16:45:01, confirmed receipt
`1cde0b0510344aa0b7a9e375127ae8b8`, 463 UTF-16 units / 11 lines / no
explicit mentions. All earlier rows were removed after delivery; only a
new 16:45:35 nascent-soul notice remained. Historical held=2 was untouched.
This completes natural acceptance of the normal reward-notice path, not
the unresolved native-voyage durability/recovery design in the other Lab.

WA supplies a second normal-path acceptance: one native return at 17:38:59,
cultivation +324, stones +97, 素女禁纹 x1 and affinity +9. Its following heart
settlement added affinity +7 independently, so 627+9+7=643; one new voyage
launched at 17:40:44. Both mutation captures are HTTP 200 / attempt 1.
The reward row was delivered with the 18:08:59 batch, confirmed receipt
`0a7e122450cd423c913113110517e72c`, 759 UTF-16 / 20 lines / no mentions.
Thirty ordinary records became one grouped/folded message, then the queue
emptied; held=2 stayed untouched. This is production observation of the
already loaded normal path, not acceptance of the unmerged native ledger.
