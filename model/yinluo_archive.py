"""Cold native Yinluo evidence; writes participate in the state transaction."""

from dataclasses import dataclass
import hashlib
import json


MAX_COMMAND_BYTES = 2 * 1024 * 1024


class ArchiveConflict(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveChange:
    identity_id: int
    account_id: int
    chat_id: int
    command_msg_id: int
    previous_digest: str | None
    payload: dict | None

    @property
    def key(self):
        return self.identity_id, self.account_id, self.chat_id, self.command_msg_id


@dataclass(frozen=True)
class BusinessPointChange:
    identity_id: int
    account_id: int
    business_key: str
    previous_digest: str | None
    payload: dict | None

    @property
    def key(self):
        return self.identity_id, self.account_id, self.business_key


def _encode(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ArchiveConflict("yinluo_archive_record_too_large")
    return encoded


def _digest(encoded):
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def empty_business_manifest():
    return {"count": 0, "digest": _digest("")}


def business_manifest(conn, identity_id, account_id, *, changes=()):
    from .yinluo_accounting import _business_key_parts

    rows = dict(conn.execute(
        "SELECT business_key, digest FROM yinluo_archive_business WHERE identity_id=? AND account_id=?",
        (identity_id, account_id),
    ))
    for key, digest in rows.items():
        if (_business_key_parts(key) is None or not isinstance(digest, str)
                or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
            raise ArchiveConflict("corrupt_yinluo_business_manifest")
    for change in changes:
        if change.key[:2] != (identity_id, account_id) or rows.get(change.business_key) != change.previous_digest:
            raise ArchiveConflict("stale_yinluo_business_manifest")
        if change.payload is None:
            rows.pop(change.business_key, None)
        else:
            rows[change.business_key] = _digest(_encode(change.payload))
    digest = hashlib.sha256()
    for key in sorted(rows):
        digest.update(json.dumps([key, rows[key]], separators=(",", ":")).encode("ascii"))
    return {"count": len(rows), "digest": digest.hexdigest()}


def read_command(conn, identity_id, account_id, chat_id, command_msg_id):
    row = conn.execute(
        "SELECT CASE WHEN length(CAST(payload AS BLOB)) <= ? THEN payload END, digest "
        "FROM yinluo_archive_commands WHERE identity_id=? AND account_id=? AND chat_id=? AND command_msg_id=?",
        (MAX_COMMAND_BYTES, identity_id, account_id, chat_id, command_msg_id),
    ).fetchone()
    if row is None:
        return None
    encoded, digest = row[0], row[1]
    if not isinstance(encoded, str) or _digest(encoded) != digest:
        raise ArchiveConflict("corrupt_yinluo_archive_digest")
    try:
        payload = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ArchiveConflict("corrupt_yinluo_archive_payload") from exc
    if not isinstance(payload, dict):
        raise ArchiveConflict("corrupt_yinluo_archive_payload")
    return payload, digest


def result_owners(conn, chat_id, msg_id, sender_id=None):
    rows = conn.execute(
        "SELECT identity_id, account_id, command_msg_id, sender_id FROM yinluo_archive_messages "
        "WHERE chat_id=? AND msg_id=?" + (" AND sender_id=?" if sender_id is not None else ""),
        (chat_id, msg_id, sender_id) if sender_id is not None else (chat_id, msg_id),
    ).fetchall()
    return [tuple(row) for row in rows]


def read_business_point(conn, identity_id, account_id, business_key):
    row = conn.execute(
        "SELECT CASE WHEN length(CAST(payload AS BLOB)) <= ? THEN payload END, digest "
        "FROM yinluo_archive_business WHERE identity_id=? AND account_id=? AND business_key=?",
        (MAX_COMMAND_BYTES, identity_id, account_id, business_key),
    ).fetchone()
    if row is None:
        return None
    encoded, digest = row[0], row[1]
    if not isinstance(encoded, str) or _digest(encoded) != digest:
        raise ArchiveConflict("corrupt_yinluo_business_digest")
    try:
        payload = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ArchiveConflict("corrupt_yinluo_business_payload") from exc
    from .yinluo_accounting import _validate_business_payload
    _validate_business_payload(payload, identity_id, account_id, business_key)
    return payload, digest


def apply_changes(conn, changes, *, business_changes=()):
    """No commit here: archive and hot state must succeed or roll back together."""
    from .yinluo_accounting import _business_key_parts, _validate_archive_payload, _validate_business_payload

    changes = tuple(changes)
    if any(not isinstance(change, ArchiveChange) for change in changes):
        raise ArchiveConflict("invalid_yinluo_archive_change")
    encoded_payloads = []
    for change in changes:
        if (any(type(item) is not int or not 0 < item < 2 ** 63 for item in
                (change.identity_id, change.account_id, change.command_msg_id))
                or type(change.chat_id) is not int or not 0 < abs(change.chat_id) < 2 ** 63
                or (change.previous_digest is not None and (
                    not isinstance(change.previous_digest, str) or len(change.previous_digest) != 64
                    or any(char not in "0123456789abcdef" for char in change.previous_digest)
                ))):
            raise ArchiveConflict("invalid_yinluo_archive_key")
        if change.payload is not None:
            _validate_archive_payload(change.payload, *change.key)
            encoded_payloads.append(_encode(change.payload))
        else:
            encoded_payloads.append(None)
    if len({change.key for change in changes}) != len(changes):
        raise ArchiveConflict("duplicate_yinluo_archive_change")
    business_changes = tuple(business_changes)
    business_payloads = []
    for change in business_changes:
        if (not isinstance(change, BusinessPointChange)
                or any(type(item) is not int or not 0 < item < 2 ** 63 for item in (change.identity_id, change.account_id))
                or _business_key_parts(change.business_key) is None
                or (change.previous_digest is not None and (
                    not isinstance(change.previous_digest, str) or len(change.previous_digest) != 64
                    or any(char not in "0123456789abcdef" for char in change.previous_digest)
                ))):
            raise ArchiveConflict("invalid_yinluo_business_key")
        if change.payload is not None:
            _validate_business_payload(change.payload, *change.key)
        business_payloads.append(_encode(change.payload) if change.payload is not None else None)
    if len({change.key for change in business_changes}) != len(business_changes):
        raise ArchiveConflict("duplicate_yinluo_business_change")
    if not changes and not business_changes:
        return
    # Acquire the write lock before checking revisions, including on a fresh
    # connection where the first SELECT would otherwise run outside a transaction.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    for change in changes:
        row = conn.execute(
            "SELECT digest FROM yinluo_archive_commands "
            "WHERE identity_id=? AND account_id=? AND chat_id=? AND command_msg_id=?", change.key,
        ).fetchone()
        if (row[0] if row else None) != change.previous_digest:
            raise ArchiveConflict("stale_yinluo_archive_change")
    for change in business_changes:
        row = conn.execute(
            "SELECT digest FROM yinluo_archive_business WHERE identity_id=? AND account_id=? AND business_key=?",
            change.key,
        ).fetchone()
        if (row[0] if row else None) != change.previous_digest:
            raise ArchiveConflict("stale_yinluo_business_change")
    for change, encoded in zip(changes, encoded_payloads):
        conn.execute(
            "DELETE FROM yinluo_archive_messages "
            "WHERE identity_id=? AND account_id=? AND chat_id=? AND command_msg_id=?", change.key,
        )
        conn.execute(
            "DELETE FROM yinluo_archive_commands "
            "WHERE identity_id=? AND account_id=? AND chat_id=? AND command_msg_id=?", change.key,
        )
        if change.payload is None:
            continue
        conn.execute(
            "INSERT INTO yinluo_archive_commands "
            "(identity_id, account_id, chat_id, command_msg_id, payload, digest) VALUES (?, ?, ?, ?, ?, ?)",
            (*change.key, encoded, _digest(encoded)),
        )
        results = {(receipt["end"]["evidence"]["msg_id"], receipt["sender_id"])
                   for receipt in change.payload["receipts"]}
        for msg_id, sender_id in sorted(results):
            conn.execute(
                "INSERT INTO yinluo_archive_messages "
                "(identity_id, account_id, chat_id, command_msg_id, msg_id, sender_id) VALUES (?, ?, ?, ?, ?, ?)",
                (*change.key, msg_id, sender_id),
            )
    for change, encoded in zip(business_changes, business_payloads):
        conn.execute(
            "DELETE FROM yinluo_archive_business WHERE identity_id=? AND account_id=? AND business_key=?",
            change.key,
        )
        if encoded is not None:
            conn.execute(
                "INSERT INTO yinluo_archive_business (identity_id, account_id, business_key, payload, digest) "
                "VALUES (?, ?, ?, ?, ?)", (*change.key, encoded, _digest(encoded)),
            )


def stats(conn, identity_id, account_id):
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(length(CAST(payload AS BLOB))), 0) "
        "FROM yinluo_archive_commands WHERE identity_id=? AND account_id=?", (identity_id, account_id),
    ).fetchone()
    return {"commands": row[0], "payload_bytes": row[1]}


def business_stats(conn, identity_id, account_id):
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(length(CAST(payload AS BLOB))), 0) "
        "FROM yinluo_archive_business WHERE identity_id=? AND account_id=?", (identity_id, account_id),
    ).fetchone()
    return {"points": row[0], "payload_bytes": row[1]}
