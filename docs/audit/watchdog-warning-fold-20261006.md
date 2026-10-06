# Watchdog Warning Presentation

## Natural Trigger

At 15:45:58 CST, the independent watchdog observed eight counted sends in
120 seconds across three identities. Its existing warn-only policy notified
once; it did not pause the game service or write a fuse marker.

All following message IDs are scoped to `-1002083016447`:

- Baji's status/divination/dream roots 1286050 / 1286052 / 1286054 each have
  a direct official reply. Dreaming found no fragment, a real game outcome.
- Heart root 1286056 received prompt 1286057. Choice roots 1286062, 1286065,
  1286069 each have a distinct accepted round followed by the corresponding
  edit of that prompt. Final settlement at 15:46:06 confirms three steady
  choices, cultivation +840, affinity +7 and demon value -5.
- WA's anchor 1286066 was followed by wisemole's warming root 1286067;
  1286068 first acknowledged it and then edited to successful settlement.
- Baji's completed heart session contains four answered steps and no pending
  choice. A new Moon Palace voyage was launched at 15:46:09 and its return
  time is 21:46:13. No command was sent by the investigating agent.

This was a real density threshold, not evidence of blind duplicate sends.
The known heart marker compatibility gap remains: current operations use
UUID op/session IDs, while the old watchdog exemption requires the legacy
prompt/round/try marker. Existing tests explicitly keep UUID-only events
counted. This patch does not grant an exemption from a UUID or success text.

## Narrow Change

Only the Telegram warning payload folds multiline evidence using the existing
Bot API HTML parse mode. Header, reason, warn action, the first detail line
(for burst warnings, explicitly saying not paused), and admin links remain
outside the expandable quote. Sample details remain available when expanded.
Empty/single-line details do not create unnecessary quotes.

The journal retains exactly the prior plain escaped representation. Dry runs
still print once without sending. Hard-fuse notices, thresholds, exemptions,
five-minute warning throttle, send transport, persistence and game control are
unchanged. There is still one Bot send per live warning, with no fallback or
retry added by this patch. Raw detail text is HTML-escaped before wrapping.

The watchdog remains stdlib-only and independent of the main runtime. It does
not import the Telethon-based main notification renderer just to wrap a quote.

## Acceptance

Initial focused watchdog regression: 122 passed. Final full regression:
16,337 passed / 1,461 subtests in 464.32 seconds. JUnit:
`/tmp/xiuxian-watchdog-warning-fold-20261006.xml`. The separate maintainer
review covered HTML escaping, visible action/admin links, single-send and
dry-run behavior, and retained thresholds/exemptions/throttling. Cross-module
regression: 300 passed / 16 subtests. Ruff, compilation and diff checks passed.
All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. The second pass is not an
independent external audit.

No test warning was sent to Telegram and no service was restarted. Deployment
of the watchdog formatter and natural rendered-message acceptance remain
separate; a passing local formatting test does not prove client display.

At 16:03, defensive preflight still reports an empty game pending queue and
WA's rift remains due at 18:55:26, before its preparation window. Baji is
sailing, returning at 21:46:13 with next routine check 21:54:22. The density
warning has aged out of the observer window, not been suppressed. The 15:54
new normal summary was confirmed (273 UTF-16 units, six lines, no mentions);
the two older unknown notification batches remain held without replay.
