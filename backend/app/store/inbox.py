"""运行中输入双队列 DAO（query_inbox，P6；借鉴 dsh-agent-loop inbox 语义）。

- kind=steer：运行中引导，图在下一边界领取并入当轮（修改工作问题）；
- kind=followup：排队下轮，当前查询终态后由 runner 自动开新查询；
- kind=inject：补充上下文（不改写问题），下一边界生效；
- 领取/丢弃都是持久状态迁移（pending→claimed/discarded），崩溃恢复后一致。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.identifiers import require_id

VALID_KINDS = ("steer", "followup", "inject")


def add_inbox(
    conn: sqlite3.Connection, *, query_id: str, kind: str, content: str, datasource_id: int | None = None
) -> int:
    if kind not in VALID_KINDS:
        from app.errors import E_BAD_REQUEST, BizError

        raise BizError(E_BAD_REQUEST, f"未知 inbox 类型: {kind}", 422)
    cur = conn.execute(
        "INSERT INTO query_inbox(query_id, kind, content, datasource_id) VALUES (?, ?, ?, ?)",
        (query_id, kind, content, datasource_id),
    )
    # 与 store 层其他 DAO 一致：lastrowid 可能为 None，显式检查而非静默 int(None)
    return require_id(cur.lastrowid)


def pending_inbox(
    conn: sqlite3.Connection, query_id: str, *, kinds: tuple[str, ...] = VALID_KINDS
) -> list[dict[str, Any]]:
    """按提交顺序取待处理输入（FIFO 领取边界）。"""
    marks = ",".join("?" * len(kinds))
    rows = conn.execute(
        f"SELECT id, kind, content, created_at FROM query_inbox"
        f" WHERE query_id = ? AND status = 'pending' AND kind IN ({marks}) ORDER BY id",
        (query_id, *kinds),
    ).fetchall()
    return [dict(r) for r in rows]


def claim_inbox(conn: sqlite3.Connection, ids: list[int]) -> None:
    """领取（持久迁移 pending→claimed）：图在节点边界消费后调用。"""
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE query_inbox SET status = 'claimed', claimed_at = datetime('now')"
        f" WHERE id IN ({marks}) AND status = 'pending'",
        ids,
    )


def discard_inbox(conn: sqlite3.Connection, query_id: str) -> int:
    """持久取消：终态/停止时清空 pending（DSH 语义「普通删除携带取消结果」）。"""
    cur = conn.execute(
        "UPDATE query_inbox SET status = 'discarded' WHERE query_id = ? AND status = 'pending'",
        (query_id,),
    )
    return int(cur.rowcount)


def oldest_pending_followup(conn: sqlite3.Connection, query_id: str) -> dict[str, Any] | None:
    """当前查询终态后排队的最早 followup（runner 自动开新轮的数据源）。"""
    row = conn.execute(
        "SELECT id, content, datasource_id FROM query_inbox"
        " WHERE query_id = ? AND status = 'pending' AND kind = 'followup' ORDER BY id LIMIT 1",
        (query_id,),
    ).fetchone()
    return dict(row) if row else None
