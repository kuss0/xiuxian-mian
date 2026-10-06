"""Opt-in grouping of confirmed routine observations; no business state writes."""

import hashlib
import json
from collections import defaultdict
from html import escape

from .audit_messages import folded_summary_details, short_html_text
from .audit_wild import validate_wild_outcome


SUMMARY_TITLES = {"deep_retreat": "闭关", "yuanying": "元婴", "fishing_skip": "钓鱼跳过"}


def confirmed_summary_kind(kind, response):
    if kind not in SUMMARY_TITLES or not isinstance(response, dict) or response.get("ok") is not True:
        return ""
    extra = response.get("extra")
    if not isinstance(extra, dict) or extra.get("outcome_unknown") or extra.get("action_skipped"):
        return ""
    if kind == "fishing_skip":
        confirmed_skip = (
            extra.get("native") is True and extra.get("terminal_skip") is True
            and extra.get("status") == "skipped" and extra.get("outcome_unknown") is False
            and extra.get("committed") is False and extra.get("supply_committed") is False
            and extra.get("expected_wait") is False
        )
        return kind if confirmed_skip else ""
    sync = extra.get("sync") if "sync" in extra else extra.get("status_sync")
    return kind if isinstance(sync, dict) and sync.get("handled") is True else ""


def routine_bucket_key(kind, identity_id, content):
    key = ("routine", kind, identity_id,
           hashlib.sha256(str(content).encode("utf-8", errors="surrogatepass")).hexdigest())
    # Old v1 readers accept string keys but reject unknown tuple kinds.
    return json.dumps(key, separators=(",", ":")) if kind == "fishing_skip" else key


def _wild_summary(rows):
    receipts, conflicts, actors = {}, set(), {}
    for row in rows:
        result = row["wild_outcome"]
        key = (result["identity_id"], result["reset_at"], result["run"])
        identity = result["identity_id"]
        if identity not in actors or float(row.get("last_at") or 0) >= float(actors[identity].get("last_at") or 0):
            actors[identity] = row
        if key in receipts and receipts[key]["wild_outcome"] != result:
            conflicts.add(key)
        receipts[key] = row
    totals = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    counts = defaultdict(int)
    for key, row in receipts.items():
        identity = key[0]
        if key in conflicts:
            continue
        counts[identity] += 1
        for name, amount in row["wild_outcome"]["gains"].items():
            totals[identity][name][0 if amount >= 0 else 1] += amount
    heading = f"野外：{len(actors)} 个身份，{sum(counts.values())} 次结算"
    if conflicts:
        heading += f"；{len(conflicts)} 份冲突结算未计收益"
    details = []
    for identity in sorted(actors):
        parts = []
        for name, (gain, loss) in sorted(totals[identity].items(), key=lambda item: (item[0] != "修为", item[0])):
            amounts = "/".join(f"{n:+d}" for n in (gain, loss) if n)
            if amounts:
                parts.append(escape(name) + amounts)
        actor = actors[identity].get("wild_actor") or str(identity)
        details.append(f"<code>{escape(actor)}</code> {counts[identity]} 次｜"
                       + ("，".join(parts) or "未计入物资变化"))
    return heading, details


def format_grouped_summary(rows, *, now_text, max_details=20):
    groups = defaultdict(list)
    for row in rows:
        if row.get("wild_outcome") is not None:
            try:
                validate_wild_outcome(row["wild_outcome"], row.get("identity_id"))
            except ValueError:
                pass
            else:
                groups["wild"].append(row)
                continue
        kind = row.get("summary_kind") or row.get("presentation_kind")
        groups[kind if kind in SUMMARY_TITLES else "other"].append(row)
    headings, details = [], []
    for kind in (*SUMMARY_TITLES, "wild", "other"):
        items = groups.get(kind, [])
        if not items:
            continue
        if kind == "wild":
            heading, group_details = _wild_summary(items)
            headings.append(heading)
            details.append([short_html_text(body, 200) for body in group_details])
            continue
        title = SUMMARY_TITLES.get(kind, "其他记录")
        total = sum(int(row.get("count") or 0) for row in items)
        actors = {row["identity_id"] for row in items if row.get("identity_id") is not None}
        headings.append(f"{title}：" + (f"{len(actors)} 个身份，" if actors else "") + f"{total} 条记录")
        if kind == "fishing_skip":
            continue
        # Show the latest observation per stable identity, not a success total.
        latest = {}
        for index, row in enumerate(items):
            key = row.get("identity_id") if kind != "other" else index
            if key not in latest or float(row.get("last_at") or 0) >= float(latest[key].get("last_at") or 0):
                latest[key] = row
        group_details = []
        for row in sorted(latest.values(), key=lambda r: (str(r.get("identity_id", "")), int(r.get("seq") or 0))):
            body = row.get("html") or str(row.get("plain") or "-")
            group_details.append(f"{title} / {row.get('last_ts') or '?'} {short_html_text(body, 140)}")
        details.append(group_details)
    # Round-robin details keep a large channel group from hiding other modules.
    interleaved = [group[i] for i in range(max(map(len, details), default=0)) for group in details if i < len(group)]
    budget = max(1, min(20, int(max_details)))
    shown = interleaved[:budget]
    if len(interleaved) > budget:
        shown = interleaved[:budget - 1] + [f"另 {len(interleaved) - budget + 1} 条明细，详见后台日志"]
    return (f"<b>运行摘要 · {now_text}</b>\n" + "\n".join(headings)
            + ("\n" + folded_summary_details(shown, limit=2800) if shown else ""))
