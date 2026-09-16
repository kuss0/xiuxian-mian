# R117 Yinluo Legacy Evidence Preservation

Status: scoped offline candidate verified; not production acceptance.
Parent: `rebuild-stability-20260907.md`, R65 and R116.

## Reproduction

Legacy pending normalization removed malformed/falsy payloads and transport
metadata, aliased nested refine evidence, or raised on scalar collect slots.
Saving an unrelated configuration or passing through scheduler normalization
could therefore remove the only evidence backing a bootstrap `legacy_pending`
hold. An existing valid book also ignored newly encountered untracked legacy
observation reservations. A new native panel overwrote an unowned old pending
business summary even though its resource book remained held.

The initial 51-case suite had 48 failures and three passing controls. This
includes prospective quarantine-marker assertions, not 48 independent defects.
Further review reproduced 19 cases where the MiniApp read bridge admitted
falsy legacy payloads or retained/malformed quarantine markers, and eight
cases where the unavailable-module scheduler erased the evidence outright.
SQLite review reproduced 22 additional corrupt-root cases: generic persistence
converted invalid roots to empty dictionaries even without module normalization.
The valid empty-observation control passed. These are offline fixtures, not
claims about current production identities.

## Contract

1. The three old `auto_collect_pending`, `auto_refine_pending` and
   `auto_soothe_pending` payloads are evidence, not UI values. Normalization
   deep-copies them unchanged, including original identifiers, clocks, nested
   snapshots and malformed payloads. No guessed owner or command is added.
2. Any nonempty legacy pending dictionary, non-dictionary pending value or
   retained invalid marker keeps resource admission held. This applies both
   to bootstrap books and to existing valid books. A stronger existing hold
   is not replaced. Read-only inspection does not persist a new book or mutate
   the observation.
3. An explicitly invalid observation root or non-dictionary pending value
   normalizes with `legacy_pending_invalid: true`. A fresh/default observation
   is valid. Only literal boolean `false` is a valid clear marker; malformed
   marker values, including null and numeric zero, retain quarantine.
4. A held unowned `last_result: pending` summary keeps its original business
   action, text and clock. Native financial facts may still be recorded, but
   a new panel is not completion or migration of the old operation.
5. MiniApp status admission uses the same legacy predicate. It rejects these
   unresolved states before session/HTTP work and cannot replace their summary.
   R116's strictly owned native read completion remains available separately.
6. An unavailable module remains disabled without deleting its observation.
   Stopping automation or hiding stale UI details is not financial evidence.
   Configuration saves, scheduler waits and reloads retain the unresolved state.
7. The existing observation column's scoped codec preserves quarantine for
   invalid roots and invalid JSON. Ordinary established-roster reads do not
   rewrite stored data; the existing first-load roster initialization and
   later saves retain the marker when they write. Valid empty dictionaries
   and the codecs of other modules remain unchanged.
8. No automatic legacy migration, retry, reservation release or hold clearance
   is implemented here. A missing outcome remains unknown. A fresh balance,
   empty-looking malformed value, timeout or new configuration is not proof.

## Compatibility And Rollback

`legacy_pending_invalid` is an additive boolean inside the existing
`yinluo_observation` JSON, not a SQL column. Old observations without this key
are accepted according to their actual pending payloads. Explicitly corrupt
roots keep a quarantine marker rather than becoming trusted default state;
the marker is not a reconstruction of the corrupt root's original content.
Pending payloads themselves retain their original content, without aliases.
The UI exposes the marker and the held resource status without mutating state.

Older readers ignore this marker and may destructively normalize the old
payloads. A code-only rollback must not resume affected identities. A later,
separately approved migration/rollback needs a verified snapshot and an explicit
reconciliation plan retaining both the marker and original pending evidence.
Clearing this marker or editing pending dictionaries is not a recovery API.
No production database, configuration or runtime has been changed.

## Validation

- Initial legacy suite: `/tmp/xiuxian-r117-initial-20260914.xml`.
- MiniApp bridge reproduction: 19 failed, 126 passed;
  `/tmp/xiuxian-r117-bridge-initial-20260914.xml`.
- Intermediate bridge/legacy/read suite: 223 passed;
  `/tmp/xiuxian-r117-bridge-fixed-20260914.xml`.
- Unavailable scheduler reproduction: eight failed;
  `/tmp/xiuxian-r117-unavailable-initial-20260914.xml`.
- Persistence reproduction: 22 failed, one valid-empty control passed;
  `/tmp/xiuxian-r117-codec-initial-20260914.xml`.
- Final focused legacy/bridge/persistence suite: 253 passed, 59 subtests,
  13.60s; `/tmp/xiuxian-r117-focused-final-20260914.xml`.
- Final associated Yinluo/Wanxin/resource/transport/recovery/persistence suite:
  1880 passed, 370 subtests, 88.69s;
  `/tmp/xiuxian-r117-related-final-20260914.xml`.
- Configured repository Ruff, scoped fatal/unused-name Ruff, compilation,
  dependency consistency and whitespace checks pass.
- Network-isolated full regression: 12524 passed, 1275 subtests, 217.85s;
  `/tmp/xiuxian-r117-full-20260914.xml`.

This checkpoint adds 122 cases: 103 in the legacy evidence suite and 19 in
the existing MiniApp read-bridge suite. Final focused, associated and full
reports have no failures, errors or skipped tests. All test sessions completed.
Candidate HEAD remains `cbf152cd61325ca9218c74ee995d643038b20905`.
Inherited dirty work, including the user-owned quiz bank and UI-key inspection
tool, remains intact.

The intermediate codec run's remaining 14 failures assumed every load was
read-only. Its fixture instead triggered the existing first-load roster
writeback. Tests now distinguish established-roster nonwriting reads from
initialization writeback; both retain quarantine. The shared loader lifecycle
is not changed to accommodate a test assumption.

## Remaining Gates

R65 legacy/no-ID outcome migration, unproved consuming intents, complete
forced-stop/storage recovery, operational capacity and beneficiary churn
remain open. R07 and whole-project Final Review remain open. A safe visible
hold alone does not establish usable long-term automation.

This checkpoint touches candidate Yinluo code, its observation codec in
`persistence.py`, tests and audit documents.
Shared sender/guard implementations are unchanged. No live game request,
deployment, restart, listener/skill edit, commit or push occurs. World Boss
and refinement controls stay untouched; inventory API stays UI-only;
CommandAttempt stays shadow-only; deep retreat never gates Tianxing.
R67 subordinate-role MiniApp cultivation remains unvalidated in production.
