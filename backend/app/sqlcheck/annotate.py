"""SQL 详细备注（feature.sql_comments，评审项-用户反馈 2026-10-17）。

在最终 SQL 前部注入注释块：表中文名 + 输出字段中文名（来自已摄取元数据
comment_custom ?? comment_db，提取中文段）。注释是合法 SQL（-- 行注释），
不影响 sqlglot 静态校验与引擎执行；解析失败/无注释时原样返回。
"""

from __future__ import annotations

import re
import sqlite3

from sqlglot import exp, parse_one


def _cn(comment: str | None) -> str:
    """注释里提取中文段：去 ASCII 词/空白/边缘标点；超长截断。"""
    if not comment:
        return ""
    s = re.sub(r"[A-Za-z0-9_]+", "", comment)
    s = re.sub(r"\s+", "", s).strip(" -—·:：/|")
    return s[:24] + ("…" if len(s) > 24 else "")


def annotate_sql(conn: sqlite3.Connection, datasource_id: int, sql: str) -> str:
    """在 sql 前注入 '-- 表：…' / '-- 字段：…' 注释块；取不到注释时原样返回。"""
    if not sql or not sql.strip():
        return sql
    try:
        tree = parse_one(sql)
    except Exception:
        return sql

    conn.row_factory = sqlite3.Row
    tmap = {
        r["name"].lower(): _cn(r["comment_custom"] or r["comment_db"])
        for r in conn.execute(
            "SELECT name, comment_db, comment_custom FROM table_meta WHERE datasource_id=?", (datasource_id,)
        ).fetchall()
    }
    cmap = {
        (r["tname"].lower(), r["cname"].lower()): _cn(r["comment_custom"] or r["comment_db"])
        for r in conn.execute(
            "SELECT m.name AS tname, c.name AS cname, c.comment_db, c.comment_custom "
            "FROM column_meta c JOIN table_meta m ON c.table_id = m.id "
            "WHERE m.datasource_id=?",
            (datasource_id,),
        ).fetchall()
    }

    # FROM/JOIN 表（去重保序）
    alias: dict[str, str] = {}
    tables: list[str] = []
    seen_t: set[str] = set()
    for t in tree.find_all(exp.Table):
        name = t.name
        low = name.lower()
        if t.alias:
            alias[t.alias.lower()] = low
        if low not in seen_t:
            seen_t.add(low)
            tables.append(name)

    # 顶层 SELECT 的裸列投影 → 归属表解析（多表裸列保守跳过）
    col_parts: list[str] = []
    root_select = tree.find(exp.Select)
    if root_select:
        seen_c: set[tuple[str, str]] = set()
        for e in root_select.expressions:
            node = e.this if isinstance(e, exp.Alias) else e
            if not isinstance(node, exp.Column):
                continue
            ref: str | None = None
            if node.table:
                ref = alias.get(node.table.lower(), node.table.lower())
            elif len(tables) == 1:
                ref = tables[0].lower()
            if not ref or (ref, node.name.lower()) in seen_c:
                continue
            seen_c.add((ref, node.name.lower()))
            cn = cmap.get((ref, node.name.lower()))
            if cn:
                col_parts.append(f"{ref}.{node.name} {cn}")

    lines: list[str] = []
    table_parts = [f"{t} {tmap[t.lower()]}" for t in tables if tmap.get(t.lower())]
    if table_parts:
        lines.append("-- 表：" + "；".join(table_parts))
    if col_parts:
        lines.append("-- 字段：" + "；".join(col_parts))
    if not lines:
        return sql
    return "\n".join(lines) + "\n" + sql
