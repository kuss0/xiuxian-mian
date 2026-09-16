# R125 Treasure Native Lifecycle

Status: scoped offline candidate repaired and verified. Associated regression
passes 2722 cases/278 subtests; isolated full regression passes 13365
cases/1275 subtests. Production and whole-project Final Review remain open.

## Review Findings

- The custom hunt loop did not share the adapter request budget or check
  current operation admission. Its raw `asyncio.to_thread` wrapper abandoned
  workers on cancellation, allowing later mutations after caller lock release.
- Only start carried `playerId`; hunt/reveal/settle could use the login role
  instead of the selected role. Public and authorized command entries had
  different locks and different session/ownership checks.
- HTTP success without `huntResult` counted an arbitrary envelope as a settled
  round. The last step returned a pre-settlement state, and later planner or
  parser failure erased previously received settlements.
- Native callers published results to replacement owners, dropped partial
  business/inventory gains and let notification failure hide adopted outcomes.
  Unknown actions were reported as successful daily exhaustion; a new day or
  a different role of the same account could bypass the hold.
- Scheduler quota checks also skipped a still-active final round. 408/425
  wait headers were lost, and the first wrapper draft did not pass cancellation
  into the pooled transport's lease wait.

## Required Contract

1. A treasure run retains one identity/account and its current controls across
   entry, WebView, HTTP, result adoption and notification. Public and command
   entries must exclude one another; account-shared quota policy remains intact.
2. One request budget covers start and every hunt/reveal/settle action. Check
   admission at each dispatch; no blind mutation replay or transport after
   cancellation. Drain an already-started worker before releasing its lock.
3. Keep returned settlements through later failure/cancellation. HTTP success
   without a settlement receipt must not fabricate a settled round. Preserve
   selected-player metadata on every mutation, not only the initial read.
4. Only the original current owner may publish returned results. Partial known
   gains remain reportable/accountable; unknown outcomes are not daily quota
   exhaustion or successful completion. Notification failure must not discard
   already-adopted results.
5. Verify actual worker and both native caller paths, successful multiround
   execution, admission/ownership changes, cancellation, budget/fault paths and
   partial-result consumers. Preserve current game strategy and all switches.

## Candidate Repair

- Reused `MiniAppRequestBudget`, `require_miniapp_operation`,
  `run_miniapp_blocking_flow` and `MiniAppIdentityOwner`. One hunt worker budget
  covers start and all hunt/reveal/settle requests; no mutation is retried.
  Checks span authentication, budget waits, HTTP dispatch and pooled leases.
  Repeated cancellation drains the thread and carries its returned result.
- Every selected-player mutation carries the same validated player ID. Both
  native entries use the existing identity-selection loader and one owned
  execution/result helper. They acquire the per-identity public-entry lock and
  the account-scoped treasure lock. The existing channel lab allowlist and
  physical-account shared quota policy are unchanged.
- Require an actual nonempty settlement object, and require a hunt session on
  enter/reveal responses. Missing receipts stop as unknown, not completed.
  Update the final state after each response and retain confirmed settlements
  through later planning/parsing errors, budget rejection and cancellation.
- Publish only to the original identity object/account. Disabling dispatch
  after a response does not discard that owner's real result. Native entry,
  session, HTTP and notification waits are checked; notification exceptions
  cannot turn an adopted result into a lost one.
- Partial material/inventory/capture consumers use the returned settlement
  list, not diagnostic or unsettled envelope rewards. Retain unknown outcomes
  across days and sibling roles of the same account; later daily summaries
  cannot clear them. The scheduler skips held work without a false daily-done
  marker and still admits settlement of a known active final round.
- Preserve 408/425 wait hints locally, without changing shared HTTP
  classification or retry policy. Removed the worker's now-unused thread/time
  imports; did not perform unrelated lint or shared-runtime cleanup.

## Offline Evidence

- Initial reproduction: 11 failed/2 passed;
  `/tmp/xiuxian-r125-repro-20260914.xml`. Two command fixtures had English
  button text and did not enter the extractor. Corrected them to actual
  Chinese entry wording with an extraction assertion; all 13 failed before
  repair in `/tmp/xiuxian-r125-native-repro-20260914.xml`.
- First repair: 12 passed/1 failed;
  `/tmp/xiuxian-r125-first-fix-20260914.xml`. The remaining selected-player
  fixture used an invalid negative Telegram ID. It now uses
  `-1_000_000_000_000 - identity_id`, retaining strict runtime validation.
- Native command tests now stub the newly shared session-selection boundary;
  the obsolete test claiming unknown means daily success was corrected.
  The first nearby rerun passed 194 cases/5 subtests;
  `/tmp/xiuxian-r125-nearby-20260914.xml`.
- Expanded fault coverage found two lost 408/425 wait headers, 97 passing
  cases; `/tmp/xiuxian-r125-expanded-20260914.xml`. The subsequent native/UI
  checkpoint passed 205 cases;
  `/tmp/xiuxian-r125-native-verified-20260914.xml`.
- Pooled-transport admission reproduction: 1 failed/8 passed;
  `/tmp/xiuxian-r125-pool-repro-20260914.xml`. The operation check is now
  propagated through the production pooled transport as well as fake HTTP.
- Associated suite: **2722 passed, 278 subtests, 75.48s**;
  `/tmp/xiuxian-r125-related-verified-20260914.xml`. It includes 108 treasure
  lifecycle cases plus the added background false-completion regression.
  An earlier isolated run had 36 local HTTP/UI fixture failures solely because
  loopback was down; `/tmp/xiuxian-r125-related-first-20260914.xml`. Enabling
  only loopback inside the fresh network namespace resolved them; external
  networking remains unavailable. Tests use temporary data/session/SQLite paths.
- Configured repository Ruff, stricter worker/new-test F checks, compileall,
  dependency validation and whitespace checks pass. An exploratory all-F check
  of the old runtime/UI also reports inherited unused imports/locals, not
  included in the configured gate; those unrelated edits were left alone.
- Final isolated full regression: **13365 passed, 1275 subtests, 338.92s**;
  `/tmp/xiuxian-r125-full-20260914.xml`. Parsed full/associated JUnit reports
  have zero failures/errors/skips and include all 109 new R125 cases. All test
  sessions completed. Branch remains `rebuild/stability-20260907`, HEAD
  `cbf152cd61325ca9218c74ee995d643038b20905`; no commit/push occurred.
- Inherited quiz-bank and UI-key-tool hashes are unchanged:
  `89268c5517eaace4deaefd7e47d17cbc5de1162506e993b7a65a6336784f37ec` and
  `9e72d7ddceea2e03907d2c45c5dd8b3c9078f5c1dd7fd1e8383b4b0937d42b77`.

## Evidence And Limits

The local upstream reference `model/features/dwelling_hunt.py` supplies
`playerId` on hunt, reveal and settle, and does not accept a missing
`huntResult` as settlement. The local Python companion estate document describes the
same HAR-verified endpoints and same-session progression. These are protocol
references, not current live acceptance.

Remaining quota/parser authority, historical receipt identity, unknown-action
reconciliation and crash-durable pre-dispatch ownership require explicit
evidence. Do not invent a recovery endpoint, silently discard unknown work or
claim a passing mock proves production health. R67 subordinate cultivation is
still unvalidated in production.

This is not an atomic or crash-durable treasure operation journal. Existing
result/inventory/state writers and completed legacy DTO compatibility remain;
per-round historical deduplication, explicit source/session authority, strict
quota validation, parser field authority and notification persistence require
separate review. The hold blocks new treasure mutations but does not prove or
automatically resolve their past outcome. Production acceptance must reconcile
legacy held state and the current game protocol, not clear it by date.

No live game request, production/config/DB mutation, service/listener operation,
skill edit, World Boss/refinement change, deployment, commit or push.
