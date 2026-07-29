#!/usr/bin/env python3
"""Read-only boundary report for the monolithic replica runtime."""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path


SEGMENT_ANCHORS = (
    ("notice_and_buttons", "_get_replica_success_cooldown_sec"),
    ("lightweight_domain_and_panels", "_get_lightweight_dungeon_state"),
    ("huanglong_scheduler", "_local_day_key"),
    ("virtual_hall_state_and_matching", "_get_replica_room_gua_records"),
    ("lightweight_command_handlers", "_send_lightweight_replica_notice"),
    ("dispatch_and_root_handlers", "_handle_replica_join_reply"),
)


class _CallCollector(ast.NodeVisitor):
    def __init__(self):
        self.names = set()

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            self.names.add(node.func.id)
        self.generic_visit(node)


def _module_tree(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _top_level_functions(tree):
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            collector = _CallCollector()
            collector.visit(node)
            result[node.name] = {
                "name": node.name,
                "line": int(node.lineno),
                "end_line": int(getattr(node, "end_lineno", node.lineno)),
                "async": isinstance(node, ast.AsyncFunctionDef),
                "calls": collector.names,
            }
    return result


def _segment_ranges(functions):
    ranges = []
    for segment, anchor in SEGMENT_ANCHORS:
        if anchor not in functions:
            raise RuntimeError(f"missing replica boundary anchor: {anchor}")
        ranges.append((segment, anchor, functions[anchor]["line"]))
    ranges.sort(key=lambda item: item[2])
    return ranges


def _segment_for_line(line, ranges):
    current = ranges[0][0]
    for segment, _anchor, start_line in ranges:
        if line < start_line:
            break
        current = segment
    return current


def _runtime_import_surface(project_root):
    surface = defaultdict(list)
    model_root = project_root / "model"
    for path in sorted(model_root.rglob("*.py")):
        if path.name == "app_replica.py":
            continue
        try:
            tree = _module_tree(path)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = str(node.module or "")
            if module != "app_replica" and not module.endswith(".app_replica"):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                surface[alias.name].append(str(path.relative_to(project_root)))
    return {name: sorted(paths) for name, paths in sorted(surface.items())}


def _strongly_connected_components(graph):
    index = 0
    stack = []
    on_stack = set()
    indices = {}
    lowlinks = {}
    components = []

    def visit(node):
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in graph.get(node, ()):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] != indices[node]:
            return
        component = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        components.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return components


def build_report(project_root):
    project_root = Path(project_root).resolve()
    source_path = project_root / "model" / "app_replica.py"
    tree = _module_tree(source_path)
    functions = _top_level_functions(tree)
    ranges = _segment_ranges(functions)
    function_names = set(functions)

    for item in functions.values():
        item["segment"] = _segment_for_line(item["line"], ranges)
        item["internal_calls"] = sorted(item["calls"] & function_names)

    graph = {name: set(item["internal_calls"]) for name, item in functions.items()}
    boundary_edges = Counter()
    boundary_samples = defaultdict(list)
    function_boundary_counts = Counter()
    for source, targets in graph.items():
        source_segment = functions[source]["segment"]
        for target in targets:
            target_segment = functions[target]["segment"]
            if source_segment == target_segment:
                continue
            edge = (source_segment, target_segment)
            boundary_edges[edge] += 1
            function_boundary_counts[source] += 1
            function_boundary_counts[target] += 1
            if len(boundary_samples[edge]) < 8:
                boundary_samples[edge].append(f"{source}->{target}")

    segment_rows = []
    for index, (segment, anchor, start_line) in enumerate(ranges):
        end_line = ranges[index + 1][2] - 1 if index + 1 < len(ranges) else max(
            item["end_line"] for item in functions.values()
        )
        members = [item for item in functions.values() if item["segment"] == segment]
        outgoing = sum(count for (source, _target), count in boundary_edges.items() if source == segment)
        incoming = sum(count for (_source, target), count in boundary_edges.items() if target == segment)
        segment_rows.append({
            "segment": segment,
            "anchor": anchor,
            "start_line": start_line,
            "end_line": end_line,
            "function_count": len(members),
            "async_function_count": sum(1 for item in members if item["async"]),
            "boundary_outgoing_calls": outgoing,
            "boundary_incoming_calls": incoming,
        })

    cross_segment_components = []
    for component in _strongly_connected_components(graph):
        if len(component) < 2:
            continue
        segments = sorted({functions[name]["segment"] for name in component})
        if len(segments) < 2:
            continue
        cross_segment_components.append({
            "size": len(component),
            "segments": segments,
            "sample": component[:12],
        })
    cross_segment_components.sort(key=lambda item: (-item["size"], item["segments"]))

    runtime_surface = _runtime_import_surface(project_root)
    runtime_surface_rows = []
    for name, paths in runtime_surface.items():
        item = functions.get(name)
        runtime_surface_rows.append({
            "name": name,
            "segment": item["segment"] if item else "non_function",
            "paths": paths,
        })

    return {
        "source": str(source_path.relative_to(project_root)),
        "line_count": len(source_path.read_text(encoding="utf-8").splitlines()),
        "function_count": len(functions),
        "async_function_count": sum(1 for item in functions.values() if item["async"]),
        "segments": segment_rows,
        "runtime_import_surface": runtime_surface_rows,
        "boundary_edges": [
            {
                "source": source,
                "target": target,
                "call_count": count,
                "samples": boundary_samples[(source, target)],
            }
            for (source, target), count in sorted(
                boundary_edges.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "top_boundary_functions": [
            {
                "name": name,
                "segment": functions[name]["segment"],
                "boundary_call_count": count,
            }
            for name, count in function_boundary_counts.most_common(20)
        ],
        "cross_segment_cycles": cross_segment_components,
    }


def _print_text(report):
    print(
        f"{report['source']}: {report['line_count']} lines, "
        f"{report['function_count']} functions ({report['async_function_count']} async)"
    )
    print("segments:")
    for row in report["segments"]:
        print(
            f"- {row['segment']}: lines {row['start_line']}-{row['end_line']}, "
            f"functions={row['function_count']}, async={row['async_function_count']}, "
            f"boundary out/in={row['boundary_outgoing_calls']}/{row['boundary_incoming_calls']}"
        )
    print("runtime import surface:")
    for row in report["runtime_import_surface"]:
        print(f"- {row['name']} [{row['segment']}] <- {', '.join(row['paths'])}")
    print("largest boundary edges:")
    for row in report["boundary_edges"][:12]:
        print(f"- {row['source']} -> {row['target']}: {row['call_count']}")
    print(f"cross-segment cycles: {len(report['cross_segment_cycles'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report(args.project_root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_text(report)


if __name__ == "__main__":
    main()
