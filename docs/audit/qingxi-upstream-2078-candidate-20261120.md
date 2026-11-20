# Qingxi upstream 2.0.78 adapter candidate

Candidate base `87aa0ec6`, isolated worktree `/home/xiu/wt/xiuxian-qingxi-2078-20261120`, branch `lab/qingxi-upstream-2078-20261120`. It is not deployed.

Source `wxjerry/xiuxian` `abbb2c54ef4e67ba78fce65918a5e98eba8e397b`, PR #10, `dwelling-autobot/trial-bot.user.js` version 2.0.78. The vendor source is byte-for-byte the upstream source. `source.json` pins commit, version and SHA256. The launcher records the pinned version in each result. Review predicates admit 2.0.53 and 2.0.78 while requiring the same per-case identity, pending receipt, combat, reward, freshness and flight evidence.

The candidate retains the local Linux Chromium/CDP worker and fetch policy. The new source no longer matched the navigation-error anchor used to capture its failure context. The adapter now matches the 2.0.78 expression that includes `flightLastFailure` and inserts the existing `navigationFailure` evidence. The `takeoffGround` replacement is narrowed to a fallback inside upstream's function. It preserves 2.0.78 outdoor, roof, landing and raised-surface checks, while allowing the verified native `canFly` test at the current actor height to handle the documented case where walking is blocked but takeoff is valid.

The request guard continues to deny writes to `/trial` and fishing requests. The worker invokes `enter`, `inspectQuests`, `mainline` and `stop`. It does not call the upstream daily, drill, trial-batch or monthly-palace entry points. The Electron launcher is deliberately not imported into the Linux worker.

Validation on the candidate included pinned-source injection, the updated navigation anchor, native takeoff and roof/landing geometry, Qingxi worker lifecycle, browser request guards, review occupancy and weekly hunt integration. After identity verification, trial actions `start`, `act`, `finish`, `claim` and `retry` still cannot reach transport; read-only trial status remains available. Versioned recovery tests admit only supported versions with unchanged identity/write evidence and a fresh explicit flight-closed receipt. Unknown `field-defeat` operations, mismatched identities, unsupported versions and still-locked flight remain held.

Final regression command, run from the isolated candidate worktree on 2026-11-20:

```bash
TZ=Asia/Shanghai /opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_qingxi_quests.py tests/test_qingxi_quest_policy.py \
  tests/test_qingxi_quest_browser.py tests/test_qingxi_review_occupancy.py \
  tests/test_hunt_weekly.py
```

Result: **444 passed in 6.04s**. Tests used the normal temporary test database; no live-test database override was enabled. Earlier 374- and 425-test checkpoints preceded the added safety regressions and are superseded by this final run.

Static validation also passed:

```bash
/opt/xiuxian-main/.venv/bin/python -m ruff check \
  model/features/qingxi_quests.py tools/qingxi_quest_browser.py \
  tests/test_qingxi_quests.py tests/test_qingxi_quest_policy.py \
  tests/test_qingxi_quest_browser.py
/opt/xiuxian-main/.venv/bin/python -m py_compile \
  model/features/qingxi_quests.py tools/qingxi_quest_browser.py \
  tests/test_qingxi_quests.py tests/test_qingxi_quest_policy.py \
  tests/test_qingxi_quest_browser.py
node --check vendor/dwelling-autobot/trial-bot.user.js
node --check tools/qingxi_quest_policy.js
git diff --check
```

The generated `browser_script(42)` passed `node --check` from a temporary file. SHA256 validation and a byte comparison against `git show abbb2c54ef4e67ba78fce65918a5e98eba8e397b:dwelling-autobot/trial-bot.user.js` passed; SHA256 is `8bf40695e93a9640f7c2626560326251a45c257b7e6fc9c235b2c9ba7aa59b72`.

No game request or production state mutation was performed. An earlier offline replay read existing production records through SQLite read-only mode and kept uncertain saved evidence held; it did not authorize gameplay recovery. Recheck current held identities' saved `result.json` files before deployment if their state has changed.

Production has not accepted 2.0.78, its browser outcome formats, or Linux startup behavior yet. The new upstream calls `finishInCave(owner)` after normal completion and errors. Existing conservative recovery predicates can require `world.current == 'yue'` and a town position, so a cave-return outcome may remain held pending extra read-only review. Those predicates have deliberately not been broadened. The first real run must inspect the final report/home/scene state, fresh final quest refresh and explicit flight closure. Keep any participant held until its existing release predicates are satisfied; a successful offline suite does not establish real-game completion.
