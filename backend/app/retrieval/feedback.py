"""生成反馈闭环（演进 #1）：执行成功的查询把真实用到的表回写为检索权重。

数据流：execute 成功 → sqlcheck.rules.tables_in_sql 提取物理表 → record_success 累计
→ supply 召回时 boosts() 给 RRF 分数乘性加权。表名经 sanitize_text 归一；
本模块只碰 retrieval_feedback 一张表，失败永远不影响主执行链路（调用侧兜底）。
"""

from __future__ import annotations

import math
import sqlite3

from app.text import sanitize_text

MAX_TABLE_LEN = 128


def record_success(conn: sqlite3.Connection, ds_id: int, tables: list[str]) -> int:
    """成功执行的物理表逐个 +1（UPSERT）。返回实际落账的表数。"""
    names = []
    for raw in tables:
        name = sanitize_text(raw, MAX_TABLE_LEN)
        if name and name not in names:
            names.append(name)
    for name in names:
        conn.execute(
            "INSERT INTO retrieval_feedback(datasource_id, table_name, success_count, last_success_at)"
            " VALUES(?, ?, 1, datetime('now'))"
            " ON CONFLICT(datasource_id, table_name) DO UPDATE SET"
            " success_count = success_count + 1, last_success_at = datetime('now')",
            (ds_id, name),
        )
    return len(names)


def boosts(conn: sqlite3.Connection, ds_id: int) -> dict[str, float]:
    """表名 → 乘性权重增量（log1p(success_count)；0 = 无加成）。

    调用侧按 score *= (1 + weight * boost) 使用；log 刻度保证高频表优势收敛，
    不会永久压制新召回。表不存在（旧库未迁移/手工 schema）→ 空表，静默降级。
    """
    try:
        rows = conn.execute(
            "SELECT table_name, success_count FROM retrieval_feedback WHERE datasource_id = ?",
            (ds_id,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(r[0]): math.log1p(max(0, int(r[1]))) for r in rows}
