#!/usr/bin/env python3
"""列出每个 /api/* 写入端点接受的 payload 键。

model/ui.py 里有两套路由注册：`elif path == "/api/x":` 链，和一张
`"/api/x": {"handler": ...}` 字典表。两者都要扫，只扫一套会漏掉一半。

用法：.venv/bin/python tools/dump_ui_write_keys.py [--md]
"""

import pathlib
import re
import sys

UI = pathlib.Path(__file__).resolve().parents[1] / "model" / "ui.py"

INLINE = re.compile(r'payload\.get\(\s*"([^"]+)"')
CALL = re.compile(r"\b([a-z_][a-z0-9_]*)\(\s*payload\s*[,)]")
CHAIN = re.compile(r'^\s*(?:el)?if path == "(/api/[^"]+)":')
TABLE = re.compile(r'^(\s+)"(/api/[^"]+)": \{')
FUNC = re.compile(r"^def ([a-z_][a-z0-9_]*)\(")


def _function_bodies(lines):
    starts = [(i, m.group(1)) for i, l in enumerate(lines) if (m := FUNC.match(l))]
    bodies = {}
    for idx, (i, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        bodies[name] = "\n".join(lines[i:end])
    return bodies


def _keys_from(block, bodies):
    keys = list(dict.fromkeys(INLINE.findall(block)))
    via = []
    for name in CALL.findall(block):
        if name in bodies:
            via.append(name)
            for key in INLINE.findall(bodies[name]):
                if key not in keys:
                    keys.append(key)
    return keys, via


def collect():
    lines = UI.read_text().splitlines()
    bodies = _function_bodies(lines)
    routes = {}

    # 1) elif 链：块到下一个 route 为止
    current = None
    for i, line in enumerate(lines):
        if (m := CHAIN.match(line)):
            current = {"path": m.group(1), "line": i + 1, "keys": [], "via": []}
            routes[m.group(1)] = current
            continue
        if current is None:
            continue
        for key in INLINE.findall(line):
            if key not in current["keys"]:
                current["keys"].append(key)
        for name in CALL.findall(line):
            if name in bodies and name not in current["via"]:
                current["via"].append(name)
                for key in INLINE.findall(bodies[name]):
                    if key not in current["keys"]:
                        current["keys"].append(key)

    # 2) 字典表：按花括号配对切块，别用固定行窗口（会串到下一个路由）
    i = 0
    while i < len(lines):
        m = TABLE.match(lines[i])
        if not m:
            i += 1
            continue
        depth, j = 0, i
        while j < len(lines):
            depth += lines[j].count("{") - lines[j].count("}")
            if depth == 0 and j > i:
                break
            j += 1
        keys, via = _keys_from("\n".join(lines[i:j + 1]), bodies)
        routes[m.group(2)] = {"path": m.group(2), "line": i + 1, "keys": keys, "via": via}
        i = j + 1

    return [routes[k] for k in sorted(routes)]


def main():
    rows = collect()
    as_md = "--md" in sys.argv
    if as_md:
        print("| 端点 | 写入键 | 经由 |")
        print("| --- | --- | --- |")
        for r in rows:
            keys = "`" + "`, `".join(r["keys"]) + "`" if r["keys"] else "—"
            via = "`" + "`, `".join(r["via"]) + "`" if r["via"] else ""
            print(f'| `{r["path"]}` | {keys} | {via} |')
    else:
        for r in rows:
            print(f'{r["path"]:44s} L{r["line"]:<6} {", ".join(r["keys"]) or "-"}')
    print(f"\n共 {len(rows)} 个端点，其中 {sum(1 for r in rows if r['keys'])} 个接受 payload 键。",
          file=sys.stderr)


if __name__ == "__main__":
    main()
