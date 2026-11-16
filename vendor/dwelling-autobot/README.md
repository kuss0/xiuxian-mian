# Upstream dwelling assistant

`trial-bot.user.js` is the unmodified source from wxjerry/xiuxian PR #8.
`source.json` pins its commit, version, original path and SHA256. Runtime startup
checks that hash; the local adapter applies four exact in-memory policy changes
and refuses changed anchors.

The local adapter uses the assistant's task workflow, collision/navigation,
combat, puzzles and region travel. It does not enable its daily/trial batch
buttons. Task fishing waits for the application's existing fishing schedule;
unconfigured reward choices are deferred. NPC runs cannot start rewarded trials.
The native overworld entry waits up to 90 seconds for enemy models, verifies
the real entry area and stops if another instance is active.
After successful combat, active collection tasks in the same field share the
native corpse pickup routine, so their drops are collected during kill quests.

Before updating this source, review the upstream diff, update provenance, and
run `tests/test_qingxi_quests.py` plus `tests/test_qingxi_quest_policy.py` and the
related activity/fishing tests. Validate one identity's real receipts before
enabling additional participants. Do not replace local schedulers or merge the
rest of the PR as part of a source refresh.
