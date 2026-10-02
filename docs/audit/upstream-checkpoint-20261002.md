# Upstream Checkpoint: October 2

Read-only SSH fetch and focused source comparison, not a whole-upstream merge.

## Versions

- wxjerry `origin/main` remains `aa9dba29`; no new changes since September 30.
- Rust `origin/main` advanced from `b74c2ed1` to `2ec7592b` (September 24).
  Its working branch `agent/codex/thunder-dps-dungeon` remains untouched and
  clean. Only remote references were fetched.
- Rust's separate `origin/agent/claude/remove-fishing-world-boss` branch is
  `1a6f1c7f`. It removes fishing/Boss code and DB records. This is not a native
  fishing upgrade and is not adopted; local fishing is still required and
  local World Boss remains disabled without deleting its history.

## Focused Decisions

1. Rust `65e1d53e` handles integer-to-boolean flags. Local
   `model/ui.py::_tianjige_bool_label` already supports bool and explicit 0/1
   display values. The affected Rust top-level API flags are not equivalent to
   the local native MiniApp completion/ownership contract. Do not import Rust's
   broader rule that strings such as `"0"` and arbitrary arrays count as true.
2. Rust `0588ee07` separates external-entry throttles from proxy penalties.
   Local `_miniapp_result_extra` and `_remember_cave_public_shared_limit`
   deliberately propagate `external_action_rate_limited` into a shared public
   hold. Before changing that scope, obtain server documentation or natural
   same-window evidence distinguishing entry, identity, account and IP limits.
   The error name and another client's interpretation alone are insufficient.
   Keep explicit Retry-After and the global 90/minute budget. The saved shared
   deadline was already expired at this checkpoint; no current global hold was
   cleared, and no traffic was generated to test the rate limit.
3. Rust dual-chat updates require `(chat_id, msg_id)` ownership and same-chat
   recovery. Local routing already implements those boundaries, including
   pending recovery and scoped background operations. No second routing layer
   or Rust scheduler was copied.
4. Rust Boss ticket timing, compensation, account concurrency and opt-in
   changes are not enabled or transplanted into a disabled local feature.
   Reopening it still requires explicit authorization and event acceptance.

The remaining Rust diff is broad (177 files), including documentation and
architectural changes. This note does not certify every changed file or claim
all upstream functionality has been absorbed. No runtime changes resulted
from this comparison.

## Live Evidence

- Today MiniApp captures peak at 43 requests per minute, below 90.
- Fate cards: all 24 latest states are settled; the 03:15 daily report recorded
  cultivation +1886 and trace +72. Trial wave two completed 12/12 identities,
  36 rounds and trace +516 at 05:12:51.
- Main supervisor/worker remain 145376/145387 after the explicit 10:24 fishing
  deployment. Watchdog and observer remained active; current warnings refer
  to the two earlier fishing checkpoint failures, not a new runtime incident.
- Both incense-refinement switches and World Boss remain off. Channel group
  sends stay frozen, inventory reads manual-only, and Attempt shadow-only.

Open acceptance is still tracked in the native-fishing and R2 audit notes.
The native gift bridge remains only on its Lab branch, not deployed.
