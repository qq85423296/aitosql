"""Write-once execution fence, not a distributed exactly-once transaction."""

from __future__ import annotations

import hashlib
import sqlite3

from app.errors import E_CONFLICT, BizError


def sql_hash(sql: str) -> str:
    """Bind approval to the exact UTF-8 SQL bytes, without normalization."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def approve(conn: sqlite3.Connection, query_id: str, sql: str) -> None:
    digest = sql_hash(sql)
    conn.execute(
        "INSERT INTO execution_attempt(query_id,sql_hash,state,approved) VALUES(?,?,'prepared',1)"
        " ON CONFLICT(query_id,sql_hash) DO UPDATE SET approved=1"
        " WHERE execution_attempt.state='prepared'",
        (query_id, digest),
    )


def start(conn: sqlite3.Connection, query_id: str, sql: str, *, require_approval: bool) -> None:
    """Commit this reservation before talking to the external database."""
    conn.execute("BEGIN IMMEDIATE")
    status = conn.execute("SELECT status FROM query WHERE id=?", (query_id,)).fetchone()
    if status is None or status[0] != "running":
        raise BizError(E_CONFLICT, "查询已停止或未处于执行状态", 409)
    digest = sql_hash(sql)
    conn.execute(
        "INSERT OR IGNORE INTO execution_attempt(query_id,sql_hash,state,approved) VALUES(?,?,'prepared',0)",
        (query_id, digest),
    )
    cur = conn.execute(
        "UPDATE execution_attempt SET state='started',updated_at=datetime('now')"
        " WHERE query_id=? AND sql_hash=? AND state='prepared' AND (approved=1 OR ?=0)",
        (query_id, digest, int(require_approval)),
    )
    if cur.rowcount != 1:
        raise BizError(E_CONFLICT, "写操作未获得本语句确认，或已有执行记录；请核对数据库结果", 409)


def finish(conn: sqlite3.Connection, query_id: str, sql: str, *, succeeded: bool) -> None:
    conn.execute(
        "UPDATE execution_attempt SET state=?,updated_at=datetime('now')"
        " WHERE query_id=? AND sql_hash=? AND state='started'",
        ("succeeded" if succeeded else "outcome_unknown", query_id, sql_hash(sql)),
    )


def has_started(conn: sqlite3.Connection, query_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM execution_attempt WHERE query_id=? AND state!='prepared' LIMIT 1", (query_id,)
        ).fetchone()
        is not None
    )
