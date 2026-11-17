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
cases; the repository-wide Ruff check and diff check also passed. The second
clean full run completed with 18,240 passed and 1,552 subtests passed in
463.40 seconds, with zero failures or errors in its JUnit report. The tested
fixture is byte-identical to the committed production-checkout fixture.

At the owner's request, GitHub is used as a code repository; stability checks
now run only through `workflow_dispatch`. Normal pushes and pull requests no
longer start this workflow or generate its failure emails. Local validation
remains in place before pushing code. Its manual job timeout is now
20 minutes because the measured full run alone used more than 8 minutes,
before allowing for checkout and dependency setup. Concurrency cancels an
obsolete run when another commit arrives on the same ref, preventing a backlog
of checks for superseded commits.

Remote acceptance was subsequently verified through GitHub's public REST API;
the unauthenticated GitHub CLI was not evidence that the repository was private.
Run `44014116501` on `a68baaa8` failed its pytest step in an 8-minute-10-second
job, matching the supplied email. Run `44015491851` on repair commit `822ba89c`
completed successfully at 2026-11-17 12:18:43 CST, including Ruff, pytest and
artifact upload:
https://github.com/kuss0/xiuxian-mian/actions/runs/44015491851

The failed run's other two annotations were a Node 20 action deprecation warning
and an `ubuntu-latest` image-migration notice, not additional test failures.
The follow-up workflow uses the released Node 24 actions `checkout@v7.0.1`,
`setup-python@v7.1.0` and `upload-artifact@v7.0.2`, with `ubuntu-24.04` to avoid
an unplanned image migration. Each release and its declared Node runtime was
checked against the official action repository before changing the references.

The final runtime commit `4f260851` also passed a fresh isolated full run:
18,273 passed and 1,552 subtests passed in 473.70 seconds. The subsequent
workflow-only changes do not restart the production game service.
