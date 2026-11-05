"""语义层四表 DAO（field_standard / dimension_standard / dimension_value / metric_standard）。

D-19 纪律：metric_standard.filter 为保留列名——全部 SQL 显式双引号 `"filter"` + 参数化；
代码评审 checklist 项「metric_standard.filter 必须引号」由本文件承载。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.errors import E_NOT_FOUND, BizError
from app.identifiers import require_id
from app.store.models import DimensionStandard, DimensionValue, FieldStandard, MetricStandard

# ---------- field_standard ----------


def query_standards(conn: sqlite3.Connection) -> list[FieldStandard]:
    rows = conn.execute("SELECT * FROM field_standard ORDER BY name").fetchall()
    return [FieldStandard.from_row(r) for r in rows]


def save_standard(
    conn: sqlite3.Connection,
    *,
    name: str,
    data_type: str | None,
    unit: str | None,
    value_desc: str | None,
    dimension_standard_id: int | None,
    description: str | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO field_standard(name, data_type, unit, value_desc,"
        " dimension_standard_id, description) VALUES (?, ?, ?, ?, ?, ?)",
        (name, data_type, unit, value_desc, dimension_standard_id, description),
    )
    return require_id(cur.lastrowid)


def update_standard(conn: sqlite3.Connection, std_id: int, **fields: Any) -> None:
    _update_by_id(
        conn,
        "field_standard",
        std_id,
        {"name", "data_type", "unit", "value_desc", "dimension_standard_id", "description"},
        fields,
    )


def delete_standard(conn: sqlite3.Connection, std_id: int) -> None:
    cur = conn.execute("DELETE FROM field_standard WHERE id = ?", (std_id,))
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"字段标准不存在: {std_id}", 404)


# ---------- dimension_standard / dimension_value ----------


def query_dimensions(conn: sqlite3.Connection) -> list[DimensionStandard]:
    rows = conn.execute("SELECT * FROM dimension_standard ORDER BY name").fetchall()
    return [DimensionStandard.from_row(r) for r in rows]


def save_dimension(conn: sqlite3.Connection, *, name: str, description: str | None) -> int:
    cur = conn.execute("INSERT INTO dimension_standard(name, description) VALUES (?, ?)", (name, description))
    return require_id(cur.lastrowid)


def update_dimension(conn: sqlite3.Connection, dim_id: int, **fields: Any) -> None:
    _update_by_id(conn, "dimension_standard", dim_id, {"name", "description"}, fields)


def delete_dimension(conn: sqlite3.Connection, dim_id: int) -> None:
    conn.execute("DELETE FROM dimension_value WHERE dimension_id = ?", (dim_id,))
    cur = conn.execute("DELETE FROM dimension_standard WHERE id = ?", (dim_id,))
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"维度不存在: {dim_id}", 404)


def query_dimension_values(conn: sqlite3.Connection, dimension_id: int) -> list[DimensionValue]:
    rows = conn.execute(
        "SELECT * FROM dimension_value WHERE dimension_id = ? ORDER BY sort, id", (dimension_id,)
    ).fetchall()
    return [DimensionValue.from_row(r) for r in rows]


def save_dimension_value(
    conn: sqlite3.Connection, *, dimension_id: int, value: str, label: str, sort: int = 0
) -> int:
    cur = conn.execute(
        "INSERT INTO dimension_value(dimension_id, value, label, sort) VALUES (?, ?, ?, ?)",
        (dimension_id, value, label, sort),
    )
    return require_id(cur.lastrowid)


def delete_dimension_value(conn: sqlite3.Connection, value_id: int) -> None:
    conn.execute("DELETE FROM dimension_value WHERE id = ?", (value_id,))


# ---------- metric_standard（filter 引号纪律） ----------


def query_metrics(conn: sqlite3.Connection) -> list[MetricStandard]:
    rows = conn.execute(
        'SELECT id, name, expr, "filter", base_tables, description, created_at'
        " FROM metric_standard ORDER BY name"
    ).fetchall()
    return [MetricStandard.from_row(r) for r in rows]


def save_metric(
    conn: sqlite3.Connection,
    *,
    name: str,
    expr: str,
    filter_: str | None,
    base_tables: str | None,
    description: str | None,
) -> int:
    cur = conn.execute(
        'INSERT INTO metric_standard(name, expr, "filter", base_tables, description) VALUES (?, ?, ?, ?, ?)',
        (name, expr, filter_, base_tables, description),
    )
    return require_id(cur.lastrowid)


def get_metric(conn: sqlite3.Connection, metric_id: int) -> MetricStandard:
    """按 id 取指标（Q-11：POST 响应回传完整行）。"""
    row = conn.execute(
        'SELECT id, name, expr, "filter", base_tables, description, created_at'
        " FROM metric_standard WHERE id = ?",
        (metric_id,),
    ).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"指标不存在: {metric_id}", 404)
    return MetricStandard.from_row(row)


def update_metric(
    conn: sqlite3.Connection,
    metric_id: int,
    *,
    name: str,
    expr: str,
    filter_: str | None,
    base_tables: str | None,
    description: str | None,
) -> None:
    cur = conn.execute(
        'UPDATE metric_standard SET name = ?, expr = ?, "filter" = ?, base_tables = ?,'
        " description = ? WHERE id = ?",
        (name, expr, filter_, base_tables, description, metric_id),
    )
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"指标不存在: {metric_id}", 404)


def delete_metric(conn: sqlite3.Connection, metric_id: int) -> None:
    cur = conn.execute("DELETE FROM metric_standard WHERE id = ?", (metric_id,))
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"指标不存在: {metric_id}", 404)


def query_metrics_for_inject(conn: sqlite3.Connection, hit_names: list[str]) -> list[MetricStandard]:
    """指标命中注入（§8.3 第 7 段）：按命中名单取口径（复合指标展开一次不递归）。"""
    if not hit_names:
        return []
    marks = ",".join("?" * len(hit_names))
    rows = conn.execute(
        'SELECT id, name, expr, "filter", base_tables, description, created_at'
        f" FROM metric_standard WHERE name IN ({marks})",
        hit_names,
    ).fetchall()
    return [MetricStandard.from_row(r) for r in rows]


# ---------- 内部 ----------


def _update_by_id(
    conn: sqlite3.Connection, table: str, row_id: int, allowed: set[str], fields: dict[str, Any]
) -> None:
    sets: list[str] = []
    params: list[Any] = []
    for key, val in fields.items():
        if key in allowed and val is not None:
            sets.append(f"{key} = ?")
            params.append(val)
    if not sets:
        return
    params.append(row_id)
    cur = conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", params)
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"{table} 记录不存在: {row_id}", 404)
