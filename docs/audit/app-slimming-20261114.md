# App slimming: 2026-11-14

Baseline: `4bb6098f`. Runtime changes add 196 lines and remove 623 lines,
for a net reduction of 427 lines across 18 Python files. The remaining changes
cover regression tests and this review.

## Shared implementations

- `message_log_recovery` owns bounded tail reads, timestamp parsing and streaming
  reads of local log days. Existing names and module-local log-directory seams
  remain available. Raw rows preserve edits and server timestamps; exact-time
  readers still apply their own timestamp window.
- Weekly adventures, Xuangu and Zhengmo reuse the day reader while retaining
  their own chat, topic, bot, command-anchor and reward-owner checks. Weekly
  startup recovery scans files in a worker and applies receipts on the owner
  event loop. Invalid server timestamps are skipped.
- Trial, tree and treasure checkpoint writers share their cross-thread submit
  implementation. Queued timeouts cancel without saving; a save already running
  must finish before dispatch. Each writer retains its own transitions,
  ownership validation and timeout constant.
- The UI delegates storage-bag writes, session/error handling and identity
  resolution to the existing runtime implementation. A batch builds its identity
  list once, preserving configured alias precedence. UI refresh retains
  `write_empty=False`. Diagnostic skipped-row counts now use runtime semantics.

An empty-inventory regression exposed an existing parser problem: the fallback
from `items=[]` could treat an outer numeric identity ID as an item quantity.
Explicit inventory containers now prevent that flat-object fallback. An empty
UI reply preserves inventory; trusted runtime callers must explicitly request
empty writes to clear it.

No gameplay commands, tactics, faction choices, quota limits, configuration
formats or persisted-state schemas are added or changed. Different gameplay
state machines remain separate. Local MiniApp pause seams remain intact.

## Validation

- Full isolated suite: **17,743 passed, 1,529 subtests passed** in 462.65s.
- Focused regressions after the parser fix: **495 passed**. Additional focused
  suites cover phaseful recovery, Taiyi, Hehuan, concubine and Zhengmo.
- Ruff over `model tools tests xiuxian.py`, compilation of changed Python files
  and `git diff --check` pass.
- Isolated browser smoke at 1280px and 390px: no page errors, no external
  requests and zero form overflow. Isolated HTTP smoke: **18 checks passed**.
- Offline baseline comparison on an 8,225,646-byte production-log copy:
  **15,212 identical rows**, equal SHA-256 and identical bounded-tail output.
  Three-run median read times were 0.129s before and 0.132s after; this is a
  deduplication change, not an asserted log-scan speedup.
- Synthetic storage batch with 100 identities and 1,000 inventory rows:
  identical nonempty update results; identity-list reads fall from **2,101 to 1**.
  Three-run median runtime falls from 0.030s to 0.019s. This is an isolated
  measurement, not a production throughput guarantee.

Evidence is kept privately on the host in
`/root/xiuxian-slim-evidence-20261114/`. Full-suite, remote CI and deployment
acceptance are recorded there when complete.
