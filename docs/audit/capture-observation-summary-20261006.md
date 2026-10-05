# Capture Observation Summary

Base: `ed72a322`. Status: `a6eb55a2` promoted and pushed; no service restart.

## Defect And Boundary

Native fishing writes a local `native_checkpoint_observation` after a run.
Its fields are adapter_key, step_key, created_at, source and observations.
It is not an HTTP request and has no ok/status/method/request/response fields.
The summary treated absent ok as false, invented a blank POST endpoint and
reported every such row as a failure, including in recent requests.

Separate only this exact local-record envelope before grouping requests.
Retain original scanned/total counts and expose observation_records separately.
CLI output names this count. Unknown records, real request failures and
request-like rows missing ok keep their existing failure behavior, even when
their step name resembles the observation name. Never delete or mutate captures
or infer a successful game action from an observation.

Changes are limited to the summary reader and tests. No gameplay, HTTP,
proof/timing, state accounting, retries, notification dispatch or flags change.
The existing UI consumes the filtered endpoints; no layout or JS changes.

## Evidence And Validation

- Initial regressions fail on the old implementation.
- Focused summary, native flow and webapp core: 178 passed, 29 subtests.
- Read-only replay of the live file at 03:28: 102 records, 14 local observations,
  87 successful requests and one real error. That error remains the pre-patch
  00:13:12 hook rejection. The previous reader misleadingly counted the 14
  observations as additional errors and as a nonexistent request endpoint.
- Real files are read explicitly from the production capture directory;
  no live DB imports, requests or state writes are part of this replay.
- Separate maintainer pass: 197 passed, including capture paths, business
  captures, native protocol/flow and UI dual-track contracts. Re-read the
  filtered endpoint/recent paths and unchanged UI consumer; no independent
  external audit implied. Full suite: 15935 passed, 1449 subtests, 464.18s;
  `/tmp/xiuxian-capture-observation-summary-20261006.xml`.
  Ruff, compileall and diff pass.

Do not interrupt natural fishing for this display-only fix. A new CLI process
can use it after promotion, while an already-imported web worker uses its old
reader until a normally scheduled restart. Keep these activation states distinct.

At 03:36, the production CLI returned 109 records: 15 local observations,
93 successful requests and one historical hook error. Post-merge isolated
summary tests: 15 passed. Main PID 2144248 and worker 2144259 are unchanged
since 03:14; this reporting fix has not been reloaded into the web worker.
Health/watchdog have no new issue; only the historical held notification
requires delivery review. No capture, game state or account switch was changed.
