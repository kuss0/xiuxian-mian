# World Boss Turnstile integration, 2026-10-02

## Scope and evidence

- Baseline: `3d2b4867`; runtime before rollout: `8dbff7a4`.
- Only identity `301299112` (Baji) may use verification. Other Boss account,
  rotation, skip, channel-send, small-world and Attempt flags remain unchanged.
- At 13:41:17 the production `/begin` returned HTTP 403 `turnstile_failed`.
  Its payload lacked verification fields. Join/WebSocket success was not battle
  settlement, and the public event success message did not prove this user's success.
- The September 8 delivery had not been integrated. Importing it alone was not
  sufficient: the real browser broker twice timed out after 60 seconds.
- Screenshot `/root/boss-verification-20261002.png` showed the old desktop click
  at (50,70), above the actual checkbox near (41,104). The browser content starts
  51px below its window origin, and the widget is 65px rather than the host's 100px.
- Clicking the actual checkbox in that same bounded diagnostic returned a token.
  This diagnostic had no Telegram credentials and made no game request.
- The project-owned browser adapter now measures the Firefox content origin,
  fixes the widget size explicitly and waits for Turnstile's interactive callback.
  It retains the broker's single-click bound and disables automatic widget retries.
- Two unattended real verification probes succeeded in **4.9s and 3.8s**.
  This proves token acquisition, not game-side acceptance or battle completion.

## Boundaries

- Imported `vendor/world_boss_turnstile_containers` bytes and source manifest are
  unchanged. Local corrections live in `tools/world_boss_turnstile_browser.py`.
- Local broker: `127.0.0.1:8193`, one browser worker, 1.5 GiB memory / 2 CPUs /
  512 process limit. Observed idle usage after probes: 613 MiB. Not publicly exposed.
- Broker receives fixed official page/sitekey/action and hashed account metadata;
  Telegram initData and entry/session tokens never leave the main process.
- Environment and UI switches default off. Host secret stays in a root-only
  `/root/.config/xiuxian-world-boss/turnstile.env`, outside version control.
- First begin remains unverified. Only an exact application JSON error
  `turnstile_required` or `turnstile_failed` permits one solve and one verified begin.
  No automatic retries of unknown results, HTTP 5xx, hits or finish were added.
- Verified begin uses a fresh idempotency key; neither proof nor key is logged.
  Broker waiting time is excluded from battle clock RTT estimation.
- Worker rechecks cancellation, identity owner/account, login, UI opt-out and
  genuine global pause before sending. Existing public-MiniApp channel-freeze
  and Tianzun-maintenance exceptions remain valid.
- UI saves retain excluded identities that are not currently displayed and keep
  offline configuration separate from actual login availability.

## Verification

- Isolated full suite: **15544 passed, 1406 subtests passed** (450.70 seconds).
- Offline HTTP UI smoke: **18 checks passed**, including 76 anonymous API denials.
- Python compile, JavaScript syntax and compose validation passed.
- Browser UI save/readback test: `tools/world_boss_turnstile_ui_smoke.py` passed at
  1280x900 and 390x844, preserving hidden exclusions; no live requests.
- Final focused regression: **446 passed, 32 subtests passed** (21.46 seconds).
- Natural-event acceptance is still required: Baji login -> join -> explicit
  rejection -> one token -> one verified begin -> real hits -> own settlement.
  Do not replay the expired 13:40 event or count global broadcasts as acceptance.

## Rollout and rollback

1. Back up SQLite and retain the existing Boss configuration.
2. Merge the tested branch; preserve unrelated backup-engine worktree changes.
3. Launch compose from production so the read-only source mounts no longer depend
   on the lab checkout. Attach the root-only environment through a systemd drop-in.
4. Restart once outside a near-term high-risk action; verify runtime/monitors.
5. Set only `turnstile_enabled` through the authenticated UI API and read back all
   Boss selections. Keep Baji as the only selected identity/account.
6. Observe the next natural Boss event. Rollback verification with UI switch off
   first; do not rewind the live database or reopen other accounts.

The natural-event gate is not yet closed by the above standalone probes.

## Natural-event checkpoint, 2026-10-03

- Event `2026-10-03:12599460`: Baji (`301299112`) joined at 13:40:15
  CST and recorded personal settlement at 13:42:51. The read-only production
  `world_boss_run_state` contains accepted hits 16/16, accepted perfects 14,
  damage 648043068 yi, grade A, quality score 100 and `full_window_run=true`.
  These are personal battle results, separate from the 13:44:21 public victory.
- The retained summary does not establish whether this event required and
  accepted a Turnstile token. Do not close the verification-specific gate merely
  because the personal battle settled; no extra live begin was sent to prove it.
- Optional WebSocket feed reports three reconnects and a state timeout. Current
  receive timeout is five seconds. Code inspection confirms this feed wakes
  pre-battle HTTP polling; it does not drive hold/release timing. There is no
  evidence attributing the two non-perfect hits to these reconnects. Server
  message cadence remains to be established before changing timeout policy.
- Focused offline regression on the current checkout: 201 tests and 3 subtests
  passed across world_boss, world_boss_miniapp, world_boss_miniapp_runtime,
  world_boss_turnstile and world_boss_turnstile_lifecycle. Live test DB access
  was disabled. No gameplay settings, runtime code or service state changed.

### Follow-up: distinguish idle sockets from failed sockets

Code review found that a five-second receive timeout always discarded the
WebSocket, even when the transport could still answer protocol pings. The
follow-up patch probes ping/pong on receive timeout and reconnects only if that
bounded probe fails. HTTP polling, action selection, verification policy and
hold/release timing are unchanged. This fixes unnecessary reconnects on a quiet
healthy socket; it does not prove all three observed reconnects had that cause.

Offline tests cover both an idle socket with pong and a socket without pong.
The focused suite now passes 203 tests and 3 subtests; Ruff passes. Natural-event
reconnect behaviour still requires observation after deployment.

## Production checkpoint, 16:48 CST

- Deployed and pushed `9adacd6c` to **xiuxian-mian/main**, not upstream origin.
- Explicit drain began 16:37:42, old worker exited cleanly at 16:37:50. Startup
  began 16:40:23; fully ready 16:40:50. Supervisor/worker: `276474` / `276476`;
  `NRestarts=0`. Observer's two inactive alerts refer to this planned deployment.
- Both root-only backups passed quick_check:
  `/root/xiuxian-before-boss-turnstile-20261002.db` and
  `/root/xiuxian-before-boss-turnstile-20261002-stopped.db`.
- Broker source mounts now point to `/opt/xiuxian-main`, not the lab. Fresh
  post-rollout verification probe acquired a token in **5.3s**, without any game
  request. Broker container healthy, restart count zero.
- At 16:44:15 authenticated UI API changed only `turnstile_enabled`. UI and DB
  readback both confirm enabled; selected identity remains `301299112`, other
  23 excluded, effective account limit one. Existing Boss selection/skip/rotation
  keys unchanged. All per-identity module-enable settings unchanged; both incense
  refinement flags remain zero. Fishing and rift timers match the stopped backup.
- Main service, safety watchdog and observer active; health/watchdog passed.
  Defensive preflight: pending queue empty; WA rift due 17:38:17, preparation
  checkpoint approximately 17:28. Sidecar remains intentionally inactive.
- The next natural Boss is still required. The latest two observed openings
  were October 1 and 2 at 13:40; this is historical timing, not a guaranteed schedule.
- Existing unrelated working changes remain untouched:
  `deploy/xiuxian-r2-backup.sh`, `deploy/backup_engine.py`,
  `tests/test_snapshot_sqlite_db.py`.
