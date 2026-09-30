# Native Dwelling Fishing Lab (2026-09-30)

## Status

Worktree: `/root/xiuxian-native-fishing-20260930`.
Branch: `lab/native-fishing-20260930`, base `8a04ebaa`.

This is an offline protocol/journal implementation, **not production fishing
automation**. No runtime imports, state fields, dispatcher branches, automatic
HTTP calls, settings, purchases or casts were added. Production stays at
`8a04ebaa`; the existing fishing UI/settings and unresolved legacy ledger remain
untouched. Do not assign this record to the legacy `fishing_operation` field.

## Sources

- wxjerry `origin/main` remains `aa9dba29` on the latest SSH check.
- Absorbed protocol/physics from `3c77db65`; voyage eligibility from `3c220736`.
  Did not import upstream global state, mutation replay, pauses or test deletion.
- Current official controller:
  `/staticweb/assets/dwelling-fishing-controller.js?v=fishing-v14-world-moon-cue`.
  SHA-256: `07d36c165f9d62bda1c7f38a72cada16f63f4fca9fb02ecc345e67304dc95237`.
- Unlike upstream's saved holding state, current frontend recovery releases the
  line at the next 20 ms tick. The Lab preserves old events and applies this
  release before resuming control. A completed checkpoint adds no future input.
- Proof events represent physics inputs. The UI's cosmetic button release after
  final simulation is not appended as an ambiguous last-tick physics input.
  Server acceptance still requires a controlled real-cast test.

## Implemented and Tested

- `fishing_dwelling_protocol.py`: strict context, quota, bait, rod, owned session,
  settlement and checkpoint parsing. No truthy success flags, inferred fish,
  filtered corrupt events or fabricated quota consumption.
- 20 ms V1/V2 physics, steady/leap/surge behavior, bounded checkpoint events and
  restored state. Independent JavaScript formula replay includes numeric, ASCII
  and non-BMP seeds, three fish powers and resumed checkpoints.
- Server time uses monotonic elapsed time plus half RTT, capped at 250 ms.
  Expired bite selects `state`, never another hook/cast. Timed proof iteration
  waits actual duration, checks ownership after waits and rejects stalled clocks.
- `fishing_dwelling_journal.py`: separate, bounded single-cast record with exact
  identity/account/player/cast/session ownership. Store contract is atomic
  compare-and-save. A cast payload is returned only after a confirmed durable
  save; failures poison that journal instance. This store is not yet bound to
  production persistence.
- A restarted/unresolved cast exposes an exact `state` query, not another cast.
  An unscoped `context`, foreign session, stale revision or negative/missing
  response cannot clear the record. Confirmed settlement is retained and cannot
  regress or change rewards; it does not itself update inventory or daily counts.
- Missing rod, missing bait, quota exhaustion, voyage and other-mode conflict
  remain distinct identity-local reasons, not a global circuit breaker.

## Real Read-Only Evidence

`/root/xiuxian-native-fishing-contract-20260930.json`, 18:24 CST:

- Identity `3765328695`, account `301299112`, player `-1003765328695`.
- Owner start, selected-player start, details and fishing context all HTTP 200.
- Entry is `fishing/integrated`; rod present, quota 0/5 used, 5 remaining,
  no active session, no conflict.
- Five bait types, all count 0. The new parser accepts the contract and produces
  `fishing_bait_missing`; this is not an entry outage or missing-rod error.
- Production fishing for this identity was already off. No toggle, bait purchase
  or gameplay mutation was performed. Context quota before/after stayed 0/5.
- Probe now saves allowlisted bait metadata, preserving missing fields instead
  of turning malformed/missing inventory into an empty successful snapshot.

## Required Before Runtime Integration

1. Bind a separate native record to checked persistence and identity ownership,
   including removal/recreation, account swaps and restart/process tests.
2. Extend durable intent and unknown-outcome reconciliation to hook, checkpoint
   and fight. Current journal covers cast binding and settlement evidence only.
   Prevent parallel or out-of-order active-session responses from scheduling
   duplicate mutations; the current journal is not a hook/fight state machine.
3. Bind selected character model and directory evidence from the verified public
   entry. Preserve original pond, bait and daily-plan settings. No guessed
   companion ID, generic command fallback or legacy-ledger reinterpretation.
4. Integrate the shared HTTP budget (90/min and daily cap), cancellation/draining,
   existing identity/game locks, and retry-after handling. A raw response parser
   is not identity authentication or a rate-limited transport.
5. Transactionally project each session's fish/quota once with its accounting
   marker; do not double-subtract bait already reflected by native context.
6. Controlled one-rod acceptance on an already-enabled identity with rod, bait
   and companion available, followed by restart/recovery verification. No bulk
   enablement or automatic purchase for the read-only probe identity.
7. Only then enable integrated-directory routing; retain legacy unresolved
   recovery until those records are reconciled. Remove obsolete code separately.

## Verification

- Native suite: 117 passed, including separate V1 and V2 JavaScript replay cases.
- Final fishing regression: 901 passed, 17 subtests.
- JavaScript syntax, Python compileall and diff whitespace checks passed.
- Isolated imports load no `model.runtime`, `model.state`, `model.config`,
  `requests` or `telethon`.
- Production observer remained ok through 18:25; PID `3597994`, NRestarts 0.
- 8-hour defensive preflight: pending queue empty. WA wild training due
  2026-10-01 01:29 CST, not yet in its Tianxing preparation window. Listener
  sidecar inactive remains a known watch item; main listener is active.

World Boss stays off; both incense-to-shenshi settings stay off. Channel-send
freeze, manual-only inventory policy and CommandAttempt shadow control remain
unchanged. Do not treat this Lab commit as a production migration approval.
