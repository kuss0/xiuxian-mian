import hashlib
import json
import re
import time

from .. import persistence
from .types import (
    AttemptConflict,
    AttemptEvidence,
    AttemptNotFound,
    AttemptRecord,
    AttemptTransition,
    BUSINESS_TRANSITIONS,
    BusinessState,
    EvidenceKind,
    RecoveryPolicy,
    TRANSPORT_TRANSITIONS,
    TransportState,
)


_SENSITIVE_KEY_RE = re.compile(
    r"(?:token|cookie|session|authorization|password|passwd|secret|init[_-]?data)",
    re.IGNORECASE,
)
_TERMINAL_BUSINESS = {
    BusinessState.TERMINAL_OK.value,
    BusinessState.TERMINAL_FAIL.value,
    BusinessState.ABANDONED.value,
}


def _json_load(raw, default=None):
    if default is None:
        default = {}
    try:
        value = json.loads(str(raw or ""))
    except Exception:
        return default
    return value if isinstance(value, type(default)) else default


def _sanitize_payload(value, *, depth=0):
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _sanitize_payload(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _json_dump(value):
    return json.dumps(_sanitize_payload(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _attempt_from_row(row):
    if row is None:
        return None
    return AttemptRecord(
        op_id=str(row["op_id"]),
        chain_id=str(row["chain_id"] or ""),
        send_as_id=int(row["send_as_id"] or 0),
        account_id=int(row["account_id"] or 0),
        source_module=str(row["source_module"] or ""),
        command=str(row["command"] or ""),
        command_family=str(row["command_family"] or ""),
        priority=str(row["priority"] or ""),
        intent=_json_load(row["intent_json"], {}),
        transport=TransportState(str(row["transport"])),
        business=BusinessState(str(row["business"])),
        recovery_policy=RecoveryPolicy(str(row["recovery_policy"])),
        block_code=str(row["block_code"] or ""),
        block_reason=str(row["block_reason"] or ""),
        definitely_unsent=bool(row["definitely_unsent"]),
        root_msg_id=int(row["root_msg_id"] or 0),
        reply_to_msg_id=int(row["reply_to_msg_id"] or 0),
        result_msg_id=int(row["result_msg_id"] or 0),
        resend_count=int(row["resend_count"] or 0),
        max_resend=int(row["max_resend"] or 0),
        transport_due_at=float(row["transport_due_at"] or 0),
        business_due_at=float(row["business_due_at"] or 0),
        business_code=str(row["business_code"] or ""),
        business_summary=str(row["business_summary"] or ""),
        last_error=str(row["last_error"] or ""),
        last_transition_key=str(row["last_transition_key"] or ""),
        meta=_json_load(row["meta_json"], {}),
        version=int(row["version"] or 0),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
        sent_at=float(row["sent_at"] or 0),
        closed_at=float(row["closed_at"] or 0),
    )


def _transition_from_row(row):
    return AttemptTransition(
        id=int(row["id"]),
        op_id=str(row["op_id"]),
        seq=int(row["seq"]),
        axis=str(row["axis"]),
        from_state=str(row["from_state"] or ""),
        to_state=str(row["to_state"]),
        code=str(row["code"] or ""),
        summary=str(row["summary"] or ""),
        transition_key=str(row["transition_key"]),
        ts=float(row["ts"] or 0),
    )


def _evidence_from_row(row):
    return AttemptEvidence(
        id=int(row["id"]),
        op_id=str(row["op_id"]),
        seq=int(row["seq"]),
        kind=EvidenceKind(str(row["kind"])),
        msg_id=int(row["msg_id"] or 0),
        edit_seq=int(row["edit_seq"] or 0),
        family=str(row["family"] or ""),
        text_digest=str(row["text_digest"] or ""),
        source=str(row["source"] or ""),
        idempotency_key=str(row["idempotency_key"]),
        ts=float(row["ts"] or 0),
        payload=_json_load(row["payload_json"], {}),
    )


def _connect():
    persistence.init_db()
    return persistence.open_db_connection(row_factory=True, set_journal_mode=False)


def _get_attempt_row(conn, op_id):
    row = conn.execute("SELECT * FROM command_attempts WHERE op_id = ?", (str(op_id),)).fetchone()
    if row is None:
        raise AttemptNotFound(f"attempt not found: {op_id}")
    return row


def _next_seq(conn, table, op_id):
    row = conn.execute(
        f"SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM {table} WHERE op_id = ?",
        (str(op_id),),
    ).fetchone()
    return int(row["next_seq"] or 1)


def _append_transition(conn, *, op_id, axis, from_state, to_state, code, summary, transition_key, ts):
    seq = _next_seq(conn, "command_attempt_transitions", op_id)
    conn.execute(
        """
        INSERT INTO command_attempt_transitions(
            op_id, seq, axis, from_state, to_state, code, summary, transition_key, ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (op_id, seq, axis, from_state, to_state, code, summary, transition_key, ts),
    )


def create_attempt(record):
    now = float(record["created_at"])
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM command_attempts WHERE op_id = ?",
            (record["op_id"],),
        ).fetchone()
        if existing is not None:
            immutable_fields = {
                "send_as_id": int(record["send_as_id"]),
                "account_id": int(record.get("account_id", 0) or 0),
                "source_module": str(record.get("source_module", "")),
                "command": str(record.get("command", "")),
                "command_family": str(record.get("command_family", "")),
                "chain_id": str(record.get("chain_id", "")),
            }
            mismatched = [
                key
                for key, expected in immutable_fields.items()
                if existing[key] != expected
            ]
            if mismatched:
                raise AttemptConflict(
                    f"op_id already belongs to a different attempt: {record['op_id']} fields={','.join(mismatched)}"
                )
            return _attempt_from_row(existing)
        conn.execute(
            """
            INSERT INTO command_attempts(
                op_id, chain_id, send_as_id, account_id, source_module, command,
                command_family, priority, intent_json, transport, business,
                recovery_policy, reply_to_msg_id, max_resend, transport_due_at,
                business_due_at, meta_json, version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                record["op_id"],
                record.get("chain_id", ""),
                int(record["send_as_id"]),
                int(record.get("account_id", 0) or 0),
                record.get("source_module", ""),
                record.get("command", ""),
                record.get("command_family", ""),
                record.get("priority", ""),
                _json_dump(record.get("intent") or {}),
                TransportState.CREATED.value,
                BusinessState.OPEN.value,
                str(record.get("recovery_policy") or RecoveryPolicy.WAIT_LATE_EDIT.value),
                int(record.get("reply_to_msg_id", 0) or 0),
                max(0, int(record.get("max_resend", 0) or 0)),
                float(record.get("transport_due_at", 0) or 0),
                float(record.get("business_due_at", 0) or 0),
                _json_dump(record.get("meta") or {}),
                now,
                now,
            ),
        )
        _append_transition(
            conn,
            op_id=record["op_id"],
            axis="transport",
            from_state="",
            to_state=TransportState.CREATED.value,
            code="created",
            summary="",
            transition_key="create:transport",
            ts=now,
        )
        _append_transition(
            conn,
            op_id=record["op_id"],
            axis="business",
            from_state="",
            to_state=BusinessState.OPEN.value,
            code="created",
            summary="",
            transition_key="create:business",
            ts=now,
        )
        return _attempt_from_row(_get_attempt_row(conn, record["op_id"]))


def get_attempt(op_id):
    with _connect() as conn:
        return _attempt_from_row(_get_attempt_row(conn, op_id))


def _list_attempt_rows(where_sql, params, *, limit=100):
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM command_attempts WHERE {where_sql} ORDER BY created_at DESC LIMIT ?",
            (*params, max(1, min(1000, int(limit or 100)))),
        ).fetchall()
    return [_attempt_from_row(row) for row in rows]


def list_attempts_by_root_msg_id(root_msg_id, *, limit=20):
    root_msg_id = int(root_msg_id or 0)
    if root_msg_id <= 0:
        return []
    return _list_attempt_rows("root_msg_id = ?", (root_msg_id,), limit=limit)


def list_attempts_by_result_msg_id(result_msg_id, *, limit=20):
    result_msg_id = int(result_msg_id or 0)
    if result_msg_id <= 0:
        return []
    return _list_attempt_rows("result_msg_id = ?", (result_msg_id,), limit=limit)


def list_attempts_by_chain_id(chain_id, *, limit=100):
    chain_id = str(chain_id or "").strip()
    if not chain_id:
        return []
    return _list_attempt_rows("chain_id = ?", (chain_id,), limit=limit)


def list_bind_candidates(*, send_as_id=0, family="", event_at=0, window_sec=900, limit=100):
    clauses = ["business NOT IN (?, ?, ?)", "transport IN (?, ?, ?, ?)"]
    params = [
        BusinessState.TERMINAL_OK.value,
        BusinessState.TERMINAL_FAIL.value,
        BusinessState.ABANDONED.value,
        TransportState.SENT.value,
        TransportState.SENT_NO_ID.value,
        TransportState.SEND_UNKNOWN.value,
        TransportState.TIMED_OUT.value,
    ]
    if int(send_as_id or 0) > 0:
        clauses.append("send_as_id = ?")
        params.append(int(send_as_id))
    family = str(family or "").strip()
    if family:
        clauses.append("command_family = ?")
        params.append(family)
    event_at = float(event_at or 0)
    if event_at > 0:
        window_sec = max(1.0, float(window_sec or 0))
        clauses.append("sent_at BETWEEN ? AND ?")
        params.extend([event_at - window_sec, event_at + 5.0])
    return _list_attempt_rows(" AND ".join(clauses), tuple(params), limit=limit)


def transition_transport(
    op_id,
    target,
    *,
    transition_key,
    expected_version=None,
    code="",
    summary="",
    now=None,
    updates=None,
):
    target = TransportState(target)
    now = float(now or time.time())
    transition_key = str(transition_key or "").strip()
    if not transition_key:
        raise ValueError("transition_key is required")
    updates = dict(updates or {})
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT 1 FROM command_attempt_transitions WHERE op_id = ? AND transition_key = ?",
            (str(op_id), transition_key),
        ).fetchone()
        if duplicate:
            return _attempt_from_row(_get_attempt_row(conn, op_id))
        row = _get_attempt_row(conn, op_id)
        current = TransportState(str(row["transport"]))
        version = int(row["version"] or 0)
        if expected_version is not None and version != int(expected_version):
            raise AttemptConflict(f"attempt version conflict: expected={expected_version} actual={version}")
        if target not in TRANSPORT_TRANSITIONS[current]:
            raise AttemptConflict(f"illegal transport transition: {current.value}->{target.value}")

        allowed_updates = {
            "block_code",
            "block_reason",
            "definitely_unsent",
            "root_msg_id",
            "result_msg_id",
            "sent_at",
            "transport_due_at",
            "last_error",
            "resend_count",
        }
        assignments = ["transport = ?", "updated_at = ?", "last_transition_key = ?", "version = version + 1"]
        values = [target.value, now, transition_key]
        for key, value in updates.items():
            if key not in allowed_updates:
                raise ValueError(f"unsupported transport projection field: {key}")
            assignments.append(f"{key} = ?")
            values.append(1 if key == "definitely_unsent" and value else value)
        values.extend([str(op_id), version])
        cursor = conn.execute(
            f"UPDATE command_attempts SET {', '.join(assignments)} WHERE op_id = ? AND version = ?",
            values,
        )
        if cursor.rowcount != 1:
            raise AttemptConflict("attempt transport compare-and-swap failed")
        _append_transition(
            conn,
            op_id=str(op_id),
            axis="transport",
            from_state=current.value,
            to_state=target.value,
            code=str(code or ""),
            summary=str(summary or ""),
            transition_key=transition_key,
            ts=now,
        )
        return _attempt_from_row(_get_attempt_row(conn, op_id))


def transition_business(
    op_id,
    target,
    *,
    transition_key,
    expected_version=None,
    code="",
    summary="",
    now=None,
    business_due_at=None,
):
    target = BusinessState(target)
    now = float(now or time.time())
    transition_key = str(transition_key or "").strip()
    if not transition_key:
        raise ValueError("transition_key is required")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT 1 FROM command_attempt_transitions WHERE op_id = ? AND transition_key = ?",
            (str(op_id), transition_key),
        ).fetchone()
        if duplicate:
            return _attempt_from_row(_get_attempt_row(conn, op_id))
        row = _get_attempt_row(conn, op_id)
        current = BusinessState(str(row["business"]))
        version = int(row["version"] or 0)
        if expected_version is not None and version != int(expected_version):
            raise AttemptConflict(f"attempt version conflict: expected={expected_version} actual={version}")
        if target not in BUSINESS_TRANSITIONS[current]:
            raise AttemptConflict(f"illegal business transition: {current.value}->{target.value}")
        closed_at = now if target.value in _TERMINAL_BUSINESS else 0.0
        due_at = float(row["business_due_at"] or 0) if business_due_at is None else float(business_due_at or 0)
        cursor = conn.execute(
            """
            UPDATE command_attempts
            SET business = ?, business_code = ?, business_summary = ?, business_due_at = ?,
                closed_at = ?, updated_at = ?, last_transition_key = ?, version = version + 1
            WHERE op_id = ? AND version = ?
            """,
            (
                target.value,
                str(code or ""),
                str(summary or ""),
                due_at,
                closed_at,
                now,
                transition_key,
                str(op_id),
                version,
            ),
        )
        if cursor.rowcount != 1:
            raise AttemptConflict("attempt business compare-and-swap failed")
        _append_transition(
            conn,
            op_id=str(op_id),
            axis="business",
            from_state=current.value,
            to_state=target.value,
            code=str(code or ""),
            summary=str(summary or ""),
            transition_key=transition_key,
            ts=now,
        )
        return _attempt_from_row(_get_attempt_row(conn, op_id))


def append_evidence(
    op_id,
    *,
    kind,
    idempotency_key,
    msg_id=0,
    edit_seq=0,
    family="",
    text_digest="",
    source="",
    payload=None,
    result_msg_id=0,
    expected_version=None,
    now=None,
):
    kind = EvidenceKind(kind)
    now = float(now or time.time())
    idempotency_key = str(idempotency_key or "").strip()
    if not idempotency_key:
        raise ValueError("idempotency_key is required")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT * FROM command_attempt_evidence WHERE op_id = ? AND idempotency_key = ?",
            (str(op_id), idempotency_key),
        ).fetchone()
        if duplicate:
            return _evidence_from_row(duplicate)
        row = _get_attempt_row(conn, op_id)
        version = int(row["version"] or 0)
        if expected_version is not None and version != int(expected_version):
            raise AttemptConflict(f"attempt version conflict: expected={expected_version} actual={version}")
        seq = _next_seq(conn, "command_attempt_evidence", op_id)
        conn.execute(
            """
            INSERT INTO command_attempt_evidence(
                op_id, seq, kind, msg_id, edit_seq, family, text_digest,
                source, idempotency_key, ts, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(op_id),
                seq,
                kind.value,
                int(msg_id or 0),
                int(edit_seq or 0),
                str(family or ""),
                str(text_digest or ""),
                str(source or ""),
                idempotency_key,
                now,
                _json_dump(payload or {}),
            ),
        )
        projected_result_msg_id = int(result_msg_id or msg_id or 0)
        cursor = conn.execute(
            """
            UPDATE command_attempts
            SET result_msg_id = CASE WHEN ? > 0 THEN ? ELSE result_msg_id END,
                updated_at = ?, version = version + 1
            WHERE op_id = ? AND version = ?
            """,
            (projected_result_msg_id, projected_result_msg_id, now, str(op_id), version),
        )
        if cursor.rowcount != 1:
            raise AttemptConflict("attempt evidence compare-and-swap failed")
        evidence_row = conn.execute(
            "SELECT * FROM command_attempt_evidence WHERE op_id = ? AND idempotency_key = ?",
            (str(op_id), idempotency_key),
        ).fetchone()
        return _evidence_from_row(evidence_row)


def list_transitions(op_id):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM command_attempt_transitions WHERE op_id = ? ORDER BY seq",
            (str(op_id),),
        ).fetchall()
        return [_transition_from_row(row) for row in rows]


def list_evidence(op_id):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM command_attempt_evidence WHERE op_id = ? ORDER BY seq",
            (str(op_id),),
        ).fetchall()
        return [_evidence_from_row(row) for row in rows]


def list_open_attempts(*, send_as_id=None, limit=100):
    clauses = ["business NOT IN (?, ?, ?)"]
    params = list(_TERMINAL_BUSINESS)
    if send_as_id is not None:
        clauses.append("send_as_id = ?")
        params.append(int(send_as_id))
    params.append(max(1, min(1000, int(limit or 100))))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM command_attempts WHERE {' AND '.join(clauses)} ORDER BY updated_at LIMIT ?",
            params,
        ).fetchall()
        return [_attempt_from_row(row) for row in rows]


def list_due_attempts(now, *, limit=100):
    now = float(now)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM command_attempts
            WHERE business NOT IN (?, ?, ?)
              AND ((transport_due_at > 0 AND transport_due_at <= ?)
                OR (business_due_at > 0 AND business_due_at <= ?))
            ORDER BY CASE
                WHEN transport_due_at > 0 AND business_due_at > 0 THEN MIN(transport_due_at, business_due_at)
                WHEN transport_due_at > 0 THEN transport_due_at
                ELSE business_due_at
            END
            LIMIT ?
            """,
            (
                *_TERMINAL_BUSINESS,
                now,
                now,
                max(1, min(1000, int(limit or 100))),
            ),
        ).fetchall()
        return [_attempt_from_row(row) for row in rows]


def prune_terminal_attempts(before_ts, *, limit=1000):
    before_ts = float(before_ts)
    limit = max(1, min(10000, int(limit or 1000)))
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT op_id FROM command_attempts
            WHERE business IN (?, ?, ?) AND closed_at > 0 AND closed_at < ?
            ORDER BY closed_at LIMIT ?
            """,
            (*_TERMINAL_BUSINESS, before_ts, limit),
        ).fetchall()
        op_ids = [str(row["op_id"]) for row in rows]
        if not op_ids:
            return 0
        placeholders = ",".join("?" for _ in op_ids)
        conn.execute(f"DELETE FROM command_attempt_evidence WHERE op_id IN ({placeholders})", op_ids)
        conn.execute(f"DELETE FROM command_attempt_transitions WHERE op_id IN ({placeholders})", op_ids)
        conn.execute(f"DELETE FROM command_attempts WHERE op_id IN ({placeholders})", op_ids)
        return len(op_ids)


def default_evidence_idempotency_key(*, kind, msg_id=0, edit_seq=0, source="", text_digest="", payload=None):
    raw = _json_dump(
        {
            "kind": str(kind),
            "msg_id": int(msg_id or 0),
            "edit_seq": int(edit_seq or 0),
            "source": str(source or ""),
            "text_digest": str(text_digest or ""),
            "payload": payload or {},
        }
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "append_evidence",
    "create_attempt",
    "default_evidence_idempotency_key",
    "get_attempt",
    "list_due_attempts",
    "list_evidence",
    "list_open_attempts",
    "list_transitions",
    "prune_terminal_attempts",
    "transition_business",
    "transition_transport",
]
