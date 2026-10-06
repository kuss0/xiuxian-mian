# Duel Loadout Read-Only Evidence

Base: `514c3a97`. Lab: `/root/xiuxian-duel-loadout-probe-20261007`.
Only an explicit diagnostic tool and tests change; no runtime loadout control,
game state, switches, cooldowns, automatic probes or service changes.

## Incident And Evidence

Lpprceqei / 7538826434 resumed natural duels after its cultivation baseline
was calibrated. Its persisted `duel_unequip_prepared` was already true, so no
new unequip was sent. A 05:12 battle narrative mentioned a treasure, but such
prose and the profile's six bound treasures are not authoritative equipped
state. They do not justify clearing the flag or sending another unequip.

An explicit `.法宝` read through command-center at 05:24:02 returned HTTP 400.
The three preceding identity/details reads were HTTP 200 and identity matched.
The tool stopped without retry. No error body was retained, so this is not a
diagnosis of why command-center rejected the read. The final candidate does not
add `.法宝` to the command-center allowlist.

The existing runtime inventory reader establishes the read-only
`section` / `inventory` contract. At **05:30:20 CST**, a separately bounded
inventory probe returned four HTTP 200 responses, with matching account,
identity and player 7538826434. The complete `bagTreasure` section contains
**active=[]**, proving no equipped treasures at that observation time. It
does not retroactively prove equipment at every past battle or forever.

Evidence files (non-secret allowlisted reports):
- `/tmp/xiuxian-lp-loadout-read-20261007-0522.json`
- `/tmp/xiuxian-lp-inventory-read-20261007-0533.json`

File labels are operator labels; response timestamps above are authoritative.
Both probes use owner-checked MemorySession with updates disabled, read-only
SQLite access, bounded HTTP reads, no redirects and no retries. No Telegram
game command, equip/unequip, manual duel, state correction or flag clearing
occurred. These eight diagnostic HTTP requests are outside normal captures;
seven succeeded and the command-center read failed. Before the inventory
probe the captured preceding-minute request count was six, below 90/minute.

## Tool Boundary

`--read inventory` selects only the existing inventory section endpoint.
The output requires exact numeric player ownership and all four inventory
lists; a missing active list cannot become an empty-equipped success. Active
rows retain only bounded names, not arbitrary fields or credentials. Bound
inventory is never counted as equipped gear. No imports from the production
runtime and no autonomous follow-up are added.

This is not a repair of persisted loadout freshness. Handling out-of-band
equipment changes remains a separate design question, requiring evidence and
careful anti-storm behavior. The current observation shows no mismatch.

## Validation

- Focused inventory, fishing-read, baseline and resource-observation regression:
  **258 passed / 5 subtests** with `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.
- Fake end-to-end probes cover the exact four read payloads, successful empty
  equipment, wrong-player rejection, HTTP failure without retry, disabled
  updates, client disconnect and omission of command/action writes.
- Parser cases cover complete/partial sections, bound versus active gear,
  malformed rows, oversized values and credential-field exclusion.
- The initial added async test used an unavailable pytest plugin. Converted
  the test to the repository's standard-library `asyncio.run` convention; no
  dependency added. This was a test harness failure, not a production error.
- Full isolated regression: **16731 passed / 1484 subtests**, 453.52 seconds;
  JUnit `/tmp/xiuxian-loadout-readonly-20261007.xml`.
- A second maintainer pass checked endpoint scope, no-write payloads, ownership,
  empty-versus-missing evidence and no-retry exits. Its cross-module regression
  passed **202 tests / 6 subtests**. This is not an independent external audit.
- Ruff, compileall and diff checks pass. Ready for scoped merge; the production
  worker remains unchanged and this tool needs no runtime reload.
