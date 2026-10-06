# Public YuanYing Sect Strategy

Base: `671a1494`. Lab: `/root/xiuxian-public-yuanying-sect-20261006`.
Candidate only: not merged or loaded into production.

## Evidence

The native scheduler already selects `get_yuanying_launch_command(identity)`.
The public-entry path instead hard-codes the out-of-body command and its
protocol allowlist omits the YuanYing-sect retreat command. An idle sect
identity would therefore use the wrong established strategy.

Lpprceqei is currently already in a sect retreat. Its 10:23 status confirmed
the retreat with accumulated cultivation, and its next status read is 18:23.
No manual recall or active probe is justified for this investigation.

Official native success wording is anchored to command/reply 1285548/1285549
in chat -1002083016447, October 6 12:44:15/12:44:18 local observation time.
The same wording appears in October 2, 4 and 5 retained messages. These are
parser fixtures, not proof that a production MiniApp retreat request succeeded.

## Candidate Contract

- Reuse the existing sect strategy in the public-entry owner. Freeze the
  selected command across reads; a strategy change before dispatch cancels
  the operation. After dispatch, a real response still belongs to its original
  command even if the sect changed in the meantime.
- Allow the sect command only as a reviewed mutation, never as a generic
  read-only command. Both YuanYing mutation variants remain one HTTP attempt
  without transport retries, with the existing unknown/429 classification.
- Persist the actual command in the existing intent. Unknown sect retreats
  require a verified active-retreat status, not a cloud-travel countdown.
  Legacy intents lacking a command retain their old out-of-body meaning;
  they are not reinterpreted from the current sect. Malformed commands hold.
- Parse the exact observed retreat-start sentence and the complete active
  retreat status line. Contradictory countdown/retreat panels and ended/failed
  suffixes do not authorize completion. An active retreat is only observed;
  no recall or re-entry is issued. Eight hours is the existing observation
  interval, not a newly asserted game cooldown.
- Preserve the existing identity/player/account/entry guards, resource
  settings, ordinary out-of-body route, summary grouping and no-resend hold.
  `reconciled_running` still means the current expected running condition is
  observed, not attribution of a historical unknown POST to a specific reward.

## Audit Findings

Initial regression against the old implementation: 6 failed, 4 passed.
Failures demonstrate the missing allowlist entry, wrong command, wrong
unknown-result postcondition and missing strategy recheck after awaits.

The maintainer's second pass additionally found an existing persistence hole:
the public YuanYing caller ignored a failed save of its dispatch intent. It
now requires an explicit True acknowledgement before mutation dispatch. False,
None or an exception returns persistence_pending with action_dispatched=false
and retains a conservative in-memory hold/recheck time. It does not claim the
game consumed anything, retry the action or roll back another identity.
Repairing storage and resolving a retained persistence hold remains separate
from proving a game result; this is not a new general recovery mechanism.

Only the pre-dispatch save exception is caught at this boundary. Exceptions
at other persistence points retain their existing behavior. The lifecycle
fixture now explicitly acknowledges successful saves instead of relying on a
truthy Mock, so the admission guard is actually tested.

## Validation

- Focused final candidate: 667 passed, 7 subtests, including UI entry wiring,
  phaseful native behavior, wrong-player and account binding, cancel/late
  results, old unknown records, command corruption, contradictory status,
  no recall, no repeated mutation, 429 and storage-ack failures for both modes.
- Intermediate full run: 16242 passed, 1461 subtests, 459.63 seconds. It began
  before the persistence-ack fix and is not final acceptance.
- Final frozen full run: **16248 passed, 1461 subtests**, 448.78 seconds.
  JUnit: `/tmp/xiuxian-public-yuanying-sect-ack-final-20261006.xml`.
  Supplementary public-entry cancellation/ownership/UI/retreat/small-world
  regression: 569 passed. All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`;
  no production mutation or fault injection.
- Ruff, py_compile and diff checks pass. Initial and second reviews are by the
  same maintainer, not an independent third-party review.

Final-run source hashes:

```text
efa7d6140568fb62bb3c9eb5a3f0184f65efeed676b1520ed7249e9d59b14a0f  model/features/cave_treasure_miniapp.py
34ba867c04cca86bed06bf02e1307204f65a7dece88fb2bcd958b21c5f631932  model/features/cave_treasure_runtime.py
73fb69fa1830be4d547d0a31bdc7ed1b577bd5aa2fa906e01adad4561291600e  tests/test_cave_yuanying_sect.py
e239a1b5f0b3763e3a4fed5d9876b9bac7d89120d2de4f86039b44e36ae3c2e4  tests/test_cave_yuanying_lifecycle.py
```

## Live Boundary

Production still loads `f7958deb`; main PID and account switches are unchanged.
At 13:44, watchdog OK and health only reports two historical held summaries.
The 13:32 Bot callback reset recovered after one failure. The 13:40 natural
Boss completed with one participant/settlement, 15 effective hits and 12 perfect
hits; individual and later global-result notifications were confirmed. This
does not certify this un-loaded YuanYing candidate or a complete Boss audit.

No held notification was replayed, no MiniApp/game request was forced, and no
automatic incense refining was enabled. Natural sect-retreat MiniApp entry,
post-maintenance loading and subsequent state observation remain unaccepted.
