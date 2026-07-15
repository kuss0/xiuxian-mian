#!/usr/bin/env python3
"""Print the MiniApp command migration catalog and validation checklist."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model import ui
from model.features import miniapp_command_catalog, miniapp_registry


def build_report():
    plans = miniapp_registry.build_known_miniapp_flow_plans()
    return {
        "policy": "read-only architecture inventory; no send, state mutation, or migration activation",
        "catalog": miniapp_command_catalog.build_command_catalog_snapshot(),
        "validation": miniapp_command_catalog.validate_command_catalog(
            flow_plans=plans,
            entry_probe_commands=ui.MINIAPP_ENTRY_PROBE_COMMANDS,
        ),
    }


def format_report(payload):
    catalog = payload["catalog"]
    validation = payload["validation"]
    lines = [
        f"MiniApp command catalog {catalog['version']}",
        f"policy: {payload['policy']}",
        (
            f"categories={catalog['summary']['category_count']} groups={catalog['summary']['group_count']} "
            f"unique_commands={catalog['summary']['unique_commands']} validation={validation['status']}"
        ),
    ]
    for category in catalog["categories"]:
        lines.append(f"- {category['label']}: groups={category['group_count']} commands={category['command_count']}")
    for issue in validation["issues"]:
        lines.append(f"  {issue['level']}: {issue['code']} {issue.get('command', '')}".rstrip())
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build_report()
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else format_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
