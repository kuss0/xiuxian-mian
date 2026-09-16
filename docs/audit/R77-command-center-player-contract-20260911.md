# R77 Command-Center Player Contract

## Scope

Candidate only: `/root/xiuxian-main-rebuild-20260907`, branch
`rebuild/stability-20260907`. This continues R67/R75/R76 into the Tianjige
command center. It does not deploy R67, rebaseline live channel timers, or
complete the YuanYing two-step lifecycle.

## Reproduced Failures

- The request builder accepted omitted/invalid player selection, and the flow
  did not compare it with the intended identity before obtaining authorization.
- Successful-looking replies without `account.playerId`, or with only an
  echoed selector, could reach the YuanYing, Tianti and Yinluo status bridges.
  Conflicting selector/account evidence and malformed completion were unsafe.
- Cancellation drained the worker but carried its raw HTTP object, not the
  command DTO expected by runtime callers.
- Final review added a separate four-case reproducer: invalid commands still
  requested authorization before whitelist rejection.

## Candidate Changes

- Require and validate the selected player in both builder and flow. Normalize
  the whitelisted command before authorization; retain the selected signed
  channel ID in the request.
- Check actual account ownership before exporting successful business data.
  A selector alone is not proof. Public callers also check the selected player
  and raw account metadata from the entry session.
- Preserve dispatch/HTTP/Retry-After evidence without automatic POST replay.
  YuanYing launch timeouts and ambiguous responses remain unknown; explicit
  429 rejection remains distinct. Read-only requests do not become mutations.
- Format the command DTO inside the worker so a drained cancellation can carry
  confirmed evidence. Runtime consumption of this evidence is a separate
  lifecycle task, not claimed complete here.
- Require literal boolean success/completion when supplied. Public caller tests
  use parseable module-specific YuanYing, Tianti and Yinluo panels, with positive
  controls proving that the same owned data can update its intended reducer.

## Verification

- Initial reproducers: **55 failed**;
  `/tmp/xiuxian-r77-command-before-20260911.xml`.
- Initial repair: **55 passed**;
  `/tmp/xiuxian-r77-command-after-20260911.xml`.
- Related suites: **291 passed, 5 subtests**;
  `/tmp/xiuxian-r77-command-related-20260911.xml`.
- Final-review reproducer: **4 failed, 58 passed**, all failures before the
  whitelist/auth ordering fix;
  `/tmp/xiuxian-r77-command-review-before-20260911.xml`.
- Final network-isolated full suite: **8045 passed, 1275 subtests**, 140.76s;
  `/tmp/xiuxian-r77-command-full-20260911.xml`. External networking disabled,
  loopback enabled, `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.
- Ruff `E9,F63,F7,F82,F811` across model/tests/tools, compileall and
  `git diff --check` pass.

## Remaining Gates

At this checkpoint YuanYing still needed whole-operation owner/control/schedule
checks, cancellation-result adoption and separation from legacy async handlers.
The subsequent scoped repair and verification are documented in
`R78-yuanying-lifecycle-contract-20260911.md`. Tianti's awaited lifecycle and
general response chronology remain open. R65/R74 migration/capacity, R07
durability and whole-project Final Review are not closed. No production
file/config/DB write, restart, game request, listener activation, skill edit,
commit or push occurred.
