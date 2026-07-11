#!/usr/bin/env python3
"""Read-only CommandAttempt timeline report for Gate 1 shadow data."""

import argparse
import json
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model.command_attempt import (  # noqa: E402
    get_attempt,
    list_evidence,
    list_open_attempts,
    list_transitions,
)


def _json_ready(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def attempt_payload(op_id):
    return {
        "attempt": _json_ready(asdict(get_attempt(op_id))),
        "transitions": [_json_ready(asdict(item)) for item in list_transitions(op_id)],
        "evidence": [_json_ready(asdict(item)) for item in list_evidence(op_id)],
    }


def _format_timeline(payload):
    attempt = payload["attempt"]
    lines = [
        (
            f"op={attempt['op_id']} identity={attempt['send_as_id']} "
            f"module={attempt['source_module'] or '-'} family={attempt['command_family'] or '-'}"
        ),
        (
            f"transport={attempt['transport']} business={attempt['business']} "
            f"version={attempt['version']} msg={attempt['root_msg_id']}/{attempt['result_msg_id']}"
        ),
        f"command={attempt['command']}",
        "transitions:",
    ]
    for item in payload["transitions"]:
        lines.append(
            f"  #{item['seq']} {item['axis']} {item['from_state'] or '-'}->{item['to_state']} "
            f"key={item['transition_key']} code={item['code'] or '-'}"
        )
    lines.append("evidence:")
    for item in payload["evidence"]:
        lines.append(
            f"  #{item['seq']} {item['kind']} msg={item['msg_id']} edit={item['edit_seq']} "
            f"source={item['source'] or '-'} digest={item['text_digest'][:12] or '-'}"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--op-id", default="", help="Print one complete Attempt timeline")
    parser.add_argument("--identity", type=int, default=0, help="Filter open attempts by identity")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.op_id:
        payload = attempt_payload(args.op_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else _format_timeline(payload))
        return 0

    attempts = [
        _json_ready(asdict(item))
        for item in list_open_attempts(
            send_as_id=args.identity or None,
            limit=max(1, min(1000, args.limit)),
        )
    ]
    if args.json:
        print(json.dumps({"open_attempts": attempts}, ensure_ascii=False, indent=2))
        return 0
    for item in attempts:
        print(
            f"{item['op_id']} identity={item['send_as_id']} transport={item['transport']} "
            f"business={item['business']} module={item['source_module'] or '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
