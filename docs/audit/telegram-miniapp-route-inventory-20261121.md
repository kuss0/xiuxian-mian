# Telegram gameplay to MiniApp/desktop route inventory

Read-only source and log inspection, 2026-11-21. No Telegram or game requests, login, browser run, configuration changes, or production edits were made. This is a routing inventory, **not** proof that the desktop path is safe/equivalent.

## Observed Telegram command volume

Counts are exact `.sent` records in local message logs from 2026-11-15 through 2026-11-21, aggregated by command prefix. They are all identities combined; they do not identify which account or schedule produced an action. They are useful for ranking only, not a rate or quota measurement.

| Command family | 7-day sent count | Current code path | Desktop/MiniApp candidate | Equivalence evidence |
|---|---:|---|---|---|
| `玄骨遗府` actions (`.玄骨行动`, `.玄骨分工`) | 116 combined | Group-targeted commands; `model/app_xuangu.py:74-87,116-130` | Desktop has separate `/party` and `/activity` operations and activity UI | **Unproven.** No mapping from Xuangu roster, role, stage, deadlines, or rewards to desktop activity was found. |
| `阴罗幡` (`.我的阴罗幡`, `.血洗山林`) | 120 combined | Current command families in sent logs | Desktop generic handout can submit dot-commands | **Unproven.** No inspected desktop API or current MiniApp flow establishes the same uses, cooldown, reward, or team semantics. |
| Tianxing (`.推命`, `.改命`) | 113 combined | Group command route is present in `model/features/wild_training.py:390-391`; additional protective/timeline behavior exists in this module | Generic handout accepts commands and `replyTo` | **Unproven.** No current desktop/ MiniApp endpoint with matching Tianxing effects/cooldowns was found. Preserve message/reply context until demonstrated. |
| Paired play (`.双修`, `.稳`, `.共历心劫`) | 370 combined | `model/features/paired_activities.py:99-119,128-163` serializes two identities and waits for reply/start outcomes | Desktop generic command plus reply references/revisions | **Plausible transport only.** Desktop can quote a prompt, but there is no proof this replaces the two-identity state machine, pairing, rewards, and cooldowns. |
| Small world (`.小世界` status; `.收割香火` action) | 55 `.小世界` status messages; harvest count is not implied | `.收割香火` remains a direct Telegram action at `model/features/small_world.py:1666-1695`; command has a MiniApp-preferred/legacy-fallback classification in `tools/legacy_route_report.py:90-95` | Desktop has a `world` panel (`analysis/frontend/client-v1.js:11`) | **Unproven.** A panel name does not show that its harvest action has identical quota, settlement, reward, or cooldown. |
| Group hunt weekly (`.登船`, `.起锚`, `.探路`, `.出手`, `.护船`, `.祭符`, etc.) | 43 combined | Group-targeted, deadline-sensitive sends at `model/app_hunt.py:48-61,104-111` | Desktop `/party` and `/activity` endpoints | **Not established as equivalent.** Generic parties exist, but no evidence connects desktop rooms to the same hunt ship, synchronized members, deadline, or rewards. Do not move urgent team actions based on this API resemblance. |
| Zhengmo (`.正魔大战`, `.出征`, etc.) | 34 `.正魔大战` prefix messages | Group-targeted serialized run at `model/app_zhengmo.py:61-82` | Desktop generic handout | **Unproven.** No desktop activity or MiniApp route with matching daily cap and faction result was identified. |
| Qingxi and daily quests | No relevant generic text prefix count derived here; real workflow uses native browser/game actions | `model/features/qingxi_quests.py` and `tools/qingxi_quest_browser.py` | Desktop embeds the game panel/page; hub can open a panel after a command result | **Same native game surface, different automation proof.** No desktop hub operation was shown to run/settle all 7 rewards or 6 trials. Keep the existing native browser route and server receipts. |

The prefix scanner also found already-modernized activity families: 7-day logs had no `.野外历练`, `.闯塔`, `.世界boss`, or old fishing follow-up commands in the matching direct-send prefix inventory. The code explicitly calls wild training MiniApp-only now (`model/features/wild_training.py:1-9,804-874`), Tower schedules its production MiniApp flow (`model/features/tower.py:333-366`, `tower_miniapp.py:288-315`), and the older world-boss command route is catalogued `miniapp_only_auto` (`tools/legacy_route_report.py:90-108`). Fishing has a native public MiniApp flow (`model/features/fishing_dwelling_runtime.py:101-147`; `fishing_dwelling_miniapp.py:282+`). These facts support continuing those existing flows; they do not establish that desktop commands give equivalent rewards.

## What the desktop code actually establishes

The `0.2.17` extracted frontend uses base `/api/desktop/dwelling/hub` and `POST .../command`, with a UUID `operationId`; it persists the pending operation before dispatch and has an `/operation` lookup path for an uncertain result. It can attach `replyTo` and `replyRevision` to a command (`analysis/frontend/client-v1.js:4,15-22,117-134,545`). The client also has `/activity` and `/party` calls (`analysis/hub-api-summary.json`).

This proves a **generic transport candidate** and a query mechanism, not that every TG command is accepted, privately scoped to the same bot/game rules, avoids Telegram side effects, or shares cooldown/quotas. The official `RESEARCH.md` says no actual authenticated handout command, account linkage, cross-client conflict, or gameplay equivalence was validated. Desktop uses Cookie/CSRF authentication, unlike the existing Telegram WebView `initData` path; the two credential models cannot be substituted.

## Safe migration order for later validation

1. First investigate generic, non-deadline personal commands such as `.小世界` read/status and selected paired/Tianxing prompts in one companion account. Confirm Telegram login maps to the exact existing player before sending anything.
2. For each candidate, compare server-owned rewards, uses, cooldown/reset behavior, replies, cross-client effects, and pending-operation recovery against existing Telegram records. A matching command name or a successful HTTP response is insufficient.
3. Keep existing phaseful/group commands (`玄骨`, hunt weekly, Zhengmo) and Qingxi native combat on their current tested paths until desktop semantics and team/deadline behavior are proven.
4. A validated desktop transport must preserve identity ownership, idempotency/operation reconciliation, one global MiniApp request budget where applicable, and a rollback switch. The actual login and action validation remain open; do not cut over on this inventory alone.
