# C4 Owned JSON Reload Review

## Status

The reproduced codec gap is repaired and verified in the offline candidate.
The original review followed the [stage handoff](rebuild-stage-handoff-20260915.md);
the bounded follow-up changes only the codec and its regression coverage.
This is not whole-C4 or C6 acceptance. C1-C6 and the full objective remain open.

Candidate: `/root/xiuxian-main-rebuild-20260907`, branch
`rebuild/stability-20260907`, HEAD
`cbf152cd61325ca9218c74ee995d643038b20905` plus its existing dirty worktree.
The initial review changed no runtime or test-suite source. The follow-up
changes `model/persistence.py` and adds `tests/test_owned_json_persistence.py`.
No production operation, commit or push occurred.

## Original Finding

**High: a corrupted owned operation can become an idle operation after reload.**

At the original checkpoint, `_serialize_db_value()` replaced `None` in
generic JSON fields with the default empty container. `_deserialize_db_value()`
similarly replaced SQL nulls, unparseable JSON and wrong-container JSON with
empty defaults.
These defaults are appropriate for some disposable views, but not for the
new action/observation journals whose absence authorizes fresh work.

All nine fields below took that generic path:

- `concubine_status_query`
- `concubine_gift_actions`
- `concubine_greet_action`
- `concubine_fragment_actions`
- `concubine_voyage_actions`
- `concubine_tianji_action`
- `concubine_heart_session`
- `concubine_reacquire_action`
- `concubine_external_observation`

For each, direct codec checks observed `{}` from database value `[]`, a
truncated `{"status":"unknown"` string, and a save/load round trip of Python
`None`. By contrast, the module record readers reject non-dict or malformed
records, and their block checks treat invalid state as unresolved. The codec
therefore removes the distinction those guards rely on.

A native greeting probe used the existing lifecycle fixture and real
`persistence.save_state()` / `persistence.load_state()` against temporary
SQLite. An invalid `[]` record was blocked before persistence. After reload
it became `{}`, lost that block and reached the greeting sender once.
The sender was an `AsyncMock`, not a Telegram client.

Original pre-fix output:

```json
{
  "before": {"block_reason": "invalid", "value": []},
  "after": {"block_reason": "", "value": {}},
  "saved": true,
  "loaded": true,
  "dispatch_started": true,
  "mock_dispatches": 1,
  "temporary_sqlite": true,
  "transport": "AsyncMock"
}
```

This proves a module dispatch-admission regression, not an observed live
duplicate or successful game request. The native probe has no surviving
legacy scalar hold; an independent scalar/pending/transport guard may still
block other states. Only greeting's downstream dispatch was exercised;
the other eight fields have confirmed codec loss, not individual live or
end-to-end dispatch verification.

## Reproduce Safely

Run from the candidate workspace. This imports the existing fixture, forces
temporary test state and a private network namespace, stubs live-guard backup
and restore, and never instantiates a real transport. The final safety
assertion failed before the fix. It passes on the verified candidate below,
with `after_block="invalid"`, `dispatch_started=false` and zero mock sends.

```sh
unshare --net env XIUXIAN_ALLOW_LIVE_TEST_DB=0 \
  /root/xiuxian-main-rebuild-venv-20260907/bin/python - <<'PY'
import asyncio
import json
import tempfile
from pathlib import Path

import tests.conftest
from pytest import MonkeyPatch
from model import persistence, state as state_module
from model.features import concubine_affinity_actions as actions
from tests.test_concubine_greet_lifecycle import env, send_greet, FIELD, ID

with tempfile.TemporaryDirectory(prefix="xiuxian-c4-owned-json-") as tmp:
    with MonkeyPatch.context() as mp:
        fixture = env.__wrapped__(mp, Path(tmp))
        h = next(fixture)
        mp.setattr(persistence, "_write_live_guard_backup", lambda *a, **k: None)
        mp.setattr(persistence, "_maybe_restore_live_guard_backup", lambda: False)
        try:
            assert persistence._db_conn is None
            h.identity[FIELD] = []
            with state_module.use_identity(ID):
                before = actions.block_reason()
            assert persistence.save_state()
            assert persistence.load_state()
            h.identity = state_module.get_identity_state(ID)
            with state_module.use_identity(ID):
                after = actions.block_reason()
            dispatched = asyncio.run(send_greet(h))
            print(json.dumps({
                "before_block": before, "after_block": after,
                "dispatch_started": dispatched,
                "mock_dispatches": h.send.await_count,
            }, sort_keys=True))
            assert before == "invalid"
            assert not dispatched, "invalid owned record reopened after reload"
        finally:
            if persistence._db_conn is not None:
                persistence._db_conn.close()
                persistence._db_conn = None
            fixture.close()
PY
```

## Acceptance Contract

1. Distinguish a legitimate empty record from malformed retained state at
   both serialization and loading, including null, wrong-container and
   truncated JSON. Preserve the corruption hold and available evidence;
   do not guess ownership or interpret missing evidence as failure.
2. Verify the hold survives unrelated saves, reload, startup, controls and
   native scheduler/reply paths. Confirm it cannot authorize a send or
   consume a result. Keep valid empty, pending and completed records working.
3. Cover all affected owning modules and adjacent authorization-bearing JSON
   fields. Do not change every generic JSON default indiscriminately: some
   are disposable views, and not every consumer understands an invalid marker.
4. Add permanent regression coverage for this real persistence boundary and
   rerun focused plus full integration tests after a bounded repair. This
   does not authorize a new shared recovery controller or production migration.

At the initial review, existing tests such as
`tests/test_concubine_gift_lifecycle.py:765` check invalid in-memory containers,
while greeting's reload test at
`tests/test_concubine_greet_lifecycle.py:681` uses valid records. Those tests
did not jointly prove invalid-record preservation through the codec. The
14467-test/1376-subtest report in the original handoff predates this negative
probe. The follow-up evidence below covers the scoped codec correction.

## Verified Repair

- An explicit eleven-field set now separates owned records from disposable
  JSON defaults. Besides the nine fields above, adjacent review confirmed the
  same loss for `second_soul_commands` and `tianti_commands`; both existing
  readers already reject malformed records and are covered by the repair.
- Saving `None` preserves JSON `null`, not a fabricated empty operation.
  Loading null, wrong-container or truncated JSON creates an invalid marker
  with the original `raw_json`. Invalid byte payloads retain `raw_bytes_hex`.
  Unrelated saves and repeated reload retain that evidence without nesting it
  again. Genuine object records, including `{}`, retain their existing meaning.
- Existing module validators reject the marker. No controller, blind retry,
  guessed ownership, schema column or runtime switch was added. This preserves
  an unresolved hold; it does not claim to recover the missing game outcome.
- Native temporary-SQLite tests cover all eleven readers. The nine concubine
  fields remain invalid through their real main/Tianji/heart/voyage controls,
  restart restoration, scheduler ticks and rejected greeting replies. UI
  snapshots do not expose the fixture secret from retained malformed text.
- Second-soul and Tianti tests enter their enabled native dispatch/scheduler
  paths and verify zero sends. A healthy greeting can still complete while
  either unrelated module retains invalid evidence. Valid empty and existing
  pending/completed lifecycle tests remain green.

| Verification | Result | Evidence |
| --- | --- | --- |
| Pre-fix regression after correcting the test's disposable `quiz_options` default | 196 failed, 36 passed | `/tmp/xiuxian-c4-owned-json-initial-corrected-20260915.xml` |
| Final new regression file | 234 passed; 5.06s | `/tmp/xiuxian-c4-owned-json-focused-final-20260915.xml` |
| Related concubine, second-soul, Tianti, persistence, controls and UI | 3736 passed + 87 subtests; 39.57s | `/tmp/xiuxian-c4-owned-json-related-20260915.xml` |
| Full regression | 14701 passed + 1376 subtests; 404.70s | `/tmp/xiuxian-c4-owned-json-full-20260915.xml` |

The full run began at 2026-09-15 15:45:06 UTC+8. Parsed JUnit comparison with
the previous full run confirms 234 added cases, none removed, all related
cases present in the full run, and zero failures, errors or skips. The
original native reproducer above was rerun and now reports zero mock sends.
Configured Ruff, scoped full-F checks, compileall, `pip check` and whitespace
checks pass. Tests use temporary state, `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and
private network namespaces; the full suite enables only namespace loopback
for its local HTTP fixtures.

The initial report at `/tmp/xiuxian-c4-owned-json-initial-20260915.xml` also
contained a test-only wrong expectation that `quiz_options` defaults to a
list. Its actual default is a dictionary; that fixture assertion was corrected
before the pre-fix rerun and no runtime behavior was changed to accommodate it.
The first repaired focused run had 232 cases; two native adjacent-dispatch
cases were then added and are included in the final focused/related/full runs.

At this follow-up, tracked `model/tests` diff SHA-256 is
`42908f33ed070a1a94ecd98ee93d1656784311e0424e16e8d0dce9b0dc0db191`.
It still excludes untracked files and is not a complete candidate fingerprint.
The new test file's SHA-256 is
`baa44246d8636a9f8272993fc73817bc6fc62a6542131775618647d395074821`.
The quiz-bank and UI-key tool retain their user-owned handoff hashes.

## Rift And Wanxin Follow-Up

The same persistence boundary was reproduced for the rift result ledger,
rebirth operation, and Wanxin observation. Malformed or wrong-container values
now retain invalid evidence. Rift result planning, Tianxing preparation, the
send-time operation check, and module status stop on an invalid ledger.
Wanxin normalization distinguishes an explicit invalid value from its no-arg
default factory and keeps scheduler/reply paths blocked without exposing raw
payloads in UI summaries. Disabling and re-enabling Wanxin retains owned
cooldowns, pending operations, and late-reply anchors.

The focused follow-up suite passes 108 cases. The related regression passes
4419 cases and 103 subtests. The isolated full suite passes 14809 tests and
1376 subtests with
zero failures, errors, or skips. Final reports:

- `/tmp/xiuxian-c4-rift-wanxin-focused-final-20260915.xml`
- `/tmp/xiuxian-c4-rift-wanxin-related-20260915.xml`
- `/tmp/xiuxian-c4-rift-wanxin-full-20260915.xml`

This remains an offline candidate and does not claim production validation or
whole-C4 acceptance.

## Still Open

This accepts the fourteen-field corruption-to-empty codec contract, including
the three fields added in the rift and Wanxin follow-up.
JSON columns outside the explicit set, historical records already normalized
to `{}` by earlier code, wider legacy ownership, migration and capacity still
need their own evidence. The fix cannot reconstruct discarded historical
payloads. A retained invalid record must be reconciled explicitly, not deleted
to restore activity. Whole-project Final Review and R67's per-role production
cultivation verification remain open.

No production, live database, configuration, listener, gameplay request,
skill, commit, push or deployment change occurred. The broader candidate's
migration/rollback constraints remain in force; this patch adds no SQL schema.
