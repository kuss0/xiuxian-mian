# Q15 native damage export candidate

Isolated worktree `/root/xiuxian-q15-damage-diagnostic-20261122`, branch `lab/qingxi-q15-damage-diagnostic-20261122`, base `49e880b6`. This candidate is uncommitted and not deployed. Production, the active native-warmup release worktree, vendor sources, m19, account records and services were not changed. No browser or game/API request was started; only the two focused offline test files were run.

The worker now retains a bounded, typed copy of the native assistant's existing `report.damage` in `result.json`. That ring previously existed in the page but was omitted by `SNAPSHOT`, so the q15 low-health stop had only brief text logs after returning home. The candidate adds no low-health hook, sampler, native command, timer or strategy change.

## Source and evidence

The pinned vendor `trial-bot.user.js:1286-1303` already records HP decreases, retaining at most 20 entries; `report():5324` exposes the last 12. Its nearest three visible living enemies are candidates, not confirmed damage sources. `action` is the current action kind when the loss is sampled, not the previous action or its clip. `myPos()/position3():528-536` projects position to `[x,0,z]`; `dist():541-543` measures the horizontal XZ distance. The exported ring cannot establish actual flight height or the exact engine hit instant.

The user reference package's `tools/longwind_trial_bot.js:614-623` has a different `hitCtx`: it appends up to 400 events and then stops, selecting the nearest non-ranged swinging candidate while recording the prior player action/reason. It lacks HP before/after, a wall timestamp and the original defense-command return. This candidate does not import that loop or change the current quest/m19 behavior.

The sanitized evidence under `/root/xiuxian-qingxi-7plus6-release-20261121/fight-timeout-diag-1855` establishes only these facts:

- `q15-q19-live-evidence.json`: WA received the original-seal pickup and q15 turn-in. The corresponding native run records successful defeats of `lantern:085/083/084/090` before that pickup.
- `baji-q15-low-health-stop-evidence.json` and `baji-q15-live-samples.jsonl`: Baji began with full 9744 HP, engaged `lantern:090`, stopped below 30% HP while the message reported the target at 35%, returned safely, and received a successful release. It has no defeat or seal-pickup receipt. Its retained recent logs show two losses of 3791; the supplied evidence explicitly lacks a target/nearby-enemy snapshot at the loss.
- The native ring was not retained by either old result, and the five-minute timeout diagnostic did not trigger for Baji's earlier low-health stop. These sources cannot identify a particular enemy move or explain why a defense failed. No historical damage event is reconstructed by this candidate.

## Export contract

`SNAPSHOT.report.damage` is always an array when a native report exists. Missing/malformed rings become empty. Only the last 12 entries and first three native candidate enemies per entry are retained. Nonfinite or incorrectly typed numeric values become `null`; numbers are not normalized, rounded or converted from strings. Positions must contain exactly three finite numbers and are copied. Unknown fields, including Mu-only behavior/dodge objects, bearer-like fields and nested objects, are omitted.

| Fields | Native unit / bound |
| --- | --- |
| `time` | `performance.now()` monotonic milliseconds in this document |
| `hpBefore`, `hp`, `hpMax`, `mana`, `stamina`, `hpPotions` | Native points/counts, unchanged |
| `hpPotionCooldown`, enemy `time` | Native seconds; enemy time is its current state/animation clock |
| `position`, enemy `distance` | Ground projection `[x,0,z]`; XZ horizontal metres |
| `action`, `keys` | At most 32 characters each |
| `arena`, row `phase` | At most 40 / 120 characters |
| `treasureFx`, `shots` | At most eight strings, each at most 40 characters |
| enemy `id`, `name`, `role`, `move`, `state` | At most 100 / 80 / 40 / 40 / 40 characters |
| enemy `phase`, row `wave` | Finite native numeric values or `null` |
| enemy `telegraph` | Native boolean or `null` |

The rest of the existing snapshot remains unchanged. It still samples at the existing worker calls. The native event ring does not include original guard/dodge return values, a player action clip, or per-enemy spawn IDs; this export cannot supply those facts. It provides the existing HP-loss context for the next separately authorized run without changing decisions.

## Validation and rollback

`python3 -m pytest -q tests/test_qingxi_damage_snapshot.py tests/test_qingxi_quest_browser.py` passed: **12 tests**, 0.30 seconds. They execute the actual snapshot expression in Node with a frozen report and command/network stubs, covering retention order, native units, bounded arrays/strings, invalid values, omitted nested fields and absence of commands/network. Test damage/move data is explicitly synthetic serialization input, not WA/Baji hit evidence.

`/opt/xiuxian-main/.venv/bin/python -m ruff check tools/qingxi_quest_browser.py tests/test_qingxi_damage_snapshot.py` and `git diff --check` passed. The system Python has no Ruff module, so the existing application virtual environment supplied that lint check; no dependency was installed.

A one-off offline revert check extracted the baseline `SNAPSHOT` from `HEAD` with Python AST and ran the same new serializer assertion: it failed at the absent `report.damage` field as expected. A separate AST comparison confirmed every Python node outside the `SNAPSHOT` assignment is identical to base `49e880b6`. The test stubs and baseline expression existed only in a temporary directory; the worktree was not reverted or mutated by that check.

An independent static review found no blocking issue. No full suite, real GPU, live browser, real encounter, commit or production operation was performed. To withdraw this candidate, discard only the isolated snapshot change and its new test/report; there is no persistent game/account/config change to undo. Any future adoption must first be rebased/reviewed against the then-current release candidate.
