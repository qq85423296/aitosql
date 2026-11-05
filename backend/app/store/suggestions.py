"""AI 建议队列 DAO + 采纳映射（C6.8 / SSOT §12.2，BR-14）。

四类 payload：comment / std_bind / relation / metric；
采纳映射：comment→comment_custom；std_bind→field_standard_id；
          relation→relation(confirmed)；metric→metric_standard。
P5：采纳前不进提示词（本模块只在 accept 时写目标表）。
"""

from __future__ import annotations

import json
import sqlite3

from app.errors import E_BAD_REQUEST, E_NOT_FOUND, BizError
from app.identifiers import require_id
from app.store import datasources as store_datasources
from app.store import semantics as store_semantics
from app.store.models import AiSuggestion

VALID_KINDS = ("comment", "std_bind", "relation", "metric")


def save_suggestion(
    conn: sqlite3.Connection,
    *,
    datasource_id: int,
    kind: str,
    payload: dict,
    confidence: float | None = None,
    run_id: int | None = None,
) -> int:
    if kind not in VALID_KINDS:
        raise BizError(E_BAD_REQUEST, f"非法建议类型: {kind}", 422)
    cur = conn.execute(
        "INSERT INTO ai_suggestion(datasource_id, kind, payload_json, confidence, run_id)"
        " VALUES (?, ?, ?, ?, ?)",
        (datasource_id, kind, json.dumps(payload, ensure_ascii=False), confidence, run_id),
    )
    return require_id(cur.lastrowid)


def query_suggestions(
    conn: sqlite3.Connection,
    datasource_id: int | None = None,
    status: str | None = "pending",
    run_id: int | None = None,
) -> list[AiSuggestion]:
    sql = "SELECT * FROM ai_suggestion"
    conds: list[str] = []
    params: list[object] = []
    if datasource_id is not None:
        conds.append("datasource_id = ?")
        params.append(datasource_id)
    if status is not None:
        conds.append("status = ?")
        params.append(status)
    if run_id is not None:
        conds.append("run_id = ?")
        params.append(run_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    rows = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    return [AiSuggestion.from_row(r) for r in rows]


def get_suggestion(conn: sqlite3.Connection, suggestion_id: int) -> AiSuggestion:
    row = conn.execute("SELECT * FROM ai_suggestion WHERE id = ?", (suggestion_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"建议不存在: {suggestion_id}", 404)
    return AiSuggestion.from_row(row)


def _apply_accept(conn: sqlite3.Connection, sug: AiSuggestion, edits: dict[int, str] | None = None) -> None:
    """单条采纳映射（C6.8 四路 + C6.8+ 表级注释）。

    edits：编辑后采纳——comment 类以编辑文本替换 payload.suggested 落库（方案 v1.1 §7）。
    """
    p = sug.payload
    if sug.kind == "comment":
        table = store_datasources.get_table_by_name(conn, sug.datasource_id, p["table"])
        if table is None:
            raise BizError(E_NOT_FOUND, f"表不存在: {p['table']}", 404)
        text = (edits or {}).get(sug.id, p["suggested"])
        if not p.get("column"):  # C6.8+：表级注释（column=""）→ tables.comment_custom
            conn.execute(
                "UPDATE table_meta SET comment_custom = ?, updated_at = datetime('now') WHERE id = ?",
                (text, table.id),
            )
            return
        row = conn.execute(
            "SELECT id FROM column_meta WHERE table_id = ? AND name = ?", (table.id, p["column"])
        ).fetchone()
        if row is None:
            raise BizError(E_NOT_FOUND, f"字段不存在: {p['table']}.{p['column']}", 404)
        store_datasources.update_column_meta(conn, row["id"], comment_custom=text)
    elif sug.kind == "std_bind":
        table = store_datasources.get_table_by_name(conn, sug.datasource_id, p["table"])
        std = conn.execute("SELECT id FROM field_standard WHERE name = ?", (p["standard"],)).fetchone()
        if table is None or std is None:
            raise BizError(E_NOT_FOUND, "std_bind 目标缺失（表或标准）", 404)
        conn.execute(
            "UPDATE column_meta SET field_standard_id = ?, updated_at = datetime('now')"
            " WHERE table_id = ? AND name = ?",
            (std["id"], table.id, p["column"]),
        )
    elif sug.kind == "relation":
        from_t, from_c = str(p["from"]).split(".", 1)
        to_t, to_c = str(p["to"]).split(".", 1)
        cur = conn.execute(
            "INSERT INTO relation(datasource_id, from_table, from_col, to_table, to_col,"
            " source, confidence, status) VALUES (?, ?, ?, ?, ?, 'inferred', ?, 'confirmed')",
            (sug.datasource_id, from_t, from_c, to_t, to_c, sug.confidence or 1.0),
        )
        _ = cur.lastrowid
    elif sug.kind == "metric":
        store_semantics.save_metric(
            conn,
            name=p["name"],
            expr=p["expr"],
            filter_=p.get("filter"),
            base_tables=p.get("base_tables"),
            description=p.get("description"),
        )
    else:  # pragma: no cover - save_suggestion 已拦截
        raise BizError(E_BAD_REQUEST, f"非法建议类型: {sug.kind}", 422)


def accept_suggestions(
    conn: sqlite3.Connection, ids: list[int], edits: dict[int, str] | None = None
) -> dict[str, int]:
    """批量采纳 `{ids}`：逐条映射写目标表，全量成功才提交（调用方事务）。"""
    done = 0
    for sid in ids:
        sug = get_suggestion(conn, sid)
        if sug.status != "pending":
            continue
        _apply_accept(conn, sug, edits)
        conn.execute("UPDATE ai_suggestion SET status = 'accepted' WHERE id = ?", (sid,))
        done += 1
    return {"accepted": done}


def ignore_suggestions(conn: sqlite3.Connection, ids: list[int]) -> dict[str, int]:
    cur = conn.execute(
        "UPDATE ai_suggestion SET status = 'ignored' WHERE status = 'pending'"
        f" AND id IN ({','.join('?' * len(ids))})",
        ids,
    )
    return {"ignored": cur.rowcount}
