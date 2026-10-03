"""Checkpoint the existing routine bucket; never replay ambiguous deliveries.

Single-process owner. SQLite is separate from the game state and all disk work
runs off the event loop. The lock is retained until a cancelled disk job ends.
"""

import asyncio
import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path


MAX_ROWS = 256
MAX_HELD = 48
MAX_BYTES = 8 * 1024 * 1024
MAX_ROW_BYTES = 24 * 1024
MAX_HELD_BYTES = 32 * 1024
PENDING_TTL = 86400
HELD_TTL = 7 * 86400
COMPACTED_KEY = "__audit_summary_compacted__"


def _encoded_size(value):
    return len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8"))


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid summary number")
    if integer and not isinstance(value, int):
        raise ValueError("invalid summary integer")
    return value


def _validate_row(row):
    if not isinstance(row, dict) or _encoded_size(row) > MAX_ROW_BYTES:
        raise ValueError("invalid summary row")
    key = row.get("bucket_key")
    if isinstance(key, list):
        if (len(key) != 4 or key[0] != "routine" or key[1] not in {"yuanying", "deep_retreat"}
                or (key[2] is not None and type(key[2]) is not int)
                or not isinstance(key[3], str) or len(key[3]) != 64):
            raise ValueError("invalid routine key")
        row["bucket_key"] = tuple(key)
    elif not isinstance(key, str) or not key or len(key) > 8192:
        raise ValueError("invalid summary key")
    for field in ("count", "seq"):
        _number(row.get(field), integer=True)
    for field in ("first_at", "last_at"):
        _number(row.get(field))
    if row.get("identity_id") is not None and type(row["identity_id"]) is not int:
        raise ValueError("invalid summary identity")
    if row.get("summary_kind", "") not in {"", "yuanying", "deep_retreat"}:
        raise ValueError("invalid summary kind")
    for field in ("html", "plain", "first_ts", "last_ts"):
        if not isinstance(row.get(field), str) or len(row[field]) > 32768:
            raise ValueError("invalid summary text")
    return row


def validate_summary_checkpoint(data):
    """Validate the on-disk format without importing game state or configuration."""
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("unsupported summary checkpoint")
    rows, held = data["rows"], data["held"]
    if not isinstance(rows, list) or len(rows) > MAX_ROWS + 1 or not isinstance(held, list) or len(held) > MAX_HELD:
        raise ValueError("invalid summary capacity")
    rows = [_validate_row(dict(row) if isinstance(row, dict) else row) for row in rows]
    if len({row["bucket_key"] for row in rows}) != len(rows):
        raise ValueError("duplicate summary rows")
    for item in held:
        if not isinstance(item, dict) or _encoded_size(item) > MAX_HELD_BYTES:
            raise ValueError("invalid held summary")
        for field in ("id", "hash", "message"):
            if not isinstance(item.get(field), str) or len(item[field]) > 32768:
                raise ValueError("invalid held text")
        _number(item.get("at"))
        _number(item.get("count"), integer=True)
    next_at = _number(data["next_at"])
    retired = _number(data["retired_records"], integer=True)
    return rows, held, next_at, retired


class AuditSummaryStore:
    def __init__(self, path, bucket, order, *, clock=time.time, report=print):
        self.path = Path(path)
        self.bucket, self.order = bucket, order
        self.clock, self.report = clock, report
        self.lock = asyncio.Lock()
        self.loaded = False
        self.sending = False
        self.next_at = 0.0
        self.held = []
        self.retired_records = 0
        self.last_error = ""
        self._reported_at = None

    def _disk(self, payload=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(self.path, timeout=0.25)) as conn, conn:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("CREATE TABLE IF NOT EXISTS summary_checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            if payload is not None:
                conn.execute("INSERT OR REPLACE INTO summary_checkpoint VALUES (1, ?)", (payload,))
                return None
            row = conn.execute("SELECT payload FROM summary_checkpoint WHERE id=1").fetchone()
            if row is None:
                return None
            if len(row[0].encode("utf-8")) > MAX_BYTES:
                raise ValueError("summary checkpoint exceeds limit")
            return json.loads(row[0])

    async def _io(self, payload=None):
        job = asyncio.create_task(asyncio.to_thread(self._disk, payload))
        cancelled = False
        while not job.done():
            try:
                await asyncio.shield(job)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            # Retrieve disk exceptions but never turn cancellation into success.
            if not job.cancelled():
                job.exception()
            raise asyncio.CancelledError
        return job.result()

    def _error(self, exc):
        self.last_error = type(exc).__name__
        now = self.clock()
        if self._reported_at is None or now - self._reported_at >= 300:
            self._reported_at = now
            self.report(f"audit summary checkpoint unavailable: {self.last_error}; routine delivery held")

    async def _load(self):
        if self.loaded:
            return True
        try:
            data = await self._io()
            if data is not None:
                rows, held, next_at, retired = validate_summary_checkpoint(data)
                # Do not alter memory until the entire checkpoint validates.
                for row in rows:
                    key = row["bucket_key"]
                    if key in self.bucket:
                        existing = self.bucket[key]
                        count = existing["count"] + row["count"]
                        first_at = min(existing["first_at"], row["first_at"])
                        if row["last_at"] > existing["last_at"]:
                            existing.update(row)
                        existing["count"], existing["first_at"] = count, first_at
                    else:
                        self.bucket[key] = row
                        self.order.append(key)
                self.held, self.next_at, self.retired_records = held, next_at, retired
            self.loaded = True
            self.last_error = ""
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _bound(self):
        now = self.clock()
        removed = 0
        for key in list(self.order):
            row = self.bucket[key]
            if key != COMPACTED_KEY and (now - row["last_at"] >= PENDING_TTL or _encoded_size(row) > MAX_ROW_BYTES):
                removed += self.bucket.pop(key)["count"]
                self.order.remove(key)
        detailed = [key for key in self.order if key != COMPACTED_KEY]
        for key in detailed[:max(0, len(detailed) - MAX_ROWS)]:
            removed += self.bucket.pop(key)["count"]
            self.order.remove(key)
        if removed:
            row = self.bucket.get(COMPACTED_KEY)
            if row is None:
                row = dict(bucket_key=COMPACTED_KEY, count=0, seq=0, first_at=now,
                           first_ts="", last_ts="", summary_kind="", identity_id=None)
                self.bucket[COMPACTED_KEY] = row
                self.order.append(COMPACTED_KEY)
            row["count"] += removed
            row["last_at"] = now
            row["plain"] = row["html"] = f"{row['count']} 条普通记录已压缩（容量/保留期），明细见本地日志"
        keep = [item for item in self.held if now - item["at"] < HELD_TTL][-MAX_HELD:]
        self.retired_records += sum(item["count"] for item in self.held if item not in keep)
        self.held = keep

    async def _save(self):
        try:
            self._bound()
            payload = json.dumps(dict(version=1, rows=[self.bucket[key] for key in self.order],
                                      held=self.held, next_at=self.next_at, retired_records=self.retired_records),
                                 ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            if len(payload.encode("utf-8")) > MAX_BYTES:
                raise ValueError("summary checkpoint exceeds limit")
            await self._io(payload)
            self.last_error = ""
            return True
        except Exception as exc:
            self._error(exc)
            return False

    async def resume(self, interval):
        async with self.lock:
            if not await self._load():
                return False
            # A restart never immediately drains an old backlog.
            self.next_at = max(self.next_at, self.clock() + interval)
            return await self._save()

    async def enqueue(self, add_row, interval):
        async with self.lock:
            loaded = await self._load()
            add_row()
            self._bound()
            if not self.next_at:
                self.next_at = self.clock() + interval
            return await self._save() if loaded else False

    async def flush(self, formatter, sender, interval):
        async with self.lock:
            if not await self._load():
                return False
            if self.sending or self.clock() < self.next_at:
                return False
            self._bound()
            if not self.bucket:
                return await self._save()
            rows = [dict(self.bucket[key]) for key in self.order]
            try:
                message = formatter(rows)
            except Exception as exc:
                self.next_at = self.clock() + interval
                await self._save()
                self._error(exc)
                return False
            batch = dict(id=uuid.uuid4().hex, at=self.clock(), count=sum(row["count"] for row in rows),
                         hash=hashlib.sha256(message.encode("utf-8")).hexdigest(), message=message)
            if _encoded_size(batch) > MAX_HELD_BYTES:
                self._error(ValueError("summary render exceeds limit"))
                self.next_at = self.clock() + interval
                return False
            self.held.append(batch)
            self.bucket.clear()
            self.order.clear()
            self.next_at = self.clock() + interval
            try:
                saved = await self._save()
            except asyncio.CancelledError:
                # No transport started. Memory is retryable; a crash may retain
                # the conservative held marker, never an automatic duplicate.
                self._restore_unsent(rows, batch)
                raise
            if not saved:
                self._restore_unsent(rows, batch)
                return False
            self.sending = True
        ok = False
        try:
            ok = await sender(message)
            return bool(ok)
        except Exception as exc:
            self._error(exc)
            return False
        finally:
            self.sending = False
            # False, exceptions and cancellation all remain held. The legacy
            # transport's bool cannot prove non-delivery, so never auto-replay.
            if ok:
                async with self.lock:
                    self.held = [item for item in self.held if item["id"] != batch["id"]]
                    await self._save()

    def _restore_unsent(self, rows, batch):
        self.held = [item for item in self.held if item["id"] != batch["id"]]
        for row in rows:
            self.bucket[row["bucket_key"]] = row
            self.order.append(row["bucket_key"])
