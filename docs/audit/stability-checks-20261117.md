# Stability Checks failure repair

The clean `a68baaa8` checkout reproduced one failing test:
`test_xuangu_headerless_reply_requires_one_owned_command` raised
`AttributeError: 'types.SimpleNamespace' object has no attribute 'load'`.
The Xuangu cancellation reconciliation added in `b043559d` reads the flow
collection after a handled reply, but the test double supplied only `active()`.

The test now uses the real `FubenFlowStore` collection with an in-memory backing
dictionary and an unexpired roster. Its assertions still reject an unknown
reply, accept the single matching command, and reject two matching owners.
No production event handler or ownership guard was weakened.

Validation used an isolated worktree with Python 3.13, `TZ=Asia/Shanghai`, and
`XIUXIAN_ALLOW_LIVE_TEST_DB=0`. The first full run completed with 18,239 passed,
1 failed, and 1,552 subtests passed in 485 seconds. After the fixture repair,
the related integration, health, pacing, and hardening tests passed all 88
cases; the repository-wide Ruff check and diff check also passed. A second
full run was started to verify the final fixture against the whole suite.

The workflow retains push and pull-request checks. Its job timeout is now
20 minutes because the measured full run alone used more than 8 minutes,
before allowing for checkout and dependency setup. Concurrency cancels an
obsolete run when another commit arrives on the same ref, preventing a backlog
of checks for superseded commits.

The host has SSH push access to `kuss0/xiuxian-mian`, but no authenticated
GitHub CLI session for reading this private repository's Actions logs. This
report distinguishes the reproduced local failure from remote acceptance.
