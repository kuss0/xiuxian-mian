# R76 Journey Request and Result Ownership

## Evidence and Scope

Continued the subordinate-role MiniApp review after R75. Production was only
read through existing local capture files. The inspected September 11 capture
contained 36 journey requests with `playerId` and response shapes containing
an integer `account.playerId`. Thus this is not another demonstrated live
missing-request-field incident. Offline tests expose gaps in rejecting missing,
foreign, malformed or contradictory account evidence.

The adapter accepted invalid selections and exported unowned responses. Its
runtime consumer validated an account only when an ID happened to be present,
accepted selector-only identity, and used a cached overview without verifying
the underlying account snapshot. Unowned results could publish inventory,
cooldowns, and Tianxing consumption text. Nonboolean completion fields were
also interpreted as successful completion.

## Candidate Changes

- Require a valid selected player in the journey builder and validate its role
  before authentication/HTTP. Preserve the selected server ID and existing
  cautious/balanced/deep and encounter-mode whitelists.
- Check matching `account.playerId` in successful transport responses before
  exporting any data. Keep the actual dispatch marker and HTTP evidence.
- Independently validate the selected session, original snapshot and action
  response at the public runtime boundary, using R67's strict account helper.
  A selector is not an account receipt; compatible signed/normalized forms
  remain supported. Reparse the verified raw snapshot rather than a detached
  cached overview before authorizing a mutation or applying cooldowns.
- An unowned successful-HTTP response remains an unknown dispatched action,
  without imported rewards, timers or business text. Existing Tianxing logic
  requests calibration instead of consuming effects from that response.
  Explicit HTTP rejection remains blocked; 429/5xx Retry-After survives.
- Require literal boolean completion when supplied, retaining old responses
  which omit that optional field. Partial owned receipts still work without
  inventing a complete resource/counter panel.

No new retry, command fallback, or gameplay policy was added. Deep retreat
still does not block or consume Tianxing. Channel-send freezing remains
compatible with an available public MiniApp. World Boss and automatic incense
refinement were not enabled or changed.

## Tests

- Initial reproduction: **35 failed, 1 passed**, 88 deselected;
  `/tmp/xiuxian-r76-identity-before-20260911.xml`.
- Initial related suites: **326 passed, 5 subtests**, 20.57 seconds;
  `/tmp/xiuxian-r76-identity-after-20260911.xml`.
- Added actual entry/selection/action integration for all three strategies,
  signed and normalized IDs, unchanged primary state, channel-owned inventory,
  and explicit 4xx/5xx classification. The first integration artifact includes
  12 fixture-assertion failures: metadata was counted as inventory, and ordinary
  4xx responses were incorrectly expected to carry retry-policy delays. Those
  assertions were calibrated to the existing storage/HTTP contracts, without
  changing the shared HTTP policy.
- Expanded lifecycle file: **139 passed**, 1.13 seconds;
  `/tmp/xiuxian-r76-integration-after-20260911.xml`.
- Full network-isolated candidate suite: **7983 passed, 1275 subtests**,
  142.39 seconds, exit 0;
  `/tmp/xiuxian-r76-identity-full-20260911.xml`. Broad Ruff, compileall and diff
  checks pass. Test state stays temporary with live DB permission disabled.

## Not Accepted by This Checkpoint

General forced-stop/unknown-result durability, legacy accounting, other
command-center consumers and the entire-project Final Review remain open.
Production services, code, state, flags and timers were not modified. There
were no game requests, listener activation, commits, pushes or skill edits.
The already reported R67 production retreat incident still requires separately
approved rollout and current identity-bound per-role recalibration.
