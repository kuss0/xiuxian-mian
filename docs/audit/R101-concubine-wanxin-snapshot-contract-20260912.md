# R101 Concubine Snapshots After Wanxin Results

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production files, live requests, data/config,
services, listeners, skills, commits, pushes or deployment are changed.
World Boss/refinement stay disabled; inventory API stays UI-only;
CommandAttempt stays shadow-only; deep retreat never affects Tianxing.
R67 production subordinate-role cultivation remains unvalidated.

## Existing Protection

Fourteen combined tests pass before any runtime change. Owned native queries
retain a fingerprint of the concubine state, and the public MiniApp reader
checks its captured state at both session and command-result boundaries.
In those single-change cases, an intervening Wanxin affinity write cancels an
obsolete MiniApp read or lets the native read complete without applying its
old panel. Fresh reads still calibrate; unrelated actions and other identities
do not cancel a read.

/tmp/xiuxian-r100-cross-writer-first-20260912.xml

## Reproduction

With no owned status-query record, the native and passive legacy/manual paths
apply an older status panel after a newer qualified Wanxin result. All six
initial cases fail: 209, 176 and 314 are overwritten with the old value 184.
Reload and an owned query whose affinity changes and then returns to its
original value bring the reproduction to nine failing cases, with 22 passing
controls. The fingerprint alone cannot detect that value round trip.

/tmp/xiuxian-r101-manual-reproducer-20260912.xml
/tmp/xiuxian-r101-manual-values-20260912.xml
/tmp/xiuxian-r101-chronology-reproducer-20260912.xml

Final-review MiniApp caller tests reproduce the same value round trip at both
await boundaries. The original read timestamp was also being used as the
processing clock, so a qualified intervening result looked like future data.
Both cases overwrite 200 with 184; two unrelated-action controls pass.
/tmp/xiuxian-r101-miniapp-round-trip-reproducer-20260912.xml

Two final boundary cases also reproduce a qualified server result 0.5 seconds
ahead of the local clock being ignored as future data. Native Wanxin admission
already permits one second of clock skew; the negative snapshot check now uses
the same allowance. Far-future/malformed data still supplies no ordering proof.
/tmp/xiuxian-r101-clock-skew-reproducer-20260912.xml

These are local state reproductions, not evidence of production resource loss.

## Scoped Contract

- Before applying a concubine snapshot, consult only the existing bounded
  Wanxin reply points for moon status, greeting and seal.
- A retained, structurally valid, handled result of the expected affinity
  type may reject a strictly older snapshot. Validate the existing receipt
  contract and its one-second native clock allowance; missing, unrelated,
  invalid or more-distant future points cannot create a new permanent module
  hold or assert freshness.
- Use a moon panel's original command timestamp, not its later edit time.
  For a gain/cost use the latest qualified result revision. This is a
  conservative negative boundary, not a resource balance or spend permission.
- Apply the check in the shared snapshot reducer used by native, passive and
  MiniApp status bridges. Preserve exact owned-query completion and cleanup.
  A rejected unowned panel must not change identity state or business clocks.
- The MiniApp caller also checks retained evidence at every existing admission
  boundary using the current processing clock. Keep the original request
  timestamp for snapshot freshness and cooldown parsing; returning late does
  not make an old read new. Value round trips cannot bypass this check.
- Fresh complete panels remain available, and the existing native/MiniApp
  owner, query-plan, transaction and identity guards remain unchanged.
- No new schema, history, recovery loop, flags, controller or sending authority.
  Wanxin replies themselves retain their existing atomic commit/reload path.

## Verification

- 48 combined lifecycle/negative-boundary cases are added in
  tests/test_concubine_wanxin_chronology.py. The existing R100 nine cases remain
  unchanged. Together they pass 57 tests in 1.21 seconds.
  /tmp/xiuxian-r101-chronology-final-v4-20260912.xml
- One added fresh-query control initially reused the old query's message ID
  and was correctly rejected by passive dedupe. The fixture now supplies a
  distinct command/reply ID and preserves message ordering for later queries;
  runtime dedupe and actual wording are unchanged.
- Final focused regression: 3421 passed, 62 subtests, 34.72 seconds.
  /tmp/xiuxian-r101-focused-final-v2-20260912.xml
- Configured/fatal Ruff, scoped unused checks for the new test and Wanxin,
  compileall, dependency and whitespace checks pass. Concubine's inherited
  imports used by its split action modules were not removed as unused.
- First full regression: 11231 passed, 1275 subtests, 168.25 seconds. JUnit
  reports 12506 total cases with no failures/errors/skips. The final clock
  boundary above was added afterwards, so this is not the final candidate run.
  /tmp/xiuxian-r101-full-first-20260912.xml
- Final network-isolated full regression: 11233 passed, 1275 subtests,
  169.36 seconds. JUnit reports 12508 total cases, no failures/errors/skips.
  /tmp/xiuxian-r101-full-final-v2-20260912.xml
- Final configured/fatal/scoped-unused Ruff, compileall, dependency and
  whitespace checks pass. All test sessions finished; HEAD is unchanged.

## Remaining Limits

This covers strictly older snapshots versus currently retained qualified
Wanxin evidence. It is not a complete cross-writer affinity ledger. Same-clock
observations, old read edits without an authoritative request clock, replaced
or missing legacy receipts, other resource writers and post-consumption
corrections still require separate review. A late cosmetic gain/cost edit can
conservatively defer an overlapping older read until a fresh query; it never
authorizes a retry or a spend. Moon-join success lacks an authoritative fixture.

Next verify the source clocks and commit rollback of the remaining unowned
native/passive status paths before changing them. The tests above do not
certify their full lifecycle or other modules' affinity writers.

R65 capacity/migration, R07 durability, remaining per-game integration,
production per-role acceptance and whole-project Final Review stay open.
