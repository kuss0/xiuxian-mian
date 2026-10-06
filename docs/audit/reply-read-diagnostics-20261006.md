# Reply Read Failure Context

## Evidence And Scope

The October 6 10:25:58 `GetMessagesRequest` RPC -504 escaped
`_resolve_event_reply()` into the existing outer traceback handler. No event
chat/message/root IDs were included, so the skipped event could not be
uniquely located. Pending=0 did not prove that every passive handler ran.

This patch annotates that original exception with a single-line
`reply_read_context` note. Python's existing `traceback.format_exc()` output
includes the note. It does not add a notification, retry, remote history read,
fallback reply, binding claim or business-state transition.

## Contract

- Record only signed 64-bit integer header IDs: chat, event message, reply
  header, topic, sender and receiving listener account. Missing or malformed
  values are 0, never inferred from the configured default chat or username.
- Classify real Telethon events as message/edit; unknown wrappers stay unknown.
- Header facts and the listener account identify the observation, not verified
  command ownership. Forwarded or unrelated events gain no authority.
- Preserve the original exception object, RPC code, arguments, traceback and
  pre-existing notes. Identical notes are not appended twice.
- Preserve cancellation and the existing disconnect/session-error branches.
  Diagnostic failures cannot replace the original RPC exception.
- No message text, launch URL, credentials or exception message is copied into
  the new note. Existing traceback contents are not globally redacted by this
  patch, and no claim is made that all historical logs are secret-free.

## Verification

- Before implementation: 12 failed / 5 passed after correcting the test's
  initial Telethon-event construction. All failures were missing context notes.
- First focused pass: 600 passed. Final focused pass including both outer
  event handlers and health-observer regressions: 725 passed / 16 subtests.
- Separate second-review pass: 226 passed / 135 subtests, including early
  reply replay, message-box shadow, runtime reply context, duel/Mulan/phaseful
  routing, Tianxing reply completion and real-message replay.
- All tests set `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; no live Telegram or MiniApp
  fault injection. Ruff, compilation and whitespace checks passed.
- Final full suite: 16269 passed / 1461 subtests, 462.76 seconds. JUnit:
  `/tmp/xiuxian-reply-read-diagnostics-20261006.xml`.

The reviews were separate passes by the same maintainer, not an independent
external audit. The change is intentionally limited to error evidence: it
does not repair RPC -504, recover the October 6 event, or establish automatic
missing-reply recovery. Suspected-bot handlers that intentionally swallow the
exception remain unchanged; they do not gain a new log path from this patch.

## Runtime Boundary

At 14:14 the supervisor/worker remained 2207210/2207227, loading `f7958deb`.
Observer 2322377 and watchdog 2178662 remained active. Pending=0; health warned
only about the two preserved unknown-delivery notification batches. The duel
check at about 14:10 deferred to 14:40:50 with the same no-baseline reason.

No service restart, switch change, message replay or live state correction
was performed. Code merge and runtime acceptance must be recorded separately;
natural annotated-error evidence is still absent and must not be forced.
