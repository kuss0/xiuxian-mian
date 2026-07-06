#!/usr/bin/env python3
"""Summarize sanitized MiniApp request/response captures for handoff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model.miniapp_capture_summary import format_miniapp_capture_summary, get_miniapp_capture_summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Summarize sanitized MiniApp capture JSONL files.")
    parser.add_argument("--game", "--game-key", dest="game_key", required=True, help="MiniApp key, e.g. fishing/trial/cave_treasure/stargazer")
    parser.add_argument("--day", default="", help="Capture day YYYY-MM-DD. Defaults to latest file for the game.")
    parser.add_argument("--limit", type=int, default=200, help="Max recent records to scan.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of compact text.")
    args = parser.parse_args(argv)

    summary = get_miniapp_capture_summary(args.game_key, day=args.day, limit=args.limit)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_miniapp_capture_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
