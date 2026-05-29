import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RealMessageSample:
    sample_id: str
    text: str
    source: str = ""
    module: str = ""
    family: str = ""
    event_type: str = "message"


def _normalize_sample(sample_id, payload):
    if not isinstance(payload, dict):
        raise ValueError(f"replay sample {sample_id!r} must be an object")
    text = str(payload.get("text") or "")
    if not text.strip():
        raise ValueError(f"replay sample {sample_id!r} is missing text")
    source = str(payload.get("source") or "").strip()
    if not source:
        raise ValueError(f"replay sample {sample_id!r} is missing source")
    module = str(payload.get("module") or "").strip()
    if not module:
        raise ValueError(f"replay sample {sample_id!r} is missing module")
    family = str(payload.get("family") or "").strip()
    if not family:
        raise ValueError(f"replay sample {sample_id!r} is missing family")
    event_type = str(payload.get("event_type") or "message").strip() or "message"
    if event_type not in {"message", "edit", "sent"}:
        raise ValueError(f"replay sample {sample_id!r} has unsupported event_type={event_type!r}")
    return RealMessageSample(
        sample_id=str(sample_id),
        text=text,
        source=source,
        module=module,
        family=family,
        event_type=event_type,
    )


def load_real_message_samples(path):
    sample_path = Path(path)
    with sample_path.open("r", encoding="utf-8") as fp:
        raw = json.load(fp)
    if not isinstance(raw, dict):
        raise ValueError("real message replay file must be a JSON object keyed by sample id")
    return {
        str(sample_id): _normalize_sample(sample_id, payload)
        for sample_id, payload in raw.items()
    }


def get_real_message_sample(path, sample_id):
    samples = load_real_message_samples(path)
    try:
        return samples[str(sample_id)]
    except KeyError:
        raise KeyError(f"unknown real message replay sample: {sample_id}") from None


def get_real_message_text(path, sample_id):
    return get_real_message_sample(path, sample_id).text


def iter_real_message_samples(path, *, module="", family="", event_type=""):
    samples = load_real_message_samples(path)
    module = str(module or "").strip()
    family = str(family or "").strip()
    event_type = str(event_type or "").strip()
    for sample in samples.values():
        if module and sample.module != module:
            continue
        if family and sample.family != family:
            continue
        if event_type and sample.event_type != event_type:
            continue
        yield sample


__all__ = [
    "RealMessageSample",
    "get_real_message_sample",
    "get_real_message_text",
    "iter_real_message_samples",
    "load_real_message_samples",
]
