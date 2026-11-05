"""数据源治理 DAO（datasource / table_meta / column_meta / relation / graph_layout）。

合并策略（design 段 6-4 / D-32）：重摄取只刷物理属性（comment_db/row_estimate/type/键标记），
comment_custom / field_standard_id / 已确认 relation 永不覆盖；
字段消失 → 绑定解除 + 样例置 stale（stale_reason 记字段名，由 store/samples 执行）。
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from typing import Any

from app.errors import E_NOT_FOUND, BizError
from app.identifiers import require_id
from app.store.models import ColumnMeta, Datasource, GraphLayout, Relation, TableMeta

# ---------- datasource ----------


def query_datasources(conn: sqlite3.Connection) -> list[Datasource]:
    rows = conn.execute("SELECT * FROM datasource ORDER BY id").fetchall()
    return [Datasource.from_row(r) for r in rows]


def datasource_stats(conn: sqlite3.Connection) -> dict[int, dict[str, int]]:
    """数据源聚合计数（Q-10）：单条 GROUP BY 取 {ds_id: {table_count, field_count}}。

    前端 DsList/DsDetail 卡片展示「表数/字段数」；旧实现逐源 COUNT 为 N+1 隐患。
    """
    stats: dict[int, dict[str, int]] = {}
    for row in conn.execute("SELECT datasource_id, COUNT(*) FROM table_meta GROUP BY datasource_id"):
        stats[int(row[0])] = {"table_count": int(row[1]), "field_count": 0}
    for row in conn.execute(
        "SELECT t.datasource_id, COUNT(*) FROM column_meta cm"
        " JOIN table_meta t ON t.id = cm.table_id GROUP BY t.datasource_id"
    ):
        stats.setdefault(int(row[0]), {"table_count": 0, "field_count": 0})["field_count"] = int(row[1])
    return stats


def get_datasource(conn: sqlite3.Connection, ds_id: int) -> Datasource:
    row = conn.execute("SELECT * FROM datasource WHERE id = ?", (ds_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"数据源不存在: {ds_id}", 404)
    return Datasource.from_row(row)


def get_datasource_row(conn: sqlite3.Connection, ds_id: int) -> sqlite3.Row:
    """原始行（含 password_enc）——仅供 ingest/exec 连接工厂使用，禁止出网。"""
    row = conn.execute("SELECT * FROM datasource WHERE id = ?", (ds_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"数据源不存在: {ds_id}", 404)
    return row


def save_datasource(
    conn: sqlite3.Connection,
    *,
    name: str,
    dialect: str,
    mode: str,
    host: str | None,
    port: int | None,
    db_name: str | None,
    username: str | None,
    password_enc: bytes | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO datasource(name, dialect, mode, host, port, db_name, username, password_enc)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, dialect, mode, host, port, db_name, username, password_enc),
    )
    return require_id(cur.lastrowid)


def update_datasource(
    conn: sqlite3.Connection,
    ds_id: int,
    *,
    name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    db_name: str | None = None,
    username: str | None = None,
    password_enc: bytes | object | None = ...,  # ... = 不触碰密文列
    version: str | None = None,
    capabilities: dict[str, Any] | None = None,
    touch_ingest: bool = False,
) -> None:
    get_datasource(conn, ds_id)
    sets: list[str] = []
    params: list[Any] = []
    for col, val in (
        ("name", name),
        ("host", host),
        ("port", port),
        ("db_name", db_name),
        ("username", username),
        ("version", version),
    ):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if password_enc is not ...:
        sets.append("password_enc = ?")
        params.append(password_enc)
    if capabilities is not None:
        sets.append("capabilities_json = ?")
        params.append(json.dumps(capabilities, ensure_ascii=False))
    if touch_ingest:
        sets.append("last_ingest_at = datetime('now')")
    if not sets:
        return
    params.append(ds_id)
    conn.execute(f"UPDATE datasource SET {', '.join(sets)} WHERE id = ?", params)


def delete_datasource(conn: sqlite3.Connection, ds_id: int) -> None:
    """删除数据源：连带清理元数据/FTS/建议/反馈；会话历史保留并解除数据源绑定。"""
    get_datasource(conn, ds_id)
    table_ids = [
        r[0] for r in conn.execute("SELECT id FROM table_meta WHERE datasource_id = ?", (ds_id,)).fetchall()
    ]
    if table_ids:
        marks = ",".join("?" * len(table_ids))
        conn.execute(f"DELETE FROM column_meta WHERE table_id IN ({marks})", table_ids)
    conn.execute("DELETE FROM table_meta WHERE datasource_id = ?", (ds_id,))
    conn.execute("DELETE FROM relation WHERE datasource_id = ?", (ds_id,))
    conn.execute("DELETE FROM ai_suggestion WHERE datasource_id = ?", (ds_id,))
    conn.execute("DELETE FROM sample WHERE datasource_id = ?", (ds_id,))
    conn.execute("DELETE FROM clarify_pref WHERE datasource_id = ?", (ds_id,))
    conn.execute("DELETE FROM retrieval_feedback WHERE datasource_id = ?", (ds_id,))
    conn.execute("DELETE FROM graph_layout WHERE datasource_id = ?", (ds_id,))
    conn.execute("DELETE FROM schema_fts WHERE datasource_id = ?", (ds_id,))
    # session.datasource_id 为可空 FK：保留查询历史，仅解除绑定（否则 FK 约束阻断删除）
    conn.execute("UPDATE session SET datasource_id = NULL WHERE datasource_id = ?", (ds_id,))
    # 向量表运行期按维度建（可能不存在或扩展未加载）：尽力清理，失败不阻断
    with contextlib.suppress(sqlite3.Error):
        conn.execute("DELETE FROM schema_vec_v2 WHERE datasource_id = ?", (str(ds_id),))
    conn.execute("DELETE FROM datasource WHERE id = ?", (ds_id,))


# ---------- table_meta / column_meta ----------


def query_tables(conn: sqlite3.Connection, ds_id: int) -> list[TableMeta]:
    rows = conn.execute("SELECT * FROM table_meta WHERE datasource_id = ? ORDER BY name", (ds_id,)).fetchall()
    return [TableMeta.from_row(r) for r in rows]


def get_table(conn: sqlite3.Connection, table_id: int) -> TableMeta:
    row = conn.execute("SELECT * FROM table_meta WHERE id = ?", (table_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"表不存在: {table_id}", 404)
    return TableMeta.from_row(row)


def get_table_by_name(conn: sqlite3.Connection, ds_id: int, name: str) -> TableMeta | None:
    row = conn.execute(
        "SELECT * FROM table_meta WHERE datasource_id = ? AND name = ?", (ds_id, name)
    ).fetchone()
    return TableMeta.from_row(row) if row else None


def query_columns(conn: sqlite3.Connection, table_id: int) -> list[ColumnMeta]:
    rows = conn.execute("SELECT * FROM column_meta WHERE table_id = ? ORDER BY id", (table_id,)).fetchall()
    return [ColumnMeta.from_row(r) for r in rows]


def get_column(conn: sqlite3.Connection, column_id: int) -> ColumnMeta:
    row = conn.execute("SELECT * FROM column_meta WHERE id = ?", (column_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"字段不存在: {column_id}", 404)
    return ColumnMeta.from_row(row)


def update_table_meta(
    conn: sqlite3.Connection,
    table_id: int,
    *,
    comment_custom: str | object | None = ...,
    partition_type: str | object | None = ...,
    row_estimate: int | object | None = ...,
) -> None:
    """表编辑（FR-D5/D7）：仅人工字段；`...` 哨兵 = 不触碰，None = 显式清空。"""
    get_table(conn, table_id)
    sets: list[str] = []
    params: list[Any] = []
    for col, val in (
        ("comment_custom", comment_custom),
        ("partition_type", partition_type),
        ("row_estimate", row_estimate),
    ):
        if val is ...:
            continue
        sets.append(f"{col} = ?")
        params.append(val)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    params.append(table_id)
    conn.execute(f"UPDATE table_meta SET {', '.join(sets)} WHERE id = ?", params)


def update_column_meta(
    conn: sqlite3.Connection,
    column_id: int,
    *,
    comment_custom: str | object | None = ...,
    is_pk: int | None = None,
    is_partition: int | None = None,
    is_bucket: int | None = None,
    field_standard_id: int | object | None = ...,
) -> None:
    """字段编辑（FR-D7）：键开关人工修正 + 标准绑定；在线库字段名/类型只读（不入参）。"""
    get_column(conn, column_id)
    sets: list[str] = []
    params: list[Any] = []
    if comment_custom is not ...:
        sets.append("comment_custom = ?")
        params.append(comment_custom)
    for col, val in (("is_pk", is_pk), ("is_partition", is_partition), ("is_bucket", is_bucket)):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(int(val))
    if field_standard_id is not ...:
        sets.append("field_standard_id = ?")
        params.append(field_standard_id)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    params.append(column_id)
    conn.execute(f"UPDATE column_meta SET {', '.join(sets)} WHERE id = ?", params)


def upsert_table_physical(
    conn: sqlite3.Connection,
    ds_id: int,
    *,
    name: str,
    comment_db: str | None,
    row_estimate: int | None,
    field_count: int | None,
    partition_type: str | None,
    schema_name: str | None = None,
) -> tuple[int, bool]:
    """重摄取合并：只刷物理属性；comment_custom 永不覆盖。返回 (table_id, 是否新建)。

    schema_name（0011）：COALESCE 合并——无模式信息的通道（DDL 粘贴）重摄取不抹掉已有值。
    """
    row = conn.execute(
        "SELECT id FROM table_meta WHERE datasource_id = ? AND name = ?", (ds_id, name)
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO table_meta(datasource_id, name, schema_name, comment_db, row_estimate,"
            " field_count, partition_type, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (ds_id, name, schema_name, comment_db, row_estimate, field_count, partition_type),
        )
        return require_id(cur.lastrowid), True
    conn.execute(
        "UPDATE table_meta SET schema_name = COALESCE(?, schema_name), comment_db = ?,"
        " row_estimate = ?, field_count = ?, partition_type = ?, updated_at = datetime('now')"
        " WHERE id = ?",
        (schema_name, comment_db, row_estimate, field_count, partition_type, row[0]),
    )
    return int(row[0]), False


def upsert_column_physical(
    conn: sqlite3.Connection,
    table_id: int,
    *,
    name: str,
    type_: str,
    comment_db: str | None,
    is_pk: int,
    is_partition: int,
    is_bucket: int,
) -> tuple[int, bool]:
    """重摄取合并：物理属性 + 键标记可刷；comment_custom / field_standard_id 永不覆盖。"""
    row = conn.execute(
        "SELECT id FROM column_meta WHERE table_id = ? AND name = ?", (table_id, name)
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO column_meta(table_id, name, type, comment_db, is_pk, is_partition,"
            " is_bucket, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (table_id, name, type_, comment_db, is_pk, is_partition, is_bucket),
        )
        return require_id(cur.lastrowid), True
    conn.execute(
        "UPDATE column_meta SET type = ?, comment_db = ?, is_pk = ?, is_partition = ?,"
        " is_bucket = ?, updated_at = datetime('now') WHERE id = ?",
        (type_, comment_db, is_pk, is_partition, is_bucket, row[0]),
    )
    return int(row[0]), False


def unbind_vanished_columns(conn: sqlite3.Connection, table_id: int, keep_names: set[str]) -> list[str]:
    """字段消失处置：解除标准绑定并返回消失字段名（供样例 stale 治理记 stale_reason）。"""
    rows = conn.execute("SELECT id, name FROM column_meta WHERE table_id = ?", (table_id,)).fetchall()
    vanished = [r for r in rows if r["name"] not in keep_names]
    for r in vanished:
        conn.execute(
            "UPDATE column_meta SET field_standard_id = NULL, updated_at = datetime('now') WHERE id = ?",
            (r["id"],),
        )
    return [str(r["name"]) for r in vanished]


# ---------- relation ----------


def query_relations(conn: sqlite3.Connection, ds_id: int, *, status: str | None = None) -> list[Relation]:
    if status:
        rows = conn.execute(
            "SELECT * FROM relation WHERE datasource_id = ? AND status = ? ORDER BY id", (ds_id, status)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM relation WHERE datasource_id = ? ORDER BY id", (ds_id,)).fetchall()
    return [Relation.from_row(r) for r in rows]


def save_relation(
    conn: sqlite3.Connection,
    ds_id: int,
    *,
    from_table: str,
    from_col: str,
    to_table: str,
    to_col: str,
    source: str,
    confidence: float = 1.0,
) -> int:
    """新建关系：inferred 默认 pending（确认后才进注入，P5/C1.2）。"""
    status = "pending" if source == "inferred" else "confirmed"
    cur = conn.execute(
        "INSERT INTO relation(datasource_id, from_table, from_col, to_table, to_col,"
        " source, confidence, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ds_id, from_table, from_col, to_table, to_col, source, confidence, status),
    )
    return require_id(cur.lastrowid)


def confirm_relation(conn: sqlite3.Connection, relation_id: int) -> None:
    cur = conn.execute(
        "UPDATE relation SET status = 'confirmed' WHERE id = ? AND status = 'pending'", (relation_id,)
    )
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"待确认关系不存在: {relation_id}", 404)


def delete_relation(conn: sqlite3.Connection, relation_id: int) -> None:
    cur = conn.execute("DELETE FROM relation WHERE id = ?", (relation_id,))
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"关系不存在: {relation_id}", 404)


# ---------- ER 图谱与摆位（D-18） ----------


def query_er_graph(conn: sqlite3.Connection, ds_id: int) -> dict[str, Any]:
    """ER 图谱（C1.2/D-18 冻结形状）：nodes=[{table, comment}]，edges=Relation 全字段行。

    形状纪律：与前端 GraphData 类型/mock 逐字一致（节点键 table、边 from_table/from_col…），
    前端不做形状适配；历史误版（name / from,to 字符串）曾导致节点全部堆叠（摆位键 undefined）。
    """
    nodes: list[dict[str, Any]] = [
        {"table": t.name, "comment": t.comment_custom or t.comment_db or ""}
        for t in query_tables(conn, ds_id)
    ]
    edges = [
        {
            "id": r.id,
            "datasource_id": r.datasource_id,
            "from_table": r.from_table,
            "from_col": r.from_col,
            "to_table": r.to_table,
            "to_col": r.to_col,
            "source": r.source,
            "confidence": r.confidence,
            "status": r.status,
        }
        for r in query_relations(conn, ds_id)
    ]
    return {"nodes": nodes, "edges": edges}


def get_graph_layout(conn: sqlite3.Connection, ds_id: int) -> GraphLayout:
    row = conn.execute("SELECT * FROM graph_layout WHERE datasource_id = ?", (ds_id,)).fetchone()
    if row is None:
        # 读取缺失：返回空摆位，前端按表名字典序确定性平铺（D-18）
        return GraphLayout(datasource_id=ds_id, node_positions={})
    return GraphLayout.from_row(row)


def save_graph_layout(conn: sqlite3.Connection, ds_id: int, positions: dict[str, Any]) -> None:
    get_datasource(conn, ds_id)
    conn.execute(
        "INSERT INTO graph_layout(datasource_id, node_positions_json, updated_at)"
        " VALUES (?, ?, datetime('now'))"
        " ON CONFLICT(datasource_id) DO UPDATE SET node_positions_json ="
        " excluded.node_positions_json, updated_at = datetime('now')",
        (ds_id, json.dumps(positions, ensure_ascii=False)),
    )


# ---------- 每数据源口径说明（P5，借鉴 dsh-agent-instructions） ----------


def get_instruction(conn: sqlite3.Connection, ds_id: int) -> str:
    """口径说明文档（术语/join 惯例/批次口径）；缺失返回空串。"""
    row = conn.execute(
        "SELECT content FROM datasource_instruction WHERE datasource_id = ?", (ds_id,)
    ).fetchone()
    return str(row["content"]) if row else ""


def save_instruction(conn: sqlite3.Connection, ds_id: int, content: str) -> None:
    """UPSERT 口径说明文档（整文覆盖，单作者本地场景无并发冲突面）。"""
    get_datasource(conn, ds_id)  # 404 闸门
    conn.execute(
        "INSERT INTO datasource_instruction(datasource_id, content, updated_at)"
        " VALUES (?, ?, datetime('now'))"
        " ON CONFLICT(datasource_id) DO UPDATE SET content = excluded.content,"
        " updated_at = datetime('now')",
        (ds_id, content),
    )


# ---------- 元数据视图（供 sqlcheck 元数据类规则，显式参数 D2） ----------


def build_meta_view(conn: sqlite3.Connection, ds_id: int) -> dict[str, dict[str, Any]]:
    """{table_name: {columns: {col: type}, row_estimate, partition_cols, pk_cols}}。"""
    view: dict[str, dict[str, Any]] = {}
    for t in query_tables(conn, ds_id):
        cols = query_columns(conn, t.id)
        view[t.name] = {
            "columns": {c.name: c.type for c in cols},
            "row_estimate": t.row_estimate or 0,
            "partition_cols": [c.name for c in cols if c.is_partition],
            "pk_cols": [c.name for c in cols if c.is_pk],
        }
    return view
