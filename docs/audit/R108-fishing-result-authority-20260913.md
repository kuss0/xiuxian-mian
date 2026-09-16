# R108 Fishing Result Authority

Offline candidate only; same operating boundary as R107. No production,
live game, services, switches, skill, commits or push. R67 is not live-verified.

## Contract Before Implementation

The catch decoder falls back to older/nested payloads after an explicit empty
catches list. The runtime can add parsed catches to inventory without a
confirmed settlement. Count hints are coerced, and a contradictory nested
ready=False can be discarded when the outer HTTP payload says ready=True.

- Explicit current catches, including an empty list, own their projection.
  Do not recover older catches through last_result after an explicit list.
- Use a shared strict confirmed-count contract: booleans, strings, negatives,
  nonintegral counts and disagreeing count fields cannot authorize gains.
  A legacy single settled/ok=True response with no count is one round; an
  explicit zero is zero. Partial failure may retain a valid positive count.
- Without confirmed rounds, no inventory, catch/reward summary, valuable-drop
  reminder, harvest notice or business-gain capture is created from the data.
  Existing explicit daily-limit/no-rod handling is not an extra settlement.
- Readiness fields must agree and be boolean when present. Contradictory or
  malformed ready/result wrappers cannot become settled, start another round,
  or hide previously confirmed rounds.
- Preserve independent identical catches from distinct confirmed rounds. Do
  not use payload equality as replay identity. This is not a historical
  receipt ledger, unknown-mutation recovery or quota/source-clock certification.

## Verification

- Initial reproducer: 30 failed, 11 passed;
  /tmp/xiuxian-r108-reproducer-20260913.xml. Explicit empty catch lists, zero
  or invalid confirmed counts and contradictory readiness previously granted
  material gains or continued the chain.
- Shared confirmed-count validation now governs result admission, projection,
  summaries, notices and business capture. Explicit list input no longer
  falls back to last_result. Zero/unconfirmed results cannot contribute
  catches, loose rewards or reminders; positive confirmed partial failures
  still retain their completed rounds. Identical independent catches remain
  separate entries.
- Follow-up review reproduced eight failures with 42 passing cases;
  /tmp/xiuxian-r108-review-reproducer-20260913.xml. Catch-count inconsistency
  was checked only by the reducer, after business capture. All consumers now
  reject catches exceeding confirmed rounds. Explicit ready=null is invalid,
  rather than being treated as a missing readiness source. Malformed nested
  result wrappers and disagreeing boolean readiness cannot settle or advance.
- Existing append_business_capture already isolates sink failures. Two actual
  caller regressions verify that this failure preserves committed counts and
  inventory; no shared capture change was needed.
- All 50 new cases pass. Final associated suite: 523 passed, 22 subtests,
  21.81 seconds; /tmp/xiuxian-r108-focused-final-20260913.xml. Final isolated
  full regression: 11802 passed, 1275 subtests, 173.50 seconds;
  /tmp/xiuxian-r108-full-final-20260913.xml. Configured Ruff, scoped F841,
  compilation, pip check and git diff --check pass. All sessions finished.

## Remaining Work

This certifies only the scoped count/catch/readiness authority boundary.
R109-fishing-chain-materials-20260913.md subsequently covers recursive
material/gain fallback and the chain's per-round reward projection. This
contract does not establish historical replay identity, source-day
or quota authority, forced-stop/unknown-effect recovery, startup reconciliation
or production health. There are no new schema or retry-controller changes.
R67 subordinate-role cultivation is still unvalidated in production.
