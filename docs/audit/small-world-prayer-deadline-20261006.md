# Small-World Prayer Deadline

## Evidence

October 6, 2026, CST; WA identity 8659059191:

- New-group query/reply `1285944 -> 1285945` at 15:10:30-33 showed a prayer.
- Manifest `1285946 -> 1285947` at 15:10:43-45 succeeded and explicitly
  returned a 360-minute prayer wait. The next check belongs near 21:10,
  plus the normal buffer/jitter, not a new god-action cooldown.
- Distinct old-group disasters `12635637` at 20:23:56 and `12636905` at
  23:25:19 each reported faith -11. Each caused exactly one new-group sermon,
  `1287207 -> 1287208` and `1287912 -> 1287913`.
- The confirmed sermons consumed 12000 cultivation each. The latter raised
  faith to 100 and stability to 84; it was not incense refining or relief.
- The first sermon set the next check to 23:34:48; the second replaced it
  with October 7 02:27:38, although god cooldown itself ends at 02:25:31.

No repeated-send storm is established here. The defect is replacement of an
independent prayer check by god-action follow-up. No exact missed reward or
newly generated prayer is inferred without another authoritative panel.

## Scope

Lab: `/root/xiuxian-small-world-prayer-deadline-20261006`, base `8db9fbb4`.

Keep a prayer check anchored to an actual panel/manifest countdown. A confirmed
god action may update its own cooldown, but must not push that check later.
An elapsed prayer timer may request a panel, never an unverified manifest.
New query/send-unknown/resource backoffs must retire this scheduling hint.
Queued cooling god actions must not starve an independently due panel check;
active pending replies and due disaster actions still take precedence.

No change to spending policy, routine 85% threshold, catastrophe priority,
MiniApp switches, shared send layer, Attempt flags, or production state.

## Validation

The candidate stores `prayer_check_at` inside the existing persisted panel JSON;
there is no database schema change or new scheduler. Full command and MiniApp
panels share the same state writer. Explicit manifest cooldowns can establish
the deadline even when other resource fields are unavailable. A missing legacy
deadline is not reconstructed from a sermon's generic `updated_at` timestamp.

Only god follow-up scheduling is capped by this hint. Expired hints do not
override general retries; query admission, guard deferral, send-unknown and
resource backoff retire them. The existing elapsed-cache regression remains.
Cooling queued actions stay queued while an eligible due panel can run. An
active reply wait or a due disaster retains precedence. Retrying the cooling
queue does not restart a one-minute delay indefinitely.

Initial code review found that the MiniApp full-panel writer would otherwise
discard the deadline; it now reuses the command writer. A partial mutation
invalidates the old hint, and a fresh failure/resource delay retires even a
new panel hint. Ownership guards still decide whether an old operation may
touch the current snapshot. A confirmed explicit manifest wait is independent
evidence and does not freshen the old resource panel.

- Two initial behavioral regressions failed on the old implementation after
  correcting test-harness syntax/StateProxy setup.
- Focused final candidate: 312 passed / 19 subtests across small-world command,
  MiniApp lifecycle, reply context and semantic-report tests.
- Pre-integration full suite: 16599 passed / 1478 subtests, 454.91s. This does
  not certify the subsequently added MiniApp integration.
- The first integration full run found two existing partial-harvest assertions
  failing (16601 passed / 2 failed). Retirement now leaves a missing panel
  missing and does not add metadata to an unrelated old resource snapshot.
  Those existing assertions were kept unchanged.
- Broadened review after that correction: 557 passed / 24 subtests, including
  harvest receipts, persistence and cave background lifecycle.
- Final reviewed full suite: 16603 passed / 1478 subtests, 437.76s; JUnit
  `/tmp/xiuxian-small-world-prayer-deadline-final-reviewed-20261006.xml`.
- Ruff, compileall and diff whitespace checks passed on the candidate.

Code review and offline acceptance are complete; ready to merge and push.
The full runtime patch is limited to the two small-world implementations;
no shared-send change, database migration, or extra game request was made.

No runtime acceptance claimed. Production worker 2207227 remains the old
generation. Existing live timers are not repaired by changing disk code; after
normal loading, the next authoritative panel/wait must establish the hint.
Do not replay an old countdown into live state or force another manifest.
