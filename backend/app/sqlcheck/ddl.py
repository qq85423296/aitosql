"""DDL 解析（sqlglot 收口件之一；供 ingest 离线通道，经 api 层显式传递产物）。

`{ddl}` → `{tables, errors:[{line, message}]}`（C1.2）：逐条 CREATE TABLE 解析，
部分失败行标红（errors 携带行号），成功部分可入库（import-ddl/commit）。
"""

from __future__ import annotations

import logging
from typing import Any

import sqlglot
from sqlglot import exp

logger = logging.getLogger("aitosql.sqlcheck.ddl")


def _dialect_arg(dialect: str) -> str:
    return {"mysql": "mysql", "doris": "mysql", "clickhouse": "clickhouse"}.get(dialect, dialect)


def parse_ddl(ddl: str, dialect: str) -> dict[str, Any]:
    """解析 DDL 文本为入库产物。

    tables: [{name, comment, columns: [{name, type, comment, is_pk, is_partition, is_bucket}]}]
    errors: [{line, message}]（按语句起始行定位，部分失败不阻断其余语句）
    """
    tables: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    statements = _split_statements(ddl)
    offset = 0
    for stmt in statements:
        line_no = ddl.count("\n", 0, offset) + 1
        offset += len(stmt) + 1
        if not stmt.strip():
            continue
        try:
            tree = sqlglot.parse_one(stmt, read=_dialect_arg(dialect))
        except Exception as exc:
            errors.append({"line": line_no, "message": str(exc)[:200]})
            continue
        if not isinstance(tree, exp.Create) or not isinstance(tree.this, exp.Schema):
            errors.append({"line": line_no, "message": "非 CREATE TABLE 语句，已跳过（仅支持建表语句）"})
            continue
        tables.append(_extract_table(tree))
    return {"tables": tables, "errors": errors}


def _split_statements(ddl: str) -> list[str]:
    """按分号切分（sqlglot 逐条解析以保证行号可定位）。"""
    return [s for s in ddl.split(";")]


def _extract_table(tree: exp.Create) -> dict[str, Any]:
    schema = tree.this
    table_name = schema.this.name if isinstance(schema.this, exp.Identifier) else str(schema.this)
    columns: list[dict[str, Any]] = []
    pk_cols: set[str] = set()
    for expr in schema.expressions or []:
        if isinstance(expr, exp.PrimaryKey):
            pk_cols.update(c.name for c in expr.expressions)
    for expr in schema.expressions or []:
        if not isinstance(expr, exp.ColumnDef):
            continue
        col: dict[str, Any] = {
            "name": expr.name,
            "type": expr.kind.sql() if expr.kind else "",
            "comment": None,
            "is_pk": expr.name in pk_cols
            or any(isinstance(c, exp.PrimaryKeyColumnConstraint) for c in (expr.constraints or [])),
            "is_partition": False,
            "is_bucket": False,
        }
        for constraint in expr.constraints or []:
            if isinstance(constraint, exp.CommentColumnConstraint):
                col["comment"] = constraint.this.name
        columns.append(col)
    # 表级约束补充（CH ENGINE / Doris PARTITION BY 等属性不逐字还原，标注于表注释位）
    table_comment = None
    for prop in tree.args.get("properties") or []:
        if isinstance(prop, exp.SchemaCommentProperty):
            table_comment = prop.this.name
    return {"name": table_name, "comment": table_comment, "columns": columns}
