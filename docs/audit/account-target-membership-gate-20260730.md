# Account Target-Group Membership Gate, Gates A-C

Date: 2026-07-30

## Scope

This audit compares the Rust target-group membership gate with the local
personal-account plus channel-identity runtime. Gates A/B established the pure
classification and projection contract. Gate C wires that contract into a
dedicated persisted health record, cold-path probes, group-command blocking,
UI/health visibility, and automatic recovery without disabling accounts,
identities, module configuration, or public MiniApp workflows.

## Absorb Decision

The Rust rule is useful but cannot be copied as whole-account disablement.
Locally, a login account that cannot speak in the game group can still own
channel identities that use the public dwelling MiniApp.  Marking that account
offline would incorrectly stop those HTTP workflows.

The accepted local contract is:

1. `USER_NOT_PARTICIPANT`, `CHANNEL_PRIVATE`, and `CHANNEL_INVALID` are the only
   deterministic negative results.
2. Flood wait, Telegram internal errors, transport failures, disconnects, and
   entity-cache misses are `unknown`; they must not disable anything.
3. A deterministic negative may block group-command sends for every identity
   explicitly mapped to that login account, including its personal identity and
   channel send-as identities.
4. Identity rows, module settings, timers, MiniApp state, and account login data
   must be preserved.
5. Public MiniApp eligibility is independent from target-group membership.

## Gate A/B Implementation

`model/account_membership.py` now provides:

- exact error classification;
- a read-only `channels.getParticipant(self)` probe;
- account-to-personal/channel identity projection;
- a pure gate decision that blocks only on `not_member`.

Focused tests cover deterministic negatives, Telegram `RpcCallFailError`, flood
wait, entity-cache misses, legacy chats, and mixed personal/channel mappings.

## Gate C Implementation

Gate C satisfies the following production contract:

- One probe is scheduled per login account, never per identity and never inside
  the hot send queue. Confirmed members are rechecked every six hours;
  `unknown` and `not_member` records retry after 15 minutes.
- `account_target_memberships` is persisted independently from account-online,
  identity, and module settings. This condition never calls
  `mark_account_offline()`.
- A matching deterministic negative blocks only group commands with the
  definitely-unsent reason `account_not_in_target_group`. The action guard is
  closed without consuming a business cooldown or starting reply recovery.
- Public MiniApp eligibility remains independent and follows its existing
  public-entry identity rules.
- A known negative survives transient rechecks, while a confirmed `member`
  result restores group-command eligibility automatically.
- Changing the configured game group invalidates the old-group conclusion; a
  previous negative is not inherited by the new target.
- Account API snapshots, the logout panel, and `health_observer` expose the
  status, latest probe result, reason, and check time without changing user
  configuration.

## Validation And Production Evidence

- Focused membership/runtime/API/health tests passed.
- Full regression suite: `3467 passed, 536 subtests`.
- UI smoke: `17/17`; Python compile, JavaScript syntax, and `git diff --check`
  passed.
- Production startup probe at 2026-07-30 08:54 CST classified all four login
  accounts (`301299112`, `7538826434`, `8574677796`, `8659059191`) as `member`.
  Account `301299112` correctly projects to its 21 configured identities; the
  other accounts project to their own identities.
- Post-deploy service health and safety watchdog were both `ok`, pending queue
  was empty, and `xiuxian.service` reported zero restarts.

Gate C is implemented and production-validated. It does not take ownership of
account login health or public MiniApp scheduling, and it does not broaden the
set of errors that can block group commands.
