# R111 Fishing Quota Field Authority

Offline candidate only. No production, live request, service, configuration,
listener, skill, commit or push changes. R67 production cultivation is not
validated. This does not implement shared R07 or CommandAttempt recovery.

## Review And Contract Before Implementation

The current quota reader searches historical/session/count-shaped dictionaries,
chooses the first integer alias, and ignores arithmetic disagreement. It can
calibrate a new daily limit from a failed result, decrease a known used count,
or call a role exhausted using unrelated data. Worker aggregation drops the
current quota into last_* diagnostics, requiring that unsafe recursive reader.

- Share a bounded current-result quota parser between worker and runtime.
  Named fishing quota fields or an explicit daily quota container establish
  scope. Generic root count/limit/remaining fields, history, shop, session,
  proof and round diagnostics do not.
- Explicit current containers, including empty/null/invalid ones, own that
  field family and prevent nested/history fallback. Aliases at the same scope
  must agree. Accept nonnegative integral JSON numbers, not booleans, strings,
  fractional values or infinities. Require a positive limit within the existing
  supported quota bound, used/remaining within it, and consistent arithmetic.
- Promote only a confirmed round's quota into the chain's current daily field.
  A later confirmed round without a usable quota clears that calibration;
  a later failed round cannot replace it with diagnostic fields. Do not lose
  confirmed catches or invent quota evidence from HTTP/application errors.
- Runtime calibration needs a confirmed settlement or explicit daily-limit
  outcome. A reported used count below the already-known count plus this
  callback's confirmed rounds cannot decrease the counter or its quota.
  Invalid quota metadata falls back to the existing confirmed-round accounting,
  not a global fuse or a new indefinite hold. Keep the existing daily-limit
  terminal policy; do not add requests, buy bait, or change user controls.

## Startup Recovery Review Boundary

R110 final review found schedule_fishing_initial_check clears message MiniApp
work as legacy text state; the public caller has no durable operation marker.
Its unresolved fingerprint alone is not a recovery token. In particular, a lost
next response can retain the already-settled previous token fingerprint, so a
new entry or old ready result cannot prove the next action's outcome.

Before changing startup admission, bind per-round receipts and result recovery
to identity/account and the exact original round. Known-unsent authorization,
confirmed local accounting, in-flight mutation and unresolved next must remain
distinct. Do not introduce timed blind replay, persist raw credentials in the
business ledger, or add an unexplained hold with no evidence/recovery contract.
This quota change is a prerequisite, not a claim to solve durable restart
recovery, historical replay or server-day chronology. Those remain open.
The old inferred-limit/cache policy also remains separate: this change does
not establish an authoritative quota when the server omits one.

## Verification

- Initial reproducer: 59 failed, 12 passed;
  `/tmp/xiuxian-r111-reproducer-20260914.xml`. It covers historical/session
  contamination, unscoped counts, malformed/conflicting aliases, arithmetic,
  unconfirmed calibration and a current quota lost by both actual callers.
- Shared bounded field parsing replaces the recursive runtime collector.
  A confirmed result now exposes one normalized daily object; latest confirmed
  rounds own it. Runtime ignores unconfirmed, contradictory or regressive
  calibration without discarding the independently confirmed catch.
- Final review reproduced two cases where copied nested aliases competed
  with the promoted outer quota; raw quota aliases are now removed at that
  normalization boundary. Evidence: two failed, 88 passed;
  `/tmp/xiuxian-r111-review-reproducer-20260914.xml`.
- All 90 new cases pass, including both actual caller/worker paths, drained
  cancellation, valid zero/upper-bound fields, and real temporary SQLite
  save failure/reload/once-only local projection recovery. Associated suite:
  846 passed, 36 subtests, 26.72s;
  `/tmp/xiuxian-r111-focused-final-20260914.xml`.
- Isolated full suite: 12011 passed, 1275 subtests, 172.90s;
  `/tmp/xiuxian-r111-full-final-20260914.xml`. Configured Ruff, runtime/new-test
  F841, compilation, pip check and whitespace checks pass. Optional broader
  F841 reports the unchanged payload variable in tests/test_webapp_core.py:1800
  from commit 4672b0b5d; it was not folded into this behavioral change. The
  existing fishing result golden assertion now includes the canonical daily
  field. All sessions finished. No production schema or runtime was changed.

## Remaining Work

Finish exact-round receipt/recovery design before changing startup admission.
R110 fingerprints do not authorize resolving an unknown next with a previous
ready result or a newly generated entry. Keep legacy inferred quotas, source
day/clock ownership, historical duplicate results, other module gates, R65/R07
and whole-project Final Review open. R67 production subordinate-role
cultivation remains unvalidated; no deployment is authorized by these tests.
