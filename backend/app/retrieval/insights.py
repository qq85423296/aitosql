"""查询日志挖掘（演进 #3）：从历史查询行为发现「高使用、低注释」的元数据缺口。

证据源（全部本地、确定性）：
- retrieval_feedback：执行成功累计（演进 #1 回写）；
- query 表 question / sql_final 的表名提及扫描（词边界正则）。

注释质量是召回质量的上限：被反复查询却缺注释的表，FTS/向量都拿不到语义信号。
输出按 使用次数 DESC, 注释覆盖率 ASC, 表名 ASC 排序，供设置页「补注释」提示。
本模块只读不写。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.store import datasources as store_datasources


def _usage_counts(conn: sqlite3.Connection, ds_id: int, table_names: list[str]) -> dict[str, int]:
    usage: dict[str, int] = {}
    try:
        rows = conn.execute(
            "SELECT table_name, success_count FROM retrieval_feedback WHERE datasource_id = ?",
            (ds_id,),
        ).fetchall()
        for name, count in rows:
            usage[str(name)] = usage.get(str(name), 0) + int(count)
    except sqlite3.Error:
        pass  # 旧库未迁移 0010：反馈证据缺失不阻塞提及扫描
    history = conn.execute(
        "SELECT question, sql_final FROM query WHERE datasource_id = ? AND status = 'done'",
        (ds_id,),
    ).fetchall()
    for question, sql_final in history:
        haystack = f"{question or ''}\n{sql_final or ''}"
        if not haystack.strip():
            continue
        for name in table_names:
            if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", haystack):
                usage[name] = usage.get(name, 0) + 1
    return usage


def comment_gaps(
    conn: sqlite3.Connection, ds_id: int, *, min_uses: int = 1, limit: int = 20
) -> list[dict[str, Any]]:
    """高使用低注释表清单（含缺口明细与建议文案）。"""
    tables = store_datasources.query_tables(conn, ds_id)
    if not tables:
        return []
    usage = _usage_counts(conn, ds_id, [t.name for t in tables])
    out: list[dict[str, Any]] = []
    for t in tables:
        uses = usage.get(t.name, 0)
        if uses < min_uses:
            continue
        cols = store_datasources.query_columns(conn, t.id)
        total = len(cols)
        commented = sum(1 for c in cols if (c.comment_custom or c.comment_db))
        table_commented = bool(t.comment_custom or t.comment_db)
        coverage = (commented + int(table_commented)) / (total + 1)
        missing: list[str] = []
        if not table_commented:
            missing.append("table_comment")
        uncommented = total - commented
        if uncommented:
            missing.append(f"columns:{uncommented}/{total}")
        out.append(
            {
                "table_name": t.name,
                "uses": uses,
                "comment_coverage": round(coverage, 3),
                "table_comment": table_commented,
                "columns_commented": commented,
                "columns_total": total,
                "missing": missing,
                "suggestion": (
                    f"表 {t.name} 被成功查询 {uses} 次但注释覆盖率 {coverage:.0%}，"
                    "补充表/字段注释可直接提升检索命中率"
                ),
            }
        )
    out.sort(key=lambda d: (-int(d["uses"]), float(d["comment_coverage"]), str(d["table_name"])))
    return out[: max(1, limit)]
