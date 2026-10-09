# Phaseful Early Result

Base `9a639141`. Isolated candidate in
`/root/xiuxian-phaseful-early-result-20261009`.
Not loaded or naturally accepted. No live sends, switch changes, state
corrections or service restarts were performed to reproduce this issue.

## Upstream Comparison

SSH fetch on October 9 around 10:39 advanced wxjerry `origin/main` from
`aa9dba29` to `d466ae4ee3e2859910ff621888624db3decb35eb`.
Its seven-file change adds an in-flight dispatch barrier for early replies
and starts reply waits from the transport receipt rather than queue time.
Rust `origin/main` remains `f18e89ac`; wxjerry's other branch remains
`cd2a2e64`. No upstream branch was merged into production.

Local runtime already returns `sent_at`, tracks receipts and replays early
replies with identity/chat/root scoping. Today's 10:23:02 natural
`wisemole` warm-up reply replay demonstrates that existing path, not this
candidate. Local rift, concubine status and phaseful sends already use receipt
time. The upstream arbiter does not exist in this architecture. Its listener
barrier is not imported; no sender/listener/Attempt control changes are needed.

## Confirmed Local Gap

`_send_summary_launch` records `queued_launch`, then awaits the existing
sender. A reply can run its reducer before that await returns. The old
continuation unconditionally replaces the resulting `running` or
`post_summary_wait` with `launching`/`waiting_summary`. A returned `None`
can likewise overwrite newer state with retry scheduling. Operator changes
are affected too. This is an offline-reproduced race, not attribution of a
particular current production failure.

The initial executable baseline was **32 failed / 4 passed**. The four
unchanged-dispatch controls confirmed that local timing already uses the
receipt time. A second inspection reproduced **six more failures** when a
result arrives while awaiting deletion of the old trigger message, before
the next command is sent. The first attempted test invocation used an absent
pytest async plugin and was corrected to the repository's `asyncio.run`
pattern; that harness error is not a product regression.

## Candidate Boundary

Only this continuation captures its identity object and seven relevant
phase/control/timer fields. It checks ownership after old-message cleanup,
refreshes the expected snapshot after entering `queued_launch`, and checks
again after sending. A newer state, disabled module, removed identity or
replacement object wins. Unrelated identity fields do not invalidate it.

No new game request, retry, receipt recovery, persistence or audit notification
is introduced by the guard. An unchanged operation retains its original
receipt-based timing and unknown-send classification. Both scheduler callers
return after awaiting the operation; the guard's false return does not
immediately schedule another command.

This is deliberately not a wholesale rewrite of `_phaseful`: active/passive
summary query paths and their sent-observer transitions require a separate
review. A blanket snapshot check there could mistake the normal
`summary_due -> observing_summary` transition for an external result.
It does not replace the native MiniApp operation ownership checks.

## Validation

- Candidate tests include both deep retreat and nascent soul, two original
  phases, successful/unknown transport returns, reply transitions, disabling,
  rescheduling, identity removal/replacement and cleanup races.
- One test runs the actual deep-retreat success parser during the mocked send
  and verifies its confirmed state survives the sender's later return.
- Related regression: **277 passed / 65 subtests** across phaseful state,
  native retreat identity/lifecycle, early-reply replay and sent observers.
- Ruff, compileall and `git diff --check` pass.
- Second maintainer review checked the two caller return paths, unchanged
  unknown-send handling, cleanup/send ordering and the seven primitive fields.
  Separate cross-module regression: **492 passed / 263 subtests** (Tianxing,
  rift, startup recovery and runtime send timing). This is not an independent
  external audit.
- Frozen full regression: **17057 passed / 1517 subtests**, 456.89 seconds.
  JUnit: `/tmp/xiuxian-phaseful-early-result-full-20261009.xml`. No runtime or
  test source changed during this full run; only documentation was updated.
- Preserve the live quiz-bank modification. Do not restart merely to load
  this candidate.

The main monitor remains session `68402`. The 19 channel identities' natural
retreat cycles completed before loading this code and cannot accept it.

## Delivery

`e326afba` fast-forwarded into main after the full run. Production-directory
isolated regression: **166 passed** (phaseful, new race cases and early reply
replay). Worker `3981692` and all service start times remain unchanged; the
running worker still loads `b686012b`. Code delivery is not deployment or
natural acceptance. The other pending notification changes remain unloaded.
