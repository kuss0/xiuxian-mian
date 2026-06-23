# Fishing Lab 2026-06-23

## Scope

This is report-only lab work for the new fishing module. It does not register a
runtime module, connect a scheduler, add UI controls, send game commands, query
APIs, or restart production services.

## Real Text Evidence

- `data/messages/2026-06-23.log:5144-5146`: `.渔具铺` shows bait and pond rules.
- `data/messages/2026-06-23.log:5191-5192`: `.打窝` usage lists `米糠小窝|灵草窝|妖腥窝`.
- `data/messages/2026-06-23.log:5211-5213`: `.打窝 灵草窝` succeeded, but the reply only says `【打窝已成】` and does not disclose cost.
- `data/messages/2026-06-23.log:6648`: `.打窝 灵草窝` failed with `打窝失败，资源不足：item_fishing_bait_spirit_ricex3。`, proving `灵草窝` consumes three `灵米饵`.
- `data/messages/2026-06-23.log:6695`: `.打窝 米糠小窝` failed with `打窝失败，资源不足：item_fishing_bait_plainx2。`, proving `米糠小窝` consumes two `凡饵`.
- `data/messages/2026-06-23.log:6698`: `.打窝 妖腥窝` failed with `打窝失败，资源不足：item_fishing_bait_demon_bloodx2。`, proving `妖腥窝` consumes two `妖血饵`.
- `data/messages/2026-06-23.log:712-713`, `:746-747`, `:2609-2613`: `.鱼篓` is the safe manual before/after comparison surface. It shows rod ownership, fishing skill, daily rod count, current chum, bait counts, and fish counts.
- `data/messages/2026-06-23.log:671-675`: `.买鱼饵` usage is `用法：.买鱼饵 <凡饵|灵米饵|灵虫饵|妖血饵|月华饵> [数量]`.
- `data/messages/2026-06-23.log:743-745`, `:764-767`: `.买鱼饵 凡饵*5` is invalid; quantity is a separated argument, for example `.买鱼饵 凡饵 5`.
- User-provided WA sample at `2026-06-23 01:37`: `.钓鱼 青溪浅滩 灵米饵` starts `静候鱼讯`; `.钓鱼状态` reaches `鱼在试口`; `.试探咬饵` reaches `正口黑漂`; `.提竿` catches `银须灵鲢`; `.开鱼 银须灵鲢` is logged as `[2026/6/23 01:37] 韩天尊: 【剖鱼取机缘】` and returns `灵石x28、灵鱼肉x1、灵鱼鳞x1、清灵草x1、修为+39`.
- `data/messages/2026-06-23.log:214-216`, `:252-260`: `.买鱼饵 <鱼饵> [数量]` success replies use `【渔具铺】\n你购得 【凡饵】x10。` and `你购得 【灵虫饵】x1。`.
- `data/messages/2026-06-23.log:235-242`: fishing without bait replies `你的鱼篓中没有【灵虫饵】。可用 .买鱼饵 灵虫饵 购买。`

## Cost Decision

Known:

- `米糠小窝`: `item_fishing_bait_plain x2` = `凡饵 x2`.
- `灵草窝`: `item_fishing_bait_spirit_rice x3` = `灵米饵 x3`.
- `妖腥窝`: `item_fishing_bait_demon_blood x2` = `妖血饵 x2`.

## Automation Decision

First fishing automation must fail closed for unproven or unavailable resources:

- Do not auto-send any chum whose exact cost has not been proven by source or a resource-shortage failure sample.
- `.钓鱼` requires one selected bait; `.打窝` requires the selected chum bait cost above.
- If a local basket/storage snapshot proves bait is insufficient, block unless `auto_buy_bait` is explicitly enabled.
- If `auto_buy_bait` is enabled, the command plan prepends `.买鱼饵 <鱼饵> <配置买饵数量，默认8；若缺口更大则按缺口>` before `.打窝` and `.钓鱼`.
- `.试探咬饵` is controlled by `auto_probe`; default is off because the benefit is still uncertain.
- Parser/state work may observe `.渔具铺`, `.鱼篓`, `.买鱼饵`, `.钓鱼状态`, `.试探咬饵`, `.提竿`, `.开鱼`, and chum replies from local text only.

## UI Configuration Contract

Fishing strategy must be operator-selectable per identity, not hardcoded:

- Fish pond: `青溪浅滩`, `灵眼寒潭`, `乱星海礁`.
- Bait: `凡饵`, `灵米饵`, `灵虫饵`, `妖血饵`, `月华饵`.
- Auto chum: disabled by default.
- Chum type: `无`, `米糠小窝`, `灵草窝`, `妖腥窝`.
- Auto buy bait: disabled by default.
- Auto probe bite (`.试探咬饵`): disabled by default.

If auto chum is enabled but the selected chum cost is not proven, the command
plan must block before sending both `.打窝` and `.钓鱼`.

If the selected bait or chum bait is missing from the local basket/storage
snapshot, the command plan blocks unless auto buy bait is enabled. With auto buy
enabled, the planner buys only the missing bait count first.

## Chum Cost Probe Protocol

Coordinate with an operator before any live spend:

1. Pick one identity with `当前窝料：无`.
2. Run `.鱼篓` and save the reply.
3. Run exactly one `.打窝 灵草窝`.
4. Run `.鱼篓` immediately again.
5. Compare bait counts and `当前窝料`.
6. If `.鱼篓` does not expose the consumed material, use a controlled shortage
   sample from a low-resource identity to obtain the internal `item_keyxN`
   failure text.

## Lab Helper

`model.features.fishing` is report-only and currently exposes only:

- `parse_chum_shortage(text)`
- `parse_fishing_status(text, auto_probe_enabled=False)`
- `parse_fishing_basket(text)`
- `parse_buy_bait_result(text)`
- `parse_missing_bait_reply(text)`
- `parse_fishing_catch(text)`
- `parse_open_fish_result(text)`
- `get_known_chum_cost(chum_name)`
- `fishing_bait_name_for_item_key(item_key)`
- `decide_chum_send(chum_name, auto_chum_enabled=False)`
- `normalize_fishing_config(pond, bait, auto_chum_enabled=False, chum_name="", auto_buy_bait_enabled=False, auto_probe_enabled=False)`
- `plan_fishing_commands(config, bait_inventory=None)`

It is intentionally not imported by runtime scheduling.
