import os
import time

from .config import (
    LOG_RETENTION_CLEANUP_INTERVAL_SEC,
    MESSAGE_LOG_MAX_MB,
    MESSAGE_LOG_RETENTION_DAYS,
    MESSAGES_DIR,
)


_last_message_cleanup_at = 0.0


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _max_bytes_from_mb(value):
    mb = _safe_int(value)
    return max(0, mb) * 1024 * 1024


def _iter_log_paths(base_dir, *, suffixes=(".log",), recursive=False):
    suffixes = tuple(str(suffix or "") for suffix in suffixes if str(suffix or ""))
    if not suffixes:
        return
    if recursive:
        for root, _dirs, names in os.walk(base_dir):
            for name in names:
                if name.endswith(suffixes):
                    yield os.path.join(root, name)
        return
    for name in os.listdir(base_dir):
        if name.endswith(suffixes):
            yield os.path.join(base_dir, name)


def cleanup_log_files(
    base_dir,
    *,
    suffixes=(".log",),
    retention_days=0,
    max_bytes=0,
    recursive=False,
    now=None,
):
    now = _safe_float(now) or time.time()
    retention_days = _safe_int(retention_days)
    max_bytes = _safe_int(max_bytes)
    cutoff = now - retention_days * 86400 if retention_days > 0 else 0
    kept = []
    deleted = 0
    deleted_bytes = 0
    try:
        paths = list(_iter_log_paths(base_dir, suffixes=suffixes, recursive=recursive))
    except OSError:
        return {"deleted": 0, "deleted_bytes": 0, "kept_bytes": 0}

    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if cutoff and stat.st_mtime < cutoff:
            try:
                os.unlink(path)
                deleted += 1
                deleted_bytes += int(stat.st_size or 0)
            except OSError:
                pass
            continue
        kept.append((float(stat.st_mtime or 0), int(stat.st_size or 0), path))

    total_bytes = sum(size for _mtime, size, _path in kept)
    if max_bytes > 0 and total_bytes > max_bytes:
        kept.sort(key=lambda item: (item[0], item[2]))
        while total_bytes > max_bytes and len(kept) > 1:
            _mtime, size, path = kept.pop(0)
            try:
                os.unlink(path)
                deleted += 1
                deleted_bytes += size
                total_bytes -= size
            except OSError:
                pass

    return {"deleted": deleted, "deleted_bytes": deleted_bytes, "kept_bytes": max(0, total_bytes)}


def cleanup_message_logs(now=None):
    global _last_message_cleanup_at
    now = _safe_float(now) or time.time()
    if _last_message_cleanup_at and now - _last_message_cleanup_at < LOG_RETENTION_CLEANUP_INTERVAL_SEC:
        return {"deleted": 0, "deleted_bytes": 0, "kept_bytes": 0}
    _last_message_cleanup_at = now
    return cleanup_log_files(
        MESSAGES_DIR,
        suffixes=(".log",),
        retention_days=MESSAGE_LOG_RETENTION_DAYS,
        max_bytes=_max_bytes_from_mb(MESSAGE_LOG_MAX_MB),
        recursive=False,
        now=now,
    )


__all__ = [
    "cleanup_log_files",
    "cleanup_message_logs",
]
