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
