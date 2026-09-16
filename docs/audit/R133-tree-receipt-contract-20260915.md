# R133 Tree Receipt Authority

Offline candidate only. This closes the reproduced receipt-classification and
current-reward parsing defects, not the whole C3 MiniApp package, durable tree
dispatch intent, or production acceptance.

## Reproduced Failures

The first 11 isolated tests failed against the R132 candidate:

- HTTP success cleared `open_run` before submit validation. Missing daily
  scores became zero; fractional scores were truncated into accepted scores.
- `bool("false")` accepted a malformed verification flag. Mode and round-token
  conflicts did not stop accounting or later allocations.
- Recursive reward traversal included historical/unrelated rewards and used
  quantity one when a current item had no quantity.

## Candidate Contract

- Both single-game and daily callers share `tree_receipts.py`. It recognizes
  the current root or one unambiguous `data` envelope, not arbitrary nested
  history. Existing minimal allocations and submissions remain valid.
- Scores and verification fields have explicit types. A real `verified.ok =
  false` still produces the established zero-score stop; an absent/malformed
  score or malformed verification is not a zero-score settlement.
- Provided mode, round token, seed, round number, season/day and player fields
  must agree with the owned request/allocation. Production wrappers pass the
  expected role, including Telegram channel-ID normalization. The parser does
  not invent mandatory server echoes that the existing protocol omits.
- `open_run` closes only after a confirmed, correctly bound submit. An
  unconfirmed submit retains the original open/unknown outcome and stops new
  mutations. A reissued submitted mode/token cannot duplicate a submit or gain.
- Reward quantities are explicit nonnegative exact integers. Item/gain aliases
  do not double-count, conflicting aliases are rejected, and unrelated
  historical containers do not contribute. Scores are not inventory gains.
- Correctly bound current rewards survive a missing score or later failure as
  partial receipts. They do not count as completed rounds. A complete score
  with incomplete material data retains known facts and stops further play.
- Runtime records and audit summaries preserve this distinction. A single-game
  summary now reports its actual materials rather than always claiming none.

The changes do not alter proof generation, score caps, podium policy, quotas,
request limits, or World Boss/refinement switches. They do not introduce a
retry controller, a new gameplay endpoint, or a production request.

## Validation

- Final focused regression: 485 passed. Report:
  `/tmp/xiuxian-r133-tree-receipts-focused-20260915.xml`.
- Native direct, command-entry, public-entry and scheduled callers retain
  unknown scores/ownership conflicts, never complete the day, and do not open
  another round on the next day. Ordinary quota-exhaustion completion and
  confirmed zero-score/mismatch behavior remain covered by existing tests.
- A real temporary SQLite save/load retains the partial receipt and prevents
  both scheduler and public-entry reentry without HTTP.
- Final full regression: 14400 passed and 1275 subtests passed, 395.53 seconds.
  Report: `/tmp/xiuxian-r133-tree-receipts-full-20260915.xml`.
- Parsed JUnit comparison against R132 confirms 59 added cases, no removed
  baseline cases, no failures/errors/skips, and every focused case in the full
  run. Configured/scoped full-F checks, compileall and dependency checks pass.
- Tests run with `XIUXIAN_ALLOW_LIVE_TEST_DB=0` inside a private network
  namespace. Only loopback is enabled for the full suite's local HTTP tests.

## Still Open

The worker still lacks a durable per-request tree intent/result journal.
`TreeMiniAppOperation.finish()` retains memory on a final persistence failure
but does not establish forced-stop durability. Saving/reloading a returned
partial result is not evidence about process death before that save.

Original-round recovery needs authoritative server correlation; a changed
entry, a new day or a matching reward digest cannot supply it. Historical
accounting and retention are also not certified by the request-local round key.
These remain C3/C4 work, not silently closed by holding unknown outcomes.

Production, including R67 cultivation for `jfdffdddd` subordinate roles, remains
unchanged and unvalidated. Whole-project Final Review has not been completed.
