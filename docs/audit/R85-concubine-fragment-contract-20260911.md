# R85 Fragment Panel Contract

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

This is an offline repair of fragment-panel interpretation and puzzle
admission. It is not a dream/puzzle lifecycle rewrite or production recovery.
Production services, configuration, databases, listeners, game requests,
remotes, skills and user-owned quiz/tool files are unchanged. World Boss and
refinement remain disabled; CommandAttempt remains shadow-only.

## Evidence And Repair

The first 22 reproductions failed. Incomplete panels borrowed cached 4/4
quantities, allowing puzzle admission without fresh confirmation. The timeout
path fabricated a reduction from 4/4 to 3/4. Subsequent review reproduced
three passive-routing failures and three incorrect handled-result failures.
All are covered by the repaired tests.

The shared panel parser now requires both Xutian and Cangkun sections, one
matching partner, explicit counts from zero through four with total four,
and collected/missing lists with the corresponding cardinalities. Duplicate
sections, fields or pieces, overlapping piece lists and conflicting progress
are rejected. No missing field is filled from the stored progress.

A valid panel replaces both explicit progress observations and confirms only
its complete kinds. Partial or conflicting panels preserve pending work and
all quantities. Timeout invalidates confirmation without inventing missing
pieces. Puzzle admission requires current confirmation; legacy puzzle_ready
without it requests fragments before spending.

Passive replies with explicit identity reach the normal panel reducer.
Generic passive fragment handling cannot overwrite progress or set dream CD.
Wrong-phase, unrelated-root and unrecognized replies return False instead of
claiming completion. A later complete edit can still finish a partial read.

The recorded concubine.fragment.scroll fixture is accepted as Xutian 4/4,
Cangkun 2/4 for its explicitly named partner. An existing affinity fixture's
partner was corrected to match its configured partner; the puzzle queue-timeout
fixture now supplies the required confirmation. Supported whitespace, colon
variants and section order remain covered.

## Verification

- New cases: 63 in tests/test_concubine_fragment_contract.py.
- Focused: 994 passed, 73 subtests, 7.34s.
  /tmp/xiuxian-r85-fragment-verified-focused-20260911.xml.
- Full isolated suite: 9266 passed, 1275 subtests, 148.80s.
  /tmp/xiuxian-r85-fragment-full-20260911.xml.
- Configured Ruff and E9/F63/F7/F82 checks passed before the full run;
  compileall, pip check and git diff --check passed after it.
- All test sessions completed. Only documentation changed after this full
  checkpoint. Tests use temporary SQLite and network namespaces with only
  loopback enabled for local fake HTTP servers.

## Remaining Gates

- Follow-up R86-concubine-fragment-lifecycle-20260911.md supplies owned
  fragment reads, checked completion, recovery and bounded confirmation.
  The limitations below describe the R85 checkpoint, not completion of R86.
- Fragment/dream/puzzle still use legacy scalar operation ownership and old
  send/recovery machinery. Panel correctness does not establish account/chat
  ownership, crash durability, checked completion saves or manual chronology.
- Dream and puzzle sends use track=False, claim their phase after transport
  and recover commands through weak command/time matching. Startup discards
  those scalar pending phases. These are the next lifecycle review targets.
- Divination, heart, voyage, broader affinity/resource accounting, legacy
  reconciliation, R65/R74 capacity and shared R07 durability remain open.
- Production jfdffdddd subordinate-role MiniApp cultivation remains
  unverified. No rollout, live-health claim or whole-project Final Review
  completion follows from this component checkpoint.
