# Qingxi bounded field-status evidence

This additive candidate is based on `2a4519d266981b9f2f5bc9c6932236b13ab18de3`. It adds diagnostics only; no recovery predicate, request, scheduler or gameplay permission changes.

The existing `run_once()` identity/session check and request wrapper still run first. After an already-performed successful `quests:status` response with a valid quest-state structure, the wrapper stores `record.field_status_evidence`. The existing protocol/status persistence saves it. Responses with mismatched identity or failed status do not create new evidence. No raw token, initData, session, full enemy roster, loot item or engagement bearer token is saved.

## Saved fields

- `verified_player_id`: the identity verified by the current owning public session; any response player ID must already have matched it. This does not invent a player ID in a response that omits one.
- `observed_at`: time of the successful status observation.
- `pending_source`: current browser result, saved browser digest when its result file is unavailable, or host pending operation. Only pending enemy IDs are selected.
- `field_active`: the exact server boolean, otherwise `unknown`.
- `field_engagement`: an exact null/boolean when supplied; strings are recorded only as present/type, and objects retain only active, enemy ID, generation and expiry. Raw engagement/session tokens are excluded. Missing or unsupported shapes are `unknown`.
- `targets`: at most eight pending targets, each with roster state, generation, HP, respawn time, active/engagement evidence and at most eight matching corpse references. Corpse references retain only ID, enemy ID, generation, expiry and looted boolean. Truncation is explicit.

The latest inspected capture shape contains `quests.field.enemies` and `regions`, without active or engagement fields. Existing fixtures and the official cached client use enemy `id`, `generation`, `hp`, `respawnAt`, and corpse `enemyId` links. The summary uses those names and explicitly marks missing fields unknown. It does not infer engagement closure from an absent roster entry, zero HP, empty corpses, elapsed time or a new generation. Duplicate target rows also leave numeric/engagement details unknown rather than selecting an arbitrary row.

For the current 吧唧 hold, the next normal refresh can therefore retain actual values for pending target `lantern:090` if returned by the API. Missing engagement state remains unknown and the original 409/body-read failure remains held. The summary is not consumed by release logic and is not a completion receipt.

## Validation

New isolated tests cover only-target selection, identity/observation provenance, redaction, explicit unknowns, malformed/duplicate/absent data, zero values, finite-number validation, target/corpse limits and the real existing refresh path. The integration test verifies exactly one existing status operation, persistence of the summary, unchanged held state and unchanged original browser result. Failed status and mismatched identities do not create the summary.

```bash
TZ=Asia/Shanghai /opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_qingxi_field_status_evidence.py tests/test_qingxi_flight_recovery.py \
  tests/test_qingxi_quests.py tests/test_qingxi_quest_policy.py \
  tests/test_qingxi_quest_browser.py tests/test_qingxi_review_occupancy.py \
  tests/test_hunt_weekly.py
```

Result: **514 passed in 8.29s** using the normal temporary test databases. Ruff, Python compilation and `git diff --check` passed. No production state was modified and no game/API request was issued during this candidate task. Deployment and inspection of the next ordinary refresh belong to the deployment owner.
