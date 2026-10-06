# Upstream Checkpoint: October 6

Read-only SSH fetch at 11:17 CST. No upstream merge, build, restart, migration,
feature switch change, or game request.

## Versions

- wxjerry `origin/main`: unchanged at `aa9dba29` (September 29).
- wxjerry `origin/xuruodeaiban`: unchanged at `cd2a2e64` (July 17).
- Rust `origin/main`: `2ec7592b` -> `3c39edc8`.
- Rust's checked-out `agent/codex/thunder-dps-dungeon` branch remains unchanged
  and clean. Only remote references were updated.

## Rust Delta

The new mainline merge adopts the previously inspected removal branch:

- `1de15cfd`: remove the complete World Boss lane.
- `afad63d7`: remove fishing, fish-opening, and fishing-specific session support.
- `1a6f1c7f`: migration v19 deletes their behavior records and drops
  `world_boss_round`.
- `3c39edc8`: merge PR #245.

The range changes 72 files, adding 1,563 and deleting 48,578 lines. This is
feature retirement, not improved native fishing or a Boss verification fix.
Focused review of hunt, pagoda, sect-farm and daily/wild behavior changes found
interface/comment/test adjustments for removed fishing session timing, not a
new protocol fix to transplant. This is not certification of every changed line.

## Local Decision

Do not copy the removal or v19 migration. Local native fishing is active and
its settlements/recovery remain required. Local Boss availability continues
under the user's existing per-identity configuration; fetching Rust does not
authorize opening or closing it. Do not delete its history.

Keep the September 30/October 2 decisions on exact button structures,
identity-bound read-only Tianji panels, separate throttle evidence and
same-chat reply ownership. No new executable changes resulted from this fetch.
wxjerry's test-untracking policy remains rejected locally.

## Concurrent Observation

Main/observer/watchdog remain active without restart. At 11:19:58 the observer
reports only the two existing held notification batches; game pending and
recent MiniApp failures are zero. Today's measured 60-second capture peak is
39/90. The 11:01 external-player quiz timeout stayed local with no new receipt
or summary entry. Held notifications were not replayed.

The first diagnostic invocation used `--limit 15 --window-sec 7200`; these
arguments mean a 15-request threshold over two hours, not an output limit and
lookback filter. Its saturation result is not a production 90/minute violation.
The corrected invocation used `--limit 90 --window-sec 60`; no rate-control
change or alert suppression was made.
