# Fishing Skip Summary

## Evidence And Scope

The October 7 midnight native-fishing pass correctly skipped identities without
a rod/companion and did not trip the global circuit. These ordinary rows still
occupied per-identity details in the untyped group. The natural 00:30:51 summary
was confirmed as 996 UTF-16 units / 23 lines / no mention, receipt
`abfea319fc42456591ba20fb15f7de7a`. This is not a per-identity notification storm.

Lab `/root/xiuxian-fishing-skip-summary-20261007`, base `536bbdc9`.

Only an explicit native response with ok=true, terminal_skip=true, status=skipped,
and false unknown/committed/supply/wait flags may enter `fishing_skip`.
The producer already requires successful checkpointing and current authority
before declaring a terminal skip. Missing/malformed flags do not qualify.
No prose matching, historical reclassification, or user-name attribution is used.

Display these rows as one identity/record-count line, without spending the detail
budget. Keep all individual rows in the durable queue until the existing delivery
protocol handles them. Actual catches, supplies, sailing waits, unknown outcomes,
high-priority messages and buttons retain their routes. No game behavior, switches,
send rate, 30-minute summary interval, or held-delivery policy changes.

## Persistence And Rollback

Review rejected adding a new required v1 kind: a still-running old observer
would reject the whole queue. The final candidate instead keeps `summary_kind`
empty, uses a stable JSON string bucket key, and adds optional
`presentation_kind=fishing_skip`. Old readers accept the existing string-key
contract and ignore the optional metadata; old formatters show the original
rows. New formatters collapse only explicitly marked rows. Historical untyped
rows are never inferred into the class.

The original `536bbdc9` reader and formatter were executed offline against a
new-format checkpoint: validation succeeded, the original row was retained,
and the legacy renderer displayed it normally. The actual loaded generation
`f7958deb` validator also accepted the new row unchanged. Store validation remains pure
standard-library code with no new Telethon or game-state import. Unknown/invalid
presentation values fail validation in the new reader.

No schema version bump, old-observer restart, queue conversion or production
send is required. Never delete or replay held deliveries to switch versions.

## Validation

- Focused classifier, renderer, durable store, health, producer and independent
  report tests: 245 passed. The new reload test initially called a nonexistent
  public load method; it now uses the store's actual locked load contract.
- Existing native producer tests now assert confirmed skips receive the class
  and failed local saves do not. No fishing business expectation was changed.
- A temporary SQLite reload retains all 24 individual observations; a single
  mocked delivery shows one skip heading. A separate renderer test preserves a
  fish reward even with the detail budget limited to one.
- High-priority/buttons, historical untyped rows, missing/contradictory proof,
  stable identity keys and summary-kind/schema parity have regression coverage.
- Final backward-compatible candidate: 16625 passed / 1478 subtests, 440.87s;
  JUnit `/tmp/xiuxian-fishing-skip-summary-final-20261007.xml`.
- Broadened presentation/store/health/acceptance/producer/lifecycle review:
  282 passed. Ruff, compileall and diff whitespace checks pass.

The pre-compatibility full suite passed 16625 tests / 1478 subtests in 453.21s.
It does not certify the subsequent backward-compatible row encoding; the final
run above is the acceptance result. Ready to merge and push without a restart.

No production loading or natural acceptance of this candidate is claimed.
