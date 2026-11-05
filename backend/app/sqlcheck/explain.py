"""EXPLAIN 干跑（SSOT §9.3 干跑安全四则 + 三方言矩阵，design D-35）。

四则：1) 禁 ANALYZE（R15 B4 已全面禁用）；2) 干跑串 = AST 去 LIMIT 副本，永不进 execute；
3) 仅 online 且能力卡 explain≠none；4) 失败降级 E3001 标注「未经验证」。

矩阵：MySQL `EXPLAIN FORMAT=JSON`（JSON rows 字段）/ Doris 文本（cardinality regex，
golden 固化，TODO-6 取样校准）/ ClickHouse `EXPLAIN ESTIMATE`（estimate 行数求和）。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from app.sqlcheck import dialects as dialect_mod
from app.sqlcheck.rules import strip_limit

logger = logging.getLogger("aitosql.sqlcheck.explain")

# Doris EXPLAIN 文本 cardinality 正则（B-6：真实样本到位后固化进 golden）
_DORIS_CARDINALITY_RE = re.compile(r"cardinality\s*[=:]\s*([\d,]+)", re.IGNORECASE)

QueryFn = Callable[[str, Any], list[tuple]]


def explain_statement(sql_final: str, *, dialect: str) -> str:
    """构造干跑语句（输入必须已是 validate 终串；本函数内再去 LIMIT 副本）。"""
    no_limit = strip_limit(sql_final, dialect=dialect)
    if dialect == "mysql":
        return f"EXPLAIN FORMAT=JSON {no_limit}"
    if dialect == "postgresql":
        return f"EXPLAIN (FORMAT JSON) {no_limit}"
    if dialect == "clickhouse":
        return f"EXPLAIN ESTIMATE {no_limit}"
    return f"EXPLAIN {no_limit}"  # doris/starrocks 文本


def parse_scan_rows(dialect: str, payload: Any) -> int | None:
    """扫描行数提取（方言矩阵）；失败返回 None（E3001 降级）。"""
    try:
        if dialect == "mysql":
            doc = payload[0][0] if payload and payload[0] else None
            data = json.loads(doc) if isinstance(doc, str) else doc
            return _mysql_rows(data)
        if dialect == "postgresql":
            # EXPLAIN (FORMAT JSON) 返回单格 JSON：[{"Plan": {..., "Plan Rows": N}}]
            doc = payload[0][0] if payload and payload[0] else None
            data = json.loads(doc) if isinstance(doc, str) else doc
            plan = data[0].get("Plan", {}) if isinstance(data, list) and data else {}
            rows = plan.get("Plan Rows")
            return int(rows) if isinstance(rows, (int, float)) else None
        if dialect in ("doris", "starrocks"):
            text = payload[0][0] if payload and payload[0] else ""
            m = _DORIS_CARDINALITY_RE.search(str(text))
            return int(m.group(1).replace(",", "")) if m else None
        if dialect == "clickhouse":
            # EXPLAIN ESTIMATE 返回行 (database, table, parts, rows, marks)
            total = 0
            for row in payload or []:
                if len(row) >= 4 and str(row[3]).isdigit():
                    total += int(row[3])
            return total or None
    except Exception:
        return None
    return None


def _mysql_rows(plan: Any) -> int | None:
    """MySQL JSON 计划：递归取 query_block 的 rows_produced_per_join / rows 最大值。"""
    found: list[int] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("rows_produced_per_join", "rows_examined_per_scan", "rows"):
                val = node.get(key)
                if isinstance(val, int):
                    found.append(val)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(plan)
    return max(found) if found else None


def dry_run(
    sql_final: str, *, dialect: str, ds_mode: str, caps: dict, query_fn: QueryFn, timeout_s: int = 5
) -> dict[str, Any]:
    """干跑主函数：四则把关后执行 EXPLAIN 并提取扫描行数。

    返回 {"ok": bool, "rows": int|None, "error": str|None}；失败降级（E3001）由调用方
    标注「未经验证」。query_fn 由 exec 层显式注入（D-35：sqlcheck 不执行 SQL）。
    """
    if ds_mode != "online":
        return {"ok": False, "rows": None, "error": "offline 库不干跑"}
    if dialect_mod.explain_form(dialect) in (None, "none"):
        return {"ok": False, "rows": None, "error": "能力卡 explain=none"}
    try:
        stmt = explain_statement(sql_final, dialect=dialect)
        payload = query_fn(stmt, None)
        return {"ok": True, "rows": parse_scan_rows(dialect, payload), "error": None}
    except Exception as exc:
        logger.warning("EXPLAIN 干跑失败: %s", type(exc).__name__)
        return {"ok": False, "rows": None, "error": f"{type(exc).__name__}: {exc}"}
