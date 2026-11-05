"""会话与澄清偏好 DAO（session / clarify_pref）。

- 列表排序：archived → pinned DESC → updated_at DESC（idx_session_list，C1.1/FR-Q1）；
- 标题策略（C6.7）：首问完成自动生成 = 问题去空白前 18 字；
  实现口径 = 仅当标题仍为占位默认值「新会话」时才自动改写，PATCH 重命名后即脱离占位态，
  不再被自动改写（DDL 无 title_locked 列，SSOT 冻结，编码期定型口径，回写项）；
- clarify_pref：UNIQUE(datasource_id, term)，命中免问（BR-4）。
"""

from __future__ import annotations

import sqlite3

from app.errors import E_NOT_FOUND, BizError
from app.ids import new_session_id
from app.store.models import ClarifyPref, Session

DEFAULT_TITLE = "新会话"
AUTO_TITLE_LEN = 18  # 问题去空白后前 18 字


def query_sessions(conn: sqlite3.Connection) -> list[Session]:
    rows = conn.execute(
        "SELECT * FROM session ORDER BY archived, pinned DESC, updated_at DESC, created_at DESC"
    ).fetchall()
    return [Session.from_row(r) for r in rows]


def get_session(conn: sqlite3.Connection, session_id: str) -> Session:
    row = conn.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"会话不存在: {session_id}", 404)
    return Session.from_row(row)


def save_session(conn: sqlite3.Connection, *, datasource_id: int | None, title: str | None = None) -> Session:
    sid = new_session_id()
    conn.execute(
        "INSERT INTO session(id, title, datasource_id, updated_at) VALUES (?, ?, ?, datetime('now'))",
        (sid, title or DEFAULT_TITLE, datasource_id),
    )
    return get_session(conn, sid)


def update_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    title: str | None = None,
    pinned: int | None = None,
    archived: int | None = None,
) -> Session:
    get_session(conn, session_id)
    sets: list[str] = []
    params: list[object] = []
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if pinned is not None:
        sets.append("pinned = ?")
        params.append(int(pinned))
    if archived is not None:
        sets.append("archived = ?")
        params.append(int(archived))
    if sets:
        sets.append("updated_at = datetime('now')")
        params.append(session_id)
        conn.execute(f"UPDATE session SET {', '.join(sets)} WHERE id = ?", params)
    return get_session(conn, session_id)


def delete_session(conn: sqlite3.Connection, session_id: str) -> list[str]:
    """删除会话（前端二次确认 FR-Q1）：级联删除 query / query_event / result_preview
    （与删除确认文案「含消息与事件溯源记录」语义一致）。

    修复（实测 2026-10-17）：旧实现只删 session 行，query.session_id 外键
    NO ACTION 直接 500。进行中的查询拒绝删除（E4090），防止 runner 半途
    写事件撞外键。返回被删 query_id 列表（供 checkpoints 孤儿清理，审计 P1）。
    """
    from app.errors import E_CONFLICT, BizError

    get_session(conn, session_id)  # 404 闸门
    running = conn.execute(
        "SELECT COUNT(*) FROM query WHERE session_id = ? "
        "AND status IN ('running','waiting_clarify','waiting_confirm')",
        (session_id,),
    ).fetchone()[0]
    if running:
        raise BizError(E_CONFLICT, f"会话内还有 {running} 条进行中的查询，请先停止再删除", 409)
    qids = [r[0] for r in conn.execute("SELECT id FROM query WHERE session_id = ?", (session_id,))]
    if qids:
        marks = ",".join("?" * len(qids))
        conn.execute(f"DELETE FROM result_preview WHERE query_id IN ({marks})", qids)
        conn.execute(f"DELETE FROM query_event WHERE query_id IN ({marks})", qids)
        conn.execute(f"DELETE FROM query WHERE id IN ({marks})", qids)
    conn.execute("DELETE FROM session WHERE id = ?", (session_id,))
    return qids


def maybe_auto_title(conn: sqlite3.Connection, session_id: str, question: str) -> None:
    """首问完成自动标题：仅占位态改写（重命名后不再改写，C6.7）。"""
    row = conn.execute("SELECT title FROM session WHERE id = ?", (session_id,)).fetchone()
    if row is None or row["title"] != DEFAULT_TITLE:
        return
    auto = "".join(question.split())[:AUTO_TITLE_LEN] or DEFAULT_TITLE
    conn.execute(
        "UPDATE session SET title = ?, updated_at = datetime('now') WHERE id = ?", (auto, session_id)
    )


def touch_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("UPDATE session SET updated_at = datetime('now') WHERE id = ?", (session_id,))


def save_summary(conn: sqlite3.Connection, session_id: str, *, summary: str, summary_upto: str) -> None:
    """滚动摘要写入（有损链；summary_upto = 覆盖到的最后 query_id）。"""
    conn.execute(
        "UPDATE session SET summary = ?, summary_upto = ? WHERE id = ?", (summary, summary_upto, session_id)
    )


# ---------- clarify_pref ----------


def get_clarify_pref(conn: sqlite3.Connection, ds_id: int, term: str) -> ClarifyPref | None:
    row = conn.execute(
        "SELECT * FROM clarify_pref WHERE datasource_id = ? AND term = ?", (ds_id, term)
    ).fetchone()
    return ClarifyPref.from_row(row) if row else None


def save_clarify_pref(conn: sqlite3.Connection, *, datasource_id: int, term: str, chosen_rule: str) -> None:
    """澄清裁决沉淀：同数据源同 term 下次免问（UPSERT 幂等）。"""
    conn.execute(
        "INSERT INTO clarify_pref(datasource_id, term, chosen_rule) VALUES (?, ?, ?)"
        " ON CONFLICT(datasource_id, term) DO UPDATE SET chosen_rule = excluded.chosen_rule",
        (datasource_id, term, chosen_rule),
    )


def query_clarify_prefs(conn: sqlite3.Connection, ds_id: int) -> list[ClarifyPref]:
    rows = conn.execute(
        "SELECT * FROM clarify_pref WHERE datasource_id = ? ORDER BY term", (ds_id,)
    ).fetchall()
    return [ClarifyPref.from_row(r) for r in rows]


def clarify_pref_rev(conn: sqlite3.Connection, ds_id: int) -> str:
    """clarify_pref 语义版本（semantic_rev 五因子之一，懒惰失效）。"""
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM clarify_pref WHERE datasource_id = ?", (ds_id,)
    ).fetchone()
    return f"{row[0]}:{row[1]}"
