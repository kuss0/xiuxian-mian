import hashlib
import json
from pathlib import Path

from . import module_manifest
from .message_contract import replay_module_for_family


TEXT_FIELDS = (
    "matched_text",
    "text",
    "raw_text",
    "message_text",
    "message",
    "content",
)
FAMILY_FIELDS = ("family", "reply_family", "matched_family")
EVENT_TYPES = {"message", "edit", "sent"}


def _clean_text(value):
    return str(value or "").replace("\r", "\n").strip()


def _text_hash(text):
    return hashlib.sha1(_clean_text(text).encode("utf-8")).hexdigest()[:8]


def _safe_event_type(value):
    text = str(value or "message").strip() or "message"
    return text if text in EVENT_TYPES else "message"


def _record_family(record):
    if not isinstance(record, dict):
        return ""
    for field in FAMILY_FIELDS:
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return ""


def _record_text(record):
    if not isinstance(record, dict):
        return ""
    for field in TEXT_FIELDS:
        text = _clean_text(record.get(field))
        if text:
            return text
    return ""


def _record_msg_id(record):
    if not isinstance(record, dict):
        return ""
    for field in ("msg_id", "message_id", "source_message_id", "id"):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return ""


def _iter_json_records_from_value(value, source):
    if isinstance(value, list):
        for index, item in enumerate(value, 1):
            yield item, f"{source}:{index}"
    elif isinstance(value, dict):
        if _record_text(value) or _record_family(value):
            yield value, source
        else:
            for key, item in value.items():
                yield item, f"{source}:{key}"


def iter_candidate_records(paths):
    for raw_path in tuple(paths or ()):
        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_file():
            continue
        if path.suffix in {".jsonl", ".log"}:
            with path.open("r", encoding="utf-8", errors="replace") as fp:
                for line_no, line in enumerate(fp, 1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                    except (TypeError, ValueError):
                        continue
                    yield from _iter_json_records_from_value(payload, f"{path}:{line_no}")
            continue
        if path.suffix == ".json":
            try:
                with path.open("r", encoding="utf-8") as fp:
                    payload = json.load(fp)
            except (OSError, TypeError, ValueError):
                continue
            yield from _iter_json_records_from_value(payload, str(path))


def _load_fixture_payload(path):
    if not path:
        return {}
    fixture_path = Path(path).expanduser()
    if not fixture_path.exists():
        return {}
    with fixture_path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return payload if isinstance(payload, dict) else {}


def missing_families_from_fixture(fixture_path):
    samples = _load_fixture_payload(fixture_path)
    readiness = module_manifest.summarize_module_readiness(samples)
    missing = set()
    for row in readiness["modules"]:
        missing.update(row.get("missing_sample_families") or ())
    return missing


def build_candidate_sample_suggestions(
    paths,
    *,
    fixture_path=None,
    include_covered=False,
    include_archived=False,
    limit=100,
    module="",
    family="",
):
    requested_module = str(module or "").strip()
    requested_family = str(family or "").strip()
    filter_covered = bool(fixture_path) and not include_covered
    wanted_missing = missing_families_from_fixture(fixture_path) if filter_covered else set()
    suggestions = []
    seen = set()
    skipped = {
        "no_family": 0,
        "unknown_family": 0,
        "covered_family": 0,
        "archived_family": 0,
        "module_filtered": 0,
        "family_filtered": 0,
        "no_text": 0,
        "duplicate_text": 0,
    }
    safe_limit = max(1, int(limit or 100))

    for record, source in iter_candidate_records(paths):
        candidate_family = _record_family(record)
        if not candidate_family:
            skipped["no_family"] += 1
            continue
        if requested_family and candidate_family != requested_family:
            skipped["family_filtered"] += 1
            continue
        module_name = module_manifest.get_module_name_for_reply_family(candidate_family)
        if not module_name:
            skipped["unknown_family"] += 1
            continue
        if module_manifest.is_reply_family_archived(candidate_family) and not include_archived:
            skipped["archived_family"] += 1
            continue
        if requested_module and module_name != requested_module:
            skipped["module_filtered"] += 1
            continue
        if filter_covered and candidate_family not in wanted_missing:
            skipped["covered_family"] += 1
            continue
        text = _record_text(record)
        if not text:
            skipped["no_text"] += 1
            continue
        key = (candidate_family, _text_hash(text))
        if key in seen:
            skipped["duplicate_text"] += 1
            continue
        seen.add(key)
        msg_id = _record_msg_id(record)
        event_type = _safe_event_type(record.get("event_type") or record.get("kind"))
        suffix = msg_id or str(len(suggestions) + 1)
        sample_id = f"candidate.{candidate_family}.{event_type}.{suffix}.{key[1]}"
        replay_module = replay_module_for_family(candidate_family) or module_name or str(record.get("module") or "").strip()
        suggestions.append(
            {
                "sample_id": sample_id,
                "payload": {
                    "source": source,
                    "module": replay_module,
                    "family": candidate_family,
                    "event_type": event_type,
                    "text": text,
                },
            }
        )
        if len(suggestions) >= safe_limit:
            break

    by_family = {}
    for item in suggestions:
        candidate_family = item["payload"]["family"]
        by_family[candidate_family] = by_family.get(candidate_family, 0) + 1
    return {
        "total": len(suggestions),
        "by_family": dict(sorted(by_family.items())),
        "suggestions": suggestions,
        "skipped": skipped,
    }


__all__ = [
    "build_candidate_sample_suggestions",
    "iter_candidate_records",
    "missing_families_from_fixture",
]
