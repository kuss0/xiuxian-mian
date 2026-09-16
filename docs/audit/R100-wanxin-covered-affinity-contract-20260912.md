# R100 Wanxin Covered Affinity Replies

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production code, state, switches, services, game
requests, listeners, skills, commits, pushes or deployments are changed.
World Boss/refinement stay disabled, inventory API stays explicit UI-only,
CommandAttempt stays shadow-only, and deep retreat never affects Tianxing.
R67 production subordinate-role cultivation remains unvalidated.

## Reproduction

Wanxin reply ownership and per-action dedupe did not protect the shared
concubine affinity cache from an already newer status snapshot:

- An older moon-status panel overwrote 200 with its older absolute 314.
- A later edit to the same old read used its edit timestamp to overwrite the
  newer snapshot with 315, even though no new status read occurred.
- A greeting already included in the newer 209-point snapshot was added again,
  producing 218. A seal cost already included in 176 was charged again locally,
  producing 152.

All four initial offline cases failed.
/tmp/xiuxian-r100-reproducer-20260912.xml
These are local-state reproductions, not claims of production resource loss.

## Scoped Contract

- Keep native source, owner/account/chat, result grammar, per-action ordering
  and exact pending cleanup unchanged.
- When a valid persisted concubine snapshot is strictly newer, do not overwrite
  its affinity with a covered Wanxin update. Ignore future/invalid snapshot
  timestamps for this coverage decision.
- A moon-status read uses its original command timestamp for freshness, not a
  later reply edit timestamp. A greeting/seal delta uses the authoritative
  result event timestamp, not local delivery time.
- This suppresses only the obsolete affinity write. The real operation still
  completes, records its parsed delta for dedupe and advances its business CD.
  It does not discard the reply, leave an unnecessary pending operation, or
  clear a sibling pending root.
- A genuinely newer supported result keeps the existing projection behavior.
  Completion and permitted identity updates retain the existing atomic save.
  SQL failure preserves the current affinity, prior evidence and original
  pending record; reload/replay cannot count the covered result again.
- No new schema, flags, query/retry loop or resource ledger is added. The
  existing snapshot fields are not repurposed to claim a full new portrait
  merely because Wanxin received an affinity-related result.

## Verification

- Nine new cases in tests/test_wanxin_affinity_chronology.py: old absolute
  panels, changed old-read edits, covered gain/cost, newer positive results,
  SQL rollback, SQLite reload and sibling pending retention.
- The first broad run passed 3100 cases and failed one new reload assertion:
  shared pending rows are normalized by the existing SQLite codec rather than
  round-tripped as arbitrary dictionaries. The test now checks the restored
  command/root/time and that duplicate handling leaves the reloaded record
  unchanged. No production codec or real game fixture was changed.
- Final targeted cases: nine passed, 0.57 seconds.
  /tmp/xiuxian-r100-chronology-final-20260912.xml
- Final focused regression: 3101 passed, 13 subtests; 33.28 seconds.
  /tmp/xiuxian-r100-focused-final-20260912.xml
- Final isolated full regression: 11185 passed, 1275 subtests; 167.46 seconds.
  /tmp/xiuxian-r100-full-first-20260912.xml
- JUnit reports 12460 cases including subtests, with no failures/errors/skips.
  Configured/fatal/scoped-unused Ruff, compileall, dependency and whitespace
  checks pass. All test sessions finished.

## Remaining Gates

This is a repair for strictly covered old writes, not a complete shared
affinity ledger or proof of all cross-module resource ordering. Same-clock or
overlapping observations, manual/legacy actions without complete provenance,
and corrections after other resource consumers remain open. Do not infer those guarantees from
these nine cases or use them to approve production rollout.

R101 verifies the existing native query-plan and MiniApp caller protections
for ordinary reverse-order delivery, then reproduces the legacy/manual and
value-round-trip gaps separately. See that contract for its scoped follow-up;
these nine R100 cases alone do not establish the additional guarantees.

R65 retention/capacity, R07 shared durability, remaining per-game integration,
production per-role acceptance and whole-project Final Review remain open.
