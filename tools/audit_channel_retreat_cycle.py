"""Read-only selected-channel cycle evidence; never sends game requests."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--date', type=dt.date.fromisoformat, required=True)
    args = parser.parse_args()
    db = args.root / 'data/state/chaogu_state.db'
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
        meta = dict(conn.execute("SELECT key,value FROM meta WHERE key IN "
                                 "('channel_send_as_health','miniapp_state_records')"))
        cohort = set(json.loads(meta['channel_send_as_health'])['restore_identity_ids'])
        records = json.loads(meta['miniapp_state_records'])
        roles = conn.execute(
            'SELECT i.send_as_id,i.username,i.enabled,m.deep_retreat_enabled,'
            'r.deep_retreat_phase FROM identities i '
            'JOIN identity_module_state m USING(send_as_id) '
            'JOIN identity_runtime_state r USING(send_as_id)').fetchall()
    actions = {ident: [] for ident in cohort}
    capture = args.root / f'data/state/miniapp_capture/cave_treasure-{args.date}.jsonl'
    with capture.open() as stream:
        for line in stream:
            row = json.loads(line)
            parts = row.get('source', '').split(':')
            if len(parts) != 3 or parts[0] != 'cave_public_deep_retreat':
                continue
            ident = int(parts[1])
            if ident in actions and row.get('step_key', '').startswith('deep_seclusion:'):
                actions[ident].append(row)
    now = dt.datetime.now().timestamp()
    results = []
    for ident, name, enabled, retreat, phase in roles:
        if ident not in cohort:
            continue
        record = records.get(f'{ident}:cave_deep_retreat', {})
        state = record.get('state', {})
        snapshot = state.get('snapshot', {})
        rows = sorted(actions[ident], key=lambda row: row['created_at'])
        matched = all(row['request']['payload'].get('playerId') ==
                      -1000000000000 - ident for row in rows)
        settles = [row['created_at'] for row in rows
                   if row['step_key'] == 'deep_seclusion:settle' and row['ok']]
        starts = [row['created_at'] for row in rows
                  if row['step_key'] == 'deep_seclusion:start' and row['ok']]
        cycle = any(start > settle for settle in settles for start in starts)
        timestamps_valid = all(0 < row['created_at'] <= now for row in rows)
        verified = (state.get('ok') is True and state.get('parser_version') == 2 and
                    state.get('identity_verified') is True and
                    state.get('sync', {}).get('handled') is True and
                    phase == 'running' and snapshot.get('active') is True and
                    snapshot.get('known') is True and snapshot.get('ok') is True and
                    snapshot.get('conflicting') is False and
                    snapshot.get('end_ms', 0) / 1000 > now and
                    not state.get('outcome_unknown') and
                    state.get('action') == 'start' and
                    0 <= now - record.get('updated_at', 0) < 8 * 3600 and
                    bool(starts) and max(starts) < snapshot.get('end_ms', 0) / 1000)
        ok = (matched and cycle and timestamps_valid and verified and enabled == 0 and retreat == 1
              and not any(row['step_key'] == 'deep_seclusion:force' for row in rows))
        results.append(dict(identity=ident, name=name, accepted=ok,
                            settle_count=len(settles), start_count=len(starts),
                            selected_player_matches=matched, verified_running=verified))
    accepted = sum(row['accepted'] for row in results)
    print(json.dumps(dict(date=str(args.date), accepted=accepted,
                          total=len(results), rows=results), ensure_ascii=False, indent=2))
    return 0 if len(results) == 19 and accepted == 19 else 1


if __name__ == '__main__':
    raise SystemExit(main())
