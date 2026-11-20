# Qingxi decoded flight-start rate-limit recovery

Candidate base `99f9e2b2493b90a3234a5d63de44689291d4997a`, branch `lab/qingxi-flight429-20261120`, isolated worktree `/home/xiu/wt/xiuxian-qingxi-flight429-20261120`. This candidate is not deployed. No game request, production state mutation, service restart or push was performed for this investigation.

## Current production evidence

The live SQLite database was opened with `mode=ro`. Current identity records map `301299112` to 吧唧 (`jfdffdddd`) and `8659059191` to wa2000 (`WalterWA2000`). The latest records still point to the original 2.0.53 result files; neither hold was produced by the new 2.0.78 worker.

- WA result: `/root/2026/qingxi-trial-v13-20261105/.runtime/8659059191/quest-cc4b3c22efc8401cb84fdb8e849f217d/result.json`, SHA256 `956e9c780810ccb5198418d7c6cd04f3cbfb046760602b8f69343577b0e8e4bd`.
- 吧唧 result: `/root/2026/qingxi-trial-v13-20261105/.runtime/301299112/quest-c45e256554da4770a907cd59ac579611/result.json`, SHA256 `3eb0fe4ba621c48f44e16add6f7874a04c12d13acd751c104b49275480a18c42`.

WA's only outstanding operation is `flight:start`, ID `flight:1`, sent on 2026-11-19 at 02:59:04.798 CST. Its decoded response at 02:59:04.912 is HTTP 429, `ok=false`, `error=rate_limited`, `Content-Type=application/json`, with no body parse error. All five other receipts are successful quest status reads, including three after that rejection. There are zero cast uploads, no field settlements or rewards, inactive combat, and identical memory/disk pending rows. The browser run ended and closed; its cleanup timeout is the queue drain waiting on the retained pending row.

The old heartbeat-only recovery accepts only pending flight `tick`/`stop`, and only transport/non-JSON top-level errors. It therefore never queries flight status for this decoded `start` rejection. A host-only unlock would also be insufficient: the next browser would reject the unchanged `flight:start` disk journal.

At the read-only inspection, normal scheduled refreshes had successfully read WA's quest status at 2026-11-20 14:34:36 CST and 吧唧's at 14:34:40 CST. Capture records show identity session loading followed by `quests:status`; no flight status was requested by the unmatched recovery predicates. These historic successful refreshes are not treated as fresh authorization for a later run.

## Narrow change

The new predicate recognizes only the complete decoded 429 scenario above: supported pinned worker version, transport/navigation policy, the exact top-level error, an ended run in the town safe area, inactive combat, zero casts, no settlements/rewards, a single matching flight-start pending row, identical memory/disk journals, exact request/receipt accounting and a fresh parent quest-status observation. Other writes, response-body failures, unknown errors/status codes and changed journals remain held.

The existing identity-verified `refresh` path reads fresh quest state first, then calls only `flight:status`. Recovery requires explicit `ok=true` and `mana.flightLocked=false`; failures or missing/ambiguous fields leave the hold intact. A successful review preserves the original result/error and archives the exact pending row. Save failure rolls back the proposed release in memory. The next browser receives that row only when its review evidence directory still matches the current held browser; the guard clears only a structurally identical `flight:start` journal entry. It does not replay `start`, `stop`, combat or rewards. A new live 429 still remains pending until reviewed through this path.

## 吧唧 remains held

The outstanding operation is `field-release-mwmea4fz-2-2qahwy` for `lantern:090`, sent on 2026-11-19 at 01:28:14.461 CST. The reply is HTTP 409/application-json, but body consumption raised `AbortError`; the semantic error code is unavailable. It cannot be reclassified as the older explicit `quest_too_far` rejection. This run also has a confirmed `field-engage` and two cast-use batches (one first read failure followed by the matching successful ACK). It has no recorded defeat, corpse pickup or reward request, but the final task snapshot was not verified and flight closure is absent.

The existing recovery for explicit `quest_too_far` is additionally limited to the older navigation policy 2. The actual result is policy 4 with a response-body failure, so that upgrade path must not be generalized to release it.

The official client snapshot in `/root/qingxi-npc-20261116/assets/dwelling-field-combat.js` shows `returnFieldEnemy()` releasing an engagement and `recoverFieldDeaths()` consulting authoritative spawn generation before recovering combat. This is a cached client snapshot, not current server implementation proof. Attempts to re-read the public static client scripts on 2026-11-20 returned HTTP 403; no authenticated game/API probe was attempted.

The normal capture retains only response shapes, and the local summarized record drops actual `quests.field.enemies` generation/HP and corpse entries. Thus the latest successful quest-status capture cannot prove this target's engagement state or identify a corresponding expiry. A separate read-only evidence improvement should preserve a bounded whitelist of target ID, generation, HP/respawn and corpse references from the already-performed status read, together with identity/time/flight status. If the API does not expose engagement closure, do not infer closure from elapsed time or empty corpses alone. No 吧唧 recovery condition is changed here.

## Actual 3D automation coverage

At inspection, NPC quest automation and gameplay are enabled only for 吧唧 and WA. That path launches Chromium/CDP and drives actual movement, flight, combat, gathering and puzzles. Both currently await review. The NPC browser guard continues to forbid trial writes and additional fishing.

The normal 长风/竹林 configuration is enabled for five identities with local daily limit 1, but its scheduler uses the HTTP flow in `changfeng_trial.py`. The separate `qingxi_trial_browser.py` route explicitly remains a manual single-browser canary and does not enable automatic browser trial runs. These enabled HTTP schedules must not be described as five-account 3D browser automation.

## Validation

`tests/fixtures/qingxi-flight-start-rate-limited.json` is a checked projection of the real WA result, with only the player ID changed to 42. Its provenance hash and each retained value were compared back to the read-only source file. Tests cover supported versions, polluted identities/receipts/journals, extra combat/reward writes, unknown HTTP/body variants, stale/failed status reads, locked/missing flight state, save failure, exact journal authorization and worker option forwarding.

```bash
TZ=Asia/Shanghai /opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_qingxi_flight_recovery.py tests/test_qingxi_quests.py \
  tests/test_qingxi_quest_policy.py tests/test_qingxi_quest_browser.py \
  tests/test_qingxi_review_occupancy.py tests/test_hunt_weekly.py
```

Result: **492 passed in 6.28s**, using normal temporary test databases. Changed Python files passed Ruff and `py_compile`; the actual policy and the generated 2.0.78 adapter with reviewed-flight options passed `node --check`; `git diff --check` passed. Real flight closure and actual resumed gameplay remain deployment-owner acceptance work, not an offline test result.
