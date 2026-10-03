# Notification Health: Audit And Second Review

Base: `326529b3`. Lab: `/root/xiuxian-notification-health-20261003`.
Scope: notification checkpoint observation, not delivery-policy changes.
The implementation, initial audit and second review were separate passes by
the same maintainer; this is not an independent external-review claim.

## Implementation

- Reuse one checkpoint validator between the runtime store and observer.
  No format migration, queue mutation or automatic recovery was introduced.
- Read the independent summary DB with SQLite `mode=ro`, `query_only`, a
  single read transaction and a 250ms lock timeout. Check the stored byte
  length before loading JSON (8 MiB maximum); VM progress has a one-second
  deadline. Missing files are never created and leaf symlinks are rejected.
- Report absent/uninitialized evidence separately from a healthy checkpoint.
  `--notification-summary-required` makes absence an error when an operator
  explicitly enables structured summaries. It remains off for today's rollout.
- A pre-send held record gets 300 seconds before a delivery-review warning,
  exceeding the current 12-second Bot and 10-second account timeout budgets.
  No delivery is inferred from age, nor does the observer retry it.
- Pending rows past the saved window plus 900 seconds warn. Retired unknown
  records stay visible instead of becoming healthy when detailed rows expire.
- Write-failure journal markers are detected even when the older checkpoint
  remains readable. Counts/reasons enter observer status, risks and evidence;
  payload text, tokens and SQL exception text do not.

## Initial Audit And Acceptance

Review boundaries: read-only persistence, bounded queries, no game imports,
secret-free output, pending-versus-in-flight distinction and disabled behavior.

Issues found and resolved before the frozen full test run:

1. A socket monkeypatch broke SSL imports instead of testing network isolation.
   The test now denies actual socket creation/connect/DNS via an audit hook.
2. Checking only a readable old checkpoint can overlook failed new writes.
   The observer explicitly recognizes the store's failure marker regardless
   of exception class name.
3. Expired held rows could look healthy after retention. The existing retired
   record counter now remains a visible unresolved-delivery warning.
4. Missing/empty storage needs an explicit expected-enabled contract, not a
   guess from a systemd environment overridden by dotenv. The required flag
   supplies this contract; missing remains unavailable when it is off.
5. Corrupt/expensive SQLite schemas need a query bound as well as a JSON size
   limit. Added VM deadline and an isolated recursive-view regression.

Validation:
- Expanded focused suite: **242 passed, 4 subtests**.
- Full isolated suite: **15779 passed, 1419 subtests**, 451.40 seconds.
  JUnit: `/tmp/xiuxian-notification-health-full-20261003.xml`.
- Ruff, compile and `git diff --check` passed. All tests used
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; no production fault injection or game request.
- Candidate observer read the current production snapshot without writing it:
  overall `ok`, summary `not_created`, `available=false`, no summary alerts.
  This is correct for the existing summary-off deployment, not proof of a
  working enabled summary stream.

## Second Review

After initial acceptance, re-read the shared-validator extraction, status/risk
integration, secret boundaries, shutdown behavior and missing-storage contract.
No additional release blocker found for this limited observer rollout.

Re-ran health/store/observer/shutdown/background-task coverage:
**184 passed, 10 subtests**. Source hashes remained identical to full acceptance:

```text
ffb063a262c09a76f18c7a8e3277cb780abcd241967f9145010e1fc38bb0a6c5  model/audit_summary_store.py
03c120bf119ca57adfad73f292df9aaa910d4837ddbe1a790d4b0e08ed2f4fa3  model/audit_summary_health.py
3187299f1a67de74a0b4fa063638624d67ecc49f6ac77e48fabab1daaca2e6b0  tools/health_observer.py
1f3058d0d5d2f14bc4942c5c7600ea4391e15c9821df502b3f943a24fb1cfe0a  tests/test_audit_summary_health.py
```

## Rollout Boundary And Remaining Work

Merge only these files and this report; preserve unrelated quiz/backup changes.
Restart the health observer only. The game service need not restart because
its loaded store validator has equivalent semantics and summaries remain off.
Main service before rollout: PID `796003`, start 15:06:02 CST, NRestarts 0.

No summary replay, acknowledgement, queue clearing, automatic fuse or TG alert
sender is added. Observer output remains the existing local/UI health channel.
Storage writes can fail without changing the old snapshot; detection of those
failures still depends on the existing bounded journal window. This is not a
new persistent error ledger. The observer uses this deployment's canonical
`data/state/audit_summary.db`, not arbitrary `XIUXIAN_STATE_DIR` overrides.

Before enabling structured summaries, coordinate the required observer flag
with the actual runtime setting. Existing held/retired warnings must not be
cleared by deleting the DB. Their operator resolution policy remains TG-06 work.
Bot unknown-result fallback, transport receipt scope, further module grouping,
24h natural acceptance and gift/backup/MiniApp debts are not closed here.
