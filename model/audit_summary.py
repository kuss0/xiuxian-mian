"""Opt-in grouping of confirmed routine observations; no business state writes."""

import hashlib
import json
from collections import defaultdict

from .audit_messages import folded_summary_details, short_html_text


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


def format_grouped_summary(rows, *, now_text, max_details=20):
    groups = defaultdict(list)
    for row in rows:
        kind = row.get("summary_kind") or row.get("presentation_kind")
        groups[kind if kind in SUMMARY_TITLES else "other"].append(row)
    headings, details = [], []
    for kind in (*SUMMARY_TITLES, "other"):
        items = groups.get(kind, [])
        if not items:
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
